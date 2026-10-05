"""The guest address a relaunch reads back, guarded on the end that writes it too.

A restart is the one launch nobody watches. ``docker restart`` brings back the
container's PID 1 (``sleep 3600``) and the guardian then re-runs ``run_qemu.sh``
with three arguments -- iid, arch, host port -- so the bridge address and the port
forward are derived from ``run_qemu.sh``'s own 192.168.1.1 assumption. That is fine
for a guest whose idea of "my network" is the one IRIS sets up, and it is not fine
for a router: Tenda DIR-868L brings up a LAN of its own at 192.168.0.1/24, which is
why the first boot measures the address out of the kernel's ``inet_insert_ifa``
printk and puts the host bridge inside it.

Measured consequence on that device: first boot answered HTTP 200, the guardian's
``WEB_SERVER_RESTART`` produced HTTP 000, and adding the one address the first boot
had added by hand (``ip addr add 192.168.0.254/24 dev br6630``) brought HTTP 200
straight back. The observation was real and simply was not repeated, because the
address it was made of existed only in the boot loop's memory.

So the address is now written next to the image, beside the arch marker, and
``run_qemu.sh`` prefers it over its assumption. Two ends, two languages, one path --
so the path is pinned from both sides here, and the reader is *run* rather than
asserted about: the failure this fixes is a bridge address and a forward computed
from the wrong octet, which no text comparison can distinguish from a working one.
"""

from __future__ import annotations

import inspect
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from iris.emulate import orchestrator

PROJECT = Path(__file__).resolve().parents[1]
RUN_QEMU = PROJECT / "scripts" / "emulate" / "run_qemu.sh"
MAKE_IMAGE = PROJECT / "scripts" / "emulate" / "make_image.sh"
RUN_QEMU_TEXT = RUN_QEMU.read_text(encoding="utf-8")
MAKE_IMAGE_TEXT = MAKE_IMAGE.read_text(encoding="utf-8")

#: The address the first boot measures, and the subnet a router puts its LAN on.
MEASURED = "192.168.0.1"
#: What ``run_qemu.sh`` assumes when it has been told nothing.
ASSUMED = "192.168.1.1"
#: The host address ``run_qemu.sh`` derives for either of those.
ASSUMED_HOST = "192.168.1.254"
MEASURED_HOST = "192.168.0.254"


def _shell() -> str:
    for candidate in (shutil.which("bash"), "C:/Program Files/Git/usr/bin/bash.exe"):
        if candidate and Path(candidate).exists():
            probe = subprocess.run([candidate, "-c", "echo ok"], capture_output=True, text=True, check=False)
            if probe.returncode == 0 and probe.stdout.strip() == "ok":
                return candidate
    pytest.skip("no working POSIX shell available to run run_qemu.sh")


@pytest.fixture(scope="module")
def bash() -> str:
    return _shell()


class TestTheWriter:
    """What the boot loop leaves behind for a launch that is not there yet."""

    @pytest.fixture
    def docker(self, monkeypatch):
        calls: list[list[str]] = []

        def fake_run(cmd, timeout=60):
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(orchestrator, "_run", fake_run)
        return calls

    def test_it_lands_next_to_the_image(self, docker):
        assert orchestrator._record_guest_ip("iris-qemu-6630", 6630, MEASURED) is True
        assert "/work/scratch/6630/guest_ip" in docker[0]

    def test_the_address_is_a_positional_parameter_not_script_text(self, docker):
        """The address comes out of a guest's own printk and is later fed to shell
        arithmetic. Interpolated into the ``sh -c`` text it would be the shell's to
        interpret, which is the difference between recording an address and running
        whatever a firmware chose to print."""
        orchestrator._record_guest_ip("iris-qemu-6630", 6630, MEASURED)
        argv = docker[0]
        assert MEASURED in argv, argv
        script = argv[argv.index("-c") + 1]
        assert MEASURED not in script
        assert "$1" in script and "$2" in script, script

    @pytest.mark.parametrize("junk", [
        "", "192.168.0", "192.168.0.1.5", "192.168.0.256", "192.168.0.-1",
        "not-an-address", "192.168.0.1 extra", "1.2.3.4/24", "١٩٢.168.0.1",
        "19².168.0.1", "192.168.0.1\n",
    ])
    def test_it_refuses_anything_that_is_not_a_dotted_quad(self, docker, junk):
        assert orchestrator._record_guest_ip("iris-qemu-6630", 6630, junk) is False
        assert docker == []

    def test_it_refuses_a_loopback_address(self, docker):
        """The detection filter already drops ``127.``; the writer holds the line on
        its own because a bridge placed for loopback is a bridge that cannot reach
        anything, and the file it writes is trusted by the next launch."""
        assert orchestrator._record_guest_ip("iris-qemu-6630", 6630, "127.0.0.1") is False
        assert docker == []

    def test_a_write_that_docker_refuses_is_not_a_success(self, monkeypatch):
        monkeypatch.setattr(orchestrator, "_run",
                            lambda cmd, timeout=60: subprocess.CompletedProcess(cmd, 1, "", "no such file"))
        assert orchestrator._record_guest_ip("iris-qemu-6630", 6630, MEASURED) is False

    def test_the_same_validator_guards_the_bridge_placement(self):
        """One definition of "an address", or the two drift and the second one is the
        stricter, which is how a subnet ends up half-validated."""
        source = inspect.getsource(orchestrator._host_address_on_guest_subnet)
        assert "_is_dotted_quad(guest_ip)" in source
        assert "isdigit" not in source

    def test_only_a_forward_that_was_established_is_recorded(self):
        """The marker tells the next launch which address the host bridge pointed at,
        so it belongs to the branch that built a forward to that address and to nothing
        else. It used to be gated on ``detected != guest_ip`` instead: the file whose
        entire job is to stop the next launch from assuming was written only when the
        assumption turned out to be wrong, so a guest sitting on the assumed address --
        iid 6715, which then had no forward and answered 000 for its whole timeout --
        got no marker at all."""
        loop = inspect.getsource(orchestrator._emulate_firmware)
        marker = "_record_guest_ip(container_name, iid, forwarded_to)"
        call = loop.index(marker)
        branch = loop.rindex("if (target := _forward_target(", 0, call)
        assert "detected != guest_ip" not in loop[branch:call]
        assert marker in loop[branch:call + len(marker)]


