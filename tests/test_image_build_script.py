"""The image build refuses early, cleans up after itself, and says where it got.

Three costs came out of one script, and each is pinned here rather than discovered
again by waiting for a build to go wrong:

* iid 6000 -- a missing rootfs tarball was found by ``tar``, after a 1G raw image
  had been created and mkfs'd;
* iid 7672 -- ``rm: cannot remove '/tmp/build-7672/image': Device or resource busy``,
  which was a previous run's ``umount`` failing under ``set -e`` and taking the rest
  of the script with it;
* ``Image build output: l Channel----`` in a run log, which was 200 characters of
  the tail of the build's stdout and said nothing except that the log had dropped
  the part that mattered.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from iris.emulate.orchestrator import _build_failure_detail, _reject_empty_artifact

ROOT = Path(__file__).resolve().parents[1]
MAKE_IMAGE = ROOT / "scripts" / "emulate" / "make_image.sh"
SCRIPT = MAKE_IMAGE.read_text(encoding="utf-8")


def _statements(script: str) -> list[str]:
    """Whole shell statements, with backslash continuations joined.

    Line-by-line reading is what made a two-line error ``echo`` look like an error
    written to stdout, because only its first line carries the redirect.
    """
    joined: list[str] = []
    buffer = ""
    for raw in script.splitlines():
        line = raw.strip()
        if buffer:
            buffer += " " + line
        else:
            buffer = line
        if buffer.endswith("\\"):
            buffer = buffer[:-1].strip()
            continue
        if buffer:
            joined.append(buffer)
        buffer = ""
    if buffer:
        joined.append(buffer)
    return joined


class TestTheTarballIsCheckedBeforeAnythingExpensive:
    def test_the_guard_comes_before_the_image_is_created(self):
        """Ordering is the entire point: the check has to precede the 1G create."""
        guard = SCRIPT.index('if [ ! -s "${TARBALL}" ]')
        assert guard < SCRIPT.index("qemu-img create")
        assert guard < SCRIPT.index("mkfs.${FS}")

    def test_the_guard_also_rejects_an_empty_tarball(self):
        """"-s" is size, not existence: a 0-byte pack is the shape this failure took."""
        assert '-s "${TARBALL}"' in SCRIPT

    def test_the_guard_names_the_path_it_wanted(self):
        block = SCRIPT[SCRIPT.index('if [ ! -s "${TARBALL}" ]'):][:400]
        assert "${TARBALL}" in block

    def test_it_exits_nonzero_so_the_caller_hears_about_it(self):
        block = SCRIPT[SCRIPT.index('if [ ! -s "${TARBALL}" ]'):][:400]
        assert "exit 3" in block


class TestTheLoopMountIsAlwaysReleased:
    def test_there_is_an_exit_trap(self):
        assert "trap cleanup EXIT" in SCRIPT

    def test_the_trap_is_armed_before_the_mount_and_disarmed_after(self):
        lines = SCRIPT.splitlines()
        mount_at = next(i for i, l in enumerate(lines) if "mount -o loop" in l)
        armed_at = max(i for i, l in enumerate(lines) if l.strip() == "MNTED=1")
        disarmed_at = max(i for i, l in enumerate(lines) if l.strip() == "MNTED=0")
        assert armed_at == mount_at + 1, "the mount must be armed before anything can fail after it"
        assert disarmed_at > next(i for i, l in enumerate(lines)
                                  if l.strip() == 'umount "${TMP_IMAGE_DIR}"')

    def test_a_busy_mount_is_tried_lazily_before_giving_up(self):
        """The first umount legitimately fails while anything holds the image open."""
        block = SCRIPT[SCRIPT.index("cleanup() {"):][:400]
        assert 'umount "${TMP_IMAGE_DIR}"' in block
        assert 'umount -l "${TMP_IMAGE_DIR}"' in block
        assert "|| true" in block

    def test_the_trap_never_makes_the_script_fail(self):
        """A trap that returns non-zero reports the cleanup as a build failure."""
        block = SCRIPT[SCRIPT.index("cleanup() {"):][:400]
        assert "return 0" in block

    def test_e2fsck_runs_after_the_explicit_unmount(self):
        """It cannot run on a mounted image, and the trap only fires at exit."""
        unmount = SCRIPT.index('umount "${TMP_IMAGE_DIR}"')
        assert unmount < SCRIPT.index("e2fsck -y")


class TestTheBuildPrintsProgressAndReturnsTheAnswer:
    def test_stdout_is_redirected_onto_stderr_and_a_new_fd(self):
        """Progress is not the answer, so it is moved off the answer's stream."""
        assert "exec 3>&1 1>&2" in SCRIPT

    def test_progress_goes_through_the_helper(self):
        assert "progress() { echo \"$@\" >&3; }" in SCRIPT

    def test_the_image_path_is_the_only_thing_left_on_stdout(self):
        """Read by statement, not by line: the guard's message is a two-line echo."""
        stdout_statements = [s for s in _statements(SCRIPT)
                             if s.startswith("echo ") and ">&2" not in s]
        assert stdout_statements == ['echo "==== Image built: ${IMAGE} ===="'], stdout_statements

    def test_the_progress_helper_is_defined_before_its_first_use(self):
        assert SCRIPT.index("progress() {") < SCRIPT.index('progress "----Creating')

    def test_every_remaining_plain_echo_is_an_error_report(self):
        """The guard's message is the only other one, and it has to reach a reader."""
        for statement in _statements(SCRIPT):
            if statement.startswith("echo ") and ">&2" not in statement:
                assert "Image built" in statement, f"progress left on stdout: {statement}"


