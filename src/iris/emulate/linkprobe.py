"""Measuring the link instead of inferring it from a boot log.

Every network verdict IRIS used to reach came out of the serial log: a regex
looked for ``inet_insert_ifa``, decided an address existed or not, and a missing
web plane was reported as ``web-unreachable``. 0.3.12 is the argument against
that. DIR-868L's log said "no address, no NIC" -- both probes had only ever
learned one spelling of each -- while the guest in fact held ``br0=192.168.0.1``
and a ``httpd`` bound to ``:80``. The real fault was on the host side, and it
was only ever visible by hand, as a sequence of four commands run inside the
container:

===================================  ==========================================
``ip route get 192.168.0.1``         ``dev br6630 src 192.168.1.254``
``ip neigh``                         ``lladdr 00:de:fa:1a:01:00 REACHABLE``
``ping 192.168.0.1``                 2 sent, 0 received
``curl http://192.168.0.1/``          ``000``
===================================  ==========================================

Route correct, L2 answered, L3 silent, L4 silent. That combination *is* the
diagnosis: a router drops packets whose source address is outside its own
subnet, by design. IRIS had been guessing at layer 4 while the evidence for
layer 3 was sitting there unread.

So this module asks the link directly and reports where it stops. Each layer is
:attr:`LayerState.OK`, :attr:`LayerState.BLOCKED`, or -- importantly --
:attr:`LayerState.UNKNOWN`. Three states because "the probe could not run" and
"the probe ran and the packet died" are different facts, and collapsing the
first into the second invents failures. A container with no ``ping`` binary is
not a guest with a broken network, and the third state is what keeps the two
apart.

The output is a :class:`LinkProfile`: one row per layer, plus the first layer
that broke. That table is the artifact -- it is what gets logged, what gets
attached to the failure as evidence, and what a reader needs in order to
disagree with the verdict.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from iris.failures import Failure, FailureKind
from iris.log import get_logger

logger = get_logger(__name__)

__all__ = [
    "LAYER_ORDER",
    "LayerProbe",
    "LayerState",
    "LinkLayer",
    "LinkProfile",
    "Runner",
    "parse_curl",
    "parse_neigh",
    "parse_ping",
    "parse_route",
    "probe_link",
    "render_table",
]

#: ``(command, timeout)`` per layer. Short on purpose: this runs inside a boot
#: loop that has already burned minutes, and a probe that hangs is worse than a
#: probe that answers "unknown".
_ROUTE_TIMEOUT = 5
_ARP_TIMEOUT = 5
_ICMP_TIMEOUT = 8
_SERVICE_TIMEOUT = 8

#: A MAC that is all zeros is what an unanswered ARP leaves behind, both in
#: ``ip neigh`` (as ``INCOMPLETE``) and in ``/proc/net/arp`` (flags ``0x0``).
#: Treating it as a resolved address would report L2 healthy on a dead link.
_NULL_MAC = "00:00:00:00:00:00"

#: ``ip neigh`` states that mean "there is a neighbour entry and it is usable".
#: ``INCOMPLETE`` and ``FAILED`` mean the kernel asked and got nothing -- both
#: were measured inside the image, not assumed.
_ARP_RESOLVED_STATES = frozenset({"REACHABLE", "STALE", "DELAY", "PROBE", "PERMANENT", "NOARP"})
_ARP_UNANSWERED_STATES = frozenset({"INCOMPLETE", "FAILED"})


class LinkLayer(StrEnum):
    """The layers a packet crosses on the way from the host bridge to the guest."""

    #: Kernel picked a path and a source address. Says nothing about delivery.
    ROUTE = "route"
    #: The guest answered for its own address -- frames reach it.
    ARP = "arp"
    #: The guest answered for itself. Routers answer from any source, so this is
    #: the layer that catches "the host's address is outside the guest's subnet".
    ICMP = "icmp"
    #: Something on the guest answered on the web port.
    SERVICE = "service"


#: Physical order, which is the order a reader thinks in. It is *not* the order
#: the probes run in -- see :func:`probe_link`.
LAYER_ORDER: tuple[LinkLayer, ...] = (
    LinkLayer.ROUTE,
    LinkLayer.ARP,
    LinkLayer.ICMP,
    LinkLayer.SERVICE,
)


class LayerState(StrEnum):
    OK = "ok"
    BLOCKED = "blocked"
    #: The probe could not be run, or ran and answered something unparseable.
    #: Never a failure on its own -- see the module docstring.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class LayerProbe:
    layer: LinkLayer
    state: LayerState
    detail: str = ""
    evidence: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "layer": self.layer.value,
            "state": self.state.value,
            "detail": self.detail,
            **dict(self.evidence),
        }


@dataclass(frozen=True)
class LinkProfile:
    """Where the path from the host to the guest stops."""

    guest_ip: str
    probes: tuple[LayerProbe, ...] = ()
    #: Why a probe could not run at all (docker refused, ``ip`` missing). Kept
    #: apart from the per-layer details because it invalidates the whole table,
    #: not one row of it.
    unavailable: str = ""

    def get(self, layer: LinkLayer) -> LayerProbe | None:
        return next((p for p in self.probes if p.layer is layer), None)

    def state(self, layer: LinkLayer) -> LayerState:
        probe = self.get(layer)
        return probe.state if probe else LayerState.UNKNOWN

    @property
    def first_break(self) -> LinkLayer | None:
        """The shallowest layer that stopped, in physical order.

        Scanning shallow-to-deep matters: when routing is broken, everything
        below it is untested, and reporting "TCP failed" for a packet that was
        never routed would name the wrong layer to go fix.
        """
        for layer in LAYER_ORDER:
            if self.state(layer) is LayerState.BLOCKED:
                return layer
        return None

    @property
    def verdict(self) -> FailureKind | None:
        """The failure this profile implies, or ``None``.

        ``None`` for an all-OK table and for a table that never ran -- an
        unreadable probe must not become a network diagnosis.
        """
        if self.unavailable:
            return None
        return _LAYER_VERDICT.get(self.first_break)

    @property
    def ping_ok(self) -> bool | None:
        """Tri-state on purpose: ``emulation_run.ping_reachable`` is nullable
        for the same reason -- "we did not manage to ask" is not "no"."""
        state = self.state(LinkLayer.ICMP)
        if state is LayerState.OK:
            return True
        if state is LayerState.BLOCKED:
            return False
        return None

    def as_failure(self) -> Failure | None:
        kind = self.verdict
        if kind is None:
            return None
        broken = self.first_break
        # Say so when the layers *below* the break were never actually measured.
        # A neighbour table with no entry at all cannot tell "no such host" from
        # "ARP unanswered", so a link-no-icmp reading with an unknown ARP row is
        # a weaker claim than the same reading with a resolved one -- and the
        # difference decides whether anyone goes looking at the bridge.
        unmeasured = [
            layer.value
            for layer in LAYER_ORDER[: LAYER_ORDER.index(broken)]
            if self.state(layer) is LayerState.UNKNOWN
        ]
        detail = f"link probe stops at the {broken.value} layer towards {self.guest_ip}"
        if unmeasured:
            detail += f" ({', '.join(unmeasured)} could not be measured)"
        return Failure(
            kind=kind,
            detail=detail,
            evidence={
                "probe": "link",
                "first_break": str(broken),
                "table": {p.layer.value: p.state.value for p in self.probes},
            },
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "guest_ip": self.guest_ip,
            "unavailable": self.unavailable,
            "first_break": str(self.first_break) if self.first_break else "",
            "layers": [p.to_dict() for p in self.probes],
        }


#: Which failure each broken layer means. Wording of the hints lives in
#: ``failures._KIND_HINT`` so the closed vocabulary stays in one place.
_LAYER_VERDICT: dict[LinkLayer, FailureKind] = {
    LinkLayer.ROUTE: FailureKind.LINK_NO_ROUTE,
    LinkLayer.ARP: FailureKind.LINK_NO_ARP,
    LinkLayer.ICMP: FailureKind.LINK_NO_ICMP,
    LinkLayer.SERVICE: FailureKind.LINK_NO_SERVICE,
}


Runner = Callable[[list[str], int], subprocess.CompletedProcess]


def parse_route(stdout: str, stderr: str = "", returncode: int = 0) -> LayerProbe:
    """``ip route get <ip>`` -> is there a path, and with which source address.

    The source address is the whole point of reading this. A route via
    ``src 192.168.1.254`` towards ``192.168.0.1`` is a route that cannot work:
    the guest will drop what arrives with that source. Recording ``src`` lets
    the ICMP row say *why* it went quiet instead of leaving it unattributed.
    """
    layer = LinkLayer.ROUTE
    if returncode != 0:
        text = (stderr or stdout).strip()
        if re.search(r"unreachable|network is down|no route", text, re.IGNORECASE):
            return LayerProbe(layer, LayerState.BLOCKED, text.splitlines()[0] if text else "no route")
        return LayerProbe(layer, LayerState.UNKNOWN, text.splitlines()[0] if text else "ip route failed")

    dev = src = None
    match = re.search(r"\bdev\s+(\S+)", stdout)
    if match:
        dev = match.group(1)
    match = re.search(r"\bsrc\s+(\S+)", stdout)
    if match:
        src = match.group(1)
    if dev is None and src is None:
        return LayerProbe(layer, LayerState.UNKNOWN, stdout.strip() or "unparseable route")
    return LayerProbe(layer, LayerState.OK, f"via {dev}" if dev else "", {"dev": dev or "", "src": src or ""})


def parse_ping(stdout: str, stderr: str = "", returncode: int = 0) -> LayerProbe:
    """``ping -c 2 -W 1 <ip>`` -> did the guest answer for itself.

    The exit code is the signal (measured: 0 with replies, 1 at 100% loss) and
    the loss line is the evidence. Both are kept because they disagree often
    enough to be worth showing: a guest that answers one of two packets exits 0
    while reading as lossy.
    """
    layer = LinkLayer.ICMP
    received = None
    match = re.search(r"(\d+)\s+received", stdout)
    if match:
        received = int(match.group(1))
    loss = ""
    match = re.search(r"([\d.]+)%\s+packet loss", stdout)
    if match:
        loss = f"{match.group(1)}% packet loss"

    if returncode == 0:
        return LayerProbe(layer, LayerState.OK, loss or "replies", {"received": received if received is not None else -1})
    if returncode == 1:
        return LayerProbe(
            layer,
            LayerState.BLOCKED,
            loss or "no reply",
            {"received": received if received is not None else 0},
        )
    text = (stderr or stdout).strip()
    return LayerProbe(
        layer,
        LayerState.UNKNOWN,
        text.splitlines()[0] if text else f"ping exited {returncode}",
    )


def parse_neigh(stdout: str, stderr: str = "", returncode: int = 0) -> LayerProbe:
    """``ip neigh show <ip>`` -> did the guest answer for its own address.

    Must be read *after* traffic has been attempted. The neighbour table is a
    cache of what sending produced, so an entry for a host nothing has talked to
    is absent whether or not the host is alive -- measured, not assumed. The
    two states that mean "answered" both carry a real MAC; ``INCOMPLETE`` and
    ``/proc/net/arp`` flags ``0x0`` both mean it did not.
    """
    layer = LinkLayer.ARP
    if returncode != 0:
        return LayerProbe(layer, LayerState.UNKNOWN, (stderr or stdout).strip() or "ip neigh failed")

    entry = ""
    mac = ""
    state = ""
    for line in stdout.splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        entry = line.strip()
        state = fields[-1].upper()
        match = re.search(r"\blladdr\s+([0-9a-f:]{17})", line, re.IGNORECASE)
        if match:
            mac = match.group(1).lower()
        break

    if not entry:
        return LayerProbe(layer, LayerState.UNKNOWN, "no neighbour entry", {"mac": "", "state": ""})
    if state in _ARP_UNANSWERED_STATES or mac == _NULL_MAC:
        # ``INCOMPLETE`` with no lladdr is what an unanswered ARP leaves behind,
        # and /proc/net/arp shows the same thing as flags 0x0. Reading it as
        # "unknown" would push the blame down to ICMP and name the wrong layer
        # for a link that never carried a frame.
        return LayerProbe(layer, LayerState.BLOCKED, f"ARP unanswered ({state or 'incomplete'})",
                          {"mac": mac, "state": state})
    if state in _ARP_RESOLVED_STATES and mac:
        return LayerProbe(layer, LayerState.OK, f"{state} {mac}", {"mac": mac, "state": state})
    return LayerProbe(layer, LayerState.UNKNOWN, entry, {"mac": mac, "state": state})


def parse_curl(stdout: str, stderr: str = "", returncode: int = 0) -> LayerProbe:
    """``curl -w %{http_code}`` -> did anything answer on the web port.

    Any code other than ``000`` counts as answered, including 403 and 404. A
    guest serving a login redirect on a port IRIS guessed wrong is still a
    working device, and calling that unreachable is how a wrong-port guess
    turns into a "the firmware is broken" verdict.
    """
    layer = LinkLayer.SERVICE
    code = (stdout or "").strip().split()[-1] if (stdout or "").strip() else ""
    if code.isdigit() and code != "000":
        return LayerProbe(layer, LayerState.OK, f"HTTP {code}", {"http_code": code})
    text = (stderr or "").strip()
    if code == "000":
        # curl's exit code is not read as a verdict. Measured in the image: 7
        # against one unreachable address, 28 against another, and 7 for a
        # refused connection on a reachable host -- so the same code covers "the
        # host is gone" and "the port is closed". Which one it was is precisely
        # what the layers above decide; here it is only recorded.
        return LayerProbe(layer, LayerState.BLOCKED, text.splitlines()[0] if text else "no answer",
                          {"http_code": code, "curl_rc": returncode})
    return LayerProbe(layer, LayerState.UNKNOWN, text.splitlines()[0] if text else "curl produced no code")


def probe_link(
    container: str,
    guest_ip: str,
    port: int = 80,
    *,
    runner: Runner,
    icmp_count: int = 2,
) -> LinkProfile:
    """Measure route, ARP, ICMP and the web port towards ``guest_ip``.

    Execution order is not report order, and the difference is the whole trick:
    ARP is read *after* ICMP has been attempted, because the neighbour entry is
    a by-product of trying to send. Probing ARP first would report it empty on a
    perfectly healthy link and then blame layer 2 for a layer 4 problem.

    Nothing here raises. A container that cannot run ``ip`` produces a profile
    whose ``unavailable`` is set and whose verdict is ``None`` -- the boot loop
    this runs inside must not die because a diagnostic could not be gathered.
    """
    steps = [
        (LinkLayer.ROUTE, ["docker", "exec", container, "ip", "route", "get", guest_ip], _ROUTE_TIMEOUT),
        # ICMP before ARP on purpose; see the docstring.
        (
            LinkLayer.ICMP,
            ["docker", "exec", container, "ping", "-c", str(icmp_count), "-W", "1", guest_ip],
            _ICMP_TIMEOUT,
        ),
        (LinkLayer.ARP, ["docker", "exec", container, "ip", "neigh", "show", guest_ip], _ARP_TIMEOUT),
        (
            LinkLayer.SERVICE,
            ["docker", "exec", container, "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "--max-time", "3", f"http://{guest_ip}:{port}/"],
            _SERVICE_TIMEOUT,
        ),
    ]
    parsers = {
        LinkLayer.ROUTE: parse_route,
        LinkLayer.ICMP: parse_ping,
        LinkLayer.ARP: parse_neigh,
        LinkLayer.SERVICE: parse_curl,
    }

    probes: dict[LinkLayer, LayerProbe] = {}
    unavailable = ""
    for layer, cmd, timeout in steps:
        try:
            done = runner(cmd, timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            # The runner is docker; a timeout or a missing binary arrives as an
            # exception, and one unreadable layer must not abort the table --
            # except the first, because with no route there is nothing below to
            # measure and the whole profile is unattributable.
            text = f"{type(exc).__name__}: {exc}"
            logger.debug(f"link probe {layer.value} could not run: {text}")
            probes[layer] = LayerProbe(layer, LayerState.UNKNOWN, text)
            if layer is LinkLayer.ROUTE:
                unavailable = text
            continue
        probes[layer] = parsers[layer](done.stdout, done.stderr, done.returncode)

    # Report in physical order regardless of the order they were gathered in.
    ordered = tuple(probes.get(layer) or LayerProbe(layer, LayerState.UNKNOWN, "not probed")
                    for layer in LAYER_ORDER)
    profile = LinkProfile(guest_ip=guest_ip, probes=ordered, unavailable=unavailable)
    logger.info(f"link probe towards {guest_ip}:{render_table(profile)}")
    return profile


def render_table(profile: LinkProfile) -> str:
    """One line per layer, for the log and for the terminal."""
    marks = {LayerState.OK: "ok", LayerState.BLOCKED: "BLOCKED", LayerState.UNKNOWN: "unknown"}
    rows = [f"{p.layer.value:<8} {marks[p.state]:<8} {p.detail}" for p in profile.probes]
    if profile.unavailable:
        rows.append(f"(probe unavailable: {profile.unavailable})")
    return "\n".join(["", *rows])