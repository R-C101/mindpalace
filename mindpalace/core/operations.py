"""
MindPalace — Mutation Operations.

The ONLY place that writes to the DB. Every public function:
    - resolves paths/names via resolver.py
    - validates invariants (sibling uniqueness, locked-entity gates)
    - bumps version + updates updated_at on touched rows
    - commits its own transaction
    - raises structured exceptions on failure

v2 changes from v1:
    - ``AuthContext`` is gone. Locked entities are gated by an explicit
      ``force: bool`` parameter that the MCP layer surfaces to the LLM.
    - ``security_level`` is gone. Locking is a single boolean.
    - ``delete_*`` is now soft-delete: sets ``deleted_at`` rather than
      ``session.delete()``.
"""

from typing import List, Optional, Union
from dataclasses import dataclass

from sqlalchemy.orm import Session

from mindpalace.db.models import (
    Container, EntityImage, EntityType, Image, Item, _utcnow, to_canonical_name,
)
from mindpalace.core.resolver import (
    find_child_by_name,
    get_children,
    get_root_container,
    resolve_container_path,
    resolve_item_by_name,
)


# -----------------------------------------------------------------------------
# Exception Classes
# -----------------------------------------------------------------------------

@dataclass
class SiblingConflictError(Exception):
    """
    Raised when a name conflicts with an existing sibling.
    """
    name: str
    parent_path: str
    existing_names: List[str]
    
    def __str__(self) -> str:
        return (
            f"Name '{self.name}' conflicts with existing sibling in '{self.parent_path}'. "
            f"Existing: {self.existing_names}"
        )


@dataclass
class ItemConflictError(Exception):
    """
    Raised when an item name conflicts within a container.
    """
    item_name: str
    container_path: str
    
    def __str__(self) -> str:
        return f"Item '{self.item_name}' already exists in '{self.container_path}'"


@dataclass
class InvalidMoveError(Exception):
    """
    Raised when a move operation is invalid.
    """
    reason: str
    source_path: str
    destination_path: str
    
    def __str__(self) -> str:
        return (
            f"Cannot move '{self.source_path}' to '{self.destination_path}': {self.reason}"
        )


@dataclass
class RootOperationError(Exception):
    """
    Raised when an operation is attempted on the root container.
    """
    operation: str
    
    def __str__(self) -> str:
        return f"Cannot {self.operation} the root container"


@dataclass
class EntityLockedError(Exception):
    """
    Raised when attempting to modify a locked entity.
    """
    entity_type: str
    entity_path: str
    
    def __str__(self) -> str:
        return f"{self.entity_type} '{self.entity_path}' is locked and cannot be modified"


@dataclass
class RootNotInitializedError(Exception):
    """
    Raised when root container doesn't exist.
    """
    def __str__(self) -> str:
        return "Root container '/home' does not exist. Initialize the database first."


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

def _check_sibling_uniqueness(
    session: Session,
    parent_id: Optional[str],
    name: str,
    exclude_id: Optional[str] = None,
) -> None:
    """
    Check that a name is unique among siblings using canonical matching.
    
    Canonical matching ensures "TV Room", "TVRoom", "tv-room" are treated
    as the same name for uniqueness purposes.
    
    Raises SiblingConflictError if conflict exists.
    """
    # Use canonical matching via find_child_by_name
    matches = find_child_by_name(session, parent_id, name)
    
    # Filter out the container being renamed (if applicable)
    if exclude_id:
        matches = [m for m in matches if m.id != exclude_id]
    
    if matches:
        # Get parent path for error message
        if parent_id:
            parent = session.query(Container).filter(Container.id == parent_id).first()
            parent_path = parent.path if parent else "/home"
        else:
            parent_path = "/"
        
        existing_names = [m.display_name for m in get_children(session, parent_id)]
        raise SiblingConflictError(
            name=name,
            parent_path=parent_path,
            existing_names=existing_names,
        )


def _check_item_uniqueness(
    session: Session,
    container_id: str,
    item_name: str,
    exclude_id: Optional[str] = None,
) -> None:
    """
    Check that an item name is unique within a container using canonical matching.
    
    Canonical matching ensures "Gold Bar", "GoldBar", "gold-bar" are treated
    as the same name for uniqueness purposes.
    
    Raises ItemConflictError if conflict exists.
    """
    canonical = to_canonical_name(item_name)
    query = session.query(Item).filter(
        Item.container_id == container_id,
        Item.canonical_name == canonical,
        Item.deleted_at.is_(None),
    )

    if exclude_id:
        query = query.filter(Item.id != exclude_id)
    
    existing = query.first()
    
    if existing:
        container = session.query(Container).filter(Container.id == container_id).first()
        raise ItemConflictError(
            item_name=item_name,
            container_path=container.path if container else "unknown",
        )


