"""The link is measured, not guessed.

0.3.12 cost two rounds of wrong conclusions because every network verdict came
out of a regex over the boot log. DIR-868L's log said "no address, no NIC"; the
guest held ``br0=192.168.0.1`` and an ``httpd`` on ``:80``, and the actual fault
was on the host side, visible only by hand as four commands. These tests pin the
parsers to the output those commands really produce -- captured inside
``iris-emulate:latest``, including the two states that are easy to confuse.

The distinction under test throughout is ``BLOCKED`` versus ``UNKNOWN``. A
container with no ``ping`` binary, a docker that will not start, a curl that
printed nothing: none of those is a guest with a broken network, and a probe that
reports them as one sends whoever reads the table to fix the wrong layer.
"""

from __future__ import annotations

import subprocess

import pytest

from iris.emulate.linkprobe import (
    LAYER_ORDER,
    LayerState,
    LinkLayer,
    LinkProfile,
    parse_curl,
    parse_neigh,
    parse_ping,
    parse_route,
    probe_link,
    render_table,
)
from iris.failures import FailureKind

# -- Captured verbatim from `iris-emulate:latest`, not written from memory ----

#: `ip route get 10.77.0.9` on a bridge with nothing behind it. Note this
#: SUCCEEDS: the kernel picked a path. A route is not delivery.
ROUTE_VIA_BRIDGE = "10.77.0.9 dev brtest src 10.77.0.254 uid 0 \n    cache \n"

#: The 0.3.12 DIR-868L case, verbatim from docs/08: right device, wrong subnet.
ROUTE_DIR868L = "192.168.0.1 dev br6630 src 192.168.1.254 uid 0 \n    cache \n"

#: ARP answered. This is what a working guest looks like at layer 2.
NEIGH_REACHABLE = "192.168.0.1 dev br6630 lladdr 00:de:fa:1a:01:00 REACHABLE\n"

#: ARP attempted and unanswered -- measured, not INFERRED: a veth peer that never
#: got an address leaves exactly this behind, with 00:00:00:00:00:00 in
#: /proc/net/arp and flags 0x0.
NEIGH_INCOMPLETE = "10.77.0.2 dev veth-a  INCOMPLETE\n"

PING_OK = (
    "PING 10.77.0.2 (10.77.0.2) 56(84) bytes of data.\n"
    "64 bytes from 10.77.0.2: icmp_seq=1 ttl=64 time=0.057 ms\n"
    "\n--- 10.77.0.2 ping statistics ---\n"
    "2 packets transmitted, 2 received, 0% packet loss, time 1007ms\n"
)

PING_LOSS = (
    "PING 10.77.0.9 (10.77.0.9) 56(84) bytes of data.\n"
    "\n--- 10.77.0.9 ping statistics ---\n"
    "2 packets transmitted, 0 received, 100% packet loss, time 1017ms\n"
)


def done(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["fake"], returncode, stdout, stderr)


class TestRoute:
    def test_a_chosen_path_reads_as_healthy(self):
        probe = parse_route(ROUTE_VIA_BRIDGE, returncode=0)
        assert probe.state is LayerState.OK
        assert probe.evidence["dev"] == "brtest"
        assert probe.evidence["src"] == "10.77.0.254"

    def test_the_source_address_is_recorded_because_it_is_the_whole_point(self):
        """`src 192.168.1.254` towards a 192.168.0.0/24 guest is a route that
        cannot work, and only the src field says so."""
        probe = parse_route(ROUTE_DIR868L, returncode=0)
        assert probe.state is LayerState.OK
        assert probe.evidence["src"] == "192.168.1.254"
        assert probe.evidence["dev"] == "br6630"

    def test_an_unreachable_network_is_blocked(self):
        probe = parse_route("", stderr="RTNETLINK answers: Network is unreachable", returncode=2)
        assert probe.state is LayerState.BLOCKED
        assert "unreachable" in probe.detail

    def test_a_route_failure_we_cannot_read_is_not_a_blocked_route(self):
        probe = parse_route("", stderr="docker: command not found", returncode=127)
        assert probe.state is LayerState.UNKNOWN


