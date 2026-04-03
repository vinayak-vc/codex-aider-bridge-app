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

# Per-client SSE queue registry
_sse_clients: list[queue.Queue] = []
_sse_lock = threading.Lock()

# Knowledge context cache: {repo_root: (context_str, timestamp)}
_knowledge_cache: dict[str, tuple[str, float]] = {}
_KNOWLEDGE_CACHE_TTL = 60.0  # seconds


def _chat_project_key(repo_root: str) -> str:
    return str(repo_root or "").strip()


def _relay_task_status_label(status: str) -> str:
    mapping = {
        "not_started": "Not started",
        "skipped": "Skipped",
        "running": "Running",
        "waiting_review": "Waiting review",
        "approved": "Done",
        "success": "Done",
        "failed": "Failed",
        "failure": "Failed",
        "rework": "Rework",
        "retrying": "Retrying",
        "stopped": "Stopped",
        "dry-run": "Dry run",
    }
    return mapping.get(status, status.replace("_", " ").title())


def _relay_executable_task_count(tasks: list[dict]) -> int:
    count = 0
    for task in tasks:
        status = str(task.get("status", "")).strip().lower()
        if status == "skipped":
            continue
        count += 1
    return count


def _relay_current_session_id() -> str:
    return str(state_store.load_relay_ui_state().get("relay_session_id") or "").strip()


def _relay_task_matches_payload(task: dict, payload: dict) -> bool:
    payload_instruction = str(payload.get("instruction", "")).strip()
    payload_files = payload.get("files", [])
    if not isinstance(payload_files, list):
        return False

    task_instruction = str(task.get("instruction", "")).strip()
    task_files = [str(item) for item in task.get("files", [])]
    return payload_instruction == task_instruction and [str(item) for item in payload_files] == task_files


def _relay_matches_session(payload: dict, relay_session_id: str) -> bool:
    if not relay_session_id:
        return True
    return str(payload.get("relay_session_id", "")).strip() == relay_session_id


def _relay_request_file(repo_root: str, task_id: int, relay_session_id: str) -> Path:
    req_dir = Path(repo_root) / "bridge_progress" / "manual_supervisor" / "requests"
    if relay_session_id:
        return req_dir / f"task_{task_id:04d}_{relay_session_id}_request.json"
    return req_dir / f"task_{task_id:04d}_request.json"


def _relay_decision_file(repo_root: str, task_id: int, relay_session_id: str) -> Path:
    dec_dir = Path(repo_root) / "bridge_progress" / "manual_supervisor" / "decisions"
    if relay_session_id:
        return dec_dir / f"task_{task_id:04d}_{relay_session_id}_decision.json"
    return dec_dir / f"task_{task_id:04d}_decision.json"


def _relay_task_statuses(repo_root: str, current_tasks: list[dict], relay_session_id: str) -> dict[int, dict]:
    statuses: dict[int, dict] = {}
    current_by_id: dict[int, dict] = {}
    for task in current_tasks:
        task_id = int(task.get("id", 0))
        if task_id > 0:
            current_by_id[task_id] = task

    run = get_run()
    for task_id, task in run.tasks.items():
        task_status = str(task.get("status", "not_started")).strip() or "not_started"
        statuses[int(task_id)] = {
            "code": task_status,
            "label": _relay_task_status_label(task_status),
        }

    if not repo_root:
        return statuses

    completed_dir = Path(repo_root) / "bridge_progress" / "manual_supervisor" / "completed"
    if completed_dir.exists():
        pattern = f"task_*_{relay_session_id}_completed.json" if relay_session_id else "task_*_completed.json"
        for completed_file in sorted(completed_dir.glob(pattern)):
            try:
                payload = json.loads(completed_file.read_text(encoding="utf-8"))
                task_id = int(payload.get("task_id", 0))
                if task_id <= 0 or task_id in statuses:
                    continue
                if not _relay_matches_session(payload, relay_session_id):
                    continue
                current_task = current_by_id.get(task_id)
                if current_task is None or not _relay_task_matches_payload(current_task, payload):
                    continue
                statuses[task_id] = {
                    "code": "approved",
                    "label": _relay_task_status_label("approved"),
                }
            except Exception:
                pass

    requests_dir = Path(repo_root) / "bridge_progress" / "manual_supervisor" / "requests"
    if requests_dir.exists():
        pattern = f"task_*_{relay_session_id}_request.json" if relay_session_id else "task_*_request.json"
        for request_file in sorted(requests_dir.glob(pattern)):
            try:
                payload = json.loads(request_file.read_text(encoding="utf-8"))
                task_id = int(payload.get("task_id", 0))
                if task_id <= 0:
                    continue
                if not _relay_matches_session(payload, relay_session_id):
                    continue
                current_task = current_by_id.get(task_id)
                if current_task is None or not _relay_task_matches_payload(current_task, payload):
                    continue
                if task_id not in statuses:
                    statuses[task_id] = {
                        "code": "waiting_review",
                        "label": _relay_task_status_label("waiting_review"),
                    }
            except Exception:
                pass

    return statuses


