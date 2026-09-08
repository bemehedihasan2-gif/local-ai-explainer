# Local AI Video Explainer — Phase 7: Final MP4 Render on Your PC

A **local, zero-cost AI video explainer** for Windows: drop in almost any video
(movie, TV, gameplay, tutorial, lecture, sports, screen recording, social,
nature …), pick a narration language (English / Hindi / Bengali) and duration
(2 / 3 / 4 minutes), and the app produces an **original narrated MP4** — the
important scenes, local narration mixed with the original audio, and
synchronized burned-in subtitles — entirely on your PC.

> **Phase 2** delivered the **upload & validation engine**: videos are
> streamed to disk (never loaded fully into RAM), fingerprinted with SHA-256,
> validated by FFprobe, and their metadata is stored in SQLite.
>
> **Phase 3** added **preprocessing & analysis-asset generation**: a single
> background worker turns a validated (**READY**) video into a **PREPARED**
> project with three FFmpeg-built assets — a low-resolution analysis copy
> (≤ 640 px @ 5 fps H.264, what vision/OCR stages read), a poster JPEG
> thumbnail, and a 16 kHz mono WAV audio track for speech-to-text.
>
> **Phase 4 added the first real local understanding pipeline:** the
> **PREPARED** project is analyzed entirely on-device — deterministic
> FFmpeg **scene detection** with representative frames, **speech-to-text**
> (faster-whisper, CPU `int8`, `tiny`/`base`), **OCR** (Tesseract) on scene
> frames, **deterministic visual metadata** (brightness/blur/complexity), and
> a **timeline alignment + context aggregation** pass that binds all evidence
> to scenes — producing structured `analysis/metadata/*.json` and the
> **ANALYZED** status. Optional local models degrade gracefully
> (`UNAVAILABLE`/`SKIPPED`), never faked.
>
> **Phase 5 adds story understanding + duration-aware script
> generation:** an **ANALYZED** project becomes **SCRIPT_READY** through
> evidence compression, hierarchical **story understanding** (a small local
> LLM — llama.cpp CLI + a quantized GGUF, never a cloud API), **important
> scene selection** (weighted evidence + story continuity, redundancy
> control), **duration planning** (2/3/4-minute word budgets allocated
> *before* writing), an **original narration script** in English / Hindi /
> Bengali, and **deterministic quality control** (0-100 score, language,
> length, chronology, repetition, source-copying and ungrounded-claims
> checks).
>
> **Phase 6 voices that script locally:** a **SCRIPT_READY** project becomes
> **NARRATION_READY** through script segmentation into narration units,
> per-segment synthesis with a **local Piper TTS engine** (en/hi/bn voices
> configured manually — never downloaded silently), **real audio timing
> measured from the generated WAV** (never word-count estimates), a
> narration timeline that preserves the Phase 5 scene mapping, SRT/VTT
> subtitles timed to the actual audio, lossless WAV assembly with gentle
> normalization, and a deterministic 0-100 QC score.
>
> **Phase 7 (this phase) renders the final MP4:** a **NARRATION_READY**
> project becomes **COMPLETED** — the Phase 5 selected scenes and the Phase 6
> narration timeline are combined into a concrete output plan
> (`render/video_plan.json`); every selected source range is extracted and
> normalized (short scenes hold their last frame — no black frames, no dead
> air); an original-audio track is sliced from the source when it has audio
> and **ducked under the narration** via sidechain compression; the
> synchronized subtitles are **burned into the frames** with FFmpeg/libass;
> and a CPU-first **final.mp4** (libx264, `+faststart`) is encoded and
> re-probed by a deterministic **final QC** (container / video / audio /
> duration / subtitles / decode, 0-100). No cloud rendering — nothing is
> faked.
>
> **No paid APIs.** No Claude/OpenAI/Gemini keys. Everything runs on the
> user's PC, targeting 8 GB RAM, CPU-only, integrated graphics. Only **one
> heavy job runs at a time** (worker, `PROCESSING_CONCURRENCY=1`), models are
> lazy-loaded and released after each stage (the LLM is spawned per
> generation and exits after every call).

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
| FFmpeg    | 6.x+ (ffmpeg **and** ffprobe) | **Required from Phase 2** — FFprobe validates every upload; scene detection in Phase 4; the final MP4 render + QC in Phase 7 |
| Tesseract | 5.x (`tesseract` on PATH)   | **Optional** — OCR on scene frames. Without it OCR reports `unavailable` and the rest of the analysis still runs |
| faster-whisper | Python package + one model | **Optional** — speech-to-text. Without it STT reports `model_download_required` and the rest of the analysis still runs |
| llama.cpp | `llama-cli` binary | **Required for Phase 5** — story understanding + script generation. Install via `winget install llama.cpp` or the official GitHub release, or set `LLAMA_CPP_PATH` |
| GGUF model | one small quantized file (~1 GB) | **Required for Phase 5** — e.g. `Qwen2.5-1.5B-Instruct Q4_K_M`. Downloaded once explicitly (never silently); see setup below |
| Piper | `piper` CLI binary | **Required for Phase 6 narration** — install the official release (or `pip install piper-tts`), or set `TTS_EXECUTABLE_PATH` |
| Piper voices | `.onnx` + `.onnx.json` per language | **Required for Phase 6** — English/Hindi/Bengali voices. Downloaded once explicitly (never silently); see setup below |
| Unicode font | e.g. Windows `Nirmala.ttc` or a Noto Sans font | **Optional for Phase 7** — burning Hindi/Bengali subtitles needs a font with those glyphs; set `SUBTITLE_FONT_PATH`/`SUBTITLE_FONT_NAME` (English renders with the default sans font) |

FFmpeg is **not** downloaded automatically. Install it (e.g. `winget install
ffmpeg` or the gyan.dev build) and ensure `ffmpeg`/`ffprobe` are on PATH, or
set `FFMPEG_PATH`/`FFPROBE_PATH` in `.env`. When FFmpeg is missing the UI shows
a setup hint and uploads are rejected with a clear `ffmpeg_unavailable` error.

### Phase 4 — first-run local model setup (optional, no API key)

Speech-to-text uses faster-whisper with a **CPU-friendly `tiny` or `base`**
model. The app **never downloads a model silently** — do it once, explicitly:

```bat
scripts\download_whisper_model.bat tiny   :: or base (~75 MB / ~145 MB)
```

