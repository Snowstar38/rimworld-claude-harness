@echo off
REM The window M can type into from her phone (Remote Control for Claude
REM Code) while a playthrough is running. setup.py spawns it when the game comes
REM up -- see ensure_listener() there, which is also what keeps there being only
REM one of these. Its whole brief is listener.md, next to this file.
REM
REM Spawned as:  cmd /c start "" cmd /k call "<this file>"
REM The outer `cmd /k` is the point: if the session ends or errors, the console
REM stays open with the command still on screen instead of vanishing.
REM
REM The failure mode this file is written against (2026-09-01, found the hard
REM way): a window that LOOKS like the listener but has no claude in it is just
REM a command prompt, and a message typed into it comes back as
REM "'u' is not recognized as an internal or external command". Silence would
REM read as delivery. So every path that ends without a session running says so
REM in as many words, and leaves a one-word way back.

title courier
cd /d "%~dp0"

set "CLAUDE=%USERPROFILE%\.local\bin\claude.exe"
if not exist "%CLAUDE%" (
    echo Cannot start the listener: no claude.exe at "%CLAUDE%".
    goto :ended
)

echo The Courier. M's phone messages arrive in this window.
echo Close the window to stop it.
echo.

REM Full model id, not the "opus" alias, for the same reason daily\run-opus.ps1
REM uses it: the alias silently becomes a different model one day.
REM
REM `--remote-control <name>` is what makes this reachable from the phone
REM without anyone typing /rc into the window -- which matters, because the
REM window opens when the game does and nobody is at the keyboard then. The
REM name is NOT optional here even though the flag says it is: the parameter
REM is optional, so leaving it off would swallow the prompt below as the
REM session's name. It is also what she picks the session by in the app.
"%CLAUDE%" --remote-control courier --model claude-opus-5 --dangerously-skip-permissions "Read listener.md in this folder and follow it exactly. Then wait quietly and do nothing at all until M writes to you."

:ended
echo.
echo ============================================================
echo  The Courier's session has ENDED. This window is now an
echo  ordinary command prompt: anything typed here goes to cmd,
echo  NOT to a model, and will not reach the game.
echo.
echo  Type   restart   and press enter to start it again.
echo ============================================================
doskey restart=call "%~f0"
