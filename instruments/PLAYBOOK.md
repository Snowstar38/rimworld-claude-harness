# PLAYBOOK - RimWorld operating manual

Run commands from `C:\Home\rimworld\instruments`. Read this at session start, then the selected [session mode](#session-mode) and [CHRONICLE.md](CHRONICLE.md). Hands inherits that context and reads [hands.md](hands.md) for its turn instructions. Use each instrument's `--help` for full options; source contracts and maintenance details live in [INSTALL.md](../companion/INSTALL.md).

## Start the session

```text
python setup.py --load "<save name>"
python setup.py --newest Lampblack
python setup.py --status
```

Choose one load command. Setup starts the stack and loads paused; `--status` only reports. **Exit 2 means an unconnected RimWorld process may be M's own game: stop and ask her.** Do not surround setup with bare `games_start` or `games_stop` calls.

Use this colony's `Lampblack - ...` saves. `Wayside - ...` belongs to Sol; shared `Autosave-1..5` files are not session saves. Lampblack files live in `C:\Home\rimworld\bridge\profile\Saves`; verify a save using the returned `path`.

If a save is repudiated, mark it with `setup.py --void "<name>" --reason "<why>"`. Newest-save loading skips it. `--unvoid "<name>"` reverses the mark.

`python stream.py go` runs **once per session**, after the mode: it resets the turn counter to 0 and closes any stale turn, keeping goals, mood and feed (`--keep-turns` keeps the numbering). If the setup STATUS block or `stream.py status` prints `!! binding : DEAD`, read *Recover a stale runtime binding* below before anything else.

### Session mode

Read the mode M selected; `python stream.py mode` reports it.

| Mode | Instrument problems |
|---|---|
| [Live](modes/live.md) | Play around them; record WEIRD. No code repair or debugging. Reading an order's refusal is ordinary gameplay. |
| [Practice](modes/practice.md) | A narrated investigation may take up to one minute, with time running. Record anything needing a patch. |
| [Workshop](modes/workshop.md) | Pause, inspect, repair and test as needed. |

One-agent ownership, human pauses and valid blocked-clock handbacks apply in every mode.

## Run the turn loop

**Only one Hands controls the game and UI.** Core sets goals, decides and reads handbacks; it does not call game instruments, except for setup/status, stack recovery and session shutdown. Scouts may make instrument/API reads that do not select, click, pause, change speed or require time to advance. The scheduler dispatches blind Lookouts automatically.

1. Core runs `python stream.py go` once to start the scheduler, then sets session goals:

   ```text
   python stream.py goals --long "..." --short "..."
   ```

2. Before spawning a background Hands fork, open its turn:

   ```text
   python stream.py hands-start --goal "<goal>"
   ```

   Brief the fork: "Read `C:\Home\rimworld\instruments\hands.md`, then take the turn: <goal>." It inherits the background. Hands runs `hands-claim` once; Core leaves gameplay and stream narration to it. The goal is where the turn starts, not a checklist that ends it: do not write "do X, Y, Z then report", and if a fork returns well under five minutes with its list done, say in the next brief that a finished list does not end the turn.

3. **A turn is six minutes of wall time, and Hands plays until the system says so.** The hook warns at five minutes, reports overdue at six, then repeats roughly every 45 seconds with elapsed time and overage; Hands has no clock of its own and is told not to estimate one. A finished brief, or a message from M handled mid-turn, does not end the turn; a human pause, an unstartable clock or `TaskStop` does. Delivery waits for the next tool boundary. Hands writes `state\hands-last.md` once at the end and returns roughly 300 tokens: CHANGED / NEEDS DECISION / WEIRD / NOTES.

4. Native stop pauses supervised play and returns event routing to Core. Core waits for completion, then publishes the handback:

   ```text
   python stream.py handback --summary "..." --mood welp --short "..."
   ```

   Make the summary a short story-so-far card, about 220-300 characters. Then open the next turn.

**Before sending a fork any message, run `python stream.py hands-check`.** Never message a completed fork: that restarts it. An intermediate report file is not completion. Use `TaskStop` to end a turn early; do not start another while the previous turn is running or unresolved.

Native routing delivers viewer messages and watcher reports. Do not poll the scheduler, inbox or overlay. Send bulk one-off reads to Scouts. Reserve `consult.py "<question>"` for a session-threatening harness failure with no documented workaround; launch it in the background and continue playing while its answer arrives.

### Recover an orphaned turn

An old turn is not proof of exit. First confirm in the native task list that **no Hands is running**. Then `stream.py hands-close --reason "..."` stamps the turn closed and touches nothing else (it refuses against a live heartbeat); `stream.py hands-release --no-pause` clears a route left by a dead fork. `stream.py reset` is no longer the recovery: it also zeroes the counter, pin, feed and rota. `hands-check` and `handback` say whether `hands-last.md` is FINAL or INTERMEDIATE; never brief off an INTERMEDIATE one.

### Recover a stale runtime binding

This is what happens when errata is restarted but the game is not: `state\session-runtime.json` still names the previous session's host process, so `hands-claim` refuses with `runtime binding is STALE` and `play.py start` with `runtime binding unavailable`. Core fixes it in one line:

```text
python stream.py bind-check            # says missing / dead (names the pid) / mismatch / live
python stream.py bind-check --repair   # rebinds this session
```

The lifecycle hook also re-binds a dead binding by itself at Core's next tool call, so often one more command is enough. **A Hands cannot fix this**: it cannot read its own session id. It reports the refusal line verbatim and hands back.

## Control the clock

```text
python play.py start --speed Normal
python play.py speed Fast
python play.py status
python play.py pause
```

Keep time running while Hands reads, thinks and narrates. Normal, Fast and Superfast are available; **do not use Ultrafast**. Use supervised play for the colony clock. `run.py` and `combat.py advance` are bounded diagnostic steps, not commands to chain in a loop. Do not use bare `play_for` or `play_until_letter`.

| Why time stopped | What to do |
|---|---|
| A person paused or changed speed | Respect their control; do not automatically resume. An unexplained pause alone does not prove human input; read `play.py status`. `external_pause` only means the pause came from outside supervised play, not that M is at the keyboard: it is not a reason to end the session. The stop detail says so, and names vanilla's own threat pause if one is behind it (the agent profile has automatic pausing off). |
| The guard stopped on an event | Handle the event, then explicitly `play.py start`. A guard stop never restarts itself. |
| `PAUSED by guard colonist_injury`, repeatedly | **Break the contact, do not restart into it:** move the pawn away or undraft them so they seek care. A restart within 180 s does not stop again on that pawn's minor injuries (downs and severe wounds still stop); `play.py start --allow-injured <pawnId>` acknowledges one. |
| An emergency needs stillness | Give the defensive orders, then resume guarded time. |
| A modal or other blocker prevents safe play | Read the named refusal and address it if possible. **A blocked clock is a valid handback state:** report the reason and next action, and return within budget. |
| `long_event` / `force_pause_cleared` rows | The daily autosave and other long events force-pause for about a second. Supervised play waits them out (up to 20 s) and logs these two rows without stopping; `play.py start` retries through one. Nothing to do. A `FORCE_PAUSED` stop now only ever names a real window. |
| `FORCE_PAUSED` names a window | The game's own popups (`Research finished: Solar panel`) force-pause and hold the picture frozen. Clear it — `python ui.py click "OK"` (text first; it prints what closed, and a miss lists the clickable texts) — then start. |
| `play.py start` failed writing its state file | The call may still have reached the game. Recover with `play.py pause`, then `play.py start`. |
| A turn ended | Leave the boundary paused; the next Hands starts supervised play after handling any blocker. |

`status.py --brief` names why the clock stopped even after the service has exited (`PAUSED by guard colonist_injury -- ... (service exited 43 s ago)`); `play.py status` prints one human line above its JSON. Only `PAUSED (... nothing recorded a stop)` means simply start it.

Only threat, death, decision and unknown notifications stop time. Neutral, positive and negative announcements (a short circuit, a dead crop, a departed visitor) arrive as non-stopping `notification_new` rows; a negative one wakes a parked Hands, so read it, but there is nothing to restart. **Alerts never pause play:** standing alerts are listed at start, and new alerts arrive at a tool boundary. An alert message needs no restart.

A `predator_hunt` guard can cover a nearby animal hunting wildlife. A refusal on hostiles lists **every** hostile and hunting predator with its ThingID and a paste-ready `--ignore-hostile` line; one flag covers a `predator_hunt` too. With a defensive plan for all of them, acknowledge only those exact animals. Other threats remain guarded. If supervised play cannot start, use the combat workflow and a bounded `combat.py advance 5`; do not keep retrying an unchanged refusal.

## Choose an instrument

Prefer direct instruments over opening tabs. Use whole-map pawn/item lists to find things; use `map.py` for spatial questions. An unreadable or failed result is not zero and is not an all-clear.

Lookout leads are visual hypotheses. Reports mark `SEEN:` (on screen) against `EVIDENCE:` (inferred); check every lead against `alerts.py`, `pawns.py --health` or `status.py --brief` before acting, because the Lookout is a blind sandboxed process and cannot run instruments itself. Alerts are the right-edge column; the top-right Learning Helper panel lists tutorial topics and is never a lead. Posture does not show a pawn collapsed or downed, and a storeroom is not cluttered or unfinished unless items lie on open floor outside storage — do not repeat either lead. Zero leads is a normal result. Check current pawns or alerts before treating an old letter or ambiguous screenshot as present danger.

| Need | Command / useful options |
|---|---|
| Colony board | `status.py` - clock, letters, alerts, colonists, threats and open UI; `--brief`, `--explain`. Downed animals count as hostiles (`1 downed -- gets back up`, named per row); `(+N hunt(s) within 40 cells that WOULD stop supervised play)` is the guard's own predator count; letter rows carry their age, `STALE` and `INFO` like `letters.py` |
| Colonists and animals | `pawns.py --roster`; combine `--health`, `--work`, `--gear`, `--bio`, `--schedule`; a bare name filters every block and a miss lists the colonists; use `--animals` or `--threats` for those lists |
| Active alerts / fire | `alerts.py` is the fire read. `inv.py` counts haulable items and cannot see fire, filth or buildings; it prints a SCOPE line saying so |
| Inventory and ownership | `inv.py <item>` - ours by default; `--all-owners`, `--forbidden`, `--near x z r`, `--corpses`. The match is a case-insensitive substring of the singular label (`components` is retried as `component`); a miss prints the nearest labels |
| Food inventory | `inv.py food` - read the `DAYS OF FOOD` line, not the unit column (raw meat is 0.05 nutrition a unit, a simple meal 0.9; 408 units was 5.3 days for five). Values come from a vanilla table, unknown defs are named and left out, corpses do not count |
| Buildings / construction | `buildings.py --pending` or `--inspect`; delivered-to-site materials and colony stock are separate figures, and `!! SHORT` means colony-usable stock is below what is owed, against `not yet delivered (N available in colony)` when it is only unhauled; a blueprint owes its full cost until the first haul lands. Reinstall blueprints list as `reinstall of <label>`. `buildings.py gizmo <thing> "<label>" [--do]` selects by thing id and refuses with `CLICK MISSED` rather than firing on an item lying on the tile (haul it off); `gizmos` only reads. MinifiedThing ids work; `mini_install.py <id> --to x z --do` installs and places in one go; `buildings.py --power` lists every power net, and the summary warns `!! POWER NET n: NO GENERATOR` when batteries or consumers sit on a net without one — `notConnectedToPower=0` can never see that. `buildings.py x,z` or `Def@x,z` resolves any occupied cell of a multi-cell building, and `NOTHING AT cell` means nothing is there, not that a filter hid it. `buildings.py reinstall <thing> x z --do` moves furniture with no Uninstall gizmo (shelves) |
| Bill queues and ingredients | `bills.py [bench]`; `bills.py recipes <bench|x,z>`; read CAN RUN / CANNOT RUN and ingredient-filter exclusions. A target-count bill's `have N in counted storage` is the game's own zone-scoped count, printed beside stored and total; `bills.py add <bench|x,z> "<recipe>" --repeat N`; an unknown flag is an error, not part of the recipe name; corpse lines name ROTTEN / DESSICATED (skeletons) as unusable, and unforbidding those changes nothing |
| Research | `research.py`; `--locked`, `--finished`, `--unlocks` |
| Local terrain and designations | `map.py <x> <z> --layers desig,rooms`; sizes are `x z W H`, `--size W` or `--size W H` around a centre, or `--corner` to read the second pair as the opposite corner; `--legend` appends the key, `--legend-only` prints the key alone with no bridge call; `--full`; `build` aliases buildings, furniture, beds, walls and doors; an unknown layer exits 2 and lists the valid names before drawing anything; `map.py areas x z W H` shows roof, home and allowed areas — they are areas, not designations, and never appear on `desig` |
| Rooms and temperature | `map.py rooms --cold` or `--hot`; `map.py room <x> <z>` for one room |
| World tile | `world.py` - biome, hilliness, elevation, rainfall, latitude, seasonal and average temperature, growing period, and settlements within N tiles with faction, relation and distance; `world.py --show 8` reveals the planet for 8 s. The World tab is a toggle button, not a tab window: it cannot be opened |
| Stockpiles and zones | `zones.py --filter` - contents, filters, occupancy and overlap; `zones.py crop <zone|x,z> Plant_Rice --do` sets a growing zone's crop, and `plantDef: null` means it was never set |
| Letters | `letters.py` — rows are CHOICE / OPEN / INFO / ANNOUNCE with an age in game hours. `STALE` (2 h or older) is history, not news: verify with `alerts.py` or `pawns.py --threats`. INFO is an opportunity quest — only jump/close buttons, nothing to answer. `open <id>`, `decide "<text>"`, `sweep`, `dismiss <id>`. Supervised play sweeps standing announcements every 30 s by itself |
| Gear / immediate drop | `gear.py <pawn>`; `gear.py <pawn> drop "<item>" --do` works paused; the game's gear-tab drop queues a job |
| Ground tend | Usually not needed: an undrafted injured pawn goes to bed and a doctor tends them. In a true emergency `order.py tend <doctor> <patient>` rescues to a bed first, then tends; RimWorld offers Tend only to a drafted doctor, so `--draft` is the tend-where-they-lie option (undraft after) |
| Pawn orders | `order.py` - resolve, menu, force, attack, goto, rescue, tend, equip, haul, work, draft, undraft. Targets take `140 152`, `"140,152"`, `DefName@x,z` (blueprints included) or a thingId; use `combat.py` during a combat session; `haul` refuses `unreachable_storage` when reach is the cause and `no_storage` only when nothing accepts the item |
| Force one job now | `order.py force <pawn> <target> ["<substring>"] [--do]` runs the pawn's own right-click option, or the single `Prioritize ...`; `order.py work` only targets bill givers and refuses a wall, pointing at `force`; `force` and `menu` move the camera onto the target before the right-click, and an empty menu names what is at the cell and the pawn's priority for that work |
| Designations | `act.py labels [word]`; `act.py apply "<label>" x z [w h] --do`; `act.py hunt <animal-id> --do`; `act.py allow x z [w h]` (alias `unforbid`) fires the Allow gizmo and re-reads, so `still forbidden` is measured; `act.py tame <animal>` prints the target's ThingID and names other animals sharing the cell; each cell reads `NO-OP` (already designated), `APPLIED` or `REFUSED: <reason>`; `"Haul things"` designates rock chunks only — everything else is hauled by `order.py haul` |
| Removing a designation | `act.py undesignate x z [w h] --do` drags Cancel over the area and names any survivor (M: the in-game Cancel tool dragged over animals removes Tame/Hunt too; unverified through the bridge). An animal holds only one designation, so Hunt overwrites Tame — that is the un-designate Cancel cannot do; the report counts designations and blueprints/frames separately (Cancel destroys blueprints too) and reads back what survived |
| Clicks and the camera | `click_cell` reports success on an off-screen cell and does nothing; `pick.py` moves the camera, clicks and reads the selection back for `act.py allow`, `buildings.py gizmo`, `mini_install.py` and `map.py` |
| Building placement | `build.py <building> x z` previews rotations; add a direction and `--do` to place; an unknown def prints the bridge's own reason plus aliases and near-misses (`Conduit` is `PowerConduit`, `SculptorsTable` is `TableSculpting`); one verdict, on the LAST line, starting `==`: PLACED with the blueprint id, ALREADY THERE, REFUSED with the reason, or DRY RUN; rotation rows above it are detail; `--do` on a rotatable def with no direction refuses with all four verdicts and the command to re-run; filth is never a blocker |
| Trade | `trade.py` - traders, open, buy/sell, preview, accept, cancel; `accept` pauses supervised play on purpose, says so, and prints `python play.py start` to restart it |
| UI / typed dialog | `ui.py --surfaces` (marks World `TOGGLE`); `ui.py tab <name>` opens a main tab, reads it and closes it again (`--keep-open` leaves it over the map); `ui.py close [name]` is the manual close — `open_main_tab` is not a toggle; `ui.py click "<text>"` (text first) prints what opened or closed; `dialog.py` lists text boxes |
| Camera | `cam.py go x z [--zoom n]`; `cam.py zoom <rootSize>` - a smaller root size is closer: 11-15 close, 24-35 colony view, 45-60 wide (60 shows 45% of the map), ~127 the whole map; `cam.py base x z` pins home; `wide` makes a round trip; `--help` prints usage |
| Narration | `say.py "<line>" --mood <mood>` |
| Tool without a wrapper | `rim.py tools --grep <word>`, `rim.py detail <tool>`, `rim.py call <tool> '<json>'` |

A DOWNED pawn is alive, not a corpse; corpse rows carry `{FRESH}` or `{SKELETON}` individually. Room IDs can change when walls change; use positions or current names. Animal names may be ambiguous; use the printed ThingID. A ThingID names a stack, so `target_not_found` one call later means hauling or eating consumed it: re-read, or address the cell as `Steel@133,143`. A `{held}` item is not spawned and no id reaches it.

Map glyphs: on the `build` layer `#` is natural rock, `S` smoothed natural rock, `:` an ore vein and `H` a constructed wall. Terrain ores are `s` steel, `k` compacted machinery (components), `K` spacer machinery, `p` plasteel, `g` gold, `l` silver, `r` uranium, `j` jade, and `%` an unlisted ore named in the footer. `--layers items` draws plants too: `b` wild food, `h` healroot, `c` sown crop, `t`/`T` tree, `"` other, with a species census. The `build` layer draws plants on empty cells as `T` tree, `*` wild food, `h` healroot, `^` crop, `"` grass. `=` is a power conduit (wire only, neither source nor load) and `e` a powered building or source; a building standing on a conduit cell always wins the glyph. `s` is a shelf or other storage building: impassable, and a door opening onto one prints a `!!` line.

## Make and verify changes

Read a preview/refusal before changing an unfamiliar setting. Configuration and placement examples below preview without `--do`; **orders and trade acceptance act immediately** unless their command offers a dry-run option. An `order.py` read or dry run prints `DRY RUN - would be accepted (nothing issued)`; a real one prints `ORDER ISSUED` and the job read-back, and `ORDER ISSUED` with `!! NO JOB` is unconfirmed. Every `order.py` subcommand accepts `--do`; it only changes anything on `force`/`menu-do`, and `--dry-run` wins if both are given. Check `--help` rather than assuming every tool shares a default.

**A job read-back on a paused clock proves only that the job is queued.** Order paths read the clock first: `VERIFIED` / `ORDER ISSUED` mean time is running, `ORDER QUEUED -- CLOCK IS STOPPED` means the job waits for `play.py start`, `CLOCK UNKNOWN` is not running. `combat.py` pauses before each order by design, so its orders read QUEUED until `combat.py advance`.

```text
python pawns.py set <pawn> --work Cooking=1,Hauling=3 --do
python pawns.py set <pawn> --schedule <24-letters> --selftend on --do
python pawns.py set <animal-id> --train Obedience=on --do
python buildings.py set <bed> --owner <pawn> --do
python bills.py add <bench> "<recipe>" --forever --do
python research.py set "<project>" --do
python zones.py filter <zone> --preset food --do
python build.py Cooler <x> <z> east --do
```

- Work priorities: 0 disables, 1 is most urgent, 4 least. In checkbox mode, active work reports/applies as 3; read `manualPriorities` before interpreting the numbers. `--work` takes displayed labels (Artistic, Animals, Bed rest) as well as defNames, and a miss lists the valid names. Schedule letters are **A/W/J/S**: Anything, Work, Joy, Sleep.
- Bills: an accepted bill may still lack usable ingredients. Read its filter, allowed worker, skill, radius and repeat count; `0x` is finished. Inventory ownership and forbidden flags matter. Construction materials "delivered" do not mean total colony stock.
- Buildings: `--power` queues a flick designation, so a pawn must operate the switch. Cooler placement previews identify the hot and cold sides.
- Animals: training can change prerequisites/dependents; read the preview. Use `--slaughter on --do` only on the intended animal ID.
- Designations: stone chunks need Haul. Use `act.py apply "Haul things" x z --do`, then `order.py haul <pawn> <thingId>` if an immediate order is needed. Area sizes are sizes, not an opposite corner, and `act.py` accepts one spelling per call: positional `label x z w h`, `--width`/`--height`, or `--size W H`. `act.py` writes are dry runs until `--do` (`DRY RUN - would designate N cell(s)`); `APPLIED` appears only after a real apply, and warns `!! touches built structure at x,z`. `Cancel` on a finished build answers `NO-OP`, not a refusal.
- Watched writes select/open a relevant menu and close it afterward. `pawns.py set --do` skips its watch tab while the clock is running (it froze the picture for 8 s per write); `--watch` forces it, `--no-watch` always suppresses it. Read the `watch:` result.

For a stalled task, check the named blocker: forbidden item, missing designation, disabled/incapable worker, bill filter, fuel, power or reachability. `order.py menu` is read-only and provides the pawn's own available actions and reasons; `NO MENU OPENED` means RimWorld would take the direct action for that click, so use `order.py force`. For exposure, inspect health and room temperature and address shelter/heating/cooling promptly.

### Letters and UI

`letters.py open <id>` shows the options; `letters.py decide "<text>"` answers. A Close button alone is not a decision. `letters.py sweep` clears eligible announcements after ten real seconds and keeps decision/unreadable letters; `run.py` does not sweep for you. A camera-only letter has no dialog to answer: explicit `letters.py dismiss <id>` asks the game to right-click-dismiss it, without accepting a quest.

The quests seen here are **opportunity quests**: there is nothing to accept or decline, only "Jump to item stash". "No route to answer" means "nothing to answer" — take the stash if it is worth the walk and leave the letter alone. Do not spend turns hunting for a dialog, and do not read a standing opportunity quest as expired.

For a modal, read `python ui.py` to identify its text and buttons, then click the intended option:

```text
python ui.py <surface> click "<text>"
python ui.py click "OK"
python dialog.py "<text>" --accept --do
```

Read what "OK" acknowledges first. In Python, use `ui.click("text", surface="...")`. Prefer these helpers to raw UI handles. If raw access is necessary:

- Open tabs with `rimworld/open_inspect_tab` / `rimworld/open_main_tab`; close the main tab with `rimworld/close_main_tab`. Opening twice is not a toggle.
- UI IDs expire on every layout capture. `click_ui_target` takes `targetId`; an inert label is not the button. The helper pairs visible text with its actionable element.
- Gizmos use `list_selected_gizmos` row `id` as `execute_gizmo {gizmoId: id}`. Context-menu execution takes `label` or `optionIndex`.
- A checkbox with `isChecked: null` may be indeterminate. Use the direct work/settings tool where available.

### Trade

```text
python trade.py
python trade.py open <trader>
python trade.py buy <item> <count>
python trade.py preview
python trade.py accept
```

Use `sell` for sales and `cancel` to abandon staging. The trade sheet's OURS column is what this trader will accept; whole-map inventory can include caravan stock. Selling a pawn requires explicit `--allow-pawns`.

After acceptance, read goods back with `inv.py <item> --all-owners` to check actual location, ownership and forbidden flags. A watched trade dialog can stop supervised play: check `play.py status` and restart after handling the stop. `accept --no-watch` avoids the decorative dialog; `close-dialog` closes a stranded trade window.

## Combat

**Draft everyone when a threat is on the map, and put the non-combatants inside.** M, 2026-09-07, after Finn -- the only doctor, incapable of violence -- walked out into a manhunter wolf and was saved by a turret. An undrafted pawn keeps taking jobs, and those jobs route it across open ground past the thing that is hunting. Drafting is what parks a pawn where you put it; it is not only for the fighters. Undraft afterwards.

When a colonist is in immediate danger, give defensive orders before taking more broad reads. Use two capable fighters against a dangerous animal when available; move vulnerable pawns away. **Do not Hunt-designate predators or animals that just attacked.** Hunt sends a lone hunter; combat orders control the response. Otherwise **hunt continuously** rather than in bursts when the food alert fires: if there are huntable animals on the map, take them, unless it is a huge herd (M).

**A turret explodes when it is destroyed.** Placement needs three answers, and a coverage score gives none of them: which side of the wall it stands on, what is inside its blast radius, and who walks past it. Ours shot out seven cells of our own wall from behind it. (M)

```text
python combat.py begin
python combat.py draft <pawn>
python combat.py attack <pawn> <target>
python combat.py flee <pawn> <x> <z>
python play.py start --mode combat --ignore-hostile <known-enemy-id>
python combat.py tend <doctor> <patient>
python combat.py end
```

Combat play requires an active ledger and a known enemy ID, inferred from orders or explicitly supplied. A target need not be hostile to be attackable. Pawns incapable of violence can still be drafted to flee. Move orders move; attack orders fight. Ground tending can auto-draft the doctor; in-bed tending uses work.

During a fight, order, let guarded time advance, handle the next stop, and restart when ready. Keep reads short while someone is in melee. `combat.py advance 5` is a bounded fallback when supervision is inactive. A failed move/flee must be replaced with a verified order; an injury while fleeing requires a fresh decision. Respect a person's pause/speed change rather than blindly passing `--resume`.

`combat.py end` restores controller-drafted pawns; verify the ledger is closed and check who remains drafted. `release <pawn>` restores one early. Do not raw-undraft ledger pawns. `end` proceeds when every remaining hostile is DOWNED, printing who is down (finish off or capture) and who stays drafted; a conscious hostile still refuses and names `--force`, which is for that case or for discarding a stale ledger after loading a different save. If a refusal cannot be resolved, report the clock state and return within the turn budget.

## Practical tips

- **A mini-turret costs 100 steel + 3 components**, not the 70 in its def: 70 steel plus 30 of stuff, merged by the game. Every cost the instruments print is the merged one.
- **Check what a list includes.** Pawn detail flags usually narrow to colonists; `pawns.py --health --all` includes other living pawns. Filters combine, so read the footer before concluding something is absent. `buildings.py --all` includes ruins and unclaimed structures.
- **A full larder can still leave a bill blocked.** Meal filters commonly exclude insect and human meat. Read the exclusions printed by `bills.py` before adding another bill or assuming the food disappeared.
- **Storage is a separate question from possession.** If Low food and inventory disagree, inspect `zones.py --filter` for stored contents and what the stockpile accepts. Food on the map is not necessarily stored or usable.
- **An unchanged alert can hide worsening health.** Use `pawns.py --health` and `map.py rooms --cold` or `--hot`. For exposure, prioritize shelter and working heat/cooling over waiting for a clothing bill to finish.
- **Check self-tend on new joiners.** It is off by default. Read their settings and, when wanted, use `pawns.py set <pawn> --selftend on --do`; being assigned Doctor does not enable it.
- **Designate over an area, not over targets.** Drag `Harvest`, `Chop wood`, `Tame` or `Mine` across the whole region and let the game pick the valid targets: `act.py apply "Harvest" x z w h`. Do not locate each plant or tree first. Unripe plants are simply refused, so an empty result is an answer, not a failure.
- **Right-click to force work.** `order.py force <pawn> <x,z> "<label>" --do` runs the job now, instead of editing a work priority and waiting for the pawn to choose it.
- **Uninstall returns a minifiable building whole; deconstruct is lossy.** The mini-turret carries `<minifiedDef>`, so relocation should never deconstruct: `act.py clear`, then `buildings.py gizmo <id> "Uninstall" --do`, then force a builder onto the designation, then `Reinstall at...`. A misplaced building is not sunk cost. A shelf is the exception: it offers only `Reinstall at...`, so moving one means deconstructing it.
- **A shelf is two cells and impassable.** `map.py` can draw a conduit over the half standing in a doorway, so a door that opens onto solid shelf reads as a hauling problem rather than a wall — check the doorway cells themselves. Steel and plasteel do not rot and have no business on a roofed shelf (rygger_dracora, in chat).
- **Components come out of the ground.** They are mined from compacted machinery, `k` on the terrain layer. Check the map before rationing them. The machining table cannot *make* them — thirteen recipes, none of them components; that needs Fabrication. Read a bench's recipe list before spending components on the bench.
- **Roof the batteries, but not beside the solar array.** An uncovered battery shorts in the rain: two in the open threw three `Zzztt` and two fires in one turn. A roofed solar panel generates nothing, so do not extend a roof over the panels to reach them.
- **Growing period ends.** `world.py` gives it in one call; a field expanded a week before frost is wasted work.
- **A comms console is useless without an orbital trade beacon.** (M)
- **An inspiration is a live state, not history.** "Go frenzy" sat running with 5.5 days left while three turns swept past its letter as old news. Check the pawn, then give them the work it speeds up.
- **Vanilla refuses some placements the preview will not explain.** Steel spike traps refuse orthogonally adjacent placement.
- **A downed wild animal is a countdown, not banked meat.** It gets back up and walks away: two donkeys and ~280 meat were lost while the session counted them as stored food. Kill and butcher it, or let it go.
- **Count the upkeep before building for a maybe.** A prison built before there is a prisoner, and a captive eating 2.8 days of the colony's food, are running costs no placement preview shows.
- **Plant-label percentages are hit points, not ripeness.** `act.py apply "Harvest" x z` previews whether harvesting is possible. Harvest and Chop wood are different designations; a crop symbol alone does not prove readiness.
- **Recheck old mining and hauling designations.** `map.py x z --layers desig` shows what remains queued. Newly opened passages can expose old orders; distant jobs can send workers far beyond the area you intended.
- **Address the building directly.** Building and bill tools accept a ThingID or `DefName@x,z`, avoiding awkward multi-cell selection. `buildings.py gizmos <thing>` lists its controls without firing them.
- **Rename without a dialog.** `pawns.py set <pawn> --nickname "<name>" --do` works for colonists and tame animals.
- **Trading need not wait for a walk.** The bridge trade path does not require the negotiator to stand beside the trader by default. Choose the negotiator for their trading ability; walking over is optional.
- **Equipping can consume combat time.** `combat.py equip` may advance guarded time while the pawn fetches the weapon. Give an endangered pawn its flee order first.
- **For a small Python helper:** game tools use `rim.game(name, args)` and GABS tools use `rim.tool(name, args)`; there is no `rim.call()`. Keep full names such as `rimworld/...` and `rimbridge/get_bridge_status` when calling tools.

## What forty turns taught (2026-09-07)

Seven things, kept short because everything situational is in [BUGS.md](BUGS.md).

- **Verify with a measure that could see the failure.** `notConnectedToPower=0`
  cannot see an orphaned battery, because a battery does not consume — three
  turns of clean power checks passed over a whole disconnected wing. Before
  trusting a check, ask what it would read if the thing were broken.
- **An error message names a cause, not the cause.** Nine sightings in one night
  of a refusal that reads as a hard failure and means something else, including
  a working instrument that reported itself unregistered. Read a refusal as a
  lead. Never conclude an instrument is dead from one message.
- **When something that should work doesn't, rebuild it elsewhere before proving
  it harder.** Two turns probed a turret's wiring cell by cell and both concluded
  it was intact; a fresh line sharing none of those cells lit it first try.
- **A shortage is usually distribution, not production.** Firewood on the ground
  with a cold stove, a ripe field with four days of food, components that could
  not be hauled into a full storeroom. Check reach and storage before designating
  more of the thing.
- **At priority 1, RimWorld breaks ties by work-list order.** A pile of urgent
  work starves whatever sits lowest in the list. Re-rank rather than disable, and
  remember colonists take the nearest designation — distant work needs
  `order.py force` or it never happens.
- **Push any long read to a Scout, and never wait on one.** A Hands that blocks on
  its own Scout is a frozen picture; spawn it, keep playing, take the verdict.
- **Say the line before the call, not after the decision.** Narration that is a
  reminder drifts within a minute every time. Anchored to the tool call, it holds.

And the one that outranks all of them: **the people watching see what no
instrument was asked.** Chat and M caught the turrets' real positions, a
component seam in an inner corner, a turret placed behind its own wall, batteries
standing in the rain, a field sown a week before frost, and an unmined junction
at the centre of a conduit T. Ask them, thank them by name, and check what they
say rather than assuming it is already covered.

## Narration and overlay

**Snap the camera to where the action is, and to whatever you are looking at** (`cam.py go x z [--zoom n]`). M asked for this on 2026-09-07: the viewers only ever see the frame, so a correct decision taken off-screen looks like nothing happening. Move the camera before the order, not after.

Hands narrates decisions and events; Core posts the handback card. Use short first-person lines, roughly 50-150 characters, and leave several seconds between posts. Moods: `happy`, `thinking`, `sad`, `scared`, `welp`, `excited`, `angry`, `veryhappy`. Letters reach the overlay automatically when read by the watch instruments; do not duplicate those posts.

The overlay is optional; its failure does not stop play. To start it, run `python server.py --port 8090` from `C:\Home\Fable 5\made\stream-overlay`, using `127.0.0.1:8090`.

## Save and finish

Hands saves only when the turn explicitly asks. Use a new descriptive Lampblack name to preserve rollbacks; never write an Autosave slot:

```text
python rim.py call rimworld/save_game '{"saveName":"Lampblack - day 60, milestone"}'
```

Use **`saveName`**, not `fileName`; verify the returned name and `path`.

At session end, record WEIRD in [BUGS.md](BUGS.md), update [CHRONICLE.md](CHRONICLE.md), and journal in `C:\Home\rimworld\journal`. Stop RimWorld through GABS only while status confirms the bridge is connected to the owned process; verify it exited and leave GABS as found. Commit the session records.

For unresolved instrument behavior, consult [BUGS.md](BUGS.md). For rare UI workflows, use the relevant section of [the extended notes](../../notes/rimworld-from-claude-code.md). Build instructions, payload schemas and implementation hazards belong in [INSTALL.md](../companion/INSTALL.md), not the turn loop.
