from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from pathlib import PurePosixPath
from typing import Optional

# On Windows, prevent spawned subprocesses from opening a visible CMD window.
_WIN_NO_WINDOW: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

from executor.aider_config import (
    FATAL_ERROR_PATTERNS,
    INTERACTIVE_PROMPT_PATTERNS,
    STANDARDS_FILENAMES,
    USELESS_RESPONSE_PATTERNS,
)
from models.task import AiderContext, ExecutionResult, Task
from utils.command_resolution import resolve_command_arguments


class AiderRunner:
    """Runs Aider against a single task, targeting a local LLM.

    Each run wraps the task instruction in a structured message template so
    Aider has full context: overall goal, task position, what is already done,
    and project-specific code standards. Read-only context files (code standards
    and task-level references) are injected via --read so Aider can consult them
    without accidentally modifying them.

    Performance tuning for local models:
      - ``--edit-format diff`` → LLM outputs only the changed lines, not the
        entire file.  Cuts generation time 5–10× for modify tasks.
      - ``--map-tokens 1024`` → smaller repo-map so slow models spend less time
        parsing context they'll never use.
      - ``--map-refresh manual`` → don't re-scan the repo mid-task.
      - Timeout hierarchy:  Ollama (+120 s) > Aider (+ 60 s) > Bridge timeout,
        so the bridge always wins and gives a clean error.
    """

    def __init__(
        self,
        repo_root: Path,
        command: str,
        logger: logging.Logger,
        model: Optional[str] = None,
        timeout: int = 600,
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
        self._edit_format_failures: dict[str, int] = {}

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

        def _strip(src: bytes) -> bytes:
            text = src.decode("utf-8", errors="replace")
            # Remove single-line // comments and blank lines
            lines = [
                re.sub(r"\s*//.*$", "", line)
                for line in text.splitlines()
            ]
            return "\n".join(l for l in lines if l.strip()).encode()

        return _strip(before_content) == _strip(after_content)

    @staticmethod
    def _is_allowed_empty_file(path: Path) -> bool:
        """Return True for intentionally empty marker/module files."""
        rel = PurePosixPath(path.as_posix())
        return (
            rel.match("**/__init__.py")
            or rel.match("**/.gitkeep")
            or rel.match("**/.keep")
        )

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

            if (
                path.exists()
                and path.stat().st_size == 0
                and not self._is_allowed_empty_file(path)
            ):
                unchanged.append(path.name)
                continue
            
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
        for pattern in INTERACTIVE_PROMPT_PATTERNS:
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

    def _classify_fatal_error(self, stdout: str, stderr: str) -> Optional[tuple[str, str]]:
        """Scan Aider output for known fatal error patterns.

        Returns (category, message) if a fatal error is detected, None otherwise.
        These errors indicate config/connection problems that will never self-heal,
        so retrying is pointless.
        """
        combined = f"{stdout}\n{stderr}"
        for pattern, category, message in FATAL_ERROR_PATTERNS:
            if pattern.lower() in combined.lower():
                self._logger.error(
                    "Aider fatal error detected [%s]: %s (matched: '%s')",
                    category, message, pattern,
                )
                return category, message
        return None

    # ── Estimate how long the LLM generation will take ─────────────────────

    def _estimate_generation_seconds(self,edit_format: str, file_paths: list[Path]) -> int:
        """Rough estimate of how long the LLM will take to generate a response.

        With ``--edit-format diff`` the model outputs only the changed lines
        (typically 5-20% of the file).  With ``whole`` it rewrites everything.
        We use diff format, so estimate ~20% of total file tokens as output.

        Returns a conservative estimate in seconds at ~6 tok/s (slow local model).
        """
        total_chars = 0
        for p in file_paths:
            try:
                if p.exists():
                    total_chars += p.stat().st_size
            except OSError:
                pass
        # ~4 chars per token, diff outputs ~20% of file, ~6 tok/s for slow models
        
        if edit_format == "whole":
            estimated_output_tokens = (total_chars / 4)
        else:
            estimated_output_tokens = (total_chars / 4) * 0.20
        
        #estimated_output_tokens = (total_chars / 4) * 0.20
        return max(60, int(estimated_output_tokens / 6) + 30)  # min 60s, +30s overhead

    # ── Adaptive edit format selection ──────────────────────────────────────

    _WHOLE_FORMAT_LINE_THRESHOLD = 2000

    # Track per-file edit mode success/failure for adaptive fallback

    def _supports_reliable_diff(self) -> bool:
        model = (self._model or "").lower()
        return model.startswith(("openai/", "anthropic/"))

    def _is_local_model(self) -> bool:
        """
        Return True if the current model is a local / self-hosted model.

        We classify as local if:
        - Explicit ollama prefix
        - Known local model families WITHOUT a remote provider prefix
        """

        model = (self._model or "").lower()

        # Explicit local provider
        if model.startswith("ollama/"):
            return True

        # Known remote providers (extend as needed)
        REMOTE_PREFIXES = (
            "openai/",
            "anthropic/",
            "azure/",
            "google/",
        )

        if any(model.startswith(p) for p in REMOTE_PREFIXES):
            return False

        # Known local model families
        LOCAL_FAMILIES = (
            "qwen",
            "llama",
            "gemma",
            "mistral",
            "deepseek",
            "phi",
        )

        return any(name in model for name in LOCAL_FAMILIES)

    def _pick_edit_format(self, file_paths: list[Path], force_whole: bool = False) -> str:
        """
        Strategy:
        1. force_whole → always whole
        2. local models → always whole (diff unreliable)
        3. previous diff failure → whole
        4. small files → whole
        5. large files → diff (only for strong remote models)
        """

        if force_whole:
            self._logger.info("Edit format: whole (forced by retry feedback)")
            return "whole"
        
        if not self._supports_reliable_diff():
            return "whole"

        total_lines = 0
        for p in file_paths:
            try:
                if p.exists():
                    with p.open(encoding="utf-8", errors="replace") as f:
                        total_lines += sum(1 for _ in f)
            except OSError:
                pass

        # If diff failed before → fallback permanently
        for p in file_paths:
            if self._edit_format_failures.get(str(p), 0) >= 1:
                self._logger.info(
                    "Edit format: whole (diff failed previously for %s)", p.name
                )
                return "whole"

        # Small files → whole (safer even for strong models)
        if total_lines <= self._WHOLE_FORMAT_LINE_THRESHOLD:
            self._logger.info(
                "Edit format: whole (%d lines — under threshold)", total_lines
            )
            return "whole"

        # Only large + strong models → diff
        self._logger.info(
            "Edit format: diff (%d lines — above threshold)", total_lines
        )
        return "diff"

    def record_edit_format_failure(self, file_paths: list[Path]) -> None:
        """Record that the current edit format failed for these files."""
        for p in file_paths:
            key = str(p)
            self._edit_format_failures[key] = self._edit_format_failures.get(key, 0) + 1

    def record_edit_format_success(self, file_paths: list[Path]) -> None:
        """Reset failure counter on success."""
        for p in file_paths:
            self._edit_format_failures.pop(str(p), None)

    # ── Core subprocess execution ────────────────────────────────────────

    def _execute_aider(
        self,
        task: Task,
        file_paths: list[Path],
        aider_context: Optional[AiderContext],
        *,
        lightweight: bool = False,
    ) -> ExecutionResult:
        """Run a single Aider subprocess and return the result.

        When ``lightweight=True``, strips repo-map and context files to minimise
        input tokens — used as an automatic fallback when the first attempt
        stalls or times out.
        """
        try:
            arguments, _ = self._build_command(
                task, file_paths, aider_context,
                lightweight=lightweight,
            )
        except (FileNotFoundError, ValueError) as ex:
            return ExecutionResult(
                task_id=task.id,
                succeeded=False,
                exit_code=-1,
                stdout="",
                stderr=str(ex),
                command=[self._command],
            )

        self._logger.debug("Running Aider%s: %s",
                           " (lightweight)" if lightweight else "", arguments)

        # ── Timeout hierarchy ────────────────────────────────────────────
        # Ollama (+120 s) > Aider (+60 s) > Bridge timeout
        # so the bridge's own timeout is always the one that fires first
        # and we get a clean error instead of a litellm.Timeout crash.
        _ollama_timeout = str(self._timeout + 120)

        _subprocess_env = {
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "BROWSER": "",                             # Prevent browser opening
            "NO_COLOR": "1",                           # No ANSI colours
            "OLLAMA_KEEP_ALIVE": "10m",                # Keep model in VRAM
            "OLLAMA_REQUEST_TIMEOUT": _ollama_timeout,  # Ollama HTTP timeout
            "LITELLM_REQUEST_TIMEOUT": _ollama_timeout, # LiteLLM HTTP timeout
            # Expand Ollama context window — models like qwen2.5-coder support
            # 128K natively but Ollama defaults to 2-4K.  Without this, any
            # prompt >32K tokens gets a useless "I'll keep that in mind" response.
            "OLLAMA_NUM_CTX": "65536",                 # 64K context window
            "OLLAMA_NUM_PARALLEL": "1",                # No parallel requests
        }

        _start = time.monotonic()
        _stdout_lines: list[str] = []
        _stderr_lines: list[str] = []

        try:
            proc = subprocess.Popen(
                arguments,
                cwd=self._repo_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=_subprocess_env,
                creationflags=_WIN_NO_WINDOW,
                stdin=subprocess.DEVNULL,
            )

            import threading

            def _read_stderr():
                for line in proc.stderr:
                    stripped = line.rstrip("\n\r")
                    _stderr_lines.append(stripped)
                    self._logger.debug("Task %s [aider stderr]: %s", task.id, stripped)

            stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
            stderr_thread.start()

            # ── Stall detection ──────────────────────────────────────────
            # Track the last time Aider emitted output.  If the LLM goes
            # silent for >120 s while the overall timeout hasn't fired yet,
            # it's likely stuck in a loop or waiting for something.
            _STALL_LIMIT = 180  # seconds of silence before we kill
            _last_output_time = time.monotonic()

            for raw_line in proc.stdout:
                stripped = raw_line.rstrip("\n\r")
                _stdout_lines.append(stripped)
                _last_output_time = time.monotonic()
                self._logger.info("Task %s [aider]: %s", task.id, stripped)

                elapsed = time.monotonic() - _start
                silent = time.monotonic() - _last_output_time

                # Hard timeout
                if elapsed > self._timeout:
                    proc.kill()
                    self._logger.warning(
                        "Task %s: Aider timed out after %ds", task.id, self._timeout)
                    return ExecutionResult(
                        task_id=task.id,
                        succeeded=False,
                        exit_code=-1,
                        stdout="\n".join(_stdout_lines[-50:]),
                        stderr=f"Aider timed out after {self._timeout}s",
                        command=arguments,
                        duration_seconds=round(elapsed, 2),
                    )

                # Stall detection
                if silent > _STALL_LIMIT:
                    proc.kill()
                    self._logger.warning(
                        "Task %s: Aider stalled — no output for %ds", task.id, int(silent))
                    return ExecutionResult(
                        task_id=task.id,
                        succeeded=False,
                        exit_code=-1,
                        stdout="\n".join(_stdout_lines[-50:]),
                        stderr=f"Aider stalled — no output for {int(silent)}s (LLM may be overloaded)",
                        command=arguments,
                        duration_seconds=round(elapsed, 2),
                    )

            proc.wait(timeout=10)
            stderr_thread.join(timeout=5)

            result_stdout = "\n".join(_stdout_lines)
            result_stderr = "\n".join(_stderr_lines)

            class _Result:
                def __init__(self, rc, out, err):
                    self.returncode = rc
                    self.stdout = out
                    self.stderr = err
            result = _Result(proc.returncode, result_stdout, result_stderr)

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

    def run(
        self,
        task: Task,
        file_paths: list[Path],
        aider_context: Optional[AiderContext] = None,
        model_override: Optional[str] = None,
    ) -> ExecutionResult:
        # Per-task model override with 7B enforcement
        _original_model = self._model
        _effective_model = model_override or self._model or ""
        # Warn if 14B+ is used — 7B is faster with retries
        if "14b" in _effective_model.lower() or "32b" in _effective_model.lower():
            self._logger.warning(
                "Task %s: using slow model '%s' — 7B is recommended for interactive use "
                "(fast retries outperform slow correctness)",
                task.id, _effective_model,
            )
        if model_override:
            self._logger.info(
                "Task %s: model override → %s (default: %s)",
                task.id, model_override, self._model,
            )
            self._model = model_override

        try:
            return self._run_inner(task, file_paths, aider_context)
        finally:
            # Always restore the original model
            self._model = _original_model

    def _run_inner(
        self,
        task: Task,
        file_paths: list[Path],
        aider_context: Optional[AiderContext] = None,
    ) -> ExecutionResult:
        # Snapshot file hashes AND raw bytes before Aider runs.
        pre_hashes = self._snapshot_hashes(file_paths)
        pre_contents: dict[str, Optional[bytes]] = {
            str(p): (p.read_bytes() if p.exists() else None) for p in file_paths
        }

        # Log estimated generation time so timeouts are understandable
        edit_format = self._pick_edit_format(file_paths)
        est = self._estimate_generation_seconds(edit_format , file_paths)
 
        self._logger.info(
            "Task %s: estimated LLM generation ~%ds (timeout=%ds, format=%s)",
            task.id, est, self._timeout, edit_format
        )

        # ── Attempt 1: normal run ────────────────────────────────────────
        result_obj = self._execute_aider(task, file_paths, aider_context)

        # ── Auto-retry on timeout with lightweight config ────────────────
        # If the first attempt timed out or stalled, retry once with
        # --map-tokens 0 (no repo-map) to slash input tokens.
        _is_timeout = (
            result_obj.exit_code == -1
            and ("timed out" in (result_obj.stderr or "").lower()
                 or "stalled" in (result_obj.stderr or "").lower())
        )
        if _is_timeout:
            self._logger.warning(
                "Task %s: first attempt timed out — retrying with lightweight config "
                "(no repo-map, no context files)",
                task.id,
            )
            # Re-snapshot in case first attempt partially wrote files
            pre_hashes = self._snapshot_hashes(file_paths)
            pre_contents = {
                str(p): (p.read_bytes() if p.exists() else None) for p in file_paths
            }
            result_obj = self._execute_aider(
                task, file_paths, aider_context, lightweight=True,
            )
            # If lightweight also timed out, return that error
            if result_obj.exit_code == -1:
                return result_obj

        # ── Post-processing (scope check, silent failure, error classification) ─

        # Scope enforcement: revert any files the model touched that were NOT
        # in the task's file list.
        self._revert_out_of_scope_changes(task.id, file_paths)

        # Detect silent failure — Aider exits 0 but never actually writes the
        # target files, or only makes trivial whitespace/comment changes.
        silent_failure_msg = self._check_for_silent_failure(
            task.id, task.type, file_paths, pre_hashes, pre_contents
        )
        if silent_failure_msg and result_obj.exit_code == 0:
            result_obj = ExecutionResult(
                task_id=task.id,
                succeeded=False,
                exit_code=0,
                stdout=result_obj.stdout,
                stderr=silent_failure_msg,
                command=result_obj.command,
                duration_seconds=result_obj.duration_seconds,
            )
            return result_obj

        # Fatal error classification — detect LiteLLM/config/connection errors
        fatal = self._classify_fatal_error(result_obj.stdout, result_obj.stderr)
        if fatal is not None:
            category, message = fatal
            return ExecutionResult(
                task_id=task.id,
                succeeded=False,
                exit_code=result_obj.exit_code if result_obj.exit_code != 0 else 1,
                stdout=result_obj.stdout,
                stderr=f"[{category}] {message}",
                command=result_obj.command,
                duration_seconds=result_obj.duration_seconds,
            )

        # Useless response detection — model acknowledged prompt but didn't code.
        # Happens when context overflows: model says "I'll keep that in mind"
        # instead of producing edit blocks.
        _stdout_lower = (result_obj.stdout or "").lower()
        for useless_pattern in USELESS_RESPONSE_PATTERNS:
            if useless_pattern in _stdout_lower:
                self._logger.warning(
                    "Task %s: model gave a useless response ('%s') — likely context overflow",
                    task.id, useless_pattern,
                )
                return ExecutionResult(
                    task_id=task.id,
                    succeeded=False,
                    exit_code=0,
                    stdout=result_obj.stdout,
                    stderr=(
                        f"[context_overflow] Model responded with '{useless_pattern}' "
                        "instead of code — the prompt likely exceeds the model's context "
                        "window. Try: reduce file size, use a model with larger context, "
                        "or split this task into smaller pieces."
                    ),
                    command=result_obj.command,
                    duration_seconds=result_obj.duration_seconds,
                )

        # Interactive prompt detection
        interactive_prompt_msg = self._detect_interactive_prompt_output(
            result_obj.stdout, result_obj.stderr,
        )
        if interactive_prompt_msg is not None:
            return ExecutionResult(
                task_id=task.id,
                succeeded=False,
                exit_code=result_obj.exit_code if result_obj.exit_code != 0 else -1,
                stdout=result_obj.stdout,
                stderr=interactive_prompt_msg,
                command=result_obj.command,
                duration_seconds=result_obj.duration_seconds,
            )

        return result_obj

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
            allowed_by_basename: dict[str, list[str]] = {}
            for allowed in allowed_rel:
                basename = PurePosixPath(allowed).name
                if basename not in allowed_by_basename:
                    allowed_by_basename[basename] = []
                allowed_by_basename[basename].append(allowed)

            dirty_paths: list[str] = []
            for rel_path in proc.stdout.splitlines():
                rel_norm = rel_path.strip().replace("\\", "/")
                if rel_norm:
                    dirty_paths.append(rel_norm)

            mapped_reverts: list[str] = []
            mapped_notes: list[str] = []
            dirty_set = set(dirty_paths)
            for rel_norm in dirty_paths:
                if rel_norm in allowed_rel:
                    continue
                # Basename-only edits are common with smaller local models.
                # If the basename maps to exactly one allowed scoped target,
                # transplant the edited content to that target path.
                if "/" in rel_norm:
                    continue
                candidates = allowed_by_basename.get(rel_norm, [])
                if len(candidates) != 1:
                    continue
                target_rel = candidates[0]
                if target_rel in dirty_set:
                    continue
                source_abs = self._repo_root / rel_norm
                target_abs = self._repo_root / target_rel
                if not source_abs.exists() or not source_abs.is_file():
                    continue
                try:
                    target_abs.parent.mkdir(parents=True, exist_ok=True)
                    target_abs.write_bytes(source_abs.read_bytes())
                    mapped_reverts.append(rel_norm)
                    mapped_notes.append(f"{rel_norm} -> {target_rel}")
                except OSError as exc:
                    self._logger.debug(
                        "Task %s: basename auto-map failed for %s -> %s: %s",
                        task_id, rel_norm, target_rel, exc,
                    )

            if mapped_notes:
                self._logger.info(
                    "Task %s: auto-mapped basename-only edit(s): %s",
                    task_id,
                    ", ".join(mapped_notes),
                )

            out_of_scope: list[str] = []
            for rel_norm in dirty_paths:
                if rel_norm not in allowed_rel:
                    out_of_scope.append(rel_norm)

            for mapped_rel in mapped_reverts:
                if mapped_rel not in out_of_scope:
                    out_of_scope.append(mapped_rel)

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
        *,
        lightweight: bool = False,
    ) -> tuple[list[str], list[Path]]:
        arguments, searched_locations = resolve_command_arguments(
            self._command, self._repo_root
        )

        if self._model:
            # Ensure model has a provider prefix — LiteLLM requires it
            _model = self._model
            if _model and "/" not in _model and not _model.startswith("gpt") and not _model.startswith("claude"):
                _model = f"ollama/{_model}"
            arguments.extend(["--model", _model])
  
        edit_format = self._pick_edit_format(file_paths)
        arguments.extend([
            "--yes-always",
            "--no-pretty",
            "--no-stream",
            "--no-auto-lint",
            "--no-gitignore",            # suppress "add .aiderignore?" interactive prompt
            "--no-show-model-warnings",  # suppress model-warning + "Open docs url?" prompt
            "--no-browser",              # prevent opening litellm docs or any browser page
            "--no-detect-urls",          # suppress URL detection warnings
            "--no-suggest-shell-commands",  # prevent shell command suggestions
            "--timeout", str(self._timeout + 60),  # LLM request timeout > bridge timeout
            "--edit-format", edit_format,
            "--map-refresh", "manual",   # Don't re-scan repo mid-task
            "--message",
            self._build_message(task,edit_format , aider_context, file_paths),
        ])

        # ── Repo-map size ────────────────────────────────────────────────
        if self._no_map or lightweight:
            # No repo-map: fastest possible, used for lightweight retries
            arguments.extend(["--map-tokens", "0"])
            if lightweight:
                self._logger.info(
                    "Task %s: lightweight mode — repo-map disabled, no context files",
                    task.id,
                )
        else:
            # Reduced repo-map: 1024 instead of Aider's 4096 default.
            # Saves ~60 s of LLM processing on large repos (485+ files).
            arguments.extend(["--map-tokens", "1024"])

        # ── Read-only context files ──────────────────────────────────────
        # Skip context files in lightweight mode to minimise input tokens.
        if not lightweight:
            read_only_paths: list[Path] = []

            for cf in task.context_files:
                cf_path = self._repo_root / cf
                if cf_path.exists():
                    read_only_paths.append(cf_path)
                else:
                    self._logger.debug("context_file not found, skipping --read: %s", cf)

            # Project-wide standards files via --read.
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
        edit_format: str,
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
            # Goal — keep short to save tokens for the actual code
            goal_text = ctx.goal[:200].rstrip()
            sections.append(f"GOAL\n{goal_text}")

            # Task position + instruction
            sections.append(
                f"TASK {ctx.task_number} OF {ctx.total_tasks} ({task.type.upper()})\n"
                f"{task.instruction}"
            )

            # What is already done — last 3 only (saves ~500 tokens)
            if ctx.completed_summaries:
                done_lines = "\n".join(
                    f"  {s}" for s in ctx.completed_summaries[-3:]
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
            "  - CRITICAL: edit ONLY the TARGET FILES listed above — do NOT create,"
            " reference, inspect, or add any other files to the chat",
            "  - CRITICAL: use the exact absolute path shown — do not change the"
            " filename, directory, or extension",
            "  - CRITICAL: stay scoped — do NOT mention, import, or reason about files"
            " outside the target list.  If you need info from another file, infer from"
            " the target file's existing imports and function signatures",
            "  - Do not ask questions or request clarification — implement directly",
            "  - Do not write TODO/stub placeholders — write complete working code",
            "  - Do not remove existing unrelated code",
            #"  - Keep your response SHORT — output only the diff/edit blocks, no explanations",
        ]
        
        if edit_format == "diff":
            rules.append("  - Output ONLY valid diff blocks")
        else:
            rules.append("  - Output the FULL updated file content")
            
        if self._standards_files:
            names = ", ".join(p.name for p in self._standards_files)
            rules.append(f"  - Follow {names} (loaded as read-only context)")
        sections.append("\n".join(rules))

        return "\n\n".join(sections)

    # ── Standards file detection (Feature 2) ─────────────────────────────────

    def _find_standards_files(self) -> list[Path]:
        """Return all known code standards files found in the repo root."""
        found: list[Path] = []
        for name in STANDARDS_FILENAMES:
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
