from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from models.task import Task


class PlanParseError(Exception):
    pass


class TaskParser:
    def parse(self, raw_text: str) -> list[Task]:
        try:
            cleaned: str = self._extract_json(raw_text)
            payload: Any = json.loads(cleaned)
        except json.JSONDecodeError as ex:
            fallback_tasks: list[Task] = self._parse_numbered_plan(raw_text)
            if fallback_tasks:
                return fallback_tasks
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
                stripped = "\n".join(lines[1:-1]).strip()

        if stripped.startswith("{") and stripped.endswith("}"):
            return stripped

        marker_index: int = stripped.find('"tasks"')
        if marker_index >= 0:
            start_index: int = stripped.rfind("{", 0, marker_index)
            if start_index >= 0:
                brace_depth: int = 0
                current_index: int = start_index
                while current_index < len(stripped):
                    current_character: str = stripped[current_index]
                    if current_character == "{":
                        brace_depth += 1
                    elif current_character == "}":
                        brace_depth -= 1
                        if brace_depth == 0:
                            return stripped[start_index : current_index + 1]
                    current_index += 1

        return stripped

    def _parse_numbered_plan(self, raw_text: str) -> list[Task]:
        numbered_lines: list[str] = []
        for line in raw_text.splitlines():
            stripped_line: str = line.strip()
            if re.match(r"^\d+\.\s+", stripped_line):
                numbered_lines.append(stripped_line)

        if not numbered_lines:
            return []

        tasks: list[Task] = []
        all_seen_files: list[str] = []
        known_file_locations: dict[str, str] = {}

        for numbered_line in numbered_lines:
            match: re.Match[str] | None = re.match(r"^(\d+)\.\s+(.*)$", numbered_line)
            if match is None:
                continue

            task_id: int = int(match.group(1))
            instruction_text: str = match.group(2).strip()
            files: list[str] = self._extract_files_from_line(instruction_text, known_file_locations, all_seen_files)

            if not files and "all new files" in instruction_text.lower():
                files = list(all_seen_files)

            if not files:
                raise PlanParseError(f"Task {task_id} must include a non-empty 'files' array.")

            for file_path in files:
                if file_path not in all_seen_files:
                    all_seen_files.append(file_path)
                known_file_locations[Path(file_path).name] = file_path

            task: Task = self._parse_task(
                {
                    "id": task_id,
                    "files": files,
                    "instruction": instruction_text,
                    "type": self._infer_task_type(instruction_text),
                }
            )
            tasks.append(task)

        return tasks

    def _extract_files_from_line(
        self,
        instruction_text: str,
        known_file_locations: dict[str, str],
        all_seen_files: list[str],
    ) -> list[str]:
        backtick_matches: list[str] = re.findall(r"`([^`]+)`", instruction_text)
        extracted_files: list[str] = []
        directories: list[str] = []

        for backtick_match in backtick_matches:
            normalized_match: str = backtick_match.strip()
            if "/" in normalized_match or "\\" in normalized_match:
                if normalized_match.endswith("/") or normalized_match.endswith("\\"):
                    directories.append(normalized_match.rstrip("/\\"))
                    continue
                extracted_files.append(normalized_match.replace("\\", "/"))
                continue

            if normalized_match.endswith((".cs", ".md", ".unity", ".asset", ".prefab")):
                extracted_files.append(self._resolve_standalone_file(normalized_match, directories, known_file_locations))

        inline_matches: list[str] = re.findall(
            r"(?:(?:Assets|Packages|ProjectSettings|UserSettings)[/\\][^,\s]+|README\.md|CHANGELOG\.md|AGENTS\.md)",
            instruction_text,
        )
        for inline_match in inline_matches:
            normalized_inline_match: str = inline_match.replace("\\", "/")
            if normalized_inline_match not in extracted_files:
                extracted_files.append(normalized_inline_match)

        bare_filename_matches: list[str] = re.findall(r"\b[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+\b", instruction_text)
        for bare_filename_match in bare_filename_matches:
            if bare_filename_match in {"README.md", "CHANGELOG.md", "AGENTS.md"}:
                if bare_filename_match not in extracted_files:
                    extracted_files.append(bare_filename_match)
                continue

            if bare_filename_match.endswith((".cs", ".unity", ".asset", ".prefab")):
                resolved_match: str = self._resolve_standalone_file(
                    bare_filename_match,
                    directories,
                    known_file_locations,
                )
                if resolved_match not in extracted_files:
                    extracted_files.append(resolved_match)

        lower_instruction: str = instruction_text.lower()
        if not extracted_files and "project guidance" in lower_instruction:
            extracted_files.append("AGENTS.md")
        if not extracted_files and ("final compile" in lower_instruction or "style pass" in lower_instruction):
            extracted_files.extend(all_seen_files)
        if not extracted_files and ("scene objects" in lower_instruction or "prefabs" in lower_instruction):
            extracted_files.append("Assets/Scenes/SampleScene.unity")

        deduplicated_files: list[str] = []
        for extracted_file in extracted_files:
            normalized_file: str = extracted_file.replace("\\", "/")
            if normalized_file not in deduplicated_files:
                deduplicated_files.append(normalized_file)

        return deduplicated_files

    def _resolve_standalone_file(
        self,
        file_name: str,
        directories: list[str],
        known_file_locations: dict[str, str],
    ) -> str:
        if file_name in known_file_locations:
            return known_file_locations[file_name]

        if directories:
            return directories[-1] + "/" + file_name

        unity_default_directories: dict[str, str] = {
            "GameManager.cs": "Assets/Scripts/Core",
            "PhaseManager.cs": "Assets/Scripts/Systems",
            "PlayerController.cs": "Assets/Scripts/Player",
            "LevelSpawner.cs": "Assets/Scripts/Systems",
            "LevelChunkAnchor.cs": "Assets/Scripts/Systems",
            "Platform.cs": "Assets/Scripts/Systems",
            "Obstacle.cs": "Assets/Scripts/Systems",
            "UIManager.cs": "Assets/Scripts/UI",
        }
        if file_name in unity_default_directories:
            return unity_default_directories[file_name] + "/" + file_name

        return file_name

    def _infer_task_type(self, instruction_text: str) -> str:
        lower_instruction: str = instruction_text.lower()
        if lower_instruction.startswith("validate") or lower_instruction.startswith("run ") or "final pass" in lower_instruction:
            return "validate"
        if lower_instruction.startswith("create") or lower_instruction.startswith("add "):
            return "create"
        return "modify"

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
        for normalized_file in normalized_files:
            if Path(normalized_file).is_absolute():
                raise PlanParseError(f"Task {task_id} must use relative file paths, not absolute paths.")
            if normalized_file in {".", "./"}:
                raise PlanParseError(f"Task {task_id} must target specific files, not the repository root.")

        normalized_instruction: str = instruction.strip()
        lower_instruction: str = normalized_instruction.lower()
        if "clarify the coding task" in lower_instruction or "ask clarifying" in lower_instruction:
            raise PlanParseError(f"Task {task_id} asked for clarification instead of implementation work.")

        return Task(
            id=task_id,
            files=normalized_files,
            instruction=normalized_instruction,
            type=task_type,
        )
