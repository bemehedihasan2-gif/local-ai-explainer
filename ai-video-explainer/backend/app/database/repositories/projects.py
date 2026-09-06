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
    "stored_filename",  # internal (relative to the project folder)
    "input_path",       # internal absolute path - never sent to clients
    "file_size",
    "sha256",
    "duration",
    "width",
    "height",
    "fps",
    "raw_fps",
    "video_codec",
    "audio_codec",
    "container_format",
    "bitrate",
    "has_video",
    "has_audio",
    "language",
    "target_duration_seconds",
    "status",
    "progress",
    "error_message",
    "created_at",
    "updated_at",
)

#: Columns the generic :meth:`ProjectRepository.update` may modify.
_UPDATABLE_COLUMNS = {
    "original_filename",
    "stored_filename",
    "input_path",
    "file_size",
    "sha256",
    "duration",
    "width",
    "height",
    "fps",
    "raw_fps",
    "video_codec",
    "audio_codec",
    "container_format",
    "bitrate",
    "has_video",
    "has_audio",
    "language",
    "target_duration_seconds",
    "status",
    "progress",
    "error_message",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_dict(row: Any) -> dict[str, Any]:
    """sqlite row -> plain dict with boolean flags normalized to real bools."""
    data = dict(row)
    for flag in ("has_video", "has_audio"):
        if data.get(flag) is not None:
            data[flag] = bool(data[flag])
    return data


class ProjectRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create(
        self,
        *,
        original_filename: str,
        language: str,
        target_duration_seconds: int,
        status: ProjectStatus | str = ProjectStatus.CREATED,
        stored_filename: str | None = None,
        input_path: str | None = None,
        file_size: int | None = None,
        sha256: str | None = None,
        duration: float | None = None,
        width: int | None = None,
        height: int | None = None,
        fps: float | None = None,
        raw_fps: str | None = None,
        video_codec: str | None = None,
        audio_codec: str | None = None,
        container_format: str | None = None,
        bitrate: int | None = None,
        has_video: bool | None = None,
        has_audio: bool | None = None,
        progress: float = 0.0,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        row_id = new_id()
        now = _now()
        status_value = status.value if isinstance(status, ProjectStatus) else status
        with self._db.connect() as conn:
            conn.execute(
                """
                INSERT INTO projects (
                    id, original_filename, stored_filename, input_path,
                    file_size, sha256, duration, width, height, fps, raw_fps,
                    video_codec, audio_codec, container_format, bitrate,
                    has_video, has_audio, language, target_duration_seconds,
                    status, progress, error_message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_id, original_filename, stored_filename, input_path,
                    file_size, sha256, duration, width, height, fps, raw_fps,
                    video_codec, audio_codec, container_format, bitrate,
                    int(has_video) if has_video is not None else 0,
                    int(has_audio) if has_audio is not None else 0,
                    language, target_duration_seconds,
                    status_value, progress, error_message, now, now,
                ),
            )
        logger.info(
            "Created project %s (status=%s)",
            row_id, status_value, extra={"project_id": row_id},
        )
        return self.get(row_id)

    def get(self, project_id: str) -> dict[str, Any]:
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_PROJECT_COLUMNS)} FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            raise ProjectNotFoundError(f"Project '{project_id}' was not found.")
        return _row_to_dict(row)

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
        return [_row_to_dict(row) for row in rows]

    def update(
        self,
        project_id: str,
        **fields: Any,
    ) -> dict[str, Any]:
        """Update whitelisted columns; returns the refreshed project row."""
        unknown = set(fields) - _UPDATABLE_COLUMNS
        if unknown:
            raise ValueError(
                f"update() called with non-column fields: {sorted(unknown)}"
            )
        if not fields:
            return self.get(project_id)

        for flag in ("has_video", "has_audio"):
            if fields.get(flag) is not None:
                fields[flag] = int(fields[flag])
        sets = ", ".join(f"{column} = ?" for column in fields)
        now = _now()
        params: list[Any] = [*fields.values(), now, project_id]
        with self._db.connect() as conn:
            conn.execute(
                f"UPDATE projects SET {sets}, updated_at = ? WHERE id = ?",
                params,
            )
        logger.debug(
            "Updated project %s (%s)",
            project_id, ", ".join(fields), extra={"project_id": project_id},
        )
        return self.get(project_id)

    def find_by_sha256(self, sha256: str) -> list[dict[str, Any]]:
        """All projects with the same content fingerprint (newest first)."""
        with self._db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT {', '.join(_PROJECT_COLUMNS)} FROM projects
                WHERE sha256 = ?
                ORDER BY created_at DESC, rowid DESC
                """,
                (sha256,),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def delete(self, project_id: str) -> None:
        # Raises 404 if the id is unknown (malformed ids simply never match).
        self.get(project_id)
        with self._db.connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        logger.info("Deleted project %s", project_id, extra={"project_id": project_id})
