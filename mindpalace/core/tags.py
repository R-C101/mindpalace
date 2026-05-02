"""
Tag and alias mutations + lookups.

Tags are an orthogonal axis to the container hierarchy: an item tagged
``valuable`` can be queried regardless of where it lives.

Aliases are alternative names that resolve to the same entity. "Passport"
should resolve when the canonical name is "United States Passport" without
forcing the user to rename. Alias canonicalisation reuses the same rules
as ``to_canonical_name``, so "US-Passport" and "us passport" collapse to
the same lookup key.

Uniqueness scoping for aliases:
    - Containers: an alias is unique among siblings of the same parent
      (so "drawer" can be an alias in two different rooms).
    - Items: unique within their owning container.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from mindpalace.db.models import (
    Alias, Container, EntityTag, EntityType, Item, Tag, to_canonical_name,
)


# --- Tags ---------------------------------------------------------------

def _ensure_tag(session: Session, tag_name: str) -> Tag:
    canonical = to_canonical_name(tag_name)
    existing = session.query(Tag).filter(Tag.canonical_name == canonical).one_or_none()
    if existing:
        return existing
    tag = Tag(name=tag_name, canonical_name=canonical)
    session.add(tag)
    session.flush()
    return tag


def add_tag(
    session: Session, entity_type: EntityType, entity_id: str, tag_name: str,
) -> Tag:
    tag = _ensure_tag(session, tag_name)
    existing = session.query(EntityTag).filter(
        EntityTag.entity_type == entity_type.value,
        EntityTag.entity_id == entity_id,
        EntityTag.tag_id == tag.id,
    ).one_or_none()
    if existing is None:
        session.add(EntityTag(
            entity_type=entity_type.value, entity_id=entity_id, tag_id=tag.id,
        ))
    session.commit()
    return tag


def remove_tag(
    session: Session, entity_type: EntityType, entity_id: str, tag_name: str,
) -> None:
    canonical = to_canonical_name(tag_name)
    tag = session.query(Tag).filter(Tag.canonical_name == canonical).one_or_none()
    if tag is None:
        return
    session.query(EntityTag).filter(
        EntityTag.entity_type == entity_type.value,
        EntityTag.entity_id == entity_id,
        EntityTag.tag_id == tag.id,
    ).delete(synchronize_session=False)
    session.commit()


def list_tags_for_entity(
    session: Session, entity_type: EntityType, entity_id: str,
) -> list[Tag]:
    return (
        session.query(Tag)
        .join(EntityTag, EntityTag.tag_id == Tag.id)
        .filter(
            EntityTag.entity_type == entity_type.value,
            EntityTag.entity_id == entity_id,
        )
        .order_by(Tag.canonical_name)
        .all()
    )


def find_entities_by_tag(
    session: Session, tag_name: str, limit: int = 50,
) -> list[tuple[str, str]]:
    """Return [(entity_type, entity_id), …] for entities carrying the tag."""
    canonical = to_canonical_name(tag_name)
    rows = (
        session.query(EntityTag)
        .join(Tag, Tag.id == EntityTag.tag_id)
        .filter(Tag.canonical_name == canonical)
        .limit(limit)
        .all()
    )
    return [(r.entity_type, r.entity_id) for r in rows]


# --- Aliases ------------------------------------------------------------

def _resolve_parent_scope(
    session: Session, entity_type: EntityType, entity_id: str,
) -> Optional[str]:
    """
    For containers: the parent_id (siblings share the alias namespace).
    For items: the container_id.
    """
    if entity_type == EntityType.CONTAINER:
        c = session.query(Container).filter(Container.id == entity_id).one_or_none()
        return c.parent_id if c else None
    item = session.query(Item).filter(Item.id == entity_id).one_or_none()
    return item.container_id if item else None


def add_alias(
    session: Session, entity_type: EntityType, entity_id: str, alias: str,
) -> Alias:
    canonical = to_canonical_name(alias)
    parent_scope = _resolve_parent_scope(session, entity_type, entity_id)

    # An alias must not collide with an existing canonical_name in the same
    # scope, otherwise resolve_by_alias would produce two different entities
    # for the same string.
    if entity_type == EntityType.CONTAINER:
        clash = session.query(Container).filter(
            Container.parent_id == parent_scope,
            Container.canonical_name == canonical,
            Container.deleted_at.is_(None),
        ).first()
    else:
        clash = session.query(Item).filter(
            Item.container_id == parent_scope,
            Item.canonical_name == canonical,
            Item.deleted_at.is_(None),
        ).first()
    if clash is not None and clash.id != entity_id:
        raise ValueError(
            f"alias '{alias}' collides with existing canonical name in same scope"
        )

    row = Alias(
        entity_type=entity_type.value, entity_id=entity_id,
        alias=alias, canonical_alias=canonical, parent_scope_id=parent_scope,
    )
    session.add(row)
    session.commit()
    return row


def remove_alias(
    session: Session, entity_type: EntityType, entity_id: str, alias: str,
) -> None:
    canonical = to_canonical_name(alias)
    session.query(Alias).filter(
        Alias.entity_type == entity_type.value,
        Alias.entity_id == entity_id,
        Alias.canonical_alias == canonical,
    ).delete(synchronize_session=False)
    session.commit()


def list_aliases_for_entity(
    session: Session, entity_type: EntityType, entity_id: str,
) -> list[Alias]:
    return (
        session.query(Alias)
        .filter(
            Alias.entity_type == entity_type.value,
            Alias.entity_id == entity_id,
        )
        .order_by(Alias.canonical_alias)
        .all()
    )


def resolve_by_alias(session: Session, name: str) -> list[Alias]:
    """
    Find every alias matching ``name`` (canonicalised). Caller decides how
    to disambiguate or report ambiguity.
    """
    canonical = to_canonical_name(name)
    return session.query(Alias).filter(Alias.canonical_alias == canonical).all()
