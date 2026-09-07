"""Shared enums used across the API, database and future pipeline.

Values are stored in SQLite as the enum ``value`` strings, so they remain
stable even if display labels change.
"""

from __future__ import annotations

from enum import Enum


class Language(str, Enum):
    """Supported explanation languages (ISO 639-1 codes)."""

    ENGLISH = "en"
    HINDI = "hi"
    BENGALI = "bn"

    @property
    def label(self) -> str:
        return {
            Language.ENGLISH: "English",
            Language.HINDI: "Hindi",
            Language.BENGALI: "Bengali",
        }[self]


# Explanation durations offered in the UI, in minutes.
AVAILABLE_DURATIONS_MINUTES = (2, 3, 4)


class ProjectStatus(str, Enum):
    """Lifecycle of a project record."""

    CREATED = "created"  # record-only projects (Phase 1 POST /api/projects)
    UPLOADING = "uploading"  # file is being streamed to project storage
    VALIDATING = "validating"  # FFprobe inspection in progress
    READY = "ready"  # valid video + metadata stored, awaiting preprocessing
    PREPROCESSING = "preprocessing"  # Phase 3 worker is building analysis assets
    PREPARED = "prepared"  # analysis copy/thumbnail/audio ready (Phase 3 done)
    ANALYZING = "analyzing"  # Phase 4 worker is running the local analysis
    ANALYZED = "analyzed"  # scenes/transcript/ocr/visual/timeline stored (Phase 4 done)
    SCRIPTING = "scripting"  # Phase 5 worker is building story + script
    SCRIPT_READY = "script_ready"  # story/selection/plan/script/QC stored (Phase 5 done)
    NARRATING = "narrating"  # Phase 6 worker is synthesizing narration audio
    NARRATION_READY = "narration_ready"  # narration.wav + SRT + manifest stored (Phase 6 done)
    RENDERING = "rendering"  # Phase 7 worker is mixing + rendering the final MP4
    RENDER_FAILED = "render_failed"  # final render failed; retry from NARRATION_READY
    COMPLETED = "completed"  # final MP4 rendered + QC-passed (Phase 7 done)
    QUEUED = "queued"  # reserved
    PROCESSING = "processing"
    FAILED = "failed"


class JobStatus(str, Enum):
    """Lifecycle of a single processing job row."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class PipelineStage(str, Enum):
    """Ordered stages of the future end-to-end video explanation pipeline."""

    UPLOAD = "upload"
    PREPROCESS = "preprocess"
    ANALYSIS = "analysis"  # Phase 4 composite job: scenes+stt+ocr+visual+timeline
    SCENE_DETECTION = "scene_detection"
    SPEECH_TO_TEXT = "speech_to_text"
    OCR = "ocr"
    VISION_ANALYSIS = "vision_analysis"
    STORY_UNDERSTANDING = "story_understanding"
    SCRIPT_GENERATION = "script_generation"
    TEXT_TO_SPEECH = "text_to_speech"
    SUBTITLE_GENERATION = "subtitle_generation"
    AUDIO_MIXING = "audio_mixing"
    VIDEO_RENDER = "video_render"
    FINAL_RENDER = "final_render"  # Phase 7 composite job: plan->mix->burn->encode->QC
    QUALITY_CONTROL = "quality_control"

    @property
    def label(self) -> str:
        return self.value.replace("_", " ").title()
