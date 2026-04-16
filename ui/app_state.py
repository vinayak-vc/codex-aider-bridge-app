"""Shared application state — SSE broadcast, run access, knowledge cache.

Blueprints import from here to avoid circular imports with ui/app.py.
The app.py module populates these at startup.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
from typing import Optional

from ui import state_store

# ── SSE broadcast ────────────────────────────────────────────────────────────

_sse_clients: list[queue.Queue] = []
_sse_lock = threading.Lock()


def broadcast(event_type: str, data: dict) -> None:
    """Push an event to all connected SSE clients."""
    payload = json.dumps({"type": event_type, **data})
    with _sse_lock:
        for q in _sse_clients:
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass


def add_sse_client(q: queue.Queue) -> None:
    with _sse_lock:
        _sse_clients.append(q)


def remove_sse_client(q: queue.Queue) -> None:
    with _sse_lock:
        try:
            _sse_clients.remove(q)
        except ValueError:
            pass


# ── Run access ───────────────────────────────────────────────────────────────

def get_run():
    """Get the singleton BridgeRun instance."""
    from ui.bridge_runner import get_run as _get_run
    return _get_run()


# ── Knowledge context cache ──────────────────────────────────────────────────

_knowledge_cache: dict[str, tuple[str, float]] = {}
_KNOWLEDGE_CACHE_TTL = 60.0


def build_chat_context(repo_root: str) -> str:
    """Build project knowledge context string (cached 60s)."""
    now = time.time()
    cached = _knowledge_cache.get(repo_root)
    if cached and (now - cached[1]) < _KNOWLEDGE_CACHE_TTL:
        return cached[0]

    ctx_parts: list[str] = []
    repo_path = Path(repo_root)

    # Project knowledge
    try:
        from utils.project_knowledge import load_knowledge, to_context_text
        knowledge = load_knowledge(repo_path)
        ctx = to_context_text(knowledge)
        if ctx:
            ctx_parts.append(ctx)
    except Exception:
        pass

    # AI Understanding doc
    try:
        ai_doc = repo_path / "AI_UNDERSTANDING.md"
        if ai_doc.exists():
            text = ai_doc.read_text(encoding="utf-8", errors="replace")[:3000]
            ctx_parts.append(f"AI Understanding:\n{text}")
    except Exception:
        pass

    result = "\n\n".join(ctx_parts)
    _knowledge_cache[repo_root] = (result, now)
    return result
