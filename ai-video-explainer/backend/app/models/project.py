"""Models describing projects and processing jobs for the API/database."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    AVAILABLE_DURATIONS_MINUTES,
    JobStatus,
    Language,
    PipelineStage,
    ProjectStatus,
)

#: Valid language codes accepted by the API.
LanguageCode = Literal["en", "hi", "bn"]

#: Valid explanation durations in seconds (2/3/4 minutes).
TargetDurationSeconds = Literal[120, 180, 240]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    """32-character hex project/job id (URL-safe, sortable)."""
    return uuid.uuid4().hex


class ProjectCreate(BaseModel):
    """Payload for POST /api/projects (Phase 1: record only, no upload)."""

    original_filename: str | None = Field(
        default=None,
        max_length=255,
        description="Original video file name. Optional in Phase 1.",
    )
    language: LanguageCode = "en"
    target_duration_minutes: int = Field(
        default=3, ge=2, le=4, description="Explanation length in minutes."
    )

    @property
    def language_enum(self) -> Language:
        return Language(self.language)

    @property
    def target_duration_seconds(self) -> int:
        return self.target_duration_minutes * 60


class ProjectOut(BaseModel):
    """Public project record returned to clients.

    Deliberately excludes internal filesystem details (``input_path``,
    ``stored_filename``): the Phase 2 API never leaks absolute server paths.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    original_filename: str
    #: Extracted / captured media metadata (populated after a Phase 2 upload).
    file_size: int | None
    sha256: str | None
    duration: float | None  # seconds
    width: int | None
    height: int | None
    fps: float | None  # normalized numeric fps, e.g. 29.97
    raw_fps: str | None  # original ffprobe value, e.g. "30000/1001"
    video_codec: str | None
    audio_codec: str | None
    container_format: str | None
    bitrate: int | None
    has_video: bool | None
    has_audio: bool | None
    language: Language
    target_duration_seconds: int
    status: ProjectStatus
    progress: float = Field(ge=0, le=100)
    error_message: str | None
    created_at: str
    updated_at: str


class JobOut(BaseModel):
    """Processing job row."""

    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    stage: PipelineStage
    status: JobStatus
    progress: float = Field(ge=0, le=100)
    error_message: str | None
    started_at: str | None
    completed_at: str | None
    created_at: str
    updated_at: str


__all__ = [
    "LanguageCode",
    "TargetDurationSeconds",
    "AVAILABLE_DURATIONS_MINUTES",
    "JobStatus",
    "PipelineStage",
    "ProjectStatus",
    "Language",
    "new_id",
    "ProjectCreate",
    "ProjectOut",
    "JobOut",
]
