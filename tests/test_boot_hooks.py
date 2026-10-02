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

import re
import shutil
import subprocess
import time
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
    # errors="replace": a backgrounded job keeps the pipe open after its parent is
    # gone and inherits the host console (GBK on Windows), so a decoding error
    # would surface as an unraisable warning from a worker thread rather than as
    # a failure of the thing under test.
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, check=False,
    )


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
            # The real launcher, not a stand-in: its whole job is resolving the
            # right directory, so a hand-written stand-in would test nothing.
            (root / "etc" / "init.d" / "iris_net_fix_bg").write_text(
                (SCRIPTS / "iris_net_fix_bg.sh").read_text(encoding="utf-8"), encoding="utf-8"
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
    def test_hook_injection_is_not_gated_on_the_architecture(self):
        """The regression: the whole boot-hook call sat inside the arm64 branch.

        Every other architecture then built an image with no sysinit hook, no rcS
        tracing and no fallback call at all, while the arm64 image was the only
        one that ever booted to a web page.
        """
        text = (SCRIPTS / "make_image.sh").read_text(encoding="utf-8")
        arm_block = text.split('if [ "${ARCH}" = "arm64" ]; then', 1)
        assert len(arm_block) == 2, "expected exactly one arm64-only block"
        assert "inject_boot_hooks.sh" not in arm_block[1]

    def test_init_d_probe_covers_the_etc_ro_layout(self):
        text = (SCRIPTS / "make_image.sh").read_text(encoding="utf-8")
        assert "etc_ro/init.d" in text


class TestRcSTailHook:
    """A second entry point for firmwares whose init never honours the inittab."""

    def test_bg_script_is_appended_to_rcs(self, run_hooks):
        root, proc = run_hooks()
        assert "appended to rcS" in proc.stdout
        rcs = (root / "etc" / "init.d" / "rcS").read_text(encoding="utf-8")
        assert "/bin/sh /etc/init.d/iris_net_fix_bg" in rcs

    def test_hook_lands_after_the_vendor_chain(self, run_hooks):
        """Tail, not head: the vendor chain has to get its chance to configure the box."""
        root, _ = run_hooks()
        rcs = (root / "etc" / "init.d" / "rcS").read_text(encoding="utf-8")
        assert rcs.index("iris_net_fix_bg") > rcs.index("start_up_run_file")

    def test_rcs_stays_valid_shell_after_the_append(self, run_hooks, sh):
        root, _ = run_hooks()
        proc = _run_sh([*sh, "-n", str(root / "etc" / "init.d" / "rcS")])
        assert proc.returncode == 0, proc.stderr

    def test_is_idempotent_across_repeated_runs(self, run_hooks, sh):
        root, _ = run_hooks()
        _run_sh([*sh, str(INJECT), str(root), str(SCRIPTS)])
        rcs = (root / "etc" / "init.d" / "rcS").read_text(encoding="utf-8")
        assert rcs.count("iris_net_fix_bg\n") == 1

    def test_skipped_when_the_bg_script_is_absent(self, run_hooks):
        _, proc = run_hooks(with_bg=False)
        assert "rcS fallback hook skipped" in proc.stdout

    def test_skipped_when_there_is_no_rcs(self, run_hooks):
        _, proc = run_hooks(rcs=None)
        assert "rcS fallback hook skipped" in proc.stdout


class TestEtcRoLayout:
    """Tenda AC15: /etc is an overlay symlink, the real tree is /etc_ro.

    Its inittab names ``/etc_ro/init.d/rcS`` directly, so a hook injected under
    /etc is written into a tree that ``cp -rf /etc_ro/* /etc/`` overwrites during
    boot — the fallback then never runs and the guest stays unreachable with no
    error anywhere.
    """

    AC15_INITTAB = "::sysinit:/etc_ro/init.d/rcS\nttyS0::respawn:/sbin/sulogin\n"
    AC15_RCS = (
        "#!/bin/sh\n"
        "mount -t ramfs none /var/\n"
        "mkdir -p /var/etc\n"
        "cp -rf /etc_ro/* /etc/\n"
    )

    @pytest.fixture()
    def etc_ro_root(self, tmp_path: Path, sh):
        root = tmp_path / "image"
        (root / "etc_ro" / "init.d").mkdir(parents=True)
        (root / "etc_ro" / "inittab").write_text(self.AC15_INITTAB, encoding="utf-8")
        (root / "etc_ro" / "init.d" / "rcS").write_text(self.AC15_RCS, encoding="utf-8")
        (root / "etc_ro" / "init.d" / "iris_net_fix_bg").write_text(
            "#!/bin/sh\n", encoding="utf-8"
        )
        proc = _run_sh([*sh, str(INJECT), str(root), str(SCRIPTS)])
        return root, proc

    def test_the_layout_is_detected_and_reported(self, etc_ro_root):
        _, proc = etc_ro_root
        assert "boot config lives under /etc_ro" in proc.stdout

    def test_sysinit_hook_names_the_etc_ro_path(self, etc_ro_root):
        """A hook pointing at /etc/init.d would exec a path the vendor never runs."""
        root, _ = etc_ro_root
        lines = (root / "etc_ro" / "inittab").read_text(encoding="utf-8").splitlines()
        assert "::sysinit:/etc_ro/init.d/iris_net_fix_bg" in lines

    def test_hook_is_inserted_before_the_vendor_sysinit_entry(self, etc_ro_root):
        root, _ = etc_ro_root
        lines = (root / "etc_ro" / "inittab").read_text(encoding="utf-8").splitlines()
        assert lines.index("::sysinit:/etc_ro/init.d/iris_net_fix_bg") < lines.index(
            "::sysinit:/etc_ro/init.d/rcS"
        )

    def test_rcs_tail_hook_also_names_the_etc_ro_path(self, etc_ro_root):
        root, _ = etc_ro_root
        rcs = (root / "etc_ro" / "init.d" / "rcS").read_text(encoding="utf-8")
        assert "/bin/sh /etc_ro/init.d/iris_net_fix_bg" in rcs

    def test_nothing_is_written_under_etc(self, etc_ro_root):
        """The overlay /etc is rebuilt from etc_ro at boot; writing there is lost."""
        root, _ = etc_ro_root
        assert not (root / "etc").exists()

    def test_a_real_etc_is_still_preferred(self, tmp_path: Path, sh):
        """Both trees present must not silently switch the guest to etc_ro."""
        root = tmp_path / "image"
        (root / "etc" / "init.d").mkdir(parents=True)
        (root / "etc" / "inittab").write_text(TES7002_INITTAB, encoding="utf-8")
        (root / "etc" / "init.d" / "rcS").write_text(TES7002_RCS, encoding="utf-8")
        (root / "etc" / "init.d" / "iris_net_fix_bg").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "etc_ro").mkdir()
        (root / "etc_ro" / "inittab").write_text(self.AC15_INITTAB, encoding="utf-8")
        proc = _run_sh([*sh, str(INJECT), str(root), str(SCRIPTS)])
        assert "boot config lives under /etc_ro" not in proc.stdout
        assert "::sysinit:/etc/init.d/iris_net_fix_bg" in (
            root / "etc" / "inittab"
        ).read_text(encoding="utf-8")

