"""The guest filesystem channel, and the two disks behind it.

The capability under test is the one the project had been describing instead of
having: the guest's root filesystem is a block image inside the emulation
container, nothing in that container mounts it, so every probe IRIS ran reported
UNKNOWN and the guardian's repairs reported `n=0`. These tests cover the decisions
that make the channel safe to use rather than the shell, which is the same
operation `make_image.sh` already performs at build time.

What they deliberately do not do is run docker. The one thing a fake runner cannot
prove is whether the loop mount works in the real container, and that was measured
instead -- see the 0.3.13 entry in CHANGELOG.md.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from iris.emulate import guestfs
from iris.emulate.guestfs import (
    GuestBusy,
    GuestError,
    GuestMissing,
    GuestReadOnly,
    disk_path,
    guest_exists,
    list_guest,
    pull_guest_file,
    push_guest_file,
    qemu_pid,
    reset_guest_state,
)

CONTAINER = "iris-qemu-4242"


class FakeDocker:
    """Records every docker call and answers from a script.

    Answers are keyed by a fragment of the argv rather than by call order, because
    the operations under test care *which* command went out -- mounting the state
    disk while QEMU runs is the mistake worth catching, and it is invisible in a
    transcript that only counts calls.
    """

    def __init__(self, answers: dict[str, subprocess.CompletedProcess] | None = None):
        self.calls: list[list[str]] = []
        self.answers = answers or {}

    def __call__(self, args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
        self.calls.append(list(args))
        for fragment, reply in self.answers.items():
            if fragment in " ".join(args):
                return reply
        return subprocess.CompletedProcess(args, 0, "", "")

    def scripts(self) -> list[str]:
        """The bash scripts that were sent to the container, in order."""
        out = []
        for call in self.calls:
            if "-c" in call:
                out.append(call[call.index("-c") + 1])
        return out


def ok(stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 0, stdout, "")


def fail(stderr: str, rc: int = 1) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, "", stderr)


@pytest.fixture
def docker(monkeypatch) -> FakeDocker:
    fake = FakeDocker()
    monkeypatch.setattr(guestfs, "_docker", fake)
    return fake


class TestDiskPaths:
    def test_the_state_disk_is_the_one_qemu_writes(self):
        assert disk_path(1) == "/work/scratch/1/state.raw"

    def test_the_baked_image_is_a_different_file(self):
        """Same directory, different file: conflating them would make `guest put`
        write something the next `make_image.sh` throws away."""
        assert disk_path(1, guestfs.IMAGE_DISK) == "/work/scratch/1/image.raw"
        assert disk_path(1) != disk_path(1, guestfs.IMAGE_DISK)

    def test_an_unknown_disk_is_refused_rather_than_defaulted(self):
        with pytest.raises(GuestError, match="unknown guest disk"):
            disk_path(1, "state.raw")


class TestTheBusyGuard:
    def test_a_running_qemu_stops_a_write_to_the_state_disk(self, tmp_path):
        """Mounting a filesystem a running guest has open read-write corrupts it, and
        the corruption surfaces later as an unexplained boot failure."""
        fake = FakeDocker({"cat /work": ok("1234\n"), "test -d /proc/1234": ok()})
        _install(fake)
        with pytest.raises(GuestBusy, match="pid 1234"):
            push_guest_file(CONTAINER, 4242, _host_file(tmp_path), "/tmp/x")

    def test_the_refusal_says_how_to_proceed(self):
        fake = FakeDocker({"cat /work": ok("7\n"), "test -d /proc/7": ok()})
        _install(fake)
        with pytest.raises(GuestBusy) as caught:
            list_guest(CONTAINER, 4242, "/")
        message = str(caught.value)
        assert "iris emulate stop" in message
        assert "--disk image" in message

    def test_a_stale_pid_file_does_not_block_a_stopped_emulation(self, monkeypatch):
        """`run_qemu.sh` leaves qemu.pid behind and removes it on no path, so the pid
        has to be checked against /proc or every later command would be refused."""
        fake = FakeDocker({"cat /work": ok("999\n"), "test -d /proc/999": fail("no", rc=1)})
        monkeypatch.setattr(guestfs, "_docker", fake)
        assert list_guest(CONTAINER, 4242, "/") == []

    def test_the_baked_image_needs_no_guard(self, monkeypatch):
        """It is never attached to QEMU, so there is nothing to collide with."""
        fake = FakeDocker()
        monkeypatch.setattr(guestfs, "_docker", fake)
        assert guest_exists(CONTAINER, 4242, "/etc", disk=guestfs.IMAGE_DISK) is True
        assert not any("qemu.pid" in " ".join(call) for call in fake.calls)

    def test_no_pid_file_means_nothing_is_running(self, monkeypatch):
        fake = FakeDocker({"cat /work": fail("no such file", rc=1)})
        monkeypatch.setattr(guestfs, "_docker", fake)
        assert qemu_pid(CONTAINER, 4242) is None

    def test_a_garbage_pid_file_is_not_a_pid(self, monkeypatch):
        fake = FakeDocker({"cat /work": ok("not-a-pid\n")})
        monkeypatch.setattr(guestfs, "_docker", fake)
        assert qemu_pid(CONTAINER, 4242) is None


class TestPathChecking:
    @pytest.mark.parametrize("path", ["", "etc/passwd", "../etc/passwd", "/etc/../../x"])
    def test_a_path_that_is_not_plainly_inside_the_guest_is_refused(self, path):
        """The value is concatenated onto a mount point inside a shell script, so
        `../../work` would reach the container's own filesystem."""
        fake = FakeDocker()
        _install(fake)
        with pytest.raises(GuestError):
            list_guest(CONTAINER, 4242, path)
        assert fake.calls == []

    def test_a_relative_path_is_refused_before_any_docker_call(self):
        fake = FakeDocker()
        _install(fake)
        with pytest.raises(GuestError, match="absolute"):
            guest_exists(CONTAINER, 4242, "firmadyne/init")
        assert fake.calls == []


