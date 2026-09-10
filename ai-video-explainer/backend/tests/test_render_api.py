"""Phase 7: end-to-end API tests (NARRATION_READY -> COMPLETED).

Reuses the Phase 5/6 harness (fake ffmpeg/ffprobe + scripted fake LLM +
FakeTTS) to reach ``narration_ready`` deterministically, then injects a
fake ``RenderService`` whose ``run`` writes a placeholder final.mp4 so the
whole render lifecycle is exercised without FFmpeg: guards, run row, worker
transition to COMPLETED, manifest, idempotent reuse, failure retry.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

import test_script_api as tsa
import test_narration_api as tna
from app.main import create_app
from app.utils.errors import RenderError

pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="Fake CLI executables (shebang + chmod) require POSIX.",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class FakeRenderService:
    """Stand-in for services.render.RenderService: writes a placeholder
    final.mp4 + manifest and returns a QC-passing summary."""

    def __init__(self, settings, storage) -> None:
        self._settings = settings
        self._storage = storage

    def cleanup_artifacts(self, project_id: str) -> None:
        for rel in ("output/final.mp4", "output/final_manifest.json",
                    "render/video_plan.json"):
            try:
                (self._storage.project_path(project_id, *rel.split("/"))).unlink(
                    missing_ok=True
                )
            except OSError:
                pass

    def run(
        self, project, *, language, narration_fingerprint, render_fingerprint,
        ffmpeg_path, ffprobe_path, progress_callback=None,
    ) -> dict:
        if getattr(self, "fail_message", None):
            raise RenderError(self.fail_message)
        project_id = str(project["id"])
        final = self._storage.project_path(project_id, "output", "final.mp4")
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_bytes(b"FAKE-FINAL-MP4")
        manifest = self._storage.project_path(
            project_id, "output", "final_manifest.json"
        )
        manifest.write_text(
            json.dumps({
                "project_id": project_id,
                "created_at": _now(),
                "generation": {"language": language, "final_duration_ms": 183500},
                "media": {"path": "output/final.mp4", "duration_ms": 183500,
                          "width": 1280, "height": 720, "fps": 30,
                          "size_bytes": 14},
                "subtitles": {"burned": False, "sidecar_srt": None},
                "quality": {"quality_score": 100, "scores": {}},
            }),
            encoding="utf-8",
        )
        if progress_callback:
            progress_callback(100.0, None)
        return {
            "output_path": "output/final.mp4",
            "output_duration_ms": 183500,
            "output_width": 1280,
            "output_height": 720,
            "output_fps": 30.0,
            "output_size_bytes": final.stat().st_size,
            "qc_score": 100,
            "subtitle_status": "sidecar_only",
            "warnings": [],
        }


@pytest.fixture
def client(settings, tmp_path):
    # Fake ffmpeg/ffprobe so upload/analysis reach NARRATION_READY without
    # real binaries (mirrors the Phase 5/6 harness).
    from _media_helpers import install_fake_media_tools
    from fastapi.testclient import TestClient

    ffmpeg_path, ffprobe_path = install_fake_media_tools(tmp_path)
    settings.ffmpeg_path = ffmpeg_path
    settings.ffprobe_path = ffprobe_path
    with TestClient(create_app(settings)) as test_client:
        yield test_client, settings


@pytest.fixture
def deterministic_ai(monkeypatch):
    return tna.deterministic_ai.__wrapped__(monkeypatch)


@pytest.fixture
def fake_llm(monkeypatch):
    return tna.fake_llm.__wrapped__(monkeypatch)


@pytest.fixture
def fake_tts(monkeypatch):
    return tna.fake_tts.__wrapped__(monkeypatch)


@pytest.fixture
def fake_render(monkeypatch):
    import app.api.render as render_api_module
    import app.services.worker as worker_module

    provider = {"instance": None, "fail": None}

    def factory(settings, storage, _ref=provider):
        service = FakeRenderService(settings, storage)
        service.fail_message = _ref["fail"]
        _ref["instance"] = service
        return service

    monkeypatch.setattr(render_api_module, "RenderService", factory)
    monkeypatch.setattr(worker_module, "RenderService", factory)
    return provider


def _narration_ready_project(test_client) -> str:
    analyzed = tsa._upload_analyzed(test_client)
    project_id = analyzed["id"]
    tna._start_script(test_client, project_id)
    tna._wait_script_ready(test_client, project_id)
    tna._start_narration(test_client, project_id)
    return tna._wait_narration_ready(test_client, project_id)["id"]


def test_full_render_flow_to_completed(
    client, deterministic_ai, fake_llm, fake_tts, fake_render,
) -> None:
    test_client, settings = client
    project_id = _narration_ready_project(test_client)

    response = test_client.post(f"/api/projects/{project_id}/render", json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["idempotent"] is False
    assert body["status"] == "rendering"
    assert body["render_run_id"]
    assert body["job"]["stage"] == "final_render"

    final = tna._wait_for(
        test_client, project_id, lambda p: p["status"] == "completed"
    )
    assert final["error_message"] is None

    run = test_client.get(f"/api/projects/{project_id}/render-status").json()
    assert run["status"] == "completed"
    assert run["qc_score"] == 100
    assert run["output_path"] == "output/final.mp4"
    assert run["output_size_bytes"] == 14

    manifest = test_client.get(f"/api/projects/{project_id}/render").json()
    assert manifest["media"]["path"] == "output/final.mp4"
    assert manifest["quality"]["quality_score"] == 100

    plan = test_client.get(f"/api/projects/{project_id}/render-plan")
    assert plan.status_code == 404  # fake service does not write the plan file

    video = test_client.get(f"/api/projects/{project_id}/render/video")
    assert video.status_code == 200
    assert video.headers["content-type"].startswith("video/mp4")

    subtitles = test_client.get(f"/api/projects/{project_id}/render/subtitles")
    assert subtitles.status_code == 200
    assert " --> " in subtitles.text


def test_render_is_idempotent_on_repeat(
    client, deterministic_ai, fake_llm, fake_tts, fake_render,
) -> None:
    test_client, _ = client
    project_id = _narration_ready_project(test_client)
    first = test_client.post(f"/api/projects/{project_id}/render", json={})
    assert first.status_code == 200
    tna._wait_for(test_client, project_id, lambda p: p["status"] == "completed")

    second = test_client.post(f"/api/projects/{project_id}/render", json={})
    assert second.status_code == 200
    body = second.json()
    assert body["idempotent"] is True
    assert body["status"] == "completed"
    # No new run row was created.
    jobs = test_client.get(f"/api/projects/{project_id}/jobs").json()
    assert [j["stage"] for j in jobs].count("final_render") == 1


def test_render_rejects_project_before_narration(
    client, deterministic_ai, fake_llm, fake_tts, fake_render,
) -> None:
    test_client, _ = client
    analyzed = tsa._upload_analyzed(test_client)
    project_id = analyzed["id"]
    response = test_client.post(f"/api/projects/{project_id}/render", json={})
    assert response.status_code == 409
    assert response.json()["error"] == "render_not_ready"


def test_render_rejects_unknown_project(
    client, deterministic_ai, fake_llm, fake_tts, fake_render,
) -> None:
    test_client, _ = client
    response = test_client.post("/api/projects/nope/render", json={})
    assert response.status_code == 404


def test_render_failure_recovers_to_render_failed_and_allows_retry(
    client, deterministic_ai, fake_llm, fake_tts, fake_render,
) -> None:
    test_client, _ = client
    project_id = _narration_ready_project(test_client)

    fake_render["fail"] = "boom: encode exploded"
    first = test_client.post(f"/api/projects/{project_id}/render", json={})
    assert first.status_code == 200
    tna._wait_for(
        test_client, project_id, lambda p: p["status"] == "render_failed"
    )
    project = test_client.get(f"/api/projects/{project_id}").json()
    assert "encode exploded" in (project["error_message"] or "")

    # Clear the failure and retry from render_failed.
    fake_render["fail"] = None
    second = test_client.post(f"/api/projects/{project_id}/render", json={})
    assert second.status_code == 200
    assert second.json()["status"] == "rendering"
    tna._wait_for(test_client, project_id, lambda p: p["status"] == "completed")
