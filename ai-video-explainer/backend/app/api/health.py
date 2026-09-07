"""GET /api/health and GET /api/system/status."""

from __future__ import annotations

import platform
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Query

from app.ai.llm import build_llm_provider
from app.ai.ocr import tesseract_available
from app.ai.stt import whisper_model_installed, whisper_package_installed
from app.ai.tts import build_tts_provider
from app.api.deps import (
    get_database,
    get_ffmpeg_service,
    get_settings,
    get_storage_service,
    get_worker,
)
from app.config import Settings
from app.database.connection import Database
from app.services.ffmpeg import FfmpegService, SETUP_HINT
from app.services.storage import StorageService
from app.services.worker import ProcessingWorker
from app.utils.logging import log_context

router = APIRouter()


def _llm_report(settings: Settings) -> dict[str, object]:
    """Public capability report for the local LLM (basename only - the
    configured model path is never exposed)."""
    provider = build_llm_provider(settings)
    detail = provider.describe()
    return {
        "provider": detail.get("provider"),
        "available": bool(detail.get("available")),
        "executable_available": bool(detail.get("executable_available", False)),
        "model_available": bool(detail.get("model_available", False)),
        "model_name": detail.get("model_name"),
        "threads": detail.get("threads"),
        "context_size": detail.get("context_size"),
        "max_tokens": detail.get("max_tokens"),
        "temperature": detail.get("temperature"),
        "setup_hint": detail.get("setup_hint"),
        "note": (
            "llama.cpp CLI + a small quantized GGUF (e.g. 1-3B Q4). Models "
            "are downloaded only by explicit user action - never silently."
        ),
    }


def _tts_report(settings: Settings) -> dict[str, object]:
    """Public capability report for the local TTS engine. Voice file paths
    are never exposed - only basenames via ``voice_id``."""
    provider = build_tts_provider(settings)
    detail = provider.describe()
    return {
        "provider": detail.get("provider"),
        "available": bool(detail.get("available")),
        "executable_available": bool(detail.get("executable_available", False)),
        "languages": detail.get("languages") or {},
        "voices": detail.get("voices") or [],
        "setup_hint": detail.get("setup_hint"),
        "settings": {
            "sample_rate": settings.tts_sample_rate,
            "channels": settings.tts_channels,
            "timeout_seconds": settings.tts_timeout_seconds,
        },
        "note": (
            "Piper CLI + per-language .onnx voice models (en/hi/bn). Voices "
            "are downloaded only by explicit user action - never silently."
        ),
    }


#: Advisory disk floor for a full pipeline run (analysis copy + narration
#: segments + final render intermediates + output). Not a hard limit - the
#: upload engine and renderer enforce their own bounds - but preflight stops
#: before expensive work when free space is below this.
_PREFLIGHT_MIN_FREE_MB = 1024

#: Languages the product supports (voice + script + subtitles).
_SUPPORTED_LANGUAGES = {"en", "hi", "bn"}


