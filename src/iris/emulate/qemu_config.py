"""L2 QEMU configuration — per-architecture device model.

One row per architecture, mirroring the ``case`` block in
``scripts/emulate/run_qemu.sh``. The two used to be kept in step by hand, with a
docstring asking the reader to remember, and the drift was invisible: every field
here was unread by production code (``get_config`` was called for its ``None``
check alone), while the arm64 branch in the script carried three settings this
table had no column for at all — ``-cpu max``, ``console=ttyAMA0`` and an
initramfs. ``-cpu max`` in particular is not a detail: ``cortex-a72`` cannot
execute the vendor binaries' ARMv8.3 pointer-auth and the guest dies on SIGILL.

So the table is now the mirror rather than a decoration, and
``tests/test_qemu_config_matches_script.py`` compares it to the script field by
field. Adding an architecture means adding a row here and a branch there; the
test is what turns "I updated one and forgot the other" into a red build.

Placeholders use ``{}`` (``:image``, ``:tap``) rather than the shell's ``${...}`` so
the two sides can be compared as strings without normalising the shell's syntax.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QemuConfig:
    """The device model for one architecture, in the shape run_qemu.sh wants it."""

    arch: str
    qemu_binary: str
    machine: str
    kernel_file: str
    rootfs_device: str
    disk_args: str
    net_args: str
    #: Empty string means "let QEMU pick"; run_qemu.sh leaves -cpu unset.
    cpu: str = ""
    #: The kernel's console device name, appended as ``console=<name>``.
    console: str = "ttyS0"
    #: Empty means no -initrd.
    initramfs: str = ""
    memory_mb: int = 256


#: virtio-blk + virtio-net is the only combination the virt machine can use with
#: an unmodified kernel: the bus defaults to force-legacy=true, which pins every
#: device to the legacy transport.
_VIRTIO_DISK = ("-drive if=none,file={image},format=raw,id=rootfs "
                "-device virtio-blk-device,drive=rootfs")
_VIRTIO_NET = "-device virtio-net-device,netdev=net0 -netdev tap,id=net0,ifname={tap_iface},script=no,downscript=no"
#: Malta has no PCI, so IDE and the e1000 are the only options there.
_IDE_DISK = "-drive if=ide,format=raw,file={image}"
_E1000_NET = "-device e1000,netdev=net0 -netdev tap,id=net0,ifname={tap_iface},script=no,downscript=no"


_CONFIGS: dict[str, QemuConfig] = {
    "armel": QemuConfig(
        arch="armel",
        qemu_binary="qemu-system-arm",
        machine="virt",
        kernel_file="zImage.armel",
        rootfs_device="/dev/vda",
        disk_args=_VIRTIO_DISK,
        net_args=_VIRTIO_NET,
    ),
    "arm64": QemuConfig(
        arch="arm64",
        qemu_binary="qemu-system-aarch64",
        machine="virt",
        # Alpine 6.6-virt aarch64, 来源见 docs/04-快速部署.md
        kernel_file="Image.arm64",
        rootfs_device="/dev/vda",
        disk_args=_VIRTIO_DISK,
        net_args=_VIRTIO_NET,
        # "max", not cortex-a72: vendor aarch64 binaries use ARMv8.3
        # pointer-auth, which cortex-a72 TCG cannot execute (SIGILL).
        cpu="-cpu max",
        console="ttyAMA0",
        initramfs="initramfs.arm64",
        memory_mb=512,
    ),
    "mipseb": QemuConfig(
        arch="mipseb",
        qemu_binary="qemu-system-mips",
        machine="malta",
        kernel_file="vmlinux.mipseb.4",
        rootfs_device="/dev/sda",
        disk_args=_IDE_DISK,
        net_args=_E1000_NET,
    ),
    "mipsel": QemuConfig(
        arch="mipsel",
        qemu_binary="qemu-system-mipsel",
        machine="malta",
        kernel_file="vmlinux.mipsel.4",
        rootfs_device="/dev/sda",
        disk_args=_IDE_DISK,
        net_args=_E1000_NET,
    ),
}


def get_config(arch: str) -> QemuConfig | None:
    return _CONFIGS.get(arch)


def supported_archs() -> list[str]:
    return list(_CONFIGS.keys())


def all_configs() -> dict[str, QemuConfig]:
    """Every configuration, for callers that need to enumerate (reports, tests)."""
    return dict(_CONFIGS)
