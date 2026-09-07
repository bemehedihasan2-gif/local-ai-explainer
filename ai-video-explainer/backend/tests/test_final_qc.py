"""Phase 7: final media QC unit tests (canned ffprobe + injected probes)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.final_qc import FinalVideoQCService
from app.utils.errors import FinalQCRejectedError


def _valid_probe() -> dict:
    return {
        "streams": [
            {
                "codec_type": "video", "codec_name": "h264",
                "width": 1280, "height": 720,
                "avg_frame_rate": "30/1",
            },
            {
                "codec_type": "audio", "codec_name": "aac",
                "channels": 2, "sample_rate": "48000",
            },
        ],
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "183.5"},
    }


def _make_service(tmp_path: Path, probe=None, decode_ok: bool = True):
    probe = probe or _valid_probe()
    service = FinalVideoQCService(
        ffprobe_bin="ffprobe",
        ffmpeg_bin="ffmpeg",
        probe_fn=lambda _path, _bin: probe,
        decode_fn=lambda *_args, **_kwargs: decode_ok,
    )
    video = tmp_path / "final.mp4"
    video.write_bytes(b"fake-mp4-bytes")
    return service, video


def test_qc_accepts_valid_file(tmp_path: Path) -> None:
    service, video = _make_service(tmp_path)
    report = service.inspect(
        video,
        expected_duration_ms=183500,
        narration_duration_ms=182000,
        narration_expected=True,
        subtitle_sidecar=None,
        burn_enabled=True,
    )
    assert report["quality_score"] >= 90
    assert report["checks"]["video_stream"] == "ok"
    assert report["checks"]["decode"] == "ok"


def test_qc_rejects_file_ending_before_narration(tmp_path: Path) -> None:
    service, video = _make_service(tmp_path, decode_ok=True)
    report = service.inspect(
        video,
        expected_duration_ms=183500,
        narration_duration_ms=400000,  # narration longer than the video
        narration_expected=True,
    )
    assert report["scores"]["timeline_score"] == 0.0


def test_qc_rejects_missing_audio_stream(tmp_path: Path) -> None:
    probe = _valid_probe()
    probe["streams"] = [probe["streams"][0]]  # video only
    service, video = _make_service(tmp_path, probe=probe)
    report = service.inspect(video, narration_duration_ms=1000)
    assert report["scores"]["audio_score"] == 0.0


def test_qc_rejects_missing_video_stream(tmp_path: Path) -> None:
    probe = {"streams": [], "format": {"duration": "10.0"}}
    service, video = _make_service(tmp_path, probe=probe)
    with pytest.raises(FinalQCRejectedError):
        service.inspect(video)


def test_qc_rejects_decode_failure(tmp_path: Path) -> None:
    service, video = _make_service(tmp_path, decode_ok=False)
    with pytest.raises(FinalQCRejectedError):
        service.inspect(video, narration_duration_ms=1000)


def test_qc_rejects_empty_file(tmp_path: Path) -> None:
    service, video = _make_service(tmp_path)
    video.write_bytes(b"")
    with pytest.raises(FinalQCRejectedError):
        service.inspect(video)


def test_qc_scores_subtitle_sidecar(tmp_path: Path) -> None:
    service, video = _make_service(tmp_path)
    srt = tmp_path / "subtitles.srt"
    srt.write_text(
        "1\n00:00:00,300 --> 00:00:05,200\nHello world.\n\n"
        "2\n00:00:05,400 --> 00:00:09,800\nMore text.\n",
        encoding="utf-8",
    )
    report = service.inspect(
        video,
        expected_duration_ms=183500,
        narration_duration_ms=182000,
        subtitle_sidecar=srt,
    )
    assert report["scores"]["subtitle_score"] == 100.0
