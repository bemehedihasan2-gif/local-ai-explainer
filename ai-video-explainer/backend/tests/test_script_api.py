"""Phase 5: end-to-end API tests (upload -> analyze -> script_ready).

Runs against fake ffmpeg/ffprobe binaries (``_media_helpers``) and a fake
``LocalLLMProvider`` injected into both the endpoint's availability check
and the worker's ``StoryService``, so the whole pipeline is exercised
deterministically without llama.cpp:

    ANALYZED -> generate-script -> SCRIPTING -> story artifacts ->
    SCRIPT_READY (idempotent reuse, conflicts, failures and retries).
"""

from __future__ import annotations

import contextlib
import json
import time
from collections import deque

import pytest
from fastapi.testclient import TestClient

from _media_helpers import install_fake_media_tools
from app.main import create_app
from app.services.story import StoryService
from app.utils.errors import StoryError

VIDEO_BYTES = (b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 2048 + b"\x00\x00\x00\x08free") * 16

BATCH_SUMMARY_JSON = json.dumps({
    "events": [
        {"text": "A tutorial introduces a recipe", "scene_ids": [1]},
        {"text": "Ingredients are prepared on screen", "scene_ids": [2, 3]},
    ],
    "facts": [
        {"text": "Each step is demonstrated with on-screen text", "scene_ids": [2]},
    ],
    "entities": ["The presenter"],
    "uncertain": [],
})

STORY_JSON = json.dumps({
    "content_type": "tutorial",
    "content_type_confidence": 0.85,
    "premise": "A cooking tutorial demonstrates a recipe step by step.",
    "main_entities": ["The presenter"],
    "locations": [],
    "chronological_events": [
        {"text": "The presenter introduces the recipe", "scene_ids": [1]},
        {"text": "Ingredients are measured", "scene_ids": [2, 3]},
        {"text": "The dish is finished", "scene_ids": [6]},
    ],
    "key_turning_points": [
        {"text": "A key mixing technique is shown", "scene_ids": [4]},
    ],
    "beginning": [{"text": "The video opens with an introduction", "scene_ids": [1]}],
    "middle": [{"text": "The main steps are shown", "scene_ids": [3, 4]}],
    "ending": [{"text": "The finished dish is presented", "scene_ids": [6]}],
    "cause_effect": [
        {"cause": "the oven is preheated", "effect": "the cake bakes evenly",
         "scene_ids": [5]},
    ],
    "important_facts": [
        {"text": "The recipe serves four people", "scene_ids": [2]},
    ],
    "uncertain_points": [],
    "evidence_scene_ids": [1, 2, 3, 4, 5, 6],
})

SCRIPT_PARAGRAPHS = (
    "This tutorial shows how to make the dish from start to finish. "
    "The presenter first introduces the recipe and gathers the ingredients "
    "shown on screen.\n\n"
    "A key technique appears mid-way, when the mixture must be folded "
    "carefully. The video then finishes with the dish presented and ready "
    "to serve."
)


class FakeProvider:
    def __init__(self, responses: list[str] | None = None) -> None:
        self._base = list(responses or [])
        self.responses: deque[str] = deque(self._base)
        self.calls = 0
        self.available_flag = True

    def available(self) -> bool:
        return self.available_flag

    def generate(self, prompt: str, *, max_tokens: int, temperature: float = 0.2) -> str:
        self.calls += 1
        if not self.responses:
            if not self._base:
                raise AssertionError("FakeProvider has no scripted responses")
            self.responses = deque(self._base)  # recycle for repeated runs
        return self.responses.popleft()

    def describe(self) -> dict:
        return {
            "provider": "llama_cpp", "available": self.available_flag,
            "executable_available": True, "model_available": True,
            "model_name": "fake.gguf", "threads": 4, "context_size": 2048,
            "max_tokens": 1024, "temperature": 0.2,
            "setup_hint": None if self.available_flag else "not configured",
        }


