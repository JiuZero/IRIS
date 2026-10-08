"""The LLM client: one request, one JSON out, and every way that can fail.

A diagnosis has to be testable and degradable, and those two properties live entirely
in this file. The client is exercised through ``httpx.MockTransport`` rather than a
stub of its own method, so the tests walk the same request-building and
response-parsing path a real endpoint takes -- a stub would let a wrong URL or a
missing ``Authorization`` header pass.

What is pinned here:

* the request shape -- one ``/chat/completions`` POST, ``temperature=0.0``, the model
  from settings. A diagnosis that varied its temperature would be a different
  diagnosis on every retry;
* the response tolerance boundary -- a ```json fence is forgiven because local
  servers put one there, a trailing remark is not, because a response that had to be
  salvaged is a response a person should see failing;
* the retry budget is spent on transport and status errors and counted, so a broken
  endpoint cannot turn one button press into a storm;
* a half-configuration refuses before any socket is opened. An endpoint without a
  model would otherwise fail once per diagnosis with a message about the model.
"""

from __future__ import annotations

import json

import httpx
import pytest

from iris.llm.client import LLMClient, LLMError

DECISION = {
    "diagnosis": "web 服务进程启动后退出，串口日志无后续输出",
    "action": "WEB_SERVER_RESTART",
    "plugin_draft": None,
    "confidence": 0.62,
    "verify_plan": ["重启后复查首页是否可达"],
}


def _body(content: str) -> dict:
    """A chat-completions envelope around ``content``."""
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _client(handler, **overrides) -> LLMClient:
    """A client wired to ``handler`` through an injected transport.

    ``max_retries=0`` by default so a test that does not think about retries gets
    exactly one request; the tests about retries ask for more.
    """
    options = {
        "base_url": "http://llm.invalid/v1",
        "model": "test-model",
        "transport": httpx.MockTransport(handler),
        "max_retries": 0,
    }
    options.update(overrides)
    return LLMClient(**options)


class TestTheRequestShape:
    def test_one_post_to_chat_completions_with_the_configured_model(self):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=_body(json.dumps(DECISION)))

        decision = _client(handler).decide("prompt")

        assert decision.action == "WEB_SERVER_RESTART"
        assert decision.confidence == pytest.approx(0.62)
        assert len(seen) == 1
        assert str(seen[0].url) == "http://llm.invalid/v1/chat/completions"
        payload = json.loads(seen[0].content)
        assert payload["model"] == "test-model"
        assert payload["temperature"] == 0.0
        assert payload["messages"] == [{"role": "user", "content": "prompt"}]

    def test_the_api_key_travels_as_a_header_and_only_when_set(self):
        headers: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            headers.append(dict(request.headers))
            return httpx.Response(200, json=_body(json.dumps(DECISION)))

        _client(handler, api_key="secret").decide("prompt")
        assert headers[0]["authorization"] == "Bearer secret"

        _client(handler).decide("prompt")
        assert "authorization" not in headers[1]

    def test_a_trailing_slash_in_the_base_url_does_not_double_up(self):
        """Configured from a YAML file by hand, so ``.../v1/`` is the likely spelling."""
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, json=_body(json.dumps(DECISION)))

        _client(handler, base_url="http://llm.invalid/v1/").decide("prompt")
        assert seen == ["http://llm.invalid/v1/chat/completions"]


