"""L2 emulation orchestrator — end-to-end firmware rehosting pipeline.

Uses docker cp + docker exec to avoid Windows volume mount issues.
Pipeline:
  1. Take extracted rootfs directory (from L1)
  2. Create rootfs tarball via Docker container
  3. Build ext2 QEMU disk image inside privileged container
  4. Start QEMU with port forwarding for web access
  5. Check network reachability (curl) via port forwarding
  6. Report results
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from iris.arch import census_to_runnable, is_little_endian, normalize_arch
from iris.emulate.auto import pick_serial_port
from iris.emulate.linkprobe import LinkProfile, probe_link, render_table
from iris.emulate.qemu_config import get_config, supported_archs
from iris.failures import BootDiagnosis, Failure, FailureKind
from iris.fsutil import safe_is_file, safe_present, safe_read_text, safe_stat_size
from iris.log import StatusLine, get_logger

logger = get_logger(__name__)

#: How many kernel reboot requests a guest may make before IRIS stops waiting.
#:
#: Some firmware never finishes booting at all: AC15's cfmd segfaults within
#: seconds of every start, its own SIGSEGV handler reboots the guest, and the
#: re-entered init never reaches a shell — 28 reboot calls inside two seconds.
#: Nothing about that improves with more waiting, so the loop is treated as the
#: failure it is instead of spending the whole boot timeout to report "HTTP 000".
REBOOT_LOOP_THRESHOLD = 3

#: Web servers IRIS knows how to recognise in a serial log. Kept in step with the
#: guest-side list in iris_net_fix.sh's web_running(); a server this module
#: cannot name is one it cannot report as "running but on the wrong port".
_KNOWN_WEB_SERVERS = ("goahead", "boa", "lighttpd", "uhttpd", "thttpd", "nginx", "httpd")

#: Where the serial port published for each running emulation lives.
#:
#: A published port is not derivable from anything already stored: the database
#: records the instance but not which host port its console ended up on, and
#: guessing it from the instance id would collide as soon as two runs overlapped.
#: So it is kept in memory for exactly as long as the container it points at --
#: added when the console is published, dropped by ``stop_emulation``. That makes
#: it the same lifetime as the process-owned runtime state the web console reads
#: to answer "which instance is running", which is also why it is not a column:
#: after a restart there is no console to connect to anyway, only a leftover row.
_SERIAL_PORTS: dict[int, int] = {}


def serial_port_of(iid: int) -> int | None:
    """The host port this instance's serial console is published on, if any."""
    return _SERIAL_PORTS.get(iid)

#: Driver names whose registration means the guest really has a network device.
#: Absence of these plus absence of any eth* mention is what separates "the NIC
#: never appeared" from "the NIC is fine and nobody gave it an address".
_NET_DRIVER_HINTS = ("virtio_net", "e1000", "rtl8139", "pcnet32", "ne2k", "8139cp", "tulip")

#: An eth* name on its own proves nothing: firmwares print ``nvram_set: wan_ifname
#: = "eth0"`` while their config is being read, long before any interface exists.
#: Only a mention that reports something *about* the interface counts.
#:
#: The ``device ethN`` and ``dev:ethN`` forms are the ones a rehosted router actually
#: produces. Tenda DIR-868L was reported here as "no network driver registered in
#: the guest" while its log carried all of these -- the bridge enslaving the VLAN
#: subinterface, both interfaces entering promiscuous mode, and 8021q installing its
#: hardware filter. A diagnosis that contradicts the log it was derived from sends
#: whoever reads it after a NIC that does not exist.
_NIC_PRESENT = re.compile(
    r"^\s*(?:\[[\d.]+\]\s+)?eth\d+:"      # kernel probe: "eth0: link becomes ready"
    r"|eth\d+:\s+Link encap"               # ifconfig
    r"|\bdev eth\d+\b"                     # addrconf / "ip addr add"
    r"|\bdevice eth\d+(?:\.\d+)?\b"        # "... entered promiscuous mode", 8021q filter
    r"|\bdev:eth\d+(?:\.\d+)?\b"           # "br_add_if: br:br0 dev:eth0.1"
    r"|IRIS-NETFIX:.*\beth\d",             # the guest-side fallback saw it
    re.MULTILINE,
)

#: ...unless the same line says the interface is not there, which is the one form
#: of these that argues the opposite. Without this the promiscuous-mode rule would
#: read "device eth0 not found" as proof of a NIC.
_NIC_ABSENT = re.compile(r"eth\d+(?:\.\d+)?\)?\s*(?:not found|no such device)", re.IGNORECASE)

#: Any of these means some interface other than loopback holds an IP address.
#: ``inet_insert_ifa`` is a kernel printk that busybox-platform firmwares rarely
#: emit at all, so relying on it alone would report "nobody assigned an address"
#: even for a guest whose network came up fine — checked instead for the two
#: signals that do show up: the guest-side fallback's own report, and ifconfig.
#:
#: The first form matches FirmAE's own rewording of that printk, which inserts the
#: calling process between the symbol and its arguments:
#: ``firmadyne: __inet_insert_ifa[PID: 10045 (ip)]: device:br0 ifa:0x0100a8c0``.
#: Matching only the bare ``inet_insert_ifa: dev X`` spelling missed every one of
#: those lines, so a guest that had configured 192.168.0.1 on its bridge was
#: reported as never having been given an address.
_HAS_NON_LO_IP = re.compile(
    r"inet_insert_ifa\[[^\]]*\]:\s*device:(?!lo\b)\S+"
    r"|inet_insert_ifa:\s*dev\s+(?!lo\b)"
    r"|IRIS-NETFIX:\s*final:\s+(?!lo\b)\S+\s+up with IP"
    r"|inet addr:(?!127\.0\.0\.1)\d"
)


#: (log fragment, cause) for the reboot triggers seen in the wild. A guest that
#: reboots in a loop is not broken in one way: the AC15 detects its own nvram
#: partition as destroyed and reboots on purpose, while a different firmware
#: reboots because a daemon segfaulted into a reboot call. Only the first is a
#: missing device; the second is a vendor bug and rm /sbin/reboot does not stop
#: either, because both go through reboot(2).
_REBOOT_TRIGGERS = (
    ("nvram partition is destory", (
        "the guest found its nvram partition unreadable and reboots on purpose: its flash "
        "partitions are not being emulated"
    )),
    ("envram_init: read flash error", (
        "reading the emulated flash failed, so the guest restores nvram from defaults and reboots"
    )),
    ("Could not open mtd device", (
        "an mtd device the guest needs is absent, so its flash-backed config cannot be read"
    )),
    ("recv segv signals and reboot", (
        "a vendor daemon segfaulted into a reboot call: its own bug, and no amount of removing "
        "the reboot binary reaches it"
    )),
)

