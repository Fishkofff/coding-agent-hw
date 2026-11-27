from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from agent.budget import BUDGET_KEYS, Budget
from agent.faults import Faults
from agent.journal import RollbackConflict, RollbackError
from agent.llm import OpenAICompatibleModel, ScriptedModel
from agent.plan import describe_check, render_checklist
from agent.policy import MODES, ArgumentProblem, PolicyEngine, PolicyError, PolicyViolation, approval_requirement
from agent.report import format_args, format_changes, format_plan, shorten
from agent.runtime import AgentRuntime, UserError
from agent.stats import compute as compute_stats, render as render_stats
from agent.store import RunBusy, RunNotFound
from agent.tools import EXECUTION, PLANNING, REGISTRY


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def make_model(args, state: dict | None = None):
    scripted = getattr(args, "scripted", None)
    if not scripted and state and (state.get("model") or {}).get("kind") == "scripted":
        scripted = state["model"]["path"]
    if scripted:
        path = str(Path(scripted).resolve())
        return ScriptedModel.from_file(path), {"kind": "scripted", "path": path}
    base_url, model = os.environ.get("OPENAI_BASE_URL"), os.environ.get("OPENAI_MODEL")
    if not base_url or not model:
        return None, None
    no_think = os.environ.get("OPENAI_NO_THINK", "") in ("1", "true", "yes")
    info = {"kind": "openai-compatible", "base_url": base_url, "model": model, "temperature": 0.0, "no_think": no_think}
    return OpenAICompatibleModel(base_url, os.environ.get("OPENAI_API_KEY", ""), model, no_think=no_think), info


def make_runtime(args, run_id: str | None = None) -> AgentRuntime:
    faults = Faults(getattr(args, "crash_at", None), hard=True)
    runtime = AgentRuntime(args.policy, None, faults=faults)
    state = runtime.load(run_id) if run_id else None
    runtime.model, _info = make_model(args, state)
    return runtime


def parse_budget(text: str | None) -> dict:
    if not text:
        return {}
    limits = {}
    for part in text.split(","):
        key, _, value = part.partition("=")
        key = key.strip()
        if key not in BUDGET_KEYS or not value.strip().isdigit():
            raise UserError(f"bad budget item {part!r}; expected e.g. model_calls=20,tool_calls=40")
        limits[key] = int(value)
    return limits


def interactive(runtime: AgentRuntime, run_id: str, status: str) -> str:
    while status == "waiting":
        kind = runtime.state["pending"]["kind"]
        try:
            if kind == "confirmation":
                answer = input("Разрешить действие? [y/N]: ").strip().lower()
                if answer in ("y", "yes", "д", "да"):
                    status = runtime.approve(run_id)
                else:
                    reason = input("Причина отказа (её увидит модель): ").strip()
                    status = runtime.deny(run_id, reason or None)
            elif kind == "plan_approval":
                answer = input("Утвердить план? [y/N]: ").strip().lower()
                if answer in ("y", "yes", "д", "да"):
                    status = runtime.approve_plan(run_id)
                else:
                    reason = input("Что исправить в плане: ").strip()
                    status = runtime.reject_plan(run_id, reason or None)
            else:
                return status
        except EOFError:
            return status
    return status


def cmd_run(args) -> int:
    model, info = make_model(args)
    if model is None:
        raise UserError("model is not configured: set OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_MODEL "
                        "(see .env.example) or use --scripted demo/<file>.json")
    auto_market_check(args.policy)
    runtime = AgentRuntime(args.policy, model, faults=Faults(args.crash_at, hard=True))
    try:
        session = runtime.store.new_session()
        run_id = runtime.start(args.task, mode=args.mode, plan_mode=not args.no_plan,
                               budget=parse_budget(args.budget), model_info=info,
                               session_id=session["session_id"], skills=not args.no_skills)
        session["runs"].append(run_id)
        runtime.store.save_session(session)
        status = runtime.drive()
        if args.interactive:
            interactive(runtime, run_id, status)
    finally:
        runtime.close()
    return 0


