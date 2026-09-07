using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using RimBridgeServer.Sdk;
using RimWorld;
using Verse;
using UnityEngine;

namespace HomeBridge.BridgeTools
{
    /// <summary>
    /// home/play_until_event — run the game and stop at the moment something happens.
    ///
    /// Constraints that must survive an edit:
    ///
    /// * EXTERNAL CONTROL ALWAYS WINS. If curTimeSpeed becomes Paused and this
    ///   tool did not set it, the tool returns "external_pause" and does NOT
    ///   unpause. If the speed is changed under it, it returns "speed_changed"
    ///   and does NOT restore. It never sets DebugViewSettings.neverForceNormalSpeed
    ///   or UltraSpeedBoost, so nothing it does outlives the call.
    /// * It pauses AT MOST ONCE, on a watch hit, and only while curTimeSpeed is
    ///   not already Paused.
    /// * Companion tools dispatch with MarshalToMainThread = false. Every read of
    ///   game state happens inside one ctx.MainThread.InvokeAsync hop per poll.
    /// * There are THREE notification channels, not two. Letters persist, alerts
    ///   persist, and MESSAGES (the fading top-left text) expire 13 REAL seconds
    ///   after they appear. A watch that skips messages loses every message-only
    ///   event with no trace and no way to recover it afterwards.
    /// * Alert state is READ, never recalculated. AlertsReadout.activeAlerts is
    ///   maintained by RimWorld's own per-frame readout cycle; Alert.Active,
    ///   Alert.Label and Alert.Priority are plain field reads (verified against
    ///   Assembly-CSharp 1.6.9676.17735: no Alert subclass overrides Priority,
    ///   and none of the three getters stores anything). Alert.Recalculate() and
    ///   Alert.GetReport() are the recalculation path and are never called.
    /// * GameCondition.TicksLeft and .Duration call Log.ErrorOnce on a PERMANENT
    ///   condition, and Log.Error calls TickManager.Pause(). Reading either
    ///   without checking Permanent first would pause the game as a side effect.
    /// * SummaryHealthPercent memoises into cachedSummaryHealthPercent when
    ///   dirty. That is the only write this tool can cause, it is a cache the UI
    ///   refreshes every frame anyway, and it happens only when healthBelowPct
    ///   is set above 0.
    /// </summary>
    public sealed class HomePlayUntilEventTools
    {
        private const string ToolName = "home/play_until_event";
        private const int MaxBudgetMs = 600000;
        private const int MinPollMs = 50;
        private const int MaxPollMs = 5000;

        // Input and chatter types. Everything else — including a type this build
        // has never seen — stops the run, so a new notification kind fails loud.
        private const string DefaultIgnoredMessageTypes = "RejectInput,CautionInput,SilentInput,TaskCompletion";

        private static readonly FieldInfo ActiveAlertsField =
            BridgeCommon.PrivateInstanceField(typeof(AlertsReadout), "activeAlerts");

        // ------------------------------------------------------------------
        // The alert debounce memo. See ActiveAlertKeys and NormalizeAlertLabel
        // for WHY this is needed; decompiled against Assembly-CSharp
        // 1.6.9676.17735 after two `advance 20` pulses burned ~0 game time each
        // on a break-risk alert that had been standing for minutes.
        //
        // Two independent causes, both real:
        //
        //  (a) THE KEY WAS UNSTABLE. RimWorld.BreakRiskAlertUtility.AlertLabel
        //      appends " x" + count when more than one colonist qualifies:
        //      "Mood break risk: minor" and "Mood break risk: minor x3" are the
        //      same alert with two different labels, so the entry baseline held
        //      one and the poll saw the other. NormalizeAlertLabel strips that
        //      suffix out of the key (never out of the reported label).
        //
        //  (b) THE ALERT GENUINELY FLAPS. AlertsReadout.AlertsReadoutUpdate
        //      re-checks 1/24th of the alert list per FRAME (AlertCycleLength
        //      24, ~0.4 s at 60 fps) and CheckAddOrRemoveAlert adds or removes
        //      with NO hysteresis and no minimum lifetime. The predicate under
        //      it is MentalBreaker.BreakMinorIsImminent, a strict `<` of the
        //      pawn's instantaneous Need_Mood.CurLevel against
        //      BreakThresholdMinor -- also with no hysteresis. A colonist whose
        //      mood drifts across the threshold takes the alert out of
        //      activeAlerts and puts it back within half a second, so an entry
        //      baseline taken in a trough legitimately does not contain an
        //      alert that has been on screen for minutes.
        //
        // Fixing the key alone would not have fixed the reported symptom. This
        // memo is the part that does: it remembers, ACROSS calls in one game
        // process, when each alert key last counted -- the Python side has no
        // memory between pulses.
        private static readonly object AlertMemoLock = new object();

        private static readonly Dictionary<string, long> AlertLastCountedMs =
            new Dictionary<string, long>(StringComparer.Ordinal);

        // Monotonic. DateTime.UtcNow moves when the OS clock does; this does not.
        private static readonly Stopwatch AlertMemoClock = Stopwatch.StartNew();

        // What the memo is about. A different Game object, or a tick that went
        // backwards, means a different loaded colony and a memo that is lying.
        private static object _alertMemoGame;
        private static int _alertMemoTick = -1;

        private static readonly FieldInfo LiveMessagesField =
            BridgeCommon.PrivateStaticField(typeof(Messages), "liveMessages");

        // Two of these loops at once would fight over the clock. One at a time.
        private static int _running;

        internal static bool ShortGuardRunning { get { return Volatile.Read(ref _running) != 0; } }

        [Tool(
            ToolName,
            Title = "Play until an event fires, then pause",
            Description =
                "Unpause at a requested speed and poll RimWorld server-side until something happens, then pause and "
                + "report what stopped the clock. Watches all three notification channels — letters, transient "
                + "messages, and alerts — plus hostile pawns, predators on a hunt, colonists newly downed, and a "
                + "colonist health threshold. Opt-in watched-pawn combat edges cover melee intent, order changes and "
                + "coalesced injury changes, with a hard duration budget. External control always wins: an outside "
                + "pause or speed change ends the call immediately and is never overridden.",
            ResultDescription =
                "success, stopReason, stopDetail, event, ticksElapsed, elapsedMs, the watch settings actually used, "
                + "the baseline that new-since-entry is measured against, and per-check timing.")]
        [ToolResponse("stopReason", "string", "What stopped the clock. Always present.", Always = true)]
        [ToolResponse("stopDetail", "string", "One line naming the specific thing. Always present.", Always = true)]
        [ToolResponse("unknownArguments", "array", "Every argument key the caller sent that this tool does not declare, sorted, case-sensitively. Empty array = every key was recognised. The host's own _rimBridgeTimeoutMs is never listed.", Always = true)]
        [ToolResponse("unknownArgumentsWarning", "string", "Present only when unknownArguments is non-empty, or when the caller's raw keys could not be read at all - in which case the empty unknownArguments means 'not known', not 'nothing unknown'. On this tool an ignored watch flag means the run did not stop for something the caller asked it to stop for.", Nullable = true)]
        [ToolResponse("alertsDebounced", "array", "Alerts that were active and NOT in the entry baseline but were suppressed by alertDebounceMs instead of stopping the run: alertKey (the stable type|priority|normalized-label key), alertType, label (what the game says right now), priority, msSinceLastCounted and pollsDebounced. Run-long and deduplicated by key. Empty array when alertDebounceMs is 0 or nothing was suppressed - a suppressed alert is NEVER a silent drop.", Always = true)]
        [ToolResponse("alertsDebouncedCount", "integer", "Rows in alertsDebounced[]. 0 with a stop reason of 'alert' means the alert that stopped the run was genuinely new.", Always = true)]
        [ToolResponse("meleeThreats", "array", "AttackMelee intents targeting watched pawns found at the stopping poll. Empty unless watchMeleeThreats is enabled and fires.", Always = true)]
        [ToolResponse("pawnOrderChanges", "array", "Watched pawn job/target signatures that differ from entry. Empty unless watchPawnOrders is enabled and fires.", Always = true)]
        [ToolResponse("pawnInjuries", "array", "Meaningful coalesced watched-pawn injury changes found in the stopping poll. Empty unless watchInjuries is enabled and fires.", Always = true)]
        [ToolResponse("injuryHookEvent", "object", "The first event-driven injury applied to a watched pawn. Null unless watchInjuryHook fires.", Always = true, Nullable = true)]
        [ToolResponse("combatCamera", "object", "Combat camera result including enabled, framesApplied, manualOverride and errors. Manual pan or zoom suppresses directing for combatCameraManualSuppressMs, renewed by further input; the camera never fails the call.", Always = true)]
        public async Task<object> PlayUntilEvent(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            [ToolParameter(Description = "Play speed to run at: Normal, Fast, Superfast, Ultrafast. Paused is rejected.", DefaultValue = "Superfast")] string speed = "Superfast",
            [ToolParameter(Description = "Hard duration budget in real milliseconds. Clamped to 600000. The bridge runs one tool call at a time, so any other bridge call queues for this whole window: keep it short and chain calls with pauseOnBudget:false.", DefaultValue = 5000)] int maxDurationMs = 5000,
            [ToolParameter(Description = "Server-side poll interval in real milliseconds, clamped to 50..5000.", DefaultValue = 250)] int pollIntervalMs = 250,
            [ToolParameter(Description = "Minimum real milliseconds between alert checks. 0 = check alerts on every poll. Raised only if alert evaluation proves expensive; the reported timing block says what it costs.", DefaultValue = 0)] int alertIntervalMs = 0,
            [ToolParameter(Description = "Stop on a letter that was not on the stack at entry.", DefaultValue = true)] bool watchLetters = true,
            [ToolParameter(Description = "Stop on an alert that becomes active after entry. Alerts already active at entry are listed in baseline.alerts and never fire.", DefaultValue = true)] bool watchAlerts = true,
            [ToolParameter(Description = "Treat an already-active qualifying alert as an immediate stop instead of baselining it.", DefaultValue = false)] bool stopOnCurrentAlerts = false,
            [ToolParameter(Description = "Comma-separated exact alert labels explicitly acknowledged for this call. Only matching current alerts are baselined; every other current or new qualifying alert still stops.", DefaultValue = "")] string ignoredAlertLabels = "",
            [ToolParameter(Description = "Stop on a new transient message — the fading top-left text, RimWorld's third notification channel. It carries events that are neither letters nor alerts (a game condition ending, a plan failing) and it evaporates after 13 real seconds, so nothing that misses it can recover it later.", DefaultValue = true)] bool watchMessages = true,
            [ToolParameter(Description = "Carry-over watermark from the preceding pulse's endTick. A still-live message whose startingTick is newer than this is reported at entry instead of swallowed into the new baseline. -1 baselines all current messages.", DefaultValue = -1)] int messageSinceTick = -1,
            [ToolParameter(Description = "Comma-separated MessageTypeDef names that may NOT stop the run. Everything else stops, including message types this build has never seen. Empty string = nothing is ignored. Ignored messages are still listed in the payload with stopped:false.", DefaultValue = DefaultIgnoredMessageTypes)] string messageTypesIgnored = DefaultIgnoredMessageTypes,
            [ToolParameter(Description = "Lowest alert priority that counts: Medium, High, or Critical.", DefaultValue = "High")] string minAlertPriority = "High",
            [ToolParameter(Description = "Real milliseconds an alert key stays suppressed after it last counted. 0 = today's behaviour, no memory. The memo is STATIC and survives across calls in the same game process, which is the point: RimWorld re-checks each alert every 24 frames and adds/removes it with no hysteresis (AlertsReadout.CheckAddOrRemoveAlert over MentalBreaker.BreakMinorIsImminent, a strict mood comparison), so a break-risk alert that has been on screen for minutes can be absent from ONE call's entry baseline and stop the next pulse dead. The stamp refreshes on every poll the key is still standing, so a continuously standing alert stays suppressed while it stands plus this many ms. Suppressed alerts are reported in alertsDebounced[]. Reset when the loaded game changes.", DefaultValue = 0)] int alertDebounceMs = 0,
            [ToolParameter(Description = "Stop when any pawn hostile to the player (hostile faction or manhunter) is on the map, at any range.", DefaultValue = true)] bool watchHostiles = true,
            [ToolParameter(Description = "Do not stop for hostiles that are already on the map at entry; only newly arrived ones. Their ids are reported in baseline.", DefaultValue = false)] bool ignoreCurrentHostiles = false,
            [ToolParameter(Description = "Comma-separated stable Thing IDs of known-contained hostiles that may be baselined. Unlike ignoreCurrentHostiles, this cannot suppress an unrelated arrival in the gap between pulses.", DefaultValue = "")] string ignoredHostileIds = "",
            [ToolParameter(Description = "Stop on a predator running a PredatorHunt job within this many cells of a colonist. 0 = never.", DefaultValue = 40)] int huntWithin = 40,
            [ToolParameter(Description = "Stop when a colonist becomes downed or dead who was not at entry.", DefaultValue = true)] bool watchDownedColonists = true,
            [ToolParameter(Description = "Treat an already-downed or dead colonist as an immediate stop instead of baselining them.", DefaultValue = false)] bool stopOnCurrentDownedColonists = false,
            [ToolParameter(Description = "Comma-separated stable Thing IDs of known downed colonists explicitly acknowledged for this call. Other current or newly downed colonists still stop.", DefaultValue = "")] string ignoredDownedColonistIds = "",
            [ToolParameter(Description = "Stop when any colonist's summary health falls below this percentage. 0 = never check. Reading it refreshes RimWorld's own health cache.", DefaultValue = 0)] int healthBelowPct = 0,
            [ToolParameter(Description = "Comma-separated stable pawn Thing IDs to use for the opt-in combat watches. Accepts 123 or Pawn_Name123; empty means no combat pawn is watched.", DefaultValue = "")] string watchedPawnIds = "",
            [ToolParameter(Description = "Comma-separated stable pawn Thing IDs eligible for watchMeleeThreats. When empty, watchedPawnIds is the compatibility fallback.", DefaultValue = "")] string meleeThreatPawnIds = "",
            [ToolParameter(Description = "Comma-separated stable pawn Thing IDs eligible for watchInjuries. When empty, watchedPawnIds is the compatibility fallback.", DefaultValue = "")] string injuryPawnIds = "",
            [ToolParameter(Description = "Stop when a hostile pawn with an AttackMelee job targeting a watched pawn crosses into meleeThreatWithin. Only already-in-range pairs are baselined, so a chase active at entry can fire later.", DefaultValue = false)] bool watchMeleeThreats = false,
            [ToolParameter(Description = "Maximum Chebyshev attacker-to-target distance for watchMeleeThreats. Default 1 means adjacent/striking range; clamped to 1..100. A distant AttackMelee chase does not stop until it enters this radius.", DefaultValue = 1)] int meleeThreatWithin = 1,
            [ToolParameter(Description = "Stop when a watched pawn's current job or any of its first three targets changes from the entry snapshot (including completion/replacement).", DefaultValue = false)] bool watchPawnOrders = false,
            [ToolParameter(Description = "Stop on meaningful aggregate injury severity/bleed-rate growth to a watched pawn at the configured thresholds. Changes in one poll are coalesced; newly created injuries alone stop only with injuryStopOnNew.", DefaultValue = false)] bool watchInjuries = false,
            [ToolParameter(Description = "Synchronously pause from the damage-application path when an actual injury is added to an injuryPawnIds pawn. Event-driven: no polling or pawn scans, and disarmed when this call ends.", DefaultValue = false)] bool watchInjuryHook = false,
            [ToolParameter(Description = "Include verbose injury-hook target, Harmony-owner and invocation-counter diagnostics. Normally false to keep combat responses compact.", DefaultValue = false)] bool injuryHookDebug = false,
            [ToolParameter(Description = "With watchInjuries, make creation of any new Hediff_Injury sufficient to stop. False avoids combat pause storms: subthreshold wounds accumulate against the entry baseline until a severity or bleed threshold is crossed.", DefaultValue = false)] bool injuryStopOnNew = false,
            [ToolParameter(Description = "Minimum aggregate Hediff_Injury severity increase that counts when watchInjuries is enabled, measured against the entry baseline. Severity is HIT POINTS (a gunshot is roughly 10-18, a scratch 2-5). Clamped to 0..10000; 0 disables this threshold.", DefaultValue = 0.01)] float injuryMinSeverityDelta = 0.01f,
            [ToolParameter(Description = "Minimum aggregate injury bleed-rate increase (RimWorld's per-day fraction) that counts when watchInjuries is enabled. Clamped to 0..10000; 0 disables this threshold.", DefaultValue = 0.001)] float injuryMinBleedRateDelta = 0.001f,
            [ToolParameter(Description = "Opt in to a server-side combat camera director for this call only.", DefaultValue = false)] bool combatCamera = false,
            [ToolParameter(Description = "Comma-separated stable pawn Thing IDs to frame when drafted. Empty disables the combat camera.", DefaultValue = "")] string combatCameraPawnIds = "",
            [ToolParameter(Description = "Real milliseconds between combat reframes, clamped to 1000..60000.", DefaultValue = 8000)] int combatCameraIntervalMs = 8000,
            [ToolParameter(Description = "Include hostiles within this Euclidean distance of a framed pawn.", DefaultValue = 20f)] float combatCameraHostileRadius = 20f,
            [ToolParameter(Description = "Compact zoom used only when maximum relevant-pawn span is strictly below combatCameraCompactSpan.", DefaultValue = 13.3315439f)] float combatCameraCompactRootSize = 13.3315439f,
            [ToolParameter(Description = "Wide zoom floor for all other combat arrangements.", DefaultValue = 25.5292435f)] float combatCameraWideRootSize = 25.5292435f,
            [ToolParameter(Description = "Strict maximum span for compact combat framing.", DefaultValue = 15f)] float combatCameraCompactSpan = 15f,
            [ToolParameter(Description = "Extra map cells around combat bounds.", DefaultValue = 5)] int combatCameraMargin = 5,
            [ToolParameter(Description = "UTC Unix-millisecond timestamp of the last camera frame applied by a preceding combat pulse. 0 means none.", DefaultValue = 0L)] long combatCameraLastFrameUnixMs = 0L,
            [ToolParameter(Description = "Minimum real milliseconds between applied combat camera frames, including across calls.", DefaultValue = 3500)] int combatCameraCooldownMs = 3500,
            [ToolParameter(Description = "UTC Unix-millisecond time until automatic framing remains suppressed after manual camera input.", DefaultValue = 0L)] long combatCameraManualSuppressUntilUnixMs = 0L,
            [ToolParameter(Description = "Real milliseconds of renewable suppression after each detected manual pan or zoom.", DefaultValue = 20000)] int combatCameraManualSuppressMs = 20000,
            [ToolParameter(Description = "Pause when the budget runs out. False leaves the game running so calls can be chained without a stutter; a watch hit always pauses.", DefaultValue = true)] bool pauseOnBudget = true,
            [ToolParameter(Description = "Refuse to start if the game is already paused, returning external_pause instead of unpausing. A caller chaining calls with pauseOnBudget:false must set this, or the next call will undo somebody else's pause.", DefaultValue = false)] bool requireRunningAtEntry = false)
        {
            return BridgeCommon.WithUnknownArguments(
                await PlayUntilEventCore(
                    ctx, cancellationToken, speed, maxDurationMs, pollIntervalMs, alertIntervalMs,
                    watchLetters, watchAlerts, stopOnCurrentAlerts, ignoredAlertLabels, watchMessages, messageSinceTick, messageTypesIgnored, minAlertPriority, alertDebounceMs,
                    watchHostiles, ignoreCurrentHostiles, ignoredHostileIds, huntWithin, watchDownedColonists,
                    stopOnCurrentDownedColonists, ignoredDownedColonistIds, healthBelowPct, watchedPawnIds, meleeThreatPawnIds, injuryPawnIds,
                    watchMeleeThreats, meleeThreatWithin, watchPawnOrders, watchInjuries, watchInjuryHook, injuryHookDebug, injuryStopOnNew,
                    injuryMinSeverityDelta, injuryMinBleedRateDelta, combatCamera, combatCameraPawnIds,
                    combatCameraIntervalMs, combatCameraHostileRadius, combatCameraCompactRootSize,
                    combatCameraWideRootSize, combatCameraCompactSpan, combatCameraMargin,
                    combatCameraLastFrameUnixMs, combatCameraCooldownMs,
                    combatCameraManualSuppressUntilUnixMs, combatCameraManualSuppressMs,
                    pauseOnBudget, requireRunningAtEntry).ConfigureAwait(false),
                ctx, typeof(HomePlayUntilEventTools), ToolName);
        }

