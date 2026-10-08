"""The six AI routes, and the promises the workbench makes about them.

``/api/v1/ai/*`` is the only surface in IRIS that can change how every future run is
repaired, which sets a higher bar than the other endpoints and puts most of what is
pinned here on the *refusals* rather than the happy paths:

* **every route is behind the token**, because an unauthenticated caller that could
  install a rule document would be a remote way to alter how runs are repaired. The
  token is configured in these tests on purpose: without one the server is in local
  mode and hands out the loopback identity, and a 401 guard would pass for the wrong
  reason;
* **every response model forbids extra fields**, so a renamed field is a 422 rather
  than a silently omitted one;
* **no route executes anything.** A diagnosis reads; a draft save writes to a
  directory the engine does not load from. Asserted by looking at what each route
  touches, because the claim "this only reads" is otherwise unfalsifiable;
* **the static draft routes are declared before the by-id ones**, the same ordering
  rule the runs routes follow -- a ``{rule_id}`` declared first would swallow the
  collection itself.

The frontend contract is pinned in the last class: ``types.ts`` is written by hand
with no generator behind it, so the only thing standing between a field renamed on one
side and a blank panel is a comparison of the two declarations.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_type_hints

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from iris.api import web_app
from iris.llm.client import LLMClient
from iris.llm.schema import LLMDecision

TOKEN = "workbench-token"
PROJECT = Path(__file__).resolve().parents[1]

STATUS_URL = "/api/v1/ai/status"
DIAGNOSE_URL = "/api/v1/ai/diagnose"
DRAFTS_URL = "/api/v1/ai/drafts"
INSTALL_URL = "/api/v1/ai/drafts/llm-web-init/install"

DRAFT_YAML = """\
id: llm-web-init
description: 在 init 脚本末尾补一行启动 web 服务
stage: boot
detect:
  - path_exists: etc/init.d/rcS
actions:
  - edit: {}
