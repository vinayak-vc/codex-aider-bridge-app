from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
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

app = Flask(__name__, template_folder=_template_folder)
app.config["JSON_SORT_KEYS"] = False

# Per-client SSE queue registry
_sse_clients: list[queue.Queue] = []
_sse_lock = threading.Lock()


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
    return render_template("index.html")


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
    state_store.save_settings(settings)

    run_id = state_store.add_history_entry({
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "goal": settings.get("goal", ""),
        "repo_root": settings.get("repo_root", ""),
        "aider_model": settings.get("aider_model", ""),
        "supervisor_command": settings.get("supervisor_command", ""),
        "dry_run": settings.get("dry_run", False),
        "status": "running",
        "tasks": 0,
        "elapsed": 0,
        "log": [],
    })

    def on_event(event_type: str, data: dict) -> None:
        _broadcast(event_type, data)
        if event_type in ("complete", "error", "stopped"):
            final = data.get("final") or {}
            state_store.update_history_entry(run_id, {
                "status": data.get("status", "failure") if event_type == "complete" else event_type,
                "tasks": final.get("tasks", len(run.tasks)),
                "elapsed": data.get("elapsed", 0),
                "log": run.log_lines[-state_store.MAX_LOG_LINES:],
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
        "tasks": list(run.tasks.values()),
        "command": run.command_preview,
        "total_tasks": run.total_tasks,
        "completed_tasks": run.completed_tasks,
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

def _project_progress_dir(request_args) -> Optional[Path]:
    """Return the bridge_progress/ path for the given repo_root query param."""
    repo_root = request_args.get("repo_root", "").strip()
    if not repo_root:
        # Fall back to the saved settings repo_root
        repo_root = state_store.load_settings().get("repo_root", "").strip()
    if not repo_root:
        return None
    return Path(repo_root) / "bridge_progress"


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
    progress_dir = _project_progress_dir(request.args)
    if progress_dir is None:
        return jsonify({"error": "repo_root not set"}), 400
    knowledge_file = progress_dir / "project_knowledge.json"
    if not knowledge_file.exists():
        return jsonify({})
    try:
        return jsonify(json.loads(knowledge_file.read_text(encoding="utf-8")))
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
