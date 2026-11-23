using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using HarmonyLib;
using RimBridgeServer.Sdk;
using RimWorld;
using Verse;

namespace HomeBridge.BridgeTools
{
    /// Persistent, main-thread play guard. The MCP call only changes or reads
    /// state; TickManagerUpdate owns monitoring and pausing after it returns.
    public sealed class HomeSupervisedPlayTools
    {
        private const string ToolName = "home/supervised_play";

        [Tool(ToolName, Title = "Supervised nonblocking play",
            Description = "Start, pause, renew or inspect a persistent in-game safety watcher. Start returns immediately; the watcher pauses independently on danger or lease expiry.")]
        [ToolResponse("unknownArguments", "array", "Every argument key the caller sent that this tool does not declare, sorted case-sensitively. Empty means the call was clean.", Always = true)]
        [ToolResponse("unknownArgumentsWarning", "string", "Present when unknown arguments were supplied or raw argument inspection was unavailable.", Nullable = true)]
        public async Task<object> SupervisedPlay(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            [ToolParameter(Description = "start, pause, speed, status, heartbeat, or events.")] string op,
            [ToolParameter(Description = "Caller identity used with epoch to reject stale control.", DefaultValue = "agent")] string owner = "agent",
            [ToolParameter(Description = "Epoch returned by start; required for heartbeat and pause.", DefaultValue = 0L)] long epoch = 0L,
            [ToolParameter(Description = "Renewable watchdog lease, clamped to 1000..30000 ms.", DefaultValue = 15000)] int leaseMs = 15000,
            [ToolParameter(Description = "Normal, Fast, Superfast or Ultrafast.", DefaultValue = "Superfast")] string speed = "Superfast",
            [ToolParameter(Description = "colony also stops on a serious new wound; combat records every ordinary wound and stops only at the configured health thresholds. Neither stops on a scratch, a social fight, or a pawn under cooldown or acknowledged.", DefaultValue = "colony")] string mode = "colony",
            [ToolParameter(Description = "Combat mode: stop when summary health drops this fraction from start, clamped 0.01..1.", DefaultValue = 0.15f)] float healthDropFraction = 0.15f,
            [ToolParameter(Description = "Combat mode: stop when summary health reaches this fraction, clamped 0.01..1.", DefaultValue = 0.5f)] float minHealthFraction = 0.5f,
            [ToolParameter(Description = "Accepted and ignored: alerts never stop play.", DefaultValue = "")] string ignoredAlertLabels = "",
            [ToolParameter(Description = "Stable IDs of explicitly acknowledged hostiles or nearby hunting predators, comma-separated.", DefaultValue = "")] string ignoredHostileIds = "",
            [ToolParameter(Description = "Stable IDs of acknowledged WILD PREDATORS, comma-separated, or the literal 'all' for every predator this epoch. Covers only the predator_hunting_ours category; a manhunter and a hostile faction still stop the clock.", DefaultValue = "")] string ignoredPredatorIds = "",
            [ToolParameter(Description = "Stable IDs of explicitly acknowledged downed colonists, comma-separated.", DefaultValue = "")] string ignoredDownedColonistIds = "",
            [ToolParameter(Description = "Stable IDs of colonists whose non-severe injuries are acknowledged; they never stop the clock in this epoch, comma-separated.", DefaultValue = "")] string ignoredInjuredColonistIds = "",
            [ToolParameter(Description = "After an injury or health stop, nothing about that colonist stops the clock again for this many real milliseconds, across restarts; downs and deaths are unaffected. Clamped 0..1800000; 0 disables.", DefaultValue = 180000)] int injuryStopCooldownMs = 180000,
            [ToolParameter(Description = "For events, return rows strictly after this cursor.", DefaultValue = 0L)] long afterCursor = 0L,
            [ToolParameter(Description = "For events, maximum rows, clamped to 1..128.", DefaultValue = 64)] int limit = 64)
        {
            return BridgeCommon.WithUnknownArguments(
                await SupervisedPlayCore(ctx, cancellationToken, op, owner, epoch,
                    leaseMs, speed, mode, healthDropFraction, minHealthFraction,
                    ignoredAlertLabels, ignoredHostileIds, ignoredPredatorIds,
                    ignoredDownedColonistIds,
                    ignoredInjuredColonistIds, injuryStopCooldownMs,
                    afterCursor, limit).ConfigureAwait(false),
                ctx, typeof(HomeSupervisedPlayTools), ToolName);
        }

        private async Task<object> SupervisedPlayCore(
            IRimBridgeContext ctx, CancellationToken cancellationToken,
            string op, string owner, long epoch, int leaseMs, string speed,
            string mode, float healthDropFraction, float minHealthFraction,
            string ignoredAlertLabels, string ignoredHostileIds,
            string ignoredPredatorIds,
            string ignoredDownedColonistIds, string ignoredInjuredColonistIds,
            int injuryStopCooldownMs, long afterCursor, int limit)
        {
            if (ctx == null || ctx.MainThread == null)
                return Fail("No main-thread dispatcher is available.");
            var action = (op ?? string.Empty).Trim().ToLowerInvariant();
            if (action == "status") return Supervisor.Status();
            if (action == "events") return Supervisor.Events(afterCursor, limit);
            if (action == "heartbeat") return Supervisor.Heartbeat(owner, epoch, leaseMs);
            if (action == "speed")
            {
                TimeSpeed changed;
                if (!Enum.TryParse(speed ?? string.Empty, true, out changed) || changed == TimeSpeed.Paused)
                    return Fail("Unknown play speed; use Normal, Fast, Superfast or Ultrafast.");
                return await ctx.MainThread.InvokeAsync(() => Supervisor.Speed(owner, epoch, changed),
                    cancellationToken).ConfigureAwait(false);
            }
            if (action == "start")
            {
                Supervisor.EnsurePatched();
                TimeSpeed requested;
                if (!Enum.TryParse(speed ?? string.Empty, true, out requested)
                    || requested == TimeSpeed.Paused)
                    return Fail("Unknown play speed; use Normal, Fast, Superfast or Ultrafast.");
                return await ctx.MainThread.InvokeAsync(() => Supervisor.Start(owner, requested,
                    leaseMs, mode, healthDropFraction, minHealthFraction,
                    ignoredHostileIds, ignoredPredatorIds, ignoredDownedColonistIds,
                    ignoredInjuredColonistIds, injuryStopCooldownMs),
                    cancellationToken).ConfigureAwait(false);
            }
            if (action == "pause")
                return await ctx.MainThread.InvokeAsync(() => Supervisor.Pause(owner, epoch),
                    cancellationToken).ConfigureAwait(false);
            return Fail("Unknown op '" + op + "'. Use start, pause, speed, status, heartbeat or events.");
        }

        private static object Fail(string message) { return new Dictionary<string, object> {
            { "success", false }, { "tool", ToolName }, { "message", message } }; }
    }

    internal static class Supervisor
    {
        private const int Capacity = 128;
        // Wall-clock grace for a force pause with no window behind it.
        // An autosave takes about a second; 20 s is generous and still
        // far short of anything a person would sit through.
        private const int ForcePauseGraceMs = 20000;
        // Hediff_Injury.Severity is hit points. A social-fight punch, a
        // scratch and an animal nip land at 2-6; a gunshot at 10-18. A new
        // wound under both of these is recorded and never stops the clock.
        private const float SeriousWoundSeverity = 10f;
        private const float SeriousWoundBleedRate = 0.5f;
        // How close a wild predator has to be to a colonist before it is the
        // colony's problem. A wolverine at 30 cells is wildlife; at 12 it is
        // inside the home area and can turn between one probe and the next.
        // Chebyshev cells, the same measure as distanceToNearestColonist.
        // Past every vanilla weapon's range (the longest, a sniper rifle or a
        // doomsday launcher, is 44.9), so nothing beyond this can hit a colonist
        // without walking first -- and walking changes the job, which is
        // re-read ten times a second. Same number combat.py's `end` uses.
        private const int DormantHostileCells = 50;
        // The game's own JobDefs, out of Data\*\Defs\JobDefs.
        // `CompCanBeDormant.SleepJob` starts `Wait_AsleepDormancy` on a dormant
        // insect hive or mech cluster, `ActivityDormant` is Anomaly's, and a
        // sleeping raider or animal lies in `LayDown`. `LayDownAwake` is
        // deliberately absent: awake is awake.
        private static readonly Dictionary<string, string> DormantJobs =
            new Dictionary<string, string>(StringComparer.Ordinal) {
                { "LayDown", "asleep" }, { "LayDownResting", "asleep" },
                { "Wait_Asleep", "asleep" }, { "RevenantSleep", "asleep" },
                { "Wait_AsleepDormancy", "dormant" }, { "ActivityDormant", "dormant" } };
        private static readonly object Gate = new object();
        private static readonly List<Dictionary<string, object>> Ring = new List<Dictionary<string, object>>();
        private static State _state;
        private static long _epoch;
        private static long _cursor;
        private static int _patched;
        private static string _patchError;
        // Injury stops are remembered across epochs on purpose. Restarting the
        // clock re-baselines injuries, so without this a wound that keeps
        // arriving reads as brand new every epoch and stops play forever.
        private static readonly Dictionary<int, InjuryStop> InjuryStops = new Dictionary<int, InjuryStop>();
        private static object _injuryStopsSession;
        // Conscious-hostile count at the last probe of the previous epoch. Like
        // the injury-stop memory this survives Start on purpose: a guard stop
        // and restart across the last kill must still raise the reminder.
        private static int _priorEpochConsciousHostiles;