#: The subset of triggers that mean the flash-backed config could not be read,
#: as opposed to a vendor bug. Only these justify a separate `nvram` finding:
#: "the guest rebooted" and "the guest rebooted because its nvram is gone" call
#: for completely different work, and the reboot loop alone hides which.
_NVRAM_TRIGGERS = frozenset({
    "nvram partition is destory",
    "envram_init: read flash error",
    "Could not open mtd device",
})


def _boot_findings(serial_log: str, *, reboots: int) -> list[Failure]:
    """One ``Failure`` per distinct way the guest failed, most specific first.

    Ordered most-specific first: the earliest link in the chain to break is the
    one worth reporting, because everything after it is downstream. Each probe
    states what was checked, so a wrong guess shows up as a missing probe rather
    than as a confident wrong answer.
    """
    findings: list[Failure] = []

    if reboots >= REBOOT_LOOP_THRESHOLD:
        cause = next((why for marker, why in _REBOOT_TRIGGERS if marker in serial_log), None)
        findings.append(Failure(
            FailureKind.REBOOT_LOOP,
            f"guest requested a kernel reboot {reboots} times and never reached a "
            f"usable userspace"
            + (f", because {cause}" if cause else "for a reason the log does not name"),
            evidence={"reboots": reboots, "trigger": cause or ""},
        ))
        marker = next((m for m, _ in _REBOOT_TRIGGERS if m in serial_log), "")
        if marker in _NVRAM_TRIGGERS:
            # The reboot loop is the symptom; this is the device that is missing.
            # Reporting only the loop would send whoever reads the histogram after
            # "wait longer" / "remove the reboot binary", neither of which can work.
            # The detail names the log marker rather than repeating the cause, which
            # the loop finding has already spelled out.
            findings.append(Failure(
                FailureKind.NVRAM_UNREADABLE,
                f"the guest's flash-backed config could not be read (log: {marker!r}), "
                "so it reboots on purpose instead of booting",
                evidence={"log_marker": marker},
            ))

    netfix = serial_log.count("IRIS-NETFIX:")
    if netfix == 0:
        findings.append(Failure(
            FailureKind.BOOT_HOOKS_MISSING,
            "IRIS network fallback never logged a single line: the boot hooks did "
            "not run (inittab/rcS not found where the image build looked)",
            evidence={"netfix_lines": 0},
        ))
    else:
        findings.append(Failure(
            FailureKind.NETWORK_FALLBACK_OK,
            f"IRIS network fallback ran ({netfix} log lines)",
            evidence={"netfix_lines": netfix},
        ))

    if not _HAS_NON_LO_IP.search(serial_log):
        findings.append(Failure(
            FailureKind.NO_GUEST_IP,
            "no non-loopback address was ever assigned in the guest: nothing "
            "configured an IP, so the forwarded port had nothing to forward to",
        ))

    has_nic = any(hint in serial_log for hint in _NET_DRIVER_HINTS) \
        or any(_NIC_PRESENT.search(line) and not _NIC_ABSENT.search(line)
               for line in serial_log.splitlines())
    if not has_nic:
        findings.append(Failure(
            FailureKind.NO_NETWORK_DRIVER,
            "no network driver registered in the guest (no virtio/e1000/rtl8139 "
            "activity and no eth* interface): the rehost has no NIC to forward to",
        ))

    running = [s for s in _KNOWN_WEB_SERVERS if re.search(rf"\b{s}\b", serial_log)]
    if running:
        binds = re.findall(r"inet_bind\[PID: \d+ \(([^)]+)\)\]: proto:SOCK_STREAM, port:(\d+)", serial_log)
        detail = ", ".join(f"{proc or '?'}:{port}" for proc, port in dict.fromkeys(binds)) or "port unknown"
        findings.append(Failure(
            FailureKind.WEB_WRONG_PORT,
            f"a web server did start ({', '.join(running)}) but bound {detail} — "
            f"anything other than :80 is unreachable through the forward",
            evidence={"servers": running,
                      "binds": [f"{proc}:{port}" for proc, port in dict.fromkeys(binds)]},
        ))
    else:
        findings.append(Failure(
            FailureKind.WEB_NOT_STARTED,
            "no known web server process ever started in the guest",
        ))

    return findings


def diagnose_boot_failure(serial_log: str, *, reboots: int = 0) -> BootDiagnosis:
    """Name the most likely reason a guest never served :80, from its serial log.

    "HTTP 000" is a symptom; the operator is left to grep six thousand lines of
    boot output by hand to find the cause. Every probe below maps to one distinct
    way this fails, and each states what was checked — so a wrong guess shows up
    as a missing probe rather than as a confident wrong answer, and the next run
    of the same firmware produces the same diagnosis to diff against.

    The return value is the same sentence callers have always received; the
    ``.findings`` behind it are the machine-readable half, produced by the same
    pass so the two cannot drift apart.
    """
    findings = _boot_findings(serial_log, reboots=reboots)
    prose = "emulation failed: " + "; ".join(f.detail for f in findings) + "."
    return BootDiagnosis(prose, tuple(findings))


def _count_guest_reboots(container_name: str, iid: int) -> int:
    """How many times the guest has asked the kernel to reboot so far.

    `grep -c` exits 1 on zero matches, so the count is read from stdout and any
    parse failure is reported as zero: a diagnostic that cannot run must not be
    mistaken for a detected reboot loop.
    """
    res = subprocess.run(
        ["docker", "exec", container_name, "grep", "-ac", "firmadyne: sys_reboot",
         f"/work/scratch/{iid}/qemu.serial.log"],
        capture_output=True, text=True, env=_env(), timeout=10, check=False,
    )
    try:
        return int(res.stdout.strip() or 0)
    except ValueError:
        return 0


