"""
Home Memory - Database Models

This module defines the SQLAlchemy ORM models for the Home Memory system.
Uses materialized paths for efficient hierarchical queries.
Compatible with SQLite (local) and PostgreSQL (future).
"""

import uuid
from datetime import datetime
from enum import Enum, IntEnum
from typing import Optional, List

from sqlalchemy import (
    String,
    Integer,
    Boolean,
    DateTime,
    Text,
    ForeignKey,
    UniqueConstraint,
    Index,
    CheckConstraint,
    Enum as SAEnum,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# -----------------------------------------------------------------------------
# Base Class
# -----------------------------------------------------------------------------

class Base(DeclarativeBase):
    """Base class for all models."""
    pass


# -----------------------------------------------------------------------------
# Enums
# -----------------------------------------------------------------------------

class SecurityLevel(IntEnum):
    """
    Security levels for containers and items.
    
    OPEN (0): Voice can reveal full location details.
    RESTRICTED (1): Voice confirms existence only, no location details.
    SECRET (2): Voice cannot confirm existence at all.
    """
    OPEN = 0
    RESTRICTED = 1
    SECRET = 2


class EntityType(str, Enum):
    """
    Entity types for the EntityImage join table.
    
    Used to distinguish between Container and Item references.
    String-based for readability and safer debugging.
    """
    CONTAINER = "container"
    ITEM = "item"


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

def generate_uuid() -> str:
    """Generate a new UUID string for primary keys."""
    return str(uuid.uuid4())


def to_canonical_name(name: str) -> str:
    """
    Generate canonical name from display name.
    
    Canonical names are used for identity/matching:
    - Lowercase
    - Remove spaces, hyphens, underscores
    - Deterministic and reversible-ish
    
    Examples:
        "TV Room"   -> "tvroom"
        "TVRoom"    -> "tvroom"
        "tv-room"   -> "tvroom"
        "tv_room"   -> "tvroom"
        "Left Drawer" -> "leftdrawer"
    """
    import re
    # Lowercase, remove spaces/hyphens/underscores
    canonical = name.lower()
    canonical = re.sub(r'[\s\-_]+', '', canonical)
    return canonical


# -----------------------------------------------------------------------------
# Container Model
# -----------------------------------------------------------------------------

class Container(Base):
    """
    Represents a container that can hold other containers or items.
    
    Examples: home, room, drawer, suitcase, purse, box.
    
    Uses materialized path pattern for efficient tree operations:
    - path: Full path from root (e.g., "/home/bedroom/drawer")
    - depth: Number of levels from root (root = 0)
    - parent_id: Reference to parent container (NULL for root)
    
    Supports:
    - Infinite nesting via self-referential parent_id
    - Fast subtree queries via path prefix matching
    - Security inheritance via security_level
    - Locking to prevent modifications
    """
    __tablename__ = "containers"

    # Primary key
    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )

    # Core fields
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Original/stored name of the container",
    )
    display_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Human-facing display name (can differ from name)",
    )
    canonical_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="Normalized name for matching (lowercase, no spaces/hyphens)",
    )
    
    # Materialized path fields
    path: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
        unique=True,
        index=True,
        comment="Full materialized path (e.g., /home/bedroom/drawer)",
    )
    depth: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Depth in tree (root = 0)",
    )

    # Tree structure
    parent_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("containers.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="Parent container ID (NULL for root)",
    )

    # Security fields
    is_locked: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="If locked, prevents modifications without auth",
    )
    security_level: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=SecurityLevel.OPEN,
        comment="0=open, 1=restricted, 2=secret",
    )

    # Metadata
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Optional description or notes",
    )
    container_type: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="Semantic type hint (room, drawer, box, etc.)",
    )
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # Relationships
    parent: Mapped[Optional["Container"]] = relationship(
        "Container",
        back_populates="children",
        remote_side=[id],
    )
    children: Mapped[List["Container"]] = relationship(
        "Container",
        back_populates="parent",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    items: Mapped[List["Item"]] = relationship(
        "Item",
        back_populates="container",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # Table constraints
    __table_args__ = (
        # Sibling canonical names must be unique (same parent, normalized name)
        UniqueConstraint("parent_id", "canonical_name", name="uq_container_sibling_canonical"),
        # Security level must be valid (0, 1, or 2)
        CheckConstraint(
            "security_level >= 0 AND security_level <= 2",
            name="ck_container_security_level",
        ),
        # Depth must be non-negative
        CheckConstraint("depth >= 0", name="ck_container_depth_positive"),
        # Index for efficient subtree queries using path prefix
        Index("ix_container_path_prefix", "path"),
        # Index for canonical name lookups
        Index("ix_container_canonical_lookup", "parent_id", "canonical_name"),
    )

    def __repr__(self) -> str:
        return f"<Container(id={self.id}, name={self.name}, path={self.path})>"


# -----------------------------------------------------------------------------
# Item Model
# -----------------------------------------------------------------------------

class Item(Base):
    """
    Represents a leaf node item stored inside a container.
    
    Examples: boots, passport, gold, keys, documents.
    
    Items cannot contain other items or containers.
    Each item belongs to exactly one container.
    
    Path is derived from parent container path + item name.
    """
    __tablename__ = "items"

    # Primary key
    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )

    # Core fields
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Original/stored name of the item",
    )
    display_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Human-facing display name (can differ from name)",
    )
    canonical_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="Normalized name for matching (lowercase, no spaces/hyphens)",
    )
    
    # Path (derived from container path + name)
    path: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
        unique=True,
        index=True,
        comment="Full path including item (e.g., /home/bedroom/drawer/passport)",
    )

    # Parent container (required)
    container_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("containers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Container that holds this item",
    )

    # Security fields
    is_locked: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="If locked, prevents modifications without auth",
    )
    security_level: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=SecurityLevel.OPEN,
        comment="0=open, 1=restricted, 2=secret",
    )

    # Metadata
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Optional description or notes",
    )
    quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        comment="How many of this item",
    )
    item_type: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="Semantic type hint (document, clothing, jewelry, etc.)",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    # Relationships
    container: Mapped["Container"] = relationship(
        "Container",
        back_populates="items",
    )

    # Table constraints
    __table_args__ = (
        # Item canonical names must be unique within a container
        UniqueConstraint("container_id", "canonical_name", name="uq_item_canonical_in_container"),
        # Security level must be valid
        CheckConstraint(
            "security_level >= 0 AND security_level <= 2",
            name="ck_item_security_level",
        ),
        # Quantity must be positive
        CheckConstraint("quantity > 0", name="ck_item_quantity_positive"),
        # Index for canonical name lookups
        Index("ix_item_canonical_lookup", "container_id", "canonical_name"),
    )

    def __repr__(self) -> str:
        return f"<Item(id={self.id}, name={self.name}, path={self.path})>"