def _relay_state_payload() -> dict:
    ui_state = state_store.load_relay_ui_state()
    tasks = state_store.load_relay_tasks()
    settings = state_store.load_settings()
    repo_root = str(ui_state.get("repo_root") or settings.get("repo_root") or "").strip()
    relay_session_id = str(ui_state.get("relay_session_id") or "").strip()
    task_statuses = _relay_task_statuses(repo_root, tasks, relay_session_id)

    decorated_tasks: list[dict] = []
    completed = 0
    for task in tasks:
        task_copy = dict(task)
        task_id = int(task_copy.get("id", 0))
        saved_status = str(task_copy.get("status", "")).strip().lower()
        if saved_status == "skipped":
            status_info = {
                "code": "skipped",
                "label": _relay_task_status_label("skipped"),
            }
        else:
            status_info = task_statuses.get(task_id, {
                "code": "not_started",
                "label": _relay_task_status_label("not_started"),
            })
        task_copy["status"] = status_info["code"]
        task_copy["status_label"] = status_info["label"]
        decorated_tasks.append(task_copy)
        if status_info["code"] in ("approved", "success"):
            completed += 1

    run = get_run()
    run_status = run.status
    live_run_active = run.is_running or run_status in ("running", "waiting_review", "paused")
    if decorated_tasks:
        if live_run_active:
            step = 3
        else:
            step = 2
    else:
        step = int(ui_state.get("step", 1) or 1)

    current_review: dict | None = None
    if live_run_active:
        try:
            manual_dir = Path(repo_root) / "bridge_progress" / "manual_supervisor" / "requests" if repo_root else None
            if manual_dir and manual_dir.exists():
                pattern = f"task_*_{relay_session_id}_request.json" if relay_session_id else "task_*_request.json"
                request_files = sorted(manual_dir.glob(pattern))
                for request_file in request_files:
                    payload = json.loads(request_file.read_text(encoding="utf-8"))
                    if _relay_matches_session(payload, relay_session_id):
                        current_review = payload
                        break
        except Exception:
            current_review = None

    return {
        "step": step,
        "goal": str(ui_state.get("goal") or settings.get("goal") or ""),
        "repo_root": repo_root,
        "aider_model": str(ui_state.get("aider_model") or settings.get("aider_model") or "ollama/qwen2.5-coder:14b"),
        "max_task_attempts": int(ui_state.get("max_task_attempts") or settings.get("max_task_retries", 2) + 1),
        "relay_session_id": relay_session_id,
        "prompt_output": str(ui_state.get("prompt_output") or ""),
        "plan_paste": str(ui_state.get("plan_paste") or ""),
        "tasks": decorated_tasks,
        "run_status": run_status,
        "is_running": run.is_running,
        "live_run_active": live_run_active,
        "completed_tasks": completed if decorated_tasks else run.completed_tasks,
        "total_tasks": _relay_executable_task_count(decorated_tasks),
        "current_review": current_review,
    }


class _ChatRuntime:
    def __init__(self) -> None:
        self.is_generating = False
        self.stop_event = threading.Event()
        self.model = ""
        self.error = ""
        self.updated_at = time.time()


_chat_runtime_lock = threading.Lock()
_chat_runtimes: dict[str, _ChatRuntime] = {}


def _sanitize_chat_messages(messages_raw: object) -> list[dict]:
    if not isinstance(messages_raw, list):
        return []

    messages: list[dict] = []
    for entry in messages_raw[-100:]:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role", "")).strip()
        content = str(entry.get("content", ""))
        if role not in ("user", "assistant"):
            continue
        messages.append({
            "role": role,
            "content": content,
        })
    return messages


