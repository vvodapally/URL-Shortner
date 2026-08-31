"""
orchestration/workflows/greenfield.py
--------------------------------------
Scenario 1: Build the URL shortener from scratch.

Demonstrates:
  - Full SDLC lifecycle from requirement → release
  - Parallel task execution (Groups A–E)
  - Human gate at design approval (before implementation begins)
  - SSRF guardrail (URL validation) documented as a policy decision
  - Release readiness validation checkpoint
"""

from __future__ import annotations

from pathlib import Path

from orchestration.engine import OrchestrationEngine
from orchestration.gates import GateMode
from orchestration.models import RetryPolicy, Task, NO_RETRY
from orchestration.tasks.shared import (
    parse_requirement,
    detect_ambiguity,
    design_architecture,
    build_task_graph,
    build_risk_register,
    implement_db_schema,
    implement_core_api,
    implement_analytics,
    implement_rate_limiting,
    implement_health_checks,
    implement_openapi,
    generate_tests,
    generate_documentation,
    validate_release_readiness,
)


def build_greenfield_workflow(
    gate_mode: GateMode = "auto",
    log_dir: Path = Path("logs"),
) -> OrchestrationEngine:
    """
    Construct and register the Greenfield scenario workflow.

    Returns a configured engine ready to call .run() on.
    """
    engine = OrchestrationEngine(
        scenario  = "greenfield",
        gate_mode = gate_mode,
        log_dir   = log_dir,
    )

    tasks = [
        # ── Stage 1: Requirement Understanding ────────────────────────
        Task(
            id          = "REQ_PARSE",
            name        = "Parse requirement",
            fn          = parse_requirement,
            depends_on  = [],
            retry_policy = NO_RETRY,
            tags        = ["requirement"],
        ),
        Task(
            id          = "REQ_AMBIGUITY",
            name        = "Detect ambiguity",
            fn          = detect_ambiguity,
            depends_on  = ["REQ_PARSE"],
            retry_policy = NO_RETRY,
            tags        = ["requirement"],
        ),

        # ── Stage 2: Design (HUMAN GATE before implementation) ─────────
        Task(
            id                   = "DESIGN_ARCH",
            name                 = "Architecture design",
            fn                   = design_architecture,
            depends_on           = ["REQ_AMBIGUITY"],
            retry_policy         = NO_RETRY,
            requires_human_gate  = True,
            gate_label           = (
                "Gate 1 — Approve architecture design\n"
                "  Review: tech stack, data model, API contract, "
                "trade-offs, and risk register."
            ),
            tags                 = ["design"],
        ),
        Task(
            id          = "DESIGN_GRAPH",
            name        = "Build task dependency graph",
            fn          = build_task_graph,
            depends_on  = ["DESIGN_ARCH"],
            retry_policy = NO_RETRY,
            tags        = ["design"],
        ),
        Task(
            id          = "DESIGN_RISKS",
            name        = "Build risk register",
            fn          = build_risk_register,
            depends_on  = ["DESIGN_ARCH"],
            retry_policy = NO_RETRY,
            tags        = ["design"],
        ),

        # ── Stage 3: Implementation (parallel group B) ─────────────────
        Task(
            id          = "IMPL_DB",
            name        = "Implement DB schema",
            fn          = implement_db_schema,
            depends_on  = ["DESIGN_GRAPH", "DESIGN_RISKS"],
            retry_policy = RetryPolicy(max_attempts=2, base_delay_s=0.5),
            tags        = ["implementation", "db"],
        ),
        Task(
            id          = "IMPL_API",
            name        = "Implement core API",
            fn          = implement_core_api,
            depends_on  = ["IMPL_DB"],
            retry_policy = RetryPolicy(max_attempts=2, base_delay_s=0.5),
            tags        = ["implementation", "api"],
        ),
        Task(
            id          = "IMPL_ANALYTICS",
            name        = "Implement analytics pipeline",
            fn          = implement_analytics,
            depends_on  = ["IMPL_DB"],
            retry_policy = RetryPolicy(max_attempts=2, base_delay_s=0.5),
            tags        = ["implementation", "analytics"],
        ),
        Task(
            id          = "IMPL_RATE_LIMIT",
            name        = "Implement rate limiting",
            fn          = implement_rate_limiting,
            depends_on  = ["IMPL_DB"],
            retry_policy = RetryPolicy(max_attempts=2, base_delay_s=0.5),
            tags        = ["implementation", "reliability"],
        ),
        Task(
            id          = "IMPL_HEALTH",
            name        = "Implement health checks",
            fn          = implement_health_checks,
            depends_on  = ["IMPL_DB"],
            retry_policy = RetryPolicy(max_attempts=2, base_delay_s=0.5),
            tags        = ["implementation", "reliability"],
        ),

        # ── Stage 3: Documentation (parallel with some impl tasks) ──────
        Task(
            id          = "IMPL_OPENAPI",
            name        = "Generate OpenAPI docs",
            fn          = implement_openapi,
            depends_on  = ["IMPL_API"],
            retry_policy = NO_RETRY,
            tags        = ["documentation"],
        ),

        # ── Stage 4: Testing ───────────────────────────────────────────
        Task(
            id          = "TEST_GEN",
            name        = "Generate tests",
            fn          = generate_tests,
            depends_on  = ["IMPL_API", "IMPL_ANALYTICS", "IMPL_RATE_LIMIT", "IMPL_HEALTH"],
            retry_policy = NO_RETRY,
            tags        = ["testing"],
        ),

        # ── Stage 5: Documentation + Release (HUMAN GATE) ──────────────
        Task(
            id          = "DOC_GEN",
            name        = "Generate documentation",
            fn          = generate_documentation,
            depends_on  = ["TEST_GEN", "IMPL_OPENAPI"],
            retry_policy = NO_RETRY,
            tags        = ["documentation"],
        ),
        Task(
            id                   = "RELEASE_VALIDATE",
            name                 = "Validate release readiness",
            fn                   = validate_release_readiness,
            depends_on           = ["DOC_GEN"],
            retry_policy         = NO_RETRY,
            requires_human_gate  = True,
            gate_label           = (
                "Gate 2 — Approve release\n"
                "  Review: all artifacts present, high-risk items mitigated, "
                "tests passing, docs complete."
            ),
            tags                 = ["release"],
        ),
    ]

    engine.register(tasks)
    return engine


def run_greenfield(gate_mode: GateMode = "auto", log_dir: Path = Path("logs")) -> dict:
    """Entry point — build and execute the greenfield workflow."""
    engine = build_greenfield_workflow(gate_mode=gate_mode, log_dir=log_dir)
    return engine.run(initial_context={
        "scenario":        "greenfield",
        "raw_requirement": (
            "Build a production-grade URL shortener service from scratch "
            "with core APIs, analytics, and reliability features."
        ),
    })


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "auto"
    result = run_greenfield(gate_mode=mode)  # type: ignore[arg-type]
    print(f"\nRelease ready: {result.get('release_ready', False)}")
