"""Visual frame analysis (Phase 4).

Baseline is fully deterministic, CPU-light PIL math on each representative
frame: brightness, edge-energy (blur proxy), colour/tonal complexity and
dimensions. No hallucinated object/action claims - if a local vision model
is plugged in later it runs through :class:`LocalVisionProvider`, and the
pipeline records ``visual_provider`` honestly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable

from app.ai.base import PipelineService
from app.models.enums import PipelineStage
from app.utils.logging import get_logger

logger = get_logger("app.ai.vision")

DETERMINISTIC_PROVIDER_NAME = "deterministic"


class LocalVisionProvider(ABC):
    """Optional plugin interface for a small local vision model.

    A future quantized VLM / ONNX model implements ``analyze_frame`` and is
    registered on the :class:`VisionAnalysisService`; the pipeline keeps
    working with the deterministic provider when no model is installed.
    """

    name: str = "unset"

    @abstractmethod
    def available(self) -> bool:
        """True when the model is installed and loadable."""

    @abstractmethod
    def analyze_frame(self, frame_path: str | Path) -> dict[str, Any]:
        """Return extra per-frame analysis (e.g. detected objects/labels).

        Implementations must only report what the model actually produced.
        """


class DeterministicVisionProvider(LocalVisionProvider):
    """PIL-only frame stats: brightness, blur proxy, complexity, size."""

    name = DETERMINISTIC_PROVIDER_NAME

    def available(self) -> bool:
        return True  # PIL is a hard dependency of this app

    def analyze_frame(self, frame_path: str | Path) -> dict[str, Any]:
        from PIL import Image, ImageFilter, ImageStat

        path = Path(frame_path)
        with Image.open(path) as image:
            width, height = image.size
            gray = image.convert("L")
            brightness = round(ImageStat.Stat(gray).mean[0], 3)
            # Blur proxy: std-dev of the Laplacian-like edge filter. High
            # edge energy => sharper frame; low => blurry/flat frame.
            edges = gray.filter(ImageFilter.FIND_EDGES)
            edge_energy = round(ImageStat.Stat(edges).stddev[0], 3)
            # Complexity proxy: number of distinct quantized grey levels.
            histogram = gray.histogram()
            distinct = sum(1 for count in histogram if count)
            complexity = round(distinct / 256.0, 3)
            gray.close()
            edges.close()
        return {
            "width": width,
            "height": height,
            "brightness": brightness,        # 0..255
            "blur_estimate": round(max(0.0, 1.0 - min(1.0, edge_energy / 60.0)), 3),
            "edge_energy": edge_energy,
            "complexity": complexity,        # 0..1 grey-level variety
        }


class VisionAnalysisService(PipelineService):
    stage = PipelineStage.VISION_ANALYSIS
    name = "Vision Analysis"
    planned_for = "Phase 4"

    def __init__(self, settings: Any | None = None) -> None:
        super().__init__(settings)
        self._provider: LocalVisionProvider = DeterministicVisionProvider()

    @property
    def provider_name(self) -> str:
        return self._provider.name

    def register_provider(self, provider: LocalVisionProvider) -> None:
        """Plug in an optional local vision model (future Phase)."""
        if provider.available():
            self._provider = provider
            logger.info("Vision provider switched to '%s'.", provider.name)
        else:
            logger.warning("Vision provider '%s' unavailable; keeping deterministic.", provider.name)

    def analyze_frames(
        self,
        frames: list[dict[str, Any]],
        *,
        progress_callback: Callable[[float], None] | None = None,
    ) -> dict[str, Any]:
        """Return ``{"provider": name, "frames": [...]}``.

        Each frame record: timestamp, frame_path (relative), + provider
        metadata. A missing/unreadable frame is skipped, never fatal.
        """
        results: list[dict[str, Any]] = []
        total = max(1, len(frames))
        for i, frame in enumerate(frames):
            try:
                meta = self._provider.analyze_frame(frame["path"])
            except Exception as exc:  # noqa: BLE001 - one frame never fails the job
                logger.warning("Visual analysis failed for %s: %s", frame.get("path"), exc)
                continue
            results.append({
                "timestamp": frame["timestamp"],
                "frame_path": frame["rel_path"],
                **meta,
            })
            if progress_callback is not None:
                progress_callback((i + 1) / total)
        return {"provider": self._provider.name, "frames": results}


__all__ = [
    "VisionAnalysisService",
    "LocalVisionProvider",
    "DeterministicVisionProvider",
    "DETERMINISTIC_PROVIDER_NAME",
]