(Linux/macOS: `scripts/download_whisper_model_unix.sh tiny`). The files land
in `models/whisper/tiny/`, which `WHISPER_MODEL` in `.env` selects. Until the
model exists, analysis completes with `transcript_available: false` and a
clear warning — nothing is faked.

Tesseract (OCR): Windows `winget install UB-Mannheim.TesseractOCR` (or the
gyan.dev build), then ensure `tesseract` is on PATH (or set `TESSERACT_PATH`
in `.env`).

### Phase 5 — first-run local LLM setup (required for Generate, no API key)

Story understanding and script writing use a **small quantized GGUF model**
through the llama.cpp CLI. The app **never downloads a model silently** — do
it once, explicitly (~1 GB for the default 1.5B Q4 model):

```bat
scripts\download_llm_model.bat   :: Qwen2.5-1.5B-Instruct GGUF into models\llm\
winget install llama.cpp         :: provides the llama-cli binary
```

(Linux/macOS: `scripts/download_llm_model_unix.sh`, `brew install llama.cpp`
or the official release.) Then either leave `.env` defaults (auto-discovery:
`llama-cli` on PATH + a single `*.gguf` in `models/`) or set explicitly:

```dotenv
LLAMA_CPP_PATH=C:\llama.cpp\build\bin\Release\llama-cli.exe
LLAMA_MODEL_PATH=C:\models\qwen2.5-1.5b-instruct-q4_k_m.gguf
```

While the binary or model is missing, **Generate explanation** shows a setup
hint and refuses with `llm_unavailable` / `model_download_required` — the
pipeline never fakes a story. `GET /api/system/status` → `llm` reports
`available` / `model_available` / `model_name` (basename only) so the UI can
warn before you even click Generate.

### Phase 6 — first-run local TTS + voice setup (required for Generate Narration, no API key)

Narration uses the **Piper** CLI with one voice per language. The app
**never downloads a voice silently** — fetch the official English voice once,
explicitly (~60-100 MB):

```bat
scripts\setup_piper_voices.bat        :: en_US-lessac-medium into models\voices\
```

(Linux/macOS: `scripts/setup_piper_voices_unix.sh`.) Hindi and Bengali voices
are community-provided on Hugging Face; run the same script with a voice file
path to fetch them (see the script header). Then point `.env` at the files:

```dotenv
TTS_PROVIDER=piper
TTS_EXECUTABLE_PATH=C:\piper\piper.exe
TTS_VOICE_EN=models\voices\en\en_US\lessac\medium\en_US-lessac-medium.onnx
TTS_VOICE_HI=models\voices\hi\...\hi_IN-....onnx
TTS_VOICE_BN=models\voices\bn\...\bn_IN-....onnx
```

While the engine or a language voice is missing, **Generate narration** for
that language shows a setup hint and refuses with `tts_unavailable` /
`voice_unavailable` — the pipeline never fakes audio. `GET /api/system/status`
→ `tts` reports `available`, `executable_available` and per-language voice
availability so the UI warns before you click.

### Phase 7 — first-run subtitle font setup (only for Hindi/Bengali burn-in, no API key)

Burning subtitles into the final video uses FFmpeg/libass. Latin text renders
with the default sans-serif font, but **Hindi/Bengali glyphs need a Unicode
font configured** or the render refuses (never boxes/garbage):

```dotenv
# In .env - Windows ships Nirmala UI (Devanagari + Bengali); Noto fonts work too.
SUBTITLE_FONT_PATH=C:\Windows\Fonts\Nirmala.ttc
# or SUBTITLE_FONT_NAME=Noto Sans Devanagari
```

