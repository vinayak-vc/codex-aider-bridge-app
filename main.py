from __future__ import annotations

import argparse
import json
import logging
import os
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
from utils.token_tracker import TokenTracker, save_session_to_log
from validator.validator import MechanicalValidator


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
    # --supervisor-command is intentionally NOT a CLI argument.
    # Set BRIDGE_SUPERVISOR_COMMAND in your environment once (e.g. in .env or
    # system env) so any AI can run the bridge without knowing its own CLI name.
    # Default: "claude" (Claude CLI).
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
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity.",
    )
    return parser


def load_plan_from_file(plan_file: Path, parser: TaskParser) -> list[Task]:
    raw = plan_file.read_text(encoding="utf-8")
    return parser.parse(raw)


def obtain_plan(
    config: BridgeConfig,
    supervisor: SupervisorAgent,
    task_parser: TaskParser,
    repo_tree: str,
    logger: logging.Logger,
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
                config.goal, repo_tree, config.idea_text, feedback
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


def execute_task_with_review(
    task: Task,
    config: BridgeConfig,
    supervisor: Optional[SupervisorAgent],
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

    In supervised mode (supervisor provided and not auto_approve):
      Steps 1-3 as above, then:
      4. SupervisorAgent reviews the diff → PASS or REWORK with new instruction.
    """
    selected_files = selector.select(task.files)
    current_instruction = task.instruction

    for attempt in range(config.max_task_retries + 1):
        current_task = Task(
            id=task.id,
            files=task.files,
            instruction=current_instruction,
            type=task.type,
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

        # ── Step 3: Mechanical checks (syntax, existence, CI gate) ───────────
        validation_result = validator.validate(current_task, selected_files.all_paths)
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

    # Auto-approve when no external supervisor is configured.
    # This is the default mode when running the bridge from an interactive
    # AI session (Claude Code / Codex / etc.) that reviews diffs directly.
    _supervisor_cmd = os.getenv("BRIDGE_SUPERVISOR_COMMAND", "")
    _auto_approve = bool(args.auto_approve) or (not _supervisor_cmd and bool(args.plan_file))
    if _auto_approve:
        logger.info(
            "Bridge running in AUTO-APPROVE mode — "
            "mechanical validation only, no external supervisor AI."
        )

    config = BridgeConfig(
        goal=args.goal,
        repo_root=repo_root,
        dry_run=bool(args.dry_run),
        max_plan_attempts=int(args.max_plan_attempts),
        max_task_retries=int(args.max_task_retries),
        validation_command=args.validation_command,
        supervisor_command=_supervisor_cmd or "claude",
        aider_command=args.aider_command,
        aider_model=args.aider_model or None,
        idea_file=idea_file,
        idea_text=idea_text,
        plan_output_file=plan_output_file,
        task_timeout_seconds=int(args.task_timeout),
        aider_no_map=bool(args.aider_no_map),
        auto_approve=_auto_approve,
    )

    run_preflight_checks(config, logger)
    rollback_sha = record_rollback_point(repo_root, logger)
    run_start = time.monotonic()

    token_tracker = TokenTracker()
    repo_tree = RepoScanner(repo_root).scan()
    task_parser = TaskParser()
    selector = FileSelector(repo_root)

    # Only create supervisor agent when needed (not in auto-approve mode).
    supervisor: Optional[SupervisorAgent] = None
    if not _auto_approve:
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

    try:
        if args.plan_file:
            tasks: list[Task] = load_plan_from_file(Path(args.plan_file).resolve(), task_parser)
            logger.info("Loaded %s task(s) from plan file", len(tasks))
        else:
            if supervisor is None:
                raise RuntimeError(
                    "No plan file provided and no supervisor configured. "
                    "Either pass --plan-file or set BRIDGE_SUPERVISOR_COMMAND."
                )
            tasks = obtain_plan(config, supervisor, task_parser, repo_tree, logger)

        # Feature 6: plan preview + confirmation before any Aider task runs.
        if args.confirm_plan and not args.dry_run:
            if not show_plan_preview(tasks, logger):
                return 0

        completed_ids = load_checkpoint(repo_root)
        completed_summaries: list[str] = []
        all_diffs: list[dict] = []  # collected for final review summary
        skipped = 0

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
                task, config, supervisor, selector,
                runner, diff_collector, validator, logger,
                aider_context=aider_context,
            )
            completed_ids.add(task.id)
            save_checkpoint(repo_root, completed_ids)
            task_summary = (
                f"[{task.id}] {task.type} {', '.join(task.files[:2])}"
                + (" ..." if len(task.files) > 2 else "")
            )
            completed_summaries.append(task_summary)
            all_diffs.append({"task_id": task.id, "summary": task_summary, "diff": task_diff or ""})

        clear_checkpoint(repo_root)
        elapsed = round(time.monotonic() - run_start, 1)
        executed = len(tasks) - skipped

        # In auto-approve mode: print a diff review summary so the AI session
        # (Claude Code / human) can review all changes in one place.
        if _auto_approve and all_diffs:
            _emit_structured({"type": "review_summary", "tasks": all_diffs})

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

        print(json.dumps(summary))
        logger.info(
            "Bridge run completed — %s task(s) executed, %s skipped, %.1fs total",
            executed, skipped, elapsed,
        )
        return 0

    except Exception as ex:
        elapsed = round(time.monotonic() - run_start, 1)
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
        print(json.dumps(failure_summary))
        return 1


if __name__ == "__main__":
    sys.exit(main())
