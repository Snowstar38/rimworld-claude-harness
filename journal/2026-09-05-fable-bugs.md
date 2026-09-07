# 2026-09-05 — Fable 5.1, the evening bug pass (alerts never pause)

M asked for BUGS.md to be worked, the "alerts have to each be muted
on game start" item first, using Opus 5 builders. Her design, taken verbatim:
alerts are the low-stakes channel; at turn start Hands gets the list of what
is active, and a new one arrives as a message on the next tool call. Nothing
about an alert pauses the game.

## What changed

**Companion (`SupervisedPlayTool.cs`).** `Start` baselines every active
High+ alert by its stable key (type, priority, label with the ` xN` count
stripped) and returns them as `baselineAlerts`. `Probe` no longer returns an
alert stop; a key not yet seen is recorded as a non-stopping ring event
`alert_new`. The key set only grows within an epoch, so a break-risk alert
that flaps off and on cannot re-report. `ignoredAlertLabels` is accepted and
ignored.

**Play stack.** `play.py start` prints `standing alerts (2): Minor break risk
x2; Low food` (or `none`) and lost `--ignore-alert`. `play_service` relays
`alert_new` to the event bus with `wake: false` unless the alert is Critical;
`event_bus.wait` only wakes a parked Hands for a wake-worthy event, and a High
alert rides along with whatever wakes it next, or reaches a busy Hands at its
next tool boundary as `[ALERT_NEW] Low food (High) — game still running`.
`run.py` and `combat.py advance` pass `watchAlerts: false`; `run.py` diffs the
alert list around the pulse and prints `NEW ALERT:` lines. `--ack-alert` is gone.

**Bills.** The "human corpse (0/1)" label came from `BillCommon.DisplayDef`
walking the recipe's fixed filter for a representative def when nothing on
the map matched, never consulting the bill's own filter; `CorpsesHumanlike`
is the first child of `Corpses`, so `Corpse_Human` won. A generic slot now
carries the filter summary (`corpses`), None counts print as not read, the
forbidden-only case prints first (`3 corpses on the map but all forbidden`),
a 0x repeat bill prints `FINISHED (0 left)`, and butcher bills get
`human corpses excluded` beside the stove bills' meat line. The CHRONICLE
claims BUGS.md complained about were already corrected in `9d27e2f`.

**Small ones.** `act.py` labels are case- and ellipsis-insensitive and a miss
names the eight closest; `act.py labels [word]` lists; `act.py hunt <animal>`
reads the current cell and designates in one process. `map.py --layers beds`
aliases `build`. DOWNED prints as `DOWNED (alive, not a corpse)` with colony
animal vs wild. `status.py`'s PAUSED line says whether a supervised-play
service is alive. The "PAUSED while 12 hours passed" item was the old
pulse-and-pause rota, not a defect.

## Evidence

Offline: 232 instrument tests (194 at start), 45 companion tests, contract
test offline clean, Release build 0 warnings. DLL 993,792 bytes, SHA-256
`C5F32F15…41E`, installed with the game closed; the previous one is
`artifacts\installed-backup-20260905\…pre-alerts-never-pause`.

Live, day-55 save, two High alerts standing: `play.py start` printed both and
ran 28,992 ticks at Fast with no alert stop; the first start stopped correctly
on a real `Quest failed` letter and the restart baselined it. A `run.py 5 Fast`
pulse ran and stopped on `Wild man wanders in`, not an alert. On a later start
only `Low food` was standing; `Minor break risk` came back mid-run and arrived
as `alert_new` (`wake: false`) with the game still running, 8,790 ticks, no
stop. The packet rendered as `[ALERT_NEW] Minor break risk (High) — game still
running`. Not seen live: a Critical alert waking a parked Hands.

The original day-55 save was reloaded and left paused; no save was written;
the test session's event rows were deleted and the runtime binding restored.
