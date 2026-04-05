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
app.register_blueprint(git_bp)
app.register_blueprint(system_bp)

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
        "keep_alive": "30s",  # Unload model 30s after response to free VRAM for Aider
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


# ── Git API ────────────────────────────────────────────────────────────────────

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


@app.route("/api/git/status")
def api_git_status():
    repo = (request.args.get("repo_root") or "").strip()
    if not repo:
        settings = state_store.load_settings()
        repo = settings.get("repo_root", "").strip()
    if not repo:
        return jsonify({"error": "repo_root not set"}), 400

    try:
        # Branch name
        r = _git(repo, "branch", "--show-current")
        branch = r.stdout.strip() or "(detached)"

        # Status counts
        r = _git(repo, "status", "--porcelain")
        lines = [l for l in r.stdout.splitlines() if l.strip()]
        staged = sum(1 for l in lines if l[0] in "MADRC")
        unstaged = sum(1 for l in lines if len(l) > 1 and l[1] in "MADRC")
        untracked = sum(1 for l in lines if l.startswith("??"))
        is_clean = len(lines) == 0

        return jsonify({
            "branch": branch,
            "is_clean": is_clean,
            "staged": staged,
            "unstaged": unstaged,
            "untracked": untracked,
        })
    except FileNotFoundError:
        return jsonify({"error": "git not found"}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"error": "git command timed out"}), 500
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/git/branches")
def api_git_branches():
    repo = (request.args.get("repo_root") or "").strip()
    if not repo:
        settings = state_store.load_settings()
        repo = settings.get("repo_root", "").strip()
    if not repo:
        return jsonify({"error": "repo_root not set"}), 400

    try:
        r = _git(repo, "branch", "--no-color")
        branches = []
        current = ""
        for line in r.stdout.splitlines():
            name = line.lstrip("* ").strip()
            if not name:
                continue
            branches.append(name)
            if line.startswith("*"):
                current = name
        return jsonify({"current": current, "branches": branches})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/git/checkout", methods=["POST"])
def api_git_checkout():
    data = request.json or {}
    repo = (data.get("repo_root") or "").strip()
    branch = (data.get("branch") or "").strip()
    create = data.get("create", False)

    if not repo:
        settings = state_store.load_settings()
        repo = settings.get("repo_root", "").strip()
    if not repo or not branch:
        return jsonify({"error": "repo_root and branch are required"}), 400

    # Sanitize branch name
    if ".." in branch or branch.startswith("-"):
        return jsonify({"error": "Invalid branch name"}), 400

    try:
        if create:
            r = _git(repo, "checkout", "-b", branch)
        else:
            r = _git(repo, "checkout", branch)

        if r.returncode != 0:
            return jsonify({"error": r.stderr.strip() or f"Failed to checkout {branch}"}), 400

        return jsonify({"ok": True, "branch": branch})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/git/log")
def api_git_log():
    repo = (request.args.get("repo_root") or "").strip()
    limit = request.args.get("limit", 20, type=int)
    if not repo:
        settings = state_store.load_settings()
        repo = settings.get("repo_root", "").strip()
    if not repo:
        return jsonify({"error": "repo_root not set"}), 400

    try:
        r = _git(repo, "log", f"--max-count={min(limit, 100)}",
                 "--format=%H%n%h%n%s%n%an%n%aI", "--no-color")
        if r.returncode != 0:
            return jsonify({"commits": []})

        lines = r.stdout.strip().splitlines()
        commits = []
        for i in range(0, len(lines), 5):
            if i + 4 >= len(lines):
                break
            sha = lines[i]
            short = lines[i + 1]
            message = lines[i + 2]
            author = lines[i + 3]
            ts = lines[i + 4]
            commits.append({
                "sha": sha,
                "short_sha": short,
                "message": message,
                "author": author,
                "timestamp": ts,
                "is_bridge_task": message.startswith("bridge: task"),
            })

        return jsonify({"commits": commits})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/git/diff")
