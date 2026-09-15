using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using RimBridgeServer.Sdk;
using RimWorld;
using RimWorld.Planet;
using UnityEngine;
using Verse;
using Verse.AI;

namespace HomeBridge.BridgeTools
{
    /// <summary>
    /// home/status — the between-turns read, in one call.
    ///
    /// Replaces a round of six: rimworld/get_game_info, list_letters,
    /// list_messages, list_alerts, list_colonists and get_ui_state. Every block
    /// is present on every reply, empty rather than absent, so a caller never
    /// has to tell "nothing there" from "not asked". `colonists` and `threats`
    /// default true and can be switched off; `explanations` and `colonistDetail`
    /// are off and add bytes.
    ///
    /// Read-only. Nothing here writes, selects, opens a tab or moves the clock.
    ///
    /// ## Hazards this tool dodges
    ///
    /// * **`TickManager.TicksAbs` can PAUSE the game.** Its getter calls
    ///   `Log.ErrorOnce` when `gameStartAbsTick == 0`, and `Log.Error` calls
    ///   `TickManager.Pause()`. `gameStartAbsTick` (a public int FIELD) is
    ///   checked first; when it is 0 the tool reports `ticksAbs: null`,
    ///   `ticksAbsAvailable: false`, and the whole calendar goes null with it
    ///   because the calendar is a function of absolute ticks. Same guard as
    ///   home/get_time.
    /// * **`MapPawns.FreeColonistsSpawned` calls `Faction.OfPlayer`**, whose
    ///   body is `get_OfPlayerSilentFail` followed by `Verse.Log.Error` —
    ///   i.e. a pause. It is never used. Colonists come from
    ///   `MapPawns.AllPawnsSpawned` filtered on `Pawn.IsFreeColonist`, which
    ///   reads `Faction.IsPlayer` off the pawn and calls nothing that logs.
    /// * **`Faction.OfPlayer` is never called anywhere below.** Hostility uses
    ///   `Faction.OfPlayerSilentFail` and treats null as an answer.
    /// * **`Alert.GetReport()` is the recalculation path.** It is called here,
    ///   once per already-active alert, because it is the only source of the
    ///   culprits `targets[]` names — exactly what stock
    ///   `rimworld/list_alerts` does. `home/play_until_event` avoids it
    ///   because it polls in a loop with the clock running; a one-shot read is
    ///   a different cost. `Alert.Active` and `Alert.Label` are plain reads of
    ///   `cachedActive` / `cachedLabel` and are used for the cheap fields.
    /// * **`ChoiceLetter.Choices` constructs its DiaOptions on each get** and
    ///   some options call `CameraJumper.CanJump`. Allocation only; nothing is
    ///   invoked. Stock `rimworld/list_letters` does the same.
    /// * **`WindowStack.MouseObscuredNow` / `CurrentWindowGetsInput` are not
    ///   read**: both compare against `currentlyDrawnWindow`, which is only
    ///   meaningful inside OnGUI and is stale everywhere else.
    ///
    /// ## Verified against the installed Assembly-CSharp.dll (1.6.9676.17735)
    ///
    ///   Verse.TickManager .TicksGame, .TicksAbs, .CurTimeSpeed, .Paused,
    ///        .ForcePaused, .gameStartAbsTick (public int FIELD)
    ///   RimWorld.GenDate .HourInteger/.DayOfQuadrum/.DayOfYear/.Quadrum/.Year
    ///        (long, float longitude); .Season/.DateFullStringAt (long, Vector2)
    ///   RimWorld.Planet.WorldGrid.LongLatOf(PlanetTile) -> Vector2 (x = longitude)
    ///   Verse.Find.LetterStack.LettersListForReading -> List&lt;Letter&gt;
    ///   Verse.Letter .ID, .def, .Label (TaggedString), .lookTargets,
    ///        .arrivalTick, .CanDismissWithRightClick,
    ///        .ShouldAutomaticallyOpenLetter, .GetUniqueLoadID()
    ///   Verse.ChoiceLetter (namespace Verse, NOT RimWorld) .Choices
    ///        -> IEnumerable&lt;DiaOption&gt;
    ///   Verse.DiaOption .text (PROTECTED string field), .disabled,
    ///        .disabledReason, .resolveTree, .action, .link, .linkLateBind
    ///   Verse.Messages.liveMessages (private static List&lt;Message&gt;)
    ///   Verse.Message .text, .def, .startingTick, .startingFrame, .Expired,
    ///        .lookTargets, .startingTime (PRIVATE float), .GetUniqueLoadID()
    ///   Verse.RealTime.LastRealTime (float) — the clock startingTime is on
    ///   RimWorld.AlertsReadout.activeAlerts (private List&lt;Alert&gt;)
    ///   RimWorld.Alert .Priority, .Label, .Active, .GetExplanation()
    ///        (TaggedString), .GetReport() -> RimWorld.AlertReport
    ///   RimWorld.AlertReport .active, .AnyCulpritValid, .AllCulprits
    ///        (IEnumerable&lt;GlobalTargetInfo&gt;)
    ///   Verse.GlobalTargetInfo .Thing, .Cell, .Map, .WorldObject, .Label,
    ///        .IsValid
    ///   Verse.MapPawns.AllPawnsSpawned -> IReadOnlyList&lt;Pawn&gt;
    ///   Verse.Pawn .IsFreeColonist, .IsColonist, .Downed, .Dead, .CurJobDef,
    ///        .MentalStateDef, .drafter.Drafted, .needs.mood.CurLevelPercentage,
    ///        .mindState.mentalBreaker.BreakThreshold{Minor,Major,Extreme},
    ///        .health.summaryHealth.SummaryHealthPercent,
    ///        .health.hediffSet.BleedRateTotal,
    ///        .health.HasHediffsNeedingTend(bool)
    ///   RimWorld.RestUtility.InBed(Pawn) — extension, body is
    ///        `p.CurrentBed() != null`
    ///   Verse.Find.WindowStack .Windows (IList&lt;Window&gt;),
    ///        .WindowsForcePause, .AnyWindowAbsorbingAllInput,
    ///        .NonImmediateDialogWindowOpen
    ///   Verse.Window .layer (WindowLayer FIELD), .forcePause,
    ///        .absorbInputAroundWindow, .optionalTitle
    ///   RimWorld.MainTabsRoot.OpenTab -> MainButtonDef
    ///   RimWorld.Selector.SelectedObjectsListForReading -> List&lt;object&gt;
    /// </summary>
    public sealed class HomeStatusTools
    {
        private const string ToolName = "home/status";

        // Caps, not parameters. A colony that has piled up two hundred letters
        // is a colony whose status read must still fit in a turn; the *NotListed
        // counts say what was left out, so a cap can never read as an empty
        // stack.
        private const int MaxLetters = 40;
        private const int MaxMessages = 16;
        private const int MaxAlerts = 40;
        private const int MaxTargetsPerAlert = 8;
        private const int MaxChoicesPerLetter = 8;
        private const int MaxWindows = 20;
        private const int MaxHostiles = 40;

        private static readonly FieldInfo ActiveAlertsField =
            BridgeCommon.PrivateInstanceField(typeof(AlertsReadout), "activeAlerts");

        private static readonly FieldInfo LiveMessagesField =
            BridgeCommon.PrivateStaticField(typeof(Messages), "liveMessages");

        // Message.startingTime is the REAL-time stamp the 13-second lifespan is
        // measured from. Private, and the only path to a message's age in
        // seconds; ticks are useless here because the game is usually paused.
        private static readonly FieldInfo MessageStartingTimeField =
            BridgeCommon.PrivateInstanceField(typeof(Message), "startingTime");

        // DiaOption.text is protected, so it needs the same non-public lookup.
        private static readonly FieldInfo DiaOptionTextField =
            BridgeCommon.PrivateInstanceField(typeof(DiaOption), "text");

        // Window types that are never the dialog eating map input: the letter
        // stack and every transient message draw as Super-layer ImmediateWindows,
        // a main tab is a MainTabWindow, and a dev EditWindow absorbs nothing.
        private static readonly string[] NonBlockingWindowPrefixes =
        {
            "Verse.ImmediateWindow", "RimWorld.MainTabWindow",
            "LudeonTK.EditWindow", "Verse.EditWindow"
        };

