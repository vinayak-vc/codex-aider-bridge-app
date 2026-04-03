from __future__ import annotations

from pathlib import Path

from models.task import SelectedFiles


class FileSelector:
    def __init__(self, repo_root: Path) -> None:
        self._repo_root: Path = repo_root

    def select(self, relative_paths: list[str]) -> SelectedFiles:
        existing: list[Path] = []
        missing: list[Path] = []
        all_paths: list[Path] = []

        repo_root_resolved = self._repo_root.resolve()
        for relative_path in relative_paths:
            resolved_path: Path = (self._repo_root / relative_path).resolve()
            if not resolved_path.is_relative_to(repo_root_resolved):
                raise ValueError(
                    f"File path escapes repository root: {relative_path!r} resolves to "
                    f"{resolved_path}, which is outside {self._repo_root}"
                )
            all_paths.append(resolved_path)
            if resolved_path.exists():
                existing.append(resolved_path)
            else:
                missing.append(resolved_path)

        return SelectedFiles(existing=existing, missing=missing, all_paths=all_paths)
