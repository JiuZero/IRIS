"""Firmware corpus manifest: load / validate / download entries."""

import hashlib
import os
import urllib.request
from pathlib import Path
from typing import Literal

import typer
from pydantic import BaseModel, Field

GITHUB_PREFIX = "https://github.com/"


class FirmwareEntry(BaseModel):
    name: str
    brand: str
    product: str = ""
    version: str = ""
    url: str
    target_type: Literal["router", "camera", "other"] = "router"
    arch_hint: str | None = None  # expected arch; verified by L1 after download
    sha256: str | None = None
    status: Literal["confirmed", "pending"] = "pending"
    source: str = ""
    notes: str = ""


class CorpusManifest(BaseModel):
    name: str
    description: str = ""
    entries: list[FirmwareEntry] = Field(default_factory=list)


def load_manifest(path: Path) -> CorpusManifest:
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        import tomli as tomllib  # type: ignore[no-redef]

    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    return CorpusManifest.model_validate(data)


def rewrite_with_mirror(url: str, mirror: str) -> str:
    """Route github.com URLs through the configured mirror (other hosts untouched)."""
    if url.startswith(GITHUB_PREFIX) and mirror:
        return mirror.rstrip("/") + "/" + url
    return url


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_entry(entry: FirmwareEntry, dest_dir: Path, mirror: str = "", timeout: int = 60) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = Path(entry.url.split("?")[0]).name
    if not name or name in ("", "/", "..", "."):
        name = entry.name
    dest = dest_dir / name
    if dest.exists():
        if entry.sha256 and sha256_file(dest) != entry.sha256:
            typer.secho(f"corrupt cache (sha mismatch), re-downloading: {dest.name}", fg=typer.colors.YELLOW)
            dest.unlink()
        else:
            typer.echo(f"skip (exists): {dest.name}")
            return dest
    url = rewrite_with_mirror(entry.url, mirror)
    typer.echo(f"downloading [{entry.status}] {entry.name} <- {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "IRIS-corpus/0.1"})
    tmp = dest.with_name(dest.name + ".part")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp, "wb") as fh:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
        if entry.sha256:
            actual = sha256_file(tmp)
            if actual != entry.sha256:
                raise ValueError(f"sha256 mismatch for {entry.name}: {actual}")
            typer.echo(f"sha256 OK: {actual[:16]}")
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return dest