def _check_not_locked_or_force(
    entity: Union[Container, Item],
    force: bool = False,
) -> None:
    """
    Gate mutations on locked entities behind an explicit ``force`` flag.

    Locking in v2 is advisory friction, not security. The MCP layer is
    expected to surface ``ENTITY_LOCKED`` to the LLM, which then asks the
    user to confirm and re-calls with ``force=True``.
    """
    if not entity.is_locked or force:
        return
    entity_type = "Container" if isinstance(entity, Container) else "Item"
    raise EntityLockedError(entity_type=entity_type, entity_path=entity.path)


def _bump_version(entity: Union[Container, Item]) -> None:
    """Increment the optimistic-concurrency counter on a touched row."""
    entity.version = (entity.version or 0) + 1


def _update_descendant_paths(
    session: Session,
    container: Container,
    old_path_prefix: str,
    new_path_prefix: str,
) -> None:
    """
    Update paths for all descendant containers and items.
    
    Uses prefix replacement: old_path_prefix → new_path_prefix
    """
    # Update descendant containers (skip soft-deleted — they keep their old
    # path so a future restore lands them back where the user left them).
    descendant_containers = session.query(Container).filter(
        Container.path.like(f"{old_path_prefix}/%"),
        Container.deleted_at.is_(None),
    ).all()

    for desc in descendant_containers:
        desc.path = new_path_prefix + desc.path[len(old_path_prefix):]

    descendant_items = session.query(Item).filter(
        Item.path.like(f"{old_path_prefix}/%"),
        Item.deleted_at.is_(None),
    ).all()

    for item in descendant_items:
        item.path = new_path_prefix + item.path[len(old_path_prefix):]


def _is_descendant_of(
    session: Session,
    potential_descendant: Container,
    potential_ancestor: Container,
) -> bool:
    """
    Check if potential_descendant is a descendant of potential_ancestor.
    
    Uses path prefix matching for efficiency.
    """
    return potential_descendant.path.startswith(potential_ancestor.path + "/")


# -----------------------------------------------------------------------------
# Container Operations
# -----------------------------------------------------------------------------

def initialize_root(session: Session) -> Container:
    """
    Initialize the root container '/home' if it doesn't exist.
    
    This should be called once during database setup.
    
    Returns:
        The root Container (created or existing)
    """
    existing = get_root_container(session)
    if existing:
        return existing
    
    root = Container(
        name="home",
        display_name="Home",
        canonical_name=to_canonical_name("home"),
        path="/home",
        depth=0,
        parent_id=None,
        container_type="home",
        description="Root container",
    )
    session.add(root)
    session.commit()
    session.refresh(root)
    return root


def add_container(
    session: Session,
    parent_path: List[str],
    name: str,
    container_type: Optional[str] = None,
    description: Optional[str] = None,
    display_name: Optional[str] = None,
    force: bool = False,
) -> Container:
    """
    Add a new container as a child of an existing container.
    
    Args:
        session: SQLAlchemy session
        parent_path: Path segments to parent container (excluding 'home')
        name: Name for the new container (used in paths)
        container_type: Optional type hint (room, drawer, box, etc.)
        description: Optional description
        display_name: Optional display name (defaults to name)
        
    Returns:
        The created Container instance
        
    Raises:
        PathNotFoundError: If parent path doesn't exist
        SiblingConflictError: If name conflicts with existing sibling
        EntityLockedError: If parent is locked and auth is insufficient
        
    Example:
        >>> add_container(session, ['bedroom'], 'left drawer', 'drawer')
        <Container(path='/home/bedroom/left drawer')>
    """
    # Resolve parent container
    parent = resolve_container_path(session, parent_path)
    
    # Check parent is not locked (or auth allows)
    _check_not_locked_or_force(parent, force)
    
    # Check sibling uniqueness (using canonical matching)
    _check_sibling_uniqueness(session, parent.id, name)
    
    # Compute path and depth
    new_path = f"{parent.path}/{name}"
    new_depth = parent.depth + 1
    
    # Create container with display_name and canonical_name
    container = Container(
        name=name,
        display_name=display_name or name,
        canonical_name=to_canonical_name(name),
        path=new_path,
        depth=new_depth,
        parent_id=parent.id,
        container_type=container_type,
        description=description,
    )
    
    session.add(container)
    session.commit()
    session.refresh(container)
    
    return container


