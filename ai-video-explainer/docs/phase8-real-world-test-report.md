# Phase 8 — Real-World End-to-End Test Report

**Status: PARTIAL** — the real-hardware acceptance run cannot be executed in
this sandbox (no Windows, no FFmpeg/Tesseract/Piper/llama.cpp, no models).
Everything that **can** be verified without the user's PC was verified and is
recorded below with exact numbers. Real-media tests are marked `NOT RUN` with
the exact dependency that must be present on the user's machine. Nothing here
is fabricated.

> Honesty rule (Phase 8 §4): a real-world test only counts as PASS when it
> actually ran on real local hardware. These rows stay `NOT RUN` until the
> acceptance procedure at the end is executed on the Windows PC.

---

## 1. Environment

### This sandbox (where this report was produced)

| Item      | Value |
| --------- | ----- |
| OS        | Linux container (x86_64), not the target machine |
| CPU       | AMD EPYC 9254 (sandbox host — **not** the Ryzen 3 3200G target) |
| RAM       | 377 GB (sandbox host — **not** the 8 GB target) |
| Python    | 3.11.2 |
| FFmpeg    | **NOT installed** → all real-media tests skip by design |
| FFprobe   | **NOT installed** |
| Tesseract | **NOT installed** |
| Piper     | **NOT installed** |
| llama.cpp | **NOT installed** |
| Whisper   | faster-whisper package installed; **no model files** |
| GGUF      | **none** |

The suite's real-media tests (`test_upload_real_media.py`,
`test_preprocess_real.py`, `test_analysis_real.py`) detect the missing
binaries and skip gracefully (7 skips), exactly as designed for machines
without FFmpeg.

### Target machine (Phase 8 acceptance must run here)

| Item       | Expected |
| ---------- | -------- |
| OS         | Windows 10/11 |
| CPU        | AMD Ryzen 3 3200G (4 cores) |
| RAM        | 8 GB, CPU-only, integrated graphics |
| Python     | 3.10+ |
| FFmpeg     | 6.x+ on PATH |
| Tesseract  | optional (OCR) |
| Piper      | required for narration |
| llama.cpp  | required for story + script |
| Whisper    | optional (speech analysis) |

---

## 2. What was verified in this session (real executions, exact numbers)

| # | Check | Result | Evidence |
| - | ----- | ------ | -------- |
| 1 | Full backend regression suite | **PASS** | `227 passed, 7 skipped` (includes 8 new Phase 8 tests) |
| 2 | Frontend typecheck | **PASS** | `bun tsc -b --noEmit` exit 0 |
| 3 | Health + system-status endpoints | **PASS** | 8/8 health tests green |
| 4 | New `GET /api/system/preflight` endpoint | **PASS** | Returns machine-readable checks; honestly reports `ok=false` + `blocking=["ffmpeg", …]` when FFmpeg is absent (never a fake PASS); 422 on unknown language |
| 5 | Machine-readable `dependencies` block in `/api/system/status` | **PASS** | Flat booleans `{python, ffmpeg, ffprobe, tesseract, whisper_model, llm, piper, voices{en,hi,bn}}`; no keys/paths |
| 6 | Diagnostic script (Unix variant) | **PASS** | `scripts/diagnose_unix.sh` runs end-to-end; exit 1 and names exactly the 4 missing required components (ffmpeg, ffprobe, piper, llama-cli) + 9 optional warnings. No false positives. |
| 7 | Security scan: no `shell=True`, no `os.system`, no `eval(` | **PASS** | grep across `backend/app` — only docstring mentions; every subprocess call uses argument arrays with `CREATE_NO_WINDOW` on Windows |
| 8 | Secret scan (frontend + backend API layer) | **PASS** | No API keys/tokens/passwords in source or API responses; LLM `model_name` and TTS `voice_id` are basenames only |
| 9 | Absolute internal paths redacted from `/api/system/status` | **PASS** | `database.path` = filename only; storage dirs = relative; ffmpeg paths = basename/null; regression test added |
| 10 | Performance report writer (`analysis/performance/performance_report.json`) | **PASS** | 3/3 unit tests; aggregates only persisted DB timestamps; explicitly does **not** fabricate peak-RAM values |
| 11 | Duplicate-upload, path-traversal, stale-cleanup guards | **PASS** | Existing `test_security_and_stubs.py` green |
| 12 | Crash recovery: stale `rendering` recovery path | **PASS** | Existing `test_render_api.py` green (worker failure → `render_failed`, retry → `COMPLETED`, idempotent reuse) |

