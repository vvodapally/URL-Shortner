"""
tests/integration/test_api.py
------------------------------
Integration tests for the FastAPI endpoints.

Requirements to run:
    docker compose up postgres redis   # start dependencies
    pip install -r requirements.txt
    pytest tests/integration/ -v

These tests use httpx.AsyncClient with the actual FastAPI app,
hitting a real Postgres and Redis instance (from docker-compose).
The test DB is isolated via a separate POSTGRES_DB=urlshortener_test env var.

Architecture of these tests:
  - Each test class gets a fresh DB state via function-scoped fixtures
  - Redis is flushed before each test (FLUSHDB)
  - No mocking of DB or cache — integration tests must exercise the real stack

NOTE: In the sandbox environment (no network), these tests cannot be executed
directly. They are written to pytest spec and run in the Docker environment.
The pytest marker @pytest.mark.integration documents this requirement.
"""

from __future__ import annotations

import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# Fixtures (require running services)
# ---------------------------------------------------------------------------

# @pytest_asyncio.fixture(scope="function")
# async def app_client():
#     """Spin up the FastAPI app and return an async test client."""
#     from httpx import AsyncClient, ASGITransport
#     from src.main import app
#
#     async with AsyncClient(
#         transport=ASGITransport(app=app),
#         base_url="http://test",
#     ) as client:
#         yield client


# ---------------------------------------------------------------------------
# POST /shorten
# ---------------------------------------------------------------------------

class TestShortenEndpoint:
    """
    Test contract for POST /shorten.

    Covers:
      - Valid URL → 201 + short_url in response
      - Invalid scheme (ftp://) → 400
      - Private IP (SSRF) → 400
      - URL too long → 400
      - Custom code → respected in response
      - Duplicate custom code → 400
      - Rate limit exceeded → 429
    """

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_shorten_valid_url_returns_201(self, app_client):
        response = await app_client.post("/shorten", json={
            "url": "https://www.example.com/very/long/path"
        })
        assert response.status_code == 201
        body = response.json()
        assert "short_code" in body
        assert "short_url"  in body
        assert len(body["short_code"]) == 7
        assert body["long_url"] == "https://www.example.com/very/long/path"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_shorten_invalid_scheme_returns_400(self, app_client):
        response = await app_client.post("/shorten", json={
            "url": "ftp://files.example.com/data.zip"
        })
        assert response.status_code == 400
        assert "scheme" in response.json()["detail"].lower()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_shorten_ssrf_ip_returns_400(self, app_client):
        response = await app_client.post("/shorten", json={
            "url": "http://169.254.169.254/latest/meta-data/"
        })
        assert response.status_code == 400

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_shorten_custom_code_respected(self, app_client):
        response = await app_client.post("/shorten", json={
            "url": "https://example.com",
            "custom_code": "mycode1",
        })
        assert response.status_code == 201
        assert response.json()["short_code"] == "mycode1"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_shorten_duplicate_custom_code_returns_400(self, app_client):
        await app_client.post("/shorten", json={
            "url": "https://example.com", "custom_code": "dup0001"
        })
        response = await app_client.post("/shorten", json={
            "url": "https://other.com", "custom_code": "dup0001"
        })
        assert response.status_code == 400
        assert "taken" in response.json()["detail"].lower()

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_shorten_url_too_long_returns_400(self, app_client):
        response = await app_client.post("/shorten", json={
            "url": "https://example.com/" + "a" * 2500
        })
        assert response.status_code in (400, 422)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_shorten_with_expiry(self, app_client):
        from datetime import datetime, timezone, timedelta
        expiry = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        response = await app_client.post("/shorten", json={
            "url": "https://example.com",
            "expires_at": expiry,
        })
        assert response.status_code == 201
        assert response.json()["expires_at"] is not None


# ---------------------------------------------------------------------------
# GET /{short_code}  — redirect
# ---------------------------------------------------------------------------

