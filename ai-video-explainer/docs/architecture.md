# Architecture — Local AI Video Explainer

## 1. Goal & constraints

Generate an original, narrated explanation MP4 for almost any input video,
fully on the user's PC:

- **Hardware target:** AMD Ryzen 3 3200G, 8 GB RAM, integrated graphics,
  Windows.
- **Budget:** zero runtime cost, CPU-only inference.
- **Rule:** no paid cloud AI APIs, ever. Local models only.

The design therefore optimizes for *memory* first: no model is loaded while a
video is being read, no full video is ever held in RAM, heavy work is
file/stream based, and only **one heavy processing job runs at a time**
(`PROCESSING_CONCURRENCY=1`, enforced by design + future worker).

## 2. Current Phase 5 architecture

```
frontend (React/Vite/TS)                  backend (FastAPI, Python 3)
  drag&drop upload UI,              ─────▶ app/api        REST endpoints
  real progress %, metadata card,           ├─ health         /api/health
  history + details, roadmap                 ├─ projects      /api/projects…
  preprocess button + polling                ├─ preprocess     POST /api/projects/{id}/preprocess
  analyze button + stage progress            ├─ analyze        POST /api/projects/{id}/analyze
  generate panel (lang/duration,            ├─ scripts        POST /api/projects/{id}/generate-script
    LLM readiness), live stages,            │                 GET  .../story-status | story |
  script preview + QC chips,                │                 selected-scenes | duration-plan |
  selected/skipped scene marks              │                 script | script-quality
       │                                     ├─ analysis       GET  /api/projects/{id}/analysis
       ▼                                     ├─ timeline       GET  /api/projects/{id}/timeline
  Vite dev proxy /api ──▶ :8000              ├─ frames         GET  /api/projects/{id}/analysis/frames/{n}
                                             ├─ jobs           GET  /api/projects/{id}/jobs
                                             └─ thumbnail      GET  /api/projects/{id}/thumbnail
                                       app/services    uploads (streaming, sha256, dedupe),
                                                       preprocess (analysis copy /
                                                       thumbnail / 16 kHz WAV via FFmpeg),
                                                       ANALYSIS (composite orchestrator),
                                                       STORY (Phase 5 composite orchestrator:
                                                       evidence → story → importance →
                                                       duration/script plan → script → QC),
                                                       EVIDENCE / SCENE_IMPORTANCE /
                                                       DURATION_PLANNING / SCRIPT_QUALITY,
                                                       TIMELINE (alignment + density + QC),
                                                       worker (single-threaded job queue),
                                                       ffmpeg detection, storage
                                       app/ai          llm.py  (LocalLLMProvider protocol +
                                       │                 LlamaCppProvider: subprocess per
                                       │                 generation, timeout, no shell,
                                       │                 no silent downloads, JSON extractor)
                                       │               story.py (batched story understanding,
                                       │                 content-type classification,
                                       │                 evidence-grounded, anti-hallucination)
                                       │               script.py (language-aware narration,
                                       │                 style rules per content type)
                                       │               stt.py  (faster-whisper, lazy,
                                       │                 int8 CPU, model-gated)
                                       │               ocr.py  (Tesseract, graceful absence)
                                       │               vision.py (deterministic PIL +
                                       │                 optional LocalVisionProvider)
                                       app/video       probe.py (FFprobe metadata
                                       │                  + validation rules)
                                       │               scenes.py (FFmpeg scene filter +
                                       │                  representative-frame extraction)
                                       ▼
                                       app/database    SQLite (WAL, FK on,
                                       │                  additive migration P1→P2→P3→P4→P5)
                                       │              data/projects/<id>/
                                       │                input|temp|output
                                       │                analysis|thumbnails|audio   (P3)
                                       │                analysis/{metadata,frames}  (P4)
                                       │                analysis/story             (P5)
                                       app/models      pydantic schemas
                                       app/utils       errors/logging/paths/fingerprints
```

Runtime state: `projects` rows carry all validated media metadata **plus**
Phase 3 analysis-asset references (relative paths, dimensions, `prepared_at`);
`processing_jobs` rows carry the worker's stage lifecycle (`preprocess` /
`analysis` / `script_generation`); `analysis_results` rows carry one summary
per analysis run; `script_runs` rows carry one summary per Phase 5 run
(language, target duration, content type, selected scenes, word count,
quality score, generation fingerprint). Large data always lives on disk
under `data/projects/<id>/` (never RAM). The worker thread is started with
the app and runs **one job at a time** (`PROCESSING_CONCURRENCY=1`); AI
models are lazy-loaded when a stage starts and released before the next one
(the LLM is a short-lived llama.cpp subprocess per generation).

