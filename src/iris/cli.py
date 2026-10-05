import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import typer

from iris.arch import census_to_runnable, normalize_arch
from iris.config import get_settings
from iris.db.engine import get_engine, init_db, make_session
from iris.failures import Failure, FailureKind
from iris.fsutil import safe_is_file, safe_present, safe_stat_size
from iris.log import LEVEL_COLORS, get_error_logger, get_stream_logger, setup_logging

app = typer.Typer(help="IRIS - IoT Rehosting & Interconnection Simulator", no_args_is_help=True)
db_app = typer.Typer(help="metadata database operations")
extract_app = typer.Typer(help="L1 extraction utilities")
corpus_app = typer.Typer(help="firmware corpus manifest operations")
emulate_app = typer.Typer(help="L2 emulation utilities")
rules_app = typer.Typer(help="L3 boot-fix rule engine")
serve_app = typer.Typer(help="L5 API server")
guest_app = typer.Typer(help="read and write the guest's own filesystem")
app.add_typer(db_app, name="db")
app.add_typer(extract_app, name="extract")
app.add_typer(corpus_app, name="corpus")
app.add_typer(emulate_app, name="emulate")
app.add_typer(rules_app, name="rules")
app.add_typer(serve_app, name="serve")
app.add_typer(guest_app, name="guest")

#: Progress, results and reports go to stdout; failures go to stderr so that
#: `iris ... 2>/dev/null` still shows what went wrong. Both render through the
#: same line shape, so a run reads as one stream when a terminal shows both.
#:
#: These are stream loggers rather than structlog loggers because the CLI needs
#: stderr and multi-line aligned blocks, neither of which structlog can express
#: (see iris.log.StreamLogger).
out = get_stream_logger()
err = get_error_logger()


def _rows(pairs: Sequence[tuple[str, str]], width: int = 16) -> list[str]:
    """Render ``label : value`` pairs as one aligned column.

    Aligned inside a single log record rather than logged row by row: a timestamp
    on every row would shift each one right, and the value column would no longer
    start at the same place on every line.
    """
    return [f"{label + ' ':<{width}}: {value}" for label, value in pairs]


def _reject_zip(path: Path) -> None:
    """Outer zip packages have unpredictable layout; require the inner .bin."""
    is_zip = path.suffix.lower() == ".zip"
    if not is_zip:
        with path.open("rb") as f:
            is_zip = f.read(4) == b"PK\x03\x04"
    if is_zip:
        err.error(
            f"refusing zip container: {path.name} — unpack the upgrade package locally "
            "and pass the firmware .bin file"
        )
        raise typer.Exit(code=2)


@db_app.command("init")
def db_init() -> None:
    """Create all IRIS metadata tables."""
    settings = get_settings()
    engine = get_engine(settings.database_url)
    init_db(engine)
    out.info(f"database initialized: {settings.database_url}")


@db_app.command("check")
def db_check() -> None:
    """Verify database connectivity."""
    from sqlalchemy import text

    settings = get_settings()
    engine = get_engine(settings.database_url)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    out.info("database connection OK")


@db_app.command("cards")
def db_cards(
    promote_only: bool = typer.Option(
        False,
        "--promote-only",
        help="only the kinds still seen in the most recent runs -- what a new "
             "deterministic rule would actually move",
    ),
    recent: int = typer.Option(
        10,
        help="how many of the most recent failing runs count as live for --promote-only",
    ),
) -> None:
    """Read the failure history back as one root-cause card per failure kind.

    ``iris db stats`` says how often things broke. This says when each kind was
    last seen and which rules were already applied to it -- so a kind that has
    dropped out of the recent window is a fix that landed, and a kind still in it
    is work that has not been done yet.

    ``recovered`` reads 0 across the current corpus, which is a fact about the data
    and not a finding: no successful run has ever carried a failure row.

    Nothing is applied automatically. The output is a ranked work list.
    """
    from iris.db.knowledge import promote_candidates, root_cause_cards

    settings = get_settings()
    engine = get_engine(settings.database_url)
    init_db(engine)
    with make_session(engine) as session:
        cards = root_cause_cards(session)

    if not cards:
        out.warning("no failure profiles recorded yet; run `iris emulate run <firmware>` first")
        return

    shown = promote_candidates(cards, recent=recent) if promote_only else cards
    rows = [
        (
            f"{c.kind} [{c.stage}]",
            (
                f"{c.runs} run(s) over {c.images} image(s), "
                f"arch={','.join(c.archs) or '-'}, "
                f"last={c.last_seen or 'undated'}, "
                f"repairs={','.join(c.repairs) or 'none'}"
            ),
        )
        for c in shown
    ]
    title = f"root-cause cards (live within the last {recent} failing run(s))" if promote_only \
        else "root-cause cards"
    out.block("info", title, _rows(rows))

    if not promote_only:
        candidates = promote_candidates(cards, recent=recent)
        dropped = [c.kind for c in cards if c not in candidates]
        if dropped:
            out.info(f"no longer seen recently (a fix landed?): {', '.join(dropped)}")
        out.info("  rerun with --promote-only to see just the live ones")
    elif not shown:
        out.info("no failure kind is live in the recent window")


