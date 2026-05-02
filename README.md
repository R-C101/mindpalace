<div align="center">
  <img src="img/sherlock mindpalace.png" alt="MindPalace" width="600" />

  <h1>MindPalace</h1>

  <p><strong>Make your LLM remember where you kept your stuff.</strong><br/>
  Private. Local. Easy to set up. No cloud, no app, no subscriptions.</p>

  <p>
    <img src="https://img.shields.io/badge/MCP-stdio-blue?style=flat-square" alt="MCP stdio" />
    <img src="https://img.shields.io/badge/works%20with-Claude%20%7C%20Cursor%20%7C%20Goose%20%7C%20Zed-blueviolet?style=flat-square" alt="Clients" />
    <img src="https://img.shields.io/badge/local--first-SQLite-green?style=flat-square" alt="SQLite" />
    <img src="https://img.shields.io/badge/license-MIT-lightgrey?style=flat-square" alt="MIT" />
  </p>
</div>

---

Set up your entire house in a few messages. Tell your AI where your passport lives, where you put the charger, which drawer holds the batteries. From then on just ask — it knows. Great for keeping your own mental map, or helping an elderly parent who can't remember where they put things.

```
You:    Where's my passport?
Claude: It's in the left drawer of the bed in your master bedroom.

You:    I moved it to the office desk.
Claude: Updated. Want me to mark it as recently seen?
```

---

## Quick start

```bash
git clone https://github.com/R-C101/mindpalace.git
cd mindpalace
uv sync
uv run python -m mindpalace init     # creates ./data/mindpalace.db
uv run python -m mindpalace doctor   # health check
```

**Wire to Claude Code:**
```bash
claude mcp add mindpalace -- uv --directory $(pwd) run python -m mindpalace serve
```

**Wire to Claude Desktop** — edit `~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "mindpalace": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/mindpalace", "run", "python", "-m", "mindpalace", "serve"]
    }
  }
}
```

Restart your client. MindPalace is live.

> Works with any MCP-capable client: Claude Desktop, Claude Code, Cursor, Continue, Goose, Zed, Cline.

---

## What it does

- **33 tools** covering containers (rooms, drawers, boxes), items, search, locking, tags, aliases, and maintenance — all driven by conversation
- **Exact → alias → fuzzy → semantic** recall chain so *"passport"*, *"my travel doc"*, and even a typo like *"passprt"* all resolve to the right item
- **Staleness tracking** — knows when you last confirmed an item is still where it should be (*"I last saw this two months ago — still there?"*)
- **Advisory locking** — mark things as locked; your LLM will ask before touching them
- **Soft delete** — deleted things are recoverable for ~30 days
- **Audit log** — every change logged with before/after snapshots
- **Local SQLite** — your data never leaves your machine

---

## Setting up your home

You don't call tools by hand — just talk. The examples below show real conversations. Tool details are collapsed underneath for the curious.

### Step 1 — Initialize and add rooms

> **You:** Set up MindPalace and add my master bedroom and a home office.
>
> **Claude:** Done. Both rooms are ready under `/home`. What goes in them?

<details><summary>Behind the scenes</summary>

```
initialize_home()
create_container(parent_path=[], name="Master Bedroom", container_type="room")
create_container(parent_path=[], name="Home Office",    container_type="room")
```

</details>

### Step 2 — Add furniture and storage

> **You:** In the bedroom there's a bed with a left and right drawer, and a wardrobe with a jewelry box inside.
>
> **Claude:** Got it — bed with two drawers, wardrobe with a jewelry box nested inside it.

<details><summary>Behind the scenes</summary>

```
create_container(["Master Bedroom"],            "Bed",         "furniture")
create_container(["Master Bedroom"],            "Wardrobe",    "furniture")
create_container(["Master Bedroom","Bed"],      "Left Drawer", "drawer")
create_container(["Master Bedroom","Bed"],      "Right Drawer","drawer")
create_container(["Master Bedroom","Wardrobe"], "Jewelry Box", "box")
```

</details>

### Step 3 — Add your items