### 2b. Phase 3 preprocessing & analysis-asset flow

```
User clicks "Prepare for analysis" (project is READY)
      │
      ▼
POST /api/projects/{id}/preprocess
      │   guard: active job? -> 409 job_conflict · status READY? -> 409 project_not_ready
      │   job row persisted (stage=preprocess, queued, 0%)
      │   project -> preprocessing (0%)
      ▼
Worker queue (FIFO, single thread, concurrency=1)
      │   job -> running
      ▼
FFmpeg step 1 — analysis copy     analysis/analysis.mp4
      │     scale≤640, fps=5, libx264 veryfast CRF30, -an
      │     progress: -progress pipe:1 -> elapsed/duration → 0-60%
      ▼
FFmpeg step 2 — poster thumbnail  thumbnails/poster.jpg  (60-75%)
      ▼
FFmpeg step 3 — audio extraction  audio/audio.wav        (75-95%)
      │     16 kHz mono PCM — skipped when has_audio=false
      ▼
Project -> prepared (100%), job -> completed
   analysis_path/width/height/fps, thumbnail_path, audio_path, prepared_at

Failure anywhere: partial assets removed, job -> failed (error_message),
project -> ready (retryable).
```

Progress is honest: each FFmpeg step reports media-elapsed-time/duration
mapped into a fixed weight window; no invented percentages. The poster is
served to the UI via `GET /api/projects/{id}/thumbnail` (path-safe, relative
paths only).

### 2c. Phase 4 local analysis flow

```
User clicks "Analyze video" (project is PREPARED)
      │
      ▼
POST /api/projects/{id}/analyze
      │   guards: ANALYZING -> 409 · READY -> 409 · unknown -> 404
      │   ANALYZED + unchanged fingerprints -> 200 idempotent reuse (nothing re-run)
      │   stale ANALYZING rows (crash) -> auto-recover to PREPARED
      │   run row (analysis_results) + job row persisted  · project -> analyzing
      ▼
Worker queue (same single thread, concurrency=1)
      │   stage windows: 5% preparing · 25% scenes · 50% STT · 70% OCR
      │                  82% visual · 92% timeline · 97% QC · 100% finalizing
      ▼
SCENE DETECTION (structural)           analysis/metadata/scenes.json
      │   FFmpeg select='gt(scene,T)' on analysis/analysis.mp4 (no extra
      │   package, flat RAM); min-duration merge + max-scenes cap
      ▼
REPRESENTATIVE FRAMES                  analysis/frames/scene_%03d.jpg
      │   one decode pass, exact indices (analysis copy has const fps)
      ▼
SPEECH-TO-TEXT (graceful)              analysis/metadata/transcript.json
      │   faster-whisper on audio/audio.wav · lazy load, released after
      │   preferred language hint + auto-detect · missing model → warning
      ▼
OCR (graceful)                         analysis/metadata/ocr.json
      │   Tesseract on scene frames (capped) · absent binary → warning
      ▼
VISUAL ANALYSIS (graceful)             analysis/metadata/visual.json
      │   deterministic PIL: brightness, blur proxy, complexity, size
      │   (LocalVisionProvider interface for future small VLMs)
      ▼
TIMELINE + CONTEXT AGGREGATION         analysis/metadata/timeline.json
      │   scene × (overlapping transcript, in-range OCR, representative
      │   frame, visual metadata, information-density 0-100)
      ▼
QUALITY CHECK + MANIFEST               analysis/metadata/analysis_manifest.json
      │   timestamp validation · warnings · fingerprints · relative paths
      ▼
Project -> analyzed (100%), run -> completed, job -> completed
      │   UI shows summary + per-scene timeline + frames

Failure (structural): run+job failed, Phase 4 artifacts removed, project
-> prepared with error_message (Phase 3 assets preserved) → Analyze retry.
Graceful stage failures (no Whisper model / no Tesseract / corrupt audio)
become warnings; the pipeline still finishes.
```
```
User selects video
      │
      ▼
Frontend upload (multipart, XHR progress %)
      │
      ▼
Backend streaming upload  (1 MiB chunks, never full file in RAM)
      │        cumulative size check  → 413 upload_too_large
      │        streaming SHA-256 computed while writing
      ▼
Security validation  (extension allowlist, sanitized internal filename,
      │   traversal-proof path under data/projects/<id>/input/)
      ▼
File saved to project storage   (projects/<id>/input/<id-prefix>.<ext>)
      │
      ▼
Duplicate check  (same SHA-256 exists?)  → 409 duplicate_video
      │
      ▼
FFprobe metadata extraction  (duration, width/height, fps + raw rational,
      │   video/audio codecs, container, bitrate, file size, has_audio)
      ▼
SQLite project update   (status READY, progress 100, metadata stored)
      │
      ▼
Project status = READY
      │
      ▼
Frontend displays metadata  (duration, resolution, FPS, audio, codec, …)
```

