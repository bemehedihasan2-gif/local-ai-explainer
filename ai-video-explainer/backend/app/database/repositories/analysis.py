"""analysis_results table repository (Phase 4).

Stores a compact summary per analysis run - never the transcript/OCR
payloads themselves (those live in JSON files under the project folder).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.database.connection import Database
from app.models.project import new_id
from app.utils.errors import ProjectNotFoundError
from app.utils.logging import get_logger

logger = get_logger("app.database.repositories.analysis")

_ANALYSIS_COLUMNS = (
    "id",
    "project_id",
    "status",
    "current_stage",
    "started_at",
    "completed_at",
    "detected_language",
    "language_probability",
    "scene_count",
    "transcript_available",
    "ocr_available",
    "visual_provider",
    "processing_seconds",
    "config_fingerprint",
    "preprocessing_fingerprint",
    "error_message",
    "warnings",
    "created_at",
    "updated_at",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AnalysisRepository:
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
        config_fingerprint: str,
        preprocessing_fingerprint: str,
    ) -> dict[str, Any]:
        row_id = new_id()
        now = _now()
        with self._db.connect() as conn:
            if not self._project_exists(conn, project_id):
                raise ProjectNotFoundError(f"Project '{project_id}' was not found.")
            conn.execute(
                """
                INSERT INTO analysis_results (
                    id, project_id, status, current_stage,
                    config_fingerprint, preprocessing_fingerprint,
                    created_at, updated_at
                ) VALUES (?, ?, 'queued', NULL, ?, ?, ?, ?)
                """,
                (row_id, project_id, config_fingerprint,
                 preprocessing_fingerprint, now, now),
            )
        logger.info(
            "Created analysis run %s for project %s",
            row_id, project_id, extra={"project_id": project_id},
        )
        return self.get(row_id)

    def get(self, analysis_id: str) -> dict[str, Any]:
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_ANALYSIS_COLUMNS)} FROM analysis_results "
                "WHERE id = ?",
                (analysis_id,),
            ).fetchone()
        if row is None:
            raise ProjectNotFoundError(f"Analysis run '{analysis_id}' was not found.")
        return self._normalize(row)

    def latest_for_project(self, project_id: str) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_ANALYSIS_COLUMNS)} FROM analysis_results "
                "WHERE project_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        return self._normalize(row) if row is not None else None

    def update(
        self,
        analysis_id: str,
        *,
        status: str | None = None,
        current_stage: str | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
        detected_language: str | None = None,
        language_probability: float | None = None,
        scene_count: int | None = None,
        transcript_available: bool | None = None,
        ocr_available: bool | None = None,
        visual_provider: str | None = None,
        processing_seconds: float | None = None,
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
            ("detected_language", detected_language),
            ("language_probability", language_probability),
            ("scene_count", scene_count),
            ("transcript_available", transcript_available),
            ("ocr_available", ocr_available),
            ("visual_provider", visual_provider),
            ("processing_seconds", processing_seconds),
            ("error_message", error_message),
            ("warnings", json.dumps(warnings) if warnings is not None else None),
        ):
            if value is None:
                continue
            sets.append(f"{column} = COALESCE(?, {column})")
            params.append(
                int(value) if isinstance(value, bool) else value
            )
        params.append(analysis_id)
        with self._db.connect() as conn:
            conn.execute(
                f"UPDATE analysis_results SET {', '.join(sets)} WHERE id = ?",
                params,
            )
        return self.get(analysis_id)

    def has_active_run(self, project_id: str) -> bool:
        with self._db.connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM analysis_results
                WHERE project_id = ? AND status IN ('queued', 'running')
                LIMIT 1
                """,
                (project_id,),
            ).fetchone()
        return row is not None

    @staticmethod
    def _normalize(row: Any) -> dict[str, Any]:
        data = dict(row)
        for flag in ("transcript_available", "ocr_available"):
            if data.get(flag) is not None:
                data[flag] = bool(data[flag])
        if data.get("warnings"):
            try:
                data["warnings"] = json.loads(data["warnings"])
            except json.JSONDecodeError:
                data["warnings"] = []
        else:
            data["warnings"] = []
        return data


__all__ = ["AnalysisRepository"]