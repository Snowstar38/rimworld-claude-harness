using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using RimBridgeServer.Sdk;
using RimWorld;
using Verse;
using Verse.AI;

namespace HomeBridge.BridgeTools
{
    /// <summary>
    /// Vanilla pawn orders issued **as jobs**, with no float menu anywhere near
    /// them.
    ///
    /// ## Why this exists
    ///
    /// On 2026-09-04 two colonists died while the agent playing could not get an
    /// attack order through. Every failure was in the float-menu path, not in
    /// the game:
    ///
    ///   * a target named by ThingID resolved to nothing, and a target named by
    ///     label was "ambiguous" because two rats shared a name;
    ///   * a DOWNED manhunter one cell away was refused, because a downed pawn
    ///     drops its mental state and falls off the hostiles list;
    ///   * a timber wolf that had just killed a colonist was refused for being
    ///     "not hostile any more" -- vanilla lets a drafted pawn melee ANY pawn,
    ///     and hostility is not a precondition anywhere in the game's own code;
    ///   * `open_context_menu` returned zero options because the SELECTING pawn
    ///     was in a mental break, which `FloatMenuMakerMap` refuses outright.
    ///
    /// So: no menu, no synthesised clicks. Resolve the pawn and the target, run
    /// the same checks vanilla runs, build the same `Job` vanilla builds, and
    /// hand it to `Pawn_JobTracker.TryTakeOrderedJob`. Then read `Pawn.CurJob`
    /// back and say whether the game actually took it.
    ///
    /// ## The four id forms
    ///
    /// `Rat361788` matched nothing on the old path, which is the whole bug in
    /// one string. Every spawned thing here answers to all four of:
    ///
    ///   1. `Thing_Rat361788`  -- `Thing.GetUniqueLoadID()`, what `home/status`
    ///      prints as `thingId`;
    ///   2. `Rat361788`        -- `Thing.ThingID`, defName + id;
    ///   3. `361788`           -- `Thing.thingIDNumber`, bare;
    ///   4. `rat` / `Longhoff` -- a label, name or nickname, case-insensitively,
    ///      exact first and then a unique substring.
    ///
    /// Forms 1-3 are EXPLICIT and can never be ambiguous: they either hit one
    /// thing or nothing at all. Only form 4 can tie, and a tie is answered with
    /// `candidates[]` carrying every id form of every candidate, so the caller
    /// learns the addresses it should have used.
    ///
    /// ## Hostility is never a precondition
    ///
    /// `requireHostile` exists, defaults to **false**, and is the only thing in
    /// this tool that looks at whether a target is hostile. `hostileToPlayer` is
    /// reported on every target as information. A downed target, a dead target,
    /// a tame animal and a pawn of the player's own faction all RESOLVE; only
    /// the action decides whether it will act on one.
    ///
    /// ## Everything is on the main thread
    ///
    /// Companion tools dispatch with `MarshalToMainThread = false`, so every
    /// game read and every write below is inside a `ctx.MainThread.InvokeAsync`
    /// hop, and every read goes through the `BridgeCommon.Try` guards -- an
    /// exception escaping into `Verse.Log.Error` calls `TickManager.Pause()`,
    /// which the harness reads as a person pressing space.
    ///
    /// `Faction.OfPlayer` is never called; `Faction.OfPlayerSilentFail` is.
    ///
    /// ## Hazards, IL-scanned against Assembly-CSharp 1.6.9676.17735
    ///
    ///   * **`ForbidUtility.SetForbidden(t, false)` is never called.** It has
    ///     three `Log.Error` arms -- a null thing, a non-`ThingWithComps`, and
    ///     one with no `CompForbiddable` -- and `Log.Error` calls
    ///     `TickManager.Pause()`. Its success arm is one line,
    ///     `comp.Forbidden = value`, which is what `Apply` does instead.
    ///   * **`ForbidUtility.IsForbidden` is never called.** The `(Thing,
    ///     Faction)` overload's body opens with `faction != Faction.OfPlayer`,
    ///     the banned getter, and the `(Thing, Pawn)` overload reaches it
    ///     through `pawn.Faction`. `CompForbiddable.Forbidden` is read direct.
    ///   * **Verified clean**, each grepped for `Log.Error` and `OfPlayer` in
    ///     the exact method called, not merely in the class:
    ///     `CellFinder.StandableCellNear` (pure radial walk),
    ///     `RCellFinder.BestOrderedGotoDestNear` (the class's three
    ///     `Log.Error`s are all in siege and map-edge finders),
    ///     `RestUtility.FindBedFor` (the class's four are in
    ///     `GetBedSleepingSlotPosFor` and `KickOutOfBed`, neither reachable
    ///     from it), `HealthAIUtility` and `EquipmentUtility` (no `Log.Error`
    ///     and no `OfPlayer` anywhere in either).
    ///   * `Pawn_JobTracker.TryTakeOrderedJob` emits `Log.Warning` -- not
    ///     `Log.Error` -- when a job's pre-toil reservations fail, and
    ///     `Log.Warning` has no `Pause()` in it. A failed reservation is
    ///     therefore a returned false, not a paused colony.
    ///   * `Pawn_MeleeVerbs.TryGetMeleeVerb` memoises `curMeleeVerb`. It is a
    ///     memo behind a tick stamp, the same class as `HediffSet.PainTotal`,
    ///     and it is the call `FloatMenuUtility.GetMeleeAttackAction` itself
    ///     makes, so the game is already making it on every right click.
    ///   * `Pawn_DraftController.Drafted`'s setter holds **no** guard at all --
    ///     no violence check, no mental-state check, no capability check. What
    ///     stops a player drafting a broken pawn is one level up, in
    ///     `Pawn.GetGizmos`, which draws the drafter's gizmos only when
    ///     `IsColonistPlayerControlled`. See `CanBeDrafted`.
    /// </summary>
    public sealed class HomeOrderTools
    {
        private const string ToolName = "home/order";

        /// <summary>Every action this tool understands, in the order they are
        /// documented. Used for validation and for the refusal text.</summary>
        private static readonly string[] Actions =
        {
            "resolve", "draft", "undraft", "attack", "goto", "equip", "rescue", "tend", "haul", "work"
        };

        /// <summary>Attack modes.</summary>
        private static readonly string[] Modes = { "auto", "melee", "ranged" };

        // =================================================================
        // The tool
        // =================================================================

        [Tool(
            ToolName,
            Title = "Order a colonist directly, as a job",
            Description =
                "Issues a vanilla pawn order as a JOB, bypassing the right-click float menu entirely. "
                + "action = resolve | draft | undraft | attack | goto | equip | rescue | tend | haul | work. "
                + "The pawn and the target each accept four id forms - the full Thing_Human123, the ThingID Human123, the bare "
                + "number 123, or a name/label case-insensitively - so an explicit id can never be 'ambiguous'. Hostility is "
                + "NEVER a precondition: a drafted pawn may attack any spawned pawn, downed or not, hostile or not, exactly as "
                + "vanilla allows; pass requireHostile:true if you want the old behaviour. dryRun resolves everything, runs every "
                + "refusal check and reports the job it WOULD issue without touching the game, which makes it the oracle for "
                + "'can this pawn attack that thing'. The float menu is never opened and no click is ever synthesised, so a pawn "
                + "in a mental break - which makes FloatMenuMakerMap return zero options - is refused here with a readable "
                + "reason instead of an empty list.",
            ResultDescription =
                "success, action, dryRun, applied, pawn{}, target{}, job{}, wouldIssue{}, candidates[], after{}, watch{}, "
                + "error, errorKind.")]
        [ToolResponse("action", "string", "The action that ran, lower-cased. Echoed even on a refusal.", Always = true)]
        [ToolResponse("dryRun", "boolean", "True = nothing was written. Defaults to FALSE for this tool, unlike the other write tools: an order that quietly did not happen is what this tool exists to stop.", Always = true)]
        [ToolResponse("applied", "boolean", "True only when the game actually took a draft change or a job. False on every dry run, every refusal, and on resolve.", Always = true)]
        [ToolResponse("pawn", "object", "The colonist being ordered: thingId, idForms[], name, position, spawned, faction, drafted, autoDrafted, autoUndrafted, downed, dead, mentalState, playerControlled, incapableOfViolence, canBeDrafted, canBeDraftedReason, weapon{}. Null only when no pawn was asked for (resolve with target alone) or none resolved.", Nullable = true)]
        [ToolResponse("target", "object", "What the order acts on: thingId, idForms[], name, label, defName, kindDef, faction, isPawn, spawned, hostileToPlayer, mentalState, downed, dead, predator, position, distance, reachable, matchedBy (loadId, thingID, idNumber, defNameAtCell, name, nameSubstring or cell -- which form actually matched). Null when the action takes no target. A downed or DEAD target still resolves - dead is reported, not hidden.", Nullable = true)]
        [ToolResponse("job", "object", "The job that was issued: def, targetA, targetB, verb, mode, killIncappedTarget, draftedTend, count, expiryInterval, unforbade, jobTag, workGiver, workType, billLabel, tendPath, rescuePath, issued, verified, verifiedReason, note. tendPath is \"work\" when the ordinary undrafted WorkGiver_Tend prioritize order was used and \"drafted\" when the patient was on the ground and the drafted provider was needed; rescuePath is the same distinction for rescue. Both are set under dryRun too, so wouldIssue says which path WOULD run. Every field is the shape vanilla's own float-menu code builds, nothing added. verified is Pawn.CurJob read back AFTER the issue and compared by def and target, so a job the game silently dropped reads as false. Null on a dry run, a refusal, and on resolve/draft/undraft.", Nullable = true)]
        [ToolResponse("wouldIssue", "object", "Under dryRun, the same shape as job{} for the job that WOULD have been issued, with issued and verified false. Null on a real run and on any refusal.", Nullable = true)]
        [ToolResponse("candidates", "array", "On an ambiguous name: the things it matched, each with thingId, idForms[], name, defName, position, distance. Capped at 25 rows; the error text carries the true total. Empty when nothing was ambiguous. Pass one of the idForms back to address exactly one -- an explicit id form never ties.", Always = true)]
        [ToolResponse("after", "object", "Read back from the game after the write: drafted, jobDef, jobTarget, mentalState. On a dry run this is the state before, unchanged.", Nullable = true)]
        [ToolResponse("diagnostics", "object", "Action diagnostics. For haul: globalHaulCandidateCount and targetInGlobalHaulList show the lister state; checks reports designation, reservation, reachability, manipulation, fire/fog and storage-search results. Always present.", Always = true)]
        [ToolResponse("watch", "object", "The decorative half: shown, selected, inspectTab, mainTab, cameraMoved, leadMs, closesAfterSeconds, note, reason. The target is selected first and the camera jumps to it, then after the lead the PAWN is selected so the inspect pane shows the new job. Never opens a float menu. reason says why nothing was shown on a dry run, a refusal or watch:false.", Always = true)]
        [ToolResponse("error", "string", "Why the call was refused, in a sentence naming what was resolved. Null when it was not refused.", Nullable = true)]
        [ToolResponse("errorKind", "string", "One of pawn_not_found, target_not_found, ambiguous, pawn_dead, pawn_downed, mental_state, incapable_of_violence, target_dead, not_reachable, draft_refused, draft_cleanup_required, job_refused, job_unverified, bad_arguments, work_disabled, missing_haul_designation, no_storage, no_bill. Null when there was no refusal.", Nullable = true)]
        [ToolResponse("unknownArguments", "array", "Every argument key the caller sent that this tool does not declare, sorted, case-sensitively. Empty array = every key was recognised.", Always = true)]
        [ToolResponse("unknownArgumentsWarning", "string", "Present only when unknownArguments is non-empty, or when the caller's raw keys could not be read at all - in which case the empty unknownArguments means 'not known', not 'nothing unknown'.", Nullable = true)]
        public async Task<object> Order(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            [ToolParameter(Description = "resolve | draft | undraft | attack | goto | equip | rescue | tend | haul | work. resolve reads and mutates nothing and is the safe way to learn a thing's id forms. haul is 'prioritize hauling X'; work is 'prioritize doing bills at X' - both go through the same WorkGiver path the float menu uses, so the job is the giver's own.", DefaultValue = "resolve")] string action = "resolve",
            [ToolParameter(Description = "The colonist to order. Any of: the full thingId (Thing_Human123), the ThingID (Human123), the bare number (123), or a name/nickname case-insensitively. Required for every action except a resolve that names only a target.")] string pawn = null,
            [ToolParameter(Description = "What to act on, for attack / rescue / tend / equip, and optional under resolve. Any spawned pawn or thing on the current map, in any of the same four id forms plus its label, plus the DefName@x,z form home/bills and home/building_config take (TableMachining@62,141) so a bench addressed by bills.py is addressable here. Hostility is never required.")] string target = null,
            [ToolParameter(Description = "Destination cell x, for goto. Also a fallback locator for equip - the weapon lying on that cell - when target is not given.", DefaultValue = int.MinValue)] int x = int.MinValue,
            [ToolParameter(Description = "Destination cell z, for goto. See x.", DefaultValue = int.MinValue)] int z = int.MinValue,
            [ToolParameter(Description = "attack only: auto | melee | ranged. auto melees when the pawn is unarmed or holds a melee weapon and shoots when it holds a ranged one, which is what vanilla's own float menu picks. melee and ranged force it.", DefaultValue = "auto")] string mode = "auto",
            [ToolParameter(Description = "Manage the draft state automatically. attack, goto and tend need a drafted pawn, so true drafts one that is not; haul and work are undrafted work orders unless the WorkGiver allows drafted work, so true undrafts. rescue and equip need no draft change at all. false refuses instead, so the caller can see the draft state was wrong.", DefaultValue = true)] bool draft = true,
            [ToolParameter(Description = "Permit a real ground-tend order to leave an auto-drafted doctor under caller-managed cleanup. False by default: raw calls otherwise have no reliable way to restore the doctor after the job completes. combat.py passes true because its ledger records and restores that obligation. Dry runs do not require this flag.", DefaultValue = false)] bool allowPersistentDraft = false,
            [ToolParameter(Description = "Resolve everything, run every refusal check and report the job that WOULD be issued, without touching the game. Defaults to FALSE - this tool's job is to make orders land.", DefaultValue = false)] bool dryRun = false,
            [ToolParameter(Description = "Refuse a target that is not hostile to the player faction. Defaults to FALSE, because vanilla imposes no such rule and imposing it is what got a colonist killed.", DefaultValue = false)] bool requireHostile = false,
            [ToolParameter(Description = "Show the order on screen: select the target and jump the camera to it, then after a short lead select the pawn so the inspect pane shows the new job. Decorative only and never opens a float menu. A dry run and a refusal show nothing.", DefaultValue = true)] bool watch = true,
            [ToolParameter(Description = "How long the watch selection stays before it is put back, 1..60.", DefaultValue = Watch.DefaultSeconds)] int watchSeconds = Watch.DefaultSeconds)
        {
            return BridgeCommon.WithUnknownArguments(
                await OrderCore(ctx, cancellationToken, action, pawn, target, x, z, mode, draft, allowPersistentDraft, dryRun, requireHostile, watch, watchSeconds)
                    .ConfigureAwait(false),
                ctx, typeof(HomeOrderTools), ToolName);
        }