        private async Task<object> PlayUntilEventCore(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            string speed,
            int maxDurationMs,
            int pollIntervalMs,
            int alertIntervalMs,
            bool watchLetters,
            bool watchAlerts,
            bool stopOnCurrentAlerts,
            string ignoredAlertLabels,
            bool watchMessages,
            int messageSinceTick,
            string messageTypesIgnored,
            string minAlertPriority,
            int alertDebounceMs,
            bool watchHostiles,
            bool ignoreCurrentHostiles,
            string ignoredHostileIds,
            int huntWithin,
            bool watchDownedColonists,
            bool stopOnCurrentDownedColonists,
            string ignoredDownedColonistIds,
            int healthBelowPct,
            string watchedPawnIds,
            string meleeThreatPawnIds,
            string injuryPawnIds,
            bool watchMeleeThreats,
            int meleeThreatWithin,
            bool watchPawnOrders,
            bool watchInjuries,
            bool watchInjuryHook,
            bool injuryHookDebug,
            bool injuryStopOnNew,
            float injuryMinSeverityDelta,
            float injuryMinBleedRateDelta,
            bool combatCamera,
            string combatCameraPawnIds,
            int combatCameraIntervalMs,
            float combatCameraHostileRadius,
            float combatCameraCompactRootSize,
            float combatCameraWideRootSize,
            float combatCameraCompactSpan,
            int combatCameraMargin,
            long combatCameraLastFrameUnixMs,
            int combatCameraCooldownMs,
            long combatCameraManualSuppressUntilUnixMs,
            int combatCameraManualSuppressMs,
            bool pauseOnBudget,
            bool requireRunningAtEntry)
        {
            if (ctx?.MainThread == null)
                return Failure("No RimBridge main-thread dispatcher is available for this invocation.", "error");

            if (Supervisor.IsActive)
                return Failure("A supervised_play session is active; use its status/pause API instead of starting a competing clock guard.", "busy");

            if (Interlocked.CompareExchange(ref _running, 1, 0) != 0)
                return Failure("Another " + ToolName + " call is already running; two loops would fight over the clock.", "busy");

            try
            {
                if (watchInjuryHook)
                {
                    try
                    {
                        var hookIds = ParsePawnIds(injuryPawnIds);
                        CombatInjuryHook.Arm(hookIds.Count > 0 ? hookIds : ParsePawnIds(watchedPawnIds));
                    }
                    catch (Exception ex)
                    {
                        var failure = (Dictionary<string, object>)Failure(
                            "Injury hook could not be verified; refusing to run the clock: " + ex.Message, "error");
                        failure["injuryHookStatus"] = CombatInjuryHook.Status();
                        return failure;
                    }
                }
                return await RunLoop(ctx, cancellationToken, speed, maxDurationMs, pollIntervalMs, alertIntervalMs,
                    watchLetters, watchAlerts, stopOnCurrentAlerts, ignoredAlertLabels, watchMessages, messageSinceTick, messageTypesIgnored, minAlertPriority, alertDebounceMs,
                    watchHostiles, ignoreCurrentHostiles, ignoredHostileIds, huntWithin,
                    watchDownedColonists, stopOnCurrentDownedColonists, ignoredDownedColonistIds, healthBelowPct, watchedPawnIds, meleeThreatPawnIds, injuryPawnIds, watchMeleeThreats,
                    meleeThreatWithin,
                    watchPawnOrders, watchInjuries, watchInjuryHook, injuryHookDebug, injuryStopOnNew, injuryMinSeverityDelta, injuryMinBleedRateDelta,
                    combatCamera, combatCameraPawnIds, combatCameraIntervalMs, combatCameraHostileRadius,
                    combatCameraCompactRootSize, combatCameraWideRootSize, combatCameraCompactSpan,
                    combatCameraMargin, combatCameraLastFrameUnixMs, combatCameraCooldownMs,
                    combatCameraManualSuppressUntilUnixMs, combatCameraManualSuppressMs,
                    pauseOnBudget, requireRunningAtEntry).ConfigureAwait(false);
            }
            finally
            {
                CombatInjuryHook.Disarm();
                Interlocked.Exchange(ref _running, 0);
            }
        }

