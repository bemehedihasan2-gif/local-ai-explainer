"""Vision understanding: describe what is visible in scenes.

Phase 1: interface only.

Planned local provider (free/offline): a small open vision-language model
(e.g. a Q4-quantized ~1-3B VLM) over sampled keyframes - never the full
video. Sampling keeps CPU inference time and RAM bounded.
"""

from __future__ import annotations

from app.ai.base import PipelineService
from app.models.enums import PipelineStage


class VisionAnalysisService(PipelineService):
    stage = PipelineStage.VISION_ANALYSIS
    name = "Vision Analysis"
    planned_for = "Phase 4"