def _failure_diagnosis(container_name: str, iid: int, *, reboots: int = 0) -> BootDiagnosis:
    """Pull the guest serial log out of the container and diagnose from it."""
    res = subprocess.run(
        ["docker", "exec", container_name, "cat", f"/work/scratch/{iid}/qemu.serial.log"],
        capture_output=True, text=True, env=_env(), timeout=30, check=False,
    )
    if res.returncode != 0:
        reason = (res.stderr.strip() or "container gone")
        return BootDiagnosis(
            f"emulation failed: guest serial log unavailable ({reason}); "
            f"guest reboots: {reboots}",
            (Failure(FailureKind.SERIAL_LOG_UNAVAILABLE,
                     f"guest serial log unavailable ({reason}); guest reboots: {reboots}",
                     evidence={"reboots": reboots, "stderr": reason}),),
        )
    return diagnose_boot_failure(res.stdout, reboots=reboots)


def preflight_arch(rootfs_dir: Path, arch: str) -> Failure | None:
    """Validate the requested arch before spinning up docker.

    Returns None when the emulation may proceed, else the structured failure:
      UNSUPPORTED_ARCH: requested arch has no QEMU config
      ARCH_MISMATCH:    rootfs ELF census disagrees with the requested arch
    """
    arch = normalize_arch(arch)
    supported = supported_archs()
    if arch not in supported:
        return Failure(
            FailureKind.UNSUPPORTED_ARCH,
            f"'{arch}' has no QEMU config (supported: {', '.join(supported)})",
            evidence={"requested": arch, "supported": list(supported)},
        )

    from iris.extract.rootfs_extract import _census_elfs

    _count, counter = _census_elfs(Path(rootfs_dir))
    known = {a: n for a, n in counter.items() if not a.startswith("unk(")}
    if not known:
        return None  # no ELF evidence (script-only rootfs etc.) — can't judge
    dominant = max(known, key=known.get)
    runnable = census_to_runnable(dominant)
    if not runnable:
        # A real architecture with no kernel here -- armeb, mips64, x64. Starting
        # it anyway fails as "the emulator is broken" rather than "this arch is
        # unsupported", and the user has no way to tell the two apart from the
        # outside. Refusing with the reason is the recoverable answer.
        return Failure(
            FailureKind.UNSUPPORTED_ARCH,
            f"rootfs is dominated by {dominant} ELFs ({known[dominant]} samples), "
            f"and no kernel for that architecture exists "
            f"(supported: {', '.join(supported)})",
            evidence={"dominant": dominant, "samples": known[dominant],
                      "supported": list(supported)},
        )
    if runnable != arch:
        return Failure(
            FailureKind.ARCH_MISMATCH,
            f"rootfs is dominated by {dominant} ELFs "
            f"({known[dominant]} samples), which cannot run under the '{arch}' kernel; "
            f"use --arch {runnable}",
            evidence={"dominant": dominant, "samples": known[dominant],
                      "requested": arch, "suggested": runnable},
        )
    return None


@dataclass
class EmulationResult:
    """Outcome of one emulation run.

    ``web_ok`` is the reachability verdict, decided only by the HTTP probe
    against the forwarded port. :attr:`link_profile` does not change that --
    it explains a failure, it never promotes a run to success.

    ``error`` stays the prose every caller prints, and ``failure`` is the
    machine-readable half. They are written together by :meth:`fail` so the
    message a human reads and the kind a dashboard counts cannot disagree.
    """

    rootfs_dir: Path
    arch: str
    success: bool = False
    web_ok: bool = False
    web_url: str = ""
    serial_log: str = ""
    error: str = ""
    duration_sec: float = 0.0
    container_id: str = ""
    #: The first failure that ended the run, plus every signal the boot diagnosis
    #: found alongside it — one failed guest usually has several causes stacked.
    failure: Failure | None = None
    findings: tuple[Failure, ...] = ()
    #: The layered link measurement, when one was taken. ``None`` means either
    #: the web plane came up (nothing to explain) or the probe could not run --
    #: see :attr:`iris.emulate.linkprobe.LinkProfile.unavailable`.
    link_profile: LinkProfile | None = None
    #: The address the run was actually judged against. Recorded because the
    #: default is an assumption and the detected one is a measurement, and
    #: those two disagree exactly when a network bug is present.
    guest_ip: str = ""

    def fail(self, failure: Failure, message: str = "") -> None:
        """Record a failure and the message that describes it."""
        self.failure = failure
        self.findings = (failure,)
        self.error = message or failure.message

    def fail_from_diagnosis(self, diagnosis: BootDiagnosis) -> None:
        """Record a multi-signal boot diagnosis under its first (most specific) kind."""
        self.findings = diagnosis.findings
        self.failure = diagnosis.primary
        self.error = str(diagnosis)

    @property
    def failures(self) -> tuple[Failure, ...]:
        """Only the findings that are actually failures, informational ones dropped."""
        return tuple(f for f in self.findings if f.is_failure)


def _env() -> dict[str, str]:
    return {**os.environ, "MSYS_NO_PATHCONV": "1"}


def _run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, env=_env(), timeout=timeout, check=False)



def _is_dotted_quad(value: str) -> bool:
    """Whether ``value`` is an IPv4 address written as four dotted decimal octets.

    Strict on purpose: the addresses that pass through here end up in shell
    arithmetic and in ``ip addr add``, where anything else is a launch that dies on
    the way in. ``str.isdigit`` is not enough for that -- it accepts superscript
    digits that ``int()`` then refuses, which is a ``ValueError`` inside a boot loop.
    """
    parts = value.split(".")
    return len(parts) == 4 and all(re.fullmatch(r"[0-9]{1,3}", p) and int(p) <= 255 for p in parts)


def _host_address_on_guest_subnet(guest_ip: str) -> str | None:
    """An address for the host bridge that the guest will accept packets from.

    run_qemu.sh puts the host at ``<assumed guest ip> - 1`` with a /16, on the
    assumption that the guest will be at that address. That assumption holds for
    the firmwares whose own idea of "my network" is the network IRIS set up, and
    fails for a router, which brings up a LAN of its own on a subnet of its own
    choosing. Tenda DIR-868L measured exactly that: br0 at 192.168.0.1/24 while
    the bridge sat at 192.168.1.254/16, so every packet the host sent arrived
    with a source address outside the guest's subnet and was dropped without a
    reply. The symptom is not a dropped packet, it is a total one -- ARP answered
    (the neighbour entry went REACHABLE), ICMP unanswered, TCP connect refused by
    timeout, and a guest web server that had demonstrably bound :80.

    .254 rather than .253 or .1 because the last octet is the one the firmware is
    least likely to have handed out or configured, and it must not collide with the
    guest itself. /24 because that is what a router LAN is in practice, and because
    the only statement of the guest's address that IRIS can read -- the kernel's
    own ``inet_insert_ifa`` printk -- carries no prefix length. A LAN on some other
    prefix is a known limit of this approach, not a case it handles.
    """
    if not _is_dotted_quad(guest_ip):
        return None
    parts = guest_ip.split(".")
    if parts[3] == "254":
        # The guest would answer to this address itself.
        return ".".join(parts[:3] + ["253"])
    return ".".join(parts[:3] + ["254"])


