# Bugfix results — 2026-09-05

This pass addressed the day-41→52 errata without saving the disposable live test.

## Verified live

- `home/place_building` now requires an explicit `dryRun`, accepts canonical `defName` with no unknown arguments, retains a guarded legacy `def` alias, and uses the same occupancy predicate for preview and apply. A bed on occupied wall cells was refused identically in both modes; a bed and `TileSlate` floor on empty cells were placed successfully.
- `buildings.py --pending` returned all three live blueprints, including the `TileSlate` terrain blueprint and its four-slate-block cost. Its empty-state wording no longer counts intentionally excluded built structures as filtered pending work.
- Bare-leaf `rim.game('list_things', ...)` resolved uniquely to `home/list_things` and succeeded. Namespace ambiguity is now explicit and transient empty discovery is not cached.
- The `home/dialog_text` contract ran deterministically with no dialog: 36/36 live checks passed.
- On a spawned stonecutter, additive `--allow ChunkGranite` remained a clearly explained no-op. `--only ChunkLimestone` narrowed the filter in dry-run and real read-back; the resulting bill saw 671 limestone and excluded 1,077 other units. After the final summary fix, `changed[]` explicitly showed the five-def list becoming `[ChunkLimestone]`.

The final installed DLL SHA-256 was `F552A6261042353F956D07DE204FE49830237A8EDBE1BE5FE9311DB5362C75B5`. The game remained paused and all test changes were left unsaved.

## Offline-proven fixes

- `act.py` has a real CLI and help, accepts `--category`, exposes rejected cells and reasons, and returns failure when the bridge refuses the action. `act.clear()` uses the qualified Orders/Cancel designator. `zones.py --help` exits before bridge initialization.
- Combat ledgers record their creating Hands turn. A later turn gets an inherited-ledger warning and must explicitly `combat.py adopt` while preserving cleanup obligations, or close it. Ownership refusals happen locally before game access.
- Static pause output now says `source unknown`; the legacy `pausedByPlayer` flag cannot prove human input. Hunting doctrine now explicitly covers muffalo revenge. Floor placement, firefighting, and downed-versus-dead behavior are documented in PLAYBOOK.
- Building configuration accepts a unique bare `x,z` occupied cell and adds direct `--temperature C` support through `CompTempControl` with dry-run/read-back behavior.
- Bills preserve additive `--allow`; an already-allowed def prints a useful no-op explanation. New `--only X,Y` clears prior allowances and establishes a complete whitelist.

Validation: 114 instrument tests and 15 companion tests passed in the final integration run (129 total); the companion Release build completed with zero warnings and zero errors. The buildings/bills slice contributed seven focused regressions.

## Still awaiting live verification

- Cooler temperature read/write and bare-coordinate building selection: the spawned cooler had no player faction and was correctly outside the colony selector, so it was not a valid fixture.
- Raw `rimworld/set_draft` pawn-parameter behavior, architect-designator runtime paths, and inherited-ledger warnings in a real Hands turn.
- Placement occupancy uses one predicate in preview and apply and passed the live occupied/empty matrix; the original cell at 113,139 was not reproduced.
- The historical claim of 45 pending rows with an empty listing was not reproduced; the live floor blueprint appeared correctly, while the misleading empty-state wording is independently fixed.

The transient-message gap, contained-hostile time blocking, stale rota/letter state after reload, unexplained stone-chunk hauling failure, and vanished pemmican remain open in `BUGS.md`.
