"""Cleanup utilities (foundations only in Phase 1).

Later phases will stream large intermediates to ``temp``; this module owns
removing them so an interrupted run cannot fill the disk on the 8 GB target
machine. Only *direct children* of the managed directories are ever touched.
"""

from __future__ import annotations

import time
from pathlib import Path

from app.utils.logging import get_logger

logger = get_logger("app.services.cleanup")


def remove_stale_files(
    directory: str | Path,
    *,
    max_age_seconds: int = 24 * 60 * 60,
    pattern: str = "*",
) -> int:
    """Delete direct-children files in ``directory`` older than the age limit.

    Returns the number of files removed. Never recurses into subdirectories.
    """
    root = Path(directory)
    if not root.is_dir():
        return 0
    cutoff = time.time() - max_age_seconds
    removed = 0
    for child in root.glob(pattern):
        if not child.is_file():
            continue
        try:
            if child.stat().st_mtime < cutoff:
                child.unlink()
                removed += 1
        except OSError as exc:  # file vanished or locked; skip, do not crash
            logger.warning("Could not remove stale file %s: %s", child, exc)
    if removed:
        logger.info("Removed %d stale file(s) from %s", removed, root)
    return removed


def directory_size_bytes(directory: str | Path) -> int:
    """Total size of regular files directly inside ``directory`` (no recursion)."""
    root = Path(directory)
    if not root.is_dir():
        return 0
    total = 0
    for child in root.iterdir():
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                continue
    return total


__all__ = ["remove_stale_files", "directory_size_bytes"]
