from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from agent import usage
from agent.report import Reporter
from agent.runtime import AgentRuntime
from agent.stats import run_usage, tool_key
from agent.util import atomic_write_json, now_iso

REPO = Path(__file__).resolve().parents[1]
FORBIDDEN_EVERYWHERE = ("commit-message",)


def load_suite(path) -> dict:
    if isinstance(path, dict):
        return path
    path = Path(path).resolve()
    suite = json.loads(path.read_text(encoding="utf-8"))
    suite["_dir"] = str(path.parent)
    return suite


def suite_path(suite: dict, rel: str) -> Path:
    return (Path(suite["_dir"]) / rel).resolve()


def substitute(value, root: Path):
    if isinstance(value, str):
        return value.replace("{repo}", str(REPO)).replace("{root}", str(root)).replace("{python}", sys.executable)
    if isinstance(value, list):
        return [substitute(v, root) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, root) for k, v in value.items()}
    return value


def prepare_trial(suite: dict, case: dict, version: str, root: Path) -> Path:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    fixture = suite_path(suite, case["fixture"])
    workspace = fixture / "workspace"
    if workspace.is_dir():
        shutil.copytree(workspace, root / "workspace")
    else:
        (root / "workspace").mkdir()
    if (fixture / "data").is_dir():
        shutil.copytree(fixture / "data", root / "data")
    config = suite["versions"][version]
    if config["skills"]:
        (root / "skills").mkdir()
        for rel in suite["skills_dirs"]:
            base = suite_path(suite, rel)
            for directory in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")):
                shutil.copytree(directory, root / "skills" / directory.name, ignore=shutil.ignore_patterns("__pycache__"))
        for name, path in config.get("skill_override", {}).items():
            target = root / "skills" / name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(path, target, ignore=shutil.ignore_patterns("__pycache__"))
    base = json.loads(suite_path(suite, suite["policy_base"]).read_text(encoding="utf-8"))
    policy = {
        "workspace": "workspace",
        "runs_dir": "runs",
        "commands": base["commands"],
        "directories": {"allow": ["workspace"], "deny": ["workspace/private"]},
        "bash": base.get("bash", {}),
        "files": base.get("files", {}),
        "journal": base.get("journal", {}),
        "budget": suite["budget"],
        "skills": {"dirs": ["skills"] if suite["versions"][version]["skills"] else []},
        "mcp": {"servers": {}},
    }
    for name, server in suite.get("mcp_servers", {}).items():
        config = substitute(server, root)
        config["env"] = dict(case.get("mcp_env", {}))
        policy["mcp"]["servers"][name] = config
    atomic_write_json(root / "policy.json", policy)
    return root / "policy.json"


def run_check(suite: dict, case: dict, root: Path, answer_path: Path) -> dict:
    script = suite_path(suite, case["check"])
    proc = subprocess.run([sys.executable, str(script), "--root", str(root), "--answer", str(answer_path),
                           "--fixture", str(suite_path(suite, case["fixture"]))],
                          cwd=str(script.parent), capture_output=True, text=True, timeout=120)
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"pass": False, "reason": f"checker crashed: {proc.stderr[-400:]}"}


def routing(case: dict, loaded: list[str], skills_on: bool) -> dict:
    if not skills_on:
        return {"pass": "n/a", "reason": "version A has no skills"}
    expected = set(case.get("expected_skills", []))
    allowed = expected | set(case.get("allowed_skills", []))
    got = set(loaded)
    problems = []
    if expected - got:
        problems.append(f"not loaded: {sorted(expected - got)}")
    if got - allowed:
        problems.append(f"wrongly loaded: {sorted(got - allowed)}")
    if got & set(FORBIDDEN_EVERYWHERE):
        problems.append("unsuitable skill loaded")
    return {"pass": not problems, "reason": "; ".join(problems) or "ok"}


def trial_metrics(events: list[dict]) -> dict:
    loaded = [e["data"]["name"] for e in events if e["type"] == "skill_loaded"]
    calls = [tool_key(e["data"]) for e in events if e["type"] == "tool_call"]
    mcp = [c for c in calls if c.startswith("mcp:")]
    totals = usage.merge(run_usage(events).values())
    return {"loaded": loaded, "calls": calls, "mcp": mcp, "usage": totals}


