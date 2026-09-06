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

## 2. Current Phase 1 architecture

```
frontend (React/Vite/TS)                  backend (FastAPI, Python 3)
  UI: upload area (Phase 2),       ─────▶ app/api        REST endpoints
  language/duration, Generate,              ├─ health         /api/health
  system status, pipeline                    ├─ projects      /api/projects…
  roadmap, history table                     └─ (jobs reserved for later)
       │
       ▼                               app/services    ffmpeg detection,
  Vite dev proxy /api ──▶ :8000                       storage, cleanup
                                       app/database    SQLite (WAL, FK on)
                                       app/models      pydantic schemas
                                       app/utils       errors/logging/paths
                                       app/ai + app/video  pipeline stubs
```

Runtime state is stored in two SQLite tables (`projects`,
`processing_jobs`); large data always lives on disk under `data/`.

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

### Stage ownership & service interfaces (Phase 1 ships stubs)

| # | Stage                  | Service class (module)          | Planned | Local/zero-cost approach (candidate, evaluated later) |
|---|------------------------|----------------------------------|---------|-------------------------------------------------------|
| 1 | Upload                 | API layer (`api/`)               | P2      | Streaming multipart → `data/uploads/`, size/type checks |
| 2 | Preprocessing          | (added P2)                       | P2      | FFmpeg stream copy / normalization to scratch files |
| 3 | Scene detection        | (added P4)                       | P4      | FFmpeg scene filter + frame sampling, on-disk frames |
| 4 | Speech-to-text         | `ai/stt.py`                      | P3      | faster-whisper small, int8, CPU, en/hi/bn |
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

All stage classes extend `PipelineService` (`ai/base.py`), are registered in
`ai/registry.py`, and **raise `NotInPhase1Error`** — an orchestrator can later
run them uniformly; the API does not pretend work happened.

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

| Method | Endpoint             | Phase 1 behavior                                  |
| ------ | -------------------- | ------------------------------------------------- |
| GET    | `/api/health`        | liveness                                          |
| GET    | `/api/system/status` | python/ffmpeg/sqlite/storage/db + limits, `phase: "1"` |
| GET    | `/api/projects`      | list                                              |
| GET    | `/api/projects/{id}` | one project (404 clean JSON on unknown id)        |
| POST   | `/api/projects`      | create record → 201 + project (files from P2)     |
| DELETE | `/api/projects/{id}` | 204, cascade removes jobs                         |

Phase 2 additions: `POST /api/projects/{id}/upload` (streaming), then
`POST /api/projects/{id}/generate` which enqueues the first real pipeline job
and the UI polls `GET /api/projects/{id}` (reactive progress).

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
- **Phase 2** upload & validation engine (+ FFprobe metadata, status
  transitions, progress wiring).
- **Phase 3** speech-to-text + worker loop + first end-to-end narration path.
- **Phase 4** scene detection, OCR, vision, story understanding.
- **Phase 5** script generation, TTS, subtitle sync, render/mix.
- **Phase 6** quality control, queue hardening, cleanup + UX polish.
