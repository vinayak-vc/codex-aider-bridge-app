from __future__ import annotations

import logging
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from models.task import Task, ValidationResult


# Maximum seconds the CI gate command is allowed to run before being killed.
_CI_TIMEOUT_SECONDS: int = 120

# Maximum seconds a per-file syntax check subprocess may run.
_SYNTAX_TIMEOUT_SECONDS: int = 30

# LLM artifact tokens injected by some models (e.g. deepseek-coder).
# Presence in a source file means the model output was corrupted.
_LLM_ARTIFACT_RE = re.compile(
    r"<\|[^|>]{1,40}\|>"       # ASCII variant:  <|begin_of_sentence|>
    r"|<｜[^｜>]{1,40}｜>"      # Unicode variant: <｜begin▁of▁sentence｜>
)


class _ProjectType:
    UNITY = "unity"
    CSHARP = "csharp"
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    JAVASCRIPT = "javascript"
    UNKNOWN = "unknown"


def _detect_project_type(repo_root: Path) -> str:
    """Identify the primary language of the repository from well-known markers."""
    # Unity: always has an Assets/ directory alongside a ProjectSettings/ dir.
    if (repo_root / "Assets").is_dir() and (repo_root / "ProjectSettings").is_dir():
        return _ProjectType.UNITY
    # Plain C#: .sln or .csproj at repo root.
    if any(repo_root.glob("*.csproj")) or any(repo_root.glob("*.sln")):
        return _ProjectType.CSHARP
    # TypeScript: tsconfig.json present.
    if (repo_root / "tsconfig.json").exists():
        return _ProjectType.TYPESCRIPT
    # JavaScript: package.json present.
    if (repo_root / "package.json").exists():
        return _ProjectType.JAVASCRIPT
    # Python: common root markers.
    for marker in ("requirements.txt", "pyproject.toml", "setup.py", "setup.cfg"):
        if (repo_root / marker).exists():
            return _ProjectType.PYTHON
    return _ProjectType.UNKNOWN


