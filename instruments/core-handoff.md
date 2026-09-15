# core handoff — errata, live session, 2026-09-07 (day 68–71 stream)

Written by Core mid-session so a context compaction cannot lose the expensive
parts. **Live reads beat this file; this file beats CHRONICLE.md tonight.**

## Who and what

I am **errata**, playing RimWorld on stream. M launched me from
`C:\Home\errata.bat`; the contract is `errata.md` in this folder. Mode is
**live** — `modes\live.md` governs, and it forbids debugging outright.

**Core is the thin thread between turns.** It sets goals, spawns Hands forks,
reads handbacks, posts cards, acks events. Core does **not** call game
instruments except setup/status, `hands-check`, `bind-check`, stack recovery and
session shutdown. Hands narrates; Core does not.

## The turn loop

```
python stream.py hands-start --goal "..."
# spawn subagent_type: fork, briefed to read hands.md
python stream.py handback --summary "..." --mood <mood> --short "..."
python stream.py goals --short "..." --long "..."
python event_bus.py ack <receipt>
python stream.py hands-check      # ALWAYS before SendMessage to a fork
```

- Summary 220–300 chars, **hard cap 320**.
- Moods: happy, thinking, sad, scared, welp, excited, angry, veryhappy.
- Never SendMessage a completed fork — it restarts it.
- **Do not re-run `python stream.py go`.** It already ran; it would reset the
  turn counter.
- SendMessage is a deferred tool: `ToolSearch("select:SendMessage")`.

**Brief shape — M corrected me on this at turn 29.** Give the situation
and the stake, not a checklist. Turns briefed as bulleted task lists came back
at 4:36 and 4:39; the goal is where the turn starts, not where it ends. Keep the
instrument-edges list — that part is cheap and saves real time.

**Do not carry figures forward.** An exact number may come only from the most
recent handback. I broke this and put "269 venison" in five consecutive briefs
off a number that had already been eaten — it justified parking the hunt for
five turns while raw food sat at 1.7 nutrition.

## Chat roster — answer people BY NAME

M is **snowstar38**. She wants us markedly more responsive to chat, and
tonight chat's hit rate is better than our instruments'.

- **rygger_dracora — goes by R&D**, by their own offer. Right every single time
  tonight. Writes mods (1740 active users). Called the walled-in conduit at
  131,139 no instrument could see, the heater behind the Zzztt, the shelf
  filters three times, prison-before-needed, the workbench in the kitchen
  doorway, and buried conduit for open crossings with normal conduit in walls.
- **claude_plays_rimworld — this is M** (she said so at the keyboard,
  turn 44). Same person as snowstar38. Do not count her two accounts as two
  independent confirmations. 1500 hours. Argued us out of the cooler and was
  right; supplied the muffalo history that keeps the six-muffalo herd unhunted;
  went four-for-four on screenshot reads earlier in the night.
- **zriatt** — called the Storage tab two turns before we used it, and that
  unlinked shelves each count as their own stockpile. Corrected the
  conduits-in-the-rain theory: plain conduits are NOT rain-affected.
- **nogoodnick_** — running the butter / rhinoctopus bit with R&D.

## The night's pattern — seventeen misfilings

**The crisis was never where we were looking.** Seven "shortages" were
deliveries. A roof over the thing that needs sky, four times. A corpse debuff on
the wrong pawn. A stale zone name that nearly got a healthy field deleted. A
full-of-food room read as a wardrobe. A shelf's contents read as a filter. Three
turns of telling chat "no instrument can reach that" without once trying the
click. And finally our own paperwork: venison that had already been eaten.

Before believing a shortage, ask **where the thing actually is** and **who was
supposed to carry it**. Hauling and Research sit at the BOTTOM of the work list,
so a pawn with five jobs tied at priority 1 never hauls.

## Standing facts — do not re-litigate

- The growing zone named **"Cotton" is set to rice and is FINE. DO NOT DELETE
  IT.** `zones.py` has no rename; `zones.py create` silently eats an overlapping
  zone's cells and deregisters it.
- **Longhoff is a Night owl** — leave him on nights.
- The **sun lamp is minified at 117,151**, waiting on power it does not have
  (2900W against the margin). Geothermal pays for it, nothing else.
- **Allow-all shelves in a cooled room accept meat fine** — shelf filters
  stopped mattering. Link settings needs multi-select the bridge lacks. Do not
  spend another turn there.
- **The six-muffalo herd stays unhunted.** A manhunter muffalo herd nearly wiped
  this colony and took Octave's toe.
- Do not Hunt-designate predators; Hunt sends a lone hunter.
- The **megasloth at 168,205** is ~800 meat, 29 cells out, declined three times.
  A live option and a colony-killer on revenge.

## Instrument edges — WEIRD-line only, never chase (live mode)

- **The clock can stop silently mid-turn**, surfacing only as
  `ORDER QUEUED -- CLOCK IS STOPPED`. A letter's guard did it once.
- **`buildings.py <thingId> --inspect` matches nothing yet prints a POWER
  all-clear with `outOfFuel=0` over a stove sitting at 10/50** — the footer
  certifies buildings it never matched. The cell form `buildings.py 126,140`
  reads correctly. Aggregate footers count only rows that survived the filter,
  so `switchedOff=0` can print beside `--power`'s `switchedOff=2`.