CHAT_HELP = """Команды: /stats — статистика сессии, /skills — каталог скиллов, /mcp — MCP-инструменты,
/doctor — разбор сессии, /market — маркет и кандидаты, /market check — проверить маркет,
/install <источник> <скилл>, /rollback-skill <скилл> — откат обновления, /help, /exit."""


def chat_history(session: dict, runtime: AgentRuntime, text: str) -> list:
    st = runtime.state
    if st["status"] == "completed":
        return st["messages"]
    base = session["history"] or [{"role": "system", "content": ""}]
    error = (st.get("error") or {}).get("message") or ""
    return base + [{"role": "user", "content": text},
                   {"role": "assistant", "content": f"[runtime] this request ended with status {st['status']}. {error}".strip()}]


def cmd_chat(args) -> int:
    model, info = make_model(args)
    if model is None:
        raise UserError("model is not configured: set OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_MODEL "
                        "(see .env.example) or use --scripted demo/<file>.json")
    from agent.report import Reporter
    runtime = AgentRuntime(args.policy, model, reporter=Reporter(hints=False))
    store = runtime.store
    session = store.load_session(args.session) if args.session else store.new_session()
    session.setdefault("skills_enabled", not args.no_skills)
    store.save_session(session)
    auto_market_check(args.policy, session["session_id"])
    skills_on = session["skills_enabled"]
    print(f"Сессия {session['session_id']} (режим {args.mode}, скиллы: {'on' if skills_on else 'off'}, "
          f"модель: {(info or {}).get('model') or (info or {}).get('kind')})")
    print(CHAT_HELP)
    try:
        while True:
            try:
                text = input("\nВы: ").strip()
            except EOFError:
                print()
                break
            if not text:
                continue
            if text in ("/exit", "/quit", "exit", "выход"):
                break
            if text == "/help":
                print(CHAT_HELP)
                continue
            if text == "/stats":
                print(render_stats(compute_stats(store.session_events(session["session_id"])), f"сессии {session['session_id']}"))
                continue
            if text in ("/skills", "/mcp"):
                print_catalog(runtime, text)
                continue
            if text.startswith(("/market", "/install", "/rollback-skill")):
                chat_market(args, text)
                continue
            if text == "/doctor":
                from agent.doctor import diagnose, render as render_doctor
                print(render_doctor(diagnose(store.session_events(session["session_id"]))))
                continue
            run_id = runtime.start(text, mode=args.mode, plan_mode=args.plan, budget=parse_budget(args.budget),
                                   model_info=info, session_id=session["session_id"], history=session["history"],
                                   skills=skills_on)
            status = runtime.drive()
            status = interactive(runtime, run_id, status)
            session["runs"].append(run_id)
            session["history"] = chat_history(session, runtime, text)
            store.save_session(session)
            if status == "completed":
                print(f"\nАгент: {runtime.state['final_answer']}")
    finally:
        runtime.close()
    print(f"Сессия сохранена: {session['session_id']}. Продолжить: python3 -m agent chat --session {session['session_id']}")
    return 0


def chat_market(args, text: str) -> None:
    parts = text.split()
    namespace = argparse.Namespace(policy=args.policy, scripted_dir=None, force=True)
    try:
        if parts[0] == "/install" and len(parts) == 3:
            cmd_market(argparse.Namespace(**vars(namespace), market_command="install", source=parts[1], name=parts[2]))
        elif parts[0] == "/rollback-skill" and len(parts) == 2:
            cmd_market(argparse.Namespace(**vars(namespace), market_command="rollback", name=parts[1]))
        elif parts[:2] == ["/market", "check"]:
            cmd_market(argparse.Namespace(**vars(namespace), market_command="check"))
        else:
            cmd_market(argparse.Namespace(**vars(namespace), market_command="list"))
    except UserError as exc:
        print(f"Ошибка: {exc}")


