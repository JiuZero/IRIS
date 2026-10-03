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


@pytest.mark.parametrize(
    "name", GUEST_INJECTED
)
def test_guest_injected_script_ends_with_a_newline(name: str):
    """The same failure with no error message at all.

    BusyBox ash drops a trailing line that has no newline after it: on Tenda
    DIR-868L the background launcher ended in ``/bin/sh .../iris_net_fix &`` with
    no final newline, so the line that actually starts the fixup was the one line
    the guest never ran -- and because the whole launcher is silent until that
    line's child logs something, the guest produced *zero* IRIS-NETFIX lines
    anywhere. Nothing on the host shows it either: ``sh -n`` passes, ``bash -n``
    passes, and the file ends in an ordinary-looking ``&``.

    The visible symptom was a firmware whose web server was up and listening on
    :80, reported as "the boot hooks did not run" -- pointing at hook installation,
    which was in fact correct. The hook line was reached; a later line was dropped.
    """
    full = next((PROJECT / "scripts").rglob(name))
    raw = full.read_bytes()
    assert raw, f"{name} is empty"
    assert raw.endswith(b"\n"), (
        f"{name} ends with {raw[-20:]!r} instead of a newline; BusyBox ash "
        "silently drops the last line of such a script"
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

#: The same two scripts, for the checks that are about guest-sh *parsing* rather than
#: guest-sh line endings.
GUEST_SCRIPTS = tuple(next((PROJECT / "scripts").rglob(n)) for n in GUEST_INJECTED)


@pytest.mark.parametrize("path", GUEST_SCRIPTS, ids=lambda p: p.name)
def test_guest_script_comments_carry_no_shell_metacharacters(path: Path):
    """The Tenda DIR-868L's BusyBox does not confine expansions to code.

    Measured on that firmware: a comment quoting a parameter expansion stopped the
    script before its first statement, with a success status and nothing on the
    console — and a comment quoting a backquoted command behaves the same way. The
    file was byte-for-byte correct, `sh -n` passed on the host, and the only
    difference from a working copy was inside text the shell is supposed to ignore.

That is the worst possible failure shape: the guest reports no error, the host
    reports no error, and the symptom (zero IRIS-NETFIX lines) points at boot-hook
    installation, which was correct. So the constraint is enforced here rather than
    rediscovered on the next reduced vendor build.

    The measured offenders were a backquoted command and a parameter expansion
    (``${`` and ``$(``), plus a trailing ampersand in the header comment, which
    stopped the script dead before its first statement -- reproduced one probe at a
    time on the firmware itself, not inferred. Redirection characters and non-ASCII
    text are in the list on the same reasoning rather than on measurement: a build
    that mishandles a comment containing ``${`` has no reason to stop at ``>`` or at
    an em dash, and the cost of keeping them out is only a habit of writing.
    """
    forbidden = ("`", "${", "$(", "&", "<", ">")
    offenders = []
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.lstrip().startswith("#"):
            continue
        hit = next((t for t in forbidden if t in line), None)
        non_ascii = next((c for c in line if ord(c) > 127), None)
        if hit or non_ascii:
            offenders.append(
                f"{path.name}:{i}: {line.strip()[:90]}"
                + (f" [carries {hit!r}]" if hit else "")
                + (f" [carries U+{ord(non_ascii):04X}]" if non_ascii else "")
            )
    assert not offenders, (
        f"shell metacharacters in comments, which a reduced vendor BusyBox may act on: "
        f"{offenders}"
    )
