from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from models.task import AiderContext, ExecutionResult, Task
from utils.command_resolution import resolve_command_arguments


# Standards files auto-injected as --read context when found in the repo root.
# Listed in priority order — first match is logged; all found are injected.
_STANDARDS_FILENAMES: list[str] = [
    "CODE_FORMAT_STANDARDS.md",
    "CODING_STANDARDS.md",
    "STYLE_GUIDE.md",
    ".editorconfig",
    "CONTRIBUTING.md",
]


class AiderRunner:
    """Runs Aider against a single task, targeting a local LLM.

    Each run wraps the task instruction in a structured message template so
    Aider has full context: overall goal, task position, what is already done,
    and project-specific code standards. Read-only context files (code standards
    and task-level references) are injected via --read so Aider can consult them
    without accidentally modifying them.
    """

    def __init__(
        self,
        repo_root: Path,
        command: str,
        logger: logging.Logger,
        model: Optional[str] = None,
        timeout: int = 300,
        no_map: bool = False,
    ) -> None:
        self._repo_root = repo_root
        self._command = command
        self._logger = logger
        self._model = model
        self._timeout = timeout
        self._no_map = no_map
        # Feature 2: auto-detect code standards files for --read injection.
        self._standards_files: list[Path] = self._find_standards_files()
        if self._standards_files:
            self._logger.info(
                "AiderRunner: auto-injecting standards via --read: %s",
                [p.name for p in self._standards_files],
            )

    def run(
        self,
        task: Task,
        file_paths: list[Path],
        aider_context: Optional[AiderContext] = None,
    ) -> ExecutionResult:
        try:
            arguments, _ = self._build_command(task, file_paths, aider_context)
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

        # Force UTF-8 in the Aider subprocess so rich/charmap errors don't
        # cause a silent crash on Windows consoles (e.g. deepseek special tokens).
        _subprocess_env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}

        _start = time.monotonic()
        try:
            result = subprocess.run(
                arguments,
                cwd=self._repo_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=self._timeout,
                env=_subprocess_env,
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

    # ── Command builder ───────────────────────────────────────────────────────

    def _build_command(
        self,
        task: Task,
        file_paths: list[Path],
        aider_context: Optional[AiderContext],
    ) -> tuple[list[str], list[Path]]:
        arguments, searched_locations = resolve_command_arguments(
            self._command, self._repo_root
        )

        if self._model:
            arguments.extend(["--model", self._model])

        arguments.extend([
            "--yes-always",
            "--no-pretty",
            "--no-stream",
            "--no-auto-commits",
            "--no-gitignore",            # suppress "add .aiderignore?" interactive prompt
            "--no-show-model-warnings",  # suppress model-warning + "Open docs url?" prompt
            "--message",
            self._build_message(task, aider_context),
        ])

        if self._no_map:
            arguments.extend(["--map-tokens", "0"])

        # Feature 4: task-level context_files via --read (read-only reference).
        for cf in task.context_files:
            cf_path = self._repo_root / cf
            if cf_path.exists():
                arguments.extend(["--read", str(cf_path)])
            else:
                self._logger.debug("context_file not found, skipping --read: %s", cf)

        # Feature 2: project-wide standards files via --read.
        for sf in self._standards_files:
            arguments.extend(["--read", str(sf)])

        # Files Aider will modify.
        for file_path in file_paths:
            arguments.extend(["--file", str(file_path)])

        return arguments, searched_locations

    # ── Message template (Feature 1) ─────────────────────────────────────────

    def _build_message(
        self, task: Task, ctx: Optional[AiderContext]
    ) -> str:
        """Build a structured prompt that gives Aider full project context."""
        sections: list[str] = []

        if ctx:
            # Goal
            goal_text = ctx.goal[:400].rstrip()
            sections.append(f"GOAL\n{goal_text}")

            # Task position
            sections.append(
                f"TASK {ctx.task_number} OF {ctx.total_tasks} ({task.type.upper()})\n"
                f"{task.instruction}"
            )

            # What is already done (last 5 tasks to stay concise)
            if ctx.completed_summaries:
                done_lines = "\n".join(
                    f"  {s}" for s in ctx.completed_summaries[-5:]
                )
                sections.append(f"ALREADY COMPLETED\n{done_lines}")
        else:
            sections.append(task.instruction)

        # Rules — prevent the most common Aider failure modes
        rules: list[str] = [
            "RULES",
            "  - Only write to the task files listed — do not create extra files",
            "  - Do not ask questions or request clarification — implement directly",
            "  - Do not write TODO/stub placeholders — write complete working code",
            "  - Do not remove existing unrelated code",
        ]
        if self._standards_files:
            names = ", ".join(p.name for p in self._standards_files)
            rules.append(f"  - Follow {names} (loaded as read-only context)")
        sections.append("\n".join(rules))

        return "\n\n".join(sections)

    # ── Standards file detection (Feature 2) ─────────────────────────────────

    def _find_standards_files(self) -> list[Path]:
        """Return all known code standards files found in the repo root."""
        found: list[Path] = []
        for name in _STANDARDS_FILENAMES:
            candidate = self._repo_root / name
            if candidate.exists():
                found.append(candidate)
        return found