def move_container(
    session: Session,
    source_path: List[str],
    destination_path: List[str],
    force: bool = False,
) -> Container:
    """
    Move a container to a new parent.
    
    Updates paths for the container and all its descendants
    (both containers and items).
    
    Args:
        session: SQLAlchemy session
        source_path: Path segments to container to move
        destination_path: Path segments to new parent
        
    Returns:
        The moved Container instance with updated path
        
    Raises:
        PathNotFoundError: If source or destination doesn't exist
        RootOperationError: If trying to move root
        InvalidMoveError: If move is invalid (into itself/descendant)
        SiblingConflictError: If name conflicts at destination
        EntityLockedError: If source or destination is locked and auth is insufficient
        
    Example:
        >>> move_container(session, ['bedroom', 'suitcase'], ['closet'])
        <Container(path='/home/closet/suitcase')>
    """
    # Resolve source container
    source = resolve_container_path(session, source_path)
    
    # Cannot move root
    if source.parent_id is None:
        raise RootOperationError(operation="move")
    
    # Check source is not locked (or auth allows)
    _check_not_locked_or_force(source, force)
    
    # Resolve destination container
    destination = resolve_container_path(session, destination_path)
    
    # Check destination is not locked (or auth allows)
    _check_not_locked_or_force(destination, force)
    
    # Cannot move into itself
    if source.id == destination.id:
        raise InvalidMoveError(
            reason="Cannot move container into itself",
            source_path=source.path,
            destination_path=destination.path,
        )
    
    # Cannot move into a descendant
    if _is_descendant_of(session, destination, source):
        raise InvalidMoveError(
            reason="Cannot move container into its own descendant",
            source_path=source.path,
            destination_path=destination.path,
        )
    
    # If already at destination, nothing to do
    if source.parent_id == destination.id:
        return source
    
    # Check sibling uniqueness at destination
    _check_sibling_uniqueness(session, destination.id, source.name)
    
    # Store old path for prefix replacement
    old_path = source.path
    new_path = f"{destination.path}/{source.name}"
    
    # Update parent reference
    source.parent_id = destination.id
    
    # Update depth
    old_depth = source.depth
    new_depth = destination.depth + 1
    depth_delta = new_depth - old_depth
    source.depth = new_depth
    
    # Update source path
    source.path = new_path
    
    # Update all descendant paths
    _update_descendant_paths(session, source, old_path, new_path)
    
    # Update depths for descendant containers (live ones only)
    descendant_containers = session.query(Container).filter(
        Container.path.like(f"{new_path}/%"),
        Container.deleted_at.is_(None),
    ).all()
    for desc in descendant_containers:
        desc.depth += depth_delta
    
    session.commit()
    session.refresh(source)
    
    return source


def rename_container(
    session: Session,
    path: List[str],
    new_name: str,
    new_display_name: Optional[str] = None,
    force: bool = False,
) -> Container:
    """
    Rename a container.
    
    Updates paths for the container and all its descendants.
    
    Args:
        session: SQLAlchemy session
        path: Path segments to container to rename
        new_name: New name for the container (used in paths)
        new_display_name: Optional new display name (defaults to new_name)
        
    Returns:
        The renamed Container instance
        
    Raises:
        PathNotFoundError: If container doesn't exist
        RootOperationError: If trying to rename root
        SiblingConflictError: If new name conflicts with sibling
        EntityLockedError: If container is locked and auth is insufficient
        
    Example:
        >>> rename_container(session, ['bedroom', 'left drawer'], 'right drawer')
        <Container(path='/home/bedroom/right drawer')>
    """
    # Resolve container
    container = resolve_container_path(session, path)
    
    # Cannot rename root
    if container.parent_id is None:
        raise RootOperationError(operation="rename")
    
    # Check not locked (or auth allows)
    _check_not_locked_or_force(container, force)
    
    new_canonical = to_canonical_name(new_name)
    
    # If canonical name unchanged, allow display name change
    if container.canonical_name == new_canonical:
        container.name = new_name
        container.display_name = new_display_name or new_name
        session.commit()
        session.refresh(container)
        return container
    
    # Check sibling uniqueness (exclude self)
    _check_sibling_uniqueness(session, container.parent_id, new_name, exclude_id=container.id)
    
    # Compute new path
    old_path = container.path
    parent_path = old_path.rsplit("/", 1)[0]
    new_path = f"{parent_path}/{new_name}"
    
    # Update name, display_name, canonical_name, and path
    container.name = new_name
    container.display_name = new_display_name or new_name
    container.canonical_name = new_canonical
    container.path = new_path
    
    # Update all descendant paths
    _update_descendant_paths(session, container, old_path, new_path)
    
    session.commit()
    session.refresh(container)
    
    return container


