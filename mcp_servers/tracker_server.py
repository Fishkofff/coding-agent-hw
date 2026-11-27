import argparse
import json
import os
import sys
import time

PROTOCOL_VERSION = "2025-06-18"
STATUSES = ["open", "in_progress", "closed"]

TOOLS = [
    {
        "name": "list_tasks",
        "description": "List tasks of the study tracker. Optional filters: status (open, in_progress, closed) and assignee. Returns a JSON array of tasks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": STATUSES},
                "assignee": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_task",
        "description": "Get one task by its id, e.g. T3.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string", "minLength": 1}},
            "required": ["id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "update_task_status",
        "description": "Change the status of a task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "minLength": 1},
                "status": {"type": "string", "enum": STATUSES},
            },
            "required": ["id", "status"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
]


def load_tasks(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_tasks(path, tasks):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(tasks, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def text_result(data, is_error=False):
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    result = {"content": [{"type": "text", "text": text}], "isError": is_error}
    if not is_error and not isinstance(data, str):
        result["structuredContent"] = {"result": data}
    return result


def call_tool(name, args, data_path):
    fail = os.environ.get("TRACKER_FAIL", "")
    if fail == "error":
        return text_result("tracker backend is unavailable: HTTP 503 Service Unavailable", True)
    if fail == "timeout":
        time.sleep(3600)
    if fail == "crash":
        sys.exit(3)
    tasks = load_tasks(data_path)
    if name == "list_tasks":
        result = [t for t in tasks
                  if ("status" not in args or t.get("status") == args["status"])
                  and ("assignee" not in args or t.get("assignee") == args["assignee"])]
        return text_result(result)
    if name == "get_task":
        for task in tasks:
            if task.get("id") == args.get("id"):
                return text_result(task)
        return text_result(f"task {args.get('id')!r} not found", True)
    if name == "update_task_status":
        for task in tasks:
            if task.get("id") == args.get("id"):
                task["status"] = args["status"]
                save_tasks(data_path, tasks)
                return text_result(task)
        return text_result(f"task {args.get('id')!r} not found", True)
    return None


def handle(message, data_path):
    method = message.get("method")
    params = message.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "study-tracker", "version": "1.0.0"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        result = call_tool(params.get("name"), params.get("arguments") or {}, data_path)
        if result is None:
            raise LookupError(f"unknown tool {params.get('name')!r}")
        return result
    raise LookupError(f"method {method!r} is not supported")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    args = parser.parse_args()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if "id" not in message:
            continue
        try:
            reply = {"jsonrpc": "2.0", "id": message["id"], "result": handle(message, args.data)}
        except LookupError as exc:
            reply = {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": str(exc)}}
        except (OSError, ValueError) as exc:
            reply = {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32603, "message": str(exc)}}
        sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
