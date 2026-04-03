"""relay_formatter.py — Prompt/packet generation and response parsing for AI Relay mode.

AI Relay lets the user copy prompts into any web AI (ChatGPT Plus, Claude.ai,
Gemini, Grok…) and paste responses back.  No API key is needed.

Public API
----------
build_plan_prompt(goal, knowledge_context, repo_root)  -> str
parse_plan(raw_text)                                   -> list[dict]
build_review_packet(task, diff, validation_result,
                    attempt, max_retries,
                    total_tasks, goal)                 -> str
parse_decision(raw_text)                               -> dict
build_replan_prompt(task, failed_reason, diff, goal)   -> str
"""
from __future__ import annotations

import json
import re
from typing import Any

# ── Constants ─────────────────────────────────────────────────────────────────

_DIVIDER = "=" * 51
_MAX_DIFF_LINES_REVIEW = 300
_MAX_DIFF_LINES_REPLAN = 200
_DECISION_SCAN_LINES   = 5


# ── Plan prompt ───────────────────────────────────────────────────────────────

def build_plan_prompt(goal: str, knowledge_context: str, repo_root: str) -> str:
    """Return a prompt the user pastes into their web AI to generate a task plan."""
    root_note = f"Repository: {repo_root}" if repo_root else "Repository: not configured"
    ctx_block = ""
    if knowledge_context and knowledge_context.strip():
        ctx_block = f"\nPROJECT CONTEXT:\n{knowledge_context.strip()}\n"

    return f"""{_DIVIDER}
BRIDGE PLAN REQUEST
{_DIVIDER}

You are a software planning assistant.
{root_note}
{ctx_block}
GOAL:
{goal}

{_DIVIDER}
OUTPUT INSTRUCTIONS

Respond with ONLY valid JSON — no markdown fences, no explanation, no preamble.
Use exactly this structure:

{{
  "plan_summary": "One sentence describing the overall approach",
  "tasks": [
    {{
      "id": 1,
      "title": "Short task title (max 60 chars)",
      "type": "create",
      "files": ["relative/path/to/file.ext"],
      "instruction": "Exact instruction for the code editor — specific and technical",
      "context": "Why this task exists / what it connects to"
    }}
  ]
}}

RULES:
- Each task = one atomic change, one or two files maximum
- Order tasks so dependencies come first
- "instruction" is sent directly to Aider (a local code editor) — no ambiguity
- "files" must be relative paths from the repo root
- "type" MUST be one of exactly: create | modify | delete | validate
  - create   = new file that does not yet exist
  - modify   = change an existing file
  - delete   = remove a file
  - validate = run a check / test, no file edit
- Maximum 15 tasks total
- If the goal is unclear, make reasonable assumptions and note them in plan_summary
{_DIVIDER}"""


# ── Plan parser ───────────────────────────────────────────────────────────────

def parse_plan(raw_text: str) -> list[dict]:
    """Extract and validate a task list from the AI's pasted response.

    Handles:
    - Bare JSON object
    - JSON wrapped in ```json ... ``` fences
    - JSON embedded anywhere in longer text

    Returns a list of validated task dicts.
    Raises ValueError with a human-readable message on any failure.
    """
    if not raw_text or not raw_text.strip():
        raise ValueError("Nothing was pasted. Copy the AI's response and paste it in the box above.")

    text = raw_text.strip()

    # Strip markdown fences if present
    fence = re.search(r'```(?:json)?\s*([\s\S]*?)```', text, re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()

    # Try bare parse first, then extract first {...} block
    data: Any = None
    for candidate in [text, _extract_first_json_object(text)]:
        if candidate is None:
            continue
        try:
            data = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue

    if data is None:
        raise ValueError(
            "Could not find valid JSON in the pasted text.\n"
            "Make sure you copied the entire response from the AI, "
            "including the opening { and closing }."
        )

    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object at the top level but got a different type.")

    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list) or len(raw_tasks) == 0:
        raise ValueError(
            'The JSON is valid but has no "tasks" array. '
            "Ask the AI to re-generate the plan using the required format."
        )

    tasks: list[dict] = []
    for i, t in enumerate(raw_tasks, start=1):
        if not isinstance(t, dict):
            raise ValueError(f"Task #{i} is not a JSON object.")
        if not isinstance(t.get("id"), (int, float)):
            raise ValueError(f'Task #{i} is missing a numeric "id" field.')
        if not isinstance(t.get("title"), str) or not t["title"].strip():
            raise ValueError(f'Task #{i} (id={t.get("id")}) is missing a "title" string.')
        if not isinstance(t.get("instruction"), str) or not t["instruction"].strip():
            raise ValueError(f'Task #{i} (id={t.get("id")}) is missing an "instruction" string.')

        # Normalise and default the "type" field.
        # Valid values expected by task_parser.TaskParser.
        _VALID_TYPES = {"create", "modify", "delete", "validate"}
        raw_type = str(t.get("type", "")).strip().lower()
        if raw_type not in _VALID_TYPES:
            # Best-effort inference: if files list is empty → validate,
            # otherwise default to modify (safe for most AI-generated plans).
            files_raw = t.get("files", [])
            raw_type = "validate" if not files_raw else "modify"

        tasks.append({
            "id":          int(t["id"]),
            "title":       t["title"].strip()[:80],
            "type":        raw_type,
            "files":       [str(f) for f in t.get("files", [])] if isinstance(t.get("files"), list) else [],
            "instruction": t["instruction"].strip(),
            "context":     str(t.get("context", "")).strip(),
        })

    return tasks


