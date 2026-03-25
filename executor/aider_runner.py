from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

from models.task import ExecutionResult, Task
from utils.command_resolution import resolve_command_arguments


class AiderRunner:
    """Runs Aider against a single task, targeting a local LLM.

    Aider acts as the developer: it receives an atomic instruction and the
    specific files to modify, then applies the changes using its configured
    local model. Results (stdout, stderr, exit code) are returned to the
    bridge so the diff can be collected and sent to the supervisor for review.
    """

    def __init__(
        self,
        repo_root: Path,
        command: str,
        logger: logging.Logger,
        model: Optional[str] = None,
        timeout: int = 300,
    ) -> None:
        self._repo_root = repo_root
        self._command = command
        self._logger = logger
        self._model = model
        self._timeout = timeout

    def run(self, task: Task, file_paths: list[Path]) -> ExecutionResult:
        try:
            arguments, _ = self._build_command(task, file_paths)
        except (FileNotFoundError, ValueError) as ex:
            return ExecutionResult(
                task_id=task.id,
                succeeded=False,
                exit_code=-1,
                stdout="",
                stderr=str(ex),
                command=[self._command],
            )

        self._logger.debug("Running Aider: %s", arguments)

        _start = time.monotonic()
        try:
            result = subprocess.run(
                arguments,
                cwd=self._repo_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired as ex:
            if ex.process:
                ex.process.kill()
            return ExecutionResult(
                task_id=task.id,
                succeeded=False,
                exit_code=-1,
                stdout="",
                stderr=f"Aider timed out after {self._timeout}s",
                command=arguments,
            )
        except OSError as ex:
            return ExecutionResult(
                task_id=task.id,
                succeeded=False,
                exit_code=-1,
                stdout="",
                stderr=str(ex),
                command=arguments,
            )

        return ExecutionResult(
            task_id=task.id,
            succeeded=result.returncode == 0,
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            command=arguments,
            duration_seconds=round(time.monotonic() - _start, 2),
        )

    def _build_command(
        self, task: Task, file_paths: list[Path]
    ) -> tuple[list[str], list[Path]]:
        arguments, searched_locations = resolve_command_arguments(
            self._command, self._repo_root
        )

        # Local LLM model — e.g. ollama/mistral, ollama/codellama, ollama/deepseek-coder
        if self._model:
            arguments.extend(["--model", self._model])

        arguments.extend([
            "--yes-always",
            "--no-pretty",
            "--no-stream",
            "--no-auto-commits",
            "--message",
            task.instruction,
        ])

        for file_path in file_paths:
            arguments.extend(["--file", str(file_path)])

        return arguments, searched_locations
