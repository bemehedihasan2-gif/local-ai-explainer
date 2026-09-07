"""Local LLM provider abstraction (Phase 5).

The pipeline never depends on one Python binding and never keeps a model
resident. :class:`LlamaCppProvider` shells out to the llama.cpp CLI
(``llama-cli``) for each generation:

- no ``shell=True``, no persistent process (one model in RAM at a time);
- hard timeout that kills the subprocess;
- stdout/stderr captured and reported in useful error messages;
- the model is **never silently downloaded** - missing binaries/models are
  reported with explicit setup instructions;
- the configured model path is never exposed through API responses
  (``describe()`` only reports the file's basename).

``LocalLLMProvider`` is the small Protocol every future provider (e.g. a
persistent llama-server client, a Python binding, ONNX) must implement.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from app.utils.errors import (
    LLMGenerationError,
    LLMMalformedOutputError,
    LLMModelMissingError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.utils.logging import get_logger

logger = get_logger("app.ai.llm")

#: Log lines llama.cpp writes to stdout that must never reach callers.
_LLAMA_LOG_PREFIXES = (
    "llama_", "main:", "system_info:", "srv ", "log ", "ggml_",
    "print_info:", "sampler", "load ", "build:", "note:", "warn :",
    "error:", "AVX", "AVX2", "NEON", "CPU", "mem", "KV self", "gguf_",
    "llama_model_loader:", "n_ctx", "n_batch", "n_ubatch", "n_threads",
    "flash_attn", "shift_attn", "encode_", "decode_", "run_on_", "pp ", "tg ",
    "task ", "generate: ", "benchmark: ", "eot", "prompt:", "sampling:",
)

#: End-of-generation markers llama.cpp may append.
_END_MARKERS = ("[end of text]", "<|end|>", "<|im_end|>", "</s>", "<s>", "<eos>")

#: llama.cpp CLI binary name (newer releases call it ``llama-cli``).
_LLAMA_CLI_NAMES = ("llama-cli",)


class UnavailableProvider:
    """Provider returned when the LLM stage is disabled by configuration."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def available(self) -> bool:
        return False

    def generate(self, prompt: str, *, max_tokens: int, temperature: float = 0.2) -> str:
        raise LLMUnavailableError(self._reason)

    def describe(self) -> dict[str, Any]:
        return {
            "provider": "none",
            "available": False,
            "model_available": False,
            "model_name": None,
            "threads": None,
            "context_size": None,
            "max_tokens": None,
            "setup_hint": self._reason,
        }


@runtime_checkable
class LocalLLMProvider(Protocol):
    """Minimal contract every local LLM backend implements."""

    def available(self) -> bool:
        """True when the executable AND model are present and runnable."""
        ...

    def generate(
        self,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float = 0.2,
    ) -> str:
        """Run one generation; returns the model's raw text reply."""
        ...

    def describe(self) -> dict[str, Any]:
        """Public capability report (never exposes full filesystem paths)."""
        ...


