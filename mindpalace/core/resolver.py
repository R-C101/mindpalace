"""
MindPalace — Path Resolution.

Pure read-side lookups: path → Container, name → Item, name → Containers, …

Determinism is the contract: no AI, no fuzzy matching here, no string scoring.
All ambiguity surfaces as a structured exception for the caller (or LLM) to
handle. Fuzzy/semantic search lives in core/search.py, separate by design.

Soft-deleted entities (``deleted_at IS NOT NULL``) are filtered out of every
read. Recovery happens through a dedicated ``restore`` operation, not by
peeking past the filter.
"""

from typing import List, Optional
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from mindpalace.db.models import Container, Item, to_canonical_name


# -----------------------------------------------------------------------------
# Exception Classes
# -----------------------------------------------------------------------------

@dataclass
class PathNotFoundError(Exception):
    """
    Raised when a path segment cannot be resolved.
    
    Provides context about where resolution failed and what alternatives exist.
    """
    path_parts: List[str]
    failed_at_index: int
    failed_segment: str
    resolved_so_far: Optional["Container"]
    available_children: List[str] = field(default_factory=list)
    
    def __str__(self) -> str:
        resolved_path = "/" + "/".join(self.path_parts[:self.failed_at_index])
        if not resolved_path or resolved_path == "/":
            resolved_path = "(root)"
        return (
            f"Path segment '{self.failed_segment}' not found. "
            f"Resolved up to: {resolved_path}. "
            f"Available: {self.available_children or 'none'}"
        )


@dataclass
class PathAmbiguousError(Exception):
    """
    Raised when a path segment matches multiple containers.
    
    This should not happen if sibling uniqueness is enforced,
    but we handle it defensively.
    """
    path_parts: List[str]
    failed_at_index: int
    failed_segment: str
    matching_containers: List["Container"]
    
    def __str__(self) -> str:
        matches = [c.name for c in self.matching_containers]
        return (
            f"Ambiguous path segment '{self.failed_segment}' "
            f"matches {len(matches)} containers: {matches}"
        )


@dataclass
class ItemNotFoundError(Exception):
    """
    Raised when an item name cannot be found.
    """
    item_name: str
    searched_in: Optional["Container"] = None
    
    def __str__(self) -> str:
        if self.searched_in:
            return f"Item '{self.item_name}' not found in container '{self.searched_in.name}'"
        return f"Item '{self.item_name}' not found anywhere"


@dataclass
class ItemAmbiguousError(Exception):
    """
    Raised when an item name matches multiple items.
    
    Provides the matching items so caller can ask user to disambiguate.
    """
    item_name: str
    matching_items: List["Item"]
    
    def __str__(self) -> str:
        locations = [item.path for item in self.matching_items]
        return (
            f"Item '{self.item_name}' is ambiguous. "
            f"Found {len(locations)} matches: {locations}"
        )


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

def get_children(
    session: Session,
    container_id: Optional[str],
) -> List[Container]:
    """
    Get all direct child containers of a given container.
    
    Args:
        session: SQLAlchemy session
        container_id: Parent container ID, or None to get root containers
        
    Returns:
        List of child Container instances
    """
    query = session.query(Container).filter(
        Container.parent_id == container_id,
        Container.deleted_at.is_(None),
    ).order_by(Container.name)

    return query.all()


def get_child_names(
    session: Session,
    container_id: Optional[str],
) -> List[str]:
    """
    Get names of all direct child containers (for error messages).
    
    Args:
        session: SQLAlchemy session
        container_id: Parent container ID
        
    Returns:
        List of child container names (lowercase for display)
    """
    children = get_children(session, container_id)
    return [c.name for c in children]


def get_root_container(session: Session) -> Optional[Container]:
    """
    Get the root container (path = '/home').
    
    Returns:
        Root Container or None if not initialized
    """
    return session.query(Container).filter(
        Container.path == "/home",
        Container.deleted_at.is_(None),
    ).first()