        private async Task<object> RunLoop(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            string speed,
            int maxDurationMs,
            int pollIntervalMs,
            int alertIntervalMs,
            bool watchLetters,
            bool watchAlerts,
            bool stopOnCurrentAlerts,
            string ignoredAlertLabels,
            bool watchMessages,
            int messageSinceTick,
            string messageTypesIgnored,
            string minAlertPriority,
            int alertDebounceMs,
            bool watchHostiles,
            bool ignoreCurrentHostiles,
            string ignoredHostileIds,
            int huntWithin,
            bool watchDownedColonists,
            bool stopOnCurrentDownedColonists,
            string ignoredDownedColonistIds,
            int healthBelowPct,
            string watchedPawnIds,
            string meleeThreatPawnIds,
            string injuryPawnIds,
            bool watchMeleeThreats,
            int meleeThreatWithin,
            bool watchPawnOrders,
            bool watchInjuries,
            bool watchInjuryHook,
            bool injuryHookDebug,
            bool injuryStopOnNew,
            float injuryMinSeverityDelta,
            float injuryMinBleedRateDelta,
            bool combatCamera,
            string combatCameraPawnIds,
            int combatCameraIntervalMs,
            float combatCameraHostileRadius,
            float combatCameraCompactRootSize,
            float combatCameraWideRootSize,
            float combatCameraCompactSpan,
            int combatCameraMargin,
            long combatCameraLastFrameUnixMs,
            int combatCameraCooldownMs,
            long combatCameraManualSuppressUntilUnixMs,
            int combatCameraManualSuppressMs,
            bool pauseOnBudget,
            bool requireRunningAtEntry)
        {
            var notes = new List<string>();

            TimeSpeed requested;
            if (!Enum.TryParse(speed ?? string.Empty, true, out requested))
                return Failure("Unknown time speed '" + speed + "'. Use Normal, Fast, Superfast or Ultrafast.", "error");
            if (requested == TimeSpeed.Paused)
                return Failure(ToolName + " requires a play speed. Paused is not one.", "error");

            AlertPriority minPriority;
            if (!Enum.TryParse(minAlertPriority ?? string.Empty, true, out minPriority))
                return Failure("Unknown alert priority '" + minAlertPriority + "'. Use Medium, High or Critical.", "error");

            var budgetMs = Clamp(maxDurationMs, 0, MaxBudgetMs);
            if (budgetMs != maxDurationMs)
                notes.Add("maxDurationMs clamped from " + maxDurationMs + " to " + budgetMs + ".");
            var pollMs = Clamp(pollIntervalMs, MinPollMs, MaxPollMs);
            if (pollMs != pollIntervalMs)
                notes.Add("pollIntervalMs clamped from " + pollIntervalMs + " to " + pollMs + ".");
            var alertMs = Math.Max(0, alertIntervalMs);

            var generalIds = ParsePawnIds(watchedPawnIds);
            var meleeIds = ParsePawnIds(meleeThreatPawnIds);
            var injuryIds = ParsePawnIds(injuryPawnIds);
            var cameraIds = ParsePawnIds(combatCameraPawnIds);
            if (meleeIds.Count == 0) meleeIds.UnionWith(generalIds);
            if (injuryIds.Count == 0) injuryIds.UnionWith(generalIds);
            var watch = new Watch
            {
                Letters = watchLetters,
                Alerts = watchAlerts && ActiveAlertsField != null,
                StopOnCurrentAlerts = stopOnCurrentAlerts,
                IgnoredAlertLabels = ParseCsv(ignoredAlertLabels),
                MinPriority = minPriority,
                AlertDebounceMs = Math.Max(0, alertDebounceMs),
                Messages = watchMessages && LiveMessagesField != null,
                MessageSinceTick = messageSinceTick,
                IgnoredMessageTypes = ParseCsv(messageTypesIgnored),
                Hostiles = watchHostiles,
                IgnoredHostileIds = ParsePawnIds(ignoredHostileIds),
                HuntWithin = Math.Max(0, huntWithin),
                Downed = watchDownedColonists,
                StopOnCurrentDowned = stopOnCurrentDownedColonists,
                IgnoredDownedColonistIds = ParsePawnIds(ignoredDownedColonistIds),
                HealthBelowPct = Clamp(healthBelowPct, 0, 100),
                WatchedPawnIds = generalIds,
                MeleeThreatPawnIds = meleeIds,
                InjuryPawnIds = injuryIds,
                MeleeThreats = watchMeleeThreats,
                MeleeThreatWithin = Clamp(meleeThreatWithin, 1, 100),
                PawnOrders = watchPawnOrders,
                Injuries = watchInjuries,
                InjuryHook = watchInjuryHook,
                InjuryHookDebug = injuryHookDebug,
                InjuryStopOnNew = injuryStopOnNew,
                // Hit points, not a 0..1 fraction: a 0..1 clamp (the first
                // version) put the strictest threshold below the smallest wound.
                InjuryMinSeverityDelta = ClampFloat(injuryMinSeverityDelta, 0f, 10000f),
                InjuryMinBleedRateDelta = ClampFloat(injuryMinBleedRateDelta, 0f, 10000f)
            };
            watch.Camera = new CombatCameraState
            {
                Enabled = combatCamera && cameraIds.Count > 0,
                PawnIds = cameraIds,
                IntervalMs = Clamp(combatCameraIntervalMs, 1000, 60000),
                HostileRadius = Math.Max(0f, combatCameraHostileRadius),
                CompactRootSize = Math.Max(1f, combatCameraCompactRootSize),
                WideRootSize = Math.Max(1f, combatCameraWideRootSize),
                CompactSpan = Math.Max(0f, combatCameraCompactSpan),
                Margin = Clamp(combatCameraMargin, 0, 50),
                LastFrameUnixMs = Math.Max(0L, combatCameraLastFrameUnixMs),
                CooldownMs = Clamp(combatCameraCooldownMs, 0, 60000),
                ManualSuppressUntilUnixMs = Math.Max(0L, combatCameraManualSuppressUntilUnixMs),
                ManualSuppressMs = Clamp(combatCameraManualSuppressMs, 0, 60000)
            };
            if ((watchPawnOrders && generalIds.Count == 0)
                || (watchMeleeThreats && meleeIds.Count == 0)
                || ((watchInjuries || watchInjuryHook) && injuryIds.Count == 0))
                notes.Add("ONE OR MORE COMBAT WATCHES INACTIVE: its applicable pawn-ID list contained no readable Thing IDs.");
            if (watchInjuryHook && injuryHookDebug)
            {
                var hookStatus = CombatInjuryHook.Status();
                notes.Add("Injury hook armed=" + hookStatus["armed"] + ", installed="
                          + hookStatus["patchInstalled"] + ", target=" + hookStatus["target"] + ".");
            }
            if (watchAlerts && ActiveAlertsField == null)
                return Failure("Alert watcher is unavailable on this build; refusing to run the clock blind.", "unavailable");
            if (watchMessages && LiveMessagesField == null)
                return Failure("Transient-message watcher is unavailable on this build; refusing to run the clock blind.", "unavailable");
            if (budgetMs > 15000)
                notes.Add("Long budget: the bridge runs one tool call at a time, so any other bridge call (a pause from "
                          + "another session) waits up to " + budgetMs + " ms. A key press in the game window is not affected.");

            var clock = Stopwatch.StartNew();
            var timing = new Timing();
            Baseline baseline;

            try
            {
                baseline = await ctx.MainThread
                    .InvokeAsync(() => CaptureBaseline(watch, ignoreCurrentHostiles, requireRunningAtEntry), cancellationToken)
                    .ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                return Failure("Could not read the game state to start from: " + ex.Message, "error");
            }

            if (baseline.Unavailable != null)
                return Failure(baseline.Unavailable, "unavailable");

            if (baseline.RefusedBecausePaused)
                return Respond(true, "external_pause",
                    "The game was already paused and requireRunningAtEntry is set. Nothing was unpaused.",
                    null, baseline, baseline.EntryProbe, requested, clock, timing, watch, budgetMs, pollMs, alertMs,
                    false, null, true, notes, null);

            // Level-triggered watches can already be satisfied before a single
            // tick runs. Say so and never touch the clock.
            var entryHit = FirstHit(baseline.EntryProbe, watch);
            if (entryHit != null)
            {
                // A real answer, not a failure: success stays true so a strict
                // caller does not turn "a raider is standing there" into a throw.
                return Respond(true, entryHit.Reason, "Already true at entry, before the clock was touched: " + entryHit.Detail,
                    entryHit.Payload, baseline, baseline.EntryProbe, requested, clock, timing, watch, budgetMs, pollMs, alertMs,
                    pausedByTool: false, pauseVerified: null, hitAtEntry: true, notes: notes,
                    error: null);
            }

            // Start the clock. CurTimeSpeed is set directly and once: no
            // neverForceNormalSpeed, no UltraSpeedBoost, nothing to restore.
            string startError = null;
            TimeSpeed startedAt = TimeSpeed.Paused;
            try
            {
                startedAt = await ctx.MainThread.InvokeAsync(() =>
                {
                    var tm = Find.TickManager;
                    if (tm == null)
                        return TimeSpeed.Paused;
                    tm.CurTimeSpeed = requested;
                    return tm.CurTimeSpeed;
                }, cancellationToken).ConfigureAwait(false);
            }
            catch (Exception ex)
            {
                startError = "Setting the play speed threw: " + ex.Message;
            }

            if (startError == null && startedAt != requested)
                startError = "Asked for speed " + requested + " and the game reports " + startedAt
                             + ". TickManager.set_CurTimeSpeed refuses when PlayerCanControl is false, so nothing ran.";

            if (startError != null)
            {
                var s = await SafeProbe(ctx, baseline, watch, true, timing, cancellationToken).ConfigureAwait(false);
                return Respond(false, "error", startError, null, baseline, s, requested, clock, timing, watch,
                    budgetMs, pollMs, alertMs, false, null, false, notes, startError);
            }

            Probe last = baseline.EntryProbe;
            Hit hit = null;
            string reason = "budget_elapsed";
            string detail = "Budget of " + budgetMs + " ms elapsed with nothing watched firing.";
            var lastAlertCheck = -1L;

            while (true)
            {
                var remaining = budgetMs - (int)clock.ElapsedMilliseconds;
                if (remaining <= 0)
                    break;

                try
                {
                    await Task.Delay(Math.Min(pollMs, remaining), cancellationToken).ConfigureAwait(false);
                }
                catch (OperationCanceledException)
                {
                    reason = "cancelled";
                    detail = "The caller cancelled the call. The game was left exactly as it was.";
                    return Respond(false, reason, detail, null, baseline, last, requested, clock, timing, watch,
                        budgetMs, pollMs, alertMs, false, null, false, notes, detail);
                }

                var checkAlerts = watch.Alerts
                                  && (alertMs <= 0 || lastAlertCheck < 0 || clock.ElapsedMilliseconds - lastAlertCheck >= alertMs);

                Probe probe;
                try
                {
                    probe = await ctx.MainThread
                        .InvokeAsync(() =>
                        {
                            var value = Snapshot(baseline, watch, checkAlerts, timing);
                            UpdateCombatCamera(watch.Camera, clock.ElapsedMilliseconds);
                            return value;
                        }, cancellationToken)
                        .ConfigureAwait(false);
                }
                catch (OperationCanceledException)
                {
                    reason = "cancelled";
                    detail = "The caller cancelled the call during a main-thread hop.";
                    return Respond(false, reason, detail, null, baseline, last, requested, clock, timing, watch,
                        budgetMs, pollMs, alertMs, false, null, false, notes, detail);
                }
                catch (Exception ex)
                {
                    reason = "error";
                    detail = "A main-thread hop failed: " + ex.Message;
                    return Respond(false, reason, detail, null, baseline, last, requested, clock, timing, watch,
                        budgetMs, pollMs, alertMs, false, null, false, notes, detail);
                }

                last = probe;
                if (checkAlerts)
                    lastAlertCheck = clock.ElapsedMilliseconds;

                if (!probe.Available)
                {
                    reason = "unavailable";
                    detail = probe.Unavailable ?? "The game stopped being playable.";
                    return Respond(false, reason, detail, null, baseline, probe, requested, clock, timing, watch,
                        budgetMs, pollMs, alertMs, false, null, false, notes, detail);
                }

                if (!ReferenceEquals(probe.Session, baseline.Session))
                {
                    reason = "session_changed";
                    detail = "The game session changed under the call (a load, a new colony, or a quit to menu).";
                    return Respond(false, reason, detail, null, baseline, probe, requested, clock, timing, watch,
                        budgetMs, pollMs, alertMs, false, null, false, notes, detail);
                }

                // Letters first, on purpose: RimWorld force-pauses ITSELF on a
                // threat letter (LetterDef.pauseMode), so the same instant looks
                // like both a new letter and an external pause. The letter is
                // the cause and the more useful answer.
                hit = FirstHit(probe, watch);
                if (hit != null)
                {
                    reason = hit.Reason;
                    detail = hit.Detail;
                    break;
                }

                if (probe.CurSpeed == TimeSpeed.Paused)
                {
                    // Somebody else stopped the clock. Yield; never re-unpause.
                    reason = "external_pause";
                    detail = "The game was paused by something outside this call. Not overridden, not resumed.";
                    return Respond(true, reason, detail, null, baseline, probe, requested, clock, timing, watch,
                        budgetMs, pollMs, alertMs, false, null, false, notes, null);
                }

                if (probe.ForcePaused)
                {
                    reason = "force_paused";
                    detail = "A modal window, long event or cutscene is force-pausing the game. Not overridden.";
                    return Respond(true, reason, detail, null, baseline, probe, requested, clock, timing, watch,
                        budgetMs, pollMs, alertMs, false, null, false, notes, null);
                }

                if (probe.CurSpeed != requested)
                {
                    reason = "speed_changed";
                    detail = "The play speed was changed to " + probe.CurSpeed + " by something outside this call. "
                             + "Not overridden, not restored.";
                    return Respond(true, reason, detail, null, baseline, probe, requested, clock, timing, watch,
                        budgetMs, pollMs, alertMs, false, null, false, notes, null);
                }
            }

            var wantPause = hit != null || pauseOnBudget;
            bool pausedByTool = false;
            bool? pauseVerified = null;

            if (wantPause)
            {
                try
                {
                    var result = await ctx.MainThread.InvokeAsync(() =>
                    {
                        var tm = Find.TickManager;
                        if (tm == null)
                            return new int[] { 0, 0 };
                        var already = tm.CurTimeSpeed == TimeSpeed.Paused ? 1 : 0;
                        if (already == 0)
                            tm.Pause();
                        return new int[] { already, tm.CurTimeSpeed == TimeSpeed.Paused ? 1 : 0 };
                    }, cancellationToken).ConfigureAwait(false);

                    pausedByTool = result[0] == 0 && result[1] == 1;
                    pauseVerified = result[1] == 1;
                }
                catch (Exception ex)
                {
                    pauseVerified = false;
                    notes.Add("The pause hop threw: " + ex.Message);
                }
            }

            // Watched combat edges often fire before the periodic interval.
            // Leave the paused screen on the event itself unless a human has
            // already taken camera control.
            if (hit != null)
            {
                try
                {
                    await ctx.MainThread.InvokeAsync(
                        () => UpdateCombatCamera(watch.Camera, clock.ElapsedMilliseconds,
                            forceFrame: true), cancellationToken).ConfigureAwait(false);
                }
                catch (Exception ex)
                {
                    notes.Add("Final combat camera frame failed: " + ex.Message);
                }
            }

            Probe final;
            try
            {
                final = await ctx.MainThread
                    .InvokeAsync(() => Snapshot(baseline, watch, false, timing), cancellationToken)
                    .ConfigureAwait(false);
            }
            catch
            {
                final = last;
            }

            string error = null;
            var ok = true;
            if (wantPause && pauseVerified != true)
            {
                ok = false;
                error = "PAUSE DID NOT TAKE. " + reason + " fired but TickManager.CurTimeSpeed is "
                        + (final != null ? final.CurSpeed.ToString() : "unreadable")
                        + ". TogglePaused refuses when PlayerCanControl is false. The game is still running.";
            }

            return Respond(ok, reason, detail, hit == null ? null : hit.Payload, baseline,
                MergeHitInto(final, last, hit), requested, clock, timing, watch, budgetMs, pollMs, alertMs,
                pausedByTool, pauseVerified, false, notes, error);
        }