def _get_chat_runtime(repo_root: str) -> _ChatRuntime:
    project_key = _chat_project_key(repo_root)
    with _chat_runtime_lock:
        runtime = _chat_runtimes.get(project_key)
        if runtime is None:
            runtime = _ChatRuntime()
            _chat_runtimes[project_key] = runtime
        return runtime


def _set_chat_runtime_idle(repo_root: str, error: str = "") -> None:
    runtime = _get_chat_runtime(repo_root)
    runtime.is_generating = False
    runtime.error = error
    runtime.stop_event.clear()
    runtime.updated_at = time.time()


def _build_chat_context(repo_root: str) -> str:
    knowledge_ctx = ""
    if not repo_root:
        return knowledge_ctx

    cached = _knowledge_cache.get(repo_root)
    if cached and (time.time() - cached[1]) < _KNOWLEDGE_CACHE_TTL:
        return cached[0]

    try:
        _root = Path(__file__).parent.parent
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))
        from utils.project_knowledge import load_knowledge, to_context_text
        knowledge = load_knowledge(Path(repo_root))
        knowledge_ctx = to_context_text(knowledge)
        _knowledge_cache[repo_root] = (knowledge_ctx, time.time())
    except Exception:
        pass

    return knowledge_ctx


def _build_chat_prompt_messages(
    repo_root: str,
    history: list[dict],
    message: str,
    raw_model: str,
) -> list[dict]:
    ollama_model = raw_model[7:] if raw_model.startswith("ollama/") else raw_model
    knowledge_ctx = _build_chat_context(repo_root)

    system_prompt = f"""You are a helpful AI coding assistant integrated into the Codex-Aider Bridge tool.
You help developers understand their codebase, plan features, debug issues, and discuss architecture.
You are conversational, concise, and precise.

IMPORTANT LIMITATIONS — disclose these when relevant:
- You CANNOT edit files or run code directly. For actual code changes, use the Run tab.
- Your project knowledge comes from a static scan and may not reflect the latest edits.
- You have NO internet access.
- Conversation history is saved per project and restored when that project is selected again.
- You run on the locally-installed Ollama model: {ollama_model}

{knowledge_ctx}

When suggesting code changes, be specific: name the file, the function, and exactly what to change.
When the user is ready to execute, suggest they go to the Run tab with a clear goal description."""

    messages = [{"role": "system", "content": system_prompt}]
    for item in history[-20:]:
        if item.get("role") in ("user", "assistant") and item.get("content"):
            messages.append({"role": item["role"], "content": item["content"]})
    messages.append({"role": "user", "content": message})
    return messages


