"""Item tools: add/rename/move/delete/find item, list_items_in_container."""

from __future__ import annotations

from typing import Optional

from mindpalace.core import audit, operations
from mindpalace.core.resolver import (
    find_items_by_name_global, get_items_in_container,
    resolve_container_path, resolve_item_by_name,
)
from mindpalace.mcp.errors import mcp_error
from mindpalace.mcp.schemas import ItemOut, item_out


def _register():
    from mindpalace.server import mcp, session_scope

    @mcp.tool
    def add_item(
        container_path: list[str],
        name: str,
        description: Optional[str] = None,
        quantity: int = 1,
        item_type: Optional[str] = None,
        display_name: Optional[str] = None,
        force: bool = False,
    ) -> ItemOut:
        """
        Add a new item (a leaf object) into the container at ``container_path``.

        Errors:
          - PATH_NOT_FOUND, ITEM_CONFLICT, ENTITY_LOCKED.
        """
        with session_scope() as s:
            try:
                i = operations.add_item(
                    s, container_path, name,
                    description=description, quantity=quantity,
                    item_type=item_type, display_name=display_name, force=force,
                )
            except Exception as e:
                raise mcp_error(e)
            audit.record(
                s, tool_name="add_item", entity_type="item", entity_id=i.id,
                args={"container_path": container_path, "name": name, "quantity": quantity},
                before=None, after=audit.snapshot(i), result_summary="added",
            )
            s.commit()
            return item_out(i)

    @mcp.tool
    def rename_item(
        item_name: str,
        new_name: str,
        container_path: Optional[list[str]] = None,
        new_display_name: Optional[str] = None,
        force: bool = False,
    ) -> ItemOut:
        """
        Rename an item. Provide ``container_path`` if the name is ambiguous globally.
        """
        with session_scope() as s:
            try:
                container = (
                    resolve_container_path(s, container_path) if container_path else None
                )
                before = audit.snapshot(resolve_item_by_name(s, item_name, container))
                i = operations.rename_item(
                    s, item_name, new_name,
                    container=container, new_display_name=new_display_name, force=force,
                )
            except Exception as e:
                raise mcp_error(e)
            audit.record(
                s, tool_name="rename_item", entity_type="item", entity_id=i.id,
                args={"item_name": item_name, "new_name": new_name},
                before=before, after=audit.snapshot(i), result_summary="renamed",
            )
            s.commit()
            return item_out(i)

    @mcp.tool
    def move_item(
        item_name: str,
        dest_container_path: list[str],
        source_container_path: Optional[list[str]] = None,
        force: bool = False,
    ) -> ItemOut:
        """
        Move an item to a different container. Provide ``source_container_path``
        if the item name is ambiguous globally.
        """
        with session_scope() as s:
            try:
                source_container = (
                    resolve_container_path(s, source_container_path)
                    if source_container_path else None
                )
                before = audit.snapshot(resolve_item_by_name(s, item_name, source_container))
                i = operations.move_item(
                    s, item_name, dest_container_path,
                    source_container=source_container, force=force,
                )
            except Exception as e:
                raise mcp_error(e)
            audit.record(
                s, tool_name="move_item", entity_type="item", entity_id=i.id,
                args={"item_name": item_name, "dest_container_path": dest_container_path},
                before=before, after=audit.snapshot(i), result_summary="moved",
            )
            s.commit()
            return item_out(i)

    @mcp.tool
    def delete_item(
        item_name: str,
        confirm: bool,
        container_path: Optional[list[str]] = None,
        force: bool = False,
    ) -> dict:
        """Soft-delete an item. Reversible until GC."""
        if not confirm:
            from fastmcp.exceptions import ToolError
            raise ToolError(
                "REFUSED: delete_item requires confirm=true",
                data={"code": "CONFIRM_REQUIRED"},
            )
        with session_scope() as s:
            try:
                container = (
                    resolve_container_path(s, container_path) if container_path else None
                )
                target = resolve_item_by_name(s, item_name, container)
                before = audit.snapshot(target)
                deleted_id = target.id
                operations.delete_item(s, item_name, container=container, force=force)
            except Exception as e:
                raise mcp_error(e)
            audit.record(
                s, tool_name="delete_item", entity_type="item", entity_id=deleted_id,
                args={"item_name": item_name},
                before=before, after=None, result_summary="soft-deleted",
            )
            s.commit()
            return {"deleted_id": deleted_id}

    @mcp.tool
    def find_item(name: str, limit: int = 20) -> list[dict]:
        """
        Exact (canonical) match of items by name across the whole tree.
        Returns ``[{item, container_path}, …]``. For fuzzy/semantic matches
        use ``where_is`` or ``search_items``.
        """
        with session_scope() as s:
            matches = find_items_by_name_global(s, name)[:limit]
            return [
                {"item": item_out(i), "container_path": i.path.rsplit("/", 1)[0]}
                for i in matches
            ]

    @mcp.tool
    def list_items_in_container(container_path: list[str]) -> list[ItemOut]:
        """List the items directly inside a container (not recursive)."""
        with session_scope() as s:
            try:
                c = resolve_container_path(s, container_path)
            except Exception as e:
                raise mcp_error(e)
            return [item_out(i) for i in get_items_in_container(s, c.id)]

    @mcp.tool
    def mark_seen(
        entity_type: str,
        id_or_path: list[str] | str,
    ) -> dict:
        """
        Bump ``last_seen_at`` for an entity. Use when the user has just
        physically confirmed the item is still where MindPalace thinks it is.
        """
        from mindpalace.db.models import EntityType
        # Some MCP clients stringify arrays when the param schema is a union;
        # parse JSON-array strings back into lists.
        if isinstance(id_or_path, str) and id_or_path.startswith("[") and id_or_path.endswith("]"):
            import json
            try:
                parsed = json.loads(id_or_path)
                if isinstance(parsed, list) and all(isinstance(x, str) for x in parsed):
                    id_or_path = parsed
            except json.JSONDecodeError:
                pass
        with session_scope() as s:
            try:
                et = EntityType(entity_type)
                entity = operations.mark_seen(s, et, id_or_path)
            except Exception as e:
                raise mcp_error(e)
            return {
                "id": entity.id, "path": entity.path,
                "last_seen_at": entity.last_seen_at.isoformat() if entity.last_seen_at else None,
            }


_register()
