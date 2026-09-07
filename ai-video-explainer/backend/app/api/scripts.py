"""Phase 5 endpoints: story understanding + script generation.

- ``POST /api/projects/{id}/generate-script`` starts (or idempotently
  reuses) the story+script run; the job is persisted before submission and
  processed by the existing single worker.
- ``GET .../story-status`` returns the run summary (honest stage labels).
- ``GET .../story`` / ``selected-scenes`` / ``duration-plan`` / ``script``
  / ``script-quality`` serve the JSON artifacts (relative paths only -
  absolute filesystem paths never leave the backend).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.ai.llm import build_llm_provider
from app.api.deps import (
    get_database,
    get_settings,
    get_storage_service,
    get_worker,
)
from app.api.projects import _validate_options
from app.config import Settings
from app.database.connection import Database
from app.database.repositories.jobs import JobRepository
from app.database.repositories.projects import ProjectRepository
from app.database.repositories.scripts import ScriptRepository
from app.models.enums import PipelineStage, ProjectStatus
from app.models.project import JobOut, LanguageCode, TargetDurationSeconds
from app.services.storage import StorageService
from app.services.story import StoryService
from app.services.worker import ProcessingWorker
from app.utils.errors import (
    AssetNotFoundError,
    LLMModelMissingError,
    LLMUnavailableError,
    ScriptConflictError,
    ScriptNotReadyError,
)
from app.utils.fingerprints import (
    preprocessing_fingerprint,
    story_generation_fingerprint,
)
from app.utils.logging import get_logger, log_context

logger = get_logger("app.api.scripts")
router = APIRouter()


class GenerateScriptRequest(BaseModel):
    """Language + target duration for the explanation."""

    language: LanguageCode = "en"
    target_duration_seconds: TargetDurationSeconds = 180


def _script_out(run: dict) -> dict:
    """Public view of a script_runs row (no internal fingerprints/paths)."""
    return {
        "id": run["id"],
        "project_id": run["project_id"],
        "status": run["status"],
        "current_stage": run["current_stage"],
        "started_at": run["started_at"],
        "completed_at": run["completed_at"],
        "language": run["language"],
        "target_duration_seconds": run["target_duration_seconds"],
        "content_type": run["content_type"],
        "content_type_confidence": run["content_type_confidence"],
        "selected_scene_count": run["selected_scene_count"],
        "word_count": run["word_count"],
        "quality_score": run["quality_score"],
        "estimated_duration_seconds": run["estimated_duration_seconds"],
        "error_message": run["error_message"],
        "warnings": run.get("warnings", []),
    }


def _recover_stale_scripting(db: Database, project_id: str) -> None:
    """A project stuck in SCRIPTING with no live job/run (crash) is reset
    to ANALYZED so generation can be started again."""
    active = (
        ScriptRepository(db).has_active_run(project_id)
        or JobRepository(db).has_active_job(project_id)
    )
    if not active:
        ProjectRepository(db).update(
            project_id,
            status=ProjectStatus.ANALYZED,
            progress=100.0,
            error_message=(
                "A previous script generation was interrupted before it "
                "could finish; start Generate again to retry."
            ),
        )


@router.post("/api/projects/{project_id}/generate-script")
def generate_script(
    project_id: str,
    payload: GenerateScriptRequest,
    db: Database = Depends(get_database),
    storage: StorageService = Depends(get_storage_service),
    worker: ProcessingWorker = Depends(get_worker),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Start (or idempotently reuse) Phase 5 for an ANALYZED project."""
    with log_context(project_id=project_id):
        language_code, duration_seconds = _validate_options(
            payload.language, str(payload.target_duration_seconds)
        )
        projects = ProjectRepository(db)
        row = projects.get(project_id)  # 404 when unknown
        status = row["status"]

        if status == ProjectStatus.SCRIPTING.value:
            _recover_stale_scripting(db, project_id)
            row = projects.get(project_id)
            if row["status"] == ProjectStatus.SCRIPTING.value:
                raise ScriptConflictError(
                    "Script generation is already running for this project."
                )
            status = row["status"]

        if status not in (ProjectStatus.ANALYZED.value, ProjectStatus.SCRIPT_READY.value):
            raise ScriptNotReadyError(
                "This project cannot generate a script: its status is "
                f"'{status}'. Run analysis first so the project becomes "
                "ANALYZED."
            )

        fingerprint = story_generation_fingerprint(
            settings, row,
            language=language_code,
            target_duration_seconds=duration_seconds,
        )
        analysis_fingerprint = preprocessing_fingerprint(row)

        # Idempotent reuse: same analyzed input + options + model config.
        run = ScriptRepository(db).latest_for_project(project_id)
        if run and run["status"] == "completed":
            story_dir = storage.project_path(project_id, "analysis", "story")
            fresh = (
                run["generation_fingerprint"] == fingerprint
                and (story_dir / "story_manifest.json").is_file()
            )
            if fresh:
                logger.info(
                    "Script run %s reused for project %s (idempotent).",
                    run["id"], project_id,
                )
                return {
                    "idempotent": True,
                    "status": "script_ready",
                    "script_run": _script_out(run),
                    "message": (
                        "Existing story + script results are still valid for "
                        "this language and duration; nothing was re-run. "
                        "Change the language or duration to regenerate."
                    ),
                }
            logger.info(
                "Generation options changed; regenerating story+script for "
                "project %s.",
                project_id,
            )

        # Fail fast with a useful message when the local LLM is not usable -
        # never queue a job that is guaranteed to fail.
        provider = build_llm_provider(settings)
        if not provider.available():
            detail = provider.describe()
            hint = detail.get("setup_hint") or ""
            if not detail.get("model_available") and detail.get("provider") != "none":
                raise LLMModelMissingError(hint)
            raise LLMUnavailableError(hint)

        # Persist before submitting: the worker only transitions
        # already-persisted states.
        StoryService(settings, storage).cleanup_artifacts(project_id)
        run = ScriptRepository(db).create(
            project_id=project_id,
            language=language_code,
            target_duration_seconds=duration_seconds,
            generation_fingerprint=fingerprint,
            analysis_fingerprint=analysis_fingerprint,
        )
        job = JobRepository(db).create(
            project_id=project_id, stage=PipelineStage.SCRIPT_GENERATION.value
        )
        projects.update(
            project_id,
            status=ProjectStatus.SCRIPTING,
            progress=0.0,
            error_message=None,
        )
        worker.submit(job["id"])
        logger.info(
            "Script job %s queued for project %s (run %s, lang=%s, %ds).",
            job["id"], project_id, run["id"], language_code, duration_seconds,
        )
        return {
            "idempotent": False,
            "status": "scripting",
            "script_run_id": run["id"],
            "job": JobOut.model_validate(job).model_dump(),
        }


