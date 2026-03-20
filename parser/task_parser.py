from __future__ import annotations

import json
from typing import Any

from models.task import Task


class PlanParseError(Exception):
    pass


class TaskParser:
    def parse(self, raw_text: str) -> list[Task]:
        cleaned: str = self._extract_json(raw_text)

        try:
            payload: Any = json.loads(cleaned)
        except json.JSONDecodeError as ex:
            raise PlanParseError(f"Planner returned invalid JSON: {ex}") from ex

        if not isinstance(payload, dict):
            raise PlanParseError("Planner JSON root must be an object.")

        tasks_payload: Any = payload.get("tasks")
        if not isinstance(tasks_payload, list) or not tasks_payload:
            raise PlanParseError("Planner JSON must contain a non-empty 'tasks' array.")

        parsed_tasks: list[Task] = []
        for item in tasks_payload:
            parsed_tasks.append(self._parse_task(item))

        return parsed_tasks

    def _extract_json(self, raw_text: str) -> str:
        stripped: str = raw_text.strip()
        if stripped.startswith("```"):
            lines: list[str] = stripped.splitlines()
            if len(lines) >= 3:
                return "\n".join(lines[1:-1]).strip()
        return stripped

    def _parse_task(self, item: Any) -> Task:
        if not isinstance(item, dict):
            raise PlanParseError("Each task entry must be an object.")

        task_id: Any = item.get("id")
        files: Any = item.get("files")
        instruction: Any = item.get("instruction")
        task_type: Any = item.get("type")

        if not isinstance(task_id, int):
            raise PlanParseError("Task 'id' must be an integer.")
        if not isinstance(files, list) or not files:
            raise PlanParseError(f"Task {task_id} must include a non-empty 'files' array.")
        if not all(isinstance(file_path, str) and file_path.strip() for file_path in files):
            raise PlanParseError(f"Task {task_id} contains an invalid file path.")
        if not isinstance(instruction, str) or not instruction.strip():
            raise PlanParseError(f"Task {task_id} must include a non-empty 'instruction'.")
        if not isinstance(task_type, str) or task_type not in {"create", "modify", "validate"}:
            raise PlanParseError(f"Task {task_id} has an unsupported 'type'.")

        normalized_files: list[str] = [file_path.strip() for file_path in files]
        return Task(
            id=task_id,
            files=normalized_files,
            instruction=instruction.strip(),
            type=task_type,
        )
