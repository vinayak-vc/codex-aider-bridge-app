from __future__ import annotations

import subprocess
from pathlib import Path


class DiffCollector:
    """Collects a compact git diff after Aider has run.

    The diff is sent to the supervisor so it can make an informed review
    decision (PASS or REWORK) without needing access to the full file contents.
    """

    _MAX_CHARS: int = 4000

    def __init__(self, repo_root: Path) -> None:
        self._root = repo_root

    def collect(self) -> str:
        """Return a truncated diff string of all changes since the last commit.

        Tries git diff HEAD first (staged + unstaged vs last commit).
        Falls back to git diff (unstaged only) if HEAD diff is empty,
        which handles repos with no commits yet.
        """
        stat = self._git(["git", "diff", "--stat", "HEAD"])
        diff = self._git(["git", "diff", "HEAD"])

        if not stat and not diff:
            stat = self._git(["git", "diff", "--stat"])
            diff = self._git(["git", "diff"])

        if not stat and not diff:
            return "(no diff available — no file changes detected or repo is not a git repository)"

        parts: list[str] = []
        if stat:
            parts.append(f"--- changed files ---\n{stat}")
        if diff:
            parts.append(f"--- diff ---\n{diff}")

        combined = "\n\n".join(parts)

        if len(combined) > self._MAX_CHARS:
            combined = combined[: self._MAX_CHARS] + "\n...[diff truncated — showing first 4000 chars]"

        return combined.strip()

    def _git(self, args: list[str]) -> str:
        try:
            result = subprocess.run(
                args,
                cwd=self._root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=15,
            )
            return result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""
