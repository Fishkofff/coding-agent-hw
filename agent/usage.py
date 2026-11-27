from __future__ import annotations


def initial() -> dict:
    return {"requests": 0, "with_usage": 0, "partial_usage": 0, "input_tokens": 0, "output_tokens": 0,
            "cached_input_tokens": 0, "estimated_input_tokens": 0}


def normalize(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    inp = raw.get("prompt_tokens", raw.get("input_tokens"))
    out = raw.get("completion_tokens", raw.get("output_tokens"))
    details = raw.get("prompt_tokens_details") or raw.get("input_tokens_details") or {}
    cached = details.get("cached_tokens") if isinstance(details, dict) else None
    if not isinstance(inp, int) and not isinstance(out, int):
        return None
    return {"input_tokens": inp if isinstance(inp, int) else None,
            "output_tokens": out if isinstance(out, int) else None,
            "cached_input_tokens": cached if isinstance(cached, int) else 0}


def record(totals: dict, raw, estimated_input: int) -> dict | None:
    norm = normalize(raw)
    totals["requests"] += 1
    totals["estimated_input_tokens"] += estimated_input
    if norm is None:
        return None
    if norm["input_tokens"] is None or norm["output_tokens"] is None:
        totals["partial_usage"] += 1
    else:
        totals["with_usage"] += 1
    totals["input_tokens"] += norm["input_tokens"] or 0
    totals["output_tokens"] += norm["output_tokens"] or 0
    totals["cached_input_tokens"] += norm["cached_input_tokens"]
    return norm


def record_failed(totals: dict, estimated_input: int) -> None:
    totals["requests"] += 1
    totals["estimated_input_tokens"] += estimated_input


def completeness(totals: dict) -> str:
    if totals["requests"] == 0:
        return "no requests"
    if totals["with_usage"] == totals["requests"]:
        return "complete"
    if totals["with_usage"] == 0 and totals["partial_usage"] == 0:
        return "unknown"
    return "partial"


def merge(items) -> dict:
    result = initial()
    for item in items:
        for key in result:
            result[key] += item.get(key, 0)
    return result


def describe(totals: dict) -> str:
    status = completeness(totals)
    if status == "unknown":
        tokens = "input tokens: unknown; output tokens: unknown"
    else:
        tokens = f"input tokens: {totals['input_tokens']}; output tokens: {totals['output_tokens']}"
        if totals["cached_input_tokens"]:
            tokens += f" (из них cached input: {totals['cached_input_tokens']})"
        if status == "partial":
            tokens += f" [неполные данные: usage есть у {totals['with_usage']} из {totals['requests']} запросов]"
    return (f"Запросов к модели: {totals['requests']}; {tokens}; "
            f"оценка input по символам: ~{totals['estimated_input_tokens']}")
