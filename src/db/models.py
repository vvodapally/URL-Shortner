"""
src/db/models.py
----------------
SQLAlchemy ORM models for the URL shortener.

Two tables:
  urls   — the canonical mapping of short_code → long_url
  clicks — append-only analytics event log (one row per redirect)

Design decisions
----------------
- `id` columns use server-side gen UUID (gen_random_uuid()) — no Python
  UUID generation in the hot path, avoids clock skew issues.
- `clicks` has no FK cascade delete. If a short URL is deleted, historical
  click data is preserved (soft-delete pattern via `is_active` on urls).
- `country_code` on clicks is nullable — populated by the Brownfield
  scenario (Scenario 2) geo-lookup enhancement; pre-existing rows are null.
- `ip_hash` stores a SHA-256 hash of the client IP, not the raw IP.
  This satisfies GDPR Art. 25 (data minimisation) — we can still detect
  abuse patterns without storing PII.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Index,
    Integer, String, Text, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# URLs table
# ---------------------------------------------------------------------------

class URL(Base):
    __tablename__ = "urls"

    id         = Column(UUID(as_uuid=True), primary_key=True,
                        server_default=func.gen_random_uuid())
    short_code = Column(String(16),  nullable=False, unique=True, index=True)
    long_url   = Column(Text,        nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False,
                        server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True)
    is_active  = Column(Boolean, nullable=False, server_default="true")
    click_count = Column(BigInteger, nullable=False, server_default="0")

    # Optional metadata set by the client
    title      = Column(String(512), nullable=True)
    created_by = Column(String(256), nullable=True)   # IP hash of creator

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at

    def __repr__(self) -> str:
        return f"<URL short={self.short_code!r} active={self.is_active}>"


# ---------------------------------------------------------------------------
# Clicks table  (analytics event log)
# ---------------------------------------------------------------------------

class Click(Base):
    __tablename__ = "clicks"

    id          = Column(UUID(as_uuid=True), primary_key=True,
                         server_default=func.gen_random_uuid())
    short_code  = Column(String(16),  nullable=False, index=True)
    clicked_at  = Column(DateTime(timezone=True), nullable=False,
                         server_default=func.now())

    # Request metadata (all optional — some clients don't send headers)
    referrer    = Column(Text,        nullable=True)
    user_agent  = Column(Text,        nullable=True)
    ip_hash     = Column(String(64),  nullable=True)  # SHA-256 hex

    # Brownfield Scenario 2 — geo data (null on pre-migration rows)
    country_code = Column(String(2),  nullable=True)  # ISO 3166-1 alpha-2
    city         = Column(String(128), nullable=True)

    # Composite index for the most common analytics queries
    __table_args__ = (
        Index("ix_clicks_code_time", "short_code", "clicked_at"),
    )

    def __repr__(self) -> str:
        return f"<Click short={self.short_code!r} at={self.clicked_at}>"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def hash_ip(ip: str) -> str:
    """Return a one-way SHA-256 hash of an IP address."""
    return hashlib.sha256(ip.encode()).hexdigest()
