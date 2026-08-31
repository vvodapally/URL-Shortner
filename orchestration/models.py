"""
orchestration/models.py
-----------------------
Immutable data structures for the orchestration engine.

Design principle: tasks are declarative — they describe *what* to do and
*when* (dependencies, retry policy, gate requirements). The engine decides
*how* and *in what order*.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, List, Optional


# ---------------------------------------------------------------------------
# Task lifecycle states
# ---------------------------------------------------------------------------

class TaskStatus(str, Enum):
    PENDING   = "pending"     # waiting for dependencies
    READY     = "ready"       # all dependencies met, queued to run
    RUNNING   = "running"     # currently executing
    COMPLETE  = "complete"    # finished successfully
    FAILED    = "failed"      # exhausted retries or unrecoverable error
    SKIPPED   = "skipped"     # upstream gate rejected; will not run
    BLOCKED   = "blocked"     # waiting at a human approval gate


class Scenario(str, Enum):
    GREENFIELD  = "greenfield"   # Scenario 1: build from scratch
    BROWNFIELD  = "brownfield"   # Scenario 2: enhance existing service
    AMBIGUOUS   = "ambiguous"    # Scenario 3: vague "make it reliable"


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------

@dataclass
class RetryPolicy:
    """
    Controls retry behaviour for a single task.

    Attributes
    ----------
    max_attempts : int
        Total attempts including the first. Default 3.
    base_delay_s : float
        Initial wait before first retry (seconds). Default 1.0.
    backoff_factor : float
        Multiplier applied to delay on each subsequent retry. Default 2.0.
        e.g. delays: 1s, 2s, 4s
    on_exhaust : str
        What the engine does when all retries fail.
        "safe_stop"  — halt the entire graph (default, safest)
        "skip"       — mark task skipped, allow graph to continue
        "continue"   — mark failed, keep running unrelated branches
    """
    max_attempts: int   = 3
    base_delay_s: float = 1.0
    backoff_factor: float = 2.0
    on_exhaust: str     = "safe_stop"   # "safe_stop" | "skip" | "continue"

    def delay_for_attempt(self, attempt: int) -> float:
        """Return the wait time (seconds) before `attempt` (1-indexed)."""
        if attempt <= 1:
            return 0.0
        return self.base_delay_s * (self.backoff_factor ** (attempt - 2))


DEFAULT_RETRY = RetryPolicy()
NO_RETRY      = RetryPolicy(max_attempts=1, on_exhaust="safe_stop")


# ---------------------------------------------------------------------------
# Task definition
# ---------------------------------------------------------------------------

@dataclass
class Task:
    """
    A single unit of work in the orchestration DAG.

    Tasks are declarative: they carry metadata and a callable (`fn`).
    The engine resolves the execution order, manages retries, and logs
    every state transition.

    Attributes
    ----------
    id : str
        Unique identifier within a workflow (e.g. "T1", "impl_core_api").
    name : str
        Human-readable label shown in logs and the audit trail.
    fn : Callable[..., Any]
        The actual work. Receives `context: dict` as its only argument.
        Must return a dict that gets merged into the shared context.
    depends_on : list[str]
        IDs of tasks that must reach COMPLETE before this task is READY.
    retry_policy : RetryPolicy
        How many times to retry and what to do on exhaustion.
    requires_human_gate : bool
        If True, the engine pauses before running this task and waits for
        explicit human approval. Used for high-impact, hard-to-reverse ops.
    gate_label : str
        Description shown to the human at the approval gate.
    scenario : Scenario | None
        Which scenario this task belongs to (for filtering / reporting).
    tags : list[str]
        Free-form labels (e.g. ["db", "migration", "brownfield"]).
    timeout_s : float | None
        Maximum wall-clock seconds allowed. None = no timeout.
    """
    id:                  str
    name:                str
    fn:                  Callable[[dict], dict]
    depends_on:          List[str]       = field(default_factory=list)
    retry_policy:        RetryPolicy     = field(default_factory=RetryPolicy)
    requires_human_gate: bool            = False
    gate_label:          str             = ""
    scenario:            Optional[Scenario] = None
    tags:                List[str]       = field(default_factory=list)
    timeout_s:           Optional[float] = None


# ---------------------------------------------------------------------------
# Task execution record (mutable runtime state)
# ---------------------------------------------------------------------------

@dataclass
class TaskRecord:
    """
    Tracks the live state of a Task during engine execution.
    Serialised to JSON for persistence and audit.
    """
    task_id:       str
    status:        TaskStatus   = TaskStatus.PENDING
    attempts:      int          = 0
    started_at:    Optional[float] = None   # epoch seconds
    finished_at:   Optional[float] = None
    error:         Optional[str]   = None
    result:        Optional[dict]  = None

    # Computed helpers
    @property
    def duration_s(self) -> Optional[float]:
        if self.started_at and self.finished_at:
            return round(self.finished_at - self.started_at, 3)
        return None

    def mark_running(self) -> None:
        self.status     = TaskStatus.RUNNING
        self.started_at = time.time()
        self.attempts  += 1

    def mark_complete(self, result: dict) -> None:
        self.status      = TaskStatus.COMPLETE
        self.finished_at = time.time()
        self.result      = result
        self.error       = None

    def mark_failed(self, error: str) -> None:
        self.status      = TaskStatus.FAILED
        self.finished_at = time.time()
        self.error       = error

    def mark_skipped(self, reason: str) -> None:
        self.status      = TaskStatus.SKIPPED
        self.finished_at = time.time()
        self.error       = reason

    def to_dict(self) -> dict:
        return {
            "task_id":     self.task_id,
            "status":      self.status.value,
            "attempts":    self.attempts,
            "started_at":  self.started_at,
            "finished_at": self.finished_at,
            "duration_s":  self.duration_s,
            "error":       self.error,
        }
