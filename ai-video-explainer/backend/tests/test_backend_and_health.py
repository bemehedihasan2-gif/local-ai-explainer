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
        assert body["phase"] == 7
        assert "/api/health" in body["api"].values()
        assert "/api/projects/upload" in body["api"].values()
        assert "/api/projects/{id}/preprocess" in body["api"].values()
        assert "/api/projects/{id}/jobs" in body["api"].values()
        assert "/api/projects/{id}/generate-narration" in body["api"].values()
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
    assert body["phase"] == "7"
    assert body["limits"]["max_upload_size_mb"] > 0
    assert body["limits"]["allowed_video_extensions"]
    assert body["limits"]["upload_chunk_size_bytes"] > 0
    assert body["preprocess"]["analysis_width"] == client.app.state.settings.analysis_width
    assert body["preprocess"]["analysis_fps"] == client.app.state.settings.analysis_fps
    assert body["preprocess"]["audio_sample_rate"] == client.app.state.settings.audio_sample_rate
    assert body["preprocess"]["preprocess_timeout_seconds"] > 0

    # Phase 6 TTS report: engine + per-language voice availability.
    tts = body["tts"]
    assert tts["provider"] == "piper"
    assert "available" in tts and "executable_available" in tts
    assert set(tts["languages"]) == {"en", "hi", "bn"}
    for language_status in tts["languages"].values():
        assert set(language_status) >= {"voice_id", "available", "configured", "model_available"}
    assert isinstance(tts["voices"], list)
    assert isinstance(tts["settings"]["sample_rate"], int)


def test_system_status_dependencies_block_is_machine_readable(client: TestClient) -> None:
    """Phase 8: flat dependency map - booleans only, no secrets/paths."""
    deps = client.get("/api/system/status").json()["dependencies"]
    assert deps["python"] is True
    for key in ("ffmpeg", "ffprobe", "tesseract", "whisper_model", "llm", "piper"):
        assert key in deps and isinstance(deps[key], bool)
    assert set(deps["voices"]) == {"en", "hi", "bn"}
    assert all(isinstance(v, bool) for v in deps["voices"].values())
    # Never expose keys/paths/tokens in the dependency report.
    assert not any("path" in str(k).lower() or "key" in str(k).lower() for k in deps)


def test_system_status_never_exposes_absolute_paths(client: TestClient) -> None:
    """Phase 8: public status redacts machine paths (basenames only)."""
    body = client.get("/api/system/status").json()
    ffmpeg = body["ffmpeg"]
    assert ffmpeg["ffmpeg"]["path"] in (None, "ffmpeg")
    assert ffmpeg["ffprobe"]["path"] in (None, "ffprobe")
    # database.path is a plain filename, never an absolute path.
    assert "/" not in body["database"]["path"] and "\\" not in body["database"]["path"]
    for entry in body["storage"]["directories"]:
        rel = str(entry["path"])
        assert not rel.startswith("/") and ".." not in rel.split("/")
        assert rel in ("data", "models", "logs") or rel.startswith("data/") or rel == "<local>"


def test_preflight_endpoint_works_and_is_honest(client: TestClient) -> None:
    """Phase 8: pre-flight returns 200 with machine-readable checks.

    In this sandbox FFmpeg is absent, so ``ok`` must be False and the
    blocking list must name ffmpeg - preflight never claims readiness.
    """
    response = client.get("/api/system/preflight", params={"language": "en"})
    assert response.status_code == 200
    body = response.json()
    assert body["language"] == "en"
    assert body["supported_languages"] == sorted(["en", "hi", "bn"])
    assert isinstance(body["ok"], bool)
    assert isinstance(body["blocking"], list)
    assert isinstance(body["messages"], list) and body["messages"]

    checks = body["checks"]
    for name in ("python", "database", "ffmpeg", "ffprobe", "storage",
                 "disk_space", "whisper", "tesseract", "llm", "piper",
                 "voice_en", "subtitle_font"):
        assert name in checks, name
        entry = checks[name]
        assert set(entry) == {"ok", "required", "detail", "setup_hint"}
        assert isinstance(entry["ok"], bool)
        assert isinstance(entry["required"], bool)
        assert isinstance(entry["detail"], str)

    # ffmpeg is not installed in this sandbox: required check must be False
    # and reported as blocking (honest pre-flight, never a fake PASS).
    assert checks["ffmpeg"]["ok"] is False
    assert checks["ffmpeg"]["required"] is True
    assert "ffmpeg" in body["blocking"]
    assert body["ok"] is False
    assert checks["ffmpeg"]["setup_hint"]


def test_preflight_rejects_unknown_language(client: TestClient) -> None:
    response = client.get("/api/system/preflight", params={"language": "xx"})
    assert response.status_code == 422


def test_preflight_hindi_requires_subtitle_font(client: TestClient, settings) -> None:
    """Phase 8: hi/bn burn-in demands a font - preflight must say so."""
    response = client.get("/api/system/preflight", params={"language": "hi"})
    assert response.status_code == 200
    checks = response.json()["checks"]
    assert checks["subtitle_font"]["required"] is True
    assert checks["subtitle_font"]["ok"] is False
    assert "SUBTITLE_FONT" in checks["subtitle_font"]["setup_hint"]
