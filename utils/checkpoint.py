"""Lightweight checkpoint: persist completed task IDs so a failed run can resume.

The checkpoint file (.bridge_checkpoint.json) is written to the repo root after
every successful task. On the next run the bridge skips all tasks whose IDs are
already in the checkpoint. The file is deleted on a fully successful run.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

_CHECKPOINT_FILENAME = ".bridge_checkpoint.json"
_logger = logging.getLogger(__name__)


def save_checkpoint(repo_root: Path, completed_ids: set[int]) -> None:
    """Write completed task IDs to the checkpoint file."""
    checkpoint_path = repo_root / _CHECKPOINT_FILENAME
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

    Returns an empty set if no checkpoint exists or if it is unreadable.
    """
    checkpoint_path = repo_root / _CHECKPOINT_FILENAME
    if not checkpoint_path.exists():
        return set()
    try:
        data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        ids: set[int] = set(data.get("completed", []))
        _logger.info("Checkpoint found: resuming — %d task(s) already completed: %s", len(ids), sorted(ids))
        return ids
    except Exception as ex:
        _logger.warning("Could not read checkpoint (ignoring): %s", ex)
        return set()


def clear_checkpoint(repo_root: Path) -> None:
    """Delete the checkpoint file after a fully successful run."""
    checkpoint_path = repo_root / _CHECKPOINT_FILENAME
    try:
        if checkpoint_path.exists():
            checkpoint_path.unlink()
            _logger.debug("Checkpoint cleared after successful run.")
    except OSError as ex:
        _logger.warning("Could not clear checkpoint: %s", ex)
