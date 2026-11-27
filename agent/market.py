from __future__ import annotations

import datetime as _dt
import json
import re
import shutil
import urllib.parse
import urllib.request
from pathlib import Path

from agent.skills import NAME_RE, SkillError, load_skill_dir
from agent.util import atomic_write_json, new_event, now_iso, sha256_bytes

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
FILE_RE = re.compile(r"^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)?\.(md|txt|json)$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_FILE = 100_000
MAX_INDEX = 1_000_000


class MarketError(Exception):
    pass


def version_key(version: str) -> tuple:
    return tuple(int(part) for part in version.split("."))


def read_location(location: str, base: Path, limit: int) -> bytes:
    if location.startswith(("http://", "https://")):
        try:
            with urllib.request.urlopen(location, timeout=15) as response:
                data = response.read(limit + 1)
        except OSError as exc:
            raise MarketError(f"cannot download {location}: {exc}") from None
    else:
        path = (base / location).resolve()
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise MarketError(f"cannot read {path}: {exc}") from None
    if len(data) > limit:
        raise MarketError(f"{location} is larger than {limit} bytes")
    return data


def join_location(index_location: str, rel: str) -> str:
    if index_location.startswith(("http://", "https://")):
        return urllib.parse.urljoin(index_location, rel)
    return str(Path(index_location).parent / rel)


def validate_entry(entry) -> list[str]:
    if not isinstance(entry, dict):
        return ["entry is not an object"]
    problems = []
    if not isinstance(entry.get("name"), str) or not NAME_RE.match(entry["name"]):
        problems.append(f"bad name {entry.get('name')!r}")
    if not isinstance(entry.get("version"), str) or not VERSION_RE.match(entry["version"]):
        problems.append(f"bad version {entry.get('version')!r}")
    if not isinstance(entry.get("description"), str) or not entry["description"].strip():
        problems.append("description is required")
    path = entry.get("path")
    if not isinstance(path, str) or ".." in path.split("/") or path.startswith("/") or ":" in path:
        problems.append(f"bad path {path!r}")
    if not isinstance(entry.get("sha256"), str) or not SHA_RE.match(entry["sha256"]):
        problems.append("sha256 must be 64 hex chars")
    files = entry.get("files", ["SKILL.md"])
    if not isinstance(files, list) or "SKILL.md" not in files or not all(isinstance(f, str) and FILE_RE.match(f) for f in files):
        problems.append(f"bad files list {files!r}: only .md/.txt/.json files, SKILL.md required")
    return problems


class Market:
    def __init__(self, policy, session_id=None, log=print):
        self.policy = policy
        self.config = policy.market
        self.base = policy.source.parent
        self.root = policy.runs_dir / "market"
        self.install_dir = Path(self.config["install_dir"])
        self.session_id = session_id
        self.log = log

    def event(self, event_type: str, **data) -> dict:
        record = new_event(event_type, self.session_id, None, data)
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.root / "events.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def events(self) -> list[dict]:
        path = self.root / "events.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def state(self) -> dict:
        path = self.root / "state.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {"sources": {}, "settings": {}}

    def save_state(self, state: dict) -> None:
        atomic_write_json(self.root / "state.json", state)

    def source_settings(self, name: str) -> dict:
        source = self.config["sources"][name]
        overrides = self.state()["settings"].get(name, {})
        return {**source, **overrides}

    def set_setting(self, name: str, key: str, value) -> None:
        if name not in self.config["sources"]:
            raise MarketError(f"unknown market source {name!r}")
        state = self.state()
        state["settings"].setdefault(name, {})[key] = value
        self.save_state(state)
        self.event("market_settings_changed", source=name, key=key, value=value)

    def load_index(self, name: str) -> dict:
        source = self.source_settings(name)
        raw = read_location(source["index"], self.base, MAX_INDEX)
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise MarketError(f"index of {name} is not valid JSON: {exc}") from None
        if not isinstance(data, dict) or not isinstance(data.get("skills"), list):
            raise MarketError(f"index of {name} must be an object with a skills list")
        entries, problems = [], []
        for entry in data["skills"]:
            errors = validate_entry(entry)
            if errors:
                problems.append({"entry": entry.get("name") if isinstance(entry, dict) else None, "errors": errors})
            else:
                entries.append(entry)
        revision = str(data.get("revision") or sha256_bytes(raw)[:12])
        return {"revision": revision, "sha256": sha256_bytes(raw), "skills": entries, "problems": problems}

    def installed(self) -> dict:
        result = {}
        if not self.install_dir.is_dir():
            return result
        for directory in sorted(self.install_dir.iterdir()):
            meta = directory / ".install.json"
            if meta.is_file():
                result[directory.name] = json.loads(meta.read_text(encoding="utf-8"))
        return result

    def fetch(self, name: str, entry: dict, destination: Path) -> dict:
        source = self.source_settings(name)
        files = entry.get("files", ["SKILL.md"])
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True)
        package_hash = []
        for rel in files:
            data = read_location(join_location(source["index"], entry["path"].rstrip("/") + "/" + rel), self.base, MAX_FILE)
            if rel == "SKILL.md" and sha256_bytes(data) != entry["sha256"]:
                shutil.rmtree(destination)
                raise MarketError(f"{entry['name']} {entry['version']}: SKILL.md sha256 does not match the catalog")
            target = destination / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            package_hash.append(f"{rel}:{sha256_bytes(data)}")
        try:
            skill = load_skill_dir(destination)
        except SkillError as exc:
            shutil.rmtree(destination)
            raise MarketError(f"{entry['name']} {entry['version']}: invalid package: {exc}") from None
        if skill.name != entry["name"]:
            shutil.rmtree(destination)
            raise MarketError(f"package name {skill.name!r} differs from the catalog entry {entry['name']!r}")
        meta = {"name": entry["name"], "version": entry["version"], "source": name, "index": source["index"],
                "sha256": entry["sha256"], "package_sha256": sha256_bytes("\n".join(package_hash).encode()),
                "fetched_at": now_iso()}
        atomic_write_json(destination / ".install.json", meta)
        return meta

    def find(self, name: str, skill: str) -> dict:
        index = self.load_index(name)
        entry = next((e for e in index["skills"] if e["name"] == skill), None)
        if entry is None:
            raise MarketError(f"skill {skill!r} is not in market {name}")
        return dict(entry, revision=index["revision"])

    def install(self, name: str, skill: str) -> dict:
        entry = self.find(name, skill)
        staging = self.root / "staging" / skill
        try:
            meta = self.fetch(name, entry, staging)
        except MarketError as exc:
            self.event("market_install_failed", source=name, skill=skill, version=entry["version"], error=str(exc))
            raise
        meta["revision"] = entry["revision"]
        atomic_write_json(staging / ".install.json", meta)
        old = self.installed().get(skill)
        self.activate(skill, staging, old)
        self.event("market_installed", source=name, skill=skill, version=entry["version"],
                   old_version=(old or {}).get("version"), revision=entry["revision"], sha256=entry["sha256"])
        return meta

    def activate(self, skill: str, package: Path, old: dict | None) -> None:
        target = self.install_dir / skill
        self.install_dir.mkdir(parents=True, exist_ok=True)
        if target.exists():
            backup = self.root / "history" / skill / (old or {}).get("version", "unknown")
            if backup.exists():
                shutil.rmtree(backup)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(backup))
        shutil.move(str(package), str(target))

    def search(self, query: str) -> list[dict]:
        installed = self.installed()
        words = [w for w in re.split(r"\W+", query.lower()) if len(w) > 2]
        found = []
        for name in self.config["sources"]:
            if not self.source_settings(name).get("check", True):
                continue
            try:
                index = self.load_index(name)
            except MarketError as exc:
                found.append({"source": name, "error": str(exc)})
                continue
            for entry in index["skills"]:
                if entry["name"] in installed:
                    continue
                text = (entry["name"] + " " + entry["description"]).lower()
                score = sum(1 for w in words if w in text)
                if score or not words:
                    found.append({"source": name, "name": entry["name"], "version": entry["version"],
                                  "description": entry["description"], "score": score})
        return sorted(found, key=lambda item: -item.get("score", 0))

    def due(self, name: str, now: _dt.datetime) -> bool:
        settings = self.source_settings(name)
        last = self.state()["sources"].get(name, {}).get("last_check")
        if not last:
            return True
        interval = _dt.timedelta(minutes=float(settings.get("interval_minutes", 60)))
        return now - _dt.datetime.fromisoformat(last) >= interval

    def check(self, force: bool = False, now: _dt.datetime | None = None) -> list[dict]:
        now = now or _dt.datetime.now(_dt.timezone.utc)
        reports = []
        for name in self.config["sources"]:
            settings = self.source_settings(name)
            if not settings.get("check", True):
                reports.append({"source": name, "status": "disabled"})
                continue
            if not force and not self.due(name, now):
                reports.append({"source": name, "status": "not_due"})
                continue
            reports.append(self.check_source(name, settings, now))
        return reports

    def check_source(self, name: str, settings: dict, now: _dt.datetime) -> dict:
        state = self.state()
        known = state["sources"].get(name, {})
        report = {"source": name, "status": "ok", "checked_at": now.isoformat(), "old_revision": known.get("revision"),
                  "new_skills": [], "updates": [], "applied": [], "candidates": [], "errors": []}
        try:
            index = self.load_index(name)
        except MarketError as exc:
            report.update(status="error", errors=[str(exc)])
            known["last_check"] = now.isoformat()
            state["sources"][name] = known
            self.save_state(state)
            self.event("market_check", **report)
            self.log(f"[market] {name}: ошибка проверки, установленные скиллы не тронуты: {exc}")
            return report
        report["new_revision"] = index["revision"]
        report["errors"].extend(f"{p['entry']}: {'; '.join(p['errors'])}" for p in index["problems"])
        installed = self.installed()
        seen = known.get("seen", {})
        for entry in index["skills"]:
            current = installed.get(entry["name"])
            if current is None:
                if entry["name"] not in seen:
                    report["new_skills"].append({"name": entry["name"], "version": entry["version"],
                                                 "description": entry["description"]})
                continue
            if current.get("source") != name:
                continue
            if version_key(entry["version"]) > version_key(current["version"]):
                update = {"name": entry["name"], "old_version": current["version"], "new_version": entry["version"],
                          "old_sha256": current["sha256"], "new_sha256": entry["sha256"]}
                report["updates"].append(update)
                if settings.get("auto_update", False):
                    self.handle_update(name, settings, entry, index, update, report)
        for entry in index["skills"]:
            seen[entry["name"]] = entry["version"]
        known.update(last_check=now.isoformat(), revision=index["revision"], seen=seen)
        state = self.state()
        state["sources"][name] = known
        self.save_state(state)
        self.event("market_check", **report)
        for item in report["new_skills"]:
            self.log(f"[market] Новый скилл в {name}: {item['name']} {item['version']} — {item['description']}")
        for item in report["updates"]:
            self.log(f"[market] Обновление {item['name']}: {item['old_version']} -> {item['new_version']}")
        for item in report["applied"]:
            self.log(f"[market] Установлено обновление {item['name']} {item['version']} (доступно со следующего run)")
        for item in report["candidates"]:
            self.log(f"[market] Кандидат {item['name']} {item['version']} сохранён и ждёт проверки через eval: "
                     f"python3 -m agent market verify {item['name']}")
        for error in report["errors"]:
            self.log(f"[market] {name}: некорректный пакет или запись: {error}")
        return report

    def handle_update(self, name, settings, entry, index, update, report) -> None:
        if settings.get("verify_updates", False):
            candidate = self.candidate_dir(entry["name"], entry["version"])
            if (candidate / ".install.json").exists():
                return
            try:
                meta = self.fetch(name, entry, candidate)
            except MarketError as exc:
                report["errors"].append(str(exc))
                return
            meta["revision"] = index["revision"]
            atomic_write_json(candidate / ".install.json", meta)
            report["candidates"].append({"name": entry["name"], "version": entry["version"], "path": str(candidate)})
            self.event("market_update_candidate", source=name, skill=entry["name"], old_version=update["old_version"],
                       new_version=entry["version"], path=str(candidate))
            return
        staging = self.root / "staging" / entry["name"]
        try:
            meta = self.fetch(name, entry, staging)
        except MarketError as exc:
            report["errors"].append(str(exc))
            return
        meta["revision"] = index["revision"]
        atomic_write_json(staging / ".install.json", meta)
        self.activate(entry["name"], staging, self.installed().get(entry["name"]))
        report["applied"].append({"name": entry["name"], "version": entry["version"]})
        self.event("market_update_applied", source=name, skill=entry["name"], old_version=update["old_version"],
                   new_version=entry["version"], revision=index["revision"])

    def candidate_dir(self, skill: str, version: str) -> Path:
        return self.root / "candidates" / skill / version / skill

    def candidates(self) -> list[dict]:
        base = self.root / "candidates"
        items = []
        if base.is_dir():
            for meta in sorted(base.glob("*/*/*/.install.json")):
                items.append(dict(json.loads(meta.read_text(encoding="utf-8")), path=str(meta.parent)))
        return items

    def apply_candidate(self, skill: str, version: str, reason: str) -> None:
        candidate = self.candidate_dir(skill, version)
        old = self.installed().get(skill)
        self.activate(skill, candidate, old)
        shutil.rmtree(candidate.parent, ignore_errors=True)
        self.event("market_update_applied", skill=skill, old_version=(old or {}).get("version"), new_version=version,
                   reason=reason, verified=True)

    def reject_candidate(self, skill: str, version: str, reason: str) -> None:
        candidate = self.candidate_dir(skill, version)
        target = self.root / "rejected" / skill / version
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(candidate), str(target))
        shutil.rmtree(candidate.parent, ignore_errors=True)
        self.event("market_update_rejected", skill=skill, version=version, reason=reason)

    def rollback(self, skill: str) -> dict:
        current = self.installed().get(skill)
        history = self.root / "history" / skill
        versions = sorted((p for p in history.iterdir() if (p / ".install.json").exists()),
                          key=lambda p: version_key(p.name)) if history.is_dir() else []
        if current is None or not versions:
            raise MarketError(f"no previous version of {skill} to roll back to")
        previous = versions[-1]
        target = self.install_dir / skill
        parked = self.root / "rolled_back" / skill / current["version"]
        if parked.exists():
            shutil.rmtree(parked)
        parked.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(parked))
        shutil.move(str(previous), str(target))
        restored = json.loads((target / ".install.json").read_text(encoding="utf-8"))
        self.event("market_rollback", skill=skill, from_version=current["version"], to_version=restored["version"])
        return {"skill": skill, "from_version": current["version"], "to_version": restored["version"]}
