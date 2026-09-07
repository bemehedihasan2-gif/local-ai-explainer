#!/usr/bin/env bash
# Phase 8 diagnostic - Local AI Video Explainer (Unix/macOS/Linux/WSL).
# Mirrors scripts/diagnose_windows.bat. Never prints secrets or API keys.
# Usage: scripts/diagnose_unix.sh   (exit 0 = ready, 1 = errors found)
set -uo pipefail
cd "$(dirname "$0")/.."

ERRORS=0
WARNINGS=0

echo "============================================================"
echo " Local AI Video Explainer - environment diagnostic"
echo " $(date)"
echo "============================================================"
echo

# --- Python ------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
    echo "[ERROR] python3 not found on PATH."
    ERRORS=$((ERRORS + 1))
else
    python3 --version
    if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null; then
        echo "[OK] Python 3.10+ is supported."
    else
        echo "[ERROR] Python 3.10+ is required."
        ERRORS=$((ERRORS + 1))
    fi
fi

# --- pip / venv ---------------------------------------------------
if python3 -m pip --version >/dev/null 2>&1; then
    echo "[OK] pip is available."
else
    echo "[ERROR] pip is not available."
    ERRORS=$((ERRORS + 1))
fi
if [ -x ".venv/bin/python" ]; then
    echo "[OK] Virtual environment found (.venv)."
else
    echo "[WARN] .venv not found - run scripts/setup_unix.sh first."
    WARNINGS=$((WARNINGS + 1))
fi

# --- .env ---------------------------------------------------------
if [ ! -f ".env" ]; then
    echo "[WARN] .env not found - copy env.example to .env and review settings."
    WARNINGS=$((WARNINGS + 1))
fi

# Read an optional key from .env (prints nothing when absent).
env_val() {
    [ -f ".env" ] || return 0
    grep -E "^$1=" .env | head -n1 | cut -d= -f2-
}

# --- FFmpeg / FFprobe ---------------------------------------------
FFMPEG_BIN="$(env_val FFMPEG_PATH)"
FFPROBE_BIN="$(env_val FFPROBE_PATH)"
FFMPEG_BIN="${FFMPEG_BIN:-ffmpeg}"
FFPROBE_BIN="${FFPROBE_BIN:-ffprobe}"
if command -v "$FFMPEG_BIN" >/dev/null 2>&1; then
    echo "[OK] FFmpeg $("$FFMPEG_BIN" -version 2>/dev/null | head -n1 | awk '{print $3}')"
else
    echo "[ERROR] FFmpeg not found ($FFMPEG_BIN). Install it or set FFMPEG_PATH in .env"
    ERRORS=$((ERRORS + 1))
fi
if command -v "$FFPROBE_BIN" >/dev/null 2>&1; then
    echo "[OK] FFprobe $("$FFPROBE_BIN" -version 2>/dev/null | head -n1 | awk '{print $3}')"
else
    echo "[ERROR] FFprobe not found ($FFPROBE_BIN). Install it or set FFPROBE_PATH in .env"
    ERRORS=$((ERRORS + 1))
fi

# --- Tesseract (optional) -----------------------------------------
TESS_BIN="$(env_val TESSERACT_PATH)"
TESS_BIN="${TESS_BIN:-tesseract}"
if command -v "$TESS_BIN" >/dev/null 2>&1; then
    echo "[OK] Tesseract $("$TESS_BIN" --version 2>/dev/null | head -n1 | awk '{print $2}')"
    if "$TESS_BIN" --list-langs 2>/dev/null | grep -iqE "^(hin|ben)$"; then
        echo "[OK] Hindi/Bengali OCR language packs present."
    else
        echo "[WARN] Hindi/Bengali OCR packs (hin/ben) not detected - OCR of those scripts skipped."
        WARNINGS=$((WARNINGS + 1))
    fi
else
    echo "[WARN] Tesseract not found - OCR will be skipped (optional)."
    WARNINGS=$((WARNINGS + 1))
fi

# --- Piper TTS ----------------------------------------------------
PIPER_BIN="$(env_val TTS_EXECUTABLE_PATH)"
PIPER_BIN="${PIPER_BIN:-piper}"
if command -v "$PIPER_BIN" >/dev/null 2>&1; then
    echo "[OK] Piper executable found."
else
    echo "[ERROR] Piper not found ($PIPER_BIN) - narration is disabled. Set TTS_EXECUTABLE_PATH in .env"
    ERRORS=$((ERRORS + 1))
