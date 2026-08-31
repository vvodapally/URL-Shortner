"""
src/db/engine.py
----------------
Async SQLAlchemy engine and session lifecycle.

Uses asyncpg under the hood (fastest async PostgreSQL driver).
Connection pool is tuned conservatively — can be raised via env vars
in high-traffic deployments.

Pattern: every request gets a session via `get_db()` (FastAPI dependency),
which is automatically committed on success and rolled back on exception.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import text

from src.config import settings
from src.utils.logger import get_logger
from src.db.models import Base

log = get_logger(__name__)

# Module-level singletons (initialised in lifespan)
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker | None = None


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Database engine not initialised. Call init_db() first.")
    return _engine


def get_session_factory() -> async_sessionmaker:
    if _session_factory is None:
        raise RuntimeError("Session factory not initialised. Call init_db() first.")
    return _session_factory


async def init_db() -> None:
    """
    Create the async engine and session factory.
    Called once during application startup (FastAPI lifespan).
    """
    global _engine, _session_factory

    log.info("Initialising database connection pool",
             extra={"host": settings.postgres_host, "db": settings.postgres_db})

    _engine = create_async_engine(
        settings.postgres_dsn,
        pool_size=settings.postgres_pool_min,
        max_overflow=settings.postgres_pool_max - settings.postgres_pool_min,
        pool_pre_ping=True,       # verify connection health before use
        pool_recycle=3600,        # recycle connections every hour
        echo=settings.debug,      # log SQL in debug mode only
    )

    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,   # avoid lazy-load errors after commit
        autocommit=False,
        autoflush=False,
    )

    log.info("Database engine ready")


async def create_tables() -> None:
    """Create all tables if they don't exist (dev / test only)."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("Database tables ensured")


async def close_db() -> None:
    """Dispose the connection pool gracefully (called on shutdown)."""
    if _engine:
        await _engine.dispose()
        log.info("Database connection pool closed")


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Yield an AsyncSession for a single request.

    Automatically commits on success, rolls back on any exception,
    and always closes the session.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

async def check_db_health() -> dict:
    """
    Return a health status dict for the /health endpoint.
    Does a lightweight SELECT 1 to verify connectivity.
    """
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "healthy", "component": "postgres"}
    except Exception as exc:
        log.error("Postgres health check failed", extra={"error": str(exc)})
        return {"status": "unhealthy", "component": "postgres", "error": str(exc)}