class TestArp:
    def test_a_resolved_neighbour_is_healthy(self):
        probe = parse_neigh(NEIGH_REACHABLE, returncode=0)
        assert probe.state is LayerState.OK
        assert probe.evidence["mac"] == "00:de:fa:1a:01:00"
        assert probe.evidence["state"] == "REACHABLE"

    def test_an_incomplete_entry_is_blocked(self):
        probe = parse_neigh(NEIGH_INCOMPLETE, returncode=0)
        assert probe.state is LayerState.BLOCKED
        assert "unanswered" in probe.detail

    def test_a_null_mac_counts_as_unanswered_even_with_a_resolved_state(self):
        """`ip neigh` printed `lladdr 00:00:00:00:00:00` -- treating that as an
        address would report L2 healthy on a dead link."""
        probe = parse_neigh("10.77.0.2 dev veth-a lladdr 00:00:00:00:00:00 STALE\n", returncode=0)
        assert probe.state is LayerState.BLOCKED

    def test_a_failed_entry_is_blocked(self):
        probe = parse_neigh("10.77.0.2 dev veth-a FAILED\n", returncode=0)
        assert probe.state is LayerState.BLOCKED

    def test_an_empty_table_is_unknown_not_blocked(self):
        """The neighbour table is a cache of what sending produced. Before any
        traffic it is empty on a perfectly healthy link, so 'empty' cannot mean
        'the guest is dead' -- which is exactly why ARP is read after ICMP."""
        probe = parse_neigh("", returncode=0)
        assert probe.state is LayerState.UNKNOWN

    @pytest.mark.parametrize("state", ["STALE", "DELAY", "PROBE", "PERMANENT"])
    def test_every_resolved_state_counts_as_answered(self, state: str):
        probe = parse_neigh(f"192.168.0.1 dev br0 lladdr 00:de:fa:1a:01:00 {state}\n", returncode=0)
        assert probe.state is LayerState.OK

    def test_a_state_we_do_not_know_is_unknown_rather_than_a_guess(self):
        probe = parse_neigh("192.168.0.1 dev br0 lladdr 00:de:fa:1a:01:00 NUD_NEW\n", returncode=0)
        assert probe.state is LayerState.UNKNOWN


class TestIcmp:
    def test_replies_are_healthy(self):
        probe = parse_ping(PING_OK, returncode=0)
        assert probe.state is LayerState.OK
        assert probe.evidence["received"] == 2

    def test_total_loss_is_blocked(self):
        probe = parse_ping(PING_LOSS, returncode=1)
        assert probe.state is LayerState.BLOCKED
        assert probe.evidence["received"] == 0
        assert "100%" in probe.detail

    def test_a_missing_ping_binary_is_unknown_not_a_dead_network(self):
        """Measured: `nc` and `arping` are absent from the image and `ping` is
        present. A container built without ping must not produce link-no-icmp."""
        probe = parse_ping("", stderr="docker: exec: ping: not found", returncode=126)
        assert probe.state is LayerState.UNKNOWN

    def test_the_loss_line_is_kept_as_evidence_when_the_code_disagrees(self):
        """ping exits 0 on one reply out of two; the loss line is what shows it."""
        lossy = PING_OK.replace("2 received, 0% packet loss", "1 received, 50% packet loss")
        probe = parse_ping(lossy, returncode=0)
        assert probe.state is LayerState.OK
        assert probe.detail == "50% packet loss"


class TestService:
    def test_any_http_code_means_something_answered(self):
        """A guest serving a 403 on the port IRIS guessed is still a working
        device; calling that unreachable is how a wrong port becomes a verdict
        about the firmware."""
        for code in ("200", "301", "403", "404"):
            probe = parse_curl(code, returncode=0)
            assert probe.state is LayerState.OK, code
            assert probe.evidence["http_code"] == code

    def test_no_code_means_blocked(self):
        probe = parse_curl("000", stderr="", returncode=7)
        assert probe.state is LayerState.BLOCKED
        assert probe.evidence["curl_rc"] == 7

    def test_curl_rc_is_recorded_but_not_interpreted(self):
        """Measured: curl exits 7 both for an unreachable host and a closed port.
        Which one it was is what the layers above decide, so rc 7 is evidence,
        not a conclusion."""
        unreachable = parse_curl("000", stderr="Failed to connect", returncode=7)
        refused = parse_curl("000", stderr="Failed to connect", returncode=7)
        assert unreachable.evidence["curl_rc"] == refused.evidence["curl_rc"]

    def test_no_output_at_all_is_unknown(self):
        probe = parse_curl("", stderr="", returncode=0)
        assert probe.state is LayerState.UNKNOWN


