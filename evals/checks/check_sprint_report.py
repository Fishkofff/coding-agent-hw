import json

from common import parse_args, section, status_count, task_ids, verdict

workspace, answer, fixture = parse_args()
config = json.loads((fixture / "expected.json").read_text(encoding="utf-8"))
path = workspace / "report.md"
if not path.exists():
    verdict(False, "report.md was not created")
text = path.read_text(encoding="utf-8")
problems = []
for status, expected in config["counts"].items():
    got = status_count(text, status)
    if got != expected:
        problems.append(f"{status}: expected {expected}, got {got}")
overdue = section(text, r"просроч|overdue")
if overdue is None:
    problems.append("no overdue section")
else:
    ids = task_ids(overdue)
    missing = set(config["overdue"]) - ids
    wrong = ids & set(config["not_overdue"])
    if missing:
        problems.append(f"overdue tasks missing: {sorted(missing)}")
    if wrong:
        problems.append(f"tasks wrongly listed as overdue: {sorted(wrong)}")
for task_id in config.get("must_mention", []):
    if task_id not in task_ids(text):
        problems.append(f"{task_id} (incomplete data) is not mentioned")
if problems:
    verdict(False, "; ".join(problems))
verdict(True, "counts, overdue tasks and incomplete data are correct")
