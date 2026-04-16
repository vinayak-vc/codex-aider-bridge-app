"""Relay API blueprint — AI relay prompt generation, plan import, review.

Extracted from ui/app.py for maintainability.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, redirect, render_template, request

from context.project_context_service import ProjectContextService
from ui import state_store
from ui.app_state import broadcast, get_run

relay_bp = Blueprint('relay', __name__)


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


def _relay_current_session_id(repo_root: str = "") -> str:
    if not repo_root:
        settings = state_store.load_settings()
        repo_root = str(settings.get("repo_root") or "").strip()
    return str(state_store.load_relay_ui_state(repo_root).get("relay_session_id") or "").strip()


def _relay_normalize(text: str) -> str:
    """Strip all non-alphanumeric chars and lowercase for lenient matching."""
    return "".join(c for c in str(text).lower() if c.isalnum())


def _relay_task_matches_payload(task: dict, payload: dict) -> bool:
    # Match primarily on Task ID for manual-supervisor resumption
    if int(task.get("id", -1)) == int(payload.get("task_id", -2)):
        # Further confirm with normalized instructions
        t_norm = _relay_normalize(task.get("instruction", ""))
        p_norm = _relay_normalize(payload.get("instruction", ""))
        if t_norm == p_norm:
            return True
            
    # Fallback to older strict matching if needed
    payload_instruction = str(payload.get("instruction", "")).strip()
    task_instruction = str(task.get("instruction", "")).strip()
    return payload_instruction == task_instruction


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

    bp_dir = Path(repo_root) / "bridge_progress"

    # 1. Read checkpoint.json (Definitive completed IDs)
    ckpt_path = bp_dir / "checkpoint.json"
    if ckpt_path.exists():
        try:
            ckpt = json.loads(ckpt_path.read_text(encoding="utf-8"))
            for tid in ckpt.get("completed", []):
                statuses[int(tid)] = {"code": "approved", "label": "Done"}
        except Exception: pass

    # 2. Read task_metrics.json (Richer per-task state)
    metrics_path = bp_dir / "task_metrics.json"
    if metrics_path.exists():
        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            for t_metric in metrics.get("tasks", []):
                tid = int(t_metric.get("id", 0))
                if tid <= 0: continue
                if t_metric.get("completed") and tid not in statuses:
                    statuses[tid] = {"code": "approved", "label": "Done"}
            
            f_id = metrics.get("failed_task_id")
            if f_id:
                statuses[int(f_id)] = {"code": "failed", "label": "Failed"}
        except Exception: pass

    # 3. Read last_run.json (Last failure reason)
    last_run_path = bp_dir / "last_run.json"
    if last_run_path.exists():
        try:
            lr = json.loads(last_run_path.read_text(encoding="utf-8"))
            if lr.get("status") == "failure":
                f_id = lr.get("failed_task_id")
                if f_id and int(f_id) not in statuses:
                    statuses[int(f_id)] = {"code": "failed", "label": "Failed"}
        except Exception: pass

    # 4. Fallback: manual_supervisor/completed/
    completed_dir = bp_dir / "manual_supervisor" / "completed"
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

    # 5. Fallback: manual_supervisor/requests/ (Waiting for review)
    requests_dir = bp_dir / "manual_supervisor" / "requests"
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


def _relay_state_payload(repo_root: str = "") -> dict:
    # If no repo_root provided as arg, try to find it from global settings
    if not repo_root:
        settings = state_store.load_settings()
        repo_root = str(settings.get("repo_root") or "").strip()

    ui_state = state_store.load_relay_ui_state(repo_root)
    tasks = state_store.load_relay_tasks(repo_root)
    settings = state_store.load_settings()
    
    # Use the verified repo_root for everything else
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




# ── AI Relay routes ───────────────────────────────────────────────────────────

@relay_bp.route("/relay")
def relay_page():
    # AI Relay is now inline on the Run page (Milestone B).
    # Keep this route so old bookmarks / links don't 404.
    from flask import redirect
    return redirect("/run", code=302)


@relay_bp.route("/api/relay/generate-prompt", methods=["POST"])
def api_relay_generate_prompt():
    """Return the plan prompt the user pastes into their web AI."""
    from utils.relay_formatter import build_plan_prompt

    data      = request.get_json(force=True) or {}
    goal      = (data.get("goal") or "").strip()
    repo_root = (data.get("repo_root") or "").strip()
    if not goal:
        return jsonify({"error": "goal is required"}), 400

    knowledge_context = ""
    if repo_root:
        repo_path = Path(repo_root)
        try:
            knowledge_context = ProjectContextService(repo_path).load_for_relay().relay_text.strip()
        except Exception:
            pass

    prompt = build_plan_prompt(goal, knowledge_context, repo_root)
    
    # Save the generated state so the UI can restore it after tab switching
    ui_state = state_store.load_relay_ui_state(repo_root)
    ui_state["goal"] = goal
    ui_state["repo_root"] = repo_root
    ui_state["prompt_output"] = prompt
    state_store.save_relay_ui_state(repo_root, ui_state)
    
    return jsonify({"prompt": prompt})


@relay_bp.route("/api/relay/import-plan", methods=["POST"])
def api_relay_import_plan():
    """Parse the web AI's plan response and persist the task list."""
    from utils.relay_formatter import parse_plan

    data      = request.get_json(force=True) or {}
    raw_text  = (data.get("raw_text") or "").strip()
    repo_root = (data.get("repo_root") or "").strip()
    if not raw_text:
        return jsonify({"error": "raw_text is required"}), 400
    
    # Try to extract repo_root from settings if not passed
    if not repo_root:
        settings = state_store.load_settings()
        repo_root = settings.get("repo_root", "")

    try:
        tasks = parse_plan(raw_text)
        if not tasks:
            return jsonify({"error": "No tasks found in AI response. Ensure it is a valid JSON plan."}), 400

        # Save to project-specific state
        state_store.save_relay_tasks(repo_root, tasks)
        
        session_id = _relay_current_session_id(repo_root)
        ui_state = state_store.load_relay_ui_state(repo_root)
        ui_state["relay_session_id"] = session_id
        ui_state["plan_paste"] = raw_text
        state_store.save_relay_ui_state(repo_root, ui_state)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 422
    return jsonify({"tasks": tasks, "count": len(tasks), "relay_session_id": ui_state["relay_session_id"]})


