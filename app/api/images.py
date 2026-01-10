"""
Home Memory - Images API

FastAPI router for image operations.
This is a THIN ADAPTER - no business logic here.
All mutations go through operations.py.

Images are stored on disk (not in DB) and served via this API.
"""

import os
import uuid
import hashlib
import shutil
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.engine import get_db
from app.db.models import EntityType, Image, EntityImage
from app.core.operations import (
    add_image_to_entity,
    remove_image,
    set_primary_image,
    get_entity_images,
    get_primary_image,
    ImageNotFoundError,
    ImageNotLinkedError,
    EntityNotFoundByIdError,
    EntityLockedError,
    AuthContext,
)
from app.core.resolver import (
    PathNotFoundError,
    ItemNotFoundError,
)


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

# Image storage directory (relative to project root)
IMAGES_DIR = Path("data/images")
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

# Allowed MIME types
ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/gif",
    "image/webp",
}

# Max file size (10 MB)
MAX_FILE_SIZE = 10 * 1024 * 1024


# -----------------------------------------------------------------------------
# Auth Configuration
# -----------------------------------------------------------------------------

AUTH_PASSWORD_HASH = hashlib.sha256(b"homememory").hexdigest()


def get_auth_context(
    x_homememory_auth: Optional[str] = Header(None, alias="X-HomeMemory-Auth"),
) -> Optional[AuthContext]:
    """
    Extract auth context from request headers.
    """
    if x_homememory_auth is None:
        return None
    
    provided_hash = hashlib.sha256(x_homememory_auth.encode()).hexdigest()
    
    if provided_hash == AUTH_PASSWORD_HASH:
        return AuthContext.authenticated_full()
    
    return None


router = APIRouter(prefix="/images", tags=["images"])


# -----------------------------------------------------------------------------
# Request/Response Schemas
# -----------------------------------------------------------------------------

class ImageResponse(BaseModel):
    """Response schema for an image."""
    id: str
    filename: str
    mime_type: Optional[str]
    file_size: Optional[int]
    description: Optional[str]
    is_primary: bool = False
    url: str

    class Config:
        from_attributes = True


class ImageListResponse(BaseModel):
    """Response schema for a list of images."""
    images: List[ImageResponse]
    count: int
    primary_id: Optional[str] = None


class SetPrimaryRequest(BaseModel):
    """Request body for setting primary image."""
    entity_type: str = Field(..., description="'container' or 'item'")
    entity_id: str = Field(..., description="ID of the container or item")
    image_id: str = Field(..., description="ID of the image to set as primary")


class RemoveImageRequest(BaseModel):
    """Request body for removing an image."""
    image_id: str = Field(..., description="ID of the image to remove")


class ErrorResponse(BaseModel):
    """Standard error response."""
    error_type: str
    message: str
    context: Optional[dict] = None


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

def _image_to_response(image: Image, is_primary: bool = False) -> ImageResponse:
    """Convert SQLAlchemy Image to response schema."""
    return ImageResponse(
        id=image.id,
        filename=image.filename,
        mime_type=image.mime_type,
        file_size=image.file_size,
        description=image.description,
        is_primary=is_primary,
        url=f"/images/{image.id}",
    )


def _handle_exception(e: Exception) -> HTTPException:
    """Map domain exceptions to HTTP exceptions."""
    if isinstance(e, EntityNotFoundByIdError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_type": "EntityNotFoundByIdError",
                "message": str(e),
                "context": {
                    "entity_type": e.entity_type,
                    "entity_id": e.entity_id,
                },
            },
        )
    elif isinstance(e, PathNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_type": "PathNotFoundError",
                "message": str(e),
            },
        )
    elif isinstance(e, ItemNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_type": "ItemNotFoundError",
                "message": str(e),
            },
        )
    elif isinstance(e, ImageNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_type": "ImageNotFoundError",
                "message": str(e),
                "context": {"image_id": e.image_id},
            },
        )
    elif isinstance(e, ImageNotLinkedError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_type": "ImageNotLinkedError",
                "message": str(e),
                "context": {
                    "image_id": e.image_id,
                    "entity_type": e.entity_type,
                    "entity_id": e.entity_id,
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


def _parse_entity_type(entity_type_str: str) -> EntityType:
    """Parse entity type string to enum."""
    if entity_type_str.lower() == "container":
        return EntityType.CONTAINER
    elif entity_type_str.lower() == "item":
        return EntityType.ITEM
    else:
        raise ValueError(f"Invalid entity_type: {entity_type_str}. Must be 'container' or 'item'.")


# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------

@router.post("/upload", response_model=ImageResponse, status_code=status.HTTP_201_CREATED)
async def upload_image(
    file: UploadFile = File(...),
    entity_type: str = Form(..., description="'container' or 'item'"),
    entity_id: str = Form(..., description="ID of the container or item"),
    description: Optional[str] = Form(None),
    is_primary: bool = Form(False),
    db: Session = Depends(get_db),
    auth: Optional[AuthContext] = Depends(get_auth_context),
):
    """
    Upload an image and attach it to a container or item.
    
    The image is saved to disk and metadata stored in the database.
    Validates MIME type and file size.
    
    If entity is locked, requires X-HomeMemory-Auth header.
    """
    # Validate entity type
    try:
        entity_type_enum = _parse_entity_type(entity_type)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_type": "ValidationError", "message": str(e)},
        )
    
    # Validate MIME type
    content_type = file.content_type
    if content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_type": "InvalidFileType",
                "message": f"File type '{content_type}' not allowed. Allowed: {list(ALLOWED_MIME_TYPES)}",
            },
        )
    
    # Read file and validate size
    content = await file.read()
    file_size = len(content)
    
    if file_size > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error_type": "FileTooLarge",
                "message": f"File size {file_size} bytes exceeds maximum of {MAX_FILE_SIZE} bytes",
            },
        )
    
    # Generate unique filename
    file_ext = Path(file.filename).suffix.lower() if file.filename else ".jpg"
    unique_filename = f"{uuid.uuid4()}{file_ext}"
    file_path = IMAGES_DIR / unique_filename
    
    # Save file to disk
    with open(file_path, "wb") as f:
        f.write(content)
    
    try:
        # Create image record and link to entity
        image = add_image_to_entity(
            session=db,
            entity_type=entity_type_enum,
            entity_id=entity_id,
            file_path=str(file_path),
            filename=file.filename or unique_filename,
            mime_type=content_type,
            file_size=file_size,
            description=description,
            is_primary=is_primary,
            auth_context=auth,
        )
        
        return _image_to_response(image, is_primary=is_primary)
    
    except (EntityNotFoundByIdError, PathNotFoundError, ItemNotFoundError, EntityLockedError) as e:
        # Clean up file if DB operation failed
        if file_path.exists():
            os.remove(file_path)
        raise _handle_exception(e)
    except Exception as e:
        # Clean up file on any error
        if file_path.exists():
            os.remove(file_path)
        raise


