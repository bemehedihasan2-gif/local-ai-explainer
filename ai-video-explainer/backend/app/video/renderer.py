"""Final video render: mix narration with (optionally) original audio, burn
subtitles, and encode the final MP4 via FFmpeg.

Phase 1: interface only. Later phases implement the actual FFmpeg command
construction (stream-copy-friendly, memory-bounded filters only).

Note: per the architecture doc this stage deliberately belongs to the
*video* domain but is registered as a pipeline service so the orchestrator
treats every stage uniformly.
"""

from __future__ import annotations

from app.ai.base import PipelineService
from app.models.enums import PipelineStage


class VideoRenderService(PipelineService):
    stage = PipelineStage.VIDEO_RENDER
    name = "Video Render"
    planned_for = "Phase 5"