def find_child_by_name(
    session: Session,
    parent_id: Optional[str],
    name: str,
) -> List[Container]:
    """
    Find child containers matching a name using canonical matching.
    
    Canonical matching normalizes names so that:
    - "TV Room", "TVRoom", "tv-room", "tv_room" all match
    
    Args:
        session: SQLAlchemy session
        parent_id: Parent container ID (None for root level)
        name: Name to search for (will be canonicalized)
        
    Returns:
        List of matching containers (should be 0 or 1 if schema is correct)
    """
    canonical = to_canonical_name(name)
    return session.query(Container).filter(
        Container.parent_id == parent_id,
        Container.canonical_name == canonical,
        Container.deleted_at.is_(None),
    ).all()


def get_items_in_container(
    session: Session,
    container_id: str,
) -> List[Item]:
    """
    Get all items directly inside a container.
    
    Args:
        session: SQLAlchemy session
        container_id: Container ID
        
    Returns:
        List of Item instances
    """
    return session.query(Item).filter(
        Item.container_id == container_id,
        Item.deleted_at.is_(None),
    ).order_by(Item.name).all()


# -----------------------------------------------------------------------------
# Path Resolution
# -----------------------------------------------------------------------------

def resolve_container_path(
    session: Session,
    path_parts: List[str],
) -> Container:
    """
    Resolve a list of path segments to a Container instance.
    
    Resolution starts from the root container ('/home') and walks
    down the tree, matching each segment case-insensitively.
    
    Args:
        session: SQLAlchemy session
        path_parts: List of path segments (e.g., ['bedroom', 'drawer'])
                   Do NOT include 'home' - resolution starts from /home
        
    Returns:
        The resolved Container instance
        
    Raises:
        PathNotFoundError: If any segment cannot be resolved
        PathAmbiguousError: If any segment matches multiple containers
        
    Example:
        >>> resolve_container_path(session, ['bedroom', 'left drawer'])
        <Container(path='/home/bedroom/left drawer')>
    """
    # Start from root
    root = get_root_container(session)
    if root is None:
        raise PathNotFoundError(
            path_parts=path_parts,
            failed_at_index=0,
            failed_segment="home",
            resolved_so_far=None,
            available_children=[],
        )
    
    # If no path parts, return root
    if not path_parts:
        return root
    
    current = root
    
    for idx, segment in enumerate(path_parts):
        # Find matching children
        matches = find_child_by_name(session, current.id, segment)
        
        if len(matches) == 0:
            # Not found - provide helpful error
            available = get_child_names(session, current.id)
            raise PathNotFoundError(
                path_parts=path_parts,
                failed_at_index=idx,
                failed_segment=segment,
                resolved_so_far=current,
                available_children=available,
            )
        
        if len(matches) > 1:
            # Ambiguous - should not happen with proper schema
            raise PathAmbiguousError(
                path_parts=path_parts,
                failed_at_index=idx,
                failed_segment=segment,
                matching_containers=matches,
            )
        
        # Exactly one match - continue
        current = matches[0]
    
    return current


def resolve_path_string(
    session: Session,
    path: str,
) -> Container:
    """
    Resolve a path string (e.g., '/home/bedroom/drawer') to a Container.
    
    Convenience wrapper around resolve_container_path that handles
    string parsing and normalization.
    
    Args:
        session: SQLAlchemy session
        path: Full path string starting with '/home'
        
    Returns:
        The resolved Container instance
        
    Raises:
        PathNotFoundError: If path cannot be resolved
        PathAmbiguousError: If path is ambiguous
        ValueError: If path format is invalid
    """
    # Normalize path
    path = path.strip()
    
    if not path.startswith("/home"):
        raise ValueError(f"Path must start with '/home', got: {path}")
    
    # Remove '/home' prefix and split
    remainder = path[5:]  # Remove '/home'
    
    if not remainder or remainder == "/":
        # Just '/home'
        return resolve_container_path(session, [])
    
    # Remove leading slash and split
    parts = remainder.lstrip("/").split("/")
    
    # Filter out empty parts (from double slashes)
    parts = [p.strip() for p in parts if p.strip()]
    
    return resolve_container_path(session, parts)


# -----------------------------------------------------------------------------
# Item Resolution
# -----------------------------------------------------------------------------

