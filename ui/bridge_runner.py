from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from . import state_store

BRIDGE_ROOT = Path(__file__).parent.parent

# When running as a PyInstaller bundle, sys.executable is the .exe itself,
# not the Python interpreter.  We detect this once at import time.
_FROZEN = getattr(sys, "frozen", False)

# Capture the real working directory at import time — before PyInstaller can
# change it.  Used as a safe fallback cwd for the bridge subprocess when the
# user has not yet configured a repo root.
_STARTUP_CWD: str = os.getcwd()

# On Windows, prevent the bridge subprocess from opening a visible CMD window.
_WIN_NO_WINDOW: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class BridgeRun:
    """Manages one bridge subprocess, parses its log output into structured
    task events, and broadcasts those events to registered SSE listeners."""

    def __init__(self) -> None:
        self._process: Optional[subprocess.Popen] = None  # type: ignore[type-arg]
        self._lock = threading.Lock()
        self._listeners: list[Callable[[str, dict], None]] = []
        self.is_running = False
        self.tasks: dict[int, dict] = {}
        self.log_lines: list[str] = []
        self.status = "idle"   # idle | running | success | failure | stopped
        self.run_id: Optional[str] = None
        self.command_preview: str = ""
        self.total_tasks: int = 0
        self.completed_tasks: int = 0
        self.token_data: Optional[dict] = None
        self.repo_root: str = ""
        self.driver: str = ""

    # ── Listener management ────────────────────────────────────────────────

    def add_listener(self, fn: Callable[[str, dict], None]) -> None:
        self._listeners.append(fn)

    def remove_listener(self, fn: Callable[[str, dict], None]) -> None:
        try:
            self._listeners.remove(fn)
        except ValueError:
            pass

    def _emit(self, event_type: str, data: dict) -> None:
        for fn in list(self._listeners):
            try:
                fn(event_type, data)
            except Exception:
                pass

    # ── Command builder ────────────────────────────────────────────────────

    def build_command(self, settings: dict) -> list[str]:
        if _FROZEN:
            # Re-invoke the bundled exe in bridge-subprocess mode.
            # launch_ui.py strips --_bridge-run and calls main.main().
            cmd = [sys.executable, "--_bridge-run"]
        else:
            cmd = [sys.executable, str(BRIDGE_ROOT / "main.py")]

        goal = settings.get("goal", "").strip()
        if goal:
            cmd.append(goal)

        if settings.get("repo_root", "").strip():
            cmd.extend(["--repo-root", settings["repo_root"].strip()])
        if settings.get("relay_session_id", "").strip():
            cmd.extend(["--relay-session-id", settings["relay_session_id"].strip()])
        if settings.get("idea_file", "").strip():
            cmd.extend(["--idea-file", settings["idea_file"].strip()])
        if settings.get("aider_model", "").strip():
            cmd.extend(["--aider-model", settings["aider_model"].strip()])
        # Universal pipeline: ALL runs use --manual-supervisor.
        # The SupervisorProxyThread in app.py handles CLI dispatch.
        cmd.append("--manual-supervisor")

        supervisor = settings.get("supervisor", "")
        # For chatbot/ai_relay, write the pre-imported tasks to a temporary plan file
        if supervisor in ("ai_relay", "chatbot") and not settings.get("plan_file", "").strip():
            tasks = [
                task for task in state_store.load_relay_tasks()
                if str(task.get("status", "")).strip().lower() != "skipped"
            ]
            if tasks:
                plan_path = state_store.DATA_DIR / "relay_active_plan.json"
                plan_path.write_text(
                    json.dumps({"tasks": tasks}, indent=2), encoding="utf-8"
                )
                if "--plan-file" not in cmd:
                    cmd.extend(["--plan-file", str(plan_path)])
        if settings.get("manual_review_poll_seconds"):
            cmd.extend(["--manual-review-poll-seconds", str(int(settings["manual_review_poll_seconds"]))])
        if settings.get("validation_command", "").strip():
            cmd.extend(["--validation-command", settings["validation_command"].strip()])
        if settings.get("max_plan_attempts"):
            cmd.extend(["--max-plan-attempts", str(settings["max_plan_attempts"])])
        if settings.get("max_task_retries"):
            cmd.extend(["--max-task-retries", str(settings["max_task_retries"])])
        if settings.get("plan_output_file", "").strip():
            cmd.extend(["--plan-output-file", settings["plan_output_file"].strip()])
        if settings.get("plan_file", "").strip():
            cmd.extend(["--plan-file", settings["plan_file"].strip()])
        if settings.get("dry_run"):
            cmd.append("--dry-run")
        if not settings.get("auto_commit", True):
            cmd.append("--no-auto-commit")
        if settings.get("task_timeout"):
            cmd.extend(["--task-timeout", str(int(settings["task_timeout"]))])
        for c in settings.get("clarifications", []):
            if str(c).strip():
                cmd.extend(["--clarification", str(c).strip()])

        cmd.extend(["--log-level", "INFO"])
        return cmd

    # ── Run lifecycle ──────────────────────────────────────────────────────

    def start(self, settings: dict, run_id: str) -> None:
        with self._lock:
            if self.is_running:
                raise RuntimeError("A run is already in progress.")
            self.is_running = True
            self.tasks = {}
            self.log_lines = []
            self.status = "running"
            self.run_id = run_id
            self.total_tasks = 0
            self.completed_tasks = 0
            self.token_data = None
            self.repo_root = str(settings.get("repo_root", "")).strip()
            self.driver = str(settings.get("supervisor", "")).strip()
            cmd = self.build_command(settings)
            self.command_preview = " ".join(cmd)
            # Determine a safe working directory for the subprocess.
            # When frozen, BRIDGE_ROOT is the PyInstaller temp extraction dir —
            # useless as a cwd.  Prefer the configured repo root; fall back to
            # the directory the exe was launched from.
            _repo = settings.get("repo_root", "").strip()
            if _FROZEN:
                subprocess_cwd = _repo or _STARTUP_CWD or str(Path.home())
            else:
                subprocess_cwd = str(BRIDGE_ROOT)
            thread = threading.Thread(
                target=self._run_process, args=(cmd, subprocess_cwd), daemon=True
            )
            thread.start()

        self._emit("start", {
            "command": self.command_preview,
            "run_id": run_id,
            "repo_root": self.repo_root,
            "driver": self.driver,
        })

    def _run_process(self, cmd: list[str], subprocess_cwd: str) -> None:
        start_time = time.time()
        final_json: Optional[dict] = None

        try:
            self._process = subprocess.Popen(
                cmd,
                cwd=subprocess_cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=_WIN_NO_WINDOW,
            )

            for raw_line in self._process.stdout:  # type: ignore[union-attr]
                line = raw_line.rstrip("\n\r")
                self.log_lines.append(line)
                self._emit("log", {"line": line})

                # Capture final JSON summary line
                stripped = line.strip()
                if stripped.startswith("{") and '"status"' in stripped:
                    try:
                        final_json = json.loads(stripped)
                    except Exception:
                        pass

                self._parse_log_line(line)

            self._process.wait()
            elapsed = round(time.time() - start_time, 1)
            exit_code = self._process.returncode

            if final_json:
                status = final_json.get("status", "failure")
            else:
                status = "success" if exit_code == 0 else "failure"

            self.status = status
            self._emit("complete", {
                "status": status,
                "exit_code": exit_code,
                "elapsed": elapsed,
                "final": final_json,
                "run_id": self.run_id,
                "task_count": len(self.tasks),
            })

        except Exception as ex:
            self.status = "failure"
            self._emit("error", {"message": str(ex)})

        finally:
            with self._lock:
                self.is_running = False
                self._process = None

    # ── Log parser — extracts structured task events from INFO lines ───────

    def _parse_log_line(self, line: str) -> None:
        # ── Structured JSON events emitted by main._emit_structured() ────────
        stripped_line = line.strip()
        if stripped_line.startswith('{"_bridge_event"'):
            try:
                event = json.loads(stripped_line)
                event_type = event.get("type", "")
                if event_type == "task_complete":
                    task_id = int(event.get("task_id", 0))
                    diff = event.get("diff", "")
                    if task_id in self.tasks:
                        self.tasks[task_id]["diff"] = diff
                    self._emit("task_diff", {"task_id": task_id, "diff": diff})
                elif event_type == "paused":
                    self.status = "paused"
                    self._emit("paused", {"pause_file": event.get("pause_file", "")})
                elif event_type == "resumed":
                    self.status = "running"
                    self._emit("resumed", {})
                elif event_type == "token_report":
                    report = event.get("report", {})
                    self.token_data = {"source": "live", "session": report}
                    self._emit("token_report", {"report": report})
                elif event_type == "review_required":
                    self.status = "waiting_review"
                    payload = {
                        "task_id": event.get("task_id", 0),
                        "request_file": event.get("request_file", ""),
                        "validation_message": event.get("validation_message", ""),
                        "mode": event.get("mode", "manual"),
                    }
                    # Universal pipeline: proxy thread handles dispatch
                    self._emit("review_required", payload)
            except Exception:
                pass
            return

        # Strip the log prefix: "YYYY-MM-DD HH:MM:SS | LEVEL | name | <message>"
        msg_match = re.search(r"\|\s*bridge_app\s*\|\s*(.+)$", line)
        msg = msg_match.group(1).strip() if msg_match else line.strip()

        # ── Task attempt start ────────────────────────────────────────────
        # "Task 1 — attempt 1/3 — files: src/login.py, src/utils.py"
        m = re.search(r"Task\s+(\d+)\D+attempt\s+(\d+)/(\d+)\D+files:\s*(.+)", msg)
        if m:
            task_id = int(m.group(1))
            attempt = int(m.group(2))
            files = [f.strip() for f in m.group(4).split(",")]
            if task_id not in self.tasks:
                self.tasks[task_id] = {
                    "id": task_id,
                    "files": files,
                    "status": "running",
                    "attempt": attempt,
                    "reworks": [],
                }
            else:
                self.tasks[task_id]["status"] = "running"
                self.tasks[task_id]["attempt"] = attempt
                self.tasks[task_id]["files"] = files
            self._emit("task_update", {"task": dict(self.tasks[task_id])})
            return

        # ── Supervisor approved ───────────────────────────────────────────
        # "Task 1: supervisor approved"
        m = re.search(r"Task\s+(\d+).*supervisor approved", msg)
        if m:
            task_id = int(m.group(1))
            if task_id in self.tasks:
                self.tasks[task_id]["status"] = "approved"
                self._emit("task_update", {"task": dict(self.tasks[task_id])})
            self.completed_tasks += 1
            if self.total_tasks > 0:
                pct = round(self.completed_tasks / self.total_tasks * 100)
                self._emit("progress", {
                    "completed": self.completed_tasks,
                    "total": self.total_tasks,
                    "percent": pct,
                })
            return

        # ── Supervisor REWORK ─────────────────────────────────────────────
        # "Task 1 — supervisor requested rework (attempt 1): Add validation"
        m = re.search(r"Task\s+(\d+).+supervisor requested rework[^:]*:\s*(.+)", msg)
        if m:
            task_id = int(m.group(1))
            new_instr = m.group(2).strip()
            if task_id in self.tasks:
                self.tasks[task_id]["status"] = "rework"
                self.tasks[task_id].setdefault("reworks", []).append(new_instr)
                self._emit("task_update", {"task": dict(self.tasks[task_id])})
            return

        # ── Mechanical check failure ──────────────────────────────────────
        m = re.search(r"Task\s+(\d+).*mechanical check failed", msg)
        if m:
            task_id = int(m.group(1))
            if task_id in self.tasks:
                self.tasks[task_id]["status"] = "retrying"
                self._emit("task_update", {"task": dict(self.tasks[task_id])})
            return

        # ── Dry-run task ──────────────────────────────────────────────────
        # "[dry-run] Task 1: Create a logging module..."
        m = re.search(r"\[dry-run\]\s+Task\s+(\d+):\s*(.+)", msg)
        if m:
            task_id = int(m.group(1))
            instruction = m.group(2).strip()
            if task_id not in self.tasks:
                self.tasks[task_id] = {
                    "id": task_id,
                    "files": [],
                    "status": "dry-run",
                    "attempt": 1,
                    "reworks": [],
                    "instruction": instruction,
                }
            else:
                self.tasks[task_id]["status"] = "dry-run"
                self.tasks[task_id]["instruction"] = instruction
            self._emit("task_update", {"task": dict(self.tasks[task_id])})
            return

        # ── Plan ready ────────────────────────────────────────────────────
        m = re.search(r"(?:Supervisor produced|Loaded) (\d+) task", msg)
        if m:
            self.total_tasks = int(m.group(1))
            self._emit("plan_ready", {"task_count": self.total_tasks})
            self._emit("planner_done", {})
            self._emit("progress", {"completed": 0, "total": self.total_tasks, "percent": 0})
            return

        # ── Bridge started ────────────────────────────────────────────────
        m = re.search(r"Bridge starting", msg)
        if m:
            self._emit("bridge_started", {})
            self._emit("planner_active", {"task_count": "?"})
            return

        # ── Failure logged ────────────────────────────────────────────────
        if re.search(r"Bridge run failed", msg):
            self._emit("bridge_failed", {"message": msg})

    # ── Stop ───────────────────────────────────────────────────────────────

    def stop(self) -> None:
        with self._lock:
            proc = self._process
        if proc:
            try:
                proc.terminate()
            except Exception:
                pass
            # Escalate: wait 5s then force kill if still running
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
        with self._lock:
            self.is_running = False
            self.status = "stopped"
        self._emit("stopped", {})

    def send_input(self, text: str) -> bool:
        """Write a line of text to the running subprocess stdin. Returns True if sent."""
        with self._lock:
            proc = self._process
        if proc and proc.stdin:
            try:
                proc.stdin.write(text + "\n")
                proc.stdin.flush()
                self._emit("log", {"line": f"[user input] {text}"})
                return True
            except Exception:
                pass
        return False


# Module-level singleton — one run at a time (local tool)
_run = BridgeRun()


def get_run() -> BridgeRun:
    return _run


# Kill orphaned subprocess on Flask shutdown
import atexit

def _cleanup():
    if _run._process and _run._process.poll() is None:
        try:
            _run._process.kill()
        except Exception:
            pass

atexit.register(_cleanup)
