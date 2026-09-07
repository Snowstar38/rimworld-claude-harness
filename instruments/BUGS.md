# Bugs -- open items

One line per item. Delete the line when it is fixed. **Active items only**: what
was fixed and how goes in `..\journal\` (M, 2026-09-07); game knowledge
goes in `PLAYBOOK.md`. Nothing here is a record.

## Patched offline, needs its live trigger

- Warm start: launch `errata.bat` at a running game with a stale binding; the hook should re-bind at Core's first tool call and `hands-claim` then succeed. `bind-check --repair` worked from Core on 2026-09-07 (not under a Git Bash `timeout` wrapper, which hides the Claude ancestor).
- `claude_ancestor()`'s 5 s PowerShell probe inside the PreToolUse hook budget when the binding is dead; the 30 s throttle is written before the probe.
- `trade.py accept` (watched): pauses play deliberately and prints the restart line; the 8 s `Dialog_Trade` watch and the `restage_mismatch` refusal. Needs a trader.
- `play.py start` refusal listing every hostile with a paste-ready `--ignore-hostile` line. Needs a raid.
- A Critical `alert_new` and a negative `notification_new` waking a parked Hands (`meta.wake`).
- `order.py tend` on a downed colonist (rescue-to-bed, `--draft`); `home/order rescue` finding a bed.
- `order.py force` on a chop / deconstruct / blueprint cell off-camera; `order.py haul` printing `unreachable_storage`.
- `act.py undesignate --do` over a real blueprint (destroys it, reports it); `act.py hunt Ibex404123`; `act.py allow --do`; `act.py undesignate` over a tamed animal (Tame/Hunt through the bridge).
- `buildings.py reinstall <shelf> x z --do` (two clicks); `buildings.py gizmo <bare number> "Cancel"`; `Def@x,z` on the second cell of a two-cell shelf (the 115,154 shelf was gone before it could be checked).
- `build.py <def> x z --do` placing, and one the game refuses; `build.py Battery x z --do` with no direction.
- `bills.py add <x,z> "<recipe>" --do`; a butcher bill with skeletons on the map printing the ROTTEN / DESSICATED line.
- `combat.py end` with one downed raider and no conscious hostile.
- `ui.py click "OK"` on a live modal (the missing `rim.init()` cause is inferred offline); `ui.py tab <name>` open-read-close; `pawns.py set --do` on a running clock printing `watch: skipped`.
- `letters.py dismiss` on a `NewQuestLetter` (whether `CanDismissWithRightClick` refuses).
- `world.py --show 8`; `zones.py crop --do`.
- `play.py start` `WinError 5` temp-name retry (needs a write collision).

## Unexplained

- 428 pemmican (2026-09-05): 216 bought, 212 reported in the Freezer, zero two turns later. Nothing on this map can be missed by `inv.py --all-owners`, so it left with a caravan, was consumed, or was one stock counted twice. Not reproducible.
- Placement occupancy mismatch at bed cell 113,139 (2026-09-05): preview and apply now share one predicate and a live matrix agreed; the original state is gone.
- `home/order equip` on a FORBIDDEN weapon unforbids it and issues the job (`unforbade`). Deliberate; vanilla would have refused. Unverified whether that is wanted.

## Upstream bridge (RimBridgeServer), worked around

- `open_main_tab` null-refs on the World toggle; `world.py` reads the tile instead.
- `rimworld/take_screenshot` returns `sourcePath: null`; `see.py` and `cam.shoot` find the file by write time and refuse a stale one.
- `rimworld/get_trade_sheet` returns a non-JSON string; nothing reprints the sheet with a session open.
- `get_selection_semantics` throws on a selected turret; `pick.py` falls back to the gizmo owners.
- One `build.py` call took ~2 minutes of wall time (day-67 turn 8); not seen since.
