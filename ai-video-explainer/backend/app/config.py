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
    app_version: str = "0.3.0"
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