class TestTheEmptyArtifactIsRejectedInPython:
    """A docker cp that succeeds has only proved that a file was copied."""

    def test_a_real_artifact_passes_through(self, tmp_path):
        artifact = tmp_path / "rootfs.tar.gz"
        artifact.write_bytes(b"x" * 32)
        assert _reject_empty_artifact(artifact, what="t", stage="s") == artifact

    def test_a_missing_artifact_is_rejected(self, tmp_path):
        with pytest.raises(RuntimeError, match="missing or empty"):
            _reject_empty_artifact(tmp_path / "absent.tar.gz", what="t", stage="s")

    def test_a_zero_byte_artifact_is_rejected(self, tmp_path):
        artifact = tmp_path / "empty.tar.gz"
        artifact.write_bytes(b"")
        with pytest.raises(RuntimeError, match="missing or empty"):
            _reject_empty_artifact(artifact, what="rootfs tarball", stage="s")

    def test_the_message_says_what_was_expected_and_where_it_came_from(self, tmp_path):
        artifact = tmp_path / "empty.tar.gz"
        artifact.write_bytes(b"")
        with pytest.raises(RuntimeError) as err:
            _reject_empty_artifact(artifact, what="rootfs tarball", stage="tar czf")
        assert "rootfs tarball" in str(err.value)
        assert "tar czf" in str(err.value)
        assert str(artifact) in str(err.value)

    def test_a_zero_byte_cached_tarball_is_not_reusable(self, tmp_path):
        """Newer than everything it was built from, and still worthless."""
        from iris.emulate.orchestrator import _tarball_is_stale
        rootfs = tmp_path / "rootfs"
        rootfs.mkdir()
        (rootfs / "etc").mkdir()
        pack = tmp_path / "1.tar.gz"
        pack.write_bytes(b"")
        assert _tarball_is_stale(rootfs, pack) is True


class TestTheBuildFailureCarriesWhicheverStreamSpoke:
    def test_stderr_only(self):
        out = subprocess.CompletedProcess([], 1, stdout="", stderr="tar: cannot open")
        assert "tar: cannot open" in _build_failure_detail(out)

    def test_stdout_only(self):
        """cp and tar report on stdout; taking only stderr produced empty errors."""
        out = subprocess.CompletedProcess([], 1, stdout="cp: cannot stat", stderr="")
        assert "cp: cannot stat" in _build_failure_detail(out)

    def test_both_streams_are_kept(self):
        out = subprocess.CompletedProcess([], 1, stdout="out-line", stderr="err-line")
        detail = _build_failure_detail(out)
        assert "out-line" in detail and "err-line" in detail

    def test_the_tail_is_kept_because_the_reason_comes_last(self):
        out = subprocess.CompletedProcess([], 1, stdout="", stderr="a" * 900 + "THE-REASON")
        assert _build_failure_detail(out).endswith("THE-REASON")

    def test_silence_is_still_a_message(self):
        out = subprocess.CompletedProcess([], 2, stdout="", stderr="")
        assert "exit 2" in _build_failure_detail(out)