"""


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    """A workbench with a token set and every directory redirected into ``tmp_path``.

    No lifespan: nothing here starts or stops an instance, and running the shutdown
    assembly would only test that a docker call nobody makes does nothing.
    """
    from iris import config
    from iris.api import auth as auth_module
    from iris.config import Settings

    builtin = tmp_path / "builtin"
    builtin.mkdir()
    monkeypatch.setattr(config.Settings, "rules_dir", property(lambda self: builtin))
    monkeypatch.setattr(config, "_settings", config.Settings(iris_home=tmp_path / "home"))
    monkeypatch.setattr(auth_module, "get_settings", lambda: Settings(api_token=TOKEN))
    return TestClient(web_app.install(FastAPI()))


@pytest.fixture
def auth(client) -> TestClient:
    client.headers.update({"X-IRIS-Token": TOKEN})
    return client


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


DECISION = LLMDecision(
    diagnosis="init 脚本没有拉起 web 服务",
    action="DRAFT_PLUGIN",
    plugin_draft={
        "rule_id": "llm-web-init",
        "stage": "boot",
        "description": "在 init 脚本末尾补一行启动 web 服务",
        "yaml": DRAFT_YAML,
    },
    confidence=0.58,
    verify_plan=["重跑后确认 :80 可达"],
)


class TestEveryAiRouteIsBehindTheToken:
    @pytest.mark.parametrize(("method", "path", "body"), [
        ("get", STATUS_URL, None),
        ("post", DIAGNOSE_URL, {"iid": 7}),
        ("get", DRAFTS_URL, None),
        ("post", DRAFTS_URL, {"rule_id": "r", "stage": "boot", "description": "d",
                              "yaml": "id: r\n"}),
        ("post", INSTALL_URL, None),
        ("delete", f"{DRAFTS_URL}/llm-web-init", None),
    ])
    def test_an_unauthenticated_call_is_refused(self, client, method, path, body) -> None:
        """An open route here would be a remote way to change how future runs are
        repaired. ``/api/v1/health`` is the only unauthenticated route in the API and
        it is not one of these."""
        call = getattr(client, method)
        response = call(path, json=body) if method == "post" else call(path)
        assert response.status_code == 401

    @pytest.mark.parametrize("path", [STATUS_URL, DRAFTS_URL])
    def test_the_authenticated_versions_answer(self, auth, path) -> None:
        """Pinned so the 401 guards above cannot pass because the route is missing."""
        assert auth.get(path).status_code == 200


class TestTheStatusEndpointNeverCarriesTheKey:
    def test_an_unconfigured_layer_says_disabled(self, auth) -> None:
        body = auth.get(STATUS_URL).json()

        assert body["state"] == "disabled"
        assert body["base_url_configured"] is False
        assert body["model"] == ""
        assert "IRIS_LLM_BASE_URL" in body["note"]

    def test_a_configured_layer_says_ready_without_a_url_or_a_key(self, auth, monkeypatch) -> None:
        from iris import config

        monkeypatch.setattr(config, "_settings", config.get_settings().model_copy(update={
            "llm_base_url": "http://localhost:11434/v1",
            "llm_api_key": "sk-do-not-leak",
            "llm_model": "qwen2.5-coder",
        }))

        response = auth.get(STATUS_URL)

        assert response.json()["state"] == "ready"
        assert response.json()["model"] == "qwen2.5-coder"
        assert "sk-do-not-leak" not in response.text, (
            "this feeds a page, and a page is the least controlled place a credential "
            "can end up"
        )

    def test_an_endpoint_without_a_model_is_reported_as_incomplete(self, auth, monkeypatch) -> None:
        """A half-configuration would otherwise fail on every diagnosis with a message
        about the model, which reads like an outage rather than a missing setting."""
        from iris import config

        monkeypatch.setattr(config, "_settings", config.get_settings().model_copy(update={
            "llm_base_url": "http://localhost:11434/v1",
        }))

        body = auth.get(STATUS_URL).json()

        assert body["state"] == "unreachable"
        assert body["base_url_configured"] is True
        assert "缺少模型名" in body["note"]


class TestTheDiagnoseEndpointOnlyReads:
    def test_it_reports_a_disabled_layer_without_calling_out(self, auth) -> None:
        body = auth.post(DIAGNOSE_URL, json={"iid": 7}).json()

        assert body == {
            "iid": 7,
            "llm_used": False,
            "decision": None,
            "rule_recommendation": None,
            "disabled_reason": "LLM 未配置：需要 IRIS_LLM_BASE_URL 与 IRIS_LLM_MODEL",
            "error": None,
            "note": body["note"],
        }
        assert body["note"]

    def test_a_model_decision_comes_back_whole(self, auth, monkeypatch) -> None:
        from iris.api import web_app as module

        monkeypatch.setattr(module, "diagnose_instance",
                            lambda iid: _outcome(iid, decision=DECISION))

        body = auth.post(DIAGNOSE_URL, json={"iid": 7}).json()

        assert body["llm_used"] is True
        assert body["decision"]["action"] == "DRAFT_PLUGIN"
        assert body["decision"]["plugin_draft"]["yaml"] == DRAFT_YAML
        assert body["decision"]["confidence"] == pytest.approx(0.58)

    def test_an_unknown_iid_is_still_a_diagnosis_rather_than_a_404(self, auth) -> None:
        """A diagnosis of an instance with no serial log and no runs is a legitimate
        question with a stated answer -- "nothing was readable". Refusing it would make
        the button look broken for exactly the instances that most need it."""
        assert auth.post(DIAGNOSE_URL, json={"iid": 9999}).status_code == 200

    @pytest.mark.parametrize("body", [{"iid": 7, "extra": 1}, {"iid": "seven"}, {}])
    def test_a_body_it_does_not_declare_is_refused(self, auth, body) -> None:
        assert auth.post(DIAGNOSE_URL, json=body).status_code == 422

    def test_the_round_trip_leaves_the_event_loop(self, auth, monkeypatch) -> None:
        """A diagnosis is a blocking HTTP call with a minute-scale timeout. Running it
        on the loop would freeze every other route on a slow endpoint, which is the one
        way this feature could take the workbench down with it."""
        from iris.api import web_app as module

        loops: list[int] = []

        def _spy(*_args, **_kwargs):
            loops.append(1)
            return _outcome(7)

        monkeypatch.setattr(module, "diagnose_instance", _spy)
        original = module.asyncio.to_thread

        async def _tracking(func, /, *args, **kwargs):
            loops.append(0)
            return await original(func, *args, **kwargs)

        monkeypatch.setattr(module.asyncio, "to_thread", _tracking)

        auth.post(DIAGNOSE_URL, json={"iid": 7})

        assert loops[0] == 0, "the diagnosis was awaited on the loop, not handed to a thread"


def _outcome(iid: int, *, decision: LLMDecision | None = None):
    """One :class:`DiagnosisOutcome`, built without the collectors behind it."""
    from iris.llm.diagnose import DiagnosisOutcome

    return DiagnosisOutcome(
        iid=iid, llm_used=decision is not None, decision=decision,
        rule_recommendation=None, disabled_reason=None, error=None, note="note",
    )


class TestTheRoutesAreDeclaredInAnOrderThatMatters:
    def test_the_static_draft_routes_come_before_the_by_id_ones(self) -> None:
        """A ``{rule_id}`` declared above the collection would match the collection
        path itself, and ``GET /api/v1/ai/drafts`` would 404 on a page with no drafts
        -- which looks like an empty library rather than a routing mistake."""
        source = (PROJECT / "src/iris/api/web_app.py").read_text(encoding="utf-8")
        collection = source.index('get("/api/v1/ai/drafts"')
        by_id = source.index('post("/api/v1/ai/drafts/{rule_id}/install"')
        assert collection < by_id

    def test_every_ai_route_is_hung_off_the_app_inside_the_route_block(self) -> None:
        """A route declared outside ``install`` would never be mounted, and the
        ``_INSTALLED`` guard would hide that on a second call."""
        source = (PROJECT / "src/iris/api/web_app.py").read_text(encoding="utf-8")
        mounted = {
            match for match in re.findall(r'@app\.\w+\("(/api/v1/ai/[^"]*)"', source)
        }
        assert mounted == {
            "/api/v1/ai/status",
            "/api/v1/ai/diagnose",
            "/api/v1/ai/drafts",
            "/api/v1/ai/drafts/{rule_id}/install",
            "/api/v1/ai/drafts/{rule_id}",
        }

    @pytest.mark.parametrize("route", [
        "/api/v1/ai/status", "/api/v1/ai/diagnose", "/api/v1/ai/drafts",
        "/api/v1/ai/drafts/{rule_id}/install", "/api/v1/ai/drafts/{rule_id}",
    ])
    def test_each_ai_route_takes_the_caller_it_is_gated_on(self, route) -> None:
        """Same reason as the 401 guards, checked statically: a handler that took no
        ``Caller`` would be authenticated by nothing at all."""
        source = (PROJECT / "src/iris/api/web_app.py").read_text(encoding="utf-8")
        block = re.search(
            rf'@app\.\w+\("{re.escape(route)}".*?\n    async def \w+\((.*?)\) ->',
            source, re.DOTALL,
        )
        assert block, f"{route} has no handler to inspect"
        assert "_caller: Caller" in block.group(1), f"{route} is not gated on Caller"


class TestEveryResponseModelForbidsExtras:
    @pytest.mark.parametrize("name", [
        "DiagnosisResponse", "AiStatusResponse", "PluginDraftView", "DraftListResponse",
        "DraftSaveRequest", "DraftMutationResponse", "DraftInstallResponse",
        "DraftRemovalResponse",
    ])
    def test_the_model_is_strict(self, name) -> None:
        model = getattr(web_app, name)
        assert model.model_config.get("extra") == "forbid", (
            f"{name} drops unknown fields silently, so a typo would return a response "
            f"that quietly omits it"
        )


class TestTheCapabilitiesRowFollowsTheConfiguration:
    def test_the_ai_row_is_available_once_an_endpoint_is_configured(self, auth, monkeypatch) -> None:
        from iris import config

        monkeypatch.setattr(config, "_settings", config.get_settings().model_copy(update={
            "llm_base_url": "http://localhost:11434/v1", "llm_model": "m",
        }))

        row = _capability(auth, "ai-guardian")

        assert row["state"] == "available"
        assert "LLM 长尾归因" in row["detail"]

    def test_the_ai_row_stays_planned_without_one_and_says_what_enables_it(self, auth) -> None:
        """The deterministic self-healing is available either way, and saying
        ``planned`` for the whole row would understate what already works."""
        row = _capability(auth, "ai-guardian")

        assert row["state"] == "planned"
        assert "确定性自愈可用" in row["detail"]
        assert "IRIS_LLM_BASE_URL" in row["detail"]
        assert row["evidence"] == "iris.monitor.ai_guardian + iris.llm"


def _capability(auth: TestClient, capability_id: str) -> dict:
    for row in auth.get("/api/v1/capabilities").json()["items"]:
        if row["id"] == capability_id:
            return row
    raise AssertionError(f"{capability_id} is not listed at all")


class TestTheFrontendTypesMatchTheServer:
    """``types.ts`` is hand-written with no generator behind it.

    Nothing else would notice a field renamed on one side only: the payload arrives,
    the response is 200, and the panel shows a blank cell.
    """

    @staticmethod
    def _frontend_interface(name: str) -> set[str]:
        source = (PROJECT / "web/src/lib/types.ts").read_text(encoding="utf-8")
        body = re.search(rf"export interface {name}\b[^{{]*\{{(.*?)\n\}}", source, re.DOTALL)
        assert body, f"{name} is not declared in types.ts"
        return set(re.findall(r"^\s*(\w+)\??:", body.group(1), re.MULTILINE))

    @pytest.mark.parametrize("name,model_name", [
        ("DiagnosisResponse", "DiagnosisResponse"),
        ("AiStatus", "AiStatusResponse"),
        ("PluginDraftView", "PluginDraftView"),
        ("DraftList", "DraftListResponse"),
        ("DraftSaveRequest", "DraftSaveRequest"),
        ("DraftMutationResponse", "DraftMutationResponse"),
        ("DraftInstallResponse", "DraftInstallResponse"),
        ("DraftRemovalResponse", "DraftRemovalResponse"),
        ("PluginDraft", "PluginDraft"),
        ("LlmDecision", "LLMDecision"),
    ])
    def test_the_declared_fields_are_the_same_on_both_sides(self, name, model_name) -> None:
        frontend = self._frontend_interface(name)
        server = set(getattr(web_app, model_name).model_fields)

        assert frontend == server, (
            f"{name} has drifted from {model_name}: "
            f"only in types.ts {sorted(frontend - server)}; "
            f"only on the server {sorted(server - frontend)}"
        )

    def test_the_action_vocabulary_is_the_same_on_both_sides(self) -> None:
        """Three values, and the browser's list is the one the review panel renders a
        badge from -- a fourth server-side value would render as one of the three."""
        from iris.llm.schema import LLMDecision

        server_values = set(get_type_hints(LLMDecision)["action"].__args__)

        source = (PROJECT / "web/src/lib/types.ts").read_text(encoding="utf-8")
        declared = re.search(r"export type LlmAction =([^\n]+)", source)
        frontend_values = set(re.findall(r"'([^']+)'", declared.group(1)))

        assert frontend_values == server_values == {
            "NONE", "WEB_SERVER_RESTART", "DRAFT_PLUGIN",
        }

    def test_every_ai_endpoint_is_reached_from_the_browser(self) -> None:
        """An endpoint nothing fetches is an endpoint nobody can see -- and the whole
        feature is button-triggered, so a missing call means a button that does
        nothing."""
        source = (PROJECT / "web/src/lib/api.ts").read_text(encoding="utf-8")
        for path in ("/api/v1/ai/status", "/api/v1/ai/diagnose", "/api/v1/ai/drafts"):
            assert path in source, f"{path} is never requested"

    def test_the_hook_naming_matches_the_endpoints_it_claims(self) -> None:
        queries = (PROJECT / "web/src/hooks/queries.ts").read_text(encoding="utf-8")
        for hook in ("useAiStatus", "useAiDrafts", "useDiagnose", "useSaveDraft",
                     "useInstallDraft", "useRejectDraft"):
            assert f"export function {hook}(" in queries, f"{hook} does not exist"

    def test_the_review_surfaces_are_actually_rendered(self) -> None:
        """A panel defined but never mounted, or a button wired to nothing, is the
        failure this whole design's safety rests on not having."""
        plugins = (PROJECT / "web/src/pages/Plugins.tsx").read_text(encoding="utf-8")
        instances = (PROJECT / "web/src/pages/Instances.tsx").read_text(encoding="utf-8")
        assert "<AiDraftPanel" in plugins
        assert "<DiagnosisModal" in instances
        assert "useSaveDraft" in instances and "useInstallDraft" in plugins


