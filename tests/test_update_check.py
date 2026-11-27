import json
import shutil
import tempfile
import unittest
from pathlib import Path

from agent.llm import ModelResponse, ScriptedModel
from agent.update_check import compare, verify_candidate
from tests.helpers import ROOT, call
from tests.test_market import market, market_project, use_v2

GOOD = ("# Отчёт по спринту Спринт 12\nВсего задач: 7\n\n## Статусы\n- open: 3\n- in_progress: 2\n- closed: 2\n\n"
        "## Просрочено\n- T1\n- T6\n\n## Неполные данные\n- нет\n")
BAD = GOOD.replace("- open: 3", "- open: 5")


def factory(new_report, new_tokens=100):
    def make(case, version, trial):
        report = GOOD if version == "old" else new_report
        tokens = 100 if version == "old" else new_tokens
        usage = {"prompt_tokens": tokens, "completion_tokens": 10}
        steps = [ModelResponse("", [call("load_skill", "s1", name="sprint-report")], usage=usage),
                 ModelResponse("", [call("read_file", "r1", path="tasks.json")], usage=usage),
                 ModelResponse("", [call("write_file", "w1", path="report.md", content=report)], usage=usage),
                 ModelResponse("done", usage=usage)]
        return ScriptedModel(steps), {"kind": "scripted", "version": version}
    return make


class UpdateCheckTest(unittest.TestCase):
    def setUp(self):
        self.p = market_project(self, verify_updates=True)
        self.m = market(self.p)
        self.m.install("study", "sprint-report")
        use_v2(self.p)
        report = self.m.check(force=True)[0]
        self.assertEqual(report["candidates"][0]["version"], "1.1.0")
        self.assertEqual(self.m.installed()["sprint-report"]["version"], "1.0.0")
        self.out = Path(tempfile.mkdtemp(prefix="agent-update-"))
        self.addCleanup(shutil.rmtree, self.out, True)

    def verify(self, model_factory):
        return verify_candidate(self.m, "sprint-report", ROOT / "evals" / "suite.json", self.out, model_factory,
                                trials=1, only={"c2_sprint_report"}, log=lambda _line: None)

    def test_candidate_that_is_not_worse_is_applied_and_can_be_rolled_back(self):
        report = self.verify(factory(GOOD))
        self.assertEqual(report["decision"], "accepted")
        self.assertEqual(self.m.installed()["sprint-report"]["version"], "1.1.0")
        rows = [json.loads(line) for line in (self.out / "results.jsonl").read_text().splitlines()]
        versions = {r["version"]: r["skill_versions"]["sprint-report"] for r in rows}
        self.assertEqual(versions, {"old": "1.0.0", "new": "1.1.0"})
        self.assertTrue((self.out / "decision.md").exists())
        self.assertIn("market_update_applied", [e["type"] for e in self.m.events()])
        self.m.rollback("sprint-report")
        self.assertEqual(self.m.installed()["sprint-report"]["version"], "1.0.0")

    def test_candidate_with_fewer_passes_is_rejected_and_old_version_stays(self):
        report = self.verify(factory(BAD))
        self.assertEqual(report["decision"], "rejected")
        self.assertIn("успешных попыток стало меньше", report["reason"])
        self.assertEqual(self.m.installed()["sprint-report"]["version"], "1.0.0")
        self.assertTrue((self.m.root / "rejected" / "sprint-report" / "1.1.0" / "SKILL.md").exists())
        self.assertEqual(self.m.candidates(), [])

    def test_token_growth_over_threshold_is_rejected(self):
        report = self.verify(factory(GOOD, new_tokens=200))
        self.assertEqual(report["decision"], "rejected")
        self.assertIn("порог 25%", report["reason"])

    def test_incomplete_usage_cannot_confirm_the_threshold(self):
        rows = [{"case_id": "c", "version": v, "outcome_pass": True, "routing_pass_or_na": True,
                 "usage_status": "unknown", "input_tokens": None, "output_tokens": None} for v in ("old", "new")]
        result = compare(rows, ["c"])
        self.assertFalse(result["accept"])
        self.assertIn("usage неполный", result["problems"][0])


if __name__ == "__main__":
    unittest.main()
