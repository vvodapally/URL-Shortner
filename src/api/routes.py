"""
src/api/routes.py
-----------------
All FastAPI route handlers in one file, split into four APIRouters:

  router_shorten    — POST /shorten
  router_redirect   — GET /{short_code}
  router_analytics  — GET /analytics/{short_code}
  router_health     — GET /health, GET /health/live, GET /health/ready

Design notes
------------
- Rate limiting is a middleware concern but implemented here as a
  per-route dependency for clarity and testability.
- Analytics writes are BackgroundTasks — they run after the response
  is sent, keeping redirect p99 latency under 10ms.
- The redirect endpoint checks Redis first (O(1)), then Postgres on miss.
  A cache miss also warms the cache for subsequent requests.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.analytics.pipeline import record_click, top_referrers, click_trend, top_countries
from src.api.schemas import (
    AnalyticsResponse, ClickTrendPoint, ComponentHealth, ErrorResponse,
    HealthResponse, ShortenRequest, ShortenResponse, TopCountry, TopReferrer,
)
from src.cache.redis_client import (
    cache_delete_url, cache_get_url, cache_set_url,
    check_redis_health, rate_limit_check,
)
from src.config import settings
from src.core.shortener import URLValidationError, build_short_url, generate_short_code, validate_url
from src.db.engine import check_db_health, get_db
from src.db.models import URL, hash_ip
from src.utils.logger import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Shared dependency — rate limiter
# ---------------------------------------------------------------------------

async def require_not_rate_limited(request: Request) -> None:
    """FastAPI dependency — raises 429 if the client IP is over the limit."""
    client_ip = request.client.host if request.client else "unknown"
    ip_hash   = hashlib.sha256(client_ip.encode()).hexdigest()
    allowed, count = await rate_limit_check(ip_hash)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {count}/{settings.rate_limit_requests} "
                   f"requests per {settings.rate_limit_window_s}s.",
            headers={"Retry-After": str(settings.rate_limit_window_s)},
        )


# ---------------------------------------------------------------------------
# POST /shorten
# ---------------------------------------------------------------------------

router_shorten = APIRouter(tags=["URL Shortening"])


@router_shorten.post(
    "/shorten",
    response_model=ShortenResponse,
    status_code=201,
    summary="Shorten a URL",
    description=(
        "Accept a long URL and return a short code + full short URL. "
        "Optionally specify a custom code and/or expiry timestamp."
    ),
    responses={
        400: {"model": ErrorResponse, "description": "Invalid URL or custom code conflict"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
    },
    dependencies=[Depends(require_not_rate_limited)],
)
async def shorten_url(
    body:    ShortenRequest,
    request: Request,
    db:      AsyncSession = Depends(get_db),
) -> ShortenResponse:

    # 1. Validate the long URL
    try:
        long_url = validate_url(body.url, max_length=settings.max_url_length)
    except URLValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # 2. Resolve short code (custom or generated)
    if body.custom_code:
        # Check uniqueness of custom code
        existing = await db.execute(
            select(URL).where(URL.short_code == body.custom_code)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail=f"Custom code {body.custom_code!r} is already taken."
            )
        short_code = body.custom_code
    else:
        # Generate a unique random code (retry on the astronomically rare collision)
        for _ in range(5):
            short_code = generate_short_code(settings.short_code_length)
            existing   = await db.execute(
                select(URL).where(URL.short_code == short_code)
            )
            if not existing.scalar_one_or_none():
                break
        else:
            raise HTTPException(status_code=500, detail="Could not generate a unique code.")

    # 3. Persist to Postgres
    client_ip = request.client.host if request.client else None
    url_row = URL(
        short_code = short_code,
        long_url   = long_url,
        expires_at = body.expires_at,
        title      = body.title,
        created_by = hash_ip(client_ip) if client_ip else None,
    )
    db.add(url_row)
    await db.flush()   # get server-side defaults (id, created_at) before commit
    await db.refresh(url_row)

    # 4. Warm the Redis cache
    ttl = None
    if body.expires_at:
        remaining = (body.expires_at - datetime.now(timezone.utc)).total_seconds()
        ttl = max(1, int(remaining))
    await cache_set_url(short_code, long_url, ttl_s=ttl)

    log.info("URL shortened", extra={
        "short_code": short_code,
        "long_url":   long_url[:80],
    })

    return ShortenResponse(
        short_code = short_code,
        short_url  = build_short_url(settings.base_url, short_code),
        long_url   = long_url,
        created_at = url_row.created_at,
        expires_at = url_row.expires_at,
        title      = url_row.title,
    )


# ---------------------------------------------------------------------------
# GET /{short_code}  — redirect
# ---------------------------------------------------------------------------

router_redirect = APIRouter(tags=["Redirect"])


@router_redirect.get(
    "/{short_code}",
    summary="Redirect to the original URL",
    response_class=RedirectResponse,
    status_code=302,
    responses={
        302: {"description": "Redirect to the original URL"},
        404: {"model": ErrorResponse, "description": "Short code not found or expired"},
        410: {"model": ErrorResponse, "description": "Short URL has been deactivated"},
    },
)
async def redirect_to_url(
    short_code:      str,
    request:         Request,
    background_tasks: BackgroundTasks,
    db:              AsyncSession = Depends(get_db),
) -> RedirectResponse:

    # 1. Cache-first lookup
    long_url = await cache_get_url(short_code)

    if long_url is None:
        # Cache miss — hit Postgres
        result = await db.execute(
            select(URL).where(URL.short_code == short_code)
        )
        url_row = result.scalar_one_or_none()

        if url_row is None:
            raise HTTPException(status_code=404, detail="Short URL not found.")

        if not url_row.is_active:
            raise HTTPException(status_code=410, detail="Short URL has been deactivated.")

        if url_row.is_expired():
            raise HTTPException(status_code=404, detail="Short URL has expired.")

        long_url = url_row.long_url

        # Warm cache for next request
        await cache_set_url(short_code, long_url)

    # 2. Fire-and-forget analytics (after response is sent)
    client_ip  = request.client.host if request.client else None
    referrer   = request.headers.get("Referer")
    user_agent = request.headers.get("User-Agent")
    ip_hash    = hash_ip(client_ip) if client_ip else None

    background_tasks.add_task(
        record_click,
        short_code   = short_code,
        referrer     = referrer,
        user_agent   = user_agent,
        ip_hash      = ip_hash,
    )

    return RedirectResponse(url=long_url, status_code=302)


# ---------------------------------------------------------------------------
# GET /analytics/{short_code}  (Brownfield Scenario 2)
# ---------------------------------------------------------------------------

router_analytics = APIRouter(prefix="/analytics", tags=["Analytics"])


@router_analytics.get(
    "/{short_code}",
    response_model=AnalyticsResponse,
    summary="Get analytics for a short URL",
    responses={
        404: {"model": ErrorResponse, "description": "Short code not found"},
    },
)
async def get_analytics(
    short_code: str,
    days:       int = 7,
    db:         AsyncSession = Depends(get_db),
) -> AnalyticsResponse:

    # Verify the short code exists
    result  = await db.execute(select(URL).where(URL.short_code == short_code))
    url_row = result.scalar_one_or_none()
    if url_row is None:
        raise HTTPException(status_code=404, detail="Short URL not found.")

    # Gather aggregations (these could be cached with a short TTL in prod)
    referrers  = await top_referrers(db, short_code)
    trend      = await click_trend(db, short_code, days=days)
    countries  = await top_countries(db, short_code)

    return AnalyticsResponse(
        short_code    = short_code,
        total_clicks  = url_row.click_count,
        top_referrers = [TopReferrer(**r) for r in referrers],
        click_trend   = [ClickTrendPoint(**t) for t in trend],
        top_countries = [TopCountry(**c) for c in countries],
    )


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

router_health = APIRouter(tags=["Observability"])


@router_health.get(
    "/health",
    response_model=HealthResponse,
    summary="Full health check (Postgres + Redis)",
)
async def health_check() -> HealthResponse:
    pg_health    = await check_db_health()
    redis_health = await check_redis_health()

    components = [
        ComponentHealth(**pg_health),
        ComponentHealth(**redis_health),
    ]
    all_healthy = all(c.status == "healthy" for c in components)
    overall     = "healthy" if all_healthy else "degraded"

    return HealthResponse(
        status     = overall,
        version    = settings.app_version,
        components = components,
    )


@router_health.get(
    "/health/live",
    summary="Liveness probe (Kubernetes)",
    status_code=200,
)
async def liveness() -> dict:
    """Returns 200 if the process is alive (no dependency checks)."""
    return {"status": "alive"}


@router_health.get(
    "/health/ready",
    summary="Readiness probe (Kubernetes)",
)
async def readiness() -> dict:
    """Returns 200 only when both Postgres and Redis are reachable."""
    pg    = await check_db_health()
    redis = await check_redis_health()
    if pg["status"] != "healthy" or redis["status"] != "healthy":
        raise HTTPException(status_code=503, detail="Service not ready")
    return {"status": "ready"}
