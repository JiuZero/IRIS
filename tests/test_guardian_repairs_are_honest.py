"""Do IRIS's own repair scripts say what they actually did?

Two of the three guest-scoped repair scripts used to end with an unconditional
``echo WATCHDOG-FIX-APPLIED; exit 0`` while searching the *container* for binaries
that live in the guest. Every boot therefore recorded two successful repairs on a
container where the probe had found, and could touch, nothing at all.

The earlier tests for these could not have caught it. All of them replaced
``subprocess.run`` with a fake that returned the marker string, so a script that
printed it unconditionally passed every one of them. Mocking the transport away
leaves the thing being trusted -- the script's own output -- untested.

So the scripts are run here, for real, against a fixture rootfs, with the search
paths overridden through the environment variables they read. The Python-side
handling of what they print is then tested against those real outputs.

What the output has to carry is a count. ``n=0`` is "looked, found nothing", which
is not evidence of a healthy guest and is recorded as such; ``None`` -- no count at
all -- is "the probe never got far enough to look", and is not credited either.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from iris.monitor.ai_guardian import (
    ACTION_DIAGNOSTIC,
    ACTION_RESOURCE,
    ACTION_WATCHDOG,
    ACTION_WEB_RESTART,
    AIHealthMonitor,
)


def _shell() -> str:
    for candidate in (shutil.which("bash"), "C:/Program Files/Git/usr/bin/bash.exe"):
        if candidate and Path(candidate).exists():
            probe = subprocess.run([candidate, "-c", "echo ok"], capture_output=True, text=True, check=False)
            if probe.returncode == 0 and probe.stdout.strip() == "ok":
                return candidate
    pytest.skip("no working POSIX shell available to run the repair scripts")


@pytest.fixture(scope="module")
def bash() -> str:
    return _shell()


def _posix(path: Path | str) -> str:
    """The scripts are shell; a Windows path would be a literal that never resolves."""
    return str(path).replace("\\", "/")


def _run(bash: str, script: str, tmp_path: Path, prelude: str = "", **env: str) -> str:
    """Run a repair script for real, with the environment the script reads.

    ``prelude`` is prepended to the same shell, so a helper the script calls can be
    a function defined right there. That is how the process lookup is exercised
    without depending on this host having ``pidof`` -- the logic under test is the
    script's counting, and a missing binary would otherwise masquerade as "found
    no processes", which is the one answer this suite must never confuse with the
    other.

    Written with ``newline="\\n"`` because a CRLF shebang is not a shebang on
    Linux and this suite runs wherever pytest runs.
    """
    path = tmp_path / "repair.sh"
    path.write_text(prelude + script, encoding="utf-8", newline="\n")
    environ = dict(os.environ)
    environ.update(env)
    proc = subprocess.run(
        [bash, _posix(path)], capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=60, check=False, env=environ,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _fixture_file(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    target.write_bytes(b"#!/bin/sh\n")
    return target


def _make_symlink(directory: Path, name: str, target: str) -> Path:
    """Create a real symlink, or skip. MSYS on Windows makes a copy instead, which
    would leave the ``find -type l`` branch untested and silently reported as
    "found nothing" -- the exact failure this suite exists to distinguish from a
    genuine empty result."""
    link = directory / name
    proc = subprocess.run(
        ["ln", "-s", target, _posix(link)], capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0 or link.is_symlink() is False:
        pytest.skip("this host cannot create symlinks, so the symlink branch is untestable here")
    return link


def _monitor(tmp_path: Path) -> AIHealthMonitor:
    return AIHealthMonitor(iid=1, scratch_dir=tmp_path)


class TestTheWatchdogScriptCountsWhatItDisabled:
    def test_a_guest_with_nothing_to_disable_says_zero(self, bash, tmp_path):
        """The regression, run for real. This printed its success marker
        unconditionally, so the count of disabled binaries was never computed and
        never appeared."""
        out = _run(
            bash, AIHealthMonitor._WATCHDOG_SCRIPT, tmp_path,
            IRIS_WATCHDOG_BINARIES=_posix(tmp_path / "absent" / "monitor"),
            IRIS_WATCHDOG_DIRS=_posix(tmp_path / "absent"),
        )
        assert re.search(r"WATCHDOG-FIX-APPLIED n=0\b", out), out

    def test_a_binary_it_finds_is_disabled_and_counted(self, bash, tmp_path):
        root = tmp_path / "rootfs"
        binary = _fixture_file(root / "bin", "monitor")
        out = _run(
            bash, AIHealthMonitor._WATCHDOG_SCRIPT, tmp_path,
            IRIS_WATCHDOG_BINARIES=_posix(binary),
            IRIS_WATCHDOG_DIRS=_posix(tmp_path / "absent"),
        )
        assert re.search(r"WATCHDOG-FIX-APPLIED n=1\b", out), out
        assert not binary.exists(), "reported as disabled but left in place"
        assert (root / "bin" / "monitor.iris-disabled").exists(), out

    def test_a_second_run_finds_nothing_because_the_first_removed_it(self, bash, tmp_path):
        """A repair that cannot be repeated is not a repair. This is also the shape
        of the original bug seen from the other side: the second boot is the one
        where an unconditional marker would be the only thing left to go on."""
        root = tmp_path / "rootfs"
        binary = _fixture_file(root / "bin", "monitor")
        env = {
            "IRIS_WATCHDOG_BINARIES": _posix(binary),
            "IRIS_WATCHDOG_DIRS": _posix(tmp_path / "absent"),
        }
        first = _run(bash, AIHealthMonitor._WATCHDOG_SCRIPT, tmp_path, **env)
        second = _run(bash, AIHealthMonitor._WATCHDOG_SCRIPT, tmp_path, **env)
        assert re.search(r"n=1\b", first), first
        assert re.search(r"n=0\b", second), second

    def test_several_binaries_are_counted_separately(self, bash, tmp_path):
        root = tmp_path / "rootfs"
        first = _fixture_file(root / "bin", "monitor")
        second = _fixture_file(root / "sbin", "watchdog")
        out = _run(
            bash, AIHealthMonitor._WATCHDOG_SCRIPT, tmp_path,
            IRIS_WATCHDOG_BINARIES=f"{_posix(first)} {_posix(second)}",
            IRIS_WATCHDOG_DIRS=_posix(tmp_path / "absent"),
        )
        assert re.search(r"WATCHDOG-FIX-APPLIED n=2\b", out), out

    def test_a_move_that_fails_is_not_counted_as_disabled(self, bash, tmp_path):
        """``mv ... && _disabled=$((_disabled + 1))``, tested by making the move fail.

        Found by mutating the script into ``mv ...; _disabled=$((_disabled + 1))``
        and finding the suite still green: the counter was the only evidence the
        rename had happened, so a count that survives a failed rename is a
        success report with nothing behind it -- which is what the whole change is
        about. ``mv`` is stubbed rather than made to fail on disk because a
        read-only directory is not portable enough to trust as a fixture.
        """
        binary = _fixture_file(tmp_path / "rootfs" / "bin", "monitor")
        out = _run(
            bash, AIHealthMonitor._WATCHDOG_SCRIPT, tmp_path,
            prelude="mv() { return 1; }\n",
            IRIS_WATCHDOG_BINARIES=_posix(binary),
            IRIS_WATCHDOG_DIRS=_posix(tmp_path / "absent"),
        )
        assert re.search(r"WATCHDOG-FIX-APPLIED n=0\b", out), out
        assert binary.exists(), "counted as disabled, and the stub means it still is"

    def test_a_symlink_found_by_scanning_a_directory_is_counted(self, bash, tmp_path):
        """The vendor's monitor is usually a symlink, and the directory scan is the
        only thing that finds it -- the named paths rarely cover it."""

        root = tmp_path / "rootfs"
        (root / "bin").mkdir(parents=True, exist_ok=True)
        _make_symlink(root / "bin", "monitor", "monitor.real")
        out = _run(
            bash, AIHealthMonitor._WATCHDOG_SCRIPT, tmp_path,
            IRIS_WATCHDOG_BINARIES=_posix(tmp_path / "absent" / "monitor"),
            IRIS_WATCHDOG_DIRS=_posix(root / "bin"),
        )
        assert re.search(r"WATCHDOG-FIX-APPLIED n=1\b", out), out
        assert not (root / "bin" / "monitor").exists(), "the symlink was left in place"


class TestTheOtherTwoScriptsCountToo:
    def test_cleanup_reports_zero_when_there_is_nothing_to_kill(self, bash, tmp_path):
        """``pidof`` finds the process and reports nothing, which is the case the
        old script could not tell from success."""
        out = _run(
            bash, AIHealthMonitor._CLEANUP_SCRIPT, tmp_path,
            prelude="pidof() { :; }\n",
            IRIS_GUARDIAN_PROCESSES="iris-monitor",
        )
        assert re.search(r"RESOURCE-CLEANUP-APPLIED n=0\b", out), out

    def test_cleanup_counts_a_process_it_actually_kills(self, bash, tmp_path):
        """``kill`` is stubbed to succeed, because what is under test is the
        script's counting and not the operating system's signal delivery -- and a
        real signal is not reachable from here. Python starts ``bash.exe`` as a
        Windows process, so a background job's pid names one namespace while the
        script's ``kill`` resolves it in another; ``cygpath -p`` does not reconcile
        the two for a job. Both directions are covered below, so nothing rests on
        the stub being believable."""
        out = _run(
            bash, AIHealthMonitor._CLEANUP_SCRIPT, tmp_path,
            prelude="pidof() { echo 4242; }\nkill() { return 0; }\n",
            IRIS_GUARDIAN_PROCESSES="iris-monitor",
        )
        assert re.search(r"RESOURCE-CLEANUP-APPLIED n=1\b", out), out

    def test_cleanup_does_not_count_a_process_it_failed_to_kill(self, bash, tmp_path):
        """``kill -9 && _killed=$((_killed + 1))`` rather than counting the attempt.
        A count that included signals that never landed would put a number in the
        ledger that no ``ps`` could confirm."""
        out = _run(
            bash, AIHealthMonitor._CLEANUP_SCRIPT, tmp_path,
            prelude="pidof() { echo 999999; }\nkill() { return 1; }\n",
            IRIS_GUARDIAN_PROCESSES="iris-monitor",
        )
        assert re.search(r"RESOURCE-CLEANUP-APPLIED n=0\b", out), out

    def test_cleanup_counts_each_pid_not_each_name(self, bash, tmp_path):
        """Three ``monitord`` processes is three kills. A counter that incremented
        once per matched name would report 1 for work worth 3, and the ledger is
        read as a count of reclaimed resources."""
        out = _run(
            bash, AIHealthMonitor._CLEANUP_SCRIPT, tmp_path,
            prelude="pidof() { echo '100 101 102'; }\nkill() { return 0; }\n",
            IRIS_GUARDIAN_PROCESSES="monitord",
        )
        assert re.search(r"RESOURCE-CLEANUP-APPLIED n=3\b", out), out

    def test_cleanup_actually_reads_the_processes_it_was_configured_with(self, bash, tmp_path):
        """The regression for a misspelling, found the hard way here.

        The script defaulted ``IRIS_GUARDIAN_PROCESSES`` and then looped over
        ``IRIS_GUARDAN_PROCESSES`` -- one letter short. Unset, the typo'd name
        expanded to nothing, the loop body never ran, and the script reported
        ``n=0``: the same output as a guest with nothing to kill. The process
        names the stub was actually asked about are written down so a rename that
        stops reaching the loop fails here instead of hiding behind that
        coincidence.
        """
        asked = tmp_path / "asked.txt"
        out = _run(
            bash, AIHealthMonitor._CLEANUP_SCRIPT, tmp_path,
            prelude=f'pidof() {{ echo "$1" >> {tmp_path.as_posix()}/asked.txt; echo 7; }}\n'
                    "kill() { return 0; }\n",
            IRIS_GUARDIAN_PROCESSES="iris-monitor iris-collector",
        )
        assert re.search(r"RESOURCE-CLEANUP-APPLIED n=2\b", out), out
        assert asked.read_text(encoding="utf-8").split() == ["iris-monitor", "iris-collector"], (
            f"the script asked about {asked.read_text(encoding='utf-8').split()!r} instead of the "
            "names it was given; an unset variable is invisible here because the default "
            "expands to the same silent nothing"
        )

    def test_diag_reports_zero_when_there_is_nothing_to_disable(self, bash, tmp_path):
        out = _run(
            bash, AIHealthMonitor._DIAG_SCRIPT, tmp_path,
            IRIS_DIAG_BINARIES=_posix(tmp_path / "absent" / "diag"),
        )
        assert re.search(r"DIAG-DISABLED n=0\b", out), out

    def test_diag_counts_and_renames_what_it_finds(self, bash, tmp_path):
        binary = _fixture_file(tmp_path / "rootfs" / "bin", "diag")
        out = _run(bash, AIHealthMonitor._DIAG_SCRIPT, tmp_path, IRIS_DIAG_BINARIES=_posix(binary))
        assert re.search(r"DIAG-DISABLED n=1\b", out), out
        assert (binary.parent / "diag.iris-disabled").exists(), out

    def test_diag_does_not_count_a_rename_that_failed(self, bash, tmp_path):
        """Same shape as the watchdog's move, and the same way in: an unconditional
        increment here would report a diagnostic tool disabled while it is still
        in place and still crashing."""
        binary = _fixture_file(tmp_path / "rootfs" / "bin", "diag")
        out = _run(
            bash, AIHealthMonitor._DIAG_SCRIPT, tmp_path,
            prelude="mv() { return 1; }\n", IRIS_DIAG_BINARIES=_posix(binary),
        )
        assert re.search(r"DIAG-DISABLED n=0\b", out), out
        assert binary.exists(), out


class TestTheScriptsCannotClaimSuccessWithoutACount:
    """The shape of the defect, pinned on the text itself.

    Not a substitute for running them above -- the two tests that ran a script
    against an empty rootfs would have caught the original bug anyway -- but it
    catches a variant that happens to fail on this host's filesystem layout, which
    the run above would report as a skip.
    """

    @pytest.mark.parametrize(
        ("script", "marker"),
        [
            (AIHealthMonitor._WATCHDOG_SCRIPT, "WATCHDOG-FIX-APPLIED"),
            (AIHealthMonitor._CLEANUP_SCRIPT, "RESOURCE-CLEANUP-APPLIED"),
            (AIHealthMonitor._DIAG_SCRIPT, "DIAG-DISABLED"),
        ],
    )
    def test_the_marker_is_printed_with_a_count(self, script, marker):
        lines = [ln for ln in script.splitlines() if marker in ln]
        assert len(lines) == 1, f"{marker} is printed from {len(lines)} places"
        assert re.search(r"n=\$\{_?\w+\}", lines[0]), (
            f"{marker} is printed without a count: {lines[0]!r}"
        )

    @pytest.mark.parametrize(
        "script",
        [
            AIHealthMonitor._WATCHDOG_SCRIPT,
            AIHealthMonitor._CLEANUP_SCRIPT,
            AIHealthMonitor._DIAG_SCRIPT,
        ],
    )
    def test_no_script_prints_its_marker_outside_the_count(self, script):
        """A marker echoed on a branch that ran no action is the original bug
        wearing a different name; the only line allowed to mention it is the one
        that interpolates the count."""
        marker_lines = [
            ln for ln in script.splitlines()
            if re.search(r"echo\s+\"?[A-Z-]+-(?:APPLIED|DISABLED)", ln)
        ]
        assert all("n=" in ln for ln in marker_lines), marker_lines


class TestWhatTheGuardianDoesWithThoseCounts:
    def test_a_count_of_zero_is_not_a_repair(self, monkeypatch, tmp_path):
        m = _monitor(tmp_path)
        monkeypatch.setattr(
            AIHealthMonitor, "_exec_in_container",
            lambda self, script, timeout=30: (True, "WATCHDOG-FIX-APPLIED n=0"),
        )
        assert m._apply_watchdog_fixes() is False

    def test_a_real_count_is_a_repair(self, monkeypatch, tmp_path):
        m = _monitor(tmp_path)
        monkeypatch.setattr(
            AIHealthMonitor, "_exec_in_container",
            lambda self, script, timeout=30: (True, "WATCHDOG-FIX-APPLIED n=3"),
        )
        assert m._apply_watchdog_fixes() is True

    def test_a_marker_with_no_count_is_not_believed(self, monkeypatch, tmp_path):
        """The old format, arriving from somewhere that still emits it. Reading it
        as success would reinstate exactly the assumption this change removed."""
        m = _monitor(tmp_path)
        monkeypatch.setattr(
            AIHealthMonitor, "_exec_in_container",
            lambda self, script, timeout=30: (True, "WATCHDOG-FIX-APPLIED\n"),
        )
        assert m._apply_watchdog_fixes() is False

    def test_a_failed_probe_is_not_a_repair(self, monkeypatch, tmp_path):
        m = _monitor(tmp_path)
        monkeypatch.setattr(
            AIHealthMonitor, "_exec_in_container",
            lambda self, script, timeout=30: (True, ""),
        )
        assert m._cleanup_resources() is False

    def test_an_unrelated_number_is_not_read_as_a_count(self, monkeypatch, tmp_path):
        """stderr arrives in the same string as stdout. Without the word boundaries
        an incidental ``mon=12`` -- a docker or mount message -- parses as a count of
        twelve and credits a repair that never happened."""
        m = _monitor(tmp_path)
        monkeypatch.setattr(
            AIHealthMonitor, "_exec_in_container",
            lambda self, script, timeout=30: (
                True, "cpumon=12 is unsupported\nWATCHDOG-FIX-APPLIED",
            ),
        )
        assert m._apply_watchdog_fixes() is False

    def test_docker_failing_is_not_a_repair(self, monkeypatch, tmp_path):
        m = _monitor(tmp_path)
        monkeypatch.setattr(
            AIHealthMonitor, "_exec_in_container",
            lambda self, script, timeout=30: (False, "cannot connect to docker"),
        )
        assert m._disable_diagnostic_tools() is False

    def test_nothing_found_marks_the_action_unreachable(self, monkeypatch, tmp_path):
        m = _monitor(tmp_path)
        monkeypatch.setattr(
            AIHealthMonitor, "_exec_in_container",
            lambda self, script, timeout=30: (True, "WATCHDOG-FIX-APPLIED n=0"),
        )
        m._apply_watchdog_fixes()
        assert ACTION_WATCHDOG in m._unreachable_actions, (
            "an action that cannot change anything is still being scheduled every interval"
        )
        assert "image.raw" in m._unreachable_actions[ACTION_WATCHDOG], (
            "the reason has to say why, or the ledger entry is just 'failed'"
        )

    def test_a_successful_run_clears_the_mark(self, monkeypatch, tmp_path):
        """Otherwise a firmware that did have a fixable binary, once repaired,
        would stay permanently excluded from the recommendation list."""
        m = _monitor(tmp_path)
        m._unreachable_actions[ACTION_WATCHDOG] = "earlier probe found nothing"
        monkeypatch.setattr(
            AIHealthMonitor, "_exec_in_container",
            lambda self, script, timeout=30: (True, "WATCHDOG-FIX-APPLIED n=1"),
        )
        m._apply_watchdog_fixes()
        assert ACTION_WATCHDOG not in m._unreachable_actions


class TestAnUnreachableActionStopsBeingRecommended:
    def test_it_is_skipped_in_favour_of_the_next_candidate(self, tmp_path):
        m = _monitor(tmp_path)
        m.status.watchdog_triggers = 3
        m.status.diag_crashes = 1
        m._unreachable_actions[ACTION_WATCHDOG] = "probe cannot see the guest"
        assert m.recommend_recovery_action() == ACTION_DIAGNOSTIC

    def test_nothing_reachable_recommends_nothing_rather_than_looping(self, tmp_path):
        """Every interval, otherwise. The loop runs for the container's whole
        timeout and would fill the ledger with one identical failure."""
        m = _monitor(tmp_path)
        m.status.watchdog_triggers = 3
        m.status.soft_lockup_events = 2
        m.status.diag_crashes = 1
        for action in (ACTION_WATCHDOG, ACTION_RESOURCE, ACTION_DIAGNOSTIC):
            m._unreachable_actions[action] = "probe cannot see the guest"
        assert m.recommend_recovery_action() is None

    def test_web_actions_are_still_reachable(self, tmp_path):
        """Container-level repairs do reach a running guest -- that is what the
        restart path is for -- so they must not be caught by the same exclusion."""
        m = _monitor(tmp_path)
        m.status.web_server_status = "started_but_stopped"
        m._unreachable_actions[ACTION_WATCHDOG] = "unreachable"
        assert m.recommend_recovery_action() == ACTION_WEB_RESTART

    def test_priority_order_survives_the_refactor(self, tmp_path):
        """The candidate list replaced a chain of early returns; the order in which
        signals win is behaviour someone chose once and would not notice losing."""
        m = _monitor(tmp_path)
        m.status.watchdog_triggers = 1
        m.status.soft_lockup_events = 1
        m.status.web_server_status = "started_but_stopped"
        assert m.recommend_recovery_action() == ACTION_WATCHDOG
        m._unreachable_actions[ACTION_WATCHDOG] = "unreachable"
        assert m.recommend_recovery_action() == ACTION_RESOURCE
        m._unreachable_actions[ACTION_RESOURCE] = "unreachable"
        assert m.recommend_recovery_action() == ACTION_WEB_RESTART


class TestTheExecIsNamedForWhereItRuns:
    def test_the_docstring_does_not_claim_the_guest(self):
        """It said "inside the running container" in prose and "the guest's own exit
        status" in its return value, while the same class's `_docker` documented
        the boundary correctly. One of the two was wrong, and it was the one every
        script below it depended on."""

        def probe() -> str:
            return (AIHealthMonitor._exec_in_container.__doc__ or "").lower()

        text = probe()
        assert "container" in text, text
        assert "guest's own exit status" not in text, text
        # It has to say the boundary, not merely stop making the false claim.
        assert "image.raw" in text or "not the guest" in text, text

    def test_no_call_site_still_uses_the_old_name(self):
        source = Path(AIHealthMonitor.__module__.replace(".", "/") + ".py")
        text = (Path(__file__).parents[1] / "src" / source).read_text(encoding="utf-8")
        assert "_exec_in_guest" not in text, "the name that hid the boundary is back"