"""Subtitles: generate synchronized subtitle cues (SRT) aligned with the
narration audio and, optionally, the original video timeline.

Phase 1: interface only.

Planned implementation (local): a segmenter that maps narration sentences to
timestamps from the TTS audio (and/or forced alignment), producing an SRT
file that later phases burn into the final MP4.
"""

from __future__ import annotations

from app.ai.base import PipelineService
from app.models.enums import PipelineStage


class SubtitleService(PipelineService):
    stage = PipelineStage.SUBTITLE_GENERATION
    name = "Subtitle Generation"
    planned_for = "Phase 5"