def print_catalog(runtime: AgentRuntime, what: str) -> None:
    from agent.skills import discover
    policy = runtime.engine.load()
    if what == "/skills":
        skills, errors = discover(policy.skills_dirs, policy.skills_disabled)
        for skill in skills:
            print(f"  {skill.name}: {skill.description}")
        for problem in errors:
            print(f"  ОШИБКА {problem['path']}: {problem['error']}")
        if not skills and not errors:
            print("  (скиллов нет)")
        return
    tools, errors, _servers = runtime.mcp_manager().connect(policy)
    for tool in tools:
        mark = "доступен" if tool["offered"] else "запрещён policy"
        print(f"  {tool['server']}/{tool['tool']} [{tool['effect']}, {mark}]: {tool['description']}")
    for problem in errors:
        print(f"  ОШИБКА {problem['server']}: {problem['error']}")
    if not tools and not errors:
        print("  (MCP-серверы не настроены)")


def cmd_sessions(args) -> int:
    runtime = AgentRuntime(args.policy)
    sessions = runtime.store.list_sessions()
    if not sessions:
        print("Сессий пока нет.")
    for session in sessions:
        print(f"{session['session_id']}  runs={len(session['runs'])}  created={session['created_at']}")
    return 0


def cmd_doctor(args) -> int:
    from agent.doctor import diagnose, render as render_doctor
    from agent.util import atomic_write_json
    runtime = AgentRuntime(args.policy)
    events = runtime.store.session_events(args.session_id)
    if not events:
        raise UserError(f"session {args.session_id} has no events")
    report = diagnose(events)
    path = runtime.store.session_dir(args.session_id) / "doctor.json"
    atomic_write_json(path, report)
    print(json.dumps(report, ensure_ascii=False, indent=1) if args.json else render_doctor(report))
    print(f"\nОтчёт сохранён: {path}")
    return 0


def eval_model_factory(args):
    from agent.llm import ScriptedModel

    def factory(case, version, trial):
        if args.scripted_dir:
            base = Path(args.scripted_dir)
            path = base / f"{case['id']}_{version}.json"
            if not path.exists():
                path = base / f"{case['id']}.json"
            return ScriptedModel.from_file(path), {"kind": "scripted", "path": str(path)}
        model, info = make_model(argparse.Namespace(scripted=None))
        if model is None:
            raise UserError("model is not configured: set OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_MODEL "
                            "(evals need a real LLM; --scripted-dir is only for runtime tests)")
        return model, info
    return factory


def cmd_eval(args) -> int:
    from agent.evals import load_suite, read_rows, render_summary, run_suite
    if args.eval_command == "summary":
        suite = load_suite(Path(args.out) / "suite.json") if (Path(args.out) / "suite.json").exists() else None
        rows = read_rows(Path(args.out))
        text = render_summary(suite, rows)
        (Path(args.out) / "summary.md").write_text(text, encoding="utf-8")
        print(text)
        return 0
    only = set(args.only.split(",")) if args.only else None
    run_suite(args.suite, args.out, eval_model_factory(args), only=only, trials=args.trials)
    return 0


def make_market(args, log=print):
    from agent.market import Market
    policy = PolicyEngine(args.policy).load()
    if not policy.market:
        raise UserError("market is not configured in the policy")
    return Market(policy, log=log)


def auto_market_check(policy_path, session_id=None) -> None:
    from agent.market import Market, MarketError
    policy = PolicyEngine(policy_path).load()
    if not policy.market:
        return
    try:
        Market(policy, session_id).check()
    except (MarketError, OSError, ValueError) as exc:
        print(f"[market] проверка не удалась, установленные скиллы не тронуты: {exc}")


