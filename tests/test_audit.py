"""Audit log — write + read."""

from __future__ import annotations


def test_audit_record_and_list(session):
    from mindpalace.core import audit
    from mindpalace.core.operations import add_container, initialize_root

    initialize_root(session)
    c = add_container(session, [], "Office")

    audit.record(
        session,
        tool_name="create_container",
        entity_type="container",
        entity_id=c.id,
        args={"name": "Office"},
        before=None,
        after=audit.snapshot(c),
        result_summary="created",
    )
    session.commit()

    entries = audit.list_entries(session)
    assert len(entries) >= 1
    latest = entries[0]
    assert latest.tool_name == "create_container"
    assert latest.entity_id == c.id
