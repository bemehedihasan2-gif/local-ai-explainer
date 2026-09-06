"""Text-to-speech: synthesize narration audio in the selected language.

Phase 1: interface only.

Planned local provider (free/offline): a neural TTS model that supports
English, Hindi and Bengali (e.g. Coqui TTS / Piper-class models or a
small multilingual neural voice), writing WAV to disk before mixing.
"""

from __future__ import annotations

from app.ai.base import PipelineService
from app.models.enums import PipelineStage


class TextToSpeechService(PipelineService):
    stage = PipelineStage.TEXT_TO_SPEECH
    name = "Text-to-Speech"
    planned_for = "Phase 5"