@db_app.command("stats")
def db_stats() -> None:
    """Summarise recorded emulation runs: reach rate per arch and failure histogram.

    Every figure here is read back from ``emulation_run``/``failure_profile``, so
    it describes runs that actually happened -- including the ones that left no
    evidence. When the tables are empty this says so instead of printing zeros
    that look like measurements.
    """
    from iris.db.runs import failure_histogram, run_stats

    settings = get_settings()
    engine = get_engine(settings.database_url)
    init_db(engine)
    with make_session(engine) as session:
        stats = run_stats(session)
        histogram = failure_histogram(session)

    if stats.total == 0:
        out.warning("no emulation runs recorded yet; run `iris emulate run <firmware>` first")
        return

    out.block("info", f"{stats.total} recorded run(s)", _rows([
        ("web reachable", f"{stats.web_ok}/{stats.total} ({stats.web_rate:.1%})"),
    ]))

    if stats.by_arch:
        pairs = []
        for arch in sorted(stats.by_arch):
            runs, ok = stats.by_arch[arch]
            pairs.append((arch, f"{ok}/{runs} web ({ok / runs:.0%})" if runs else "-"))
        out.block("info", "by arch", _rows(pairs))

    if histogram:
        out.block("info", "failure histogram",
                  _rows([(f"{kind} [{stage}]", str(count)) for stage, kind, count in histogram]))
    else:
        out.info("no failure profiles recorded")


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
        err.error(f"archive not found: {archive}")
        raise typer.Exit(code=1)
    _reject_zip(archive)

    if tarfile.is_tarfile(archive):
        cand = find_rootfs_in_archive(archive)
        arch_counter = identify_tar_members(archive)
        if cand is None:
            err.error("no rootfs candidate found")
            raise typer.Exit(code=3)
        out.block("info", "inspect result (tar archive)", _rows([
            ("format", "tar archive"),
            ("rootfs prefix", cand.prefix or "<root>"),
            ("unix dir hits", f"{cand.unix_hits} (threshold 4)"),
            ("busybox", str(cand.has_busybox)),
            ("/etc/init.d", str(cand.has_initd)),
            ("score", str(cand.score)),
            ("is_rootfs", str(cand.is_rootfs)),
            ("arch census", str(dict(arch_counter) or "<no ELF found>")),
        ]))
        return

    from iris.extract.firmware import analyze_firmware

    data = archive.read_bytes()
    info = analyze_firmware(data, arch_hint=arch_hint)
    pairs = [
        ("format", str(info.format)),
        ("arch", info.arch or "<unknown>"),
        ("rootfs offset", str(info.rootfs_offset) if info.rootfs_offset is not None else "<not found>"),
    ]
    if info.uimage:
        pairs.extend([
            ("uImage name", str(info.uimage.name)),
            ("uImage arch", f"field={info.uimage.arch_field} inferred={info.uimage.arch_name}"),
            ("uImage comp", str(info.uimage.comp)),
            ("uImage load/ep", f"0x{info.uimage.load:08x} / 0x{info.uimage.ep:08x}"),
        ])
    for sq in info.squashfs or []:
        pairs.append(("squashfs", f"offset=0x{sq.offset:x} endian={sq.endian} comp={sq.comp}"))
    if info.tendaw:
        tw = info.tendaw
        pairs.append(("tendaw", f"model={tw.model} version={tw.version} zip@0x{tw.zip_offset:x}"))
        for p in tw.partitions:
            pairs.append((
                f"  part {p.name}",
                f"{p.payload_type:<8} size={p.size} mount={p.mount_point or '-'}",
            ))
        if tw.scripts:
            pairs.append(("  scripts", str(tw.scripts)))
        if tw.unreadable:
            pairs.append(("  unreadable", str(tw.unreadable)))
    if info.fit:
        pairs.append(("fit", "True (Flattened Image Tree inner image)"))
    if info.segmented_offsets:
        pairs.append((
            "encrypted segs",
            f"{len(info.segmented_offsets)} (first 0x{info.segmented_offsets[0]:x})",
        ))
    if info.elf_archs:
        pairs.append(("ELF census", str(dict(info.elf_archs))))
    out.block("info", f"inspect result ({archive.name})", _rows(pairs))


@extract_app.command("add")
def extract_add(
    archive: Path,
    brand: str = typer.Option(..., help="vendor brand name"),
    product: str = typer.Option("", help="product name"),
    version: str = typer.Option("", help="firmware version"),
    target_type: str = typer.Option("router", help="router / camera / ..."),
    arch_hint: str = typer.Option("", help="arch hint for ambiguous cases (mipseb/mipsel/armel/x64)"),
    verify: bool = typer.Option(True, help="extract rootfs and persist the ELF-verified arch"),
) -> None:
    """Register a firmware image into the metadata database."""
    from iris.db.models import Brand, Image

    if not archive.exists():
        err.error(f"firmware not found: {archive}")
        raise typer.Exit(code=1)
    _reject_zip(archive)
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
            out.info(f"already registered: image id={existing.id}")
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
            rootfs_ok = fw_info.rootfs_offset is not None or fw_info.tendaw is not None
            out.info(f"raw firmware {fw_info.format}; arch={arch or '?'} rootfs={rootfs_ok}")
            if verify and rootfs_ok:
                from iris.extract.rootfs_extract import extract_rootfs as do_extract

                try:
                    ext = do_extract(archive, settings.scratch_dir, arch_hint=arch_hint)
                    if ext.arch_verified:
                        arch = ext.arch_verified
                        out.info(f"ELF census: {ext.elf_count} binaries -> {arch}")
                except Exception as exc:  # noqa: BLE001 - best-effort probe, never fail the import
                    err.warning(f"arch verify skipped: {exc}")
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
        out.info(f"registered image id={image.id} brand={brand} arch={arch or '?'} md5={md5[:12]}")


