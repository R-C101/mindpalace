"""Lock tools: lock_entity, unlock_entity, is_locked."""

from __future__ import annotations

from mindpalace.core import audit, operations
from mindpalace.core.resolver import resolve_container_path, resolve_item_by_name
from mindpalace.db.models import EntityType
from mindpalace.mcp.errors import mcp_error


def _coerce_id_or_path(v):
    """
    Some MCP clients stringify arrays when a parameter's schema is a union of
    array and string. Parse JSON-array strings back into lists so the LLM can
    pass either shape.
    """
    if isinstance(v, str) and v.startswith("[") and v.endswith("]"):
        import json
        try:
            parsed = json.loads(v)
            if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
                return parsed
        except json.JSONDecodeError:
            pass
    return v


def _resolve(s, entity_type: str, id_or_path):
    et = EntityType(entity_type)
    id_or_path = _coerce_id_or_path(id_or_path)
    if et == EntityType.CONTAINER:
        if not isinstance(id_or_path, list):
            raise ValueError("containers are addressed by list[str] path")
        return resolve_container_path(s, id_or_path), et
    if not isinstance(id_or_path, str):
        raise ValueError("items are addressed by name string")
    return resolve_item_by_name(s, id_or_path), et


def _register():
    from mindpalace.server import mcp, session_scope

    @mcp.tool
    def lock_entity(entity_type: str, id_or_path: list[str] | str) -> dict:
        """
        Mark a container or item as locked.

        Locking is *advisory friction*, not security: future mutations on the
        locked entity will return ENTITY_LOCKED unless the call passes
        ``force=True``. Use this when the user wants extra confirmation
        before something is changed (e.g. "lock my passport").
        """
        id_or_path = _coerce_id_or_path(id_or_path)
        with session_scope() as s:
            try:
                entity, et = _resolve(s, entity_type, id_or_path)
                before = audit.snapshot(entity)
                e = operations.lock_entity(s, et, id_or_path)
            except Exception as exc:
                raise mcp_error(exc)
            audit.record(
                s, tool_name="lock_entity", entity_type=et.value, entity_id=e.id,
                args={"entity_type": entity_type, "id_or_path": id_or_path},
                before=before, after=audit.snapshot(e), result_summary="locked",
            )
            s.commit()
            return {"id": e.id, "path": e.path, "is_locked": e.is_locked}

    @mcp.tool
    def unlock_entity(entity_type: str, id_or_path: list[str] | str) -> dict:
        """Mark a container or item as unlocked. Always permitted."""
        id_or_path = _coerce_id_or_path(id_or_path)
        with session_scope() as s:
            try:
                entity, et = _resolve(s, entity_type, id_or_path)
                before = audit.snapshot(entity)
                e = operations.unlock_entity(s, et, id_or_path)
            except Exception as exc:
                raise mcp_error(exc)
            audit.record(
                s, tool_name="unlock_entity", entity_type=et.value, entity_id=e.id,
                args={"entity_type": entity_type, "id_or_path": id_or_path},
                before=before, after=audit.snapshot(e), result_summary="unlocked",
            )
            s.commit()
            return {"id": e.id, "path": e.path, "is_locked": e.is_locked}

    @mcp.tool
    def is_locked(entity_type: str, id_or_path: list[str] | str) -> dict:
        """Trivial read of an entity's lock state."""
        id_or_path = _coerce_id_or_path(id_or_path)
        with session_scope() as s:
            try:
                entity, _ = _resolve(s, entity_type, id_or_path)
            except Exception as exc:
                raise mcp_error(exc)
            return {"id": entity.id, "path": entity.path, "is_locked": entity.is_locked}


_register()