@contextlib.contextmanager
def _client_with_media(settings, tmp_path, **media_kwargs):
    ffmpeg_path, ffprobe_path = install_fake_media_tools(tmp_path, **media_kwargs)
    settings.ffmpeg_path = ffmpeg_path
    settings.ffprobe_path = ffprobe_path
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def client(settings, tmp_path):
    with _client_with_media(settings, tmp_path) as test_client:
        yield test_client, settings


@pytest.fixture
def deterministic_ai(monkeypatch):
    monkeypatch.setattr("app.ai.stt.whisper_model_installed", lambda settings: False)
    monkeypatch.setattr("app.ai.ocr.tesseract_available", lambda settings: False)
    return None


@pytest.fixture
def fake_llm(monkeypatch):
    """Inject a scripted FakeProvider into the endpoint + the worker."""
    provider = FakeProvider([BATCH_SUMMARY_JSON, STORY_JSON, SCRIPT_PARAGRAPHS])
    import app.services.worker as worker_module

    def factory(settings, storage, _provider=provider) -> StoryService:
        return StoryService(settings, storage, provider=_provider)

    monkeypatch.setattr("app.api.scripts.build_llm_provider", lambda settings: provider)
    monkeypatch.setattr(worker_module, "StoryService", factory)
    return provider


def _post_video(test_client, *, payload: bytes = VIDEO_BYTES, filename: str = "clip.mp4"):
    return test_client.post(
        "/api/projects/upload",
        files={"file": (filename, payload, "video/mp4")},
        data={"language": "en", "target_duration": "180"},
    )


