from __future__ import annotations

import json
import os
import queue
import re
import signal
import subprocess
import threading
import time

from agent.policy import mcp_effect
from agent.util import truncate_text

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "coding-agent-hw2", "version": "2.0.0"}
EXPOSED_RE = re.compile(r"[^A-Za-z0-9_-]")
MAX_EXPOSED = 64


class McpError(Exception):
    pass


class McpTimeout(McpError):
    pass


def exposed_name(server: str, tool: str) -> str:
    return f"mcp__{server}__{EXPOSED_RE.sub('_', tool)}"


def server_env(server: dict) -> dict:
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    env.update(server["env"])
    return env


class McpConnection:
    def __init__(self, server: dict):
        self.server = server
        self.proc = None
        self.lines = queue.Queue()
        self.stderr = bytearray()
        self.next_id = 1
        self.server_info = None
        self.protocol_version = None

    def start(self) -> None:
        try:
            self.proc = subprocess.Popen(
                [self.server["command"], *self.server["args"]], cwd=self.server["cwd"], env=server_env(self.server),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        except OSError as exc:
            raise McpError(f"cannot start MCP server {self.server['name']}: {exc}") from None
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        result = self.request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                             "clientInfo": CLIENT_INFO})
        self.server_info = result.get("serverInfo")
        self.protocol_version = result.get("protocolVersion")
        self.notify("notifications/initialized")

    def _read_stdout(self) -> None:
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def _read_stderr(self) -> None:
        for line in self.proc.stderr:
            if len(self.stderr) < 8000:
                self.stderr += line[:8000 - len(self.stderr)]

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _send(self, message: dict) -> None:
        if not self.alive():
            raise McpError(f"MCP server {self.server['name']} is not running{self._stderr_tail()}")
        try:
            self.proc.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except OSError as exc:
            raise McpError(f"cannot write to MCP server {self.server['name']}: {exc}") from None

    def _stderr_tail(self) -> str:
        text = self.stderr.decode("utf-8", errors="replace").strip()
        return f" (stderr: {text[-500:]})" if text else ""

    def notify(self, method: str, params: dict | None = None) -> None:
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)

    def request(self, method: str, params: dict, timeout: float | None = None) -> dict:
        request_id = self.next_id
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + (timeout or self.server["timeout_sec"])
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise McpTimeout(f"MCP server {self.server['name']} did not answer {method} within "
                                 f"{timeout or self.server['timeout_sec']} s")
            try:
                line = self.lines.get(timeout=left)
            except queue.Empty:
                continue
            if line is None:
                raise McpError(f"MCP server {self.server['name']} exited{self._stderr_tail()}")
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            if "method" in message and "id" in message:
                self._answer_server_request(message)
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"] or {}
                raise McpError(f"MCP error {error.get('code')}: {error.get('message')}")
            return message.get("result") or {}

    def _answer_server_request(self, message: dict) -> None:
        if message["method"] == "ping":
            self._send({"jsonrpc": "2.0", "id": message["id"], "result": {}})
        else:
            self._send({"jsonrpc": "2.0", "id": message["id"],
                        "error": {"code": -32601, "message": f"client does not support {message['method']}"}})

    def list_tools(self) -> list[dict]:
        tools, cursor = [], None
        for _ in range(50):
            result = self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(result.get("tools") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def close(self) -> None:
        if self.proc is None:
            return
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.proc.wait()
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except OSError:
                pass


class McpManager:
    def __init__(self, connection_factory=McpConnection):
        self.factory = connection_factory
        self.connections = {}

    def connect(self, policy) -> tuple[list[dict], list[dict], list[dict]]:
        tools, errors, servers = [], [], []
        for server in policy.mcp_servers:
            if not server["enabled"]:
                continue
            try:
                connection = self._connection(server, fresh=True)
                listed = connection.list_tools()
            except McpError as exc:
                self._drop(server["name"])
                errors.append({"server": server["name"], "error": str(exc)})
                continue
            names = []
            for tool in listed:
                remote = str(tool.get("name") or "")
                exposed = exposed_name(server["name"], remote)
                if not remote or len(exposed) > MAX_EXPOSED:
                    errors.append({"server": server["name"], "error": f"tool name {remote!r} cannot be exposed"})
                    continue
                schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {"type": "object"}
                tools.append({"exposed": exposed, "server": server["name"], "tool": remote,
                              "description": str(tool.get("description") or ""), "input_schema": schema,
                              "offered": remote in server["allow"] and remote not in server["deny"],
                              "effect": mcp_effect(server, remote)})
                names.append(remote)
            servers.append({"server": server["name"], "server_info": connection.server_info,
                            "protocol_version": connection.protocol_version, "tools": names})
        return tools, errors, servers

    def _connection(self, server: dict, fresh: bool = False) -> McpConnection:
        name = server["name"]
        current = self.connections.get(name)
        if current is not None and current.alive() and not fresh:
            return current
        self._drop(name)
        connection = self.factory(server)
        self.connections[name] = connection
        connection.start()
        return connection

    def _drop(self, name: str) -> None:
        connection = self.connections.pop(name, None)
        if connection is not None:
            connection.close()

    def call(self, server: dict, tool: str, arguments: dict, allowance: int) -> dict:
        cap = max(0, min(server["max_result_bytes"], allowance))
        base = {"server": server["name"], "tool": tool}
        try:
            connection = self._connection(server)
            result = connection.call_tool(tool, arguments)
        except McpTimeout as exc:
            self._drop(server["name"])
            return {"ok": False, "error": "mcp_timeout", "message": str(exc), **base}
        except McpError as exc:
            self._drop(server["name"])
            return {"ok": False, "error": "mcp_error", "message": str(exc), **base}
        parts = []
        for item in result.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif isinstance(item, dict):
                parts.append(f"[{item.get('type')} content omitted]")
        text, cut = truncate_text("\n".join(parts), cap)
        if result.get("isError"):
            return {"ok": False, "error": "mcp_tool_error", "message": text, "truncated": cut, **base}
        output = {"ok": True, "content": text, "truncated": cut, **base}
        if isinstance(result.get("structuredContent"), (dict, list)) and not cut:
            structured = json.dumps(result["structuredContent"], ensure_ascii=False)
            if len(structured.encode("utf-8")) + len(text.encode("utf-8")) <= cap:
                output["structured"] = result["structuredContent"]
        return output

    def close(self) -> None:
        for name in list(self.connections):
            self._drop(name)
