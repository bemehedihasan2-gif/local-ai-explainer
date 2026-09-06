"""Speech-to-text (transcribe speech to text with timestamps).

Phase 1: interface only.

Planned local provider (free/offline): faster-whisper with the ``small``
model quantized to int8, CPU-only, ~1-2 GB RAM - a reasonable fit for the
8 GB target when nothing else heavy is loaded. English/Hindi/Bengali are
all supported by Whisper.
"""

from __future__ import annotations

from app.ai.base import PipelineService
from app.models.enums import PipelineStage


class SpeechToTextService(PipelineService):
    stage = PipelineStage.SPEECH_TO_TEXT
    name = "Speech-to-Text"
    planned_for = "Phase 3"