`GET /api/system/status` → `render.subtitle_font_configured` reports whether a
usable font is set, so the **Create final video** panel warns before you click.

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
│   │   ├── api/              # health, system status, projects, jobs, thumbnail,
│   │   │                     #   analyze, analysis, timeline, frames, scripts,
│   │   │                     #   narration, render
│   │   ├── services/         # ffmpeg detection, storage, uploads, preprocess,
│   │   │                     #   analysis (orchestrator), timeline, story/script
│   │   │                     #   (Phase 5), narration/subtitles/audio (Phase 6),
│   │   │                     #   render plan + final render (Phase 7),
│   │   │                     #   worker (single-job queue), cleanup
│   │   ├── ai/               # stt (faster-whisper), ocr (Tesseract), vision
│   │   │                     #   (deterministic PIL + optional LocalVisionProvider),
│   │   │                     #   llm (llama.cpp CLI), story, script, tts (Piper)
│   │   ├── video/            # ffprobe probing (metadata + validation),
│   │   │                     #   scenes (FFmpeg scene detection + frames),
│   │   │                     #   renderer (Phase 7 final encode)
│   │   ├── database/         # SQLite connection + schema + migration
│   │   ├── models/           # pydantic models + enums
│   │   └── utils/            # errors, structured logging, path safety,
│   │                         #   fingerprints (analysis/session idempotency)
│   ├── tests/                # pytest suite (Phases 1-7)
│   ├── requirements.txt
│   └── requirements-dev.txt
├── frontend/                 # React + Vite + TypeScript UI
│   └── src/                  # App, API client, types, styles
├── models/                   # local model files (whisper/ … Phase 4, llm/ Phase 5,
│                             #   voices/ Phase 6)
├── data/
│   ├── projects/             # per project: input/ temp/ output/
│   │                         #   analysis/ thumbnails/ audio/  (Phase 3)
│   │                         #   analysis/{metadata,frames}/ (Phase 4)
│   │                         #   analysis/story/ (Phase 5)
│   │                         #   subtitles/ audio/narration* (Phase 6)
│   │                         #   render/ output/final.mp4 (Phase 7)
│   ├── uploads/  temp/  outputs/  cache/
├── logs/                     # app.log + errors.log (auto-rotated)
├── scripts/                  # Windows .bat + unix helpers: setup/check/
│                             #   configure, run, diagnose, health, backup,
│                             #   uninstall, shortcut, whisper/LLM downloads +
│                             #   Piper voices
├── FIRST_RUN.bat             # Phase 9: one-click first-time setup
├── START_AI_VIDEO_EXPLAINER.bat   # one-click launch (backend + frontend + browser)
├── STOP_AI_VIDEO_EXPLAINER.bat    # one-click stop (launcher-started processes)
├── RESTART_AI_VIDEO_EXPLAINER.bat # stop → wait → start
├── docs/architecture.md      # pipeline design (Phases 1-7 flows)
├── docs/WINDOWS_SETUP.md     # beginner Windows install guide (A-O)
├── docs/WINDOWS_TROUBLESHOOTING.md
├── docs/LOCAL_MODELS.md      # model layout + .env wiring
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
| `ANALYSIS_TIMEOUT_SECONDS` | `1800`                          | Timeout per Phase 4 analysis stage       |
| `WHISPER_MODEL` / `WHISPER_DEVICE` / `WHISPER_COMPUTE_TYPE` | `tiny` / `cpu` / `int8` | faster-whisper config (CPU-first)        |
| `WHISPER_LANGUAGE_MODE`  | `preferred`                     | `preferred` (hint + auto-detect) · `auto` · `forced` |
| `SCENE_THRESHOLD`        | `0.3`                           | FFmpeg scene filter sensitivity (0..1)    |
| `MIN_SCENE_DURATION_SECONDS` / `MAX_SCENES` | `2.0` / `500`   | Scene assembly limits                    |
| `OCR_ENABLED` / `OCR_FRAME_LIMIT` | `true` / `60`           | Tesseract OCR toggle + frame cap         |
| `VISUAL_ANALYSIS_ENABLED` | `true`                          | Deterministic PIL frame metadata toggle   |
| `TESSERACT_PATH`         | (auto-discover on PATH)         | Absolute binary path if not on PATH      |
| `FFMPEG_PATH` / `FFPROBE_PATH` | (auto-discover on PATH)     | Absolute binary paths if not on PATH     |
| `OUTPUT_MAX_WIDTH` / `OUTPUT_MAX_HEIGHT` | `1280` / `720`   | Final-video size cap (downscales only, even pixels) |
| `OUTPUT_FPS`      | `30`                             | Final-video frame rate (number or `source`) |
| `VIDEO_CODEC` / `VIDEO_PRESET` / `VIDEO_CRF` | `libx264` / `veryfast` / `23` | CPU-first final encode settings |
| `ORIGINAL_AUDIO_ENABLED` | `true`                     | Mix (ducked) original audio from the selected ranges |
| `ORIGINAL_AUDIO_VOLUME` / `NARRATION_AUDIO_VOLUME` | `0.18` / `1.0` | Mix levels |
| `AUDIO_DUCKING_ENABLED` (+ `_THRESHOLD`/`_RATIO`/`_ATTACK_MS`/`_RELEASE_MS`) | `true` / `0.02` / `6` / `15` / `350` | Narration-driven sidechain ducking of the original audio |
| `SUBTITLE_BURN_ENABLED` | `true`                       | Burn subtitles into the final frames (libass) |
| `SUBTITLE_FONT_PATH` / `SUBTITLE_FONT_NAME` | (empty)       | Unicode font for burn-in — required for Hindi/Bengali glyphs |
| `SUBTITLE_FONT_SIZE` / `SUBTITLE_MARGIN_V` | `20` / `24`     | Burn-in style                           |
| `RENDER_TAIL_MS` / `RENDER_HOLD_GAP_MAX_MS` | `1200` / `1500` | Narration tail / max held gap (no black frames) |
| `RENDER_TIMEOUT_SECONDS` | `3600`                      | Upper bound for one render FFmpeg pass  |

## Windows installation & launch (Phase 9 — beginner-friendly)

The full installation/launch system lives next to this README. Install the
free external tools once (Python, Node.js, FFmpeg, plus optional Tesseract;
models are configured later — see `docs/LOCAL_MODELS.md`), then:

```bat
FIRST_RUN.bat                    :: one-time install + checks (double-click)
START_AI_VIDEO_EXPLAINER.bat     :: start backend + frontend, open browser
STOP_AI_VIDEO_EXPLAINER.bat      :: stop what the launcher started
RESTART_AI_VIDEO_EXPLAINER.bat   :: stop, wait, start again
```

- `FIRST_RUN.bat` (→ `scripts\setup_windows_full.bat`) creates the venv,
  installs backend/frontend dependencies, prepares `.env` + storage + the
  SQLite database, runs the backend tests and frontend type check, and ends
  with a full dependency check. Missing models are reported with
  `[MISSING]`/`[WHY]`/`[WHERE]`/`[HOW]` — **large models are never
downloaded automatically**.
- `START_AI_VIDEO_EXPLAINER.bat` runs `scripts\check_dependencies.bat`
  (PASS/WARN/ERROR gate → `READY TO RUN`), starts the backend on
  `http://127.0.0.1:8000` and the frontend on `http://127.0.0.1:5173` in
  their own tracked console windows, waits until `/api/health` and the page
  are live, and opens the browser. Already-running instances are reused;
  ports occupied by other programs produce clear WHAT/WHY/HOW errors
  instead of killing anything.
- Other helpers: `scripts\health_check.bat` (live PASS/WARN/FAIL report),
  `scripts\backup_data.bat` (data ZIP, never secrets), `scripts\uninstall_app.bat`
  (keeps your data unless you choose the full wipe),
  `scripts\create_desktop_shortcut.ps1` (desktop shortcut),
  `scripts\diagnose_windows.bat` + `scripts\check_dependencies.bat`
  (reports), and `scripts\configure_windows.bat` (creates/validates `.env`).
- Guides: **`docs\WINDOWS_SETUP.md`** (A–O install steps),
  **`docs\WINDOWS_TROUBLESHOOTING.md`** (WHAT/WHY/HOW for every error) and
  **`docs\LOCAL_MODELS.md`** (model layout + `.env` wiring).

### Start the backend

```bat
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```
(cd into `backend/` first, or use `scripts\run_backend.bat` — foreground,
Ctrl+C to stop)

Interactive API docs: http://127.0.0.1:8000/docs

### Start the frontend

```bat
cd frontend
npm run dev
```
http://127.0.0.1:5173 — Vite proxies `/api` to the backend, so no CORS setup.
Dependencies install automatically when missing (`scripts\run_frontend.bat`).

### Run the tests

```bat
.venv\Scripts\python -m pytest backend\tests -q
```
(or `scripts\run_tests.bat`)

