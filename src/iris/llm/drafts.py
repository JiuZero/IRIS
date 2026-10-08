"""Where a model-authored rule draft waits for a human, and how it becomes real.

Two doors, in order, and neither can be skipped:

* the **draft directory** (``iris_home/ai-drafts``) is kept apart from
  ``plugin_dir`` for the same reason ``plugin_dir`` is kept out of ``rules_dir``:
  the engine loads everything in the plugin directory on the next run, and a
  hallucinated document must never reach that far without a person accepting it.
* the **install** path is not a copy: it goes through
  :func:`iris.api.plugins.install_plugin`, the same validation chain a browser
  upload takes (size, name, YAML, whitelists, id collisions, zero warnings on
  re-load). A draft the engine would not accept from a person is not accepted
  from a model either -- that symmetry is the whole point of reusing the chain.

Every draft carries the metadata a review needs: which instance produced it,
what the model claimed, and when. A draft without its provenance is a document
nobody can audit, and an audit trail is the only thing standing between "AI
wrote a plugin" and "AI silently changed how every future run is repaired".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from iris.api.plugins import PluginRejected, install_plugin
from iris.config import get_settings
from iris.llm.schema import PluginDraft
from iris.log import get_logger

logger = get_logger(__name__)

__all__ = [
    "DRAFT_STATUS_ACCEPTED",
    "DRAFT_STATUS_PENDING",
    "DRAFT_STATUS_REJECTED",
    "DraftRecord",
    "install_draft",
    "list_drafts",
    "load_draft",
    "reject_draft",
    "save_draft",
]


DRAFT_STATUS_PENDING = "pending"
DRAFT_STATUS_ACCEPTED = "accepted"
DRAFT_STATUS_REJECTED = "rejected"


@dataclass(frozen=True)
class DraftRecord:
    """One draft on disk, with the provenance a review reads before deciding."""

    rule_id: str
    stage: str
    description: str
    status: str
    created_at: str
    source_iid: int | None
    confidence: float
    diagnosis: str


def drafts_dir() -> Path:
    return get_settings().llm_draft_dir


def save_draft(
    draft: PluginDraft, *, iid: int | None, confidence: float, diagnosis: str
) -> DraftRecord:
    """Write one draft document plus its metadata into the draft directory.

    Both files land together or neither does: the metadata is what makes the
    YAML reviewable, and a YAML alone would be a proposal nobody can trace back
    to the run that produced it. An id that collides with a draft already
    waiting is refused rather than overwritten -- overwriting would silently
    replace a review that was still pending.
    """
    if not draft.rule_id.strip():
        raise PluginRejected("草案缺少 rule_id")
    directory = drafts_dir()
    directory.mkdir(parents=True, exist_ok=True)
    yaml_path = directory / f"{draft.rule_id}.yaml"
    if yaml_path.exists():
        raise PluginRejected(f"草案 {draft.rule_id} 已在等待审核,不能覆盖")

    now = datetime.now(UTC).isoformat(timespec="seconds")
    meta = {
        "rule_id": draft.rule_id,
        "stage": draft.stage,
        "description": draft.description,
        "status": DRAFT_STATUS_PENDING,
        "created_at": now,
        "source_iid": iid,
        "confidence": confidence,
        "diagnosis": diagnosis,
    }
    yaml_path.write_text(draft.yaml, encoding="utf-8", newline="\n")
    try:
        (directory / f"{draft.rule_id}.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8", newline="\n",
        )
    except OSError:
        yaml_path.unlink(missing_ok=True)
        raise
    logger.info(f"saved rule draft {draft.rule_id!r} from instance {iid}")
    return DraftRecord(**meta)


def list_drafts() -> list[DraftRecord]:
    """Every draft on disk, newest first. An empty directory is a real answer.

    Drafts whose YAML is gone but whose metadata remains are dropped rather
    than half-listed: a record without its document cannot be reviewed, and
    listing one would hand back an install button that 404s.
    """
    directory = drafts_dir()
    if not directory.is_dir():
        return []
    records: list[DraftRecord] = []
    for meta_path in directory.glob("*.meta.json"):
        rule_id = meta_path.name.removesuffix(".meta.json")
        if not (directory / f"{rule_id}.yaml").is_file():
            logger.warning(f"draft metadata without document, dropped: {meta_path.name}")
            meta_path.unlink(missing_ok=True)
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            records.append(DraftRecord(**meta))
        except (OSError, ValueError, TypeError) as exc:
            logger.warning(f"unreadable draft metadata {meta_path.name}: {exc}")
    records.sort(key=lambda record: record.created_at, reverse=True)
    return records


def load_draft(rule_id: str) -> tuple[PluginDraft, DraftRecord]:
    """One draft's document and metadata, for the review and the install."""
    directory = drafts_dir()
    yaml_path = directory / f"{rule_id}.yaml"
    meta_path = directory / f"{rule_id}.meta.json"
    if not yaml_path.is_file() or not meta_path.is_file():
        raise PluginRejected(f"草案 {rule_id} 不存在")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    record = DraftRecord(**meta)
    text = yaml_path.read_text(encoding="utf-8")
    draft = PluginDraft(
        rule_id=record.rule_id, stage=record.stage,
        description=record.description, yaml=text,
    )
    return draft, record


