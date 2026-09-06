"""processing_jobs table repository.

Unused by the Phase 1 API (no background worker yet) but created now so
later phases can record per-stage progress without schema changes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.database.connection import Database
from app.models.enums import JobStatus
from app.models.project import new_id
from app.utils.errors import ProjectNotFoundError
from app.utils.logging import get_logger

logger = get_logger("app.database.repositories.jobs")

_JOB_COLUMNS = (
    "id",
    "project_id",
    "stage",
    "status",
    "progress",
    "error_message",
    "started_at",
    "completed_at",
    "created_at",
    "updated_at",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def _project_exists(self, conn: Any, project_id: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM projects WHERE id = ?", (project_id,)
        ).fetchone() is not None

    def create(self, *, project_id: str, stage: str) -> dict[str, Any]:
        row_id = new_id()
        now = _now()
        with self._db.connect() as conn:
            if not self._project_exists(conn, project_id):
                raise ProjectNotFoundError(f"Project '{project_id}' was not found.")
            conn.execute(
                """
                INSERT INTO processing_jobs (
                    id, project_id, stage, status, progress, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, ?, ?)
                """,
                (row_id, project_id, stage, JobStatus.QUEUED.value, now, now),
            )
        logger.info(
            "Created job %s (stage=%s) for project %s",
            row_id, stage, project_id,
            extra={"project_id": project_id, "job_id": row_id},
        )
        return self.get(row_id)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_JOB_COLUMNS)} FROM processing_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        if row is None:
            raise ProjectNotFoundError(f"Job '{job_id}' was not found.")
        return dict(row)

    def list_for_project(self, project_id: str) -> list[dict[str, Any]]:
        with self._db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT {', '.join(_JOB_COLUMNS)} FROM processing_jobs
                WHERE project_id = ?
                ORDER BY created_at ASC
                """,
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_status(
        self,
        job_id: str,
        *,
        status: JobStatus,
        progress: float | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        now = _now()
        started = datetime.now(timezone.utc).isoformat(timespec="seconds") if status == JobStatus.RUNNING else None
        completed = now if status in (JobStatus.COMPLETED, JobStatus.FAILED) else None
        with self._db.connect() as conn:
            conn.execute(
                """
                UPDATE processing_jobs
                SET status = ?, progress = COALESCE(?, progress),
                    error_message = COALESCE(?, error_message),
                    started_at = COALESCE(?, started_at),
                    completed_at = COALESCE(?, completed_at),
                    updated_at = ?
                WHERE id = ?
                """,
                (status.value, progress, error_message, started, completed, now, job_id),
            )
        logger.info(
            "Job %s -> %s (progress=%s)",
            job_id, status.value, progress,
            extra={"job_id": job_id},
        )
        return self.get(job_id)
