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

# Cloud AI pricing per million tokens (USD)
PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4": {"input": 15.0, "output": 75.0},
    "claude-sonnet-4": {"input": 3.0, "output": 15.0},
    "claude-haiku": {"input": 0.25, "output": 1.25},
    "gpt-4o": {"input": 2.50, "output": 10.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "default": {"input": 3.0, "output": 15.0},  # Sonnet pricing as default
}


def estimate_cost(input_tokens: int, output_tokens: int, model: str = "default") -> float:
    """Estimate USD cost for a given token count and model."""
    prices = PRICING.get(model, PRICING["default"])
    return (input_tokens * prices["input"] + output_tokens * prices["output"]) / 1_000_000


def _estimate(text: str) -> int:
    """Estimate token count from character length (1 token ≈ 4 chars)."""
    return max(1, len(text) // 4)


def _classify_waste_reason(
    tasks_executed: int,
    failure_reason: Optional[str],
) -> Optional[str]:
    if tasks_executed > 0:
        return None

    normalized = (failure_reason or "").lower()
    if "invalid argument" in normalized or "stdout" in normalized:
        return "bridge_stdout_crash"
    if "model" in normalized and ("not found" in normalized or "missing" in normalized):
        return "model_missing"
    if (
        "mechanical check" in normalized
        or "unexpected file" in normalized
        or "did not modify" in normalized
        or "whitespace or comments" in normalized
    ):
        return "mechanical_validation_loop"
    if "manual supervisor" in normalized or "decision" in normalized or "request" in normalized:
        return "manual_review_rerun"
    return "zero_progress_other"


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
        # Tokens spent by the AI supervisor in its interactive session
        self._session_tokens: int = 0
        self._session_tokens_is_estimate: bool = True
        # Aider/Ollama estimated token usage
        self._aider_total: int = 0
        self._aider_per_task: list[dict] = []

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

    def record_session_tokens(self, tokens: int, is_estimate: bool = True) -> None:
        """Record tokens spent by the AI supervisor in its interactive session.

        This covers everything the AI does outside of bridge subprocess calls:
        reading the onboarding doc, idea file, knowledge cache, WORK_LOG,
        generating the plan JSON, reviewing diffs, and the conversation itself.

        Pass is_estimate=False when the AI provides the exact value via
        --session-tokens N (e.g. from its own token counter or /cost output).
        """
        self._session_tokens = max(0, tokens)
        self._session_tokens_is_estimate = is_estimate

    def record_aider_task(
        self,
        task_id: int,
        instruction: str,
        input_file_chars: int,
        diff_chars: int,
    ) -> None:
        """Estimate Aider/Ollama token usage for one task.

        Aider calls Ollama internally so we cannot intercept the API response.
        We estimate from: instruction length + input file sizes + output diff.
        """
        estimated = _estimate(instruction) + max(1, input_file_chars // 4) + max(1, diff_chars // 4)
        self._aider_total += estimated
        self._aider_per_task.append({
            "task_id": task_id,
            "estimated_tokens": estimated,
        })

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
            "session_tokens": self._session_tokens,
            "session_tokens_is_estimate": self._session_tokens_is_estimate,
            "aider_total": self._aider_total,
            "aider_per_task": list(self._aider_per_task),
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
        failure_reason: Optional[str] = None,
    ) -> dict:
        """Build a complete session report dict including savings calculation."""
        snap = self.snapshot()
        total_supervisor = snap["total"]

        # What the AI would have spent without the bridge.
        #
        # Baseline = planning cost + (per-task direct-coding cost).
        # Planning cost is whichever is larger: subprocess plan tokens (supervised
        # mode) or session tokens (interactive mode where the AI IS the planner).
        # This prevents the "100% savings" illusion where session tokens are
        # ignored and the baseline collapses to zero.
        plan_tokens = snap["plan_in"] + snap["plan_out"]
        session_tokens = snap["session_tokens"]
        baseline_planning = max(plan_tokens, session_tokens)
        estimated_direct = baseline_planning + (tasks_executed * _DIRECT_TOKENS_PER_TASK)

        # Total tokens charged to the AI account this session:
        #   subprocess supervisor calls (plan/review) + interactive session work
        total_ai_tokens = total_supervisor + session_tokens

        tokens_saved = max(0, estimated_direct - total_ai_tokens)
        savings_pct = (
            round(tokens_saved / estimated_direct * 100, 1)
            if estimated_direct > 0 else 0.0
        )
        waste_reason = _classify_waste_reason(tasks_executed, failure_reason)
        productivity = {
            "is_productive": tasks_executed > 0,
            "waste_reason": waste_reason,
        }
        note = (
            f"Without bridge: plan ({plan_tokens} tokens) + "
            f"{tasks_executed} tasks x {_DIRECT_TOKENS_PER_TASK} "
            f"direct-coding tokens = {estimated_direct}; "
            f"with bridge: supervisor {total_ai_tokens} tokens (cloud) + "
            f"Aider ~{self._aider_total} tokens (local/free); "
            f"cloud AI saved: {tokens_saved}"
        )
        if tasks_executed == 0:
            note += "; session completed no tasks and should be treated as overhead, not productive savings"

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
            "session": {
                "tokens": snap["session_tokens"],
                "is_estimate": snap["session_tokens_is_estimate"],
                "total_ai_tokens": total_ai_tokens,
            },
            "aider": {
                "tasks_executed": tasks_executed,
                "tasks_skipped": tasks_skipped,
                "reworks": self._reworks,
                "subplans_generated": self._subplans,
                "estimated_tokens": self._aider_total,
                "per_task": list(self._aider_per_task),
            },
            "productivity": productivity,
            "savings": {
                "estimated_direct_tokens": estimated_direct,
                "actual_supervisor_tokens": total_supervisor,
                "total_ai_tokens": total_ai_tokens,
                "tokens_saved": tokens_saved,
                "savings_percent": savings_pct,
                "note": note,
            },
            "cost": {
                "bridge_cost_opus": round(estimate_cost(snap["total_in"], snap["total_out"], "claude-opus-4"), 4),
                "bridge_cost_sonnet": round(estimate_cost(snap["total_in"], snap["total_out"], "claude-sonnet-4"), 4),
                "direct_cost_opus": round(estimate_cost(estimated_direct * 0.6, estimated_direct * 0.4, "claude-opus-4"), 4),
                "direct_cost_sonnet": round(estimate_cost(estimated_direct * 0.6, estimated_direct * 0.4, "claude-sonnet-4"), 4),
                "ollama_cost": 0.0,
                "ollama_tokens": self._aider_total,
                "savings_dollar_opus": 0.0,
                "savings_dollar_sonnet": 0.0,
            },
            "elapsed_seconds": round(elapsed_seconds, 1),
        }
        # Calculate dollar savings
        c = report["cost"]
        c["savings_dollar_opus"] = round(max(0, c["direct_cost_opus"] - c["bridge_cost_opus"]), 4)
        c["savings_dollar_sonnet"] = round(max(0, c["direct_cost_sonnet"] - c["bridge_cost_sonnet"]), 4)
        return report


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
    total_session = sum(s.get("session", {}).get("tokens", 0) for s in sessions)
    total_ai = sum(s.get("session", {}).get("total_ai_tokens", s["supervisor"]["total"]) for s in sessions)
    total_saved = sum(s["savings"]["tokens_saved"] for s in sessions)
    successful_sessions = [s for s in sessions if s["aider"]["tasks_executed"] > 0]
    zero_progress_sessions = [s for s in sessions if s["aider"]["tasks_executed"] == 0]
    successful_avg = (
        round(
            sum(s["savings"]["savings_percent"] for s in successful_sessions) / len(successful_sessions),
            1,
        )
        if successful_sessions else 0.0
    )
    total_estimated_direct = sum(s["savings"]["estimated_direct_tokens"] for s in sessions)
    weighted_savings_pct = (
        round(total_saved / total_estimated_direct * 100, 1)
        if total_estimated_direct > 0 else 0.0
    )
    wasted_tokens_total = sum(
        s.get("session", {}).get("total_ai_tokens", s["supervisor"]["total"])
        for s in zero_progress_sessions
    )
    waste_reason_counts: dict[str, int] = {}
    for zero_progress_session in zero_progress_sessions:
        reason = str(zero_progress_session.get("productivity", {}).get("waste_reason", "zero_progress_other"))
        waste_reason_counts[reason] = waste_reason_counts.get(reason, 0) + 1

    total_aider = sum(s.get("aider", {}).get("estimated_tokens", 0) for s in sessions)

    # Aggregate dollar costs
    total_cost_opus = sum(s.get("cost", {}).get("bridge_cost_opus", 0) for s in sessions)
    total_cost_sonnet = sum(s.get("cost", {}).get("bridge_cost_sonnet", 0) for s in sessions)
    total_direct_opus = sum(s.get("cost", {}).get("direct_cost_opus", 0) for s in sessions)
    total_direct_sonnet = sum(s.get("cost", {}).get("direct_cost_sonnet", 0) for s in sessions)

    data["totals"] = {
        "sessions_count": len(sessions),
        "tasks_executed_total": total_tasks,
        "supervisor_tokens_total": total_supervisor,
        "session_tokens_total": total_session,
        "total_ai_tokens_total": total_ai,
        "aider_tokens_total": total_aider,
        "tokens_saved_total": total_saved,
        "savings_percent_weighted": weighted_savings_pct,
        "savings_percent_successful_avg": successful_avg,
        "savings_percent_avg": successful_avg,
        "wasted_tokens_total": wasted_tokens_total,
        "wasted_sessions_count": len(zero_progress_sessions),
        "waste_reason_counts": waste_reason_counts,
        "cost_total": {
            "bridge_opus": round(total_cost_opus, 4),
            "bridge_sonnet": round(total_cost_sonnet, 4),
            "direct_opus": round(total_direct_opus, 4),
            "direct_sonnet": round(total_direct_sonnet, 4),
            "ollama": 0.0,
            "savings_opus": round(max(0, total_direct_opus - total_cost_opus), 4),
            "savings_sonnet": round(max(0, total_direct_sonnet - total_cost_sonnet), 4),
        },
        "last_updated": datetime.now().isoformat(timespec="seconds"),
    }

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _empty_totals() -> dict:
    return {
        "sessions_count": 0,
        "tasks_executed_total": 0,
        "supervisor_tokens_total": 0,
        "session_tokens_total": 0,
        "total_ai_tokens_total": 0,
        "aider_tokens_total": 0,
        "tokens_saved_total": 0,
        "savings_percent_weighted": 0.0,
        "savings_percent_successful_avg": 0.0,
        "savings_percent_avg": 0.0,
        "wasted_tokens_total": 0,
        "wasted_sessions_count": 0,
        "waste_reason_counts": {},
        "last_updated": None,
    }
