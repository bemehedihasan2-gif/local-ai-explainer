"""Script generation: write an original narration script in the selected
language (English/Hindi/Bengali) sized for the chosen duration
(2/3/4 minutes ≈ words-per-minute pacing), with scene cues for subtitles.

Phase 1: interface only.

Planned local provider (free/offline): the same small CPU LLM as the story
stage (script style rules are pure prompting; no cloud API ever).
"""

from __future__ import annotations

from app.ai.base import PipelineService
from app.models.enums import PipelineStage


class ScriptGenerationService(PipelineService):
    stage = PipelineStage.SCRIPT_GENERATION
    name = "Script Generation"
    planned_for = "Phase 5"
