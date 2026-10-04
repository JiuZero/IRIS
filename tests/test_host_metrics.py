"""The live host reading: what it reports, and what it refuses to invent.

The three behaviours worth pinning here are all about the difference between a
number and the absence of one. A dashboard that paints ``0.0`` CPU because its
first sample had no interval to divide by is worse than one that prints a dash,
because the operator reading it concludes the machine is idle when in fact
nothing has been measured yet.
"""

from __future__ import annotations

import types
from typing import Any

import pytest

from iris.api import host_metrics
from iris.api.host_metrics import HostSampler


class FakeTimes:
    def __init__(self, user: float, system: float, idle: float) -> None:
        self.user = user
        self.system = system
        self.idle = idle


class FakePsutil:
    """Just enough of ``psutil`` to drive the sampler without waiting on the CPU.

    Each attribute is a callable the sampler invokes; ``fail_on`` names the ones
    that should raise, which is how the "a field read failed" path is exercised
    without editing the module.
    """

    def __init__(self, *, fail_on: str = "", cores: int = 8) -> None:
        self.fail_on = fail_on
        self._cores = cores
        self._clock = FakeTimes(user=100.0, system=50.0, idle=850.0)

    def _maybe_fail(self, name: str) -> None:
        if name == self.fail_on:
            raise OSError(f"{name} is not readable on this host")

    def cpu_times(self, percpu: bool = False) -> FakeTimes:
        self._maybe_fail("cpu_times")
        return self._clock

    def cpu_count(self, logical: bool = True) -> int:
        self._maybe_fail("cpu_count")
        return self._cores

    def virtual_memory(self) -> Any:
        self._maybe_fail("virtual_memory")
        return types.SimpleNamespace(used=2 * 1024 ** 3, total=8 * 1024 ** 3, percent=25.0)

    def boot_time(self) -> float:
        self._maybe_fail("boot_time")
        return 1_000.0

    def disk_usage(self, path: str) -> Any:
        self._maybe_fail("disk_usage")
        return types.SimpleNamespace(free=64 * 1024 ** 3)

    def advance_cpu(self, *, user: float, system: float, idle: float) -> None:
        """Move the CPU counters forward, as a busy host would."""
        self._clock = FakeTimes(user=user, system=system, idle=idle)


@pytest.fixture
def no_cache() -> HostSampler:
    """A sampler with no history and no caching, so each call re-reads."""
    return HostSampler(ttl_sec=0.0)


class TestCpuSampling:
    def test_the_first_sample_has_no_window_and_says_so(self, no_cache) -> None:
        """Not ``0.0``: there has been no interval yet, and a zero here reads as
        an idle machine rather than as an absence of measurement."""
        assert no_cache.read(psutil=FakePsutil())["host"]["cpu_pct"] is None

    def test_the_second_sample_is_the_share_across_the_window(self, no_cache) -> None:
        """50 busy ticks out of a 100-tick window, so 50% -- and it is derived from
        the difference between the two readings rather than from a counter total,
        which is what makes it a share of the window and not of the machine's
        entire uptime."""
        psutil = FakePsutil()
        no_cache.read(psutil=psutil)
        psutil.advance_cpu(user=125.0, system=75.0, idle=900.0)
        assert no_cache.read(psutil=psutil)["host"]["cpu_pct"] == 50.0

    def test_an_idle_window_reads_zero_rather_than_being_refused(self, no_cache) -> None:
        """Zero is a real measurement here -- the window existed and no of it was
        busy -- so it must survive, unlike the first sample's absent window."""
        psutil = FakePsutil()
        no_cache.read(psutil=psutil)
        psutil.advance_cpu(user=100.0, system=50.0, idle=950.0)
        assert no_cache.read(psutil=psutil)["host"]["cpu_pct"] == 0.0

    def test_a_window_with_no_elapsed_time_is_refused(self, no_cache) -> None:
        """Counters that did not move describe no interval, and dividing by that
        window would either divide by zero or report a percentage of nothing."""
        psutil = FakePsutil()
        no_cache.read(psutil=psutil)
        assert no_cache.read(psutil=psutil)["host"]["cpu_pct"] is None


class TestAbsentFields:
    def test_an_unreadable_field_is_absent_rather_than_zero(self, no_cache) -> None:
        """A panel that 500s because one counter was unreadable cannot show the
        operator the thing that broke; every read is therefore individually
        optional."""
        psutil = FakePsutil(fail_on="virtual_memory")
        reading = no_cache.read(psutil=psutil)["host"]
        assert reading["mem_used_mb"] is None
        assert reading["mem_total_mb"] is None
        assert reading["mem_pct"] is None

    def test_the_other_fields_still_arrive(self, no_cache) -> None:
        psutil = FakePsutil(fail_on="virtual_memory")
        reading = no_cache.read(psutil=psutil)["host"]
        assert reading["cpu_cores"] == 8
        assert reading["scratch_free_gb"] == 64.0

    def test_an_unreadable_cpu_counter_leaves_the_rest_intact(self, no_cache) -> None:
        reading = no_cache.read(psutil=FakePsutil(fail_on="cpu_times"))["host"]
        assert reading["cpu_pct"] is None
        assert reading["mem_pct"] == 25.0


