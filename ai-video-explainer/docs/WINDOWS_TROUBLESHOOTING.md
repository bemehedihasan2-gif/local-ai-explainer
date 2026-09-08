# Windows Troubleshooting — Local AI Video Explainer

Every error the setup/launcher scripts print follows the same shape:

```
[WHAT] what happened
[WHY]  why it matters
[HOW]  how to fix it
```

Start with the quickest diagnostic:

```
scripts\check_dependencies.bat      :: PASS/WARN/ERROR report + exit code
scripts\diagnose_windows.bat        :: verbose Phase 8 diagnostic
scripts\health_check.bat            :: live check against the running app
```

Log files (no secrets are ever written to them):

| File | Content |
| ---- | ------- |
| `logs\setup.log`     | Setup summaries (`FIRST_RUN.bat` / `setup_windows_full.bat`) |
| `logs\launch.log`    | Backend + frontend console output while running |
| `logs\diagnostic.log`| Dependency-check summaries |
| `logs\errors.log`    | Backend error details (auto-rotating) |

---

## Setup / launcher errors

| Message | What it means | Fix |
| ------- | ------------- | --- |
| `Python not found on PATH` | Python not installed, or installed without PATH | Install from python.org and tick **Add python.exe to PATH**; open a **new** terminal |
| `Python is too old` | Below 3.10 | Install 3.10+ (64-bit) |
| `pip is not available` | Python install lacks pip | Reinstall Python with pip enabled |
| `.venv` missing | First-time setup has not finished | Run `FIRST_RUN.bat` |
| `Node.js not found / too old` | Node absent or < 18 | Install the LTS from nodejs.org; new terminal |
| `FFmpeg not found` | ffmpeg/ffprobe not on PATH | `winget install Gyan.FFmpeg`, add `bin` to PATH (new terminal), or set `FFMPEG_PATH`/`FFPROBE_PATH` in `.env` |
| `llama.cpp (llama-cli) not found` | Local LLM CLI missing | `winget install llama.cpp` or the GitHub release; set `LLAMA_CPP_PATH` |
| `No GGUF model found` | Model file missing | `scripts\download_llm_model.bat` (~1 GB, one time), then set `LLAMA_MODEL_PATH` (see docs\LOCAL_MODELS.md) |
| `Multiple .gguf files in models\` | Auto-discovery needs exactly one | Set `LLAMA_MODEL_PATH=models\<chosen>.gguf` in `.env` |
| `GGUF found in models\llm\ but LLAMA_MODEL_PATH is empty` | File downloaded but not configured | Set `LLAMA_MODEL_PATH=models\llm\<file>.gguf` in `.env` |
| `Piper not found` | TTS engine missing | Install Piper (release zip or `pip install piper-tts`); set `TTS_EXECUTABLE_PATH` |
| `voice - NOT CONFIGURED` | No `.onnx` voice for that language | `scripts\setup_piper_voices.bat` + set `TTS_VOICE_<LANG>` in `.env` |
| `Port 8000 occupied by ANOTHER program` | Something else uses 8000 | Stop that program **or** change `BACKEND_PORT` in `.env` (the frontend proxy target is fixed to 8000 in `frontend\vite.config.ts`) |
| `Port 5173 occupied by ANOTHER program` | Something else uses 5173 | Stop that program or change the port in `frontend\vite.config.ts` |
| `Backend did not become healthy within 90 seconds` | Backend crashed on startup | Read `logs\launch.log` and `logs\errors.log`; fix the underlying error, re-run |
| `Frontend did not become ready within 90 seconds` | Vite crashed on startup | Read `logs\launch.log`; fix the underlying error, re-run |
| `Insufficient disk space` | < 1 GB free on the project drive | Free disk space and re-run |
| `Could not initialize database` | SQLite init failed | Check permissions on `data\`; `data\explainer.db` is auto-created — do not hand-edit it |
| `X was unexpected at this time.` during setup (e.g. `. was unexpected at this time.`) | A `.bat`/`.cmd` file has Unix (LF) line endings — cmd.exe misparses multi-line blocks in LF-only files | Re-save the file as CRLF (VS Code: bottom-right `LF` → `CRLF`), or re-copy from the repo — `.gitattributes` now enforces CRLF on checkout |
| `.env could not be loaded` | An invalid value in `.env` | Run `cd backend` then `..\.venv\Scripts\python.exe -c "from app.config import Settings; Settings()"` — the traceback names the bad setting |

---

## In-app pipeline errors

| Symptom | Fix |
| ------- | --- |
| “FFmpeg not detected” / `ffmpeg_unavailable` | Install FFmpeg, restart the backend, reload the UI |
| `llm_unavailable` | Install llama.cpp / set `LLAMA_CPP_PATH`; restart the backend |
| `model_download_required` | `scripts\download_llm_model.bat` and/or set `LLAMA_MODEL_PATH` |
| `voice_unavailable` for a language | Configure that language’s `TTS_VOICE_<LANG>` in `.env` |
| Hindi/Bengali burn-in “needs a Unicode font” | Set `SUBTITLE_FONT_PATH` (e.g. `C:\Windows\Fonts\Nirmala.ttc`) or `SUBTITLE_FONT_NAME` |
| Subtitles appear as boxes in the final video | The configured font lacks Devanagari/Bengali glyphs — use Nirmala UI or a Noto font |
| Render very slow | Expected on CPU; lower `OUTPUT_MAX_WIDTH/HEIGHT` or raise `VIDEO_CRF`; retry is safe |
| Render fails / `render_failed` | Check `logs\errors.log`; Phases 1–6 are kept — click **Create final video** again |
| Project stuck at a stage after restart | The in-process worker lost its job; re-run the stage (stale runs auto-recover) |
| Preflight `ok=false` | Run `scripts\check_dependencies.bat` and fix each `[ERROR]` row |

---

## Model-file reference

| Component | Expected location | Proves ready |
| --------- | ----------------- | ------------ |
| Whisper | `models\whisper\<model>\` | `model.bin` + `config.json` exist |
| LLM (GGUF) | one `models\*.gguf`, **or** any file referenced by `LLAMA_MODEL_PATH` | the file exists |
| Piper voices | anywhere referenced by `TTS_VOICE_<LANG>` | the `.onnx` exists (sibling `.onnx.json` next to it) |

The app never downloads models/voices silently. If one is missing, its
stage refuses with setup instructions — nothing is faked.

---

## Windows-specific notes

- **“Add python.exe to PATH” was not ticked** — the classic cause of
  “Python not found”. Re-run the installer → *Modify* → tick PATH.
- **Opened a terminal before installing a tool** — PATH changes apply only
  to **new** terminals/windows.
- **Defender/SmartScreen** may warn on first launch of downloaded zips —
  choose *Run anyway* only for files you downloaded from the official
  sources listed in <WINDOWS_SETUP.md>.
- **Firewall prompt** — the app binds only to `127.0.0.1` (loopback), so
  no firewall rule is needed; deny the prompt if one appears.
- **Path too long / folder in OneDrive** — keep the project on a local
  drive with a short path (e.g. `C:\ave`) if you hit odd permission errors.
