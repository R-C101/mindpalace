"""Search — exact / alias / fuzzy."""

from __future__ import annotations


def _seed(session):
    from mindpalace.core.operations import add_container, add_item, initialize_root
    initialize_root(session)
    add_container(session, [], "Master Bedroom")
    add_container(session, ["Master Bedroom"], "Drawer")
    add_item(session, ["Master Bedroom", "Drawer"], "Passport")
    add_item(session, ["Master Bedroom", "Drawer"], "Black Boots")


def test_where_is_exact(session):
    from mindpalace.core.search import MatchType, where_is
    _seed(session)

    hits = where_is(session, "passport")
    assert len(hits) == 1
    assert hits[0].match_type == MatchType.EXACT
    assert hits[0].path.endswith("/Passport")


def test_where_is_alias(session):
    from mindpalace.core.operations import initialize_root, add_container, add_item
    from mindpalace.core.search import MatchType, where_is
    from mindpalace.core.tags import add_alias
    from mindpalace.db.models import EntityType

    initialize_root(session)
    add_container(session, [], "Drawer")
    p = add_item(session, ["Drawer"], "United States Passport")
    add_alias(session, EntityType.ITEM, p.id, "passport")

    hits = where_is(session, "passport")
    assert len(hits) == 1
    assert hits[0].match_type == MatchType.ALIAS


def test_where_is_fuzzy_when_no_exact_or_alias(session):
    from mindpalace.core.search import MatchType, where_is
    _seed(session)

    hits = where_is(session, "boot")  # "Black Boots" via fuzzy
    assert len(hits) >= 1
    assert hits[0].match_type == MatchType.FUZZY
    assert "Boots" in hits[0].name
