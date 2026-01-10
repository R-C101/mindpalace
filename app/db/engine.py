"""
Home Memory - Database Engine Setup

Provides database engine, session factory, and dependency injection
for FastAPI request handlers.
"""

from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.db.models import Base


# Database URL - SQLite for local development
# Switch to PostgreSQL URL for production
DATABASE_URL = "sqlite:///./data/home_memory.db"

# Create engine with SQLite-specific settings
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},  # SQLite only
    echo=False,  # Set to True for SQL debugging
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

