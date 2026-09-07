# Combat controller review, 2026-09-04

M asked for as many eyes as possible on Sol's combat system before she
plays with it. Eight Opus 5 reviewers read it in parallel, each with one lens
(C# companion; session ledger; move and advance; equip and attack; camera and
threads; tests and docs; architecture; adversarial scenario walkthroughs),
then Fable 5.1 read the core code and fixed what could wreck a session that
night. Read-only until the fix pass; no game was launched for any of it. All
64 offline unit tests passed before and after, plus the offline contract test.
The full lane reports are in Fable's session scratchpad, not the repo; this
file is the durable record.

Sol: this is your code and these are edits to your uncommitted work. Every
change is marked with a 2026-09-04 comment at the site. Push back on any of it.

## What every lane said was well done

The write-ahead ledger: mkstemp, fsync, `os.replace`; intent, then mutate,
then confirm, in every drafting path; reload after callbacks. Two lanes
attacked it at four kill points and could not tear it or make it forget a
pawn. Every order is verified against a real game job, never a click. Reads
never run time. The Harmony hook's arm/disarm state machine is textbook, costs
one volatile read when off, and refuses to run the clock if it cannot verify
its own patch (lane 1 decompiled Assembly-CSharp 1.6 to check the target:
`Thing.TakeDamage` is non-virtual, `Pawn` does not override it, one patch
catches every pawn). Moving the camera server-side was the right call. The
companion's human-control returns sit before the pause block. `end`'s hostile
gate, `--dry-run`, `release`, the `status.py` warning line. Refuse, don't
degrade, applied consistently.

## Fixed on 2026-09-04 (Fable)

Each of these was found independently by two or more lanes unless noted.

1. **`advance` trusted `stopReason` alone.** The companion also returns
   `success`, `error`, `pauseVerified`. On "PAUSE DID NOT TAKE", `cancelled`
   or `session_changed` the game kept running while the operator was told it
   had stopped, and `session_changed` advanced the watermark. Now: any of
   those, or `success:false`, pauses (verified), records nothing, and refuses
   with the companion's own `error` text and a pause verdict. `notes` are
   printed. `combat.py:advance`.
2. **A person's pause was lifted by the next `advance`.** run.py does the
   same across calls, but combat pulses are chained fast. Now the ledger's
   `lastStop` is read: after `external_pause`, `speed_changed` or
   `force_paused` the next `advance` refuses until `--resume`.
3. **A combat pulse never watched for new hostiles** (`watchHostiles:False`).
   Now `watchHostiles:True, ignoreCurrentHostiles:True`, which the companion
   already baselines per call, so it is edge-triggered: arrivals stop the
   clock, the ones already here do not. The old comment said the generic
   watch was level-triggered; with `ignoreCurrentHostiles` it is not.
4. **Injury thresholds were in the wrong unit.** `Hediff_Injury.Severity` is
   hit points (floored at 1), and the C# clamped the threshold to 0..1, so
   the strictest setting still fired on every scratch and the coalescing was
   unreachable. C# clamp is now 0..10000 with the unit in the parameter doc;
   Python sends 8 HP / 0.1 bleed per day, cumulative against the pulse's
   entry baseline.
5. **The "injured while fleeing" latch cleared itself** on the next `advance`
   whenever the injury had ended the escape (`_reconcile_flee_orders` popped
   `pendingDecisions` on both branches). Lane 8 reproduced 300 ticks running
   with the pawn on the ground. Now only arrival clears it; a Goto that ended
   short of the destination latches "flee interrupted" if nothing more
   specific is already there.
