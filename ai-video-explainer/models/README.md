# Local model files

Large AI model files are stored here, **outside the source tree** (this
folder is git-ignored except for this file). The app never downloads models
silently — run the explicit helper scripts once, then wire them in `.env`:

```
models\
├── whisper\<model>\   faster-whisper snapshot (scripts\download_whisper_model.bat)
│                       e.g. models\whisper\tiny\model.bin + config.json
├── llm\                staging folder for downloaded GGUFs
│                       (scripts\download_llm_model.bat → models\llm\<file>.gguf)
└── voices\             Piper voices, per language
                        (scripts\setup_piper_voices.bat → models\voices\...)
```

`.env` wiring:

- Whisper: `WHISPER_MODEL=tiny` (auto-looks in `models\whisper\<model>\`).
- LLM: set `LLAMA_MODEL_PATH=models\llm\<file>.gguf`, or put exactly one
  `*.gguf` directly in `models\` (auto-discovery).
- Piper: `TTS_VOICE_EN` / `TTS_VOICE_HI` / `TTS_VOICE_BN` point at the
  downloaded `.onnx` files.

Full instructions: see `docs/LOCAL_MODELS.md` and `docs/WINDOWS_SETUP.md`.
