"""
Search — exact, alias, fuzzy, and (optionally) semantic.

Determinism-first: ``auto`` mode runs the cheapest, most predictable strategy
first and stops once it finds something. The LLM only ever falls into
fuzzy/semantic when exact and alias have nothing — and the ``match_type``
field on every result tells it (and the user) why a row appeared.

Semantic search is gated behind the ``[semantic]`` extra. If
``sentence-transformers`` is not installed, semantic queries cleanly return
``[]`` instead of crashing — the rest of the pipeline keeps working.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal, Optional

from rapidfuzz import fuzz, process
from sqlalchemy.orm import Session

from mindpalace.core.tags import resolve_by_alias
from mindpalace.db.models import Container, EntityType, Item, to_canonical_name


class MatchType(str, Enum):
    EXACT = "exact"
    ALIAS = "alias"
    FUZZY = "fuzzy"
    SEMANTIC = "semantic"


@dataclass
class SearchHit:
    entity_type: EntityType
    entity_id: str
    name: str
    path: str
    score: float           # 0.0–1.0
    match_type: MatchType
    is_locked: bool
    last_seen_at: Optional[str]


def _to_hit(entity, match_type: MatchType, score: float) -> SearchHit:
    et = EntityType.ITEM if isinstance(entity, Item) else EntityType.CONTAINER
    return SearchHit(
        entity_type=et,
        entity_id=entity.id,
        name=entity.display_name,
        path=entity.path,
        score=score,
        match_type=match_type,
        is_locked=entity.is_locked,
        last_seen_at=entity.last_seen_at.isoformat() if entity.last_seen_at else None,
    )


# --- Strategies ---------------------------------------------------------

def _exact_items(session: Session, query: str) -> list[Item]:
    canonical = to_canonical_name(query)
    return (
        session.query(Item)
        .filter(Item.canonical_name == canonical, Item.deleted_at.is_(None))
        .all()
    )


def _exact_containers(session: Session, query: str) -> list[Container]:
    canonical = to_canonical_name(query)
    return (
        session.query(Container)
        .filter(Container.canonical_name == canonical, Container.deleted_at.is_(None))
        .all()
    )


def _alias_hits(session: Session, query: str) -> list[SearchHit]:
    hits: list[SearchHit] = []
    for alias in resolve_by_alias(session, query):
        if alias.entity_type == EntityType.CONTAINER.value:
            entity = (
                session.query(Container)
                .filter(Container.id == alias.entity_id, Container.deleted_at.is_(None))
                .one_or_none()
            )
        else:
            entity = (
                session.query(Item)
                .filter(Item.id == alias.entity_id, Item.deleted_at.is_(None))
                .one_or_none()
            )
        if entity is not None:
            hits.append(_to_hit(entity, MatchType.ALIAS, score=0.95))
    return hits


def _fuzzy_items(session: Session, query: str, limit: int) -> list[SearchHit]:
    rows = session.query(Item).filter(Item.deleted_at.is_(None)).all()
    return _fuzzy_rank(rows, query, limit)


def _fuzzy_containers(session: Session, query: str, limit: int) -> list[SearchHit]:
    rows = session.query(Container).filter(Container.deleted_at.is_(None)).all()
    return _fuzzy_rank(rows, query, limit)


def _fuzzy_rank(rows, query: str, limit: int) -> list[SearchHit]:
    if not rows:
        return []
    by_name = {r.id: r for r in rows}
    haystack = {r.id: r.display_name for r in rows}
    scored = process.extract(
        query, haystack, scorer=fuzz.WRatio, limit=limit,
    )
    # rapidfuzz returns (value, score, key) — use the key (id) to look up.
    out: list[SearchHit] = []
    for _, score, rid in scored:
        if score < 60:        # WRatio is 0–100; anything below is noise
            continue
        out.append(_to_hit(by_name[rid], MatchType.FUZZY, score=score / 100.0))
    return out


def _semantic_search(
    session: Session, query: str, limit: int, scope: Literal["items", "containers", "both"],
) -> list[SearchHit]:
    """
    Vector search against pre-computed embeddings. Returns [] if the optional
    embedding stack isn't installed — callers must treat absence as 'try the
    cheaper modes first'.
    """
    try:
        from mindpalace.core.embeddings import embed_query, search as vec_search  # noqa: F401
    except ImportError:
        return []
    return vec_search(session, query, limit=limit, scope=scope)


# --- Public API ---------------------------------------------------------

def search_items(
    session: Session,
    query: str,
    mode: Literal["exact", "fuzzy", "semantic", "auto"] = "auto",
    limit: int = 20,
) -> list[SearchHit]:
    if mode in ("exact", "auto"):
        items = _exact_items(session, query)
        if items:
            return [_to_hit(i, MatchType.EXACT, 1.0) for i in items][:limit]
    if mode == "auto":
        alias_hits = [h for h in _alias_hits(session, query) if h.entity_type == EntityType.ITEM]
        if alias_hits:
            return alias_hits[:limit]
    if mode in ("fuzzy", "auto"):
        fuzzy = _fuzzy_items(session, query, limit)
        if fuzzy or mode == "fuzzy":
            return fuzzy[:limit]
    if mode in ("semantic", "auto"):
        return _semantic_search(session, query, limit, scope="items")[:limit]
    return []


def search_containers(
    session: Session,
    query: str,
    mode: Literal["exact", "fuzzy", "semantic", "auto"] = "auto",
    limit: int = 20,
) -> list[SearchHit]:
    if mode in ("exact", "auto"):
        cs = _exact_containers(session, query)
        if cs:
            return [_to_hit(c, MatchType.EXACT, 1.0) for c in cs][:limit]
    if mode == "auto":
        alias_hits = [
            h for h in _alias_hits(session, query) if h.entity_type == EntityType.CONTAINER
        ]
        if alias_hits:
            return alias_hits[:limit]
    if mode in ("fuzzy", "auto"):
        fuzzy = _fuzzy_containers(session, query, limit)
        if fuzzy or mode == "fuzzy":
            return fuzzy[:limit]
    if mode in ("semantic", "auto"):
        return _semantic_search(session, query, limit, scope="containers")[:limit]
    return []


def where_is(session: Session, query: str, limit: int = 5) -> list[SearchHit]:
    """
    "Where is X?" — items first (the typical question), then containers as
    a fallback. Auto-mode within each.
    """
    items = search_items(session, query, mode="auto", limit=limit)
    if items:
        return items[:limit]
    return search_containers(session, query, mode="auto", limit=limit)[:limit]
