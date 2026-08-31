"""
src/analytics/pipeline.py
--------------------------
Two-stage analytics pipeline:

Stage 1  — Record (hot path, async, non-blocking)
  The redirect endpoint fires a BackgroundTask that pushes a click event
  JSON blob to a Redis List. The redirect response is returned to the
  client before this even begins.

Stage 2  — Flush (background worker)
  A periodic coroutine drains the Redis queue in batches and writes to
  Postgres. Runs every `analytics_flush_interval` seconds (default 5s).
  This decouples redirect latency from DB write latency.

Brownfield additions (Scenario 2) live in this module:
  - top_referrers()
  - click_trend()
  - (country_code populated by geo-lookup in the API layer)
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.cache.redis_client import analytics_drain, analytics_push
from src.db.models import Click
from src.utils.logger import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Stage 1 — Record (called from redirect endpoint as a BackgroundTask)
# ---------------------------------------------------------------------------

async def record_click(
    short_code:   str,
    referrer:     Optional[str],
    user_agent:   Optional[str],
    ip_hash:      Optional[str],
    country_code: Optional[str] = None,
    city:         Optional[str] = None,
) -> None:
    """
    Push a click event onto the Redis analytics queue.

    This is fire-and-forget: the redirect response is already sent by the
    time this coroutine is awaited. Any exception here is logged but never
    propagates to the client.
    """
    event = {
        "short_code":   short_code,
        "clicked_at":   datetime.now(timezone.utc).isoformat(),
        "referrer":     referrer,
        "user_agent":   user_agent,
        "ip_hash":      ip_hash,
        "country_code": country_code,
        "city":         city,
    }
    try:
        await analytics_push(event)
    except Exception as exc:
        # Analytics failure must NEVER affect the redirect response
        log.error("Analytics push failed", extra={"error": str(exc), "short_code": short_code})


# ---------------------------------------------------------------------------
# Stage 2 — Flush worker
# ---------------------------------------------------------------------------

async def flush_analytics(session: AsyncSession, batch_size: int = 100) -> int:
    """
    Drain the Redis queue and write click rows to Postgres.

    Returns the number of events written.
    Called by the background flush loop and can also be called explicitly
    in tests or on graceful shutdown.
    """
    events = await analytics_drain(max_items=batch_size)
    if not events:
        return 0

    clicks = []
    short_codes = set()
    for ev in events:
        clicks.append(Click(
            short_code   = ev["short_code"],
            clicked_at   = datetime.fromisoformat(ev["clicked_at"]),
            referrer     = ev.get("referrer"),
            user_agent   = ev.get("user_agent"),
            ip_hash      = ev.get("ip_hash"),
            country_code = ev.get("country_code"),
            city         = ev.get("city"),
        ))
        short_codes.add(ev["short_code"])

    try:
        session.add_all(clicks)

        # Bulk increment click_count on the urls table
        for code in short_codes:
            count = sum(1 for c in clicks if c.short_code == code)
            await session.execute(
                text(
                    "UPDATE urls SET click_count = click_count + :n "
                    "WHERE short_code = :code"
                ),
                {"n": count, "code": code},
            )

        await session.commit()
        log.info("Analytics flushed", extra={"events": len(events)})
        return len(events)
    except Exception as exc:
        await session.rollback()
        log.error("Analytics flush failed", extra={"error": str(exc)})
        raise


async def analytics_flush_loop(session_factory, flush_interval: float = 5.0) -> None:
    """
    Background coroutine that continuously flushes analytics to Postgres.
    Runs until the application shuts down (task is cancelled on lifespan exit).
    """
    log.info("Analytics flush loop started", extra={"interval_s": flush_interval})
    while True:
        try:
            async with session_factory() as session:
                await flush_analytics(session)
        except asyncio.CancelledError:
            # Graceful shutdown — do one final flush before exiting
            log.info("Analytics flush loop shutting down — final flush")
            try:
                async with session_factory() as session:
                    await flush_analytics(session, batch_size=10_000)
            except Exception as exc:
                log.error("Final analytics flush failed", extra={"error": str(exc)})
            return
        except Exception as exc:
            log.error("Analytics flush loop error", extra={"error": str(exc)})

        await asyncio.sleep(flush_interval)


# ---------------------------------------------------------------------------
# Aggregation queries (Brownfield Scenario 2)
# ---------------------------------------------------------------------------

async def top_referrers(
    session: AsyncSession,
    short_code: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return the top `limit` referrers for a short URL by click count."""
    result = await session.execute(
        select(
            Click.referrer,
            func.count(Click.id).label("clicks"),
        )
        .where(
            Click.short_code == short_code,
            Click.referrer.isnot(None),
        )
        .group_by(Click.referrer)
        .order_by(func.count(Click.id).desc())
        .limit(limit)
    )
    return [{"referrer": row.referrer, "clicks": row.clicks} for row in result]


async def click_trend(
    session: AsyncSession,
    short_code: str,
    days: int = 7,
) -> list[dict[str, Any]]:
    """
    Return daily click counts for the last `days` days.
    Suitable for rendering a trend sparkline.
    """
    result = await session.execute(
        text("""
            SELECT
                DATE(clicked_at AT TIME ZONE 'UTC') AS day,
                COUNT(*)                            AS clicks
            FROM clicks
            WHERE short_code = :code
              AND clicked_at >= NOW() - INTERVAL ':days days'
            GROUP BY day
            ORDER BY day ASC
        """),
        {"code": short_code, "days": days},
    )
    return [{"day": str(row.day), "clicks": row.clicks} for row in result]


async def top_countries(
    session: AsyncSession,
    short_code: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return click counts grouped by country (Brownfield Scenario 2)."""
    result = await session.execute(
        select(
            Click.country_code,
            func.count(Click.id).label("clicks"),
        )
        .where(
            Click.short_code == short_code,
            Click.country_code.isnot(None),
        )
        .group_by(Click.country_code)
        .order_by(func.count(Click.id).desc())
        .limit(limit)
    )
    return [{"country_code": row.country_code, "clicks": row.clicks} for row in result]
