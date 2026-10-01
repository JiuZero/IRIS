"""Guest script rendering invariants that hold regardless of the host OS.

`tests/test_rules.py::TestGuestScriptIsPosix` pins "no backslashes in the
rendered output" for synthetic rules. The shipped rules, however, legitimately
contain backslashes — POSIX line continuations and escaped `find` metacharacters
written by the rule author. This suite pins the distinction:

* every backslash in the generated script must be one of the allowed shell
  escapes (line continuation, `\\(`, `\\)`, `\\;`, escaped blank);
* everything the *host* renders into the script (verify-log path, mkdir target)
  must be a POSIX literal — that part must never derive from `Path(...)`.
"""

from __future__ import annotations

import re
from pathlib import Path

from iris.rules.engine import (
    GUEST_SCRIPT_PATH,
    VERIFY_LOG_DIR,
    VERIFY_LOG_PATH,
    apply_rules,
    load_rules,
)

PROJECT_RULES = Path(__file__).resolve().parents[1] / "rules"

#: The only backslash uses a POSIX sh script may legitimately contain.
_ALLOWED_ESCAPES = re.compile(r"\\$|\\\(|\\\)|\\;|\\ ")


def _watchdog_rootfs(tmp_path: Path) -> Path:
    """A rootfs that trips the vendor-watchdog-monitor fingerprint."""
    rootfs = tmp_path / "rootfs"
    (rootfs / "etc").mkdir(parents=True)
    (rootfs / "bin").mkdir()
    (rootfs / "etc" / "inittab").write_text("::once:-/bin/monitor\n", encoding="utf-8")
    (rootfs / "bin" / "monitor").write_bytes(b"\x7fELF fake monitor")
    return rootfs


class TestRenderedScriptEscapeHygiene:
    def test_every_backslash_is_a_legal_posix_escape(self, tmp_path):
        rootfs = _watchdog_rootfs(tmp_path)
        apply_rules(rootfs, load_rules(PROJECT_RULES), dry_run=False)
        script = (rootfs / GUEST_SCRIPT_PATH).read_text(encoding="utf-8")
        illegal = [
            line for line in script.splitlines()
            if "\\" in _ALLOWED_ESCAPES.sub("", line)
        ]
        assert illegal == [], f"host-side path leaked into guest script: {illegal}"

    def test_verify_log_target_is_the_posix_literal(self, tmp_path):
        rootfs = _watchdog_rootfs(tmp_path)
        apply_rules(rootfs, load_rules(PROJECT_RULES), dry_run=False)
        script = (rootfs / GUEST_SCRIPT_PATH).read_text(encoding="utf-8")
        assert f"mkdir -p {VERIFY_LOG_DIR} " in script
        assert VERIFY_LOG_PATH in script
        # The literal must appear verbatim; a Path()-derived variant would
        # render as \etc\scripts on Windows and be an escape to POSIX sh.
        assert "\\etc" not in script and "etcscripts" not in script

    def test_continuation_lines_reassemble_to_one_command(self, tmp_path):
        """A continuation whose next line lost its indentation would silently
        split the loop word list; pin the exact shape the rule ships."""
        rootfs = _watchdog_rootfs(tmp_path)
        apply_rules(rootfs, load_rules(PROJECT_RULES), dry_run=False)
        script = (rootfs / GUEST_SCRIPT_PATH).read_text(encoding="utf-8")
        joined = re.sub(r"\\\n\s+", " ", script)
        # After re-joining continuations the for-loop must carry every variant.
        for variant in ("/bin/monitor", "/opt/monitor", "/sbin/keep_alive",
                        "/usr/bin/keepalive", "/bin/wdt", "/bin/arp_monitor",
                        "/bin/ppp-monitor", "/etc/scripts/watchdog"):
            assert variant in joined
        assert "\\; 2>/dev/null" in joined  # find -exec terminator intact