class MechanicalValidator:
    """Runs fast, token-free mechanical checks after each Aider execution.

    Checks (in order, stopping at first failure):
    1. Required files exist on disk (create/modify tasks only).
    2. Language-appropriate syntax validation based on detected project type:
       - Unity / C#: LLM-artifact scan + brace balance + dotnet build (if not Unity)
       - Python:     py_compile module
       - TypeScript: tsc --noEmit (if available) else node --check
       - JavaScript: node --check
    3. Optional CI gate command (e.g. pytest, dotnet test).

    Quality review is handled by the SupervisorAgent. This validator never
    calls the supervisor and never spends supervisor tokens.
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
        self._project_type = _detect_project_type(repo_root)
        self._logger.info("Validator: detected project type '%s'", self._project_type)

    def validate(self, task: Task, file_paths: list[Path]) -> ValidationResult:
        existence = self._check_file_existence(task, file_paths)
        if not existence.succeeded:
            return existence

        syntax = self._check_syntax(task.id, file_paths)
        if not syntax.succeeded:
            return syntax

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

    # ── File existence ────────────────────────────────────────────────────────

    def _check_file_existence(self, task: Task, file_paths: list[Path]) -> ValidationResult:
        if task.type not in {"create", "modify"} or not file_paths:
            return ValidationResult(
                task_id=task.id,
                succeeded=True,
                message="File existence check skipped.",
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

    # ── Syntax dispatch ───────────────────────────────────────────────────────

    def _check_syntax(self, task_id: int, file_paths: list[Path]) -> ValidationResult:
        """Dispatch to the correct syntax checker based on detected project type."""
        pt = self._project_type

        if pt in (_ProjectType.UNITY, _ProjectType.CSHARP):
            return self._check_csharp_syntax(task_id, file_paths)
        if pt == _ProjectType.PYTHON:
            return self._check_python_syntax(task_id, file_paths)
        if pt == _ProjectType.TYPESCRIPT:
            return self._check_typescript_syntax(task_id, file_paths)
        if pt == _ProjectType.JAVASCRIPT:
            return self._check_javascript_syntax(task_id, file_paths)

        # Unknown project type — skip language-specific check.
        return ValidationResult(
            task_id=task_id,
            succeeded=True,
            message=f"Syntax check skipped (project type: {pt}).",
            stdout="",
            stderr="",
        )

    # ── C# / Unity ────────────────────────────────────────────────────────────

    def _check_csharp_syntax(self, task_id: int, file_paths: list[Path]) -> ValidationResult:
        cs_files = [p for p in file_paths if p.suffix.lower() == ".cs" and p.exists()]
        if not cs_files:
            return ValidationResult(
                task_id=task_id,
                succeeded=True,
                message="No C# files to check.",
                stdout="",
                stderr="",
            )

        # Step 1: LLM artifact scan (always, no external tool needed).
        artifact_result = self._scan_llm_artifacts(task_id, cs_files)
        if not artifact_result.succeeded:
            return artifact_result

        # Step 2: Brace balance check (catches unclosed class/method bodies).
        brace_result = self._check_brace_balance(task_id, cs_files)
        if not brace_result.succeeded:
            return brace_result

        # Step 3: dotnet build — only for plain C# projects (not Unity).
        # Unity's .csproj files reference Unity DLLs unavailable outside the editor.
        if self._project_type == _ProjectType.CSHARP and shutil.which("dotnet"):
            return self._run_dotnet_build(task_id)

        return ValidationResult(
            task_id=task_id,
            succeeded=True,
            message="C# syntax checks passed.",
            stdout="",
            stderr="",
        )

    def _scan_llm_artifacts(self, task_id: int, file_paths: list[Path]) -> ValidationResult:
        """Detect special tokens injected by LLMs into source files."""
        for path in file_paths:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            match = _LLM_ARTIFACT_RE.search(content)
            if match:
                self._logger.warning(
                    "Task %s: LLM artifact token found in %s: %r",
                    task_id, path.name, match.group(),
                )
                return ValidationResult(
                    task_id=task_id,
                    succeeded=False,
                    message=(
                        f"LLM artifact token {match.group()!r} found in {path.name}. "
                        "The model injected a special token into the source file. "
                        "Rewrite the file without any special tokens."
                    ),
                    stdout="",
                    stderr="",
                )
        return ValidationResult(
            task_id=task_id,
            succeeded=True,
            message="No LLM artifact tokens found.",
            stdout="",
            stderr="",
        )

    def _check_brace_balance(self, task_id: int, file_paths: list[Path]) -> ValidationResult:
        """Verify that { and } counts match in each C# file."""
        for path in file_paths:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # Strip single-line comments and string literals for a rough balance check.
            stripped = re.sub(r"//[^\n]*", "", content)
            stripped = re.sub(r'"(?:[^"\\]|\\.)*"', '""', stripped)
            open_count = stripped.count("{")
            close_count = stripped.count("}")
            if open_count != close_count:
                self._logger.warning(
                    "Task %s: brace mismatch in %s — %d open vs %d close",
                    task_id, path.name, open_count, close_count,
                )
                return ValidationResult(
                    task_id=task_id,
                    succeeded=False,
                    message=(
                        f"Brace mismatch in {path.name}: "
                        f"{open_count} opening '{{' vs {close_count} closing '}}'. "
                        "Fix the unclosed or extra brace."
                    ),
                    stdout="",
                    stderr="",
                )
        return ValidationResult(
            task_id=task_id,
            succeeded=True,
            message="Brace balance OK.",
            stdout="",
            stderr="",
        )

    def _run_dotnet_build(self, task_id: int) -> ValidationResult:
        csproj_files = list(self._repo_root.glob("*.csproj"))
        sln_files = list(self._repo_root.glob("*.sln"))
        target = str(sln_files[0]) if sln_files else (str(csproj_files[0]) if csproj_files else ".")

        self._logger.debug("Task %s: running dotnet build on %s", task_id, target)
        try:
            result = subprocess.run(
                ["dotnet", "build", target, "--no-restore", "--verbosity", "quiet"],
                cwd=self._repo_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=_SYNTAX_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return ValidationResult(
                task_id=task_id,
                succeeded=False,
                message="dotnet build timed out.",
                stdout="",
                stderr="",
            )

        return ValidationResult(
            task_id=task_id,
            succeeded=result.returncode == 0,
            message="dotnet build." if result.returncode == 0 else "dotnet build reported errors.",
            stdout=result.stdout[:2000],
            stderr=result.stderr[:2000],
        )

    # ── Python ────────────────────────────────────────────────────────────────

    def _check_python_syntax(self, task_id: int, file_paths: list[Path]) -> ValidationResult:
        py_files = [p for p in file_paths if p.suffix.lower() == ".py" and p.exists()]
        if not py_files:
            return ValidationResult(
                task_id=task_id,
                succeeded=True,
                message="No Python files to check.",
                stdout="",
                stderr="",
            )

        result = subprocess.run(
            [sys.executable, "-m", "compileall", "-q"] + [str(p) for p in py_files],
            cwd=self._repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=_SYNTAX_TIMEOUT_SECONDS,
        )

        return ValidationResult(
            task_id=task_id,
            succeeded=result.returncode == 0,
            message=(
                "Python syntax OK."
                if result.returncode == 0
                else "Python syntax error detected."
            ),
            stdout=result.stdout[:2000],
            stderr=result.stderr[:2000],
        )

    # ── JavaScript ────────────────────────────────────────────────────────────

    def _check_javascript_syntax(self, task_id: int, file_paths: list[Path]) -> ValidationResult:
        js_files = [
            p for p in file_paths
            if p.suffix.lower() in {".js", ".mjs", ".cjs"} and p.exists()
        ]
        if not js_files:
            return ValidationResult(
                task_id=task_id,
                succeeded=True,
                message="No JavaScript files to check.",
                stdout="",
                stderr="",
            )

        if not shutil.which("node"):
            self._logger.debug("Task %s: node not found — skipping JS syntax check", task_id)
            return ValidationResult(
                task_id=task_id,
                succeeded=True,
                message="JS syntax check skipped (node not on PATH).",
                stdout="",
                stderr="",
            )

        errors: list[str] = []
        for js_file in js_files:
            result = subprocess.run(
                ["node", "--check", str(js_file)],
                cwd=self._repo_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=_SYNTAX_TIMEOUT_SECONDS,
            )
            if result.returncode != 0:
                errors.append(f"{js_file.name}: {result.stderr.strip()[:500]}")

        if errors:
            return ValidationResult(
                task_id=task_id,
                succeeded=False,
                message="JavaScript syntax error(s) detected.",
                stdout="",
                stderr="\n".join(errors),
            )

        return ValidationResult(
            task_id=task_id,
            succeeded=True,
            message="JavaScript syntax OK.",
            stdout="",
            stderr="",
        )

    # ── TypeScript ────────────────────────────────────────────────────────────

    def _check_typescript_syntax(self, task_id: int, file_paths: list[Path]) -> ValidationResult:
        ts_files = [
            p for p in file_paths
            if p.suffix.lower() in {".ts", ".tsx"} and p.exists()
        ]
        if not ts_files:
            return ValidationResult(
                task_id=task_id,
                succeeded=True,
                message="No TypeScript files to check.",
                stdout="",
                stderr="",
            )

        # Prefer tsc for full type-aware check; fall back to node --check for syntax only.
        if shutil.which("tsc"):
            result = subprocess.run(
                ["tsc", "--noEmit", "--skipLibCheck"],
                cwd=self._repo_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=_SYNTAX_TIMEOUT_SECONDS,
            )
            return ValidationResult(
                task_id=task_id,
                succeeded=result.returncode == 0,
                message=(
                    "TypeScript check OK."
                    if result.returncode == 0
                    else "TypeScript errors detected."
                ),
                stdout=result.stdout[:2000],
                stderr=result.stderr[:2000],
            )

        # tsc not available — use node --check as a syntax-only fallback.
        self._logger.debug("Task %s: tsc not found — falling back to node --check", task_id)
        if shutil.which("node"):
            errors: list[str] = []
            for ts_file in ts_files:
                result = subprocess.run(
                    ["node", "--check", str(ts_file)],
                    cwd=self._repo_root,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    timeout=_SYNTAX_TIMEOUT_SECONDS,
                )
                if result.returncode != 0:
                    errors.append(f"{ts_file.name}: {result.stderr.strip()[:500]}")
            if errors:
                return ValidationResult(
                    task_id=task_id,
                    succeeded=False,
                    message="TypeScript syntax error(s) detected (node --check).",
                    stdout="",
                    stderr="\n".join(errors),
                )

        return ValidationResult(
            task_id=task_id,
            succeeded=True,
            message="TypeScript syntax OK.",
            stdout="",
            stderr="",
        )

    # ── CI gate ───────────────────────────────────────────────────────────────

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
                errors="replace",
                shell=False,
                timeout=_CI_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ValidationResult(
                task_id=task_id,
                succeeded=False,
                message=f"CI gate command timed out after {_CI_TIMEOUT_SECONDS}s",
                stdout="",
                stderr="",
            )

        return ValidationResult(
            task_id=task_id,
            succeeded=result.returncode == 0,
            message="CI gate passed." if result.returncode == 0 else "CI gate failed.",
            stdout=result.stdout[:2000],
            stderr=result.stderr[:2000],
        )


# Backwards-compatible alias
ProjectValidator = MechanicalValidator