        internal static bool IsActive { get { lock (Gate) return _state != null && _state.Active; } }

        internal static void EnsurePatched()
        {
            if (Interlocked.CompareExchange(ref _patched, 1, 0) != 0)
            {
                if (_patchError != null) throw new InvalidOperationException(_patchError);
                return;
            }
            try
            {
                var target = AccessTools.Method(typeof(TickManager), "TickManagerUpdate");
                if (target == null) throw new MissingMethodException("TickManager.TickManagerUpdate");
                new Harmony("homebridge.supervised-play").Patch(target,
                    postfix: new HarmonyMethod(typeof(Supervisor), nameof(OnUpdate)));
                var info = Harmony.GetPatchInfo(target);
                if (info == null || !info.Owners.Contains("homebridge.supervised-play"))
                    throw new InvalidOperationException("Harmony did not report the supervised-play patch after installation.");
                _patchError = null;
            }
            catch (Exception ex)
            {
                _patchError = ex.GetType().Name + ": " + ex.Message;
                Interlocked.Exchange(ref _patched, 0);
                throw;
            }
        }

        internal static object Start(string owner, TimeSpeed speed, int leaseMs,
            string mode, float healthDropFraction, float minHealthFraction,
            string ignoredHostiles, string ignoredPredators,
            string ignoredDowned, string ignoredInjured,
            int injuryStopCooldownMs)
        {
            lock (Gate)
            {
                if (_state != null && _state.Active)
                    return Failure("A supervisor is already active; pause it with its owner and epoch first.");
                if (HomePlayUntilEventTools.ShortGuardRunning)
                    return Failure("play_until_event is active; wait for that short guard to return before starting supervision.");
                if (!HomePlayUntilEventTools.CoreWatchersAvailable)
                    return Failure("Alert or transient-message reflection watcher is unavailable; refusing blind play.");
                if (Current.Game == null || Find.TickManager == null || Find.CurrentMap == null)
                    return Failure("No playable map is loaded.");
                if (LongEventHandler.AnyEventNowOrWaiting)
                    return Retryable("A long event (autosave, map generation) is running or queued; nothing is "
                        + "open to dismiss. An autosave clears in about a second -- the caller should retry, "
                        + "not report a refusal.", "long_event");
                // Only a window a human can close is a refusal. A merely paused
                // clock is not: the speed set below unpauses it.
                if (ForcePausingWindows().Count > 0)
                    return Failure(ForcePauseDetail());
                var profile = (mode ?? "colony").Trim().ToLowerInvariant();
                if (profile != "colony" && profile != "combat")
                    return Failure("Unknown mode; use colony or combat.");
                var s = new State {
                    Active = true, Epoch = ++_epoch, Owner = owner ?? "agent",
                    Session = Current.Game, RequestedSpeed = speed,
                    Mode = profile,
                    HealthDropFraction = ClampFloat(healthDropFraction, 0.01f, 1f),
                    MinHealthFraction = ClampFloat(minHealthFraction, 0.01f, 1f),
                    LeaseExpiresMs = NowMs() + Clamp(leaseMs, 1000, 30000),
                    IgnoredHostiles = PawnIds(ignoredHostiles),
                    IgnoredPredators = PawnIds(ignoredPredators),
                    // `all` is a whole-epoch decision about one CATEGORY, not a
                    // list of ids: predators wander in and out, which is why one
                    // wolverine cost four nights. It never covers a manhunter or
                    // a hostile faction -- those are checked before this.
                    IgnorePredatorsAll = Csv(ignoredPredators).Contains("all"),
                    IgnoredDowned = PawnIds(ignoredDowned),
                    IgnoredInjured = PawnIds(ignoredInjured),
                    InjuryStopCooldownMs = Clamp(injuryStopCooldownMs, 0, 1800000)
                };
                // Pawn IDs belong to a save; another colony must not inherit
                // this one's injury-stop memory.
                if (!ReferenceEquals(_injuryStopsSession, Current.Game))
                { InjuryStops.Clear(); _priorEpochConsciousHostiles = 0; _injuryStopsSession = Current.Game; }
                // Alerts are baselined like letters and messages, by the stable
                // key, so a count suffix moving cannot look like a new alert.
                foreach (var a in HomePlayUntilEventTools.ActiveAlertKeys(AlertPriority.High, null))
                    if (s.AlertKeys.Add(a.Key)) s.BaselineAlerts.Add(AlertRow(a));
                foreach (var l in HomePlayUntilEventTools.Letters()) s.Letters.Add(HomePlayUntilEventTools.SafeLetterId(l));
                foreach (var m in HomePlayUntilEventTools.LiveMessages()) s.Messages.Add(HomePlayUntilEventTools.SafeMessageId(m));
                foreach (var p in HomePlayUntilEventTools.SpawnedPawns(Find.CurrentMap).Where(HomePlayUntilEventTools.SafeIsColonist))
                {
                    var snapshot = InjurySnapshot.Capture(p);
                    s.Injuries[p.thingIDNumber] = snapshot;
                    var owed = InjuryCooldownRemainingMs(p.thingIDNumber, s.InjuryStopCooldownMs);
                    // A pawn already at or under the health floor has been seen
                    // in that state by whoever started this epoch. Acknowledge
                    // them here and say so on the start line, or the floor is a
                    // level trigger that refuses the start outright.
                    var below = snapshot.Health <= s.MinHealthFraction;
                    if (below) s.IgnoredInjured.Add(p.thingIDNumber);
                    var acked = s.IgnoredInjured.Contains(p.thingIDNumber);
                    if (owed <= 0 && !acked) continue;
                    s.SuppressedInjuries.Add(new Dictionary<string, object> {
                        { "pawnId", p.thingIDNumber },
                        { "pawnName", HomePlayUntilEventTools.SafeName(p) },
                        { "reason", below ? "below_health_floor"
                            : owed > 0 ? "cooldown" : "acknowledged" },
                        { "healthFraction", snapshot.Health },
                        { "minHealthFraction", s.MinHealthFraction },
                        { "secondsRemaining", (int)((owed + 999) / 1000) } });
                }
                _state = s;
                var hit = Probe(s);
                if (hit != null)
                {
                    // Starting a guard is itself a request for safety. If the
                    // initial probe finds danger, do not leave a running game
                    // unattended merely because no speed change was needed.
                    Stop(s, hit.Kind, hit.Detail, true, hit.Payload);
                    return Snapshot(s, s.PauseVerified == true);
                }
                Find.TickManager.CurTimeSpeed = speed;
                if (Find.TickManager.CurTimeSpeed != speed)
                {
                    Stop(s, "start_refused", "The requested speed did not take.", true, null);
                    return Snapshot(s, s.PauseVerified == true);
                }
                Add("started", "Supervised play started.", s, null);
                return Snapshot(s, true);
            }
        }

        internal static object Heartbeat(string owner, long epoch, int leaseMs)
        {
            lock (Gate)
            {
                var s = _state;
                if (!Owns(s, owner, epoch)) return Failure("Owner/epoch mismatch or no active supervisor.");
                s.LeaseExpiresMs = NowMs() + Clamp(leaseMs, 1000, 30000);
                return Snapshot(s, true);
            }
        }

