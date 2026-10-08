"""The diagnosis itself: rules first, model only for the long tail, never a hard failure.

Three orders are pinned here, and they are the whole design:

* **The rule engine goes first, and a hit short-circuits the round trip.** Asserted by
  passing a client that raises if it is called: a test that only checked the outcome
  would still pass with a "short-circuit" that called the model and then ignored the
  answer. This is the difference between "AI attribution" and "an API bill per failed
  run", and the promoted-repair loop is only worth having if the rules engine really
  does keep handling what it already handles.
* **The model is only reached for signals the pattern counts cannot name.** With no
  serial log there is no rule recommendation and no material, so the prompt says so
  instead of carrying a placeholder.
* **No configuration is a stated answer, not a failure.** Every collector degrades on
  its own, a disabled layer still returns the rules engine's verdict, and a failed call
  reports its reason rather than raising -- a diagnosis endpoint that 500s would be
  indistinguishable, to the operator, from the thing being broken.

The outcome's field set is what the page renders, so ``llm_used`` alone is pinned not
to be enough: three outcomes with ``llm_used=False`` must stay distinguishable, or
"the rules engine already handled it" would render as "no diagnosis".
"""

from __future__ import annotations

import httpx
import pytest

from iris.llm.client import LLMClient, LLMError
from iris.llm.context import DiagnosisMaterials, build_prompt
from iris.llm.diagnose import diagnose_instance
from iris.llm.schema import LLMDecision
from iris.monitor.ai_guardian import (
    ACTION_DIAGNOSTIC,
    ACTION_WEB_DIAGNOSIS,
)

DECISION = LLMDecision(
    diagnosis="init 脚本没有拉起 web 服务进程",
    action="DRAFT_PLUGIN",
    plugin_draft={
        "rule_id": "llm-web-init",
        "stage": "boot",
        "description": "在 init 脚本末尾补一行启动 web 服务",
        "yaml": "id: llm-web-init\ndescription: d\nstage: boot\n",
    },
    confidence=0.58,
    verify_plan=["重跑后确认 :80 可达"],
)


class _ForbiddenClient(LLMClient):
    """A client that fails the test if it is ever asked for a round trip."""

    def __init__(self) -> None:
        super().__init__(base_url="http://llm.invalid/v1", model="m")

    def decide(self, prompt: str) -> LLMDecision:
        raise AssertionError("the model must not be called for this diagnosis")


def _client_returning(decision: LLMDecision) -> LLMClient:
    """A client whose single round trip yields ``decision``, with no socket."""
    return LLMClient(
        base_url="http://llm.invalid/v1",
        model="m",
        transport=httpx.MockTransport(
            lambda _r: httpx.Response(
                200,
                json={"choices": [{"message": {"content": decision.model_dump_json()}}]},
            )
        ),
    )


def _client_failing(reason: str) -> LLMClient:
    """A client whose single round trip fails, with no socket."""
    return LLMClient(
        base_url="http://llm.invalid/v1",
        model="m",
        max_retries=0,
        transport=httpx.MockTransport(
            lambda _r: (_ for _ in ()).throw(httpx.ConnectError(reason))
        ),
    )


def _write_log(scratch, iid: int, text: str) -> None:
    log_dir = scratch / f"emulate-{iid}"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "qemu.serial.log").write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """An isolated scratch directory, pointed at by the settings.

    Every collector resolves its inputs from ``settings.scratch_dir``, so this is what
    keeps a diagnosis from reading the real corpus's serial logs.
    """
    from iris import config

    monkeypatch.setattr(config, "_settings", config.Settings(iris_home=tmp_path / "home"))
    return tmp_path / "home" / "scratch"


class TestTheRuleEngineGoesFirst:
    def test_a_pattern_the_rules_understand_never_reaches_the_model(self, scratch) -> None:
        """`not_started` is the guardian's own `web_server_status` reading, and it
        carries a recommendation. Asking a model about it would cost a call to be told
        what a regular expression already said."""
        _write_log(scratch, 7, "Linux version 2.6.32\nboot ok\n")

        outcome = diagnose_instance(7, client=_ForbiddenClient())

        assert outcome.llm_used is False
        assert outcome.decision is None
        assert outcome.rule_recommendation == ACTION_WEB_DIAGNOSIS
        assert outcome.disabled_reason is None and outcome.error is None
        assert "未调用模型" in outcome.note

    def test_a_stronger_signal_wins_over_the_weaker_one(self, scratch) -> None:
        """Watchdog first, then lockup, then diag, then the web states: the same order
        the guardian uses, because a diagnosis that recommended a restart for a guest
        that is crash-looping in diag would be recommending the wrong repair."""
        _write_log(scratch, 7, "diag: handler: signal 11\nLinux version 2.6.32\n")

        outcome = diagnose_instance(7, client=_ForbiddenClient())

        assert outcome.rule_recommendation == ACTION_DIAGNOSTIC

    def test_no_serial_log_means_no_recommendation_rather_than_a_guess(self, scratch) -> None:
        """The rule engine reads the log's patterns. With no log there is nothing to
        read, so the rules layer steps aside -- but the model is still consulted,
        because an absent log is exactly the long tail this layer exists for. What
        must not happen is a rule engine reporting a state it never observed."""
        outcome = diagnose_instance(7, client=_client_returning(DECISION))

        assert outcome.rule_recommendation is None
        assert outcome.llm_used is True


