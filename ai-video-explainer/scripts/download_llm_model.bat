@echo off
setlocal
REM One-time local LLM (GGUF) download for Phase 5 story + script generation.
REM Explicit download only - the app never downloads models silently.
REM Usage: scripts\download_llm_model.bat [repo] [gguf-name]   (defaults below)
REM Default: a ~1.5B Q4_K_M instruct model (~1 GB) - comfortable on 8 GB RAM.
cd /d "%~dp0.."

set REPO=%~1
if "%REPO%"=="" set REPO=Qwen/Qwen2.5-1.5B-Instruct-GGUF
set FILE=%~2
if "%FILE%"=="" set FILE=qwen2.5-1.5b-instruct-q4_k_m.gguf

echo Downloading "%FILE%" from huggingface.co/%REPO% into models\llm\ ...
echo This is a one-time download (no API key). Q4_K_M 1.5B ~= 1 GB.
echo The app never downloads models automatically.

python -m pip install huggingface_hub
if errorlevel 1 exit /b 1

python -c "from huggingface_hub import hf_hub_download; hf_hub_download(repo_id='%REPO%', filename='%FILE%', local_dir='models/llm')"
if errorlevel 1 (
    echo [ERROR] Download failed. Check the model name on
    echo https://huggingface.co/%REPO% (tree view) and retry, e.g.:
    echo   scripts\download_llm_model.bat Qwen/Qwen2.5-1.5B-Instruct-GGUF qwen2.5-1.5b-instruct-q4_k_m.gguf
    exit /b 1
)

echo.
echo Done. Also install the llama.cpp CLI (llama-cli) if you do not have it:
echo   winget install llama.cpp        (or grab the official GitHub release zip)
echo Then set in .env:
echo   LLAMA_CPP_PATH=C:\path\to\llama-cli.exe
echo   LLAMA_MODEL_PATH=models\llm\%FILE%
echo (or leave both empty: the app auto-discovers llama-cli on PATH and a
echo  single .gguf inside models\ )
echo Restart the backend, then Generate explanation will work.
endlocal