def cmd_market(args) -> int:
    from agent.market import MarketError
    market = make_market(args)
    action = args.market_command
    try:
        if action == "list":
            installed = market.installed()
            print("Установлено из маркета:")
            for name, meta in installed.items():
                print(f"  {name} {meta['version']} (источник {meta['source']}, sha256 {meta['sha256'][:12]})")
            if not installed:
                print("  -")
            print("Источники:")
            state = market.state()["sources"]
            for name in market.config["sources"]:
                settings = market.source_settings(name)
                known = state.get(name, {})
                print(f"  {name}: index={settings['index']} check={settings.get('check', True)} "
                      f"auto_update={settings.get('auto_update', False)} verify_updates={settings.get('verify_updates', False)} "
                      f"interval={settings.get('interval_minutes', 60)} мин; последняя проверка {known.get('last_check', '-')}, "
                      f"ревизия {known.get('revision', '-')}")
            for item in market.candidates():
                print(f"Кандидат обновления: {item['name']} {item['version']} ({item['path']})")
        elif action == "search":
            for item in market.search(args.query):
                if item.get("error"):
                    print(f"  {item['source']}: ошибка {item['error']}")
                else:
                    print(f"  {item['source']}/{item['name']} {item['version']}: {item['description']}")
        elif action == "install":
            meta = market.install(args.source, args.name)
            print(f"Установлен {meta['name']} {meta['version']} из {meta['source']} (sha256 {meta['sha256'][:12]})")
        elif action == "check":
            for report in market.check(force=args.force):
                if report["status"] in ("disabled", "not_due"):
                    print(f"[market] {report['source']}: {'проверка выключена' if report['status'] == 'disabled' else 'интервал ещё не прошёл (--force для принудительной проверки)'}")
                elif report["status"] == "ok" and not (report["new_skills"] or report["updates"] or report["errors"]):
                    print(f"[market] {report['source']}: изменений нет (ревизия {report['new_revision']})")
        elif action == "set":
            for key, value in (("check", args.check), ("auto_update", args.auto_update), ("verify_updates", args.verify_updates)):
                if value is not None:
                    market.set_setting(args.source, key, value == "on")
                    print(f"{args.source}: {key} = {value}")
        elif action == "rollback":
            result = market.rollback(args.name)
            print(f"Откат {result['skill']}: {result['from_version']} -> {result['to_version']}")
        elif action == "verify":
            from agent.update_check import verify_candidate
            report = verify_candidate(market, args.name, args.suite, args.out, eval_model_factory(args),
                                      trials=args.trials, only=set(args.only.split(",")) if args.only else None)
            print(f"Решение: {report['decision']} — {report['reason']}")
            print(f"Отчёт: {report['report_path']}")
        elif action == "events":
            for event in market.events()[-args.last:]:
                print(json.dumps(event, ensure_ascii=False))
    except MarketError as exc:
        raise UserError(str(exc)) from None
    return 0


def cmd_ui(args) -> int:
    from agent.ui import serve

    def factory():
        model, info = make_model(args)
        if model is None:
            raise UserError("model is not configured: set OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_MODEL")
        return model, info
    auto_market_check(args.policy)
    serve(args.policy, factory, host=args.host, port=args.port)
    return 0


def cmd_stats(args) -> int:
    runtime = AgentRuntime(args.policy)
    events = runtime.store.session_events(args.session_id)
    stats = compute_stats(events)
    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=1))
    else:
        print(render_stats(stats, f"сессии {args.session_id}"))
    return 0


def _drive_command(args, action) -> int:
    runtime = make_runtime(args, args.run_id)
    status = action(runtime)
    if getattr(args, "interactive", False):
        interactive(runtime, args.run_id, status)
    return 0


def cmd_approve(args) -> int:
    return _drive_command(args, lambda rt: rt.approve(args.run_id, args.call))


def cmd_deny(args) -> int:
    return _drive_command(args, lambda rt: rt.deny(args.run_id, args.reason, args.call))


def cmd_resume(args) -> int:
    return _drive_command(args, lambda rt: rt.resume(args.run_id))


