"""Resolver invariants ported forward from v1 — these lock current behaviour."""

from __future__ import annotations

import pytest


def test_canonical_name_normalisation():
    from mindpalace.db.models import to_canonical_name

    assert to_canonical_name("TV Room") == "tvroom"
    assert to_canonical_name("tv-room") == "tvroom"
    assert to_canonical_name("tv_room") == "tvroom"
    assert to_canonical_name("TVRoom") == "tvroom"
    assert to_canonical_name("Left Drawer") == "leftdrawer"


def test_initialize_root_idempotent(session):
    from mindpalace.core.operations import initialize_root

    root1 = initialize_root(session)
    root2 = initialize_root(session)
    assert root1.id == root2.id
    assert root1.path == "/home"
    assert root1.depth == 0


def test_resolve_path_finds_nested_container(session):
    from mindpalace.core.operations import initialize_root, add_container
    from mindpalace.core.resolver import resolve_container_path

    initialize_root(session)
    add_container(session, [], "Master Bedroom")
    add_container(session, ["Master Bedroom"], "Bed")

    bed = resolve_container_path(session, ["master-bedroom", "bed"])
    assert bed.name == "Bed"
    assert bed.path == "/home/Master Bedroom/Bed"


def test_resolve_unknown_path_raises_with_available_children(session):
    from mindpalace.core.operations import initialize_root, add_container
    from mindpalace.core.resolver import resolve_container_path, PathNotFoundError

    initialize_root(session)
    add_container(session, [], "Kitchen")
    add_container(session, [], "Living Room")

    with pytest.raises(PathNotFoundError) as exc_info:
        resolve_container_path(session, ["bedroom"])

    err = exc_info.value
    assert "Kitchen" in err.available_children or "Living Room" in err.available_children


def test_sibling_canonical_uniqueness_enforced(session):
    """Two containers under the same parent cannot share a canonical name."""
    from mindpalace.core.operations import initialize_root, add_container, SiblingConflictError

    initialize_root(session)
    add_container(session, [], "TV Room")
    with pytest.raises(SiblingConflictError):
        add_container(session, [], "tv-room")  # canonicalises to same value
