"""
tests/unit/test_orchestration_engine.py
----------------------------------------
Unit tests for the orchestration engine, retry logic, gate keeper,
audit log, and guardrail policy.

Run with:  pytest tests/unit/test_orchestration_engine.py -v
"""

import json
import time
from pathlib import Path


from orchestration import (
    OrchestrationEngine,
    GuardrailPolicy,
    Task,
    RetryPolicy,
    NO_RETRY,
    TaskStatus,
    GateDecision,
)
from orchestration.models import TaskRecord


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _noop(context: dict) -> dict:
    """Task fn that does nothing and returns empty result."""
    return {}


def _context_writer(key: str, value):
    """Task fn factory — writes a value into context."""
    def fn(context: dict) -> dict:
        return {key: value}
    return fn


def _failing_fn(context: dict) -> dict:
    raise RuntimeError("Intentional failure")


def _flaky_fn(fail_count: int):
    """Succeeds on attempt `fail_count + 1`."""
    state = {"calls": 0}
    def fn(context: dict) -> dict:
        state["calls"] += 1
        if state["calls"] <= fail_count:
            raise RuntimeError(f"Flaky failure #{state['calls']}")
        return {"flaky_succeeded": True}
    return fn


def make_engine(tmp_path: Path, gate_mode="auto") -> OrchestrationEngine:
    return OrchestrationEngine(
        scenario="test",
        gate_mode=gate_mode,
        log_dir=tmp_path,
    )


# ---------------------------------------------------------------------------
# DAG validation
# ---------------------------------------------------------------------------

class TestDAGValidation:

    def test_detects_missing_dependency(self, tmp_path):
        engine = make_engine(tmp_path)
        with pytest.raises(ValueError, match="not registered"):
            engine.register([
                Task(id="T1", name="Task 1", fn=_noop, depends_on=["T_MISSING"])
            ])

    def test_detects_cycle(self, tmp_path):
        engine = make_engine(tmp_path)
        with pytest.raises(ValueError, match="Cycle"):
            engine.register([
                Task(id="T1", name="Task 1", fn=_noop, depends_on=["T2"]),
                Task(id="T2", name="Task 2", fn=_noop, depends_on=["T1"]),
            ])

    def test_linear_dag_is_valid(self, tmp_path):
        engine = make_engine(tmp_path)
        engine.register([
            Task(id="T1", name="Task 1", fn=_noop),
            Task(id="T2", name="Task 2", fn=_noop, depends_on=["T1"]),
            Task(id="T3", name="Task 3", fn=_noop, depends_on=["T2"]),
        ])
        # No exception raised

    def test_diamond_dag_is_valid(self, tmp_path):
        engine = make_engine(tmp_path)
        engine.register([
            Task(id="A",  name="A",  fn=_noop),
            Task(id="B1", name="B1", fn=_noop, depends_on=["A"]),
            Task(id="B2", name="B2", fn=_noop, depends_on=["A"]),
            Task(id="C",  name="C",  fn=_noop, depends_on=["B1", "B2"]),
        ])


# ---------------------------------------------------------------------------
# Execution order and context propagation
# ---------------------------------------------------------------------------

class TestExecution:

    def test_single_task_runs(self, tmp_path):
        engine = make_engine(tmp_path)
        engine.register([Task(id="T1", name="Task 1", fn=_context_writer("done", True))])
        ctx = engine.run()
        assert ctx["done"] is True

    def test_linear_chain_executes_in_order(self, tmp_path):
        order = []
        def make_fn(label):
            def fn(context):
                order.append(label)
                return {}
            return fn

        engine = make_engine(tmp_path)
        engine.register([
            Task(id="T1", name="T1", fn=make_fn("T1")),
            Task(id="T2", name="T2", fn=make_fn("T2"), depends_on=["T1"]),
            Task(id="T3", name="T3", fn=make_fn("T3"), depends_on=["T2"]),
        ])
        engine.run()
        assert order == ["T1", "T2", "T3"]

    def test_context_propagates_between_tasks(self, tmp_path):
        def writer(ctx):
            return {"x": 42}
        def reader(ctx):
            assert ctx["x"] == 42
            return {"y": ctx["x"] * 2}

        engine = make_engine(tmp_path)
        engine.register([
            Task(id="T1", name="Writer", fn=writer),
            Task(id="T2", name="Reader", fn=reader, depends_on=["T1"]),
        ])
        ctx = engine.run()
        assert ctx["y"] == 84

    def test_parallel_tasks_both_run(self, tmp_path):
        ran = []
        def a(ctx): ran.append("A"); return {}
        def b(ctx): ran.append("B"); return {}

        engine = make_engine(tmp_path)
        engine.register([
            Task(id="ROOT", name="Root", fn=_noop),
            Task(id="A", name="A", fn=a, depends_on=["ROOT"]),
            Task(id="B", name="B", fn=b, depends_on=["ROOT"]),
        ])
        engine.run()
        assert set(ran) == {"A", "B"}


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------

