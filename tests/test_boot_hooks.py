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

import os
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

# OpenWrt (Newifi D2), the one firmware in the corpus that boots under procd.
# The `<S|K> <param>` tail on both rcS lines is what procd's own inittab parser
# requires, and it is the only thing here that separates procd from BusyBox init.
PROCD_INITTAB = """\
::sysinit:/etc/init.d/rcS S boot
::shutdown:/etc/init.d/rcS K shutdown
::askconsole:/usr/libexec/login.sh
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

    def build(
        inittab: str | None = TES7002_INITTAB,
        rcs: str | None = TES7002_RCS,
        with_bg: bool = True,
        with_rcd: bool = False,
        pre_existing_link: bool = False,
        fake_ln: bool = False,
    ):
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
        if with_rcd:
            (root / "etc" / "rc.d").mkdir()
        if pre_existing_link:
            (root / "etc" / "rc.d").mkdir(exist_ok=True)
            (root / "etc" / "rc.d" / "S99iris_net_fix").write_text(
                "#!/bin/sh\n", encoding="utf-8"
            )
        if fake_ln:
            proc = _run_sh(_with_fake_ln(sh, tmp_path, [str(INJECT), str(root), str(SCRIPTS)]))
        else:
            proc = _run_sh([*sh, str(INJECT), str(root), str(SCRIPTS)])
        return root, proc

    return build


def _ln_log(tmp_path: Path) -> list[str]:
    log = tmp_path / "ln.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _posix(path: Path | str) -> str:
    """POSIX form of a host path: `/bin/sh -c` execs its words verbatim, so a
    backslash-separated Windows path never resolves for the shell it hands them to."""
    return str(path).replace("\\", "/")


def _quoted(path: Path | str) -> str:
    """One shell word. `C:/Program Files/...` splits into two without quotes."""
    return "'" + _posix(path).replace("'", "'\\''") + "'"


def _with_fake_ln(sh: list[str], tmp_path: Path, argv: list[str]) -> list[str]:
    """Run the script with `ln` shadowed by a recording shell function.

    Windows Git Bash cannot create symlinks here at all — `ln -s` fails outright,
    and where MSYS does fall back to a shortcut, Python cannot stat it (`exists()`
    and `lexists()` both report it missing). The one thing this script now does on
    procd firmwares would be invisible to any assertion in that environment, and
    the link would fail the run under `set -e` besides.

    Shadowing is done with a function and `.` (source) rather than a stub binary on
    PATH: MSYS rewrites a `PATH=...:$PATH` assignment whose value looks like a
    Windows path list, so a prepended directory silently loses to `/usr/bin`. A
    function needs no PATH round-trip and still records the exact argv, and it
    leaves a plain file where the link would be, so the script's own idempotence
    check (`[ -e ]`) has something to find. The real symlink is exercised by the
    Linux container build and by the firmware regression, not by this host.
    """
    command = "\n".join(
        [
            (
                "ln() { printf '%s\\n' \"$*\" >> \"${IRIS_TEST_LN_LOG}\";"
                " printf '%s\\n' \"${2}\" > \"${3}\"; }"
            ),
            f"IRIS_TEST_LN_LOG={_quoted(tmp_path / 'ln.log')}",
            ". " + " ".join(_quoted(a) for a in argv),
        ]
    )
    return [*sh, "-c", command]


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

class TestProcdInittab:
    """OpenWrt, where the sysinit hook cannot be injected at all.

    procd_inittab_run() walks the action list and ``break``s after the first match
    unless the handler is flagged ``multi`` — ``sysinit`` and ``shutdown`` are not
    — so an injected entry does not run ahead of the vendor rcS entry, it takes
    its place. runrc() then refuses that entry for carrying no ``<S|K> <param>``
    tail and only logs "valid format is rcS <S|K> <param>".

    The result is a guest that boots cleanly and reaches nothing: no netifd, no
    uhttpd, and IRIS's own fallback rejected alongside the vendor chain. Measured
    on Newifi D2, and removing the injected entry from the same image made uhttpd
    bind port 80 at 37s. procd also has no rcS file to tail-hook — it walks
    /etc/rc.d/S* itself — so that is where the fallback has to go instead.
    """

    def _procd_hooks(self, run_hooks, **kwargs):
        kwargs.setdefault("rcs", None)
        kwargs.setdefault("inittab", PROCD_INITTAB)
        return run_hooks(**kwargs)

    def test_sysinit_entry_is_not_injected(self, run_hooks):
        root, proc = self._procd_hooks(run_hooks)
        assert proc.returncode == 0, proc.stderr
        lines = (root / "etc" / "inittab").read_text(encoding="utf-8").splitlines()
        assert not any("iris_net_fix" in line for line in lines)

    def test_the_vendor_sysinit_entry_stays_first(self, run_hooks):
        """The break means position is not the question here — presence is."""
        root, _ = self._procd_hooks(run_hooks)
        lines = [ln for ln in (root / "etc" / "inittab").read_text(
            encoding="utf-8").splitlines() if ln.strip()]
        assert lines == PROCD_INITTAB.strip().splitlines()

    def test_the_family_and_its_reason_are_reported(self, run_hooks):
        _, proc = self._procd_hooks(run_hooks)
        assert "is procd's" in proc.stdout
        assert "sysinit hook skipped" in proc.stdout

    def test_fallback_is_linked_onto_the_channel_procd_drives(self, run_hooks):
        root, _ = self._procd_hooks(run_hooks, with_rcd=True, fake_ln=True)
        assert _ln_log(root.parent) == [
            f"-s ../init.d/iris_net_fix {root.as_posix()}/etc/rc.d/S99iris_net_fix"
        ]

    def test_rc_d_is_created_when_the_vendor_ships_none(self, run_hooks):
        """OpenWrt always ships rc.d, but the link is what matters, not the dir."""
        root, _ = self._procd_hooks(run_hooks, fake_ln=True)
        assert _ln_log(root.parent) == [
            f"-s ../init.d/iris_net_fix {root.as_posix()}/etc/rc.d/S99iris_net_fix"
        ]

    def test_an_existing_link_is_not_created_twice(self, run_hooks):
        """make_image.sh already installs one; a second would run the fixup twice."""
        _, proc = self._procd_hooks(
            run_hooks, with_rcd=True, pre_existing_link=True, fake_ln=True
        )
        assert "already present" in proc.stdout

    def test_a_stale_busybox_entry_is_removed(self, run_hooks):
        """A rootfs hooked before it was recognised as procd must lose that entry."""
        hooked = f"#IRIS-NETFIX-SYSINIT\n{ENTRY}\n" + PROCD_INITTAB
        root, _ = self._procd_hooks(run_hooks, inittab=hooked, fake_ln=True)
        lines = (root / "etc" / "inittab").read_text(encoding="utf-8").splitlines()
        assert not any("iris_net_fix" in ln or "IRIS-NETFIX" in ln for ln in lines)

    def test_leaves_no_temp_files_behind(self, run_hooks):
        root, _ = self._procd_hooks(run_hooks)
        assert [p.name for p in (root / "etc").iterdir() if p.name.startswith("inittab.")] == []

    def test_a_long_respawn_line_is_not_read_as_procd(self, run_hooks):
        """`respawn` lines carry many fields on either init; only rcS lines count."""
        inittab = "ttyS0::respawn:/sbin/getty -L 0 115200 ttyS0 vt100\n::askconsole:/bin/sh\n"
        root, proc = run_hooks(inittab=inittab, rcs=None, fake_ln=True)
        assert "is procd's" not in proc.stdout
        assert ENTRY in (root / "etc" / "inittab").read_text(encoding="utf-8").splitlines()

    def test_a_shutdown_line_alone_still_identifies_procd(self, run_hooks):
        _, proc = run_hooks(inittab="::shutdown:/etc/init.d/rcS K shutdown\n", rcs=None)
        assert "is procd's" in proc.stdout


class TestNoInittabRcSHook:
    """A firmware with no /etc/inittab ever boots BusyBox straight into rcS.

    The sysinit channel is skipped entirely, so the rcS tail hook becomes the
    fallback's ONLY channel. Appending to the end of rcS lets a trailing handoff
    script starve it — Tenda DIR-868L's rcS ends with `/etc/init0.d/rcS`, which
    loops on `service status`, and the measured effect was `IRIS-NETFIX` 0 lines
    for a firmware whose vendor chain Had already built br0/eth0.1. In this
    layout the hook is inserted before the last non-blank, non-comment line
    instead, so vendor S??* scripts still run first and no handoff line can eat
    the fallback.
    """

    NO_INITTAB_RCS = (
        "#!/bin/sh\n"
        "for i in /etc/init.d/S??* ;do\n"
        "\t[ ! -f \"$i\" ] && continue\n"
        "\techo \"[$i]\"\n"
        "\t$i\n"
        "done\n"
        "echo \"[$0] done!\"\n"
        "/etc/init0.d/rcS\n"
    )

    def _hook(self, run_hooks, rcs: str | None = NO_INITTAB_RCS, **kw):
        kw.setdefault("inittab", None)
        kw.setdefault("rcs", rcs)
        return run_hooks(**kw)

    def test_hook_lands_before_the_trailing_handoff(self, run_hooks):
        root, proc = self._hook(run_hooks)
        assert proc.returncode == 0, proc.stderr
        rcs = (root / "etc" / "init.d" / "rcS").read_text(encoding="utf-8")
        assert rcs.index("/bin/sh /etc/init.d/iris_net_fix_bg") < rcs.index("/etc/init0.d/rcS")

    def test_the_vendor_s_chain_still_runs_first(self, run_hooks):
        """Inserted after the S??* loop, never before the vendor's own work."""
        root, _ = self._hook(run_hooks)
        rcs = (root / "etc" / "init.d" / "rcS").read_text(encoding="utf-8")
        assert rcs.index("for i in /etc/init.d/S??*") < rcs.index("iris_net_fix_bg")

    @pytest.mark.skipif(os.name == "nt", reason="Windows ACLs do not expose POSIX x bits; the exec-bit contract is exercised by the Linux container build")
    def test_rcs_stays_executable_after_the_replace(self, run_hooks):
        root, _ = self._hook(run_hooks)
        rcs = root / "etc" / "init.d" / "rcS"
        assert rcs.stat().st_mode & 0o111, "rcS lost its executable bit"

    def test_the_no_inittab_insert_branch_reapplies_the_exec_bit(self):
        """The replace moves rcS to a new inode (awk temp file + mv), which starts
        with the umask rather than the old mode; the insert branch must re-apply
        chmod +x or rcS silently stops being executable on Linux hosts."""
        text = (SCRIPTS / "inject_boot_hooks.sh").read_text(encoding="utf-8")
        insert = "inserted before the final non-comment line"
        assert insert in text, "insert branch marker missing, static guard is stale"
        branch = text[text.index(insert) - 600:text.index(insert) + 100]
        assert "chmod +x" in branch, "insert branch lost its chmod +x"

    def test_rcs_stays_valid_shell(self, run_hooks, sh):
        root, _ = self._hook(run_hooks)
        proc = _run_sh([*sh, "-n", str(root / "etc" / "init.d" / "rcS")])
        assert proc.returncode == 0, proc.stderr

    def test_is_idempotent_across_repeated_runs(self, run_hooks):
        root, _ = self._hook(run_hooks)
        _run_sh([*_shell(), str(INJECT), str(root), str(SCRIPTS)])
        rcs = (root / "etc" / "init.d" / "rcS").read_text(encoding="utf-8")
        assert rcs.count("iris_net_fix_bg") == 1

    def test_a_vendor_exit_line_is_not_starved(self, run_hooks):
        root, proc = self._hook(run_hooks, rcs="#!/bin/sh\nexit 0\n")
        rcs = (root / "etc" / "init.d" / "rcS").read_text(encoding="utf-8")
        assert rcs.index("iris_net_fix_bg") < rcs.index("exit")
        assert "inserted before the final non-comment line" in proc.stdout

    def test_an_empty_rcs_still_gets_the_hook(self, run_hooks, sh):
        root, proc = self._hook(run_hooks, rcs="")
        assert proc.returncode == 0, proc.stderr
        rcs = (root / "etc" / "init.d" / "rcS").read_text(encoding="utf-8")
        assert "iris_net_fix_bg" in rcs
        assert _run_sh([*sh, "-n", str(root / "etc" / "init.d" / "rcS")]).returncode == 0

    def test_no_temp_files_are_left_behind(self, run_hooks):
        root, _ = self._hook(run_hooks)
        leftovers = [p.name for p in (root / "etc" / "init.d").iterdir() if "rcS.iris" in p.name]
        assert leftovers == []

    def test_an_inittab_still_appends_instead_of_inserting(self, run_hooks):
        """Inserting is the no-inittab fallback; with an inittab the existing
        append behaviour (unconditional second line) is preserved."""
        root, proc = run_hooks()
        assert "appended to rcS" in proc.stdout
        rcs = (root / "etc" / "init.d" / "rcS").read_text(encoding="utf-8")
        assert rcs.index("start_up_run_file") < rcs.index("iris_net_fix_bg")