class TestProfile:
    @staticmethod
    def profile(**states: object) -> LinkProfile:
        probes = []
        for layer in LAYER_ORDER:
            state = LayerState(states.get(layer, LayerState.OK))  # type: ignore[arg-type]
            probes.append(_probe(layer, state))
        return LinkProfile(guest_ip="192.168.0.1", probes=tuple(probes))

    def test_rows_are_reported_in_physical_order(self):
        assert [p.layer for p in self.profile().probes] == list(LAYER_ORDER)

    def test_a_clean_path_has_no_verdict(self):
        assert self.profile().verdict is None
        assert self.profile().as_failure() is None

    def test_each_broken_layer_names_its_own_failure(self):
        expected = {
            LinkLayer.ROUTE: FailureKind.LINK_NO_ROUTE,
            LinkLayer.ARP: FailureKind.LINK_NO_ARP,
            LinkLayer.ICMP: FailureKind.LINK_NO_ICMP,
            LinkLayer.SERVICE: FailureKind.LINK_NO_SERVICE,
        }
        for layer, kind in expected.items():
            broken = {other: LayerState.OK for other in LAYER_ORDER}
            broken[layer] = LayerState.BLOCKED
            profile = self.profile(**{k.value: v for k, v in broken.items()})
            assert profile.verdict is kind, layer

    def test_the_shallowest_break_wins(self):
        """A packet that never routed did not fail to be served -- naming TCP
        here would send the reader to the wrong layer."""
        profile = self.profile(**{
            LinkLayer.ROUTE.value: LayerState.OK,
            LinkLayer.ARP.value: LayerState.BLOCKED,
            LinkLayer.ICMP.value: LayerState.BLOCKED,
            LinkLayer.SERVICE.value: LayerState.BLOCKED,
        })
        assert profile.first_break is LinkLayer.ARP
        assert profile.verdict is FailureKind.LINK_NO_ARP

    def test_unknown_layers_never_manufacture_a_failure(self):
        profile = self.profile(**{
            LinkLayer.ROUTE.value: LayerState.UNKNOWN,
            LinkLayer.ARP.value: LayerState.UNKNOWN,
            LinkLayer.ICMP.value: LayerState.UNKNOWN,
            LinkLayer.SERVICE.value: LayerState.UNKNOWN,
        })
        assert profile.verdict is None
        assert profile.as_failure() is None

    def test_an_unavailable_probe_suppresses_the_verdict_entirely(self):
        """Even a measured break is not reported when the table itself is
        unattributable -- a half-read table invites a confident wrong layer."""
        base = self.profile(**{LinkLayer.SERVICE.value: LayerState.BLOCKED})
        profile = LinkProfile(
            guest_ip=base.guest_ip, probes=base.probes, unavailable="docker: not found"
        )
        assert profile.first_break is LinkLayer.SERVICE
        assert profile.verdict is None
        assert profile.as_failure() is None

    def test_ping_reachable_is_tri_state(self):
        assert self.profile().ping_ok is True
        assert self.profile(**{LinkLayer.ICMP.value: LayerState.BLOCKED}).ping_ok is False
        assert self.profile(**{LinkLayer.ICMP.value: LayerState.UNKNOWN}).ping_ok is None

    def test_the_failure_carries_the_whole_table(self):
        profile = self.profile(**{LinkLayer.ICMP.value: LayerState.BLOCKED})
        failure = profile.as_failure()
        assert failure is not None
        assert failure.evidence["first_break"] == "icmp"
        assert failure.evidence["table"] == {
            "route": "ok", "arp": "ok", "icmp": "blocked", "service": "ok",
        }
        assert failure.is_failure

    def test_the_table_renders_every_row(self):
        profile = self.profile(**{LinkLayer.ARP.value: LayerState.BLOCKED})
        text = render_table(profile)
        assert "BLOCKED" in text
        for layer in LAYER_ORDER:
            assert layer.value in text

    def test_a_verdict_built_on_an_unmeasured_layer_says_so(self):
        """Measured case: against a docker bridge with nothing behind it, the
        neighbour table has no entry at all, so ARP is unknown while ICMP is
        blocked. link-no-icmp is then a weaker claim than it looks, and the
        detail is the only place a reader can see that."""
        profile = self.profile(**{
            LinkLayer.ARP.value: LayerState.UNKNOWN,
            LinkLayer.ICMP.value: LayerState.BLOCKED,
            LinkLayer.SERVICE.value: LayerState.BLOCKED,
        })
        detail = profile.as_failure().detail
        assert "arp could not be measured" in detail

    def test_a_verdict_with_every_lower_layer_measured_does_not_hedge(self):
        profile = self.profile(**{
            LinkLayer.ICMP.value: LayerState.BLOCKED,
            LinkLayer.SERVICE.value: LayerState.BLOCKED,
        })
        assert "could not be measured" not in profile.as_failure().detail


