"""Launching an emulation from an uploaded file.

The endpoint exists because the workbench could only ever start a run whose rootfs
already sat in the scratch directory. Two of the three inputs a person actually has
-- a tar of a rootfs they extracted by hand, and the vendor's ``.bin`` -- were
reachable only from a terminal.

Nothing here starts a container. ``emulate_firmware`` is replaced with a recorder,
so these tests assert the endpoint's contract (what it accepts, what it derives,
what it registers, what it refuses) without docker, a baked image, or a boot that
takes minutes and can only end in "web unreachable" on a host that is not the one
being demonstrated.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import iris.api.server as server_mod
from iris.api.server import UploadLaunchResponse, app
from iris.emulate.orchestrator import EmulationResult

TOKEN = "upload-launch-token"

#: A rootfs-shaped archive. The ELF magic is irrelevant to these tests -- the census
#: reads the first four bytes and finds nothing runnable, which is exactly the case
#: that forces the caller to pass ``arch`` explicitly.
ROOTFS_ENTRIES = (
    "bin/busybox",
    "etc/passwd",
    "etc/init.d/rcS",
    "lib/libc.so.0",
    "sbin/init",
    "usr/bin/httpd",
    "var/log/messages",
)


def _rootfs_tar(path: Path, prefix: str = "squashfs-root/", payload: bytes = b"data") -> bytes:
    with tarfile.open(path, "w") as tf:
        for rel in ROOTFS_ENTRIES:
            info = tarfile.TarInfo(f"{prefix}{rel}")
            info.size = len(payload)
            info.mode = 0o755
            tf.addfile(info, io.BytesIO(payload))
    return path.read_bytes()


def _fake_firmware(tmp_path: Path) -> bytes:
    """Bytes that are not a tar, so the source auto-detection calls them firmware."""
    content = b"\x27\x05\x00\x00" + b"\x00" * 32
    (tmp_path / "vendor.bin").write_bytes(content)
    return content


@pytest.fixture
def env(tmp_path, monkeypatch):
    """An isolated server: temp home, temp database, no token, no docker.

    Returned rather than yielded so a test can retune it -- ``api_max_upload_mb``
    in particular, which is what the cap tests turn down. ``get_settings`` is
    patched to a *lambda over this object*, so mutating the object is how a test
    changes the server's view of its configuration.
    """
    from iris.api import auth as auth_mod
    from iris.config import Settings

    home = tmp_path / "home"
    (home / "scratch").mkdir(parents=True, exist_ok=True)
    settings = Settings(
        iris_home=str(home),
        database_url=f"sqlite:///{(home / 'api.db').as_posix()}",
    )
    # `rules_dir` walks up looking for the repository's own rules/ directory, which
    # exists in a source checkout -- so pointing it at a temp path is what keeps
    # these tests from writing repairs into a tree they then assert on.
    monkeypatch.setattr(
        type(settings), "rules_dir", property(lambda self: tmp_path / "no-such-rules")
    )
    monkeypatch.setattr(auth_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(server_mod, "get_settings", lambda: settings)
    monkeypatch.setattr("iris.db.active.container_alive", lambda name: True)
    return settings


@pytest.fixture
def runs(monkeypatch):
    """Record every ``emulate_firmware`` call instead of booting anything."""
    calls: list[dict] = []

    def record(**kwargs):
        calls.append(kwargs)
        return EmulationResult(
            rootfs_dir=kwargs["rootfs_dir"],
            arch=kwargs["arch"],
            success=True,
            web_ok=True,
            web_url=f"http://127.0.0.1:{kwargs['host_port']}/",
            duration_sec=1.5,
            container_id=f"iris-qemu-{kwargs['iid']}",
        )

    monkeypatch.setattr(server_mod, "emulate_firmware", record)
    # No preflight: it walks the tree and reads real ELF headers, and these trees
    # hold none. The preflight contract has its own tests.
    monkeypatch.setattr(server_mod, "preflight_arch", lambda rootfs, arch: None)
    return calls


@pytest.fixture
def client(env, runs):
    return TestClient(app)


def _upload(client, name: str, content: bytes, **params):
    return client.post(
        "/api/v1/emulate/upload",
        files={"file": (name, content, "application/octet-stream")},
        params=params,
    )


# ------------------------------------------------------------- the rootfs path


class TestRootfsUpload:
    def test_a_rootfs_archive_boots_and_registers(self, client, runs, env) -> None:
        resp = _upload(client, "rootfs.tar", _rootfs_tar(Path(env.scratch_dir) / "a.tar"),
                       arch="mipsel")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["source"] == "rootfs"
        assert body["arch"] == "mipsel"
        assert body["success"] is True
        assert body["members"] == len(ROOTFS_ENTRIES)
        assert runs[0]["arch"] == "mipsel"

    def test_the_tree_lands_in_the_scratch_tree_under_its_own_name(self, client, runs, env) -> None:
        resp = _upload(client, "dir-868l-rootfs.tar",
                       _rootfs_tar(Path(env.scratch_dir) / "b.tar"), arch="mipsel")
        rootfs = Path(resp.json()["rootfs_path"])
        assert rootfs.parent == Path(env.scratch_dir)
        assert rootfs.name.endswith("-rootfs")
        assert (rootfs / "bin" / "busybox").is_file()

    def test_the_wrapper_prefix_is_not_part_of_the_result_path(self, client, runs, env) -> None:
        """The archive holds ``squashfs-root/``; the reported rootfs is the tree
        inside it, because every later path in the product is built by appending to
        the rootfs and would otherwise carry a level nobody asked for."""
        resp = _upload(client, "rootfs.tar",
                       _rootfs_tar(Path(env.scratch_dir) / "c.tar"), arch="mipsel")
        assert "squashfs-root" not in resp.json()["rootfs_path"]

    def test_the_source_is_reported_so_the_page_can_explain_the_two_paths(
        self, client, runs, env
    ) -> None:
        """A rootfs archive that holds no runnable ELF and a firmware image whose
        extraction failed need different sentences; one shared field cannot carry
        both."""
        resp = _upload(client, "rootfs.tar",
                       _rootfs_tar(Path(env.scratch_dir) / "d.tar"), arch="mipsel")
        assert resp.json()["source"] == "rootfs"

    def test_the_unpack_accounting_reaches_the_response(self, client, runs, env) -> None:
        """Skipped links are the difference between a rootfs that boots and one that
        dies on a missing init, so the count cannot stay server-side."""
        resp = _upload(client, "rootfs.tar",
                       _rootfs_tar(Path(env.scratch_dir) / "e.tar"), arch="mipsel")
        body = resp.json()
        assert body["members"] > 0
        assert body["total_bytes"] > 0
        assert body["links_created"] >= 0
        assert body["links_skipped"] >= 0
        assert body["rejected_members"] == 0

    def test_the_run_is_registered_as_owned_by_the_caller(self, client, runs, env) -> None:
        resp = _upload(client, "rootfs.tar",
                       _rootfs_tar(Path(env.scratch_dir) / "f.tar"), arch="mipsel")
        iid = resp.json()["iid"]
        listed = client.get("/api/v1/emulate").json()
        assert [row["iid"] for row in listed] == [iid]
        assert client.get(f"/api/v1/emulate/{iid}").status_code == 200

    def test_port_zero_becomes_a_real_port(self, client, runs, env) -> None:
        """``port=0`` used to reach ``docker create -p 0:0``, which publishes
        nothing: the run then burned its whole boot timeout probing a port that was
        never open and reported the guest at fault."""
        resp = _upload(client, "rootfs.tar",
                       _rootfs_tar(Path(env.scratch_dir) / "g.tar"), arch="mipsel", port=0)
        assert resp.status_code == 200
        assert resp.json()["host_port"] > 0
        assert runs[0]["host_port"] == resp.json()["host_port"]

    def test_an_explicit_port_is_honoured(self, client, runs, env) -> None:
        resp = _upload(client, "rootfs.tar",
                       _rootfs_tar(Path(env.scratch_dir) / "h.tar"), arch="mipsel", port=8123)
        assert resp.json()["host_port"] == 8123
        assert runs[0]["host_port"] == 8123

    def test_the_response_model_forbids_extra_fields(self) -> None:
        """Pinned on the model, not the body: pydantic drops unknown fields, so a
        response that quietly stopped carrying one would still type-check."""
        from pydantic import ValidationError

        minimal = {
            "iid": 1, "source": "rootfs", "name": "r.tar", "arch": "mipsel",
            "rootfs_path": "/tmp/r-rootfs", "host_port": 8080, "members": 1,
            "total_bytes": 4, "links_created": 0, "links_skipped": 0,
            "rejected_members": 0, "success": True, "web_ok": True,
            "web_url": "-", "duration_sec": 1.0, "error": "", "container_id": "x",
        }
        assert UploadLaunchResponse(**minimal).iid == 1
        with pytest.raises(ValidationError):
            UploadLaunchResponse(**minimal, active_emulations=0)


# ------------------------------------------------------------- the firmware path


class TestFirmwareUpload:
    def test_a_non_archive_takes_the_firmware_path(self, client, runs, env, monkeypatch) -> None:
        """Detection by tar magic, not by filename: a firmware image that happens to
        be called ``rootfs.tar.gz`` must still be extracted."""
        seen: dict = {}

        def prepare(firmware, scratch_dir, **kwargs):
            seen["source_path"] = firmware
            from iris.emulate.auto import PreparedRootfs

            return PreparedRootfs(rootfs_dir=scratch_dir / "fw-rootfs", arch="mipsel",
                                  notes=["squashfs extracted via squashfs"])

        monkeypatch.setattr("iris.emulate.auto.prepare_from_firmware", prepare)
        content = _fake_firmware(Path(env.scratch_dir))
        resp = _upload(client, "rootfs.tar.gz", content)
        assert resp.status_code == 200, resp.text
        assert resp.json()["source"] == "firmware"
        assert seen["source_path"].name.endswith("rootfs.tar.gz")

    def test_the_kind_can_be_forced_to_firmware(self, client, runs, env, monkeypatch) -> None:
        from iris.emulate.auto import PreparedRootfs

        monkeypatch.setattr(
            "iris.emulate.auto.prepare_from_firmware",
            lambda firmware, scratch_dir, **kw: PreparedRootfs(
                rootfs_dir=scratch_dir / "fw-rootfs", arch="mipsel"),
        )
        resp = _upload(client, "mystery.bin", b"\x00" * 32, kind="firmware")
        assert resp.status_code == 200
        assert resp.json()["source"] == "firmware"


# ------------------------------------------------------------------- refusals


class TestRefusals:
    def test_an_empty_file_is_refused(self, client, runs, env) -> None:
        resp = _upload(client, "empty.tar", b"", arch="mipsel")
        assert resp.status_code == 400
        assert runs == [], "an empty upload reached the orchestrator"

    def test_a_zip_container_is_refused(self, client, runs, env) -> None:
        resp = _upload(client, "rootfs.zip", b"PK\x03\x04rest", arch="mipsel")
        assert resp.status_code == 415
        assert "zip" in resp.json()["detail"]

    def test_claiming_rootfs_for_a_non_archive_is_refused(self, client, runs, env) -> None:
        """Explicitly asking for the rootfs path and sending a firmware image is a
        mistake worth naming; silently taking the other path would hide it."""
        resp = _upload(client, "vendor.bin", b"\x00" * 32, kind="rootfs")
        assert resp.status_code == 415
        assert "not a tar archive" in resp.json()["detail"]

    def test_a_tar_without_a_rootfs_is_refused(self, client, runs, env) -> None:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            info = tarfile.TarInfo("README.md")
            info.size = 3
            tf.addfile(info, io.BytesIO(b"hi\n"))
        resp = _upload(client, "docs.tar", buf.getvalue(), arch="mipsel")
        assert resp.status_code == 422
        assert "no rootfs directory structure" in resp.json()["detail"]
        assert runs == []

    def test_an_unsupported_arch_is_refused_before_docker(self, client, runs, env) -> None:
        resp = _upload(client, "rootfs.tar",
                       _rootfs_tar(Path(env.scratch_dir) / "i.tar"), arch="vax")
        assert resp.status_code == 400
        assert "unsupported arch" in resp.json()["detail"]
        assert runs == []

    def test_undetermined_arch_is_refused_with_the_supported_list(
        self, client, runs, env
    ) -> None:
        """A tree with no ELF answers nothing, and the message has to say what
        would work -- otherwise the page shows "failed" for a missing dropdown."""
        resp = _upload(client, "rootfs.tar",
                       _rootfs_tar(Path(env.scratch_dir) / "j.tar"))
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "mipsel" in detail and "armel" in detail
        assert runs == []

    def test_a_preflight_failure_stops_before_docker(self, client, runs, env, monkeypatch) -> None:
        from iris.failures import Failure, FailureKind

        monkeypatch.setattr(
            server_mod, "preflight_arch",
            lambda rootfs, arch: Failure(FailureKind.ARCH_MISMATCH, "rootfs is armel"),
        )
        resp = _upload(client, "rootfs.tar",
                       _rootfs_tar(Path(env.scratch_dir) / "k.tar"), arch="mipsel")
        assert resp.status_code == 400
        assert "rootfs is armel" in resp.json()["detail"]
        assert runs == []

    def test_a_failed_boot_is_a_200_with_the_verdict(self, client, runs, env, monkeypatch) -> None:
        """A guest that does not come up is a *result*, not a request error: the
        status code carries the HTTP contract, and a 500 here would tell the page
        the request was malformed."""
        def failed(**kwargs):
            return EmulationResult(
                rootfs_dir=kwargs["rootfs_dir"],
                arch=kwargs["arch"],
                success=False,
                web_ok=False,
                error="guest never opened its web port",
                container_id=f"iris-qemu-{kwargs['iid']}",
            )

        monkeypatch.setattr(server_mod, "emulate_firmware", failed)
        resp = _upload(client, "rootfs.tar",
                       _rootfs_tar(Path(env.scratch_dir) / "l.tar"), arch="mipsel")
        assert resp.status_code == 200
        assert resp.json()["success"] is False
        assert "never opened" in resp.json()["error"]


# ------------------------------------------------------------------ the cap


class TestUploadCap:
    def test_an_upload_past_the_cap_is_refused(self, client, runs, env) -> None:
        env.api_max_upload_mb = 1
        # 1 MiB cap, 1 MiB + 1 byte: the boundary has to be "over", not "at least
        # this big", or the guard is off by a whole chunk.
        resp = _upload(client, "rootfs.tar", b"\x00" * (1024 * 1024 + 1), arch="mipsel")
        assert resp.status_code == 413
        assert "1 MiB" in resp.json()["detail"]
        assert runs == []

    def test_an_over_cap_upload_leaves_no_tree_behind(self, client, runs, env) -> None:
        """The cap is enforced while the body streams in, so nothing is staged --
        the same bound that replaced ``firmware.read()`` on the pipeline route."""
        env.api_max_upload_mb = 1
        big = _rootfs_tar(Path(env.scratch_dir) / "m.tar", payload=b"x" * 200_000)
        assert len(big) > 1024 * 1024, "the fixture archive is too small to exceed the cap"
        resp = _upload(client, "rootfs.tar", big, arch="mipsel")
        assert resp.status_code == 413
        assert not [p for p in Path(env.scratch_dir).iterdir() if p.name.endswith("-rootfs")]
        assert runs == []

    def test_a_large_upload_under_the_cap_is_accepted(self, client, runs, env) -> None:
        """Negative control: a guard that refuses everything is not a guard."""
        env.api_max_upload_mb = 1
        resp = _upload(client, "rootfs.tar",
                       _rootfs_tar(Path(env.scratch_dir) / "p.tar"), arch="mipsel")
        assert resp.status_code == 200
        assert len(runs) == 1


# ------------------------------------------------------------------- auth


@pytest.fixture
def token_on(monkeypatch):
    """Configure a token for the duration of one test.

    Patched as a module attribute rather than by deleting it afterwards: an earlier
    version did ``del auth.configured_token`` to undo the patch, which removed the
    real function from the module for the rest of the session and turned every
    later request into a ``NameError``.
    """
    from iris.api import auth as auth_mod

    monkeypatch.setattr(auth_mod, "configured_token", lambda settings=None: TOKEN)
    return TOKEN


class TestAuth:
    def test_the_route_requires_a_token_when_one_is_configured(self, client, token_on) -> None:
        resp = client.post(
            "/api/v1/emulate/upload",
            files={"file": ("r.tar", b"x", "application/octet-stream")},
            params={"arch": "mipsel"},
        )
        assert resp.status_code == 401

    def test_a_wrong_token_is_refused(self, client, token_on) -> None:
        resp = client.post(
            "/api/v1/emulate/upload",
            files={"file": ("r.tar", b"x", "application/octet-stream")},
            params={"arch": "mipsel"},
            headers={"X-IRIS-Token": "not-the-token"},
        )
        assert resp.status_code == 401

    def test_the_correct_token_is_accepted(self, client, runs, env, token_on) -> None:
        resp = client.post(
            "/api/v1/emulate/upload",
            files={"file": ("rootfs.tar",
                            _rootfs_tar(Path(env.scratch_dir) / "n.tar"),
                            "application/octet-stream")},
            params={"arch": "mipsel"},
            headers={"X-IRIS-Token": token_on},
        )
        assert resp.status_code == 200, resp.text


# ------------------------------------------------------------ parameter bounds


class TestParameterValidation:
    @pytest.mark.parametrize(
        "params",
        [
            {"kind": "banana"},
            {"port": "-1"},
            {"port": "70000"},
            {"timeout": "0"},
            {"timeout": "99999"},
        ],
    )
    def test_a_bad_parameter_is_refused(self, client, runs, env, params) -> None:
        resp = _upload(client, "rootfs.tar",
                       _rootfs_tar(Path(env.scratch_dir) / "o.tar"),
                       arch="mipsel", **params)
        assert resp.status_code == 422, resp.text
        assert runs == []

# --------------------------------------------------- port 0 on every launch route


class TestPortZeroIsResolvedEverywhere:
    """``port=0`` means "pick a free one" on all three launch routes.

    It used to reach ``docker create -p 0:0`` on ``/api/v1/emulate`` and
    ``/api/v1/pipeline``, which publishes nothing usable. The run then spent its
    whole boot timeout probing a port that was never open and reported
    ``web-unreachable`` -- pointing at the guest when the fault was the request.

    These live here rather than beside the upload route because the fix is one
    shared helper: a test that only covered the new endpoint would have stayed green
    while the two older routes kept the defect.
    """

    def test_the_helper_never_returns_zero(self) -> None:
        assert server_mod._resolve_port(0) > 0

    def test_an_explicit_port_is_passed_through(self) -> None:
        assert server_mod._resolve_port(8123) == 8123

    def test_emulate_resolves_port_zero(self, tmp_path, monkeypatch) -> None:
        seen: dict = {}

        def record(**kwargs):
            seen["host_port"] = kwargs["host_port"]
            return EmulationResult(rootfs_dir=kwargs["rootfs_dir"], arch=kwargs["arch"])

        monkeypatch.setattr(server_mod, "emulate_firmware", record)
        monkeypatch.setattr(server_mod, "preflight_arch", lambda rootfs, arch: None)
        rootfs = tmp_path / "fw-rootfs"
        (rootfs / "bin").mkdir(parents=True)
        resp = TestClient(app).post(
            "/api/v1/emulate",
            json={"rootfs_path": str(rootfs), "arch": "mipsel", "port": 0, "timeout": 5},
        )
        assert resp.status_code == 200, resp.text
        assert seen["host_port"] > 0