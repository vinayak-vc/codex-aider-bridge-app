"""Lightweight checkpoint: persist completed task IDs so a failed run can resume.

The checkpoint file is written inside the target project's bridge_progress/
directory after every successful task. On the next run the bridge skips all
tasks whose IDs are already in the checkpoint. The directory and file are
deleted on a fully successful run.

All bridge progress files live in <repo_root>/bridge_progress/ so that
running the bridge against multiple projects never mixes their state.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

_PROGRESS_DIR = "bridge_progress"
_CHECKPOINT_FILENAME = "checkpoint.json"
_logger = logging.getLogger(__name__)


def _progress_dir(repo_root: Path) -> Path:
    """Return (and create) the per-project bridge progress directory."""
    d = repo_root / _PROGRESS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_checkpoint(repo_root: Path, completed_ids: set[int]) -> None:
    """Write completed task IDs to the checkpoint file."""
    checkpoint_path = _progress_dir(repo_root) / _CHECKPOINT_FILENAME
    try:
        checkpoint_path.write_text(
            json.dumps({"completed": sorted(completed_ids)}, indent=2),
            encoding="utf-8",
        )
        _logger.debug("Checkpoint saved: %d task(s) completed", len(completed_ids))
    except OSError as ex:
        _logger.warning("Could not save checkpoint: %s", ex)


def load_checkpoint(repo_root: Path) -> set[int]:
    """Load completed task IDs from the checkpoint file.

    Also checks the legacy location (repo root) and migrates it on first load.
    Returns an empty set if no checkpoint exists or if it is unreadable.
    """
    checkpoint_path = _progress_dir(repo_root) / _CHECKPOINT_FILENAME

    # Migrate legacy .bridge_checkpoint.json from repo root if present.
    legacy_path = repo_root / ".bridge_checkpoint.json"
    if not checkpoint_path.exists() and legacy_path.exists():
        try:
            legacy_path.rename(checkpoint_path)
            _logger.info("Migrated checkpoint from %s to %s", legacy_path, checkpoint_path)
        except OSError:
            checkpoint_path = legacy_path  # fall back to reading legacy location

    if not checkpoint_path.exists():
        return set()
    try:
        data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        ids: set[int] = set(data.get("completed", []))
        _logger.info(
            "Checkpoint found: resuming — %d task(s) already completed: %s",
            len(ids), sorted(ids),
        )
        return ids
    except Exception as ex:
        _logger.warning("Could not read checkpoint (ignoring): %s", ex)
        return set()


def clear_checkpoint(repo_root: Path) -> None:
    """Delete the checkpoint file after a fully successful run."""
    checkpoint_path = _progress_dir(repo_root) / _CHECKPOINT_FILENAME
    try:
        if checkpoint_path.exists():
            checkpoint_path.unlink()
            _logger.debug("Checkpoint cleared after successful run.")
    except OSError as ex:
        _logger.warning("Could not clear checkpoint: %s", ex)