        // ------------------------------------------------------------------
        // Main-thread work. Everything below CaptureBaseline/Snapshot runs
        // inside a ctx.MainThread.InvokeAsync hop and nowhere else.
        // ------------------------------------------------------------------

        private static Baseline CaptureBaseline(Watch watch, bool ignoreCurrentHostiles, bool requireRunningAtEntry)
        {
            var b = new Baseline();

            string why;
            if (!Playable(out why))
            {
                b.Unavailable = why;
                return b;
            }

            b.Session = Current.Game;
            var tm = Find.TickManager;
            b.StartTick = tm != null ? tm.TicksGame : 0;
            b.StartSpeed = tm != null ? tm.CurTimeSpeed : TimeSpeed.Paused;
            b.StartPaused = tm != null && tm.CurTimeSpeed == TimeSpeed.Paused;
            b.StartForcePaused = tm != null && tm.ForcePaused;

            if (b.StartForcePaused)
            {
                b.Unavailable = "A modal window, long event or cutscene is force-pausing the game; the clock cannot be started.";
                return b;
            }

            // Somebody else's pause is not this tool's to lift.
            b.RefusedBecausePaused = requireRunningAtEntry && b.StartPaused;

            // Only after the refusals: a call that runs no time moves no camera.
            if (!b.RefusedBecausePaused)
            {
                InitializeCombatCamera(watch.Camera);
                UpdateCombatCamera(watch.Camera, 0, forceFrame: true);
            }

            foreach (var letter in Letters())
                b.LetterIds.Add(SafeLetterId(letter));

            foreach (var message in LiveMessages())
            {
                // On chained calls, do not swallow a message that appeared in
                // the bridge gap after the preceding call's final snapshot.
                // Equal-tick messages may have arrived after the preceding
                // final snapshot. Reporting a possible duplicate is safer
                // than swallowing an event; IDs still dedupe within this call.
                if (watch.MessageSinceTick < 0 || SafeMessageTick(message) < watch.MessageSinceTick)
                    b.MessageIds.Add(SafeMessageId(message));
            }

            // The memo is about ONE loaded colony. Do this before anything is
            // read out of it, so a memo from the previous save can never
            // suppress an alert in this one.
            SyncAlertMemoToGame();

            foreach (var key in ActiveAlertKeys(watch.MinPriority, null))
            {
                if (!watch.StopOnCurrentAlerts || watch.IgnoredAlertLabels.Contains(key.Value))
                    b.AlertKeys.Add(key.Key);
                b.AlertLabels.Add(key.Value);
                // "Was baselined" counts as "counted": an alert standing at
                // entry must not fire on the NEXT call either.
                if ((!watch.StopOnCurrentAlerts || watch.IgnoredAlertLabels.Contains(key.Value))
                    && watch.AlertDebounceMs > 0)
                    StampAlert(key.Key);
            }

            var map = Find.CurrentMap;
            foreach (var pawn in SpawnedPawns(map))
            {
                if (pawn == null)
                    continue;
                if ((watch.PawnOrders && watch.WatchedPawnIds.Contains(pawn.thingIDNumber))
                    || (watch.Injuries && watch.InjuryPawnIds.Contains(pawn.thingIDNumber)))
                {
                    var state = CaptureCombatPawn(pawn);
                    b.WatchedPawns[pawn.thingIDNumber] = state;
                    b.WatchedPawnRows.Add(DescribeCombatBaseline(state));
                }
                if (SafeIsColonist(pawn))
                {
                    if (SafeDowned(pawn) || SafeDead(pawn))
                    {
                        if (!watch.StopOnCurrentDowned
                            || watch.IgnoredDownedColonistIds.Contains(pawn.thingIDNumber))
                            b.DownedColonists.Add(pawn.thingIDNumber);
                        b.DownedColonistNames.Add(SafeName(pawn));
                    }
                    continue;
                }
                string ignoredReason;
                if ((ignoreCurrentHostiles || watch.IgnoredHostileIds.Contains(pawn.thingIDNumber))
                    && IsHostile(pawn, out ignoredReason))
                {
                    b.IgnoredHostiles.Add(pawn.thingIDNumber);
                    b.IgnoredHostileNames.Add(SafeName(pawn) + " (" + ignoredReason + ")");
                }
            }

            if (watch.MeleeThreats && watch.MeleeThreatPawnIds.Count > 0)
            {
                foreach (var attacker in SpawnedPawns(map))
                {
                    string hostileReason;
                    if (attacker == null || !IsHostile(attacker, out hostileReason)
                        || !string.Equals(SafeJob(attacker), "AttackMelee", StringComparison.OrdinalIgnoreCase))
                        continue;
                    var target = SafeJobTargetPawn(attacker);
                    if (target != null && watch.MeleeThreatPawnIds.Contains(target.thingIDNumber)
                        && PawnDistance(attacker, target) <= watch.MeleeThreatWithin)
                        b.MeleeIntentKeys.Add(MeleeIntentKey(attacker, target));
                }
            }

            b.EntryProbe = Snapshot(b, watch, watch.Alerts, null);
            return b;
        }

        private static Probe Snapshot(Baseline baseline, Watch watch, bool checkAlerts, Timing timing)
        {
            var whole = Stopwatch.StartNew();
            var p = new Probe();

            string why;
            if (!Playable(out why))
            {
                p.Available = false;
                p.Unavailable = why;
                return p;
            }

            p.Session = Current.Game;
            var tm = Find.TickManager;
            p.Tick = tm != null ? tm.TicksGame : 0;
            p.CurSpeed = tm != null ? tm.CurTimeSpeed : TimeSpeed.Paused;
            p.ForcePaused = tm != null && tm.ForcePaused;

            if (watch.Letters)
            {
                foreach (var letter in Letters())
                {
                    var id = SafeLetterId(letter);
                    if (baseline.LetterIds.Contains(id))
                        continue;
                    p.NewLetters.Add(DescribeLetter(letter, id, p.Tick));
                }
            }

            // Messages are the third channel and the only one that expires: a
            // live message is gone 13 real seconds after it appears, so a run
            // that does not look here loses the event outright.
            if (watch.Messages)
            {
                foreach (var message in LiveMessages())
                {
                    var id = SafeMessageId(message);
                    if (baseline.MessageIds.Contains(id))
                        continue;
                    var type = SafeMessageType(message);
                    var stops = type == null || !watch.IgnoredMessageTypes.Contains(type);
                    var row = new Dictionary<string, object>
                    {
                        { "id", id },
                        { "text", SafeMessageText(message) },
                        { "messageType", type },
                        { "startingTick", SafeMessageTick(message) },
                        // The filter states itself per row, not just in a count.
                        { "stopped", stops }
                    };
                    p.NewMessages.Add(row);
                    if (stops)
                        p.StoppingMessages.Add(row);
                    else if (baseline.NoticedMessageIds.Add(id))
                        // A message can expire between polls; the run-long list
                        // is the only place an ignored one is still recoverable.
                        baseline.IgnoredMessages.Add(row);
                }
            }

            if (checkAlerts && watch.Alerts)
            {
                var alertClock = Stopwatch.StartNew();
                foreach (var entry in ActiveAlertKeys(watch.MinPriority, null))
                {
                    p.AlertsConsidered++;
                    var parts = entry.Key.Split('|');
                    var alertType = parts[0];
                    var alertPriority = parts.Length > 1 ? parts[1] : null;

                    if (baseline.AlertKeys.Contains(entry.Key))
                    {
                        // Standing since entry. Push the memo forward so a key
                        // that is CONTINUOUSLY up cannot fire on the next call
                        // just because that call's baseline caught it in one of
                        // RimWorld's half-second flap troughs.
                        if (watch.AlertDebounceMs > 0)
                            StampAlert(entry.Key);
                        continue;
                    }

                    if (watch.AlertDebounceMs > 0)
                    {
                        var ago = AlertLastCountedAgoMs(entry.Key);
                        if (ago.HasValue && ago.Value < watch.AlertDebounceMs)
                        {
                            // Suppressed, not dropped. The caller is told the
                            // key, the live label and how stale the memo is, so
                            // it can decide the debounce was set too wide.
                            Dictionary<string, object> row;
                            if (!baseline.DebouncedAlerts.TryGetValue(entry.Key, out row))
                            {
                                row = new Dictionary<string, object>
                                {
                                    { "alertKey", entry.Key },
                                    { "alertType", alertType },
                                    { "label", entry.Value },
                                    { "priority", alertPriority },
                                    { "msSinceLastCounted", ago.Value },
                                    { "pollsDebounced", 0 }
                                };
                                baseline.DebouncedAlerts[entry.Key] = row;
                            }
                            row["label"] = entry.Value;
                            row["msSinceLastCounted"] = ago.Value;
                            row["pollsDebounced"] = (int)row["pollsDebounced"] + 1;
                            // Still standing, so the window rolls forward.
                            StampAlert(entry.Key);
                            continue;
                        }
                        StampAlert(entry.Key);
                    }

                    p.NewAlerts.Add(new Dictionary<string, object>
                    {
                        { "alertKey", entry.Key },
                        { "alertType", alertType },
                        { "label", entry.Value },
                        { "priority", alertPriority }
                    });
                }
                alertClock.Stop();
                p.AlertsChecked = true;
                p.AlertMs = alertClock.Elapsed.TotalMilliseconds;
                if (timing != null)
                    timing.RecordAlert(p.AlertMs, p.AlertsConsidered);
            }

            // Zero ordinary-play cost: combat state is never touched unless at
            // least one combat watch was explicitly enabled for this call.
            var combatEnabled = (watch.MeleeThreats && watch.MeleeThreatPawnIds.Count > 0)
                                || (watch.PawnOrders && watch.WatchedPawnIds.Count > 0)
                                || (watch.Injuries && watch.InjuryPawnIds.Count > 0);
            var needPawns = watch.Hostiles || watch.HuntWithin > 0 || watch.Downed
                            || watch.HealthBelowPct > 0 || combatEnabled;
            if (needPawns)
            {
                var map = Find.CurrentMap;
                var all = SpawnedPawns(map);
                var colonists = new List<Pawn>();
                foreach (var pawn in all)
                {
                    if (pawn != null && SafeIsColonist(pawn) && !SafeDead(pawn))
                        colonists.Add(pawn);
                }

                foreach (var pawn in all)
                {
                    if (pawn == null)
                        continue;

                    if (combatEnabled
                        && ((watch.PawnOrders && watch.WatchedPawnIds.Contains(pawn.thingIDNumber))
                            || (watch.Injuries && watch.InjuryPawnIds.Contains(pawn.thingIDNumber))))
                    {
                        CombatPawnState before;
                        if (baseline.WatchedPawns.TryGetValue(pawn.thingIDNumber, out before))
                        {
                            var after = CaptureCombatPawn(pawn);
                            if (watch.PawnOrders && watch.WatchedPawnIds.Contains(pawn.thingIDNumber)
                                && !SameOrder(before, after))
                                p.PawnOrderChanges.Add(DescribeOrderChange(before, after));
                            if (watch.Injuries && watch.InjuryPawnIds.Contains(pawn.thingIDNumber)
                                && MeaningfulInjuryChange(before, after, watch))
                                p.PawnInjuries.Add(DescribeInjuryChange(before, after));
                        }
                    }

                    if (SafeIsColonist(pawn))
                    {
                        if (watch.Downed && (SafeDowned(pawn) || SafeDead(pawn))
                            && !baseline.DownedColonists.Contains(pawn.thingIDNumber))
                        {
                            p.NewlyDowned.Add(new Dictionary<string, object>
                            {
                                { "name", SafeName(pawn) },
                                { "dead", SafeDead(pawn) },
                                { "downed", SafeDowned(pawn) },
                                { "position", Pos(pawn) }
                            });
                        }
                        if (watch.HealthBelowPct > 0)
                        {
                            var pct = SafeHealthPct(pawn);
                            if (pct != null && pct.Value * 100f < watch.HealthBelowPct)
                            {
                                p.Hurt.Add(new Dictionary<string, object>
                                {
                                    { "name", SafeName(pawn) },
                                    { "healthPct", Math.Round(pct.Value * 100f, 1) },
                                    { "downed", SafeDowned(pawn) },
                                    { "position", Pos(pawn) }
                                });
                            }
                        }
                        continue;
                    }

                    if (SafeDead(pawn))
                        continue;

                    string reason;
                    var hostile = IsHostile(pawn, out reason);
                    var nearest = NearestColonist(pawn, colonists);

                    if (combatEnabled && watch.MeleeThreats && hostile
                        && string.Equals(SafeJob(pawn), "AttackMelee", StringComparison.OrdinalIgnoreCase))
                    {
                        var target = SafeJobTargetPawn(pawn);
                        if (target != null && watch.MeleeThreatPawnIds.Contains(target.thingIDNumber)
                            && PawnDistance(pawn, target) <= watch.MeleeThreatWithin
                            && !baseline.MeleeIntentKeys.Contains(MeleeIntentKey(pawn, target)))
                            p.MeleeThreats.Add(DescribeMeleeThreat(pawn, target));
                    }

                    if (watch.Hostiles && hostile && !baseline.IgnoredHostiles.Contains(pawn.thingIDNumber))
                        p.Hostiles.Add(DescribePawn(pawn, reason, nearest));

                    if (!hostile && watch.HuntWithin > 0
                        && string.Equals(SafeJob(pawn), "PredatorHunt", StringComparison.OrdinalIgnoreCase)
                        && nearest != null && nearest.Value <= watch.HuntWithin)
                        ClassifyHunter(pawn, nearest, p);
                }
            }

            whole.Stop();
            p.ProbeMs = whole.Elapsed.TotalMilliseconds;
            if (timing != null)
                timing.RecordProbe(p.ProbeMs);
            return p;
        }

