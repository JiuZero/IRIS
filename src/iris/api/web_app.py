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

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from sqlalchemy import select

from iris.api import web_data
from iris.api.server import Caller
from iris.api.web_terminal import close_all_bridges, notify_shutdown, terminal_endpoint
from iris.config import get_settings
from iris.db.active import list_owned, release
from iris.db.models import ActiveEmulation
from iris.emulate.orchestrator import stop_emulation
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

    @app.get("/api/v1/stats/eval-set")
    async def read_eval_set(_caller: Caller) -> dict:
        return web_data.eval_set()

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

    @app.get("/api/v1/console/{iid}")
    async def read_console(iid: int, _caller: Caller, start_line: int = Query(0, ge=0),
                           max_lines: int = Query(2000, ge=1, le=20000)) -> dict:
        return web_data.serial_log(iid, start_line=start_line, max_lines=max_lines)

    @app.get("/api/v1/instances/{iid}/stats")
    async def read_instance_stats(iid: int, caller: Caller) -> dict:
        require_owned(caller, iid)
        return web_data.instance_stats(iid)

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
    try:
        with _session() as session:
            mine = [record for record in list_owned(session, caller) if record.iid == iid]
    except Exception as exc:
        logger.warning(f"could not verify ownership of instance {iid}: {exc}")
        raise HTTPException(status_code=503,
                            detail="active emulation table unavailable") from exc
    if not mine:
        raise HTTPException(status_code=404, detail=f"emulation {iid} not found")


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