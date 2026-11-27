from __future__ import annotations

import json
from collections import Counter, OrderedDict

from agent import usage
from agent.stats import compute, run_usage
from agent.util import canonical_json

READ_TOOLS = ("read_file", "list_files")

RULES = {
    "repeated_error": "один и тот же вызов (имя + аргументы) завершился ошибкой 2 и более раз в одном run",
    "unchanged_reread": "одинаковое чтение повторено, а между повторами не было ни одного действия с побочным эффектом",
    "skill_without_actions": "скилл загружен, но после загрузки в этом run не было ни одного вызова других инструментов",
    "finished_without_verification": "run завершён, файл записан, но после последней записи не было ни read_file этого файла, ни Bash",
}


def by_run(events: list[dict]) -> "OrderedDict[str, list]":
    runs = OrderedDict()
    for event in events:
        if event.get("run_id"):
            runs.setdefault(event["run_id"], []).append(event)
    return runs


def call_signature(data: dict) -> str:
    return data["name"] + " " + canonical_json(data.get("arguments") or {})


def result_bytes(event: dict) -> int:
    return len(json.dumps(event["data"].get("result"), ensure_ascii=False).encode("utf-8"))


def check_repeated_errors(run_id: str, events: list[dict]) -> list[dict]:
    calls = {e["data"]["call_id"]: e for e in events if e["type"] == "tool_call"}
    failures = {}
    for event in events:
        if event["type"] == "tool_result" and not event["data"].get("ok"):
            call = calls.get(event["data"]["call_id"])
            if call is None:
                continue
            failures.setdefault(call_signature(call["data"]), []).append((call, event))
    findings = []
    for signature, items in failures.items():
        if len(items) < 2:
            continue
        errors = Counter(result["data"].get("error") for _call, result in items)
        findings.append({
            "rule": "repeated_error", "run_id": run_id,
            "event_ids": [e["id"] for pair in items for e in pair],
            "counts": {"failed_attempts": len(items), "errors": dict(errors)},
            "interpretation": f"модель повторила вызов {signature[:160]} {len(items)} раз(а) и каждый раз получала "
                              f"ошибку ({', '.join(map(str, errors))}); повтор не менял исход",
            "suggestion": "добавить в системный промпт или в скилл правило: после ошибки инструмента менять аргументы "
                          "или подход; если ошибка policy — заранее перечислить разрешённые действия в описании инструмента",
        })
    return findings


def check_unchanged_reread(run_id: str, events: list[dict]) -> list[dict]:
    calls = {e["data"]["call_id"]: e for e in events if e["type"] == "tool_call"}
    seen = {}
    groups = {}
    for event in events:
        if event["type"] != "tool_result" or event["data"]["status"] != "completed" or not event["data"].get("ok"):
            continue
        call = calls.get(event["data"]["call_id"])
        if call is None:
            continue
        effect = call["data"].get("effect") or ("read" if call["data"]["name"] in READ_TOOLS else "write")
        if effect != "read":
            seen.clear()
            continue
        signature = call_signature(call["data"])
        if signature in seen:
            groups.setdefault(signature, [seen[signature]]).append((call, event))
        else:
            seen[signature] = (call, event)
    findings = []
    for signature, items in groups.items():
        repeated = items[1:]
        findings.append({
            "rule": "unchanged_reread", "run_id": run_id,
            "event_ids": [e["id"] for pair in items for e in pair],
            "counts": {"reads": len(items), "repeated_reads": len(repeated),
                       "repeated_result_bytes": sum(result_bytes(r) for _c, r in repeated)},
            "interpretation": f"{signature[:160]} выполнено {len(items)} раз(а) без действий с побочным эффектом "
                              f"между повторами; повтор мог быть лишним, но мог быть и осознанной перепроверкой",
            "suggestion": "в инструкциях скилла явно сказать, что после первого чтения данные уже в контексте; "
                          "перечитывать только после изменения файла",
        })
    return findings


def check_skill_without_actions(run_id: str, events: list[dict]) -> list[dict]:
    findings = []
    for index, event in enumerate(events):
        if event["type"] != "skill_loaded":
            continue
        later = [e for e in events[index + 1:] if e["type"] == "tool_call" and e["data"]["name"] != "load_skill"]
        if later:
            continue
        findings.append({
            "rule": "skill_without_actions", "run_id": run_id, "event_ids": [event["id"]],
            "counts": {"tool_calls_after_skill": 0},
            "interpretation": f"скилл {event['data']['name']} загружен, но шаги скилла не выполнялись "
                              f"(после загрузки нет вызовов инструментов); загрузка не доказывает следование инструкциям",
            "suggestion": "уточнить description скилла, чтобы он выбирался только для подходящих задач, "
                          "или добавить в начало скилла явный первый шаг с вызовом инструмента",
        })
    return findings