        private async Task<object> OrderCore(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            string action,
            string pawnArg,
            string targetArg,
            int x,
            int z,
            string mode,
            bool draft,
            bool allowPersistentDraft,
            bool dryRun,
            bool requireHostile,
            bool watch,
            int watchSeconds)
        {
            if (ctx == null || ctx.MainThread == null)
                return Failure("No RimBridge main-thread dispatcher is available for this invocation.", "bad_arguments", action);

            var request = new Request
            {
                Action = Normalise(action),
                PawnArg = pawnArg,
                TargetArg = targetArg,
                X = x,
                Z = z,
                Mode = Normalise(mode),
                Draft = draft,
                AllowPersistentDraft = allowPersistentDraft,
                DryRun = dryRun,
                RequireHostile = requireHostile
            };

            // ---------------------------------------------------------- hop 1
            // Resolve and validate. When there is nothing to show -- a refusal,
            // a dry run, a read-only resolve, or watch:false -- the write also
            // happens here, in ONE hop, so a combat order is never split across
            // a gap it did not need.
            var first = await ctx.MainThread
                .InvokeAsync(() => HopOne(ctx, request, watch), cancellationToken)
                .ConfigureAwait(false);

            if (first.Session == null)
                return first.Reply;

            // ------------------------------------------------- the watch lead
            // Off the main thread, so the selected target is actually on screen
            // for a moment before the order lands.
            await Watch.Lead(first.Session, cancellationToken).ConfigureAwait(false);

            // ---------------------------------------------------------- hop 2
            // The plan is NOT carried across the gap: everything is resolved
            // again, because a tick may have passed and the target may be gone.
            return await ctx.MainThread
                .InvokeAsync(() => HopTwo(request, first.Session, watchSeconds), cancellationToken)
                .ConfigureAwait(false);
        }

        // =================================================================
        // The two hops
        // =================================================================

        private sealed class FirstHop
        {
            internal object Reply;
            internal Watch.Session Session;
        }

        private static FirstHop HopOne(IRimBridgeContext ctx, Request request, bool watch)
        {
            var plan = Prepare(request);

            if (plan.Error != null)
                return new FirstHop { Reply = Reply(plan, Watch.Skipped("refused"), false) };

            if (request.Action == "resolve")
                return new FirstHop { Reply = Reply(plan, Watch.Skipped("read-only call"), false) };

            if (request.DryRun)
                return new FirstHop { Reply = Reply(plan, Watch.Skipped("dry run"), false) };

            if (!watch)
            {
                Apply(plan);
                return new FirstHop { Reply = Reply(plan, Watch.Skipped("watch:false"), true) };
            }

            // Show the TARGET first -- who is being ordered at what -- and jump
            // the camera to it. The pawn is selected in hop 2, after the lead,
            // so the inspect pane ends on the pawn and its new job text.
            var shown = plan.Target != null && BridgeCommon.Try(() => plan.Target.Spawned, false)
                ? (Thing)plan.Target
                : plan.Pawn;
            var session = Watch.Open(ctx, shown, null, null, true);
            return new FirstHop { Session = session };
        }

        private static object HopTwo(Request request, Watch.Session session, int watchSeconds)
        {
            var plan = Prepare(request);

            if (plan.Error != null)
            {
                // The lead let a tick through and the picture changed. Say so
                // rather than issuing an order against a stale resolution.
                return Reply(plan, Watch.Finish(session, watchSeconds), false);
            }

            Apply(plan);

            if (plan.Pawn != null)
            {
                Watch.SelectNow(session, plan.Pawn);
                JumpTo(session, plan.Pawn);
            }

            return Reply(plan, Watch.Finish(session, watchSeconds), true);
        }

        /// <summary>Move the camera onto the ordered pawn in hop 2, so the
        /// inspect pane a viewer reads is next to the pawn it describes. Watch
        /// has no public camera helper; this is the same guarded call it
        /// makes.</summary>
        private static void JumpTo(Watch.Session session, Thing thing)
        {
            if (session == null || thing == null)
                return;
            try
            {
                if (!thing.Spawned)
                    return;
                var driver = Find.CameraDriver;
                if (driver == null)
                    return;
                driver.JumpToCurrentMapLoc(thing.Position);
                session.CameraMoved = true;
                session.AnythingShown = true;
            }
            catch
            {
                // Decorative. A camera that did not move never fails an order.
            }
        }

        // =================================================================
        // The request and the plan
        // =================================================================

        private sealed class Request
        {
            internal string Action;
            internal string PawnArg;
            internal string TargetArg;
            internal int X;
            internal int Z;
            internal string Mode;
            internal bool Draft;
            internal bool AllowPersistentDraft;
            internal bool DryRun;
            internal bool RequireHostile;
        }

        /// <summary>Everything one call resolved, every refusal it found, and
        /// the job it intends to issue. Built by <see cref="Prepare"/>, which
        /// touches nothing; spent by <see cref="Apply"/>, which is the only
        /// method in this file that writes.</summary>
        private sealed class Plan
        {
            internal Request Request;
            internal Map Map;

            internal Pawn Pawn;
            internal Thing Target;
            internal Pawn TargetPawn;
            internal IntVec3 Cell = IntVec3.Invalid;

            internal string TargetMatchedBy;
            internal List<Thing> Candidates = new List<Thing>();

            /// <summary>Keep at most MaxCandidates of a tie, so a one-letter
            /// query cannot return a payload naming most of the map.</summary>
            internal void SetCandidates(List<Thing> found)
            {
                Candidates = found == null
                    ? new List<Thing>()
                    : (found.Count > MaxCandidates ? found.GetRange(0, MaxCandidates) : found);
            }

            internal string Error;
            internal string ErrorKind;

            // What Apply will do.
            internal bool NeedsDraft;
            internal bool NeedsUndraft;
            internal bool AutoDrafted;
            internal bool AutoUndrafted;

            internal JobDef JobDef;
            internal LocalTargetInfo TargetA = LocalTargetInfo.Invalid;
            internal LocalTargetInfo TargetB = LocalTargetInfo.Invalid;
            internal bool KillIncappedTarget;
            internal int ExpiryInterval = -1;
            internal Verb Verb;
            internal string ResolvedMode;
            internal int Count = -1;
            internal bool DraftedTend;
            internal bool UnforbidTarget;
            internal bool Unforbade;

            /// <summary>A job a WorkGiver built for us, for haul and work. When
            /// this is set, Apply issues THIS object rather than building one:
            /// a work job carries queues, counts and a bill that only the
            /// giver knows how to fill in.</summary>
            internal Job PreparedJob;
            internal WorkGiver_Scanner Scanner;
            internal WorkGiverDef GiverDef;
            internal WorkTypeDef WorkType;
            internal string BillLabel;
            internal string TendPath;
            internal int? HaulGlobalCandidateCount;
            internal bool? HaulTargetInGlobalList;
            internal Dictionary<string, object> HaulChecks;
            internal string RescuePath;

            internal bool Issued;
            internal bool Verified;
            internal string VerifiedReason;
            internal bool Applied;

            internal void Refuse(string kind, string message)
            {
                if (Error != null)
                    return;
                ErrorKind = kind;
                Error = message;
            }
        }

        // =================================================================
        // Prepare -- resolve and validate, writing nothing
        // =================================================================

        private static Plan Prepare(Request request)
        {
            var plan = new Plan { Request = request };

            if (Array.IndexOf(Actions, request.Action) < 0)
            {
                plan.Refuse("bad_arguments",
                    "'" + (request.Action ?? "null") + "' is not an action. Use one of: " + string.Join(", ", Actions) + ".");
                return plan;
            }
            if (Array.IndexOf(Modes, request.Mode) < 0)
            {
                plan.Refuse("bad_arguments",
                    "'" + (request.Mode ?? "null") + "' is not a mode. Use one of: " + string.Join(", ", Modes) + ".");
                return plan;
            }

            Map map;
            string mapError;
            if (!BridgeCommon.TryGetMap(ToolName, out map, out mapError))
            {
                plan.Refuse("bad_arguments", mapError);
                return plan;
            }
            plan.Map = map;

            // ------------------------------------------------------ the pawn
            var pawnWanted = !string.IsNullOrEmpty(request.PawnArg);
            if (!pawnWanted && request.Action != "resolve")
            {
                plan.Refuse("bad_arguments", "action '" + request.Action + "' needs a pawn. Only resolve may be called without one.");
                return plan;
            }

            if (pawnWanted)
            {
                List<Thing> pawnCandidates;
                string matchedBy;
                var found = Resolve(map, request.PawnArg, true, out pawnCandidates, out matchedBy);
                if (found == null)
                {
                    plan.SetCandidates(pawnCandidates);
                    if (pawnCandidates.Count > 1)
                        plan.Refuse("ambiguous",
                            "'" + request.PawnArg + "' matched " + pawnCandidates.Count
                            + " pawns" + Listing(pawnCandidates.Count)
                            + ". Pass one of the idForms in candidates[] -- an explicit id is never ambiguous.");
                    else
                        plan.Refuse("pawn_not_found",
                            "No pawn on this map answers to '" + request.PawnArg
                            + "'. Accepted forms: Thing_Human123, Human123, 123, or a name.");
                    return plan;
                }
                plan.Pawn = found as Pawn;
                if (plan.Pawn == null)
                {
                    plan.Refuse("pawn_not_found",
                        "'" + request.PawnArg + "' resolved to " + Describe(found) + ", which is a thing, not a pawn.");
                    return plan;
                }
            }

            // ---------------------------------------------------- the target
            var targetWanted = !string.IsNullOrEmpty(request.TargetArg);
            if (targetWanted)
            {
                List<Thing> targetCandidates;
                string matchedBy;
                var found = Resolve(map, request.TargetArg, false, out targetCandidates, out matchedBy);
                if (found == null)
                {
                    plan.SetCandidates(targetCandidates);
                    if (targetCandidates.Count > 1)
                        plan.Refuse("ambiguous",
                            "'" + request.TargetArg + "' matched " + targetCandidates.Count
                            + " things" + Listing(targetCandidates.Count)
                            + ". Pass one of the idForms in candidates[] -- an explicit id is never ambiguous.");
                    else
                        plan.Refuse("target_not_found",
                            "Nothing on this map answers to '" + request.TargetArg
                            + "'. Accepted forms: Thing_Wolf_Timber334862, Wolf_Timber334862, 334862, or a label.");
                    return plan;
                }
                plan.Target = found;
                plan.TargetPawn = found as Pawn;
                plan.TargetMatchedBy = matchedBy;
            }

            if (request.X != int.MinValue && request.Z != int.MinValue)
                plan.Cell = new IntVec3(request.X, 0, request.Z);

            if (request.Action == "resolve")
                return plan;

            // ------------------------------------------- pawn-wide refusals
            if (BridgeCommon.Try(() => plan.Pawn.Dead, false))
            {
                plan.Refuse("pawn_dead", NameOf(plan.Pawn) + " is dead and cannot be given an order.");
                return plan;
            }

            var mentalState = MentalStateOf(plan.Pawn);
            if (mentalState != null && request.Action != "undraft")
            {
                plan.Refuse("mental_state",
                    NameOf(plan.Pawn) + " is in a mental state (" + mentalState
                    + "), so the game will not accept a player order. Pawn.IsColonistPlayerControlled reads MentalStateDef "
                    + "== null, so it is false; Pawn.CanTakeOrder is false with it; and FloatMenuContext's constructor does "
                    + "selectedPawns.RemoveAll(p => !p.CanTakeOrder), which is exactly why the float menu came back with zero "
                    + "options rather than an error. FloatMenuUtility.GetMeleeAttackAction and GetRangedAttackAction gate on "
                    + "the same property. Only 'undraft' is allowed through here, because dropping the draft is unguarded and "
                    + "is sometimes the thing you want.");
                return plan;
            }

            if (BridgeCommon.Try(() => plan.Pawn.Downed, false)
                && request.Action != "undraft")
            {
                plan.Refuse("pawn_downed", NameOf(plan.Pawn) + " is downed and cannot act.");
                return plan;
            }

            switch (request.Action)
            {
                case "draft": PrepareDraft(plan, true); break;
                case "undraft": PrepareDraft(plan, false); break;
                case "attack": PrepareAttack(plan); break;
                case "goto": PrepareGoto(plan); break;
                case "equip": PrepareEquip(plan); break;
                case "rescue": PrepareRescue(plan); break;
                case "tend": PrepareTend(plan); break;
                case "haul": PrepareHaul(plan); break;
                case "work": PrepareWork(plan); break;
            }

            return plan;
        }