def resolve_item_by_name(
    session: Session,
    item_name: str,
    container: Optional[Container] = None,
) -> Item:
    """
    Find an item by name using canonical matching.
    
    Canonical matching normalizes names so that:
    - "Gold Bar", "GoldBar", "gold-bar", "gold_bar" all match
    
    If container is provided, searches only within that container.
    If container is None, searches globally (all items).
    
    Args:
        session: SQLAlchemy session
        item_name: Name to search for (will be canonicalized)
        container: Optional container to scope search
        
    Returns:
        The resolved Item instance
        
    Raises:
        ItemNotFoundError: If no matching item found
        ItemAmbiguousError: If multiple items match
        
    Example:
        >>> resolve_item_by_name(session, 'passport')
        <Item(path='/home/bedroom/drawer/passport')>
    """
    canonical = to_canonical_name(item_name)
    query = session.query(Item).filter(
        Item.canonical_name == canonical,
        Item.deleted_at.is_(None),
    )

    if container is not None:
        query = query.filter(Item.container_id == container.id)

    matches = query.all()
    
    if len(matches) == 0:
        raise ItemNotFoundError(
            item_name=item_name,
            searched_in=container,
        )
    
    if len(matches) > 1:
        raise ItemAmbiguousError(
            item_name=item_name,
            matching_items=matches,
        )
    
    return matches[0]


def resolve_item_in_path(
    session: Session,
    path_parts: List[str],
    item_name: str,
) -> Item:
    """
    Resolve a container path, then find an item within it.
    
    Convenience function that combines path resolution with item lookup.
    
    Args:
        session: SQLAlchemy session
        path_parts: Container path segments (excluding 'home')
        item_name: Name of item to find
        
    Returns:
        The resolved Item instance
        
    Raises:
        PathNotFoundError: If container path cannot be resolved
        PathAmbiguousError: If container path is ambiguous
        ItemNotFoundError: If item not found in container
        ItemAmbiguousError: If multiple items match in container
    """
    container = resolve_container_path(session, path_parts)
    return resolve_item_by_name(session, item_name, container)


def find_items_by_name_global(
    session: Session,
    item_name: str,
) -> List[Item]:
    """
    Find all items matching a name globally using canonical matching.
    
    Unlike resolve_item_by_name, this returns all matches
    without raising an error. Useful for disambiguation flows.
    
    Args:
        session: SQLAlchemy session
        item_name: Name to search for (will be canonicalized)
        
    Returns:
        List of matching Item instances (may be empty)
    """
    canonical = to_canonical_name(item_name)
    return session.query(Item).filter(
        Item.canonical_name == canonical,
        Item.deleted_at.is_(None),
    ).all()


def find_containers_by_name_global(
    session: Session,
    container_name: str,
) -> List[Container]:
    """
    Find all containers matching a name globally using canonical matching.
    
    Useful for disambiguation when user says "the drawer" and
    there are multiple drawers in different locations.
    
    Args:
        session: SQLAlchemy session
        container_name: Name to search for (will be canonicalized)
        
    Returns:
        List of matching Container instances (may be empty)
    """
    canonical = to_canonical_name(container_name)
    return session.query(Container).filter(
        Container.canonical_name == canonical,
        Container.deleted_at.is_(None),
    ).all()


# -----------------------------------------------------------------------------
# Path Utilities
# -----------------------------------------------------------------------------

def get_container_ancestors(
    session: Session,
    container: Container,
) -> List[Container]:
    """
    Get all ancestors of a container (from root to parent).
    
    Uses the materialized path to efficiently find ancestors.
    
    Args:
        session: SQLAlchemy session
        container: The container to find ancestors for
        
    Returns:
        List of ancestor containers, ordered root to parent
    """
    if container.parent_id is None:
        return []
    
    # Parse path to get ancestor paths
    # e.g., '/home/bedroom/drawer' → ['/home', '/home/bedroom']
    parts = container.path.split("/")
    ancestor_paths = []
    
    current_path = ""
    for part in parts[1:-1]:  # Skip empty first and current container
        current_path += "/" + part
        ancestor_paths.append(current_path)
    
    if not ancestor_paths:
        return []
    
    # Query all ancestors in one go
    ancestors = session.query(Container).filter(
        Container.path.in_(ancestor_paths),
        Container.deleted_at.is_(None),
    ).order_by(Container.depth).all()

    return ancestors

