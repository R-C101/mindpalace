"""
Home Memory - Mutation Operations

This module is the ONLY place where database writes occur.
All mutations go through these functions, which:
- Use the resolver for path lookups
- Enforce business rules
- Manage transactions
- Raise structured exceptions on failure

No direct database writes should happen outside this module.
"""

from typing import Optional, List, Union
from dataclasses import dataclass, field

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.models import Container, Item, EntityType, SecurityLevel, to_canonical_name
from app.core.resolver import (
    resolve_container_path,
    resolve_item_by_name,
    get_children,
    find_child_by_name,
    get_items_in_container,
    get_root_container,
    PathNotFoundError,
)


# -----------------------------------------------------------------------------
# Authentication Context
# -----------------------------------------------------------------------------

@dataclass
class AuthContext:
    """
    Authentication context for authorizing access to locked entities.
    
    Passed to operations to allow mutations on locked entities
    when proper authorization is provided.
    
    Security rules:
    - Voice interfaces: NEVER send auth_context (always None)
    - App/UI: Can send auth_context after password verification
    """
    authenticated: bool = False
    auth_level: int = 0  # 0=none, 1=basic, 2=full
    
    @classmethod
    def unauthenticated(cls) -> "AuthContext":
        """Create an unauthenticated context (default for voice)."""
        return cls(authenticated=False, auth_level=0)
    
    @classmethod
    def authenticated_basic(cls) -> "AuthContext":
        """Create a basic authenticated context (can access level 1)."""
        return cls(authenticated=True, auth_level=1)
    
    @classmethod
    def authenticated_full(cls) -> "AuthContext":
        """Create a fully authenticated context (can access level 2)."""
        return cls(authenticated=True, auth_level=2)
    
    def can_access(self, security_level: int) -> bool:
        """Check if this context can access a given security level."""
        if not self.authenticated:
            return security_level == 0
        return self.auth_level >= security_level


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
        Item.canonical_name == canonical
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


