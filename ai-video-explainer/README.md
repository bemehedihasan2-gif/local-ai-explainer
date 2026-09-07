# Local AI Video Explainer — Phase 3: Preprocessing & Analysis Assets

A **local, zero-cost AI video explainer** for Windows: drop in almost any video
(movie, TV, gameplay, tutorial, lecture, sports, screen recording, social,
nature …), pick a narration language (English / Hindi / Bengali) and duration
(2 / 3 / 4 minutes), and the app will eventually produce an original narrated
MP4 with synchronized subtitles.

> **Phase 2** delivered the **upload & validation engine**: videos are
> streamed to disk (never loaded fully into RAM), fingerprinted with SHA-256,
> validated by FFprobe, and their metadata is stored in SQLite.
>
> **Phase 3** adds **preprocessing & analysis-asset generation**: a single
> background worker turns a validated (**READY**) video into a **PREPARED**
> project with three FFmpeg-built assets — a low-resolution analysis copy
> (≤ 640 px @ 5 fps H.264, what later vision/OCR stages will read), a poster
> JPEG thumbnail, and a 16 kHz mono WAV audio track for speech-to-text.
>
> **No AI inference happens yet** — no Whisper, no vision, no script, no TTS,
> no rendering — and the code never fakes results.
>
> **No paid APIs.** No Claude/OpenAI/Gemini keys. Everything runs on the
> user's PC, targeting 8 GB RAM, CPU-only, integrated graphics. Only **one
> heavy job runs at a time** (worker, `PROCESSING_CONCURRENCY=1`).

## Why this folder?

This repository root hosts a separate web application, so the desktop app lives
in its own self-contained folder. **Copy or download the `ai-video-explainer/`
folder anywhere on a Windows PC** — nothing machine-specific is hard-coded.
All storage paths resolve relative to this folder by default.

## Requirements (target: Windows, AMD Ryzen 3 3200G, 8 GB RAM)

| Tool      | Minimum                    | Why                                                                 |
| --------- | -------------------------- | ------------------------------------------------------------------- |
| Python    | 3.10+ (3.11/3.12 preferred) | Backend (FastAPI, SQLite)                                          |
| Node.js   | 18+ (20/22 preferred)       | Frontend build (Vite)                                              |
| FFmpeg    | 6.x+ (ffmpeg **and** ffprobe) | **Required from Phase 2** — FFprobe validates every upload        |

FFmpeg is **not** downloaded automatically. Install it (e.g. `winget install
ffmpeg` or the gyan.dev build) and ensure `ffmpeg`/`ffprobe` are on PATH, or
set `FFMPEG_PATH`/`FFPROBE_PATH` in `.env`. When FFmpeg is missing the UI shows
a setup hint and uploads are rejected with a clear `ffmpeg_unavailable` error.

## Supported video formats

*(unchanged from Phase 2)*

`.mp4` `.mkv` `.avi` `.mov` `.webm` `.m4v` `.mpeg` `.mpg` `.ts`

Validation never trusts the extension alone: after storage, **FFprobe** must
confirm a real video stream with a usable duration and dimensions. Rejected:
empty files, corrupt files, non-video files, audio-only files, unsupported
containers, and files over the size limit. **Audio is optional** — a silent
video is accepted (`has_audio: false`) because later phases analyze visuals.

**Maximum upload size:** `MAX_UPLOAD_SIZE_MB`, default **4096 MB** (4 GB).
Exceeding it aborts mid-stream with `413 upload_too_large`; nothing is
truncated and no partial file is kept.

## Project structure

```
ai-video-explainer/
├── backend/                  # Python FastAPI backend
│   ├── app/
│   │   ├── main.py           # app factory + entrypoint (uvicorn app.main:app)
│   │   ├── config.py         # central settings (.env supported)
│   │   ├── api/              # health, system status, projects, jobs, thumbnail
│   │   ├── services/         # ffmpeg detection, storage, uploads, PREPROCESS,
│   │   │                     #   worker (single-job queue), cleanup
│   │   ├── ai/               # future pipeline interfaces (stubs, registry)
│   │   ├── video/            # ffprobe probing (metadata + validation)
│   │   ├── database/         # SQLite connection + schema + migration
│   │   ├── models/           # pydantic models + enums
│   │   └── utils/            # errors, structured logging, path safety
│   ├── tests/                # pytest suite (Phase 1 + 2 + 3)
│   ├── requirements.txt
│   └── requirements-dev.txt
├── frontend/                 # React + Vite + TypeScript UI
│   └── src/                  # App, API client, types, styles
├── models/                   # future local model files (empty)
├── data/
│   ├── projects/             # per project: input/ temp/ output/
│   │                         #   analysis/ thumbnails/ audio/  (Phase 3)
│   ├── uploads/  temp/  outputs/  cache/
├── logs/                     # app.log + errors.log (auto-rotated)
├── scripts/                  # Windows .bat + unix helpers
├── docs/architecture.md      # pipeline design (Phase 2 + 3 flows)
├── env.example               # copy to .env (no real secrets exist)
└── README.md
```