@extract_app.command("rootfs")
def extract_rootfs(
    firmware: Path,
    arch_hint: str = typer.Option("", help="arch hint (mipseb/mipsel/armel/x64)"),
    out_dir: Path = typer.Option(
        None,
        "--out",
        help="write the extracted rootfs tree here instead of iris-home/scratch; "
        "refuses to overwrite a non-empty directory unless --force",
    ),
    force: bool = typer.Option(
        False, "--force", help="replace the contents of --out when it already holds a tree"
    ),
) -> None:
    """Extract squashfs rootfs from a firmware image and verify arch via ELF census."""
    from iris.extract.rootfs_extract import extract_rootfs as do_extract

    if not firmware.exists():
        err.error(f"firmware not found: {firmware}")
        raise typer.Exit(code=1)
    _reject_zip(firmware)

    settings = get_settings()
    scratch = settings.scratch_dir
    out.info(f"extracting rootfs from {firmware.name} ...")
    try:
        result = do_extract(
            firmware, scratch, arch_hint=arch_hint, out_dir=out_dir, force=force
        )
    except ValueError as exc:
        # The exception already names the path and the remedy; repeating them in a
        # prefix just prints the directory twice.
        err.error(str(exc))
        raise typer.Exit(code=2) from exc

    fi = result.firmware_info
    pairs = [
        ("format", str(fi.format)),
        ("arch (inferred)", fi.arch or "?"),
        ("rootfs offset", str(fi.rootfs_offset) if fi.rootfs_offset is not None else "<not found>"),
    ]
    if result.rootfs_dir is None:
        reason = result.failure_reason or "no rootfs structure found"
        out.block("error", f"extraction failed for {firmware.name}", _rows(pairs))
        err.error(f"extraction failed: {reason}")
        raise typer.Exit(code=2)

    pairs.extend([
        ("squashfs file", str(result.squashfs_path)),
        ("rootfs dir", str(result.rootfs_dir)),
        ("extraction method", result.extraction_method or "unknown"),
        ("ELF count", str(result.elf_count)),
        ("ELF arch census", str(dict(result.elf_archs) or "<none>")),
        ("arch (verified)", result.arch_verified or "?"),
    ])
    if fi.arch and result.arch_verified:
        match = "OK" if fi.arch == result.arch_verified else "MISMATCH"
        pairs.append(("arch check", f"{fi.arch} vs {result.arch_verified} -> {match}"))
    out.block("info", f"rootfs extracted from {firmware.name}", _rows(pairs))


@corpus_app.command("list")
def corpus_list(
    manifest: Path = typer.Argument(Path("iris-home/corpus/m0-baseline.toml")),
) -> None:
    """List entries of a corpus manifest."""
    from iris.corpus.manifest import load_manifest

    m = load_manifest(manifest)
    rows = [
        f"[{e.status:^8}] {e.name:<24} brand={e.brand:<12} arch={e.arch_hint or '-':<7} {e.target_type}"
        for e in m.entries
    ]
    out.block("info", f"manifest: {m.name} ({m.description})", rows)


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
            err.error(f"FAILED: {e.name}: {exc}")
    out.info(f"done, {failures} failure(s)")


