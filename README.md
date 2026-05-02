# MindPalace

> The memory layer every AI assistant wishes it had.

MindPalace is a **local-first MCP server** that lets any LLM remember
where your physical things live. It's a deterministic memory engine —
the LLM handles natural language; MindPalace owns the truth, the
hierarchy, the locks, and the recall.

> **You:** Where's my passport?
>
> **Claude:** It's in the left drawer of the bed in your master bedroom.
>
> **You:** Move it to the office desk drawer.
>
> **Claude:** Done. Want me to mark it as recently seen?

---

## What this is (and isn't)

- ✅ A 33-tool MCP server, stdio-first, runs locally on your machine.
- ✅ A SQLite-backed memory of containers (rooms, drawers, boxes, …) and items.
- ✅ Vendor-neutral: any MCP-capable client (Claude Desktop, Claude Code,
  Cursor, Continue, Goose, Zed, Cline) can drive it.
- ❌ Not an LLM. Not an app. Not a cloud service. Not a chat UI.

---

## Install

```bash
git clone https://github.com/R-C101/mindpalace.git
cd mindpalace
uv sync
uv run python -m mindpalace init     # creates ./data/mindpalace.db
uv run python -m mindpalace doctor   # round-trip health check
```

That's the whole install. You now have a working MCP server.

---

## Wire it to your LLM client

### Claude Code

```bash
claude mcp add mindpalace -- uv --directory $(pwd) run python -m mindpalace serve
```

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "mindpalace": {
      "command": "uv",
      "args": [
        "--directory", "/absolute/path/to/mindpalace",
        "run", "python", "-m", "mindpalace", "serve"
      ]
    }
  }
}
```

Restart Claude Desktop. The hammer icon will show 33 new tools.

### Cursor / Continue / Goose / Zed

Same shape — a `command + args` JSON block in the client's MCP config.
See each client's docs.

### Anything else

```bash
uv run python -m mindpalace serve --transport http --port 7077
```

…will give you HTTP+SSE on localhost. v1 has no auth, so do not expose
it beyond your machine without an SSH tunnel.

---

## Server lifecycle (start, stop, restart)

**You don't normally start it yourself** when using a stdio MCP client
(Claude Desktop, Claude Code, Cursor, …). The client spawns the server
as a subprocess each time it launches and kills it on exit. Restart
the client → fresh server.

If you want to run it standalone (debugging, HTTP transport, scripting):

```bash
# Foreground — Ctrl-C to stop
uv run python -m mindpalace serve

# HTTP/SSE on a port
uv run python -m mindpalace serve --transport http --port 7077

# Background, log to a file
nohup uv run python -m mindpalace serve > mindpalace.log 2>&1 &
# stop it with: pkill -f "mindpalace serve"
```

### Your data is persistent

The DB lives at `./data/mindpalace.db` (override with
`MINDPALACE_DB_PATH`). It survives every restart. Closing the server
doesn't lose anything — when it next starts, every container, item,
tag, alias, lock, and audit entry is right where you left it.

If you want a backup before doing something risky:

```bash
cp data/mindpalace.db data/mindpalace.backup.db
```

…or use the in-tool `create_backup` (timestamped + sha256, dumped to
`data/backups/`).

### Removing MindPalace

**Unregister from your LLM client first.**

```bash
# Claude Code
claude mcp remove mindpalace

# Claude Desktop — delete the "mindpalace" block from
#   ~/Library/Application Support/Claude/claude_desktop_config.json
# then restart Claude Desktop.

