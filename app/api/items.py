"""
Home Memory - Items API

FastAPI router for item operations.
This is a THIN ADAPTER - no business logic here.
All mutations go through operations.py.
"""

from typing import List, Optional
import hashlib

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models import EntityType
from app.core.resolver import (
    resolve_item_by_name,
    find_items_by_name_global,
    PathNotFoundError,
    PathAmbiguousError,
    ItemNotFoundError,
    ItemAmbiguousError,
)
from app.core.operations import (
    add_item,
    move_item,
    rename_item,
    delete_item,
    lock_entity,
    SiblingConflictError,
    ItemConflictError,
    EntityLockedError,
    AuthContext,
)


# -----------------------------------------------------------------------------
# Auth Configuration
# -----------------------------------------------------------------------------

# Simple password hash for demo purposes
# In production, use proper secrets management
AUTH_PASSWORD_HASH = hashlib.sha256(b"homememory").hexdigest()


def get_auth_context(
    x_homememory_auth: Optional[str] = Header(None, alias="X-HomeMemory-Auth"),
) -> Optional[AuthContext]:
    """
    Extract auth context from request headers.
    
    Header format: X-HomeMemory-Auth: <password>
    
    Returns:
        AuthContext if valid auth provided, None otherwise
    """
    if x_homememory_auth is None:
        return None
    
    # Hash the provided password and compare
    provided_hash = hashlib.sha256(x_homememory_auth.encode()).hexdigest()
    
    if provided_hash == AUTH_PASSWORD_HASH:
        return AuthContext.authenticated_full()
    
    return None


router = APIRouter(prefix="/items", tags=["items"])


# -----------------------------------------------------------------------------
# Request Schemas
# -----------------------------------------------------------------------------

class CreateItemRequest(BaseModel):
    """Request body for creating an item."""
    container_path: List[str] = Field(
        ...,
        description="Path segments to parent container (excluding 'home')",
        examples=[["bedroom", "drawer"]],
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Name for the new item",
    )
    description: Optional[str] = Field(
        None,
        description="Optional description",
    )
    quantity: int = Field(
        1,
        ge=1,
        description="Quantity of this item",
    )
    item_type: Optional[str] = Field(
        None,
        max_length=100,
        description="Type hint (document, clothing, jewelry, etc.)",
    )


class MoveItemRequest(BaseModel):
    """Request body for moving an item."""
    item_name: str = Field(
        ...,
        description="Name of item to move",
    )
    destination_path: List[str] = Field(
        ...,
        description="Path segments to destination container",
    )


class RenameItemRequest(BaseModel):
    """Request body for renaming an item."""
    item_name: str = Field(
        ...,
        description="Current name of item",
    )
    new_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="New name for the item",
    )


class LockItemRequest(BaseModel):
    """Request body for locking an item."""
    name: str = Field(
        ...,
        description="Name of item to lock",
    )
    security_level: int = Field(
        1,
        ge=0,
        le=2,
        description="Security level: 0=open, 1=restricted, 2=secret",
    )


class DeleteItemRequest(BaseModel):
    """Request body for deleting an item."""
    name: str = Field(
        ...,
        description="Name of item to delete",
    )


# -----------------------------------------------------------------------------
# Response Schemas
# -----------------------------------------------------------------------------

class ItemResponse(BaseModel):
    """Response schema for an item."""
    id: str
    name: str
    display_name: str
    canonical_name: str
    path: str
    container_id: str
    description: Optional[str]
    quantity: int
    item_type: Optional[str]
    is_locked: bool
    security_level: int

    class Config:
        from_attributes = True


class ItemListResponse(BaseModel):
    """Response schema for a list of items."""
    items: List[ItemResponse]
    count: int


class ItemLocationResponse(BaseModel):
    """Response for item location lookup."""
    name: str
    locations: List[str]
    count: int


class ErrorResponse(BaseModel):
    """Standard error response."""
    error_type: str
    message: str
    context: Optional[dict] = None


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

def _item_to_response(item) -> ItemResponse:
    """Convert SQLAlchemy Item to response schema."""
    return ItemResponse(
        id=item.id,
        name=item.name,
        display_name=item.display_name,
        canonical_name=item.canonical_name,
        path=item.path,
        container_id=item.container_id,
        description=item.description,
        quantity=item.quantity,
        item_type=item.item_type,
        is_locked=item.is_locked,
        security_level=item.security_level,
    )


def _handle_exception(e: Exception) -> HTTPException:
    """Map domain exceptions to HTTP exceptions."""
    if isinstance(e, PathNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_type": "PathNotFoundError",
                "message": str(e),
                "context": {
                    "failed_segment": e.failed_segment,
                    "available_children": e.available_children,
                },
            },
        )
    elif isinstance(e, PathAmbiguousError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_type": "PathAmbiguousError",
                "message": str(e),
                "context": {
                    "failed_segment": e.failed_segment,
                    "matching_paths": [c.path for c in e.matching_containers],
                },
            },
        )
    elif isinstance(e, ItemNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_type": "ItemNotFoundError",
                "message": str(e),
                "context": {
                    "item_name": e.item_name,
                },
            },
        )
    elif isinstance(e, ItemAmbiguousError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_type": "ItemAmbiguousError",
                "message": str(e),
                "context": {
                    "item_name": e.item_name,
                    "matching_paths": [i.path for i in e.matching_items],
                },
            },
        )
    elif isinstance(e, ItemConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_type": "ItemConflictError",
                "message": str(e),
                "context": {
                    "item_name": e.item_name,
                    "container_path": e.container_path,
                },
            },
        )
    elif isinstance(e, SiblingConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_type": "SiblingConflictError",
                "message": str(e),
                "context": {
                    "name": e.name,
                    "existing_names": e.existing_names,
                },
            },
        )
    elif isinstance(e, EntityLockedError):
        return HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail={
                "error_type": "EntityLockedError",
                "message": str(e),
                "context": {
                    "entity_type": e.entity_type,
                    "entity_path": e.entity_path,
                },
            },
        )
    else:
        return HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error_type": "InternalError",
                "message": str(e),
            },
        )


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------

