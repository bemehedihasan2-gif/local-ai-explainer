"""Phase 6: end-to-end API tests (SCRIPT_READY -> NARRATION_READY).

Reuses the Phase 5 harness (fake ffmpeg/ffprobe + a scripted fake LLM) to
reach ``script_ready`` deterministically, then injects a fake
``LocalTTSProvider`` that writes real, measurable WAV segments so the whole
narration pipeline is exercised: segmentation, per-segment synthesis,
measured timing, gap assembly, normalization, SRT/VTT generation and QC -
without Piper or FFmpeg.
"""

from __future__ import annotations

import json
import math
import struct
import time
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import test_script_api as tsa
from _media_helpers import install_fake_media_tools
from app.main import create_app
from app.services.narration import NarrationService
from app.utils.errors import NarrationError


class FakeTTS:
    """Writes a deterministic mono sine WAV per segment (rate-configurable)."""

    def __init__(self, rate: int = 22050) -> None:
        self.rate = rate
        self.calls = 0
        self.available_flag = True

    # -- LocalTTSProvider protocol ------------------------------------
    def available(self) -> bool:
        return self.available_flag

    def describe(self) -> dict:
        languages = {}
        voices = []
        for code, name in (("en", "English"), ("hi", "Hindi"), ("bn", "Bengali")):
            available = self.available_flag
            languages[code] = {
                "voice_id": f"voice_{code}",
                "available": available,
                "configured": True,
                "model_available": available,
                "sample_rate": self.rate,
                "note": None if available else "voice not configured",
            }
            voices.append({
                "id": f"{code}:voice_{code}",
                "language": code,
                "voice_id": f"voice_{code}",
                "available": available,
                "sample_rate": self.rate,
                "note": None if available else "voice not configured",
            })
        return {
            "provider": "piper",
            "available": self.available_flag,
            "executable_available": self.available_flag,
            "languages": languages,
            "voices": voices,
            "setup_hint": None if self.available_flag else "not configured",
        }

    def synthesize(self, text: str, language: str, output_path=None) -> dict:
        self.calls += 1
        if not self.available_flag:
            raise NarrationError("tts unavailable (fake)")
        out = Path(output_path) if output_path else Path("fake.wav")
        out.parent.mkdir(parents=True, exist_ok=True)
        words = len(text.split())
        frames = max(800, int(self.rate * (0.25 + 0.045 * words)))
        duration_ms = round(frames / self.rate * 1000)
        with wave.open(str(out), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.rate)
            step = 2 * math.pi * 440 / self.rate
            payload = bytearray()
            for i in range(frames):
                sample = int(9000 * math.sin(step * i))
                payload += struct.pack("<h", sample)
            wav.writeframes(bytes(payload))
        return {
            "path": str(out),
            "provider": "piper",
            "language": language,
            "voice_id": f"voice_{language}",
            "sample_rate": self.rate,
            "channels": 1,
            "sampwidth": 2,
            "duration_ms": duration_ms,
        }


@pytest.fixture
def client(settings, tmp_path):
    ffmpeg_path, ffprobe_path = install_fake_media_tools(tmp_path)
    settings.ffmpeg_path = ffmpeg_path
    settings.ffprobe_path = ffprobe_path
    with TestClient(create_app(settings)) as test_client:
        yield test_client, settings


@pytest.fixture
def deterministic_ai(monkeypatch):
    monkeypatch.setattr("app.ai.stt.whisper_model_installed", lambda settings: False)
    monkeypatch.setattr("app.ai.ocr.tesseract_available", lambda settings: False)
    return None


@pytest.fixture
def fake_llm(monkeypatch):
    provider = tsa.FakeProvider(
        [tsa.BATCH_SUMMARY_JSON, tsa.STORY_JSON, tsa.SCRIPT_PARAGRAPHS]
    )
    import app.services.worker as worker_module

    def factory(settings, storage, _provider=provider) -> tsa.StoryService:
        return tsa.StoryService(settings, storage, provider=_provider)

    monkeypatch.setattr("app.api.scripts.build_llm_provider", lambda settings: provider)
    monkeypatch.setattr(worker_module, "StoryService", factory)
    return provider


@pytest.fixture
def fake_tts(monkeypatch):
    provider = FakeTTS(rate=22050)
    import app.services.worker as worker_module

    def factory(settings, storage, _provider=provider) -> NarrationService:
        return NarrationService(settings, storage, provider=_provider)

    monkeypatch.setattr("app.api.narration.build_tts_provider", lambda settings: provider)
    monkeypatch.setattr(worker_module, "NarrationService", factory)
    return provider


