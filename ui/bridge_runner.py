from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

BRIDGE_ROOT = Path(__file__).parent.parent

# When running as a PyInstaller bundle, sys.executable is the .exe itself,
# not the Python interpreter.  We detect this once at import time.
_FROZEN = getattr(sys, "frozen", False)


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
        if settings.get("idea_file", "").strip():
            cmd.extend(["--idea-file", settings["idea_file"].strip()])
        if settings.get("aider_model", "").strip():
            cmd.extend(["--aider-model", settings["aider_model"].strip()])
        if settings.get("supervisor_command", "").strip():
            cmd.extend(["--supervisor-command", settings["supervisor_command"].strip()])
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
            cmd = self.build_command(settings)
            self.command_preview = " ".join(cmd)
            thread = threading.Thread(
                target=self._run_process, args=(cmd,), daemon=True
            )
            thread.start()

        self._emit("start", {"command": self.command_preview, "run_id": run_id})

    def _run_process(self, cmd: list[str]) -> None:
        start_time = time.time()
        final_json: Optional[dict] = None

        try:
            self._process = subprocess.Popen(
                cmd,
                cwd=str(BRIDGE_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
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
        m = re.search(r"Supervisor produced (\d+) task", msg)
        if m:
            self._emit("plan_ready", {"task_count": int(m.group(1))})
            return

        # ── Bridge started ────────────────────────────────────────────────
        m = re.search(r"Bridge starting", msg)
        if m:
            self._emit("bridge_started", {})
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
        with self._lock:
            self.is_running = False
            self.status = "stopped"
        self._emit("stopped", {})


# Module-level singleton — one run at a time (local tool)
_run = BridgeRun()


def get_run() -> BridgeRun:
    return _run
