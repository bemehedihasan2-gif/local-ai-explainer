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
from datetime import datetime, timezone
from typing import Any

from app.database.connection import Database
from app.database.repositories.analysis import AnalysisRepository
from app.database.repositories.jobs import JobRepository
from app.database.repositories.projects import ProjectRepository
from app.database.repositories.render_runs import RenderRepository
from app.database.repositories.scripts import ScriptRepository
from app.database.repositories.tts_runs import TtsRepository
from app.models.enums import JobStatus, PipelineStage, ProjectStatus
from app.services.analysis import AnalysisService, _STAGE_LABELS
from app.services.ffmpeg import FfmpegService
from app.services.narration import NarrationService, _STAGE_LABELS as _TTS_STAGE_LABELS
from app.services.performance import write_performance_report
from app.services.preprocess import PreprocessService
from app.services.render import RenderService, _STAGE_LABELS as _RENDER_STAGE_LABELS
from app.services.storage import StorageService
from app.services.story import StoryService, _STAGE_LABELS as _SCRIPT_STAGE_LABELS
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
                if job["stage"] == PipelineStage.PREPROCESS.value:
                    # Fail fast with a clean, recorded error when FFmpeg is gone.
                    ffmpeg_status = self._ffmpeg.require()
                    self._run_preprocess(
                        projects, jobs, project_id, job_id, project,
                        str(ffmpeg_status.ffmpeg_path),
                    )
                elif job["stage"] == PipelineStage.ANALYSIS.value:
                    ffmpeg_status = self._ffmpeg.require()
                    self._run_analysis(
                        projects, jobs, project_id, job_id, project,
                        str(ffmpeg_status.ffmpeg_path),
                    )
                elif job["stage"] == PipelineStage.SCRIPT_GENERATION.value:
                    # No FFmpeg needed: the LLM pipeline works on JSON + frames.
                    self._run_script(projects, jobs, project_id, job_id, project)
                elif job["stage"] == PipelineStage.TEXT_TO_SPEECH.value:
                    # No FFmpeg needed: narration is synthesized per segment.
                    self._run_narration(projects, jobs, project_id, job_id, project)
                elif job["stage"] == PipelineStage.FINAL_RENDER.value:
                    ffmpeg_status = self._ffmpeg.require()
                    self._run_render(
                        projects, jobs, project_id, job_id, project,
                        str(ffmpeg_status.ffmpeg_path),
                        str(ffmpeg_status.ffprobe_path),
                    )
                else:  # pragma: no cover - future stages not wired yet
                    raise ExplainerError(
                        f"Pipeline stage '{job['stage']}' is not implemented yet."
                    )
            except ExplainerError as exc:
                self._fail(
                    projects, jobs, project_id, job_id, job["stage"],
                    message=exc.message,
                )
            except Exception:  # noqa: BLE001 - worker must never die
                logger.exception(
                    "Unhandled failure in job %s (project %s).",
                    job_id, project_id,
                )
                self._fail(
                    projects, jobs, project_id, job_id, job["stage"],
                    message=(
                        "Processing failed unexpectedly; "
                        "check backend/logs/errors.log."
                    ),
                )

    # ------------------------------------------------------------------
    # Stage runners
    # ------------------------------------------------------------------
    def _run_preprocess(
        self, projects, jobs, project_id, job_id, project, ffmpeg_path,
    ) -> None:
        service = PreprocessService(self._settings, self._storage)

        def tick(progress: float) -> None:
            jobs.update_progress(job_id, progress)
            projects.update(project_id, progress=progress)

        assets = service.run(project, ffmpeg_path, progress_callback=tick)
        try:
            projects.update(
                project_id,
                status=ProjectStatus.PREPARED,
                progress=100.0,
                error_message=None,
                **assets,
            )
            jobs.update_status(job_id, status=JobStatus.COMPLETED, progress=100.0)
        except ProjectNotFoundError:
            # Project (and its cascade-deleted job row) vanished mid-run.
            logger.info(
                "Project %s was deleted during job %s; dropping result.",
                project_id, job_id,
            )
            return
        logger.info("Job %s completed; project %s PREPARED.", job_id, project_id)

    def _run_analysis(
        self, projects, jobs, project_id, job_id, project, ffmpeg_path,
    ) -> None:
        analysis_repo = AnalysisRepository(self._db)
        run = analysis_repo.latest_for_project(project_id)
        run_id = run["id"] if run and run["status"] in ("queued", "running") else None
        if run_id:
            analysis_repo.update(
                run_id,
                status="running",
                started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
        analysis = AnalysisService(self._settings, self._storage)

        def tick(progress: float, stage: str | None = None) -> None:
            jobs.update_progress(job_id, progress)
            projects.update(project_id, progress=progress)
            if run_id and stage:
                analysis_repo.update(
                    run_id,
                    current_stage=_STAGE_LABELS.get(stage, stage),
                )

        summary = analysis.run(project, ffmpeg_path, progress_callback=tick)
        try:
            projects.update(
                project_id,
                status=ProjectStatus.ANALYZED,
                progress=100.0,
                error_message=None,
            )
            if run_id:
                analysis_repo.update(
                    run_id,
                    status="completed",
                    current_stage=None,
                    completed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    detected_language=summary.get("detected_language"),
                    language_probability=summary.get("language_probability"),
                    scene_count=summary.get("scene_count"),
                    transcript_available=summary.get("transcript_available"),
                    ocr_available=summary.get("ocr_available"),
                    visual_provider=summary.get("visual_provider"),
                    processing_seconds=summary.get("processing_seconds"),
                    warnings=summary.get("warnings", []),
                )
            jobs.update_status(job_id, status=JobStatus.COMPLETED, progress=100.0)
        except ProjectNotFoundError:
            logger.info(
                "Project %s was deleted during job %s; dropping result.",
                project_id, job_id,
            )
            return
        logger.info("Job %s completed; project %s ANALYZED.", job_id, project_id)

    def _run_script(
        self, projects, jobs, project_id, job_id, project,
    ) -> None:
        script_repo = ScriptRepository(self._db)
        run = script_repo.latest_for_project(project_id)
        run_id = run["id"] if run and run["status"] in ("queued", "running") else None
        if run_id:
            script_repo.update(
                run_id,
                status="running",
                started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
        service = StoryService(self._settings, self._storage)

        def tick(progress: float, stage: str | None = None) -> None:
            jobs.update_progress(job_id, progress)
            projects.update(project_id, progress=progress)
            if run_id and stage:
                script_repo.update(
                    run_id,
                    current_stage=_SCRIPT_STAGE_LABELS.get(stage, stage),
                )

        summary = service.run(
            project,
            language=run["language"] if run else project.get("language", "en"),
            target_duration_seconds=(
                run["target_duration_seconds"] if run
                else int(project.get("target_duration_seconds") or 180)
            ),
            progress_callback=tick,
        )
        try:
            projects.update(
                project_id,
                status=ProjectStatus.SCRIPT_READY,
                progress=100.0,
                error_message=None,
            )
            if run_id:
                script_repo.update(
                    run_id,
                    status="completed",
                    current_stage=None,
                    completed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    content_type=summary.get("content_type"),
                    content_type_confidence=summary.get("content_type_confidence"),
                    selected_scene_count=summary.get("selected_scene_count"),
                    word_count=summary.get("word_count"),
                    quality_score=summary.get("quality_score"),
                    estimated_duration_seconds=summary.get("estimated_duration_seconds"),
                    warnings=summary.get("warnings", []),
                )
            jobs.update_status(job_id, status=JobStatus.COMPLETED, progress=100.0)
        except ProjectNotFoundError:
            logger.info(
                "Project %s was deleted during job %s; dropping result.",
                project_id, job_id,
            )
            return
        logger.info("Job %s completed; project %s SCRIPT_READY.", job_id, project_id)

    def _run_narration(
        self, projects, jobs, project_id, job_id, project,
    ) -> None:
        tts_repo = TtsRepository(self._db)
        run = tts_repo.latest_for_project(project_id)
        run_id = run["id"] if run and run["status"] in ("queued", "running") else None
        if run_id:
            tts_repo.update(
                run_id,
                status="running",
                started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
        service = NarrationService(self._settings, self._storage)

        def tick(progress: float, stage: str | None = None) -> None:
            jobs.update_progress(job_id, progress)
            projects.update(project_id, progress=progress)
            if run_id and stage:
                tts_repo.update(
                    run_id,
                    current_stage=_TTS_STAGE_LABELS.get(stage, stage),
                )

        summary = service.run(
            project,
            language=run["language"] if run else project.get("language", "en"),
            voice_id=run["voice_id"] if run else None,
            script_fingerprint=(
                run["script_fingerprint"] if run else None
            ),
            generation_fingerprint=(
                run["generation_fingerprint"] if run else None
            ),
            progress_callback=tick,
        )
        try:
            projects.update(
                project_id,
                status=ProjectStatus.NARRATION_READY,
                progress=100.0,
                error_message=None,
            )
            if run_id:
                tts_repo.update(
                    run_id,
                    status="completed",
                    current_stage=None,
                    completed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    audio_path=summary.get("audio_path"),
                    subtitle_path=summary.get("subtitle_path"),
                    duration_ms=summary.get("duration_ms"),
                    segment_count=summary.get("segment_count"),
                    quality_score=summary.get("quality_score"),
                    warnings=summary.get("warnings", []),
                )
            jobs.update_status(job_id, status=JobStatus.COMPLETED, progress=100.0)
        except ProjectNotFoundError:
            logger.info(
                "Project %s was deleted during job %s; dropping result.",
                project_id, job_id,
            )
            return
        logger.info(
            "Job %s completed; project %s NARRATION_READY "
            "(%d ms, QC=%s).",
            job_id, project_id, summary.get("duration_ms"),
            summary.get("quality_score"),
        )

    def _run_render(
        self, projects, jobs, project_id, job_id, project, ffmpeg_path, ffprobe_path,
    ) -> None:
        render_repo = RenderRepository(self._db)
        run = render_repo.latest_for_project(project_id)
        run_id = run["id"] if run and run["status"] in ("queued", "running") else None
        if run_id:
            render_repo.update(
                run_id,
                status="running",
                started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
        service = RenderService(self._settings, self._storage)

        def tick(progress: float, stage: str | None = None) -> None:
            jobs.update_progress(job_id, progress)
            projects.update(project_id, progress=progress)
            if run_id and stage:
                render_repo.update(
                    run_id,
                    current_stage=_RENDER_STAGE_LABELS.get(stage, stage),
                )

        summary = service.run(
            project,
            language=run["language"] if run else project.get("language", "en"),
            narration_fingerprint=(
                run["narration_fingerprint"] if run else None
            ),
            render_fingerprint=(
                run["render_fingerprint"] if run else None
            ),
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
            progress_callback=tick,
        )
        try:
            projects.update(
                project_id,
                status=ProjectStatus.COMPLETED,
                progress=100.0,
                error_message=None,
            )
            if run_id:
                render_repo.update(
                    run_id,
                    status="completed",
                    current_stage=None,
                    completed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    output_path=summary.get("output_path"),
                    output_duration_ms=summary.get("output_duration_ms"),
                    output_width=summary.get("output_width"),
                    output_height=summary.get("output_height"),
                    output_fps=summary.get("output_fps"),
                    output_size_bytes=summary.get("output_size_bytes"),
                    qc_score=summary.get("qc_score"),
                    subtitle_status=summary.get("subtitle_status"),
                    warnings=summary.get("warnings", []),
                )
            jobs.update_status(job_id, status=JobStatus.COMPLETED, progress=100.0)
        except ProjectNotFoundError:
            logger.info(
                "Project %s was deleted during job %s; dropping result.",
                project_id, job_id,
            )
            return
        # Phase 8: aggregate the per-stage timings into a disk artifact.
        # Best-effort - a report failure must never fail a successful render.
        try:
            write_performance_report(self._db, self._storage, project_id)
        except Exception:  # noqa: BLE001 - report is best-effort
            logger.warning(
                "Could not write the performance report for project %s.",
                project_id, exc_info=True,
            )
        logger.info(
            "Job %s completed; project %s COMPLETED (QC=%s).",
            job_id, project_id, summary.get("qc_score"),
        )

    def _fail(
        self,
        projects: ProjectRepository,
        jobs: JobRepository,
        project_id: str,
        job_id: str,
        stage: str,
        *,
        message: str,
    ) -> None:
        """Record a job failure; return the project to its retry state.

        Preprocess failure -> READY (assets gone). Analysis failure ->
        PREPARED (Phase 3 assets preserved; Phase 4 artifacts cleared).
        Script failure -> ANALYZED (Phase 4 results preserved; Phase 5
        artifacts cleared). Narration failure -> SCRIPT_READY (Phase 5
        results preserved; Phase 6 audio/subtitle artifacts cleared) so
        narration can be retried.
        """
        return_status = (
            ProjectStatus.READY if stage == PipelineStage.PREPROCESS.value
            else ProjectStatus.PREPARED if stage == PipelineStage.ANALYSIS.value
            else ProjectStatus.RENDER_FAILED
            if stage == PipelineStage.FINAL_RENDER.value
            else ProjectStatus.SCRIPT_READY
            if stage == PipelineStage.TEXT_TO_SPEECH.value
            else ProjectStatus.ANALYZED
        )
        try:
            try:
                projects.update(
                    project_id,
                    status=return_status,
                    progress=100.0,
                    error_message=message,
                )
            except ProjectNotFoundError:
                logger.warning(
                    "Project %s disappeared during job failure handling.",
                    project_id,
                )
            if stage == PipelineStage.ANALYSIS.value:
                AnalysisService(self._settings, self._storage).cleanup_artifacts(project_id)
                run = AnalysisRepository(self._db).latest_for_project(project_id)
                if run and run["status"] in ("queued", "running"):
                    AnalysisRepository(self._db).update(
                        run["id"],
                        status="failed",
                        current_stage=None,
                        completed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        error_message=message,
                    )
            if stage == PipelineStage.SCRIPT_GENERATION.value:
                StoryService(self._settings, self._storage).cleanup_artifacts(project_id)
                run = ScriptRepository(self._db).latest_for_project(project_id)
                if run and run["status"] in ("queued", "running"):
                    ScriptRepository(self._db).update(
                        run["id"],
                        status="failed",
                        current_stage=None,
                        completed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        error_message=message,
                    )
            if stage == PipelineStage.TEXT_TO_SPEECH.value:
                NarrationService(self._settings, self._storage).cleanup_artifacts(project_id)
                run = TtsRepository(self._db).latest_for_project(project_id)
                if run and run["status"] in ("queued", "running"):
                    TtsRepository(self._db).update(
                        run["id"],
                        status="failed",
                        current_stage=None,
                        completed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        error_message=message,
                    )
            if stage == PipelineStage.FINAL_RENDER.value:
                RenderService(self._settings, self._storage).cleanup_artifacts(project_id)
                run = RenderRepository(self._db).latest_for_project(project_id)
                if run and run["status"] in ("queued", "running"):
                    RenderRepository(self._db).update(
                        run["id"],
                        status="failed",
                        current_stage=None,
                        completed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        error_message=message,
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