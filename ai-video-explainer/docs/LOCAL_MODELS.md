# Local Models — layout, download & `.env` wiring

The app runs **fully locally** with these components. Nothing is ever
downloaded silently, nothing is stored in the source tree, and no API keys
exist. You download each model once (explicitly) and point `.env` at it.

## Directory layout

```
ai-video-explainer\
├── models\                  <- all model files live here (gitignored)
│   ├── whisper\
│   │   └── tiny\            <- faster-whisper "tiny" snapshot
│   │       ├── model.bin
│   │       └── config.json
│   ├── llm\                 <- optional staging folder for downloaded GGUFs
│   │   └── qwen2.5-1.5b-instruct-q4_k_m.gguf
│   └── voices\              <- Piper voices (any sub-path works)
│       └── en\en_US\lessac\medium\
│           ├── en_US-lessac-medium.onnx
│           └── en_US-lessac-medium.onnx.json
```

> The **backend** auto-discovers exactly **one** `*.gguf` directly inside
> `models\` (not `models\llm\`). Files downloaded by the helper scripts land
> in `models\llm\` — so either set `LLAMA_MODEL_PATH` (recommended) or move
> the file to `models\`. Whisper snapshots and Piper voices are always found
> through `.env` / configured settings.

## 1. Whisper (speech-to-text, OPTIONAL)

| Size | Use |
| ---- | --- |
| `tiny` (~75 MB) | default — recommended on 8 GB RAM |
| `base` (~145 MB) | better accuracy, still CPU-friendly |

Download once:

```
scripts\download_whisper_model.bat tiny
```

`.env` (already defaulted):

```
WHISPER_MODEL=tiny
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
```

Ready check: `models\whisper\tiny\model.bin` and `config.json` exist.
Missing → analysis still runs; the transcript is honestly reported as
unavailable.

## 2. llama.cpp CLI + GGUF (story + script, REQUIRED)

Two parts:

1. **CLI** — `winget install llama.cpp` (or the official GitHub release zip;
   note the folder with `llama-cli.exe`). `.env`: `LLAMA_CPP_PATH=` when the
   binary is not on PATH.
2. **Model** — one small quantized instruct GGUF. Recommended for 8 GB RAM:
   a ~1–3B **Q4_K_M** (the default `Qwen2.5-1.5B-Instruct-GGUF`, `~1 GB`).

Download once:

```
scripts\download_llm_model.bat
:: downloads models\llm\qwen2.5-1.5b-instruct-q4_k_m.gguf
```

`.env` — set the path (recommended) or auto-discover:

```
# Option A (recommended): explicit path
LLAMA_MODEL_PATH=models\llm\qwen2.5-1.5b-instruct-q4_k_m.gguf
# Option B: move ONE .gguf to models\ and leave LLAMA_MODEL_PATH empty
LLAMA_CPP_PATH=C:\llama.cpp\build\bin\Release\llama-cli.exe   # optional
LLAMA_THREADS=4
LLAMA_CONTEXT_SIZE=2048
LLAMA_MAX_TOKENS=1024
```

Memory notes for the Ryzen 3 3200G + 8 GB:

- Only **one** heavy model is in RAM at a time — the llama.cpp CLI process
  is spawned per generation and exits afterwards.
- Keep `LLAMA_THREADS=4` (4 physical cores) — more threads do not speed up
  a 4-core CPU and steal cycles from everything else.
- `LLAMA_CONTEXT_SIZE=2048` keeps the context small; the app never dumps a
  full transcript into a prompt (per-scene excerpts are capped).

## 3. Piper voices (narration, REQUIRED per language)

Piper needs a voice **per language**. The official English voice plus
community Hindi/Bengali voices on Hugging Face:

```
scripts\setup_piper_voices.bat
scripts\setup_piper_voices.bat hi\hi_IN\sanman\medium\hi_IN-sanman-medium.onnx
scripts\setup_piper_voices.bat bn\bn_IN\tanmay\medium\bn_IN-tanmay-medium.onnx
```

`.env` — each voice must point at an existing `.onnx` (Piper reads the
sibling `.onnx.json` automatically):

```
TTS_EXECUTABLE_PATH=C:\piper\piper.exe      # optional - PATH auto-discovery
TTS_VOICE_EN=models\voices\en\en_US\lessac\medium\en_US-lessac-medium.onnx
TTS_VOICE_HI=models\voices\hi\...\....onnx
TTS_VOICE_BN=models\voices\bn\...\....onnx
```

Voice readiness is reported per language on
`http://127.0.0.1:8000/api/system/status` → `tts` → `languages`
(READY / NOT CONFIGURED). A missing voice refuses narration for that
language — never fake audio. Piper itself is tiny; one voice (~60–100 MB)
loads per synthesis call and is released after each segment.

## 4. Tesseract language data (OCR, OPTIONAL)

Tesseract reads scene frames. English (`eng`) always; `hin`/`ben` when you
want OCR of Hindi/Bengali text (analysis never requires OCR). Installed
through the UB-Mannheim installer (tick the languages), or place the
`tessdata` files in Tesseract’s `tessdata` folder.

## 5. Subtitle font (Hindi/Bengali burn-in)

Not a model — Windows ships **Nirmala UI** (`C:\Windows\Fonts\Nirmala.ttc`),
which covers Latin, Devanagari and Bengali:

```
SUBTITLE_FONT_PATH=C:\Windows\Fonts\Nirmala.ttc
```

or `SUBTITLE_FONT_NAME=Noto Sans Devanagari` with a Noto font installed.
Without one of these, Hindi/Bengali burn-in refuses with setup instructions
(never tofu boxes). English burn-in uses the default sans font.

---

## Disk / memory budget on 8 GB

| Item | Approx. size | When loaded |
| ---- | ------------ | ----------- |
| Whisper `tiny` int8 | ~75 MB disk, ~500 MB RAM while transcribing | analysis stage only |
| GGUF 1.5B Q4_K_M | ~1 GB disk, ~1.5–2 GB RAM per generation | story/script stage only (process exits after each call) |
| Piper voice | ~60–100 MB disk, small RAM | one voice per segment, released each call |
| FFmpeg passes | streaming (never the whole video in RAM) | preprocessing / analysis / render |

`PROCESSING_CONCURRENCY=1` guarantees only one heavy job runs at a time.
FFprobe/FFmpeg and all subprocesses use argument arrays (`shell=False`),
stream to disk, and enforce timeouts.
