# Stream safety and errata fixes — 2026-09-05

**Architecture update:** Normal play and reviewer delivery now use [Supervised play](SUPERVISED-PLAY-2026-09-05.md). The running-time and rota sections below describe the earlier repair pass.

Implemented with three Sol subagents, preserving the pre-existing working-tree edits. Installed the final companion DLL and tested against the agent profile's named day-55 save. No game was saved. At completion, all 39 original `.rws` files matched their starting SHA-256 hashes; the original day-55 save was reloaded and left paused (tick 3806999).

## Running time

- `run.py`, `run.run`, and `run.until` now play for at most **five seconds per invocation**, then pause and return `review needed` if more time was requested. No remaining duration is scheduled. `--force` cannot bypass this cap. Each companion pulse is at most one second, with a bounded client deadline. RPC/report overhead can add wall time beyond the five seconds of play.
- Missing/busy/broken safety watchers refuse to run time; the blind polling fallback is no longer reachable from `until`. Pause claims require explicit `success: true` and `paused: true`. Failure to confirm pause is reported prominently.
- Repeated hostiles, wild hunting predators, qualifying alerts, and already-downed colonists remain blocking. They are not silently dismissed as previously reported scenery.
- Intentional exceptions are explicit and visible for each invocation: `--ignore-hostile STABLE_ID` for a verified contained hostile; `--ack-alert "Exact displayed label"` for a known alert; `--allow-downed STABLE_ID` for a known patient. Other conditions remain watched.
- Companion `messageSinceTick` carries the preceding pulse's `endTick` across calls. Boundary messages at the same tick are conservatively reported, which can repeat a message instead of losing it.
- `move.goto` uses the same review cap. On `review needed`, the pawn can remain drafted, the game is paused, and repeating `goto` deliberately resumes. Letter sweeping is now separate from `run.py` so it does not delay the return.

**Live:** a 120-second request stopped on a newly arriving quest letter in 0.47 seconds. A subsequent request returned `review needed` in 5.24 seconds, after 1,808 ticks, with the game paused. Simulating a missing guard returned `watcher unavailable`, confirmed pause, and advanced zero ticks. The original manhunter-pack arrival was not recreated; threat and carry-over paths additionally have regression coverage.

## Targeting, hauling, and medicine

- Inventory stack positions expose ThingIDs and ID forms. `inv.py --near x,z[,radius]` actually filters the census; the two-number form defaults to radius 12. Live radius-3 output scanned 933 things but showed only 11 nearby units.
- Orders resolve bare cells, `DefName@x,z`, and stable IDs. A live spawned animal wins over same-name inner pawns hidden in corpses. Ambiguous genuinely live targets still require disambiguation.
- **The kitchen chunks lacked Haul designations.** The original marble chunk was reachable, unforbidden, reservable, outside storage, and had a valid dumping destination. Its `alwaysHaulable` and `haulDesignation` were both false. `home/order` now returns `missing_haul_designation` with the exact corrective command, plus storage and hauling diagnostics. The speculative global hauling-cache bypass was removed after live evidence disproved it as the cause.
- **Live hauling proof:** `python act.py apply "Haul things" 121 138` made the existing chunk eligible. Finn's verified `HaulToCell` job moved `Thing_ChunkMarble329434` to `137,128` in Dumping stockpile zone 2. The test was discarded by reload.
- Combat attack/tend/rescue support `--dry-run` without changing the combat ledger. Ground tending outside a cleanup ledger is refused with an actionable route through combat. Raw `home/order` requires explicit `allowPersistentDraft:true` for the auto-drafted path; `combat.py` supplies it under its existing write-ahead cleanup ledger. This is a lifecycle guard, not automatic undrafting after every raw job. A fresh injured ground-patient fixture was not available for an end-to-end live tend test.
- The `set_draft` compatibility shim translates `pawn` to the actual `pawnName`/`pawnId` schema and refuses conflicting selectors. Both Finn's name and `Thing_Human150` passed live undraft calls.

## UI, buildings, and filters

