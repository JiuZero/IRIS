"""The guest fallback's mutual exclusion, exercised by running the real script.

Two channels reach ``iris_net_fix``: inittab's ``::sysinit:`` (which does not
wait for the vendor rcS chain) and the tail of rcS itself (which only runs if
that chain finished). Both are wired up on purpose, so both can fire — and two
concurrent fixups would each find :80 empty and each launch goahead, leaving one
of them logging "Cannot bind to address *:80, errno 98".

The lock is the only thing preventing that, and its failure modes are invisible
from the host: a script that stands down too eagerly is indistinguishable from
one that ran, because both leave the web unreachable. So the shell functions are
sourced and driven directly rather than asserted on as text.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

NET_FIX = Path(__file__).resolve().parents[1] / "scripts" / "emulate" / "iris_net_fix.sh"

# Defining add_func makes the script take its rc.common branch (register, don't
# run), so sourcing it defines fixup/acquire_lock without executing the 45s
# fixup. LOCK_DIR is redirected at a scratch path so no /var/run is involved.
# $3 seeds a lock dir whose recorded pid belongs to a process that is genuinely
# alive for the duration of the call, because "is /proc/<pid> present" is the
# staleness test the script uses.
_SOURCE = """
add_func() { :; }
export IRIS_NET_FIX_LOCK_DIR="$1"
export IRIS_CONSOLE=/dev/null
sleep 30 &
LIVE=$!
[ -n "$3" ] && echo "$LIVE" > "$1/pid"
. "$2"
acquire_lock
rc=$?
kill "$LIVE" 2>/dev/null
wait "$LIVE" 2>/dev/null
printf 'rc=%s\\n' "$rc"
"""


def _shell() -> str:
    for candidate in (shutil.which("bash"), "C:/Program Files/Git/usr/bin/bash.exe"):
        if candidate and Path(candidate).exists():
            probe = subprocess.run([candidate, "-c", "echo ok"], capture_output=True, text=True, check=False)
            if probe.returncode == 0 and probe.stdout.strip() == "ok":
                return candidate
    pytest.skip("no working POSIX shell available to run iris_net_fix.sh")


@pytest.fixture(scope="module")
def bash() -> str:
    return _shell()


def _acquire(bash: str, lock_dir: Path, seed_owner: str | None = None) -> str:
    # POSIX form: the parent is derived from this path with sed, which only knows
    # "/" -- the guest's own paths are all absolute and slash-separated, so a
    # Windows path here would test a case that cannot occur and quietly pass for
    # the wrong reason. IRIS_CONSOLE is pointed at /dev/null for the same reason
    # the script sends it to /dev/console on-target: off-target the console cannot
    # be opened, so log() takes its documented fallback to the inherited stdout --
    # which is the very stream these tests parse "rc=" out of.
    posix = str(lock_dir).replace("\\", "/")
    proc = subprocess.run(
        [bash, "-c", _SOURCE, "_", posix, str(NET_FIX).replace("\\", "/"), seed_owner or ""],
        capture_output=True,
        text=True,
        # errors="replace": shell diagnostics from the script are bytes in the
        # host's console encoding (GBK on Windows), and a decoding error inside
        # subprocess's reader thread surfaces as an unraisable pytest warning
        # rather than as a failure of the assertion below.
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


class TestAcquireLock:
    def test_first_caller_wins_and_creates_the_lock_dir(self, bash, tmp_path):
        lock_dir = tmp_path / "var" / "run" / ".iris_net_fix.lock"
        assert not lock_dir.parent.exists(), "the parent must be created by the script too"
        assert _acquire(bash, lock_dir) == "rc=0"
        assert lock_dir.is_dir()

    def test_second_caller_stands_down(self, bash, tmp_path):
        """The regression: two goahead processes fighting over :80."""
        lock_dir = tmp_path / ".iris_net_fix.lock"
        lock_dir.mkdir()
        assert _acquire(bash, lock_dir, seed_owner="live") == "rc=1"
        assert lock_dir.is_dir(), "the holder's lock must survive"

    def test_stale_lock_from_a_dead_pid_is_reclaimed(self, bash, tmp_path):
        lock_dir = tmp_path / ".iris_net_fix.lock"
        lock_dir.mkdir()
        (lock_dir / "pid").write_text("999999", encoding="utf-8")
        assert _acquire(bash, lock_dir) == "rc=0"
        assert lock_dir.is_dir()

    def test_lock_dir_without_a_pid_file_is_honoured(self, bash, tmp_path):
        """An owner that has not written its pid yet is still an owner."""
        lock_dir = tmp_path / ".iris_net_fix.lock"
        lock_dir.mkdir()
        assert _acquire(bash, lock_dir) == "rc=1"

    def test_uncreatable_lock_dir_still_lets_the_fixup_run(self, bash, tmp_path):
        """A read-only /var/run must not silently disable the only fallback.

        mkdir fails here, and the lock dir does not exist — that is a broken
        filesystem, not a conflict. Standing down would reproduce the original
        symptom (no web at all) with no error anywhere.
        """
        blocker = tmp_path / "not-a-directory"
        blocker.write_text("", encoding="utf-8")
        assert _acquire(bash, blocker / ".iris_net_fix.lock") == "rc=0"

    def test_lock_dir_is_taken_before_any_probe_happens(self):
        """fixup must acquire first: a later probe is what two instances race on."""
        text = NET_FIX.read_text(encoding="utf-8")
        body = text.split("fixup() {", 1)[1]
        assert body.index("acquire_lock") < body.index("sleep 15")

    def test_fixup_writes_its_pid_inside_the_lock_it_took(self):
        text = NET_FIX.read_text(encoding="utf-8")
        assert 'echo $$ > "${LOCK_DIR}/pid"' in text