### Regression suite breakdown (run on Linux sandbox)

- `224 → 227 passed` (3 new performance tests added after the first full run),
  `7 skipped` (real-FFmpeg integration tests, by design), `0 failed`.
- Skipped tests: `test_upload_real_media*`, `test_preprocess_real*`,
  `test_analysis_real*` — all skip because **FFmpeg is not installed here**.
  They run on the user's Windows PC after `setup_windows.bat`.

---

## 3. Test matrix (Phase 8 §9)

| Test | Language | Duration | Result |
| ---- | -------- | -------: | ------ |
| A    | English  | 2 min   | **NOT RUN** — needs Windows + FFmpeg + Piper voice + GGUF |
| B    | Bengali  | 2 min   | **NOT RUN** — needs Windows + FFmpeg + bn Piper voice + GGUF |
| C    | Hindi    | 2 min   | **NOT RUN** — needs Windows + FFmpeg + hi Piper voice + GGUF |
| D    | English  | 3 min   | **NOT RUN** — needs Windows + FFmpeg + Piper voice + GGUF |
| E    | Bengali  | 3 min   | **NOT RUN** — needs Windows + FFmpeg + bn Piper voice + GGUF |
| F    | Hindi    | 3 min   | **NOT RUN** — needs Windows + FFmpeg + hi Piper voice + GGUF |
| G    | English  | 4 min   | **NOT RUN** — needs Windows + FFmpeg + Piper voice + GGUF |

Every pipeline stage that these tests would exercise (upload → preprocess →
analysis → story → script → TTS → subtitles → narration → render → QC) is
covered **deterministically** by the automated suite with scripted fake
ffmpeg/ffprobe binaries and fake LLM/TTS/render services (Phases 1–7 tests,
all green). What is *not* covered by automation is the real media + real
binary behavior on the target hardware — that is exactly what the acceptance
procedure below runs.

---

## 4. Performance table (Phase 8 §29)

**No real-hardware performance numbers exist yet** — they would be fabricated.
The infrastructure to record them honestly is now in place:

- Every pipeline job already persists `started_at`/`completed_at` in SQLite;
  analysis runs store `processing_seconds`; narration and render runs store
  their own timestamps plus output stats.
- When a render completes, the worker now writes
  `data/projects/<id>/analysis/performance/performance_report.json`
  aggregating those real timings (wall-clock seconds per stage, totals,
  narration/render stats). It explicitly does **not** invent peak-RAM values —
  `measurement_notes` tells the operator to record those with Task Manager on
  the target machine.

| Stage | Input | Time | Peak RAM | Result |
| ----- | ----- | ---: | -------: | ------ |
| upload | — | — | — | NOT RUN (real) / PASS (automated lifecycle) |
| preprocessing | — | — | — | NOT RUN (real) / PASS (automated lifecycle) |
| analysis | — | — | — | NOT RUN (real) / PASS (automated lifecycle) |
| story/LLM | — | — | — | NOT RUN (real) / PASS (automated with fake provider) |
| TTS | — | — | — | NOT RUN (real) / PASS (automated with fake provider) |
| render | — | — | — | NOT RUN (real) / PASS (automated with fake renderer) |
| final QC | — | — | — | NOT RUN (real) / PASS (deterministic QC unit tests) |

---

## 5. Final render (Phase 8 §23–24)

**NOT RUN.** The final MP4 can only be produced on a machine with real
FFmpeg + the local AI components. The render pipeline itself is fully
implemented and tested deterministically: render plan, clip extraction,
original-audio slicing, narration+ducking mix, libass burn-in, CPU-first
libx264 encode, FFprobe re-check, decode spot-checks and the 0–100 QC score
(see `test_render_plan.py`, `test_final_qc.py`, `test_render_api.py`).

---

## 6. Problems found & fixes

