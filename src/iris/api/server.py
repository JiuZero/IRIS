"""L5 orchestration API — FastAPI service for batch firmware emulation.

Three things changed when this became something other than a local convenience
script, and each one is enforced here rather than in documentation:

* **Authentication.** Every route below except ``/api/v1/health`` requires the
  shared token (:mod:`iris.api.auth`). Without one the server runs in local mode,
  which is only reachable because ``iris serve start`` refuses to bind a
  non-loopback address in that case.
* **Ownership.** Emulations live in ``active_emulation`` with the client that
  created them, not in a module-level dict. A run survives a restart, and no
  caller can read or stop somebody else's run -- both were impossible with a
  dict, which also meant two workers disagreed about what was running.
* **Bounded input.** ``/api/v1/pipeline`` streams the upload and aborts past a
  configured cap instead of ``firmware.read()``-ing an arbitrary body into
  memory.

``/api/v1/health`` stays unauthenticated on purpose: a liveness probe cannot
carry a token. It therefore reports only what a probe needs.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from iris import __version__
from iris.api.auth import require_client
from iris.arch import normalize_arch
from iris.config import get_settings
from iris.db.active import list_owned, reconcile, register, release
from iris.db.engine import get_engine, init_db, make_session
from iris.emulate.linkprobe import LayerState
from iris.emulate.orchestrator import emulate_firmware, preflight_arch, stop_emulation
from iris.emulate.qemu_config import supported_archs
from iris.extract.arch import identify_elf
from iris.fsutil import safe_is_dir, safe_present
from iris.log import get_logger

logger = get_logger(__name__)

_SUPPORTED_ARCHS = tuple(supported_archs())

app = FastAPI(
    title="IRIS — IoT Rehosting & Interconnection Simulator",
    version=__version__,
    description="Automated firmware rehosting platform for network devices",
)

#: Read in chunks so the cap is enforced while the body arrives. 1 MiB keeps the
#: number of iterations low for a 64 MiB default without ever holding two copies.
_UPLOAD_CHUNK = 1024 * 1024


class EmulateRequest(BaseModel):
    rootfs_path: str = Field(..., description="path to extracted rootfs directory")
    arch: str = Field(..., description="target architecture (mipsel/mipseb/armel)")
    iid: int = Field(0, description="image ID (auto-assigned if 0)")
    port: int = Field(8080, description="host port for web access")
    timeout: int = Field(120, description="boot timeout in seconds")



class LinkLayerView(BaseModel):
    """One row of the layered link table.

    ``state`` is the closed :class:`~iris.emulate.linkprobe.LayerState` enum
    rather than a string: ``unknown`` means the probe could not run, which is not
    the same claim as ``blocked``, and a client that cannot tell them apart will
    page someone about a network that was never measured.
    """

    layer: str
    state: LayerState
    detail: str = ""


class LinkProfileView(BaseModel):
    guest_ip: str
    first_break: str = ""
    unavailable: str = ""
    layers: list[LinkLayerView] = Field(default_factory=list)


class EmulateResponse(BaseModel):
    iid: int
    success: bool
    web_ok: bool
    web_url: str
    duration_sec: float
    error: str
    container_id: str
    #: ``None`` when the run succeeded, or when the probe could not run at all.
    #: Present on the failure path because that is the only path where it says
    #: anything: it names the layer a packet stopped at.
    link: LinkProfileView | None = None


class FirmwareInfo(BaseModel):
    name: str
    path: str
    arch: str


class HealthResponse(BaseModel):
    #: ``extra="forbid"`` because the default silently drops unknown fields.
    #: With the default, re-adding ``active_emulations`` produced a response that
    #: simply did not contain it, so the mutation stayed green and nothing
    #: constrained this model's shape from the outside.
    model_config = ConfigDict(extra="forbid")

    status: str
    version: str


#: Every stateful route depends on this. It returns the caller's client id, which
#: is what ownership is checked against.
Caller = Annotated[str, Depends(require_client)]


#: Engines cached per database URL. Building an ``Engine`` per request would
#: open a fresh connection pool and re-run ``create_all`` on every call; keying
#: by URL rather than keeping one global also means a caller that repoints
#: ``database_url`` gets its own pool instead of the real database's.
_ENGINES: dict[str, Any] = {}


def _session():
    """A session against the configured database, creating the schema on first use.

    Created per request rather than at import time: importing this module must
    not touch the filesystem, because the tests import it and the CLI imports it
    for ``serve start`` without ever serving.
    """
    url = get_settings().database_url
    engine = _ENGINES.get(url)
    if engine is None:
        engine = get_engine(url)
        init_db(engine)
        _ENGINES[url] = engine
    return make_session(engine)


@app.get("/api/v1/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness probe. Unauthenticated on purpose -- a probe cannot carry a token.

    Reports liveness and the version only. The emulation count this used to
    return was unauthenticated information about how much work the host is doing,
    and it moved to ``/api/v1/emulate``, which is authenticated.
    """
    return HealthResponse(status="ok", version=__version__)


