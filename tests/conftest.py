"""Shared pytest fixtures for MindPalace tests."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

# Point the package at a fresh temp DB *before* importing anything that
# touches mindpalace.config — config snapshots paths at import time.
@pytest.fixture(scope="function")
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MINDPALACE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MINDPALACE_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("MINDPALACE_DATABASE_URL", f"sqlite:///{tmp_path}/test.db")

    # Force a re-import of any module that read config at import time.
    import importlib
    import sys
    for mod in list(sys.modules):
        if mod.startswith("mindpalace"):
            del sys.modules[mod]
    return tmp_path


@pytest.fixture(scope="function")
def session(tmp_data_dir: Path):
    from mindpalace.db.engine import engine, SessionLocal
    from mindpalace.db.models import Base

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