def api_git_diff():
    repo = (request.args.get("repo_root") or "").strip()
    sha = (request.args.get("sha") or "").strip()
    staged = request.args.get("staged", "false").lower() == "true"
    file_path = (request.args.get("file") or "").strip()

    if not repo:
        settings = state_store.load_settings()
        repo = settings.get("repo_root", "").strip()
    if not repo:
        return jsonify({"error": "repo_root not set"}), 400

    try:
        if sha:
            # Diff for a specific commit
            r = _git(repo, "diff", f"{sha}~1..{sha}", "--stat")
            stat = r.stdout.strip()
            r = _git(repo, "diff", f"{sha}~1..{sha}", timeout=30)
            diff_text = r.stdout[:8000]
        elif staged:
            r = _git(repo, "diff", "--cached", "--stat")
            stat = r.stdout.strip()
            r = _git(repo, "diff", "--cached")
            diff_text = r.stdout[:8000]
        elif file_path:
            r = _git(repo, "diff", "--", file_path)
            stat = ""
            diff_text = r.stdout[:8000]
        else:
            # Working tree diff
            r = _git(repo, "diff", "--stat")
            stat = r.stdout.strip()
            r = _git(repo, "diff")
            diff_text = r.stdout[:8000]

        # Parse stat into file list
        files = []
        for line in stat.splitlines():
            parts = line.strip().split("|")
            if len(parts) == 2:
                fname = parts[0].strip()
                changes = parts[1].strip()
                ins = changes.count("+")
                dels = changes.count("-")
                files.append({"path": fname, "insertions": ins, "deletions": dels})

        return jsonify({"diff": diff_text, "files": files, "stat": stat})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


# ── VS Code Integration ───────────────────────────────────────────────────────

