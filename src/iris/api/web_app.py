"""把工作台装到现有的 FastAPI 应用上。

只在这里做装配:路由注册、静态资源、SPA 回退、关机清理。其余逻辑各归其位——数据
形状在 :mod:`iris.api.web_data`,串口桥在 :mod:`iris.api.serial_bridge`,WebSocket
端点在 :mod:`iris.api.web_terminal`。

**为什么复用同一个 app 而不是新建一个。** `iris.api.server:app` 上已经有 7 条经过
鉴权与所有权校验的路由。复制一份 app 会让"工作台看到的仿真列表"和"API 能操作的
仿真列表"变成两套状态,而它们必须是同一套——用户从页面上停止一个实例,再用 API
查,看到的必须是一致的。所以这里是往同一个 app 上挂。

**为什么安装是幂等的。** uvicorn 的 reload 会重新执行装配。重复挂载静态目录会在
路由表里留下两条同样的规则(行为不确定),重复注册关机回调会让清理跑两遍(第二遍
面对的是已经清空的容器列表)。所以用 app.state 上的标记做门。

模块级**不**安装:``import iris.api.web_app`` 不应该改变任何一个已经建好的 app,
包括测试里用 TestClient 建的那些。装配由 :func:`install` 显式触发。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from iris.api import host_metrics, web_data
from iris.api.plugins import (
    MAX_PLUGIN_BYTES,
    PluginRejected,
    install_plugin,
    remove_plugin,
)
from iris.api.server import Caller, _read_upload
from iris.api.web_terminal import close_all_bridges, notify_shutdown, terminal_endpoint
from iris.config import get_settings
from iris.db.active import get_live, release
from iris.db.models import ActiveEmulation
from iris.emulate.orchestrator import stop_emulation
from iris.llm.diagnose import diagnose_instance
from iris.llm.drafts import DraftRecord, install_draft, list_drafts, reject_draft, save_draft
from iris.llm.schema import LLMDecision, PluginDraft
from iris.log import get_logger

logger = get_logger(__name__)

#: Set once per app so a second install is a no-op rather than a duplicate.
_INSTALLED = "iris_web_installed"

#: Where the built frontend may live, most specific first.
#:
#: A single relative path cannot cover both cases, and guessing one is how a wheel
#: ends up serving a 503 with no hint: in a source checkout the files sit at
#: ``<repo>/web/dist``, while in an installed wheel they sit inside the package at
#: ``iris/web/dist``. The environment variable is an override for the third case --
#: a distribution that stages the assets somewhere else entirely.
_DIST_CANDIDATES = (
    Path(__file__).resolve().parents[1] / "web" / "dist",
    Path(__file__).resolve().parents[3] / "web" / "dist",
)

#: Reported when nothing is found, so the 503 names the path a build would produce.
_DIST_FALLBACK = _DIST_CANDIDATES[1]


def dist_dir() -> Path:
    """Where the built frontend is.

    The first candidate that actually holds an ``index.html`` wins. Resolved per
    call rather than cached at import: a dev server may build the frontend after
    this process started, and "not built yet" must not be latched into a constant.
    """
    override = os.environ.get("IRIS_WEB_DIST", "").strip()
    if override:
        return Path(override)
    for candidate in _DIST_CANDIDATES:
        if (candidate / "index.html").is_file():
            return candidate
    return _DIST_FALLBACK


class PluginMutationResponse(BaseModel):
    """What an install actually put on disk.

    ``extra="forbid"`` for the reason ``HealthResponse`` gives: the default drops
    unknown fields silently, so a typo in a field name would return a response that
    quietly omits it and leave the mutation untested. ``source_file`` is reported
    rather than the host path -- the page shows the name, and a name is enough to
    uninstall with. It is not necessarily the uploaded name: a ``.yml`` is stored
    under the rule's id, so that a second document with the same id cannot sit
    beside it under a different spelling.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    origin: str
    source_file: str


class PluginRemovalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The file name that was deleted, echoed so the caller can match it against what
    #: it listed -- a bare 200 would leave "which one went?" open when a page holds
    #: several plugins.
    removed: str


