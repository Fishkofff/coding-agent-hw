import json

from common import parse_args, verdict

EXPECTED = {"T1", "T3", "T4", "T6", "T7"}

workspace, answer, fixture = parse_args()
path = workspace / "open-tasks.json"
if not path.exists():
    verdict(False, "open-tasks.json was not created")
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except ValueError as exc:
    verdict(False, f"open-tasks.json is not valid JSON: {exc}")
if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
    verdict(False, "open-tasks.json must be a JSON array of strings", got=data)
got = set(data)
if len(got) != len(data):
    verdict(False, "duplicate ids", got=data)
if got != EXPECTED:
    verdict(False, "wrong set of ids", missing=sorted(EXPECTED - got), extra=sorted(got - EXPECTED))
verdict(True, "ids match")
