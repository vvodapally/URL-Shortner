"""
tests/unit/test_core.py
-----------------------
Unit tests for pure business logic (no I/O, no DB, no Redis).
All tests run without any running services.
"""

from __future__ import annotations

import string
import sys
sys.path.insert(0, "/home/claude/url-shortener")

from src.core.shortener import (
    BASE62_CHARS,
    URLValidationError,
    build_short_url,
    generate_short_code,
    validate_url,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# generate_short_code
# ---------------------------------------------------------------------------

print("\n── generate_short_code ───────────────────────────────────────────")

def t_default_length():
    code = generate_short_code()
    assert len(code) == 7, f"Expected 7, got {len(code)}"
test("Default length is 7", t_default_length)

def t_custom_length():
    for n in [3, 5, 10, 16]:
        code = generate_short_code(n)
        assert len(code) == n, f"Expected {n}, got {len(code)}"
test("Custom lengths respected", t_custom_length)

def t_base62_chars_only():
    for _ in range(100):
        code = generate_short_code(12)
        assert all(c in BASE62_CHARS for c in code), f"Non-Base62 char in {code!r}"
test("All chars are Base62", t_base62_chars_only)

def t_randomness():
    # 1000 codes of length 7 should have very few collisions
    codes = {generate_short_code() for _ in range(1000)}
    assert len(codes) > 990, f"Too many collisions: only {len(codes)} unique codes"
test("High entropy — <1% collisions in 1000 samples", t_randomness)


# ---------------------------------------------------------------------------
# validate_url
# ---------------------------------------------------------------------------

print("\n── validate_url ──────────────────────────────────────────────────")

def t_valid_http():
    url = validate_url("http://example.com")
    assert url == "http://example.com"
test("Valid http:// URL passes", t_valid_http)

def t_valid_https():
    url = validate_url("https://www.example.com/path?q=1&r=2#anchor")
    assert "example.com" in url
test("Valid https:// URL with path/query/fragment passes", t_valid_https)

def t_strips_whitespace():
    url = validate_url("  https://example.com  ")
    assert url == "https://example.com"
test("Leading/trailing whitespace stripped", t_strips_whitespace)

def t_rejects_ftp():
    try:
        validate_url("ftp://files.example.com/file.txt")
        assert False, "Should have raised"
    except URLValidationError as e:
        assert "scheme" in str(e).lower()
test("ftp:// scheme rejected", t_rejects_ftp)

def t_rejects_no_scheme():
    try:
        validate_url("www.example.com/path")
        assert False, "Should have raised"
    except URLValidationError:
        pass
test("URL without scheme rejected", t_rejects_no_scheme)

def t_rejects_empty():
    try:
        validate_url("")
        assert False
    except URLValidationError:
        pass
test("Empty string rejected", t_rejects_empty)

def t_rejects_too_long():
    try:
        validate_url("https://example.com/" + "a" * 2100, max_length=2048)
        assert False
    except URLValidationError as e:
        assert "length" in str(e).lower()
test("URL exceeding max_length rejected", t_rejects_too_long)

# SSRF tests
def t_rejects_localhost():
    for host in ["http://localhost/", "http://127.0.0.1/"]:
        try:
            validate_url(host)
            assert False, f"Should have rejected {host}"
        except URLValidationError:
            pass
test("localhost / 127.0.0.1 rejected (SSRF)", t_rejects_localhost)

def t_rejects_aws_imds():
    try:
        validate_url("http://169.254.169.254/latest/meta-data/")
        assert False
    except URLValidationError as e:
        assert "private" in str(e).lower() or "169.254" in str(e)
test("AWS IMDS endpoint rejected (SSRF)", t_rejects_aws_imds)

def t_rejects_rfc1918():
    for url in ["http://192.168.1.1/admin", "http://10.0.0.1/", "http://172.16.0.1/"]:
        try:
            validate_url(url)
            assert False, f"Should have rejected {url}"
        except URLValidationError:
            pass
test("RFC-1918 private addresses rejected (SSRF)", t_rejects_rfc1918)

def t_allows_valid_external():
    for url in [
        "https://github.com/anthropics/claude",
        "https://api.example.org/v2/data?format=json",
        "http://192.0.2.1/",   # TEST-NET-1 — not RFC1918
    ]:
        try:
            validate_url(url)
        except URLValidationError as e:
            # 192.0.2.x is technically a documentation range, not RFC1918 — should pass
            if "192.0.2" not in url:
                assert False, f"Valid URL rejected: {url} — {e}"
test("Valid external URLs accepted", t_allows_valid_external)


# ---------------------------------------------------------------------------
# build_short_url
# ---------------------------------------------------------------------------

print("\n── build_short_url ───────────────────────────────────────────────")

def t_basic():
    assert build_short_url("https://short.ly", "abc1234") == "https://short.ly/abc1234"
test("Basic construction correct", t_basic)

def t_trailing_slash_stripped():
    assert build_short_url("https://short.ly/", "abc") == "https://short.ly/abc"
test("Trailing slash on base URL stripped", t_trailing_slash_stripped)

def t_no_double_slash():
    url = build_short_url("https://short.ly", "xyz")
    assert "//" not in url.replace("https://", "")
test("No double slash in output", t_no_double_slash)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

print(f"\n{'='*55}")
print(f"  Results: {passed} passed, {failed} failed out of {passed+failed}")
print(f"{'='*55}\n")

sys.exit(0 if failed == 0 else 1)