class InstanceStatsResponse(BaseModel):
    """One instance's resource reading, with the states spelled out.

    ``extra="forbid"`` for the reason ``HealthResponse`` gives: a typo in a field
    name would otherwise return a response that quietly omits it.

    ``state`` is what lets a client stop asking. Everything else on this model
    describes a live container; a client that has just stopped the instance cannot
    learn that from a 404, because a 404 is equally the answer for an address it
    typed wrong, and the honest reaction to those two is different.
    """

    model_config = ConfigDict(extra="forbid")

    iid: int
    container: str
    #: ``"running"`` or ``"gone"``. A closed vocabulary on purpose: the frontend
    #: branches on it, and a third state it does not know would render as "running".
    state: Literal["running", "gone"]
    sampled: bool
    cpu_pct: float | None
    mem_mb: float | None
    mem_limit_mb: float | None
    serial_port: int | None
    console_available: bool


class FirmwareRow(BaseModel):
    """One firmware's recorded runs, aggregated.

    ``extra="forbid"`` for the reason ``HealthResponse`` gives: a typo in a field
    name would otherwise return a response that quietly omits it, and the panel
    would render a blank cell instead of failing.

    ``arch`` is a closed vocabulary with two non-architecture values in it: ``mixed``
    for a firmware whose runs were measured as more than one architecture, and ``?``
    for one whose runs recorded none. Reporting a single architecture for either
    would invent an agreement the data does not contain.

    ``run_ids`` are the newest runs, truncated; ``run_ids_total`` is the real count,
    so a client can say "showing 50 of 812" instead of implying those were all of
    them.
    """

    model_config = ConfigDict(extra="forbid")

    image_id: int | None
    label: str
    arch: Literal["armel", "arm64", "mipseb", "mipsel", "mixed", "?"]
    target_type: str
    runs: int
    web_ok: int
    last_result_kind: str
    run_ids: list[int]
    run_ids_total: int
    run_ids_truncated: bool


class CorpusTotals(BaseModel):
    """The same numbers the stat cards read, so this panel cannot become a rival.

    Counted by ``run_stats`` rather than by summing the rows above: a total derived
    from the rows would agree with them by construction and therefore prove nothing
    when the two disagreed.
    """

    model_config = ConfigDict(extra="forbid")

    firmwares: int
    runs: int
    web_ok: int
    web_reach_rate: float


class CorpusViewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    firmwares: list[FirmwareRow]
    #: The runs no registered firmware owns, as one row. Not mixed into
    #: ``firmwares``: a wrong attribution corrupts every per-firmware number, and a
    #: missing one is visible.
    unattributed: FirmwareRow | None
    totals: CorpusTotals
    note: str


class ArchLatencyRow(BaseModel):
    """Latency for one architecture. Nullable figures mean "no sample", not zero."""

    model_config = ConfigDict(extra="forbid")

    arch: str
    samples: int
    min_sec: int | None
    median_sec: int | None
    p90_sec: int | None
    max_sec: int | None
    values: list[int]
    truncated: bool


class LatencyViewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    by_arch: list[ArchLatencyRow]
    #: Recorded runs that never reached a working web plane, and so have no duration
    #: at all. Counted because an absent bar would otherwise read as "no slow runs".
    unmeasured: int
    note: str


