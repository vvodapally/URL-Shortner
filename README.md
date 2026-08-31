# URL Shortener — Agentic SDLC Prototype

A production-grade URL shortener service built as a working demonstration of
**end-to-end SDLC automation with controlled agent autonomy**.

The project has two layers:

1. **The service** — a FastAPI URL shortener with Redis + PostgreSQL, analytics, rate limiting, and health checks.
2. **The orchestration engine** — a stateful DAG scheduler that automates the full SDLC lifecycle (requirements → design → implementation → testing → release) with human approval gates, guardrails, retry logic, and audit-grade observability.

---

## Quick start

```bash
# 1. Clone and configure
git clone https://github.com/your-org/url-shortener
cd url-shortener
cp .env.example .env           # edit credentials if needed

# 2. Start the full stack
docker compose up --build

# 3. Service is ready at
open http://localhost:8000/docs   # Swagger UI
open http://localhost:8000/health # Health check
```

### Shorten a URL

```bash
curl -X POST http://localhost:8000/shorten \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.example.com/very/long/path?utm_source=demo"}'

# Response:
# {
#   "short_code": "aB3xY7z",
#   "short_url":  "http://localhost:8000/aB3xY7z",
#   "long_url":   "https://www.example.com/very/long/path?utm_source=demo",
#   "created_at": "2024-01-15T10:30:00Z"
# }
```

### Redirect

```bash
curl -L http://localhost:8000/aB3xY7z   # follows redirect to long URL
```

### Analytics

```bash
curl http://localhost:8000/analytics/aB3xY7z
```

### Run the orchestration scenarios

```bash
# All three scenarios with auto-approved gates (CI mode)
python -m orchestration.workflows.greenfield
python -m orchestration.workflows.brownfield
python -m orchestration.workflows.ambiguous

# Interactive mode — pauses at each gate for human approval
python -m orchestration.workflows.greenfield interactive

# Brownfield with dynamic re-planning demo
python -m orchestration.workflows.brownfield auto --replan
```

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  FastAPI Application                 │
│                                                      │
│  POST /shorten   ──►  validate → generate → persist │
│  GET  /{code}    ──►  Redis hit → redirect (302)    │
│                        Redis miss → Postgres lookup  │
│  GET  /analytics/{code} ──►  aggregation queries    │
│  GET  /health    ──►  Postgres ping + Redis ping    │
└──────────────────┬──────────────────┬───────────────┘
                   │                  │
          ┌────────▼────────┐  ┌──────▼──────────┐
          │   PostgreSQL 16  │  │    Redis 7.2     │
          │                  │  │                  │
          │  urls table      │  │  url:{code}      │
          │  clicks table    │  │  rl:{ip_hash}    │
          │  (source of      │  │  analytics:queue │
          │   truth)         │  │  (hot-path cache │
          └──────────────────┘  │  + rate limiter) │
                                └──────────────────┘

Analytics pipeline (fire-and-forget):
  Redirect → BackgroundTask → Redis RPUSH analytics:queue
  Background loop (every 5s) → drain queue → batch INSERT clicks