        // ------------------------------------------------------ draft/undraft

        private static void PrepareDraft(Plan plan, bool wanted)
        {
            var drafted = BridgeCommon.Try(() => plan.Pawn.Drafted, false);

            if (!wanted)
            {
                // Undrafting is unguarded in Pawn_DraftController's setter and is
                // never the dangerous direction, so it is allowed on a downed or
                // mentally broken pawn where drafting is not.
                if (BridgeCommon.Try(() => plan.Pawn.drafter, (Pawn_DraftController)null) == null)
                    plan.Refuse("draft_refused",
                        NameOf(plan.Pawn) + " has no Pawn_DraftController, so it can be neither drafted nor undrafted.");
                else if (drafted)
                    plan.NeedsUndraft = true;
                return;
            }

            string why;
            if (!CanBeDrafted(plan.Pawn, out why))
            {
                plan.Refuse("draft_refused", NameOf(plan.Pawn) + " cannot be drafted: " + why);
                return;
            }
            if (!drafted)
                plan.NeedsDraft = true;
        }

        /// <summary>The draft state an action needs, applied or refused
        /// according to the caller's `draft` flag.</summary>
        private static bool EnsureDraft(Plan plan, bool wantDrafted)
        {
            var drafted = BridgeCommon.Try(() => plan.Pawn.Drafted, false);
            if (drafted == wantDrafted)
                return true;

            if (!plan.Request.Draft)
            {
                plan.Refuse("draft_refused",
                    NameOf(plan.Pawn) + " is " + (drafted ? "drafted" : "not drafted")
                    + " and action '" + plan.Request.Action + "' needs the opposite, but draft:false was passed. "
                    + "Pass draft:true to have this tool " + (wantDrafted ? "draft" : "undraft") + " the pawn, or change it "
                    + "with action '" + (wantDrafted ? "draft" : "undraft") + "' first.");
                return false;
            }

            if (wantDrafted)
            {
                string why;
                if (!CanBeDrafted(plan.Pawn, out why))
                {
                    plan.Refuse("draft_refused",
                        NameOf(plan.Pawn) + " needs to be drafted for '" + plan.Request.Action + "' and cannot be: " + why);
                    return false;
                }
                plan.NeedsDraft = true;
            }
            else
            {
                plan.NeedsUndraft = true;
            }
            return true;
        }

        // ------------------------------------------------------------ attack

        private static void PrepareAttack(Plan plan)
        {
            if (plan.Target == null)
            {
                plan.Refuse("bad_arguments", "action 'attack' needs a target.");
                return;
            }
            if (ReferenceEquals(plan.Target, plan.Pawn))
            {
                plan.Refuse("bad_arguments", NameOf(plan.Pawn) + " cannot attack itself.");
                return;
            }
            if (!BridgeCommon.Try(() => plan.Target.Spawned, false))
            {
                plan.Refuse("target_not_found",
                    Describe(plan.Target) + " is not spawned on the map, so there is nothing to attack.");
                return;
            }
            if (plan.TargetPawn != null && BridgeCommon.Try(() => plan.TargetPawn.Dead, false))
            {
                plan.Refuse("target_dead", Describe(plan.Target) + " is already dead.");
                return;
            }
            if (BridgeCommon.Try(() => plan.Target.Destroyed, false))
            {
                plan.Refuse("target_dead", Describe(plan.Target) + " has been destroyed.");
                return;
            }

            // WorkTags.Violent is the ONE thing that stops an attack, and it
            // does not stop a draft: see CanBeDrafted.
            if (IncapableOfViolence(plan.Pawn))
            {
                plan.Refuse("incapable_of_violence",
                    NameOf(plan.Pawn) + " has WorkTags.Violent disabled and cannot be ordered to attack anything. "
                    + "Drafting, moving and every non-violent order still work.");
                return;
            }

            if (plan.Request.RequireHostile && !HostileToPlayer(plan.Target))
            {
                plan.Refuse("job_refused",
                    "requireHostile:true was passed and " + Describe(plan.Target)
                    + " is not hostile to the player faction. Vanilla imposes no such rule; drop requireHostile to attack anyway.");
                return;
            }

            if (!EnsureDraft(plan, true))
                return;

            var melee = ChooseMelee(plan);
            plan.ResolvedMode = melee ? "melee" : "ranged";

            if (melee)
                BuildMeleeJob(plan);
            else
                BuildRangedJob(plan);
        }

        /// <summary>
        /// Which of vanilla's two drafted attack options this is. `auto` asks
        /// the game's own predicate rather than reproducing it:
        /// `FloatMenuUtility.UseRangedAttack(pawn)` is
        /// `equipment.Primary != null &amp;&amp; !equipment.PrimaryEq.PrimaryVerb.verbProps.IsMeleeAttack`,
        /// and it is what `FloatMenuUtility.GetAttackAction` picks with. Note
        /// that it asks the equipped VERB, not `ThingDef.IsRangedWeapon` -- a
        /// distinction that matters for a weapon with both kinds of verb.
        /// </summary>
        private static bool ChooseMelee(Plan plan)
        {
            if (plan.Request.Mode == "melee")
                return true;
            if (plan.Request.Mode == "ranged")
                return false;
            return !BridgeCommon.Try(() => FloatMenuUtility.UseRangedAttack(plan.Pawn), false);
        }

        // ------------------------------------------------------------- goto

        private static void PrepareGoto(Plan plan)
        {
            if (!plan.Cell.IsValid)
            {
                plan.Refuse("bad_arguments", "action 'goto' needs both x and z.");
                return;
            }
            if (!BridgeCommon.Try(() => plan.Cell.InBounds(plan.Map), false))
            {
                plan.Refuse("bad_arguments",
                    "(" + plan.Cell.x + "," + plan.Cell.z + ") is outside this map.");
                return;
            }
            // Vanilla's own snap, so a cell inside a wall still orders a move to
            // the nearest sensible spot instead of being refused.
            var asked = plan.Cell;
            var snapped = BridgeCommon.Try(
                () => CellFinder.StandableCellNear(asked, plan.Map, 2.9f), IntVec3.Invalid);
            if (!snapped.IsValid)
                snapped = asked;
            var best = BridgeCommon.Try(
                () => RCellFinder.BestOrderedGotoDestNear(snapped, plan.Pawn), IntVec3.Invalid);
            plan.Cell = best.IsValid ? best : snapped;

            if (!BridgeCommon.Try(() => plan.Pawn.CanReach(plan.Cell, PathEndMode.OnCell, Danger.Deadly), false))
            {
                plan.Refuse("not_reachable",
                    NameOf(plan.Pawn) + " cannot reach (" + plan.Cell.x + "," + plan.Cell.z
                    + ")" + (plan.Cell == asked ? "" : ", the nearest standable cell to the one asked for")
                    + ", even accepting deadly danger.");
                return;
            }
            if (!EnsureDraft(plan, true))
                return;

            BuildGotoJob(plan);
        }

        // ------------------------------------------------------------ equip

        private static void PrepareEquip(Plan plan)
        {
            var weapon = plan.Target;
            if (weapon == null && plan.Cell.IsValid)
            {
                weapon = WeaponOnCell(plan.Map, plan.Cell);
                if (weapon == null)
                {
                    plan.Refuse("target_not_found",
                        "No equippable weapon is lying on (" + plan.Cell.x + "," + plan.Cell.z + ").");
                    return;
                }
                plan.Target = weapon;
                plan.TargetMatchedBy = "cell";
            }
            if (weapon == null)
            {
                plan.Refuse("bad_arguments", "action 'equip' needs a target, or an x and z naming the cell the weapon is on.");
                return;
            }
            if (!BridgeCommon.Try(() => weapon.Spawned, false))
            {
                plan.Refuse("target_not_found",
                    Describe(weapon) + " is not lying on the map (it is carried, stored or destroyed), so it cannot be picked up.");
                return;
            }

            // FloatMenuOptionProvider_Equip's own gates, in its own order.
            if (BridgeCommon.Try(() => plan.Pawn.equipment, (Pawn_EquipmentTracker)null) == null)
            {
                plan.Refuse("job_refused", NameOf(plan.Pawn) + " has no equipment tracker and can carry no weapon.");
                return;
            }
            if (!BridgeCommon.Try(() => weapon.def != null && weapon.def.IsWeapon, false)
                || !BridgeCommon.Try(() => (weapon as ThingWithComps) != null
                                           && ((ThingWithComps)weapon).GetComp<CompEquippable>() != null, false))
            {
                plan.Refuse("bad_arguments",
                    Describe(weapon) + " is not an equippable weapon (no CompEquippable).");
                return;
            }
            if (IncapableOfViolence(plan.Pawn))
            {
                plan.Refuse("incapable_of_violence",
                    NameOf(plan.Pawn) + " has WorkTags.Violent disabled and cannot equip a weapon.");
                return;
            }
            if (BridgeCommon.Try(() => weapon.def.IsRangedWeapon, false)
                && BridgeCommon.Try(() => plan.Pawn.WorkTagIsDisabled(WorkTags.Shooting), false))
            {
                plan.Refuse("job_refused", NameOf(plan.Pawn) + " has WorkTags.Shooting disabled and cannot equip a ranged weapon.");
                return;
            }
            if (!BridgeCommon.Try(() => plan.Pawn.CanReach(weapon, PathEndMode.ClosestTouch, Danger.Deadly), false))
            {
                plan.Refuse("not_reachable", NameOf(plan.Pawn) + " cannot reach " + Describe(weapon) + ".");
                return;
            }
            if (!BridgeCommon.Try(
                    () => plan.Pawn.health.capacities.CapableOf(PawnCapacityDefOf.Manipulation), false))
            {
                plan.Refuse("job_refused", NameOf(plan.Pawn) + " is incapable of manipulation and cannot pick anything up.");
                return;
            }
            if (BridgeCommon.Try(() => weapon.IsBurning(), false))
            {
                plan.Refuse("job_refused", Describe(weapon) + " is on fire.");
                return;
            }
            string cantReason = null;
            if (!BridgeCommon.Try(() => EquipmentUtility.CanEquip(weapon, plan.Pawn, out cantReason, false), false))
            {
                plan.Refuse("job_refused",
                    NameOf(plan.Pawn) + " cannot equip " + Describe(weapon) + ": "
                    + (string.IsNullOrEmpty(cantReason) ? "EquipmentUtility.CanEquip said no with no reason text." : cantReason));
                return;
            }

            BuildEquipJob(plan);
        }

        // ----------------------------------------------------------- rescue

        private static void PrepareRescue(Plan plan)
        {
            if (plan.TargetPawn == null)
            {
                plan.Refuse(plan.Target == null ? "bad_arguments" : "target_not_found",
                    plan.Target == null
                        ? "action 'rescue' needs a target."
                        : Describe(plan.Target) + " is not a pawn, so it cannot be rescued.");
                return;
            }
            if (BridgeCommon.Try(() => plan.TargetPawn.Dead, false))
            {
                plan.Refuse("target_dead", Describe(plan.Target) + " is dead; a corpse is hauled, not rescued.");
                return;
            }
            if (!BridgeCommon.Try(() => plan.TargetPawn.Spawned, false))
            {
                plan.Refuse("target_not_found", Describe(plan.Target) + " is not spawned on the map.");
                return;
            }
            // Rescue needs NO draft change either way:
            // FloatMenuOptionProvider_RescuePawn has Drafted => true AND
            // Undrafted => true, so the option is offered in both states.
            if (ReferenceEquals(plan.TargetPawn, plan.Pawn))
            {
                plan.Refuse("bad_arguments", NameOf(plan.Pawn) + " cannot rescue itself.");
                return;
            }
            // ---- 1. The ordinary path: "Prioritize rescuing X", UNDRAFTED,
            // through WorkGiver_RescueDowned on the same prioritize route.
            // Preferred because it is what a player's undrafted right-click
            // does and it needs no draft change.
            string rescueFail;
            if (TryWorkGiverJob(plan,
                    def => BridgeCommon.Try(
                        () => def.giverClass != null && typeof(WorkGiver_RescueDowned).IsAssignableFrom(def.giverClass), false),
                    out rescueFail))
            {
                plan.RescuePath = "work";
                FinishWorkGiverPlan(plan);
                return;
            }

            // ---- 2. FloatMenuOptionProvider_RescuePawn, which works drafted
            // or undrafted and finds the bed itself.
            plan.RescuePath = "drafted";
            if (!BridgeCommon.Try(() => HealthAIUtility.CanRescueNow(plan.Pawn, plan.TargetPawn, true), false))
            {
                plan.Refuse("job_refused",
                    NameOf(plan.Pawn) + " cannot rescue " + Describe(plan.Target)
                    + " right now: HealthAIUtility.CanRescueNow is false. The usual causes are that the patient is not "
                    + "downed, is already being carried, or cannot be reached.");
                return;
            }
            if (HostileToPlayer(plan.Target))
            {
                plan.Refuse("job_refused",
                    Describe(plan.Target) + " belongs to a faction hostile to the colony; vanilla offers no rescue for one, "
                    + "only a capture. Rescue is refused rather than issued as something else.");
                return;
            }
            if (!BridgeCommon.Try(() => plan.Pawn.CanReach(plan.TargetPawn, PathEndMode.Touch, Danger.Deadly), false))
            {
                plan.Refuse("not_reachable", NameOf(plan.Pawn) + " cannot reach " + Describe(plan.Target) + ".");
                return;
            }
            BuildRescueJob(plan);
        }