Tests that need a real encoder generate tiny synthetic videos with FFmpeg and
**skip gracefully** when FFmpeg is missing; the rest of the suite (Phases 1-7)
runs against scripted fake ffmpeg/ffprobe binaries and fake LLM/TTS/render
services, so it works everywhere — including the full preprocess → analysis →
script → narration → **render** job lifecycles, worker serialization, failure
paths, STT/OCR/vision service units, timeline alignment, render planning and
final QC.

### Phase 8 — diagnostics, pre-flight & performance (QA tooling)

* `scripts\diagnose_windows.bat` (or `scripts\diagnose_unix.sh`) prints a
  full environment report: Python/pip/venv, FFmpeg, FFprobe, Tesseract (+hin/ben
  packs), Piper, llama-cli, GGUF/Whisper/Piper model files, RAM, CPU, disk
  space, `.env` presence and every required directory. Exit code 0 = ready,
  1 = errors (never prints secrets).
* `GET /api/system/preflight?language=en&target_duration_seconds=120`
  machine-readably checks every component a run needs (ffmpeg, ffprobe,
  database, storage, disk space, LLM, Piper + the selected language's voice,
  subtitle font for hi/bn burn-in) with `ok/required/detail/setup_hint` per
  check — optional components (Whisper/OCR) are reported but never block.
  `GET /api/system/status` also carries a flat `dependencies` block.
* When a render completes, the worker writes
  `data/projects/<id>/analysis/performance/performance_report.json` — real
  wall-clock timings per stage from the persisted job timestamps (peak RAM is
  operator-measured on real hardware; never fabricated).
* See `docs/phase8-real-world-test-report.md` for the honest Phase 8 status:
  everything verifiable without the target PC is verified; the real-media
  acceptance matrix must run on the Windows machine (procedure included).

## API

| Method | Endpoint                        | Purpose                                   |
| ------ | ------------------------------- | ----------------------------------------- |
| GET    | `/api/health`                   | Liveness (app + version)                  |
| GET    | `/api/system/status`            | Python, FFmpeg, SQLite, storage, DB, limits, **worker state**, **LLM report**, **TTS report**, **render readiness** |
| GET    | `/api/projects`                 | List projects (metadata included)         |
| GET    | `/api/projects/{id}`            | One project + current status/progress     |
| POST   | `/api/projects/upload`          | **Multipart video upload** (below)        |
| POST   | `/api/projects`                 | Create an empty record (legacy/record-only) |
| POST   | `/api/projects/{id}/preprocess` | **Queue Phase 3 preprocessing** (below)   |
| GET    | `/api/projects/{id}/jobs`       | Job history (stage/status/progress/error) |
| GET    | `/api/projects/{id}/thumbnail`  | Poster JPEG (once PREPARED)               |
| POST   | `/api/projects/{id}/analyze`    | **Queue Phase 4 local analysis** (below)  |
| GET    | `/api/projects/{id}/analysis`   | Latest analysis-run summary               |
| GET    | `/api/projects/{id}/timeline`   | Aligned per-scene evidence timeline       |
| GET    | `/api/projects/{id}/analysis/frames/{scene_id}` | Scene representative JPEG (path-safe) |
| POST   | `/api/projects/{id}/generate-script` | **Queue Phase 5 story+script** (JSON: `language`, `target_duration_seconds`) |
| GET    | `/api/projects/{id}/story-status`  | Latest script-run summary + live stage  |
| GET    | `/api/projects/{id}/story`        | Story model (content type, premise, events) |
| GET    | `/api/projects/{id}/selected-scenes` | Important scenes with reasons         |
| GET    | `/api/projects/{id}/duration-plan`  | Word budgets per scene + targets      |
| GET    | `/api/projects/{id}/script`        | The generated narration script        |
| GET    | `/api/projects/{id}/script-quality` | Deterministic QC report (0-100)       |
| POST   | `/api/projects/{id}/generate-narration` | **Queue Phase 6 TTS narration** (JSON: `language`, optional `voice_id`) |
| GET    | `/api/projects/{id}/narration-status` | Latest narration-run summary + live stage |
| GET    | `/api/projects/{id}/narration`     | Narration manifest (relative paths only) |
| GET    | `/api/projects/{id}/narration/audio` | Streams the assembled `narration.wav` |
| GET    | `/api/projects/{id}/narration/subtitles?format=srt\|vtt` | Subtitles timed to real audio |
| GET    | `/api/projects/{id}/narration/segments` | Segment timeline (text/scene ids/times) |
| POST   | `/api/projects/{id}/render`   | **Queue Phase 7 final render** (idempotent; re-encodes only when inputs changed) |
| GET    | `/api/projects/{id}/render-status` | Latest render-run summary + live stage |
| GET    | `/api/projects/{id}/render`   | Final render manifest (relative paths only)      |
| GET    | `/api/projects/{id}/render-plan` | Output video plan (`render/video_plan.json`)  |
| GET    | `/api/projects/{id}/render/video` | Streams the final `final.mp4`              |
| GET    | `/api/projects/{id}/render/subtitles?format=srt\|vtt` | Sidecar subtitles for the final video |
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
| `analyzing`    | Phase 4 worker is running the local analysis (0 → 100%)        |
| `analyzed`     | Scenes / transcript / OCR / visual / timeline stored (100%)    |
| `scripting`    | Phase 5 worker is writing the story + script (0 → 100%)       |
| `script_ready` | Original explanation ready (en/hi/bn) — awaiting narration   |
| `narrating`    | Phase 6 worker is synthesizing narration (0 → 100%)          |
| `narration_ready` | TTS WAV + synced SRT/VTT ready (Phase 6 complete)         |
| `rendering`    | Phase 7 worker is mixing + rendering the final MP4 (0 → 100%) |
| `render_failed`| Final render failed; artifacts cleaned, retry from NARRATION_READY |
| `completed`    | Final MP4 rendered + QC-passed (Phase 7 complete)           |
| `failed`       | Upload/validation/preprocessing/analysis/story/narration failed; `error_message` explains |
| `created`      | Record-only project created via the legacy endpoint             |
| `queued/processing` | Reserved for the future pipeline worker                |

On any **upload** failure the partial file is removed, the project folder
cleaned up, and the record stays `failed` with the error message. On a
**preprocessing** failure the job is recorded as `failed` with the reason and
the project **returns to `ready`** (the input video is fine) so you can retry;
partial assets are removed. A **render** failure records the job/run as
`failed`, clears only the Phase 7 artifacts (render plan, temp clips, partial
output) and sets the project to `render_failed` — Phases 1-6 data stays
intact, so **Create final video** can simply be retried. Deleting a project
removes its database record **and** its controlled folder under
`data/projects/` (including assets).

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
stages (`preprocess`/`analysis`), lifecycle and progress.

