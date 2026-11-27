import json
import unittest

from tests.helpers import Project, act, call, events, say, tool_results, tracker_server


def mcp_events(project, run_id, event_type):
    return [e for e in events(project, run_id) if e["type"] == event_type and e["data"].get("kind") == "mcp"]


class McpConnectionTest(unittest.TestCase):
    def test_tools_come_from_the_server_and_results_go_back_to_the_model(self):
        p = Project(self, mcp={})
        rt = p.runtime([act(call("mcp__tracker__list_tasks", "m1", status="open")), say("done")])
        run_id = rt.run("open tasks", mode="safe", plan_mode=False)
        state = p.state(run_id)
        offered = rt.model.requests[0]["tools"]
        self.assertIn("mcp__tracker__list_tasks", offered)
        self.assertIn("mcp__tracker__get_task", offered)
        self.assertNotIn("mcp__tracker__update_task_status", offered)
        self.assertEqual(state["status"], "completed")
        result = tool_results(state)["m1"]
        self.assertTrue(result["ok"])
        self.assertEqual({t["id"] for t in json.loads(result["content"])}, {"T1", "T4", "T7"})
        connected = [e for e in events(p, run_id) if e["type"] == "mcp_server_connected"][0]["data"]
        self.assertEqual(connected["server_info"]["name"], "study-tracker")
        call_event = mcp_events(p, run_id, "tool_call")[0]["data"]
        self.assertEqual((call_event["server"], call_event["tool"], call_event["arguments"]),
                         ("tracker", "list_tasks", {"status": "open"}))
        result_event = mcp_events(p, run_id, "tool_result")[0]["data"]
        self.assertTrue(result_event["ok"])
        self.assertIn("T1", result_event["result"]["content"])

    def test_denied_tool_is_rejected_by_runtime_and_logged(self):
        p = Project(self, mcp={})
        before = (p.root / "data" / "tracker.json").read_text(encoding="utf-8")
        steps = [act(call("mcp__tracker__update_task_status", "m1", id="T1", status="closed")), say("done")]
        run_id = p.runtime(steps).run("close T1", mode="auto", plan_mode=False)
        result = tool_results(p.state(run_id))["m1"]
        self.assertEqual(result["error"], "policy_denied")
        self.assertIn("denied by the policy", result["message"])
        self.assertEqual((p.root / "data" / "tracker.json").read_text(encoding="utf-8"), before)
        logged = mcp_events(p, run_id, "tool_result")[0]["data"]
        self.assertEqual((logged["status"], logged["error"]), ("rejected", "policy_denied"))

    def test_tool_not_in_allowlist_is_rejected(self):
        p = Project(self, mcp={"allow": ("list_tasks",), "deny": ()})
        run_id = p.runtime([act(call("mcp__tracker__get_task", "m1", id="T1")), say("done")]).run(
            "x", mode="auto", plan_mode=False)
        self.assertIn("not in the allowlist", tool_results(p.state(run_id))["m1"]["message"])

    def test_write_effect_tool_needs_confirmation_in_safe_mode(self):
        p = Project(self, mcp={"allow": ("list_tasks", "update_task_status"), "deny": ()})
        steps = [act(call("mcp__tracker__update_task_status", "m1", id="T1", status="closed")), say("done")]
        run_id = p.runtime(steps).run("close", mode="safe", plan_mode=False)
        self.assertEqual(p.state(run_id)["pending"]["kind"], "confirmation")
        p.runtime(steps).deny(run_id, "do not touch the tracker")
        self.assertEqual(tool_results(p.state(run_id))["m1"]["error"], "denied_by_user")
        data = json.loads((p.root / "data" / "tracker.json").read_text(encoding="utf-8"))
        self.assertEqual(data[0]["status"], "open")

    def test_schema_of_the_server_is_checked_before_the_call(self):
        p = Project(self, mcp={})
        run_id = p.runtime([act(call("mcp__tracker__list_tasks", "m1", status="lost")), say("done")]).run(
            "x", mode="auto", plan_mode=False)
        self.assertEqual(tool_results(p.state(run_id))["m1"]["error"], "invalid_arguments")


