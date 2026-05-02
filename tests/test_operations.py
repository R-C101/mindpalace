"""Operations invariants ported forward from v1."""

from __future__ import annotations

import pytest


def test_add_and_resolve_item(session):
    from mindpalace.core.operations import initialize_root, add_container, add_item
    from mindpalace.core.resolver import resolve_item_in_path

    initialize_root(session)
    add_container(session, [], "Master Bedroom")
    add_container(session, ["Master Bedroom"], "Drawer")
    add_item(session, ["Master Bedroom", "Drawer"], "Passport")

    item = resolve_item_in_path(session, ["master-bedroom", "drawer"], "passport")
    assert item.name == "Passport"


def test_move_container_updates_descendants(session):
    from mindpalace.core.operations import initialize_root, add_container, move_container, add_item
    from mindpalace.core.resolver import resolve_container_path

    initialize_root(session)
    add_container(session, [], "Master Bedroom")
    add_container(session, ["Master Bedroom"], "Drawer")
    add_item(session, ["Master Bedroom", "Drawer"], "Passport")

    add_container(session, [], "Guest Bedroom")
    move_container(session, ["Master Bedroom", "Drawer"], ["Guest Bedroom"])

    moved = resolve_container_path(session, ["guest-bedroom", "drawer"])
    assert moved.path == "/home/Guest Bedroom/Drawer"
    # the item's path string must have been updated too
    [item] = moved.items
    assert item.path == "/home/Guest Bedroom/Drawer/Passport"


def test_cannot_delete_root(session):
    from mindpalace.core.operations import initialize_root, delete_container, RootOperationError

    initialize_root(session)
    with pytest.raises(RootOperationError):
        delete_container(session, [])


def test_cannot_move_into_own_descendant(session):
    from mindpalace.core.operations import initialize_root, add_container, move_container, InvalidMoveError

    initialize_root(session)
    add_container(session, [], "A")
    add_container(session, ["A"], "B")
    add_container(session, ["A", "B"], "C")

    with pytest.raises(InvalidMoveError):
        move_container(session, ["A"], ["A", "B", "C"])


# --- v2 behaviour: locking is advisory, force is the override -----------

def test_locked_container_blocks_mutation_unless_forced(session):
    from mindpalace.core.operations import (
        EntityLockedError, add_container, add_item, initialize_root, lock_entity,
    )
    from mindpalace.db.models import EntityType

    initialize_root(session)
    safe = add_container(session, [], "Safe")
    lock_entity(session, EntityType.CONTAINER, ["Safe"])

    with pytest.raises(EntityLockedError):
        add_item(session, ["Safe"], "Passport")  # default force=False

    # explicit force=True bypasses the gate
    item = add_item(session, ["Safe"], "Passport", force=True)
    assert item.name == "Passport"


def test_unlock_clears_force_requirement(session):
    from mindpalace.core.operations import (
        add_container, add_item, initialize_root, lock_entity, unlock_entity,
    )
    from mindpalace.db.models import EntityType

    initialize_root(session)
    add_container(session, [], "Safe")
    lock_entity(session, EntityType.CONTAINER, ["Safe"])
    unlock_entity(session, EntityType.CONTAINER, ["Safe"])

    item = add_item(session, ["Safe"], "Passport")  # no force needed
    assert item.name == "Passport"


# --- v2 behaviour: soft delete --------------------------------------------

def test_delete_item_is_soft(session):
    from mindpalace.core.operations import add_container, add_item, delete_item, initialize_root
    from mindpalace.core.resolver import ItemNotFoundError, resolve_item_by_name

    initialize_root(session)
    add_container(session, [], "Drawer")
    add_item(session, ["Drawer"], "Boots")
    delete_item(session, "Boots")

    # Resolver must NOT see the soft-deleted item
    with pytest.raises(ItemNotFoundError):
        resolve_item_by_name(session, "Boots")

    # … but the row is still in the table with deleted_at set
    from mindpalace.db.models import Item
    rows = session.query(Item).filter(Item.canonical_name == "boots").all()
    assert len(rows) == 1
    assert rows[0].deleted_at is not None


def test_delete_container_cascades_soft_delete(session):
    from mindpalace.core.operations import (
        add_container, add_item, delete_container, initialize_root,
    )
    from mindpalace.core.resolver import PathNotFoundError, resolve_container_path
    from mindpalace.db.models import Container, Item

    initialize_root(session)
    add_container(session, [], "Bedroom")
    add_container(session, ["Bedroom"], "Drawer")
    add_item(session, ["Bedroom", "Drawer"], "Passport")

    delete_container(session, ["Bedroom"])

    with pytest.raises(PathNotFoundError):
        resolve_container_path(session, ["bedroom"])

    # Both descendant container and item are soft-deleted too.
    drawer = session.query(Container).filter(Container.canonical_name == "drawer").one()
    passport = session.query(Item).filter(Item.canonical_name == "passport").one()
    assert drawer.deleted_at is not None
    assert passport.deleted_at is not None


def test_can_recreate_name_after_soft_delete(session):
    """Soft-deleted siblings don't block re-using the same name."""
    from mindpalace.core.operations import add_container, delete_container, initialize_root

    initialize_root(session)
    add_container(session, [], "Office")
    delete_container(session, ["Office"])

    # Same canonical name is now free
    again = add_container(session, [], "office")
    assert again.canonical_name == "office"


def test_mark_seen_updates_last_seen_at(session):
    from mindpalace.core.operations import (
        add_container, add_item, initialize_root, mark_seen,
    )
    from mindpalace.db.models import EntityType

    initialize_root(session)
    add_container(session, [], "Drawer")
    item = add_item(session, ["Drawer"], "Boots")
    assert item.last_seen_at is None

    refreshed = mark_seen(session, EntityType.ITEM, "Boots")
    assert refreshed.last_seen_at is not None
