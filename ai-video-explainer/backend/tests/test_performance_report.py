"""Phase 8: performance report aggregates real persisted timings."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.database.connection import Database
from app.database.repositories.jobs import JobRepository
from app.database.repositories.projects import ProjectRepository
from app.models.enums import JobStatus, PipelineStage, ProjectStatus
from app.services.performance import write_performance_report
from app.services.storage import StorageService


def _make_project(settings, db: Database) -> str:
    repo = ProjectRepository(db)
    row = repo.create(
        original_filename="clip.mp4",
        language="en",
        target_duration_seconds=120,
        status=ProjectStatus.UPLOADING,
        progress=0.0,
    )
    return row["id"]


def _complete_job(db: Database, project_id: str, stage: str, seconds: int) -> None:
    """Create a completed job whose persisted timestamps span ``seconds``.

    The worker stamps started_at/completed_at itself; we then pin the
    stored values to controlled times so the report's elapsed math is
    deterministic.
    """
    repo = JobRepository(db)
    job = repo.create(project_id=project_id, stage=stage)
    repo.update_status(job["id"], status=JobStatus.RUNNING, progress=0.0)
    repo.update_status(job["id"], status=JobStatus.COMPLETED, progress=100.0)
    now = datetime.now(timezone.utc)
    with db.connect() as conn:
        conn.execute(
            "UPDATE processing_jobs SET started_at = ?, completed_at = ? WHERE id = ?",
            (
                (now - timedelta(seconds=seconds)).isoformat(timespec="seconds"),
                now.isoformat(timespec="seconds"),
                job["id"],
            ),
        )


def test_performance_report_aggregates_real_timings(settings) -> None:
    db = Database(settings.database_path).initialize()
    storage = StorageService(settings)
    storage.ensure_ready()
    project_id = _make_project(settings, db)

    # Two real jobs: preprocess 30 s, analysis 120 s (from persisted stamps).
    _complete_job(db, project_id, PipelineStage.PREPROCESS.value, seconds=30)
    _complete_job(db, project_id, PipelineStage.ANALYSIS.value, seconds=120)

    path = write_performance_report(db, storage, project_id)
    assert path.is_file()
    report = json.loads(path.read_text(encoding="utf-8"))

    assert report["project_id"] == project_id
    assert report["schema_version"] == 1
    assert report["created_at"]

    stages = {s["stage"]: s for s in report["stages"]}
    assert set(stages) == {"preprocessing", "analysis"}
    assert stages["preprocessing"]["elapsed_s"] == 30.0
    assert stages["analysis"]["elapsed_s"] == 120.0
    assert stages["preprocessing"]["status"] == "completed"
    assert report["total_pipeline_s"] == 150.0

    # No fabricated fields: RAM is explicitly marked as operator-measured.
    assert "measurement_notes" in report
    assert "peak_ram" not in report


def test_performance_report_handles_missing_timestamps(settings) -> None:
    db = Database(settings.database_path).initialize()
    storage = StorageService(settings)
    storage.ensure_ready()
    project_id = _make_project(settings, db)

    # A job that never started: elapsed must be None, report must still write.
    JobRepository(db).create(
        project_id=project_id, stage=PipelineStage.FINAL_RENDER.value
    )

    path = write_performance_report(db, storage, project_id)
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["stages"][0]["elapsed_s"] is None
    assert report["total_pipeline_s"] is None
    assert report["render_run"]["status"] is None


def test_performance_report_reruns_are_idempotent(settings) -> None:
    db = Database(settings.database_path).initialize()
    storage = StorageService(settings)
    storage.ensure_ready()
    project_id = _make_project(settings, db)
    _complete_job(db, project_id, PipelineStage.PREPROCESS.value, seconds=10)

    first = write_performance_report(db, storage, project_id)
    second = write_performance_report(db, storage, project_id)
    # Same file, regenerated from the same DB state (created_at changes).
    assert first == second
    assert json.loads(first.read_text(encoding="utf-8"))["total_pipeline_s"] == 10.0