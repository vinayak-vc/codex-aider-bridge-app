from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from . import setup_checker, state_store
from .bridge_runner import get_run

# Telemetry — local-only usage analytics
try:
    _root_path = Path(__file__).parent.parent
    if str(_root_path) not in sys.path:
        sys.path.insert(0, str(_root_path))
    from utils.telemetry import get_collector as _get_telemetry
except ImportError:
    _get_telemetry = None

# On Windows, prevent subprocess calls from opening visible CMD windows.
_WIN_CREATE_FLAGS: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# When bundled by PyInstaller, Flask cannot locate templates via __file__.
# Point it explicitly at the extracted bundle path.
# NOTE: passing template_folder=None disables the loader entirely; use the
# string "templates" (Flask's default) when running from source.
_frozen = getattr(sys, "frozen", False)
_template_folder = (
    str(Path(sys._MEIPASS) / "ui" / "templates")  # type: ignore[attr-defined]
    if _frozen
    else "templates"  # relative to this file's directory (ui/templates/)
)

_static_folder = (
    str(Path(sys._MEIPASS) / "ui" / "static")  # type: ignore[attr-defined]
    if _frozen
    else "static"
)

app = Flask(__name__, template_folder=_template_folder, static_folder=_static_folder)
app.config["JSON_SORT_KEYS"] = False

# Register blueprints — extracted API route groups
from ui.api.git_routes import git_bp
from ui.api.system_routes import system_bp
from ui.api.firebase_routes import firebase_bp
from ui.api.chat_routes import chat_bp
from ui.api.relay_routes import relay_bp
app.register_blueprint(git_bp)
app.register_blueprint(system_bp)
app.register_blueprint(firebase_bp)
app.register_blueprint(chat_bp)
app.register_blueprint(relay_bp)

# SSE + shared state from app_state.py
from ui.app_state import broadcast as _broadcast, build_chat_context as _build_chat_context
from ui.app_state import _sse_clients, _sse_lock

# Knowledge context cache: {repo_root: (context_str, timestamp)}
_knowledge_cache: dict[str, tuple[str, float]] = {}
_KNOWLEDGE_CACHE_TTL = 60.0  # seconds


def _chat_project_key(repo_root: str) -> str:
    return str(repo_root or "").strip()


# Relay helpers — MOVED to ui/api/relay_routes.py

# Chat runtime + helpers — MOVED to ui/api/chat_routes.py

# ── Pages ──────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    from flask import redirect
    return redirect("/dashboard")


# ── Telemetry: page view tracking ─────────────────────────────────────────────

_TRACKED_PAGES = {"/dashboard", "/run", "/knowledge", "/history", "/tokens", "/git", "/setup"}

@app.after_request
def _track_page_view(response):
    if _get_telemetry and request.path in _TRACKED_PAGES and response.status_code == 200:
        _get_telemetry().page_viewed(request.path)
    return response


# ── Page routes ────────────────────────────────────────────────────────────────

@app.route("/dashboard")
def page_dashboard():
    return render_template("dashboard.html", active_page="dashboard")


@app.route("/run")
def page_run():
    return render_template("run.html", active_page="run")


@app.route("/cloud")
def page_cloud():
    return render_template("cloud.html", active_page="cloud")


@app.route("/git")
def page_git():
    return render_template("git.html", active_page="git")


@app.route("/knowledge")
def page_knowledge():
    return render_template("knowledge.html", active_page="knowledge")


@app.route("/history")
def page_history():
    return render_template("history.html", active_page="history")


@app.route("/tokens")
def page_tokens():
    return render_template("tokens.html", active_page="tokens")


@app.route("/setup")
def page_setup():
    return render_template("setup.html", active_page="setup")


# ── Setup checks ───────────────────────────────────────────────────────────────

@app.route("/api/check")
def api_check():
    return jsonify(setup_checker.check_all())


@app.route("/api/ollama/models")
def api_ollama_models():
    info = setup_checker.check_ollama()
    return jsonify(info.get("models", []))


@app.route("/api/install/aider", methods=["POST"])
def api_install_aider():
    """Stream pip install aider-chat output as SSE."""
    def generate():
        proc = subprocess.Popen(
            [sys.executable, "-m", "pip", "install", "--upgrade", "aider-chat"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=_WIN_CREATE_FLAGS,
        )
        for line in proc.stdout:  # type: ignore[union-attr]
            yield f"data: {json.dumps({'line': line.rstrip()})}\n\n"
        proc.wait()
        done_status = "success" if proc.returncode == 0 else "failure"
        yield f"data: {json.dumps({'done': True, 'status': done_status})}\n\n"

    return Response(stream_with_context(generate()), content_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/ollama/pull", methods=["POST"])
def api_ollama_pull():
    """Stream ollama pull <model> output as SSE."""
    model = (request.json or {}).get("model", "mistral")

    def generate():
        proc = subprocess.Popen(
            ["ollama", "pull", model],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=_WIN_CREATE_FLAGS,
        )
        for line in proc.stdout:  # type: ignore[union-attr]
            yield f"data: {json.dumps({'line': line.rstrip()})}\n\n"
        proc.wait()
        done_status = "success" if proc.returncode == 0 else "failure"
        yield f"data: {json.dumps({'done': True, 'status': done_status})}\n\n"

    return Response(stream_with_context(generate()), content_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Settings ────────────────────────────────────────────────────────────────────

@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    return jsonify(state_store.load_settings())


@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    settings = request.json or {}
    state_store.save_settings(settings)
    # Firebase sync: push settings to user's Firestore
    try:
        from utils.firebase_user_setup import get_user_setup
        _fbu = get_user_setup()
        if _fbu.is_configured() and _fbu.is_authenticated():
            _safe_settings = {
                "default_model": settings.get("aider_model", ""),
                "default_supervisor": settings.get("supervisor", ""),
                "auto_commit": settings.get("auto_commit", True),
                "task_timeout": settings.get("task_timeout", 600),
            }
            _fbu.write_to_user_firestore("settings/global", _safe_settings)
    except Exception:
        pass
    return jsonify({"ok": True})


# ── Native folder / file picker (opens OS dialog) ──────────────────────────────

@app.route("/api/browse/folder")
def api_browse_folder():
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        folder = filedialog.askdirectory(parent=root, title="Select project folder")
        root.destroy()
        return jsonify({"path": folder or ""})
    except Exception as ex:
        return jsonify({"path": "", "error": str(ex)})


@app.route("/api/browse/file")
def api_browse_file():
    filter_type = request.args.get("filter", "docs")
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)

        filters = {
            "docs": ([("Markdown", "*.md"), ("Text", "*.txt"), ("All files", "*.*")], "Select idea / brief file"),
            "json": ([("JSON files", "*.json"), ("All files", "*.*")], "Select plan file (JSON)"),
        }
        filetypes, title = filters.get(filter_type, filters["docs"])

        path = filedialog.askopenfilename(
            parent=root,
            title=title,
            filetypes=filetypes,
        )
        root.destroy()
        return jsonify({"path": path or ""})
    except Exception as ex:
        return jsonify({"path": "", "error": str(ex)})


# ── Git API — MOVED to ui/api/git_routes.py ──────────────────────────────────
# Keeping _git() helper here as it's used by other parts of app.py.

def _git(repo_root: str, *args: str, timeout: int = 15) -> subprocess.CompletedProcess:
    """Run a git command in repo_root. Returns CompletedProcess."""
    return subprocess.run(
        ["git"] + list(args),
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=_WIN_CREATE_FLAGS,
    )


    # Git routes moved to ui/api/git_routes.py (git_bp blueprint)
    # VS Code route moved to ui/api/git_routes.py (git_bp blueprint)


# ── Supervisor Proxy Thread ────────────────────────────────────────────────────

# CLI command templates for each supervisor type (mirrors run.js SUPERVISOR_CMDS)
_SUPERVISOR_CLI_CMDS: dict[str, str] = {
    "codex":    "codex.cmd exec --skip-git-repo-check --color never",
    "claude":   "claude",
    "cursor":   "cursor",
    "windsurf": "windsurf",
}

# Active proxy threads keyed by run_id
_active_proxy_threads: dict[str, "SupervisorProxyThread"] = {}
_proxy_lock = threading.Lock()


class HealthWatchdog(threading.Thread):
    """Monitors Ollama and GPU health during runs. Restarts Ollama if unresponsive."""

    def __init__(self, interval: int = 15):
        super().__init__(daemon=True, name="health-watchdog")
        self._interval = interval
        self._stop_event = threading.Event()
        self._ollama_fail_count = 0

    def stop(self):
        self._stop_event.set()

    def run(self):
        while not self._stop_event.is_set():
            self._check_ollama()
            self._check_gpu()
            self._stop_event.wait(self._interval)

    def _check_ollama(self):
        try:
            r = subprocess.run(
                ["ollama", "ps"], capture_output=True, text=True, timeout=5,
                creationflags=_WIN_CREATE_FLAGS,
            )
            if r.returncode != 0:
                self._ollama_fail_count += 1
            else:
                self._ollama_fail_count = 0
        except Exception:
            self._ollama_fail_count += 1

        if self._ollama_fail_count >= 2:
            _broadcast("health_warning", {
                "component": "ollama",
                "message": "Ollama is unresponsive. Attempting restart...",
            })
            _broadcast("log", {"line": "[watchdog] Ollama unresponsive — attempting restart"})
            self._restart_ollama()

    def _restart_ollama(self):
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/IM", "ollama.exe"],
                               capture_output=True, timeout=5, creationflags=_WIN_CREATE_FLAGS)
                subprocess.Popen(["ollama", "serve"], creationflags=_WIN_CREATE_FLAGS)
            else:
                subprocess.run(["pkill", "-f", "ollama"], capture_output=True, timeout=5)
                subprocess.Popen(["ollama", "serve"])
            self._stop_event.wait(5)
            # Re-check
            try:
                r = subprocess.run(["ollama", "ps"], capture_output=True, timeout=5,
                                   creationflags=_WIN_CREATE_FLAGS)
                if r.returncode == 0:
                    _broadcast("log", {"line": "[watchdog] Ollama restarted successfully"})
                    self._ollama_fail_count = 0
                else:
                    _broadcast("log", {"line": "[watchdog] Ollama restart may have failed"})
            except Exception:
                pass
        except Exception as ex:
            _broadcast("log", {"line": f"[watchdog] Ollama restart failed: {ex}"})

    def _check_gpu(self):
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5, creationflags=_WIN_CREATE_FLAGS,
            )
            if r.returncode == 0 and r.stdout.strip():
                parts = r.stdout.strip().split(",")
                used = float(parts[0].strip())
                total = float(parts[1].strip())
                if total > 0 and used / total > 0.95:
                    _broadcast("health_warning", {
                        "component": "gpu",
                        "message": f"GPU VRAM critically low: {int(used)}MB / {int(total)}MB",
                    })
        except Exception:
            pass