@corpus_app.command("eval")
def corpus_eval(
    manifest: Path = typer.Argument(Path("iris-home/corpus/m0-baseline.toml")),
    # `None`, not `Path("")`: typer renders an empty Path default as "." and
    # `bool(Path("."))` is True, so an "unset" Path option reads the current
    # directory and raises PermissionError instead of being skipped.
    observations: Path | None = typer.Option(
        None,
        help="JSON file of {entry_name: {web_ok, duration_sec, failure_kind}}; "
             "omit to report the manifest's declared expectations unmeasured",
    ),
    env_broken: str = typer.Option(
        "",
        help="comma-separated entry names whose failure was environmental, not IRIS's",
    ),
    from_db: bool = typer.Option(
        False,
        "--from-db",
        help="take each entry's observation from the newest recorded run instead of --observations",
    ),
    baseline: Path | None = typer.Option(
        None,
        help="a previous report JSON to diff against; prints per-device regressions",
    ),
    write: Path | None = typer.Option(None, help="also write the Markdown report here"),
    write_json: Path | None = typer.Option(
        None, help="also write the machine-readable report here (for --baseline)"
    ),
) -> None:
    """Score the corpus against declared expectations.

    The denominator is the entries that declare an expectation and were actually
    measured -- never "all entries in the manifest", because an unmeasured device
    silently becomes a failure that way.
    """
    import json

    from iris.corpus.baseline import (
        evaluate,
        render_markdown,
        report_from_json,
        report_to_json,
    )
    from iris.corpus.manifest import load_manifest

    m = load_manifest(manifest)
    observed: dict[str, dict[str, object]] = {}
    if from_db:
        from iris.corpus.baseline import observations_from_db

        settings = get_settings()
        engine = get_engine(settings.database_url)
        init_db(engine)
        with make_session(engine) as session:
            observed = observations_from_db(m.entries, session)
        out.info(f"{len(observed)}/{len(m.entries)} entries matched a recorded run")
    elif observations:
        observed = json.loads(observations.read_text(encoding="utf-8"))
    broken = tuple(n.strip() for n in env_broken.split(",") if n.strip())

    report = evaluate(m.entries, observed, env_broken=broken)

    rows = [
        f"{r.name:<28} {r.verdict:<10} web={'-' if r.web_ok is None else r.web_ok!s:<5} "
        f"{r.duration_sec:>7.1f}s {r.failure_kind}"
        for r in report.results
    ]
    out.block("info", f"corpus eval: {m.name}", rows)
    if report.total == 0:
        # 0/0 printed as "0%" reads as "everything failed", which is the opposite
        # of what happened: nothing was measured. Say which it is.
        err.warning(
            "denominator is 0 -- no entry declared an expectation and was measured. "
            "This is NOT a 0% success rate; pass --from-db or --observations."
        )
    out.info(
        f"participants={report.total} met={report.met} "
        f"web_rate={report.web_rate:.0%} env_broken={report.env_broken} "
        f"capability_rate={report.capability_rate:.0%}"
    )

    if baseline:
        # An absent baseline file must say so. Silently skipping the comparison
        # prints no regression line, which reads as "nothing got worse" -- the
        # one conclusion a regression check exists to prevent.
        if not baseline.is_file():
            err.warning(
                f"baseline {baseline} does not exist -- no comparison was made. "
                "This is NOT a clean bill of health; write one with --write-json."
            )
        else:
            previous = report_from_json(json.loads(baseline.read_text(encoding="utf-8")))
            regressions = report.regressions_against(previous)
            if regressions:
                for r in regressions:
                    err.warning(
                        f"REGRESSION: {r.name} was met, now {r.verdict} "
                        f"({r.failure_kind or 'no failure kind recorded'})"
                    )
            else:
                out.info(f"no regressions against {baseline.name}")

    if write:
        write.parent.mkdir(parents=True, exist_ok=True)
        write.write_text(render_markdown(report, title=f"{m.name} 评测基线"), encoding="utf-8")
        out.info(f"report written: {write}")

    if write_json:
        write_json.parent.mkdir(parents=True, exist_ok=True)
        write_json.write_text(
            json.dumps(report_to_json(report), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        out.info(f"machine-readable report written: {write_json}")


@rules_app.command("list")
def rules_list() -> None:
    """Show all L3 boot-fix rules, built-in and installed plugins alike."""
    from iris.rules.engine import load_all_rules

    settings = get_settings()
    rows = [
        f"{r.id:<26} stage={r.stage:<10} {(r.description.splitlines() or [''])[0][:64]}"
        for r in load_all_rules(settings.effective_rules_dirs)
    ]
    out.block("info", "L3 boot-fix rules", rows)


@rules_app.command("apply")
def rules_apply(
    rootfs: Path = typer.Argument(..., help="path to extracted rootfs directory"),
    apply: bool = typer.Option(False, "--apply", help="write changes (default: dry-run report)"),
) -> None:
    """Detect boot-failure patterns in a rootfs and apply rule fixes."""
    from iris.rules.engine import apply_rules, load_all_rules, report_json

    reports = apply_rules(
        rootfs,
        load_all_rules(get_settings().effective_rules_dirs),
        dry_run=not apply,
    )
    # The one place in the CLI that writes a bare line: this is a machine-readable
    # report, not a log record, and `iris rules apply ... | jq` has to keep working.
    # A timestamp in front of the JSON would make the output unparseable.
    sys.stdout.write(report_json(reports) + "\n")
    sys.stdout.flush()


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    app()


@emulate_app.command("run")
def emulate_run(
    target: Path = typer.Argument(..., help="firmware .bin or extracted rootfs directory"),
    arch: str = typer.Option(
        "auto",
        help=(
            "target architecture (mipsel/mipseb/armel/arm64; the ELF spellings "
            "aarch64/arm64le are accepted too) or 'auto' for ELF census inference"
        ),
    ),
    iid: int = typer.Option(0, help="image ID for scratch directory naming"),
    port: int = typer.Option(8080, help="host port for web access (use 0 to pick a free one)"),
    timeout: int = typer.Option(120, help="boot timeout in seconds"),
    force: bool = typer.Option(False, "--force", help="skip the rootfs ELF arch preflight"),
    apply_rules: bool = typer.Option(True, "--apply-rules/--no-apply-rules", help="apply L3 boot-fix rules during emulation build"),
    parts_dir: Path = typer.Option(
        None, "--parts-dir", help="TendaW -parts dir of raw .jffs2 slices; merges them container-side (symlink-safe)"
    ),
) -> None:
    """Run QEMU emulation of a firmware rootfs and check web reachability.

    Accepts either an extracted rootfs directory or a firmware file (.bin). When a firmware file is given,
    IRIS extracts it, infers the target architecture from the ELF census, applies boot-fix rules, and proceeds
    to emulation. Use --arch auto (default) for automatic architecture detection, or specify a concrete arch
    such as mipsel/mipseb/armel/arm64."""
    from iris.emulate.auto import pick_host_port, prepare_from_firmware, prepare_from_rootfs
    from iris.emulate.orchestrator import build_parts_mounts, emulate_firmware, preflight_arch
    from iris.emulate.qemu_config import supported_archs

    if not safe_present(target):
        err.error(f"not found: {target}")
        raise typer.Exit(code=1)

    # Only reject zips if input is a file
    if safe_is_file(target):
        _reject_zip(target)

    settings = get_settings()
    scratch = settings.scratch_dir

    # Decide whether input is firmware .bin vs pre-extracted rootfs
    inferred_arch = ""
    if safe_is_file(target):
        out.info(f"extracting rootfs from {target.name} ...")
        prepared = prepare_from_firmware(
            target,
            scratch_dir=scratch,
            arch_hint=arch if arch != "auto" else "",
            apply_rules_flag=apply_rules,
            rules_dir=settings.rules_dir,
            dry_run_rules=False,  # Write fixes to disk for auto-pipeline
        )
        if prepared.failure_reason:
            err.error(f"extraction failed: {prepared.failure_reason}")
            raise typer.Exit(code=2)
        if applied_rules := prepared.matched_rule_ids:
            out.info(f"L3 rules matched: {', '.join(applied_rules)}")
        if arch == "auto":
            inferred_arch = prepared.arch
        else:
            inferred_arch = arch
        rootfs = prepared.rootfs_dir
    else:
        # Pre-extracted rootfs — just infer arch and report rule matches (dry-run)
        out.info(f"using existing rootfs: {target.resolve()}")
        prepared = prepare_from_rootfs(rootfs_dir=target, rules_dir=settings.rules_dir)
        if arch == "auto":
            inferred_arch = prepared.arch
        else:
            inferred_arch = arch
        if arch != "auto" and prepared.arch and prepared.arch != arch:
            err.warning(f"rootfs ELF census suggests {prepared.arch}, using {arch} instead")
        if applied_rules := prepared.matched_rule_ids:
            out.info(f"L3 rules matched: {', '.join(applied_rules)}")
        rootfs = target

    # One normaliser for every spelling a caller may use: the ELF census reports
    # `aarch64`, the kernel assets and --arch take `arm64`, and extract/arch.py
    # still reports `arm64le`. This used to be two inline copies of a four-entry
    # dict in this function plus a third in auto.py and a fourth in orchestrator.py.
    selected_arch = ""
    if inferred_arch:
        selected_arch = census_to_runnable(inferred_arch) or normalize_arch(inferred_arch)

    # Preflight is the authority on whether an architecture can be emulated; it
    # also rejects a rootfs whose dominant architecture has no kernel here.
    if selected_arch and not selected_arch.startswith("unk("):
        problem = preflight_arch(rootfs, selected_arch) if not force else None
        if problem:
            err.error(f"preflight: {problem}")
            err.warning("(override with --force)")
            raise typer.Exit(code=3)

    if not selected_arch:
        selected_arch = "auto"

    # If still auto after all inference attempts, show error
    if selected_arch == "auto":
        supported = supported_archs()
        err.error(Failure(
            FailureKind.ARCH_UNDETERMINED,
            f"the ELF census named no architecture this host can run; pass one explicitly "
            f"(supported: {', '.join(supported)})",
            evidence={"supported": list(supported)},
        ).message)
        err.error(f"  Usage: iris emulate run <rootfs|bin> --arch {'|'.join(supported)}")
        raise typer.Exit(code=3)

    out.info(f"emulating {target.name} arch={selected_arch}")
    result_port = port if port != 0 else 8080
    try:
        if port == 0:
            selected_port = pick_host_port(preferred=port)
            out.info(f"picked host port: {selected_port}")
            result_port = selected_port
    except RuntimeError:
        err.error("no available host port in range [8080,8199]; use --port XXX")
        raise typer.Exit(code=3) from None

    partition_mounts = None
    if parts_dir is not None:
        if not parts_dir.is_dir():
            err.error(f"parts dir not found: {parts_dir}")
            raise typer.Exit(code=1)
        partition_mounts = build_parts_mounts(parts_dir)
        if not partition_mounts:
            err.error(f"no .jffs2 slices under {parts_dir}")
            raise typer.Exit(code=1)

    result = emulate_firmware(
        rootfs_dir=rootfs,
        arch=selected_arch,
        iid=iid if iid > 0 else int(hashlib.md5(str(rootfs.resolve()).encode()).hexdigest(), 16) % 10000,
        scratch_dir=scratch,
        host_port=result_port,
        timeout_sec=timeout,
        parts_slices_dir=parts_dir,
        partition_mounts=partition_mounts,
        applied_rule_ids=tuple(prepared.matched_rule_ids),
    )

    # The verdict is one record so the result reads as a single outcome; the
    # serial tail is a second one because it is bulk guest output, not a summary,
    # and grepping a redirected log should find it without the summary in between.
    outcome = "success" if result.success else "failure"
    summary = _rows([
        ("success", str(result.success)),
        ("web ok", str(result.web_ok)),
        ("web url", result.web_url or "-"),
        ("duration", f"{result.duration_sec:.1f}s"),
    ])
    if result.error:
        summary.append(f"{'error':<17}: {result.error}")
    out.block("info" if result.success else "error", f"emulation {outcome}", summary)
    if result.error:
        err.error(f"emulation {outcome}: {result.error}")
    if result.serial_log:
        out.block("info", "serial log (tail)", result.serial_log.splitlines()[-20:])


@emulate_app.command("stop")
def emulate_stop(
    iid: int = typer.Argument(..., help="image ID to stop"),
) -> None:
    """Stop a running QEMU emulation container."""
    from iris.emulate.orchestrator import stop_emulation

    ok = stop_emulation(iid)
    (out.info if ok else err.error)(f"stopped container {iid}: {ok}")


@guest_app.command("ls")
def guest_ls(
    iid: int = typer.Argument(..., help="image ID of the emulation container"),
    path: str = typer.Argument("/", help="absolute path inside the guest"),
    disk: str = typer.Option(
        "state", "--disk", help="`state` = what the guest booted from and wrote to; "
                                "`image` = the baked image, never attached to QEMU",
    ),
) -> None:
    """List a directory inside the guest's filesystem.

    The guest's root filesystem is a block image inside the emulation container and
    nothing in that container mounts it, so `docker exec` cannot see a single guest
    file. This mounts the image on a loop device instead -- the same operation the
    image build performs -- which is the first channel that can look at the guest at
    all rather than inferring it from serial logs.

    `--disk state` needs the emulation stopped: QEMU has that disk open read-write,
    and mounting it underneath would corrupt it.
    """
    from iris.emulate.guestfs import GuestError, guest_path, list_guest

    # Normalised once here so the recovery is reported once and the message shows
    # the path the guest actually has.
    shown = guest_path(path)
    try:
        names = list_guest(f"iris-qemu-{iid}", iid, shown, disk=disk)
    except GuestError as exc:
        err.error(str(exc))
        raise typer.Exit(code=1) from exc
    out.block("info", f"{len(names)} entr{'y' if len(names) == 1 else 'ies'} "
                      f"in {shown} ({disk} disk, iid {iid})", names)


@guest_app.command("get")
def guest_get(
    iid: int = typer.Argument(..., help="image ID of the emulation container"),
    path: str = typer.Argument(..., help="absolute path of a file inside the guest"),
    dest: Path = typer.Option(..., "--out", help="where to write it on the host"),
    disk: str = typer.Option("state", "--disk", help="`state` or `image`"),
) -> None:
    """Copy a file out of the guest's filesystem.

    The evidence channel: a claim about what the guest contains can now be checked
    against the guest instead of against a serial log line that may well be
    describing something else.
    """
    from iris.emulate.guestfs import GuestError, guest_path, pull_guest_file

    try:
        written = pull_guest_file(f"iris-qemu-{iid}", iid, guest_path(path), dest, disk=disk)
    except GuestError as exc:
        err.error(str(exc))
        raise typer.Exit(code=1) from exc
    out.info(f"wrote {written} ({written.stat().st_size} bytes)")


@guest_app.command("put")
def guest_put(
    iid: int = typer.Argument(..., help="image ID of the emulation container"),
    source: Path = typer.Argument(..., help="file on the host"),
    path: str = typer.Argument(..., help="absolute path to write inside the guest"),
    mode: str = typer.Option(None, "--mode", help="octal mode to set in the guest, e.g. 755"),
    disk: str = typer.Option("state", "--disk", help="`state` or `image`"),
) -> None:
    """Copy a file into the guest's filesystem.

    This is the write half of the only channel that reaches the guest, so it is also
    the only way to put a repair *into* a guest rather than waiting for one of its own
    daemons to perform it. Nothing applies one automatically: the evidence that a
    repair works is the guest running with it, and that is a decision, not a side
    effect of an earlier run.

    Needs the emulation stopped, and `--disk state` is what makes the change survive
    the next boot: `image` writes the baked image, which the next `make_image.sh`
    replaces.
    """
    from iris.emulate.guestfs import GuestError, guest_path, push_guest_file

    shown = guest_path(path)
    try:
        push_guest_file(f"iris-qemu-{iid}", iid, source, shown, disk=disk, mode=mode)
    except GuestError as exc:
        err.error(str(exc))
        raise typer.Exit(code=1) from exc
    out.info(f"put {source} at {shown} (iid {iid}, {disk} disk)")


@guest_app.command("reset")
def guest_reset(
    iid: int = typer.Argument(..., help="image ID of the emulation container"),
) -> None:
    """Discard the guest's state disk so the next launch boots the baked image.

    The undo for `guest put --disk state`, and the way out of a state disk left
    inconsistent by a hard kill.
    """
    from iris.emulate.guestfs import GuestError, reset_guest_state

    try:
        removed = reset_guest_state(f"iris-qemu-{iid}", iid)
    except GuestError as exc:
        err.error(str(exc))
        raise typer.Exit(code=1) from exc
    if removed:
        out.info(f"state disk of iid {iid} discarded; the next launch boots the baked image")
    else:
        out.info(f"iid {iid} has no state disk; nothing to discard")


@emulate_app.command("list")
def emulate_list() -> None:
    """List all IRIS QEMU emulation containers with status and ports."""
    try:
        # Use docker format string for consistent output
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=iris-qemu",
             "--format", "{{.Names}}|{{.Status}}|{{.Ports}}"],
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8",
            errors="ignore"
        )

        if not result.stdout.strip():
            out.info("no IRIS emulation containers found")
            return

        # The column header and its rule stay inside the block so the columns
        # below them are the only lines carrying a timestamp, and therefore the
        # only ones whose widths have to agree.
        rows = [f"{'CONTAINER':<30} {'STATUS':<35} {'PORTS'}", "-" * 95]
        tints: list[str | None] = [None, None]
        for line in result.stdout.strip().split("\n"):
            parts = line.split("|")
            if len(parts) < 3:
                continue
            name, status, ports = parts[0], parts[1][:35], parts[2]
            rows.append(f"{name:<30} {status:<35} {ports}")
            # "Up" is the only state that means the guest is serving; created,
            # restarting and exited are all equally not-ready, and the tint is
            # what makes that readable without reading every status string.
            tints.append(LEVEL_COLORS["info"] if "Up" in status else LEVEL_COLORS["warn"])
        out.block("info", f"IRIS emulation containers ({len(rows) - 2})", rows, row_color=tints)

    except subprocess.CalledProcessError as e:
        err.error(f"failed to list containers: {e}")
        raise typer.Exit(code=1) from None
    except OSError as e:
        # No JSON parsing happens above, so this is only ever "docker is missing
        # or not runnable" — say that instead of leaking a traceback.
        err.error(f"error listing containers: {e}")
        raise typer.Exit(code=1) from None


