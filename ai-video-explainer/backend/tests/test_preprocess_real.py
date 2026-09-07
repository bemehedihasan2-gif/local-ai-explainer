"""Phase 3 real-media integration tests.

Require an actual FFmpeg/FFprobe installation; skip cleanly when missing.
Verifies preprocessing against genuinely encoded videos: the analysis copy
must be a real, decodable MP4 with the configured dimensions/fps, the poster
must be a real JPEG, and the WAV must be 16 kHz mono PCM.
"""

from __future__ import annotations

import json
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from _media_helpers import (
    generate_real_video,
    real_ffmpeg_available,
    where_ffmpeg,
    where_ffprobe,
)
from app.main import create_app

pytestmark = pytest.mark.skipif(
    not real_ffmpeg_available(), reason="FFmpeg/FFprobe not installed on this machine"
)


def _wait_prepared(client: TestClient, project_id: str, timeout: float = 60.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/projects/{project_id}").json()
        if body["status"] == "prepared":
            return body
        assert body["status"] in ("preprocessing", "prepared"), body
        time.sleep(0.1)
    raise AssertionError(f"Project {project_id} did not become prepared in time")


def _probe_dimensions(ffprobe: str, path) -> dict:
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    stream = json.loads(proc.stdout)["streams"][0]
    return stream


@pytest.fixture
def client(settings, tmp_path):
    assert where_ffmpeg() is not None
    with TestClient(create_app(settings)) as test_client:
        yield test_client, tmp_path, settings


def test_real_video_preprocesses_to_valid_analysis_assets(client) -> None:
    test_client, tmp_path, settings = client
    video = generate_real_video(where_ffmpeg(), tmp_path / "real.mp4", with_audio=True)
    with video.open("rb") as fh:
        response = test_client.post(
            "/api/projects/upload",
            files={"file": ("real.mp4", fh, "video/mp4")},
            data={"language": "en", "target_duration": "180"},
        )
    assert response.status_code == 201, response.text
    project_id = response.json()["id"]

    start = test_client.post(f"/api/projects/{project_id}/preprocess")
    assert start.status_code == 201, start.text

    prepared = _wait_prepared(test_client, project_id)
    assert prepared["analysis_width"] == 160   # source is 160x120 -> not upscaled
    assert prepared["analysis_height"] == 120
    assert prepared["analysis_fps"] == 5.0
    assert prepared["audio_path"] == "audio/audio.wav"
    assert prepared["thumbnail_path"] == "thumbnails/poster.jpg"

    project_dir = settings.projects_dir / project_id

    # The analysis copy is a real, decodable H.264 MP4 at the recorded specs.
    analysis = project_dir / "analysis" / "analysis.mp4"
    assert analysis.stat().st_size > 0
    stream = _probe_dimensions(where_ffprobe(), analysis)
    assert stream["width"] == 160 and stream["height"] == 120
    num, _, den = stream["r_frame_rate"].partition("/")
    assert float(num) / float(den or 1) == 5.0

    # The poster is a genuine JPEG (starts with the JPEG magic bytes).
    poster = (project_dir / "thumbnails" / "poster.jpg").read_bytes()
    assert poster[:3] == b"\xff\xd8\xff"

    # The WAV is 16 kHz mono PCM: parse the 44-byte RIFF header.
    wav = (project_dir / "audio" / "audio.wav").read_bytes()
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    assert wav[22:24] == b"\x01\x00"      # mono
    assert wav[24:28] == b"\x80\x3e\x00\x00"  # 16000 Hz little-endian

    # Thumbnail endpoint serves the real JPEG.
    thumb = test_client.get(f"/api/projects/{project_id}/thumbnail")
    assert thumb.status_code == 200
    assert thumb.content[:3] == b"\xff\xd8\xff"

    jobs = test_client.get(f"/api/projects/{project_id}/jobs").json()
    assert jobs[0]["status"] == "completed"
    assert jobs[0]["progress"] == 100


def test_real_silent_video_preprocesses_without_wav(client) -> None:
    test_client, tmp_path, settings = client
    video = generate_real_video(where_ffmpeg(), tmp_path / "silent.mp4", with_audio=False)
    with video.open("rb") as fh:
        response = test_client.post(
            "/api/projects/upload",
            files={"file": ("silent.mp4", fh, "video/mp4")},
            data={"language": "hi", "target_duration": "120"},
        )
    assert response.status_code == 201, response.text
    assert response.json()["has_audio"] is False
    project_id = response.json()["id"]

    assert test_client.post(f"/api/projects/{project_id}/preprocess").status_code == 201
    prepared = _wait_prepared(test_client, project_id)
    assert prepared["audio_path"] is None
    assert not (settings.projects_dir / project_id / "audio" / "audio.wav").exists()
    assert (settings.projects_dir / project_id / "analysis" / "analysis.mp4").stat().st_size > 0