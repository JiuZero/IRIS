"""`docker stats` 的解析:单位不能猜,没测到不能写成 0。

这一层单独测,因为它错起来很安静:把 512MiB 当成 512MB 会让内存上限显示成 511.99,
把"还没采样"当成 0 会让一条已经消失的容器继续显示一条漂亮的零线。
"""

from __future__ import annotations

import subprocess

import pytest

from iris.emulate.container_stats import ContainerStats, container_stats, parse_cpu, parse_mem_mb


class TestCpu:
    @pytest.mark.parametrize("raw,expected", [
        ("0.15%", 0.15),
        ("100.00%", 100.0),
        ("  3.5 %  ", 3.5),
        ("0%", 0.0),
    ])
    def test_it_reads_the_percentage(self, raw, expected):
        assert parse_cpu(raw) == expected

    @pytest.mark.parametrize("raw", ["--", "", "N/A", "abc"])
    def test_no_sample_is_not_zero(self, raw):
        """`--` is what docker prints before its first sample. Reading it as 0
        would draw a flat healthy line for a container that was never measured."""
        assert parse_cpu(raw) is None


class TestMemory:
    @pytest.mark.parametrize("raw,expected", [
        ("12.3MiB / 512MiB", (12.3, 512.0)),
        ("0B / 0B", (0.0, 0.0)),
        ("1.5GiB / 2GiB", (1536.0, 2048.0)),
        ("1.2GB / 2GB", (1144.18, 1907.35)),   # decimal units, not binary ones
        ("512MiB / 1.5GiB", (512.0, 1536.0)),
    ])
    def test_both_sides_are_read_in_mb(self, raw, expected):
        mem, limit = parse_mem_mb(raw)
        assert mem == pytest.approx(expected[0], rel=1e-3)
        assert limit == pytest.approx(expected[1], rel=1e-3)

    def test_the_two_sides_may_use_different_units(self):
        """`512MiB / 1.5GiB` is what docker prints once the limit stops being a
        round number of MiB; assuming one unit for both reads the limit 1024x off."""
        _, limit = parse_mem_mb("512MiB / 1.5GiB")
        assert limit == pytest.approx(1536.0)

    @pytest.mark.parametrize("raw", ["-- / --", "", "N/A / N/A", "12MiB"])
    def test_unreadable_memory_is_not_zero(self, raw):
        assert parse_mem_mb(raw) == (None, None)


def _completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["docker"], returncode, stdout, stderr)


class TestSampling:
    def test_a_readable_sample_comes_back(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **k: _completed("3.25%|118.4MiB / 512MiB\n"))
        stats = container_stats("iris-qemu-7100")
        assert stats is not None
        assert stats.cpu_pct == 3.25
        assert stats.mem_mb == pytest.approx(118.4, rel=1e-3)
        assert stats.mem_limit_mb == pytest.approx(512.0)

    def test_a_container_docker_does_not_know_is_not_reported_as_idle(self, monkeypatch):
        """The container being gone is the single most likely reason here, and a
        zero sample would be the most expensive way to report it."""
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **k: _completed("", 1, "No such container"))
        assert container_stats("iris-qemu-9999") is None

    def test_docker_missing_is_not_reported_as_idle(self, monkeypatch):
        def boom(*args, **kwargs):
            raise FileNotFoundError("docker")

        monkeypatch.setattr(subprocess, "run", boom)
        assert container_stats("iris-qemu-7100") is None

    def test_an_empty_reading_is_not_reported_as_idle(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed("\n"))
        assert container_stats("iris-qemu-7100") is None

    def test_a_line_we_cannot_parse_is_not_reported_as_idle(self, monkeypatch):
        """docker's own '--' placeholder for "no sample yet"."""
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed("--|-- / --\n"))
        assert container_stats("iris-qemu-7100") is None

    def test_no_container_name_means_no_sample(self):
        """Not a subprocess call at all: an empty name is the caller's bug, and
        `docker stats ''` would go looking for something."""
        assert container_stats("") is None

    def test_cpu_without_memory_is_still_a_sample(self, monkeypatch):
        """A firmware that mounts no tmpfs can report memory the way this does; a
        partial reading is still a reading, and dropping it would blank the chart."""
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _completed("0.00%|-- / --\n"))
        stats = container_stats("iris-qemu-7100")
        assert stats == ContainerStats(cpu_pct=0.0, mem_mb=None, mem_limit_mb=None)

    def test_it_serialises_for_the_api(self):
        assert ContainerStats(cpu_pct=1.0, mem_mb=2.0, mem_limit_mb=3.0).to_dict() == {
            "cpu_pct": 1.0, "mem_mb": 2.0, "mem_limit_mb": 3.0}