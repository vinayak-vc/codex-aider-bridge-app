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


@app.route("/api/reports/understanding")
def api_reports_understanding():
    """AI_UNDERSTANDING.md content (reads bridge_progress/AI_UNDERSTANDING.md)."""
    progress_dir = _project_progress_dir(request.args)
    if progress_dir is None:
        return jsonify({"content": "", "exists": False, "error": "repo_root not set"})
    understanding_file = progress_dir / "AI_UNDERSTANDING.md"
    if not understanding_file.exists():
        return jsonify({"content": "", "exists": False})
    try:
        content = understanding_file.read_text(encoding="utf-8")
        return jsonify({"content": content, "exists": True})
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
    raw_model = settings.get("aider_model", "ollama/qwen2.5-coder:14b")
    # Ollama API wants the bare model name (no "ollama/" prefix)
    ollama_model = raw_model[7:] if raw_model.startswith("ollama/") else raw_model

    # --- Reject non-Ollama models (no API key in the UI) ---
    if not raw_model.startswith("ollama/"):
        return jsonify({
            "error": (
                f"Chat only works with local Ollama models (e.g. ollama/qwen2.5-coder:14b). "
                f"Your configured model '{raw_model}' requires an API key which the chat "
                f"endpoint does not manage. Switch to an Ollama model in Run settings first."
            )
        }), 400

    # --- Build project context from knowledge file ---
    repo_root = settings.get("repo_root", "").strip()
    knowledge_ctx = ""
    if repo_root:
        try:
            _root = Path(__file__).parent.parent
            if str(_root) not in sys.path:
                sys.path.insert(0, str(_root))
            from utils.project_knowledge import load_knowledge, to_context_text
            knowledge = load_knowledge(Path(repo_root))
            knowledge_ctx = to_context_text(knowledge)
        except Exception:
            pass

    system_prompt = f"""You are a helpful AI coding assistant integrated into the Codex-Aider Bridge tool.
You help developers understand their codebase, plan features, debug issues, and discuss architecture.
You are conversational, concise, and precise.

IMPORTANT LIMITATIONS — disclose these when relevant:
- You CANNOT edit files or run code directly. For actual code changes, use the Run tab.
- Your project knowledge comes from a static scan and may not reflect the latest edits.
- You have NO internet access.
- Conversation history is NOT saved after the page is refreshed.
- You run on the locally-installed Ollama model: {ollama_model}

{knowledge_ctx}

When suggesting code changes, be specific: name the file, the function, and exactly what to change.
When the user is ready to execute, suggest they go to the Run tab with a clear goal description."""

    messages = [{"role": "system", "content": system_prompt}]
    for h in history[-20:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": message})

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


# ── AI Relay routes ───────────────────────────────────────────────────────────

@app.route("/relay")
def relay_page():
    return render_template("relay.html", active_page="relay")


@app.route("/api/relay/generate-prompt", methods=["POST"])
def api_relay_generate_prompt():
    """Return the plan prompt the user pastes into their web AI."""
    from utils.relay_formatter import build_plan_prompt
    from utils.project_knowledge import load_knowledge, to_context_text

    data     = request.get_json(force=True) or {}
    goal     = (data.get("goal") or "").strip()
    repo_root = (data.get("repo_root") or "").strip()
    if not goal:
        return jsonify({"error": "goal is required"}), 400

    knowledge_context = ""
    if repo_root:
        try:
            knowledge = load_knowledge(repo_root)
            knowledge_context = to_context_text(knowledge)
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
    return jsonify({"tasks": tasks, "count": len(tasks)})


@app.route("/api/relay/review-packet", methods=["GET"])
def api_relay_review_packet():
    """Build the review text for a completed task."""
    from utils.relay_formatter import build_review_packet

    task_id   = request.args.get("task_id", "")
    repo_root = (request.args.get("repo_root") or "").strip()
    goal      = (request.args.get("goal") or "").strip()

    if not task_id or not repo_root:
        return jsonify({"error": "task_id and repo_root are required"}), 400

    # Locate the request file written by bridge_runner
    req_dir  = Path(repo_root) / "bridge_progress" / "manual_supervisor" / "requests"
    req_file = req_dir / f"task_{int(task_id):04d}_request.json"
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

    if not task_id or not repo_root:
        return jsonify({"error": "task_id and repo_root are required"}), 400

    parsed = parse_decision(raw_text)
    if parsed["decision"] == "unparseable":
        return jsonify({"error": "Could not parse a decision from the text.", "raw": parsed.get("raw", "")}), 422

    # Map relay decision names → manual-supervisor decision names
    decision_map = {"approved": "pass", "rework": "rework", "failed": "fail"}
    ms_decision  = decision_map.get(parsed["decision"], parsed["decision"])

    decision_payload: dict = {"task_id": int(task_id), "decision": ms_decision}
    if ms_decision == "rework" and "instruction" in parsed:
        decision_payload["instruction"] = parsed["instruction"]
    if ms_decision == "fail" and "reason" in parsed:
        decision_payload["reason"] = parsed["reason"]

    dec_dir  = Path(repo_root) / "bridge_progress" / "manual_supervisor" / "decisions"
    dec_dir.mkdir(parents=True, exist_ok=True)
    dec_file = dec_dir / f"task_{int(task_id):04d}_decision.json"
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

    if not task_id or not repo_root:
        return jsonify({"error": "task_id and repo_root are required"}), 400

    req_dir  = Path(repo_root) / "bridge_progress" / "manual_supervisor" / "requests"
    req_file = req_dir / f"task_{int(task_id):04d}_request.json"
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
