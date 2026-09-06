"""Managed storage directories.

The backend creates and validates its own directories at startup (never
user-supplied paths), and reports their state to the system status endpoint.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from app.utils.errors import StorageUnavailableError
from app.utils.logging import get_logger

logger = get_logger("app.services.storage")


class StorageService:
    """Creates and inspects the directories configured in settings."""

    def __init__(self, settings) -> None:
        self._settings = settings

    def directories(self) -> dict[str, Path]:
        return self._settings.storage_directories

    def inspect(self) -> list[dict[str, Any]]:
        """Ensure each directory exists, then report existence/writability."""
        entries: list[dict[str, Any]] = []
        for name, path in self.directories().items():
            path = Path(path)
            entry: dict[str, Any] = {"name": name, "path": str(path)}
            try:
                path.mkdir(parents=True, exist_ok=True)
                entry["exists"] = True
            except OSError as exc:
                entry["exists"] = False
                entry["error"] = str(exc)
                entries.append(entry)
                continue
            entry["exists"] = path.is_dir()
            entry["writable"] = os.access(path, os.W_OK) and path.is_dir()
            entry.pop("error", None)
            entries.append(entry)
        return entries

    def ok(self, entries: list[dict[str, Any]] | None = None) -> bool:
        entries = entries if entries is not None else self.inspect()
        return all(e.get("exists") and e.get("writable") for e in entries)

    def ensure_ready(self) -> None:
        """Raise :class:`StorageUnavailableError` if any directory is broken."""
        entries = self.inspect()
        failing = [
            e["name"] for e in entries
            if not (e.get("exists") and e.get("writable"))
        ]
        if failing:
            raise StorageUnavailableError(
                "Required storage directories are not ready: "
                + ", ".join(failing)
            )
        for entry in entries:
            logger.info(
                "Storage directory %-9s ready: %s", entry["name"], entry["path"]
            )

    def free_space_mb(self, path: Path | None = None) -> int:
        """Free disk space on the volume hosting ``path`` (outputs by default)."""
        target = path or self._settings.output_dir
        try:
            return shutil.disk_usage(target).free // (1024 * 1024)
        except OSError:
            return -1


__all__ = ["StorageService"]
