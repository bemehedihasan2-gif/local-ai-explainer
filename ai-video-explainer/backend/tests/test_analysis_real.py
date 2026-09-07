"""Phase 4 real-media integration tests.

Skips honestly when real FFmpeg is unavailable (the sandbox has none, so
these normally skip here and run on the user's Windows machine). The video
is a tiny synthetic clip (testsrc pattern + sine tone) so preprocessing and
the whole analysis pipeline are exercised against genuine decoding.

STT/OCR stages are dependency-gated (Whisper model, Tesseract), so this
suite asserts their *honest* outcome rather than forcing a result.
"""

from __future__ import annotations

import json
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
    not real_ffmpeg_available(),
    reason="Real FFmpeg is not available in this environment.",
)


def _wait_for(test_client, project_id, predicate, timeout: float = 120.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = test_client.get(f"/api/projects/{project_id}").json()
        if predicate(last):
            return last
        time.sleep(0.1)
    raise AssertionError(f"Timed out waiting for project {project_id}; last state: {last}")


def test_real_video_end_to_end_analysis(settings, tmp_path) -> None:
    settings.ffmpeg_path = where_ffmpeg()
    settings.ffprobe_path = where_ffprobe()
    video = generate_real_video(
        settings.ffmpeg_path, tmp_path / "clip.mp4",
        duration_s=1.5, with_audio=True,
    )

    with TestClient(create_app(settings)) as client:
        with open(video, "rb") as handle:
            response = client.post(
                "/api/projects/upload",
                files={"file": ("clip.mp4", handle, "video/mp4")},
                data={"language": "en", "target_duration": "180"},
            )
        assert response.status_code == 201, response.text
        project_id = response.json()["id"]

        assert client.post(f"/api/projects/{project_id}/preprocess").status_code == 201
        prepared = _wait_for(
            client, project_id, lambda body: body["status"] == "prepared"
        )
        assert prepared["analysis_width"] == 160  # never upscaled above source

        assert client.post(f"/api/projects/{project_id}/analyze").status_code == 200
        analyzed = _wait_for(
            client, project_id, lambda body: body["status"] == "analyzed"
        )
        assert analyzed["error_message"] is None

        run = client.get(f"/api/projects/{project_id}/analysis").json()
        assert run["status"] == "completed"
        assert run["scene_count"] >= 1
        # STT/OCR outcomes are honest per machine (model/binary present?).
        assert run["transcript_available"] in (True, False)
        assert run["ocr_available"] in (True, False)
        assert run["visual_provider"] == "deterministic"

        metadata = settings.projects_dir / project_id / "analysis" / "metadata"
        scenes = json.loads((metadata / "scenes.json").read_text(encoding="utf-8"))
        assert len(scenes["scenes"]) == run["scene_count"]
        duration = float(prepared["duration"])
        for scene in scenes["scenes"]:
            assert scene["end"] > scene["start"] >= 0
            assert scene["end"] <= duration + 0.05
            assert scene["start"] <= scene["representative_timestamp"] <= scene["end"]

        timeline = client.get(f"/api/projects/{project_id}/timeline")
        assert timeline.status_code == 200
        assert timeline.json()["scene_count"] == run["scene_count"]

        manifest = json.loads(
            (metadata / "analysis_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["results"]["scene_count"] == run["scene_count"]
        assert str(settings.base_dir) not in json.dumps(manifest)

        # Real extracted frame is a genuine JPEG the server can serve.
        frame = client.get(f"/api/projects/{project_id}/analysis/frames/1")
        assert frame.status_code == 200
        assert frame.content[:2] == b"\xff\xd8"  # JPEG magic


def test_real_silent_video_analysis(settings, tmp_path) -> None:
    """A video without audio must still reach ANALYZED via visual stages."""
    settings.ffmpeg_path = where_ffmpeg()
    settings.ffprobe_path = where_ffprobe()
    video = generate_real_video(
        settings.ffmpeg_path, tmp_path / "silent.mp4",
        duration_s=1.0, with_audio=False,
    )

    with TestClient(create_app(settings)) as client:
        with open(video, "rb") as handle:
            response = client.post(
                "/api/projects/upload",
                files={"file": ("silent.mp4", handle, "video/mp4")},
                data={"language": "hi", "target_duration": "120"},
            )
        assert response.status_code == 201, response.text
        project_id = response.json()["id"]
        assert response.json()["has_audio"] is False

        assert client.post(f"/api/projects/{project_id}/preprocess").status_code == 201
        _wait_for(client, project_id, lambda body: body["status"] == "prepared")
        assert client.post(f"/api/projects/{project_id}/analyze").status_code == 200
        analyzed = _wait_for(
            client, project_id, lambda body: body["status"] == "analyzed"
        )
        assert analyzed["error_message"] is None
        run = client.get(f"/api/projects/{project_id}/analysis").json()
        assert run["transcript_available"] is False
        assert any("no audio" in w.lower() for w in run["warnings"])
        assert run["scene_count"] >= 1