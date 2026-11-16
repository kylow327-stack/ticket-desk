"""OpenAI-compatible DeepSeek client. The API key is never logged."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol

from openai import OpenAI

from ticket_agent.config import Settings, get_settings
from ticket_agent.schemas import Usage


def _message_to_dict(message: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": "assistant", "content": message.content or ""}
    reasoning = getattr(message, "reasoning_content", None)
    if reasoning:
        payload["reasoning_content"] = reasoning
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        payload["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments or "{}",
                },
            }
            for tc in tool_calls
        ]
    return payload


def _usage_from_response(response: Any) -> Usage:
    usage = getattr(response, "usage", None)
    if not usage:
        return Usage()
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion = int(getattr(usage, "completion_tokens", 0) or 0)
    total = int(getattr(usage, "total_tokens", 0) or (prompt + completion))
    cached = 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        cached = int(getattr(details, "cached_tokens", 0) or 0)
    return Usage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        cached_tokens=cached,
    )


def sanitize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for message in messages:
        item = {k: v for k, v in message.items() if v is not None}
        if item.get("role") == "assistant" and not item.get("tool_calls"):
            item.pop("tool_calls", None)
        cleaned.append(item)
    return cleaned


class ChatClient(Protocol):
    model: str

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], Usage]: ...


class OpenAIChatClient:
    def __init__(self, settings: Optional[Settings] = None, client: Optional[OpenAI] = None):
        self.settings = settings or get_settings()
        self.model = self.settings.model
        self._client = client or OpenAI(
            api_key=self.settings.api_key,
            base_url=self.settings.base_url,
        )

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], Usage]:
        # Thinking is disabled for this demo: lower latency and cost, and simpler tool-call traces.
        response = self._client.chat.completions.create(
            model=self.model,
            messages=sanitize_messages(messages),
            tools=tools or None,
            extra_body={"thinking": {"type": "disabled"}},
        )
        choice = response.choices[0].message
        return _message_to_dict(choice), _usage_from_response(response)


@dataclass
class FakeResponse:
    message: dict[str, Any]
    usage: Usage


class ScriptedClient:
    """Deterministic client for loop-limit and other unit tests. No network."""

    def __init__(self, script: list[dict[str, Any]], model: str = "scripted-fake"):
        self.model = model
        self.script = list(script)
        self.calls = 0

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], Usage]:
        self.calls += 1
        if not self.script:
            raise RuntimeError("ScriptedClient has no more scripted responses")
        item = self.script.pop(0)
        if callable(item):
            item = item(messages, tools, self.calls)
        return item, Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2)