        internal static object Speed(string owner, long epoch, TimeSpeed speed)
        {
            lock (Gate)
            {
                var s = _state;
                if (!Owns(s, owner, epoch)) return Failure("Owner/epoch mismatch or no active supervisor.");
                // Update expectation and game speed in the same main-thread
                // critical section, so our own change cannot look external.
                var old = s.RequestedSpeed;
                s.RequestedSpeed = speed;
                Find.TickManager.CurTimeSpeed = speed;
                if (Find.TickManager.CurTimeSpeed != speed)
                {
                    s.RequestedSpeed = old;
                    return Failure("The requested speed did not take; supervision remains active.");
                }
                Add("speed_changed", "Supervisor owner changed speed to " + speed + ".", s,
                    new Dictionary<string, object> { { "speed", speed.ToString() } });
                return Snapshot(s, true);
            }
        }

        internal static object Pause(string owner, long epoch)
        {
            lock (Gate)
            {
                var s = _state;
                if (!Owns(s, owner, epoch)) return Failure("Owner/epoch mismatch or no active supervisor.");
                Stop(s, "requested_pause", "Paused by the supervisor owner.", true, null);
                return Snapshot(s, s.PauseVerified == true);
            }
        }

        internal static object Status()
        {
            lock (Gate) return Snapshot(_state, true);
        }

        internal static object Events(long after, int limit)
        {
            lock (Gate)
            {
                var take = Clamp(limit, 1, Capacity);
                var oldest = Ring.Count == 0 ? _cursor + 1 : Convert.ToInt64(Ring[0]["cursor"]);
                var gap = after > 0 && after < oldest - 1;
                var rows = Ring.Where(x => Convert.ToInt64(x["cursor"]) > after).Take(take).ToList();
                return new Dictionary<string, object> {
                    { "success", true }, { "events", rows }, { "gap", gap },
                    { "lostCount", gap ? oldest - after - 1 : 0 },
                    { "oldestCursor", oldest }, { "newestCursor", _cursor },
                    { "nextCursor", rows.Count > 0 ? Convert.ToInt64(rows[rows.Count - 1]["cursor"]) : after }
                };
            }
        }

        internal static void OnUpdate()
        {
            lock (Gate)
            {
                var s = _state;
                if (s == null || !s.Active) return;
                try
                {
                    // A stale watcher belongs to the old session. Retire it,
                    // but never pause the newly loaded colony.
                    if (!ReferenceEquals(Current.Game, s.Session)) { Stop(s, "session_changed", "Loaded game changed.", false, null); return; }
                    var tm = Find.TickManager;
                    if (tm == null) { Stop(s, "unavailable", "Tick manager disappeared.", false, null); return; }
                    s.LastTick = tm.TicksGame;
                    if (s.PendingKind != null)
                    {
                        Stop(s, s.PendingKind, s.PendingDetail, true, s.PendingPayload);
                        return;
                    }
                    // A force pause is checked BEFORE the paused/speed tests: it is
                    // the more specific diagnosis, and a long event can hold the
                    // clock without ever touching CurTimeSpeed.
                    if (tm.ForcePaused) { HandleForcePause(s); return; }
                    if (s.ForcePauseSinceMs != 0 && !ResumeAfterForcePause(s)) return;
                    if (tm.CurTimeSpeed == TimeSpeed.Paused) { Stop(s, "external_pause", ExternalPauseDetail(), false, null); return; }
                    if (tm.CurTimeSpeed != s.RequestedSpeed) { Stop(s, "external_speed_changed", "Speed changed outside the supervisor.", true,
                        new Dictionary<string, object> { { "expectedSpeed", s.RequestedSpeed.ToString() },
                            { "actualSpeed", tm.CurTimeSpeed.ToString() } }); return; }
                    if (NowMs() >= s.LeaseExpiresMs) { Stop(s, "lease_expired", "Heartbeat lease expired.", true, null); return; }
                    if (NowMs() - s.LastProbeMs < 100) return;
                    s.LastProbeMs = NowMs();
                    var hit = Probe(s);
                    if (hit != null) Stop(s, hit.Kind, hit.Detail, true, hit.Payload);
                }
                catch (Exception ex) { Stop(s, "watcher_error", ex.GetType().Name + ": " + ex.Message, true, null); }
            }
        }

        /// A force pause with NO force-pausing window is a long event (the
        /// daily autosave is the common one) or a one-frame transient, and it
        /// clears itself. Decompiled 1.6: `TickManager.ForcePaused` ORs
        /// `WindowStack.WindowsForcePause` with `LongEventHandler.ForcePause`,
        /// which is simply `AnyEventNowOrWaiting`; `Autosaver.AutosaverTick`
        /// queues `DoAutosave` through `QueueLongEvent`, and `Root_Play.Update`
        /// still runs `UpdatePlay` (and so this patch) while that event is
        /// merely QUEUED. With autosaveIntervalDays=1 that fired every in-game
        /// day and killed the supervisor a dozen times a session.
        ///
        /// So: wait it out, say so once, and stop only if it outlives the
        /// grace. A force-pausing WINDOW still stops immediately and is named.
        private static void HandleForcePause(State s)
        {
            var windows = ForcePausingWindows();
            if (windows.Count > 0) { Stop(s, "force_paused", ForcePauseDetail(), true, null); return; }
            var now = NowMs();
            if (s.ForcePauseSinceMs == 0)
            {
                s.ForcePauseSinceMs = now;
                s.ForcePauseKind = LongEventHandler.AnyEventNowOrWaiting
                    ? "long_event" : "transient_force_pause";
                Add(s.ForcePauseKind,
                    (s.ForcePauseKind == "long_event"
                        ? "A long event (an autosave, most likely) is holding the clock"
                        : "The clock is force-paused with no window behind it")
                    + "; waiting up to " + (ForcePauseGraceMs / 1000)
                    + " s for it to clear. Play is NOT stopped and nothing needs dismissing. "
                    + ClockState() + ".", s,
                    new Dictionary<string, object> {
                        { "longEvent", LongEventHandler.AnyEventNowOrWaiting },
                        { "graceMs", ForcePauseGraceMs },
                        { "requestedSpeed", s.RequestedSpeed.ToString() } });
            }
            if (now - s.ForcePauseSinceMs < ForcePauseGraceMs) return;
            Stop(s, "force_paused", ForcePauseDetail() + " It did not clear in "
                + (ForcePauseGraceMs / 1000) + " s, which is far longer than an autosave, "
                + "so this is no longer treated as transient.", true, null);
        }

        /// The force pause cleared. Put the requested speed back and carry on.
        /// -> false when the speed would not take, in which case play stopped.
        private static bool ResumeAfterForcePause(State s)
        {
            var waited = NowMs() - s.ForcePauseSinceMs;
            var kind = s.ForcePauseKind ?? "transient_force_pause";
            s.ForcePauseSinceMs = 0; s.ForcePauseKind = null;
            var tm = Find.TickManager;
            var restored = tm != null && tm.CurTimeSpeed == s.RequestedSpeed;
            if (tm != null && !restored)
            {
                tm.CurTimeSpeed = s.RequestedSpeed;
                restored = tm.CurTimeSpeed == s.RequestedSpeed;
            }
            Add("force_pause_cleared", "The " + (kind == "long_event" ? "long event" : "transient force pause")
                + " cleared after " + (waited / 1000) + "." + ((waited % 1000) / 100) + " s; speed "
                + (restored ? "restored to " + s.RequestedSpeed : "could NOT be restored to " + s.RequestedSpeed)
                + ". Play was never stopped.", s,
                new Dictionary<string, object> { { "waitedMs", waited },
                    { "forcePauseKind", kind }, { "speedRestored", restored } });
            if (restored) return true;
            Stop(s, "start_refused", "The requested speed did not take after a force pause cleared.", true, null);
            return false;
        }

        /// Read by a human on stream. An unexplained pause is not evidence of a
        /// person: nothing in this process can see a keypress.
        private static string ExternalPauseDetail()
        {
            var auto = RecentAutoPauseLetters();
            if (auto.Count > 0)
                return "Game was paused by VANILLA, not by a person: " + string.Join("; ", auto.ToArray())
                    + " arrived and Prefs.AutomaticPauseMode = " + SafeAutoPauseMode()
                    + ", so LetterStack.ReceiveLetter called TickManager.Pause(). Deal with the letter, "
                    + "then restart supervised play. " + ClockState() + ".";
            return "Game was paused outside the supervisor (" + ClockState()
                + "), and no letter that vanilla auto-pauses for arrived in the last second "
                + "(Prefs.AutomaticPauseMode = " + SafeAutoPauseMode() + "). This is NOT evidence of "
                + "human input -- nothing here can see a keypress, and a dialog, a mod or a game event "
                + "pauses exactly the same way. Restart play unless a person says otherwise.";
        }

