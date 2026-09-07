using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using RimBridgeServer.Sdk;
using RimWorld;
using Verse;

namespace HomeBridge.BridgeTools
{
    /// <summary>
    /// home/building_config — the write side of `home/list_buildings`, the way
    /// `home/pawn_config` is the write side of `home/list_pawns`.
    ///
    /// One building is selected with `thing`, and the four inspect-pane toggles a
    /// player would click become fields: `forbidden` (CompForbiddable),
    /// `power` (CompFlickable's want-switch plus the flick designation),
    /// `medical` and `forPrisoners` (Building_Bed), and `owner`
    /// (CompAssignableToPawn's Set-owner dialog). `gizmos: true` lists what the
    /// selected thing's gizmo bar actually holds without firing any of it.
    /// Every write is a dry run until `dryRun:false`, and every `after` is read
    /// back from the game inside the same main-thread hop as the write.
    ///
    /// ## Verified against Assembly-CSharp 1.6.9676.17735
    ///
    ///   Verse.Thing.GetGizmos(), Verse.ThingWithComps.GetGizmos(),
    ///     Verse.Building.GetGizmos(), Verse.Gizmo.Disabled / .disabledReason,
    ///     Verse.Command.Label, Verse.Command_Toggle.isActive (Func&lt;bool&gt;)
    ///   RimWorld.CompForbiddable.Forbidden (property with side effects, below)
    ///   RimWorld.CompFlickable.SwitchIsOn / .WantsFlick() / private `wantSwitchOn`
    ///   RimWorld.FlickUtility.UpdateFlickDesignation(Thing),
    ///     RimWorld.DesignationDefOf.Flick
    ///   RimWorld.Building_Bed.Medical / .ForPrisoners / .ForOwnerType /
    ///     .ForHumanBabies / .OwnersForReading / .SleepingSlotsCount /
    ///     .RoomCanBePrisonCell(Room), BuildingProperties.bed_canBeMedical /
    ///     .bed_humanlike, RimWorld.BedOwnerType
    ///   RimWorld.CompAssignableToPawn.AssigningCandidates / .CanAssignTo /
    ///     .IdeoligionForbids / .TryAssignPawn / .TryUnassignPawn /
    ///     .AssignedPawnsForReading / .MaxAssignedPawnsCount
    ///   RimWorld.Pawn_Ownership.ClaimBedIfNonMedical / .UnclaimBed / .OwnedBed
    ///   Verse.RegionAndRoomQuery.GetRoom(Thing), Verse.Room.ProperRoom / .IsHuge
    ///
    /// ## Hazards, each pre-checked rather than caught
    ///
    /// ### Enumerating gizmos calls `Faction.OfPlayer`
    ///
    /// `Verse.Thing.GetGizmos()` opens with `foreach (Ideo ideo in
    /// Faction.OfPlayer.ideos.AllIdeos)`, and `Building.GetGizmos`,
    /// `Building_Bed.GetGizmos`, `CompForbiddable.CompGetGizmosExtra`,
    /// `CompFlickable.CompGetGizmosExtra` and
    /// `CompAssignableToPawn.ShouldShowAssignmentGizmo` each test
    /// `Faction.OfPlayer` again. `Faction.OfPlayer` is `OfPlayerSilentFail`
    /// followed by `Verse.Log.Error`, and `Log.Error` calls
    /// `TickManager.Pause()`. `CompAssignableToPawn_Bed.AssigningCandidates`
    /// reads it too, for an animal bed. So the whole tool refuses up front when
    /// `Faction.OfPlayerSilentFail` is null: there is no colony to configure on
    /// such a map, and asking would pause it.
    ///
    /// ### `Building_Bed.ForPrisoners = false` is a `Log.Error`
    ///
    /// The setter's else arm is literally `forOwnerType = BedOwnerType.Colonist;
    /// Log.Error("Bed ForPrisoners=false, but should it be for for colonists or
    /// slaves? Set ForOwnerType instead.")`. So `forPrisoners` is written
    /// through `ForOwnerType` in both directions, never through `ForPrisoners`,
    /// and a bed currently set for slaves is refused rather than silently
    /// demoted to a colonist bed.
    ///
    /// ### `FlickUtility.UpdateFlickDesignation` can open a modal dialog
    ///
    /// Its tail is `TutorUtility.DoModalDialogIfNotKnown(ConceptDefOf.
    /// SwitchFlickingDesignation)`, which adds a `Dialog_MessageBox` to the
    /// window stack the first time the concept is met and would leave a window
    /// standing on screen. The designation half of that method — the
    /// `WantsFlick()` sweep over `AllComps`, `DesignationOn`, `AddDesignation`
    /// or `Delete` — is mirrored here instead, so the flick designation is
    /// identical and no window opens. `AddDesignation` is only ever reached when
    /// `DesignationOn` returned null; a double add is a `Log.Error`.
    ///
    /// ### Assigning against an ideoligion is a `Log.Error`
    ///
    /// `Pawn_Ownership.ClaimBedIfNonMedical` ends with `if (pawn.IsFreeman &&
    /// newBed.CompAssignableToPawn.IdeoligionForbids(pawn)) Log.Error(...)`, so
    /// `IdeoligionForbids` is tested first and the assignment is refused. The
    /// game's own dialog draws "IdeoligionForbids" in place of the button there.
    ///
    /// ### The three writes that drop owners
    ///
    /// `Medical`'s setter, `ForOwnerType`'s setter and `ForPrisoners`'s setter
    /// all call `RemoveAllOwners()` first, which unclaims every owner and posts
    /// a "lost assignment" message per pawn. The field row names them in
    /// `ownersDropped[]`, on a dry run as well.
    ///
    /// ### What the assign dialog does on top of the click
    ///
    /// `CompAssignableToPawn_Bed.TryAssignPawn` is
    /// `pawn.ownership.ClaimBedIfNonMedical(bed)`, which unclaims the pawn's
    /// PREVIOUS bed and, when the bed is already full, evicts its last owner.
    /// Both are predicted into the field row (`unassignedFrom`, `evicts`) rather
    /// than discovered afterwards. `TryAssignPawn` / `TryUnassignPawn` are
    /// virtual and the throne, grave and bed comps all override them, so calling
    /// through the base type is correct for any assignable building.
    ///
    /// ### What is deliberately not mirrored
    ///
    /// `Building_Bed.SetBedOwnerTypeByInterface` — the prisoner gizmo's actual
    /// action — reads `Find.Selector.SelectedObjects`, cascades to every bed in
    /// the room, plays a sound and can raise a confirmation dialog. This tool
    /// changes the one bed it was given and says so in `notes`. The gizmos'
    /// sounds and `PlayerKnowledgeDatabase.KnowledgeDemonstrated` bookkeeping
    /// are skipped for the same reason `home/research` skips its sound.
    /// </summary>
    public sealed class HomeBuildingConfigTools
    {
        private const string ToolName = "home/building_config";

        /// <summary>Candidates listed when a selector is ambiguous. A refusal
        /// that prints two hundred rows is a refusal nobody reads.</summary>
        private const int MaxCandidates = 25;

        /// <summary>Gizmo rows listed per thing. The bar holds a dozen at most;
        /// the cap is there so a modded thing cannot produce a wall of them.</summary>
        private const int MaxGizmos = 60;

        /// <summary>`CompFlickable.wantSwitchOn` is private and has no public
        /// setter. The gizmo's toggleAction assigns it directly, so this is the
        /// only way to mirror the click; a null field is a refusal, never a
        /// silent write of the switch itself (which would flip the power with no
        /// colonist walking over).</summary>
        private static readonly FieldInfo WantSwitchOnField =
            BridgeCommon.PrivateInstanceField(typeof(CompFlickable), "wantSwitchOn");

