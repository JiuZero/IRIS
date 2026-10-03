"""Which emulations this service is hosting right now, and who owns each one.

``iris.api.server`` kept that answer in a module-level ``dict``. Three things a
product cannot accept follow from that: the list is empty after a restart even
though the containers are still up, two API workers disagree about it, and
``DELETE /emulate/{iid}`` stops whoever's container the id names because nothing
recorded an owner.

So the live set lives in ``active_emulation`` (see :class:`iris.db.models.ActiveEmulation`)
and this module is the only thing that writes it. The rule it enforces is narrow
and absolute: **a row can only be read or stopped by the client that created
it**. Callers that want "all my emulations" pass their own client id and get
exactly that -- no route can ask for someone else's.

Reconciliation is what makes a restart useful rather than merely possible. A row
whose container is gone is not an emulation, it is a leftover, and reporting it
would send the operator to ``docker ps`` to find out. `reconcile` drops those, and
reports what it dropped so the caller can say so.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from iris.db.models import ActiveEmulation
from iris.log import get_logger

logger = get_logger(__name__)

__all__ = [
    "ActiveRecord",
    "container_alive",
    "list_owned",
    "reconcile",
    "register",
    "release",
    "to_dict",
]


@dataclass(frozen=True)
class ActiveRecord:
    """One hosted emulation, as returned to callers."""

    iid: int
    client_id: str
    arch: str
    container_id: str
    rootfs_path: str
    web_url: str
    success: bool
    web_ok: bool
    started_at: str


def to_dict(row: ActiveEmulation) -> ActiveRecord:
    started = row.started_at or datetime.now(UTC).replace(tzinfo=None)
    return ActiveRecord(
        iid=row.iid,
        client_id=row.client_id,
        arch=row.arch or "",
        container_id=row.container_id or "",
        rootfs_path=row.rootfs_path or "",
        web_url=row.web_url or "",
        success=bool(row.success),
        web_ok=bool(row.web_ok),
        started_at=started.isoformat(),
    )


def register(
    session: Session,
    *,
    iid: int,
    client_id: str,
    arch: str,
    rootfs_path: str,
    container_id: str,
    web_url: str = "",
    success: bool = False,
    web_ok: bool = False,
) -> ActiveRecord:
    """Record a run this service is now hosting, replacing any row for that iid.

    Replaces instead of failing on the unique constraint because the orchestrator
    reuses the iid for the same rootfs across requests; the newest container is
    the one that exists, so the newest row is the true one.
    """
    existing = session.scalar(select(ActiveEmulation).where(ActiveEmulation.iid == iid))
    if existing is not None:
        session.delete(existing)
        session.flush()
    row = ActiveEmulation(
        iid=iid,
        client_id=client_id,
        arch=arch,
        container_id=container_id,
        rootfs_path=str(rootfs_path),
        web_url=web_url,
        success=success,
        web_ok=web_ok,
        started_at=datetime.now(UTC).replace(tzinfo=None),
    )
    session.add(row)
    session.commit()
    return to_dict(row)


def list_owned(session: Session, client_id: str) -> list[ActiveRecord]:
    """Every emulation belonging to ``client_id``, oldest first."""
    rows = session.scalars(
        select(ActiveEmulation)
        .where(ActiveEmulation.client_id == client_id)
        .order_by(ActiveEmulation.iid)
    )
    return [to_dict(row) for row in rows]


def release(session: Session, *, iid: int, client_id: str) -> ActiveRecord | None:
    """Remove and return the row for ``iid`` **only if** ``client_id`` owns it.

    Returns None when the row does not exist or belongs to somebody else; the
    caller turns that into the same 404 either way, because "this id exists but
    is not yours" is information the API should not hand out.
    """
    row = session.scalar(select(ActiveEmulation).where(ActiveEmulation.iid == iid))
    if row is None or row.client_id != client_id:
        return None
    record = to_dict(row)
    session.delete(row)
    session.commit()
    return record


def container_alive(container_id: str) -> bool | None:
    """Whether docker still has this container -- running *or* exited.

    Three answers, not two. ``True``/``False`` mean docker was asked and answered;
    ``None`` means the question could not be asked (docker missing, probe timed
    out). Collapsing that third case into ``False`` looks conservative and is not:
    :func:`reconcile` deletes on ``False``, so a five-second docker hiccup would
    erase the record of a container that is still running, and the caller would
    lose the ability to stop it.

    An exited container counts as alive: its serial log is the evidence a failure
    diagnosis needs, so "not running" is not the same as "gone".
    """
    if not container_id:
        return False
    from iris.emulate.orchestrator import container_exists

    try:
        return container_exists(container_id)
    except Exception as exc:  # noqa: BLE001 - a probe must not break listing
        logger.debug(f"container probe failed for {container_id}: {exc}")
        return None



def reconcile(
    session: Session,
    *,
    alive: Callable[[str], bool | None] | None = None,
    keep: Iterable[int] = (),
) -> list[int]:
    """Drop rows whose container is confirmed gone; return the iids dropped.

    ``alive`` is injectable so this can be tested without docker -- and so the
    policy ("gone means gone") is testable separately from the probe. It defaults
    to ``None`` and resolves the probe *inside* the body on purpose: a default of
    ``alive=container_alive`` binds the function object at definition time, so
    monkeypatching the module attribute would silently do nothing and every test
    would shell out to docker for real.

    Only a confirmed ``False`` deletes. ``None`` -- the probe could not run --
    keeps the row, because losing the ability to stop a running container is worse
    than showing one that has already exited.

    ``keep`` spares ids the caller already knows are alive, which avoids a probe
    per row on the common path where the caller just listed the containers.
    """
    probe = alive if alive is not None else container_alive
    spared = set(keep)
    rows = list(session.scalars(select(ActiveEmulation)))
    dropped: list[int] = []
    for row in rows:
        if row.iid in spared:
            continue
        if probe(row.container_id or "") is not False:
            continue
        dropped.append(row.iid)
        session.delete(row)
    if dropped:
        session.commit()
        logger.info(f"dropped {len(dropped)} stale emulation row(s): {dropped}")
    return dropped