Phase 4 adds an `analysis_results` table (same additive migration): one row
per run with `status` (`queued`/`running`/`completed`/`failed`),
`current_stage`, timestamps, `detected_language`, `scene_count`,
`transcript_available`, `ocr_available`, `visual_provider`,
`processing_seconds`, `warnings`, plus **config/preprocessing fingerprints**
for idempotency. Transcript/OCR payloads are **never** stored in SQLite —
they live as JSON files under `analysis/metadata/`.

Phase 5 adds a `script_runs` table (language, target duration, content type,
selected-scene count, word count, quality score, estimated duration,
generation fingerprint), Phase 6 adds a `tts_runs` table (voice, audio/subtitle
paths, measured duration, segment count, quality score, narration
fingerprint) and Phase 7 adds a `render_runs` table (output path/duration/size,
QC score, subtitle status, render fingerprint) — all via the same additive
`migrate_schema`, so databases from earlier phases upgrade in place. Large
payloads (transcripts, scripts, subtitles, manifests) always stay as JSON
text files inside the project folder.

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

## Phase 4 local analysis (how it works)

`POST /api/projects/{id}/analyze` on a **PREPARED** project (idempotent on
**ANALYZED**, `409` conflicts while running, retry after failure):

1. A `processing_jobs` row (`stage=analysis`) **and** an `analysis_results`
   row are persisted, and the project becomes **analyzing**.
2. The same single background worker runs the stages **sequentially** — one
   heavy operation at a time, models lazy-loaded and released between stages:
   - **Scene detection** (structural): FFmpeg's `select='gt(scene,T)'` filter
     on the analysis copy (no extra Python package, RAM stays flat), then
     boundary assembly enforcing `MIN_SCENE_DURATION_SECONDS` and
     `MAX_SCENES`. Representative frames are extracted in one decode pass
     into `analysis/frames/scene_%03d.jpg`.
   - **Speech-to-text** (graceful): faster-whisper (`tiny`/`base`, CPU,
     `int8`) transcribes `audio/audio.wav` — 16 kHz mono is its natural
     input. `WHISPER_LANGUAGE_MODE=preferred` hints with the project language
     while keeping auto-detection; the detected language + probability are
     recorded. Missing model/package → `transcript_available: false` + a
     clear message; silent videos → `SKIPPED_NO_AUDIO`.
   - **OCR** (graceful): Tesseract on scene representative frames only
     (grayscale + autocontrast, capped by `OCR_FRAME_LIMIT`), duplicates
     dropped. Missing Tesseract → `ocr_available: false` + install hint.
   - **Visual analysis** (graceful): deterministic PIL metadata per frame
     (brightness, blur/edge-energy proxy, complexity, dimensions). A small
     local vision model can be plugged in later through
     `LocalVisionProvider` without touching the pipeline.
   - **Timeline alignment**: every scene gets its overlapping transcript
     segments, in-range OCR entries, representative frame and visual
     metadata, plus a deterministic **information-density score** (0-100).
   - **Quality check + manifest**: timestamp/range validation warnings and
     `analysis_manifest.json` (fingerprints, availability flags, relative
     asset references only — never absolute paths).
3. Success → **analyzed** (100%) with the run summary in SQLite; the aligned
   evidence is served by `GET /timeline` and frames by
   `GET /analysis/frames/{scene_id}` (path-safe, integer-validated).
   Failure → the run and job are `failed`, Phase 4 artifacts are removed
   (Phase 3 assets are preserved), and the project returns to **prepared**
   so Analyze can be retried without re-uploading or re-preprocessing.

**Progress is honest:** fixed stage windows (preparing 5%, scene 25%, STT
50%, OCR 70%, visual 82%, timeline 92%, QC 97%, finalizing 100%) fed by real
per-stage progress (FFmpeg elapsed time, Whisper segment time, frames
processed) — never invented percentages.

**Idempotency:** the run stores a config fingerprint and a preprocessing
fingerprint. If neither changed, re-analyzing reuses the existing results
without re-running Whisper/OCR/scenes; changing e.g. `SCENE_THRESHOLD` or
`WHISPER_MODEL` invalidates them and triggers a fresh run.

**Privacy:** nothing leaves the PC — no uploads, no telemetry, no cloud
inference. Transcript/OCR/visual documents are local analysis artifacts only.

## Frontend

The single-page UI runs the whole pipeline, upload → final MP4:

- drag & drop **or** file picker, with client-side format/size checks
- language (English / Hindi / **বাংলা**) and target length (2 / 3 / 4 min)
- real upload progress (bytes streamed) + stage status
- success shows the validated metadata card (duration, resolution, FPS +
  raw frame rate, audio, codecs, container, size, bitrate, SHA-256)
- **Prepare for analysis** queues preprocessing; the card live-updates a
  progress bar (1 s polling) while the worker runs
- when **prepared**: poster thumbnail, analysis-copy specs, audio-track
  status, and an **Analyze video** button (Phase 4)
- during analysis: live progress bar with the current stage label (Scene
  detection → Speech recognition → OCR → Visual analysis → Timeline → QC)
- when **analyzed**: results panel with detected language, scene count,
  speech/OCR/visual status, processing time, warnings, and a per-scene
  timeline — plus the Phase 5 **Generate explanation** panel (language
  English / Hindi / বাংলা, duration 2 / 3 / 4 min, local-LLM readiness
  warning when llama.cpp or the GGUF is missing)
- while **scripting**: honest stage progress (Preparing evidence →
  Understanding story → Scoring scenes → Selecting important scenes →
  Planning duration → Writing explanation → Quality checking) driven by
  the persisted run row
- when **script_ready**: an “Explanation ready” card — language, target
  and estimated duration, word count, quality score, detected content
  type, selected-scene count; **Story overview** (premise + turning
  points); **Important scenes** (timestamp, importance score, reasons,
  narration word budget, thumbnail); the full **script** with a Copy
  button; the **quality check** chips; regenerate controls (language /
  duration) that re-run the backend pipeline; and the scene timeline
  with selected vs. skipped marks
