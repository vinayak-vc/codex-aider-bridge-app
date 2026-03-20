from __future__ import annotations

import shlex
import shutil
import sys
from pathlib import Path


def split_command(command: str) -> list[str]:
    return shlex.split(command, posix=False)


def resolve_command_arguments(command: str, repo_root: Path) -> tuple[list[str], list[Path]]:
    arguments: list[str] = split_command(command)
    if not arguments:
        raise ValueError("Command text cannot be empty.")

    executable, searched_locations = resolve_executable(arguments[0], repo_root)
    arguments[0] = executable
    return arguments, searched_locations


def resolve_executable(executable: str, repo_root: Path) -> tuple[str, list[Path]]:
    candidate_path: Path = Path(executable)
    if candidate_path.is_absolute():
        if candidate_path.exists():
            return str(candidate_path), []
        raise FileNotFoundError(f"Configured executable was not found: {candidate_path}")

    if candidate_path.parent != Path("."):
        resolved_relative_path: Path = (repo_root / candidate_path).resolve()
        if resolved_relative_path.exists():
            return str(resolved_relative_path), []
        raise FileNotFoundError(f"Configured executable was not found: {resolved_relative_path}")

    resolved_from_path: str | None = shutil.which(executable)
    if resolved_from_path:
        return resolved_from_path, []

    searched_locations: list[Path] = []
    for scripts_directory in iter_local_script_directories(repo_root):
        searched_locations.append(scripts_directory)
        for suffix in ("", ".exe", ".cmd", ".bat"):
            candidate: Path = scripts_directory / f"{executable}{suffix}"
            if candidate.exists():
                return str(candidate), searched_locations

    raise FileNotFoundError(build_missing_executable_message(executable, searched_locations))


def iter_local_script_directories(repo_root: Path) -> list[Path]:
    directories: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        resolved: Path = path.resolve()
        if resolved not in seen and resolved.exists():
            seen.add(resolved)
            directories.append(resolved)

    add(repo_root / ".venv" / "Scripts")
    add(repo_root / "venv" / "Scripts")
    add(repo_root / "aider-env" / "Scripts")
    add(Path(sys.executable).resolve().parent)

    return directories


def build_missing_executable_message(executable: str, searched_locations: list[Path]) -> str:
    if not searched_locations:
        return f"Executable '{executable}' was not found on PATH."

    joined_locations: str = ", ".join(str(path) for path in searched_locations)
    return (
        f"Executable '{executable}' was not found on PATH or in local script directories: "
        f"{joined_locations}"
    )
