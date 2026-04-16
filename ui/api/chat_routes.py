"""Chat API blueprint — Ollama chat, history, runtime state.

Extracted from ui/app.py for maintainability.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

from flask import Blueprint, Response, jsonify, render_template, request, stream_with_context

from ui import state_store
from ui.app_state import build_chat_context

chat_bp = Blueprint("chat", __name__)


# ── Chat runtime state ──────────────────────────────────────────────────────

class _ChatRuntime:
    def __init__(self) -> None:
        self.is_generating = False
        self.stop_event = threading.Event()
        self.model = ""
        self.error = ""
        self.updated_at = time.time()


_chat_runtime_lock = threading.Lock()
_chat_runtimes: dict[str, _ChatRuntime] = {}


def _chat_project_key(repo_root: str) -> str:
    return str(repo_root or "").strip()


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
        messages.append({"role": role, "content": content})
    return messages


def _build_chat_prompt_messages(
    repo_root: str, history: list[dict], message: str, raw_model: str,
) -> list[dict]:
    ollama_model = raw_model[7:] if raw_model.startswith("ollama/") else raw_model
    knowledge_ctx = build_chat_context(repo_root)

    system_prompt = f"""You are a helpful AI coding assistant integrated into the Codex-Aider Bridge tool.
You help developers understand their codebase, plan features, debug issues, and discuss architecture.
You are conversational, concise, and precise.

IMPORTANT LIMITATIONS:
- You CANNOT edit files or run code directly. For actual code changes, use the Run tab.
- Your project knowledge comes from a static scan and may not reflect the latest edits.
- You have NO internet access.
- You run on the locally-installed Ollama model: {ollama_model}

{knowledge_ctx}

