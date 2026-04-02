from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

# In a PyInstaller bundle, store persistent data next to the .exe so it
# survives across updates (sys._MEIPASS is a temp dir that changes each run).
if getattr(sys, "frozen", False):
    DATA_DIR = Path(sys.executable).parent / "data"
else:
    DATA_DIR = Path(__file__).parent / "data"
SETTINGS_FILE    = DATA_DIR / "settings.json"
HISTORY_FILE     = DATA_DIR / "history.json"
TOKEN_LOG_FILE   = DATA_DIR / "token_log.json"
RELAY_TASKS_FILE = DATA_DIR / "relay_tasks.json"
MAX_HISTORY = 50
MAX_LOG_LINES = 500

DEFAULT_SETTINGS: dict = {
    "goal": "",
    "repo_root": "",
    "idea_file": "",
    "aider_model": "ollama/mistral",
    "supervisor": "codex",
    "supervisor_command": "codex.cmd exec --skip-git-repo-check --color never",
    "manual_supervisor": False,
    "manual_review_poll_seconds": 2,
    "workflow_profile": "standard",
    "validation_command": "",
    "max_plan_attempts": 3,
    "max_task_retries": 2,
    "task_timeout": 300,
    "plan_output_file": "",
    "plan_file": "",
    "dry_run": False,
    "clarifications": [],
}


def _ensure() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


# ── Settings ──────────────────────────────────────────────────────────────────

def load_settings() -> dict:
    _ensure()
    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            return {**DEFAULT_SETTINGS, **saved}
        except Exception:
            pass
    return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict) -> None:
    _ensure()
    # Only persist known keys so stale keys don't pile up
    cleaned = {k: settings[k] for k in DEFAULT_SETTINGS if k in settings}
    SETTINGS_FILE.write_text(json.dumps(cleaned, indent=2), encoding="utf-8")


# ── History ───────────────────────────────────────────────────────────────────

def load_history() -> list[dict]:
    _ensure()
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_history(history: list[dict]) -> None:
    _ensure()
    HISTORY_FILE.write_text(json.dumps(history, indent=2), encoding="utf-8")


def add_history_entry(entry: dict) -> str:
    history = load_history()
    entry_id = str(uuid.uuid4())
    entry["id"] = entry_id
    history.insert(0, entry)
    _save_history(history[:MAX_HISTORY])
    return entry_id


def update_history_entry(entry_id: str, updates: dict) -> None:
    history = load_history()
    for entry in history:
        if entry.get("id") == entry_id:
            entry.update(updates)
            break
    _save_history(history)


def delete_history_entry(entry_id: str) -> None:
    history = [e for e in load_history() if e.get("id") != entry_id]
    _save_history(history)


def clear_history() -> None:
    _save_history([])


# ── Token log ─────────────────────────────────────────────────────────────────

def load_token_log() -> dict:
    """Load the token log from the UI data directory."""
    _ensure()
    if TOKEN_LOG_FILE.exists():
        try:
            return json.loads(TOKEN_LOG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"sessions": [], "totals": _empty_token_totals()}


def _empty_token_totals() -> dict:
    return {
        "sessions_count": 0,
        "tasks_executed_total": 0,
        "supervisor_tokens_total": 0,
        "tokens_saved_total": 0,
        "savings_percent_avg": 0.0,
        "last_updated": None,
    }


# ── Relay tasks ───────────────────────────────────────────────────────────────

def load_relay_tasks() -> list[dict]:
    """Return the currently imported relay task list (empty list if none)."""
    _ensure()
    if RELAY_TASKS_FILE.exists():
        try:
            return json.loads(RELAY_TASKS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def save_relay_tasks(tasks: list[dict]) -> None:
    """Persist the relay task list."""
    _ensure()
    RELAY_TASKS_FILE.write_text(json.dumps(tasks, indent=2), encoding="utf-8")


def clear_relay_tasks() -> None:
    """Remove any previously imported relay tasks."""
    _ensure()
    if RELAY_TASKS_FILE.exists():
        RELAY_TASKS_FILE.unlink()