Failure at any point marks the project **FAILED** with a readable
`error_message`, removes the partial file/folder and returns a clean error
code. Videos are only **READY** after FFprobe confirms a real video stream,
a valid duration and usable dimensions (audio is optional).

Statuses: `uploading` (byte-based 0-90%) → `validating` (90%) → `ready` (100%)
or `failed`. Progress is honest: sub-stage percentages are never invented.

## 3. Future end-to-end pipeline

```
Video Upload ──▶ Preprocessing ──▶ Scene Detection ──▶ Speech-to-Text
      │                │                 │                   │
      ▼                ▼                 ▼                   ▼
 OCR ◀── keyframes    frames        scene list          transcript
      │                                                     (+timings)
      ▼                        ┌──────────────────────────────┘
 Vision Understanding         │
      │                        ▼
      ▼              Story Understanding (context auto-detection:
 Duration Selection      movie/TV/gameplay/edu/news/sports/tutorial/
      │                 screenrec/lecture/social/nature/general)
      ▼
 Script Generation (language: en/hi/bn · length: 2/3/4 min)
      │
      ├───────────────► TTS (narration audio)
      │                        │
      │                        ▼
      ├───────────────► Subtitle Generation (synchronized SRT)
      │                        │
      │                        ▼
      └───────────────► Audio Mixing (narration + optional original audio)
                                    │
                                    ▼
                        FFmpeg Rendering (subtitles burned in → final MP4)
                                    │
                                    ▼
                        Quality Control (duration, audio level, subtitle sync)
```

### Stage ownership & service interfaces (Phase 5: through script real, TTS/render stubbed)

| # | Stage                  | Service class (module)          | Planned | Local/zero-cost approach |
|---|------------------------|----------------------------------|---------|-------------------------------------------------------|
| 1 | Upload                 | `services/uploads.py`            | P2 ✅   | Streaming multipart → `projects/<id>/input/`, SHA-256, FFprobe validation |
| 2 | Preprocessing          | `services/preprocess.py`         | P3 ✅   | Worker + FFmpeg: analysis copy (≤640px @ 5fps), poster, 16 kHz WAV |
| 3 | Scene detection        | `video/scenes.py`                | P4 ✅   | FFmpeg `select='gt(scene,T)'` + min-duration/max-count assembly |
| 4 | Speech-to-text         | `ai/stt.py`                      | P4 ✅   | faster-whisper tiny/base, int8, CPU; reads audio/audio.wav; model-gated |
| 5 | OCR                    | `ai/ocr.py`                      | P4 ✅   | Tesseract on scene frames (capped); graceful absence |
| 6 | Visual understanding   | `ai/vision.py`                   | P4 ✅   | Deterministic PIL metadata (brightness/blur/complexity); optional `LocalVisionProvider` |
| 7 | Timeline alignment     | `services/timeline.py`           | P4 ✅   | Scene × transcript/OCR/visual binding + information density + QC |
| 8 | Evidence preparation   | `services/evidence.py`           | P5 ✅   | Compact per-scene evidence (bounded excerpts), evidence manifest |
| 9 | Story understanding    | `ai/story.py`                    | P5 ✅   | llama.cpp CLI (Q4 GGUF): batch summaries → story model with scene-id evidence; content-type classification; anti-hallucination |
| 10 | Scene importance      | `services/scene_importance.py`   | P5 ✅   | Weighted evidence + story scores; coverage-first selection; redundancy control |
| 11 | Duration selection    | `services/duration_planning.py`  | P5 ✅   | Word budgets (250-300/375-450/500-600) allocated before writing; script plan (hook/sections/ending) |
| 12 | Script generation     | `ai/script.py`                   | P5 ✅   | Same local LLM, prompted per section + language + content-type style |
| 13 | Script quality        | `services/script_quality.py`     | P5 ✅   | Deterministic checks + documented 0-100 score |
| 14 | TTS                    | `ai/tts.py`                      | P6      | Local neural TTS with en/hi/bn voices |
| 15 | Subtitle generation    | `ai/subtitles.py`                | P6      | Sentence→timestamp mapping from TTS audio → SRT |
| 16 | Audio mixing           | (added P6)                       | P6      | FFmpeg amix/volume ducking, streamed |
| 17 | FFmpeg rendering       | `video/renderer.py`              | P6      | FFmpeg filter graph, memory-bounded encode |
| 18 | Quality control        | (added P6)                       | P6      | Re-probe duration/audio/ocr subtitle spot check |
| 11 | TTS                    | `ai/tts.py`                      | P5      | Local neural TTS with en/hi/bn voices |
| 12 | Subtitle generation    | `ai/subtitles.py`                | P5      | Sentence→timestamp mapping from TTS audio → SRT |
| 13 | Audio mixing           | (added P6)                       | P6      | FFmpeg amix/volume ducking, streamed |
| 14 | FFmpeg rendering       | `video/renderer.py`              | P6      | FFmpeg filter graph, memory-bounded encode |
| 15 | Quality control        | (added P6)                       | P6      | Re-probe duration/audio/ocr subtitle spot check |

