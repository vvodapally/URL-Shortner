"""
src/cache/redis_client.py
--------------------------
Redis client wrapper for:
  1. Hot-path URL cache    (shortCode → longUrl, O(1) redirect)
  2. Rate limiting         (sliding window counter per IP)
  3. Analytics buffer      (batched click events before Postgres flush)
  4. Health check          (PING)

Key schema
----------
  url:{short_code}          → long URL string  (TTL = redis_ttl_s)
  rl:{ip_hash}              → sliding window count  (TTL = rate_limit_window_s)
  analytics:queue           → Redis List of JSON-encoded click events

All keys are namespaced to avoid collisions if this Redis instance is
shared with other services.
"""

from __future__ import annotations

import json
from typing import Optional

from src.config import settings
from src.utils.logger import get_logger
from src.utils.retry import retry

log = get_logger(__name__)

# Module-level client (initialised in lifespan)
_redis = None

# Key prefixes
_URL_PREFIX   = "url:"
_RL_PREFIX    = "rl:"
_ANALYTICS_Q  = "analytics:queue"


def get_redis():
    if _redis is None:
        raise RuntimeError("Redis client not initialised. Call init_redis() first.")
    return _redis


async def init_redis() -> None:
    """Initialise the async Redis connection pool."""
    global _redis
    try:
        import redis.asyncio as aioredis
        _redis = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=20,
            socket_connect_timeout=3,
            socket_timeout=3,
            retry_on_timeout=True,
        )
        # Verify connectivity
        await _redis.ping()
        log.info("Redis client ready", extra={"url": settings.redis_url})
    except Exception as exc:
        log.error("Redis init failed", extra={"error": str(exc)})
        raise


async def close_redis() -> None:
    if _redis:
        await _redis.aclose()
        log.info("Redis connection closed")


# ---------------------------------------------------------------------------
# URL cache
# ---------------------------------------------------------------------------

@retry(max_attempts=3, base_delay=0.05)
async def cache_get_url(short_code: str) -> Optional[str]:
    """Return cached long URL or None on miss."""
    r = get_redis()
    return await r.get(f"{_URL_PREFIX}{short_code}")


@retry(max_attempts=3, base_delay=0.05)
async def cache_set_url(
    short_code: str,
    long_url: str,
    ttl_s: Optional[int] = None,
) -> None:
    """Write a short_code → long_url mapping with TTL."""
    r   = get_redis()
    ttl = ttl_s if ttl_s is not None else settings.redis_ttl_s
    await r.set(f"{_URL_PREFIX}{short_code}", long_url, ex=ttl)


@retry(max_attempts=3, base_delay=0.05)
async def cache_delete_url(short_code: str) -> None:
    """Evict a URL from cache (called on deactivation)."""
    r = get_redis()
    await r.delete(f"{_URL_PREFIX}{short_code}")


# ---------------------------------------------------------------------------
# Rate limiting  (sliding window using Redis INCR + EXPIRE)
# ---------------------------------------------------------------------------

@retry(max_attempts=2, base_delay=0.05)
async def rate_limit_check(ip_hash: str) -> tuple[bool, int]:
    """
    Check whether an IP has exceeded the rate limit.

    Uses a fixed-window counter (simple and O(1)). Each call increments
    the counter; if it was 0 before, sets TTL = rate_limit_window_s.

    Returns (allowed: bool, current_count: int).
    """
    r   = get_redis()
    key = f"{_RL_PREFIX}{ip_hash}"
    count = await r.incr(key)
    if count == 1:
        # First request in this window — set the expiry
        await r.expire(key, settings.rate_limit_window_s)
    allowed = count <= settings.rate_limit_requests
    return allowed, count


# ---------------------------------------------------------------------------
# Analytics buffer
# ---------------------------------------------------------------------------

@retry(max_attempts=2, base_delay=0.05)
async def analytics_push(event: dict) -> None:
    """Append a click event to the analytics queue (Redis List)."""
    r = get_redis()
    await r.rpush(_ANALYTICS_Q, json.dumps(event))


@retry(max_attempts=2, base_delay=0.05)
async def analytics_drain(max_items: int = 100) -> list[dict]:
    """
    Pop up to `max_items` events from the analytics queue.

    Uses LMPOP (atomic left-pop of N items). Falls back to a LRANGE +
    LTRIM sequence for Redis < 7.0.
    """
    r = get_redis()
    try:
        # Redis 7.0+ — atomic multi-pop
        result = await r.lmpop(1, _ANALYTICS_Q, direction="LEFT", count=max_items)
        if result is None:
            return []
        _, items = result
        return [json.loads(item) for item in items]
    except Exception:
        # Fallback for Redis < 7.0
        raw = await r.lrange(_ANALYTICS_Q, 0, max_items - 1)
        if raw:
            await r.ltrim(_ANALYTICS_Q, len(raw), -1)
        return [json.loads(item) for item in raw]


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

async def check_redis_health() -> dict:
    """Return a health status dict for the /health endpoint."""
    try:
        r = get_redis()
        await r.ping()
        return {"status": "healthy", "component": "redis"}
    except Exception as exc:
        log.error("Redis health check failed", extra={"error": str(exc)})
        return {"status": "unhealthy", "component": "redis", "error": str(exc)}
