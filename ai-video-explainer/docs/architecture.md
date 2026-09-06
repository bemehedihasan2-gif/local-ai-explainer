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

## 2. Current Phase 2 architecture

```
frontend (React/Vite/TS)                  backend (FastAPI, Python 3)
  drag&drop upload UI,              ─────▶ app/api        REST endpoints
  real progress %, metadata card,           ├─ health         /api/health
  history + details, roadmap                 ├─ projects      /api/projects…
       │                                     └─ upload        POST /api/projects/upload
       ▼                               app/services    uploads (streaming,
  Vite dev proxy /api ──▶ :8000                       sha256, dedupe, statuses),
                                                       ffmpeg detection, storage
                                       app/video       probe.py (FFprobe metadata
                                       │                  + validation rules)
                                       ▼
                                       app/database    SQLite (WAL, FK on,
                                       │                  additive migration)
                                       │              data/projects/<id>/input|temp|output
                                       app/models      pydantic schemas
                                       app/utils       errors/logging/paths
                                       app/ai          pipeline stubs (unchanged)
```

Runtime state: `projects` rows carry all validated media metadata; large data
always lives on disk under `data/projects/<id>/` (never RAM).

### 2a. Phase 2 upload & validation flow

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

### Stage ownership & service interfaces (Phase 2: upload real, AI stages stubbed)

| # | Stage                  | Service class (module)          | Planned | Local/zero-cost approach |
|---|------------------------|----------------------------------|---------|-------------------------------------------------------|
| 1 | Upload                 | `services/uploads.py` **done P2** | P2 ✅   | Streaming multipart → `projects/<id>/input/`, SHA-256, FFprobe validation |
| 2 | Preprocessing          | (added P3)                       | P3      | FFmpeg stream copy / normalization to scratch files |
| 3 | Speech-to-text         | `ai/stt.py`                      | P3      | faster-whisper small, int8, CPU, en/hi/bn |
| 4 | Scene detection        | (added P4)                       | P4      | FFmpeg scene filter + frame sampling, on-disk frames |
| 5 | OCR                    | `ai/ocr.py`                      | P4      | Lightweight ONNX OCR (RapidOCR class), sampled frames |
| 6 | Vision understanding   | `ai/vision.py`                   | P4      | Small open VLM (Q4), sampled keyframes only |
| 7 | Story understanding    | `ai/story.py`                    | P4      | Small CPU LLM (llama.cpp Q4) over compact stage text |
| 8 | Duration selection     | Orchestrator logic               | P5      | Script length from target minutes (words/min pacing) |
| 9 | Script generation      | `ai/script.py`                   | P5      | Same local LLM, prompted per context + language |
| 10 | TTS                    | `ai/tts.py`                      | P5      | Local neural TTS with en/hi/bn voices |
| 11 | Subtitle generation    | `ai/subtitles.py`                | P5      | Sentence→timestamp mapping from TTS audio → SRT |
| 12 | Audio mixing           | (added P6)                       | P6      | FFmpeg amix/volume ducking, streamed |
| 13 | FFmpeg rendering       | `video/renderer.py`              | P5      | FFmpeg filter graph, memory-bounded encode |
| 14 | Quality control        | (added P6)                       | P6      | Re-probe duration/audio/ocr subtitle spot check |

All AI stage classes extend `PipelineService` (`ai/base.py`), are registered
in `ai/registry.py`, and **raise a controlled not-implemented error** — an
orchestrator can later run them uniformly; the API never pretends work
happened. Upload (`services/uploads.py`) is the first *real* stage.

### Orchestration plan (from Phase 3)

- A worker (asyncio task or single background thread — **no multiprocessing**
  on the 8 GB target) pops the next `processing_jobs` row in `queued` state.
- Per stage: `job.status=running, progress=x` → stage service executes
  (file-based I/O) → result dict persisted → next stage enqueued.
- `projects.status/progress` mirrors the aggregate; `error_message` captures
  failures. A global gate enforces `PROCESSING_CONCURRENCY=1`.
- Cleanup utilities (`services/cleanup.py`) sweep stale `temp/` files after
  crashes so disk never fills.

## 4. Resource-safety rules (8 GB RAM)

1. Never load more than one model at a time; unload between stages.
2. Prefer smallest quantized model that still works (int8/Q4).
3. Stream video through FFmpeg; write frames/audio to disk, never to RAM.
4. One heavy job at a time (`PROCESSING_CONCURRENCY`, default 1).
5. SQLite WAL + short connections: idle RAM near zero.
6. Cap upload size (`MAX_UPLOAD_SIZE_MB`) and free-disk checks before render.

## 5. API surface (stable for later phases)

| Method | Endpoint                | Behavior (Phase 2)                                  |
| ------ | ----------------------- | --------------------------------------------------- |
| GET    | `/api/health`           | liveness                                            |
| GET    | `/api/system/status`    | python/ffmpeg/sqlite/storage/db + limits, `phase: "2"` |
| GET    | `/api/projects`         | list (metadata included)                            |
| GET    | `/api/projects/{id}`    | one project + status/progress (404 on unknown id)   |
| POST   | `/api/projects/upload`  | **multipart upload** → streams, validates, 201 READY |
| POST   | `/api/projects`         | create empty record (legacy)                        |
| DELETE | `/api/projects/{id}`    | 204; deletes record **and** `projects/<id>/` files   |

`POST /api/projects/upload` fields: `file`, `language` (`en|hi|bn`),
`target_duration` (`120|180|240` seconds). Public responses never include
internal filesystem paths. `POST /api/projects/{id}/generate` arrives in a
later phase and the UI will poll `GET /api/projects/{id}` for reactive
progress.

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
- **Phase 3** preprocessing & analysis asset generation, worker loop,
  speech-to-text + first end-to-end narration path.
- **Phase 4** scene detection, OCR, vision, story understanding.
- **Phase 5** script generation, TTS, subtitle sync, render/mix.
- **Phase 6** quality control, queue hardening, cleanup + UX polish.