        /// <summary>
        /// The one place stop-condition precedence lives. Letters before
        /// everything because the game auto-pauses on them; hostiles before
        /// health because a raid is the more urgent fact.
        /// </summary>
        private static Hit FirstHit(Probe p, Watch watch)
        {
            if (p == null)
                return null;

            var hookedInjury = watch.InjuryHook ? CombatInjuryHook.Peek() : null;
            if (hookedInjury != null)
            {
                var row = hookedInjury.ToPayload();
                return new Hit("pawn_injury_hook",
                    hookedInjury.PawnName + " sustained an injury; the game was paused synchronously at tick " + hookedInjury.Tick,
                    row);
            }

            if (watch.Letters && p.NewLetters.Count > 0)
            {
                var first = p.NewLetters[0];
                return new Hit("letter",
                    "New letter: " + Str(first, "label") + " (" + Str(first, "letterDef") + ")"
                    + (p.NewLetters.Count > 1 ? " and " + (p.NewLetters.Count - 1) + " more" : ""),
                    new Dictionary<string, object> { { "kind", "letter" }, { "letters", p.NewLetters } });
            }

            if (watch.Messages && p.StoppingMessages.Count > 0)
            {
                var first = p.StoppingMessages[0];
                return new Hit("message",
                    "Message: " + Str(first, "text") + " [" + Str(first, "messageType") + "]"
                    + (p.StoppingMessages.Count > 1 ? " and " + (p.StoppingMessages.Count - 1) + " more" : ""),
                    new Dictionary<string, object> { { "kind", "message" }, { "messages", p.StoppingMessages } });
            }

            if (watch.Alerts && p.NewAlerts.Count > 0)
            {
                var first = p.NewAlerts[0];
                return new Hit("alert",
                    "Alert became active: " + Str(first, "label") + " [" + Str(first, "priority") + "]"
                    + (p.NewAlerts.Count > 1 ? " and " + (p.NewAlerts.Count - 1) + " more" : ""),
                    new Dictionary<string, object> { { "kind", "alert" }, { "alerts", p.NewAlerts } });
            }

            if (watch.Hostiles && p.Hostiles.Count > 0)
            {
                var first = p.Hostiles[0];
                return new Hit("hostile",
                    p.Hostiles.Count + " hostile pawn(s) on the map, nearest " + Str(first, "defName")
                    + " at " + Str(first, "nearestColonistDistance") + " cells (" + Str(first, "hostileReason") + ")",
                    new Dictionary<string, object> { { "kind", "hostile" }, { "pawns", p.Hostiles } });
            }

            if (watch.HuntWithin > 0 && p.Hunters.Count > 0)
            {
                var first = p.Hunters[0];
                return new Hit("predator_hunt",
                    Str(first, "defName") + " is hunting " + Str(first, "nearestColonistDistance")
                    + " cells from " + Str(first, "nearestColonist"),
                    new Dictionary<string, object> { { "kind", "predator_hunt" }, { "pawns", p.Hunters } });
            }

            if (watch.MeleeThreats && p.MeleeThreats.Count > 0)
            {
                var first = p.MeleeThreats[0];
                return new Hit("melee_threat",
                    Str(first, "attackerName") + " started a melee attack on " + Str(first, "targetPawnName")
                    + " at distance " + Str(first, "distance"),
                    new Dictionary<string, object> { { "kind", "melee_threat" }, { "meleeThreats", p.MeleeThreats } });
            }

            if (watch.PawnOrders && p.PawnOrderChanges.Count > 0)
            {
                var first = p.PawnOrderChanges[0];
                return new Hit("pawn_order_changed",
                    Str(first, "pawnName") + " had its watched order replaced or completed",
                    new Dictionary<string, object> { { "kind", "pawn_order_changed" }, { "pawnOrderChanges", p.PawnOrderChanges } });
            }

            if (watch.Injuries && p.PawnInjuries.Count > 0)
            {
                var first = p.PawnInjuries[0];
                return new Hit("pawn_injury",
                    Str(first, "pawnName") + " sustained a meaningful injury change",
                    new Dictionary<string, object> { { "kind", "pawn_injury" }, { "pawnInjuries", p.PawnInjuries } });
            }

            if (watch.Downed && p.NewlyDowned.Count > 0)
            {
                var first = p.NewlyDowned[0];
                return new Hit("colonist_downed",
                    Str(first, "name") + " is " + (Str(first, "dead") == "True" ? "DEAD" : "down"),
                    new Dictionary<string, object> { { "kind", "colonist_downed" }, { "colonists", p.NewlyDowned } });
            }

            if (watch.HealthBelowPct > 0 && p.Hurt.Count > 0)
            {
                var first = p.Hurt[0];
                return new Hit("colonist_health",
                    Str(first, "name") + " is at " + Str(first, "healthPct") + "% health, under the "
                    + watch.HealthBelowPct + "% threshold",
                    new Dictionary<string, object> { { "kind", "colonist_health" }, { "colonists", p.Hurt } });
            }

            return null;
        }

        // ------------------------------------------------------------------
        // Game reads
        // ------------------------------------------------------------------

        // The shared map gate plus the two extra conditions this tool needs: it
        // is about to RUN the clock, not just read it.
        private static bool Playable(out string why)
        {
            Map map;
            if (!BridgeCommon.TryGetMap(ToolName, out map, out why))
                return false;

            why = null;
            if (Find.TickManager == null)
            {
                why = "No TickManager; the game is not in a playable state.";
                return false;
            }
            if (LongEventHandler.AnyEventNowOrWaiting)
            {
                why = "RimWorld is busy with a long event.";
                return false;
            }
            return true;
        }

        internal static List<Letter> Letters()
        {
            try
            {
                var stack = Find.LetterStack;
                if (stack == null)
                    return new List<Letter>();
                var list = stack.LettersListForReading;
                if (list == null)
                    return new List<Letter>();
                return list.Where(l => l != null).ToList();
            }
            catch
            {
                return new List<Letter>();
            }
        }

        internal static string SafeLetterId(Letter letter)
        {
            try { return letter.GetUniqueLoadID(); }
            catch { return "letter-" + letter.ID; }
        }

        internal static List<Message> LiveMessages()
        {
            if (LiveMessagesField == null)
                return new List<Message>();
            try
            {
                var live = LiveMessagesField.GetValue(null) as List<Message>;
                if (live == null)
                    return new List<Message>();
                var copy = new List<Message>(live.Count);
                for (var i = 0; i < live.Count; i++)
                    if (live[i] != null)
                        copy.Add(live[i]);
                return copy;
            }
            catch
            {
                return new List<Message>();
            }
        }

        internal static string SafeMessageId(Message message)
        {
            try { return message.GetUniqueLoadID(); }
            catch { return "message-" + message.startingTick + "-" + (message.text ?? string.Empty).GetHashCode(); }
        }

        internal static string SafeMessageText(Message message) { try { return message.text; } catch { return null; } }
        internal static string SafeMessageType(Message message) { try { return message.def != null ? message.def.defName : null; } catch { return null; } }
        internal static int SafeMessageTick(Message message) { try { return message.startingTick; } catch { return 0; } }

        private static HashSet<string> ParseCsv(string csv)
        {
            var set = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            if (string.IsNullOrEmpty(csv))
                return set;
            foreach (var part in csv.Split(','))
            {
                var t = part.Trim();
                if (t.Length > 0)
                    set.Add(t);
            }
            return set;
        }

        private static Dictionary<string, object> DescribeLetter(Letter letter, string id, int tick)
        {
            string label = null, def = null, pauseMode = null, type = null;
            int arrival = 0;
            bool auto = false;
            try { label = letter.Label.ToString(); } catch { }
            try { def = letter.def != null ? letter.def.defName : null; } catch { }
            try { pauseMode = letter.def != null ? letter.def.pauseMode.ToString() : null; } catch { }
            try { type = letter.GetType().Name; } catch { }
            try { arrival = letter.arrivalTick; } catch { }
            try { auto = letter.ShouldAutomaticallyOpenLetter; } catch { }

            return new Dictionary<string, object>
            {
                // Same id as rimworld/list_letters, so this hands straight to
                // rimworld/open_letter or letters.py.
                { "id", id },
                { "letterDef", def },
                { "label", label },
                { "type", type },
                { "arrivalTick", arrival },
                { "ageTicks", Math.Max(0, tick - arrival) },
                { "pauseMode", pauseMode },
                { "shouldAutomaticallyOpenLetter", auto }
            };
        }

        /// <summary>
        /// Active alerts at or above <paramref name="min"/>, as key/label pairs.
        /// Reads AlertsReadout.activeAlerts, which RimWorld's own readout keeps
        /// current. Nothing here recalculates an alert.
        /// </summary>
        internal static IEnumerable<KeyValuePair<string, string>> ActiveAlertKeys(
            AlertPriority min, List<KeyValuePair<string, string>> sink)
        {
            var result = sink ?? new List<KeyValuePair<string, string>>();
            if (ActiveAlertsField == null)
                return result;

            AlertsReadout readout;
            try { readout = Find.Alerts; }
            catch { return result; }
            if (readout == null)
                return result;

            List<Alert> active;
            try { active = ActiveAlertsField.GetValue(readout) as List<Alert>; }
            catch { return result; }
            if (active == null)
                return result;

            for (var i = 0; i < active.Count; i++)
            {
                var alert = active[i];
                if (alert == null)
                    continue;
                AlertPriority priority;
                string label;
                string type;
                try
                {
                    priority = alert.Priority;
                    if (priority < min)
                        continue;
                    label = alert.Label ?? string.Empty;
                    type = alert.GetType().FullName ?? alert.GetType().Name;
                }
                catch
                {
                    continue;
                }
                // Type alone is not unique: precept and quest alerts share a
                // type across instances. Label separates them; priority rides
                // along so the payload does not need a second lookup. The label
                // in the KEY is normalized (see NormalizeAlertLabel); the label
                // in the VALUE is what the game actually says right now, so the
                // payload never reports a label nobody could see on screen.
                result.Add(new KeyValuePair<string, string>(
                    type + "|" + priority + "|" + NormalizeAlertLabel(label), label));
            }
            return result;
        }

        /// <summary>
        /// Strip the volatile count suffix out of an alert label so the key is
        /// stable frame to frame.
        ///
        /// RimWorld's own idiom is `text + " x" + num.ToStringCached()` -- see
        /// BreakRiskAlertUtility.AlertLabel, which appends it whenever more
        /// than one colonist is at risk. The count changes every time any pawn
        /// crosses the mood threshold, which made "Mood break risk: minor" and
        /// "Mood break risk: minor x3" two different keys for one alert: the
        /// entry baseline held one, the next poll saw the other, and the run
        /// stopped for something that had been on screen for minutes.
        ///
        /// Only a trailing " x&lt;digits&gt;" is removed, and only when something
        /// is left in front of it, so a label that IS a number survives.
        /// </summary>
        private static string NormalizeAlertLabel(string label)
        {
            if (string.IsNullOrEmpty(label))
                return string.Empty;

            var s = label.TrimEnd();
            var end = s.Length;
            var i = end - 1;
            while (i >= 0 && s[i] >= '0' && s[i] <= '9')
                i--;
            // Needs at least one digit, an 'x' before it, and a space before that.
            if (i == end - 1 || i < 2 || (s[i] != 'x' && s[i] != 'X') || s[i - 1] != ' ')
                return s;
            return s.Substring(0, i - 1).TrimEnd();
        }

