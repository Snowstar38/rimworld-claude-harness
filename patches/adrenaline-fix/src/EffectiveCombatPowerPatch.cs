using System;
using System.Collections.Generic;
using System.Reflection;
using HarmonyLib;
using RimWorld;
using UnityEngine;
using Verse;

namespace AdrenalineFix
{
    /// <summary>
    /// Installs the patch at startup. Does nothing at all if Adrenaline is not loaded, so this mod
    /// is harmless to leave enabled after unsubscribing from Adrenaline.
    /// </summary>
    [StaticConstructorOnStartup]
    internal static class AdrenalineFixStartup
    {
        internal const string HarmonyId = "home.adrenalinefix";
        private const string UtilityTypeName = "Adrenaline.AdrenalineUtility";

        static AdrenalineFixStartup()
        {
            Type utility = AccessTools.TypeByName(UtilityTypeName);
            if (utility == null)
            {
                // Adrenaline isn't installed. Nothing to fix; stay silent.
                return;
            }

            MethodInfo target = AccessTools.Method(utility, "EffectiveCombatPower", new[] { typeof(Thing) });
            if (target == null)
            {
                Log.Warning("[AdrenalineFix] Found " + UtilityTypeName + " but not EffectiveCombatPower(Thing). "
                            + "Adrenaline has changed shape; this fix is doing nothing. Check whether it is still needed.");
                return;
            }

            PlayerHomeWealth.ResolveCurve(utility);

            new Harmony(HarmonyId).Patch(
                target,
                prefix: new HarmonyMethod(typeof(EffectiveCombatPowerPatch), nameof(EffectiveCombatPowerPatch.Prefix)),
                finalizer: new HarmonyMethod(typeof(EffectiveCombatPowerPatch), nameof(EffectiveCombatPowerPatch.Finalizer)));

            Log.Message("[AdrenalineFix] Patched Adrenaline.AdrenalineUtility.EffectiveCombatPower.");
        }
    }

    /// <summary>
    /// Adrenaline scores a colonist's own combat power against the wealth of the player's richest
    /// home map, and looks that map up as
    ///
    ///     Current.Game.World.worldObjects.Settlements
    ///         .Where(s =&gt; s.HasMap &amp;&amp; s.Map.IsPlayerHome)
    ///         .MaxBy(s =&gt; s.Map.PlayerWealthForStoryteller)
    ///
    /// Verse.GenCollection.MaxBy has no empty guard, so this throws "Sequence contains no elements"
    /// whenever no *Settlement world object* holds a loaded player-home map. WorldObjectsHolder
    /// .Settlements only ever returns Settlement, so a player home hanging off any other MapParent --
    /// an Odyssey asteroid or space map, a gravship in transit, a pocket map, a quest site -- is
    /// invisible to it even while the colonists are standing on it.
    ///
    /// It throws for the *perceiving* pawn, so it does not depend on what the threat is: on such a
    /// map every colonist fails, every time. And because the throw happens inside
    /// Hediff_AdrenalineRush.Tick(), RimWorld's response is to rip the hediff off the pawn -- so the
    /// mod silently stops working in exactly the fights it exists for, once per pawn per tick, with
    /// a wall of red in the log.
    ///
    /// The prefix reproduces the colonist branch verbatim except that it finds the map through
    /// Find.Maps, which sees every loaded player-home map regardless of what parents it. Everything
    /// else -- non-colonist pawns, turrets, the NotImplementedException fallthrough -- is left to
    /// the original method.
    /// </summary>
    internal static class EffectiveCombatPowerPatch
    {
        private static bool loggedFallback;

        internal static bool Prefix(Thing t, ref float __result)
        {
            Pawn pawn = t as Pawn;
            if (pawn == null || !pawn.IsColonist)
            {
                return true; // Not the branch that breaks -- run the original.
            }

            float basePower = Mathf.Max(
                PlayerHomeWealth.Curve.Evaluate(PlayerHomeWealth.Richest()),
                pawn.kindDef.combatPower);

            // Floored for the same reason as Fallback: PerceivedThreatSignificanceFor divides by
            // this, and SummaryHealthPercent is a hard 0 for a dead pawn, which would give NaN
            // severity rather than a crash. Unreachable in practice -- a corpse is not a perceived
            // threat and the perceiving pawn is alive -- but free.
            __result = Mathf.Max(basePower * HealthFactor(pawn) * BodySizeFactor(pawn), 1f);
            return false;
        }

