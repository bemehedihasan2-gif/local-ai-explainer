# Local model files

Later phases will download local (free, offline) models into this folder, one
subfolder per model family, for example:

```
models/
├── whisper-small/     # speech-to-text
├── ocr/               # OCR engine models
└── tts/               # neural TTS voice
```

Rules that keep this app usable on an 8 GB RAM machine:

- Always prefer the smallest quantized model that still works for the task.
- CPU-only inference (no GPU available on the target hardware).
- Load one model at a time, and unload it between pipeline stages.
- Keep a note of each model's RAM footprint in this folder's docs.

Nothing is downloaded in Phase 1. This folder intentionally stays empty.
