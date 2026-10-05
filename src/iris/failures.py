"""One closed vocabulary for "this run did not work", shared by every layer.

Extraction, arch selection, container orchestration and boot diagnosis each used
to invent their own wording: ``"unsupported-arch: ..."`` in one function,
``f"Unsupported architecture: {arch}"`` in another, and a prose sentence with no
kind at all in a third. Nothing could count them, so a dashboard built on this
data would have had to grep English -- and ``docs/eval-log.md`` claimed "8 fixed
failure stages" while the code contained three distinct literals.

The taxonomy is a closed ``StrEnum`` because the point is that it is complete:
a new failure that nobody added here shows up as an unmapped value rather than as
a new bucket nobody aggregates. Each kind carries the stage it belongs to and one
line on what to do about it, so ``iris db stats`` can print something actionable
instead of a bare slug.

``Failure.message`` keeps the ``kind: detail`` shape the old strings already had,
so every existing consumer -- CLI output, log lines, ``EmulationResult.error`` --
keeps working while the machine-readable half travels alongside it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Self

__all__ = [
    "INFORMATIONAL_KINDS",
    "STAGES",
    "BootDiagnosis",
    "Failure",
    "FailureKind",
    "Stage",
    "hint_of",
    "kind_of",
    "stage_of",
]


class Stage(StrEnum):
    """Where in the pipeline it broke. The values are what ``failure_profile.stage``
    stores, so they are also the vocabulary any dashboard groups by."""

    EXTRACTION = "extraction"
    ARCH = "arch"
    BOOT = "boot"
    NVRAM = "nvram"
    NETWORK = "network"
    SERVICE = "service"
    INFRA = "infra"


#: ``docs/eval-log.md`` used to promise ``wizard`` and ``verify`` stages as well.
#: They are gone because no code path can produce them: a run that reaches the
#: verify step either passed or produced one of the kinds below.
STAGES = tuple(Stage)


class FailureKind(StrEnum):
    """Every way a firmware can fail to come up, and nothing else."""

    # -- extraction: the rootfs could not be recovered from the image -----------
    NO_ROOTFS = "no-rootfs"
    ENCRYPTED_FIT = "encrypted-fit"
    FIT_UNSUPPORTED = "fit-unsupported"
    TENDAW_NO_JFFS2 = "tendaw-nojffs2"
    UBI_NO_SQUASHFS = "ubi-no-squashfs"

    # -- arch: no kernel can run this rootfs ------------------------------------
    UNSUPPORTED_ARCH = "unsupported-arch"
    ARCH_MISMATCH = "arch-mismatch"
    ARCH_UNDETERMINED = "arch-undetermined"

    # -- infra: docker or the image build, before the guest ever runs ------------
    TARBALL_FAILED = "tarball-failed"
    CONTAINER_CREATE_FAILED = "container-create-failed"
    CONTAINER_START_FAILED = "container-start-failed"
    COPY_TARBALL_FAILED = "copy-tarball-failed"
    IMAGE_BUILD_FAILED = "image-build-failed"
    QEMU_START_FAILED = "qemu-start-failed"

    # -- boot: the guest started and did not get far enough ---------------------
    REBOOT_LOOP = "reboot-loop"
    BOOT_HOOKS_MISSING = "boot-hooks-missing"
    SERIAL_LOG_UNAVAILABLE = "serial-log-unavailable"

    # -- kernel: the guest kernel itself stopped --------------------------------
    #: A panic the kernel could not continue past, so nothing in the guest ever
    #: came up. Kept apart from REBOOT_LOOP (a guest can panic and stop rather
    #: than ask for a reboot) and from GUEST_KERNEL_OOPS (which the guest can
    #: live through), because "no init" and "one process died" call for different
    #: work and neither is a network problem.
    GUEST_KERNEL_PANIC = "guest-kernel-panic"
    #: The kernel took an exception, dumped registers and kept running. This is
    #: not a failure and is never counted as one: a guest whose netifd died still
    #: serves its web plane, and calling that a failed run would put a working
    #: signal into the failure histogram. It is recorded because it is the reason
    #: the web plane was flaky, and a verdict that says only "web reachable" hides
    #: it from whoever has to trust that verdict.
    GUEST_KERNEL_OOPS = "guest-kernel-oops"

    # -- nvram: the flash-backed config could not be read at all ----------------
    #: Deliberately separate from REBOOT_LOOP. "The guest rebooted 28 times" and
    #: "the guest rebooted because its nvram partition reads as destroyed" call
    #: for opposite work -- waiting longer versus emulating the flash -- and a
    #: histogram that only knows about the loop sends people to wait.
    NVRAM_UNREADABLE = "nvram-unreadable"

    # -- network: the guest is up but nothing can reach it ----------------------
    NO_GUEST_IP = "no-guest-ip"
    NO_NETWORK_DRIVER = "no-network-driver"

    # -- network: measured, layer by layer, by ``emulate.linkprobe`` ------------
    #: These four name *where a packet stopped*, which is a different claim from
    #: the two above (which are read out of a boot log) and a much more specific
    #: one than ``web-unreachable``. Each is produced only when the layer was
    #: measured and measured blocked; a probe that could not run produces none
    #: of them, so an unreadable diagnostic never becomes a network diagnosis.
    LINK_NO_ROUTE = "link-no-route"
    LINK_NO_ARP = "link-no-arp"
    #: The signature of 0.3.12's DIR-868L: frames arrive and the guest answers
    #: ARP, but it drops what comes from an address outside its own subnet --
    #: which is what a router is designed to do. Guessing "the firmware is
    #: broken" here cost two rounds of wrong conclusions.
    LINK_NO_ICMP = "link-no-icmp"
    LINK_NO_SERVICE = "link-no-service"

    # -- service: the guest is fine, the web plane is not -----------------------
    WEB_NOT_STARTED = "web-not-started"
    WEB_WRONG_PORT = "web-wrong-port"
    WEB_UNREACHABLE = "web-unreachable"

    #: Not a failure: the network fallback ran as designed. A run can fail for
    #: other reasons while this holds, and counting it as ``web-unreachable``
    #: would put a working signal into the failure histogram.
    NETWORK_FALLBACK_OK = "network-fallback-ok"


_KIND_STAGE: dict[FailureKind, Stage] = {
    FailureKind.NO_ROOTFS: Stage.EXTRACTION,
    FailureKind.ENCRYPTED_FIT: Stage.EXTRACTION,
    FailureKind.FIT_UNSUPPORTED: Stage.EXTRACTION,
    FailureKind.TENDAW_NO_JFFS2: Stage.EXTRACTION,
    FailureKind.UBI_NO_SQUASHFS: Stage.EXTRACTION,
    FailureKind.UNSUPPORTED_ARCH: Stage.ARCH,
    FailureKind.ARCH_MISMATCH: Stage.ARCH,
    FailureKind.ARCH_UNDETERMINED: Stage.ARCH,
    FailureKind.TARBALL_FAILED: Stage.INFRA,
    FailureKind.CONTAINER_CREATE_FAILED: Stage.INFRA,
    FailureKind.CONTAINER_START_FAILED: Stage.INFRA,
    FailureKind.COPY_TARBALL_FAILED: Stage.INFRA,
    FailureKind.IMAGE_BUILD_FAILED: Stage.INFRA,
    FailureKind.QEMU_START_FAILED: Stage.INFRA,
    FailureKind.REBOOT_LOOP: Stage.BOOT,
    FailureKind.BOOT_HOOKS_MISSING: Stage.BOOT,
    FailureKind.SERIAL_LOG_UNAVAILABLE: Stage.BOOT,
    FailureKind.GUEST_KERNEL_PANIC: Stage.BOOT,
    FailureKind.GUEST_KERNEL_OOPS: Stage.BOOT,
    FailureKind.NVRAM_UNREADABLE: Stage.NVRAM,
    FailureKind.NO_GUEST_IP: Stage.NETWORK,
    FailureKind.NO_NETWORK_DRIVER: Stage.NETWORK,
    FailureKind.LINK_NO_ROUTE: Stage.NETWORK,
    FailureKind.LINK_NO_ARP: Stage.NETWORK,
    FailureKind.LINK_NO_ICMP: Stage.NETWORK,
    FailureKind.LINK_NO_SERVICE: Stage.NETWORK,
    FailureKind.WEB_NOT_STARTED: Stage.SERVICE,
    FailureKind.WEB_WRONG_PORT: Stage.SERVICE,
    FailureKind.WEB_UNREACHABLE: Stage.SERVICE,
    FailureKind.NETWORK_FALLBACK_OK: Stage.NETWORK,
}

#: Kinds that describe the guest working. Present in the diagnosis because the
#: prose has always reported them -- "the fallback ran" is evidence that the boot
#: hooks fired -- but excluded from every failure count.
INFORMATIONAL_KINDS = frozenset({
    FailureKind.NETWORK_FALLBACK_OK,
    FailureKind.GUEST_KERNEL_OOPS,
})

_KIND_HINT: dict[FailureKind, str] = {
    FailureKind.NO_ROOTFS: "inspect the image header; no supported container was found",
    FailureKind.ENCRYPTED_FIT: "needs the vendor decryption key; not reachable from the image alone",
    FailureKind.FIT_UNSUPPORTED: "FIT blob unpacking is not implemented",
    FailureKind.TENDAW_NO_JFFS2: "container parsed, but no mountable JFFS2 partition inside",
    FailureKind.UBI_NO_SQUASHFS: "UBI container holds no squashfs volume; needs yaffs2/cramfs support",
    FailureKind.UNSUPPORTED_ARCH: "no QEMU configuration for this architecture",
    FailureKind.ARCH_MISMATCH: "re-run with the architecture the ELF census reports",
    FailureKind.ARCH_UNDETERMINED: "pass --arch explicitly; the census found no usable ELF",
    FailureKind.TARBALL_FAILED: "packaging the rootfs failed; check the source tree",
    FailureKind.CONTAINER_CREATE_FAILED: "docker refused to create the container",
    FailureKind.CONTAINER_START_FAILED: "docker created but would not start the container",
    FailureKind.COPY_TARBALL_FAILED: "the rootfs tarball never reached the container",
    FailureKind.IMAGE_BUILD_FAILED: "make_image.sh failed; run it manually for the full log",
    FailureKind.QEMU_START_FAILED: "run_qemu.sh refused to start; check the kernel asset for this arch",
    FailureKind.REBOOT_LOOP: "the guest asks for a reboot before reaching userspace; see the serial log",
    FailureKind.BOOT_HOOKS_MISSING: "no inittab/rcS hook was found where the image build looked",
    FailureKind.SERIAL_LOG_UNAVAILABLE: "the container is gone, so the evidence is lost",
    FailureKind.GUEST_KERNEL_PANIC: "the guest kernel stopped; the log's panic line names what it could not do",
    FailureKind.GUEST_KERNEL_OOPS: "one guest process died inside the kernel; the web plane still answered, but the reason it was unreliable is here",
    FailureKind.NVRAM_UNREADABLE: "the guest could not read its flash-backed config; "
                                   "the nvram/flash device is not being emulated",
    FailureKind.NO_GUEST_IP: "nothing assigned an address in the guest; check the netfix injection",
    FailureKind.NO_NETWORK_DRIVER: "the rehost exposes no NIC this kernel can bind",
    FailureKind.LINK_NO_ROUTE: "the host has no path to the guest address; the bridge or "
                               "the assumed subnet is wrong",
    FailureKind.LINK_NO_ARP: "the path exists but the guest does not answer for its own "
                             "address; frames are not reaching it",
    FailureKind.LINK_NO_ICMP: "the guest answers ARP but ignores ICMP -- it is dropping "
                              "packets whose source address is outside its own subnet, "
                              "which is what a router does. Put the host bridge inside "
                              "the guest's subnet",
    FailureKind.LINK_NO_SERVICE: "the guest answers at IP level but nothing served the web "
                                 "port; check which port httpd actually bound",
    FailureKind.WEB_NOT_STARTED: "no known web server process ever appeared in the serial log",
    FailureKind.WEB_WRONG_PORT: "the web server bound a port the forward does not reach",
    FailureKind.WEB_UNREACHABLE: "the guest booted but :80 never answered within the timeout",
    FailureKind.NETWORK_FALLBACK_OK: "the injected network fallback ran as designed",
}


@dataclass(frozen=True)
class Failure:
    """One machine-readable kind plus the evidence that produced it."""

    kind: FailureKind
    detail: str
    #: Optional structured evidence (probe names, reboot counts, ...). Stored as
    #: JSON in ``failure_profile.detail`` so a diagnosis can be re-read later
    #: without re-running the firmware.
    #:
    #: Wrapped in a read-only mapping because ``frozen=True`` only stops the
    #: attribute from being rebound -- a plain dict would still let recorded
    #: evidence be edited in place after the fact, which is the one thing a
    #: persisted diagnosis has to forbid.
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # The proxy has to wrap a copy, not the caller's dict: a proxy over the
        # original still lets whoever built the Failure keep mutating the
        # evidence through their own reference to it.
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))

    @property
    def stage(self) -> Stage:
        return _KIND_STAGE[self.kind]

    @property
    def is_failure(self) -> bool:
        """False for the kinds that report the guest working.

        A run can fail for one reason while the network fallback worked fine, so
        counting that signal alongside real failures would dilute exactly the
        histogram this taxonomy exists to produce.
        """
        return self.kind not in INFORMATIONAL_KINDS

    @property
    def hint(self) -> str:
        return _KIND_HINT[self.kind]

    @property
    def message(self) -> str:
        """``kind: detail`` -- the shape the pre-taxonomy strings already had."""
        return f"{self.kind.value}: {self.detail}"

    def __str__(self) -> str:
        return self.message


def stage_of(kind: FailureKind) -> Stage:
    return _KIND_STAGE[kind]


def hint_of(kind: FailureKind) -> str:
    return _KIND_HINT[kind]


def kind_of(message: str) -> FailureKind | None:
    """The kind a legacy ``kind: detail`` string names, or None.

    Only for reading strings that predate the taxonomy (logs, test fixtures).
    Producers build a ``Failure`` directly; parsing prose back into a kind is how
    the vocabulary starts drifting again.
    """
    head, sep, _ = message.partition(":")
    if not sep:
        return None
    try:
        return FailureKind(head.strip())
    except ValueError:
        return None


class BootDiagnosis(str):
    """The human-readable diagnosis, carrying the findings that produced it.

    A ``str`` subclass so the twenty-odd assertions (and every log line and CLI
    message) that treat this as prose keep working unchanged, while callers that
    need to aggregate get ``.findings`` -- the same evidence as ``Failure`` objects
    instead of English. Two representations of one diagnosis, produced together,
    so they cannot disagree.
    """

    findings: tuple[Failure, ...] = ()

    def __new__(cls, prose: str, findings: tuple[Failure, ...] = ()) -> Self:
        obj = super().__new__(cls, prose)
        obj.findings = tuple(findings)
        return obj

    @property
    def primary(self) -> Failure | None:
        """The finding that names the cause, for the run's single ``result_kind``.

        The prose keeps its most-specific-first order, but "the network fallback
        ran" can legitimately be the first thing a diagnosis says -- reporting
        that as *the* cause would file a working signal as the failure. So the
        primary skips informational findings and falls back to the first one only
        when nothing else was found.
        """
        for finding in self.findings:
            if finding.is_failure:
                return finding
        return self.findings[0] if self.findings else None