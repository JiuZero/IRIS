"""容器资源占用的一次性采样。

`docker inspect` 能回答"在不在跑、发布了哪些端口、挂了哪些网卡",但它不回答
"现在用了多少 CPU 和内存"——那组数字只有 `docker stats` 有,而全仓库此前没有任何
一处调用它,所以资源曲线在本文件之前无从谈起。

三条约定:

* **采样失败返回 None,不返回 0。** 容器刚起、`docker stats` 还在取第一个快照、
  或者容器已经没了,这三件事都表现为"没有数据"。0 是一个有意义的量(一个不用 CPU
  的容器就是 0),把它当成"没测到"会让曲线在容器消失之后仍然显示一条漂亮的零线,
  读起来像"仿真很省资源",而真相是它已经不在了。
* **不缓存。** 缓存是调用方的事,因为新鲜度取决于谁在问:Inspector 面板 2 秒一次
  足够,导出报表要的却是当下那一刻的值。把缓存埋在采样器里会让后者永远拿不到。
* **解析只接受 docker 自己的单位后缀。** 看到一个不带单位的数字就当 MB 用的写法,
  在 GiB 上会差三个数量级,而那正好是固件镜像这种体积下最常出现的单位。
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

from iris.log import get_logger

logger = get_logger(__name__)

#: `12.3MiB / 512MiB` —— 左边是用量,右边是上限。两边都带 `i?`:docker 一旦上限
#: 不再是整数 MiB(比如 1.5GiB)就会在右侧也换成二进制单位,而右侧若只认 `B` 结尾,
#: 这行会被整体判为不可读,于是「有上限」这件事恰好在上限变得有意思的时候消失。
_MEM = re.compile(r"^\s*([\d.]+)\s*([KMGTP]?i?B)\s*/\s*([\d.]+)\s*([KMGTP]?i?B)\s*$")
_CPU = re.compile(r"^\s*([\d.]+)\s*%\s*$")

_UNIT_TO_BYTES = {
    "B": 1,
    "KB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3, "TB": 1000 ** 4,
    "KIB": 1024, "MIB": 1024 ** 2, "GIB": 1024 ** 3, "TIB": 1024 ** 4,
}


@dataclass
class ContainerStats:
    """One sample. ``cpu_pct`` is docker's percentage of one host CPU."""

    cpu_pct: float | None = None
    mem_mb: float | None = None
    mem_limit_mb: float | None = None

    def to_dict(self) -> dict[str, float | None]:
        return {"cpu_pct": self.cpu_pct, "mem_mb": self.mem_mb,
                "mem_limit_mb": self.mem_limit_mb}


def parse_cpu(raw: str) -> float | None:
    """``'0.15%'`` → ``0.15``. ``'--'`` (no sample yet) → None."""
    match = _CPU.match(raw or "")
    return float(match.group(1)) if match else None


def parse_mem_mb(raw: str) -> tuple[float | None, float | None]:
    """``'12.3MiB / 512MiB'`` → ``(12.3, 512.0)``, both in MB."""
    match = _MEM.match(raw or "")
    if not match:
        return None, None
    value, unit, limit, limit_unit = match.groups()
    scale = _UNIT_TO_BYTES.get(unit.upper()) or _UNIT_TO_BYTES.get(unit.replace("i", ""))
    limit_scale = _UNIT_TO_BYTES.get(limit_unit.upper())
    if scale is None or limit_scale is None:
        return None, None
    return float(value) * scale / (1024 ** 2), float(limit) * limit_scale / (1024 ** 2)


def container_stats(name: str, *, timeout: int = 10) -> ContainerStats | None:
    """Sample one container, or None when there is nothing to sample.

    None covers every reason the answer is unavailable -- no docker, no such
    container, no sample yet -- because from the caller's side they are the same
    statement: "not measured right now". The reason is logged, not returned.
    """
    if not name:
        return None
    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}|{{.MemUsage}}", name],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug(f"docker stats failed for {name}: {exc}")
        return None
    if result.returncode != 0:
        logger.debug(f"docker stats reported nothing for {name}: {result.stderr.strip()}")
        return None
    line = result.stdout.strip().splitlines()
    if not line:
        return None
    cpu_raw, _, mem_raw = line[0].partition("|")
    cpu = parse_cpu(cpu_raw)
    mem, limit = parse_mem_mb(mem_raw)
    if cpu is None and mem is None:
        # A line we cannot read is not a sample of zero; docker prints '--' while
        # the first sample is still being taken, which is the common case here.
        return None
    return ContainerStats(cpu_pct=cpu, mem_mb=mem, mem_limit_mb=limit)