@app.route("/api/vscode/open", methods=["POST"])
def api_vscode_open():
    """Open a file or folder in VS Code."""
    data = request.json or {}
    target = (data.get("path") or "").strip()
    repo_root = (data.get("repo_root") or "").strip()

    if not target and not repo_root:
        settings = state_store.load_settings()
        repo_root = settings.get("repo_root", "").strip()

    # Determine what to open
    open_path = target or repo_root
    if not open_path:
        return jsonify({"error": "No path specified"}), 400

    # If target is relative, make it absolute using repo_root
    from pathlib import Path as _Path
    p = _Path(open_path)
    if not p.is_absolute() and repo_root:
        p = _Path(repo_root) / p

    try:
        cmd = ["code"]
        if p.is_file():
            cmd.append("--goto")
        cmd.append(str(p))
        subprocess.Popen(cmd, creationflags=_WIN_CREATE_FLAGS)
        return jsonify({"ok": True, "path": str(p)})
    except FileNotFoundError:
        return jsonify({"error": "VS Code ('code' command) not found. Install it or add to PATH."}), 404
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/git/gitignore", methods=["POST"])
def api_git_gitignore():
    """Add a pattern to .gitignore."""
    data = request.json or {}
    repo = (data.get("repo_root") or "").strip()
    pattern = (data.get("pattern") or "").strip()

    if not repo:
        settings = state_store.load_settings()
        repo = settings.get("repo_root", "").strip()
    if not repo or not pattern:
        return jsonify({"error": "repo_root and pattern are required"}), 400

    gitignore_path = Path(repo) / ".gitignore"
    try:
        existing = ""
        if gitignore_path.exists():
            existing = gitignore_path.read_text(encoding="utf-8")

        # Check if pattern already exists
        lines = existing.splitlines()
        if pattern in lines:
            return jsonify({"ok": True, "message": "Already in .gitignore"})

        # Append
        separator = "\n" if existing and not existing.endswith("\n") else ""
        gitignore_path.write_text(existing + separator + pattern + "\n", encoding="utf-8")
        return jsonify({"ok": True, "pattern": pattern})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


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
            state_store.update_history_entry(run_id, {
                "status": data.get("status", "failure") if event_type == "complete" else event_type,
                "tasks": final.get("tasks", len(run.tasks)),
                "elapsed": data.get("elapsed", 0),
                "log": run.log_lines[-state_store.MAX_LOG_LINES:],
                "tasks_detail": list(run.tasks.values()),
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


# ── Firebase Auth & Sync ─────────────────────────────────────────────────────

@app.route("/api/auth/status")
def api_auth_status():
    try:
        from utils.firebase_sync import get_firebase_sync
        sync = get_firebase_sync()
        if not sync:
            return jsonify({"logged_in": False, "configured": False})
        return jsonify({**sync.get_user_info(), "configured": True})
    except Exception:
        return jsonify({"logged_in": False, "configured": False})


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    try:
        from utils.firebase_sync import get_firebase_sync, AuthError
        sync = get_firebase_sync()
        if not sync:
            return jsonify({"error": "Firebase not configured. Place firebase_config.json in the app data directory."}), 400
        result = sync.login_with_google()
        if result.get("ok"):
            sync.update_profile()
        return jsonify(result)
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    try:
        from utils.firebase_sync import get_firebase_sync
        sync = get_firebase_sync()
        if sync:
            sync.logout()
        return jsonify({"ok": True})
    except Exception:
        return jsonify({"ok": True})


@app.route("/api/sync/enable", methods=["POST"])
def api_sync_enable():
    from utils.firebase_sync import get_firebase_sync
    sync = get_firebase_sync()
    if not sync or not sync.is_authenticated():
        return jsonify({"error": "Login first to enable sync."}), 400
    sync.set_enabled(True)
    # Flush any queued operations
    flushed = sync.flush_queue()
    return jsonify({"ok": True, "flushed": flushed})


@app.route("/api/sync/disable", methods=["POST"])
def api_sync_disable():
    from utils.firebase_sync import get_firebase_sync
    sync = get_firebase_sync()
    if sync:
        sync.set_enabled(False)
    return jsonify({"ok": True})


@app.route("/api/sync/status")
def api_sync_status():
    from utils.firebase_sync import get_firebase_sync
    sync = get_firebase_sync()
    if not sync:
        return jsonify({"enabled": False, "authenticated": False, "configured": False})
    status = sync.get_sync_status()
    status["configured"] = True
    return jsonify(status)


@app.route("/api/sync/push", methods=["POST"])
def api_sync_push():
    """Force manual sync of current project data."""
    from utils.firebase_sync import get_firebase_sync
    sync = get_firebase_sync()
    if not sync or not sync.is_enabled():
        return jsonify({"error": "Sync not enabled."}), 400

    data = request.json or {}
    repo_root = (data.get("repo_root") or "").strip()
    if not repo_root:
        settings = state_store.load_settings()
        repo_root = settings.get("repo_root", "").strip()

    if repo_root:
        project_name = Path(repo_root).name
        # Push knowledge
        try:
            from utils.project_knowledge import load_knowledge
            knowledge = load_knowledge(Path(repo_root))
            sync.push_project_meta(project_name, knowledge)
        except Exception:
            pass
        # Push settings
        sync.push_settings(state_store.load_settings())
        # Flush queue
        flushed = sync.flush_queue()
        return jsonify({"ok": True, "project": project_name, "flushed": flushed})

    return jsonify({"ok": True, "flushed": sync.flush_queue()})


@app.route("/api/sync/export")
def api_sync_export():
    """Export all user's cloud data (GDPR data portability)."""
    from utils.firebase_sync import get_firebase_sync
    sync = get_firebase_sync()
    if not sync or not sync.is_authenticated():
        return jsonify({"error": "Not authenticated"}), 401
    try:
        data = sync.export_all_data()
        return jsonify(data)
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/sync/delete-account", methods=["POST"])
def api_sync_delete_account():
    """Delete all cloud data and logout."""
    from utils.firebase_sync import get_firebase_sync
    sync = get_firebase_sync()
    if sync:
        sync.delete_all_data()
    return jsonify({"ok": True})


@app.route("/api/version")
def api_version():
    try:
        from utils.version import get_version_info
        return jsonify(get_version_info())
    except Exception:
        return jsonify({"version": "0.4.62", "commit": "", "branch": ""})


# ── Per-User Firebase Setup ───────────────────────────────────────────────────

@app.route("/api/firebase/status")
def api_firebase_status():
    """Check if user's own Firebase project is configured."""
    try:
        from utils.firebase_user_setup import get_user_setup
        return jsonify(get_user_setup().get_status())
    except Exception as ex:
        return jsonify({"configured": False, "error": str(ex)})


@app.route("/api/firebase/setup", methods=["POST"])
def api_firebase_setup():
    """Save and validate user's Firebase config."""
    from utils.firebase_user_setup import get_user_setup, SetupError
    data = request.json or {}
    try:
        result = get_user_setup().save_config(data)
        return jsonify(result)
    except SetupError as ex:
        return jsonify({"error": str(ex)}), 400
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/firebase/test", methods=["POST"])
def api_firebase_test():
    """Test connection to user's Firestore."""
    from utils.firebase_user_setup import get_user_setup
    try:
        result = get_user_setup().test_connection()
        return jsonify(result)
    except Exception as ex:
        return jsonify({"ok": False, "error": str(ex)}), 500


@app.route("/api/firebase/login", methods=["POST"])
def api_firebase_login():
    """Login to user's Firebase project via Google OAuth."""
    from utils.firebase_user_setup import get_user_setup, SetupError
    try:
        result = get_user_setup().login()
        return jsonify(result)
    except SetupError as ex:
        return jsonify({"error": str(ex)}), 400
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/firebase/logout", methods=["POST"])
def api_firebase_logout():
    """Logout from user's Firebase project."""
    from utils.firebase_user_setup import get_user_setup
    get_user_setup().logout()
    return jsonify({"ok": True})


@app.route("/api/firebase/clear", methods=["POST"])
def api_firebase_clear():
    """Remove user's Firebase config entirely."""
    from utils.firebase_user_setup import get_user_setup
    get_user_setup().clear_config()
    return jsonify({"ok": True})


@app.route("/dashboard/cloud")
def page_cloud_dashboard():
    """Serve the personal cloud dashboard with user's Firebase config injected."""
    from utils.firebase_user_setup import get_user_setup
    setup = get_user_setup()
    config = {}
    if setup.is_configured():
        config = {
            "apiKey": setup._config.get("apiKey", ""),
            "authDomain": setup._config.get("authDomain", ""),
            "projectId": setup._config.get("projectId", ""),
        }
    # Inject config into the template
    html = render_template("cloud_dashboard.html")
    config_script = f"<script>window.__FIREBASE_CONFIG__ = {json.dumps(config)};</script>"
    html = html.replace("</head>", f"{config_script}</head>")
    return html


@app.route("/api/firebase/dashboard-url")
def api_firebase_dashboard_url():
    """Get the URL for the user's personal cloud dashboard."""
    from utils.firebase_user_setup import get_user_setup
    setup = get_user_setup()
    if not setup.is_configured():
        return jsonify({"url": None, "local_url": "/dashboard/cloud"})
    return jsonify({
        "url": f"https://{setup._config.get('projectId', '')}.web.app",
        "local_url": "/dashboard/cloud",
        "project_id": setup._config.get("projectId", ""),
    })


@app.route("/api/firebase/export-dashboard")
def api_firebase_export_dashboard():
    """Export the dashboard as a standalone HTML file for Firebase Hosting deployment."""
    from utils.firebase_user_setup import get_user_setup
    setup = get_user_setup()
    config = {}
    if setup.is_configured():
        config = {
            "apiKey": setup._config.get("apiKey", ""),
            "authDomain": setup._config.get("authDomain", ""),
            "projectId": setup._config.get("projectId", ""),
        }
    html = render_template("cloud_dashboard.html")
    config_script = f"<script>window.__FIREBASE_CONFIG__ = {json.dumps(config)};</script>"
    html = html.replace("</head>", f"{config_script}</head>")
    return Response(html, mimetype="text/html",
                    headers={"Content-Disposition": "attachment; filename=index.html"})


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
    return jsonify({"plans": state_store.load_plan_favorites()})


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


@app.route("/api/run/preflight", methods=["POST"])
def api_run_preflight():
    """Run pre-launch validation checks."""
    data = request.json or {}
    result = setup_checker.check_preflight(data)
    return jsonify(result)


@app.route("/api/system/gpu-processes")
def api_gpu_processes():
    """List all processes using the GPU."""
    from . import setup_checker
    try:
        procs = setup_checker.check_gpu_processes()
        gpu = setup_checker.check_gpu()
        return jsonify({
            "processes": procs,
            "gpu": gpu,
            "total_gpu_mem_mb": sum(p.get("memory_mb", 0) for p in procs),
        })
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/system/kill-process", methods=["POST"])
def api_kill_process():
    """Kill a process by PID."""
    data = request.json or {}
    pid = data.get("pid", 0)
    if not pid:
        return jsonify({"error": "pid is required"}), 400

    # Safety: never kill critical system processes
    protected = {"explorer.exe", "System", "csrss.exe", "winlogon.exe",
                 "svchost.exe", "lsass.exe", "smss.exe", "services.exe"}

    try:
        import os
        import signal
        # Get process name first to check protection
        if sys.platform == "win32":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=3,
                creationflags=_WIN_CREATE_FLAGS,
            )
            pname = ""
            if result.stdout.strip():
                pname = result.stdout.strip().split(",")[0].strip('"')
            if pname.lower() in {p.lower() for p in protected}:
                return jsonify({"error": f"Cannot kill system process: {pname}"}), 403

            # Kill it
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, timeout=5,
                           creationflags=_WIN_CREATE_FLAGS)
        else:
            os.kill(int(pid), signal.SIGTERM)

        return jsonify({"ok": True, "pid": pid})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/system/unload-model", methods=["POST"])