AI stage classes extend `PipelineService` (`ai/base.py`) and are registered
in `ai/registry.py`. The remaining stubbed stages **raise a controlled
not-implemented error** — the orchestrator runs them uniformly later; the
API never pretends work happened. Stages 3-7 are *real* since Phase 4 and
degrade gracefully per-stage (missing models/binary → warning, not failure).

### Orchestration (Phase 5: live for preprocessing + analysis + script)

- `services/worker.py` is a **single daemon thread** with a FIFO queue — no
  multiprocessing on the 8 GB target. Jobs are persisted in SQLite *before*
  submission; the worker only transitions persisted states.
- Per stage: `job: queued → running (progress%) → completed|failed`;
  `projects.status/progress` mirrors the aggregate; `error_message` captures
  failures; `analysis_results` / `script_runs` mirror per-run stage +
  summary. The worker never dies: unexpected exceptions are logged and the
  job is failed cleanly (project/job rows deleted mid-run are dropped
  silently).
- `PipelineStage` values (`preprocess`, `analysis`, `script_generation`)
  dispatch into the same queue, one stage per job — later phases plug more
  values unchanged. FFmpeg is only required for the video stages; the LLM
  stage runs on JSON + frames alone.
- Phase 4 idempotency: config + preprocessing fingerprints per run;
  unchanged → reuse, changed → invalidate and re-run. Phase 5 adds a
  **generation fingerprint** (analysis identity + language + target
  duration + model identity + prompt/planner versions + narration
  settings); matching completed run + valid artifacts → idempotent reuse.
  Stale `analyzing`/`scripting` projects (crash) auto-recover to
  `prepared`/`analyzed` on the next call.
- Failure policy: preprocess → READY (assets gone); analysis → PREPARED
  (Phase 4 artifacts cleared, Phase 3 kept); script → ANALYZED (Phase 5
  artifacts cleared, Phase 3/4 kept).
- Cleanup utilities (`services/cleanup.py`) sweep stale `temp/` files after
  crashes so disk never fills; failed jobs remove partial artifacts.

## 4. Resource-safety rules (8 GB RAM)

1. Never load more than one model at a time; unload between stages.
2. Prefer smallest quantized model that still works (int8/Q4).
3. Stream video through FFmpeg; write frames/audio to disk, never to RAM.
4. One heavy job at a time (`PROCESSING_CONCURRENCY`, default 1).
5. SQLite WAL + short connections: idle RAM near zero.
6. Cap upload size (`MAX_UPLOAD_SIZE_MB`) and free-disk checks before render.

## 5. API surface (stable for later phases)

