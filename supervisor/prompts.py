"""Supervisor prompt templates — extracted from agent.py for readability.

All prompt-building logic lives here. The SupervisorAgent calls these
functions to construct prompts for planning, reviewing, and sub-planning.
"""
from __future__ import annotations

from typing import Optional

from models.task import Task, TaskReport

_IDEA_MAX_CHARS = 2000


def build_plan_prompt(
    goal: str,
    repo_tree: str,
    idea_text: Optional[str],
    feedback: Optional[str],
    knowledge_context: Optional[str] = None,
    workflow_profile: str = "standard",
    feature_specs: Optional[str] = None,
    model_roster: Optional[str] = None,
) -> str:
    idea_block = ""
    if idea_text:
        trimmed = idea_text[:_IDEA_MAX_CHARS]
        idea_block = f"\nProject brief:\n{trimmed}\n"

    knowledge_block = ""
    if knowledge_context:
        knowledge_block = f"\nProject knowledge (file roles and history):\n{knowledge_context}\n"

    feedback_block = ""
    if feedback:
        feedback_block = (
            "\nThe previous plan was rejected for the following reason. "
            "Fix these issues and return only the corrected plan:\n"
            f"{feedback}\n"
        )

    profile_block = ""
    if workflow_profile == "micro":
        profile_block = (
            "\nMICRO-TASK PROFILE (STRICT):\n"
            "- One file per task. Do not produce multi-file tasks.\n"
            "- One concern per task.\n"
            "- Prefer create/modify/delete tasks over broad validate tasks.\n"
            "- Every create task must include must_exist.\n"
            "- Every delete task must include must_not_exist.\n"
            "- Every modify task should include at least one assertion when the output is observable.\n"
            "- Assume a small local coding model is implementing the task, so keep instructions surgical.\n"
        )

    return (
        "Decompose the development goal below into an atomic sequential task plan\n"
        "for a coding tool called Aider. Return ONLY valid JSON.\n\n"
        "CRITICAL CONTEXT — AIDER RUNS ON A SMALL LOCAL LLM (7-14B parameters):\n"
        "The coding model has a 32K token context window and runs at 6-65 tok/s.\n"
        "It can only see the target file(s) you specify — it cannot browse the repo.\n"
        "If your instruction is vague, the model will drift into unrelated files,\n"
        "overflow its context, and fail. Every instruction MUST be self-contained.\n\n"
        "AIDER-GRADE INSTRUCTION RULES:\n"
        "- Name the exact function/class/variable to modify. Never say 'refactor the module'.\n"
        "- Name the exact parameters, fields, or config keys involved.\n"
        "- If the task consumes data from another file, describe the data shape inline\n"
        "  (e.g., 'payload.maxVideos (number)') — do NOT tell the model to go read that file.\n"
        "- Each instruction must be completable by reading ONLY the target file.\n"
        "  The model must never need to open, inspect, or reason about other files.\n"
        "- Specify what the current code does AND what it should do after the change.\n"
        "- Keep instructions under 200 words. Longer = more tokens = slower.\n\n"
        "BAD instruction: 'Refactor upload command building so it consumes advanced\n"
        "  operator inputs from the renderer instead of relying on minimal hardcoded defaults.'\n"
        "  Why bad: 'advanced operator inputs' is undefined. Model will search the repo for\n"
        "  what inputs exist, find large files, overflow context, and fail.\n\n"
        "GOOD instruction: 'In buildUploadCommand(), replace the hardcoded --max-videos \"1\"\n"
        "  with String(payload.maxVideos || 1). Replace --shorts-policy \"convert\" with\n"
        "  payload.shortsPolicy || \"convert\". Add new flags: --resolution (payload.resolution),\n"
        "  --format (payload.format), --quality (payload.quality) if they are defined.'\n"
        "  Why good: names the function, the parameters, the exact changes. Zero ambiguity.\n\n"
        "STRICT RULES:\n"
        "- Return ONLY the JSON plan. No code. No prose. No questions.\n"
        "- Each task targets exactly one concern and one or two specific files.\n"
        "- Use only relative file paths that are visible in the repo structure below.\n"
        "  If a file does not yet exist, use the path it should be created at.\n"
        "- Task type must be one of: create, modify, delete, validate, read, investigate\n"
        "- Use type 'read' for simple file reading — returns file content for analysis.\n"
        "  Read tasks skip Aider entirely. Use for: list features, check status, read config.\n"
        "- Use type 'investigate' for multi-file analysis that requires understanding code.\n"
        "  Investigate tasks read multiple files + their imports/dependencies and send\n"
        "  everything to the supervisor for deep analysis. Use for: find bugs, review security,\n"
        "  trace data flow, identify impact of changes, find missing tests.\n"
        "  Investigate tasks can be followed by create/modify tasks to fix what was found.\n"
        "- Tasks execute sequentially. Later tasks may depend on earlier ones.\n"
        "- Use must_exist / must_not_exist when the task has a clear post-condition.\n"
        "- Do not ask questions. Do not explain. Return the plan only.\n"
        "- Use the FILE REGISTRY below to reference existing file roles correctly.\n"
        "  Do not duplicate work that is already marked as done.\n\n"
        f"{profile_block}"
        f"Repo structure:\n{repo_tree}\n"
        f"{knowledge_block}"
        f"{idea_block}"
        f"{_build_feature_specs_block(feature_specs)}"
        f"{_build_model_roster_block(model_roster)}"
        f"\nGoal: {goal}\n"
        f"{feedback_block}"
    )


