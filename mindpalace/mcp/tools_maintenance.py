"""Maintenance tools: health_check, initialize_home, audit_log, backup, export."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from mindpalace import __version__
from mindpalace.core import audit, backup
from mindpalace.core.operations import initialize_root
from mindpalace.config import DB_PATH
from mindpalace.db.models import AuditLog, Container, Item


class HealthOut(BaseModel):
    status: str
    db_ok: bool
    root_initialized: bool
    embedding_model_ok: bool
    tool_count: int
    db_size_bytes: int
    container_count: int
    item_count: int
    mindpalace_version: str


class ContainerOut(BaseModel):
    id: str
    name: str
    display_name: str
    path: str
    depth: int
    is_locked: bool
    parent_id: Optional[str] = None


class AuditEntryOut(BaseModel):
    id: str
    ts: datetime
    tool_name: str
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    result_summary: Optional[str] = None


def _container_out(c: Container) -> ContainerOut:
    return ContainerOut(
        id=c.id, name=c.name, display_name=c.display_name, path=c.path,
        depth=c.depth, is_locked=c.is_locked, parent_id=c.parent_id,
    )


def _register():
    from mindpalace.server import mcp, session_scope

    @mcp.tool
    async def health_check() -> HealthOut:
        """
        Quick liveness + sanity report for the server.

        Returns counts, version, and whether the optional embedding stack
        is available. Always safe to call; never mutates anything.
        """
        try:
            from mindpalace.core import embeddings  # noqa: F401
            embedding_ok = True
        except ImportError:
            embedding_ok = False
        with session_scope() as s:
            from mindpalace.core.resolver import get_root_container
            root = get_root_container(s)
            container_count = s.query(Container).filter(Container.deleted_at.is_(None)).count()
            item_count = s.query(Item).filter(Item.deleted_at.is_(None)).count()
        return HealthOut(
            status="ok",
            db_ok=True,
            root_initialized=root is not None,
            embedding_model_ok=embedding_ok,
            tool_count=len(await mcp.list_tools()),
            db_size_bytes=DB_PATH.stat().st_size if DB_PATH.exists() else 0,
            container_count=container_count,
            item_count=item_count,
            mindpalace_version=__version__,
        )

    @mcp.tool
    def initialize_home(display_name: str = "Home") -> ContainerOut:
        """
        Create the root ``/home`` container if it doesn't exist. Idempotent.

        Call this before adding any containers or items. If the root already
        exists, the existing root is returned unchanged.
        """
        with session_scope() as s:
            root = initialize_root(s)
            return _container_out(root)

    @mcp.tool
    def audit_log_entries(
        since: Optional[datetime] = None,
        entity_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        limit: int = 100,
    ) -> list[AuditEntryOut]:
        """
        Read the audit log (most-recent-first).

        Filters: ``since`` (ISO8601 timestamp), ``entity_id``, ``tool_name``.
        Useful for "what changed last Tuesday" or to power undo (planned).
        """
        with session_scope() as s:
            entries = audit.list_entries(
                s, since=since, entity_id=entity_id, tool_name=tool_name, limit=limit,
            )
            return [
                AuditEntryOut(
                    id=e.id, ts=e.ts, tool_name=e.tool_name,
                    entity_type=e.entity_type, entity_id=e.entity_id,
                    result_summary=e.result_summary,
                )
                for e in entries
            ]

    @mcp.tool
    def create_backup(note: Optional[str] = None) -> dict:
        """
        Snapshot the live DB into ``data/backups/``. Returns
        ``{backup_id, path, sha256, bytes, created_at, note}``.
        """
        return backup.create_backup(note=note)

    @mcp.tool
    def list_backups() -> list[dict]:
        """List available backups (most-recent-first by name sort)."""
        return list(reversed(backup.list_backups()))

    @mcp.tool
    def export_data() -> dict:
        """
        Export the live (non-deleted) state to a JSON file under
        ``data/backups/``. Returns ``{path, bytes, sha256, containers, items}``.
        """
        with session_scope() as s:
            return backup.write_export(s)


_register()