| Method | Endpoint                  | Behavior (Phase 4)                                  |
| ------ | ------------------------- | --------------------------------------------------- |
| GET    | `/api/health`             | liveness                                            |
| GET    | `/api/system/status`      | python/ffmpeg/sqlite/storage/db + limits + worker + **analysis deps** + **LLM report** (provider, available, model_name basename, threads, ctx), `phase: "5"` |
| GET    | `/api/projects`           | list (metadata + asset refs included)               |
| GET    | `/api/projects/{id}`      | one project + status/progress (404 on unknown id)   |
| POST   | `/api/projects/upload`    | **multipart upload** → streams, validates, 201 READY |
| POST   | `/api/projects/{id}/preprocess` | **queue analysis-asset job** → 201 JobOut (409 guards) |
| POST   | `/api/projects/{id}/analyze` | **queue Phase 4 analysis** (idempotent reuse; 409 conflicts) |
| GET    | `/api/projects/{id}/analysis` | latest run summary (404 until analyzed once)    |
| GET    | `/api/projects/{id}/timeline` | aligned per-scene evidence JSON (404 until analyzed) |
| GET    | `/api/projects/{id}/analysis/frames/{n}` | scene JPEG (int-validated, 404 if missing) |
| POST   | `/api/projects/{id}/generate-script` | **queue Phase 5 story+script** (JSON `language`, `target_duration_seconds`; idempotent reuse; 503 when LLM unavailable) |
| GET    | `/api/projects/{id}/story-status` | latest script-run summary + live stage (404 until started) |
| GET    | `/api/projects/{id}/story` | story model JSON (content type, premise, events)    |
| GET    | `/api/projects/{id}/selected-scenes` | important scenes with reasons (404 until generated) |
| GET    | `/api/projects/{id}/duration-plan` | per-scene word budgets + targets (404 until generated) |
| GET    | `/api/projects/{id}/script` | narration script JSON (404 until generated)         |
| GET    | `/api/projects/{id}/script-quality` | deterministic QC report 0-100 (404 until generated) |
| GET    | `/api/projects/{id}/jobs` | job history (stage/status/progress/error)           |
| GET    | `/api/projects/{id}/thumbnail` | poster JPEG (404 until PREPARED)               |
| POST   | `/api/projects`           | create empty record (legacy)                        |
| DELETE | `/api/projects/{id}`      | 204; deletes record **and** `projects/<id>/` files   |

`POST /api/projects/upload` fields: `file`, `language` (`en|hi|bn`),
`target_duration` (`120|180|240` seconds). Public responses never include
internal filesystem paths (asset paths are relative only; the LLM report
only exposes the model file's *basename*).

Phase 5 statuses: `analyzed → scripting → script_ready` (failure returns to
`analyzed`). The UI polls `GET /api/projects/{id}` + `/story-status` for
reactive progress with honest stage labels.

## 6. Configuration & security model

- Central `Settings` (pydantic-settings). Relative paths resolve against the
  project folder; overridable per env var. `.env` ignored by git.
- Path traversal rejected (`utils/paths.py`); filenames sanitized.
- Subprocesses (ffmpeg/ffprobe) always `shell=False`, never user-controlled.
- Central error hierarchy (`utils/errors.py`) → HTTP mapping; internal details
  only in `logs/errors.log`. No secrets in code or logs.

## 7. Phase roadmap

- **Phase 1 ✅** architecture, config, SQLite, REST foundation, FFmpeg
  detection, UI, stubs, tests, docs.
- **Phase 2 ✅** upload & validation engine (streaming storage, SHA-256
  fingerprint + duplicate detection, FFprobe metadata, status/progress).
- **Phase 3 ✅** preprocessing & analysis assets (analysis copy, poster,
  16 kHz WAV) + single-job background worker (`services/worker.py`).
- **Phase 4 ✅** on-device analysis: scene detection + frames,
  faster-whisper STT, Tesseract OCR, deterministic visual metadata, timeline
  alignment + context aggregation → ANALYZED (graceful per-stage absence).
- **Phase 5 ✅ (this phase)** story understanding (small local LLM, llama.cpp
  + Q4 GGUF, hierarchical batches), important-scene selection, duration-aware
  script generation (en/hi/bn, 2/3/4 min) + deterministic QC → SCRIPT_READY.
- **Phase 6** TTS narration, subtitle sync, audio mixing, FFmpeg rendering,
  quality control, queue hardening, cleanup + UX polish.
