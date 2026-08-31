"""
src/main.py
-----------
FastAPI application factory.

Lifespan pattern (FastAPI 0.93+):
  Startup  → init DB pool, init Redis, start analytics flush loop
  Shutdown → cancel flush loop, final analytics drain, close pools

This guarantees no in-flight analytics events are lost on graceful shutdown.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.analytics.pipeline import analytics_flush_loop
from src.api.routes import (
    router_analytics,
    router_health,
    router_redirect,
    router_shorten,
)
from src.cache.redis_client import close_redis, init_redis
from src.config import settings
from src.db.engine import close_db, create_tables, get_session_factory, init_db
from src.utils.logger import get_logger

log = get_logger(__name__, level=settings.log_level, fmt=settings.log_format)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic as a single async context manager."""
    log.info("Starting up", extra={"env": settings.environment, "version": settings.app_version})

    # Startup
    await init_db()
    await init_redis()

    if settings.environment in ("development", "test"):
        # Auto-create tables in non-production environments
        await create_tables()

    # Start analytics background flush loop as a supervised task
    session_factory = get_session_factory()
    flush_task = asyncio.create_task(
        analytics_flush_loop(session_factory, flush_interval=settings.analytics_flush_interval),
        name="analytics-flush-loop",
    )

    log.info("Service ready")
    yield  # ← application runs here

    # Shutdown
    log.info("Shutting down")
    flush_task.cancel()
    try:
        await flush_task
    except asyncio.CancelledError:
        pass   # expected — the loop handles CancelledError internally

    await close_redis()
    await close_db()
    log.info("Shutdown complete")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    app = FastAPI(
        title       = settings.app_name,
        version     = settings.app_version,
        description = (
            "Production-grade URL shortener with analytics, "
            "rate limiting, and agentic SDLC orchestration."
        ),
        docs_url    = "/docs",
        redoc_url   = "/redoc",
        openapi_url = "/openapi.json",
        lifespan    = lifespan,
    )

    # ── Middleware ────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins  = ["*"],   # tighten in production
        allow_methods  = ["GET", "POST"],
        allow_headers  = ["*"],
    )

    # ── Request ID + structured access log ───────────────────────────────
    @app.middleware("http")
    async def access_log(request: Request, call_next):
        import time, uuid
        request_id = str(uuid.uuid4())[:8]
        start      = time.time()
        response   = await call_next(request)
        elapsed    = round((time.time() - start) * 1000, 1)
        log.info(
            "Request",
            extra={
                "request_id": request_id,
                "method":     request.method,
                "path":       request.url.path,
                "status":     response.status_code,
                "ms":         elapsed,
            },
        )
        response.headers["X-Request-ID"] = request_id
        return response

    # ── Global exception handler ──────────────────────────────────────────
    @app.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception):
        log.error("Unhandled exception", extra={"error": str(exc), "path": request.url.path})
        return JSONResponse(status_code=500, content={"detail": "Internal server error."})

    # ── Routers ───────────────────────────────────────────────────────────
    # Order matters: /health and /analytics must be registered before
    # /{short_code} or FastAPI will try to resolve "health" as a short code.
    app.include_router(router_health)
    app.include_router(router_analytics)
    app.include_router(router_shorten)
    app.include_router(router_redirect)

    return app


app = create_app()
