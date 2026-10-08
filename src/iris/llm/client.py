"""One OpenAI-compatible round trip, and nothing more.

The client is deliberately small. It is a *single* request per diagnosis -- no
conversation, no tool loop, no streaming -- because a diagnosis has to be
testable (one request in, one JSON out), auditable (the ledger records what the
model said, not a transcript nobody replays) and degradable (a slow or absent
endpoint is one exception, not a session that hangs).

The endpoint is an OpenAI-compatible ``/chat/completions`` so a local server
(ollama, vLLM, llama.cpp) is configured the same way a hosted one is: that is
what keeps a judge running offline able to point ``IRIS_LLM_BASE_URL`` at
localhost instead of being walled out of the feature.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from iris.llm.schema import LLMDecision

__all__ = [
    "LLMClient",
    "LLMError",
]


class LLMError(RuntimeError):
    """One failed round trip, with the reason the caller will ledger.

    Raising rather than returning None keeps the failure visible at the call
    site: a diagnosis that silently produced nothing would read as "the model
    said nothing was wrong", which is the one reading a failure diagnosis must
    never allow.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class LLMClient:
    """A single-round-trip client for one OpenAI-compatible endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout_sec: float = 60.0,
        max_retries: int = 1,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_sec = timeout_sec
        self.max_retries = max(0, max_retries)
        # Injectable for the tests: a diagnosis is one POST, and a fake
        # transport exercises the same parse path a real endpoint takes.
        self._transport = transport

    @property
    def enabled(self) -> bool:
        """Both an endpoint and a model are needed; one without the other is a
        half-configuration that would fail on every request."""
        return bool(self.base_url.strip()) and bool(self.model.strip())

    def decide(self, prompt: str) -> LLMDecision:
        """One diagnosis round trip. Raises :class:`LLMError` on any failure."""
        if not self.enabled:
            raise LLMError("LLM 未配置：需要 IRIS_LLM_BASE_URL 与 IRIS_LLM_MODEL")

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
        }
        url = f"{self.base_url}/chat/completions"

        last_reason = ""
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(
                    timeout=self.timeout_sec, transport=self._transport
                ) as http:
                    response = http.post(url, headers=headers, json=payload)
            except httpx.HTTPError as exc:
                last_reason = f"LLM 请求失败：{exc.__class__.__name__}: {exc}"
                continue
            if response.status_code != 200:
                last_reason = (
                    f"LLM 端点返回 HTTP {response.status_code}: "
                    f"{response.text[:200]}"
                )
                continue
            try:
                return _parse_decision(response.json())
            except (ValueError, KeyError, TypeError) as exc:
                last_reason = f"LLM 响应无法解析：{exc}"
                continue
        raise LLMError(last_reason or "LLM 请求失败：重试预算耗尽")


def _parse_decision(payload: Any) -> LLMDecision:
    """Pull the decision JSON out of a chat-completions response body.

    Tolerates the `` ```json `` fence many local servers put around JSON, but
    nothing else: the content either parses into the fixed schema or the round
    trip failed, and an unfenced trailing remark does not get forgiven -- a
    diagnosis that had to be salvaged is a diagnosis a person should see
    failing, not one silently repaired.
    """
    choices = payload["choices"]
    content = choices[0]["message"]["content"]
    if not isinstance(content, str):
        raise TypeError(f"content 不是字符串: {type(content).__name__}")
    text = content.strip()
    fenced = re.match(r"^```(?:json)?\s*\n(.*?)\n?```\s*$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    return LLMDecision.model_validate_json(text)