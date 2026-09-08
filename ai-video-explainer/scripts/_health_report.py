"""Internal helper for health_check.bat (never shows secrets).

Queries the live backend endpoints and prints a human summary:
    BACKEND / DATABASE / STORAGE / FFMPEG / FFPROBE /
    AI ANALYSIS (whisper) / OCR / LOCAL LLM / TTS / VOICES /
    RENDER / PREFLIGHT

Usage:  python _health_report.py [base_url]   (default http://127.0.0.1:8000)
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
TIMEOUT = 10


def _get(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _line(name: str, verdict: str, detail: str = "") -> None:
    print(f"{name}: {verdict}" + (f"  ({detail})" if detail else ""))


def main() -> int:
    # -- backend / health ----------------------------------------------
    try:
        health = _get("/api/health")
    except (urllib.error.URLError, OSError) as exc:
        _line("BACKEND", "FAIL", f"not reachable at {BASE} - {type(exc).__name__}")
        print("\nStart the app first: START_AI_VIDEO_EXPLAINER.bat")
        return 1

    if health.get("status") == "ok":
        _line("BACKEND", "PASS", f"v{health.get('version', '?')}")
    else:
        _line("BACKEND", "FAIL", str(health.get("status")))

    # -- system status --------------------------------------------------
    status = _get("/api/system/status")
    deps = status.get("dependencies") or {}

    db = status.get("database") or {}
    _line("DATABASE", "PASS" if db.get("reachable") else "FAIL")

    storage = status.get("storage") or {}
    _line("STORAGE", "PASS" if storage.get("ok") else "FAIL")

    _line("FFMPEG", "PASS" if deps.get("ffmpeg") else "FAIL")
    _line("FFPROBE", "PASS" if deps.get("ffprobe") else "FAIL")

    whisper = deps.get("whisper_model")
    _line("AI ANALYSIS (whisper)", "PASS" if whisper else "WARN",
          "speech-to-text ready" if whisper else "no model - speech skipped (optional)")

    tesseract = deps.get("tesseract")
    _line("OCR (tesseract)", "PASS" if tesseract else "WARN",
          "ready" if tesseract else "not installed - OCR skipped (optional)")

    llm = deps.get("llm")
    _line("LOCAL LLM", "PASS" if llm else "WARN",
          "story + script ready" if llm else "unavailable - Generate blocked")

    piper = deps.get("piper")
    _line("TTS (piper)", "PASS" if piper else "WARN",
          "narration engine ready" if piper else "unavailable - narration blocked")

    voices = deps.get("voices") or {}
    for lang in ("en", "hi", "bn"):
        _line(f"VOICE {lang.upper()}", "PASS" if voices.get(lang) else "WARN",
              "" if voices.get(lang) else "no voice configured for this language")

    render = status.get("render") or {}
    _line("RENDER", "PASS" if render.get("enabled") else "WARN",
          f"{render.get('codec')} {render.get('preset')}/CRF {render.get('crf')}, "
          f"font: {'yes' if render.get('subtitle_font_configured') else 'no (hi/bn burn-in needs it)'}")

    worker = status.get("worker") or {}
    _line("WORKER", "PASS" if worker.get("running") else "WARN",
          "single heavy job at a time")

    # -- preflight for the default English 2-minute run ------------------
    try:
        pre = _get("/api/system/preflight?language=en&target_duration_seconds=120")
        blocking = pre.get("blocking") or []
        if pre.get("ok"):
            _line("PREFLIGHT (en, 2 min)", "PASS")
        else:
            _line("PREFLIGHT (en, 2 min)", "FAIL", "blocking: " + ", ".join(blocking))
    except (urllib.error.URLError, OSError) as exc:
        _line("PREFLIGHT", "FAIL", f"query failed - {type(exc).__name__}")

    print("\nPASS = ready  WARN = optional/missing component  FAIL = blocks the pipeline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
