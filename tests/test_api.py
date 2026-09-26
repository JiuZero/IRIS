"""Tests for L5 FastAPI orchestration API."""

from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient

from iris.api.server import app

client = TestClient(app)


def test_health() -> None:
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"
    assert data["active_emulations"] == 0


def test_list_firmware() -> None:
    resp = client.get("/api/v1/firmware")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


def test_list_emulations_empty() -> None:
    resp = client.get("/api/v1/emulate")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_emulation_not_found() -> None:
    resp = client.get("/api/v1/emulate/99999")
    assert resp.status_code == 404


def test_stop_emulation_not_found() -> None:
    resp = client.delete("/api/v1/emulate/99999")
    assert resp.status_code == 200
    assert "stopped" in resp.json()
    assert "iid" in resp.json()


def test_emulate_invalid_arch() -> None:
    resp = client.post(
        "/api/v1/emulate",
        json={"rootfs_path": "/tmp", "arch": "x86", "iid": 1, "port": 8080, "timeout": 10},
    )
    assert resp.status_code == 400


def test_emulate_rootfs_not_found() -> None:
    resp = client.post(
        "/api/v1/emulate",
        json={"rootfs_path": "/nonexistent/path", "arch": "mipsel", "iid": 1, "port": 8080, "timeout": 10},
    )
    assert resp.status_code == 404


def test_openapi_docs() -> None:
    resp = client.get("/docs")
    assert resp.status_code == 200


def test_pipeline_rejects_zip() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("rootfs.bin", b"\x00" * 64)
    resp = client.post(
        "/api/v1/pipeline",
        files={"firmware": ("fw.zip", buf.getvalue(), "application/zip")},
    )
    assert resp.status_code == 415
    assert ".bin" in resp.json()["detail"]