class TestTheReader:
    """``run_qemu.sh`` run for real, with the network tools replaced by recorders."""

    #: Stubs as exported shell functions: run_qemu.sh is a script, not a sourced
    #: library, so the replacements have to survive into a child bash -- which is
    #: what `export -f` is for. Function stubs rather than files on PATH because a
    #: Windows-hosted exec bit is not something to depend on.
    _PRELUDE = """
_record() { echo "$*" >> "$IRIS_STUB_LOG"; }
ip() { _record ip "$@"; }
tunctl() { _record tunctl "$@"; }
brctl() { _record brctl "$@"; }
socat() { _record socat "$@"; }
e2fsck() { _record e2fsck "$@"; }
qemu-system-arm() { _record qemu-system-arm "$@"; }
export -f _record ip tunctl brctl socat e2fsck qemu-system-arm
"""

    @pytest.fixture
    def launch(self, bash, tmp_path):
        def run(marker: str | None = None, guest_ip: str = "") -> tuple[int, str, list[str]]:
            # WORK_DIR is the one thing the script hardcodes that a test host cannot
            # write to; everything under test -- the resolution, the awk arithmetic
            # and the forward -- is the script's own text.
            scratch = tmp_path / "scratch" / "6630"
            scratch.mkdir(parents=True)
            (scratch / "image.raw").write_bytes(b"not really a disk")
            if marker is not None:
                (scratch / "guest_ip").write_text(marker, encoding="utf-8")

            anchor = 'WORK_DIR=/work/scratch/${IID}'
            assert RUN_QEMU_TEXT.count(anchor) == 1, \
                f"the harness rewrites exactly one WORK_DIR, found {RUN_QEMU_TEXT.count(anchor)}"
            script = tmp_path / "run_qemu.sh"
            script.write_text(RUN_QEMU_TEXT.replace(anchor, f'WORK_DIR="{_posix(tmp_path / "scratch")}/${{IID}}"'),
                              encoding="utf-8", newline="\n")

            log = tmp_path / "stub.log"
            log.write_text("", encoding="utf-8")
            proc = subprocess.run(
                [bash, "-c", self._PRELUDE + 'bash "$1" "$2" "$3" "${4}" "${5}"',
                 "_", _posix(script), "6630", "armel", "8080", guest_ip],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                env={**os.environ, "IRIS_STUB_LOG": _posix(log),
                     # run_qemu.sh refuses to launch without a published console
                     # port, and this harness is a caller: it has to supply one the
                     # way the orchestrator does. QEMU is stubbed, so nothing binds
                     # it -- the value only has to be a legal port.
                     "IRIS_SERIAL_PORT": "46000"},
                timeout=120, check=False,
            )
            return proc.returncode, proc.stdout + proc.stderr, log.read_text(encoding="utf-8").splitlines()
        return run

    def test_a_recorded_address_moves_the_bridge_onto_the_guest_subnet(self, launch):
        rc, out, log = launch(marker=f"{MEASURED}\n")
        assert rc == 0, out
        assert f"ip addr add {MEASURED_HOST}/16 dev br6630" in log, log

    def test_and_the_port_forward_with_it(self, launch):
        """The bridge is only half of it. A forward aimed at the assumed address is a
        connection refused by timeout even from a bridge that is inside the guest's
        subnet, which is what the restarted device actually showed.

        Read from the script's own echo rather than the stub's log: socat is started
        with ``&`` and nothing waits for it, so a backgrounded recorder would be a
        race, and the echo is printed by the parent shell."""
        rc, out, _log = launch(marker=f"{MEASURED}\n")
        assert rc == 0, out
        assert f"forwarding :8080 -> {MEASURED}:80" in out, out

    def test_with_nothing_recorded_the_first_boot_assumption_still_stands(self, launch):
        """The first boot has no marker -- it is what produces one -- so the fallback
        is the path every guest takes today and must not move."""
        rc, out, log = launch()
        assert rc == 0, out
        assert f"ip addr add {ASSUMED_HOST}/16 dev br6630" in log, log
        assert f"forwarding :8080 -> {ASSUMED}:80" in out, out

    def test_a_corrupt_marker_falls_back_instead_of_failing_the_launch(self, launch):
        """`set -e` plus an ``awk`` that rejects the line would abort the launch, so a
        hand-edited or half-written file would cost the guest an emulation instead of
        costing it an address."""
        rc, out, log = launch(marker="192.168.0\n")
        assert rc == 0, out
        assert f"ip addr add {ASSUMED_HOST}/16 dev br6630" in log, log
        assert "WARNING" in out, out

    def test_an_out_of_range_octet_is_refused_too(self, launch):
        rc, out, log = launch(marker="192.168.0.999\n")
        assert rc == 0, out
        assert f"ip addr add {ASSUMED_HOST}/16 dev br6630" in log, log

    def test_a_trailing_second_line_is_ignored(self, launch):
        rc, out, log = launch(marker=f"{MEASURED}\n10.0.0.9\n")
        assert rc == 0, out
        assert f"ip addr add {MEASURED_HOST}/16 dev br6630" in log, log

    def test_a_marker_saved_from_windows_is_refused_not_obeyed(self, launch):
        """A hand-edited file arrives with CRLF, and the carriage return rides into the
        last octet. The assumption is the safe answer; `192.168.0.254/16` on the bridge
        with a forward pointed at a name nothing answers to is not."""
        rc, out, log = launch(marker=f"{MEASURED}\r\n")
        assert rc == 0, out
        assert f"ip addr add {ASSUMED_HOST}/16 dev br6630" in log, log

    def test_an_explicit_argument_still_wins(self, launch):
        """A caller that already knows where the guest is -- the orchestrator on the
        first boot, a human with the answer in front of them -- is not second-guessed
        by a file left over from an earlier boot."""
        rc, out, log = launch(marker=f"{MEASURED}\n", guest_ip="10.9.9.1")
        assert rc == 0, out
        assert "ip addr add 10.9.9.254/16 dev br6630" in log, log
        assert "forwarding :8080 -> 10.9.9.1:80" in out, out


