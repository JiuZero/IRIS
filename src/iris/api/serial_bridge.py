"""QEMU 的串口 chardev 与浏览器之间的桥。

QEMU 的 socket chardev 只接受**一个**客户端,而浏览器里想看同一个实例的标签页
可以有多个;桥存在的理由就是这个扇出。输出方向是一对多,输入方向必须仲裁:串口是
双向的,谁都能往里写就等于谁都能在 guest 里执行命令,所以同一时刻只允许一个订阅者
持有输入权,其余订阅者仍然能看。

桥不认识 WebSocket。发送走订阅者带进来的两个回调,于是整份仲裁与背压逻辑可以在
没有 ASGI 服务器的情况下测。

为什么不用 asyncio 的广播原语:它们要么要求每个消费者都有独立的写协程(即要求桥
认识 WebSocket),要么在慢消费者身上无限缓冲。终端输出在 boot 期间是突发的大流量,
一个连不上的浏览器标签页如果能无限缓冲,内存就会被它吃光——所以每个订阅者有上限,
超了丢最旧的字节**并且计数**,计数要能报到前端去:丢字节的终端如果不讲,看起来就
和一个完整的终端没有区别。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from iris.log import get_logger

logger = get_logger(__name__)

#: 单个订阅者的待发缓冲上限(字节)。超出后丢最旧的字节并计数。
#:
#: 宁可在报告里承认丢了字节,也不要让一个卡住的客户端把宿主的内存吃光。256 KiB
#: 相当于几分钟的高密度串口输出,足够吸收一次正常的网络抖动。
SUBSCRIBER_BUFFER_BYTES = 256 * 1024

#: 从上游一次读多少字节。够大以减少每字节的系统调用,又不至于让单个订阅者的
#: 发送协程 starve 在 await 上。
READ_CHUNK = 8192


class SerialUnavailable(RuntimeError):
    """上游串口连不上,或者已经断开。"""


@dataclass
class Subscription:
    """一个下游订阅者:一份待发缓冲,加一对发送回调。

    ``holds_input`` 由桥维护,不由订阅者自己置位——否则每个客户端都能自称持有,
    仲裁就没有意义了。
    """

    name: str
    send_bytes: Callable[[bytes], Awaitable[None]]
    send_text: Callable[[str], Awaitable[None]]
    buffer: bytearray = field(default_factory=bytearray)
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    dropped_bytes: int = 0
    holds_input: bool = False

    def offer(self, data: bytes) -> None:
        """把上游字节放进缓冲,满了就从最旧的开始丢。"""
        self.buffer += data
        overflow = len(self.buffer) - SUBSCRIBER_BUFFER_BYTES
        if overflow > 0:
            del self.buffer[:overflow]
            self.dropped_bytes += overflow
        self.wake.set()

    def take(self) -> bytes:
        """取走当前该发的字节并重置就绪信号。"""
        data = bytes(self.buffer)
        self.buffer.clear()
        self.wake.clear()
        return data


#: 打开一条上游连接的协程签名。默认是 ``asyncio.open_connection``,测试注入别的。
Connector = Callable[[], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]


class SerialBridge:
    """一条串口 chardev 的扇出与输入仲裁。

    生命周期跟着容器走:容器在,桥就有上游;容器没了,桥关闭并让所有订阅者看到
    ``closed``。桥不会自己重连 QEMU 那一侧——QEMU 退出即这个实例的仿真结束,
    重连只会连到一个没人监听的端口;需要重连的是浏览器到桥这一段,那是前端的
    退避重试。
    """

    def __init__(
        self,
        iid: int,
        port: int,
        *,
        host: str = "127.0.0.1",
        connector: Connector | None = None,
        connect_retries: int = 3,
        connect_delay: float = 0.5,
    ) -> None:
        self.iid = iid
        self.port = port
        self.host = host
        self._connector = connector or self._default_connector
        self._connect_retries = max(1, connect_retries)
        self._connect_delay = connect_delay

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._upstream: asyncio.Task[None] | None = None
        self._subs: dict[str, Subscription] = {}
        self._holder: str | None = None
        self._closed = False
        self._lock = asyncio.Lock()

    async def _default_connector(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        return await asyncio.open_connection(self.host, self.port)

    # ---------------------------------------------------------------- 上游

    @property
    def connected(self) -> bool:
        return self._reader is not None and self._upstream is not None and not self._upstream.done()

    @property
    def closed(self) -> bool:
        return self._closed

    async def start(self) -> None:
        """连上上游串口。失败时按 ``connect_retries`` 退避重试。

        重试是必要的:桥往往在 QEMU 真正起来之前几秒就被建立起来了(容器已经
        create,QEMU 还在 exec 里加载内核),一次失败不代表这个实例没有串口。
        """
        last: Exception | None = None
        for attempt in range(self._connect_retries):
            try:
                self._reader, self._writer = await self._connector()
            except OSError as exc:
                last = exc
                if attempt + 1 < self._connect_retries:
                    await asyncio.sleep(self._connect_delay)
                continue
            self._upstream = asyncio.create_task(self._pump_upstream(), name=f"iris-serial-{self.iid}")
            return
        raise SerialUnavailable(
            f"serial console for instance {self.iid} is not reachable on "
            f"{self.host}:{self.port}: {last}"
        )

    async def _pump_upstream(self) -> None:
        """上游 → 所有订阅者。上游断了就结束,并让订阅者知道自己看到的已定格。"""
        assert self._reader is not None
        try:
            while True:
                data = await self._reader.read(READ_CHUNK)
                if not data:
                    break
                for sub in list(self._subs.values()):
                    sub.offer(data)
        finally:
            await self._shutdown_from_upstream()

    async def _shutdown_from_upstream(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Unconditionally: whoever held the keyboard, the console is gone, so the
        # claim goes with it. Passing a sentinel instead would only ever match a
        # subscriber that happened to be named after it.
        self._release_holder(self._holder)
        await self._close_writer()
        for sub in list(self._subs.values()):
            await _safe(sub.send_text, '{"type":"error","code":"SERIAL_CLOSED",'
                                       '"message":"the guest serial console has closed"}')

    async def _close_writer(self) -> None:
        writer, self._writer = self._writer, None
        if writer is not None:
            with contextlib.suppress(Exception):
                writer.close()
                await writer.wait_closed()

    # ------------------------------------------------------------ 订阅者

    def subscribe(self, sub: Subscription) -> None:
        self._subs[sub.name] = sub
        # 新订阅者立刻知道输入权在谁手上,否则它会先按"我有输入权"渲染几秒。
        sub.holds_input = self._holder == sub.name

    async def unsubscribe(self, name: str) -> None:
        self._subs.pop(name, None)
        self._release_holder(name)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    def subscribers(self) -> tuple[Subscription, ...]:
        """Current subscribers, for a frame that goes to all of them at once."""
        return tuple(self._subs.values())

    @property
    def input_holder(self) -> str | None:
        return self._holder

    @property
    def dropped_bytes(self) -> int:
        return sum(sub.dropped_bytes for sub in self._subs.values())

    # ---------------------------------------------------------- 输入仲裁

    async def claim_input(self, name: str) -> bool:
        """把输入权给 ``name``。已经有人持有时返回 False,不抢占。

        不抢占是有意的:两个标签页都在打字,后到的那个把前一个正在敲的半行吃掉,
        比明确拒绝更糟。
        """
        async with self._lock:
            if self._holder is not None and self._holder != name:
                return False
            self._holder = name
            sub = self._subs.get(name)
            if sub is not None:
                sub.holds_input = True
            return True

    async def release_input(self, name: str) -> None:
        async with self._lock:
            self._release_holder(name)

    def _release_holder(self, name: str | None) -> None:
        """同步版本,供已经在持有锁的地方与上游断开路径调用。"""
        if self._holder != name:
            return
        self._holder = None
        for sub in self._subs.values():
            sub.holds_input = False

    async def write_input(self, name: str, data: bytes) -> bool:
        """把订阅者的输入转发给 guest。

        没有输入权就返回 False,调用方负责把 ``readonly`` 回给它——静默丢弃按键
        会让人以为键盘坏了。
        """
        if self._holder != name or self._writer is None:
            return False
        self._writer.write(data)
        with contextlib.suppress(ConnectionError):
            await self._writer.drain()
        return True

    # -------------------------------------------------------------- 关闭

    async def close(self) -> None:
        if self._closed and self._upstream is None:
            return
        self._closed = True
        self._release_holder(None)
        if self._upstream is not None:
            self._upstream.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._upstream
            self._upstream = None
        await self._close_writer()
        for sub in list(self._subs.values()):
            sub.wake.set()
        self._subs.clear()

    # -------------------------------------------------------- 订阅者泵

    async def pump_subscriber(self, sub: Subscription) -> None:
        """把某个订阅者的缓冲发出去,直到它被取消或发不出去。

        每个订阅者一个协程,而不是在一个协程里 await 全体:一个慢客户端只能拖慢
        自己那个循环。

        发送失败就结束这个协程,并把它记进日志:吞掉异常只会让一个已经掉线的订阅者
        在界面上继续显示"串口就绪",而它的字节从此无人送达——和真的链路故障看起来
        一模一样,却既查不出来也修不掉。
        """
        while True:
            await sub.wake.wait()
            data = sub.take()
            if not data:
                continue
            try:
                await sub.send_bytes(data)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a gone client is not a bridge fault
                logger.warning(f"serial subscriber {sub.name} stopped receiving: {exc}")
                return

    @staticmethod
    def subscriber_dropped(sub: Subscription) -> int:
        """这个订阅者丢了多少字节,供前端如实展示。"""
        return sub.dropped_bytes


async def _safe(fn: Callable[..., Awaitable[Any]], *args: Any) -> None:
    """调用一个可能因为客户端已经走了而失败的发送。"""
    with contextlib.suppress(Exception):
        await fn(*args)


