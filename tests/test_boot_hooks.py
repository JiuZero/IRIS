"""Guest boot-hook injection, exercised by running the real shell script.

``scripts/emulate/inject_boot_hooks.sh`` is what makes the guest reachable on a
firmware whose vendor boot chain never finishes. Its behaviour is invisible to
Python: the failure mode is a container that boots, reports nothing wrong, and
never listens on :80. Asserting on the *text* of the injection inside
``make_image.sh`` would pass while the ordering was wrong again — the bug already
happened twice that way (an entry appended after the blocking rcS entry, then an
entry merged into the previous line by a missing newline). So the script is run
against a synthetic ``/etc`` and the resulting inittab is inspected.

The container image runs this under Linux; the suite runs it under whatever POSIX
shell the host has (Git Bash on Windows), which is the same awk/sed/grep family.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "emulate"
INJECT = SCRIPTS / "inject_boot_hooks.sh"

TES7002_INITTAB = """\
::sysinit:/etc/init.d/rcS
::sysinit:/etc/init.d/watchdog
ttyS0::respawn:/sbin/getty -L 0 115200 ttyS0 vt100
tty1::respawn:-/bin/sh
"""

TES7002_RCS = """\
#!/bin/sh
/bin/mount -t proc proc /proc 2>/dev/null
for rc_file in /etc/init.d/rc* ; do
    [ -x "$rc_file" ] || continue
    sh $rc_file > /dev/null 2>&1