def delete_container(
    session: Session,
    path: List[str],
    force: bool = False,
) -> None:
    """
    Delete a container and all its contents.
    
    This cascades to all child containers and items.
    
    Args:
        session: SQLAlchemy session
        path: Path segments to container to delete
        
    Raises:
        PathNotFoundError: If container doesn't exist
        RootOperationError: If trying to delete root
        EntityLockedError: If container is locked and auth is insufficient
    """
    # Resolve container
    container = resolve_container_path(session, path)

    # Cannot delete root
    if container.parent_id is None:
        raise RootOperationError(operation="delete")

    _check_not_locked_or_force(container, force)

    # Soft-delete: mark deleted_at on this container *and every descendant*
    # (containers + items). Resolver filters deleted_at IS NULL, so the
    # subtree disappears from reads while remaining recoverable.
    now = _utcnow()
    container.deleted_at = now
    _bump_version(container)
    for desc in session.query(Container).filter(
        Container.path.like(f"{container.path}/%"),
        Container.deleted_at.is_(None),
    ).all():
        desc.deleted_at = now
        desc.version = (desc.version or 0) + 1
    for item in session.query(Item).filter(
        Item.path.like(f"{container.path}/%"),
        Item.deleted_at.is_(None),
    ).all():
        item.deleted_at = now
        item.version = (item.version or 0) + 1
    session.commit()


# -----------------------------------------------------------------------------
# Item Operations
# -----------------------------------------------------------------------------

def add_item(
    session: Session,
    container_path: List[str],
    name: str,
    description: Optional[str] = None,
    quantity: int = 1,
    item_type: Optional[str] = None,
    display_name: Optional[str] = None,
    force: bool = False,
) -> Item:
    """
    Add a new item to a container.
    
    Args:
        session: SQLAlchemy session
        container_path: Path segments to parent container (excluding 'home')
        name: Name for the new item (used in paths)
        description: Optional description
        quantity: How many of this item (default 1)
        item_type: Optional type hint (document, clothing, etc.)
        display_name: Optional display name (defaults to name)
        
    Returns:
        The created Item instance
        
    Raises:
        PathNotFoundError: If container doesn't exist
        ItemConflictError: If item name already exists in container
        EntityLockedError: If container is locked and auth is insufficient
        
    Example:
        >>> add_item(session, ['bedroom', 'drawer'], 'passport')
        <Item(path='/home/bedroom/drawer/passport')>
    """
    # Resolve container
    container = resolve_container_path(session, container_path)
    
    # Check container is not locked (or auth allows)
    _check_not_locked_or_force(container, force)
    
    # Check item uniqueness within container (using canonical matching)
    _check_item_uniqueness(session, container.id, name)
    
    # Compute path
    item_path = f"{container.path}/{name}"
    
    # Create item with display_name and canonical_name
    item = Item(
        name=name,
        display_name=display_name or name,
        canonical_name=to_canonical_name(name),
        path=item_path,
        container_id=container.id,
        description=description,
        quantity=quantity,
        item_type=item_type,
    )
    
    session.add(item)
    session.commit()
    session.refresh(item)
    
    return item