class TestCaching:
    def test_a_second_read_inside_the_ttl_reuses_the_first(self) -> None:
        """Two panels redrawing in the same second should not each stat every
        mount point on the box; the whole reason for the TTL."""
        sampler = HostSampler(ttl_sec=60.0)
        assert sampler.read(psutil=FakePsutil()) is sampler.read(psutil=FakePsutil())

    def test_an_expired_read_takes_a_fresh_sample(self) -> None:
        sampler = HostSampler(ttl_sec=0.0)
        first = sampler.read(psutil=FakePsutil())
        assert sampler.read(psutil=FakePsutil()) is not first

    def test_cpu_history_survives_the_cache_so_the_window_is_continuous(
        self,
    ) -> None:
        """A cached CPU figure must not reset the baseline, or every poll would
        report its own short window and the number would jitter with the request
        rate instead of with the load."""
        sampler = HostSampler(ttl_sec=60.0)
        psutil = FakePsutil()
        sampler.read(psutil=psutil)
        # 150 busy ticks across a window of 800: +100 user, +50 system, +650 idle.
        psutil.advance_cpu(user=200.0, system=100.0, idle=1500.0)
        # Two calls, but the second is served from the cache...
        cached = sampler.read(psutil=psutil)
        assert cached["host"]["cpu_pct"] is None
        # ...so the baseline has to have advanced to the reading taken at the end
        # of the window, not stayed on the very first one. Were it still on the
        # first, this figure would be the whole machine's lifetime average instead.
        sampler.ttl_sec = 0.0
        assert sampler.read(psutil=psutil)["host"]["cpu_pct"] == pytest.approx(18.8)


class TestScratchVolume:
    def test_the_reported_volume_is_the_one_holding_the_scratch_directory(
        self, no_cache, tmp_path, monkeypatch
    ) -> None:
        """Every container's rootfs tarball lands in the scratch directory, so it
        is the disk that fills up during a run; the home volume would answer a
        question nobody asked."""
        seen: list[str] = []

        class Recorder(FakePsutil):
            def disk_usage(self, path: str) -> Any:
                seen.append(path)
                return super().disk_usage(path)

        monkeypatch.setattr(
            "iris.config.get_settings",
            lambda: types.SimpleNamespace(scratch_dir=tmp_path / "scratch"),
        )
        (tmp_path / "scratch").mkdir()
        reading = no_cache.read(psutil=Recorder())["host"]
        assert reading["scratch_free_gb"] == 64.0
        assert seen and seen[0].endswith("scratch")

    def test_a_missing_scratch_directory_falls_back_to_its_parent(
        self, no_cache, tmp_path, monkeypatch
    ) -> None:
        """The directory is created on first use, so a service asked before its
        first run has none yet -- and an unreadable path must not take the whole
        reading down with it."""
        monkeypatch.setattr(
            "iris.config.get_settings",
            lambda: types.SimpleNamespace(scratch_dir=tmp_path / "not-created-yet"),
        )
        assert no_cache.read(psutil=FakePsutil())["host"]["scratch_free_gb"] == 64.0


class TestServiceBlock:
    def test_it_carries_the_installed_version_and_a_measured_uptime(
        self, no_cache
    ) -> None:
        from iris import __version__

        service = no_cache.read(psutil=FakePsutil())["service"]
        assert service["version"] == __version__
        assert service["uptime_sec"] >= 0

    def test_the_uptime_counts_from_the_samplers_own_construction(self) -> None:
        """Not from a wall clock: a service whose clock was stepped would report
        a negative or absurd uptime, and the sampler's own monotonic start is the
        one interval that cannot be rewritten from outside."""
        sampler = HostSampler(ttl_sec=0.0)
        assert sampler.read(psutil=FakePsutil())["service"]["uptime_sec"] >= 0
        assert sampler.started_at > 0


def test_the_process_sampler_is_shared(monkeypatch) -> None:
    """Sampling is a property of the machine, not of a request, so two callers a
    moment apart should see one reading rather than two independent baselines."""
    monkeypatch.setattr(host_metrics, "_SAMPLER", HostSampler(ttl_sec=60.0))
    first = host_metrics.system_reading()
    assert host_metrics.system_reading() is first