"""Task IR (Intermediate Representation) — structured intent for deterministic execution.

Transforms loose natural-language task instructions into a validated, normalized
schema that the execution layer can process deterministically. This reduces
reliance on the LLM for precise edits and catches invalid tasks before they
reach Aider.

Architecture shift:
  Old: natural language instruction → LLM rewrites file → hope it works
  New: natural language instruction → Task IR → deterministic executor → LLM fallback
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class TargetSpec:
    """Where in the code the change should happen."""
    file: str
    function: Optional[str] = None
    lines: Optional[tuple[int, int]] = None  # (start, end) line range


@dataclass
class OperationSpec:
    """What change to make — for deterministic execution."""
    action: str  # replace | insert | delete | append
    search: Optional[str] = None  # exact string to find
    replace: Optional[str] = None  # replacement string
    position: Optional[str] = None  # before | after | at_line (for insert)


@dataclass
class TaskIR:
    """Normalized task representation — validated before execution."""
    id: int
    type: str  # modify | create | delete | validate | read | investigate
    target: TargetSpec
    instruction: str  # original instruction (kept for LLM fallback)
    operations: list[OperationSpec] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    model: Optional[str] = None
    context_files: list[str] = field(default_factory=list)

    # Execution metadata
    is_deterministic: bool = False  # True if operations can execute without LLM
    estimated_complexity: str = "medium"  # tiny | small | medium | large

    @property
    def can_execute_deterministically(self) -> bool:
        """True if all operations have exact search/replace pairs."""
        if not self.operations:
            return False
        return all(
            op.action == "replace" and op.search and op.replace is not None
            for op in self.operations
        )


class TaskIRValidationError(Exception):
    """Raised when a task fails IR validation."""
    pass


# ── Validation ───────────────────────────────────────────────────────────────

def validate_task_ir(ir: TaskIR, repo_root: Path) -> list[str]:
    """Validate a TaskIR before execution. Returns list of warnings (empty = valid).

    Raises TaskIRValidationError for fatal issues.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Required fields
    if not ir.target.file:
        errors.append(f"Task {ir.id}: target.file is empty")
    if not ir.instruction:
        errors.append(f"Task {ir.id}: instruction is empty")
    if ir.type not in {"modify", "create", "delete", "validate", "read", "investigate"}:
        errors.append(f"Task {ir.id}: invalid type '{ir.type}'")

    # File existence checks
    target_path = repo_root / ir.target.file
    if ir.type in ("modify", "delete", "validate") and not target_path.exists():
        errors.append(f"Task {ir.id}: target file does not exist: {ir.target.file}")
    if ir.type == "create" and target_path.exists():
        warnings.append(f"Task {ir.id}: file already exists (will overwrite): {ir.target.file}")

    # Operation validation
    for i, op in enumerate(ir.operations):
        if op.action not in ("replace", "insert", "delete", "append"):
            errors.append(f"Task {ir.id} op {i}: invalid action '{op.action}'")
        if op.action == "replace" and not op.search:
            errors.append(f"Task {ir.id} op {i}: replace action requires 'search' field")
        if op.action == "replace" and op.replace is None:
            errors.append(f"Task {ir.id} op {i}: replace action requires 'replace' field")

    # Verify search strings exist in the file
    if ir.type == "modify" and target_path.exists():
        try:
            content = target_path.read_text(encoding="utf-8", errors="replace")
            for i, op in enumerate(ir.operations):
                if op.action == "replace" and op.search:
                    if op.search not in content:
                        warnings.append(
                            f"Task {ir.id} op {i}: search string not found in file "
                            f"(first 40 chars: {op.search[:40]!r})"
                        )
        except OSError:
            warnings.append(f"Task {ir.id}: could not read file for validation")

    if errors:
        raise TaskIRValidationError("; ".join(errors))

    return warnings


# ── Extraction from instruction ──────────────────────────────────────────────

