"""
orchestration/gates.py
----------------------
Human approval checkpoint logic.

Gates are the boundary between autonomous agent execution and human oversight.
When a task has `requires_human_gate=True`, the engine calls `GateKeeper.check`
before running it. The gate can be:

  - AUTO-APPROVED  (CI / test mode — gate_mode="auto")
  - INTERACTIVE    (CLI prompt — gate_mode="interactive")
  - FILE-BASED     (gate_mode="file", reads a signal file dropped by a human)

This design lets the same orchestration code run unattended in CI while still
requiring real human approval in production / assessment demos.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Literal, Optional


GateMode = Literal["interactive", "auto", "file"]


class GateDecision:
    APPROVED = "approved"
    REJECTED = "rejected"


class GateKeeper:
    """
    Manages human approval gates across a workflow run.

    Parameters
    ----------
    mode : GateMode
        "interactive" — prompt the operator at the terminal (default).
        "auto"        — approve all gates automatically (CI / test mode).
        "file"        — wait for a signal file at `signal_dir/<task_id>.approve`
                        or `<task_id>.reject`.
    signal_dir : Path | None
        Directory to watch in "file" mode.
    timeout_s : float
        How long to wait in "file" mode before timing out (default: 300s).
    """

    def __init__(
        self,
        mode: GateMode = "interactive",
        signal_dir: Optional[Path] = None,
        timeout_s: float = 300.0,
    ) -> None:
        self.mode       = mode
        self.signal_dir = signal_dir or Path("/tmp/orch_gates")
        self.timeout_s  = timeout_s

        if mode == "file":
            self.signal_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API (called by engine)
    # ------------------------------------------------------------------

    def check(
        self,
        task_id: str,
        gate_label: str,
        context_snapshot: dict,
    ) -> tuple[str, str]:
        """
        Present the gate to the human and return their decision.

        Returns
        -------
        (decision, reason)
            decision — GateDecision.APPROVED or GateDecision.REJECTED
            reason   — free-text explanation (empty string if approved in auto mode)
        """
        self._print_gate_summary(task_id, gate_label, context_snapshot)

        if self.mode == "auto":
            return GateDecision.APPROVED, "auto-approved (CI mode)"

        if self.mode == "interactive":
            return self._interactive_gate(task_id, gate_label)

        if self.mode == "file":
            return self._file_gate(task_id)

        raise ValueError(f"Unknown gate mode: {self.mode!r}")

    # ------------------------------------------------------------------
    # Mode implementations
    # ------------------------------------------------------------------

    def _interactive_gate(
        self, task_id: str, gate_label: str
    ) -> tuple[str, str]:
        """Block at the terminal until the operator approves or rejects."""
        print("\n" + "=" * 60)
        print(f"  ⬡  HUMAN GATE: {gate_label}")
        print(f"     Task: {task_id}")
        print("=" * 60)
        print("  Options:  [a] Approve   [r] Reject   [i] Inspect context")
        print()

        while True:
            choice = input("  Decision > ").strip().lower()
            if choice == "a":
                print("  ✓ Approved — continuing workflow.\n")
                return GateDecision.APPROVED, ""
            elif choice == "r":
                reason = input("  Rejection reason: ").strip()
                print(f"  ✗ Rejected — halting workflow: {reason}\n")
                return GateDecision.REJECTED, reason
            elif choice == "i":
                print(json.dumps(self._safe_snapshot({}), indent=2))
            else:
                print("  Please enter 'a', 'r', or 'i'.")

    def _file_gate(self, task_id: str) -> tuple[str, str]:
        """
        Poll for a signal file.

        Operator creates one of:
            <signal_dir>/<task_id>.approve   →  approved
            <signal_dir>/<task_id>.reject    →  rejected (first line = reason)
        """
        approve_path = self.signal_dir / f"{task_id}.approve"
        reject_path  = self.signal_dir / f"{task_id}.reject"
        deadline     = time.time() + self.timeout_s

        print(f"\n  Waiting for gate signal in: {self.signal_dir}")
        print(f"  Create '{task_id}.approve' or '{task_id}.reject' to proceed.")
        print(f"  Timeout in {self.timeout_s}s.\n")

        while time.time() < deadline:
            if approve_path.exists():
                approve_path.unlink()
                return GateDecision.APPROVED, ""
            if reject_path.exists():
                reason = reject_path.read_text().strip().splitlines()[0] if reject_path.stat().st_size else ""
                reject_path.unlink()
                return GateDecision.REJECTED, reason
            time.sleep(2.0)

        return GateDecision.REJECTED, f"Gate timed out after {self.timeout_s}s"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _print_gate_summary(
        self, task_id: str, gate_label: str, context_snapshot: dict
    ) -> None:
        """Pretty-print the gate context so the human can make an informed decision."""
        print("\n" + "-" * 60)
        print(f"  Gate checkpoint for task: {task_id}")
        print(f"  Label: {gate_label}")
        print()

        # Show the most useful context keys without dumping the whole thing
        keys_to_show = [
            "scenario", "assumptions", "risks", "architecture_decisions",
            "migration_plan", "impact_analysis", "reliability_interpretation",
        ]
        for key in keys_to_show:
            if key in context_snapshot:
                val = context_snapshot[key]
                if isinstance(val, (list, dict)):
                    print(f"  {key}:")
                    for item in (val if isinstance(val, list) else [val]):
                        print(f"    • {item}")
                else:
                    print(f"  {key}: {val}")
        print("-" * 60)

    @staticmethod
    def _safe_snapshot(context: dict) -> dict:
        """Return a JSON-serialisable subset of the context."""
        safe = {}
        for k, v in context.items():
            try:
                json.dumps(v)
                safe[k] = v
            except (TypeError, ValueError):
                safe[k] = str(v)
        return safe
