# daily-play.ps1 -- run a bounded native-turn-loop RimWorld session from a
# daily slot (or anywhere). M asked for this bridge (mail, 2026-09-15):
# the turn loop keeps context lean, and the hooks that drive it only load for
# a session started in this folder with RIMWORLD_SESSION=1 -- both fixed at
# claude.exe start, so a daily session cannot join the loop itself. It can,
# however, start a child session that does. This is that start.
#
# Usage, from an agent session (run it in the background; a play session is
# long):   powershell -NoProfile -File "C:\Home\rimworld\instruments\daily-play.ps1" [-Turns 4] [-Model claude-fable-5] [-Launcher fable] [-Smoke]
#
# The child writes its deliverable to state\daily-play-last.md; read that,
# not the transcript log. -Smoke verifies the wiring (hooks, binding, stack
# status) without touching the game.

param(
    [int]$Turns = 4,
    [string]$Model = "claude-fable-5",
    [string]$Launcher = "a daily session",
    [switch]$Smoke
)

$ErrorActionPreference = "Continue"

$here   = $PSScriptRoot
$claude = Join-Path $env:USERPROFILE ".local\bin\claude.exe"
$stamp  = Get-Date -Format "yyyy-MM-dd-HHmm"
$logDir = Join-Path $here "state\daily-play-logs"
$log    = Join-Path $logDir "daily-play-$stamp.log"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

if (-not (Test-Path $claude)) {
    Write-Output "FAILED: no claude.exe at $claude"
    exit 1
}

Set-Location $here
$env:RIMWORLD_SESSION = "1"

if ($Smoke) {
    $prompt = @"
This is a smoke test of the daily-play wiring, launched by $Launcher. Do NOT launch, load, or touch the game or its clock in any way. In order: (1) run ``python stream.py bind-check`` and note whether this session's runtime binding is live -- if it is, the SessionStart hook fired, which proves RIMWORLD_SESSION and the hooks folder both reached you; (2) run ``python setup.py --status`` (report only -- if it says the game is not up, that is the expected answer, not a problem to fix); (3) write what you found, plus anything surprising, to state\daily-play-last.md under a "SMOKE" heading, then end. Do not commit.
"@
} else {
    $prompt = @"
Read daily-play.md in this folder and follow it. You were launched by $Launcher. Turn budget: $Turns turns. Setup command if the stack is down: python setup.py --newest Lampblack
"@
}

"$(Get-Date -Format s)  daily-play start (model=$Model, turns=$Turns, smoke=$Smoke, by=$Launcher)" |
    Add-Content -Path (Join-Path $logDir "runs.log") -Encoding utf8

& $claude -p $prompt --model $Model --dangerously-skip-permissions |
    Out-File -FilePath $log -Encoding utf8
$code = $LASTEXITCODE

"$(Get-Date -Format s)  daily-play end (exit $code)" |
    Add-Content -Path (Join-Path $logDir "runs.log") -Encoding utf8

# The child's deliverable, surfaced for the caller.
$report = Join-Path $here "state\daily-play-last.md"
if (Test-Path $report) {
    Get-Content $report
} else {
    Write-Output "WARNING: child session left no state\daily-play-last.md (transcript: $log)"
}
exit $code