## Configuration

Copy `env.example` to `.env` (same folder) and adjust if needed. Everything
has defaults relative to the project folder — no absolute paths are required.
Notable knobs:

| Variable                    | Default                         | Meaning                                  |
| --------------------------- | ------------------------------- | ---------------------------------------- |
| `MAX_UPLOAD_SIZE_MB`        | `4096`                          | Largest accepted upload (MB)             |
| `UPLOAD_CHUNK_SIZE`         | `1048576` (1 MiB)               | Bytes read per streaming chunk           |
| `ALLOWED_VIDEO_EXTENSIONS`  | the 9 formats above             | Extension allowlist (JSON list)          |
| `FFPROBE_TIMEOUT_SECONDS`   | `60`                            | Timeout per FFprobe run                  |
| `PROCESSING_CONCURRENCY`    | `1`                             | One heavy job at a time (8 GB RAM)       |
| `ANALYSIS_WIDTH`            | `640`                           | Analysis copy width cap (height follows) |
| `ANALYSIS_FPS`              | `5`                             | Analysis copy constant frame rate        |
| `ANALYSIS_ENCODER_PRESET` / `ANALYSIS_CRF` | `veryfast` / `30` | x264 settings for the analysis copy      |
| `THUMBNAIL_WIDTH`           | `320`                           | Poster JPEG width cap                    |
| `AUDIO_SAMPLE_RATE` / `AUDIO_CHANNELS` | `16000` / `1`       | WAV for speech-to-text (16 kHz mono)     |
| `PREPROCESS_TIMEOUT_SECONDS` | `600`                          | Timeout per FFmpeg preprocessing step    |
| `FFMPEG_PATH` / `FFPROBE_PATH` | (auto-discover on PATH)     | Absolute binary paths if not on PATH     |

## Windows setup

```bat
:: one time
python -m venv .venv
.venv\Scripts\pip install -r backend\requirements-dev.txt
cd frontend && npm install && cd ..
copy env.example .env
```

…or run the bundled helper: `scripts\setup_windows.bat` (and
`scripts\setup_unix.sh` on Linux/macOS). Verify FFmpeg once:
`ffmpeg -version`.

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

Tests that need a real encoder generate tiny synthetic videos with FFmpeg and
**skip gracefully** when FFmpeg is missing; the rest of the suite (Phase 1 + 2
+ 3) runs against scripted fake ffmpeg/ffprobe binaries, so it works
everywhere — including the full preprocess job lifecycle, worker
serialization and failure paths.

## API (Phase 3)

| Method | Endpoint                        | Purpose                                   |
| ------ | ------------------------------- | ----------------------------------------- |
| GET    | `/api/health`                   | Liveness (app + version)                  |
| GET    | `/api/system/status`            | Python, FFmpeg, SQLite, storage, DB, limits, **worker state** |
| GET    | `/api/projects`                 | List projects (metadata included)         |
| GET    | `/api/projects/{id}`            | One project + current status/progress     |
| POST   | `/api/projects/upload`          | **Multipart video upload** (below)        |
| POST   | `/api/projects`                 | Create an empty record (legacy/record-only) |
| POST   | `/api/projects/{id}/preprocess` | **Queue Phase 3 preprocessing** (below)   |
| GET    | `/api/projects/{id}/jobs`       | Job history (stage/status/progress/error) |
| GET    | `/api/projects/{id}/thumbnail`  | Poster JPEG (once PREPARED)               |
| DELETE | `/api/projects/{id}`            | Delete record **and** controlled files    |

### `POST /api/projects/upload`

`multipart/form-data` with fields:

- `file` — the video (streamed; extension checked first, FFprobe is authority)
- `language` — `en` | `hi` | `bn`
- `target_duration` — `120` | `180` | `240` (seconds)

Success (`201`) returns the READY project, e.g.:

```json
{
  "id": "…",
  "original_filename": "clip.mp4",
  "status": "ready",
  "progress": 100,
  "duration": 123.45,
  "width": 1920,
  "height": 1080,
  "fps": 29.97,
  "raw_fps": "30000/1001",
  "video_codec": "h264",
  "audio_codec": "aac",
  "container_format": "mov",
  "bitrate": 1500000,
  "file_size": 123456789,
  "sha256": "…",
  "has_video": true,
  "has_audio": true,
  "language": "en",
  "target_duration_seconds": 180,
  "error_message": null,
  "created_at": "…",
  "updated_at": "…"
}
```

