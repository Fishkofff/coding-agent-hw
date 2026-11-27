from __future__ import annotations

import io
import json
import threading
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent.doctor import diagnose
from agent.market import Market, MarketError
from agent.policy import PolicyEngine, PolicyError
from agent.report import Reporter
from agent.runtime import AgentRuntime, UserError
from agent.skills import discover
from agent.store import RunNotFound
from agent.stats import compute
from agent.util import atomic_write_json

STATIC = Path(__file__).resolve().parent / "ui_static"
SECRET_KEYS = ("api_key", "OPENAI_API_KEY", "authorization")


class Job:
    def __init__(self, name: str):
        self.name = name
        self.lines = []
        self.status = "running"
        self.result = None

    def log(self, line: str) -> None:
        self.lines.append(str(line))

    def to_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "lines": self.lines[-200:], "result": self.result}


class Controller:
    def __init__(self, policy_path, model_factory):
        self.policy_path = str(policy_path)
        self.model_factory = model_factory
        self.lock = threading.Lock()
        self.busy = {}
        self.outputs = {}
        self.jobs = {}
        self.runtimes = {}

    def engine(self) -> PolicyEngine:
        return PolicyEngine(self.policy_path)

    def policy(self):
        return self.engine().load()

    def runtime(self, session_id: str) -> AgentRuntime:
        if session_id not in self.runtimes:
            model, info = self.model_factory()
            stream = io.StringIO()
            self.outputs[session_id] = stream
            runtime = AgentRuntime(self.policy_path, model, reporter=Reporter(stream, hints=False))
            runtime.model_info = info
            self.runtimes[session_id] = runtime
        return self.runtimes[session_id]

    def store(self):
        return AgentRuntime(self.policy_path).store

    def overview(self) -> dict:
        store = self.store()
        _model, info = self.model_factory()
        return {"sessions": [{"session_id": s["session_id"], "created_at": s["created_at"], "runs": len(s["runs"]),
                              "title": self.session_title(s)} for s in reversed(store.list_sessions())],
                "model": info, "policy": self.policy_path}

    def session_title(self, session: dict) -> str:
        for message in session.get("history") or []:
            if message.get("role") == "user":
                return message["content"].split("\n")[0].replace("Task from the user:", "").strip()[:60]
        return "(новая сессия)"

    def new_session(self, skills: bool) -> dict:
        session = self.store().new_session()
        session["skills_enabled"] = skills
        self.store().save_session(session)
        return session

    def session(self, session_id: str) -> dict:
        store = self.store()
        session = store.load_session(session_id)
        runs = []
        for run_id in session["runs"]:
            state = store.load(run_id)
            runs.append({"run_id": run_id, "status": state["status"], "task": state["task"],
                         "final_answer": state.get("final_answer"), "error": state.get("error")})
        pending = None
        runtime = self.runtimes.get(session_id)
        if runtime is not None and runtime.state and runtime.state.get("status") == "waiting":
            pending = {"run_id": runtime.state["run_id"], "kind": runtime.state["pending"]["kind"],
                       "details": runtime.pending_details()}
        return {"session": {k: v for k, v in session.items() if k != "history"}, "runs": runs, "pending": pending,
                "busy": self.busy.get(session_id, False),
                "output": self.outputs.get(session_id, io.StringIO()).getvalue()[-20000:]}

    def events(self, session_id: str, after: int) -> dict:
        items = self.store().session_events(session_id)
        return {"total": len(items), "events": items[after:]}

    def background(self, session_id: str, work) -> None:
        if self.busy.get(session_id):
            raise UserError("агент ещё работает в этой сессии")
        self.busy[session_id] = True

        def target():
            try:
                work()
            except Exception:
                self.outputs.setdefault(session_id, io.StringIO()).write(traceback.format_exc())
            finally:
                self.busy[session_id] = False
        threading.Thread(target=target, daemon=True).start()

    def send(self, session_id: str, text: str, mode: str) -> None:
        store = self.store()
        session = store.load_session(session_id)
        runtime = self.runtime(session_id)

        def work():
            run_id = runtime.start(text, mode=mode, plan_mode=False, model_info=runtime.model_info,
                                   session_id=session_id, history=session["history"],
                                   skills=session.get("skills_enabled", True))
            session["runs"].append(run_id)
            store.save_session(session)
            runtime.drive()
            self.finish(session_id, text)
        self.background(session_id, work)

    def finish(self, session_id: str, text: str) -> None:
        from agent.cli import chat_history
        runtime = self.runtimes[session_id]
        if runtime.state["status"] == "waiting":
            return
        store = self.store()
        session = store.load_session(session_id)
        session["history"] = chat_history(session, runtime, runtime.state["task"])
        store.save_session(session)

    def decide(self, session_id: str, approve: bool, reason: str | None) -> None:
        runtime = self.runtimes.get(session_id)
        if runtime is None or runtime.state.get("status") != "waiting":
            raise UserError("в этой сессии нет ожидающего действия")
        run_id = runtime.state["run_id"]
        kind = runtime.state["pending"]["kind"]

        def work():
            if kind == "confirmation":
                runtime.approve(run_id) if approve else runtime.deny(run_id, reason)
            elif kind == "plan_approval":
                runtime.approve_plan(run_id) if approve else runtime.reject_plan(run_id, reason)
            self.finish(session_id, runtime.state["task"])
        self.background(session_id, work)

    def stats(self, session_id: str) -> dict:
        return compute(self.store().session_events(session_id))

    def doctor(self, session_id: str) -> dict:
        report = diagnose(self.store().session_events(session_id))
        atomic_write_json(self.store().session_dir(session_id) / "doctor.json", report)
        return report

    def read_policy_file(self) -> dict:
        return json.loads(Path(self.policy_path).read_text(encoding="utf-8"))

    def write_policy_file(self, data: dict) -> None:
        path = Path(self.policy_path)
        backup = path.read_text(encoding="utf-8")
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            self.engine().load()
        except PolicyError:
            path.write_text(backup, encoding="utf-8")
            raise

    def skills(self) -> dict:
        policy = self.policy()
        found, errors = discover(policy.skills_dirs)
        return {"skills": [{"name": s.name, "description": s.description, "path": s.path,
                            "enabled": s.name not in policy.skills_disabled} for s in found], "errors": errors}

    def toggle_skill(self, name: str, enabled: bool) -> None:
        data = self.read_policy_file()
        disabled = set(data.setdefault("skills", {}).get("disabled", []))
        disabled.discard(name) if enabled else disabled.add(name)
        data["skills"]["disabled"] = sorted(disabled)
        self.write_policy_file(data)

    def mcp(self) -> dict:
        from agent.mcp import McpManager
        policy = self.policy()
        manager = McpManager()
        try:
            tools, errors, servers = manager.connect(policy)
        finally:
            manager.close()
        configured = []
        for server in policy.mcp_servers:
            configured.append({"name": server["name"], "enabled": server["enabled"], "command": server["command"],
                               "args": server["args"], "allow": list(server["allow"]), "deny": list(server["deny"]),
                               "effects": server["effects"], "timeout_sec": server["timeout_sec"]})
        return {"servers": configured, "connected": servers, "tools": tools, "errors": errors}

    def save_mcp(self, body: dict) -> None:
        name = body["name"].strip()
        data = self.read_policy_file()
        servers = data.setdefault("mcp", {}).setdefault("servers", {})
        if body.get("remove"):
            servers.pop(name, None)
        else:
            servers[name] = {
                "enabled": bool(body.get("enabled", True)), "command": body["command"],
                "args": [a for a in body.get("args", []) if a], "cwd": body.get("cwd") or ".",
                "timeout_sec": float(body.get("timeout_sec") or 15),
                "tools": {"allow": [t for t in body.get("allow", []) if t], "deny": [t for t in body.get("deny", []) if t]},
                "effects": {k: v for k, v in (body.get("effects") or {}).items() if k},
            }
        self.write_policy_file(data)

    def market(self, session_id=None, log=None) -> Market:
        policy = self.policy()
        if not policy.market:
            raise UserError("market is not configured in the policy")
        return Market(policy, session_id, log=log or (lambda _line: None))

    def market_overview(self) -> dict:
        market = self.market()
        sources = {}
        state = market.state()["sources"]
        for name in market.config["sources"]:
            sources[name] = {"settings": market.source_settings(name), "state": state.get(name, {})}
        try:
            available = market.search("")
        except MarketError as exc:
            available = [{"error": str(exc)}]
        return {"installed": market.installed(), "sources": sources, "candidates": market.candidates(),
                "available": available, "events": market.events()[-30:], "updates": self.update_reports(market)}

    def update_reports(self, market) -> list:
        reports = []
        for base in (market.root / "updates", Path("evals/results")):
            if base.is_dir():
                for path in sorted(base.glob("*/decision.json")):
                    reports.append(json.loads(path.read_text(encoding="utf-8")))
        return reports

    def job(self, name: str, work) -> str:
        job = Job(name)
        job_id = f"job-{len(self.jobs) + 1}"
        self.jobs[job_id] = job

        def target():
            try:
                job.result = work(job.log)
                job.status = "done"
            except Exception as exc:
                job.log(traceback.format_exc())
                job.status = f"failed: {exc}"
        threading.Thread(target=target, daemon=True).start()
        return job_id

    def eval_factory(self):
        def factory(case, version, trial):
            return self.model_factory()
        return factory

    def run_eval(self, suite: str, out: str, only, trials) -> str:
        from agent.evals import run_suite
        return self.job(f"eval {out}", lambda log: len(run_suite(suite, out, self.eval_factory(), only=only,
                                                                trials=trials, log=log)))

    def verify_update(self, skill: str, trials, only) -> str:
        from agent.update_check import verify_candidate
        def work(log):
            report = verify_candidate(self.market(log=log), skill, "evals/suite.json", None, self.eval_factory(),
                                      trials=trials, only=only, log=log)
            return {"decision": report["decision"], "reason": report["reason"]}
        return self.job(f"verify {skill}", work)

    def evals(self) -> list:
        from agent.evals import read_rows
        result = []
        for base in (Path("evals/results"), self.market().root / "updates" if self.policy().market else None):
            if base is None or not base.is_dir():
                continue
            for out in sorted(p for p in base.iterdir() if (p / "results.jsonl").exists()):
                rows = read_rows(out)
                versions = sorted({r["version"] for r in rows})
                result.append({"path": str(out), "rows": len(rows),
                               "by_version": {v: f"{sum(r['outcome_pass'] for r in rows if r['version'] == v)}/"
                                                 f"{sum(1 for r in rows if r['version'] == v)}" for v in versions}})
        return result

    def eval_detail(self, path: str) -> dict:
        from agent.evals import read_rows
        out = Path(path)
        summary = out / "summary.md"
        decision = out / "decision.md"
        return {"rows": read_rows(out), "summary": summary.read_text(encoding="utf-8") if summary.exists() else "",
                "decision": decision.read_text(encoding="utf-8") if decision.exists() else ""}