def move_item(
    session: Session,
    item_name: str,
    destination_path: List[str],
    source_container: Optional[Container] = None,
    force: bool = False,
) -> Item:
    """
    Move an item to a different container.
    
    Args:
        session: SQLAlchemy session
        item_name: Name of item to move (resolved via resolver)
        destination_path: Path segments to new container
        source_container: Optional container to scope item search
        
    Returns:
        The moved Item instance
        
    Raises:
        ItemNotFoundError: If item doesn't exist
        ItemAmbiguousError: If multiple items match name
        PathNotFoundError: If destination doesn't exist
        ItemConflictError: If item name conflicts at destination
        EntityLockedError: If item or destination is locked and auth is insufficient
        
    Example:
        >>> move_item(session, 'passport', ['closet', 'safe'])
        <Item(path='/home/closet/safe/passport')>
    """
    # Resolve item
    item = resolve_item_by_name(session, item_name, source_container)
    
    # Check item is not locked (or auth allows)
    _check_not_locked_or_force(item, force)
    
    # Resolve destination
    destination = resolve_container_path(session, destination_path)
    
    # Check destination is not locked (or auth allows)
    _check_not_locked_or_force(destination, force)
    
    # If already in destination, nothing to do
    if item.container_id == destination.id:
        return item
    
    # Check item uniqueness at destination
    _check_item_uniqueness(session, destination.id, item.name)
    
    # Update container and path
    item.container_id = destination.id
    item.path = f"{destination.path}/{item.name}"
    
    session.commit()
    session.refresh(item)
    
    return item


def rename_item(
    session: Session,
    item_name: str,
    new_name: str,
    container: Optional[Container] = None,
    new_display_name: Optional[str] = None,
    force: bool = False,
) -> Item:
    """
    Rename an item.
    
    Args:
        session: SQLAlchemy session
        item_name: Current name of item
        new_name: New name for item (used in paths)
        container: Optional container to scope item search
        new_display_name: Optional new display name (defaults to new_name)
        
    Returns:
        The renamed Item instance
        
    Raises:
        ItemNotFoundError: If item doesn't exist
        ItemAmbiguousError: If multiple items match name
        ItemConflictError: If new name conflicts in container
        EntityLockedError: If item is locked and auth is insufficient
    """
    # Resolve item
    item = resolve_item_by_name(session, item_name, container)
    
    # Check not locked (or auth allows)
    _check_not_locked_or_force(item, force)
    
    new_canonical = to_canonical_name(new_name)
    
    # If canonical name unchanged, allow display name change
    if item.canonical_name == new_canonical:
        item.name = new_name
        item.display_name = new_display_name or new_name
        session.commit()
        session.refresh(item)
        return item
    
    # Check uniqueness (exclude self)
    _check_item_uniqueness(session, item.container_id, new_name, exclude_id=item.id)
    
    # Update name, display_name, canonical_name, and path
    container_path = item.path.rsplit("/", 1)[0]
    item.name = new_name
    item.display_name = new_display_name or new_name
    item.canonical_name = new_canonical
    item.path = f"{container_path}/{new_name}"
    
    session.commit()
    session.refresh(item)
    
    return item


def delete_item(
    session: Session,
    item_name: str,
    container: Optional[Container] = None,
    force: bool = False,
) -> None:
    """
    Delete an item.
    
    Args:
        session: SQLAlchemy session
        item_name: Name of item to delete
        container: Optional container to scope item search
        
    Raises:
        ItemNotFoundError: If item doesn't exist
        ItemAmbiguousError: If multiple items match name
        EntityLockedError: If item is locked and auth is insufficient
    """
    # Resolve item
    item = resolve_item_by_name(session, item_name, container)

    _check_not_locked_or_force(item, force)

    item.deleted_at = _utcnow()
    _bump_version(item)
    session.commit()


# -----------------------------------------------------------------------------
# Security Operations
# -----------------------------------------------------------------------------

def lock_entity(
    session: Session,
    entity_type: EntityType,
    identifier: Union[List[str], str],
) -> Union[Container, Item]:
    """
    Mark an entity as locked.

    Locking is advisory in v2 — it forces mutating tools to require
    ``force=True``, which the LLM is expected to surface to the user.
    """
    if entity_type == EntityType.CONTAINER:
        if not isinstance(identifier, list):
            raise ValueError("identifier must be a list of path segments for containers")
        entity = resolve_container_path(session, identifier)
    else:
        if not isinstance(identifier, str):
            raise ValueError("identifier must be an item name string for items")
        entity = resolve_item_by_name(session, identifier)

    entity.is_locked = True
    _bump_version(entity)
    session.commit()
    session.refresh(entity)
    return entity


def unlock_entity(
    session: Session,
    entity_type: EntityType,
    identifier: Union[List[str], str],
) -> Union[Container, Item]:
    """Mark an entity as unlocked (always permitted)."""
    if entity_type == EntityType.CONTAINER:
        entity = resolve_container_path(session, identifier)
    else:
        entity = resolve_item_by_name(session, identifier)

    entity.is_locked = False
    _bump_version(entity)
    session.commit()
    session.refresh(entity)
    return entity


