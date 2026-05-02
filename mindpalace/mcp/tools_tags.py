"""Tag and alias tools."""

from __future__ import annotations

from mindpalace.core import audit, tags
from mindpalace.core.resolver import resolve_container_path, resolve_item_by_name
from mindpalace.db.models import EntityType
from mindpalace.mcp.errors import mcp_error


def _coerce_id_or_path(v):
    """Parse JSON-array strings (some clients stringify arrays for union schemas)."""
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
    return resolve_item_by_name(s, id_or_path), et


def _register():
    from mindpalace.server import mcp, session_scope

    @mcp.tool
    def add_tag(entity_type: str, id_or_path: list[str] | str, tag: str) -> dict:
        """Attach a tag to a container or item. Auto-creates the tag if new."""
        with session_scope() as s:
            try:
                entity, et = _resolve(s, entity_type, id_or_path)
                t = tags.add_tag(s, et, entity.id, tag)
            except Exception as e:
                raise mcp_error(e)
            audit.record(
                s, tool_name="add_tag", entity_type=et.value, entity_id=entity.id,
                args={"tag": tag}, before=None, after=None, result_summary=f"tagged:{tag}",
            )
            s.commit()
            return {
                "tags": [t.canonical_name for t in tags.list_tags_for_entity(s, et, entity.id)],
            }

    @mcp.tool
    def remove_tag(entity_type: str, id_or_path: list[str] | str, tag: str) -> dict:
        """Detach a tag. No-op if the tag wasn't attached."""
        with session_scope() as s:
            try:
                entity, et = _resolve(s, entity_type, id_or_path)
                tags.remove_tag(s, et, entity.id, tag)
            except Exception as e:
                raise mcp_error(e)
            audit.record(
                s, tool_name="remove_tag", entity_type=et.value, entity_id=entity.id,
                args={"tag": tag}, before=None, after=None, result_summary=f"untagged:{tag}",
            )
            s.commit()
            return {
                "tags": [t.canonical_name for t in tags.list_tags_for_entity(s, et, entity.id)],
            }

    @mcp.tool
    def list_tags(entity_type: str, id_or_path: list[str] | str) -> list[str]:
        """List the tags attached to one entity."""
        with session_scope() as s:
            try:
                entity, et = _resolve(s, entity_type, id_or_path)
            except Exception as e:
                raise mcp_error(e)
            return [t.canonical_name for t in tags.list_tags_for_entity(s, et, entity.id)]

    @mcp.tool
    def find_by_tag(tag: str, limit: int = 50) -> list[dict]:
        """Return ``[{entity_type, entity_id}, …]`` for entities carrying the tag."""
        with session_scope() as s:
            return [
                {"entity_type": et, "entity_id": eid}
                for et, eid in tags.find_entities_by_tag(s, tag, limit=limit)
            ]

    @mcp.tool
    def add_alias(entity_type: str, id_or_path: list[str] | str, alias: str) -> dict:
        """
        Register an alternative name for the entity. ``where_is(<alias>)`` will
        then resolve to it. Aliases must not collide with the canonical names
        of sibling entities.
        """
        with session_scope() as s:
            try:
                entity, et = _resolve(s, entity_type, id_or_path)
                a = tags.add_alias(s, et, entity.id, alias)
            except Exception as e:
                raise mcp_error(e)
            audit.record(
                s, tool_name="add_alias", entity_type=et.value, entity_id=entity.id,
                args={"alias": alias}, before=None, after=None, result_summary=f"alias+:{alias}",
            )
            s.commit()
            return {"alias": a.alias, "canonical_alias": a.canonical_alias}

    @mcp.tool
    def remove_alias(entity_type: str, id_or_path: list[str] | str, alias: str) -> dict:
        """Remove an alias. No-op if the alias wasn't there."""
        with session_scope() as s:
            try:
                entity, et = _resolve(s, entity_type, id_or_path)
                tags.remove_alias(s, et, entity.id, alias)
            except Exception as e:
                raise mcp_error(e)
            audit.record(
                s, tool_name="remove_alias", entity_type=et.value, entity_id=entity.id,
                args={"alias": alias}, before=None, after=None, result_summary=f"alias-:{alias}",
            )
            s.commit()
            return {"removed": alias}

    @mcp.tool
    def list_aliases(entity_type: str, id_or_path: list[str] | str) -> list[str]:
        """List the aliases of one entity."""
        with session_scope() as s:
            try:
                entity, et = _resolve(s, entity_type, id_or_path)
            except Exception as e:
                raise mcp_error(e)
            return [a.alias for a in tags.list_aliases_for_entity(s, et, entity.id)]


_register()
