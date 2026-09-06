"""Managed storage directories.

The backend creates and validates its own directories at startup (never
user-supplied paths), and reports their state to the system status endpoint.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from app.utils.errors import PathTraversalError, StorageUnavailableError
from app.utils.logging import get_logger
from app.utils.paths import is_within, safe_join

logger = get_logger("app.services.storage")

#: Sub-directories created inside ``projects/<project_id>/``.
PROJECT_SUBDIRS = ("input", "temp", "output")


class StorageService:
    """Creates and inspects the directories configured in settings."""

    def __init__(self, settings) -> None:
        self._settings = settings

    def directories(self) -> dict[str, Path]:
        return self._settings.storage_directories

    # -- per-project layout -------------------------------------------
    def project_root(self, project_id: str) -> Path:
        """Resolved ``projects/<id>`` path (never escapes the projects dir)."""
        return safe_join(self._settings.projects_dir, project_id)

    def project_path(self, project_id: str, *parts: str) -> Path:
        """Resolved path under a project folder, traversal-safe."""
        return safe_join(self.project_root(project_id), *parts)

    def ensure_project_dirs(self, project_id: str) -> dict[str, Path]:
        """Create ``input/``, ``temp/`` and ``output/`` for a project.

        Layout: projects/<id>/{input,temp,output}. ``projects/<id>`` itself
        is created too. Returns the named paths.
        """
        root = self.project_root(project_id)
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageUnavailableError(
                f"Could not create project folder '{root}': {exc}"
            ) from exc
        paths: dict[str, Path] = {"project": root}
        for name in PROJECT_SUBDIRS:
            sub = safe_join(root, name)
            try:
                sub.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise StorageUnavailableError(
                    f"Could not create project folder '{sub}': {exc}"
                ) from exc
            paths[name] = sub
        return paths

    def remove_project_directory(self, project_id: str) -> None:
        """Recursively delete ``projects/<id>`` if it exists.

        Refuses to touch anything outside the configured projects root.
        Missing directories are fine (nothing to remove).
        """
        root = self.project_root(project_id)
        if not is_within(self._settings.projects_dir, root):
            raise PathTraversalError(
                f"Refusing to delete '{root}': outside the projects directory."
            )
        if not root.exists():
            return
        try:
            shutil.rmtree(root)
        except OSError as exc:
            logger.warning(
                "Could not remove project directory %s: %s", root, exc,
                extra={"project_id": project_id},
            )
            raise StorageUnavailableError(
                f"Could not remove project files '{root}': {exc}"
            ) from exc
        logger.info(
            "Removed project directory %s", root, extra={"project_id": project_id}
        )

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
