"""Pipeline service registry.

Later phases will look up a stage's implementation here instead of importing
concrete classes all over the codebase, and will add entries (e.g. scene
detection, audio mixing, quality control) as those stages are implemented.
"""

from __future__ import annotations

from app.ai.base import PipelineService
from app.ai.ocr import OCRService
from app.ai.script import ScriptGenerationService
from app.ai.story import StoryUnderstandingService
from app.ai.stt import SpeechToTextService
from app.ai.subtitles import SubtitleService
from app.ai.tts import TextToSpeechService
from app.ai.vision import VisionAnalysisService
from app.models.enums import PipelineStage
from app.video.renderer import VideoRenderService

#: stage -> service class. Order follows the pipeline (render last).
REGISTRY: dict[PipelineStage, type[PipelineService]] = {
    PipelineStage.SPEECH_TO_TEXT: SpeechToTextService,
    PipelineStage.OCR: OCRService,
    PipelineStage.VISION_ANALYSIS: VisionAnalysisService,
    PipelineStage.STORY_UNDERSTANDING: StoryUnderstandingService,
    PipelineStage.SCRIPT_GENERATION: ScriptGenerationService,
    PipelineStage.TEXT_TO_SPEECH: TextToSpeechService,
    PipelineStage.SUBTITLE_GENERATION: SubtitleService,
    PipelineStage.VIDEO_RENDER: VideoRenderService,
}


def service_for(stage: PipelineStage | str) -> type[PipelineService]:
    """Resolve a stage name/value to its service class."""
    key = PipelineStage(stage) if isinstance(stage, str) else stage
    if key not in REGISTRY:
        raise KeyError(f"No service registered for pipeline stage '{key.value}'.")
    return REGISTRY[key]


def instantiate(settings) -> list[PipelineService]:
    """Create one instance per registered stage (used by status endpoint)."""
    return [cls(settings=settings) for cls in REGISTRY.values()]
