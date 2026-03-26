from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

from models.task import ReviewResult, SubTask, TaskReport


class ManualSupervisorError(Exception):
    pass


class ManualSupervisorSession:
    """Filesystem-backed supervisor loop for in-session/manual review.

    The bridge writes one review request JSON per task into
    <repo_root>/bridge_progress/manual_supervisor/, then waits for a matching
    decision JSON file to appear. This keeps the bridge purely local and lets
    the active agent session act as the supervisor without any external CLI.
    """

    def __init__(
        self,
        repo_root: Path,
        logger: logging.Logger,
        poll_seconds: int = 2,
    ) -> None:
        self._repo_root = repo_root
        self._logger = logger
        self._poll_seconds = poll_seconds
        self._base_dir = repo_root / "bridge_progress" / "manual_supervisor"
        self._requests_dir = self._base_dir / "requests"
        self._decisions_dir = self._base_dir / "decisions"
        self._archive_dir = self._base_dir / "archive"
        self._requests_dir.mkdir(parents=True, exist_ok=True)
        self._decisions_dir.mkdir(parents=True, exist_ok=True)
        self._archive_dir.mkdir(parents=True, exist_ok=True)

    def submit_review_request(
        self,
        report: TaskReport,
        validation_message: Optional[str] = None,
        unexpected_files: Optional[list[str]] = None,
    ) -> Path:
        request_payload = {
            "task_id": report.task.id,
            "task_type": report.task.type,
            "files": list(report.task.files),
            "instruction": report.task.instruction,
            "execution": {
                "succeeded": report.execution_result.succeeded,
                "exit_code": report.execution_result.exit_code,
                "stdout": report.execution_result.stdout[:4000],
                "stderr": report.execution_result.stderr[:4000],
                "duration_seconds": report.execution_result.duration_seconds,
                "command": report.execution_result.command,
            },
            "validation": {
                "message": validation_message,
            },
            "unexpected_files": unexpected_files or [],
            "diff": report.diff,
            "response_schema": {
                "decision": "pass | rework | subplan",
                "instruction": "required when decision == rework",
                "sub_tasks": [
                    {
                        "instruction": "required when decision == subplan",
                        "files": ["relative/path.py"],
                        "type": "create | modify | delete | validate",
                    }
                ],
            },
        }
        request_path = self._request_path(report.task.id)
        request_path.write_text(json.dumps(request_payload, indent=2), encoding="utf-8")
        self._logger.info("Manual supervisor review requested: %s", request_path)
        return request_path

    def wait_for_decision(self, task_id: int) -> ReviewResult:
        decision_path = self._decision_path(task_id)
        self._logger.info("Waiting for manual supervisor decision: %s", decision_path)

        while not decision_path.exists():
            time.sleep(self._poll_seconds)

        try:
            raw = decision_path.read_text(encoding="utf-8")
            payload = json.loads(raw)
        except (OSError, json.JSONDecodeError) as ex:
            raise ManualSupervisorError(
                f"Could not read manual supervisor decision for task {task_id}: {ex}"
            ) from ex

        result = self._parse_decision(task_id, payload)
        archived_decision_path = self._archive_dir / decision_path.name
        decision_path.replace(archived_decision_path)

        request_path = self._request_path(task_id)
        if request_path.exists():
            archived_request_path = self._archive_dir / request_path.name
            request_path.replace(archived_request_path)

        return result

    def _parse_decision(self, task_id: int, payload: object) -> ReviewResult:
        if not isinstance(payload, dict):
            raise ManualSupervisorError(
                f"Manual supervisor decision for task {task_id} must be a JSON object."
            )

        decision = str(payload.get("decision", "")).strip().lower()
        if decision == "pass":
            return ReviewResult(
                task_id=task_id,
                verdict="PASS",
                new_instruction=None,
                message="Manual supervisor approved.",
                sub_tasks=[],
            )

        if decision == "rework":
            instruction = str(payload.get("instruction", "")).strip()
            if not instruction:
                raise ManualSupervisorError(
                    f"Manual supervisor decision for task {task_id} is missing 'instruction'."
                )
            return ReviewResult(
                task_id=task_id,
                verdict="REWORK",
                new_instruction=instruction,
                message="Manual supervisor requested rework.",
                sub_tasks=[],
            )

        if decision == "subplan":
            raw_sub_tasks = payload.get("sub_tasks", [])
            if not isinstance(raw_sub_tasks, list) or not raw_sub_tasks:
                raise ManualSupervisorError(
                    f"Manual supervisor subplan for task {task_id} must include 'sub_tasks'."
                )
            sub_tasks: list[SubTask] = []
            for index, item in enumerate(raw_sub_tasks, start=1):
                if not isinstance(item, dict):
                    continue
                instruction = str(item.get("instruction", "")).strip()
                files = item.get("files", [])
                task_type = str(item.get("type", "modify")).strip().lower()
                if not instruction or not isinstance(files, list) or not files:
                    continue
                safe_type = task_type if task_type in {"create", "modify", "delete", "validate"} else "modify"
                sub_tasks.append(
                    SubTask(
                        parent_id=task_id,
                        step=index,
                        instruction=instruction,
                        files=[str(file_path).strip() for file_path in files if str(file_path).strip()],
                        type=safe_type,
                    )
                )

            if not sub_tasks:
                raise ManualSupervisorError(
                    f"Manual supervisor subplan for task {task_id} contained no valid sub-tasks."
                )

            return ReviewResult(
                task_id=task_id,
                verdict="SUBPLAN",
                new_instruction=None,
                message="Manual supervisor returned a corrective subplan.",
                sub_tasks=sub_tasks,
            )

        raise ManualSupervisorError(
            f"Manual supervisor decision for task {task_id} has unsupported decision {decision!r}."
        )

    def _request_path(self, task_id: int) -> Path:
        return self._requests_dir / f"task_{task_id:04d}_request.json"

    def _decision_path(self, task_id: int) -> Path:
        return self._decisions_dir / f"task_{task_id:04d}_decision.json"