# -----------------------------------------------------------------------------
# Image Model
# -----------------------------------------------------------------------------

class Image(Base):
    """
    Stores image metadata for containers and items.
    
    Images are stored as file paths or URLs.
    Multiple images can be associated with any container or item.
    
    The actual image binary is stored on disk, not in the database.
    """
    __tablename__ = "images"

    # Primary key
    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )

    # Image storage
    file_path: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
        comment="Path to image file on disk or URL",
    )
    
    # Metadata
    filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Original filename",
    )
    mime_type: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="MIME type (image/jpeg, image/png, etc.)",
    )
    file_size: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="File size in bytes",
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Optional description or caption",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    # Relationships
    entity_images: Mapped[List["EntityImage"]] = relationship(
        "EntityImage",
        back_populates="image",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Image(id={self.id}, filename={self.filename})>"


# -----------------------------------------------------------------------------
# EntityImage Model (Join Table)
# -----------------------------------------------------------------------------

class EntityImage(Base):
    """
    Join table linking images to containers or items.
    
    Uses a polymorphic approach with entity_type + entity_id
    to reference either Container or Item.
    
    This allows:
    - Multiple images per container/item
    - Same image shared across multiple entities
    - Ordering of images per entity
    """
    __tablename__ = "entity_images"

    # Primary key
    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )

    # Polymorphic reference to Container or Item
    entity_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="'container' or 'item'",
    )
    entity_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
        comment="ID of the container or item",
    )

    # Image reference
    image_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("images.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Ordering
    display_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="Order for displaying multiple images",
    )
    
    # Primary image flag
    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="True if this is the primary/thumbnail image",
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=datetime.utcnow,
    )

    # Relationships
    image: Mapped["Image"] = relationship(
        "Image",
        back_populates="entity_images",
    )

    # Table constraints
    __table_args__ = (
        # Prevent duplicate image-entity associations
        UniqueConstraint(
            "entity_type", "entity_id", "image_id",
            name="uq_entity_image_link",
        ),
        # Entity type must be valid
        CheckConstraint(
            "entity_type IN ('container', 'item')",
            name="ck_entity_image_type_valid",
        ),
        # Composite index for looking up images by entity
        Index("ix_entity_images_lookup", "entity_type", "entity_id"),
    )

    def __repr__(self) -> str:
        return f"<EntityImage({self.entity_type}:{self.entity_id} -> Image:{self.image_id})>"