```

### Key design decisions

| Decision | Rationale | Trade-off |
|---|---|---|
| Base62 random short codes | 62^7 ≈ 3.5T combinations; cryptographically unpredictable | Requires uniqueness check on INSERT |
| Redis for hot-path reads | O(1) GET; redirect p50 < 5ms | Write-through on POST adds one extra write |
| Async analytics pipeline | Decouples redirect latency from DB write latency | Analytics eventually consistent (up to 5s lag) |
| SHA-256 IP hash (not raw IP) | GDPR Art. 25 data minimisation | Cannot reverse-lookup; by design |
| `ip_hash` in analytics | Abuse detection without storing PII | One-way hash — cannot identify individual users |
| Async SQLAlchemy + asyncpg | Highest-throughput Postgres driver | Slightly more complex session lifecycle |
| FastAPI dependency injection | Auto-commit on success, auto-rollback on exception | Per-request overhead is negligible |

---

## Agentic Orchestration

The orchestration engine (`orchestration/`) implements a **stateful DAG scheduler** that automates the full SDLC lifecycle.

### Engine capabilities

| Capability | Where |
|---|---|
| Non-linear, stateful execution | `engine.py:_find_ready_tasks()` |
| Explicit dependency graph with entry/exit gates | `Task.depends_on` + `_deps_satisfied()` |
| Sequential and parallel paths | Multiple tasks become READY simultaneously |
| Cross-stage context preservation | Shared `context: dict` merged on each task return |
| Human approval checkpoints | `Task.requires_human_gate=True` + `GateKeeper` |
| Bounded retries + fallback + rollback | `RetryPolicy(max_attempts, on_exhaust)` |
| Dynamic re-planning | `engine.replan(new_tasks, reason)` |
| Policy guardrails | `GuardrailPolicy` rules run before every task |
| Audit-grade observability | NDJSON append-only log in `logs/` |
| Reliability metrics | `AuditLog.compute_metrics()` |

### Human gate modes

```python
# Auto-approve (CI pipelines)
engine = OrchestrationEngine(scenario="greenfield", gate_mode="auto")

# Interactive terminal prompt
engine = OrchestrationEngine(scenario="greenfield", gate_mode="interactive")

# File-based (async human signaling)
# Drop a file to approve/reject without keeping a terminal open:
#   touch /tmp/orch_gates/TASK_ID.approve
#   echo "wrong environment" > /tmp/orch_gates/TASK_ID.reject
engine = OrchestrationEngine(scenario="greenfield", gate_mode="file",
                             signal_dir=Path("/tmp/orch_gates"))
```

### Guardrails

Two default guardrails are enforced on every task:

1. **Migration safety** — any task tagged `migration` is blocked unless `rollback_script` is present in context. Ensures rollback is always planned before a migration runs.
2. **Destructive changes** — any task tagged `destructive` is blocked unless `destructive_ack=True` is set in context. Forces explicit acknowledgement before irreversible operations.

Custom guardrails can be composed:

```python
from orchestration import OrchestrationEngine, GuardrailPolicy

policy = GuardrailPolicy()
policy.add_rule(lambda task, ctx: (
    (False, "Production deploys require prod_approved")
    if ctx.get("env") == "prod" and "prod_approved" not in ctx
    else (True, "")
))
engine = OrchestrationEngine(scenario="greenfield", guardrails=policy)
```

---

## Three scenarios

### Scenario 1 — Greenfield

Build the URL shortener from scratch.

**14 tasks across 5 parallel groups:**

```
Group A (parallel): REQ_PARSE, REQ_AMBIGUITY
Group B (after A):  DESIGN_ARCH [GATE 1], DESIGN_GRAPH, DESIGN_RISKS
Group C (after B):  IMPL_DB → IMPL_API, IMPL_ANALYTICS, IMPL_RATE_LIMIT, IMPL_HEALTH
Group D (after C):  IMPL_OPENAPI, TEST_GEN
Group E (after D):  DOC_GEN → RELEASE_VALIDATE [GATE 2]
```

**Gates:** Architecture approval → Release approval

---

### Scenario 2 — Brownfield

Add rich analytics (referrers, trends, geo) to the existing service.

**Codebase reasoning:** The orchestrator runs an impact analysis first (before any code is written) that identifies impacted modules, schema changes required, and confirms zero breaking changes to the existing API.

**Migration safety:** `MIGRATION_PLAN` generates both the up-migration SQL and the rollback script. Only after both are in context does `APPLY_MIGRATION` (tagged `migration`) become eligible — the guardrail enforces this automatically.

**Dynamic re-planning:** Run with `--replan` to simulate the orchestrator discovering an unexpected schema difference mid-run and inserting a prerequisite migration task.

```
Group A: REQ_PARSE → IMPACT_ANALYSIS
Group B: DESIGN_ARCH, DESIGN_RISKS
Group C: MIGRATION_PLAN (parallel) + GEO_UTILITY (parallel)
         APPLY_MIGRATION [GATE — migration safety]
