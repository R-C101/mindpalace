"""Backup + restore."""

from __future__ import annotations


def test_create_and_list_backup(session):
    from mindpalace.core import backup
    from mindpalace.core.operations import add_container, initialize_root

    initialize_root(session)
    add_container(session, [], "Office")

    meta = backup.create_backup(note="phase-3-test")
    assert meta["sha256"]
    assert meta["bytes"] > 0

    listed = backup.list_backups()
    assert any(b["backup_id"] == meta["backup_id"] for b in listed)


def test_export_data_round_trips_shape(session):
    from mindpalace.core import backup
    from mindpalace.core.operations import add_container, add_item, initialize_root

    initialize_root(session)
    add_container(session, [], "Office")
    add_item(session, ["Office"], "Stapler")

    payload = backup.export_data(session)
    assert payload["schema_version"] == 2
    assert any(c["name"] == "Office" for c in payload["containers"])
    assert any(i["name"] == "Stapler" for i in payload["items"])
