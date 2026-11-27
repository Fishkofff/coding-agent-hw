from __future__ import annotations

from collections import Counter, OrderedDict

from agent import usage


def tool_key(data: dict) -> str:
    if data.get("kind") == "mcp":
        return f"mcp:{data.get('server')}/{data.get('tool')}"
    return data.get("name") or "?"


def run_usage(events: list[dict]) -> "OrderedDict[str, dict]":
    runs = OrderedDict()
    for event in events:
        run_id = event.get("run_id")
        if not run_id:
            continue
        totals = runs.setdefault(run_id, usage.initial())
        data = event["data"]
        if event["type"] == "model_request":
            totals["requests"] += 1
            totals["estimated_input_tokens"] += data.get("estimated_input_tokens") or 0
        elif event["type"] == "model_response":
            norm = data.get("usage")
            if not norm:
                continue
            if norm.get("input_tokens") is None or norm.get("output_tokens") is None:
                totals["partial_usage"] += 1
            else:
                totals["with_usage"] += 1
            totals["input_tokens"] += norm.get("input_tokens") or 0
            totals["output_tokens"] += norm.get("output_tokens") or 0
            totals["cached_input_tokens"] += norm.get("cached_input_tokens") or 0
    return runs


def compute(events: list[dict]) -> dict:
    skill_loads = Counter()
    tool_calls = Counter()
    mcp_calls = Counter()
    outcomes = Counter()
    errors = []
    discovered = None
    invalid = []
    for event in events:
        data = event["data"]
        kind = event["type"]
        if kind == "skills_discovered":
            discovered = [s["name"] for s in data.get("skills", [])]
        elif kind == "skill_invalid":
            invalid.append(data)
        elif kind == "skill_loaded":
            skill_loads[data["name"]] += 1
        elif kind == "tool_call":
            tool_calls[tool_key(data)] += 1
            if data.get("kind") == "mcp":
                mcp_calls[f"{data.get('server')}/{data.get('tool')}"] += 1
        elif kind == "tool_result":
            outcomes[data["status"]] += 1
            if not data.get("ok"):
                errors.append({"event_id": event["id"], "run_id": event["run_id"], "tool": tool_key(data),
                               "status": data["status"], "error": data.get("error")})
        elif kind in ("model_error", "mcp_server_error", "run_failed"):
            errors.append({"event_id": event["id"], "run_id": event["run_id"], "tool": None, "status": kind,
                           "error": data.get("message") or data.get("error") or data.get("code")})
    runs = run_usage(events)
    return {
        "runs": list(runs),
        "skills_discovered": discovered,
        "skills_invalid": invalid,
        "skill_loads": dict(skill_loads),
        "unique_skills": len(skill_loads),
        "tool_calls": dict(tool_calls),
        "unique_tools": len(tool_calls),
        "mcp_calls": dict(mcp_calls),
        "outcomes": dict(outcomes),
        "errors": errors,
        "usage_by_run": {run_id: dict(totals, status=usage.completeness(totals)) for run_id, totals in runs.items()},
        "usage_total": dict(usage.merge(runs.values()), status=usage.completeness(usage.merge(runs.values()))),
    }


def render(stats: dict, title: str) -> str:
    lines = [f"Статистика {title}", f"Запусков (run): {len(stats['runs'])}"]
    discovered = stats["skills_discovered"]
    lines.append(f"Скиллов найдено: {len(discovered) if discovered is not None else 'скиллы выключены'}"
                 + (f" ({', '.join(discovered)})" if discovered else ""))
    if stats["skills_invalid"]:
        lines.append(f"Некорректных SKILL.md: {len(stats['skills_invalid'])}")
    lines.append(f"Загрузки скиллов (уникальных: {stats['unique_skills']}):")
    lines.extend(f"  {name}: {count}" for name, count in sorted(stats["skill_loads"].items())) if stats["skill_loads"] else lines.append("  -")
    lines.append(f"Вызовы инструментов (уникальных: {stats['unique_tools']}):")
    lines.extend(f"  {name}: {count}" for name, count in sorted(stats["tool_calls"].items())) if stats["tool_calls"] else lines.append("  -")
    lines.append("MCP-вызовы по серверу/инструменту:")
    lines.extend(f"  {name}: {count}" for name, count in sorted(stats["mcp_calls"].items())) if stats["mcp_calls"] else lines.append("  -")
    lines.append("Исходы вызовов: " + (", ".join(f"{k}={v}" for k, v in sorted(stats["outcomes"].items())) or "-"))
    lines.append(f"Ошибки и отказы: {len(stats['errors'])}")
    for item in stats["errors"][-10:]:
        lines.append(f"  [{item['event_id']}] run {item['run_id']}: {item['tool'] or ''} {item['status']} {item['error']}")
    lines.append("Токены по запускам (из usage API):")
    for run_id, totals in stats["usage_by_run"].items():
        lines.append(f"  {run_id}: {usage.describe(totals)}")
    lines.append("Итого по сессии: " + usage.describe(stats["usage_total"]))
    return "\n".join(lines)