def cmd_recover(args) -> int:
    return _drive_command(args, lambda rt: rt.recover(args.run_id, args.action, args.changes))


def cmd_plan(args) -> int:
    if args.plan_command == "approve":
        return _drive_command(args, lambda rt: rt.approve_plan(args.run_id))
    if args.plan_command == "reject":
        return _drive_command(args, lambda rt: rt.reject_plan(args.run_id, args.reason))
    state = AgentRuntime(args.policy).load(args.run_id)
    plans = state["plan_history"] + ([state["plan"]] if state["plan"] and state["plan"] not in state["plan_history"] else [])
    if not plans:
        print("План ещё не отправлен.")
    for plan in plans:
        print(format_plan(plan))
        if plan.get("rejection_reason"):
            print(f"Причина отклонения: {plan['rejection_reason']}")
        print()
    return 0


def cmd_abort(args) -> int:
    AgentRuntime(args.policy).abort(args.run_id, args.reason)
    return 0


def cmd_status(args) -> int:
    runtime = AgentRuntime(args.policy)
    st = runtime.load(args.run_id)
    print(f"run_id:   {st['run_id']}")
    print(f"задача:   {shorten(st['task'], 300)}")
    print(f"режим:    {st['mode']}   plan mode: {'on' if st['plan_mode'] else 'off'}   фаза: {st['phase']}")
    status = st["status"]
    if status == "waiting":
        status += f" ({st['pending']['kind']})"
    started = [r["id"] for r in runtime.all_calls() if r["status"] == "started"]
    if started and st["status"] == "running":
        status += f"  — процесс прервался во время вызова {started[0]}; выполните resume"
    print(f"статус:   {status}")
    if st.get("error"):
        print(f"ошибка:   {st['error'].get('code')}: {st['error'].get('message')}")
    print(f"бюджет:   {Budget(st['budget']).summary()}")
    if st["plan"]:
        print(f"план:     v{st['plan']['version']} ({st['plan']['status']})")
    if st["tasks"]:
        print("Задачи:")
        print(render_checklist(st["tasks"]))
    details = runtime.pending_details()
    if details:
        print(details)
    if st.get("final_answer"):
        print(f"ответ:    {st['final_answer']}")
    return 0


def cmd_tasks(args) -> int:
    st = AgentRuntime(args.policy).load(args.run_id)
    if not st["tasks"]:
        print("У запуска нет задач (план ещё не утверждён или запуск без plan mode).")
        return 0
    print("Задачи:")
    print(render_checklist(st["tasks"]))
    for task in st["tasks"]:
        print(f"\n{task['id']} [{task['status']}] {task['description']}")
        print("  проверки: " + "; ".join(describe_check(c) for c in task["checks"]))
        if task.get("evidence"):
            print("  подтверждение runtime: " + json.dumps(task["evidence"], ensure_ascii=False))
        if task.get("last_failures") and task["status"] != "done":
            print("  последняя неудачная проверка: " + "; ".join(f["reason"] for f in task["last_failures"]))
    return 0


def cmd_calls(args) -> int:
    runtime = AgentRuntime(args.policy)
    runtime.load(args.run_id)
    for record in runtime.all_calls():
        where = record["task_id"] or ("planning" if record["phase"] == PLANNING else "-")
        attempts = f" attempts={len(record['attempts'])}" if record["attempts"] else ""
        print(f"{record['id']:<28} {where:<9} {record['status']:<22} {record['name']}({format_args(record)}){attempts}")
    return 0


def cmd_list(args) -> int:
    runtime = AgentRuntime(args.policy)
    runs = runtime.store.list_runs()
    if not runs:
        print("Запусков пока нет.")
    for st in runs:
        print(f"{st['run_id']}  {st['status']:<12} {st['mode']:<5} {shorten(st['task'], 70)}")
    return 0


