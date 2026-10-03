"""Tests for L5 FastAPI orchestration API."""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from iris.api.server import app

#: These cases are about the endpoint contracts that did not change (status
#: codes, error shapes). Authentication, ownership and the upload cap have their
#: own file, ``test_api_security.py``, because they need an isolated database and
#: a configurable token.
client = TestClient(app)


def test_health() -> None:
    from iris import __version__

    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == __version__


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
    """An iid with no hosted run is a 404, not a 200 with a failed delete.

    It used to answer 200 and call ``docker rm -f`` first, which meant "stop
    something you do not own" was indistinguishable from "stop nothing" -- and
    the delete could land on another caller's container.
    """
    resp = client.delete("/api/v1/emulate/99999")
    assert resp.status_code == 404


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

class TestUnresolvableRootfsEntries:
    """A dead reparse point must not become a 500.

    A firmware rootfs is full of ``/sbin -> /bin`` style links. Recreated on the
    host they raise ``WinError 1920`` from ``stat()``, which used to escape the
    pipeline endpoint as an unhandled exception instead of the ordinary
    "extraction failed" verdict every other unsupported image gets.
    """

    @pytest.fixture
    def dead_links(self, monkeypatch, tmp_path):
        from pathlib import Path

        scratch = tmp_path / "scratch"
        (scratch / "fw-rootfs" / "sbin").mkdir(parents=True)
        from iris.config import Settings

        # A real Settings, not a hand-rolled stand-in: the pipeline endpoint reads
        # ``api_max_upload_mb`` off it, and a stub missing that field fails with
        # AttributeError instead of testing the behaviour.
        monkeypatch.setattr("iris.api.server.get_settings", lambda: Settings(
            scratch_dir=scratch, iris_home=str(tmp_path),
            database_url=f"sqlite:///{tmp_path / 'dead.db'}",
        ))
        monkeypatch.setattr("iris.api.auth.get_settings", lambda: Settings(
            scratch_dir=scratch, iris_home=str(tmp_path),
        ))
        real = {a: getattr(Path, a) for a in ("exists", "is_file", "is_dir", "stat")}

        def wrap(attr):
            def query(self):
                if self.name == "sbin":
                    raise OSError(1920, "The system cannot access this file")
                return real[attr](self)
            return query

        for attr in real:
            monkeypatch.setattr(Path, attr, wrap(attr))
        return scratch

    def test_list_firmware_skips_dead_entries(self, dead_links) -> None:
        resp = client.get("/api/v1/firmware")
        assert resp.status_code == 200
        assert all(isinstance(item["arch"], str) for item in resp.json())

    def test_pipeline_reports_extraction_failure_instead_of_500(
        self, dead_links, monkeypatch, tmp_path
    ) -> None:
        import iris.extract.firmware as fw_mod
        import iris.extract.rootfs_extract as rx_mod

        # The endpoint imports both helpers inside the function body, so the
        # patch has to land on the defining module.
        monkeypatch.setattr(fw_mod, "analyze_firmware",
                            lambda *a, **k: type("I", (), {"arch": "mipsel"})())
        monkeypatch.setattr(
            rx_mod, "extract_rootfs",
            lambda *a, **k: (_ for _ in ()).throw(OSError(1920, "cannot access")),
        )
        resp = client.post(
            "/api/v1/pipeline",
            files={"firmware": ("fw.bin", b"\x00" * 64, "application/octet-stream")},
            params={"arch": "mipsel"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "1920" in body["error"]

    def test_emulate_endpoint_reports_404_for_a_dead_link(self, dead_links) -> None:
        """A ``/sbin -> /bin`` remnant is not a rootfs directory, so 404 — not 500."""
        resp = client.post(
            "/api/v1/emulate",
            json={"rootfs_path": str(dead_links / "fw-rootfs" / "sbin"),
                  "arch": "mipsel", "iid": 1, "port": 8080, "timeout": 10},
        )
        assert resp.status_code == 404
