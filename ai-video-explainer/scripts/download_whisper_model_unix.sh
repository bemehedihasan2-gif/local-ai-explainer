#!/usr/bin/env bash
# One-time Whisper model download for Phase 4 speech-to-text (macOS/Linux).
# Explicit download only - the app never downloads models silently.
# Usage: scripts/download_whisper_model_unix.sh [tiny|base]   (default: tiny)
set -euo pipefail
cd "$(dirname "$0")/.."

MODEL="${1:-tiny}"
if [[ "$MODEL" != "tiny" && "$MODEL" != "base" ]]; then
    echo "[ERROR] Model must be \"tiny\" or \"base\" (got \"$MODEL\")." >&2
    exit 1
fi

echo "Downloading faster-whisper \"$MODEL\" into models/whisper/$MODEL/ ..."
echo "This is a one-time download (no API key). tiny ~= 75 MB, base ~= 145 MB."

python3 -m pip install huggingface_hub

python3 -c "from huggingface_hub import snapshot_download; snapshot_download(repo_id='Systran/faster-whisper-$MODEL', local_dir='models/whisper/$MODEL')"

echo
echo "Done. Restart the backend and re-run Analyze; speech-to-text will now run."
echo "(WHISPER_MODEL=$MODEL in .env selects which model is used.)"