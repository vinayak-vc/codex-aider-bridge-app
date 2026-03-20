from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class Task:
    id: int
    files: list[str]
    instruction: str
    type: str


@dataclass(frozen=True)
class SelectedFiles:
    existing: list[Path]
    missing: list[Path]
    all_paths: list[Path]


@dataclass(frozen=True)
class ExecutionResult:
    task_id: int
    succeeded: bool
    exit_code: int
    stdout: str
    stderr: str
    command: list[str]


@dataclass(frozen=True)
class ValidationResult:
    task_id: int
    succeeded: bool
    message: str
    stdout: str
    stderr: str


@dataclass(frozen=True)
class BridgeConfig:
    goal: str
    repo_root: Path
    dry_run: bool
    max_plan_attempts: int
    max_task_retries: int
    validation_command: Optional[str]
    codex_command: str
    aider_command: str
