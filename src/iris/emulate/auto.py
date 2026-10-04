"""One-shot firmware→emulation: L1 extract + L3 rules + arch/port picker.

Wraps:
  • `iris.extract.rootfs_extract.extract_rootfs()` to turn a firmware image into a rootfs dir
  • `iris.rules.engine.apply_rules()` to apply boot-fixes (auto unless --no-rules)
  • A simple TCP port allocator so iris emulate run <firmware.bin> can omit --port

Result: a PreparedRootfs with the repaired rootfs directory, inferred architecture,
and notes for display.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from pathlib import Path

from iris.arch import census_to_runnable
from iris.extract.rootfs_extract import _census_elfs
from iris.extract.rootfs_extract import extract_rootfs as do_extract
from iris.fsutil import safe_is_dir
from iris.rules.engine import apply_rules, load_rules


@dataclass
class PreparedRootfs:
    """Output of preparing a firmware for emulation."""

    rootfs_dir: Path
    arch: str = ""
    failure_reason: str = ""
    squashfs_path: Path | None = None
    extraction_method: str = ""
    elf_count: int = 0
    matched_rule_ids: list[str] = field(default_factory=list)
    failed_rule_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_error(cls, reason: str) -> PreparedRootfs:
        return cls(rootfs_dir=Path("."), failure_reason=reason, arch="")


def pick_host_port(preferred: int = 0, start: int = 8080, stop: int = 8199,
                   bind_host: str = "") -> int:
    """Find an unused TCP port in [start,stop); prefer preferred if given.

    ``bind_host`` has to match how the port will actually be published, and the
    two do not probe the same set of ports. Binding ``0.0.0.0`` on Windows does
    not fail when something already holds ``127.0.0.1`` on that port, so a
    loopback-only publish probed with ``0.0.0.0`` looks free and gets handed out
    twice. Pass the address the publish will use.
    """
    candidates: list[int] = []
    if preferred and start <= preferred <= stop:
        candidates.append(preferred)
    while start <= stop:
        if start not in candidates:
            candidates.append(start)
        start += 1
    for p in candidates:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind((bind_host, p))
            s.close()
            return p
        except OSError:
            pass
    raise RuntimeError(f"no free port found in [{start}, {stop}]")


#: Serial ports live in their own range so a serial allocation can never pick the
#: port a web forward is about to take, or the other way round. Both are found by
#: probing for a free port before the container is created, and a container that
#: already published one holds it for as long as it runs, so the two ranges have
#: to be disjoint rather than merely unlikely to collide.
SERIAL_PORT_RANGE = (46000, 46999)


def pick_serial_port(preferred: int = 0) -> int:
    """Find an unused TCP port for QEMU's bidirectional serial chardev.

    Probed on ``127.0.0.1`` because that is how the console is published -- and
    unlike the web forward it has to be, since a published console accepts input.
    """
    start, stop = SERIAL_PORT_RANGE
    return pick_host_port(preferred=preferred, start=start, stop=stop, bind_host="127.0.0.1")


def _pick_arch_from_counter(counter) -> str:
    """Map the dominant ELF census label to a runnable kernel label.

    ``aarch64`` is the ELF standard name while ``arm64`` is what the kernel
    assets and ``run_qemu.sh`` call it, so the mapping has to exist; without it
    an aarch64 guest is reported as an unknown architecture and never auto-selected.
    The mapping itself lives in :mod:`iris.arch` -- this used to be a private dict
    byte-identical to ``orchestrator._CENSUS_TO_RUNNABLE``, kept in step by hand.
    """
    known = {a: n for a, n in counter.items() if not a.startswith("unk(")}
    if not known:
        return ""
    dominant = max(known, key=known.get)
    return census_to_runnable(dominant)


def prepare_from_firmware(
    firmware: Path,
    scratch_dir: Path,
    arch_hint: str = "",
    apply_rules_flag: bool = True,
    rules_dir: Path | None = None,
    dry_run_rules: bool = True,  # false when auto-pipeline writes fixes
) -> PreparedRootfs:
    """Extract rootfs from a firmware file, optionally apply L3 rules, return result."""
    if not firmware.exists():
        return PreparedRootfs.from_error(f"firmware not found: {firmware}")

    try:
        result = do_extract(firmware, scratch_dir, arch_hint=arch_hint)
    except Exception as e:  # noqa: BLE001 - contract: extraction never raises, it reports
        return PreparedRootfs.from_error(f"extraction error: {e}")

    if result.rootfs_dir is None:
        return PreparedRootfs.from_error(result.failure_reason or "unknown extraction failure")

    prepared = PreparedRootfs(
        rootfs_dir=result.rootfs_dir,
        squashfs_path=result.squashfs_path,
        extraction_method=result.extraction_method or "<none>",
        elf_count=result.elf_count,
        arch=result.arch_verified or "",
        notes=[f"squashfs extracted via {result.extraction_method or 'unknown'}"],
    )

    # Infer runnable arch from ELF census; fall back to user hint only if nothing detected
    if not prepared.arch:
        prepared.arch = _pick_arch_from_counter(result.elf_archs)
    if not prepared.arch and arch_hint:
        prepared.arch = arch_hint
        prepared.notes.append(f"architecture from user hint: {arch_hint}")

    # Apply L3 rules (dry-run by default for safety; set dry_run_rules=False to write)
    if apply_rules_flag and safe_is_dir(prepared.rootfs_dir):
        rules_dir = rules_dir or Path("rules")
        if rules_dir.is_dir():
            reports = apply_rules(
                prepared.rootfs_dir,
                load_rules(rules_dir),
                dry_run=dry_run_rules,
            )
            for r in reports:
                if r.matched:
                    prepared.matched_rule_ids.append(r.rule_id)
                    for warning in r.warnings:
                        prepared.notes.append(f"rule {r.rule_id}: {warning}")
                else:
                    prepared.failed_rule_ids.append(r.rule_id)
            prepared.notes.append(
                f"L3 rules matched: {', '.join(prepared.matched_rule_ids) or 'none'}"
            )

    return prepared


def prepare_from_rootfs(
    rootfs_dir: Path,
    rules_dir: Path | None = None,
    dry_run_rules: bool = True,
) -> PreparedRootfs:
    """Wrap a pre-extracted rootfs dir for emulation.

    ``dry_run_rules`` mirrors :func:`prepare_from_firmware` and defaults to True so
    the existing ``iris emulate run <rootfs-dir>`` keeps only *reporting* which
    rules match. It is a parameter rather than a fixed value because a caller that
    received the tree over the network wants the repairs written: the tree is
    theirs, disposable, and a boot-fix that was only observed does not boot.
    """
    if not safe_is_dir(rootfs_dir):
        return PreparedRootfs.from_error(f"rootfs dir not found: {rootfs_dir}")

    prepared = PreparedRootfs(
        rootfs_dir=rootfs_dir, arch="", notes=["pre-extracted rootfs"]
    )

    # Try to infer arch from rootfs ELF census

    count, counter = _census_elfs(rootfs_dir)
    prepared.elf_count = count
    if counter:
        prepared.arch = _pick_arch_from_counter(counter)
        top_5 = ", ".join(f"{k}={v}" for k, v in counter.most_common(5))
        prepared.notes.append(f"ELF census: {count} files ({top_5})")

    # Apply L3 rules
    rules_dir = rules_dir or Path("rules")
    if rules_dir.is_dir():
        reports = apply_rules(rootfs_dir, load_rules(rules_dir), dry_run=dry_run_rules)
        for r in reports:
            if r.matched:
                prepared.matched_rule_ids.append(r.rule_id)
                for warning in r.warnings:
                    prepared.notes.append(f"rule {r.rule_id}: {warning}")
            else:
                prepared.failed_rule_ids.append(r.rule_id)

        prepared.notes.append(
            f"L3 rules matched: {', '.join(prepared.matched_rule_ids) or 'none'}"
            + ("" if dry_run_rules else " (applied)")
        )

    return prepared