_health_watchdog: Optional[HealthWatchdog] = None


class SupervisorProxyThread(threading.Thread):
    """Polls manual_supervisor/requests/ and dispatches review to the correct
    supervisor backend based on the current setting.  Enables mid-run switching."""

    def __init__(
        self,
        run_id: str,
        repo_root: str,
        get_supervisor_fn,
        relay_session_id: str = "",
        timeout: int = 300,
    ):
        super().__init__(daemon=True, name=f"supervisor-proxy-{run_id}")
        self._run_id = run_id
        self._repo_root = repo_root
        self._get_supervisor = get_supervisor_fn
        self._relay_session_id = relay_session_id
        self._timeout = timeout
        self._stop_event = threading.Event()
        self._seen: set[str] = set()
        self._supervisor_override: Optional[str] = None
        self._supervisor_command_override: Optional[str] = None

    def get_supervisor(self) -> str:
        if self._supervisor_override is not None:
            return self._supervisor_override
        return self._get_supervisor()

    def set_supervisor(self, supervisor: str, supervisor_command: str = "") -> None:
        self._supervisor_override = supervisor
        self._supervisor_command_override = supervisor_command or ""

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        requests_dir = Path(self._repo_root) / "bridge_progress" / "manual_supervisor" / "requests"
        decisions_dir = Path(self._repo_root) / "bridge_progress" / "manual_supervisor" / "decisions"

        while not self._stop_event.is_set():
            if requests_dir.exists():
                for req_file in sorted(requests_dir.glob("*.json")):
                    # Track by filename + mtime so rework rewrites are detected
                    try:
                        mtime = req_file.stat().st_mtime
                    except OSError:
                        continue
                    seen_key = f"{req_file.stem}:{mtime}"
                    if seen_key in self._seen:
                        continue

                    # Check if a decision already exists AND is newer than the request
                    dec_file = decisions_dir / req_file.name.replace("_request.json", "_decision.json")
                    if dec_file.exists():
                        try:
                            if dec_file.stat().st_mtime >= mtime:
                                self._seen.add(seen_key)
                                continue
                        except OSError:
                            pass

                    try:
                        req_data = json.loads(req_file.read_text(encoding="utf-8"))
                    except Exception:
                        continue

                    # Session filtering: skip requests from other sessions
                    if self._relay_session_id:
                        req_session = str(req_data.get("relay_session_id", "")).strip()
                        if req_session and req_session != self._relay_session_id:
                            continue

                    self._seen.add(seen_key)
                    supervisor = self.get_supervisor()
                    self._dispatch(supervisor, req_file, req_data, decisions_dir)

            self._stop_event.wait(0.5)

    def _dispatch(self, supervisor: str, req_file: Path, req_data: dict, decisions_dir: Path) -> None:
        task_id = req_data.get("task_id", 0)

        if supervisor in ("chatbot", "ai_relay"):
            # Chatbot path — emit SSE event, UI shows copy-paste card
            # Build review packet for the user
            packet = ""
            try:
                from utils.relay_formatter import build_review_packet
                tasks = state_store.load_relay_tasks()
                total = len(tasks)
                task = next((t for t in tasks if str(t.get("id")) == str(task_id)), req_data)
                diff = req_data.get("diff", "")
                validation = req_data.get("validation_result", "not run")
                attempt = req_data.get("attempt", 1)
                max_retries = req_data.get("max_retries", 2)
                packet = build_review_packet(task, diff, validation, attempt, max_retries, total, "")
            except Exception:
                packet = json.dumps(req_data, indent=2)

            _broadcast("reviewer_active", {
                "task_index": task_id,
                "task_total": len(state_store.load_relay_tasks()) or "?",
                "task_title": req_data.get("instruction", "")[:60],
            })
            _broadcast("supervisor_review_requested", {
                "request_id": req_file.stem,
                "task_id": task_id,
                "packet": packet,
                **{k: req_data.get(k, "") for k in ("diff", "validation_result", "attempt")},
            })
            # Decision will come via POST /api/relay/submit-decision

        elif supervisor == "manual":
            # Manual path — emit SSE event, user writes decision via UI
            _broadcast("reviewer_active", {
                "task_index": task_id,
                "task_total": "?",
                "task_title": req_data.get("instruction", "")[:60],
            })
            _broadcast("supervisor_review_requested", {
                "request_id": req_file.stem,
                "task_id": task_id,
                "manual": True,
                **{k: req_data.get(k, "") for k in ("diff", "validation_result", "attempt")},
            })
            # Decision will come via manual review UI / submit-decision

        else:
            # CLI supervisor path — auto-call the supervisor CLI
            cli_cmd = self._resolve_cli_command(supervisor)
            if not cli_cmd:
                _broadcast("supervisor_review_submitted", {
                    "request_id": req_file.stem,
                    "task_id": task_id,
                    "decision": {"decision": "pass"},
                    "error": f"No CLI command configured for supervisor '{supervisor}', auto-passing.",
                })
                decision_payload = {"task_id": int(task_id), "decision": "pass"}
                if self._relay_session_id:
                    decision_payload["relay_session_id"] = self._relay_session_id
                decisions_dir.mkdir(parents=True, exist_ok=True)
                dec_file = decisions_dir / req_file.name.replace("_request.json", "_decision.json")
                dec_file.write_text(json.dumps(decision_payload, indent=2), encoding="utf-8")
                return

            # Build review prompt from request data
            prompt = self._build_review_prompt(req_data)

            _broadcast("reviewer_active", {
                "task_index": task_id,
                "task_total": "?",
                "task_title": req_data.get("instruction", "")[:60],
            })
            _broadcast("log", {"line": f"[proxy] Calling {supervisor} CLI for task {task_id}..."})

            try:
                decision = self._call_cli(cli_cmd, prompt)
            except FileNotFoundError:
                _broadcast("log", {"line": f"[proxy] ERROR: '{cli_cmd.split()[0]}' not found. Is it installed and on PATH? Auto-passing task."})
                _broadcast("log", {"line": f"[proxy] Tip: check Setup page or run 'which {cli_cmd.split()[0]}' in terminal."})
                decision = {"decision": "pass"}
            except subprocess.TimeoutExpired:
                _broadcast("log", {"line": f"[proxy] ERROR: '{supervisor}' CLI timed out after {self._timeout}s. Auto-passing task."})
                decision = {"decision": "pass"}
            except Exception as exc:
                _broadcast("log", {"line": f"[proxy] CLI supervisor error: {exc}"})
                decision = {"decision": "pass"}

            decision["task_id"] = int(task_id)
            if self._relay_session_id:
                decision["relay_session_id"] = self._relay_session_id

            decisions_dir.mkdir(parents=True, exist_ok=True)
            dec_file = decisions_dir / req_file.name.replace("_request.json", "_decision.json")
            dec_file.write_text(json.dumps(decision, indent=2), encoding="utf-8")

            _broadcast("reviewer_done", {"decision": decision.get("decision", "pass")})
            _broadcast("supervisor_review_submitted", {
                "request_id": req_file.stem,
                "task_id": task_id,
                "decision": decision,
            })

    def _resolve_cli_command(self, supervisor: str) -> str:
        if supervisor == "custom":
            return self._supervisor_command_override or ""
        return _SUPERVISOR_CLI_CMDS.get(supervisor, "")

    def _build_review_prompt(self, req_data: dict) -> str:
        task_id = req_data.get("task_id", "?")
        instruction = req_data.get("instruction", "")
        files = req_data.get("files", [])
        diff = req_data.get("diff", "(no diff)")
        task_type = req_data.get("type", "modify")

        return (
            "You are a Tech Supervisor reviewing completed developer work.\n"
            "Reply with exactly one of these two forms (nothing else):\n"
            "  PASS\n"
            "  REWORK: <one-sentence atomic replacement instruction — no code>\n\n"
            f"Task {task_id} ({task_type})\n"
            f"Files: {', '.join(files) if isinstance(files, list) else files}\n"
            f"Instruction: {instruction}\n\n"
            f"Changes made:\n{diff}\n"
        )

    def _call_cli(self, cli_cmd: str, prompt: str) -> dict:
        """Call a CLI supervisor and parse PASS/REWORK response."""
        from utils.command_resolution import resolve_command_arguments

        repo_root = Path(self._repo_root)
        arguments, _ = resolve_command_arguments(cli_cmd, repo_root)

        # Determine prompt delivery: exec-style (Codex) gets prompt as arg,
        # others receive it on stdin.
        is_exec_style = "exec" in arguments
        if is_exec_style:
            arguments.append(prompt)
            stdin_prompt = None
        else:
            stdin_prompt = prompt

        result = subprocess.run(
            arguments,
            input=stdin_prompt,
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=self._timeout,
            creationflags=_WIN_CREATE_FLAGS,
        )

        response = result.stdout.strip()
        if not response:
            return {"decision": "pass"}

        upper = response.upper().strip()
        if upper.startswith("PASS"):
            return {"decision": "pass"}
        elif upper.startswith("REWORK"):
            colon_pos = response.find(":")
            instruction = response[colon_pos + 1:].strip() if colon_pos >= 0 else ""
            return {"decision": "rework", "instruction": instruction}
        else:
            # Couldn't parse — default to pass
            return {"decision": "pass"}


