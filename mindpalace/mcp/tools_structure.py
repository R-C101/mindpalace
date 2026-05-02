"""Structure tools: create/rename/move/delete container, list_children, get_tree."""

from __future__ import annotations

from typing import Optional

from mindpalace.core import audit, operations
from mindpalace.core.resolver import (
    get_children, get_items_in_container, resolve_container_path,
)
from mindpalace.db.models import Container, Item
from mindpalace.mcp.errors import mcp_error
from mindpalace.mcp.schemas import (
    ContainerOut, ItemOut, TreeNode, container_out, item_out,
)


def _children_node(session, container: Container, max_depth: int, include_items: bool) -> TreeNode:
    node = TreeNode(
        kind="container", id=container.id, name=container.display_name,
        path=container.path, is_locked=container.is_locked,
    )
    if max_depth <= 0:
        return node
    for child in get_children(session, container.id):
        node.children.append(_children_node(session, child, max_depth - 1, include_items))
    if include_items:
        for it in get_items_in_container(session, container.id):
            node.items.append(TreeNode(
                kind="item", id=it.id, name=it.display_name,
                path=it.path, is_locked=it.is_locked,
            ))
    return node


def _register():
    from mindpalace.server import mcp, session_scope

    @mcp.tool
    def create_container(
        parent_path: list[str],
        name: str,
        container_type: Optional[str] = None,
        description: Optional[str] = None,
        display_name: Optional[str] = None,
        force: bool = False,
    ) -> ContainerOut:
        """
        Create a new container under ``parent_path``.

        ``parent_path`` is segments below /home — pass ``[]`` for "directly under home".
        Containers can nest infinitely. They are NOT items: items are leaf objects
        you store *inside* containers.

        Errors:
          - PATH_NOT_FOUND: parent doesn't exist; payload includes ``available_children``.
          - SIBLING_CONFLICT: a sibling with that canonical name already exists.
          - ENTITY_LOCKED: parent is locked; ask the user, then retry with force=True.
        """
        with session_scope() as s:
            try:
                c = operations.add_container(
                    s, parent_path, name,
                    container_type=container_type,
                    description=description,
                    display_name=display_name,
                    force=force,
                )
            except Exception as e:
                raise mcp_error(e)
            audit.record(
                s, tool_name="create_container", entity_type="container", entity_id=c.id,
                args={"parent_path": parent_path, "name": name, "force": force},
                before=None, after=audit.snapshot(c), result_summary="created",
            )
            s.commit()
            return container_out(c)

    @mcp.tool
    def rename_container(
        path: list[str],
        new_name: str,
        new_display_name: Optional[str] = None,
        force: bool = False,
    ) -> ContainerOut:
        """
        Rename a container. All descendant paths are recomputed atomically.
        Cannot rename root.
        """
        with session_scope() as s:
            try:
                before = audit.snapshot(resolve_container_path(s, path))
                c = operations.rename_container(
                    s, path, new_name,
                    new_display_name=new_display_name, force=force,
                )
            except Exception as e:
                raise mcp_error(e)
            audit.record(
                s, tool_name="rename_container", entity_type="container", entity_id=c.id,
                args={"path": path, "new_name": new_name, "force": force},
                before=before, after=audit.snapshot(c), result_summary="renamed",
            )
            s.commit()
            return container_out(c)

    @mcp.tool
    def move_container(
        source_path: list[str],
        dest_parent_path: list[str],
        force: bool = False,
    ) -> ContainerOut:
        """
        Move a container under a new parent. Cannot move root, cannot move
        into self/descendant. Descendants follow.
        """
        with session_scope() as s:
            try:
                before = audit.snapshot(resolve_container_path(s, source_path))
                c = operations.move_container(s, source_path, dest_parent_path, force=force)
            except Exception as e:
                raise mcp_error(e)
            audit.record(
                s, tool_name="move_container", entity_type="container", entity_id=c.id,
                args={"source_path": source_path, "dest_parent_path": dest_parent_path, "force": force},
                before=before, after=audit.snapshot(c), result_summary="moved",
            )
            s.commit()
            return container_out(c)

    @mcp.tool
    def delete_container(
        path: list[str],
        confirm: bool,
        force: bool = False,
    ) -> dict:
        """
        Soft-delete a container and everything inside it. Returns
        ``{deleted_id, descendant_count}``.

        ``confirm`` MUST be True (forces the LLM to surface impact to the user).
        Soft-delete is reversible until garbage-collected (~30 days).
        """
        if not confirm:
            from fastmcp.exceptions import ToolError
            raise ToolError(
                "REFUSED: delete_container requires confirm=true",
                data={"code": "CONFIRM_REQUIRED"},
            )
        with session_scope() as s:
            try:
                container = resolve_container_path(s, path)
                before = audit.snapshot(container)
                deleted_id = container.id
                desc_count = (
                    s.query(Container)
                    .filter(Container.path.like(f"{container.path}/%"))
                    .filter(Container.deleted_at.is_(None))
                    .count()
                    + s.query(Item)
                    .filter(Item.path.like(f"{container.path}/%"))
                    .filter(Item.deleted_at.is_(None))
                    .count()
                )
                operations.delete_container(s, path, force=force)
            except Exception as e:
                raise mcp_error(e)
            audit.record(
                s, tool_name="delete_container", entity_type="container", entity_id=deleted_id,
                args={"path": path, "force": force},
                before=before, after=None, result_summary=f"soft-deleted ({desc_count} descendants)",
            )
            s.commit()
            return {"deleted_id": deleted_id, "descendant_count": desc_count}

    @mcp.tool
    def list_children(
        path: list[str],
        include_items: bool = True,
    ) -> dict:
        """
        List the direct contents of a container.
        Returns ``{containers: [ContainerOut], items: [ItemOut]}``.
        """
        with session_scope() as s:
            try:
                c = resolve_container_path(s, path)
            except Exception as e:
                raise mcp_error(e)
            return {
                "containers": [container_out(x) for x in get_children(s, c.id)],
                "items": [item_out(x) for x in get_items_in_container(s, c.id)] if include_items else [],
            }

    @mcp.tool
    def get_tree(
        root_path: Optional[list[str]] = None,
        max_depth: int = 10,
        include_items: bool = True,
    ) -> TreeNode:
        """
        Recursive tree view starting at ``root_path`` (default: root).
        ``max_depth`` is bounded to keep responses tractable.
        """
        if max_depth > 20:
            max_depth = 20
        with session_scope() as s:
            try:
                c = resolve_container_path(s, root_path or [])
            except Exception as e:
                raise mcp_error(e)
            return _children_node(s, c, max_depth, include_items)


_register()
