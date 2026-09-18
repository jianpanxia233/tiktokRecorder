# ===================================================================
#  DouyinLiveRecorder - local portable launcher
#
#  This machine had no Python installed, so the repo carries its own
#  portable runtime:
#     .runtime\pyfull   Python 3.12.8 full (tkinter + pip included)
#     ffmpeg\           ffmpeg 6.1.1 + ffprobe
#  All dependencies are already installed - just run this script.
#
#  Usage:
#     powershell -ExecutionPolicy Bypass -File .\start.ps1         # GUI
#     powershell -ExecutionPolicy Bypass -File .\start.ps1 -Cli    # recorder
#     (or simply double-click start.cmd)
#
#  NOTE: keep this file ASCII-only. Windows PowerShell 5.1 parses .ps1
#  as ANSI when there is no BOM, so non-ASCII text here gets mangled.
#  Chinese docs live in 本地运行说明.md instead.
# ===================================================================
param(
    [switch]$Cli
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }

$python = Join-Path $root '.runtime\pyfull\python.exe'
if (-not (Test-Path $python)) {
    Write-Host "[ERROR] portable Python not found: $python" -ForegroundColor Red
    Write-Host "        .runtime may have been deleted." -ForegroundColor Yellow
    exit 1
}

# ffmpeg must be on PATH - main.py checks for it during startup
$env:PATH = (Join-Path $root 'ffmpeg') + ';' + $env:PATH
# sources and config.ini contain Chinese; force UTF-8 or Windows decodes them as GBK
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

Set-Location $root

if ($Cli) {
    Write-Host "Starting recorder (main.py, console mode) ..." -ForegroundColor Cyan
    Write-Host "Live URLs : config\URL_config.ini" -ForegroundColor DarkGray
    Write-Host "Recordings: downloads\" -ForegroundColor DarkGray
    & $python (Join-Path $root 'run.py') main.py
} else {
    Write-Host "Starting desktop GUI ..." -ForegroundColor Cyan
    & $python (Join-Path $root 'launcher.py')
}
