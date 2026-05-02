"""
MindPalace runtime configuration.

All knobs are read from environment variables (or .env via python-dotenv),
with sensible local-first defaults.

Why a module-level singleton instead of pydantic-settings: the surface is small,
the values are read once at startup, and we want zero indirection in hot paths
like resolver.py and operations.py.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- Paths ---------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.getenv("MINDPALACE_DATA_DIR", REPO_ROOT / "data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.getenv("MINDPALACE_DB_PATH", DATA_DIR / "mindpalace.db")).resolve()

BACKUPS_DIR = DATA_DIR / "backups"
BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

AUDIT_LOG_PATH = DATA_DIR / "audit.log"

# --- Database -----------------------------------------------------------

DATABASE_URL = os.getenv("MINDPALACE_DATABASE_URL", f"sqlite:///{DB_PATH}")

# --- Embeddings (optional) ----------------------------------------------

EMBEDDING_MODEL = os.getenv("MINDPALACE_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")

# --- Behaviour ----------------------------------------------------------

READONLY = os.getenv("MINDPALACE_READONLY", "").lower() in ("1", "true", "yes")
LOG_LEVEL = os.getenv("MINDPALACE_LOG_LEVEL", "INFO").upper()
