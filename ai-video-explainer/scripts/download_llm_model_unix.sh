#!/usr/bin/env bash
# One-time local LLM (GGUF) download for Phase 5 story + script generation.
# Explicit download only - the app never downloads models silently.
# Usage: scripts/download_llm_model_unix.sh [repo] [gguf-name]  (defaults below)
# Default: a ~1.5B Q4_K_M instruct model (~1 GB) - comfortable on 8 GB RAM.
set -euo pipefail
cd "$(dirname "$0")/.."

REPO="${1:-Qwen/Qwen2.5-1.5B-Instruct-GGUF}"
FILE="${2:-qwen2.5-1.5b-instruct-q4_k_m.gguf}"

echo "Downloading \"$FILE\" from huggingface.co/$REPO into models/llm/ ..."
echo "This is a one-time download (no API key). Q4_K_M 1.5B ~= 1 GB."
echo "The app never downloads models automatically."

python3 -m pip install huggingface_hub

python3 -c "from huggingface_hub import hf_hub_download; hf_hub_download(repo_id='$REPO', filename='$FILE', local_dir='models/llm')"

echo
echo "Done. Also install the llama.cpp CLI (llama-cli) if you do not have it"
echo "(e.g. 'brew install llama.cpp' or the official GitHub release)."
echo "Then set in .env:"
echo "  LLAMA_CPP_PATH=/path/to/llama-cli"
echo "  LLAMA_MODEL_PATH=models/llm/$FILE"
echo "(or leave both empty: the app auto-discovers llama-cli on PATH and a"
echo " single .gguf inside models/ )"
echo "Restart the backend, then Generate explanation will work."