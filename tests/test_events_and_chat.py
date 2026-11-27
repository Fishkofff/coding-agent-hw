import json
import os
import re
import subprocess
import sys
import unittest

from agent.llm import ModelResponse
from agent.stats import compute
from tests.helpers import ROOT, Project, act, call, events, say

REQUIRED = {"id", "time", "type", "data", "session_id", "run_id"}


class EventLogTest(unittest.TestCase):
    def test_every_event_has_required_fields_and_links_to_session_and_run(self):
        p = Project(self, skills=True, mcp={})
        steps = [act(call("load_skill", "s1", name="open-tasks-export")),
                 act(call("mcp__tracker__list_tasks", "m1"), call("read_file", "r1", path="missing.txt")),
                 say("done")]
        rt = p.runtime(steps)
        run_id = rt.run("export", mode="auto", plan_mode=False)
        state = p.state(run_id)
        items = events(p, run_id)
        ids = [e["id"] for e in items]
        self.assertEqual(len(ids), len(set(ids)))
        for event in items:
            self.assertEqual(set(event), REQUIRED)
            self.assertEqual((event["run_id"], event["session_id"]), (run_id, state["session_id"]))
        session_log = p.root / "runs" / "sessions" / state["session_id"] / "events.jsonl"
        self.assertEqual(len(session_log.read_text(encoding="utf-8").splitlines()), len(items))
        types = [e["type"] for e in items]
        for expected in ("user_message", "skills_discovered", "skill_loaded", "mcp_server_connected", "model_request",
                         "model_response", "tool_call", "tool_result", "run_completed"):
            self.assertIn(expected, types)
        failed = [e for e in items if e["type"] == "tool_result" and not e["data"]["ok"]]
        self.assertEqual(failed[0]["data"]["error"], "invalid_arguments")

    def test_api_key_is_not_logged(self):
        p = Project(self)
        os.environ["OPENAI_API_KEY"] = "sk-secret-value-123"
        self.addCleanup(os.environ.pop, "OPENAI_API_KEY", None)
        run_id = p.runtime([say("ok")]).run("x", mode="auto", plan_mode=False, model_info={"kind": "test"})
        text = (p.root / "runs" / run_id / "events.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("sk-secret-value-123", text)


class UsageTest(unittest.TestCase):
    def run_with(self, usages):
        p = Project(self)
        steps = [ModelResponse("", [call("read_file", f"r{i}", path="order.py")], usage=u) for i, u in enumerate(usages[:-1])]
        steps.append(ModelResponse("done", usage=usages[-1]))
        run_id = p.runtime(steps).run("x", mode="auto", plan_mode=False)
        return p.state(run_id)["usage"], compute(events(p, run_id))

    def test_complete_usage_is_summed_and_cached_tokens_are_not_double_counted(self):
        totals, stats = self.run_with([
            {"prompt_tokens": 100, "completion_tokens": 10, "prompt_tokens_details": {"cached_tokens": 60}},
            {"prompt_tokens": 200, "completion_tokens": 20}])
        self.assertEqual((totals["input_tokens"], totals["output_tokens"], totals["cached_input_tokens"]), (300, 30, 60))
        self.assertEqual(stats["usage_total"]["status"], "complete")
        self.assertGreater(totals["estimated_input_tokens"], 0)

    def test_missing_usage_is_unknown_and_partial_usage_is_marked(self):
        totals, stats = self.run_with([None, None])
        self.assertEqual(stats["usage_total"]["status"], "unknown")
        totals, stats = self.run_with([{"prompt_tokens": 50, "completion_tokens": 5}, None])
        self.assertEqual(stats["usage_total"]["status"], "partial")
        self.assertEqual(stats["usage_total"]["input_tokens"], 50)


class StatsTest(unittest.TestCase):
    def test_counts_skills_tools_and_mcp_by_server(self):
        p = Project(self, skills=True, mcp={})
        steps = [act(call("load_skill", "s1", name="sprint-report")),
                 act(call("mcp__tracker__list_tasks", "m1"), call("mcp__tracker__get_task", "m2", id="T1"),
                     call("mcp__tracker__get_task", "m3", id="T2")),
                 act(call("load_skill", "s2", name="sprint-report"), call("read_file", "r1", path="order.py")),
                 say("done")]
        run_id = p.runtime(steps).run("x", mode="auto", plan_mode=False)
        stats = compute(events(p, run_id))
        self.assertEqual(stats["skill_loads"], {"sprint-report": 2})
        self.assertEqual(stats["unique_skills"], 1)
        self.assertEqual(stats["mcp_calls"], {"tracker/list_tasks": 1, "tracker/get_task": 2})
        self.assertEqual(stats["tool_calls"]["load_skill"], 2)
        self.assertEqual(stats["unique_tools"], 4)


class ChatTest(unittest.TestCase):
    def chat(self, p, script, stdin, *extra):
        script_path = p.root / "script.json"
        script_path.write_text(json.dumps({"steps": script}, ensure_ascii=False), encoding="utf-8")
        proc = subprocess.run([sys.executable, "-m", "agent", "chat", "--policy", str(p.policy_path),
                               "--scripted", str(script_path), *extra],
                              cwd=ROOT, input=stdin, capture_output=True, text=True, timeout=120,
                              env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(p.root)})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc.stdout

    def test_dialog_keeps_context_confirms_denies_and_shows_stats(self):
        p = Project(self, skills=True, mcp={})
        script = [
            {"tool_calls": [{"id": "c1", "name": "write_file", "arguments": {"path": "a.txt", "content": "1"}}]},
            {"content": "Записал a.txt"},
            {"tool_calls": [{"id": "c2", "name": "write_file", "arguments": {"path": "b.txt", "content": "2"}}]},
            {"content": "Хорошо, b.txt не создаю"},
        ]
        out = self.chat(p, script, "создай a.txt\ny\nа теперь b.txt\nn\nне надо\n/stats\n/exit\n")
        self.assertIn("Агент: Записал a.txt", out)
        self.assertIn("Агент: Хорошо, b.txt не создаю", out)
        self.assertTrue((p.ws / "a.txt").exists())
        self.assertFalse((p.ws / "b.txt").exists())
        self.assertIn("Статистика сессии", out)
        self.assertIn("write_file: 2", out)
        session_id = re.search(r"Сессия (s-\S+)", out).group(1)
        session = json.loads((p.root / "runs" / "sessions" / session_id / "session.json").read_text(encoding="utf-8"))
        self.assertEqual(len(session["runs"]), 2)
        second = json.loads((p.root / "runs" / session["runs"][1] / "state.json").read_text(encoding="utf-8"))
        texts = [m.get("content") for m in second["messages"]]
        self.assertTrue(any("создай a.txt" in (t or "") for t in texts))
        self.assertTrue(any("Записал a.txt" in (t or "") for t in texts))
        denied = [json.loads(m["content"]) for m in second["messages"] if m["role"] == "tool"]
        self.assertEqual(denied[-1]["error"], "denied_by_user")
        self.assertEqual(denied[-1]["reason"], "не надо")

    def test_session_can_be_continued_after_restart(self):
        p = Project(self)
        out = self.chat(p, [{"content": "первый ответ"}], "привет\n/exit\n")
        session_id = re.search(r"Сессия (s-\S+)", out).group(1)
        out = self.chat(p, [{"content": "первый ответ"}, {"content": "второй ответ"}], "ещё\n/exit\n",
                        "--session", session_id)
        self.assertIn("Агент: второй ответ", out)
        session = json.loads((p.root / "runs" / "sessions" / session_id / "session.json").read_text(encoding="utf-8"))
        self.assertEqual(len(session["runs"]), 2)


if __name__ == "__main__":
    unittest.main()