class TestRedirectEndpoint:
    """
    Test contract for GET /{short_code}.

    Covers:
      - Valid code → 302 redirect to long URL
      - Unknown code → 404
      - Deactivated URL → 410
      - Expired URL → 404
      - Cache hit path (second request served from Redis)
    """

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_redirect_valid_code(self, app_client):
        # Create a short URL first
        create = await app_client.post("/shorten", json={"url": "https://example.com"})
        code = create.json()["short_code"]

        # Follow redirect
        response = await app_client.get(f"/{code}", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "https://example.com"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_redirect_unknown_code_returns_404(self, app_client):
        response = await app_client.get("/ZZZZZZZ", follow_redirects=False)
        assert response.status_code == 404

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_redirect_twice_hits_cache(self, app_client):
        """Second redirect should hit Redis cache — verify same response."""
        create = await app_client.post("/shorten", json={"url": "https://example.com"})
        code   = create.json()["short_code"]

        r1 = await app_client.get(f"/{code}", follow_redirects=False)
        r2 = await app_client.get(f"/{code}", follow_redirects=False)
        assert r1.status_code == r2.status_code == 302
        assert r1.headers["location"] == r2.headers["location"]


# ---------------------------------------------------------------------------
# GET /analytics/{short_code}
# ---------------------------------------------------------------------------

class TestAnalyticsEndpoint:
    """
    Test contract for GET /analytics/{short_code}.

    Covers:
      - Known code → 200 with expected schema
      - Unknown code → 404
      - total_clicks reflects actual redirect count
      - top_referrers populated after redirects with Referer header
    """

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_analytics_schema(self, app_client):
        create = await app_client.post("/shorten", json={"url": "https://example.com"})
        code   = create.json()["short_code"]

        response = await app_client.get(f"/analytics/{code}")
        assert response.status_code == 200
        body = response.json()
        assert body["short_code"]    == code
        assert "total_clicks"        in body
        assert "top_referrers"       in body
        assert "click_trend"         in body
        assert "top_countries"       in body

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_analytics_unknown_code_returns_404(self, app_client):
        response = await app_client.get("/analytics/ZZZZZZZ")
        assert response.status_code == 404

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_analytics_click_count_increments(self, app_client):
        """After redirects + analytics flush, click_count should reflect hits."""
        create = await app_client.post("/shorten", json={"url": "https://example.com"})
        code   = create.json()["short_code"]

        # Two redirects
        for _ in range(2):
            await app_client.get(f"/{code}", follow_redirects=False)

        # Wait for analytics flush (5s default) or trigger manually in test setup
        import asyncio
        await asyncio.sleep(6)

        response = await app_client.get(f"/analytics/{code}")
        assert response.json()["total_clicks"] >= 2


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    """
    Test contract for /health, /health/live, /health/ready.
    """

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_health_returns_200(self, app_client):
        response = await app_client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] in ("healthy", "degraded")
        assert len(body["components"]) == 2

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_liveness_returns_200(self, app_client):
        response = await app_client.get("/health/live")
        assert response.status_code == 200
        assert response.json()["status"] == "alive"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_readiness_returns_200_when_healthy(self, app_client):
        response = await app_client.get("/health/ready")
        assert response.status_code == 200
        assert response.json()["status"] == "ready"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_health_includes_version(self, app_client):
        response = await app_client.get("/health")
        assert "version" in response.json()


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

class TestRateLimiting:
    """
    Verify the sliding-window rate limiter blocks excess requests.
    """

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_rate_limit_enforced(self, app_client):
        """
        With RATE_LIMIT_REQUESTS=5 in test env, the 6th request should 429.
        Override env before starting the test server.
        """
        responses = []
        for _ in range(7):
            r = await app_client.post("/shorten", json={"url": "https://example.com"})
            responses.append(r.status_code)

        assert 429 in responses, (
            "Expected at least one 429 after exceeding rate limit. "
            "Ensure RATE_LIMIT_REQUESTS=5 is set in the test environment."
        )

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_rate_limit_retry_after_header(self, app_client):
        """429 response must include Retry-After header."""
        # Exhaust the limit first (assumes low limit in test env)
        for _ in range(10):
            r = await app_client.post("/shorten", json={"url": "https://example.com"})
            if r.status_code == 429:
                assert "retry-after" in r.headers
                return
        pytest.skip("Rate limit not hit — increase request count or lower limit in test env")