@relay_bp.route("/api/relay/tasks/skip", methods=["POST"])
def api_relay_skip_task():
    data = request.get_json(force=True) or {}
    task_id = int(data.get("task_id", 0))
    skip = bool(data.get("skip", True))

    if task_id <= 0:
        return jsonify({"error": "task_id is required"}), 400

    run = get_run()
    if run.is_running or run.status in ("running", "waiting_review", "paused"):
        return jsonify({"error": "Stop or finish the active run before changing skipped tasks."}), 409

    # Try to extract repo_root from settings if not passed
    repo_root = (data.get("repo_root") or "").strip()
    if not repo_root:
        settings = state_store.load_settings()
        repo_root = settings.get("repo_root", "")

    tasks = state_store.load_relay_tasks(repo_root)
    task = next((item for item in tasks if int(item.get("id", 0)) == task_id), None)
    if task is None:
        return jsonify({"error": f"Task {task_id} was not found."}), 404

    current_status = str(task.get("status", "")).strip().lower()
    ui_state = state_store.load_relay_ui_state(repo_root)
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

    state_store.save_relay_tasks(repo_root, tasks)
    return jsonify(_relay_state_payload(repo_root))


@relay_bp.route("/api/relay/state", methods=["GET"])
def api_relay_state():
    repo_root = (request.args.get("repo_root") or "").strip()
    return jsonify(_relay_state_payload(repo_root))


@relay_bp.route("/api/relay/state", methods=["DELETE"])
def api_relay_state_clear():
    repo_root = (request.args.get("repo_root") or "").strip()
    if not repo_root:
        settings = state_store.load_settings()
        repo_root = settings.get("repo_root", "")
    
    state_store.clear_relay_ui_state(repo_root)
    state_store.clear_relay_tasks(repo_root)
    return jsonify({"ok": True})


