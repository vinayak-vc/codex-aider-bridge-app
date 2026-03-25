from __future__ import annotations

import logging
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Optional

from models.task import Task, ValidationResult


# Maximum seconds the CI gate command is allowed to run before being killed.
_CI_TIMEOUT_SECONDS: int = 120


class MechanicalValidator:
    """Runs fast, token-free mechanical checks after each Aider execution.

    This validator handles structural correctness only:
    - Required files exist on disk after create/modify tasks.
    - Python source files compile without syntax errors.
    - Optional CI gate command passes (e.g. pytest, mypy).

    Quality review — deciding whether the implementation is correct or complete —
    is handled exclusively by the SupervisorAgent via diff review (PASS / REWORK).
    This validator never calls the supervisor and never spends supervisor tokens.
    """

    def __init__(
        self,
        repo_root: Path,
        validation_command: Optional[str],
        logger: logging.Logger,
    ) -> None:
        self._repo_root = repo_root
        self._validation_command = validation_command
        self._logger = logger

    def validate(self, task: Task, file_paths: list[Path]) -> ValidationResult:
        existence = self._check_file_existence(task, file_paths)
        if not existence.succeeded:
            return existence

        python_check = self._check_python_syntax(task.id, file_paths)
        if not python_check.succeeded:
            return python_check

        ci_check = self._run_ci_command(task.id)
        if not ci_check.succeeded:
            return ci_check

        return ValidationResult(
            task_id=task.id,
            succeeded=True,
            message="Mechanical checks passed.",
            stdout="",
            stderr="",
        )

    def _check_file_existence(self, task: Task, file_paths: list[Path]) -> ValidationResult:
        if task.type not in {"create", "modify"}:
            return ValidationResult(
                task_id=task.id,
                succeeded=True,
                message="File existence check skipped for validate tasks.",
                stdout="",
                stderr="",
            )

        missing = [str(p) for p in file_paths if not p.exists()]
        if missing:
            return ValidationResult(
                task_id=task.id,
                succeeded=False,
                message=f"Expected files missing after execution: {missing}",
                stdout="",
                stderr="",
            )

        return ValidationResult(
            task_id=task.id,
            succeeded=True,
            message="All expected files exist.",
            stdout="",
            stderr="",
        )

    def _check_python_syntax(self, task_id: int, file_paths: list[Path]) -> ValidationResult:
        py_files = [p for p in file_paths if p.suffix.lower() == ".py" and p.exists()]
        if not py_files:
            return ValidationResult(
                task_id=task_id,
                succeeded=True,
                message="No Python files to compile.",
                stdout="",
                stderr="",
            )

        result = subprocess.run(
            [sys.executable, "-m", "compileall"] + [str(p) for p in py_files],
            cwd=self._repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

        return ValidationResult(
            task_id=task_id,
            succeeded=result.returncode == 0,
            message="Python syntax check.",
            stdout=result.stdout,
            stderr=result.stderr,
        )

    def _run_ci_command(self, task_id: int) -> ValidationResult:
        if not self._validation_command:
            return ValidationResult(
                task_id=task_id,
                succeeded=True,
                message="No CI gate command configured.",
                stdout="",
                stderr="",
            )

        self._logger.debug("Running CI gate: %s", self._validation_command)

        try:
            cmd_parts = shlex.split(self._validation_command)
        except ValueError:
            return ValidationResult(
                task_id=task_id,
                succeeded=False,
                message=f"CI gate command could not be parsed: {self._validation_command!r}",
                stdout="",
                stderr="",
            )

        try:
            result = subprocess.run(
                cmd_parts,
                cwd=self._repo_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                shell=False,
                timeout=_CI_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ValidationResult(
                task_id=task_id,
                succeeded=False,
                message="CI gate command timed out after 120s",
                stdout="",
                stderr="",
            )

        return ValidationResult(
            task_id=task_id,
            succeeded=result.returncode == 0,
            message="CI gate command.",
            stdout=result.stdout,
            stderr=result.stderr,
        )


# Backwards-compatible alias
ProjectValidator = MechanicalValidator
