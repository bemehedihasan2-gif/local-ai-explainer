# Windows Setup Guide — Local AI Video Explainer

A beginner-friendly, step-by-step guide to install and run the app on a
**Windows 10/11 64-bit** PC (target: AMD Ryzen 3 3200G, 8 GB RAM, no GPU,
no paid API, no cloud AI — everything stays on your PC).

**The short version** (after the tools below are installed):

1. Copy the `ai-video-explainer` folder anywhere on your PC.
2. Double-click `FIRST_RUN.bat` (installs + checks everything).
3. Double-click `START_AI_VIDEO_EXPLAINER.bat` — the browser opens at
   `http://127.0.0.1:5173`.
4. When done, double-click `STOP_AI_VIDEO_EXPLAINER.bat`.

---

## A. System requirements

| Item      | Recommended                          | Why |
| --------- | ------------------------------------ | --- |
| OS        | Windows 10 64-bit (1803+) or Windows 11 | The launcher scripts use curl.exe (ships since 1803) |
| CPU       | Any 64-bit x86; AMD Ryzen 3 3200G works | CPU-only inference and encoding |
| RAM       | 8 GB                                 | The app is tuned for one heavy job at a time on 8 GB |
| Disk      | ≥ 10 GB free                         | Models (~1.5 GB) + your videos + final MP4s + temp files |
| Python    | 3.10–3.12 **64-bit**, on PATH        | Backend (FastAPI, SQLite) |
| Node.js   | 18+ LTS (20/22 preferred), on PATH   | Frontend (React/Vite) |

> 32-bit Python/Node will not work well — install the 64-bit installers.

---

## B. Python installation

1. Go to <https://www.python.org/downloads/> and download the latest
   **Python 3.10+ (64-bit)** installer.
2. Run it and **tick “Add python.exe to PATH”** at the bottom of the first
   screen, then click **Install Now**.
3. Open a **new** terminal and verify:
   ```
   python --version
   pip --version
   ```
   Both should print versions. If `python` is not found, re-run the installer
   and tick “Add python.exe to PATH”, then open a new terminal.

---

## C. FFmpeg installation

FFmpeg is **required** — the app uses it for upload validation (ffprobe),
preprocessing, scene detection, audio extraction, rendering and the final QC.

1. **Easiest:** open a terminal (Win+R → `cmd`) and run:
   ```
   winget install Gyan.FFmpeg
   ```
   (or install the “gyan.dev” full build from <https://ffmpeg.org/download.html>
   and unzip it to e.g. `C:\ffmpeg`, then add `C:\ffmpeg\bin` to your PATH).
2. Open a **new** terminal and verify:
   ```
   ffmpeg -version
   ffprobe -version
   ```
   Both must print a version line. If they do not, FFmpeg’s `bin` folder is
   not on PATH:
   - Win+R → `sysdm.cpl` → Advanced → Environment Variables → under *User
     variables* edit `Path` → **New** → paste the full `bin` path
     (e.g. `C:\ffmpeg\bin`) → OK → open a **new** terminal.

> No PATH editing is needed when you set `FFMPEG_PATH` / `FFPROBE_PATH` in
> `.env` to the full exe paths instead.

---

## D. Tesseract installation (optional — OCR)

Tesseract adds OCR of on-screen text. Without it the analysis still runs;
OCR is simply reported as unavailable.

