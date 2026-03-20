from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from bridge_logging.logger import configure_logging
from context.idea_loader import IdeaLoader
from context.file_selector import FileSelector
from executor.aider_runner import AiderRunner
from models.task import BridgeConfig, Task
from parser.task_parser import PlanParseError, TaskParser
from planner.codex_client import CodexClient, PlannerError
from planner.fallback_planner import FallbackPlanner
from validator.validator import ProjectValidator


def build_argument_parser() -> argparse.ArgumentParser:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        description="Bridge Codex planning with Aider execution and validation."
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
        help="Optional JSON plan file to execute instead of calling Codex.",
    )
    parser.add_argument(
        "--idea-file",
        default=None,
        help="Optional architecture or product idea file to inject into planning prompts.",
    )
    parser.add_argument(
        "--plan-output-file",
        default=None,
        help="Optional path to write the generated plan JSON.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate and parse the plan without invoking Aider.",
    )
    parser.add_argument(
        "--max-plan-attempts",
        type=int,
        default=3,
        help="Maximum retries when the planner returns invalid JSON.",
    )
    parser.add_argument(
        "--max-task-retries",
        type=int,
        default=2,
        help="Maximum retries per task after execution or validation failures.",
    )
    parser.add_argument(
        "--validation-command",
        default=os.getenv("BRIDGE_DEFAULT_VALIDATION"),
        help="Optional command executed after each task.",
    )
    parser.add_argument(
        "--codex-command",
        default=os.getenv(
            "BRIDGE_CODEX_COMMAND",
            "codex.cmd exec --skip-git-repo-check --color never",
        ),
        help="Planner command. Supports optional {prompt} and {output_file} placeholders.",
    )
    parser.add_argument(
        "--aider-command",
        default=os.getenv("BRIDGE_AIDER_COMMAND", "aider"),
        help="Aider command prefix.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity.",
    )
    return parser


def load_plan_from_file(plan_file: Path, parser: TaskParser) -> list[Task]:
    raw_content: str = plan_file.read_text(encoding="utf-8")
    return parser.parse(raw_content)


def obtain_plan(
    config: BridgeConfig,
    planner: CodexClient,
    fallback_planner: FallbackPlanner,
    parser: TaskParser,
    logger: logging.Logger,
) -> list[Task]:
    attempt: int = 0
    feedback: Optional[str] = None

    while attempt < config.max_plan_attempts:
        attempt += 1
        logger.info("Requesting plan attempt %s of %s", attempt, config.max_plan_attempts)

        try:
            plan_text: str = planner.generate_plan(config.goal, config.idea_text, feedback)
            tasks: list[Task] = parser.parse(plan_text)
            if config.plan_output_file is not None:
                config.plan_output_file.parent.mkdir(parents=True, exist_ok=True)
                config.plan_output_file.write_text(plan_text, encoding="utf-8")
                logger.info("Wrote generated plan to %s", config.plan_output_file)
            logger.info("Planner returned %s tasks", len(tasks))
            return tasks
        except (PlannerError, PlanParseError) as ex:
            feedback = str(ex)
            logger.warning("Planner attempt %s failed: %s", attempt, ex)

    logger.warning("Planner retries exhausted. Falling back to deterministic local plan generation.")
    tasks = fallback_planner.build_plan(config.goal, config.idea_text)
    if config.plan_output_file is not None:
        fallback_payload: dict[str, object] = {
            "tasks": [
                {
                    "id": task.id,
                    "files": task.files,
                    "instruction": task.instruction,
                    "type": task.type,
                }
                for task in tasks
            ]
        }
        config.plan_output_file.parent.mkdir(parents=True, exist_ok=True)
        config.plan_output_file.write_text(json.dumps(fallback_payload, indent=2), encoding="utf-8")
        logger.info("Wrote fallback plan to %s", config.plan_output_file)
    return tasks


