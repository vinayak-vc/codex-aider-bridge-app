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
        if not isinstance(tasks_payload, list) or not tasks_payload:
            raise PlanParseError(
                "Supervisor JSON must contain a non-empty 'tasks' array."
            )

        return [self._parse_task(item) for item in tasks_payload]

    def _extract_json(self, raw_text: str) -> str:
        stripped = raw_text.strip()

        # Strip markdown code fences (```json ... ``` or ``` ... ```)
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

        if not isinstance(task_id, int):
            raise PlanParseError("Task 'id' must be an integer.")
        if not isinstance(files, list) or not files:
            raise PlanParseError(f"Task {task_id} must include a non-empty 'files' array.")
        if not all(isinstance(fp, str) and fp.strip() for fp in files):
            raise PlanParseError(f"Task {task_id} contains an invalid file path.")
        if not isinstance(instruction, str) or not instruction.strip():
            raise PlanParseError(f"Task {task_id} must include a non-empty 'instruction'.")
        if not isinstance(task_type, str) or task_type not in {"create", "modify", "validate"}:
            raise PlanParseError(
                f"Task {task_id} has an unsupported type {task_type!r}. "
                "Must be one of: create, modify, validate."
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

        return Task(
            id=task_id,
            files=normalized_files,
            instruction=normalized_instruction,
            type=task_type,
        )
