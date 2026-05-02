"""
MindPalace MCP server bootstrap.

This module is the single import target for ``fastmcp run`` /
``fastmcp install``. It:

    1. instantiates the FastMCP app
    2. opens a SQLAlchemy engine + session factory
    3. registers all tool modules (registration is import side-effect,
       which is fine for a small, finite tool surface like ours)
    4. exposes a ``session_scope()`` context manager that tools use for
       per-call DB sessions

We deliberately keep tool functions synchronous — SQLAlchemy 2's session is
sync and the operations layer is sync. FastMCP handles thread offloading.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from fastmcp import FastMCP
from sqlalchemy.orm import Session

from mindpalace import __version__
from mindpalace.db.engine import SessionLocal, engine  # noqa: F401 — engine import primes config
from mindpalace.db.models import Base


mcp = FastMCP(
    name="mindpalace",
    instructions=(
        "MindPalace is a deterministic memory of where physical objects live. "
        "Use these tools to record, look up, and modify a hierarchical map of "
        "rooms, drawers, boxes, and items. Always prefer 'where_is' for recall. "
        "If a tool returns ENTITY_LOCKED, surface that to the user and only "
        "retry with force=True after they confirm."
    ),
)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Per-tool-call DB session — commits inside operations, rolls back on error."""
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ensure_schema() -> None:
    """First-run safety: if the DB is empty, create tables. Production
    setups run alembic; this keeps the server runnable on a clean clone."""
    Base.metadata.create_all(bind=engine)


# Register tool modules — import order doesn't matter because each module
# decorates onto the shared ``mcp`` instance.
ensure_schema()
from mindpalace.mcp import tools_maintenance  # noqa: E402,F401
from mindpalace.mcp import tools_structure  # noqa: E402,F401
from mindpalace.mcp import tools_items  # noqa: E402,F401
from mindpalace.mcp import tools_search  # noqa: E402,F401
from mindpalace.mcp import tools_lock  # noqa: E402,F401
from mindpalace.mcp import tools_tags  # noqa: E402,F401


__all__ = ["mcp", "session_scope", "__version__"]