        /// <summary>
        /// Safety net. Adrenaline has shipped three crashes of this shape -- despawned pawns in
        /// 1.6.1, Anomaly unnatural corpses in 1.6.3, and this one -- each fixed upstream by
        /// special-casing whatever triggered it. Whatever the next one turns out to be, it must not
        /// be allowed to strip the hediff and flood the log: swallow it, return a defensible number,
        /// and say so once per session.
        /// </summary>
        internal static Exception Finalizer(Thing t, ref float __result, Exception __exception)
        {
            if (__exception == null)
            {
                return null;
            }

            __result = Fallback(t);

            if (!loggedFallback)
            {
                loggedFallback = true;
                Log.Warning("[AdrenalineFix] Adrenaline.EffectiveCombatPower threw for " + ThingLabel(t)
                            + "; substituting " + __result.ToString("0.##")
                            + " and suppressing further reports this session. This is a bug in Adrenaline, "
                            + "not a broken save. Original exception:\n" + __exception);
            }

            return null; // Suppressed -- the hediff keeps ticking.
        }

        private static float Fallback(Thing t)
        {
            if (t is Pawn pawn)
            {
                // Never zero: PerceivedThreatSignificanceFor divides by this.
                return Mathf.Max(pawn.kindDef.combatPower * HealthFactor(pawn) * BodySizeFactor(pawn), 1f);
            }

            if (t is Building_Turret turret)
            {
                return Mathf.Max(turret.def.GetStatValueAbstract(StatDefOf.MarketValue) / 6f, 1f);
            }

            return 1f;
        }

        private static float HealthFactor(Pawn pawn)
        {
            return pawn.health?.summaryHealth?.SummaryHealthPercent ?? 1f;
        }

        private static float BodySizeFactor(Pawn pawn)
        {
            return pawn.ageTracker?.CurLifeStage?.bodySizeFactor ?? 1f;
        }

        private static string ThingLabel(Thing t)
        {
            if (t == null)
            {
                return "a null thing";
            }

            try
            {
                return t.LabelShortCap + " (" + t.GetType().Name + ")";
            }
            catch
            {
                return t.GetType().Name;
            }
        }
    }

    /// <summary>
    /// The wealth lookup Adrenaline should have been doing, plus a short cache.
    /// </summary>
    internal static class PlayerHomeWealth
    {
        /// <summary>
        /// Adrenaline's own curve, read off its private static field so it tracks any future
        /// rebalance upstream. This literal copy is only used if that field ever goes away.
        /// </summary>
        private static SimpleCurve curve = new SimpleCurve
        {
            new CurvePoint(0f, 15f),
            new CurvePoint(10000f, 15f),
            new CurvePoint(400000f, 140f),
            new CurvePoint(1000000f, 200f)
        };

        // EffectiveCombatPower runs once per perceived threat per colonist, and
        // PlayerWealthForStoryteller is not cheap. A second of staleness on a wealth curve that is
        // flat below 10k costs nothing in gameplay and a good deal in tick time.
        private const int CacheTicks = 60;

        private static Game cachedGame;
        private static int cachedTick = -1;
        private static float cachedWealth;

        internal static SimpleCurve Curve => curve;

        internal static void ResolveCurve(Type utility)
        {
            if (AccessTools.Field(utility, "pointsPerColonistByWealthCurve")?.GetValue(null) is SimpleCurve found)
            {
                curve = found;
            }
        }

        /// <summary>
        /// Wealth of the richest loaded player-home map, whatever parents it, or zero if the player
        /// has no home map at all. Never throws.
        ///
        /// Zero is the right answer for the no-home case rather than something pawn-specific: the
        /// caller takes Mathf.Max against a colonist's combatPower of 30, and Adrenaline's curve
        /// only clears 30 above roughly 56,800 wealth, so every input below that is already
        /// indistinguishable. Reading a non-home map's wealth to get there would mean an uncached
        /// walk over every player pawn's gear, once per threat per colonist, to compute a number
        /// that cannot change the result.
        /// </summary>
        internal static float Richest()
        {
            Game game = Current.Game;
            if (game == null)
            {
                return 0f;
            }

            // Find.TickManager dereferences Current.Game unconditionally, so the guard has to be
            // here rather than a null-conditional on the Find accessor.
            int tick = game.tickManager?.TicksGame ?? 0;
            if (cachedGame == game && cachedTick >= 0 && tick >= cachedTick && tick - cachedTick < CacheTicks)
            {
                return cachedWealth;
            }

            float best = 0f;
            List<Map> maps = game.Maps;
            if (maps != null)
            {
                for (int i = 0; i < maps.Count; i++)
                {
                    Map map = maps[i];
                    if (map == null || !map.IsPlayerHome)
                    {
                        continue;
                    }

                    float wealth = map.PlayerWealthForStoryteller;
                    if (wealth > best)
                    {
                        best = wealth;
                    }
                }
            }

            cachedGame = game;
            cachedTick = tick;
            cachedWealth = best;
            return best;
        }
    }
}