| # | Problem | Fix | Regression test |
| - | ------- | --- | --------------- |
| 1 | `/api/system/status` exposed absolute machine paths (`database.path`, storage dirs, ffmpeg paths) | Redacted: basename/relative only; the localhost UI never rendered them | `test_system_status_never_exposes_absolute_paths` |
| 2 | No pre-flight way to stop before expensive processing when a component is missing | New `GET /api/system/preflight?language=&target_duration_seconds=` with per-check `ok/required/detail/setup_hint`; optional components never block | 4 new preflight tests |
| 3 | No machine-readable flat dependency status | New `dependencies` block in `/api/system/status` | `test_system_status_dependencies_block_is_machine_readable` |
| 4 | No Windows environment diagnostic script | New `scripts/diagnose_windows.bat` (+ `diagnose_unix.sh`, verified running) | Verified by execution in this sandbox |
| 5 | No performance artifact for §29 | Worker writes `analysis/performance/performance_report.json` at render completion (best-effort, never fails a job) | 3 new `test_performance_report.py` tests |

No crashes, no incorrect-output, no sync, no subtitle, no memory-safety bugs
were *found* in this session — the deterministic suite covers those paths and
is green. Real-media-only defects (if any) can only surface during the
acceptance run on the target PC; any discovered there must be reported back
with logs (`logs/errors.log`, per-stage run rows) and fixed with regression
tests per Phase 8 §53.

---

## 7. Remaining limitations (honest)

1. **No real-hardware verification yet.** Everything media-related is
   `NOT RUN` until the acceptance procedure below executes on the Windows PC.
2. **Peak RAM is operator-measured** — the app cannot measure itself;
   the performance report records wall-clock timings only.
3. **Hindi/Bengali voices and OCR packs may be missing** on the target —
   the app fails honestly with setup instructions, never fake audio/OCR.
4. **Subtitle burn-in for Hindi/Bengali requires a Unicode font**
   (`SUBTITLE_FONT_PATH`/`SUBTITLE_FONT_NAME`) — the renderer refuses rather
   than rendering tofu boxes.
5. **Whisper/OCR are optional** — without them speech/OCR evidence is
   skipped and the explanation is built from the remaining evidence.

---

## 8. Windows acceptance procedure (run on the target PC)

```bat
:: 1. Environment check — must report no errors
scripts\diagnose_windows.bat

:: 2. Pre-flight API check (while the backend runs)
curl "http://127.0.0.1:8000/api/system/preflight?language=en&target_duration_seconds=120"

:: 3. If anything is missing: run the setup scripts (explicit, no auto-download)
scripts\setup_windows.bat
scripts\download_whisper_model.bat tiny        :: optional
scripts\download_llm_model.bat                 :: required for Generate
scripts\setup_piper_voices.bat                 :: English; rerun for hi/bn
:: set TTS_VOICE_EN/HI/BN, LLAMA_CPP_PATH, LLAMA_MODEL_PATH,
:: SUBTITLE_FONT_PATH in .env; restart the backend

:: 4. Automated suite (real-media tests run now instead of skipping)
.venv\Scripts\python -m pytest backend\tests -q
```

Then, in the browser UI (`http://127.0.0.1:5173`) for **Test A**:

1. Upload a 5–10 minute narrative/general video (drag & drop or picker).
2. Confirm the metadata panel matches FFprobe output.
3. **Prepare for analysis** → wait for real progress → **Analyze**.
4. Inspect scene thumbnails + timeline; run **Generate explanation**
   (English, 2 min) → script + QC visible.
5. **Generate narration** → segment WAVs + narration.wav + SRT/VTT.
6. **Create final video** → real progress → final MP4.
7. Play the MP4 (Windows Media Player / VLC): narration present, subtitles
   burned in, audio/video in sync, no black frames.
8. Repeat for Bengali and Hindi at 2/3/4 minutes as voices allow (matrix
   rows B–G), then record timings from the performance report
   (`data/projects/<id>/analysis/performance/performance_report.json`).

Failure injection (Phase 8 §33): remove/rename the FFmpeg binary and start a
render → expect a clear setup error; break `TTS_VOICE_EN` → narration fails
with setup guidance; remove `SUBTITLE_FONT_PATH` for hi/bn → render refuses
before encoding; kill the backend mid-render → restart → **Create final
video** retries from NARRATION_READY (stale-recovery verified in tests).

When the acceptance run completes, fill in the Environment table in §1,
flip each `NOT RUN` to `PASS`/`FAIL` with measured numbers, append any
problems found + fixes, and update this report.