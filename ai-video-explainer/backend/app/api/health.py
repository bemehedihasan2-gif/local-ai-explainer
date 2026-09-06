"""GET /api/health and GET /api/system/status."""

from __future__ import annotations

import platform
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from app.api.deps import get_database, get_ffmpeg_service, get_settings, get_storage_service
from app.config import Settings
from app.database.connection import Database
from app.services.ffmpeg import FfmpegService
from app.services.storage import StorageService
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
            "limits": {
                "max_upload_size_mb": settings.max_upload_size_mb,
                "upload_chunk_size_bytes": settings.upload_chunk_size,
                "ffprobe_timeout_seconds": settings.ffprobe_timeout_seconds,
                "allowed_video_extensions": list(settings.allowed_video_extensions),
            },
            "phase": "2",
            "message": (
                "Phase 2 upload & validation engine: videos stream to disk, "
                "are fingerprinted (SHA-256) and validated with FFprobe. "
                "Video analysis/generation are connected in later phases."
            ),
        }
