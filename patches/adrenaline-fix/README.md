# Adrenaline Threat-Power Fix

A one-patch RimWorld mod that stops `[XND] Adrenaline! (Continued)` (`Mlie.XNDAdrenaline`, workshop
`2037728879`, v1.6.3) throwing `InvalidOperationException: Sequence contains no elements` every tick
during combat. Written 2026-09-10 for M's `Common Sky Compact` save.

## The bug

`AdrenalineUtility.EffectiveCombatPower` looks up the player's richest home map through
`worldObjects.Settlements`, which only ever contains `Settlement` world objects, and calls
`GenCollection.MaxBy` on the result without an empty guard. On a player home parented to anything
else — that save's map hangs off a `ClaimableSite`, and the origin colony's `Settlement` was
abandoned on gravship takeoff — the sequence is empty and it throws.

It throws for the *perceiving* pawn, so it is independent of the threat: every colonist fails, every
time. And because it throws inside `Hediff_AdrenalineRush.Tick()`, `Pawn_HealthTracker.HealthTick`
removes the hediff and `HediffGiver_Adrenaline` re-adds it, so every exception reads
`ticksSinceCreation=0` and the mod does nothing at all on such a map. 188 exceptions across 15
colonists in the fight that prompted this.

Full write-up, including the upstream report, is in `UPSTREAM-ISSUE.md`.

## What the patch does

Harmony prefix on `EffectiveCombatPower` that reproduces the colonist branch exactly, except it finds
the map through `Find.Maps` — which sees every loaded player-home map whatever its parent. Identical
result on an ordinary surface colony. Non-colonist pawns, turrets and the `NotImplementedException`
fallthrough are left to the original method.

Plus a finalizer safety net: any future exception out of that method is swallowed, a defensible
number substituted, and one warning logged per session, rather than stripping the hediff. Adrenaline
has shipped three crashes of this shape now (despawned pawns 1.6.1, unnatural corpses 1.6.3, this).

Also caches the wealth lookup for 60 ticks. Not for the wealth itself — `WealthWatcher` already has a
5000-tick floor — but because `Map.IsPlayerHome` falls through to `GravshipUtility.PlayerHasGravEngine`
on non-home maps, which can walk every thing on the map, and Adrenaline calls this once per perceived
threat per colonist.

## Build

```
cd src
dotnet build
```

Outputs to `mod\1.6\Assemblies\AdrenalineFix.dll`. Everything needed is in the local NuGet cache
(net472 reference assemblies, `Lib.Harmony.Ref`), so it builds offline. Same shape as
`rimworld\companion\src\HomeBridge.BridgeTools.csproj`; override `RimWorldManagedDir` on the command
line if RimWorld moves.

## Install

Copy `mod\` to `<RimWorld>\Mods\AdrenalineFix\` and add `home.adrenalinefix` to `<activeMods>` in
`ModsConfig.xml`, immediately after `mlie.xndadrenaline`. Requires a game restart; a new mod cannot
hot-load.

Installed 2026-09-10 to
`C:\Program Files (x86)\Steam\steamapps\common\RimWorld\Mods\AdrenalineFix`, enabled at load-order
position 20 of 177. The pre-edit mod list is backed up beside the original as
`ModsConfig.xml.bak-20260910-093053`.

## Uninstall

Delete the folder and remove the `<li>` from `ModsConfig.xml`. It adds no defs and writes nothing to
the save, so removing it leaves the save exactly as it was.

## Verifying it worked

On next launch the log should carry `[AdrenalineFix] Patched Adrenaline.AdrenalineUtility.EffectiveCombatPower.`
and no further `Sequence contains no elements`. If `[AdrenalineFix] Adrenaline.EffectiveCombatPower
threw for ...` ever appears, the safety net caught a *new* variant — worth reading.
