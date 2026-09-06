"""Phase 2 real-media integration tests.

These require an actual FFmpeg/FFprobe installation and skip cleanly when it
is missing (e.g. CI or a machine that has not installed FFmpeg yet). They
verify the same upload engine against genuinely encoded MP4/MKV fixtures.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from _media_helpers import (
    generate_real_video,
    real_ffmpeg_available,
    where_ffmpeg,
)
from app.main import create_app

pytestmark = pytest.mark.skipif(
    not real_ffmpeg_available(), reason="FFmpeg/FFprobe not installed on this machine"
)


def _upload(
    client: TestClient, video_path, filename: str, language: str = "en"
):
    with video_path.open("rb") as fh:
        return client.post(
            "/api/projects/upload",
            files={"file": (filename, fh, "video/mp4")},
            data={"language": language, "target_duration": "180"},
        )


@pytest.fixture
def client(settings, tmp_path):
    assert where_ffmpeg() is not None
    with TestClient(create_app(settings)) as test_client:
        yield test_client, tmp_path, settings


def test_real_mp4_upload_with_audio(client) -> None:
    test_client, tmp_path, _ = client
    video = generate_real_video(
        where_ffmpeg(), tmp_path / "real.mp4", with_audio=True
    )
    response = _upload(test_client, video, "real.mp4", language="hi")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["has_video"] is True
    assert body["has_audio"] is True
    assert body["width"] == 160
    assert body["height"] == 120
    assert body["duration"] and 0.5 < body["duration"] <= 2.0
    assert body["fps"] and body["fps"] > 0
    assert body["video_codec"] == "h264"
    assert body["audio_codec"] == "aac"
    assert body["file_size"] == video.stat().st_size


def test_real_mkv_and_silent_video_accepted(client) -> None:
    test_client, tmp_path, _ = client
    mkv = generate_real_video(where_ffmpeg(), tmp_path / "real.mkv", with_audio=True)
    silent = generate_real_video(
        where_ffmpeg(), tmp_path / "silent.mp4", with_audio=False
    )

    response = _upload(test_client, mkv, "real.mkv")
    assert response.status_code == 201, response.text
    assert response.json()["container_format"] == "matroska"

    response = _upload(test_client, silent, "silent.mp4")
    assert response.status_code == 201, response.text
    assert response.json()["has_audio"] is False
    assert response.json()["audio_codec"] is None


def test_real_garbage_bytes_rejected(client) -> None:
    test_client, tmp_path, settings = client
    fake = tmp_path / "fake.mp4"
    fake.write_bytes(b"\x00\x01\x02 not a real mp4 at all " * 200)
    response = _upload(test_client, fake, "fake.mp4")
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_video"
    # Cleanup: FAILED row exists but no files remain on disk.
    rows = test_client.get("/api/projects").json()
    assert rows[0]["status"] == "failed"
    assert not list(settings.projects_dir.iterdir())