def mark_seen(
    session: Session,
    entity_type: EntityType,
    identifier: Union[List[str], str],
) -> Union[Container, Item]:
    """Bump ``last_seen_at`` to now — "I just verified this is still here"."""
    if entity_type == EntityType.CONTAINER:
        entity = resolve_container_path(session, identifier)
    else:
        entity = resolve_item_by_name(session, identifier)
    entity.last_seen_at = _utcnow()
    session.commit()
    session.refresh(entity)
    return entity


# -----------------------------------------------------------------------------
# Image Exception Classes
# -----------------------------------------------------------------------------

@dataclass
class ImageNotFoundError(Exception):
    """
    Raised when an image is not found.
    """
    image_id: str
    
    def __str__(self) -> str:
        return f"Image with ID '{self.image_id}' not found"


@dataclass
class ImageNotLinkedError(Exception):
    """
    Raised when an image is not linked to an entity.
    """
    image_id: str
    entity_type: str
    entity_id: str
    
    def __str__(self) -> str:
        return f"Image '{self.image_id}' is not linked to {self.entity_type} '{self.entity_id}'"


@dataclass
class EntityNotFoundByIdError(Exception):
    """
    Raised when an entity cannot be found by its ID.
    """
    entity_type: str
    entity_id: str
    
    def __str__(self) -> str:
        return f"{self.entity_type} with ID '{self.entity_id}' not found"


# -----------------------------------------------------------------------------
# Image Operations
# -----------------------------------------------------------------------------

def add_image_to_entity(
    session: Session,
    entity_type: EntityType,
    entity_id: str,
    file_path: str,
    filename: str,
    mime_type: Optional[str] = None,
    file_size: Optional[int] = None,
    description: Optional[str] = None,
    is_primary: bool = False,
    force: bool = False,
) -> Image:
    """
    Add an image to a container or item.
    
    Creates an Image record and links it to the entity via EntityImage.
    
    Args:
        session: SQLAlchemy session
        entity_type: EntityType.CONTAINER or EntityType.ITEM
        entity_id: ID of the container or item
        file_path: Path to the image file on disk
        filename: Original filename
        mime_type: MIME type (image/jpeg, image/png, etc.)
        file_size: File size in bytes
        description: Optional description or caption
        is_primary: If True, set as primary image (unsets others)
        
    Returns:
        The created Image instance
        
    Raises:
        EntityNotFoundByIdError: If entity doesn't exist
        EntityLockedError: If entity is locked and auth is insufficient
    """
    # Resolve and check entity by ID
    if entity_type == EntityType.CONTAINER:
        entity = session.query(Container).filter(Container.id == entity_id).first()
        if not entity:
            raise EntityNotFoundByIdError(
                entity_type="Container",
                entity_id=entity_id,
            )
    else:
        entity = session.query(Item).filter(Item.id == entity_id).first()
        if not entity:
            raise EntityNotFoundByIdError(
                entity_type="Item",
                entity_id=entity_id,
            )
    
    # Check entity is not locked (or auth allows)
    _check_not_locked_or_force(entity, force)
    
    # If setting as primary, unset existing primary images for this entity
    if is_primary:
        existing_links = session.query(EntityImage).filter(
            EntityImage.entity_type == entity_type.value,
            EntityImage.entity_id == entity_id,
            EntityImage.is_primary == True,
        ).all()
        for link in existing_links:
            link.is_primary = False
    
    # Get next display order
    max_order = session.query(EntityImage.display_order).filter(
        EntityImage.entity_type == entity_type.value,
        EntityImage.entity_id == entity_id,
    ).order_by(EntityImage.display_order.desc()).first()
    
    next_order = (max_order[0] + 1) if max_order else 0
    
    # Create Image record
    image = Image(
        file_path=file_path,
        filename=filename,
        mime_type=mime_type,
        file_size=file_size,
        description=description,
    )
    session.add(image)
    session.flush()  # Get the image ID
    
    # Create EntityImage link
    entity_image = EntityImage(
        entity_type=entity_type.value,
        entity_id=entity_id,
        image_id=image.id,
        display_order=next_order,
        is_primary=is_primary,
    )
    session.add(entity_image)
    
    session.commit()
    session.refresh(image)
    
    return image


