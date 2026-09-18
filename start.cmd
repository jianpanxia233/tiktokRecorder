@echo off
rem ===================================================================
rem  DouyinLiveRecorder launcher (portable runtime, no system Python)
rem
rem  GUI   : double-click this file, or run  start.cmd
rem  CLI   : run  start.cmd cli
rem
rem  Note: this file is intentionally ASCII-only - cmd.exe reads .bat
rem  using the OEM code page, so Chinese text here would get garbled.
rem  The real logic lives in start.ps1 (UTF-8, displays Chinese).
rem ===================================================================
setlocal
set "ROOT=%~dp0"
if /i "%~1"=="cli" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%start.ps1" -Cli
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%start.ps1"
)
endlocal
