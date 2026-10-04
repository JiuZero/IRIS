"""The serial console: which port it gets, who may reach it, and when it stops existing.

The console is the one channel into a guest that accepts bytes as well as showing
them, so its port is the one thing in an emulation that must not be published the
way the web forward is. These tests cover the three claims that are easy to lose:
the port comes from a range of its own, it is bound to loopback, and it is
forgotten exactly when the container goes away.
"""

from __future__ import annotations

import socket
import subprocess
from pathlib import Path

import pytest

from iris.emulate import auto, orchestrator
from iris.emulate.auto import SERIAL_PORT_RANGE, pick_host_port, pick_serial_port

ORCHESTRATOR_SOURCE = Path(orchestrator.__file__).read_text(encoding="utf-8")


class TestSerialPortAllocation:
    def test_it_lands_in_the_serial_range(self):
        port = pick_serial_port()
        start, stop = SERIAL_PORT_RANGE
        assert start <= port <= stop

    def test_the_serial_range_cannot_collide_with_the_web_forward(self):
        """Both are found by probing before the container exists, so overlapping
        ranges would let a serial pick take the port a web forward is about to
        need -- or the reverse, which fails later and further from the cause."""
        web_start, web_stop = 8080, 8199
        serial_start, serial_stop = SERIAL_PORT_RANGE
        assert serial_start > web_stop or serial_stop < web_start

    def test_an_occupied_port_is_skipped(self):
        """Docker publishes the port for as long as the container lives, so the
        next emulation has to be able to find another one."""
        first = pick_serial_port()
        held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        held.bind(("127.0.0.1", first))
        held.listen(1)
        try:
            assert pick_serial_port() != first
        finally:
            held.close()

    def test_the_probe_uses_the_address_the_port_will_be_published_on(self, monkeypatch):
        """Probing 0.0.0.0 instead does not fail on Windows when 127.0.0.1 on the
        same port is taken, so the console port would be handed out twice."""
        seen: dict[str, object] = {}

        def fake(preferred: int = 0, start: int = 0, stop: int = 0, bind_host: str = "") -> int:
            seen.update(bind_host=bind_host, start=start, stop=stop)
            return 46000

        monkeypatch.setattr(auto, "pick_host_port", fake)
        assert auto.pick_serial_port() == 46000
        assert seen["bind_host"] == "127.0.0.1"
        assert (seen["start"], seen["stop"]) == SERIAL_PORT_RANGE

    def test_a_preferred_port_wins_when_it_is_free(self):
        preferred = pick_serial_port()
        assert pick_serial_port(preferred=preferred) == preferred

    def test_the_shared_probe_is_what_allocates_it(self):
        """Not a second implementation: two probes would drift, and only one of
        them would ever learn that a bound port is not free."""
        start, stop = SERIAL_PORT_RANGE
        assert pick_host_port(preferred=0, start=start, stop=stop, bind_host="127.0.0.1") > 0


class TestTheConsolePortIsPublishedOnLoopbackOnly:
    """The web forward above it is deliberately bound wide; the console is not,
    and the difference is the whole point -- anyone who can reach a published
    serial port can type into the guest."""

    LOOPBACK_PUBLISH = '"-p", f"127.0.0.1:{serial_port}:{serial_port}"'

    def test_the_serial_publish_is_loopback_bound(self):
        assert self.LOOPBACK_PUBLISH in ORCHESTRATOR_SOURCE

    def test_the_console_is_published_no_other_way(self):
        """So a later edit cannot add a wide console publish beside this one: with
        the loopback line set aside, the console port appears nowhere else in the
        container's published ports."""
        create = ORCHESTRATOR_SOURCE.split("create_cmd = [")[1].split("]")[0]
        assert "{serial_port}" not in create.replace(self.LOOPBACK_PUBLISH, "")

    def test_the_web_forward_keeps_its_existing_bind(self):
        """Recorded so the line above is not read as a rule for every published
        port: the web forward is read-only and was already wide."""
        assert '"-p", f"{host_port}:{host_port}"' in ORCHESTRATOR_SOURCE


class TestTheConsolePortReachesTheScript:
    def test_it_is_injected_as_an_environment_variable(self):
        assert 'f"IRIS_SERIAL_PORT={serial_port}"' in ORCHESTRATOR_SOURCE

    def test_the_container_is_created_before_the_port_is_known_to_the_script(self):
        """Ordering, because the script refuses to run without it: publish, then
        launch, never the reverse."""
        create = ORCHESTRATOR_SOURCE.index("create_cmd = [")
        launch = ORCHESTRATOR_SOURCE.index('f"IRIS_SERIAL_PORT={serial_port}"')
        assert create < launch


class TestTheConsolePortIsForgottenWithTheContainer:
    def test_an_unknown_instance_has_no_console(self):
        assert orchestrator.serial_port_of(999999) is None

    def test_stopping_an_instance_drops_its_port(self, monkeypatch):
        monkeypatch.setitem(orchestrator._SERIAL_PORTS, 4242, 46001)
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(orchestrator.subprocess, "run", fake_run)
        assert orchestrator.stop_emulation(4242) is True
        assert orchestrator.serial_port_of(4242) is None
        assert calls and calls[0][:4] == ["docker", "rm", "-f", "iris-qemu-4242"]

    def test_the_port_is_dropped_even_when_docker_fails(self, monkeypatch):
        """A container docker could not remove is exactly the case where a stale
        port would keep advertising a console that is gone or going."""
        monkeypatch.setitem(orchestrator._SERIAL_PORTS, 4243, 46002)
        monkeypatch.setattr(
            orchestrator.subprocess, "run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "boom"),
        )
        assert orchestrator.stop_emulation(4243) is False
        assert orchestrator.serial_port_of(4243) is None

    def test_it_is_dropped_before_the_container_is_removed(self, monkeypatch):
        """The order is what makes a racing caller safe: cleared first, a caller
        cannot be handed a port that is already on its way out."""
        seen: list[int | None] = []
        monkeypatch.setitem(orchestrator._SERIAL_PORTS, 4244, 46003)

        def fake_run(cmd, **kwargs):
            seen.append(orchestrator.serial_port_of(4244))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(orchestrator.subprocess, "run", fake_run)
        orchestrator.stop_emulation(4244)
        assert seen == [None]


class TestThePortIsOnlyAdvertisedOnceQemuIsUp:
    def test_the_mapping_is_written_after_the_launch_succeeds(self):
        """Before QEMU runs there is no listener, and a console advertised for a
        chardev that never came up can only fail to connect."""
        launch = ORCHESTRATOR_SOURCE.index('f"IRIS_SERIAL_PORT={serial_port}"')
        record = ORCHESTRATOR_SOURCE.index("_SERIAL_PORTS[iid] = serial_port")
        assert record > launch

    @pytest.mark.parametrize("marker", ["_SERIAL_PORTS[iid] = serial_port"])
    def test_the_write_is_guarded_by_the_launch_failure_path(self, marker):
        """Every path that removes the container without reaching QEMU must not
        leave a published port behind."""
        source = ORCHESTRATOR_SOURCE
        record = source.index(marker)
        before = source[:record]
        # The launch failure branch returns before the record, which is the point:
        # assert it exists and precedes the record.
        assert before.index("FailureKind.QEMU_START_FAILED") < record