@router.get("/api/projects/{project_id}/story-status")
def story_status(
    project_id: str,
    db: Database = Depends(get_database),
) -> dict:
    """Latest script-run summary (404 when generation never started)."""
    with log_context(project_id=project_id):
        ProjectRepository(db).get(project_id)  # 404 when unknown
        run = ScriptRepository(db).latest_for_project(project_id)
        if run is None:
            raise AssetNotFoundError(
                "No story/script generation has been started for this "
                "project yet. Use POST /api/projects/{id}/generate-script."
            )
        return _script_out(run)


def _read_story_artifact(
    project_id: str,
    filename: str,
    db: Database,
    storage: StorageService,
    hint: str,
) -> JSONResponse:
    ProjectRepository(db).get(project_id)  # 404 when unknown
    path = storage.project_path(project_id, "analysis", "story", filename)
    if not path.is_file():
        raise AssetNotFoundError(hint)
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@router.get("/api/projects/{project_id}/story")
def get_story(
    project_id: str,
    db: Database = Depends(get_database),
    storage: StorageService = Depends(get_storage_service),
) -> JSONResponse:
    with log_context(project_id=project_id):
        return _read_story_artifact(
            project_id, "story.json", db, storage,
            "No story model exists yet - run script generation first.",
        )


@router.get("/api/projects/{project_id}/selected-scenes")
def get_selected_scenes(
    project_id: str,
    db: Database = Depends(get_database),
    storage: StorageService = Depends(get_storage_service),
) -> JSONResponse:
    with log_context(project_id=project_id):
        return _read_story_artifact(
            project_id, "selected_scenes.json", db, storage,
            "No scene selection exists yet - run script generation first.",
        )


@router.get("/api/projects/{project_id}/duration-plan")
def get_duration_plan(
    project_id: str,
    db: Database = Depends(get_database),
    storage: StorageService = Depends(get_storage_service),
) -> JSONResponse:
    with log_context(project_id=project_id):
        return _read_story_artifact(
            project_id, "duration_plan.json", db, storage,
            "No duration plan exists yet - run script generation first.",
        )


@router.get("/api/projects/{project_id}/script")
def get_script(
    project_id: str,
    db: Database = Depends(get_database),
    storage: StorageService = Depends(get_storage_service),
) -> JSONResponse:
    with log_context(project_id=project_id):
        return _read_story_artifact(
            project_id, "script.json", db, storage,
            "No script exists yet - run script generation first.",
        )


@router.get("/api/projects/{project_id}/script-quality")
def get_script_quality(
    project_id: str,
    db: Database = Depends(get_database),
    storage: StorageService = Depends(get_storage_service),
) -> JSONResponse:
    with log_context(project_id=project_id):
        return _read_story_artifact(
            project_id, "script_quality.json", db, storage,
            "No quality report exists yet - run script generation first.",
        )


__all__ = ["router", "GenerateScriptRequest"]