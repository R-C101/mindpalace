"""
Append-only audit log.

Every mutation that changes durable state writes one row to ``audit_log``
and one JSONL line to ``data/audit.log``. The DB table is queryable via the
``audit_log`` MCP tool; the JSONL file is the redundancy belt — it survives
DB corruption and is friendly to ``tail -f`` while debugging.

Snapshots are taken with ``snapshot(entity)`` *before* and *after* the
mutation and stored as JSON. They're tiny (the ORM rows are small) and
priceless for the planned ``undo`` tool in v1.1.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional, Union

from sqlalchemy.orm import Session

from mindpalace.config import AUDIT_LOG_PATH
from mindpalace.db.models import AuditLog, Container, Item


def snapshot(entity: Optional[Union[Container, Item]]) -> Optional[dict[str, Any]]:
    """Capture a JSON-friendly snapshot of an entity's mutable state."""
    if entity is None:
        return None
    common = {
        "id": entity.id,
        "name": entity.name,
        "display_name": entity.display_name,
        "canonical_name": entity.canonical_name,
        "path": entity.path,
        "is_locked": entity.is_locked,
        "description": entity.description,
        "version": entity.version,
        "deleted_at": entity.deleted_at.isoformat() if entity.deleted_at else None,
        "last_seen_at": entity.last_seen_at.isoformat() if entity.last_seen_at else None,
    }
    if isinstance(entity, Container):
        common.update(
            depth=entity.depth, parent_id=entity.parent_id,
            container_type=entity.container_type,
        )
    else:
        common.update(
            container_id=entity.container_id, quantity=entity.quantity,
            item_type=entity.item_type,
        )
    return common


def record(
    session: Session,
    *,
    tool_name: str,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    args: Optional[dict[str, Any]] = None,
    before: Optional[dict[str, Any]] = None,
    after: Optional[dict[str, Any]] = None,
    result_summary: Optional[str] = None,
) -> AuditLog:
    """
    Write one audit entry. Caller is responsible for committing the
    surrounding transaction; this only ``session.add()``s the row so it
    flushes alongside the mutation it describes.

    The JSONL mirror is written immediately (best-effort) so it captures
    even mutations that later roll back — that's intentional, those rolled-back
    attempts are themselves interesting.
    """
    row = AuditLog(
        ts=datetime.now(timezone.utc),
        tool_name=tool_name,
        entity_type=entity_type,
        entity_id=entity_id,
        args_json=json.dumps(args, default=str) if args is not None else None,
        result_summary=result_summary,
        before_snapshot=json.dumps(before, default=str) if before is not None else None,
        after_snapshot=json.dumps(after, default=str) if after is not None else None,
    )
    session.add(row)

    # JSONL mirror — best-effort, never raises into the caller.
    try:
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": row.ts.isoformat(),
                "tool": tool_name,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "args": args,
                "before": before,
                "after": after,
                "result": result_summary,
            }, default=str) + "\n")
    except OSError:
        pass

    return row


def list_entries(
    session: Session,
    *,
    since: Optional[datetime] = None,
    entity_id: Optional[str] = None,
    tool_name: Optional[str] = None,
    limit: int = 100,
) -> list[AuditLog]:
    q = session.query(AuditLog).order_by(AuditLog.ts.desc())
    if since is not None:
        q = q.filter(AuditLog.ts >= since)
    if entity_id is not None:
        q = q.filter(AuditLog.entity_id == entity_id)
    if tool_name is not None:
        q = q.filter(AuditLog.tool_name == tool_name)
    return q.limit(limit).all()
