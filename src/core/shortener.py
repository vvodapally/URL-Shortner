"""
src/core/shortener.py
---------------------
Pure business logic for the URL shortener.

No I/O here — all functions are deterministic and testable in isolation.
The API layer calls these and passes results to DB/cache layers.

Design decisions
----------------
Short code algorithm : Base62 random (not hash-based)
  - Hash-based (MD5/SHA truncation) produces collisions that are hard to
    detect without a DB round-trip. Random Base62 of length 7 gives
    62^7 ≈ 3.5 trillion combinations — collision probability under 1%
    up to ~60 million URLs (birthday problem).
  - Collision handling: the DB layer retries with a new code on UNIQUE
    constraint violation (expected to be astronomically rare).

URL validation : Schema check + hostname extraction
  - We validate that the URL is parseable and has an http/https scheme.
  - We do NOT do live DNS resolution — that adds latency and makes the
    endpoint dependent on network availability.
  - We DO reject known localhost/private network targets to prevent
    SSRF (Server-Side Request Forgery) via the redirect endpoint.
"""

from __future__ import annotations

import ipaddress
import re
import secrets
import string
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE62_CHARS = string.ascii_letters + string.digits  # 62 chars: a-z A-Z 0-9
_PRIVATE_HOSTNAMES = frozenset({
    "localhost", "127.0.0.1", "0.0.0.0", "::1",
    "metadata.google.internal",   # GCP metadata endpoint
    "169.254.169.254",            # AWS/GCP/Azure IMDS — classic SSRF target
})
_PRIVATE_PREFIXES = ("10.", "172.16.", "172.17.", "172.18.", "172.19.",
                     "172.20.", "172.21.", "172.22.", "172.23.", "172.24.",
                     "172.25.", "172.26.", "172.27.", "172.28.", "172.29.",
                     "172.30.", "172.31.", "192.168.")


# ---------------------------------------------------------------------------
# Short code generation
# ---------------------------------------------------------------------------

def generate_short_code(length: int = 7) -> str:
    """
    Return a cryptographically random Base62 string of `length` characters.

    Uses `secrets.choice` (backed by os.urandom) — not `random.choice` —
    so the output is unpredictable and resistant to enumeration attacks.

    >>> len(generate_short_code()) == 7
    True
    >>> all(c in BASE62_CHARS for c in generate_short_code())
    True
    """
    return "".join(secrets.choice(BASE62_CHARS) for _ in range(length))


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------

class URLValidationError(ValueError):
    """Raised when a submitted URL fails validation."""
    pass


def validate_url(url: str, max_length: int = 2048) -> str:
    """
    Validate and normalise a URL submitted for shortening.

    Returns the normalised URL string on success.
    Raises URLValidationError with a user-facing message on failure.

    Checks performed (in order):
    1. Length limit
    2. Parseable by urllib
    3. Scheme is http or https
    4. Hostname is present
    5. Not a private/loopback/SSRF-risk target
    """
    if not url or not isinstance(url, str):
        raise URLValidationError("URL must be a non-empty string.")

    url = url.strip()

    if len(url) > max_length:
        raise URLValidationError(
            f"URL exceeds maximum length of {max_length} characters."
        )

    try:
        parsed = urlparse(url)
    except Exception as exc:
        raise URLValidationError(f"URL could not be parsed: {exc}") from exc

    if parsed.scheme not in ("http", "https"):
        raise URLValidationError(
            f"URL scheme must be 'http' or 'https', got {parsed.scheme!r}."
        )

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise URLValidationError("URL must include a hostname.")

    _check_ssrf(hostname)

    return url


def _check_ssrf(hostname: str) -> None:
    """Raise URLValidationError if the hostname targets a private network."""
    if hostname in _PRIVATE_HOSTNAMES:
        raise URLValidationError(
            f"URLs pointing to private/loopback addresses are not allowed: {hostname!r}"
        )
    if any(hostname.startswith(prefix) for prefix in _PRIVATE_PREFIXES):
        raise URLValidationError(
            f"URLs pointing to RFC-1918 private addresses are not allowed: {hostname!r}"
        )
    # Try parsing as a raw IP address
    try:
        addr = ipaddress.ip_address(hostname)
        if addr.is_private or addr.is_loopback or addr.is_link_local:
            raise URLValidationError(
                f"URLs pointing to private/loopback IP addresses are not allowed: {hostname!r}"
            )
    except ValueError:
        pass  # hostname is a domain name — fine


# ---------------------------------------------------------------------------
# Short URL construction
# ---------------------------------------------------------------------------

def build_short_url(base_url: str, short_code: str) -> str:
    """
    Combine the service base URL with a short code.

    >>> build_short_url("https://short.ly", "abc1234")
    'https://short.ly/abc1234'
    """
    return f"{base_url.rstrip('/')}/{short_code}"
