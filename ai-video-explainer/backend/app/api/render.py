"""Phase 7 endpoints: final video rendering (mixing + burn-in + QC).

- ``POST /api/projects/{id}/render`` starts (or idempotently reuses) the
  final render for a NARRATION_READY (or RENDER_FAILED) project; the run row
  is persisted before submission and processed by the existing worker.
- ``GET .../render-status`` -> run summary; ``GET .../render`` -> manifest;
  ``GET .../render-plan`` -> output video plan; ``GET .../render/video`` ->
  the final MP4; ``GET .../render/subtitles`` -> the sidecar SRT/VTT.

All paths are relative; the final video never leaves the backend without
going through the stream endpoint. Font/binary problems are reported with
explicit setup instructions before a render is queued.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from app.api.deps import (
    get_database,
    get_ffmpeg_service,
    get_settings,
    get_storage_service,
    get_worker,
)
from app.config import Settings
from app.database.connection import Database
from app.database.repositories.jobs import JobRepository
from app.database.repositories.projects import ProjectRepository
from app.database.repositories.render_runs import RenderRepository
from app.database.repositories.tts_runs import TtsRepository
from app.models.enums import PipelineStage, ProjectStatus
from app.models.project import JobOut, LanguageCode
from app.services.ffmpeg import FfmpegService
from app.services.render import RenderService
from app.services.render_plan import digest_document
from app.services.storage import StorageService
from app.services.worker import ProcessingWorker
from app.utils.errors import (
    AssetNotFoundError,
    RenderArtifactError,
    RenderConflictError,
    RenderNotReadyError,
    SubtitleFontMissingError,
)
from app.utils.fingerprints import narration_fingerprint, render_fingerprint
from app.utils.logging import get_logger, log_context

logger = get_logger("app.api.render")
router = APIRouter()


class RenderRequest(BaseModel):
    """No free-form options: rendering is fully determined by the stored
    artifacts + local configuration (fingerprinted for idempotency)."""

    pass  # kept as a typed request object for forward compatibility


def _render_out(run: dict) -> dict:
    return {
        "id": run["id"],
        "project_id": run["project_id"],
        "status": run["status"],
        "current_stage": run["current_stage"],
        "started_at": run["started_at"],
        "completed_at": run["completed_at"],
        "language": run["language"],
        "output_path": run["output_path"],
        "output_duration_ms": run["output_duration_ms"],
        "output_width": run["output_width"],
        "output_height": run["output_height"],
        "output_fps": run["output_fps"],
        "output_size_bytes": run["output_size_bytes"],
        "qc_score": run["qc_score"],
        "subtitle_status": run["subtitle_status"],
        "error_message": run["error_message"],
        "warnings": run.get("warnings", []),
    }


def _recover_stale_rendering(db: Database, project_id: str) -> None:
    active = (
        RenderRepository(db).has_active_run(project_id)
        or JobRepository(db).has_active_job(project_id)
    )
    if not active:
        ProjectRepository(db).update(
            project_id,
            status=ProjectStatus.NARRATION_READY,
            progress=100.0,
            error_message=(
                "A previous render was interrupted before it could finish; "
                "start Create Final Video again to retry."
            ),
        )


def _validate_subtitle_font(settings: Settings, language: str) -> None:
    """Burn-in must be able to render the script language's glyphs."""
    if not settings.subtitle_burn_enabled:
        return
    font_path = settings.subtitle_font_path
    if font_path and Path(font_path).is_file():
        return
    if settings.subtitle_font_name:
        return
    if language in ("hi", "bn"):
        raise SubtitleFontMissingError(
            "Hindi/Bengali burn-in needs a Unicode font: set "
            "SUBTITLE_FONT_PATH (e.g. C:\\Windows\\Fonts\\Nirmala.ttc or a "
            "Noto Sans font) or SUBTITLE_FONT_NAME in .env. English can "
            "render with the default sans font."
        )


def _require_artifacts(storage: StorageService, project_id: str) -> None:
    required = [
        ("analysis", "story", "selected_scenes.json"),
        ("analysis", "story", "duration_plan.json"),
        ("analysis", "story", "script.json"),
        ("audio", "narration_manifest.json"),
        ("audio", "narration_timeline.json"),
        ("audio", "narration.wav"),
        ("subtitles", "subtitles.srt"),
    ]
    missing = [
        "/".join(parts) for parts in required
        if not storage.project_path(project_id, *parts).is_file()
    ]
    if missing:
        raise RenderArtifactError(
            "Missing render inputs: " + ", ".join(missing)
            + ". Re-run the Phase 5/6 generation for this project."
        )


def _latest_narration_fingerprint(db: Database, project_id: str) -> str | None:
    run = TtsRepository(db).latest_for_project(project_id)
    if run and run["status"] == "completed" and run.get("generation_fingerprint"):
        return run["generation_fingerprint"]
    return None


