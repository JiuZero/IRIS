"""值守动作账本（guardian action ledger）。

每次恢复动作与诊断都落一行 SQLite，供事后审计与 P3"修复沉淀回规则"消费。
刻意不挂在 ``emulation_run`` 下：run_id 要 orchestrator 全链路接库才存在，
现在强行归属会把观测层焊死在编排层上。字段与 ``db.models.RepairAction``
对齐（source/rule_id/evidence/applied/promoted），迁移时是列改名而非重新设计。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from iris.log import get_logger

logger = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS guardian_action (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    iid         INTEGER NOT NULL,
    ts          TEXT    NOT NULL,
    action      TEXT    NOT NULL,
    kind        TEXT    NOT NULL,           -- repair / diagnosis
    success     INTEGER NOT NULL,
    detail      TEXT,
    evidence    TEXT,
    promoted    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_guardian_action_iid ON guardian_action(iid);
"""


class GuardianLedger:
    """Append-only record of every recovery action and diagnosis."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def record(
        self, iid: int, action: str, kind: str, success: bool,
        detail: str = "", evidence: str = "",
    ) -> None:
        """Append one entry; a ledger failure must never break the watch loop."""
        try:
            self._conn.execute(
                "INSERT INTO guardian_action (iid, ts, action, kind, success, detail, evidence)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    iid, datetime.now(UTC).isoformat(timespec="seconds"),
                    action, kind, 1 if success else 0, detail, evidence,
                ),
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            logger.warning(f"ledger write failed ({self.path}): {exc}")

    def recent(self, iid: int | None = None, limit: int = 20) -> list[dict]:
        """Newest-first entries, optionally filtered by container."""
        sql = ("SELECT id, iid, ts, action, kind, success, detail, evidence, promoted"
               " FROM guardian_action")
        params: tuple = ()
        if iid is not None:
            sql += " WHERE iid = ?"
            params = (iid,)
        sql += " ORDER BY id DESC LIMIT ?"
        rows = self._conn.execute(sql, (*params, limit)).fetchall()
        return [
            {
                "id": r[0], "iid": r[1], "ts": r[2], "action": r[3], "kind": r[4],
                "success": bool(r[5]), "detail": r[6], "evidence": r[7], "promoted": bool(r[8]),
            }
            for r in rows
        ]

    def mark_promoted(self, entry_id: int, promoted: bool = True) -> bool:
        """Flip the promoted flag once a repair became a deterministic rule."""
        cur = self._conn.execute(
            "UPDATE guardian_action SET promoted = ? WHERE id = ?",
            (1 if promoted else 0, entry_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def export_json(self, iid: int | None = None, limit: int = 20) -> str:
        return json.dumps(self.recent(iid=iid, limit=limit), ensure_ascii=False, indent=2)

    def close(self) -> None:
        self._conn.close()