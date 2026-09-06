# Local AI Video Explainer — Phase 1 Foundation

A **local, zero-cost AI video explainer** for Windows: drop in almost any video
(movie, TV, gameplay, tutorial, lecture, sports, screen recording, social,
nature …), pick a narration language (English / Hindi / Bengali) and duration
(2 / 3 / 4 minutes), and the app will eventually produce an original narrated
MP4 with synchronized subtitles.

> **Phase 1** builds the foundation only: clean architecture, config, SQLite,
> REST API, FFmpeg detection, a Phase-1 UI, and *interface stubs* for the
> future AI pipeline. **No real AI processing happens yet** — and the code
> never fakes results.
>
> **No paid APIs.** No Claude/OpenAI/Gemini keys. Everything runs on the
> user's PC, targeting 8 GB RAM, CPU-only, integrated graphics.

## Why this folder?

This repository root hosts a separate web application, so the desktop app lives
in its own self-contained folder. **Copy or download the `ai-video-explainer/`
folder anywhere on a Windows PC** — nothing machine-specific is hard-coded.
All storage paths resolve relative to this folder by default.

## Requirements (target: Windows, AMD Ryzen 3 3200G, 8 GB RAM)

| Tool      | Minimum                    | Why                                                              |
| --------- | -------------------------- | ---------------------------------------------------------------- |
| Python    | 3.10+ (3.11/3.12 preferred) | Backend (FastAPI, SQLite)                                        |
| Node.js   | 18+ (20/22 preferred)       | Frontend build (Vite)                                            |
| FFmpeg    | 6.x+ (ffmpeg **and** ffprobe) | Video processing (required from Phase 2; detected in Phase 1)  |

FFmpeg is **not** downloaded automatically. Install it (e.g. `winget install
`ffmpeg`` or the gyan.dev build) and ensure `ffmpeg`/`ffprobe` are on PATH, or
set `FFMPEG_PATH`/`FFPROBE_PATH` in `.env`. The UI reports clearly when FFmpeg
is missing.

## Project structure

```
ai-video-explainer/
├── backend/                  # Python FastAPI backend
│   ├── app/
│   │   ├── main.py           # app factory + entrypoint (uvicorn app.main:app)
│   │   ├── config.py         # central settings (.env supported)
│   │   ├── api/              # REST endpoints: health, system status, projects
│   │   ├── services/         # ffmpeg detection, storage, cleanup
│   │   ├── ai/               # future pipeline interfaces (stubs, registry)
│   │   ├── video/            # ffprobe probing + render stub
│   │   ├── database/         # SQLite connection + schema + repositories
│   │   ├── models/           # pydantic models + enums
│   │   └── utils/            # errors, structured logging, path safety
│   ├── tests/                # pytest suite
│   ├── requirements.txt
│   └── requirements-dev.txt
├── frontend/                 # React + Vite + TypeScript UI
│   └── src/                  # App, API client, styles
├── models/                   # future local model files (empty in Phase 1)
├── data/
│   ├── uploads/              # Phase 2: uploaded videos
│   ├── projects/             # Phase 2: per-project working files
│   ├── temp/                 # streaming intermediates (disk, not RAM)
│   ├── outputs/              # final rendered MP4s
│   └── cache/                # model/thumbnail caches
├── logs/                     # app.log + errors.log (auto-rotated)
├── scripts/                  # Windows .bat + unix helpers
├── docs/architecture.md      # future pipeline design
├── env.example               # copy to .env (no real secrets exist)
└── README.md
```

> **Improvement over the spec tree:** this project is a fully self-contained
> folder instead of being spread across this repository's root, because the
> repo root already hosts another application. That makes the desktop app
> portable (copy folder → run) and prevents accidental collisions. Structure
> inside the folder follows the required layout.

## Configuration

Copy `env.example` to `.env` (same folder) and adjust if needed. Everything
has sensible defaults relative to the project folder — no absolute paths are
required. Notable knobs: `BACKEND_PORT`, `FRONTEND_URL`, `CORS_ORIGINS`,
`MAX_UPLOAD_SIZE_MB` (default 2048), `PROCESSING_CONCURRENCY` (default **1**
heavy job at a time), `FFMPEG_PATH`/`FFPROBE_PATH`, log rotation settings.

