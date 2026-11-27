from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class ModelError(Exception):
    pass


@dataclass
class ToolCallRequest:
    id: str
    name: str
    arguments: str


@dataclass
class ModelResponse:
    content: str = ""
    tool_calls: list = field(default_factory=list)
    usage: dict | None = None
    recovered_from_text: bool = False


class OpenAICompatibleModel:
    def __init__(self, base_url: str, api_key: str, model: str, *, timeout: float = 300.0,
                 temperature: float = 0.0, max_retries: int = 2, no_think: bool = False):
        self.base_url, self.api_key, self.model = base_url, api_key or "none", model
        self.timeout, self.temperature, self.max_retries = timeout, temperature, max_retries
        self.no_think = no_think

    def complete(self, messages: list[dict], tools: list[dict]) -> ModelResponse:
        if self.no_think and messages and messages[0].get("role") == "system":
            messages = [dict(messages[0], content=messages[0]["content"] + "\n/no_think")] + messages[1:]
        body = {"model": self.model, "messages": messages, "temperature": self.temperature}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        url = self.base_url.rstrip("/") + "/chat/completions"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        last_error = ModelError("no attempts were made")
        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(url, data=data, method="POST", headers={
                "Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                return recover_text_tool_call(self.parse(payload), tools)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                last_error = ModelError(f"HTTP {exc.code} from {url}: {detail}")
                if exc.code not in (408, 409, 429, 500, 502, 503, 504):
                    raise last_error from None
            except (urllib.error.URLError, OSError) as exc:
                last_error = ModelError(f"cannot reach {url}: {exc}")
            except json.JSONDecodeError as exc:
                raise ModelError(f"model API returned invalid JSON: {exc}") from None
            if attempt < self.max_retries:
                time.sleep(min(2 ** attempt, 8))
        raise last_error

    @staticmethod
    def parse(payload: dict) -> ModelResponse:
        try:
            message = payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise ModelError(f"unexpected model response: {str(payload)[:300]}") from None
        content = message.get("content") or ""
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        content = THINK_RE.sub("", content).strip()
        calls = []
        for raw in message.get("tool_calls") or []:
            function = raw.get("function") or {}
            arguments = function.get("arguments")
            if isinstance(arguments, (dict, list)):
                arguments = json.dumps(arguments, ensure_ascii=False)
            calls.append(ToolCallRequest(id=str(raw.get("id") or ""), name=str(function.get("name") or ""),
                                         arguments=arguments if isinstance(arguments, str) else "{}"))
        return ModelResponse(content=content, tool_calls=calls, usage=payload.get("usage"))


TOOL_TAG_RE = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.DOTALL)
FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def recover_text_tool_call(response: ModelResponse, tools: list[dict]) -> ModelResponse:
    if response.tool_calls or not response.content:
        return response
    names = {t["function"]["name"] for t in tools or []}
    text = response.content.strip()
    tagged = TOOL_TAG_RE.findall(text)
    candidates = tagged or [FENCE_RE.sub(r"\1", text)]
    calls = []
    for index, candidate in enumerate(candidates):
        try:
            data = json.loads(candidate)
        except ValueError:
            return response
        if not isinstance(data, dict) or data.get("name") not in names:
            return response
        arguments = data.get("arguments", data.get("parameters", {}))
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                return response
        if not isinstance(arguments, dict):
            return response
        calls.append(ToolCallRequest(id=f"text-{index}-{abs(hash(candidate)) % 10**8}", name=data["name"],
                                     arguments=json.dumps(arguments, ensure_ascii=False)))
    return ModelResponse(content="", tool_calls=calls, usage=response.usage, recovered_from_text=True)


class ScriptedModel:
    def __init__(self, steps: list):
        self.steps = list(steps)
        self.requests: list[dict] = []

    @classmethod
    def from_file(cls, path) -> "ScriptedModel":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        steps = data["steps"] if isinstance(data, dict) else data
        return cls([_step_from_json(step) for step in steps])

    def complete(self, messages: list[dict], tools: list[dict]) -> ModelResponse:
        index = sum(1 for m in messages if m.get("role") == "assistant")
        self.requests.append({"index": index, "messages": messages,
                              "tools": [t["function"]["name"] for t in tools]})
        if index >= len(self.steps):
            raise ModelError(f"scripted model has no response #{index + 1}")
        step = self.steps[index]
        if callable(step):
            step = step(messages, tools)
        return step


def _step_from_json(step: dict) -> ModelResponse:
    calls = []
    for raw in step.get("tool_calls", []):
        arguments = raw.get("arguments", {})
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False)
        calls.append(ToolCallRequest(id=raw.get("id", ""), name=raw["name"], arguments=arguments))
    return ModelResponse(content=step.get("content", ""), tool_calls=calls, usage=step.get("usage"))
