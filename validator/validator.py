from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

from models.task import Task, ValidationResult


class ProjectValidator:
    def __init__(self, repo_root: Path, validation_command: Optional[str], logger: logging.Logger) -> None:
        self._repo_root: Path = repo_root
        self._validation_command: Optional[str] = validation_command
        self._logger: logging.Logger = logger

    def validate(self, task: Task, file_paths: list[Path]) -> ValidationResult:
        existence_result: ValidationResult = self._validate_file_existence(task, file_paths)
        if not existence_result.succeeded:
            return existence_result

        python_result: ValidationResult = self._validate_python_sources(file_paths)
        if not python_result.succeeded:
            return python_result

        command_result: ValidationResult = self._run_custom_validation_command()
        if not command_result.succeeded:
            return command_result

        return ValidationResult(
            task_id=task.id,
            succeeded=True,
            message="Validation completed successfully.",
            stdout="",
            stderr="",
        )

    def _validate_file_existence(self, task: Task, file_paths: list[Path]) -> ValidationResult:
        missing_paths: list[str] = [str(path) for path in file_paths if not path.exists()]
        if missing_paths and task.type in {"create", "modify"}:
            return ValidationResult(
                task_id=task.id,
                succeeded=False,
                message=f"Expected files were not found after execution: {missing_paths}",
                stdout="",
                stderr="",
            )

        return ValidationResult(
            task_id=task.id,
            succeeded=True,
            message="File existence checks passed.",
            stdout="",
            stderr="",
        )

    def _validate_python_sources(self, file_paths: list[Path]) -> ValidationResult:
        python_files: list[Path] = [path for path in file_paths if path.suffix.lower() == ".py" and path.exists()]
        if not python_files:
            return ValidationResult(
                task_id=0,
                succeeded=True,
                message="No Python files to compile.",
                stdout="",
                stderr="",
            )

        arguments: list[str] = [sys.executable, "-m", "compileall"]
        for python_file in python_files:
            arguments.append(str(python_file))

        result: subprocess.CompletedProcess[str] = subprocess.run(
            arguments,
            cwd=self._repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

        return ValidationResult(
            task_id=0,
            succeeded=result.returncode == 0,
            message="Python compilation validation completed.",
            stdout=result.stdout,
            stderr=result.stderr,
        )

    def _run_custom_validation_command(self) -> ValidationResult:
        if not self._validation_command:
            return ValidationResult(
                task_id=0,
                succeeded=True,
                message="No custom validation command configured.",
                stdout="",
                stderr="",
            )

        result: subprocess.CompletedProcess[str] = subprocess.run(
            self._validation_command,
            cwd=self._repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            shell=True,
            check=False,
        )

        return ValidationResult(
            task_id=0,
            succeeded=result.returncode == 0,
            message="Custom validation command completed.",
            stdout=result.stdout,
            stderr=result.stderr,
        )
