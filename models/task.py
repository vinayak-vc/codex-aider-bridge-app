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
    attempt_number: int = 1
    duration_seconds: float = 0.0


@dataclass(frozen=True)
class ValidationResult:
    task_id: int
    succeeded: bool
    message: str
    stdout: str
    stderr: str


@dataclass(frozen=True)
class TaskReport:
    """Snapshot of a completed task sent to the supervisor for review.

    Contains the task definition, Aider's execution result, and the git diff
    so the supervisor can make an informed PASS or REWORK decision.
    """

    task: Task
    execution_result: ExecutionResult
    diff: str


@dataclass(frozen=True)
class ReviewResult:
    """The supervisor's verdict on a completed task.

    verdict is either "PASS" (approved) or "REWORK" (rejected with new instruction).
    new_instruction is only populated when verdict == "REWORK".
    """

    task_id: int
    verdict: str
    new_instruction: Optional[str]
    message: str


@dataclass(frozen=True)
class BridgeConfig:
    goal: str
    repo_root: Path
    dry_run: bool
    max_plan_attempts: int
    max_task_retries: int
    validation_command: Optional[str]
    supervisor_command: str
    aider_command: str
    aider_model: Optional[str]
    idea_file: Optional[Path]
    idea_text: Optional[str]
    plan_output_file: Optional[Path]
    task_timeout_seconds: int = 300
