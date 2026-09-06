# Local AI Video Explainer — Phase 2: Upload & Validation

A **local, zero-cost AI video explainer** for Windows: drop in almost any video
(movie, TV, gameplay, tutorial, lecture, sports, screen recording, social,
nature …), pick a narration language (English / Hindi / Bengali) and duration
(2 / 3 / 4 minutes), and the app will eventually produce an original narrated
MP4 with synchronized subtitles.

> **Phase 2** delivers the **video upload & validation engine**: videos are
> streamed to disk (never loaded fully into RAM), fingerprinted with SHA-256,
> validated by FFprobe, and their metadata is stored in SQLite. A validated
> video is **READY** for the AI pipeline of later phases.
>
> **No AI processing happens yet** — no Whisper, no vision, no script, no TTS,
> no rendering — and the code never fakes results.
>
> **No paid APIs.** No Claude/OpenAI/Gemini keys. Everything runs on the
> user's PC, targeting 8 GB RAM, CPU-only, integrated graphics.

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
│   │   ├── api/              # health, system status, projects + upload
│   │   ├── services/         # ffmpeg detection, storage, uploads, cleanup
│   │   ├── ai/               # future pipeline interfaces (stubs, registry)
│   │   ├── video/            # ffprobe probing (metadata + validation)
│   │   ├── database/         # SQLite connection + schema + migration
│   │   ├── models/           # pydantic models + enums
│   │   └── utils/            # errors, structured logging, path safety
│   ├── tests/                # pytest suite (Phase 1 + Phase 2)
│   ├── requirements.txt
│   └── requirements-dev.txt
├── frontend/                 # React + Vite + TypeScript UI
│   └── src/                  # App, API client, types, styles
├── models/                   # future local model files (empty)
├── data/
│   ├── projects/             # per project: input/ temp/ output/
│   ├── uploads/  temp/  outputs/  cache/
├── logs/                     # app.log + errors.log (auto-rotated)
├── scripts/                  # Windows .bat + unix helpers
├── docs/architecture.md      # pipeline design (Phase 2 flow added)
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
**skip gracefully** when FFmpeg is missing; the rest of the Phase 2 suite runs
against scripted fake ffprobe binaries, so it works everywhere.

## API (Phase 2)

| Method | Endpoint                  | Purpose                                   |
| ------ | ------------------------- | ----------------------------------------- |
| GET    | `/api/health`             | Liveness (app + version)                  |
| GET    | `/api/system/status`      | Python, FFmpeg, SQLite, storage, DB, limits |
| GET    | `/api/projects`           | List projects (metadata included)         |
| GET    | `/api/projects/{id}`      | One project + current status/progress     |
| POST   | `/api/projects/upload`    | **Multipart video upload** (below)        |
| POST   | `/api/projects`           | Create an empty record (legacy/record-only) |
| DELETE | `/api/projects/{id}`      | Delete record **and** controlled files    |

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

| Status       | Meaning                                                        |
| ------------ | -------------------------------------------------------------- |
| `uploading`  | File is streaming to `data/projects/<id>/input/` (bytes → 0-90%) |
| `validating` | FFprobe inspecting the stored file (90%)                       |
| `ready`      | Valid video + metadata stored (100%) — ready for Phase 3+      |
| `failed`     | Upload/validation failed; `error_message` explains why          |
| `created`    | Record-only project created via the legacy endpoint             |
| `queued/processing/completed` | Reserved for the future pipeline worker            |

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

## Frontend (Phase 2)

The single-page UI now runs a real upload flow:

- drag & drop **or** file picker, with client-side format/size checks
- language (English / Hindi / **বাংলা**) and target length (2 / 3 / 4 min)
- real upload progress (bytes streamed) + stage status
- success shows **Video ready** with full metadata (duration, resolution,
  FPS + raw frame rate, audio, codecs, container, size, bitrate, SHA-256)
- duplicate / oversized / invalid uploads surface the backend error
- project history shows filename, language, length, resolution, FPS, audio,
  status and date; **View** opens details, **Delete** removes record + files

Backend validation is authoritative — the client checks are only UX.

## Logging

Structured lines: `timestamp | level | module | message | project=… | job=…`.
`logs/app.log` for everything, `logs/errors.log` for errors only, both
auto-rotating. Project ids are attached via context; secrets are never logged.

## Phase 2 limitations (honest)

- No AI processing: the Generate button is intentionally inert until the
  pipeline stages land (no fake narration, no placeholder MP4s).
- FFprobe metadata is captured; transcoding/thumbnail/preprocessing are
  Phase 3 work (the project folders already contain `temp/`/`output/`).
- Upload progress percentages are byte-based while streaming and stage-based
  during validation — precise sub-stage percentages are not invented.
- The `processing_jobs` table is still reserved for the Phase 3+ worker.

## Roadmap

1. **Phase 1 (done)** — architecture, config, SQLite, REST foundation, FFmpeg
   detection, UI scaffold, tests, docs.
2. **Phase 2 (this phase)** — upload & validation engine: streaming storage,
   SHA-256 fingerprints, FFprobe metadata, READY/FAILED lifecycle.
3. **Phase 3** — preprocessing & analysis asset generation (optimized
   copies, thumbnails, audio extraction) + local speech-to-text.
4. **Phase 4** — scene detection, OCR, vision + story understanding.
5. **Phase 5** — script generation, TTS narration, subtitle sync, FFmpeg
   mixing + render.
6. **Phase 6** — quality control, background job queue (one heavy job at a
   time), cleanup polish.

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
