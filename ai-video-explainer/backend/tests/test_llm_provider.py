"""Phase 5: local LLM provider tests.

``LlamaCppProvider`` runs an external binary, so the tests use tiny fake
executables (bash scripts, same pattern as ``_media_helpers``) to exercise
the full subprocess contract: missing binary, missing model, clean output,
timeout, malformed output, non-zero exit.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from app.ai.llm import (
    LlamaCppProvider,
    UnavailableProvider,
    build_llm_provider,
    clean_llm_output,
    extract_json,
)
from app.utils.errors import (
    LLMGenerationError,
    LLMMalformedOutputError,
    LLMModelMissingError,
    LLMTimeoutError,
    LLMUnavailableError,
)

pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="Fake CLI executables (shebang + chmod) require POSIX.",
)


def _write_executable(path: Path, source: str) -> str:
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _make_model(tmp_path: Path, name: str = "model.gguf") -> Path:
    model = tmp_path / name
    model.write_bytes(b"GGUF fake model bytes")
    return model


def _provider(settings, *, exe: str | None, model: Path | None) -> LlamaCppProvider:
    settings.llama_cpp_path = exe
    settings.llama_model_path = model
    return LlamaCppProvider(settings)


# ----------------------------------------------------------------------
# Availability & configuration errors
# ----------------------------------------------------------------------


def test_executable_missing_reports_unavailable(settings, tmp_path) -> None:
    model = _make_model(tmp_path)
    provider = _provider(settings, exe=str(tmp_path / "missing-llama-cli"), model=model)
    assert provider.available() is False
    detail = provider.describe()
    assert detail["executable_available"] is False
    assert detail["model_name"] == "model.gguf"  # basename only - no path
    assert "llama-cli" in (detail["setup_hint"] or "")
    assert str(model.parent) not in json.dumps(detail)  # never expose paths

    with pytest.raises(LLMUnavailableError) as excinfo:
        provider.generate("hello", max_tokens=64)
    assert "llama-cli" in str(excinfo.value)
    assert "LLAMA_CPP_PATH" in str(excinfo.value)


def test_model_missing_reports_download_required(settings, tmp_path) -> None:
    exe = _write_executable(
        tmp_path / "llama-cli",
        "#!/usr/bin/env bash\necho fake\n",
    )
    provider = _provider(
        settings, exe=exe, model=tmp_path / "does-not-exist.gguf"
    )
    assert provider.available() is False
    detail = provider.describe()
    assert detail["model_available"] is False
    assert "does-not-exist.gguf" in (detail["setup_hint"] or "")
    assert "automatic download" in (detail["setup_hint"] or "").lower()  # no auto download

    with pytest.raises(LLMModelMissingError) as excinfo:
        provider.generate("hello", max_tokens=64)
    assert "LLAMA_MODEL_PATH" in str(excinfo.value)


def test_no_model_configured_is_unavailable(settings, tmp_path) -> None:
    exe = _write_executable(
        tmp_path / "llama-cli",
        "#!/usr/bin/env bash\necho fake\n",
    )
    settings.llama_cpp_path = exe
    settings.llama_model_path = None
    empty_dir = tmp_path / "empty-models"
    empty_dir.mkdir()
    settings.model_dir = empty_dir
    provider = LlamaCppProvider(settings)
    assert provider.available() is False
    assert "gguf" in (provider.describe()["setup_hint"] or "").lower()


def test_single_gguf_in_models_dir_is_discovered(settings, tmp_path) -> None:
    exe = _write_executable(
        tmp_path / "llama-cli",
        "#!/usr/bin/env bash\necho fake\n",
    )
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    _make_model(model_dir, name="tiny-q4.gguf")
    settings.llama_cpp_path = exe
    settings.llama_model_path = None
    settings.model_dir = model_dir
    provider = LlamaCppProvider(settings)
    assert provider.available() is True
    assert provider.describe()["model_name"] == "tiny-q4.gguf"


def test_llm_provider_none_disables_stage(settings) -> None:
    settings.llm_provider = "none"
    provider = build_llm_provider(settings)
    assert isinstance(provider, UnavailableProvider)
    assert provider.available() is False
    with pytest.raises(LLMUnavailableError) as excinfo:
        provider.generate("hello", max_tokens=64)
    assert "LLM_PROVIDER=none" in str(excinfo.value)


# ----------------------------------------------------------------------
# Generation behavior
# ----------------------------------------------------------------------


def test_generate_returns_cleaned_output(settings, tmp_path) -> None:
    exe = _write_executable(
        tmp_path / "llama-cli",
        "#!/usr/bin/env bash\n"
        "echo 'llama_model_loader: loading model'\n"
        "echo 'main: n_ctx = 2048'\n"
        "echo 'This is the model answer.'\n"
        "echo '[end of text]'\n",
    )
    provider = _provider(settings, exe=exe, model=_make_model(tmp_path))
    output = provider.generate("Tell me something", max_tokens=128)
    assert output == "This is the model answer."
    assert "llama_model_loader" not in output
    assert "main:" not in output
    assert "[end of text]" not in output


def test_generate_timeout_kills_subprocess(settings, tmp_path) -> None:
    exe = _write_executable(
        tmp_path / "llama-cli",
        "#!/usr/bin/env bash\nsleep 30\necho late\n",
    )
    settings.llama_timeout_seconds = 1
    provider = _provider(settings, exe=exe, model=_make_model(tmp_path))
    with pytest.raises(LLMTimeoutError) as excinfo:
        provider.generate("hello", max_tokens=64)
    assert "terminated" in str(excinfo.value)


def test_generate_subprocess_failure_reports_exit_code(settings, tmp_path) -> None:
    exe = _write_executable(
        tmp_path / "llama-cli",
        "#!/usr/bin/env bash\necho 'error: bad model format' >&2\nexit 3\n",
    )
    provider = _provider(settings, exe=exe, model=_make_model(tmp_path))
    with pytest.raises(LLMGenerationError) as excinfo:
        provider.generate("hello", max_tokens=64)
    message = str(excinfo.value)
    assert "exit code 3" in message
    assert "bad model format" in message


def test_generate_empty_output_raises(settings, tmp_path) -> None:
    exe = _write_executable(
        tmp_path / "llama-cli",
        "#!/usr/bin/env bash\necho 'llama_log_only_line'\n",
    )
    provider = _provider(settings, exe=exe, model=_make_model(tmp_path))
    with pytest.raises(LLMGenerationError) as excinfo:
        provider.generate("hello", max_tokens=64)
    assert "empty" in str(excinfo.value).lower()


# ----------------------------------------------------------------------
# Output cleaning & JSON extraction
# ----------------------------------------------------------------------


def test_clean_llm_output_strips_prompt_echo_and_logs() -> None:
    prompt = "Respond with JSON about the video."
    raw = (
        "llama_model_loader: loaded meta\n"
        "main: prompt eval\n"
        f"{prompt}\n"
        "Here is the real answer.\n"
        "\n"
        "Second paragraph.\n"
        "[end of text]"
    )
    cleaned = clean_llm_output(raw, prompt)
    assert cleaned == "Here is the real answer.\n\nSecond paragraph."
    assert prompt not in cleaned
    assert "llama_model_loader" not in cleaned


def test_extract_json_plain_and_fenced() -> None:
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Here is the answer:\n```json\n{"a": [1, 2]}\n```\nDone.') == {
        "a": [1, 2]
    }
    assert extract_json('prose {"nested": {"deep": true}} trailing') == {
        "nested": {"deep": True}
    }


def test_extract_json_handles_strings_with_braces() -> None:
    text = '{"text": "a { b } c", "n": 2}'
    assert extract_json(text) == {"text": "a { b } c", "n": 2}


def test_extract_json_rejects_garbage() -> None:
    for bad in ("no json here", "{unbalanced", "[]", "{\"a\": }"):
        with pytest.raises(LLMMalformedOutputError):
            extract_json(bad)