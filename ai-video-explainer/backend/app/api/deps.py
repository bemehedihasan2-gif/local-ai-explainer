"""Shared FastAPI dependencies.

Application services live on ``app.state`` (populated during startup) and
are accessed through these dependencies, which keeps endpoints easy to test
without globals.
"""

from __future__ import annotations

from fastapi import Request

from app.config import Settings
from app.database.connection import Database
from app.services.ffmpeg import FfmpegService
from app.services.storage import StorageService
from app.services.worker import ProcessingWorker


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_ffmpeg_service(request: Request) -> FfmpegService:
    return request.app.state.ffmpeg


def get_storage_service(request: Request) -> StorageService:
    return request.app.state.storage


def get_worker(request: Request) -> ProcessingWorker:
    return request.app.state.worker
