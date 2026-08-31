"""
src/config.py
-------------
Single source of truth for all environment-driven configuration.

Uses Python dataclasses + os.environ so the app works with or without
pydantic (keeping the dependency footprint small for the assessment).

All values have safe defaults so the service starts in dev with just:
    docker-compose up
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    # ── Application ────────────────────────────────────────────
    app_name:    str = "url-shortener"
    app_version: str = "1.0.0"
    environment: str = "development"   # development | staging | production
    debug:       bool = False
    base_url:    str = "http://localhost:8000"  # used to build short URLs

    # ── PostgreSQL ─────────────────────────────────────────────
    postgres_host:     str = "localhost"
    postgres_port:     int = 5432
    postgres_db:       str = "urlshortener"
    postgres_user:     str = "postgres"
    postgres_password: str = "postgres"
    postgres_pool_min: int = 2
    postgres_pool_max: int = 10

    # ── Redis ──────────────────────────────────────────────────
    redis_host:     str = "localhost"
    redis_port:     int = 6379
    redis_db:       int = 0
    redis_password: str = ""
    redis_ttl_s:    int = 86400 * 7   # 7-day default cache TTL

    # ── Short code ─────────────────────────────────────────────
    short_code_length: int = 7         # Base62 chars — ~3.5 trillion combos
    max_url_length:    int = 2048

    # ── Rate limiting ──────────────────────────────────────────
    rate_limit_requests: int = 60      # per window
    rate_limit_window_s: int = 60      # sliding window in seconds

    # ── Analytics ─────────────────────────────────────────────
    analytics_batch_size:    int = 100    # flush to Postgres after N events
    analytics_flush_interval: float = 5.0 # seconds between forced flushes

    # ── Observability ─────────────────────────────────────────
    log_level:   str = "INFO"
    log_format:  str = "json"          # "json" | "text"

    @classmethod
    def from_env(cls) -> "Settings":
        """Load settings from environment variables with type coercion."""
        def env(key: str, default: str = "") -> str:
            return os.environ.get(key, default)

        def env_int(key: str, default: int) -> int:
            return int(os.environ.get(key, default))

        def env_bool(key: str, default: bool) -> bool:
            val = os.environ.get(key, str(default)).lower()
            return val in ("1", "true", "yes")

        def env_float(key: str, default: float) -> float:
            return float(os.environ.get(key, default))

        return cls(
            app_name    = env("APP_NAME",    "url-shortener"),
            app_version = env("APP_VERSION", "1.0.0"),
            environment = env("ENVIRONMENT", "development"),
            debug       = env_bool("DEBUG", False),
            base_url    = env("BASE_URL", "http://localhost:8000"),

            postgres_host     = env("POSTGRES_HOST",     "localhost"),
            postgres_port     = env_int("POSTGRES_PORT", 5432),
            postgres_db       = env("POSTGRES_DB",       "urlshortener"),
            postgres_user     = env("POSTGRES_USER",     "postgres"),
            postgres_password = env("POSTGRES_PASSWORD", "postgres"),
            postgres_pool_min = env_int("POSTGRES_POOL_MIN", 2),
            postgres_pool_max = env_int("POSTGRES_POOL_MAX", 10),

            redis_host     = env("REDIS_HOST",     "localhost"),
            redis_port     = env_int("REDIS_PORT", 6379),
            redis_db       = env_int("REDIS_DB",   0),
            redis_password = env("REDIS_PASSWORD", ""),
            redis_ttl_s    = env_int("REDIS_TTL_S", 86400 * 7),

            short_code_length = env_int("SHORT_CODE_LENGTH", 7),
            max_url_length    = env_int("MAX_URL_LENGTH",    2048),

            rate_limit_requests = env_int("RATE_LIMIT_REQUESTS", 60),
            rate_limit_window_s = env_int("RATE_LIMIT_WINDOW_S", 60),

            analytics_batch_size     = env_int("ANALYTICS_BATCH_SIZE",   100),
            analytics_flush_interval = env_float("ANALYTICS_FLUSH_INTERVAL", 5.0),

            log_level  = env("LOG_LEVEL",  "INFO"),
            log_format = env("LOG_FORMAT", "json"),
        )

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def postgres_dsn_sync(self) -> str:
        """Synchronous DSN for Alembic migrations."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


# Module-level singleton — imported everywhere as `from src.config import settings`
settings = Settings.from_env()
