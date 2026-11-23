# Draft GitHub issue — emipa606/XNDAdrenaline

**Not posted.** Draft for M's review. Nothing here is signed in her name.

---

**Title:** `EffectiveCombatPower` throws "Sequence contains no elements" on any player home that isn't a Settlement world object (Odyssey sites/asteroids, pocket maps, quest sites)

---

### Summary

`AdrenalineUtility.EffectiveCombatPower` finds the player's richest home map by searching
`worldObjects.Settlements`. `WorldObjectsHolder.Settlements` contains only `Settlement`-typed world
objects, so any player home hanging off a different `MapParent` is invisible to it — and
`GenCollection.MaxBy` has no empty-source guard:

```csharp
Map map = Current.Game.World.worldObjects.Settlements
    .Where(s => s.HasMap && s.Map.IsPlayerHome)
    .MaxBy(s => s.Map.PlayerWealthForStoryteller).Map;
```

`InvalidOperationException: Sequence contains no elements`.

This is thrown for the **perceiving** pawn — it's the denominator of
`t.EffectiveCombatPower() / pawn.EffectiveCombatPower()` in `PerceivedThreatSignificanceFor` — so it
doesn't depend on what the threat is. On an affected map it fires for **every colonist, every time**.

Because the throw propagates out of `Hediff_AdrenalineRush.Tick()`, `Pawn_HealthTracker.HealthTick`
catches it and **removes the hediff**, which `HediffGiver_Adrenaline` then re-adds, which throws
again. Every exception reads `ticksSinceCreation=0`. The practical effect is that adrenaline silently
does nothing at all on those maps, on top of the log spam.

### Affected states

Any loaded map where `Map.IsPlayerHome` is true but no `Settlement` world object holds a player-home
map. In 1.6 + Odyssey that includes:

- `ClaimableSite` / `ClaimableSpaceSite` (`Site`) — **this is what triggered it for us**
- `BasicAsteroidMapParent` / `ResourceAsteroidMapParent` / `SpaceMapParent`
- `PocketMapParent` with the grav engine aboard
- `EscapeShip`, `AbandonedArchotechStructures`

Odyssey makes it easy to reach: `WorldComponent_GravshipController.TakeoffEnded` calls
`GravshipUtility.AbandonMap` unless a grav anchor was left behind, so launching the gravship removes
the origin `Settlement` outright; and `GravshipUtility.ArriveNewMap` only calls
`SettleUtility.AddNewHome` when the destination tile's layer default *is* a settlement world object —
landing on a site or asteroid takes the `else` branch and just re-factions the existing parent.
So both halves of the condition are satisfied at once.

Note `Map.IsPlayerHome` returns true on such maps for reasons that have nothing to do with
`Settlements`: `wasSpawnedViaGravShipLanding`, or parent faction + `canBePlayerHome`, or
`GravshipUtility.PlayerHasGravEngine`.

### Reproduction

RimWorld 1.6.4871, XNDAdrenaline 1.6.3, Odyssey active. Launch a gravship without leaving a grav
anchor, land on a claimable site or asteroid, get into any fight. Every colonist spams the stack
below once per tick.

```
Exception ticking hediff (Adrenaline ticksSinceCreation=0) for pawn <name>. Removing hediff...
System.InvalidOperationException: Sequence contains no elements
  at Verse.GenCollection.MaxBy[TSource,TKey] (...)
  at Adrenaline.AdrenalineUtility.EffectiveCombatPower (Verse.Thing t) [0x0005f]
  at Adrenaline.AdrenalineUtility.PerceivedThreatSignificanceFor (Verse.Thing t, Verse.Pawn pawn)
  at Adrenaline.Hediff_AdrenalineRush.<UpdateTotalThreatSignificance>b__14_0 (Verse.Thing t)
  at System.Linq.Enumerable.Sum[TSource] (...)
  at Adrenaline.Hediff_AdrenalineRush.UpdateTotalThreatSignificance ()
  at Adrenaline.Hediff_AdrenalineRush.Tick ()
  at Verse.Pawn_HealthTracker.HealthTick ()
```

188 occurrences across 15 colonists in one fight here.

### Suggested fix

Ask `Find.Maps` instead of the settlement list — it sees every loaded player-home map whatever its
parent, and gives an identical result on an ordinary surface colony:

```diff
     if (pawn.IsColonist)
     {
-        Map map = Current.Game.World.worldObjects.Settlements
-            .Where(s => s.HasMap && s.Map.IsPlayerHome)
-            .MaxBy(s => s.Map.PlayerWealthForStoryteller).Map;
-        num = Mathf.Max(pointsPerColonistByWealthCurve.Evaluate(map.PlayerWealthForStoryteller),
-                        pawn.kindDef.combatPower);
+        float wealth = 0f;
+        List<Map> maps = Find.Maps;
+        for (int i = 0; i < maps.Count; i++)
+        {
+            if (maps[i].IsPlayerHome)
+            {
+                float w = maps[i].PlayerWealthForStoryteller;
+                if (w > wealth) wealth = w;
+            }
+        }
+        num = Mathf.Max(pointsPerColonistByWealthCurve.Evaluate(wealth), pawn.kindDef.combatPower);
     }
```

Taking `max` of the wealth is equivalent to `MaxBy` then reading that map's wealth, and zero is a safe
floor: the curve is flat at 15 below 10k wealth, and `Mathf.Max` against a colonist's `combatPower`
of 30 dominates until roughly 56,800 wealth, so the no-home case is already indistinguishable from a
poor colony.

`Find.WorldObjects.MapParents` would also work, but `Find.Maps` is simpler and covers
`wasSpawnedViaGravShipLanding` and the grav-engine clause for free.

### Aside

The same method throws `NotImplementedException` for any `Thing` that is neither `Pawn` nor
`Building_Turret`, and `PerceivedThreatSignificanceFor` throws a bare `NotImplementedException` if an
animal perceives a non-`Pawn` threat. Both are currently unreachable because `isPerceivedThreatBy`
filters to those two types, but given this bug class has now bitten three times (despawned pawns in
1.6.1, `UnnaturalCorpse_Human` in 1.6.3, this), it may be worth having `Hediff_AdrenalineRush.Tick`
fail soft rather than letting anything in the scoring path strip the hediff.
