from __future__ import annotations

import logging
import shlex
import subprocess
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

    def generate_plan(self, goal: str, feedback: Optional[str] = None) -> str:
        prompt: str = self._build_plan_prompt(goal, feedback)
        return self._run_codex(prompt)

    def refine_task_instruction(self, task: Task, feedback: str) -> str:
        prompt: str = self._build_refinement_prompt(task, feedback)
        response: str = self._run_codex(prompt)
        refined_instruction: str = response.strip()
        if not refined_instruction:
            raise PlannerError(f"Planner returned an empty refined instruction for task {task.id}.")
        return refined_instruction

    def _run_codex(self, prompt: str) -> str:
        arguments: list[str] = self._build_command(prompt)
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

        output: str = result.stdout.strip()
        if not output:
            raise PlannerError("Codex planner returned no output.")

        return output

    def _build_command(self, prompt: str) -> list[str]:
        if "{prompt}" in self._command:
            command_text: str = self._command.replace("{prompt}", prompt)
            return shlex.split(command_text, posix=False)

        arguments: list[str] = shlex.split(self._command, posix=False)
        arguments.append(prompt)
        return arguments

    def _build_plan_prompt(self, goal: str, feedback: Optional[str]) -> str:
        feedback_block: str = ""
        if feedback:
            feedback_block = (
                "\nPrevious planner feedback:\n"
                f"{feedback}\n"
                "Correct the response and return only valid JSON.\n"
            )

        return (
            "You are generating an execution plan for a coding task.\n"
            "Return ONLY valid JSON with this exact shape:\n"
            "{\n"
            '  "tasks": [\n'
            "    {\n"
            '      "id": 1,\n'
            '      "files": ["relative/path.ext"],\n'
            '      "instruction": "Atomic implementation step",\n'
            '      "type": "create"\n'
            "    }\n"
            "  ]\n"
            "}\n"
            "Rules:\n"
            "- Tasks must be small and atomic.\n"
            "- Every task must include specific relative file paths.\n"
            "- Use only create, modify, or validate for type.\n"
            "- Do not include markdown fences.\n"
            f"Goal:\n{goal}\n"
            f"{feedback_block}"
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