- when **script_ready** and the narration has not run yet: a **Generate
  narration** panel — language + voice, with Piper/voice readiness warnings;
  while **narrating**: honest stage progress (Segmenting script →
  Synthesizing → Measuring audio → Writing subtitles → Assembling →
  Quality check); then **Narration ready**: measured duration + QC score
  chips, a player for `narration.wav`, the synced **subtitles**
  (SRT/VTT preview + download), the **segment → scene** mapping, and
  regenerate controls (language / voice)
- when **narration_ready**: the **Create final video** panel with render
  readiness (FFmpeg + subtitle-font warnings); while **rendering**: live
  stage progress (Preparing plan → Extracting clips → Mixing audio →
  Burning subtitles → Final QC); when **completed**: **Final video ready** —
  playback + **Download MP4** (plus sidecar SRT/VTT), duration/resolution/
  codec/size stats, subtitle status (burned or sidecar-only), the **Final
  QC** chips, warnings, and a **Render again** action (idempotent — inputs
  must change before anything re-encodes)
- project history shows status tags incl. `preprocessing`/`prepared`/
  `analyzing`/`analyzed`/`scripting`/`script_ready`/`narrating`/
  `narration_ready`/`rendering`/`render_failed`/`completed`; **View** opens
  details, **Delete** removes record + files

Backend validation is authoritative — the client checks are only UX.

## Logging

Structured lines: `timestamp | level | module | message | project=… | job=…`.
`logs/app.log` for everything, `logs/errors.log` for errors only, both
auto-rotating. Project ids are attached via context; secrets are never logged.

## Phase 5 story + script (how it works)

`POST /api/projects/{id}/generate-script` on an **ANALYZED** project with
`{"language": "en|hi|bn", "target_duration_seconds": 120|180|240}` — idempotent
reuse when the generation fingerprint (analysis + language + duration +
model + prompt/planner versions) matches, `409` while running, `503` with a
setup hint when the local LLM is unavailable, retry after failure:

1. **Evidence preparation** — Phase 4 artifacts are compressed into
   `analysis/story/evidence_manifest.json`: per-scene transcript excerpts
   (capped by `STORY_MAX_EXCERPT_CHARS`), OCR text, visual metadata,
   information/speech density, neighbor ids. Long videos never dump a full
   transcript into a prompt.
2. **Story understanding** — scenes are processed in batches of
   `STORY_BATCH_SCENES` (default 15); each batch yields a compact JSON
   summary (events/facts/entities/uncertainties), then one global call
   produces `story.json`: content type + confidence, premise, events,
   turning points, beginning/middle/ending, cause/effect, important facts,
   uncertain points — every claim carries `scene_ids` evidence references.
   The prompt forbids invented names/numbers; uncertain content goes to
   `uncertain_points`; invalid scene ids and unknown content types are
   filtered in code (falls back to `general` with confidence ≤ 0.5).
   Per-scene semantic scores are computed deterministically from which
   evidence sets a scene appears in.
3. **Importance + selection** — `scene_importance.json` scores every scene
   with documented weights (information density 25%, semantic 25%, turning
   point 15%, speech 15%, continuity 10%, OCR 5%, re-normalized; up to 5%
   redundancy penalty for text-duplicate neighbors). `selected_scenes.json`
   always keeps the opening/closing scenes, picks by score, fills timeline
   gaps for coverage and drops redundant consecutive near-duplicates.
4. **Duration planning** — the target duration defines a word budget
   (2 min: 250-300, 3 min: 375-450, 4 min: 500-600 at `NARRATION_WPM=145`)
   that is allocated across selected scenes **before** writing; scenes
   without evidence get a 0-word budget (except fully silent videos, which
   fall back to duration-proportional). Output: `duration_plan.json` +
   `script_plan.json` (hook, sections with scene ids/purposes/budgets).
5. **Script generation** — one llama.cpp call writes the original narration
   in the selected language: per-section evidence + word budgets + content-
   type style rules; paraphrase (never verbatim transcript), no invented
   facts, natural Hindi/Bengali rather than word-for-word translation.
   Output: `script.json` (sections, full text, word count, estimated
   duration = words ÷ WPM × 60).
6. **Quality control** — `script_quality.json`: deterministic checks
   (language script, length, scene validity, chronology, repetition,
   source copying via verbatim n-gram runs, ungrounded numbers/claims,
   empty/garbage rejection) and a documented 0-100 score — grounding 25%,
   coverage 20%, coherence 15%, duration fit 20%, chronology 10%, language
   5%, originality 5%.
7. Success → **script_ready** with the run summary in SQLite; all artifacts
   live under `analysis/story/` with relative paths only. Failure → the run
   and job are `failed`, Phase 5 artifacts are removed (Phase 3/4 assets
   are preserved), and the project returns to **analyzed** for retry.

**LLM lifecycle (8 GB policy):** llama.cpp is spawned per generation with a
hard timeout and exits afterwards — one model in RAM at a time, nothing
resident between jobs, `PROCESSING_CONCURRENCY=1`.

## Phase 6 local narration + subtitles (how it works)

`POST /api/projects/{id}/generate-narration` on a **SCRIPT_READY** project
with `{"language": "en|hi|bn"}` and an optional `voice_id` — idempotent reuse
when the narration fingerprint (script + language + voice + TTS settings)
matches, `409` while running, `503` with a setup hint when the local TTS
engine or that language's voice is unusable, retry after failure:

1. **Script segmentation** — the narration script is split into sentence-ish
   narration units that keep the Phase 5 section/scene mapping
   (`services/segmentation.py`).
2. **Per-segment local TTS** — each unit is synthesized one at a time by the
   **Piper CLI** subprocess (`ai/tts.py`: arg arrays, no shell, hard timeout,
   configured en/hi/bn voice; never downloaded silently) into a WAV on disk.
3. **Real audio timing** — every segment's duration is **measured from its
   generated WAV**, never estimated from word counts; configured lead-in and
   between-segment gaps build `audio/narration_timeline.json` (start/end ms +
   scene ids).
4. **Subtitles** — SRT and VTT are generated timed to that real audio
   (UTF-8, per-line/per-caption readability caps).
5. **Assembly** — the segments are concatenated losslessly with FFmpeg into
   `audio/narration.wav` and gently normalized to a target level (no
   clipping).
6. **Deterministic QC** (`services/narration_qc.py`) — checks audio duration,
   subtitle/timeline validity and the scene mapping; a documented 0-100 score.