        [Tool(
            ToolName,
            Title = "One batched read of clock, letters, messages, alerts, colonists, threats and UI",
            Description =
                "The between-turns status read in one call, replacing get_game_info + list_letters + list_messages + "
                + "list_alerts + list_colonists + get_ui_state. Always returns time{} (ticks, pause flags, speed, the "
                + "in-game date), letters[] (with their choices), messages[] (the fading transient text, which expires "
                + "after 13 real seconds and cannot be recovered later), alerts[] with the culprit targets[] named, "
                + "colonists[] one compact row each, threats{} (hostiles and hunting predators, tame predators "
                + "excluded) and ui{} (open main tab, selection, windows, and whether a modal is eating map input). "
                + "colonists:false and threats:false cut it to the three-line poll; explanations:true adds each "
                + "alert's prose; colonistDetail:true adds all needs and the hediff labels. Read-only.",
            ResultDescription =
                "success, tool, status ('game_loaded' / 'no_map' / 'no_game'), time{}, letters[], messages[], alerts[], "
                + "colonists[], threats{}, ui{}, counts{}, blocks{} saying what was asked for, skipped[] naming "
                + "anything that could not be read, and notes{}.")]
        [ToolResponse("status", "string", "'game_loaded', 'no_map' (a game with no current map: world view or mid-load) or 'no_game'. Never absent; the last two are answers, not failures, and every block is still present and empty.", Always = true)]
        [ToolResponse("time", "object", "ticksGame, ticksAbs (+ticksAbsAvailable), paused, pausedByPlayer (legacy speed-control flag, NOT evidence of human input), forcePaused, timeSpeed, hourInteger, dayOfQuadrum (0-based) and dayOfQuadrumDisplay (1-based), dayOfYear, quadrum, season, year, dateFull, mapName. Same names and same values as home/get_time. Never null: with no game every field inside is null/0/false.", Always = true)]
        [ToolResponse("letters", "array", "The letter stack, newest first, same ids as rimworld/list_letters and rimworld/open_letter: id, label, letterDef, type (full class name), arrivalTick, ageTicks, shouldAutomaticallyOpenLetter, canDismissWithRightClick, choiceCount, hasChoices, choices[] ({index,text,disabled,disabledReason,closesDialog}) and lookTarget. Empty array when the stack is empty; anything past the row cap is named in skipped[].", Always = true)]
        [ToolResponse("messages", "array", "The live transient messages, newest first: id, text, messageType, startingTick, ageTicks, startingFrame, ageSeconds, expired, lookTarget. This channel evaporates 13 REAL seconds after it appears and nothing can recover it afterwards. Empty array when none are live.", Always = true)]
        [ToolResponse("alerts", "array", "Active alerts, loudest first: label, priority, type, active, targetCount, targetsTruncated, targets[] ({name,thingId,defName,kind,position}) naming the culprits, and explanation (null unless explanations:true). Empty array when nothing is alerting.", Always = true)]
        [ToolResponse("colonists", "array", "One compact row per free colonist spawned on the current map: name, thingId, position, dead, downed, drafted, inBed, job, mood, breakRisk, healthPct, needsTend, bleeding, mentalState, plus needs{} and hediffs[] under colonistDetail:true. EMPTY ARRAY when colonists:false was passed - blocks.colonists says which case this is.", Always = true)]
        [ToolResponse("threats", "object", "THREE lists, none of them a subset of another. hostileCount + hostiles[] (name, kindDef, faction, position, hostileReason, job, downed, predator, manhunterOnDamageChance, distanceToNearestColonist); huntingPredators[] + huntersIgnored[] with the reason each hunt was not counted (a tame predator's hunt and a wild predator eating wildlife are both excluded); and downedNearCount + downedNear[], downed non-colonist non-player pawns in the same radius, hostileReason 'downed', because a manhunter that goes down loses its mental state and silently leaves hostiles[]. predatorRadius echoes the radius used. Never null; an empty object's counts are 0, and blocks.threats says whether it was read at all.", Always = true)]
        [ToolResponse("ui", "object", "mainTabOpen, mainTabDefName, mainTabLabel, selectedCount, selectedFirstLabel, modalOpen, modalWindow (the type name of the dialog absorbing map input, or null), windowsForcePause, anyWindowAbsorbingAllInput, nonImmediateDialogWindowOpen, windowCount and windows[] ({type,layer,title,forcePause,absorbInputAroundWindow}). modalOpen is the check move.blocking_window() does by hand. targeter{active,source,caster} is Find.Targeter: active true means a map click is being read as a target (a Deploy turret gizmo, any Command_VerbTarget or Command_Target), source is the gizmo/verb label, caster the pawn thingId or null. A targeter opens NO window, so a window diff cannot see it.", Always = true)]
        [ToolResponse("counts", "object", "letterCount, letterChoiceCount, messageCount, alertCount, loudAlertCount (High or Critical), colonistCount, downedCount, hostileCount, huntingPredatorCount and downedNearCount. The one-line summary, so a brief caller reads ten numbers instead of six arrays. The last one is NOT folded into hostileCount: nothing in them is fighting yet, and hostileCount 0 has to keep meaning what it means.", Always = true)]
        [ToolResponse("blocks", "object", "What was actually asked for: colonists, threats, explanations, colonistDetail and predatorRadius. An empty colonists[] means something different under each, and predatorRadius 0 means downedNear[] was switched off, not empty.", Always = true)]
        [ToolResponse("skipped", "array", "One entry per thing that could not be read, naming the field and the reason. Empty array = everything answered. A null anywhere above is accounted for here.", Always = true)]
        [ToolResponse("unknownArguments", "array", "Every argument key the caller sent that this tool does not declare, sorted, case-sensitively. Empty array = every key was recognised. The host's own _rimBridgeTimeoutMs is never listed.", Always = true)]
        [ToolResponse("unknownArgumentsWarning", "string", "Present only when unknownArguments is non-empty, or when the caller's raw keys could not be read at all - in which case the empty unknownArguments means 'not known', not 'nothing unknown'.", Nullable = true)]
        public async Task<object> Status(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            [ToolParameter(Description = "Include colonists[]. TRUE by default. Pass false for the three-line poll: time, letters, messages, alerts and ui only.", DefaultValue = true)] bool colonists = true,
            [ToolParameter(Description = "Include threats{}. TRUE by default. Pass false to skip the whole-map pawn walk when only the notification channels matter.", DefaultValue = true)] bool threats = true,
            [ToolParameter(Description = "Add each alert's explanation prose. Off by default: it is the largest single thing in the payload and the label plus targets[] usually says enough.", DefaultValue = false)] bool explanations = false,
            [ToolParameter(Description = "Add needs{} (every need on a 0..1 scale, with the mental-break thresholds) and hediffs[] (the label and severity word the game shows) to every colonist row. Off by default.", DefaultValue = false)] bool colonistDetail = false,
            [ToolParameter(Description = "Cell radius around any colonist for threats{} downedNear[], which catches a manhunter that went DOWN and therefore stopped being hostile. Chebyshev distance, the same measure as distanceToNearestColonist. 0 disables the list. Ignored when threats:false.", DefaultValue = 30)] int predatorRadius = 30)
        {
            return BridgeCommon.WithUnknownArguments(
                await StatusCore(ctx, cancellationToken, colonists, threats, explanations, colonistDetail, predatorRadius)
                    .ConfigureAwait(false),
                ctx, typeof(HomeStatusTools), ToolName);
        }

        private async Task<object> StatusCore(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            bool wantColonists,
            bool wantThreats,
            bool wantExplanations,
            bool wantColonistDetail,
            int predatorRadius)
        {
            if (ctx?.MainThread == null)
                return Failure("No RimBridge main-thread dispatcher is available for this invocation.");

            // No arguments need parsing before the hop; the four are bools.
            // Companion tools dispatch with MarshalToMainThread = false, so
            // every read below goes inside ONE hop. It matters twice here: the
            // whole point of this tool is that the six answers describe the SAME
            // instant, and a hop per block would smear them across ticks.
            return await ctx.MainThread
                .InvokeAsync(() => Run(wantColonists, wantThreats, wantExplanations, wantColonistDetail, predatorRadius),
                             cancellationToken)
                .ConfigureAwait(false);
        }

        // =================================================================== run

        private static object Run(bool wantColonists, bool wantThreats,
                                  bool wantExplanations, bool wantColonistDetail,
                                  int predatorRadius)
        {
            var skipped = new List<object>();

            var payload = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["success"] = true,
                ["tool"] = ToolName
            };

            var game = BridgeCommon.Try(() => Current.Game, (Game)null);
            var map = SafeMap();

            payload["status"] = game == null ? "no_game" : (map == null ? "no_map" : "game_loaded");

            payload["time"] = TimeBlock(map, skipped);

            var letters = LetterRows(skipped);
            payload["letters"] = letters;

            var messages = MessageRows(skipped);
            payload["messages"] = messages;

            var alerts = AlertRows(wantExplanations, skipped);
            payload["alerts"] = alerts;

            var colonistRows = new List<object>();
            var threatBlock = EmptyThreats();

            if (map != null && (wantColonists || wantThreats))
            {
                // One walk of the spawned pawns feeds both blocks. Walking it
                // twice would cost twice and could disagree with itself.
                var spawned = SpawnedPawns(map);
                var freeColonists = new List<Pawn>();
                foreach (var pawn in spawned)
                {
                    if (pawn != null && BridgeCommon.Try(() => pawn.IsFreeColonist, false))
                        freeColonists.Add(pawn);
                }

                if (wantColonists)
                {
                    foreach (var pawn in freeColonists)
                        colonistRows.Add(ColonistRow(pawn, wantColonistDetail));
                }

                if (wantThreats)
                    threatBlock = ThreatBlock(spawned, freeColonists, predatorRadius < 0 ? 0 : predatorRadius);
            }
            else if (map == null && (wantColonists || wantThreats))
            {
                skipped.Add(Skip("colonists/threats", "there is no current map to walk."));
            }