def check_finished_without_verification(run_id: str, events: list[dict]) -> list[dict]:
    if not any(e["type"] == "run_completed" for e in events):
        return []
    calls = {e["data"]["call_id"]: e for e in events if e["type"] == "tool_call"}
    ordered = []
    for event in events:
        if event["type"] == "tool_result" and event["data"]["status"] == "completed" and event["data"].get("ok"):
            call = calls.get(event["data"]["call_id"])
            if call is not None:
                ordered.append((call, event))
    writes = [i for i, (call, _r) in enumerate(ordered) if call["data"]["name"] == "write_file"]
    if not writes:
        return []
    last = writes[-1]
    path = (ordered[last][0]["data"].get("arguments") or {}).get("path")
    after = ordered[last + 1:]
    verified = any(call["data"]["name"] == "Bash" or
                   (call["data"]["name"] == "read_file" and (call["data"].get("arguments") or {}).get("path") == path)
                   for call, _r in after)
    if verified:
        return []
    return [{
        "rule": "finished_without_verification", "run_id": run_id,
        "event_ids": [ordered[last][0]["id"], ordered[last][1]["id"]] +
                     [e["id"] for e in events if e["type"] == "run_completed"],
        "counts": {"writes": len(writes), "checks_after_last_write": 0},
        "interpretation": f"последняя запись {path} не была проверена: после неё нет read_file этого файла и нет Bash; "
                          f"итоговый ответ опирается только на намерение модели",
        "suggestion": "добавить в скилл шаг «перечитай записанный файл и сверь с данными» или запуск теста/проверки",
    }]


CHECKS = (check_repeated_errors, check_unchanged_reread, check_skill_without_actions, check_finished_without_verification)


def diagnose(events: list[dict]) -> dict:
    runs = by_run(events)
    usages = run_usage(events)
    costs = []
    findings = []
    limitations = []
    for run_id, run_events in runs.items():
        totals = usages.get(run_id, usage.initial())
        categories = Counter()
        result_size = Counter()
        for event in run_events:
            if event["type"] == "tool_call":
                categories[event["data"].get("kind") or "builtin"] += 1
            elif event["type"] == "tool_result":
                result_size[event["data"].get("kind") or "builtin"] += result_bytes(event)
        status = usage.completeness(totals)
        costs.append({"run_id": run_id, "usage": dict(totals, status=status),
                      "tool_calls_by_category": dict(categories), "result_bytes_by_category": dict(result_size)})
        if status in ("unknown", "partial"):
            limitations.append(f"run {run_id}: usage {status} — токены для этого run не суммируются полностью, "
                               f"доктор не оценивает «лишние токены»")
        for check in CHECKS:
            findings.extend(check(run_id, run_events))
    return {"rules": RULES, "costs": costs, "stats": compute(events), "findings": findings,
            "limitations": limitations, "doctor_usage": {"model_requests": 0, "method": "rules"}}


def render(report: dict) -> str:
    lines = ["Доктор сессии (правила, без LLM: собственный расход 0 запросов к модели)", "", "Расходы по запускам:"]
    for cost in report["costs"]:
        lines.append(f"  {cost['run_id']}: {usage.describe(cost['usage'])}")
        lines.append(f"    вызовы по категориям: {cost['tool_calls_by_category'] or '-'}; "
                     f"байты результатов: {cost['result_bytes_by_category'] or '-'}")
    stats = report["stats"]
    lines.append("Частота скиллов: " + (", ".join(f"{k}={v}" for k, v in stats["skill_loads"].items()) or "-"))
    lines.append("MCP-вызовы: " + (", ".join(f"{k}={v}" for k, v in stats["mcp_calls"].items()) or "-"))
    lines.append("")
    if not report["findings"]:
        lines.append("Находок нет (проверенные правила: " + ", ".join(report["rules"]) + ")")
    for number, item in enumerate(report["findings"], 1):
        lines.append(f"Находка {number}: {item['rule']} — {report['rules'][item['rule']]}")
        lines.append(f"  run_id: {item['run_id']}")
        lines.append(f"  события: {', '.join(item['event_ids'])}")
        lines.append(f"  счётчики: {json.dumps(item['counts'], ensure_ascii=False)}")
        lines.append(f"  интерпретация: {item['interpretation']}")
        lines.append(f"  предложение: {item['suggestion']}")
    if report["limitations"]:
        lines.append("")
        lines.append("Ограничения данных:")
        lines.extend(f"  - {item}" for item in report["limitations"])
    return "\n".join(lines)