Internal filesystem paths are **never** returned. Errors are clean JSON with a
stable `error` code: `missing_file`, `unsupported_file_type`,
`upload_too_large`, `invalid_video`, `duplicate_video`,
`invalid_parameter`, `ffmpeg_unavailable`, `project_not_found`, …
Stack traces go to `logs/errors.log` only.

### Project statuses

| Status         | Meaning                                                        |
| -------------- | -------------------------------------------------------------- |
| `uploading`    | File is streaming to `data/projects/<id>/input/` (bytes → 0-90%) |
| `validating`   | FFprobe inspecting the stored file (90%)                       |
| `ready`        | Valid video + metadata stored (100%) — awaiting preprocessing  |
| `preprocessing`| Phase 3 worker is building the analysis assets (0 → 100%)      |
| `prepared`     | Analysis copy + poster + 16 kHz WAV ready — input for Phase 4+ |
| `failed`       | Upload/validation/preprocessing failed; `error_message` explains |
| `created`      | Record-only project created via the legacy endpoint             |
| `queued/processing/completed` | Reserved for the future pipeline worker            |

On any **upload** failure the partial file is removed, the project folder
cleaned up, and the record stays `failed` with the error message. On a
**preprocessing** failure the job is recorded as `failed` with the reason and
the project **returns to `ready`** (the input video is fine) so you can retry;
partial assets are removed. Deleting a project removes its database record
**and** its controlled folder under `data/projects/` (including assets).

On any failure the partial file is removed, the project folder cleaned up, and
the record stays as `failed` with the error message. Deleting a project removes
its database record **and** its controlled folder under `data/projects/`.

### Duplicate detection

While streaming, the backend computes a **SHA-256 fingerprint**. If the exact
same content was uploaded before (any non-failed project), the upload is
rejected with `409 duplicate_video` and the frontend tells you which earlier
project matches — nothing is auto-deleted; the existing project is untouched.

## Database

SQLite at `data/explainer.db` (auto-created, WAL mode, foreign keys on). A
**safe, additive migration** (`database/schema.py::migrate_schema`) adds the
Phase 2 columns to databases created in Phase 1 — existing rows are never
touched. `projects` now stores: `original_filename`, `stored_filename`,
`input_path` (internal), `file_size`, `sha256`, `duration`, `width`, `height`,
`fps`, `raw_fps`, `video_codec`, `audio_codec`, `container_format`, `bitrate`,
`has_video`, `has_audio`, `language`, `target_duration_seconds`, `status`,
`progress`, `error_message`, timestamps (indexed by status, created_at,
sha256). FPS keeps both a normalized number (`29.97`) and the raw FFprobe
value (`30000/1001`); duration stays numeric seconds.

Phase 3 extends the same migration pattern with asset columns:
`analysis_path`/`analysis_width`/`analysis_height`/`analysis_fps`,
`thumbnail_path`, `audio_path` (relative paths inside the project folder,
never absolute) and `prepared_at`. `processing_jobs` now carries the worker's
stages (`preprocess`), lifecycle and progress.

## Phase 3 preprocessing (how it works)

`POST /api/projects/{id}/preprocess` on a **READY** project:

1. A `processing_jobs` row is persisted (`stage=preprocess`, `queued`, 0%) and
   the project becomes **preprocessing**.
2. The single background worker (one thread, started with the app — no
   multiprocessing) picks it up and runs FFmpeg three times, all **streamed
   on disk**, with honest duration-weighted progress via `-progress pipe:1`:
   - `analysis/analysis.mp4` — `scale≤ANALYSIS_WIDTH, fps=ANALYSIS_FPS`,
     libx264 `veryfast`/CRF 30, no audio, yuv420p
   - `thumbnails/poster.jpg` — one frame near 10% in (poster)
   - `audio/audio.wav` — first audio stream → 16 kHz mono PCM
     (skipped when the video has no audio)
3. On success the project becomes **prepared** (100%) with asset metadata
   (analysis dimensions/fps, relative asset paths, `prepared_at`) and the job
   `completed`. On failure the job is `failed`, partial assets are removed,
   and the project returns to **ready** with the error in `error_message`.

Each FFmpeg step has `PREPROCESS_TIMEOUT_SECONDS`; progress is real (media
elapsed time vs duration), never invented. The worker runs **one job at a
time**; a second `preprocess` call for the same project while a job is active
returns `409 job_conflict`. Missing FFmpeg fails the job cleanly
(`ffmpeg_unavailable` message, project back to READY).

