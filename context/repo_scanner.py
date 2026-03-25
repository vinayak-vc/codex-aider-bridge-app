from __future__ import annotations

from pathlib import Path


class RepoScanner:
    """Produces a compact directory tree of the target repository.

    The tree is injected into the supervisor planning prompt so the supervisor
    knows which files already exist and can produce accurate relative file paths
    without guessing or hallucinating paths.
    """

    _IGNORE: frozenset[str] = frozenset({
        ".git", "__pycache__", "node_modules", ".venv", "venv", "aider-env",
        "logs", ".vs", "obj", "bin", "Library", "Temp", "Packages",
        ".idea", ".vscode", "dist", "build", ".mypy_cache", ".pytest_cache",
        ".tox", "coverage", ".eggs", "*.egg-info",
    })

    def __init__(self, repo_root: Path) -> None:
        self._root = repo_root

    def scan(self, max_depth: int = 4, max_entries: int = 100) -> str:
        """Return a tree string rooted at repo_root, capped by depth and entry count."""
        lines: list[str] = [self._root.name + "/"]
        counter = [0]
        self._walk(self._root, "", 0, max_depth, lines, counter, max_entries)
        return "\n".join(lines)

    def _walk(
        self,
        path: Path,
        prefix: str,
        depth: int,
        max_depth: int,
        lines: list[str],
        counter: list[int],
        max_entries: int,
    ) -> None:
        if depth >= max_depth or counter[0] >= max_entries:
            return

        try:
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except PermissionError:
            return

        entries = [e for e in entries if e.name not in self._IGNORE]

        for i, entry in enumerate(entries):
            if counter[0] >= max_entries:
                lines.append(prefix + "└── ...")
                break

            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            lines.append(prefix + connector + entry.name)
            counter[0] += 1

            if entry.is_dir():
                extension = "    " if is_last else "│   "
                self._walk(
                    entry, prefix + extension, depth + 1,
                    max_depth, lines, counter, max_entries,
                )
