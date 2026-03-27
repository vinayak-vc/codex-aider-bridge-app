"""Project knowledge cache — stores what every file does so any AI can
understand the project architecture by reading one JSON file.

Stored at: <repo_root>/bridge_progress/project_knowledge.json

Grows automatically after every bridge run. Any AI (Claude Code, Codex,
Antigravity, etc.) reads this file at session start and immediately knows:
  - What the project is
  - What every file's role is
  - What patterns the codebase uses
  - What is already done
  - What questions to ask the user before generating a new task plan
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Optional

_KNOWLEDGE_FILENAME = "project_knowledge.json"
_logger = logging.getLogger(__name__)

# How many chars of a task instruction to use as the file role summary.
_ROLE_SUMMARY_CHARS = 200


def _today() -> str:
    return date.today().isoformat()


def _role_from_instruction(instruction: str, file_path: str) -> str:
    """Extract a concise role description from a task instruction.

    Tries to find the first sentence. Falls back to first N chars.
    """
    # Strip leading boilerplate like "Create X.cs." or "Modify X.cs."
    cleaned = re.sub(
        r"^(Create|Modify|Update|Add|Implement|Write|Open)\s+\S+\.\w+[\.\s]*",
        "",
        instruction.strip(),
        flags=re.IGNORECASE,
    ).strip()

    if not cleaned:
        cleaned = instruction.strip()

    # Take up to first sentence boundary.
    sentence_end = re.search(r"[.!?]\s", cleaned)
    if sentence_end and sentence_end.start() < _ROLE_SUMMARY_CHARS:
        return cleaned[: sentence_end.start() + 1].strip()

    # Fall back to first N chars, trimmed to last word boundary.
    if len(cleaned) <= _ROLE_SUMMARY_CHARS:
        return cleaned

    truncated = cleaned[:_ROLE_SUMMARY_CHARS]
    last_space = truncated.rfind(" ")
    return (truncated[:last_space] if last_space > 0 else truncated) + "..."


def load_knowledge(repo_root: Path) -> dict:
    """Load project knowledge from disk. Returns empty structure if not found."""
    path = repo_root / "bridge_progress" / _KNOWLEDGE_FILENAME
    if not path.exists():
        return _empty_knowledge(repo_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        _logger.debug("Loaded project knowledge (%d files)", len(data.get("files", {})))
        return data
    except Exception as ex:
        _logger.warning("Could not read project knowledge (ignoring): %s", ex)
        return _empty_knowledge(repo_root)


def save_knowledge(knowledge: dict, repo_root: Path) -> None:
    """Persist project knowledge to disk."""
    progress_dir = repo_root / "bridge_progress"
    progress_dir.mkdir(parents=True, exist_ok=True)
    path = progress_dir / _KNOWLEDGE_FILENAME
    try:
        path.write_text(json.dumps(knowledge, indent=2), encoding="utf-8")
        _logger.info("Project knowledge saved to %s", path)
    except OSError as ex:
        _logger.warning("Could not save project knowledge: %s", ex)


def update_knowledge_from_run(
    knowledge: dict,
    goal: str,
    tasks: list,
    all_diffs: list[dict],
    repo_root: Path,
    append_run_record: bool = True,
    run_status: str = "success",
    tasks_completed_override: Optional[int] = None,
) -> dict:
    """Update knowledge after a successful bridge run.

    - Registers every file touched in the run with its role.
    - Adds the run to history.
    - Appends completed feature names.
    - Refreshes last_updated.
    """
    knowledge["project"]["last_updated"] = _today()

    if not knowledge["project"].get("summary") and goal:
        knowledge["project"]["summary"] = goal[:300]

    # Build a map of file → task instruction for role extraction.
    for task in tasks:
        instruction = getattr(task, "instruction", "") or ""
        task_type = getattr(task, "type", "modify")
        for file_path in getattr(task, "files", []):
            if not file_path:
                continue
            existing = knowledge["files"].get(file_path, {})
            knowledge["files"][file_path] = {
                "role": existing.get("role") or _role_from_instruction(instruction, file_path),
                "task_type": task_type,
                "last_modified": _today(),
                "created": existing.get("created", _today()),
            }

    # Mark feature as done (use task instruction summary as feature name).
    done_set = set(knowledge.get("features_done", []))
    for task in tasks:
        instruction = getattr(task, "instruction", "") or ""
        files = getattr(task, "files", [])
        if files:
            feature_label = Path(files[0]).stem  # e.g. "PlayerController"
            if feature_label not in done_set:
                knowledge["features_done"].append(feature_label)
                done_set.add(feature_label)

    # Append run record when explicitly requested.
    if append_run_record:
        knowledge["runs"].append({
            "date": _today(),
            "goal": goal[:150] if goal else "",
            "tasks_completed": (
                tasks_completed_override
                if tasks_completed_override is not None
                else len(tasks)
            ),
            "status": run_status,
        })

    return knowledge


def to_context_text(knowledge: dict) -> str:
    """Produce a compact human-readable summary for injection into AI prompts.

    This text is injected into the supervisor planning prompt so the AI
    knows the full project architecture without reading any source files.
    """
    proj = knowledge.get("project", {})
    files = knowledge.get("files", {})
    patterns = knowledge.get("patterns", [])
    done = knowledge.get("features_done", [])
    suggested = knowledge.get("suggested_next", [])

    lines: list[str] = []

    # Project header
    name = proj.get("name", "Unknown Project")
    lang = proj.get("language", "")
    ptype = proj.get("type", "")
    type_str = f" ({ptype}/{lang})" if ptype and lang else f" ({lang or ptype})" if (lang or ptype) else ""
    lines.append(f"PROJECT: {name}{type_str}")

    summary = proj.get("summary", "")
    if summary:
        lines.append(f"SUMMARY: {summary}")

    # File registry — the core value
    if files:
        lines.append("")
        lines.append("FILE REGISTRY (what each file does):")
        for file_path, meta in sorted(files.items()):
            role = meta.get("role", "no description")
            lines.append(f"  {file_path}")
            lines.append(f"    -> {role}")

    # Code patterns
    if patterns:
        lines.append("")
        lines.append("CODE PATTERNS:")
        for p in patterns:
            lines.append(f"  -{p}")

    # What's done
    if done:
        lines.append("")
        lines.append(f"ALREADY IMPLEMENTED: {', '.join(done)}")

    # Suggested next steps
    if suggested:
        lines.append("")
        lines.append("POSSIBLE NEXT STEPS:")
        for s in suggested:
            lines.append(f"  -{s}")

    # Run history
    runs = knowledge.get("runs", [])
    if runs:
        last = runs[-1]
        lines.append("")
        lines.append(
            f"LAST RUN: {last.get('date', '?')} | "
            f"{last.get('tasks_completed', 0)} tasks | "
            f"\"{last.get('goal', '')}\""
        )

    return "\n".join(lines)


def _empty_knowledge(repo_root: Path) -> dict:
    """Return a blank knowledge structure for a new project."""
    return {
        "project": {
            "name": repo_root.name,
            "type": "",
            "language": "",
            "summary": "",
            "repo_root": str(repo_root),
            "first_seen": _today(),
            "last_updated": _today(),
        },
        "files": {},
        "patterns": [],
        "features_done": [],
        "suggested_next": [],
        "runs": [],
    }
