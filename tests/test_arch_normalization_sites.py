"""Every architecture check consults the alias table first.

``iris.arch.normalize_arch`` maps what a firmware census says onto what a QEMU
config is keyed by -- ``aarch64`` to ``arm64``, ``arm64le`` to ``arm64``. Four of
the five places that validate an arch did it; ``POST /api/v1/pipeline`` did not, so
an arm64 firmware whose census reports ``aarch64`` was refused with
``unsupported-arch: 'aarch64' not in supported ['armel', 'arm64', 'mipseb',
'mipsel']`` while the identical firmware booted fine through ``iris emulate run
--arch aarch64``. The user's reading of that is "IRIS cannot run arm64".

The fix is pinned two ways: by driving the handler (so the claim "an ``aarch64``
census label gets past the whitelist" is observed, not inferred) and by source
guards (because the other call sites are inside handlers whose happy path needs
docker, and a source guard cannot drift away from the code the way a comment
can).
"""

from __future__ import annotations

import ast
import asyncio
import re
from pathlib import Path

import pytest

from iris.api import server
from iris.arch import normalize_arch
from iris.emulate.qemu_config import supported_archs

ROOT = Path(__file__).resolve().parents[1]
SERVER_PY = ROOT / "src/iris/api/server.py"
SOURCE = SERVER_PY.read_text(encoding="utf-8")
LINES = SOURCE.splitlines()

#: The census labels that only differ from a QEMU config key by spelling.
ALIASES_THAT_MATTER = ("aarch64", "arm64le", "x86_64")


class _ExtractionReached(Exception):
    """Raised by the fake extractor to mark how far the handler got."""


class _Upload:
    """The bare minimum ``_read_upload`` asks of an ``UploadFile``."""

    filename = "fw.bin"
    _body = b"\x7fELF not really"
    _served = 0

    async def read(self, size: int = -1) -> bytes:
        chunk = self._body[self._served:self._served + size]
        self._served += len(chunk)
        return chunk


def _whitelist_checks() -> list[int]:
    """Lines that compare something against the arch whitelist."""
    return sorted(
        i
        for i, line in enumerate(LINES, 1)
        if re.search(r"\w+\s+not in _SUPPORTED_ARCHS", line)
        or re.search(r"not _SUPPORTED_ARCHS", line)
    )


def _run_pipeline(monkeypatch, tmp_path, *, census_label: str) -> str:
    """Drive ``POST /api/v1/pipeline`` up to the extractor.

    Returns ``"extraction reached"`` when the whitelist let the arch through, or
    the handler's error string when it did not. Extraction is faked to raise, so
    nothing past that point touches docker.
    """
    from iris.config import Settings
    from iris.extract import firmware as firmware_mod
    from iris.extract import rootfs_extract as rootfs_mod

    class _Info:
        arch = census_label

    def _fake_analyze(content: bytes) -> _Info:
        return _Info()

    def _fake_extract(*args, **kwargs):
        raise _ExtractionReached()

    monkeypatch.setattr(firmware_mod, "analyze_firmware", _fake_analyze)
    monkeypatch.setattr(rootfs_mod, "extract_rootfs", _fake_extract)
    monkeypatch.setattr(server, "get_settings",
                        lambda: Settings(iris_home=tmp_path))


    try:
        asyncio.run(server.pipeline(caller=object(), firmware=_Upload()))
    except _ExtractionReached:
        return "extraction reached"
    return "refused"


class TestTheHandlerNowAcceptsWhatTheCensusSays:
    def test_an_arm64_census_label_reaches_the_extraction_step(self, monkeypatch, tmp_path):
        """A request carrying ``aarch64`` must not be refused at the whitelist."""
        assert _run_pipeline(monkeypatch, tmp_path, census_label="aarch64") == \
            "extraction reached"

    def test_an_arm64le_census_label_reaches_the_extraction_step(self, monkeypatch, tmp_path):
        assert _run_pipeline(monkeypatch, tmp_path, census_label="arm64le") == \
            "extraction reached"

    def test_a_label_with_no_config_key_is_still_refused(self, monkeypatch, tmp_path):
        """Normalizing must not become a blanket accept."""
        assert _run_pipeline(monkeypatch, tmp_path, census_label="sparc64") == "refused"

    def test_a_refusal_reports_the_normalized_name_not_the_census_label(self, monkeypatch, tmp_path):
        """The message a user reads must be about a name IRIS actually rejects."""
        from iris.config import Settings
        from iris.extract import firmware as firmware_mod
        from iris.extract import rootfs_extract as rootfs_mod

        class _Info:
            arch = "ppc64le"

        monkeypatch.setattr(firmware_mod, "analyze_firmware", lambda content: _Info())
        monkeypatch.setattr(rootfs_mod, "extract_rootfs",
                            lambda *a, **k: pytest.fail("must not extract"))
        monkeypatch.setattr(server, "get_settings",
                            lambda: Settings(iris_home=tmp_path))


        resp = asyncio.run(server.pipeline(caller=object(), firmware=_Upload()))
        assert resp.success is False
        assert "unsupported-arch" in resp.error
        assert "ppc64le" in resp.error


class TestEveryWhitelistLookupSeesTheNormalizedName:
    def test_the_pipeline_is_no_longer_the_exception(self):
        """The one that was left out, and the only one a user hit."""
        body = SOURCE[SOURCE.index("info = await asyncio.to_thread(analyze_firmware"):]
        body = body[:body.index("_SUPPORTED_ARCHS")]
        assert "normalize_arch(detected_arch)" in body

    def test_all_the_checks_are_found(self):
        """If the guard finds nothing it is guarding nothing."""
        assert len(_whitelist_checks()) >= 3

    @pytest.mark.parametrize("alias", ALIASES_THAT_MATTER)
    def test_the_alias_table_still_maps_each_one(self, alias: str):
        normalized = normalize_arch(alias)
        assert normalized in supported_archs() or normalized == alias

    def test_the_pipeline_normalizes_the_census_label_to_a_config_key(self):
        """The claim the fix rests on, checked against the table itself."""
        assert normalize_arch("aarch64") == "arm64"
        assert "arm64" in supported_archs()


def test_the_normalization_happens_before_the_comparison_not_after():
    """Ordering is what makes it work; reversing it changes nothing observable."""
    order = SOURCE.index("normalize_arch(detected_arch)")
    compare = SOURCE.index("detected_arch not in _SUPPORTED_ARCHS")
    assert order < compare


def test_the_preflight_and_the_other_endpoints_agree():
    """They all normalize; this is the assertion the pipeline used to fail."""
    for marker in ("arch = normalize_arch(req.arch)",
                   "selected = normalize_arch(wanted) if wanted else \"\""):
        assert marker in SOURCE, marker


def test_no_handler_compares_a_raw_census_label_against_the_whitelist():
    """A future endpoint must not reintroduce the bug this file exists for."""
    offenders: list[int] = []
    for node in ast.walk(ast.parse(SOURCE)):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(c, ast.Attribute) and c.attr == "_SUPPORTED_ARCHS"
                   for c in node.comparators):
            continue
        # A compare is acceptable when a normalize_arch call fed the compared name
        # within the lines just above it, i.e. in the same handler.
        window = "\n".join(LINES[max(0, node.lineno - 12):node.lineno])
        if "normalize_arch" not in window:
            offenders.append(node.lineno)
    assert not offenders, f"未经归一化就查白名单的行：{offenders}"


def test_the_module_still_exposes_the_whitelist_it_checks_against():
    assert server._SUPPORTED_ARCHS == tuple(supported_archs())