from __future__ import annotations

from pathlib import Path
from typing import Optional


class IdeaLoader:
    def load(self, idea_file: Optional[Path]) -> Optional[str]:
        if idea_file is None:
            return None

        resolved_path: Path = idea_file.resolve()
        if not resolved_path.exists():
            raise FileNotFoundError(f"Idea file not found: {resolved_path}")

        return resolved_path.read_text(encoding="utf-8").strip()