def run_trial(suite: dict, case: dict, version: str, trial: int, out: Path, model_factory, log=print) -> dict:
    root = out / "trials" / case["id"] / f"{version}{trial}"
    policy_path = prepare_trial(suite, case, version, root)
    console = open(root / "console.log", "w", encoding="utf-8")
    started = time.monotonic()
    model, info = model_factory(case, version, trial)
    runtime = AgentRuntime(policy_path, model, reporter=Reporter(console, hints=False))
    error = None
    status = None
    st = {}
    try:
        run_id = runtime.start(case["request"], mode=suite["mode"], plan_mode=False, model_info=info,
                               skills=suite["versions"][version]["skills"])
        status = runtime.drive()
        while status == "waiting" and runtime.state["pending"]["kind"] == "confirmation":
            status = runtime.deny(run_id, "eval: confirmations are denied automatically in both versions")
        st = runtime.state
        if status != "completed":
            error = f"{status}: {(st.get('error') or {}).get('code')}: {(st.get('error') or {}).get('message')}"
    finally:
        runtime.close()
        console.close()
    answer_path = root / "final_answer.txt"
    answer_path.write_text(st.get("final_answer") or "", encoding="utf-8")
    events_path = root / "runs" / st["run_id"] / "events.jsonl"
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    check = run_check(suite, case, root, answer_path)
    atomic_write_json(root / "check.json", check)
    metrics = trial_metrics(events)
    route = routing(case, metrics["loaded"], suite["versions"][version]["skills"])
    required = case.get("required_tools", [])
    missing_tools = [t for t in required if t not in metrics["calls"]]
    totals = metrics["usage"]
    usage_status = usage.completeness(totals)
    row = {
        "case_id": case["id"], "version": version, "trial": trial, "run_id": st["run_id"],
        "session_id": st["session_id"], "outcome_pass": bool(check.get("pass")), "outcome_reason": check.get("reason"),
        "routing_pass_or_na": route["pass"], "routing_reason": route["reason"],
        "tools_pass": not missing_tools, "missing_tools": missing_tools,
        "input_tokens": totals["input_tokens"] if usage_status != "unknown" else None,
        "output_tokens": totals["output_tokens"] if usage_status != "unknown" else None,
        "usage_status": usage_status, "model_requests": totals["requests"],
        "skill_activations": metrics["loaded"], "mcp_calls": len(metrics["mcp"]), "tool_calls": len(metrics["calls"]),
        "status": status, "error": error, "duration_sec": round(time.monotonic() - started, 1), "model": info,
        "events": str(events_path.relative_to(out)), "finished_at": now_iso(), "skill_versions": skills_versions(root),
    }
    log(f"  {case['id']} {version}{trial}: outcome={'PASS' if row['outcome_pass'] else 'FAIL'} "
        f"routing={row['routing_pass_or_na']} tokens={row['input_tokens']}/{row['output_tokens']} "
        f"status={status} ({row['duration_sec']} s) {'' if row['outcome_pass'] else '- ' + str(row['outcome_reason'])}")
    return row


def read_rows(out: Path) -> list[dict]:
    path = out / "results.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_suite(suite_file, out, model_factory, *, only=None, trials=None, log=print) -> list[dict]:
    suite = load_suite(suite_file)
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    trials = trials or suite["trials"]
    done = {(r["case_id"], r["version"], r["trial"]) for r in read_rows(out)}
    atomic_write_json(out / "suite.json", dict({k: v for k, v in suite.items() if k != "_dir"}, trials=trials))
    cases = [c for c in suite["cases"] if not only or c["id"] in only]
    plan = [(c, v, t) for c in cases for t in range(1, trials + 1) for v in suite["versions"]]
    log(f"eval {suite['name']}: {len(plan)} trials, already saved: {len(done)}")
    for case, version, trial in plan:
        if (case["id"], version, trial) in done:
            continue
        row = run_trial(suite, case, version, trial, out, model_factory, log)
        with open(out / "results.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    rows = read_rows(out)
    (out / "summary.md").write_text(render_summary(suite, rows), encoding="utf-8")
    log(f"summary: {out / 'summary.md'}")
    return rows


def skills_versions(root: Path) -> dict:
    result = {}
    skills = root / "skills"
    if skills.is_dir():
        for directory in sorted(skills.iterdir()):
            meta = directory / ".install.json"
            result[directory.name] = json.loads(meta.read_text(encoding="utf-8"))["version"] if meta.exists() else "local"
    return result


def _avg(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values)) if values else None