def extract_operations_from_instruction(instruction: str, file_path: Path) -> list[OperationSpec]:
    """Try to extract deterministic operations from a natural-language instruction.

    Looks for patterns like:
      - "replace X with Y"
      - "change 'foo' to 'bar'"
      - "add X after Y"

    Returns empty list if no deterministic operations can be extracted.
    """
    operations: list[OperationSpec] = []

    # Pattern: replace/change "X" with/to "Y"
    replace_patterns = [
        r'(?:replace|change)\s+["\']([^"\']+)["\']\s+(?:with|to)\s+["\']([^"\']+)["\']',
        r'(?:replace|change)\s+`([^`]+)`\s+(?:with|to)\s+`([^`]+)`',
    ]
    for pattern in replace_patterns:
        for match in re.finditer(pattern, instruction, re.IGNORECASE):
            search_str = match.group(1)
            replace_str = match.group(2)
            # Verify search string exists in the file
            if file_path.exists():
                try:
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                    if search_str in content:
                        operations.append(OperationSpec(
                            action="replace",
                            search=search_str,
                            replace=replace_str,
                        ))
                except OSError:
                    pass

    return operations


# ── Complexity classification ────────────────────────────────────────────────

def classify_complexity(ir: TaskIR, repo_root: Path) -> str:
    """Classify task complexity for routing decisions.

    Returns: tiny | small | medium | large
    """
    # Count target file lines
    target_path = repo_root / ir.target.file
    line_count = 0
    if target_path.exists():
        try:
            line_count = sum(1 for _ in target_path.open(encoding="utf-8", errors="replace"))
        except OSError:
            pass

    # Deterministic operations = tiny
    if ir.can_execute_deterministically:
        return "tiny"

    # Single file, short instruction, small file
    word_count = len(ir.instruction.split())
    if word_count < 30 and line_count < 100:
        return "small"
    if word_count < 80 and line_count < 500:
        return "medium"

    return "large"


# ── Convert from legacy Task ─────────────────────────────────────────────────

def task_to_ir(task, repo_root: Path) -> TaskIR:
    """Convert a legacy Task dataclass to TaskIR.

    Attempts to extract deterministic operations from the instruction.
    Falls back to LLM execution if extraction fails.
    """
    file_path = repo_root / task.files[0] if task.files else Path("")

    # Build target spec
    target = TargetSpec(
        file=task.files[0] if task.files else "",
        function=_extract_function_name(task.instruction),
        lines=_extract_line_range(task.instruction),
    )

    # Try to extract deterministic operations
    operations = extract_operations_from_instruction(task.instruction, file_path)

    ir = TaskIR(
        id=task.id,
        type=task.type,
        target=target,
        instruction=task.instruction,
        operations=operations,
        constraints=_extract_constraints(task.instruction),
        model=task.model,
        context_files=task.context_files,
        is_deterministic=bool(operations),
    )

    # Classify complexity
    ir.estimated_complexity = classify_complexity(ir, repo_root)

    return ir


# ── Private helpers ──────────────────────────────────────────────────────────

def _extract_function_name(instruction: str) -> Optional[str]:
    """Extract target function name from instruction text."""
    patterns = [
        r'(?:in|inside|within)\s+(?:function\s+)?(\w+)\s*\(',
        r'(?:function|method|def)\s+(\w+)',
        r'(\w+)\(\)\s*(?:at|on|around)',
    ]
    for pattern in patterns:
        m = re.search(pattern, instruction, re.IGNORECASE)
        if m:
            name = m.group(1)
            # Filter out common false positives
            if name.lower() not in {"the", "a", "an", "this", "that", "each", "every", "all"}:
                return name
    return None


def _extract_line_range(instruction: str) -> Optional[tuple[int, int]]:
    """Extract line range from instruction text."""
    # Pattern: "line 42" or "around line 42"
    m = re.search(r'(?:line|~line)\s+(\d+)', instruction, re.IGNORECASE)
    if m:
        line = int(m.group(1))
        return (max(1, line - 5), line + 5)
    # Pattern: "lines 42-50"
    m = re.search(r'lines?\s+(\d+)\s*[-–]\s*(\d+)', instruction, re.IGNORECASE)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return None


def _extract_constraints(instruction: str) -> list[str]:
    """Extract explicit constraints from instruction text."""
    constraints: list[str] = []
    lower = instruction.lower()
    if "do not change" in lower or "don't change" in lower:
        constraints.append("preserve_other_code")
    if "do not modify" in lower or "don't modify" in lower:
        constraints.append("preserve_other_code")
    if "only change" in lower or "only modify" in lower:
        constraints.append("scope_to_target_only")
    if "do not remove" in lower or "don't remove" in lower:
        constraints.append("preserve_existing_code")
    if "keep" in lower and "format" in lower:
        constraints.append("preserve_formatting")
    return constraints
