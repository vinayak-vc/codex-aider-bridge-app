from __future__ import annotations

import logging
import shlex
import subprocess
from pathlib import Path

from models.task import ExecutionResult, Task


class AiderRunner:
    def __init__(self, repo_root: Path, command: str, logger: logging.Logger) -> None:
        self._repo_root: Path = repo_root
        self._command: str = command
        self._logger: logging.Logger = logger

    def run(self, task: Task, file_paths: list[Path]) -> ExecutionResult:
        arguments: list[str] = self._build_command(task, file_paths)
        self._logger.debug("Running Aider command: %s", arguments)

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
        )

    def _build_command(self, task: Task, file_paths: list[Path]) -> list[str]:
        arguments: list[str] = shlex.split(self._command, posix=False)
        arguments.extend(
            [
                "--yes-always",
                "--no-pretty",
                "--no-stream",
                "--no-auto-commits",
                "--message",
                task.instruction,
            ]
        )

        for file_path in file_paths:
            arguments.extend(["--file", str(file_path)])

        return arguments