`GET /api/projects/{id}/jobs` returns the job history; the poster is served
from `GET /api/projects/{id}/thumbnail` (relative path only, path-safe).

## Frontend (Phase 3)

The single-page UI runs the full upload → preprocess flow:

- drag & drop **or** file picker, with client-side format/size checks
- language (English / Hindi / **বাংলা**) and target length (2 / 3 / 4 min)
- real upload progress (bytes streamed) + stage status
- success shows the validated metadata card (duration, resolution, FPS +
  raw frame rate, audio, codecs, container, size, bitrate, SHA-256)
- **Prepare for analysis** queues preprocessing; the card live-updates a
  progress bar (1 s polling) while the worker runs
- when **prepared**: poster thumbnail, analysis-copy specs, audio-track
  status, and an enabled **Generate explanation** button (pipeline work of
  later phases — it does not fake output)
- project history shows status tags incl. `preprocessing`/`prepared`;
  **View** opens details, **Delete** removes record + files

Backend validation is authoritative — the client checks are only UX.

## Logging

Structured lines: `timestamp | level | module | message | project=… | job=…`.
`logs/app.log` for everything, `logs/errors.log` for errors only, both
auto-rotating. Project ids are attached via context; secrets are never logged.

## Phase 3 limitations (honest)

- No AI processing: the Generate button is intentionally inert until the
  pipeline stages land (no fake narration, no placeholder MP4s).
- Preprocessing covers **analysis assets only**: no scene detection,
  no speech-to-text, no OCR/vision yet (Phase 4 work).
- The worker queue is in-process: jobs do not survive a backend restart
  (a queued row would remain `queued`; re-run preprocessing to retry).
- Upload/preprocess progress is honest but stage-weighted: byte-based for
  the transfer, duration-weighted for each FFmpeg step.

## Roadmap

1. **Phase 1 (done)** — architecture, config, SQLite, REST foundation, FFmpeg
   detection, UI scaffold, tests, docs.
2. **Phase 2 (done)** — upload & validation engine: streaming storage,
   SHA-256 fingerprints, FFprobe metadata, READY/FAILED lifecycle.
3. **Phase 3 (this phase)** — preprocessing & analysis assets (optimized
   copy, thumbnail, 16 kHz WAV) + single-job background worker.
4. **Phase 4** — scene detection, speech-to-text, OCR, vision + story
   understanding (local models).
5. **Phase 5** — script generation, TTS narration, subtitle sync, FFmpeg
   mixing + render.
6. **Phase 6** — quality control, queue hardening, cleanup polish.

See `docs/architecture.md` for the full pipeline design.

## Troubleshooting

| Symptom                                        | Fix                                                                 |
| ---------------------------------------------- | ------------------------------------------------------------------- |
| “FFmpeg not detected” / `ffmpeg_unavailable`   | Install FFmpeg (winget/gyan.dev), restart the backend, reload the UI |
| `413 upload_too_large`                         | Raise `MAX_UPLOAD_SIZE_MB` in `.env` or use a smaller video          |
| `unsupported_file_type`                        | Container not in the allowlist (list is shown in the error)          |
| `invalid_video` after a full upload            | The file is not decodable as video (corrupt/audio-only/empty)        |
| `duplicate_video`                              | The identical file was already uploaded — delete the earlier project |
| Upload stalls / interrupted                    | No partial file is kept; simply retry (a FAILED row shows the error) |
| Preprocessing fails (`FFmpeg failed…`)         | Check `logs/errors.log`; the project returns to READY — retry, or lower `ANALYSIS_WIDTH`/`ANALYSIS_FPS` for very large videos |
| Preprocessing times out                        | Raise `PREPROCESS_TIMEOUT_SECONDS` (per-step cap)                    |
| `409 job_conflict`                             | A preprocess job is already running for that project — wait          |
| Project stuck at `preprocessing` after restart | In-process queue lost the job; start preprocessing again             |
| Port 8000 busy                                 | Change `BACKEND_PORT` in `.env`                                      |

## Security foundations

- Uploads stream in bounded chunks; files are stored under generated internal
  names in `data/projects/<id>/` — user filenames are metadata only.
- Path traversal (`../`, drive letters, UNC) is blocked by `utils/paths.py`;
  deletion never touches anything outside the managed projects root.
- FFprobe runs with fixed argument arrays (`shell=False`) and a timeout;
  user input never reaches a shell.
- No secrets in source, no API keys, `.env` is gitignored.
- Errors returned to the frontend are clean JSON with stable codes; internal
  details (paths, stack traces) stay in `logs/errors.log`.