class TestRetryPolicy:

    def test_retry_policy_delay_calculation(self):
        policy = RetryPolicy(max_attempts=3, base_delay_s=1.0, backoff_factor=2.0)
        assert policy.delay_for_attempt(1) == 0.0   # first attempt — no delay
        assert policy.delay_for_attempt(2) == 1.0   # first retry
        assert policy.delay_for_attempt(3) == 2.0   # second retry

    def test_flaky_task_succeeds_on_retry(self, tmp_path):
        engine = make_engine(tmp_path)
        policy = RetryPolicy(max_attempts=3, base_delay_s=0.0)
        engine.register([
            Task(id="T1", name="Flaky", fn=_flaky_fn(fail_count=1),
                 retry_policy=policy)
        ])
        ctx = engine.run()
        assert ctx.get("flaky_succeeded") is True

    def test_safe_stop_on_exhaustion(self, tmp_path):
        engine = make_engine(tmp_path)
        policy = RetryPolicy(max_attempts=2, base_delay_s=0.0, on_exhaust="safe_stop")
        engine.register([
            Task(id="T1", name="Always fails", fn=_failing_fn, retry_policy=policy),
            Task(id="T2", name="Downstream",  fn=_noop, depends_on=["T1"]),
        ])
        engine.run()
        assert engine._aborted is True
        assert engine._records["T1"].status == TaskStatus.FAILED

    def test_skip_on_exhaustion_cascades(self, tmp_path):
        engine = make_engine(tmp_path)
        policy = RetryPolicy(max_attempts=1, base_delay_s=0.0, on_exhaust="skip")
        engine.register([
            Task(id="T1", name="Fails",      fn=_failing_fn, retry_policy=policy),
            Task(id="T2", name="Downstream", fn=_noop, depends_on=["T1"]),
        ])
        engine.run()
        assert engine._records["T1"].status == TaskStatus.SKIPPED
        assert engine._records["T2"].status == TaskStatus.SKIPPED
        assert engine._aborted is False  # workflow continues

    def test_no_retry_policy(self, tmp_path):
        engine = make_engine(tmp_path)
        engine.register([
            Task(id="T1", name="One-shot", fn=_failing_fn, retry_policy=NO_RETRY)
        ])
        engine.run()
        assert engine._records["T1"].attempts == 1


# ---------------------------------------------------------------------------
# Human gates
# ---------------------------------------------------------------------------

class TestHumanGates:

    def test_auto_gate_approves(self, tmp_path):
        ran = []
        def gated_fn(ctx):
            ran.append("gated")
            return {}

        engine = make_engine(tmp_path, gate_mode="auto")
        engine.register([
            Task(id="T1", name="Gated task", fn=gated_fn,
                 requires_human_gate=True, gate_label="Approve deployment"),
        ])
        engine.run()
        assert "gated" in ran

    def test_file_gate_approve(self, tmp_path):
        signal_dir = tmp_path / "gates"
        signal_dir.mkdir()

        ran = []
        def gated_fn(ctx):
            ran.append("gated")
            return {}

        engine = OrchestrationEngine(
            scenario="test_gate",
            gate_mode="file",
            log_dir=tmp_path,
            signal_dir=signal_dir,
        )
        # Pre-create the approve signal file
        (signal_dir / "T1.approve").write_text("")

        engine.register([
            Task(id="T1", name="Gated", fn=gated_fn,
                 requires_human_gate=True, gate_label="Test gate"),
        ])
        engine.run()
        assert "gated" in ran

    def test_file_gate_reject_aborts_workflow(self, tmp_path):
        signal_dir = tmp_path / "gates"
        signal_dir.mkdir()
        (signal_dir / "T1.reject").write_text("Wrong environment")

        engine = OrchestrationEngine(
            scenario="test_reject",
            gate_mode="file",
            log_dir=tmp_path,
            signal_dir=signal_dir,
        )
        engine.register([
            Task(id="T1", name="Gated", fn=_noop,
                 requires_human_gate=True, gate_label="Test gate"),
        ])
        engine.run()
        assert engine._aborted is True


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------

class TestGuardrails:

    def test_migration_tag_blocked_without_rollback(self, tmp_path):
        engine = make_engine(tmp_path)
        engine.register([
            Task(id="M1", name="Migration", fn=_noop, tags=["migration"]),
        ])
        engine.run()
        # Guardrail blocks it — task should be FAILED, workflow aborted
        assert engine._records["M1"].status == TaskStatus.FAILED
        assert engine._aborted is True

    def test_migration_tag_allowed_with_rollback(self, tmp_path):
        engine = make_engine(tmp_path)
        engine.register([
            Task(id="M1", name="Migration", fn=_noop, tags=["migration"]),
        ])
        engine.run(initial_context={"rollback_script": "-- rollback SQL here"})
        assert engine._records["M1"].status == TaskStatus.COMPLETE

    def test_custom_guardrail(self, tmp_path):
        policy = GuardrailPolicy()
        def block_prod(task, context):
            if context.get("env") == "prod" and "prod_approved" not in context:
                return False, "Production deploys require prod_approved in context"
            return True, ""
        policy.add_rule(block_prod)

        engine = OrchestrationEngine(
            scenario="prod_test", gate_mode="auto",
            log_dir=tmp_path, guardrails=policy,
        )
        engine.register([Task(id="T1", name="Prod deploy", fn=_noop)])
        engine.run(initial_context={"env": "prod"})
        assert engine._aborted is True


