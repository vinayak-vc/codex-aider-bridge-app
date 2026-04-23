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
from pathlib import PurePosixPath
from typing import Optional

from bridge_logging.logger import configure_logging
from context.file_selector import FileSelector
from context.idea_loader import IdeaLoader
from context.project_context_service import ProjectContextService
from context.project_understanding import ensure_project_understanding, understanding_file_path
from executor.aider_runner import AiderRunner
from executor.diff_collector import DiffCollector
from models.task import AiderContext, BridgeConfig, ExecutionResult, SubTask, Task, TaskReport
from parser.task_parser import PlanParseError, TaskParser
from supervisor.agent import SupervisorAgent, SupervisorError
from utils.checkpoint import clear_checkpoint, load_checkpoint, save_checkpoint
from utils.manual_supervisor import ManualSupervisorError, ManualSupervisorSession
from utils.token_tracker import TokenTracker, save_session_to_log
from utils.report_generator import generate_run_report
from utils.run_diagnostics import RunDiagnostics
from utils.project_knowledge import (
    load_knowledge,
    save_knowledge,
    update_knowledge_from_run,
)
from utils.project_type_prompt import PROJECT_TYPES, describe, prompt_project_type
from validator.validator import MechanicalValidator
import memory.memory_client as memory_client


def _is_allowed_empty_task_file(relative_path: str) -> bool:
    rel = PurePosixPath(relative_path.replace("\\", "/"))
    return (
        rel.match("**/__init__.py")
        or rel.match("**/.gitkeep")
        or rel.match("**/.keep")
    )


def _write_json_file(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _build_task_metrics(
    tasks: list[Task],
    completed_ids: set[int],
    resumed_completed_ids: set[int],
    task_commit_shas: dict[int, str],
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
                "commit_sha": task_commit_shas.get(task.id),
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
    task_commit_shas: dict[int, str],
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
            task_commit_shas,
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
        default=10,
        help="Maximum retry attempts per task (escalating strategy: 1-3 standard, 4-6 simplified, 7 diagnostic, 8-9 informed, 10 takeover).",
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
        "--relay-session-id",
        default=None,
        help="Optional AI Relay session id used to isolate manual-supervisor artefacts for one imported plan.",
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
        default=600,
        help="Max seconds any single subprocess call (Aider or supervisor) may run before being killed. Default: 600.",
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
        "--model-lock",
        action="store_true",
        help="Lock the model to --aider-model for all tasks. Disables smart model routing.",
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
            "Recommended: 3 when using small local models (e.g. ollama/qwen2.5-coder:7b). "
            "14B models (e.g. ollama/qwen2.5-coder:14b) handle multi-file tasks well without splitting."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Resume a previous run using the saved plan in bridge_progress/improvement_plan.json. "
            "Tasks already marked complete in bridge_progress/task_metrics.json are skipped. "
            "Equivalent to --plan-file bridge_progress/improvement_plan.json --auto-approve."
        ),
    )
    parser.add_argument(
        "--project-type",
        default=None,
        metavar="TYPE",
        help=(
            "Declare the project type up-front so the bridge picks the right validator, "
            "file filters, and plan hints from the start. "
            "Common values: unity, godot, unreal, python, typescript, javascript, csharp, "
            "flutter, rust, go, other. "
            "If omitted and the type is not already saved in project_knowledge.json, "
            "the bridge will ask interactively (when running in a terminal)."
        ),
    )
    return parser


