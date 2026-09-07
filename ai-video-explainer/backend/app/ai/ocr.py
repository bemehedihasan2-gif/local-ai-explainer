"""OCR (Phase 4): Tesseract via pytesseract, local only.

Availability is checked honestly: the pytesseract package must be
importable AND the ``tesseract`` binary must run (explicit
``TESSERACT_PATH`` or PATH lookup). When either is missing the analysis
pipeline records ``ocr_available=false`` with a clear install message and
keeps going - OCR is never faked.

Only scene representative frames are processed (capped by
``OCR_FRAME_LIMIT``); preprocessing is deliberately light (grayscale +
autocontrast, upscale only when tiny). Images are streamed from disk one
at a time; PIL objects are dropped after each frame.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from app.ai.base import PipelineService
from app.models.enums import PipelineStage
from app.utils.errors import TesseractUnavailableError
from app.utils.logging import get_logger

logger = get_logger("app.ai.ocr")

_INSTALL_HINT = (
    "OCR is unavailable because Tesseract is not installed. Install it "
    "(Windows: 'winget install UB-Mannheim.TesseractOCR' or the gyan.dev "
    "build; Linux: apt install tesseract-ocr) and ensure 'tesseract' is on "
    "PATH, or set TESSERACT_PATH in .env. Analysis continues without OCR."
)


def _tesseract_binary(settings: Any) -> str | None:
    configured = getattr(settings, "tesseract_path", None)
    if configured:
        candidate = str(configured)
        if os.path.isfile(candidate):
            return candidate
        logger.warning("Configured TESSERACT_PATH '%s' not found; trying PATH.", candidate)
    return shutil.which("tesseract")


def tesseract_available(settings: Any) -> bool:
    """Package importable AND binary executes ``tesseract --version``."""
    if importlib.util.find_spec("pytesseract") is None:
        return False
    binary = _tesseract_binary(settings)
    if not binary:
        return False
    try:
        proc = subprocess.run(
            [binary, "--version"],
            capture_output=True, text=True, timeout=10,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class OCRService(PipelineService):
    stage = PipelineStage.OCR
    name = "OCR"
    planned_for = "Phase 4"

    def availability(self) -> dict[str, object]:
        ok = tesseract_available(self.settings)
        return {
            "available": ok,
            "binary": _tesseract_binary(self.settings),
            "setup_hint": None if ok else _INSTALL_HINT,
        }

    def run_on_frames(
        self,
        frames: list[dict[str, Any]],
        *,
        progress_callback: Callable[[float], None] | None = None,
    ) -> list[dict[str, Any]]:
        """OCR a list of ``{"timestamp", "path"}`` frames.

        Returns deduplicated results ``[{"timestamp", "text", "confidence"}]``
        in frame order. Raises :class:`TesseractUnavailableError` when
        Tesseract cannot run (callers treat that as OCR-unavailable, not a
        pipeline failure).
        """
        if not tesseract_available(self.settings):
            raise TesseractUnavailableError(_INSTALL_HINT)
        import pytesseract
        from PIL import Image, ImageOps

        pytesseract.pytesseract.tesseract_cmd = _tesseract_binary(self.settings)
        results: list[dict[str, Any]] = []
        seen_texts: set[str] = set()
        total = max(1, len(frames))
        for i, frame in enumerate(frames):
            path = Path(frame["path"])
            if not path.is_file():
                logger.warning("OCR: frame missing: %s", path)
                continue
            try:
                with Image.open(path) as image:
                    image = image.convert("L")          # grayscale
                    image = ImageOps.autocontrast(image)  # contrast
                    if min(image.size) < 200:           # tiny frames upscale
                        scale = 2 if min(image.size) < 100 else 1
                        if scale > 1:
                            image = image.resize(
                                (image.width * scale, image.height * scale)
                            )
                    data = pytesseract.image_to_data(
                        image, output_type=pytesseract.Output.DICT
                    )
            except Exception as exc:  # noqa: BLE001 - one bad frame never fails the job
                logger.warning("OCR failed for %s: %s", path.name, exc)
                continue
            finally:
                image = None  # release PIL buffers before the next frame

            words: list[tuple[str, float]] = []
            for j, text in enumerate(data.get("text", [])):
                word = (text or "").strip()
                if not word:
                    continue
                try:
                    conf = float(data["conf"][j])
                except (TypeError, ValueError):
                    conf = 0.0
                words.append((word, conf))
            if not words:
                continue
            line = " ".join(w for w, _ in words)
            confidence = round(sum(c for _, c in words) / len(words), 3)
            key = line.lower()
            if key in seen_texts:  # obvious duplicate across frames
                continue
            seen_texts.add(key)
            results.append({
                "timestamp": frame["timestamp"],
                "text": line[:500],
                "confidence": confidence,
            })
            if progress_callback is not None:
                progress_callback((i + 1) / total)

        logger.info("OCR: %d result(s) from %d frame(s).", len(results), len(frames))
        return results


__all__ = ["OCRService", "tesseract_available", "_tesseract_binary"]