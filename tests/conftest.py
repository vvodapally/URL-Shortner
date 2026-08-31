"""
tests/conftest.py
-----------------
Shared pytest fixtures for all test tiers.

Unit tests: no fixtures needed (all pure Python).
Integration tests: app_client fixture provides an httpx.AsyncClient
  wired to the real FastAPI app with test DB + Redis.
E2E tests: uses the running docker-compose stack directly.
"""

from __future__ import annotations

import asyncio
import os
import pytest

# ---------------------------------------------------------------------------
# Event loop policy (required for pytest-asyncio)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def event_loop_policy():
    """Use the default event loop policy for all tests."""
    return asyncio.DefaultEventLoopPolicy()


# ---------------------------------------------------------------------------
# Integration test fixtures
# ---------------------------------------------------------------------------

# Uncomment when running integration tests against docker-compose:
#
# @pytest.fixture(scope="session", autouse=True)
# def set_test_env():
#     """Override settings to point at test database."""
#     os.environ.update({
#         "POSTGRES_DB":           "urlshortener_test",
#         "ENVIRONMENT":           "test",
#         "RATE_LIMIT_REQUESTS":   "5",    # Low limit to test rate limiting easily
#         "RATE_LIMIT_WINDOW_S":   "60",
#         "ANALYTICS_FLUSH_INTERVAL": "1.0",  # Flush every 1s in tests
#     })
#
#
# @pytest.fixture(scope="session")
# async def test_db_engine():
#     """Create test DB tables once per session."""
#     from src.db.engine import init_db, create_tables, close_db
#     await init_db()
#     await create_tables()
#     yield
#     await close_db()
#
#
# @pytest.fixture(scope="session")
# async def test_redis():
#     """Initialise Redis once per session."""
#     from src.cache.redis_client import init_redis, close_redis
#     await init_redis()
#     yield
#     await close_redis()
#
#
# @pytest.fixture(autouse=True)
# async def clean_redis(test_redis):
#     """Flush Redis before each test to ensure isolation."""
#     from src.cache.redis_client import get_redis
#     await get_redis().flushdb()
#
#
# @pytest.fixture(autouse=True)
# async def clean_db(test_db_engine):
#     """Truncate all tables before each test."""
#     from src.db.engine import get_engine
#     from sqlalchemy import text
#     engine = get_engine()
#     async with engine.begin() as conn:
#         await conn.execute(text("TRUNCATE TABLE clicks, urls RESTART IDENTITY CASCADE"))
#
#
# @pytest_asyncio.fixture
# async def app_client(test_db_engine, test_redis):
#     """Return an async httpx client wired to the FastAPI ASGI app."""
#     from httpx import AsyncClient, ASGITransport
#     from src.main import app
#     async with AsyncClient(
#         transport=ASGITransport(app=app),
#         base_url="http://test",
#     ) as client:
#         yield client