def execute_task_loop(
    task: Task,
    config: BridgeConfig,
    planner: CodexClient,
    selector: FileSelector,
    runner: AiderRunner,
    validator: ProjectValidator,
    logger: logging.Logger,
) -> None:
    selected_files = selector.select(task.files)
    attempt: int = 0
    refinement_feedback: Optional[str] = None

    while attempt <= config.max_task_retries:
        current_instruction: str = task.instruction
        if refinement_feedback:
            try:
                current_instruction = planner.refine_task_instruction(task, refinement_feedback)
            except PlannerError as ex:
                logger.warning(
                    "Unable to refine task %s after failure feedback; retrying original instruction. Reason: %s",
                    task.id,
                    ex,
                )

        current_task: Task = Task(
            id=task.id,
            files=task.files,
            instruction=current_instruction,
            type=task.type,
        )

        logger.info("Executing task %s attempt %s", current_task.id, attempt + 1)

        if config.dry_run:
            logger.info("Dry-run task %s on files: %s", current_task.id, ", ".join(current_task.files))
            return

        execution_result = runner.run(current_task, selected_files.all_paths)
        if not execution_result.succeeded:
            attempt += 1
            refinement_feedback = (
                "Aider execution failed.\n"
                f"Exit code: {execution_result.exit_code}\n"
                f"Stdout:\n{execution_result.stdout}\n"
                f"Stderr:\n{execution_result.stderr}"
            )
            logger.warning(
                "Task %s failed during execution with exit code %s. %s",
                current_task.id,
                execution_result.exit_code,
                _summarize_process_failure(execution_result.stderr, execution_result.stdout),
            )
            if execution_result.exit_code == -1:
                raise RuntimeError(
                    f"Task {current_task.id} could not start Aider. {execution_result.stderr}"
                )
            if attempt > config.max_task_retries:
                raise RuntimeError(f"Task {current_task.id} failed after execution retries.")
            continue

        validation_result = validator.validate(current_task, selected_files.all_paths)
        if validation_result.succeeded:
            logger.info("Task %s validated successfully", current_task.id)
            return

        attempt += 1
        refinement_feedback = (
            "Validation failed after task execution.\n"
            f"Message: {validation_result.message}\n"
            f"Stdout:\n{validation_result.stdout}\n"
            f"Stderr:\n{validation_result.stderr}"
        )
        logger.warning("Task %s failed validation", current_task.id)

    raise RuntimeError(f"Task {task.id} exhausted retries.")


def _summarize_process_failure(stderr: str, stdout: str) -> str:
    for stream_name, content in (("stderr", stderr), ("stdout", stdout)):
        normalized: str = content.strip()
        if normalized:
            first_line: str = normalized.splitlines()[0]
            return f"{stream_name}: {first_line}"
    return "No process output was captured."


def main() -> int:
    parser: argparse.ArgumentParser = build_argument_parser()
    args: argparse.Namespace = parser.parse_args()

    repo_root: Path = Path(args.repo_root).resolve()
    logger: logging.Logger = configure_logging(repo_root / "logs", args.log_level)
    logger.info("Starting bridge app in %s", repo_root)
    idea_loader: IdeaLoader = IdeaLoader()
    idea_file: Optional[Path] = Path(args.idea_file).resolve() if args.idea_file else None
    plan_output_file: Optional[Path] = Path(args.plan_output_file).resolve() if args.plan_output_file else None
    idea_text: Optional[str] = idea_loader.load(idea_file)

    if idea_file is not None:
        logger.info("Loaded idea file from %s", idea_file)

    config: BridgeConfig = BridgeConfig(
        goal=args.goal,
        repo_root=repo_root,
        dry_run=bool(args.dry_run),
        max_plan_attempts=int(args.max_plan_attempts),
        max_task_retries=int(args.max_task_retries),
        validation_command=args.validation_command,
        codex_command=args.codex_command,
        aider_command=args.aider_command,
        idea_file=idea_file,
        idea_text=idea_text,
        plan_output_file=plan_output_file,
    )

    task_parser: TaskParser = TaskParser()
    selector: FileSelector = FileSelector(repo_root)
    planner: CodexClient = CodexClient(repo_root, config.codex_command, logger)
    fallback_planner: FallbackPlanner = FallbackPlanner()
    runner: AiderRunner = AiderRunner(repo_root, config.aider_command, logger)
    validator: ProjectValidator = ProjectValidator(repo_root, config.validation_command, logger)

    try:
        if args.plan_file:
            tasks: list[Task] = load_plan_from_file(Path(args.plan_file).resolve(), task_parser)
        else:
            tasks = obtain_plan(config, planner, fallback_planner, task_parser, logger)

        for task in tasks:
            execute_task_loop(task, config, planner, selector, runner, validator, logger)

        summary: str = json.dumps({"status": "success", "tasks": len(tasks)})
        print(summary)
        logger.info("Bridge run completed successfully")
        return 0
    except Exception as ex:
        logger.exception("Bridge run failed: %s", ex)
        print(json.dumps({"status": "failure", "error": str(ex)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