# ---------------------------------------------------------------------------
# Dynamic re-planning
# ---------------------------------------------------------------------------

class TestReplan:

    def test_replan_adds_tasks_mid_run(self, tmp_path):
        new_tasks_ran = []

        def trigger_replan(ctx):
            return {"replan_needed": True}

        def new_task_fn(ctx):
            new_tasks_ran.append("new")
            return {}

        engine = make_engine(tmp_path)
        engine.register([
            Task(id="T1", name="Trigger", fn=trigger_replan),
        ])

        # Inject new task after T1 completes (simulating mid-run replan)
        original_run = engine.run

        def patched_run(initial_context=None):
            ctx = initial_context or {}
            engine._context.update(ctx)
            engine._context["scenario"] = engine.scenario
            from orchestration.audit import Event
            engine._audit.workflow_start(list(engine._tasks.keys()))

            # Run T1
            ready = engine._find_ready_tasks()
            for t in ready:
                engine._execute_task(t)

            # Simulate replan after T1
            engine.replan(
                [Task(id="T_NEW", name="New task", fn=new_task_fn)],
                reason="Context revealed additional work needed"
            )

            # Run new task
            ready = engine._find_ready_tasks()
            for t in ready:
                engine._execute_task(t)

            engine._finalise()
            return engine._context

        engine.run = patched_run
        engine.run()
        assert "new" in new_tasks_ran


# ---------------------------------------------------------------------------
# Audit log and metrics
# ---------------------------------------------------------------------------

class TestAuditLog:

    def test_ndjson_file_created(self, tmp_path):
        engine = make_engine(tmp_path)
        engine.register([Task(id="T1", name="T1", fn=_noop)])
        engine.run()
        logs = list(tmp_path.glob("*.ndjson"))
        assert len(logs) == 1

    def test_all_entries_are_valid_json(self, tmp_path):
        engine = make_engine(tmp_path)
        engine.register([
            Task(id="T1", name="T1", fn=_noop),
            Task(id="T2", name="T2", fn=_noop, depends_on=["T1"]),
        ])
        engine.run()
        log_file = list(tmp_path.glob("*.ndjson"))[0]
        for line in log_file.read_text().splitlines():
            entry = json.loads(line)  # raises on invalid JSON
            assert "event" in entry
            assert "ts" in entry

    def test_metrics_success_rate_all_pass(self, tmp_path):
        engine = make_engine(tmp_path)
        engine.register([
            Task(id="T1", name="T1", fn=_noop),
            Task(id="T2", name="T2", fn=_noop, depends_on=["T1"]),
        ])
        ctx = engine.run()
        assert ctx["_metrics"]["success_rate"] == 1.0

    def test_metrics_retry_counted(self, tmp_path):
        engine = make_engine(tmp_path)
        policy = RetryPolicy(max_attempts=3, base_delay_s=0.0)
        engine.register([
            Task(id="T1", name="Flaky", fn=_flaky_fn(fail_count=1),
                 retry_policy=policy)
        ])
        ctx = engine.run()
        assert ctx["_metrics"]["total_retries"] >= 1

    def test_summary_contains_required_fields(self, tmp_path):
        engine = make_engine(tmp_path)
        engine.register([Task(id="T1", name="T1", fn=_noop)])
        ctx = engine.run(initial_context={
            "assumptions": ["Base URL is https://short.ly"],
            "risks": ["Redis unavailability"],
            "artifacts": ["src/api/shorten.py"],
        })
        summary = ctx["_summary"]
        assert "run_id"   in summary
        assert "metrics"  in summary
        assert "risks"    in summary
        assert "artifacts" in summary


# ---------------------------------------------------------------------------
# TaskRecord helpers
# ---------------------------------------------------------------------------

class TestTaskRecord:

    def test_duration_computed_correctly(self):
        record = TaskRecord(task_id="T1")
        record.started_at  = 1000.0
        record.finished_at = 1002.5
        assert record.duration_s == 2.5

    def test_duration_none_when_not_finished(self):
        record = TaskRecord(task_id="T1")
        record.started_at = 1000.0
        assert record.duration_s is None

    def test_mark_running_increments_attempts(self):
        record = TaskRecord(task_id="T1")
        record.mark_running()
        assert record.attempts == 1
        record.mark_running()
        assert record.attempts == 2