# ── Run lifecycle ───────────────────────────────────────────────────────────────

def _start_bridge_run(settings: dict, extra_history: dict = None) -> str:
    """Helper to initialize history, listeners, and start a bridge run."""
    run = get_run()
    repo_root = settings.get("repo_root", "").strip()

    # Auto-register the repo as a known project
    if repo_root:
        state_store.add_project(repo_root)

    # Telemetry: record run start
    if _get_telemetry:
        t = _get_telemetry()
        t.run_started(
            supervisor=settings.get("supervisor", "?"),
            model=settings.get("aider_model", "?"),
            goal_len=len(settings.get("goal", "")),
            task_count=0,
        )

    history_payload = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "goal": settings.get("goal", ""),
        "repo_root": settings.get("repo_root", ""),
        "aider_model": settings.get("aider_model", ""),
        "supervisor": settings.get("supervisor", ""),
        "supervisor_command": settings.get("supervisor_command", ""),
        "dry_run": settings.get("dry_run", False),
        "status": "running",
        "tasks": 0,
        "elapsed": 0,
        "log": [],
        "tasks_detail": [],
    }
    if extra_history:
        history_payload.update(extra_history)

    run_id = state_store.add_history_entry(history_payload)

    def on_event(event_type: str, data: dict) -> None:
        _broadcast(event_type, data)
        if event_type in ("task_update", "progress", "review_required", "paused", "resumed", "start"):
            state_store.update_history_entry(run_id, {
                "status": run.status,
                "tasks": len(run.tasks),
                "tasks_detail": list(run.tasks.values()),
                "log": run.log_lines[-state_store.MAX_LOG_LINES:],
            })
        if event_type in ("complete", "error", "stopped"):
            final = data.get("final") or {}
            run_status = data.get("status", "failure") if event_type == "complete" else event_type
            state_store.update_history_entry(run_id, {
                "status": run_status,
                "tasks": final.get("tasks", len(run.tasks)),
                "elapsed": data.get("elapsed", 0),
                "log": run.log_lines[-state_store.MAX_LOG_LINES:],
                "tasks_detail": list(run.tasks.values()),
            })
            # Update generated plan status
            import time as _t
            _active_plan_id = settings.get("_active_plan_id", "")
            if _active_plan_id:
                _plan_status = "completed" if run_status == "success" else run_status
                state_store.update_generated_plan(_active_plan_id, {
                    "status": _plan_status,
                    "last_run_at": _t.strftime("%Y-%m-%d %H:%M:%S"),
                    "completed_tasks": run.completed_tasks,
                    "failed_task_id": data.get("failed_task_id"),
                })
            run.remove_listener(on_event)
            # Telemetry: record run outcome
            if _get_telemetry:
                t = _get_telemetry()
                if event_type == "complete" and data.get("status") == "success":
                    t.run_completed(run.completed_tasks, run.total_tasks, data.get("elapsed", 0), 0)
                elif event_type in ("error", "stopped"):
                    t.run_failed(str(data.get("message", data.get("status", "?"))), elapsed=data.get("elapsed", 0))
                else:
                    t.run_failed("failure", elapsed=data.get("elapsed", 0))
                # Auto-save telemetry
                try:
                    rr = settings.get("repo_root", "").strip()
                    if rr:
                        t.save(Path(rr))
                except Exception:
                    pass
            # Stop health watchdog
            if _health_watchdog:
                _health_watchdog.stop()
            # Stop the supervisor proxy thread
            with _proxy_lock:
                proxy = _active_proxy_threads.pop(run_id, None)
            if proxy:
                proxy.stop()

            # Run queue: auto-advance to next queued item on success
            if event_type == "complete" and data.get("status") == "success":
                next_item = state_store.pop_run_queue()
                if next_item:
                    _broadcast("log", {"line": "[queue] Starting next queued run..."})
                    _broadcast("queue_advance", {"goal": next_item.get("goal", "")})
                    try:
                        _start_bridge_run(next_item)
                    except Exception as qex:
                        _broadcast("log", {"line": f"[queue] Failed to start next run: {qex}"})

    run.add_listener(on_event)

    # Pre-warm: load model into VRAM before Aider's first task
    _model = settings.get("aider_model", "").replace("ollama/", "")
    if _model:
        try:
            import urllib.request as _ur
            _body = json.dumps({"model": _model, "prompt": "hi", "keep_alive": "5m"}).encode()
            _req = _ur.Request("http://localhost:11434/api/generate", data=_body,
                               headers={"Content-Type": "application/json"}, method="POST")
            with _ur.urlopen(_req, timeout=60) as _resp:
                _resp.read()
        except Exception:
            pass  # Non-blocking — if Ollama isn't ready, Aider will handle it

    # Start health watchdog
    global _health_watchdog
    if _health_watchdog:
        _health_watchdog.stop()
    _health_watchdog = HealthWatchdog()
    _health_watchdog.start()

    # Start supervisor proxy thread
    supervisor = settings.get("supervisor", "codex")
    relay_session_id = settings.get("relay_session_id", "")
    supervisor_command = settings.get("supervisor_command", "")

    def _get_supervisor():
        return supervisor

    proxy = SupervisorProxyThread(
        run_id=run_id,
        repo_root=repo_root,
        get_supervisor_fn=_get_supervisor,
        relay_session_id=relay_session_id,
        timeout=int(settings.get("task_timeout", 300)),
    )
    if supervisor == "custom" and supervisor_command:
        proxy.set_supervisor("custom", supervisor_command)
    with _proxy_lock:
        _active_proxy_threads[run_id] = proxy

    run.start(settings, run_id)
    proxy.start()
    return run_id


@app.route("/api/run", methods=["POST"])
def api_start_run():
    run = get_run()
    if run.is_running:
        return jsonify({"error": "A run is already in progress."}), 409

    settings = request.json or {}

    # Validate repo_root before doing anything
    _repo = settings.get("repo_root", "").strip()
    if not _repo:
        return jsonify({
            "error": (
                "No project folder configured. "
                "Open the Run settings panel, set 'Repo Root' to your project directory, "
                "and try again."
            )
        }), 400

    state_store.save_settings(settings)
    run_id = _start_bridge_run(settings)

    return jsonify({"ok": True, "run_id": run_id})


@app.route("/api/run/nl/launch", methods=["POST"])
def api_run_nl_launch():
    """Launch a run directly from the confirmed NL brief and plan."""
    run = get_run()
    if run.is_running:
        return jsonify({"error": "A run is already in progress."}), 409

    data = request.get_json(force=True) or {}
    repo_root = str(data.get("repo_root", "")).strip()
    if not repo_root:
        settings = state_store.load_settings()
        repo_root = settings.get("repo_root", "").strip()
    
    if not repo_root:
        return jsonify({"error": "No project folder configured."}), 400

    state = state_store.load_run_nl_state(repo_root)
    if not state or not state.get("brief"):
        return jsonify({"error": "No confirmed plan found for this project. Generate and confirm a plan first."}), 400
    
    brief     = state.get("brief", {})
    plan_file = state.get("plan_file", "")

    # Validate plan file exists — stale references cause Aider to hang on nonexistent files
    if plan_file and not Path(plan_file).exists():
        plan_file = ""

    if not plan_file:
        return jsonify({"error": "No confirmed plan file found. Click 'New Conversation', regenerate the plan, and confirm it."}), 400

    # Merge current global settings (model, supervisor) with NL-specific goal/plan
    settings = state_store.load_settings()
    run_settings = dict(settings)
    run_settings.update({
        "goal":      brief.get("goal", ""),
        "repo_root": repo_root,
        "plan_file": plan_file,
    })
    
    run_id = _start_bridge_run(run_settings, extra_history={"source": "natural_language"})
    
    # Persist the run_id in NL state for traceability
    state["last_run_id"] = run_id
    state_store.save_run_nl_state(repo_root, state)
    
    return jsonify({"ok": True, "run_id": run_id})


@app.route("/api/run/stop", methods=["POST"])
def api_stop_run():
    run = get_run()
    # Stop any active proxy thread
    if run.run_id:
        with _proxy_lock:
            proxy = _active_proxy_threads.pop(run.run_id, None)
        if proxy:
            proxy.stop()
    run.stop()
    return jsonify({"ok": True})


@app.route("/api/run/supervisor", methods=["POST"])
def api_switch_supervisor():
    """Switch supervisor mid-run. Takes effect on the next review point."""
    data = request.json or {}
    new_supervisor = data.get("supervisor", "")
    new_command = data.get("supervisor_command", "")

    run = get_run()
    if not run.is_running or not run.run_id:
        return jsonify({"error": "No active run."}), 409

    with _proxy_lock:
        proxy = _active_proxy_threads.get(run.run_id)
    if proxy:
        proxy.set_supervisor(new_supervisor, new_command)

    # Update the run's driver label
    run.driver = new_supervisor

    _broadcast("log", {"line": f"[proxy] Supervisor switched to: {new_supervisor}"})
    return jsonify({"ok": True, "supervisor": new_supervisor})


@app.route("/api/run/status")
def api_run_status():
    run = get_run()
    return jsonify({
        "is_running": run.is_running,
        "status": run.status,
        "paused": run.status == "paused",
        "tasks": list(run.tasks.values()),
        "command": run.command_preview,
        "total_tasks": run.total_tasks,
        "completed_tasks": run.completed_tasks,
        "done_tasks": run.completed_tasks,
        "repo_root": run.repo_root,
        "driver": run.driver,
        "run_id": run.run_id,
        "percent": (
            round(run.completed_tasks / run.total_tasks * 100)
            if run.total_tasks > 0 else 0
        ),
    })


@app.route("/api/run/log")
def api_run_log():
    """Return the current run's full log as a JSON array of strings."""
    run = get_run()
    offset = request.args.get("offset", 0, type=int)
    return jsonify({
        "lines": run.log_lines[offset:],
        "total": len(run.log_lines),
    })


