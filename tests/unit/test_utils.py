"""
tests/unit/test_utils.py
-------------------------
Unit tests for logger and retry decorator.
No I/O — all async tests use a simple event loop shim.
"""

from __future__ import annotations
import sys, asyncio, json, logging
sys.path.insert(0, "/home/claude/url-shortener")

passed = failed = 0

def test(name, fn):
    global passed, failed
    try:
        if asyncio.iscoroutinefunction(fn):
            asyncio.run(fn())
        else:
            fn()
        print(f"  ✓  {name}")
        passed += 1
    except Exception as e:
        import traceback
        print(f"  ✗  {name}: {e}")
        traceback.print_exc()
        failed += 1

# ── Logger ────────────────────────────────────────────────────────────────
print("\n── Logger ────────────────────────────────────────────────────────")

from src.utils.logger import get_logger, JsonFormatter, TextFormatter

def t_json_output():
    import io
    log = logging.getLogger("test_json_unique")
    log.handlers.clear()
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(JsonFormatter())
    log.addHandler(h)
    log.setLevel(logging.DEBUG)
    log.info("hello world")
    line = buf.getvalue().strip()
    parsed = json.loads(line)
    assert parsed["msg"]   == "hello world"
    assert parsed["level"] == "INFO"
    assert "ts" in parsed
test("JSON formatter produces valid JSON with required fields", t_json_output)

def t_extra_fields():
    import io
    log = logging.getLogger("test_extra_unique")
    log.handlers.clear()
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(JsonFormatter())
    log.addHandler(h)
    log.setLevel(logging.DEBUG)
    log.info("with extra", extra={"request_id": "abc123", "ms": 42})
    parsed = json.loads(buf.getvalue().strip())
    assert parsed.get("request_id") == "abc123"
    assert parsed.get("ms") == 42
test("Extra fields included in JSON output", t_extra_fields)

def t_get_logger_returns_logger():
    log = get_logger("mymodule", level="WARNING", fmt="text")
    assert isinstance(log, logging.Logger)
    assert log.level == logging.WARNING
test("get_logger returns configured Logger instance", t_get_logger_returns_logger)

def t_no_duplicate_handlers():
    log = get_logger("dedup_test", fmt="json")
    count_before = len(log.handlers)
    get_logger("dedup_test", fmt="json")  # second call
    assert len(log.handlers) == count_before
test("get_logger does not add duplicate handlers", t_no_duplicate_handlers)


# ── Retry decorator ───────────────────────────────────────────────────────
print("\n── Retry decorator ───────────────────────────────────────────────")

from src.utils.retry import retry

async def t_success_first_try():
    calls = []
    @retry(max_attempts=3, base_delay=0.0)
    async def fn():
        calls.append(1)
        return "ok"
    result = await fn()
    assert result == "ok"
    assert len(calls) == 1
test("Succeeds on first attempt — no retry", t_success_first_try)

async def t_retries_and_succeeds():
    calls = []
    @retry(max_attempts=3, base_delay=0.0, jitter=False)
    async def fn():
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("transient")
        return "recovered"
    result = await fn()
    assert result == "recovered"
    assert len(calls) == 3
test("Retries on transient error and eventually succeeds", t_retries_and_succeeds)

async def t_raises_after_exhaustion():
    @retry(max_attempts=2, base_delay=0.0, jitter=False)
    async def fn():
        raise ValueError("permanent")
    try:
        await fn()
        assert False, "Should have raised"
    except ValueError as e:
        assert "permanent" in str(e)
test("Raises original exception after all retries exhausted", t_raises_after_exhaustion)

async def t_only_retries_specified_exceptions():
    calls = []
    @retry(max_attempts=3, base_delay=0.0, exceptions=(ConnectionError,))
    async def fn():
        calls.append(1)
        raise TypeError("not retried")
    try:
        await fn()
    except TypeError:
        pass
    assert len(calls) == 1  # no retry on TypeError
test("Only retries specified exception types", t_only_retries_specified_exceptions)

async def t_preserves_return_value():
    @retry(max_attempts=1)
    async def fn():
        return {"key": "value", "n": 42}
    result = await fn()
    assert result == {"key": "value", "n": 42}
test("Return value preserved through decorator", t_preserves_return_value)


# ── Results ───────────────────────────────────────────────────────────────
print(f"\n{'='*55}")
print(f"  Results: {passed} passed, {failed} failed out of {passed+failed}")
print(f"{'='*55}\n")
sys.exit(0 if failed == 0 else 1)