# Cursor / Continue / Goose / Zed — remove the block from their MCP config.
```

Then delete the repo. Your data lives only inside it (`./data/`), so
removing the directory removes everything:

```bash
cd ..
rm -rf mindpalace
```

If you want to keep the data but stop running the server, just
unregister from the client — no other state is left lying around on
your system.

---

## How you actually talk to it

**You don't call any tools by hand.** You just talk to your LLM the way
you already do; it picks the tools. The examples below show what a
conversation looks like — tool internals are collapsed underneath each
exchange for the curious.

### 1. First-time setup

> **You:** Set up MindPalace and add my master bedroom.
>
> **Claude:** Done. Your master bedroom is ready under `/home`. What's in it?

<details><summary>Behind the scenes</summary>

```
initialize_home()
create_container(parent_path=[], name="Master Bedroom", container_type="room")
```

</details>

### 2. Build out the room

> **You:** There's a bed with a left and a right drawer, and a wardrobe
> with a top shelf and a jewelry box.
>
> **Claude:** Got it — bed with two drawers, wardrobe with a top shelf
> and a jewelry box.

<details><summary>Behind the scenes</summary>

```
create_container(["Master Bedroom"],            "Bed",         "furniture")
create_container(["Master Bedroom"],            "Wardrobe",    "furniture")
create_container(["Master Bedroom","Bed"],      "Left Drawer", "drawer")
create_container(["Master Bedroom","Bed"],      "Right Drawer","drawer")
create_container(["Master Bedroom","Wardrobe"], "Top Shelf",   "shelf")
create_container(["Master Bedroom","Wardrobe"], "Jewelry Box", "box")
```

</details>

### 3. Add items

> **You:** My passport is in the left drawer of the bed. My Chanel bag
> is on the top shelf of the wardrobe. My gold watch and diamond
> earrings are in the jewelry box.
>
> **Claude:** Recorded — passport, Chanel bag, gold watch, and diamond
> earrings are all logged.

<details><summary>Behind the scenes</summary>

```
add_item(["Master Bedroom","Bed","Left Drawer"],     "Passport",         description="Blue US passport")
add_item(["Master Bedroom","Wardrobe","Top Shelf"],  "Chanel Bag",       description="Black quilted classic flap")
add_item(["Master Bedroom","Wardrobe","Jewelry Box"],"Gold Watch",       description="Omega Seamaster")
add_item(["Master Bedroom","Wardrobe","Jewelry Box"],"Diamond Earrings", description="1 carat each")
```

</details>

You're set. From here on you just ask.

---

## What you can say (every tool, in conversation)

Each example below is something you can literally say. The tool
internals are collapsed beneath the conversation — open them only if
you want to see the wiring.

### Asking where something is

> **You:** Where's my passport?
>
> **Claude:** It's in the left drawer of the bed in your master bedroom.

<details><summary>Behind the scenes</summary>

```
where_is(query="passport")
→ match_type="exact", score=1.0
  path="/home/Master Bedroom/Bed/Left Drawer/Passport"
```

</details>

> **You:** Where's my bag?
>
> **Claude:** Your Chanel bag is on the top shelf of the wardrobe. (You'd
> set "bag" as an alias for the Chanel bag earlier, so I'm fairly sure
> that's the one you mean.)

<details><summary>Behind the scenes</summary>

```
where_is(query="bag")
→ match_type="alias", score=0.95 → Chanel Bag
```

</details>

> **You:** Where's my passprt?  *(typo)*
>
> **Claude:** I think you mean your passport — it's in the left drawer
> of the bed in your master bedroom.

<details><summary>Behind the scenes</summary>

```
where_is(query="passprt")
→ match_type="fuzzy", score=0.80 → Passport
```

</details>

The `match_type` field — `exact` / `alias` / `fuzzy` / `semantic` — is
how Claude knows whether to answer with confidence or hedge.

### Browsing what's where

> **You:** What's in the wardrobe?
>
> **Claude:** Two sub-containers: a top shelf and a jewelry box. Nothing
> directly inside the wardrobe itself.

<details><summary>Behind the scenes</summary>

```
what_is_inside(path=["Master Bedroom","Wardrobe"])
```

</details>

> **You:** Show me the jewelry box.
>
> **Claude:** Three items: gold watch, diamond earrings, pearl necklace.

<details><summary>Behind the scenes</summary>

```
list_children(path=["Master Bedroom","Wardrobe","Jewelry Box"])
```

</details>

> **You:** Give me a full map of my home.
>
> **Claude:** *renders the nested tree…*

<details><summary>Behind the scenes</summary>

```
get_tree(max_depth=5)
```

</details>

### Moving things

> **You:** Move my red boots from the closet to the wardrobe top shelf.
>
> **Claude:** Done — red boots are now on the top shelf of the wardrobe.

<details><summary>Behind the scenes</summary>

```
move_item(item_name="Red Boots",
          dest_container_path=["Master Bedroom","Wardrobe","Top Shelf"])
```

</details>

> **You:** Actually move the whole jewelry box into the bed's left drawer.
>
> **Claude:** Moved. The watch, earrings, and necklace inside it came
> along — their paths were updated in one transaction.

<details><summary>Behind the scenes</summary>

```
move_container(source_path=["Master Bedroom","Wardrobe","Jewelry Box"],
               dest_parent_path=["Master Bedroom","Bed","Left Drawer"])