class TestTheNewSurfacesStayInsideTheDesignTokens:
    @pytest.mark.parametrize("page,panels", [
        ("Plugins", ["AiDraftPanel", "DraftRow", "DraftDetailModal"]),
        ("Instances", ["DiagnosisModal", "DraftProposal"]),
    ])
    def test_no_hardcoded_colour_reaches_them(self, page, panels) -> None:
        source = (PROJECT / f"web/src/pages/{page}.tsx").read_text(encoding="utf-8")
        for panel in panels:
            body = re.search(rf"function {panel}\b.*?(?=\nfunction |\Z)", source, re.DOTALL)
            assert body, f"{panel} is not defined on the {page} page"
            text = body.group(0)
            for banned in ("bg-[#", "text-[#", "border-[#", "rgba(", "hsl("):
                assert banned not in text, f"{panel} hardcodes {banned}"
            # Word-bounded, because ``whitespace-pre-wrap`` contains "white" and a
            # substring rule would flag the very panels that got the tokens right.
            for banned in ("white", "black"):
                assert not re.search(rf"[\s'\"/-]{banned}[\s'\"/-]", text), \
                    f"{panel} hardcodes {banned}"

    def test_the_llm_config_is_reachable_only_through_the_documented_names(self) -> None:
        """The offline requirement is the reason the base URL exists at all, so the
        environment variable a judge would set has to be the real one."""
        source = (PROJECT / "src/iris/config.py").read_text(encoding="utf-8")
        for name in ("llm_base_url", "llm_api_key", "llm_model", "llm_timeout_sec",
                     "llm_max_retries", "llm_max_context_chars"):
            assert name in source, f"{name} is not a setting"

    def test_the_web_data_row_and_the_status_endpoint_tell_the_same_story(self) -> None:
        """Two places that report the layer's state have to agree, or the capability
        list says "planned" while the status endpoint says "ready"."""
        source = (PROJECT / "src/iris/api/web_data.py").read_text(encoding="utf-8")
        assert "llm_ready" in source, "the capability row no longer follows the settings"