import argparse
import json
import re
import sys
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--answer", required=True)
    parser.add_argument("--fixture", required=True)
    args = parser.parse_args()
    root = Path(args.root)
    answer_path = Path(args.answer)
    answer = answer_path.read_text(encoding="utf-8") if answer_path.exists() else ""
    return root / "workspace", answer, Path(args.fixture)


def verdict(passed, reason, **details):
    print(json.dumps({"pass": bool(passed), "reason": reason, **details}, ensure_ascii=False))
    sys.exit(0)


def task_ids(text):
    return set(re.findall(r"\bT\d+\b", text))


STATUS_PATTERNS = {
    "open": r"(?<![a-z_])open\b|открыт\w*|новы\w*",
    "in_progress": r"in[_ ]progress|в работе|в процессе|выполняется",
    "closed": r"(?<![a-z_])closed\b|закрыт\w*|выполнен\w*|done",
}


def status_count(text, status):
    for line in text.splitlines():
        low = line.lower()
        if status != "in_progress" and re.search(STATUS_PATTERNS["in_progress"], low):
            continue
        if re.search(STATUS_PATTERNS[status], low):
            numbers = re.findall(r"(?<![\w-])(\d+)(?![\w-])", low)
            if numbers:
                return int(numbers[0])
    return None


def section(text, marker):
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if re.search(marker, line.lower()):
            body = [line]
            for follow in lines[index + 1:]:
                if follow.startswith("#"):
                    break
                body.append(follow)
            return "\n".join(body)
    return None
