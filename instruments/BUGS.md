# Bugs -- open items

One line per item. Delete the line when it is fixed. **Active items only**: what
was fixed and how goes in `..\journal\` (M, 2026-09-07); game knowledge
goes in `PLAYBOOK.md`. Nothing here is a record.

## Patched offline, needs its live trigger

- Warm start: launch `errata.bat` at a running game with a stale binding; the hook should re-bind at Core's first tool call and `hands-claim` then succeed. `bind-check --repair` worked from Core on 2026-09-07 and 2026-09-08 (not under a Git Bash `timeout` wrapper, which hides the Claude ancestor).
- `debug.py`: `flare` through `Actions\Do incident\SolarFlare` was refused by the game live (`Incident target is null or not allowed`, the node's label ends `[NO]`), while `run "Actions\Add Game Condition...\Solar flare\1 hour" --do` produced a real flare; being rerouted, with a refusal on any `[NO]` label. `find` / `show` / `spawn` held live. `raid`, `manhunter`, `trader`, `spawn`, `down`, `kill`, `heal` and `fight`'s second stage (`followUp` -> `rimworld/click_cell`, which really did start `Ernst started a social fight with Samantha`) all held live 2026-09-12. Unproven: `letter` through the bridge, and the `--do` refusal in a `stream.py mode live` session.
- `trade.py accept`'s `restage_mismatch` refusal. The rest of the watched path held live 2026-09-12 (deliberate pause, the `python play.py start` line, the 8 s `Dialog_Trade` window, the deal executed). The mismatch is verified inside `accept` against its OWN snapshot before and after the window, so re-staging between `preview` and `accept` does not raise it -- it needs somebody editing the real trade window during those 8 s.
- `act.py tame` on a 100%-wildness animal (the refusal). None on Threadneedle; `tame Megaspider45858` was ACCEPTED live 2026-09-12, so an insectoid is not that case either.
- `ui.py click --index N` / `--rect x,y,w,h` on the storyteller page (new-colony start only).
- `letters.py open` on an expired quest REFUSES before the dialog (none on Threadneedle; `DescribeLetter` carries no timeout, so it is inferred from every choice being disabled).
- `dialog.py` on the colony-name dialog with `--accept`, which refuses without text (the dialog cannot be opened while `open_main_tab` null-refs on the World toggle).
- `power.py` with a generator out of fuel (`no fuel` on the idle line). Needs a built fuelled generator; Threadneedle has none finished, and the flare banner was confirmed live 2026-09-12.
- `play.py start` `WinError 5` temp-name retry (needs a write collision).


## Open -- found live 2026-09-12 (destructive pass)

- `debug.py wound <pawn> --severity scratch` cannot raise the guard's no-stop
  path: `T: 10 damage` is vanilla's smallest damage action (the others are 300
  and 5000), it lands one injury of exactly 10.00 hit points, and the guard's
  serious test is `>= 10f`. FIXED in `debug.py` only to the extent of telling
  the truth -- the note and docstring now say it stops -- plus
  `test_debug.WoundTests.test_the_scratch_says_it_will_stop_supervised_play`.
  The cooldown and `--allow-injured` paths are what prove the suppression.

## Unexplained

- 428 pemmican (2026-09-05): 216 bought, 212 reported in the Freezer, zero two turns later. Nothing on this map can be missed by `inv.py --all-owners`, so it left with a caravan, was consumed, or was one stock counted twice. Not reproducible.
- Placement occupancy mismatch at bed cell 113,139 (2026-09-05): preview and apply now share one predicate and a live matrix agreed; the original state is gone.
- `home/order equip` on a FORBIDDEN weapon unforbids it and issues the job (`unforbade`). Deliberate; vanilla would have refused. Unverified whether that is wanted.
- `buildings.py --pending --near` reads 60 s apart did not reconcile (10 blueprints / 5 frames, then 5 / 1, 2026-09-07). The header now counts both together and the tick is printed; reopen only if it recurs with two ticks to compare.

## Companion (HomeBridge DLL), known and deferred -- each costs a game-closed reinstall

- `BillCommon.cs`'s reachability is now `TraverseMode.ByPawn` for the bill's allowed worker (`MapItems.TraverserFor`) with a PassDoors fallback when no representative pawn exists. The installed DLL was exercised live 2026-09-12 only against a REAL region break (a chunk spawned in the sealed insect cave), which PassDoors would also have caught: the LOCKED-DOOR case the change exists for is still unproven. Needs a bench, a stack behind a door forbidden to the worker, and a bill that wants it.
- `DialogTool` hard-codes `success: true` on `--accept`: on `Dialog_NamePawn` the accept applied nothing (live 2026-09-12; the dialog stayed up, the pawn kept her name). `dialog.py` now reads the window stack back and says `accepted: NO ... STILL OPEN`; the C# side should press the dialog's own accept button (or call `Dialog_NamePawn`'s confirm) and report what the stack says afterwards.

## Upstream bridge (RimBridgeServer), worked around

- `open_main_tab` null-refs on the World toggle; `world.py` reads the tile instead.
- `rimworld/take_screenshot` returns `sourcePath: null`; `see.py` and `cam.shoot` find the file by write time and refuse a stale one.
- `rimworld/get_trade_sheet` returns a non-JSON string; nothing reprints the sheet with a session open.
- `get_selection_semantics` throws on a selected turret; `pick.py` falls back to the gizmo owners.
- `Widgets_RadioButtonLabeled_UiWorkbench_Patch.Prefix` never binds `chosen`, so a radio button's selected state is not in the ui payload; `ui.py` prints `[?]`. PATCHED in the `companion/upstream` source (submodule commit `cac8416`, 2026-09-12); the installed bridge DLL is from 2026-08-24 and predates it, so it needs an upstream rebuild and a game-closed reinstall.
- `DescribeLetter` emits none of `LetterWithTimeout`'s `disappearAtTick` / `TimeoutPassed`, so an expired quest is inferred from every choice being disabled.
- `execute_debug_action` cannot run `ToolWorld` actions (the policy says unimplemented) and leaves a second `Dialog_DebugOptionListLister` standing for `Apply damage...` / `Add Hediff...`; `debug.py` says so and routes around both.
- `click_ui_target` on the Bio tab's rename pencil (an `icon_button` inside an ITab `ImmediateWindow`) returns success and fires nothing; only the real mouse at the scaled pixel opens `Dialog_NamePawn`.
