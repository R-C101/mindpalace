"""
Home Memory - Containers API

FastAPI router for container operations.
This is a THIN ADAPTER - no business logic here.
All mutations go through operations.py.
"""

from typing import List, Optional
import hashlib

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models import EntityType
from app.core.resolver import (
    resolve_container_path,
    get_children,
    PathNotFoundError,
    PathAmbiguousError,
)
from app.core.operations import (
    add_container,
    move_container,
    rename_container,
    lock_entity,
    delete_container,
    initialize_root,
    SiblingConflictError,
    InvalidMoveError,
    RootOperationError,
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


router = APIRouter(prefix="/containers", tags=["containers"])


# -----------------------------------------------------------------------------
# Request Schemas
# -----------------------------------------------------------------------------

class CreateContainerRequest(BaseModel):
    """Request body for creating a container."""
    parent_path: List[str] = Field(
        ...,
        description="Path segments to parent container (excluding 'home')",
        examples=[["bedroom"], ["bedroom", "closet"]],
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Name for the new container",
    )
    container_type: Optional[str] = Field(
        None,
        max_length=100,
        description="Type hint (room, drawer, box, etc.)",
    )
    description: Optional[str] = Field(
        None,
        description="Optional description",
    )


class MoveContainerRequest(BaseModel):
    """Request body for moving a container."""
    source_path: List[str] = Field(
        ...,
        description="Path segments to container to move",
    )
    destination_path: List[str] = Field(
        ...,
        description="Path segments to new parent container",
    )


class RenameContainerRequest(BaseModel):
    """Request body for renaming a container."""
    path: List[str] = Field(
        ...,
        description="Path segments to container to rename",
    )
    new_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="New name for the container",
    )


class LockContainerRequest(BaseModel):
    """Request body for locking a container."""
    path: List[str] = Field(
        ...,
        description="Path segments to container to lock",
    )
    security_level: int = Field(
        1,
        ge=0,
        le=2,
        description="Security level: 0=open, 1=restricted, 2=secret",
    )


class DeleteContainerRequest(BaseModel):
    """Request body for deleting a container."""
    path: List[str] = Field(
        ...,
        description="Path segments to container to delete",
    )


class GetContainerRequest(BaseModel):
    """Request body for getting a container."""
    path: List[str] = Field(
        ...,
        description="Path segments to container",
    )


# -----------------------------------------------------------------------------
# Response Schemas
# -----------------------------------------------------------------------------

class ContainerResponse(BaseModel):
    """Response schema for a container."""
    id: str
    name: str
    display_name: str
    canonical_name: str
    path: str
    depth: int
    container_type: Optional[str]
    description: Optional[str]
    is_locked: bool
    security_level: int
    parent_id: Optional[str]

    class Config:
        from_attributes = True


class ContainerListResponse(BaseModel):
    """Response schema for a list of containers."""
    containers: List[ContainerResponse]
    count: int


class ErrorResponse(BaseModel):
    """Standard error response."""
    error_type: str
    message: str
    context: Optional[dict] = None


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

def _container_to_response(container) -> ContainerResponse:
    """Convert SQLAlchemy Container to response schema."""
    return ContainerResponse(
        id=container.id,
        name=container.name,
        display_name=container.display_name,
        canonical_name=container.canonical_name,
        path=container.path,
        depth=container.depth,
        container_type=container.container_type,
        description=container.description,
        is_locked=container.is_locked,
        security_level=container.security_level,
        parent_id=container.parent_id,
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
    elif isinstance(e, InvalidMoveError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_type": "InvalidMoveError",
                "message": str(e),
                "context": {
                    "reason": e.reason,
                    "source_path": e.source_path,
                    "destination_path": e.destination_path,
                },
            },
        )
    elif isinstance(e, RootOperationError):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_type": "RootOperationError",
                "message": str(e),
                "context": {"operation": e.operation},
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

@router.post("/init", response_model=ContainerResponse)
def init_root(db: Session = Depends(get_db)):
    """
    Initialize the root container '/home'.
    
    Idempotent - can be called multiple times safely.
    """
    root = initialize_root(db)
    return _container_to_response(root)


