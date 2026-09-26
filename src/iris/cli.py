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
app.add_typer(db_app, name="db")
app.add_typer(extract_app, name="extract")
app.add_typer(corpus_app, name="corpus")

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
def extract_inspect(archive: Path) -> None:
    """Analyze a tar/tar.gz rootfs archive: rootfs candidate + arch census."""
    from collections import Counter

    from iris.extract.arch import identify_tar_members
    from iris.extract.rootfs import find_rootfs_in_archive

    import tarfile

    if not archive.exists():
        typer.secho(f"archive not found: {archive}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    cand = None
    arch_counter: Counter = Counter()
    if tarfile.is_tarfile(archive):
        cand = find_rootfs_in_archive(archive)
        arch_counter = identify_tar_members(archive)
    else:
        typer.secho("not a tar archive; raw firmware analysis comes in M1 (binwalk/unblob)", fg=typer.colors.YELLOW)
        raise typer.Exit(code=2)

    if cand is None:
        typer.secho("no rootfs candidate found", fg=typer.colors.RED)
        raise typer.Exit(code=3)

    typer.echo(f"rootfs prefix   : {cand.prefix or '<root>'}")
    typer.echo(f"unix dir hits   : {cand.unix_hits} (threshold {4})")
    typer.echo(f"busybox         : {cand.has_busybox}")
    typer.echo(f"/etc/init.d     : {cand.has_initd}")
    typer.echo(f"score           : {cand.score}")
    typer.echo(f"is_rootfs       : {cand.is_rootfs}")
    typer.echo(f"arch census     : {dict(arch_counter) or '<no ELF found>'}")


@extract_app.command("add")
def extract_add(
    archive: Path,
    brand: str = typer.Option(..., help="vendor brand name"),
    product: str = typer.Option("", help="product name"),
    version: str = typer.Option("", help="firmware version"),
    target_type: str = typer.Option("router", help="router / camera / ..."),
) -> None:
    """Register an extracted rootfs archive into the metadata database."""
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
        from collections import Counter

        from iris.extract.arch import identify_tar_members
        from iris.extract.rootfs import find_rootfs_in_archive

        counter = identify_tar_members(archive)
        if counter:
            arch = counter.most_common(1)[0][0]
        rootfs_ok = find_rootfs_in_archive(archive) is not None
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
        typer.echo(f"registered image id={image.id} brand={brand} arch={arch or '?'} md5={md5[:12]}")


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


if __name__ == "__main__":
    main()