@relay_bp.route("/api/relay/review-packet", methods=["GET"])
def api_relay_review_packet():
    """Build the review text for a completed task."""
    from utils.relay_formatter import build_review_packet

    task_id   = request.args.get("task_id", "")
    repo_root = (request.args.get("repo_root") or "").strip()
    goal      = (request.args.get("goal") or "").strip()
    relay_session_id = (request.args.get("relay_session_id") or _relay_current_session_id(repo_root)).strip()

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

    tasks      = state_store.load_relay_tasks(repo_root)
    total      = len(tasks)
    task       = next((t for t in tasks if str(t.get("id")) == str(task_id)), req_data)
    diff       = req_data.get("diff", "")
    validation = req_data.get("validation_result", "not run")
    attempt    = req_data.get("attempt", 1)
    max_retries = req_data.get("max_retries", 2)

    packet = build_review_packet(task, diff, validation, attempt, max_retries, total, goal)
    return jsonify({"packet": packet})


@relay_bp.route("/api/relay/submit-decision", methods=["POST"])
def api_relay_submit_decision():
    """Parse the web AI's review response and write the decision file."""
    from utils.relay_formatter import parse_decision

    data      = request.get_json(force=True) or {}
    raw_text  = data.get("raw_text", "")
    task_id   = data.get("task_id")
    repo_root = (data.get("repo_root") or "").strip()
    relay_session_id = str(data.get("relay_session_id") or _relay_current_session_id(repo_root)).strip()

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


@relay_bp.route("/api/relay/replan-prompt", methods=["POST"])
def api_relay_replan_prompt():
    """Build the replan prompt for a failed task."""
    from utils.relay_formatter import build_replan_prompt

    data          = request.get_json(force=True) or {}
    task_id       = data.get("task_id")
    failed_reason = (data.get("failed_reason") or "").strip()
    repo_root     = (data.get("repo_root") or "").strip()
    goal          = (data.get("goal") or "").strip()
    relay_session_id = str(data.get("relay_session_id") or _relay_current_session_id(repo_root)).strip()

    if not task_id or not repo_root:
        return jsonify({"error": "task_id and repo_root are required"}), 400

    req_file = _relay_request_file(repo_root, int(task_id), relay_session_id)
    diff     = ""
    task     = {"id": task_id, "title": f"Task {task_id}", "instruction": ""}
    if req_file.exists():
        try:
            req_data = json.loads(req_file.read_text(encoding="utf-8"))
            diff     = req_data.get("diff", "")
            tasks    = state_store.load_relay_tasks(repo_root)
            found    = next((t for t in tasks if str(t.get("id")) == str(task_id)), None)
            if found:
                task = found
        except Exception:
            pass

    if not failed_reason:
        failed_reason = "Task marked as failed by reviewer."

    prompt = build_replan_prompt(task, failed_reason, diff, goal)
    return jsonify({"prompt": prompt})


@relay_bp.route("/api/relay/import-replan", methods=["POST"])
def api_relay_import_replan():
    """Parse replacement tasks from the web AI and splice them into the task list."""
    from utils.relay_formatter import parse_plan

    data      = request.get_json(force=True) or {}
    raw_text  = data.get("raw_text", "")
    task_id   = data.get("task_id")
    repo_root = (data.get("repo_root") or "").strip()

    if not task_id or not repo_root:
        return jsonify({"error": "task_id and repo_root are required"}), 400

    try:
        replacement_tasks = parse_plan(raw_text)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 422

    tasks = state_store.load_relay_tasks(repo_root)
    # Remove the failed task and everything after it, then splice in replacements
    pivot = next((i for i, t in enumerate(tasks) if str(t.get("id")) == str(task_id)), None)
    if pivot is not None:
        tasks = tasks[:pivot] + replacement_tasks
    else:
        tasks = tasks + replacement_tasks

    state_store.save_relay_tasks(repo_root, tasks)
    return jsonify({"tasks": tasks, "count": len(tasks)})
