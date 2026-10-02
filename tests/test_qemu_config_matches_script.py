"""The QEMU device model exists twice: `qemu_config.py` and `run_qemu.sh`.

Nothing in Python assembles the command line -- the guest needs a TAP + bridge
that a user-mode `hostfwd` cannot provide, so the script does it inside the
container. That left the Python table as a *description* of the device model that
no code read, next to the thing that actually ran. A table nobody reads cannot
be kept in step by memory, and it was not: the arm64 branch of the script carries
three settings the dataclass had no field for, one of which (`-cpu max`) decides
whether the guest lives or dies on SIGILL.

So the table is now the mirror, and this test is what makes it one. It parses the
script's `case` block rather than grepping for strings, and compares field by
field, so adding an architecture to one side and not the other -- or changing
`-cpu` in one and not the other -- fails here rather than on a guest that will not
boot.

Asserting on structure rather than on exact text is deliberate: the script may
reorder assignments or wrap lines, and none of that changes the device model.
What must not change silently is the value.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from iris.emulate.qemu_config import all_configs

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "emulate" / "run_qemu.sh"
TEXT = SCRIPT.read_text(encoding="utf-8")

#: ``VAR="value"`` / ``VAR=value`` inside a case branch. The value may be empty and
#: may contain spaces, because every one of these is a command-line fragment.
_ASSIGNMENT_RE = re.compile(r'^\s*(?P<name>[A-Z_][A-Z0-9_]*)="(?P<quoted>[^"]*)"\s*$|'
                            r'^\s*(?P<name2>[A-Z_][A-Z0-9_]*)=(?P<bare>\S+)\s*$')


def _case_branches() -> dict[str, dict[str, str]]:
    """arch -> the shell variables its ``case`` branch assigns.

    Defaults set before the ``case`` (CONSOLE=ttyS0, MEMORY=256) are applied
    afterwards: a branch that does not mention them inherits the default, and
    comparing against a table that hardcodes them would flag the inheritance as a
    disagreement.
    """
    block = TEXT[TEXT.index('case "${ARCH}" in'):]
    end = block.index("\nesac")
    block = block[:end]

    defaults = dict(_assignments_in(TEXT[:TEXT.index('case "${ARCH}" in')]))
    branches: dict[str, dict[str, str]] = {}
    current: str | None = None
    for line in block.splitlines():
        branch = re.match(r"^\s{4}([A-Za-z0-9_]+)\)\s*$", line)
        if branch:
            current = branch.group(1)
            branches[current] = dict(defaults)
            continue
        if current is None:
            continue
        for name, value in _assignments_in(line):
            branches[current][name] = value
    return branches


def _assignments_in(text: str) -> list[tuple[str, str]]:
    found = []
    for line in text.splitlines():
        m = _ASSIGNMENT_RE.match(line)
        if not m:
            continue
        if m.group("name"):
            found.append((m.group("name"), m.group("quoted")))
        else:
            found.append((m.group("name2"), m.group("bare")))
    return found


@pytest.fixture(scope="module")
def branches() -> dict[str, dict[str, str]]:
    return _case_branches()


def test_the_script_and_the_table_cover_the_same_architectures(branches):
    table = set(all_configs())
    script = {a for a in branches if a != "*"}
    assert table == script, (
        f"only in Python: {sorted(table - script)}; only in run_qemu.sh: {sorted(script - table)}"
    )


def _shell_to_table(text: str) -> str:
    """Rewrite the script's shell placeholders to the table's.

    ``${IMAGE}`` and ``{image}`` name the same thing in two syntaxes; the table
    uses ``{}`` so it can be compared as a plain string, and this is the one place
    that knows the difference.
    """
    for name in ("IMAGE", "TAP_IFACE", "BINARIES", "HOST_IP"):
        text = text.replace("${" + name + "}", "{" + name.lower() + "}")
    return text


@pytest.mark.parametrize("arch", sorted(all_configs()))
def test_every_field_matches_the_script(arch, branches):
    cfg = all_configs()[arch]
    shell = branches[arch]

    def check(name, expected):
        actual = _shell_to_table(shell.get(name, ""))
        assert actual == expected, (
            f"{arch}: {name} is {expected!r} in qemu_config.py but {actual!r} in run_qemu.sh. "
            f"The script is what boots the guest, so the table is what needs fixing."
        )

    check("KERNEL", f"{{binaries}}/{cfg.kernel_file}")
    check("QEMU", cfg.qemu_binary)
    check("QEMU_MACHINE", cfg.machine)
    check("QEMU_ROOTFS", cfg.rootfs_device)
    check("QEMU_DISK", cfg.disk_args)
    check("QEMU_NET", cfg.net_args)
    check("QEMU_CPU", cfg.cpu)
    check("CONSOLE", cfg.console)
    check("MEMORY", str(cfg.memory_mb))


@pytest.mark.parametrize("arch", sorted(all_configs()))
def test_the_initramfs_matches_the_script(arch, branches):
    """The initramfs is special: run_qemu.sh stores the whole ``-initrd ...``
    fragment, not the filename, so it can only be checked when the arch has one.

    Both directions are asserted because a one-sided change is the failure this
    exists for: an arch that grows an initramfs in the script and not in the table
    (or the reverse) boots without the tools it needs, or with a table promising
    an initramfs that was never copied into the image.
    """
    cfg = all_configs()[arch]
    fragment = _shell_to_table(branches[arch].get("QEMU_INITRD", ""))
    if cfg.initramfs:
        assert f"{{binaries}}/{cfg.initramfs}" in fragment, (
            f"{arch}: qemu_config names initramfs {cfg.initramfs!r} but run_qemu.sh has {fragment!r}"
        )
    else:
        assert not fragment, (
            f"{arch}: run_qemu.sh passes {fragment!r} but qemu_config lists no initramfs"
        )


def test_the_unsupported_branch_exists():
    """A table row with no shell branch would boot nothing; a shell branch with no
    table row would never be reached. Both directions are checked above; this
    pins the failure path that makes an unknown arch an error rather than a
    silently default device model."""
    assert '"Error: Unsupported architecture' in TEXT