- `buildings.py --inspect Shelf` denies a match three times, then lists all 20.
- `buildings.py set --temperature` reports `flick designated off` on a cooler
  whose switch reads on. `buildings.py reinstall` fires and leaves no blueprint.
- `order.py force` says "no designation, blueprint or frame" at BOTH a fresh
  blueprint and a finished build. At a burning cell it offered no fire option
  while `order.py menu` listed "Prioritize extinguishing fires" enabled.
- `order.py haul` refuses a buried corpse with `target_not_found`.
- `build.py` names the loose floor item in a "Space already occupied" refusal —
  same wording as a building conflict. Wants rotation as a positional BEFORE
  flags.
- **Remove-roof is an AREA**, not a designation: label "Remove roof area", read
  back with `map.py areas`; cells already in it return
  `REFUSED: The designator rejected this cell`.
- `rimworld/open_inspect_tab` takes **`inspectTabId`**, working value
  **`ITab_Storage`**; `list_inspect_tabs` returns Storage with `id: None`.
  `rimworld/select_thing` does not exist, only `select_pawn`.
  **`pick.select_thing` holds a selection**; `buildings.py gizmos` clears it.
  `pick.py` has no bare click form from the CLI.
- `ui.py click "Foods"` refuses — filter tree nodes are labels with no
  actionable partner.
- The trade sheet's OURS column does not count bought goods until hauled.
- `letters.py sweep` keeps expired quests alive. `act.py undesignate` cannot
  remove a Hunt. `combat.py` cannot resolve "Octave" — use
  **Thing_Human195630**. `map.py --legend` dumps ~4KB. A bad `bills.py --allow`
  argument dumps the whole companion-registration help text.
- **Lookout large-animal leads are 0 for 7 tonight** — a "dead emu" was a human
  raider named Black Emu; twice a "large animal indoors" was a sculpture or
  nothing within 59 cells; the last was the caravan's pack train. Verify before
  saying one on air.

## Live state at the turn-32 boundary (day 71, 16h)

- **THE HARVEST IS RUNNING.** After eleven turns of "must designate
  sufficiently-grown plants", `act.py apply "Harvest"` took **48 cells**. Rice is
  already moving. Ian is Growing 1 and standing in the field; Longhoff Growing 1.
  **Turn 33's whole job is getting every grain of it indoors.**
- **The venison mystery is solved and it was never eaten.** There were **two
  butcher spots** (118,143 and 117,140) carrying live Butcher-creature bills
  while the butcher table stood walled off behind its own doorway — every animal
  this colony ever processed went through a dirt spot at a meat penalty. zriatt
  asked the question, claude_plays_rimworld assembled it, R&D confirmed it. Both
  spots are now designated for deconstruction; check that it happened.
- R&D's doorway is fixed: butcher table moved 119,141 → 118,141, the wooden door
  at 120,141 is clear.
- **R&D's next instruction, unactioned — it arrived after the turn closed:**
  *"don't put the workbench across the walkway, move it to the wall south from
  the storage room."* Put this in turn 33's brief.
- The kitchen is 18 worktables in a 12x10 box with no free 1x3 whose interaction
  side isn't another bench. It wants a redesign, not a cell swap.
- Board: 0 letters, 0 alerts, 0 hostiles, 0 down. Food 6.3 days before the rice.
  A psychic drone (Low, male) has been running since day 71 4h; four of five
  colonists are male. Moods were 67–82%.

**Edge upgrade:** `buildings.py reinstall` printed *"the placing click left NO
blueprint there (the cell reads empty). Nothing else was changed."* — **and the
table had in fact moved.** It reports failure on a move that succeeds; its
verdict line cannot be trusted in either direction.

## Session-end obligations, none of them done yet

1. Append **every WEIRD line** from turns 21+ to `BUGS.md`, dated. On live this
   is the whole record of what we were not allowed to chase.
2. Update `CHRONICLE.md` (a first pass was done at the turn-32 boundary).
3. Journal entry in `C:\Home\rimworld\journal` — RimWorld sessions go there, not
   the shared `C:\Home\journal\`.
4. Save: `python rim.py call rimworld/save_game '{"saveName":"Lampblack - ..."}'`
   — a new descriptive name, never an Autosave slot; `saveName`, not `fileName`.
5. Stop RimWorld through GABS only while the bridge is connected to the owned
   process; verify it exited and leave GABS as found.
6. Commit the session records.

## The mistake that cost us a viewer (turn 38, and it was Core's)

**I wrote "DO NOT RE-LITIGATE" over things chat kept asking for.** The butcher
table blocked a door; rygger_dracora called it twice, claude_plays_rimworld
called it, zriatt called it. I marked it closed in turn 36's brief and again in
turn 38's — "it's a detour, not a trap" — so Hands was *forbidden* from touching
the thing the audience most wanted touched. Same with the unnecessary cooler.

**zriatt left the stream over it.** "This is rough to watch." They had cracked
the butcher-spot mystery and been right two turns ahead of us, twice.

Someone told us it blocked a doorway. Then someone else did. We kept going.
That is the whole of it.