class FailureCell(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    count: int
    per_arch: dict[str, int]


class FailureStageRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str
    total: int
    cells: list[FailureCell]


class FailureMatrixResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stages: list[FailureStageRow]
    archs: list[str]
    kind_totals: dict[str, int]
    #: Failing signals whose stage resolves to nothing. Reported rather than dropped:
    #: a total that does not add up looks like a complete picture.
    unclassified: int
    note: str


class DiagnoseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iid: int


class DiagnosisResponse(BaseModel):
    """What one diagnosis produced, plus every fact about how it ran.

    ``llm_used`` false with a ``rule_recommendation`` is the rules engine having
    handled it for free; false with ``disabled_reason`` or ``error`` is nothing
    having run. The page shows all three differently, because a diagnosis panel
    that cannot tell them apart would report "no diagnosis" for a run the rules
    engine already diagnosed.
    """

    model_config = ConfigDict(extra="forbid")

    iid: int
    llm_used: bool
    decision: LLMDecision | None
    rule_recommendation: str | None
    disabled_reason: str | None
    error: str | None
    note: str


class AiStatusResponse(BaseModel):
    """Whether the LLM layer would run, with the key reported as configured or not.

    Never the key itself -- the same bargain ``/api/v1/config`` keeps: this feeds a
    page, and a page is the least controlled place a credential can end up.
    """

    model_config = ConfigDict(extra="forbid")

    #: ``disabled`` (nothing configured), ``ready`` (endpoint and model present) or
    #: ``unreachable`` (configured, but the settings' own sanity check failed).
    state: Literal["disabled", "ready", "unreachable"]
    base_url_configured: bool
    model: str
    note: str


class PluginDraftView(BaseModel):
    """One model-authored draft, with the provenance a review needs.

    ``yaml`` is the full document text: drafts are few, and a review that cannot
    read the document it is confirming is a rubber stamp.
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    stage: str
    description: str
    #: ``pending`` / ``accepted`` / ``rejected`` -- a closed vocabulary because the
    #: page renders a badge on it, and a fourth state it does not know would
    #: render as "pending".
    status: Literal["pending", "accepted", "rejected"]
    created_at: str
    source_iid: int | None
    confidence: float
    diagnosis: str
    yaml: str


class DraftListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    drafts: list[PluginDraftView]
    note: str


class DraftSaveRequest(BaseModel):
    """The body of "save this decision's draft", from the diagnosis panel.

    ``iid`` and ``confidence`` are optional because a draft saved by hand does not
    have a run behind it; the provenance a real diagnosis carries is what makes
    the promoted-repair record possible, so it travels when it exists.
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    stage: str
    description: str
    yaml: str
    iid: int | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    diagnosis: str = ""


class DraftMutationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    saved: str


class DraftInstallResponse(BaseModel):
    """What accepting a draft put on disk, and whether the repair got recorded.

    ``promoted_recorded`` is a separate fact from ``installed``: the engine
    accepting the document and the repair ledger gaining a
    ``source="llm", promoted=True`` row happen at the same moment only when the
    run that produced the draft still exists.
    """

    model_config = ConfigDict(extra="forbid")

    installed: PluginMutationResponse
    promoted_recorded: bool


class DraftRemovalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    removed: str


def install(app: FastAPI) -> FastAPI:
    """Attach the dashboard to ``app``. Safe to call more than once."""
    if getattr(app.state, _INSTALLED, False):
        return app
    setattr(app.state, _INSTALLED, True)
    _add_api_routes(app)
    app.add_api_websocket_route("/ws/terminal", terminal_endpoint)
    _add_spa(app)
    _chain_shutdown(app, shutdown)
    return app


def _chain_shutdown(app: FastAPI, handler) -> None:
    """Run ``handler`` after the app's own lifespan has finished shutting down.

    Composed onto the existing lifespan rather than replacing it: the base app's
    lifespan is where the database engine and anything else it owns get closed, and
    dropping it would be a change nobody asked for and nobody would notice until
    something leaked. Starlette 1.6 no longer exposes ``add_event_handler``, so this
    is also the only way to hang a handler off an app that was not constructed with
    a lifespan of its own.
    """
    previous = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(inner: FastAPI):
        async with previous(inner):
            yield
        await handler()

    app.router.lifespan_context = lifespan


def _session():
    # Imported lazily for the same reason ``iris.api.server`` does it: importing this
    # module must not open a database connection.
    from iris.api.server import _session as make

    return make()


def _add_api_routes(app: FastAPI) -> None:
    """Every dashboard read route.

    All of them take ``caller`` even where the body does not need it. The routes
    that do not use it say so with a leading underscore, which keeps the intent
    visible -- *this parameter exists to require a token* -- while leaving the
    dependency itself in place. The reason every one of them needs it: this is
    the same rule the seven existing API routes follow, and a read of the corpus
    (which firmware was tried, what its serial console printed, how it failed) is
    exactly the information the token exists to withhold. ``/api/v1/health`` stays
    open because a probe cannot carry a token.
    """

    @app.get("/api/v1/stats")
    async def read_stats(caller: Caller) -> dict:
        return web_data.stats(caller)

    @app.get("/api/v1/capabilities")
    async def read_capabilities(_caller: Caller) -> dict:
        return web_data.capabilities()

    @app.get("/api/v1/system")
    async def read_system(_caller: Caller) -> dict:
        """The live host reading behind the dashboard's top strip and the sidebar.

        Separate from `/api/v1/stats` because the two answer different questions
        with different lifetimes: stats answers "what happened", which is a record
        that survives a restart, and this answers "what is happening now", which
        stops being true the moment it is rendered. Merging them would have made
        the caching rule for one wrong for the other.
        """
        return host_metrics.system_reading()

    @app.get("/api/v1/stats/eval-set")
    async def read_eval_set(_caller: Caller) -> dict:
        return web_data.eval_set()

    @app.get("/api/v1/stats/corpus", response_model=CorpusViewResponse)
    async def read_corpus(_caller: Caller) -> CorpusViewResponse:
        """One row per firmware, so the corpus is readable at a glance."""
        return CorpusViewResponse(**web_data.corpus_view())

    @app.get("/api/v1/stats/latency", response_model=LatencyViewResponse)
    async def read_latency(_caller: Caller) -> LatencyViewResponse:
        """How long the runs that worked took. What it does not cover is in ``note``."""
        return LatencyViewResponse(**web_data.latency_view())

    @app.get("/api/v1/stats/failure-matrix", response_model=FailureMatrixResponse)
    async def read_failure_matrix(_caller: Caller) -> FailureMatrixResponse:
        """Failures as stage x architecture, on the dashboard's own counting rules."""
        return FailureMatrixResponse(**web_data.failure_matrix_view())

    @app.get("/api/v1/runs")
    async def read_runs(
        _caller: Caller,
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        arch: str = "",
        result_kind: str = "",
        query: str = "",
    ) -> dict:
        return web_data.runs_page(limit=limit, offset=offset, arch=arch,
                                  result_kind=result_kind, query=query)

    # Declared before ``{run_id}`` on purpose: a literal path segment loses to a
    # typed parameter segment when Starlette matches in declaration order, so the
    # CSV route below ``{run_id}`` was reachable only as ``/runs/export.csv/1``.
    @app.get("/api/v1/runs/export.csv")
    async def export_runs(_caller: Caller) -> PlainTextResponse:
        return PlainTextResponse(
            web_data.export_csv(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="iris-runs.csv"'},
        )

    @app.get("/api/v1/runs/{run_id}")
    async def read_run(run_id: int, _caller: Caller) -> dict:
        detail = web_data.run_detail(run_id)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")
        return detail

    @app.delete("/api/v1/runs/{run_id}")
    async def remove_run(run_id: int, caller: Caller) -> dict:
        """Erase one recorded run.

        404 for an id that is not there, which is deliberately the same answer the
        emulate routes give for "exists but is not yours": otherwise this route
        would turn into an oracle for probing which run ids exist.
        """
        if not web_data.delete_run_record(run_id):
            raise HTTPException(status_code=404, detail=f"run {run_id} not found")
        return {"removed": 1, "run_id": run_id}

    @app.delete("/api/v1/runs")
    async def remove_runs(caller: Caller) -> dict:
        """Erase the whole recorded history.

        Counted rather than a boolean, because "there was nothing to remove" and
        "the delete failed" are different states and a caller cannot tell them
        apart from a bare success code.
        """
        return web_data.clear_run_records()

    @app.get("/api/v1/rules")
    async def read_rules(_caller: Caller) -> dict:
        return web_data.rule_plugins()

    @app.post("/api/v1/plugins", response_model=PluginMutationResponse)
    async def upload_plugin(
        _caller: Caller,
        file: UploadFile = File(..., description="a rule document (.yaml or .yml)"),
    ) -> PluginMutationResponse:
        """Install an externally authored rule plugin.

        The body is read through the same bounded reader the firmware upload uses, so
        an oversized document is cut off at the cap instead of being buffered whole.

        Rejections come back as 422 with the reason in Chinese, written for the plugin
        author rather than for the log: whatever the engine would object to on load
        comes back verbatim, because "上传成功但规则不生效" has no other useful answer.
        """
        content = await _read_upload(file, MAX_PLUGIN_BYTES)
        try:
            return PluginMutationResponse(**install_plugin(file.filename or "", content))
        except PluginRejected as exc:
            raise HTTPException(status_code=422, detail=exc.detail) from exc

    @app.delete("/api/v1/plugins/{name}", response_model=PluginRemovalResponse)
    async def uninstall_plugin(name: str, _caller: Caller) -> PluginRemovalResponse:
        """Remove one installed plugin by file name.

        404 rather than 422 for "no such file": the path is the caller's own upload,
        so a stale page is the likely cause and the browser should re-fetch rather than
        retry the delete.
        """
        try:
            removed = remove_plugin(name)
        except PluginRejected as exc:
            raise HTTPException(status_code=404, detail=exc.detail) from exc
        return PluginRemovalResponse(removed=removed.name)

    @app.get("/api/v1/console/{iid}")
    async def read_console(iid: int, _caller: Caller, start_line: int = Query(0, ge=0),
                           max_lines: int = Query(2000, ge=1, le=20000)) -> dict:
        return web_data.serial_log(iid, start_line=start_line, max_lines=max_lines)

    @app.get("/api/v1/instances/{iid}/stats", response_model=InstanceStatsResponse)
    async def read_instance_stats(iid: int, caller: Caller) -> InstanceStatsResponse:
        """Resource use for one instance, and whether it is still there.

        Three answers rather than two. An instance the caller owns gets its reading;
        one that has been stopped gets ``200`` with ``state="gone"``, so a panel left
        open behind a stop renders an honest empty state instead of a retry loop
        against an answer that can never change; one that belongs to somebody else
        still gets 404, because for *that* id the caller's guess is wrong and saying
        so is the whole point of the ownership model.
        """
        state = live_state(caller, iid)
        if state == "not-yours":
            raise HTTPException(status_code=404, detail=f"emulation {iid} not found")
        return InstanceStatsResponse(**web_data.instance_stats(iid, running=state == "owned"))

    @app.get("/api/v1/knowledge/root-cause")
    async def read_root_causes(_caller: Caller, recent: int = Query(10, ge=1, le=100)) -> dict:
        return web_data.root_causes(recent=recent)

    @app.get("/api/v1/config")
    async def read_config(_caller: Caller) -> dict:
        """Effective settings, read-only, with the token reported as present or not.

        Never the token itself: this endpoint feeds a page, and a page is the least
        controlled place a credential can end up. The dashboard points at ``.env``,
        which is where this project already reads configuration from.
        """
        settings = get_settings()
        return {
            "database_url": redact_database_url(settings.database_url),
            "scratch_dir": str(settings.scratch_dir),
            "api_max_upload_mb": settings.api_max_upload_mb,
            "api_token_configured": bool(settings.api_token.strip()),
            "read_only": True,
            "note": "配置来自 .env 与 IRIS_* 环境变量，修改后需重启 iris web",
        }

    # ------------------------------------------------------------------ ai layer
    #
    # The diagnosis is the model's one round trip, and everything else here is
    # bookkeeping around it. None of these routes executes anything on its own:
    # the outcome is a decision, the restart suggestion is performed by a person
    # on the instance page, and a draft only reaches the plugin directory when a
    # person posts the install below.

    @app.get("/api/v1/ai/status", response_model=AiStatusResponse)
    async def read_ai_status(_caller: Caller) -> AiStatusResponse:
        """Whether the LLM layer would run, never the key itself."""
        settings = get_settings()
        if settings.llm_enabled:
            state, note = "ready", (
                f"模型 {settings.llm_model} 已配置;诊断在实例页手动触发,"
                "草案在插件页审核安装"
            )
        elif not settings.llm_base_url.strip():
            state, note = "disabled", (
                "LLM 层未配置;确定性规则自愈不受影响。"
                "配置 IRIS_LLM_BASE_URL 与 IRIS_LLM_MODEL 后启用"
            )
        else:
            state, note = "unreachable", (
                "已配置 base_url 但缺少模型名;两者都配置后该层才可用"
            )
        return AiStatusResponse(
            state=state,
            base_url_configured=bool(settings.llm_base_url.strip()),
            model=settings.llm_model,
            note=note,
        )

    @app.post("/api/v1/ai/diagnose", response_model=DiagnosisResponse)
    async def diagnose(_caller: Caller, req: DiagnoseRequest) -> DiagnosisResponse:
        """One diagnosis: rule engine first, the model only for the long tail.

        Off the event loop because the round trip is a blocking HTTP call with a
        minute-scale timeout -- a diagnosis that blocked the loop would freeze
        every other route on a slow endpoint, which is the one way this feature
        could take the workbench down with it.
        """
        outcome = await asyncio.to_thread(diagnose_instance, req.iid)
        return DiagnosisResponse(
            iid=outcome.iid,
            llm_used=outcome.llm_used,
            decision=outcome.decision,
            rule_recommendation=outcome.rule_recommendation,
            disabled_reason=outcome.disabled_reason,
            error=outcome.error,
            note=outcome.note,
        )

    @app.get("/api/v1/ai/drafts", response_model=DraftListResponse)
    async def read_drafts(_caller: Caller) -> DraftListResponse:
        drafts = list_drafts()
        return DraftListResponse(
            drafts=[_draft_view(draft) for draft in drafts],
            note=(
                "模型生成的规则草案;仅在此列出,不会进引擎加载路径,"
                "由人工确认安装(repair_action 记 source=llm 且 promoted)"
            ),
        )

    @app.post("/api/v1/ai/drafts", response_model=DraftMutationResponse)
    async def save_one_draft(_caller: Caller, req: DraftSaveRequest) -> DraftMutationResponse:
        """Put one decision's draft into the draft directory. Nothing is loaded."""
        try:
            record = save_draft(
                PluginDraft(
                    rule_id=req.rule_id, stage=req.stage,
                    description=req.description, yaml=req.yaml,
                ),
                iid=req.iid, confidence=req.confidence, diagnosis=req.diagnosis,
            )
        except PluginRejected as exc:
            raise HTTPException(status_code=422, detail=exc.detail) from exc
        return DraftMutationResponse(saved=record.rule_id)

    # Drafts are addressed by id, so the install and the removal are declared
    # after the static list/save above -- the same declaration-order rule the
    # runs routes follow.
    @app.post("/api/v1/ai/drafts/{rule_id}/install", response_model=DraftInstallResponse)
    async def install_one_draft(rule_id: str, _caller: Caller) -> DraftInstallResponse:
        """Accept one draft through the same validation chain as a browser upload.

        A draft the engine would reject from a person is rejected from a model
        too, and the reason comes back verbatim: "安装成功但规则不生效" has no
        other useful answer.
        """
        try:
            result = install_draft(rule_id)
        except PluginRejected as exc:
            raise HTTPException(status_code=422, detail=exc.detail) from exc
        return DraftInstallResponse(
            installed=PluginMutationResponse(**result["installed"]),
            promoted_recorded=result["promoted_recorded"],
        )

    @app.delete("/api/v1/ai/drafts/{rule_id}", response_model=DraftRemovalResponse)
    async def reject_one_draft(rule_id: str, _caller: Caller) -> DraftRemovalResponse:
        """Refuse one draft. 404 for a stale page, not a retry."""
        try:
            removed = reject_draft(rule_id)
        except PluginRejected as exc:
            raise HTTPException(status_code=404, detail=exc.detail) from exc
        return DraftRemovalResponse(removed=removed.name)


def redact_database_url(url: str) -> str:
    """A database URL with its password replaced."""
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    credentials, _, host = rest.rpartition("@")
    if ":" not in credentials:
        return f"{scheme}://{rest}"
    user, _, _password = credentials.partition(":")
    return f"{scheme}://{user}:***@{host}"


def require_owned(caller: str, iid: int) -> None:
    """404 for anything this caller does not own -- the same rule as the emulate routes.

    Not 403: telling a caller that an id exists but belongs to someone else is the
    one piece of information the ownership model exists to withhold.
    """
    if live_state(caller, iid) != "owned":
        raise HTTPException(status_code=404, detail=f"emulation {iid} not found")


def _draft_view(record: DraftRecord) -> dict:
    """One draft row for the list endpoint, document text included.

    Reads the document rather than trusting the metadata: the review confirms a
    YAML the engine will one day load, and the text on disk is the thing it will
    load -- not whatever the model said about it.
    """
    from iris.llm.drafts import drafts_dir

    text = (drafts_dir() / f"{record.rule_id}.yaml").read_text(encoding="utf-8")
    return {
        "rule_id": record.rule_id,
        "stage": record.stage,
        "description": record.description,
        "status": record.status,
        "created_at": record.created_at,
        "source_iid": record.source_iid,
        "confidence": record.confidence,
        "diagnosis": record.diagnosis,
        "yaml": text,
    }


#: What a lookup of one instance id can conclude. ``owned`` and ``not-yours`` are the
#: two halves of :func:`require_owned`, which reports them identically; ``gone`` is the
#: third answer, kept separate for the one caller -- a panel polling a live resource
#: -- that has to tell "you stopped this" from "that id was never yours".
LiveState = Literal["owned", "not-yours", "gone"]


def live_state(caller: str, iid: int) -> LiveState:
    """Whether a *running* instance under ``iid`` belongs to ``caller``.

    503 rather than a silent "no" when the table cannot be read: answering a probe
    that never reached the database would retire the panel of an instance that is
    running perfectly well, and the user would see it as the instance having stopped.
    """
    try:
        with _session() as session:
            record = get_live(session, iid)
    except Exception as exc:
        logger.warning(f"could not verify ownership of instance {iid}: {exc}")
        raise HTTPException(status_code=503,
                            detail="active emulation table unavailable") from exc
    if record is None:
        return "gone"
    return "owned" if record.client_id == caller else "not-yours"


def _add_spa(app: FastAPI) -> None:
    """Serve the built frontend, falling back to index.html for client-side routes.

    Registered last on purpose: FastAPI matches in registration order, so every API
    route above is considered before this catch-all. A fallback that answered unknown
    ``/api/v1/...`` paths with index.html would turn a typo into a page of HTML and a
    200 -- the hardest kind of API mistake to notice.

    The dist directory is resolved per request rather than captured here. Capturing
    it would freeze the answer at install time, which is indistinguishable from "the
    frontend does not exist" for anything that builds afterwards -- a dev server
    writing into ``web/dist``, or a test pointing the dashboard at a fixture.
    """
    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="no such API route")
        dist = dist_dir()
        index = dist / "index.html"
        if not index.is_file():
            return JSONResponse(status_code=503, content={
                "detail": "frontend has not been built",
                "expected": str(index),
                "hint": "cd web && npm install && npm run build",
            })
        candidate = safe_join(dist, full_path)
        if candidate is not None and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)


