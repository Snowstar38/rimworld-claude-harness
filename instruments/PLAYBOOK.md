# PLAYBOOK - RimWorld operating manual

Run commands from `C:\Home\rimworld\instruments`. Read this at session start, then the selected [session mode](#session-mode) and [CHRONICLE.md](CHRONICLE.md). Hands inherits that context and reads [hands.md](hands.md) for its turn instructions. Use each instrument's `--help` for options; source contracts and maintenance details live in [INSTALL.md](../companion/INSTALL.md).

## Start the session

```text
python setup.py --load "<save name>"
python setup.py --newest Lampblack
python setup.py --status
```

Choose one load command. Setup starts the stack and loads paused; `--status` only reports. **Exit 2 means an unconnected RimWorld process may be M's own game: stop and ask her.** Do not surround setup with bare `games_start` or `games_stop` calls.

Use this colony's `Lampblack - ...` saves. `Wayside - ...` belongs to Sol; shared `Autosave-1..5` files are not session saves. Saves live in `C:\Home\rimworld\bridge\profile\Saves`. `setup.py --void "<name>" --reason "<why>"` marks a repudiated save so newest-save loading skips it; `--unvoid` reverses it.

`python stream.py go` runs **once per session**, after the mode. If setup or `stream.py status` prints `!! binding : DEAD`, run `python stream.py bind-check --repair` first.

### Session mode

Read the mode M selected; `python stream.py mode` reports it.

| Mode | Instrument problems |
|---|---|
| [Live](modes/live.md) | Play around them; record WEIRD. No code repair or debugging. Reading an order's refusal is ordinary gameplay. |
| [Practice](modes/practice.md) | A narrated investigation may take up to one minute, with time running. Record anything needing a patch. |
| [Workshop](modes/workshop.md) | Pause, inspect, repair and test as needed. |

One-agent ownership, human pauses and valid blocked-clock handbacks apply in every mode.

## Run the turn loop

**Only one Hands controls the game and UI.** Core sets goals, decides and reads handbacks; it does not call game instruments except for setup/status, stack recovery and shutdown. Scouts may make reads that do not select, click, pause, change speed or need time to advance. The scheduler dispatches blind Lookouts automatically.

1. Core runs `python stream.py go` once, then sets session goals, **each at or under 82 characters** (the bottom bar truncates longer ones):

   ```text
   python stream.py goals --long "..." --short "..."
   ```

2. Before spawning a background Hands fork, open its turn:

   ```text
   python stream.py hands-start --goal "<goal>"
   ```

   Brief the fork: "Read `C:\Home\rimworld\instruments\hands.md`, then take the turn: <goal>." Hands runs `hands-claim` once; Core leaves gameplay and narration to it. The goal is where the turn starts, not a checklist that ends it.

3. **A turn is six minutes of wall time, and Hands plays until the system says so.** The hook warns at five minutes and reports overdue after six. A finished brief or a mid-turn message from M does not end the turn; a human pause, an unstartable clock or `TaskStop` does. Hands writes `state\hands-last.md` once at the end and returns roughly 300 tokens: CHANGED / NEEDS DECISION / WEIRD / NOTES.

4. Native stop pauses supervised play and returns event routing to Core. Core waits for completion, then publishes the handback, a story-so-far card of about 220-300 characters, and opens the next turn:

   ```text
   python stream.py handback --summary "..." --mood welp --short "..."
   ```

**Before sending a fork any message, run `python stream.py hands-check`.** Never message a completed fork: that restarts it. Use `TaskStop` to end a turn early; do not start another while the previous turn is running or unresolved.

Native routing delivers viewer messages and watcher reports. Do not poll the scheduler, inbox or overlay. Send bulk one-off reads to Scouts. Reserve `consult.py "<question>"` for a session-threatening harness failure with no documented workaround; run it in the background and keep playing.

### What Core carries

Core is the thin thread between turns, not the commander. Everything Hands reads dies at the handback.

- **No reading out of curiosity.** No git history, no instrument source, no logs or transcripts.
- **No game reads from Core**, except recovery: `hands-check`, `bind-check`, `play.py status`, `hands-release`.
- **Do not carry figures forward.** An exact number may come only from the most recent turn's summary. A state ("low on food") is usually more useful than an inventory.
- **Turn 1's goal is "get oriented".** Hands sets the real focus once it has seen the map.

### Recover an orphaned turn or a stale binding

An old turn is not proof of exit: confirm in the native task list that no Hands is running. Then `stream.py hands-close --reason "..."` stamps the turn closed, and `stream.py hands-release --no-pause` clears a route left by a dead fork. Do not use `stream.py reset` for this. `hands-check` says whether `hands-last.md` is FINAL or INTERMEDIATE; never brief off an INTERMEDIATE one.

If `hands-claim` refuses with `runtime binding is STALE`, Core runs `python stream.py bind-check --repair`. A Hands cannot fix this; it reports the line verbatim and hands back.

## Control the clock

```text
python play.py start --speed Normal
python play.py start --ignore-hostile              # bare = all; or a name, or a ThingID
python play.py start --ignore-predator             # a predator already hunting one of ours
python play.py speed Fast
python play.py status
python play.py pause
```

Keep time running while Hands reads, thinks and narrates. Normal, Fast and Superfast are available (`1`, `2`, `3`); **do not use Ultrafast**. Supervised play auto-pauses on a threat, a serious injury, a death or a decision, and its stop line says why. Handle the event, then explicitly `play.py start`; a guard stop never restarts itself. `run.py` and `combat.py advance` are bounded diagnostic steps, not commands to chain. Do not use bare `play_for` or `play_until_letter`.

| Why time stopped | What to do |
|---|---|
| A person paused or changed speed | Respect their control; do not automatically resume. `external_pause` means only that the pause came from outside supervised play, not that M is at the keyboard. |
| A threat, injury, death or decision | Deal with it, then `play.py start`. Break contact with an injured pawn rather than restarting into it: move or undraft them so they seek care. |
| A refusal names threats | It lists every hostile and hunting predator with a paste-ready `--ignore-hostile` / `--ignore-predator` line. Acknowledge only with a defensive plan. A sleeping hostile far from everyone is listed as `hostile_dormant` and blocks nothing. A wild predator that is not hunting one of ours is never a reason to stop, at any distance; the `X is being attacked by Y!` message is, always. |
| `FORCE_PAUSED` names a window | A game popup is holding the clock. Clear it with `python ui.py click "OK"` (a miss lists the clickable texts), then start. Letter dialogs and trade windows force-pause too. |
| A modal or other blocker prevents safe play | Read the named refusal. **A blocked clock is a valid handback state:** report the reason and next action, and return within budget. |
| `play.py start` failed writing its state file | `play.py pause`, then `play.py start`. |
| `run.py` while supervised play is alive | It refuses; `play.py pause` first if you want `run.py`. |
| A turn ended | Leave the boundary paused; the next Hands starts supervised play after handling any blocker. |

`status.py --brief` names why the clock stopped even after the service has exited. Only `PAUSED (... nothing recorded a stop)` means simply start it. Neutral, positive and negative announcements never stop time; a social fight's start relays without stopping and its wounds are suppressed while it lasts (a fight already over when probed can still stop). High alerts never pause play; a Critical alert stops it through its own announcement message, with the Critical `alert_new` relayed (wake) before the stop.

## Choose an instrument

Prefer direct instruments over opening tabs. Use whole-map pawn/item lists to find things; use `map.py` for spatial questions. An unreadable or failed result is not zero and is not an all-clear. An unknown flag stops a call rather than being ignored.

Lookout leads are visual hypotheses (`SEEN:` on screen, `EVIDENCE:` inferred); check every lead against `alerts.py`, `pawns.py --health` or `status.py --brief` before acting. Alerts are the right-edge column; the Learning Helper panel is never a lead. Zero leads is a normal result.

| Need | Command / useful options |
|---|---|
| Colony board | `status.py` - clock, letters, alerts, colonists, threats and open UI; `--brief`, `--explain` |
| Colonists and animals | `pawns.py --roster`; combine `--health`, `--work`, `--gear`, `--bio`, `--schedule`, `--skills`; a bare name filters every block; `--animals`, `--wild`, `--tame`, `--threats`, `--hostile`, `--downed`. Every animal row carries its ThingID (`id:Ibex404123`); a ThingID positional is an address, not a name filter. Ghouls are ours and appear in the roster marked `ghoul` |
| Active alerts / fire | `alerts.py` is the fire read; `inv.py` cannot see fire, filth or buildings |
| Inventory and ownership | `inv.py <item>` - ours by default; `--all-owners`, `--forbidden`, `--near x z r`, `--corpses`. The match is a substring of the singular label; a miss re-asks the whole map and says what matched, so `0 haulable matches; 98 plants match` is an answer |
| Food inventory | `inv.py food` - read the `DAYS OF FOOD` line, not the unit column |
| Buildings / construction | `buildings.py --pending` or `--inspect`; `!! SHORT` means colony stock is below what is owed. `buildings.py gizmos <thing|pawn>` reads a gizmo bar; `gizmo <thing|pawn> "<label>" --do` fires one. `buildings.py --power` lists power nets and warns `NO GENERATOR`. `buildings.py at x,z` is a coordinate query; `--match X --center X,Z --radius N` is the scope form. `reinstall <thing> x z --do` moves furniture; `rotate <blueprint|frame> <dir> --do` turns a blueprint; `--rock` reads rock and ore; `--all` includes ruins |
| Bill queues | `bills.py [bench]`; `bills.py recipes <bench|x,z>`; read CAN RUN / CANNOT RUN and the filter exclusions. `bills.py add <bench|x,z> "<recipe>" --repeat N`; `bills.py <bench> set <n> --target 30 --do`; `bills.py <bench> --forever --do` for a bench's only bill. Ambiguity is never guessed: several bills print numbered with a paste-ready command each. A `--do` that reached no write prints `WROTE NOTHING:` and why |
| Research | `research.py`; `--locked`, `--finished`, `--unlocks`; `research.py set "<project>" --do` |
| Local terrain and designations | `map.py <x> <z> --layers desig,rooms`; `--size W H`, `--corner`, `--legend`, `--full`; `map.py areas x z W H` shows roof, home and allowed areas |
| Rooms and temperature | `map.py rooms --cold` or `--hot`; `map.py room <x> <z>` |
| World tile | `world.py` - biome, temperatures, growing period, nearby settlements; `--show 8` reveals the planet for 8 s |
| Stockpiles and zones | `zones.py --filter`; `zones.py crop <zone|x,z> Plant_Rice --do`; `zones.py create` refuses an overlap unless `--merge` or `--replace` |
| Letters | `letters.py` — rows are CHOICE / OPEN / INFO / ANNOUNCE with an age. `STALE` (2 h or older) is history: verify with `alerts.py` or `pawns.py --threats`. INFO is an opportunity quest with nothing to answer. `open <id>`, `decide "<text>"`, `sweep`, `dismiss <id>` |
| Gear | `gear.py <pawn>`; `gear.py <pawn> drop "<item>" --do` works paused |
| Pawn orders | `order.py` - attack, goto (alias `move`), rest, rescue, tend, equip, haul, work, deploy, draft, undraft, force, menu. The PAWN comes first; targets take `x z`, `"x,z"`, `DefName@x,z` or a thingId. `goto` drafts and leaves the pawn drafted unless `--undraft`. `rest <pawn> [<bed>]` undrafts and beds them. `tend <doctor> <patient>` rescues to a bed then tends; `--draft` tends where they lie. `force <pawn> <target> ["<label>"] --do` runs the pawn's own right-click option and lists the labels on a miss; `menu` only reads |
| Deploy a worn pack | `order.py deploy <pawn> <x> <z> --do`. The cell must hold no building at all and be in line of sight within 22.9 tiles; a refusal lists nearby cells that work. Never click the gizmo for this |
| Designations | `act.py labels [word]`; `act.py apply "<label>" x z [w h] --do`, or the label as a verb (`act.py mine 106 127`). `act.py hunt <animal-id> --do` and `act.py tame <animal-id>` write onto the animal by ThingID and clear its other designation. `act.py allow x z [w h]` unforbids; `act.py uninstall <x z | id> --do` keeps a minifiable building whole; `act.py undesignate x z [w h] --do` cancels over an area, `act.py undesignate <animal> --do` clears an animal's marks. `"Haul things"` designates rock chunks only; everything else is `order.py haul` |
| Clicks and the camera | A pawn is selected by id, never by clicking its cell. `click.py --cell <x> <z>` is the real mouse at a map cell, `click.py <x> <y>` at a pixel, `click.py --key esc` a real keypress; it verifies the game has focus first and clicks nothing if it cannot. `cam.py go x z` first if the cell is off screen |
| Building placement | `build.py <building> x z` previews rotations; add a direction and `--do` to place. The last line is the verdict: PLACED, ALREADY THERE, REFUSED with the reason, or DRY RUN, with `would wipe/replace:` when a placement removes something. A line or a room is ONE call: `--to <x2> <z2>` for a straight run, `--cells "x,z;x,z"` for any shape (cap 200); the verdict counts placed / already there / refused |
| Trade | `trade.py` - traders, open, buy/sell, preview, accept, cancel; `accept` pauses supervised play and prints the restart line |
| UI / typed dialog | `ui.py --surfaces`; `ui.py tab <name>` opens, reads and closes a main tab; `ui.py click "<text>"` (text first) prints what opened or closed; a button with no text is `--index N` / `--rect x,y,w,h`; `dialog.py` lists text boxes. A gizmo is clicked with the real mouse via `ui.py click` and it refuses rather than guessing; `ui.py targeter` reads an open targeter and `--cancel` closes it (costs the selection) |
| Camera | `cam.py go x z [--zoom n]`; `cam.py zoom <rootSize>` - 11-15 close, 24-35 colony view, 45-60 wide; `cam.py base x z` pins home |
| Coordinate grid on screen | `grid.py on --do` / `off --do`; `--step 5`, `--no-labels`, `--color yellow`. **Viewers see this** |
| Narration | `say.py "<line>" --mood <mood>`; omitting `--mood` sends `happy`; `--mood none` is faceless |
| Tool without a wrapper | `rim.py tools --grep <word>`, `rim.py detail <tool>`, `rim.py call <tool> '<json>'` |
| Raise a situation on purpose (WORKSHOP ONLY) | `debug.py raid|manhunter|trader|flare|letter|fight A B|wound <pawn>|down|kill|heal|alert|spawn <def> x z`, every write a dry run until `--do`, refused in live mode. `debug.py find <words>` searches the debug tree; `run "<path>" [--pawn <name>] [--cell x z] --do` is the raw form; paths are node labels joined with a backslash. Never save over a real save afterwards |

A DOWNED pawn is alive, not a corpse. Room IDs can change when walls change; use positions or current names. Animal names may be ambiguous; use the printed ThingID. A ThingID names a stack, so `target_not_found` one call later means it was consumed: re-read, or address the cell as `Steel@133,143`.

Map glyphs: on the `build` layer `#` is natural rock, `S` smoothed rock, `:` an ore vein, `H` a constructed wall, `=` a power conduit, `e` a powered building, `s` a shelf or other storage (impassable). Terrain ores are `s` steel, `k` compacted machinery (components), `K` spacer machinery, `p` plasteel, `g` gold, `l` silver, `r` uranium, `j` jade, `%` an unlisted ore named in the footer. Plants on `items`: `b` wild food, `h` healroot, `c` sown crop, `t`/`T` tree.

## Make and verify changes

Read a preview or refusal before changing an unfamiliar setting. Configuration and placement examples preview without `--do`; **orders and trade acceptance act immediately** unless the command offers a dry run. `ORDER ISSUED` with `!! NO JOB` is unconfirmed. A job read-back on a paused clock proves only that the job is queued: `ORDER QUEUED -- CLOCK IS STOPPED` waits for `play.py start`.

```text
python pawns.py set <pawn> --work Cooking=1,Hauling=3 --do
python pawns.py set <pawn> --schedule <24-letters> --selftend on --do
python pawns.py set <animal-id> --train Obedience=on --do
python buildings.py set <bed> --owner <pawn> --do
python bills.py add <bench> "<recipe>" --forever --do
python bills.py <bench> set <index> --target 30 --do
python bills.py <bench> allow <index> Corpse_Human --do
python research.py set "<project>" --do
python zones.py filter <zone> --preset food --do
python build.py Cooler <x> <z> east --do
```

- Work priorities: 0 disables, 1 is most urgent, 4 least; in checkbox mode active work is 3. `--work` takes displayed labels or defNames. Schedule letters are **A/W/J/S**: Anything, Work, Joy, Sleep.
- Bills: an accepted bill may still lack usable ingredients. Read its filter, worker, skill, radius and repeat count; `0x` is finished. A write that moves nothing prints `WROTE NOTHING:` and the field.
- Buildings: `--power` queues a flick designation, so a pawn must operate the switch. Cooler previews identify the hot and cold sides.
- Animals: training can change prerequisites; read the preview. Use `--slaughter on --do` only on the intended animal ID.
- Designations: stone chunks need `act.py apply "Haul things" x z --do`. Area sizes are sizes, not an opposite corner. `Cancel` on a finished build answers `NO-OP`.
- Watched writes open a relevant tab and close it afterward; `pawns.py set --do` skips the watch while the clock is running (`--watch` forces it).

For a stalled task, check the named blocker: forbidden item, missing designation, disabled or incapable worker, bill filter, fuel, power or reachability. `order.py menu` shows the pawn's own available actions and reasons; `NO MENU OPENED` means the click would act directly, so use `order.py force`. For exposure, check health and room temperature and fix shelter, heating or cooling promptly.

### Letters and UI

`letters.py open <id>` shows the options; `letters.py decide "<text>"` answers. A Close button alone is not a decision. Every letter dialog force-pauses the game, so `open` and `decide` print the restart line. A letter whose every decision is DISABLED is refused; `letters.py dismiss` refuses a letter somebody has to answer unless you pass `--anyway`. Quests seen here are **opportunity quests**: nothing to accept or decline, only "Jump to item stash". Do not spend turns hunting for a dialog.

For a modal, read `python ui.py` to identify its text and buttons, then click the intended option:

```text
python ui.py click "OK"
python dialog.py "<text>" --accept --do
```

Read what "OK" acknowledges first. `dialog.py --accept` verifies the dialog actually closed and says `accepted: NO` when it did not; `ui.py click "Accept"` is the fallback. `pawns.py set <pawn> --nickname "<name>" --do` renames without any dialog.

### Trade

```text
python trade.py
python trade.py open <trader>
python trade.py buy <item> <count>
python trade.py preview
python trade.py accept
```

Use `sell` for sales and `cancel` to abandon staging. The trade sheet's OURS column is what this trader will accept. Selling a pawn requires `--allow-pawns`. After acceptance, read goods back with `inv.py <item> --all-owners`. `accept --no-watch` skips the on-screen dialog; `close-dialog` closes a stranded trade window. The negotiator need not walk to the trader; choose them for trading skill.

## Combat

**Draft everyone when a threat is on the map, and put the non-combatants inside.** An undrafted pawn keeps taking jobs, and those jobs route it across open ground past the thing that is hunting. Undraft afterwards. (M)

When a colonist is in immediate danger, give defensive orders before taking more reads. Use two capable fighters against a dangerous animal; move vulnerable pawns away. **Do not Hunt-designate predators or animals that just attacked**; combat orders control the response. Otherwise **hunt continuously** rather than in bursts when the food alert fires, unless it is a huge herd. (M)

**A turret explodes when it is destroyed.** Placement needs three answers: which side of the wall it stands on, what is inside its blast radius, and who walks past it. (M)

```text
python combat.py begin
python combat.py adopt <pawn>          # a ghoul / colony mech / late joiner
python combat.py draft <pawn>
python combat.py attack <pawn> <target>
python combat.py flee <pawn> <x> <z> [--advance 20]
python play.py start --mode combat --ignore-hostile <known-enemy-id>
python combat.py tend <doctor> <patient>
python combat.py end
```

Combat play needs an active ledger and a known enemy ID. A target need not be hostile to be attackable. Pawns incapable of violence can still be drafted to flee. **Every combat.py command leaves the clock paused** and its last line names both ways back: `combat.py advance 20` inside the fight, `play.py start` outside it. `advance` is capped at 20 s. A failed move or flee must be replaced with a verified order; an injury while fleeing needs a fresh decision. Respect a person's pause or speed change.

`combat.py end` restores controller-drafted pawns; `release <pawn>` restores one early. Do not raw-undraft ledger pawns. Only a conscious hostile within 50 cells refuses `end`; `--force` is for a genuine conscious threat or a stale ledger after loading a different save.

## Practical tips

- **A mini-turret costs 100 steel + 3 components**, not the 70 in its def. Every cost the instruments print is the merged one.
- **Check what a list includes.** Pawn detail flags usually narrow to colonists; `pawns.py --health --all` includes other living pawns. Read the footer before concluding something is absent. `pawns.py --json` leaves grazing animals out by default and counts them in `omittedAnimals`.
- **A full larder can still leave a bill blocked.** Meal filters commonly exclude insect and human meat. Read the exclusions before adding another bill. Do not carry a meat number across a turn boundary.
- **Storage is a separate question from possession.** If Low food and inventory disagree, inspect `zones.py --filter`.
- **An unchanged alert can hide worsening health.** Use `pawns.py --health` and `map.py rooms --cold` or `--hot`.
- **Check self-tend on new joiners.** It is off by default: `pawns.py set <pawn> --selftend on --do`.
- **Designate over an area, not over targets.** Drag `Harvest`, `Chop wood`, `Tame` or `Mine` across the whole region and let the game pick the valid targets. Unripe plants are simply refused.
- **Right-click to force work.** `order.py force <pawn> <x,z> "<label>" --do` runs the job now.
- **Uninstall returns a minifiable building whole; deconstruct is lossy.** `act.py uninstall <x z | id> --do`, then force a builder onto it, then `Reinstall at...`. A shelf moves in one step with `buildings.py reinstall`. A cooler is not minifiable; a heater is.
- **A shelf is two cells and impassable.** Check doorway cells themselves. Steel and plasteel do not rot and have no business on a roofed shelf.
- **Components come out of the ground.** They are mined from compacted machinery, `k` on the terrain layer. The machining table cannot make them; that needs Fabrication.
- **Roof the batteries, but not beside the solar array.** An uncovered battery shorts in the rain; a roofed solar panel generates nothing.
- **Growing period ends.** `world.py` gives it in one call.
- **A comms console is useless without an orbital trade beacon.** (M)
- **An inspiration is a live state, not history.** Check the pawn, then give them the work it speeds up.
- **Steel spike traps refuse orthogonally adjacent placement.**
- **A downed wild animal is a countdown, not banked meat.** Kill and butcher it, or let it go.
- **Count the upkeep before building for a maybe.** A prison before there is a prisoner is a running cost.
- **Plant-label percentages are hit points, not ripeness.** Harvest and Chop wood are different designations.
- **Recheck old mining and hauling designations.** `map.py x z --layers desig` shows what remains queued; newly opened passages expose old orders.
- **Address the building directly.** Building and bill tools accept a ThingID or `DefName@x,z`.
- **Equipping can consume combat time.** Give an endangered pawn its flee order first.
- **For a small Python helper:** game tools use `rim.game(name, args)` and GABS tools use `rim.tool(name, args)`; there is no `rim.call()`.

## Operating lessons

Situational detail lives in [BUGS.md](BUGS.md).

- **A perimeter is ONE call, not a loop.** `build.py Wall <x> <z> --to <x2> <z2> --do --stuff BlocksSandstone` places the whole run; a refused cell does not stop the rest.
- **A Scout's or Lookout's census is a LEAD, not a fact.** Verify with a direct read before it goes into a brief or on air.
- **Verify with a measure that could see the failure.** Before trusting a check, ask what it would read if the thing were broken.
- **An error message names a cause, not the cause.** Read a refusal as a lead; never conclude an instrument is dead from one message.
- **When something that should work doesn't, rebuild it elsewhere before proving it harder.**
- **A shortage is usually distribution, not production.** Check reach and storage before designating more of the thing.
- **At priority 1, RimWorld breaks ties by work-list order.** Re-rank rather than disable. Colonists take the nearest designation, so distant work needs `order.py force`.
- **Push any long read to a Scout, and never wait on one.** Spawn it, keep playing, take the verdict.
- **Say the line before the call, not after the decision.**
- **The people watching see what no instrument was asked.** Ask them, thank them by name, and check what they say.

## Narration and overlay

**Snap the camera to where the action is** (`cam.py go x z [--zoom n]`) before the order, not after; the viewers only ever see the frame. (M)

Hands narrates decisions and events; Core posts the handback card. Use short first-person lines, roughly 50-150 characters, and leave several seconds between posts. Moods: `happy`, `thinking`, `sad`, `scared`, `welp`, `excited`, `angry`, `veryhappy`. Letters reach the overlay automatically; do not duplicate those posts.

The overlay is optional; its failure does not stop play. To start it, run `python server.py --port 8090` from `C:\Home\Fable 5\made\stream-overlay`.

`grid.py on --do` draws a labelled coordinate grid over the map so a viewer can say `104,127`; leave it off between the moments chat is being asked to point at something.

## Save and finish

Hands saves only when the turn explicitly asks. Use a new descriptive Lampblack name; never write an Autosave slot:

```text
python rim.py call rimworld/save_game '{"saveName":"Lampblack - day 60, milestone"}'
```

Use **`saveName`**, not `fileName`; verify the returned name and `path`.

At session end, record WEIRD in [BUGS.md](BUGS.md), update [CHRONICLE.md](CHRONICLE.md), and journal in `C:\Home\rimworld\journal`. Stop RimWorld through GABS only while status confirms the bridge is connected to the owned process; verify it exited and leave GABS as found. Commit the session records.

For unresolved instrument behavior, consult [BUGS.md](BUGS.md). For rare UI workflows, use [the extended notes](../../notes/rimworld-from-claude-code.md). Build instructions, payload schemas and implementation hazards belong in [INSTALL.md](../companion/INSTALL.md).