When suggesting code changes, be specific: name the file, the function, and exactly what to change."""

    messages = [{"role": "system", "content": system_prompt}]
    for item in history[-20:]:
        if item.get("role") in ("user", "assistant") and item.get("content"):
            messages.append({"role": item["role"], "content": item["content"]})
    messages.append({"role": "user", "content": message})
    return messages


def _run_chat_completion(
    repo_root: str, history: list[dict], message: str, raw_model: str,
) -> None:
    runtime = _get_chat_runtime(repo_root)
    ollama_model = raw_model[7:] if raw_model.startswith("ollama/") else raw_model
    messages = _build_chat_prompt_messages(repo_root, history, message, raw_model)

    body = json.dumps({
        "model": ollama_model, "messages": messages,
        "stream": True, "keep_alive": "30s",
    }).encode("utf-8")

    req = urllib.request.Request(
        "http://localhost:11434/api/chat", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )

    final_content = ""
    pkey = _chat_project_key(repo_root)
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
                    current = state_store.load_chat_history(pkey)
                    if current and current[-1].get("role") == "assistant":
                        current[-1]["content"] = final_content
                        state_store.save_chat_history(pkey, current)
                if chunk.get("done"):
                    break

        if runtime.stop_event.is_set():
            current = state_store.load_chat_history(pkey)
            if current and current[-1].get("role") == "assistant" and not str(current[-1].get("content", "")).strip():
                current.pop()
                state_store.save_chat_history(pkey, current)
        _set_chat_runtime_idle(repo_root)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8", errors="replace")).get("error") or f"HTTP {exc.code}"
        except Exception:
            detail = f"HTTP {exc.code}: {exc.reason}"
        _update_last_assistant_message(pkey, f"Ollama error: {detail}")
        _set_chat_runtime_idle(repo_root, f"Ollama error: {detail}")
    except urllib.error.URLError as exc:
        detail = f"Ollama not reachable: {getattr(exc, 'reason', str(exc))}. Is Ollama running?"
        _update_last_assistant_message(pkey, detail)
        _set_chat_runtime_idle(repo_root, detail)
    except Exception as exc:
        _update_last_assistant_message(pkey, str(exc))
        _set_chat_runtime_idle(repo_root, str(exc))


def _update_last_assistant_message(pkey: str, content: str) -> None:
    history = state_store.load_chat_history(pkey)
    if history and history[-1].get("role") == "assistant":
        history[-1]["content"] = content
        state_store.save_chat_history(pkey, history)


# ── Routes ───────────────────────────────────────────────────────────────────

@chat_bp.route("/chat")
def page_chat():
    return render_template("chat.html", active_page="chat")


@chat_bp.route("/api/chat", methods=["POST"])
def api_chat():
    """Stream a chat response from Ollama."""
    data = request.get_json(force=True) or {}
    message = str(data.get("message", "")).strip()
    history = _sanitize_chat_messages(data.get("history", []))

    if not message:
        return jsonify({"error": "message is required"}), 400

    settings = state_store.load_settings()
    request_model = (data.get("model") or "").strip()
    if request_model and not request_model.startswith("ollama/"):
        request_model = "ollama/" + request_model
    raw_model = request_model or settings.get("aider_model", "ollama/qwen2.5-coder:14b")
    ollama_model = raw_model[7:] if raw_model.startswith("ollama/") else raw_model
    repo_root = settings.get("repo_root", "").strip()
    messages = _build_chat_prompt_messages(repo_root, history, message, raw_model)

    def generate():
        body = json.dumps({"model": ollama_model, "messages": messages, "stream": True}).encode("utf-8")
        req = urllib.request.Request(
            "http://localhost:11434/api/chat", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
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
                detail = json.loads(exc.read().decode("utf-8", errors="replace")).get("error") or f"HTTP {exc.code}"
            except Exception:
                detail = f"HTTP {exc.code}: {exc.reason}"
            yield f"data: {json.dumps({'error': f'Ollama error: {detail}'})}\n\n"
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", str(exc))
            yield f"data: {json.dumps({'error': f'Ollama not reachable: {reason}'})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@chat_bp.route("/api/chat/start", methods=["POST"])
def api_chat_start():
    data = request.get_json(force=True) or {}
    message = str(data.get("message", "")).strip()
    history = _sanitize_chat_messages(data.get("history", []))
    repo_root = str(data.get("repo_root", "")).strip()

    if not message:
        return jsonify({"error": "message is required"}), 400
    if not repo_root:
        repo_root = str(state_store.load_settings().get("repo_root", "")).strip()
    if not repo_root:
        return jsonify({"error": "repo_root is required"}), 400

    settings = state_store.load_settings()
    request_model = str(data.get("model", "")).strip()
    if request_model and not request_model.startswith("ollama/"):
        request_model = "ollama/" + request_model
    raw_model = request_model or settings.get("aider_model", "ollama/qwen2.5-coder:14b")

    runtime = _get_chat_runtime(repo_root)
    if runtime.is_generating:
        return jsonify({"error": "A chat response is already running for this project."}), 409

    persisted = history + [{"role": "user", "content": message}, {"role": "assistant", "content": ""}]
    state_store.save_chat_history(_chat_project_key(repo_root), persisted)

    runtime.is_generating = True
    runtime.stop_event.clear()
    runtime.model = raw_model
    runtime.error = ""
    runtime.updated_at = time.time()

    threading.Thread(target=_run_chat_completion, args=(repo_root, history, message, raw_model), daemon=True).start()
    return jsonify({"ok": True})


@chat_bp.route("/api/chat/stop", methods=["POST"])
def api_chat_stop():
    data = request.get_json(force=True) or {}
    repo_root = str(data.get("repo_root") or state_store.load_settings().get("repo_root", "")).strip()
    if not repo_root:
        return jsonify({"error": "repo_root is required"}), 400
    runtime = _get_chat_runtime(repo_root)
    runtime.stop_event.set()
    runtime.updated_at = time.time()
    return jsonify({"ok": True})


@chat_bp.route("/api/chat/state", methods=["GET"])
def api_chat_state():
    repo_root = (request.args.get("repo_root") or state_store.load_settings().get("repo_root", "")).strip()
    runtime = _get_chat_runtime(repo_root)
    return jsonify({
        "repo_root": repo_root,
        "messages": state_store.load_chat_history(_chat_project_key(repo_root)),
        "is_generating": runtime.is_generating,
        "model": runtime.model,
        "error": runtime.error,
        "updated_at": runtime.updated_at,
    })


@chat_bp.route("/api/chat/history", methods=["GET"])
def api_chat_history():
    repo_root = (request.args.get("repo_root") or state_store.load_settings().get("repo_root", "")).strip()
    return jsonify({
        "repo_root": repo_root,
        "messages": state_store.load_chat_history(_chat_project_key(repo_root)),
    })


@chat_bp.route("/api/chat/history", methods=["POST"])
def api_chat_history_save():
    data = request.get_json(force=True) or {}
    repo_root = str(data.get("repo_root", "")).strip()
    if not repo_root:
        return jsonify({"error": "repo_root is required"}), 400
    messages = _sanitize_chat_messages(data.get("messages", []))
    state_store.save_chat_history(_chat_project_key(repo_root), messages)
    return jsonify({"ok": True, "count": len(messages)})


@chat_bp.route("/api/chat/history", methods=["DELETE"])
def api_chat_history_delete():
    repo_root = (request.args.get("repo_root") or state_store.load_settings().get("repo_root", "")).strip()
    if not repo_root:
        return jsonify({"error": "repo_root is required"}), 400
    state_store.clear_chat_history(_chat_project_key(repo_root))
    return jsonify({"ok": True})


@chat_bp.route("/api/chat/status")
def api_chat_status():
    settings = state_store.load_settings()
    raw_model = settings.get("aider_model", "")
    ollama_model = raw_model[7:] if raw_model.startswith("ollama/") else raw_model

    result = {"ollama_running": False, "model_available": False, "model": ollama_model, "error": None}
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            result["ollama_running"] = True
            models = [m.get("name", "") for m in data.get("models", [])]
            result["model_available"] = any(
                m == ollama_model or m.startswith(ollama_model.split(":")[0]) for m in models
            )
            result["available_models"] = models
    except urllib.error.URLError:
        result["error"] = "Ollama is not running"
    except Exception as exc:
        result["error"] = str(exc)
    return jsonify(result)