class LlamaCppProvider:
    """llama.cpp CLI provider: one short-lived subprocess per generation.

    Executable resolution order: ``LLAMA_CPP_PATH`` env/setting, then
    ``llama-cli`` on PATH. Model resolution order: ``LLAMA_MODEL_PATH``,
    then a single ``*.gguf`` auto-discovered in the models directory
    (multiple candidates -> unavailable with a clear message).
    """

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        self._executable = self._resolve_executable()
        self._model_path = self._resolve_model_path()

    # -- resolution ------------------------------------------------------
    def _resolve_executable(self) -> str | None:
        configured = getattr(self._settings, "llama_cpp_path", None)
        if configured:
            candidate = str(configured)
            if os.path.isfile(candidate):
                return candidate
            logger.warning(
                "Configured LLAMA_CPP_PATH '%s' not found; trying PATH.", candidate
            )
        for name in _LLAMA_CLI_NAMES:
            found = shutil.which(name)
            if found:
                return found
        return None

    def _resolve_model_path(self) -> Path | None:
        configured = getattr(self._settings, "llama_model_path", None)
        if configured:
            path = Path(configured).expanduser()
            if not path.is_absolute():
                path = (Path(self._settings.base_dir) / path).resolve()
            return path if path.is_file() else path  # report missing clearly
        model_dir = Path(self._settings.model_dir)
        if not model_dir.is_dir():
            return None
        candidates = sorted(model_dir.glob("*.gguf"))
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            logger.warning(
                "Multiple .gguf models found in %s; set LLAMA_MODEL_PATH "
                "to choose one.", model_dir,
            )
        return None

    # -- availability ----------------------------------------------------
    def _executable_error(self) -> str | None:
        if self._executable is None:
            return (
                "The llama.cpp CLI ('llama-cli') was not found. Install "
                "llama.cpp (Windows: 'winget install llama.cpp' or the "
                "official GitHub release zip) and ensure 'llama-cli' is on "
                "PATH, or set LLAMA_CPP_PATH in .env to the binary. The app "
                "never downloads it automatically."
            )
        return None

    def _model_error(self) -> str | None:
        if self._model_path is None:
            return (
                "No GGUF model is configured. Place exactly one .gguf file "
                "in the models/ directory, or set LLAMA_MODEL_PATH in .env "
                "to a quantized GGUF. The app never downloads models - run "
                "the explicit setup from the README ('Phase 5 - first-run "
                "model setup') once."
            )
        if not self._model_path.is_file():
            return (
                f"The configured model '{self._model_path.name}' was not "
                "found at the configured location. Check LLAMA_MODEL_PATH "
                "and run the explicit model setup from the README. No "
                "automatic download is performed."
            )
        return None

    def available(self) -> bool:
        return self._executable_error() is None and self._model_error() is None

    # -- generation ------------------------------------------------------
    def generate(
        self,
        prompt: str,
        *,
        max_tokens: int,
        temperature: float = 0.2,
    ) -> str:
        exec_error = self._executable_error()
        if exec_error is not None:
            raise LLMUnavailableError(exec_error)
        model_error = self._model_error()
        if model_error is not None:
            raise LLMModelMissingError(model_error)

        args = [
            self._executable,
            "-m", str(self._model_path),
            "-p", prompt,
            "-n", str(max_tokens),
            "-t", str(self._settings.llama_threads),
            "-c", str(self._settings.llama_context_size),
            "--temp", f"{temperature:.2f}",
            "--seed", str(self._settings.llm_seed),
            "--no-display-prompt",
        ]
        logger.info(
            "llama.cpp generation (model=%s, threads=%s, ctx=%s, max_tokens=%s)",
            self._model_path.name, self._settings.llama_threads,
            self._settings.llama_context_size, max_tokens,
        )
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=self._settings.llama_timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:  # pragma: no cover - raced deletion
            raise LLMUnavailableError(
                f"The llama.cpp binary vanished between checks: {exc}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            logger.error(
                "llama.cpp timed out after %ss (model=%s).",
                self._settings.llama_timeout_seconds, self._model_path.name,
            )
            raise LLMTimeoutError(
                "The local language model did not finish within "
                f"{self._settings.llama_timeout_seconds}s. The process was "
                "terminated. Try again, or raise LLAMA_TIMEOUT_SECONDS for "
                "very long generations."
            ) from exc

        if proc.returncode != 0:
            stderr_tail = (proc.stderr or "").strip().splitlines()[-6:]
            logger.error(
                "llama.cpp exited with code %s (model=%s).",
                proc.returncode, self._model_path.name,
            )
            raise LLMGenerationError(
                "The local language model process failed "
                f"(exit code {proc.returncode}). "
                + (" ".join(stderr_tail) if stderr_tail else "")
            )

        output = clean_llm_output(proc.stdout, prompt)
        if not output.strip():
            logger.error("llama.cpp produced empty output (model=%s).", self._model_path.name)
            raise LLMGenerationError(
                "The local language model returned an empty answer. Check "
                "the model file (LLAMA_MODEL_PATH) - it may be corrupt or "
                "an incompatible format."
            )
        return output.strip()

    # -- reporting -------------------------------------------------------
    def describe(self) -> dict[str, Any]:
        exec_error = self._executable_error()
        model_error = self._model_error()
        return {
            "provider": "llama_cpp",
            "available": exec_error is None and model_error is None,
            "executable_available": exec_error is None,
            "model_available": model_error is None,
            "model_name": self._model_path.name if self._model_path else None,
            "threads": self._settings.llama_threads,
            "context_size": self._settings.llama_context_size,
            "max_tokens": self._settings.llama_max_tokens,
            "temperature": self._settings.llm_temperature,
            "setup_hint": model_error or exec_error,
        }


