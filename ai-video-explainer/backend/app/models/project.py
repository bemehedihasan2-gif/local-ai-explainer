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
    """Full project record returned to clients."""

    model_config = ConfigDict(extra="forbid")

    id: str
    original_filename: str
    stored_filename: str | None
    input_path: str | None
    duration: float | None
    width: int | None
    height: int | None
    fps: float | None
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
