from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from models.task import Task


class PlanParseError(Exception):
    pass


class TaskParser:
    """Parses the supervisor's JSON plan into typed Task objects.

    The supervisor is expected to return strict JSON. This parser:
    - Strips markdown code fences if the supervisor wraps output in them.
    - Extracts a JSON object by brace-matching when the supervisor adds
      surrounding prose.
    - Validates each task field and rejects anything non-conformant so the
      caller can feed the error back to the supervisor as correction feedback.

    Numbered-plan fallback and hardcoded file lookups have been removed.
    The supervisor owns file path decisions; the parser only validates them.
    """

    def parse(self, raw_text: str) -> list[Task]:
        try:
            cleaned = self._extract_json(raw_text)
            payload: Any = json.loads(cleaned)
        except json.JSONDecodeError as ex:
            raise PlanParseError(
                f"Supervisor returned invalid JSON: {ex}. "
                "Ensure the supervisor returns only a JSON object with a 'tasks' array."
            ) from ex

        if not isinstance(payload, dict):
            raise PlanParseError("Supervisor JSON root must be an object.")

        tasks_payload: Any = payload.get("tasks")

        # If root is a dict but no "tasks" key, check if it looks like a
        # single task or if the tasks are under a different key
        if tasks_payload is None:
            # Maybe Claude returned {"plan": {"tasks": [...]}} or similar nesting
            for key, val in payload.items():
                if isinstance(val, list) and val and isinstance(val[0], dict) and "instruction" in val[0]:
                    tasks_payload = val
                    break
                if isinstance(val, dict) and "tasks" in val:
                    tasks_payload = val["tasks"]
                    break

        # Maybe Claude returned a bare array [{...}, {...}] instead of {"tasks": [...]}
        if tasks_payload is None and isinstance(payload, list):
            tasks_payload = payload

        if not isinstance(tasks_payload, list) or not tasks_payload:
            print(f"[PARSER] Cannot find tasks in response. Keys: {list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__}", flush=True)
            print(f"[PARSER] First 500 chars: {str(payload)[:500]}", flush=True)
            raise PlanParseError(
                "Supervisor JSON must contain a non-empty 'tasks' array."
            )

        return [self._parse_task(item) for item in tasks_payload]

    def _extract_json(self, raw_text: str) -> str:
        stripped = raw_text.strip()

        # Strip markdown code fences anywhere in the text
        # Handles: ```json\n{...}\n``` or prose before/after fences
        import re as _re
        fence_match = _re.search(r'```(?:json)?\s*\n(.*?)\n```', stripped, _re.DOTALL)
        if fence_match:
            stripped = fence_match.group(1).strip()

        # Legacy: fences at the start only
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if len(lines) >= 3:
                stripped = "\n".join(lines[1:-1]).strip()

        if stripped.startswith("{") and stripped.endswith("}"):
            return stripped

        # Find JSON object by locating the "tasks" key and brace-matching
        marker_index = stripped.find('"tasks"')
        if marker_index >= 0:
            start_index = stripped.rfind("{", 0, marker_index)
            if start_index >= 0:
                depth = 0
                i = start_index
                while i < len(stripped):
                    c = stripped[i]
                    if c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                        if depth == 0:
                            return stripped[start_index: i + 1]
                    i += 1

        return stripped

    def _parse_task(self, item: Any) -> Task:
        if not isinstance(item, dict):
            raise PlanParseError("Each task entry must be a JSON object.")

        task_id: Any = item.get("id")
        files: Any = item.get("files")
        instruction: Any = item.get("instruction")
        task_type: Any = item.get("type")
        context_files: Any = item.get("context_files", [])
        must_exist: Any = item.get("must_exist", [])
        must_not_exist: Any = item.get("must_not_exist", [])

        # Fix #5: coerce string IDs like "1" or "task-1" to int where possible.
        if not isinstance(task_id, int):
            if isinstance(task_id, str):
                # Strip common prefixes: "task-3" → 3, "step_2" → 2, "3" → 3
                digits = "".join(c for c in task_id if c.isdigit())
                if digits:
                    task_id = int(digits)
                else:
                    raise PlanParseError(
                        f"Task 'id' cannot be coerced to an integer: {task_id!r}"
                    )
            else:
                raise PlanParseError("Task 'id' must be an integer.")

        # Coerce files: string → [string], None → []
        if isinstance(files, str) and files.strip():
            files = [files.strip()]
        elif files is None:
            files = []

        # Fix #6: allow empty files array — Aider will use repo-map to pick files.
        if not isinstance(files, list):
            raise PlanParseError(f"Task {task_id} 'files' must be an array.")
        if files and not all(isinstance(fp, str) and fp.strip() for fp in files):
            raise PlanParseError(f"Task {task_id} contains an invalid file path.")
        if not isinstance(instruction, str) or not instruction.strip():
            raise PlanParseError(f"Task {task_id} must include a non-empty 'instruction'.")
        if not isinstance(task_type, str) or task_type not in {"create", "modify", "delete", "validate", "read", "investigate"}:
            raise PlanParseError(
                f"Task {task_id} has an unsupported type {task_type!r}. "
                "Must be one of: create, modify, delete, validate, read, investigate."
            )

        normalized_files = [fp.strip() for fp in files]
        for fp in normalized_files:
            if Path(fp).is_absolute():
                raise PlanParseError(
                    f"Task {task_id} must use relative file paths. Got absolute: {fp}"
                )
            if re.match(r"^[A-Za-z]:(?![\\/])", fp):
                raise PlanParseError(
                    f"Task {task_id} has a malformed Windows drive-relative path: {fp}"
                )
            if fp in {".", "./"}:
                raise PlanParseError(
                    f"Task {task_id} must target specific files, not the repository root."
                )
            if ".." in Path(fp).parts:
                raise PlanParseError(
                    f"Task {task_id} contains a path traversal sequence: {fp!r}. "
                    "All file paths must stay within the repository root."
                )

        normalized_instruction = instruction.strip()
        lower = normalized_instruction.lower()
        if "ask clarifying" in lower or "clarify the coding task" in lower:
            raise PlanParseError(
                f"Task {task_id} asked for clarification instead of specifying implementation work."
            )

        # Feature 4: optional context_files — read-only references passed via --read.
        normalized_context_files: list[str] = []
        if isinstance(context_files, list):
            for cf in context_files:
                if not isinstance(cf, str) or not cf.strip():
                    continue
                cf = cf.strip()
                if ".." in Path(cf).parts or Path(cf).is_absolute():
                    raise PlanParseError(
                        f"Task {task_id} context_files contains invalid path: {cf!r}"
                    )
                normalized_context_files.append(cf)

        normalized_must_exist = self._normalize_relative_paths(
            task_id, must_exist, "must_exist"
        )
        normalized_must_not_exist = self._normalize_relative_paths(
            task_id, must_not_exist, "must_not_exist"
        )

        # Optional model field — supervisor may recommend a specific model per task
        task_model: Any = item.get("model")
        if task_model is not None and not isinstance(task_model, str):
            task_model = None  # ignore invalid model values silently

        return Task(
            id=task_id,
            files=normalized_files,
            instruction=normalized_instruction,
            type=task_type,
            context_files=normalized_context_files,
            must_exist=normalized_must_exist,
            must_not_exist=normalized_must_not_exist,
            model=task_model,
        )

    def _normalize_relative_paths(
        self,
        task_id: int,
        raw_paths: Any,
        field_name: str,
    ) -> list[str]:
        normalized: list[str] = []
        if raw_paths is None:
            return normalized
        if not isinstance(raw_paths, list):
            raise PlanParseError(
                f"Task {task_id} field {field_name!r} must be an array of relative paths."
            )
        for raw_path in raw_paths:
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise PlanParseError(
                    f"Task {task_id} field {field_name!r} contains an invalid path."
                )
            file_path = raw_path.strip()
            if Path(file_path).is_absolute() or ".." in Path(file_path).parts:
                raise PlanParseError(
                    f"Task {task_id} field {field_name!r} contains invalid path: {file_path!r}"
                )
            normalized.append(file_path)
        return normalized