> **You:** My passport is in the left drawer of the bed. My Chanel bag and gold watch are in the wardrobe's jewelry box.
>
> **Claude:** Recorded — passport, Chanel bag, and gold watch are all logged.

<details><summary>Behind the scenes</summary>

```
add_item(["Master Bedroom","Bed","Left Drawer"],     "Passport",   description="Blue US passport")
add_item(["Master Bedroom","Wardrobe","Jewelry Box"],"Chanel Bag", description="Black quilted classic flap")
add_item(["Master Bedroom","Wardrobe","Jewelry Box"],"Gold Watch",  description="Omega Seamaster")
```

</details>

You're set. From here on, just ask.

---

## Everyday use

### Finding things

> **You:** Where's my passport?
>
> **Claude:** It's in the left drawer of the bed in your master bedroom. (Last confirmed: never — want to mark it as seen?)

> **You:** Where's my bag? *(alias for Chanel Bag)*
>
> **Claude:** Your Chanel bag is in the jewelry box in the wardrobe. I matched "bag" from an alias you'd set earlier.

> **You:** Where's my passprt? *(typo)*
>
> **Claude:** I think you mean your passport — left drawer of the bed.

<details><summary>Behind the scenes — match types</summary>

```
where_is("passport") → match_type="exact",  score=1.0
where_is("bag")      → match_type="alias",  score=0.95  (alias: bag → Chanel Bag)
where_is("passprt")  → match_type="fuzzy",  score=0.80
```

`match_type` tells Claude whether to answer confidently or hedge.

</details>

### Moving things

> **You:** Move my passport to the office desk drawer.
>
> **Claude:** Done — passport is now in the office desk drawer.

> **You:** Move the whole jewelry box into the bed's right drawer.
>
> **Claude:** Moved. The watch and Chanel bag inside it came along — paths updated in one transaction.

<details><summary>Behind the scenes</summary>

```
move_item(item_name="Passport", dest_container_path=["Home Office","Desk","Drawer"])
move_container(source_path=["Master Bedroom","Wardrobe","Jewelry Box"],
               dest_parent_path=["Master Bedroom","Bed","Right Drawer"])
```

</details>

### Browsing

> **You:** What's in the wardrobe?
>
> **Claude:** One sub-container (jewelry box) and nothing directly inside. Want me to go deeper?

> **You:** Show me the full map of my home.
>
> **Claude:** *(renders nested tree of every room, container, and item)*

<details><summary>Behind the scenes</summary>

```
what_is_inside(path=["Master Bedroom","Wardrobe"])
get_tree(max_depth=5)
```

</details>

### Shortcuts — aliases and tags

> **You:** When I say "watch" I mean my gold watch. And when I say "bag", I mean the Chanel bag.
>
> **Claude:** Noted. Both shortcuts are saved.

> **You:** Tag my passport, Chanel bag, and gold watch as valuable.
>
> **Claude:** Tagged.

> **You:** Show me all my valuables.
>
> **Claude:** Three items tagged "valuable": passport (left drawer of bed), Chanel bag and gold watch (jewelry box in wardrobe).

<details><summary>Behind the scenes</summary>

```
add_alias(entity_type="item", id_or_path="Gold Watch", alias="watch")
add_alias(entity_type="item", id_or_path="Chanel Bag", alias="bag")

add_tag(entity_type="item", id_or_path="Passport",  tag="valuable")
add_tag(entity_type="item", id_or_path="Chanel Bag", tag="valuable")
add_tag(entity_type="item", id_or_path="Gold Watch", tag="valuable")

find_by_tag(tag="valuable")
```

</details>

### Locking — the "are you sure?" gate

Locking is friction, not security. A locked entity won't be mutated unless your LLM asks you first.

> **You:** Lock my jewelry box.
>
> **Claude:** Locked. I'll ask before touching anything inside it.

> **You:** Move the gold watch to the office.
>
> **Claude:** The jewelry box is locked. Still want to move the watch?
>
> **You:** Yes.
>
> **Claude:** Done.

<details><summary>Behind the scenes</summary>

```
lock_entity(entity_type="container", id_or_path=["Master Bedroom","Wardrobe","Jewelry Box"])

move_item(item_name="Gold Watch", dest_container_path=["Home Office"])
→ ENTITY_LOCKED → Claude asks you

move_item(item_name="Gold Watch", dest_container_path=["Home Office"], force=True)
→ success
```

