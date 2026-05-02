"""
MindPalace - Database Engine Setup

Provides the SQLAlchemy engine + session factory. Path/URL come from
mindpalace.config so the same code is used by tests, the MCP server,
and alembic migrations.
"""

from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from mindpalace.config import DATABASE_URL
from mindpalace.db.models import Base


engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    echo=False,
)

# Session factory
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


def create_tables() -> None:
    """
    Create all database tables.
    
    Should be called once during application startup.
    """
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """
    Dependency injection for FastAPI.
    
    Yields a database session that is:
    - Created per request
    - Automatically closed after request
    - Rolled back on exceptions (commits happen in operations.py)
    
    Usage:
        @app.get("/example")
        def example(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