async def _read_upload(firmware: UploadFile, limit_bytes: int) -> bytes:
    """Read the upload, refusing to go past ``limit_bytes``.

    Streaming rather than ``firmware.read()``: the whole point is that a body
    larger than the cap never gets to exist in memory. The client is disconnected
    from as soon as the cap is passed, so an oversized upload costs the bytes
    already transferred and nothing more.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await firmware.read(_UPLOAD_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > limit_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"upload exceeds the {limit_bytes // (1024 * 1024)} MiB limit",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@app.get("/api/v1/firmware", response_model=list[FirmwareInfo])
async def list_firmware(caller: Caller) -> list[FirmwareInfo]:
    settings = get_settings()
    scratch = settings.scratch_dir
    result = []
    if scratch.exists():
        for d in sorted(scratch.iterdir()):
            if d.is_dir() and d.name.endswith("-rootfs"):
                arch = "unknown"
                bin_dir = d / "bin"
                if safe_is_dir(bin_dir):
                    for f in bin_dir.iterdir():
                        try:
                            if not f.is_file() or f.is_symlink():
                                continue
                            data = f.read_bytes()[:20]
                        except OSError:
                            continue
                        # The same census every other layer uses. This used to be
                        # a fourth copy of the e_machine switch, and it read the
                        # field with a hardcoded "little" regardless of EI_DATA --
                        # so a big-endian MIPS binary decoded e_machine 8 into
                        # 2048 and fell through to the `unk` case.
                        info = identify_elf(data)
                        if info is not None:
                            arch = info.arch
                            break
                result.append(FirmwareInfo(name=d.name, path=str(d), arch=arch))
    return result


@app.post("/api/v1/emulate", response_model=EmulateResponse)
async def emulate(req: EmulateRequest, caller: Caller) -> EmulateResponse:
    rootfs = Path(req.rootfs_path)
    if not safe_is_dir(rootfs):
        raise HTTPException(status_code=404, detail=f"rootfs not found: {req.rootfs_path}")

    # A caller that read the architecture off an ELF header sends `aarch64`; the
    # kernel asset is `arm64`. Rejecting that spelling with "unsupported arch"
    # pointed at the wrong thing -- the architecture was supported, the string was
    # not translated.
    arch = normalize_arch(req.arch)
    if arch not in _SUPPORTED_ARCHS:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported arch: {req.arch} (supported: {', '.join(_SUPPORTED_ARCHS)})",
        )

    problem = await asyncio.to_thread(preflight_arch, rootfs, arch)
    if problem:
        raise HTTPException(status_code=400, detail=f"preflight: {problem.message}")

    iid = req.iid if req.iid > 0 else int(hashlib.md5(str(rootfs.resolve()).encode()).hexdigest(), 16) % 10000

    settings = get_settings()
    scratch = settings.scratch_dir

    result = await asyncio.to_thread(
        emulate_firmware,
        rootfs_dir=rootfs,
        arch=arch,
        iid=iid,
        scratch_dir=scratch,
        host_port=req.port,
        timeout_sec=req.timeout,
    )

    _remember(
        iid=iid,
        caller=caller,
        arch=arch,
        rootfs_path=rootfs,
        result=result,
    )

    return EmulateResponse(
        iid=iid,
        success=result.success,
        web_ok=result.web_ok,
        web_url=result.web_url or "-",
        duration_sec=result.duration_sec,
        error=result.error or "",
        container_id=result.container_id,
        link=_link_view(result.link_profile),
    )


def _link_view(profile: Any) -> LinkProfileView | None:
    """The layered link table, shaped for a client. ``None`` when there is none."""
    if profile is None:
        return None
    return LinkProfileView(
        guest_ip=profile.guest_ip,
        first_break=str(profile.first_break) if profile.first_break else "",
        unavailable=profile.unavailable,
        layers=[
            LinkLayerView(layer=p.layer.value, state=p.state.value, detail=p.detail)
            for p in profile.probes
        ],
    )


def _remember(*, iid: int, caller: str, arch: str, rootfs_path: Path, result: Any) -> None:
    """Record the run as hosted by ``caller``. Best-effort, like run recording.

    A database that cannot be reached must not turn a finished emulation into an
    error response -- but it must say so, because that is exactly the state in
    which a caller cannot later stop their own container.
    """
    try:
        with _session() as session:
            register(
                session,
                iid=iid,
                client_id=caller,
                arch=arch,
                rootfs_path=str(rootfs_path),
                container_id=result.container_id,
                web_url=result.web_url or "",
                success=result.success,
                web_ok=result.web_ok,
            )
    except Exception as exc:  # noqa: BLE001 - bookkeeping must not fail a run
        logger.warning(f"emulation {iid} was not registered as active: {exc}")


def _drop_stale(client_id: str) -> None:
    """Reconcile before listing, so a restart does not report dead containers."""
    try:
        with _session() as session:
            reconcile(session)
    except Exception as exc:  # noqa: BLE001 - listing must not fail on this
        logger.warning(f"could not reconcile stale emulations: {exc}")


@app.get("/api/v1/emulate", response_model=list[dict])
async def list_emulations(caller: Caller) -> list[dict]:
    """Only the caller's own emulations. There is no way to ask for others'."""
    _drop_stale(caller)
    try:
        with _session() as session:
            rows = list_owned(session, caller)
    except Exception as exc:  # noqa: BLE001 - an empty list beats a 500 here
        logger.warning(f"could not read active emulations: {exc}")
        return []
    return [vars(record) for record in rows]


@app.get("/api/v1/emulate/{iid}", response_model=dict)
async def get_emulation(iid: int, caller: Caller) -> dict:
    try:
        with _session() as session:
            rows = [r for r in list_owned(session, caller) if r.iid == iid]
    except Exception as exc:
        logger.warning(f"could not read emulation {iid}: {exc}")
        raise HTTPException(status_code=503, detail="active emulation table unavailable") from exc
    if not rows:
        # 404 for "not yours" as well as "not there": saying which would tell a
        # caller that somebody else's id exists.
        raise HTTPException(status_code=404, detail=f"emulation {iid} not found")
    return vars(rows[0])


@app.delete("/api/v1/emulate/{iid}")
async def stop_emulation_api(iid: int, caller: Caller) -> dict:
    try:
        with _session() as session:
            record = release(session, iid=iid, client_id=caller)
    except Exception as exc:
        logger.warning(f"could not release emulation {iid}: {exc}")
        raise HTTPException(status_code=503, detail="active emulation table unavailable") from exc
    if record is None:
        raise HTTPException(status_code=404, detail=f"emulation {iid} not found")
    ok = stop_emulation(iid)
    return {"stopped": ok, "iid": iid}


class PipelineResponse(BaseModel):
    iid: int
    firmware_name: str
    arch: str
    rootfs_path: str
    success: bool
    web_ok: bool
    web_url: str
    duration_sec: float
    error: str


@app.post("/api/v1/pipeline", response_model=PipelineResponse)
async def pipeline(
    caller: Caller,
    firmware: UploadFile = File(..., description="Firmware binary file"),
    arch: str = "",
    port: int = 8080,
    timeout: int = 120,
) -> PipelineResponse:
    """End-to-end pipeline: upload firmware → extract rootfs → emulate → web access."""
    from iris.extract.firmware import analyze_firmware
    from iris.extract.rootfs_extract import extract_rootfs

    settings = get_settings()
    scratch = settings.scratch_dir

    limit_bytes = max(1, settings.api_max_upload_mb) * 1024 * 1024
    content = await _read_upload(firmware, limit_bytes)
    safe_name = Path(firmware.filename or "firmware.bin").name
    if safe_name.lower().endswith(".zip") or content[:4] == b"PK\x03\x04":
        raise HTTPException(
            status_code=415,
            detail="zip containers are not accepted (unpredictable internal layout); "
            "unpack the upgrade package and upload the firmware .bin",
        )
    fw_hash = hashlib.md5(content).hexdigest()[:8]
    iid = int(fw_hash, 16) % 10000

    fw_path = scratch / f"upload-{iid}-{safe_name}"
    fw_path.parent.mkdir(parents=True, exist_ok=True)
    fw_path.write_bytes(content)

    info = await asyncio.to_thread(analyze_firmware, content)
    detected_arch = arch or info.arch or ""

    if not detected_arch or detected_arch not in _SUPPORTED_ARCHS:
        return PipelineResponse(
            iid=iid,
            firmware_name=safe_name,
            arch=detected_arch or "unknown",
            rootfs_path="",
            success=False,
            web_ok=False,
            web_url="-",
            duration_sec=0.0,
            error=f"unsupported-arch: '{detected_arch or 'unknown'}' not in supported {list(_SUPPORTED_ARCHS)}",
        )

    # An extraction failure is an expected outcome for unsupported containers, and a
    # WinError-1920 remnant in the scratch tree is a filesystem quirk rather than a
    # server fault — neither deserves a bare 500.
    try:
        ext = await asyncio.to_thread(lambda: extract_rootfs(fw_path, scratch, arch_hint=detected_arch))
    except (RuntimeError, OSError) as exc:
        return PipelineResponse(
            iid=iid,
            firmware_name=safe_name,
            arch=detected_arch,
            rootfs_path="",
            success=False,
            web_ok=False,
            web_url="-",
            duration_sec=0.0,
            error=f"rootfs extraction error: {exc}",
        )
    rootfs_dir = scratch / f"{fw_path.stem}-rootfs"

    if not safe_present(rootfs_dir):
        return PipelineResponse(
            iid=iid,
            firmware_name=safe_name,
            arch=detected_arch,
            rootfs_path="",
            success=False,
            web_ok=False,
            web_url="-",
            duration_sec=0.0,
            error=f"rootfs extraction failed: "
                   f"{ext.failure_reason or 'no rootfs directory created'}",
        )

    problem = await asyncio.to_thread(preflight_arch, rootfs_dir, detected_arch)
    if problem:
        return PipelineResponse(
            iid=iid,
            firmware_name=safe_name,
            arch=detected_arch,
            rootfs_path=str(rootfs_dir),
            success=False,
            web_ok=False,
            web_url="-",
            duration_sec=0.0,
            error=f"preflight: {problem.message}",
        )

    result = await asyncio.to_thread(
        emulate_firmware,
        rootfs_dir=rootfs_dir,
        arch=detected_arch,
        iid=iid,
        scratch_dir=scratch,
        host_port=port,
        timeout_sec=timeout,
    )

    _remember(
        iid=iid,
        caller=caller,
        arch=detected_arch,
        rootfs_path=rootfs_dir,
        result=result,
    )

    return PipelineResponse(
        iid=iid,
        firmware_name=safe_name,
        arch=detected_arch,
        rootfs_path=str(rootfs_dir),
        success=result.success,
        web_ok=result.web_ok,
        web_url=result.web_url or "-",
        duration_sec=result.duration_sec,
        error=result.error or "",
    )
