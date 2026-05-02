"""Pydantic IO models shared across tool modules."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class ContainerOut(BaseModel):
    id: str
    name: str
    display_name: str
    canonical_name: str
    path: str
    depth: int
    parent_id: Optional[str] = None
    is_locked: bool
    container_type: Optional[str] = None
    description: Optional[str] = None
    version: int
    last_seen_at: Optional[datetime] = None


class ItemOut(BaseModel):
    id: str
    name: str
    display_name: str
    canonical_name: str
    path: str
    container_id: str
    is_locked: bool
    quantity: int
    item_type: Optional[str] = None
    description: Optional[str] = None
    version: int
    last_seen_at: Optional[datetime] = None


class TreeNode(BaseModel):
    kind: Literal["container", "item"]
    id: str
    name: str
    path: str
    is_locked: bool
    children: list["TreeNode"] = Field(default_factory=list)
    items: list["TreeNode"] = Field(default_factory=list)


TreeNode.model_rebuild()


class SearchHitOut(BaseModel):
    entity_type: Literal["container", "item"]
    entity_id: str
    name: str
    path: str
    score: float
    match_type: Literal["exact", "alias", "fuzzy", "semantic"]
    is_locked: bool
    last_seen_at: Optional[str] = None


def container_out(c) -> ContainerOut:
    return ContainerOut(
        id=c.id, name=c.name, display_name=c.display_name,
        canonical_name=c.canonical_name, path=c.path, depth=c.depth,
        parent_id=c.parent_id, is_locked=c.is_locked,
        container_type=c.container_type, description=c.description,
        version=c.version, last_seen_at=c.last_seen_at,
    )


def item_out(i) -> ItemOut:
    return ItemOut(
        id=i.id, name=i.name, display_name=i.display_name,
        canonical_name=i.canonical_name, path=i.path,
        container_id=i.container_id, is_locked=i.is_locked,
        quantity=i.quantity, item_type=i.item_type, description=i.description,
        version=i.version, last_seen_at=i.last_seen_at,
    )
