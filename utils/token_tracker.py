"""Token usage tracker for the bridge.

Tracks every supervisor call (plan, review, sub-plan) and estimates:
  - How many tokens Claude actually used as supervisor
  - How many tokens Claude WOULD have used if it wrote all code directly
  - How many tokens the bridge saved

Token estimation uses the standard approximation: 1 token ≈ 4 characters.
This is accurate to within ~15% for English/code text.

SAVINGS MODEL
─────────────
Without the bridge, Claude would need to:
  1. Plan (same tokens — unavoidable)
  2. Per task: write the full implementation + review its own output
     → estimated at _DIRECT_TOKENS_PER_TASK (default 5 000)

With the bridge:
  1. Plan (same)
  2. Per task: review the diff only (~700 tokens)

So savings ≈ (tasks_executed × (_DIRECT_TOKENS_PER_TASK − avg_review_tokens))
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

# Estimated tokens Claude would spend writing + self-reviewing one task's code
# directly (without Aider). Conservative mid-range estimate for a typical task.
_DIRECT_TOKENS_PER_TASK: int = 5_000


def _estimate(text: str) -> int:
    """Estimate token count from character length (1 token ≈ 4 chars)."""
    return max(1, len(text) // 4)


class TokenTracker:
    """Accumulates token usage for a single bridge run session."""

    def __init__(self) -> None:
        self._plan_in: int = 0
        self._plan_out: int = 0
        self._review_in: int = 0
        self._review_out: int = 0
        self._subplan_in: int = 0
        self._subplan_out: int = 0
        self._reworks: int = 0
        self._subplans: int = 0

    # ── Recording ─────────────────────────────────────────────────────────────

    def record_plan(self, prompt: str, response: str) -> None:
        """Record tokens for a planning call."""
        self._plan_in += _estimate(prompt)
        self._plan_out += _estimate(response)

    def record_review(self, prompt: str, response: str, is_rework: bool = False) -> None:
        """Record tokens for a task review call."""
        self._review_in += _estimate(prompt)
        self._review_out += _estimate(response)
        if is_rework:
            self._reworks += 1

    def record_subplan(self, prompt: str, response: str) -> None:
        """Record tokens for a sub-plan generation call."""
        self._subplan_in += _estimate(prompt)
        self._subplan_out += _estimate(response)
        self._subplans += 1

    # ── Live snapshot ─────────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """Return current totals as a plain dict (no savings calc — no task count yet)."""
        total_in = self._plan_in + self._review_in + self._subplan_in
        total_out = self._plan_out + self._review_out + self._subplan_out
        return {
            "plan_in": self._plan_in,
            "plan_out": self._plan_out,
            "review_in": self._review_in,
            "review_out": self._review_out,
            "subplan_in": self._subplan_in,
            "subplan_out": self._subplan_out,
            "total_in": total_in,
            "total_out": total_out,
            "total": total_in + total_out,
            "reworks": self._reworks,
            "subplans_generated": self._subplans,
        }

    # ── Session report ────────────────────────────────────────────────────────

    def build_session_report(
        self,
        goal: str,
        repo_root: Path,
        supervisor_command: str,
        tasks_executed: int,
        tasks_skipped: int,
        elapsed_seconds: float,
    ) -> dict:
        """Build a complete session report dict including savings calculation."""
        snap = self.snapshot()
        total_supervisor = snap["total"]

        # What Claude would have spent without the bridge
        plan_tokens = snap["plan_in"] + snap["plan_out"]
        estimated_direct = plan_tokens + (tasks_executed * _DIRECT_TOKENS_PER_TASK)

        tokens_saved = max(0, estimated_direct - total_supervisor)
        savings_pct = (
            round(tokens_saved / estimated_direct * 100, 1)
            if estimated_direct > 0 else 0.0
        )

        return {
            "session_id": str(uuid.uuid4()),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "goal": goal,
            "repo_root": str(repo_root),
            "supervisor_command": supervisor_command,
            "supervisor": {
                "plan_in": snap["plan_in"],
                "plan_out": snap["plan_out"],
                "review_in": snap["review_in"],
                "review_out": snap["review_out"],
                "subplan_in": snap["subplan_in"],
                "subplan_out": snap["subplan_out"],
                "total_in": snap["total_in"],
                "total_out": snap["total_out"],
                "total": total_supervisor,
            },
            "aider": {
                "tasks_executed": tasks_executed,
                "tasks_skipped": tasks_skipped,
                "reworks": self._reworks,
                "subplans_generated": self._subplans,
            },
            "savings": {
                "estimated_without_bridge": estimated_direct,
                "actual_supervisor_tokens": total_supervisor,
                "tokens_saved": tokens_saved,
                "savings_percent": savings_pct,
                "note": (
                    f"Without bridge: plan ({plan_tokens} tokens) + "
                    f"{tasks_executed} tasks × {_DIRECT_TOKENS_PER_TASK} "
                    f"direct-coding tokens = {estimated_direct}"
                ),
            },
            "elapsed_seconds": round(elapsed_seconds, 1),
        }


# ── Persistence ───────────────────────────────────────────────────────────────

def load_token_log(log_path: Path) -> dict:
    """Load the token log JSON file. Returns empty structure if missing/corrupt."""
    if log_path.exists():
        try:
            return json.loads(log_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"sessions": [], "totals": _empty_totals()}


def save_session_to_log(session: dict, log_path: Path) -> None:
    """Append a session report to the log and recalculate all-time totals."""
    data = load_token_log(log_path)
    data["sessions"].insert(0, session)      # newest first

    sessions = data["sessions"]
    total_tasks = sum(s["aider"]["tasks_executed"] for s in sessions)
    total_supervisor = sum(s["supervisor"]["total"] for s in sessions)
    total_saved = sum(s["savings"]["tokens_saved"] for s in sessions)
    avg_savings_pct = (
        round(sum(s["savings"]["savings_percent"] for s in sessions) / len(sessions), 1)
        if sessions else 0.0
    )

    data["totals"] = {
        "sessions_count": len(sessions),
        "tasks_executed_total": total_tasks,
        "supervisor_tokens_total": total_supervisor,
        "tokens_saved_total": total_saved,
        "savings_percent_avg": avg_savings_pct,
        "last_updated": datetime.now().isoformat(timespec="seconds"),
    }

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _empty_totals() -> dict:
    return {
        "sessions_count": 0,
        "tasks_executed_total": 0,
        "supervisor_tokens_total": 0,
        "tokens_saved_total": 0,
        "savings_percent_avg": 0.0,
        "last_updated": None,
    }