def _run_chat_completion(
    repo_root: str,
    history: list[dict],
    message: str,
    raw_model: str,
) -> None:
    import urllib.request
    import urllib.error

    runtime = _get_chat_runtime(repo_root)
    ollama_model = raw_model[7:] if raw_model.startswith("ollama/") else raw_model
    messages = _build_chat_prompt_messages(repo_root, history, message, raw_model)

    body = json.dumps({
        "model": ollama_model,
        "messages": messages,
        "stream": True,
    }).encode("utf-8")

    req = urllib.request.Request(
        "http://localhost:11434/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    final_content = ""
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            for raw_line in resp:
                if runtime.stop_event.is_set():
                    break

                line = raw_line.strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue

                token = chunk.get("message", {}).get("content", "")
                if token:
                    final_content += token
                    current = state_store.load_chat_history(_chat_project_key(repo_root))
                    if current and current[-1].get("role") == "assistant":
                        current[-1]["content"] = final_content
                        state_store.save_chat_history(_chat_project_key(repo_root), current)
                if chunk.get("done"):
                    break

        if runtime.stop_event.is_set():
            current = state_store.load_chat_history(_chat_project_key(repo_root))
            if current and current[-1].get("role") == "assistant" and not str(current[-1].get("content", "")).strip():
                current.pop()
                state_store.save_chat_history(_chat_project_key(repo_root), current)
            _set_chat_runtime_idle(repo_root)
            return

        _set_chat_runtime_idle(repo_root)
    except urllib.error.HTTPError as exc:
        try:
            body_json = json.loads(exc.read().decode("utf-8", errors="replace"))
            detail = body_json.get("error") or f"HTTP {exc.code}"
        except Exception:
            detail = f"HTTP {exc.code}: {exc.reason}"
        history_now = state_store.load_chat_history(_chat_project_key(repo_root))
        if history_now and history_now[-1].get("role") == "assistant":
            history_now[-1]["content"] = f"Ollama error: {detail}"
            state_store.save_chat_history(_chat_project_key(repo_root), history_now)
        _set_chat_runtime_idle(repo_root, f"Ollama error: {detail}")
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", str(exc))
        detail = f"Ollama not reachable: {reason}. Is Ollama running?"
        history_now = state_store.load_chat_history(_chat_project_key(repo_root))
        if history_now and history_now[-1].get("role") == "assistant":
            history_now[-1]["content"] = detail
            state_store.save_chat_history(_chat_project_key(repo_root), history_now)
        _set_chat_runtime_idle(repo_root, detail)
    except Exception as exc:
        detail = str(exc)
        history_now = state_store.load_chat_history(_chat_project_key(repo_root))
        if history_now and history_now[-1].get("role") == "assistant":
            history_now[-1]["content"] = detail
            state_store.save_chat_history(_chat_project_key(repo_root), history_now)
        _set_chat_runtime_idle(repo_root, detail)


def _broadcast(event_type: str, data: dict) -> None:
    payload = json.dumps({"type": event_type, **data})
    with _sse_lock:
        for q in _sse_clients:
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass


# ── Pages ──────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    from flask import redirect
    return redirect("/dashboard")


# ── Page routes ────────────────────────────────────────────────────────────────

@app.route("/dashboard")
def page_dashboard():
    return render_template("dashboard.html", active_page="dashboard")


@app.route("/run")
def page_run():
    return render_template("run.html", active_page="run")


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
    state_store.save_settings(request.json or {})
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
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askopenfilename(
            parent=root,
            title="Select idea / brief file",
            filetypes=[("Markdown", "*.md"), ("Text", "*.txt"), ("All files", "*.*")],
        )
        root.destroy()
        return jsonify({"path": path or ""})
    except Exception as ex:
        return jsonify({"path": "", "error": str(ex)})


# ── Run lifecycle ───────────────────────────────────────────────────────────────

@app.route("/api/run", methods=["POST"])
def api_start_run():
    run = get_run()
    if run.is_running:
        return jsonify({"error": "A run is already in progress."}), 409

    settings = request.json or {}

    # Validate repo_root before doing anything — without it the bridge
    # subprocess would default to the PyInstaller temp dir (frozen) or the
    # source tree CWD (dev mode), neither of which is a user project.
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

    # Auto-register the repo as a known project
    if _repo:
        state_store.add_project(_repo)

    run_id = state_store.add_history_entry({
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
    })

    def on_event(event_type: str, data: dict) -> None:
        _broadcast(event_type, data)
        if event_type in ("task_update", "progress", "review_required", "relay_review_needed", "paused", "resumed", "start"):
            state_store.update_history_entry(run_id, {
                "status": run.status,
                "tasks": len(run.tasks),
                "tasks_detail": list(run.tasks.values()),
                "log": run.log_lines[-state_store.MAX_LOG_LINES:],
            })
        if event_type in ("complete", "error", "stopped"):
            final = data.get("final") or {}
            state_store.update_history_entry(run_id, {
                "status": data.get("status", "failure") if event_type == "complete" else event_type,
                "tasks": final.get("tasks", len(run.tasks)),
                "elapsed": data.get("elapsed", 0),
                "log": run.log_lines[-state_store.MAX_LOG_LINES:],
                "tasks_detail": list(run.tasks.values()),
            })
            run.remove_listener(on_event)

    run.add_listener(on_event)
    run.start(settings, run_id)

    return jsonify({"ok": True, "run_id": run_id})


@app.route("/api/run/stop", methods=["POST"])
def api_stop_run():
    get_run().stop()
    return jsonify({"ok": True})


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


@app.route("/api/run/brief", methods=["POST"])
def api_run_brief():
    """Convert a natural-language request into a structured execution brief via Ollama."""
    import urllib.request
    import urllib.error

    data = request.get_json(force=True) or {}
    message = str(data.get("message", "")).strip()
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
        '  "needs_clarification": false\n'
        "}\n\n"
        "Rules:\n"
        "- goal: single precise technical statement, not a question\n"
        "- assumptions: what you assume about the project or intent\n"
        "- constraints: what must NOT change or must be preserved\n"
        "- acceptance_criteria: observable, testable outcomes\n"
        "- clarification_questions: ONLY when something critical is ambiguous; leave [] when clear\n"
        "- needs_clarification: true only when clarification_questions is non-empty\n"
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
        return jsonify(brief)
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", str(exc))
        return jsonify({"error": f"Ollama not reachable: {reason}. Is Ollama running?"}), 503
    except json.JSONDecodeError as exc:
        return jsonify({"error": f"Model returned invalid JSON: {exc}. Try again."}), 422
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


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
    state_store.save_run_nl_state(repo_root, {
        "message": str(data.get("message", "")),
        "brief":   data.get("brief") or {},
        "status":  str(data.get("status", "drafting")),
    })
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

@app.route("/chat")
def page_chat():
    return render_template("chat.html", active_page="chat")


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """Stream a chat response from Ollama using project knowledge as context."""
    import urllib.request
    import urllib.error

    data = request.json or {}
    message = data.get("message", "").strip()
    history = data.get("history", [])   # [{role, content}, ...]

    if not message:
        return jsonify({"error": "message is required"}), 400

    settings = state_store.load_settings()
    # Allow per-request model override from the chat model selector
    request_model = (data.get("model") or "").strip()
    if request_model and not request_model.startswith("ollama/"):
        request_model = "ollama/" + request_model
    raw_model = request_model or settings.get("aider_model", "ollama/qwen2.5-coder:14b")
    # Ollama API wants the bare model name (no "ollama/" prefix)
    ollama_model = raw_model[7:] if raw_model.startswith("ollama/") else raw_model

    repo_root = settings.get("repo_root", "").strip()
    messages = _build_chat_prompt_messages(repo_root, history, message, raw_model)

    def generate():
        body = json.dumps({
            "model": ollama_model,
            "messages": messages,
            "stream": True,
        }).encode("utf-8")

        req = urllib.request.Request(
            "http://localhost:11434/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                for raw_line in resp:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                        token = chunk.get("message", {}).get("content", "")
                        if token:
                            yield f"data: {json.dumps({'token': token})}\n\n"
                        if chunk.get("done"):
                            yield f"data: {json.dumps({'done': True})}\n\n"
                    except json.JSONDecodeError:
                        pass
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8", errors="replace"))
                detail = body.get("error") or f"HTTP {exc.code}"
            except Exception:
                detail = f"HTTP {exc.code}: {exc.reason}"
            yield f"data: {json.dumps({'error': f'Ollama error: {detail}'})}\n\n"
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", str(exc))
            yield f"data: {json.dumps({'error': f'Ollama not reachable: {reason}. Is Ollama running?'})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/chat/start", methods=["POST"])
def api_chat_start():
    data = request.get_json(force=True) or {}
    message = str(data.get("message", "")).strip()
    history = _sanitize_chat_messages(data.get("history", []))
    repo_root = str(data.get("repo_root", "")).strip()

    if not message:
        return jsonify({"error": "message is required"}), 400

    if not repo_root:
        settings = state_store.load_settings()
        repo_root = str(settings.get("repo_root", "")).strip()

    if not repo_root:
        return jsonify({"error": "repo_root is required"}), 400

    settings = state_store.load_settings()
    request_model = str(data.get("model", "")).strip()
    if request_model and not request_model.startswith("ollama/"):
        request_model = "ollama/" + request_model
    raw_model = request_model or settings.get("aider_model", "ollama/qwen2.5-coder:14b")
    #if not raw_model.startswith("ollama/"):
    #    return jsonify({"error": "Chat only works with local Ollama models."}), 400

    runtime = _get_chat_runtime(repo_root)
    if runtime.is_generating:
        return jsonify({"error": "A chat response is already running for this project."}), 409

    persisted = history + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": ""},
    ]
    state_store.save_chat_history(_chat_project_key(repo_root), persisted)

    runtime.is_generating = True
    runtime.stop_event.clear()
    runtime.model = raw_model
    runtime.error = ""
    runtime.updated_at = time.time()

    worker = threading.Thread(
        target=_run_chat_completion,
        args=(repo_root, history, message, raw_model),
        daemon=True,
    )
    worker.start()

    return jsonify({"ok": True})


