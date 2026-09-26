import hashlib
from pathlib import Path

import typer

from iris.config import get_settings
from iris.db.engine import get_engine, init_db, make_session
from iris.log import get_logger, setup_logging

app = typer.Typer(help="IRIS - IoT Rehosting & Interconnection Simulator", no_args_is_help=True)
db_app = typer.Typer(help="metadata database operations")
extract_app = typer.Typer(help="L1 extraction utilities")
corpus_app = typer.Typer(help="firmware corpus manifest operations")
emulate_app = typer.Typer(help="L2 emulation utilities")
app.add_typer(db_app, name="db")
app.add_typer(extract_app, name="extract")
app.add_typer(corpus_app, name="corpus")
app.add_typer(emulate_app, name="emulate")

log = get_logger(__name__)


@db_app.command("init")
def db_init() -> None:
    """Create all IRIS metadata tables."""
    settings = get_settings()
    engine = get_engine(settings.database_url)
    init_db(engine)
    typer.echo(f"database initialized: {settings.database_url}")


@db_app.command("check")
def db_check() -> None:
    """Verify database connectivity."""
    from sqlalchemy import text

    settings = get_settings()
    engine = get_engine(settings.database_url)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    typer.echo("database connection OK")


@extract_app.command("inspect")
def extract_inspect(
    archive: Path,
    arch_hint: str = typer.Option("", help="arch hint for ambiguous cases (mipseb/mipsel/armel/x64)"),
) -> None:
    """Analyze a firmware image: tar rootfs or raw firmware format/arch inference."""
    import tarfile

    from iris.extract.arch import identify_tar_members
    from iris.extract.rootfs import find_rootfs_in_archive

    if not archive.exists():
        typer.secho(f"archive not found: {archive}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    if tarfile.is_tarfile(archive):
        cand = find_rootfs_in_archive(archive)
        arch_counter = identify_tar_members(archive)
        if cand is None:
            typer.secho("no rootfs candidate found", fg=typer.colors.RED)
            raise typer.Exit(code=3)
        typer.echo("format          : tar archive")
        typer.echo(f"rootfs prefix   : {cand.prefix or '<root>'}")
        typer.echo(f"unix dir hits   : {cand.unix_hits} (threshold {4})")
        typer.echo(f"busybox         : {cand.has_busybox}")
        typer.echo(f"/etc/init.d     : {cand.has_initd}")
        typer.echo(f"score           : {cand.score}")
        typer.echo(f"is_rootfs       : {cand.is_rootfs}")
        typer.echo(f"arch census     : {dict(arch_counter) or '<no ELF found>'}")
        return

    from iris.extract.firmware import analyze_firmware

    data = archive.read_bytes()
    info = analyze_firmware(data, arch_hint=arch_hint)
    typer.echo(f"format          : {info.format}")
    typer.echo(f"arch            : {info.arch or '<unknown>'}")
    typer.echo(f"rootfs offset   : {info.rootfs_offset if info.rootfs_offset is not None else '<not found>'}")
    if info.uimage:
        typer.echo(f"uImage name     : {info.uimage.name}")
        typer.echo(f"uImage arch     : field={info.uimage.arch_field} inferred={info.uimage.arch_name}")
        typer.echo(f"uImage comp     : {info.uimage.comp}")
        typer.echo(f"uImage load/ep  : 0x{info.uimage.load:08x} / 0x{info.uimage.ep:08x}")
    if info.squashfs:
        for sq in info.squashfs:
            typer.echo(f"squashfs        : offset=0x{sq.offset:x} endian={sq.endian} comp={sq.comp}")
    if info.elf_archs:
        typer.echo(f"ELF census      : {dict(info.elf_archs)}")


@extract_app.command("add")
def extract_add(
    archive: Path,
    brand: str = typer.Option(..., help="vendor brand name"),
    product: str = typer.Option("", help="product name"),
    version: str = typer.Option("", help="firmware version"),
    target_type: str = typer.Option("router", help="router / camera / ..."),
    arch_hint: str = typer.Option("", help="arch hint for ambiguous cases (mipseb/mipsel/armel/x64)"),
) -> None:
    """Register a firmware image into the metadata database."""
    from iris.db.models import Brand, Image

    settings = get_settings()
    engine = get_engine(settings.database_url)
    md5 = hashlib.md5(archive.read_bytes()).hexdigest()

    with make_session(engine) as session:
        brand_obj = session.query(Brand).filter_by(name=brand).one_or_none()
        if brand_obj is None:
            brand_obj = Brand(name=brand)
            session.add(brand_obj)
            session.flush()
        existing = session.query(Image).filter_by(hash=md5).one_or_none()
        if existing is not None:
            typer.echo(f"already registered: image id={existing.id}")
            raise typer.Exit()
        arch = ""
        rootfs_ok = False
        import tarfile

        from iris.extract.arch import identify_tar_members
        from iris.extract.rootfs import find_rootfs_in_archive

        if tarfile.is_tarfile(archive):
            counter = identify_tar_members(archive)
            if counter:
                arch = counter.most_common(1)[0][0]
            rootfs_ok = find_rootfs_in_archive(archive) is not None
        else:
            from iris.extract.firmware import analyze_firmware

            data = archive.read_bytes()
            fw_info = analyze_firmware(data, arch_hint=arch_hint)
            arch = fw_info.arch
            rootfs_ok = fw_info.rootfs_offset is not None
            typer.secho(
                f"  (raw firmware {fw_info.format}; arch={arch or '?'} rootfs={rootfs_ok})",
                fg=typer.colors.YELLOW,
            )
        image = Image(
            filename=archive.name,
            description=f"{product} {version}".strip() or None,
            brand_id=brand_obj.id,
            hash=md5,
            rootfs_extracted=rootfs_ok,
            arch=arch or None,
            target_type=target_type,
        )
        session.add(image)
        session.flush()
        session.commit()
        typer.echo(f"registered image id={image.id} brand={brand} arch={arch or '?'} md5={md5[:12]}")


@extract_app.command("rootfs")
def extract_rootfs(
    firmware: Path,
    arch_hint: str = typer.Option("", help="arch hint (mipseb/mipsel/armel/x64)"),
) -> None:
    """Extract squashfs rootfs from a firmware image and verify arch via ELF census."""
    from iris.extract.rootfs_extract import extract_rootfs as do_extract

    if not firmware.exists():
        typer.secho(f"firmware not found: {firmware}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    settings = get_settings()
    scratch = settings.scratch_dir
    typer.echo(f"extracting rootfs from {firmware.name} ...")
    result = do_extract(firmware, scratch, arch_hint=arch_hint)

    fi = result.firmware_info
    typer.echo(f"format          : {fi.format}")
    typer.echo(f"arch (inferred) : {fi.arch or '?'}")
    typer.echo(f"rootfs offset   : {fi.rootfs_offset if fi.rootfs_offset is not None else '<not found>'}")

    if result.rootfs_dir is None:
        typer.secho("no squashfs rootfs found; nothing to extract", fg=typer.colors.YELLOW)
        raise typer.Exit(code=2)

    typer.echo(f"squashfs file   : {result.squashfs_path}")
    typer.echo(f"rootfs dir      : {result.rootfs_dir}")
    typer.echo(f"extraction method: {result.extraction_method or 'unknown'}")
    typer.echo(f"ELF count       : {result.elf_count}")
    typer.echo(f"ELF arch census : {dict(result.elf_archs) or '<none>'}")
    typer.echo(f"arch (verified) : {result.arch_verified or '?'}")
    if fi.arch and result.arch_verified:
        match = "OK" if fi.arch == result.arch_verified else "MISMATCH"
        typer.echo(f"arch check      : {fi.arch} vs {result.arch_verified} -> {match}")


@corpus_app.command("list")
def corpus_list(
    manifest: Path = typer.Argument(Path("iris-home/corpus/m0-baseline.toml")),
) -> None:
    """List entries of a corpus manifest."""
    from iris.corpus.manifest import load_manifest

    m = load_manifest(manifest)
    typer.echo(f"manifest: {m.name} ({m.description})")
    for e in m.entries:
        typer.echo(
            f"  [{e.status:^8}] {e.name:<24} brand={e.brand:<12} arch={e.arch_hint or '-':<7} {e.target_type}"
        )


@corpus_app.command("download")
def corpus_download(
    manifest: Path = typer.Argument(Path("iris-home/corpus/m0-baseline.toml")),
    only: str = typer.Option("", help="download only entries whose name contains this string"),
    include_pending: bool = typer.Option(False, help="also download pending entries"),
) -> None:
    """Download firmware files into iris-home/corpus/."""
    from iris.corpus.manifest import download_entry, load_manifest

    settings = get_settings()
    m = load_manifest(manifest)
    dest_dir = settings.corpus_dir
    failures = 0
    for e in m.entries:
        if only and only not in e.name:
            continue
        if e.status == "pending" and not include_pending:
            continue
        if e.url.startswith("https://TBD"):
            continue
        try:
            download_entry(e, dest_dir, mirror=settings.download_mirror)
        except Exception as exc:  # noqa: BLE001 - report and continue with next entry
            failures += 1
            typer.secho(f"  FAILED: {e.name}: {exc}", fg=typer.colors.RED)
    typer.echo(f"done, {failures} failure(s)")


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    app()


@emulate_app.command("run")
def emulate_run(
    rootfs: Path = typer.Argument(..., help="path to extracted rootfs directory"),
    arch: str = typer.Option(..., help="target architecture (mipsel/mipseb/armel)"),
    iid: int = typer.Option(0, help="image ID for scratch directory naming"),
    port: int = typer.Option(8080, help="host port for web access"),
    timeout: int = typer.Option(120, help="boot timeout in seconds"),
) -> None:
    """Run QEMU emulation of a firmware rootfs and check web reachability."""
    from iris.emulate.orchestrator import emulate_firmware

    if not rootfs.exists():
        typer.secho(f"rootfs not found: {rootfs}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    settings = get_settings()
    scratch = settings.scratch_dir

    typer.echo(f"emulating {rootfs.name} arch={arch} port={port}")
    result = emulate_firmware(
        rootfs_dir=rootfs,
        arch=arch,
        iid=iid if iid > 0 else abs(hash(str(rootfs))) % 10000,
        scratch_dir=scratch,
        host_port=port,
        timeout_sec=timeout,
    )

    typer.echo(f"success     : {result.success}")
    typer.echo(f"web ok      : {result.web_ok}")
    typer.echo(f"web url     : {result.web_url or '-'}")
    typer.echo(f"duration    : {result.duration_sec:.1f}s")
    if result.error:
        typer.secho(f"error       : {result.error}", fg=typer.colors.RED)
    if result.serial_log:
        typer.echo("serial log (tail):")
        for line in result.serial_log.splitlines()[-20:]:
            typer.echo(f"  {line}")


@emulate_app.command("stop")
def emulate_stop(
    iid: int = typer.Argument(..., help="image ID to stop"),
) -> None:
    """Stop a running QEMU emulation container."""
    from iris.emulate.orchestrator import stop_emulation

    ok = stop_emulation(iid)
    typer.echo(f"stopped: {ok}")


if __name__ == "__main__":
    main()