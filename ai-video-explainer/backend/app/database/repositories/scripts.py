"""script_runs table repository (Phase 5).

Stores a compact summary per story+script run - never the story/script
payloads themselves (those live in JSON files under the project folder's
``analysis/story/`` directory).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.database.connection import Database
from app.models.project import new_id
from app.utils.errors import ProjectNotFoundError
from app.utils.logging import get_logger

logger = get_logger("app.database.repositories.scripts")

_SCRIPT_COLUMNS = (
    "id",
    "project_id",
    "status",
    "current_stage",
    "started_at",
    "completed_at",
    "language",
    "target_duration_seconds",
    "content_type",
    "content_type_confidence",
    "selected_scene_count",
    "word_count",
    "quality_score",
    "estimated_duration_seconds",
    "generation_fingerprint",
    "analysis_fingerprint",
    "error_message",
    "warnings",
    "created_at",
    "updated_at",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ScriptRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def _project_exists(self, conn: Any, project_id: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM projects WHERE id = ?", (project_id,)
        ).fetchone() is not None

    def create(
        self,
        *,
        project_id: str,
        language: str,
        target_duration_seconds: int,
        generation_fingerprint: str,
        analysis_fingerprint: str,
    ) -> dict[str, Any]:
        row_id = new_id()
        now = _now()
        with self._db.connect() as conn:
            if not self._project_exists(conn, project_id):
                raise ProjectNotFoundError(f"Project '{project_id}' was not found.")
            conn.execute(
                """
                INSERT INTO script_runs (
                    id, project_id, status, current_stage, language,
                    target_duration_seconds, generation_fingerprint,
                    analysis_fingerprint, created_at, updated_at
                ) VALUES (?, ?, 'queued', NULL, ?, ?, ?, ?, ?, ?)
                """,
                (row_id, project_id, language, target_duration_seconds,
                 generation_fingerprint, analysis_fingerprint, now, now),
            )
        logger.info(
            "Created script run %s for project %s (lang=%s, %ds)",
            row_id, project_id, language, target_duration_seconds,
        )
        return self.get(row_id)

    def get(self, script_run_id: str) -> dict[str, Any]:
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_SCRIPT_COLUMNS)} FROM script_runs "
                "WHERE id = ?",
                (script_run_id,),
            ).fetchone()
        if row is None:
            raise ProjectNotFoundError(f"Script run '{script_run_id}' was not found.")
        return self._normalize(row)

    def latest_for_project(self, project_id: str) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_SCRIPT_COLUMNS)} FROM script_runs "
                "WHERE project_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        return self._normalize(row) if row is not None else None

    def update(
        self,
        script_run_id: str,
        *,
        status: str | None = None,
        current_stage: str | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
        content_type: str | None = None,
        content_type_confidence: float | None = None,
        selected_scene_count: int | None = None,
        word_count: int | None = None,
        quality_score: int | None = None,
        estimated_duration_seconds: float | None = None,
        error_message: str | None = None,
        warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        """Update whitelisted summary fields (COALESCE keeps prior values)."""
        sets = ["updated_at = ?"]
        params: list[Any] = [_now()]
        for column, value in (
            ("status", status),
            ("current_stage", current_stage),
            ("started_at", started_at),
            ("completed_at", completed_at),
            ("content_type", content_type),
            ("content_type_confidence", content_type_confidence),
            ("selected_scene_count", selected_scene_count),
            ("word_count", word_count),
            ("quality_score", quality_score),
            ("estimated_duration_seconds", estimated_duration_seconds),
            ("error_message", error_message),
            ("warnings", json.dumps(warnings) if warnings is not None else None),
        ):
            if value is None:
                continue
            sets.append(f"{column} = COALESCE(?, {column})")
            params.append(int(value) if isinstance(value, bool) else value)
        params.append(script_run_id)
        with self._db.connect() as conn:
            conn.execute(
                f"UPDATE script_runs SET {', '.join(sets)} WHERE id = ?",
                params,
            )
        return self.get(script_run_id)

    def has_active_run(self, project_id: str) -> bool:
        with self._db.connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM script_runs
                WHERE project_id = ? AND status IN ('queued', 'running')
                LIMIT 1
                """,
                (project_id,),
            ).fetchone()
        return row is not None

    @staticmethod
    def _normalize(row: Any) -> dict[str, Any]:
        data = dict(row)
        if data.get("warnings"):
            try:
                data["warnings"] = json.loads(data["warnings"])
            except json.JSONDecodeError:
                data["warnings"] = []
        else:
            data["warnings"] = []
        return data


__all__ = ["ScriptRepository"]