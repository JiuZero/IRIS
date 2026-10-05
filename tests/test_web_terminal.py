"""The terminal websocket: two tabs on one machine, and the one that gets evicted.

Every browser tab on one machine shares a peer address. Naming subscribers by that
address alone let a reconnect evict the connection that had replaced it: the
surviving tab kept reporting a ready console and then never received another byte,
which reads as an unstable link rather than as a bug.

The eviction itself is tested at the bridge, where the subscriber registry lives.
It cannot be tested through two ``TestClient`` websockets: Starlette gives each
``WebSocketTestSession`` its own portal, so closing one tears down the event loop the
bridge is running in and fails every subscriber for reasons that have nothing to do
with naming. The naming that makes the registry safe is therefore pinned directly,
against the function the endpoint calls.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from iris.api import web_terminal
from iris.api.serial_bridge import SerialBridge, Subscription

IID = 7002


class Upstream:
    """A TCP endpoint standing in for QEMU's serial chardev, on its own thread.

    A thread rather than an event-loop server because the bridge runs inside the
    TestClient's portal loop, and a listener owned by another loop could not be
    reached from it.
    """

    def __init__(self) -> None:
        self._listener = socket.socket()
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        self.port: int = self._listener.getsockname()[1]
        self.received = bytearray()
        self._peer: socket.socket | None = None
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            peer, _ = self._listener.accept()
        except OSError:
            return
        with self._lock:
            self._peer = peer
        try:
            while True:
                try:
                    data = peer.recv(4096)
                except OSError:
                    return
                if not data:
                    return
                self.received += data
        finally:
            peer.close()

    def wait_for_peer(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._peer is not None:
                    return
            time.sleep(0.01)
        raise AssertionError("no bridge ever connected to the upstream")

    def emit(self, data: bytes) -> None:
        self.wait_for_peer()
        with self._lock:
            assert self._peer is not None
            self._peer.sendall(data)

    def close(self) -> None:
        with self._lock:
            if self._peer is not None:
                self._peer.close()
                self._peer = None
        self._listener.close()
        self._thread.join(timeout=2)


class AsyncGuest:
    """QEMU's serial chardev as an in-loop asyncio server.

    Separate from :class:`Upstream` because the two live on different sides of the
    loop boundary: this one runs inside the test's own event loop, which is the only
    kind the bridge can be driven from without a portal in the way.
    """

    def __init__(self) -> None:
        self.received = bytearray()
        self.port = 0
        self.connected = asyncio.Event()
        self._server: asyncio.AbstractServer | None = None
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
                    return
                self.received += data
        except (ConnectionError, OSError):
            return

    def emit(self, data: bytes) -> None:
        assert self._writer is not None, "nothing has connected to the guest yet"
        self._writer.write(data)

    async def stop(self) -> None:
        if self._writer is not None:
            self._writer.close()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()


class Collector:
    """A subscriber that keeps whatever it was handed, for assertions."""

    def __init__(self, name: str) -> None:
        self.bytes = bytearray()
        self.sub = Subscription(name=name, send_bytes=self._send, send_text=self._text)

    async def _send(self, data: bytes) -> None:
        self.bytes += data

    async def _text(self, data: str) -> None:
        return None

    async def wait_for_bytes(self, count: int, timeout: float = 5.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while len(self.bytes) < count:
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(f"expected {count} bytes, saw {len(self.bytes)}")
            await asyncio.sleep(0.01)


@pytest.fixture
def upstream():
    server = Upstream()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def client(upstream, monkeypatch):
    """A console whose port resolves to ``upstream``, with no token configured.

    Ownership is stubbed rather than seeded into a database: what matters here is
    what happens *after* the console has been authorised, and a stub that always
    agrees keeps that visible instead of burying it under table fixtures.
    """
    monkeypatch.setattr(web_terminal, "_own_console",
                        lambda iid, caller: upstream.port if iid == IID else None)
    monkeypatch.setattr(web_terminal, "configured_token", lambda: "")
    web_terminal._bridges.clear()
    app = FastAPI()
    app.add_api_websocket_route("/ws/terminal", web_terminal.terminal_endpoint)
    try:
        yield TestClient(app)
    finally:
        # The bridges run in the portal loop TestClient is about to tear down, so
        # they cannot be awaited from here; dropping the registry is what keeps one
        # test's console from being handed to the next.
        web_terminal._bridges.clear()


class TestSubscriberNaming:
    """The registry is keyed on this, so it has to differ between two tabs on one
    machine -- which is the only multi-tab arrangement a browser ever produces."""

    @staticmethod
    def _socket(host: str = "127.0.0.1"):
        return SimpleNamespace(client=SimpleNamespace(host=host))

    def test_two_tabs_from_one_machine_get_different_names(self):
        first = web_terminal.subscriber_name(self._socket())
        second = web_terminal.subscriber_name(self._socket())
        assert first != second

    def test_the_peer_address_is_kept_in_the_name(self):
        """The input-holder readout is read by a person: "this machine" says more
        than "#7", and the counter alone would say nothing about who."""

        assert web_terminal.subscriber_name(self._socket("10.0.0.9")).startswith("10.0.0.9#")

    def test_a_connection_without_a_peer_is_still_named_uniquely(self):
        """Guarded because ``websocket.client`` is optional, and an anonymous
        subscriber must not collapse into a shared key either."""

        anonymous = SimpleNamespace(client=None)
        assert web_terminal.subscriber_name(anonymous) != web_terminal.subscriber_name(anonymous)


class TestTheRegistryKeepsBothTabs:
    def test_one_tab_going_away_leaves_the_other_subscribed(self):
        """The regression. Keyed on the peer address alone, the second tab replaced
        the first in the registry and the first tab's teardown then removed the
        second -- leaving a live terminal that never received another byte."""

        async def scenario():
            guest = AsyncGuest()
            await guest.start()
            try:
                bridge = SerialBridge(iid=IID, port=guest.port)
                await bridge.start()
                # start() returns once the client socket is up; the guest end is
                # accepted a beat later, and emit() before that has no writer.
                await asyncio.wait_for(guest.connected.wait(), timeout=5)
                first, second = Collector("127.0.0.1#1"), Collector("127.0.0.1#2")
                pumps = []
                for c in (first, second):
                    bridge.subscribe(c.sub)
                    pumps.append(asyncio.create_task(bridge.pump_subscriber(c.sub)))
                assert bridge.subscriber_count == 2

                await bridge.unsubscribe("127.0.0.1#1")
                assert bridge.subscriber_count == 1

                guest.emit(b"still here\r\n")
                await second.wait_for_bytes(len(b"still here\r\n"))
                assert bytes(first.bytes) == b""
                for pump in pumps:
                    pump.cancel()
                await bridge.close()
            finally:
                await guest.stop()

        asyncio.run(asyncio.wait_for(scenario(), timeout=20))

    def test_a_subscriber_whose_send_fails_stops_instead_of_spinning(self):
        """Silently dying looks identical to a healthy console that has gone quiet,
        and the endpoint awaits this task during teardown -- an exception escaping
        here would resurface there as an unrelated failure."""

        async def scenario():
            bridge = SerialBridge(iid=IID, port=1)

            async def refuse(data: bytes) -> None:
                raise ConnectionResetError("the tab is gone")

            async def text(data: str) -> None:
                return None

            sub = Subscription(name="127.0.0.1#9", send_bytes=refuse, send_text=text)
            bridge.subscribe(sub)
            pump = asyncio.create_task(bridge.pump_subscriber(sub))
            sub.offer(b"anything\r\n")
            await asyncio.wait_for(pump, timeout=5)
            assert pump.exception() is None

        asyncio.run(scenario())


class TestTwoLiveTabs:
    def test_both_are_registered_and_both_keep_receiving(self, client, upstream):
        """QEMU's chardev would hand the stream to whichever tab connected second;
        the bridge exists so that neither of them loses it."""

        first = client.websocket_connect(f"/ws/terminal?iid={IID}")
        first.__enter__()
        second = client.websocket_connect(f"/ws/terminal?iid={IID}")
        second.__enter__()
        try:
            assert first.receive_json()["type"] == "hello"
            hello = second.receive_json()
            assert hello["type"] == "hello"
            assert hello["subscriber_count"] == 2
            upstream.emit(b"kernel boot\r\n")
            assert b"kernel boot" in first.receive_bytes()
            assert b"kernel boot" in second.receive_bytes()
        finally:
            second.__exit__(None, None, None)
            first.__exit__(None, None, None)

    def test_the_second_tab_is_told_who_holds_the_keyboard(self, client):
        """Otherwise it renders an editable terminal for a few seconds before its
        first keystroke comes back refused."""

        first = client.websocket_connect(f"/ws/terminal?iid={IID}")
        first.__enter__()
        second = client.websocket_connect(f"/ws/terminal?iid={IID}")
        second.__enter__()
        try:
            assert first.receive_json()["type"] == "hello"
            assert second.receive_json()["type"] == "hello"
            first.send_json({"type": "claim"})
            granted = first.receive_json()
            assert granted["granted"] is True
            second.send_json({"type": "claim"})
            refused = second.receive_json()
            assert refused["granted"] is False
            assert refused["input_holder"] == granted["input_holder"]
        finally:
            second.__exit__(None, None, None)
            first.__exit__(None, None, None)


class TestAnInstanceWithNoConsole:
    def test_it_is_refused_before_the_socket_is_accepted(self, client):
        """A close on an unaccepted socket is the only thing a browser can observe;
        accepting first would hand the page a connection it must then be told is
        unauthorised. Starlette raises out of the connect itself, which is the same
        refusal seen from the other side."""

        with pytest.raises(WebSocketDisconnect) as excinfo, \
                client.websocket_connect(f"/ws/terminal?iid={IID + 1}"):
            pass
        assert excinfo.value.code == web_terminal.CLOSE_NO_CONSOLE