def render_summary(suite: dict, rows: list[dict]) -> str:
    lines = [f"# Сводка eval: {suite['name']}", "", f"**Гипотеза.** {suite['hypothesis']}", "",
             f"**Критерии.** {suite['criteria']}", ""]
    models = sorted({json.dumps(r.get("model")) for r in rows if r.get("model")})
    lines.append(f"Режим: {suite['mode']}; подтверждения в eval: {suite['on_confirmation']}; бюджет на trial: "
                 f"{json.dumps(suite['budget'])}; trials на версию: {suite['trials']}. Строк результатов: {len(rows)}.")
    if models:
        lines.append(f"Модель: {', '.join(models)}")
    lines += ["", "## Итог по сценариям", "",
              "| сценарий | версия | outcome pass | routing ошибок | tools pass | ср. input | ср. output | usage | ср. MCP-вызовов | ср. вызовов | ошибки |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for case in suite["cases"]:
        for version in suite["versions"]:
            group = [r for r in rows if r["case_id"] == case["id"] and r["version"] == version]
            if not group:
                continue
            passed = sum(r["outcome_pass"] for r in group)
            route = [r["routing_pass_or_na"] for r in group]
            route_text = "n/a" if all(x == "n/a" for x in route) else str(sum(1 for x in route if x is False))
            usage_states = sorted({r["usage_status"] for r in group})
            errors = sum(1 for r in group if r["error"])
            lines.append(f"| {case['id']} | {version} | {passed}/{len(group)} | {route_text} | "
                         f"{sum(r['tools_pass'] for r in group)}/{len(group)} | {_avg(r['input_tokens'] for r in group)} | "
                         f"{_avg(r['output_tokens'] for r in group)} | {', '.join(usage_states)} | "
                         f"{_avg(r['mcp_calls'] for r in group)} | {_avg(r['tool_calls'] for r in group)} | {errors} |")
    lines += ["", "## Итог по версиям", ""]
    for version in suite["versions"]:
        group = [r for r in rows if r["version"] == version]
        if group:
            lines.append(f"- {version}: успешных {sum(r['outcome_pass'] for r in group)}/{len(group)}, "
                         f"ср. input {_avg(r['input_tokens'] for r in group)}, ср. output {_avg(r['output_tokens'] for r in group)}, "
                         f"ошибок выбора скиллов {sum(1 for r in group if r['routing_pass_or_na'] is False)}")
    lines += ["", "## Все trials", "",
              "| case_id | A/B | trial | run_id | outcome_pass | routing_pass_or_na | input_tokens | output_tokens | skill_activations | mcp_calls | error | журнал |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['case_id']} | {r['version']} | {r['trial']} | {r['run_id']} | {r['outcome_pass']} | "
                     f"{r['routing_pass_or_na']} | {r['input_tokens']} | {r['output_tokens']} | "
                     f"{', '.join(r['skill_activations']) or '-'} | {r['mcp_calls']} | {r['error'] or '-'} | "
                     f"[events]({r['events']}) |")
    failed = [r for r in rows if not r["outcome_pass"] or r["routing_pass_or_na"] is False]
    if failed:
        lines += ["", "## Причины неуспеха", ""]
        for r in failed:
            lines.append(f"- {r['case_id']} {r['version']}{r['trial']}: outcome: {r['outcome_reason']}; "
                         f"routing: {r['routing_reason']}")
    return "\n".join(lines) + "\n"