class TestTheModelSeesWhatWasActuallyRead:
    def test_a_long_tail_signal_reaches_the_model_and_its_decision_comes_back(
        self, scratch,
    ) -> None:
        """A live web server in the log with no crash: the counts say active, so there
        is no recovery action to recommend, and this is exactly the case the model is
        for."""
        _write_log(scratch, 7, "goahead: listening on 0.0.0.0:80\n")

        outcome = diagnose_instance(7, client=_client_returning(DECISION))

        assert outcome.llm_used is True
        assert outcome.rule_recommendation is None
        assert outcome.decision is not None
        assert outcome.decision.action == "DRAFT_PLUGIN"
        assert outcome.decision.plugin_draft is not None
        assert "人工确认" in outcome.note

    def test_the_prompt_carries_the_materials_rather_than_a_placeholder(
        self, scratch, monkeypatch,
    ) -> None:
        """A prompt that fabricated what it could not read would make the model
        attribute a failure the materials do not describe -- the one way this feature
        could be confidently wrong."""
        _write_log(scratch, 7, "goahead: listening on 0.0.0.0:80\ninit: panic\n")
        seen: list[str] = []

        class _Capturing(LLMClient):
            def decide(self, prompt: str) -> LLMDecision:
                seen.append(prompt)
                return DECISION

        client = _Capturing(base_url="http://llm.invalid/v1", model="m")
        diagnose_instance(7, client=client)

        prompt = seen[0]
        assert "goahead: listening on 0.0.0.0:80" in prompt
        assert "未取到：找不到提取出的 rootfs 目录" in prompt
        assert "未取到：串口日志不存在或不可读" not in prompt
        assert "web_server_start: 0" in prompt

    def test_a_missing_serial_log_is_stated_rather_than_faked(self, scratch) -> None:
        seen: list[str] = []

        class _Capturing(LLMClient):
            def decide(self, prompt: str) -> LLMDecision:
                seen.append(prompt)
                return DECISION

        diagnose_instance(7, client=_Capturing(base_url="http://llm.invalid/v1", model="m"))

        assert "未取到：串口日志不存在或不可读" in seen[0]
        assert "无命中模式" in seen[0]

    def test_the_prompt_states_the_engine_grammar_it_may_use(self, scratch) -> None:
        """The model is writing a document the engine parses. Keys it invents are
        recorded as warnings and ignored, so the prompt names the allowed ones."""
        seen: list[str] = []

        class _Capturing(LLMClient):
            def decide(self, prompt: str) -> LLMDecision:
                seen.append(prompt)
                return DECISION

        diagnose_instance(7, client=_Capturing(base_url="http://llm.invalid/v1", model="m"))

        prompt = seen[0]
        assert "path_exists" in prompt and "file_regex" in prompt
        assert "guest_shell" in prompt
        assert "NONE / WEB_SERVER_RESTART / DRAFT_PLUGIN" in prompt

    def test_the_serial_log_is_capped_so_a_long_boot_cannot_blow_the_context(
        self, scratch,
    ) -> None:
        """A 1.3 MB serial log is normal; sending it whole would be a request the
        endpoint refuses, and a refusal reads as "AI unavailable"."""
        _write_log(scratch, 7, "goahead: listening on 0.0.0.0:80\n" + ("noise line\n" * 5000))
        seen: list[str] = []

        class _Capturing(LLMClient):
            def decide(self, prompt: str) -> LLMDecision:
                seen.append(prompt)
                return DECISION

        diagnose_instance(7, client=_Capturing(base_url="http://llm.invalid/v1", model="m"))

        assert "另有更早内容未列入" in seen[0]
        assert len(seen[0]) < 20000