        // ------------------------------------------------------------- tend

        private static void PrepareTend(Plan plan)
        {
            if (plan.TargetPawn == null)
            {
                plan.Refuse(plan.Target == null ? "bad_arguments" : "target_not_found",
                    plan.Target == null
                        ? "action 'tend' needs a target."
                        : Describe(plan.Target) + " is not a pawn, so it cannot be tended.");
                return;
            }
            if (BridgeCommon.Try(() => plan.TargetPawn.Dead, false))
            {
                plan.Refuse("target_dead", Describe(plan.Target) + " is dead and cannot be tended.");
                return;
            }
            if (!BridgeCommon.Try(() => plan.TargetPawn.Spawned, false))
            {
                plan.Refuse("target_not_found", Describe(plan.Target) + " is not spawned on the map.");
                return;
            }
            if (BridgeCommon.Try(() => plan.Pawn.WorkTypeIsDisabled(WorkTypeDefOf.Doctor), false))
            {
                plan.Refuse("work_disabled",
                    NameOf(plan.Pawn) + " has the Doctor work type disabled and cannot tend anyone. "
                    + "This is a capability, not a work priority: turning Doctor on in the Work tab will not help.");
                return;
            }

            // ---- 1. The ordinary path: "Prioritize tending X", UNDRAFTED.
            // This is what tending IS in vanilla -- WorkGiver_Tend through the
            // same prioritize route `work` and `haul` use. It covers a patient
            // in a bed, which is the overwhelmingly common case, and it needs
            // no draft. Only when it yields nothing AND the patient is lying on
            // the ground does the drafted provider get a turn.
            string tendFail;
            if (TryWorkGiverJob(plan,
                    def => BridgeCommon.Try(
                        () => def.giverClass != null && typeof(WorkGiver_Tend).IsAssignableFrom(def.giverClass), false),
                    out tendFail))
            {
                plan.TendPath = "work";
                FinishWorkGiverPlan(plan);
                return;
            }

            var inBed = BridgeCommon.Try(() => plan.TargetPawn.InBed(), false);
            if (inBed)
            {
                // In a bed with no WorkGiver_Tend job, the drafted provider
                // would add nothing a player could not already do, so the
                // game's own reason is the answer rather than a second attempt.
                plan.Refuse("job_refused",
                    Describe(plan.Target) + " is in a bed and no WorkGiver_Tend job could be made for "
                    + NameOf(plan.Pawn) + "."
                    + (string.IsNullOrEmpty(tendFail)
                        ? " The game gave no reason; the usual causes are nothing needing tending, the patient already "
                          + "being tended, or the bed being reserved."
                        : " The game's reason: " + tendFail));
                return;
            }

            // ---- 2. The ground: FloatMenuOptionProvider_DraftedTend, which
            // never looks at a bed but IS gated on the doctor being drafted --
            // `IsValidTendTarget` opens with
            // `if (!doctor.Drafted && patient != doctor) return false;`.
            plan.TendPath = "drafted";
            if (!plan.Request.DryRun && !plan.Request.AllowPersistentDraft)
            {
                plan.Refuse("draft_cleanup_required",
                    "Ground tending requires auto-drafting " + NameOf(plan.Pawn)
                    + ", but this raw order has no lifecycle owner to restore the doctor after tending completes or is cancelled. "
                    + "Use combat.py tend (which records the cleanup obligation), or pass allowPersistentDraft:true only if the caller will reliably undraft them.");
                return;
            }
            if (!EnsureDraft(plan, true))
                return;

            if (!BridgeCommon.Try(() => plan.TargetPawn.health.HasHediffsNeedingTend(), false))
            {
                plan.Refuse("job_refused",
                    Describe(plan.Target) + " has nothing that needs tending right now "
                    + "(Pawn_HealthTracker.HasHediffsNeedingTend is false).");
                return;
            }
            if (ReferenceEquals(plan.TargetPawn, plan.Pawn)
                && !BridgeCommon.Try(() => plan.Pawn.playerSettings != null && plan.Pawn.playerSettings.selfTend, false))
            {
                plan.Refuse("job_refused",
                    NameOf(plan.Pawn) + " may only tend itself when self-tend is on, which it is not. "
                    + "Turn it on with home/pawn_config selfTend, or send a different doctor.");
                return;
            }
            if (BridgeCommon.Try(() => plan.TargetPawn.InAggroMentalState, false))
            {
                plan.Refuse("mental_state",
                    Describe(plan.Target) + " is in an aggressive mental state and cannot be tended until it ends.");
                return;
            }
            if (!BridgeCommon.Try(
                    () => plan.Pawn.CanReach(plan.TargetPawn, PathEndMode.ClosestTouch, Danger.Deadly), false))
            {
                plan.Refuse("not_reachable", NameOf(plan.Pawn) + " cannot reach " + Describe(plan.Target) + ".");
                return;
            }
            BuildTendJob(plan);
        }

        // ============================================== haul / work (WorkGivers)

        /// <summary>
        /// `haul` -- what "Prioritize hauling X" does.
        ///
        /// The float menu has no special haul option: it walks every
        /// `WorkGiverDef` and asks each `WorkGiver_Scanner` for a job on the
        /// clicked thing with `forced: true`. `WorkGiver_Haul.JobOnThing` is
        /// `PawnCanAutomaticallyHaulFast` then
        /// `HaulAIUtility.HaulToStorageJob`, which produces a `HaulToCell` or
        /// a `HaulToContainer` job with the count and haul mode already filled
        /// in -- which is why this action issues the giver's own job object
        /// rather than building one.
        /// </summary>
        private static void PrepareHaul(Plan plan)
        {
            if (plan.Target == null)
            {
                plan.Refuse("bad_arguments", "action 'haul' needs a target: the item to haul.");
                return;
            }
            if (!BridgeCommon.Try(() => plan.Target.Spawned, false))
            {
                plan.Refuse("target_not_found",
                    Describe(plan.Target) + " is not lying on the map, so there is nothing to haul.");
                return;
            }

            var globalHaulables = BridgeCommon.Try<List<Thing>>(
                () => plan.Map.listerHaulables.ThingsPotentiallyNeedingHauling().ToList(), null);
            plan.HaulGlobalCandidateCount = globalHaulables == null ? (int?)null : globalHaulables.Count;
            plan.HaulTargetInGlobalList = globalHaulables == null
                ? (bool?)null : globalHaulables.Contains(plan.Target);
            plan.HaulChecks = HaulCheckBlock(plan);
            var alwaysHaulable = (bool)plan.HaulChecks["alwaysHaulable"];
            var designated = (bool)plan.HaulChecks["haulDesignation"];
            var inStorage = (bool)plan.HaulChecks["inValidStorage"];
            if (!alwaysHaulable && !designated && !inStorage)
            {
                plan.Refuse("missing_haul_designation",
                    Describe(plan.Target) + " is haulable but is not automatically haulable, has no Haul designation, "
                    + "and is not already in valid storage. RimWorld excludes chunks in this state before making a haul job. "
                    + "Apply RimWorld's 'Haul things' designator to this chunk (for example: "
                    + "python act.py apply \"Haul things\" " + plan.Target.Position.x + " " + plan.Target.Position.z
                    + "), then retry the order.");
                return;
            }

            var hauling = BridgeCommon.Try<WorkTypeDef>(() => WorkTypeDefOf.Hauling, null);
            if (hauling != null && BridgeCommon.Try(() => plan.Pawn.WorkTypeIsDisabled(hauling), false))
            {
                plan.Refuse("work_disabled",
                    NameOf(plan.Pawn) + " is incapable of Hauling, so no haul order can be given. This is a capability, "
                    + "not a work priority.");
                return;
            }

            // NOT Thing.IsForbidden(pawn): it reaches Faction.OfPlayer. The
            // comp is the same answer with no getter that can pause the game.
            var comps = plan.Target as ThingWithComps;
            var forbiddable = comps == null ? null : BridgeCommon.Try<CompForbiddable>(() => comps.GetComp<CompForbiddable>(), null);
            if (forbiddable != null && BridgeCommon.Try(() => forbiddable.Forbidden, false))
            {
                plan.Refuse("job_refused",
                    Describe(plan.Target) + " is forbidden, so no colonist may haul it. Unforbid it first "
                    + "(home/building_config forbidden, or the item's own toggle).");
                return;
            }

            string failReason;
            if (!TryWorkGiverJob(plan, def => ReferenceEquals(BridgeCommon.Try<WorkTypeDef>(() => def.workType, null), hauling),
                                 out failReason))
            {
                if (!HasSomewhereToPut(plan))
                {
                    IntVec3 elsewhere;
                    var ignoringCarrier = StorageIgnoringCarrier(plan, out elsewhere);
                    if (ignoringCarrier == true)
                    {
                        // Storage exists and accepts it; what failed is this
                        // pawn's access to it. IsGoodStoreCell's carrier gates
                        // are reach, forbidden and reservation, in that order
                        // of likelihood.
                        var reachesCell = BridgeCommon.Try(
                            () => plan.Map.reachability.CanReach(
                                plan.Target.Position, elsewhere, PathEndMode.ClosestTouch,
                                TraverseParms.For(plan.Pawn)), true);
                        plan.Refuse("unreachable_storage",
                            "Storage that accepts " + Describe(plan.Target) + " DOES exist -- the same "
                            + "StoreUtility.TryFindBestBetterStorageFor search finds a cell at "
                            + elsewhere.x + "," + elsewhere.z + " when the carrier is ignored. It is "
                            + NameOf(plan.Pawn) + "'s access that fails: StoreUtility.IsGoodStoreCell drops "
                            + "every cell the carrier cannot reach, may not touch (forbidden) or cannot reserve"
                            + (reachesCell
                                ? ". The cell is reachable, so a forbidden cell or a standing reservation is the cause."
                                : ", and this one is NOT reachable from the item. Open a route, or give the job to a "
                                  + "colonist on that side.")
                            + " This is NOT a storage problem: adding another stockpile will not help.");
                        return;
                    }
                    plan.Refuse("no_storage",
                        "There is nowhere better to put " + Describe(plan.Target)
                        + ": StoreUtility.TryFindBestBetterStorageFor found no cell and no container that would accept it"
                        + (ignoringCarrier == false
                            ? ", with or without the carrier's reach and forbidden filters"
                            : " (the carrier-free second search threw, so reach was not ruled out)")
                        + ". Add a stockpile, widen an existing one's filter, or free some space.");
                    return;
                }
                plan.Refuse("job_refused",
                    "No hauling WorkGiver would produce a job for " + Describe(plan.Target) + "."
                    + (string.IsNullOrEmpty(failReason) ? " The game gave no reason." : " The game's reason: " + failReason));
                return;
            }

            FinishWorkGiverPlan(plan);
        }

        /// <summary>
        /// `work` -- what "Prioritize doing bills at X" does, through the same
        /// generic WorkGiver path, narrowed to the `WorkGiver_DoBill` givers.
        ///
        /// `WorkGiver_DoBill.JobOnThing` can legitimately hand back something
        /// that is NOT a `DoBill` job: an unfuelled bench yields a `Refuel`
        /// job, and a bench with product in the way yields a haul-off job.
        /// Both are what a player's click produces, so both are issued and
        /// reported honestly under their own `job.def` rather than being
        /// refused as "not a bill".
        /// </summary>
        private static void PrepareWork(Plan plan)
        {
            if (plan.Target == null)
            {
                plan.Refuse("bad_arguments",
                    "action 'work' needs a target: the bench to work at. The DefName@x,z form home/bills prints works here.");
                return;
            }
            if (!BridgeCommon.Try(() => plan.Target.Spawned, false))
            {
                plan.Refuse("target_not_found", Describe(plan.Target) + " is not on the map.");
                return;
            }
            if (!(plan.Target is IBillGiver))
            {
                plan.Refuse("bad_arguments",
                    Describe(plan.Target) + " is not a bill giver (it does not implement IBillGiver), so no bills can be "
                    + "done at it. home/list_buildings names the benches that can.");
                return;
            }

            string failReason;
            if (!TryWorkGiverJob(plan,
                    def => BridgeCommon.Try(
                        () => def.giverClass != null && typeof(WorkGiver_DoBill).IsAssignableFrom(def.giverClass), false),
                    out failReason))
            {
                plan.Refuse("no_bill",
                    "No bill at " + Describe(plan.Target) + " can be worked on by " + NameOf(plan.Pawn) + " right now."
                    + (string.IsNullOrEmpty(failReason)
                        ? " The game gave no reason; the usual causes are an empty bill list, every bill suspended or "
                          + "finished, or the bench being unreachable or reserved."
                        : " The game's reason: " + failReason));
                return;
            }

            FinishWorkGiverPlan(plan);
        }