@emulate_app.command("status")
def emulate_status(iid: int = typer.Argument(..., help="image ID to inspect")) -> None:
    """Display detailed status of an IRIS QEMU emulation container."""

    settings = get_settings()
    scratch_dir = settings.scratch_dir

    # Check if container exists
    try:
        result = subprocess.run(
            ["docker", "inspect", f"iris-qemu-{iid}"],
            capture_output=True, text=True, check=True
        )
    except subprocess.CalledProcessError:
        err.error(f"container iris-qemu-{iid} not found")
        raise typer.Exit(code=1) from None
    except OSError as e:
        err.error(f"docker is unavailable: {e}")
        raise typer.Exit(code=1) from None

    # check=True already guarantees a non-empty result for an existing container,
    # but an empty list here means the id vanished between the two calls.
    inspected = json.loads(result.stdout)
    if not inspected:
        err.error(f"container iris-qemu-{iid} not found")
        raise typer.Exit(code=1)
    info = inspected[0]

    # Extract key information
    container_name = info["Name"].lstrip("/")
    state = info["State"]["Status"]
    created = info["Created"]
    started_at = info["State"].get("StartedAt", "N/A")
    finished_at = info["State"].get("FinishedAt", "N/A")

    # Get port mappings
    ports = info.get("NetworkSettings", {}).get("Ports", {})
    port_info = []
    for container_port, mapping in ports.items():
        if mapping:
            for m in mapping:
                host_ip = m.get("HostIp", "0.0.0.0")
                host_port = m.get("HostPort", "")
                if host_port:
                    # A wildcard bind is the common case and adds nothing here;
                    # a specific address does, because it decides where to click.
                    origin = host_port if host_ip in ("0.0.0.0", "::", "") \
                        else f"{host_ip}:{host_port}"
                    port_info.append(f"{origin} → {container_port}")
        else:
            port_info.append(f"{container_port} (no mapping)")

    # Get network info
    networks = info.get("NetworkSettings", {}).get("Networks", {})
    ip_address = ""
    network_names = []
    for net_name, net_info in networks.items():
        network_names.append(net_name)
        if net_info.get("IPAddress"):
            ip_address = net_info["IPAddress"]

    # One block per report section instead of a `====` frame with blank lines:
    # the timestamped header is already the visual separator, and blank lines in
    # a redirected log are just noise.
    pairs = [
        ("State", state),
        ("Created", created),
        ("Started At", started_at),
    ]
    if state == "exited":
        pairs.append(("Finished At", finished_at))
    if port_info:
        pairs.append(("Ports", ", ".join(port_info)))
    if ip_address:
        pairs.append(("IP Address", ip_address))
        pairs.append(("Networks", ", ".join(network_names)))
    scratch_path = scratch_dir / str(iid)
    if scratch_path.exists():
        size_gb = sum(safe_stat_size(f) for f in scratch_path.rglob("*") if safe_is_file(f)) / (1024**3)
        pairs.append(("Scratch", f"{scratch_path.resolve()} ({size_gb:.2f} GB)"))
    out.block("info", f"container {container_name}", _rows(pairs))

    # Check web service accessibility
    web_ports = [80, 8080, 8000, 443]
    accessible_ports = []
    for p in web_ports:
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(("127.0.0.1", p))
            sock.close()
            if result == 0:
                accessible_ports.append(p)
        except OSError:
            # An unbindable or filtered port is just "not reachable", not an error.
            pass

    if accessible_ports:
        out.info(f"web services accessible on ports: {', '.join(map(str, accessible_ports))}")
    elif state == "running":
        # Running but not serving is the failure worth flagging: the container
        # looks healthy to `docker ps` while the guest never brought up a web port.
        err.warning(f"no web services detected on standard ports ({'/'.join(map(str, web_ports))}) "
                    f"while container state is running")


