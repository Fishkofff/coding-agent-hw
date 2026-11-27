from __future__ import annotations

from pathlib import Path

from agent.evals import load_suite, render_summary, run_suite
from agent.market import MarketError, version_key
from agent.util import atomic_write_json, now_iso

TOKEN_GROWTH_LIMIT = 0.25

RULE = ("Обновление принимается, только если одновременно: (1) ни в одном сценарии число успешных попыток новой версии "
        "не меньше, чем у старой; (2) ошибок выбора скиллов у новой версии не больше; (3) usage полный во всех trials "
        "обеих версий и средний расход input+output токенов на trial вырос не более чем на 25%. Иначе отклоняется.")


def compare(rows: list[dict], cases: list[str]) -> dict:
    per_case = {}
    problems = []
    for case in cases:
        old = [r for r in rows if r["case_id"] == case and r["version"] == "old"]
        new = [r for r in rows if r["case_id"] == case and r["version"] == "new"]
        item = {"old_pass": sum(r["outcome_pass"] for r in old), "new_pass": sum(r["outcome_pass"] for r in new),
                "old_trials": len(old), "new_trials": len(new),
                "old_routing_errors": sum(1 for r in old if r["routing_pass_or_na"] is False),
                "new_routing_errors": sum(1 for r in new if r["routing_pass_or_na"] is False)}
        per_case[case] = item
        if item["new_pass"] < item["old_pass"]:
            problems.append(f"{case}: успешных попыток стало меньше ({item['old_pass']} -> {item['new_pass']})")
        if item["new_routing_errors"] > item["old_routing_errors"]:
            problems.append(f"{case}: ошибок выбора скиллов стало больше")
    tokens = {}
    for version in ("old", "new"):
        group = [r for r in rows if r["version"] == version]
        complete = all(r["usage_status"] == "complete" for r in group) and bool(group)
        total = [(r["input_tokens"] or 0) + (r["output_tokens"] or 0) for r in group]
        tokens[version] = {"usage_complete": complete, "avg_tokens": sum(total) / len(total) if total else None}
    if not (tokens["old"]["usage_complete"] and tokens["new"]["usage_complete"]):
        problems.append("usage неполный: соблюдение порога по токенам подтвердить нельзя")
    elif tokens["old"]["avg_tokens"] and tokens["new"]["avg_tokens"] > tokens["old"]["avg_tokens"] * (1 + TOKEN_GROWTH_LIMIT):
        growth = tokens["new"]["avg_tokens"] / tokens["old"]["avg_tokens"] - 1
        problems.append(f"средний расход токенов вырос на {growth:.0%} (порог {TOKEN_GROWTH_LIMIT:.0%})")
    return {"per_case": per_case, "tokens": tokens, "problems": problems, "accept": not problems}


def verify_candidate(market, skill: str, suite_file, out, model_factory, *, trials=None, only=None, log=print) -> dict:
    candidates = [c for c in market.candidates() if c["name"] == skill]
    if not candidates:
        raise MarketError(f"no update candidate for {skill}; run: python3 -m agent market check --force")
    candidate = max(candidates, key=lambda c: version_key(c["version"]))
    installed = market.installed().get(skill)
    if installed is None:
        raise MarketError(f"{skill} is not installed from the market")
    old_path = Path(market.install_dir) / skill
    suite = dict(load_suite(suite_file))
    suite["name"] = f"update-{skill}-{installed['version']}-to-{candidate['version']}"
    suite["hypothesis"] = f"Новая версия {skill} {candidate['version']} не хуже установленной {installed['version']} на учебном наборе."
    suite["criteria"] = RULE
    suite["versions"] = {"old": {"skills": True, "skill_override": {skill: str(old_path)}},
                         "new": {"skills": True, "skill_override": {skill: candidate["path"]}}}
    out = Path(out) if out else market.root / "updates" / f"{skill}-{installed['version']}-to-{candidate['version']}"
    market.event("market_update_verification_started", skill=skill, old_version=installed["version"],
                 new_version=candidate["version"], out=str(out))
    rows = run_suite(suite, out, model_factory, only=only, trials=trials, log=log)
    cases = [c["id"] for c in suite["cases"] if not only or c["id"] in only]
    result = compare(rows, cases)
    reason = "все условия правила выполнены" if result["accept"] else "; ".join(result["problems"])
    decision = "accepted" if result["accept"] else "rejected"
    report = {"skill": skill, "old_version": installed["version"], "new_version": candidate["version"],
              "old_sha256": installed["sha256"], "new_sha256": candidate["sha256"], "rule": RULE,
              "decision": decision, "reason": reason, "comparison": result, "decided_at": now_iso(),
              "results": str(out / "results.jsonl"), "summary": str(out / "summary.md")}
    if result["accept"]:
        market.apply_candidate(skill, candidate["version"], reason)
    else:
        market.reject_candidate(skill, candidate["version"], reason)
    atomic_write_json(out / "decision.json", report)
    (out / "decision.md").write_text(render_decision(report, suite, rows), encoding="utf-8")
    report["report_path"] = str(out / "decision.md")
    return report


def render_decision(report: dict, suite: dict, rows: list[dict]) -> str:
    lines = [f"# Проверка обновления {report['skill']}: {report['old_version']} -> {report['new_version']}", "",
             f"**Правило (задано до запуска).** {report['rule']}", "",
             f"**Решение: {report['decision']}.** {report['reason']}", "",
             "| сценарий | old pass | new pass | old routing ошибок | new routing ошибок |", "|---|---|---|---|---|"]
    for case, item in report["comparison"]["per_case"].items():
        lines.append(f"| {case} | {item['old_pass']}/{item['old_trials']} | {item['new_pass']}/{item['new_trials']} | "
                     f"{item['old_routing_errors']} | {item['new_routing_errors']} |")
    tokens = report["comparison"]["tokens"]
    lines += ["", f"Средний расход токенов на trial: old {tokens['old']['avg_tokens']}, new {tokens['new']['avg_tokens']} "
                  f"(usage полный: old {tokens['old']['usage_complete']}, new {tokens['new']['usage_complete']}).", "",
              f"sha256 SKILL.md: old {report['old_sha256']}, new {report['new_sha256']}.", "",
              "Решение относится к учебному набору сценариев, а не ко всем возможным задачам.", "",
              "Откат принятого обновления: `python3 -m agent market rollback " + report["skill"] + "` "
              "или в chat: `/rollback-skill " + report["skill"] + "`.", "", "---", "", render_summary(suite, rows)]
    return "\n".join(lines) + "\n"
