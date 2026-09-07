"""Projects endpoints.

Phase 1 scope: create/list/get/delete *records*.
Phase 2 scope: ``POST /api/projects/upload`` streams a video into project
storage, validates it with FFprobe, and returns the READY project with
metadata.
Phase 3 scope: ``POST /api/projects/{id}/preprocess`` queues the background
worker to build analysis assets (analysis copy, poster thumbnail, 16 kHz
WAV); ``GET .../jobs`` lists job history; ``GET .../thumbnail`` serves the
poster. Public responses never contain internal filesystem paths.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from fastapi.responses import FileResponse

from fastapi.responses import FileResponse, JSONResponse

from app.api.deps import (
    get_database,
    get_ffmpeg_service,
    get_settings,
    get_storage_service,
    get_worker,
)
from app.config import Settings
from app.database.connection import Database
from app.database.repositories.analysis import AnalysisRepository
from app.database.repositories.jobs import JobRepository
from app.database.repositories.projects import ProjectRepository
from app.models.enums import Language, PipelineStage, ProjectStatus
from app.models.project import JobOut, ProjectCreate, ProjectOut
from app.services.analysis import AnalysisService
from app.services.ffmpeg import FfmpegService
from app.services.storage import StorageService
from app.services.uploads import UploadService
from app.services.worker import ProcessingWorker
from app.utils.errors import (
    AnalysisConflictError,
    AnalysisNotReadyError,
    AssetNotFoundError,
    ExplainerError,
    InvalidParameterError,
    JobConflictError,
    ProjectNotReadyError,
)
from app.utils.fingerprints import analysis_config_fingerprint, preprocessing_fingerprint
from app.utils.logging import get_logger, log_context
from app.utils.paths import sanitize_filename

logger = get_logger("app.api.projects")
router = APIRouter()

#: Valid explanation durations in seconds (2 / 3 / 4 minutes).
VALID_TARGET_DURATIONS = {120, 180, 240}

#: Public project fields = ProjectOut schema (internal DB columns excluded).
_PUBLIC_FIELDS = set(ProjectOut.model_fields)


def _project_out(row: dict[str, Any]) -> ProjectOut:
    public = {name: row[name] for name in _PUBLIC_FIELDS if name in row}
    return ProjectOut.model_validate(public)


def _validate_options(language: str, target_duration: str) -> tuple[str, int]:
    """Return (language_code, target_duration_seconds) with clear errors."""
    if language not in {member.value for member in Language}:
        raise InvalidParameterError(
            f"Unsupported narration language '{language}'. "
            "Allowed values: en, hi, bn."
        )
    try:
        duration = int(target_duration)
    except (TypeError, ValueError):
        raise InvalidParameterError(
            f"Unsupported target duration '{target_duration}'. "
            "Allowed values (seconds): 120, 180, 240."
        ) from None
    if duration not in VALID_TARGET_DURATIONS:
        raise InvalidParameterError(
            f"Unsupported target duration '{duration}' seconds. "
            "Allowed values (seconds): 120, 180, 240."
        )
    return language, duration


@router.get("/api/projects", response_model=list[ProjectOut])
def list_projects(
    db: Database = Depends(get_database),
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ProjectOut]:
    with log_context():
        rows = ProjectRepository(db).list(limit=limit, offset=offset)
        return [_project_out(row) for row in rows]


@router.get("/api/projects/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: str,
    db: Database = Depends(get_database),
) -> ProjectOut:
    with log_context(project_id=project_id):
        return _project_out(ProjectRepository(db).get(project_id))


@router.post("/api/projects", response_model=ProjectOut, status_code=201)
def create_project(
    payload: ProjectCreate,
    db: Database = Depends(get_database),
) -> ProjectOut:
    with log_context():
        repo = ProjectRepository(db)
        safe_name = sanitize_filename(payload.original_filename, fallback="untitled-video")
        row = repo.create(
            original_filename=safe_name,
            stored_filename=None,  # record-only projects have no file yet
            language=payload.language_enum.value,
            target_duration_seconds=payload.target_duration_seconds,
        )
        with log_context(project_id=row["id"]):
            return _project_out(row)


@router.post("/api/projects/upload", response_model=ProjectOut, status_code=201)
def upload_project(
    file: UploadFile = File(...),
    language: str = Form("en"),
    target_duration: str = Form("180"),
    db: Database = Depends(get_database),
    ffmpeg: FfmpegService = Depends(get_ffmpeg_service),
    storage: StorageService = Depends(get_storage_service),
    settings: Settings = Depends(get_settings),
) -> ProjectOut:
    """Multipart video upload: streams to disk, validates via FFprobe."""
    language_code, duration_seconds = _validate_options(language, target_duration)
    uploader = UploadService(settings, db, ffmpeg, storage)
    try:
        row = uploader.handle_upload(
            upload_file=file,
            original_filename=file.filename,
            language=language_code,
            target_duration_seconds=duration_seconds,
        )
    except ExplainerError as exc:
        with log_context(project_id=exc.project_id):
            logger.info("Upload rejected: %s", exc.code)
        raise
    with log_context(project_id=row["id"]):
        return _project_out(row)


@router.delete("/api/projects/{project_id}", status_code=204)
def delete_project(
    project_id: str,
    db: Database = Depends(get_database),
    storage: StorageService = Depends(get_storage_service),
) -> Response:
    with log_context(project_id=project_id):
        # 404 when unknown; never touches anything outside projects/.
        ProjectRepository(db).get(project_id)
        try:
            storage.remove_project_directory(project_id)
        except ExplainerError as exc:
            logger.warning(
                "Project files could not be removed (%s); deleting the record anyway.",
                exc.message,
            )
        ProjectRepository(db).delete(project_id)
        return Response(status_code=204)


# ----------------------------------------------------------------------
# Phase 3: preprocessing (analysis assets) via the background worker
# ----------------------------------------------------------------------


@router.post(
    "/api/projects/{project_id}/preprocess",
    response_model=JobOut,
    status_code=201,
)
def start_preprocess(
    project_id: str,
    db: Database = Depends(get_database),
    worker: ProcessingWorker = Depends(get_worker),
) -> JobOut:
    """Queue the analysis-asset job for a READY project (409 otherwise).

    The job row is persisted *before* it is handed to the worker; the worker
    only transitions already-persisted states.
    """
    with log_context(project_id=project_id):
        projects = ProjectRepository(db)
        row = projects.get(project_id)  # 404 when unknown
        jobs = JobRepository(db)
        if jobs.has_active_job(project_id):
            raise JobConflictError(
                "A preprocessing job is already queued or running for this "
                "project. Wait for it to finish before starting another."
            )
        if row["status"] != ProjectStatus.READY.value:
            raise ProjectNotReadyError(
                "This project cannot be preprocessed: its status is "
                f"'{row['status']}'. Only validated, READY projects can be "
                "prepared for analysis."
            )
        job = jobs.create(
            project_id=project_id, stage=PipelineStage.PREPROCESS.value
        )
        projects.update(
            project_id,
            status=ProjectStatus.PREPROCESSING,
            progress=0.0,
            error_message=None,
        )
        worker.submit(job["id"])
        logger.info(
            "Preprocess job %s queued for project %s",
            job["id"], project_id,
            extra={"job_id": job["id"]},
        )
        return JobOut.model_validate(job)


@router.get(
    "/api/projects/{project_id}/jobs", response_model=list[JobOut]
)
def list_jobs(
    project_id: str,
    db: Database = Depends(get_database),
) -> list[JobOut]:
    """Processing-job history for a project (oldest first)."""
    with log_context(project_id=project_id):
        ProjectRepository(db).get(project_id)  # 404 when unknown
        jobs = JobRepository(db).list_for_project(project_id)
        return [JobOut.model_validate(job) for job in jobs]


# ----------------------------------------------------------------------
# Phase 4: local analysis (scenes/transcript/ocr/visual/timeline)
# ----------------------------------------------------------------------


def _analysis_out(run: dict) -> dict:
    """Public view of an analysis_results row (no internal paths)."""
    return {
        "id": run["id"],
        "project_id": run["project_id"],
        "status": run["status"],
        "current_stage": run["current_stage"],
        "started_at": run["started_at"],
        "completed_at": run["completed_at"],
        "detected_language": run["detected_language"],
        "language_probability": run["language_probability"],
        "scene_count": run["scene_count"],
        "transcript_available": run["transcript_available"],
        "ocr_available": run["ocr_available"],
        "visual_provider": run["visual_provider"],
        "processing_seconds": run["processing_seconds"],
        "error_message": run["error_message"],
        "warnings": run.get("warnings", []),
    }


def _recover_stale_analyzing(db: Database, project_id: str) -> None:
    """A project stuck in ANALYZING with no live job/run (crash) is reset
    to PREPARED so analysis can be started again."""
    active = (
        AnalysisRepository(db).has_active_run(project_id)
        or JobRepository(db).has_active_job(project_id)
    )
    if not active:
        ProjectRepository(db).update(
            project_id,
            status=ProjectStatus.PREPARED,
            progress=100.0,
            error_message=(
                "A previous analysis was interrupted before it could finish; "
                "start Analyze again to retry."
            ),
        )


@router.post("/api/projects/{project_id}/analyze")
def analyze_project(
    project_id: str,
    db: Database = Depends(get_database),
    storage: StorageService = Depends(get_storage_service),
    worker: ProcessingWorker = Depends(get_worker),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Start (or reuse) the Phase 4 analysis for a PREPARED project."""
    with log_context(project_id=project_id):
        projects = ProjectRepository(db)
        row = projects.get(project_id)  # 404 when unknown
        status = row["status"]

        if status == ProjectStatus.ANALYZING.value:
            _recover_stale_analyzing(db, project_id)
            row = projects.get(project_id)
            if row["status"] == ProjectStatus.ANALYZING.value:
                raise AnalysisConflictError(
                    "Analysis is already running for this project."
                )
            status = row["status"]

        if status == ProjectStatus.ANALYZED.value:
            # Idempotent reuse when nothing that affects the output changed.
            run = AnalysisRepository(db).latest_for_project(project_id)
            if run and run["status"] == "completed":
                fresh = (
                    run["config_fingerprint"] == analysis_config_fingerprint(settings)
                    and run["preprocessing_fingerprint"]
                    == preprocessing_fingerprint(row)
                )
                if fresh:
                    logger.info(
                        "Analysis %s reused for project %s (idempotent).",
                        run["id"], project_id,
                    )
                    return {
                        "idempotent": True,
                        "status": "analyzed",
                        "analysis": _analysis_out(run),
                        "message": "Existing analysis results are still valid; nothing was re-run.",
                    }
                logger.info(
                    "Analysis config/preprocessing changed; re-analyzing project %s.",
                    project_id,
                )
                # The old results are stale: re-run the pipeline as if the
                # project were freshly PREPARED (Phase 3 assets are kept).
                status = ProjectStatus.PREPARED.value

        if status != ProjectStatus.PREPARED.value:
            raise AnalysisNotReadyError(
                "This project cannot be analyzed: its status is "
                f"'{status}'. Run preprocessing first so the analysis assets "
                "exist (status becomes PREPARED)."
            )
        if AnalysisRepository(db).has_active_run(project_id) or JobRepository(db).has_active_job(project_id):
            raise AnalysisConflictError(
                "An analysis job is already queued or running for this project."
            )

        # Stale artifacts from an older configuration must not leak into the
        # fresh run (Phase 3 assets are never touched).
        AnalysisService(settings, storage).cleanup_artifacts(project_id)
        cfg = analysis_config_fingerprint(settings)
        pre = preprocessing_fingerprint(row)
        run = AnalysisRepository(db).create(
            project_id=project_id,
            config_fingerprint=cfg,
            preprocessing_fingerprint=pre,
        )
        job = JobRepository(db).create(
            project_id=project_id, stage=PipelineStage.ANALYSIS.value
        )
        projects.update(
            project_id,
            status=ProjectStatus.ANALYZING,
            progress=0.0,
            error_message=None,
        )
        worker.submit(job["id"])
        logger.info(
            "Analysis job %s queued for project %s (run %s).",
            job["id"], project_id, run["id"],
        )
        return {
            "idempotent": False,
            "status": "analyzing",
            "analysis_id": run["id"],
            "job": JobOut.model_validate(job).model_dump(),
        }