class TestReading:
    def test_a_listing_sorts_and_drops_the_dot_entries(self, monkeypatch):
        fake = FakeDocker({"ls -1A": ok("usr\nfirmadyne\nbin\n")})
        monkeypatch.setattr(guestfs, "_docker", fake)
        assert list_guest(CONTAINER, 4242, "/") == ["bin", "firmadyne", "usr"]

    def test_a_read_mounts_the_image_read_only(self, monkeypatch):
        """A read that mounted read-write would be a repair nobody asked for."""
        fake = FakeDocker({"ls -1A": ok("bin\n")})
        monkeypatch.setattr(guestfs, "_docker", fake)
        list_guest(CONTAINER, 4242, "/", disk=guestfs.IMAGE_DISK)
        assert any('-o ro,loop' in script for script in fake.scripts())

    def test_a_missing_directory_says_so_instead_of_pretending_to_be_empty(
            self, monkeypatch):
        """An empty listing and a missing directory are different facts, and only one
        of them is an answer."""
        fake = FakeDocker({"ls -1A": fail("IRIS_GUESTFS_MISSING: /nope", rc=4)})
        monkeypatch.setattr(guestfs, "_docker", fake)
        with pytest.raises(GuestMissing, match="/nope"):
            list_guest(CONTAINER, 4242, "/nope")

    def test_a_mount_failure_is_not_reported_as_an_empty_guest(self, monkeypatch):
        fake = FakeDocker({"ls -1A": fail("IRIS_GUESTFS_MOUNT_FAILED: permission denied",
                                         rc=5)})
        monkeypatch.setattr(guestfs, "_docker", fake)
        with pytest.raises(GuestError, match="could not mount"):
            list_guest(CONTAINER, 4242, "/")

    def test_exists_distinguishes_present_from_absent(self, monkeypatch):
        monkeypatch.setattr(guestfs, "_docker", FakeDocker({"test -d /proc": fail("", rc=1)}))
        assert guest_exists(CONTAINER, 4242, "/firmadyne/init") is True

    def test_a_missing_file_is_absent_not_an_error(self, monkeypatch):
        fake = FakeDocker({"[ -e": fail("", rc=1)})
        monkeypatch.setattr(guestfs, "_docker", fake)
        assert guest_exists(CONTAINER, 4242, "/nope") is False

    def test_a_mount_failure_during_exists_is_an_error(self, monkeypatch):
        """`test -e` on an unmounted point answers about the mount point, so a failed
        mount would otherwise come back as a confident "no such file"."""
        fake = FakeDocker({"[ -e": fail("IRIS_GUESTFS_MOUNT_FAILED", rc=5)})
        monkeypatch.setattr(guestfs, "_docker", fake)
        with pytest.raises(GuestError, match="could not mount"):
            guest_exists(CONTAINER, 4242, "/firmadyne/init")