@router.get("/{image_id}")
async def get_image(
    image_id: str,
    db: Session = Depends(get_db),
):
    """
    Serve an image file by ID.
    
    Returns the actual image file for display.
    """
    # Find the image
    image = db.query(Image).filter(Image.id == image_id).first()
    if not image:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_type": "ImageNotFoundError",
                "message": f"Image with ID '{image_id}' not found",
            },
        )
    
    # Check file exists
    file_path = Path(image.file_path)
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_type": "FileNotFound",
                "message": f"Image file not found on disk",
            },
        )
    
    return FileResponse(
        path=file_path,
        media_type=image.mime_type or "application/octet-stream",
        filename=image.filename,
    )


@router.get("/{image_id}/info", response_model=ImageResponse)
async def get_image_info(
    image_id: str,
    db: Session = Depends(get_db),
):
    """
    Get image metadata by ID.
    
    Returns image details without the actual file.
    """
    image = db.query(Image).filter(Image.id == image_id).first()
    if not image:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_type": "ImageNotFoundError",
                "message": f"Image with ID '{image_id}' not found",
            },
        )
    
    # Check if this image is primary for any entity
    is_primary = db.query(EntityImage).filter(
        EntityImage.image_id == image_id,
        EntityImage.is_primary == True,
    ).first() is not None
    
    return _image_to_response(image, is_primary=is_primary)


@router.get("/entity/{entity_type}/{entity_id}", response_model=ImageListResponse)
async def get_entity_images_endpoint(
    entity_type: str,
    entity_id: str,
    db: Session = Depends(get_db),
):
    """
    Get all images for a container or item.
    
    Returns images ordered by display_order, with primary image flagged.
    """
    try:
        entity_type_enum = _parse_entity_type(entity_type)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_type": "ValidationError", "message": str(e)},
        )
    
    # Get all entity-image links
    entity_images = db.query(EntityImage).filter(
        EntityImage.entity_type == entity_type_enum.value,
        EntityImage.entity_id == entity_id,
    ).order_by(EntityImage.display_order).all()
    
    # Build response with primary flag
    images = []
    primary_id = None
    
    for ei in entity_images:
        if ei.is_primary:
            primary_id = ei.image_id
        images.append(_image_to_response(ei.image, is_primary=ei.is_primary))
    
    return ImageListResponse(
        images=images,
        count=len(images),
        primary_id=primary_id,
    )


@router.post("/primary", response_model=ImageResponse)
async def set_primary_image_endpoint(
    request: SetPrimaryRequest,
    db: Session = Depends(get_db),
    auth: Optional[AuthContext] = Depends(get_auth_context),
):
    """
    Set an image as the primary image for an entity.
    
    If entity is locked, requires X-HomeMemory-Auth header.
    """
    try:
        entity_type_enum = _parse_entity_type(request.entity_type)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error_type": "ValidationError", "message": str(e)},
        )
    
    try:
        entity_image = set_primary_image(
            session=db,
            entity_type=entity_type_enum,
            entity_id=request.entity_id,
            image_id=request.image_id,
            auth_context=auth,
        )
        
        return _image_to_response(entity_image.image, is_primary=True)
    
    except (EntityNotFoundByIdError, ImageNotLinkedError, PathNotFoundError, ItemNotFoundError, EntityLockedError) as e:
        raise _handle_exception(e)


@router.post("/delete", status_code=status.HTTP_204_NO_CONTENT)
async def delete_image(
    request: RemoveImageRequest,
    db: Session = Depends(get_db),
    auth: Optional[AuthContext] = Depends(get_auth_context),
):
    """
    Delete an image and remove from all entities.
    
    Also removes the file from disk.
    If any linked entity is locked, requires X-HomeMemory-Auth header.
    """
    # Find the image to get file path before deletion
    image = db.query(Image).filter(Image.id == request.image_id).first()
    if not image:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error_type": "ImageNotFoundError",
                "message": f"Image with ID '{request.image_id}' not found",
            },
        )
    
    file_path = Path(image.file_path)
    
    try:
        remove_image(db, request.image_id, auth_context=auth)
        
        # Delete file from disk
        if file_path.exists():
            os.remove(file_path)
    
    except (ImageNotFoundError, EntityLockedError) as e:
        raise _handle_exception(e)

