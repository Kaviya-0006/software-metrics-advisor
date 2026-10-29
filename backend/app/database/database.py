"""
Database connection and session management (SQLAlchemy + SQLite).

This module only sets up the *connection layer*. Table creation is
triggered from main.py on startup using the models defined in models.py.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

# SQLite requires this connect_arg when used with FastAPI's threaded
# request handling.
connect_args = (
    {"check_same_thread": False}
    if settings.DATABASE_URL.startswith("sqlite")
    else {}
)

engine = create_engine(settings.DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def init_db() -> None:
    """Create all tables that don't already exist."""
    # Import models here so they are registered on Base before create_all.
    from app.database import models  # noqa: F401

    Base.metadata.create_all(bind=engine)

    # Phase 9A: give scans stored before project versioning a version record.
    # Idempotent and a no-op on a database that is already up to date.
    from app.database.versioning import backfill_versions

    session = SessionLocal()
    try:
        backfill_versions(session)
    finally:
        session.close()


def get_db():
    """FastAPI dependency that yields a database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