def safe_join(root: Path, relative: str) -> Path | None:
    """Resolve ``relative`` under ``root``, or None if it tries to leave.

    Without this the catch-all would serve the filesystem: ``GET /../../...`` is a
    user-supplied path matched by a route that matches everything.
    """
    if not relative:
        return None
    try:
        candidate = (root / relative).resolve()
        candidate.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return candidate


async def shutdown() -> None:
    """Tell the consoles, close them, then stop every instance this process hosts.

    Order matters. The consoles go first so a browser attached to a container is told
    the server is going away instead of watching its socket stop resolving, and so the
    ``docker rm`` below is not racing websocket pumps that are still reading from it.
    Every instance is attempted even if one fails: a shutdown that gives up halfway
    leaves privileged containers running with nobody watching.
    """
    from iris.db.active import reconcile

    try:
        with _session() as session:
            rows = list(session.scalars(select(ActiveEmulation)))
    except Exception as exc:  # noqa: BLE001 - shutdown must not raise
        logger.warning(f"could not read active emulations during shutdown: {exc}")
        rows = []

    await notify_shutdown()
    await close_all_bridges()

    for row in rows:
        iid = row.iid
        try:
            stopped = await asyncio.to_thread(stop_emulation, iid)
            logger.info(f"stopped instance {iid} on shutdown: {stopped}")
        except Exception as exc:  # noqa: BLE001 - one bad row must not skip the rest
            logger.warning(f"could not stop instance {iid} during shutdown: {exc}")
        try:
            with _session() as session:
                release(session, iid=iid, client_id=row.client_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"could not release instance {iid} from the active table: {exc}")

    try:
        with _session() as session:
            reconcile(session)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"could not reconcile after shutdown: {exc}")