def build_llm_provider(settings: Any) -> LocalLLMProvider:
    """Create the configured provider (``none`` -> honest refusal)."""
    if getattr(settings, "llm_provider", "llama_cpp") == "none":
        return UnavailableProvider(
            "The local LLM stage is disabled (LLM_PROVIDER=none). Set it "
            "to 'llama_cpp' and configure LLAMA_CPP_PATH / LLAMA_MODEL_PATH "
            "to generate explanations."
        )
    return LlamaCppProvider(settings)


def llm_model_identity(settings: Any) -> str | None:
    """Stable identity used in generation fingerprints (basename only)."""
    provider = LlamaCppProvider(settings)
    if provider._model_path is None:  # noqa: SLF001 - same-module resolution
        return None
    return provider._model_path.name


# ----------------------------------------------------------------------
# Output post-processing
# ----------------------------------------------------------------------


def clean_llm_output(raw: str, prompt: str) -> str:
    """Strip llama.cpp log noise, an echoed prompt and end markers.

    Works without depending on ``--no-display-prompt`` support: the last
    occurrence of the prompt tail (normalized whitespace) is used to cut
    any echoed prompt prefix, and known log-line prefixes are removed.
    """
    lines = raw.splitlines()
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        # Blank lines are kept: they are paragraph separators in the answer.
        if not stripped:
            kept.append(line)
            continue
        lowered = stripped.lower()
        if any(lowered.startswith(prefix.lower()) for prefix in _LLAMA_LOG_PREFIXES):
            continue
        if any(stripped.startswith(m) for m in _END_MARKERS):
            continue
        kept.append(line)

    text = "\n".join(kept)
    # Cut an echoed prompt: find the prompt tail (last 40 normalized chars)
    # in the normalized output and drop everything up to and including it.
    tail = " ".join(prompt.split())[-40:]
    if tail:
        normalized = text.replace("\n", " ")
        index = normalized.rfind(tail)
        if index >= 0:
            cut = index + len(tail)
            text = text[cut:]

    # Re-normalize: keep paragraph breaks, drop over-long blank runs.
    import re

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    for marker in _END_MARKERS:
        text = text.replace(marker, "")
    return text.strip()


def extract_json(text: str) -> dict[str, Any]:
    """Extract the first balanced JSON object from model output.

    Tolerates markdown fences, prose around the JSON and trailing noise.
    Raises :class:`LLMMalformedOutputError` when no object can be parsed.
    """
    cleaned = text.strip()
    # Strip code fences first (```json ... ```).
    if "```" in cleaned:
        parts = cleaned.split("```")
        cleaned = parts[1] if len(parts) >= 3 else cleaned
    start = cleaned.find("{")
    if start < 0:
        raise LLMMalformedOutputError(
            "The model output contained no JSON object. "
            f"Output preview: {text[:200]!r}"
        )
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = cleaned[start : index + 1]
                try:
                    parsed = json.loads(candidate)
                except json.JSONDecodeError as exc:
                    raise LLMMalformedOutputError(
                        f"Model returned invalid JSON: {exc}. "
                        f"Output preview: {candidate[:200]!r}"
                    ) from exc
                if not isinstance(parsed, dict):
                    raise LLMMalformedOutputError(
                        "Model returned a JSON array instead of an object."
                    )
                return parsed
    raise LLMMalformedOutputError(
        "The model output was truncated before a complete JSON object "
        f"appeared. Output preview: {text[:200]!r}"
    )


__all__ = [
    "LocalLLMProvider",
    "LlamaCppProvider",
    "UnavailableProvider",
    "build_llm_provider",
    "llm_model_identity",
    "clean_llm_output",
    "extract_json",
]