fi
for lang in EN HI BN; do
    voice="$(env_val "TTS_VOICE_$lang")"
    if [ -n "$voice" ]; then
        if [ -f "$voice" ]; then
            echo "[OK] $lang voice: $voice"
        else
            echo "[ERROR] $lang voice file missing: $voice"
            ERRORS=$((ERRORS + 1))
        fi
    else
        echo "[WARN] TTS_VOICE_$lang not set in .env - $lang narration disabled."
        WARNINGS=$((WARNINGS + 1))
    fi
done

# --- llama.cpp ----------------------------------------------------
LLAMA_BIN="$(env_val LLAMA_CPP_PATH)"
LLAMA_MODEL="$(env_val LLAMA_MODEL_PATH)"
LLAMA_BIN="${LLAMA_BIN:-llama-cli}"
if command -v "$LLAMA_BIN" >/dev/null 2>&1; then
    echo "[OK] llama-cli found."
else
    echo "[ERROR] llama.cpp (llama-cli) not found - story/script generation disabled. Set LLAMA_CPP_PATH in .env"
    ERRORS=$((ERRORS + 1))
fi
if [ -n "$LLAMA_MODEL" ]; then
    if [ -f "$LLAMA_MODEL" ]; then
        echo "[OK] GGUF model: $LLAMA_MODEL"
    else
        echo "[ERROR] GGUF model file missing: $LLAMA_MODEL"
        ERRORS=$((ERRORS + 1))
    fi
elif ls models/*.gguf >/dev/null 2>&1; then
    echo "[OK] GGUF model found in models/:"
    ls -1 models/*.gguf
else
    echo "[WARN] No .gguf model in models/ - run scripts/download_llm_model_unix.sh"
    WARNINGS=$((WARNINGS + 1))
fi

# --- Whisper (optional) -------------------------------------------
if ls models/whisper/*.bin >/dev/null 2>&1; then
    echo "[OK] Whisper model present:"
    ls -1 models/whisper/*.bin
else
    echo "[WARN] No Whisper model in models/whisper/ - speech analysis skipped."
    echo "       Run scripts/download_whisper_model_unix.sh to enable it."
    WARNINGS=$((WARNINGS + 1))
fi

# --- Subtitle font -------------------------------------------------
FONT_PATH="$(env_val SUBTITLE_FONT_PATH)"
if [ -n "$FONT_PATH" ]; then
    if [ -f "$FONT_PATH" ]; then
        echo "[OK] Subtitle font: $FONT_PATH"
    else
        echo "[ERROR] SUBTITLE_FONT_PATH points to a missing file: $FONT_PATH"
        ERRORS=$((ERRORS + 1))
    fi
else
    echo "[WARN] SUBTITLE_FONT_PATH not set - Hindi/Bengali burn-in will be refused until a font is set."
    WARNINGS=$((WARNINGS + 1))
fi

# --- Hardware / disk ----------------------------------------------
echo
echo "--- Hardware ---"
if command -v lscpu >/dev/null 2>&1; then
    lscpu 2>/dev/null | grep -E "^(Model name|Architecture)" | sed 's/^/  /'
fi
MEM_KB=$(grep MemTotal /proc/meminfo 2>/dev/null | awk '{print $2}')
if [ -n "$MEM_KB" ]; then
    echo "  RAM: $((MEM_KB / 1024 / 1024)) GB"
fi
echo "--- Disk space ---"
FREE_GB=$(df -BG . 2>/dev/null | awk 'NR==2 {print $4}' | tr -d 'G')
if [ -n "$FREE_GB" ]; then
    echo "[OK] Free space on current volume: ${FREE_GB} GB"
else
    echo "[WARN] Could not determine free disk space."
    WARNINGS=$((WARNINGS + 1))
fi

# --- Required directories ------------------------------------------
echo
echo "--- Required directories ---"
for d in data data/uploads data/projects data/temp data/outputs data/cache models logs; do
    if [ -d "$d" ]; then
        echo "[OK] $d exists"
    else
        echo "[ERROR] $d missing - create it or re-run scripts/setup_unix.sh"
        ERRORS=$((ERRORS + 1))
    fi
done

# --- Summary --------------------------------------------------------
echo
echo "============================================================"
if [ "$ERRORS" -gt 0 ]; then
    echo " RESULT: $ERRORS error(s), $WARNINGS warning(s) - fix the errors above,"
    echo " then re-run this script. See README Phase 8 for setup steps."
    echo "============================================================"
    exit 1
else
    echo " RESULT: no errors ($WARNINGS warning(s))."
    if [ "$WARNINGS" -gt 0 ]; then
        echo " Warnings are optional components - the pipeline will skip them."
    fi
    echo " The environment is ready for a full local pipeline run."
    echo "============================================================"
    exit 0
fi