        /// Letters on the stack that vanilla would have paused the game for,
        /// arrived within the last game-second. `LetterStack.ReceiveLetter`
        /// pauses when `Prefs.AutomaticPauseMode >= let.def.pauseMode`;
        /// `Letter.arrivalTick` is stamped in that same method.
        private static List<string> RecentAutoPauseLetters()
        {
            var result = new List<string>();
            try
            {
                var tm = Find.TickManager;
                if (tm == null) return result;
                foreach (var l in HomePlayUntilEventTools.Letters())
                {
                    if (l == null || l.def == null) continue;
                    if ((int)Prefs.AutomaticPauseMode < (int)l.def.pauseMode) continue;
                    if (tm.TicksGame - l.arrivalTick > 60) continue;
                    result.Add(BridgeCommon.SafeString(() => l.Label.ToString())
                        + " (" + l.def.defName + ", pauseMode " + l.def.pauseMode + ")");
                }
            }
            catch { }
            return result;
        }

        private static string SafeAutoPauseMode()
        {
            try { return Prefs.AutomaticPauseMode.ToString(); } catch { return "unreadable"; }
        }

        // Letter defNames and MessageTypeDef names that inform without stopping.
        // Everything else -- ThreatBig, ThreatSmall, Death/PawnDeath, choice
        // letters, anything a mod adds -- still stops the clock.
        private static readonly HashSet<string> NonStoppingLetterDefs = new HashSet<string>(StringComparer.Ordinal) {
            "NeutralEvent", "PositiveEvent", "NegativeEvent" };
        private static readonly HashSet<string> NonStoppingMessageTypes = new HashSet<string>(StringComparer.Ordinal) {
            "NeutralEvent", "PositiveEvent", "HistoricalEvent", "NegativeEvent", "NegativeHealthEvent", "SituationResolved" };

        private static Hit Probe(State s)
        {
            var newLetters = new List<Dictionary<string, object>>();
            foreach (var l in HomePlayUntilEventTools.Letters())
            {
                var id = HomePlayUntilEventTools.SafeLetterId(l);
                if (!s.Letters.Add(id)) continue;
                var label = l.Label.ToString();
                // Ordinary announcements -- neutral, positive AND negative (a
                // short circuit, a dead crop, a departed visitor) -- inform
                // Hands without interrupting time; `negative` marks the ones
                // the relay wakes a parked Hands for. Threat, death, decision
                // and unknown letter classes stop. (2026-09-07: each negative
                // announcement had been costing a manual restart on stream.)
                if (l.GetType() == typeof(StandardLetter) && !l.ShouldAutomaticallyOpenLetter && l.def != null
                    && NonStoppingLetterDefs.Contains(l.def.defName))
                {
                    Add("notification_new", label, s, new Dictionary<string, object> {
                        { "id", id }, { "label", label }, { "letterDef", l.def.defName },
                        { "negative", l.def.defName == "NegativeEvent" } });
                    continue;
                }
                newLetters.Add(new Dictionary<string, object> { { "id", id }, { "label", label },
                    { "letterDef", l.def != null ? l.def.defName : null } });
            }
            var newMessages = new List<Dictionary<string, object>>();
            foreach (var m in HomePlayUntilEventTools.LiveMessages())
            {
                var id = HomePlayUntilEventTools.SafeMessageId(m);
                if (!s.Messages.Add(id)) continue;
                var type = HomePlayUntilEventTools.SafeMessageType(m);
                var text = HomePlayUntilEventTools.SafeMessageText(m) ?? "Transient message";
                var payload = new Dictionary<string, object> { { "id", id }, { "messageType", type },
                    { "text", text }, { "startingTick", HomePlayUntilEventTools.SafeMessageTick(m) } };
                // Same rule for the transient top-left messages: only
                // ThreatBig, ThreatSmall, PawnDeath and an unknown type stop.
                if (type != null && NonStoppingMessageTypes.Contains(type))
                {
                    payload["negative"] = type == "NegativeEvent" || type == "NegativeHealthEvent";
                    Add("notification_new", text, s, payload);
                    continue;
                }
                // A social fight's start is announced as ThreatSmall, so it
                // produced a stopping notification_batch before the injury
                // guard's socialFight suppression below was ever reached --
                // two clock stops off one debug fight, live 2026-09-12. The
                // test is state, not prose: every pawn this message points at
                // is ours and currently in MentalState SocialFighting. Vanilla
                // ends the fight on its own; the wounds stay the injury
                // guard's to judge, and a down or death still stops below.
                if (type == "ThreatSmall" && IsSocialFightAnnouncement(m))
                {
                    payload["negative"] = true;
                    payload["socialFight"] = true;
                    Add("notification_new", text, s, payload);
                    continue;
                }
                if (type == null || !(new[] { "RejectInput", "CautionInput", "SilentInput", "TaskCompletion" }).Contains(type))
                    newMessages.Add(payload);
            }
            // Alerts are scanned BEFORE the notification batch can end the
            // probe. RimWorld announces every Alert_Critical with a ThreatBig
            // message in the same frame, so with this scan below that return
            // the epoch always ended on the message and the next Start
            // baselined the now-standing alert -- a Critical alert_new was
            // structurally unreachable (live 2026-09-12: every alert_new ever
            // relayed was priority High). An alert still never stops play; its
            // announcement message is what stops, exactly as before.
            foreach (var a in HomePlayUntilEventTools.ActiveAlertKeys(AlertPriority.High, null))
                if (s.AlertKeys.Add(a.Key)) Add("alert_new", a.Value, s, AlertRow(a));
            if (newLetters.Count > 0 || newMessages.Count > 0)
            {
                var names = newLetters.Select(x => Convert.ToString(x["label"]))
                    .Concat(newMessages.Select(x => Convert.ToString(x["text"]))).ToList();
                return new Hit("notification_batch",
                    names.Count + " new notification(s): " + string.Join("; ", names),
                    new Dictionary<string, object> { { "letters", newLetters },
                        { "messages", newMessages }, { "letterCount", newLetters.Count },
                        { "messageCount", newMessages.Count } });
            }
            var pawns = HomePlayUntilEventTools.SpawnedPawns(Find.CurrentMap);
            var colonists = pawns.Where(HomePlayUntilEventTools.SafeIsColonist).ToList();
            CheckHostilesCleared(s, pawns);
            foreach (var p in pawns)
            {
                string category, reason;
                bool stops;
                category = ClassifyThreat(p, colonists, out reason, out stops);
                if (stops && !Acknowledged(s, p, category))
                    return ThreatHit(s, p, category, reason, pawns, colonists);
                if (HomePlayUntilEventTools.SafeIsColonist(p)
                    && (HomePlayUntilEventTools.SafeDowned(p) || HomePlayUntilEventTools.SafeDead(p))
                    && !s.IgnoredDowned.Contains(p.thingIDNumber))
                    return PawnHit("colonist_downed", p, HomePlayUntilEventTools.SafeDead(p) ? "dead" : "downed");
                if (HomePlayUntilEventTools.SafeIsColonist(p))
                {
                    var after = InjurySnapshot.Capture(p);
                    InjurySnapshot before;
                    if (!s.Injuries.TryGetValue(p.thingIDNumber, out before) || before == null)
                    {
                        // A newly joined/spawned colonist gets a baseline on
                        // first sight; subsequent worsening is guarded.
                        s.Injuries[p.thingIDNumber] = after;
                        continue;
                    }
                    // A NEW wound is a count or bleed increase. Severity and
                    // blood loss creeping on a known wound is worsening, not
                    // news: the tend alert already stands and a down still
                    // stops.
                    var newWound = after.Count > before.Count
                        || after.BleedRate > before.BleedRate + 0.001f;
                    var worsened = newWound
                        || after.Severity > before.Severity + 0.01f
                        || after.BloodLoss > before.BloodLoss + 0.001f;
                    // Hediff_Injury.Severity is hit points: a punch, a scratch
                    // and an animal nip are 2-6, a gunshot 10-18. Below the
                    // floor a new wound is recorded and never stops the clock.
                    var woundSeverity = after.Severity - before.Severity;
                    var woundBleed = after.BleedRate - before.BleedRate;
                    var seriousWound = newWound
                        && (woundSeverity >= SeriousWoundSeverity
                            || woundBleed >= SeriousWoundBleedRate);
                    // The health floor is EDGE-triggered against the epoch
                    // baseline: a pawn already below it at Start was
                    // acknowledged there and cannot cross it again here. The
                    // drop is measured from that same baseline and re-baselined
                    // whenever it fires, so one slide cannot stop forever.
                    var crossedFloor = before.Health > s.MinHealthFraction
                        && after.Health <= s.MinHealthFraction;
                    var bigDrop = before.Health - after.Health >= s.HealthDropFraction;
                    var severe = crossedFloor || bigDrop
                        || (s.Mode == "colony" && seriousWound);
                    if (!severe && !worsened) continue;
                    var owedMs = InjuryCooldownRemainingMs(p.thingIDNumber, s.InjuryStopCooldownMs);
                    var acknowledged = s.IgnoredInjured.Contains(p.thingIDNumber);
                    // Colonists brawling. Vanilla ends it on its own, every
                    // punch is a new wound, and four stops came off one fight.
                    var socialFight = InSocialFight(p);
                    // The cooldown is PER PAWN and covers every later wound,
                    // not the one that caused it: a wolf scratching a pawn
                    // every few seconds must not turn each restart into
                    // another ten-second epoch. Downs and deaths are a
                    // separate check above and no suppression reaches them.
                    var suppressed = acknowledged || owedMs > 0 || socialFight;
                    var condition = !severe ? null
                        : crossedFloor ? "health_floor"
                        : bigDrop ? "health_drop" : "serious_wound";
                    var payload = new Dictionary<string, object> {
                            { "pawnId", p.thingIDNumber },
                            { "pawnName", HomePlayUntilEventTools.SafeName(p) },
                            { "position", Position(p) }, { "injuryCountBefore", before.Count },
                            { "injuryCountAfter", after.Count }, { "severityBefore", before.Severity },
                            { "severityAfter", after.Severity }, { "bleedRateBefore", before.BleedRate },
                            { "bleedRateAfter", after.BleedRate }, { "bloodLossBefore", before.BloodLoss },
                            { "bloodLossAfter", after.BloodLoss }, { "healthAtStart", before.Health },
                            { "healthNow", after.Health }, { "minHealthFraction", s.MinHealthFraction },
                            { "healthDropFraction", s.HealthDropFraction },
                            { "newWound", newWound }, { "woundSeverity", woundSeverity },
                            { "stopCondition", condition },
                            { "socialFight", socialFight },
                            { "suppressed", suppressed },
                            { "suppressedBy", !suppressed ? null
                                : acknowledged ? "acknowledged"
                                : owedMs > 0 ? "cooldown" : "social_fight" },
                            { "cooldownRemainingMs", owedMs } };
                    if (severe && !suppressed)
                    {
                        RecordInjuryStop(p);
                        // Re-baseline on the way out. Without this the epoch
                        // baseline keeps the threshold true and every later
                        // tick stops again inside its own cooldown window.
                        s.Injuries[p.thingIDNumber] = after;
                        return new Hit(condition == "serious_wound" ? "colonist_injury" : "colonist_health",
                            InjuryDetail(s, p, before, after, condition), payload);
                    }
                    Add("injury_observed", HomePlayUntilEventTools.SafeName(p)
                        + (suppressed
                            ? (acknowledged
                                ? " is acknowledged with --allow-injured; play continues."
                                : socialFight
                                    ? " is in a social fight; play continues (a brawl is not a threat)."
                                    : " is inside the " + (s.InjuryStopCooldownMs / 1000)
                                        + " s injury-stop cooldown; play continues.")
                            : (newWound
                                ? " took a wound worth " + Num(woundSeverity)
                                    + " hit points; play continues (under the "
                                    + Num(SeriousWoundSeverity) + " that stops the clock)."
                                : " has a known wound worsening (blood loss/severity creep); play continues.")), s, payload);
                    // Both modes deliberately coalesce ordinary damage into
                    // durable observations instead of pause storms. A threshold
                    // that was suppressed re-baselines health too: it stays
                    // true against the old baseline, and a level-triggered
                    // observation would then fire ten times a second.
                    if (!severe) after.Health = before.Health; // retain start-of-session health baseline
                    s.Injuries[p.thingIDNumber] = after;
                }
            }
            return null;
        }