@router.post("", response_model=ContainerResponse, status_code=status.HTTP_201_CREATED)
def create_container(
    request: CreateContainerRequest,
    db: Session = Depends(get_db),
    auth: Optional[AuthContext] = Depends(get_auth_context),
):
    """
    Create a new container.
    
    The container is created as a child of the parent path.
    Names must be unique among siblings (canonical matching).
    
    If parent is locked, requires X-HomeMemory-Auth header.
    """
    try:
        container = add_container(
            db,
            parent_path=request.parent_path,
            name=request.name,
            container_type=request.container_type,
            description=request.description,
            auth_context=auth,
        )
        return _container_to_response(container)
    except (
        PathNotFoundError,
        PathAmbiguousError,
        SiblingConflictError,
        EntityLockedError,
    ) as e:
        raise _handle_exception(e)


@router.post("/resolve", response_model=ContainerResponse)
def resolve_container(
    request: GetContainerRequest,
    db: Session = Depends(get_db),
):
    """
    Resolve a path to a container.
    
    Use this to look up container details by path.
    """
    try:
        container = resolve_container_path(db, request.path)
        return _container_to_response(container)
    except (PathNotFoundError, PathAmbiguousError) as e:
        raise _handle_exception(e)


@router.post("/children", response_model=ContainerListResponse)
def get_container_children(
    request: GetContainerRequest,
    db: Session = Depends(get_db),
):
    """
    Get all direct children of a container.
    """
    try:
        container = resolve_container_path(db, request.path)
        children = get_children(db, container.id)
        return ContainerListResponse(
            containers=[_container_to_response(c) for c in children],
            count=len(children),
        )
    except (PathNotFoundError, PathAmbiguousError) as e:
        raise _handle_exception(e)


@router.post("/move", response_model=ContainerResponse)
def move_container_endpoint(
    request: MoveContainerRequest,
    db: Session = Depends(get_db),
    auth: Optional[AuthContext] = Depends(get_auth_context),
):
    """
    Move a container to a new parent.
    
    Updates paths for the container and all descendants.
    Cannot move root, or move a container into itself or its descendants.
    
    If source or destination is locked, requires X-HomeMemory-Auth header.
    """
    try:
        container = move_container(
            db,
            source_path=request.source_path,
            destination_path=request.destination_path,
            auth_context=auth,
        )
        return _container_to_response(container)
    except (
        PathNotFoundError,
        PathAmbiguousError,
        SiblingConflictError,
        InvalidMoveError,
        RootOperationError,
        EntityLockedError,
    ) as e:
        raise _handle_exception(e)


@router.post("/rename", response_model=ContainerResponse)
def rename_container_endpoint(
    request: RenameContainerRequest,
    db: Session = Depends(get_db),
    auth: Optional[AuthContext] = Depends(get_auth_context),
):
    """
    Rename a container.
    
    Updates paths for the container and all descendants.
    Cannot rename root.
    
    If container is locked, requires X-HomeMemory-Auth header.
    """
    try:
        container = rename_container(
            db,
            path=request.path,
            new_name=request.new_name,
            auth_context=auth,
        )
        return _container_to_response(container)
    except (
        PathNotFoundError,
        PathAmbiguousError,
        SiblingConflictError,
        RootOperationError,
        EntityLockedError,
    ) as e:
        raise _handle_exception(e)


@router.post("/lock", response_model=ContainerResponse)
def lock_container(
    request: LockContainerRequest,
    db: Session = Depends(get_db),
):
    """
    Lock a container and set its security level.
    
    Security levels:
    - 0 = open (voice can reveal location)
    - 1 = restricted (voice confirms existence only)
    - 2 = secret (voice cannot confirm existence)
    """
    try:
        container = lock_entity(
            db,
            entity_type=EntityType.CONTAINER,
            identifier=request.path,
            security_level=request.security_level,
        )
        return _container_to_response(container)
    except (PathNotFoundError, PathAmbiguousError, ValueError) as e:
        raise _handle_exception(e)


@router.post("/delete", status_code=status.HTTP_204_NO_CONTENT)
def delete_container_endpoint(
    request: DeleteContainerRequest,
    db: Session = Depends(get_db),
    auth: Optional[AuthContext] = Depends(get_auth_context),
):
    """
    Delete a container and all its contents.
    
    This cascades to all child containers and items.
    Cannot delete root.
    
    If container is locked, requires X-HomeMemory-Auth header.
    """
    try:
        delete_container(db, path=request.path, auth_context=auth)
    except (
        PathNotFoundError,
        PathAmbiguousError,
        RootOperationError,
        EntityLockedError,
    ) as e:
        raise _handle_exception(e)

