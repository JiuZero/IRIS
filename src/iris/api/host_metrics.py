"""What the host this service runs on is actually doing right now.

The dashboard's other numbers come out of the metadata database, which is a
record of the past: how many runs there have been, how many of them reached the
web plane. Nothing in it says whether the machine is out of memory, whether QEMU
is eating a core, or whether the service has been up for four minutes or four
days -- and a rehosting run is exactly the kind of thing that finds out by
dying. So the workbench also needs a live reading of the host, and this is it.

Three decisions worth stating, because each of them has an obvious cheaper
alternative that is wrong:

- **A missing measurement is ``None``, never ``0``.** The first CPU sample has no
  interval to divide by, so there is genuinely nothing to report yet. Reporting
  ``0.0`` there would paint an idle CPU on the panel one second after start-up,
  which is the exact reading a user takes as "nothing is happening".
- **CPU is measured between two calls, not over a sleep.** ``psutil.cpu_percent``
  with an ``interval`` blocks for that long, and this is called from a request
  handler; without one it has to be primed by a previous call. So the sampler
  keeps the previous ``cpu_times`` and derives the share from the delta. The
  result is "the average load since the panel was last drawn", which is the only
  figure a dashboard can honestly claim about an instantaneous quantity.
- **A read that fails is reported as absent.** Every field is wrapped, because a
  workbench that 500s because ``/proc`` was unreadable cannot show the operator
  the thing that went wrong.

The numbers are a **live observation, not a record**: unlike the run table, this
sampler keeps nothing, so a later call cannot reproduce an earlier reading. The
page says so where it shows them.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from iris import __version__
from iris.log import get_logger

logger = get_logger(__name__)

# Long enough that the panel's poll interval (a few seconds) reads a fresh value,
# short enough that a manual refresh still means "now". Without a cache every
# panel redraw would spawn a filesystem stat for the scratch volume and re-read
# every mount point on the box.
DEFAULT_TTL_SEC = 2.0

_MB = 1024 ** 2
_GB = 1024 ** 3


@dataclass
class HostSampler:
    """Cache one host reading per :attr:`ttl_sec`.

    ``_previous_cpu`` is the whole trick: the CPU share is only computable once
    two readings exist, and it is kept per instance rather than in a module
    global so a test gets a sampler with no history instead of one that has
    already been primed by some other test.
    """

    ttl_sec: float = DEFAULT_TTL_SEC
    started_at: float = field(default_factory=time.monotonic)
    _cached: dict[str, Any] | None = None
    _cached_at: float = 0.0
    _previous_cpu: Any = None

    def read(self, *, psutil: Any) -> dict[str, Any]:
        """One host reading, or the last one if it is younger than ``ttl_sec``.

        ``psutil`` is a parameter rather than a module global so the tests can
        drive the field-absence and first-sample paths without waiting for real
        CPU time to pass.
        """
        now = time.monotonic()
        if self._cached is not None and now - self._cached_at < self.ttl_sec:
            return self._cached
        reading = {
            "host": self._host(now, psutil=psutil),
            "service": {
                "version": __version__,
                # From the sampler's own construction, not from a wall clock: a
                # service whose system clock was stepped would otherwise report
                # a negative or absurd uptime.
                "uptime_sec": round(now - self.started_at, 3),
                "sample_ttl_sec": self.ttl_sec,
            },
        }
        self._cached = reading
        self._cached_at = now
        return reading

    def _host(self, now: float, *, psutil: Any) -> dict[str, Any]:
        return {
            "cpu_pct": _cpu_since_last_read(self, psutil=psutil),
            "cpu_cores": _int_of(lambda: psutil.cpu_count(logical=True)),
            "mem_used_mb": _float_of(lambda: psutil.virtual_memory().used / _MB),
            "mem_total_mb": _float_of(lambda: psutil.virtual_memory().total / _MB),
            "mem_pct": _float_of(lambda: psutil.virtual_memory().percent),
            "uptime_sec": _float_of(lambda: time.time() - psutil.boot_time()),
            "scratch_free_gb": _float_of(lambda: self._scratch_free_gb(psutil)),
        }

    @staticmethod
    def _scratch_free_gb(psutil: Any) -> float:
        from iris.config import get_settings

        settings = get_settings()
        path = settings.scratch_dir
        # The volume holding the scratch directory is the one that fills up
        # during a rehosting run -- every container's rootfs tarball lands there
        # -- so it is the disk worth reporting. Home would answer a question
        # nobody asked.
        probe = path if path.exists() else path.parent
        return psutil.disk_usage(str(probe)).free / _GB


def _cpu_since_last_read(sampler: HostSampler, *, psutil: Any) -> float | None:
    """Host CPU share across the window between the two most recent calls.

    ``None`` on the first call of a sampler's life: there is no window yet. The
    alternative -- priming in the constructor -- would report a share of
    zero percent for whatever interval the constructor ran in, which on a fast
    page load is a couple of milliseconds and reliably reads as "idle".
    """
    try:
        current = psutil.cpu_times(percpu=False)
    except Exception as exc:  # noqa: BLE001 - one unreadable counter must not 500 the panel
        logger.debug(f"cpu share unavailable: {exc}")
        return None
    previous = sampler._previous_cpu
    sampler._previous_cpu = current
    if previous is None:
        return None
    busy = (current.user - previous.user) + (current.system - previous.system)
    idle = current.idle - previous.idle
    window = busy + idle
    if window <= 0:
        # A sub-tick window, or a host that reports counters which do not move.
        # Dividing here would be a divide-by-zero or a percentage of nothing.
        return None
    return round(100.0 * busy / window, 1)


def _float_of(read: Any) -> float | None:
    try:
        return round(float(read()), 2)
    except Exception as exc:  # noqa: BLE001 - an absent reading must not 500 the panel
        logger.debug(f"host metric unavailable: {exc}")
        return None


def _int_of(read: Any) -> int | None:
    value = _float_of(read)
    return None if value is None else int(value)


# One sampler for the process. Sampling is a property of the machine, not of a
# request, so two callers a second apart should see one reading rather than two.
_SAMPLER = HostSampler()


def system_reading() -> dict[str, Any]:
    """The live host reading served to the dashboard and the sidebar."""
    import psutil

    return _SAMPLER.read(psutil=psutil)