"""Phase 6 endpoints: local TTS narration + synchronized subtitles.

- ``POST /api/projects/{id}/generate-narration`` starts (or idempotently
  reuses) the narration run for a SCRIPT_READY project; the run row is
  persisted before submission and processed by the existing single worker.
- ``GET .../narration-status`` returns the run summary (honest stage
  labels from the worker).
- ``GET .../narration`` serves ``audio/narration_manifest.json``.
- ``GET .../narration/audio`` streams the assembled WAV.
- ``GET .../narration/subtitles`` returns the SRT (or ``?format=vtt``).
- ``GET .../narration/segments`` serves the measured narration timeline.

All paths are relative; absolute filesystem paths never leave the backend.
Missing voices/engines are reported with explicit setup instructions -
audio is never faked.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from app.ai.tts import build_tts_provider
from app.api.deps import (
    get_database,
    get_settings,
    get_storage_service,
    get_worker,
)
from app.config import Settings
from app.database.connection import Database
from app.database.repositories.jobs import JobRepository
from app.database.repositories.projects import ProjectRepository
from app.database.repositories.scripts import ScriptRepository
from app.database.repositories.tts_runs import TtsRepository
from app.models.enums import PipelineStage, ProjectStatus
from app.models.project import JobOut, LanguageCode
from app.services.narration import NarrationService
from app.services.storage import StorageService
from app.services.worker import ProcessingWorker
from app.utils.errors import (
    AssetNotFoundError,
    InvalidParameterError,
    NarrationConflictError,
    NarrationNotReadyError,
    TTSEngineUnavailableError,
    TTSVoiceMissingError,
)
from app.utils.fingerprints import narration_fingerprint
from app.utils.logging import get_logger, log_context

logger = get_logger("app.api.narration")
router = APIRouter()

_SUPPORTED_LANGUAGES = {"en", "hi", "bn"}


class GenerateNarrationRequest(BaseModel):
    """Language (and optional explicit voice) for the narration."""

    language: LanguageCode = "en"
    voice_id: str | None = None


def _tts_out(run: dict) -> dict:
    """Public view of a tts_runs row (no internal fingerprints/paths)."""
    return {
        "id": run["id"],
        "project_id": run["project_id"],
        "status": run["status"],
        "current_stage": run["current_stage"],
        "started_at": run["started_at"],
        "completed_at": run["completed_at"],
        "language": run["language"],
        "voice_id": run["voice_id"],
        "provider": run["provider"],
        "audio_path": run["audio_path"],
        "subtitle_path": run["subtitle_path"],
        "duration_ms": run["duration_ms"],
        "segment_count": run["segment_count"],
        "quality_score": run["quality_score"],
        "error_message": run["error_message"],
        "warnings": run.get("warnings", []),
    }


def _recover_stale_narrating(db: Database, project_id: str) -> None:
    """A project stuck in NARRATING with no live job/run (crash) is reset
    to SCRIPT_READY so narration can be started again."""
    active = (
        TtsRepository(db).has_active_run(project_id)
        or JobRepository(db).has_active_job(project_id)
    )
    if not active:
        ProjectRepository(db).update(
            project_id,
            status=ProjectStatus.SCRIPT_READY,
            progress=100.0,
            error_message=(
                "A previous narration run was interrupted before it could "
                "finish; start Generate narration again to retry."
            ),
        )


def _latest_script_fingerprint(db: Database, project_id: str) -> str | None:
    """Generation fingerprint of the newest completed Phase 5 script run."""
    run = ScriptRepository(db).latest_for_project(project_id)
    if run and run["status"] == "completed" and run.get("generation_fingerprint"):
        return run["generation_fingerprint"]
    return None


@router.post("/api/projects/{project_id}/generate-narration")
def generate_narration(
    project_id: str,
    payload: GenerateNarrationRequest,
    db: Database = Depends(get_database),
    storage: StorageService = Depends(get_storage_service),
    worker: ProcessingWorker = Depends(get_worker),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Start (or idempotently reuse) Phase 6 for a SCRIPT_READY project."""
    with log_context(project_id=project_id):
        language = payload.language
        if language not in _SUPPORTED_LANGUAGES:
            raise InvalidParameterError(
                f"Unsupported narration language '{language}'. "
                "Allowed values: en, hi, bn."
            )
        projects = ProjectRepository(db)
        row = projects.get(project_id)  # 404 when unknown
        status = row["status"]

        if status == ProjectStatus.NARRATING.value:
            _recover_stale_narrating(db, project_id)
            row = projects.get(project_id)
            if row["status"] == ProjectStatus.NARRATING.value:
                raise NarrationConflictError(
                    "Narration is already running for this project."
                )
            status = row["status"]

        if status not in (
            ProjectStatus.SCRIPT_READY.value,
            ProjectStatus.NARRATION_READY.value,
        ):
            raise NarrationNotReadyError(
                "This project cannot generate narration: its status is "
                f"'{status}'. Generate the explanation script first so the "
                "project becomes SCRIPT_READY."
            )

        script_fingerprint = _latest_script_fingerprint(db, project_id)
        if not script_fingerprint:
            raise NarrationNotReadyError(
                "No completed script exists for this project. Run "
                "POST /api/projects/{id}/generate-script first."
            )

        # Fail fast with a useful message when the local TTS engine or the
        # requested voice is unusable - never queue a guaranteed failure.
        provider = build_tts_provider(settings)
        report = provider.describe()
        if not report.get("executable_available"):
            raise TTSEngineUnavailableError(
                report.get("setup_hint")
                or "The local TTS engine is not available."
            )
        language_status = (report.get("languages") or {}).get(language)
        if not language_status or not language_status.get("available"):
            raise TTSVoiceMissingError(
                (language_status or {}).get("note")
                or f"No voice available for language '{language}'."
            )
        voice_id = payload.voice_id or (language_status or {}).get("voice_id")
        if payload.voice_id:
            voice_ids = {
                voice["voice_id"]
                for voice in report.get("voices", [])
                if voice.get("language") == language
            }
            if payload.voice_id not in voice_ids:
                raise InvalidParameterError(
                    f"Voice '{payload.voice_id}' is not configured for "
                    f"'{language}'. Configured: {sorted(voice_ids) or 'none'}."
                )

        fingerprint = narration_fingerprint(
            settings,
            script_fingerprint,
            language=language,
            voice_id=voice_id,
        )

        # Idempotent reuse: same script + language + voice + settings.
        run = TtsRepository(db).latest_for_project(project_id)
        if run and run["status"] == "completed":
            manifest_path = storage.project_path(
                project_id, "audio", "narration_manifest.json"
            )
            fresh = (
                run["generation_fingerprint"] == fingerprint
                and manifest_path.is_file()
                and (storage.project_path(project_id, "audio", "narration.wav")).is_file()
            )
            if fresh:
                logger.info(
                    "Narration run %s reused for project %s (idempotent).",
                    run["id"], project_id,
                )
                return {
                    "idempotent": True,
                    "status": "narration_ready",
                    "tts_run": _tts_out(run),
                    "message": (
                        "The existing narration matches this script, "
                        "language and voice; nothing was re-synthesized. "
                        "Change the script, language or voice to regenerate."
                    ),
                }
            logger.info(
                "Narration inputs changed; regenerating narration for "
                "project %s.",
                project_id,
            )

        # Persist before submitting: the worker only transitions
        # already-persisted states.
        NarrationService(settings, storage).cleanup_artifacts(project_id)
        run = TtsRepository(db).create(
            project_id=project_id,
            language=language,
            voice_id=voice_id,
            provider=report.get("provider", "piper"),
            script_fingerprint=script_fingerprint,
            generation_fingerprint=fingerprint,
        )
        job = JobRepository(db).create(
            project_id=project_id, stage=PipelineStage.TEXT_TO_SPEECH.value
        )
        projects.update(
            project_id,
            status=ProjectStatus.NARRATING,
            progress=0.0,
            error_message=None,
        )
        worker.submit(job["id"])
        logger.info(
            "Narration job %s queued for project %s (run %s, lang=%s, voice=%s).",
            job["id"], project_id, run["id"], language, voice_id,
        )
        return {
            "idempotent": False,
            "status": "narrating",
            "tts_run_id": run["id"],
            "job": JobOut.model_validate(job).model_dump(),
        }


