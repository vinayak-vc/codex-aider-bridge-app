"""Run Diagnostics — structured failure analysis for AI supervisors.

Accumulates per-task events during a bridge run and writes a single
RUN_DIAGNOSTICS.json that an AI supervisor can read to understand:
  - What happened (timeline of every attempt)
  - Where Aider or the bridge failed/blocked (with stdout excerpts)
  - Detected blocking patterns (timeouts, interactive prompts, loops)
  - Actionable suggestions for the next plan
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional


# Stdout lines to keep per attempt (last N lines)
_STDOUT_TAIL_LINES: int = 30
_STDERR_TAIL_LINES: int = 10


def _tail(text: str, max_lines: int) -> str:
    """Return the last max_lines lines of text."""
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[-max_lines:])


def _classify_aider_failure(exit_code: int, stdout: str, stderr: str) -> tuple[str, str]:
    """Classify an Aider failure into a named reason + detail string."""
    combined = (stdout + "\n" + stderr).lower()

    if exit_code == -1 and "timed out" in combined:
        return "timeout", "Aider process timed out"

    if exit_code == -1:
        return "could_not_start", stderr.strip()[:200] or "Aider binary not found or failed to launch"

    # Interactive prompt patterns (from aider_runner.py)
    prompt_patterns = [
        r"add.*file.*to the chat",
        r"\(y\)es/\(n\)o",
        r"\[y/n\]",
        r"attempt to fix lint",
        r"open docs url",
    ]
    for pat in prompt_patterns:
        m = re.search(pat, combined)
        if m:
            return "interactive_prompt", f"Aider asked: '{m.group(0).strip()}'"

    if "did not modify" in combined or "only changed whitespace" in combined:
        return "silent_failure", "Aider exited 0 but made no meaningful changes"

    if exit_code != 0:
        # Try to extract a meaningful error from stderr
        err_lines = [l for l in stderr.splitlines() if l.strip()]
        detail = err_lines[-1].strip()[:200] if err_lines else f"exit code {exit_code}"
        return "aider_error", detail

    return "unknown", ""


class RunDiagnostics:
    """Accumulates diagnostic events during one bridge run."""

    def __init__(self, goal: str, aider_model: str, supervisor: str, task_timeout: int = 300):
        self._goal = goal
        self._aider_model = aider_model
        self._supervisor = supervisor
        self._task_timeout = task_timeout
        self._start_time = time.time()
        self._tasks: dict[int, dict] = {}  # task_id -> task diagnostic record

    def _ensure_task(self, task_id: int, instruction: str = "", files: list = None, task_type: str = "") -> dict:
        if task_id not in self._tasks:
            self._tasks[task_id] = {
                "task_id": task_id,
                "instruction": instruction,
                "files": files or [],
                "type": task_type,
                "attempts": [],
                "outcome": "pending",
                "total_duration_seconds": 0.0,
            }
        t = self._tasks[task_id]
        if instruction and not t["instruction"]:
            t["instruction"] = instruction
        if files and not t["files"]:
            t["files"] = files
        if task_type and not t["type"]:
            t["type"] = task_type
        return t

    def _ensure_attempt(self, task_id: int, attempt: int) -> dict:
        t = self._ensure_task(task_id)
        # Find or create the attempt record
        for a in t["attempts"]:
            if a["attempt"] == attempt:
                return a
        record = {
            "attempt": attempt,
            "aider": None,
            "validation": None,
            "review": None,
        }
        t["attempts"].append(record)
        return record

    # ── Recording methods ─────────────────────────────────────────────

    def record_task_start(self, task_id: int, instruction: str, files: list, task_type: str) -> None:
        self._ensure_task(task_id, instruction, files, task_type)

    def record_aider_result(
        self,
        task_id: int,
        attempt: int,
        exit_code: int,
        succeeded: bool,
        stdout: str,
        stderr: str,
        duration_seconds: float,
        command: list = None,
    ) -> None:
        rec = self._ensure_attempt(task_id, attempt)
        reason, detail = ("", "")
        if not succeeded:
            reason, detail = _classify_aider_failure(exit_code, stdout, stderr)

        rec["aider"] = {
            "exit_code": exit_code,
            "succeeded": succeeded,
            "duration_seconds": round(duration_seconds, 2),
            "stdout_tail": _tail(stdout, _STDOUT_TAIL_LINES),
            "stderr_tail": _tail(stderr, _STDERR_TAIL_LINES),
            "failure_reason": reason,
            "failure_detail": detail,
        }

        t = self._tasks[task_id]
        t["total_duration_seconds"] = round(
            t["total_duration_seconds"] + duration_seconds, 2
        )

    def record_validation(
        self, task_id: int, attempt: int, succeeded: bool, message: str
    ) -> None:
        rec = self._ensure_attempt(task_id, attempt)
        rec["validation"] = {
            "succeeded": succeeded,
            "message": message[:500] if message else "",
        }

    def record_review(
        self, task_id: int, attempt: int, decision: str, instruction: str = ""
    ) -> None:
        rec = self._ensure_attempt(task_id, attempt)
        rec["review"] = {
            "decision": decision,
            "instruction": instruction[:300] if instruction else "",
        }
        if decision == "pass":
            self._tasks[task_id]["outcome"] = "passed"
        elif decision == "rework":
            self._tasks[task_id]["outcome"] = "rework"

    def record_escalation(self, task_id: int, escalation_log: list[dict]) -> None:
        """Record the escalation history for a task."""
        t = self._ensure_task(task_id)
        t["escalation_log"] = escalation_log

    def record_task_failure(self, task_id: int, reason: str) -> None:
        t = self._ensure_task(task_id)
        t["outcome"] = "failed"
        if not t["attempts"]:
            self._ensure_attempt(task_id, 1)

    def record_task_skipped(self, task_id: int) -> None:
        t = self._ensure_task(task_id)
        t["outcome"] = "skipped"

    # ── Finalize and write ────────────────────────────────────────────

    def finalize(
        self,
        status: str,
        completed_task_ids: list[int],
        failed_task_id: Optional[int],
        error_message: str = "",
        total_tasks: int = 0,
    ) -> dict:
        """Build the final diagnostics report dict."""
        elapsed = round(time.time() - self._start_time, 1)

        # Mark completed tasks
        for tid in completed_task_ids:
            if tid in self._tasks:
                self._tasks[tid]["outcome"] = "passed"

        # Mark failed task
        if failed_task_id and failed_task_id in self._tasks:
            self._tasks[failed_task_id]["outcome"] = "failed"

        tasks_list = sorted(self._tasks.values(), key=lambda t: t["task_id"])
        patterns = self._detect_patterns(tasks_list)
        summary = self._build_summary(status, tasks_list, patterns, failed_task_id, error_message)

        return {
            "run_id": datetime.now().strftime("%Y%m%d_%H%M%S"),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "goal": self._goal,
            "supervisor": self._supervisor,
            "aider_model": self._aider_model,
            "status": status,
            "total_tasks": total_tasks or len(tasks_list),
            "completed_tasks": len(completed_task_ids),
            "failed_task_id": failed_task_id,
            "error_message": error_message[:500] if error_message else "",
            "elapsed_seconds": elapsed,
            "tasks": tasks_list,
            "blocking_patterns": patterns,
            "ai_summary": summary,
        }

    def write(self, output_path: Path, report: dict = None) -> None:
        """Write the diagnostics JSON file."""
        if report is None:
            report = self.finalize("unknown", [], None)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    # ── Pattern detection ─────────────────────────────────────────────

    def _detect_patterns(self, tasks: list[dict]) -> list[dict]:
        patterns = []

        # 1. Interactive prompt detection
        prompt_tasks = []
        for t in tasks:
            for a in t.get("attempts", []):
                aider = a.get("aider") or {}
                if aider.get("failure_reason") == "interactive_prompt":
                    prompt_tasks.append(t["task_id"])
                    break
        if prompt_tasks:
            patterns.append({
                "pattern": "interactive_prompt",
                "count": len(prompt_tasks),
                "tasks": prompt_tasks,
                "suggestion": (
                    "Tasks are too broad for the model. Aider is asking to add files "
                    "to the chat — this means the instruction references files not in "
                    "the task's file list. Fix: add referenced files to context_files, "
                    "or split into single-file tasks with --auto-split-threshold 2."
                ),
            })

        # 2. Timeout detection
        timeout_tasks = []
        for t in tasks:
            for a in t.get("attempts", []):
                aider = a.get("aider") or {}
                if aider.get("failure_reason") == "timeout":
                    timeout_tasks.append(t["task_id"])
                    break
        if timeout_tasks:
            patterns.append({
                "pattern": "timeout",
                "count": len(timeout_tasks),
                "tasks": timeout_tasks,
                "suggestion": (
                    "Aider timed out on these tasks. The task scope may be too large "
                    "for the model, or the model is too slow. Try: increase "
                    "--task-timeout, use a faster model, or split large tasks."
                ),
            })

        # 3. Silent failure (exit 0 but no changes)
        silent_tasks = []
        for t in tasks:
            for a in t.get("attempts", []):
                aider = a.get("aider") or {}
                if aider.get("failure_reason") == "silent_failure":
                    silent_tasks.append(t["task_id"])
                    break
        if silent_tasks:
            patterns.append({
                "pattern": "silent_failure",
                "count": len(silent_tasks),
                "tasks": silent_tasks,
                "suggestion": (
                    "Aider reported success but made no meaningful changes. The "
                    "instruction may be too vague. Make instructions more specific "
                    "with exact method names, class names, and expected behavior."
                ),
            })

        # 4. Repeated validation failure (same task fails 3+ times)
        for t in tasks:
            val_failures = sum(
                1 for a in t.get("attempts", [])
                if (a.get("validation") or {}).get("succeeded") is False
            )
            if val_failures >= 3:
                patterns.append({
                    "pattern": "repeated_validation_failure",
                    "count": val_failures,
                    "tasks": [t["task_id"]],
                    "suggestion": (
                        f"Task {t['task_id']} failed validation {val_failures} times. "
                        "The instruction may be ambiguous or the model lacks capability. "
                        "Rewrite the instruction with exact expected output, or try a "
                        "larger model."
                    ),
                })

        # 5. Supervisor rework loop (same task gets REWORK 2+ times)
        for t in tasks:
            reworks = sum(
                1 for a in t.get("attempts", [])
                if (a.get("review") or {}).get("decision") == "rework"
            )
            if reworks >= 2:
                patterns.append({
                    "pattern": "supervisor_rework_loop",
                    "count": reworks,
                    "tasks": [t["task_id"]],
                    "suggestion": (
                        f"Task {t['task_id']} was sent back for rework {reworks} times. "
                        "The original instruction and the reviewer's expectations may "
                        "conflict. Clarify the acceptance criteria in the instruction."
                    ),
                })

        # 6. Model capability gap (syntax errors in output)
        syntax_tasks = []
        for t in tasks:
            for a in t.get("attempts", []):
                val = a.get("validation") or {}
                msg = (val.get("message") or "").lower()
                if not val.get("succeeded") and (
                    "syntax" in msg or "compile" in msg or "parse error" in msg
                    or "brace" in msg or "artifact" in msg
                ):
                    syntax_tasks.append(t["task_id"])
                    break
        if syntax_tasks:
            patterns.append({
                "pattern": "model_capability_gap",
                "count": len(syntax_tasks),
                "tasks": syntax_tasks,
                "suggestion": (
                    "The model produced syntactically invalid code. Try a larger or "
                    "more capable model (e.g., qwen2.5-coder:14b instead of 7b), "
                    "or simplify the task instruction."
                ),
            })

        # 7. Missing dependency (import errors)
        dep_tasks = []
        for t in tasks:
            for a in t.get("attempts", []):
                val = a.get("validation") or {}
                msg = (val.get("message") or "").lower()
                if not val.get("succeeded") and (
                    "import" in msg or "require" in msg or "module not found" in msg
                    or "no module" in msg or "cannot find" in msg
                ):
                    dep_tasks.append(t["task_id"])
                    break
        if dep_tasks:
            patterns.append({
                "pattern": "missing_dependency",
                "count": len(dep_tasks),
                "tasks": dep_tasks,
                "suggestion": (
                    "Validation failed due to missing imports or dependencies. "
                    "Add a task before these to install/create the required "
                    "dependency, or add the dependency file to context_files."
                ),
            })

        return patterns

    # ── Summary builder ───────────────────────────────────────────────

    def _build_summary(
        self,
        status: str,
        tasks: list[dict],
        patterns: list[dict],
        failed_task_id: Optional[int],
        error_message: str,
    ) -> str:
        """Build a plain-English summary for AI consumption."""
        passed = sum(1 for t in tasks if t["outcome"] == "passed")
        failed = sum(1 for t in tasks if t["outcome"] == "failed")
        total = len(tasks)

        parts = []

        if status == "success":
            parts.append(f"Run completed successfully. {passed}/{total} tasks passed.")
        else:
            parts.append(f"Run failed. {passed}/{total} tasks completed before failure.")
            if failed_task_id:
                ft = next((t for t in tasks if t["task_id"] == failed_task_id), None)
                if ft:
                    attempts = len(ft.get("attempts", []))
                    parts.append(f"Failed at task {failed_task_id} after {attempts} attempt(s).")

                    # Find the dominant failure reason
                    reasons = []
                    for a in ft.get("attempts", []):
                        aider = a.get("aider") or {}
                        if aider.get("failure_reason"):
                            reasons.append(aider["failure_reason"])
                        val = a.get("validation") or {}
                        if not val.get("succeeded") and val.get("message"):
                            reasons.append("validation: " + val["message"][:80])

                    if reasons:
                        parts.append(f"Failure reasons: {'; '.join(reasons)}.")

            if error_message:
                parts.append(f"Error: {error_message[:200]}.")

        if patterns:
            pattern_names = [p["pattern"] for p in patterns]
            parts.append(f"Blocking patterns detected: {', '.join(pattern_names)}.")
            # Include the top suggestion
            parts.append(f"Top suggestion: {patterns[0]['suggestion']}")

        return " ".join(parts)