@serve_app.command("start")
def serve_start(
    host: str = typer.Option(
        "127.0.0.1",
        help="bind address; 0.0.0.0 exposes the API to the network and requires a token",
    ),
    port: int = typer.Option(9000, help="API server port"),
    reload: bool = typer.Option(False, help="auto-reload on code changes"),
    api_token: str = typer.Option(
        "",
        help="API token; falls back to $IRIS_API_TOKEN, and is mandatory for non-loopback binds",
    ),
) -> None:
    """Start the IRIS FastAPI orchestration server."""
    import uvicorn

    from iris.api.auth import configured_token, is_loopback_host

    token = api_token.strip() or configured_token()
    if not is_loopback_host(host) and not token:
        # Fail closed. The API can upload a file, start a container and stop one
        # by id; binding it to every interface without a token hands all three to
        # everyone on the LAN, which is how the server shipped with a default
        # nobody had opted into.
        err.warning(
            f"refusing to bind {host}: no API token configured. "
            "Set IRIS_API_TOKEN, pass --api-token, or bind a loopback address."
        )
        raise typer.Exit(code=2)

    bind_note = " (token required)" if token else " (local mode: no token)"
    out.info(f"IRIS API server starting on {host}:{port}{bind_note}")
    out.info(f"docs: http://{host}:{port}/docs")
    uvicorn.run("iris.api.server:app", host=host, port=port, reload=reload)


