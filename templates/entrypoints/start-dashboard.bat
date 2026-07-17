@echo off
setlocal
set "PYTHONUTF8=1"
title Second Brain Dashboard
cd /d "%~dp0"

echo.
echo          SSSSSSS      BBBBBB
echo        SS             BB   BB
echo          SSSSS        BBBBBB
echo              SS       BB   BB
echo        SSSSSSS        BBBBBB
powershell -NoProfile -Command "$brain=[char]::ConvertFromUtf32(0x1F9E0); Write-Host ('              ' + $brain + '  SECOND BRAIN') -ForegroundColor Magenta"
echo.
echo   Starting the private local dashboard...
echo   Keep this window open. Press Ctrl+C to stop it.
echo.

powershell -NoProfile -NonInteractive -Command "$ErrorActionPreference='SilentlyContinue'; $health=Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/health' -TimeoutSec 1; if($health.service -eq 'second-brain-dashboard'){Start-Process 'http://127.0.0.1:8765/'; exit 0}; exit 1" >nul 2>nul
if not errorlevel 1 exit /b 0

uv run --project Protocol sb dashboard serve
if errorlevel 1 pause