@router.get("/api/projects/{project_id}/analysis")
def get_analysis(
    project_id: str,
    db: Database = Depends(get_database),
) -> dict:
    """Latest analysis-run summary for a project (404 when never analyzed)."""
    with log_context(project_id=project_id):
        ProjectRepository(db).get(project_id)  # 404 when unknown
        run = AnalysisRepository(db).latest_for_project(project_id)
        if run is None:
            raise AssetNotFoundError(
                "No analysis has been run for this project yet. "
                "Use POST /api/projects/{id}/analyze after preprocessing."
            )
        return _analysis_out(run)


@router.get("/api/projects/{project_id}/timeline")
def get_timeline(
    project_id: str,
    db: Database = Depends(get_database),
    storage: StorageService = Depends(get_storage_service),
) -> JSONResponse:
    """The aligned analysis timeline (relative asset paths only)."""
    with log_context(project_id=project_id):
        ProjectRepository(db).get(project_id)  # 404 when unknown
        timeline_path = storage.project_path(project_id, "analysis", "metadata", "timeline.json")
        if not timeline_path.is_file():
            raise AssetNotFoundError(
                "No timeline exists yet - run analysis first."
            )
        import json

        return JSONResponse(
            json.loads(timeline_path.read_text(encoding="utf-8"))
        )


