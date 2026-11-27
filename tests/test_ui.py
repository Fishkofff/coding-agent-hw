import json
import shutil
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from agent.llm import ScriptedModel
from agent.ui import Controller, make_handler
from tests.helpers import ROOT, Project


class UiApiTest(unittest.TestCase):
    def setUp(self):
        self.p = Project(self, skills=True, mcp={})
        shutil.copytree(ROOT / "market", self.p.root / "market")
        self.p.policy["market"] = {"install_dir": "skills_installed", "agent_install": True,
                                   "sources": {"study": {"index": "market/index-v1.json"}}}
        self.p.write_policy()
        factory = lambda: (ScriptedModel.from_file(ROOT / "demo" / "scripted_chat.json"), {"kind": "scripted", "api_key": "sk-x"})
        self.controller = Controller(self.p.policy_path, factory)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.controller))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(lambda: [rt.close() for rt in self.controller.runtimes.values()])
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def api(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(f"{self.base}/api/{path}", data=data, headers={"Content-Type": "application/json"})
        try:
            return json.loads(urllib.request.urlopen(request, timeout=30).read())
        except urllib.error.HTTPError as exc:
            return {"http": exc.code, **json.loads(exc.read())}

    def wait(self, sid, predicate):
        for _ in range(100):
            state = self.api(f"sessions/{sid}")
            if not state["busy"] and predicate(state):
                return state
            time.sleep(0.05)
        self.fail("timeout")

    def test_dialog_with_confirmation_events_stats_and_doctor(self):
        self.assertIn(b"<html", urllib.request.urlopen(self.base + "/").read())
        sid = self.api("sessions", {"skills": True})["session_id"]
        self.api(f"sessions/{sid}/message", {"text": "Выгрузи открытые задачи", "mode": "safe"})
        state = self.wait(sid, lambda s: s["pending"])
        self.assertEqual(state["pending"]["kind"], "confirmation")
        self.api(f"sessions/{sid}/decision", {"approve": True})
        state = self.wait(sid, lambda s: s["runs"] and s["runs"][-1]["status"] == "completed")
        self.api(f"sessions/{sid}/message", {"text": "Какие в работе?", "mode": "safe"})
        state = self.wait(sid, lambda s: len(s["runs"]) == 2 and s["runs"][-1]["status"] == "completed")
        self.assertIn("T3", state["runs"][1]["final_answer"])
        events = self.api(f"sessions/{sid}/events?after=0")
        types = {e["type"] for e in events["events"]}
        self.assertTrue({"skill_loaded", "tool_call", "tool_result", "user_decision"} <= types)
        later = self.api(f"sessions/{sid}/events?after={events['total']}")
        self.assertEqual(later["events"], [])
        stats = self.api(f"sessions/{sid}/stats")
        self.assertEqual(stats["mcp_calls"], {"tracker/list_tasks": 1, "tracker/get_task": 1})
        doctor = self.api(f"sessions/{sid}/doctor")
        self.assertEqual(doctor["doctor_usage"]["model_requests"], 0)
        overview = self.api("overview")
        self.assertEqual(overview["model"]["api_key"], "***")

    def test_settings_skills_mcp_and_market_are_changed_through_the_api(self):
        self.api("skills", {"name": "commit-message", "enabled": False})
        policy = json.loads(self.p.policy_path.read_text())
        self.assertEqual(policy["skills"]["disabled"], ["commit-message"])
        self.api("mcp", {"name": "tracker-two", "command": "python3",
                         "args": [str(ROOT / "mcp_servers" / "tracker_server.py"), "--data", str(self.p.root / "data" / "tracker.json")],
                         "allow": ["get_task"], "deny": [], "effects": {"get_task": "read"}})
        servers = {t["server"] for t in self.api("mcp")["tools"]}
        self.assertEqual(servers, {"tracker", "tracker-two"})
        bad = self.api("mcp", {"name": "Bad Name", "command": "x"})
        self.assertEqual(bad["http"], 400)
        self.assertIn("tracker-two", json.loads(self.p.policy_path.read_text())["mcp"]["servers"])
        self.api("market/install", {"source": "study", "name": "workload-summary"})
        market = self.api("market")
        self.assertIn("workload-summary", market["installed"])
        self.api("market/settings", {"source": "study", "key": "auto_update", "value": False})
        self.assertFalse(self.api("market")["sources"]["study"]["settings"]["auto_update"])
        self.assertEqual(self.api("sessions/nope")["http"], 400)


if __name__ == "__main__":
    unittest.main()
