"""Bridge Telemetry — collects anonymized usage data for product improvement.

Writes a local telemetry log that can be exported and fed to an AI
(Claude) to identify:
  - Which features are used most/least
  - Where users get stuck (repeated failures, abandoned flows)
  - What error patterns occur across users
  - UX friction points (rage clicks, back-and-forth navigation)
  - Feature requests implied by usage patterns

All data stays LOCAL. Nothing is sent anywhere automatically.
The user exports it manually via the UI or API.

Schema designed for AI consumption — structured JSON that Claude
can read and produce actionable improvement recommendations.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional


# Max events per session before rotation
_MAX_EVENTS = 2000

# Telemetry file location (per-project)
def _telemetry_path(repo_root: Path) -> Path:
    return repo_root / "bridge_progress" / "telemetry.json"


def _global_telemetry_path() -> Path:
    """Global telemetry (not project-specific)."""
    import os
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    else:
        base = Path.home() / ".local" / "share"
    d = base / "AiderBridge"
    d.mkdir(parents=True, exist_ok=True)
    return d / "telemetry.json"


class TelemetryCollector:
    """Collects structured usage events for product analytics."""

    def __init__(self, instance_id: str = ""):
        self._instance_id = instance_id or str(uuid.uuid4())[:8]
        self._session_id = str(uuid.uuid4())[:8]
        self._session_start = datetime.now().isoformat(timespec="seconds")
        self._events: list[dict] = []

    # ── Event recording ───────────────────────────────────────────────

    def record(self, category: str, action: str, detail: dict = None) -> None:
        """Record a telemetry event.

        Categories:
          run      — run lifecycle (start, complete, fail, resume)
          plan     — plan generation (generate, confirm, import, type)
          task     — task execution (start, pass, fail, rework, skip, timeout)
          review   — supervisor review (auto, manual, chatbot, switch)
          ui       — UI interactions (page_view, tab_switch, button_click)
          error    — errors and crashes
          feature  — feature usage (git, knowledge, tokens, diagnostics)
        """
        event = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "cat": category,
            "act": action,
        }
        if detail:
            event["d"] = detail
        self._events.append(event)
        if len(self._events) > _MAX_EVENTS:
            self._events = self._events[-_MAX_EVENTS:]

    # Shorthand methods
    def run_started(self, supervisor: str, model: str, goal_len: int, task_count: int):
        self.record("run", "start", {"sup": supervisor, "model": model, "goal_len": goal_len, "tasks": task_count})

    def run_completed(self, tasks_done: int, tasks_total: int, elapsed: float, savings_pct: float):
        self.record("run", "complete", {"done": tasks_done, "total": tasks_total, "elapsed": round(elapsed), "savings": savings_pct})

    def run_failed(self, error: str, task_id: int = 0, elapsed: float = 0):
        self.record("run", "fail", {"err": error[:200], "task": task_id, "elapsed": round(elapsed)})

    def run_resumed(self, from_task: int, total: int):
        self.record("run", "resume", {"from": from_task, "total": total})

    def plan_generated(self, source: str, task_count: int, goal_category: str):
        self.record("plan", "generate", {"src": source, "tasks": task_count, "cat": goal_category})

    def plan_imported(self, task_count: int):
        self.record("plan", "import", {"tasks": task_count})

    def task_completed(self, task_id: int, task_type: str, duration: float):
        self.record("task", "pass", {"id": task_id, "type": task_type, "dur": round(duration)})

    def task_failed(self, task_id: int, reason: str, attempts: int):
        self.record("task", "fail", {"id": task_id, "reason": reason[:100], "attempts": attempts})

    def task_timeout(self, task_id: int, timeout_secs: int):
        self.record("task", "timeout", {"id": task_id, "timeout": timeout_secs})

    def task_rework(self, task_id: int):
        self.record("task", "rework", {"id": task_id})

    def review_decision(self, supervisor: str, decision: str, auto: bool):
        self.record("review", decision, {"sup": supervisor, "auto": auto})

    def supervisor_switched(self, from_sup: str, to_sup: str):
        self.record("review", "switch", {"from": from_sup, "to": to_sup})

    def page_viewed(self, page: str):
        self.record("ui", "page", {"page": page})

    def feature_used(self, feature: str):
        self.record("feature", "use", {"feat": feature})

    def error_occurred(self, error: str, source: str):
        self.record("error", "crash", {"err": error[:200], "src": source})

    # ── Export ────────────────────────────────────────────────────────

    def build_report(self) -> dict:
        """Build the telemetry report for AI consumption."""
        # Aggregate stats
        cats = {}
        errors = []
        task_types = {}
        supervisors = {}
        timeouts = 0
        reworks = 0
        runs_started = 0
        runs_completed = 0
        runs_failed = 0
        total_elapsed = 0

        for e in self._events:
            cat = e.get("cat", "?")
            act = e.get("act", "?")
            d = e.get("d", {})
            cats[cat] = cats.get(cat, 0) + 1

            if cat == "run" and act == "start":
                runs_started += 1
                sup = d.get("sup", "?")
                supervisors[sup] = supervisors.get(sup, 0) + 1
            elif cat == "run" and act == "complete":
                runs_completed += 1
                total_elapsed += d.get("elapsed", 0)
            elif cat == "run" and act == "fail":
                runs_failed += 1
                errors.append(d.get("err", "?"))
            elif cat == "task" and act == "pass":
                tt = d.get("type", "?")
                task_types[tt] = task_types.get(tt, 0) + 1
            elif cat == "task" and act == "timeout":
                timeouts += 1
            elif cat == "task" and act == "rework":
                reworks += 1
            elif cat == "error":
                errors.append(d.get("err", "?"))

        # Top errors (deduplicated)
        error_counts = {}
        for e in errors:
            key = e[:80]
            error_counts[key] = error_counts.get(key, 0) + 1
        top_errors = sorted(error_counts.items(), key=lambda x: -x[1])[:10]

        return {
            "instance_id": self._instance_id,
            "session_id": self._session_id,
            "session_start": self._session_start,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "event_count": len(self._events),
            "summary": {
                "runs_started": runs_started,
                "runs_completed": runs_completed,
                "runs_failed": runs_failed,
                "success_rate": round(runs_completed / max(1, runs_started) * 100, 1),
                "total_elapsed_seconds": total_elapsed,
                "timeouts": timeouts,
                "reworks": reworks,
                "task_types_used": task_types,
                "supervisors_used": supervisors,
                "event_categories": cats,
            },
            "top_errors": [{"error": e, "count": c} for e, c in top_errors],
            "events": self._events,
            "ai_prompt": (
                "You are analyzing telemetry data from a bridge app that connects "
                "AI supervisors (Claude/Codex) with local LLMs (Aider/Ollama). "
                "Based on the events and summary above, identify:\n"
                "1. TOP PAIN POINTS: Where do users get stuck or fail most?\n"
                "2. UX FRICTION: What flows are abandoned or retried?\n"
                "3. FEATURE GAPS: What are users trying to do that the app doesn't support?\n"
                "4. BUGS: What error patterns suggest code bugs vs user mistakes?\n"
                "5. PRIORITIES: Rank the top 5 improvements by impact.\n"
            ),
        }

    def save(self, repo_root: Optional[Path] = None) -> Path:
        """Save telemetry to disk. Returns the file path."""
        report = self.build_report()
        if repo_root:
            path = _telemetry_path(repo_root)
        else:
            path = _global_telemetry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return path


# Module-level singleton
_collector: Optional[TelemetryCollector] = None


def get_collector() -> TelemetryCollector:
    global _collector
    if _collector is None:
        _collector = TelemetryCollector()
    return _collector