def _dependency_checks(
    settings: Settings,
    db: Database,
    ffmpeg: FfmpegService,
    storage: StorageService,
    language: str,
) -> tuple[dict[str, dict[str, object]], list[str]]:
    """Machine-readable dependency status for a pipeline run.

    Returns (checks, messages). Every check has ``ok``, ``required``,
    ``detail`` and (when failing) ``setup_hint``. Optional components
    (whisper, OCR) are reported but never block the run. No secrets or
    absolute model paths are ever included.
    """
    ff = ffmpeg.detect()
    llm_detail = build_llm_provider(settings).describe()
    tts = _tts_report(settings)
    tts_langs: dict[str, dict[str, object]] = tts.get("languages") or {}
    language_status = tts_langs.get(language) or {}

    dir_entries = storage.inspect()
    storage_ok = all(e.get("exists") and e.get("writable") for e in dir_entries)
    free_mb = storage.free_space_mb()

    db_reachable = False
    try:
        with db.connect() as conn:
            conn.execute("SELECT 1").fetchone()
        db_reachable = True
    except Exception:  # noqa: BLE001 - reported, not raised
        pass

    # Subtitle burn-in needs a Unicode font only for scripts that require
    # non-Latin glyphs (Hindi/Bengali); English falls back to libass default.
    font_ok = bool(
        (settings.subtitle_font_path and Path(settings.subtitle_font_path).is_file())
        or settings.subtitle_font_name
    )
    font_required = bool(
        settings.subtitle_burn_enabled and language in ("hi", "bn")
    )

    checks: dict[str, dict[str, object]] = {
        "python": {
            "ok": True,
            "required": True,
            "detail": f"{platform.python_implementation()} {platform.python_version()}",
            "setup_hint": None,
        },
        "database": {
            "ok": db_reachable,
            "required": True,
            "detail": "connected" if db_reachable else "unreachable",
            "setup_hint": (
                None
                if db_reachable
                else "Check backend logs; the SQLite database could not be opened."
            ),
        },
        "ffmpeg": {
            "ok": ff.ffmpeg_available,
            "required": True,
            "detail": ff.ffmpeg_version or "not found",
            "setup_hint": None if ff.ffmpeg_available else SETUP_HINT,
        },
        "ffprobe": {
            "ok": ff.ffprobe_available,
            "required": True,
            "detail": ff.ffprobe_version or "not found",
            "setup_hint": None if ff.ffprobe_available else SETUP_HINT,
        },
        "storage": {
            "ok": storage_ok,
            "required": True,
            "detail": "all directories writable" if storage_ok else "missing or not writable",
            "setup_hint": (
                None
                if storage_ok
                else "Create the project folders (data/uploads, data/projects, data/temp, data/outputs, data/cache, logs) or fix their permissions."
            ),
        },
        "disk_space": {
            "ok": free_mb >= _PREFLIGHT_MIN_FREE_MB,
            "required": True,
            "detail": f"{free_mb} MB free",
            "setup_hint": (
                None
                if free_mb >= _PREFLIGHT_MIN_FREE_MB
                else f"Free at least {_PREFLIGHT_MIN_FREE_MB} MB on the data drive before running the pipeline."
            ),
        },
        "whisper": {
            "ok": whisper_model_installed(settings),
            "required": False,
            "detail": (
                f"model '{settings.whisper_model}' ready"
                if whisper_model_installed(settings)
                else "not installed - speech will be skipped"
            ),
            "setup_hint": (
                None
                if whisper_model_installed(settings)
                else "Speech analysis is skipped. To enable it, place the faster-whisper model (see scripts\download_whisper_model.bat)."
            ),
        },
        "tesseract": {
            "ok": tesseract_available(settings),
            "required": False,
            "detail": (
                "ready"
                if tesseract_available(settings)
                else "not installed - OCR will be skipped"
            ),
            "setup_hint": (
                None
                if tesseract_available(settings)
                else "OCR is optional. Install Tesseract and, for Hindi/Bengali, its language packs (see README)."
            ),
        },
        "llm": {
            "ok": bool(llm_detail.get("available")),
            "required": True,
            "detail": (
                f"{llm_detail.get('provider')} ready - {llm_detail.get('model_name') or 'model'}"
                if llm_detail.get("available")
                else "not available - story/script generation disabled"
            ),
            "setup_hint": llm_detail.get("setup_hint"),
        },
        "piper": {
            "ok": bool(tts.get("executable_available")),
            "required": True,
            "detail": (
                "engine ready"
                if tts.get("executable_available")
                else "not found - narration disabled"
            ),
            "setup_hint": tts.get("setup_hint"),
        },
        f"voice_{language}": {
            "ok": bool(language_status.get("available")),
            "required": True,
            "detail": (
                f"{language_status.get('voice_id') or 'configured voice'} ready"
                if language_status.get("available")
                else "no voice configured for this language"
            ),
            "setup_hint": (
                None
                if language_status.get("available")
                else f"Set TTS_VOICE_{language.upper()} in .env to a Piper .onnx voice for '{language}' (see scripts\setup_piper_voices.bat)."
            ),
        },
        "subtitle_font": {
            "ok": font_ok,
            "required": font_required,
            "detail": (
                "configured"
                if font_ok
                else ("required for this language" if font_required else "not needed for English burn-in")
            ),
            "setup_hint": (
                None
                if font_ok or not font_required
                else "Hindi/Bengali burn-in needs a Unicode font: set SUBTITLE_FONT_PATH (e.g. C:\\Windows\\Fonts\\Nirmala.ttc) or SUBTITLE_FONT_NAME in .env."
            ),
        },
    }

    messages = [
        f"{name}: {entry['detail']}"
        for name, entry in checks.items()
        if not entry["ok"]
    ]
    if not messages:
        messages.append("All required components are ready.")
    return checks, messages