class TestBackgroundLauncher:
    """The launcher starts the fixup without blocking, and knows where it is.

    Two channels reach the fallback and they need different things from this file.
    An inittab ::sysinit: entry execs its process field verbatim, so the `&` has to
    live here rather than in the entry; and the fixup's own path has to be known,
    because the launcher is installed into whichever tree the firmware's inittab
    names — /etc_ro on the AC15, where /etc is an empty overlay the vendor rcS only
    populates one step later.

    The path is baked in at install time rather than derived from $0 at runtime.
    Both runtime ways of deriving it are unavailable on real guests: no dirname
    applet, and `${0%/*}` expanding to the empty string on the Tenda DIR-868L
    (measured: `x=/a/b/c; echo "[${x%/*}]"` printed `[]`). With $0 unusable the
    launcher looked for /iris_net_fix, exited 0, and printed nothing — a guest log
    with zero IRIS-NETFIX lines, which reads exactly like "the hook never ran".
    """

    BG = SCRIPTS / "iris_net_fix_bg.sh"
    PLACEHOLDER = "@IRIS_GUEST_INIT_D@"

    def _install(self, root: Path) -> tuple[Path, str]:
        """Write the launcher the way inject_boot_hooks.sh does, placeholder filled in."""
        guest_dir = str(root).replace("\\", "/")
        text = self.BG.read_text(encoding="utf-8").replace(self.PLACEHOLDER, guest_dir)
        assert self.PLACEHOLDER not in text, (
            "the launcher lost its install-path placeholder, so inject_boot_hooks.sh "
            "would have nothing to substitute"
        )
        path = root / "iris_net_fix_bg"
        path.write_text(text, encoding="utf-8", newline="\n")
        return path, guest_dir

    def test_it_finds_the_fixup_beside_itself(self, sh, tmp_path, monkeypatch):
        root = tmp_path / "etc_ro" / "init.d"
        root.mkdir(parents=True)
        (root / "iris_net_fix").write_text(
            f'#!/bin/sh\necho "ran from $0" > "{str(root).replace(chr(92), "/")}/console"\n',
            encoding="utf-8",
        )
        _launcher, posix = self._install(root)
        monkeypatch.setenv("IRIS_BG", f"{posix}/iris_net_fix_bg")
        proc = _run_sh([*sh, "-c", 'exec "$IRIS_BG"'])
        assert proc.returncode == 0, proc.stderr
        console = root / "console"
        for _ in range(50):
            if console.exists() and console.read_text(encoding="utf-8").strip():
                break
            time.sleep(0.1)
        assert "ran from" in console.read_text(encoding="utf-8"), (
            f"the launcher did not start the sibling in {posix}"
        )

    def test_the_install_path_is_baked_in_by_the_injector(self, sh, tmp_path):
        """The placeholder is only useful if the real injector fills it in."""
        root = tmp_path / "image"
        (root / "etc" / "init.d").mkdir(parents=True)
        (root / "etc" / "init.d" / "rcS").write_text(
            '#!/bin/sh\nfor i in /etc/init.d/S??*; do $i; done\n'
            'echo "[$0] done!"\n/etc/init0.d/rcS\n',
            encoding="utf-8", newline="\n",
        )
        (root / "etc" / "init.d" / "iris_net_fix_bg").write_text(
            self.BG.read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
        )
        proc = _run_sh([*sh, str(SCRIPTS / "inject_boot_hooks.sh"), str(root), str(SCRIPTS)])
        assert proc.returncode == 0, proc.stderr
        assert "launcher path baked into /etc/init.d" in proc.stdout, proc.stdout
        installed = (root / "etc" / "init.d" / "iris_net_fix_bg").read_text(encoding="utf-8")
        assert self.PLACEHOLDER not in installed, installed
        assert "DIR=/etc/init.d\n" in installed, installed

    def test_the_path_it_uses_is_not_hardcoded(self):
        text = self.BG.read_text(encoding="utf-8")
        assert "/etc/init.d/iris_net_fix" not in text
        assert f"DIR={self.PLACEHOLDER}" in text

    def test_it_never_parses_its_own_path_at_runtime(self):
        """`${0%/*}` is empty on the DIR-868L, and dirname(1) is absent on the AC15.

        Both failures are silent: the script keeps going with an empty directory and
        exits 0, so nothing anywhere records that the lookup was never attempted.

        Only executable lines count — the comments in both scripts quote the exact
        expansion that broke, and a guard that flagged those would have them deleted.
        """
        pattern = re.compile(r"^\s*[^#]*\$\{[A-Za-z_0-9][A-Za-z0-9_]*[%#]")
        for script in (self.BG, SCRIPTS / "iris_net_fix.sh"):
            text = script.read_text(encoding="utf-8")
            offenders = [
                f"{script.name}: {line}"
                for line in text.splitlines()
                if pattern.search(line)
            ]
            assert not offenders, (
                f"{offenders} rely on parameter expansion that measured empty on a "
                "real guest"
            )
            assert "$(dirname" not in text, script.name

    def test_it_never_asks_the_shell_to_supply_a_default(self):
        """`${VAR:-default}` and `${VAR:=default}` are the same defect, one function away.

        The substitution form of this problem was found and fixed first; the
        default-value forms were left in the script on the assumption that a shell
        which drops `${x%/*}` will still handle `${x:-/tmp}`. That assumption was
        never tested, and the failure it would cause is the same silent one: an
        unset variable stays unset, so `CONSOLE` ends up empty, the redirect to it
        fails, and the fallback writes its verdict nowhere at all.

        The costs of avoiding the syntax are one line per default and no cleverness,
        so the guard is on the syntax rather than on the resulting behaviour -- which
        cannot be checked off-target, since a working shell is exactly what the test
        host provides.
        """
        pattern = re.compile(
            r"^\s*[^#]*\$\{[A-Za-z_0-9][A-Za-z0-9_]*[:?][-=+]?\}?"
        )
        for script in (self.BG, SCRIPTS / "iris_net_fix.sh"):
            offenders = [
                f"{script.name}: {line}"
                for line in script.read_text(encoding="utf-8").splitlines()
                if pattern.search(line)
            ]
            assert not offenders, (
                f"{offenders} ask the shell for a default value, which a reduced "
                "vendor BusyBox was measured to reduce to the empty string; spell it "
                "as an explicit test instead"
            )

    def test_it_really_does_background_the_fixup(self, sh, tmp_path, monkeypatch):
        """A foreground fixup blocks every later ::sysinit: step behind a 45s sleep.

        stdout and stderr go to DEVNULL rather than being captured: the launcher does
        not redirect the fixup, so the backgrounded child inherits the pipes and a
        capturing read would wait out the child's whole 30s — measuring the pipe, not
        the backgrounding. The fixup writes its own marker file instead.
        """
        root = tmp_path / "blocking"
        root.mkdir()
        posix = str(root).replace("\\", "/")
        (root / "iris_net_fix").write_text(
            f'#!/bin/sh\nsleep 30\necho done >> "{posix}/console"\n', encoding="utf-8"
        )
        _launcher, posix = self._install(root)
        started = time.monotonic()
        monkeypatch.setenv("IRIS_BG", f"{posix}/iris_net_fix_bg")
        proc = subprocess.run(
            [*sh, "-c", 'exec "$IRIS_BG"'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=60, check=False,
        )
        assert proc.returncode == 0
        assert time.monotonic() - started < 10, "the launcher waited for the fixup"

    def test_the_launcher_marks_itself_before_spawning_the_fixup(self, sh, tmp_path, monkeypatch):
        """"The hook never ran" and "the fixup never started" are otherwise the same
        empty log, and they need different fixes. The mark is printed before the fixup
        is spawned, so its presence says which of the two happened — and it names the
        path it is about to use, which is what told the DIR-868L investigation that the
        path was the problem."""
        root = tmp_path / "mark"
        root.mkdir()
        (root / "iris_net_fix").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        _launcher, posix = self._install(root)
        monkeypatch.setenv("IRIS_BG", f"{posix}/iris_net_fix_bg")
        proc = _run_sh([*sh, "-c", 'exec "$IRIS_BG"'])
        assert proc.returncode == 0, proc.stderr
        assert "bg launcher starting" in proc.stdout, proc.stdout
        assert f"{posix}/iris_net_fix" in proc.stdout, proc.stdout

    def test_the_mark_goes_to_the_inherited_stdout(self):
        """The launcher must not open /dev/console itself.

        On the DIR-868L that open fails, and a redirection that cannot be opened takes
        its command down with it — so the redirected form printed nothing at all while
        the surrounding rcS, writing to the same console through the descriptor init
        handed it, printed fine. stdout is already that console.
        """
        text = self.BG.read_text(encoding="utf-8")
        assert "IRIS_CONSOLE" not in text, text
        assert not re.search(r'^\s*echo .*>\s*"\$\{?CONSOLE', text, re.MULTILINE), text

    def test_the_fixup_is_not_given_a_redirection_that_cannot_be_opened(self):
        """`>> /dev/console` fails outright — log() says so in the fixup itself.

        A redirection that cannot be opened takes its command down with it, so the
        backgrounded fixup would never start and the guest would get no fallback
        without a single line anywhere saying why.
        """
        text = self.BG.read_text(encoding="utf-8")
        assert not re.search(r'iris_net_fix"?\s*>>', text), text
        assert not re.search(r'iris_net_fix"?\s*2>&1', text), text