        [Tool(
            ToolName,
            Title = "Read and configure a colony building",
            Description =
                "Write tool for one building, with dryRun defaulting to TRUE. `thing` picks it: a ThingID (Bed1234), a "
                + "\"DefName@x,z\" pair as home/list_buildings prints them, or a unique label/defName substring among colony "
                + "buildings -- an ambiguous name is REFUSED with the candidates and their thingIds. gizmos:true lists what the "
                + "gizmo bar holds (label, type, disabled, disabledReason, and isActive on a toggle) and fires nothing. The "
                + "writes are fields: forbidden (CompForbiddable), power on/off (CompFlickable "
                + "-- this places a FLICK DESIGNATION and a colonist walks over, it does not switch the power itself), medical "
                + "and forPrisoners on a bed, owner (a colonist name, or \"none\" to clear), and temperature on a building "
                + "with CompTempControl. Any other gizmo is out of "
                + "scope. Every field named gets a before and an after; on a real run the after is READ BACK from the game. A "
                + "value the game will not accept is REFUSED with the game's own reason -- a prisoner bed in a room that cannot "
                + "be a cell, a colonist who is not an assignment candidate, power on a thing with no switch -- and never "
                + "silently skipped. The matching READ is home/list_buildings.",
            ResultDescription =
                "success, tool, dryRun, applied, afterIsPredicted, thing{thingId,defName,label,position,faction,isBed}, "
                + "gizmos[] and gizmoCount (null unless gizmos:true), fields[] (field, requested, before, after, changed, "
                + "refused, reason, plus ownersDropped[]/unassignedFrom/evicts where a write moves something else), changed[], "
                + "refused[], before{} and after{} (the whole writable configuration: forbidden, the flick trio, medical, "
                + "forPrisoners, bedOwnerType, assignedPawns[]), options{assigningCandidates,maxAssignedPawns,roomCanBePrisonCell}, "
                + "watch{}, notes{}, and candidates[] on an ambiguous selector.")]
        [ToolResponse("thing", "object", "The building this call resolved to: thingId, defName, label, position{x,z}, faction, isBed. Present on every successful call; a selector that matched nothing or too many is a refusal instead.", Always = true)]
        [ToolResponse("gizmos", "array", "Only when gizmos:true. One row per gizmo the game would draw on this thing's bar: label (null for a gizmo that is not a Command), type (the class name), disabled, disabledReason, isActive (only a Command_Toggle has one; null everywhere else). Reading this fires nothing. NULL when it was not asked for.", Nullable = true)]
        [ToolResponse("gizmoCount", "number", "How many gizmos the bar holds, including any past the listing cap. NULL when gizmos was not asked for, so an absent list is never read as an empty bar.", Nullable = true)]
        [ToolResponse("fields", "array", "One row per field the caller named: field, requested, before, after, changed, refused, reason. A field absent here was never asked for; a refused one is here AND in refused[], never in changed[].", Always = true)]
        [ToolResponse("refused", "array", "Every field this tool would not write, with the reason. Empty means nothing was refused -- a refusal is never a silent skip.", Always = true)]
        [ToolResponse("before", "object", "The whole writable configuration as it was: forbiddable/forbidden, flickable/wantSwitchOn/switchIsOn/flickDesignated, hasPower/powered/connected, isBed/medical/canBeMedical/forPrisoners/bedOwnerType/humanlikeBed, assignable/assignedPawns[]/maxAssignedPawns. A capability the thing does not have reads false with its values null.", Always = true)]
        [ToolResponse("after", "object", "The same block after the writes. On a real run it is READ BACK from the game, so a write the game declined shows as a mismatch; on a dry run it equals before and afterIsPredicted is true.", Always = true)]
        [ToolResponse("options", "object", "What a write may be handed: assigningCandidates[] (the pawns the game's own Set-owner dialog would list, each with its thingId), assigningCandidateCount, maxAssignedPawns, roomCanBePrisonCell and roomNote. Never null.", Always = true)]
        [ToolResponse("dryRun", "boolean", "True = nothing was written. Defaults to TRUE; a caller must pass dryRun:false deliberately.", Always = true)]
        [ToolResponse("applied", "boolean", "True only when at least one field was actually written to the game. False on every dry run, every refusal, and every gizmos-only read.", Always = true)]
        [ToolResponse("watch", "object", "What a viewer was shown while the write landed: shown, and either the selection and tab that were opened or the reason nothing was. A dry run, a refusal or watch:false is shown:false with the reason.", Always = true)]
        [ToolResponse("candidates", "array", "Only on an ambiguous selector: every colony building the name matched, with thingId, defName, label and position, so the next call can be exact. NULL on every other reply.", Nullable = true)]
        [ToolResponse("unknownArguments", "array", "Every argument key the caller sent that this tool does not declare, sorted, case-sensitively. Empty array = every key was recognised. On a WRITE tool this matters twice over: a misspelled dryRun is the difference between a plan and a changed building. The host's own _rimBridgeTimeoutMs is never listed.", Always = true)]
        [ToolResponse("unknownArgumentsWarning", "string", "Present only when unknownArguments is non-empty, or when the caller's raw keys could not be read at all - in which case the empty unknownArguments means 'not known', not 'nothing unknown'.", Nullable = true)]
        public async Task<object> BuildingConfig(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            [ToolParameter(Description = "Which building: a ThingID (Bed1234, or Thing_Bed1234), the bare thingIDNumber build.py prints (1234), a bare x,z cell, a \"DefName@x,z\" pair (Bed@62,141), or a unique label/defName substring among colony buildings. A cell matches any cell the building OCCUPIES, not just its anchor. An ambiguous selector is REFUSED with candidates[].")] string thing = null,
            [ToolParameter(Description = "List the gizmos the game would draw on this thing's bar -- label, class name, disabled, disabledReason, and the current isActive of every toggle. Nothing is fired. Off by default.", DefaultValue = false)] bool gizmos = false,
            [ToolParameter(Description = "WRITE: forbid (true) or allow (false) this thing, the Allow/Forbid toggle. Omit to leave it alone. Refused on a thing with no CompForbiddable.")] bool? forbidden = null,
            [ToolParameter(Description = "WRITE: \"on\" or \"off\" -- the power switch. This does what clicking it does: it sets the want-switch and places a FLICK DESIGNATION, and a colonist has to walk over and flick it. switchIsOn and powered do NOT change until then, and the reply reports all three honestly. Refused on a thing with no CompFlickable.")] string power = null,
            [ToolParameter(Description = "WRITE: target temperature in Celsius for a cooler, heater, or other building with CompTempControl. Omit to leave it alone. Refused outside the game's -273.15 to 1000 C interface range or on a thing without temperature controls.")] float? temperature = null,
            [ToolParameter(Description = "WRITE: make this bed a medical bed (true) or an ordinary one (false). Omit to leave it alone. Changing it in EITHER direction drops every owner the bed has (RimWorld's own setter does), and ownersDropped[] names them. Refused on a non-bed and on a bed whose def cannot be medical.")] bool? medical = null,
            [ToolParameter(Description = "WRITE: assign or clear this thing's owner. A colonist name (or ThingID) assigns, \"none\" unassigns everyone. Mirrors the Set-owner dialog: a pawn the game would not list, one it would grey out, or one an ideoligion forbids is REFUSED with the reason. Assigning also unclaims the pawn's previous bed and, on a full bed, evicts its last owner -- both named in the field row. Refused on a medical or prisoner bed, where the game draws no such button.")] string owner = null,
            [ToolParameter(Description = "WRITE: make this bed a prisoner bed (true) or a colonist bed (false). Omit to leave it alone. Setting it true is refused when the room cannot be a prison cell, with the game's own reason; either direction drops the bed's owners. Refused on a non-humanlike bed, a crib, and a bed currently set for slaves (that would silently make it a colonist bed).")] bool? forPrisoners = null,
            [ToolParameter(Description = "TRUE by default. Resolve the thing, check every field and report what WOULD happen without touching the game. Pass false to actually apply it.", DefaultValue = true)] bool dryRun = true,
            [ToolParameter(Description = "On a real write, select the building and move the camera to it before the change lands, then clear the selection. Decorative only: the write is the same either way. Ignored on a dry run, a refusal and a gizmos-only read.", DefaultValue = true)] bool watch = true,
            [ToolParameter(Description = "How long the selection stays up after the write, in seconds.", DefaultValue = 8)] int watchSeconds = 8)
        {
            return BridgeCommon.WithUnknownArguments(
                await BuildingConfigCore(ctx, cancellationToken, thing, gizmos, forbidden, power, temperature,
                                         medical, owner, forPrisoners, dryRun, watch, watchSeconds)
                    .ConfigureAwait(false),
                ctx, typeof(HomeBuildingConfigTools), ToolName);
        }