@router.get("/api/system/preflight")
def preflight(
    language: str = Query(default="en", pattern="^(en|hi|bn)$"),
    target_duration_seconds: int = Query(default=120, ge=60, le=600),
    settings: Settings = Depends(get_settings),
    db: Database = Depends(get_database),
    ffmpeg: FfmpegService = Depends(get_ffmpeg_service),
    storage: StorageService = Depends(get_storage_service),
) -> dict[str, object]:
    """Phase 8 pre-flight: stop before expensive processing.

    Machine-readable check of every component the pipeline needs for a run
    in ``language``. ``ok`` is True only when all *required* checks pass;
    optional components (whisper, OCR) are reported but never block.
    """
    checks, messages = _dependency_checks(
        settings, db, ffmpeg, storage, language
    )
    required_failed = [
        name for name, entry in checks.items()
        if entry["required"] and not entry["ok"]
    ]
    return {
        "ok": not required_failed,
        "language": language,
        "target_duration_seconds": target_duration_seconds,
        "supported_languages": sorted(_SUPPORTED_LANGUAGES),
        "checks": checks,
        "blocking": required_failed,
        "messages": messages,
        "note": (
            "Run this before starting a pipeline to stop early when a "
            "required local component is missing. Optional components are "
            "reported but do not block. No secrets are ever included."
        ),
    }


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
        ff_dict = ff.to_dict()
        # Phase 8: never expose absolute machine paths in the public response.
        # Basenames only - enough to see which binary was found.
        for key in ("ffmpeg", "ffprobe"):
            block = ff_dict.get(key) or {}
            p = block.get("path")
            block["path"] = Path(p).name if p else None

        def _redact_dir(entry: dict[str, object]) -> dict[str, object]:
            p = entry.get("path")
            if isinstance(p, str):
                try:
                    rel = Path(p).resolve().relative_to(settings.base_dir.resolve())
                    entry["path"] = str(rel)
                except (OSError, ValueError):
                    entry["path"] = "<local>"
            return entry

        dir_entries = [_redact_dir(dict(e)) for e in storage.inspect()]
        storage_ok = all(e.get("exists") and e.get("writable") for e in dir_entries)

        status = "ok"
        notes: list[str] = []
        if not storage_ok:
            status, notes = "degraded", ["storage"]
        if not database["reachable"]:
            status = "degraded"
            notes.append("database")
        if not ff.available:
            # ffmpeg absence alone is "degraded" (needed only Phase 2-4/7)
            notes.append("ffmpeg")
            if status == "ok":
                status = "degraded"

        tts = _tts_report(settings)
        if not tts["available"]:
            notes.append("tts")
            if status == "ok":
                status = "degraded"

        llm_detail = _llm_report(settings)
        tts_langs: dict[str, dict[str, object]] = tts.get("languages") or {}

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
            "ffmpeg": ff_dict,
            "sqlite": {
                "available": True,
                "version": sqlite3.sqlite_version,
            },
            "database": {**database, "path": settings.database_path.name},
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
            "llm": llm_detail,
            "tts": tts,
            # Phase 8: flat machine-readable dependency status (no secrets).
            "dependencies": {
                "python": True,
                "ffmpeg": ff.ffmpeg_available,
                "ffprobe": ff.ffprobe_available,
                "tesseract": tesseract_available(settings),
                "whisper_model": whisper_model_installed(settings),
                "llm": bool(llm_detail.get("available")),
                "piper": bool(tts.get("executable_available")),
                "voices": {
                    lang: bool(
                        (tts_langs.get(lang) or {}).get("available")
                    )
                    for lang in sorted(_SUPPORTED_LANGUAGES)
                },
            },
            "render": {
                "enabled": True,
                "codec": settings.video_codec,
                "preset": settings.video_preset,
                "crf": settings.video_crf,
                "output_max": [settings.output_max_width, settings.output_max_height],
                "output_fps": settings.output_fps,
                "subtitle_burn": settings.subtitle_burn_enabled,
                "subtitle_font_configured": bool(
                    settings.subtitle_font_path and Path(settings.subtitle_font_path).is_file()
                ) or bool(settings.subtitle_font_name),
                "original_audio": settings.original_audio_enabled,
                "ducking": settings.audio_ducking_enabled,
                "note": (
                    "CPU-first libx264 encode; the original audio is ducked "
                    "under the narration; subtitles burn via FFmpeg/libass. "
                    "Hindi/Bengali burn-in requires SUBTITLE_FONT_PATH."
                ),
            },
            "phase": "7",
            "message": (
                "Phase 7 final video: NARRATION_READY projects render the "
                "selected important scenes with mixed audio (narration "
                "leads, original audio ducked under it), burned-in UTF-8 "
                "subtitles and a deterministic final QC report (COMPLETED). "
                "No cloud APIs; missing binaries/fonts are reported with "
                "explicit setup instructions."
            ),
        }
