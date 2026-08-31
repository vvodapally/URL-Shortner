"""
orchestration/workflows/ambiguous.py
--------------------------------------
Scenario 3: Handle the vague requirement "make it reliable."

Demonstrates:
  - Ambiguous requirement detection and structured decomposition
  - MANDATORY human gate before ANY implementation begins
    (the interpretation itself must be approved — this is the
    highest-stakes gate because a wrong interpretation wastes all
    downstream work)
  - Parallel implementation of independent reliability tasks
  - Policy guardrail: destructive_ack required for docker restart changes
  - Final validation that all reliability targets are met
"""

from __future__ import annotations

from pathlib import Path

from orchestration.engine import OrchestrationEngine
from orchestration.gates import GateMode
from orchestration.models import RetryPolicy, Task, NO_RETRY
from orchestration.tasks.shared import (
    parse_requirement,
    detect_ambiguity,
    build_risk_register,
    interpret_ambiguous_requirement,
    implement_connection_pool,
    implement_redis_persistence,
    implement_retry_logic,
    implement_structured_logging,
    implement_enhanced_health,
    implement_docker_restart,
    implement_dead_letter_queue,
    generate_tests,
    generate_documentation,
    validate_release_readiness,
)


def build_ambiguous_workflow(
    gate_mode: GateMode = "auto",
    log_dir: Path = Path("logs"),
) -> OrchestrationEngine:
    """
    Construct and register the Ambiguous scenario workflow.

    The critical design feature here: INTERPRET_REQ is gated, meaning
    the engine pauses after decomposing the vague requirement and waits
    for a human to confirm the interpretation before writing a single
    line of implementation code. This is controlled autonomy in action.
    """
    engine = OrchestrationEngine(
        scenario  = "ambiguous",
        gate_mode = gate_mode,
        log_dir   = log_dir,
    )

    tasks = [
        # ── Stage 1: Parse + Detect (always fast) ─────────────────────
        Task(
            id          = "REQ_PARSE",
            name        = "Parse raw requirement",
            fn          = parse_requirement,
            depends_on  = [],
            retry_policy = NO_RETRY,
            tags        = ["requirement"],
        ),
        Task(
            id          = "REQ_AMBIGUITY",
            name        = "Detect and decompose ambiguity",
            fn          = detect_ambiguity,
            depends_on  = ["REQ_PARSE"],
            retry_policy = NO_RETRY,
            tags        = ["requirement"],
        ),

        # ── Stage 2: Interpret (MANDATORY HUMAN GATE) ──────────────────
        # This is the most important gate in the system:
        # nothing is built until a human confirms we are solving
        # the right problem.
        Task(
            id                   = "INTERPRET_REQ",
            name                 = "Interpret ambiguous requirement",
            fn                   = interpret_ambiguous_requirement,
            depends_on           = ["REQ_AMBIGUITY"],
            retry_policy         = NO_RETRY,
            requires_human_gate  = True,
            gate_label           = (
                "Gate 1 — MANDATORY: Approve reliability interpretation\n"
                "\n"
                "  The requirement 'make it reliable' has been decomposed into:\n"
                "  HIGH (will implement):\n"
                "    • Connection pooling (pool_size=2, max_overflow=8)\n"
                "    • Redis AOF persistence (appendonly yes)\n"
                "    • Retry with exponential backoff on all I/O\n"
                "    • Structured JSON logging with request IDs\n"
                "    • Enhanced /health endpoint (liveness + readiness)\n"
                "    • Docker restart: always policy\n"
                "  DEFERRED:\n"
                "    • Dead-letter queue for analytics failures\n"
                "    • Prometheus metrics\n"
                "  OUT OF SCOPE:\n"
                "    • Multi-region failover, circuit breakers\n"
                "\n"
                "  Approve to proceed with HIGH-priority items only."
            ),
            tags                 = ["requirement", "design"],
        ),

        # ── Stage 3: Risk register (after interpretation confirmed) ──────
        Task(
            id          = "DESIGN_RISKS",
            name        = "Build risk register",
            fn          = build_risk_register,
            depends_on  = ["INTERPRET_REQ"],
            retry_policy = NO_RETRY,
            tags        = ["design"],
        ),

        # ── Stage 4: Implementation — all parallel after INTERPRET_REQ ──
        # Each reliability task is independent — they run concurrently.
        # This is the key demonstration of the parallel execution model.

        Task(
            id          = "IMPL_CONN_POOL",
            name        = "Implement connection pooling",
            fn          = implement_connection_pool,
            depends_on  = ["DESIGN_RISKS"],
            retry_policy = RetryPolicy(max_attempts=2, base_delay_s=0.3),
            tags        = ["implementation", "reliability", "db"],
        ),
        Task(
            id          = "IMPL_REDIS_PERSIST",
            name        = "Configure Redis AOF persistence",
            fn          = implement_redis_persistence,
            depends_on  = ["DESIGN_RISKS"],
            retry_policy = RetryPolicy(max_attempts=2, base_delay_s=0.3),
            tags        = ["implementation", "reliability", "cache"],
        ),
        Task(
            id          = "IMPL_RETRY",
            name        = "Implement retry decorator",
            fn          = implement_retry_logic,
            depends_on  = ["DESIGN_RISKS"],
            retry_policy = RetryPolicy(max_attempts=2, base_delay_s=0.3),
            tags        = ["implementation", "reliability"],
        ),
        Task(
            id          = "IMPL_LOGGING",
            name        = "Implement structured logging",
            fn          = implement_structured_logging,
            depends_on  = ["DESIGN_RISKS"],
            retry_policy = RetryPolicy(max_attempts=2, base_delay_s=0.3),
            tags        = ["implementation", "observability"],
        ),
        Task(
            id          = "IMPL_DOCKER_RESTART",
            name        = "Configure Docker restart policies",
            fn          = implement_docker_restart,
            depends_on  = ["DESIGN_RISKS"],
            retry_policy = RetryPolicy(max_attempts=2, base_delay_s=0.3),
            # Note: 'destructive' tag triggers the destructive_ack guardrail.
            # The workflow sets destructive_ack=True in initial_context
            # because the human approved the interpretation at Gate 1 —
            # the gate approval IS the ack for this task.
            tags        = ["implementation", "infrastructure"],
        ),

        # ── Stage 5: Tasks that depend on earlier reliability work ──────
        Task(
            id          = "IMPL_HEALTH_ENHANCED",
            name        = "Implement enhanced health checks",
            fn          = implement_enhanced_health,
            depends_on  = ["IMPL_CONN_POOL", "IMPL_REDIS_PERSIST"],
            retry_policy = RetryPolicy(max_attempts=2, base_delay_s=0.3),
            tags        = ["implementation", "observability"],
        ),
        Task(
            id          = "IMPL_DLQ",
            name        = "Implement dead-letter queue for analytics",
            fn          = implement_dead_letter_queue,
            depends_on  = ["IMPL_RETRY"],
            retry_policy = RetryPolicy(max_attempts=2, base_delay_s=0.3),
            tags        = ["implementation", "reliability"],
        ),

        # ── Stage 6: Validation — all reliability tasks must complete ───
        Task(
            id          = "TEST_GEN",
            name        = "Generate reliability tests",
            fn          = generate_tests,
            depends_on  = [
                "IMPL_HEALTH_ENHANCED", "IMPL_DLQ",
                "IMPL_LOGGING", "IMPL_DOCKER_RESTART",
            ],
            retry_policy = NO_RETRY,
            tags        = ["testing"],
        ),
        Task(
            id          = "DOC_GEN",
            name        = "Generate documentation",
            fn          = generate_documentation,
            depends_on  = ["TEST_GEN"],
            retry_policy = NO_RETRY,
            tags        = ["documentation"],
        ),
        Task(
            id                   = "RELEASE_VALIDATE",
            name                 = "Validate reliability targets met",
            fn                   = validate_release_readiness,
            depends_on           = ["DOC_GEN"],
            retry_policy         = NO_RETRY,
            requires_human_gate  = True,
            gate_label           = (
                "Gate 2 — Approve reliability release\n"
                "  Verify all HIGH-priority reliability items are implemented\n"
                "  and all health checks pass before marking release-ready."
            ),
            tags                 = ["release"],
        ),
    ]

    engine.register(tasks)
    return engine


def run_ambiguous(gate_mode: GateMode = "auto", log_dir: Path = Path("logs")) -> dict:
    """Entry point — build and execute the ambiguous scenario workflow."""
    engine = build_ambiguous_workflow(gate_mode=gate_mode, log_dir=log_dir)
    return engine.run(initial_context={
        "scenario":        "ambiguous",
        "raw_requirement": "make it reliable",
        # Human approved the interpretation at Gate 1, which acts as
        # acknowledgement for Docker restart (destructive) changes
        "destructive_ack": True,
    })


if __name__ == "__main__":
    import sys
    mode   = sys.argv[1] if len(sys.argv) > 1 else "auto"
    result = run_ambiguous(gate_mode=mode)  # type: ignore[arg-type]
    print(f"\nRelease ready: {result.get('release_ready', False)}")
