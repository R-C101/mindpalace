"""Search tools: where_is, what_is_inside, search_items, search_containers."""

from __future__ import annotations

from typing import Literal, Optional

from mindpalace.core import search
from mindpalace.core.resolver import (
    get_children, get_items_in_container, resolve_container_path,
)
from mindpalace.mcp.errors import mcp_error
from mindpalace.mcp.schemas import (
    ContainerOut, ItemOut, SearchHitOut, container_out, item_out,
)


def _hit_to_out(hit: search.SearchHit) -> SearchHitOut:
    return SearchHitOut(
        entity_type=hit.entity_type.value, entity_id=hit.entity_id,
        name=hit.name, path=hit.path, score=hit.score,
        match_type=hit.match_type.value, is_locked=hit.is_locked,
        last_seen_at=hit.last_seen_at,
    )


def _register():
    from mindpalace.server import mcp, session_scope

    @mcp.tool
    def where_is(query: str, limit: int = 5) -> list[SearchHitOut]:
        """
        "Where is X?" — primary recall tool. Runs exact → alias → fuzzy
        (and semantic, if installed) and returns the best matches first.

        The ``match_type`` tells you why a row was returned. Use that in your
        reply: an exact match is unambiguous; a fuzzy match should be phrased
        as "I think you mean…".
        """
        with session_scope() as s:
            return [_hit_to_out(h) for h in search.where_is(s, query, limit=limit)]

    @mcp.tool
    def what_is_inside(
        path: list[str],
        recursive: bool = False,
        limit: int = 100,
    ) -> dict:
        """
        Return the contents of a container. ``recursive=False`` lists direct
        children only; ``recursive=True`` walks the full subtree (capped by limit).
        """
        with session_scope() as s:
            try:
                c = resolve_container_path(s, path)
            except Exception as e:
                raise mcp_error(e)
            if not recursive:
                return {
                    "containers": [container_out(x) for x in get_children(s, c.id)[:limit]],
                    "items": [item_out(x) for x in get_items_in_container(s, c.id)[:limit]],
                }
            from mindpalace.db.models import Container, Item
            cs = s.query(Container).filter(
                Container.path.like(f"{c.path}/%"),
                Container.deleted_at.is_(None),
            ).order_by(Container.path).limit(limit).all()
            its = s.query(Item).filter(
                Item.path.like(f"{c.path}/%"),
                Item.deleted_at.is_(None),
            ).order_by(Item.path).limit(limit).all()
            return {
                "containers": [container_out(x) for x in cs],
                "items": [item_out(x) for x in its],
            }

    @mcp.tool
    def search_items(
        query: str,
        mode: Literal["exact", "fuzzy", "semantic", "auto"] = "auto",
        limit: int = 20,
    ) -> list[SearchHitOut]:
        """
        Search items by name. ``auto`` runs exact → alias → fuzzy → semantic
        and returns the first non-empty result class. Use ``exact`` to forbid
        fuzzy matches when you need certainty.
        """
        with session_scope() as s:
            return [_hit_to_out(h) for h in search.search_items(s, query, mode=mode, limit=limit)]

    @mcp.tool
    def search_containers(
        query: str,
        mode: Literal["exact", "fuzzy", "semantic", "auto"] = "auto",
        limit: int = 20,
    ) -> list[SearchHitOut]:
        """Search containers by name with the same auto/exact/fuzzy/semantic modes."""
        with session_scope() as s:
            return [_hit_to_out(h) for h in search.search_containers(s, query, mode=mode, limit=limit)]


_register()
