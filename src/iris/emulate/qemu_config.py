"""L2 QEMU configuration — per-architecture emulator selection.

Maps IRIS architecture strings to QEMU system emulators, machine types,
disk interfaces, and network devices. Adapted from FirmAE's run scripts.

This module answers *which* emulator to use. The actual command line is assembled
by ``scripts/emulate/run_qemu.sh`` inside the emulation container, because the
guest needs a TAP + bridge network that a user-mode ``hostfwd`` here cannot
provide; keep ``_CONFIGS`` and that script in step when adding an architecture.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QemuConfig:
    arch: str
    qemu_binary: str
    machine: str
    kernel_file: str
    rootfs_device: str
    disk_args: str
    net_device: str
    net_backend: str
    memory_mb: int = 256


_CONFIGS: dict[str, QemuConfig] = {
    "armel": QemuConfig(
        arch="armel",
        qemu_binary="qemu-system-arm",
        machine="virt",
        kernel_file="zImage.armel",
        rootfs_device="/dev/vda",
        disk_args="-drive if=none,file={image},format=raw,id=rootfs -device virtio-blk-device,drive=rootfs",
        net_device="virtio-net-device",
        net_backend="virtio-net-device",
    ),
    "arm64": QemuConfig(
        arch="arm64",
        qemu_binary="qemu-system-aarch64",
        machine="virt",
        kernel_file="Image.arm64",  # Alpine 6.6-virt aarch64, 来源见 docs/04-快速部署.md
        rootfs_device="/dev/vda",
        disk_args="-drive if=none,file={image},format=raw,id=rootfs -device virtio-blk-device,drive=rootfs",
        net_device="virtio-net-device",
        net_backend="virtio-net-device",
        memory_mb=512,
    ),
    "mipseb": QemuConfig(
        arch="mipseb",
        qemu_binary="qemu-system-mips",
        machine="malta",
        kernel_file="vmlinux.mipseb.4",
        rootfs_device="/dev/sda",
        disk_args="-drive if=ide,format=raw,file={image}",
        net_device="e1000",
        net_backend="e1000",
    ),
    "mipsel": QemuConfig(
        arch="mipsel",
        qemu_binary="qemu-system-mipsel",
        machine="malta",
        kernel_file="vmlinux.mipsel.4",
        rootfs_device="/dev/sda",
        disk_args="-drive if=ide,format=raw,file={image}",
        net_device="e1000",
        net_backend="e1000",
    ),
}


def get_config(arch: str) -> QemuConfig | None:
    return _CONFIGS.get(arch)


def supported_archs() -> list[str]:
    return list(_CONFIGS.keys())
