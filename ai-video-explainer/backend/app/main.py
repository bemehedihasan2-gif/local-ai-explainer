"""Application entrypoint.

Run from the ``backend/`` folder:

    uvicorn app.main:app --host 127.0.0.1 --port 8000

``create_app(settings)`` is exported so tests build isolated instances with
temporary settings; ``app`` (module level) is the production instance.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.health import router as health_router
from app.api.projects import router as projects_router
from app.config import Settings, get_settings
from app.database.connection import Database
from app.services.ffmpeg import FfmpegService
from app.services.storage import StorageService
from app.services.worker import ProcessingWorker
from app.utils.errors import ExplainerError
from app.utils.logging import get_logger, setup_logging

logger = get_logger("app.main")


def register_exception_handlers(app: FastAPI) -> None:
    """Map application errors to clean JSON; never leak internals."""

    @app.exception_handler(ExplainerError)
    async def explainer_handler(request: Request, exc: ExplainerError) -> JSONResponse:
        logger.warning(
            "%s (%s) -> %d: %s",
            exc.code, exc.__class__.__name__, exc.status_code, exc.message,
            extra={"project_id": exc.project_id or "-"},
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.message, "error": exc.code},
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "An unexpected internal error occurred. "
                          "Check backend/logs/errors.log for details.",
                "error": "internal_error",
            },
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured FastAPI application (test-friendly factory)."""
    settings = settings or get_settings()

    setup_logging(
        level=settings.log_level,
        logs_dir=settings.logs_dir,
        log_max_bytes=settings.log_max_bytes,
        log_backup_count=settings.log_backup_count,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Startup: prepare managed storage and the SQLite schema.
        db = Database(settings.database_path).initialize()
        app.state.database = db
        storage = StorageService(settings)
        storage.ensure_ready()
        worker = ProcessingWorker(settings, db, FfmpegService(settings), storage)
        app.state.worker = worker
        worker.start()
        logger.info(
            "%s v%s starting (env=%s, db=%s)",
            settings.app_name, settings.app_version,
            settings.environment, settings.database_path,
        )
        try:
            yield
        finally:
            worker.stop()
            logger.info("Application shutdown complete.")

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "Phase 4: a local, zero-cost AI video explainer. Videos stream "
            "to disk, are validated with FFprobe, preprocessed into analysis "
            "assets, then analyzed entirely on-device (scene detection, "
            "speech-to-text, OCR, visual metadata, aligned timeline). "
            "Narration generation arrives in later phases."
        ),
        lifespan=lifespan,
    )

    # State for request dependencies (also set eagerly for safety).
    app.state.settings = settings
    app.state.database = Database(settings.database_path)  # replaced at startup
    app.state.ffmpeg = FfmpegService(settings)
    app.state.storage = StorageService(settings)
    app.state.worker = ProcessingWorker(
        settings, app.state.database, app.state.ffmpeg, app.state.storage
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    app.include_router(projects_router)
    register_exception_handlers(app)

    @app.get("/", include_in_schema=False)
    def root() -> dict[str, object]:
        return {
            "app": settings.app_name,
            "version": settings.app_version,
            "phase": 4,
            "api": {
                "health": "/api/health",
                "system_status": "/api/system/status",
                "projects": "/api/projects",
                "upload": "/api/projects/upload",
                "preprocess": "/api/projects/{id}/preprocess",
                "jobs": "/api/projects/{id}/jobs",
                "thumbnail": "/api/projects/{id}/thumbnail",
                "analyze": "/api/projects/{id}/analyze",
                "analysis": "/api/projects/{id}/analysis",
                "timeline": "/api/projects/{id}/timeline",
            },
            "docs": "/docs",
        }

    logger.info("Application factory ready (phase 4).")
    return app


app = create_app()