        /// <summary>
        /// Drop the whole debounce memo when the loaded game changes. Cheap and
        /// honest: a new Current.Game object is a different colony, and a tick
        /// that went backwards is a save reloaded under us. Either way every
        /// remembered timestamp is about a world that is no longer on screen.
        /// Call inside the main-thread hop; the caller holds no lock.
        /// </summary>
        private static void SyncAlertMemoToGame()
        {
            var game = BridgeCommon.Try(() => (object)Current.Game, null);
            var tick = BridgeCommon.Try(() => Find.TickManager != null ? Find.TickManager.TicksGame : -1, -1);

            lock (AlertMemoLock)
            {
                var changed = !ReferenceEquals(game, _alertMemoGame)
                              || (tick >= 0 && _alertMemoTick >= 0 && tick < _alertMemoTick);
                if (changed)
                    AlertLastCountedMs.Clear();
                _alertMemoGame = game;
                _alertMemoTick = tick;
            }
        }

        /// <summary>Stamp an alert key as having counted right now.</summary>
        private static void StampAlert(string key)
        {
            if (string.IsNullOrEmpty(key))
                return;
            lock (AlertMemoLock)
                AlertLastCountedMs[key] = AlertMemoClock.ElapsedMilliseconds;
        }

        /// <summary>
        /// How long ago this key last counted, in real milliseconds, or null if
        /// the memo has never seen it. Null is "not known", never "just now".
        /// </summary>
        private static long? AlertLastCountedAgoMs(string key)
        {
            if (string.IsNullOrEmpty(key))
                return null;
            lock (AlertMemoLock)
            {
                long stamp;
                if (!AlertLastCountedMs.TryGetValue(key, out stamp))
                    return null;
                var ago = AlertMemoClock.ElapsedMilliseconds - stamp;
                return ago < 0 ? 0 : ago;
            }
        }

        internal static List<Pawn> SpawnedPawns(Map map)
        {
            try
            {
                if (map == null || map.mapPawns == null)
                    return new List<Pawn>();
                var all = map.mapPawns.AllPawnsSpawned;
                return all == null ? new List<Pawn>() : all.ToList();
            }
            catch
            {
                return new List<Pawn>();
            }
        }