            payload["colonists"] = colonistRows;
            payload["threats"] = threatBlock;
            payload["ui"] = UiBlock(skipped);

            payload["counts"] = Counts(letters, messages, alerts, colonistRows, threatBlock);

            payload["blocks"] = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "colonists", wantColonists },
                { "threats", wantThreats },
                { "explanations", wantExplanations },
                { "colonistDetail", wantColonistDetail },
                { "predatorRadius", predatorRadius < 0 ? 0 : predatorRadius }
            };

            payload["skipped"] = skipped;
            payload["notes"] = Notes(wantExplanations);
            return payload;
        }

        // ================================================================= time

        /// <summary>
        /// The same snapshot home/get_time returns, under the same key names, so
        /// a caller can swap one for the other. Never null: with no game every
        /// field inside is null / 0 / false.
        /// </summary>
        private static Dictionary<string, object> TimeBlock(Map map, List<object> skipped)
        {
            var block = new Dictionary<string, object>(StringComparer.Ordinal);

            var tickManager = BridgeCommon.Try(() => Find.TickManager, (TickManager)null);
            if (tickManager == null)
            {
                block["ticksGame"] = 0;
                block["ticksAbs"] = null;
                block["ticksAbsAvailable"] = false;
                block["paused"] = false;
                block["pausedByPlayer"] = false;
                block["forcePaused"] = false;
                block["timeSpeed"] = null;
                WriteCalendar(block, null, map);
                block["mapName"] = map == null ? null : BridgeCommon.SafeString(() => map.Parent == null ? null : map.Parent.Label);
                skipped.Add(Skip("time", "Find.TickManager was not readable; the clock fields are 0/false and the calendar is null."));
                return block;
            }

            block["ticksGame"] = BridgeCommon.TryN(() => tickManager.TicksGame) ?? 0;

            // THE HAZARD. get_TicksAbs calls Log.ErrorOnce when gameStartAbsTick
            // is 0, Log.ErrorOnce calls Log.Error, and Log.Error calls
            // TickManager.Pause(). Reading the property to find out whether it is
            // readable would stop the colony. gameStartAbsTick is a public int
            // field; check it, and only then read the property.
            int? absTicks = null;
            var gameStartAbsTick = BridgeCommon.TryN(() => tickManager.gameStartAbsTick);
            if (gameStartAbsTick.HasValue && gameStartAbsTick.Value != 0)
                absTicks = BridgeCommon.TryN(() => tickManager.TicksAbs);

            block["ticksAbs"] = absTicks;
            block["ticksAbsAvailable"] = absTicks.HasValue;

            // Paused = (curTimeSpeed == Paused || ForcePaused), so a modal window
            // makes `paused` true with nobody having touched the speed control.
            // Legacy pausedByPlayer reports the speed control, not provenance.
            // Tools and game code also set it; it cannot identify human input.
            var speed = BridgeCommon.SafeString(() => tickManager.CurTimeSpeed.ToString());
            block["paused"] = BridgeCommon.TryN(() => tickManager.Paused) ?? false;
            block["pausedByPlayer"] = string.Equals(speed, "Paused", StringComparison.Ordinal);
            block["forcePaused"] = BridgeCommon.TryN(() => tickManager.ForcePaused) ?? false;
            block["timeSpeed"] = speed;

            WriteCalendar(block, absTicks, map);
            block["mapName"] = map == null ? null : BridgeCommon.SafeString(() => map.Parent == null ? null : map.Parent.Label);

            if (!absTicks.HasValue)
                skipped.Add(Skip("time.ticksAbs", "TickManager.gameStartAbsTick is 0, so TicksAbs was NOT read (its getter pauses the game); the calendar is null with it."));
            else if (map == null)
                skipped.Add(Skip("time.calendar", "there is no map, so no longitude; a date computed at longitude 0 would be a plausible date for nowhere."));

            return block;
        }

        /// <summary>
        /// The calendar is a function of (absolute ticks, longitude). Without
        /// both, every field is null rather than computed at longitude 0.
        /// </summary>
        private static void WriteCalendar(IDictionary<string, object> block, int? absTicks, Map map)
        {
            var longLat = default(Vector2);
            var haveLongLat = map != null && TryGetLongLat(map, out longLat);

            if (!absTicks.HasValue || !haveLongLat)
            {
                block["hourInteger"] = null;
                block["dayOfQuadrum"] = null;
                block["dayOfQuadrumDisplay"] = null;
                block["dayOfYear"] = null;
                block["quadrum"] = null;
                block["season"] = null;
                block["year"] = null;
                block["dateFull"] = null;
                return;
            }

            long abs = absTicks.Value;
            var lon = longLat.x;
            var capturedLongLat = longLat;

            block["hourInteger"] = BridgeCommon.TryN(() => GenDate.HourInteger(abs, lon));

            // 0-based straight from the API; the game prints that number + 1.
            var dayOfQuadrum = BridgeCommon.TryN(() => GenDate.DayOfQuadrum(abs, lon));
            block["dayOfQuadrum"] = dayOfQuadrum;
            block["dayOfQuadrumDisplay"] = dayOfQuadrum.HasValue ? (int?)(dayOfQuadrum.Value + 1) : null;

            block["dayOfYear"] = BridgeCommon.TryN(() => GenDate.DayOfYear(abs, lon));
            block["quadrum"] = BridgeCommon.SafeString(() => GenDate.Quadrum(abs, lon).ToString());
            block["year"] = BridgeCommon.TryN(() => GenDate.Year(abs, lon));
            // The Vector2 overload, not Season(long, float latitude, float
            // longitude): that one's argument order differs from every other
            // GenDate entry point here, which is exactly what gets guessed wrong.
            block["season"] = BridgeCommon.SafeString(() => GenDate.Season(abs, capturedLongLat).ToString());
            block["dateFull"] = BridgeCommon.SafeString(() => GenDate.DateFullStringAt(abs, capturedLongLat));
        }

        /// <summary>WorldGrid.LongLatOf(PlanetTile) -> Vector2, x = longitude.
        /// PlanetTile is a struct with a .Valid flag; a pocket map or a map
        /// mid-generation holds an invalid one, so it is checked rather than
        /// caught.</summary>
        private static bool TryGetLongLat(Map map, out Vector2 longLat)
        {
            longLat = default(Vector2);
            try
            {
                var grid = Find.WorldGrid;
                if (grid == null)
                    return false;
                var tile = map.Tile;
                if (!tile.Valid)
                    return false;
                longLat = grid.LongLatOf(tile);
                return true;
            }
            catch { return false; }
        }

        // ============================================================== letters

        /// <summary>
        /// The letter stack, newest first — the order the game draws it and the
        /// order stock rimworld/list_letters returns. Ids are
        /// Letter.GetUniqueLoadID(), so a row hands straight to
        /// rimworld/open_letter or letters.py.
        /// </summary>
        private static List<object> LetterRows(List<object> skipped)
        {
            var rows = new List<object>();

            List<Letter> letters;
            try
            {
                var stack = Find.LetterStack;
                var list = stack == null ? null : stack.LettersListForReading;
                letters = list == null ? new List<Letter>() : list.Where(l => l != null).ToList();
            }
            catch
            {
                skipped.Add(Skip("letters", "Find.LetterStack.LettersListForReading threw; an empty letters[] here means 'not read'."));
                return rows;
            }

            letters.Reverse();                                  // newest first

            var tick = CurrentTick();
            var notListed = 0;
            for (var i = 0; i < letters.Count; i++)
            {
                if (rows.Count >= MaxLetters) { notListed++; continue; }
                rows.Add(LetterRow(letters[i], tick));
            }

            if (notListed > 0)
                skipped.Add(Skip("letters", notListed + " letter(s) past the " + MaxLetters + "-row cap were not listed."));
            return rows;
        }

        private static Dictionary<string, object> LetterRow(Letter letter, int tick)
        {
            var arrival = BridgeCommon.TryN(() => letter.arrivalTick) ?? 0;

            var row = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "id", BridgeCommon.SafeString(() => letter.GetUniqueLoadID())
                        ?? ("letter-" + BridgeCommon.TryN(() => letter.ID)) },
                { "label", BridgeCommon.SafeString(() => letter.Label.ToString()) },
                { "letterDef", BridgeCommon.SafeString(() => letter.def != null ? letter.def.defName : null) },
                { "type", BridgeCommon.SafeString(() => letter.GetType().FullName) },
                { "arrivalTick", arrival },
                { "ageTicks", Math.Max(0, tick - arrival) },
                { "shouldAutomaticallyOpenLetter", BridgeCommon.Try(() => letter.ShouldAutomaticallyOpenLetter, false) },
                { "canDismissWithRightClick", BridgeCommon.Try(() => letter.CanDismissWithRightClick, false) },
                { "lookTarget", PrimaryLookTarget(BridgeCommon.Try(() => letter.lookTargets, (LookTargets)null)) }
            };

            // Verse.ChoiceLetter, not RimWorld.ChoiceLetter — and note that
            // Verse.StandardLetter derives from it, so "has choices" is not
            // "somebody has to decide": an announcement's buttons come back as
            // Close / Jump to location. The caller makes that judgement; this
            // reports what the dialog would offer.
            var choices = new List<object>();
            var choiceLetter = letter as ChoiceLetter;
            if (choiceLetter != null)
            {
                try
                {
                    var options = choiceLetter.Choices;
                    if (options != null)
                    {
                        var index = 0;
                        foreach (var option in options)
                        {
                            index++;
                            if (option == null)
                                continue;
                            if (choices.Count >= MaxChoicesPerLetter)
                                break;
                            choices.Add(ChoiceRow(option, index));
                        }
                    }
                }
                catch
                {
                    // A letter whose Choices getter throws still gets a row; an
                    // empty choices[] with the letter present beats a dropped
                    // letter.
                }
            }

            row["choiceCount"] = choices.Count;
            row["hasChoices"] = choices.Count > 0;
            row["choices"] = choices;
            return row;
        }

        private static Dictionary<string, object> ChoiceRow(DiaOption option, int index)
        {
            string text = null;
            if (DiaOptionTextField != null)
            {
                try { text = DiaOptionTextField.GetValue(option) as string; }
                catch { text = null; }
            }

            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "index", index },
                { "text", text == null ? null : text.Trim() },
                { "disabled", BridgeCommon.Try(() => option.disabled, false) },
                { "disabledReason", BridgeCommon.SafeString(() => option.disabledReason) },
                { "closesDialog", BridgeCommon.Try(() => option.resolveTree, false) }
            };
        }

        // ============================================================= messages

        /// <summary>
        /// The live transient messages, newest first. This is RimWorld's THIRD
        /// notification channel: it is neither a letter nor an alert, it carries
        /// events that appear nowhere else, and it evaporates 13 real seconds
        /// after it appears with no way to recover it afterwards.
        /// Messages.liveMessages is private static; it is reflected exactly as
        /// stock rimworld/list_messages reflects it.
        /// </summary>
        private static List<object> MessageRows(List<object> skipped)
        {
            var rows = new List<object>();

            if (LiveMessagesField == null)
            {
                skipped.Add(Skip("messages", "Verse.Messages.liveMessages was not found by reflection on this build; an empty messages[] means 'not read'."));
                return rows;
            }

            List<Message> live;
            try
            {
                var value = LiveMessagesField.GetValue(null) as List<Message>;
                live = value == null ? new List<Message>() : value.Where(m => m != null).ToList();
            }
            catch
            {
                skipped.Add(Skip("messages", "reading Verse.Messages.liveMessages threw; an empty messages[] means 'not read'."));
                return rows;
            }

            live.Reverse();                                     // newest first

            var tick = CurrentTick();
            var now = BridgeCommon.TryN(() => RealTime.LastRealTime);
            var notListed = 0;

            for (var i = 0; i < live.Count; i++)
            {
                if (rows.Count >= MaxMessages) { notListed++; continue; }
                rows.Add(MessageRow(live[i], tick, now));
            }

            if (notListed > 0)
                skipped.Add(Skip("messages", notListed + " message(s) past the " + MaxMessages + "-row cap were not listed."));
            return rows;
        }

        private static Dictionary<string, object> MessageRow(Message message, int tick, float? now)
        {
            var startingTick = BridgeCommon.TryN(() => message.startingTick) ?? 0;

            // Age in SECONDS, not ticks. The 13-second lifespan is real time and
            // the game is usually paused, so ageTicks can be 0 on a message that
            // has been on screen for ten seconds and is about to vanish.
            double? ageSeconds = null;
            if (MessageStartingTimeField != null && now.HasValue)
            {
                try
                {
                    var startingTime = MessageStartingTimeField.GetValue(message);
                    if (startingTime is float)
                        ageSeconds = Math.Round(now.Value - (float)startingTime, 2);
                }
                catch { ageSeconds = null; }
            }

            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "id", BridgeCommon.SafeString(() => message.GetUniqueLoadID()) },
                { "text", BridgeCommon.SafeString(() => message.text) },
                { "messageType", BridgeCommon.SafeString(() => message.def != null ? message.def.defName : null) },
                { "startingTick", startingTick },
                { "ageTicks", Math.Max(0, tick - startingTick) },
                { "startingFrame", BridgeCommon.TryN(() => message.startingFrame) ?? 0 },
                { "ageSeconds", ageSeconds },
                { "expired", BridgeCommon.Try(() => message.Expired, false) },
                { "lookTarget", PrimaryLookTarget(BridgeCommon.Try(() => message.lookTargets, (LookTargets)null)) }
            };
        }

        // =============================================================== alerts

        /// <summary>
        /// Active alerts, loudest first. AlertsReadout.activeAlerts is the list
        /// RimWorld's own readout keeps current; nothing here recalculates
        /// whether an alert is active. GetReport() IS called, once per already
        /// active alert, because it is the only source of the culprit names —
        /// which is the whole reason "Tattered apparel" is worth reading.
        /// </summary>
        private static List<object> AlertRows(bool wantExplanations, List<object> skipped)
        {
            var rows = new List<object>();

            if (ActiveAlertsField == null)
            {
                skipped.Add(Skip("alerts", "RimWorld.AlertsReadout.activeAlerts was not found by reflection on this build; an empty alerts[] means 'not read'."));
                return rows;
            }

            AlertsReadout readout;
            try { readout = Find.Alerts; }
            catch { readout = null; }
            if (readout == null)
            {
                skipped.Add(Skip("alerts", "Find.Alerts is null; an empty alerts[] means 'not read'."));
                return rows;
            }

            List<Alert> active;
            try { active = ActiveAlertsField.GetValue(readout) as List<Alert>; }
            catch { active = null; }
            if (active == null)
            {
                skipped.Add(Skip("alerts", "AlertsReadout.activeAlerts did not read as a List<Alert>; an empty alerts[] means 'not read'."));
                return rows;
            }

            var built = new List<Dictionary<string, object>>();
            for (var i = 0; i < active.Count; i++)
            {
                var alert = active[i];
                if (alert == null)
                    continue;
                built.Add(AlertRow(alert, i, wantExplanations));
            }

            // AlertPriority is an ORDERED enum (Low < Medium < High < Critical)
            // and is sorted as one. Descending priority, then the readout's own
            // order, which is what the player sees down the right-hand side.
            built.Sort((a, b) =>
            {
                var pa = a.ContainsKey("prioritySortValue") && a["prioritySortValue"] is int ? (int)a["prioritySortValue"] : -1;
                var pb = b.ContainsKey("prioritySortValue") && b["prioritySortValue"] is int ? (int)b["prioritySortValue"] : -1;
                if (pa != pb)
                    return pb.CompareTo(pa);
                var ia = a.ContainsKey("ordinal") && a["ordinal"] is int ? (int)a["ordinal"] : 0;
                var ib = b.ContainsKey("ordinal") && b["ordinal"] is int ? (int)b["ordinal"] : 0;
                return ia.CompareTo(ib);
            });

            var notListed = 0;
            for (var i = 0; i < built.Count; i++)
            {
                if (rows.Count >= MaxAlerts) { notListed++; continue; }
                rows.Add(built[i]);
            }

            if (notListed > 0)
                skipped.Add(Skip("alerts", notListed + " alert(s) past the " + MaxAlerts + "-row cap were not listed."));
            return rows;
        }

        private static Dictionary<string, object> AlertRow(Alert alert, int ordinal, bool wantExplanations)
        {
            var row = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "ordinal", ordinal },
                { "type", BridgeCommon.SafeString(() => alert.GetType().FullName ?? alert.GetType().Name) },
                { "label", BridgeCommon.SafeString(() => alert.Label) },
                { "priority", BridgeCommon.SafeString(() => alert.Priority.ToString()) },
                { "prioritySortValue", BridgeCommon.TryN(() => (int)alert.Priority) ?? -1 },
                { "active", BridgeCommon.Try(() => alert.Active, false) }
            };

            // Prose is the biggest single thing in this payload and the label
            // plus the culprits usually says enough, so it is opt-in. Null when
            // not asked for; explanations:true is the difference.
            row["explanation"] = wantExplanations
                ? BridgeCommon.SafeString(() => { var e = alert.GetExplanation(); return e.ToString(); })
                : null;

            var targets = new List<object>();
            var targetCount = 0;
            var reportRead = false;
            try
            {
                var report = alert.GetReport();
                reportRead = true;
                var culprits = report.AllCulprits;
                if (culprits != null)
                {
                    foreach (var culprit in culprits)
                    {
                        targetCount++;
                        if (targets.Count >= MaxTargetsPerAlert)
                            continue;
                        targets.Add(DescribeTarget(culprit));
                    }
                }
            }
            catch
            {
                // The alert stays in the list with its label: an alert that
                // cannot name its culprits is still an alert, and dropping it
                // would make it indistinguishable from one that is not firing.
            }

            row["targets"] = targets;
            row["targetCount"] = targetCount;
            row["targetsTruncated"] = targetCount > targets.Count;
            // An alert with no culprits at all is map-wide (Need research
            // project, Low food); one whose report threw is not, and this is
            // what separates the two.
            row["culpritsReadable"] = reportRead;
            return row;
        }

        // ============================================================ colonists

        /// <summary>
        /// One compact row per free colonist spawned on the current map.
        /// `IsFreeColonist` rather than MapPawns.FreeColonistsSpawned: that
        /// property's body is `FreeHumanlikesSpawnedOfFaction(Faction.OfPlayer)`
        /// and Faction.OfPlayer's tail is Log.Error, which pauses the colony.
        /// </summary>
        private static Dictionary<string, object> ColonistRow(Pawn pawn, bool detail)
        {
            var row = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "name", SafeName(pawn) },
                { "thingId", BridgeCommon.SafeString(() => pawn.GetUniqueLoadID()) },
                { "position", BridgeCommon.PositionOf(pawn) },
                { "dead", BridgeCommon.Try(() => pawn.Dead, false) },
                { "downed", BridgeCommon.Try(() => pawn.Downed, false) },
                { "drafted", BridgeCommon.Try(() => pawn.drafter != null && pawn.drafter.Drafted, false) },
                { "inBed", BridgeCommon.Try(() => RestUtility.InBed(pawn), false) },
                { "job", BridgeCommon.SafeString(() => pawn.CurJobDef != null ? pawn.CurJobDef.defName : null) },
                { "mentalState", BridgeCommon.SafeString(() => pawn.MentalStateDef != null ? pawn.MentalStateDef.defName : null) }
            };

            var needs = BridgeCommon.Try(() => pawn.needs, (Pawn_NeedsTracker)null);
            var mood = needs == null ? null : Round3(BridgeCommon.Try(
                () => needs.mood != null ? (float?)needs.mood.CurLevelPercentage : null, (float?)null));
            row["mood"] = mood;
            row["breakRisk"] = BreakRisk(pawn, mood);

            var health = BridgeCommon.Try(() => pawn.health, (Pawn_HealthTracker)null);
            row["healthPct"] = health == null ? null : Round3(BridgeCommon.Try(
                () => health.summaryHealth != null ? (float?)health.summaryHealth.SummaryHealthPercent : null, (float?)null));
            row["needsTend"] = health != null && BridgeCommon.Try(() => health.HasHediffsNeedingTend(false), false);

            var bleed = health == null ? null : BridgeCommon.Try(
                () => health.hediffSet != null ? (float?)health.hediffSet.BleedRateTotal : null, (float?)null);
            row["bleeding"] = bleed.HasValue && bleed.Value > 0f;
            row["bleedRatePerDay"] = Round3(bleed);

            if (detail)
            {
                row["needs"] = NeedsDetail(pawn, needs);
                row["hediffs"] = HediffLabels(health);
            }

            return row;
        }

        /// <summary>
        /// The mood band, in a word. `mentalState` only becomes non-null AFTER a
        /// break; this is the number that lets a caller see one coming. Null for
        /// a pawn with no mood need at all.
        /// </summary>
        private static string BreakRisk(Pawn pawn, double? mood)
        {
            if (!mood.HasValue)
                return null;

            double? minor = null, major = null, extreme = null;
            var breaker = BridgeCommon.Try(
                () => pawn.mindState != null ? pawn.mindState.mentalBreaker : null, (MentalBreaker)null);
            if (breaker != null)
            {
                minor = Round3(BridgeCommon.Try(() => (float?)breaker.BreakThresholdMinor, (float?)null));
                major = Round3(BridgeCommon.Try(() => (float?)breaker.BreakThresholdMajor, (float?)null));
                extreme = Round3(BridgeCommon.Try(() => (float?)breaker.BreakThresholdExtreme, (float?)null));
            }

            if (extreme.HasValue && mood.Value <= extreme.Value) return "extreme";
            if (major.HasValue && mood.Value <= major.Value) return "major";
            if (minor.HasValue && mood.Value <= minor.Value) return "minor";
            return "none";
        }

        private static Dictionary<string, object> NeedsDetail(Pawn pawn, Pawn_NeedsTracker needs)
        {
            var block = new Dictionary<string, object>(StringComparer.Ordinal);
            var all = new Dictionary<string, object>(StringComparer.Ordinal);

            if (needs != null)
            {
                try
                {
                    var list = needs.AllNeeds;
                    if (list != null)
                    {
                        foreach (var need in list)
                        {
                            if (need == null || need.def == null)
                                continue;
                            all[need.def.defName] = Round3(BridgeCommon.Try(
                                () => (float?)need.CurLevelPercentage, (float?)null));
                        }
                    }
                }
                catch { }
            }

            block["all"] = all;
            block["food"] = needs == null ? null : Round3(BridgeCommon.Try(
                () => needs.food != null ? (float?)needs.food.CurLevelPercentage : null, (float?)null));
            block["hungerCategory"] = needs == null ? null : BridgeCommon.SafeString(
                () => needs.food != null ? needs.food.CurCategory.ToString() : null);
            block["rest"] = needs == null ? null : Round3(BridgeCommon.Try(
                () => needs.rest != null ? (float?)needs.rest.CurLevelPercentage : null, (float?)null));
            block["joy"] = needs == null ? null : Round3(BridgeCommon.Try(
                () => needs.joy != null ? (float?)needs.joy.CurLevelPercentage : null, (float?)null));

            var breaker = BridgeCommon.Try(
                () => pawn.mindState != null ? pawn.mindState.mentalBreaker : null, (MentalBreaker)null);
            block["breakThresholdMinor"] = breaker == null ? null : Round3(BridgeCommon.Try(() => (float?)breaker.BreakThresholdMinor, (float?)null));
            block["breakThresholdMajor"] = breaker == null ? null : Round3(BridgeCommon.Try(() => (float?)breaker.BreakThresholdMajor, (float?)null));
            block["breakThresholdExtreme"] = breaker == null ? null : Round3(BridgeCommon.Try(() => (float?)breaker.BreakThresholdExtreme, (float?)null));
            return block;
        }

        /// <summary>
        /// Every hediff's label and the severity word the game shows. No watch
        /// list: the caller filters, because a curated list of interesting
        /// conditions is how "Food poisoning" gets dropped in silence.
        /// </summary>
        private static List<object> HediffLabels(Pawn_HealthTracker health)
        {
            var rows = new List<object>();
            if (health == null)
                return rows;

            List<Hediff> hediffs;
            try
            {
                var set = health.hediffSet;
                hediffs = set == null || set.hediffs == null
                    ? new List<Hediff>()
                    : set.hediffs.Where(h => h != null).ToList();
            }
            catch { return rows; }

            foreach (var hediff in hediffs)
            {
                rows.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    { "label", BridgeCommon.SafeString(() => hediff.LabelCap) },
                    { "severityLabel", BridgeCommon.SafeString(() => hediff.SeverityLabel) },
                    { "part", BridgeCommon.SafeString(() => hediff.Part != null ? hediff.Part.LabelCap : null) },
                    { "bleeding", BridgeCommon.Try(() => hediff.Bleeding, false) }
                });
            }
            return rows;
        }

        // ============================================================== threats

        private static Dictionary<string, object> EmptyThreats()
        {
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "hostileCount", 0 },
                { "hostiles", new List<object>() },
                { "huntingPredatorCount", 0 },
                { "huntingPredators", new List<object>() },
                { "huntersIgnored", new List<object>() },
                { "downedNearCount", 0 },
                { "downedNear", new List<object>() },
                { "predatorRadius", 0 }
            };
        }

        /// <summary>
        /// Verse.RaceProperties.predator -- a public bool FIELD on the race def.
        /// The game's own predator flag, which is why nothing in this stack
        /// needs a hardcoded list of defNames; a list written from memory is
        /// wrong the moment a mod adds a race, and it was already wrong for the
        /// wild boars that stood fifteen cells from a colonist unreported.
        /// </summary>
        private static bool SafePredator(Pawn pawn)
        {
            try { return pawn != null && pawn.RaceProps != null && pawn.RaceProps.predator; }
            catch { return false; }
        }

        /// <summary>Verse.RaceProperties.manhunterOnDamageChance, 0..1: the
        /// chance this race turns on whoever hurt it. The RAW race field;
        /// RimWorld's own stat line routes through
        /// PawnUtility.GetManhunterOnDamageChance, which applies modifiers this
        /// does not.</summary>
        private static float SafeManhunterOnDamageChance(Pawn pawn)
        {
            try { return pawn != null && pawn.RaceProps != null ? pawn.RaceProps.manhunterOnDamageChance : 0f; }
            catch { return 0f; }
        }

        /// <summary>
        /// Hostiles and hunting predators, on the same tests home/play_until_event
        /// stops for — including the tame-predator exclusion it documents: a
        /// colony's own warg runs the same PredatorHunt job a cougar runs, and a
        /// wild lynx eating a wild hare is wildlife, not a threat. Both are
        /// reported in huntersIgnored[] with the reason rather than dropped.
        /// </summary>
        private static Dictionary<string, object> ThreatBlock(List<Pawn> spawned, List<Pawn> colonists, int predatorRadius)
        {
            var hostiles = new List<object>();
            var hunters = new List<object>();
            var ignored = new List<object>();
            var nearDowned = new List<object>();
            var hostileCount = 0;
            var hunterCount = 0;

            foreach (var pawn in spawned)
            {
                if (pawn == null || BridgeCommon.Try(() => pawn.Dead, false))
                    continue;
                if (BridgeCommon.Try(() => pawn.IsColonist, false))
                    continue;

                var nearest = NearestColonist(pawn, colonists);

                string reason;
                if (IsHostile(pawn, out reason))
                {
                    hostileCount++;
                    if (hostiles.Count < MaxHostiles)
                        hostiles.Add(DescribeThreatPawn(pawn, reason, nearest));
                    continue;
                }

                var job = BridgeCommon.SafeString(() => pawn.CurJobDef != null ? pawn.CurJobDef.defName : null);
                if (!string.Equals(job, "PredatorHunt", StringComparison.OrdinalIgnoreCase))
                {
                    // Neither hostile nor hunting. Only a downed pawn within
                    // predatorRadius is listed from here.
                    if (predatorRadius <= 0 || !nearest.HasValue || nearest.Value > predatorRadius)
                        continue;
                    if (IsPlayerFactionPawn(pawn))
                        continue;

                    // A manhunter that goes DOWN loses its mental state, so
                    // IsHostile stops being true and it silently leaves
                    // hostiles[]. The muffalo that mauled a colonist is then one
                    // cell away and invisible. It is listed here instead.
                    if (BridgeCommon.Try(() => pawn.Downed, false)
                        && nearDowned.Count < MaxHostiles)
                        nearDowned.Add(DescribeThreatPawn(pawn, "downed", nearest));

                    continue;
                }

                var row = DescribeThreatPawn(pawn, "predatorHunt", nearest);
                Pawn prey;
                var preyIsOurs = PreyBelongsToPlayer(pawn, out prey);
                var predatorIsOurs = IsPlayerFactionPawn(pawn);

                row["predatorIsOurs"] = predatorIsOurs;
                row["prey"] = prey == null ? null : SafeName(prey);
                row["preyIsOurs"] = preyIsOurs;

                if (predatorIsOurs)
                {
                    row["ignoredReason"] = "tame: this predator is on the player faction, so its hunt is ours";
                    ignored.Add(row);
                }
                else if (!preyIsOurs)
                {
                    row["ignoredReason"] = prey == null
                        ? "prey could not be read off the job; a hunt with no readable target is not treated as a threat"
                        : "prey is not a colonist, a colony animal or a colony prisoner";
                    ignored.Add(row);
                }
                else
                {
                    row["ignoredReason"] = null;
                    hunterCount++;
                    hunters.Add(row);
                }
            }

            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "hostileCount", hostileCount },
                { "hostiles", hostiles },
                { "huntingPredatorCount", hunterCount },
                { "huntingPredators", hunters },
                { "huntersIgnored", ignored },
                // Outside hostileCount on purpose: nothing in here is attacking yet.
                { "downedNearCount", nearDowned.Count },
                { "downedNear", nearDowned },
                { "predatorRadius", predatorRadius }
            };
        }

        private static Dictionary<string, object> DescribeThreatPawn(Pawn pawn, string reason, int? nearest)
        {
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "name", SafeName(pawn) },
                { "thingId", BridgeCommon.SafeString(() => pawn.GetUniqueLoadID()) },
                { "defName", BridgeCommon.SafeString(() => pawn.def != null ? pawn.def.defName : null) },
                { "kindDef", BridgeCommon.SafeString(() => pawn.kindDef != null ? pawn.kindDef.defName : null) },
                { "faction", BridgeCommon.SafeString(() => pawn.Faction != null ? pawn.Faction.Name : null) },
                { "position", BridgeCommon.PositionOf(pawn) },
                { "hostileReason", reason },
                { "job", BridgeCommon.SafeString(() => pawn.CurJobDef != null ? pawn.CurJobDef.defName : null) },
                { "downed", BridgeCommon.Try(() => pawn.Downed, false) },
                // Verse.RaceProperties.predator and .manhunterOnDamageChance,
                // on EVERY threat row, not just the predator lists: whether the
                // thing shooting back is a wolf, and how likely it is to turn on
                // whoever shoots it, are the two facts that decide the order.
                { "predator", SafePredator(pawn) },
                { "manhunterOnDamageChance", SafeManhunterOnDamageChance(pawn) },
                { "distanceToNearestColonist", nearest }
            };
        }

        /// <summary>Manhunter, or a faction hostile to the player. The player
        /// faction comes from Faction.OfPlayerSilentFail; a null player faction
        /// is an answer (nothing is hostile to nobody), never a Log.Error.</summary>
        private static bool IsHostile(Pawn pawn, out string reason)
        {
            reason = "none";

            var mental = BridgeCommon.SafeString(
                () => pawn.MentalStateDef != null ? pawn.MentalStateDef.defName : null);
            if (!string.IsNullOrEmpty(mental)
                && mental.IndexOf("Manhunter", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                reason = "manhunter:" + mental;
                return true;
            }

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

        /// <summary>NOT Faction.OfPlayer: its body is OfPlayerSilentFail followed
        /// by Verse.Log.Error, and Log.Error calls TickManager.Pause(), so asking
        /// who the player is on a map with no player faction would pause the
        /// colony.</summary>
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

        /// <summary>The prey of a PredatorHunt job, and whether the colony owns
        /// it. job.targetA is the prey (JobDriver_PredatorHunt.PreyInd is
        /// TargetIndex.A); it holds the prey pawn during the chase and the prey's
        /// Corpse once the kill is made, so both are unwrapped.</summary>
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
                    return true;                        // colonist, tame animal, colony pet
                return prey.HostFaction == player;      // our prisoner
            }
            catch { return false; }
        }

        private static int? NearestColonist(Pawn pawn, List<Pawn> colonists)
        {
            int? best = null;
            try
            {
                var here = pawn.Position;
                foreach (var colonist in colonists)
                {
                    if (colonist == null || colonist == pawn)
                        continue;
                    var there = colonist.Position;
                    var d = Math.Max(Math.Abs(there.x - here.x), Math.Abs(there.z - here.z));
                    if (best == null || d < best.Value)
                        best = d;
                }
            }
            catch { return best; }
            return best;
        }

        // =================================================================== ui

        /// <summary>
        /// What is on screen. `modalOpen` is the check move.blocking_window()
        /// does by hand, decided here from the window LIST rather than from
        /// focusedWindowType: the letter stack is itself a Super-layer
        /// ImmediateWindow and takes the focus field, so the focus test reports
        /// no dialog while a modal sits open. A window that absorbs input or
        /// force-pauses outranks one that merely sits on the Dialog layer, and
        /// among equals the last (topmost) wins.
        /// </summary>
        private static Dictionary<string, object> UiBlock(List<object> skipped)
        {
            var block = new Dictionary<string, object>(StringComparer.Ordinal);

            var stack = BridgeCommon.Try(() => Find.WindowStack, (WindowStack)null);
            var windowRows = new List<object>();
            string modalWindow = null;
            var modalRank = -1;

            if (stack == null)
            {
                skipped.Add(Skip("ui.windows", "Find.WindowStack is null; windows[] is empty because it was not read."));
            }
            else
            {
                List<Window> windows;
                try
                {
                    var list = stack.Windows;
                    windows = list == null ? new List<Window>() : list.Where(w => w != null).ToList();
                }
                catch
                {
                    windows = new List<Window>();
                    skipped.Add(Skip("ui.windows", "WindowStack.Windows threw; windows[] is empty because it was not read."));
                }

                var notListed = 0;
                for (var i = 0; i < windows.Count; i++)
                {
                    var window = windows[i];
                    var type = BridgeCommon.SafeString(() => window.GetType().FullName) ?? "unknown";
                    var forcePause = BridgeCommon.Try(() => window.forcePause, false);
                    var absorb = BridgeCommon.Try(() => window.absorbInputAroundWindow, false);
                    var layer = BridgeCommon.SafeString(() => window.layer.ToString());

                    if (windowRows.Count < MaxWindows)
                    {
                        windowRows.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                        {
                            { "type", type },
                            { "layer", layer },
                            { "title", BridgeCommon.SafeString(() => window.optionalTitle) },
                            { "forcePause", forcePause },
                            { "absorbInputAroundWindow", absorb }
                        });
                    }
                    else
                    {
                        notListed++;
                    }

                    if (IsNonBlockingWindowType(type))
                        continue;
                    var isModal = forcePause || absorb;
                    if (!isModal && !string.Equals(layer, "Dialog", StringComparison.Ordinal))
                        continue;

                    // Rank: a window that eats input beats one that only sits on
                    // the Dialog layer; >= so a later window of equal rank (the
                    // topmost, most recently opened) wins.
                    var rank = isModal ? 1 : 0;
                    if (rank >= modalRank)
                    {
                        modalRank = rank;
                        modalWindow = type;
                    }
                }

                if (notListed > 0)
                    skipped.Add(Skip("ui.windows", notListed + " window(s) past the " + MaxWindows + "-row cap were not listed."));
            }

            block["windows"] = windowRows;
            block["windowCount"] = windowRows.Count;
            block["modalOpen"] = modalWindow != null;
            block["modalWindow"] = modalWindow;
            block["windowsForcePause"] = stack != null && BridgeCommon.Try(() => stack.WindowsForcePause, false);
            block["anyWindowAbsorbingAllInput"] = stack != null && BridgeCommon.Try(() => stack.AnyWindowAbsorbingAllInput, false);
            block["nonImmediateDialogWindowOpen"] = stack != null && BridgeCommon.Try(() => stack.NonImmediateDialogWindowOpen, false);

            // MainTabsRoot.OpenTab is WindowStack.WindowOfType<MainTabWindow>()?.def
            // — a lookup, not a click.
            var openTab = BridgeCommon.Try(
                () => Find.MainTabsRoot != null ? Find.MainTabsRoot.OpenTab : null, (MainButtonDef)null);
            block["mainTabOpen"] = openTab != null;
            block["mainTabDefName"] = openTab == null ? null : BridgeCommon.SafeString(() => openTab.defName);
            block["mainTabLabel"] = openTab == null ? null : BridgeCommon.SafeString(() => openTab.LabelCap);

            var selected = new List<object>();
            try
            {
                var selector = Find.Selector;
                var objects = selector == null ? null : selector.SelectedObjectsListForReading;
                if (objects != null)
                {
                    foreach (var item in objects)
                        if (item != null)
                            selected.Add(item);
                }
            }
            catch
            {
                skipped.Add(Skip("ui.selection", "Find.Selector.SelectedObjectsListForReading threw; selectedCount is 0 because it was not read."));
            }

            block["selectedCount"] = selected.Count;
            block["selectedFirstLabel"] = selected.Count == 0 ? null : SelectableLabel(selected[0]);
            block["targeter"] = TargeterBlock(skipped);
            return block;
        }

        /// <summary>
        /// Find.Targeter, as {active, source, caster}. The one piece of screen
        /// state nothing in the stack could see.
        ///
        /// A gizmo that opens a targeter -- "Deploy turret" on a turret pack,
        /// any Command_VerbTarget, any Command_Target -- opens NO window, so a
        /// window-stack diff (the bridge's own `changed` flag, and ui.py's
        /// surface signature) reads identical before and after and the click
        /// reports "nothing opened or closed". The targeter was open the whole
        /// time. Live 2026-09-08, Threadneedle turns 12-17.
        ///
        /// Hazards:
        /// * `Find.Targeter` is `((UIRoot_Play)UIRoot).mapUI.targeter` -- a HARD
        ///   CAST that throws InvalidCastException outside a play UI root. Read
        ///   through BridgeCommon.Try, never bare.
        /// * `Targeter.caster` is PRIVATE and is the only caster a
        ///   Command_Target leaves behind (that overload sets targetingSource
        ///   to null), so it is reached by reflection. A Command_VerbTarget
        ///   goes the other way: targetingSource IS the Verb and CasterPawn is
        ///   the wearer.
        /// * `Command.LabelCap` is virtual and a subclass may compute it; the
        ///   plain `defaultLabel` FIELD is read first for that reason.
        /// </summary>
        private static Dictionary<string, object> TargeterBlock(List<object> skipped)
        {
            var block = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "active", false },
                { "source", null },
                { "caster", null }
            };

            var targeter = BridgeCommon.Try(() => Find.Targeter, (Targeter)null);
            if (targeter == null)
            {
                skipped.Add(Skip("ui.targeter", "Find.Targeter is not readable here (no play UI root); active is false because it was not read."));
                return block;
            }

            var active = BridgeCommon.Try(() => targeter.IsTargeting, false);
            block["active"] = active;
            if (!active)
                return block;

            var source = BridgeCommon.Try(() => targeter.targetingSource, (ITargetingSource)null)
                         ?? BridgeCommon.Try(() => targeter.targetingSourceParent, (ITargetingSource)null);

            string label = null;
            Pawn caster = null;

            if (source != null)
            {
                label = TargetingSourceLabel(source);
                caster = BridgeCommon.Try(() => source.CasterPawn, (Pawn)null);
            }

            if (label == null)
                label = TargeterDelegateLabel(targeter);
            if (caster == null)
                caster = TargeterPrivateCaster(targeter);

            block["source"] = label;
            block["caster"] = caster == null ? null : BridgeCommon.SafeString(() => caster.GetUniqueLoadID());
            return block;
        }

        /// <summary>The gizmo text for a verb-shaped targeting source: the verb's
        /// own label, which is exactly what CompApparelVerbOwner copies into the
        /// gizmo's defaultLabel.</summary>
        private static string TargetingSourceLabel(ITargetingSource source)
        {
            var verb = BridgeCommon.Try(() => source.GetVerb, (Verb)null);
            if (verb != null)
            {
                var castAbility = verb as Verb_CastAbility;
                if (castAbility != null)
                {
                    var abilityLabel = BridgeCommon.SafeString(
                        () => castAbility.ability == null || castAbility.ability.def == null
                            ? null
                            : castAbility.ability.def.LabelCap);
                    if (!string.IsNullOrWhiteSpace(abilityLabel))
                        return abilityLabel;
                }

                var verbLabel = BridgeCommon.SafeString(
                    () => verb.verbProps == null ? null : verb.verbProps.label);
                if (!string.IsNullOrWhiteSpace(verbLabel))
                    return BridgeCommon.SafeString(() => verbLabel.CapitalizeFirst()) ?? verbLabel;
            }

            return BridgeCommon.SafeString(() => source.GetType().Name);
        }

        /// <summary>
        /// The label for a targeter opened by Command_Target, which leaves
        /// targetingSource null and keeps the gizmo only inside its callbacks.
        /// `onUpdateAction` is the reliable one: Command_Target passes its own
        /// `Update` METHOD GROUP, so the delegate's Target is the gizmo itself.
        /// </summary>
        private static string TargeterDelegateLabel(Targeter targeter)
        {
            for (var i = 0; i < TargeterDelegateFields.Length; i++)
            {
                var field = BridgeCommon.PrivateInstanceField(typeof(Targeter), TargeterDelegateFields[i]);
                if (field == null)
                    continue;
                var del = BridgeCommon.Try(() => field.GetValue(targeter) as Delegate, (Delegate)null);
                var label = DelegateOwnerLabel(del);
                if (label != null)
                    return label;
            }
            return null;
        }

        private static readonly string[] TargeterDelegateFields =
        {
            "onUpdateAction", "highlightAction", "onGuiAction", "action"
        };

        /// <summary>The name of whatever object owns a targeter callback: the
        /// gizmo if the delegate closed over one, else the declaring type.</summary>
        private static string DelegateOwnerLabel(Delegate del)
        {
            if (del == null)
                return null;

            var owner = OwningGizmo(BridgeCommon.Try(() => del.Target, (object)null));
            var command = owner as Command;
            if (command != null)
            {
                var plain = BridgeCommon.SafeString(() => command.defaultLabel);
                if (!string.IsNullOrWhiteSpace(plain))
                    return BridgeCommon.SafeString(() => plain.CapitalizeFirst()) ?? plain;
                var cap = BridgeCommon.SafeString(() => command.LabelCap);
                if (!string.IsNullOrWhiteSpace(cap))
                    return cap;
            }
            if (owner != null)
                return BridgeCommon.SafeString(() => owner.GetType().Name);

            var declaring = BridgeCommon.Try(
                () => del.Method == null ? null : del.Method.DeclaringType, (Type)null);
            return declaring == null ? null : BridgeCommon.SafeString(() => declaring.Name);
        }

        /// <summary>A delegate target is either the gizmo itself (a method group
        /// or a lambda that captured only `this`) or a compiler display class
        /// holding it in a field. Both shapes are unwrapped here; nothing else
        /// is followed.</summary>
        private static Gizmo OwningGizmo(object target)
        {
            var direct = target as Gizmo;
            if (direct != null)
                return direct;
            if (target == null)
                return null;

            var fields = BridgeCommon.Try(
                () => target.GetType().GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic),
                new FieldInfo[0]);
            for (var i = 0; i < fields.Length; i++)
            {
                var index = i;
                var held = BridgeCommon.Try(() => fields[index].GetValue(target) as Gizmo, (Gizmo)null);
                if (held != null)
                    return held;
            }
            return null;
        }

        /// <summary>`Targeter.caster` is private and is the only pawn a
        /// Command_Target-opened targeter records.</summary>
        private static Pawn TargeterPrivateCaster(Targeter targeter)
        {
            var field = BridgeCommon.PrivateInstanceField(typeof(Targeter), "caster");
            if (field == null)
                return null;
            return BridgeCommon.Try(() => field.GetValue(targeter) as Pawn, (Pawn)null);
        }

        private static bool IsNonBlockingWindowType(string type)
        {
            if (string.IsNullOrEmpty(type))
                return false;
            for (var i = 0; i < NonBlockingWindowPrefixes.Length; i++)
                if (type.StartsWith(NonBlockingWindowPrefixes[i], StringComparison.Ordinal))
                    return true;
            return false;
        }

        /// <summary>A selected object's name. Zones are ISelectable but not
        /// Things, so both are tried before the type name.</summary>
        private static string SelectableLabel(object selection)
        {
            var thing = selection as Thing;
            if (thing != null)
            {
                var label = BridgeCommon.SafeString(() => thing.LabelCap);
                if (!string.IsNullOrEmpty(label))
                    return label;
            }

            var zone = selection as Zone;
            if (zone != null)
            {
                var label = BridgeCommon.SafeString(() => zone.label);
                if (!string.IsNullOrEmpty(label))
                    return label;
            }

            return BridgeCommon.SafeString(() => selection.GetType().Name);
        }

        // =============================================================== shared

        private static Dictionary<string, object> Counts(
            List<object> letters, List<object> messages, List<object> alerts,
            List<object> colonists, Dictionary<string, object> threats)
        {
            var letterChoices = 0;
            var loud = 0;
            var downed = 0;

            foreach (var entry in letters)
            {
                var row = entry as Dictionary<string, object>;
                if (row != null && row.ContainsKey("choiceCount") && row["choiceCount"] is int)
                    letterChoices += (int)row["choiceCount"];
            }

            foreach (var entry in alerts)
            {
                var row = entry as Dictionary<string, object>;
                if (row == null)
                    continue;
                // AlertPriority is an ordered byte enum: Medium 0, High 1,
                // Critical 2. The rank is compared, never one name matched, so
                // a priority above the set still counts as loud.
                if (row.ContainsKey("prioritySortValue") && row["prioritySortValue"] is int
                    && (int)row["prioritySortValue"] >= (int)AlertPriority.High)
                    loud++;
            }

            foreach (var entry in colonists)
            {
                var row = entry as Dictionary<string, object>;
                if (row == null)
                    continue;
                if ((row.ContainsKey("downed") && row["downed"] is bool && (bool)row["downed"])
                    || (row.ContainsKey("dead") && row["dead"] is bool && (bool)row["dead"]))
                    downed++;
            }

            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "letterCount", letters.Count },
                { "letterChoiceCount", letterChoices },
                { "messageCount", messages.Count },
                { "alertCount", alerts.Count },
                { "loudAlertCount", loud },
                { "colonistCount", colonists.Count },
                { "downedCount", downed },
                { "hostileCount", threats.ContainsKey("hostileCount") ? threats["hostileCount"] : 0 },
                { "huntingPredatorCount", threats.ContainsKey("huntingPredatorCount") ? threats["huntingPredatorCount"] : 0 },
                // Separate numbers on purpose. Rolling these into hostileCount
                // would make "hostileCount 0" stop meaning "nobody is fighting".
                { "downedNearCount", threats.ContainsKey("downedNearCount") ? threats["downedNearCount"] : 0 }
            };
        }

        /// <summary>
        /// The four things a caller gets wrong reading this payload. Kept short
        /// on purpose: this is the between-turns call, and prose it already
        /// knows is the cheapest thing to cut.
        /// </summary>
        private static Dictionary<string, object> Notes(bool wantExplanations)
        {
            var notes = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["messagesExpire"] = "messages[] dies 13 REAL seconds after it appears whatever the clock is doing. Read ageSeconds; ageTicks is 0 on a paused game.",
                ["alertTargets"] = "Culprit names come from targets[], never from the prose. targetCount 0 with culpritsReadable true is map-wide; culpritsReadable false could not name anyone.",
                ["letterChoices"] = "choiceCount > 0 is NOT 'somebody must decide': an announcement gets a dialog too, buttons Close / Jump to location. Read the choice texts or shouldAutomaticallyOpenLetter.",
                ["modalOpen"] = "From the window LIST, not the focused window: the letter stack is a Super-layer ImmediateWindow and takes the focus field while a modal sits open under it.",
                ["colonistScope"] = "Free colonists spawned on the CURRENT map (Pawn.IsFreeColonist). Prisoners and slaves are not colonists here.",
                ["targeter"] = "ui.targeter.active true means the next map click is consumed as a target, not as a selection. It opens no window, so modalOpen and the window list are both blind to it."
            };

            if (!wantExplanations)
                notes["explanations"] = "Every explanation is null: pass explanations:true for the prose.";

            return notes;
        }

        private static Dictionary<string, object> DescribeTarget(GlobalTargetInfo target)
        {
            var row = new Dictionary<string, object>(StringComparer.Ordinal);

            Thing thing = null;
            try { thing = target.Thing; }
            catch { thing = null; }

            if (thing != null)
            {
                var pawn = thing as Pawn;
                row["kind"] = pawn != null ? "pawn" : "thing";
                row["name"] = pawn != null ? SafeName(pawn) : BridgeCommon.SafeString(() => thing.LabelCap);
                row["thingId"] = BridgeCommon.SafeString(() => thing.GetUniqueLoadID());
                row["defName"] = BridgeCommon.SafeString(() => thing.def != null ? thing.def.defName : null);
                row["position"] = BridgeCommon.PositionOf(thing);
                return row;
            }

            var worldObject = BridgeCommon.Try(() => target.WorldObject, (WorldObject)null);
            if (worldObject != null)
            {
                row["kind"] = "worldObject";
                row["name"] = BridgeCommon.SafeString(() => target.Label);
                row["thingId"] = BridgeCommon.SafeString(() => worldObject.GetUniqueLoadID());
                row["defName"] = null;
                row["position"] = null;
                return row;
            }

            var cell = BridgeCommon.TryN(() => target.Cell);
            row["kind"] = cell.HasValue && cell.Value.IsValid ? "cell" : "target";
            row["name"] = BridgeCommon.SafeString(() => target.Label);
            row["thingId"] = null;
            row["defName"] = null;
            row["position"] = cell.HasValue && cell.Value.IsValid ? BridgeCommon.Pos(cell.Value) : null;
            return row;
        }

        /// <summary>The primary look target of a letter or message as
        /// {kind,name,thingId,defName,position}, or null when there is none.
        /// LookTargets.targets is dereferenced by both IsValid and Any, so the
        /// list is walked directly instead.</summary>
        private static Dictionary<string, object> PrimaryLookTarget(LookTargets lookTargets)
        {
            if (lookTargets == null)
                return null;
            try
            {
                var targets = lookTargets.targets;
                if (targets == null)
                    return null;
                for (var i = 0; i < targets.Count; i++)
                {
                    if (targets[i].IsValid)
                        return DescribeTarget(targets[i]);
                }
            }
            catch { return null; }
            return null;
        }

        private static List<Pawn> SpawnedPawns(Map map)
        {
            try
            {
                if (map == null || map.mapPawns == null)
                    return new List<Pawn>();
                var all = map.mapPawns.AllPawnsSpawned;
                return all == null ? new List<Pawn>() : all.ToList();
            }
            catch { return new List<Pawn>(); }
        }

        /// <summary>Find.CurrentMap, falling back to the first loaded map on the
        /// world view or mid-load, exactly as home/get_time does.</summary>
        private static Map SafeMap()
        {
            try
            {
                if (Current.Game == null || Current.ProgramState != ProgramState.Playing)
                    return null;

                var current = Find.CurrentMap;
                if (current != null)
                    return current;

                var maps = Find.Maps;
                if (maps != null && maps.Count > 0)
                    return maps[0];
            }
            catch { }
            return null;
        }

        private static int CurrentTick()
        {
            return BridgeCommon.Try(() => Find.TickManager != null ? Find.TickManager.TicksGame : 0, 0);
        }

        private static string SafeName(Pawn pawn)
        {
            var name = BridgeCommon.SafeString(() => pawn.LabelShortCap);
            return name ?? BridgeCommon.SafeString(() => pawn.LabelCap);
        }

        private static Dictionary<string, object> Skip(string field, string reason)
        {
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "field", field },
                { "reason", reason }
            };
        }

        private static double? Round3(float? value)
        {
            return value.HasValue
                ? (double?)Math.Round((double)value.Value, 3, MidpointRounding.AwayFromZero)
                : null;
        }

        private static double? Round3(double? value)
        {
            return value.HasValue
                ? (double?)Math.Round(value.Value, 3, MidpointRounding.AwayFromZero)
                : null;
        }

        /// <summary>The shared refusal shape; see BridgeCommon.Failure.</summary>
        private static object Failure(string error)
        {
            return BridgeCommon.Failure(ToolName, error);
        }
    }
}
