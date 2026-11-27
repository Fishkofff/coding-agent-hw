from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agent.util import sha256_bytes

NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_NAME = 64
MAX_DESCRIPTION = 1024
MAX_SKILL_BYTES = 50_000
ALLOWED_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}


class SkillError(Exception):
    pass


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: str
    body: str
    sha256: str
    meta: dict

    def to_state(self) -> dict:
        return {"name": self.name, "description": self.description, "path": self.path, "body": self.body,
                "sha256": self.sha256, "meta": self.meta}


def parse_frontmatter(text: str) -> tuple[dict, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillError("SKILL.md must start with a '---' frontmatter block")
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end is None:
        raise SkillError("frontmatter is not closed with '---'")
    data = {}
    current = None
    block = []
    style = None
    for line in lines[1:end]:
        if current is not None and (line.startswith(" ") or line.startswith("\t") or not line.strip()):
            block.append(line.strip())
            continue
        if current is not None:
            data[current] = _join_block(block, style)
            current = None
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise SkillError(f"cannot parse frontmatter line: {line!r}")
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip()
        if value in (">", ">-", "|", "|-"):
            current, block, style = key, [], value[0]
            continue
        if value == "":
            current, block, style = key, [], "nested"
            continue
        data[key] = _unquote(value)
    if current is not None:
        data[current] = _join_block(block, style)
    body = "\n".join(lines[end + 1:]).strip() + "\n"
    return data, body


def _join_block(block: list, style: str):
    items = [item for item in block if item]
    if style == "nested":
        result = {}
        for item in items:
            if ":" in item:
                key, value = item.split(":", 1)
                result[key.strip()] = _unquote(value.strip())
        return result
    if style == "|":
        return "\n".join(block).strip()
    return " ".join(items)


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def load_skill_dir(directory: Path) -> Skill:
    path = directory / "SKILL.md"
    if not path.is_file():
        raise SkillError("SKILL.md not found")
    raw = path.read_bytes()
    if len(raw) > MAX_SKILL_BYTES:
        raise SkillError(f"SKILL.md is larger than {MAX_SKILL_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise SkillError("SKILL.md is not valid UTF-8") from None
    meta, body = parse_frontmatter(text)
    unknown = sorted(set(meta) - ALLOWED_KEYS)
    if unknown:
        raise SkillError(f"unknown frontmatter fields: {', '.join(unknown)}")
    name = meta.get("name")
    description = meta.get("description")
    if not isinstance(name, str) or not name:
        raise SkillError("frontmatter field 'name' is required")
    if len(name) > MAX_NAME or not NAME_RE.match(name):
        raise SkillError(f"invalid name {name!r}: use lowercase letters, digits and single hyphens, up to {MAX_NAME} chars")
    if name != directory.name:
        raise SkillError(f"name {name!r} must match the directory name {directory.name!r}")
    if not isinstance(description, str) or not description.strip():
        raise SkillError("frontmatter field 'description' is required")
    if len(description) > MAX_DESCRIPTION:
        raise SkillError(f"description is longer than {MAX_DESCRIPTION} chars")
    if not body.strip():
        raise SkillError("SKILL.md has no instructions after the frontmatter")
    return Skill(name=name, description=description.strip(), path=str(path), body=body,
                 sha256=sha256_bytes(raw), meta={k: v for k, v in meta.items() if k not in ("name", "description")})


def discover(dirs, disabled=()) -> tuple[list[Skill], list[dict]]:
    skills, errors, seen = [], [], {}
    for base in dirs:
        base = Path(base)
        if not base.is_dir():
            errors.append({"path": str(base), "error": "skills directory does not exist"})
            continue
        for directory in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")):
            try:
                skill = load_skill_dir(directory)
            except (SkillError, OSError) as exc:
                errors.append({"path": str(directory / "SKILL.md"), "error": str(exc)})
                continue
            if skill.name in seen:
                errors.append({"path": skill.path, "error": f"duplicate skill name {skill.name!r} (already loaded from {seen[skill.name]})"})
                continue
            if skill.name in disabled:
                continue
            seen[skill.name] = skill.path
            skills.append(skill)
    return skills, errors