done
touch /var/run/.start_up_run_file
"""

ENTRY = "::sysinit:/etc/init.d/iris_net_fix_bg"


def _shell() -> list[str]:
    """A POSIX shell on this host, or skip: the injection is shell behaviour."""
    candidates = [shutil.which("sh"), shutil.which("bash"), "C:/Program Files/Git/usr/bin/bash.exe", "/bin/sh"]
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        probe = _run_sh([candidate, "-c", "echo ok"])
        if probe.returncode == 0 and probe.stdout.strip() == "ok":
            return [candidate]
    pytest.skip("no working POSIX shell available to run inject_boot_hooks.sh")


def _run_sh(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)


@pytest.fixture(scope="module")
def sh() -> list[str]:
    return _shell()


@pytest.fixture()
def run_hooks(sh, tmp_path: Path):
    """Build a synthetic guest /etc, run the real injection, hand back the paths."""

    def build(inittab: str | None = TES7002_INITTAB, rcs: str | None = TES7002_RCS, with_bg: bool = True):
        root = tmp_path / "image"
        (root / "etc" / "init.d").mkdir(parents=True)
        if inittab is not None:
            (root / "etc" / "inittab").write_text(inittab, encoding="utf-8")
        if rcs is not None:
            (root / "etc" / "init.d" / "rcS").write_text(rcs, encoding="utf-8")
        if with_bg:
            (root / "etc" / "init.d" / "iris_net_fix_bg").write_text(
                "#!/bin/sh\n/bin/sh /etc/init.d/iris_net_fix >> /dev/console 2>&1 &\n", encoding="utf-8"
            )
        proc = _run_sh([*sh, str(INJECT), str(root), str(SCRIPTS)])
        return root, proc

    return build


def _inittab_lines(root: Path) -> list[str]:
    return (root / "etc" / "inittab").read_text(encoding="utf-8").splitlines()


class TestSysinitHookOrdering:
    def test_entry_is_inserted_before_the_vendor_sysinit_chain(self, run_hooks):
        """The exact regression: rcS blocks forever, so the hook must precede it."""
        root, proc = run_hooks()
        assert proc.returncode == 0, proc.stderr
        lines = _inittab_lines(root)
        assert lines.index(ENTRY) < lines.index("::sysinit:/etc/init.d/rcS")

    def test_entry_is_inserted_before_the_first_sysinit_entry_only(self, run_hooks):
        root, _ = run_hooks()
        lines = _inittab_lines(root)
        # Exactly one insertion, and it precedes *both* vendor ::sysinit: entries.
        assert lines.count(ENTRY) == 1
        assert lines.index(ENTRY) < lines.index("::sysinit:/etc/init.d/watchdog")

    def test_marker_precedes_the_entry(self, run_hooks):
        root, _ = run_hooks()
        lines = _inittab_lines(root)
        assert lines[lines.index(ENTRY) - 1] == "#IRIS-NETFIX-SYSINIT"

    def test_vendor_entries_are_preserved_in_order(self, run_hooks):
        root, _ = run_hooks()
        lines = [line for line in _inittab_lines(root) if line and not line.startswith("#")]
        assert lines == [
            ENTRY,
            "::sysinit:/etc/init.d/rcS",
            "::sysinit:/etc/init.d/watchdog",
            "ttyS0::respawn:/sbin/getty -L 0 115200 ttyS0 vt100",
            "tty1::respawn:-/bin/sh",
        ]

    def test_is_idempotent_across_repeated_runs(self, run_hooks):
        """Rebuilding an image from an already-hooked tree must not stack entries."""
        root, _ = run_hooks()
        _run_sh([*_shell(), str(INJECT), str(root), str(SCRIPTS)])
        lines = _inittab_lines(root)
        assert lines.count(ENTRY) == 1
        assert lines.count("#IRIS-NETFIX-SYSINIT") == 1

    def test_prepends_when_inittab_has_no_sysinit_entry(self, run_hooks):
        root, proc = run_hooks(inittab="ttyS0::respawn:/sbin/getty\ntty1::respawn:-/bin/sh\n")
        assert proc.returncode == 0, proc.stderr
        lines = _inittab_lines(root)
        assert lines[0] == "#IRIS-NETFIX-SYSINIT"
        assert lines[1] == ENTRY
        assert "iris_net_fix_bg prepended" in proc.stdout

    def test_entry_has_no_tty_field(self, run_hooks):
        """BusyBox rejects a sysinit line that carries one; keep the 2-field form."""
        root, _ = run_hooks()
        assert ENTRY in _inittab_lines(root)

    def test_skips_when_the_bg_script_is_absent(self, run_hooks):
        root, proc = run_hooks(with_bg=False)
        assert "iris_net_fix_bg missing" in proc.stdout
        assert ENTRY not in _inittab_lines(root)

    def test_skips_when_there_is_no_inittab(self, run_hooks):
        root, proc = run_hooks(inittab=None)
        assert "sysinit hook skipped" in proc.stdout
        assert not (root / "etc" / "inittab").exists()

    def test_leaves_no_temp_files_behind(self, run_hooks):
        """mktemp writes into the image dir; a stray inittab.XXXXXX ships to the guest."""
        root, _ = run_hooks()
        leftovers = [p.name for p in (root / "etc").iterdir() if p.name.startswith("inittab.")]
        assert leftovers == []

    def test_exits_zero_on_an_unusual_inittab(self, run_hooks):
        _, proc = run_hooks(inittab="")
        assert proc.returncode == 0, proc.stderr


class TestRcsTracing:
    def test_each_rc_run_is_bracketed_by_a_console_marker(self, run_hooks):
        root, _ = run_hooks()
        rcs = (root / "etc" / "init.d" / "rcS").read_text(encoding="utf-8")
        assert 'echo "IRIS-RC: begin $rc_file" > /dev/console' in rcs
        assert 'echo "IRIS-RC: end $rc_file" > /dev/console' in rcs

    def test_redirection_is_preserved_verbatim(self, run_hooks):
        """sed reads a bare & as 'the whole match'; an unescaped one splices the
        line into `2>sh $rc_file > /dev/null 2>&11` and rcS fails to run at all."""
        root, _ = run_hooks()
        rcs = (root / "etc" / "init.d" / "rcS").read_text(encoding="utf-8")
        assert "sh $rc_file > /dev/null 2>&1" in rcs

    def test_the_traced_rcS_is_still_valid_shell(self, run_hooks, sh):
        root, _ = run_hooks()
        proc = _run_sh([*sh, "-n", str(root / "etc" / "init.d" / "rcS")])
        assert proc.returncode == 0, proc.stderr

    def test_is_idempotent_across_repeated_runs(self, run_hooks):
        root, _ = run_hooks()
        _run_sh([*_shell(), str(INJECT), str(root), str(SCRIPTS)])
        rcs = (root / "etc" / "init.d" / "rcS").read_text(encoding="utf-8")
        assert rcs.count("IRIS-RC: begin") == 1
        assert rcs.count("IRIS-RC: end") == 1

    def test_skipped_when_the_redirection_line_differs(self, run_hooks):
        root, proc = run_hooks(rcs="#!/bin/sh\nfor f in /etc/init.d/rc*; do $f; done\n")
        assert "rcS tracing skipped" in proc.stdout
        rcs = (root / "etc" / "init.d" / "rcS").read_text(encoding="utf-8")
        assert "IRIS-RC" not in rcs

    def test_skipped_when_there_is_no_rcs(self, run_hooks):
        _, proc = run_hooks(rcs=None)
        assert "rc tracing skipped" in proc.stdout

    def test_tolerates_other_redirection_targets(self, run_hooks):
        _, proc = run_hooks(rcs="#!/bin/sh\nsh $rc_file >/dev/null 2>&1\n")
        assert "rcS tracing enabled" in proc.stdout


class TestMakeImageDelegates:
    def test_make_image_calls_the_extracted_script(self):
        """The extraction must stay wired up, or the tested script is dead code."""
        text = (SCRIPTS / "make_image.sh").read_text(encoding="utf-8")
        assert "/work/scripts/inject_boot_hooks.sh" in text
        assert 'ENTRY=' not in text, "inittab injection logic is duplicated in make_image.sh"

    def test_bg_script_is_still_installed_into_init_d(self):
        text = (SCRIPTS / "make_image.sh").read_text(encoding="utf-8")
        assert "iris_net_fix_bg.sh" in text