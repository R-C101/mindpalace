"""
MindPalace — SQLAlchemy ORM models.

Materialised path + adjacency list for the Container/Item hierarchy.
Compatible with SQLite (default) and PostgreSQL.

v2 changes (vs the home-memory v1 schema):
    - Dropped ``security_level`` (single-user local server has no use for it).
    - Added ``deleted_at`` (soft-delete), ``version`` (optimistic concurrency),
      ``last_seen_at`` (real-world freshness).
    - New tables: aliases, tags, entity_tags, embeddings, audit_log.
    - Image tables retained as-is; image MCP tools are deferred to v1.1.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# --- Base ----------------------------------------------------------------

class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def generate_uuid() -> str:
    return str(uuid.uuid4())


def to_canonical_name(name: str) -> str:
    """
    Normalise a name for identity matching.

    "TV Room", "tv-room", "tv_room", "TVRoom" all canonicalise to "tvroom".
    Used for sibling uniqueness and lookup. Stored alongside the original
    ``name`` so the human-readable form survives.
    """
    import re
    canonical = name.lower()
    canonical = re.sub(r"[\s\-_]+", "", canonical)
    return canonical


# --- Enums ---------------------------------------------------------------

class EntityType(str, Enum):
    """Polymorphic discriminator used by aliases / entity_images / entity_tags / embeddings."""
    CONTAINER = "container"
    ITEM = "item"


# --- Container -----------------------------------------------------------

class Container(Base):
    """
    A nestable storage location: home, room, drawer, suitcase, …

    Materialised path (``path``) gives O(1) ancestor lookup;
    ``parent_id`` keeps the adjacency-list structure for cascade deletes.
    """
    __tablename__ = "containers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    path: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True, index=True)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    parent_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("containers.id", ondelete="CASCADE"), nullable=True, index=True,
    )

    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    container_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # v2 additions
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    parent: Mapped[Optional["Container"]] = relationship(
        "Container", back_populates="children", remote_side=[id],
    )
    children: Mapped[List["Container"]] = relationship(
        "Container", back_populates="parent",
        cascade="all, delete-orphan", passive_deletes=True,
    )
    items: Mapped[List["Item"]] = relationship(
        "Item", back_populates="container",
        cascade="all, delete-orphan", passive_deletes=True,
    )

    # Sibling canonical-name uniqueness scoped to live rows. Soft-deleted
    # entries don't participate so a name freed by deletion can be reused.
    __table_args__ = (
        Index(
            "uq_container_sibling_canonical_live",
            "parent_id", "canonical_name",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("depth >= 0", name="ck_container_depth_positive"),
        Index("ix_container_path_prefix", "path"),
    )

    def __repr__(self) -> str:
        return f"<Container(id={self.id}, name={self.name}, path={self.path})>"


# --- Item ---------------------------------------------------------------

class Item(Base):
    """
    Leaf node — a thing stored inside exactly one container.
    """
    __tablename__ = "items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    path: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True, index=True)

    container_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("containers.id", ondelete="CASCADE"), nullable=False, index=True,
    )

    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    item_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # v2 additions
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    container: Mapped["Container"] = relationship("Container", back_populates="items")

    __table_args__ = (
        Index(
            "uq_item_canonical_in_container_live",
            "container_id", "canonical_name",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("quantity > 0", name="ck_item_quantity_positive"),
    )

    def __repr__(self) -> str:
        return f"<Item(id={self.id}, name={self.name}, path={self.path})>"


# --- Image / EntityImage (kept for v1 data; no MCP tools in v1) -----------

class Image(Base):
    __tablename__ = "images"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    entity_images: Mapped[List["EntityImage"]] = relationship(
        "EntityImage", back_populates="image",
        cascade="all, delete-orphan", passive_deletes=True,
    )


class EntityImage(Base):
    __tablename__ = "entity_images"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    image_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("images.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    image: Mapped["Image"] = relationship("Image", back_populates="entity_images")

    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", "image_id", name="uq_entity_image_link"),
        CheckConstraint("entity_type IN ('container', 'item')", name="ck_entity_image_type_valid"),
        Index("ix_entity_images_lookup", "entity_type", "entity_id"),
    )


# --- Aliases (v2) -------------------------------------------------------

class Alias(Base):
    """
    Alternative names that resolve to the same entity.

    "passport" → "United States Passport" without renaming the entity.
    Uniqueness is scoped to siblings within a parent container so the same
    alias can apply to different items in different rooms.
    """
    __tablename__ = "aliases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    alias: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_alias: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # parent_scope_id: parent container id for containers, container_id for items.
    # Used to scope alias uniqueness without a polymorphic join.
    parent_scope_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    __table_args__ = (
        UniqueConstraint(
            "entity_type", "parent_scope_id", "canonical_alias",
            name="uq_alias_canonical_in_scope",
        ),
        CheckConstraint("entity_type IN ('container', 'item')", name="ck_alias_type_valid"),
    )


# --- Tags (v2) ----------------------------------------------------------

class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    color: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class EntityTag(Base):
    __tablename__ = "entity_tags"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    tag_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tags.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)

    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", "tag_id", name="uq_entity_tag_link"),
        CheckConstraint("entity_type IN ('container', 'item')", name="ck_entity_tag_type_valid"),
        Index("ix_entity_tags_lookup", "entity_type", "entity_id"),
    )


# --- Embeddings (v2, optional) ------------------------------------------

class Embedding(Base):
    """
    One embedding per (entity, model_name). Vector stored as raw float32 BLOB
    (numpy.tobytes). Tiny: 384-dim float32 = 1.5KB per row.
    """
    __tablename__ = "embeddings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", "model_name", name="uq_embedding_per_entity_model"),
        CheckConstraint("entity_type IN ('container', 'item')", name="ck_embedding_type_valid"),
    )


# --- Audit log (v2) -----------------------------------------------------

class AuditLog(Base):
    """
    Append-only mutation history. Every tool call that changes state writes
    one row with before/after JSON snapshots. Foundation for ``undo`` (v1.1)
    and the ``audit_log`` MCP tool.
    """
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, index=True)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    entity_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    args_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    before_snapshot: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    after_snapshot: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