@app.route("/api/chat/stop", methods=["POST"])
def api_chat_stop():
    data = request.get_json(force=True) or {}
    repo_root = str(data.get("repo_root", "")).strip()
    if not repo_root:
        settings = state_store.load_settings()
        repo_root = str(settings.get("repo_root", "")).strip()

    if not repo_root:
        return jsonify({"error": "repo_root is required"}), 400

    runtime = _get_chat_runtime(repo_root)
    runtime.stop_event.set()
    runtime.updated_at = time.time()
    return jsonify({"ok": True})


@app.route("/api/chat/state", methods=["GET"])
def api_chat_state():
    repo_root = (request.args.get("repo_root") or "").strip()
    if not repo_root:
        settings = state_store.load_settings()
        repo_root = str(settings.get("repo_root", "")).strip()

    runtime = _get_chat_runtime(repo_root)
    return jsonify({
        "repo_root": repo_root,
        "messages": state_store.load_chat_history(_chat_project_key(repo_root)),
        "is_generating": runtime.is_generating,
        "model": runtime.model,
        "error": runtime.error,
        "updated_at": runtime.updated_at,
    })


@app.route("/api/chat/history", methods=["GET"])
def api_chat_history():
    repo_root = (request.args.get("repo_root") or "").strip()
    if not repo_root:
        settings = state_store.load_settings()
        repo_root = str(settings.get("repo_root", "")).strip()

    return jsonify({
        "repo_root": repo_root,
        "messages": state_store.load_chat_history(_chat_project_key(repo_root)),
    })