def _record_guest_ip(container_name: str, iid: int, guest_ip: str) -> bool:
    """Write the address this boot measured next to the image, for the next launch.

    ``run_qemu.sh`` derives both the bridge address and the port forward from the
    guest address it is handed, and the only caller that can hand it one is the boot
    loop below -- which does not exist at restart time. A container restart brings
    back PID 1 (``sleep 3600``) and nothing else, so a relaunch has to be told where
    the guest is, and this file is the only place that can still say so. Without it
    the guardian's ``WEB_SERVER_RESTART`` re-derives everything from the
    192.168.1.1 assumption: on Tenda DIR-868L, measured, the restarted bridge sat at
    192.168.1.254/16 and socat forwarded to an address nothing was listening on, so
    a guest that had just answered HTTP 200 answered HTTP 000 after the restart that
    was meant to bring it back.

    Recorded next to ``image.raw`` beside the arch marker rather than in the run
    table, because it has to outlive the run: what needs it is a launch that starts
    after the run is over. Best-effort, and reported when it fails -- losing the
    marker costs the next restart this address, which is exactly the failure it
    exists to prevent, so it must not pass silently.

    The address is handed to ``sh -c`` as a positional parameter rather than
    interpolated into the script text: it comes out of a guest's own printk, and
    whatever lands in this file is later fed to shell arithmetic.
    """
    if not _is_dotted_quad(guest_ip) or guest_ip.startswith("127."):
        return False
    marker = f"/work/scratch/{iid}/guest_ip"
    res = _run(["docker", "exec", container_name, "sh", "-c",
                'printf "%s\\n" "$1" > "$2"', "sh", guest_ip, marker], timeout=10)
    if res.returncode != 0:
        logger.warning(f"could not record the measured guest address {guest_ip} in {marker}: "
                       f"{res.stderr.strip()} -- the next launch will assume 192.168.1.1")
        return False
    logger.info(f"Recorded guest address {guest_ip} in {marker} for the next launch")
    return True


def _place_host_on_guest_subnet(container_name: str, iid: int, guest_ip: str) -> bool:
    """Give the container's bridge an address inside the guest's own subnet.

    A no-op returning True when the bridge is already inside that subnet, which is
    the common case: the point is only to cover a guest whose LAN is not the one
    IRIS assumed. Best-effort by design -- the address is an optimisation of the
    guest's packet filter, not a requirement for the port forward to exist, so a
    failure here must not be reported as a boot failure.
    """
    host_ip = _host_address_on_guest_subnet(guest_ip)
    if host_ip is None:
        return False
    bridge = f"br{iid}"

    add = _run(["docker", "exec", container_name, "ip", "addr", "add",
                f"{host_ip}/24", "dev", bridge], timeout=10)
    if add.returncode == 0:
        logger.info(f"Placed host at {host_ip}/24 on {bridge}, inside the guest's subnet")
        return True
    # "File exists" is success for our purpose: the address is already there.
    if "File exists" in (add.stderr or ""):
        return True
    logger.debug(f"could not add {host_ip}/24 to {bridge}: {add.stderr.strip()}")
    return False


def _baked_sources(project_root: Path) -> list[Path]:
    """Everything ``Dockerfile.baked`` bakes into the image, in a stable order."""
    sources = [project_root / "docker" / "emulate" / "Dockerfile.baked"]
    sources += sorted((project_root / "scripts" / "emulate").glob("*"))
    return sources


def _baked_scripts_fingerprint(project_root: Path | None = None) -> str:
    """Short digest of everything ``Dockerfile.baked`` copies into the image.

    The emulation shell scripts live inside the baked image, so editing one and
    re-running must not be served the image built from the previous revision.
    """
    if project_root is None:
        project_root = Path(__file__).parent.parent.parent.parent
    digest = hashlib.sha256()
    for path in _baked_sources(project_root):
        if not path.is_file():
            continue
        digest.update(path.name.encode("utf-8", "replace"))
        try:
            digest.update(path.read_bytes())
        except OSError:
            # Unreadable source cannot be fingerprinted; the name still enters the
            # digest so a rename is not mistaken for "nothing changed".
            continue
    return digest.hexdigest()[:12]