        private async Task<object> BuildingConfigCore(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            string thing,
            bool gizmos,
            bool? forbidden,
            string power,
            float? temperature,
            bool? medical,
            string owner,
            bool? forPrisoners,
            bool dryRun,
            bool watch,
            int watchSeconds)
        {
            if (ctx?.MainThread == null)
                return Failure("No RimBridge main-thread dispatcher is available for this invocation.");

            // Arguments are parsed out here, before the hop, per the house rule.
            var spec = string.IsNullOrEmpty(thing) ? null : thing.Trim();
            if (spec != null && spec.Length == 0)
                spec = null;
            var powerSpec = string.IsNullOrEmpty(power) ? null : power.Trim();
            var ownerSpec = string.IsNullOrEmpty(owner) ? null : owner.Trim();
            var seconds = watchSeconds < 1 ? 1 : (watchSeconds > 60 ? 60 : watchSeconds);

            // Hop 1: resolve, read `before`, validate every field, and -- only
            // when a real write survived -- open the watch. It returns the
            // finished payload for every other case, because building that
            // payload is itself a pile of game reads.
            var stage = await ctx.MainThread
                .InvokeAsync(() => Stage(ctx, spec, gizmos, forbidden, powerSpec, temperature, medical, ownerSpec,
                                         forPrisoners, dryRun, watch),
                             cancellationToken)
                .ConfigureAwait(false);

            if (stage == null)
                return Failure("The staging pass returned nothing, which should not happen.");
            if (stage.Payload != null)
                return stage.Payload;

            // Off the main thread: let the open selection be seen before the
            // change lands inside it.
            await Watch.Lead(stage.Session, cancellationToken).ConfigureAwait(false);

            // Hop 2: apply, read back, close the watch. Read-write-read-back is
            // one hop for the same reason home/pawn_config gives -- a tick in
            // between would make `after` answer a different question.
            return await ctx.MainThread
                .InvokeAsync(() => Apply(stage, seconds), cancellationToken)
                .ConfigureAwait(false);
        }

        // ================================================================ state

        /// <summary>What hop 1 hands hop 2. Either a finished Payload (nothing
        /// to write) or the target, its plans and the open watch session.</summary>
        private sealed class Stage2
        {
            internal Dictionary<string, object> Payload;
            internal Thing Target;
            internal string ThingId;
            internal Map Map;
            internal List<FieldPlan> Plans;
            internal List<object> Gizmos;
            internal int? GizmoCount;
            internal Watch.Session Session;
            internal bool Watched;
        }

        /// <summary>One requested field: what it was asked, what it was, what it
        /// would become, and the closure that writes it. Extras are the keys a
        /// particular field owes its row (ownersDropped, evicts, ...); they are
        /// predictions computed from `before` and read the same on a dry run and
        /// a real one.</summary>
        private sealed class FieldPlan
        {
            internal string Field;
            internal object Requested;
            internal object Before;
            internal object Predicted;
            internal bool Refused;
            internal string Reason;
            internal string Note;
            internal Dictionary<string, object> Extras = new Dictionary<string, object>(StringComparer.Ordinal);
            /// <summary>The whole config block as it stood when this field was
            /// planned, so `before{}` is the state the plan was made against
            /// rather than a second read taken after the writes.</summary>
            internal Dictionary<string, object> Snapshot;
            internal Action<Thing> Write;
            internal Func<Thing, object> ReadBack;
        }

        // ================================================================= hop 1

        private static Stage2 Stage(IRimBridgeContext ctx, string spec, bool wantGizmos,
                                    bool? forbidden, string powerSpec, float? temperature, bool? medical,
                                    string ownerSpec, bool? forPrisoners,
                                    bool dryRun, bool watch)
        {
            var stage = new Stage2();

            Map map;
            string mapError;
            if (!BridgeCommon.TryGetMap(ToolName, out map, out mapError))
            {
                stage.Payload = Failure(mapError);
                return stage;
            }

            // The gizmo enumeration and the assignment candidates both reach
            // Faction.OfPlayer inside RimWorld's own code, and its failure path
            // pauses the colony. No player faction means no colony building to
            // configure, so this is a refusal rather than a guard.
            var player = PawnSettingsRead.PlayerFactionSilent();
            if (player == null)
            {
                stage.Payload = Failure(
                    "This map has no player faction, and every read this tool needs -- Thing.GetGizmos() and "
                    + "CompAssignableToPawn.AssigningCandidates -- calls Faction.OfPlayer internally, whose failure path "
                    + "calls Verse.Log.Error and pauses the game. Nothing was read.");
                return stage;
            }

            var colony = ColonyBuildings(map, player);
            Thing target;
            string resolveError;
            List<object> candidates;
            if (!TryResolveThing(colony, spec, out target, out resolveError, out candidates))
            {
                var failure = Failure(resolveError);
                if (candidates != null)
                    failure["candidates"] = candidates;
                stage.Payload = failure;
                return stage;
            }

            stage.Target = target;
            stage.Map = map;
            stage.ThingId = BridgeCommon.SafeString(() => target.ThingID);

            if (wantGizmos)
            {
                int total;
                stage.Gizmos = GizmoRows(target, out total);
                stage.GizmoCount = total;
            }

            var plans = new List<FieldPlan>();
            if (forbidden != null)
                plans.Add(PlanForbidden(target, forbidden.Value));
            if (powerSpec != null)
                plans.Add(PlanPower(target, powerSpec));
            if (temperature != null)
                plans.Add(PlanTemperature(target, temperature.Value));
            if (medical != null)
                plans.Add(PlanMedical(target, medical.Value));
            if (forPrisoners != null)
                plans.Add(PlanForPrisoners(target, forPrisoners.Value));
            if (ownerSpec != null)
                plans.Add(PlanOwner(target, ownerSpec));
            stage.Plans = plans;

            var writable = plans.Count(p => !p.Refused && p.Write != null);
            var realWrite = !dryRun && writable > 0;

            if (!realWrite)
            {
                // Nothing reaches hop 2, so nothing is shown. The three ways
                // that happens each get their own word: a read, a plan, and a
                // call where every field was refused. watch:false is said in
                // hop 2 instead, because that call DOES write.
                string reason;
                if (plans.Count == 0)
                    reason = wantGizmos ? "read only: no field was asked for" : "no field was asked for";
                else if (dryRun)
                    reason = "dry run";
                else
                    reason = "refused";

                stage.Payload = Payload(target, map, plans, false, dryRun, stage.Gizmos, stage.GizmoCount,
                                        Watch.Skipped(reason), null);
                return stage;
            }

            // The building toggle a player would use is on the inspect pane of
            // the selected building: select it, move the camera, no tab.
            if (watch)
            {
                stage.Session = Watch.Open(ctx, target, null, null, true);
                stage.Watched = true;
            }
            return stage;
        }

        // ================================================================= hop 2

        private static object Apply(Stage2 stage, int watchSeconds)
        {
            // Re-resolved rather than trusted across the gap. The game is
            // normally paused here, so a miss means something genuinely moved.
            var map = stage.Map;
            var player = PawnSettingsRead.PlayerFactionSilent();
            Thing target = null;
            if (player != null)
            {
                var colony = ColonyBuildings(map, player);
                target = colony.FirstOrDefault(t =>
                    string.Equals(BridgeCommon.SafeString(() => t.ThingID), stage.ThingId, StringComparison.Ordinal));
            }

            var watchBlock = stage.Session == null
                ? Watch.Skipped("watch:false")
                : Watch.Finish(stage.Session, watchSeconds);

            if (target == null || !ReferenceEquals(target, stage.Target))
            {
                var failure = Failure(
                    "The building " + (stage.ThingId ?? "?") + " was no longer where it was when this call started, so "
                    + "nothing was written. Read it again with home/list_buildings.");
                failure["watch"] = watchBlock;
                return failure;
            }

            var applied = false;
            foreach (var plan in stage.Plans)
            {
                if (plan.Refused || plan.Write == null)
                    continue;
                try
                {
                    plan.Write(target);
                    applied = true;
                }
                catch (Exception ex)
                {
                    plan.Refused = true;
                    plan.Reason = "Writing " + plan.Field + " threw " + ex.GetType().Name + ". Nothing was written for it.";
                }
            }

            return Payload(target, map, stage.Plans, applied, false, stage.Gizmos, stage.GizmoCount,
                           watchBlock, null);
        }

        // =============================================================== payload

