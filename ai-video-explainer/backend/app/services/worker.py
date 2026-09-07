"""Background processing worker (Phase 3).

A single daemon thread drains a FIFO queue of ``processing_jobs`` rows, so
at most **one** heavy FFmpeg preprocessing job runs at any time on the
8 GB target machine (``PROCESSING_CONCURRENCY=1``). Jobs are persisted in
SQLite *before* they are submitted; the worker only transitions persisted
states, so a crash never invents work.

Lifecycle per job::

    queued -> running -> completed   (project: READY -> PREPROCESSING -> PREPARED)
                       -> failed     (project: back to READY + error_message)

Threading is deliberately kept to this single worker thread: no process
pool, no multiprocessing. SQLite is accessed per-operation with short
connections (WAL allows the worker writer and API readers to coexist).
"""

from __future__ import annotations

import queue
import threading
from typing import Any

from app.database.connection import Database
from app.database.repositories.jobs import JobRepository
from app.database.repositories.projects import ProjectRepository
from app.models.enums import JobStatus, ProjectStatus
from app.services.ffmpeg import FfmpegService
from app.services.preprocess import PreprocessService
from app.services.storage import StorageService
from app.utils.errors import ExplainerError, ProjectNotFoundError
from app.utils.logging import get_logger, log_context

logger = get_logger("app.services.worker")

_SENTINEL = None  # queue item that stops the worker thread


class ProcessingWorker:
    """Runs queued processing jobs one at a time on a background thread."""

    def __init__(
        self,
        settings: Any,
        db: Database,
        ffmpeg: FfmpegService,
        storage: StorageService,
    ) -> None:
        self._settings = settings
        self._db = db
        self._ffmpeg = ffmpeg
        self._storage = storage
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._active_job: str | None = None
        self._lock = threading.Lock()

    # -- lifecycle ------------------------------------------------------
    def start(self) -> None:
        """Spawn the single worker thread (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop, name="explainer-worker", daemon=True
        )
        self._thread.start()
        logger.info("Processing worker started (concurrency=1).")

    def stop(self, timeout: float = 3.0) -> None:
        """Signal shutdown and wait for the active job to be interrupted."""
        if self._thread is None or not self._thread.is_alive():
            return
        self._queue.put(_SENTINEL)
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():  # pragma: no cover - hard-stop fallback
            logger.warning("Worker thread did not stop in time; continuing.")
        logger.info("Processing worker stopped.")

    # -- submission ------------------------------------------------------
    def submit(self, job_id: str) -> None:
        """Enqueue a persisted job row for processing."""
        self._queue.put(job_id)
        logger.info("Job %s submitted to the worker queue.", job_id, extra={"job_id": job_id})

    # -- introspection ---------------------------------------------------
    def status(self) -> dict[str, object]:
        with self._lock:
            return {
                "running": self._thread is not None and self._thread.is_alive(),
                "queue_size": self._queue.qsize(),
                "active_job": self._active_job,
            }

    # -- internals -------------------------------------------------------
    def _loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is _SENTINEL:
                self._queue.task_done()
                break
            with self._lock:
                self._active_job = item
            try:
                self._process_job(item)
            except Exception:  # noqa: BLE001 - the worker thread must never die
                logger.exception("Worker crashed while processing job %s.", item)
            finally:
                self._queue.task_done()
                with self._lock:
                    self._active_job = None

    def _process_job(self, job_id: str) -> None:
        jobs = JobRepository(self._db)
        projects = ProjectRepository(self._db)

        try:
            job = jobs.get(job_id)
        except ProjectNotFoundError:
            # Job row deleted while queued (project cascade-deleted).
            return
        project_id = job["project_id"]

        with log_context(project_id=project_id, job_id=job_id):
            if job["status"] != JobStatus.QUEUED.value:
                logger.warning("Job %s is not queued (%s); skipping.", job_id, job["status"])
                return

            try:
                project = projects.get(project_id)
            except ProjectNotFoundError:
                logger.warning("Project %s vanished; dropping job %s.", project_id, job_id)
                return

            try:
                jobs.update_status(job_id, status=JobStatus.RUNNING, progress=0.0)
            except ProjectNotFoundError:
                # Row cascade-deleted between our reads and this write.
                logger.info("Job %s vanished before it could start.", job_id)
                return
            logger.info("Started processing job %s (stage=%s).", job_id, job["stage"])

            try:
                # Fail fast with a clean, recorded error when FFmpeg is gone.
                ffmpeg_status = self._ffmpeg.require()
                service = PreprocessService(self._settings, self._storage)

                def tick(progress: float) -> None:
                    jobs.update_progress(job_id, progress)
                    projects.update(project_id, progress=progress)

                assets = service.run(
                    project,
                    str(ffmpeg_status.ffmpeg_path),
                    progress_callback=tick,
                )
                try:
                    projects.update(
                        project_id,
                        status=ProjectStatus.PREPARED,
                        progress=100.0,
                        error_message=None,
                        **assets,
                    )
                    jobs.update_status(
                        job_id, status=JobStatus.COMPLETED, progress=100.0
                    )
                except ProjectNotFoundError:
                    # Project (and its cascade-deleted job row) vanished
                    # while we were processing; nothing to persist.
                    logger.info(
                        "Project %s was deleted during job %s; dropping result.",
                        project_id, job_id,
                    )
                    return
                logger.info("Job %s completed; project %s PREPARED.", job_id, project_id)
            except ExplainerError as exc:
                self._fail(
                    projects, jobs, project_id, job_id,
                    message=exc.message,
                )
            except Exception:  # noqa: BLE001 - worker must never die
                logger.exception(
                    "Unhandled failure in job %s (project %s).",
                    job_id, project_id,
                )
                self._fail(
                    projects, jobs, project_id, job_id,
                    message="Preprocessing failed unexpectedly; check backend/logs/errors.log.",
                )

    @staticmethod
    def _fail(
        projects: ProjectRepository,
        jobs: JobRepository,
        project_id: str,
        job_id: str,
        *,
        message: str,
    ) -> None:
        """Record a job failure and return the project to READY for retry."""
        try:
            try:
                projects.update(
                    project_id,
                    status=ProjectStatus.READY,
                    progress=100.0,
                    error_message=message,
                )
            except ProjectNotFoundError:
                logger.warning(
                    "Project %s disappeared during job failure handling.",
                    project_id,
                )
            jobs.update_status(
                job_id, status=JobStatus.FAILED, error_message=message
            )
        except ProjectNotFoundError:  # pragma: no cover - both rows vanished
            logger.warning(
                "Job %s disappeared while recording its failure.", job_id
            )
        logger.error("Job %s FAILED: %s", job_id, message)


__all__ = ["ProcessingWorker"]