class TestNothingHereIsAFailure:
    def test_an_unconfigured_layer_reports_itself_disabled(self, scratch) -> None:
        """With no endpoint configured the endpoint is a fact, not an error: the rules
        engine already ran, and its verdict still comes back."""
        outcome = diagnose_instance(7)

        assert outcome.llm_used is False
        assert outcome.decision is None
        assert outcome.rule_recommendation is None
        assert "IRIS_LLM_BASE_URL" in outcome.disabled_reason
        assert outcome.error is None

    def test_a_failed_call_reports_its_reason_and_keeps_the_endpoint_up(self, scratch) -> None:
        _write_log(scratch, 7, "goahead: listening on 0.0.0.0:80\n")

        outcome = diagnose_instance(7, client=_client_failing("connection refused"))

        assert outcome.llm_used is False
        assert outcome.decision is None
        assert outcome.error is not None and "connection refused" in outcome.error
        assert outcome.disabled_reason is None
        assert "可修正端点后重试" in outcome.note

    def test_the_rules_layers_verdict_survives_a_failed_call(self, scratch) -> None:
        _write_log(scratch, 7, "Linux version 2.6.32\nboot ok\n")

        outcome = diagnose_instance(7, client=_client_failing("boom"))

        assert outcome.rule_recommendation == ACTION_WEB_DIAGNOSIS
        assert outcome.error is None, "the model was never reached, so nothing failed"

    def test_an_unreadable_database_does_not_stop_a_diagnosis(self, scratch, monkeypatch) -> None:
        """Every collector degrades on its own. A corpus that cannot be read is a gap
        in the materials, stated as one, not a reason to refuse the question."""
        from iris.db import engine as engine_module

        def _boom(_url: str):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(engine_module, "get_engine", _boom)

        outcome = diagnose_instance(7, client=_client_returning(DECISION))

        assert outcome.llm_used is True
        assert "运行历史不可读" in _history_of(7)


def _history_of(iid: int) -> str:
    """One instance's collected history, for reading what the prompt would have said."""
    from iris.llm.diagnose import _history_summary

    return _history_summary(iid)


class TestTheOutcomeSaysWhichOfThreeThingsHappened:
    """The page branches on these fields, so the three answers have to stay
    distinguishable: a failed call rendered as "the rules engine handled it" is the
    specific wrong reading this shape exists to prevent."""

    @staticmethod
    def _which(outcome) -> str | None:
        """Which of the three mutually exclusive fields this outcome carries."""
        present = [
            name for name in ("rule_recommendation", "disabled_reason", "error")
            if getattr(outcome, name) is not None
        ]
        assert len(present) <= 1, f"{present} are all set; the page would pick one and drop the rest"
        return present[0] if present else None

    def test_a_rules_hit_is_a_rules_hit(self, scratch) -> None:
        _write_log(scratch, 7, "Linux version 2.6.32\nboot ok\n")
        assert self._which(diagnose_instance(7)) == "rule_recommendation"

    def test_no_configuration_is_the_disabled_answer(self, scratch) -> None:
        assert self._which(diagnose_instance(7)) == "disabled_reason"

    def test_a_failed_call_is_the_error_answer(self, scratch) -> None:
        _write_log(scratch, 7, "goahead: listening on 0.0.0.0:80\n")
        assert self._which(
            diagnose_instance(7, client=_client_failing("boom"))
        ) == "error"

    def test_a_successful_call_carries_none_of_them(self, scratch) -> None:
        """The model answered, so there is no rule recommendation, no reason to be
        disabled and no failure to report -- and the page reads that as the one case
        where a decision exists."""
        _write_log(scratch, 7, "goahead: listening on 0.0.0.0:80\n")
        outcome = diagnose_instance(7, client=_client_returning(DECISION))

        assert self._which(outcome) is None
        assert outcome.llm_used is True

    def test_the_outcome_carries_the_iid_it_was_asked_about(self, scratch) -> None:
        assert diagnose_instance(7).iid == 7


class TestThePromptIsAFunctionOfItsMaterials:
    def test_the_same_materials_produce_the_same_prompt(self) -> None:
        materials = DiagnosisMaterials(
            iid=7, arch="mips", serial_tail="goahead: listening on 0.0.0.0:80\n",
            serial_truncated=False, crash_context=None,
            pattern_counts={"web_server_active": 1},
            web_server_status="active", rootfs_summary=None,
            history="共 1 次运行", plugins_summary="（无已加载的规则插件）",
        )
        assert build_prompt(materials) == build_prompt(materials)

    def test_an_unknown_arch_is_a_question_mark_not_an_empty_field(self) -> None:
        materials = DiagnosisMaterials(
            iid=7, arch="", serial_tail=None, serial_truncated=False, crash_context=None,
            pattern_counts={}, web_server_status="unknown", rootfs_summary=None,
            history="无", plugins_summary="无",
        )
        assert "架构：?" in build_prompt(materials)

    def test_a_truncated_log_says_so_and_a_whole_one_does_not(self) -> None:
        def _prompt(truncated: bool) -> str:
            return build_prompt(DiagnosisMaterials(
                iid=7, arch="mips", serial_tail="line\n", serial_truncated=truncated,
                crash_context=None, pattern_counts={}, web_server_status="unknown",
                rootfs_summary=None, history="无", plugins_summary="无",
            ))

        assert "另有更早内容未列入" in _prompt(True)
        assert "全文在内" in _prompt(False)


class TestTheClientRefusesToLie:
    def test_an_error_carries_the_reason_the_caller_ledgers(self) -> None:
        """Raising rather than returning None keeps the failure visible at the call
        site: a diagnosis that silently produced nothing would read as "the model said
        nothing was wrong"."""
        error = LLMError("endpoint refused")
        assert error.reason == "endpoint refused"
        assert isinstance(error, RuntimeError)