@router.get("/api/projects/{project_id}/analysis/frames/{scene_id}")
def analysis_frame(
    project_id: str,
    scene_id: int,
    db: Database = Depends(get_database),
    storage: StorageService = Depends(get_storage_service),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    """Serve one scene's representative frame (path-safe, int-validated)."""
    with log_context(project_id=project_id):
        ProjectRepository(db).get(project_id)  # 404 when unknown
        if not 1 <= scene_id <= settings.max_scenes:
            raise AssetNotFoundError(
                f"Scene id must be between 1 and {settings.max_scenes}."
            )
        path = storage.project_path(
            project_id, "analysis", "frames", f"scene_{scene_id - 1:03d}.jpg"
        )
        if not path.is_file():
            raise AssetNotFoundError(
                f"Representative frame for scene {scene_id} is missing."
            )
        return FileResponse(path, media_type="image/jpeg")


@router.get("/api/projects/{project_id}/thumbnail")
def project_thumbnail(
    project_id: str,
    db: Database = Depends(get_database),
    storage: StorageService = Depends(get_storage_service),
) -> FileResponse:
    """Serve the poster JPEG for a PREPARED project (path-safe)."""
    with log_context(project_id=project_id):
        row = ProjectRepository(db).get(project_id)  # 404 when unknown
        rel = row.get("thumbnail_path")
        if not rel:
            raise AssetNotFoundError(
                "No thumbnail exists yet - preprocessing has not finished "
                "for this project."
            )
        # safe_join refuses any path escaping the project folder.
        path = storage.project_path(project_id, *rel.split("/"))
        if not path.is_file():
            raise AssetNotFoundError(
                "The thumbnail file is missing on disk. Re-run preprocessing "
                "to regenerate it."
            )
        return FileResponse(path, media_type="image/jpeg")
