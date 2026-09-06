"""Story understanding: fuse transcript + vision + OCR into one coherent
narrative summary of what the video is about (context auto-detection).

Phase 1: interface only.

Planned local provider (free/offline): a small CPU language model
(llama.cpp, Q4, ~1-3B) consuming the compact transcript/scene text. Prompt
templates will target: movie/film, TV, gameplay, education, news, sports,
tutorial, screen recording, lecture, social media, nature, general.
"""

from __future__ import annotations

from app.ai.base import PipelineService
from app.models.enums import PipelineStage


class StoryUnderstandingService(PipelineService):
    stage = PipelineStage.STORY_UNDERSTANDING
    name = "Story Understanding"
    planned_for = "Phase 4"
