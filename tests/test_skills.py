import unittest

from agent.skills import discover
from tests.helpers import FIXED_ORDER, Project, act, call, events, say, tool_results

SKILL_TEMPLATE = "---\nname: {name}\ndescription: {description}\n---\n\n1. Do the thing.\n"


class SkillDiscoveryTest(unittest.TestCase):
    def test_repo_skills_are_valid(self):
        p = Project(self, skills=True)
        skills, errors = discover([p.root / "skills"])
        self.assertEqual(errors, [])
        self.assertEqual(sorted(s.name for s in skills), ["commit-message", "open-tasks-export", "sprint-report"])
        self.assertTrue(all(s.description and s.body.strip() for s in skills))

    def test_invalid_skill_gives_clear_error_and_others_still_load(self):
        p = Project(self, skills=True)
        broken = {
            "no-frontmatter": "just text\n",
            "no-description": "---\nname: no-description\n---\nsteps\n",
            "Bad_Name": "---\nname: Bad_Name\ndescription: x\n---\nsteps\n",
            "wrong-dir": "---\nname: other-name\ndescription: x\n---\nsteps\n",
            "unclosed": "---\nname: unclosed\ndescription: x\n",
        }
        for name, text in broken.items():
            (p.root / "skills" / name).mkdir()
            (p.root / "skills" / name / "SKILL.md").write_text(text, encoding="utf-8")
        (p.root / "skills" / "empty-dir").mkdir()
        skills, errors = discover([p.root / "skills"])
        self.assertEqual(len(skills), 3)
        reasons = {e["path"].split("/")[-2]: e["error"] for e in errors}
        self.assertIn("must start with a '---'", reasons["no-frontmatter"])
        self.assertIn("'description' is required", reasons["no-description"])
        self.assertIn("invalid name", reasons["Bad_Name"])
        self.assertIn("must match the directory name", reasons["wrong-dir"])
        self.assertIn("not closed", reasons["unclosed"])
        self.assertIn("SKILL.md not found", reasons["empty-dir"])

        run_id = p.runtime([say("ok")]).run("hi", mode="auto", plan_mode=False)
        types = [e["type"] for e in events(p, run_id)]
        self.assertEqual(types.count("skill_invalid"), 6)
        self.assertIn("[skills] ОШИБКА", p.output)
        self.assertEqual(p.state(run_id)["status"], "completed")

    def test_new_skill_folder_is_picked_up_by_the_next_run(self):
        p = Project(self, skills=True)
        first = p.runtime([say("ok")]).run("hi", mode="auto", plan_mode=False)
        (p.root / "skills" / "release-notes").mkdir()
        (p.root / "skills" / "release-notes" / "SKILL.md").write_text(
            SKILL_TEMPLATE.format(name="release-notes", description="Write release notes."), encoding="utf-8")
        second = p.runtime([say("ok")]).run("hi", mode="auto", plan_mode=False)
        self.assertNotIn("release-notes", p.state(first)["messages"][0]["content"])
        self.assertIn("release-notes: Write release notes.", p.state(second)["messages"][0]["content"])


class SkillLoadingTest(unittest.TestCase):
    def test_model_sees_only_catalog_and_loads_the_full_skill(self):
        p = Project(self, skills=True)
        rt = p.runtime([act(call("load_skill", "s1", name="sprint-report")), say("done")])
        run_id = rt.run("report", mode="auto", plan_mode=False)
        state = p.state(run_id)
        system = state["messages"][0]["content"]
        self.assertIn("sprint-report: Готовить отчёт", system)
        self.assertNotIn("Просроченная задача", system)
        first_request = rt.model.requests[0]
        self.assertIn("load_skill", first_request["tools"])
        result = tool_results(state)["s1"]
        self.assertTrue(result["ok"])
        self.assertIn("Просроченная задача", result["instructions"])
        loaded = [e for e in events(p, run_id) if e["type"] == "skill_loaded"]
        self.assertEqual([e["data"]["name"] for e in loaded], ["sprint-report"])

    def test_version_a_has_no_catalog_and_no_loader(self):
        p = Project(self, skills=True)
        rt = p.runtime([act(call("load_skill", "s1", name="sprint-report")), say("done")])
        run_id = rt.start("report", mode="auto", plan_mode=False, skills=False)
        rt.drive()
        state = p.state(run_id)
        self.assertNotIn("Skills:", state["messages"][0]["content"])
        self.assertNotIn("load_skill", rt.model.requests[0]["tools"])
        self.assertEqual(tool_results(state)["s1"]["error"], "unknown_tool")
        types = [e["type"] for e in events(p, run_id)]
        self.assertNotIn("skills_discovered", types)

    def test_unknown_skill_is_an_error_for_the_model(self):
        p = Project(self, skills=True)
        run_id = p.runtime([act(call("load_skill", "s1", name="nope")), say("done")]).run(
            "x", mode="auto", plan_mode=False)
        result = tool_results(p.state(run_id))["s1"]
        self.assertEqual(result["error"], "invalid_arguments")
        self.assertIn("available skills", result["message"])

    def test_skill_does_not_change_runtime_permissions(self):
        p = Project(self, skills=True)
        steps = [act(call("load_skill", "s1", name="open-tasks-export")),
                 act(call("write_file", "w1", path="order.py", content=FIXED_ORDER)), say("done")]
        run_id = p.runtime(steps).run("x", mode="safe", plan_mode=False)
        state = p.state(run_id)
        self.assertEqual(state["status"], "waiting")
        self.assertEqual(state["pending"]["kind"], "confirmation")
        p.runtime(steps).deny(run_id, "no")
        self.assertEqual(tool_results(p.state(run_id))["w1"]["error"], "denied_by_user")

    def test_skill_snapshot_is_kept_for_the_started_run(self):
        p = Project(self, skills=True)
        steps = [act(call("write_file", "w1", path="a.txt", content="x")),
                 act(call("load_skill", "s1", name="sprint-report")), say("done")]
        run_id = p.runtime(steps).run("x", mode="safe", plan_mode=False)
        (p.root / "skills" / "sprint-report" / "SKILL.md").write_text(
            SKILL_TEMPLATE.format(name="sprint-report", description="changed"), encoding="utf-8")
        p.runtime(steps).approve(run_id)
        result = tool_results(p.state(run_id))["s1"]
        self.assertIn("Просроченная задача", result["instructions"])


if __name__ == "__main__":
    unittest.main()