@app.route("/api/chat/history", methods=["POST"])
def api_chat_history_save():
    data = request.get_json(force=True) or {}
    repo_root = str(data.get("repo_root", "")).strip()
    messages_raw = data.get("messages", [])

    if not repo_root:
        return jsonify({"error": "repo_root is required"}), 400
    if not isinstance(messages_raw, list):
        return jsonify({"error": "messages must be a list"}), 400

    messages: list[dict] = []
    for entry in messages_raw[-100:]:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("role", "")).strip()
        content = str(entry.get("content", ""))
        if role not in ("user", "assistant"):
            continue
        messages.append({
            "role": role,
            "content": content,
        })

    state_store.save_chat_history(_chat_project_key(repo_root), messages)
    return jsonify({"ok": True, "count": len(messages)})


@app.route("/api/chat/history", methods=["DELETE"])
def api_chat_history_delete():
    repo_root = (request.args.get("repo_root") or "").strip()
    if not repo_root:
        settings = state_store.load_settings()
        repo_root = str(settings.get("repo_root", "")).strip()

    if not repo_root:
        return jsonify({"error": "repo_root is required"}), 400

    state_store.clear_chat_history(_chat_project_key(repo_root))
    return jsonify({"ok": True})


@app.route("/api/chat/status")
def api_chat_status():
    """Check if Ollama is running and the configured model is available."""
    import urllib.request, urllib.error
    settings = state_store.load_settings()
    raw_model = settings.get("aider_model", "")
    ollama_model = raw_model[7:] if raw_model.startswith("ollama/") else raw_model

    result = {"ollama_running": False, "model_available": False, "model": ollama_model, "error": None}
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            result["ollama_running"] = True
            models = [m.get("name", "") for m in data.get("models", [])]
            # Match if model name starts with the configured name (handles tags like :latest)
            result["model_available"] = any(
                m == ollama_model or m.startswith(ollama_model.split(":")[0])
                for m in models
            )
            result["available_models"] = models
    except urllib.error.URLError:
        result["error"] = "Ollama is not running"
    except Exception as exc:
        result["error"] = str(exc)
    return jsonify(result)


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


# ── AI Relay routes ───────────────────────────────────────────────────────────

@app.route("/relay")
def relay_page():
    return render_template("relay.html", active_page="relay")


@app.route("/api/relay/generate-prompt", methods=["POST"])
def api_relay_generate_prompt():
    """Return the plan prompt the user pastes into their web AI."""
    from utils.relay_formatter import build_plan_prompt
    from utils.project_knowledge import load_knowledge, to_context_text

    data      = request.get_json(force=True) or {}
    goal      = (data.get("goal") or "").strip()
    repo_root = (data.get("repo_root") or "").strip()
    if not goal:
        return jsonify({"error": "goal is required"}), 400

    knowledge_context = ""
    if repo_root:
        repo_path = Path(repo_root)
        try:
            knowledge = load_knowledge(repo_path)   # must receive a Path, not str
            knowledge_context = to_context_text(knowledge)
        except Exception:
            pass

        # Fallback: if the knowledge file hasn't been built yet (first use),
        # include a compact file-tree scan so the web AI knows the project layout.
        if not knowledge_context.strip():
            try:
                _root = Path(__file__).parent.parent
                if str(_root) not in sys.path:
                    sys.path.insert(0, str(_root))
                from context.repo_scanner import RepoScanner
                tree = RepoScanner(repo_path).scan()
                knowledge_context = f"FILE TREE:\n{tree}"
            except Exception:
                pass

    prompt = build_plan_prompt(goal, knowledge_context, repo_root)
    return jsonify({"prompt": prompt})


