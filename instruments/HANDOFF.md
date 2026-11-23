# Start here — the bridge expansion

*Written 2026-09-03 by Fable 5.1, evening, at the end of the session that built phase 4. Supersedes the early-morning handoff.*

One file. It points; it does not repeat.

- **`WANTED.md`** — what is still open, ranked. Technical only; shipped items
  are deleted from it.
- **`INSTALL.md`** (in `rimworld\companion\`) — build, install, load test, the
  hazard list, and a payload-shape section per tool. Ground truth for every
  field name.
- **`PLAYBOOK.md`** — how a playing session calls the instruments.
- **`BUGS.md`** — one line per open bug.
- **`EFFICIENCY-AUDIT.md`** — the 2026-09-02 audit; its open items are now
  all shipped and it is a historical document.
- **`tests\contract_test.py`** (in `rimworld\companion\`) — run after every
  DLL change, offline first, then live. The `live_*.py` scripts beside it are
  one command each per feature.
- **`runtests.py`** — the unit suite (`test_*.py`), hermetically: it blocks
  the bridge and overlay ports and fails on ANY socket connect, so it is safe
  beside a live game. Prefer it to bare `unittest discover`, which started
  the supervised-play clock twice on 2026-09-12 through unmocked tests.

## Shipped in phase 4, live-verified on `Lampblack - day 39`, nothing saved

Six Opus 5 agents built in parallel on disjoint files; Fable integrated,
installed and verified. **Eighteen tools**, 545,280 → 779,776 bytes;
predecessor backed up as `.pre-phase4`. Contract test live: 0 failures. Every
new live check passed.

- **WANTED 0, watchability** — `Watch.cs`. A real write selects the target
  as if clicked, opens the tab a player would use, waits ~1.5 s, then the write
  lands inside the open menu, and the menu closes itself after `watchSeconds`
  (default 8). Wired into every write tool; `watch:false` or `--no-watch`
  writes with no UI. Decorative by design: the write is identical either way.
- **WANTED 4, bills** — `home/bills` + `bills.py`: list, recipes, add, set,
  delete, move, with per-ingredient needed/available/shortfall and a
  `CAN RUN` / `CANNOT RUN` verdict at the moment of adding.
  `home/list_buildings {billIngredients:true}` carries the same verdict per
  bill, and `rota.py`'s BUILDINGS section flags a queue that cannot run.
- **WANTED 12, gizmos** — `home/building_config` + `buildings.py set`:
  forbidden, power (a flick designation, honestly reported), medical, owner,
  prisoner bed, as direct fields; `gizmos:true` lists the bar without firing.
- **WANTED 5, stockpile presets** — `home/zone_cells {op:"filter"}` with
  `everything|nothing|food|perishables|nonperishables|outdoorSafe`, then
  `allow`/`disallow`/`priority`; `create` takes the same; `list_zones
  {filter:true}`; `zones.py filter`.
- **WANTED 10, batched status** — `home/status` + `status.py`: clock, letters,
  messages, alerts with culprits, colonists, threats, UI (with a real
  `modalOpen`) in one ~6 KB call. Opt-outs shrink it.
- **WANTED 2, pawn filters** — `wildOnly`, `tameOnly`, `animalsOnly`,
  `humanlikeOnly`, `mechanoidsOnly`, `colonistsOnly`, `prisonersOnly`,
  `downedOnly`, `draftedOnly`, `nameFilter` on `home/list_pawns`;
  `pawns.py --wild --tame --prisoners --downed --drafted --mechs --humans`.
- **WANTED 17** — `get_cells_plus {summary:true}` over the whole map.
- `home/list_buildings` rows carry `thingId`; `home/bills` never lists pawns
  or corpses as benches.

## The governing principle

The cost of a tool is surface area, not milliseconds. One tool with opt-in
blocks over two tools whenever the data hangs off the same object. Phase 4
added three tools (`status`, `bills`, `building_config`), each because its data
hangs off nothing that existed; everything else is a block, a field or an op
on something that was there.

## How to build in parallel without a worktree

Each C# agent copies `companion\src` to a private folder, edits only its own
files there, builds with `/p:OutputPath=… /p:BaseIntermediateOutputPath=…`, and
copies only its own `.cs` back. Shared contracts (this time `Watch.cs`) go in as
a stub with final signatures BEFORE the agents copy, so everyone compiles
against the same surface. Python instruments are owned one file per agent.
Doc text comes back as a report and one agent merges it. Reports from this
phase are in git under the journal entry's paths.

## Hazards worth carrying forward

`INSTALL.md` carries the full list. The ones that bit this phase: the obvious
way to list colonists (`MapPawns.FreeColonists`) calls `Faction.OfPlayer`;
`Thing.GetGizmos()` calls it unconditionally; `RecipeDef.AvailableNow` and
`ForbidUtility.IsForbidden(Thing, Faction)` reach it too; `Building_Bed.
ForPrisoners = false` is a `Log.Error`; `FlickUtility.UpdateFlickDesignation`
can open a tutor dialog; `Bill_Production.ShouldDoNow()` mutates; `MakeNewBill`
consumes an id; `Selector.Select` logs on null, destroyed or world pawns. The
memory-deleting getters (`GetDistinctMoodThoughtGroups`, `OpinionOf`) still
stand. Every one is pre-checked in the tool that meets it, never caught.

## Loose ends

- `--force-void` has never loaded a real save.
- The `training` and `slaughter` writes have never run for real (no tame
  animal on day 39).
- `unknownArgumentsWarning`'s "could not run" branch is still unexercised.
- The watch's open-before-write ordering was confirmed by one screenshot
  (`bridge\profile\Screenshots\watch-bills-add.png`), not by a script; a script
  can only see either side of the gap.
- `home/bills`' ingredient scan is optimistic (no reachability); WANTED 8.