        /// Non-stopping reminder for the moment a fight ends: the last hostile
        /// who could still act just went down or died. Ignored hostiles count --
        /// acknowledging a raider does not make them not a raider.
        private static void CheckHostilesCleared(State s, List<Pawn> pawns)
        {
            var conscious = 0;
            var downed = new List<Pawn>();
            var colonistsNear = pawns.Where(HomePlayUntilEventTools.SafeIsColonist).ToList();
            foreach (var p in pawns)
            {
                string why;
                if (p == null || HomePlayUntilEventTools.SafeIsColonist(p)) continue;
                if (HomePlayUntilEventTools.SafeDead(p)) continue;
                var hostile = HomePlayUntilEventTools.IsHostile(p, out why);
                if (HomePlayUntilEventTools.SafeDowned(p))
                {
                    // A downed manhunter loses its mental state and a wild animal
                    // has no faction, so IsHostile can read false on the ground.
                    // Anything downed, non-player and near a colonist gets back up.
                    if (hostile || (!IsPlayerFactionPawn(p) && p.RaceProps != null
                        && p.RaceProps.Animal && colonistsNear.Any(c => Distance(p, c) <= 30)))
                        downed.Add(p);
                    continue;
                }
                if (hostile) conscious++;
            }
            // First probe of an epoch has no in-epoch predecessor, so it reads
            // the previous epoch's last count instead.
            var before = s.ConsciousHostiles ?? _priorEpochConsciousHostiles;
            var first = s.ConsciousHostiles == null;
            s.ConsciousHostiles = conscious;
            _priorEpochConsciousHostiles = conscious;
            if (conscious > 0) { s.HostilesCleared = false; return; }
            if (before <= 0 || s.HostilesCleared) return;
            var drafted = pawns.Where(p => p != null && HomePlayUntilEventTools.SafeIsColonist(p)
                && !HomePlayUntilEventTools.SafeDowned(p) && !HomePlayUntilEventTools.SafeDead(p)
                && SafeDrafted(p)).ToList();
            // Across a restart, only fire when there is something left to do.
            if (first && downed.Count == 0 && drafted.Count == 0) return;
            s.HostilesCleared = true;
            var parts = new List<string>();
            if (downed.Count > 0)
                parts.Add(downed.Count + " downed (" + string.Join("; ", downed
                    .Select(p => HomePlayUntilEventTools.SafeName(p) + " at "
                        + p.Position.x + "," + p.Position.z).ToArray())
                    + ") -- finish off or capture");
            if (drafted.Count > 0)
                parts.Add(drafted.Count + " colonist" + (drafted.Count == 1 ? "" : "s")
                    + " still drafted (" + string.Join(", ", drafted
                        .Select(HomePlayUntilEventTools.SafeName).ToArray()) + ") -- undraft them");
            Add("hostiles_cleared", "No conscious hostiles remain: "
                + (parts.Count > 0 ? string.Join("; ", parts.ToArray())
                    : "nothing downed and nobody drafted") + ".", s,
                new Dictionary<string, object> {
                    { "downedHostiles", downed.Select(p => (object)new Dictionary<string, object> {
                        { "thingId", p.thingIDNumber }, { "name", HomePlayUntilEventTools.SafeName(p) },
                        { "x", p.Position.x }, { "z", p.Position.z } }).ToList() },
                    { "draftedColonists", drafted.Select(p => (object)new Dictionary<string, object> {
                        { "thingId", p.thingIDNumber }, { "name", HomePlayUntilEventTools.SafeName(p) } }).ToList() },
                    { "consciousHostilesBefore", before },
                    { "acrossRestart", first } });
        }

        private static bool SafeDrafted(Pawn pawn) { try { return pawn.Drafted; } catch { return false; } }

        /// Milliseconds of injury-stop cooldown still owed to this pawn, or 0.
        private static long InjuryCooldownRemainingMs(int pawnId, long cooldownMs)
        {
            InjuryStop stop;
            if (cooldownMs <= 0 || !InjuryStops.TryGetValue(pawnId, out stop)) return 0;
            var remaining = stop.AtMs + cooldownMs - NowMs();
            return remaining > 0 ? remaining : 0;
        }

