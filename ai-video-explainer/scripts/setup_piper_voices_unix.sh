#!/usr/bin/env bash
# One-time Piper voice download (macOS/Linux) for Phase 6 local narration.
# Explicit download only - the app never downloads voices silently.
#
# Usage:
#   scripts/setup_piper_voices_unix.sh                    # English voice (default)
#   scripts/setup_piper_voices_unix.sh hi/hi_IN/xxx/medium/hi_IN-xxx-medium.onnx
#       ^ pass the HF file path of another voice (e.g. Hindi/Bengali community
#         voices) to fetch it instead/additionally.
#
# Voice files come from the official rhasspy/piper-voices repo on Hugging Face.
set -euo pipefail
cd "$(dirname "$0")/.."

VOICE_FILE="${1:-en/en_US/lessac/medium/en_US-lessac-medium.onnx}"
LANG_DIR="$(echo "$VOICE_FILE" | cut -d/ -f1-2)"

echo "Downloading Piper voice \"$VOICE_FILE\" into models/voices/$LANG_DIR/ ..."
echo "This is a one-time download (no API key). The app never downloads voices automatically."
echo

python3 -m pip install --quiet huggingface_hub

python3 -c "from huggingface_hub import hf_hub_download; hf_hub_download(repo_id='rhasspy/piper-voices', filename='$VOICE_FILE', local_dir='models/voices')"

echo
echo "Done. The .onnx.json config file sits next to the .onnx automatically."
echo "Point the matching voice setting in .env at the downloaded file, e.g.:"
echo "  TTS_VOICE_EN=models/voices/$VOICE_FILE"
echo
echo "Hindi and Bengali: many community Piper voices exist on Hugging Face."
echo "Find a voice id (e.g. hi_IN-...-medium / bn_IN-...-medium), then run this"
echo "script again with its file path, e.g.:"
echo "  scripts/setup_piper_voices_unix.sh hi/hi_IN/sanman/medium/hi_IN-sanman-medium.onnx"
echo "  scripts/setup_piper_voices_unix.sh bn/bn_IN/tanmay/medium/bn_IN-tanmay-medium.onnx"
echo "and set TTS_VOICE_HI / TTS_VOICE_BN in .env to the downloaded files."
echo
echo "Also make sure the Piper CLI itself is installed and on PATH, or set"
echo "TTS_EXECUTABLE_PATH in .env. Restart the backend afterwards; the System"
echo "status endpoint then reports the configured voices."
