from __future__ import annotations

import logging
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from models.task import Task


class PlannerError(Exception):
    pass


class CodexClient:
    def __init__(self, repo_root: Path, command: str, logger: logging.Logger) -> None:
        self._repo_root: Path = repo_root
        self._command: str = command
        self._logger: logging.Logger = logger

    def generate_plan(self, goal: str, idea_text: Optional[str], feedback: Optional[str] = None) -> str:
        prompt: str = self._build_plan_prompt(goal, idea_text, feedback)
        return self._run_codex(prompt, self._build_plan_output_schema())

    def refine_task_instruction(self, task: Task, feedback: str) -> str:
        prompt: str = self._build_refinement_prompt(task, feedback)
        response: str = self._run_codex(prompt)
        refined_instruction: str = response.strip()
        if not refined_instruction:
            raise PlannerError(f"Planner returned an empty refined instruction for task {task.id}.")
        return refined_instruction

    def _run_codex(self, prompt: str, output_schema: Optional[str] = None) -> str:
        with tempfile.TemporaryDirectory(prefix="codex-bridge-") as temporary_directory:
            output_file: Path = Path(temporary_directory) / "planner-output.txt"
            schema_file: Optional[Path] = None
            if output_schema is not None:
                schema_file = Path(temporary_directory) / "planner-schema.json"
                schema_file.write_text(output_schema, encoding="utf-8")

            arguments: list[str] = self._build_command(prompt, output_file, schema_file)
            self._logger.debug("Running Codex command: %s", arguments)

            try:
                result: subprocess.CompletedProcess[str] = subprocess.run(
                    arguments,
                    cwd=self._repo_root,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=False,
                )
            except OSError as ex:
                raise PlannerError(f"Unable to start Codex command '{self._command}': {ex}") from ex

            if result.returncode != 0:
                raise PlannerError(
                    f"Codex planner failed with exit code {result.returncode}. Stderr: {result.stderr.strip()}"
                )

            if output_file.exists():
                output: str = output_file.read_text(encoding="utf-8").strip()
                if output:
                    return output

            stdout_output: str = result.stdout.strip()
            if stdout_output:
                return stdout_output

            raise PlannerError("Codex planner returned no output.")

    def _build_command(self, prompt: str, output_file: Path, schema_file: Optional[Path]) -> list[str]:
        command_text: str = self._command
        if "{prompt}" in command_text:
            command_text = command_text.replace("{prompt}", prompt)

        if "{output_file}" in command_text:
            command_text = command_text.replace("{output_file}", str(output_file))

        arguments: list[str] = shlex.split(command_text, posix=False)

        if "{output_file}" not in self._command and "exec" in arguments and "-o" not in arguments:
            arguments.extend(["-o", str(output_file)])

        if schema_file is not None and "--output-schema" not in arguments and "exec" in arguments:
            arguments.extend(["--output-schema", str(schema_file)])

        if "{prompt}" not in self._command:
            arguments.append(prompt)

        return arguments

    def _build_plan_prompt(self, goal: str, idea_text: Optional[str], feedback: Optional[str]) -> str:
        feedback_block: str = ""
        if feedback:
            feedback_block = (
                "\nPlanner feedback to correct in the next response:\n"
                f"{feedback}\n"
                "Correct the plan and return only the plan.\n"
            )

        idea_block: str = self._build_idea_summary_block(idea_text)
        files_and_systems: str = self._build_files_and_systems_summary(idea_text)
        expected_behavior: str = self._build_expected_behavior_summary(idea_text)
        constraints: str = self._build_constraints_summary(idea_text)
        validation_criteria: str = self._build_validation_summary(idea_text)

        return (
            "Return an atomic Aider execution plan only.\n"
            "Do not ask questions.\n"
            "If you cannot return JSON, return a numbered implementation plan where every step names specific relative files.\n"
            "Use only relative file paths. Never use absolute paths. Never target the repository root.\n"
            "Target feature/fix:\n"
            f"{goal}\n"
            "Files or systems involved:\n"
            f"{files_and_systems}\n"
            "Expected behavior:\n"
            f"{expected_behavior}\n"
            "Constraints or non-goals:\n"
            f"{constraints}\n"
            "Validation criteria:\n"
            f"{validation_criteria}\n"
            f"{idea_block}"
            f"{feedback_block}"
        )

    def _build_files_and_systems_summary(self, idea_text: Optional[str]) -> str:
        is_unity_project: bool = (self._repo_root / "Assets").exists()
        if not is_unity_project and idea_text is not None:
            is_unity_project = "unity" in idea_text.lower()

        if not is_unity_project:
            return "Use the most relevant repository files for the requested feature."

        return (
            "Assets/Scripts/Core/GameManager.cs, Assets/Scripts/Systems/PhaseManager.cs, "
            "Assets/Scripts/Player/PlayerController.cs, Assets/Scripts/Systems/LevelSpawner.cs, "
            "Assets/Scripts/Systems/LevelChunkAnchor.cs, Assets/Scripts/Systems/Platform.cs, "
            "Assets/Scripts/Systems/Obstacle.cs, Assets/Scripts/UI/UIManager.cs, "
            "Assets/Scenes/SampleScene.unity, README.md, CHANGELOG.md, AGENTS.md."
        )

    def _build_expected_behavior_summary(self, idea_text: Optional[str]) -> str:
        if idea_text is None:
            return "Implement the requested feature with production-ready behavior."

        lowered_idea: str = idea_text.lower()
        if "phase flip runner" in lowered_idea or "unity" in lowered_idea:
            return (
                "Produce a playable Unity vertical slice where the player auto-runs, one tap toggles phase, "
                "wrong-phase support causes failure, obstacles can kill, endless chunks spawn, score updates, "
                "and restart works."
            )

        return "Implement the requested feature with behavior aligned to the provided idea file."

    def _build_constraints_summary(self, idea_text: Optional[str]) -> str:
        if idea_text is None:
            return "Keep tasks atomic and implementation-ready for Aider."

        lowered_idea: str = idea_text.lower()
        if "unity" in lowered_idea:
            return (
                "Unity URP project, mobile-leaning, event-driven architecture, SOLID structure, "
                "object pooling where beneficial, simple colliders, avoid unnecessary Update usage, "
                "and keep tasks atomic with explicit relative paths."
            )

        return "Keep tasks atomic, technical, and scoped to specific relative files."

    def _build_validation_summary(self, idea_text: Optional[str]) -> str:
        if idea_text is None:
            return "The code should complete without obvious errors."

        lowered_idea: str = idea_text.lower()
        if "unity" in lowered_idea:
            return (
                "The project should reach a playable vertical slice with no obvious compile blockers, "
                "phase switching should function, run/death/restart flow should exist, and docs should be updated."
            )

        return "The requested implementation should complete successfully and update any required documentation."

    def _build_idea_summary_block(self, idea_text: Optional[str]) -> str:
        if not idea_text:
            return ""

        normalized_lines: list[str] = []
        for raw_line in idea_text.splitlines():
            stripped_line: str = raw_line.strip()
            if not stripped_line:
                continue
            normalized_lines.append(stripped_line)

        summary_lines: list[str] = []
        current_length: int = 0
        for normalized_line in normalized_lines:
            line_length: int = len(normalized_line)
            if current_length + line_length > 2200:
                break
            summary_lines.append(normalized_line)
            current_length += line_length + 1
            if len(summary_lines) >= 24:
                break

        if not summary_lines:
            return ""

        return "Idea summary:\n" + "\n".join(summary_lines) + "\n"

    def _build_plan_output_schema(self) -> str:
        return (
            "{\n"
            '  "type": "object",\n'
            '  "additionalProperties": false,\n'
            '  "required": ["tasks"],\n'
            '  "properties": {\n'
            '    "tasks": {\n'
            '      "type": "array",\n'
            '      "minItems": 1,\n'
            '      "items": {\n'
            '        "type": "object",\n'
            '        "additionalProperties": false,\n'
            '        "required": ["id", "files", "instruction", "type"],\n'
            '        "properties": {\n'
            '          "id": { "type": "integer" },\n'
            '          "files": {\n'
            '            "type": "array",\n'
            '            "minItems": 1,\n'
            '            "items": {\n'
            '              "type": "string",\n'
            '              "minLength": 1,\n'
            '              "pattern": "^(?![A-Za-z]:\\\\\\\\)(?![/\\\\\\\\]?$)(?![/\\\\\\\\]).+"\n'
            "            }\n"
            "          },\n"
            '          "instruction": { "type": "string", "minLength": 1 },\n'
            '          "type": { "type": "string", "enum": ["create", "modify", "validate"] }\n'
            "        }\n"
            "      }\n"
            "    }\n"
            "  }\n"
            "}\n"
        )

    def _build_refinement_prompt(self, task: Task, feedback: str) -> str:
        return (
            "You are refining a single atomic coding instruction after a failed execution or validation.\n"
            "Return ONLY the replacement instruction as plain text.\n"
            f"Task id: {task.id}\n"
            f"Task files: {', '.join(task.files)}\n"
            f"Task type: {task.type}\n"
            f"Original instruction: {task.instruction}\n"
            f"Failure feedback:\n{feedback}\n"
        )
