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
            [ToolParameter(Description = "colony stops on any new/worsened injury; combat records ordinary wounds and stops only at configured health thresholds.", DefaultValue = "colony")] string mode = "colony",
            [ToolParameter(Description = "Combat mode: stop when summary health drops this fraction from start, clamped 0.01..1.", DefaultValue = 0.15f)] float healthDropFraction = 0.15f,
            [ToolParameter(Description = "Combat mode: stop when summary health reaches this fraction, clamped 0.01..1.", DefaultValue = 0.5f)] float minHealthFraction = 0.5f,
            [ToolParameter(Description = "Accepted and ignored: alerts never stop play.", DefaultValue = "")] string ignoredAlertLabels = "",
            [ToolParameter(Description = "Stable IDs of explicitly acknowledged hostiles or nearby hunting predators, comma-separated.", DefaultValue = "")] string ignoredHostileIds = "",
            [ToolParameter(Description = "Stable IDs of explicitly acknowledged downed colonists, comma-separated.", DefaultValue = "")] string ignoredDownedColonistIds = "",
            [ToolParameter(Description = "Stable IDs of colonists whose non-severe injuries are acknowledged; they never stop the clock in this epoch, comma-separated.", DefaultValue = "")] string ignoredInjuredColonistIds = "",
            [ToolParameter(Description = "After a colonist_injury stop, that colonist non-severe injuries do not stop the clock again for this many real milliseconds, across restarts. Clamped 0..1800000; 0 disables.", DefaultValue = 180000)] int injuryStopCooldownMs = 180000,
            [ToolParameter(Description = "For events, return rows strictly after this cursor.", DefaultValue = 0L)] long afterCursor = 0L,
            [ToolParameter(Description = "For events, maximum rows, clamped to 1..128.", DefaultValue = 64)] int limit = 64)
        {
            return BridgeCommon.WithUnknownArguments(
                await SupervisedPlayCore(ctx, cancellationToken, op, owner, epoch,
                    leaseMs, speed, mode, healthDropFraction, minHealthFraction,
                    ignoredAlertLabels, ignoredHostileIds, ignoredDownedColonistIds,
                    ignoredInjuredColonistIds, injuryStopCooldownMs,
                    afterCursor, limit).ConfigureAwait(false),
                ctx, typeof(HomeSupervisedPlayTools), ToolName);
        }

        private async Task<object> SupervisedPlayCore(
            IRimBridgeContext ctx, CancellationToken cancellationToken,
            string op, string owner, long epoch, int leaseMs, string speed,
            string mode, float healthDropFraction, float minHealthFraction,
            string ignoredAlertLabels, string ignoredHostileIds,
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
                    ignoredHostileIds, ignoredDownedColonistIds,
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
            string ignoredHostiles, string ignoredDowned, string ignoredInjured,
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
                    s.Injuries[p.thingIDNumber] = InjurySnapshot.Capture(p);
                    var owed = InjuryCooldownRemainingMs(p.thingIDNumber, s.InjuryStopCooldownMs);
                    var acked = s.IgnoredInjured.Contains(p.thingIDNumber);
                    if (owed <= 0 && !acked) continue;
                    s.SuppressedInjuries.Add(new Dictionary<string, object> {
                        { "pawnId", p.thingIDNumber },
                        { "pawnName", HomePlayUntilEventTools.SafeName(p) },
                        { "reason", owed > 0 ? "cooldown" : "acknowledged" },
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
                if (type == null || !(new[] { "RejectInput", "CautionInput", "SilentInput", "TaskCompletion" }).Contains(type))
                    newMessages.Add(payload);
            }
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
            // An alert never stops play; a new one is a message. The key set only
            // grows within an epoch, so an alert that flaps cannot re-report.
            foreach (var a in HomePlayUntilEventTools.ActiveAlertKeys(AlertPriority.High, null))
                if (s.AlertKeys.Add(a.Key)) Add("alert_new", a.Value, s, AlertRow(a));
            var pawns = HomePlayUntilEventTools.SpawnedPawns(Find.CurrentMap);
            var colonists = pawns.Where(HomePlayUntilEventTools.SafeIsColonist).ToList();
            CheckHostilesCleared(s, pawns);
            foreach (var p in pawns)
            {
                string why;
                if (!HomePlayUntilEventTools.SafeIsColonist(p)
                    && HomePlayUntilEventTools.IsHostile(p, out why)
                    && !s.IgnoredHostiles.Contains(p.thingIDNumber))
                    return PawnHit("hostile", p, why);
                if (HomePlayUntilEventTools.SafeIsColonist(p)
                    && (HomePlayUntilEventTools.SafeDowned(p) || HomePlayUntilEventTools.SafeDead(p))
                    && !s.IgnoredDowned.Contains(p.thingIDNumber))
                    return PawnHit("colonist_downed", p, HomePlayUntilEventTools.SafeDead(p) ? "dead" : "downed");
                if (PredatorHunting(p) && p.Faction != Faction.OfPlayer
                    && !s.IgnoredHostiles.Contains(p.thingIDNumber)
                    && colonists.Any(c => Distance(p, c) <= 40))
                    return PawnHit("predator_hunt", p, "PredatorHunt within 40 cells");
                if (HomePlayUntilEventTools.SafeIsColonist(p))
                {
                    var after = InjurySnapshot.Capture(p);
                    InjurySnapshot before;
                    if (!s.Injuries.TryGetValue(p.thingIDNumber, out before))
                    {
                        // A newly joined/spawned colonist gets a baseline on
                        // first sight; subsequent worsening is guarded.
                        s.Injuries[p.thingIDNumber] = after;
                        continue;
                    }
                    if (before != null
                        && s.Mode == "combat"
                        && (after.Health <= s.MinHealthFraction
                            || before.Health - after.Health >= s.HealthDropFraction))
                        return new Hit("colonist_health", HomePlayUntilEventTools.SafeName(p)
                            + " crossed a combat health threshold.",
                            new Dictionary<string, object> { { "pawnId", p.thingIDNumber },
                                { "pawnName", HomePlayUntilEventTools.SafeName(p) },
                                { "position", Position(p) }, { "healthAtStart", before.Health },
                                { "healthNow", after.Health }, { "minHealthFraction", s.MinHealthFraction },
                                { "healthDropFraction", s.HealthDropFraction } });
                    if (before != null
                        && (after.Count > before.Count
                            || after.Severity > before.Severity + 0.01f
                            || after.BleedRate > before.BleedRate + 0.001f
                            || after.BloodLoss > before.BloodLoss + 0.001f))
                    {
                        var payload = new Dictionary<string, object> { { "pawnId", p.thingIDNumber },
                                { "pawnName", HomePlayUntilEventTools.SafeName(p) },
                                { "position", Position(p) }, { "injuryCountBefore", before.Count },
                                { "injuryCountAfter", after.Count }, { "severityBefore", before.Severity },
                                { "severityAfter", after.Severity }, { "bleedRateBefore", before.BleedRate },
                                { "bleedRateAfter", after.BleedRate }, { "bloodLossBefore", before.BloodLoss },
                                { "bloodLossAfter", after.BloodLoss }, { "healthAtStart", before.Health },
                                { "healthNow", after.Health } };
                        var threshold = after.Health <= s.MinHealthFraction
                            || before.Health - after.Health >= s.HealthDropFraction;
                        // Colony mode stops on any worsening -- except for a
                        // colonist who already caused a colonist_injury stop
                        // inside the cooldown, or who was acknowledged. A wolf
                        // scratching a pawn every few seconds must not turn each
                        // restart into another ten-second epoch. Threshold
                        // severity, downs and deaths still stop.
                        // A known wound bleeding on (blood loss or severity
                        // creeping) is not news: the tend alert already stands
                        // and a down still stops. Only a NEW wound or NEW
                        // bleeding is a colony-mode stop. Live 2026-09-06: two
                        // bleeding pawns re-fired this every few seconds with
                        // "injuries 3 -> 3, bleed 0.80 -> 0.80".
                        var newWound = after.Count > before.Count
                            || after.BleedRate > before.BleedRate + 0.001f;
                        var owedMs = InjuryCooldownRemainingMs(p.thingIDNumber, s.InjuryStopCooldownMs);
                        var acknowledged = s.IgnoredInjured.Contains(p.thingIDNumber);
                        var suppressed = !threshold && (owedMs > 0 || acknowledged);
                        payload["suppressed"] = suppressed;
                        payload["suppressedBy"] = suppressed ? (acknowledged ? "acknowledged" : "cooldown") : null;
                        payload["cooldownRemainingMs"] = owedMs;
                        payload["newWound"] = newWound;
                        if (((s.Mode == "colony" && newWound) || threshold) && !suppressed)
                        {
                            RecordInjuryStop(p);
                            return new Hit("colonist_injury", InjuryDetail(s, p, before, after), payload);
                        }
                        Add("injury_observed", HomePlayUntilEventTools.SafeName(p)
                            + (suppressed
                                ? (acknowledged
                                    ? " took another injury; play continues (acknowledged with --allow-injured)."
                                    : " took another injury; play continues (within the "
                                        + (s.InjuryStopCooldownMs / 1000) + " s injury-stop cooldown).")
                                : (newWound
                                    ? " took a sub-threshold combat injury; play continues."
                                    : " has a known wound worsening (blood loss/severity creep); play continues.")), s, payload);
                        // Both modes deliberately coalesce ordinary damage into
                        // durable observations instead of pause storms.
                        after.Health = before.Health; // retain start-of-session health baseline
                        s.Injuries[p.thingIDNumber] = after;
                    }
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
                    if (hostile || (p.Faction != Faction.OfPlayer && p.RaceProps != null
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

        /// One line, because it goes on screen: what changed and what to do.
        private static string InjuryDetail(State s, Pawn p, InjurySnapshot before, InjurySnapshot after)
        {
            var name = HomePlayUntilEventTools.SafeName(p);
            var seconds = s.InjuryStopCooldownMs / 1000;
            var advice = seconds > 0
                ? "a restart within " + seconds + " s will not stop again on minor injuries for "
                    + name + ", or pass --allow-injured " + p.thingIDNumber + "."
                : "pass --allow-injured " + p.thingIDNumber + " to acknowledge the wound.";
            return name + " was injured (injuries " + before.Count + " -> " + after.Count
                + ", bleed " + Num(before.BleedRate) + " -> " + Num(after.BleedRate)
                + "/day, blood loss " + Num(before.BloodLoss) + " -> " + Num(after.BloodLoss)
                + ", health " + Num(after.Health) + "). If this repeats, break the contact: "
                + "move them away or undraft them so they seek care; " + advice;
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
            public HashSet<int> IgnoredInjured; public int InjuryStopCooldownMs;
            public readonly List<Dictionary<string, object>> SuppressedInjuries = new List<Dictionary<string, object>>();
        }

        private sealed class InjuryStop
        {
            public long AtMs; public int Tick; public string Name;
        }
    }
}
