"""Why IRIS puts a second address on its own bridge.

The port forward is only half of reaching a guest's web server. The other half is
that the guest has to be willing to answer packets that come from the host, and a
router is built to refuse exactly that when the source is not on its own subnet.

Measured on Tenda DIR-868L (iid 6630). The guest came up correct in every respect
IRIS could see from the serial log: eth0 enslaved into br0, br0 holding
192.168.0.1/24, httpd bound to :80, and IRIS's own port forward already pointing
at 192.168.0.1:80. Nothing answered. The layer-by-layer probe:

* ARP resolved and went REACHABLE, so the frames were arriving and the guest was
  answering them -- L2 was fine.
* ICMP to 192.168.0.1 went unanswered, and TCP to :80 timed out rather than being
  refused, so nothing was rejecting the connection either.
* The bridge sat at 192.168.1.254/16 while the guest's LAN was 192.168.0.0/24.

Adding 192.168.0.254/24 to the same bridge -- one command, no other change --
turned both into HTTP 200 and working ICMP, with the already-running socat forward
picking it up without being touched. The kernel picks the source address in the
destination's own subnet by itself, which is why nothing downstream had to change.

So the fix is a host-side address, not a guest-side change, and not a new forward.
These tests pin the address arithmetic and the command; the measurement above is
what the arithmetic exists to reproduce.
"""

from __future__ import annotations

import subprocess

import pytest

from iris.emulate import orchestrator
from iris.emulate.orchestrator import (
    _host_address_on_guest_subnet,
    _place_host_on_guest_subnet,
)


class TestHostAddressOnGuestSubnet:
    def test_the_dir868l_case(self):
        """The address that turned HTTP 000 into HTTP 200."""
        assert _host_address_on_guest_subnet("192.168.0.1") == "192.168.0.254"

    def test_it_shares_the_guests_first_three_octets(self):
        for guest in ("192.168.0.1", "10.0.7.1", "172.16.254.1"):
            host = _host_address_on_guest_subnet(guest)
            assert host is not None
            assert host.rsplit(".", 1)[0] == guest.rsplit(".", 1)[0]

    def test_it_never_lands_on_the_guest_itself(self):
        assert _host_address_on_guest_subnet("192.168.0.254") == "192.168.0.253"

    def test_a_guest_already_at_253_still_gets_a_free_address(self):
        assert _host_address_on_guest_subnet("192.168.0.253") == "192.168.0.254"

    @pytest.mark.parametrize("bad", ["", "192.168.0", "192.168.0.1.5", "192.168.0.x",
                                     "192.168.0.256", "not-an-ip", "192.168.0.1 "])
    def test_garbage_in_gives_nothing_out(self, bad):
        """The address is built by string surgery, so this is where it could go wrong.

        A malformed address reaching `ip addr add` fails, but only after the guest
        has already been waited on; refusing it here costs nothing and cannot be
        mistaken for a boot failure.
        """
        assert _host_address_on_guest_subnet(bad) is None


class _Recorder:
    """Stands in for ``_run`` and records the docker commands, nothing else."""

    def __init__(self, returncode: int = 0, stderr: str = ""):
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.stderr = stderr

    def __call__(self, cmd: list[str], timeout: int = 60):
        self.calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, self.returncode, "", self.stderr)


@pytest.fixture()
def recorder(monkeypatch):
    def make(returncode: int = 0, stderr: str = "") -> _Recorder:
        rec = _Recorder(returncode, stderr)
        monkeypatch.setattr(orchestrator, "_run", rec)
        return rec
    return make


class TestPlaceHostOnGuestSubnet:
    def test_it_adds_a_24_inside_the_guests_subnet_to_the_right_bridge(self, recorder):
        rec = recorder()
        assert _place_host_on_guest_subnet("iris-qemu-6630", 6630, "192.168.0.1") is True
        assert rec.calls == [[
            "docker", "exec", "iris-qemu-6630", "ip", "addr", "add",
            "192.168.0.254/24", "dev", "br6630",
        ]]

    def test_an_address_already_there_is_success_not_failure(self, recorder):
        """`ip addr add` on an address the bridge already has exits 2, not 0.

        Reporting that as a failure would log a warning on every re-run of a guest
        whose bridge was set up by an earlier attempt, which is the normal case when
        a boot is retried.
        """
        recorder(returncode=2, stderr="RTNETLINK answers: File exists")
        assert _place_host_on_guest_subnet("c", 1, "192.168.0.1") is True

    def test_a_real_refusal_is_reported_but_not_fatal(self, recorder):
        """The address only helps the guest's filter accept us; the forward stands
        without it, so this must not be escalated into a boot failure."""
        recorder(returncode=255, stderr="Cannot find device \"br1\"")
        assert _place_host_on_guest_subnet("c", 1, "192.168.0.1") is False

    def test_an_unusable_address_stops_before_docker(self, recorder):
        rec = recorder()
        assert _place_host_on_guest_subnet("c", 1, "nonsense") is False
        assert rec.calls == []

    def test_the_default_forwarded_case_still_works(self, recorder):
        """When the guest is where IRIS expected, the added address is a second
        address on the same subnet, not a move."""
        rec = recorder()
        assert _place_host_on_guest_subnet("c", 1, "192.168.1.1") is True
        assert rec.calls[0][-3:] == ["192.168.1.254/24", "dev", "br1"]