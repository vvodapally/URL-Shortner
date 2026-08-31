"""
orchestration/audit.py
----------------------
Audit-grade observability for the orchestration engine.

Every state transition, human gate decision, retry, and metric is written
here as a structured JSON log entry. The AuditLog also computes the
reliability metrics required by the assessment:
  - success_rate
  - retry_frequency  (retries per task)
  - mttr             (mean time to recover from failure)
  - e2e_latency      (total wall-clock time per scenario)

Design: append-only. Nothing is ever deleted. Entries are newline-delimited
JSON (NDJSON) so they can be streamed, grepped, or fed to any log aggregator.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import TaskRecord, TaskStatus


# ---------------------------------------------------------------------------
# Log entry types (used as the `event` field)
# ---------------------------------------------------------------------------

class Event:
    TASK_READY      = "task_ready"
    TASK_STARTED    = "task_started"
    TASK_COMPLETE   = "task_complete"
    TASK_FAILED     = "task_failed"
    TASK_RETRY      = "task_retry"
    TASK_SKIPPED    = "task_skipped"
    GATE_PRESENTED  = "gate_presented"
    GATE_APPROVED   = "gate_approved"
    GATE_REJECTED   = "gate_rejected"
    WORKFLOW_START  = "workflow_start"
    WORKFLOW_DONE   = "workflow_done"
    WORKFLOW_ABORT  = "workflow_abort"
    REPLAN          = "replan"          # dynamic re-planning triggered
    SAFE_STOP       = "safe_stop"


# ---------------------------------------------------------------------------
# AuditLog
# ---------------------------------------------------------------------------

class AuditLog:
    """
    Append-only structured log for one workflow run.

    Parameters
    ----------
    log_path : Path
        Where to write NDJSON entries.
    scenario : str
        Label written into every entry for filtering.
    run_id : str
        Unique run identifier (timestamp-based by default).
    """

    def __init__(self, log_path: Path, scenario: str, run_id: str) -> None:
        self.log_path  = log_path
        self.scenario  = scenario
        self.run_id    = run_id
        self._entries: List[Dict[str, Any]] = []
        self._start_time: Optional[float] = None

        log_path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Core write
    # ------------------------------------------------------------------

    def log(self, event: str, **kwargs: Any) -> None:
        """Write a single structured entry."""
        entry: Dict[str, Any] = {
            "ts":       round(time.time(), 3),
            "run_id":   self.run_id,
            "scenario": self.scenario,
            "event":    event,
            **kwargs,
        }
        self._entries.append(entry)
        with self.log_path.open("a") as fh:
            fh.write(json.dumps(entry) + "\n")

    # ------------------------------------------------------------------
    # Convenience wrappers (called by the engine)
    # ------------------------------------------------------------------

    def workflow_start(self, task_ids: List[str]) -> None:
        self._start_time = time.time()
        self.log(Event.WORKFLOW_START, tasks=task_ids)

    def workflow_done(self, summary: dict) -> None:
        elapsed = round(time.time() - self._start_time, 3) if self._start_time else None
        self.log(Event.WORKFLOW_DONE, elapsed_s=elapsed, **summary)

    def workflow_abort(self, reason: str) -> None:
        elapsed = round(time.time() - self._start_time, 3) if self._start_time else None
        self.log(Event.WORKFLOW_ABORT, elapsed_s=elapsed, reason=reason)

    def task_ready(self, task_id: str) -> None:
        self.log(Event.TASK_READY, task_id=task_id)

    def task_started(self, task_id: str, attempt: int) -> None:
        self.log(Event.TASK_STARTED, task_id=task_id, attempt=attempt)

    def task_complete(self, record: TaskRecord) -> None:
        self.log(Event.TASK_COMPLETE, **record.to_dict())

    def task_failed(self, record: TaskRecord) -> None:
        self.log(Event.TASK_FAILED, **record.to_dict())

    def task_retry(self, task_id: str, attempt: int, delay_s: float, error: str) -> None:
        self.log(Event.TASK_RETRY, task_id=task_id,
                 attempt=attempt, delay_s=delay_s, error=error)

    def task_skipped(self, task_id: str, reason: str) -> None:
        self.log(Event.TASK_SKIPPED, task_id=task_id, reason=reason)

    def gate_presented(self, gate_label: str, task_id: str, context_snapshot: dict) -> None:
        self.log(Event.GATE_PRESENTED, gate_label=gate_label,
                 task_id=task_id, context_keys=list(context_snapshot.keys()))

    def gate_approved(self, gate_label: str, task_id: str) -> None:
        self.log(Event.GATE_APPROVED, gate_label=gate_label, task_id=task_id)

    def gate_rejected(self, gate_label: str, task_id: str, reason: str) -> None:
        self.log(Event.GATE_REJECTED, gate_label=gate_label,
                 task_id=task_id, reason=reason)

    def replan(self, reason: str, new_task_ids: List[str]) -> None:
        self.log(Event.REPLAN, reason=reason, new_task_ids=new_task_ids)

    def safe_stop(self, task_id: str, reason: str) -> None:
        self.log(Event.SAFE_STOP, task_id=task_id, reason=reason)

    # ------------------------------------------------------------------
    # Reliability metrics
    # ------------------------------------------------------------------

    def compute_metrics(self) -> Dict[str, Any]:
        """
        Compute the reliability metrics required by the assessment rubric.

        Returns
        -------
        dict with keys:
            success_rate      — fraction of tasks that completed successfully
            retry_frequency   — average retries per task (excludes first attempt)
            mttr_s            — mean time to recover (avg duration of failed→retry→complete)
            e2e_latency_s     — total elapsed time for the workflow
            tasks_complete    — count
            tasks_failed      — count
            tasks_skipped     — count
            total_retries     — count
        """
        complete_entries = [e for e in self._entries if e["event"] == Event.TASK_COMPLETE]
        failed_entries   = [e for e in self._entries if e["event"] == Event.TASK_FAILED]
        retry_entries    = [e for e in self._entries if e["event"] == Event.TASK_RETRY]
        skipped_entries  = [e for e in self._entries if e["event"] == Event.TASK_SKIPPED]

        n_complete = len(complete_entries)
        n_failed   = len(failed_entries)
        n_skipped  = len(skipped_entries)
        n_retries  = len(retry_entries)
        n_total    = n_complete + n_failed + n_skipped

        success_rate    = round(n_complete / n_total, 3) if n_total > 0 else 0.0
        retry_frequency = round(n_retries / n_total, 3)  if n_total > 0 else 0.0

        # MTTR: for tasks that retried and eventually completed,
        # measure time from first attempt start to final complete
        mttr_s = self._compute_mttr()

        # E2E latency from workflow_start to workflow_done/abort
        e2e_s = self._compute_e2e()

        return {
            "success_rate":    success_rate,
            "retry_frequency": retry_frequency,
            "mttr_s":          mttr_s,
            "e2e_latency_s":   e2e_s,
            "tasks_complete":  n_complete,
            "tasks_failed":    n_failed,
            "tasks_skipped":   n_skipped,
            "total_retries":   n_retries,
        }

    def _compute_mttr(self) -> Optional[float]:
        """Average time from first attempt to final success for retried tasks."""
        # Group start/complete times by task_id for tasks that retried
        retried: Dict[str, dict] = {}
        for e in self._entries:
            tid = e.get("task_id")
            if not tid:
                continue
            if e["event"] == Event.TASK_STARTED and e.get("attempt", 1) == 1:
                retried.setdefault(tid, {})["first_start"] = e["ts"]
            if e["event"] == Event.TASK_COMPLETE and tid in retried:
                retried[tid]["final_end"] = e["ts"]

        # Keep only tasks that actually retried (appeared in retry log)
        retried_ids = {e["task_id"] for e in self._entries if e["event"] == Event.TASK_RETRY}
        durations = [
            v["final_end"] - v["first_start"]
            for tid, v in retried.items()
            if tid in retried_ids and "final_end" in v and "first_start" in v
        ]
        return round(sum(durations) / len(durations), 3) if durations else None

    def _compute_e2e(self) -> Optional[float]:
        start_entries = [e for e in self._entries if e["event"] == Event.WORKFLOW_START]
        end_entries   = [e for e in self._entries if e["event"] in (
            Event.WORKFLOW_DONE, Event.WORKFLOW_ABORT)]
        if start_entries and end_entries:
            return round(end_entries[-1]["ts"] - start_entries[0]["ts"], 3)
        return None

    # ------------------------------------------------------------------
    # Final engineering summary (assessment deliverable)
    # ------------------------------------------------------------------

    def generate_summary(self, context: dict) -> Dict[str, Any]:
        """
        Produce the final engineering summary document required by the
        assessment rubric (section 8: Final Engineering Summary).
        """
        metrics = self.compute_metrics()
        gates   = [e for e in self._entries if e["event"] in (
            Event.GATE_APPROVED, Event.GATE_REJECTED)]

        return {
            "run_id":   self.run_id,
            "scenario": self.scenario,
            "metrics":  metrics,
            "human_gates": [
                {
                    "label":    g.get("gate_label"),
                    "task_id":  g.get("task_id"),
                    "decision": "approved" if g["event"] == Event.GATE_APPROVED else "rejected",
                    "ts":       g["ts"],
                }
                for g in gates
            ],
            "context_keys_at_completion": list(context.keys()),
            "assumptions": context.get("assumptions", []),
            "risks":       context.get("risks", []),
            "limitations": context.get("limitations", []),
            "artifacts":   context.get("artifacts", []),
            "log_path":    str(self.log_path),
        }