        private static Dictionary<string, object> Payload(
            Thing target, Map map, List<FieldPlan> plans, bool applied, bool dryRun,
            List<object> gizmos, int? gizmoCount, Dictionary<string, object> watchBlock,
            List<object> candidates)
        {
            // On a real run every `after` is a fresh read of the game; on a dry
            // run nothing moved, so `after` is `before` and each row carries its
            // own predicted value.
            var after = Config(target);

            var fields = new List<object>();
            var changed = new List<object>();
            var refused = new List<object>();

            foreach (var plan in plans)
            {
                var row = new Dictionary<string, object>
                {
                    { "field", plan.Field },
                    { "requested", plan.Requested },
                    { "before", plan.Before },
                    { "after", plan.Refused ? plan.Before : (dryRun ? plan.Predicted : ReadBack(plan, target)) },
                    { "changed", false },
                    { "refused", plan.Refused },
                    { "reason", plan.Reason }
                };
                foreach (var pair in plan.Extras)
                    row[pair.Key] = pair.Value;
                if (plan.Note != null)
                    row["note"] = plan.Note;

                var isChanged = !plan.Refused && !Equals(row["before"], row["after"]);
                row["changed"] = isChanged;

                fields.Add(row);
                if (plan.Refused)
                    refused.Add(row);
                else if (isChanged)
                    changed.Add(plan.Field + ": " + Show(row["before"]) + " -> " + Show(row["after"]));
            }

            var payload = new Dictionary<string, object>
            {
                { "success", true },
                { "tool", ToolName },
                { "dryRun", dryRun },
                { "applied", applied },
                { "afterIsPredicted", dryRun },
                { "thing", ThingRow(target) },
                { "gizmos", gizmos },
                { "gizmoCount", gizmoCount },
                { "fields", fields },
                { "fieldCount", fields.Count },
                { "changed", changed },
                { "changeCount", changed.Count },
                { "refused", refused },
                { "refusedCount", refused.Count },
                { "before", BeforeBlock(plans, target) },
                { "after", after },
                { "options", Options(target) },
                { "candidates", candidates },
                { "watch", watchBlock },
                { "notes", Notes(dryRun) }
            };
            return payload;
        }

        /// <summary>`before` is the whole block as it stood when the fields were
        /// planned. It is captured once, at plan time, on the first plan that
        /// carries it; with no field asked for there was nothing to change and
        /// the current read is the honest answer for both sides.</summary>
        private static Dictionary<string, object> BeforeBlock(List<FieldPlan> plans, Thing target)
        {
            foreach (var plan in plans)
                if (plan.Snapshot != null)
                    return plan.Snapshot;
            return Config(target);
        }

        private static object ReadBack(FieldPlan plan, Thing target)
        {
            if (plan.ReadBack == null)
                return plan.Predicted;
            try { return plan.ReadBack(target); }
            catch { return null; }
        }

        private static Dictionary<string, object> ThingRow(Thing thing)
        {
            return new Dictionary<string, object>
            {
                { "thingId", BridgeCommon.SafeString(() => thing.ThingID) },
                { "defName", BridgeCommon.SafeString(() => thing.def.defName) },
                { "label", BridgeCommon.SafeString(() => thing.LabelCap.ToString()) },
                { "position", BridgeCommon.PositionOf(thing) },
                { "faction", BridgeCommon.SafeString(() => thing.Faction == null ? null : thing.Faction.Name) },
                { "isBed", thing is Building_Bed }
            };
        }

        private static Dictionary<string, object> Notes(bool dryRun)
        {
            return new Dictionary<string, object>
            {
                { "dryRunMeaning", dryRun
                    ? "NOTHING WAS WRITTEN. after{} is identical to before{}; each fields[] row carries the value the write WOULD produce. Run again with dryRun:false to apply."
                    : "Applied. after{} was READ BACK from the game after the writes -- it is not an echo of what was requested, so a field whose after does not match its request was declined by the game." },
                { "refusalsAreAnswers", "A field this tool will not write appears in refused[] with a reason and does NOT appear in changed[]. Nothing is ever silently skipped; a field absent from fields[] was never asked for." },
                { "powerIsADesignation", "power= sets the want-switch and places a Flick designation, which is exactly what clicking the gizmo does. A colonist then walks over and flicks it: switchIsOn and powered are unchanged until they do, and this reply never claims otherwise." },
                { "bedOwnersAreDropped", "medical and forPrisoners both clear every owner the bed has, in either direction -- RimWorld's own setters call RemoveAllOwners() first. The field row's ownersDropped[] names them." },
                { "oneBedOnly", "The game's prisoner toggle cascades to every bed in the room and can raise a confirmation dialog. This tool changes the ONE bed it was given, with no cascade and no dialog." },
                { "scope", "The write surface is forbidden, power, temperature, medical/forPrisoners and owner. Anything else on the bar -- deconstruct, install, build copy, rituals -- is out of scope and is only ever LISTED, by gizmos:true." },
                { "readSide", "The matching read is home/list_buildings; before{} and after{} here are the writable half of a building's row." }
            };
        }

        private static Dictionary<string, object> Options(Thing thing)
        {
            var options = new Dictionary<string, object>();
            var assign = Comp<CompAssignableToPawn>(thing);

            var candidates = new List<object>();
            if (assign != null)
            {
                foreach (var pawn in Candidates(assign))
                    candidates.Add(PawnRow(pawn));
            }
            options["assigningCandidates"] = candidates;
            options["assigningCandidateCount"] = candidates.Count;
            options["maxAssignedPawns"] = assign == null ? (object)null : BridgeCommon.TryN(() => assign.MaxAssignedPawnsCount);

            var bed = thing as Building_Bed;
            var room = bed == null ? null : BridgeCommon.Try<Room>(() => RegionAndRoomQuery.GetRoom(bed), null);
            options["roomCanBePrisonCell"] = bed == null || room == null
                ? (object)null
                : BridgeCommon.TryN(() => Building_Bed.RoomCanBePrisonCell(room));
            options["roomNote"] = bed == null
                ? "Not a bed, so no prison-cell question applies."
                : (room == null
                    ? "This bed is in no room the game will name (unroofed, or touching the map edge), so roomCanBePrisonCell is null and forPrisoners:true is refused."
                    : "Building_Bed.RoomCanBePrisonCell is the gizmo's own test: the room must be a proper room (roofed, not touching the map edge) and not huge.");
            return options;
        }

        // ================================================================ gizmos

        /// <summary>
        /// Every gizmo the bar would draw, read and never fired. The enumeration
        /// is lazy and each element is produced by another mod's or the game's
        /// own iterator, so it is stepped one element at a time behind a guard:
        /// a getter that throws ends the list with a note instead of failing the
        /// call.
        /// </summary>
        private static List<object> GizmoRows(Thing thing, out int total)
        {
            var rows = new List<object>();
            total = 0;

            IEnumerator<Gizmo> walker;
            try
            {
                var sequence = thing.GetGizmos();
                walker = sequence == null ? null : sequence.GetEnumerator();
            }
            catch (Exception ex)
            {
                rows.Add(new Dictionary<string, object>
                {
                    { "label", null },
                    { "type", null },
                    { "disabled", null },
                    { "disabledReason", "Thing.GetGizmos() threw " + ex.GetType().Name + "; no gizmo could be listed." },
                    { "isActive", null }
                });
                return rows;
            }

            if (walker == null)
                return rows;

            while (true)
            {
                Gizmo gizmo;
                try
                {
                    if (!walker.MoveNext())
                        break;
                    gizmo = walker.Current;
                }
                catch (Exception ex)
                {
                    rows.Add(new Dictionary<string, object>
                    {
                        { "label", null },
                        { "type", null },
                        { "disabled", null },
                        { "disabledReason", "The gizmo enumeration threw " + ex.GetType().Name + " after " + total
                                            + " gizmo(s); the rest of the bar is not listed." },
                        { "isActive", null }
                    });
                    break;
                }

                total++;
                if (gizmo == null || rows.Count >= MaxGizmos)
                    continue;

                var command = gizmo as Command;
                var toggle = gizmo as Command_Toggle;
                rows.Add(new Dictionary<string, object>
                {
                    { "label", command == null ? null : BridgeCommon.SafeString(() => command.Label) },
                    { "type", BridgeCommon.SafeString(() => gizmo.GetType().Name) },
                    { "disabled", BridgeCommon.TryN(() => gizmo.Disabled) },
                    { "disabledReason", BridgeCommon.SafeString(() => gizmo.disabledReason) },
                    // Command_Toggle.isActive is the checkbox the bar draws. It
                    // is a Func<bool> the game evaluates every frame, so reading
                    // it here is what the bar itself does, not an activation.
                    { "isActive", toggle == null || toggle.isActive == null
                        ? (object)null
                        : BridgeCommon.TryN(() => toggle.isActive()) }
                });
            }

            return rows;
        }