@app.command("web")
def web_workbench(
    host: str = typer.Option(
        "127.0.0.1",
        help="bind address; 0.0.0.0 exposes the workbench to the network and requires a token",
    ),
    port: int = typer.Option(9000, help="workbench port"),
    api_token: str = typer.Option(
        "",
        help="API token; falls back to $IRIS_API_TOKEN, and is mandatory for non-loopback binds",
    ),
    no_browser: bool = typer.Option(False, "--no-browser", help="do not open a browser on startup"),
    reload: bool = typer.Option(False, help="auto-reload Python changes (the frontend is not rebuilt)"),
) -> None:
    """Start the IRIS firmware emulation workbench and open it in a browser.

    One command, no arguments, no configuration file: the point is that a judge can
    see the thing working. Everything it needs comes from the same .env and IRIS_*
    environment the rest of the CLI already reads, so this command adds no settings
    of its own -- a second place to configure the same server is a second thing to
    get wrong.

    Stops with Ctrl+C. The shutdown handler installed by the web assembly tells open
    consoles the server is going away, closes their sockets, then stops every
    instance this process is hosting, so quitting does not leave privileged
    containers running with no way to stop them from a UI that no longer exists.
    """
    import uvicorn

    from iris.api import web_app as web_assembly
    from iris.api.auth import configured_token, is_loopback_host
    from iris.api.server import app as api_app

    token = api_token.strip() or configured_token()
    if not is_loopback_host(host) and not token:
        # Same gate as ``iris serve start``, and for the same reason: this process
        # can upload a firmware, start a privileged container and stop one by id.
        # A workbench is the more dangerous of the two to expose, because it is
        # the one a person is tempted to bind to 0.0.0.0 to show a colleague.
        err.warning(
            f"refusing to bind {host}: no API token configured. "
            "Set IRIS_API_TOKEN, pass --api-token, or bind a loopback address."
        )
        raise typer.Exit(code=2)

    if api_token.strip():
        # Exported, not just passed in-process: ``--reload`` re-imports the app in
        # a child process, and a token that only lived in this one would leave the
        # worker serving unauthenticated. ``setdefault`` semantics would be wrong
        # here -- an explicit flag has to win over an inherited value.
        os.environ["IRIS_API_TOKEN"] = api_token.strip()

    web_assembly.install(api_app)
    browser_url = f"http://{_browser_host(host)}:{port}/"
    out.block("info", "IRIS workbench starting", _rows(_web_banner_rows(host, port, token, browser_url)))
    if not (web_assembly.dist_dir() / "index.html").is_file():
        err.warning(
            f"frontend not built at {web_assembly.dist_dir()} - the API works but "
            "the pages will answer 503. Build it with: cd web && npm install && npm run build"
        )
    if not no_browser:
        _open_browser_soon(browser_url)
    out.info("press Ctrl+C to stop; running instances will be stopped and their consoles closed")

    if reload:
        # uvicorn refuses to reload an application object (it re-executes an import
        # string in a child process), so the reload path goes through the factory.
        uvicorn.run(_WEB_APP_TARGET, host=host, port=port, reload=True)
    else:
        # The object, not the import string: ``install`` has already mutated it, and
        # a string would hand uvicorn the un-installed app and a 404 for every page.
        uvicorn.run(api_app, host=host, port=port)


