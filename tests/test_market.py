import datetime as dt
import json
import shutil
import unittest

from agent.market import Market, MarketError
from agent.policy import load_policy
from agent.skills import discover
from tests.helpers import ROOT, Project, act, call, say, tool_results


def market_project(testcase, **source):
    p = Project(testcase, skills=True)
    shutil.copytree(ROOT / "market", p.root / "market")
    shutil.rmtree(p.root / "skills" / "sprint-report", ignore_errors=True)
    p.policy["market"] = {"install_dir": "skills_installed", "agent_install": True, "sources": {"study": {
        "index": "market/index.json", "check": True, "auto_update": True, "verify_updates": False,
        "interval_minutes": 60, **source}}}
    p.write_policy()
    shutil.copy(p.root / "market" / "index-v1.json", p.root / "market" / "index.json")
    return p


def market(p, log=None):
    return Market(load_policy(p.policy_path), log=(log.append if log is not None else lambda _line: None))


def use_v2(p):
    shutil.copy(p.root / "market" / "index-v2.json", p.root / "market" / "index.json")


def types(m):
    return [e["type"] for e in m.events()]


class MarketInstallTest(unittest.TestCase):
    def test_install_records_source_version_hash_and_is_visible_to_the_loader(self):
        p = market_project(self)
        m = market(p)
        meta = m.install("study", "sprint-report")
        self.assertEqual((meta["source"], meta["version"]), ("study", "1.0.0"))
        installed = json.loads((p.root / "skills_installed" / "sprint-report" / ".install.json").read_text())
        self.assertEqual(installed["revision"], "v1")
        self.assertEqual(len(installed["sha256"]), 64)
        skills, errors = discover(load_policy(p.policy_path).skills_dirs)
        self.assertIn("sprint-report", [s.name for s in skills])
        run_id = p.runtime([say("ok")]).run("x", mode="auto", plan_mode=False)
        self.assertIn("sprint-report:", p.state(run_id)["messages"][0]["content"])

    def test_hash_mismatch_and_invalid_package_are_rejected(self):
        p = market_project(self)
        use_v2(p)
        m = market(p)
        with self.assertRaisesRegex(MarketError, "sha256 does not match"):
            m.install("study", "broken-example")
        self.assertFalse((p.root / "skills_installed" / "broken-example").exists())
        package = p.root / "market" / "packages" / "standup-notes" / "1.0.0" / "SKILL.md"
        package.write_text("no frontmatter", encoding="utf-8")
        index = json.loads((p.root / "market" / "index.json").read_text())
        import hashlib
        for entry in index["skills"]:
            if entry["name"] == "standup-notes":
                entry["sha256"] = hashlib.sha256(b"no frontmatter").hexdigest()
        (p.root / "market" / "index.json").write_text(json.dumps(index))
        with self.assertRaisesRegex(MarketError, "invalid package"):
            m.install("study", "standup-notes")
        self.assertIn("market_install_failed", types(m))


