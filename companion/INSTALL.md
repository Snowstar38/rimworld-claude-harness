# HomeBridge.BridgeTools — build, install, load test, payload shapes

A RimBridgeServer **companion DLL**. Twenty-four `home/` tools on the live bridge
surface, one DLL, no RimWorld mod. This file is the reference you build and
install from; the source under `src\` is ground truth for every payload.

The count was 22 until 2026-09-08, when `home/grid` and `home/starting_pawns`
were added; count `[Tool(` in `src\` rather than trusting a number written down.
The table below still lists 22 of them — `home/supervised_play` has its own
section under **Payload shapes**, and `home/starting_pawns` (the new-colony
starting pawns, read-only) has no section yet.

## What is here

| Tool | What it does |
|---|---|
| `home/ping` | Smoke test. Touches no game state. Companion + SDK versions, `onMainThread`. |
| `home/list_pawns` | Every spawned pawn: position, faction, `hostile`, `job`, distance to the nearest colonist. Ten opt-in blocks — `health`, `needs`, `equipment`, `bio`, `thoughts`, `work`, `schedule`, `settings`, `relations`, `animals` — and nine narrowing flags plus `nameFilter`, applied server-side. |
| `home/list_things` | Item census by def with an ownership breakdown (`ours`, `forbidden`, `inStockpile`, `fogged`, `carried`, `traderStock`, `otherFaction`, `inContainer`) and a `holders` partition naming who has the rest. Corpses carry rot stage and skeleton counts; `corpses: true` narrows the census to bodies and names each one. |
| `home/list_buildings` | Whole-map census of built buildings, blueprints and frames: construction deficit, bill queues, power, rotation. Mundane structures aggregate by def; anything actionable gets its own row. Every detailed row carries `thingId`, which addresses it in `home/building_config` and `home/bills`. `inspect: true` adds each row's inspect-pane text; `billIngredients: true` adds each bill's ingredient shortfall and whether it can run. |
| `home/list_rooms` | Whole-map room census: the game's role label plus bed owner, extents, temperature, the five room stats, owners, beds, contents, pawns, overlapping stockpiles. Optional cell lookup or `roomGrid`. |
| `home/get_cells_plus` | Per-thing `forbidden` and per-bed `ownerName` over a rectangle of up to 1024 cells, in either `{x,z,width,height}` or inclusive `{x0,z0,x1,z1}` form. Opt-in field selection (`fields`, `thingFields`), `sparse`, and `summary` mode — under `summary` the cap is the whole map. |
| `home/get_temperatures` | Celsius over up to 16384 cells. `mode:"rooms"` (default) gives a row per room plus a room-index grid; `mode:"cells"` gives a `temps[][]` grid. |
| `home/get_time` | Read-only snapshot of the clock and calendar: ticks, pause flags, time speed, hour / day / quadrum / season / year, RimWorld's own `dateFull`. No parameters. |
| `home/world` | The world inspect pane's facts for one planet tile, read off the world grid **without opening the world view**: biome, hilliness, elevation, rainfall, swampiness, pollution, longitude/latitude, the seasonal temperature, the tile's average/min/max temperatures, the growing period in days, and the settlements within `settlementRadius` tiles with their factions and distances. The World main tab is a `MainButtonDef` toggle rather than a tab window, so `open_main_tab` and `click_ui_target` cannot reach it at all; this is the route that works. `show: true` with `dryRun: false` toggles the planet view for `watchSeconds` and hides it again -- the only write in the tool. |
| `home/status` | The whole between-turns read in one call: clock, letters with their choices, live messages, alerts loudest first with the things they name, one row per colonist, hostiles and hunting predators, and what the UI has open. Read-only. |
| `home/play_until_event` | Run the game and pause **at** the event. Watches letters, messages, alerts, hostiles, hunting predators, downed colonists and a health threshold, under a duration budget. An outside pause always wins. |
| `home/list_zones` | Read-only zone census. Reports the zone's own cell count **and** what the zone grid assigns it, naming every cell where they disagree. Stockpile occupancy; growing-zone plant counts over both cell sets. `filter: true` adds each stockpile's storage filter. |
| `home/zone_cells` | The zone write tool. `op` = `add` / `remove` / `create` / `delete` / `repair` / `filter` / `crop`, with a before/after summary and a per-cell verdict. `filter` sets a stockpile's storage filter from a preset and allow/disallow lists; `create` takes the same keys. `crop` sets a growing zone's plant with `plant` (a ThingDef defName or label) and reads the crop back off the private `plantDefToGrow` field, never the property whose getter writes. |
| `home/place_building` | Placement check and placement, all four rotations by default. The game's own refusal reason, the occupied cells, what would be wiped, cooler/vent side temperatures. |
| `home/pawn_config` | The write side of `list_pawns`: work priorities, schedule, medical care, hostility response, self-tend, follow toggles, allowed area, master, `nickname` (rename a colonist or a tamed animal), `drop` (one worn, wielded or packed item onto the pawn's cell, immediate even while paused), and on an animal training, slaughter, release to wild, and — for a **wild** one — hunt and tame. `after` is read back from the game. |
| `home/building_config` | The write side of `list_buildings`: forbidden, power, medical bed, prisoner bed, bed owner, plus the thing's gizmo bar as a read. `after` is read back from the game. |
| `home/bills` | Worktable bills in one call: every bench's queue, each bill's configuration, and per ingredient how much is needed against how much is on the map, so a bill that cannot run says why. `action` = `list` / `recipes` / `add` / `set` / `delete` / `move`. |
| `home/trade` | A whole trade with no dialog: `action` = `list_traders` / `open` / `sheet` / `set` / `preview` / `accept` / `cancel` / `close_dialog` / `status`. Counts are signed — positive = the colony buys. |
| `home/dialog_text` | Type into the dialog that is on screen. Lists the top-most window's string fields, sets one, and optionally presses accept. Real keyboard input does not reach this game, so this is the only way to answer a name prompt. |
| `home/research` | Research in one call: the current project (null IS the `Need research project` alert), every startable project, the benches and whether one is powered, the colonists with Research on and their Intellectual level. Opt-in `locked` (with what blocks each), `finished`, `unlocks`, `filter`. `set: "<name>"` chooses a project, dry run by default, `after` read back from the game. |
| `home/order` | The order tool: vanilla pawn orders issued **as jobs**, with the float menu never opened. `action` = `resolve` / `draft` / `undraft` / `attack` / `goto` / `equip` / `rescue` / `tend` / `haul` / `work` / `rest` / `deploy`. Pawn and target take five id forms each, so an explicit id can never be "ambiguous"; hostility is never a precondition; `dryRun` is the oracle for "can this pawn attack that thing" and for "can this pawn deploy onto that cell". Every job shape is decompiled vanilla, and `job.verified` is `Pawn.CurJob` read back. |
| `home/grid` | A labelled coordinate grid drawn over the play area **inside the game**, so the capture carries it and a viewer can say "104,127". `enabled` / `step` / `labels` / `alpha` / `color`, dry run by default; no arguments at all is a read. The drawing is a Harmony **Prefix** on `MapInterface.MapInterfaceOnGUI_BeforeMainTabs`, installed on the first call, so the grid sits over the terrain and under every piece of UI; off, the hook is one flag read and a return. `render{}` reports what the last frame actually drew — the spacing thins with zoom rather than drawing thousands of lines. The label loops test strictly `<` the clamped far edge, because `x1`/`z1` is a boundary and not a cell: on a 250-wide map a label at 250 would name a cell no tool accepts. The line loops stay inclusive — a line at 250 is the correct east wall of cell 249. |

```
C:\Home\rimworld\companion\
├── src\
│   ├── HomeBridge.BridgeTools.csproj
│   ├── BridgeCommon.cs                 shared: unknownArguments[], raw-argument lookup
│   ├── Watch.cs                        shared: the watch session a write opens and closes
│   ├── BillCommon.cs                   shared: bill reading, ingredient scan, filter summary
│   ├── PingTool.cs                     home/ping
│   ├── ListPawnsTool.cs                home/list_pawns
│   ├── ListThingsTool.cs               home/list_things
│   ├── ListBuildingsTool.cs            home/list_buildings
│   ├── ListRoomsTool.cs                home/list_rooms
│   ├── CellsPlusTool.cs                home/get_cells_plus
│   ├── TemperatureTool.cs              home/get_temperatures
│   ├── TimeTool.cs                     home/get_time
│   ├── WorldTool.cs                    home/world
│   ├── StatusTool.cs                   home/status
│   ├── PlayUntilEventTool.cs           home/play_until_event
│   ├── ZonesTool.cs                    home/list_zones
│   ├── ZoneCellsTool.cs                home/zone_cells
│   ├── PlaceBuildingTool.cs            home/place_building
│   ├── PawnConfigTool.cs               home/pawn_config
│   ├── DialogTextTool.cs               home/dialog_text
│   ├── BuildingConfigTool.cs           home/building_config
│   ├── BillsTool.cs                    home/bills
│   ├── ResearchTool.cs                 home/research
│   ├── OrderTool.cs                    home/order
│   ├── GridOverlayTool.cs              home/grid + the per-frame draw hook
│   └── TradeTool.cs                    home/trade
├── tests\
│   ├── contract_test.py                every tool's promised keys, offline + live
│   ├── live_animals.py                 live check: the animals block and its writes
│   ├── live_bills.py                   live check script for home/bills
│   ├── live_building_config.py         live check script for home/building_config
│   ├── live_drop.py                    live check script for home/pawn_config drop (`--apply` for the one real write)
│   ├── live_cells_plus.py              live check: fields, sparse, summary
│   ├── live_list_buildings_inspect.py  live check: inspect:true
│   ├── live_list_rooms.py              live check script for home/list_rooms
│   ├── live_list_things_corpses.py     live check: corpses
│   ├── live_pawn_config.py             live check script for home/pawn_config
│   ├── live_pawn_filters.py            live check: the list_pawns narrowing flags
│   ├── live_research.py                live check script for home/research
│   ├── live_status.py                  live check script for home/status
│   ├── live_watch.py                   live check: the watch session every write opens
│   ├── live_zone_filter.py             live check: zone_cells op filter, create presets
│   └── test_setup_void.py              offline check: setup.py --void / --unvoid
├── artifacts\BridgeTools\HomeBridge\
│   └── HomeBridge.BridgeTools.dll      <- the only file that gets deployed
├── artifacts\installed-backup-20260902\ the installed DLLs that earlier copies replaced
├── upstream\                           git clone of pardeike/RimBridgeServer @ ca5997c (v2.1.1)
├── INSTALL.md
└── TRADE-TEST.md                       the first-run procedure for home/trade
```

## Build

```powershell
dotnet build "C:\Home\rimworld\companion\src\HomeBridge.BridgeTools.csproj" -c Release
```

Clean: 0 warnings, 0 errors, about a second. `TreatWarningsAsErrors` is on. The
csproj writes straight into `artifacts\BridgeTools\HomeBridge\` (its
`OutputPath`), so a build **is** the refresh; there is no copy step. It uses
`EnableDefaultCompileItems` and lists no `<Compile>` items, so a new `.cs` in
`src\` is picked up with no csproj edit.

It references two things it never copies:

- `RimBridgeServer.Sdk.dll` from `C:\Home\rimworld\bridge\RimBridgeServer\RimBridgeServer\1.6\Assemblies\`
- `Assembly-CSharp.dll` + `UnityEngine*.dll` from `C:\Program Files (x86)\Steam\steamapps\common\RimWorld\RimWorldWin64_Data\Managed\`

Both are csproj properties (`RimBridgeSdkDir`, `RimWorldManagedDir`); override
with `/p:` if either moves. `net472` reference assemblies come from the
`Microsoft.NETFramework.ReferenceAssemblies.net472` NuGet package, because the
targeting pack is not installed on this machine.

**Do not let the build copy `RimBridgeServer.Sdk.dll` into the output.** Every
reference is `<Private>false</Private>` for that reason; a local SDK copy in the
bundle folder is what produces the `capability.sdk_mismatch` / "local SDK copy"
warnings in `rimbridge/get_bridge_status`. The check is one line: after a build
the output folder must hold **exactly one file**, our DLL. The current build is
**1,169,920 bytes** (Release, 2026-09-13 11:44, SHA-256
`18e4711534728be0ae2a0d7cbed14b96fcd09dbac878f885f356a86a8bc75b4f` — the
supervised-play stop-behavior build, verified live 2026-09-14), and its references are `mscorlib`, `System`, `System.Core`,
`RimBridgeServer.Sdk 2.1.1.0`, `Assembly-CSharp 1.6.9676.17735` and
`UnityEngine.CoreModule` — all resolved by the host, none copied.

When scanning a built DLL to confirm the `[Tool]` names registered: the
attribute's string arguments live in the **#Blob heap as UTF-8**, not the
UTF-16 #US heap. A UTF-16 scan finds most of them and silently misses some,
which looks exactly like a tool failing to register. Decode as UTF-8.

## Install

Copy the one DLL into a **first-level bundle folder** under the game-root
`BridgeTools` directory, **with RimWorld closed** — a running game holds the file
with a user-mapped section open and the copy fails. Companions are discovered
only at bridge startup, so a new build needs a restart regardless.

```powershell
$dest = "C:\Program Files (x86)\Steam\steamapps\common\RimWorld\BridgeTools\HomeBridge"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Copy-Item "C:\Home\rimworld\companion\artifacts\BridgeTools\HomeBridge\HomeBridge.BridgeTools.dll" $dest -Force
```

Result: `RimWorld\BridgeTools\HomeBridge\HomeBridge.BridgeTools.dll`. To
uninstall, delete `RimWorld\BridgeTools\HomeBridge\`. Nothing else is touched.

Move the copy you are replacing into `artifacts\installed-backup-<date>\` first,
so a bad build is one copy away from being undone. The four most recent are
`HomeBridge.BridgeTools.dll.pre-phase1` (318,464 bytes),
`HomeBridge.BridgeTools.dll.pre-phase2` (335,360 bytes),
`HomeBridge.BridgeTools.dll.pre-phase3` (435,712 bytes) and
`HomeBridge.BridgeTools.dll.pre-phase4` (545,280 bytes) — the builds that
preceded the 779,776-byte phase-4 build; `installed-backup-20260904\HomeBridge.BridgeTools.dll.pre-combat-review` (882,176 bytes, Debug) and
`installed-backup-20260904\HomeBridge.BridgeTools.dll.pre-nickname` (840,704 bytes) preceded the 860,672-byte one;
`installed-backup-bugpass-20260912\HomeBridge.BridgeTools.dll.pre-bugpass-20260912`
(1,167,360 bytes, SHA-256 `6ad4dd10…`) preceded the 1,169,920-byte Sep 12 one;
`installed-backup-20260913\` holds that build as `.pre-equipverify` (1,169,920)
and the equip-verification build as `.pre-startingpawns-error` (1,169,408 — the
starting_pawns error-key fix landed the same day), plus the equip/starting_pawns
build again as `.pre-supervisedplay-fixes` (1,169,408, sha `1124ee52…`) from the
Sep 13 11:30 install that was rolled back; `installed-backup-20260914\` holds
that same verified build as `.pre-stopbehavior`, replaced when the stop-behavior
build shipped after its live verification. All installed with the game closed.

This is the SDK's documented "standalone global tool" layout
(`upstream\skills\rimbridge-companion-tools\references\companion-dll-guide.md`).
We are not a RimWorld mod: nothing goes into `Mods\`, no `ModsConfig.xml` is
edited in either profile, and companions never appear in RimWorld's mod list. The
game root is writable without admin despite living under `Program Files (x86)`.

### The shared install, and why it is safe

The `BridgeTools` folder is genuinely shared between profiles —
`RimBridgeExtensionDiscovery.TryGetGlobalBridgeToolsRoot()` resolves it as
`Directory.GetParent(GenFilePaths.ModsFolderPath) + "\BridgeTools"`, which is
install-relative, not profile-relative. What makes it safe is that
`DiscoverCompanionCandidates()` is reached only from RimBridgeServer's own
startup: if `brrainz.rimbridgeserver` is not an active mod, nothing scans
`BridgeTools` and the DLL is never opened. Mod activation lives in
`<savedatafolder>\Config\ModsConfig.xml`; the agent runs with
`-savedatafolder=C:\Home\rimworld\bridge\profile`, so the two profiles have
separate ModsConfig files — the agent's has RimBridgeServer active, M's
(under `%LOCALAPPDATA%Low\Ludeon Studios\`) does not. The DLL is therefore inert
in her game, and her ModsConfig is never read or written by this install. The one
residual caveat, stated honestly: the folder really is shared, so if she ever
enables RimBridgeServer in her own profile this companion loads there too and any
exception it throws lands in her log. That is a conditional on an action nobody
plans to take, not a live risk.

## Load test

Requires a RimWorld restart: companions are discovered once, at bridge startup.
Install with the game closed, start RimWorld through GABS, wait for the bridge to
connect, then:

1. **`rimbridge/get_bridge_status`.** Expect a record for
   `…\BridgeTools\HomeBridge\HomeBridge.BridgeTools.dll` with
   `status: "registered"`, `toolClassCount: 24`, **`toolCount: 24`**,
   `errors: []`, `warnings: []`, `localSdkPaths: []`, and
   `referencedSdkVersion 2.1.1.0 == hostSdkVersion 2.1.1.0`. A `toolCount` below
   24 means the current DLL was never copied into the game. That is what the
   2026-09-12 install read back: `registered`, `ToolCount: 24`, no errors, no
   warnings, `LocalSdkPaths: []`.
2. **`home/ping`.** Expect `success: true`, `pong: "pong"`,
   `sdkVersion: "2.1.1.0"`, and **`onMainThread: false`** — false is the
   *expected* answer, see the main-thread note below.
3. **The unknown-argument contract.** Send any tool a bogus key
   (`{bogusKeyXYZ: 1}`) and expect it named in `unknownArguments[]` with
   `unknownArgumentsWarning` set; send a clean call and expect `[]`. On a write
   tool, send a case-wrong `DryRun` and confirm it is listed **and** the call
   still defaults to `dryRun: true, applied: false`.
4. **The check scripts**, all against a loaded, **paused** game:

   ```powershell
   cd C:\Home\rimworld\instruments
   python ..\companion\tests\live_list_rooms.py
   python ..\companion\tests\live_pawn_config.py
   python ..\companion\tests\contract_test.py
   python ..\companion\tests\live_cells_plus.py
   python ..\companion\tests\live_research.py
   python ..\companion\tests\live_list_buildings_inspect.py
   python ..\companion\tests\live_list_things_corpses.py
   python ..\companion\tests\live_animals.py
   python ..\companion\tests\live_status.py
   python ..\companion\tests\live_pawn_filters.py
   python ..\companion\tests\live_zone_filter.py
   python ..\companion\tests\live_building_config.py
   python ..\companion\tests\live_bills.py
   python ..\companion\tests\live_drop.py
   python ..\companion\tests\live_watch.py
   ```

   `live_list_rooms.py` is read-only: six scenarios (clean call, rectangle,
   single cell, `includeOutdoors`, `cells:true`, bogus key) plus the structural
   invariants — every non-null `roomGrid` entry a valid index into `rooms[]`,
   `cellCount == len(cells) + cellsNotListed`,
   `roomCount + roomsOmitted == roomCountTotal` — and a cell-for-cell cross-check
   of the room grid against `home/get_temperatures {mode: "rooms"}` over the same
   rectangle. `live_pawn_config.py` is dry-run throughout except **one** real
   write — `selfTend` toggled on a single colonist and put straight back — and
   confirms that every `before` the write tool reports matches what
   `home/list_pawns` independently reads, that a bad enum value comes back
   refused with a reason, and that all ten pawn blocks answer together.

   `contract_test.py` checks the documentation contract rather than the
   colony. Offline it parses every `[Tool]` method's `[ToolResponse]` and
   `[ToolParameter]` attributes out of `src\*.cs` — comments and strings
   masked, multi-line attributes, `+` concatenation, verbatim strings and
   `const` names all handled — and reports `SUSPECT` for any key a tool
   promises but never writes, every camelCase word in a description
   included, which is the exact shape of the `promotedOut` promise. Live it
   calls every tool with `{}`, then each bool block one at a time, then a
   per-tool table of string arguments, then one bogus key each, asserting
   `success`, every `Always` key present, no non-`Nullable` key null and
   `unknownArguments == []`. `home/play_until_event` is hard-skipped because
   it unpauses; the write tools run only their table cases with
   `dryRun: true`, and an `assert` refuses `dryRun: false` whatever the
   table says. `--offline` and `--tool <name>` narrow it. It is a
   load test, not a between-turns check.

   `live_cells_plus.py` is read-only: `fields`/`thingFields` selection
   against the unnarrowed payload, the refusal on an unrecognised field
   name, `fieldsApplied[]`/`thingFieldsApplied[]` on every reply, and
   `len(cells) + cellsOmitted == cellCount` in all three modes.
   `live_research.py` is read-only apart from one optional write — a project
   chosen and the previous one put straight back — which it skips when no
   project is current; it checks that the four buckets sum to `totalCount`,
   the bench and researcher block, and the refusals.
   `live_list_buildings_inspect.py` is read-only: `inspectString` on every
   detailed row under `inspect: true` and on none without it, no aggregated
   row carrying one, every null accounted for in `inspectSkipped[]`.
   `live_list_things_corpses.py` is read-only: the three always-present
   corpse keys with zeros included,
   `corpsesListed + corpsesNotListed == corpseCount`, and the
   `corpses: true` narrowing.
   `live_animals.py` is read-only apart from one training write put straight
   back, skipped when no tame animal is on the map: the `animals{}` block on
   an animal and on a humanlike, the trainable rows, the designation flags,
   and the cascade a dry run names.

   `live_status.py` is read-only: every `Always` key present, the payload
   under 10 KB, and the clock, the letter ids and the alert labels
   cross-checked against `rimworld/get_game_info`, `list_letters` and
   `list_alerts`.
   `live_pawn_filters.py` is read-only: each narrowing flag's answer must
   **equal** the set computed from one unfiltered payload read at the start,
   not merely be a subset of it, and `pawnsListed`/`pawnsFiltered` must close
   against `spawnedPawnTotal`.
   `live_zone_filter.py` is read-only apart from a stockpile priority set and
   put straight back, plus an `allow` of a def the stockpile already allows,
   which must report `changed: false`; every preset is exercised as a dry run,
   because `preset: "nothing"` could not be undone from the payload.
   `live_building_config.py` is read-only apart from `forbidden` toggled on
   one building and written straight back — the only field with a guaranteed
   way back; `power`, `medical`, `forPrisoners` and `owner` are dry runs only.
   `live_bills.py` is read-only apart from one bill added and then deleted
   again, with `watch: false` so nothing moves, skipped when the bench offers
   no recipe or the stack is already at `BillStack.MaxCount`; the bill count
   before and after must match.
   `live_watch.py` is the exception to "selects nothing": it toggles
   `selfTend` on one colonist and back, and asserts that a dry run shows
   nothing, that the real write opens the pawn's Health tab with the camera
   moved, and that everything it opened closes itself again.

   None of these scripts saves or unpauses. Only the three that exercise a
   watch session select anything, and each waits for what it opened to close.

5. **One control per older tool**, each one a plausible-looking wrong
   implementation would fail:
   - `home/get_cells_plus` — the same cells asked in both rect forms must return
     identical `cells[]`; a malformed mixture must be refused with
     `argumentsSeen`.
   - `home/get_temperatures` — over a heated fort, a room warmer than
     `outdoorTemp` with `outdoors: false` and the outdoors reading `true`;
     `sum(rooms[].cellsInRect) + cellsWithNoRoom == cellCount`, exactly.
   - `home/list_things` — a known-forbidden stack reports `forbidden` non-zero,
     and the two `ownership` modes reconcile on `stacks`/`total`.
   - `home/list_buildings` — an aggregate row's `count` equals
     `positionsListed + positionsNotListed` and matches `aggregate: false`.
   - `home/list_zones` — called twice with `includeCells: true`, the `cells`
     arrays must be **identical in order**, and a never-sown growing zone must
     keep `plantDef: null` across both calls.
   - `home/zone_cells` — `op: "repair"` as a dry run must change nothing: the
     anomalies must still be there on the next `home/list_zones`.
   - `home/place_building` — with no `rotation`, four rows back, and a spot where
     `acceptedRotations` is a strict subset of them.
   - `home/play_until_event` — a keyboard SPACE mid-run returns `external_pause`
     with `pausedByThisTool: false`, and the game is left paused.
   - `home/get_time` — `dateFull` and `hourInteger` match the game's own
     top-right readout (the longitude check).
   - `home/trade` — see `TRADE-TEST.md`.

6. If a tool does not appear, read the RimWorld log for `[RimBridge]` companion
   discovery lines before changing anything.

**Known gotcha:** `games_tool_names` / `games_tools` do not list the `home/`
tools even when `rimbridge/get_bridge_status` shows them registered — GABS caches
its tool surface at connect time. Calling them by name works anyway
(`rim.game('home/list_rooms', {})`), which is what every instrument does.

### The main-thread hop

Companion tools are dispatched with **`MarshalToMainThread = false`**
(`AnnotatedExtensionCapabilityProvider.InvokeAsync`), unlike built-in tools, so a
companion that reads `map.thingGrid` naively is reading Unity/RimWorld state off
the main thread. **Every tool here therefore does all of its game access inside a
single `ctx.MainThread.InvokeAsync(...)` hop.** Removing that hop still compiles
and fails intermittently and unreproducibly at runtime, the worst failure mode
available; `home/ping` returning `onMainThread: false` is what confirms the
dispatch model is still what this assumes.

### `unknownArguments[]` — on every reply of all twenty-four tools

The SDK binder (`AnnotatedExtensionCapabilityProvider.BindArguments`) hands a
tool method only the parameters it declares, by name, and **drops every other key
the caller sent without a word**; a declared parameter nothing supplied is then
filled with `Activator.CreateInstance`, i.e. zero. That is how
`{equipment: true}` was discarded at the door for five sessions while
`equipment: None` read as "this build cannot see a weapon", and how a corner-form
rectangle became a successful 1x1 scan of cell (0,0).

`BridgeCommon.WithUnknownArguments` closes it. Every reply carries
`unknownArguments[]` — every key the caller sent that the tool does not declare,
sorted and case-sensitive, so a mis-cased `DryRun` is named as loudly as a
misspelling; an empty array means every key was recognised, and the host's own
`_rimBridgeTimeoutMs` is never listed. The declared names come from reflecting the
`[Tool]` method's own `[ToolParameter]` list, so the check cannot drift from the
schema. The caller's raw keys are not reachable through the SDK's public surface
at all, so the helper reads them out of the server's operation journal
(`RimBridgeCapabilities.Journal` → `OperationJournal.GetOperation(id)` →
`Metadata["arguments"]`) by reflection, keyed on `ctx.OperationId`. Every step is
reflection into another mod's internals and every step is guarded: if the lookup
fails the reply carries **`unknownArgumentsWarning`** and the empty
`unknownArguments` means "not known", never "nothing unknown". That branch has not
fired live.

### Debug actions through the bridge

`rimworld/execute_debug_action` is the host's tool, not one of ours, but two of
its behaviours are worth writing down because nothing in the reply says them.

**An incident's target is always the current map.**
`Verse.DebugActionsIncidents.GetTarget()` returns a world object only when the
World view is up — `WorldRendererUtility.WorldSelected` gives the selected world
object, else `Find.World` — and otherwise returns `Find.CurrentMap`. The bridge
never puts the World view up, so every incident fired through it targets the map.
World-tagged incidents — `SolarFlare`, `Eclipse`, `Aurora`, `GiveQuest_Random` —
therefore log `Incident target is null or not allowed.` and the node's own label
ends `[NO]`, **while the bridge still answers `success: true`**: the call reached
the action, and the action declined. The routes that do work from a map are
`Actions\Add Game Condition...\<name>\<duration>` for a game condition and
`Actions\Generate quest...\*Natural random` for a quest.

**`Prefs.DevMode` is never checked** by `ExecuteDebugActionResponse`
(`upstream\Source\RimWorldDebugActions.cs`). The dev-mode toggle gates the
in-game menu, not this path, so a debug action runs with dev mode off.

## Payload shapes

**What holds for all twenty-four.** Every reply carries `success`, `tool`,
`unknownArguments[]` and, conditionally, `unknownArgumentsWarning`; those are not
repeated below. `success: false` means a *tool* failure — "no game is loaded",
"the trader refused", "a raider is standing there" are answers and come back
`success: true`. A boolean that could be false is emitted as false; a value that
could not be read is null with its name in a `skipped[]` or a warning field, never
absent, because null and absent are the same thing once this is JSON. Bad
arguments are **refused**, naming what was parsed — nothing is clipped into an
answer that looks complete. Every write tool and `trade`'s mutating actions
default to `dryRun: true`. **`Faction.OfPlayer` is never called anywhere in this
DLL**: its body is `get_OfPlayerSilentFail` followed by `Verse.Log.Error`, and
`Log.Error` calls `TickManager.Pause()`, so on a map with no player faction asking
who the player is would pause the colony — which the harness reads as a person
pressing space. Every API named below was verified by reflecting the installed
`Assembly-CSharp.dll`, **1.6.9676.17735**.

**Watchability.** A write that lands with nothing on screen is invisible to
anyone watching the game, so every write tool takes `watch` (bool, default
**true**) and `watchSeconds` (int, default **8**, clamped 1..60) and **always**
emits a `watch{}` block. It is decorative: the write itself is the same call
either way, and `Watch.cs` only shows a viewer where it landed.

**The order is the point.** The menu a player would use opens **first**, on the
main thread; the tool then waits `leadMs` (1500) off the main thread so the open
menu is actually seen; then the change is applied inside the open menu and read
back; then the close is scheduled `closesAfterSeconds` later. A write is
therefore two main-thread hops with a wait between them, and the target is
re-resolved in the second hop rather than carried across the gap — a null
re-resolution is a refusal, reported.

`watch{}` has the same nine keys whether anything was shown or not: `shown`,
`selected`, `inspectTab` (the `ITab` class name, or null), `mainTab` (the
`MainButtonDef` defName, or null), `cameraMoved`, `leadMs` (0 when nothing was
shown), `closesAfterSeconds` (0 when nothing was shown), `note`, and `reason`
— null when shown, otherwise `"dry run"`, `"refused"`, `"watch:false"`,
`"read-only call"` or what else stopped it. A dry run and a refusal never reach
the second hop, so neither ever opens a menu; nothing this DLL opens is left on
screen. Which menu each tool picks is stated in its section below.
`Selector.Select` `Log.Error`s on a null, destroyed, unspawned or world pawn, so
every target is pre-checked before it is selected.

**One stock-tool shape is stated here, because reading it wrong cost fourteen
turns.** `rimworld/get_map_target_info`'s **envelope** carries `kind`
(`"pawn"` / `"thing"`), `thingId`, `pawnId`, `position` and `cellRect`. The
nested `target` is `RimWorldState.DescribePawn` for a pawn and `DescribeThing`
for a thing — and **`DescribePawn` names the id `pawnId` and carries no
`thingId` at all**, so a caller that reads `target["thingId"]` gets `None` for
every pawn, and every id check against it fails. `pick.resolve()` copies the
envelope's ids onto the target for exactly this reason.

### `home/ping`

Optional `label`. Returns `pong`, `companion`, `companionVersion`, `sdkVersion`,
`onMainThread`, `utc`. Touches no game state; `onMainThread: false` is correct.

### `home/list_pawns`

Filters: `hostileOnly`, `includeColonists`, `withinOfColonists`, `includeDead`,
`visibleHediffsOnly`. Ten opt-in blocks, all off by default — `health`, `needs`,
`equipment`, `bio`, `thoughts`, `work`, `schedule`, `settings`, `relations`,
`animals` — so the ordinary threat sweep does not grow. They are flags rather than separate tools
because the data hangs off the same object and one call must answer for every
pawn.

**Nine narrowing flags and a name filter**, all applied server-side and
**ANDed** together: `wildOnly` (an animal with no faction), `tameOnly` (an
animal of the player faction), `animalsOnly`, `humanlikeOnly`,
`mechanoidsOnly`, `colonistsOnly` (`IsFreeColonist`), `prisonersOnly`,
`downedOnly`, `draftedOnly` — each a bool defaulting to false — plus
`nameFilter`, a case-insensitive substring matched against the name, the
defName and the kindDef. A **contradictory** pair is refused rather than
answered with an empty list: `success: false`, `error` naming the pair,
`filtersSeen` and `expected`. Every flag has a matching bool on the row, so a
narrowed list can be checked against an unnarrowed one field for field.

Top level: `pawnCount`, `pawnsListed`, `pawnsFiltered`, `spawnedPawnTotal`,
`colonistCount`, `ghoulCount`, `hostileCount`, `ticksGame`,
`animalCount`, `tameAnimalCount`, `skippedByDistance`, `filters{}`, `notes{}`,
`pawns[]`, plus
`unarmedColonists[]` with `equipment:true` and `pawnConfigOptions{}` with
`settings:true`. A pawn row: `name`, `thingId`, `defName`, `kindDef`, `position`, `faction`,
`hostile`, `hostileReason`, `animal`, `humanlike`, `mechanoid`, `tame`, `wild`,
`isColonist`, `ghoul`, `playerFaction`, `isFreeColonist`, `isPrisoner`,
`downed`, `drafted`, `job`,
`mentalState`, `predator`, `manhunterOnDamageChance`, distance to the nearest
colonist, and the blocks asked for.

**`predator` is the game's own flag, and it replaces every hardcoded list.**
`Verse.RaceProperties.predator` is a public `bool` **field** on the race def,
XML-loaded, so reading it touches nothing and cannot `Log.Error`. False for
humanlikes and mechanoids. `watch.py` kept a hand-written `PREDATORS` set of
defNames beside a `Mech_` prefix, and a list written from memory is wrong the
moment a mod adds a race — it was already wrong for the wild boars that stood
fifteen cells from a colonist unreported on 2026-09-04. Beside it,
`manhunterOnDamageChance` is `Verse.RaceProperties.manhunterOnDamageChance`
(public `float`, 0..1): the chance the animal turns on whoever hurt it, which is
what makes shooting a predator a combat decision and not a hunting one. It is the
**raw race field** — RimWorld's own stat line routes through
`PawnUtility.GetManhunterOnDamageChance`, which applies modifiers this does not.

**`ghoul` and `playerFaction`, and why `isColonist` is not "one of ours".**
`Verse.Pawn.IsColonist` is `Faction != null && Faction.IsPlayer &&
RaceProps.Humanlike && (!IsSlave || guest.SlaveIsSecure) && !IsSubhuman`, and
`IsSubhuman` is `IsMutant && mutant.Def.consideredSubhuman`, which a ghoul's
MutantDef sets. So Anomaly's colony ghoul is humanlike, of the player faction,
standing in your colony — and `isColonist: false`. This tool never dropped one;
every caller did, by reading `isColonist` as "ours". The colony's ghoul Ben
Cooper was in Threadneedle for its whole run and never once in `pawns.py
--roster`. Read **`isColonist || (ghoul && playerFaction)`** for "one of ours".
`playerFaction` is half of it on purpose: a ghoul raised against you by a ritual
reports the same `ghoul: true`. `ghoul` is `Pawn.IsGhoul`, which tests
`ModsConfig.AnomalyActive` before it touches `mutant.Def`, so it is false rather
than throwing without Anomaly and reaches neither `Faction.OfPlayer` nor any
`Log.Error` path. `ghoulCount` counts live player-faction ghouls and is **not**
part of `colonistCount` — RimWorld does not count a ghoul as a colonist and
neither does this number. `notes.ghoulIsNotAColonist` says all of this in the
reply. `colonistsOnly` is unchanged and still means `Pawn.IsFreeColonist`.

**`ticksGame`** is `Find.TickManager.TicksGame` read on the same main-thread hop
as every row, so a caller printing "as of" prints this snapshot's own moment
rather than a second bridge call's — the two-moments misread that turned 100
cells into "8 from Longhoff" on 2026-09-07. `null` when no game is loaded.

**`thingId` rides on every row, unasked.** `Verse.Thing.ThingID` —
`Ibex404123` — the exact spelling `home/pawn_config`, `home/order`, `act.py`,
`order.py` and `pick.py` take. It lived only inside `settings{}` and `animals{}`
until 2026-09-11, so `list_pawns {wildOnly:true}` answered "which ibex" with a
name five of them share and no address for any of them, and `act.py hunt <id>`
had no id to be given. It is one short string per row, from the same
`pawn.ThingID` call the two blocks make, so a row and a block can never
disagree about how to address one pawn. Note that it is **not** the
`GetUniqueLoadID()` form `home/status` prints: that one carries a `Thing_`
prefix.

`pawnsListed` is `pawns[]`'s length under the name that says what it counts and
`pawnsFiltered` is `spawnedPawnTotal - pawnsListed`, counting every narrowing
together — so it is non-zero on a default call whenever a corpse is on the map.
`filters{}` echoes all ten new names alongside the old ones, and the existing
counts keep their old meanings; `notes.whatEachCountCounts` spells that out.

- **`health{}`** *(may be null — no health tracker)*: `downed`, `dead`, `state`,
  `inBed`, `summaryPct`, `painTotal`, `bleedRatePerDay`, `bleeding`,
  `hoursUntilDeathFromBloodLoss`, `needsTend`, `selfTend`, `medicalCare`,
  `capacitiesImpaired{}`, `capacitiesRead[]`, `bloodLoss`, `anyLifeThreatening`,
  `tendableHediffCount`, `hediffCount`, `hediffsHiddenByVisibleFilter`,
  `hediffs[]` — each `label`, `labelBase`, `severityLabel`, `defName`,
  `severity`, `part`, `partDefName`, `bleeding`, `bleedRate`, `tendableNow`,
  `isTended`, `tendQuality`, `permanent`, `lifeThreatening`, `isBad`, `visible`.
- **`needs{}`** *(may be null — mechanoids have none)*: `all{}`, `food`,
  `hungerCategory`, `rest`, `joy`, `mood`, `breakThresholdMinor/Major/Extreme`,
  `breakRisk`, `mentalState`. Every scale is 0..1.
- **`equipment{}`**: `hasEquipmentTracker`, `armed`, `primary`, `primaryLabel`,
  `equipped[]`, `equippedCount`, `hasInventoryTracker`, `inventoryWeapons[]`,
  `inventoryWeaponCount`, `inventoryItemCount`, `hasApparelTracker`, `apparel[]`,
  `apparelCount`. Gear rows share one shape: `slot`, `defName`, `label`,
  `labelBase`, `quality`, `stuff`, `stackCount`, `isWeapon`, `ranged`, `melee`,
  `isApparel`, `bodyPartGroups[]`, `apparelLayers[]`, `hitPoints`,
  `maxHitPoints`, `conditionPct`.
- **`bio{}`**: `hasAgeTracker`, `ageBiological`, `ageChronological`, `hasStory`,
  `childhood`, `adulthood`, `traits[]`, `traitCount`, `skills[]`, `skillCount`,
  `incapableOf[]` (the labels the game draws), `incapableOfTags[]` (raw
  `WorkTags`), `incapableOfCount`, `incapableOfRead`, `disabledWorkTagsFlags`,
  `title`, `titleSource`.
- **`thoughts{}`**: `hasMood`, `mood`, `moodPercent`, `memories[]`,
  `memoryCount`, `situational[]`, `situationalCount`, `situationalCacheReadable`,
  `situationalCacheStale`, `moodOffsetTotal`, `memoryMoodOffsetTotal`,
  `situationalMoodOffsetTotal`. Rows: `label`, `defName`, `count`, `moodOffset`
  (the stack total), `moodOffsetEach`, worst first.
- **`animals{}`**: `isAnimal`, `applies`, and on a non-animal a `note` and
  nothing else. On an animal: `tame` (player faction), `wild`, `otherFaction`,
  `faction`, `race`, `kindLabel`, `named`, `name` (null when unnamed — a state,
  not a failed read), `thingId`, `gender`, `ageYears`, `lifeStage`, `bodySize`,
  `baseHungerRate`, `petness`, `predator`, `packAnimal`, `canReleaseToWild`,
  `wildness` (a **StatDef** in 1.6, not a `RaceProperties` field),
  `trainability`, `minimumHandlingSkill`, `bonded[]`, and three sub-objects.
  **`training{}`**: `applies` (false with a note on an animal with no training
  tracker — wild animals have none), `stepsReadable`, `trainables[]` (`name`,
  `label`, `wanted` — the tick box, `learned`, `canTrain`, `visible`, `reason` —
  RimWorld's own sentence when not assignable, `canBeTrainedNow`, `stepsDone`,
  `stepsTotal`, `steps` "x/y"), `nextToTrain`; the rows are in
  `TrainableUtility.TrainableDefsInListOrder`. **`designations{}`**: `all[]` plus
  `slaughter`, `releaseToWild`, `tame`, `hunt` hoisted as bools — "marked for
  slaughter" is one field. **`produce{}`**: egg/milk/wool fullness (0..1) and
  `milkFull`/`woolFull` (`activeAndFull`, the trustworthy one — `Active` is
  protected and unreadable), `pregnant`, `gestationProgress`.
- **`work{}`, `schedule{}`, `settings{}`, `relations{}`** come from the same
  reader `home/pawn_config` uses; keys are listed there. **`settings.thingId` is
  `Pawn.ThingID`, with no `Thing_` prefix** — `Thing.GetUniqueLoadID()` is
  literally `"Thing_" + ThingID`, so this id and `home/status`'s
  `colonists[].thingId` are one prefix apart and a caller keying both has to
  normalise. `combat.py` builds its roster from
  `{humanlikeOnly|mechanoidsOnly, settings: true}` and depends on that, and on
  the row bools `ghoul` / `playerFaction`.

**Never-null is the contract** for all eight of `equipment`, `bio`, `thoughts`,
`work`, `schedule`, `settings`, `relations`, `animals`: a block that does not
apply says `applies: false` rather than going missing. `armed` is always a bool and
`primaryLabel` is the literal word `"unarmed"`, so an unarmed colonist cannot be
mistaken for an unread one; `incapableOfRead` separates "no incapabilities" from
"could not be read". Only `health` and `needs` may be null.

**`animals` adds a block and narrows nothing.** A humanlike gets
`applies: false` rather than a missing key, and the top-level `animalCount` and
`tameAnimalCount` are on every reply. **Nothing in `animals{}` is a second
copy**: `master`, `followDrafted`, `followFieldwork` and `allowedArea` live in
`settings{}` and hunger in `needs{}`, which `settingsBlockCarries` says out
loud. `Pawn_TrainingTracker.GetSteps` is internal and reached by reflection —
`stepsReadable` is the tripwire. `DesignationManager.DesignationOn` is not used
(its wrong-type arm is a `Log.Error`); `AllDesignationsOn` is.

**Two reflection hazards, both about deleting memories.** Reading mood through the
Mood tab's getters (`GetDistinctMoodThoughtGroups` → `UpdateAllMoodThoughts` →
`Thought_Situational.Notify_BecameActive`) **deletes every memory of the def that
thought produces**, so `thoughts` reads `memories.Memories` and the private
`SituationalThoughtHandler.cachedThoughts` field by reflection.
`Pawn_RelationsTracker.OpinionOf` reaches the same delete path, so `relations`
**reconstructs** opinion from relation offsets, social memories and the
uncalculated cache — `opinionMethod` states the chain, and a row with
`situationalSocialCached: false` is a **floor**, not an exact number.

Also avoided: `TraitSet.TraitsSorted`, `Pawn_SkillTracker.GetSkill`,
`CharacterCardUtility.WorkTagsFrom` (not public). Verified reads:
`HediffSet.PainTotal`/`.BleedRateTotal`, `PawnCapacitiesHandler.GetLevel`,
`Hediff.SeverityLabel`/`.Visible`/`.TendableNow()`, `HediffUtility.IsTended()`,
`HealthUtility.TicksUntilDeathDueToBloodLoss()`, `Need.CurLevelPercentage`,
`MentalBreaker.BreakThreshold*`, `RestUtility.InBed()`,
`Pawn_EquipmentTracker.Primary`, `Pawn_ApparelTracker.WornApparel`,
`QualityUtility.TryGetQuality`, `ApparelProperties.bodyPartGroups`/`.layers`,
`Pawn.CombinedDisabledWorkTags`, `TraitDef.DataAtDegree`,
`MemoryThoughtHandler.Memories`, `Thought.GroupsWith`/`.MoodOffset()`.

### `home/list_things`

Filters: `match`, `category` (`haulable` default / `all` / `buildings`),
`ownership` (`ours` default / `all`), `includeHeld` (default true),
`forbiddenOnly`, `excludeChunks`, `x`/`z`/`radius`, `maxPositionsPerDef`,
`corpses` (default false), `maxCorpsesPerRow` (50).

A row, always: `defName`, `label`, `stacks`, `total`, `oursStacks`, `ours`,
`oursUnforbidden`, `forbidden`, `inStockpile`, `inHomeArea`, `fogged`, `carried`,
`traderStock`, `otherFaction`, `inContainer`, `reserved`, `holders`,
`positions[]`, `positionsNotListed`. `stacks`/`total` are **whole-map** figures in
both ownership modes and `oursStacks`/`ours` the colony's share, so the two modes
reconcile against each other; only which rows are emitted, which positions are
listed and `itemTotal` change with `ownership`. Every count except the two
`*stacks` is in units.

**The buckets overlap; `holders` does not.** A stimulant in an ancient soldier's
pocket inside an unopened casket is `fogged` **and** `otherFaction` **and**
`carried`; `holders` is the partition, where every not-ours unit lands under
exactly one key (a pawn's label with faction and caravan role, `"corpse of X"`, a
container's label, `"fogged (unopened ruin)"`, `"faction: X"`).

`ours` means the colony could use it now: spawned and unfogged with no other
faction's claim, or held by a player pawn, or in a spawned unfogged container the
player owns. **Fogged is disqualifying** — ruin loot is unreachable until somebody
breaches the room. `ours` is not `tradeable`, which counts only what is in range of
the trade spot or a beacon. Deliberately not counted, each said out loud in
`notes`: worn apparel and wielded weapons (walking them would add every colonist's
parka to the clothing count); orbital trade ships; resources already delivered to a
`Blueprint` or `Frame`, which are that site's `have` in `home/list_buildings`.

Four things reflection settled: `map.listerThings` holds **spawned things only**,
so trader stock in `Pawn.inventory.innerContainer` was invisible to the old tool
and what it over-counted was fogged and other-faction loot;
`ThingOwnerUtility.GetAllThingsRecursively` reaches orbital traders and walks
apparel and equipment, so the roots are walked by hand; non-generic `ThingOwner`
has no `InnerListForReading`; `TraderCaravanUtility.GetTraderCaravanRole` returns
`Carrier` for **any** pack animal with a non-empty inventory, our own muffalo
included, so `traderStock` is `(Trader || Carrier) && faction != player`. Also
used: `Thing.PositionHeld` (not `Position` — every fog and home-area test uses
it), `FogGrid.IsFogged`, `AreaManager.Home`,
`ReservationManager.AllReservedThings()`, `Corpse.InnerPawn`.

**Corpses.** `corpses: true` counts ONLY corpses; every other filter still
applies and `skipped.byCorpsesOnly` says how many non-corpses it removed.
`maxCorpsesPerRow` (50) caps the per-body sub-list. Three top-level keys are
**always present, zeros included**: `corpseTotal`, `corpseSkeletonTotal`
(`RotStage.Dessicated`) and `corpsesSkipped[]` (`defName`, `count`, `reason`),
and every row gains `corpse` (bool). A corpse row also carries `corpseCount`,
`skeletons`, `rotStages{fresh, rotting, dessicated, unknown}`, `corpses[]`,
`corpsesListed`, `corpsesNotListed`, `corpsesTruncated`, and its `label` is the
**def's** label ("Human corpse"), not the first body's name.

**Why a sub-list.** A corpse is a `Verse.Corpse` with a generated def,
`Corpse_<race>`, so corpses were always in this census, aggregated by def.
Meat-or-skeleton is a per-BODY fact, so the aggregation contract is untouched
and the per-body facts hang off the row. A `corpses[]` entry: `race`, `name`
(null if the pawn never had one), `humanlike`, `wasColonist` (a faction test —
a dead colony muffalo is `true` too), `rotStage` (`"fresh"`/`"rotting"`/
`"dessicated"`, or null), `skeleton` (`rotStage == "dessicated"` and nothing
looser), `ours`, `forbidden`, `position{x,z}`, `holder` (null = lying on the
map; otherwise a grave, a casket, a pawn's pack). `corpsesListed +
corpsesNotListed == corpseCount`. A **mechanoid** corpse has no `CompRottable`
(`ThingDefGenerator_Corpses` adds it only when `!race.IsMechanoid`), so its
`rotStage` is null with reason `noRotComp` — never guessed as fresh.
`CompRottable.Stage` is a pure read.

### `home/list_buildings`

Filters: `match`, `status` (`all`/`built`/`blueprint`/`frame`/`pending`),
`category` (`artificial` default / `all`), `playerOnly`, `aggregate` (default
true), `damagedBelowPct`, `x`/`z`/`radius`, `maxPositionsPerDef`, `maxDetailed`
(400), `inspect` (default false), `billIngredients` (default false).

```
counts      { scanned, detailed, aggregatedRows, aggregatedBuildings,
              blueprints, frames, built }
attention   { blueprints, frames, pendingMissingResources, unpowered,
              notConnectedToPower, switchedOff, brokenDown, outOfFuel,
              billGiversWithNoBills, billGiversWithNoActiveBill,
              finishedBills, suspendedBills, damagedBuildings }
resourceDeficit[] { defName, label, stillNeeded, sites,
                    onMapTotal, countedAsResource }
powerNets[] { index, transmitterCount, connectorCount, producerCount,
              consumerCount, batteryCount, playerBuildingCount, buildingCount,
              generationW, consumptionW, netW, storedWd, storedMaxWd,
              hasPowerSource, hasActivePowerSource, flags[], buildings[],
              buildingsNotListed }
powerSummary { netCount, flaggedNetCount, readable, error, flags{} }
skipped     { byMatch, byStatus, byRadius, byPlayerOnly, byMaxDetailed }
filters{}  notes{}  buildings[]  aggregated[]
```

**`buildings[]`**, always: `defName`, `label`, `thingId`, `position{x,z}`,
`status`, `isBlueprint`, `isFrame`, `faction`, `stuff`, `rotation`, `rotatable`,
`reasons[]`. **`thingId` is the row's handle**: it is what `home/building_config`
and `home/bills` take to address exactly this building, with no ambiguity to
resolve and no position arithmetic. Conditionally `occupies` (only when the footprint exceeds 1x1 —
absence means one cell at `position`), `hitPoints`/`maxHitPoints` (only when
`def.useHitPoints`), and the blocks: pending (`buildDefName`, `buildLabel`,
`workLeft`, `workToBuild`, `percentComplete`,
`resources[]{defName,label,have,need,stillNeeded}`, `resourcesComplete`,
`materialCostUnreadable`); `power` (`powered`, `connected`, `switchedOn`,
`brokenDown`, `powerOutput`, negative = drawing); `fuel`; `bills` (`index`,
`label`, `recipe`, `repeatMode`, `repeatCount`, `targetCount`, `repeatInfo`,
`suspended`, `paused`, `finished`, `active`, `completableEver`); and
`ownerNames[]`/`medical` on any `Building_Bed`.

**`aggregated[]`**: `defName`, `label`, `status`, `count`, `stuff`, `stuffVaried`,
`damaged`, `worstHitPointsPct`, `promotedOut`, `positions[]`, `positionsListed`,
`positionsNotListed`, `positionsTruncated`, `positionsCap`, `distinctCells`,
`positionsUnreadable`. **`count == positionsListed + positionsNotListed` by
construction** — all three come off one cell list at emit time. `distinctCells`
separates the other reason a count can exceed its coordinates (several things of
one def on one cell) from the cap, because raising the cap fixes one and not the
other. `promotedOut` is how many instances left the aggregate for a row of their
own, so `count + promotedOut` is the def's whole population.

**Promotion rule.** Built structures aggregate by def; anything actionable gets a
full row with `reasons[]`: `blueprint`, `frame`, `billGiver`, `bed`, `turret`,
`unpowered`, `switchedOff`, `brokenDown`, `outOfFuel`, `damaged`. Damage promotes
only when `damagedBelowPct > 0`, or a post-raid map floods the payload.
Blueprints and frames are processed first and are never truncated by
`maxDetailed`. `attention` and `skipped` are emitted in full including zeros.
`position` is `Thing.Position`, which for a multi-cell building is **not** the min
corner — that is `occupies`, from `GenAdj.OccupiedRect`. Plants and zones are not
countable here at all; that is `home/list_zones`.

Four things reflection settled: **`Bill_Production.ShouldDoNow()` mutates the
bill**, writing `paused` at three IL offsets, so it is never called and `finished`
is computed from fields; `Blueprint_Build.BuildDef`/`Frame.BuildDef` are casts
that throw on flooring, so the `BuildableDef` field is read directly; `Blueprint`
and `Frame` live in `RimWorld`, not `Verse`, and are **not** in
`ThingRequestGroup.BuildingArtificial`, so all three groups are queried and
de-duplicated by reference; `CompProperties_Power` has no consumption field in
1.6, so `CompPowerTrader.PowerOutput` is what to report. Also used:
`IConstructible` (implemented by both `Blueprint` and `Frame`),
`Frame.resourceContainer`/`.WorkLeft`, `StatExtension.GetStatValueAbstract`,
`ResourceCounter.GetCount`, `CompFlickable.SwitchIsOn`,
`CompBreakdownable.BrokenDown`, `CompRefuelable.HasFuel`, `Rot4.ToStringHuman()`.

**`powerNets[]` is on every reply** — the whole-map grid, one row per PowerNet, flagged `noProducer` / `noConsumer` / `isolatedBattery` / `isolatedTransmitter`, with the buildings named on a flagged net only (capped at 12, `buildingsNotListed` says how many were cut) and never flagged when no player-faction building sits on it. It exists because `attention.notConnectedToPower` structurally cannot see an orphaned battery: `CompPowerBattery` is not a `CompPowerTrader`, so it has no `powered` flag that could be false. Reads used: `PowerNetManager.AllNetsListForReading`, `PowerNet.transmitters/connectors/powerComps/batteryComps`, `CurrentStoredEnergy()`, `CompProperties_Power.PowerConsumption` (the sign of the DEF's base draw decides producer vs consumer — `PowerOutput` is 0 on a dark solar panel), `CompPowerBattery.StoredEnergy`, `CompProperties_Battery.storedEnergyMax`.

`onMapTotal` and `countedAsResource` are different numbers on purpose: the first
sums `stackCount` over every spawned stack, forbidden and unstored included; the
second is RimWorld's own resource readout, whose storage rules we did not verify.

**`inspect: true`** gives every `buildings[]` row an **`inspectString`** — the
text RimWorld's inspect pane would draw for that thing, rich-text tags stripped
and the pane's lines joined with `" | "`. Three values, three meanings: a string
is the pane text; `""` is "this thing has nothing to say"; `null` is "the read
threw", and a null is **always** accounted for in **`inspectSkipped[]`**
(`defName`, `count`, `error`) with `inspectSkippedThings` summing the counts.
Both keys exist only when `inspect: true`; `filters.inspect` is present in both
modes. **Aggregated rows never get one** — an `aggregated[]` row is a def, not a
thing; `aggregate: false` gives an inspect string per building.

This is `Thing.GetInspectString()` and nothing else, and it needs **no
selection**. `ThingWithComps.GetInspectString()` concatenates every comp's
`CompInspectStringExtra()`, so the power line comes free: `CompPowerTrader`
gives "Power needed: N W" / "Power output: N W", its base `CompPower` gives
"Not connected to a power grid", `CompRefuelable` the fuel line,
`CompBreakdownable` "Broken down". A mini-turret reads
`Power needed: 80 W | Grid excess: 520 W (0 Wd stored) | Shots until barrel
change: 34 / 60`; a fuelled stove reads `Work speed factor: 56% (outdoors, bad
temperature) | Fuel: 24 / 50`.

**`GetInspectStringLowPriority()` is deliberately never called.**
`Verse.Building` overrides it and its first act is
`DeconstructibleBy(Faction.OfPlayer)` — the banned property, whose failure path
pauses the colony. The deterioration and "attack to destroy" lines are the only
text this loses. When `Prefs.DevMode` is on, a modded comp whose inspect text
ends in whitespace triggers `Log.ErrorOnce` inside
`InspectStringPartsFromComps`; the payload emits `notes.inspectDevModeWarning`
in that case.

**`billIngredients: true`** answers the question the inspect pane cannot:
`Building_WorkTable` does not override `GetInspectString` and `Bill` has no
inspect text at all, so no amount of pane-reading says why a queued bill is not
running. Under the opt-in every `bills[]` row gains `ingredients[]`, `canRunNow`
and `blockedBy[]`, computed off the map exactly as `home/bills` computes them
and documented there. `attention.billsShortOfIngredients` appears only under the
opt-in; `filters.billIngredients` is present in both modes and
`notes.billIngredientsAreOptIn` says why. The `Component: 0 / 3` line is a
*frame's* inspect pane, and this tool already has it structurally as
`resources[]`.

### `home/list_rooms`

Parameters: `x`/`z` (alone: which room covers that cell), `width`/`height` (with
x,z: a `roomGrid` over that rectangle), `cells`, `includeOutdoors`,
`includeBoundary`. No rectangle cap on the census itself — `RegionGrid.AllRooms`
is one property access over the whole map; the per-room cell walks are the cost.

Top level: `mapName`, `roomCountTotal`, `roomCount`, `roomsOmitted`,
`outdoorRoomsOmitted`, `doorwaysOmitted`, `dereferencedRoomsOmitted`,
`includeOutdoors`, `includeBoundary`, `cellsListed`, `unit`, `rooms[]`,
`omitted[]` (`id`, `name`, `cellCount`, `reason`), `notes{}`. Conditionally:
`cell{}` (`x`, `z`, `inBounds`, `found`, `roomIndex`, `roomId`, `why`) when x,z
was asked without a rectangle; `rect{}`, `roomGrid[][]`, `gridCellsWithNoRoom`,
`gridCellsInUnlistedRooms`, `gridCellsOutOfBounds`, `gridRoomsInRect` when a
rectangle was.

A room row: `index`, `id`, `name`, `gameLabel`, `role`, `roleLabel`,
`extents{x,z,width,height}`, `center`, `cellCount`, `cellsWalked`, `properRoom`,
`psychologicallyOutdoors`, `outdoors`, `fogged`, `isDoorway`, `doorDef`,
`touchesMapEdge`, `openRoofCount`, `temperature`,
`stats{cleanliness,wealth,space,beauty,impressiveness}` (each `value`/`label`/
`display`), `owners[]`, `ownersRead`, `beds[]`, `bedCount`, `contents[]`,
`contentsNotListed`, `looseThingCount`, `boundaryThingsExcluded`, `pawns[]`,
`pawnCount`, `stockpiles[]`, `stockpileCellsInRoom`, `skipped[]`, plus
`cells[]`/`cellsNotListed`/`cellsComplete` with `cells: true`.

`name` is not player-given identity: it is the role label plus bed owners
("Bedroom (Lucas)"), recomputed every call, because `id` (`Room.ID`) changes
whenever a wall changes — hence no external key file, on purpose; `index` is
response-local. The outdoors mega-room (≈49,000 cells), doorway rooms and
dereferenced rooms (`RegionCount == 0`) are **counted** in `roomsOmitted` and
named in `omitted[]` but not listed unless `includeOutdoors: true`. Invariants:
`roomCount + roomsOmitted == roomCountTotal`, and
`cellCount == cells.length + cellsNotListed` always; caps are 60 building defs and
20,000 cells per room, stated in the row. `outdoors` is `UsesOutdoorTemperature`,
`psychologicallyOutdoors` is RimWorld's separate mood flag, and a large under-roof
room can be one and not the other.

**Cached-buffer hazard.** `Room.ContainedAndAdjacentThings` clears and refills one
`HashSet` and one `List` **on the room itself**, so a second read invalidates the
first; it is copied into a local list the moment it is read, and beds, contents
and boundary are all asked of the copy. `Room.ContainedBeds` and `Room.Owners`
re-read that live property lazily *while yielding*, so `Owners` is consumed to
completion before anything else touches the room. `Zone.Cells` (capital C)
shuffles the zone's cell list in place, so stockpile overlap comes from
`ZoneManager.ZoneAt(cell)`, the authoritative grid. The whole census runs in one
main-thread hop, because rooms are destroyed and remade when a wall changes.

Verified: `RegionGrid.AllRooms`/`.RoomLookup`; `Room.ID` (a public int **field**),
`.ExtentsClose`, `.Cells`, `.CellCount`, `.RegionCount`, `.Role`,
`.GetRoomRoleLabel()`, `.Owners`, `.ContainedBeds`, `.ContainedAndAdjacentThings`,
`.ProperRoom`, `.PsychologicallyOutdoors`, `.UsesOutdoorTemperature`, `.Fogged`,
`.IsDoorway`, `.Door`, `.TouchesMapEdge`, `.OpenRoofCount`, `.Temperature`,
`.ContainsCell`, `.GetStat(RoomStatDef)`; `RoomStatDef.GetScoreStage`/
`.ScoreToString`; `RoomRoleDef.PostProcessedLabelCap`; `ZoneManager.ZoneAt`;
`Building_Bed.OwnersForReading`/`.Medical`/`.ForPrisoners`.

### `home/get_cells_plus`

Row-major from the top-left corner, max 1024 cells. **The rectangle may be given
two ways**: `{x, z, width, height}` (origin plus extent, both defaulting to 1) or
`{x0, z0, x1, z1}` (INCLUSIVE corner to corner, so `x0:10, x1:41` is 32 cells
wide). `x0`/`z0` are aliases of `x`/`z`; reversed corners are normalised, not
refused (`cornersSwapped: true`, omitted when false). All eight names are declared
and default to `int.MinValue`, because the binder fills an undeclared name with
zero and a negative coordinate must come back as out of bounds — a true answer —
rather than "you sent nothing". Arguments that do not describe exactly one
rectangle of at least one cell are refused, with `argumentsSeen` quoting what
arrived and `expected` spelling out both shapes. `rect` is echoed in **both**
shapes plus `argumentShape`, the caller's own names in canonical order.

Leaner than `rimworld/get_cells_info` because payload size is the measured cost of
a map sweep: zone and area descriptors go once into top-level `zones`/`areas`
dictionaries and each cell carries only `zoneId`/`areaIds`, and null, false and
empty fields are omitted — 152 KB for a 1024-cell rectangle against 649 KB.

**Four fields are never omitted.** `forbidden` is true or false for anything with
a `CompForbiddable` — things without one omit the key, and absence there means
"cannot be forbidden". `ownerName` is emitted for every `Building_Bed`, explicitly
null when unassigned, with `ownerNames[]` when there is more than one. `walkable`
(`IntVec3.Walkable`, the **path grid**) and `passable` (`!IntVec3.Impassable`,
terrain and edifice passability) are on every cell and are two different questions
— a cell can be passable terrain and unwalkable to the path grid, and the reverse.
If either read throws, the field carries the **string** `"unknown"`. `fogged`
stays omit-when-false. Free and included: `stackCount` (when ≠ 1), `medical` on
beds, `growth`/`harvestableNow` on plants. A multi-cell thing appears once per
cell it occupies, so callers counting across a rectangle must de-duplicate.
Verified: `CompForbiddable.Forbidden`, `Building_Bed.OwnersForReading`/`.Medical`,
`Plant.Growth`/`.HarvestableNow`, `Pawn.LabelShortCap`.

**Three opt-in narrowings, all off by default.** A call that sends only a
rectangle gets the payload above, byte for byte; the three exist because a
32x32 block measures 178.7 KB and a caller usually wants three fields of it.

| call | payload | % of default |
|---|---|---|
| default (unchanged shape) | 178.7 KB | 100 |
| `thingFields:"defName,forbidden"` | 134.9 KB | 77 |
| `map.py`'s own selection (7 cell fields, 8 thing fields) | 130.5 KB | 74 |
| `fields:"terrain,roof,walkable"` | 73.9 KB | 42 |
| `fields:"things", sparse:true` | 72.2 KB | 41 |
| `summary:true` | 6.8 KB | 3.9 |

`fields` (string, comma-separated, case-insensitive, whitespace ignored) picks
the per-cell keys from `terrain`, `roof`, `fogged`, `walkable`, `passable`,
`zone`, `areas`, `things`, `designations`. `x` and `z` are always emitted.
`thingFields` picks the keys inside `things[]` from `label`, `className`,
`stackCount`, `stuff`, `hitPoints`, `forbidden`, `owner`
(`ownerName`/`ownerNames`/`medical`), `plant` (`growth`/`harvestableNow`),
`build` (`isBlueprint`/`blueprintBuildDefName`/`isFrame`/`frameBuildDefName`);
`defName` is emitted whatever is sent. Omitting either argument, sending an
empty string, or sending `"all"` selects everything. **An unrecognised name is
refused** — `success: false`, quoting the argument, naming the bad word, and
listing `accepted[]` — never ignored, because a name that is quietly dropped is
a caller who believes they narrowed and did not.

`fieldsApplied[]` and `thingFieldsApplied[]` are on **every** reply, including
the default one, sorted, in canonical spelling. They are how a reader tells
"deselected" from "absent": a key missing from a cell is explained by that list
or it is a bug. The per-field contracts hold unchanged *when the field is
selected* — `walkable` and `passable` on every cell, `forbidden` true-or-false
on every forbiddable thing, `ownerName` on every bed. `zones{}`/`areas{}` stay
top-level dictionaries and come back empty when `zone`/`areas` are not
selected.

`sparse` (bool, default false) omits cells that carry none of the **selected**
optional content — no `things`, no `designations`, no `zoneId`, no `areaIds` —
i.e. a cell whose only keys would be `x`, `z` and the scalars. **Fog is not
content**: an empty fogged cell is dropped like any other empty cell. A fogged
cell that holds a thing or a designation is still returned with its flag.
Callers that need the fog *shape* — every `map.py` layer draws `?` — must not
use `sparse`, and `map.py` does not.

`summary` (bool, default false) returns `cells: []` and a `summary{}` of
aggregates over the whole rectangle. **Under `summary` the cap is the whole
map**, not 1024: the reply is a fixed handful of aggregates however large the
rectangle, so the limit that exists to bound payload size does not apply. The
`cells[]` modes keep the 1024-cell cap. `cellCap`, `capReason` and
`mapSize{x,z,cells}` are on **every** reply, and a refusal names the cap that
was in force and points at `summary: true`. `fields` still governs which sections are
computed, so `{summary: true, fields: "things"}` is the cheapest "what is in
this rectangle" read there is. Sections: `terrain` (defName → cell count),
`roof` (defName → count plus `unroofed`, totalling `cellCount`), `fogged`
(int), `walkable` and `passable` (each `{true, false, unknown}`), `things`
(defName → `{count, stacks, forbidden, label}` where `count` is the total
`stackCount`, `stacks` the number of thing entries, `forbidden` the number of
forbidden entries), `pawns` (a sorted list of labels), `designations` (defName →
count), `zones` and `areas` (id → descriptor plus `cellsInRect`). Under
`summary` the top-level `zones{}`/`areas{}` are empty and the descriptors live
in `summary.zones`/`summary.areas`.

**The summary de-duplicates by thing identity and `cells[]` does not.** In
`cells[]` a 2x2 workbench appears in four cells; in `summary.things` it is
counted once. Designations are de-duplicated the same way, because
`DesignationManager.AllDesignationsAt` walks `thingGrid` and reports a
designation on a multi-cell thing at every one of its cells.

`cellsOmitted` (int) is on every reply and
`len(cells) + cellsOmitted == cellCount` holds in all three modes: 0 by
default, the dropped count under `sparse`, `cellCount` under `summary`.
**`cellCount` stays the number of cells in the RECTANGLE**, never the length of
`cells[]`.

On the Python side `map.py` asks for exactly the fields its seven layers read
and prints both applied lists on every run; because `normalise_plus()` no
longer defaults a missing `walkable` to true, an old DLL under the current
`map.py` falls back to the stock cell tool with a DEGRADED line rather than
degrading silently.

### `home/get_temperatures`

`get_cells_plus`'s rect semantics, with a cap of **16384** cells (128x128),
because a cell here is one number rather than a ~150-byte object. `mode` is
`rooms` (default) or `cells`. Both modes: `mode`, `mapName`, `rect`, `cellCount`,
`outdoorTemp`, `unit: "C"`, `notes`. `rooms` adds `roomCount`, `cellsWithNoRoom`,
`rooms[]`, `roomGrid`; `cells` adds `temps[][]` and `cellsWithoutTemperature`.

RimWorld does not simulate a temperature per cell — it simulates one per **room**
(`RoomTempTracker`), and every per-cell getter is a lookup of the containing
room's number, which is why `rooms` is the default. The grid carries the
**response-local index** into `rooms[]`, not `Room.ID`, because a room ID is a 6–8
digit int. RimWorld stores Celsius and converts only at the UI boundary.

**Never omitted:** every cell of the rect has an entry in `temps`/`roomGrid`, and
a cell with no answer is an explicit null, because a short row is
indistinguishable from a cold room. `temperature`, `outdoors`,
`psychologicallyOutdoors`, `properRoom`, `cellCount`, `cellsInRect`,
`representativeCell` and `center` are on every room row; the legend fields
`closestPawn`, `closestPawnDistance`, `notableBuilding` and
`notableBuildingOwner` are the deliberate exception, omitted when there is no
answer so a legend line can be built by testing presence. `center` is snapped to a
real cell of its own room. Same cached-buffer hazard as `home/list_rooms` on
`Room.ContainedAndAdjacentThings`, which also includes the room's own walls and
doors — filtered out by `BuildingProperties.isWall`/`isNaturalRock` and a
`Building_Door` type test, so no room may report `notableBuilding: "Wall"`.

**Two refusals, both with a reason.** A rectangle over 16384 cells is refused,
and so is `mode: "rooms"` with a 1x1 rectangle: the binder fills an omitted int
with 0, so a rooms call that named no rect would otherwise answer "one room, the
outdoors" — a complete-looking reply to a question nobody asked. Pass a width and
a height, or use `home/list_rooms`, which takes `x`/`z` alone to say which room a
single cell is in. Cells mode keeps its 1x1: one cell is one temperature there.
Every refusal from this tool carries the reason under **both** `error` and
`message`, because a bridge reply is read for `message`.

Verified: `GenTemperature.TryGetTemperatureForCell(IntVec3, Map, out float)` — it
falls back to `TryGetAirTemperatureAroundThing`, so a **constructed wall cell
returns a number** even with no room, and deep natural rock is what returns null;
`MapTemperature.OutdoorTemp`; `GridsUtility.GetRoom(IntVec3, Map)` (two args in
1.6); the `Room` members above; `Building_WorkTable`.

### `home/get_time`

No parameters, no cells read, nothing mutated. Always present whatever the state:
`status` (`game_loaded`/`no_game`), `hasMap`, `mapSource`, `ticksGame`,
`ticksAbs`, `ticksAbsAvailable`, `paused`, `forcePaused`, `timeSpeed`,
`hourInteger`, `dayOfQuadrum`, `dayOfQuadrumDisplay`, `dayOfYear`, `quadrum`,
`season`, `year`, `dateFull`, `mapName`, `tile`, `longitude`, `latitude`, `notes`.

**`TickManager.TicksAbs` can pause the game**: its IL is
`get_TicksAbs → Log.ErrorOnce → Log.Error → TickManager.Pause`, taken when
`gameStartAbsTick == 0`. The tool checks `gameStartAbsTick` — a public int
**field** — and reads `TicksAbs` only when it is non-zero, because probing the
property to find out whether it is safe to probe would itself stop the colony.
When it is zero, `ticksAbs` is null, `ticksAbsAvailable` is false, and the whole
calendar goes null with it.

Read `timeSpeed` together with `forcePaused`, never alone: `paused` is
`(timeSpeed == Paused || forcePaused)`, so a modal window makes `paused` true
while `timeSpeed` still says `Superfast`. **`dayOfQuadrum` and `dayOfYear` are
0-based and the screen is not** — both forms are emitted, because quietly picking
one is how a caller's "day 5" and the screen's "day 5" become different days. With
no map there is no longitude, so the calendar fields are null rather than computed
at longitude 0; `Find.CurrentMap` is null on the world view, so the tool falls
back to `Find.Maps[0]` and says which in `mapSource`.

Verified: `TickManager.TicksGame`/`.TicksAbs`/`.CurTimeSpeed`/`.Paused`/
`.ForcePaused`/`.gameStartAbsTick`. **`GenDate` is in `RimWorld`, not `Verse`**;
`HourInteger`, `DayOfQuadrum`, `DayOfYear`, `Quadrum` and `Year` take
`(long absTicks, float longitude)`, while `Season` and `DateFullStringAt` take a
`Vector2 longLat` — and a `Season(long, float latitude, float longitude)`
overload exists whose argument order is **reversed relative to every other method
here**, so the `Vector2` overload is used for both.
`WorldGrid.LongLatOf(PlanetTile) -> Vector2` (x is longitude). `Map.Tile` is a
`PlanetTile` struct with `.Valid` and a public int field `.tileId`; its `.Tile`
*property* is a different object entirely, and `map.Tile.Tile` where you meant
`map.Tile.tileId` compiles. `GenLocalDate` has no `Quadrum` accessor.

### `home/status`

The whole between-turns read in one call, in place of the clock, the letter
stack, the message list, the alert readout, the colonist roster and the threat
sweep. **Read-only**: it selects nothing, opens nothing and never moves the
clock, so it has no `dryRun` and no `watch`.

Parameters: `colonists` (default **true**), `threats` (default **true**),
`explanations` (false), `colonistDetail` (false), `predatorRadius` (**30**;
0 disables the two proximity lists, ignored when `threats: false`). About 7 KB on a quiet colony
and 9 KB with eight letters standing; `{colonists: false, threats: false}` saves
roughly 1.5 KB.

**Every block is always present and empty rather than absent**, and `blocks{}`
restates what was asked, so a reader can tell "nothing there" from "not asked
for". Top level: `status` (`game_loaded`/`no_map`/`no_game`), `time{}`,
`letters[]`, `messages[]`, `alerts[]`, `colonists[]`, `threats{}`, `ui{}`,
`counts{}`, `blocks{}`, `skipped[]` (`field`, `reason`), `notes{}`.

- **`time{}`**: `ticksGame`, `ticksAbs`, `ticksAbsAvailable`, `paused`,
  `pausedByPlayer`, `forcePaused`, `timeSpeed`, `hourInteger`, `dayOfQuadrum`
  (0-based) and `dayOfQuadrumDisplay`, `dayOfYear`, `quadrum`, `season`, `year`,
  `dateFull`, `mapName` — the same reads, and the same `TicksAbs` guard, as
  `home/get_time`.
- **`letters[]`**, newest first, capped at 40 with the overflow in `skipped[]`:
  `id` — the **same id `rimworld/list_letters` uses**, which is what
  `letters.py open` takes — `label`, `letterDef`, `type`, `arrivalTick`,
  `ageTicks`, `shouldAutomaticallyOpenLetter`, `canDismissWithRightClick`,
  `choiceCount`, `hasChoices`, `choices[]` (`index`, `text`, `disabled`,
  `disabledReason`, `closesDialog`), `lookTarget`. No letter body text.
- **`messages[]`**, newest first, capped at 16: `id`, `text`, `messageType`,
  `startingTick`, `ageTicks`, `startingFrame`, `ageSeconds`, `expired`,
  `lookTarget`. `ageSeconds` is real seconds, which is the clock a message
  actually expires on.
- **`alerts[]`**, loudest first: `ordinal`, `type`, `label`, `priority`,
  `prioritySortValue`, `active`, `explanation` (null unless
  `explanations: true`), `targets[]` (`kind`, `name`, `thingId`, `defName`,
  `position`), `targetCount`, `targetsTruncated`, `culpritsReadable`.
- **`colonists[]`**: `name`, `thingId`, `position`, `dead`, `downed`, `drafted`,
  `inBed`, `job`, `mentalState`, `mood`, `breakRisk`, `healthPct`, `needsTend`,
  `bleeding`, `bleedRatePerDay`; `colonistDetail: true` adds `needs{}` and
  `hediffs[]`.
- **`threats{}`**: **four lists, none a subset of another.** `hostileCount`,
  `hostiles[]` (`name`, `thingId`, `defName`, `kindDef`, `faction`, `position`,
  `hostileReason`, `job`, `downed`, `predator`, `manhunterOnDamageChance`,
  `distanceToNearestColonist`), `huntingPredatorCount`, `huntingPredators[]`,
  `huntersIgnored[]` with an `ignoredReason` each, plus
  `wildPredatorsNearCount` / `wildPredatorsNear[]`, `downedNearCount` /
  `downedNear[]`, and `predatorRadius` echoing the radius used. Every row in all
  four lists has the same shape.
- **`ui{}`**: `mainTabOpen`, `mainTabDefName`, `mainTabLabel`, `selectedCount`,
  `selectedFirstLabel`, `modalOpen`, `modalWindow`, `windowsForcePause`,
  `anyWindowAbsorbingAllInput`, `nonImmediateDialogWindowOpen`, `windowCount`,
  `windows[]` (`type`, `layer`, `title`, `forcePause`,
  `absorbInputAroundWindow`), and **`targeter{}`**: `active`, `source`,
  `caster`. `modalOpen` is the blocking-window check.
- **`counts{}`**: `letterCount`, `letterChoiceCount`, `messageCount`,
  `alertCount`, `loudAlertCount`, `colonistCount`, `downedCount`,
  `hostileCount`, `huntingPredatorCount`, `wildPredatorsNearCount`,
  `downedNearCount`.

**`ui.targeter` is the one piece of screen state a window diff cannot see.**
A RimWorld targeter — the placement mode a `Command_VerbTarget` or
`Command_Target` gizmo opens, e.g. `Deploy turret` on a worn turret pack — is
**not a `Window`**, so `modalOpen`, `windows[]` and the bridge's own
`click_ui_target` `changed` flag are all identical on both sides of the click
that opened it. That is why `ui.py click` reported *"nothing opened or closed"*
on a gizmo that had worked, for fourteen turns on 2026-09-08. `active` true
means **the next map click is eaten as a target, not as a selection**. `source`
is the gizmo/verb label (`verbProps.label` for a verb source; for a
`Command_Target`, recovered from the gizmo that owns the targeter's
`onUpdateAction` delegate, since that overload leaves `targetingSource` null).
`caster` is a pawn `thingId` or null. **Hazards:** `Find.Targeter` is
`((UIRoot_Play)UIRoot).mapUI.targeter`, a hard cast that throws outside a play
UI root — it is read through `BridgeCommon.Try` and an unreadable one lands in
`skipped[]` as `ui.targeter` rather than reporting `active:false`;
`Targeter.caster` is private and is reached by reflection; `Command.LabelCap`
is virtual, so the plain `defaultLabel` field is read first. **A targeter cannot
be closed through the bridge's input tools** — `rimworld/press_cancel` is
`WindowStack.Notify_PressedCancel()`, and a synthetic key or right-click never
reaches `Targeter.ProcessInputEvents` (both confirmed dead live, 2026-09-11).
`rimworld/clear_selection` does close it, through `ConfirmStillValid`.

**`wildPredatorsNear[]` and `downedNear[]` exist because `hostiles[]` and
`huntingPredators[]` structurally cannot hold them.** A wild wolf that has not
started a `PredatorHunt` yet is hostile to nobody and hunting nothing, so on
2026-09-04 the boars fifteen cells from a colonist appeared in **no** list.
`wildPredatorsNear[]` is every spawned, non-dead, non-colonist,
**non-player-faction**, non-hostile, non-hunting pawn whose
`Verse.RaceProperties.predator` is true within `predatorRadius` Chebyshev cells
of a colonist, `hostileReason: "predator_near"`. The flag is the game's own — no
defName list anywhere. Separately, **a manhunter that goes down loses its mental
state**, so `IsHostile` stops being true and it silently leaves `hostiles[]`:
`downedNear[]` is every downed, non-dead, non-colonist, non-player-faction pawn
in the same radius, `hostileReason: "downed"`, which is what keeps the downed
muffalo one cell away visible. A downed wild predator appears in **both** lists
on purpose; the row's `downed` field tells them apart, so neither list has to be
read as the other's complement. **Neither feeds `hostileCount` or
`huntingPredatorCount`** — nothing in them is fighting yet, and `hostileCount: 0`
has to keep meaning what it means. Our own tame warg is excluded by the
player-faction test. Both lists cap at 40 like `hostiles[]`, and
`blocks.predatorRadius` distinguishes "switched off" (0) from "empty".

**`MapPawns.FreeColonists` and `.FreeColonistsSpawned` call `Faction.OfPlayer`**,
so the roster is `AllPawnsSpawned` filtered on `Pawn.IsFreeColonist`.
`Alert.GetReport()` is called once per **active** alert — it is the only source
of `targets[]`, and the stock `rimworld/list_alerts` makes the same call; across
roughly 200 `Alert` subclasses that is residual risk, stated rather than hidden.
`AlertPriority` has only `Medium`, `High` and `Critical`. Field names match what
`alerts.py` and `letters.py` already parse.

### `home/play_until_event`

One call is one bounded run: unpause at a requested speed, poll server-side until
something happens, pause, and report what stopped the clock.

**Invariants — the contract, not implementation detail.** External control always
wins: an outside pause returns `stopReason: "external_pause"` and does not
unpause, a speed change returns `"speed_changed"` and does not restore, a modal
window returns `"force_paused"`. The speed is set once at entry and nothing
outlives the call. It pauses at most once, on a watch hit, and verifies it
(`pauseVerified: false` with `success: false` if `TickManager` refused).
`requireRunningAtEntry: true` refuses to start on a paused game — a caller
chaining calls must set it, or the next call lifts somebody else's pause. A
second concurrent call returns `stopReason: "busy"`.

**The bridge runs one tool call at a time**, so keep `maxDurationMs` short and
chain calls with `pauseOnBudget: false` — a `pause_game` sent 3 s into a 20 s call
waited 17 s, and chaining at 1.5 s took it down to 1.08 s. A key press in the game
window is unaffected. But a chain ending with `pauseOnBudget: false` and no
explicit pause leaves the colony **running unattended**: end with a pause, or make
the last call `pauseOnBudget: true`.

Parameters: `speed` (`Superfast`; `Paused` rejected), `maxDurationMs` (5000,
clamped to 600000), `pollIntervalMs` (250, clamped 50..5000), `alertIntervalMs`
(0 = every poll; a check costs ~0.013 ms), `watchLetters`, `watchMessages`,
`messageTypesIgnored` (`RejectInput,CautionInput,SilentInput,TaskCompletion`),
`watchAlerts`, `minAlertPriority` (`High`), `alertDebounceMs` (0), `watchHostiles`,
`ignoreCurrentHostiles`, `huntWithin` (40), `watchDownedColonists`,
`healthBelowPct` (0), `pauseOnBudget`, `requireRunningAtEntry`. `healthBelowPct`
reads `SummaryHealthPercent`, RimWorld's **body-part damage** summary: food
poisoning still reads 100 %, a healed scar reads under it.

Combat watches are opt-in and exist only for the lifetime of this call. Set
`watchedPawnIds` to comma-separated stable Thing IDs (numeric IDs or full load
IDs ending in that number), then enable any of `watchMeleeThreats`,
`watchPawnOrders`, `watchInjuries`, or `watchInjuryHook`. With all four false (the default), the
probe does not read combat jobs, targets, or injuries and installs no hook or
background work. `meleeThreatPawnIds` narrows melee intent specifically (for
example, to flee-tagged pawns), while `injuryPawnIds` independently selects the
pawns whose injuries matter; each dedicated list falls back to `watchedPawnIds`
when empty. The resolved numeric ID lists are echoed under `watching`.
`watchMeleeThreats` stops when a hostile with `AttackMelee` against a
melee-threat pawn crosses within `meleeThreatWithin` Chebyshev cells (default 1,
adjacent/striking range). Only pairs already inside that radius are baselined,
so an attacker whose chase job existed at entry still fires when it closes.
`watchPawnOrders` compares
the job def plus target A/B/C with entry, so arrival/replacement can stop the
clock. `watchInjuries` coalesces every changed watched pawn in one poll and stops
when aggregate severity/bleed-rate growth since entry reaches
`injuryMinSeverityDelta` (0.01) / `injuryMinBleedRateDelta` (0.001). This means
several subthreshold wounds accumulate before one pause. A new `Hediff_Injury`
alone does not stop by default; `injuryStopOnNew:true` explicitly enables that
more sensitive behavior. A threshold of 0 disables that threshold. Down/death
is independently covered by `watchDownedColonists`.
`watchInjuryHook` is the immediate combat alternative: while the call is active,
a Harmony prefix/postfix around `Thing.TakeDamage` pauses synchronously
on the first actual injury to an `injuryPawnIds` pawn and returns its damage,
instigator, severity and bleed deltas as `injuryHookEvent`. It performs no tick
checks or pawn scans and is disarmed in a `finally` block on every exit path.
Normal responses expose only `injuryHookAvailable`; set `injuryHookDebug:true`
to include the exact patch target, Harmony owners, errors and invocation
counters. Those verbose diagnostics are also returned automatically when hook
installation fails, before the tool refuses to run the clock.

`stopReason` is one of `letter`, `message`, `alert`, `hostile`, `predator_hunt`,
`colonist_downed`, `colonist_health`, `budget_elapsed`, `external_pause`,
`melee_threat`, `pawn_order_changed`, `pawn_injury`, `pawn_injury_hook`,
`force_paused`, `speed_changed`, `session_changed`, `unavailable`, `cancelled`,
`busy`, `error`. Alongside it: `stopDetail`, `event`, `hitAtEntry`, `startTick`,
`endTick`, `ticksElapsed`, `elapsedMs`, `budgetMs`, `requestedSpeed`,
`speedAtEntry`, `speedAtExit`, `pausedAtEntry`, `pausedAtExit`,
`forcePausedAtExit`, `pausedByThisTool`, `pauseVerified`, `watching` (every watch
and its setting, the off ones included), `baseline` (letter and alert counts,
`alerts[]`, `liveMessageCount`, `downedColonists[]`, `ignoredHostiles[]` — what
can never fire), `timing`, `letters[]`, `messages[]`, `alerts[]`, `hostiles[]`,
`hunters[]`, `huntersIgnored[]`, `downed[]`, `hurt[]`, `messagesIgnored[]`,
`alertsDebounced[]`, `alertsDebouncedCount`, `notes[]`. Letter rows carry the
**same id** `rimworld/list_letters` uses.

**An alert already on screen must not stop a pulse — and twice it did.** On
2026-09-04 two `advance 20` calls burned ~0 game time each on a break-risk alert
that had been standing for minutes while a colonist bled out. The tool's own
promise is that alerts active at entry go into `baseline.alerts` and never fire.
Decompiling found **two independent causes**, and fixing either alone would not
have fixed it:

* **The key was unstable.** `BreakRiskAlertUtility.AlertLabel` appends
  `" x" + count` when more than one colonist qualifies, so `Mood break risk:
  minor` and `Mood break risk: minor x3` were two keys for one alert — the
  baseline held one and the next poll saw the other. The key is now
  `type|priority|`**normalized**` label`: a trailing ` x<digits>` is stripped out
  of the key only. The reported `label` is always what the game says right now.
* **The alert genuinely flaps.** `AlertsReadout.AlertsReadoutUpdate` re-checks
  1/24th of the alert list per **frame** (`AlertCycleLength` 24) and
  `CheckAddOrRemoveAlert` adds or removes with **no hysteresis and no minimum
  lifetime**. Under it, `Alert_MinorBreakRisk.GetReport()` →
  `BreakRiskAlertUtility.PawnsAtRiskMinor` → `MentalBreaker.BreakMinorIsImminent`
  is a strict `<` of the pawn's instantaneous `Need_Mood.CurLevel` against
  `BreakThresholdMinor`, also with no hysteresis. A colonist whose mood drifts
  across the threshold takes the alert out of `activeAlerts` and puts it back
  within ~0.4 s. An entry baseline taken in one of those troughs legitimately
  does **not** contain an alert that has been on screen for minutes. (Each re-add
  also calls `Alert.Notify_Started()` — the bell — and for
  `Alert_MajorOrExtremeBreakRisk`, inheriting `Alert_Critical`, a fresh
  `MessageCriticalAlert` message.)

`alertDebounceMs` (default **0** = the old behaviour, no memory) is the fix for
the second cause. An alert key that fired **or was baselined** within the last
`alertDebounceMs` real milliseconds does not fire again. The memo is a **static
dictionary keyed by alert key and survives across calls in the same game
process** — which is the whole point, because the Python side has no memory
between pulses. The stamp is refreshed on every poll the key is still standing,
so a continuously standing alert stays suppressed while it stands plus
`alertDebounceMs`. The memo is dropped when the loaded game changes: a different
`Current.Game` object, or a `TicksGame` that went backwards. Timing is a static
monotonic `Stopwatch`, not `DateTime.UtcNow`.

Suppressed alerts are **never a silent drop**: they come back in
`alertsDebounced[]` (run-long, deduplicated by key) as `alertKey`, `alertType`,
`label`, `priority`, `msSinceLastCounted` and `pollsDebounced`, with
`alertsDebouncedCount` beside it. `watching.alertDebounceMs` echoes the setting.
A run that stopped for `alert` with `alertsDebouncedCount: 0` stopped for
something genuinely new.

**There are three notification channels**, and **messages expire 13 real seconds
after they appear** — "the cold snap is over" is a `NeutralEvent` message, not a
letter and not an alert. That is why `messageTypesIgnored` is an ignore-list, not
an allow-list: an unrecognised type stops the run rather than being dropped.

**A predator hunt is only an event when it is somebody else's predator eating
ours** — a colony's own tame warg runs the same `PredatorHunt` job a cougar does,
and testing the job name alone froze a live stream on every call. So: the predator
must not be on the player faction, and the prey must be ours (`prey.Faction` or
`prey.HostFaction` is the player). Prey comes off `job.targetA`
(`JobDriver_PredatorHunt.PreyInd == 1`), which holds the prey `Pawn` during the
chase and its `Corpse` after the kill, so **both are unwrapped**. An ignored hunt
goes to `huntersIgnored[]` with an `ignoredReason`, and every hunter row carries
`predatorIsOurs`, `prey`, `preyDefName`, `preyFaction`, `preyIsOurs`.

**Four mutation hazards, all avoided.** `Alert.Recalculate()`/`.GetReport()` are
the recalculation path and are never called. `GameCondition.TicksLeft` and
`.Duration` call `Log.ErrorOnce` on a **permanent** condition, and `Log.Error`
pauses. `SummaryHealthPercent` writes `cachedSummaryHealthPercent` when dirty, so
it is read only when `healthBelowPct > 0`. And `TickManager.TogglePaused()`
branches on `curTimeSpeed`, not on `Paused`, so calling it while `ForcePaused`
would **pause a running game** — the tool refuses to start under `ForcePaused`.

Verified reads: `AlertsReadout.activeAlerts` (private `List<Alert>`, reflection;
RimWorld re-evaluates it once per `AlertCycleLength` = 24 Update frames, so
detection latency is bounded by ~0.4 s, not by `pollIntervalMs`),
`Alert.Active`/`.Label`/`.Priority`; `Messages.liveMessages` (private static,
reflection);
`TickManager.CurTimeSpeed`/`.ForcePaused`/`.Pause()`. What can change the speed
under a run, from an IL scan of every caller: `TimeControls.DoTimeControlsGUI`,
`LetterStack.ReceiveLetter`, `Verse.Log.Error`, and map generation / gravship /
quest / credits paths. **`TimeSlower` is not among them** — it changes
`TickRateMultiplier`, not `curTimeSpeed`.

### `home/supervised_play`

The supervised-play guard — in this DLL, but not among the tools listed in the
table at the top of this file. Every op answers with a status snapshot; only
the fields this pass added are stated here.

Two fields on the **status snapshot**:

- **`predatorRadius`** — the guard's `PredatorNearCells`, currently `12`. The
  Chebyshev radius inside which a conscious wild predator is a threat. Constant,
  reported so the caller never has to guess it.
- **`stopThreats`** — null until a threat stop; then the classified list,
  exactly as the stop's `threats[]` below. It is the only way the classification
  reaches a `start` refusal, because `start` answers with a snapshot and nothing
  else.

New **tool parameter**: `ignoredPredatorIds` (string, default `""`) —
comma-separated stable IDs, or the literal `all`. Covers **only** the
`predator_near` and `predator_hunting_ours` categories — its own parameter
description said `predator_hunt`, which is the stop kind and not a category,
until 2026-09-12; `ignoredHostileIds` still covers everything.

A threat stop's ring row (`op: "events"`) and its `event` payload:

```json
{ "kind": "predator_near",
  "detail": "wolverine [predator_near] wild predator within 12 cells of a colonist (8 cells from Finn)",
  "event": {
    "pawnId": 334862, "pawnName": "wolverine",
    "thingId": "Thing_Wolverine334862",
    "position": { "x": 118, "z": 131 },
    "category": "predator_near",
    "reason": "wild predator within 12 cells of a colonist (8 cells from Finn)",
    "predatorRadius": 12,
    "threats": [
      { "pawnId": 334862, "thingId": "Thing_Wolverine334862", "name": "wolverine",
        "defName": "Wolverine", "category": "predator_near",
        "reason": "wild predator within 12 cells of a colonist (8 cells from Finn)",
        "stops": true, "acknowledged": false, "downed": false,
        "distanceToNearestColonist": 8 },
      { "pawnId": 220114, "thingId": "Thing_Lynx220114", "name": "lynx",
        "defName": "Lynx", "category": "predator",
        "reason": "wild predator hunting wildlife, 34 cells from Finn; not a threat to the colony",
        "stops": false, "acknowledged": false, "downed": false,
        "distanceToNearestColonist": 34 }
    ] } }
```

`threats[]` is **every classified non-colonist on the map**, capped at 40, not
just the one that stopped the clock — including the `predator` rows that did
not, because "why that wolverine and not this one" is the operator's actual
question. `stops` is false on a row that is acknowledged
(`acknowledged: true`). Stop kinds: `hostile` (covers `category: "hostile"` and
`"manhunter"`), `predator_hunt` (`category: "predator_hunting_ours"`),
`predator_near`. Categories, in test order: `manhunter`,
`hostile_dormant`, `hostile`, `predator_hunting_ours`, `predator_near`,
`predator`.

**`hostile_dormant`** — a hostile whose current job is one of `LayDown`,
`LayDownResting`, `Wait_Asleep`, `RevenantSleep`, `Wait_AsleepDormancy`,
`ActivityDormant` **and** whose nearest colonist is more than
`DormantHostileCells` (50) Chebyshev cells away. `stops: false`, reason
`"asleep 76 cells away; wakes -> stops"`. Still a hostile and still in
`threats[]`; it stops the clock on the first probe after its job changes or it
walks inside 50 cells. A manhunter is never dormant — the aggro test runs
first and returns before the dormant branch. 50 is past every vanilla weapon's
range (44.9), and it is the same number and the same JobDef set `combat.py end`
uses — except the guard requires BOTH conditions where `end` accepts either.
A **downed** hostile more than 50 cells out holds `LayDown` and therefore
classifies here too; `hostiles_cleared` still names it to finish off or capture.

`Faction.OfPlayer` is never read here either: the predator line and
`CheckHostilesCleared` both go through `Faction.OfPlayerSilentFail`, because a
guard whose job is noticing pauses must not be able to cause one.

### `home/list_zones`

Read-only census of every zone in the map's `ZoneManager`. Parameters: `match`,
`x`/`z`/`radius`, `includeCells`, `includeContents` (default true),
`maxContentRows`, `maxCellsPerZone`, `filter` (default false).

Top level: `mapName`, `zoneCount`, `totals{listedCells, gridCells, phantomCells,
orphanGridCells}`, `zones[]`, `anomalies[]` (one English sentence per
disagreement; empty means the data is internally consistent), `filters`, `notes`.
A zone row: `label`, `id`, `type`, `hidden`, `listedCellCount`, `gridCellCount`,
`phantomCells[]`, `phantomCellCount`, `orphanGridCells[]`, `orphanGridCellCount`,
`consistent`, `contiguous`, `bounds`, a stockpile or growing block, and
`cells[]`/`gridCells[]` with `includeCells: true`. Stockpiles add priority, a
filter summary, occupancy (`cellsImpassable`, `cellsNotStandable`,
`cellsWithItems`, `cellsFree`), `contents` and `blockingBuildings`.

**`filter: true`** gives every stockpile row a `filter{}` — the same block
`home/zone_cells`'s `op: "filter"` reports as `before`/`after`, described there
— so what a stockpile actually accepts can be read without a write call.
`filters.filter` echoes the flag.

**A zone is stored twice** — `Zone.cells` and `ZoneManager.zoneGrid` — and a
direct `AddCell` writes only the grid pointer, so the zone's own list can be a
small fraction of what the grid gives it. Seeing that divergence is what this tool
is for, and why the two counts are never merged. Contiguity is a local flood fill,
never `Zone.CheckContiguous()`, which answers the question by **deleting** the
cells it cannot reach.

The growing block: `plantDef`, `plantLabel`, `plantDefExplicitlySet`, `allowSow`,
`allowCut`, `plantScanRan`, `plantScanFailed`, `plantsInListedCells`,
`plantsInGridCells`, `plantsInEitherCellSet`, `plantsOnlyOnGridCells`,
`cropPlantsInListedCells`, `cropPlantsInGridCells`, `cellsSownInListedCells`,
`cellsSownInGridCells`, `plantCountsDisagree`, `plants[]` (`defName`, `label`,
`isSetCrop`, `inListedCells`, `inGridCells`, `inEitherCellSet`),
`plantRowsNotListed`, `plantDefCount`. The two plant counts are never merged:
"7 listed cells, 43 crops" is the same divergence seen from the crop side, and one
number would answer whichever question the caller did not ask. Cells are swept
over the **union** of both sets. Requires `includeContents`; with it false every
count is null and `plantScanRan` is false.

**Two getters that write, both avoided.** `Zone.Cells` (capital C) shuffles the
cell list on first read, so the field `Zone.cells` is read instead — otherwise
every listing call quietly reorders the colony's stockpile cells. And
`Zone_Growing.PlantDefToGrow`'s getter **assigns** Potato (or Toxipotato on
polluted ground) when the field is null, so looking at the map would set the crop;
`plantDef: null` with `plantDefExplicitlySet: false` is the honest answer. The
plant sweep reads `map.thingGrid.ThingsListAtFast` and `ThingDef` fields only.

### `home/zone_cells`

The zone write tool; a dry run is a full simulation, not a guess. Parameters:
`op` (`add`/`remove`/`create`/`delete`/`repair`/`filter`), `zone` (exact label,
case-insensitive, or numeric id as a string; optional for `repair`, which then
repairs every zone), `x`/`z`/`width`/`height` and/or an explicit `cells` list
(`"113,145;113,144"`, combined with the rect if both are given), `zoneType`
(`stockpile`/`dumping`/`growing`), `label`, `priority`, `preset`, `allow`,
`disallow`, `allowSplit`, `dryRun`, `watch`, `watchSeconds`.

Response: `dryRun`, `op`, `zone` (label/id/type after the op), `cells[]` (`x`,
`z`, `accepted`, `reason`, `takenFrom`), `before` and `after`
(`listedCellCount`, `gridCellCount`, `phantomCellCount` — equal counts with zero
phantoms is the healthy state), `zonesRemoved[]`, `changes[]` (every mutation as
an English sentence, in the order it was or would be applied),
`skippedNotifies`, `notes`.

- **`op=add` does the step a direct `AddCell` skips**: `owner.RemoveCell(c)`
  **first**, then `target.AddCell(c)`, with the row naming what it was
  `takenFrom`. That order is the entire fix.
- **`op=remove` on a phantom cell is refused**: `Zone.RemoveCell` calls
  `ClearZoneGridCell`, which clears whoever the *grid* says owns the cell, so
  allowing it would blank a second zone while claiming to tidy the first.
  `op=delete` on a zone with phantoms is refused for the same reason (`Delete()`
  is a loop of `RemoveCell`), pointing at `op=repair`.
- **`op=repair` edits the list only, never the grid.**
- `SlotGroup.Notify_LostCell` reaches `HaulDestinationManager.ClearCellFor`, which
  `Log.Error`s — and so pauses the game — whenever the haul grid does not point at
  that slot group, which is exactly a phantom cell's state; every skipped notify
  is listed with its reason in `skippedNotifies`.
- `op=create` on a dry run allocates nothing (`zone.notYetCreated: true`), because
  a dry run that burned a zone ID every time somebody asked a question would not
  be one.
- `allowSplit` is off by default: adding a non-contiguous cell reports
  `contiguous: false`, `checkContiguousCalled: false`, and **still adds the
  cell**. Only `allowSplit: true` may call `CheckContiguous()`.

**`op: "filter"` sets a stockpile's storage filter.** It takes `zone` plus at
least one of `preset`, `allow`, `disallow`, `priority`, and applies them in that
fixed order — **preset first, then `allow`, then `disallow`, then `priority`** —
so a preset can be narrowed in the same call. `allow`/`disallow` are
comma-separated `ThingCategoryDef` or `ThingDef` names, matched on defName then
label, case-insensitive; a category cascades to its whole subtree. **An
unrecognised name refuses the whole call** rather than silently setting the
rest.

The six presets are computed over the stockpile's own parent filter
(`EverStorable(true)`), whose size is reported as `storableDefCount`:
`everything` (`SetAllowAll`), `nothing` (`SetDisallowAll`), `food`
(`IsNutritionGivingIngestible`, so kibble and hay are in it), `perishables`
(`HasComp<CompRottable>`), `nonperishables` (the complement) and `outdoorSafe`
(not rottable **and** either cannot deteriorate or has a deterioration rate of
zero). `presetDefinition{}` in the reply states the rule that was applied, so a
caller never has to trust the name.

The reply carries `dryRun`, `op`, `changed`, `zone{label,id,type}`, `before{}`,
`after{}`, `presetDefinition{}`, `changes[]`, `watch{}` and `notes`. A filter
summary — the same block `home/list_zones {filter: true}` emits — is
`allowedDefCount`, `storableDefCount`, `priority`, `categoriesFullyAllowed[]`,
`categoriesPartlyAllowed[]`, `allowsRottable`, `allowedRottableCount`,
`sampleAllowed[]` (ten labels) and `sampleTruncated`. **A dry run plans on a
copy of the filter** and never touches the live one; a real run replays the same
plan on the live filter and reads `after` back off the game.

**`op: "create"` takes the same four keys**, resolving every name *before* the
zone is registered so a bad one cannot leave an empty zone behind, and its reply
gains `filter{}`. A growing zone refuses them, and every other `op` refuses them
too.

**Watch.** Every `op` runs as two hops: `filter` selects the zone and opens
`ITab_Storage`, and `create` re-opens on the zone it just made
(`notes.watchOnCreate`). A `repair` with no `zone` has nothing to select and
skips. A refusal that is only detectable in the second hop still closes the menu
it opened.

The category cascade is `ThingFilter.SetAllow(ThingCategoryDef)`, which
**`Log.Error`s when `ThingCategoryNodeDatabase` is not initialised** — so the
database is checked before the call, never after.

### `home/place_building`

Placement check and placement; `rotation` defaults to `all`, so all four are
evaluated in one call. Parameters: `def` (defName preferred, or label; ThingDefs
searched before TerrainDefs), `x`, `z`, `cells` (batch: `"141,130;142,130"`),
`rotation`, `stuff`, `godMode` (evaluates as though god mode were on, which skips
the map-edge check; does not enable it),
`dryRun`, `watch`, `watchSeconds`.

Response: `dryRun`, `def`, `stuff`, `size`, `rotatable`, `researchFinished`,
`buildableByPlayer`, `costList[]`, `materials{}`, `rotationsEvaluated`, `acceptedRotations`,
`rotations[]`, `placed`, `wiped`, `framesCancelled`, `notes`. A rotation row:
`rotation`, `accepted`, `reason` (RimWorld's own translated
`AcceptanceReport.Reason` — an empty reason on a refusal means the report is being
read wrong), `occupiedRect`, `occupiedCells[]`, `blockingThings[]` (each flagged
`wouldBeWiped`, `mustBeHauledFirst` or `frameWouldBeCancelled`), `wipeOnPlace`,
`framesCancelledOnPlace`, `identicalBlueprintExists`, and `sides` for coolers and
vents.

- **`wouldBeWiped` is `GenSpawn.SpawningWipes(entDef.blueprintDef, thing.def)` —
  the blueprint's def, which is what actually spawns** — because computing it from
  `entDef` reports every loose item in the footprint as doomed, an Impassable
  building with `surfaceType: None` wiping items where a blueprint does not. An
  item the finished building would displace but the blueprint will not is
  `mustBeHauledFirst`: a builder carries it out. A stone chunk is that case.
- **A real placement replicates `Designator_Build.DesignateSingleCell` in order:**
  cancel any `Frame` in the anchor cell whose `replaceTags` overlap the
  blueprint's with `DestroyMode.Cancel` (which refunds it), then
  `GenSpawn.WipeExistingThings(…, DestroyMode.Deconstruct)`, then place. Skipping
  the wipe spares nothing — `PlaceBlueprintForBuild`'s `GenSpawn.Spawn` defaults
  to `WipeMode.Vanish` and wipes anyway, **with no refund**. Everything removed is
  in `wiped`/`framesCancelled`, and the dry run predicts both exactly.
- `Find.DesignatorManager` is never touched; placement is
  `GenConstruct.PlaceBlueprintForBuild` directly, so the player's selected
  designator, stuff and rotation are left alone. The tool exists because
  `Designator_Build.placingRot` is protected, so a dry run through the architect
  menu answers for one fixed rotation.
- **Coolers**: `Building_Cooler.TickRare` cools
  `Position + IntVec3.South.RotatedBy(Rotation)` and pushes heat north, and
  `Rot4.North` is the identity rotation, so an unrotated cooler chills the cell to
  its south. `sides` names each face's cell, its room, whether that room uses
  outdoor temperature, and its temperature. **Vents report `a`/`b`, not
  `hot`/`cold`**, because `Building_Vent` equalises both sides.
- Placing the same thing twice is not an error: a real run returns
  `success: true`, `alreadyPlaced: true`, `placed: null`.
- **Watch**: the first hop moves the camera to the empty cell so the ground is
  on screen before anything is put on it, and the second selects the blueprint
  it just placed.
- `Rot4.FromString` and `CostListAdjusted`'s default `errorOnNullStuff` both call
  `Log.Error`, so neither is used on its logging path. Stuff defaults to
  `GenStuff.DefaultStuffFor`; one the def cannot use is reported as
  `stuff.allowedForThisDef: false`, never silently swapped.
- **`cells` — a wall line is ONE call.** `cells="141,130;142,130;…"` (x/z, when
  given, is the first cell; duplicates collapse; order asked is order placed; cap
  **200**, needs ONE rotation, never `"all"`). Sending `cells` always returns a
  `batch{}` block: `requested`, `rotation`, `cellsPerHop`, `hops`, `hopGapMs`,
  `placed`, `alreadyPresent`, `refused`, `errors`, `accepted`, `placedIds[]`,
  `firstRefusal` and `rows[]` — one row per cell, each the same rotation row a
  single-cell call returns plus `x`, `z`, `outcome`
  (placed/already_present/refused/error/preview), `placed{}`, `wiped[]`,
  `framesCancelled[]`, `error`. A refused cell never stops the batch, so
  top-level `outcome` gains **`partial`**; top-level `placed` is the FIRST
  blueprint made, `wiped`/`framesCancelled` are the whole batch's, `rotations[]`
  is the first cell's row, and `alreadyPlaced` is true only when every cell was
  already there. Before: 64 cells = 64 calls × ~1.8 s, 1.5 s of it the watch
  lead. After: ~2 s for the batch.
- **The batch never holds the main thread.** Placements are chunked **8 cells per
  `MainThread.InvokeAsync` hop** with **20 ms** released between hops, because a
  long synchronous loop inside one hop stalls `Verse.Root.Update` — the game's
  tick and the queue every other bridge call is pumped from — which is the
  "a 64-cell build delays every event" complaint moved rather than fixed. Eight
  `CanPlaceBlueprintAt` + `PlaceBlueprintForBuild` pairs is less work than one
  frame of a player dragging a wall. Every cell goes through the same `PlaceOne`
  a single placement uses, so the two cannot drift apart.
- **Watch on a batch**: ONE session — the camera jumps to the midpoint of the
  batch's bounding rect before anything is placed, the lead is waited once, and
  the FIRST blueprint made is selected when the batch finishes.
- **`materials` on a batch is costed for every requested cell**: `needed` is
  per-cell × `materials.forCells`, with `rows[].perCellNeeded` beside it. Still
  one pass over the map per call.

**`materials{}` — can the colony actually build it.** "The game will accept this
blueprint here" and "there is steel to finish it" are different questions, and
until 2026-09-04 only the first was answered. `materials.rows[]` has one row per
entry of `def.CostListAdjusted(stuff, errorOnNullStuff: false)`:

| field | meaning |
|---|---|
| `defName`, `label` | the resource |
| `needed` | what this building costs |
| `onMap` | spawned stacks on THIS map whose `Faction` is the player **or null**. A trader's crate and a raider's dropped gun are on the map and are not yours. |
| `forbidden` | of those, the ones marked forbidden — counted in `onMap` too, then subtracted |
| `reservedByOtherBlueprints` | the outstanding deficit of every **other** blueprint and frame on the map |
| `available` | `onMap − forbidden − reservedByOtherBlueprints`, floored at 0 |
| `shortfall` | `max(0, needed − available)` |
| `perCellNeeded` | what ONE of them costs; `needed` is this × `materials.forCells` (1 unless the call sent `cells`) |

Beside them: `canBuildNow` (every `shortfall` is 0) and `missing`, one line —
`missing: 25 steel (have 15, 10 forbidden), 3 components (have 0)` — and the
**empty string**, not null, when nothing is missing. `unreadable: true` with
`canBuildNow: null` means the cost list could not be read: **not known, never
"yes"**.

It is computed **once per call, before any placement**, not once per rotation —
the cost does not depend on which way the thing faces, and computing it before
the blueprint exists is what makes `reservedByOtherBlueprints` mean *other*
blueprints rather than counting this one against itself.

`reservedByOtherBlueprints` is not a second opinion: it is
`BridgeCommon.OutstandingConstructionDeficit`, the same
`IConstructible.ThingCountNeeded` sum `home/list_buildings` reports as
`resourceDeficit` and per-site as `resources[].stillNeeded`. Both tools now go
through `BridgeCommon.ConstructibleStillNeeded`, so they cannot drift apart.

**`canBuildNow: false` does NOT refuse a placement, and is not meant to.** A
blueprint standing while the haulers bring steel is ordinary play. The point is
that the shortfall is on screen when the blueprint is made instead of being
discovered later. `rotations[].accepted` is the only thing that refuses.

**Three `Log.Error` paths on this page were found by decompiling and closed**,
each of which pauses the colony because `Verse.Log.Error` calls
`TickManager.Pause()`:

* `CostListCalculator.CostListAdjusted` has a **second** `Log.Error` that
  `errorOnNullStuff` does not gate — "got AdjustedCostList for X with stuff Y but
  is not MadeFromStuff". This tool accepts `stuff` on a def that is not made from
  stuff and merely notes it, so that branch was reachable. The stuff is now
  dropped before the call (`CostStuff`).
* `Blueprint_Install.TotalMaterialCost()` is, in full, `Log.Error("Called
  MaterialsNeededTotal on a Blueprint_Install."); return new List<>();` — so
  asking a **reinstall** blueprint what it needs pauses the game. One minified bed
  waiting to be reinstalled would have paused the colony on every
  `home/list_buildings` call. Both tools now skip it, the way vanilla's
  `GenConstruct.CanGetResources_NewTemp` does (`if (thing is Blueprint_Install)
  return true;`). A reinstall costs nothing, so an empty `resources[]` is the true
  answer — `home/list_buildings` rows carry `isInstallBlueprint` and no longer
  claim `materialCostUnreadable` for that case.
* `ListerThings.ThingsOfDef` opens with a `Log.ErrorOnce` for
  `ThingDefOf.MinifiedThing`. Nothing vanilla costs one, so the guard is
  defensive; when it fires the row is left at zero on hand, which reports a
  shortfall rather than a confident "you have it", and `materials.note` says so.

`CostListAdjusted` also returns a **shared cached list** and memoises into a
static dictionary, so it is read and never mutated or sorted, and
`ListerThings.ThingsOfDef` returns the **live internal list**, iterated
immediately and never held. `CompForbiddable.Forbidden` is read off
`ThingWithComps` directly: the obvious call,
`ForbidUtility.IsForbidden(Thing, Faction)`, opens by comparing against
`Faction.OfPlayer`, which is the pause again.

### `home/pawn_config`

The write side of `home/list_pawns`. Parameters: `pawn` (the name `list_pawns`
prints — case-insensitive, a unique substring is enough — or the exact ThingID
from `settings.thingId`), `work` (`"Cooking=1,Hauling=3"`), `schedule` (24
letters, hour 0 first), `medCare`, `hostilityResponse`, `selfTend`,
`followDrafted`, `followFieldwork`, `allowedArea`, `master`, `training`,
`slaughter`, `releaseToWild`, `hunt`, `tame`, `drop`, `nickname`, `dryRun`, `watch`,
`watchSeconds`.

Response: `dryRun`, `applied`, `afterIsPredicted`,
`pawn{name,thingId,isColonist,isAnimal}`, `fields[]` (`field`, `requested`,
`before`, `after`, `changed`, `refused`, `reason`, optional `note`; a `drop` row
adds `item{}`, `carried[]` and `droppedAt`; a `slaughter`/`releaseToWild` row
adds `alsoRemoved[]` naming the one opposite designation cleared, and a
`hunt`/`tame` row the same key naming **every** designation cleared),
`fieldCount`,
`changed[]`, `changeCount`, `refused[]`, `refusedCount`,
`before{work,schedule,settings,animals}`, `after{…}`, `options{allowedAreas,
allowedAreaUnrestricted, medCare, hostilityResponse, scheduleKey}`, `notes{}`.

**On a real run `after` is READ BACK from the game**, never the value that was
asked for, so a write the game silently declined shows up as a mismatch rather
than a false success; on a dry run it equals `before` while each field row carries
its predicted value and `afterIsPredicted` is true. The whole
read-write-read-back runs inside one main-thread hop, because a tick in between
would make `after` answer a different question. **A refusal is never a silent
skip**: a rejected field appears in `fields[]` with `refused: true` and a reason,
and in `refused[]`, never in `changed[]`; a field absent from `fields[]` was never
asked for. Pawn resolution refuses ambiguity rather than guessing — exact ThingID,
then exact name, then a unique substring, naming every candidate and its
ThingID on a tie, in the exact-name branch as well as the substring one,
because five muffalo are all called "Muffalo".

The blocks, shared with `home/list_pawns` and never null:

- **`work{}`**: `applies`, `hasWorkSettings`, `everWork`, `initialized`,
  `manualPriorities`, `priorityMin`, `priorityMax`, `types[]` (`name`, `label`,
  `naturalPriority`, `visibleInTab`, `disabled`, `priority`, `priorityStored`,
  `active`), `typeCount`, `activeWorkTypes[]`, `disabledWorkTypes[]`,
  `priorityStoredReadable`, `note`. 0 = never, 1 = most urgent, 4 = least.
- **`schedule{}`**: `applies`, `hasTimetable`, `key{letter→defName}`, `hourCount`,
  `hours` (a 24-character string), `assignments[]`, `current`, `currentLetter`,
  `currentHour`, `note`.
- **`settings{}`**: `thingId`, `applies`, `hasPlayerSettings`, `medCare`,
  `hostilityResponse`, `selfTend`, `followDrafted`, `followFieldwork`,
  `allowedArea`, `allowedAreaIsUnrestricted`, `allowedAreaCellCount`,
  `supportsAllowedAreas`, `master`, `masterThingId`, `note`. `allowedArea: null`
  means **unrestricted** — a real setting, said out loud by the boolean beside it,
  not a failed read. `selfTend` is off by default for every colonist, which in a
  colony of one is fatal.
- **`relations{}`** is read-only, so it is not in `before`/`after`; on
  `list_pawns` it carries `direct[]` (`defName`, `label`, `otherName`,
  `otherThingId`, `otherIsColonist`, `otherIsAnimal`, `otherDead`,
  `otherOnThisMap`, `startTicks`), `partners[]`, `bondedAnimals[]`,
  `colonistOpinions[]` (`name`, `thingId`, `relations[]`, `opinion`,
  `opinionParts{relations,memories,situational}`, `situationalSocialCached`,
  `drivers[]`), `opinionMethod`, `opinionScale`.

- **`animals{}`** is the block described under `home/list_pawns`; it joins
  `work`, `schedule` and `settings` in `before`/`after`.

**Watch** picks the view a player would use for the field being written: `work`
opens the Work main tab, `schedule` the Schedule tab, `allowedArea` the Assign
tab; `medCare`, `hostilityResponse` and `selfTend` select the pawn and open
`ITab_Pawn_Health`; `training`, `slaughter`, `releaseToWild`, `master`,
`followDrafted` and `followFieldwork` select the pawn and open
`ITab_Pawn_Training`; `drop` selects the pawn and opens `ITab_Pawn_Gear`;
`nickname` selects the pawn and opens `ITab_Pawn_Character` (`ITab_Pawn_Training`
on an animal); `hunt` and `tame` select the animal and move the camera to it
with **no** inspect tab, because a wild animal draws neither an Animals-tab row
nor an `ITab_Pawn_Training`. When
one call writes several fields the view covering the most of them wins, and
`watch.note` says so.

**Five animal fields**, same dryRun / before / after / refusal contract as the
rest:

- **`training`** — `"Obedience=on,Release=off"`, the `work` grammar. It calls
  `Pawn_TrainingTracker.SetWantedRecursive`, exactly what the Animals tab's
  checkbox calls, so it **cascades**: on turns every prerequisite on, off turns
  every dependent off. Each field row carries `cascades[]` naming every def that
  will move — on a dry run as well — and on a real run any def the cascade moved
  that the caller did not name gets its own line in `changed[]`. A trainable
  `CanAssignToTrain` rejects is refused with RimWorld's reason; a pawn with no
  training tracker is refused once for the whole field.
- **`slaughter`** / **`releaseToWild`** — `"on"`/`"off"`. What
  `Designator_Slaughter` / `Designator_ReleaseAnimalToWild` do minus the sound,
  the mouse icon and the bonded-animal popup, **including clearing the opposite
  designation**; `alsoRemoved[]` names what was cleared. Refused for a
  non-animal, an animal not in the player faction, a dead one, one
  `InAggroMentalState`, a non-flesh race (slaughter) or a race whose
  `canReleaseToWild` is false. Setting one already set is a no-op row, never a
  second `AddDesignation`, which is a `Log.Error`. `AddDesignation` throws a
  visible puff of particles at the target, which is left in.
- **`hunt`** / **`tame`** — `"on"`/`"off"`, the **wild** pair. What
  `Designator_Hunt` / `Designator_Tame` do minus the sound, the mouse icon and
  the warning toasts (`FinalizeDesignationSucceeded` throws "no hunters
  available", "this kind goes manhunter", "no handler skilled enough";
  `TameUtility.ShowDesignationWarnings` reads `Faction.OfPlayer`, so it is
  skipped rather than caught). The designation hangs on the **animal**, not on
  a cell, so a caller that read the animal a tick ago still hits it after it
  has walked. **Both designators call
  `DesignationManager.RemoveAllDesignationsOn(t)` before adding**, so turning
  either ON clears **every** designation standing on that animal — not one
  named opposite the way slaughter/releaseToWild do — and `alsoRemoved[]`
  names each defName it took, on a dry run as well. `hunt` and `tame` both
  `"on"` in one call is **refused** for both, because the second would erase
  the first. Gates, run one at a time so a refusal says which closed: `hunt`
  needs `Pawn.AnimalOrWildMan()`, `!IsPrisonerInPrisonCell()` and
  `Faction == null || !Faction.def.humanlikeFaction`; `tame` needs the same
  faction test plus `GetStatValue(StatDefOf.Wildness) < 1f` and whatever else
  `TameUtility.CanTame` wants (not a dryad, no Scaria). A dead animal is
  refused. An animal of **ours** is refused with the sentence "the write you
  want is slaughter" — our animals are slaughtered, never hunted, so the two
  pairs cannot collide on one pawn. Setting one already set is a no-op row,
  never a second `AddDesignation`, which is a `Log.Error`.

**`drop`** — one item off the pawn, named by a case-insensitive substring of
its label or by its exact ThingID. The row's `before` is the slot it was in
(`apparel` / `equipment` / `inventory`), `after` is where it is now (`map` once
it is on the ground, otherwise the slot still holding it, or `gone`), and
`droppedAt` is the cell, read back from the dropped thing. `item{}` is what the
spec resolved to; `carried[]` is every item the pawn holds — `slot`, `label`,
`defName`, `thingId`, `stackCount`, `isApparel`, `isWeapon` — and is on the row
whether the drop landed or was refused, so a caller that named the item wrongly
gets the list it needed. Its length is
`apparelCount + equippedCount + inventoryItemCount` as `home/list_pawns`
`equipment{}` counts them; both walk the same three trackers.

The write is **direct**: `Pawn_ApparelTracker.TryDrop(ap, out Apparel, cell,
forbid: false)`, `Pawn_EquipmentTracker.TryDropEquipment(eq, out ThingWithComps,
cell, forbid: false)` and
`ThingOwner.TryDrop(thing, cell, map, ThingPlaceMode.Near, out Thing)`. Not a
job: `ITab_Pawn_Gear.InterfaceDrop` queues `JobDefOf.RemoveApparel` for apparel,
so the game's own drop button does nothing on a paused game. These land
immediately, paused or not, on `pawn.PositionHeld`, and nothing is forbidden.
Refused for a pawn that is not the player's, a dead one, one with no `MapHeld`,
a substring matching zero or several carried items (every match named — this
tool writes and will not pick one), `ThingDef.destroyOnDrop`,
`Pawn_ApparelTracker.IsLocked`, and anything
`EquipmentUtility.QuestLodgerCanUnequip` says no to, which is the test the Gear
tab greys its own button out with.

**`nickname`** — the rename a player reaches through the pawn's own rename
button. A `NameTriple` (every colonist) keeps its `First` and `Last` and gets a
new nick, `new NameTriple(first, nickname, last)`; a `NameSingle` (animals,
mechs) and a null `Name` (an unnamed animal) become `new NameSingle(nickname)`.
`Pawn.Name` is a plain field setter, so the write lands immediately, paused or
not. The row's `before` and `after` are both `{name, nick}` — `Name.ToStringFull`
and `Name.ToStringShort` — and on a real run `after` is read back off the pawn.
Refused for an empty or whitespace-only nickname, one over **32** characters, a
pawn whose faction is not the player's, and a `Name` that is neither
`NameTriple` nor `NameSingle`. Watch selects the pawn and opens
`ITab_Pawn_Character` on a humanlike, `ITab_Pawn_Training` on an animal whose
def has one, and nothing but the selection otherwise.

**Five write hazards, each pre-checked and refused rather than triggered.**
(1) `Pawn_WorkSettings.GetPriority`/`SetPriority` open with
`ConfirmInitializedDebug()`, which on a null priority table both `Log.Error`s and
writes a fresh 6-job table, so the pure null checks `EverWork`/`Initialized` are
tested first. (2) `SetPriority` also `Log.Error`s when the priority is non-zero
and `WorkTypeIsDisabled` is true, so a disabled work type accepts only 0.
(3) Priority range 0..4 is validated locally; RimWorld would otherwise
`Log.Message` and store the value anyway. (4) In simple (checkbox) mode —
`Find.PlaySettings.useWorkPriorities` false — `GetPriority` **lies**, returning 3
for any active job whatever is stored, so `priority` is what the game acts on,
`priorityStored` is the raw DefMap cell read by reflection, and a write above 0
and not 3 is coerced to 3 to match a checkbox click. (5) Setting `master` calls a
setter that itself calls `training.HasLearned(Obedience)`, which throws on any
human and `Log.ErrorOnce`s on an untrained animal.

The schedule is **all or nothing**: all 24 characters are validated before
anything is written, so an unknown letter refuses the whole schedule rather than
writing half a day. `allowedArea` accepts `"none"`/`"unrestricted"` to clear the
restriction and otherwise matches an assignable area's label exactly; it is
refused if unmatched, if `!SupportsAllowedAreas`, or if `pawn.MapHeld` is null,
because the setter keys off `MapHeld` and the write would otherwise go nowhere
silently. `EffectiveAreaRestrictionInPawnCurrentMap` is deliberately not read: its
`RespectsAllowedArea` branch calls `Faction.OfPlayer`.

Verified beyond the above: `Pawn_TimetableTracker.times`/`.GetAssignment`/
`.SetAssignment`; `Pawn_PlayerSettings` public fields `medCare`,
`hostilityResponse`, `selfTend`, `followDrafted`, `followFieldwork`, plus
`Master`, `AreaRestrictionInPawnCurrentMap`, `SupportsAllowedAreas`;
`AreaManager.AllAreas`, `Area.AssignableAsAllowed()`; `Pawn.WorkTypeIsDisabled`;
`Pawn_TrainingTracker.HasLearned(TrainableDefOf.Obedience)`;
`WorkTypeDefsUtility.WorkTypeDefsInPriorityOrder`.

### `home/dialog_text`

Types into the dialog that is on screen. RimWorld's text boxes are plain string
fields redrawn every frame by `Widgets.TextField`, so setting the field IS
typing; no OS keyboard event reaches this game.

Parameters: `text` (required unless `list`; an empty string clears the box),
`field` (which box, by field name or by a substring of its label), `list` (bool,
default false — report the boxes and write nothing), `accept` (bool, default
false), `dryRun` (bool, default **true**).

Response: `dryRun`, `applied`, `window` (the window's type `FullName`),
`fields[]` (`name`, `before`), `fieldCount`, `set{field, before, after}` (null
when nothing was written), `accepted`, `error` (null when nothing was refused),
`note`.

The target is the **top-most** window — the last entry of
`Find.WindowStack.Windows`. An empty stack is refused, and so is a top window
that is a `MainTabWindow` or an `ImmediateWindow`: those are the main tabs and
the message/letter overlays, which hold no typed input.

The boxes are found by reflection, never by a per-dialog table. Two shapes:

- every writable instance `string` field declared on the window's own class or
  any base **up to but excluding `Verse.Window`** — `Dialog_Rename.curName`,
  `Dialog_GiveName.curName` and `.curSecondName`. `Window`'s own strings are
  chrome and are never offered.
- `Verse.Dialog_NamePawn`, whose boxes are a `List<NameContext>` of a private
  nested class, one element per name part, each holding its text in `current`
  and its untranslated box label in `textboxName` (`FirstName`, `NickName`,
  `LastName`, `BackstoryTitle`). Those rows are named
  `<listField>[<index>].<sub>` — `names[1].current` — and an element whose
  `editable` field is false is a label the dialog draws, not a box, and is left
  out.

Which box is written: `field` matched case-insensitively against the row name,
then against the row's label, then as a substring of the row name; otherwise the
only row when there is exactly one; otherwise the first of `curName`, `name`,
`text`, `newName` that exists. Failing all of that the call is refused with
every candidate named — `Dialog_NamePlayerFactionAndSettlement` carries two
name fields and needs `field` to pick one.

`accept: true` on a real run calls `WindowStack.Notify_PressedAccept()` after
the write, which is what the Return key does: it walks the stack from the top
and calls `OnAcceptKeyPressed` on the first window that takes it. A dialog with
`closeOnAccept` false — a letter — ignores it, and `accepted` reports what was
attempted, not what the dialog decided. Every reflection read and write is
wrapped: an exception comes back as a refusal naming the type, never out of the
tool.

No watch block: the dialog is already on screen.

### `home/building_config`

The write side of `home/list_buildings`: the four toggles a player reaches
through a building's gizmo bar, plus the gizmo bar itself as a read.

Parameters: `thing` (a ThingID — the `thingId` on any `list_buildings` row — or
`DefName@x,z`, or a unique label or defName substring **among colony buildings,
blueprints and frames**), `gizmos` (bool), `forbidden` (bool), `power`
(`"on"`/`"off"`), `medical` (bool), `owner` (a colonist name or ThingID, or
`"none"`), `forPrisoners` (bool), `dryRun` (**true**), `watch`, `watchSeconds`.
**A field that is omitted is left alone**; an ambiguous selector is refused with
`candidates[]`.

Response: `thing{thingId, defName, label, position, faction, isBed}`,
`gizmos[]` (`label`, `type`, `disabled`, `disabledReason`, `isActive`; null
unless `gizmos: true`) with `gizmoCount`, `fields[]`, `changed[]`,
`changeCount`, `refused[]`, `refusedCount`, `fieldCount`, `before{}`, `after{}`,
`options{}`, `dryRun`, `applied`, `afterIsPredicted`, `watch{}`, `notes{}`.

A `fields[]` row is the shape `home/pawn_config` uses — `field`, `requested`,
`before`, `after`, `changed`, `refused`, `reason` — plus, where they apply,
`note`, `ownersDropped[]`, `unassignedFrom{}`, `evicts{}` and
`bedOwnerTypeBefore`. `before{}`/`after{}` carry the readable state whether or
not it was written: `forbiddable`, `forbidden`; `flickable`, `wantSwitchOn`,
`switchIsOn`, `flickDesignated`; `hasPower`, `powered`, `connected`; `isBed`,
`medical`, `canBeMedical`, `forPrisoners`, `bedOwnerType`, `humanlikeBed`;
`assignable`, `assignedPawns[]` (`name`, `thingId`), `maxAssignedPawns`.
`options{}` is `assigningCandidates[]`, `assigningCandidateCount`,
`maxAssignedPawns`, `roomCanBePrisonCell` and `roomNote`.

**What each field actually does**, because three of them are not what they look
like:

- **`forbidden`** is the `CompForbiddable.Forbidden` setter, which is what the
  gizmo's own action calls.
- **`power` does not move the switch.** It sets `wantSwitchOn` and places the
  Flick designation, exactly as clicking the gizmo does; a colonist then walks
  over and flicks it. `after` reports that honestly — `wantSwitchOn` changes,
  `switchIsOn` does not.
- **`medical`** is `Building_Bed.Medical`, and **either direction drops every
  owner the bed has**; `ownersDropped[]` names them, on a dry run too.
- **`forPrisoners`** goes through `ForOwnerType`, and is refused with the game's
  own string when `RoomCanBePrisonCell` is false. One bed per call.
- **`owner`** is `TryAssignPawn`/`TryUnassignPawn`, with `"none"` unassigning.
  It refuses a pawn that is not an assigning candidate, one `CanAssignTo`
  rejects, one an ideoligion forbids, and a medical or prisoner bed; the dry run
  predicts `unassignedFrom` and `evicts`.

**Watch**: a real write selects the thing and moves the camera to it; a
`gizmos`-only call writes nothing and never watches.

**`Thing.GetGizmos()` calls `Faction.OfPlayer` unconditionally**, in six places,
so `gizmos: true` refuses the **whole call** when `Faction.OfPlayerSilentFail`
is null rather than pausing the colony to draw a gizmo bar. Three more, each
pre-checked rather than caught: `Building_Bed.ForPrisoners = false` is a
`Log.Error`, so `ForOwnerType` is written instead;
`FlickUtility.UpdateFlickDesignation` can open a tutor `Dialog_MessageBox`, so
the designation is mirrored by hand; and `Pawn_Ownership.ClaimBedIfNonMedical`
ends in `Log.Error` when an ideoligion forbids the bed.
`SetBedOwnerTypeByInterface` reads `Find.Selector` and is not used.
`CompFlickable.wantSwitchOn` is private and read by reflection, with a refusal
if the field is ever renamed.

### `home/bills`

Worktable bills in one call: every bench's queue, each bill's configuration,
and — the thing no pane in the game shows — **whether each bill can actually
run**, with the ingredient arithmetic behind the verdict.

Parameters. `action` is `list` (default), `recipes`, `add`, `set`, `delete` or
`move`. `bench` is a ThingID, `defName@x,z`, or a unique label substring;
omitted, `list` answers for every bench on the map. Then `recipe`, `index`
(-1), `to` (-1), `direction` (`up`/`down`), `repeatMode`
(`Forever`/`RepeatCount`/`TargetCount`), `repeatCount` (-1), `targetCount` (-1),
`unpauseWhenYouHave` (-1), `pauseWhenSatisfied` (`on`/`off`), `suspended`
(`on`/`off`), `ingredientSearchRadius` (-1), `skillMin` (-1), `skillMax` (-1),
`worker` (a colonist name, or `anyone`), `storeMode`
(`bestStockpile`/`dropOnFloor`/a zone label), `allow` and `disallow`
(comma-separated `ThingDef` or `ThingCategoryDef` names), `allFactions`
(false), `dryRun` (**true**), `watch`, `watchSeconds`. **`-1` means leave that
setting alone**, and the toggles are `on`/`off` **strings** because the binder
fills an unsupplied bool with false, which would silently clear a setting nobody
mentioned.

Response: `action`, `mapName`, `benches[]`, `benchCount`, `benchesOnMap`,
`benchesSkippedByFaction`, `attention{}`, `recipes[]` (null unless
`action: "recipes"`), `write{}` (null unless the action writes),
`ingredientsScanned`, `filters{}`, `notes{}`, `dryRun`, `applied`, `watch{}`. A
refusal is `success: false` with `error` and `candidates[]`, still carrying
every always-present key.

A **bench** row: `thingId`, `defName`, `label`, `position`, `faction`,
`usableForBills`, `unusableReason`, `needsPower`, `powered`, `maxBills`,
`billCount`, `activeBillCount`, `billsCanRunNow`, `billStackEmpty`,
`billStackFull`, `billStackUnreadable`, `bills[]`. **Pawns and corpses are never
listed as bill givers**, though the game counts them as such.

A **bill** row: `index`, `label`, `recipe`, `recipeLabel`, `billClass`,
`repeatInfo`, `suspended`, `paused`, `finished`, `active`, `completableEver`,
`canRunNow`, `blockedBy[]`, `ingredients[]`, `ingredientsSatisfied`, `filter{}`,
`config{}`. **`canRunNow` is exactly `blockedBy[]` being empty**, and each
`blockedBy` entry is one English reason: suspended · finished · paused · target
already met (N/M) · the bench unpowered, broken down, out of fuel or not usable
for bills · recipe not available · missing `<label>` (have/need) · this bill's
filter excludes N on the map.

An **`ingredients[]`** row: `summary`, `label`, `needed`, `available` (the best
**single** def unless the recipe allows mixing), `shortfall`, `satisfied`,
`availableTotal`, `availableDefs[]` (top five), `availableDefsNotListed`,
`filterAllowsAny`, `excludedByFilter`, `excludedForbidden`,
`excludedOutOfRadius`, `isFixedIngredient`, `mixingAllowed`, `wholeStacks`. The
radius is measured from the bill giver's own position, and 999 means unlimited.
The scan subtracts stacks with no route from the bench's interaction cell
(`excludedUnreachable`, no door bashing), tallies
reserved stacks without subtracting them (`excludedReserved` — the claimant is
usually fetching it for this bill), and reports `reachabilityChecked` /
`reservationChecked` per ingredient so a row past the 4000-check budget says so
rather than reading `0 unreachable`. Each ingredient also carries `searchRadius`
/ `radiusUnlimited`, and the payload carries `countedAtTick`, because two reads
are two snapshots. It still does not model a pawn's own forbid rules, and
`notes.ingredientScan` says so. `write{}` carries `requestedOptions` and
`optionsNotApplied` (each requested field compared against the bill read back),
and `watch{}` on a real `add` carries `scrolledToNewBill` / `scrollNote`.

**The route test is a pawn's, not a fence's.** Until 2026-09-12 the scan asked
`TraverseMode.PassDoors`, and `Verse.Region.Allows` answers `return !flag` in
that mode — the door ignored entirely — so a locked door could never strand an
ingredient, however loudly the note said it did. `NoPassClosedDoors` is the
opposite mistake: its arm is `door == null || door.FreePassage`, which refuses
every ordinary closed door in the colony. The scan now asks
`TraverseMode.ByPawn` for the bill's own `PawnRestriction` worker when that pawn
is usable, else for the first spawned free colonist, and only on a map with no
colonist at all does it fall back to the old `PassDoors` call. Under `ByPawn`
the door arm runs `Building_Door.CanPhysicallyPass` (free passage, or
`PawnCanOpen`, or standing open) and then `IsForbiddenToPass`, both against that
pawn, so **a locked or forbidden door now counts as unreachable for that pawn**.
The representative colonist is walked by hand off `AllPawnsSpawned` filtered on
`Pawn.IsFreeColonist`, never `MapPawns.FreeColonists`, which reaches
`Faction.OfPlayer`. `UsableTraverser` screens the candidate first — null,
unspawned, dead, or on another map — because `Reachability.CanReach` `Log.Error`s
on a pawn spawned on another map and answers a flat false for one that is not
spawned at all. The reach cache is keyed by **traverser** as well as by root and
target cell: two bills on one bench can name two different workers, and a door
one of them may open is a door the other may not.

**The `blockedBy` lines that were never printed.** `BridgeCommon.Num` unboxed
only `double`. `excludedUnreachable` and `excludedByFilter` are boxed `int`s and
`BillCommon.IngredientRows` reads them straight back off the row it has just
built, so both read as 0 and the two lines "N … have NO ROUTE from this bench's
interaction cell" and "filter excludes N on map" could not be emitted at all —
dead from the day they were written until 2026-09-12. `Num` now accepts any
boxed numeric (`sbyte` through `decimal`, `int`, `long`, `float`, `double`) and
never throws; a missing key, a null and a conversion that throws still read as 0,
and `bool`, `char` and `string` still read as 0 **on purpose**, because answering
1.0 for `true` would hide a mis-keyed lookup behind a number that looks read.

`filter{}` is `allowedDefCount`, `allowsHumanMeat`, `allowsInsectMeat` (null
when the recipe's fixed filter could never admit it at all),
`categoriesFullyAllowed[]`, `categoriesPartlyAllowed[]`, `categoriesNotListed`.
`config{}` is the bill dialog in fields: `repeatMode`, `repeatCount`,
`targetCount`, `productCount`, `pauseWhenSatisfied`, `unpauseWhenYouHave`,
`ingredientSearchRadius`, `ingredientSearchRadiusUnlimited`, `skillRange`,
`pawnRestriction`, `slavesOnly`, `mechsOnly`, `nonMechsOnly`, `storeMode`,
`storeZone`, `hpRange`, `qualityRange`, `limitToAllowedStuff`,
`includeEquipped`, `includeTainted`.

`attention{}` is emitted in full including zeros: `benchesWithNoBills`,
`benchesWithNoActiveBill`, `benchesUnusable`, `billsShortOfIngredients`,
`finishedBills`, `suspendedBills`, `pausedBills`.

**`action: "recipes"`** lists what could be added to a bench and whether it
could run today: `defName`, `label`, `availableNow`, `availableOnNow`,
`workAmount`, `workSkill`, `minSkill[]`, `products[]`, `ingredients[]` (against
the recipe's default filter), `ingredientsOnHand`, `blockedBy[]`, `filter{}`.

**`write{}`** covers all four writing actions: `action`, `requestedBench`,
`requestedRecipe`, `resolvedRecipe`, `index`, `to`, `refused`, `reason`,
`before{billCount, bills[], bill}`, `after{…}`, `afterIsPredicted`, `changed[]`,
`canRunNow`, `blockedBy[]`. The new or edited bill's **can-run verdict is
computed at the moment of the write**, so adding a bill answers on the spot
whether it will do anything.

**Watch**: a real `add`, `set`, `delete` or `move` selects the bench, opens
`ITab_Bills` and moves the camera; a bill giver that is not a worktable has no
such tab and gets none.

**Bill APIs that write, or log, and how each is dodged.**
`Bill_Production.ShouldDoNow()` writes `paused` and is never called — `finished`
and the verdict are computed from fields. `RecipeDef.AvailableNow` reaches
`Faction.OfPlayer` on three paths, so it is called only when
`Faction.OfPlayerSilentFail` is non-null; `Bill.ValidateSettings` reaches it too
and is never called. `ForbidUtility.IsForbidden(Thing, Faction)` reaches it as
well, so `CompForbiddable.Forbidden` is read directly.
`ThingFilter.SetAllow(ThingCategoryDef)` `Log.Error`s before the category node
database is initialised, so a category is expanded to its defs instead.
`Bill.SetStoreMode` `Log.ErrorOnce`s on a non-production bill and
`IngredientCount.FixedIngredient` `Log.Error`s when the ingredient is not fixed;
both are pre-checked. And **`BillStack.MakeNewBill` consumes a bill id**, so a
dry-run `add` never calls it.

### `home/trade`

One tool for a whole trade, driven by `action`. It sets up RimWorld's own
`TradeSession` directly, so the entire stock sheet is one read and any row is
addressable by name or index — the vanilla dialog's scroll view reached about nine
of 44 rows. Every reply carries `action` and `sessionActive`, and a session block
(`traderId`, `traderName`, `traderKind`, `negotiator`, `giftMode`,
`openedByThisTool`, `openedAtTick`) rides on `open`, `sheet`, `set`, `preview`,
`status` and the cannot-afford failure.

- **`list_traders`**: `mapTraderCount`, `orbitalTraderCount`, `traders[]` (`id`,
  `name`, `pawnName`, `faction`, `traderKind`, `traderCategory`, `position`,
  `canTradeNow`, `downed`, `dead`, `hasQuest`, `goodsStacks`,
  `negotiatorDistance`, `negotiatorAdjacent`, `colonySilverForThisTrade`),
  `orbitalTraders[]` (adds `ticksUntilDeparture`, `traderSilver`, `note`),
  `commsConsoles`, `usableCommsConsoles`, `orbitalTradeBeaconsPowered`,
  `negotiator` and `negotiatorCandidates[]` (`name`, `id`, `socialSkill`,
  `tradePriceImprovement`, `position`, `drafted`, `talkingCapacity`,
  `hearingCapacity`), `silverOnMapTotal`, `silverNote`.
- **`open`**: `sheet`'s payload plus `negotiatorDistance`, `negotiatorAdjacent`,
  `adjacencyNote`, `cannotSellReasons`.
- **`sheet`**: `rowCount`, `rowsReturned`, `omittedByFilter`, `omittedByRowCap`,
  `omittedUntradeable`, `filters`, `rows[]`, `balance`, `signConvention`. A row:
  `index`, `label`,
  `defName`, `stuff`, `category`, `colonyCount`, `traderCount`, `buyPrice`,
  `sellPrice`, `buyPriceType`, `sellPriceType`, `baseMarketValue`,
  `traderWillTrade`, `isCurrency`, `isPawn`, `pawnDescription`,
  `countToTransfer`, `actionToDo`, `minCount`, `maxCount`.
- **`set`**: `linesRequested`, `linesApplied`, `linesRejected`, `allowPawns`,
  `lines[]`, `balance`, `staged[]`. **`preview`**: `balance`, `staged[]`,
  `wouldSucceed`.
- **`accept`**: `executed`, `actuallyTraded`, `moved[]`, `balanceBefore`,
  `traderResponse[]`, `goodwillBefore`, `goodwillAfter`, `questReceived`,
  `indicesInvalidated` (always true — `TryExecute` calls `TradeDeal.Reset()`, so
  sheet indices are stale afterwards).
- **`cancel`**: `wasActive`, `discarded[]`, `questReceived`. **`close_dialog`**:
  `dialogsFound`, `dialogs[]`, `sessionWasActive`, `questReceived`,
  `openWindows[]`. **`status`**: the session block, `tradeDialogOpen`,
  `tradeDialogType`, and with a session open `rowCount`, `balance`, `staged[]`.

`staged[]` rows are `index`, `label`, `defName`, `count`, `action`, `unitPrice`,
`lineValue`, `isCurrency`. `balance{}` is `giftMode`, `currencyRowPresent`,
`colonySilverNow`, `silverCountToTransfer`, `colonySilverAfter`,
`netSilverToColony`, `traderSilverNow`, `traderSilverAfter`, `colonyCanAfford`,
`traderHasEnoughSilver`, `buyLines`, `sellLines`, `buyValue`, `sellValue`.

**Errors are structured results, never exceptions**: `success: false` with
`error`, `errorKind` and a best-effort `sessionActive`. `errorKind` is one of
`no_dispatcher`, `busy`, `bad_action`, `no_map`, `dialog_open`, `session_open`,
`no_trader_id`, `trader_not_found`, `cannot_trade_now`, `no_negotiator`,
`not_adjacent`, `setup_failed`, `no_session`, `bad_lines`, `exception`,
`no_window_stack`, `cannot_afford`, `execute_threw`, `restage_mismatch`. Per-line failures inside
`set` carry `error` but only the pawn guard carries an `errorKind`
(`pawn_sale_refused`) — an inconsistency worth fixing.

**A row the trader will not trade is not on the sheet.** `includeUntradeable`
(default **false**, on `sheet` and therefore on `open`) drops every row whose
`Tradeable.TraderWillTrade` is false, and counts them in `omittedUntradeable`.
Before this, every row came back with `traderWillTrade` and the Python column
printed an `x` that read as **staged** until a sell bounced — the combat supplier
with `WillTrade: false` on all leather. `traderWillTrade` is still on every row
that IS emitted, so nothing is hidden about a row you can see. **`rowCount` is
unchanged: it is `TradeDeal.AllTradeables.Count`, the trader's whole sheet,
and it COUNTS the omitted rows** — `rowsReturned` is what came back, and
`rowCount - rowsReturned` is accounted for by `omittedUntradeable +
omittedByFilter + omittedByRowCap`. **Row indices stay absolute**, so an `index`
read off an `includeUntradeable: true` sheet still addresses the same row on a
default one. `filters{}` echoes `includeUntradeable`.

`set` on an untradeable row still refuses, and now **names the row**: the label,
the `#index`, the defName, `traderWillTrade: false`, that nothing was staged, and
that the row is omitted from the sheet unless `includeUntradeable: true` — so a
caller that reached one from somewhere else is not left wondering why it was not
on the sheet it read.

**Signs.** `Tradeable.CountToTransfer` positive = the colony **buys**, negative =
the colony **sells**; the legal range per row is `[-colonyCount, +traderCount]`,
reported as `minCount`/`maxCount`. **`giftMode` flips the convention**, so the
pawn guard follows `TradeSession.giftMode` rather than hardcoding a sign, and an
over-max count is **clamped, not refused**. **Selling a pawn is refused without
`allowPawns: true`**; buying one is always allowed, and the
`traderWillTrade: false` check runs first, because a trader who will not take the
pawn at all should not be told to pass `allowPawns`. **The silver row is computed,
not set** — `TradeDeal.UpdateCurrencyCount` recomputes it from every other line —
so a non-zero `set` on it is refused (in gift mode it is settable).

Hazards. `Transferable.AdjustTo` calls `Log.Error` on a value `CanAdjustTo`
rejected, so every line is tested with `CanAdjustTo` first.
`TradeDeal.TryExecute`'s cannot-afford branch dereferences `Dialog_Trade` with no
null check and throws headless, so `accept` evaluates the same predicate itself.
`TradeSession.SetupWith` logs a warning when the trader is unwilling, which can pop
the log window on stream, so `open` checks `CanTradeNow` first. A vanilla
`Dialog_Trade` caches its own row list at `PostOpen`, so while one is open every
mutating action refuses with `dialog_open` — `sheet` still works, and
`close_dialog` exists for that case, closing the session too because
`Dialog_Trade.Close` does **not** clear `TradeSession.trader`, and going through
the virtual `Window.Close` so the quest handoff still fires.
`Tradeable.GetPriceFor` memoises price factors on first read, the same scratch
mutation the vanilla dialog makes every draw frame. Overlapping invocations are
guarded by an `Interlocked` flag → `errorKind: "busy"`, and
`Messages.liveMessages` is reflected to populate `traderResponse`.

`requireAdjacent` defaults **false** because nothing on the trade path checks
distance — verified across `TradeSession.SetupWith`, the `TradeDeal` constructor,
`Tradeable.ResolveTrade` and `TradeDeal.TryExecute`. Note that
`Pawn_TraderTracker.GiveSoldThingToPlayer` drops bought goods at the **trader's**
cell and adds them to the trader lord's `extraForbiddenThings` — vanilla
behaviour, and *not* the same field as `CompForbiddable.Forbidden`, so
`home/list_things` will not report them as forbidden. `receiveQuest` (default
true) calls `TradeUtility.ReceiveQuestFromTrader` when the trader carries one.

**Watch** applies to `accept` alone, because that is the action a player would
have watched happen. Every other action reports
`watch.reason: "action is not watched"`.

**`accept` opens the real trade window.** M did not know a trade had
happened at all last session: selecting the trader and panning the camera is not
enough, because a deal executed headless looks like nothing. So with `watch` on,
`accept` now selects the trader, opens the actual `Dialog_Trade` showing the
staged rows, holds it `watchSeconds` (default **8**, clamped 1..60), executes the
deal **while the window is on screen**, then closes it. `watch{}` gains two
trade-only keys beside the usual nine: **`dialogShown`** (bool) and
**`secondsShown`** (int).

**The wrinkle, and why this is not a re-stage.** `Dialog_Trade`'s only
constructor is `Dialog_Trade(Pawn playerNegotiator, ITrader trader, bool
giftsOnly)` — negotiator first — and its body calls `TradeSession.SetupWith`,
which ends in an unconditional `deal = new TradeDeal()`. `TradeDeal`'s
constructor calls `Reset()`, which clears the tradeable list and rebuilds it. So
**merely constructing the window wipes every staged count**, and `Tradeable`
instance identity is not stable across two deals.

The obvious repair — snapshot `(thing, count)` pairs and re-apply them with
`TransferableUtility.TradeableMatching` — is reachable and **lossy**:
`TransferAsOne` groups on hit points within 10, quality, stuff and
`tradeNeverStack`, and the synthetic zero-stack silver row is a brand new `Thing`
on every deal, so it matches nothing. Any of those turns "the same trade" into "a
similar trade", which is the one thing a deal a person previewed may never
become.

`TradeSession.deal` is a **public static field**, so there is an exact route
instead: keep the original `TradeDeal` **object**, let the constructor install
its throwaway, then **put the original back before the window is added to the
stack**. `Window.PostOpen` → `Dialog_Trade.CacheTradeables` reads
`TradeSession.deal.AllTradeables` and stores *references to those same
`Tradeable` objects*, so the dialog caches the original deal and draws the
original staged counts. Nothing is re-applied and nothing is matched — the rows
on screen are the same objects `preview` costed. The ordering is the entire
trick.

This works because **`Dialog_Trade.Close(bool)` leaves `TradeSession` alone**: it
neither nulls `trader` nor calls `TradeSession.Close()` (which has *no callers
anywhere* in Assembly-CSharp), and it pops **no confirmation** for pending
changes — the class overrides neither `PreClose`/`PostClose` nor
`OnCancelKeyPressed`, and its only `Dialog_MessageBox`es are the
impaired-negotiator notice and the trader-short-funds prompt. So the session and
its counts survive the window closing, which is what makes the sequence possible.

The deal is verified **three times** — after the swap, after the window caches
its rows, and once more immediately before `TryExecute` (the window is up for
seconds and a person could click in it). The check is a sorted
`defName=count` signature plus `netSilverToColony`. Any mismatch **refuses** with
`errorKind: "restage_mismatch"`, reporting `stagedExpected` and `stagedActual`,
and **executes nothing**, leaving the session open and untouched: nothing is
traded that was not previewed.

Two more details. The window is closed **after** `Dispatch`, so `TryExecute`
still had a real `Dialog_Trade` to reach for on its cannot-afford branch (that
branch calls `Find.WindowStack.WindowOfType<Dialog_Trade>().FlashSilver()` with
no null check — the same headless NRE `accept` already pre-empts). And a
`Dialog_Trade` a **person** opened is still refused with `dialog_open`; only this
tool's own window is executed over.

The hold **blocks the tool call** — the deal must execute after the window has
been seen, and keeping the execute inside the call is what lets the reply say
honestly what happened. The wait is off the main thread, between two hops, the
same shape `Watch.Lead` already uses, so it does not block the game thread. But
the bridge runs one call at a time, so an accept occupies the bridge for about
`watchSeconds`. `Dialog_Trade` sets `forcePause = true` and
`absorbInputAroundWindow = true` in its constructor and both are left at vanilla,
so the colony pauses and input is absorbed while the window is up — exactly what
a person sees when they trade. The window is scrolled to the first staged row by
reflection (`cachedTradeables` and `scrollPosition` are both private; rows lay
out at `y = 6 + i*30`), which is decorative and best-effort: a window that did not
scroll is still a window.

`TRADE-TEST.md` holds the live procedure and what it verified — a real sale and
purchase against a caravan, cross-checked against world state, the load-bearing
control being that **nothing pauses the game across any refusal**. Still
unexercised: the `pawn_sale_refused` branch itself, the ambiguous-name
`candidates[]` path, `requireAdjacent: true`, `open` against
`canTradeNow: false`, gift mode entirely, `questReceived: true`, and the
Ideology-forbidden-trade refusal.

### `home/research`

Research in one call: what is being researched, what could be, and why nothing
is. Parameters: `locked`, `finished`, `unlocks` (opt-in blocks, all false by
default), `filter` (a case-insensitive substring on label **or** defName,
applied to every list and to none of the counts), `set` (the write — a defName,
an exact label, or a unique substring), `dryRun` (**true** by default),
`watch` and `watchSeconds`. A call with no arguments is the whole read.

Top level, always: `mapName`, `anomalyActive`, `playerTechLevel`, `current` (a
project row, or `null`), `currentNote`, `currentByCategory{}`,
`anyProjectIsAvailable`, `available[]`, `availableCount`, `availableListed`,
`lockedCount`, `finishedCount`, `hiddenCount`, `totalCount`,
`unreadableProjects[]`, `blocks{locked,finished,unlocks}`, `filter`,
`filterApplied`, `researchBenches{}`, `write` (an object or **null**), `dryRun`,
`applied`, `notes{}`. Conditionally: `locked[]` + `lockedListed` with
`locked: true`; `finished[]` (defNames only, sorted) + `finishedListed` with
`finished: true`.

A project row: `defName`, `label`, `cost`, `baseCost`, `progress`,
`progressPercent`, `costApparent`, `techLevel`, `tab`, `tabLabel`, `finished`,
`canStartNow`, `prerequisitesCompleted`, `isCurrent`, `hidden`,
`techprintsNeeded`, `techprintsApplied`, `techprintRequirementMet`,
`prerequisites[]`, `requiredResearchBuilding`, `requiredResearchFacilities[]`,
and more of the def's own fields. A **locked** row adds
`missingPrerequisites[]`, `missingBuilding`, `missingFacilities[]`,
`missingMechanitor`, `missingAnalyzed[]`, `missingInspection` and
`lockReasons[]`. With `unlocks: true` every listed row also carries `unlocks[]`
(`defName`, `label`, `type`), capped at 40 per project with `unlocksNotListed`
counting the rest.

**`current: null` IS the game's `Need research project` alert** — no tab has to
be opened to confirm it, and `currentNote` says so in words. On Anomaly each
`KnowledgeCategoryDef` has its own slot, so `currentByCategory{}` is keyed by
category defName; without the DLC it is an empty object and `anomalyActive` is
false.

`researchBenches{}`: `count`, `poweredCount`, `anyPowered`, `benches[]`
(`defName`, `label`, `pos{x,z}`, `needsPower`, `powered`, `facilities[]`),
`researchers[]` (`name`, `everWork`, `priority`, `active`, `intellectual`,
`passion`), `researcherCount`, `activeResearcherCount`, `skipped[]`. "Nothing
is being researched" has four causes and three of them are not the project: no
bench, an unpowered bench, or nobody with Research switched on.

**The four buckets are disjoint and exhaustive** — `IsFinished`, then
`IsHidden`, then `CanStartNow`, then everything else — so
`availableCount + lockedCount + finishedCount + hiddenCount +
unreadableProjects.length == totalCount` always, whatever `filter` was passed.

`write` is **null** when no `set` was given. Otherwise: `requested`, `resolved`
(`defName`, `label`) or null, `candidates[]`, `alreadyCurrent`, `refused`,
`reason`, `before{}`, `after{}`, `changed`, `afterIsPredicted`, `appliedNote`,
and `lockDetail{}` when the refusal was "cannot start yet". Resolution is exact
defName, then exact label (case-insensitive), then a unique substring; **an
ambiguous substring is refused with the candidates listed**. A finished
project, or one that cannot start, is refused with the specific requirement.
`applied` means `SetCurrentProject` was called; `changed` means the current
project is a *different* def than before. On a real run `after` is **read back**
through `GetProject()`, never echoed. The write does what
`MainTabWindow_Research.DoBeginResearch` does minus the UI
(`SetCurrentProject` plus the tutor event): no sound, no selection. **Watch**
opens the Research main tab before the write and then moves the pane onto the
newly chosen project, so the change is seen where a player would have made it.

**`CostApparent` and `ProgressApparent` are deliberately not read** (both call
`Faction.OfPlayer`); `costApparent` is reconstructed from
`CostFactor(playerTechLevel)` and is null with `costApparentReadable: false`
when there is no player faction. **`Pawn_SkillTracker.GetSkill` is not called**
— its miss path is `Log.Error` and the wrong skill — so `pawn.skills.skills` is
walked, and **`GetPriority` is gated on `EverWork`**. One documented side
effect: `ResearchManager.GetProgress` inserts a zero row for an untouched
project, exactly as vanilla does when the Research tab first draws — a zero row
means the same as no row.

Verified: `ResearchManager.GetProject(KnowledgeCategoryDef = null)`,
`.GetProgress`, `.GetTechprints`, `.AnyProjectIsAvailable`,
`.SetCurrentProject`, `.CurrentAnomalyKnowledgeProjects` (null when Anomaly is
inactive — gated on `ModsConfig.AnomalyActive`); `ResearchProjectDef.Cost`/
`.ProgressReal`/`.ProgressPercent`/`.IsFinished`/`.CanStartNow`/
`.PrerequisitesCompleted`/`.TechprintCount`/`.TechprintsApplied`/
`.PlayerHasAnyAppropriateResearchBench`/`.IsHidden`/`.UnlockedDefs`/
`.CostFactor`; `ListerBuildings.allBuildingsColonist`;
`CompAffectedByFacilities.LinkedFacilitiesListForReading`;
`CompPowerTrader.PowerOn`; `TutorSystem.Notify_Event`.

### `home/order`

The tool the 2026-09-04 session needed and did not have. Two colonists died
while the fork playing could not get an attack order through -- and every one of
those failures was in the float-menu path, not in the game. This issues the
**job** vanilla issues and never opens a menu.

Parameters: `action` (`resolve` default / `draft` / `undraft` / `attack` /
`goto` / `equip` / `rescue` / `tend` / `haul` / `work` / `rest` / `deploy`),
`pawn`, `target`, `x`, `z`, `mode` (`auto` / `melee` / `ranged`), `draft`
(default **true**), `dryRun` (default **false** -- this is the one write tool
that defaults to doing the thing), `requireHostile` (default **false**),
`watch` (default true),
`watchSeconds`.

Response: `action`, `dryRun`, `applied`, `pawn{}`, `target{}`, `job{}`,
`wouldIssue{}`, `candidates[]`, `after{}`, `watch{}`, `error`, `errorKind`.
`target{}` carries `isPlayerFaction` (compared against `Faction.OfPlayerSilentFail`,
since `faction` is a colony name). `rest` lays the pawn down in the named bed, the
bed covering `x`/`z`, or `RestUtility.FindBedFor`, undrafting first; `goto` with
`draft: false` issues a plain undrafted Goto job.

**Five id forms, and an explicit one never ties.** `Rat361788` matched nothing
on the old path and `Rat` was "ambiguous" because two rats shared a label; both
now work. Every pawn and thing answers to all of:

| Form | Example | Source |
|---|---|---|
| full load id | `Thing_Wolf_Timber334862` | `Thing.GetUniqueLoadID()` -- what `home/status` prints |
| ThingID | `Wolf_Timber334862` | `Thing.ThingID` |
| bare number | `334862` | `Thing.thingIDNumber` |
| `DefName@x,z` | `TableMachining@62,141` | the address `home/bills` and `home/building_config` take |
| name or label | `Longhoff`, `rat` | case-insensitive, exact then unique substring |

The first four are **explicit**: they hit one thing or nothing, so they can
never come back `ambiguous`. Only the last can tie, and a tie returns
`candidates[]` -- capped at 25 rows, with the true total in the error text --
where every row carries `idForms[]`, so the caller learns the address it should
have used. `matchedBy` says which form actually matched. The pool is
`map.listerThings.AllThings` plus each corpse's `InnerPawn`, so a pawn that has
just died still resolves and reports `dead: true` instead of vanishing.

**Hostility is never a precondition.** `FloatMenuUtility.GetMeleeAttackAction`
and `GetRangedAttackAction` do not test it at all -- hostility only decides
whether the float menu *draws* the option. So a downed manhunter, a wolf that
has stopped being aggressive, and a tame animal all resolve and can all be
attacked. `requireHostile: true` restores the old behaviour for anyone who
wants it; `hostileToPlayer` is reported on every target either way.

**Job shapes are vanilla's, field for field**, read out of Assembly-CSharp
1.6.9676.17735 rather than guessed:

| action | job | notes |
|---|---|---|
| `attack` melee | `AttackMelee`, `killIncappedTarget = target.Downed` | that is the whole job -- no expiry, no attack cap. `killIncappedTarget` is what the game calls "Melee attack **to death**" |
| `attack` ranged | `AttackStatic` | **no `verbToUse`**: `JobDriver_AttackStatic` picks the verb itself every run. The verb is reported, not written |
| `goto` | `Goto` | cell snapped with `CellFinder.StandableCellNear` + `RCellFinder.BestOrderedGotoDestNear`, as the menu does |
| `equip` | `Equip` | the target is **unforbidden first**, as vanilla does -- the likely cause of "equip failed, then the identical retry worked" |
| `rescue` | the `WorkGiver_RescueDowned` prioritize job, else `Rescue` + a bed from `RestUtility.FindBedFor` | `job.rescuePath` says which; no bed = no job in vanilla either |
| `tend` | the `WorkGiver_Tend` prioritize job, or `TendPatient` + `draftedTend` on the ground | two paths, `job.tendPath` says which — see below |
| `haul` | whatever `WorkGiver_Haul` builds (`HaulToCell` / `HaulToContainer`) | the giver's own job object, issued unmodified |
| `work` | whatever `WorkGiver_DoBill` builds (`DoBill`, or `Refuel` / a haul-off) | likewise |
| `deploy` | `UseVerbOnThingStaticReserve` (one-use packs), `UseVerbOnThingStatic`, or `Verb.OrderForceTarget`'s own `ai_IsWeapon ? AttackStatic : UseVerbOnThing` | `job.verbToUse` is the pack's own gizmo verb. The driver is `StopDead` then `CastVerb`: the pawn does **not** walk, it throws from where it stands |

`mode: "auto"` asks the game's own predicate, `FloatMenuUtility.UseRangedAttack`
-- which reads the equipped **verb**, not `ThingDef.IsRangedWeapon`.

**Ground truth this settled**, since two forks disagreed about each:

- **Tending has two paths, and the ordinary one is undrafted.** Everyday
  tending -- a patient in a bed -- is "Prioritize tending X", the
  `WorkGiver_Tend` prioritize order, which needs **no draft**. `tend` tries that
  first and reports `job.tendPath: "work"`. Only when it yields nothing **and**
  the patient is not in a bed (`RestUtility.InBed`) does the drafted provider
  get a turn: `FloatMenuOptionProvider_DraftedTend` never looks at a bed --
  `IsValidTendTarget` is
  `if (!doctor.Drafted && patient != doctor) return false;` then
  `if (patient.Downed) return true;` -- but its `Drafted` is **true** and its
  `Undrafted` is **false**, so the option is not drawn until the doctor is
  drafted. That is why one fork read `Rescue / Strip` off an undrafted pawn and
  concluded ground tending was impossible: it is real, it just needs the draft.
  That fallback reports `job.tendPath: "drafted"` and sets `draftedTend`.
  Medicine is optional on it (`FindBestMedicine(..., onlyUseInventory: true)`
  may be null; vanilla then labels it "without medicine" and issues the job
  anyway). A patient in a bed that yields no job is refused with the game's own
  `JobFailReason` rather than being retried as a drafted tend.
  `rescue` works the same way -- `WorkGiver_RescueDowned` first
  (`job.rescuePath: "work"`), the bed-finding float-menu path second
  (`"drafted"`). Both paths are chosen during a dry run too, so `wouldIssue`
  says which one WOULD be taken.
- **An incapable-of-violence pawn drafts fine.** `Pawn_DraftController`'s
  `Drafted` setter holds no violence check, no mental-state check, no capability
  check at all, and `GetGizmos` disables the toggle only for downed, deathresting
  and un-draftable mechs. So `canBeDrafted` does **not** test `WorkTags.Violent`,
  and `draft` / `undraft` / `goto` all work for such a pawn -- only `attack` and
  `equip` refuse, with `incapable_of_violence`.
- **Zero float-menu options in a mental break** is `FloatMenuContext`'s
  constructor doing `selectedPawns.RemoveAll(p => !p.CanTakeOrder)`, and
  `Pawn.IsColonistPlayerControlled` is `Spawned && IsColonist && MentalStateDef
  == null`. So a broken pawn is refused here with `errorKind: "mental_state"`
  and a sentence naming that chain, instead of an empty list. `undraft` is the
  one action allowed through, because dropping a draft is unguarded.

**`errorKind`** is one of `pawn_not_found`, `target_not_found`, `ambiguous`,
`pawn_dead`, `pawn_downed`, `mental_state`, `incapable_of_violence`,
`target_dead`, `not_reachable`, `draft_refused`, `draft_cleanup_required`,
`job_refused`, `job_unverified`, `bad_arguments`, `work_disabled`,
`missing_haul_designation`, `no_storage`, `unreachable_storage`, `no_bill`.

**`job.verified` is the point of the tool.** `TryTakeOrderedJob` returning true
means the game accepted the job, not that it is running it. So `Pawn.CurJob` is
read back after the issue and compared by def **and** target; a mismatch is
`success: false` with `errorKind: "job_unverified"` and a sentence saying what
the pawn is doing instead. (`TryTakeOrderedJob` also checks
`KeyBindingDefOf.QueueOrder.IsDownEvent`, so a physically held SHIFT key would
queue the order rather than replace the current one -- which is exactly the kind
of silent miss `verified` exists to catch.)

**`haul` and `work` go through the WorkGiver path**, which is the same generic
path the float menu's work options use: walk `DefDatabase<WorkTypeDef>` in
`workGiversByPriority` order, take the first `WorkGiver_Scanner` whose
`JobOnThing(pawn, thing, forced: true)` returns a job, and issue it with
`TryTakeOrderedJobPrioritizedWork(job, giver, cell)`. `job.workGiver`,
`job.workType` and `job.billLabel` are reported. Two deliberate departures:
`Pawn_WorkSettings.WorkGiversInOrderNormal` is **never** touched, because its
cache rebuild calls `GetPriority`, the `Log.Error`-and-write path; and a work
type merely set to priority 0 is **not** a refusal, because reading the priority
safely is impossible and a player-forced order is the thing that overrides
priorities anyway. Only a real incapability refuses, with `work_disabled`.
`FloatMenuMakerMap.makingFor` is set for the duration of the scan and restored
in a `finally`, because `WorkGiver_DoBill` gates every one of its
`JobFailReason.Is(...)` messages on it -- without that, a bench that cannot run
a bill comes back as a bare null with no reason at all.

**Watch** selects the **target** first and jumps the camera to it, waits the
standard 1500 ms lead, then selects the **pawn** and moves the camera to it -- so
a viewer sees who is being ordered at what, and ends on the inspect pane showing
the new job ("Attacking wolf"). No float menu is ever opened. A dry run and a
refusal show nothing, as everywhere else.

**Hazards this tool dodges**, each IL-checked in the exact method called:
`ForbidUtility.SetForbidden` is never called (three `Log.Error` arms, and
`Log.Error` pauses the colony) -- `CompForbiddable.Forbidden` is written direct;
`ForbidUtility.IsForbidden` is never called (its body opens with
`faction != Faction.OfPlayer`); `Pawn_WorkSettings.GetPriority` is never called.
`CellFinder.StandableCellNear`, `RCellFinder.BestOrderedGotoDestNear`,
`RestUtility.FindBedFor`, `HealthAIUtility` and `EquipmentUtility` were each
grepped and are clean. `Pawn_JobTracker.TryTakeOrderedJob` emits `Log.Warning`,
not `Log.Error`, on a failed reservation, and `Log.Warning` has no `Pause()`.
The one place the game's own code reaches `Faction.OfPlayer` from here is
`StoreUtility.StoragePriorityAtFor` under `haul`, and only for a thing already
sitting in a building storage; that getter's `Log.Error` arm fires only when
there is **no player faction**, which cannot be true on a loaded colony map.

**`deploy` — the worn-pack gizmo, and the rule the UI refuses in silence.**
`{action:"deploy", pawn, x, z, dryRun}`. `target` is optional and names *which*
worn pack, only when the pawn wears more than one — a worn pack is not on
`map.listerThings`, so it is matched against `pawn.apparel` by thingId, defName,
label or gizmo label, never by cell.

**A pack deploy is a thrown grenade, not a build order.** `Apparel_PackTurret`
is **Anomaly**, not Odyssey (`Data\Anomaly\Defs\ThingDefs_Misc\Apparel_Packs.xml`),
and like all DLC content its code is in the ordinary `Assembly-CSharp.dll`. It
carries `CompProperties_ApparelVerbOwnerCharged` (1 charge, `destroyOnEmpty`,
gizmo shown drafted and undrafted) and one verb,
`Verb_LaunchProjectileStaticOneUse`, range 22.9, `canTargetLocations` only,
`requireLineOfSight` defaulting **true**. Two gates decide the cell:

| gate | rule | what the game does when it fails |
|---|---|---|
| `Verb_LaunchProjectileStaticOneUse.ValidateTarget` | `cell.GetFirstBuilding(map) == null` **and** `cell.Standable(map)` — ANY `Building` refuses: wall, door, **power conduit under the floor**, chair, unfinished `Frame` | the click is **swallowed** and the targeter **stays open** |
| `Verb.CanHitTarget` (`TryFindShootLineFromTo`) | inside `EffectiveRange`, and `GenSight.LineOfSight` from the pawn's cell or a `ShootLeanUtility` lean-out cell | the targeter **CLOSES** and nothing is placed |

**Neither posts a `Messages.Message`.** The only branch in the path that does
is `ReloadableUtility.CanUseConsideringQueuedJobs`, for a pack with no charges
— which is why `home/status` messages were empty for the four clicks lost on
2026-09-08. Inside a mined-out mountain room, a cell on the far side of the rock
is invisible however close it is. `EffectiveMinRange` is **0** for a plain cell,
because `VerbUtility.AllowAdjacentShot` returns true when the target has no
`Thing`, so "too close" is not a real refusal here.

`ValidateTarget` is **never called by this tool**: it chains to
`CanUseConsideringQueuedJobs`, which reads `Event.current.shift`, and
`Event.current` is null outside `OnGUI`. Its two cell lines are run directly.
`Pawn.CanReserve(cell)` is pre-checked because
`JobDriver_CastVerbOnceStaticReserve.TryMakePreToilReservations` reserves the
target cell. `Verb.Available()` is called only after
`Faction.OfPlayerSilentFail` is proved non-null, because
`Verb_LaunchProjectile.Available()` reaches `Faction.OfPlayer`, whose null arm
is a `Log.Error` and therefore a `TickManager.Pause()`.

`diagnostics.deploy` = `{ok, reason, reasonKind, rule, cell{x,z},
pack{thingId, defName, label, gizmoLabel, verbClass, charges, maxCharges,
oneUse}, checks{verbClass, requiresLineOfSight, mustCastOnOpenGround,
availableChecked, charges, maxCharges, range, minRange, distance, fogged,
buildingOnCell, standable, lineOfSight, inRange, canHitTarget, canReserve},
validCellsNearby[≤8 of {x, z, distanceFromAsked, distanceFromPawn}],
validCellsOrigin}`. `rule` is the plain-words statement above and is present on
success and refusal alike. On any refusal `validCellsNearby` is filled by a
radial scan around the cell asked for, falling back to a scan around the pawn
out to the verb's range.

New `errorKind`s: `no_deploy_pack`, `deploy_refused`, `deploy_cell_blocked`,
`deploy_out_of_range`, `deploy_no_line_of_sight`, `deploy_cell_reserved`.

**Three further silent closes**, all `Targeter.ConfirmStillValid`, which runs
every frame: the caster stops being **selected**, its map changes, or
`verb.Available()` goes false. A click-driven route that changes the selection
between the gizmo click and the map click loses the targeter for that reason
and no other.

## Decompiling the game

When a tool has to match what the game does — zone bookkeeping, blueprint wipes,
which cooler face is which — read the game's code rather than guessing from
behaviour. ILSpy's command-line build is installed as a dotnet tool under
`C:\Home\tools\ilspy\` (version 8.2, targets .NET 6; this machine has 7/8/10, so
it needs roll-forward):

```bash
export DOTNET_ROLL_FORWARD=Major
"C:\Home\tools\ilspy\ilspycmd" -t RimWorld.Building_Cooler "C:\Program Files (x86)\Steam\steamapps\common\RimWorld\RimWorldWin64_Data\Managed\Assembly-CSharp.dll"
"C:\Home\tools\ilspy\ilspycmd" -l c "<same dll>" | grep -i starv     # list classes
```

Namespaces are not guessable: `Verse.Zone`, `Verse.ZoneManager`,
`Verse.GenSpawn`, but `RimWorld.Designator_Build`, `RimWorld.GenConstruct`,
`RimWorld.GenDate`, `RimWorld.Zone_Stockpile`. A wrong namespace prints an
eight-line error, not an empty file. If the folder is ever gone:
`dotnet tool install ilspycmd --version 8.2.0.7535 --tool-path "C:\Home\tools\ilspy"`
(a newer ilspycmd needs a newer runtime).

The recurring finding is that RimWorld getters write. Before using one in a
read-only tool, IL-scan it for field stores. The ones already settled, so nobody
re-derives them: `Faction.OfPlayer` is `OfPlayerSilentFail` + `Log.Error`, and
`Log.Error` calls `TickManager.Pause()`; `Zone.Cells` shuffles;
`Zone_Growing.PlantDefToGrow` assigns a crop; `Bill_Production.ShouldDoNow()`
writes `paused`; `Room.ContainedAndAdjacentThings` returns a cached buffer;
`Thought_Situational.Notify_BecameActive` deletes memories, and both
`ThoughtHandler.GetDistinctMoodThoughtGroups` and
`Pawn_RelationsTracker.OpinionOf` reach it; `TickManager.TicksAbs` and
`GameCondition.TicksLeft` both route to `Log.Error`; `Pawn_WorkSettings.GetPriority`
initialises a missing priority table; and `TraverseParms.For(pawn, …)` calls
`pawn.CurJob.GetCachedDriver(pawn)`, a lazy same-pawn initialisation of
`Job.cachedDriver` — benign, but a write.

**Five more roads to `Faction.OfPlayer`**, each of which looks like an innocent
read: `MapPawns.FreeColonists` and `.FreeColonistsSpawned` (walk
`AllPawnsSpawned` and test `Pawn.IsFreeColonist` instead); `Thing.GetGizmos()`,
unconditionally and in six places; `RecipeDef.AvailableNow`, on three paths;
`ForbidUtility.IsForbidden(Thing, Faction)` (read `CompForbiddable.Forbidden`
directly); and `Building_Door.PawnCanOpen`, which reads it when
`Map.Parent.doorsAlwaysOpenForPlayerPawns` is set — harmless under a `ByPawn`
reach test, because a free colonist existing at all means the player faction
does. `Bill.ValidateSettings` is a sixth and is simply never called.

Two more `Log.Error` paths on the reachability side: `TraverseParms.For(pawn:
null)` is one in itself, and `Reachability.CanReach` `Log.Error`s when handed a
pawn spawned on another map — it answers a flat false for one that is not
spawned at all, which is the quieter half of the same trap. Screen the traverser
before passing it.

And on the write side: `Zone.AddCell` never
tells the previous owner, `Zone.RemoveCell` clears the *grid's* owner whoever that
is, `SlotGroup.Notify_LostCell` / `Notify_AddedCell` `Log.Error` when the haul
grid disagrees, `GenSpawn.Spawn` wipes with `WipeMode.Vanish` (no refund) unless
the caller wiped with `DestroyMode.Deconstruct` first, and
`GenSpawn.SpawningWipes(blueprintDef, itemDef)` is false, so a blueprint never
destroys a chunk. `Building_Bed.ForPrisoners = false` is a `Log.Error` — the
setter only accepts true, and `ForOwnerType` is the field to write.
`FlickUtility.UpdateFlickDesignation` can open a tutor `Dialog_MessageBox`, so
the Flick designation is placed by hand. `ThingFilter.SetAllow(ThingCategoryDef)`
`Log.Error`s before `ThingCategoryNodeDatabase` is initialised.
`BillStack.MakeNewBill` **consumes a bill id**, so it must not be called to
answer a hypothetical. And `Selector.Select` `Log.Error`s on a null, destroyed,
unspawned or world pawn, which is why every watch target is pre-checked before
it is selected.