7. Success → **narration_ready** with the run summary in SQLite. Failure →
   run/job `failed`, Phase 6 artifacts cleared (Phases 3-5 preserved), project
   returns to **script_ready** for retry.

## Phase 7 final render (how it works)

`POST /api/projects/{id}/render` on a **NARRATION_READY** (or
**RENDER_FAILED**) project — no free-form options: rendering is fully
determined by the stored artifacts plus local render settings, all
fingerprinted, so identical inputs reuse the existing video without a
re-encode:

1. **Render planning** (`services/render_plan.py`) — reads Phase 5
   `analysis/story/selected_scenes.json` and Phase 6
   `audio/narration_timeline.json` and writes `render/video_plan.json`. The
   **narration timeline is the master clock**: every narration segment starts
   at the same absolute millisecond in the output as in the narration, so
   subtitles/voice never drift from the visuals and the video is never
   shorter than the narration. Segments are assigned chronologically to the
   selected scenes; a scene window shorter than its source is played at
   source speed and then **holds its last frame** (tpad clone), and narration
   gaps hold the previous scene — **no black frames, no dead air**.
2. **Clip extraction** — only the planned source ranges are cut from the
   **original upload** (`-ss`/`-t`, per clip, streamed), normalized once to a
   common size/fps/pixfmt as H.264 MPEG-TS intermediates. The whole source
   video is never re-encoded or held in RAM.
3. **Original audio** — only when the source has audio and
   `ORIGINAL_AUDIO_ENABLED=true`: one AAC slice per clip window (silence-free,
   padded to its exact length) is cut and concatenated into an original track.
4. **Mix** — one final FFmpeg pass maps the video, the original track (if
   any) and `narration.wav`: narration is normalized to stereo 48 kHz at
   `NARRATION_AUDIO_VOLUME`; the original audio plays at
   `ORIGINAL_AUDIO_VOLUME` and is **ducked under the narration** via
   sidechain compression (`AUDIO_DUCKING_*`); a limiter guards against peaks.
   When there is no original audio the narration is padded to the full video
   length so the tail is not silent.
5. **Subtitle burn-in** — FFmpeg's `subtitles` (libass) filter burns
   `subtitles/subtitles.srt` with the configured font size/margin/outline;
   Hindi/Bengali require `SUBTITLE_FONT_PATH`/`SUBTITLE_FONT_NAME` and the
   render **refuses with a setup message** (never renders tofu boxes).
6. **Encode** — `libx264` preset/CRF (default `veryfast`/`23`) + AAC 160 kbps
   stereo 48 kHz with `-movflags +faststart`, written to a temp file and
   atomically renamed to `output/final.mp4` only on success; every pass has a
   hard `RENDER_TIMEOUT_SECONDS`.
7. **Final QC** (`services/final_qc.py`) — the finished file is re-probed
   with FFprobe: container, video and audio streams must exist; duration must
   be within tolerance of the render plan and **never shorter than the
   narration**; frames are spot-decoded at the start/middle/end; the sidecar
   SRT is sanity-checked. Documented 0-100 weights: container 20%, video 20%,
   audio 20%, timeline 15%, subtitles 15%, decode 10%. Structural problems
   raise `FinalQCRejectedError` — a broken file is never delivered.
8. Success writes `output/final_manifest.json` and
   `output/final_timeline.json` (source → output mapping), removes
   `render/temp/`, and the project becomes **completed** (100%) with the QC
   score in the run row. Failure records the run/job as `failed`, clears only
   Phase 7 artifacts and sets **render_failed** — Phases 1-6 stay intact and
   **Create final video** can be retried as-is.

**Resource policy:** the render runs on the same single worker (concurrency
1); FFmpeg subprocesses are memory-bounded (streamed, never whole-video in
RAM) and CPU-only — the final pass is the heaviest step and runs with a
CPU-first x264 preset. Nothing is uploaded; FFprobe/FFmpeg never talk to the
network.

## Limitations (honest)

- **Voices must be installed manually** — English is one explicit download
  (`scripts/setup_piper_voices.*`); Hindi/Bengali depend on the community
  voices you fetch and configure. Missing voices fail honestly with
  `voice_unavailable`, never fake audio.
- **TTS is slow on this hardware** — one short segment at a time on the
  single worker. A 3-minute narration can take several minutes of synthesis
  on a Ryzen 3 3200G; progress is persisted per stage and retry is safe.
- **The final encode is CPU-only** — x264 at ≤ 1280×720 on a Ryzen 3 3200G
  is not fast: expect minutes (longer for high-resolution sources). Only the
  selected ranges are decoded, progress is real, and a failed/timed-out pass
  can simply be retried (temp files are cleaned first).
- **Hindi/Bengali burn-in needs a Unicode font** — without
  `SUBTITLE_FONT_PATH`/`SUBTITLE_FONT_NAME` the render refuses with setup
  instructions instead of burning unusable glyphs.
- **No visual understanding model** — the LLM only sees transcript/OCR text
  and numeric visual metadata, never the frames themselves. It is explicitly
  told not to describe visible objects that the evidence does not support;
  the render only ever shows real source scenes.
- Story/script quality depends on the local model: a small quantized GGUF
  gives good grounding but cannot match a frontier model. The QC stages flag
  empty, copied, repetitive, out-of-language or ungrounded output;
  `llm_unavailable`/`voice_unavailable` are never faked.
- The worker queue is in-process: jobs do not survive a backend restart (a
  queued row would remain `queued`; re-run the stage to retry; stale
  `analyzing`/`scripting`/`narrating`/`rendering` projects auto-recover to
  `prepared`/`analyzed`/`script_ready`/`narration_ready` on the next call).
- Real-FFmpeg integration tests skip when FFmpeg is absent (they run on your
  PC); the deterministic fake-binary/fake-LLM/fake-TTS/fake-render suite
  covers the full lifecycle.

## Roadmap

1. **Phase 1 (done)** — architecture, config, SQLite, REST foundation, FFmpeg
   detection, UI scaffold, tests, docs.
2. **Phase 2 (done)** — upload & validation engine: streaming storage,
   SHA-256 fingerprints, FFprobe metadata, READY/FAILED lifecycle.
3. **Phase 3 (done)** — preprocessing & analysis assets (optimized copy,
   thumbnail, 16 kHz WAV) + single-job background worker.
4. **Phase 4 (done)** — on-device analysis: scene detection, STT,
   OCR, deterministic visual metadata, timeline alignment + context
   aggregation → ANALYZED.