        /// <summary>
        /// The checks that follow a successful `JobOnThing`, shared by haul and
        /// work: the three "this pawn cannot do that work" tests the float menu
        /// runs to grey its option out, then the draft state the giver needs.
        ///
        /// **`pawn.workSettings.GetPriority` is deliberately NOT among them.**
        /// The float menu also greys the option out when the work type sits at
        /// priority 0, but `GetPriority` opens with `ConfirmInitializedDebug()`,
        /// which `Log.Error`s AND writes a fresh priority table onto the pawn.
        /// So a work type merely switched off in the Work tab is NOT refused
        /// here -- and it should not be: a player-forced order is exactly the
        /// thing that overrides priorities. Only a real incapability refuses.
        /// </summary>
        private static void FinishWorkGiverPlan(Plan plan)
        {
            var scanner = plan.Scanner;
            var giver = plan.GiverDef;

            var missing = BridgeCommon.Try<PawnCapacityDef>(() => scanner.MissingRequiredCapacity(plan.Pawn), null);
            if (missing != null)
            {
                plan.Refuse("work_disabled",
                    NameOf(plan.Pawn) + " is missing a capacity this work needs ("
                    + (BridgeCommon.SafeString(() => missing.LabelCap.ToString()) ?? DefNameOf(missing)) + ").");
                return;
            }
            if (BridgeCommon.Try(() => plan.Pawn.WorkTagIsDisabled(giver.workTags), false))
            {
                plan.Refuse("work_disabled",
                    NameOf(plan.Pawn) + " has the work tags this job needs disabled (" + giver.workTags + ").");
                return;
            }
            if (plan.WorkType != null && BridgeCommon.Try(() => plan.Pawn.WorkTypeIsDisabled(plan.WorkType), false))
            {
                plan.Refuse("work_disabled",
                    NameOf(plan.Pawn) + " is incapable of " + DefNameOf(plan.WorkType)
                    + ". This is a capability, not a work priority: turning it on in the Work tab will not help.");
                return;
            }

            // Vanilla's own per-giver rule: a drafted pawn is offered the
            // option only when the giver says it may be done while drafted.
            if (BridgeCommon.Try(() => plan.Pawn.Drafted, false)
                && !BridgeCommon.Try(() => giver.canBeDoneWhileDrafted, false))
            {
                if (!EnsureDraft(plan, false))
                    return;
            }

            var job = plan.PreparedJob;
            plan.JobDef = BridgeCommon.Try<JobDef>(() => job.def, null);
            plan.TargetA = BridgeCommon.Try(() => job.targetA, LocalTargetInfo.Invalid);
            plan.TargetB = BridgeCommon.Try(() => job.targetB, LocalTargetInfo.Invalid);
            plan.Count = BridgeCommon.Try(() => job.count, -1);
            plan.BillLabel = BridgeCommon.SafeString(() => job.bill == null ? null : job.bill.LabelCap);
        }

        /// <summary>
        /// Walk every WorkGiver the way `FloatMenuOptionProvider_WorkGivers`
        /// does and take the first job one produces for the target.
        ///
        /// Two things here are load-bearing:
        ///
        ///   * **`Pawn_WorkSettings.WorkGiversInOrderNormal` is never used.**
        ///     Its `CacheWorkGiversInOrder()` calls `GetPriority`, the
        ///     `Log.Error`-and-write path. `DefDatabase&lt;WorkTypeDef&gt;` walked in
        ///     `workGiversByPriority` order is what the float menu itself
        ///     walks, and `WorkGiverDef.Worker` is a lazily cached
        ///     `Activator.CreateInstance` with no logging and no game write.
        ///   * **`FloatMenuMakerMap.makingFor` is set for the duration.**
        ///     `WorkGiver_DoBill.StartOrResumeBillJob` gates every one of its
        ///     `JobFailReason.Is(...)` calls on `makingFor == pawn`, so without
        ///     it a bench that cannot run a bill comes back as a bare null with
        ///     no reason at all. It is restored in a `finally`, to whatever it
        ///     held before -- normally null.
        /// </summary>
        private static bool TryWorkGiverJob(Plan plan, Func<WorkGiverDef, bool> accept, out string failReason)
        {
            failReason = null;
            var pawn = plan.Pawn;
            var thing = plan.Target;

            List<WorkTypeDef> types;
            try { types = DefDatabase<WorkTypeDef>.AllDefsListForReading.ToList(); }
            catch { types = new List<WorkTypeDef>(); }

            Pawn previous = null;
            var swapped = false;
            try
            {
                try
                {
                    previous = FloatMenuMakerMap.makingFor;
                    FloatMenuMakerMap.makingFor = pawn;
                    swapped = true;
                }
                catch { swapped = false; }

                try { JobFailReason.Clear(); } catch { }

                foreach (var type in types)
                {
                    if (type == null)
                        continue;
                    List<WorkGiverDef> givers;
                    try { givers = type.workGiversByPriority; }
                    catch { givers = null; }
                    if (givers == null)
                        continue;

                    for (var i = 0; i < givers.Count; i++)
                    {
                        var giver = givers[i];
                        if (giver == null || !accept(giver))
                            continue;
                        if (!BridgeCommon.Try(() => giver.directOrderable, false))
                            continue;

                        var scanner = BridgeCommon.Try<WorkGiver_Scanner>(() => giver.Worker as WorkGiver_Scanner, null);
                        if (scanner == null)
                            continue;
                        if (ScannerShouldSkip(pawn, scanner, thing))
                            continue;

                        var job = BridgeCommon.Try<Job>(
                            () => scanner.HasJobOnThing(pawn, thing, true) ? scanner.JobOnThing(pawn, thing, true) : null,
                            null);
                        if (job == null)
                            continue;

                        try { job.workGiverDef = scanner.def; } catch { }
                        plan.PreparedJob = job;
                        plan.Scanner = scanner;
                        plan.GiverDef = giver;
                        plan.WorkType = type;
                        return true;
                    }
                }

                failReason = BridgeCommon.Try(() => JobFailReason.HaveReason, false)
                    ? BridgeCommon.SafeString(() => JobFailReason.Reason)
                    : null;
                return false;
            }
            finally
            {
                if (swapped)
                {
                    try { FloatMenuMakerMap.makingFor = previous; } catch { }
                }
                try { JobFailReason.Clear(); } catch { }
            }
        }

        /// <summary>`FloatMenuOptionProvider_WorkGivers.ScannerShouldSkip`,
        /// verbatim: a giver that does not even claim the thing is skipped
        /// before it is asked for a job.</summary>
        private static bool ScannerShouldSkip(Pawn pawn, WorkGiver_Scanner scanner, Thing t)
        {
            return !BridgeCommon.Try(() =>
            {
                var accepts = scanner.PotentialWorkThingRequest.Accepts(t);
                if (!accepts)
                {
                    var global = scanner.PotentialWorkThingsGlobal(pawn);
                    accepts = global != null && global.Contains(t);
                }
                return accepts && !scanner.ShouldSkip(pawn, true);
            }, false);
        }

        /// <summary>Is there anywhere better to put this thing at all? Only
        /// asked AFTER a haul job failed, to tell "nowhere to put it" apart
        /// from every other reason, so the caller gets `no_storage` rather than
        /// a shrug. This is `HaulAIUtility.HaulToStorageJob`'s own first
        /// question, asked again.</summary>
        private static bool HasSomewhereToPut(Plan plan)
        {
            try
            {
                IntVec3 cell;
                IHaulDestination destination;
                var current = StoreUtility.CurrentStoragePriorityOf(plan.Target, true);
                return StoreUtility.TryFindBestBetterStorageFor(
                    plan.Target, plan.Pawn, plan.Map, current, plan.Pawn.Faction, out cell, out destination);
            }
            catch { return true; }
        }

        /// <summary>
        /// The same search with NO carrier, which is the only way to tell
        /// "nowhere to put it" from "this pawn cannot get to where it goes".
        ///
        /// `StoreUtility.IsGoodStoreCell` runs three carrier-only gates --
        /// `c.IsForbidden(carrier)`, `carrier.CanReserveNew(c)` and
        /// `carrier.Map.reachability.CanReach(start, c, ClosestTouch, ...)` --
        /// and `TryFindBestBetterNonSlotGroupStorageFor` adds the same for
        /// containers. Passing `carrier: null` skips all of them and asks the
        /// storage question on its own. Turn 31 of the 2026-09-07 stream was
        /// told `no_storage` when a stockpile was standing empty and willing on
        /// the far side of a wall.
        ///
        /// -> null when the search threw; otherwise found/cell.
        /// </summary>
        private static bool? StorageIgnoringCarrier(Plan plan, out IntVec3 foundCell)
        {
            foundCell = IntVec3.Invalid;
            try
            {
                IntVec3 cell;
                IHaulDestination destination;
                var current = StoreUtility.CurrentStoragePriorityOf(plan.Target, true);
                var found = StoreUtility.TryFindBestBetterStorageFor(
                    plan.Target, null, plan.Map, current, plan.Pawn.Faction, out cell, out destination);
                if (found)
                    foundCell = cell;
                return found;
            }
            catch { return null; }
        }

        /// <summary>Every silent gate in PawnCanAutomaticallyHaulFast_NewTemp,
        /// plus the storage search. Pure reads, emitted on success and refusal
        /// so a null JobOnThing is diagnosable without changing the colony.</summary>
        private static Dictionary<string, object> HaulCheckBlock(Plan plan)
        {
            var p = plan.Pawn;
            var t = plan.Target;
            var checks = new Dictionary<string, object>();
            checks["everHaulable"] = BridgeCommon.Try(() => t.def != null && t.def.EverHaulable, false);
            checks["alwaysHaulable"] = BridgeCommon.Try(() => t.def != null && t.def.alwaysHaulable, false);
            checks["fogged"] = BridgeCommon.Try(() => t.Fogged(), false);
            checks["canReserveForced"] = BridgeCommon.Try(() => p.CanReserve(t, 1, -1, null, true), false);
            checks["manipulationCapable"] = BridgeCommon.Try(
                () => p.health.capacities.CapableOf(PawnCapacityDefOf.Manipulation), false);
            checks["reachableNormalDanger"] = BridgeCommon.Try(
                () => p.CanReach(t, PathEndMode.ClosestTouch, p.NormalMaxDanger()), false);
            checks["burning"] = BridgeCommon.Try(() => t.IsBurning(), false);
            checks["haulDesignation"] = BridgeCommon.Try(
                () => t.Map.designationManager.DesignationOn(t, DesignationDefOf.Haul) != null, false);
            checks["inValidStorage"] = BridgeCommon.Try(() => t.IsInValidStorage(), false);
            try
            {
                IntVec3 cell;
                IHaulDestination destination;
                var priority = StoreUtility.CurrentStoragePriorityOf(t, true);
                var found = StoreUtility.TryFindBestBetterStorageFor(
                    t, p, plan.Map, priority, p.Faction, out cell, out destination);
                checks["currentStoragePriority"] = priority.ToString();
                checks["betterStorageFound"] = found;
                checks["storageCell"] = found ? BridgeCommon.Pos(cell) : null;
                checks["storageDestination"] = found && destination != null
                    ? destination.ToString() : null;
            }
            catch (Exception ex)
            {
                checks["storageCheckError"] = ex.GetType().Name;
            }
            // The same search with the carrier ignored. `betterStorageFound`
            // false while this one is true means the storage is fine and the
            // PAWN's reach/forbidden/reservation gates are what failed -- the
            // distinction `no_storage` used to swallow (turn 31).
            IntVec3 ignoringCell;
            var ignoring = StorageIgnoringCarrier(plan, out ignoringCell);
            checks["betterStorageFoundIgnoringCarrier"] = ignoring;
            checks["storageCellIgnoringCarrier"] =
                ignoring == true ? BridgeCommon.Pos(ignoringCell) : null;
            return checks;
        }

        // =================================================================
        // Apply -- the only writes in this file
        // =================================================================

