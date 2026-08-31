# syntax=docker/dockerfile:1
# ── URL Shortener — FastAPI service ──────────────────────────────────────
#
# Multi-stage build:
#   Stage 1 (builder) — install dependencies into a venv
#   Stage 2 (runtime) — copy only the venv + source; no build tools
#
# This keeps the final image lean (~200 MB vs ~600 MB for a single stage)
# and ensures build tools (gcc, pip) are not present in production.

# ── Stage 1: Builder ─────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies (layer-cached separately from source code)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt


# ── Stage 2: Runtime ──────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Runtime system deps only (libpq for asyncpg)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for security (CIS Docker Benchmark 4.1)
RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

# Copy venv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application source
COPY src/ ./src/
COPY orchestration/ ./orchestration/

# Set ownership
RUN chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 8000

# Liveness probe used by Docker and Kubernetes
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health/live || exit 1

# Entry point — uvicorn with:
#   --workers 1        single worker (scaling via replicas, not threads)
#   --loop uvloop      fastest async loop implementation
#   --access-log       structured access logging
CMD ["uvicorn", "src.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--loop", "uvloop", \
     "--access-log"]
