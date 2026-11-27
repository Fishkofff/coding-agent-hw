import argparse
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from agent.cli import eval_model_factory
from agent.evals import read_rows, run_suite
from tests.helpers import ROOT

SUITE = ROOT / "evals" / "suite.json"
SCRIPTS = ROOT / "tests" / "eval_scripts"


class EvalRunnerTest(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp(prefix="agent-eval-"))
        self.addCleanup(shutil.rmtree, self.out, True)
        self.factory = eval_model_factory(argparse.Namespace(scripted_dir=str(SCRIPTS)))
        self.log = []

    def run_suite(self, **kwargs):
        return run_suite(SUITE, self.out, self.factory, trials=1, log=self.log.append, **kwargs)

    def test_all_cases_both_versions_checks_and_summary(self):
        rows = self.run_suite()
        self.assertEqual(len(rows), 10)
        by_key = {(r["case_id"], r["version"]): r for r in rows}
        self.assertFalse(by_key[("c1_open_tasks_mcp", "A")]["outcome_pass"])
        self.assertTrue(by_key[("c1_open_tasks_mcp", "B")]["outcome_pass"])
        self.assertEqual(by_key[("c1_open_tasks_mcp", "A")]["routing_pass_or_na"], "n/a")
        self.assertTrue(by_key[("c1_open_tasks_mcp", "B")]["routing_pass_or_na"])
        self.assertFalse(by_key[("c2_sprint_report", "B")]["routing_pass_or_na"])
        self.assertTrue(by_key[("c3_fix_order_no_skill", "A")]["outcome_pass"])
        self.assertFalse(by_key[("c5_mcp_failure", "A")]["outcome_pass"])
        self.assertTrue(by_key[("c5_mcp_failure", "B")]["outcome_pass"])
        self.assertEqual(by_key[("c5_mcp_failure", "B")]["mcp_calls"], 1)
        for row in rows:
            self.assertTrue((self.out / row["events"]).exists())
            self.assertEqual(set(row) >= {"case_id", "version", "trial", "run_id", "outcome_pass", "routing_pass_or_na",
                                          "input_tokens", "output_tokens", "skill_activations", "mcp_calls", "error"},
                             True)
        summary = (self.out / "summary.md").read_text(encoding="utf-8")
        self.assertIn("| c1_open_tasks_mcp | B | 1/1 |", summary)
        self.assertIn("[events](trials/c1_open_tasks_mcp/A1/runs/", summary)

    def test_resume_does_not_duplicate_and_redoes_unfinished_trial(self):
        self.run_suite(only={"c1_open_tasks_mcp"})
        first = read_rows(self.out)
        unfinished = self.out / "trials" / "c2_sprint_report" / "A1"
        unfinished.mkdir(parents=True)
        (unfinished / "garbage.txt").write_text("left after a crash")
        rows = self.run_suite(only={"c1_open_tasks_mcp", "c2_sprint_report"})
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[:2], first)
        self.assertFalse((unfinished / "garbage.txt").exists())
        self.assertTrue(any("already saved: 2" in line for line in self.log))

    def test_agent_cannot_see_checker_or_expected_data(self):
        self.run_suite(only={"c2_sprint_report"})
        trial = self.out / "trials" / "c2_sprint_report" / "B1"
        policy = json.loads((trial / "policy.json").read_text(encoding="utf-8"))
        self.assertEqual(policy["directories"]["allow"], ["workspace"])
        self.assertFalse((trial / "workspace" / "expected.json").exists())
        self.assertFalse(any(p.name.startswith("check") for p in (trial / "workspace").iterdir()))
        a_policy = json.loads((self.out / "trials" / "c2_sprint_report" / "A1" / "policy.json").read_text(encoding="utf-8"))
        self.assertEqual(a_policy["skills"]["dirs"], [])
        same = json.dumps(a_policy["mcp"]).replace("/A1", "/X")
        self.assertEqual(same, json.dumps(policy["mcp"]).replace("/B1", "/X"))


if __name__ == "__main__":
    unittest.main()
