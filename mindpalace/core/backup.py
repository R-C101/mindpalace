"""
Snapshot, restore, export, import.

For a SQLite-backed local server, "backup" is a file copy + a sha256.
That's all this needs to be in v1 — Postgres support arrives with the
v1.x cloud story and brings its own dump/restore pathway.

Exports are JSON for portability; imports validate against the JSON and
write through the same operations layer so invariants are enforced.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from mindpalace.config import BACKUPS_DIR, DB_PATH
from mindpalace.db.models import Container, Item


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def create_backup(note: str | None = None) -> dict[str, Any]:
    """
    Copy the live SQLite file into ``data/backups/`` with a UTC-timestamped
    name, plus a sidecar JSON describing it.
    """
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    backup_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    backup_path = BACKUPS_DIR / f"{backup_id}.db"
    shutil.copy2(DB_PATH, backup_path)
    digest = _sha256(backup_path)
    meta = {
        "backup_id": backup_id,
        "path": str(backup_path),
        "sha256": digest,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "note": note,
        "source": str(DB_PATH),
        "bytes": backup_path.stat().st_size,
    }
    (BACKUPS_DIR / f"{backup_id}.json").write_text(json.dumps(meta, indent=2))
    return meta


def list_backups() -> list[dict[str, Any]]:
    if not BACKUPS_DIR.exists():
        return []
    out: list[dict[str, Any]] = []
    for meta_file in sorted(BACKUPS_DIR.glob("*.json")):
        try:
            out.append(json.loads(meta_file.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def restore_backup(backup_id: str) -> dict[str, Any]:
    """
    Replace the live DB with the named backup. Snapshots the *current* DB
    first so a bad restore is itself reversible.
    """
    target = BACKUPS_DIR / f"{backup_id}.db"
    if not target.exists():
        raise FileNotFoundError(f"backup '{backup_id}' not found at {target}")

    # safety snapshot
    pre_restore = create_backup(note=f"auto-snapshot-before-restore:{backup_id}")
    shutil.copy2(target, DB_PATH)
    return {
        "restored_from": backup_id,
        "pre_restore_snapshot": pre_restore["backup_id"],
        "restored_at": datetime.now(timezone.utc).isoformat(),
    }


# --- Export / Import (JSON, lossless for v1 surface) ---------------------

def export_data(session: Session, include_deleted: bool = False) -> dict[str, Any]:
    cq = session.query(Container)
    iq = session.query(Item)
    if not include_deleted:
        cq = cq.filter(Container.deleted_at.is_(None))
        iq = iq.filter(Item.deleted_at.is_(None))

    payload = {
        "schema_version": 2,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "containers": [
            {
                "id": c.id, "name": c.name, "display_name": c.display_name,
                "canonical_name": c.canonical_name, "path": c.path, "depth": c.depth,
                "parent_id": c.parent_id, "is_locked": c.is_locked,
                "container_type": c.container_type, "description": c.description,
                "version": c.version,
            }
            for c in cq.order_by(Container.depth, Container.path).all()
        ],
        "items": [
            {
                "id": i.id, "name": i.name, "display_name": i.display_name,
                "canonical_name": i.canonical_name, "path": i.path,
                "container_id": i.container_id, "is_locked": i.is_locked,
                "quantity": i.quantity, "item_type": i.item_type,
                "description": i.description, "version": i.version,
            }
            for i in iq.order_by(Item.path).all()
        ],
    }
    return payload


def write_export(session: Session, dest: Path | None = None) -> dict[str, Any]:
    payload = export_data(session)
    dest = dest or (BACKUPS_DIR / f"export-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2))
    return {
        "path": str(dest), "bytes": dest.stat().st_size,
        "sha256": _sha256(dest), "containers": len(payload["containers"]),
        "items": len(payload["items"]),
    }
