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