@router.get("/api/projects/{project_id}/narration-status")
def narration_status(
    project_id: str,
    db: Database = Depends(get_database),
) -> dict:
    """Latest narration-run summary (404 when narration never started)."""
    with log_context(project_id=project_id):
        ProjectRepository(db).get(project_id)  # 404 when unknown
        run = TtsRepository(db).latest_for_project(project_id)
        if run is None:
            raise AssetNotFoundError(
                "No narration has been generated for this project yet. "
                "Use POST /api/projects/{id}/generate-narration."
            )
        return _tts_out(run)


@router.get("/api/projects/{project_id}/narration")
def get_narration_manifest(
    project_id: str,
    db: Database = Depends(get_database),
    storage: StorageService = Depends(get_storage_service),
) -> JSONResponse:
    """The narration manifest (relative paths only)."""
    with log_context(project_id=project_id):
        ProjectRepository(db).get(project_id)  # 404 when unknown
        path = storage.project_path(project_id, "audio", "narration_manifest.json")
        if not path.is_file():
            raise AssetNotFoundError(
                "No narration exists yet - run narration generation first."
            )
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@router.get("/api/projects/{project_id}/narration/segments")
def get_narration_segments(
    project_id: str,
    db: Database = Depends(get_database),
    storage: StorageService = Depends(get_storage_service),
) -> JSONResponse:
    """The measured narration timeline (segments with start/end ms)."""
    with log_context(project_id=project_id):
        ProjectRepository(db).get(project_id)  # 404 when unknown
        path = storage.project_path(
            project_id, "audio", "narration_timeline.json"
        )
        if not path.is_file():
            raise AssetNotFoundError(
                "No narration timeline exists yet - run narration first."
            )
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@router.get("/api/projects/{project_id}/narration/audio")
def get_narration_audio(
    project_id: str,
    db: Database = Depends(get_database),
    storage: StorageService = Depends(get_storage_service),
) -> FileResponse:
    """Stream the assembled narration WAV (mono PCM, measured duration)."""
    with log_context(project_id=project_id):
        ProjectRepository(db).get(project_id)  # 404 when unknown
        path = storage.project_path(project_id, "audio", "narration.wav")
        if not path.is_file():
            raise AssetNotFoundError(
                "No narration audio exists yet - run narration generation "
                "first."
            )
        return FileResponse(
            path, media_type="audio/wav", filename="narration.wav"
        )


@router.get("/api/projects/{project_id}/narration/subtitles")
def get_narration_subtitles(
    project_id: str,
    format: str = Query("srt", pattern="^(srt|vtt)$"),
    db: Database = Depends(get_database),
    storage: StorageService = Depends(get_storage_service),
) -> PlainTextResponse:
    """Serve the UTF-8 subtitles (SRT by default, VTT via ?format=vtt)."""
    with log_context(project_id=project_id):
        ProjectRepository(db).get(project_id)  # 404 when unknown
        filename = "subtitles.srt" if format == "srt" else "subtitles.vtt"
        path = storage.project_path(project_id, "subtitles", filename)
        if not path.is_file():
            raise AssetNotFoundError(
                "No subtitles exist yet - run narration generation first."
            )
        media_type = (
            "application/x-subrip" if format == "srt" else "text/vtt"
        )
        text = Path(path).read_text(encoding="utf-8")
        return PlainTextResponse(text, media_type=media_type)


__all__ = ["router", "GenerateNarrationRequest"]