def build_subplan_prompt(task: Task, error_message: str) -> str:
    return (
        "A development task failed mechanical validation.\n\n"
        "Create 1-3 atomic correction sub-tasks that fix the specific error.\n\n"
        "STRICT RULES:\n"
        "- Return ONLY JSON. No prose. No code. No questions.\n"
        "- Sub-tasks must target only files from the parent task's file list.\n"
        "- Instructions must name exact functions, variables, and parameters to change.\n"
        "  The coding model is a small local LLM — vague instructions cause it to drift\n"
        "  into unrelated files and overflow its context window.\n"
        "- Maximum 3 sub-tasks. Prefer fewer.\n\n"
        f"Parent Task {task.id} ({task.type})\n"
        f"Files: {', '.join(task.files)}\n"
        f"Original instruction: {task.instruction}\n\n"
        f"Mechanical validation error:\n{error_message}\n\n"
        'Return format: {"sub_tasks": [{"instruction": "...", "files": ["..."], "type": "modify"}]}'
    )


def build_review_prompt(report: TaskReport) -> str:
    task = report.task
    result = report.execution_result
    diff = report.diff or "(no diff captured — no file changes detected)"

    exit_summary = "succeeded" if result.exit_code == 0 else f"failed (exit code {result.exit_code})"

    return (
        "Review the completed developer work below.\n"
        "Reply with exactly one of these two forms (nothing else):\n"
        "  PASS\n"
        "  REWORK: <specific replacement instruction>\n\n"
        "If REWORK: the instruction must name exact functions, variables, and parameters.\n"
        "The coding model is a small local LLM (7-14B) with 32K context. Vague rework\n"
        "instructions like 'fix the implementation' cause it to drift and fail.\n"
        "Example REWORK: 'In buildUploadCommand(), the --max-videos flag still uses\n"
        "hardcoded \"1\" — replace with String(payload.maxVideos || 1)'\n\n"
        f"Task {task.id} ({task.type})\n"
        f"Files: {', '.join(task.files)}\n"
        f"Instruction: {task.instruction}\n"
        f"Aider execution: {exit_summary}\n\n"
        f"Changes made:\n{diff}\n"
    )


def plan_schema() -> str:
    return (
        "{\n"
        '  "type": "object",\n'
        '  "additionalProperties": true,\n'
        '  "required": ["tasks"],\n'
        '  "properties": {\n'
        '    "tasks": {\n'
        '      "type": "array",\n'
        '      "minItems": 1,\n'
        '      "items": {\n'
        '        "type": "object",\n'
        '        "additionalProperties": true,\n'
        '        "required": ["id", "files", "instruction", "type"],\n'
        '        "properties": {\n'
        '          "id": { "type": "integer" },\n'
        '          "files": {\n'
        '            "type": "array",\n'
        '            "minItems": 1,\n'
        '            "items": { "type": "string", "minLength": 1 }\n'
        "          },\n"
        '          "instruction": { "type": "string", "minLength": 1 },\n'
        '          "type": { "type": "string", "enum": ["create", "modify", "delete", "validate", "read", "investigate"] },\n'
        '          "must_exist": { "type": "array", "items": { "type": "string" } },\n'
        '          "must_not_exist": { "type": "array", "items": { "type": "string" } },\n'
        '          "model": { "type": "string" }\n'
        "        }\n"
        "      }\n"
        "    }\n"
        "  }\n"
        "}\n"
    )


# ── Private helpers ──────────────────────────────────────────────────────────

def _build_feature_specs_block(feature_specs: Optional[str]) -> str:
    if not feature_specs:
        return ""
    return (
        "\nFEATURE SPECIFICATIONS:\n"
        "The user wants you to implement each feature described below.\n"
        "Generate specific Aider-grade tasks for EACH feature specification.\n"
        "Each task instruction MUST reference exact details from the spec —\n"
        "function names, parameters, routes, fields, data shapes, etc.\n"
        "Do NOT generate vague 'implement feature X' tasks. The coding model\n"
        "is a small local LLM that cannot read the spec files itself.\n\n"
        f"{feature_specs}\n"
    )


def _build_model_roster_block(model_roster: Optional[str]) -> str:
    if not model_roster:
        return ""
    return (
        "\nAVAILABLE MODELS — pick the best model for each task:\n"
        f"{model_roster}\n\n"
        "Add a \"model\" field to each task JSON with the model name.\n"
        "Guidelines:\n"
        "- Use FAST models for: simple edits, config changes, renames,\n"
        "  single-function modifications, adding imports, small refactors\n"
        "- Use SLOW/HIGH-QUALITY models for: new algorithms, complex business\n"
        "  logic, multi-concern refactors, security-sensitive code, API design\n"
        "- When in doubt, prefer the fast model — speed matters more than\n"
        "  marginal quality for most coding tasks\n"
        "- Omit the \"model\" field to use the user's default model\n\n"
    )