# ── Planning — delegated to planning/plan_manager.py ─────────────────────────
from planning.plan_manager import (
    load_plan_from_file,
    auto_split_tasks,
    get_installed_models_for_routing as _get_installed_models_for_routing,
    build_model_roster_text as _build_model_roster_text,
    build_feature_manifest as _build_feature_manifest,
    obtain_plan,
    show_plan_preview,
    enforce_workflow_profile as _enforce_workflow_profile,
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
_PYTHON_RUNTIME_SUFFIXES: tuple[str, ...] = (
    ".pyc",
    ".pyo",
)
# Unity auto-generates a .meta file for every new asset file and every new
# directory. These are never task outputs; treat them as expected side-effects.
_UNITY_META_SUFFIX: str = ".meta"


def _is_ignorable_runtime_artifact(relative_path: str) -> bool:
    normalized = relative_path.replace("\\", "/")
    path_parts = normalized.split("/")
    if "__pycache__" in path_parts:
        return True
    if normalized.endswith(_PYTHON_RUNTIME_SUFFIXES):
        return True
    return False


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
        if _is_ignorable_runtime_artifact(relative_path):
            continue
        snapshot.add(relative_path)
    return snapshot


def _find_unexpected_files(
    before_snapshot: set[str],
    after_snapshot: set[str],
    task: Task,
    is_unity_project: bool = False,
) -> list[str]:
    allowed = {Path(file_path).as_posix() for file_path in task.files}
    allowed.update(Path(file_path).as_posix() for file_path in task.must_exist)
    new_files = (after_snapshot - before_snapshot) - allowed

    if is_unity_project:
        # Unity auto-generates .meta files for every new asset and every new
        # directory. Filter them out — they are expected side-effects, not scope
        # violations.  Example: creating Foo.cs also creates Foo.cs.meta and
        # potentially Assets/Scripts.meta if that directory was new.
        new_files = {f for f in new_files if not f.endswith(_UNITY_META_SUFFIX)}

    new_files = {f for f in new_files if not _is_ignorable_runtime_artifact(f)}
    return sorted(new_files)


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
    diagnostics: Optional[RunDiagnostics] = None,
    token_tracker: Optional[TokenTracker] = None,
    model_override: Optional[str] = None,
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
    # Guard: prevent the model from pulling in extra context files and
    # concluding the task is already done because related files were recently
    # modified by a previous task in the same session.
    _guard = (
        "\n\nIMPORTANT: Do NOT request or read any other files beyond the one(s) "
        "already in the chat. Only edit the file(s) listed for this task. "
        "Ignore what other files in the repo may already contain — your job is "
        "solely to apply the changes described above to the specified file(s)."
    )
    current_instruction = current_instruction + _guard
    current_instruction = memory_client.enhance_prompt(current_instruction)

    if manual_supervisor is not None:
        resumed_diff = manual_supervisor.try_resume_completed_task(
            task.id,
            current_instruction,
            task.files,
            selected_files.all_paths,
        )
        if resumed_diff is not None:
            logger.info("Task %s: resumed from completed manual review receipt", task.id)
            _emit_structured(
                {
                    "type": "task_complete",
                    "task_id": task.id,
                    "diff": resumed_diff[:1500],
                    "resumed": True,
                }
            )
            return resumed_diff

    _failure_reasons: list[str] = []  # Accumulate failure reasons for escalation
    _escalation_log: list[dict] = []  # Structured log for diagnostics/telemetry
    _previous_rework_instructions: list[str] = []  # Track rework instructions for dedup
    _retry_budget_seconds = 0.0  # Track cumulative time for retry budget
    _MAX_RETRY_SECONDS = 1800  # 30 minute budget per task

    for attempt in range(config.max_task_retries + 1):

        # Retry budget check
        if attempt > 0 and _retry_budget_seconds > _MAX_RETRY_SECONDS:
            logger.warning(
                "Task %s: retry budget exhausted (%.0fs / %ds). Failing early.",
                task.id, _retry_budget_seconds, _MAX_RETRY_SECONDS,
            )
            _escalation_log.append({"attempt": attempt + 1, "stage": "budget_exhausted", "seconds": round(_retry_budget_seconds)})
            break

        # ── Escalating retry strategy ────────────────────────────────────
        # Attempts 1-3:  Original instruction (standard retries)
        # Attempts 4-6:  Simplified instruction with failure context
        # Attempt 7:     Supervisor diagnostic — analyzes all failures
        # Attempts 8-9:  Diagnostic-informed instruction
        # Attempt 10:    Supervisor takeover prompt (user chooses)
        if attempt == 3 and _failure_reasons:
            _escalation_log.append({"attempt": attempt + 1, "stage": "escalate_simplify", "failure_count": len(_failure_reasons)})
            logger.info(
                "Task %s: escalating — simplifying instruction after %d failures",
                task.id, attempt,
            )
            _emit_structured({"type": "log", "line": f"[escalation] Task {task.id}: simplifying instruction after {attempt} failures"})
            current_instruction = (
                f"SIMPLIFIED INSTRUCTION (previous {attempt} attempts failed):\n"
                f"{task.instruction}\n\n"
                f"PREVIOUS FAILURE REASONS:\n" +
                "\n".join(f"- {r}" for r in _failure_reasons[-3:]) +
                "\n\nFocus on the core requirement. Keep it simple. Fix the issues above."
            )

        if attempt == 6 and supervisor is not None and _failure_reasons:
            _escalation_log.append({"attempt": attempt + 1, "stage": "escalate_diagnostic", "failure_count": len(_failure_reasons)})
            logger.info(
                "Task %s: escalating — requesting supervisor diagnostic after %d failures",
                task.id, attempt,
            )
            _emit_structured({"type": "log", "line": f"[escalation] Task {task.id}: supervisor analyzing {len(_failure_reasons)} failures"})
            try:
                diag_prompt = (
                    f"A developer tool (Aider) has failed to complete this task {attempt} times.\n\n"
                    f"TASK: {task.instruction}\n"
                    f"FILES: {', '.join(task.files)}\n\n"
                    f"FAILURE REASONS:\n" +
                    "\n".join(f"  {i+1}. {r}" for i, r in enumerate(_failure_reasons)) +
                    "\n\nAnalyze WHY the tool keeps failing and provide a REWRITTEN instruction "
                    "that avoids all the issues above. Be extremely specific and concrete."
                )
                diag_response = supervisor._run(diag_prompt)
                current_instruction = diag_response.strip()
                logger.info("Task %s: supervisor rewrote instruction for attempts 8-9", task.id)
            except Exception as diag_ex:
                logger.warning("Task %s: supervisor diagnostic failed: %s", task.id, diag_ex)

        if attempt == 9 and _failure_reasons:
            logger.warning(
                "Task %s: FINAL ATTEMPT — Aider failed %d times. Supervisor takeover available.",
                task.id, attempt,
            )
            _emit_structured({
                "type": "escalation_takeover",
                "task_id": task.id,
                "attempt": attempt + 1,
                "failure_count": len(_failure_reasons),
                "reasons": _failure_reasons[-5:],
                "message": (
                    f"Aider failed {attempt} times on task {task.id}. "
                    "The supervisor can attempt this task directly (higher token cost). "
                    "Check the log for failure reasons."
                ),
            })

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

        if manual_supervisor is not None:
            existing_review = manual_supervisor.consume_existing_decision(current_task)
            if existing_review is not None:
                review, request_payload = existing_review
                request_diff = str(request_payload.get("diff", ""))
                if review.verdict == "PASS":
                    manual_supervisor.record_completed_review(
                        current_task.id,
                        current_task.instruction,
                        current_task.files,
                        selected_files.all_paths,
                        request_diff,
                    )
                    logger.info("Task %s: reused existing manual PASS decision", current_task.id)
                    _emit_structured(
                        {
                            "type": "task_complete",
                            "task_id": current_task.id,
                            "diff": request_diff[:1500],
                            "resumed": True,
                        }
                    )
                    return request_diff
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
                logger.info(
                    "Task %s: reused existing manual REWORK decision on rerun",
                    current_task.id,
                )
                _rework_instr = review.new_instruction or current_instruction
                if _rework_instr in _previous_rework_instructions:
                    _rework_instr = (
                        f"{_rework_instr}\n\n"
                        "NOTE: This exact instruction was tried before and failed. "
                        f"Previous failure: {_failure_reasons[-1] if _failure_reasons else 'unknown'}. "
                        "Try a DIFFERENT approach."
                    )
                _previous_rework_instructions.append(review.new_instruction or "")
                current_instruction = _rework_instr
                wait_seconds = min(2 ** attempt, 30)
                time.sleep(wait_seconds)
                continue

        if config.dry_run:
            logger.info("[dry-run] Task %s: %s", current_task.id, current_instruction)
            return

        # ── Read/Investigate task: skip Aider, send file content to supervisor ─
        if current_task.type in ("read", "investigate"):
            if diagnostics:
                diagnostics.record_task_start(current_task.id, current_instruction, current_task.files, current_task.type)
            logger.info("Task %s: read-only — reading files without Aider", current_task.id)

            file_contents = []
            for fp in current_task.files:
                abs_fp = config.repo_root / fp
                if abs_fp.exists():
                    try:
                        text = abs_fp.read_text(encoding="utf-8", errors="replace")
                        file_contents.append(f"=== {fp} ===\n{text}")
                    except Exception as read_ex:
                        file_contents.append(f"=== {fp} === (error reading: {read_ex})")
                else:
                    file_contents.append(f"=== {fp} === (file not found)")

            # For investigate tasks: also scan related files (imports, references)
            if current_task.type == "investigate":
                import re as _re
                _seen = set(current_task.files)
                for fp in list(current_task.files):
                    abs_fp = config.repo_root / fp
                    if not abs_fp.exists():
                        continue
                    try:
                        source = abs_fp.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        continue
                    # Extract imports/requires
                    related = set()
                    for m in _re.finditer(r'(?:from|import)\s+([.\w]+)', source):
                        mod = m.group(1).replace(".", "/") + ".py"
                        if (config.repo_root / mod).exists():
                            related.add(mod)
                    for m in _re.finditer(r'require\(["\']([^"\']+)["\']\)', source):
                        req = m.group(1)
                        for ext in ("", ".js", ".ts", ".jsx", ".tsx"):
                            if (config.repo_root / (req + ext)).exists():
                                related.add(req + ext)
                                break
                    # Add related files (up to 5 extra)
                    for rel in sorted(related)[:5]:
                        if rel not in _seen:
                            _seen.add(rel)
                            rel_fp = config.repo_root / rel
                            try:
                                text = rel_fp.read_text(encoding="utf-8", errors="replace")
                                file_contents.append(f"=== {rel} (related) ===\n{text[:3000]}")
                            except Exception:
                                pass
                logger.info("Task %s: investigate — read %d files (including related)", current_task.id, len(file_contents))

            combined_content = "\n\n".join(file_contents)

            # Build analysis prompt for the supervisor
            analysis_prompt = (
                "You are analyzing project files. Read the content below and answer the question.\n\n"
                f"QUESTION: {current_instruction}\n\n"
                f"FILE CONTENT:\n{combined_content[:8000]}\n\n"
                "Provide a clear, structured answer. No code changes needed."
            )

            analysis_result = ""
            if manual_supervisor is not None:
                # In manual-supervisor mode, write the analysis as a review request
                # so the proxy thread or user can see it
                fake_result = ExecutionResult(
                    task_id=current_task.id, succeeded=True, exit_code=0,
                    stdout=combined_content[:2000], stderr="",
                    command=[], attempt_number=attempt + 1,
                )
                task_report = TaskReport(task=current_task, execution_result=fake_result, diff=combined_content[:10000])
                request_path = manual_supervisor.submit_review_request(
                    task_report, validation_message="Read-only analysis task — review the file content.",
                    unexpected_files=[],
                )
                _emit_structured({
                    "type": "review_required",
                    "task_id": current_task.id,
                    "request_file": str(request_path),
                    "validation_message": "Read-only analysis task",
                    "mode": "manual",
                })
                review = manual_supervisor.wait_for_decision(current_task.id)
                if review.verdict == "PASS":
                    logger.info("Task %s: read-only analysis approved", current_task.id)
                    if diagnostics:
                        diagnostics.record_review(current_task.id, attempt + 1, "pass")
                    _emit_structured({"type": "task_complete", "task_id": current_task.id, "diff": ""})
                    return ""
                logger.info("Task %s: read-only task reviewed: %s", current_task.id, review.verdict)

            elif supervisor is not None:
                # CLI supervisor mode — ask supervisor to analyze directly
                try:
                    response = supervisor._run(analysis_prompt)
                    analysis_result = response.strip()
                    logger.info("Task %s: supervisor analysis:\n%s", current_task.id, analysis_result[:500])
                except Exception as sup_ex:
                    logger.warning("Task %s: supervisor analysis failed: %s", current_task.id, sup_ex)

            if diagnostics:
                diagnostics.record_review(current_task.id, attempt + 1, "pass")
            _emit_structured({"type": "task_complete", "task_id": current_task.id, "diff": analysis_result[:1500]})
            return analysis_result

        # ── Pre-check: validate target files exist for non-create tasks ─────
        if current_task.type not in ("create",):
            missing = [f for f in current_task.files if not (config.repo_root / f).exists()]
            if missing and len(missing) == len(current_task.files):
                if current_task.type == "modify":
                    logger.warning(
                        "Task %s: Auto-converting 'modify' to 'create' because target files do not exist: %s",
                        current_task.id, ", ".join(missing)
                    )
                    # Use a modified copy to avoid polluting the underlying plan if persisted
                    current_task.type = "create"
                else:
                    # ALL target files are missing — this task will definitely fail
                    raise RuntimeError(
                        f"Task {current_task.id} ({current_task.type}): none of the target files exist: "
                        f"{', '.join(missing)}. Check the file paths in your plan — "
                        f"the file may have a different name or location."
                    )
            elif missing:
                logger.warning(
                    "Task %s (%s): some target files do not exist: %s",
                    current_task.id, current_task.type, ", ".join(missing),
                )

        # ── Step 0.5: Task IR — validate + try deterministic execution ───────
        _deterministic_result = None
        try:
            from executor.task_ir import task_to_ir, validate_task_ir, TaskIRValidationError
            from executor.deterministic_executor import execute_deterministic

            task_ir = task_to_ir(current_task, config.repo_root)
            try:
                ir_warnings = validate_task_ir(task_ir, config.repo_root)
                for w in ir_warnings:
                    logger.debug("Task %s IR warning: %s", current_task.id, w)
            except TaskIRValidationError as val_err:
                logger.warning("Task %s IR validation failed: %s", current_task.id, val_err)

            # Try deterministic execution first (zero LLM tokens)
            if task_ir.can_execute_deterministically:
                logger.info(
                    "Task %s: attempting deterministic execution (%d operations)",
                    current_task.id, len(task_ir.operations),
                )
                _deterministic_result = execute_deterministic(task_ir, config.repo_root)
                if _deterministic_result and _deterministic_result.succeeded:
                    logger.info("Task %s: deterministic execution succeeded — 0 LLM tokens", current_task.id)
                    if token_tracker:
                        token_tracker.record_aider_task(
                            task_id=current_task.id,
                            instruction=current_instruction,
                            input_file_chars=0,
                            diff_chars=0,
                            performer="deterministic",
                        )
        except Exception as ir_err:
            logger.debug("Task %s: IR processing failed (non-fatal): %s", current_task.id, ir_err)

        # ── Step 1: Execute via Aider (if deterministic didn't handle it) ────
        if _deterministic_result and _deterministic_result.succeeded:
            execution_result = _deterministic_result
        else:
            if diagnostics:
                diagnostics.record_task_start(current_task.id, current_instruction, current_task.files, current_task.type)
            execution_result = runner.run(
                current_task, selected_files.all_paths, aider_context,
                model_override=model_override,
            )

        if execution_result.exit_code == -1:
            stderr = execution_result.stderr
            # Aider couldn't start at all (not found, permissions, etc.)
            # — but timeouts and stalls are now handled by the runner's auto-retry,
            # so only raise immediately for non-recoverable launch failures.
            if "timed out" not in stderr.lower() and "stalled" not in stderr.lower():
                raise RuntimeError(
                    f"Task {current_task.id}: Aider could not start. {stderr}"
                )

        _retry_budget_seconds += execution_result.duration_seconds

        if not execution_result.succeeded:
            _fail_msg = f"Aider exit code {execution_result.exit_code}"
            if execution_result.stderr:
                _fail_msg += f": {execution_result.stderr[:150]}"
            _failure_reasons.append(_fail_msg)
            _escalation_log.append({
                "attempt": attempt + 1,
                "stage": "aider_failure",
                "reason": _fail_msg,
                "exit_code": execution_result.exit_code,
                "duration": execution_result.duration_seconds,
                "stdout_tail": execution_result.stdout[-300:] if execution_result.stdout else "",
            })
            logger.warning(
                "Task %s: Aider failed (exit %s): %s",
                current_task.id,
                execution_result.exit_code,
                _fail_msg[:200],
            )

            # Record failure for diagnostics before retrying
            if diagnostics:
                diagnostics.record_aider_result(
                    task_id=current_task.id,
                    attempt=attempt + 1,
                    exit_code=execution_result.exit_code,
                    succeeded=False,
                    stdout=execution_result.stdout,
                    stderr=execution_result.stderr,
                    duration_seconds=execution_result.duration_seconds,
                )

            # ── Failure feedback classification ───────────────────────────
            _feedback = None
            try:
                from executor.failure_feedback import classify_failure, build_retry_instruction
                _file_changed = execution_result.succeeded  # rough proxy
                _feedback = classify_failure(
                    exit_code=execution_result.exit_code,
                    stdout=execution_result.stdout or "",
                    stderr=execution_result.stderr or "",
                    file_changed=_file_changed,
                    instruction=current_instruction,
                )
                logger.info(
                    "Task %s: failure classified as '%s' — %s",
                    current_task.id, _feedback.failure_type, _feedback.suggested_action,
                )
                _escalation_log[-1]["failure_type"] = _feedback.failure_type
                _escalation_log[-1]["suggested_action"] = _feedback.suggested_action

                # Non-retryable failures (config errors) → stop immediately
                if not _feedback.is_retryable:
                    raise RuntimeError(
                        f"Task {current_task.id}: non-retryable failure ({_feedback.failure_type}): {_feedback.reason}"
                    )

                # Adjust instruction for next retry based on feedback
                current_instruction = build_retry_instruction(
                    current_instruction, _feedback, attempt + 1,
                )
            except ImportError:
                pass  # failure_feedback module not available — continue with existing logic

            # ── Same-error detection ─────────────────────────────────────
            if len(_failure_reasons) >= 2 and _failure_reasons[-1] == _failure_reasons[-2]:
                logger.error(
                    "Task %s: same error repeated %d times — aborting retries: %s",
                    current_task.id, 2, _failure_reasons[-1],
                )
                raise RuntimeError(
                    f"Task {current_task.id}: same error repeated — {_failure_reasons[-1]}"
                )

            if attempt >= config.max_task_retries:
                raise RuntimeError(
                    f"Task {current_task.id} failed after {attempt + 1} attempts: {_fail_msg}"
                )

            # ── Skip review, retry immediately ───────────────────────────
            wait_seconds = min(2 ** attempt, 30)
            logger.info(
                "Task %s: backing off %ss before retry %s (skipping review — no useful output)",
                current_task.id, wait_seconds, attempt + 2,
            )
            time.sleep(wait_seconds)
            continue

        # Fix #4: catch 0-byte output files — Aider may exit 0 but leave files
        # empty if it was killed mid-write (timeout) or hit an encoding crash.
        if current_task.type in {"create", "modify"}:
            empty_files = [
                fp for fp in current_task.files
                if (config.repo_root / fp).exists()
                and (config.repo_root / fp).stat().st_size == 0
                and not _is_allowed_empty_task_file(fp)
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

        # Record Aider execution result for diagnostics
        if diagnostics:
            diagnostics.record_aider_result(
                task_id=current_task.id,
                attempt=attempt + 1,
                exit_code=execution_result.exit_code,
                succeeded=execution_result.succeeded,
                stdout=execution_result.stdout,
                stderr=execution_result.stderr,
                duration_seconds=execution_result.duration_seconds,
            )

        # ── Step 2: Collect diff ─────────────────────────────────────────────
        diff = diff_collector.collect(files=current_task.files)
        logger.debug("Task %s: diff collected (%s chars)", current_task.id, len(diff))

        # Track Aider token usage (estimated from task instruction + file sizes + diff)
        _input_file_chars = 0
        for fp in current_task.files:
            _fp = config.repo_root / fp
            if _fp.exists():
                try:
                    _input_file_chars += _fp.stat().st_size
                except OSError:
                    pass
        if token_tracker:
            token_tracker.record_aider_task(
                task_id=current_task.id,
                instruction=current_instruction,
                input_file_chars=_input_file_chars,
                diff_chars=len(diff),
            )

        repo_after = _snapshot_repo_files(config.repo_root)
        unexpected_files = _find_unexpected_files(
            repo_before, repo_after, current_task,
            is_unity_project=validator.is_unity_project,
        )
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

        # Record validation result for diagnostics
        if diagnostics:
            diagnostics.record_validation(
                current_task.id, attempt + 1,
                validation_result.succeeded, validation_result.message,
            )

        if not validation_result.succeeded:
            _failure_reasons.append(f"Validation: {validation_result.message[:150]}")
            _escalation_log.append({
                "attempt": attempt + 1,
                "stage": "validation_failure",
                "reason": validation_result.message[:200],
            })
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
                    manual_supervisor.record_completed_review(
                        current_task.id,
                        current_task.instruction,
                        current_task.files,
                        selected_files.all_paths,
                        diff,
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

        # ── Empty diff handling ──────────────────────────────────────────────
        # If Aider succeeded (exit 0) but produced an empty diff, this can mean:
        # - The task is already satisfied (idempotent no-op)
        # - Aider failed to apply changes (silent failure)
        #
        # In MANUAL-SUPERVISOR mode we must never loop/abort without giving the
        # supervisor a chance to PASS/REWORK, so we do NOT auto-retry here.
        if execution_result.succeeded and not diff.strip():
            if manual_supervisor is not None:
                logger.info(
                    "Task %s: Aider exited 0 but produced no diff — sending to manual review",
                    current_task.id,
                )
            else:
                # For CREATE tasks, allow idempotent no-op when the target file
                # already exists and is non-empty. This prevents placeholder-backed
                # creates from killing the run.
                if current_task.type == "create":
                    _all_present_and_non_empty = True
                    for p in selected_files.all_paths:
                        try:
                            if not p.exists() or p.stat().st_size == 0:
                                _all_present_and_non_empty = False
                                break
                        except OSError:
                            _all_present_and_non_empty = False
                            break
                    if _all_present_and_non_empty:
                        logger.info(
                            "Task %s: create task produced no diff, but file(s) already exist and are non-empty — treating as satisfied",
                            current_task.id,
                        )
                    else:
                        logger.info(
                            "Task %s: Aider exited 0 but produced no diff — retrying",
                            current_task.id,
                        )
                        _failure_reasons.append("Aider exited 0 but no files changed (empty diff)")
                        _escalation_log.append({
                            "attempt": attempt + 1,
                            "stage": "empty_diff",
                            "reason": "Aider reported success but diff is empty",
                        })
                        if attempt >= config.max_task_retries:
                            raise RuntimeError(
                                f"Task {current_task.id}: Aider reported success but never "
                                f"modified any files after {attempt + 1} attempts"
                            )
                        wait_seconds = min(2 ** attempt, 30)
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
                if diagnostics:
                    diagnostics.record_review(current_task.id, attempt + 1, "pass")
                manual_supervisor.record_completed_review(
                    current_task.id,
                    current_task.instruction,
                    current_task.files,
                    selected_files.all_paths,
                    diff,
                )
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

            _failure_reasons.append(f"Rework: {(review.new_instruction or '')[:150]}")
            _escalation_log.append({"attempt": attempt + 1, "stage": "rework", "reason": (review.new_instruction or "")[:200], "source": "manual"})
            if diagnostics:
                diagnostics.record_review(current_task.id, attempt + 1, "rework", review.new_instruction or "")
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
            _rework_instr = review.new_instruction or current_instruction
            if _rework_instr in _previous_rework_instructions:
                _rework_instr = (
                    f"{_rework_instr}\n\n"
                    "NOTE: This exact instruction was tried before and failed. "
                    f"Previous failure: {_failure_reasons[-1] if _failure_reasons else 'unknown'}. "
                    "Try a DIFFERENT approach."
                )
            _previous_rework_instructions.append(review.new_instruction or "")
            current_instruction = _rework_instr
            continue

        if config.auto_approve or supervisor is None:
            # Auto-approve mode: mechanical pass = done. No external AI call.
            if diagnostics:
                diagnostics.record_review(current_task.id, attempt + 1, "pass")
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
            if diagnostics:
                diagnostics.record_review(current_task.id, attempt + 1, "pass")
            _emit_structured({
                "type": "task_complete",
                "task_id": current_task.id,
                "diff": diff[:1500],
            })
            return diff

        _failure_reasons.append(f"Rework: {(review.new_instruction or '')[:150]}")
        _escalation_log.append({"attempt": attempt + 1, "stage": "rework", "reason": (review.new_instruction or "")[:200], "source": "cli"})
        if diagnostics:
            diagnostics.record_review(current_task.id, attempt + 1, "rework", review.new_instruction or "")
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
        _rework_instr = review.new_instruction or current_instruction  # type: ignore[assignment]
        if _rework_instr in _previous_rework_instructions:
            _rework_instr = (
                f"{_rework_instr}\n\n"
                "NOTE: This exact instruction was tried before and failed. "
                f"Previous failure: {_failure_reasons[-1] if _failure_reasons else 'unknown'}. "
                "Try a DIFFERENT approach."
            )
        _previous_rework_instructions.append(review.new_instruction or "")
        current_instruction = _rework_instr

    # Emit escalation data for telemetry/diagnostics before raising
    if _escalation_log:
        _emit_structured({
            "type": "escalation_report",
            "task_id": task.id,
            "total_attempts": config.max_task_retries + 1,
            "failure_count": len(_failure_reasons),
            "escalation_stages": list({e["stage"] for e in _escalation_log}),
            "log": _escalation_log,
        })
    if diagnostics:
        diagnostics.record_escalation(task.id, _escalation_log)
        diagnostics.record_task_failure(
            task.id,
            f"Exhausted {config.max_task_retries + 1} attempts. "
            f"Escalation stages: {', '.join(set(e.get('stage','?') for e in _escalation_log))}. "
            f"Last reasons: {'; '.join(_failure_reasons[-3:])}"
        )

    reasons_summary = "; ".join(_failure_reasons[-5:]) if _failure_reasons else "unknown"
    raise RuntimeError(
        f"Task {task.id} exhausted all {config.max_task_retries + 1} attempts. "
        f"Failure reasons: {reasons_summary}"
    )


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
    logger = logging.getLogger(__name__)
    try:
        payload = json.dumps({"_bridge_event": True, **data}, ensure_ascii=False)
    except (TypeError, ValueError) as ex:
        logger.warning("Could not serialize structured bridge event: %s", ex)
        return

    _safe_stdout_write(payload, logger)


def _safe_stdout_write(text: str, logger: Optional[logging.Logger] = None) -> bool:
    active_logger = logger or logging.getLogger(__name__)
    try:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
        return True
    except (OSError, UnicodeEncodeError, ValueError) as ex:
        preview = text if len(text) <= 500 else text[:500] + "...<truncated>"
        active_logger.warning("Could not write bridge output to stdout: %s | payload=%s", ex, preview)
        return False


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
            creationflags=_WIN_NO_WINDOW,
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


# Model size patterns that indicate a small local model (≤ 8 B parameters).
# Matched against the --aider-model value (case-insensitive).
# Examples that match: 7b, 7B, 6.7b, 8b, 3b, 1b, 1.5b
# Examples that do NOT match: 9b, 13b, 14b, 32b, 70b, claude, gpt-4, gemini
# 14B+ models (deepseek-r1:14b, qwen2.5-coder:14b) are capable enough to handle
# multi-file tasks without splitting.
_SMALL_MODEL_RE = re.compile(r"\b([1-8](\.\d+)?)b\b", re.IGNORECASE)
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
      - Reading docs/AGENTIC_AI_ONBOARDING.md (from the bridge directory)
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
    onboarding = bridge_dir / "docs" / "AGENTIC_AI_ONBOARDING.md"
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


_WIN_NO_WINDOW: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


# ── Git operations — delegated to utils/git_manager.py ───────────────────────
from utils.git_manager import (
    run_git_command as _run_git_command,
    is_git_repository as _is_git_repository,
    has_git_head as _has_git_head,
    get_git_branch_name as _get_git_branch_name,
    collect_git_readiness as _collect_git_readiness,
    log_git_readiness_preview as _log_git_readiness_preview,
    prompt_for_git_repo_creation as _prompt_for_git_repo_creation,
    ensure_git_repository_exists as _ensure_git_repository_exists,
    ensure_git_baseline_commit as _ensure_git_baseline_commit,
    auto_commit_task_changes as _auto_commit_task_changes,
)






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
    _log_git_readiness_preview(config.repo_root, logger)
    _ensure_git_repository_exists(config.repo_root, logger)

    logger.debug("Pre-flight: checking disk space at %s", config.repo_root)
    free_bytes = shutil.disk_usage(config.repo_root).free
    if free_bytes < 50 * 1024 * 1024:
        raise RuntimeError(
            f"Insufficient disk space at {config.repo_root}: "
            f"less than 50 MB free ({free_bytes // (1024 * 1024)} MB available)."
        )

    # Ensure all bridge-generated artefacts are in the target repo's .gitignore.
    # We append only the entries that are not already present, so repeated runs
    # are idempotent and existing .gitignore content is never disturbed.
    _BRIDGE_GITIGNORE_ENTRIES: list[tuple[str, str]] = [
        ("taskJsons/",                  "AI-generated task plan JSON files"),
        (".aider.tags.cache.v4/",       "Aider repo-map tag cache"),
        ("bridge_progress/",            "Bridge run state, checkpoints and reports"),
        ("logs.meta",                   "Unity meta for bridge logs folder"),
        (".aider.input.history",        "Aider interactive input history"),
        (".aider.chat.history.md",      "Aider chat history"),
        ("bridge_progress.meta",        "Unity meta for bridge_progress folder"),
    ]
    _gitignore_path = config.repo_root / ".gitignore"
    try:
        existing_text = _gitignore_path.read_text(encoding="utf-8") if _gitignore_path.exists() else ""
        existing_lines = set(existing_text.splitlines())
        missing = [
            (entry, comment)
            for entry, comment in _BRIDGE_GITIGNORE_ENTRIES
            if entry not in existing_lines
        ]
        if missing:
            with _gitignore_path.open("a", encoding="utf-8") as _gf:
                _gf.write("\n# ── Aider / Bridge artefacts ─────────────────────────────────────\n")
                for entry, comment in missing:
                    _gf.write(f"{entry:<40} # {comment}\n")
            added = [e for e, _ in missing]
            logger.info("Added %d bridge entries to %s: %s", len(added), _gitignore_path, added)
        else:
            logger.debug(".gitignore already contains all bridge entries — nothing to add.")
    except OSError as _ge:
        logger.warning("Could not update .gitignore: %s (non-fatal)", _ge)

    _ensure_git_baseline_commit(config.repo_root, logger)
    _log_git_readiness_preview(config.repo_root, logger)
    logger.info("Pre-flight checks passed.")


def _fix_windows_encoding() -> None:
    """Reconfigure stdout/stderr to UTF-8 on Windows.

    The default Windows console codec (cp1252 / cp850) cannot encode box-drawing
    characters, emoji, or many other Unicode symbols used in bridge output.
    Reconfiguring to UTF-8 with errors='replace' means any unencodable character
    becomes '?' instead of raising UnicodeEncodeError.

    Safe to call on all platforms — reconfigure() is a no-op when the stream
    already uses UTF-8 or when it is not a real text stream (e.g. subprocess pipe).
    """
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass


def main() -> int:
    _fix_windows_encoding()
    arg_parser = build_argument_parser()
    args = arg_parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    logger = configure_logging(repo_root / "logs", args.log_level)
    logger.info("Bridge starting — repo: %s", repo_root)

    # --resume: shorthand for --plan-file <saved plan> --auto-approve
    if args.resume:
        _saved_plan = repo_root / "bridge_progress" / "improvement_plan.json"
        if not _saved_plan.exists():
            logger.error(
                "--resume requires bridge_progress/improvement_plan.json to exist. "
                "Run without --resume first to generate a plan."
            )
            sys.exit(1)
        if not args.plan_file:
            args.plan_file = str(_saved_plan)
            logger.info("--resume: using saved plan %s", _saved_plan)
        args.auto_approve = True
        logger.info("--resume: auto-approve enabled, skipping already-completed tasks via checkpoint")

    # Warn early if no aider model is set — prevents silently falling back to
    # Aider's default (GPT-4o) which requires an OpenAI API key.
    if not args.aider_model and not os.getenv("BRIDGE_AIDER_MODEL"):
        logger.warning(
            "No --aider-model set and BRIDGE_AIDER_MODEL env var is not set. "
            "Aider will use its default model (usually GPT-4o) which requires an OpenAI API key. "
            "For local/free inference use e.g. --aider-model ollama/qwen2.5-coder:14b"
        )

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
            "bridge_progress/manual_supervisor/ and the UI proxy thread handles supervisor dispatch."
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
        relay_session_id=str(args.relay_session_id).strip() if args.relay_session_id else None,
        auto_commit=True,
    )

    run_preflight_checks(config, logger)
    rollback_sha = record_rollback_point(repo_root, logger)
    run_start = time.monotonic()

    token_tracker = TokenTracker()
    diagnostics = RunDiagnostics(
        goal=config.goal,
        aider_model=config.aider_model or "",
        supervisor=config.supervisor_command or "manual",
        task_timeout=config.task_timeout_seconds,
    )
    task_parser = TaskParser()
    selector = FileSelector(repo_root)

    # Load project knowledge so every AI session starts with full project context.
    knowledge = load_knowledge(repo_root)

    # ── Project-type selection ────────────────────────────────────────────────
    # Priority: CLI flag > saved in knowledge > interactive prompt > auto-detect.
    _saved_type: str = knowledge.get("project", {}).get("type", "")
    _cli_type: Optional[str] = getattr(args, "project_type", None)

    if _cli_type:
        # Validate the CLI value against the known catalogue.
        if _cli_type not in PROJECT_TYPES:
            logger.warning(
                "--project-type '%s' is not a recognised type. "
                "Known types: %s. Falling back to auto-detect.",
                _cli_type, ", ".join(PROJECT_TYPES),
            )
            _cli_type = None
        else:
            logger.info("Project type set via CLI: %s", describe(_cli_type))
            knowledge["project"]["type"] = _cli_type
            save_knowledge(knowledge, repo_root)

    if not _cli_type and not _saved_type:
        # No type known yet — ask interactively (terminal only).
        _prompted_type = prompt_project_type()
        if _prompted_type and _prompted_type != "other":
            knowledge["project"]["type"] = _prompted_type
            save_knowledge(knowledge, repo_root)
            logger.info("Project type saved: %s", describe(_prompted_type))
        elif _prompted_type == "other":
            logger.info("Project type: auto-detect (user selected 'other')")
        # If None (non-interactive), the validator will auto-detect from file markers.

    elif _saved_type and not _cli_type:
        logger.info("Project type (from saved knowledge): %s", describe(_saved_type))

    # Project understanding bootstrap: runs once on first use.
    # Discovers docs, infers file roles via static scan (OnboardingScanner),
    # writes AI_UNDERSTANDING.md, and optionally asks user to confirm/clarify.
    try:
        knowledge = ensure_project_understanding(
            repo_root,
            knowledge,
            logger,
            skip_source_scan=bool(config.skip_onboarding_scan),
            allow_user_confirm=bool(sys.stdin.isatty()),
        )
        _emit_structured(
            {
                "type": "understanding_ready",
                "understanding_file": str(understanding_file_path(repo_root)),
                "confirmed": bool(
                    knowledge.get("project", {}).get("understanding_confirmed", False)
                ),
                "doc_count": len(knowledge.get("docs", [])),
                "file_count": len(knowledge.get("files", {})),
            }
        )
        logger.info(
            "Project understanding ready: %d docs, %d files, confirmed=%s",
            len(knowledge.get("docs", [])),
            len(knowledge.get("files", {})),
            knowledge.get("project", {}).get("understanding_confirmed", False),
        )
    except Exception as _understanding_err:
        logger.warning(
            "Project understanding bootstrap failed (continuing without blocking run): %s",
            _understanding_err,
        )

    project_context = ProjectContextService(repo_root).load_for_planner()
    if knowledge.get("files"):
        logger.info(
            "Project knowledge loaded: %d files registered, %d features done",
            len(knowledge["files"]),
            len(knowledge.get("features_done", [])),
        )
    else:
        logger.info("No project knowledge yet — will create after this run.")

    logger.info(
        "Planner context source: %s%s",
        project_context.source,
        " (graphify available)" if project_context.graphify.available else "",
    )

    # Only create supervisor agent when needed (not in auto-approve mode).
    supervisor: Optional[SupervisorAgent] = None
    manual_supervisor: Optional[ManualSupervisorSession] = None
    if _manual_supervisor_enabled:
        manual_supervisor = ManualSupervisorSession(
            repo_root,
            logger,
            poll_seconds=config.manual_review_poll_seconds,
            session_id=config.relay_session_id,
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
    # Pass the user-declared type so the validator doesn't have to re-detect
    # from file markers (which may miss the correct type for mono-repos etc.)
    _declared_type = knowledge.get("project", {}).get("type", "") or None
    validator = MechanicalValidator(
        repo_root, config.validation_command, logger,
        project_type_override=_declared_type,
    )
    tasks: list[Task] = []
    completed_ids: set[int] = set()
    task_commit_shas: dict[int, str] = {}
    completed_summaries: list[str] = []
    all_diffs: list[dict] = []
    skipped = 0
    failed_task_id: Optional[int] = None
    resumed_completed_ids: set[int] = set()

    # Smart model routing — initialized here so both plan-file and
    # plan-generation paths have access in the task loop.
    _model_lock = getattr(args, "model_lock", False)
    _installed_models = _get_installed_models_for_routing(logger)

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
            # ── Feature manifest: read spec files from folders referenced in goal ─
            feature_specs = _build_feature_manifest(config.goal, repo_root, logger)
            if feature_specs:
                _emit_structured({
                    "type": "bridge_status",
                    "message": "Found feature specs — building manifest for planning",
                })

            # ── Smart model routing: detect installed models for supervisor ──
            _model_lock = args.model_lock
            _installed_models = _get_installed_models_for_routing(logger)
            _model_roster: Optional[str] = None

            if len(_installed_models) <= 1:
                if _installed_models:
                    logger.info("Model routing: only 1 model installed — using %s for all tasks",
                                _installed_models[0]["name"])
                # Suggest installing a second model for routing
                if _installed_models and _installed_models[0].get("speed") == "slow":
                    logger.info(
                        "Tip: install a fast model (e.g. 'ollama pull qwen2.5-coder:7b') "
                        "to enable smart model routing — fast model for simple tasks, "
                        "current model for complex ones"
                    )
                    _emit_structured({
                        "type": "bridge_status",
                        "message": "Tip: install qwen2.5-coder:7b for smart model routing",
                    })
            elif _model_lock:
                logger.info("Model routing: model locked to %s — skipping auto-selection",
                            config.aider_model)
            else:
                _model_roster = _build_model_roster_text(_installed_models)
                logger.info("Model routing: %d models available — supervisor will pick per-task",
                            len(_installed_models))
                _emit_structured({
                    "type": "bridge_status",
                    "message": f"Smart routing: {len(_installed_models)} models available",
                })

            tasks = obtain_plan(
                config, supervisor, task_parser, project_context, logger,
                feature_specs=feature_specs,
                model_roster=_model_roster,
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

        # Compute a hash of the current plan so the checkpoint can detect
        # when a NEW plan is loaded (different tasks, same sequential IDs).
        import hashlib as _hashlib
        _plan_signature = "|".join(
            f"{t.id}:{t.type}:{','.join(t.files)}:{t.instruction[:80]}"
            for t in tasks
        )
        _plan_hash = _hashlib.sha256(_plan_signature.encode()).hexdigest()[:16]
        logger.info("Plan hash: %s (%d tasks)", _plan_hash, len(tasks))

        completed_ids = load_checkpoint(repo_root, expected_plan_hash=_plan_hash)
        resumed_completed_ids = set(completed_ids)

        # Circuit breaker: stop after N consecutive failures with same error category
        _consecutive_failures = 0
        _last_error_category = ""

        # ── Model validation test ────────────────────────────────────────
        # Send a 1-line test prompt through Aider to verify the full
        # pipeline (Ollama running, model loaded, LiteLLM configured).
        # Catches config errors before any real task runs.
        if not args.dry_run:
            logger.info("Running Aider connection test…")
            _emit_structured({"type": "bridge_status", "message": "Testing Aider + LLM connection…"})
            _test_task = Task(
                id=0,
                files=[],
                instruction="Reply with OK. Do not modify any files.",
                type="validate",
            )
            _test_result = runner.run(_test_task, [], None)
            _test_stdout = (_test_result.stdout or "").lower()
            _test_stderr = (_test_result.stderr or "").lower()
            if not _test_result.succeeded or any(
                err in _test_stdout or err in _test_stderr
                for err in ["error", "exception", "traceback", "litellm"]
            ):
                _diag = _test_result.stderr or _test_result.stdout[:300]
                logger.error("Aider connection test FAILED: %s", _diag)
                _emit_structured({
                    "type": "bridge_status",
                    "status": "error",
                    "message": f"Aider connection test failed: {_diag[:200]}",
                })
                raise RuntimeError(
                    f"Aider connection test failed — fix the config before running tasks.\n{_diag}"
                )
            logger.info("Aider connection test passed ✓")
            _emit_structured({"type": "bridge_status", "message": "Aider connection test passed ✓"})

        for task_index, task in enumerate(tasks):
            wait_if_paused(repo_root, logger)

            if task.id in completed_ids:
                # Verify the completed task's files still exist and have content.
                # If a git reset reverted the changes, don't skip — re-run.
                _files_reverted = False
                if task.type == "create":
                    # Create tasks: file should exist if task was done
                    for fp in task.files:
                        if not (repo_root / fp).exists():
                            _files_reverted = True
                            logger.warning(
                                "Task %s: checkpoint says done but %s doesn't exist (reverted?) — will re-run",
                                task.id, fp,
                            )
                            break
                elif task.type == "modify":
                    # Modify tasks: check if file has uncommitted changes vs checkpoint commit
                    try:
                        _diff_check = _run_git_command(repo_root, ["diff", "HEAD", "--name-only", "--"] + list(task.files))
                        if _diff_check.returncode == 0 and not _diff_check.stdout.strip():
                            # No diff = files match HEAD. Check if HEAD actually has the task changes
                            # by looking at the commit message
                            pass  # Can't verify content without storing hash — trust checkpoint
                    except Exception:
                        pass

                if _files_reverted:
                    completed_ids.discard(task.id)
                    save_checkpoint(repo_root, completed_ids, plan_hash=_plan_hash)
                    logger.info("Task %s: removed from checkpoint — will re-run", task.id)
                else:
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

            # ── Per-task model override (smart routing) ────────────────────
            _task_model_override: Optional[str] = None
            if task.model and not _model_lock and len(_installed_models) > 1:
                # Validate the supervisor's model pick exists
                _valid_names = {m["name"] for m in _installed_models}
                if task.model in _valid_names:
                    _task_model_override = task.model
                    logger.info("Task %s: using model %s (supervisor pick)", task.id, task.model)
                else:
                    logger.warning(
                        "Task %s: supervisor picked '%s' but it's not installed — using default",
                        task.id, task.model,
                    )

            # Record pre-task SHA for rollback on failure
            _pre_task_sha = None
            try:
                _r = _run_git_command(repo_root, ["rev-parse", "HEAD"])
                if _r.returncode == 0:
                    _pre_task_sha = _r.stdout.strip()
            except Exception:
                pass

            try:
                task_diff = execute_task_with_review(
                    task, config, supervisor, manual_supervisor, selector,
                    runner, diff_collector, validator, logger,
                    aider_context=aider_context,
                    diagnostics=diagnostics,
                    token_tracker=token_tracker,
                    model_override=_task_model_override,
                )
                _consecutive_failures = 0  # Reset circuit breaker on success
                memory_client.ingest_result(task.instruction, (task_diff or "")[:1000], "aider-bridge")
            except RuntimeError as task_ex:
                # Task-level rollback: revert to pre-task state
                if _pre_task_sha:
                    try:
                        _run_git_command(repo_root, ["reset", "--hard", _pre_task_sha])
                        logger.info("Task %s: rolled back to %s after failure", task.id, _pre_task_sha[:8])
                    except Exception:
                        pass

                # Circuit breaker: classify error and check consecutive count
                err_str = str(task_ex).lower()
                if "timed out" in err_str:
                    _error_cat = "timeout"
                elif "validation" in err_str or "mechanical" in err_str:
                    _error_cat = "validation"
                elif "interactive" in err_str or "prompt" in err_str:
                    _error_cat = "interactive_prompt"
                else:
                    _error_cat = "other"

                if _error_cat == _last_error_category:
                    _consecutive_failures += 1
                else:
                    _consecutive_failures = 1
                    _last_error_category = _error_cat

                if _consecutive_failures >= 3:
                    raise RuntimeError(
                        f"Circuit breaker: {_consecutive_failures} consecutive tasks failed with "
                        f"'{_error_cat}'. Stopping run. Last error: {task_ex}"
                    ) from task_ex

                # Re-raise for normal failure handling
                raise

            failed_task_id = None
            commit_sha = None
            if config.auto_commit:
                commit_sha = _auto_commit_task_changes(repo_root, task, logger)
            else:
                logger.info("Task %s: auto-commit disabled — changes left in working tree", task.id)
            if commit_sha:
                task_commit_shas[task.id] = commit_sha
            completed_ids.add(task.id)
            save_checkpoint(repo_root, completed_ids, plan_hash=_plan_hash)
            task_summary = (
                f"[{task.id}] {task.type} {', '.join(task.files[:2])}"
                + (" ..." if len(task.files) > 2 else "")
            )
            completed_summaries.append(task_summary)
            all_diffs.append(
                {
                    "task_id": task.id,
                    "summary": task_summary,
                    "diff": task_diff or "",
                    "commit_sha": commit_sha,
                }
            )
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
                task_commit_shas,
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

        # Generate Markdown run report
        try:
            generate_run_report(token_report, repo_root)
            logger.info("Run report written to %s", _progress_dir / "RUN_REPORT.md")
        except Exception as _rep_ex:
            logger.warning("Could not generate run report: %s", _rep_ex)

        # Firebase cloud sync — dual destination (user's Firestore + admin metrics)
        try:
            from utils.firebase_user_setup import get_user_setup
            _fbu = get_user_setup()
            if _fbu.is_configured() and _fbu.is_authenticated():
                _project_name = repo_root.name

                # 1. Push to USER's Firestore (full run + token data)
                _run_data = {
                    "status": token_report.get("status", ""),
                    "tasks_planned": token_report.get("aider", {}).get("tasks_executed", 0) + token_report.get("aider", {}).get("tasks_skipped", 0),
                    "tasks_completed": token_report.get("aider", {}).get("tasks_executed", 0),
                    "elapsed_seconds": token_report.get("elapsed_seconds", 0),
                    "supervisor": token_report.get("supervisor_command", ""),
                    "model": config.aider_model or "",
                    "supervisor_tokens": token_report.get("supervisor", {}).get("total", 0),
                    "aider_tokens": token_report.get("aider", {}).get("estimated_tokens", 0),
                    "tokens_saved": token_report.get("savings", {}).get("tokens_saved", 0),
                    "savings_percent": token_report.get("savings", {}).get("savings_percent", 0),
                    "timestamp": token_report.get("timestamp", ""),
                }
                import uuid as _uuid
                _run_id = token_report.get("session_id", str(_uuid.uuid4())[:8])
                try:
                    _fbu.write_to_user_firestore(f"projects/{_project_name}/runs/{_run_id}", _run_data)
                except Exception:
                    pass

                # 2. Push anonymized metrics to ADMIN's Firebase
                try:
                    _fbu.push_admin_metrics({
                        "project_names": [_project_name],
                        "total_tasks": _run_data["tasks_completed"],
                        "total_runs": 1,
                        "total_supervisor_tokens": _run_data["supervisor_tokens"],
                        "total_aider_tokens": _run_data["aider_tokens"],
                        "total_tokens_saved": _run_data["tokens_saved"],
                        "avg_savings_percent": _run_data["savings_percent"],
                    })
                except Exception:
                    pass

                logger.info("Cloud sync: pushed to user's Firestore + admin metrics for %s", _project_name)
        except Exception as _fb_ex:
            logger.debug("Cloud sync skipped: %s", _fb_ex)

        # Write run diagnostics
        try:
            diag_report = diagnostics.finalize(
                "success", list(completed_ids), None, total_tasks=len(tasks),
            )
            diagnostics.write(_progress_dir / "RUN_DIAGNOSTICS.json", diag_report)
            logger.info("Run diagnostics written to %s", _progress_dir / "RUN_DIAGNOSTICS.json")
        except Exception as _diag_ex:
            logger.warning("Could not write run diagnostics: %s", _diag_ex)

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
            task_commit_shas,
            skipped,
            all_diffs,
            elapsed,
            "success",
            None,
        )

        _safe_stdout_write(json.dumps(summary, ensure_ascii=False), logger)
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
                task_commit_shas,
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
                failure_reason=str(ex),
            )
            try:
                save_session_to_log(failure_token_report, repo_root / "bridge_progress" / "token_log.json")
            except Exception as token_ex:
                logger.warning("Could not save token log after failure: %s", token_ex)

            try:
                generate_run_report(failure_token_report, repo_root)
            except Exception:
                pass

            # Write run diagnostics for failure analysis
            try:
                diag_report = diagnostics.finalize(
                    "failure", list(completed_ids), failed_task_id,
                    error_message=str(ex), total_tasks=len(tasks) if tasks else 0,
                )
                diagnostics.write(repo_root / "bridge_progress" / "RUN_DIAGNOSTICS.json", diag_report)
            except Exception:
                pass

        _safe_stdout_write(json.dumps(failure_summary, ensure_ascii=False), logger)
        return 1


if __name__ == "__main__":
    sys.exit(main())