class TestPulling:
    def test_a_pulled_file_lands_where_the_caller_asked(self, monkeypatch, tmp_path):
        fake = FakeDocker()
        monkeypatch.setattr(guestfs, "_docker", fake)
        dest = tmp_path / "out" / "init"
        assert pull_guest_file(CONTAINER, 4242, "/firmadyne/init", dest) == dest
        assert any(call[:3] == ["docker", "cp", f"{CONTAINER}:/tmp/iris-guestfs-out"]
                   for call in fake.calls)

    def test_the_staging_file_is_removed_afterwards(self, monkeypatch, tmp_path):
        """It lives in the container's /tmp, which nothing else ever cleans."""
        fake = FakeDocker()
        monkeypatch.setattr(guestfs, "_docker", fake)
        pull_guest_file(CONTAINER, 4242, "/firmadyne/init", tmp_path / "init")
        assert any(call[3:5] == ["rm", "-f"] for call in fake.calls)

    def test_a_directory_is_refused_rather_than_copied_as_a_file(self, monkeypatch,
                                                                 tmp_path):
        fake = FakeDocker({"cp": fail("IRIS_GUESTFS_NOT_A_FILE: /etc", rc=6)})
        monkeypatch.setattr(guestfs, "_docker", fake)
        with pytest.raises(GuestReadOnly):
            pull_guest_file(CONTAINER, 4242, "/etc", tmp_path / "etc")

    def test_a_failed_copy_out_is_an_error_not_an_empty_file(self, monkeypatch, tmp_path):
        def runner(args, timeout=120):
            if args[:2] == ["docker", "cp"]:
                return fail("no such file", rc=1)
            return ok()

        monkeypatch.setattr(guestfs, "_docker", runner)
        with pytest.raises(GuestError, match="no such file"):
            pull_guest_file(CONTAINER, 4242, "/firmadyne/init", tmp_path / "init")


class TestPushing:
    def test_the_mode_is_applied_in_the_guest(self, monkeypatch, tmp_path):
        """A host executable bit is not portable -- on Windows there is not one --
        so a repair that has to be executable has to be able to say so."""
        fake = FakeDocker()
        monkeypatch.setattr(guestfs, "_docker", fake)
        push_guest_file(CONTAINER, 4242, _host_file(tmp_path), "/firmadyne/proof.sh",
                        mode="755")
        script = fake.scripts()[-1]
        assert 'chmod "$3" "$MNT$TARGET"' in script
        assert fake.calls[-1][-1] == "755"

    def test_an_empty_mode_changes_nothing(self, monkeypatch, tmp_path):
        fake = FakeDocker()
        monkeypatch.setattr(guestfs, "_docker", fake)
        push_guest_file(CONTAINER, 4242, _host_file(tmp_path), "/etc/marker")
        assert 'if [ -n "$3" ]; then' in fake.scripts()[-1]

    def test_a_non_octal_mode_is_refused_before_anything_is_copied(self, tmp_path):
        """`chmod` would take "755junk" as a symbolic mode and do something else."""
        fake = FakeDocker()
        _install(fake)
        with pytest.raises(GuestError, match="octal"):
            push_guest_file(CONTAINER, 4242, _host_file(tmp_path), "/x", mode="rwxr-xr-x")
        assert fake.calls == []

    def test_a_write_unmounts_before_it_checks_the_filesystem(self, monkeypatch, tmp_path):
        """`e2fsck` on a mounted image refuses, and an image left mounted is what
        makes the next mount fail with 'already mounted'."""
        fake = FakeDocker()
        monkeypatch.setattr(guestfs, "_docker", fake)
        push_guest_file(CONTAINER, 4242, _host_file(tmp_path), "/etc/marker")
        script = fake.scripts()[-1]
        assert script.index("umount") < script.index("e2fsck")

    def test_a_write_creates_the_parent_directory(self, monkeypatch, tmp_path):
        fake = FakeDocker()
        monkeypatch.setattr(guestfs, "_docker", fake)
        push_guest_file(CONTAINER, 4242, _host_file(tmp_path), "/var/run/x/marker")
        assert 'mkdir -p "$(dirname "$MNT$TARGET")"' in fake.scripts()[-1]

    def test_a_missing_source_is_refused(self, monkeypatch, tmp_path):
        fake = FakeDocker()
        monkeypatch.setattr(guestfs, "_docker", fake)
        with pytest.raises(GuestError, match="not a file"):
            push_guest_file(CONTAINER, 4242, tmp_path / "absent", "/x")
        assert fake.calls == []

    def test_a_filesystem_check_failure_does_not_fail_the_write(self, monkeypatch, tmp_path):
        """`e2fsck` runs after the unmount and its verdict is not allowed to become
        the write's verdict: the file is already in the image, and reporting failure
        would send the caller looking for a repair that is already there."""
        fake = FakeDocker()
        monkeypatch.setattr(guestfs, "_docker", fake)
        push_guest_file(CONTAINER, 4242, _host_file(tmp_path), "/etc/marker")
        assert 'e2fsck -p "$DISK" || true' in fake.scripts()[-1]