def _probe(layer: LinkLayer, state: LayerState):
    from iris.emulate.linkprobe import LayerProbe

    return LayerProbe(layer, state, "")


class TestProbeLink:
    """``probe_link`` against a scripted container, including the 0.3.12 case."""

    @staticmethod
    def runner(outputs: dict[str, subprocess.CompletedProcess], log: list[list[str]] | None = None):
        def run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
            if log is not None:
                log.append(list(cmd))
            key = " ".join(cmd[3:6])
            for candidate, result in outputs.items():
                if candidate in key:
                    return result
            return done(stderr=f"unexpected probe: {key}")

        return run

    def test_the_dir868l_signature_is_reproduced_end_to_end(self):
        """Route via the right device, ARP answered, ICMP silent, no service.

        This is the combination that was read as "the firmware is broken" for two
        releases. It must come back as link-no-icmp.
        """
        seen: list[list[str]] = []
        runner = self.runner(
            {
                "ip route get": done(ROUTE_DIR868L),
                "ping -c": done(PING_LOSS, returncode=1),
                "ip neigh show": done(NEIGH_REACHABLE),
                "curl": done("000", returncode=7),
            },
            seen,
        )
        profile = probe_link("iris-qemu-6630", "192.168.0.1", runner=runner)
        assert profile.verdict is FailureKind.LINK_NO_ICMP
        assert profile.first_break is LinkLayer.ICMP
        assert profile.ping_ok is False
        assert [p.state for p in profile.probes] == [
            LayerState.OK, LayerState.OK, LayerState.BLOCKED, LayerState.BLOCKED,
        ]

    def test_the_icmp_failure_hints_at_the_subnet_because_that_is_the_measured_cause(self):
        runner = self.runner({
            "ip route get": done(ROUTE_DIR868L),
            "ping -c": done(PING_LOSS, returncode=1),
            "ip neigh show": done(NEIGH_REACHABLE),
            "curl": done("000", returncode=7),
        })
        hint = probe_link("c", "192.168.0.1", runner=runner).as_failure().hint
        assert "source address" in hint
        assert "subnet" in hint

    def test_arp_is_read_after_the_traffic_that_fills_the_table(self):
        seen: list[list[str]] = []
        runner = self.runner(
            {
                "ip route get": done(ROUTE_VIA_BRIDGE),
                "ping -c": done(PING_OK),
                "ip neigh show": done(NEIGH_REACHABLE),
                "curl": done("200"),
            },
            seen,
        )
        profile = probe_link("c", "10.77.0.2", runner=runner)
        order = [" ".join(cmd) for cmd in seen]

        def at(marker: str) -> int:
            return next(i for i, cmd in enumerate(order) if marker in cmd)

        assert at("ping") < at("ip neigh"), "ARP must be read after the traffic that fills the table"
        assert profile.verdict is None
        assert profile.ping_ok is True

    def test_a_healthy_link_produces_no_failure_at_all(self):
        runner = self.runner({
            "ip route get": done(ROUTE_VIA_BRIDGE),
            "ping -c": done(PING_OK),
            "ip neigh show": done(NEIGH_REACHABLE),
            "curl": done("200"),
        })
        assert probe_link("c", "10.77.0.2", runner=runner).as_failure() is None

    def test_a_dead_link_stops_at_arp(self):
        runner = self.runner({
            "ip route get": done(ROUTE_VIA_BRIDGE),
            "ping -c": done(PING_LOSS, returncode=1),
            "ip neigh show": done(NEIGH_INCOMPLETE),
            "curl": done("000", returncode=7),
        })
        profile = probe_link("c", "10.77.0.9", runner=runner)
        assert profile.verdict is FailureKind.LINK_NO_ARP

    def test_a_guest_with_the_wrong_web_port_stops_at_service(self):
        """ICMP answers, ARP answers, nothing on :80 -- the wrong-port case,
        which a log-only diagnosis cannot tell from a dead network."""
        runner = self.runner({
            "ip route get": done(ROUTE_VIA_BRIDGE),
            "ping -c": done(PING_OK),
            "ip neigh show": done(NEIGH_REACHABLE),
            "curl": done("000", stderr="Failed to connect", returncode=7),
        })
        profile = probe_link("c", "10.77.0.2", runner=runner)
        assert profile.verdict is FailureKind.LINK_NO_SERVICE
        assert profile.ping_ok is True

    def test_a_thrown_probe_marks_one_layer_unknown_and_keeps_the_table(self):
        def run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
            if "ping" in " ".join(cmd):
                raise subprocess.TimeoutExpired(cmd, timeout)
            if "neigh" in " ".join(cmd):
                return done(NEIGH_REACHABLE)
            if "curl" in " ".join(cmd):
                return done("000", returncode=7)
            return done(ROUTE_VIA_BRIDGE)

        profile = probe_link("c", "10.77.0.2", runner=run)
        assert profile.state(LinkLayer.ICMP) is LayerState.UNKNOWN
        assert profile.state(LinkLayer.SERVICE) is LayerState.BLOCKED
        # ARP answered and ICMP was never measured, so the service layer is the
        # first thing actually known to be broken.
        assert profile.verdict is FailureKind.LINK_NO_SERVICE
        assert profile.ping_ok is None

    def test_docker_being_absent_invalidates_the_whole_table(self):
        def run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
            raise FileNotFoundError("docker")

        profile = probe_link("c", "10.77.0.2", runner=run)
        assert profile.unavailable
        assert profile.verdict is None
        assert profile.as_failure() is None
        assert all(p.state is LayerState.UNKNOWN for p in profile.probes)
        assert "unavailable" in render_table(profile)

    def test_the_probes_stay_inside_the_timeout_the_boot_loop_can_afford(self):
        seen: list[tuple[list[str], int]] = []

        def run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
            seen.append((list(cmd), timeout))
            return done("200")

        probe_link("c", "10.77.0.2", runner=run)
        assert len(seen) == len(LAYER_ORDER)
        assert all(0 < timeout <= 10 for _, timeout in seen)

    def test_the_probes_reach_the_guest_directly_not_through_the_forward(self):
        """Measuring 127.0.0.1:<host_port> would measure socat, not the link."""
        seen: list[list[str]] = []
        runner = self.runner(
            {
                "ip route get": done(ROUTE_VIA_BRIDGE),
                "ping -c": done(PING_OK),
                "ip neigh show": done(NEIGH_REACHABLE),
                "curl": done("200"),
            },
            seen,
        )
        probe_link("iris-qemu-6711", "192.168.0.1", 8080, runner=runner)
        curl = next(cmd for cmd in seen if "curl" in cmd)
        assert "http://192.168.0.1:8080/" in curl
        assert "127.0.0.1" not in " ".join(curl)

    def test_the_port_is_configurable_for_a_non_default_web_port(self):
        seen: list[list[str]] = []
        runner = self.runner(
            {
                "ip route get": done(ROUTE_VIA_BRIDGE),
                "ping -c": done(PING_OK),
                "ip neigh show": done(NEIGH_REACHABLE),
                "curl": done("404"),
            },
            seen,
        )
        profile = probe_link("c", "10.77.0.2", 8081, runner=runner)
        assert any("http://10.77.0.2:8081/" in cmd for cmd in seen)
        assert profile.verdict is None