        // ============================================================ the fields

        private static FieldPlan PlanForbidden(Thing thing, bool wanted)
        {
            var plan = NewPlan("forbidden", wanted, thing);
            var comp = Comp<CompForbiddable>(thing);
            if (comp == null)
                return Refuse(plan, null, "This thing has no CompForbiddable, so the game draws no Allow/Forbid toggle on it.");

            var before = BridgeCommon.TryN(() => comp.Forbidden);
            plan.Before = before;
            plan.Predicted = wanted;
            plan.ReadBack = t => BridgeCommon.TryN(() => comp.Forbidden);
            if (before != null && before.Value == wanted)
                plan.Note = "Already " + (wanted ? "forbidden" : "allowed") + "; the setter early-returns on an unchanged value, so this writes nothing.";
            // The property setter IS the gizmo's toggleAction: it notifies
            // listerHaulables and listerMergeables, clears a door's reachability
            // cache and updates the forbidden overlay. Assigning it directly is
            // the whole of what the click does, minus the tutorial bookkeeping.
            plan.Write = t => { comp.Forbidden = wanted; };
            return plan;
        }

        private static FieldPlan PlanPower(Thing thing, string spec)
        {
            var plan = NewPlan("power", spec, thing);
            bool wanted;
            if (!TryParseOnOff(spec, out wanted))
                return Refuse(plan, null, "\"" + spec + "\" is not on/off. Accepted: on, off, true, false, yes, no, 1, 0.");

            plan.Requested = wanted ? "on" : "off";
            var comp = Comp<CompFlickable>(thing);
            if (comp == null)
                return Refuse(plan, null, "This thing has no CompFlickable, so it has no power switch to set. home/list_buildings' power{} block says whether it draws power at all.");
            if (WantSwitchOnField == null)
                return Refuse(plan, null, "CompFlickable.wantSwitchOn was not found by reflection; it is private with no setter, so the click cannot be mirrored and nothing was written.");
            if (!BridgeCommon.Try(() => thing.Spawned, false) || BridgeCommon.Try<Map>(() => thing.Map, null) == null)
                return Refuse(plan, null, "This thing is not spawned on a map, and the flick designation is placed on the map's designation manager.");

            var before = FlickState(thing, comp);
            plan.Before = before;
            plan.Predicted = FlickPredicted(comp, wanted);
            plan.ReadBack = t => FlickState(t, comp);

            var wantBefore = WantSwitchOn(comp);
            if (wantBefore != null && wantBefore.Value == wanted)
                plan.Note = "The want-switch is already " + (wanted ? "on" : "off") + "; nothing was placed.";
            else
                plan.Note = "A FLICK DESIGNATION is what this places. A colonist has to walk over and flick the switch before switchIsOn and powered follow.";

            plan.Write = t =>
            {
                WantSwitchOnField.SetValue(comp, wanted);
                UpdateFlickDesignation(t);
            };
            return plan;
        }

        private static FieldPlan PlanTemperature(Thing thing, float wanted)
        {
            var plan = NewPlan("temperature", wanted, thing);
            var comp = Comp<CompTempControl>(thing);
            if (comp == null)
                return Refuse(plan, null, "This thing has no CompTempControl, so it has no temperature setpoint.");
            if (float.IsNaN(wanted) || float.IsInfinity(wanted) || wanted < -273.15f || wanted > 1000f)
                return Refuse(plan, BridgeCommon.TryN(() => comp.targetTemperature),
                    "Temperature must be between -273.15 and 1000 C, the range used by RimWorld's temperature controls.");

            var before = BridgeCommon.TryN(() => comp.targetTemperature);
            plan.Before = before;
            plan.Predicted = wanted;
            plan.ReadBack = t => BridgeCommon.TryN(() => Comp<CompTempControl>(t).targetTemperature);
            if (before != null && Math.Abs(before.Value - wanted) < 0.001f)
                plan.Note = "The target temperature is already " + wanted + " C; nothing changes.";
            plan.Write = t => { Comp<CompTempControl>(t).targetTemperature = wanted; };
            return plan;
        }

        private static FieldPlan PlanMedical(Thing thing, bool wanted)
        {
            var plan = NewPlan("medical", wanted, thing);
            var bed = thing as Building_Bed;
            if (bed == null)
                return Refuse(plan, null, "This is not a bed, so it has no medical toggle.");

            var before = BridgeCommon.TryN(() => bed.Medical);
            plan.Before = before;
            plan.Predicted = wanted;
            plan.ReadBack = t => BridgeCommon.TryN(() => ((Building_Bed)t).Medical);

            if (wanted && !BedFlag(bed, "bed_canBeMedical"))
                return Refuse(plan, before, "This bed's def has bed_canBeMedical false, so the game draws no medical toggle and its setter would silently ignore the write.");

            if (before != null && before.Value == wanted)
            {
                plan.Note = "Already " + (wanted ? "a medical bed" : "an ordinary bed") + "; the setter early-returns, so no owner is dropped.";
                plan.Extras["ownersDropped"] = new List<object>();
            }
            else
            {
                var owners = OwnerNames(bed);
                plan.Extras["ownersDropped"] = owners;
                if (owners.Count > 0)
                    plan.Note = "Changing this drops the bed's owner(s): RimWorld's setter calls RemoveAllOwners() first and posts a 'lost assignment' message for each.";
            }

            plan.Write = t => { ((Building_Bed)t).Medical = wanted; };
            return plan;
        }

        private static FieldPlan PlanForPrisoners(Thing thing, bool wanted)
        {
            var plan = NewPlan("forPrisoners", wanted, thing);
            var bed = thing as Building_Bed;
            if (bed == null)
                return Refuse(plan, null, "This is not a bed, so it has no prisoner toggle.");

            var before = BridgeCommon.TryN(() => bed.ForPrisoners);
            plan.Before = before;
            plan.Predicted = wanted;
            plan.ReadBack = t => BridgeCommon.TryN(() => ((Building_Bed)t).ForPrisoners);
            plan.Extras["bedOwnerTypeBefore"] = BridgeCommon.SafeString(() => bed.ForOwnerType.ToString());

            if (!BedFlag(bed, "bed_humanlike"))
                return Refuse(plan, before, "This bed's def has bed_humanlike false (an animal bed), and the game draws the prisoner toggle only on humanlike beds.");
            if (BridgeCommon.Try(() => bed.ForHumanBabies, false))
                return Refuse(plan, before, "This is a crib (bed_maxBodySize below a child's), and the game draws no prisoner toggle on one.");
            if (!wanted && BridgeCommon.Try(() => bed.ForSlaves, false))
                return Refuse(plan, before, "This bed is currently set for SLAVES. forPrisoners:false would make it a colonist bed, which is not what was asked -- RimWorld's own ForPrisoners setter logs an error for exactly this ambiguity. Change the owner type in the game's dropdown.");

            if (wanted)
            {
                var room = BridgeCommon.Try<Room>(() => RegionAndRoomQuery.GetRoom(bed), null);
                if (room == null)
                    return Refuse(plan, before, "This bed is in no room, so it cannot be a prison cell. " + PrisonerFailReason());
                if (!BridgeCommon.Try(() => Building_Bed.RoomCanBePrisonCell(room), false))
                    return Refuse(plan, before, PrisonerFailReason()
                        + " (Building_Bed.RoomCanBePrisonCell: the room must be a proper room -- roofed and not touching the map edge -- and not huge.)");
            }

            if (before != null && before.Value == wanted)
            {
                plan.Note = "Already " + (wanted ? "a prisoner bed" : "not a prisoner bed") + "; the owner type does not move, so no owner is dropped.";
                plan.Extras["ownersDropped"] = new List<object>();
            }
            else
            {
                plan.Extras["ownersDropped"] = OwnerNames(bed);
                plan.Note = "Written through Building_Bed.ForOwnerType, never ForPrisoners: that setter's false arm is a Verse.Log.Error, which pauses the colony. Changing the owner type drops every owner.";
            }

            var target = wanted ? BedOwnerType.Prisoner : BedOwnerType.Colonist;
            plan.Write = t => { ((Building_Bed)t).ForOwnerType = target; };
            return plan;
        }