        private static void RecordInjuryStop(Pawn pawn)
        {
            var now = NowMs();
            foreach (var id in InjuryStops.Where(kv => now - kv.Value.AtMs > 3600000L)
                         .Select(kv => kv.Key).ToList())
                InjuryStops.Remove(id);
            InjuryStops[pawn.thingIDNumber] = new InjuryStop {
                AtMs = now, Tick = Find.TickManager != null ? Find.TickManager.TicksGame : 0,
                Name = HomePlayUntilEventTools.SafeName(pawn) };
        }

        /// One line, because it goes on screen: WHICH condition fired, what
        /// changed, and what to do. Naming the condition is the point -- a stop
        /// that only said "was injured" read the same for a scratch and for a
        /// pawn sliding under the health floor.
        private static string InjuryDetail(State s, Pawn p, InjurySnapshot before,
            InjurySnapshot after, string condition)
        {
            var name = HomePlayUntilEventTools.SafeName(p);
            var seconds = s.InjuryStopCooldownMs / 1000;
            var advice = seconds > 0
                ? "a restart within " + seconds + " s will not stop again on any injury to "
                    + name + ", or pass --allow-injured " + p.thingIDNumber + "."
                : "pass --allow-injured " + p.thingIDNumber + " to acknowledge the wound.";
            var why = condition == "health_floor"
                ? "health fell to " + Num(after.Health) + ", at or under the "
                    + Num(s.MinHealthFraction) + " floor"
                : condition == "health_drop"
                    ? "health fell " + Num(before.Health - after.Health) + " from "
                        + Num(before.Health) + " at the start of this epoch (limit "
                        + Num(s.HealthDropFraction) + ")"
                    : "took a new wound worth " + Num(after.Severity - before.Severity)
                        + " hit points (a scratch or a punch is under "
                        + Num(SeriousWoundSeverity) + ")";
            return name + " stopped play: " + why + " (injuries " + before.Count + " -> " + after.Count
                + ", bleed " + Num(before.BleedRate) + " -> " + Num(after.BleedRate)
                + "/day, blood loss " + Num(before.BloodLoss) + " -> " + Num(after.BloodLoss)
                + ", health " + Num(after.Health) + "). If this repeats, break the contact: "
                + "move them away or undraft them so they seek care; " + advice;
        }

        /// Two colonists brawling. Vanilla ends a social fight on its own and
        /// nobody dies of one; every punch is a new wound.
        private static bool InSocialFight(Pawn p)
        {
            try
            {
                return p.MentalStateDef != null && p.MentalStateDef.defName == "SocialFighting";
            }
            catch { return false; }
        }

        /// A ThreatSmall message whose every look-target is one of our pawns
        /// currently in a social fight is the fight's own announcement. The
        /// pawns' state is the test rather than the message text, so wording
        /// cannot break it; the cost is that a fight already over by the time
        /// the probe reads the message stops the clock like any ThreatSmall,
        /// which is the old behaviour and wastes one restart, never a fight.
        /// `Faction.OfPlayerSilentFail`, never `OfPlayer` -- the usual hazard.
        private static bool IsSocialFightAnnouncement(Message m)
        {
            try
            {
                var targets = m.lookTargets == null ? null : m.lookTargets.targets;
                if (targets == null || targets.Count == 0) return false;
                var player = Faction.OfPlayerSilentFail;
                if (player == null) return false;
                var sawPawn = false;
                foreach (var t in targets)
                {
                    var p = t.Thing as Pawn;
                    if (p == null || p.Faction != player || !InSocialFight(p)) return false;
                    sawPawn = true;
                }
                return sawPawn;
            }
            catch { return false; }
        }

        private static string Num(float value) { return value.ToString("0.00", CultureInfo.InvariantCulture); }

        private static Hit PawnHit(string kind, Pawn pawn, string reason)
        {
            return new Hit(kind, HomePlayUntilEventTools.SafeName(pawn) + " (" + reason + ")",
                new Dictionary<string, object> { { "pawnId", pawn.thingIDNumber },
                    { "pawnName", HomePlayUntilEventTools.SafeName(pawn) }, { "position", Position(pawn) },
                    { "reason", reason } });
        }
        /// One alert row; the key is type|priority|normalized-label.
        private static Dictionary<string, object> AlertRow(KeyValuePair<string, string> a)
        {
            var parts = a.Key.Split('|');
            return new Dictionary<string, object> { { "alertKey", a.Key }, { "label", a.Value },
                { "priority", parts.Length > 1 ? parts[1] : null } };
        }

        private static object Position(Pawn pawn) { return new Dictionary<string, object> {
            { "x", pawn.Position.x }, { "z", pawn.Position.z } }; }

        private static List<string> ForcePausingWindows()
        {
            var windows = Find.WindowStack == null ? null : Find.WindowStack.Windows;
            return windows == null ? new List<string>() : windows
                .Where(w => w != null && w.forcePause).Select(w => w.GetType().FullName).ToList();
        }

        private static string ClockState()
        {
            var tm = Find.TickManager;
            if (tm == null) return "no tick manager";
            return "TimeSpeed." + tm.CurTimeSpeed + ", ForcePaused=" + tm.ForcePaused
                + (LongEventHandler.AnyEventNowOrWaiting ? ", a long event is running or queued" : "")
                + (Find.WindowStack != null && Find.WindowStack.WindowsForcePause ? ", WindowStack.WindowsForcePause" : "");
        }

        private static string ForcePauseDetail()
        {
            var names = ForcePausingWindows();
            if (names.Count == 0)
                return "no force-pausing window found; clock was paused by " + ClockState()
                    + ". There is no dialog to dismiss: do not go looking for one. A long event (the daily "
                    + "autosave) does this and is waited out for " + (ForcePauseGraceMs / 1000)
                    + " s without stopping play, so reaching this stop means it outlasted that. Restart "
                    + "supervised play.";
            return "Game is force-paused by " + string.Join(", ", names)
                + ". Read python ui.py for the visible title and buttons; close or answer it before restarting.";
        }

        private static bool PredatorHunting(Pawn p) { try { return p.CurJob != null && p.CurJob.def != null && p.CurJob.def.defName == "PredatorHunt"; } catch { return false; } }

        /// <summary>
        /// What kind of threat this pawn is, or null for "not a threat at all".
        /// `stops` is the only thing the guard acts on; the category and the
        /// reason are what the refusal prints.
        ///
        /// ## Why this exists (Threadneedle, four separate nights, 2026-09-08)
        ///
        /// The old test for a predator was the JOB NAME and a 40-cell radius,
        /// nothing else: `PredatorHunting(p) && p.Faction != Faction.OfPlayer &&
        /// within 40 of a colonist`. A wolverine eating a hare on the far side
        /// of the home area matches all three, so it stopped the clock, and
        /// `--ignore-hostile all` could not even name it -- `home/status` has
        /// had the prey-ownership test since 2026-09-01 (`huntersIgnored[]`),
        /// so the animal the guard was stopping for appeared in NO list the
        /// refusal read. The two now agree.
        ///
        /// A wild predator merely existing, or hunting wildlife, is scenery.
        /// It is a threat when, and only when:
        ///   * **manhunter** -- a `MentalStateDef` with Manhunter in the name,
        ///     or `Pawn.InAggroMentalState`. Ours or wild; a tame animal that
        ///     goes berserk is checked here, before the faction test, on purpose.
        ///   * **hostile** -- `Faction.HostileTo(player)`. Raiders, unchanged.
        ///     Except **hostile_dormant**, `stops` false: one of `DormantJobs`
        ///     AND more than `DormantHostileCells` away. Still a hostile, still
        ///     listed, still stops the moment it stands up or walks closer.
        ///   * **predator_hunting_ours** -- its current job target is a colonist,
        ///     a colony animal or a colony prisoner. No radius: something eating
        ///     our muffalo across the map is exactly what the clock should stop
        ///     for. `job.targetA` is the prey (`JobDriver_PredatorHunt.PreyInd`
        ///     is `TargetIndex.A`) and holds the `Corpse` after the kill.
        /// Anything else predatory is category `predator`, `stops` false: it is
        /// listed in the payload so the operator can see it was considered.
        /// </summary>
        private static string ClassifyThreat(Pawn p, List<Pawn> colonists, out string reason, out bool stops)
        {
            reason = null; stops = false;
            if (p == null || HomePlayUntilEventTools.SafeIsColonist(p)
                || HomePlayUntilEventTools.SafeDead(p)) return null;
            string why;
            var hostile = HomePlayUntilEventTools.IsHostile(p, out why);
            var aggro = SafeAggro(p);
            if (hostile || aggro)
            {
                // IsHostile spells its own reason: "manhunter:<MentalStateDef>"
                // or "faction:<name>". InAggroMentalState catches a state whose
                // defName does not contain the word.
                var manhunter = aggro || (why != null
                    && why.StartsWith("manhunter", StringComparison.OrdinalIgnoreCase));
                // Asleep or dormant AND out of every weapon's reach. Five Sorne
                // Geneline insectoids asleep in a cave 74-78 cells out refused
                // every bare `start` on Threadneedle until somebody typed
                // --ignore-hostile all, which is a decision nobody should be
                // making nightly about a raid they cannot see. A MANHUNTER is
                // never dormant: the mental state is the wakefulness.
                string sleeping;
                var away = NearestColonistDistance(p, colonists);
                if (!manhunter && DormantJobs.TryGetValue(SafeJobDef(p) ?? "", out sleeping)
                    && away.HasValue && away.Value > DormantHostileCells)
                {
                    reason = sleeping + " " + away.Value + " cells away; wakes -> stops";
                    stops = false;
                    return "hostile_dormant";
                }
                reason = (why == null || why == "none" ? "in an aggro mental state" : why)
                    + " (" + Bearing(p, colonists) + ")";
                stops = true;
                return manhunter ? "manhunter" : "hostile";
            }
            // Our own tame warg hunts; that is our hunt, not an event.
            if (IsPlayerFactionPawn(p)) return null;
            var predator = SafePredator(p);
            Pawn prey;
            if ((predator || PredatorHunting(p)) && TargetBelongsToPlayer(p, out prey))
            {
                reason = "hunting " + HomePlayUntilEventTools.SafeName(prey)
                    + ", which belongs to the colony (" + Bearing(p, colonists) + ")";
                stops = true;
                return "predator_hunting_ours";
            }
            if (!predator) return null;
            if (HomePlayUntilEventTools.SafeDowned(p))
            {
                reason = "wild predator, DOWNED (" + Bearing(p, colonists) + ")";
                return "predator";
            }
            reason = "wild predator " + (PredatorHunting(p) ? "hunting wildlife" : "on the map")
                + ", " + Bearing(p, colonists) + "; not a threat to the colony";
            return "predator";
        }

