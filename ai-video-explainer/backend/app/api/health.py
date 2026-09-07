"""GET /api/health and GET /api/system/status."""

from __future__ import annotations

import platform
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.ai.ocr import tesseract_available
from app.ai.stt import whisper_model_installed, whisper_package_installed
from app.api.deps import (
    get_database,
    get_ffmpeg_service,
    get_settings,
    get_storage_service,
    get_worker,
)
from app.config import Settings
from app.database.connection import Database
from app.services.ffmpeg import FfmpegService
from app.services.storage import StorageService
from app.services.worker import ProcessingWorker
from app.utils.logging import log_context

router = APIRouter()


@router.get("/api/health")
def health(
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Liveness probe: the app is up, DB reachable."""
    with log_context():
        return {
            "status": "ok",
            "app_name": settings.app_name,
            "version": settings.app_version,
            "environment": settings.environment,
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }


@router.get("/api/system/status")
def system_status(
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_database),
    ffmpeg: FfmpegService = Depends(get_ffmpeg_service),
    storage: StorageService = Depends(get_storage_service),
    worker: ProcessingWorker = Depends(get_worker),
) -> dict[str, object]:
    """Full capability report: python, ffmpeg, sqlite, storage, app."""
    with log_context():
        dir_entries = storage.inspect()
        storage_ok = all(e.get("exists") and e.get("writable") for e in dir_entries)

        database: dict[str, object] = {
            "path": str(settings.database_path),
            "initialized": db.initialized,
            "reachable": False,
            "sqlite_version": sqlite3.sqlite_version,
            "error": None,
        }
        try:
            with db.connect() as conn:
                conn.execute("SELECT 1").fetchone()
            database["reachable"] = True
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            database["error"] = f"{type(exc).__name__}: {exc}"

        ff = ffmpeg.detect()
        status = "ok"
        notes: list[str] = []
        if not storage_ok:
            status, notes = "degraded", ["storage"]
        if not database["reachable"]:
            status = "degraded"
            notes.append("database")
        if not ff.available:
            # ffmpeg absence alone is "degraded" (needed only Phase 2+)
            notes.append("ffmpeg")
            if status == "ok":
                status = "degraded"

        return {
            "status": status,
            "notes": notes,
            "application": {
                "name": settings.app_name,
                "version": settings.app_version,
                "environment": settings.environment,
            },
            "python": {
                "version": platform.python_version(),
                "implementation": platform.python_implementation(),
            },
            "ffmpeg": ff.to_dict(),
            "sqlite": {
                "available": True,
                "version": sqlite3.sqlite_version,
            },
            "database": database,
            "storage": {
                "ok": storage_ok,
                "free_disk_mb_outputs": storage.free_space_mb(),
                "directories": dir_entries,
            },
            "concurrency": {
                "heavy_jobs": settings.processing_concurrency,
                "note": "One heavy video-processing job at a time (8 GB RAM target).",
            },
            "worker": worker.status(),
            "limits": {
                "max_upload_size_mb": settings.max_upload_size_mb,
                "upload_chunk_size_bytes": settings.upload_chunk_size,
                "ffprobe_timeout_seconds": settings.ffprobe_timeout_seconds,
                "allowed_video_extensions": list(settings.allowed_video_extensions),
            },
            "preprocess": {
                "analysis_width": settings.analysis_width,
                "analysis_fps": settings.analysis_fps,
                "analysis_encoder_preset": settings.analysis_encoder_preset,
                "analysis_crf": settings.analysis_crf,
                "thumbnail_width": settings.thumbnail_width,
                "audio_sample_rate": settings.audio_sample_rate,
                "audio_channels": settings.audio_channels,
                "preprocess_timeout_seconds": settings.preprocess_timeout_seconds,
            },
            "analysis": {
                "scene_detection": {
                    "engine": "ffmpeg-select",
                    "available": ff.available,
                    "note": "Built-in FFmpeg scene filter (no extra package).",
                },
                "speech_to_text": {
                    "package": "installed" if whisper_package_installed() else "not_installed",
                    "model": (
                        "ready" if whisper_model_installed(settings) else "not_installed"
                    ),
                    "model_name": settings.whisper_model,
                    "device": settings.whisper_device,
                    "compute_type": settings.whisper_compute_type,
                    "language_mode": settings.whisper_language_mode,
                },
                "ocr": {
                    "available": tesseract_available(settings),
                    "binary": None,
                    "setup_hint": None,
                },
                "visual": {
                    "provider": "deterministic",
                    "note": "Brightness/blur/complexity via PIL; a local VLM can be plugged in later.",
                },
                "settings": {
                    "whisper_model": settings.whisper_model,
                    "scene_threshold": settings.scene_threshold,
                    "min_scene_duration_seconds": settings.min_scene_duration_seconds,
                    "max_scenes": settings.max_scenes,
                    "ocr_enabled": settings.ocr_enabled,
                    "ocr_frame_limit": settings.ocr_frame_limit,
                    "visual_analysis_enabled": settings.visual_analysis_enabled,
                },
            },
            "phase": "4",
            "message": (
                "Phase 4 local analysis: PREPARED videos become ANALYZED with "
                "scene boundaries, speech-to-text, OCR, deterministic visual "
                "metadata, and an aligned timeline - all on-device, no cloud "
                "APIs. Missing models (whisper/tesseract) degrade gracefully."
            ),
        }