class TestTheTwoEndsAgree:
    """One path, two languages, neither able to check the other at runtime.

    Renaming the marker on one side leaves a script that reads a file nobody writes
    or a writer whose file nobody reads, and both look exactly like the old
    behaviour: a restart that assumes 192.168.1.1.
    """

    def _written(self) -> str:
        calls: list[list[str]] = []
        original = orchestrator._run
        try:
            orchestrator._run = lambda cmd, timeout=60: (
                calls.append(list(cmd)), subprocess.CompletedProcess(cmd, 0, "", ""))[1]
            orchestrator._record_guest_ip("iris-qemu-6630", 6630, MEASURED)
        finally:
            orchestrator._run = original
        return next(a for a in calls[0] if "guest_ip" in a)

    def test_the_script_reads_the_path_python_writes(self):
        marker = re.search(r"^GUEST_IP_MARKER=(\S+)$", RUN_QEMU_TEXT, re.MULTILINE)
        assert marker, "run_qemu.sh no longer resolves the guest address from a marker"
        read = (marker.group(1).replace("${WORK_DIR}", "/work/scratch/${IID}")
                             .replace("${IID}", "6630"))
        assert read == self._written()

    def test_the_marker_sits_beside_the_image_not_inside_it(self):
        """``${WORK_DIR}`` is the directory holding ``image.raw``; a path built from
        ``${IMAGE}`` would be a path inside the guest's filesystem, read by the host's
        own tooling -- the disk, not the directory beside it."""
        assert re.search(r"^GUEST_IP_MARKER=\$\{WORK_DIR\}/guest_ip$", RUN_QEMU_TEXT, re.MULTILINE)


class TestTheRebakeDropsIt:
    def test_a_rebake_removes_the_recorded_address(self):
        """``run_qemu.sh`` only creates state when it is missing, and it would read
        this address just as happily: an address measured on the previous image
        describes a network the new firmware does not have."""
        assert 'rm -f "${WORK_DIR}/guest_ip"' in MAKE_IMAGE_TEXT

    def test_and_it_is_removed_with_the_state_disk_not_before_the_build(self):
        body = MAKE_IMAGE_TEXT.split('rm -f "${WORK_DIR}/state.raw"', 1)[1]
        assert 'rm -f "${WORK_DIR}/guest_ip"' in body.split("rm -rf", 1)[0]


def _posix(path: Path) -> str:
    """The guest and the shell are POSIX; a Windows path is a literal that never
    resolves."""
    return str(path).replace("\\", "/")