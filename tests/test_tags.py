"""Tags + aliases."""

from __future__ import annotations

import pytest


def test_add_and_list_tag(session):
    from mindpalace.core.operations import add_container, add_item, initialize_root
    from mindpalace.core.tags import add_tag, find_entities_by_tag, list_tags_for_entity
    from mindpalace.db.models import EntityType

    initialize_root(session)
    add_container(session, [], "Drawer")
    item = add_item(session, ["Drawer"], "Watch")

    add_tag(session, EntityType.ITEM, item.id, "valuable")
    add_tag(session, EntityType.ITEM, item.id, "Valuable")  # canonical-dedupe
    tags = list_tags_for_entity(session, EntityType.ITEM, item.id)
    assert [t.canonical_name for t in tags] == ["valuable"]

    found = find_entities_by_tag(session, "valuable")
    assert (EntityType.ITEM.value, item.id) in found


def test_remove_tag_is_idempotent(session):
    from mindpalace.core.operations import add_container, add_item, initialize_root
    from mindpalace.core.tags import add_tag, list_tags_for_entity, remove_tag
    from mindpalace.db.models import EntityType

    initialize_root(session)
    add_container(session, [], "Drawer")
    item = add_item(session, ["Drawer"], "Watch")
    add_tag(session, EntityType.ITEM, item.id, "valuable")
    remove_tag(session, EntityType.ITEM, item.id, "valuable")
    remove_tag(session, EntityType.ITEM, item.id, "valuable")  # second call: no error
    assert list_tags_for_entity(session, EntityType.ITEM, item.id) == []


def test_alias_resolves_to_entity(session):
    from mindpalace.core.operations import add_container, add_item, initialize_root
    from mindpalace.core.tags import add_alias, resolve_by_alias
    from mindpalace.db.models import EntityType

    initialize_root(session)
    add_container(session, [], "Drawer")
    passport = add_item(session, ["Drawer"], "United States Passport")

    add_alias(session, EntityType.ITEM, passport.id, "passport")
    matches = resolve_by_alias(session, "Passport")  # canonicalises identically
    assert len(matches) == 1
    assert matches[0].entity_id == passport.id


def test_alias_collision_with_canonical_name_rejected(session):
    """An alias must not shadow an existing canonical name in the same scope."""
    from mindpalace.core.operations import add_container, add_item, initialize_root
    from mindpalace.core.tags import add_alias
    from mindpalace.db.models import EntityType

    initialize_root(session)
    add_container(session, [], "Drawer")
    add_item(session, ["Drawer"], "Boots")
    watch = add_item(session, ["Drawer"], "Watch")

    with pytest.raises(ValueError):
        add_alias(session, EntityType.ITEM, watch.id, "boots")
