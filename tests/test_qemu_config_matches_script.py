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

class TestKernelAssetsExistOnDisk:
    """The table names kernel assets; the file has to be there too.

    QEMU refuses a missing ``-kernel`` immediately, and ``QEMU_START_FAILED``'
s
    hint already points at the kernel asset -- so the runtime half of this is
    loud. The gap this covers is the commit-time one: adding an architecture
    means a table row, a ``case`` branch and a file under ``binaries/``, and the
    file is the one of the three with no guard -- a row naming ``zImage.armel``
    while the file was never added (or is spelled differently, or is a zero-byte
    placeholder) boots nothing and only the in-container QEMU message would say
    why.
    """

    BINARIES = Path(__file__).resolve().parents[1] / "binaries"

    def test_the_binaries_directory_itself_is_present(self):
        assert self.BINARIES.is_dir(), (
            f"{self.BINARIES} is missing; every kernel asset lives under it and "
            "the checks below are meaningless without it"
        )

    @pytest.mark.parametrize("arch", sorted(all_configs()))
    def test_every_named_kernel_file_is_a_real_nonempty_file(self, arch):
        cfg = all_configs()[arch]
        kernel = self.BINARIES / cfg.kernel_file
        assert kernel.is_file(), (
            f"{arch}: qemu_config names kernel {cfg.kernel_file!r} but "
            f"{self.BINARIES} has no such file; the guest for this arch boots nothing"
        )
        assert kernel.stat().st_size > 0, (
            f"{arch}: {cfg.kernel_file} is a zero-byte placeholder; QEMU would "
            "refuse it at boot with an error that does not name the table row"
        )

    @pytest.mark.parametrize("arch", sorted(all_configs()))
    def test_every_named_initramfs_is_a_real_nonempty_file(self, arch):
        cfg = all_configs()[arch]
        if not cfg.initramfs:
            pytest.skip(f"{arch} declares no initramfs")
        initrd = self.BINARIES / cfg.initramfs
        assert initrd.is_file() and initrd.stat().st_size > 0, (
            f"{arch}: qemu_config names initramfs {cfg.initramfs!r} but it is "
            "missing or empty under binaries/; the arm64 guest boots without the "
            "tools its init expects"
        )

    def test_no_asset_name_drifts_between_the_table_and_the_directory(self):
        """One side of this is the table, the other the directory; the diff names
        exactly which file needs adding or which row needs renaming."""
        table_names = {f"{cfg.kernel_file}" for cfg in all_configs().values()}
        table_names |= {cfg.initramfs for cfg in all_configs().values() if cfg.initramfs}
        on_disk = {p.name for p in self.BINARIES.iterdir() if p.is_file()}
        missing = table_names - on_disk
        assert not missing, (
            f"named by qemu_config but absent from binaries/: {sorted(missing)}"
        )

def test_the_deploy_doc_lists_every_kernel_asset_the_table_names():
    """The deploy doc's asset table is the fourth place an asset name lives
    (table, script, directory, doc); a doc that misses one tells the reader a
    kernel comes from nowhere, and a doc that lists one the table does not name
    advertises an asset nothing consumes."""
    doc = Path(__file__).resolve().parents[1] / "docs" / "04-快速部署.md"
    text = doc.read_text(encoding="utf-8")
    for cfg in all_configs().values():
        assert cfg.kernel_file in text, (
            f"{cfg.kernel_file} is named by qemu_config but absent from the deploy doc's asset table"
        )
        if cfg.initramfs:
            assert cfg.initramfs in text, (
                f"{cfg.initramfs} is named by qemu_config but absent from the deploy doc's asset table"
            )