class TestTheResponseToleranceBoundary:
    def test_a_json_fence_is_forgiven(self):
        """Local servers fence their JSON. Refusing it would make an offline judge
        unable to use the feature at all, which is the opposite of the intent."""
        fenced = f"```json\n{json.dumps(DECISION)}\n```"
        decision = _client(lambda _r: httpx.Response(200, json=_body(fenced))).decide("p")
        assert decision.action == "WEB_SERVER_RESTART"

    def test_a_trailing_remark_is_not(self):
        """A response that had to be salvaged is a response a person should see
        failing -- a diagnosis quietly repaired out of prose is a diagnosis nobody
        reviewed."""
        rambling = f"{json.dumps(DECISION)}\n希望以上分析对您有帮助！"
        with pytest.raises(LLMError) as caught:
            _client(lambda _r: httpx.Response(200, json=_body(rambling))).decide("p")
        assert "无法解析" in caught.value.reason

    @pytest.mark.parametrize("content", ["", "not json at all", "[1, 2, 3]"])
    def test_content_that_is_not_a_decision_fails(self, content):
        with pytest.raises(LLMError):
            _client(lambda _r: httpx.Response(200, json=_body(content))).decide("p")

    def test_an_action_outside_the_three_values_is_refused(self):
        """The vocabulary is three values because the other recovery actions take
        their parameters from the pattern counts, and this call only happens when the
        counts said nothing. A model inventing a fourth action must not get a
        `params` blob nobody validated."""
        widened = dict(DECISION, action="RESOURCE_CLEANUP")
        with pytest.raises(LLMError):
            _client(lambda _r: httpx.Response(200, json=_body(json.dumps(widened)))).decide("p")

    def test_a_decision_carrying_an_undeclared_key_is_refused(self):
        extra = dict(DECISION, params={"pid": 42})
        with pytest.raises(LLMError):
            _client(lambda _r: httpx.Response(200, json=_body(json.dumps(extra)))).decide("p")

    def test_a_confidence_outside_zero_to_one_is_refused(self):
        over = dict(DECISION, confidence=1.4)
        with pytest.raises(LLMError):
            _client(lambda _r: httpx.Response(200, json=_body(json.dumps(over)))).decide("p")

    def test_a_response_without_choices_fails_rather_than_returning_nothing(self):
        with pytest.raises(LLMError):
            _client(lambda _r: httpx.Response(200, json={"id": "x"})).decide("p")

    def test_a_null_content_fails(self):
        envelope = {"choices": [{"message": {"role": "assistant", "content": None}}]}
        with pytest.raises(LLMError):
            _client(lambda _r: httpx.Response(200, json=envelope)).decide("p")


class TestTheRetryBudget:
    def test_a_transport_error_is_retried_within_the_budget(self):
        attempts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            if len(attempts) == 1:
                raise httpx.ConnectError("connection refused", request=request)
            return httpx.Response(200, json=_body(json.dumps(DECISION)))

        assert _client(handler, max_retries=1).decide("p").action == "WEB_SERVER_RESTART"
        assert len(attempts) == 2

    def test_a_server_error_is_retried_and_the_body_is_quoted_when_it_gives_up(self):
        attempts: list[int] = []

        def handler(_request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(503, text="upstream not ready")

        with pytest.raises(LLMError) as caught:
            _client(handler, max_retries=1).decide("p")
        assert len(attempts) == 2
        assert "HTTP 503" in caught.value.reason
        assert "upstream not ready" in caught.value.reason

    def test_a_zero_budget_means_exactly_one_attempt(self):
        attempts: list[int] = []

        def handler(_request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            return httpx.Response(500, text="boom")

        with pytest.raises(LLMError):
            _client(handler, max_retries=0).decide("p")
        assert len(attempts) == 1

    def test_an_unparseable_body_is_retried_rather_than_reported_at_once(self):
        """Same shape as a flaky endpoint: one bad body in a run of good ones should
        not cost the diagnosis."""
        attempts: list[int] = []

        def handler(_request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            if len(attempts) == 1:
                return httpx.Response(200, json={"id": "no-choices-here"})
            return httpx.Response(200, json=_body(json.dumps(DECISION)))

        assert _client(handler, max_retries=1).decide("p").action == "WEB_SERVER_RESTART"
        assert len(attempts) == 2


class TestAHalfConfiguredLayerNeverOpensASocket:
    @pytest.mark.parametrize(
        ("base_url", "model"),
        [("", "test-model"), ("http://llm.invalid/v1", ""), ("   ", "   ")],
    )
    def test_one_without_the_other_is_refused_before_the_request(self, base_url, model):
        def handler(_request: httpx.Request) -> httpx.Response:
            raise AssertionError("a disabled layer must not reach the network")

        client = LLMClient(base_url=base_url, model=model, transport=httpx.MockTransport(handler))
        assert client.enabled is False
        with pytest.raises(LLMError, match="未配置"):
            client.decide("p")

    def test_both_set_means_enabled(self):
        assert LLMClient(base_url="http://llm.invalid/v1", model="m").enabled is True