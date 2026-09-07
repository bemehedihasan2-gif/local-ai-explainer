@echo off
setlocal
REM One-time Piper voice download (Windows) for Phase 6 local narration.
REM Explicit download only - the app never downloads voices silently.
REM
REM Usage:
REM   scripts\setup_piper_voices.bat                    (English voice, default)
REM   scripts\setup_piper_voices.bat hi/hi_IN/xxx/medium/hi_IN-xxx-medium.onnx
REM       ^ pass the HF file path of another voice (e.g. Hindi/Bengali
REM         community voices) to fetch it instead.
REM Voice files come from the official rhasspy/piper-voices repo on Hugging Face.
cd /d "%~dp0.."

set VOICE_FILE=%~1
if "%VOICE_FILE%"=="" set VOICE_FILE=en\en_US\lessac\medium\en_US-lessac-medium.onnx

for /f "tokens=1,2 delims=/" %%A in ("%VOICE_FILE%") do set LANG_DIR=%%A\%%B

echo Downloading Piper voice "%VOICE_FILE%" into models\voices\%LANG_DIR%\ ...
echo This is a one-time download (no API key). The app never downloads voices automatically.
echo.

python -m pip install --quiet huggingface_hub
if errorlevel 1 exit /b 1

python -c "from huggingface_hub import hf_hub_download; hf_hub_download(repo_id='rhasspy/piper-voices', filename=r'%VOICE_FILE%', local_dir='models/voices')"
if errorlevel 1 (
    echo [ERROR] Download failed. Check the voice path on
    echo https://huggingface.co/rhasspy/piper-voices/tree/main
    exit /b 1
)

echo.
echo Done. The .onnx.json config file sits next to the .onnx automatically.
echo Point the matching voice setting in .env at the downloaded file, e.g.:
echo   TTS_VOICE_EN=models\voices\%VOICE_FILE%
echo.
echo Hindi and Bengali: many community Piper voices exist on Hugging Face.
echo Find a voice id (e.g. hi_IN-...-medium / bn_IN-...-medium), then run this
echo script again with its file path, e.g.:
echo   scripts\setup_piper_voices.bat hi\hi_IN\sanman\medium\hi_IN-sanman-medium.onnx
echo   scripts\setup_piper_voices.bat bn\bn_IN\tanmay\medium\bn_IN-tanmay-medium.onnx
echo and set TTS_VOICE_HI / TTS_VOICE_BN in .env to the downloaded files.
echo.
echo Also make sure the Piper CLI itself is installed and on PATH (or set
echo TTS_EXECUTABLE_PATH in .env). Restart the backend afterwards; the System
echo status endpoint then reports the configured voices.
endlocal
