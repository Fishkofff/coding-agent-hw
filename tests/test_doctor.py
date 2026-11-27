import unittest

from agent.doctor import diagnose, render
from tests.helpers import FIXED_ORDER, TEST_CMD, Project, act, call, events, say


def findings(project, steps, rule, **start):
    run_id = project.runtime(steps).run("x", mode="auto", plan_mode=False, **start)
    report = diagnose(events(project, run_id))
    return [f for f in report["findings"] if f["rule"] == rule], report, run_id


class DoctorRulesTest(unittest.TestCase):
    def test_repeated_error_problem(self):
        p = Project(self)
        steps = [act(call("read_file", "r1", path="nope.txt")), act(call("read_file", "r2", path="nope.txt")),
                 act(call("read_file", "r3", path="nope.txt")), say("cannot")]
        found, report, run_id = findings(p, steps, "repeated_error")
        self.assertEqual(len(found), 1)
        item = found[0]
        self.assertEqual(item["run_id"], run_id)
        self.assertEqual(item["counts"]["failed_attempts"], 3)
        self.assertEqual(len(item["event_ids"]), 6)
        ids = {e["id"] for e in events(p, run_id)}
        self.assertTrue(set(item["event_ids"]) <= ids)
        self.assertTrue(item["interpretation"] and item["suggestion"])

    def test_repeated_error_normal_case_changes_approach(self):
        p = Project(self)
        steps = [act(call("read_file", "r1", path="nope.txt")), act(call("list_files", "l1", path=".")),
                 act(call("read_file", "r2", path="order.py")), say("ok")]
        self.assertEqual(findings(p, steps, "repeated_error")[0], [])

    def test_unchanged_reread_problem(self):
        p = Project(self)
        steps = [act(call("read_file", "r1", path="order.py")), act(call("read_file", "r2", path="order.py")), say("ok")]
        found = findings(p, steps, "unchanged_reread")[0]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["counts"]["reads"], 2)
        self.assertGreater(found[0]["counts"]["repeated_result_bytes"], 0)

    def test_unchanged_reread_normal_case_reads_after_write(self):
        p = Project(self)
        steps = [act(call("read_file", "r1", path="order.py")),
                 act(call("write_file", "w1", path="order.py", content=FIXED_ORDER)),
                 act(call("read_file", "r2", path="order.py")), say("ok")]
        self.assertEqual(findings(p, steps, "unchanged_reread")[0], [])

    def test_skill_without_actions_problem(self):
        p = Project(self, skills=True)
        steps = [act(call("load_skill", "s1", name="sprint-report")), say("here is a report from memory")]
        found = findings(p, steps, "skill_without_actions")[0]
        self.assertEqual(len(found), 1)
        self.assertIn("sprint-report", found[0]["interpretation"])

    def test_skill_without_actions_normal_case(self):
        p = Project(self, skills=True)
        steps = [act(call("load_skill", "s1", name="sprint-report")), act(call("read_file", "r1", path="order.py")),
                 say("ok")]
        self.assertEqual(findings(p, steps, "skill_without_actions")[0], [])

    def test_finished_without_verification_problem(self):
        p = Project(self)
        steps = [act(call("write_file", "w1", path="order.py", content=FIXED_ORDER)), say("fixed")]
        found = findings(p, steps, "finished_without_verification")[0]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["counts"], {"writes": 1, "checks_after_last_write": 0})

    def test_finished_without_verification_normal_case_runs_tests(self):
        p = Project(self)
        steps = [act(call("write_file", "w1", path="order.py", content=FIXED_ORDER)),
                 act(call("Bash", "b1", command=TEST_CMD, cwd=".")), say("fixed")]
        self.assertEqual(findings(p, steps, "finished_without_verification")[0], [])


class DoctorReportTest(unittest.TestCase):
    def test_costs_frequencies_and_honest_limitations(self):
        p = Project(self, skills=True, mcp={})
        steps = [act(call("load_skill", "s1", name="open-tasks-export")), act(call("mcp__tracker__list_tasks", "m1")),
                 say("done")]
        _found, report, run_id = findings(p, steps, "none")
        cost = report["costs"][0]
        self.assertEqual(cost["run_id"], run_id)
        self.assertEqual(cost["tool_calls_by_category"], {"skill": 1, "mcp": 1})
        self.assertEqual(report["stats"]["skill_loads"], {"open-tasks-export": 1})
        self.assertEqual(report["stats"]["mcp_calls"], {"tracker/list_tasks": 1})
        self.assertEqual(cost["usage"]["status"], "unknown")
        self.assertTrue(any("не оценивает" in item for item in report["limitations"]))
        self.assertEqual(report["doctor_usage"]["model_requests"], 0)
        text = render(report)
        self.assertIn("Расходы по запускам", text)
        self.assertIn("unknown", text)


if __name__ == "__main__":
    unittest.main()
