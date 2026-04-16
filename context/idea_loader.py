from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class IdeaLoader:
    """Load project brief / idea text from a single file or a folder of specs.

    When *idea_file* points to a **directory**, every ``.md`` file inside is
    read and concatenated with clear ``=== FEATURE: name ===`` separators so
    the supervisor can generate per-feature tasks.
    """

    def load(self, idea_file: Optional[Path]) -> Optional[str]:
        if idea_file is None:
            return None

        resolved_path: Path = idea_file.resolve()

        # Folder support — read all .md files inside
        if resolved_path.is_dir():
            return self._load_folder(resolved_path)

        if not resolved_path.exists():
            raise FileNotFoundError(f"Idea file not found: {resolved_path}")

        return resolved_path.read_text(encoding="utf-8").strip()

    # ── Folder loading ───────────────────────────────────────────────────

    @staticmethod
    def _load_folder(folder: Path) -> Optional[str]:
        """Read all .md files in *folder* and return as a feature manifest."""
        md_files = sorted(folder.glob("*.md"))
        if not md_files:
            logger.warning("IdeaLoader: folder %s contains no .md files", folder)
            return None

        sections: list[str] = []
        for md_file in md_files:
            content = md_file.read_text(encoding="utf-8", errors="replace").strip()
            feature_name = md_file.stem
            # Truncate very large specs to keep prompt manageable
            if len(content) > 3000:
                content = content[:3000] + "\n... (truncated)"
            sections.append(f"=== FEATURE: {feature_name} ===\n{content}")

        logger.info(
            "IdeaLoader: loaded %d feature spec(s) from %s",
            len(sections), folder.name,
        )
        return "\n\n".join(sections)