1. Install (one of):
   ```
   winget install UB-Mannheim.TesseractOCR
   ```
   or download the installer from the
   [UB-Mannheim Tesseract builds](https://github.com/UB-Mannheim/tesseract/wiki).
2. During the installer you may pick extra language data. **English is
   required**; tick **Hindi** and **Bengali** if you want OCR of those scripts.
3. Verify in a new terminal: `tesseract --version` and
   `tesseract --list-langs` (should list `eng`, and `hin`/`ben` if installed).
4. If Tesseract is not on PATH, set `TESSERACT_PATH` in `.env` to the full
   exe path (default install:
   `C:\Program Files\Tesseract-OCR\tesseract.exe`).

---

## E. Whisper model setup (optional — speech-to-text)

The app uses faster-whisper **locally** — no API key. The model is small
(`tiny` ~75 MB, `base` ~145 MB) and is downloaded **once, explicitly**:

```
scripts\download_whisper_model.bat tiny
```

Files land in `models\whisper\tiny\`. `WHISPER_MODEL` in `.env` selects the
model. Until the model exists, analysis completes but the transcript is
reported unavailable — nothing is faked.

---

## F. llama.cpp setup (required — story + script generation)

The local LLM used to write the explanation is **llama.cpp** (the command
line `llama-cli`). It runs the small GGUF model below on your CPU.

1. Install the CLI:
   ```
   winget install llama.cpp
   ```
   (or download the latest `llama.cpp` **release zip** from
   <https://github.com/ggml-org/llama.cpp/releases> and unzip it; note the
   folder that contains `llama-cli.exe`).
2. If `llama-cli` is not on PATH, set in `.env`:
   ```
   LLAMA_CPP_PATH=C:\path\to\llama-cli.exe
   ```
3. Verify: `llama-cli --version`.

See **G** for the model file and <docs/LOCAL_MODELS.md> for details.

---

## G. GGUF model setup (required)

The app needs **one small quantized GGUF** (~1 GB for the default
Qwen 1.5B Q4_K_M). Download it once, explicitly:

```
scripts\download_llm_model.bat
```

The file lands in `models\llm\`. Two ways to make the app see it:

1. **Recommended — set the path in `.env`:**
   ```
   LLAMA_MODEL_PATH=models\llm\qwen2.5-1.5b-instruct-q4_k_m.gguf
   ```
2. Or move a single `.gguf` to the **`models\` root** (the app
   auto-discovers exactly one `.gguf` there):
   ```
   move models\llm\*.gguf models\
   ```

> The app **never downloads models automatically**. Missing model → the UI
> and `scripts\check_dependencies.bat` show exact setup instructions.

---

## H. Piper setup (required — narration/TTS)

The narration voice engine is **Piper**, which runs fully offline.

1. Download the latest **piper Windows release zip** from
   <https://github.com/rhasspy/piper/releases> and unzip it (note the folder
   containing `piper.exe`). Alternatively install via pip:
   `pip install piper-tts`.
2. If `piper` is not on PATH, set in `.env`:
   ```
   TTS_EXECUTABLE_PATH=C:\path\to\piper\piper.exe
   ```
3. Verify: `piper --help`.

---

## I. English / Hindi / Bengali voice setup

One voice model per language is needed. Voices are `.onnx` files (with a
sibling `.onnx.json`), downloaded once **explicitly**:

```
scripts\setup_piper_voices.bat
```

…downloads the official English voice (`en_US-lessac-medium`) into
`models\voices\`. Hindi/Bengali are community voices on Hugging Face — run
the same script with their file path, e.g.:

```
scripts\setup_piper_voices.bat hi\hi_IN\sanman\medium\hi_IN-sanman-medium.onnx
scripts\setup_piper_voices.bat bn\bn_IN\tanmay\medium\bn_IN-tanmay-medium.onnx
```

Then point `.env` at the downloaded files:

```
TTS_VOICE_EN=models\voices\en\en_US\lessac\medium\en_US-lessac-medium.onnx
TTS_VOICE_HI=models\voices\hi\hi_IN\sanman\medium\hi_IN-sanman-medium.onnx
TTS_VOICE_BN=models\voices\bn\bn_IN\tanmay\medium\bn_IN-tanmay-medium.onnx
```

The backend reports each language as **READY / NOT CONFIGURED** on
`http://127.0.0.1:8000/api/system/status` → `tts`. The app never fakes
audio: a missing voice refuses cleanly with setup instructions.

---

## J. Subtitle font setup

English subtitles burn in with the default sans-serif font. **Hindi and
Bengali need a Unicode font** configured, or the render refuses with setup
instructions (it never burns “tofu” boxes):

```
# in .env - Windows ships Nirmala UI (Devanagari + Bengali); Noto works too
SUBTITLE_FONT_PATH=C:\Windows\Fonts\Nirmala.ttc
# or: SUBTITLE_FONT_NAME=Noto Sans Devanagari
```

---

## K. First run

1. Copy the whole `ai-video-explainer` folder somewhere on your PC
   (e.g. `C:\Users\You\ai-video-explainer`). Do not keep it inside a
   system-protected folder like `C:\Program Files`.
2. Double-click **`FIRST_RUN.bat`**. It:
   - checks Windows / Python / Node.js,
   - creates the virtual environment (`.venv`) and installs backend
     dependencies,
   - installs frontend dependencies (first time only),
   - creates `.env` (from `env.example`, **never overwrites** an existing
     `.env`),
   - creates the storage folders,
   - initializes the SQLite database (safe additive migrations),
   - verifies FFmpeg / FFprobe / Tesseract / Whisper / llama.cpp / GGUF /
     Piper / voices / subtitle font — each missing item prints
     `[MISSING]` + `[WHY]` + `[WHERE]` + `[HOW]`,
   - runs the backend tests and the frontend type check,
   - finishes with the full system check → **READY TO RUN** or a fix list.
3. Fix any reported missing required component (details above / in
   `docs\WINDOWS_TROUBLESHOOTING.md` / `docs\LOCAL_MODELS.md`), then run
   `FIRST_RUN.bat` again — it is safe to repeat and never deletes data.

---

## L. Starting the application

Double-click **`START_AI_VIDEO_EXPLAINER.bat`**:

1. Dependency check (stops safely with WHAT/WHY/HOW if something required
   is missing).
2. Starts the backend (own console window, output logged to
   `logs\launch.log`) and waits until `http://127.0.0.1:8000/api/health`
   reports OK.
3. Starts the frontend and waits until `http://127.0.0.1:5173` serves the
   app.
4. Opens your default browser at `http://127.0.0.1:5173`.

Already-running instances are detected and reused (no duplicate processes).
Ports occupied by *another* program produce a clear error instead of killing
it.

Then run the pipeline in the browser:

```
Upload Video → Prepare → Analyze → Generate Explanation
→ Generate Narration → Create Final Video
```

(Choose English/Hindi/Bengali and 2/3/4 minutes.)

Alternatives (from a terminal, in the foreground):

```
scripts\run_backend.bat     :: backend console, Ctrl+C to stop
scripts\run_frontend.bat    :: frontend console, Ctrl+C to stop
```

Useful endpoints:

| URL | What it shows |
| --- | ------------- |
| `http://127.0.0.1:5173` | The app UI |
| `http://127.0.0.1:8000/docs` | Interactive API docs |
| `http://127.0.0.1:8000/api/health` | Liveness |
| `http://127.0.0.1:8000/api/system/status` | Full readiness (FFmpeg, LLM, TTS, voices, render) |
| `http://127.0.0.1:8000/api/system/preflight?language=en&target_duration_seconds=120` | Pre-flight check |

---

## M. Stopping the application

Double-click **`STOP_AI_VIDEO_EXPLAINER.bat`** — it stops exactly the two
console windows the launcher started (`AVE-Backend`, `AVE-Frontend`) and
never touches other Python/Node programs or your data. A restart script
(`RESTART_AI_VIDEO_EXPLAINER.bat`) stops, waits, and starts again.

---

## N. Updating the application

1. Back up your data first (`scripts\backup_data.bat`).
2. Copy the new `ai-video-explainer` folder **next to** your old one
   (or replace the code files, keeping `data\`, `models\`, `.env`, `logs\`).
3. Run `FIRST_RUN.bat` in the new folder — your old `data\projects`,
   `data\explainer.db` and `.env` are preserved (setup never deletes them).

---

## O. Backup

`scripts\backup_data.bat` creates a ZIP (next to the project) containing:

- `data\projects\` — your uploads, analysis, narration and final MP4s,
- `data\explainer.db` — the project database,
- `env.example.txt` + a configuration-notes file.

It never bundles `.venv`, `node_modules`, model files, logs or your live
`.env`. To restore, unzip and copy `projects\` → `data\projects\` and
`explainer.db` → `data\explainer.db`.

See <LOCAL_MODELS.md> for the model directory layout and
<WINDOWS_TROUBLESHOOTING.md> when something does not work.
