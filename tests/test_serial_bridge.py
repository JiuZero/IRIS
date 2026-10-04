"""串口桥:扇出、输入仲裁、背压,以及上游不存在时的行为。

上游是一个真实的 asyncio TCP 服务,不是 mock:桥要处理的正是"对端连不上""对端发来
一大块""对端写到一半断开"这些真实时序,用内存对象替身会把它们全绕过去。

项目没有异步测试插件,所以每个用例用 ``asyncio.run`` 驱动一个完整的场景协程。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from iris.api.serial_bridge import (
    SUBSCRIBER_BUFFER_BYTES,
    SerialBridge,
    SerialUnavailable,
    Subscription,
)


def run(scenario) -> Any:
    """Drive one async scenario to completion."""
    return asyncio.run(asyncio.wait_for(scenario(), timeout=20))


class FakeGuest:
    """A real TCP endpoint standing in for QEMU's serial chardev.

    A chardev accepts one client and nothing else, which is the property that makes
    the bridge necessary; serving over a real socket reproduces it -- a second
    connect is accepted by the OS backlog but never receives the stream, exactly as
    QEMU's second client would get nothing.
    """

    def __init__(self) -> None:
        self._server: asyncio.AbstractServer | None = None
        self.port = 0
        self.received = bytearray()
        self.connected = asyncio.Event()
        self._writer: asyncio.StreamWriter | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._on_client, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writer = writer
        self.connected.set()
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                self.received += data
        except ConnectionError:
            pass

    def emit(self, data: bytes) -> None:
        if self._writer is None:
            raise AssertionError("nothing has connected to the guest yet")
        self._writer.write(data)

    async def stop(self) -> None:
        if self._writer is not None:
            self._writer.close()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()


class Collector:
    """A subscriber that keeps whatever it was handed, for assertions."""

    def __init__(self, name: str = "c1") -> None:
        self.bytes = bytearray()
        self.texts: list[str] = []
        self.sub = Subscription(name=name, send_bytes=self._bytes, send_text=self._text)

    async def _bytes(self, data: bytes) -> None:
        self.bytes += data

    async def _text(self, data: str) -> None:
        self.texts.append(data)

    async def pump(self, bridge: SerialBridge) -> asyncio.Task[None]:
        return asyncio.create_task(bridge.pump_subscriber(self.sub))

    async def wait_for_bytes(self, count: int, timeout: float = 5.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while len(self.bytes) < count:
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(f"expected {count} bytes, saw {len(self.bytes)}: {bytes(self.bytes)!r}")
            await asyncio.sleep(0.01)

    async def wait_for_text(self, needle: str, timeout: float = 5.0) -> str:
        deadline = asyncio.get_running_loop().time() + timeout
        while not any(needle in t for t in self.texts):
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(f"no text containing {needle!r} in {self.texts!r}")
            await asyncio.sleep(0.01)
        return next(t for t in self.texts if needle in t)


async def _bridge(guest: FakeGuest, **kwargs: Any) -> SerialBridge:
    bridge = SerialBridge(iid=7002, port=guest.port, **kwargs)
    await bridge.start()
    # start() returns as soon as the client socket is up; the guest end is accepted
    # a beat later, and emit() before that has no writer to write through.
    await asyncio.wait_for(guest.connected.wait(), timeout=5)
    return bridge


class TestOutputFanOut:
    def test_a_subscriber_sees_what_the_guest_writes(self):
        async def scenario():
            guest = FakeGuest()
            await guest.start()
            try:
                bridge = await _bridge(guest)
                sub = Collector()
                bridge.subscribe(sub.sub)
                pump = await sub.pump(bridge)
                line = b"Linux version 4.9.37\r\n"
                guest.emit(line)
                await sub.wait_for_bytes(len(line))
                assert b"Linux version" in bytes(sub.bytes)
                pump.cancel()
                await bridge.close()
            finally:
                await guest.stop()

        run(scenario)

    def test_every_subscriber_sees_the_same_stream(self):
        """Two tabs on one instance is the case the bridge exists for: QEMU's
        chardev would hand the stream to whichever connected second."""

        async def scenario():
            guest = FakeGuest()
            await guest.start()
            try:
                bridge = await _bridge(guest)
                first, second = Collector("a"), Collector("b")
                for c in (first, second):
                    bridge.subscribe(c.sub)
                    await c.pump(bridge)
                line = b"IRIS-NETFIX: done\r\n"
                guest.emit(line)
                await first.wait_for_bytes(len(line))
                await second.wait_for_bytes(len(line))
                assert bytes(first.bytes) == bytes(second.bytes)
                assert bridge.subscriber_count == 2
                await bridge.close()
            finally:
                await guest.stop()

        run(scenario)

    def test_an_unsubscribed_tab_stops_receiving(self):
        async def scenario():
            guest = FakeGuest()
            await guest.start()
            try:
                bridge = await _bridge(guest)
                gone = Collector("gone")
                bridge.subscribe(gone.sub)
                await bridge.unsubscribe("gone")
                guest.emit(b"after unsubscribe\r\n")
                await asyncio.sleep(0.2)
                assert bytes(gone.bytes) == b""
                assert bridge.subscriber_count == 0
                await bridge.close()
            finally:
                await guest.stop()

        run(scenario)


class TestInputArbitration:
    def test_only_the_holder_can_write_to_the_guest(self):
        async def scenario():
            guest = FakeGuest()
            await guest.start()
            try:
                bridge = await _bridge(guest)
                holder, other = Collector("holder"), Collector("other")
                for c in (holder, other):
                    bridge.subscribe(c.sub)
                assert await bridge.claim_input("holder") is True
                assert await bridge.write_input("holder", b"uname -a\r") is True
                assert await bridge.write_input("other", b"rm -rf /\r") is False
                await asyncio.sleep(0.2)
                assert bytes(guest.received) == b"uname -a\r"
                await bridge.close()
            finally:
                await guest.stop()

        run(scenario)

    def test_a_second_claimant_is_refused_rather_than_preempting(self):
        """Preempting would eat the half-typed line of whoever is already typing."""

        async def scenario():
            guest = FakeGuest()
            await guest.start()
            try:
                bridge = await _bridge(guest)
                a, b = Collector("a"), Collector("b")
                bridge.subscribe(a.sub)
                bridge.subscribe(b.sub)
                assert await bridge.claim_input("a") is True
                assert await bridge.claim_input("b") is False
                assert bridge.input_holder == "a"
                assert a.sub.holds_input is True
                assert b.sub.holds_input is False
                await bridge.close()
            finally:
                await guest.stop()

        run(scenario)

    def test_reclaiming_your_own_input_is_not_a_conflict(self):
        async def scenario():
            guest = FakeGuest()
            await guest.start()
            try:
                bridge = await _bridge(guest)
                a = Collector("a")
                bridge.subscribe(a.sub)
                assert await bridge.claim_input("a") is True
                assert await bridge.claim_input("a") is True
                await bridge.close()
            finally:
                await guest.stop()

        run(scenario)

    def test_releasing_lets_someone_else_take_it(self):
        async def scenario():
            guest = FakeGuest()
            await guest.start()
            try:
                bridge = await _bridge(guest)
                a, b = Collector("a"), Collector("b")
                bridge.subscribe(a.sub)
                bridge.subscribe(b.sub)
                await bridge.claim_input("a")
                await bridge.release_input("a")
                assert bridge.input_holder is None
                assert a.sub.holds_input is False
                assert await bridge.claim_input("b") is True
                assert b.sub.holds_input is True
                await bridge.close()
            finally:
                await guest.stop()

        run(scenario)

    def test_a_dropped_tab_gives_its_input_up(self):
        """Otherwise a closed tab keeps the console read-only for everyone else,
        with nothing left to release it."""

        async def scenario():
            guest = FakeGuest()
            await guest.start()
            try:
                bridge = await _bridge(guest)
                a, b = Collector("a"), Collector("b")
                bridge.subscribe(a.sub)
                bridge.subscribe(b.sub)
                await bridge.claim_input("a")
                await bridge.unsubscribe("a")
                assert bridge.input_holder is None
                assert await bridge.claim_input("b") is True
                await bridge.close()
            finally:
                await guest.stop()

        run(scenario)

    def test_a_late_subscriber_is_told_who_already_holds_input(self):
        """Otherwise it renders an editable terminal for a few seconds before its
        first keystroke is refused."""

        async def scenario():
            guest = FakeGuest()
            await guest.start()
            try:
                bridge = await _bridge(guest)
                a = Collector("a")
                bridge.subscribe(a.sub)
                await bridge.claim_input("a")
                late = Collector("late")
                bridge.subscribe(late.sub)
                assert late.sub.holds_input is False
                await bridge.close()
            finally:
                await guest.stop()

        run(scenario)

    def test_input_is_refused_when_there_is_no_console_to_write_to(self):
        """A claim that succeeded before the guest went away must not report that
        the keystroke reached anything."""

        bridge = SerialBridge(iid=7002, port=1)
        sub = Collector()
        bridge.subscribe(sub.sub)
        assert asyncio.run(bridge.claim_input("c1")) is True
        assert asyncio.run(bridge.write_input("c1", b"x")) is False


class TestBackPressure:
    def test_a_slow_subscriber_loses_the_oldest_bytes_and_says_so(self):
        """A terminal that silently dropped output would look exactly like one that
        had produced none, so the loss has to be counted and reportable."""

        async def scenario():
            bridge = SerialBridge(iid=7002, port=1)
            sub = Collector()
            bridge.subscribe(sub.sub)
            chunk = b"x" * (SUBSCRIBER_BUFFER_BYTES // 4)
            for _ in range(8):
                sub.sub.offer(chunk)
            assert len(sub.sub.buffer) == SUBSCRIBER_BUFFER_BYTES
            assert sub.sub.dropped_bytes == 8 * len(chunk) - SUBSCRIBER_BUFFER_BYTES
            assert bridge.dropped_bytes == sub.sub.dropped_bytes
            # What survives is the newest output -- the tail -- because that is what
            # is being read. The head is what a stalled client falls behind on.
            assert bytes(sub.sub.buffer) == chunk * 4

        run(scenario)

    def test_anything_under_the_limit_is_never_dropped(self):
        async def scenario():
            bridge = SerialBridge(iid=7002, port=1)
            sub = Collector()
            bridge.subscribe(sub.sub)
            sub.sub.offer(b"a" * (SUBSCRIBER_BUFFER_BYTES - 1))
            sub.sub.offer(b"b")
            assert sub.sub.dropped_bytes == 0
            assert len(sub.sub.buffer) == SUBSCRIBER_BUFFER_BYTES

        run(scenario)

    def test_the_pump_sends_what_is_queued_and_keeps_listening(self):
        async def scenario():
            bridge = SerialBridge(iid=7002, port=1)
            sub = Collector()
            bridge.subscribe(sub.sub)
            pump = await sub.pump(bridge)
            sub.sub.offer(b"first ")
            sub.sub.offer(b"second")
            await sub.wait_for_bytes(12)
            assert bytes(sub.bytes) == b"first second"
            sub.sub.offer(b" third")
            await sub.wait_for_bytes(18)
            assert bytes(sub.bytes) == b"first second third"
            pump.cancel()
            await bridge.close()

        run(scenario)


class TestTheConsoleNotBeingThere:
    def test_it_is_retried_before_it_is_called_unavailable(self):
        """The bridge is often created seconds before QEMU binds the port -- the
        container exists while the kernel is still being loaded -- so one refused
        connect says nothing about whether this instance has a console."""

        async def scenario():
            attempts = 0
            guest = FakeGuest()

            async def connector():
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise ConnectionRefusedError("not yet")
                return await asyncio.open_connection("127.0.0.1", guest.port)

            await guest.start()
            try:
                bridge = SerialBridge(iid=7002, port=guest.port,
                                      connector=connector, connect_delay=0.01)
                await bridge.start()
                assert attempts == 3
                assert bridge.connected is True
                await bridge.close()
            finally:
                await guest.stop()

        run(scenario)

    def test_it_reports_the_port_it_tried(self):
        """A console that cannot be reached has to say where it looked, or the
        report is not actionable."""

        async def scenario():
            async def connector():
                raise ConnectionRefusedError("nope")

            bridge = SerialBridge(iid=7002, port=45999, connector=connector,
                                  connect_retries=2, connect_delay=0.01)
            with pytest.raises(SerialUnavailable) as excinfo:
                await bridge.start()
            message = str(excinfo.value)
            assert "45999" in message
            assert "7002" in message
            assert bridge.connected is False

        run(scenario)

    def test_the_guest_going_away_marks_the_bridge_closed_and_says_so(self):
        async def scenario():
            guest = FakeGuest()
            await guest.start()
            try:
                bridge = await _bridge(guest)
                sub = Collector()
                bridge.subscribe(sub.sub)
                pump = await sub.pump(bridge)
                assert await bridge.claim_input("c1") is True
                await guest.stop()
                await sub.wait_for_text("SERIAL_CLOSED")
                assert bridge.closed is True
                assert bridge.connected is False
                assert bridge.input_holder is None
                # And the keyboard is not accepted afterwards, silently or otherwise.
                assert await bridge.write_input("c1", b"x") is False
                pump.cancel()
            finally:
                await bridge.close()

        run(scenario)

    def test_closing_twice_is_not_an_error(self):
        """Shutdown paths overlap -- a Ctrl+C during an unmount -- and cleanup that
        raises on the second call turns an orderly exit into a traceback."""

        async def scenario():
            bridge = SerialBridge(iid=7002, port=1)
            await bridge.close()
            await bridge.close()
            assert bridge.closed is True

        run(scenario)