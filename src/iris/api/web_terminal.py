"""WebSocket 端点:把浏览器的终端接到 QEMU 的串口上。

这一层只做三件事,其余全在 :mod:`iris.api.serial_bridge`:鉴权、把 WebSocket 的收发
适配成桥的回调、在连接结束时把订阅者摘掉。

**为什么鉴权放在这里手工做**:浏览器发起的 WebSocket 不能自定义请求头,所以 token
只能走查询参数。依赖注入那套(header 比较 + 常量时间 + 404 语义)是给 HTTP 路由写的,
WebSocket 走不了 ``Depends``,所以这里复用了同一批底层函数,而不是放宽校验。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from iris.api.auth import LOCAL_CLIENT, client_id_of, configured_token
from iris.api.serial_bridge import SerialBridge, SerialUnavailable, Subscription
from iris.emulate.orchestrator import serial_port_of
from iris.log import get_logger

logger = get_logger(__name__)

#: One bridge per instance, for the same reason QEMU's chardev accepts one client:
#: the upstream connection is the scarce resource, and the bridge exists to share it.
_bridges: dict[int, SerialBridge] = {}

#: Close codes. 4404 rather than a bare 1008-with-a-message so a client can tell
#: "not yours / not there" from "the console is gone" without parsing text -- the
#: first is a permission answer, the second is a lifecycle event.
CLOSE_NOT_FOUND = 4404
CLOSE_NO_CONSOLE = 4403
CLOSE_UNAVAILABLE = 1011


def _authorise(websocket: WebSocket, token: str) -> str | None:
    """Return the caller when the token matches, else None.

    Same constant-time comparison as :func:`iris.api.auth.require_client`, reached
    through the same primitives so a websocket cannot end up with weaker rules than
    the REST routes beside it.
    """
    import hmac

    expected = configured_token()
    if not expected:
        return LOCAL_CLIENT
    if not token or not hmac.compare_digest(token, expected):
        return None
    return client_id_of(token)


def _own_console(iid: int, caller: str) -> int | None:
    """The published console port for ``iid``, if this caller may use it.

    Ownership is checked against ``active_emulation`` rather than assumed: the port
    registry is process-wide, so without this a caller who knows an iid could open a
    console for somebody else's emulation -- and a console accepts keystrokes.
    """
    from iris.api.server import _session
    from iris.db.active import list_owned

    try:
        with _session() as session:
            mine = [record for record in list_owned(session, caller) if record.iid == iid]
    except Exception as exc:  # noqa: BLE001 - an unreadable table is not "yours"
        logger.warning(f"could not verify ownership of instance {iid}: {exc}")
        return None
    if not mine:
        return None
    return serial_port_of(iid)


async def bridge_for(iid: int, port: int) -> SerialBridge:
    """The bridge for this instance, started on first use.

    Kept after the console is gone rather than closed eagerly, because closing it
    would have to be coordinated with every attached websocket, and a second
    connection attempt inside the retry window should find a console that is
    starting rather than one that has already failed once.
    """
    bridge = _bridges.get(iid)
    if bridge is not None and bridge.connected:
        return bridge
    if bridge is not None:
        await bridge.close()
    bridge = SerialBridge(iid=iid, port=port)
    await bridge.start()
    _bridges[iid] = bridge
    return bridge


async def close_all_bridges() -> int:
    """Close every bridge, for shutdown. Returns how many were open."""
    open_now = list(_bridges.values())
    for bridge in open_now:
        with contextlib.suppress(Exception):
            await bridge.close()
    _bridges.clear()
    return len(open_now)


async def notify_shutdown() -> int:
    """Tell every attached terminal the server is going away.

    Sent before the bridges close and before the containers go, so a browser stops
    reading a console instead of sitting on a socket that will never produce another
    byte -- and never shows a container disappearing for no visible reason.
    """
    frame = json.dumps({"type": "server.shutdown",
                        "message": "iris web is stopping; this console is closing"})
    return await broadcast(frame)


async def broadcast(frame: str) -> int:
    """Send one text frame to every subscriber of every bridge. Returns the count."""
    sent = 0
    for bridge in list(_bridges.values()):
        for sub in bridge.subscribers():
            try:
                await sub.send_text(frame)
                sent += 1
            except Exception as exc:  # noqa: BLE001 - a gone client is not a failure
                logger.debug(f"shutdown notice did not reach {sub.name}: {exc}")
    return sent


async def terminal_endpoint(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token", "")
    caller = _authorise(websocket, token)
    if caller is None:
        # Refused before the accept: a 4404 on an unaccepted socket is the only
        # thing a browser can actually observe, and accepting first would hand the
        # page a connection it then has to be told is unauthorised.
        await websocket.close(code=CLOSE_NOT_FOUND)
        return

    raw_iid = websocket.query_params.get("iid", "")
    try:
        iid = int(raw_iid)
    except ValueError:
        await websocket.close(code=CLOSE_NOT_FOUND)
        return

    port = _own_console(iid, caller)
    if port is None:
        await websocket.close(code=CLOSE_NO_CONSOLE)
        return

    await websocket.accept()
    name = websocket.client.host if websocket.client else "anonymous"

    try:
        bridge = await bridge_for(iid, port)
    except SerialUnavailable as exc:
        await websocket.send_text(json.dumps(
            {"type": "error", "code": "SERIAL_UNAVAILABLE", "message": str(exc)}))
        await websocket.close(code=CLOSE_UNAVAILABLE)
        return

    async def send_bytes(data: bytes) -> None:
        await websocket.send_bytes(data)

    async def send_text(data: str) -> None:
        await websocket.send_text(data)

    sub = Subscription(name=name, send_bytes=send_bytes, send_text=send_text)
    bridge.subscribe(sub)
    pump = asyncio.create_task(bridge.pump_subscriber(sub))

    # Honest about what the channel cannot do: QEMU's serial has no window-size
    # channel, so a resize frame is recorded here and never forwarded. Saying so up
    # front is why the terminal page can show a fixed 80x24 instead of pretending.
    await send_text(json.dumps({
        "type": "hello",
        "iid": iid,
        "serial_port": port,
        "size": {"cols": 80, "rows": 24},
        "resize_supported": False,
        "input_holder": bridge.input_holder,
        "subscriber_count": bridge.subscriber_count,
    }))

    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if (raw_bytes := message.get("bytes")) is not None:
                # Keystrokes. Refused rather than dropped when another tab holds the
                # console, because a terminal that silently swallows typing looks
                # broken.
                if not await bridge.write_input(name, raw_bytes):
                    await send_text(json.dumps({
                        "type": "readonly",
                        "reason": "another client holds the console input",
                    }))
                continue
            if (raw_text := message.get("text")) is not None:
                await _handle_control(bridge, sub, raw_text, send_text)
    except WebSocketDisconnect:
        pass
    except RuntimeError as exc:
        # Starlette raises this when the socket goes away underneath a send; the
        # disconnect handler above cannot see it because it happens during the send.
        logger.debug(f"terminal socket for instance {iid} ended: {exc}")
    finally:
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump
        await bridge.unsubscribe(name)
        with contextlib.suppress(RuntimeError):
            await websocket.close()


async def _handle_control(
    bridge: SerialBridge,
    sub: Subscription,
    raw: str,
    send_text: Any,
) -> None:
    """Client control frames: claim/release the keyboard, run a command, report."""
    try:
        message = json.loads(raw)
    except json.JSONDecodeError:
        await send_text(json.dumps({"type": "error", "code": "BAD_FRAME",
                                    "message": "control frames must be JSON"}))
        return
    kind = message.get("type")
    if kind == "claim":
        got = await bridge.claim_input(sub.name)
        await send_text(json.dumps(
            {"type": "claim", "granted": got, "input_holder": bridge.input_holder}))
    elif kind == "release":
        await bridge.release_input(sub.name)
        await send_text(json.dumps({"type": "release", "input_holder": bridge.input_holder}))
    elif kind == "input":
        # The shortcut-command panel's channel: base64 so a command containing a
        # newline or a control character survives the JSON round trip intact.
        import base64
        try:
            payload = base64.b64decode(message.get("data", ""), validate=True)
        except (ValueError, TypeError):
            await send_text(json.dumps({"type": "error", "code": "BAD_COMMAND",
                                        "message": "data must be base64"}))
            return
        if not await bridge.write_input(sub.name, payload):
            await send_text(json.dumps({
                "type": "readonly",
                "reason": "another client holds the console input",
            }))
    elif kind == "ping":
        await send_text(json.dumps({"type": "pong",
                                    "dropped_bytes": bridge.subscriber_dropped(sub)}))
    elif kind == "resize":
        # Recorded and ignored on purpose. See the hello frame.
        await send_text(json.dumps({"type": "resize", "applied": False,
                                    "reason": "QEMU's serial has no window-size channel"}))
    else:
        await send_text(json.dumps({"type": "error", "code": "UNKNOWN_FRAME",
                                    "message": f"no handler for {kind!r}"}))