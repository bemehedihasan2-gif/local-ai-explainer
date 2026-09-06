"""Projects endpoints.

Phase 1 scope: create/list/get/delete *records*.
Phase 2 scope: ``POST /api/projects/upload`` streams a video into project
storage, validates it with FFprobe, and returns the READY project with
metadata. Public responses never contain internal filesystem paths.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile

from app.api.deps import (
    get_database,
    get_ffmpeg_service,
    get_settings,
    get_storage_service,
)
from app.config import Settings
from app.database.connection import Database
from app.database.repositories.projects import ProjectRepository
from app.models.enums import Language
from app.models.project import ProjectCreate, ProjectOut
from app.services.ffmpeg import FfmpegService
from app.services.storage import StorageService
from app.services.uploads import UploadService
from app.utils.errors import ExplainerError, InvalidParameterError
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
