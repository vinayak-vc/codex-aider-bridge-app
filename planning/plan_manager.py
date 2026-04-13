"""Plan generation, model routing, feature manifest, and workflow enforcement.

Extracted from main.py to keep the CLI entry point focused on orchestration.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from context.project_context import ProjectContext
from models.task import BridgeConfig, Task
from parser.task_parser import PlanParseError, TaskParser
from supervisor.agent import SupervisorAgent, SupervisorError


def load_plan_from_file(plan_file: Path, parser: TaskParser) -> list[Task]:
    raw = plan_file.read_text(encoding="utf-8")
    return parser.parse(raw)


def auto_split_tasks(
    tasks: list[Task],
    threshold: int,
    logger: logging.Logger,
) -> list[Task]:
    """Split tasks that target >= threshold files into single-file sub-tasks."""
    if threshold <= 0:
        return tasks

    result: list[Task] = []
    for task in tasks:
        if len(task.files) < threshold:
            result.append(task)
            continue

        logger.info(
            "Auto-split: task %s has %d file(s) (threshold=%d) → %d single-file sub-tasks",
            task.id, len(task.files), threshold, len(task.files),
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


# ── Smart model routing ──────────────────────────────────────────────────────

def get_installed_models_for_routing(
    logger: logging.Logger,
) -> list[dict]:
    """Get installed Ollama models with quality/speed metadata."""
    try:
        from utils.model_advisor import MODELS, _get_ollama_models
    except ImportError:
        return []

    installed_names = _get_ollama_models()
    if not installed_names:
        return []

    result: list[dict] = []
    for model in MODELS:
        bare_name = model.name.replace("ollama/", "")
        for inst in installed_names:
            if bare_name in inst or inst in bare_name:
                result.append({
                    "name": model.name,
                    "quality": model.quality,
                    "speed": model.speed,
                    "param_size": model.param_size,
                })
                break

    if result:
        logger.info(
            "Model routing: %d coding model(s) installed: %s",
            len(result), ", ".join(m["name"] for m in result),
        )
    return result


def build_model_roster_text(models: list[dict]) -> str:
    """Build a text block describing available models for the supervisor prompt."""
    lines: list[str] = []
    for m in models:
        lines.append(
            f"  {m['name']}  —  {m['speed']}, quality {m['quality']}/10, {m['param_size']} params"
        )
    return "\n".join(lines)


# ── Feature manifest ─────────────────────────────────────────────────────────

def build_feature_manifest(
    goal: str,
    repo_root: Path,
    logger: logging.Logger,
) -> Optional[str]:
    """Scan the goal for folder references and read feature spec .md files."""
    folder_patterns = re.findall(
        r'(?:from|in|at|under)\s+([A-Za-z0-9_./\\-]+/)',
        goal, re.IGNORECASE,
    )

    for folder_ref in folder_patterns:
        folder_path = repo_root / folder_ref.rstrip("/")
        if not folder_path.is_dir():
            continue

        md_files = sorted(folder_path.glob("*.md"))
        if not md_files:
            continue

        logger.info("Feature manifest: found %d spec(s) in %s", len(md_files), folder_ref)

        sections: list[str] = []
        for md_file in md_files:
            content = md_file.read_text(encoding="utf-8", errors="replace").strip()
            if len(content) > 3000:
                content = content[:3000] + "\n... (truncated)"
            sections.append(f"=== FEATURE: {md_file.stem} ===\n{content}")

        return "\n\n".join(sections)

    return None


# ── Plan generation ──────────────────────────────────────────────────────────

def obtain_plan(
    config: BridgeConfig,
    supervisor: SupervisorAgent,
    task_parser: TaskParser,
    project_context: ProjectContext,
    logger: logging.Logger,
    feature_specs: Optional[str] = None,
    model_roster: Optional[str] = None,
) -> list[Task]:
    """Ask the supervisor to produce a valid JSON plan, retrying on failure."""
    feedback: Optional[str] = None

    for attempt in range(1, config.max_plan_attempts + 1):
        logger.info("Requesting plan — attempt %s of %s", attempt, config.max_plan_attempts)
        try:
            plan_text = supervisor.generate_plan(
                config.goal,
                project_context=project_context,
                idea_text=config.idea_text,
                feedback=feedback,
                workflow_profile=config.workflow_profile,
                feature_specs=feature_specs,
                model_roster=model_roster,
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
    """Print the task plan and ask the user to confirm before execution."""
    print("\n" + "=" * 60)
    print(f"  PLAN PREVIEW — {len(tasks)} task(s)")
    print("=" * 60)
    for task in tasks:
        files_display = ", ".join(task.files) if task.files else "(no specific files)"
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


def enforce_workflow_profile(tasks: list[Task], config: BridgeConfig) -> None:
    """Validate tasks conform to the selected workflow profile."""
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
            normalized_must_exist = [Path(fp).as_posix() for fp in task.must_exist]
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
