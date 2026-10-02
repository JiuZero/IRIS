"""L5 orchestration API — FastAPI service for batch firmware emulation."""

from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from iris.arch import normalize_arch
from iris.config import get_settings
from iris.emulate.orchestrator import emulate_firmware, preflight_arch, stop_emulation
from iris.emulate.qemu_config import supported_archs
from iris.extract.arch import identify_elf
from iris.fsutil import safe_is_dir, safe_present

_SUPPORTED_ARCHS = tuple(supported_archs())

app = FastAPI(
    title="IRIS — IoT Rehosting & Interconnection Simulator",
    version="0.1.0",
    description="Automated firmware rehosting platform for network devices",
)

_active_emulations: dict[int, dict[str, Any]] = {}


class EmulateRequest(BaseModel):
    rootfs_path: str = Field(..., description="path to extracted rootfs directory")
    arch: str = Field(..., description="target architecture (mipsel/mipseb/armel)")
    iid: int = Field(0, description="image ID (auto-assigned if 0)")
    port: int = Field(8080, description="host port for web access")
    timeout: int = Field(120, description="boot timeout in seconds")


class EmulateResponse(BaseModel):
    iid: int
    success: bool
    web_ok: bool
    web_url: str
    duration_sec: float
    error: str
    container_id: str


class FirmwareInfo(BaseModel):
    name: str
    path: str
    arch: str


class HealthResponse(BaseModel):
    status: str
    version: str
    active_emulations: int


@app.get("/api/v1/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version="0.1.0", active_emulations=len(_active_emulations))


@app.get("/api/v1/firmware", response_model=list[FirmwareInfo])
async def list_firmware() -> list[FirmwareInfo]:
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
async def emulate(req: EmulateRequest) -> EmulateResponse:
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

    _active_emulations[iid] = {
        "iid": iid,
        "arch": arch,
        "success": result.success,
        "web_ok": result.web_ok,
        "web_url": result.web_url,
        "container_id": result.container_id,
        "started_at": time.time(),
    }

    return EmulateResponse(
        iid=iid,
        success=result.success,
        web_ok=result.web_ok,
        web_url=result.web_url or "-",
        duration_sec=result.duration_sec,
        error=result.error or "",
        container_id=result.container_id,
    )


@app.get("/api/v1/emulate", response_model=list[dict])
async def list_emulations() -> list[dict]:
    return list(_active_emulations.values())


@app.get("/api/v1/emulate/{iid}", response_model=dict)
async def get_emulation(iid: int) -> dict:
    if iid not in _active_emulations:
        raise HTTPException(status_code=404, detail=f"emulation {iid} not found")
    return _active_emulations[iid]


@app.delete("/api/v1/emulate/{iid}")
async def stop_emulation_api(iid: int) -> dict:
    ok = stop_emulation(iid)
    _active_emulations.pop(iid, None)
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

    content = await firmware.read()
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

    _active_emulations[iid] = {
        "iid": iid,
        "arch": detected_arch,
        "success": result.success,
        "web_ok": result.web_ok,
        "web_url": result.web_url,
        "container_id": result.container_id,
        "started_at": time.time(),
    }

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
