# Pause-storm bug pass — 2026-09-06 (evening)

M's ask: fix the pause bug from the 2026-09-07 stream errata, plus whatever else blocks playing on stream. Five Opus 5 builders in parallel, a sixth stopped and undone (a companion `remove_designation` op; M: dragging Cancel over the area already does it). RimWorld was closed mid-pass; the pause path and the click path were then tested live on M's save from the storm itself (see Live results). DLL installed with the game closed.

## The pause bug

`colonist_injury` fired on every scratch from a downed wolf; each stop exited the service, each restart re-baselined and stopped again ~10 s later. Six turns lost. Fix, in `SupervisedPlayTool.cs`:

- A `colonist_injury` stop is remembered per pawn across epochs. A restart within the cooldown (`--injury-cooldown`, default 180 s) turns that pawn's further minor injuries into non-stopping `injury_observed` rows. Health thresholds, downs and deaths still stop.
- `play.py start --allow-injured <pawnId>` acknowledges a pawn for the epoch. The start line prints suppressed pawns.
- The stop detail names the delta and what to do: break the contact, undraft them, or pass `--allow-injured`.
- `play_service.py` writes `stopReason` / `stopDetail` / `stopKind` into `state/play-service.json` at exit; `play.py status` prints one human line above its JSON.
- `status.py --brief` reads the stop reason from the companion (kept after the service exits) or the file: `PAUSED by guard colonist_injury -- ... (service exited 43 s ago; fix the cause, then play.py start)`. The old two-wordings bug is gone.

**Live finding, second root cause.** On the storm save both Longhoff and Octave were bleeding, and the guard fired with `injuries 3 -> 3, bleed 0.80 -> 0.80`: blood-loss creep on a known wound counted as a new injury. The cooldown alone only slowed the storm (one stop per pawn per 180 s). Rule tightened: in colony mode only a **new wound** (injury count up) or **new bleeding** (bleed rate up) stops; creep is an `injury_observed` row. Health thresholds, downs and deaths still stop. The stop line now also prints blood loss.

## Other fixes

| Report | Change | Limit |
|---|---|---|
| Orders on a stopped clock returned VERIFIED | New `clock.py`; `combat.py` and `order.py` print `ORDER QUEUED -- CLOCK IS STOPPED` when time is not running | combat orders now normally read QUEUED (stage 1 pauses by design) |
| `--do` refused by `order.py goto/haul/tend` | Every subcommand accepts `--do`; `--dry-run` wins | |
| Ground tend had no path | `order.py tend` rescues to a bed first; `--draft` tends where they lie. RimWorld offers Tend only to a drafted doctor, so the brief's "undrafted float-menu Tend" was wrong | first live downed pawn |
| `click_cell` silent off-screen | New `pick.py`: camera onto the cell, armed designator dropped, click, selection read back; used by `act.py allow`, `buildings.py gizmo`, `mini_install.py`, `map.py` | 3-cell edge margin is a guess. Upstream: `get_selection_semantics` throws on a selected turret; `pick.py` falls back to the gizmo owners |
| Items on a tile hijacked `gizmo --do` | Selects by thing id, cycling the cell, refuses with `CLICK MISSED`; MinifiedThing ids accepted | cycling beyond 8 things unproven |
| `act.py apply` dry run wore applied wording | Caller's `--do` decides; `APPLIED` reprints under warnings; `Cancel` on a built cell is `NO-OP` | turn-5 root cause inferred |
| No designation remover | `act.py undesignate x z [w h] --do` (area Cancel, names survivors) | Tame/Hunt through the bridge unverified |
| `map.py` build layer hid plants; arg bugs | Plants drawn on empty cells; `--corner` errors name the tokens; no-centre falls back to base | |
| 0 hostiles beside a downed manhunter | Downed non-colonists within 30 cells count as hostiles, named per row; guard's own predator predicate printed | deliberate over-count |
| `pawns.py --animals` distance / wolf miss | Not reproduced; rows print both endpoints from one snapshot, footer accounts for every row | |
| `inv.py` food in units | `inv.py food` prints nutrition and days (vanilla table; unknown defs named and excluded) | table, not game data |
| Turn tracker stuck (turns 8, 18, 20) | Claim records `handsAgent` before routing; forks leave a heartbeat; `stream.py hands-close` closes a dead turn without `reset`; `hands-release --no-pause`; SessionEnd force-closes | live turn 20 closed this way |
| Chat `@` "block" | Misdiagnosed: approved in 20 ms, delivered 5 m 49 s later behind a review backlog, plus lock failures dropping packets. Mentions now jump the queue and wake a parked agent | rota service and overlay server need a restart to load it |
| `hands-last.md` intermediate briefs | `hands-check`/`handback` print FINAL / INTERMEDIATE / UNDATED | |

## Tests and build

Instruments 502 → 675, companion 59 → 66, stream-overlay 30 → 38, all OK. Release build 0 warnings; DLL 1,028,096 bytes, SHA-256 `0442371d…` (the first pass's `69037ac5…` is kept beside the backup), installed to `RimWorld\BridgeTools\HomeBridge\` with the game closed; previous (1,024,000 bytes, `70b47d5e…c3ef`) in `companion\artifacts\installed-backup-pause-20260906\`.

## Live results (save "Lampblack - perimeter sealed…", tick 4907193, both wounded pawns untended)

- First DLL: `play.py start` stopped within 12 s on `colonist_injury` with the new detail line; `status.py --brief` printed `PAUSED by guard colonist_injury -- …`; the restart printed `injury stops suppressed for 161 s: Longhoff` and ran until Octave's creep stopped it; the third start (both suppressed) ran 20 s, `play.py pause` read back as `PAUSED by play.py pause`.
- Rebuilt DLL: a fresh start ran 60 s+ (4,233 ticks) with only `injury_observed` creep rows, no stop.
- `order.py goto` on the paused clock printed the QUEUED warning. `buildings.py gizmo Turret_MiniTurret@133,154 "Uninstall"` selected the turret by id, listed nine controls and named what it would fire.
- `inv.py food` days-of-food needed a fix (colonist count read as 0 with the colonists block off); `status.py --brief` now prints the service error text when that is the stop.
- Something started supervised play right after the second load, before my own `start` (it reported `already owned`, epoch 1); not chased.

## M's request: hostiles-cleared reminder

New non-stopping `hostiles_cleared` event (wakes a parked Hands) when the last conscious hostile goes down: `No conscious hostiles remain: 2 downed (Timber wolf at 123,146; Raider Bex at 130,150) -- finish off or capture; 3 colonists still drafted (Finn, Ian, Longhoff) -- undraft them.` Fires once, re-arms when a conscious hostile reappears, and survives a guard stop plus restart across the last kill. Downed wild animals within 30 cells of a colonist are listed even though a downed manhunter no longer reads as hostile. DLL 1,030,656 bytes, SHA-256 `d83f26bb…`, installed with the game closed. Unverified live: the end of a real raid.

## Still owed

`order.py tend` on a downed pawn; a chat mention reaching Hands promptly once the overlay server is restarted; `act.py undesignate` on a tamed animal; a `gizmo --do` on a tile with items lying on it.