def _build_baked_image() -> str:
    """Return the baked image tag for the current scripts, building it if needed.

    ``scripts/emulate/*.sh`` is copied into the image by ``Dockerfile.baked``, so
    the image *is* the compiled form of those scripts. Tagging it ``:latest`` and
    reusing it whenever it exists made every script edit silently inert: the run
    looked successful while the container executed the previous revision of
    ``make_image.sh`` — which is indistinguishable from the fix not working.
    The tag therefore carries a digest of the sources that go into it.
    """
    project_root = Path(__file__).parent.parent.parent.parent
    dockerfile = project_root / "docker" / "emulate" / "Dockerfile.baked"
    image_name = f"iris-emulate-baked:{_baked_scripts_fingerprint()}"
    result = _run(["docker", "image", "inspect", image_name])
    if result.returncode == 0:
        return image_name
    logger.info(f"Building baked emulation image {image_name} from current scripts...")
    # --pull=false: the base image is this project's own iris-emulate, already on
    # the daemon. buildkit would otherwise contact the registry on every build,
    # which is pointless here and turns any mirror outage into a build failure —
    # exactly when a script fix needs to be rebuilt to be tested at all.
    result = _run(
        ["docker", "build", "--pull=false", "-t", image_name, "-f", str(dockerfile), str(project_root)],
        timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Docker build failed: {result.stderr}")
    _drop_other_baked_tags(keep=image_name)
    return image_name


def _drop_other_baked_tags(keep: str) -> None:
    """Remove baked tags that no longer correspond to any script revision."""
    listing = _run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"])
    for line in listing.stdout.splitlines():
        name = line.strip()
        if name.startswith("iris-emulate-baked:") and name != keep:
            logger.info(f"Removing stale baked image {name}")
            _run(["docker", "rmi", "-f", name], timeout=120)


def _create_tarball(rootfs_dir: Path, tarball_path: Path) -> Path:
    tarball_path.parent.mkdir(parents=True, exist_ok=True)
    env = _env()

    create_cmd = [
        "docker", "create",
        "-v", f"{rootfs_dir.resolve()}:/rootfs:ro",
        "alpine:3.20",
        "tar", "czf", "/tmp/rootfs.tar.gz", "-C", "/rootfs", ".",
    ]
    create_result = subprocess.run(create_cmd, capture_output=True, text=True, env=env, timeout=30, check=False)
    if create_result.returncode != 0:
        raise RuntimeError(f"docker create failed: {create_result.stderr}")
    container_id = create_result.stdout.strip()

    start_result = subprocess.run(["docker", "start", "-a", container_id], capture_output=True, text=True, env=env, timeout=120, check=False)
    if start_result.returncode != 0:
        subprocess.run(["docker", "rm", "-f", container_id], env=env, capture_output=True, check=False)
        raise RuntimeError(f"tar creation failed: {start_result.stderr}")

    cp_result = subprocess.run(["docker", "cp", f"{container_id}:/tmp/rootfs.tar.gz", str(tarball_path)], capture_output=True, text=True, env=env, timeout=60, check=False)
    subprocess.run(["docker", "rm", "-f", container_id], env=env, capture_output=True, check=False)
    if cp_result.returncode != 0:
        raise RuntimeError(f"docker cp failed: {cp_result.stderr}")
    return tarball_path


def _docker_produce(volumes: list[tuple[str, str]], image: str, script: str,
                    container_out: str, host_out: Path, timeout: int = 240) -> Path:
    """Run a throwaway container with bind mounts, copy one artifact back to host."""
    env = _env()
    create_cmd = ["docker", "create"]
    for host, cont in volumes:
        create_cmd += ["-v", f"{host}:{cont}"]
    create_cmd += [image, "sh", "-c", script]
    create_result = subprocess.run(create_cmd, capture_output=True, text=True, env=env, timeout=30, check=False)
    if create_result.returncode != 0:
        raise RuntimeError(f"docker create failed: {create_result.stderr}")
    container_id = create_result.stdout.strip()
    if not container_id or " " in container_id:
        raise RuntimeError(f"docker create returned unexpected output: {container_id[:200]!r}")
    start_result = subprocess.run(
        ["docker", "start", "-a", container_id],
        capture_output=True, text=True, env=env, timeout=timeout, check=False,
    )
    if start_result.returncode != 0:
        subprocess.run(["docker", "rm", "-f", container_id], env=env, capture_output=True, check=False)
        raise RuntimeError(f"container script failed: {start_result.stderr[-500:]}")
    cp_result = subprocess.run(
        ["docker", "cp", f"{container_id}:{container_out}", str(host_out)],
        capture_output=True, text=True, env=env, timeout=120, check=False,
    )
    subprocess.run(["docker", "rm", "-f", container_id], env=env, capture_output=True, check=False)
    if cp_result.returncode != 0:
        raise RuntimeError(f"docker cp failed: {cp_result.stderr}")
    return host_out


def _compose_rootfs_from_slices(
    slices_dir: Path,
    partition_mounts: list[tuple[str, str]],
    tarball_path: Path,
    guest_script: str = "",
    image: str = "python:3.11-alpine",
) -> Path:
    """Rebuild a multi-partition rootfs entirely inside a Linux container.

    ``partition_mounts`` is ordered base-first: ``(slice_stem, mount_point)`` pairs
    whose ``<stem>.jffs2`` files live in ``slices_dir``. jefferson extraction and
    merge run on ext4 in the container so JFFS2 soft links survive; the result is
    tarred and copied back. This avoids the NTFS path where a host-side ``cp`` or
    ``copytree`` silently drops the ~230 busybox symlinks (WinError 123).
    """
    env_lines = ["set -e", "pip install -q jefferson 2>/dev/null || pip install -q jefferson"]
    env_lines.append("BASE=/work/rootfs; rm -rf /work/rootfs; mkdir -p $BASE")
    for stem, mount in partition_mounts:
        tree = f"/work/{stem}"
        target = "$BASE" if mount in ("/", "") else f"$BASE/{mount.lstrip('/')}"
        env_lines.append(f"rm -rf {tree}; jefferson -d {tree} -f /in/{stem}.jffs2 >/dev/null")
        env_lines.append(f"mkdir -p {target}; cp -a {tree}/. {target}/")
    # after merge, the mount targets hold real content; re-mounting the empty
    # vendor JFFS2 partition over them would shadow the binaries (RP3 "Kylin:
    # not found"). Comment out any such mount line in the boot scripts.
    for _stem, mount in partition_mounts:
        if mount in ("/", ""):
            continue
        mp = mount.rstrip("/")
        env_lines.append(
            "for s in $(ls $BASE/etc/init.d/* $BASE/etc/*rc* $BASE/etc_ro/init.d/* 2>/dev/null); do "
            f"sed -i -E 's|^([[:space:]]*mount.*{mp}[[:space:]].*)$|#IRIS-shadow-fix: \\1|' \"$s\" 2>/dev/null || true; done"
        )
    if guest_script:
        import base64

        b64 = base64.b64encode(guest_script.encode()).decode()
        env_lines.append("mkdir -p $BASE/firmadyne")
        env_lines.append(f"echo {b64} | base64 -d > $BASE/firmadyne/iris_rules.sh")
        env_lines.append("chmod +x $BASE/firmadyne/iris_rules.sh")
    env_lines.append("tar -czf /work/rootfs.tar.gz -C $BASE .")
    # the /work bind mount is the host scratch dir; drop our intermediate trees
    stems = " ".join(stem for stem, _m in partition_mounts)
    env_lines.append("for d in rootfs " + stems + "; do rm -rf /work/$d; done")
    script = "; ".join(env_lines)

    tarball_path.parent.mkdir(parents=True, exist_ok=True)
    volumes = [(str(slices_dir.resolve()), "/in:ro"), (str(tarball_path.parent.resolve()), "/work")]
    produced = _docker_produce(volumes, image, script, "/work/rootfs.tar.gz", tarball_path)
    if tarball_path.name != "rootfs.tar.gz":
        (tarball_path.parent / "rootfs.tar.gz").unlink(missing_ok=True)
    return produced


def build_parts_mounts(parts_dir: Path) -> list[tuple[str, str]]:
    """Order a TendaW ``-parts`` dir's ``<stem>.jffs2`` slices base-first.

    The slice named ``romfs`` (mount ``/``) must merge before overlays.
    Unknown stems fall back to mounting at ``/opt/<stem>``.
    """
    from iris.extract.tenda import PARTITION_MOUNTS

    stems = sorted(p.stem for p in parts_dir.glob("*.jffs2"))
    pairs = [(s, PARTITION_MOUNTS.get(s, f"/opt/{s}")) for s in stems]
    pairs.sort(key=lambda p: (p[1] != "/", p[1]))
    return pairs


def _newest_mtime(rootfs_dir: Path) -> float:
    """Newest mtime anywhere in the tree, ignoring entries that refuse to stat.

    ``os.walk`` is used rather than ``rglob`` because an extracted rootfs carries
    POSIX symlinks that raise on the Windows host; ``os.walk`` skips those instead
    of propagating, and every entry it does skip is one no rule could have edited
    anyway.
    """
    newest = 0.0
    for root, _dirs, files in os.walk(rootfs_dir):
        for name in files:
            try:
                newest = max(newest, os.stat(os.path.join(root, name)).st_mtime)
            except OSError:
                continue
    return newest


def _mtime_or_zero(path: Path) -> float:
    """mtime of *path*, or 0.0 when it cannot be stat'ed."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _tarball_is_stale(rootfs_dir: Path, tarball_path: Path) -> bool:
    """Whether a cached tarball predates the tree it was built from.

    The tarball is cached under ``scratch/emulate-<iid>/<iid>.tar.gz`` and ``iid``
    is derived from the rootfs *path*, so it is stable across sessions: a rerun
    reuses the pack from the first run forever. That is only sound while the tree
    is unchanged, and it very rarely is — every ``iris emulate run <firmware.bin>``
    re-extracts the rootfs and re-applies the L3 rules, which rewrites files in
    place. Reusing the old pack silently discards the whole repair: the guest
    boots a month-old rootfs in which ``diag`` was never disabled, and the only
    symptom is a crash loop the rules were supposed to have prevented.

    Comparing mtimes rather than hashing two thousand files keeps the check at
    well under a second, and a file edited by a rule always carries a newer stamp
    than the pack that was built before the edit.
    """
    if not safe_present(tarball_path) or not safe_present(rootfs_dir):
        return True
    pack_mtime = _mtime_or_zero(tarball_path)
    if pack_mtime == 0.0:
        # Occupies its name but cannot be stat'ed: its age is unknowable, and a
        # pack whose age is unknowable cannot be shown to be current.
        return True
    return _newest_mtime(rootfs_dir) > pack_mtime


def emulate_firmware(
    rootfs_dir: Path,
    arch: str,
    iid: int,
    scratch_dir: Path,
    host_port: int = 8080,
    timeout_sec: int = 120,
    docker_image: str = "",
    parts_slices_dir: Path | None = None,
    partition_mounts: list[tuple[str, str]] | None = None,
    record: bool = True,
    applied_rule_ids: tuple[str, ...] = (),
) -> EmulationResult:
    """Boot ``rootfs_dir`` under QEMU and report whether its web plane answered.

    Every outcome -- including the early returns before a container even exists --
    is written to ``emulation_run``/``failure_profile``, because this is the one
    function all entry points pass through and therefore the only place a run can
    be counted without one of them being forgotten. Those early returns are also
    the runs a hand-kept table drops, so the recording lives in this wrapper
    rather than at the end of the pipeline: a `return` added mid-function would
    otherwise skip it silently. Pass ``record=False`` to opt out.

    ``applied_rule_ids`` closes the other half of the loop: the L3 rules that fired
    are recorded against this run, which is what lets a later query ask whether
    applying them changed anything. Without it ``repair_action`` stays empty and
    every rule's worth is unmeasurable.
    """
    started_at = datetime.now(UTC).replace(tzinfo=None)  # naive: the column is naive
    result = _emulate_firmware(
        rootfs_dir=rootfs_dir,
        arch=arch,
        iid=iid,
        scratch_dir=scratch_dir,
        host_port=host_port,
        timeout_sec=timeout_sec,
        docker_image=docker_image,
        parts_slices_dir=parts_slices_dir,
        partition_mounts=partition_mounts,
    )
    if record:
        _record_outcome(result, iid=iid, started_at=started_at, applied_rule_ids=applied_rule_ids)
    return result


def _emulate_firmware(
    rootfs_dir: Path,
    arch: str,
    iid: int,
    scratch_dir: Path,
    host_port: int = 8080,
    timeout_sec: int = 120,
    docker_image: str = "",
    parts_slices_dir: Path | None = None,
    partition_mounts: list[tuple[str, str]] | None = None,
) -> EmulationResult:
    start_time = time.time()
    result = EmulationResult(rootfs_dir=rootfs_dir, arch=arch)

    config = get_config(arch)
    if config is None:
        result.fail(Failure(FailureKind.UNSUPPORTED_ARCH, f"no QEMU configuration for '{arch}'"),
                    f"Unsupported architecture: {arch}")
        return result

    work_dir = scratch_dir / f"emulate-{iid}"
    work_dir.mkdir(parents=True, exist_ok=True)
    tarball_path = work_dir / f"{iid}.tar.gz"

    container_compose = parts_slices_dir is not None and partition_mounts is not None
    try:
        stale = _tarball_is_stale(rootfs_dir, tarball_path)
        if stale:
            if container_compose:
                guest_script = ""
                host_rules_script = rootfs_dir / "firmadyne" / "iris_rules.sh"
                if safe_is_file(host_rules_script):
                    guest_script = safe_read_text(host_rules_script)
                logger.info(
                    f"Composing rootfs from {len(partition_mounts)} JFFS2 slices in-container "
                    f"(symlink-safe, guest_script={bool(guest_script)})..."
                )
                _compose_rootfs_from_slices(parts_slices_dir, partition_mounts, tarball_path, guest_script)
            else:
                logger.info("Creating rootfs tarball...")
                _create_tarball(rootfs_dir, tarball_path)
        else:
            logger.info(f"Reusing up-to-date tarball {tarball_path.name} (rootfs unchanged since it was built)")
        logger.info(f"Tarball: {safe_stat_size(tarball_path)} bytes")
    except RuntimeError as e:
        result.fail(Failure(FailureKind.TARBALL_FAILED, str(e)))
        result.duration_sec = time.time() - start_time
        return result

    if not docker_image:
        docker_image = _build_baked_image()

    container_name = f"iris-qemu-{iid}"
    _run(["docker", "rm", "-f", container_name])

    logger.info(f"Starting emulation container {container_name}...")
    # Picked before the container exists so the probe sees a free port, and
    # published on loopback only: the serial chardev is writable, so unlike the
    # read-only web forward it would hand anyone who can reach this port a
    # keyboard on the guest. The web forward above keeps its wider bind because
    # that is the pre-existing behaviour and its own exposure is deliberate.
    serial_port = pick_serial_port()
    create_cmd = [
        "docker", "create", "--privileged",
        "-p", f"{host_port}:{host_port}",
        "-p", f"127.0.0.1:{serial_port}:{serial_port}",
        "--name", container_name,
        docker_image, "sleep", "3600",
    ]
    create_result = _run(create_cmd)
    if create_result.returncode != 0:
        result.fail(Failure(FailureKind.CONTAINER_CREATE_FAILED,
                            f"docker refused to create the container: {create_result.stderr}"),
                    f"Container create failed: {create_result.stderr}")
        result.duration_sec = time.time() - start_time
        return result

    start_result = _run(["docker", "start", container_name])
    if start_result.returncode != 0:
        result.fail(Failure(FailureKind.CONTAINER_START_FAILED,
                            f"docker created but would not start it: {start_result.stderr}"),
                    f"Container start failed: {start_result.stderr}")
        result.duration_sec = time.time() - start_time
        return result
    result.container_id = container_name

    logger.info("Copying tarball into container...")
    cp_target = f"/work/scratch/{iid}/{iid}.tar.gz"
    _run(["docker", "exec", container_name, "mkdir", "-p", f"/work/scratch/{iid}"])
    cp_result = _run(["docker", "cp", str(tarball_path), f"{container_name}:{cp_target}"], timeout=60)
    if cp_result.returncode != 0:
        result.fail(Failure(FailureKind.COPY_TARBALL_FAILED,
                            f"the rootfs tarball never reached the container: {cp_result.stderr}"),
                    f"docker cp tarball failed: {cp_result.stderr}")
        result.duration_sec = time.time() - start_time
        _run(["docker", "rm", "-f", container_name])
        return result

    logger.info(f"Building QEMU image for iid={iid} arch={arch}...")
    make_result = _run(
        ["docker", "exec", container_name, "bash", "/work/scripts/make_image.sh", str(iid), arch],
        timeout=180,
    )
    if make_result.returncode != 0:
        result.fail(Failure(FailureKind.IMAGE_BUILD_FAILED,
                            f"make_image.sh failed: {make_result.stderr[-500:]}"),
                    f"Image build failed: {make_result.stderr[-500:]}")
        result.duration_sec = time.time() - start_time
        _run(["docker", "rm", "-f", container_name])
        return result
    logger.debug(f"image build output: {make_result.stdout[-200:]}")

    logger.info(f"Starting QEMU (port {host_port} -> guest:80, serial on :{serial_port})...")
    # IRIS_SERIAL_PORT goes in through the environment rather than a fourth
    # positional argument: run_qemu.sh's positional contract is (iid, arch,
    # host_port) and every caller of it -- including the ones that only want the
    # web forward -- would otherwise have to know a console port it never uses.
    qemu_result = _run(
        ["docker", "exec", "-d", "-e", f"IRIS_SERIAL_PORT={serial_port}",
         container_name, "bash", "/work/scripts/run_qemu.sh", str(iid), arch, str(host_port)],
        timeout=15,
    )
    if qemu_result.returncode != 0:
        result.fail(Failure(FailureKind.QEMU_START_FAILED,
                            f"run_qemu.sh refused to start: {qemu_result.stderr}"),
                    f"QEMU start failed: {qemu_result.stderr}")
        result.duration_sec = time.time() - start_time
        _run(["docker", "rm", "-f", container_name])
        return result

    # Recorded here, not at container create: until QEMU is running there is no
    # listener on the port, and a console advertised for a chardev that never
    # came up would hand the web UI a connection that can only fail.
    _SERIAL_PORTS[iid] = serial_port

    logger.info(f"Waiting for firmware to boot (timeout {timeout_sec}s)...")
    boot_deadline = time.time() + timeout_sec
    guest_ip = "192.168.1.1"
    # Carried on the result from here on, so a failed run still records which
    # address it was judged against -- the default is an assumption and the
    # detected one is a measurement, and they disagree exactly when there is a
    # network bug to explain.
    result.guest_ip = guest_ip
    socat_updated = False
    placed_subnets: set[str] = set()
    progress = StatusLine()
    while time.time() < boot_deadline:
        time.sleep(5)
        elapsed = int(time.time() - start_time)

        reboots = _count_guest_reboots(container_name, iid)
        if reboots >= REBOOT_LOOP_THRESHOLD:
            # Stop here rather than burning the rest of the timeout: the guest is
            # not slow to boot, it is looping, and every further second of waiting
            # produces the same verdict with less information attached.
            progress.clear()
            diagnosis = _failure_diagnosis(container_name, iid, reboots=reboots)
            result.fail_from_diagnosis(diagnosis)
            logger.error(result.error)
            break

        if not socat_updated:
            log_cmd = ["docker", "exec", container_name, "grep", "-a", "inet_insert_ifa",
                       f"/work/scratch/{iid}/qemu.serial.log"]
            log_res = subprocess.run(log_cmd, capture_output=True, text=True, env=_env(), timeout=10, check=False)
            detected = None
            for line in log_res.stdout.splitlines():
                if "device:lo" not in line and "ifa:0x" in line:
                    m = re.search(r"ifa:0x([0-9a-f]+)", line)
                    if m:
                        raw = int(m.group(1), 16)
                        if is_little_endian(arch):
                            detected = f"{raw & 0xFF}.{(raw >> 8) & 0xFF}.{(raw >> 16) & 0xFF}.{(raw >> 24) & 0xFF}"
                        else:
                            detected = f"{(raw >> 24) & 0xFF}.{(raw >> 16) & 0xFF}.{(raw >> 8) & 0xFF}.{raw & 0xFF}"
                        break
            # Placed for whichever address is current, not only once an address has
            # been read out of the log: the placement is a precondition for the guest
            # answering at all, and a guest that never prints the printk this loop
            # reads would otherwise never get it. Tracked by address because the
            # detected one can change under us, and re-adding is not free.
            for target in (detected, guest_ip):
                if target and not target.startswith("127.") and target not in placed_subnets:
                    _place_host_on_guest_subnet(container_name, iid, target)
                    placed_subnets.add(target)
            if detected and detected != guest_ip and not detected.startswith("127."):
                guest_ip = detected
                result.guest_ip = detected
                progress.clear()
                # Before the forward, not after: the forward is only useful once the
                # guest will answer packets from this host, and a router whose LAN is
                # not the assumed subnet will not.
                logger.info(f"Detected guest IP: {guest_ip}, forwarding :{host_port}...")
                # Recorded inside this branch on purpose. `guest_ip` starts out as the
                # 192.168.1.1 assumption, so writing it unconditionally would pin the
                # assumption as a measurement and leave a relaunch with exactly the
                # address it already had by default.
                _record_guest_ip(container_name, iid, guest_ip)
                _run(["docker", "exec", container_name, "pkill", "-f", "socat.*TCP"], timeout=5)
                _run(["docker", "exec", "-d", container_name, "socat",
                      f"TCP-LISTEN:{host_port},reuseaddr,fork", f"TCP:{guest_ip}:80"], timeout=5)
                socat_updated = True

        check_result = subprocess.run(
            ["docker", "exec", container_name, "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "--max-time", "3", f"http://127.0.0.1:{host_port}"],
            capture_output=True, text=True, env=_env(), timeout=10, check=False,
        )
        http_code = check_result.stdout.strip()
        if http_code and http_code != "000":
            result.web_ok = True
            result.web_url = f"http://localhost:{host_port}"
            result.success = True
            result.guest_ip = guest_ip
            progress.close()
            logger.info(f"Web reachable at {result.web_url} (HTTP {http_code}) after {elapsed}s")
            break
        progress.update(f"[{elapsed}s] waiting for guest web on :{host_port} (HTTP {http_code or '---'})")
    else:
        progress.clear()
        logger.warning(f"guest web still unreachable on :{host_port} after {timeout_sec}s")
        if not result.error:
            diagnosis = _failure_diagnosis(container_name, iid)
            # A log cannot say where a packet stopped. Before falling back to the
            # log-only verdict, ask the link: the layered table distinguishes a
            # guest that never came up from one that is up and dropping our
            # packets, which the log reads identically. The measured failure is
            # put first so it leads the diagnosis, with the log findings kept
            # behind it as the boot-side explanation.
            profile = probe_link(container_name, guest_ip, 80, runner=_run)
            measured = profile.as_failure()
            findings = ((measured,) if measured else ()) + tuple(diagnosis.findings)
            if measured:
                logger.error(render_table(profile))
                diagnosis = BootDiagnosis(
                    f"emulation failed: {measured.detail}; "
                    + "; ".join(f.detail for f in diagnosis.findings) + ".",
                    findings,
                )
            result.fail_from_diagnosis(diagnosis)
            result.link_profile = profile
            logger.error(result.error)

    log_result = _run(["docker", "cp", f"{container_name}:/work/scratch/{iid}/qemu.serial.log", str(work_dir / "qemu.serial.log")])
    if log_result.returncode == 0:
        serial_path = work_dir / "qemu.serial.log"
        if serial_path.exists():
            result.serial_log = serial_path.read_text(errors="replace")[-2000:]

    result.duration_sec = time.time() - start_time

    return result


def _record_outcome(
    result: EmulationResult,
    *,
    iid: int,
    started_at: datetime,
    applied_rule_ids: tuple[str, ...] = (),
) -> None:
    """Persist the run. Never allowed to change or delay the caller's verdict.

    A metrics write that can fail an emulation would make the measurement part of
    the system under test; a database that is missing, locked or corrupt must
    cost exactly one log line.
    """
    try:
        from iris.config import get_settings
        from iris.db.engine import get_engine, init_db, make_session
        from iris.db.knowledge import record_repairs
        from iris.db.runs import record_run

        engine = get_engine(get_settings().database_url)
        init_db(engine)
        with make_session(engine) as session:
            run_id = record_run(
                session,
                iid=iid,
                arch=result.arch,
                rootfs_dir=result.rootfs_dir,
                success=result.success,
                web_ok=result.web_ok,
                findings=result.findings,
                duration_sec=result.duration_sec,
                started_at=started_at,
                ping_reachable=result.link_profile.ping_ok if result.link_profile else None,
                ip=result.guest_ip,
            )
            if applied_rule_ids:
                # After ``record_run``, which commits: the repair rows carry a
                # foreign key to this run, so writing them into an uncommitted
                # session would either order the inserts wrong or lose both.
                record_repairs(session, run_id=run_id, applied_rule_ids=applied_rule_ids)
                session.commit()
    except Exception as exc:  # noqa: BLE001 - a metric must never fail a run
        logger.warning(f"run {iid} was not recorded to the metadata database: {exc}")


def container_exists(name: str, *, timeout: int = 10) -> bool:
    """Whether docker still has this container -- running *or* exited.

    `stop_emulation` answers this implicitly by deleting and checking the exit
    code, which cannot be used to look without changing anything. The API needs
    the read-only version to tell a hosted emulation from a leftover row after a
    restart. `inspect` rather than `ps` because an exited container is still
    there: its serial log is the evidence a failure diagnosis needs.

    A name that does not exist makes docker exit non-zero, which is the answer;
    only a timeout or a missing docker is an error, and the caller decides what
    to do about that (see ``iris.db.active.container_alive``).
    """
    if not name:
        return False
    result = _run(["docker", "inspect", "--type", "container", name], timeout=timeout)
    return result.returncode == 0


def stop_emulation(iid: int) -> bool:
    env = _env()
    # Dropped before the container goes, not after: once the container is gone
    # the port cannot be connected to either way, and clearing first means a
    # caller racing this cannot be handed a port that is already on its way out.
    _SERIAL_PORTS.pop(iid, None)
    result = subprocess.run(["docker", "rm", "-f", f"iris-qemu-{iid}"], capture_output=True, text=True, env=env, timeout=15, check=False)
    return result.returncode == 0
