"""Checks 1-3: backend starts, /api/health and /api/system/status work."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def test_backend_starts_and_root_responds(settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/")
        assert response.status_code == 200
        body = response.json()
        assert body["app"] == settings.app_name
        assert body["phase"] == 5
        assert "/api/health" in body["api"].values()
        assert "/api/projects/upload" in body["api"].values()
        assert "/api/projects/{id}/preprocess" in body["api"].values()
        assert "/api/projects/{id}/jobs" in body["api"].values()
        assert body["docs"] == "/docs"


def test_health_endpoint_works(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app_name"] == client.app.state.settings.app_name
    assert body["version"]
    assert body["environment"] == "test"
    assert body["time"]


def test_system_status_reports_capabilities(client: TestClient) -> None:
    response = client.get("/api/system/status")
    assert response.status_code == 200
    body = response.json()

    # App info
    assert body["application"]["name"]
    assert body["application"]["version"]

    # Python
    assert "version" in body["python"]
    assert body["python"]["implementation"]

    # FFmpeg block always has clear boolean/version fields
    ffmpeg = body["ffmpeg"]
    assert ffmpeg["ffmpeg"]["available"] is False or ffmpeg["ffmpeg"]["version"]
    assert isinstance(ffmpeg["setup_hint"], (str, type(None)))

    # SQLite
    assert body["sqlite"]["available"] is True
    assert body["sqlite"]["version"]

    # Storage directories all present & labelled
    assert body["storage"]["ok"] is True
    names = {d["name"] for d in body["storage"]["directories"]}
    assert {"uploads", "projects", "temp", "outputs", "cache", "logs"} <= names

    # Database reachable
    assert body["database"]["reachable"] is True
    assert body["database"]["initialized"] is True

    # Concurrency reflects the 8 GB target (one heavy job)
    assert body["concurrency"]["heavy_jobs"] == 1

    # Phase 3 worker is alive inside the app process
    worker = body["worker"]
    assert worker["running"] is True
    assert worker["queue_size"] == 0
    assert worker["active_job"] is None

    # Phase marker + upload limits + preprocessing settings advertised
    assert body["phase"] == "5"
    assert body["limits"]["max_upload_size_mb"] > 0
    assert body["limits"]["allowed_video_extensions"]
    assert body["limits"]["upload_chunk_size_bytes"] > 0
    assert body["preprocess"]["analysis_width"] == client.app.state.settings.analysis_width
    assert body["preprocess"]["analysis_fps"] == client.app.state.settings.analysis_fps
    assert body["preprocess"]["audio_sample_rate"] == client.app.state.settings.audio_sample_rate
    assert body["preprocess"]["preprocess_timeout_seconds"] > 0
