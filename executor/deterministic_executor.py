"""Deterministic executor — applies structured operations without LLM involvement.

When a TaskIR has exact search/replace operations, this executor applies them
directly via string replacement. No LLM tokens consumed, instant execution,
100% predictable results.

Falls through to Aider (LLM) execution when deterministic operations are not
available or verification fails.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from executor.task_ir import TaskIR, OperationSpec
from models.task import ExecutionResult

logger = logging.getLogger(__name__)


class DeterministicExecutionError(Exception):
    """Raised when deterministic execution fails verification."""
    pass


def execute_deterministic(
    ir: TaskIR,
    repo_root: Path,
) -> Optional[ExecutionResult]:
    """Try to execute a task deterministically (no LLM).

    Returns ExecutionResult on success, None if deterministic execution
    is not possible (caller should fall through to Aider).
    """
    if not ir.can_execute_deterministically:
        return None

    target_path = repo_root / ir.target.file
    if not target_path.exists():
        return None

    try:
        original = target_path.read_text(encoding="utf-8", errors="replace")
    except OSError as ex:
        logger.warning("Task %s: cannot read file for deterministic exec: %s", ir.id, ex)
        return None

    modified = original
    changes_applied = 0

    for i, op in enumerate(ir.operations):
        if op.action == "replace" and op.search and op.replace is not None:
            if op.search not in modified:
                logger.warning(
                    "Task %s op %d: search string not found — aborting deterministic exec",
                    ir.id, i,
                )
                return None  # Fall through to LLM

            count = modified.count(op.search)
            modified = modified.replace(op.search, op.replace)
            changes_applied += count
            logger.info(
                "Task %s op %d: replaced %d occurrence(s) of '%s'",
                ir.id, i, count, op.search[:50],
            )

    # Verify something actually changed
    if modified == original:
        logger.warning("Task %s: deterministic exec produced no changes", ir.id)
        return None

    # Write the result
    try:
        target_path.write_text(modified, encoding="utf-8")
    except OSError as ex:
        logger.error("Task %s: failed to write result: %s", ir.id, ex)
        return ExecutionResult(
            task_id=ir.id,
            succeeded=False,
            exit_code=1,
            stdout="",
            stderr=f"Write failed: {ex}",
            command=["deterministic-executor"],
        )

    logger.info(
        "Task %s: deterministic execution succeeded — %d change(s), 0 LLM tokens",
        ir.id, changes_applied,
    )

    return ExecutionResult(
        task_id=ir.id,
        succeeded=True,
        exit_code=0,
        stdout=f"Deterministic: {changes_applied} replacement(s) in {ir.target.file}",
        stderr="",
        command=["deterministic-executor"],
        duration_seconds=0.0,
    )


def extract_function_context(
    file_path: Path,
    function_name: str,
    context_lines: int = 5,
) -> Optional[str]:
    """Extract a specific function's code from a file for context minimization.

    Returns the function body + surrounding context lines, or None if not found.
    """
    try:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    # Find function definition
    func_start = None
    for i, line in enumerate(lines):
        if function_name in line and ("function " in line or "def " in line or f"{function_name}(" in line):
            func_start = i
            break

    if func_start is None:
        return None

    # Find function end (simple brace/indent matching)
    func_end = func_start + 1
    indent_level = len(lines[func_start]) - len(lines[func_start].lstrip())

    for i in range(func_start + 1, len(lines)):
        line = lines[i]
        if line.strip() and not line.startswith(" " * (indent_level + 1)) and not line.strip().startswith(("}", ")")):
            # Check if this is a closing brace at the same indent level
            if line.strip() == "}" or line.strip() == "};" or line.strip() == ");":
                func_end = i + 1
            break
        func_end = i + 1

    # Add context lines
    start = max(0, func_start - context_lines)
    end = min(len(lines), func_end + context_lines)

    return "\n".join(lines[start:end])