def _check_not_locked(
    entity: Union[Container, Item],
    auth_context: Optional[AuthContext] = None,
) -> None:
    """
    Check that an entity is not locked, or that auth_context allows access.
    
    Authorization-based locking:
    - If entity is not locked: always allowed
    - If entity is locked AND auth_context allows access: allowed
    - If entity is locked AND no/insufficient auth: EntityLockedError
    
    Args:
        entity: Container or Item to check
        auth_context: Optional auth context for accessing locked entities
        
    Raises:
        EntityLockedError: If locked and auth_context is insufficient
    """
    if not entity.is_locked:
        return
    
    # Entity is locked - check if auth_context allows access
    if auth_context is not None and auth_context.can_access(entity.security_level):
        return
    
    entity_type = "Container" if isinstance(entity, Container) else "Item"
    raise EntityLockedError(
        entity_type=entity_type,
        entity_path=entity.path,
    )


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
    # Update descendant containers
    descendant_containers = session.query(Container).filter(
        Container.path.like(f"{old_path_prefix}/%")
    ).all()
    
    for desc in descendant_containers:
        desc.path = new_path_prefix + desc.path[len(old_path_prefix):]
    
    # Update descendant items (items directly in this container + nested)
    descendant_items = session.query(Item).filter(
        Item.path.like(f"{old_path_prefix}/%")
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
    auth_context: Optional[AuthContext] = None,
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
        auth_context: Optional auth context for accessing locked parents
        
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
    _check_not_locked(parent, auth_context)
    
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
    auth_context: Optional[AuthContext] = None,
) -> Container:
    """
    Move a container to a new parent.
    
    Updates paths for the container and all its descendants
    (both containers and items).
    
    Args:
        session: SQLAlchemy session
        source_path: Path segments to container to move
        destination_path: Path segments to new parent
        auth_context: Optional auth context for accessing locked entities
        
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
    _check_not_locked(source, auth_context)
    
    # Resolve destination container
    destination = resolve_container_path(session, destination_path)
    
    # Check destination is not locked (or auth allows)
    _check_not_locked(destination, auth_context)
    
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
    
    # Update depths for descendant containers
    descendant_containers = session.query(Container).filter(
        Container.path.like(f"{new_path}/%")
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
    auth_context: Optional[AuthContext] = None,
) -> Container:
    """
    Rename a container.
    
    Updates paths for the container and all its descendants.
    
    Args:
        session: SQLAlchemy session
        path: Path segments to container to rename
        new_name: New name for the container (used in paths)
        new_display_name: Optional new display name (defaults to new_name)
        auth_context: Optional auth context for accessing locked containers
        
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
    _check_not_locked(container, auth_context)
    
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
    auth_context: Optional[AuthContext] = None,
) -> None:
    """
    Delete a container and all its contents.
    
    This cascades to all child containers and items.
    
    Args:
        session: SQLAlchemy session
        path: Path segments to container to delete
        auth_context: Optional auth context for deleting locked containers
        
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
    
    # Check not locked (or auth allows)
    _check_not_locked(container, auth_context)
    
    # Delete (cascade handles children and items)
    session.delete(container)
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
    auth_context: Optional[AuthContext] = None,
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
        auth_context: Optional auth context for adding to locked containers
        
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
    _check_not_locked(container, auth_context)
    
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
    auth_context: Optional[AuthContext] = None,
) -> Item:
    """
    Move an item to a different container.
    
    Args:
        session: SQLAlchemy session
        item_name: Name of item to move (resolved via resolver)
        destination_path: Path segments to new container
        source_container: Optional container to scope item search
        auth_context: Optional auth context for moving locked items
        
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
    _check_not_locked(item, auth_context)
    
    # Resolve destination
    destination = resolve_container_path(session, destination_path)
    
    # Check destination is not locked (or auth allows)
    _check_not_locked(destination, auth_context)
    
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
    auth_context: Optional[AuthContext] = None,
) -> Item:
    """
    Rename an item.
    
    Args:
        session: SQLAlchemy session
        item_name: Current name of item
        new_name: New name for item (used in paths)
        container: Optional container to scope item search
        new_display_name: Optional new display name (defaults to new_name)
        auth_context: Optional auth context for renaming locked items
        
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
    _check_not_locked(item, auth_context)
    
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
    auth_context: Optional[AuthContext] = None,
) -> None:
    """
    Delete an item.
    
    Args:
        session: SQLAlchemy session
        item_name: Name of item to delete
        container: Optional container to scope item search
        auth_context: Optional auth context for deleting locked items
        
    Raises:
        ItemNotFoundError: If item doesn't exist
        ItemAmbiguousError: If multiple items match name
        EntityLockedError: If item is locked and auth is insufficient
    """
    # Resolve item
    item = resolve_item_by_name(session, item_name, container)
    
    # Check not locked (or auth allows)
    _check_not_locked(item, auth_context)
    
    session.delete(item)
    session.commit()


# -----------------------------------------------------------------------------
# Security Operations
# -----------------------------------------------------------------------------

def lock_entity(
    session: Session,
    entity_type: EntityType,
    identifier: Union[List[str], str],
    security_level: int = SecurityLevel.RESTRICTED,
) -> Union[Container, Item]:
    """
    Lock an entity and set its security level.
    
    Args:
        session: SQLAlchemy session
        entity_type: EntityType.CONTAINER or EntityType.ITEM
        identifier: Path segments (for container) or name (for item)
        security_level: Security level (0=open, 1=restricted, 2=secret)
        
    Returns:
        The locked entity
        
    Raises:
        PathNotFoundError: If container doesn't exist
        ItemNotFoundError: If item doesn't exist
        ItemAmbiguousError: If item name is ambiguous
        ValueError: If security_level is invalid
        
    Example:
        >>> lock_entity(session, EntityType.CONTAINER, ['bedroom', 'safe'], 2)
        <Container(path='/home/bedroom/safe', is_locked=True, security_level=2)>
    """
    # Validate security level
    if security_level not in (0, 1, 2):
        raise ValueError(f"Invalid security_level: {security_level}. Must be 0, 1, or 2.")
    
    if entity_type == EntityType.CONTAINER:
        if not isinstance(identifier, list):
            raise ValueError("identifier must be a list of path segments for containers")
        entity = resolve_container_path(session, identifier)
    else:
        if not isinstance(identifier, str):
            raise ValueError("identifier must be an item name string for items")
        entity = resolve_item_by_name(session, identifier)
    
    entity.is_locked = True
    entity.security_level = security_level
    
    session.commit()
    session.refresh(entity)
    
    return entity


def unlock_entity(
    session: Session,
    entity_type: EntityType,
    identifier: Union[List[str], str],
) -> Union[Container, Item]:
    """
    Unlock an entity (resets security_level to OPEN).
    
    Args:
        session: SQLAlchemy session
        entity_type: EntityType.CONTAINER or EntityType.ITEM
        identifier: Path segments (for container) or name (for item)
        
    Returns:
        The unlocked entity
    """
    if entity_type == EntityType.CONTAINER:
        entity = resolve_container_path(session, identifier)
    else:
        entity = resolve_item_by_name(session, identifier)
    
    entity.is_locked = False
    entity.security_level = SecurityLevel.OPEN
    
    session.commit()
    session.refresh(entity)
    
    return entity

