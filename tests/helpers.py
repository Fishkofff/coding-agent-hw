from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from agent.faults import Faults
from agent.llm import ModelResponse, ScriptedModel, ToolCallRequest
from agent.report import Reporter
from agent.runtime import AgentRuntime

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_ORDER = (ROOT / "workspace" / "order.py").read_text(encoding="utf-8")
ORIGINAL_TEST = (ROOT / "workspace" / "test_order.py").read_text(encoding="utf-8")
FIXED_ORDER = '''def calculate_total(prices: list[float], discount_percent: float) -> float:
    if not 0 <= discount_percent <= 100:
        raise ValueError("discount_percent must be between 0 and 100")
    return sum(prices) * (100 - discount_percent) / 100
'''
TEST_CMD = "python3 -m unittest -v"


def call(tool_name: str, call_id: str, **arguments) -> ToolCallRequest:
    return ToolCallRequest(id=call_id, name=tool_name, arguments=json.dumps(arguments, ensure_ascii=False))


def act(*calls: ToolCallRequest, text: str = "") -> ModelResponse:
    return ModelResponse(content=text, tool_calls=list(calls))


def say(text: str = "done") -> ModelResponse:
    return ModelResponse(content=text)


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tool_messages(state: dict) -> list[tuple[str, dict]]:
    return [(m["tool_call_id"], json.loads(m["content"])) for m in state["messages"] if m["role"] == "tool"]


def tool_results(state: dict) -> dict:
    return dict(tool_messages(state))


def tracker_server(root, env=None, allow=("list_tasks", "get_task"), deny=("update_task_status",),
                   timeout=10, command=None, effects=None):
    return {
        "command": command or sys.executable,
        "args": [str(ROOT / "mcp_servers" / "tracker_server.py"), "--data", str(root / "data" / "tracker.json")],
        "cwd": str(root),
        "env": env or {},
        "timeout_sec": timeout,
        "tools": {"allow": list(allow), "deny": list(deny)},
        "effects": effects or {"list_tasks": "read", "get_task": "read", "update_task_status": "write"},
    }


def events(project, run_id: str) -> list[dict]:
    path = project.root / "runs" / run_id / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class Project:
    def __init__(self, testcase, *, allow=None, deny=None, dirs_allow=None, dirs_deny=None,
                 budget=None, bash=None, files=None, extra_files=None, skills=False, mcp=None):
        self.root = Path(os.path.realpath(tempfile.mkdtemp(prefix="agent-test-")))
        testcase.addCleanup(shutil.rmtree, self.root, True)
        self.ws = self.root / "workspace"
        self.ws.mkdir()
        (self.ws / "order.py").write_text(ORIGINAL_ORDER, encoding="utf-8")
        (self.ws / "test_order.py").write_text(ORIGINAL_TEST, encoding="utf-8")
        for rel, content in (extra_files or {}).items():
            path = self.ws / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self.policy_path = self.root / "policy.json"
        self.policy = {
            "workspace": "workspace",
            "runs_dir": "runs",
            "commands": {"allow": allow if allow is not None else [TEST_CMD, "ls"],
                         "deny": deny if deny is not None else ["rm", "bash -c"]},
            "directories": {"allow": dirs_allow or ["workspace"],
                            "deny": dirs_deny if dirs_deny is not None else ["workspace/private"]},
            "bash": bash or {"timeout_sec": 30, "max_output_bytes": 20000},
            "budget": budget or {"model_calls": 50, "tool_calls": 100, "side_effects": 50, "result_bytes": 500000},
        }
        if files:
            self.policy["files"] = files
        if skills:
            shutil.copytree(ROOT / "skills", self.root / "skills")
            for directory in (ROOT / "skills_installed").iterdir():
                if directory.is_dir():
                    shutil.copytree(directory, self.root / "skills" / directory.name)
            self.policy["skills"] = {"dirs": ["skills"]}
        if mcp is not None:
            (self.root / "data").mkdir()
            shutil.copy(ROOT / "data" / "tracker.json", self.root / "data" / "tracker.json")
            self.policy["mcp"] = {"servers": {"tracker": tracker_server(self.root, **mcp)}}
        self.write_policy()
        self.out = io.StringIO()
        self.cleanup = []
        testcase.addCleanup(lambda: [rt.close() for rt in self.cleanup])

    def write_policy(self) -> None:
        self.policy_path.write_text(json.dumps(self.policy, indent=2), encoding="utf-8")

    def runtime(self, steps, *, crash_at: str | None = None, executor=None) -> AgentRuntime:
        model = steps if isinstance(steps, ScriptedModel) else ScriptedModel(steps)
        runtime = AgentRuntime(self.policy_path, model, reporter=Reporter(self.out), faults=Faults(crash_at),
                               executor=executor)
        self.cleanup.append(runtime)
        return runtime

    def read(self, rel: str) -> str:
        return (self.ws / rel).read_text(encoding="utf-8")

    def sha(self, rel: str) -> str:
        return sha_bytes((self.ws / rel).read_bytes())

    def state(self, run_id: str) -> dict:
        return json.loads((self.root / "runs" / run_id / "state.json").read_text(encoding="utf-8"))

    @property
    def output(self) -> str:
        return self.out.getvalue()
