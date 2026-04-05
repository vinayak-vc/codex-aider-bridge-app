"""Git operations for the bridge — init, commit, readiness checks.

Extracted from main.py to keep the orchestrator focused on task execution.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Optional

from models.task import Task

_WIN_NO_WINDOW: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def run_git_command(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git"] + args,
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
        creationflags=_WIN_NO_WINDOW,
    )


def is_git_repository(repo_root: Path) -> bool:
    result = run_git_command(repo_root, ["rev-parse", "--is-inside-work-tree"])
    return result.returncode == 0 and result.stdout.strip().lower() == "true"


def has_git_head(repo_root: Path) -> bool:
    result = run_git_command(repo_root, ["rev-parse", "--verify", "HEAD"])
    return result.returncode == 0


def get_git_branch_name(repo_root: Path) -> Optional[str]:
    result = run_git_command(repo_root, ["branch", "--show-current"])
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    return branch or None


def collect_git_readiness(repo_root: Path) -> dict[str, object]:
    is_git = is_git_repository(repo_root)
    has_head = has_git_head(repo_root) if is_git else False
    branch = get_git_branch_name(repo_root) if is_git else None

    staged_count = 0
    unstaged_count = 0
    untracked_count = 0
    clean = False

    if is_git:
        status_result = run_git_command(repo_root, ["status", "--porcelain"])
        if status_result.returncode == 0:
            lines = [line for line in status_result.stdout.splitlines() if line.strip()]
            for line in lines:
                if line.startswith("??"):
                    untracked_count += 1
                    continue
                if len(line) >= 2:
                    if line[0] != " ":
                        staged_count += 1
                    if line[1] != " ":
                        unstaged_count += 1
            clean = len(lines) == 0

    next_action = "continue"
    if not is_git:
        next_action = "prompt_to_create_or_stop"
    elif not has_head:
        next_action = "create_baseline_commit"

    return {
        "is_git_repository": is_git,
        "has_head": has_head,
        "branch": branch,
        "clean_worktree": clean,
        "staged_changes": staged_count,
        "unstaged_changes": unstaged_count,
        "untracked_files": untracked_count,
        "next_action": next_action,
    }


def log_git_readiness_preview(repo_root: Path, logger: logging.Logger) -> dict[str, object]:
    readiness = collect_git_readiness(repo_root)
    branch = readiness.get("branch") or "(none)"
    logger.info(
        "Git readiness — repo=%s, head=%s, branch=%s, clean=%s, staged=%s, unstaged=%s, untracked=%s, next=%s",
        "yes" if readiness["is_git_repository"] else "no",
        "yes" if readiness["has_head"] else "no",
        branch,
        "yes" if readiness["clean_worktree"] else "no",
        readiness["staged_changes"],
        readiness["unstaged_changes"],
        readiness["untracked_files"],
        readiness["next_action"],
    )
    return readiness


def prompt_for_git_repo_creation(repo_root: Path) -> bool:
    if not sys.stdin.isatty():
        raise RuntimeError(
            f"Repo root is not a git repository: {repo_root}. "
            "The bridge requires a git repository with at least one commit before it can run. "
            "Create the repository yourself, or rerun interactively and let the bridge do it for you."
        )

    print()
    print(f"Target project is not a git repository: {repo_root}")
    print("The bridge will not run without git because diff-based review depends on it.")
    print("Choose one:")
    print("  [1] I will create the git repository myself and rerun the bridge")
    print("  [2] Create a local git repository and baseline commit for me now")

    while True:
        try:
            answer = input("Select 1 or 2: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            raise RuntimeError(
                "Bridge cancelled. The target project must be a git repository before the run can continue."
            )

        if answer in {"1", "self", "manual"}:
            raise RuntimeError(
                "Bridge stopped. Create the git repository yourself and rerun when it is ready."
            )
        if answer in {"2", "bridge", "auto"}:
            return True

        print("Please enter 1 or 2.")


def ensure_git_repository_exists(repo_root: Path, logger: logging.Logger) -> None:
    if is_git_repository(repo_root):
        return

    logger.warning("Pre-flight: %s is not a git repository.", repo_root)
    should_create = prompt_for_git_repo_creation(repo_root)
    if not should_create:
        raise RuntimeError(
            "Bridge stopped. The target project must be a git repository before the run can continue."
        )

    logger.info("Initialising local git repository at %s", repo_root)
    init_result = run_git_command(repo_root, ["init"])
    if init_result.returncode != 0:
        raise RuntimeError(
            "Failed to initialise git repository at "
            f"{repo_root}: {init_result.stderr.strip() or init_result.stdout.strip()}"
        )

    logger.info("Local git repository created at %s", repo_root)


def ensure_git_baseline_commit(repo_root: Path, logger: logging.Logger) -> None:
    if has_git_head(repo_root):
        return

    logger.info("Pre-flight: repository at %s has no commits yet. Creating baseline commit.", repo_root)

    add_result = run_git_command(repo_root, ["add", "-A"])
    if add_result.returncode != 0:
        raise RuntimeError(
            "Failed to stage files for the initial git commit: "
            f"{add_result.stderr.strip() or add_result.stdout.strip()}"
        )

    commit_result = run_git_command(
        repo_root,
        ["commit", "--allow-empty", "-m", "Initial bridge baseline"],
    )
    if commit_result.returncode != 0:
        output = commit_result.stderr.strip() or commit_result.stdout.strip()
        raise RuntimeError(
            "Failed to create the initial git commit required by the bridge: "
            f"{output}"
        )

    logger.info("Created initial git baseline commit for %s", repo_root)


def auto_commit_task_changes(repo_root: Path, task: Task, logger: logging.Logger) -> Optional[str]:
    file_args = list(task.files)
    add_result = run_git_command(repo_root, ["add", "-A", "--"] + file_args)
    if add_result.returncode != 0:
        raise RuntimeError(
            f"Failed to stage task {task.id} changes for auto-commit: "
            f"{add_result.stderr.strip() or add_result.stdout.strip()}"
        )

    diff_result = run_git_command(repo_root, ["diff", "--cached", "--name-only", "--"] + file_args)
    if diff_result.returncode != 0:
        raise RuntimeError(
            f"Failed to inspect staged changes for task {task.id}: "
            f"{diff_result.stderr.strip() or diff_result.stdout.strip()}"
        )

    staged_files = [line.strip() for line in diff_result.stdout.splitlines() if line.strip()]
    if not staged_files:
        logger.info("Task %s: no staged file changes to auto-commit.", task.id)
        return None

    commit_message = f"bridge: task {task.id} {task.type} " + ", ".join(task.files[:2])
    if len(task.files) > 2:
        commit_message += " ..."

    commit_result = run_git_command(repo_root, ["commit", "-m", commit_message])
    if commit_result.returncode != 0:
        raise RuntimeError(
            f"Failed to auto-commit task {task.id}: "
            f"{commit_result.stderr.strip() or commit_result.stdout.strip()}"
        )

    head_result = run_git_command(repo_root, ["rev-parse", "--short", "HEAD"])
    if head_result.returncode != 0:
        raise RuntimeError(
            f"Task {task.id} committed, but the new HEAD could not be resolved: "
            f"{head_result.stderr.strip() or head_result.stdout.strip()}"
        )

    commit_sha = head_result.stdout.strip()
    logger.info(
        "Task %s: auto-committed %d file(s) at %s",
        task.id,
        len(staged_files),
        commit_sha,
    )
    return commit_sha