```

</details>

### Renaming

> **You:** Rename the master bedroom to "Primary Bedroom".
>
> **Claude:** Done. Every path under it (the bed, the drawers, the
> wardrobe, every item) was rewritten in one go.

<details><summary>Behind the scenes</summary>

```
rename_container(path=["Master Bedroom"], new_name="Primary Bedroom")
```

</details>

> **You:** Rename my passport entry to "US Passport".
>
> **Claude:** Renamed.

<details><summary>Behind the scenes</summary>

```
rename_item(item_name="Passport", new_name="US Passport")
```

</details>

### Teaching it short names (aliases)

> **You:** When I say "bag", I always mean my Chanel bag.
>
> **Claude:** Noted. Now "where's my bag?" will resolve to your Chanel
> bag directly.

<details><summary>Behind the scenes</summary>

```
add_alias(entity_type="item", id_or_path="Chanel Bag", alias="bag")
```

</details>

Other useful aliases you can teach: `watch` → Gold Watch, `keys` →
House Keys, `earrings` → Diamond Earrings.

### Tagging across the hierarchy

Tags are the orthogonal axis — *"show me everything valuable"* doesn't
care which room or drawer.

> **You:** Tag my passport, Chanel bag, gold watch, and diamond
> earrings as valuable.
>
> **Claude:** Tagged.

<details><summary>Behind the scenes</summary>

```
add_tag(entity_type="item", id_or_path="Passport",         tag="valuable")
add_tag(entity_type="item", id_or_path="Chanel Bag",       tag="valuable")
add_tag(entity_type="item", id_or_path="Gold Watch",       tag="valuable")
add_tag(entity_type="item", id_or_path="Diamond Earrings", tag="valuable")
```

</details>

> **You:** Show me all my valuables.
>
> **Claude:** Four items tagged "valuable": passport (left drawer of the
> bed), Chanel bag (top shelf of the wardrobe), gold watch and diamond
> earrings (jewelry box).

<details><summary>Behind the scenes</summary>

```
find_by_tag(tag="valuable")  → 4 entity ids, then resolved
```

</details>

### Locking — the "are you sure?" gate

Locking does **not** encrypt or hide anything. It's a *friction layer*:
once an entity is locked, any future mutation on it (or anything inside
it, for containers) returns `ENTITY_LOCKED`. The LLM is expected to
surface that to you and only retry with `force: true` after you confirm.

> **You:** Lock my jewelry box.
>
> **Claude:** Locked. I'll ask you before changing anything inside it.

<details><summary>Behind the scenes</summary>

```
lock_entity(entity_type="container",
            id_or_path=["Master Bedroom","Wardrobe","Jewelry Box"])
```

</details>

A few minutes later:

> **You:** Add a pearl necklace to the jewelry box.
>
> **Claude:** Your jewelry box is locked. Want me to add the pearl
> necklace anyway?
>
> **You:** Yes.
>
> **Claude:** Added.

<details><summary>Behind the scenes</summary>

```
add_item(["Master Bedroom","Wardrobe","Jewelry Box"], "Pearl Necklace")
→ ENTITY_LOCKED error → Claude asks you

add_item(["Master Bedroom","Wardrobe","Jewelry Box"], "Pearl Necklace", force=True)
→ success
```

</details>

> **You:** Unlock it.
>
> **Claude:** Unlocked.

<details><summary>Behind the scenes</summary>

```
unlock_entity(entity_type="container",
              id_or_path=["Master Bedroom","Wardrobe","Jewelry Box"])