        /// Which stop reason a category is filed under. `manhunter` files as
        /// `hostile` because that is what every downstream reader already knows.
        private static string StopKind(string category)
        {
            if (category == "predator_hunting_ours") return "predator_hunt";
            return "hostile";
        }

        /// Has this threat been acknowledged for the epoch? `--ignore-hostile`
        /// covers everything, as it always has; `--ignore-predator` covers only
        /// a hunt on one of ours, so it can never wave through a raid.
        private static bool Acknowledged(State s, Pawn p, string category)
        {
            if (s == null || p == null) return false;
            if (s.IgnoredHostiles.Contains(p.thingIDNumber)) return true;
            if (category != "predator_hunting_ours") return false;
            return s.IgnorePredatorsAll || s.IgnoredPredators.Contains(p.thingIDNumber);
        }

        /// Every classified non-colonist on the map, so the refusal can say
        /// which category each one fell in -- including the ones that did NOT
        /// stop the clock, which is the half the operator could never see.
        private static List<object> ThreatRows(State s, List<Pawn> pawns, List<Pawn> colonists)
        {
            var rows = new List<object>();
            foreach (var p in pawns ?? new List<Pawn>())
            {
                if (rows.Count >= 40) break;
                string category, reason;
                bool stops;
                category = ClassifyThreat(p, colonists, out reason, out stops);
                if (category == null) continue;
                var acked = stops && Acknowledged(s, p, category);
                rows.Add(new Dictionary<string, object> {
                    { "pawnId", p.thingIDNumber },
                    { "thingId", BridgeCommon.SafeString(() => p.GetUniqueLoadID()) },
                    { "name", HomePlayUntilEventTools.SafeName(p) },
                    { "defName", BridgeCommon.SafeString(() => p.def != null ? p.def.defName : null) },
                    { "category", category }, { "reason", reason },
                    { "stops", stops && !acked }, { "acknowledged", acked },
                    { "downed", HomePlayUntilEventTools.SafeDowned(p) },
                    { "distanceToNearestColonist", NearestColonistDistance(p, colonists) } });
            }
            return rows;
        }

        private static Hit ThreatHit(State s, Pawn p, string category, string reason,
            List<Pawn> pawns, List<Pawn> colonists)
        {
            return new Hit(StopKind(category),
                HomePlayUntilEventTools.SafeName(p) + " [" + category + "] " + reason,
                new Dictionary<string, object> { { "pawnId", p.thingIDNumber },
                    { "pawnName", HomePlayUntilEventTools.SafeName(p) },
                    { "thingId", BridgeCommon.SafeString(() => p.GetUniqueLoadID()) },
                    { "position", Position(p) }, { "reason", reason },
                    { "category", category },
                    { "threats", ThreatRows(s, pawns, colonists) } });
        }

        /// "8 cells from Finn" / "no colonist on the map". Read aloud on stream.
        private static string Bearing(Pawn p, List<Pawn> colonists)
        {
            Pawn nearest = null;
            var best = int.MaxValue;
            foreach (var c in colonists ?? new List<Pawn>())
            {
                if (c == null) continue;
                var d = Distance(p, c);
                if (d < best) { best = d; nearest = c; }
            }
            if (nearest == null) return "no colonist on the map";
            return best + " cells from " + HomePlayUntilEventTools.SafeName(nearest);
        }

        private static int? NearestColonistDistance(Pawn p, List<Pawn> colonists)
        {
            int? best = null;
            foreach (var c in colonists ?? new List<Pawn>())
            {
                if (c == null) continue;
                var d = Distance(p, c);
                if (!best.HasValue || d < best.Value) best = d;
            }
            return best;
        }

        /// <summary>Verse.RaceProperties.predator -- a plain public bool field
        /// on the race def, the game's own answer, so nothing here needs a list
        /// of defNames and nothing here can Log.Error.</summary>
        private static bool SafePredator(Pawn p)
        { try { return p != null && p.RaceProps != null && p.RaceProps.predator; } catch { return false; } }

        /// `Pawn.InAggroMentalState` -- true for manhunter and for every other
        /// aggressive mental state, including ones whose defName does not say so.
        private static bool SafeAggro(Pawn p)
        { try { return p != null && p.InAggroMentalState; } catch { return false; } }

        /// The defName of whatever this pawn is doing, or null. The same read
        /// `home/status` puts on a threat row as `job`, so the two agree about
        /// which insectoid is asleep.
        private static string SafeJobDef(Pawn p)
        { try { return p != null && p.CurJobDef != null ? p.CurJobDef.defName : null; } catch { return null; } }

        /// <summary>NOT `Faction.OfPlayer`: its body is
        /// `get_OfPlayerSilentFail` followed by `Verse.Log.Error`, whose call
        /// path contains `TickManager.Pause()`. Asking who the player is on a
        /// map with no player faction would PAUSE the colony from inside the
        /// guard that exists to notice pauses.</summary>
        private static Faction PlayerFactionSafe()
        { try { return Faction.OfPlayerSilentFail; } catch { return null; } }

        private static bool IsPlayerFactionPawn(Pawn p)
        {
            try
            {
                var player = PlayerFactionSafe();
                return player != null && p != null && p.Faction == player;
            }
            catch { return false; }
        }

        /// The thing this pawn's current job is aimed at, and whether the colony
        /// owns it. `job.targetA` holds the prey during a `PredatorHunt` chase
        /// and the prey's `Corpse` once the kill is made, so both are unwrapped.
        /// Ours means on the player faction (colonist, tame animal) or held by
        /// it (a prisoner).
        private static bool TargetBelongsToPlayer(Pawn hunter, out Pawn target)
        {
            target = null;
            try
            {
                if (hunter == null) return false;
                var job = hunter.CurJob;
                if (job == null) return false;
                var thing = job.targetA.Thing;
                target = thing as Pawn;
                if (target == null)
                {
                    var corpse = thing as Corpse;
                    if (corpse != null) target = corpse.InnerPawn;
                }
                if (target == null) return false;
                var player = PlayerFactionSafe();
                if (player == null) return false;
                if (target.Faction == player) return true;
                return target.HostFaction == player;
            }
            catch { return false; }
        }