- `ui.py --click` uses the Python API's fresh-layout click path. `say.py` prints a delivery acknowledgement or an explicit failure instead of silence (covered offline; the Sol data-scout smoke test is separate from overlay narration).
- `dialog_text` searches beneath immediate/main-tab overlays and accepts the selected modal directly. Live Options-window inspection returned `RimWorld.Dialog_Options` and its `modFilter` field beneath the overlay.
- `buildings.py --pending` no longer claims the map has no worktables or no power problems. Built-only sections are omitted and the final result is scoped to pending construction; rows filtered by status are not described as faction-checked.
- `byPlayerOnly` is an actual companion alias, with conflict handling. Live calls returned no unknown arguments and used colony-faction filtering.
- Bare building coordinates worked live at `109,134`. The existing colony cooler at `115,161` was read at -9°C, changed to -10°C, read back, and restored to -9°C before the final reload.
- `get_cells_plus` accepts `minX/minZ/maxX/maxZ` and refuses conflicting coordinate shapes. A live one-cell request succeeded with no unknown arguments.
- Hostile pawn filters preserve the requested scope and reject contradictory nonzero-hostile-count/empty-list replies rather than printing an all-clear. The loaded map had no hostiles, so the original disappearing-raccoon comparison is covered by offline checks rather than an identical live fixture.
- PLAYBOOK now specifies `list_selected_gizmos` row `id` → `execute_gizmo {gizmoId: id}`; `targetId` belongs to UI targets. Bed ownership uses the direct building-config path.

## Reloads and the automatic watch rotation

- `home/get_time` exposes a runtime `sessionId` that changes on every Game load, including a same-tick reload. Python clock observations keep a generation and detect tick rollback for older companions. Reloads clear letter-dedupe, letter-age, and old run-condition observations.
- Scout and Lookout reports carry their captured generation/tick/time. Unknown-generation, expired, future-tick, and pre-reload reports are withheld or discarded. The old Hands report is not seeded into a new loaded session. Lookout headers use the game's own calendar date instead of inventing a colony day from raw ticks.
- `stream.py hands-start` starts a hidden, detached singleton `rota_service`. The service schedules both teams; the Core does not dispatch or acknowledge scouts. Scout readers alternate Claude/Sol; Lookout readers retain Luna/Sol/Sol/Luna.
- `stream.py human-check` collects report-only output. `rota.py tick` is a compatibility alias for report delivery, not a second scheduler. `stream.py reset` or `rota_service.py stop` stops the scheduler. Process locking prevents duplicate schedulers; Windows liveness probes do not send process signals.
- The harness runs a fixed set of data readers and passes their captured output to scouts over stdin. Scouts no longer depend on permission to execute Python during inference. Failed reads and truncation remain explicit, and absent checks must be reported as `NOT CHECKED`. This uses the CLI's documented [non-interactive input path](https://learn.chatgpt.com/docs/non-interactive-mode), with the installed CLI help used to verify its flags.
- **Live scout proof:** Sol processed a 19,487-byte captured packet and returned a six-line health/needs/gear report in 19 seconds without the former execution-policy failure. Missing bed-owner/trait checks were explicitly reported. Automatic alternation and service lifecycle were tested with mocks; a full alternating live stream was not run.
- **Live reload proof:** after reloading day 55, both old watcher mailboxes were rejected instead of delivered as current.

## Validation and installed artifact

- 146 instrument tests and 27 companion tests passed (**173 total**).
- Final companion Release build: zero warnings and zero errors.
- Final live contract run: **814 of 814 checks passed**, zero suspect promised keys.
- Installed DLL: `C:\Program Files (x86)\Steam\steamapps\common\RimWorld\BridgeTools\HomeBridge\HomeBridge.BridgeTools.dll`.
- SHA-256: `1E65BA6F659C7F1F413A8A65FC1A105A5FEB8F8BFB6FBBCF819CD2343A3CEBD9`.
- Previous installed DLL preserved at `companion/artifacts/installed-backup-20260905/HomeBridge.BridgeTools.dll.pre-stream-safety`.
- Detailed local evidence: `scratchpad/stream-safety-instruments-tests.log`, `stream-safety-companion-tests.log`, `stream-safety-live-contract.log`, `stream-safety-live-final.json`, and `save-hashes-bugfix-20260905.json`.

23 resolved or clarified bug-list entries were moved out of the open list. Their original text is preserved in `scratchpad/bugs-resolved-stream-safety-20260905.md`. `BUGS.md` retains unresolved historical issues, including vanished pemmican, the original placement mismatch, the reported pause/draft contradiction, moving-animal designation races, butcher-filter observations, and live fixtures still needed for trade and ground tending.