5. **Phase 5 (done)** — story understanding, important-scene
   selection, duration-aware script generation (en/hi/bn) with
   deterministic QC → SCRIPT_READY.
6. **Phase 6 (done)** — local TTS narration (Piper CLI, en/hi/bn voices),
   real-audio segment timing, SRT/VTT subtitles, narration assembly +
   normalization, deterministic audio/timeline/subtitle QC → NARRATION_READY.
7. **Phase 7 (done)** — render planning (narration-as-master-clock),
   selected-scene clip extraction, original-audio + narration mix with
   ducking, subtitle burn-in and the CPU-first final MP4 encode, with a
   deterministic final QC → COMPLETED.
8. **Phase 8 (done — tooling; real-hardware acceptance pending)** — Windows
   diagnostic script, machine-readable dependency status + pre-flight
   endpoint, per-stage performance report, absolute-path redaction in the
   public status response, security/regression scans. The real-media
   acceptance matrix runs on the target Windows PC (see
   `docs/phase8-real-world-test-report.md`).
9. **Phase 9 (done — code; real Windows run pending)** — beginner-friendly
   Windows installation & launch system: `FIRST_RUN.bat` +
   `scripts/setup_windows_full.bat` (one-command setup: venv, deps,
   directories, DB migrations, tests, type check, security scan),
   `scripts/check_dependencies.bat` + `scripts/configure_windows.bat`,
   `START/STOP/RESTART_AI_VIDEO_EXPLAINER.bat` (dependency gate, port/
   duplicate handling, health waits, auto-browser), plus
   `health_check.bat`, `backup_data.bat`, `uninstall_app.bat`,
   `create_desktop_shortcut.ps1` and the `docs/WINDOWS_*` + `LOCAL_MODELS`
   guides. The one-click flow must be executed once on a real Windows PC
   (every step is scripted and documented there).

With Phase 7 the full product loop is complete: upload any supported video,
pick English/Hindi/Bengali and 2/3/4 minutes, and the app returns a final
narrated, subtitled MP4 — every step local. With Phase 9 the loop is
installable by double-clicking two `.bat` files on Windows. Future work (no
rewrite needed) is polish: optional GPU encode, better local vision models,
more voices/languages and packaging (installer).

See `docs/architecture.md` for the full pipeline design.

## Troubleshooting

| Symptom                                        | Fix                                                                 |
| ---------------------------------------------- | ------------------------------------------------------------------- |
| Setup/launcher prints `[ERROR]`/`[MISSING]`    | Follow its WHAT/WHY/WHERE/HOW; full guides in `docs\WINDOWS_SETUP.md`, `docs\WINDOWS_TROUBLESHOOTING.md`, `docs\LOCAL_MODELS.md` |
| “FFmpeg not detected” / `ffmpeg_unavailable`   | Install FFmpeg (winget/gyan.dev), restart the backend, reload the UI |
| `413 upload_too_large`                         | Raise `MAX_UPLOAD_SIZE_MB` in `.env` or use a smaller video          |
| `unsupported_file_type`                        | Container not in the allowlist (list is shown in the error)          |
| `invalid_video` after a full upload            | The file is not decodable as video (corrupt/audio-only/empty)        |
| `duplicate_video`                              | The identical file was already uploaded — delete the earlier project |
| Upload stalls / interrupted                    | No partial file is kept; simply retry (a FAILED row shows the error) |
| Preprocessing fails (`FFmpeg failed…`)         | Check `logs/errors.log`; the project returns to READY — retry, or lower `ANALYSIS_WIDTH`/`ANALYSIS_FPS` for very large videos |
| Preprocessing times out                        | Raise `PREPROCESS_TIMEOUT_SECONDS` (per-step cap)                    |
| `409 job_conflict`                             | A preprocess/analysis job is already running for that project — wait |
| Analysis: “Whisper model not installed”        | Run `scripts\download_whisper_model.bat tiny` once (no API key); analysis still completes without speech |
| Analysis: “OCR unavailable / Tesseract…”       | `winget install UB-Mannheim.TesseractOCR`, or set `TESSERACT_PATH`; analysis still completes without OCR |
| Analysis fails (scene detection)               | Check `logs/errors.log`; Phase 3 assets are kept and the project returns to PREPARED — retry Analyze |
| Generate shows `llm_unavailable`              | Install llama.cpp (`winget install llama.cpp` or the GitHub release) and/or set `LLAMA_CPP_PATH` in `.env`; restart the backend |
| Generate shows `model_download_required`      | Run `scripts\download_llm_model.bat` once (no API key, ~1 GB) and/or set `LLAMA_MODEL_PATH`; the app never auto-downloads |
| Generate fails / empty script                  | Check the QC report (`/api/projects/{id}/script-quality`) and `logs/errors.log`; the project returns to ANALYZED — retry, or raise `LLAMA_MAX_TOKENS` |
| Generate narration fails (`voice_unavailable`) | Install the voice (`scripts/setup_piper_voices.*`) and/or set `TTS_VOICE_<LANG>`; the project returns to SCRIPT_READY — retry |
| Render fails / `render_failed`                | Check `logs/errors.log` and the run row (`/api/projects/{id}/render-status`); Phase 7 artifacts are cleaned and Phases 1-6 kept — click **Create final video** again |
| “Hindi/Bengali burn-in needs a Unicode font”  | Set `SUBTITLE_FONT_PATH` (e.g. `C:\Windows\Fonts\Nirmala.ttc`) or `SUBTITLE_FONT_NAME` in `.env`, restart the backend |
| Final video has no subtitle text burned in     | Check `SUBTITLE_BURN_ENABLED` (default true) and the `Subtitles` status on the **Final video ready** card |
| Final encode is very slow                      | Expected on CPU at ≤1280×720; lower `OUTPUT_MAX_WIDTH/HEIGHT` or raise `VIDEO_CRF`; progress is real and retry is safe |
| Project stuck at `preprocessing`/`analyzing`/`scripting`/`narrating`/`rendering` after restart | In-process queue lost the job; restart the stage (stale runs auto-recover to their retry state on the next call) |
| Pre-flight says `ok=false` / diagnose reports errors | Run `scripts\diagnose_windows.bat` and follow the per-check `setup_hint` (missing FFmpeg/Piper/LLM/voice/font are the usual causes) |
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
