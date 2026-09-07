"""Central configuration.

Loaded from environment variables / ``.env`` via pydantic-settings.
No machine-specific absolute paths are hard-coded: every directory defaults
to a location *relative to the project root*, so the project can live
anywhere on disk. Everything can be overridden with environment variables
(see ``env.example`` at the project root).

Relative directory values are resolved against ``base_dir``; absolute
values (e.g. ``DATABASE_PATH=D:\\data\\explainer.db``) are used as-is.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root: backend/app/config.py -> backend -> project root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

_DIR_FIELDS = (
    "data_dir",
    "upload_dir",
    "projects_dir",
    "temp_dir",
    "output_dir",
    "cache_dir",
    "model_dir",
    "logs_dir",
)


class Settings(BaseSettings):
    """All tunable application settings. Values may come from ``.env``."""

    # ``.env`` lives at the project root (next to ``env.example``), regardless
    # of the working directory the backend is started from.
    model_config = SettingsConfigDict(
        env_file=Path(PROJECT_ROOT) / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Application -------------------------------------------------------
    app_name: str = "Local AI Video Explainer"
    app_version: str = "0.5.0"
    environment: str = "development"

    # Servers -----------------------------------------------------------
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    frontend_url: str = "http://127.0.0.1:5173"
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"]
    )

    # Limits & concurrency ----------------------------------------------
    #: Maximum single upload size in MB (enforced during streaming upload).
    max_upload_size_mb: int = 4096
    #: Upload is streamed to disk in chunks of this many bytes. 1 MiB keeps
    #: per-request memory near zero even for multi-GB videos.
    upload_chunk_size: int = 1024 * 1024
    #: Video containers accepted by the upload engine (extension allowlist).
    #: Values are normalized (lowercase, leading dot) by the validator.
    allowed_video_extensions: list[str] = [
        ".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".mpeg", ".mpg", ".ts",
    ]
    processing_concurrency: int = 1  # one heavy video job at a time (8 GB RAM)

    # FFmpeg -------------------------------------------------------------
    ffmpeg_path: Path | None = None  # None -> discover on PATH
    ffprobe_path: Path | None = None
    #: Upper bound for a single ffprobe invocation on a large/corrupt file.
    ffprobe_timeout_seconds: int = 60

    # Phase 3 - preprocessing / analysis assets ---------------------------
    #: Width cap of the analysis copy. Height follows aspect ratio (even
    #: pixels). Kept modest: later vision/OCR stages run on this copy, and
    #: the target machine is CPU-only with 8 GB RAM.
    analysis_width: int = 640
    #: Constant frame rate of the analysis copy. The fps filter duplicates
    #: frames when the source is slower, so the output is exactly this rate.
    analysis_fps: int = 5
    #: Analysis video encoder: ultrafast/veryfast keep 8 GB machines usable.
    analysis_encoder_preset: str = "veryfast"
    analysis_crf: int = 30
    #: Width cap of the poster JPEG thumbnail (aspect ratio preserved).
    thumbnail_width: int = 320
    #: Audio extraction settings for later speech-to-text (16 kHz mono WAV
    #: is the universal local-STT input; pcm_s16le keeps decoding trivial).
    audio_sample_rate: int = 16000
    audio_channels: int = 1
    #: Upper bound for one ffmpeg preprocessing step (analysis copy,
    #: thumbnail or audio extraction) on a large video.
    preprocess_timeout_seconds: int = 600

    # Phase 4 - local analysis -------------------------------------------
    #: Upper bound for one analysis stage (scene detection pass, whisper
    #: transcription, OCR batch, visual pass) on a large video.
    analysis_timeout_seconds: int = 1800

    # Speech-to-text (faster-whisper, CPU-only). The model is lazy-loaded
    # when an analysis job starts and released afterwards - the web server
    # must not hold hundreds of MB while idle.
    whisper_model: str = "tiny"  # "tiny" | "base" (CPU-friendly defaults only)
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    # "preferred": hint Whisper with the project's UI language but let it
    # correct itself; "auto": pure auto-detection; "forced": force the
    # project language (used when the user knows the audio language).
    whisper_language_mode: str = "preferred"

    # Scene detection (FFmpeg scene filter - deterministic, no extra deps)
    scene_threshold: float = 0.3       # 0..1: lower = more scene changes
    min_scene_duration_seconds: float = 2.0
    max_scenes: int = 500

    # OCR (Tesseract)
    ocr_enabled: bool = True
    #: Cap on how many representative frames are OCR'd (they are processed
    #: in scene order, so early scenes win when the cap is hit).
    ocr_frame_limit: int = 60
    tesseract_path: Path | None = None  # None -> discover on PATH

    # Visual frame analysis (deterministic PIL metadata by default; a local
    # vision model can be plugged in later via LocalVisionProvider).
    visual_analysis_enabled: bool = True

    # Phase 5 - local LLM (story understanding + script generation) -------
    # Provider abstraction: the app shells out to an external llama.cpp CLI
    # (no Python binding, no always-loaded model). "none" disables the
    # stage explicitly; the pipeline then refuses with a clear message.
    llm_provider: str = "llama_cpp"  # "llama_cpp" | "none"
    #: Path to the llama.cpp CLI binary (``llama-cli``). None -> PATH lookup.
    llama_cpp_path: Path | None = None
    #: Path to a quantized GGUF model. None -> auto-discover a single
    #: ``*.gguf`` inside the models directory. The app never downloads one.
    llama_model_path: Path | None = None
    llama_threads: int = 4            # Ryzen 3 3200G = 4 cores, CPU-only
    llama_context_size: int = 2048    # small context keeps 8 GB usable
    llama_max_tokens: int = 1024      # ~600 words + prompt overhead
    llama_timeout_seconds: int = 300  # one generation must finish
    llm_temperature: float = 0.2      # low = grounded, repeatable narration
    llm_seed: int = 42                # deterministic-ish local sampling

    # Story understanding (evidence compression + hierarchical batches)
    story_batch_scenes: int = 15      # scenes per batch summary (long videos)
    #: Per-scene transcript/OCR excerpt cap in characters (bounded prompts).
    story_max_excerpt_chars: int = 240
    story_prompt_version: str = "1.0"
    script_prompt_version: str = "1.0"
    planner_version: str = "1.0"

    # Narration pacing: word targets per duration (min, max) and WPM.
    narration_wpm: int = 145
    script_word_targets: dict[int, list[int]] = {
        120: [250, 300],   # 2 minutes
        180: [375, 450],   # 3 minutes
        240: [500, 600],   # 4 minutes
    }

    # Scene importance weights (documented; additive part sums to 0.95 and
    # is re-normalized to 100%; the redundancy adjustment applies up to a
    # 5% multiplicative penalty on top).
    importance_weight_information_density: float = 0.25
    importance_weight_speech_density: float = 0.15
    importance_weight_semantic: float = 0.25
    importance_weight_turning_point: float = 0.15
    importance_weight_continuity: float = 0.10
    importance_weight_ocr: float = 0.05
    importance_redundancy_penalty: float = 0.05

    # Script budget clamps (per-scene narration words).
    min_words_per_scene: int = 15
    max_words_per_scene: int = 110
    min_scenes_per_script: int = 3
    max_selected_scenes: int = 24

    # Storage (relative -> resolved against base_dir by the validator) ---
    base_dir: Path = PROJECT_ROOT
    data_dir: Path = Path("data")
    upload_dir: Path = Path("data/uploads")
    projects_dir: Path = Path("data/projects")
    temp_dir: Path = Path("data/temp")
    output_dir: Path = Path("data/outputs")
    cache_dir: Path = Path("data/cache")
    model_dir: Path = Path("models")
    logs_dir: Path = Path("logs")
    database_path: Path = Path("data/explainer.db")

    # Logging ------------------------------------------------------------
    log_level: str = "INFO"
    log_max_bytes: int = 5 * 1024 * 1024
    log_backup_count: int = 3

    # ------------------------------------------------------------------
    # Validation & normalization
    # ------------------------------------------------------------------
    @field_validator("environment")
    @classmethod
    def _env_must_be_known(cls, value: str) -> str:
        allowed = {"development", "production", "test"}
        if value.lower() not in allowed:
            raise ValueError(f"environment must be one of {sorted(allowed)}")
        return value.lower()

    @field_validator("processing_concurrency")
    @classmethod
    def _concurrency_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("processing_concurrency must be >= 1")
        return value

    @field_validator("max_upload_size_mb")
    @classmethod
    def _max_upload_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_upload_size_mb must be >= 1")
        return value

    @field_validator("upload_chunk_size")
    @classmethod
    def _chunk_size_positive(cls, value: int) -> int:
        if value < 1024:
            raise ValueError("upload_chunk_size must be at least 1 KiB")
        return value

    @field_validator("ffprobe_timeout_seconds")
    @classmethod
    def _ffprobe_timeout_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("ffprobe_timeout_seconds must be >= 1")
        return value

    @field_validator("preprocess_timeout_seconds")
    @classmethod
    def _preprocess_timeout_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("preprocess_timeout_seconds must be >= 1")
        return value

    @field_validator("analysis_width")
    @classmethod
    def _analysis_width_positive(cls, value: int) -> int:
        if value < 16:
            raise ValueError("analysis_width must be at least 16 px")
        return value

    @field_validator("analysis_fps")
    @classmethod
    def _analysis_fps_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("analysis_fps must be >= 1")
        return value

    @field_validator("audio_sample_rate")
    @classmethod
    def _audio_rate_positive(cls, value: int) -> int:
        if value < 8000:
            raise ValueError("audio_sample_rate must be >= 8000")
        return value

    @field_validator("whisper_model")
    @classmethod
    def _whisper_model_allowed(cls, value: str) -> str:
        value = value.strip().lower()
        allowed = {"tiny", "base"}
        if value not in allowed:
            raise ValueError(
                f"whisper_model must be one of {sorted(allowed)} "
                "(CPU-friendly sizes only on the 8 GB target)"
            )
        return value

    @field_validator("whisper_device")
    @classmethod
    def _whisper_device_allowed(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"cpu", "cuda", "auto"}:
            raise ValueError("whisper_device must be 'cpu', 'cuda' or 'auto'")
        return value

    @field_validator("whisper_compute_type")
    @classmethod
    def _whisper_compute_allowed(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"int8", "int8_float16", "float16", "float32"}:
            raise ValueError(
                "whisper_compute_type must be int8, int8_float16, float16 or float32"
            )
        return value

    @field_validator("whisper_language_mode")
    @classmethod
    def _whisper_language_mode_allowed(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"preferred", "auto", "forced"}:
            raise ValueError(
                "whisper_language_mode must be 'preferred', 'auto' or 'forced'"
            )
        return value

    @field_validator("scene_threshold")
    @classmethod
    def _scene_threshold_range(cls, value: float) -> float:
        if not 0.0 < value <= 1.0:
            raise ValueError("scene_threshold must be in (0, 1]")
        return value

    @field_validator("min_scene_duration_seconds")
    @classmethod
    def _min_scene_duration_positive(cls, value: float) -> float:
        if value < 0.1:
            raise ValueError("min_scene_duration_seconds must be >= 0.1")
        return value

    @field_validator("max_scenes")
    @classmethod
    def _max_scenes_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_scenes must be >= 1")
        return value

    @field_validator("ocr_frame_limit")
    @classmethod
    def _ocr_frame_limit_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("ocr_frame_limit must be >= 1")
        return value

    @field_validator("analysis_timeout_seconds")
    @classmethod
    def _analysis_timeout_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("analysis_timeout_seconds must be >= 1")
        return value

    @field_validator("llm_provider")
    @classmethod
    def _llm_provider_allowed(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in {"llama_cpp", "none"}:
            raise ValueError("llm_provider must be 'llama_cpp' or 'none'")
        return value

    @field_validator("llama_threads")
    @classmethod
    def _llama_threads_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("llama_threads must be >= 1")
        return value

    @field_validator("llama_context_size")
    @classmethod
    def _llama_context_positive(cls, value: int) -> int:
        if value < 512:
            raise ValueError("llama_context_size must be >= 512")
        return value

    @field_validator("llama_max_tokens")
    @classmethod
    def _llama_max_tokens_positive(cls, value: int) -> int:
        if value < 64:
            raise ValueError("llama_max_tokens must be >= 64")
        return value

    @field_validator("llama_timeout_seconds")
    @classmethod
    def _llama_timeout_positive(cls, value: int) -> int:
        if value < 10:
            raise ValueError("llama_timeout_seconds must be >= 10")
        return value

    @field_validator("llm_temperature")
    @classmethod
    def _llm_temperature_range(cls, value: float) -> float:
        if not 0.0 <= value <= 2.0:
            raise ValueError("llm_temperature must be in [0, 2]")
        return value

    @field_validator("story_batch_scenes")
    @classmethod
    def _story_batch_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("story_batch_scenes must be >= 1")
        return value

    @field_validator("story_max_excerpt_chars")
    @classmethod
    def _story_excerpt_positive(cls, value: int) -> int:
        if value < 40:
            raise ValueError("story_max_excerpt_chars must be >= 40")
        return value

    @field_validator("narration_wpm")
    @classmethod
    def _narration_wpm_range(cls, value: int) -> int:
        if not 60 <= value <= 400:
            raise ValueError("narration_wpm must be in [60, 400]")
        return value

    @field_validator("script_word_targets")
    @classmethod
    def _script_word_targets_valid(cls, value: dict[int, list[int]]) -> dict[int, list[int]]:
        if set(value) != {120, 180, 240}:
            raise ValueError("script_word_targets must define 120, 180 and 240 seconds")
        for duration, (low, high) in value.items():
            if not (0 < low < high):
                raise ValueError(f"script_word_targets[{duration}] must be (min, max) with min < max")
        return value

    @field_validator("importance_weight_information_density")
    @classmethod
    def _importance_weight_information(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("importance_weight_information_density must be in [0, 1]")
        return value

    @field_validator("importance_weight_speech_density")
    @classmethod
    def _importance_weight_speech(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("importance_weight_speech_density must be in [0, 1]")
        return value

    @field_validator("importance_weight_semantic")
    @classmethod
    def _importance_weight_semantic(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("importance_weight_semantic must be in [0, 1]")
        return value

    @field_validator("importance_weight_turning_point")
    @classmethod
    def _importance_weight_turning(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("importance_weight_turning_point must be in [0, 1]")
        return value

    @field_validator("importance_weight_continuity")
    @classmethod
    def _importance_weight_continuity(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("importance_weight_continuity must be in [0, 1]")
        return value

    @field_validator("importance_weight_ocr")
    @classmethod
    def _importance_weight_ocr(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("importance_weight_ocr must be in [0, 1]")
        return value

    @field_validator("importance_redundancy_penalty")
    @classmethod
    def _importance_redundancy(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("importance_redundancy_penalty must be in [0, 1]")
        return value

    @model_validator(mode="after")
    def _importance_weights_consistent(self) -> "Settings":
        additive = (
            self.importance_weight_information_density
            + self.importance_weight_speech_density
            + self.importance_weight_semantic
            + self.importance_weight_turning_point
            + self.importance_weight_continuity
            + self.importance_weight_ocr
        )
        if not 0.94 <= additive <= 0.96:
            raise ValueError(
                "The six additive importance weights must sum to 0.95 "
                f"(got {additive:.3f}); the redundancy adjustment is the "
                "remaining 5%."
            )
        if not 0.0 <= self.importance_redundancy_penalty <= 0.1:
            raise ValueError("importance_redundancy_penalty must be in [0, 0.1]")
        if self.min_words_per_scene < 1 or self.max_words_per_scene < self.min_words_per_scene:
            raise ValueError("min/max_words_per_scene are inconsistent")
        if self.min_scenes_per_script < 1 or self.max_selected_scenes < self.min_scenes_per_script:
            raise ValueError("min_scenes_per_script/max_selected_scenes are inconsistent")
        return self

    @field_validator("allowed_video_extensions")
    @classmethod
    def _normalize_extensions(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for ext in value:
            ext = ext.strip().lower()
            if not ext.startswith("."):
                ext = f".{ext}"
            if ext not in normalized:
                normalized.append(ext)
        if not normalized:
            raise ValueError("allowed_video_extensions must not be empty")
        return normalized

    @field_validator("log_level")
    @classmethod
    def _log_level_known(cls, value: str) -> str:
        value = value.upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if value not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return value

    @model_validator(mode="after")
    def _resolve_paths(self) -> "Settings":
        base = self.base_dir.expanduser()
        if not base.is_absolute():
            base = (Path.cwd() / base).resolve()
        base = base.resolve()
        self.base_dir = base

        for field_name in _DIR_FIELDS:
            path: Path = getattr(self, field_name)
            if not path.is_absolute():
                setattr(self, field_name, (base / path).resolve())

        db = self.database_path
        if not db.is_absolute():
            db = base / db
        self.database_path = db.resolve()
        return self

    # Convenience ---------------------------------------------------------
    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @property
    def storage_directories(self) -> dict[str, Path]:
        """Named managed directories, in the order used by system status."""
        return {
            "data": self.data_dir,
            "uploads": self.upload_dir,
            "projects": self.projects_dir,
            "temp": self.temp_dir,
            "outputs": self.output_dir,
            "cache": self.cache_dir,
            "models": self.model_dir,
            "logs": self.logs_dir,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide cached settings (used by ``app.main:app``)."""
    return Settings()