        internal static bool IsHostile(Pawn pawn, out string reason)
        {
            reason = "none";
            try
            {
                var mental = SafeMentalState(pawn);
                if (!string.IsNullOrEmpty(mental)
                    && mental.IndexOf("Manhunter", StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    reason = "manhunter:" + mental;
                    return true;
                }
            }
            catch { }

            try
            {
                var faction = pawn.Faction;
                if (faction == null)
                    return false;
                var player = PlayerFaction();
                if (player == null || faction == player)
                    return false;
                if (faction.HostileTo(player))
                {
                    reason = "faction:" + faction.Name;
                    return true;
                }
            }
            catch
            {
                reason = "unknown";
            }
            return false;
        }

        /// <summary>
        /// Decide whether a pawn on a `PredatorHunt` job is a reason to STOP the
        /// run, or merely something that is happening.
        ///
        /// ## Why this exists (the live-stream freeze, 2026-09-01)
        ///
        /// This colony owns a tame warg, bought from a war merchant around day
        /// 30. A tame warg hunts: it runs the same `PredatorHunt` job a cougar
        /// runs. The old test here was the job name and nothing else, so every
        /// single `home/play_until_event` call returned
        /// `stopReason: "predator_hunt"` at 0 ticks — a frozen picture on a live
        /// stream, once per call, for the whole session. `run.py` grew a Python
        /// filter to paper over it; this is the same filter at the source, where
        /// the game is still paused by the stop, which the Python could not undo.
        ///
        /// Two independent tests, and BOTH are about ownership:
        ///
        ///   * **The predator is ours.** `Faction == Faction.OfPlayerSilentFail`
        ///     — a tame animal on the player faction. Our own dog hunting is
        ///     never an event.
        ///   * **The prey is not ours.** A wild lynx eating a wild hare is
        ///     wildlife. `job.targetA` is the prey (verified: the constant
        ///     `JobDriver_PredatorHunt.PreyInd` is `TargetIndex.A`); it holds the
        ///     prey pawn during the chase and the prey's `Corpse` once the kill
        ///     is made, so both are unwrapped. Prey counts as ours when it is on
        ///     the player faction (colonist, tame animal, colony pet) or is held
        ///     by the player faction (a prisoner).
        ///
        /// Nothing is dropped silently: an ignored hunt goes to
        /// `huntersIgnored[]` with `ignoredReason`, the same shape
        /// `messagesIgnored[]` already uses. When the prey cannot be read at all
        /// the hunt is reported as informational rather than as a stop, because
        /// an unreadable target is not evidence of a threat — and the loud copy
        /// of it is in the payload either way.
        /// </summary>
        private static void ClassifyHunter(Pawn pawn, int? nearest, Probe p)
        {
            Dictionary<string, object> row;
            try { row = DescribePawn(pawn, "predatorHunt", nearest); }
            catch { return; }

            Pawn prey;
            var preyIsOurs = PreyBelongsToPlayer(pawn, out prey);
            var predatorIsOurs = IsPlayerFactionPawn(pawn);

            // Always present, never omitted: an absent field and a false one
            // read identically to a caller.
            row["predatorIsOurs"] = predatorIsOurs;
            row["prey"] = prey != null ? SafeName(prey) : null;
            row["preyDefName"] = prey != null && prey.def != null ? prey.def.defName : null;
            row["preyFaction"] = prey != null ? SafeFactionName(prey) : null;
            row["preyIsOurs"] = preyIsOurs;

            if (predatorIsOurs)
            {
                row["ignoredReason"] = "tame: this predator is on the player faction, so its hunt is ours";
                p.HuntersIgnored.Add(row);
                return;
            }

            if (!preyIsOurs)
            {
                row["ignoredReason"] = prey == null
                    ? "prey could not be read off the job; a hunt with no readable target is not treated as a threat"
                    : "prey is not a colonist, a colony animal or a colony prisoner";
                p.HuntersIgnored.Add(row);
                return;
            }

            row["ignoredReason"] = null;
            p.Hunters.Add(row);
        }

        /// <summary>
        /// The player faction, or null. NOT `Faction.OfPlayer`: its whole body is
        /// `get_OfPlayerSilentFail` followed by `Verse.Log.Error`, and
        /// `Log.Error`'s call path contains `TickManager.Pause()` — so on a map
        /// with no player faction, asking who the player is would PAUSE the
        /// colony, and the harness would read that as a person pressing space.
        /// </summary>
        private static Faction PlayerFaction()
        {
            try { return Faction.OfPlayerSilentFail; }
            catch { return null; }
        }

        private static bool IsPlayerFactionPawn(Pawn pawn)
        {
            try
            {
                var player = PlayerFaction();
                return player != null && pawn != null && pawn.Faction == player;
            }
            catch { return false; }
        }

        /// <summary>
        /// The prey of a `PredatorHunt` job, and whether the colony owns it.
        /// </summary>
        private static bool PreyBelongsToPlayer(Pawn predator, out Pawn prey)
        {
            prey = null;
            try
            {
                if (predator == null)
                    return false;
                var job = predator.CurJob;
                if (job == null)
                    return false;

                var thing = job.targetA.Thing;
                prey = thing as Pawn;
                if (prey == null)
                {
                    var corpse = thing as Corpse;
                    if (corpse != null)
                        prey = corpse.InnerPawn;
                }
                if (prey == null)
                    return false;

                var player = PlayerFaction();
                if (player == null)
                    return false;
                if (prey.Faction == player)
                    return true;                    // colonist, tame animal, colony pet
                if (prey.HostFaction == player)
                    return true;                    // our prisoner
                return false;
            }
            catch
            {
                return false;
            }
        }

        private static int? NearestColonist(Pawn pawn, List<Pawn> colonists)
        {
            int? best = null;
            foreach (var c in colonists)
            {
                if (c == null || c == pawn)
                    continue;
                var d = Math.Max(Math.Abs(c.Position.x - pawn.Position.x),
                                 Math.Abs(c.Position.z - pawn.Position.z));
                if (best == null || d < best.Value)
                    best = d;
            }
            return best;
        }

        private static Dictionary<string, object> DescribePawn(Pawn pawn, string reason, int? nearest)
        {
            string nearestName = null;
            try
            {
                var map = pawn.Map;
                if (map != null && nearest != null)
                {
                    foreach (var c in SpawnedPawns(map))
                    {
                        if (c == null || !SafeIsColonist(c) || SafeDead(c))
                            continue;
                        var d = Math.Max(Math.Abs(c.Position.x - pawn.Position.x),
                                         Math.Abs(c.Position.z - pawn.Position.z));
                        if (d == nearest.Value)
                        {
                            nearestName = SafeName(c);
                            break;
                        }
                    }
                }
            }
            catch { }

            return new Dictionary<string, object>
            {
                { "pawnId", pawn.thingIDNumber },
                { "name", SafeName(pawn) },
                { "defName", pawn.def != null ? pawn.def.defName : null },
                { "kindDef", pawn.kindDef != null ? pawn.kindDef.defName : null },
                { "position", Pos(pawn) },
                { "faction", SafeFactionName(pawn) },
                { "hostileReason", reason },
                { "job", SafeJob(pawn) },
                { "downed", SafeDowned(pawn) },
                { "nearestColonist", nearestName },
                { "nearestColonistDistance", nearest }
            };
        }

        private static Dictionary<string, object> Pos(Pawn pawn)
        {
            try
            {
                return BridgeCommon.Pos(pawn.Position);
            }
            catch
            {
                return null;
            }
        }

        private static float? SafeHealthPct(Pawn pawn)
        {
            try
            {
                if (pawn.health == null || pawn.health.summaryHealth == null)
                    return null;
                return pawn.health.summaryHealth.SummaryHealthPercent;
            }
            catch { return null; }
        }

        private static HashSet<int> ParsePawnIds(string csv)
        {
            var ids = new HashSet<int>();
            if (string.IsNullOrWhiteSpace(csv))
                return ids;
            foreach (var raw in csv.Split(','))
            {
                var token = raw.Trim();
                int id;
                if (int.TryParse(token, out id))
                {
                    ids.Add(id);
                    continue;
                }
                var end = token.Length - 1;
                while (end >= 0 && char.IsDigit(token[end])) end--;
                if (end < token.Length - 1 && int.TryParse(token.Substring(end + 1), out id))
                    ids.Add(id);
            }
            return ids;
        }

        private static CombatPawnState CaptureCombatPawn(Pawn pawn)
        {
            var s = new CombatPawnState
            {
                PawnId = pawn.thingIDNumber,
                PawnName = SafeName(pawn),
                Position = Pos(pawn),
                Job = SafeJob(pawn)
            };
            try
            {
                var job = pawn.CurJob;
                if (job != null)
                {
                    s.TargetAId = SafeTargetId(job.targetA);
                    s.TargetBId = SafeTargetId(job.targetB);
                    s.TargetCId = SafeTargetId(job.targetC);
                    s.TargetA = SafeTargetKey(job.targetA);
                    s.TargetB = SafeTargetKey(job.targetB);
                    s.TargetC = SafeTargetKey(job.targetC);
                }
                var hediffs = pawn.health != null && pawn.health.hediffSet != null
                    ? pawn.health.hediffSet.hediffs : null;
                if (hediffs != null)
                {
                    foreach (var h in hediffs)
                    {
                        var injury = h as Hediff_Injury;
                        if (injury == null) continue;
                        s.InjuryCount++;
                        s.TotalInjurySeverity += Math.Max(0f, injury.Severity);
                        s.BleedRate += Math.Max(0f, injury.BleedRate);
                    }
                }
            }
            catch { }
            return s;
        }

        private static int? SafeTargetId(LocalTargetInfo target)
        {
            try { return target.HasThing && target.Thing != null ? (int?)target.Thing.thingIDNumber : null; }
            catch { return null; }
        }

        private static string SafeTargetKey(LocalTargetInfo target)
        {
            try
            {
                if (!target.IsValid) return null;
                if (target.HasThing && target.Thing != null) return "thing:" + target.Thing.thingIDNumber;
                var c = target.Cell;
                return "cell:" + c.x + "," + c.z;
            }
            catch { return null; }
        }

        private static Pawn SafeJobTargetPawn(Pawn attacker)
        {
            try
            {
                var j = attacker.CurJob;
                if (j == null) return null;
                return j.targetA.Thing as Pawn ?? j.targetB.Thing as Pawn ?? j.targetC.Thing as Pawn;
            }
            catch { return null; }
        }

        private static bool SameOrder(CombatPawnState a, CombatPawnState b)
        {
            return string.Equals(a.Job, b.Job, StringComparison.Ordinal)
                   && string.Equals(a.TargetA, b.TargetA, StringComparison.Ordinal)
                   && string.Equals(a.TargetB, b.TargetB, StringComparison.Ordinal)
                   && string.Equals(a.TargetC, b.TargetC, StringComparison.Ordinal);
        }

        private static bool MeaningfulInjuryChange(CombatPawnState before, CombatPawnState after, Watch watch)
        {
            return (watch.InjuryStopOnNew && after.InjuryCount > before.InjuryCount)
                   || (watch.InjuryMinSeverityDelta > 0f
                       && after.TotalInjurySeverity - before.TotalInjurySeverity >= watch.InjuryMinSeverityDelta)
                   || (watch.InjuryMinBleedRateDelta > 0f
                       && after.BleedRate - before.BleedRate >= watch.InjuryMinBleedRateDelta);
        }

        private static Dictionary<string, object> DescribeCombatBaseline(CombatPawnState s)
        {
            return new Dictionary<string, object>
            {
                { "pawnId", s.PawnId }, { "pawnName", s.PawnName }, { "position", s.Position },
                { "order", DescribeOrder(s) }, { "injuries", DescribeInjuries(s) }
            };
        }

        private static Dictionary<string, object> DescribeOrder(CombatPawnState s)
        {
            return new Dictionary<string, object>
            {
                { "job", s.Job }, { "targetAId", s.TargetAId },
                { "targetBId", s.TargetBId }, { "targetCId", s.TargetCId },
                { "targetA", s.TargetA }, { "targetB", s.TargetB }, { "targetC", s.TargetC }
            };
        }

        private static Dictionary<string, object> DescribeInjuries(CombatPawnState s)
        {
            return new Dictionary<string, object>
            {
                { "injuryCount", s.InjuryCount },
                { "totalSeverity", Math.Round(s.TotalInjurySeverity, 4) },
                { "bleedRate", Math.Round(s.BleedRate, 4) }
            };
        }

        private static Dictionary<string, object> DescribeOrderChange(CombatPawnState before, CombatPawnState after)
        {
            return new Dictionary<string, object>
            {
                { "pawnId", after.PawnId }, { "pawnName", after.PawnName },
                { "before", DescribeOrder(before) }, { "after", DescribeOrder(after) },
                { "completed", string.IsNullOrEmpty(after.Job) || after.Job.StartsWith("Wait", StringComparison.OrdinalIgnoreCase) }
            };
        }

        private static Dictionary<string, object> DescribeInjuryChange(CombatPawnState before, CombatPawnState after)
        {
            return new Dictionary<string, object>
            {
                { "pawnId", after.PawnId }, { "pawnName", after.PawnName }, { "position", after.Position },
                { "before", DescribeInjuries(before) }, { "after", DescribeInjuries(after) },
                { "newInjuryCount", Math.Max(0, after.InjuryCount - before.InjuryCount) },
                { "severityDelta", Math.Round(after.TotalInjurySeverity - before.TotalInjurySeverity, 4) },
                { "bleedRateDelta", Math.Round(after.BleedRate - before.BleedRate, 4) }
            };
        }

        private static Dictionary<string, object> DescribeMeleeThreat(Pawn attacker, Pawn target)
        {
            var distance = PawnDistance(attacker, target);
            return new Dictionary<string, object>
            {
                { "attackerId", attacker.thingIDNumber }, { "attackerName", SafeName(attacker) },
                { "attackerDefName", attacker.def != null ? attacker.def.defName : null },
                { "attackerPosition", Pos(attacker) }, { "job", SafeJob(attacker) },
                { "jobTargetId", target.thingIDNumber }, { "jobTargetName", SafeName(target) },
                { "targetPawnId", target.thingIDNumber }, { "targetPawnName", SafeName(target) },
                { "targetPosition", Pos(target) }, { "distance", distance }
            };
        }

        private static int PawnDistance(Pawn a, Pawn b)
        {
            try
            {
                return Math.Max(Math.Abs(a.Position.x - b.Position.x),
                                Math.Abs(a.Position.z - b.Position.z));
            }
            catch { return int.MaxValue; }
        }

        private static string MeleeIntentKey(Pawn attacker, Pawn target)
        {
            return attacker.thingIDNumber + ">" + target.thingIDNumber;
        }

        internal static string SafeName(Pawn pawn)
        {
            try { return pawn.LabelShortCap.ToString(); }
            catch
            {
                try { return pawn.LabelCap.ToString(); }
                catch { return null; }
            }
        }

        private static string SafeFactionName(Pawn pawn) { try { return pawn.Faction != null ? pawn.Faction.Name : null; } catch { return null; } }
        internal static bool SafeIsColonist(Pawn pawn) { try { return pawn.IsColonist; } catch { return false; } }
        internal static bool SafeDowned(Pawn pawn) { try { return pawn.Downed; } catch { return false; } }
        internal static bool SafeDead(Pawn pawn) { try { return pawn.Dead; } catch { return false; } }

        internal static bool CoreWatchersAvailable
        {
            get { return ActiveAlertsField != null && LiveMessagesField != null; }
        }
        private static string SafeJob(Pawn pawn) { try { return pawn.CurJobDef != null ? pawn.CurJobDef.defName : null; } catch { return null; } }
        private static string SafeMentalState(Pawn pawn) { try { return pawn.MentalStateDef != null ? pawn.MentalStateDef.defName : null; } catch { return null; } }

        // ------------------------------------------------------------------
        // Plumbing
        // ------------------------------------------------------------------

        private static async Task<Probe> SafeProbe(IRimBridgeContext ctx, Baseline baseline, Watch watch,
            bool checkAlerts, Timing timing, CancellationToken token)
        {
            try
            {
                return await ctx.MainThread
                    .InvokeAsync(() => Snapshot(baseline, watch, checkAlerts, timing), token)
                    .ConfigureAwait(false);
            }
            catch
            {
                return null;
            }
        }

        /// <summary>Keep the hit's own findings visible even though the final
        /// probe was taken after the pause, when they may already be gone.</summary>
        private static Probe MergeHitInto(Probe final, Probe atHit, Hit hit)
        {
            if (final == null)
                return atHit;
            if (hit == null || atHit == null)
                return final;
            final.NewLetters = atHit.NewLetters;
            final.NewMessages = atHit.NewMessages;
            final.StoppingMessages = atHit.StoppingMessages;
            final.NewAlerts = atHit.NewAlerts;
            final.Hostiles = atHit.Hostiles;
            final.Hunters = atHit.Hunters;
            final.HuntersIgnored = atHit.HuntersIgnored;
            final.NewlyDowned = atHit.NewlyDowned;
            final.Hurt = atHit.Hurt;
            final.MeleeThreats = atHit.MeleeThreats;
            final.PawnOrderChanges = atHit.PawnOrderChanges;
            final.PawnInjuries = atHit.PawnInjuries;
            return final;
        }

        private static int Clamp(int v, int lo, int hi) { return v < lo ? lo : (v > hi ? hi : v); }
        private static float ClampFloat(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }

        private static string Str(Dictionary<string, object> d, string key)
        {
            object v;
            if (d != null && d.TryGetValue(key, out v) && v != null)
                return v.ToString();
            return "?";
        }

        private static object Failure(string error, string reason)
        {
            return new Dictionary<string, object>
            {
                { "success", false },
                { "tool", ToolName },
                { "stopReason", reason },
                { "stopDetail", error },
                { "error", error }
            };
        }

        private static object Respond(
            bool success, string reason, string detail, Dictionary<string, object> hitPayload,
            Baseline baseline, Probe final, TimeSpeed requested, Stopwatch clock, Timing timing,
            Watch watch, int budgetMs, int pollMs, int alertMs, bool pausedByTool, bool? pauseVerified,
            bool hitAtEntry, List<string> notes, string error)
        {
            var endTick = final != null ? final.Tick : baseline.StartTick;
            var payload = new Dictionary<string, object>
            {
                { "success", success },
                { "tool", ToolName },
                { "stopReason", reason },
                { "stopDetail", detail },
                { "event", hitPayload },
                { "hitAtEntry", hitAtEntry },

                { "startTick", baseline.StartTick },
                { "endTick", endTick },
                { "ticksElapsed", Math.Max(0, endTick - baseline.StartTick) },
                { "elapsedMs", (long)clock.ElapsedMilliseconds },
                { "budgetMs", budgetMs },

                { "requestedSpeed", requested.ToString() },
                { "speedAtEntry", baseline.StartSpeed.ToString() },
                { "speedAtExit", final != null ? final.CurSpeed.ToString() : null },
                { "pausedAtEntry", baseline.StartPaused },
                { "pausedAtExit", final != null && final.CurSpeed == TimeSpeed.Paused },
                { "forcePausedAtExit", final != null && final.ForcePaused },
                { "pausedByThisTool", pausedByTool },
                { "pauseVerified", pauseVerified },

                { "watching", new Dictionary<string, object>
                    {
                        { "letters", watch.Letters },
                        { "alerts", watch.Alerts },
                        { "stopOnCurrentAlerts", watch.StopOnCurrentAlerts },
                        { "ignoredAlertLabels", watch.IgnoredAlertLabels.OrderBy(x => x).ToList() },
                        { "minAlertPriority", watch.MinPriority.ToString() },
                        { "alertDebounceMs", watch.AlertDebounceMs },
                        { "messages", watch.Messages },
                        { "messageSinceTick", watch.MessageSinceTick },
                        { "messageTypesIgnored", new List<string>(watch.IgnoredMessageTypes) },
                        { "hostiles", watch.Hostiles },
                        { "ignoredHostileIds", watch.IgnoredHostileIds.OrderBy(x => x).ToList() },
                        { "huntWithin", watch.HuntWithin },
                        { "downedColonists", watch.Downed },
                        { "stopOnCurrentDownedColonists", watch.StopOnCurrentDowned },
                        { "ignoredDownedColonistIds", watch.IgnoredDownedColonistIds.OrderBy(x => x).ToList() },
                        { "healthBelowPct", watch.HealthBelowPct },
                        { "watchedPawnIds", watch.WatchedPawnIds.OrderBy(x => x).ToList() },
                        { "meleeThreatPawnIds", watch.MeleeThreatPawnIds.OrderBy(x => x).ToList() },
                        { "injuryPawnIds", watch.InjuryPawnIds.OrderBy(x => x).ToList() },
                        { "meleeThreats", watch.MeleeThreats },
                        { "meleeThreatWithin", watch.MeleeThreatWithin },
                        { "pawnOrders", watch.PawnOrders },
                        { "injuries", watch.Injuries },
                        { "injuryHook", watch.InjuryHook },
                        { "injuryHookAvailable", !watch.InjuryHook || CombatInjuryHook.IsInstalled() },
                        { "injuryStopOnNew", watch.InjuryStopOnNew },
                        { "injuryMinSeverityDelta", watch.InjuryMinSeverityDelta },
                        { "injuryMinBleedRateDelta", watch.InjuryMinBleedRateDelta },
                        { "combatCamera", watch.Camera != null && watch.Camera.Enabled },
                        { "pollIntervalMs", pollMs },
                        { "alertIntervalMs", alertMs }
                    } },

                // Every filter states itself: what was already true at entry
                // and therefore can never fire.
                { "baseline", new Dictionary<string, object>
                    {
                        { "letterCount", baseline.LetterIds.Count },
                        { "alertCount", baseline.AlertKeys.Count },
                        { "alerts", baseline.AlertLabels },
                        { "liveMessageCount", baseline.MessageIds.Count },
                        { "downedColonists", baseline.DownedColonistNames },
                        { "ignoredHostiles", baseline.IgnoredHostileNames },
                        { "watchedPawns", baseline.WatchedPawnRows }
                    } },

                { "timing", timing.ToPayload() },

                { "letters", final != null ? final.NewLetters : null },
                { "messages", final != null ? final.NewMessages : null },
                // Seen and deliberately not stopped on. Never a silent drop.
                { "messagesIgnored", baseline.IgnoredMessages },
                { "messagesIgnoredCount", baseline.IgnoredMessages.Count },
                { "alerts", final != null ? final.NewAlerts : null },
                // Active, not in the entry baseline, and deliberately NOT
                // stopped on because alertDebounceMs says they already counted
                // recently. Never a silent drop.
                { "alertsDebounced", baseline.DebouncedAlerts.Values.ToList() },
                { "alertsDebouncedCount", baseline.DebouncedAlerts.Count },
                { "hostiles", final != null ? final.Hostiles : null },
                { "hunters", final != null ? final.Hunters : null },
                // Predator hunts seen and deliberately NOT stopped on: our own
                // tame animals, and wild predators eating wild prey. Never a
                // silent drop -- each row carries ignoredReason.
                { "huntersIgnored", final != null ? final.HuntersIgnored : null },
                { "huntersIgnoredCount", final != null && final.HuntersIgnored != null ? final.HuntersIgnored.Count : 0 },
                { "downed", final != null ? final.NewlyDowned : null },
                { "hurt", final != null ? final.Hurt : null },
                { "meleeThreats", final != null ? final.MeleeThreats : null },
                { "pawnOrderChanges", final != null ? final.PawnOrderChanges : null },
                { "pawnInjuries", final != null ? final.PawnInjuries : null },
                { "injuryHookEvent", watch.InjuryHook && CombatInjuryHook.Peek() != null ? CombatInjuryHook.Peek().ToPayload() : null },
                { "combatCamera", watch.Camera != null ? watch.Camera.ToPayload() : null },

                { "notes", notes }
            };

            if (error != null)
                payload["error"] = error;
            if (watch.InjuryHookDebug)
            {
                ((Dictionary<string, object>)payload["watching"])["injuryHookStatus"] = CombatInjuryHook.Status();
                ((Dictionary<string, object>)payload["baseline"])["injuryHookStatus"] = CombatInjuryHook.Status();
            }
            return payload;
        }

        // ------------------------------------------------------------------
        // Carriers
        // ------------------------------------------------------------------

        private static void InitializeCombatCamera(CombatCameraState camera)
        {
            if (camera == null || !camera.Enabled || Find.CameraDriver == null) return;
            camera.ExpectedPosition = Find.CameraDriver.MapPosition;
            camera.ExpectedRootSize = Find.CameraDriver.RootSize;
            camera.Initialized = true;
        }

        private static void UpdateCombatCamera(CombatCameraState camera, long elapsedMs,
            bool forceFrame = false)
        {
            // The camera is a courtesy for whoever is watching, never a
            // safety gate: a throw here is counted and swallowed so it can
            // not turn a guarded combat pulse into stopReason "error".
            try { UpdateCombatCameraCore(camera, elapsedMs, forceFrame); }
            catch (Exception) { if (camera != null) camera.Errors++; }
        }

        private static void UpdateCombatCameraCore(CombatCameraState camera, long elapsedMs,
            bool forceFrame)
        {
            if (camera == null || !camera.Enabled || !camera.Initialized
                || Find.CameraDriver == null || Find.CurrentMap == null) return;
            var driver = Find.CameraDriver;
            var now = UnixMillisecondsNow();
            // MapPosition is an IntVec3 that includes screen shake, and the
            // driver clamps rootPos at the map edge every frame, so a
            // one-cell wobble is not a person's hand. Two cells is.
            if (Math.Abs(driver.MapPosition.x - camera.ExpectedPosition.x) > 1
                || Math.Abs(driver.MapPosition.z - camera.ExpectedPosition.z) > 1
                || Math.Abs(driver.RootSize - camera.ExpectedRootSize) > 0.5f)
            {
                camera.ManualOverride = true;
                camera.ManualSuppressUntilUnixMs = now + camera.ManualSuppressMs;
                camera.ExpectedPosition = driver.MapPosition;
                camera.ExpectedRootSize = driver.RootSize;
                return;
            }
            camera.ManualOverride = now < camera.ManualSuppressUntilUnixMs;
            if (camera.ManualOverride) return;
            if (!forceFrame && elapsedMs - camera.LastFrameMs < camera.IntervalMs) return;

            var all = SpawnedPawns(Find.CurrentMap);
            var friendlies = all.Where(p => p != null && p.Spawned && p.Drafted
                && camera.PawnIds.Contains(p.thingIDNumber)).ToList();
            if (friendlies.Count == 0)
            {
                // Nobody to frame: still honour the interval, or this scan
                // repeats on every 250 ms poll for the rest of the pulse.
                camera.LastFrameMs = elapsedMs;
                return;
            }
            var points = new List<IntVec3>(friendlies.Select(p => p.Position));
            var radiusSquared = camera.HostileRadius * camera.HostileRadius;
            foreach (var pawn in all)
            {
                string ignored;
                if (pawn == null || !pawn.Spawned || SafeDead(pawn) || !IsHostile(pawn, out ignored)) continue;
                if (friendlies.Any(f => (pawn.Position - f.Position).LengthHorizontalSquared <= radiusSquared))
                    points.Add(pawn.Position);
            }
            var minX = points.Min(p => p.x); var maxX = points.Max(p => p.x);
            var minZ = points.Min(p => p.z); var maxZ = points.Max(p => p.z);
            var rawWidth = maxX - minX; var rawHeight = maxZ - minZ;
            var floor = Math.Max(rawWidth, rawHeight) < camera.CompactSpan
                ? camera.CompactRootSize : camera.WideRootSize;
            var aspect = Screen.height > 0 ? (float)Screen.width / Screen.height : 1.5f;
            var required = Math.Max((rawHeight + 2 * camera.Margin) / 2f,
                (rawWidth + 2 * camera.Margin) / (2f * Math.Max(1f, aspect)));
            var root = Math.Max(floor, required);
            if (driver.config != null)
                root = Mathf.Clamp(root, driver.config.sizeRange.min, driver.config.sizeRange.max);
            // Whole cells, so the read-back IntVec3 position equals what was
            // asked for and an unchanged fight is not reframed every interval.
            var center = new Vector3(Mathf.Round((minX + maxX) / 2f), 0f, Mathf.Round((minZ + maxZ) / 2f));
            if (Math.Abs(center.x - camera.ExpectedPosition.x) > 0.15f
                || Math.Abs(center.z - camera.ExpectedPosition.z) > 0.15f
                || Math.Abs(root - camera.ExpectedRootSize) > 0.15f)
            {
                if (camera.LastFrameUnixMs > 0
                    && now - camera.LastFrameUnixMs < camera.CooldownMs)
                {
                    camera.CooldownSkips++;
                    camera.LastFrameMs = elapsedMs;
                    return;
                }
                driver.SetRootPosAndSize(center, root);
                camera.FramesApplied++;
                camera.LastFrameUnixMs = now;
            }
            camera.ExpectedPosition = driver.MapPosition;
            camera.ExpectedRootSize = driver.RootSize;
            camera.LastFrameMs = elapsedMs;
        }

        private static long UnixMillisecondsNow()
        {
            return (DateTime.UtcNow.Ticks - new DateTime(1970, 1, 1, 0, 0, 0,
                DateTimeKind.Utc).Ticks) / TimeSpan.TicksPerMillisecond;
        }

        private sealed class CombatCameraState
        {
            public bool Enabled; public HashSet<int> PawnIds = new HashSet<int>();
            public int IntervalMs; public float HostileRadius; public float CompactRootSize;
            public float WideRootSize; public float CompactSpan; public int Margin;
            public bool Initialized; public bool ManualOverride; public int FramesApplied;
            public long LastFrameMs; public IntVec3 ExpectedPosition; public float ExpectedRootSize;
            public long LastFrameUnixMs; public int CooldownMs; public int CooldownSkips;
            public long ManualSuppressUntilUnixMs; public int ManualSuppressMs;
            public int Errors;
            public object ToPayload() { return new Dictionary<string, object> {
                { "enabled", Enabled }, { "manualOverride", ManualOverride },
                { "framesApplied", FramesApplied }, { "intervalMs", IntervalMs },
                { "errors", Errors },
                { "lastFrameUnixMs", LastFrameUnixMs }, { "cooldownMs", CooldownMs },
                { "cooldownSkips", CooldownSkips },
                { "manualSuppressUntilUnixMs", ManualSuppressUntilUnixMs },
                { "manualSuppressMs", ManualSuppressMs },
                { "pawnIds", PawnIds.OrderBy(x => x).ToList() } }; }
        }

        private sealed class Watch
        {
            public bool Letters;
            public bool Alerts;
            public bool StopOnCurrentAlerts;
            public HashSet<string> IgnoredAlertLabels = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            public AlertPriority MinPriority;
            public int AlertDebounceMs;
            public bool Messages;
            public int MessageSinceTick;
            public HashSet<string> IgnoredMessageTypes = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            public bool Hostiles;
            public HashSet<int> IgnoredHostileIds = new HashSet<int>();
            public int HuntWithin;
            public bool Downed;
            public bool StopOnCurrentDowned;
            public HashSet<int> IgnoredDownedColonistIds = new HashSet<int>();
            public int HealthBelowPct;
            public HashSet<int> WatchedPawnIds = new HashSet<int>();
            public HashSet<int> MeleeThreatPawnIds = new HashSet<int>();
            public HashSet<int> InjuryPawnIds = new HashSet<int>();
            public bool MeleeThreats;
            public int MeleeThreatWithin;
            public bool PawnOrders;
            public bool Injuries;
            public bool InjuryHook;
            public bool InjuryHookDebug;
            public bool InjuryStopOnNew;
            public float InjuryMinSeverityDelta;
            public float InjuryMinBleedRateDelta;
            public CombatCameraState Camera;
        }

        private sealed class Baseline
        {
            public string Unavailable;
            public object Session;
            public int StartTick;
            public TimeSpeed StartSpeed;
            public bool StartPaused;
            public bool StartForcePaused;
            public bool RefusedBecausePaused;
            public readonly HashSet<string> LetterIds = new HashSet<string>(StringComparer.Ordinal);
            public readonly HashSet<string> MessageIds = new HashSet<string>(StringComparer.Ordinal);
            public readonly HashSet<string> NoticedMessageIds = new HashSet<string>(StringComparer.Ordinal);
            public readonly List<Dictionary<string, object>> IgnoredMessages = new List<Dictionary<string, object>>();
            public readonly HashSet<string> AlertKeys = new HashSet<string>(StringComparer.Ordinal);
            public readonly List<string> AlertLabels = new List<string>();
            // Run-long and deduplicated by alert key: an alert suppressed on
            // forty polls is one row, not forty.
            public readonly Dictionary<string, Dictionary<string, object>> DebouncedAlerts =
                new Dictionary<string, Dictionary<string, object>>(StringComparer.Ordinal);
            public readonly HashSet<int> DownedColonists = new HashSet<int>();
            public readonly List<string> DownedColonistNames = new List<string>();
            public readonly HashSet<int> IgnoredHostiles = new HashSet<int>();
            public readonly List<string> IgnoredHostileNames = new List<string>();
            public readonly HashSet<string> MeleeIntentKeys = new HashSet<string>(StringComparer.Ordinal);
            public readonly Dictionary<int, CombatPawnState> WatchedPawns = new Dictionary<int, CombatPawnState>();
            public readonly List<Dictionary<string, object>> WatchedPawnRows = new List<Dictionary<string, object>>();
            public Probe EntryProbe;
        }

        private sealed class Probe
        {
            public bool Available = true;
            public string Unavailable;
            public object Session;
            public int Tick;
            public TimeSpeed CurSpeed;
            public bool ForcePaused;
            public bool AlertsChecked;
            public int AlertsConsidered;
            public double AlertMs;
            public double ProbeMs;
            public List<Dictionary<string, object>> NewLetters = new List<Dictionary<string, object>>();
            public List<Dictionary<string, object>> NewMessages = new List<Dictionary<string, object>>();
            public List<Dictionary<string, object>> StoppingMessages = new List<Dictionary<string, object>>();
            public List<Dictionary<string, object>> NewAlerts = new List<Dictionary<string, object>>();
            public List<Dictionary<string, object>> Hostiles = new List<Dictionary<string, object>>();
            public List<Dictionary<string, object>> Hunters = new List<Dictionary<string, object>>();
            public List<Dictionary<string, object>> HuntersIgnored = new List<Dictionary<string, object>>();
            public List<Dictionary<string, object>> NewlyDowned = new List<Dictionary<string, object>>();
            public List<Dictionary<string, object>> Hurt = new List<Dictionary<string, object>>();
            public List<Dictionary<string, object>> MeleeThreats = new List<Dictionary<string, object>>();
            public List<Dictionary<string, object>> PawnOrderChanges = new List<Dictionary<string, object>>();
            public List<Dictionary<string, object>> PawnInjuries = new List<Dictionary<string, object>>();
        }

        private sealed class CombatPawnState
        {
            public int PawnId;
            public string PawnName;
            public Dictionary<string, object> Position;
            public string Job;
            public int? TargetAId;
            public int? TargetBId;
            public int? TargetCId;
            public string TargetA;
            public string TargetB;
            public string TargetC;
            public int InjuryCount;
            public float TotalInjurySeverity;
            public float BleedRate;
        }

        private sealed class Hit
        {
            public readonly string Reason;
            public readonly string Detail;
            public readonly Dictionary<string, object> Payload;

            public Hit(string reason, string detail, Dictionary<string, object> payload)
            {
                Reason = reason;
                Detail = detail;
                Payload = payload;
            }
        }

        private sealed class Timing
        {
            private int _alertChecks;
            private double _alertTotal;
            private double _alertMax;
            private int _alertsSeen;
            private int _probes;
            private double _probeTotal;
            private double _probeMax;

            public void RecordAlert(double ms, int considered)
            {
                _alertChecks++;
                _alertTotal += ms;
                _alertsSeen += considered;
                if (ms > _alertMax)
                    _alertMax = ms;
            }

            public void RecordProbe(double ms)
            {
                _probes++;
                _probeTotal += ms;
                if (ms > _probeMax)
                    _probeMax = ms;
            }

            public object ToPayload()
            {
                return new Dictionary<string, object>
                {
                    { "probes", _probes },
                    { "probeMsMean", _probes == 0 ? 0 : Math.Round(_probeTotal / _probes, 4) },
                    { "probeMsMax", Math.Round(_probeMax, 4) },
                    { "alertChecks", _alertChecks },
                    { "alertMsMean", _alertChecks == 0 ? 0 : Math.Round(_alertTotal / _alertChecks, 4) },
                    { "alertMsMax", Math.Round(_alertMax, 4) },
                    { "alertMsTotal", Math.Round(_alertTotal, 4) },
                    { "activeAlertsSeenPerCheck", _alertChecks == 0 ? 0 : Math.Round((double)_alertsSeen / _alertChecks, 2) }
                };
            }
        }
    }
}