def _wait_for(test_client, project_id, predicate, timeout: float = 20.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = test_client.get(f"/api/projects/{project_id}").json()
        if predicate(last):
            return last
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for project {project_id}; last state: {last}")


def _upload_analyzed(test_client, *, payload: bytes = VIDEO_BYTES) -> dict:
    response = _post_video(test_client, payload=payload)
    assert response.status_code == 201, response.text
    project = response.json()
    assert test_client.post(f"/api/projects/{project['id']}/preprocess").status_code == 201
    prepared = _wait_for(
        test_client, project["id"], lambda body: body["status"] == "prepared"
    )
    assert test_client.post(f"/api/projects/{prepared['id']}/analyze").status_code == 200
    return _wait_for(
        test_client, project["id"], lambda body: body["status"] == "analyzed"
    )


def _start_script(test_client, project_id: str, language="en", duration=180) -> dict:
    response = test_client.post(
        f"/api/projects/{project_id}/generate-script",
        json={"language": language, "target_duration_seconds": duration},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["idempotent"] is False
    assert body["status"] == "scripting"
    assert body["script_run_id"]
    assert body["job"]["stage"] == "script_generation"
    return body


def _wait_script_ready(test_client, project_id: str) -> dict:
    return _wait_for(
        test_client, project_id, lambda body: body["status"] == "script_ready"
    )


def _assert_no_absolute_paths(text: str, settings_base: str) -> None:
    assert settings_base not in text, "absolute path leaked in API response"


# ----------------------------------------------------------------------
# Happy path
# ----------------------------------------------------------------------


def test_full_script_flow_to_script_ready(client, deterministic_ai, fake_llm) -> None:
    test_client, settings = client
    analyzed = _upload_analyzed(test_client)
    project_id = analyzed["id"]

    _start_script(test_client, project_id, language="en", duration=180)
    ready = _wait_script_ready(test_client, project_id)
    assert ready["progress"] == 100
    assert ready["error_message"] is None

    # Run summary persisted in SQLite (no story payload blobs).
    run = test_client.get(f"/api/projects/{project_id}/story-status").json()
    assert run["status"] == "completed"
    assert run["language"] == "en"
    assert run["target_duration_seconds"] == 180
    assert run["content_type"] == "tutorial"
    assert 0.0 <= run["content_type_confidence"] <= 1.0
    assert run["selected_scene_count"] >= 3
    assert run["word_count"] > 0
    assert 0 <= run["quality_score"] <= 100
    assert run["estimated_duration_seconds"] > 0
    assert run["started_at"] and run["completed_at"]

    # Jobs persisted: preprocess, analysis, script_generation (in order).
    jobs = test_client.get(f"/api/projects/{project_id}/jobs").json()
    assert [j["stage"] for j in jobs] == ["preprocess", "analysis", "script_generation"]
    assert jobs[-1]["status"] == "completed"
    assert jobs[-1]["progress"] == 100

    # Artifacts on disk, relative paths only.
    story_dir = settings.projects_dir / project_id / "analysis" / "story"
    for filename in (
        "evidence_manifest.json", "scene_summaries.json", "story.json",
        "scene_importance.json", "selected_scenes.json", "duration_plan.json",
        "script_plan.json", "script.json", "script_quality.json",
        "story_manifest.json",
    ):
        assert (story_dir / filename).is_file(), filename

    story = json.loads((story_dir / "story.json").read_text(encoding="utf-8"))
    assert story["content_type"] == "tutorial"
    assert story["scene_scores"]
    manifest = json.loads((story_dir / "story_manifest.json").read_text(encoding="utf-8"))
    assert manifest["results"]["assets"]["script"] == "analysis/story/script.json"
    _assert_no_absolute_paths(json.dumps(manifest), str(settings.base_dir))

    # Public artifact endpoints.
    for endpoint, key in (
        ("/story", "premise"),
        ("/selected-scenes", "selected"),
        ("/duration-plan", "total_word_budget"),
        ("/script", "full_text"),
        ("/script-quality", "quality_score"),
    ):
        response = test_client.get(f"/api/projects/{project_id}{endpoint}")
        assert response.status_code == 200, endpoint
        doc = response.json()
        assert key in doc, endpoint
        _assert_no_absolute_paths(response.text, str(settings.base_dir))

    # Story overview + important scenes are coherent.
    selection = test_client.get(f"/api/projects/{project_id}/selected-scenes").json()
    ids = [row["scene_id"] for row in selection["selected"]]
    assert ids == sorted(ids)
    script = test_client.get(f"/api/projects/{project_id}/script").json()
    for section in script["sections"]:
        assert section["scene_ids"]
    script_quality = test_client.get(f"/api/projects/{project_id}/script-quality").json()
    assert 0 <= script_quality["quality_score"] <= 100
    assert script_quality["estimated_duration_seconds"] > 0


# ----------------------------------------------------------------------
# Idempotency & option changes
# ----------------------------------------------------------------------


def test_generate_script_is_idempotent(client, deterministic_ai, fake_llm) -> None:
    test_client, _ = client
    analyzed = _upload_analyzed(test_client)
    project_id = analyzed["id"]
    _start_script(test_client, project_id)
    _wait_script_ready(test_client, project_id)
    run_before = test_client.get(f"/api/projects/{project_id}/story-status").json()
    jobs_before = test_client.get(f"/api/projects/{project_id}/jobs").json()

    response = test_client.post(
        f"/api/projects/{project_id}/generate-script",
        json={"language": "en", "target_duration_seconds": 180},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["idempotent"] is True
    assert body["status"] == "script_ready"
    assert body["script_run"]["id"] == run_before["id"]

    # Nothing was re-run.
    assert test_client.get(f"/api/projects/{project_id}/jobs").json() == jobs_before
    assert (
        test_client.get(f"/api/projects/{project_id}/story-status").json()["id"]
        == run_before["id"]
    )


def test_changed_language_regenerates(client, deterministic_ai, fake_llm) -> None:
    test_client, _ = client
    analyzed = _upload_analyzed(test_client)
    project_id = analyzed["id"]
    _start_script(test_client, project_id, language="en", duration=180)
    _wait_script_ready(test_client, project_id)
    first_run = test_client.get(f"/api/projects/{project_id}/story-status").json()

    # Regenerate in Hindi: new run row, language recorded, artifacts replaced.
    response = test_client.post(
        f"/api/projects/{project_id}/generate-script",
        json={"language": "hi", "target_duration_seconds": 180},
    )
    assert response.status_code == 200
    assert response.json()["idempotent"] is False
    _wait_script_ready(test_client, project_id)
    second_run = test_client.get(f"/api/projects/{project_id}/story-status").json()
    assert second_run["id"] != first_run["id"]
    assert second_run["language"] == "hi"
    script = test_client.get(f"/api/projects/{project_id}/script").json()
    assert script["language"] == "hi"
    jobs = test_client.get(f"/api/projects/{project_id}/jobs").json()
    assert [j["stage"] for j in jobs].count("script_generation") == 2


# ----------------------------------------------------------------------
# Guard rails
# ----------------------------------------------------------------------


def test_generate_script_rejects_non_analyzed_project(
    client, deterministic_ai, fake_llm,
) -> None:
    test_client, _ = client
    response = _post_video(test_client)
    project = response.json()
    assert project["status"] == "ready"
    response = test_client.post(
        f"/api/projects/{project['id']}/generate-script",
        json={"language": "en", "target_duration_seconds": 180},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "script_not_ready"


def test_generate_script_rejects_invalid_options(client, deterministic_ai, fake_llm) -> None:
    test_client, _ = client
    analyzed = _upload_analyzed(test_client)
    project_id = analyzed["id"]
    for payload in (
        {"language": "fr", "target_duration_seconds": 180},
        {"language": "en", "target_duration_seconds": 300},
        {"language": "en", "target_duration_seconds": 90},
    ):
        response = test_client.post(
            f"/api/projects/{project_id}/generate-script", json=payload,
        )
        assert response.status_code == 422, payload
    # Missing duration falls back to the default (180 s) by design.
    response = test_client.post(
        f"/api/projects/{project_id}/generate-script", json={"language": "en"},
    )
    assert response.status_code == 200, response.text


def test_generate_script_conflict_when_running(client, deterministic_ai, fake_llm, monkeypatch) -> None:
    test_client, _ = client
    analyzed = _upload_analyzed(test_client)
    project_id = analyzed["id"]
    worker = test_client.app.state.worker
    submitted: list[str] = []
    monkeypatch.setattr(worker, "submit", submitted.append)  # job never runs

    first = test_client.post(
        f"/api/projects/{project_id}/generate-script",
        json={"language": "en", "target_duration_seconds": 180},
    )
    assert first.status_code == 200
    assert submitted
    assert test_client.get(f"/api/projects/{project_id}").json()["status"] == "scripting"

    second = test_client.post(
        f"/api/projects/{project_id}/generate-script",
        json={"language": "en", "target_duration_seconds": 180},
    )
    assert second.status_code == 409
    assert second.json()["error"] == "script_already_running"

    jobs = test_client.get(f"/api/projects/{project_id}/jobs").json()
    assert jobs[-1]["stage"] == "script_generation"
    assert jobs[-1]["status"] == "queued"


def test_generate_script_llm_unavailable_503(client, deterministic_ai, monkeypatch) -> None:
    test_client, _ = client
    analyzed = _upload_analyzed(test_client)
    project_id = analyzed["id"]
    provider = FakeProvider([])
    provider.available_flag = False
    monkeypatch.setattr("app.api.scripts.build_llm_provider", lambda settings: provider)

    response = test_client.post(
        f"/api/projects/{project_id}/generate-script",
        json={"language": "en", "target_duration_seconds": 180},
    )
    assert response.status_code == 503
    assert response.json()["error"] == "llm_unavailable"
    assert "not configured" in response.json()["detail"]
    # No job was queued and the project stayed ANALYZED.
    body = test_client.get(f"/api/projects/{project_id}").json()
    assert body["status"] == "analyzed"
    jobs = test_client.get(f"/api/projects/{project_id}/jobs").json()
    assert [j["stage"] for j in jobs] == ["preprocess", "analysis"]


def test_script_failure_returns_to_analyzed_and_allows_retry(
    client, deterministic_ai, fake_llm, monkeypatch,
) -> None:
    test_client, settings = client
    analyzed = _upload_analyzed(test_client)
    project_id = analyzed["id"]
    provider = fake_llm

    class FailingService:
        def __init__(self, settings_, storage_) -> None:
            pass

        def run(self, project_row, *, language, target_duration_seconds, progress_callback=None):
            raise StoryError("simulated story failure")

        def cleanup_artifacts(self, project_id_) -> None:
            pass

    import app.services.worker as worker_module

    def working_factory(settings_, storage_) -> StoryService:
        return StoryService(settings_, storage_, provider=provider)

    monkeypatch.setattr(worker_module, "StoryService", FailingService)
    response = test_client.post(
        f"/api/projects/{project_id}/generate-script",
        json={"language": "en", "target_duration_seconds": 180},
    )
    assert response.status_code == 200
    body = _wait_for(
        test_client, project_id,
        lambda b: b["status"] == "analyzed" and b["error_message"] is not None,
    )
    assert "story" in body["error_message"].lower()

    jobs = test_client.get(f"/api/projects/{project_id}/jobs").json()
    assert jobs[-1]["stage"] == "script_generation"
    assert jobs[-1]["status"] == "failed"
    run = test_client.get(f"/api/projects/{project_id}/story-status").json()
    assert run["status"] == "failed"
    assert run["error_message"]

    # Phase 4 analysis is preserved (timeline still served).
    assert test_client.get(f"/api/projects/{project_id}/timeline").status_code == 200
    assert test_client.get(f"/api/projects/{project_id}/story").status_code == 404

    # Retry with the working service succeeds without re-analyzing.
    monkeypatch.setattr(worker_module, "StoryService", working_factory)
    response = test_client.post(
        f"/api/projects/{project_id}/generate-script",
        json={"language": "en", "target_duration_seconds": 180},
    )
    assert response.status_code == 200
    assert response.json()["idempotent"] is False
    _wait_script_ready(test_client, project_id)
    jobs = test_client.get(f"/api/projects/{project_id}/jobs").json()
    assert [j["status"] for j in jobs] == [
        "completed", "completed", "failed", "completed",
    ]


def test_stale_scripting_state_recovers(client, deterministic_ai, fake_llm) -> None:
    test_client, settings = client
    analyzed = _upload_analyzed(test_client)
    project_id = analyzed["id"]
    worker = test_client.app.state.worker
    original_submit = worker.submit
    worker.submit = lambda job_id: None  # type: ignore[method-assign]

    assert test_client.post(
        f"/api/projects/{project_id}/generate-script",
        json={"language": "en", "target_duration_seconds": 180},
    ).status_code == 200
    assert test_client.get(f"/api/projects/{project_id}").json()["status"] == "scripting"

    # Simulate the crash: run + job rows vanished without a worker.
    from app.database.connection import Database

    db = Database(settings.database_path)
    with db.connect() as conn:
        conn.execute(
            "DELETE FROM script_runs WHERE project_id = ?", (project_id,)
        )
        conn.execute(
            "DELETE FROM processing_jobs WHERE project_id = ?", (project_id,)
        )

    worker.submit = original_submit  # real submit again
    response = test_client.post(
        f"/api/projects/{project_id}/generate-script",
        json={"language": "en", "target_duration_seconds": 180},
    )
    assert response.status_code == 200
    _wait_script_ready(test_client, project_id)


def test_artifact_endpoints_404_before_generation(client, deterministic_ai) -> None:
    test_client, _ = client
    analyzed = _upload_analyzed(test_client)
    project_id = analyzed["id"]
    for endpoint in (
        "/story-status", "/story", "/selected-scenes", "/duration-plan",
        "/script", "/script-quality",
    ):
        assert test_client.get(f"/api/projects/{project_id}{endpoint}").status_code == 404


# ----------------------------------------------------------------------
# System status
# ----------------------------------------------------------------------


def test_system_status_reports_llm_capabilities(client, fake_llm) -> None:
    test_client, _ = client
    body = test_client.get("/api/system/status").json()
    assert body["phase"] == "7"
    llm = body["llm"]
    assert llm["provider"] == "llama_cpp"
    assert llm["available"] in (True, False)
    assert llm["executable_available"] in (True, False)
    assert llm["model_available"] in (True, False)
    assert llm["threads"] == 4
    assert llm["context_size"] == 2048
    assert llm["max_tokens"] > 0
    # No full model path is ever exposed (basename only).
    assert "gguf" not in str(llm.get("model_name")) or "/" not in str(llm["model_name"])