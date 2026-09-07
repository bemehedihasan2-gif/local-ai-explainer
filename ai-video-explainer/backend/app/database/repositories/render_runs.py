"""render_runs table repository (Phase 7).

Stores a compact summary per final render - never the media itself (that
lives as files under the project folder: ``render/temp/``, ``output/``).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.database.connection import Database
from app.models.project import new_id
from app.utils.errors import ProjectNotFoundError
from app.utils.logging import get_logger

logger = get_logger("app.database.repositories.render_runs")

_RENDER_COLUMNS = (
    "id",
    "project_id",
    "status",
    "current_stage",
    "started_at",
    "completed_at",
    "narration_fingerprint",
    "render_fingerprint",
    "language",
    "output_path",
    "output_duration_ms",
    "output_width",
    "output_height",
    "output_fps",
    "output_size_bytes",
    "qc_score",
    "subtitle_status",
    "error_message",
    "warnings",
    "created_at",
    "updated_at",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RenderRepository:
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
        narration_fingerprint: str,
        render_fingerprint: str,
    ) -> dict[str, Any]:
        row_id = new_id()
        now = _now()
        with self._db.connect() as conn:
            if not self._project_exists(conn, project_id):
                raise ProjectNotFoundError(f"Project '{project_id}' was not found.")
            conn.execute(
                """
                INSERT INTO render_runs (
                    id, project_id, status, current_stage, language,
                    narration_fingerprint, render_fingerprint,
                    created_at, updated_at
                ) VALUES (?, ?, 'queued', NULL, ?, ?, ?, ?, ?)
                """,
                (row_id, project_id, language, narration_fingerprint,
                 render_fingerprint, now, now),
            )
        logger.info(
            "Created render run %s for project %s (lang=%s)",
            row_id, project_id, language,
        )
        return self.get(row_id)

    def get(self, render_run_id: str) -> dict[str, Any]:
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_RENDER_COLUMNS)} FROM render_runs WHERE id = ?",
                (render_run_id,),
            ).fetchone()
        if row is None:
            raise ProjectNotFoundError(f"Render run '{render_run_id}' was not found.")
        return self._normalize(row)

    def latest_for_project(self, project_id: str) -> dict[str, Any] | None:
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_RENDER_COLUMNS)} FROM render_runs "
                "WHERE project_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        return self._normalize(row) if row is not None else None

    def update(
        self,
        render_run_id: str,
        *,
        status: str | None = None,
        current_stage: str | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
        output_path: str | None = None,
        output_duration_ms: int | None = None,
        output_width: int | None = None,
        output_height: int | None = None,
        output_fps: float | None = None,
        output_size_bytes: int | None = None,
        qc_score: int | None = None,
        subtitle_status: str | None = None,
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
            ("output_path", output_path),
            ("output_duration_ms", output_duration_ms),
            ("output_width", output_width),
            ("output_height", output_height),
            ("output_fps", output_fps),
            ("output_size_bytes", output_size_bytes),
            ("qc_score", qc_score),
            ("subtitle_status", subtitle_status),
            ("error_message", error_message),
            ("warnings", json.dumps(warnings) if warnings is not None else None),
        ):
            if value is None:
                continue
            sets.append(f"{column} = COALESCE(?, {column})")
            params.append(value)
        params.append(render_run_id)
        with self._db.connect() as conn:
            conn.execute(
                f"UPDATE render_runs SET {', '.join(sets)} WHERE id = ?",
                params,
            )
        return self.get(render_run_id)

    def has_active_run(self, project_id: str) -> bool:
        with self._db.connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM render_runs
                WHERE project_id = ? AND status IN ('queued', 'running')
                LIMIT 1
                """,
                (project_id,),
            ).fetchone()
        return row is not None

    @staticmethod
    def _normalize(row: Any) -> dict[str, Any]:
        data = dict(row)
        raw = data.get("warnings")
        if raw:
            try:
                data["warnings"] = json.loads(raw)
            except json.JSONDecodeError:
                data["warnings"] = []
        else:
            data["warnings"] = []
        return data


__all__ = ["RenderRepository"]