def api_unload_model():
    """Tell Ollama to unload the current model from VRAM."""
    import urllib.request
    import urllib.error

    try:
        # Send a generate request with keep_alive=0 to force unload
        settings = state_store.load_settings()
        model = settings.get("aider_model", "").replace("ollama/", "")
        if not model:
            return jsonify({"error": "No model configured"}), 400

        body = json.dumps({
            "model": model,
            "keep_alive": 0,
        }).encode("utf-8")

        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()

        return jsonify({"ok": True, "model": model, "message": f"Model {model} unloaded from VRAM"})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/system/benchmark", methods=["POST"])
def api_benchmark():
    """Benchmark Ollama model speed — measures tokens/second."""
    import urllib.request
    import urllib.error

    data = request.json or {}
    settings = state_store.load_settings()
    model = data.get("model") or settings.get("aider_model", "")
    model = model.replace("ollama/", "")
    if not model:
        return jsonify({"error": "No model specified"}), 400

    prompt = "Write a Python function called reverse_string that takes a string and returns it reversed. Handle edge cases like empty strings and None."

    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "keep_alive": "5s",
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        import time as _time
        start = _time.time()
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        elapsed = _time.time() - start

        eval_count = result.get("eval_count", 0)
        eval_duration = result.get("eval_duration", 0)  # nanoseconds

        if eval_duration > 0:
            tok_per_sec = round(eval_count / (eval_duration / 1e9), 1)
        elif elapsed > 0:
            tok_per_sec = round(eval_count / elapsed, 1)
        else:
            tok_per_sec = 0

        # Estimate task time: ~500 tokens per typical task
        est_task_sec = round(500 / max(1, tok_per_sec))

        return jsonify({
            "model": model,
            "tokens_generated": eval_count,
            "elapsed_seconds": round(elapsed, 1),
            "tokens_per_second": tok_per_sec,
            "estimated_task_seconds": est_task_sec,
            "speed_tier": "fast" if tok_per_sec >= 20 else ("medium" if tok_per_sec >= 5 else "slow"),
        })
    except urllib.error.URLError as exc:
        return jsonify({"error": f"Ollama not reachable: {exc}"}), 503
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/system/recommend-model")
def api_recommend_model():
    """Detect system specs and recommend the best Ollama coding model."""
    _root = Path(__file__).parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    from utils.model_advisor import recommend
    try:
        result = recommend()
        return jsonify(result)
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


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
        fix_signals = ["fix", "repair", "resolve", "patch", "update", "change", "add", "create", "implement"]
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

    return jsonify({"ok": True, "plan_file": plan_file})


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
    # AI Relay is now inline on the Run page (Milestone B).
    # Keep this route so old bookmarks / links don't 404.
    from flask import redirect
    return redirect("/run", code=302)


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