class TestCleanup:
    def test_every_operation_unmounts_on_the_way_out(self, monkeypatch):
        """Including when it fails: a leaked loop mount in a container that lives for
        the whole session makes the next mount fail as 'already mounted', which reads
        as a corrupt image."""
        fake = FakeDocker({"ls -1A": ok("bin\n")})
        monkeypatch.setattr(guestfs, "_docker", fake)
        list_guest(CONTAINER, 4242, "/")
        script = fake.scripts()[-1]
        assert "trap cleanup EXIT" in script
        assert 'if [ "$MNTED" = 1 ]; then' in script

    def test_the_cleanup_does_not_mask_the_exit_code(self, monkeypatch):
        """`umount` on an already-unmounted point fails, and a trap that returns
        non-zero turns a successful read into a failure."""
        fake = FakeDocker({"ls -1A": ok("bin\n")})
        monkeypatch.setattr(guestfs, "_docker", fake)
        list_guest(CONTAINER, 4242, "/")
        assert "return 0" in fake.scripts()[-1]


class TestReset:
    def test_a_reset_removes_the_state_disk(self, monkeypatch):
        fake = FakeDocker({"test -e": ok()})
        monkeypatch.setattr(guestfs, "_docker", fake)
        assert reset_guest_state(CONTAINER, 4242) is True
        assert any(call[3:5] == ["rm", "-f"] for call in fake.calls)

    def test_resetting_nothing_says_nothing_happened(self, monkeypatch):
        """Otherwise 'reset' and 'there was nothing to reset' both print success, and
        a caller cannot tell a repair was undone from one that never landed."""
        fake = FakeDocker({"test -e": fail("no", rc=1)})
        monkeypatch.setattr(guestfs, "_docker", fake)
        assert reset_guest_state(CONTAINER, 4242) is False
        assert not any(call[3:5] == ["rm", "-f"] for call in fake.calls)

    def test_a_reset_needs_the_emulation_stopped(self, monkeypatch):
        fake = FakeDocker({"cat /work": ok("5\n"), "test -d /proc/5": ok()})
        monkeypatch.setattr(guestfs, "_docker", fake)
        with pytest.raises(GuestBusy):
            reset_guest_state(CONTAINER, 4242)


def _install(fake: FakeDocker):
    """Point the module at ``fake`` for this test, restored afterwards."""
    original = guestfs._docker
    guestfs._docker = fake
    _RESTORED.append(original)
    return fake


_RESTORED: list = []


@pytest.fixture(autouse=True)
def _restore_runner():
    yield
    while _RESTORED:
        guestfs._docker = _RESTORED.pop()


def _host_file(tmp_path: Path) -> Path:
    """A real file, because `push_guest_file` refuses anything that is not one."""
    path = tmp_path / "proof.txt"
    path.write_text("proof\n", encoding="utf-8")
    return path

class TestGitBashPaths:
    """Git Bash rewrites a POSIX argument into a Windows path before the process sees
    it. This project's users are on Windows, so without this every documented
    `iris guest ls 1 /etc/passwd` fails before it reaches the guest -- and the error
    ("guest path must be absolute") points at the guest rather than at the shell.
    """

    @pytest.mark.parametrize(("given", "expected"), [
        ("C:/Program Files/Git/", "/"),
        ("C:/Program Files/Git/firmadyne", "/firmadyne"),
        ("C:/Program Files/Git/etc/passwd", "/etc/passwd"),
    ])
    def test_a_rewritten_path_is_recovered(self, given, expected):
        assert guestfs.guest_path(given) == expected

    def test_a_path_git_bash_left_alone_is_untouched(self):
        assert guestfs.guest_path("/firmadyne/init") == "/firmadyne/init"

    def test_the_recovered_path_is_what_the_container_receives(self, monkeypatch):
        fake = FakeDocker()
        monkeypatch.setattr(guestfs, "_docker", fake)
        guestfs.list_guest(CONTAINER, 4242, guestfs.guest_path("C:/Program Files/Git/etc"))
        assert fake.calls[-1][-1] == "/etc"

    def test_a_windows_path_that_is_not_a_rewrite_is_still_refused(self):
        """Only the Git installation prefix is recoverable; `C:/temp/x` is not a guest
        path under any reading, and silently truncating it would be worse."""
        with pytest.raises(GuestError, match="absolute"):
            guestfs.guest_path("C:/temp/x")