        private static FieldPlan PlanOwner(Thing thing, string spec)
        {
            var plan = NewPlan("owner", spec, thing);
            var comp = Comp<CompAssignableToPawn>(thing);
            var bed = thing as Building_Bed;

            var before = AssignedRows(comp);
            plan.Before = before;
            plan.ReadBack = t => AssignedRows(Comp<CompAssignableToPawn>(t));

            if (comp == null)
                return Refuse(plan, before, "This thing has no CompAssignableToPawn, so it has no owner to set.");
            if (bed != null && BridgeCommon.Try(() => bed.Medical, false))
                return Refuse(plan, before, "This is a medical bed. The game hides the Set-owner button on one, and Pawn_Ownership.ClaimBedIfNonMedical refuses a medical bed outright.");
            if (bed != null && BridgeCommon.Try(() => bed.ForPrisoners, false))
                return Refuse(plan, before, "This is a prisoner bed, and the game draws no Set-owner button on one.");

            // "none" is every Unassign row in the dialog, clicked.
            if (string.Equals(spec, "none", StringComparison.OrdinalIgnoreCase))
            {
                plan.Requested = "none";
                plan.Predicted = new List<object>();
                var current = AssignedPawns(comp);
                if (current.Count == 0)
                    plan.Note = "Nobody is assigned; nothing was written.";
                plan.Write = t =>
                {
                    var live = Comp<CompAssignableToPawn>(t);
                    if (live == null)
                        return;
                    foreach (var pawn in AssignedPawns(live))
                        live.TryUnassignPawn(pawn);
                };
                return plan;
            }

            var candidates = Candidates(comp);
            Pawn chosen;
            string error;
            if (!TryResolvePawn(candidates, spec, out chosen, out error))
                return Refuse(plan, before, error);

            plan.Requested = BridgeCommon.SafeString(() => chosen.LabelShortCap.ToString());

            if (AssignedPawns(comp).Any(p => ReferenceEquals(p, chosen)))
            {
                plan.Predicted = before;
                plan.Note = "Already assigned to this thing; nothing was written.";
                plan.Write = t => { };
                return plan;
            }

            var report = BridgeCommon.Try(() => comp.CanAssignTo(chosen), AcceptanceReport.WasAccepted);
            if (!BridgeCommon.Try(() => report.Accepted, true))
                return Refuse(plan, before, "The game will not assign " + Show(plan.Requested) + " to this: "
                    + (BridgeCommon.SafeString(() => report.Reason) ?? "CanAssignTo rejected it with no reason given.")
                    + " That is the greyed-out row in the Set-owner dialog.");

            // ClaimBedIfNonMedical's tail is `if (pawn.IsFreeman &&
            // IdeoligionForbids(pawn)) Log.Error(...)`, and the dialog draws
            // "IdeoligionForbids" instead of a button. Pre-checked, not caught.
            if (BridgeCommon.Try(() => comp.IdeoligionForbids(chosen), false))
                return Refuse(plan, before, Show(plan.Requested) + "'s ideoligion forbids this assignment. The game's own dialog draws no button on that row, and assigning anyway is a Verse.Log.Error.");

            var predicted = new List<object>(before);
            predicted.Add(PawnRow(chosen));
            plan.Predicted = predicted;

            var previous = BridgeCommon.Try<Building_Bed>(
                () => chosen.ownership == null ? null : chosen.ownership.OwnedBed, null);
            if (previous != null && !ReferenceEquals(previous, thing))
                plan.Extras["unassignedFrom"] = ThingRow(previous);

            // A full bed evicts its LAST owner, which is what the dialog does
            // too; naming it up front is the difference between a write and a
            // surprise.
            if (bed != null)
            {
                var owners = AssignedPawns(comp);
                var slots = BridgeCommon.TryN(() => bed.SleepingSlotsCount);
                if (slots != null && owners.Count >= slots.Value && owners.Count > 0)
                {
                    plan.Extras["evicts"] = PawnRow(owners[owners.Count - 1]);
                    var trimmed = new List<object>(predicted);
                    if (trimmed.Count > 1)
                        trimmed.RemoveAt(0);
                    plan.Predicted = trimmed;
                }
            }

            plan.Write = t =>
            {
                var live = Comp<CompAssignableToPawn>(t);
                if (live != null)
                    live.TryAssignPawn(chosen);
            };
            return plan;
        }

        // ============================================================== readers

        /// <summary>The whole writable configuration of one thing. Every
        /// capability the thing does not have reads false with its values null,
        /// so an absent comp is never mistaken for a false setting.</summary>
        private static Dictionary<string, object> Config(Thing thing)
        {
            var forbiddable = Comp<CompForbiddable>(thing);
            var flick = Comp<CompFlickable>(thing);
            var power = Comp<CompPowerTrader>(thing);
            var temperature = Comp<CompTempControl>(thing);
            var assign = Comp<CompAssignableToPawn>(thing);
            var bed = thing as Building_Bed;

            var block = new Dictionary<string, object>();
            block["forbiddable"] = forbiddable != null;
            block["forbidden"] = forbiddable == null ? null : (object)BridgeCommon.TryN(() => forbiddable.Forbidden);

            block["flickable"] = flick != null;
            block["wantSwitchOn"] = flick == null ? null : (object)WantSwitchOn(flick);
            block["switchIsOn"] = flick == null ? null : (object)BridgeCommon.TryN(() => flick.SwitchIsOn);
            block["flickDesignated"] = flick == null ? null : (object)FlickDesignated(thing);

            block["hasPower"] = power != null;
            block["powered"] = power == null ? null : (object)BridgeCommon.TryN(() => power.PowerOn);
            block["connected"] = power == null ? null : (object)BridgeCommon.TryN(() => power.PowerNet != null);

            block["temperatureControl"] = temperature != null;
            block["targetTemperature"] = temperature == null ? null : (object)BridgeCommon.TryN(() => temperature.targetTemperature);

            block["isBed"] = bed != null;
            block["medical"] = bed == null ? null : (object)BridgeCommon.TryN(() => bed.Medical);
            block["canBeMedical"] = bed == null ? null : (object)BedFlag(bed, "bed_canBeMedical");
            block["forPrisoners"] = bed == null ? null : (object)BridgeCommon.TryN(() => bed.ForPrisoners);
            block["bedOwnerType"] = bed == null ? null : BridgeCommon.SafeString(() => bed.ForOwnerType.ToString());
            block["humanlikeBed"] = bed == null ? null : (object)BedFlag(bed, "bed_humanlike");

            block["assignable"] = assign != null;
            block["assignedPawns"] = assign == null ? null : (object)AssignedRows(assign);
            block["maxAssignedPawns"] = assign == null ? null : (object)BridgeCommon.TryN(() => assign.MaxAssignedPawnsCount);
            return block;
        }

        /// <summary>The three numbers the power field owes a caller, together:
        /// what was asked of the switch, what the switch is, and whether a
        /// colonist has been told to go and change it.</summary>
        private static Dictionary<string, object> FlickState(Thing thing, CompFlickable flick)
        {
            return new Dictionary<string, object>
            {
                { "wantSwitchOn", WantSwitchOn(flick) },
                { "switchIsOn", BridgeCommon.TryN(() => flick.SwitchIsOn) },
                { "flickDesignated", FlickDesignated(thing) }
            };
        }

        private static Dictionary<string, object> FlickPredicted(CompFlickable flick, bool wanted)
        {
            var isOn = BridgeCommon.TryN(() => flick.SwitchIsOn);
            return new Dictionary<string, object>
            {
                { "wantSwitchOn", wanted },
                { "switchIsOn", isOn },
                { "flickDesignated", isOn == null ? (object)null : isOn.Value != wanted }
            };
        }

        private static bool? WantSwitchOn(CompFlickable flick)
        {
            if (flick == null || WantSwitchOnField == null)
                return null;
            try { return (bool)WantSwitchOnField.GetValue(flick); }
            catch { return null; }
        }

