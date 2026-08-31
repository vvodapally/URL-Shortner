"""
orchestration/tasks/shared.py
------------------------------
Reusable task functions shared across Scenario 1, 2, and 3.

Every function here matches the Task callable signature:
    fn(context: dict) -> dict

The returned dict is merged into the shared context so downstream
tasks can read what upstream tasks produced.

Design principle: tasks are side-effect descriptions, not direct
executors. In a real agentic system these would invoke Claude/Copilot
to generate code; here they document the decision they represent and
return structured artifacts so the audit trail is meaningful.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Requirement Understanding
# ---------------------------------------------------------------------------

def parse_requirement(context: dict) -> dict:
    """
    T1 — Parse the raw requirement into a structured problem statement.

    In an agentic system, this would call an LLM with the raw requirement
    text and return a structured JSON decomposition. Here we return the
    pre-analysed structure so the orchestration mechanics are the focus.
    """
    scenario = context.get("scenario", "unknown")
    raw_req  = context.get("raw_requirement", "(none provided)")

    parsed = {
        "scenario":         scenario,
        "raw_requirement":  raw_req,
        "parsed_at":        datetime.now(timezone.utc).isoformat(),
        "problem_statement": _problem_statements.get(scenario, raw_req),
    }
    print(f"\n    📋 Requirement parsed for scenario: {scenario}")
    print(f"       Problem: {parsed['problem_statement'][:80]}...")
    return {"parsed_requirement": parsed}


_problem_statements = {
    "greenfield": (
        "Build a production-grade URL shortener service from scratch with "
        "POST /shorten, GET /{code} redirect, analytics tracking, rate limiting, "
        "health checks, and OpenAPI documentation."
    ),
    "brownfield": (
        "Enhance an existing URL shortener service by adding richer analytics: "
        "top referrers, click trends over time, and geographic bucketing by country. "
        "Preserve the existing API contract; all changes are additive."
    ),
    "ambiguous": (
        "The vague requirement 'make it reliable' has been interpreted as: "
        "connection pooling, Redis persistence, retry logic with exponential backoff, "
        "structured logging, enhanced health checks, Docker restart policies, "
        "and dead-letter handling for failed analytics writes."
    ),
}


# ---------------------------------------------------------------------------
# Ambiguity Detection
# ---------------------------------------------------------------------------

def detect_ambiguity(context: dict) -> dict:
    """
    T2 — Identify ambiguous terms and resolve them with explicit decisions.
    Triggered automatically when scenario == 'ambiguous'.
    """
    scenario = context.get("scenario")
    ambiguities = []

    if scenario == "ambiguous":
        ambiguities = [
            {
                "term":       "reliable",
                "interpretations": [
                    "High availability (no downtime)",
                    "Data durability (no data loss)",
                    "Graceful error recovery",
                    "Observability (know when broken)",
                    "Traffic resilience (survive spikes)",
                ],
                "resolution": (
                    "Implement all HIGH priority interpretations: "
                    "connection pooling, Redis AOF, retry with backoff, "
                    "structured logging, enhanced /health endpoint, "
                    "Docker restart-always policy."
                ),
                "deferred": [
                    "API versioning (LOW — no breaking changes planned)",
                    "Multi-region failover (OUT OF SCOPE for assessment)",
                ],
            }
        ]
        print(f"\n    🔍 Ambiguity detected: 'reliable' resolved to "
              f"{len(ambiguities[0]['interpretations'])} interpretations")
        print(f"       Resolution: HIGH priority items only (human gate required)")

    return {
        "ambiguities":      ambiguities,
        "ambiguity_count":  len(ambiguities),
        "assumptions": context.get("assumptions", []) + [
            "Base URL is configurable via BASE_URL env var",
            "Short codes are 7-char Base62 (collision-safe to ~60M URLs)",
            "Analytics writes are eventually consistent (async pipeline)",
            "Rate limiting is per-IP sliding window, not per-user",
        ],
    }


# ---------------------------------------------------------------------------
# Architecture Design
# ---------------------------------------------------------------------------

def design_architecture(context: dict) -> dict:
    """
    T3 — Produce the architecture decision record (ADR) for this scenario.
    """
    scenario = context.get("scenario", "greenfield")

    decisions = _architecture_decisions.get(scenario, [])
    print(f"\n    🏗  Architecture designed ({len(decisions)} decisions recorded)")
    for d in decisions[:3]:
        print(f"       • {d['decision']}")
    if len(decisions) > 3:
        print(f"       … and {len(decisions)-3} more")

    return {
        "architecture_decisions": decisions,
        "tech_stack": {
            "backend":        "Python 3.12 / FastAPI 0.115",
            "database":       "PostgreSQL 16 (asyncpg driver)",
            "cache":          "Redis 7.2",
            "containerisation": "Docker 26 + Docker Compose v2",
            "testing":        "pytest + pytest-asyncio + httpx",
            "api_docs":       "OpenAPI 3.1 (auto-generated by FastAPI)",
        },
    }


_architecture_decisions = {
    "greenfield": [
        {
            "decision":   "Base62 random short codes (not hash-based)",
            "rationale":  "Avoids MD5 collisions; 62^7 ≈ 3.5T combinations",
            "trade_off":  "Requires uniqueness check on insert; collision rate negligible",
        },
        {
            "decision":   "Redis for hot-path reads, Postgres as source of truth",
            "rationale":  "Redis O(1) GET keeps redirect p50 < 5ms; Postgres ensures durability",
            "trade_off":  "Write-through cache adds one extra write on POST /shorten",
        },
        {
            "decision":   "Async analytics pipeline (Redis queue → Postgres batch flush)",
            "rationale":  "Decouples redirect latency from analytics write latency",
            "trade_off":  "Analytics are eventually consistent; up to flush_interval delay",
        },
        {
            "decision":   "ip_hash (SHA-256) instead of raw IP for analytics",
            "rationale":  "GDPR Art. 25 data minimisation; still detects abuse patterns",
            "trade_off":  "Cannot reverse-lookup IP from hash; by design",
        },
        {
            "decision":   "FastAPI dependency injection for DB sessions",
            "rationale":  "Auto-commit on success, auto-rollback on exception, per-request scope",
            "trade_off":  "Slight overhead vs raw asyncpg; worth it for correctness guarantees",
        },
    ],
    "brownfield": [
        {
            "decision":   "country_code column added as nullable",
            "rationale":  "Pre-migration rows have no geo data; NOT NULL would fail backfill",
            "trade_off":  "Analytics queries must handle null country_code values",
        },
        {
            "decision":   "Additive-only migration — no existing columns altered",
            "rationale":  "Zero risk of breaking existing API responses or indexes",
            "trade_off":  "Old rows show country_code=null; acceptable for new feature",
        },
        {
            "decision":   "Analytics endpoint caches aggregations in Redis (5-min TTL)",
            "rationale":  "Aggregation queries on large clicks tables are expensive",
            "trade_off":  "Stats may lag up to 5 minutes; acceptable for a dashboard",
        },
    ],
    "ambiguous": [
        {
            "decision":   "SQLAlchemy async connection pool (min=2, max=10)",
            "rationale":  "Prevents connection exhaustion under load; pre-ping validates health",
            "trade_off":  "Idle connections consume Postgres resources; tunable via env vars",
        },
        {
            "decision":   "Redis AOF (append-only file) persistence enabled",
            "rationale":  "Survives Redis restart without losing the analytics queue buffer",
            "trade_off":  "Slightly higher disk I/O; acceptable for reliability goal",
        },
        {
            "decision":   "Exponential backoff retry on all DB and Redis calls",
            "rationale":  "Transient network blips self-heal without cascading failures",
            "trade_off":  "Max retry delay adds latency on persistent failures; bounded by max_attempts",
        },
        {
            "decision":   "Docker restart: always policy on all services",
            "rationale":  "Auto-recovery from OOM kills and container crashes",
            "trade_off":  "May mask bugs if crash loops are fast; mitigated by health checks",
        },
    ],
}


# ---------------------------------------------------------------------------
# Task Dependency Graph Output
# ---------------------------------------------------------------------------

def build_task_graph(context: dict) -> dict:
    """
    T4 — Document the task dependency graph for this scenario.
    Returns a structured representation suitable for human review at Gate 1.
    """
    scenario = context.get("scenario", "greenfield")
    graph    = _task_graphs.get(scenario, {})

    print(f"\n    🔗 Task graph built: {len(graph.get('tasks', []))} tasks, "
          f"{len(graph.get('parallel_groups', []))} parallel groups")

    return {
        "task_graph": graph,
        "parallelisable_tasks": graph.get("parallel_groups", []),
    }


_task_graphs = {
    "greenfield": {
        "tasks": [
            {"id": "DB_SCHEMA",    "depends_on": [],              "parallel_group": "A"},
            {"id": "CODE_GEN",     "depends_on": [],              "parallel_group": "A"},
            {"id": "REDIS_SCHEMA", "depends_on": ["DB_SCHEMA"],   "parallel_group": "B"},
            {"id": "API_CORE",     "depends_on": ["DB_SCHEMA", "CODE_GEN"], "parallel_group": "B"},
            {"id": "ANALYTICS",    "depends_on": ["DB_SCHEMA"],   "parallel_group": "B"},
            {"id": "RATE_LIMIT",   "depends_on": ["REDIS_SCHEMA"], "parallel_group": "B"},
            {"id": "HEALTH",       "depends_on": ["DB_SCHEMA", "REDIS_SCHEMA"], "parallel_group": "B"},
            {"id": "OPENAPI",      "depends_on": ["API_CORE"],    "parallel_group": "C"},
            {"id": "UNIT_TESTS",   "depends_on": ["API_CORE"],    "parallel_group": "C"},
            {"id": "INT_TESTS",    "depends_on": ["API_CORE", "ANALYTICS", "RATE_LIMIT"], "parallel_group": "D"},
            {"id": "DOCKER",       "depends_on": ["INT_TESTS"],   "parallel_group": "E"},
        ],
        "parallel_groups": [
            {"group": "A", "tasks": ["DB_SCHEMA", "CODE_GEN"],          "note": "No dependencies — run first"},
            {"group": "B", "tasks": ["REDIS_SCHEMA", "API_CORE", "ANALYTICS", "RATE_LIMIT", "HEALTH"], "note": "All unblock when A completes"},
            {"group": "C", "tasks": ["OPENAPI", "UNIT_TESTS"],          "note": "After API_CORE"},
            {"group": "D", "tasks": ["INT_TESTS"],                      "note": "After all B tasks complete"},
            {"group": "E", "tasks": ["DOCKER"],                         "note": "After integration tests pass"},
        ],
    },
    "brownfield": {
        "tasks": [
            {"id": "IMPACT_ANALYSIS",  "depends_on": [],                   "parallel_group": "A"},
            {"id": "MIGRATION_PLAN",   "depends_on": ["IMPACT_ANALYSIS"],  "parallel_group": "B"},
            {"id": "ROLLBACK_SCRIPT",  "depends_on": ["MIGRATION_PLAN"],   "parallel_group": "B"},
            {"id": "GEO_UTILITY",      "depends_on": ["IMPACT_ANALYSIS"],  "parallel_group": "B"},
            {"id": "APPLY_MIGRATION",  "depends_on": ["MIGRATION_PLAN", "ROLLBACK_SCRIPT"], "parallel_group": "C"},
            {"id": "ANALYTICS_QUERIES","depends_on": ["APPLY_MIGRATION"],  "parallel_group": "D"},
            {"id": "ANALYTICS_ENDPOINT","depends_on":["ANALYTICS_QUERIES","GEO_UTILITY"],"parallel_group": "D"},
            {"id": "ANALYTICS_CACHE",  "depends_on": ["ANALYTICS_ENDPOINT"],"parallel_group": "E"},
            {"id": "UPDATE_TESTS",     "depends_on": ["ANALYTICS_ENDPOINT"],"parallel_group": "E"},
        ],
        "parallel_groups": [
            {"group": "A", "tasks": ["IMPACT_ANALYSIS"],   "note": "Read-only codebase scan"},
            {"group": "B", "tasks": ["MIGRATION_PLAN", "ROLLBACK_SCRIPT", "GEO_UTILITY"], "note": "Parallel after analysis"},
            {"group": "C", "tasks": ["APPLY_MIGRATION"],   "note": "Gated — requires human approval"},
            {"group": "D", "tasks": ["ANALYTICS_QUERIES", "ANALYTICS_ENDPOINT"], "note": "Parallel after migration"},
            {"group": "E", "tasks": ["ANALYTICS_CACHE", "UPDATE_TESTS"], "note": "Parallel final tasks"},
        ],
    },
    "ambiguous": {
        "tasks": [
            {"id": "INTERPRET_REQ",   "depends_on": [],                    "parallel_group": "A"},
            {"id": "CONN_POOL",       "depends_on": ["INTERPRET_REQ"],     "parallel_group": "B"},
            {"id": "REDIS_PERSIST",   "depends_on": ["INTERPRET_REQ"],     "parallel_group": "B"},
            {"id": "RETRY_LOGIC",     "depends_on": ["INTERPRET_REQ"],     "parallel_group": "B"},
            {"id": "STRUCT_LOGGING",  "depends_on": ["INTERPRET_REQ"],     "parallel_group": "B"},
            {"id": "HEALTH_ENHANCED", "depends_on": ["CONN_POOL", "REDIS_PERSIST"], "parallel_group": "C"},
            {"id": "DOCKER_RESTART",  "depends_on": ["INTERPRET_REQ"],     "parallel_group": "B"},
            {"id": "DEAD_LETTER",     "depends_on": ["RETRY_LOGIC"],       "parallel_group": "C"},
            {"id": "VALIDATE_ALL",    "depends_on": ["HEALTH_ENHANCED", "DEAD_LETTER"], "parallel_group": "D"},
        ],
        "parallel_groups": [
            {"group": "A", "tasks": ["INTERPRET_REQ"],    "note": "Disambiguation — gated by human"},
            {"group": "B", "tasks": ["CONN_POOL", "REDIS_PERSIST", "RETRY_LOGIC", "STRUCT_LOGGING", "DOCKER_RESTART"], "note": "All parallel after interpretation"},
            {"group": "C", "tasks": ["HEALTH_ENHANCED", "DEAD_LETTER"], "note": "Parallel after B"},
            {"group": "D", "tasks": ["VALIDATE_ALL"],     "note": "Final validation gate"},
        ],
    },
}


# ---------------------------------------------------------------------------
# Risk Register
# ---------------------------------------------------------------------------

def build_risk_register(context: dict) -> dict:
    """T5 — Identify and score risks for this scenario."""
    scenario = context.get("scenario", "greenfield")
    risks    = _risks.get(scenario, [])

    print(f"\n    ⚠️  Risk register built: {len(risks)} risks identified")
    high = [r for r in risks if r["severity"] == "HIGH"]
    if high:
        print(f"       HIGH severity: {[r['risk'] for r in high]}")

    return {
        "risks": [r["risk"] for r in risks],
        "risk_register": risks,
        "limitations": _limitations.get(scenario, []),
    }


_risks = {
    "greenfield": [
        {"risk": "Short code collision",          "severity": "LOW",    "mitigation": "Retry with new code on DB UNIQUE violation"},
        {"risk": "Redis unavailability",          "severity": "MEDIUM", "mitigation": "Fallback to Postgres-only mode; cache miss increases latency"},
        {"risk": "Analytics queue overflow",      "severity": "LOW",    "mitigation": "Redis List is bounded by available memory; flush interval keeps it small"},
        {"risk": "SSRF via redirect endpoint",    "severity": "HIGH",   "mitigation": "URL validation rejects private IPs and localhost"},
        {"risk": "Rate limit bypass via IP spoof","severity": "MEDIUM", "mitigation": "Rate limit is advisory; production would add WAF"},
    ],
    "brownfield": [
        {"risk": "Migration breaks existing rows","severity": "HIGH",   "mitigation": "Nullable column; rollback script generated before migration runs"},
        {"risk": "Geo-lookup adds redirect latency","severity":"MEDIUM","mitigation": "Geo-lookup runs in BackgroundTask, not in redirect path"},
        {"risk": "Analytics cache stale data",    "severity": "LOW",    "mitigation": "5-min TTL; acceptable for a dashboard use case"},
        {"risk": "MaxMind DB not present",        "severity": "MEDIUM", "mitigation": "Geo-lookup fails gracefully; country_code stays null"},
    ],
    "ambiguous": [
        {"risk": "Misinterpreting 'reliable'",   "severity": "HIGH",   "mitigation": "Human gate 1 requires explicit approval of interpretation"},
        {"risk": "Retry storms on DB outage",    "severity": "MEDIUM", "mitigation": "Bounded retries (max 3) with jitter prevent thundering herd"},
        {"risk": "AOF disk exhaustion",          "severity": "LOW",    "mitigation": "Docker volume mount; monitor disk usage"},
        {"risk": "Restart loop masking bugs",    "severity": "MEDIUM", "mitigation": "Health checks prevent restart loops from appearing healthy"},
    ],
}

_limitations = {
    "greenfield": [
        "No authentication — any client can shorten any URL",
        "Rate limiting is in-process; a horizontally scaled deployment needs Redis-backed rate limiting",
        "No URL preview endpoint before redirect",
    ],
    "brownfield": [
        "Geographic data requires MaxMind GeoLite2 database (free, requires registration)",
        "click_trend query uses date truncation — not suitable for sub-daily granularity",
    ],
    "ambiguous": [
        "Reliability improvements are defensive, not tested under load (no k6/Locust in this assessment)",
        "Dead-letter queue is a Redis List — not a durable message queue; surviving Redis restart requires AOF",
    ],
}


# ---------------------------------------------------------------------------
# Implementation task stubs (represent code generation phases)
# ---------------------------------------------------------------------------

def implement_db_schema(context: dict) -> dict:
    """Represent DB schema implementation (models.py already built)."""
    print("\n    💾 DB schema: urls + clicks tables with indexes")
    return {"artifact_db_schema": "src/db/models.py"}


def implement_core_api(context: dict) -> dict:
    """Represent core API implementation."""
    print("\n    🔌 Core API: POST /shorten + GET /{code} implemented")
    return {"artifact_api": "src/api/routes.py"}


def implement_analytics(context: dict) -> dict:
    """Represent analytics pipeline implementation."""
    print("\n    📊 Analytics: async Redis queue + Postgres flush loop implemented")
    return {"artifact_analytics": "src/analytics/pipeline.py"}


def implement_rate_limiting(context: dict) -> dict:
    """Represent rate limiting implementation."""
    print("\n    🚦 Rate limiting: sliding window via Redis INCR implemented")
    return {"artifact_rate_limit": "src/cache/redis_client.py:rate_limit_check"}


def implement_health_checks(context: dict) -> dict:
    """Represent health check implementation."""
    print("\n    ❤️  Health checks: /health, /health/live, /health/ready implemented")
    return {"artifact_health": "src/api/routes.py:router_health"}


def implement_openapi(context: dict) -> dict:
    """Represent OpenAPI documentation generation."""
    print("\n    📄 OpenAPI: auto-generated by FastAPI at /docs and /openapi.json")
    return {"artifact_openapi": "docs/api/openapi.json"}


# ---------------------------------------------------------------------------
# Brownfield-specific tasks
# ---------------------------------------------------------------------------

def run_impact_analysis(context: dict) -> dict:
    """
    Brownfield T1 — Scan the existing codebase for impact of the change.
    In a real agentic system this would use AST analysis or an LLM.
    """
    print("\n    🔬 Impact analysis: scanning existing service")
    analysis = {
        "impacted_modules": [
            "src/db/models.py   → add country_code, city columns to clicks",
            "src/analytics/pipeline.py → add top_referrers, click_trend, top_countries",
            "src/api/routes.py  → add GET /analytics/{short_code} endpoint",
            "src/api/schemas.py → add AnalyticsResponse schema",
        ],
        "breaking_changes": [],
        "additive_changes": [
            "New nullable columns on clicks table",
            "New analytics endpoint (no existing endpoint modified)",
        ],
        "schema_diff": {
            "clicks": {
                "add_columns": ["country_code VARCHAR(2) NULL", "city VARCHAR(128) NULL"],
            }
        },
    }
    print(f"       Impacted: {len(analysis['impacted_modules'])} modules")
    print(f"       Breaking changes: {len(analysis['breaking_changes'])} (zero — safe to proceed)")
    return {"impact_analysis": analysis}


def generate_migration_plan(context: dict) -> dict:
    """Brownfield T2 — Produce a Postgres migration plan."""
    print("\n    📋 Migration plan generated")
    plan = {
        "migration_id":   "0002_add_geo_columns",
        "description":    "Add country_code and city to clicks table",
        "sql_up": (
            "ALTER TABLE clicks\n"
            "  ADD COLUMN IF NOT EXISTS country_code VARCHAR(2),\n"
            "  ADD COLUMN IF NOT EXISTS city VARCHAR(128);\n"
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_clicks_country\n"
            "  ON clicks (country_code);"
        ),
        "sql_down": (
            "DROP INDEX CONCURRENTLY IF EXISTS ix_clicks_country;\n"
            "ALTER TABLE clicks\n"
            "  DROP COLUMN IF EXISTS country_code,\n"
            "  DROP COLUMN IF EXISTS city;"
        ),
        "is_destructive": False,
        "estimated_lock_time_ms": 0,  # ADD COLUMN on Postgres 11+ is instant
        "notes": "CONCURRENTLY index creation avoids table lock",
    }
    return {
        "migration_plan":    plan,
        "rollback_script":   plan["sql_down"],  # satisfies guardrail check
    }


def apply_migration(context: dict) -> dict:
    """
    Brownfield T3 — Apply the DB migration.
    GATED: requires human approval (migration tag + human_gate).
    In CI this runs the actual SQL via psql; here we simulate it.
    """
    plan = context.get("migration_plan", {})
    print(f"\n    ✅ Migration applied: {plan.get('migration_id', 'unknown')}")
    print(f"       SQL: {plan.get('sql_up', '')[:60]}...")
    return {"migration_applied": True, "migration_id": plan.get("migration_id")}


def implement_geo_lookup(context: dict) -> dict:
    """Brownfield T4 — Implement IP-to-country geo-lookup utility."""
    print("\n    🌍 Geo-lookup utility implemented (MaxMind GeoLite2)")
    return {"artifact_geo": "src/utils/geo.py"}


def implement_analytics_queries(context: dict) -> dict:
    """Brownfield T5 — Implement aggregation query functions."""
    print("\n    📈 Analytics queries: top_referrers, click_trend, top_countries")
    return {"artifact_analytics_queries": "src/analytics/pipeline.py"}


def implement_analytics_endpoint(context: dict) -> dict:
    """Brownfield T6 — Implement GET /analytics/{short_code}."""
    print("\n    🔌 Analytics endpoint: GET /analytics/{short_code} implemented")
    return {"artifact_analytics_endpoint": "src/api/routes.py:router_analytics"}


def implement_analytics_cache(context: dict) -> dict:
    """Brownfield T7 — Add Redis caching to analytics aggregations."""
    print("\n    ⚡ Analytics cache: 5-min Redis TTL on aggregation results")
    return {"artifact_analytics_cache": "src/cache/redis_client.py"}


def update_tests(context: dict) -> dict:
    """Brownfield T8 — Update integration tests for new endpoint."""
    print("\n    🧪 Integration tests updated for /analytics/{short_code}")
    return {
        "artifact_updated_tests": "tests/integration/test_analytics.py",
        "test_artifacts": context.get("test_artifacts", []) + [
            "tests/integration/test_analytics.py"
        ],
    }


# ---------------------------------------------------------------------------
# Ambiguous-specific tasks
# ---------------------------------------------------------------------------

def interpret_ambiguous_requirement(context: dict) -> dict:
    """
    Ambiguous T1 — Decompose 'make it reliable' into measurable items.
    This task requires a HUMAN GATE before proceeding — the interpretation
    must be confirmed before any code is written.
    """
    print("\n    🤔 Ambiguous requirement decomposed into engineering tasks")
    print("       Presenting interpretation to human for approval...")
    return {
        "reliability_interpretation": {
            "high_priority": [
                "Connection pooling (asyncpg + SQLAlchemy pool_pre_ping)",
                "Redis AOF persistence (appendonly yes in redis.conf)",
                "Retry decorator with exponential backoff on all I/O calls",
                "Structured JSON logging with request IDs",
                "Enhanced /health endpoint with per-component status",
                "Docker restart: always policy",
            ],
            "medium_priority_deferred": [
                "Dead-letter queue for failed analytics writes",
                "Prometheus metrics endpoint",
            ],
            "out_of_scope": [
                "Multi-region failover",
                "Circuit breaker (Hystrix-style)",
                "Chaos engineering tests",
            ],
        }
    }


def implement_connection_pool(context: dict) -> dict:
    print("\n    🏊 Connection pool: pool_size=2, max_overflow=8, pool_pre_ping=True")
    return {"artifact_conn_pool": "src/db/engine.py:init_db"}


def implement_redis_persistence(context: dict) -> dict:
    print("\n    💾 Redis AOF: appendonly yes, appendfsync everysec")
    return {"artifact_redis_persist": "docker/redis.conf"}


def implement_retry_logic(context: dict) -> dict:
    print("\n    🔄 Retry decorator: max_attempts=3, exponential backoff with jitter")
    return {"artifact_retry": "src/utils/retry.py"}


def implement_structured_logging(context: dict) -> dict:
    print("\n    📝 Structured logging: JSON format with ts, level, logger, msg + extras")
    return {"artifact_logging": "src/utils/logger.py"}


def implement_enhanced_health(context: dict) -> dict:
    print("\n    ❤️  Enhanced health: /health/live (liveness) + /health/ready (readiness)")
    return {"artifact_health_enhanced": "src/api/routes.py:router_health"}


def implement_docker_restart(context: dict) -> dict:
    print("\n    🐳 Docker restart: restart: always on all services in docker-compose.yml")
    return {"artifact_docker_restart": "docker-compose.yml"}


def implement_dead_letter_queue(context: dict) -> dict:
    print("\n    📬 Dead-letter queue: failed analytics → Redis list 'analytics:dlq'")
    return {"artifact_dlq": "src/analytics/pipeline.py"}


# ---------------------------------------------------------------------------
# Shared final tasks
# ---------------------------------------------------------------------------

def generate_tests(context: dict) -> dict:
    """Produce test artifacts for this scenario."""
    scenario = context.get("scenario", "greenfield")
    artifacts = {
        "greenfield": ["tests/unit/test_core.py", "tests/integration/test_api.py"],
        "brownfield": ["tests/integration/test_analytics.py"],
        "ambiguous":  ["tests/unit/test_reliability.py"],
    }
    print(f"\n    🧪 Tests generated: {artifacts.get(scenario, [])}")
    return {
        "test_artifacts": artifacts.get(scenario, []),
        "artifacts": context.get("artifacts", []) + artifacts.get(scenario, []),
    }


def generate_documentation(context: dict) -> dict:
    """Generate documentation artifacts."""
    print("\n    📚 Documentation: README, OpenAPI spec, architecture decision record")
    scenario = context.get("scenario", "greenfield")
    # Include any test artifacts already in context so the release check sees them
    existing   = context.get("artifacts", [])
    test_arts  = context.get("test_artifacts", [])
    new_arts   = ["README.md", "docs/api/openapi.json", "docs/architecture/decisions.md"]
    all_arts   = list({*existing, *test_arts, *new_arts})  # deduplicate
    return {
        "doc_artifacts": new_arts,
        "artifacts":     all_arts,
    }


def validate_release_readiness(context: dict) -> dict:
    """
    Final validation gate — checks all required artifacts are present
    and metrics meet the bar before declaring release-ready.
    """
    required_artifacts = context.get("artifacts", [])
    risks              = context.get("risk_register", [])
    high_risks         = [r for r in risks if r.get("severity") == "HIGH"]

    checks = {
        "artifacts_present":  len(required_artifacts) > 0,
        "high_risks_mitigated": all("mitigation" in r for r in high_risks),
        "tests_exist":        any("test" in a for a in required_artifacts),
        "docs_exist":         any("README" in a or "docs" in a for a in required_artifacts),
    }
    all_pass = all(checks.values())

    print(f"\n    {'✅' if all_pass else '⚠️ '} Release readiness: "
          f"{'PASS' if all_pass else 'FAIL'}")
    for check, result in checks.items():
        print(f"       {'✓' if result else '✗'} {check}")

    return {
        "release_ready":   all_pass,
        "readiness_checks": checks,
    }
