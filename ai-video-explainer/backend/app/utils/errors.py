"""Central application error hierarchy.

Every failure that should reach an API client as a clean, understandable
message is raised as an ``ExplainerError`` subclass. FastAPI exception
handlers registered in :mod:`app.main` translate them into JSON responses
with the right HTTP status code. Unknown errors are logged (never returned
verbatim) and mapped to a generic 500 response.
"""

from __future__ import annotations


class ExplainerError(Exception):
    """Base class for all application errors surfaced to the API."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str | None = None,
        *,
        project_id: str | None = None,
    ) -> None:
        super().__init__(message or self.default_message)
        self.message = message or self.default_message
        self.project_id = project_id

    default_message = "An internal error occurred."

    def public_detail(self) -> str:
        return self.message


class ConfigError(ExplainerError):
    """Invalid or inconsistent application configuration."""

    status_code = 500
    code = "invalid_configuration"
    default_message = "Application configuration is invalid."


class DatabaseOperationError(ExplainerError):
    """A SQLite operation failed (raised after the underlying exception)."""

    status_code = 500
    code = "database_error"
    default_message = "A database error occurred."


class StorageError(ExplainerError):
    """A filesystem operation on managed storage directories failed."""

    status_code = 500
    code = "filesystem_error"
    default_message = "A filesystem error occurred."


class StorageUnavailableError(StorageError):
    """A required storage directory is missing or not writable."""

    status_code = 503
    code = "storage_unavailable"
    default_message = "Required storage directories are not available."


class FFmpegUnavailableError(ExplainerError):
    """FFmpeg/ffprobe binaries could not be located or executed."""

    status_code = 503
    code = "ffmpeg_unavailable"
    default_message = (
        "FFmpeg was not found. Install FFmpeg (https://ffmpeg.org) and make "
        "sure 'ffmpeg' and 'ffprobe' are on PATH, or set FFMPEG_PATH/"
        "FFPROBE_PATH in your .env file."
    )


class ProjectNotFoundError(ExplainerError):
    """Requested project does not exist (unknown or malformed id)."""

    status_code = 404
    code = "project_not_found"
    default_message = "Project not found."


class MissingFileError(ExplainerError):
    """No file part / empty filename was supplied for an upload."""

    status_code = 400
    code = "missing_file"
    default_message = "No file was provided with the upload request."


class UnsupportedFileTypeError(ExplainerError):
    """File extension is not in the configured video container allowlist."""

    status_code = 400
    code = "unsupported_file_type"
    default_message = "The file type is not supported."


class UploadTooLargeError(ExplainerError):
    """Upload exceeds the configured maximum size."""

    status_code = 413
    code = "upload_too_large"
    default_message = "Video file exceeds the maximum allowed upload size."


class InvalidVideoError(ExplainerError):
    """FFprobe could not validate the file as a usable video.

    Covers corrupt files, non-video files and files whose metadata is
    impossible (empty, no video stream, no duration, no dimensions).
    """

    status_code = 422
    code = "invalid_video"
    default_message = "The file could not be validated as a video."


class DuplicateVideoError(ExplainerError):
    """The exact same file content (SHA-256) was uploaded before."""

    status_code = 409
    code = "duplicate_video"
    default_message = "This exact video was already uploaded."

    def __init__(
        self,
        message: str | None = None,
        *,
        project_id: str | None = None,
        existing_project_id: str | None = None,
        existing_filename: str | None = None,
    ) -> None:
        super().__init__(message, project_id=project_id)
        self.existing_project_id = existing_project_id
        self.existing_filename = existing_filename


class InvalidParameterError(ExplainerError):
    """A request field failed domain validation (language, duration, ...)."""

    status_code = 422
    code = "invalid_parameter"
    default_message = "A request parameter is invalid."


class ProjectNotReadyError(ExplainerError):
    """An operation requires a validated, READY project (e.g. preprocessing)."""

    status_code = 409
    code = "project_not_ready"
    default_message = (
        "This project is not ready for processing. Wait for validation "
        "to finish, or re-upload the video."
    )


class JobConflictError(ExplainerError):
    """A processing job for this project is already queued or running."""

    status_code = 409
    code = "job_conflict"
    default_message = "A processing job is already queued or running for this project."


class PreprocessError(ExplainerError):
    """FFmpeg preprocessing failed (analysis copy / thumbnail / audio)."""

    status_code = 500
    code = "preprocess_failed"
    default_message = "Video preprocessing failed."


class AssetNotFoundError(ExplainerError):
    """A generated asset (e.g. thumbnail) does not exist for this project."""

    status_code = 404
    code = "asset_not_found"
    default_message = "The requested asset is not available for this project."


class AnalysisNotReadyError(ExplainerError):
    """Analysis requires a PREPARED project (preprocessing must run first)."""

    status_code = 409
    code = "analysis_not_ready"
    default_message = (
        "This project cannot be analyzed yet. Run preprocessing first "
        "(Prepare for analysis) to build its analysis assets."
    )


class AnalysisConflictError(ExplainerError):
    """An analysis job is already queued or running for this project."""

    status_code = 409
    code = "analysis_already_running"
    default_message = "Analysis is already running for this project."


class WhisperUnavailableError(ExplainerError):
    """faster-whisper is not installed (or could not be imported)."""

    status_code = 503
    code = "whisper_unavailable"
    default_message = (
        "Speech-to-text is not available: the faster-whisper package is "
        "not installed. Run 'pip install faster-whisper' and restart the "
        "backend. The rest of the analysis still runs."
    )


class WhisperModelMissingError(ExplainerError):
    """The configured Whisper model files are not present locally."""

    status_code = 503
    code = "model_download_required"
    default_message = (
        "The Whisper model is not installed. See the README ("
        "'Phase 4 - first-run model setup') for the one-time download "
        "command; no API key is needed. Analysis continues without speech "
        "recognition until the model is installed."
    )


class TesseractUnavailableError(ExplainerError):
    """The Tesseract binary or pytesseract package is missing."""

    status_code = 503
    code = "tesseract_unavailable"
    default_message = (
        "OCR is not available: Tesseract is not installed. Install it "
        "(e.g. 'winget install UB-Mannheim.TesseractOCR' on Windows) and "
        "restart the backend. The rest of the analysis still runs."
    )


class SceneDetectionError(ExplainerError):
    """FFmpeg scene detection failed or produced unusable output."""

    status_code = 500
    code = "scene_detection_failed"
    default_message = "Scene detection failed."


class AnalysisError(ExplainerError):
    """The composite Phase 4 analysis job failed as a whole."""

    status_code = 500
    code = "analysis_failed"
    default_message = "Video analysis failed."


class LLMUnavailableError(ExplainerError):
    """The local LLM provider/executable is missing or not configured."""

    status_code = 503
    code = "llm_unavailable"
    default_message = (
        "The local language model is not available. Phase 5 needs llama.cpp "
        "(the 'llama-cli' binary) and a small quantized GGUF model. The app "
        "never downloads models automatically - see the README 'Phase 5 - "
        "first-run model setup' section for the explicit one-time install, "
        "or set LLAMA_CPP_PATH / LLAMA_MODEL_PATH in your .env file."
    )


class LLMModelMissingError(ExplainerError):
    """The configured GGUF model file does not exist locally."""

    status_code = 503
    code = "model_download_required"
    default_message = (
        "The local language model file is missing. No automatic download "
        "is performed - run the explicit setup (see README 'Phase 5 - "
        "first-run model setup') to fetch a small quantized GGUF, then "
        "point LLAMA_MODEL_PATH at it (or drop it into the models "
        "directory)."
    )


class LLMTimeoutError(ExplainerError):
    """A llama.cpp generation exceeded its configured timeout."""

    status_code = 504
    code = "llm_timeout"
    default_message = "The local language model did not answer in time."


class LLMMalformedOutputError(ExplainerError):
    """The model answered with text that could not be parsed as requested."""

    status_code = 500
    code = "llm_malformed_output"
    default_message = "The local language model returned unreadable output."


class LLMGenerationError(ExplainerError):
    """The llama.cpp subprocess itself failed (non-zero exit)."""

    status_code = 500
    code = "llm_generation_failed"
    default_message = "The local language model failed to generate text."


class EvidenceError(ExplainerError):
    """Phase 4 analysis artifacts could not be loaded for Phase 5."""

    status_code = 500
    code = "evidence_preparation_failed"
    default_message = "The analyzed evidence could not be prepared for story understanding."


class StoryError(ExplainerError):
    """Story understanding failed as a whole (structural Phase 5 stage)."""

    status_code = 500
    code = "story_understanding_failed"
    default_message = "Story understanding failed."


class ScriptGenerationError(ExplainerError):
    """Script generation failed as a whole (structural Phase 5 stage)."""

    status_code = 500
    code = "script_generation_failed"
    default_message = "Script generation failed."


class ScriptQualityError(ExplainerError):
    """Deterministic script QC rejected the output (e.g. empty script)."""

    status_code = 500
    code = "script_quality_failed"
    default_message = "The generated script failed quality control."


class ScriptNotReadyError(ExplainerError):
    """Script generation requires an ANALYZED project."""

    status_code = 409
    code = "script_not_ready"
    default_message = (
        "This project cannot generate a script yet. Run analysis first "
        "(status becomes ANALYZED)."
    )


class ScriptConflictError(ExplainerError):
    """A script-generation job is already queued or running for the project."""

    status_code = 409
    code = "script_already_running"
    default_message = "Script generation is already running for this project."


class TTSEngineUnavailableError(ExplainerError):
    """The local TTS executable is missing or not configured."""

    status_code = 503
    code = "tts_unavailable"
    default_message = (
        "Local text-to-speech is not available. Phase 6 uses Piper (the "
        "'piper' CLI) with per-language voice models. The app never "
        "downloads voices automatically - see the README 'Phase 6 - first-"
        "run TTS setup' section for the explicit one-time install, or set "
        "TTS_EXECUTABLE_PATH / TTS_VOICE_EN / TTS_VOICE_HI / TTS_VOICE_BN "
        "in your .env file."
    )


class TTSVoiceMissingError(ExplainerError):
    """No voice model is configured (or configured model file is missing)
    for the requested language."""

    status_code = 503
    code = "voice_not_available"
    default_message = (
        "No Piper voice is configured for this language. Set the matching "
        "TTS_VOICE_* path in .env to a local .onnx voice model and run the "
        "explicit setup from the README ('Phase 6 - first-run TTS setup'). "
        "No automatic download is performed."
    )


class TTSTimeoutError(ExplainerError):
    """One Piper synthesis exceeded its configured timeout."""

    status_code = 504
    code = "tts_timeout"
    default_message = "The local text-to-speech engine did not answer in time."


class TTSGenerationError(ExplainerError):
    """The Piper subprocess failed or produced unusable audio."""

    status_code = 500
    code = "tts_generation_failed"
    default_message = "Local speech synthesis failed."


class TTSAudioError(ExplainerError):
    """A synthesized WAV segment is corrupt or has the wrong format."""

    status_code = 500
    code = "tts_audio_invalid"
    default_message = "The generated narration audio is invalid."


class NarrationError(ExplainerError):
    """The composite Phase 6 narration job failed as a whole."""

    status_code = 500
    code = "narration_failed"
    default_message = "Narration generation failed."


class NarrationNotReadyError(ExplainerError):
    """Narration generation requires a SCRIPT_READY project."""

    status_code = 409
    code = "narration_not_ready"
    default_message = (
        "This project cannot generate narration yet. Generate the "
        "explanation script first (status becomes SCRIPT_READY)."
    )


class NarrationConflictError(ExplainerError):
    """A narration job is already queued or running for the project."""

    status_code = 409
    code = "narration_already_running"
    default_message = "Narration generation is already running for this project."


class RenderNotReadyError(ExplainerError):
    """Final rendering requires a NARRATION_READY project."""

    status_code = 409
    code = "render_not_ready"
    default_message = (
        "This project cannot be rendered yet. Generate the narration first "
        "(status becomes NARRATION_READY)."
    )


class RenderConflictError(ExplainerError):
    """A render job is already queued or running for the project."""

    status_code = 409
    code = "render_already_running"
    default_message = "Final rendering is already running for this project."


class RenderArtifactError(ExplainerError):
    """A required Phase 5/6 artifact is missing or unreadable."""

    status_code = 422
    code = "render_artifacts_missing"
    default_message = (
        "Some required story/narration artifacts are missing. Re-run the "
        "script or narration generation for this project."
    )


class SubtitleFontMissingError(ExplainerError):
    """Subtitle burn-in needs a Unicode font for Hindi/Bengali rendering."""

    status_code = 503
    code = "subtitle_font_missing"
    default_message = (
        "Subtitle burn-in for Hindi/Bengali needs a Unicode font that "
        "covers Devanagari/Bengali (e.g. Windows 'Nirmala UI' or a Noto "
        "Sans font). Set SUBTITLE_FONT_PATH (or SUBTITLE_FONT_NAME) in "
        ".env - the renderer refuses to burn boxes. English can render "
        "with the default sans font."
    )


class RenderError(ExplainerError):
    """FFmpeg mixing/encoding failed or produced an unusable file."""

    status_code = 500
    code = "render_failed"
    default_message = "Final video rendering failed."


class FinalQCRejectedError(ExplainerError):
    """The rendered MP4 failed the deterministic final media QC."""

    status_code = 500
    code = "final_qc_failed"
    default_message = "The rendered video failed final quality control."


class NotInPhase1Error(ExplainerError):
    """Feature is deliberately not implemented yet (scope guard).

    Pipeline services raise this instead of faking AI results. The name and
    code are kept from Phase 1 for compatibility, but the class is used for
    any stage later phases have not implemented yet.
    """

    status_code = 501
    code = "not_implemented_phase_1"
    default_message = "This capability is not implemented yet."


class PathTraversalError(ExplainerError):
    """A caller supplied a path escaping an allowed base directory."""

    status_code = 400
    code = "invalid_path"
    default_message = "Invalid path: escaping the allowed directory is not permitted."