        private static void Apply(Plan plan)
        {
            if (plan.Error != null || plan.Pawn == null)
                return;

            if (plan.NeedsDraft || plan.NeedsUndraft)
            {
                var wanted = plan.NeedsDraft;
                try
                {
                    var drafter = plan.Pawn.drafter;
                    if (drafter == null)
                    {
                        plan.Refuse("draft_refused", NameOf(plan.Pawn) + " has no draft controller.");
                        return;
                    }
                    drafter.Drafted = wanted;
                }
                catch (Exception ex)
                {
                    plan.Refuse("draft_refused",
                        "Setting Pawn_DraftController.Drafted threw " + ex.GetType().Name + "; nothing else was attempted.");
                    return;
                }

                var now = BridgeCommon.Try(() => plan.Pawn.Drafted, false);
                if (now != wanted)
                {
                    plan.Refuse("draft_refused",
                        "The game refused to " + (wanted ? "draft " : "undraft ") + NameOf(plan.Pawn)
                        + ": Pawn.Drafted still reads " + (now ? "true" : "false") + " after the write.");
                    return;
                }
                if (wanted) plan.AutoDrafted = true; else plan.AutoUndrafted = true;
                plan.Applied = true;
            }

            if (plan.JobDef == null)
                return;

            // Vanilla unforbids an equip target before ordering the pickup; a
            // forbidden weapon cannot be reserved, which is the likeliest cause
            // of "equip failed, then the identical retry worked".
            //
            // NOT `ForbidUtility.SetForbidden(t, false)`, and NOT
            // `ForbidUtility.IsForbidden`. SetForbidden has THREE `Log.Error`
            // arms -- null, not a ThingWithComps, no CompForbiddable -- and
            // `Log.Error` calls `TickManager.Pause()`, so unforbidding a thing
            // that cannot be forbidden would pause the colony. Its success arm
            // is one line, `comp.Forbidden = value`, which is what runs here.
            // `IsForbidden(Thing, Faction)` is worse: its body opens with
            // `faction != Faction.OfPlayer`, the banned getter.
            if (plan.UnforbidTarget && plan.Target != null)
            {
                try
                {
                    var comps = plan.Target as ThingWithComps;
                    var forbiddable = comps == null ? null : comps.GetComp<CompForbiddable>();
                    if (forbiddable != null && forbiddable.Forbidden)
                    {
                        forbiddable.Forbidden = false;
                        plan.Unforbade = true;
                    }
                }
                catch
                {
                    plan.Unforbade = false;
                }
            }

            // The job shapes below are vanilla's, field for field. Nothing is
            // added: `playerForced` is set by TryTakeOrderedJob itself, and
            // neither drafted attack option sets an expiry, a verb or an
            // attack cap. See the Build* remarks for the source they came from.
            Job job;
            try
            {
                // A WorkGiver's job is issued exactly as the giver built it:
                // it carries targetQueueB, countQueue, haulMode and the bill,
                // none of which can be reconstructed from a def and a target.
                if (plan.PreparedJob != null)
                {
                    job = plan.PreparedJob;
                }
                else
                {
                    job = plan.TargetB.IsValid
                        ? JobMaker.MakeJob(plan.JobDef, plan.TargetA, plan.TargetB)
                        : JobMaker.MakeJob(plan.JobDef, plan.TargetA);
                    if (plan.KillIncappedTarget)
                        job.killIncappedTarget = true;
                    if (plan.Count >= 0)
                        job.count = plan.Count;
                    if (plan.DraftedTend)
                        job.draftedTend = true;
                }
                plan.ExpiryInterval = job.expiryInterval;
            }
            catch (Exception ex)
            {
                plan.Refuse("job_refused", "Building the " + DefNameOf(plan.JobDef) + " job threw " + ex.GetType().Name + ".");
                return;
            }

            try
            {
                var jobs = plan.Pawn.jobs;
                if (jobs == null)
                {
                    plan.Refuse("job_refused", NameOf(plan.Pawn) + " has no job tracker.");
                    return;
                }
                // TryTakeOrderedJobPrioritizedWork is what the float menu uses
                // for a work order: it forwards to TryTakeOrderedJob with the
                // giver's own tagToGive, then records the priority-work cell so
                // the pawn keeps working that spot. A plain order uses JobTag.Misc.
                plan.Issued = plan.Scanner != null
                    ? jobs.TryTakeOrderedJobPrioritizedWork(job, plan.Scanner, BridgeCommon.Try(() => plan.Target.Position, IntVec3.Invalid))
                    : jobs.TryTakeOrderedJob(job, JobTag.Misc);
            }
            catch (Exception ex)
            {
                plan.Refuse("job_refused",
                    "Pawn_JobTracker.TryTakeOrderedJob threw " + ex.GetType().Name + " on a " + DefNameOf(plan.JobDef) + " job.");
                return;
            }

            plan.Applied = true;

            // The read-back. TryTakeOrderedJob returning true is the game
            // saying it accepted the job, not that it is running it: a mental
            // state or a higher-priority job giver can replace it in the same
            // frame. CurJob is what is actually true.
            var current = BridgeCommon.Try<Job>(() => plan.Pawn.CurJob, null);
            if (current == null)
            {
                plan.VerifiedReason = "Pawn.CurJob is null after the issue: the game took no job at all.";
            }
            else if (!ReferenceEquals(current.def, plan.JobDef))
            {
                plan.VerifiedReason = "Pawn.CurJob is a " + DefNameOf(current.def) + " job, not the "
                                      + DefNameOf(plan.JobDef) + " job that was issued.";
            }
            else if (plan.TargetA.IsValid && !SameTarget(current.targetA, plan.TargetA))
            {
                plan.VerifiedReason = "Pawn.CurJob is a " + DefNameOf(plan.JobDef)
                                      + " job against a different target than the one ordered.";
            }
            else
            {
                plan.Verified = true;
                plan.VerifiedReason = null;
            }

            if (!plan.Verified)
            {
                plan.Refuse("job_unverified",
                    "The order did not stick. " + (plan.VerifiedReason ?? "Pawn.CurJob did not match the job that was issued.")
                    + " TryTakeOrderedJob returned " + (plan.Issued ? "true" : "false") + ".");
            }
        }

        private static bool SameTarget(LocalTargetInfo a, LocalTargetInfo b)
        {
            try
            {
                if (b.HasThing)
                    return a.HasThing && ReferenceEquals(a.Thing, b.Thing);
                return !a.HasThing && a.Cell == b.Cell;
            }
            catch { return false; }
        }

        // =================================================================
        // Job construction -- the shapes vanilla itself builds
        // =================================================================

        /// <summary>
        /// `FloatMenuUtility.GetMeleeAttackAction`, minus the menu:
        ///
        ///     Job job = JobMaker.MakeJob(JobDefOf.AttackMelee, target);
        ///     if (target.Thing is Pawn p) job.killIncappedTarget = p.Downed;
        ///     pawn.jobs.TryTakeOrderedJob(job, JobTag.Misc);
        ///
        /// That is the entire job. No `expiryInterval`, no
        /// `maxNumMeleeAttacks`, no `canBashDoors`, no `attackDoorIfTargetLost`
        /// -- and `playerForced` is set by `TryTakeOrderedJob` itself, not here.
        /// `killIncappedTarget` on a downed target is what the game labels
        /// "Melee attack to death".
        ///
        /// The two refusals are vanilla's own, in vanilla's order:
        /// `!pawn.CanReach(target, PathEndMode.Touch, Danger.Deadly)` ("NoPath")
        /// and `pawn.meleeVerbs.TryGetMeleeVerb(target) == null` ("Incapable").
        /// </summary>
        private static void BuildMeleeJob(Plan plan)
        {
            plan.JobDef = BridgeCommon.Try<JobDef>(() => JobDefOf.AttackMelee, null);
            if (plan.JobDef == null)
            {
                plan.Refuse("job_refused", "JobDefOf.AttackMelee was not found in this build.");
                return;
            }

            if (!BridgeCommon.Try(() => plan.Pawn.CanReach(plan.Target, PathEndMode.Touch, Danger.Deadly), false))
            {
                plan.Refuse("not_reachable",
                    NameOf(plan.Pawn) + " cannot reach " + Describe(plan.Target)
                    + " to melee it, even accepting deadly danger -- this is vanilla's own \"NoPath\" refusal. "
                    + "Try mode:\"ranged\" if the pawn is armed, or move the pawn first.");
                return;
            }

            // Pawn_MeleeVerbs.TryGetMeleeVerb memoises curMeleeVerb; it is the
            // call the float menu itself makes, so it is safe here.
            if (BridgeCommon.Try<Verb>(() => plan.Pawn.meleeVerbs.TryGetMeleeVerb(plan.Target), null) == null)
            {
                plan.Refuse("job_refused",
                    NameOf(plan.Pawn) + " has no melee verb that can strike " + Describe(plan.Target)
                    + " (Pawn_MeleeVerbs.TryGetMeleeVerb returned null) -- vanilla's \"Incapable\".");
                return;
            }

            plan.TargetA = plan.Target;
            plan.KillIncappedTarget = plan.TargetPawn != null && BridgeCommon.Try(() => plan.TargetPawn.Downed, false);
            plan.Verb = BridgeCommon.Try<Verb>(() => plan.Pawn.meleeVerbs.TryGetMeleeVerb(plan.Target), null);
        }

        /// <summary>
        /// `FloatMenuUtility.GetRangedAttackAction`, minus the menu:
        ///
        ///     Job job = JobMaker.MakeJob(JobDefOf.AttackStatic, target);
        ///     pawn.jobs.TryTakeOrderedJob(job, JobTag.Misc);
        ///
        /// **No `verbToUse` is set, deliberately.** `JobDriver_AttackStatic`
        /// picks the verb itself every time it runs, with
        /// `pawn.TryGetAttackVerb(TargetA.Thing, !pawn.IsColonist)`; pinning a
        /// verb onto the job would be a shape vanilla never builds. The verb is
        /// still REPORTED, from the same call, so a caller can see which one
        /// the driver will choose.
        ///
        /// Refusals are vanilla's: no primary equipment, a primary whose verb
        /// is a melee attack, and `PrimaryVerb.CanHitTarget(target)` false --
        /// which is out of range, too close, or no line of sight.
        /// </summary>
        private static void BuildRangedJob(Plan plan)
        {
            plan.JobDef = BridgeCommon.Try<JobDef>(() => JobDefOf.AttackStatic, null);
            if (plan.JobDef == null)
            {
                plan.Refuse("job_refused", "JobDefOf.AttackStatic was not found in this build.");
                return;
            }

            if (!BridgeCommon.Try(() => FloatMenuUtility.UseRangedAttack(plan.Pawn), false))
            {
                plan.Refuse("job_refused",
                    NameOf(plan.Pawn) + " has no ranged attack: FloatMenuUtility.UseRangedAttack is false, which means "
                    + "either nothing is equipped or the equipped weapon's primary verb is a melee attack. Use mode:\"melee\".");
                return;
            }

            var primary = BridgeCommon.Try<Verb>(
                () => plan.Pawn.equipment.PrimaryEq.PrimaryVerb, null);
            if (primary == null)
            {
                plan.Refuse("job_refused",
                    NameOf(plan.Pawn) + "'s equipped weapon has no primary verb to fire.");
                return;
            }
            if (!BridgeCommon.Try(() => primary.CanHitTarget(plan.Target), false))
            {
                plan.Refuse("job_refused",
                    NameOf(plan.Pawn) + " cannot hit " + Describe(plan.Target)
                    + " from where it stands: Verb.CanHitTarget is false, which is out of range, too close, or no line of "
                    + "sight. Move the pawn with action 'goto' first, or use mode:\"melee\".");
                return;
            }

            plan.TargetA = plan.Target;
            // Reported, never written onto the job: this is the verb
            // JobDriver_AttackStatic will pick for itself.
            plan.Verb = BridgeCommon.Try<Verb>(
                () => plan.Pawn.TryGetAttackVerb(plan.Target, !plan.Pawn.IsColonist), null);
        }

        /// <summary>
        /// `FloatMenuOptionProvider_DraftedMove`: the clicked cell is snapped
        /// with `CellFinder.StandableCellNear(cell, map, 2.9f)` and then
        /// `RCellFinder.BestOrderedGotoDestNear(cell, pawn)`, so clicking a
        /// wall still sends the pawn to the nearest sensible spot. Both are
        /// tried here and both fall back to the raw cell.
        ///
        /// **`exitMapOnArrival` is deliberately NOT set**, unlike vanilla. The
        /// game sets it when the clicked cell is on the exit grid, which turns
        /// a mis-aimed move order into a colonist walking off the map for good.
        /// That is the one thing in this file that does less than the float
        /// menu, on purpose, and it is stated in the payload's `note`.
        /// </summary>
        private static void BuildGotoJob(Plan plan)
        {
            plan.JobDef = BridgeCommon.Try<JobDef>(() => JobDefOf.Goto, null);
            if (plan.JobDef == null)
            {
                plan.Refuse("job_refused", "JobDefOf.Goto was not found in this build.");
                return;
            }
            plan.TargetA = plan.Cell;
        }

        /// <summary>
        /// `FloatMenuOptionProvider_Equip`. The job itself is one line --
        /// `MakeJob(JobDefOf.Equip, thing)` -- but vanilla calls
        /// `clickedThing.SetForbidden(false)` FIRST, and that is almost
        /// certainly the "equip fails then succeeds on an identical retry" bug:
        /// a forbidden weapon cannot be reserved, so the first order dies and
        /// something else unforbids it in between.
        /// </summary>
        private static void BuildEquipJob(Plan plan)
        {
            plan.JobDef = BridgeCommon.Try<JobDef>(() => JobDefOf.Equip, null);
            if (plan.JobDef == null)
            {
                plan.Refuse("job_refused", "JobDefOf.Equip was not found in this build.");
                return;
            }
            plan.TargetA = plan.Target;
            plan.UnforbidTarget = true;
        }

        /// <summary>
        /// `FloatMenuOptionProvider_RescuePawn`. The bed is found the way
        /// vanilla finds it -- `RestUtility.FindBedFor(patient, rescuer,
        /// checkSocialProperness: false)`, then again with
        /// `ignoreOtherReservations: true` -- and no bed means no job at all,
        /// which vanilla reports as a rejected click and this reports as a
        /// refusal naming the missing bed.
        /// </summary>
        private static void BuildRescueJob(Plan plan)
        {
            plan.JobDef = BridgeCommon.Try<JobDef>(() => JobDefOf.Rescue, null);
            if (plan.JobDef == null)
            {
                plan.Refuse("job_refused", "JobDefOf.Rescue was not found in this build.");
                return;
            }

            var bed = BridgeCommon.Try<Building_Bed>(
                () => RestUtility.FindBedFor(plan.TargetPawn, plan.Pawn, checkSocialProperness: false), null);
            if (bed == null)
            {
                bed = BridgeCommon.Try<Building_Bed>(
                    () => RestUtility.FindBedFor(plan.TargetPawn, plan.Pawn, false, ignoreOtherReservations: true), null);
            }
            if (bed == null)
            {
                plan.Refuse("job_refused",
                    "There is no free bed " + Describe(plan.Target) + " could be carried to, so vanilla issues no rescue "
                    + "job either (RestUtility.FindBedFor returned null both with and without ignoreOtherReservations). "
                    + "Build or free a bed first; an animal needs an animal bed and a non-prisoner needs a non-prisoner bed.");
                return;
            }

            plan.TargetA = plan.Target;
            plan.TargetB = bed;
            plan.Count = 1;
        }

