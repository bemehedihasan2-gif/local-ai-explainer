"""Project table repository."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.database.connection import Database
from app.models.enums import ProjectStatus
from app.models.project import new_id
from app.utils.errors import ProjectNotFoundError
from app.utils.logging import get_logger

logger = get_logger("app.database.repositories.projects")

#: Columns always selected when loading a project row.
_PROJECT_COLUMNS = (
    "id",
    "original_filename",
    "stored_filename",
    "input_path",
    "duration",
    "width",
    "height",
    "fps",
    "language",
    "target_duration_seconds",
    "status",
    "progress",
    "error_message",
    "created_at",
    "updated_at",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ProjectRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create(
        self,
        *,
        original_filename: str,
        stored_filename: str | None,
        language: str,
        target_duration_seconds: int,
    ) -> dict[str, Any]:
        row_id = new_id()
        now = _now()
        with self._db.connect() as conn:
            conn.execute(
                """
                INSERT INTO projects (
                    id, original_filename, stored_filename, language,
                    target_duration_seconds, status, progress, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    row_id,
                    original_filename,
                    stored_filename,
                    language,
                    target_duration_seconds,
                    ProjectStatus.CREATED.value,
                    now,
                    now,
                ),
            )
        logger.info("Created project %s", row_id, extra={"project_id": row_id})
        return self.get(row_id)

    def get(self, project_id: str) -> dict[str, Any]:
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_PROJECT_COLUMNS)} FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            raise ProjectNotFoundError(f"Project '{project_id}' was not found.")
        return dict(row)

    def list(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        with self._db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT {', '.join(_PROJECT_COLUMNS)} FROM projects
                ORDER BY created_at DESC, rowid DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete(self, project_id: str) -> None:
        # Raises 404 if the id is unknown (malformed ids simply never match).
        self.get(project_id)
        with self._db.connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        logger.info("Deleted project %s", project_id, extra={"project_id": project_id})