</details>

### Freshness — *"I just checked"*

> **You:** I just confirmed my passport is still in the drawer.
>
> **Claude:** Got it — marked as seen just now.

Now when you ask where it is, Claude will say *"last confirmed today"* instead of *"I haven't verified this recently"*.

<details><summary>Behind the scenes</summary>

```
mark_seen(entity_type="item", id_or_path="Passport")
→ last_seen_at = now()
```

</details>

### Backups and history

> **You:** Make a backup before I rearrange everything.
>
> **Claude:** Snapshot saved.

> **You:** What changed in the last hour?
>
> **Claude:** *(lists recent moves, renames, additions with before/after)*

<details><summary>Behind the scenes</summary>

```
create_backup(note="before-bedroom-redo")
audit_log_entries(limit=50)
```

</details>

---

## Tool reference

| Domain | Tools |
|---|---|
| **Structure** | `create_container` `rename_container` `move_container` `delete_container` `list_children` `get_tree` |
| **Items** | `add_item` `rename_item` `move_item` `delete_item` `find_item` `list_items_in_container` `mark_seen` |
| **Recall** | `where_is` `what_is_inside` `search_items` `search_containers` |
| **Locking** | `lock_entity` `unlock_entity` `is_locked` |
| **Tags** | `add_tag` `remove_tag` `list_tags` `find_by_tag` |
| **Aliases** | `add_alias` `remove_alias` `list_aliases` |
| **Maintenance** | `health_check` `initialize_home` `audit_log_entries` `create_backup` `list_backups` `export_data` |

**33 tools.** Every mutation is validated, gated, and written to the audit log.

### Error codes

| Code | Means | What Claude does |
|---|---|---|
| `PATH_NOT_FOUND` | A path segment doesn't exist | Suggests closest match or asks which one |
| `SIBLING_CONFLICT` | Name already taken at that level | Suggests a different name |
| `ENTITY_LOCKED` | Locked entity touched without force | Surfaces the lock to you, asks to confirm |
| `INVALID_MOVE` | Moving a container into its own child | Refuses and explains |
| `ITEM_AMBIGUOUS` | Name matches items in multiple places | Lists matches with paths, asks which |
| `CONFIRM_REQUIRED` | Delete called without `confirm: true` | Shows impact count, asks you to confirm |

---

## Optional: semantic search

```bash
uv sync --extra semantic
```

Installs `sentence-transformers` (~30 MB). `where_is` will then fall back to vector similarity if exact, alias, and fuzzy all miss — useful for queries like *"the leather thing from Italy"*. Disabled by default.

---

## Server lifecycle

**You don't start it yourself** with stdio clients — your LLM client spawns it automatically. Restart the client to restart the server.

If you need to run it directly:

```bash
uv run python -m mindpalace serve                             # foreground stdio
uv run python -m mindpalace serve --transport http --port 7077 # HTTP+SSE (no auth — localhost only)
nohup uv run python -m mindpalace serve > mp.log 2>&1 &       # background
pkill -f "mindpalace serve"                                   # stop background
```

**Your data is persistent.** The DB lives at `./data/mindpalace.db`. Every restart picks up exactly where you left off.

---

## Removing MindPalace

**1. Unregister from your client first:**

```bash
# Claude Code
claude mcp remove mindpalace

# Claude Desktop — remove the "mindpalace" block from
#   ~/Library/Application Support/Claude/claude_desktop_config.json
# then restart Claude Desktop.
```

**2. Delete the repo** (your data lives inside it, so this removes everything):

```bash
cd ..
rm -rf mindpalace
```

---

## Development

```bash
uv sync --extra dev
uv run pytest                              # 10 e2e tests against live MCP server
uv run fastmcp dev mindpalace.server:mcp   # FastMCP inspector
uv run python -m mindpalace doctor         # round-trip health check

# Migrate from v1 home_memory.db
uv run python scripts/migrate_v1_to_v2.py /path/to/home_memory.db
```

---

## License

MIT.