        /// <summary>
        /// `FloatMenuOptionProvider_DraftedTend` -- and this settles the
        /// question two forks disagreed about on 2026-09-04.
        ///
        /// **Tending a pawn lying on the ground is real.** The provider never
        /// looks at a bed. `IsValidTendTarget` is
        /// `if (!doctor.Drafted &amp;&amp; patient != doctor) return false;` then
        /// `if (patient.Downed) return true;` -- so a DRAFTED doctor may tend
        /// any downed pawn where it lies. Medicine is optional:
        /// `HealthAIUtility.FindBestMedicine(doctor, patient, onlyUseInventory:
        /// true)` may return null, and vanilla then labels the option
        /// "Tend X (without medicine)" and issues the job anyway.
        ///
        ///     Job job = JobMaker.MakeJob(JobDefOf.TendPatient, patient, medicine);
        ///     job.count = 1;
        ///     job.draftedTend = true;
        ///     doctor.jobs.TryTakeOrderedJob(job, JobTag.Misc);
        ///
        /// The fork that read `Rescue / Strip` only and concluded ground
        /// tending is impossible was reading the menu of an UNDRAFTED pawn:
        /// this provider's `Drafted` is true and its `Undrafted` is false, so
        /// the Tend option is simply not drawn until the doctor is drafted.
        /// That is why this action drafts.
        /// </summary>
        private static void BuildTendJob(Plan plan)
        {
            plan.JobDef = BridgeCommon.Try<JobDef>(() => JobDefOf.TendPatient, null);
            if (plan.JobDef == null)
            {
                plan.Refuse("job_refused", "JobDefOf.TendPatient was not found in this build.");
                return;
            }
            plan.TargetA = plan.Target;
            plan.TargetB = BridgeCommon.Try<Thing>(
                () => HealthAIUtility.FindBestMedicine(plan.Pawn, plan.TargetPawn, onlyUseInventory: true), null);
            plan.Count = 1;
            plan.DraftedTend = true;
        }

        // =================================================================
        // Resolution
        // =================================================================

        /// <summary>
        /// One thing on this map, by any of the four id forms.
        ///
        /// Order matters and is the whole point: an EXPLICIT id (the load id,
        /// the ThingID, the bare number) is tried first and can only hit one
        /// thing, so it never ties. A name is tried afterwards, exactly first
        /// and then as a unique substring, and only that last arm can produce
        /// `candidates[]`.
        ///
        /// The pool is `map.listerThings.AllThings`, which is every SPAWNED
        /// thing including every pawn, plus each corpse's `InnerPawn` -- so a
        /// pawn that has just died still answers to its own id rather than
        /// vanishing, and the caller gets `dead: true` instead of
        /// "target_not_found".
        /// </summary>
        private static Thing Resolve(Map map, string query, bool pawnsOnly, out List<Thing> candidates, out string matchedBy)
        {
            candidates = new List<Thing>();
            matchedBy = null;

            var text = query == null ? string.Empty : query.Trim();
            if (text.Length == 0)
                return null;

            var pool = Pool(map, pawnsOnly);

            // -------------------------------------------- 1. full load id
            var hit = pool.FirstOrDefault(t => Eq(BridgeCommon.SafeString(() => t.GetUniqueLoadID()), text));
            if (hit != null) { matchedBy = "loadId"; return hit; }

            // -------------------------------------------------- 2. ThingID
            hit = pool.FirstOrDefault(t => Eq(BridgeCommon.SafeString(() => t.ThingID), text));
            if (hit != null) { matchedBy = "thingID"; return hit; }

            // ------------------------------------------- 3. bare id number
            int number;
            if (int.TryParse(text, NumberStyles.Integer, CultureInfo.InvariantCulture, out number))
            {
                hit = pool.FirstOrDefault(t => BridgeCommon.Try(() => t.thingIDNumber, -1) == number);
                if (hit != null) { matchedBy = "idNumber"; return hit; }
                // A bare number that matched nothing is a not-found, never a
                // name: no thing is called "361788".
                return null;
            }

            // A bare x,z is useful when another map reader did not expose an
            // id. Prefer the single spawned pawn there (the combat case), else
            // require the cell to identify exactly one spawned thing.
            var cellBits = text.Split(',');
            int bareX, bareZ;
            if (cellBits.Length == 2
                && int.TryParse(cellBits[0].Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out bareX)
                && int.TryParse(cellBits[1].Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out bareZ))
            {
                var bareCell = new IntVec3(bareX, 0, bareZ);
                var atCell = pool.Where(t => BridgeCommon.Try(() => t.Spawned, false)
                                             && Covers(t, bareCell)).ToList();
                var pawnsAtCell = atCell.Where(t => t is Pawn).ToList();
                if (pawnsAtCell.Count == 1) { matchedBy = "cellPawn"; return pawnsAtCell[0]; }
                if (atCell.Count == 1) { matchedBy = "cell"; return atCell[0]; }
                candidates = pawnsAtCell.Count > 1 ? pawnsAtCell : atCell;
                return null;
            }

            // -------------------------------------- 3b. the DefName@x,z form
            // The same address home/bills and home/building_config take, so a
            // bench found with bills.py can be handed straight to this tool.
            // A malformed cell part falls THROUGH to the name arms rather than
            // refusing: a label is allowed to contain an "@".
            var at = text.IndexOf('@');
            if (at > 0)
            {
                var defPart = text.Substring(0, at).Trim();
                var cellPart = text.Substring(at + 1).Trim();
                var bits = cellPart.Split(',');
                int cx, cz;
                if (bits.Length == 2
                    && int.TryParse(bits[0].Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out cx)
                    && int.TryParse(bits[1].Trim(), NumberStyles.Integer, CultureInfo.InvariantCulture, out cz))
                {
                    var cell = new IntVec3(cx, 0, cz);
                    var here = pool.Where(t =>
                        Eq(BridgeCommon.SafeString(() => t.def == null ? null : t.def.defName), defPart)
                        && Covers(t, cell)).ToList();
                    if (here.Count == 1) { matchedBy = "defNameAtCell"; return here[0]; }
                    candidates = here;
                    return null;
                }
            }

            // ------------------------------------------- 4a. exact name/label
            var exact = pool.Where(t => Names(t).Any(n => Eq(n, text))).ToList();
            if (exact.Count == 1) { matchedBy = "name"; return exact[0]; }
            if (exact.Count > 1)
            {
                // Hidden pawns inside old corpses stay explicitly addressable,
                // but must not make one live animal with that name ambiguous.
                var liveSpawned = exact.Where(t => BridgeCommon.Try(() => t.Spawned, false)
                                                   && !BridgeCommon.Try(() => t.Destroyed, false)
                                                   && !(t is Pawn && BridgeCommon.Try(() => ((Pawn)t).Dead, false)))
                                       .ToList();
                if (liveSpawned.Count == 1) { matchedBy = "liveName"; return liveSpawned[0]; }
                candidates = exact;
                return null;
            }

            // ---------------------------------------- 4b. unique substring
            var partial = pool.Where(t => Names(t).Any(n => Contains(n, text))).ToList();
            if (partial.Count == 1) { matchedBy = "nameSubstring"; return partial[0]; }
            candidates = partial;
            return null;
        }

        /// <summary>How many candidates a tie may list. A one-letter query can
        /// match most of the map, and a payload naming nine hundred things is
        /// no more useful than one naming twenty-five. The refusal text always
        /// carries the true total.</summary>
        private const int MaxCandidates = 25;

        /// <summary>Every spawned thing on the map, plus the pawn inside each
        /// corpse, de-duplicated by reference.</summary>
        private static List<Thing> Pool(Map map, bool pawnsOnly)
        {
            var pool = new List<Thing>();
            var seen = new HashSet<Thing>();

            List<Thing> spawned;
            try { spawned = map.listerThings.AllThings.ToList(); }
            catch { spawned = new List<Thing>(); }

            foreach (var thing in spawned)
            {
                if (thing == null)
                    continue;
                if (seen.Add(thing) && (!pawnsOnly || thing is Pawn))
                    pool.Add(thing);

                var corpse = thing as Corpse;
                if (corpse == null)
                    continue;
                var inner = BridgeCommon.Try<Pawn>(() => corpse.InnerPawn, null);
                if (inner != null && seen.Add(inner))
                    pool.Add(inner);
            }

            return pool;
        }

        /// <summary>Every string a caller might reasonably call this thing.
        /// Nulls are dropped rather than matched against.</summary>
        private static IEnumerable<string> Names(Thing thing)
        {
            var names = new List<string>();
            var pawn = thing as Pawn;
            if (pawn != null)
            {
                names.Add(BridgeCommon.SafeString(() => pawn.Name == null ? null : pawn.Name.ToStringShort));
                names.Add(BridgeCommon.SafeString(() => pawn.Name == null ? null : pawn.Name.ToStringFull));
                names.Add(BridgeCommon.SafeString(() => pawn.LabelShort));
                names.Add(BridgeCommon.SafeString(() => pawn.KindLabel));
            }
            names.Add(BridgeCommon.SafeString(() => thing.LabelShort));
            names.Add(BridgeCommon.SafeString(() => thing.LabelNoCount));
            names.Add(BridgeCommon.SafeString(() => thing.Label));
            names.Add(BridgeCommon.SafeString(() => thing.def == null ? null : thing.def.label));
            names.Add(BridgeCommon.SafeString(() => thing.def == null ? null : thing.def.defName));
            return names.Where(n => !string.IsNullOrEmpty(n));
        }

        /// <summary>Does a thing stand on this cell? `GenAdj.OccupiedRect`
        /// so a multi-cell bench answers at every cell it covers, not only at
        /// `Thing.Position` -- which for a 3x1 table is not even the corner.
        /// Same test home/building_config uses.</summary>
        private static bool Covers(Thing thing, IntVec3 cell)
        {
            try { return GenAdj.OccupiedRect(thing).Contains(cell); }
            catch { return BridgeCommon.Try(() => thing.Position == cell, false); }
        }

        /// <summary>" (the first 25 are listed)" when a tie was capped.</summary>
        private static string Listing(int total)
        {
            return total > MaxCandidates
                ? " (the first " + MaxCandidates + " are listed in candidates[])"
                : string.Empty;
        }

        private static bool Eq(string a, string b)
        {
            return a != null && string.Equals(a, b, StringComparison.OrdinalIgnoreCase);
        }

        private static bool Contains(string haystack, string needle)
        {
            return haystack != null && haystack.IndexOf(needle, StringComparison.OrdinalIgnoreCase) >= 0;
        }

        // =================================================================
        // Reads
        // =================================================================

        /// <summary>
        /// Whether the game will let this pawn be drafted, and why not when it
        /// will not. **WorkTags.Violent is deliberately NOT on this list**: a
        /// pawn incapable of violence drafts perfectly well in vanilla -- the
        /// draft gizmo is drawn and enabled for it -- and only the attack
        /// ORDERS are refused. A fork that skipped drafting everyone on that
        /// belief is what left two colonists standing still.
        /// </summary>
        private static bool CanBeDrafted(Pawn pawn, out string reason)
        {
            reason = null;
            if (pawn == null) { reason = "there is no pawn."; return false; }

            if (BridgeCommon.Try(() => pawn.drafter, (Pawn_DraftController)null) == null)
            {
                reason = "it has no Pawn_DraftController -- animals, prisoners and other factions' pawns have none.";
                return false;
            }
            if (BridgeCommon.Try(() => pawn.Dead, false)) { reason = "it is dead."; return false; }
            if (BridgeCommon.Try(() => pawn.Downed, false)) { reason = "it is downed."; return false; }
            if (!BridgeCommon.Try(() => pawn.Spawned, false)) { reason = "it is not spawned on a map."; return false; }

            var mental = MentalStateOf(pawn);
            if (mental != null)
            {
                reason = "it is in a mental state (" + mental + ").";
                return false;
            }
            return true;
        }

        private static bool IncapableOfViolence(Pawn pawn)
        {
            return BridgeCommon.Try(() => pawn.WorkTagIsDisabled(WorkTags.Violent), false);
        }

        private static string MentalStateOf(Pawn pawn)
        {
            return BridgeCommon.SafeString(
                () => pawn.MentalStateDef == null ? null : pawn.MentalStateDef.defName);
        }

        private static ThingWithComps PrimaryWeapon(Pawn pawn)
        {
            return BridgeCommon.Try<ThingWithComps>(
                () => pawn.equipment == null ? null : pawn.equipment.Primary, null);
        }

        private static bool IsRangedWeapon(Thing weapon)
        {
            return BridgeCommon.Try(() => weapon.def != null && weapon.def.IsRangedWeapon, false);
        }

        private static Thing WeaponOnCell(Map map, IntVec3 cell)
        {
            try
            {
                var things = map.thingGrid.ThingsListAtFast(cell);
                if (things == null)
                    return null;
                for (var i = 0; i < things.Count; i++)
                {
                    var t = things[i];
                    if (t != null && BridgeCommon.Try(() => t.def != null && t.def.IsWeapon, false))
                        return t;
                }
            }
            catch { }
            return null;
        }

        private static bool HostileToPlayer(Thing thing)
        {
            try
            {
                var pawn = thing as Pawn;
                var mental = pawn == null ? null : MentalStateOf(pawn);
                if (!string.IsNullOrEmpty(mental)
                    && mental.IndexOf("Manhunter", StringComparison.OrdinalIgnoreCase) >= 0)
                    return true;

                var faction = thing.Faction;
                if (faction == null)
                    return false;
                var player = Faction.OfPlayerSilentFail;
                return player != null && faction != player && faction.HostileTo(player);
            }
            catch { return false; }
        }