        private static int Distance(Pawn a, Pawn b) { return Math.Max(Math.Abs(a.Position.x - b.Position.x), Math.Abs(a.Position.z - b.Position.z)); }
        private static bool Owns(State s, string owner, long epoch) { return s != null && s.Active && s.Epoch == epoch && string.Equals(s.Owner, owner ?? "agent", StringComparison.Ordinal); }
        private static void Stop(State s, string kind, string detail, bool pause,
            Dictionary<string, object> payload)
        {
            if (!ReferenceEquals(s, _state) || !s.Active) return;
            if (pause && Find.TickManager != null && Find.TickManager.CurTimeSpeed != TimeSpeed.Paused) Find.TickManager.Pause();
            s.PausedAtStop = Find.TickManager != null && Find.TickManager.CurTimeSpeed == TimeSpeed.Paused;
            s.PauseVerified = !pause || s.PausedAtStop;
            if (pause && !s.PausedAtStop)
            {
                // Stay armed and retry on the next frame. Going inactive here
                // would turn a failed pause into unguarded play.
                s.PendingKind = kind; s.PendingDetail = detail; s.PendingPayload = payload;
                if (!s.PauseFailureReported)
                {
                    s.PauseFailureReported = true;
                    Add("pause_failed", "Pause did not take while handling " + kind + ": " + detail,
                        s, payload);
                }
                return;
            }
            s.PendingKind = null; s.PendingDetail = null; s.PendingPayload = null;
            // Carried onto the status snapshot: `start` answers with a snapshot
            // and nothing else, so without this the refusal has one name and no
            // categories. The ring row keeps the same list.
            if (payload != null && payload.ContainsKey("threats")) s.StopThreats = payload["threats"];
            s.Active = false; s.StopReason = kind; s.StopDetail = detail; Add(kind, detail, s, payload);
        }
        private static void Add(string kind, string detail, State s, Dictionary<string, object> payload)
        {
            var row = new Dictionary<string, object> { { "cursor", ++_cursor }, { "epoch", s.Epoch },
                { "kind", kind }, { "detail", detail }, { "event", payload },
                { "tick", Find.TickManager != null ? Find.TickManager.TicksGame : s.LastTick }, { "atMs", NowMs() } };
            Ring.Add(row); if (Ring.Count > Capacity) Ring.RemoveAt(0);
        }
        private static object Snapshot(State s, bool success)
        {
            return new Dictionary<string, object> { { "success", success }, { "active", s != null && s.Active },
                { "epoch", s != null ? s.Epoch : 0 }, { "owner", s != null ? s.Owner : null },
                { "requestedSpeed", s != null ? s.RequestedSpeed.ToString() : null },
                { "mode", s != null ? s.Mode : null },
                { "healthDropFraction", s != null ? s.HealthDropFraction : 0 },
                { "minHealthFraction", s != null ? s.MinHealthFraction : 0 },
                { "lastTick", s != null ? s.LastTick : 0 },
                { "injuryStopCooldownMs", s != null ? s.InjuryStopCooldownMs : 0 },
                { "suppressedInjuryPawns", s != null ? (object)s.SuppressedInjuries : null },
                { "baselineAlerts", s != null ? (object)s.BaselineAlerts : null },
                { "paused", s != null ? (object)s.PausedAtStop : null },
                { "forcePauseWaitingMs", s != null && s.ForcePauseSinceMs != 0 ? (object)(NowMs() - s.ForcePauseSinceMs) : null },
                { "forcePauseKind", s != null ? s.ForcePauseKind : null },
                { "pauseVerified", s != null ? (object)s.PauseVerified : null },
                { "sessionChanged", s != null && s.StopReason == "session_changed" },
                { "leaseExpiresAtMs", s != null ? s.LeaseExpiresMs : 0 }, { "leaseRemainingMs", s != null ? Math.Max(0, s.LeaseExpiresMs - NowMs()) : 0 },
                { "stopReason", s != null ? s.StopReason : null }, { "stopDetail", s != null ? s.StopDetail : null },
                { "stopThreats", s != null ? s.StopThreats : null },
                { "newestCursor", _cursor }, { "patchError", _patchError } };
        }
        private static object Failure(string message) { return new Dictionary<string, object> { { "success", false }, { "message", message }, { "retryable", false }, { "refusal", null }, { "newestCursor", _cursor } }; }
        /// A refusal the caller should retry rather than report: the condition
        /// clears on its own within a second or two.
        private static object Retryable(string message, string refusal) { return new Dictionary<string, object> { { "success", false }, { "message", message }, { "retryable", true }, { "refusal", refusal }, { "newestCursor", _cursor } }; }
        private static HashSet<string> Csv(string value) { return new HashSet<string>((value ?? "").Split(',').Select(x => x.Trim()).Where(x => x.Length > 0), StringComparer.OrdinalIgnoreCase); }
        private static HashSet<int> PawnIds(string value) { var r = new HashSet<int>(); foreach (var x in Csv(value)) { int n; var digits = new string(x.Reverse().TakeWhile(char.IsDigit).Reverse().ToArray()); if (int.TryParse(digits, out n)) r.Add(n); } return r; }
        private static int Clamp(int x, int lo, int hi) { return Math.Max(lo, Math.Min(hi, x)); }
        private static float ClampFloat(float x, float lo, float hi) { return Math.Max(lo, Math.Min(hi, x)); }
        private static long NowMs() { return (DateTime.UtcNow.Ticks - 621355968000000000L) / TimeSpan.TicksPerMillisecond; }

        private sealed class Hit
        {
            public readonly string Kind; public readonly string Detail;
            public readonly Dictionary<string, object> Payload;
            public Hit(string kind, string detail, Dictionary<string, object> payload)
            { Kind = kind; Detail = detail; Payload = payload; }
        }

        private sealed class InjurySnapshot
        {
            public int Count; public float Severity; public float BleedRate; public float BloodLoss; public float Health;
            public static InjurySnapshot Capture(Pawn pawn)
            {
                var result = new InjurySnapshot();
                try
                {
                    result.Health = pawn.health.summaryHealth.SummaryHealthPercent;
                    var hediffs = pawn.health.hediffSet.hediffs;
                    foreach (var h in hediffs)
                    {
                        var injury = h as Hediff_Injury;
                        if (injury != null) { result.Count++; result.Severity += injury.Severity; result.BleedRate += injury.BleedRate; }
                        if (h.def == HediffDefOf.BloodLoss) result.BloodLoss = Math.Max(result.BloodLoss, h.Severity);
                    }
                }
                catch { }
                return result;
            }
        }

        private sealed class State
        {
            public bool Active; public long Epoch; public string Owner; public object Session; public TimeSpeed RequestedSpeed;
            public string Mode; public float HealthDropFraction; public float MinHealthFraction;
            public long LeaseExpiresMs; public long LastProbeMs; public int LastTick; public bool PausedAtStop;
            public bool? PauseVerified; public bool PauseFailureReported;
            public string StopReason; public string StopDetail;
            public string PendingKind; public string PendingDetail;
            // Wall-clock ms at which a windowless force pause began, 0 when none.
            public long ForcePauseSinceMs; public string ForcePauseKind;
            public Dictionary<string, object> PendingPayload;
            public readonly HashSet<string> Letters = new HashSet<string>(StringComparer.Ordinal);
            public readonly HashSet<string> Messages = new HashSet<string>(StringComparer.Ordinal);
            public readonly Dictionary<int, InjurySnapshot> Injuries = new Dictionary<int, InjurySnapshot>();
            // Grows only: an alert that clears and returns is not news again.
            public readonly HashSet<string> AlertKeys = new HashSet<string>(StringComparer.Ordinal);
            public readonly List<Dictionary<string, object>> BaselineAlerts = new List<Dictionary<string, object>>();
            // null until the first probe of this epoch has counted.
            public int? ConsciousHostiles; public bool HostilesCleared;
            public HashSet<int> IgnoredHostiles; public HashSet<int> IgnoredDowned;
            public HashSet<int> IgnoredPredators; public bool IgnorePredatorsAll;
            public HashSet<int> IgnoredInjured; public int InjuryStopCooldownMs;
            // The classified threat list as it stood at the stop, so a refusal
            // can name a category per pawn instead of one name and no id.
            public object StopThreats;
            public readonly List<Dictionary<string, object>> SuppressedInjuries = new List<Dictionary<string, object>>();
        }

        private sealed class InjuryStop
        {
            public long AtMs; public int Tick; public string Name;
        }
    }
}
