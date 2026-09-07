"""Subtitles: generate synchronized subtitle cues (SRT / VTT) aligned with
the *measured* narration audio, never with estimated word timing.

Phase 1: interface only. Phase 6 implements the real generator in
:mod:`app.services.subtitles` (consumed by the narration worker through
:class:`app.services.narration.NarrationService`).
"""

from __future__ import annotations

from app.ai.base import PipelineService
from app.models.enums import PipelineStage


class SubtitleService(PipelineService):
    """Registry marker for the subtitle stage.

    The Phase 1 rule is preserved: ``execute()`` (the base implementation)
    still refuses to fake work - Phase 6 subtitle generation runs through
    the single worker and is driven by actual TTS audio durations.
    """

    stage = PipelineStage.SUBTITLE_GENERATION
    name = "Subtitle Generation"
    planned_for = "Phase 6"