```

</details>

### Freshness — *"I just put it back"*

MindPalace tracks when an entity was last *physically confirmed*,
separately from when it was last *edited in the database*. Useful for
the LLM to say "I last saw your passport in the safe two months ago —
still there?".

> **You:** I just put my passport back in the drawer.
>
> **Claude:** Got it — marked as seen now.

<details><summary>Behind the scenes</summary>

```
mark_seen(entity_type="item", id_or_path="Passport")
→ last_seen_at = now()
```

</details>

### Deletion — soft, reversible

Deletes never lose data. Rows get a `deleted_at` timestamp; the
resolver hides them from every read. Reversible until garbage-collected
(~30 days, planned). The `confirm: true` flag is mandatory so the LLM
has to surface the impact first.

> **You:** Delete the master bedroom.
>
> **Claude:** That will remove the master bedroom and 9 things inside
> it (bed, wardrobe, drawers, jewelry box, items). Sure?
>
> **You:** Yes.
>
> **Claude:** Deleted. Recoverable for the next ~30 days if you change
> your mind.

<details><summary>Behind the scenes</summary>

```
delete_container(path=["Master Bedroom"], confirm=True)
→ {"descendant_count": 9}
```

(If the container were locked, you'd also need `force: true`.)

</details>

After a soft-delete, freed names become reusable immediately — the
unique constraint ignores deleted rows.

### Backups, history, health

> **You:** Make a backup before I rearrange everything.
>
> **Claude:** Snapshot saved as `20260502T064500Z-a1b2c3d4`.

<details><summary>Behind the scenes</summary>

```
create_backup(note="before-bedroom-redo")
→ {backup_id, path, sha256, bytes}
```

</details>

> **You:** What changed in the last hour?
>
> **Claude:** *lists recent mutations: rename of master bedroom, lock on
> jewelry box, addition of pearl necklace, …*

<details><summary>Behind the scenes</summary>

```
audit_log_entries(limit=50)
→ append-only log; filterable by since / entity_id / tool_name
```

</details>

> **You:** Is the server healthy?
>
> **Claude:** All good — 33 tools loaded, root initialised, 12
> containers, 13 items, version 0.2.0.

<details><summary>Behind the scenes</summary>

```
health_check()
```

</details>

Other maintenance tools: `list_backups`, `export_data` (JSON dump for
portability).

---

## Full tool reference

| Domain | Tools |
|---|---|
| Structure | `create_container`, `rename_container`, `move_container`, `delete_container`, `list_children`, `get_tree` |
| Items | `add_item`, `rename_item`, `move_item`, `delete_item`, `find_item`, `list_items_in_container`, `mark_seen` |
| Recall | `where_is`, `what_is_inside`, `search_items`, `search_containers` |
| Locking | `lock_entity`, `unlock_entity`, `is_locked` |
| Tags | `add_tag`, `remove_tag`, `list_tags`, `find_by_tag` |
| Aliases | `add_alias`, `remove_alias`, `list_aliases` |
| Maintenance | `health_check`, `initialize_home`, `audit_log_entries`, `create_backup`, `list_backups`, `export_data` |

33 tools total. Every mutation is gated, validated, and audited.

---

## Design rules (the load-bearing ones)

1. **The LLM never touches the DB.** Every mutation goes through a typed,
   validated MCP tool. Errors are codes (`PATH_NOT_FOUND`, `ENTITY_LOCKED`,
   `SIBLING_CONFLICT`, `INVALID_MOVE`, `ITEM_CONFLICT`, …) with structured
   payloads the LLM can branch on.
2. **Determinism beats cleverness.** `where_is` runs exact → alias → fuzzy
   → semantic and stops at the first match class. Every result tells you
   *why* it matched (`match_type`).
3. **Locking is friction, not security.** Locked entities are mutable, but
   only with `force: true` — which forces the LLM to surface intent to you
   first.
4. **Soft delete by default.** Deleted things don't leave the DB; they're
   recoverable until GC. The whole point of a memory system is forgiveness.
5. **Every mutation is audited.** Before/after JSON snapshots in
   `audit_log` (DB) and `data/audit.log` (JSONL). Foundation for `undo`.

---

## Errors you'll see (and what they mean)

| Code | When | What the LLM should do |
|---|---|---|
| `PATH_NOT_FOUND` | A path segment doesn't resolve. Payload includes `available_children`. | Suggest the closest match or ask which one you meant. |
| `SIBLING_CONFLICT` | A new container's name canonicalises to an existing sibling. | Pick a different name or use the existing one. |
| `ITEM_CONFLICT` | Same, for items inside a container. | Same. |
| `INVALID_MOVE` | Trying to move a container into itself or its descendant. | Refuse and explain. |
| `ROOT_OPERATION_FORBIDDEN` | Tried to rename/move/delete `/home`. | Refuse. Root is permanent. |
| `ENTITY_LOCKED` | Mutation attempted on a locked entity without `force`. | Ask the user to confirm, then retry with `force: true`. |
| `CONFIRM_REQUIRED` | `delete_*` called without `confirm: true`. | Surface the count of things that will be deleted, then re-call with confirm. |
| `ITEM_AMBIGUOUS` | Item name matches multiple items globally. | Ask which one (payload includes `matching_items` with paths). |

---

## Optional: semantic search

```bash
uv sync --extra semantic
```

Adds `sentence-transformers` (~30 MB). `where_is` then falls back to
vector similarity if exact / alias / fuzzy all miss. Disabled by
default — the deterministic strategies cover most queries.

---

## Development

```bash
uv sync --extra dev
uv run pytest                              # 35 tests, core + MCP surface
uv run fastmcp dev mindpalace.server:mcp   # FastMCP inspector / hot reload
uv run python -m mindpalace doctor         # round-trip create/read/delete
```

Migrating from a v1 `home_memory.db`:

```bash
uv run python scripts/migrate_v1_to_v2.py /path/to/home_memory.db
```

---

## License

MIT.