## Windows setup

```bat
:: one time
python -m venv .venv
.venv\Scripts\pip install -r backend\requirements-dev.txt
cd frontend && npm install && cd ..
copy env.example .env
```

…or run the bundled helper: `scripts\setup_windows.bat`.

### Start the backend

```bat
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```
(cd into `backend/` first, or use `scripts\run_backend.bat`)

Interactive API docs: http://127.0.0.1:8000/docs

### Start the frontend

```bat
cd frontend
npm run dev
```
http://127.0.0.1:5173 — Vite proxies `/api` to the backend, so no CORS setup.

### Run the tests

```bat
.venv\Scripts\python -m pytest backend\tests -q
```

## API (Phase 1)

| Method | Endpoint                  | Purpose                                   |
| ------ | ------------------------- | ----------------------------------------- |
| GET    | `/api/health`             | Liveness (app + version)                  |
| GET    | `/api/system/status`      | Python, FFmpeg, SQLite, storage, DB, limits |
| GET    | `/api/projects`           | List project records                      |
| GET    | `/api/projects/{id}`      | One project (404 with clean JSON)         |
| POST   | `/api/projects`           | Create a project record (returns id)      |
| DELETE | `/api/projects/{id}`      | Delete a project                          |

`POST /api/projects` accepts `{original_filename?, language: "en"|"hi"|"bn",
target_duration_minutes: 2|3|4}`. Durations are stored in seconds.

## Database

SQLite file at `data/explainer.db` (auto-created, WAL mode, foreign keys on).

- `projects` — id, filenames, probe metadata columns (duration/width/height/fps
  filled from Phase 2), language, target duration, status, progress, errors,
  timestamps.
- `processing_jobs` — id, project_id (cascade delete), stage, status, progress,
  timestamps, errors. Reserved for the future worker; API does not create jobs
  yet.

Indexes on status/created_at (projects) and project_id/status (jobs).

## Logging

Structured lines: `timestamp | level | module | message | project=… | job=…`.
`logs/app.log` for everything, `logs/errors.log` for errors only, both
auto-rotating. Project/job ids are attached via context and default to `-`.
Secrets are never logged.

## Phase 1 limitations (honest)

- The Generate button registers a project record only; upload, analysis,
  narration, subtitles and rendering arrive in later phases.
- FFmpeg is detected and reported, but never invoked for processing.
- AI pipeline classes (`SpeechToTextService`, `OCRService`,
  `VisionAnalysisService`, `StoryUnderstandingService`,
  `ScriptGenerationService`, `TextToSpeechService`, `SubtitleService`,
  `VideoRenderService`) exist and raise a clear
  "not implemented in Phase 1" error — no placeholder AI output.
- SQLite is embedded and local; nothing is stored in the cloud.

## Roadmap

1. **Phase 1 (done)** — architecture, config, SQLite, REST foundation, FFmpeg
   detection, Phase-1 UI, tests, docs.
2. **Phase 2** — video upload & validation engine (streaming to disk, size/type
   checks, FFprobe metadata capture, project status transitions).
3. **Phase 3** — speech-to-text (local Whisper, CPU, small model) + first
   narration & subtitle pass.
4. **Phase 4** — scene detection, OCR, vision + story understanding.
5. **Phase 5** — script generation, TTS narration, subtitle sync, FFmpeg
   mixing + render.
6. **Phase 6** — quality control, background job queue (one heavy job at a
   time), cleanup polish.

See `docs/architecture.md` for the detailed future pipeline.

## Security foundations

- User filenames are sanitized; path traversal is rejected by `utils/paths.py`.
- FFmpeg/ffprobe are only executed with internal argument lists (`shell=False`).
- No secrets in source, no API keys, `.env` is gitignored.
- Errors returned to the frontend are clean JSON with stable codes; internals
  go to `logs/errors.log` only.