def cmd_diff(args) -> int:
    report = AgentRuntime(args.policy).diff_report(args.run_id)
    if report["unresolved_calls"]:
        print(f"ВНИМАНИЕ: есть неопределённые после сбоя вызовы {report['unresolved_calls']}; "
              f"их изменения ещё не отнесены к запуску (resume / recover).")
    if report["rolled_back"]:
        print(f"Запуск откатан: {report['rolled_back']['at']}")
    print(f"Изменения файлов запуском {report['run_id']} (before -> after):")
    if not report["files"]:
        print("  (запуск не менял файлы)")
    for item in report["files"]:
        current = "совпадает с after" if not item["externally_modified"] else "ИЗМЕНЁН ИЗВНЕ (откат даст конфликт)"
        print(f"\n{item['op'].upper()} {item['path']}")
        print(f"  before:  {item['before'] or '(не было)'}")
        print(f"  after:   {item['after'] or '(удалён)'}")
        print(f"  сейчас:  {item['current'] or '(нет файла)'}  — {current}")
        print("\n".join("  " + line for line in item["diff"].rstrip("\n").splitlines()))
    if args.entries:
        print("\nЖурнал действий:")
        for entry in report["entries"]:
            label = entry["tool"] or entry["kind"]
            print(f"#{entry['seq']} {entry['kind']} {label} call={entry['call_id']} task={entry['task_id']} at={entry['at']}")
            print(format_changes(entry["changes"]))
    return 0


def cmd_rollback(args) -> int:
    result = AgentRuntime(args.policy).rollback(args.run_id)
    print(f"Откат выполнен. Восстановлены: {result['restored'] or '-'}; удалены созданные запуском: "
          f"{result['deleted'] or '-'}; без чистых изменений: {result['unchanged'] or '-'}")
    return 0