class MarketCheckTest(unittest.TestCase):
    def test_new_skill_is_reported_once_and_same_version_is_not_reinstalled(self):
        p = market_project(self)
        log = []
        m = market(p, log)
        m.install("study", "sprint-report")
        first = m.check(force=True)[0]
        self.assertEqual([s["name"] for s in first["new_skills"]], ["workload-summary"])
        self.assertTrue(any("Новый скилл" in line for line in log))
        mtime = (p.root / "skills_installed" / "sprint-report" / "SKILL.md").stat().st_mtime_ns
        second = m.check(force=True)[0]
        self.assertEqual((second["new_skills"], second["updates"], second["applied"]), ([], [], []))
        self.assertEqual(types(m).count("market_installed"), 1)
        self.assertEqual(mtime, (p.root / "skills_installed" / "sprint-report" / "SKILL.md").stat().st_mtime_ns)

    def test_v2_update_is_applied_new_skill_reported_and_bad_package_logged(self):
        p = market_project(self)
        m = market(p)
        m.install("study", "sprint-report")
        m.check(force=True)
        use_v2(p)
        report = m.check(force=True)[0]
        self.assertEqual((report["old_revision"], report["new_revision"]), ("v1", "v2"))
        self.assertEqual(report["updates"][0]["new_version"], "1.1.0")
        self.assertEqual(report["applied"], [{"name": "sprint-report", "version": "1.1.0"}])
        self.assertEqual([s["name"] for s in report["new_skills"]], ["standup-notes", "broken-example"])
        self.assertEqual(m.installed()["sprint-report"]["version"], "1.1.0")
        self.assertTrue((m.root / "history" / "sprint-report" / "1.0.0" / "SKILL.md").exists())
        check = [e for e in m.events() if e["type"] == "market_check"][-1]
        self.assertIn("time", check)
        self.assertEqual(check["data"]["old_revision"], "v1")

    def test_started_run_keeps_its_skill_version(self):
        p = market_project(self)
        m = market(p)
        m.install("study", "sprint-report")
        steps = [act(call("write_file", "w1", path="a.txt", content="x")),
                 act(call("load_skill", "s1", name="sprint-report")), say("done")]
        run_id = p.runtime(steps).run("report", mode="safe", plan_mode=False)
        use_v2(p)
        m.check(force=True)
        p.runtime(steps).approve(run_id)
        body = tool_results(p.state(run_id))["s1"]["instructions"]
        self.assertNotIn("Повторно эти файлы не читай", body)
        next_run = p.runtime([act(call("load_skill", "s1", name="sprint-report")), say("ok")]).run(
            "report", mode="auto", plan_mode=False)
        self.assertIn("Повторно эти файлы не читай", tool_results(p.state(next_run))["s1"]["instructions"])

    def test_disabled_auto_update_only_reports_and_disabled_check_does_nothing(self):
        p = market_project(self, auto_update=False)
        m = market(p)
        m.install("study", "sprint-report")
        use_v2(p)
        report = m.check(force=True)[0]
        self.assertEqual(report["updates"][0]["new_version"], "1.1.0")
        self.assertEqual(report["applied"], [])
        self.assertEqual(m.installed()["sprint-report"]["version"], "1.0.0")
        m.set_setting("study", "check", False)
        self.assertEqual(m.check(force=True)[0]["status"], "disabled")

    def test_unavailable_market_keeps_installed_copy(self):
        p = market_project(self)
        m = market(p)
        m.install("study", "sprint-report")
        (p.root / "market" / "index.json").unlink()
        report = m.check(force=True)[0]
        self.assertEqual(report["status"], "error")
        self.assertEqual(m.installed()["sprint-report"]["version"], "1.0.0")
        (p.root / "market" / "index.json").write_text("{not json")
        self.assertEqual(m.check(force=True)[0]["status"], "error")
        self.assertTrue((p.root / "skills_installed" / "sprint-report" / "SKILL.md").exists())

    def test_interval_triggers_automatic_check(self):
        p = market_project(self)
        m = market(p)
        now = dt.datetime(2026, 11, 27, 12, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(m.check(now=now)[0]["status"], "ok")
        self.assertEqual(m.check(now=now + dt.timedelta(minutes=10))[0]["status"], "not_due")
        self.assertEqual(m.check(now=now + dt.timedelta(minutes=61))[0]["status"], "ok")

    def test_rollback_restores_previous_version(self):
        p = market_project(self)
        m = market(p)
        m.install("study", "sprint-report")
        use_v2(p)
        m.check(force=True)
        result = m.rollback("sprint-report")
        self.assertEqual((result["from_version"], result["to_version"]), ("1.1.0", "1.0.0"))
        self.assertEqual(m.installed()["sprint-report"]["version"], "1.0.0")


class AgentMarketToolsTest(unittest.TestCase):
    def test_agent_searches_installs_and_loads_a_new_skill(self):
        p = market_project(self)
        steps = [act(call("market_search", "q1", query="нагрузка исполнителей")),
                 act(call("market_install", "i1", source="study", name="workload-summary")),
                 act(call("load_skill", "s1", name="workload-summary")), say("ok")]
        run_id = p.runtime(steps).run("у кого сколько задач?", mode="auto", plan_mode=False)
        results = tool_results(p.state(run_id))
        self.assertEqual(results["q1"]["results"][0]["name"], "workload-summary")
        self.assertTrue(results["i1"]["ok"])
        self.assertTrue(results["s1"]["ok"])
        self.assertTrue((p.root / "skills_installed" / "workload-summary" / ".install.json").exists())

    def test_install_needs_confirmation_in_safe_mode_and_can_be_disabled(self):
        p = market_project(self)
        steps = [act(call("market_install", "i1", source="study", name="workload-summary")), say("ok")]
        run_id = p.runtime(steps).run("x", mode="safe", plan_mode=False)
        self.assertEqual(p.state(run_id)["pending"]["kind"], "confirmation")
        p.runtime(steps).deny(run_id, "no")
        self.assertFalse((p.root / "skills_installed" / "workload-summary").exists())
        p.policy["market"]["agent_install"] = False
        p.write_policy()
        rt = p.runtime([say("ok")])
        rt.run("x", mode="auto", plan_mode=False)
        self.assertNotIn("market_install", rt.model.requests[0]["tools"])


if __name__ == "__main__":
    unittest.main()
