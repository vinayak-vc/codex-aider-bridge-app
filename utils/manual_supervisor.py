from __future__ import annotations

import hashlib
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
        session_id: Optional[str] = None,
    ) -> None:
        self._repo_root = repo_root
        self._logger = logger
        self._poll_seconds = poll_seconds
        self._session_id = str(session_id or "").strip() or None
        self._base_dir = repo_root / "bridge_progress" / "manual_supervisor"
        self._requests_dir = self._base_dir / "requests"
        self._decisions_dir = self._base_dir / "decisions"
        self._completed_dir = self._base_dir / "completed"
        self._archive_dir = self._base_dir / "archive"
        self._requests_dir.mkdir(parents=True, exist_ok=True)
        self._decisions_dir.mkdir(parents=True, exist_ok=True)
        self._completed_dir.mkdir(parents=True, exist_ok=True)
        self._archive_dir.mkdir(parents=True, exist_ok=True)

    def submit_review_request(
        self,
        report: TaskReport,
        validation_message: Optional[str] = None,
        unexpected_files: Optional[list[str]] = None,
    ) -> Path:
        request_payload = {
            "task_id": report.task.id,
            "relay_session_id": self._session_id,
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

    def consume_existing_decision(self, task: TaskReport | object) -> Optional[tuple[ReviewResult, dict]]:
        if not hasattr(task, "id"):
            return None

        task_id = int(getattr(task, "id"))
        request_path = self._request_path(task_id)
        decision_path = self._decision_path(task_id)

        if not request_path.exists() or not decision_path.exists():
            return None

        request_payload = self._read_json_file(request_path, f"manual review request for task {task_id}")
        decision_payload = self._read_json_file(decision_path, f"manual decision for task {task_id}")
        if request_payload is None or decision_payload is None:
            return None

        # Lenient matching: Primarily trust the Task ID if it's a manual supervisor session
        if not self._request_matches_task(request_payload, task):
            self._logger.debug("Existing decision for task %s exists but payload changed. Archiving.", task_id)
            self._archive_path(request_path, suffix="stale")
            self._archive_path(decision_path, suffix="stale")
            return None

        review = self._parse_decision(task_id, decision_payload)
        self._archive_path(decision_path)
        self._archive_path(request_path)
        self._logger.info("Recovered existing manual decision for task %s without waiting.", task_id)
        return review, request_payload

    def record_completed_review(
        self,
        task_id: int,
        instruction: str,
        files: list[str],
        file_paths: list[Path],
        diff: str,
    ) -> None:
        payload = {
            "task_id": task_id,
            "relay_session_id": self._session_id,
            "instruction": instruction,
            "files": list(files),
            "diff": diff,
            "file_fingerprints": [
                self._build_file_fingerprint(path)
                for path in file_paths
            ],
        }
        completed_path = self._completed_path(task_id)
        completed_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self._logger.debug("Recorded completed manual review receipt: %s", completed_path)

    def _normalize(self, text: str) -> str:
        """Strip all non-alphanumeric chars and lowercase for lenient matching."""
        return "".join(c for c in str(text).lower() if c.isalnum())

    def try_resume_completed_task(
        self,
        task_id: int,
        instruction: str,
        files: list[str],
        file_paths: list[Path],
    ) -> Optional[str]:
        completed_path = self._completed_path(task_id)
        if not completed_path.exists():
            return None

        payload = self._read_json_file(completed_path, f"completed review receipt for task {task_id}")
        if payload is None:
            return None

        # Lenient instruction matching: ignore casing, punctuation, and whitespace
        saved_norm = self._normalize(payload.get("instruction", ""))
        curr_norm = self._normalize(instruction)
        if saved_norm != curr_norm:
            self._logger.debug("Stale receipt for task %s: instructions differ significantly", task_id)
            self._archive_path(completed_path, suffix="stale_instr")
            return None

        # If instructions match exactly (normalized), we can be more lenient with fingerprints
        # during a manual-supervisor session resume to prevent "starting over" due to minor metadata changes.
        self._archive_stale_live_review_files(task_id)
        diff = str(payload.get("diff", ""))
        self._logger.info(
            "Recovered completed manual task %s from persisted receipt; skipping duplicate Aider run.",
            task_id,
        )
        return diff

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
        self._archive_path(decision_path, suffix="processed")
        
        request_path = self._request_path(task_id)
        if request_path.exists():
            self._archive_path(request_path, suffix="processed")

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
        suffix = f"_{self._session_id}" if self._session_id else ""
        return self._requests_dir / f"task_{task_id:04d}{suffix}_request.json"

    def _decision_path(self, task_id: int) -> Path:
        suffix = f"_{self._session_id}" if self._session_id else ""
        return self._decisions_dir / f"task_{task_id:04d}{suffix}_decision.json"

    def _completed_path(self, task_id: int) -> Path:
        suffix = f"_{self._session_id}" if self._session_id else ""
        return self._completed_dir / f"task_{task_id:04d}{suffix}_completed.json"

    def _archive_stale_live_review_files(self, task_id: int) -> None:
        request_path = self._request_path(task_id)
        decision_path = self._decision_path(task_id)
        if request_path.exists():
            self._archive_path(request_path, suffix="stale")
        if decision_path.exists():
            self._archive_path(decision_path, suffix="stale")

    def _archive_path(self, path: Path, suffix: Optional[str] = None) -> None:
        destination_name = path.name if not suffix else f"{path.stem}_{suffix}{path.suffix}"
        destination = self._archive_dir / destination_name
        if destination.exists():
            destination.unlink()
        path.replace(destination)

    def _read_json_file(self, path: Path, description: str) -> Optional[dict]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as ex:
            self._logger.warning("Could not read %s from %s: %s", description, path, ex)
            return None

        if not isinstance(payload, dict):
            self._logger.warning("%s in %s is not a JSON object.", description, path)
            return None

        return payload

    def _request_matches_task(self, payload: dict, task: object) -> bool:
        # Lenient matching for manual supervisor mode
        p_instr = self._normalize(payload.get("instruction", ""))
        t_instr = self._normalize(getattr(task, "instruction", ""))
        return p_instr == t_instr

    def _build_file_fingerprint(self, path: Path) -> dict:
        if not path.exists():
            return {
                "path": path.as_posix(),
                "exists": False,
                "sha256": None,
            }

        file_bytes = path.read_bytes()
        return {
            "path": path.as_posix(),
            "exists": True,
            "sha256": hashlib.sha256(file_bytes).hexdigest(),
        }
