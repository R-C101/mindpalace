"""Baseline — current v1 schema as the starting point.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-02

We take the v1 SQLAlchemy metadata as ground truth and emit a single
create_all() against the migration's connection. Every migration after
this one (0002+) uses ``op.*`` calls so alembic autogenerate stays useful.

For installs that already have a v1 ``home_memory.db`` matching this schema,
``scripts/migrate_v1_to_v2.py`` stamps the DB at this revision rather than
re-creating tables.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

from mindpalace.db.models import Base

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