6. **A colonist who died wedged `end` and `release` forever** (absent from
   `home/status` colonists means "unresolved", and `--force` only bypassed
   the hostile gate). The five `combat-session.stale-*.json` files in
   `state\` are what escaping that looked like. Now `--force` drops the
   obligation for a pawn who is no longer a colonist, and `end --force` on a
   STALE ledger discards it without touching the game. `begin` refuses a
   stale ledger with those instructions instead of silently returning it.
7. **A mistyped pawn name latched an unclearable decision** keyed by the raw
   token. A token that resolves to nobody mutated nothing, so it now latches
   nothing.
8. **No exit path in `advance` (or attack/equip) paused on exception or
   Ctrl+C**, and the move/flee recovery pause was unverified while printing
   "GAME PAUSED". Now every mutating command ends with a verified pause
   attempt and prints "GAME PAUSED" or "COULD NOT VERIFY PAUSE, press Space".
   `attack` failures latch a decision like move/flee. `KeyboardInterrupt` is
   caught.
9. **`attack` drafted before the modal check**, leaving a colonist drafted and
   idle on refusal. The check now runs first.
10. **`advance` accepted any duration**; the companion clamps at 600 s and
    says so only in `notes`. Now refuses above 20 s (PLAYBOOK's rule); equip's
    default wait is 15 s, not 30.
11. **Two documented paths could never succeed** (lane 4): `--weapon-id`
    compared a gear `thingId` that `GearRow` never emitted, and
    `issue_melee_attack(a, t)` without `target_state` always refused because
    `home/status` hostile rows carry no `hostile` key. `GearRow` now emits
    `thingId`; `combat_actions._pawn` marks rows from `threats.hostiles`.
    Both passed green because the test fakes invented those fields.
12. **The hook armed for every combatant while anyone fled**, so a pawn
    ordered into melee latched "injured while fleeing" on the first scratch.
    Now the hook arms for the fleeing pawns only; when nobody flees the
    thresholded poll covers every combatant. Gap: the companion has one
    `injuryPawnIds` set for both, so hook-for-fleeing plus poll-for-fighters
    cannot run in the same pulse yet. A second ID parameter would fix it.
13. **Camera (C#).** A throw in `UpdateCombatCamera` aborted the guarded pulse
    as `stopReason:error`; it is now caught and counted (`errors` in the
    payload). The camera moved before the force-paused / requireRunningAtEntry
    refusals; it now moves only after them. Drift detection compared an
    `IntVec3` that includes screen shake and edge clamping with a 0.15
    tolerance, so shake or a map-edge fight registered as manual input and
    disabled directing; the tolerance is now two cells and 0.5 root size. The
    frame centre is rounded to whole cells so an unchanged fight is not
    reframed every interval. The nobody-to-frame early return now honours the
    interval instead of rescanning every poll. `driver.config` null-checked.
14. **The Harmony prefix and postfix had no try/catch.** An exception in a
    patch surfaces as a `Log.Error`, which pauses the colony with nothing
    attributing it. Both now count and swallow (`hookErrors` in `Status()`).
15. **The installed DLL was a Debug build**, md5-identical to `obj\Debug`,
    while `obj\Release` was an hour stale. Rebuilt Release (840,704 bytes, 0
    warnings), backed up the Debug file to
    `artifacts\installed-backup-20260904\`, installed with the game closed.
16. Docs: PLAYBOOK's combat section now states the enforced 20 s cap,
    `--resume`, what `end --force` abandons, and not to raw-`set_draft` a
    session pawn; hands.md points at `end`/`release`; INSTALL.md's size line.

## Not fixed, for Sol to decide

Ordered by how much the reviewers cared.

- **Dead code that makes the system look more guarded than it is.**
  `watchMeleeThreats` and `watchPawnOrders` are hardcoded False and set
  nowhere else: roughly 160 C# lines, 5 parameters, and the two `advance`
  handler branches (about 38 lines, plus the only use of `snapshot_reader`
  and an extra `home/status` read) are unreachable, and two green tests
  assert the unreachable branch. `combat_camera.py` is 188 lines of which
  `participant_ids` (5 lines) is used; the Python `CombatCameraDirector` was
  superseded by the C# one, has drifted from it (`width/3.0` vs the real
  aspect term), and 6 of its 7 tests test the dead version. Either wire them
  or delete them before committing.
- **`play_until_event` is 40 positional parameters** hand-threaded through
  three signatures and two forwarding calls (about 150 lines). Lane 7 traced
  both forwards and they are correct today, but adjacent same-typed pairs are
  a transposition waiting to compile clean. Binding into `Watch` and
  `CombatCameraState` once would remove most of it.
- **Two injury detectors.** The polled `watchInjuries` re-walks hediffs 4x a
  second on the main thread and is up to 250 ms late; the hook is exact. A
  threshold on the hook plus a second pawn-ID parameter would retire the poll
  and close the gap in item 12.
- **Per-poll waste.** In combat mode `Snapshot()` computes `IsHostile` and
  `NearestColonist` for every non-colonist pawn and discards both when the
  hostile watch's consumers are off; on a busy map that is hundreds of
  distance computations 4x a second on the game thread during a raid.
  `home/status` (8 KB) is read 3x per `attack` and 5x per `equip` because
  `combat_actions._pawn` takes no snapshot; `issue_goto` doubled the camera
  calls and added 1.0 s of unconditional sleep, so the live-test doc's 491 ms
  move is no longer possible.
- **Verification depth.** Move checks that a Goto job exists, not its
  destination; the companion already returns `order.targetA` as `cell:x,z`
  and combat stores it unread. Melee checks the job def, not the target, so
  a stale AttackMelee on raider A verifies an order at raider B. Weapon label
  matching is an unanchored substring.
- **Camera between pulses.** `Expected*` is reseeded from the live camera at
  every call, so a pan while the game is paused (the natural moment) is
  stolen back on the next `advance` with no grace. Round-trip the expected
  position through the ledger the way `cameraLastFrameUnixMs` already is.
  `advance` also never claims camlock, so `rota.py`'s Lookout can glide the
  camera home mid-raid. Adding `--no-camera` is house convention on five
  other tools.
- **Ledger identity.** A save loaded from inside the game is invisible unless
  the tick regresses; `session.json` is only rewritten by `setup.py`, the
  running RimWorld pid is never compared, and `home/status` `mapName` is
  unused. `_ledger_pawn_id` reverse-engineers the digit suffix; the C# could
  echo the full `GetUniqueLoadID` string instead.
- **Tests and docs.** Every `advance` fixture returns `ticksGame`, a key the
  companion never emits (`endTick` is the live branch and has no coverage).
  The C# source test is a regex change-detector; `ParsePawnIds`,
  `MeaningfulInjuryChange`, `SameOrder`, `PawnDistance` and the camera maths
  are pure functions with no coverage. INSTALL.md documents none of the 12
  `combatCamera*` parameters. COMBAT-DESIGN's header still says `advance`
  "remains design only". The live-test doc predates `flee`, the hook, the
  camera and `pendingDecisions` by about an hour; nothing has been live-tested
  against a real raid, a killed colonist, a human pause mid-pulse, a bridge
  death, or a mid-session reload. Fable's earlier review items that were not
  taken up are not acknowledged anywhere (deadline/capacity injury
  thresholds, overlay narration, contract-test coverage of the new watches);
  the Harmony patch Fable advised against was probably right, but say so.

## What tonight's changes were NOT checked against

A live game. The fixes are unit-tested (72 cases across the suites, all
green) and the DLL compiles with warnings as errors, but the first real pulse
after this is the test. If a combat command misbehaves, the pre-fix DLL is one
copy away in `artifacts\installed-backup-20260904\`, and `git diff` on
`combat.py` shows every Python change.
