"""
One-shot migration from a v1 ``home_memory.db`` to a v2 ``mindpalace.db``.

v1 schema (no soft-delete, no aliases/tags/embeddings/audit_log) is a
strict subset of v2. We:
    1. Open the old DB read-only.
    2. Bring the new DB to v2 schema (alembic upgrade or create_all).
    3. Copy containers, items, images, entity_images row-by-row, dropping
       columns that no longer exist (``security_level``).

Usage:
    uv run python scripts/migrate_v1_to_v2.py path/to/home_memory.db [path/to/mindpalace.db]

The destination must not exist (we won't overwrite — make a backup first).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


V1_TABLES = ["containers", "items", "images", "entity_images"]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    src = Path(argv[1]).resolve()
    dst = Path(argv[2]).resolve() if len(argv) > 2 else src.parent / "mindpalace.db"
    if not src.exists():
        print(f"v1 db not found: {src}", file=sys.stderr)
        return 1
    if dst.exists():
        print(f"refusing to overwrite existing destination: {dst}", file=sys.stderr)
        return 1

    # Build the v2 schema by importing the package and running create_all
    # against the destination URL.
    import os
    os.environ["MINDPALACE_DB_PATH"] = str(dst)
    os.environ["MINDPALACE_DATABASE_URL"] = f"sqlite:///{dst}"
    from mindpalace.db.engine import engine
    from mindpalace.db.models import Base
    Base.metadata.create_all(bind=engine)

    # Stream rows from old → new, skipping dropped columns.
    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    src_conn.row_factory = sqlite3.Row
    dst_conn = sqlite3.connect(dst)
    dst_conn.row_factory = sqlite3.Row

    try:
        for table in V1_TABLES:
            cols_v1 = [r["name"] for r in src_conn.execute(f"PRAGMA table_info({table})")]
            cols_v2 = [r["name"] for r in dst_conn.execute(f"PRAGMA table_info({table})")]
            shared = [c for c in cols_v1 if c in cols_v2]
            placeholders = ",".join("?" for _ in shared)
            collist = ",".join(shared)
            count = 0
            for row in src_conn.execute(f"SELECT {collist} FROM {table}"):
                dst_conn.execute(
                    f"INSERT INTO {table} ({collist}) VALUES ({placeholders})",
                    [row[c] for c in shared],
                )
                count += 1
            print(f"  {table}: {count} rows copied")
        dst_conn.commit()
    finally:
        src_conn.close()
        dst_conn.close()

    print(f"\nMigration complete: {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
