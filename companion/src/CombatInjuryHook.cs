using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Threading;
using HarmonyLib;
using Verse;

namespace HomeBridge.BridgeTools
{
    /// <summary>
    /// A normally-disarmed damage edge for short, explicit combat waits.  The
    /// Harmony patch always exists, but its ordinary-game path is one volatile
    /// read and return.  No ticking or pawn scanning is involved.
    /// </summary>
    internal static class CombatInjuryHook
    {
        private static readonly object Gate = new object();
        private static ArmState _armed;
        private static InjuryEvent _first;
        private static int _patched;
        private static int _prefixCalls;
        private static int _watchedCalls;
        private static int _injuryChanges;
        private static int _hookErrors;
        private static MethodInfo _target;
        private static string _patchError;

        internal static void Arm(IEnumerable<int> pawnIds)
        {
            EnsurePatched();
            var ids = new HashSet<int>(pawnIds ?? Enumerable.Empty<int>());
            lock (Gate)
            {
                _first = null;
                Volatile.Write(ref _armed, ids.Count == 0 ? null : new ArmState(ids));
            }
        }

        internal static void Disarm()
        {
            lock (Gate)
            {
                Volatile.Write(ref _armed, null);
                _first = null;
            }
        }

        internal static InjuryEvent Peek()
        {
            lock (Gate) return _first;
        }

        internal static Dictionary<string, object> Status()
        {
            var target = _target;
            var info = target != null ? Harmony.GetPatchInfo(target) : null;
            var owners = info == null ? new List<string>() : info.Owners.OrderBy(x => x).ToList();
            return new Dictionary<string, object>
            {
                { "armed", Volatile.Read(ref _armed) != null },
                { "target", target != null ? target.DeclaringType.FullName + "." + target : null },
                { "patchInstalled", owners.Contains("homebridge.combat-injury-edge") },
                { "patchOwners", owners }, { "patchError", _patchError },
                { "prefixCalls", Volatile.Read(ref _prefixCalls) },
                { "watchedCalls", Volatile.Read(ref _watchedCalls) },
                { "injuryChanges", Volatile.Read(ref _injuryChanges) },
                { "hookErrors", Volatile.Read(ref _hookErrors) }
            };
        }

        internal static bool IsInstalled()
        {
            var target = _target;
            var info = target != null ? Harmony.GetPatchInfo(target) : null;
            return info != null && info.Owners.Contains("homebridge.combat-injury-edge");
        }

        private static void EnsurePatched()
        {
            if (Interlocked.CompareExchange(ref _patched, 1, 0) != 0) return;
            try
            {
                // Pawn_HealthTracker.PostApplyDamage is too late to take the
                // before snapshot: DamageWorker.Apply has already added the
                // wound when that method is entered. Thing.TakeDamage brackets
                // PreApplyDamage, DamageWorker.Apply and PostApplyDamage.
                _target = AccessTools.Method(typeof(Thing), "TakeDamage",
                    new[] { typeof(DamageInfo) });
                if (_target == null)
                    throw new MissingMethodException(typeof(Thing).FullName,
                        "TakeDamage(DamageInfo)");
                new Harmony("homebridge.combat-injury-edge").Patch(
                    _target,
                    prefix: new HarmonyMethod(typeof(TakeDamagePatch), nameof(TakeDamagePatch.Prefix)),
                    postfix: new HarmonyMethod(typeof(TakeDamagePatch), nameof(TakeDamagePatch.Postfix)));
                var info = Harmony.GetPatchInfo(_target);
                if (info == null || !info.Owners.Contains("homebridge.combat-injury-edge"))
                    throw new InvalidOperationException("Harmony did not report the injury patch owner after Patch().");
                _patchError = null;
            }
            catch (Exception ex)
            {
                _patchError = ex.GetType().Name + ": " + ex.Message;
                Interlocked.Exchange(ref _patched, 0);
                throw;
            }
        }

        private static InjurySnapshot Snapshot(Pawn_HealthTracker tracker)
        {
            var injuries = tracker?.hediffSet?.hediffs?.OfType<Hediff_Injury>();
            return injuries == null
                ? new InjurySnapshot()
                : new InjurySnapshot
                {
                    Count = injuries.Count(),
                    Severity = injuries.Sum(x => x.Severity),
                    BleedRate = injuries.Sum(x => x.BleedRate)
                };
        }

