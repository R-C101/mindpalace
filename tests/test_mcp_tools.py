"""End-to-end tests against the live FastMCP server using the in-process Client."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def mcp_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Spin up a fresh in-memory MindPalace server, keyed by a tmp data dir.
    Yields a callable that opens a FastMCP Client to it.
    """
    monkeypatch.setenv("MINDPALACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MINDPALACE_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("MINDPALACE_DATABASE_URL", f"sqlite:///{tmp_path}/test.db")

    import sys
    for mod in list(sys.modules):
        if mod.startswith("mindpalace"):
            del sys.modules[mod]

    from mindpalace.server import mcp
    from fastmcp import Client

    return Client(mcp)


@pytest.mark.asyncio
async def test_health_check(mcp_session):
    async with mcp_session as client:
        result = await client.call_tool("health_check", {})
        data = result.structured_content or result.data
        assert data["status"] == "ok"
        assert data["tool_count"] >= 30
        assert data["root_initialized"] is False  # fresh DB


@pytest.mark.asyncio
async def test_happy_path_create_and_recall(mcp_session):
    """The end-to-end memory loop the user actually cares about."""
    async with mcp_session as client:
        await client.call_tool("initialize_home", {})
        await client.call_tool("create_container", {
            "parent_path": [], "name": "Master Bedroom", "container_type": "room",
        })
        await client.call_tool("create_container", {
            "parent_path": ["Master Bedroom"], "name": "Drawer",
        })
        await client.call_tool("add_item", {
            "container_path": ["Master Bedroom", "Drawer"],
            "name": "Passport",
            "description": "Blue US passport",
        })

        result = await client.call_tool("where_is", {"query": "passport"})
        hits = result.structured_content["result"] if result.structured_content else result.data
        assert len(hits) == 1
        assert hits[0]["match_type"] == "exact"
        assert hits[0]["path"].endswith("/Passport")


@pytest.mark.asyncio
async def test_locked_entity_requires_force(mcp_session):
    """ENTITY_LOCKED is the headline error the LLM must surface to users."""
    from fastmcp.exceptions import ToolError

    async with mcp_session as client:
        await client.call_tool("initialize_home", {})
        await client.call_tool("create_container", {"parent_path": [], "name": "Safe"})
        await client.call_tool("lock_entity", {"entity_type": "container", "id_or_path": ["Safe"]})

        with pytest.raises(ToolError) as exc:
            await client.call_tool("add_item", {
                "container_path": ["Safe"], "name": "Passport",
            })
        assert "ENTITY_LOCKED" in str(exc.value)

        # With force=True, the same call succeeds.
        result = await client.call_tool("add_item", {
            "container_path": ["Safe"], "name": "Passport", "force": True,
        })
        data = result.structured_content or result.data
        assert data["name"] == "Passport"


@pytest.mark.asyncio
async def test_path_not_found_returns_structured_error(mcp_session):
    from fastmcp.exceptions import ToolError

    async with mcp_session as client:
        await client.call_tool("initialize_home", {})
        await client.call_tool("create_container", {"parent_path": [], "name": "Kitchen"})

        with pytest.raises(ToolError) as exc:
            await client.call_tool("create_container", {
                "parent_path": ["Bedroom"], "name": "Drawer",
            })
        assert "PATH_NOT_FOUND" in str(exc.value)


# --- Verification scenarios (Phase 9) -----------------------------------

@pytest.mark.asyncio
async def test_scenario_full_memory_loop(mcp_session):
    """Agent 1 — happy path. The actual user story."""
    async with mcp_session as client:
        await client.call_tool("initialize_home", {})
        await client.call_tool("create_container", {
            "parent_path": [], "name": "Master Bedroom", "container_type": "room",
        })
        await client.call_tool("create_container", {
            "parent_path": ["Master Bedroom"], "name": "Bed", "container_type": "furniture",
        })
        await client.call_tool("create_container", {
            "parent_path": ["Master Bedroom", "Bed"], "name": "Left Drawer",
            "container_type": "drawer",
        })
        await client.call_tool("add_item", {
            "container_path": ["Master Bedroom", "Bed", "Left Drawer"],
            "name": "Passport",
        })

        # where_is finds it
        r = await client.call_tool("where_is", {"query": "passport"})
        hits = (r.structured_content or {}).get("result") or r.data
        assert len(hits) == 1
        original_path = hits[0]["path"]
        assert original_path.endswith("/Passport")

        # move it
        await client.call_tool("create_container", {"parent_path": [], "name": "Safe"})
        await client.call_tool("move_item", {
            "item_name": "Passport",
            "dest_container_path": ["Safe"],
        })

        # where_is reflects the new location
        r = await client.call_tool("where_is", {"query": "passport"})
        hits = (r.structured_content or {}).get("result") or r.data
        assert hits[0]["path"] == "/home/Safe/Passport"


@pytest.mark.asyncio
async def test_scenario_error_surfaces_are_structured(mcp_session):
    """Agent 2 — every error must be a code the LLM can branch on."""
    from fastmcp.exceptions import ToolError

    async with mcp_session as client:
        await client.call_tool("initialize_home", {})

        # ROOT_OPERATION_FORBIDDEN
        with pytest.raises(ToolError) as exc:
            await client.call_tool("delete_container", {"path": [], "confirm": True})
        assert "ROOT_OPERATION_FORBIDDEN" in str(exc.value)

        # SIBLING_CONFLICT
        await client.call_tool("create_container", {"parent_path": [], "name": "Office"})
        with pytest.raises(ToolError) as exc:
            await client.call_tool("create_container", {"parent_path": [], "name": "office"})
        assert "SIBLING_CONFLICT" in str(exc.value)

        # INVALID_MOVE (cannot move container into its own descendant)
        await client.call_tool("create_container", {"parent_path": ["Office"], "name": "Drawer"})
        with pytest.raises(ToolError) as exc:
            await client.call_tool("move_container", {
                "source_path": ["Office"], "dest_parent_path": ["Office", "Drawer"],
            })
        assert "INVALID_MOVE" in str(exc.value)


@pytest.mark.asyncio
async def test_scenario_recall_quality_via_tags_and_aliases(mcp_session):
    """Agent 3 — recall covers exact, alias, fuzzy without manual remix."""
    async with mcp_session as client:
        await client.call_tool("initialize_home", {})
        await client.call_tool("create_container", {"parent_path": [], "name": "Closet"})
        result = await client.call_tool("add_item", {
            "container_path": ["Closet"], "name": "Black Leather Boots",
        })
        item_data = result.structured_content or result.data
        item_id = item_data["id"]

        # Tag it
        await client.call_tool("add_tag", {
            "entity_type": "item", "id_or_path": "Black Leather Boots", "tag": "winter",
        })

        # Alias it
        await client.call_tool("add_alias", {
            "entity_type": "item", "id_or_path": "Black Leather Boots", "alias": "boots",
        })

        # Tag query
        tagged = await client.call_tool("find_by_tag", {"tag": "winter"})
        rows = (tagged.structured_content or {}).get("result") or tagged.data
        assert any(r["entity_id"] == item_id for r in rows)

        # Alias resolves through where_is
        r = await client.call_tool("where_is", {"query": "boots"})
        hits = (r.structured_content or {}).get("result") or r.data
        assert len(hits) >= 1
        # Match type should be alias (since "boots" isn't the canonical name)
        assert hits[0]["match_type"] == "alias"


@pytest.mark.asyncio
async def test_scenario_destructive_ops_with_backup(mcp_session):
    """Agent 4 — soft-delete is reversible, backups are real."""
    async with mcp_session as client:
        await client.call_tool("initialize_home", {})
        await client.call_tool("create_container", {"parent_path": [], "name": "Office"})
        await client.call_tool("add_item", {"container_path": ["Office"], "name": "Stapler"})

        backup = await client.call_tool("create_backup", {"note": "before-delete"})
        backup_meta = backup.structured_content or backup.data
        assert backup_meta["sha256"]

        # delete — soft, so still recoverable
        await client.call_tool("delete_container", {"path": ["Office"], "confirm": True})

        # the resolver no longer sees Office
        from fastmcp.exceptions import ToolError
        with pytest.raises(ToolError) as exc:
            await client.call_tool("list_children", {"path": ["Office"]})
        assert "PATH_NOT_FOUND" in str(exc.value)

        # but the audit log captured the deletion
        log = await client.call_tool("audit_log_entries", {"tool_name": "delete_container"})
        entries = (log.structured_content or {}).get("result") or log.data
        assert len(entries) >= 1


@pytest.mark.asyncio
async def test_lock_container_accepts_stringified_array(mcp_session):
    """
    Regression: some MCP clients (Claude Code) stringify arrays when a
    parameter's schema is ``list[str] | str``. The tool must coerce a
    JSON-array string back to a list before passing to the operations layer.
    """
    async with mcp_session as client:
        await client.call_tool("initialize_home", {})
        await client.call_tool("create_container", {"parent_path": [], "name": "Vault"})

        # The pathological shape: array sent as a JSON string.
        result = await client.call_tool("lock_entity", {
            "entity_type": "container",
            "id_or_path": '["Vault"]',
        })
        data = result.structured_content or result.data
        assert data["is_locked"] is True
        assert data["path"] == "/home/Vault"

        # Same shape works for unlock.
        result = await client.call_tool("unlock_entity", {
            "entity_type": "container",
            "id_or_path": '["Vault"]',
        })
        data = result.structured_content or result.data
        assert data["is_locked"] is False


@pytest.mark.asyncio
async def test_scenario_staleness_via_mark_seen(mcp_session):
    """Agent 5 — last_seen_at surfaces in where_is responses."""
    async with mcp_session as client:
        await client.call_tool("initialize_home", {})
        await client.call_tool("create_container", {"parent_path": [], "name": "Drawer"})
        await client.call_tool("add_item", {"container_path": ["Drawer"], "name": "Watch"})

        # Initial where_is — last_seen_at is null
        r = await client.call_tool("where_is", {"query": "watch"})
        hits = (r.structured_content or {}).get("result") or r.data
        assert hits[0]["last_seen_at"] is None

        # Mark it seen
        await client.call_tool("mark_seen", {
            "entity_type": "item", "id_or_path": "Watch",
        })

        r = await client.call_tool("where_is", {"query": "watch"})
        hits = (r.structured_content or {}).get("result") or r.data
        assert hits[0]["last_seen_at"] is not None
