from __future__ import annotations

import logging
import subprocess
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
    ) -> None:
        self._repo_root = repo_root
        self._command = command
        self._logger = logger
        self._model = model

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

        # Snapshot untracked files BEFORE running aider so we only clean up
        # files that aider itself creates, not pre-existing untracked work.
        pre_untracked = self._get_untracked_files()

        try:
            result = subprocess.run(
                arguments,
                cwd=self._repo_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
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

        # Revert any files aider touched that are outside the allowed scope
        self._revert_out_of_scope_changes(task.id, file_paths, pre_untracked)

        return ExecutionResult(
            task_id=task.id,
            succeeded=result.returncode == 0,
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            command=arguments,
        )

    def _get_untracked_files(self) -> set[str]:
        """Return a set of currently untracked file paths (repo-relative posix)."""
        try:
            out = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard"],
                cwd=self._repo_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            return {p.strip() for p in out.stdout.splitlines() if p.strip()}
        except Exception:
            return set()

    def _revert_out_of_scope_changes(
        self, task_id: int, allowed_paths: list[Path], pre_untracked: set[str]
    ) -> None:
        """Revert tracked files aider modified outside scope.
        Only remove NEW untracked files aider created (not files that existed before)."""
        allowed_rel = {
            p.relative_to(self._repo_root).as_posix()
            if p.is_absolute() else Path(p).as_posix()
            for p in allowed_paths
        }

        # --- Revert tracked files modified outside scope ---
        try:
            diff_out = subprocess.run(
                ["git", "diff", "--name-only"],
                cwd=self._repo_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            for rel_path in diff_out.stdout.splitlines():
                rel_path = rel_path.strip()
                if rel_path and rel_path not in allowed_rel:
                    self._logger.warning(
                        "Task %s: reverting out-of-scope tracked file: %s",
                        task_id,
                        rel_path,
                    )
                    subprocess.run(
                        ["git", "checkout", "--", rel_path],
                        cwd=self._repo_root,
                        check=False,
                    )
        except Exception as ex:
            self._logger.warning("Task %s: scope revert (tracked) failed: %s", task_id, ex)

        # --- Remove ONLY untracked files aider CREATED (not pre-existing ones) ---
        try:
            post_untracked = self._get_untracked_files()
            new_files = post_untracked - pre_untracked  # files that didn't exist before
            for rel_path in new_files:
                if rel_path not in allowed_rel:
                    full_path = self._repo_root / rel_path
                    self._logger.warning(
                        "Task %s: removing out-of-scope new file: %s",
                        task_id,
                        rel_path,
                    )
                    try:
                        full_path.unlink()
                    except Exception as ex:
                        self._logger.warning(
                            "Task %s: could not remove %s: %s", task_id, rel_path, ex
                        )
        except Exception as ex:
            self._logger.warning("Task %s: scope revert (untracked) failed: %s", task_id, ex)

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
