@echo off
setlocal
title RimWorld Claude Harness Setup
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 (
  echo.
  echo Setup did not finish. Read the error above.
) else (
  echo.
  echo Setup complete. Double-click "Run RimWorld Harness.bat".
)
pause