def install_draft(rule_id: str) -> dict[str, Any]:
    """Accept one draft: run it through the same validation chain as an upload.

    The document goes to the plugin directory only if the engine accepts it
    whole. On success the draft is marked accepted (its document and metadata
    stay, because the provenance is the audit trail for what is now an
    installed plugin), and when the source run can be located the repair is
    recorded in ``repair_action`` with ``promoted`` -- that row is what makes
    "AI wrote a rule" readable from the workbench's repair ledger.
    """
    draft, record = load_draft(rule_id)
    installed = install_plugin(f"{draft.rule_id}.yaml", draft.yaml.encode("utf-8"))
    _mark_status(rule_id, DRAFT_STATUS_ACCEPTED)
    recorded = _record_promoted_repair(record)
    logger.info(f"draft {rule_id!r} installed, promoted-repair recorded={recorded}")
    return {"installed": installed, "promoted_recorded": recorded}


def reject_draft(rule_id: str) -> Path:
    """Refuse one draft: its document is removed, its metadata stays behind as
    the record of a review that happened and said no.

    Only a ``pending`` draft can be refused. A document that is already gone is
    refused too rather than reported as removed again -- the leftover metadata is what
    makes a stale page's second press a 404, because saying "removed" twice would
    credit two operators with one refusal. And an ``accepted`` draft is refused
    because refusing it would report "已拒绝" about a document whose plugin is
    *installed and running*: the review record would then contradict what the engine
    does on the next run, which is the one thing this metadata exists to prevent.
    """
    directory = drafts_dir()
    yaml_path = directory / f"{rule_id}.yaml"

    if not yaml_path.is_file():
        raise PluginRejected(f"草案 {rule_id} 不存在")
    if _status_of(rule_id) != DRAFT_STATUS_PENDING:
        raise PluginRejected(
            f"草案 {rule_id} 已处理过（{_status_of(rule_id)}），不能拒绝；"
            f"已接受的草案其插件已在插件目录生效，要撤销请用插件页的卸载"
        )
    yaml_path.unlink()
    _mark_status(rule_id, DRAFT_STATUS_REJECTED)
    logger.info(f"draft {rule_id!r} rejected")
    return yaml_path


def _status_of(rule_id: str) -> str | None:
    """One draft's recorded status, or ``None`` when the metadata is unreadable.

    An unreadable metadata reads as "not pending" on purpose: refusing is the
    destructive direction, and a draft nobody can read the state of is not one to
    delete on a guess.
    """
    meta_path = drafts_dir() / f"{rule_id}.meta.json"
    if not meta_path.is_file():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8")).get("status")
    except (OSError, ValueError):
        return None


def _mark_status(rule_id: str, status: str) -> None:
    """Rewrite one draft's status in place. Best-effort bookkeeping."""
    meta_path = drafts_dir() / f"{rule_id}.meta.json"
    if not meta_path.is_file():
        return
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["status"] = status
        meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8", newline="\n",
        )
    except (OSError, ValueError):
        logger.warning(f"could not mark draft {rule_id!r} as {status}")


def _record_promoted_repair(record: DraftRecord) -> bool:
    """One ``repair_action(source="llm", promoted=True)`` row, when the run that
    produced the draft can still be located.

    ``emulation_run.iid`` holds the scratch run number, which is what the draft's
    ``source_iid`` is. A run that was cleared since leaves no parent, and the
    repair is then not recorded -- the draft's metadata carries the provenance
    either way, and a missing row is visible rather than wrong.
    """
    if record.source_iid is None:
        return False
    try:
        from sqlalchemy import select

        from iris.db.engine import get_engine, make_session
        from iris.db.models import EmulationRun, RepairAction

        settings = get_settings()
        engine = get_engine(settings.database_url)
        with make_session(engine) as session:
            run = session.scalars(
                select(EmulationRun)
                .where(EmulationRun.iid == record.source_iid)
                .order_by(EmulationRun.id.desc())
            ).first()
            if run is None:
                return False
            session.add(RepairAction(
                run_id=run.id,
                source="llm",
                rule_id=record.rule_id,
                evidence=f"AI 草案,置信度 {record.confidence},{record.diagnosis[:300]}",
                applied=True,
                promoted=True,
            ))
            session.commit()
        return True
    except Exception as exc:  # noqa: BLE001 - bookkeeping must not fail an install
        logger.warning(f"promoted-repair record failed for draft {record.rule_id}: {exc}")
        return False