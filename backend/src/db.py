"""
Shared SQLAlchemy engine factory with connection pooling.

Provides a single get_engine() entry point for all PostgreSQL access
so that every consumer shares a bounded pool instead of creating
new engines on each request.
"""

from sqlalchemy import create_engine

from backend.config.settings import DATABASE_URL, DB_MAX_OVERFLOW, DB_POOL_SIZE

_engine = None


def get_engine():
    """Return a shared SQLAlchemy engine with connection pooling.

    Pool defaults:
    - pool_size=5            (max idle connections kept open)
    - max_overflow=10        (extra connections allowed under load)
    - pool_pre_ping=True     (validate connection before handing it out)
    - pool_recycle=3600      (recycle connections after 1 hour)

    Raises:
        RuntimeError: if DATABASE_URL is empty or the engine cannot be created.
    """
    global _engine
    if _engine is None:
        if not DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL is not set. "
                "Define it in .env or as an environment variable."
            )
        _engine = create_engine(
            DATABASE_URL,
            pool_size=DB_POOL_SIZE,
            max_overflow=DB_MAX_OVERFLOW,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
    return _engine