        // =================================================================
        // The reply
        // =================================================================

        private static object Reply(Plan plan, Dictionary<string, object> watch, bool applied)
        {
            var payload = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "success", plan.Error == null },
                { "tool", ToolName },
                { "action", plan.Request.Action },
                { "dryRun", plan.Request.DryRun },
                { "applied", applied && plan.Applied },
                { "pawn", PawnBlock(plan) },
                { "target", TargetBlock(plan) },
                { "job", plan.Request.DryRun || !plan.Applied || plan.JobDef == null ? null : JobBlock(plan, true) },
                { "wouldIssue", plan.Request.DryRun && plan.JobDef != null ? JobBlock(plan, false) : null },
                { "candidates", CandidateBlocks(plan) },
                { "after", AfterBlock(plan) },
                { "diagnostics", DiagnosticsBlock(plan) },
                { "watch", watch },
                { "error", plan.Error },
                { "errorKind", plan.ErrorKind }
            };
            return payload;
        }

        private static object DiagnosticsBlock(Plan plan)
        {
            if (plan.Request.Action != "haul")
                return new Dictionary<string, object>();
            return new Dictionary<string, object>
            {
                { "globalHaulCandidateCount", plan.HaulGlobalCandidateCount },
                { "targetInGlobalHaulList", plan.HaulTargetInGlobalList },
                { "checks", plan.HaulChecks ?? new Dictionary<string, object>() }
            };
        }

        private static object PawnBlock(Plan plan)
        {
            var pawn = plan.Pawn;
            if (pawn == null)
                return null;

            var weapon = PrimaryWeapon(pawn);
            string draftReason;
            var canDraft = CanBeDrafted(pawn, out draftReason);

            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "thingId", BridgeCommon.SafeString(() => pawn.GetUniqueLoadID()) },
                { "idForms", IdForms(pawn) },
                { "name", NameOf(pawn) },
                { "position", BridgeCommon.PositionOf(pawn) },
                { "spawned", BridgeCommon.Try(() => pawn.Spawned, false) },
                { "faction", BridgeCommon.SafeString(() => pawn.Faction == null ? null : pawn.Faction.Name) },
                { "drafted", BridgeCommon.Try(() => pawn.Drafted, false) },
                { "autoDrafted", plan.AutoDrafted },
                { "autoUndrafted", plan.AutoUndrafted },
                { "downed", BridgeCommon.Try(() => pawn.Downed, false) },
                { "dead", BridgeCommon.Try(() => pawn.Dead, false) },
                { "mentalState", MentalStateOf(pawn) },
                { "playerControlled", BridgeCommon.Try(() => pawn.IsColonistPlayerControlled, false) },
                { "incapableOfViolence", IncapableOfViolence(pawn) },
                { "canBeDrafted", canDraft },
                { "canBeDraftedReason", draftReason },
                {
                    "weapon", weapon == null ? null : new Dictionary<string, object>(StringComparer.Ordinal)
                    {
                        { "thingId", BridgeCommon.SafeString(() => weapon.GetUniqueLoadID()) },
                        { "label", BridgeCommon.SafeString(() => weapon.LabelCap.ToString()) },
                        { "defName", BridgeCommon.SafeString(() => weapon.def == null ? null : weapon.def.defName) },
                        { "ranged", IsRangedWeapon(weapon) },
                        { "melee", BridgeCommon.Try(() => weapon.def != null && weapon.def.IsMeleeWeapon, false) }
                    }
                }
            };
        }

        private static object TargetBlock(Plan plan)
        {
            var target = plan.Target;
            if (target == null)
                return null;

            var pawn = plan.TargetPawn;
            int? distance = null;
            bool? reachable = null;
            if (plan.Pawn != null && BridgeCommon.Try(() => target.Spawned && plan.Pawn.Spawned, false))
            {
                distance = BridgeCommon.TryN(() => plan.Pawn.Position.DistanceToSquared(target.Position) <= 0
                    ? 0
                    : (int)Math.Round(Math.Sqrt(plan.Pawn.Position.DistanceToSquared(target.Position))));
                reachable = BridgeCommon.TryN(() => plan.Pawn.CanReach(target, PathEndMode.Touch, Danger.Deadly));
            }

            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "thingId", BridgeCommon.SafeString(() => target.GetUniqueLoadID()) },
                { "idForms", IdForms(target) },
                { "name", pawn != null ? NameOf(pawn) : BridgeCommon.SafeString(() => target.LabelShortCap.ToString()) },
                { "label", BridgeCommon.SafeString(() => target.LabelCap.ToString()) },
                { "defName", BridgeCommon.SafeString(() => target.def == null ? null : target.def.defName) },
                { "kindDef", pawn == null ? null : BridgeCommon.SafeString(() => pawn.kindDef == null ? null : pawn.kindDef.defName) },
                { "faction", BridgeCommon.SafeString(() => target.Faction == null ? null : target.Faction.Name) },
                { "isPawn", pawn != null },
                { "spawned", BridgeCommon.Try(() => target.Spawned, false) },
                { "hostileToPlayer", HostileToPlayer(target) },
                { "mentalState", pawn == null ? null : MentalStateOf(pawn) },
                { "downed", pawn == null ? (object)null : BridgeCommon.Try(() => pawn.Downed, false) },
                { "dead", pawn == null ? (object)null : BridgeCommon.Try(() => pawn.Dead, false) },
                { "predator", pawn == null ? (object)null : BridgeCommon.Try(() => pawn.RaceProps != null && pawn.RaceProps.predator, false) },
                { "position", BridgeCommon.PositionOf(target) },
                { "distance", distance },
                { "reachable", reachable },
                { "matchedBy", plan.TargetMatchedBy }
            };
        }

        private static object JobBlock(Plan plan, bool real)
        {
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "def", DefNameOf(plan.JobDef) },
                { "targetA", TargetInfoBlock(plan.TargetA) },
                { "targetB", TargetInfoBlock(plan.TargetB) },
                { "verb", plan.Verb == null ? null : BridgeCommon.SafeString(() => plan.Verb.ToString()) },
                { "mode", plan.ResolvedMode },
                { "killIncappedTarget", plan.KillIncappedTarget },
                { "draftedTend", plan.DraftedTend },
                { "count", plan.Count },
                { "expiryInterval", plan.ExpiryInterval },
                { "unforbade", plan.Unforbade },
                { "jobTag", plan.GiverDef == null ? "Misc" : BridgeCommon.SafeString(() => plan.GiverDef.tagToGive.ToString()) },
                { "workGiver", plan.GiverDef == null ? null : DefNameOf(plan.GiverDef) },
                { "workType", plan.WorkType == null ? null : DefNameOf(plan.WorkType) },
                { "billLabel", plan.BillLabel },
                { "tendPath", plan.TendPath },
                { "rescuePath", plan.RescuePath },
                { "issued", real && plan.Issued },
                { "verified", real && plan.Verified },
                { "verifiedReason", real ? plan.VerifiedReason : "dry run: nothing was issued, so nothing was read back." },
                { "note", JobNote(plan) }
            };
        }

        /// <summary>What a reader of this job should know that the fields do
        /// not say: where the shape came from, and the one place this tool
        /// deliberately does less than the float menu.</summary>
        private static string JobNote(Plan plan)
        {
            var note = "The job shape is FloatMenuUtility / FloatMenuOptionProvider_* verbatim; playerForced is set by "
                     + "Pawn_JobTracker.TryTakeOrderedJob itself. An expiryInterval of -1 is JobMaker's default and means "
                     + "vanilla sets none for this order.";
            if (plan.JobDef != null && string.Equals(DefNameOf(plan.JobDef), "Goto", StringComparison.Ordinal))
            {
                note += " exitMapOnArrival is deliberately NOT set, unlike the float menu: vanilla sets it when the clicked "
                      + "cell is on the exit grid, which would turn a mis-aimed move into a colonist leaving the map for good.";
            }
            if (plan.JobDef != null && string.Equals(DefNameOf(plan.JobDef), "AttackStatic", StringComparison.Ordinal))
            {
                note += " verb is REPORTED, not written onto the job: JobDriver_AttackStatic calls "
                      + "pawn.TryGetAttackVerb(target, !pawn.IsColonist) for itself on every run.";
            }
            note += " TryTakeOrderedJob checks KeyBindingDefOf.QueueOrder.IsDownEvent, so a physically held SHIFT key would "
                  + "queue this order behind the current one instead of replacing it; that is what verified is for.";
            return note;
        }

        private static object TargetInfoBlock(LocalTargetInfo info)
        {
            try
            {
                if (!info.IsValid)
                    return null;
                if (info.HasThing)
                {
                    return new Dictionary<string, object>(StringComparer.Ordinal)
                    {
                        { "thingId", BridgeCommon.SafeString(() => info.Thing.GetUniqueLoadID()) },
                        { "label", BridgeCommon.SafeString(() => info.Thing.LabelCap.ToString()) },
                        { "position", BridgeCommon.PositionOf(info.Thing) }
                    };
                }
                return new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    { "thingId", null },
                    { "label", null },
                    { "position", BridgeCommon.Pos(info.Cell) }
                };
            }
            catch { return null; }
        }

        private static List<object> CandidateBlocks(Plan plan)
        {
            var rows = new List<object>();
            foreach (var thing in plan.Candidates)
            {
                if (thing == null)
                    continue;
                var pawn = thing as Pawn;
                int? distance = null;
                if (plan.Pawn != null && BridgeCommon.Try(() => thing.Spawned && plan.Pawn.Spawned, false))
                {
                    distance = BridgeCommon.TryN(() =>
                        (int)Math.Round(Math.Sqrt(plan.Pawn.Position.DistanceToSquared(thing.Position))));
                }
                rows.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    { "thingId", BridgeCommon.SafeString(() => thing.GetUniqueLoadID()) },
                    { "idForms", IdForms(thing) },
                    { "name", pawn != null ? NameOf(pawn) : BridgeCommon.SafeString(() => thing.LabelShortCap.ToString()) },
                    { "defName", BridgeCommon.SafeString(() => thing.def == null ? null : thing.def.defName) },
                    { "position", BridgeCommon.PositionOf(thing) },
                    { "distance", distance }
                });
            }
            return rows;
        }

        private static object AfterBlock(Plan plan)
        {
            if (plan.Pawn == null)
                return null;
            var job = BridgeCommon.Try<Job>(() => plan.Pawn.CurJob, null);
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "drafted", BridgeCommon.Try(() => plan.Pawn.Drafted, false) },
                { "jobDef", job == null ? null : DefNameOf(job.def) },
                { "jobTarget", job == null ? null : TargetInfoBlock(job.targetA) },
                { "mentalState", MentalStateOf(plan.Pawn) }
            };
        }

        /// <summary>All four addresses of one thing, in the order the class
        /// remarks list them, so a caller that got a name learns the ids.</summary>
        private static List<object> IdForms(Thing thing)
        {
            var forms = new List<object>();
            var load = BridgeCommon.SafeString(() => thing.GetUniqueLoadID());
            var id = BridgeCommon.SafeString(() => thing.ThingID);
            var number = BridgeCommon.TryN(() => thing.thingIDNumber);
            var pawn = thing as Pawn;
            var label = pawn != null
                ? NameOf(pawn)
                : BridgeCommon.SafeString(() => thing.LabelShortCap.ToString());

            if (load != null) forms.Add(load);
            if (id != null) forms.Add(id);
            if (number != null) forms.Add(number.Value.ToString(CultureInfo.InvariantCulture));
            if (BridgeCommon.Try(() => thing.Spawned, false))
            {
                var defName = BridgeCommon.SafeString(() => thing.def == null ? null : thing.def.defName);
                var pos = BridgeCommon.Try(() => thing.Position, IntVec3.Invalid);
                if (!string.IsNullOrEmpty(defName) && pos.IsValid)
                    forms.Add(defName + "@" + pos.x.ToString(CultureInfo.InvariantCulture)
                              + "," + pos.z.ToString(CultureInfo.InvariantCulture));
            }
            if (!string.IsNullOrEmpty(label)) forms.Add(label);
            return forms;
        }

        private static string NameOf(Pawn pawn)
        {
            if (pawn == null)
                return null;
            var name = BridgeCommon.SafeString(() => pawn.Name == null ? null : pawn.Name.ToStringShort);
            if (!string.IsNullOrEmpty(name))
                return name;
            return BridgeCommon.SafeString(() => pawn.LabelShortCap.ToString());
        }

        private static string Describe(Thing thing)
        {
            if (thing == null)
                return "nothing";
            var label = BridgeCommon.SafeString(() => thing.LabelShortCap.ToString()) ?? "a thing";
            var id = BridgeCommon.SafeString(() => thing.GetUniqueLoadID());
            return id == null ? label : label + " (" + id + ")";
        }

        private static string DefNameOf(Def def)
        {
            return def == null ? null : BridgeCommon.SafeString(() => def.defName);
        }

        private static string Normalise(string s)
        {
            return s == null ? null : s.Trim().ToLowerInvariant();
        }

        private static object Failure(string message, string kind, string action)
        {
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "success", false },
                { "tool", ToolName },
                { "action", Normalise(action) },
                { "dryRun", false },
                { "applied", false },
                { "pawn", null },
                { "target", null },
                { "job", null },
                { "wouldIssue", null },
                { "candidates", new List<object>() },
                { "after", null },
                { "watch", Watch.Skipped("refused") },
                { "error", message },
                { "errorKind", kind }
            };
        }
    }
}