@app.route("/api/run/tasks")
def api_run_tasks():
    """Return structured task objects from the current (or last) run."""
    run = get_run()
    return jsonify({
        "tasks": list(run.tasks.values()),
        "total": run.total_tasks,
        "completed": run.completed_tasks,
    })


@app.route("/api/run/progress")
def api_run_progress():
    """Return persisted task progress from checkpoint + task_metrics.

    This survives restarts — reads from bridge_progress/ on disk.
    """
    repo_root = (request.args.get("repo_root") or "").strip()
    if not repo_root:
        settings = state_store.load_settings()
        repo_root = settings.get("repo_root", "").strip()
    if not repo_root:
        return jsonify({"tasks": [], "completed": [], "total_tasks": 0, "can_resume": False, "plan_file": "", "failed_task_id": None, "last_status": ""})

    progress_dir = Path(repo_root) / "bridge_progress"

    # Load checkpoint (completed task IDs)
    completed = []
    checkpoint_file = progress_dir / "checkpoint.json"
    if checkpoint_file.exists():
        try:
            data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
            completed = sorted(data.get("completed", []))
        except Exception:
            pass

    # Load task metrics (full task details)
    tasks = []
    total_tasks = 0
    failed_task_id = None
    last_status = ""
    metrics_file = progress_dir / "task_metrics.json"
    if metrics_file.exists():
        try:
            data = json.loads(metrics_file.read_text(encoding="utf-8"))
            total_tasks = data.get("planned_tasks", 0)
            failed_task_id = data.get("failed_task_id")
            last_status = data.get("status", "")
            for t in data.get("tasks", []):
                tid = t.get("id", 0)
                status = "done" if t.get("completed") or tid in completed else "pending"
                if tid == failed_task_id:
                    status = "failed"
                tasks.append({
                    "id": tid,
                    "type": t.get("type", ""),
                    "files": t.get("files", []),
                    "instruction": t.get("instruction", ""),
                    "status": status,
                    "commit_sha": t.get("commit_sha"),
                })
        except Exception:
            pass

    # Load plan file — try multiple sources (NL state, improvement_plan, taskJsons/)
    plan_file = ""
    try:
        nl_state = state_store.load_run_nl_state(repo_root)
        plan_file = nl_state.get("plan_file", "") if nl_state else ""
    except Exception:
        pass

    # Fallback: check standard plan locations
    if not plan_file or not Path(plan_file).exists():
        for candidate in [
            progress_dir / "improvement_plan.json",
            *sorted(Path(repo_root, "taskJsons").glob("*.json"), reverse=True)[:1],
        ]:
            if candidate.exists():
                plan_file = str(candidate)
                break

    # Enrich tasks with instructions from plan file (task_metrics doesn't store them)
    if plan_file and Path(plan_file).exists() and tasks:
        try:
            plan_data = json.loads(Path(plan_file).read_text(encoding="utf-8"))
            plan_tasks = plan_data.get("tasks", [])
            instr_map = {}
            for pt in plan_tasks:
                pid = pt.get("id", 0)
                instr_map[pid] = pt.get("instruction", "")
            for t in tasks:
                if not t.get("instruction") and t["id"] in instr_map:
                    t["instruction"] = instr_map[t["id"]]
        except Exception:
            pass

    # If no tasks from metrics but plan file exists, load tasks from plan
    if not tasks and plan_file and Path(plan_file).exists():
        try:
            plan_data = json.loads(Path(plan_file).read_text(encoding="utf-8"))
            plan_tasks = plan_data.get("tasks", [])
            total_tasks = len(plan_tasks)
            for pt in plan_tasks:
                tid = pt.get("id", 0)
                tasks.append({
                    "id": tid,
                    "type": pt.get("type", ""),
                    "files": pt.get("files", []),
                    "instruction": pt.get("instruction", ""),
                    "status": "done" if tid in completed else "pending",
                    "commit_sha": None,
                })
        except Exception:
            pass

    can_resume = bool(completed) and len(completed) < total_tasks and bool(plan_file)

    return jsonify({
        "total_tasks": total_tasks,
        "completed": completed,
        "failed_task_id": failed_task_id,
        "last_status": last_status,
        "tasks": tasks,
        "plan_file": plan_file,
        "can_resume": can_resume,
    })


@app.route("/api/telemetry", methods=["GET"])
def api_telemetry():
    """Export telemetry data for AI analysis."""
    if not _get_telemetry:
        return jsonify({"error": "Telemetry not available"}), 500
    t = _get_telemetry()
    return jsonify(t.build_report())


@app.route("/api/telemetry/save", methods=["POST"])
def api_telemetry_save():
    """Save telemetry to disk."""
    if not _get_telemetry:
        return jsonify({"error": "Telemetry not available"}), 500
    t = _get_telemetry()
    data = request.json or {}
    repo_root = (data.get("repo_root") or "").strip()
    if repo_root:
        path = t.save(Path(repo_root))
    else:
        path = t.save()
    return jsonify({"ok": True, "path": str(path)})


# Firebase/Auth/Sync routes — MOVED to ui/api/firebase_routes.py (firebase_bp)

@app.route("/api/version")
def api_version():
    try:
        from utils.version import get_version_info
        return jsonify(get_version_info())
    except Exception:
        return jsonify({"version": "0.5.6", "commit": "", "branch": ""})


@app.route("/api/projects/status")
def api_projects_status():
    """Return all projects with their last run status and task progress."""
    projects = state_store.load_projects()
    result = []
    for p in projects:
        path = p.get("path", "")
        if not path:
            continue
        entry = {
            "name": p.get("name") or Path(path).name,
            "path": path,
            "last_run_status": "",
            "last_run_date": "",
            "tasks_completed": 0,
            "tasks_total": 0,
        }
        # Read task_metrics
        metrics_file = Path(path) / "bridge_progress" / "task_metrics.json"
        if metrics_file.exists():
            try:
                m = json.loads(metrics_file.read_text(encoding="utf-8"))
                entry["last_run_status"] = m.get("status", "")
                entry["tasks_total"] = m.get("planned_tasks", 0)
                entry["tasks_completed"] = len(m.get("completed_task_ids", []))
            except Exception:
                pass
        # Read checkpoint
        cp_file = Path(path) / "bridge_progress" / "checkpoint.json"
        if cp_file.exists():
            try:
                cp = json.loads(cp_file.read_text(encoding="utf-8"))
                entry["tasks_completed"] = max(entry["tasks_completed"], len(cp.get("completed", [])))
            except Exception:
                pass
        # Read last run from history
        try:
            history = state_store.load_history()
            for h in history:
                if h.get("repo_root") == path:
                    entry["last_run_date"] = h.get("timestamp", "")
                    if not entry["last_run_status"]:
                        entry["last_run_status"] = h.get("status", "")
                    break
        except Exception:
            pass
        result.append(entry)
    return jsonify({"projects": result})


@app.route("/api/run/undo-task", methods=["POST"])
def api_undo_task():
    """Revert the git commit for a specific task."""
    run = get_run()
    if run.is_running:
        return jsonify({"error": "Cannot undo while a run is active."}), 409

    data = request.json or {}
    task_id = data.get("task_id")
    repo_root = (data.get("repo_root") or "").strip()
    if not task_id or not repo_root:
        return jsonify({"error": "task_id and repo_root required"}), 400

    progress_dir = Path(repo_root) / "bridge_progress"
    metrics_file = progress_dir / "task_metrics.json"
    checkpoint_file = progress_dir / "checkpoint.json"

    # Find the commit SHA
    commit_sha = None
    try:
        if metrics_file.exists():
            metrics = json.loads(metrics_file.read_text(encoding="utf-8"))
            for t in metrics.get("tasks", []):
                if t.get("id") == task_id:
                    commit_sha = t.get("commit_sha")
                    break
    except Exception:
        pass

    if not commit_sha:
        return jsonify({"error": f"No commit SHA found for task {task_id}"}), 404

    # Verify SHA exists
    r = _git(repo_root, "cat-file", "-t", commit_sha)
    if r.returncode != 0:
        return jsonify({"error": f"Commit {commit_sha} not found in git history"}), 404

    # Revert
    r = _git(repo_root, "revert", "--no-edit", commit_sha, timeout=30)
    if r.returncode != 0:
        return jsonify({"error": f"Git revert failed: {r.stderr.strip()}"}), 500

    # Update checkpoint — remove task_id
    try:
        if checkpoint_file.exists():
            cp = json.loads(checkpoint_file.read_text(encoding="utf-8"))
            completed = cp.get("completed", [])
            if task_id in completed:
                completed.remove(task_id)
                checkpoint_file.write_text(json.dumps({"completed": sorted(completed)}, indent=2), encoding="utf-8")
    except Exception:
        pass

    # Update task_metrics — mark task as not completed
    try:
        if metrics_file.exists():
            metrics = json.loads(metrics_file.read_text(encoding="utf-8"))
            for t in metrics.get("tasks", []):
                if t.get("id") == task_id:
                    t["completed"] = False
                    t["commit_sha"] = None
                    break
            if task_id in metrics.get("completed_task_ids", []):
                metrics["completed_task_ids"].remove(task_id)
                metrics["completed_tasks"] = len(metrics["completed_task_ids"])
            metrics_file.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    except Exception:
        pass

    return jsonify({"ok": True, "task_id": task_id, "reverted_sha": commit_sha})


@app.route("/api/plans/list")
def api_plans_list():
    return jsonify({
        "plans": state_store.load_plan_favorites(),
        "generated": state_store.load_generated_plans(),
    })