@app.route("/api/relay/import-plan", methods=["POST"])
def api_relay_import_plan():
    """Parse the web AI's plan response and persist the task list."""
    from utils.relay_formatter import parse_plan

    data     = request.get_json(force=True) or {}
    raw_text = data.get("raw_text", "")
    try:
        tasks = parse_plan(raw_text)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 422

    state_store.save_relay_tasks(tasks)
    ui_state = state_store.load_relay_ui_state()
    ui_state["step"] = 2
    ui_state["relay_session_id"] = uuid.uuid4().hex[:12]
    state_store.save_relay_ui_state(ui_state)
    return jsonify({"tasks": tasks, "count": len(tasks), "relay_session_id": ui_state["relay_session_id"]})


@app.route("/api/relay/tasks/skip", methods=["POST"])
def api_relay_skip_task():
    data = request.get_json(force=True) or {}
    task_id = int(data.get("task_id", 0))
    skip = bool(data.get("skip", True))

    if task_id <= 0:
        return jsonify({"error": "task_id is required"}), 400

    run = get_run()
    if run.is_running or run.status in ("running", "waiting_review", "paused"):
        return jsonify({"error": "Stop or finish the active run before changing skipped tasks."}), 409

    tasks = state_store.load_relay_tasks()
    task = next((item for item in tasks if int(item.get("id", 0)) == task_id), None)
    if task is None:
        return jsonify({"error": f"Task {task_id} was not found."}), 404

    current_status = str(task.get("status", "")).strip().lower()
    ui_state = state_store.load_relay_ui_state()
    known_statuses = _relay_task_statuses(
        str(ui_state.get("repo_root", "") or ""),
        tasks,
        str(ui_state.get("relay_session_id", "") or ""),
    )
    effective_status = known_statuses.get(task_id, {}).get("code") or current_status or "not_started"

    if skip:
        if effective_status in ("running", "waiting_review", "approved", "success"):
            return jsonify({"error": f"Task {task_id} cannot be skipped from its current status."}), 409
        task["status"] = "skipped"
    else:
        if current_status == "skipped":
            task.pop("status", None)

    state_store.save_relay_tasks(tasks)
    return jsonify(_relay_state_payload())


@app.route("/api/relay/state", methods=["GET"])
def api_relay_state():
    return jsonify(_relay_state_payload())


@app.route("/api/relay/state", methods=["POST"])
def api_relay_state_save():
    data = request.get_json(force=True) or {}
    state_store.save_relay_ui_state(data)
    return jsonify({"ok": True})


@app.route("/api/relay/state", methods=["DELETE"])
def api_relay_state_clear():
    state_store.clear_relay_ui_state()
    state_store.clear_relay_tasks()
    return jsonify({"ok": True})


@app.route("/api/relay/review-packet", methods=["GET"])
def api_relay_review_packet():
    """Build the review text for a completed task."""
    from utils.relay_formatter import build_review_packet

    task_id   = request.args.get("task_id", "")
    repo_root = (request.args.get("repo_root") or "").strip()
    goal      = (request.args.get("goal") or "").strip()
    relay_session_id = (request.args.get("relay_session_id") or _relay_current_session_id()).strip()

    if not task_id or not repo_root:
        return jsonify({"error": "task_id and repo_root are required"}), 400

    # Locate the request file written by bridge_runner
    req_file = _relay_request_file(repo_root, int(task_id), relay_session_id)
    if not req_file.exists():
        return jsonify({"error": f"Request file not found: {req_file.name}"}), 404

    try:
        req_data = json.loads(req_file.read_text(encoding="utf-8"))
    except Exception as exc:
        return jsonify({"error": f"Could not read request file: {exc}"}), 500

    tasks      = state_store.load_relay_tasks()
    total      = len(tasks)
    task       = next((t for t in tasks if str(t.get("id")) == str(task_id)), req_data)
    diff       = req_data.get("diff", "")
    validation = req_data.get("validation_result", "not run")
    attempt    = req_data.get("attempt", 1)
    max_retries = req_data.get("max_retries", 2)

    packet = build_review_packet(task, diff, validation, attempt, max_retries, total, goal)
    return jsonify({"packet": packet})