def cmd_policy_check(args) -> int:
    engine = PolicyEngine(args.policy)
    policy = engine.load()
    spec = REGISTRY.get(args.tool)
    if spec is None:
        raise UserError(f"unknown tool {args.tool!r}; known: {', '.join(REGISTRY)}")
    call_args = json.loads(args.arguments)
    from agent.jsonschema import validate
    problems = validate(call_args, spec.parameters)
    if problems:
        print("schema: ОТКЛОНЕНО — " + "; ".join(problems))
        return 1
    print("schema: ok")
    if spec.effect != "control":
        try:
            checked = engine.check_call(policy, spec.name, call_args)
            print(f"policy: разрешено (путь/cwd: {checked.rel})")
        except (PolicyViolation, ArgumentProblem) as exc:
            print(f"policy: ОТКЛОНЕНО — {exc}")
            return 1
    phase = PLANNING if args.phase == "planning" else EXECUTION
    requirement = "forbidden" if phase not in spec.phases else approval_requirement(spec.effect, args.mode, phase)
    meaning = {"auto": "исполняется сразу", "confirm": "нужно подтверждение пользователя",
               "forbidden": "запрещено в этой фазе"}[requirement]
    print(f"режим {args.mode}, фаза {phase}: {meaning}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--policy", default=os.environ.get("AGENT_POLICY", "policy.json"),
                        help="путь к policy-файлу (по умолчанию ./policy.json)")
    driving = argparse.ArgumentParser(add_help=False)
    driving.add_argument("--scripted", help="JSON-сценарий ответов модели вместо LLM (детерминированное демо)")
    driving.add_argument("--crash-at", help="имитация сбоя: точка[:инструмент], напр. after_tool_execute:write_file")
    driving.add_argument("-i", "--interactive", action="store_true", help="подтверждать действия прямо в консоли")

    parser = argparse.ArgumentParser(prog="python3 -m agent", description="Coding agent с tool calling")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", parents=[common, driving], help="новый запуск агента")
    p.add_argument("task", help="задача для агента")
    p.add_argument("--mode", choices=MODES, default="safe", help="safe (по умолчанию) или auto")
    p.add_argument("--no-plan", action="store_true", help="без Plan Mode (сразу исполнение, без задач)")
    p.add_argument("--budget", help="переопределить лимиты: model_calls=20,tool_calls=40,side_effects=10,result_bytes=50000")
    p.add_argument("--no-skills", action="store_true", help="версия A: без каталога скиллов и load_skill")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("chat", parents=[common], help="интерактивный диалог (основной режим)")
    p.add_argument("--session", help="продолжить сохранённую сессию")
    p.add_argument("--mode", choices=MODES, default="safe")
    p.add_argument("--plan", action="store_true", help="каждое сообщение начинать с Plan Mode")
    p.add_argument("--no-skills", action="store_true", help="версия A: без скиллов")
    p.add_argument("--budget", help="лимиты на каждый запуск, как у run")
    p.add_argument("--scripted", help="JSON-сценарий ответов модели вместо LLM")
    p.set_defaults(func=cmd_chat)

    p = sub.add_parser("ui", parents=[common], help="локальный веб-интерфейс поверх того же runtime")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--scripted", help="JSON-сценарий ответов модели вместо LLM (для демо и тестов)")
    p.set_defaults(func=cmd_ui)

    p = sub.add_parser("sessions", parents=[common], help="список сессий")
    p.set_defaults(func=cmd_sessions)

    p = sub.add_parser("eval", help="A/B evals: run | summary")
    eval_sub = p.add_subparsers(dest="eval_command", required=True)
    q = eval_sub.add_parser("run", help="подготовить состояние, прогнать A/B trials, проверить и собрать сводку")
    q.add_argument("suite", help="файл набора сценариев, напр. evals/suite.json")
    q.add_argument("--out", required=True, help="папка результатов; повторный запуск продолжает без дублей")
    q.add_argument("--only", help="только эти сценарии через запятую")
    q.add_argument("--trials", type=int, help="число trials на версию (по умолчанию из набора)")
    q.add_argument("--scripted-dir", help="детерминированные ответы модели для тестов runner (не для настоящих trials)")
    q.set_defaults(func=cmd_eval)
    q = eval_sub.add_parser("summary", help="пересобрать summary.md по results.jsonl")
    q.add_argument("out")
    q.set_defaults(func=cmd_eval)

    p = sub.add_parser("market", help="маркет скиллов: list | search | install | check | set | verify | rollback | events")
    market_sub = p.add_subparsers(dest="market_command", required=True)
    q = market_sub.add_parser("list", parents=[common], help="установленные скиллы, источники, кандидаты")
    q.set_defaults(func=cmd_market)
    q = market_sub.add_parser("search", parents=[common], help="найти не установленные скиллы")
    q.add_argument("query")
    q.set_defaults(func=cmd_market)
    q = market_sub.add_parser("install", parents=[common], help="установить скилл из источника")
    q.add_argument("source")
    q.add_argument("name")
    q.set_defaults(func=cmd_market)
    q = market_sub.add_parser("check", parents=[common], help="проверить маркет (с учётом интервала)")
    q.add_argument("--force", action="store_true", help="проверить сейчас, не дожидаясь интервала")
    q.set_defaults(func=cmd_market)
    q = market_sub.add_parser("set", parents=[common], help="включить/выключить проверку и автообновление источника")
    q.add_argument("source")
    q.add_argument("--check", choices=("on", "off"))
    q.add_argument("--auto-update", dest="auto_update", choices=("on", "off"))
    q.add_argument("--verify-updates", dest="verify_updates", choices=("on", "off"))
    q.set_defaults(func=cmd_market)
    q = market_sub.add_parser("rollback", parents=[common], help="откатить скилл к предыдущей версии")
    q.add_argument("name")
    q.set_defaults(func=cmd_market)
    q = market_sub.add_parser("verify", parents=[common], help="проверить кандидата обновления через eval и принять/отклонить")
    q.add_argument("name")
    q.add_argument("--suite", default="evals/suite.json")
    q.add_argument("--out", help="папка результатов сравнения")
    q.add_argument("--trials", type=int)
    q.add_argument("--only")
    q.add_argument("--scripted-dir")
    q.set_defaults(func=cmd_market)
    q = market_sub.add_parser("events", parents=[common], help="последние события маркета")
    q.add_argument("--last", type=int, default=20)
    q.set_defaults(func=cmd_market)

    p = sub.add_parser("doctor", parents=[common], help="разбор сохранённой сессии: расходы, частоты, находки")
    p.add_argument("session_id")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("stats", parents=[common], help="статистика сессии по журналу событий")
    p.add_argument("session_id")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("approve", parents=[common, driving], help="разрешить ожидающее действие")
    p.add_argument("run_id")
    p.add_argument("--call", help="id вызова (для проверки)")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("deny", parents=[common, driving], help="отклонить ожидающее действие")
    p.add_argument("run_id")
    p.add_argument("--reason", help="причина (её получит модель)")
    p.add_argument("--call", help="id вызова (для проверки)")
    p.set_defaults(func=cmd_deny)

    p = sub.add_parser("plan", parents=[common], help="Plan Mode: show | approve | reject")
    plan_sub = p.add_subparsers(dest="plan_command", required=True)
    for name, text in (("show", "показать план"), ("approve", "утвердить план"), ("reject", "отклонить план")):
        q = plan_sub.add_parser(name, parents=[common, driving] if name != "show" else [common], help=text)
        q.add_argument("run_id")
        if name == "reject":
            q.add_argument("--reason", help="что исправить (получит модель)")
        q.set_defaults(func=cmd_plan)

    p = sub.add_parser("resume", parents=[common, driving], help="продолжить запуск по run_id")
    p.add_argument("run_id")
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("recover", parents=[common, driving], help="решение по неопределённому вызову или конфликту")
    p.add_argument("run_id")
    p.add_argument("--action", required=True, choices=("retry", "fail", "abort"))
    p.add_argument("--changes", choices=("attribute", "external"),
                   help="обнаруженные изменения дерева: attribute — сделаны этим запуском, external — посторонние")
    p.set_defaults(func=cmd_recover)

    p = sub.add_parser("abort", parents=[common], help="завершить запуск")
    p.add_argument("run_id")
    p.add_argument("--reason")
    p.set_defaults(func=cmd_abort)

    for name, func, text in (("status", cmd_status, "состояние запуска"), ("tasks", cmd_tasks, "задачи и подтверждения"),
                             ("calls", cmd_calls, "журнал tool calls"), ("rollback", cmd_rollback, "откатить изменения запуска")):
        p = sub.add_parser(name, parents=[common], help=text)
        p.add_argument("run_id")
        p.set_defaults(func=func)

    p = sub.add_parser("diff", parents=[common], help="изменения файлов запуска")
    p.add_argument("run_id")
    p.add_argument("--entries", action="store_true", help="показать также diff каждого действия")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("list", parents=[common], help="список запусков")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("policy-check", parents=[common], help="решение policy для одного вызова (без модели)")
    p.add_argument("tool", help="имя инструмента, напр. Bash")
    p.add_argument("arguments", help="JSON аргументов, напр. '{\"command\": \"ls\", \"cwd\": \".\"}'")
    p.add_argument("--mode", choices=MODES, default="safe")
    p.add_argument("--phase", choices=("planning", "execution"), default="execution")
    p.set_defaults(func=cmd_policy_check)
    return parser


def main(argv=None) -> int:
    load_dotenv(Path.cwd() / ".env")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args) or 0
    except (UserError, PolicyError, RunNotFound, RunBusy, RollbackError, ValueError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
    except RollbackConflict as exc:
        print("КОНФЛИКТ: откат не выполнен, ни один файл не перезаписан.", file=sys.stderr)
        for problem in exc.problems:
            print(f"  {problem['path']}: {problem['reason']}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("\nПрервано. Состояние сохранено; продолжить: python3 -m agent resume <run_id>", file=sys.stderr)
        return 130