@router.post("/api/projects/{project_id}/render")
def start_render(
    project_id: str,
    payload: RenderRequest = RenderRequest(),
    db: Database = Depends(get_database),
    storage: StorageService = Depends(get_storage_service),
    ffmpeg: FfmpegService = Depends(get_ffmpeg_service),
    worker: ProcessingWorker = Depends(get_worker),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Start (or idempotently reuse) Phase 7 for a NARRATION_READY project."""
    with log_context(project_id=project_id):
        projects = ProjectRepository(db)
        row = projects.get(project_id)  # 404 when unknown
        status = row["status"]

        if status == ProjectStatus.RENDERING.value:
            _recover_stale_rendering(db, project_id)
            row = projects.get(project_id)
            if row["status"] == ProjectStatus.RENDERING.value:
                raise RenderConflictError(
                    "Final rendering is already running for this project."
                )
            status = row["status"]

        if status not in (
            ProjectStatus.NARRATION_READY.value,
            ProjectStatus.RENDER_FAILED.value,
            ProjectStatus.COMPLETED.value,
        ):
            raise RenderNotReadyError(
                "This project cannot be rendered: its status is "
                f"'{status}'. Generate the narration first so the project "
                "becomes NARRATION_READY."
            )

        ffmpeg.require()  # 503 with install guidance when missing
        _require_artifacts(storage, project_id)

        language = str(row.get("language") or "en")
        _validate_subtitle_font(settings, language)

        narration_fp = _latest_narration_fingerprint(db, project_id)
        if not narration_fp:
            raise RenderNotReadyError(
                "No completed narration exists for this project - run "
                "POST /api/projects/{id}/generate-narration first."
            )

        selected_doc = json.loads(
            storage.project_path(project_id, "analysis", "story", "selected_scenes.json")
            .read_text(encoding="utf-8")
        )
        selected_digest = digest_document(selected_doc)
        fingerprint = render_fingerprint(
            settings,
            source_sha256=row.get("sha256"),
            source_duration=row.get("duration"),
            selected_scenes_digest=selected_digest,
            narration_fingerprint=narration_fp,
        )

        # Idempotent reuse: same source/selection/narration/settings.
        run = RenderRepository(db).latest_for_project(project_id)
        final_mp4 = storage.project_path(project_id, "output", "final.mp4")
        if run and run["status"] == "completed":
            fresh = (
                run["render_fingerprint"] == fingerprint
                and final_mp4.is_file()
            )
            if fresh:
                logger.info(
                    "Render run %s reused for project %s (idempotent).",
                    run["id"], project_id,
                )
                return {
                    "idempotent": True,
                    "status": "completed",
                    "render_run": _render_out(run),
                    "message": (
                        "The existing final video matches this source, "
                        "selection and narration; nothing was re-encoded. "
                        "Change the script/narration or render settings to "
                        "regenerate."
                    ),
                }
            logger.info("Render inputs changed; re-rendering project %s.", project_id)

        # Persist before submitting: the worker only transitions
        # already-persisted states.
        RenderService(settings, storage).cleanup_artifacts(project_id)
        run = RenderRepository(db).create(
            project_id=project_id,
            language=language,
            narration_fingerprint=narration_fp,
            render_fingerprint=fingerprint,
        )
        job = JobRepository(db).create(
            project_id=project_id, stage=PipelineStage.FINAL_RENDER.value
        )
        projects.update(
            project_id,
            status=ProjectStatus.RENDERING,
            progress=0.0,
            error_message=None,
        )
        worker.submit(job["id"])
        logger.info(
            "Render job %s queued for project %s (run %s).",
            job["id"], project_id, run["id"],
        )
        return {
            "idempotent": False,
            "status": "rendering",
            "render_run_id": run["id"],
            "job": JobOut.model_validate(job).model_dump(),
        }


@router.get("/api/projects/{project_id}/render-status")
def render_status(
    project_id: str,
    db: Database = Depends(get_database),
) -> dict:
    with log_context(project_id=project_id):
        ProjectRepository(db).get(project_id)  # 404 when unknown
        run = RenderRepository(db).latest_for_project(project_id)
        if run is None:
            raise AssetNotFoundError(
                "No render has been started for this project yet. Use "
                "POST /api/projects/{id}/render."
            )
        return _render_out(run)


@router.get("/api/projects/{project_id}/render")
def get_render_manifest(
    project_id: str,
    db: Database = Depends(get_database),
    storage: StorageService = Depends(get_storage_service),
) -> JSONResponse:
    with log_context(project_id=project_id):
        ProjectRepository(db).get(project_id)  # 404 when unknown
        path = storage.project_path(project_id, "output", "final_manifest.json")
        if not path.is_file():
            raise AssetNotFoundError("No final render exists yet.")
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@router.get("/api/projects/{project_id}/render-plan")
def get_render_plan(
    project_id: str,
    db: Database = Depends(get_database),
    storage: StorageService = Depends(get_storage_service),
) -> JSONResponse:
    with log_context(project_id=project_id):
        ProjectRepository(db).get(project_id)  # 404 when unknown
        path = storage.project_path(project_id, "render", "video_plan.json")
        if not path.is_file():
            raise AssetNotFoundError("No render plan exists yet.")
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@router.get("/api/projects/{project_id}/render/video")
def get_render_video(
    project_id: str,
    db: Database = Depends(get_database),
    storage: StorageService = Depends(get_storage_service),
) -> FileResponse:
    with log_context(project_id=project_id):
        ProjectRepository(db).get(project_id)  # 404 when unknown
        path = storage.project_path(project_id, "output", "final.mp4")
        if not path.is_file():
            raise AssetNotFoundError(
                "No final video exists yet - run Create Final Video first."
            )
        return FileResponse(path, media_type="video/mp4", filename="final.mp4")


@router.get("/api/projects/{project_id}/render/subtitles")
def get_render_subtitles(
    project_id: str,
    format: str = Query("srt", pattern="^(srt|vtt)$"),
    db: Database = Depends(get_database),
    storage: StorageService = Depends(get_storage_service),
) -> PlainTextResponse:
    with log_context(project_id=project_id):
        ProjectRepository(db).get(project_id)  # 404 when unknown
        filename = "subtitles.srt" if format == "srt" else "subtitles.vtt"
        path = storage.project_path(project_id, "subtitles", filename)
        if not path.is_file():
            raise AssetNotFoundError(
                "No subtitles exist for this project - run narration first."
            )
        media_type = "application/x-subrip" if format == "srt" else "text/vtt"
        return PlainTextResponse(
            Path(path).read_text(encoding="utf-8"), media_type=media_type
        )


__all__ = ["router"]
