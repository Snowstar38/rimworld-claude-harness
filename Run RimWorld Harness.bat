@echo off
setlocal
title RimWorld Claude Harness
cd /d "%~dp0instruments"
set "RIMWORLD_HARNESS_ROOT=%~dp0"
set "RIMWORLD_SESSION=1"
where claude >nul 2>nul || (
  echo Claude Code is not installed or is not on PATH.
  echo Install it, then run Setup.bat again.
  pause
  exit /b 1
)
claude --dangerously-skip-permissions "Read PLAYBOOK.md, the selected mode under modes, and CHRONICLE.md. Run python setup.py --newest Lampblack, then wait for me before playing."
echo.
echo The session has ended. Run this file again to restart it.
pause