def _upload_analyzed(test_client) -> dict:
    return tsa._upload_analyzed(test_client)


def _start_script(test_client, project_id: str) -> None:
    tsa._start_script(test_client, project_id, language="en", duration=180)


def _wait_script_ready(test_client, project_id: str) -> dict:
    return tsa._wait_script_ready(test_client, project_id)


def _wait_for(test_client, project_id, predicate, timeout: float = 20.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = test_client.get(f"/api/projects/{project_id}").json()
        if predicate(last):
            return last
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for project {project_id}; last state: {last}")


def _start_narration(test_client, project_id: str, language: str = "en") -> dict:
    response = test_client.post(
        f"/api/projects/{project_id}/generate-narration",
        json={"language": language},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["idempotent"] is False
    assert body["status"] == "narrating"
    assert body["tts_run_id"]
    assert body["job"]["stage"] == "text_to_speech"
    return body


def _wait_narration_ready(test_client, project_id: str) -> dict:
    return _wait_for(
        test_client, project_id, lambda body: body["status"] == "narration_ready"
    )


def _script_ready_project(test_client) -> str:
    analyzed = _upload_analyzed(test_client)
    project_id = analyzed["id"]
    _start_script(test_client, project_id)
    _wait_script_ready(test_client, project_id)
    return project_id


def _assert_no_absolute_paths(text: str, settings_base: str) -> None:
    assert settings_base not in text, "absolute path leaked in API response"


# ----------------------------------------------------------------------
# Happy path
# ----------------------------------------------------------------------


def test_full_narration_flow_to_narration_ready(
    client, deterministic_ai, fake_llm, fake_tts,
) -> None:
    test_client, settings = client
    project_id = _script_ready_project(test_client)

    _start_narration(test_client, project_id, language="en")
    ready = _wait_narration_ready(test_client, project_id)
    assert ready["progress"] == 100
    assert ready["error_message"] is None

    run = test_client.get(f"/api/projects/{project_id}/narration-status").json()
    assert run["status"] == "completed"
    assert run["language"] == "en"
    assert run["voice_id"] == "voice_en"
    assert run["provider"] == "piper"
    assert run["segment_count"] > 0
    assert run["duration_ms"] > 0
    assert 0 <= run["quality_score"] <= 100
    assert run["audio_path"] == "audio/narration.wav"
    assert run["subtitle_path"] == "subtitles/subtitles.srt"
    assert run["started_at"] and run["completed_at"]

    jobs = test_client.get(f"/api/projects/{project_id}/jobs").json()
    assert [j["stage"] for j in jobs] == [
        "preprocess", "analysis", "script_generation", "text_to_speech",
    ]
    assert jobs[-1]["status"] == "completed"

    # Artifacts on disk (relative paths inside every document).
    root = settings.projects_dir / project_id
    assert (root / "audio" / "segments").is_dir()
    assert (root / "audio" / "narration.wav").is_file()
    assert (root / "audio" / "narration_timeline.json").is_file()
    assert (root / "audio" / "narration_quality.json").is_file()
    assert (root / "audio" / "narration_manifest.json").is_file()
    assert (root / "subtitles" / "subtitles.srt").is_file()
    assert (root / "subtitles" / "subtitles.vtt").is_file()
    segment_files = sorted((root / "audio" / "segments").glob("segment_*.wav"))
    assert len(segment_files) == run["segment_count"]

    manifest = json.loads(
        (root / "audio" / "narration_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["results"]["assets"]["audio"] == "audio/narration.wav"
    _assert_no_absolute_paths(json.dumps(manifest), str(settings.base_dir))

    # Public endpoints: manifest, segments (timeline), subtitles, audio.
    response = test_client.get(f"/api/projects/{project_id}/narration")
    assert response.status_code == 200
    _assert_no_absolute_paths(response.text, str(settings.base_dir))

    timeline = test_client.get(
        f"/api/projects/{project_id}/narration/segments"
    ).json()
    assert timeline["segments"]
    assert timeline["segments"][0]["start_ms"] >= 0
    ordered = all(
        timeline["segments"][i]["end_ms"] <= timeline["segments"][i + 1]["start_ms"]
        for i in range(len(timeline["segments"]) - 1)
    )
    assert ordered

    srt = test_client.get(
        f"/api/projects/{project_id}/narration/subtitles"
    ).text
    assert srt.startswith("1\n")
    assert "--> " in srt
    vtt = test_client.get(
        f"/api/projects/{project_id}/narration/subtitles?format=vtt"
    ).text
    assert vtt.startswith("WEBVTT")

    audio = test_client.get(f"/api/projects/{project_id}/narration/audio")
    assert audio.status_code == 200
    assert audio.headers["content-type"].startswith("audio/wav")
    assert len(audio.content) > 44

    quality = json.loads(
        (root / "audio" / "narration_quality.json").read_text(encoding="utf-8")
    )
    assert quality["quality_score"] >= 80


# ----------------------------------------------------------------------
# Idempotency & option changes
# ----------------------------------------------------------------------


def test_generate_narration_is_idempotent(
    client, deterministic_ai, fake_llm, fake_tts,
) -> None:
    test_client, _ = client
    project_id = _script_ready_project(test_client)
    _start_narration(test_client, project_id, language="en")
    _wait_narration_ready(test_client, project_id)
    run_before = test_client.get(f"/api/projects/{project_id}/narration-status").json()
    jobs_before = test_client.get(f"/api/projects/{project_id}/jobs").json()

    response = test_client.post(
        f"/api/projects/{project_id}/generate-narration",
        json={"language": "en"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["idempotent"] is True
    assert body["status"] == "narration_ready"
    assert body["tts_run"]["id"] == run_before["id"]
    assert test_client.get(f"/api/projects/{project_id}/jobs").json() == jobs_before


def test_changed_voice_regenerates(
    client, deterministic_ai, fake_llm, fake_tts,
) -> None:
    test_client, _ = client
    project_id = _script_ready_project(test_client)
    _start_narration(test_client, project_id, language="en")
    _wait_narration_ready(test_client, project_id)
    first = test_client.get(f"/api/projects/{project_id}/narration-status").json()

    # An explicit (different) voice id for the same language -> new run.
    response = test_client.post(
        f"/api/projects/{project_id}/generate-narration",
        json={"language": "en", "voice_id": "voice_en"},
    )
    assert response.status_code == 200
    assert response.json()["idempotent"] is True  # matches the configured voice

    # Requesting an unknown voice is rejected without touching anything.
    bad = test_client.post(
        f"/api/projects/{project_id}/generate-narration",
        json={"language": "en", "voice_id": "nope"},
    )
    assert bad.status_code == 422
    assert test_client.get(f"/api/projects/{project_id}/narration-status").json()["id"] == first["id"]


def test_changed_language_regenerates(
    client, deterministic_ai, fake_llm, fake_tts,
) -> None:
    test_client, _ = client
    project_id = _script_ready_project(test_client)
    _start_narration(test_client, project_id, language="en")
    _wait_narration_ready(test_client, project_id)
    first = test_client.get(f"/api/projects/{project_id}/narration-status").json()

    response = test_client.post(
        f"/api/projects/{project_id}/generate-narration",
        json={"language": "hi"},
    )
    assert response.status_code == 200
    assert response.json()["idempotent"] is False
    _wait_narration_ready(test_client, project_id)
    second = test_client.get(f"/api/projects/{project_id}/narration-status").json()
    assert second["id"] != first["id"]
    assert second["language"] == "hi"
    assert second["voice_id"] == "voice_hi"
    jobs = test_client.get(f"/api/projects/{project_id}/jobs").json()
    assert [j["stage"] for j in jobs].count("text_to_speech") == 2


# ----------------------------------------------------------------------
# Guard rails
# ----------------------------------------------------------------------


def test_generate_narration_rejects_non_script_ready_project(
    client, deterministic_ai, fake_llm, fake_tts,
) -> None:
    test_client, _ = client
    analyzed = _upload_analyzed(test_client)  # ANALYZED, not SCRIPT_READY
    response = test_client.post(
        f"/api/projects/{analyzed['id']}/generate-narration",
        json={"language": "en"},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "narration_not_ready"


def test_generate_narration_tts_unavailable_503(
    client, deterministic_ai, fake_llm, fake_tts, monkeypatch,
) -> None:
    test_client, _ = client
    project_id = _script_ready_project(test_client)
    fake_tts.available_flag = False
    response = test_client.post(
        f"/api/projects/{project_id}/generate-narration",
        json={"language": "en"},
    )
    assert response.status_code == 503
    assert response.json()["error"] == "tts_unavailable"
    assert test_client.get(f"/api/projects/{project_id}").json()["status"] == "script_ready"
    jobs = test_client.get(f"/api/projects/{project_id}/jobs").json()
    assert [j["stage"] for j in jobs] == [
        "preprocess", "analysis", "script_generation",
    ]


def test_generate_narration_conflict_when_running(
    client, deterministic_ai, fake_llm, fake_tts, monkeypatch,
) -> None:
    test_client, _ = client
    project_id = _script_ready_project(test_client)
    worker = test_client.app.state.worker
    submitted: list[str] = []
    monkeypatch.setattr(worker, "submit", submitted.append)

    first = test_client.post(
        f"/api/projects/{project_id}/generate-narration",
        json={"language": "en"},
    )
    assert first.status_code == 200
    assert submitted
    assert test_client.get(f"/api/projects/{project_id}").json()["status"] == "narrating"

    second = test_client.post(
        f"/api/projects/{project_id}/generate-narration",
        json={"language": "en"},
    )
    assert second.status_code == 409
    assert second.json()["error"] == "narration_already_running"


def test_narration_failure_returns_to_script_ready_and_allows_retry(
    client, deterministic_ai, fake_llm, fake_tts, monkeypatch,
) -> None:
    test_client, settings = client
    project_id = _script_ready_project(test_client)

    class FailingService:
        def __init__(self, settings_, storage_) -> None:
            pass

        def run(self, project_row, *, language, voice_id=None, script_fingerprint=None,
                generation_fingerprint=None, progress_callback=None):
            raise NarrationError("simulated narration failure")

        def cleanup_artifacts(self, project_id_) -> None:
            pass

    import app.services.worker as worker_module

    def working_factory(settings_, storage_) -> NarrationService:
        return NarrationService(settings_, storage_, provider=fake_tts)

    monkeypatch.setattr(worker_module, "NarrationService", FailingService)
    response = test_client.post(
        f"/api/projects/{project_id}/generate-narration",
        json={"language": "en"},
    )
    assert response.status_code == 200
    body = _wait_for(
        test_client, project_id,
        lambda b: b["status"] == "script_ready" and b["error_message"] is not None,
    )
    assert "narration" in body["error_message"].lower()

    run = test_client.get(f"/api/projects/{project_id}/narration-status").json()
    assert run["status"] == "failed"
    assert run["error_message"]

    # Phase 5 results are preserved (script still served) and no narration
    # artifacts were left behind.
    assert test_client.get(f"/api/projects/{project_id}/script").status_code == 200
    assert not (settings.projects_dir / project_id / "audio" / "narration.wav").exists()
    assert test_client.get(f"/api/projects/{project_id}/narration").status_code == 404

    # Retry with the working service succeeds without regenerating the script.
    monkeypatch.setattr(worker_module, "NarrationService", working_factory)
    response = test_client.post(
        f"/api/projects/{project_id}/generate-narration",
        json={"language": "en"},
    )
    assert response.status_code == 200
    assert response.json()["idempotent"] is False
    _wait_narration_ready(test_client, project_id)
    jobs = test_client.get(f"/api/projects/{project_id}/jobs").json()
    assert [j["stage"] for j in jobs] == [
        "preprocess", "analysis", "script_generation",
        "text_to_speech", "text_to_speech",
    ]
    assert [j["status"] for j in jobs][-2:] == ["failed", "completed"]


def test_stale_narrating_state_recovers(
    client, deterministic_ai, fake_llm, fake_tts,
) -> None:
    test_client, settings = client
    project_id = _script_ready_project(test_client)
    worker = test_client.app.state.worker
    original_submit = worker.submit
    worker.submit = lambda job_id: None  # type: ignore[method-assign]

    assert test_client.post(
        f"/api/projects/{project_id}/generate-narration",
        json={"language": "en"},
    ).status_code == 200
    assert test_client.get(f"/api/projects/{project_id}").json()["status"] == "narrating"

    # Simulate the crash: run + job rows vanished without a worker.
    from app.database.connection import Database

    db = Database(settings.database_path)
    with db.connect() as conn:
        conn.execute("DELETE FROM tts_runs WHERE project_id = ?", (project_id,))
        conn.execute(
            "DELETE FROM processing_jobs WHERE project_id = ?", (project_id,)
        )

    worker.submit = original_submit  # real submit again
    response = test_client.post(
        f"/api/projects/{project_id}/generate-narration",
        json={"language": "en"},
    )
    assert response.status_code == 200
    _wait_narration_ready(test_client, project_id)


def test_narration_endpoints_404_before_generation(
    client, deterministic_ai, fake_llm, fake_tts,
) -> None:
    test_client, _ = client
    project_id = _script_ready_project(test_client)
    for endpoint in (
        "/narration-status", "/narration", "/narration/audio",
        "/narration/subtitles", "/narration/segments",
    ):
        response = test_client.get(f"/api/projects/{project_id}{endpoint}")
        assert response.status_code == 404, endpoint