@app.route("/api/plans/save", methods=["POST"])
def api_plans_save():
    data = request.json or {}
    name = (data.get("name") or "").strip()
    tasks = data.get("tasks") or []
    goal = (data.get("goal") or "").strip()
    if not name or not tasks:
        return jsonify({"error": "name and tasks required"}), 400
    fav = {
        "id": str(uuid.uuid4())[:8],
        "name": name,
        "goal": goal,
        "tasks": tasks,
        "task_count": len(tasks),
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    state_store.save_plan_favorite(fav)
    return jsonify({"ok": True, "id": fav["id"]})


@app.route("/api/plans/<plan_id>", methods=["DELETE"])
def api_plans_delete(plan_id):
    state_store.delete_plan_favorite(plan_id)
    return jsonify({"ok": True})


@app.route("/api/plans/generated/<plan_id>/load", methods=["POST"])
def api_plans_generated_load(plan_id):
    """Load a generated plan for execution — returns tasks with checkpoint overlay."""
    plan = state_store.get_generated_plan(plan_id)
    if not plan:
        return jsonify({"error": "Plan not found"}), 404

    tasks = plan.get("tasks", [])
    plan_file = plan.get("plan_file", "")
    repo_root = plan.get("repo_root", "")

    # Overlay checkpoint status if available
    if repo_root:
        from utils.checkpoint import load_checkpoint
        completed = load_checkpoint(Path(repo_root))
        for t in tasks:
            t["status"] = "done" if t.get("id") in completed else "pending"

    return jsonify({
        "ok": True,
        "plan_id": plan_id,
        "plan_file": plan_file,
        "tasks": tasks,
        "goal": plan.get("goal", ""),
    })


@app.route("/api/plans/generated/<plan_id>", methods=["DELETE"])
def api_plans_generated_delete(plan_id):
    state_store.delete_generated_plan(plan_id)
    return jsonify({"ok": True})


@app.route("/api/run/queue")
def api_run_queue():
    return jsonify({"queue": state_store.load_run_queue()})


@app.route("/api/run/queue", methods=["POST"])
def api_run_queue_add():
    data = request.json or {}
    data["queued_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    state_store.append_run_queue(data)
    return jsonify({"ok": True, "queue_size": len(state_store.load_run_queue())})


@app.route("/api/run/queue/<int:index>", methods=["DELETE"])
def api_run_queue_remove(index):
    state_store.remove_from_queue(index)
    return jsonify({"ok": True})


    # /api/run/preflight moved to ui/api/system_routes.py (system_bp)


    # System routes moved to ui/api/system_routes.py (system_bp blueprint):
    # /api/system/gpu-processes, /api/system/kill-process,
    # /api/system/unload-model, /api/system/benchmark, /api/system/recommend-model


@app.route("/api/run/import-plan", methods=["POST"])
def api_run_import_plan():
    """Read a plan JSON file and return tasks with checkpoint status overlay."""
    data = request.json or {}
    plan_file = (data.get("plan_file") or "").strip()
    repo_root = (data.get("repo_root") or "").strip()

    if not repo_root:
        settings = state_store.load_settings()
        repo_root = settings.get("repo_root", "").strip()

    if not plan_file or not Path(plan_file).exists():
        return jsonify({"error": "Plan file not found"}), 404

    # Load checkpoint
    completed = set()
    if repo_root:
        checkpoint_file = Path(repo_root) / "bridge_progress" / "checkpoint.json"
        if checkpoint_file.exists():
            try:
                completed = set(json.loads(checkpoint_file.read_text(encoding="utf-8")).get("completed", []))
            except Exception:
                pass

    # Read plan file
    try:
        plan_data = json.loads(Path(plan_file).read_text(encoding="utf-8"))
        plan_tasks = plan_data.get("tasks", [])
    except Exception as ex:
        return jsonify({"error": f"Failed to parse plan: {ex}"}), 400

    tasks = []
    for pt in plan_tasks:
        tid = pt.get("id", 0)
        tasks.append({
            "id": tid,
            "type": pt.get("type", ""),
            "files": pt.get("files", []),
            "instruction": pt.get("instruction", ""),
            "status": "done" if tid in completed else "pending",
        })

    can_resume = bool(completed) and len(completed) < len(tasks)

    return jsonify({
        "tasks": tasks,
        "total_tasks": len(tasks),
        "completed": sorted(completed),
        "can_resume": can_resume,
    })


@app.route("/api/run/pause", methods=["POST"])
def api_pause_run():
    """Create the pause file so the bridge stops between tasks."""
    run = get_run()
    if not run.is_running:
        return jsonify({"error": "No run in progress."}), 400
    settings = state_store.load_settings()
    repo_root = settings.get("repo_root", "").strip()
    if not repo_root:
        return jsonify({"error": "repo_root not set."}), 400
    pause_file = Path(repo_root) / ".bridge_pause"
    try:
        pause_file.touch()
        return jsonify({"ok": True, "pause_file": str(pause_file)})
    except OSError as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/run/resume", methods=["POST"])
def api_resume_run():
    """Delete the pause file so the bridge continues."""
    settings = state_store.load_settings()
    repo_root = settings.get("repo_root", "").strip()
    if not repo_root:
        return jsonify({"error": "repo_root not set."}), 400
    pause_file = Path(repo_root) / ".bridge_pause"
    try:
        if pause_file.exists():
            pause_file.unlink()
        return jsonify({"ok": True})
    except OSError as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/run/input", methods=["POST"])
def api_run_input():
    """Send a line of text to the running subprocess stdin."""
    data = request.get_json(force=True) or {}
    text = (data.get("text") or "").rstrip("\n")
    if not text:
        return jsonify({"error": "text is required"}), 400
    sent = get_run().send_input(text)
    if not sent:
        return jsonify({"error": "No process running or stdin not available"}), 409
    return jsonify({"ok": True})


def _classify_goal(goal: str) -> str:
    """Classify a user goal into a routing category.

    Returns one of:
      'read'        — simple file reading / status check
      'investigate'  — deep analysis: bug finding, security review, impact analysis
      'code'        — requires code changes (create/modify/delete)
    """
    lower = goal.lower().strip()

    # Investigate patterns checked FIRST (higher priority than read)
    investigate_keywords = [
        "investigate", "debug", "find the bug", "find bug",
        "why does", "why is", "what causes", "root cause",
        "review ", "security review", "code review", "audit",
        "trace ", "data flow", "call graph", "dependency",
        "impact of", "what breaks", "what would break",
        "missing test", "test coverage", "test gap", "tests are missing",
        "compare ", "difference between", "diff ",
        "explain how", "explain the", "how does",
        "architecture", "design of",
    ]
    if any(kw in lower for kw in investigate_keywords):
        return "investigate"

    # Read patterns: simple information retrieval, listing, checking status
    read_keywords = [
        "read ", "list ", "show ", "tell me", "what is", "what are",
        "which ", "how many", "count ", "check status", "check the",
        "display ", "print ", "find all", "search for",
        "in progress", "in-progress", "status of",
    ]
    if any(kw in lower for kw in read_keywords):
        # But some "find" goals actually want fixes
        fix_signals = ["fix", "repair", "resolve", "patch", "update", "change", "add", "create", "implement", "implemen", "build", "refactor", "develop", "write"]
        if not any(fs in lower for fs in fix_signals):
            return "read"

    return "code"


@app.route("/api/run/brief", methods=["POST"])
def api_run_brief():
    """Convert a natural-language request into a structured execution brief via Ollama."""
    import urllib.request
    import urllib.error

    data = request.get_json(force=True) or {}
    # Accept both "message" and "goal" keys for backwards compatibility
    message = str(data.get("message") or data.get("goal") or "").strip()
    repo_root = str(data.get("repo_root", "")).strip()

    if not message:
        return jsonify({"error": "message is required"}), 400

    settings = state_store.load_settings()
    if not repo_root:
        repo_root = str(settings.get("repo_root", "")).strip()

    raw_model = str(settings.get("aider_model", "ollama/qwen2.5-coder:14b")).strip()
    if not raw_model.startswith("ollama/"):
        raw_model = "ollama/qwen2.5-coder:14b"
    ollama_model = raw_model[7:]

    knowledge_ctx = _build_chat_context(repo_root) if repo_root else ""

    system_prompt = (
        "You are a precise software project planner. "
        "Convert the developer's request into a structured execution brief.\n\n"
        "Output ONLY valid JSON with no markdown, no explanation, no code fences. "
        "Use exactly this shape:\n"
        "{\n"
        '  "goal": "One clear technical sentence describing what to build or fix.",\n'
        '  "assumptions": ["assumption 1"],\n'
        '  "constraints": ["Do not touch X"],\n'
        '  "acceptance_criteria": ["User can do A"],\n'
        '  "clarification_questions": [],\n'
        '  "needs_clarification": false,\n'
        '  "confidence_score": 85,\n'
        '  "risks": ["Potential risk 1"],\n'
        '  "risk_level": "low"\n'
        "}\n\n"
        "Rules:\n"
        "- goal: single precise technical statement, not a question\n"
        "- assumptions: what you assume about the project or intent\n"
        "- constraints: what must NOT change or must be preserved\n"
        "- acceptance_criteria: observable, testable outcomes\n"
        "- clarification_questions: ONLY when something critical is ambiguous; leave [] when clear\n"
        "- needs_clarification: true only when clarification_questions is non-empty\n"
        "- confidence_score: 0 to 100 based on how well you understand the request\n"
        "- risks: identify patterns like bulk deletion, massive refactors, or sensitive file edits\n"
        "- risk_level: 'low', 'medium', or 'high'\n"
        "- Be concise. One item per list entry. No generic items like 'code should work'."
    )
    if knowledge_ctx:
        system_prompt += f"\n\nProject context:\n{knowledge_ctx}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": message},
    ]

    body = json.dumps({
        "model": ollama_model,
        "messages": messages,
        "stream": False,
        "keep_alive": "30s",
    }).encode("utf-8")

    req = urllib.request.Request(
        "http://localhost:11434/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        content = result.get("message", {}).get("content", "").strip()
        # Strip markdown code fences if model wraps output
        if content.startswith("```"):
            lines = content.split("\n")
            end = -1 if lines[-1].strip() == "```" else len(lines)
            content = "\n".join(lines[1:end])
        brief = json.loads(content)
        brief.setdefault("goal", "")
        brief.setdefault("assumptions", [])
        brief.setdefault("constraints", [])
        brief.setdefault("acceptance_criteria", [])
        brief.setdefault("clarification_questions", [])
        brief["needs_clarification"] = bool(brief.get("clarification_questions"))
        brief.setdefault("confidence_score", 100)
        brief.setdefault("risks", [])
        brief.setdefault("risk_level", "low")
        brief["requires_confirmation"] = brief.get("risk_level") in ("medium", "high")
        return jsonify(brief)
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", str(exc))
        return jsonify({"error": f"Ollama not reachable: {reason}. Is Ollama running?"}), 503
    except json.JSONDecodeError as exc:
        return jsonify({"error": f"Model returned invalid JSON: {exc}. Try again."}), 422
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/run/nl/plan", methods=["POST"])
def api_run_nl_plan():
    """Generate a structured task plan using the selected supervisor.

    If supervisor is a CLI tool (Claude, Codex, etc.), it generates the plan
    via SupervisorAgent — the same path the CLI uses. This produces high-quality
    atomic plans because the expensive AI does the thinking.

    If supervisor is Chatbot or Manual (no CLI available), falls back to Ollama.
    """
    import logging

    data = request.get_json(force=True) or {}
    repo_root = str(data.get("repo_root", "")).strip()
    brief = data.get("brief") or {}

    # Accept both full brief and simple {goal: "..."} format
    if not brief or not brief.get("goal"):
        # Fallback: check if goal is at top level
        goal_direct = str(data.get("goal", "")).strip()
        if goal_direct:
            brief = {"goal": goal_direct}
        else:
            return jsonify({"error": "brief with goal is required"}), 400

    settings = state_store.load_settings()
    if not repo_root:
        repo_root = settings.get("repo_root", "").strip()

    # Build a rich goal string from the brief fields
    goal_parts = [str(brief.get("goal", "")).strip()]
    constraints = brief.get("constraints") or []
    if constraints:
        goal_parts.append("Constraints:\n" + "\n".join(f"- {c}" for c in constraints))
    acceptance = brief.get("acceptance_criteria") or []
    if acceptance:
        goal_parts.append("Acceptance criteria:\n" + "\n".join(f"- {a}" for a in acceptance))
    goal_text = "\n\n".join(goal_parts)

    # Smart goal routing: classify and add hint for the supervisor
    goal_category = _classify_goal(goal_text)
    if goal_category == "read":
        goal_text += (
            "\n\nCRITICAL: This is a READ-ONLY analysis request. "
            "You MUST use task type 'read' (not 'validate', not 'create', not 'modify'). "
            "The 'read' type reads the file and returns the answer — Aider is NOT invoked. "
            "Do NOT create new files. Do NOT modify existing files. "
            "Target the ACTUAL file that exists in the repo tree below — do NOT guess file names."
        )
    elif goal_category == "investigate":
        goal_text += (
            "\n\nCRITICAL: This requires investigation/analysis. "
            "Use task type 'investigate' (not 'validate', not 'create'). "
            "The 'investigate' type reads files and sends content to the supervisor for analysis — Aider is NOT invoked. "
            "Target ACTUAL files that exist in the repo tree below. "
            "You may follow with 'create' or 'modify' tasks if fixes are needed."
        )

    knowledge_ctx = _build_chat_context(repo_root) if repo_root else ""

    _root = Path(__file__).parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

    # Determine which supervisor to use for plan generation
    supervisor_type = settings.get("supervisor", "codex")
    # NOTE: Claude CLI needs -p flag for non-interactive piped mode.
    # Without it, subprocess.run(["claude"], input=prompt) hangs forever
    # waiting for an interactive terminal.
    cli_commands = {
        "codex":    "codex.cmd exec --skip-git-repo-check --color never",
        "claude":   "claude -p",
        "cursor":   "cursor",
        "windsurf": "windsurf",
    }
    supervisor_cmd = cli_commands.get(supervisor_type, "")
    if supervisor_type == "custom":
        supervisor_cmd = settings.get("supervisor_command", "").strip()

    # ── CLI supervisor path (Claude, Codex, etc.) ─────────────────────────
    if supervisor_cmd:
        try:
            from supervisor.agent import SupervisorAgent
            from context.repo_scanner import RepoScanner
            from parser.task_parser import TaskParser

            logger = logging.getLogger("bridge_app")
            repo_path = Path(repo_root)

            print(f"[PLAN] Scanning repo tree: {repo_path}", flush=True)
            repo_tree = RepoScanner(repo_path).scan()
            print(f"[PLAN] Repo tree: {len(repo_tree)} chars", flush=True)

            # Plan generation timeout: 180s for CLI supervisors.
            _plan_timeout = 180
            print(f"[PLAN] Generating plan via '{supervisor_cmd}' (timeout={_plan_timeout}s)", flush=True)
            print(f"[PLAN] Goal: {goal_text[:200]}...", flush=True)

            agent = SupervisorAgent(
                repo_root=repo_path,
                command=supervisor_cmd,
                logger=logger,
                timeout=_plan_timeout,
            )
            plan_text = agent.generate_plan(
                goal=goal_text,
                repo_tree=repo_tree,
                knowledge_context=knowledge_ctx or None,
                workflow_profile=settings.get("workflow_profile", "standard"),
            )
            print(f"[PLAN] Supervisor returned {len(plan_text)} chars", flush=True)

            parser = TaskParser()
            tasks_parsed = parser.parse(plan_text)

            # Convert Task objects to dicts for JSON response
            tasks = []
            for t in tasks_parsed:
                tasks.append({
                    "id": t.id,
                    "title": t.instruction[:60] if t.instruction else "",
                    "type": t.type,
                    "files": t.files,
                    "instruction": t.instruction,
                })

            plan_summary = f"Generated by {supervisor_type} — {len(tasks)} tasks"
            return jsonify({"plan_summary": plan_summary, "tasks": tasks, "goal_category": goal_category})

        except Exception as exc:
            logger.error("Supervisor plan generation failed: %s", exc)
            error_msg = str(exc)
            if "not logged in" in error_msg.lower() or "/login" in error_msg.lower():
                error_msg = (
                    "Claude CLI is not logged in. Open a terminal and run:\n"
                    "  claude /login\n"
                    "Then try again."
                )
                return jsonify({"error": error_msg}), 401
            if "timed out" in error_msg.lower():
                error_msg = (
                    f"Supervisor timed out after {_plan_timeout}s. "
                    "The repo may be too large, or the Claude CLI is not responding. "
                    "Test with: echo \"Reply with OK\" | claude -p"
                )
            return jsonify({"error": f"Plan generation failed: {error_msg}"}), 500

    # ── Ollama fallback (Chatbot / Manual — no CLI supervisor) ────────────
    import urllib.request
    import urllib.error
    from utils.relay_formatter import build_plan_prompt, parse_plan

    raw_model = str(settings.get("aider_model", "ollama/qwen2.5-coder:14b")).strip()
    if not raw_model.startswith("ollama/"):
        raw_model = "ollama/qwen2.5-coder:14b"
    ollama_model = raw_model[7:]

    prompt = build_plan_prompt(goal_text, knowledge_ctx, repo_root)

    body = json.dumps({
        "model": ollama_model,
        "messages": [
            {"role": "system", "content": "You are a software planning assistant. Output only the JSON requested."},
            {"role": "user",   "content": prompt},
        ],
        "stream": False,
        "keep_alive": "30s",
    }).encode("utf-8")

    req = urllib.request.Request(
        "http://localhost:11434/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        content = result.get("message", {}).get("content", "").strip()
        tasks = parse_plan(content)
        plan_summary = ""
        try:
            plan_summary = str(json.loads(content).get("plan_summary", ""))
        except Exception:
            pass
        return jsonify({"plan_summary": plan_summary, "tasks": tasks})
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", str(exc))
        return jsonify({"error": f"Ollama not reachable: {reason}. Is Ollama running?"}), 503
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 422
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/run/analyze", methods=["POST"])
def api_run_analyze():
    """Quick read-only analysis: read files + ask supervisor to analyze them.

    No Aider invoked. No git changes. Just reads files and returns the
    supervisor's analysis. For questions like "what features are in progress?"
    """
    import logging

    data = request.get_json(force=True) or {}
    repo_root = (data.get("repo_root") or "").strip()
    question = (data.get("question") or "").strip()
    files = data.get("files") or []

    if not repo_root:
        settings = state_store.load_settings()
        repo_root = settings.get("repo_root", "").strip()
    if not repo_root or not question:
        return jsonify({"error": "repo_root and question are required"}), 400

    _root = Path(__file__).parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

    repo_path = Path(repo_root)

    # Read file contents
    file_contents = []
    for fp in files:
        abs_fp = repo_path / fp
        if abs_fp.exists():
            try:
                text = abs_fp.read_text(encoding="utf-8", errors="replace")
                file_contents.append(f"=== {fp} ===\n{text}")
            except Exception as ex:
                file_contents.append(f"=== {fp} === (error: {ex})")
        else:
            file_contents.append(f"=== {fp} === (not found)")

    combined = "\n\n".join(file_contents) if file_contents else "(no files specified)"

    # Try supervisor CLI first
    settings = state_store.load_settings()
    supervisor_type = settings.get("supervisor", "codex")
    cli_commands = {
        "codex": "codex.cmd exec --skip-git-repo-check --color never",
        "claude": "claude",
        "cursor": "cursor",
        "windsurf": "windsurf",
    }
    supervisor_cmd = cli_commands.get(supervisor_type, "")
    if supervisor_type == "custom":
        supervisor_cmd = settings.get("supervisor_command", "").strip()

    prompt = (
        "You are analyzing project files. Read the content below and answer the question.\n\n"
        f"QUESTION: {question}\n\n"
        f"FILE CONTENT:\n{combined[:8000]}\n\n"
        "Provide a clear, structured answer."
    )

    if supervisor_cmd:
        try:
            from supervisor.agent import SupervisorAgent
            logger = logging.getLogger("bridge_app")
            agent = SupervisorAgent(repo_path, supervisor_cmd, logger, timeout=60)
            answer = agent._run(prompt)
            return jsonify({"answer": answer.strip(), "source": supervisor_type})
        except Exception as exc:
            return jsonify({"error": f"Supervisor analysis failed: {exc}"}), 500

    # Fallback to Ollama
    import urllib.request
    import urllib.error

    raw_model = str(settings.get("aider_model", "ollama/qwen2.5-coder:14b")).strip()
    if not raw_model.startswith("ollama/"):
        raw_model = "ollama/qwen2.5-coder:14b"
    ollama_model = raw_model[7:]

    body = json.dumps({
        "model": ollama_model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "keep_alive": "30s",
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/chat", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        answer = result.get("message", {}).get("content", "").strip()
        return jsonify({"answer": answer, "source": "ollama"})
    except Exception as exc:
        return jsonify({"error": f"Analysis failed: {exc}"}), 500


@app.route("/api/run/nl/plan/prompt", methods=["POST"])
def api_run_nl_plan_prompt():
    """Return the raw planning prompt for manual copy-paste into Claude."""
    data = request.get_json(force=True) or {}
    brief = data.get("brief") or {}
    goal = str(brief.get("goal") or data.get("goal") or "").strip()
    repo_root = str(data.get("repo_root", "")).strip()

    if not goal:
        return jsonify({"error": "goal is required"}), 400

    settings = state_store.load_settings()
    if not repo_root:
        repo_root = settings.get("repo_root", "").strip()

    try:
        from supervisor.agent import SupervisorAgent
        from context.repo_scanner import RepoScanner

        repo_path = Path(repo_root)
        repo_tree = RepoScanner(repo_path).scan()
        knowledge_ctx = _build_chat_context(repo_root) if repo_root else ""

        agent = SupervisorAgent(
            repo_root=repo_path,
            command="interactive",  # won't actually run
            logger=logging.getLogger("bridge_app"),
            timeout=30,
        )
        prompt = agent._build_plan_prompt(
            goal=goal,
            repo_tree=repo_tree,
            idea_text=None,
            feedback=None,
            knowledge_context=knowledge_ctx or None,
            workflow_profile=settings.get("workflow_profile", "standard"),
        )
        return jsonify({"prompt": prompt, "chars": len(prompt)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/run/nl/plan/confirm", methods=["POST"])
def api_run_nl_plan_confirm():
    """Write the confirmed plan JSON to the project's taskJsons/ directory and persist state."""
    import datetime

    data = request.get_json(force=True) or {}
    repo_root    = str(data.get("repo_root", "")).strip()
    tasks        = data.get("tasks") or []
    plan_summary = str(data.get("plan_summary", "")).strip()
    brief        = data.get("brief") or {}

    if not tasks:
        return jsonify({"error": "tasks are required"}), 400

    settings = state_store.load_settings()
    if not repo_root:
        repo_root = settings.get("repo_root", "").strip()

    plan_file = ""
    if repo_root:
        try:
            tasks_dir = Path(repo_root) / "taskJsons"
            tasks_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            plan_path = tasks_dir / f"nl_plan_{stamp}.json"
            plan_payload = {"plan_summary": plan_summary, "tasks": tasks}
            plan_path.write_text(json.dumps(plan_payload, indent=2), encoding="utf-8")
            plan_file = str(plan_path)
        except Exception as exc:
            return jsonify({"error": f"Could not write plan file: {exc}"}), 500

    # Persist the confirmed plan in NL state
    if repo_root:
        state_store.save_run_nl_state(repo_root, {
            "brief":        brief,
            "tasks":        tasks,
            "plan_summary": plan_summary,
            "plan_file":    plan_file,
            "plan_status":  "plan_confirmed",
            "status":       "plan_confirmed",
        })

    # Save to generated plans library (persists across restarts)
    goal = str(brief.get("goal", "")).strip() if isinstance(brief, dict) else ""
    plan_id = state_store.save_generated_plan({
        "goal": goal or plan_summary,
        "plan_summary": plan_summary,
        "plan_file": plan_file,
        "repo_root": repo_root,
        "task_count": len(tasks),
        "tasks": tasks,
    })

    return jsonify({"ok": True, "plan_file": plan_file, "plan_id": plan_id})


@app.route("/api/run/nl/state", methods=["GET"])
def api_run_nl_state_get():
    """Return the persisted NL conversation state for the current project."""
    repo_root = request.args.get("repo_root", "").strip()
    if not repo_root:
        settings = state_store.load_settings()
        repo_root = settings.get("repo_root", "").strip()
    state = state_store.load_run_nl_state(repo_root)
    return jsonify(state)


@app.route("/api/run/nl/state", methods=["POST"])
def api_run_nl_state_save():
    """Persist the NL conversation state for a project."""
    data = request.get_json(force=True) or {}
    repo_root = str(data.get("repo_root", "")).strip()
    if not repo_root:
        settings = state_store.load_settings()
        repo_root = settings.get("repo_root", "").strip()
    if not repo_root:
        return jsonify({"error": "repo_root is required"}), 400
    
    # Save the entire data blob (state_store will filter for allowed keys)
    state_store.save_run_nl_state(repo_root, data)
    return jsonify({"ok": True})


@app.route("/api/run/nl/state", methods=["DELETE"])
def api_run_nl_state_clear():
    """Clear the NL conversation state for a project."""
    data = request.get_json(force=True) or {}
    repo_root = str(data.get("repo_root", "")).strip()
    if not repo_root:
        settings = state_store.load_settings()
        repo_root = settings.get("repo_root", "").strip()
    if repo_root:
        state_store.clear_run_nl_state(repo_root)
    return jsonify({"ok": True})


def _manual_supervisor_dir() -> Optional[Path]:
    settings = state_store.load_settings()
    repo_root = settings.get("repo_root", "").strip()
    if not repo_root:
        return None
    return Path(repo_root) / "bridge_progress" / "manual_supervisor"


@app.route("/api/run/review/current")
def api_current_review():
    manual_dir = _manual_supervisor_dir()
    if manual_dir is None:
        return jsonify({"error": "repo_root not set."}), 400
    requests_dir = manual_dir / "requests"
    requests_dir.mkdir(parents=True, exist_ok=True)
    request_files = sorted(requests_dir.glob("task_*_request.json"))
    if not request_files:
        return jsonify({"pending": False})
    latest = request_files[0]
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500
    return jsonify({"pending": True, "request_file": str(latest), "request": payload})


@app.route("/api/run/review/submit", methods=["POST"])
def api_submit_review():
    manual_dir = _manual_supervisor_dir()
    if manual_dir is None:
        return jsonify({"error": "repo_root not set."}), 400

    payload = request.json or {}
    task_id = int(payload.get("task_id", 0))
    if task_id <= 0:
        return jsonify({"error": "task_id is required."}), 400

    decisions_dir = manual_dir / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    decision_path = decisions_dir / f"task_{task_id:04d}_decision.json"
    try:
        decision_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as ex:
        return jsonify({"error": str(ex)}), 500

    return jsonify({"ok": True, "decision_file": str(decision_path)})


@app.route("/api/run/stream")
def api_run_stream():
    """SSE endpoint — pushes all bridge events to the browser in real time."""
    client_q: queue.Queue = queue.Queue(maxsize=200)

    with _sse_lock:
        _sse_clients.append(client_q)

    def generate():
        try:
            while True:
                try:
                    payload = client_q.get(timeout=25)
                    yield f"data: {payload}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"
        finally:
            with _sse_lock:
                if client_q in _sse_clients:
                    _sse_clients.remove(client_q)

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── History ─────────────────────────────────────────────────────────────────────

@app.route("/api/history")
def api_get_history():
    """Return run history with optional server-side filtering.

    Query params:
      ?status=success|failure|running|stopped  — filter by run status
      ?q=<text>                                — case-insensitive search in goal
      ?limit=<n>                               — max entries to return (default: all)
    """
    history = state_store.load_history()

    status_filter = request.args.get("status", "").strip().lower()
    query = request.args.get("q", "").strip().lower()
    limit = request.args.get("limit", 0, type=int)

    if status_filter:
        history = [e for e in history if e.get("status", "").lower() == status_filter]
    if query:
        history = [e for e in history if query in e.get("goal", "").lower()]
    if limit > 0:
        history = history[:limit]

    return jsonify(history)


@app.route("/api/history/<entry_id>")
def api_get_history_entry(entry_id: str):
    for entry in state_store.load_history():
        if entry.get("id") == entry_id:
            return jsonify(entry)
    return jsonify({"error": "Not found"}), 404


@app.route("/api/history/<entry_id>", methods=["DELETE"])
def api_delete_history_entry(entry_id: str):
    state_store.delete_history_entry(entry_id)
    return jsonify({"ok": True})


@app.route("/api/history", methods=["DELETE"])
def api_clear_history():
    state_store.clear_history()
    return jsonify({"ok": True})


# ── Token usage ─────────────────────────────────────────────────────────────────

@app.route("/api/tokens")
def api_get_tokens():
    """Return the full persisted token log (all sessions + all-time totals)."""
    return jsonify(state_store.load_token_log())


@app.route("/api/tokens/current")
def api_get_tokens_current():
    """Return live token stats for the currently running (or last) run."""
    run = get_run()
    token_data = getattr(run, "token_data", None)
    if token_data:
        return jsonify(token_data)
    # Fall back to the most recent persisted session if no live data
    log = state_store.load_token_log()
    sessions = log.get("sessions", [])
    if sessions:
        return jsonify({"source": "persisted", "session": sessions[0], "totals": log.get("totals", {})})
    return jsonify({"source": "none", "session": None, "totals": log.get("totals", {})})


# ── Project-scoped reports (reads from <repo_root>/bridge_progress/) ─────────

def _project_repo_root(request_args) -> Optional[Path]:
    """Return the selected repo root from query args or saved settings."""
    repo_root = request_args.get("repo_root", "").strip()
    if not repo_root:
        # Fall back to the saved settings repo_root
        repo_root = state_store.load_settings().get("repo_root", "").strip()
    if not repo_root:
        return None
    return Path(repo_root)


def _project_progress_dir(request_args) -> Optional[Path]:
    """Return the bridge_progress/ path for the given repo_root query param."""
    repo_root = _project_repo_root(request_args)
    if repo_root is None:
        return None
    return repo_root / "bridge_progress"


def _normalize_knowledge_payload(raw: dict, repo_root: Path) -> dict:
    """Normalize multiple knowledge-file shapes to the UI's expected schema."""
    if not isinstance(raw, dict):
        return {}

    # Native bridge shape already matches what the Knowledge page expects.
    if "project" in raw and "files" in raw:
        return raw

    project_name = str(raw.get("project_name") or repo_root.name)
    project_type = str(raw.get("project_type") or "")
    primary_languages = raw.get("primary_languages") or []
    language = ", ".join(str(item) for item in primary_languages if str(item).strip())
    summary = str(raw.get("summary") or "")

    files: dict[str, dict] = {}
    for item in raw.get("file_registry", []) if isinstance(raw.get("file_registry"), list) else []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        files[path] = {
            "role": str(item.get("role") or "No description").strip(),
            "task_type": "scan",
            "last_modified": str(item.get("last_modified") or raw.get("generated_at") or "").strip(),
        }

    docs: list[dict] = []
    for rel_path in ("README.md", "AGENT_CONTEXT.md", "AI_UNDERSTANDING.md"):
        doc_path = repo_root / rel_path
        if doc_path.exists():
            docs.append({
                "path": rel_path,
                "summary": f"Detected in {repo_root.name}.",
            })

    patterns = []
    architecture = raw.get("architecture")
    if isinstance(architecture, dict):
        for pattern in architecture.get("patterns", []) if isinstance(architecture.get("patterns"), list) else []:
            pattern_text = str(pattern).strip()
            if pattern_text:
                patterns.append(pattern_text)

    features_done = []
    for item in raw.get("generated_directories", []) if isinstance(raw.get("generated_directories"), list) else []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        if role:
            features_done.append(role)

    constraints = raw.get("constraints") if isinstance(raw.get("constraints"), list) else []

    return {
        "project": {
            "name": project_name,
            "type": project_type,
            "language": language,
            "summary": summary,
            "repo_root": str(repo_root),
            "scanned": bool(files),
            "understanding_confirmed": True,
        },
        "files": files,
        "docs": docs,
        "patterns": patterns,
        "features_done": features_done,
        "suggested_next": [],
        "clarifications": [str(item) for item in constraints if str(item).strip()],
        "runs": [],
    }


@app.route("/api/reports/tokens")
def api_reports_tokens():
    """Token log for a specific project (reads bridge_progress/token_log.json)."""
    progress_dir = _project_progress_dir(request.args)
    if progress_dir is None:
        return jsonify({"error": "repo_root not set"}), 400
    token_file = progress_dir / "token_log.json"
    if not token_file.exists():
        return jsonify({"sessions": [], "totals": {}})
    try:
        return jsonify(json.loads(token_file.read_text(encoding="utf-8")))
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/reports/diagnostics")
def api_reports_diagnostics():
    """Run diagnostics for a specific project (reads bridge_progress/RUN_DIAGNOSTICS.json)."""
    progress_dir = _project_progress_dir(request.args)
    if progress_dir is None:
        return jsonify({"error": "repo_root not set"}), 400
    diag_file = progress_dir / "RUN_DIAGNOSTICS.json"
    if not diag_file.exists():
        return jsonify({"error": "No diagnostics available. Complete a run first."}), 404
    try:
        return jsonify(json.loads(diag_file.read_text(encoding="utf-8")))
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/reports/knowledge")
def api_reports_knowledge():
    """Project knowledge cache (reads bridge_progress/project_knowledge.json)."""
    repo_root = _project_repo_root(request.args)
    progress_dir = _project_progress_dir(request.args)
    if progress_dir is None or repo_root is None:
        return jsonify({"error": "repo_root not set"}), 400
    knowledge_file = progress_dir / "project_knowledge.json"
    if not knowledge_file.exists():
        return jsonify({})
    try:
        raw = json.loads(knowledge_file.read_text(encoding="utf-8"))
        return jsonify(_normalize_knowledge_payload(raw, repo_root))
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/knowledge/refresh", methods=["POST"])
def api_knowledge_refresh():
    """Re-scan the project and refresh knowledge + AI_UNDERSTANDING.md."""
    import logging
    _root = Path(__file__).parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

    from utils.onboarding_scanner import OnboardingScanner
    from utils.project_knowledge import load_knowledge, save_knowledge
    from context.project_understanding import ensure_project_understanding

    data = request.get_json(force=True) or {}
    repo_root = (data.get("repo_root") or "").strip()
    if not repo_root:
        settings = state_store.load_settings()
        repo_root = settings.get("repo_root", "").strip()
    if not repo_root:
        return jsonify({"error": "repo_root not set"}), 400

    repo_path = Path(repo_root)
    if not repo_path.is_dir():
        return jsonify({"error": f"Directory not found: {repo_root}"}), 400

    try:
        logger = logging.getLogger("bridge_app")

        # Load or create knowledge
        knowledge = load_knowledge(repo_path)

        # Run the scanner (re-scans all source files)
        scanner = OnboardingScanner(repo_path, logger)
        knowledge = scanner.run(knowledge)

        # Set last_refreshed timestamp
        from datetime import datetime
        knowledge.setdefault("project", {})["last_refreshed"] = datetime.now().isoformat(timespec="seconds")

        # Save knowledge
        save_knowledge(knowledge, repo_path)

        # Regenerate AI_UNDERSTANDING.md
        try:
            ensure_project_understanding(
                repo_root=repo_path,
                knowledge=knowledge,
                logger=logger,
                skip_source_scan=True,  # already scanned above
                allow_user_confirm=False,
                input_func=lambda _: "",
            )
        except Exception as _ue:
            logger.warning("Could not regenerate AI_UNDERSTANDING.md: %s", _ue)

        files_count = len(knowledge.get("files", {}))

        # Firebase sync: push project metadata to user's Firestore
        try:
            from utils.firebase_user_setup import get_user_setup
            _fbu = get_user_setup()
            if _fbu.is_configured() and _fbu.is_authenticated():
                _meta = {
                    "name": repo_path.name,
                    "language": knowledge.get("project", {}).get("language", ""),
                    "type": knowledge.get("project", {}).get("type", ""),
                    "file_count": len(knowledge.get("files", {})),
                    "patterns": knowledge.get("patterns", [])[:10],
                    "features_done": knowledge.get("features_done", [])[:20],
                    "last_refreshed": knowledge.get("project", {}).get("last_refreshed", ""),
                }
                _fbu.write_to_user_firestore(f"projects/{repo_path.name}/knowledge/latest", _meta)
        except Exception:
            pass

        return jsonify({
            "ok": True,
            "files_scanned": files_count,
            "last_refreshed": knowledge["project"]["last_refreshed"],
        })
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/reports/last_run")
def api_reports_last_run():
    """Last run summary (reads bridge_progress/last_run.json)."""
    progress_dir = _project_progress_dir(request.args)
    if progress_dir is None:
        return jsonify({"error": "repo_root not set"}), 400
    last_run_file = progress_dir / "last_run.json"
    if not last_run_file.exists():
        return jsonify({})
    try:
        return jsonify(json.loads(last_run_file.read_text(encoding="utf-8")))
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/reports/understanding")
def api_reports_understanding():
    """AI_UNDERSTANDING.md content for the selected project."""
    repo_root = _project_repo_root(request.args)
    progress_dir = _project_progress_dir(request.args)
    if progress_dir is None or repo_root is None:
        return jsonify({"content": "", "exists": False, "error": "repo_root not set"})
    understanding_file = progress_dir / "AI_UNDERSTANDING.md"
    if not understanding_file.exists():
        understanding_file = repo_root / "AI_UNDERSTANDING.md"
    if not understanding_file.exists():
        return jsonify({"content": "", "exists": False})
    try:
        content = understanding_file.read_text(encoding="utf-8")
        return jsonify({"content": content, "exists": True, "path": str(understanding_file)})
    except Exception as ex:
        return jsonify({"content": "", "exists": False, "error": str(ex)})

# ── Chat ───────────────────────────────────────────────────────────────────────

# Chat routes — MOVED to ui/api/chat_routes.py (chat_bp)

# ── Project routes ───────────────────────────────────────────────────────────

@app.route("/api/projects", methods=["GET"])
def api_projects_list():
    return jsonify(state_store.load_projects())


@app.route("/api/projects", methods=["POST"])
def api_projects_add():
    data = request.get_json(force=True) or {}
    path = (data.get("path") or "").strip()
    name = (data.get("name") or "").strip()
    if not path:
        return jsonify({"error": "path is required"}), 400
    state_store.add_project(path, name)
    return jsonify({"ok": True, "projects": state_store.load_projects()})


@app.route("/api/projects/switch", methods=["POST"])
def api_projects_switch():
    """Switch active project — updates repo_root in settings and promotes to top of list."""
    data = request.get_json(force=True) or {}
    path = (data.get("path") or "").strip()
    if not path:
        return jsonify({"error": "path is required"}), 400
    settings = state_store.load_settings()
    settings["repo_root"] = path
    state_store.save_settings(settings)
    state_store.add_project(path)          # promote to front
    return jsonify({"ok": True, "repo_root": path})


@app.route("/api/projects/<path:proj_path>", methods=["DELETE"])
def api_projects_delete(proj_path):
    state_store.remove_project(proj_path)
    return jsonify({"ok": True})


@app.route("/api/projects/rename", methods=["POST"])
def api_projects_rename():
    data = request.get_json(force=True) or {}
    path = (data.get("path") or "").strip()
    name = (data.get("name") or "").strip()
    if not path or not name:
        return jsonify({"error": "path and name are required"}), 400
    state_store.rename_project(path, name)
    return jsonify({"ok": True})


# Relay routes — MOVED to ui/api/relay_routes.py (relay_bp)
