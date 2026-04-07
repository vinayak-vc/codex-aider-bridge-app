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
PLAN_FAVORITES_FILE  = DATA_DIR / "plan_favorites.json"
GENERATED_PLANS_FILE = DATA_DIR / "generated_plans.json"
MAX_HISTORY = 50
MAX_GENERATED_PLANS = 50
MAX_LOG_LINES = 500

DEFAULT_SETTINGS: dict = {
    "goal": "",
    "repo_root": "",
    "idea_file": "",
    "aider_model": "ollama/qwen2.5-coder:7b",
    "supervisor": "manual",
    "supervisor_command": "",
    "manual_supervisor": True,
    "manual_review_poll_seconds": 2,
    "workflow_profile": "standard",
    "validation_command": "",
    "max_plan_attempts": 3,
    "max_task_retries": 10,
    "task_timeout": 600,
    "plan_output_file": "",
    "plan_file": "",
    "dry_run": False,
    "auto_commit": True,
    "model_lock": False,  # True = always use aider_model, skip smart routing
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


# ── Relay tasks (Project-Scoped) ──────────────────────────────────────────────

def _relay_state_file(repo_root: str) -> Path:
    """Return the absolute path to the relay state file for a project."""
    return Path(repo_root) / "bridge_progress" / "relay_state.json"


def load_relay_tasks(repo_root: str) -> list[dict]:
    """Return the currently imported relay task list for a project."""
    if not repo_root:
        return []
    state_file = _relay_state_file(repo_root)
    if state_file.exists():
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            return data.get("tasks", [])
        except Exception:
            pass
    return []


def save_relay_tasks(repo_root: str, tasks: list[dict]) -> None:
    """Persist the relay task list for a project."""
    if not repo_root:
        return
    state_file = _relay_state_file(repo_root)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Load current UI state to preserve it
    current_state = load_relay_ui_state(repo_root)
    payload = {"tasks": tasks, "ui_state": current_state}
    state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def clear_relay_tasks(repo_root: str) -> None:
    """Remove any previously imported relay tasks for a project."""
    if not repo_root:
        return
    state_file = _relay_state_file(repo_root)
    if state_file.exists():
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            data["tasks"] = []
            state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass


def load_relay_ui_state(repo_root: str) -> dict:
    """Return the persisted relay UI state for a project."""
    if not repo_root:
        return {}
    state_file = _relay_state_file(repo_root)
    if state_file.exists():
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            return data.get("ui_state", {})
        except Exception:
            pass
    return {}


def save_relay_ui_state(repo_root: str, state: dict) -> None:
    """Persist the relay UI state for a project."""
    if not repo_root:
        return
    state_file = _relay_state_file(repo_root)
    state_file.parent.mkdir(parents=True, exist_ok=True)

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
    
    # Load current tasks to preserve them
    current_tasks = load_relay_tasks(repo_root)
    payload = {"tasks": current_tasks, "ui_state": cleaned}
    state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def clear_relay_ui_state(repo_root: str) -> None:
    """Remove any previously imported relay UI state for a project."""
    if not repo_root:
        return
    state_file = _relay_state_file(repo_root)
    if state_file.exists():
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            data["ui_state"] = {}
            state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass


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
        "last_run_id", "confidence_score", "risks", "risk_level", "updated_at",
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


# ── Plan Favorites ────────────────────────────────────────────────────────────

def load_plan_favorites() -> list[dict]:
    if PLAN_FAVORITES_FILE.exists():
        try:
            return json.loads(PLAN_FAVORITES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def save_plan_favorite(fav: dict) -> None:
    favs = load_plan_favorites()
    favs.insert(0, fav)
    if len(favs) > 50:
        favs = favs[:50]
    PLAN_FAVORITES_FILE.write_text(json.dumps(favs, indent=2), encoding="utf-8")


def delete_plan_favorite(fav_id: str) -> None:
    favs = load_plan_favorites()
    favs = [f for f in favs if f.get("id") != fav_id]
    PLAN_FAVORITES_FILE.write_text(json.dumps(favs, indent=2), encoding="utf-8")


# ── Generated Plans Library ──────────────────────────────────────────────────

def load_generated_plans() -> list[dict]:
    _ensure()
    if GENERATED_PLANS_FILE.exists():
        try:
            return json.loads(GENERATED_PLANS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_generated_plans(plans: list[dict]) -> None:
    _ensure()
    GENERATED_PLANS_FILE.write_text(json.dumps(plans, indent=2), encoding="utf-8")


def save_generated_plan(plan: dict) -> str:
    """Save a newly generated plan to the library. Returns the plan ID."""
    import time as _time
    plans = load_generated_plans()
    plan_id = str(uuid.uuid4())[:8]
    plan["id"] = plan_id
    plan.setdefault("status", "generated")
    plan.setdefault("generated_at", _time.strftime("%Y-%m-%d %H:%M:%S"))
    plan.setdefault("last_run_at", None)
    plan.setdefault("completed_tasks", 0)
    plan.setdefault("failed_task_id", None)
    plans.insert(0, plan)
    if len(plans) > MAX_GENERATED_PLANS:
        plans = plans[:MAX_GENERATED_PLANS]
    _save_generated_plans(plans)
    return plan_id


def update_generated_plan(plan_id: str, updates: dict) -> None:
    """Update fields on an existing generated plan (status, last_run_at, etc.)."""
    plans = load_generated_plans()
    for plan in plans:
        if plan.get("id") == plan_id:
            plan.update(updates)
            break
    _save_generated_plans(plans)


def delete_generated_plan(plan_id: str) -> None:
    plans = load_generated_plans()
    plans = [p for p in plans if p.get("id") != plan_id]
    _save_generated_plans(plans)


def get_generated_plan(plan_id: str) -> dict | None:
    for plan in load_generated_plans():
        if plan.get("id") == plan_id:
            return plan
    return None


# ── Run Queue ─────────────────────────────────────────────────────────────────

RUN_QUEUE_FILE = DATA_DIR / "run_queue.json"


def load_run_queue() -> list[dict]:
    if RUN_QUEUE_FILE.exists():
        try:
            return json.loads(RUN_QUEUE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def append_run_queue(item: dict) -> None:
    q = load_run_queue()
    q.append(item)
    RUN_QUEUE_FILE.write_text(json.dumps(q, indent=2), encoding="utf-8")


def pop_run_queue() -> dict | None:
    q = load_run_queue()
    if not q:
        return None
    item = q.pop(0)
    RUN_QUEUE_FILE.write_text(json.dumps(q, indent=2), encoding="utf-8")
    return item


def remove_from_queue(index: int) -> None:
    q = load_run_queue()
    if 0 <= index < len(q):
        q.pop(index)
        RUN_QUEUE_FILE.write_text(json.dumps(q, indent=2), encoding="utf-8")