@router.post("", response_model=ItemResponse, status_code=status.HTTP_201_CREATED)
def create_item(
    request: CreateItemRequest,
    db: Session = Depends(get_db),
    auth: Optional[AuthContext] = Depends(get_auth_context),
):
    """
    Create a new item in a container.
    
    Item names must be unique within the container (canonical matching).
    
    If container is locked, requires X-HomeMemory-Auth header.
    """
    try:
        item = add_item(
            db,
            container_path=request.container_path,
            name=request.name,
            description=request.description,
            quantity=request.quantity,
            item_type=request.item_type,
            auth_context=auth,
        )
        return _item_to_response(item)
    except (
        PathNotFoundError,
        PathAmbiguousError,
        ItemConflictError,
        EntityLockedError,
    ) as e:
        raise _handle_exception(e)


@router.get("/find", response_model=ItemLocationResponse)
def find_item(
    name: str = Query(..., description="Item name to search for"),
    db: Session = Depends(get_db),
):
    """
    Find an item by name globally.
    
    Returns all locations where an item with this name exists.
    Useful for disambiguation before move operations.
    """
    items = find_items_by_name_global(db, name)
    return ItemLocationResponse(
        name=name,
        locations=[item.path for item in items],
        count=len(items),
    )


@router.get("/resolve", response_model=ItemResponse)
def resolve_item(
    name: str = Query(..., description="Item name to resolve"),
    db: Session = Depends(get_db),
):
    """
    Resolve an item by name.
    
    Returns the item if exactly one match exists.
    Returns 404 if not found, 409 if ambiguous.
    """
    try:
        item = resolve_item_by_name(db, name)
        return _item_to_response(item)
    except (ItemNotFoundError, ItemAmbiguousError) as e:
        raise _handle_exception(e)


@router.post("/move", response_model=ItemResponse)
def move_item_endpoint(
    request: MoveItemRequest,
    db: Session = Depends(get_db),
    auth: Optional[AuthContext] = Depends(get_auth_context),
):
    """
    Move an item to a different container.
    
    The item is resolved by name (canonical matching).
    
    If item or destination is locked, requires X-HomeMemory-Auth header.
    """
    try:
        item = move_item(
            db,
            item_name=request.item_name,
            destination_path=request.destination_path,
            auth_context=auth,
        )
        return _item_to_response(item)
    except (
        ItemNotFoundError,
        ItemAmbiguousError,
        PathNotFoundError,
        PathAmbiguousError,
        ItemConflictError,
        EntityLockedError,
    ) as e:
        raise _handle_exception(e)


@router.post("/rename", response_model=ItemResponse)
def rename_item_endpoint(
    request: RenameItemRequest,
    db: Session = Depends(get_db),
    auth: Optional[AuthContext] = Depends(get_auth_context),
):
    """
    Rename an item.
    
    The item is resolved by name (canonical matching).
    
    If item is locked, requires X-HomeMemory-Auth header.
    """
    try:
        item = rename_item(
            db,
            item_name=request.item_name,
            new_name=request.new_name,
            auth_context=auth,
        )
        return _item_to_response(item)
    except (
        ItemNotFoundError,
        ItemAmbiguousError,
        ItemConflictError,
        EntityLockedError,
    ) as e:
        raise _handle_exception(e)


@router.post("/lock", response_model=ItemResponse)
def lock_item(
    request: LockItemRequest,
    db: Session = Depends(get_db),
):
    """
    Lock an item and set its security level.
    
    Security levels:
    - 0 = open (voice can reveal location)
    - 1 = restricted (voice confirms existence only)
    - 2 = secret (voice cannot confirm existence)
    """
    try:
        item = lock_entity(
            db,
            entity_type=EntityType.ITEM,
            identifier=request.name,
            security_level=request.security_level,
        )
        return _item_to_response(item)
    except (ItemNotFoundError, ItemAmbiguousError, ValueError) as e:
        raise _handle_exception(e)


@router.post("/delete", status_code=status.HTTP_204_NO_CONTENT)
def delete_item_endpoint(
    request: DeleteItemRequest,
    db: Session = Depends(get_db),
    auth: Optional[AuthContext] = Depends(get_auth_context),
):
    """
    Delete an item.
    
    The item is resolved by name (canonical matching).
    
    If item is locked, requires X-HomeMemory-Auth header.
    """
    try:
        delete_item(db, item_name=request.name, auth_context=auth)
    except (
        ItemNotFoundError,
        ItemAmbiguousError,
        EntityLockedError,
    ) as e:
        raise _handle_exception(e)