def _extract_first_json_object(text: str) -> str | None:
    """Return the substring of *text* from the first '{' to its matching '}'."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i, ch in enumerate(text[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


# ── Review packet ─────────────────────────────────────────────────────────────

def build_review_packet(
    task:              dict,
    diff:              str,
    validation_result: str,
    attempt:           int,
    max_retries:       int,
    total_tasks:       int,
    goal:              str,
) -> str:
    """Return the review text the user copies into their web AI after a task completes."""
    task_id    = task.get("id", "?")
    task_title = task.get("title", "(unknown)")
    instruction = task.get("instruction", "(unknown)")

    # Summarise changed files from diff header lines
    files_changed = _summarise_diff_files(diff)
    files_section = files_changed if files_changed else "(no files changed)"

    # Truncate diff if too long
    diff_lines  = (diff or "").splitlines()
    diff_body   = "\n".join(diff_lines[:_MAX_DIFF_LINES_REVIEW])
    diff_note   = ""
    if len(diff_lines) > _MAX_DIFF_LINES_REVIEW:
        diff_note = f"\n... (truncated — {len(diff_lines) - _MAX_DIFF_LINES_REVIEW} more lines not shown)"

    return f"""{_DIVIDER}
BRIDGE REVIEW REQUEST — Task {task_id} of {total_tasks}
{_DIVIDER}

ORIGINAL GOAL:
{goal}

TASK {task_id}: {task_title}
INSTRUCTION GIVEN TO AIDER:
{instruction}

FILES CHANGED:
{files_section}

DIFF:
{diff_body}{diff_note}

VALIDATION: {validation_result or "not run"}
ATTEMPT: {attempt} of {max_retries}

{_DIVIDER}
RESPOND WITH EXACTLY ONE LINE:

APPROVED
REWORK: [your specific instruction — what exactly needs to change]
FAILED: [reason — why this approach needs a new plan]
{_DIVIDER}"""


def _summarise_diff_files(diff: str) -> str:
    """Parse unified diff headers to produce 'file.py: +12 -3' summaries."""
    if not diff:
        return ""
    lines      = diff.splitlines()
    current    = None
    added      = 0
    removed    = 0
    summaries: list[str] = []

    def _flush():
        if current:
            summaries.append(f"  {current}: +{added} -{removed}")

    for line in lines:
        if line.startswith("+++ b/"):
            _flush()
            current = line[6:]
            added   = 0
            removed = 0
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1

    _flush()
    return "\n".join(summaries) if summaries else ""


# ── Decision parser ───────────────────────────────────────────────────────────

def parse_decision(raw_text: str) -> dict:
    """Parse the AI's pasted decision response.

    Returns one of:
        {"decision": "approved"}
        {"decision": "rework",      "instruction": "..."}
        {"decision": "failed",      "reason": "..."}
        {"decision": "unparseable", "raw": "..."}
    """
    if not raw_text or not raw_text.strip():
        return {"decision": "unparseable", "raw": ""}

    lines = raw_text.strip().splitlines()
    scan  = lines[:_DECISION_SCAN_LINES]

    for line in scan:
        stripped = line.strip()
        upper    = stripped.upper()

        # APPROVED
        if upper.startswith("APPROVED"):
            return {"decision": "approved"}

        # REWORK: ...
        m = re.match(r'^REWORK\s*:\s*(.+)', stripped, re.IGNORECASE)
        if m:
            instruction = m.group(1).strip()
            if instruction:
                return {"decision": "rework", "instruction": instruction}

        # FAILED: ...
        m = re.match(r'^FAILED\s*:\s*(.+)', stripped, re.IGNORECASE)
        if m:
            reason = m.group(1).strip()
            if reason:
                return {"decision": "failed", "reason": reason}

    return {"decision": "unparseable", "raw": raw_text.strip()[:500]}


# ── Replan prompt ─────────────────────────────────────────────────────────────

def build_replan_prompt(
    task:          dict,
    failed_reason: str,
    diff:          str,
    goal:          str,
) -> str:
    """Return a prompt asking the AI to supply replacement tasks for a failed task."""
    task_id     = task.get("id", "?")
    task_title  = task.get("title", "(unknown)")
    instruction = task.get("instruction", "(unknown)")

    diff_lines = (diff or "").splitlines()
    diff_body  = "\n".join(diff_lines[:_MAX_DIFF_LINES_REPLAN])
    diff_note  = ""
    if len(diff_lines) > _MAX_DIFF_LINES_REPLAN:
        diff_note = f"\n... (truncated — {len(diff_lines) - _MAX_DIFF_LINES_REPLAN} more lines)"

    return f"""{_DIVIDER}
BRIDGE REPLAN REQUEST
{_DIVIDER}

ORIGINAL GOAL:
{goal}

Task {task_id} FAILED: {task_title}
Failure reason: {failed_reason}

ORIGINAL INSTRUCTION THAT WAS TRIED:
{instruction}

FAILED IMPLEMENTATION (diff):
{diff_body}{diff_note}

{_DIVIDER}
Please provide replacement tasks starting at id={task_id}.
Only include the replacement tasks — NOT the entire plan.

Output ONLY valid JSON, no markdown, no explanation:

{{
  "tasks": [
    {{
      "id": {task_id},
      "title": "...",
      "files": ["relative/path/file.ext"],
      "instruction": "Exact instruction for the code editor",
      "context": "Why this replaces the failed approach"
    }}
  ]
}}
{_DIVIDER}"""