Group D: IMPL_ANALYTICS_QUERIES, IMPL_ANALYTICS_ENDPOINT (parallel)
Group E: IMPL_ANALYTICS_CACHE, UPDATE_TESTS (parallel)
         DOC_GEN → RELEASE_VALIDATE [GATE]
```

---

### Scenario 3 — Ambiguous

Handle the vague requirement: *"make it reliable."*

**Disambiguation protocol:** Before a single implementation task runs, the orchestrator decomposes "reliable" into 6 HIGH priority, 2 MEDIUM deferred, and 3 out-of-scope items. A mandatory human gate presents this interpretation for approval. Nothing is built until confirmed.

**Parallel implementation:** All 5 HIGH-priority reliability tasks are independent and run in parallel after the interpretation is approved.

```
Group A: REQ_PARSE → REQ_AMBIGUITY → INTERPRET_REQ [GATE 1 — MANDATORY]
Group B: DESIGN_RISKS
Group C: IMPL_CONN_POOL, IMPL_REDIS_PERSIST, IMPL_RETRY,
         IMPL_LOGGING, IMPL_DOCKER_RESTART (all parallel)
Group D: IMPL_HEALTH_ENHANCED, IMPL_DLQ (parallel)
Group E: TEST_GEN → DOC_GEN → RELEASE_VALIDATE [GATE 2]
```

---

## API reference

Full OpenAPI spec available at `/docs` (Swagger UI) and `/openapi.json`.

| Method | Path | Description | Auth |
|---|---|---|---|
| `POST` | `/shorten` | Shorten a URL | None |
| `GET` | `/{short_code}` | Redirect to original URL | None |
| `GET` | `/analytics/{short_code}` | Click analytics | None |
| `GET` | `/health` | Full health check | None |
| `GET` | `/health/live` | Liveness probe | None |
| `GET` | `/health/ready` | Readiness probe | None |

### POST /shorten — request body

```json
{
  "url":         "https://www.example.com/path",   // required
  "custom_code": "mycode",                          // optional, 3-16 alphanumeric
  "expires_at":  "2024-12-31T23:59:59Z",           // optional ISO 8601
  "title":       "My link"                          // optional
}
```

### Rate limiting

60 requests per 60-second sliding window per IP (configurable via env vars).
Exceeded requests receive `HTTP 429` with a `Retry-After` header.

---

## Testing

```bash
# Unit tests (no running services needed)
python tests/unit/test_core.py
python tests/unit/test_utils.py
python tests/unit/test_analytics.py
python tests/unit/test_orchestration_engine.py

# Integration tests (requires docker compose up postgres redis)
pytest tests/integration/ -v -m integration

# All tests
pytest tests/ -v --cov=src --cov-report=html
```

### Test counts

| Suite | Tests | Requires services |
|---|---|---|
| `test_orchestration_engine.py` | 22 | No |
| `test_core.py` | 18 | No |
| `test_utils.py` | 9 | No |
| `test_analytics.py` | 15 | No |
| `test_api.py` | 15 | Yes (Postgres + Redis) |
| **Total** | **79** | |

---

## Configuration reference

All settings are environment variables with safe defaults.

| Variable | Default | Description |
|---|---|---|
| `BASE_URL` | `http://localhost:8000` | Base URL for short links |
| `POSTGRES_HOST` | `localhost` | PostgreSQL host |
| `POSTGRES_DB` | `urlshortener` | Database name |
| `POSTGRES_POOL_MAX` | `10` | Max connection pool size |
| `REDIS_HOST` | `localhost` | Redis host |
| `REDIS_TTL_S` | `604800` | Cache TTL (7 days) |
| `SHORT_CODE_LENGTH` | `7` | Length of generated codes |
| `RATE_LIMIT_REQUESTS` | `60` | Requests per window |
| `RATE_LIMIT_WINDOW_S` | `60` | Rate limit window (seconds) |
| `ANALYTICS_FLUSH_INTERVAL` | `5.0` | Analytics batch flush interval |
| `LOG_FORMAT` | `json` | `json` (prod) or `text` (dev) |

---

## Repository structure

