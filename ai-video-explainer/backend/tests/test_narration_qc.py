"""Phase 6: deterministic narration QC tests (0-100 score + checks)."""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import pytest

from app.services.narration_qc import run_quality_check
from app.services.subtitles import build_srt
from app.utils.errors import NarrationError

RATE = 22050


def _tone(path: Path, frames: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        step = 2 * math.pi * 440 / RATE
        payload = bytearray()
        for i in range(frames):
            sample = int(9000 * math.sin(step * i))
            payload += struct.pack("<h", sample)
        wav.writeframes(bytes(payload))


def _good_segments() -> list[dict]:
    return [
        {
            "segment_id": 1,
            "start_ms": 300,
            "end_ms": 2300,
            "text": "This is the first narration sentence.",
            "scene_ids": [1],
        },
        {
            "segment_id": 2,
            "start_ms": 2500,
            "end_ms": 5200,
            "text": "Here the second important point is made.",
            "scene_ids": [2],
        },
    ]


def _script_doc() -> dict:
    return {
        "sections": [
            {"scene_ids": [1], "purpose": "hook", "text": "first"},
            {"scene_ids": [2], "purpose": "body", "text": "second"},
        ]
    }


def _write_artifacts(tmp_path: Path, timeline_segments: list[dict]) -> tuple[Path, Path]:
    wav_path = tmp_path / "narration.wav"
    _tone(wav_path, RATE // 2)  # 500 ms... extended below if needed
    # Real duration: last timeline end in ms -> frames
    last_end = max(int(s["end_ms"]) for s in timeline_segments)
    _tone(wav_path, round(RATE * last_end / 1000))
    srt_path = tmp_path / "subtitles.srt"
    srt_path.write_text(
        build_srt(
            timeline_segments,
            max_chars_per_caption=84,
            max_chars_per_line=42,
        ),
        encoding="utf-8",
    )
    return wav_path, srt_path


class _Settings:
    def __init__(self) -> None:
        self.subtitle_max_chars_per_caption = 84
        self.subtitle_max_chars_per_line = 42
        self.audio_qc_silence_threshold_db = -60.0
        self.narration_qc_duration_tolerance_ms = 400


def test_perfect_artifacts_score_high(tmp_path: Path) -> None:
    segments = _good_segments()
    timeline = {"segments": segments, "total_duration_ms": 5200}
    wav, srt = _write_artifacts(tmp_path, segments)
    quality = run_quality_check(
        _Settings(),
        timeline=timeline,
        segments_doc={"segments": [dict(s, text=s["text"]) for s in segments]},
        script_doc=_script_doc(),
        wav_path=wav,
        srt_path=srt,
        expected_sample_rate=RATE,
    )
    assert 90 <= quality["quality_score"] <= 100
    assert quality["scores"]["timeline_score"] == 100
    assert quality["scores"]["mapping_score"] == 100
    assert quality["scores"]["duration_consistency_score"] == 100
    assert quality["audio"]["sample_rate"] == RATE


def test_qc_detects_ordering_and_overlap(tmp_path: Path) -> None:
    bad_segments = _good_segments()
    bad_segments[1]["start_ms"] = 2000  # overlaps segment 1 (ends 2300)
    timeline = {"segments": bad_segments}
    wav, srt = _write_artifacts(tmp_path, bad_segments)
    quality = run_quality_check(
        _Settings(),
        timeline=timeline,
        segments_doc={"segments": [dict(s, text=s["text"]) for s in bad_segments]},
        script_doc=_script_doc(),
        wav_path=wav,
        srt_path=srt,
        expected_sample_rate=RATE,
    )
    assert quality["scores"]["timeline_score"] < 100
    checks = {c["check"]: c for c in quality["checks"]}
    assert checks["timeline_no_overlap"]["passed"] is False


def test_qc_detects_duration_mismatch(tmp_path: Path) -> None:
    segments = _good_segments()
    timeline = {"segments": segments}
    wav, srt = _write_artifacts(tmp_path, segments)
    # Truncate the wav far below the timeline total.
    truncated = tmp_path / "short.wav"
    _tone(truncated, RATE // 10)
    truncated.replace(wav)
    quality = run_quality_check(
        _Settings(),
        timeline=timeline,
        segments_doc={"segments": segments},
        script_doc=_script_doc(),
        wav_path=wav,
        srt_path=srt,
        expected_sample_rate=RATE,
    )
    assert quality["scores"]["duration_consistency_score"] < 100
    # The audio itself is a healthy short tone; only the duration check fails.
    assert quality["scores"]["audio_score"] == 100


def test_qc_detects_missing_segment_mapping(tmp_path: Path) -> None:
    segments = _good_segments()
    timeline = {"segments": segments}
    wav, srt = _write_artifacts(tmp_path, segments)
    # segments_doc only knows segment 1: segment 2 is unmapped.
    quality = run_quality_check(
        _Settings(),
        timeline=timeline,
        segments_doc={"segments": [segments[0]]},
        script_doc=_script_doc(),
        wav_path=wav,
        srt_path=srt,
        expected_sample_rate=RATE,
    )
    assert quality["scores"]["mapping_score"] < 100


def test_qc_detects_invalid_scene_references(tmp_path: Path) -> None:
    segments = _good_segments()
    segments[1]["scene_ids"] = [999]  # no such scene in the script
    timeline = {"segments": segments}
    wav, srt = _write_artifacts(tmp_path, segments)
    quality = run_quality_check(
        _Settings(),
        timeline=timeline,
        segments_doc={"segments": segments},
        script_doc=_script_doc(),
        wav_path=wav,
        srt_path=srt,
        expected_sample_rate=RATE,
    )
    assert quality["scores"]["mapping_score"] < 100
    checks = {c["check"]: c for c in quality["checks"]}
    assert checks["mapping_scene_ids_valid"]["passed"] is False


def test_qc_rejects_missing_wav(tmp_path: Path) -> None:
    segments = _good_segments()
    timeline = {"segments": segments}
    wav = tmp_path / "missing.wav"
    srt = tmp_path / "subtitles.srt"
    srt.write_text(
        build_srt(segments, max_chars_per_caption=84, max_chars_per_line=42),
        encoding="utf-8",
    )
    with pytest.raises(NarrationError, match="WAV"):
        run_quality_check(
            _Settings(),
            timeline=timeline,
            segments_doc={"segments": segments},
            script_doc=_script_doc(),
            wav_path=wav,
            srt_path=srt,
            expected_sample_rate=RATE,
        )


def test_qc_rejects_broken_srt(tmp_path: Path) -> None:
    segments = _good_segments()
    timeline = {"segments": segments}
    wav = tmp_path / "narration.wav"
    _tone(wav, RATE // 2)
    srt = tmp_path / "subtitles.srt"
    srt.write_text("not a subtitle file", encoding="utf-8")
    with pytest.raises(NarrationError, match="SRT|srt"):
        run_quality_check(
            _Settings(),
            timeline=timeline,
            segments_doc={"segments": segments},
            script_doc=_script_doc(),
            wav_path=wav,
            srt_path=srt,
            expected_sample_rate=RATE,
        )
