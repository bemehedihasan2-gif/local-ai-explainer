# ============================================================
#  create_desktop_shortcut.ps1 - Local AI Video Explainer
#
#  Creates an "AI Video Explainer" shortcut on the current
#  user's desktop that starts START_AI_VIDEO_EXPLAINER.bat.
#  No third-party software required.
#
#  Usage (right-click Start -> Windows PowerShell, or any
#  PowerShell window, from the project folder):
#      powershell -ExecutionPolicy Bypass -File scripts\create_desktop_shortcut.ps1
# ============================================================

$ErrorActionPreference = "Stop"

# Project root = parent of the scripts folder that contains this file.
$projectRoot = Split-Path -Parent $PSScriptRoot

$launcher = Join-Path $projectRoot "START_AI_VIDEO_EXPLAINER.bat"
if (-not (Test-Path -LiteralPath $launcher)) {
    Write-Host "[ERROR] START_AI_VIDEO_EXPLAINER.bat not found at:" -ForegroundColor Red
    Write-Host "        $launcher"
    exit 1
}

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "AI Video Explainer.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $launcher
$shortcut.WorkingDirectory = $projectRoot
$shortcut.Description = "Start the Local AI Video Explainer (backend + frontend + browser)"
$shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,220"
$shortcut.Save()

Write-Host ""
Write-Host "[OK] Desktop shortcut created:" -ForegroundColor Green
Write-Host "     $shortcutPath"
Write-Host ""
Write-Host "Double-click 'AI Video Explainer' on your desktop to start the app."
Write-Host "Use STOP_AI_VIDEO_EXPLAINER.bat when you are done."
