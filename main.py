from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
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
from models.task import BridgeConfig, Task, TaskReport
from parser.task_parser import PlanParseError, TaskParser
from supervisor.agent import SupervisorAgent, SupervisorError
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
    parser.add_argument(
        "--supervisor-command",
        default=os.getenv(
            "BRIDGE_SUPERVISOR_COMMAND",
            "codex.cmd exec --skip-git-repo-check --color never",
        ),
        help=(
            "Command used to invoke the supervisor agent "
            "(Codex, Claude CLI, or any coding agent). "
            "Supports {prompt} and {output_file} placeholders. "
            "Set to 'interactive' to provide supervisor inputs manually."
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


def execute_task_with_review(
    task: Task,
    config: BridgeConfig,
    supervisor: SupervisorAgent,
    selector: FileSelector,
    runner: AiderRunner,
    diff_collector: DiffCollector,
    validator: MechanicalValidator,
    logger: logging.Logger,
) -> None:
    """Run one task through the full Aider → diff → mechanical check → supervisor review loop.

    Flow per attempt:
      1. Aider executes the current instruction against the task files.
      2. DiffCollector captures what changed in the repo.
      3. MechanicalValidator runs fast token-free checks (file existence, Python syntax, CI gate).
         Mechanical failures reuse the same instruction without spending supervisor tokens.
      4. SupervisorAgent reviews the diff and returns PASS or REWORK.
         REWORK provides a new instruction for the next attempt.
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
        execution_result = runner.run(current_task, selected_files.all_paths)

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

        # ── Step 2: Collect diff ─────────────────────────────────────────────
        diff = diff_collector.collect()
        logger.debug("Task %s: diff collected (%s chars)", current_task.id, len(diff))

        # ── Step 3: Mechanical pre-checks (no supervisor tokens spent) ────────
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
            # Retry with the same instruction — no supervisor call needed
            wait_seconds = min(2 ** attempt, 30)
            logger.debug(
                "Task %s: backing off %ss before retry attempt %s",
                current_task.id, wait_seconds, attempt + 2,
            )
            time.sleep(wait_seconds)
            continue

        # ── Step 4: Supervisor review ─────────────────────────────────────────
        task_report = TaskReport(
            task=current_task,
            execution_result=execution_result,
            diff=diff,
        )
        review = supervisor.review_task(task_report)

        if review.verdict == "PASS":
            logger.info("Task %s: supervisor approved", current_task.id)
            return

        # REWORK — supervisor provides a corrected instruction
        logger.warning(
            "Task %s: supervisor requested rework (attempt %s): %s",
            current_task.id,
            attempt + 1,
            review.new_instruction,
        )

        if attempt >= config.max_task_retries:
            raise RuntimeError(
                f"Task {current_task.id} exhausted rework retries "
                f"after supervisor feedback."
            )

        wait_seconds = min(2 ** attempt, 30)
        logger.debug(
            "Task %s: backing off %ss before rework attempt %s",
            current_task.id, wait_seconds, attempt + 2,
        )
        time.sleep(wait_seconds)
        current_instruction = review.new_instruction  # type: ignore[assignment]

    raise RuntimeError(f"Task {task.id} exhausted all retries.")


def _summarize_process_failure(stderr: str, stdout: str) -> str:
    for stream_name, content in (("stderr", stderr), ("stdout", stdout)):
        normalized = content.strip()
        if normalized:
            return f"{stream_name}: {normalized.splitlines()[0]}"
    return "No process output captured."


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

    config = BridgeConfig(
        goal=args.goal,
        repo_root=repo_root,
        dry_run=bool(args.dry_run),
        max_plan_attempts=int(args.max_plan_attempts),
        max_task_retries=int(args.max_task_retries),
        validation_command=args.validation_command,
        supervisor_command=args.supervisor_command,
        aider_command=args.aider_command,
        aider_model=args.aider_model or None,
        idea_file=idea_file,
        idea_text=idea_text,
        plan_output_file=plan_output_file,
        task_timeout_seconds=int(args.task_timeout),
    )

    run_preflight_checks(config, logger)

    repo_tree = RepoScanner(repo_root).scan()
    task_parser = TaskParser()
    selector = FileSelector(repo_root)
    supervisor = SupervisorAgent(repo_root, config.supervisor_command, logger, timeout=config.task_timeout_seconds)
    runner = AiderRunner(repo_root, config.aider_command, logger, config.aider_model, timeout=config.task_timeout_seconds)
    diff_collector = DiffCollector(repo_root)
    validator = MechanicalValidator(repo_root, config.validation_command, logger)

    try:
        if args.plan_file:
            tasks: list[Task] = load_plan_from_file(Path(args.plan_file).resolve(), task_parser)
            logger.info("Loaded %s task(s) from plan file", len(tasks))
        else:
            tasks = obtain_plan(config, supervisor, task_parser, repo_tree, logger)

        for task in tasks:
            execute_task_with_review(
                task, config, supervisor, selector,
                runner, diff_collector, validator, logger,
            )

        summary = json.dumps({"status": "success", "tasks": len(tasks)})
        print(summary)
        logger.info("Bridge run completed — %s task(s)", len(tasks))
        return 0

    except Exception as ex:
        logger.exception("Bridge run failed: %s", ex)
        print(json.dumps({"status": "failure", "error": str(ex)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