def scrub(value):
    if isinstance(value, dict):
        return {k: ("***" if any(s.lower() in k.lower() for s in SECRET_KEYS) else scrub(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def make_handler(controller: Controller):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def reply(self, status: int, payload, content_type="application/json; charset=utf-8"):
            body = payload if isinstance(payload, bytes) else json.dumps(scrub(payload), ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"{}") if length else {}

        def do_GET(self):
            self.dispatch("GET")

        def do_POST(self):
            self.dispatch("POST")

        def dispatch(self, method: str):
            parsed = urllib.parse.urlparse(self.path)
            parts = [p for p in parsed.path.split("/") if p]
            query = dict(urllib.parse.parse_qsl(parsed.query))
            try:
                if method == "GET" and not parts:
                    return self.reply(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
                if parts[:1] != ["api"]:
                    return self.reply(404, {"error": "not found"})
                result = self.route(method, parts[1:], query)
                self.reply(200, result if result is not None else {"ok": True})
            except (UserError, MarketError, PolicyError, RunNotFound, KeyError, ValueError, FileNotFoundError) as exc:
                self.reply(400, {"error": str(exc)})
            except Exception as exc:
                self.reply(500, {"error": f"{type(exc).__name__}: {exc}"})

        def route(self, method, parts, query):
            c = controller
            if method == "GET" and parts == ["overview"]:
                return c.overview()
            if method == "POST" and parts == ["sessions"]:
                return c.new_session(bool(self.body().get("skills", True)))
            if len(parts) >= 2 and parts[0] == "sessions":
                session_id = parts[1]
                tail = parts[2:]
                if method == "GET" and not tail:
                    return c.session(session_id)
                if method == "GET" and tail == ["events"]:
                    return c.events(session_id, int(query.get("after", 0)))
                if method == "GET" and tail == ["stats"]:
                    return c.stats(session_id)
                if method == "GET" and tail == ["doctor"]:
                    return c.doctor(session_id)
                if method == "POST" and tail == ["message"]:
                    body = self.body()
                    return c.send(session_id, body["text"], body.get("mode", "safe"))
                if method == "POST" and tail == ["decision"]:
                    body = self.body()
                    return c.decide(session_id, bool(body.get("approve")), body.get("reason"))
            if parts == ["skills"]:
                if method == "GET":
                    return c.skills()
                body = self.body()
                return c.toggle_skill(body["name"], bool(body["enabled"]))
            if parts == ["mcp"]:
                return c.mcp() if method == "GET" else c.save_mcp(self.body())
            if parts == ["market"] and method == "GET":
                return c.market_overview()
            if parts[:1] == ["market"] and method == "POST":
                body = self.body()
                market = c.market(log=lambda _line: None)
                action = parts[1]
                if action == "check":
                    return market.check(force=bool(body.get("force", True)))
                if action == "install":
                    return market.install(body["source"], body["name"])
                if action == "settings":
                    return market.set_setting(body["source"], body["key"], bool(body["value"]))
                if action == "rollback":
                    return market.rollback(body["skill"])
                if action == "verify":
                    only = set(body["only"]) if body.get("only") else None
                    return {"job": c.verify_update(body["skill"], body.get("trials"), only)}
            if parts == ["evals"]:
                if method == "GET":
                    return c.evals()
                body = self.body()
                only = set(body["only"]) if body.get("only") else None
                return {"job": c.run_eval(body.get("suite", "evals/suite.json"), body["out"], only, body.get("trials"))}
            if parts == ["evals", "detail"]:
                return c.eval_detail(query["path"])
            if parts == ["jobs"]:
                return {job_id: job.to_dict() for job_id, job in c.jobs.items()}
            raise KeyError(f"unknown endpoint {method} /api/{'/'.join(parts)}")
    return Handler


def serve(policy_path, model_factory, host="127.0.0.1", port=8765):
    controller = Controller(policy_path, model_factory)
    server = ThreadingHTTPServer((host, port), make_handler(controller))
    print(f"UI: http://{host}:{port}  (policy {policy_path}; Ctrl+C — остановить)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for runtime in controller.runtimes.values():
            runtime.close()
        server.server_close()
    return controller
