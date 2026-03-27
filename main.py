from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from bridge_logging.logger import configure_logging
from context.file_selector import FileSelector
from context.idea_loader import IdeaLoader
from context.repo_scanner import RepoScanner
from executor.aider_runner import AiderRunner
from executor.diff_collector import DiffCollector
from models.task import AiderContext, BridgeConfig, Task, TaskReport
from parser.task_parser import PlanParseError, TaskParser
from supervisor.agent import SupervisorAgent, SupervisorError
from utils.checkpoint import clear_checkpoint, load_checkpoint, save_checkpoint
from models.task import SubTask
from utils.manual_supervisor import ManualSupervisorError, ManualSupervisorSession
from utils.token_tracker import TokenTracker, save_session_to_log
from utils.project_knowledge import (
    load_knowledge,
    save_knowledge,
    to_context_text,
    update_knowledge_from_run,
)
from validator.validator import MechanicalValidator


def _write_json_file(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _build_task_metrics(
    tasks: list[Task],
    completed_ids: set[int],
    resumed_completed_ids: set[int],
    skipped: int,
    all_diffs: list[dict],
    run_status: str,
    failed_task_id: Optional[int],
) -> dict:
    current_run_completed_ids = sorted(completed_ids - resumed_completed_ids)
    return {
        "status": run_status,
        "planned_tasks": len(tasks),
        "completed_task_ids": current_run_completed_ids,
        "completed_tasks": len(current_run_completed_ids),
        "resumed_completed_task_ids": sorted(resumed_completed_ids),
        "skipped_tasks": skipped,
        "failed_task_id": failed_task_id,
        "diffs_recorded": len(all_diffs),
        "tasks": [
            {
                "id": task.id,
                "type": task.type,
                "files": list(task.files),
                "must_exist": list(task.must_exist),
                "must_not_exist": list(task.must_not_exist),
                "completed": task.id in completed_ids,
            }
            for task in tasks
        ],
    }


def _build_project_snapshot(
    repo_root: Path,
    goal: str,
    knowledge: dict,
    tasks: list[Task],
    completed_ids: set[int],
    resumed_completed_ids: set[int],
    failed_task_id: Optional[int],
    run_status: str,
) -> dict:
    file_tree: list[str] = []
    for path in sorted(repo_root.rglob("*")):
        if path.is_dir():
            continue
        relative_path = path.relative_to(repo_root).as_posix()
        if relative_path.startswith(".git/"):
            continue
        file_tree.append(relative_path)

    return {
        "status": run_status,
        "goal": goal,
        "repo_root": str(repo_root),
        "failed_task_id": failed_task_id,
        "completed_task_ids": sorted(completed_ids - resumed_completed_ids),
        "resumed_completed_task_ids": sorted(resumed_completed_ids),
        "pending_task_ids": [task.id for task in tasks if task.id not in completed_ids],
        "files": knowledge.get("files", {}),
        "features_done": knowledge.get("features_done", []),
        "runs": knowledge.get("runs", []),
        "file_tree": file_tree,
    }


def _build_latest_report(
    repo_root: Path,
    goal: str,
    config: BridgeConfig,
    tasks: list[Task],
    completed_ids: set[int],
    resumed_completed_ids: set[int],
    failed_task_id: Optional[int],
    elapsed_seconds: float,
    run_status: str,
) -> str:
    completed_tasks = [task for task in tasks if task.id in (completed_ids - resumed_completed_ids)]
    pending_tasks = [task for task in tasks if task.id not in completed_ids]
    lines: list[str] = [
        "# Latest Bridge Report",
        "",
        f"Status: `{run_status}`",
        f"Goal: `{goal}`",
        f"Repo: `{repo_root}`",
        f"Workflow profile: `{config.workflow_profile}`",
        f"Supervisor mode: `{config.supervisor_mode}`",
        f"Aider model: `{config.aider_model or 'default'}`",
        f"Elapsed seconds: `{round(elapsed_seconds, 1)}`",
        "",
        "## Task summary",
        "",
        f"- Planned tasks: `{len(tasks)}`",
        f"- Completed tasks: `{len(completed_ids - resumed_completed_ids)}`",
        f"- Resumed-from-checkpoint tasks: `{len(resumed_completed_ids)}`",
        f"- Failed task: `{failed_task_id if failed_task_id is not None else 'none'}`",
        "",
        "## Completed files",
        "",
    ]

    if completed_tasks:
        for task in completed_tasks:
            lines.append(f"- `{', '.join(task.files)}`")
    else:
        lines.append("- None")

    lines.extend(["", "## Pending files", ""])
    if pending_tasks:
        for task in pending_tasks[:10]:
            lines.append(f"- `{', '.join(task.files)}`")
    else:
        lines.append("- None")

    return "\n".join(lines) + "\n"


def _persist_bridge_progress(
    repo_root: Path,
    goal: str,
    config: BridgeConfig,
    knowledge: dict,
    tasks: list[Task],
    completed_ids: set[int],
    resumed_completed_ids: set[int],
    skipped: int,
    all_diffs: list[dict],
    elapsed_seconds: float,
    run_status: str,
    failed_task_id: Optional[int],
) -> None:
    progress_dir = repo_root / "bridge_progress"
    progress_dir.mkdir(parents=True, exist_ok=True)

    _write_json_file(
        progress_dir / "task_metrics.json",
        _build_task_metrics(
            tasks,
            completed_ids,
            resumed_completed_ids,
            skipped,
            all_diffs,
            run_status,
            failed_task_id,
        ),
    )
    _write_json_file(
        progress_dir / "project_snapshot.json",
        _build_project_snapshot(
            repo_root,
            goal,
            knowledge,
            tasks,
            completed_ids,
            resumed_completed_ids,
            failed_task_id,
            run_status,
        ),
    )
    (progress_dir / "LATEST_REPORT.md").write_text(
        _build_latest_report(
            repo_root,
            goal,
            config,
            tasks,
            completed_ids,
            resumed_completed_ids,
            failed_task_id,
            elapsed_seconds,
            run_status,
        ),
        encoding="utf-8",
    )


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Bridge a Supervisor Agent (Codex / Claude / any coding agent) "
            "with Aider running on a local LLM."
        )
    )
    parser.add_argument(
        "goal",
        nargs="?",
        default="Build a logging system feature",
        help="High-level implementation goal.",
    )
    parser.add_argument(
        "--repo-root",
        default=os.getcwd(),
        help="Target repository root. Defaults to the current working directory.",
    )
    parser.add_argument(
        "--plan-file",
        default=None,
        help="JSON plan file to execute instead of asking the supervisor to plan.",
    )
    parser.add_argument(
        "--idea-file",
        default=None,
        help="Architecture or product brief injected into the supervisor planning prompt.",
    )
    parser.add_argument(
        "--plan-output-file",
        default=None,
        help="Path to write the generated plan JSON for inspection or reuse.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate and parse the plan without invoking Aider or the review loop.",
    )
    parser.add_argument(
        "--max-plan-attempts",
        type=int,
        default=3,
        help="Retries when the supervisor returns invalid or unparseable JSON.",
    )
    parser.add_argument(
        "--max-task-retries",
        type=int,
        default=2,
        help="Maximum supervisor-requested REWORK cycles per task.",
    )
    parser.add_argument(
        "--validation-command",
        default=os.getenv("BRIDGE_DEFAULT_VALIDATION"),
        help="Optional CI gate command run after each task (e.g. 'python -m pytest').",
    )
    parser.add_argument(
        "--supervisor-command",
        default=None,
        help=(
            "Optional external supervisor command override. Ignored in "
            "--manual-supervisor mode."
        ),
    )
    parser.add_argument(
        "--manual-supervisor",
        action="store_true",
        help=(
            "Run in manual supervisor mode. The bridge will never invoke an external "
            "supervisor CLI. After each task it writes a review request JSON into "
            "bridge_progress/manual_supervisor/ and waits for a decision JSON."
        ),
    )
    parser.add_argument(
        "--manual-review-poll-seconds",
        type=int,
        default=2,
        help="Polling interval in seconds while waiting for a manual supervisor decision.",
    )
    parser.add_argument(
        "--workflow-profile",
        default="standard",
        choices=["standard", "micro"],
        help=(
            "Execution profile. 'micro' enforces one-file atomic tasks, required task assertions, "
            "and is optimized for local Aider plus manual supervision."
        ),
    )
    parser.add_argument(
        "--aider-command",
        default=os.getenv("BRIDGE_AIDER_COMMAND", "aider"),
        help="Command prefix for Aider.",
    )
    parser.add_argument(
        "--aider-model",
        default=os.getenv("BRIDGE_AIDER_MODEL"),
        help=(
            "Local LLM model passed to Aider via --model "
            "(e.g. ollama/mistral, ollama/codellama, ollama/deepseek-coder). "
            "Leave unset to use Aider's default model configuration."
        ),
    )
    parser.add_argument(
        "--task-timeout",
        type=int,
        default=300,
        help="Max seconds any single subprocess call (Aider or supervisor) may run before being killed. Default: 300.",
    )
    parser.add_argument(
        "--confirm-plan",
        action="store_true",
        help=(
            "Show a preview of all tasks after planning and ask for confirmation "
            "before any Aider task runs. Useful when running interactively."
        ),
    )
    parser.add_argument(
        "--aider-no-map",
        action="store_true",
        help=(
            "Pass --map-tokens 0 to Aider, disabling repo-map scanning. "
            "Use for projects with large non-code directories (Unity Library/, node_modules/) "
            "that cause Aider to hang during its initial scan."
        ),
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help=(
            "Skip supervisor review after each task. "
            "Mechanical validation (syntax, file existence) still runs. "
            "Automatically enabled when BRIDGE_SUPERVISOR_COMMAND is not set. "
            "Use this when running the bridge from an interactive AI session "
            "(e.g. Claude Code) that reviews diffs directly."
        ),
    )
    parser.add_argument(
        "--skip-onboarding-scan",
        action="store_true",
        help=(
            "Skip the one-time source scan performed on the first run against an "
            "existing project. The scan reads source files to pre-populate project "
            "knowledge so the supervisor generates a better first plan. Use this flag "
            "to bypass it (e.g. when knowledge already exists or for faster CI runs)."
        ),
    )
    parser.add_argument(
        "--session-tokens",
        type=int,
        default=0,
        metavar="N",
        help=(
            "Tokens spent by the AI supervisor in this interactive session "
            "(reading files, generating the plan, reviewing diffs, conversation). "
            "Pass the exact value if your AI client shows token usage (e.g. Claude Code "
            "token counter, /cost command). If omitted the bridge estimates from file sizes."
        ),
    )
    parser.add_argument(
        "--auto-split-threshold",
        type=int,
        default=0,
        metavar="N",
        help=(
            "Automatically split any task targeting N or more files into individual "
            "single-file sub-tasks before Aider runs them. 0 = disabled (default). "
            "Recommended: 3 when using small local models (e.g. ollama/qwen2.5-coder:7b) "
            "that struggle to edit multiple files in a single pass without losing context."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity.",
    )
    return parser


def load_plan_from_file(plan_file: Path, parser: TaskParser) -> list[Task]:
    raw = plan_file.read_text(encoding="utf-8")
    return parser.parse(raw)


def auto_split_tasks(
    tasks: list[Task],
    threshold: int,
    logger: logging.Logger,
) -> list[Task]:
    """Split tasks that target >= threshold files into individual single-file sub-tasks.

    Each sub-task keeps the full original instruction (so Aider retains cross-file
    context) plus a one-line focus directive at the top:

        Focus ONLY on Assets/Scripts/Core/PlayerController.cs.
        Do NOT create or modify any other file.

        [original instruction follows]

    This prevents small models (7B/13B) from editing the wrong file or producing
    partial implementations across multiple files in a single Aider run.

    Sub-task ID scheme: original_id * 1000 + 1-based index.
    Example: task 5 with 3 files → sub-tasks 5001, 5002, 5003.

    Tasks with fewer than threshold files are passed through unchanged.
    context_files are preserved on every sub-task so read-only references
    remain available.
    """
    if threshold <= 0:
        return tasks

    result: list[Task] = []
    for task in tasks:
        if len(task.files) < threshold:
            result.append(task)
            continue

        logger.info(
            "Auto-split: task %s has %d file(s) (threshold=%d) → %d single-file sub-tasks",
            task.id,
            len(task.files),
            threshold,
            len(task.files),
        )
        for index, file_path in enumerate(task.files, start=1):
            focus_prefix = (
                f"Focus ONLY on {file_path}.\n"
                f"Do NOT create or modify any other file.\n\n"
            )
            result.append(
                Task(
                    id=task.id * 1000 + index,
                    files=[file_path],
                    instruction=focus_prefix + task.instruction,
                    type=task.type,
                    context_files=task.context_files,
                    must_exist=task.must_exist,
                    must_not_exist=task.must_not_exist,
                )
            )

    return result


def obtain_plan(
    config: BridgeConfig,
    supervisor: SupervisorAgent,
    task_parser: TaskParser,
    repo_tree: str,
    logger: logging.Logger,
    knowledge_context: Optional[str] = None,
) -> list[Task]:
    """Ask the supervisor to produce a valid JSON plan, retrying on failure.

    No fallback planner exists. If the supervisor cannot produce a valid plan
    after all attempts, the bridge raises and the user should supply --plan-file.
    """
    feedback: Optional[str] = None

    for attempt in range(1, config.max_plan_attempts + 1):
        logger.info("Requesting plan — attempt %s of %s", attempt, config.max_plan_attempts)
        try:
            plan_text = supervisor.generate_plan(
                config.goal,
                repo_tree,
                config.idea_text,
                feedback,
                knowledge_context,
                config.workflow_profile,
            )
            tasks = task_parser.parse(plan_text)

            if config.plan_output_file is not None:
                config.plan_output_file.parent.mkdir(parents=True, exist_ok=True)
                config.plan_output_file.write_text(plan_text, encoding="utf-8")
                logger.info("Saved plan to %s", config.plan_output_file)

            logger.info("Supervisor produced %s task(s)", len(tasks))
            return tasks

        except (SupervisorError, PlanParseError) as ex:
            feedback = str(ex)
            logger.warning("Plan attempt %s failed: %s", attempt, ex)

    raise RuntimeError(
        "Supervisor failed to produce a valid plan after all attempts. "
        "Use --plan-file to supply a plan manually."
    )


def show_plan_preview(tasks: list[Task], logger: logging.Logger) -> bool:
    """Print the task plan and ask the user to confirm before execution.

    Returns True if the user confirms, False if they cancel.
    """
    print("\n" + "=" * 60)
    print(f"  PLAN PREVIEW — {len(tasks)} task(s)")
    print("=" * 60)
    for task in tasks:
        files_display = ", ".join(task.files) if task.files else "(no specific files — Aider chooses)"
        ctx_display = f"  [reads: {', '.join(task.context_files)}]" if task.context_files else ""
        instruction_preview = task.instruction[:120].replace("\n", " ")
        if len(task.instruction) > 120:
            instruction_preview += "..."
        print(f"\n  [{task.id}] {task.type.upper()}  {files_display}{ctx_display}")
        print(f"       {instruction_preview}")
    print("\n" + "=" * 60)

    try:
        answer = input("Proceed? [y]es / [n]o: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False

    if answer in {"y", "yes"}:
        return True

    logger.info("Run cancelled by user at plan preview.")
    return False


def _enforce_workflow_profile(tasks: list[Task], config: BridgeConfig) -> None:
    if config.workflow_profile != "micro":
        return

    for task in tasks:
        if len(task.files) != 1:
            raise RuntimeError(
                f"Micro-task profile requires exactly one file per task. "
                f"Task {task.id} targets {len(task.files)} file(s)."
            )

        if task.type == "create":
            if not task.must_exist:
                raise RuntimeError(
                    f"Micro-task profile requires must_exist for create tasks. Task {task.id} is missing it."
                )
            target_file = Path(task.files[0]).as_posix()
            normalized_must_exist = [Path(file_path).as_posix() for file_path in task.must_exist]
            if target_file not in normalized_must_exist:
                raise RuntimeError(
                    f"Micro-task create task {task.id} must assert its target file in must_exist."
                )

        if task.type == "delete" and not task.must_not_exist:
            raise RuntimeError(
                f"Micro-task profile requires must_not_exist for delete tasks. Task {task.id} is missing it."
            )

        if task.type == "modify" and not task.must_exist and not task.must_not_exist:
            raise RuntimeError(
                f"Micro-task profile requires an observable assertion on modify tasks. "
                f"Task {task.id} must define must_exist or must_not_exist."
            )


_UNEXPECTED_FILE_IGNORE_PREFIXES: tuple[str, ...] = (
    ".git/",
    ".aider.tags.cache.v4/",
    "__pycache__/",
    "bridge_progress/",
    "logs/",
    "taskJsons/",
)
_UNEXPECTED_FILE_IGNORE_NAMES: tuple[str, ...] = (
    ".aider.chat.history.md",
    ".aider.input.history",
)


def _snapshot_repo_files(repo_root: Path) -> set[str]:
    snapshot: set[str] = set()
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        relative_path = path.relative_to(repo_root).as_posix()
        if relative_path in _UNEXPECTED_FILE_IGNORE_NAMES:
            continue
        if any(relative_path.startswith(prefix) for prefix in _UNEXPECTED_FILE_IGNORE_PREFIXES):
            continue
        snapshot.add(relative_path)
    return snapshot


def _find_unexpected_files(
    before_snapshot: set[str],
    after_snapshot: set[str],
    task: Task,
) -> list[str]:
    allowed = {Path(file_path).as_posix() for file_path in task.files}
    allowed.update(Path(file_path).as_posix() for file_path in task.must_exist)
    unexpected = sorted((after_snapshot - before_snapshot) - allowed)
    return unexpected


def _execute_sub_tasks(
    sub_tasks: list[SubTask],
    config: BridgeConfig,
    selector: FileSelector,
    runner: AiderRunner,
    validator: MechanicalValidator,
    logger: logging.Logger,
    aider_context: Optional[AiderContext],
) -> None:
    for sub_task in sub_tasks:
        selected_files = selector.select(sub_task.files)
        logger.info(
            "Executing corrective sub-task %s.%s — files: %s",
            sub_task.parent_id,
            sub_task.step,
            ", ".join(sub_task.files),
        )
        execution_result = runner.run(
            Task(
                id=sub_task.id,
                files=sub_task.files,
                instruction=sub_task.instruction,
                type=sub_task.type,
            ),
            selected_files.all_paths,
            aider_context,
        )
        if not execution_result.succeeded:
            raise RuntimeError(
                f"Corrective sub-task {sub_task.id} failed: "
                f"{_summarize_process_failure(execution_result.stderr, execution_result.stdout)}"
            )
        validation = validator.validate(
            Task(
                id=sub_task.id,
                files=sub_task.files,
                instruction=sub_task.instruction,
                type=sub_task.type,
            ),
            selected_files.all_paths,
        )
        if not validation.succeeded:
            raise RuntimeError(
                f"Corrective sub-task {sub_task.id} failed mechanical checks: {validation.message}"
            )


def execute_task_with_review(
    task: Task,
    config: BridgeConfig,
    supervisor: Optional[SupervisorAgent],
    manual_supervisor: Optional[ManualSupervisorSession],
    selector: FileSelector,
    runner: AiderRunner,
    diff_collector: DiffCollector,
    validator: MechanicalValidator,
    logger: logging.Logger,
    aider_context: Optional[AiderContext] = None,
) -> str:
    """Run one task through the Aider → diff → mechanical check loop.

    In auto-approve mode (supervisor is None or config.auto_approve):
      1. Aider executes the instruction.
      2. DiffCollector captures what changed.
      3. MechanicalValidator runs syntax/existence checks. On failure: retry.
      4. Mechanical pass → task approved automatically. Diff returned to caller.

    In manual-supervisor mode:
      1. Aider executes the instruction.
      2. DiffCollector and MechanicalValidator collect execution evidence.
      3. The bridge writes a review request JSON and waits for a decision file.
      4. PASS continues, REWORK retries, SUBPLAN executes corrective sub-tasks.

    In supervised mode (supervisor provided and not auto_approve):
      Steps 1-3 as above, then:
      4. SupervisorAgent reviews the diff → PASS or REWORK with new instruction.
    """
    selected_files = selector.select(task.files)
    current_instruction = task.instruction

    for attempt in range(config.max_task_retries + 1):
        repo_before = _snapshot_repo_files(config.repo_root)
        current_task = Task(
            id=task.id,
            files=task.files,
            instruction=current_instruction,
            type=task.type,
            context_files=task.context_files,
            must_exist=task.must_exist,
            must_not_exist=task.must_not_exist,
        )

        logger.info(
            "Task %s — attempt %s/%s — files: %s",
            current_task.id,
            attempt + 1,
            config.max_task_retries + 1,
            ", ".join(current_task.files),
        )

        if config.dry_run:
            logger.info("[dry-run] Task %s: %s", current_task.id, current_instruction)
            return

        # ── Step 1: Execute via Aider ────────────────────────────────────────
        execution_result = runner.run(current_task, selected_files.all_paths, aider_context)

        if execution_result.exit_code == -1:
            raise RuntimeError(
                f"Task {current_task.id}: Aider could not start. {execution_result.stderr}"
            )

        if not execution_result.succeeded:
            logger.warning(
                "Task %s: Aider exited with code %s",
                current_task.id,
                execution_result.exit_code,
            )

        # Fix #4: catch 0-byte output files — Aider may exit 0 but leave files
        # empty if it was killed mid-write (timeout) or hit an encoding crash.
        if current_task.type in {"create", "modify"}:
            empty_files = [
                fp for fp in current_task.files
                if (config.repo_root / fp).exists()
                and (config.repo_root / fp).stat().st_size == 0
            ]
            if empty_files:
                logger.warning(
                    "Task %s: Aider wrote 0-byte file(s): %s — treating as failure",
                    current_task.id,
                    ", ".join(empty_files),
                )
                if attempt >= config.max_task_retries:
                    raise RuntimeError(
                        f"Task {current_task.id} produced empty file(s) after "
                        f"{attempt + 1} attempt(s): {', '.join(empty_files)}"
                    )
                wait_seconds = min(2 ** attempt, 30)
                time.sleep(wait_seconds)
                continue

        # ── Step 2: Collect diff ─────────────────────────────────────────────
        diff = diff_collector.collect()
        logger.debug("Task %s: diff collected (%s chars)", current_task.id, len(diff))
        repo_after = _snapshot_repo_files(config.repo_root)
        unexpected_files = _find_unexpected_files(repo_before, repo_after, current_task)
        if unexpected_files:
            logger.warning(
                "Task %s: unexpected files created outside task scope: %s",
                current_task.id,
                ", ".join(unexpected_files),
            )

        # ── Step 3: Mechanical checks (syntax, existence, CI gate) ───────────
        validation_result = validator.validate(current_task, selected_files.all_paths)
        if validation_result.succeeded and unexpected_files:
            validation_result = validator.validate(
                Task(
                    id=current_task.id,
                    files=current_task.files,
                    instruction=current_task.instruction,
                    type=current_task.type,
                    context_files=current_task.context_files,
                    must_exist=current_task.must_exist,
                    must_not_exist=current_task.must_not_exist + unexpected_files,
                ),
                selected_files.all_paths,
            )

        if not validation_result.succeeded:
            logger.warning(
                "Task %s: mechanical check failed — %s",
                current_task.id,
                validation_result.message,
            )
            if attempt >= config.max_task_retries:
                raise RuntimeError(
                    f"Task {current_task.id} failed mechanical checks after "
                    f"{attempt + 1} attempt(s): {validation_result.message}"
                )

            # In supervised mode: ask supervisor for corrective sub-tasks.
            # In auto-approve mode: just retry the original instruction.
            if manual_supervisor is not None:
                task_report = TaskReport(
                    task=current_task,
                    execution_result=execution_result,
                    diff=diff,
                )
                request_path = manual_supervisor.submit_review_request(
                    task_report,
                    validation_message=validation_result.message,
                    unexpected_files=unexpected_files,
                )
                _emit_structured(
                    {
                        "type": "review_required",
                        "mode": "manual",
                        "task_id": current_task.id,
                        "request_file": str(request_path),
                        "validation_message": validation_result.message,
                    }
                )
                decision = manual_supervisor.wait_for_decision(current_task.id)
                if decision.verdict == "PASS":
                    logger.info(
                        "Task %s: manual supervisor overrode validation failure and approved",
                        current_task.id,
                    )
                    _emit_structured(
                        {
                            "type": "task_complete",
                            "task_id": current_task.id,
                            "diff": diff[:1500],
                        }
                    )
                    return diff
                if decision.verdict == "SUBPLAN":
                    _execute_sub_tasks(
                        decision.sub_tasks,
                        config,
                        selector,
                        runner,
                        validator,
                        logger,
                        aider_context,
                    )
                elif decision.new_instruction:
                    current_instruction = decision.new_instruction
                wait_seconds = min(2 ** attempt, 30)
                time.sleep(wait_seconds)
                continue

            if supervisor is not None and not config.auto_approve:
                try:
                    sub_tasks = supervisor.generate_subplan(
                        current_task, validation_result.message
                    )
                    logger.info(
                        "Task %s: supervisor generated %d sub-task(s) to fix: %s",
                        current_task.id, len(sub_tasks), validation_result.message,
                    )
                    for sub_task in sub_tasks:
                        sub_files = selector.select(sub_task.files)
                        runner_task = Task(
                            id=sub_task.id,
                            files=sub_task.files,
                            instruction=sub_task.instruction,
                            type=sub_task.type,
                        )
                        sub_result = runner.run(runner_task, sub_files.all_paths, aider_context)
                        logger.info(
                            "Task %s — sub-task %s.%s: exit code %s",
                            current_task.id, sub_task.parent_id, sub_task.step,
                            sub_result.exit_code,
                        )
                except SupervisorError as sub_ex:
                    logger.warning(
                        "Task %s: sub-plan generation failed (%s) — retrying original",
                        current_task.id, sub_ex,
                    )

            wait_seconds = min(2 ** attempt, 30)
            logger.debug(
                "Task %s: backing off %ss before retry %s",
                current_task.id, wait_seconds, attempt + 2,
            )
            time.sleep(wait_seconds)
            continue

        # ── Step 4: Review ────────────────────────────────────────────────────
        if manual_supervisor is not None:
            task_report = TaskReport(
                task=current_task,
                execution_result=execution_result,
                diff=diff,
            )
            request_path = manual_supervisor.submit_review_request(
                task_report,
                validation_message=validation_result.message,
                unexpected_files=unexpected_files,
            )
            _emit_structured(
                {
                    "type": "review_required",
                    "mode": "manual",
                    "task_id": current_task.id,
                    "request_file": str(request_path),
                    "validation_message": validation_result.message,
                }
            )
            review = manual_supervisor.wait_for_decision(current_task.id)
            if review.verdict == "PASS":
                logger.info("Task %s: manual supervisor approved", current_task.id)
                _emit_structured({
                    "type": "task_complete",
                    "task_id": current_task.id,
                    "diff": diff[:1500],
                })
                return diff
            if review.verdict == "SUBPLAN":
                _execute_sub_tasks(
                    review.sub_tasks,
                    config,
                    selector,
                    runner,
                    validator,
                    logger,
                    aider_context,
                )
                wait_seconds = min(2 ** attempt, 30)
                time.sleep(wait_seconds)
                continue

            logger.warning(
                "Task %s: manual supervisor requested rework (attempt %s): %s",
                current_task.id,
                attempt + 1,
                review.new_instruction,
            )
            if attempt >= config.max_task_retries:
                raise RuntimeError(
                    f"Task {current_task.id} exhausted rework retries after manual supervisor feedback."
                )
            wait_seconds = min(2 ** attempt, 30)
            time.sleep(wait_seconds)
            current_instruction = review.new_instruction or current_instruction
            continue

        if config.auto_approve or supervisor is None:
            # Auto-approve mode: mechanical pass = done. No external AI call.
            logger.info("Task %s: mechanical checks passed — auto-approved", current_task.id)
            _emit_structured({
                "type": "task_complete",
                "task_id": current_task.id,
                "diff": diff[:1500],
            })
            return diff

        # Supervised mode: send diff to supervisor for PASS / REWORK decision.
        task_report = TaskReport(
            task=current_task,
            execution_result=execution_result,
            diff=diff,
        )
        review = supervisor.review_task(task_report)

        if review.verdict == "PASS":
            logger.info("Task %s: supervisor approved", current_task.id)
            _emit_structured({
                "type": "task_complete",
                "task_id": current_task.id,
                "diff": diff[:1500],
            })
            return diff

        logger.warning(
            "Task %s: supervisor requested rework (attempt %s): %s",
            current_task.id, attempt + 1, review.new_instruction,
        )
        if attempt >= config.max_task_retries:
            raise RuntimeError(
                f"Task {current_task.id} exhausted rework retries after supervisor feedback."
            )
        wait_seconds = min(2 ** attempt, 30)
        time.sleep(wait_seconds)
        current_instruction = review.new_instruction  # type: ignore[assignment]

    raise RuntimeError(f"Task {task.id} exhausted all retries.")


_PAUSE_FILENAME = ".bridge_pause"


def wait_if_paused(repo_root: Path, logger: logging.Logger) -> None:
    """Block execution if a pause file exists at the repo root.

    The UI creates .bridge_pause to pause and deletes it to resume.
    Checks every second so the bridge stays responsive to resume requests.
    """
    pause_file = repo_root / _PAUSE_FILENAME
    if not pause_file.exists():
        return
    logger.info("Bridge paused — waiting for resume (delete %s to continue)", _PAUSE_FILENAME)
    _emit_structured({"type": "paused", "pause_file": str(pause_file)})
    while pause_file.exists():
        time.sleep(1)
    logger.info("Bridge resumed.")
    _emit_structured({"type": "resumed"})


def _emit_structured(data: dict) -> None:
    """Print a machine-readable JSON event on stdout for the UI bridge runner.

    bridge_runner.py detects lines starting with {"_bridge_event": true} and
    parses them into rich SSE events for the frontend. Regular log lines are
    unaffected — they don't start with that key.
    """
    print(json.dumps({"_bridge_event": True, **data}), flush=True)


def _summarize_process_failure(stderr: str, stdout: str) -> str:
    for stream_name, content in (("stderr", stderr), ("stdout", stdout)):
        normalized = content.strip()
        if normalized:
            return f"{stream_name}: {normalized.splitlines()[0]}"
    return "No process output captured."


def record_rollback_point(repo_root: Path, logger: logging.Logger) -> Optional[str]:
    """Record the current HEAD SHA so the user can undo all bridge changes on failure.

    Returns the SHA string, or None if the repo has no commits yet.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if result.returncode == 0:
            sha = result.stdout.strip()
            logger.info("Rollback point: %s", sha)
            logger.info("If this run fails, undo all changes with:  git reset --hard %s", sha)
            return sha
    except Exception:
        pass
    logger.debug("Could not record rollback point (no commits yet or git unavailable).")
    return None


# Model size patterns that indicate a small local model (≤ 14 B parameters).
# Matched against the --aider-model value (case-insensitive).
# Examples that match: 7b, 7B, 6.7b, 8b, 13b, 14b, 3b, 1b, 1.5b
# Examples that do NOT match: 32b, 34b, 70b, 72b, claude, gpt-4, gemini
_SMALL_MODEL_RE = re.compile(r"\b(1[0-4]|[1-9](\.\d+)?)b\b", re.IGNORECASE)
_AUTO_SPLIT_DEFAULT_THRESHOLD = 3


def _resolve_auto_split_threshold(
    explicit_threshold: int,
    aider_model: Optional[str],
    logger: logging.Logger,
) -> int:
    """Return the effective auto-split threshold.

    Rules (in priority order):
    1. If the user explicitly passed --auto-split-threshold N (N > 0) → use N.
    2. If --auto-split-threshold was NOT set (== 0) and the model name contains
       a small-model size marker (≤ 14 B) → auto-enable with threshold 3.
    3. Otherwise → 0 (disabled).

    This means AIs never need to think about whether to pass the flag —
    the bridge enables it automatically for small local models.
    """
    if explicit_threshold > 0:
        return explicit_threshold

    if aider_model and _SMALL_MODEL_RE.search(aider_model):
        logger.info(
            "Auto-split: detected small model %r — enabling --auto-split-threshold %d automatically. "
            "Tasks with %d+ files will be split into single-file sub-tasks. "
            "Pass --auto-split-threshold 0 to disable.",
            aider_model,
            _AUTO_SPLIT_DEFAULT_THRESHOLD,
            _AUTO_SPLIT_DEFAULT_THRESHOLD,
        )
        return _AUTO_SPLIT_DEFAULT_THRESHOLD

    return 0


def estimate_session_tokens(
    idea_file: Optional[Path],
    plan_file: Optional[Path],
    repo_root: Path,
    tasks: list[Task],
) -> int:
    """Estimate tokens the AI supervisor spent in its interactive session.

    Covers the work every AI does before and during a bridge run:
      - Reading AGENTIC_AI_ONBOARDING.md (from the bridge directory)
      - Reading WORK_LOG.md (from the target repo, if present)
      - Reading bridge_progress/project_knowledge.json (if present)
      - Reading the idea / brief file (if provided)
      - Generating the task plan JSON (output tokens — from plan file or task instructions)
      - Conversation overhead (~500 tokens for messages back and forth)

    Uses 1 token ≈ 4 characters (standard approximation, ±15%).
    """
    total = 0

    def _file_tokens(path: Path) -> int:
        try:
            return path.stat().st_size // 4
        except OSError:
            return 0

    # Bridge onboarding doc — always read by any AI supervisor
    bridge_dir = Path(__file__).resolve().parent
    onboarding = bridge_dir / "AGENTIC_AI_ONBOARDING.md"
    total += _file_tokens(onboarding) if onboarding.exists() else 2_000

    # WORK_LOG.md in the target repo
    work_log = repo_root / "WORK_LOG.md"
    total += _file_tokens(work_log)

    # Project knowledge cache
    knowledge = repo_root / "bridge_progress" / "project_knowledge.json"
    total += _file_tokens(knowledge)

    # Idea / brief file
    if idea_file and idea_file.exists():
        total += _file_tokens(idea_file)

    # Plan output — either from the plan file or from task instruction lengths
    if plan_file and plan_file.exists():
        total += _file_tokens(plan_file)
    else:
        total += sum(len(t.instruction) // 4 for t in tasks)

    # Conversation overhead (user messages, AI replies around the run)
    total += 500

    return total


def run_preflight_checks(config: BridgeConfig, logger: logging.Logger) -> None:
    """Validate environment before spending any tokens on planning or execution.

    Checks (in order):
    1. Aider executable is installed and on PATH.
    2. Repo root is a git repository.
    3. At least 50 MB of free disk space is available at the repo root.
    """
    aider_bin = config.aider_command.split()[0]
    logger.debug("Pre-flight: checking Aider binary %r", aider_bin)
    if shutil.which(aider_bin) is None:
        raise RuntimeError(
            f"Aider not found: {aider_bin!r}. Install it with: pip install aider-chat"
        )

    logger.debug("Pre-flight: checking git repository at %s", config.repo_root)
    if not (config.repo_root / ".git").exists():
        raise RuntimeError(
            f"Repo root is not a git repository: {config.repo_root}. "
            "Initialise with: git init"
        )

    logger.debug("Pre-flight: checking disk space at %s", config.repo_root)
    free_bytes = shutil.disk_usage(config.repo_root).free
    if free_bytes < 50 * 1024 * 1024:
        raise RuntimeError(
            f"Insufficient disk space at {config.repo_root}: "
            f"less than 50 MB free ({free_bytes // (1024 * 1024)} MB available)."
        )

    # Ensure taskJsons/ is in the target repo's .gitignore so AI-generated plan
    # files are never accidentally committed.
    _gitignore_path = config.repo_root / ".gitignore"
    _gitignore_entry = "taskJsons/"
    try:
        existing = _gitignore_path.read_text(encoding="utf-8") if _gitignore_path.exists() else ""
        if _gitignore_entry not in existing.splitlines():
            with _gitignore_path.open("a", encoding="utf-8") as _gf:
                _gf.write(f"\n# AI-generated task plan JSON files\n{_gitignore_entry}\n")
            logger.info("Added %r to %s", _gitignore_entry, _gitignore_path)
    except OSError:
        pass  # Non-fatal — just log nothing

    logger.info("Pre-flight checks passed.")


def main() -> int:
    arg_parser = build_argument_parser()
    args = arg_parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    logger = configure_logging(repo_root / "logs", args.log_level)
    logger.info("Bridge starting — repo: %s", repo_root)

    idea_loader = IdeaLoader()
    idea_file: Optional[Path] = Path(args.idea_file).resolve() if args.idea_file else None
    plan_output_file: Optional[Path] = (
        Path(args.plan_output_file).resolve() if args.plan_output_file else None
    )
    idea_text = idea_loader.load(idea_file)
    if idea_file:
        logger.info("Loaded idea file: %s", idea_file)

    # Resolve supervisor mode.
    _supervisor_cmd = args.supervisor_command or os.getenv("BRIDGE_SUPERVISOR_COMMAND", "")
    _manual_supervisor_enabled = bool(args.manual_supervisor) or args.workflow_profile == "micro"
    _auto_approve = (
        not _manual_supervisor_enabled
        and (bool(args.auto_approve) or (not _supervisor_cmd and bool(args.plan_file)))
    )
    _supervisor_mode = "manual" if _manual_supervisor_enabled else ("auto" if _auto_approve else "external")
    if _manual_supervisor_enabled:
        logger.info(
            "Bridge running in MANUAL-SUPERVISOR mode — review requests will be written to "
            "bridge_progress/manual_supervisor/ and no external AI CLI will be invoked."
        )
    elif _auto_approve:
        logger.info(
            "Bridge running in AUTO-APPROVE mode — "
            "mechanical validation only, no external supervisor AI."
        )

    # Resolve auto-split threshold: explicit flag takes priority; falls back to
    # model-name detection so small models (≤14B) get it automatically.
    _aider_model = args.aider_model or None
    _auto_split = _resolve_auto_split_threshold(
        int(args.auto_split_threshold), _aider_model, logger
    )
    if args.workflow_profile == "micro":
        if _auto_split > 0:
            logger.info(
                "Workflow profile 'micro' disables auto-splitting because tasks are expected to be pre-split."
            )
        _auto_split = 0

    config = BridgeConfig(
        goal=args.goal,
        repo_root=repo_root,
        dry_run=bool(args.dry_run),
        max_plan_attempts=int(args.max_plan_attempts),
        max_task_retries=int(args.max_task_retries),
        validation_command=args.validation_command,
        supervisor_command="manual" if _manual_supervisor_enabled else (_supervisor_cmd or "claude"),
        aider_command=args.aider_command,
        aider_model=_aider_model,
        idea_file=idea_file,
        idea_text=idea_text,
        plan_output_file=plan_output_file,
        task_timeout_seconds=int(args.task_timeout),
        aider_no_map=bool(args.aider_no_map),
        auto_approve=_auto_approve,
        auto_split_threshold=_auto_split,
        supervisor_mode=_supervisor_mode,
        manual_review_poll_seconds=max(1, int(args.manual_review_poll_seconds)),
        workflow_profile=str(args.workflow_profile),
        skip_onboarding_scan=bool(args.skip_onboarding_scan),
    )

    run_preflight_checks(config, logger)
    rollback_sha = record_rollback_point(repo_root, logger)
    run_start = time.monotonic()

    token_tracker = TokenTracker()
    repo_tree = RepoScanner(repo_root).scan()
    task_parser = TaskParser()
    selector = FileSelector(repo_root)

    # Load project knowledge so every AI session starts with full project context.
    knowledge = load_knowledge(repo_root)

    # Onboarding scan: run once on first use against an existing project to
    # pre-populate file roles so the supervisor generates an accurate plan
    # from the very first run (instead of guessing from file names only).
    if not knowledge.get("files") and not config.skip_onboarding_scan:
        try:
            from utils.onboarding_scanner import OnboardingScanner  # lazy import
            knowledge = OnboardingScanner(repo_root, logger).run(knowledge)
            save_knowledge(knowledge, repo_root)
            logger.info(
                "Onboarding scan complete: %d files registered, type=%s, language=%s",
                len(knowledge["files"]),
                knowledge["project"].get("type", "unknown"),
                knowledge["project"].get("language", "unknown"),
            )
        except Exception as _scan_err:
            logger.warning("Onboarding scan failed (continuing without): %s", _scan_err)

    knowledge_context = to_context_text(knowledge)
    if knowledge.get("files"):
        logger.info(
            "Project knowledge loaded: %d files registered, %d features done",
            len(knowledge["files"]),
            len(knowledge.get("features_done", [])),
        )
    else:
        logger.info("No project knowledge yet — will create after this run.")

    # Only create supervisor agent when needed (not in auto-approve mode).
    supervisor: Optional[SupervisorAgent] = None
    manual_supervisor: Optional[ManualSupervisorSession] = None
    if _manual_supervisor_enabled:
        manual_supervisor = ManualSupervisorSession(
            repo_root,
            logger,
            poll_seconds=config.manual_review_poll_seconds,
        )
    elif not _auto_approve:
        supervisor = SupervisorAgent(
            repo_root,
            config.supervisor_command,
            logger,
            timeout=config.task_timeout_seconds,
            token_tracker=token_tracker,
        )

    runner = AiderRunner(
        repo_root,
        config.aider_command,
        logger,
        config.aider_model,
        timeout=config.task_timeout_seconds,
        no_map=config.aider_no_map,
    )
    diff_collector = DiffCollector(repo_root)
    validator = MechanicalValidator(repo_root, config.validation_command, logger)
    tasks: list[Task] = []
    completed_ids: set[int] = set()
    completed_summaries: list[str] = []
    all_diffs: list[dict] = []
    skipped = 0
    failed_task_id: Optional[int] = None
    resumed_completed_ids: set[int] = set()

    try:
        if args.plan_file:
            tasks = load_plan_from_file(Path(args.plan_file).resolve(), task_parser)
            logger.info("Loaded %s task(s) from plan file", len(tasks))
        else:
            if _manual_supervisor_enabled:
                raise RuntimeError(
                    "Manual-supervisor mode requires --plan-file. "
                    "Generate the plan in-session and pass it explicitly."
                )
            if supervisor is None:
                raise RuntimeError(
                    "No plan file provided and no supervisor configured. "
                    "Either pass --plan-file or set BRIDGE_SUPERVISOR_COMMAND."
                )
            tasks = obtain_plan(
                config, supervisor, task_parser, repo_tree, logger, knowledge_context
            )

        # Auto-split multi-file tasks into single-file sub-tasks.
        # Applied after planning so the split is visible in --confirm-plan preview
        # and in --plan-output-file (which captures the original supervisor plan).
        if config.auto_split_threshold > 0:
            _pre_split_count = len(tasks)
            tasks = auto_split_tasks(tasks, config.auto_split_threshold, logger)
            _post_split_count = len(tasks)
            if _post_split_count != _pre_split_count:
                logger.info(
                    "Auto-split complete: %d original task(s) → %d single-file sub-task(s)",
                    _pre_split_count,
                    _post_split_count,
                )

        _enforce_workflow_profile(tasks, config)

        # Feature 6: plan preview + confirmation before any Aider task runs.
        if args.confirm_plan and not args.dry_run:
            if not show_plan_preview(tasks, logger):
                return 0

        completed_ids = load_checkpoint(repo_root)
        resumed_completed_ids = set(completed_ids)

        for task_index, task in enumerate(tasks):
            wait_if_paused(repo_root, logger)

            if task.id in completed_ids:
                logger.info("Task %s: skipping — already completed (checkpoint)", task.id)
                completed_summaries.append(
                    f"[{task.id}] {task.type} {', '.join(task.files[:2])}"
                    + (" ..." if len(task.files) > 2 else "")
                )
                skipped += 1
                continue

            aider_context = AiderContext(
                goal=config.goal,
                task_number=task_index + 1,
                total_tasks=len(tasks),
                completed_summaries=list(completed_summaries),
            )

            task_diff = execute_task_with_review(
                task, config, supervisor, manual_supervisor, selector,
                runner, diff_collector, validator, logger,
                aider_context=aider_context,
            )
            failed_task_id = None
            completed_ids.add(task.id)
            save_checkpoint(repo_root, completed_ids)
            task_summary = (
                f"[{task.id}] {task.type} {', '.join(task.files[:2])}"
                + (" ..." if len(task.files) > 2 else "")
            )
            completed_summaries.append(task_summary)
            all_diffs.append({"task_id": task.id, "summary": task_summary, "diff": task_diff or ""})
            knowledge = update_knowledge_from_run(
                knowledge,
                config.goal,
                [task],
                all_diffs[-1:],
                repo_root,
                append_run_record=False,
            )
            save_knowledge(knowledge, repo_root)
            _persist_bridge_progress(
                repo_root,
                config.goal,
                config,
                knowledge,
                tasks,
                completed_ids,
                resumed_completed_ids,
                skipped,
                all_diffs,
                round(time.monotonic() - run_start, 1),
                "running",
                None,
            )

        clear_checkpoint(repo_root)
        elapsed = round(time.monotonic() - run_start, 1)
        executed = len(tasks) - skipped

        # Update project knowledge with every file touched and feature completed.
        executed_tasks = [t for t in tasks if t.id not in completed_ids or True]
        knowledge = update_knowledge_from_run(
            knowledge, config.goal, tasks, all_diffs, repo_root
        )
        save_knowledge(knowledge, repo_root)
        logger.info(
            "Project knowledge updated: %d files now registered",
            len(knowledge["files"]),
        )

        # In auto-approve mode: print a diff review summary so the AI session
        # (Claude Code / human) can review all changes in one place.
        if _auto_approve and all_diffs:
            _emit_structured({"type": "review_summary", "tasks": all_diffs})

        # ── Session token tracking ────────────────────────────────────────────
        # If the AI passed --session-tokens N (exact), use it.
        # Otherwise estimate from file sizes (what the AI likely read/wrote).
        _explicit_session_tokens = int(args.session_tokens)
        if _explicit_session_tokens > 0:
            token_tracker.record_session_tokens(_explicit_session_tokens, is_estimate=False)
            logger.info("Session tokens (exact, provided by AI): %d", _explicit_session_tokens)
        else:
            _estimated = estimate_session_tokens(
                idea_file, Path(args.plan_file).resolve() if args.plan_file else None,
                repo_root, tasks,
            )
            token_tracker.record_session_tokens(_estimated, is_estimate=True)
            logger.info(
                "Session tokens (estimated from file sizes): %d — "
                "pass --session-tokens N for exact value",
                _estimated,
            )

        # ── Token tracking: build session report and persist ─────────────────
        token_report = token_tracker.build_session_report(
            goal=config.goal,
            repo_root=repo_root,
            supervisor_command=config.supervisor_command,
            tasks_executed=executed,
            tasks_skipped=skipped,
            elapsed_seconds=elapsed,
        )
        # All progress files go into <repo_root>/bridge_progress/ so that
        # running the bridge on multiple projects never mixes their state.
        _progress_dir = repo_root / "bridge_progress"
        _progress_dir.mkdir(parents=True, exist_ok=True)
        _token_log_path = _progress_dir / "token_log.json"
        try:
            save_session_to_log(token_report, _token_log_path)
            logger.info(
                "Token usage: %s supervisor tokens used, ~%s saved (%.1f%%) — saved to %s",
                token_report["savings"]["actual_supervisor_tokens"],
                token_report["savings"]["tokens_saved"],
                token_report["savings"]["savings_percent"],
                _token_log_path,
            )
        except Exception as _tok_ex:
            logger.warning("Could not save token log: %s", _tok_ex)

        _emit_structured({"type": "token_report", "report": token_report})
        _emit_structured({"type": "knowledge_updated", "files": len(knowledge["files"])})

        summary = {
            "status": "success",
            "tasks": len(tasks),
            "executed": executed,
            "skipped": skipped,
            "elapsed_seconds": elapsed,
            "tokens": token_tracker.snapshot(),
        }
        # Write last_run.json so any tool can inspect the most recent run result.
        try:
            (_progress_dir / "last_run.json").write_text(
                json.dumps(summary, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

        _persist_bridge_progress(
            repo_root,
            config.goal,
            config,
            knowledge,
            tasks,
            completed_ids,
            resumed_completed_ids,
            skipped,
            all_diffs,
            elapsed,
            "success",
            None,
        )

        print(json.dumps(summary))
        logger.info(
            "Bridge run completed — %s task(s) executed, %s skipped, %.1fs total",
            executed, skipped, elapsed,
        )
        return 0

    except Exception as ex:
        elapsed = round(time.monotonic() - run_start, 1)
        if failed_task_id is None and tasks:
            pending_tasks = [task.id for task in tasks if task.id not in completed_ids]
            failed_task_id = pending_tasks[0] if pending_tasks else None
        logger.exception("Bridge run failed after %.1fs: %s", elapsed, ex)
        if rollback_sha:
            logger.warning("To undo all changes made during this run:  git reset --hard %s", rollback_sha)
        failure_summary = {"status": "failure", "error": str(ex), "elapsed_seconds": elapsed}
        try:
            _fail_dir = repo_root / "bridge_progress"
            _fail_dir.mkdir(parents=True, exist_ok=True)
            (_fail_dir / "last_run.json").write_text(
                json.dumps(failure_summary, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

        if tasks:
            knowledge = update_knowledge_from_run(
                knowledge,
                config.goal,
                [task for task in tasks if task.id in completed_ids],
                all_diffs,
                repo_root,
                append_run_record=True,
                run_status="failure",
                tasks_completed_override=len(completed_ids),
            )
            save_knowledge(knowledge, repo_root)
            _persist_bridge_progress(
                repo_root,
                config.goal,
                config,
                knowledge,
                tasks,
                completed_ids,
                resumed_completed_ids,
                skipped,
                all_diffs,
                elapsed,
                "failure",
                failed_task_id,
            )

            _explicit_session_tokens = int(args.session_tokens)
            if _explicit_session_tokens > 0:
                token_tracker.record_session_tokens(_explicit_session_tokens, is_estimate=False)
            else:
                _estimated = estimate_session_tokens(
                    idea_file,
                    Path(args.plan_file).resolve() if args.plan_file else None,
                    repo_root,
                    tasks,
                )
                token_tracker.record_session_tokens(_estimated, is_estimate=True)

            failure_token_report = token_tracker.build_session_report(
                goal=config.goal,
                repo_root=repo_root,
                supervisor_command=config.supervisor_command,
                tasks_executed=len(completed_ids) - skipped,
                tasks_skipped=skipped,
                elapsed_seconds=elapsed,
            )
            try:
                save_session_to_log(failure_token_report, repo_root / "bridge_progress" / "token_log.json")
            except Exception as token_ex:
                logger.warning("Could not save token log after failure: %s", token_ex)

        print(json.dumps(failure_summary))
        return 1


if __name__ == "__main__":
    sys.exit(main())
