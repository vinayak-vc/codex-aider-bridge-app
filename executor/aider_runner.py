from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# On Windows, prevent spawned subprocesses from opening a visible CMD window.
_WIN_NO_WINDOW: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

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
_INTERACTIVE_PROMPT_PATTERNS: tuple[str, ...] = (
    "add file to the chat",
    "attempt to fix lint errors",
    "(y)es/(n)o",
    "[y/n]",
    "open docs url",
)


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
        self._repo_file_index: list[str] = self._build_repo_file_index()

    # ── Pre/post hash helpers (YourStore suggestion 2) ───────────────────────

    @staticmethod
    def _hash_file(path: Path) -> Optional[str]:
        """Return the SHA-256 hex digest of a file, or None if it does not exist."""
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return None

    def _snapshot_hashes(self, file_paths: list[Path]) -> dict[str, Optional[str]]:
        """Record the SHA-256 of every target file before Aider runs."""
        return {str(p): self._hash_file(p) for p in file_paths}

    @staticmethod
    def _is_trivial_change(before_content: Optional[bytes], after_content: Optional[bytes]) -> bool:
        """Return True if the only change between two file versions is whitespace/comments.

        Small local models (e.g. qwen2.5-coder:7b) sometimes touch whitespace or
        add a blank line, changing the file hash while leaving the logic unchanged.
        We normalise both versions (strip blank lines + C-style line comments) and
        compare — if the stripped versions are identical, the change is trivial.
        """
        if before_content is None or after_content is None:
            return False
        import re as _re

        def _strip(src: bytes) -> bytes:
            text = src.decode("utf-8", errors="replace")
            # Remove single-line // comments and blank lines
            lines = [
                _re.sub(r"\s*//.*$", "", line)
                for line in text.splitlines()
            ]
            return "\n".join(l for l in lines if l.strip()).encode()

        return _strip(before_content) == _strip(after_content)

    def _check_for_silent_failure(
        self,
        task_id: int,
        task_type: str,
        file_paths: list[Path],
        before: dict[str, Optional[str]],
        before_contents: Optional[dict[str, Optional[bytes]]] = None,
    ) -> Optional[str]:
        """Return an error message if Aider reported success but changed nothing meaningful.

        Two levels of detection:
          1. Hash unchanged  → file not touched at all.
          2. Trivial change  → only whitespace/comments changed (local model trick).

        Checks create/modify tasks for real content changes and delete tasks for
        actual removal. validate tasks may legitimately leave file content unchanged.
        """
        if task_type == "delete":
            undeleted: list[str] = [path.name for path in file_paths if path.exists()]
            if undeleted:
                self._logger.warning(
                    "Task %s: delete task left files on disk: %s",
                    task_id,
                    ", ".join(undeleted),
                )
                return (
                    f"Delete task did not remove: {', '.join(undeleted)}. "
                    "Delete the specified files instead of editing or documenting them."
                )
            return None

        if task_type not in {"create", "modify"}:
            return None

        unchanged: list[str] = []
        trivial: list[str] = []

        for path in file_paths:
            key = str(path)
            after_hash = self._hash_file(path)
            pre_hash = before.get(key)

            if pre_hash == after_hash:
                # For CREATE tasks: file didn't exist before AND still doesn't →
                # that means Aider never created it — this IS a failure, not a skip.
                if pre_hash is None and not path.exists():
                    if task_type == "create":
                        unchanged.append(path.name)
                    # For modify/validate: file simply wasn't touched → skip
                    continue
                unchanged.append(path.name)
                continue

            # Hash changed — check if it's only a trivial whitespace/comment edit
            if before_contents is not None:
                pre_bytes = before_contents.get(key)
                try:
                    after_bytes = path.read_bytes() if path.exists() else None
                except OSError:
                    after_bytes = None
                if self._is_trivial_change(pre_bytes, after_bytes):
                    trivial.append(path.name)

        problems: list[str] = []
        if unchanged:
            self._logger.warning(
                "Task %s: Aider exited 0 but these file(s) are unchanged: %s",
                task_id, ", ".join(unchanged),
            )
            problems.append(
                f"Aider reported success but did not modify: {', '.join(unchanged)}. "
                "Implement the task completely — do not skip or stub."
            )
        if trivial:
            self._logger.warning(
                "Task %s: Aider made only whitespace/comment changes to: %s",
                task_id, ", ".join(trivial),
            )
            problems.append(
                f"Aider only changed whitespace or comments in: {', '.join(trivial)}. "
                "Implement the actual logic changes required — do not just reformat."
            )
        return "\n".join(problems) if problems else None

    def _detect_interactive_prompt_output(self, stdout: str, stderr: str) -> Optional[str]:
        combined = "\n".join([stdout, stderr]).lower()
        matched_patterns: list[str] = []
        for pattern in _INTERACTIVE_PROMPT_PATTERNS:
            if pattern in combined:
                matched_patterns.append(pattern)

        if not matched_patterns:
            return None

        self._logger.warning(
            "Aider emitted interactive prompt text during automated run: %s",
            ", ".join(matched_patterns),
        )
        return (
            "Aider emitted an interactive prompt during a non-interactive bridge run "
            f"({', '.join(matched_patterns)}). Re-run with a narrower task or adjust the prompt "
            "so Aider does not request confirmation."
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

        # Snapshot file hashes AND raw bytes before Aider runs.
        # Bytes are needed for the trivial-change detector (whitespace-only edits).
        pre_hashes = self._snapshot_hashes(file_paths)
        pre_contents: dict[str, Optional[bytes]] = {
            str(p): (p.read_bytes() if p.exists() else None) for p in file_paths
        }

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
                creationflags=_WIN_NO_WINDOW,
                stdin=subprocess.DEVNULL,  # Never wait for input — prevents hang on hidden prompts
            )
        except subprocess.TimeoutExpired as ex:
            # ex.process is not always set (Python 3.11 bug on Windows)
            try:
                if ex.process:
                    ex.process.kill()
            except Exception:
                pass
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

        # Scope enforcement: revert any files the model touched that were NOT
        # in the task's file list. Reasoning models (deepseek-r1, etc.) tend to
        # "helpfully" edit nearby files, which can corrupt binary assets or cause
        # unexpected side-effects.
        self._revert_out_of_scope_changes(task.id, file_paths)

        # Detect silent failure — Aider exits 0 but never actually writes the
        # target files, or only makes trivial whitespace/comment changes.
        silent_failure_msg = self._check_for_silent_failure(
            task.id, task.type, file_paths, pre_hashes, pre_contents
        )
        if silent_failure_msg and result.returncode == 0:
            return ExecutionResult(
                task_id=task.id,
                succeeded=False,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=silent_failure_msg,
                command=arguments,
                duration_seconds=round(time.monotonic() - _start, 2),
            )

        interactive_prompt_msg = self._detect_interactive_prompt_output(
            result.stdout,
            result.stderr,
        )
        if interactive_prompt_msg is not None:
            return ExecutionResult(
                task_id=task.id,
                succeeded=False,
                exit_code=result.returncode if result.returncode != 0 else -1,
                stdout=result.stdout,
                stderr=interactive_prompt_msg,
                command=arguments,
                duration_seconds=round(time.monotonic() - _start, 2),
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

    # ── Scope enforcement ─────────────────────────────────────────────────────

    def _revert_out_of_scope_changes(
        self,
        task_id: str,
        allowed_paths: list[Path],
    ) -> None:
        """Revert any files modified by Aider that were NOT in the task's file list.

        Reasoning models (deepseek-r1, qwen-thinking, etc.) often "helpfully" edit
        nearby files — this can corrupt binary assets, Unity .asset files, or cause
        unintended side-effects.  We detect all dirty working-tree files via
        ``git diff --name-only`` and restore anything outside *allowed_paths*.
        """
        try:
            proc = subprocess.run(
                ["git", "diff", "--name-only"],
                cwd=self._repo_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                creationflags=_WIN_NO_WINDOW,
            )
            if proc.returncode != 0:
                return  # not a git repo or git unavailable — skip quietly

            # Resolve allowed paths to strings relative to repo root for comparison
            allowed_rel: set[str] = set()
            for p in allowed_paths:
                try:
                    allowed_rel.add(str(p.resolve().relative_to(self._repo_root.resolve())))
                except ValueError:
                    allowed_rel.add(p.name)  # fallback: match by name only

            # Normalise to forward-slash for cross-platform comparison
            allowed_rel = {r.replace("\\", "/") for r in allowed_rel}

            out_of_scope: list[str] = []
            for rel_path in proc.stdout.splitlines():
                rel_norm = rel_path.strip().replace("\\", "/")
                if not rel_norm:
                    continue
                if rel_norm not in allowed_rel:
                    out_of_scope.append(rel_path.strip())

            if not out_of_scope:
                return

            self._logger.warning(
                "Task %s: model edited %d out-of-scope file(s) — reverting: %s",
                task_id,
                len(out_of_scope),
                ", ".join(out_of_scope),
            )
            # Revert each file individually so a bad path doesn't abort the batch
            for rel in out_of_scope:
                revert = subprocess.run(
                    ["git", "checkout", "--", rel],
                    cwd=self._repo_root,
                    capture_output=True,
                    timeout=15,
                    creationflags=_WIN_NO_WINDOW,
                )
                if revert.returncode != 0:
                    self._logger.debug(
                        "Task %s: could not revert %s (may be untracked — ignoring)", task_id, rel
                    )
        except Exception as exc:  # noqa: BLE001
            self._logger.debug("Task %s: scope-enforcement check failed: %s", task_id, exc)

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
            "--no-auto-lint",
            "--no-auto-commits",
            "--no-gitignore",            # suppress "add .aiderignore?" interactive prompt
            "--no-show-model-warnings",  # suppress model-warning + "Open docs url?" prompt
            "--message",
            self._build_message(task, aider_context, file_paths),
        ])

        if self._no_map:
            arguments.extend(["--map-tokens", "0"])

        # Feature 4: task-level context_files via --read (read-only reference).
        read_only_paths: list[Path] = []

        for cf in task.context_files:
            cf_path = self._repo_root / cf
            if cf_path.exists():
                read_only_paths.append(cf_path)
            else:
                self._logger.debug("context_file not found, skipping --read: %s", cf)

        # Feature 2: project-wide standards files via --read.
        for sf in self._standards_files:
            read_only_paths.append(sf)

        for mentioned_path in self._find_instruction_reference_files(task, file_paths):
            read_only_paths.append(mentioned_path)

        unique_read_only_paths: list[Path] = []
        seen_read_only_paths: set[str] = set()
        for read_only_path in read_only_paths:
            normalized_path = str(read_only_path.resolve()).lower()
            if normalized_path in seen_read_only_paths:
                continue
            seen_read_only_paths.add(normalized_path)
            unique_read_only_paths.append(read_only_path)

        for read_only_path in unique_read_only_paths:
            arguments.extend(["--read", str(read_only_path)])

        # Files Aider will modify.
        for file_path in file_paths:
            arguments.extend(["--file", str(file_path)])

        return arguments, searched_locations

    # ── Message template (Feature 1) ─────────────────────────────────────────

    def _build_message(
        self,
        task: Task,
        ctx: Optional[AiderContext],
        file_paths: Optional[list[Path]] = None,
    ) -> str:
        """Build a structured prompt that gives Aider full project context.

        file_paths are injected as an explicit TARGET FILES section so small
        local models (e.g. qwen2.5-coder:7b) that ignore --file CLI flags
        still know exactly which absolute paths to edit and never create
        stray files at the repo root.
        """
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

        # ── TARGET FILES (path-enforcement for local models) ──────────────────
        # Always list absolute paths explicitly so the model cannot guess wrong.
        if file_paths:
            path_lines = "\n".join(f"  {p.resolve()}" for p in file_paths)
            sections.append(
                f"TARGET FILES — edit ONLY these exact paths, nothing else:\n{path_lines}"
            )

        # Rules — prevent the most common Aider failure modes
        rules: list[str] = [
            "RULES",
            "  - CRITICAL: edit only the TARGET FILES listed above — do NOT create"
            " new files or write to any other path",
            "  - CRITICAL: use the exact absolute path shown — do not change the"
            " filename, directory, or extension",
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

    def _build_repo_file_index(self) -> list[str]:
        indexed_files: list[str] = []
        for path in self._repo_root.rglob("*"):
            if not path.is_file():
                continue
            try:
                relative_path = path.relative_to(self._repo_root).as_posix()
            except ValueError:
                continue
            if relative_path.startswith(".git/"):
                continue
            indexed_files.append(relative_path)
        return indexed_files

    def _find_instruction_reference_files(
        self,
        task: Task,
        file_paths: list[Path],
    ) -> list[Path]:
        instruction = task.instruction
        target_paths = {str(path.resolve()).lower() for path in file_paths}
        referenced_paths: list[Path] = []

        for relative_path in self._repo_file_index:
            if relative_path in task.files:
                continue
            pattern = r"(?<![A-Za-z0-9_./\\\\-])" + re.escape(relative_path) + r"(?![A-Za-z0-9_./\\\\-])"
            if re.search(pattern, instruction):
                candidate = (self._repo_root / relative_path).resolve()
                if str(candidate).lower() in target_paths:
                    continue
                referenced_paths.append(candidate)

        if referenced_paths:
            self._logger.debug(
                "AiderRunner: auto-injecting instruction reference files via --read: %s",
                [str(path.relative_to(self._repo_root)) for path in referenced_paths],
            )

        return referenced_paths