        private static bool? FlickDesignated(Thing thing)
        {
            try
            {
                var map = thing.Map;
                var def = DesignationDefOf.Flick;
                if (map == null || map.designationManager == null || def == null)
                    return null;
                // DesignationOn's wrong-target-type arm is a Log.Error, so the
                // target type is checked here rather than relied on.
                if (def.targetType == TargetType.Cell)
                    return null;
                return map.designationManager.DesignationOn(thing, def) != null;
            }
            catch { return null; }
        }

        /// <summary>`FlickUtility.UpdateFlickDesignation`'s designation half,
        /// mirrored. Its own tail calls `TutorUtility.DoModalDialogIfNotKnown`,
        /// which adds a Dialog_MessageBox to the window stack the first time the
        /// concept is met -- a window that would then sit on screen.</summary>
        private static void UpdateFlickDesignation(Thing thing)
        {
            var map = thing.Map;
            var def = DesignationDefOf.Flick;
            if (map == null || map.designationManager == null || def == null || def.targetType == TargetType.Cell)
                return;

            var wants = false;
            var withComps = thing as ThingWithComps;
            if (withComps != null && withComps.AllComps != null)
            {
                for (var i = 0; i < withComps.AllComps.Count; i++)
                {
                    var comp = withComps.AllComps[i] as CompFlickable;
                    if (comp != null && comp.WantsFlick())
                    {
                        wants = true;
                        break;
                    }
                }
            }

            var standing = map.designationManager.DesignationOn(thing, def);
            if (wants && standing == null)
                map.designationManager.AddDesignation(new Designation(thing, def));
            else if (!wants && standing != null)
                standing.Delete();
        }

        /// <summary>A `def.building` flag by name. `def.building` is null on
        /// anything that is not a building, and a missing field reads false
        /// rather than throwing into Log.Error.</summary>
        private static bool BedFlag(Building_Bed bed, string flag)
        {
            try
            {
                var props = bed.def == null ? null : bed.def.building;
                if (props == null)
                    return false;
                if (flag == "bed_canBeMedical")
                    return props.bed_canBeMedical;
                if (flag == "bed_humanlike")
                    return props.bed_humanlike;
                return false;
            }
            catch { return false; }
        }

        private static string PrisonerFailReason()
        {
            return BridgeCommon.SafeString(() => "CommandBedSetForPrisonersFailOutdoors".Translate().ToString())
                   ?? "This room cannot be a prison cell.";
        }

        private static List<Pawn> AssignedPawns(CompAssignableToPawn comp)
        {
            var list = new List<Pawn>();
            try
            {
                var assigned = comp.AssignedPawnsForReading;
                if (assigned != null)
                    foreach (var pawn in assigned)
                        if (pawn != null)
                            list.Add(pawn);
            }
            catch { }
            return list;
        }

        private static List<object> AssignedRows(CompAssignableToPawn comp)
        {
            var rows = new List<object>();
            if (comp == null)
                return rows;
            foreach (var pawn in AssignedPawns(comp))
                rows.Add(PawnRow(pawn));
            return rows;
        }

        private static List<object> OwnerNames(Building_Bed bed)
        {
            var names = new List<object>();
            try
            {
                var owners = bed.OwnersForReading;
                if (owners != null)
                    foreach (var pawn in owners)
                        if (pawn != null)
                            names.Add(BridgeCommon.SafeString(() => pawn.LabelShortCap.ToString()));
            }
            catch { }
            return names;
        }

        /// <summary>The pawns the game's own Set-owner dialog would list.
        /// `CompAssignableToPawn_Bed.AssigningCandidates` reads `Faction.OfPlayer`
        /// for an animal bed, which is why the whole tool refuses a map with no
        /// player faction before it gets here.</summary>
        private static List<Pawn> Candidates(CompAssignableToPawn comp)
        {
            var list = new List<Pawn>();
            try
            {
                var candidates = comp.AssigningCandidates;
                if (candidates != null)
                    foreach (var pawn in candidates)
                        if (pawn != null)
                            list.Add(pawn);
            }
            catch { }
            return list;
        }

        private static Dictionary<string, object> PawnRow(Pawn pawn)
        {
            return new Dictionary<string, object>
            {
                { "name", BridgeCommon.SafeString(() => pawn.LabelShortCap.ToString()) },
                { "thingId", BridgeCommon.SafeString(() => pawn.ThingID) }
            };
        }

        // ============================================================ resolution

        /// <summary>
        /// The colony's own buildings, blueprints and frames. Blueprints and
        /// frames are NOT in ThingRequestGroup.BuildingArtificial (they have
        /// their own groups) and they carry CompForbiddable, so all three groups
        /// are queried and de-duplicated by reference, exactly as
        /// home/list_buildings does.
        /// </summary>
        private static List<Thing> ColonyBuildings(Map map, Faction player)
        {
            var seen = new HashSet<Thing>();
            var list = new List<Thing>();
            foreach (var group in new[] { ThingRequestGroup.BuildingArtificial,
                                          ThingRequestGroup.Blueprint,
                                          ThingRequestGroup.BuildingFrame })
            {
                List<Thing> found;
                try { found = map.listerThings.ThingsInGroup(group); }
                catch { continue; }
                if (found == null)
                    continue;
                for (var i = 0; i < found.Count; i++)
                {
                    var thing = found[i];
                    if (thing == null || !seen.Add(thing))
                        continue;
                    var faction = BridgeCommon.Try<Faction>(() => thing.Faction, null);
                    if (faction != player)
                        continue;
                    list.Add(thing);
                }
            }
            return list;
        }

        /// <summary>
        /// A building by exact ThingID, by "DefName@x,z", by exact label or
        /// defName, or by a unique case-insensitive substring of either. An
        /// AMBIGUOUS name is refused with every match listed, never resolved to
        /// the first one: this tool writes.
        /// </summary>
        private static bool TryResolveThing(List<Thing> colony, string spec, out Thing thing,
                                            out string error, out List<object> candidates)
        {
            thing = null;
            error = string.Empty;
            candidates = null;

            var wanted = (spec ?? string.Empty).Trim();
            if (wanted.Length == 0)
            {
                error = "No building given. Pass thing=\"<ThingID>\", thing=\"DefName@x,z\" (home/list_buildings prints both "
                      + "halves), or a unique label/defName substring.";
                return false;
            }

            var byId = colony.FirstOrDefault(t =>
                string.Equals(BridgeCommon.SafeString(() => t.ThingID), wanted, StringComparison.Ordinal));
            if (byId == null)
            {
                // Every id spelling that reaches this tool in practice. The
                // bridge prints `Thing_Wall1234`, `home/list_buildings` prints
                // `Wall1234`, and `build.py` prints the bare thingIDNumber off
                // `placed.thingIDNumber` -- 2026-09-07 turn 37, where a Cancel
                // was refused on an id build.py had returned seconds earlier.
                var bare = wanted.StartsWith("Thing_", StringComparison.OrdinalIgnoreCase)
                    ? wanted.Substring(6) : wanted;
                byId = colony.FirstOrDefault(t =>
                    string.Equals(BridgeCommon.SafeString(() => t.ThingID), bare, StringComparison.OrdinalIgnoreCase));
                int number;
                if (byId == null && int.TryParse(wanted, NumberStyles.Integer,
                        CultureInfo.InvariantCulture, out number))
                    byId = colony.FirstOrDefault(t => BridgeCommon.Try(() => t.thingIDNumber, 0) == number);
            }
            if (byId != null)
            {
                thing = byId;
                return true;
            }

            // A coordinate copied from a listing is sufficient when exactly
            // one colony building occupies that cell. Multi-cell buildings are
            // tested by their whole occupied rect, just like DefName@x,z.
            IntVec3 bareCell;
            if (wanted.IndexOf('@') < 0 && TryParseCell(wanted, out bareCell))
            {
                var atCell = colony.Where(t => Covers(t, bareCell)).ToList();
                if (atCell.Count == 1)
                {
                    thing = atCell[0];
                    return true;
                }
                candidates = Rows(atCell.Count == 0 ? colony : atCell);
                error = atCell.Count == 0
                    ? "No colony building occupies cell " + bareCell.x + "," + bareCell.z + "."
                    : "Cell " + bareCell.x + "," + bareCell.z + " contains " + atCell.Count
                      + " colony buildings. This tool writes, so use a thingId from candidates[].";
                return false;
            }

            var at = wanted.IndexOf('@');
            if (at > 0)
            {
                var defPart = wanted.Substring(0, at).Trim();
                var cellPart = wanted.Substring(at + 1).Trim();
                IntVec3 cell;
                if (!TryParseCell(cellPart, out cell))
                {
                    error = "\"" + wanted + "\" looks like DefName@x,z but \"" + cellPart
                          + "\" is not a cell. Expected two integers, e.g. Bed@62,141.";
                    return false;
                }

                var here = colony.Where(t =>
                    string.Equals(BridgeCommon.SafeString(() => t.def.defName), defPart, StringComparison.OrdinalIgnoreCase)
                    && Covers(t, cell)).ToList();
                if (here.Count == 1)
                {
                    thing = here[0];
                    return true;
                }
                if (here.Count == 0)
                {
                    error = "No colony " + defPart + " occupies cell " + cell.x + "," + cell.z + ".";
                    return false;
                }
                candidates = Rows(here);
                error = "\"" + wanted + "\" matches " + here.Count + " things on that cell. This tool writes, so it will not "
                      + "pick one -- use one of the thingIds in candidates[].";
                return false;
            }

            var exact = colony.Where(t => Matches(t, wanted, false)).ToList();
            if (exact.Count == 1)
            {
                thing = exact[0];
                return true;
            }
            if (exact.Count > 1)
            {
                candidates = Rows(exact);
                error = "\"" + wanted + "\" names " + exact.Count + " colony buildings. This tool writes, so it will not pick "
                      + "one -- pass a thingId from candidates[], or \"DefName@x,z\".";
                return false;
            }

            var partial = colony.Where(t => Matches(t, wanted, true)).ToList();
            if (partial.Count == 1)
            {
                thing = partial[0];
                return true;
            }
            if (partial.Count > 1)
            {
                candidates = Rows(partial);
                error = "\"" + wanted + "\" matches " + partial.Count + " colony buildings. Be exact, or pass a thingId from "
                      + "candidates[], or \"DefName@x,z\".";
                return false;
            }

            error = "No colony building matches \"" + wanted + "\". home/list_buildings lists them; the default scope here is "
                  + "the player faction's own buildings, blueprints and frames.";
            return false;
        }

