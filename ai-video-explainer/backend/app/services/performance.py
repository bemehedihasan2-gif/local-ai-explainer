"""Phase 8: per-stage performance report for a completed project.

Aggregates the timings the pipeline already records in SQLite (job
started/completed timestamps, analysis processing_seconds, narration and
render run timestamps) into a single disk-backed JSON artifact:

    analysis/performance/performance_report.json

Elapsed times are honest wall-clock measurements from persisted
timestamps - nothing is estimated or fabricated. Peak RAM is not
measurable from inside the process (the target is the user's own
Windows PC), so the report carries an explicit ``measurement_notes``
field telling the operator what to record manually on real hardware.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.database.connection import Database
from app.database.repositories.analysis import AnalysisRepository
from app.database.repositories.jobs import JobRepository
from app.database.repositories.render_runs import RenderRepository
from app.database.repositories.tts_runs import TtsRepository
from app.services.storage import StorageService
from app.utils.logging import get_logger

logger = get_logger("app.services.performance")

#: Human labels for the persisted job stages (values from models.enums).
_STAGE_LABELS = {
    "preprocess": "preprocessing",
    "analysis": "analysis",
    "script_generation": "story + script",
    "text_to_speech": "tts narration",
    "final_render": "final render",
}

_SCHEMA_VERSION = 1


def _elapsed_s(started: str | None, completed: str | None) -> float | None:
    """Wall-clock seconds between two ISO timestamps, or None when unknown."""
    if not started or not completed:
        return None
    try:
        t0 = datetime.fromisoformat(started)
        t1 = datetime.fromisoformat(completed)
    except (TypeError, ValueError):
        return None
    seconds = (t1 - t0).total_seconds()
    return round(seconds, 1) if seconds >= 0 else None


def _job_stages(db: Database, project_id: str) -> list[dict[str, Any]]:
    """Per-job rows in start order: stage label, status, elapsed seconds."""
    jobs = JobRepository(db).list_for_project(project_id)
    stages: list[dict[str, Any]] = []
    for job in sorted(jobs, key=lambda j: j.get("started_at") or ""):
        stage_value = job.get("stage") or "unknown"
        stages.append(
            {
                "stage": _STAGE_LABELS.get(stage_value, stage_value),
                "status": job.get("status"),
                "elapsed_s": _elapsed_s(
                    job.get("started_at"), job.get("completed_at")
                ),
                "started_at": job.get("started_at"),
                "completed_at": job.get("completed_at"),
            }
        )
    return stages


def write_performance_report(
    db: Database,
    storage: StorageService,
    project_id: str,
) -> Path:
    """Write ``analysis/performance/performance_report.json`` for a project.

    Safe to call repeatedly (e.g. after a render retry): the report is
    regenerated from the current database state.
    """
    stages = _job_stages(db, project_id)
    elapsed = [s["elapsed_s"] for s in stages if s["elapsed_s"] is not None]
    total_s = round(sum(elapsed), 1) if elapsed else None

    analysis_run = AnalysisRepository(db).latest_for_project(project_id)
    narration_run = TtsRepository(db).latest_for_project(project_id)
    render_run = RenderRepository(db).latest_for_project(project_id)

    narration_elapsed_s = (
        _elapsed_s(narration_run.get("started_at"), narration_run.get("completed_at"))
        if narration_run
        else None
    )
    render_elapsed_s = (
        _elapsed_s(render_run.get("started_at"), render_run.get("completed_at"))
        if render_run
        else None
    )

    report: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "project_id": project_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_pipeline_s": total_s,
        "stages": stages,
        "analysis_run": {
            "processing_seconds": (
                analysis_run.get("processing_seconds")
                if analysis_run
                else None
            ),
            "scene_count": analysis_run.get("scene_count") if analysis_run else None,
            "transcript_available": (
                analysis_run.get("transcript_available") if analysis_run else None
            ),
            "ocr_available": analysis_run.get("ocr_available") if analysis_run else None,
        },
        "narration_run": {
            "status": narration_run.get("status") if narration_run else None,
            "elapsed_s": narration_elapsed_s,
            "segment_count": (
                narration_run.get("segment_count") if narration_run else None
            ),
            "duration_ms": narration_run.get("duration_ms") if narration_run else None,
        },
        "render_run": {
            "status": render_run.get("status") if render_run else None,
            "elapsed_s": render_elapsed_s,
            "output_duration_ms": (
                render_run.get("output_duration_ms") if render_run else None
            ),
            "output_width": render_run.get("output_width") if render_run else None,
            "output_height": render_run.get("output_height") if render_run else None,
            "output_size_bytes": (
                render_run.get("output_size_bytes") if render_run else None
            ),
            "qc_score": render_run.get("qc_score") if render_run else None,
        },
        "measurement_notes": (
            "Elapsed times are wall-clock seconds between the persisted "
            "started/completed timestamps of each job. Peak RAM is not "
            "measured inside the process; record it with Task Manager on "
            "real hardware (target: 8 GB machine, one heavy job at a time)."
        ),
    }

    path = storage.project_path(
        project_id, "analysis", "performance", "performance_report.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    logger.info(
        "Performance report written for project %s (%d stages, total=%ss).",
        project_id, len(stages), total_s,
        extra={"project_id": project_id},
    )
    return path


__all__ = ["write_performance_report"]