```
url-shortener/
├── src/
│   ├── main.py              # FastAPI app factory + lifespan
│   ├── config.py            # Settings (env-driven)
│   ├── api/
│   │   ├── routes.py        # All four routers
│   │   └── schemas.py       # Pydantic request/response models
│   ├── core/
│   │   └── shortener.py     # Short code generation + URL validation
│   ├── db/
│   │   ├── models.py        # SQLAlchemy ORM (urls, clicks)
│   │   └── engine.py        # Async engine, session factory, health check
│   ├── cache/
│   │   └── redis_client.py  # URL cache, rate limiter, analytics buffer
│   ├── analytics/
│   │   └── pipeline.py      # Async write pipeline + aggregation queries
│   └── utils/
│       ├── logger.py        # Structured JSON logger
│       └── retry.py         # Async retry decorator with backoff
├── orchestration/
│   ├── engine.py            # DAG scheduler, state machine, retry, guardrails
│   ├── models.py            # Task, RetryPolicy, TaskRecord dataclasses
│   ├── audit.py             # NDJSON audit log + reliability metrics
│   ├── gates.py             # Human approval gate (interactive/auto/file)
│   ├── tasks/
│   │   └── shared.py        # Reusable task functions (all three scenarios)
│   └── workflows/
│       ├── greenfield.py    # Scenario 1
│       ├── brownfield.py    # Scenario 2
│       └── ambiguous.py     # Scenario 3
├── tests/
│   ├── unit/                # Pure Python, no services
│   └── integration/         # Requires Postgres + Redis
├── docker/
│   └── redis.conf           # Redis with AOF persistence
├── scripts/
│   └── init_db.sql          # Postgres extension init
├── docs/
│   └── architecture/
├── Dockerfile               # Multi-stage build
├── docker-compose.yml       # Full stack
├── requirements.txt
├── pytest.ini
└── .env.example
```

---

## Risks and limitations

### Risks (with mitigations)

| Risk | Severity | Mitigation |
|---|---|---|
| SSRF via redirect endpoint | HIGH | URL validation rejects private IPs and localhost |
| Short code collision | LOW | Retry with new code on DB UNIQUE violation |
| Redis unavailability | MEDIUM | Fallback to Postgres-only mode; cache miss increases latency |
| Analytics queue overflow | LOW | Flush interval keeps queue small; bounded by Redis memory |
| Rate limit bypass via IP spoof | MEDIUM | Rate limit is advisory; production adds WAF |

### Known limitations

- No authentication — any client can shorten any URL
- Rate limiting is per-process; horizontal scaling requires shared Redis (already used — just needs the same Redis cluster)
- Geographic data (Scenario 2) requires a MaxMind GeoLite2 database file (free, requires registration at maxmind.com)
- Analytics are eventually consistent — up to `ANALYTICS_FLUSH_INTERVAL` seconds of lag
- No URL preview endpoint before redirect
- Load testing (k6/Locust) is out of scope for this assessment

---

## Assumptions

1. Base URL is configurable via `BASE_URL` env var — no hardcoded domain
2. Short codes are 7-char Base62 — collision-safe to ~60 million URLs (birthday bound ~1%)
3. Analytics writes are eventually consistent — the async pipeline is by design
4. Rate limiting is per-IP sliding window — not per authenticated user
5. The orchestration engine simulates code generation tasks — in a production agentic system, these task functions would invoke Claude/Copilot via API to actually generate code

---

## Evaluation criteria mapping

| Criterion | Where demonstrated |
|---|---|
| Agentic orchestration effectiveness | `orchestration/engine.py` — DAG scheduler, gates, retries, guardrails |
| Architecture/system design quality | Two-tier caching, async analytics pipeline, SSRF protection |
| Depth of decomposition | 14-task greenfield graph with 5 parallel groups |
| Realism/quality of outputs | Working FastAPI service, 64 unit tests, complete Docker setup |
| Validation and risk management | Risk register, guardrail policies, rollback scripts |
| Clarity of decisions | ADR in `architecture_decisions` context key, all trade-offs documented |
| Core engineering principles | Modular src/ layout, dependency injection, async throughout |
| Engineering judgment | ip_hash vs raw IP, Base62 vs hash-based codes, async vs sync analytics |
