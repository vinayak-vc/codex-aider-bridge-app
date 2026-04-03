from __future__ import annotations

import os
import json
import sys
import uuid
from pathlib import Path

# In a PyInstaller bundle, store persistent data next to the .exe so it
# survives across updates (sys._MEIPASS is a temp dir that changes each run).
#if getattr(sys, "frozen", False):
#    DATA_DIR = Path(sys.executable).parent / "data"#
#else:
#    DATA_DIR = Path(__file__).parent / "data"

APP_NAME = "AiderBridge"

if os.name == "nt":
    base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
else:
    base = os.path.expanduser("~/.local/share")

DATA_DIR = Path(base) / APP_NAME
DATA_DIR.mkdir(parents=True, exist_ok=True)
    
SETTINGS_FILE    = DATA_DIR / "settings.json"
HISTORY_FILE     = DATA_DIR / "history.json"
TOKEN_LOG_FILE   = DATA_DIR / "token_log.json"
RELAY_TASKS_FILE = DATA_DIR / "relay_tasks.json"
RELAY_UI_STATE_FILE = DATA_DIR / "relay_ui_state.json"
CHAT_SESSIONS_FILE   = DATA_DIR / "chat_sessions.json"
RUN_NL_STATES_FILE   = DATA_DIR / "run_nl_states.json"
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


def load_relay_ui_state() -> dict:
    _ensure()
    if RELAY_UI_STATE_FILE.exists():
        try:
            raw = json.loads(RELAY_UI_STATE_FILE.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            pass
    return {}


def save_relay_ui_state(state: dict) -> None:
    _ensure()
    allowed = {
        "step",
        "goal",
        "repo_root",
        "aider_model",
        "max_task_attempts",
        "relay_session_id",
        "prompt_output",
        "plan_paste",
    }
    cleaned = {key: state.get(key) for key in allowed if key in state}
    RELAY_UI_STATE_FILE.write_text(json.dumps(cleaned, indent=2), encoding="utf-8")


def clear_relay_ui_state() -> None:
    _ensure()
    if RELAY_UI_STATE_FILE.exists():
        RELAY_UI_STATE_FILE.unlink()


# Chat sessions

def load_chat_sessions() -> dict[str, list[dict]]:
    _ensure()
    if CHAT_SESSIONS_FILE.exists():
        try:
            raw = json.loads(CHAT_SESSIONS_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                cleaned: dict[str, list[dict]] = {}
                for key, value in raw.items():
                    if isinstance(key, str) and isinstance(value, list):
                        cleaned[key] = value
                return cleaned
        except Exception:
            pass
    return {}


def _save_chat_sessions(sessions: dict[str, list[dict]]) -> None:
    _ensure()
    CHAT_SESSIONS_FILE.write_text(json.dumps(sessions, indent=2), encoding="utf-8")


def load_chat_history(project_key: str) -> list[dict]:
    if not project_key:
        return []
    sessions = load_chat_sessions()
    history = sessions.get(project_key, [])
    return history if isinstance(history, list) else []


def save_chat_history(project_key: str, messages: list[dict]) -> None:
    if not project_key:
        return
    sessions = load_chat_sessions()
    sessions[project_key] = messages[-100:]
    _save_chat_sessions(sessions)


def clear_chat_history(project_key: str) -> None:
    if not project_key:
        return
    sessions = load_chat_sessions()
    if project_key in sessions:
        del sessions[project_key]
        _save_chat_sessions(sessions)


# ── Projects ──────────────────────────────────────────────────────────────────

PROJECTS_FILE = DATA_DIR / "projects.json"
MAX_PROJECTS  = 20


def load_projects() -> list[dict]:
    """Return list of saved projects: [{name, path}, …] most-recent first."""
    _ensure()
    if PROJECTS_FILE.exists():
        try:
            return json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_projects(projects: list[dict]) -> None:
    _ensure()
    PROJECTS_FILE.write_text(json.dumps(projects, indent=2), encoding="utf-8")


def add_project(path: str, name: str = "") -> None:
    """Add or promote a project to the front of the list."""
    path = str(path).strip()
    if not path:
        return
    if not name:
        name = Path(path).name or path
    projects = [p for p in load_projects() if p.get("path") != path]
    projects.insert(0, {"name": name, "path": path})
    _save_projects(projects[:MAX_PROJECTS])


def remove_project(path: str) -> None:
    """Remove a project by path."""
    projects = [p for p in load_projects() if p.get("path") != path]
    _save_projects(projects)


def rename_project(path: str, new_name: str) -> None:
    """Rename a project by path."""
    projects = load_projects()
    for p in projects:
        if p.get("path") == path:
            p["name"] = new_name.strip() or Path(path).name
    _save_projects(projects)


# ── Run NL conversation state ──────────────────────────────────────────────────

def _load_run_nl_states() -> dict[str, dict]:
    _ensure()
    if RUN_NL_STATES_FILE.exists():
        try:
            raw = json.loads(RUN_NL_STATES_FILE.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            pass
    return {}


def _save_run_nl_states(states: dict[str, dict]) -> None:
    _ensure()
    RUN_NL_STATES_FILE.write_text(json.dumps(states, indent=2), encoding="utf-8")


def load_run_nl_state(project_key: str) -> dict:
    """Return the persisted NL conversation state for a project, or {}."""
    if not project_key:
        return {}
    return _load_run_nl_states().get(project_key, {})


def save_run_nl_state(project_key: str, state: dict) -> None:
    """Persist NL conversation state for a project.

    Allowed keys: message, brief, status, tasks, plan_summary, plan_file,
    plan_status, updated_at.
    """
    if not project_key:
        return
    import time as _time
    allowed = {
        "message", "brief", "status",
        "tasks", "plan_summary", "plan_file", "plan_status",
        "updated_at",
    }
    cleaned = {k: state[k] for k in allowed if k in state}
    cleaned.setdefault("updated_at", _time.time())
    states = _load_run_nl_states()
    states[project_key] = cleaned
    _save_run_nl_states(states)


def clear_run_nl_state(project_key: str) -> None:
    """Delete the NL conversation state for a project."""
    if not project_key:
        return
    states = _load_run_nl_states()
    if project_key in states:
        del states[project_key]
        _save_run_nl_states(states)
