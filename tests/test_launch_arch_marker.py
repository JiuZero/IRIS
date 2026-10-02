"""The launch-arch marker that lets a restart re-run QEMU, guarded on both ends.

``WEB_SERVER_RESTART`` used to be a no-op dressed as a repair: ``docker restart``
brought back PID 1 (``sleep 3600``) and nothing else, because QEMU had been
launched separately with ``docker exec -d``. Re-running it needs the arch, and the
container had no record of it -- so ``make_image.sh`` now writes one next to the
image it builds, and the guardian reads it back.

That makes two independent copies of one path: a shell script and a Python string.
A disagreement between them is invisible at every level below this file -- the unit
tests fake ``docker``, the guardian logs an error and returns ``False``, and the
user sees a repair that silently does nothing while the log stays quiet about why.
The contract is therefore pinned statically here: no Docker required, because the
agreement between the two ends *is* the thing under test.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from iris.monitor.ai_guardian import AIHealthMonitor

PROJECT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT / "scripts" / "emulate"
MAKE_IMAGE = SCRIPTS / "make_image.sh"
RUN_QEMU = SCRIPTS / "run_qemu.sh"
GUARDIAN_SRC = PROJECT / "src" / "iris" / "monitor" / "ai_guardian.py"

MAKE_IMAGE_TEXT = MAKE_IMAGE.read_text(encoding="utf-8")
RUN_QEMU_TEXT = RUN_QEMU.read_text(encoding="utf-8")
GUARDIAN_TEXT = GUARDIAN_SRC.read_text(encoding="utf-8")

#: Where the guardian looks for the marker: make_image.sh's ``${WORK_DIR}/arch``
#: with the shell's ``${IID}`` rendered as the f-string ``{self.iid}``.
MARKER_IN_GUEST = "/work/scratch/{self.iid}/arch"


def _shell() -> str:
    for candidate in (shutil.which("bash"), "C:/Program Files/Git/usr/bin/bash.exe"):
        if candidate and Path(candidate).exists():
            probe = subprocess.run([candidate, "-c", "echo ok"], capture_output=True, text=True, check=False)
            if probe.returncode == 0 and probe.stdout.strip() == "ok":
                return candidate
    pytest.skip("no working POSIX shell available")


@pytest.fixture(scope="module")
def bash() -> str:
    return _shell()


def _run_qemu_positional() -> list[str]:
    """The shell variables ``run_qemu.sh`` binds, in the order they consume $N.

    Handles both spellings the script uses -- ``IID=$1`` and
    ``HOST_PORT=${3:-8080}`` -- because reading only the braced form silently
    drops the first two arguments and shifts every position after them.
    """
    bound: dict[str, str] = {}
    pattern = r"^(\w+)=\$\{(\d)(?::-[^}]*)?\}$|^(\w+)=\$(\d)$"
    for braced_name, braced_arg, bare_name, bare_arg in re.findall(pattern, RUN_QEMU_TEXT, re.MULTILINE):
        bound[braced_arg or bare_arg] = braced_name or bare_name
    return [bound[k] for k in sorted(bound, key=int)]


class TestWriter:
    def test_the_arch_is_written_next_to_the_image(self):
        assert re.search(r'printf .*\$\{ARCH\}.*>\s*"\$\{WORK_DIR\}/arch"', MAKE_IMAGE_TEXT), \
            "make_image.sh must record ${ARCH} into ${WORK_DIR}/arch"

    def test_it_writes_the_arch_and_not_the_image_path(self):
        """The marker is a file *beside* image.raw. Redirecting to ${IMAGE}
        instead would be read back as the arch and handed to qemu-system-<arch>."""
        assert not re.search(r'>\s*"\$\{IMAGE\}"', MAKE_IMAGE_TEXT)

    def test_the_directory_is_created_first(self):
        """The orchestrator mkdir's the scratch dir before copying the tarball in,
        but make_image.sh must not depend on the order of another caller."""
        assert re.search(r'mkdir -p "\$\{WORK_DIR\}"', MAKE_IMAGE_TEXT)

    def test_the_marker_is_written_before_the_image_is_built(self):
        """Written after the build, it would be missing on every early failure --
        and those are exactly the cases the guardian should be able to retry."""
        assert MAKE_IMAGE_TEXT.index('> "${WORK_DIR}/arch"') < MAKE_IMAGE_TEXT.index("qemu-img create")

    def test_the_reason_is_recorded_next_to_the_write(self):
        """A bare `> arch` is the kind of line a future reader deletes as noise."""
        block = MAKE_IMAGE_TEXT[:MAKE_IMAGE_TEXT.index('> "${WORK_DIR}/arch"')]
        assert "entrypoint" in block and "WEB_SERVER_RESTART" in block


class TestReaderMatchesWriter:
    def test_the_scripts_work_dir_is_still_where_the_guardian_looks(self):
        work_dir = re.search(r"^WORK_DIR=(\S+)$", MAKE_IMAGE_TEXT, re.MULTILINE)
        assert work_dir, "make_image.sh no longer defines WORK_DIR on a bare assignment line"
        # The two ends disagree by syntax, not by accident: the shell writes
        # /work/scratch/$IID/arch, the guardian reads the f-string equivalent.
        assert work_dir.group(1).replace("${IID}", "{self.iid}") + "/arch" == MARKER_IN_GUEST

    def test_the_file_the_script_actually_writes_is_the_one_the_guardian_reads(self):
        """Derived from the script's own redirect rather than restated, so
        renaming the marker on either side is a failing test."""
        target = re.search(r'printf .*>\s*"(\$\{WORK_DIR\}/[^"]+)"', MAKE_IMAGE_TEXT)
        assert target, "make_image.sh no longer writes a marker below WORK_DIR"
        written = target.group(1).replace("${WORK_DIR}", "/work/scratch/${IID}") \
                            .replace("${IID}", "{self.iid}")
        assert written == MARKER_IN_GUEST

    def test_the_guardian_reads_exactly_that_path(self):
        assert MARKER_IN_GUEST in GUARDIAN_TEXT, \
            f"guardian no longer reads {MARKER_IN_GUEST}, which is what make_image.sh writes"

    def test_the_marker_is_read_before_qemu_is_relaunched(self):
        """Re-launching with an empty arch falls into run_qemu.sh's `*)` branch and
        exits 1, so the read has to gate the exec -- not merely accompany it."""
        assert GUARDIAN_TEXT.index("_read_launch_arch()") < \
            GUARDIAN_TEXT.index('"-d", name, "bash", "/work/scripts/run_qemu.sh"')

    def test_the_relaunch_cannot_proceed_without_an_arch(self):
        """The recovery is only honest if it fails loudly when it cannot relaunch."""
        body = GUARDIAN_TEXT.split("def _restart_container", 1)[1].split("\n    def ", 1)[0]
        assert "if not arch:" in body and "return False" in body


class TestRelaunchArgumentOrder:
    """``run_qemu.sh`` consumes ``$1=iid $2=arch $3=port``; a permutation exits 1
    with "Unsupported architecture: <iid>", which names neither the cause nor the
    fix. The order is therefore derived from the script, not restated here."""

    def test_run_qemu_consumes_iid_then_arch_then_port(self):
        assert _run_qemu_positional()[:3] == ["IID", "ARCH", "HOST_PORT"]

    def test_the_guardian_relaunches_in_that_order(self, tmp_path, monkeypatch):
        calls: list[list[str]] = []

        def fake_docker(args, timeout=60):
            calls.append(list(args))
            return True, ("armel\n" if "cat" in args else "")

        (tmp_path / "1").mkdir()
        monitor = AIHealthMonitor(iid=1, scratch_dir=tmp_path, http_probe_port=8080,
                                  restart_verify_seconds=1)
        monkeypatch.setattr(monitor, "_docker", fake_docker)
        monkeypatch.setattr(monitor, "probe_http", lambda port, timeout=5: True)

        assert monitor._restart_container(verify_port=8080, verify_seconds=1) is True

        argv = next(a for a in calls if "run_qemu.sh" in " ".join(a))
        tail = argv[argv.index("/work/scripts/run_qemu.sh") + 1:]
        assert tail == ["1", "armel", "8080"], argv

    def test_the_orchestrator_uses_the_same_order(self):
        """The guardian copies this call from the orchestrator; if the two ever
        disagree, one of them boots the wrong machine without saying so."""
        source = (PROJECT / "src" / "iris" / "emulate" / "orchestrator.py").read_text(encoding="utf-8")
        calls = re.findall(r'"bash", "/work/scripts/run_qemu\.sh".*$', source, re.MULTILINE)
        assert len(calls) == 1, calls
        assert calls[0].strip().endswith('str(iid), arch, str(host_port)],')


class TestScriptSyntax:
    def test_make_image_parses(self, bash):
        proc = subprocess.run([bash, "-n", str(MAKE_IMAGE)], capture_output=True, text=True, check=False)
        assert proc.returncode == 0, proc.stderr

    def test_the_script_stays_strict(self):
        assert MAKE_IMAGE_TEXT.splitlines()[1] == "set -e"

    def test_the_edited_script_invalidates_the_baked_tag(self):
        """``Dockerfile.baked`` copies these scripts into the image, so an edit that
        did not move the fingerprint would never reach a container -- which reads
        exactly like the marker not working."""
        from iris.emulate.orchestrator import _baked_scripts_fingerprint

        assert _baked_scripts_fingerprint()