class TestBackgroundLauncher:
    """The launcher's only job is finding the fixup in whatever tree it landed in.

    It is installed into whichever tree the firmware's inittab names, and on the
    AC15 that tree is /etc_ro while /etc is an empty overlay the vendor rcS only
    populates one step later. A launcher that names /etc/init.d therefore fails at
    exactly the moment it was added to be early, which is the sysinit hook.
    """

    BG = SCRIPTS / "iris_net_fix_bg.sh"

    def _run_bg(self, sh, tree: str) -> str:
        """Install the real launcher beside a stand-in fixup, run it, report the hit."""
        root = Path(tree)
        root.mkdir(parents=True, exist_ok=True)
        (root / "marker").mkdir(exist_ok=True)
        # POSIX form: backslashes are escape characters to /bin/sh, both in the
        # redirection target and in the path the launcher has to resolve.
        posix = str(root).replace("\\", "/")
        (root / "iris_net_fix").write_text(
            f'#!/bin/sh\necho "ran from $0" > "{posix}/console"\n', encoding="utf-8"
        )
        (root / "iris_net_fix_bg").write_text(self.BG.read_text(encoding="utf-8"), encoding="utf-8")
        proc = _run_sh([*sh, "-c",
                        f'IRIS_CONSOLE="{posix}/console" "{posix}/iris_net_fix_bg"'])
        assert proc.returncode == 0, proc.stderr
        console = root / "console"
        # The launcher backgrounds the fixup, so the parent is gone before it runs.
        for _ in range(50):
            if console.exists() and console.read_text(encoding="utf-8").strip():
                return console.read_text(encoding="utf-8")
            time.sleep(0.1)
        return ""

    def test_it_finds_the_fixup_next_to_itself(self, sh, tmp_path):
        assert "ran from" in self._run_bg(sh, str(tmp_path / "etc_ro"))

    def test_the_path_it_uses_is_not_hardcoded(self):
        text = self.BG.read_text(encoding="utf-8")
        assert "/etc/init.d/iris_net_fix" not in text
        assert "${0%/*}" in text

    def test_it_does_not_need_an_external_applet(self):
        """The AC15's busybox has no dirname applet; the command fails, the
        directory collapses to empty, and the fixup is looked up at /iris_net_fix."""
        for script in (self.BG, SCRIPTS / "iris_net_fix.sh"):
            text = script.read_text(encoding="utf-8")
            assert not re.search(r"^\s*\w+=?\$\(dirname", text, re.MULTILINE), script.name
            assert "$(dirname" not in text, script.name

    def test_it_really_does_background_the_fixup(self, sh, tmp_path):
        """A foreground fixup blocks every later ::sysinit: step behind a 45s sleep."""
        root = tmp_path / "blocking"
        root.mkdir()
        posix = str(root).replace("\\", "/")
        (root / "iris_net_fix").write_text(
            f'#!/bin/sh\nsleep 30\necho done >> "{posix}/console"\n', encoding="utf-8"
        )
        (root / "iris_net_fix_bg").write_text(self.BG.read_text(encoding="utf-8"), encoding="utf-8")
        started = time.monotonic()
        proc = _run_sh([*sh, "-c",
                        f'IRIS_CONSOLE="{posix}/console" "{posix}/iris_net_fix_bg"'])
        assert proc.returncode == 0, proc.stderr
        assert time.monotonic() - started < 10, "the launcher waited for the fixup"
        # The redirect itself creates the file before the background job starts,
        # so its existence proves nothing; the fixup's first output would arrive
        # 30 seconds from now, and the launcher must be long gone by then.
        time.sleep(3)
        assert (root / "console").read_text(encoding="utf-8") == "", "the fixup ran in the foreground"
