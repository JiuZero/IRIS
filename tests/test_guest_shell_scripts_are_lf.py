"""Shell scripts that reach a guest must be LF, and that is not obvious here.

CRLF is completely invisible on this host. Git Bash's ``bash -n`` tolerates it,
git has ``.gitattributes`` (``*.sh text eol=lf``) to cover the checkout path --
and an editor or script that writes the file directly bypasses that attribute
entirely. BusyBox ash does not tolerate it: every assignment picks up a trailing
``\\r``, and the guest reports

    /etc/init.d/iris_net_fix: line 7:  : not found

The whole fallback then fails silently -- no IP on eth0, no web server, and a
qemu.serial.log whose only clue is four lines of ``: not found``. Two 120-second
emulations were lost to this before the cause was found, because nothing on the
host can see it and the guest's symptom points at the network, not at line
endings. Hence a test: the failure is cheap to prevent and expensive to debug.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]

#: Scripts make_image.sh copies into the guest rootfs. A CRLF in any of these
#: disables a boot-time fallback, and the guest's symptom will be "the network
#: is down" rather than "a shell script is malformed".
GUEST_INJECTED = ("iris_net_fix.sh", "iris_net_fix_bg.sh")

#: The rest still run inside the container or under Git Bash, where CRLF breaks
#: things less visibly but just as permanently.
ALL_SHELL_SCRIPTS = sorted((PROJECT / "scripts").rglob("*.sh"))


def _carriage_returns(path: Path) -> int:
    return path.read_bytes().count(b"\r\n")


@pytest.mark.parametrize(
    "path", ALL_SHELL_SCRIPTS, ids=lambda p: p.relative_to(PROJECT).as_posix()
)
def test_shell_script_has_no_crlf(path: Path):
    assert _carriage_returns(path) == 0, (
        f"{path.relative_to(PROJECT).as_posix()} has {_carriage_returns(path)} CRLF "
        "line endings; BusyBox ash fails on every one of them inside the guest"
    )


def test_the_guest_injected_scripts_are_actually_still_present():
    """Guards the list above against a rename turning these tests into a no-op."""
    for name in GUEST_INJECTED:
        found = list((PROJECT / "scripts").rglob(name))
        assert found, f"{name} is gone; guest injection and this guard both need updating"


def test_gitattributes_still_pins_shell_scripts_to_lf():
    """The checkout half of the protection. It does not cover a write that
    skips git, which is exactly how the CRLF got in -- so this test and the one
    above are both needed, and neither is redundant."""
    text = (PROJECT / ".gitattributes").read_text(encoding="utf-8")
    assert "*.sh text eol=lf" in text, text