        private static void OnApplied(Pawn pawn, InjurySnapshot before, DamageInfo damage)
        {
            var armed = before.Armed;
            Interlocked.Increment(ref _watchedCalls);
            if (pawn == null || !armed.PawnIds.Contains(pawn.thingIDNumber)) return;

            var after = Snapshot(pawn.health);
            if (after.Count <= before.Count && after.Severity <= before.Severity + 0.0001f) return;
            Interlocked.Increment(ref _injuryChanges);

            lock (Gate)
            {
                if (!ReferenceEquals(armed, _armed) || _first != null) return;
                var instigator = damage.Instigator;
                _first = new InjuryEvent
                {
                    PawnId = pawn.thingIDNumber,
                    PawnName = pawn.LabelShortCap,
                    Tick = Find.TickManager != null ? Find.TickManager.TicksGame : 0,
                    InjuryCountBefore = before.Count,
                    InjuryCountAfter = after.Count,
                    SeverityBefore = before.Severity,
                    SeverityAfter = after.Severity,
                    BleedRateBefore = before.BleedRate,
                    BleedRateAfter = after.BleedRate,
                    DamageDef = damage.Def != null ? damage.Def.defName : null,
                    DamageAmount = damage.Amount,
                    InstigatorId = instigator != null ? (int?)instigator.thingIDNumber : null,
                    InstigatorName = instigator != null ? instigator.LabelCap : null
                };
                var tm = Find.TickManager;
                if (tm != null && tm.CurTimeSpeed != TimeSpeed.Paused) tm.Pause();
            }
        }

        private sealed class TakeDamagePatch
        {
            // An exception inside a Harmony patch surfaces as a RimWorld
            // Log.Error, which pauses the colony with nothing attributing
            // it. This hook must never be that: it counts and swallows.
            internal static void Prefix(Thing __instance, out InjurySnapshot __state)
            {
                __state = default(InjurySnapshot);
                var armed = Volatile.Read(ref _armed); // normal play: this read + return
                if (armed == null) return;
                try
                {
                    var pawn = __instance as Pawn;
                    if (pawn == null || !armed.PawnIds.Contains(pawn.thingIDNumber)) return;
                    Interlocked.Increment(ref _prefixCalls);
                    __state = Snapshot(pawn.health);
                    __state.Armed = armed;
                    __state.Pawn = pawn;
                }
                catch (Exception)
                {
                    __state = default(InjurySnapshot);
                    Interlocked.Increment(ref _hookErrors);
                }
            }

            internal static void Postfix(DamageInfo dinfo, InjurySnapshot __state)
            {
                if (__state.Armed == null) return;
                try { OnApplied(__state.Pawn, __state, dinfo); }
                catch (Exception) { Interlocked.Increment(ref _hookErrors); }
            }
        }

        private sealed class ArmState
        {
            internal readonly HashSet<int> PawnIds;
            internal ArmState(HashSet<int> pawnIds) { PawnIds = pawnIds; }
        }

        private struct InjurySnapshot
        {
            internal ArmState Armed;
            internal Pawn Pawn;
            internal int Count;
            internal float Severity;
            internal float BleedRate;
        }

        internal sealed class InjuryEvent
        {
            internal int PawnId;
            internal string PawnName;
            internal int Tick;
            internal int InjuryCountBefore;
            internal int InjuryCountAfter;
            internal float SeverityBefore;
            internal float SeverityAfter;
            internal float BleedRateBefore;
            internal float BleedRateAfter;
            internal string DamageDef;
            internal float DamageAmount;
            internal int? InstigatorId;
            internal string InstigatorName;

            internal Dictionary<string, object> ToPayload() => new Dictionary<string, object>
            {
                { "kind", "pawn_injury_hook" }, { "pawnId", PawnId }, { "pawnName", PawnName }, { "tick", Tick },
                { "injuryCountBefore", InjuryCountBefore }, { "injuryCountAfter", InjuryCountAfter },
                { "severityBefore", SeverityBefore }, { "severityAfter", SeverityAfter },
                { "severityDelta", SeverityAfter - SeverityBefore },
                { "bleedRateBefore", BleedRateBefore }, { "bleedRateAfter", BleedRateAfter },
                { "bleedRateDelta", BleedRateAfter - BleedRateBefore },
                { "damageDef", DamageDef }, { "damageAmount", DamageAmount },
                { "instigatorId", InstigatorId }, { "instigatorName", InstigatorName }
            };
        }
    }
}
