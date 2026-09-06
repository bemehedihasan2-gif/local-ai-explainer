"""OCR: read on-screen text from frames (subtitles burned into video, UI).

Phase 1: interface only.

Planned local provider (free/offline): a lightweight ONNX OCR engine
(RapidOCR / PaddleOCR small models) on sampled frames; supports Latin and
Devanagari/Bengali scripts needed for Hindi and Bengali videos.
"""

from __future__ import annotations

from app.ai.base import PipelineService
from app.models.enums import PipelineStage


class OCRService(PipelineService):
    stage = PipelineStage.OCR
    name = "OCR"
    planned_for = "Phase 4"