        private static bool Matches(Thing thing, string wanted, bool substring)
        {
            var defName = BridgeCommon.SafeString(() => thing.def.defName) ?? string.Empty;
            var label = BridgeCommon.SafeString(() => thing.LabelCap.ToString()) ?? string.Empty;
            var defLabel = BridgeCommon.SafeString(() => thing.def.label) ?? string.Empty;
            if (!substring)
                return string.Equals(defName, wanted, StringComparison.OrdinalIgnoreCase)
                    || string.Equals(label, wanted, StringComparison.OrdinalIgnoreCase)
                    || string.Equals(defLabel, wanted, StringComparison.OrdinalIgnoreCase);
            return defName.IndexOf(wanted, StringComparison.OrdinalIgnoreCase) >= 0
                || label.IndexOf(wanted, StringComparison.OrdinalIgnoreCase) >= 0
                || defLabel.IndexOf(wanted, StringComparison.OrdinalIgnoreCase) >= 0;
        }

        /// <summary>Position is Thing.Position, which for a multi-cell building
        /// is not the min corner, so the whole occupied rect is tested and the
        /// anchor cell alone is the fallback when the rect read throws.</summary>
        private static bool Covers(Thing thing, IntVec3 cell)
        {
            try
            {
                var rect = GenAdj.OccupiedRect(thing);
                return rect.Contains(cell);
            }
            catch
            {
                return BridgeCommon.Try(() => thing.Position == cell, false);
            }
        }

        private static bool TryParseCell(string text, out IntVec3 cell)
        {
            cell = IntVec3.Invalid;
            var parts = (text ?? string.Empty).Split(new[] { ',', ' ' }, StringSplitOptions.RemoveEmptyEntries);
            int x, z;
            if (parts.Length != 2
                || !int.TryParse(parts[0], out x)
                || !int.TryParse(parts[1], out z))
                return false;
            cell = new IntVec3(x, 0, z);
            return true;
        }

        private static List<object> Rows(List<Thing> things)
        {
            var rows = new List<object>();
            for (var i = 0; i < things.Count && i < MaxCandidates; i++)
                rows.Add(ThingRow(things[i]));
            return rows;
        }

        /// <summary>A pawn among a fixed candidate list, by exact ThingID, exact
        /// name, or a unique case-insensitive substring. Ambiguity is refused
        /// with the candidates named.</summary>
        private static bool TryResolvePawn(List<Pawn> candidates, string spec, out Pawn pawn, out string error)
        {
            pawn = null;
            error = string.Empty;

            if (candidates.Count == 0)
            {
                error = "The game lists nobody who could be assigned to this thing (CompAssignableToPawn.AssigningCandidates "
                      + "is empty), so \"" + spec + "\" cannot be one of them.";
                return false;
            }

            var byId = candidates.FirstOrDefault(p =>
                string.Equals(BridgeCommon.SafeString(() => p.ThingID), spec, StringComparison.Ordinal));
            if (byId != null)
            {
                pawn = byId;
                return true;
            }

            var exact = candidates.Where(p =>
                string.Equals(BridgeCommon.SafeString(() => p.LabelShortCap.ToString()), spec, StringComparison.OrdinalIgnoreCase))
                .ToList();
            if (exact.Count == 1)
            {
                pawn = exact[0];
                return true;
            }

            var partial = exact.Count > 1 ? exact : candidates.Where(p =>
            {
                var name = BridgeCommon.SafeString(() => p.LabelShortCap.ToString());
                return name != null && name.IndexOf(spec, StringComparison.OrdinalIgnoreCase) >= 0;
            }).ToList();

            if (partial.Count == 1)
            {
                pawn = partial[0];
                return true;
            }
            if (partial.Count > 1)
            {
                error = "\"" + spec + "\" matches " + partial.Count + " of the pawns this thing will accept: "
                      + Names(partial) + ". This tool writes, so it will not pick one -- pass a ThingID.";
                return false;
            }

            error = "\"" + spec + "\" is not one of the pawns the game would let this thing be assigned to. It lists: "
                  + Names(candidates) + ". (options.assigningCandidates carries their thingIds.)";
            return false;
        }

        private static string Names(List<Pawn> pawns)
        {
            return string.Join(", ", pawns
                .Take(MaxCandidates)
                .Select(p => (BridgeCommon.SafeString(() => p.LabelShortCap.ToString()) ?? "?"))
                .ToArray()) + (pawns.Count > MaxCandidates ? ", and " + (pawns.Count - MaxCandidates) + " more" : "");
        }

        // ============================================================== helpers

        private static T Comp<T>(Thing thing) where T : ThingComp
        {
            try
            {
                var withComps = thing as ThingWithComps;
                return withComps == null ? null : withComps.GetComp<T>();
            }
            catch { return null; }
        }

        private static FieldPlan NewPlan(string field, object requested, Thing thing)
        {
            return new FieldPlan
            {
                Field = field,
                Requested = requested,
                Snapshot = Config(thing)
            };
        }

        private static FieldPlan Refuse(FieldPlan plan, object before, string reason)
        {
            plan.Refused = true;
            plan.Reason = reason;
            plan.Before = before;
            plan.Predicted = before;
            plan.Write = null;
            plan.ReadBack = null;
            return plan;
        }

        private static bool TryParseOnOff(string spec, out bool value)
        {
            value = false;
            var s = (spec ?? string.Empty).Trim().ToLowerInvariant();
            if (s == "on" || s == "true" || s == "yes" || s == "1") { value = true; return true; }
            if (s == "off" || s == "false" || s == "no" || s == "0") { value = false; return true; }
            return false;
        }

        private static string Show(object value)
        {
            if (value == null)
                return "(none)";
            if (value is bool)
                return (bool)value ? "on" : "off";
            var list = value as List<object>;
            if (list != null)
            {
                if (list.Count == 0)
                    return "(nobody)";
                return string.Join(", ", list.Select(Show).ToArray());
            }
            var row = value as Dictionary<string, object>;
            if (row != null)
            {
                object name;
                if (row.TryGetValue("name", out name) && name != null)
                    return name.ToString();
                return string.Join(" ", row.Select(pair => pair.Key + "=" + Show(pair.Value)).ToArray());
            }
            return value.ToString();
        }

        private static Dictionary<string, object> Failure(string error)
        {
            return BridgeCommon.Failure(ToolName, error);
        }
    }
}