#: Import-string target for ``iris web --reload``. Resolved by path, so it has to
#: live at module scope in a module uvicorn's child process can import.
_WEB_APP_TARGET = "iris.cli:_web_app"


def _web_app():
    """The assembled app, for ``iris web --reload``'s worker process."""
    from iris.api.server import app as api_app
    from iris.api.web_app import install

    return install(api_app)


def _browser_host(host: str) -> str:
    """The address to put in a browser for a server bound to ``host``.

    ``0.0.0.0`` is a bind address, not a destination: opening it works on some
    machines and refuses to connect on others, and the wildcard listener is
    reachable at loopback anyway.
    """
    return "127.0.0.1" if host.strip() in {"0.0.0.0", "::", "*"} else host.strip()


def _open_browser_soon(url: str, delay: float = 1.2) -> None:
    """Open ``url`` after the listener is up.

    On a timer rather than before ``uvicorn.run``: opening first is a race the
    browser usually loses, and the user sees a connection error on a server that is
    about to work. Daemon so an exit during the delay cannot hold the process open.
    """
    import threading
    import webbrowser

    def open_it() -> None:
        try:
            webbrowser.open(url)
        except Exception as exc:  # noqa: BLE001 - a browser is never worth a traceback
            err.warning(f"could not open a browser at {url}: {exc}; open it manually")

    timer = threading.Timer(delay, open_it)
    timer.daemon = True
    timer.start()


def _web_banner_rows(host: str, port: int, token: str, browser_url: str) -> list[tuple[str, str]]:
    """What the operator needs to know before the first page loads."""
    from iris import __version__
    from iris.api.web_app import redact_database_url
    from iris.api.web_data import serial_console_available
    from iris.config import get_settings

    settings = get_settings()
    console = serial_console_available()
    return [
        ("version", __version__),
        ("mode", "token required" if token else "local mode (no token, loopback only)"),
        ("workbench", browser_url),
        # FastAPI mounts its OpenAPI UI at /docs and it survives the SPA catch-all,
        # because those routes are registered when the app is created.
        ("api", f"http://{host}:{port}/api/v1  (docs at /docs)"),
        ("console", "interactive serial console available" if console
         else "serial is one-way in this build (run_qemu.sh has no chardev socket)"),
        ("database", redact_database_url(settings.database_url)),
        ("scratch", str(settings.scratch_dir)),
    ]


@emulate_app.command("guardian-start")
def emulate_guardian_start(
    iid: int = typer.Argument(..., help="container image ID to monitor"),
    interval: int = typer.Option(30, "--interval", help="health check interval in seconds"),
    probe_port: int = typer.Option(
        0, "--probe-port",
        help="host port to HTTP-probe for the web server (0 = serial log only)",
    ),
    restart_verify: int = typer.Option(
        120, "--restart-verify",
        help="seconds a WEB_SERVER_RESTART gets to bring the probe port back",
    ),
) -> None:
    """Start AI Guardian for continuous container health monitoring and self-healing."""
    from iris.config import get_settings
    from iris.monitor.ai_guardian import LEDGER_FILENAME, AIHealthMonitor

    settings = get_settings()
    ledger_path = settings.scratch_dir / LEDGER_FILENAME
    out.block("info", f"starting AI Guardian for container {iid}", _rows([
        ("health check", f"every {interval}s"),
        ("http probe", f"http://127.0.0.1:{probe_port} (failure overrides serial log)"
         if probe_port else "disabled (serial log only)"),
        ("action ledger", str(ledger_path)),
    ]))
    out.info("press Ctrl+C to stop monitoring")

    guardian = AIHealthMonitor(
        iid=iid, scratch_dir=settings.scratch_dir,
        http_probe_port=probe_port, restart_verify_seconds=restart_verify,
        ledger_path=ledger_path,
    )

    try:
        guardian.start_continuous_monitoring(check_interval=interval)
    except KeyboardInterrupt:
        out.info("monitoring stopped by user")


@emulate_app.command("guardian-log")
def emulate_guardian_log(
    iid: int = typer.Option(None, "--iid", help="filter by container image ID"),
    limit: int = typer.Option(20, "--limit", help="max entries to show"),
) -> None:
    """Show recent AI Guardian actions and diagnoses from the ledger."""
    from iris.config import get_settings
    from iris.monitor.ai_guardian import LEDGER_FILENAME
    from iris.monitor.ledger import GuardianLedger

    settings = get_settings()
    ledger_path = settings.scratch_dir / LEDGER_FILENAME
    if not ledger_path.is_file():
        err.warning(f"no ledger at {ledger_path} — guardian-start has not run yet")
        raise typer.Exit(code=1)

    ledger = GuardianLedger(ledger_path)
    entries = ledger.recent(iid=iid, limit=limit)
    ledger.close()
    if not entries:
        out.info("ledger is empty")
        return

    rows = [f"{'ID':<6} {'IID':<8} {'TIMESTAMP':<21} {'ACTION':<24} {'KIND':<10} OK", "-" * 84]
    tints: list[str | None] = [None, None]
    for e in entries:
        mark = "yes" if e["success"] else "NO"
        rows.append(
            f"{e['id']:<6} {e['iid']:<8} {e['ts']:<21} {e['action']:<24} {e['kind']:<10} {mark}"
        )
        tints.append(LEVEL_COLORS["info"] if e["success"] else LEVEL_COLORS["error"])
    out.block("info", f"guardian action ledger ({len(entries)} entries)", rows, row_color=tints)

    promoted = sum(1 for e in entries if e["promoted"])
    if promoted:
        out.info(f"{promoted} of the shown entries have been promoted to deterministic rules")


if __name__ == "__main__":
    main()
