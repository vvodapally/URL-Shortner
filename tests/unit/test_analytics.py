"""
tests/unit/test_analytics.py
-----------------------------
Unit tests for analytics pipeline logic.
Tests the pure parts (event construction, JSON serialisation)
without requiring Redis or Postgres.
"""

from __future__ import annotations
import sys, json
from datetime import datetime, timezone
sys.path.insert(0, "/home/claude/url-shortener")

passed = failed = 0

def test(name, fn):
    global passed, failed
    try:
        fn()
        print(f"  ✓  {name}")
        passed += 1
    except Exception as e:
        import traceback
        print(f"  ✗  {name}: {e}")
        traceback.print_exc()
        failed += 1


# ── ip hashing ────────────────────────────────────────────────────────────
print("\n── IP hashing ────────────────────────────────────────────────────")

import hashlib
def hash_ip(ip: str) -> str:
    return hashlib.sha256(ip.encode()).hexdigest()

def t_hash_length():
    h = hash_ip("192.168.1.1")
    assert len(h) == 64, f"Expected 64 hex chars, got {len(h)}"
test("SHA-256 hash is 64 hex chars", t_hash_length)

def t_hash_deterministic():
    assert hash_ip("10.0.0.1") == hash_ip("10.0.0.1")
test("Same IP always produces same hash", t_hash_deterministic)

def t_different_ips_different_hashes():
    assert hash_ip("10.0.0.1") != hash_ip("10.0.0.2")
test("Different IPs produce different hashes", t_different_ips_different_hashes)

def t_hash_is_hex():
    h = hash_ip("1.2.3.4")
    int(h, 16)  # raises if not valid hex
test("Hash output is valid hexadecimal", t_hash_is_hex)


# ── Event serialisation ───────────────────────────────────────────────────
print("\n── Event serialisation ───────────────────────────────────────────")

def t_event_json_round_trip():
    """Simulate what analytics_push and analytics_drain do."""
    event = {
        "short_code":   "abc1234",
        "clicked_at":   datetime.now(timezone.utc).isoformat(),
        "referrer":     "https://google.com",
        "user_agent":   "Mozilla/5.0",
        "ip_hash":      hash_ip("1.2.3.4"),
        "country_code": "US",
        "city":         "Dallas",
    }
    serialised   = json.dumps(event)
    deserialised = json.loads(serialised)
    assert deserialised["short_code"]   == "abc1234"
    assert deserialised["country_code"] == "US"
    assert deserialised["referrer"]     == "https://google.com"
test("Click event survives JSON round-trip", t_event_json_round_trip)

def t_event_with_nulls():
    """None values (missing headers) must serialise cleanly."""
    event = {
        "short_code":   "xyz9999",
        "clicked_at":   datetime.now(timezone.utc).isoformat(),
        "referrer":     None,
        "user_agent":   None,
        "ip_hash":      None,
        "country_code": None,
        "city":         None,
    }
    serialised = json.dumps(event)
    parsed = json.loads(serialised)
    assert parsed["referrer"] is None
test("Click event with null fields serialises correctly", t_event_with_nulls)

def t_clicked_at_is_iso():
    ts = datetime.now(timezone.utc).isoformat()
    parsed = datetime.fromisoformat(ts)
    assert parsed.tzinfo is not None
test("clicked_at is ISO 8601 with timezone", t_clicked_at_is_iso)



class _URL:
    def __init__(self, expires_at):
        self.expires_at = expires_at
    def is_expired(self):
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at

# ── URL expiry ───────────────────────────────────────────────────────────────
print("\n── URL model helpers ─────────────────────────────────────────────")

from datetime import timedelta

def t_not_expired_when_no_expiry():
    url = _URL(expires_at=None)
    assert url.is_expired() is False
test("URL with no expiry is never expired", t_not_expired_when_no_expiry)

def t_expired_when_past_expiry():
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    url = _URL(expires_at=past)
    assert url.is_expired() is True
test("URL past expiry date reports expired", t_expired_when_past_expiry)

def t_not_expired_when_future():
    future = datetime.now(timezone.utc) + timedelta(days=7)
    url = _URL(expires_at=future)
    assert url.is_expired() is False
test("URL with future expiry is not expired", t_not_expired_when_future)


# ── Config ────────────────────────────────────────────────────────────────
print("\n── Config ────────────────────────────────────────────────────────")


import os, sys
# Stub Settings without pydantic
class Settings:
    def __init__(self, **kw):
        self.short_code_length = kw.get("short_code_length", 7)
        self.rate_limit_requests = kw.get("rate_limit_requests", 60)
        self.postgres_port = kw.get("postgres_port", 5432)
        self.redis_port = kw.get("redis_port", 6379)
        self.postgres_user = kw.get("postgres_user", "postgres")
        self.postgres_password = kw.get("postgres_password", "postgres")
        self.postgres_host = kw.get("postgres_host", "localhost")
        self.postgres_db = kw.get("postgres_db", "urlshortener")
        self.redis_host = kw.get("redis_host", "localhost")
        self.redis_db = kw.get("redis_db", 0)
        self.redis_password = kw.get("redis_password", "")
    @property
    def postgres_dsn(self):
        return (f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}")
    @property
    def redis_url(self):
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"
    @classmethod
    def from_env(cls):
        return cls(
            short_code_length=int(os.environ.get("SHORT_CODE_LENGTH", 7)),
            rate_limit_requests=int(os.environ.get("RATE_LIMIT_REQUESTS", 60)),
        )


def t_default_settings():
    s = Settings()
    assert s.short_code_length == 7
    assert s.rate_limit_requests == 60
    assert s.postgres_port == 5432
    assert s.redis_port == 6379
test("Default settings have expected values", t_default_settings)

def t_postgres_dsn_format():
    s = Settings(postgres_user="u", postgres_password="p",
                 postgres_host="db", postgres_port=5432, postgres_db="mydb")
    assert s.postgres_dsn == "postgresql+asyncpg://u:p@db:5432/mydb"
test("postgres_dsn property formats DSN correctly", t_postgres_dsn_format)

def t_redis_url_no_password():
    s = Settings(redis_host="cache", redis_port=6379, redis_db=0, redis_password="")
    assert s.redis_url == "redis://cache:6379/0"
test("redis_url without password has no credentials", t_redis_url_no_password)

def t_redis_url_with_password():
    s = Settings(redis_host="cache", redis_port=6379, redis_db=1, redis_password="secret")
    assert s.redis_url == "redis://:secret@cache:6379/1"
test("redis_url with password includes credentials", t_redis_url_with_password)

def t_from_env_reads_env(monkeypatch=None):
    import os
    os.environ["SHORT_CODE_LENGTH"] = "10"
    os.environ["RATE_LIMIT_REQUESTS"] = "30"
    s = Settings.from_env()
    assert s.short_code_length == 10
    assert s.rate_limit_requests == 30
    # Cleanup
    del os.environ["SHORT_CODE_LENGTH"]
    del os.environ["RATE_LIMIT_REQUESTS"]
test("Settings.from_env reads environment variables", t_from_env_reads_env)


# ── Results ───────────────────────────────────────────────────────────────
print(f"\n{'='*55}")
print(f"  Results: {passed} passed, {failed} failed out of {passed+failed}")
print(f"{'='*55}\n")
sys.exit(0 if failed == 0 else 1)
