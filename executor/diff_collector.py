from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

_logger = logging.getLogger(__name__)
_WIN_NO_WINDOW: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class DiffCollector:
    """Collects a compact git diff after Aider has run.

    The diff is sent to the supervisor so it can make an informed review
    decision (PASS or REWORK) without needing access to the full file contents.
    """

    _MAX_CHARS: int = 10000

    def __init__(self, repo_root: Path) -> None:
        self._root = repo_root

    def collect(self, files: list[str] | None = None) -> str:
        """Return a truncated diff string of changes.

        If files list provided: 
        1. Runs 'git add -N' on those specific files to make new ones visible.
        2. Returns diff scoped strictly to those files.

        Otherwise: 
        Returns everything changed since last commit (staged + unstaged).
        """
        if files:
            # 1. Intent-to-add only the scoped files
            for f in files:
                self._git(["git", "add", "-N", str(f)])
            
            # 2. Scoped diff
            args_stat = ["git", "diff", "--stat"] + files
            args_diff = ["git", "diff"] + files
            
            stat = self._git(args_stat)
            diff = self._git(args_diff)
        else:
            # Fallback: Whole repo
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
            cut = combined.rfind("\n", 0, self._MAX_CHARS)
            if cut == -1:
                cut = self._MAX_CHARS
            omitted_lines = combined[cut:].count("\n")
            _logger.debug(
                "Diff truncated: %d lines omitted (original %d chars, limit %d)",
                omitted_lines, len(combined), self._MAX_CHARS,
            )
            combined = (
                combined[:cut]
                + f"\n...[diff truncated — showing first {self._MAX_CHARS} chars,"
                f" {omitted_lines} lines omitted]"
            )

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
                creationflags=_WIN_NO_WINDOW,
            )
            return result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""