@app.route("/api/relay/submit-decision", methods=["POST"])
def api_relay_submit_decision():
    """Parse the web AI's review response and write the decision file."""
    from utils.relay_formatter import parse_decision

    data      = request.get_json(force=True) or {}
    raw_text  = data.get("raw_text", "")
    task_id   = data.get("task_id")
    repo_root = (data.get("repo_root") or "").strip()
    relay_session_id = str(data.get("relay_session_id") or _relay_current_session_id()).strip()

    if not task_id or not repo_root:
        return jsonify({"error": "task_id and repo_root are required"}), 400

    parsed = parse_decision(raw_text)
    if parsed["decision"] == "unparseable":
        return jsonify({"error": "Could not parse a decision from the text.", "raw": parsed.get("raw", "")}), 422

    # Map relay decision names → manual-supervisor decision names
    decision_map = {"approved": "pass", "rework": "rework", "failed": "fail"}
    ms_decision  = decision_map.get(parsed["decision"], parsed["decision"])

    decision_payload: dict = {"task_id": int(task_id), "decision": ms_decision, "relay_session_id": relay_session_id}
    if ms_decision == "rework" and "instruction" in parsed:
        decision_payload["instruction"] = parsed["instruction"]
    if ms_decision == "fail" and "reason" in parsed:
        decision_payload["reason"] = parsed["reason"]

    dec_dir  = Path(repo_root) / "bridge_progress" / "manual_supervisor" / "decisions"
    dec_dir.mkdir(parents=True, exist_ok=True)
    dec_file = _relay_decision_file(repo_root, int(task_id), relay_session_id)
    dec_file.write_text(json.dumps(decision_payload, indent=2), encoding="utf-8")

    return jsonify({"decision": ms_decision, "file": dec_file.name})


@app.route("/api/relay/replan-prompt", methods=["POST"])
def api_relay_replan_prompt():
    """Build the replan prompt for a failed task."""
    from utils.relay_formatter import build_replan_prompt

    data          = request.get_json(force=True) or {}
    task_id       = data.get("task_id")
    failed_reason = (data.get("failed_reason") or "").strip()
    repo_root     = (data.get("repo_root") or "").strip()
    goal          = (data.get("goal") or "").strip()
    relay_session_id = str(data.get("relay_session_id") or _relay_current_session_id()).strip()

    if not task_id or not repo_root:
        return jsonify({"error": "task_id and repo_root are required"}), 400

    req_file = _relay_request_file(repo_root, int(task_id), relay_session_id)
    diff     = ""
    task     = {"id": task_id, "title": f"Task {task_id}", "instruction": ""}
    if req_file.exists():
        try:
            req_data = json.loads(req_file.read_text(encoding="utf-8"))
            diff     = req_data.get("diff", "")
            tasks    = state_store.load_relay_tasks()
            found    = next((t for t in tasks if str(t.get("id")) == str(task_id)), None)
            if found:
                task = found
        except Exception:
            pass

    if not failed_reason:
        failed_reason = "Task marked as failed by reviewer."

    prompt = build_replan_prompt(task, failed_reason, diff, goal)
    return jsonify({"prompt": prompt})


@app.route("/api/relay/import-replan", methods=["POST"])
def api_relay_import_replan():
    """Parse replacement tasks from the web AI and splice them into the task list."""
    from utils.relay_formatter import parse_plan

    data     = request.get_json(force=True) or {}
    raw_text = data.get("raw_text", "")
    task_id  = data.get("task_id")

    if not task_id:
        return jsonify({"error": "task_id is required"}), 400

    try:
        replacement_tasks = parse_plan(raw_text)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 422

    tasks = state_store.load_relay_tasks()
    # Remove the failed task and everything after it, then splice in replacements
    pivot = next((i for i, t in enumerate(tasks) if str(t.get("id")) == str(task_id)), None)
    if pivot is not None:
        tasks = tasks[:pivot] + replacement_tasks
    else:
        tasks = tasks + replacement_tasks

    state_store.save_relay_tasks(tasks)
    return jsonify({"tasks": tasks, "count": len(tasks)})
