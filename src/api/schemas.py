"""
src/api/schemas.py
------------------
Pydantic v2 request/response models.

All public API shapes live here. Separating schemas from ORM models
means the API contract is explicit and independent of the DB schema —
we can evolve the DB without breaking clients (and vice versa).
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator


# ---------------------------------------------------------------------------
# POST /shorten
# ---------------------------------------------------------------------------

class ShortenRequest(BaseModel):
    url:        str = Field(..., description="The long URL to shorten.", max_length=2048)
    custom_code: Optional[str] = Field(
        None,
        description="Optional custom short code (3–16 alphanumeric chars).",
        min_length=3,
        max_length=16,
        pattern=r"^[a-zA-Z0-9]+$",
    )
    expires_at: Optional[datetime] = Field(
        None,
        description="Optional expiry timestamp (ISO 8601). Omit for no expiry.",
    )
    title: Optional[str] = Field(None, max_length=512)

    model_config = {"json_schema_extra": {
        "example": {
            "url": "https://www.example.com/very/long/path?utm_source=newsletter",
            "title": "Example newsletter link",
        }
    }}


class ShortenResponse(BaseModel):
    short_code: str
    short_url:  str
    long_url:   str
    created_at: datetime
    expires_at: Optional[datetime] = None
    title:      Optional[str]      = None


# ---------------------------------------------------------------------------
# GET /{shortCode}  (redirect — no body, 302 response)
# ---------------------------------------------------------------------------

class RedirectInfo(BaseModel):
    """Internal — returned by the lookup service, not exposed directly."""
    long_url:   str
    short_code: str
    is_active:  bool
    is_expired: bool


# ---------------------------------------------------------------------------
# GET /analytics/{shortCode}
# ---------------------------------------------------------------------------

class ClickTrendPoint(BaseModel):
    day:    str
    clicks: int


class TopReferrer(BaseModel):
    referrer: str
    clicks:   int


class TopCountry(BaseModel):
    country_code: str
    clicks:       int


class AnalyticsResponse(BaseModel):
    short_code:   str
    total_clicks: int
    top_referrers: List[TopReferrer]
    click_trend:   List[ClickTrendPoint]
    top_countries: List[TopCountry]


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

class ComponentHealth(BaseModel):
    component: str
    status:    str
    error:     Optional[str] = None


class HealthResponse(BaseModel):
    status:     str          # "healthy" | "degraded" | "unhealthy"
    version:    str
    components: List[ComponentHealth]


# ---------------------------------------------------------------------------
# Error responses
# ---------------------------------------------------------------------------

class ErrorResponse(BaseModel):
    detail:  str
    code:    Optional[str] = None

    model_config = {"json_schema_extra": {
        "example": {"detail": "URL not found or has expired.", "code": "NOT_FOUND"}
    }}