def remove_image(
    session: Session,
    image_id: str,
    force: bool = False,
) -> None:
    """
    Remove an image and all its entity links.
    
    Args:
        session: SQLAlchemy session
        image_id: ID of the image to remove
        
    Raises:
        ImageNotFoundError: If image doesn't exist
        EntityLockedError: If any linked entity is locked and auth is insufficient
    """
    # Find the image
    image = session.query(Image).filter(Image.id == image_id).first()
    if not image:
        raise ImageNotFoundError(image_id=image_id)
    
    # Check all linked entities for locks
    for entity_image in image.entity_images:
        if entity_image.entity_type == EntityType.CONTAINER.value:
            entity = session.query(Container).filter(
                Container.id == entity_image.entity_id
            ).first()
        else:
            entity = session.query(Item).filter(
                Item.id == entity_image.entity_id
            ).first()
        
        if entity:
            _check_not_locked_or_force(entity, force)
    
    # Delete image (cascades to entity_images)
    session.delete(image)
    session.commit()


def set_primary_image(
    session: Session,
    entity_type: EntityType,
    entity_id: str,
    image_id: str,
    force: bool = False,
) -> EntityImage:
    """
    Set an image as the primary image for an entity.
    
    Unsets any existing primary image for that entity.
    
    Args:
        session: SQLAlchemy session
        entity_type: EntityType.CONTAINER or EntityType.ITEM
        entity_id: ID of the container or item
        image_id: ID of the image to set as primary
        
    Returns:
        The updated EntityImage link
        
    Raises:
        EntityNotFoundByIdError: If entity doesn't exist
        ImageNotLinkedError: If image is not linked to entity
        EntityLockedError: If entity is locked and auth is insufficient
    """
    # Resolve and check entity by ID
    if entity_type == EntityType.CONTAINER:
        entity = session.query(Container).filter(Container.id == entity_id).first()
        if not entity:
            raise EntityNotFoundByIdError(
                entity_type="Container",
                entity_id=entity_id,
            )
    else:
        entity = session.query(Item).filter(Item.id == entity_id).first()
        if not entity:
            raise EntityNotFoundByIdError(
                entity_type="Item",
                entity_id=entity_id,
            )
    
    # Check entity is not locked (or auth allows)
    _check_not_locked_or_force(entity, force)
    
    # Find the entity-image link
    target_link = session.query(EntityImage).filter(
        EntityImage.entity_type == entity_type.value,
        EntityImage.entity_id == entity_id,
        EntityImage.image_id == image_id,
    ).first()
    
    if not target_link:
        raise ImageNotLinkedError(
            image_id=image_id,
            entity_type=entity_type.value,
            entity_id=entity_id,
        )
    
    # Unset existing primary images
    existing_primaries = session.query(EntityImage).filter(
        EntityImage.entity_type == entity_type.value,
        EntityImage.entity_id == entity_id,
        EntityImage.is_primary == True,
        EntityImage.id != target_link.id,
    ).all()
    
    for link in existing_primaries:
        link.is_primary = False
    
    # Set new primary
    target_link.is_primary = True
    
    session.commit()
    session.refresh(target_link)
    
    return target_link


def get_entity_images(
    session: Session,
    entity_type: EntityType,
    entity_id: str,
) -> List[Image]:
    """
    Get all images for an entity, ordered by display_order.
    
    Args:
        session: SQLAlchemy session
        entity_type: EntityType.CONTAINER or EntityType.ITEM
        entity_id: ID of the container or item
        
    Returns:
        List of Image instances
    """
    entity_images = session.query(EntityImage).filter(
        EntityImage.entity_type == entity_type.value,
        EntityImage.entity_id == entity_id,
    ).order_by(EntityImage.display_order).all()
    
    return [ei.image for ei in entity_images]


def get_primary_image(
    session: Session,
    entity_type: EntityType,
    entity_id: str,
) -> Optional[Image]:
    """
    Get the primary image for an entity.
    
    Args:
        session: SQLAlchemy session
        entity_type: EntityType.CONTAINER or EntityType.ITEM
        entity_id: ID of the container or item
        
    Returns:
        Primary Image or None if no primary exists
    """
    entity_image = session.query(EntityImage).filter(
        EntityImage.entity_type == entity_type.value,
        EntityImage.entity_id == entity_id,
        EntityImage.is_primary == True,
    ).first()
    
    return entity_image.image if entity_image else None

