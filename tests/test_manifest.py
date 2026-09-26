from pathlib import Path

from iris.corpus.manifest import (
    FirmwareEntry,
    load_manifest,
    rewrite_with_mirror,
)

ROOT = Path(__file__).parent.parent / "iris-home" / "corpus"


def test_rewrite_github_only():
    assert rewrite_with_mirror(
        "https://github.com/a/b/releases/download/1/x.zip", "https://gh-proxy.com/"
    ) == "https://gh-proxy.com/https://github.com/a/b/releases/download/1/x.zip"
    url = "https://downloads.openwrt.org/releases/1/x.bin"
    assert rewrite_with_mirror(url, "https://gh-proxy.com/") == url


def test_load_default_m0_manifest():
    manifest = load_manifest(ROOT / "m0-baseline.toml")
    assert manifest.name == "m0-baseline"
    assert len(manifest.entries) == 10
    confirmed = [e for e in manifest.entries if e.status == "confirmed"]
    assert len(confirmed) == 6
    archs = {e.arch_hint for e in confirmed}
    assert {"mipseb", "mipsel", "armel", "x64"} <= archs
    camera = [e for e in manifest.entries if e.target_type == "camera"]
    assert len(camera) == 1


def test_entry_validation():
    entry = FirmwareEntry(name="x", brand="B", url="https://example.com/f.bin")
    assert entry.target_type == "router"
    assert entry.status == "pending"