class McpFailureTest(unittest.TestCase):
    def test_timeout_is_returned_to_the_model_and_logged(self):
        p = Project(self, mcp={"env": {"TRACKER_FAIL": "timeout"}, "timeout": 1})
        steps = [act(call("mcp__tracker__list_tasks", "m1")), say("tracker is unavailable")]
        run_id = p.runtime(steps).run("x", mode="auto", plan_mode=False)
        state = p.state(run_id)
        self.assertEqual(state["status"], "completed")
        result = tool_results(state)["m1"]
        self.assertEqual(result["error"], "mcp_timeout")
        logged = mcp_events(p, run_id, "tool_result")[0]["data"]
        self.assertEqual((logged["ok"], logged["error"], logged["server"]), (False, "mcp_timeout", "tracker"))

    def test_server_error_is_a_tool_error(self):
        p = Project(self, mcp={"env": {"TRACKER_FAIL": "error"}})
        run_id = p.runtime([act(call("mcp__tracker__list_tasks", "m1")), say("down")]).run(
            "x", mode="auto", plan_mode=False)
        result = tool_results(p.state(run_id))["m1"]
        self.assertEqual(result["error"], "mcp_tool_error")
        self.assertIn("503", result["message"])

    def test_crashed_server_is_reported_and_reconnected_on_next_call(self):
        p = Project(self, mcp={"env": {"TRACKER_FAIL": "crash"}})
        steps = [act(call("mcp__tracker__list_tasks", "m1")), act(call("mcp__tracker__list_tasks", "m2")), say("down")]
        run_id = p.runtime(steps).run("x", mode="auto", plan_mode=False)
        results = tool_results(p.state(run_id))
        self.assertEqual(results["m1"]["error"], "mcp_error")
        self.assertIn("exited", results["m1"]["message"])
        self.assertEqual(results["m2"]["error"], "mcp_error")

    def test_server_that_does_not_start_does_not_break_the_run(self):
        p = Project(self, mcp={"command": "/nonexistent/mcp-server"})
        rt = p.runtime([say("no tracker")])
        run_id = rt.run("x", mode="auto", plan_mode=False)
        self.assertEqual(p.state(run_id)["status"], "completed")
        self.assertFalse(any(name.startswith("mcp__") for name in rt.model.requests[0]["tools"]))
        errors = [e for e in events(p, run_id) if e["type"] == "mcp_server_error"]
        self.assertIn("cannot start", errors[0]["data"]["error"])


class McpConfigurationTest(unittest.TestCase):
    def test_second_compatible_server_is_added_only_through_configuration(self):
        p = Project(self, mcp={})
        p.policy["mcp"]["servers"]["tracker-copy"] = tracker_server(p.root, allow=("get_task",), deny=())
        p.write_policy()
        rt = p.runtime([act(call("mcp__tracker-copy__get_task", "m1", id="T3")), say("done")])
        run_id = rt.run("x", mode="auto", plan_mode=False)
        self.assertIn("mcp__tracker-copy__get_task", rt.model.requests[0]["tools"])
        self.assertTrue(tool_results(p.state(run_id))["m1"]["ok"])
        servers = {e["data"]["server"] for e in events(p, run_id) if e["type"] == "mcp_server_connected"}
        self.assertEqual(servers, {"tracker", "tracker-copy"})

    def test_disabled_server_is_not_connected(self):
        p = Project(self, mcp={})
        p.policy["mcp"]["servers"]["tracker"]["enabled"] = False
        p.write_policy()
        rt = p.runtime([say("ok")])
        rt.run("x", mode="auto", plan_mode=False)
        self.assertFalse(any(name.startswith("mcp__") for name in rt.model.requests[0]["tools"]))


if __name__ == "__main__":
    unittest.main()
