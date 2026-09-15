using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using RimBridgeServer.Sdk;
using RimWorld;
using Verse;

namespace HomeBridge.BridgeTools
{
    /// <summary>
    /// home/place_building — answer "can this go here, facing which way, and what
    /// is in the way" for one or all rotations, and then place the blueprint
    /// if asked. Callers must state `dryRun` explicitly; writes need one rotation.
    ///
    /// ## Why the rotation sweep is the whole point
    ///
    /// `Designator_Build.CanDesignateCell(c)` is
    /// `GenConstruct.CanPlaceBlueprintAt(entDef, c, placingRot, Map, ...)`, and
    /// `placingRot` is a **protected** field on `Designator_Place`. A bridge that
    /// drives the architect menu cannot set it, so every dry run it has ever
    /// answered was for one fixed rotation — usually North — and a caller reading
    /// "cannot place here" had no way to know that turning the thing would have
    /// worked. This tool takes the rotation as a parameter, calls
    /// `CanPlaceBlueprintAt` directly, and by default reports all four.
    ///
    /// ## Which face of a cooler is the cold one
    ///
    /// Read out of `Building_Cooler.TickRare`:
    /// `Position + IntVec3.South.RotatedBy(Rotation)` is the cell whose ROOM gets
    /// cooled, and `Position + IntVec3.North.RotatedBy(Rotation)` is where the heat
    /// is pushed. So a cooler wants its *south* face indoors and its *north* face
    /// out, and getting that backwards heats the room you meant to chill. Both
    /// cells are reported per rotation with their room, whether that room uses
    /// outdoor temperature, and the temperature there right now — which is enough
    /// to read off which orientation actually puts the hot side outside.
    ///
    /// `Building_Vent` is symmetric: `TickRare` calls
    /// `GenTemperature.EqualizeTemperaturesThroughBuilding(this, 14f, twoWay: true)`
    /// which averages the rooms at `cell + Rotation.FacingCell` and
    /// `cell - Rotation.FacingCell` over the building's occupied rect. For a 1x1
    /// vent those are the same two cells as the cooler's, so they are reported as
    /// `a` / `b` rather than hot / cold, because neither side is privileged.
    ///
    /// ## Designators are bypassed on purpose
    ///
    /// Nothing here touches `Find.DesignatorManager`. `Designator_Build` carries UI
    /// state (the selected stuff, the placing rotation, the mouse attachment) and
    /// driving it from a bridge call means mutating what the player sees. Placement
    /// is `GenConstruct.PlaceBlueprintForBuild(def, center, map, rot, faction,
    /// stuff)`, which is the same call the designator itself makes.
    ///
    /// ## The wipe has to happen, and with the right DestroyMode
    ///
    /// This tool used to skip `GenSpawn.WipeExistingThings` on the grounds that not
    /// destroying things is the safer default. It is not.
    /// `PlaceBlueprintForBuild` calls `GenSpawn.Spawn`, whose `wipeMode` defaults
    /// to `WipeMode.Vanish`, and Vanish runs
    /// `WipeExistingThings(..., DestroyMode.Vanish)` anyway. So the choice was
    /// never "wipe or do not wipe" -- it was "wipe with a refund or wipe without
    /// one". Placing over another def's frame under Vanish destroys the materials
    /// already delivered to it and returns nothing, where the architect menu
    /// refunds them.
    ///
    /// A real placement now does exactly what
    /// `Designator_Build.DesignateSingleCell` does, in the same order:
    ///
    ///   1. destroy any `Frame` in the ANCHOR cell whose `replaceTags` intersect
    ///      `entDef.blueprintDef.replaceTags`, with `DestroyMode.Cancel`;
    ///   2. `GenSpawn.WipeExistingThings(center, rot, entDef.blueprintDef, map,
    ///      DestroyMode.Deconstruct)` -- Deconstruct, so materials come back;
    ///   3. `PlaceBlueprintForBuild`, whose own Vanish wipe then finds nothing
    ///      left to destroy.
    ///
    /// Everything removed is reported as `wiped` / `framesCancelled`, and a dry run
    /// predicts both under `wipeOnPlace` / `framesCancelledOnPlace` using the same
    /// predicate, so the two cannot disagree.
    ///
    /// ## Log.Error avoidance
    ///
    /// `Verse.Log.Error` calls `TickManager.Pause()`. Two ordinary-looking calls on
    /// this path reach it and are avoided:
    ///
    ///   * `Rot4.FromString(s)` logs "Invalid rotation" for anything it does not
    ///     recognise. Rotation names are parsed here by hand.
    ///   * `CostListAdjusted(def, stuff)` defaults `errorOnNullStuff` to TRUE and
    ///     logs when a stuffed def is costed with a null stuff. It is called with
    ///     `errorOnNullStuff: false`.
    ///
    /// `Faction.OfPlayer` is likewise never used: its body is
    /// `get_OfPlayerSilentFail` followed by `Log.Error`.
    ///
    /// ## `cells` — a wall line is ONE call
    ///
    /// A 64-cell wall placed one call per cell cost ~1.8 s a cell, of which
    /// 1.5 s was `Watch.DefaultLeadMs`: two minutes of wall time in which the
    /// caller could answer nothing, which on a stream is dead air (found live on
    /// Threadneedle, 2026-09-08). `cells="141,130;142,130;..."` places the whole
    /// line in one call: one def resolution, ONE `materials` scan of the map,
    /// one camera move, ONE 1.5 s watch lead, one close. Every cell is still
    /// evaluated by `GenConstruct.CanPlaceBlueprintAt` and placed by
    /// `GenConstruct.PlaceBlueprintForBuild` exactly as a single-cell call does
    /// — `PlaceOne` is the one placement path both take — so a batch cannot
    /// place anything a single call would not.
    ///
    /// **The main thread is never held for the batch.** A long synchronous loop
    /// inside one `MainThread.InvokeAsync` hop stalls `Verse.Root.Update`, which
    /// is the game's tick AND the queue every other bridge call is pumped from:
    /// exactly the "delays every event" complaint, moved rather than fixed. So
    /// the batch is chunked — at most `CellsPerHop` (8) cells per hop, with a
    /// `HopGapMs` (20 ms, about one frame at 60 fps) gap between hops so the
    /// game ticks, renders and delivers its letters in between. Each cell is a
    /// `CanPlaceBlueprintAt`, a thing-grid read of its own footprint and a
    /// `PlaceBlueprintForBuild` — the same work a player's click does, and eight
    /// of them is well under a frame. Nothing map-wide runs per cell:
    /// `materials` is computed once, before the first placement, for the whole
    /// batch.
    ///
    /// A refused cell does not stop the batch — a wall line crossing one
    /// doorway should lay the other 63 cells and say which one it skipped — so
    /// the reply carries per-cell rows and `outcome: "partial"` when some cells
    /// landed and some did not.
    /// </summary>
    public sealed class HomePlaceBuildingTools
    {
        private const string ToolName = "home/place_building";
        private static readonly string[] RotationNames = { "north", "east", "south", "west" };

        /// <summary>Cells placed inside ONE main-thread hop. The bound on how
        /// long a batch can hold Root.Update: eight CanPlaceBlueprintAt +
        /// PlaceBlueprintForBuild pairs, which is less work than one frame of
        /// a player dragging a wall line.</summary>
        private const int CellsPerHop = 8;

        /// <summary>Milliseconds released between hops, off the main thread, so
        /// the game gets its frame: ticks, rendering and every other queued
        /// bridge call run in the gap.</summary>
        private const int HopGapMs = 20;

        /// <summary>Refusal point for a single batch. 200 cells is ~4 s of
        /// hops and a reply of a few tens of KB; beyond it the caller should
        /// split, and is told so by name.</summary>
        private const int MaxBatchCells = 200;

        [Tool(
            ToolName,
            Title = "Check and place a building blueprint, for any rotation",
            Description =
                "Evaluates GenConstruct.CanPlaceBlueprintAt for a buildable ThingDef or TerrainDef at a cell for one or all "
                + "rotations, and reports for each whether it is accepted, the game's own refusal reason, the cells it "
                + "would occupy, and everything standing in them. Coolers and vents additionally report which cell each face lands "
                + "in, with that cell's room, whether the room uses outdoor temperature, and its current temperature -- so a caller "
                + "can tell which way round puts the hot side outside. With dryRun:false and an accepted rotation it places "
                + "the blueprint. Pass cells=\"x,z;x,z\" to do a whole wall line or room in ONE call, with one materials scan, "
                + "one camera move and one watch lead for the batch; the placements are chunked across main-thread hops so the "
                + "game keeps ticking. It never touches the architect menu or any designator.",
            ResultDescription =
                "success, dryRun, def (defName/label/kind), stuff, size, rotatable, researchFinished, buildableByPlayer, costList[], "
                + "rotations[] (one row per rotation evaluated), placed (the blueprint, when one was made), notes.")]
        [ToolResponse("rotations", "array", "One row per rotation evaluated: rotation, accepted, reason, occupiedRect, occupiedCells[], blockingThings[] (each with effectOnPlace: wiped/replaced/crop/cleared/hauled/frameCancelled/none), and sides for coolers/vents.", Always = true)]
        [ToolResponse("materials", "object", "Whether the colony can actually build this, answered at placement time and on a dry run alike -- 'the game accepts this blueprint here' and 'there is steel to finish it' are different questions. rows[] has one entry per cost entry of the def for the chosen stuff (def.CostListAdjusted(stuff, errorOnNullStuff:false)): defName, label, needed, onMap (spawned stacks on THIS map owned by the player or nobody -- a trader's crate does not count), forbidden (of those), reservedByOtherBlueprints (the outstanding deficit of every other blueprint and frame, the same number home/list_buildings reports as resourceDeficit), available (= onMap - forbidden - reservedByOtherBlueprints, floored at 0) and shortfall (= max(0, needed - available)). Beside them canBuildNow (every shortfall is 0) and missing (one line, e.g. 'missing: 25 steel (have 15, 10 forbidden), 3 components (have 0)', EMPTY STRING when nothing is missing). One pass over the map per call, never one per rotation. unreadable:true with canBuildNow NULL means the cost list could not be read -- not known, never 'yes'.", Always = true)]
        [ToolResponse("dryRun", "boolean", "True = nothing was placed. Callers must state true or false explicitly.", Always = true)]
        [ToolResponse("canPlace", "boolean", "True when at least one requested rotation is accepted by the game's placement validator. For rotation:'all', this means any listed rotation can be placed; inspect acceptedRotations for which ones.", Always = true)]
        [ToolResponse("applied", "boolean", "True only when this call created a blueprint. A successful preview is always false.", Always = true)]
        [ToolResponse("outcome", "string", "preview, placed, already_present, refused, or error. Unlike success, this says what happened to the requested blueprint.", Always = true)]
        [ToolResponse("detail", "string", "Prominent one-line outcome. Preview text explicitly says that no blueprint was placed and how many rotations were accepted.", Always = true)]
        [ToolResponse("placed", "object", "The blueprint that was created: thingIDNumber, defName, position, rotation. Null on a dry run, a refusal, or when an identical blueprint already existed.", Always = true, Nullable = true)]
        [ToolResponse("unknownArguments", "array", "Every argument key the caller sent that this tool does not declare, sorted, case-sensitively. Empty array = every key was recognised. The host's own _rimBridgeTimeoutMs is never listed.", Always = true)]
        [ToolResponse("unknownArgumentsWarning", "string", "Present only when unknownArguments is non-empty, or when the caller's raw keys could not be read at all - in which case the empty unknownArguments means 'not known', not 'nothing unknown'. On a WRITE tool this matters twice over: a misspelled dryRun is the difference between a plan and a blueprint.", Nullable = true)]
        [ToolResponse("batch", "object", "Present ONLY when the caller sent cells. requested, rotation, cellsPerHop, hops, placed, alreadyPresent, refused, errors, accepted (dry run), placedIds[], firstRefusal (null when nothing was refused) and rows[] -- one row per requested cell, in the order asked, each the same rotation row a single-cell call returns plus x, z, outcome (placed/already_present/refused/error/preview) and, on a real run, placed{} for that cell. A refused cell never stops the batch; top-level outcome is 'partial' when some cells landed and some did not. Top-level placed is the FIRST blueprint made, wiped/framesCancelled are the whole batch's, rotations[] is the first cell's row, and materials.needed is the cost for ALL requested cells (materials.forCells says how many, perCellNeeded the cost of one).", Nullable = true)]
        [ToolResponse("watch", "object", "The decorative half of the write: shown (bool), selected, inspectTab, mainTab, cameraMoved, leadMs, closesAfterSeconds, note and reason. On a real placement the camera jumps to the empty cell BEFORE the blueprint is made, then the new blueprint is selected and deselects itself. shown:false with a reason on a dry run, a refusal, or watch:false.", Always = true)]
        public async Task<object> PlaceBuilding(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            [ToolParameter(Description = "What to place: the defName or label (case-insensitive) of a buildable ThingDef or TerrainDef. ThingDefs are searched first. Legacy alias for defName; if both are sent they must agree.")] string def = null,
            [ToolParameter(Description = "What to place: the defName (preferred) or label (case-insensitive) of a buildable ThingDef or TerrainDef. ThingDefs are searched first. If def is also sent, both must agree.")] string defName = null,
            [ToolParameter(Description = "Cell x (the building's anchor cell, the same one Thing.Position reports).", DefaultValue = -1)] int x = -1,
            [ToolParameter(Description = "Cell z.", DefaultValue = -1)] int z = -1,
            [ToolParameter(Description = "Batch: an explicit cell list, \"141,130;142,130;143,130\" -- a wall line or a room in ONE call, with one materials scan, one camera move and one watch lead for the whole batch. x/z, when given, is the first cell; duplicates are collapsed and the order asked is the order placed. Needs ONE rotation, never \"all\". Max 200 cells. Every cell is still checked and placed one at a time, chunked 8 per main-thread hop so the game keeps ticking; a refused cell is reported and the rest still go in.")] string cells = null,
            [ToolParameter(Description = "north / east / south / west, or 'all' to evaluate every rotation. Defaults to 'all' for previews; a real placement needs a single value.", DefaultValue = "all")] string rotation = "all",
            [ToolParameter(Description = "Material defName or label. Defaults to GenStuff.DefaultStuffFor(def) when the def is made from stuff, and is ignored when it is not.")] string stuff = null,
            [ToolParameter(Description = "Evaluate as though god mode were on, which skips the map-edge check. Does not enable god mode.", DefaultValue = false)] bool godMode = false,
            [ToolParameter(Description = "Required: true evaluates only; false actually places the blueprint.")] bool? dryRun = null,
            [ToolParameter(Description = "TRUE by default. On a real placement, jump the camera to the empty cell a moment BEFORE the blueprint is made, then select the new blueprint and let it deselect itself. Decorative only: it never changes what is placed. Pass false to place with no UI.", DefaultValue = true)] bool watch = true,
            [ToolParameter(Description = "How long the new blueprint stays selected, in seconds. Clamped 1..60. Ignored when watch is false or the run is a dry run.", DefaultValue = 8)] int watchSeconds = 8)
        {
            if (string.IsNullOrWhiteSpace(def) && string.IsNullOrWhiteSpace(defName))
                return BridgeCommon.WithUnknownArguments(Failure("defName is required (legacy alias: def)."), ctx, typeof(HomePlaceBuildingTools), ToolName);
            if (!string.IsNullOrWhiteSpace(def) && !string.IsNullOrWhiteSpace(defName)
                && !string.Equals(def.Trim(), defName.Trim(), StringComparison.OrdinalIgnoreCase))
                return BridgeCommon.WithUnknownArguments(Failure("def and defName were both supplied but do not match."), ctx, typeof(HomePlaceBuildingTools), ToolName);
            if (!dryRun.HasValue)
                return BridgeCommon.WithUnknownArguments(Failure("dryRun is required: pass true to inspect or false to place."), ctx, typeof(HomePlaceBuildingTools), ToolName);

            var requestedDef = string.IsNullOrWhiteSpace(defName) ? def : defName;
            return BridgeCommon.WithUnknownArguments(
                await PlaceBuildingCore(ctx, cancellationToken, requestedDef, x, z, cells, rotation, stuff, godMode, dryRun.Value, watch, watchSeconds).ConfigureAwait(false),
                ctx, typeof(HomePlaceBuildingTools), ToolName);
        }

        private async Task<object> PlaceBuildingCore(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            string def,
            int x,
            int z,
            string cells,
            string rotation,
            string stuff,
            bool godMode,
            bool dryRun,
            bool watch,
            int watchSeconds)
        {
            if (ctx?.MainThread == null)
                return Failure("No RimBridge main-thread dispatcher is available for this invocation.");

            // `cells` in, batch out: the reply shape follows the ASK, so a caller
            // that sent a list always reads batch.rows and never has to work out
            // which shape came back from how many cells it happened to contain.
            if (!string.IsNullOrWhiteSpace(cells))
            {
                List<IntVec3> batchCells;
                string cellsError;
                if (!TryParseCellList(cells, x, z, out batchCells, out cellsError))
                    return Failure(cellsError);
                return await PlaceBatch(ctx, cancellationToken, def, batchCells, rotation, stuff,
                                        godMode, dryRun, watch, watchSeconds).ConfigureAwait(false);
            }

            // Companion tools are dispatched with MarshalToMainThread = false
            // (AnnotatedExtensionCapabilityProvider.InvokeAsync). The def database,
            // the thing grid, the room grid, the temperature trackers and (on a
            // real run) GenSpawn.Spawn all have to happen on RimWorld's own thread,
            // and the evaluation and the placement must be the same instant --
            // otherwise the tool can report "accepted" and then place into a cell
            // something moved into. ONE hop covers both.
            if (dryRun)
            {
                var evaluated = await ctx.MainThread
                    .InvokeAsync(() => Build(def, x, z, rotation, stuff, godMode, true, false, null), cancellationToken)
                    .ConfigureAwait(false);
                Stamp(evaluated, Watch.Skipped("dry run"));
                return evaluated;
            }

            // Hop 1: every check a real placement makes, stopping immediately
            // before the wipe. The blueprint does not exist yet, so the watch
            // step can only show the empty cell; the blueprint is selected in
            // hop 2, the moment it is made.
            var pass = await ctx.MainThread
                .InvokeAsync(() => Pass1(ctx, def, x, z, rotation, stuff, godMode, watch), cancellationToken)
                .ConfigureAwait(false);

            if (pass.Failure != null)
                return pass.Failure;

            // Off the main thread: a moment on the empty cell with nothing placed.
            await Watch.Lead(pass.Session, cancellationToken).ConfigureAwait(false);

            // Hop 2: the placement. Every check runs again against the game as it
            // is now rather than trusting hop 1's verdict across the gap.
            var session = pass.Session;
            var reason = pass.SkipReason;
            return await ctx.MainThread
                .InvokeAsync(() =>
                {
                    Thing placedThing = null;
                    var reply = Build(def, x, z, rotation, stuff, godMode, false, false, t => placedThing = t);
                    if (session == null)
                    {
                        Stamp(reply, Watch.Skipped(reason));
                    }
                    else
                    {
                        if (placedThing != null)
                            Watch.SelectNow(session, placedThing);
                        Stamp(reply, Watch.Finish(session, watchSeconds));
                    }
                    return reply;
                }, cancellationToken)
                .ConfigureAwait(false);
        }

        /// <summary>Put the watch block on a reply that is a payload.</summary>
        private static void Stamp(object reply, Dictionary<string, object> watch)
        {
            var payload = reply as Dictionary<string, object>;
            if (payload != null)
                payload["watch"] = watch;
        }

        /// <summary>What hop 1 hands to hop 2. Failure non-null means stop.</summary>
        private sealed class Pass1Result
        {
            internal object Failure;
            internal Watch.Session Session;
            internal string SkipReason;
        }

        /// <summary>Main thread. Run the placement up to the point of no return
        /// and, when it would really place something, put the camera on the cell.
        /// Places nothing.</summary>
        private static Pass1Result Pass1(IRimBridgeContext ctx, string defSpec, int x, int z, string rotationSpec,
                                         string stuffSpec, bool godMode, bool wantWatch)
        {
            var planned = Build(defSpec, x, z, rotationSpec, stuffSpec, godMode, false, true, null);

            var payload = planned as Dictionary<string, object>;
            if (payload == null || !BridgeCommon.Bool(payload, "success"))
            {
                Stamp(planned, Watch.Skipped("refused"));
                return new Pass1Result { Failure = planned };
            }

            if (!wantWatch)
                return new Pass1Result { SkipReason = "watch:false" };
            if (Equals(payload["alreadyPlaced"], true))
                return new Pass1Result { SkipReason = "nothing to place" };

            return new Pass1Result { Session = Watch.OpenAtCell(ctx, new IntVec3(x, 0, z)) };
        }

        // ================================================================= batch

        /// <summary>One key per cell, so a duplicate in the list is collapsed
        /// rather than placed twice.</summary>
        private static long CellKey(int x, int z)
        {
            return ((long)x << 32) ^ (uint)z;
        }

        /// <summary>
        /// `"141,130;142,130"` (or a comma-and-semicolon list with spaces) plus
        /// the optional x/z anchor, in the order asked, duplicates collapsed.
        /// A cell that cannot be read refuses the whole call by name: a list
        /// silently short of one cell is a wall with a hole in it.
        /// </summary>
        private static bool TryParseCellList(string spec, int x, int z, out List<IntVec3> cells, out string error)
        {
            cells = new List<IntVec3>();
            error = null;
            var seen = new HashSet<long>();

            if (x >= 0 && z >= 0 && seen.Add(CellKey(x, z)))
                cells.Add(new IntVec3(x, 0, z));

            var parts = (spec ?? string.Empty).Split(new[] { ';', '|', '\n' }, StringSplitOptions.RemoveEmptyEntries);
            foreach (var part in parts)
            {
                var text = part.Trim();
                if (text.Length == 0)
                    continue;
                var bits = text.Split(',');
                int cx, cz;
                if (bits.Length != 2
                    || !int.TryParse(bits[0].Trim(), out cx)
                    || !int.TryParse(bits[1].Trim(), out cz))
                {
                    error = "cells must be \"x,z;x,z;x,z\" -- \"" + text + "\" is not a cell.";
                    return false;
                }
                if (cx < 0 || cz < 0)
                {
                    error = "cells contains (" + cx + "," + cz + "), which is not a cell on any map.";
                    return false;
                }
                if (seen.Add(CellKey(cx, cz)))
                    cells.Add(new IntVec3(cx, 0, cz));
            }

            if (cells.Count == 0)
            {
                error = "cells was given but named no cell. Pass cells=\"x,z;x,z\", or drop it and pass x/z for a single placement.";
                return false;
            }
            if (cells.Count > MaxBatchCells)
            {
                error = "cells names " + cells.Count + " cells and the cap is " + MaxBatchCells
                      + " per call. Split it: the cap is there so one call cannot hold the game for longer than a caller expects.";
                return false;
            }
            return true;
        }

        /// <summary>Everything the batch carries between its hops. One hop runs
        /// at a time -- each is awaited before the next is queued -- so the
        /// counters need no locking.</summary>
        private sealed class BatchRun
        {
            internal Map Map;
            internal BuildableDef EntDef;
            internal ThingDef StuffDef;
            internal ThingDef BlueprintDef;
            internal Rot4 Rot;
            internal bool GodMode;
            internal bool IsCooler;
            internal bool IsVent;
            internal bool DryRun;
            internal Faction Player;
            internal List<IntVec3> Cells;
            internal Dictionary<string, object> Payload;
            internal readonly List<object> Rows = new List<object>();
            internal readonly List<object> Wiped = new List<object>();
            internal readonly List<object> FramesCancelled = new List<object>();
            internal readonly List<object> PlacedIds = new List<object>();
            internal Dictionary<string, object> FirstPlaced;
            internal Thing FirstThing;
            internal string FirstRefusal;
            internal int Placed;
            internal int AlreadyPresent;
            internal int Refused;
            internal int Errors;
            internal int Accepted;
            internal object Failure;
            internal Watch.Session Session;
            internal string SkipReason;
        }

        /// <summary>
        /// The whole batch: one plan hop (resolve, one map-wide materials scan,
        /// one camera move), ONE watch lead, then the cells in chunks of
        /// <see cref="CellsPerHop"/> with <see cref="HopGapMs"/> of released
        /// main thread between them, then one hop to select and schedule the
        /// close. What a caller pays for the 64th cell is one more
        /// CanPlaceBlueprintAt and one more PlaceBlueprintForBuild.
        /// </summary>
        private static async Task<object> PlaceBatch(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            string def,
            List<IntVec3> cells,
            string rotation,
            string stuff,
            bool godMode,
            bool dryRun,
            bool watch,
            int watchSeconds)
        {
            var run = await ctx.MainThread
                .InvokeAsync(() => PlanBatch(ctx, def, cells, rotation, stuff, godMode, dryRun, watch), cancellationToken)
                .ConfigureAwait(false);

            if (run.Failure != null)
            {
                Stamp(run.Failure, Watch.Skipped("refused"));
                return run.Failure;
            }

            // The batch's ONE lead: the camera is on the middle of the line and
            // the viewer sees the ground before it fills, once, not 64 times.
            if (!dryRun)
                await Watch.Lead(run.Session, cancellationToken).ConfigureAwait(false);

            var hops = 0;
            for (var start = 0; start < run.Cells.Count; start += CellsPerHop)
            {
                if (hops > 0)
                {
                    // Off the main thread, uncancelled on purpose: 20 ms is not
                    // worth a cancellation path that would strand a half-placed
                    // batch and an open menu.
                    await Task.Delay(HopGapMs).ConfigureAwait(false);
                }
                var from = start;
                await ctx.MainThread
                    .InvokeAsync(() => { RunChunk(run, from, CellsPerHop); return (object)null; }, cancellationToken)
                    .ConfigureAwait(false);
                hops++;
            }

            var payload = run.Payload;
            var total = run.Cells.Count;
            payload["wiped"] = run.Wiped;
            payload["framesCancelled"] = run.FramesCancelled;
            payload["placed"] = run.FirstPlaced;
            payload["applied"] = run.Placed > 0;
            payload["canPlace"] = run.Accepted > 0;
            payload["alreadyPlaced"] = run.AlreadyPresent == total;
            // The anchor cell's own row, so a reader of the single-cell shape
            // still finds `rotations[0]`; every cell is in batch.rows.
            payload["rotations"] = run.Rows.Count > 0 ? new List<object> { run.Rows[0] } : new List<object>();
            payload["rotationsEvaluated"] = run.Rows.Count > 0 ? 1 : 0;
            payload["acceptedRotations"] = run.Accepted > 0
                ? new List<object> { RotationNames[run.Rot.AsInt & 3] }
                : new List<object>();

            string outcome;
            if (dryRun)
                outcome = "preview";
            else if (run.Placed > 0 && run.Refused + run.Errors == 0)
                outcome = "placed";
            else if (run.Placed > 0)
                outcome = "partial";
            else if (run.AlreadyPresent > 0 && run.Refused + run.Errors == 0)
                outcome = "already_present";
            else if (run.Errors > 0)
                outcome = "error";
            else
                outcome = "refused";
            payload["outcome"] = outcome;
            payload["detail"] = dryRun
                ? "BATCH PREVIEW: NO blueprint placed; " + run.Accepted + " of " + total
                  + " cells accepted, " + run.AlreadyPresent + " already there, " + run.Refused + " refused."
                : "BATCH: " + run.Placed + " of " + total + " cells placed, " + run.AlreadyPresent
                  + " already there, " + run.Refused + " refused, " + run.Errors + " errored."
                  + (run.FirstRefusal == null ? string.Empty : " First refusal: " + run.FirstRefusal);

            payload["batch"] = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "requested", total },
                { "rotation", RotationNames[run.Rot.AsInt & 3] },
                { "cellsPerHop", CellsPerHop },
                { "hops", hops },
                { "hopGapMs", HopGapMs },
                { "placed", run.Placed },
                { "alreadyPresent", run.AlreadyPresent },
                { "refused", run.Refused },
                { "errors", run.Errors },
                { "accepted", run.Accepted },
                { "placedIds", run.PlacedIds },
                { "firstRefusal", run.FirstRefusal },
                { "rows", run.Rows }
            };

            if (dryRun)
            {
                Stamp(payload, Watch.Skipped("dry run"));
                return payload;
            }
            if (run.Session == null)
            {
                Stamp(payload, Watch.Skipped(run.SkipReason));
                return payload;
            }

            var finished = await ctx.MainThread
                .InvokeAsync(() =>
                {
                    if (run.FirstThing != null)
                        Watch.SelectNow(run.Session, run.FirstThing);
                    return (object)Watch.Finish(run.Session, watchSeconds);
                }, cancellationToken)
                .ConfigureAwait(false);
            payload["watch"] = finished;
            return payload;
        }

        /// <summary>Main thread. Resolve everything the batch shares, scan the
        /// map for materials ONCE, and put the camera on the middle of the
        /// batch. Places nothing.</summary>
        private static BatchRun PlanBatch(IRimBridgeContext ctx, string defSpec, List<IntVec3> cells,
                                          string rotationSpec, string stuffSpec, bool godMode,
                                          bool dryRun, bool wantWatch)
        {
            var run = new BatchRun { Cells = cells, GodMode = godMode, DryRun = dryRun };

            Map map;
            string mapError;
            if (!TryGetMap(out map, out mapError))
            {
                run.Failure = Failure(mapError);
                return run;
            }
            if (string.IsNullOrEmpty(defSpec))
            {
                run.Failure = Failure("def is required: the defName or label of a buildable ThingDef or TerrainDef.");
                return run;
            }
            BuildableDef entDef;
            string kind;
            if (!TryResolveBuildable(defSpec, out entDef, out kind))
            {
                run.Failure = Failure("No ThingDef or TerrainDef matches \"" + defSpec + "\" by defName or label.");
                return run;
            }

            List<int> rotations;
            string rotationError;
            if (!TryParseRotations(rotationSpec, out rotations, out rotationError))
            {
                run.Failure = Failure(rotationError);
                return run;
            }
            if (rotations.Count != 1)
            {
                run.Failure = Failure("A batch needs exactly one rotation: pass rotation=north|east|south|west beside cells, not \""
                                      + (rotationSpec ?? "all") + "\". Sweep the rotations with a single-cell dry run first.");
                return run;
            }

            // Stuff, by exactly the rules a single-cell call uses.
            ThingDef stuffDef = null;
            string stuffNote = null;
            var madeFromStuff = SafeMadeFromStuff(entDef);
            if (!string.IsNullOrEmpty(stuffSpec))
            {
                stuffDef = ResolveThingDef(stuffSpec);
                if (stuffDef == null)
                {
                    run.Failure = Failure("No ThingDef matches the stuff \"" + stuffSpec + "\".");
                    return run;
                }
                if (!madeFromStuff)
                    stuffNote = entDef.defName + " is not made from stuff; the stuff argument is recorded but has no effect.";
            }
            else if (madeFromStuff)
            {
                stuffDef = SafeDefaultStuff(entDef);
                if (stuffDef == null)
                    stuffNote = "This def is made from stuff and GenStuff.DefaultStuffFor returned nothing; placement would produce a blueprint with no material.";
                else
                    stuffNote = "stuff defaulted to GenStuff.DefaultStuffFor(" + entDef.defName + ") = " + stuffDef.defName + ".";
            }

            var blueprintDef = SafeBlueprintDef(entDef);
            var thingDef = entDef as ThingDef;

            Faction player = null;
            if (!dryRun)
            {
                if (blueprintDef == null)
                {
                    run.Failure = Failure(entDef.defName + " has no blueprintDef, so no blueprint can be made for it.");
                    return run;
                }
                // NOT Faction.OfPlayer: its body ends in Log.Error, which pauses.
                try { player = Faction.OfPlayerSilentFail; }
                catch { player = null; }
                if (player == null)
                {
                    run.Failure = Failure("There is no player faction on this map, so the blueprint would belong to nobody.");
                    return run;
                }
            }

            run.Map = map;
            run.EntDef = entDef;
            run.StuffDef = stuffDef;
            run.BlueprintDef = blueprintDef;
            run.Rot = new Rot4(rotations[0]);
            run.IsCooler = thingDef != null && IsClass(thingDef, typeof(Building_Cooler));
            run.IsVent = thingDef != null && IsClass(thingDef, typeof(Building_Vent));
            run.Player = player;

            var payload = new Dictionary<string, object>
            {
                { "success", true },
                { "tool", ToolName },
                { "dryRun", dryRun },
                { "def", new Dictionary<string, object>
                    {
                        { "defName", entDef.defName },
                        { "label", entDef.label },
                        { "kind", kind }
                    } },
                { "position", BridgeCommon.Pos(cells[0]) },
                { "size", SizeBlock(entDef) },
                { "rotatable", thingDef == null ? (bool?)null : SafeRotatable(thingDef) },
                { "researchFinished", SafeResearchFinished(entDef) },
                { "buildableByPlayer", SafeBuildableByPlayer(entDef) },
                { "hasBlueprintDef", blueprintDef != null },
                { "madeFromStuff", madeFromStuff },
                { "stuff", stuffDef == null ? null : new Dictionary<string, object>
                    {
                        { "defName", stuffDef.defName },
                        { "label", stuffDef.label },
                        { "allowedForThisDef", StuffAllowed(entDef, stuffDef) }
                    } },
                { "costList", CostList(entDef, stuffDef) },
                // ONE pass over the map for the whole batch, costed for every
                // requested cell: 64 walls need 64 walls' worth of blocks, and
                // reading one wall's cost beside a 64-cell ask is how a caller
                // starts a line it cannot finish.
                { "materials", Materials(map, entDef, stuffDef, cells.Count) },
                { "thingClass", thingDef == null || thingDef.thingClass == null ? null : thingDef.thingClass.Name },
                { "isCooler", run.IsCooler },
                { "isVent", run.IsVent },
                { "rotationsEvaluated", 0 },
                { "acceptedRotations", new List<object>() },
                { "rotations", new List<object>() },
                { "canPlace", false },
                { "applied", false },
                { "outcome", dryRun ? "preview" : "pending" },
                { "detail", "Batch planned; no cell has been evaluated yet." },
                { "placed", null },
                { "alreadyPlaced", false },
                { "wiped", new List<object>() },
                { "framesCancelled", new List<object>() },
                { "notes", Notes() }
            };
            if (stuffNote != null)
                payload["stuffNote"] = stuffNote;
            run.Payload = payload;

            if (dryRun)
                run.SkipReason = "dry run";
            else if (!wantWatch)
                run.SkipReason = "watch:false";
            else
                run.Session = Watch.OpenAtCell(ctx, Midpoint(cells));

            return run;
        }

        /// <summary>The middle of the batch's bounding rect: for a wall line the
        /// camera then holds the whole line, not one end of it.</summary>
        private static IntVec3 Midpoint(List<IntVec3> cells)
        {
            int minX = cells[0].x, maxX = cells[0].x, minZ = cells[0].z, maxZ = cells[0].z;
            foreach (var c in cells)
            {
                if (c.x < minX) minX = c.x;
                if (c.x > maxX) maxX = c.x;
                if (c.z < minZ) minZ = c.z;
                if (c.z > maxZ) maxZ = c.z;
            }
            return new IntVec3((minX + maxX) / 2, 0, (minZ + maxZ) / 2);
        }

        /// <summary>
        /// Main thread, ONE chunk: up to <see cref="CellsPerHop"/> cells
        /// evaluated and -- on a real run -- placed. Bounded on purpose. A hop
        /// runs inside Verse.Root.Update, which is the game's own tick and the
        /// queue every other bridge call is pumped from, so an unbounded loop
        /// here would stall the colony and every event with it.
        /// </summary>
        private static void RunChunk(BatchRun run, int from, int count)
        {
            var map = run.Map;
            var last = Math.Min(from + count, run.Cells.Count);
            for (var i = from; i < last; i++)
            {
                var cell = run.Cells[i];
                Dictionary<string, object> row;
                if (!cell.InBounds(map))
                {
                    row = new Dictionary<string, object>
                    {
                        { "rotation", RotationNames[run.Rot.AsInt & 3] },
                        { "rotationInt", run.Rot.AsInt },
                        { "accepted", false },
                        { "reason", "(" + cell.x + "," + cell.z + ") is not a cell on this map." },
                        { "occupiedRect", null },
                        { "occupiedCells", new List<object>() },
                        { "blockingThings", new List<object>() },
                        { "blockingThingCount", 0 },
                        { "wipeOnPlace", new List<object>() },
                        { "framesCancelledOnPlace", new List<object>() },
                        { "identicalBlueprintExists", false }
                    };
                }
                else
                {
                    row = EvaluateRotation(map, run.EntDef, run.BlueprintDef, cell, run.Rot,
                                           run.StuffDef, run.GodMode, run.IsCooler, run.IsVent);
                }

                row["x"] = cell.x;
                row["z"] = cell.z;
                // Constant row shape: a cell that placed nothing still says so
                // with the same keys, because absent and false read alike in JSON.
                row["placed"] = null;
                row["wiped"] = new List<object>();
                row["framesCancelled"] = new List<object>();
                row["error"] = null;

                var accepted = Equals(row["accepted"], true);
                if (accepted)
                    run.Accepted++;

                string outcome;
                if (Equals(row["identicalBlueprintExists"], true))
                {
                    // The caller's end state already holds. Not an error, and
                    // never a second blueprint on the same cell.
                    outcome = "already_present";
                    run.AlreadyPresent++;
                }
                else if (!accepted)
                {
                    outcome = "refused";
                    run.Refused++;
                    if (run.FirstRefusal == null)
                    {
                        var why = row["reason"] as string;
                        run.FirstRefusal = string.IsNullOrEmpty(why)
                            ? "(" + cell.x + "," + cell.z + "): no reason given"
                            : "(" + cell.x + "," + cell.z + "): " + why;
                    }
                }
                else if (run.DryRun)
                {
                    outcome = "preview";
                }
                else
                {
                    var result = PlaceOne(map, run.EntDef, cell, run.Rot, run.StuffDef, run.Player);
                    row["wiped"] = result.Wiped;
                    row["framesCancelled"] = result.FramesCancelled;
                    run.Wiped.AddRange(result.Wiped);
                    run.FramesCancelled.AddRange(result.FramesCancelled);
                    if (result.Error != null)
                    {
                        outcome = "error";
                        run.Errors++;
                        row["error"] = result.Error;
                    }
                    else
                    {
                        outcome = "placed";
                        run.Placed++;
                        row["placed"] = result.Placed;
                        object id;
                        if (result.Placed != null && result.Placed.TryGetValue("thingIDNumber", out id))
                            run.PlacedIds.Add(id);
                        if (run.FirstPlaced == null)
                        {
                            run.FirstPlaced = result.Placed;
                            run.FirstThing = result.Thing;
                        }
                    }
                }

                row["outcome"] = outcome;
                run.Rows.Add(row);
            }
        }

        /// <summary>Resolve, evaluate every rotation and - unless dryRun - place.
        /// `planOnly` stops after the last check and before the first
        /// destructive step, so hop 1 can learn whether a real run would place
        /// anything without placing it. `onPlaced` hands the new blueprint back
        /// to the watch step inside the same hop it was made in.</summary>
        private static object Build(string defSpec, int x, int z, string rotationSpec,
                                    string stuffSpec, bool godMode, bool dryRun,
                                    bool planOnly, Action<Thing> onPlaced)
        {
            if (!TryGetMap(out var map, out var mapError))
                return Failure(mapError);

            if (string.IsNullOrEmpty(defSpec))
                return Failure("def is required: the defName or label of a buildable ThingDef or TerrainDef.");

            BuildableDef entDef;
            string kind;
            if (!TryResolveBuildable(defSpec, out entDef, out kind))
                return Failure("No ThingDef or TerrainDef matches \"" + defSpec + "\" by defName or label.");

            var center = new IntVec3(x, 0, z);
            if (x < 0 || z < 0 || !center.InBounds(map))
                return Failure("(" + x + "," + z + ") is not a cell on this map.");

            List<int> rotations;
            string rotationError;
            if (!TryParseRotations(rotationSpec, out rotations, out rotationError))
                return Failure(rotationError);

            // Stuff. GenStuff.DefaultStuffFor is what the architect menu itself
            // pre-selects, so defaulting to it makes a bridge call agree with what
            // a player clicking the same button would get.
            ThingDef stuffDef = null;
            string stuffNote = null;
            var madeFromStuff = SafeMadeFromStuff(entDef);
            if (!string.IsNullOrEmpty(stuffSpec))
            {
                stuffDef = ResolveThingDef(stuffSpec);
                if (stuffDef == null)
                    return Failure("No ThingDef matches the stuff \"" + stuffSpec + "\".");
                if (!madeFromStuff)
                    stuffNote = entDef.defName + " is not made from stuff; the stuff argument is recorded but has no effect.";
            }
            else if (madeFromStuff)
            {
                stuffDef = SafeDefaultStuff(entDef);
                if (stuffDef == null)
                    stuffNote = "This def is made from stuff and GenStuff.DefaultStuffFor returned nothing; placement would produce a blueprint with no material.";
                else
                    stuffNote = "stuff defaulted to GenStuff.DefaultStuffFor(" + entDef.defName + ") = " + stuffDef.defName + ".";
            }

            var blueprintDef = SafeBlueprintDef(entDef);
            var thingDef = entDef as ThingDef;
            var isCooler = thingDef != null && IsClass(thingDef, typeof(Building_Cooler));
            var isVent = thingDef != null && IsClass(thingDef, typeof(Building_Vent));

            var rotationRows = new List<object>();
            var acceptedRotations = new List<int>();

            foreach (var r in rotations)
            {
                var rot = new Rot4(r);
                var row = EvaluateRotation(map, entDef, blueprintDef, center, rot, stuffDef, godMode, isCooler, isVent);
                if (Equals(row["accepted"], true))
                    acceptedRotations.Add(r);
                rotationRows.Add(row);
            }

            var payload = new Dictionary<string, object>
            {
                { "success", true },
                { "tool", ToolName },
                { "dryRun", dryRun },
                { "def", new Dictionary<string, object>
                    {
                        { "defName", entDef.defName },
                        { "label", entDef.label },
                        { "kind", kind }
                    } },
                { "position", BridgeCommon.Pos(center) },
                { "size", SizeBlock(entDef) },
                // rotatable is a ThingDef flag; a TerrainDef has no such concept,
                // so it is null rather than a fabricated false.
                { "rotatable", thingDef == null ? (bool?)null : SafeRotatable(thingDef) },
                { "researchFinished", SafeResearchFinished(entDef) },
                { "buildableByPlayer", SafeBuildableByPlayer(entDef) },
                { "hasBlueprintDef", SafeBlueprintDef(entDef) != null },
                { "madeFromStuff", madeFromStuff },
                { "stuff", stuffDef == null ? null : new Dictionary<string, object>
                    {
                        { "defName", stuffDef.defName },
                        { "label", stuffDef.label },
                        { "allowedForThisDef", StuffAllowed(entDef, stuffDef) }
                    } },
                { "costList", CostList(entDef, stuffDef) },
                // Computed ONCE here, before any placement, not once per
                // rotation: the cost does not depend on which way it faces.
                { "materials", Materials(map, entDef, stuffDef) },
                { "thingClass", thingDef == null || thingDef.thingClass == null ? null : thingDef.thingClass.Name },
                { "isCooler", isCooler },
                { "isVent", isVent },
                { "rotationsEvaluated", rotations.Count },
                { "acceptedRotations", acceptedRotations.Select(r => (object)RotationNames[r]).ToList() },
                { "rotations", rotationRows },
                { "canPlace", acceptedRotations.Count > 0 },
                { "applied", false },
                { "outcome", dryRun ? "preview" : "pending" },
                { "detail", dryRun
                    ? "PREVIEW ONLY: no blueprint placed; " + acceptedRotations.Count + "/" + rotations.Count + " rotations accepted."
                    : "Placement checks completed; no blueprint has been placed yet." },
                { "placed", null },
                { "alreadyPlaced", false },
                // Present on every response, empty on a dry run: a caller must be
                // able to tell "nothing was destroyed" from "the tool did not say".
                { "wiped", new List<object>() },
                { "framesCancelled", new List<object>() },
                { "notes", Notes() }
            };
            if (stuffNote != null)
                payload["stuffNote"] = stuffNote;

            if (dryRun)
                return payload;

            // ------------------------------------------------------------ place
            if (rotations.Count != 1)
            {
                payload["success"] = false;
                payload["error"] = "A real placement needs exactly one rotation. Pass rotation=north|east|south|west, not \"all\".";
                payload["outcome"] = "refused";
                payload["detail"] = payload["error"];
                return payload;
            }

            var chosen = new Rot4(rotations[0]);
            var chosenRow = (Dictionary<string, object>)rotationRows[0];

            if (Equals(chosenRow["identicalBlueprintExists"], true))
            {
                // Not an error: the caller asked for a blueprint that is already
                // there, and the desired end state holds.
                payload["alreadyPlaced"] = true;
                payload["note"] = "An identical blueprint (or the finished thing) already exists at this cell and rotation; nothing was placed.";
                payload["outcome"] = "already_present";
                payload["detail"] = payload["note"];
                return payload;
            }

            if (!Equals(chosenRow["accepted"], true))
            {
                payload["success"] = false;
                payload["error"] = "The game refuses this placement: " + (chosenRow["reason"] as string ?? "(no reason given)");
                payload["outcome"] = "refused";
                payload["detail"] = payload["error"];
                return payload;
            }

            if (SafeBlueprintDef(entDef) == null)
            {
                payload["success"] = false;
                payload["error"] = entDef.defName + " has no blueprintDef, so no blueprint can be made for it.";
                payload["outcome"] = "refused";
                payload["detail"] = payload["error"];
                return payload;
            }

            // NOT Faction.OfPlayer: its body is get_OfPlayerSilentFail followed by
            // Verse.Log.Error, and Log.Error calls TickManager.Pause().
            Faction player;
            try { player = Faction.OfPlayerSilentFail; }
            catch { player = null; }
            if (player == null)
            {
                payload["success"] = false;
                payload["error"] = "There is no player faction on this map, so the blueprint would belong to nobody.";
                payload["outcome"] = "refused";
                payload["detail"] = payload["error"];
                return payload;
            }

            // Hop 1 stops here: everything above is a check, everything below
            // destroys or spawns.
            if (planOnly)
                return payload;

            // ONE placement path for a single cell and for every cell of a batch:
            // PlaceOne is the only code in this file that destroys or spawns.
            var single = PlaceOne(map, entDef, center, chosen, stuffDef, player);
            payload["wiped"] = single.Wiped;
            payload["framesCancelled"] = single.FramesCancelled;
            if (single.Error != null)
            {
                payload["success"] = false;
                payload["error"] = single.Error;
                payload["outcome"] = "error";
                payload["detail"] = payload["error"];
                return payload;
            }
            payload["placed"] = single.Placed;
            payload["applied"] = true;
            payload["outcome"] = "placed";
            payload["detail"] = "PLACED: blueprint created at the requested cell and rotation.";
            if (onPlaced != null)
                onPlaced(single.Thing);
            return payload;
        }

        /// <summary>What one placement did. Error non-null means nothing stands
        /// there and the caller says so; Wiped and FramesCancelled are truthful
        /// either way, because a throw can happen after the wipe.</summary>
        private sealed class PlacementResult
        {
            internal Dictionary<string, object> Placed;
            internal List<object> Wiped = new List<object>();
            internal List<object> FramesCancelled = new List<object>();
            internal Thing Thing;
            internal string Error;
        }

        /// <summary>
        /// Main thread. The three destructive steps, in
        /// `Designator_Build.DesignateSingleCell`'s own order, for ONE cell. Every
        /// placement this tool makes -- single or one cell of a batch -- comes
        /// through here, so the two can never drift apart.
        /// </summary>
        private static PlacementResult PlaceOne(Map map, BuildableDef entDef, IntVec3 center,
                                                Rot4 chosen, ThingDef stuffDef, Faction player)
        {
            var result = new PlacementResult();
            var wiped = result.Wiped;
            var cancelledFrames = result.FramesCancelled;
            var bpDef = SafeBlueprintDef(entDef);

            try
            {
                // STEP 1, exactly as Designator_Build.DesignateSingleCell does it:
                // a Frame in the ANCHOR CELL whose replaceTags overlap the
                // blueprint's is cancelled, not wiped. DestroyMode.Cancel is what
                // returns the materials already hauled to it. The game checks the
                // one cell rather than the whole footprint, so this does too.
                foreach (var t in map.thingGrid.ThingsListAt(center).ToList())
                {
                    var frame = t as Frame;
                    if (frame == null || frame.Destroyed || !TagsIntersect(bpDef, frame.def))
                        continue;
                    cancelledFrames.Add(ThingRow(frame));
                    frame.Destroy(DestroyMode.Cancel);
                }

                // STEP 2. Record what the wipe will take BEFORE taking it -- a
                // destroyed Thing no longer has a readable position or label -- then
                // wipe with DestroyMode.Deconstruct.
                //
                // This is not optional tidiness. GenSpawn.Spawn, which
                // PlaceBlueprintForBuild calls, defaults to WipeMode.Vanish and runs
                // WipeExistingThings(..., DestroyMode.Vanish) itself. Skipping this
                // step does not spare anything; it only downgrades the refund.
                if (bpDef != null)
                {
                    var seenIds = new HashSet<int>();
                    foreach (var c in GenAdj.CellsOccupiedBy(center, chosen, bpDef.Size))
                    {
                        if (!c.InBounds(map))
                            continue;
                        foreach (var t in map.thingGrid.ThingsListAt(c).ToList())
                        {
                            if (t == null || t.def == null || t.Destroyed)
                                continue;
                            if (!GenSpawn.SpawningWipes(bpDef, t.def))
                                continue;
                            // A multi-cell thing appears once per cell it covers.
                            if (!seenIds.Add(t.thingIDNumber))
                                continue;
                            wiped.Add(ThingRow(t));
                        }
                    }
                    GenSpawn.WipeExistingThings(center, chosen, bpDef, map, DestroyMode.Deconstruct);
                }

                // STEP 3.
                var blueprint = GenConstruct.PlaceBlueprintForBuild(entDef, center, map, chosen, player, stuffDef);
                if (blueprint == null)
                {
                    result.Error = "PlaceBlueprintForBuild returned nothing.";
                    return result;
                }

                result.Thing = blueprint;
                result.Placed = new Dictionary<string, object>
                {
                    { "thingIDNumber", SafeInt(() => blueprint.thingIDNumber) },
                    { "defName", blueprint.def != null ? blueprint.def.defName : null },
                    { "label", SafeThingLabel(blueprint) },
                    { "buildDefName", entDef.defName },
                    { "position", PositionOf(blueprint) },
                    { "rotation", SafeRotationHuman(blueprint) },
                    { "rotationWord", RotationNames[chosen.AsInt & 3] },
                    { "stuff", stuffDef == null ? null : stuffDef.defName },
                    { "faction", SafeFactionName(player) }
                };
                return result;
            }
            catch (Exception e)
            {
                result.Error = "Placement threw: " + e.Message;
                return result;
            }
        }

        // ============================================================= rotations

        private static Dictionary<string, object> EvaluateRotation(
            Map map, BuildableDef entDef, ThingDef blueprintDef, IntVec3 center, Rot4 rot,
            ThingDef stuffDef, bool godMode, bool isCooler, bool isVent)
        {
            var row = new Dictionary<string, object>
            {
                { "rotation", RotationNames[rot.AsInt & 3] },
                { "rotationInt", rot.AsInt }
            };

            var accepted = false;
            var reason = string.Empty;
            try
            {
                // The same call Designator_Build.CanDesignateCell makes, with the
                // rotation supplied instead of read off a protected field.
                var report = GenConstruct.CanPlaceBlueprintAt(entDef, center, rot, map, godMode, null, null, stuffDef);
                accepted = report.Accepted;
                reason = accepted ? string.Empty : (report.Reason ?? string.Empty);
            }
            catch (Exception e)
            {
                reason = "CanPlaceBlueprintAt threw: " + e.Message;
            }
            row["accepted"] = accepted;
            row["reason"] = reason;

            CellRect rect;
            var cells = new List<object>();
            try
            {
                rect = GenAdj.OccupiedRect(center, rot, entDef.Size);
                row["occupiedRect"] = new Dictionary<string, object>
                {
                    { "minX", rect.minX }, { "minZ", rect.minZ },
                    { "maxX", rect.maxX }, { "maxZ", rect.maxZ },
                    { "width", rect.Width }, { "height", rect.Height }
                };
                foreach (var c in rect)
                    cells.Add(BridgeCommon.Pos(c));
            }
            catch
            {
                rect = CellRect.SingleCell(center);
                row["occupiedRect"] = null;
            }
            row["occupiedCells"] = cells;

            // Everything standing in the footprint. GenConstruct.BlocksConstruction
            // needs an existing constructible Thing, which does not exist yet at
            // dry-run time, so GenSpawn.SpawningWipes -- the test it is built on --
            // is used directly.
            //
            // THE DEF PASSED HERE MATTERS, and getting it wrong is what reported a
            // stone chunk as about to be destroyed. What gets placed is the
            // BLUEPRINT, and WipeExistingThings is called with entDef.blueprintDef.
            // Passing entDef instead takes the SpawningWipes branch
            // `oldDef.category == Item && newDef.passability == Impassable &&
            // newDef.surfaceType == None`, which a Cooler satisfies and a blueprint
            // (Standable, Ethereal) does not. A blueprint wipes no item at all; the
            // chunk is hauled away by the builder before work starts.
            var blockers = new List<object>();
            var wipeOnPlace = new List<object>();
            var framesCancelled = new List<object>();
            var identical = false;
            try
            {
                foreach (var c in rect)
                {
                    if (!c.InBounds(map))
                        continue;
                    foreach (var t in map.thingGrid.ThingsListAtFast(c))
                    {
                        if (t == null || t.def == null)
                            continue;
                        if (blockers.Any(b => Equals(((Dictionary<string, object>)b)["thingIDNumber"], t.thingIDNumber)))
                            continue;

                        // What the blueprint destroys the moment it is placed.
                        var wipes = false;
                        try { wipes = blueprintDef != null && GenSpawn.SpawningWipes(blueprintDef, t.def); }
                        catch { }

                        // What the FINISHED building would displace but the
                        // blueprint does not: chunks and other loose items. The
                        // builder hauls these out before construction. They are not
                        // destroyed and they are not a refusal, but they do have to
                        // go, so they are named under their own flag rather than
                        // being reported as wiped.
                        var finishedWipes = false;
                        try { finishedWipes = GenSpawn.SpawningWipes(entDef, t.def); }
                        catch { }

                        var haulFirst = false;
                        try
                        {
                            haulFirst = !wipes
                                        && t.def.category == ThingCategory.Item
                                        && finishedWipes;
                        }
                        catch { }

                        // Designator_Build cancels a Frame in the ANCHOR cell whose
                        // replaceTags overlap the blueprint's -- that is how
                        // rebuilding a wall as a different wall works. It checks the
                        // one cell, not the whole footprint; replicated exactly.
                        var frameCancel = false;
                        try
                        {
                            frameCancel = t is Frame && c == center
                                          && TagsIntersect(blueprintDef, t.def);
                        }
                        catch { }

                        if (t.Position == center && SameRotation(t, rot) &&
                            (t.def == entDef || t.def.entityDefToBuild == entDef))
                            identical = true;

                        blockers.Add(new Dictionary<string, object>
                        {
                            { "defName", t.def.defName },
                            { "label", SafeThingLabel(t) },
                            { "thingIDNumber", t.thingIDNumber },
                            { "category", t.def.category.ToString() },
                            { "position", PositionOf(t) },
                            { "isBlueprint", t is Blueprint },
                            { "isFrame", t is Frame },
                            { "wouldBeWiped", wipes },
                            { "wipedWhenBuilt", finishedWipes },
                            { "isCrop", IsCrop(t) },
                            { "effectOnPlace", EffectOnPlace(t, wipes, frameCancel, haulFirst, finishedWipes) },
                            { "mustBeHauledFirst", haulFirst },
                            { "frameWouldBeCancelled", frameCancel }
                        });

                        if (wipes)
                            wipeOnPlace.Add(ThingRow(t));
                        if (frameCancel)
                            framesCancelled.Add(ThingRow(t));
                    }
                }
            }
            catch { }
            row["blockingThings"] = blockers;
            row["blockingThingCount"] = blockers.Count;
            // The dry-run prediction of the two destructive steps a real placement
            // performs, computed with the same predicates the real run uses, so a
            // dry run and a real run cannot disagree about what goes.
            row["wipeOnPlace"] = wipeOnPlace;
            row["framesCancelledOnPlace"] = framesCancelled;
            // "Identical blueprint already exists" is a desired end state, not a
            // failure, so it is flagged separately from `accepted`.
            row["identicalBlueprintExists"] = identical;

            if (isCooler || isVent)
                row["sides"] = Sides(map, center, rot, isCooler);

            return row;
        }

        /// <summary>
        /// The two cells a cooler or a vent actually acts on, computed exactly as
        /// the game does. For a cooler: south-of-rotation is the room that gets
        /// cooled, north-of-rotation is where the heat goes. For a vent the two
        /// sides are symmetric (EqualizeTemperaturesThroughBuilding averages the
        /// rooms at +/- Rotation.FacingCell), so they are named a and b.
        /// </summary>
        private static Dictionary<string, object> Sides(Map map, IntVec3 center, Rot4 rot, bool isCooler)
        {
            var south = center + IntVec3.South.RotatedBy(rot);
            var north = center + IntVec3.North.RotatedBy(rot);

            if (isCooler)
            {
                return new Dictionary<string, object>
                {
                    { "cold", CellFace(map, south, "the room this cooler would chill") },
                    { "hot", CellFace(map, north, "where this cooler would dump its heat") },
                    { "rule", "Building_Cooler.TickRare cools Position + IntVec3.South.RotatedBy(Rotation) and pushes heat to Position + IntVec3.North.RotatedBy(Rotation). Rot4.North is the identity rotation." }
                };
            }

            return new Dictionary<string, object>
            {
                { "a", CellFace(map, north, "one side of the vent") },
                { "b", CellFace(map, south, "the other side of the vent") },
                { "rule", "Building_Vent.TickRare calls GenTemperature.EqualizeTemperaturesThroughBuilding(this, 14f, twoWay: true), which averages the rooms at cell + Rotation.FacingCell and cell - Rotation.FacingCell. Neither side is privileged." }
            };
        }

        private static Dictionary<string, object> CellFace(Map map, IntVec3 c, string what)
        {
            var face = new Dictionary<string, object>
            {
                { "x", c.x }, { "z", c.z },
                { "what", what },
                { "inBounds", false },
                { "roomRole", null },
                { "isOutdoors", null },
                { "psychologicallyOutdoors", null },
                { "temperature", null },
                { "impassable", null }
            };

            try
            {
                if (!c.InBounds(map))
                    return face;
                face["inBounds"] = true;

                var room = c.GetRoom(map);
                if (room != null)
                {
                    // UsesOutdoorTemperature is the flag that governs whether the
                    // room's temperature is slaved to the outdoors -- the question a
                    // "did I put the hot side outside" check is actually asking.
                    // PsychologicallyOutdoors is RimWorld's separate mood flag and
                    // is reported under its own name rather than conflated with it.
                    face["isOutdoors"] = SafeBool(() => room.UsesOutdoorTemperature);
                    face["psychologicallyOutdoors"] = SafeBool(() => room.PsychologicallyOutdoors);
                    face["roomRole"] = SafeString(() => room.Role == null ? null : room.Role.defName);
                    face["roomCellCount"] = SafeInt(() => room.CellCount);
                }

                face["temperature"] = SafeFloat(() => c.GetTemperature(map));
                // Both a cooler and a vent do nothing at all when either side is
                // impassable -- Building_Cooler.TickRare returns early -- so it is
                // reported next to the temperature rather than left to be inferred.
                face["impassable"] = SafeBool(() => c.Impassable(map));
            }
            catch { }

            return face;
        }

        // =============================================================== helpers

        private static bool TryParseRotations(string spec, out List<int> rotations, out string error)
        {
            rotations = new List<int>();
            error = null;
            var text = (spec ?? "all").Trim().ToLowerInvariant();

            if (text.Length == 0 || text == "all")
            {
                rotations.AddRange(new[] { 0, 1, 2, 3 });
                return true;
            }

            // NOT Rot4.FromString: it Log.Errors on anything it does not recognise,
            // and Log.Error calls TickManager.Pause().
            for (var i = 0; i < RotationNames.Length; i++)
            {
                if (text == RotationNames[i] || text == i.ToString())
                {
                    rotations.Add(i);
                    return true;
                }
            }

            error = "rotation must be north, east, south, west, 0-3, or 'all'. Got: " + spec;
            return false;
        }

        private static bool TryResolveBuildable(string spec, out BuildableDef def, out string kind)
        {
            def = null;
            kind = null;
            var trimmed = spec.Trim();

            var thing = ResolveThingDef(trimmed);
            if (thing != null)
            {
                def = thing;
                kind = "ThingDef";
                return true;
            }

            try
            {
                var terrain = DefDatabase<TerrainDef>.GetNamedSilentFail(trimmed);
                if (terrain == null)
                {
                    terrain = DefDatabase<TerrainDef>.AllDefsListForReading
                        .FirstOrDefault(d => string.Equals(d.label, trimmed, StringComparison.OrdinalIgnoreCase));
                }
                if (terrain != null)
                {
                    def = terrain;
                    kind = "TerrainDef";
                    return true;
                }
            }
            catch { }

            return false;
        }

        private static ThingDef ResolveThingDef(string spec)
        {
            try
            {
                // GetNamedSilentFail, not GetNamed: the latter logs an error for a
                // miss, and a miss is an ordinary outcome when the caller passed a
                // label rather than a defName.
                var byName = DefDatabase<ThingDef>.GetNamedSilentFail(spec);
                if (byName != null)
                    return byName;
                return DefDatabase<ThingDef>.AllDefsListForReading
                    .FirstOrDefault(d => string.Equals(d.label, spec, StringComparison.OrdinalIgnoreCase));
            }
            catch { return null; }
        }

        /// <summary>
        /// The stuff it is SAFE to cost this def with.
        ///
        /// <c>CostListCalculator.CostListAdjusted</c> carries two
        /// <c>Log.Error</c> calls and <c>Verse.Log.Error</c> pauses the colony.
        /// <c>errorOnNullStuff: false</c> silences the first (a stuffed def with
        /// no stuff). The second — a non-null stuff on a def that is not
        /// <c>MadeFromStuff</c> — is <b>not</b> gated by that flag at all, and
        /// this tool reaches it whenever a caller passes <c>stuff</c> for a Cooler
        /// or a Vent, which it explicitly allows and merely notes. So the stuff is
        /// dropped here rather than handed to a method that would log it.
        /// </summary>
        private static ThingDef CostStuff(BuildableDef entDef, ThingDef stuffDef)
        {
            return SafeMadeFromStuff(entDef) ? stuffDef : null;
        }

        private static List<object> CostList(BuildableDef entDef, ThingDef stuffDef)
        {
            var rows = new List<object>();
            try
            {
                // errorOnNullStuff: false. The default is TRUE and logs (and
                // therefore pauses the game) when a stuffed def is costed with a
                // null stuff -- which happens whenever DefaultStuffFor found none.
                //
                // CostListAdjusted has a SECOND Log.Error that errorOnNullStuff
                // does NOT gate: "got AdjustedCostList for X with stuff Y but is
                // not MadeFromStuff". This tool accepts a stuff argument on a def
                // that is not made from stuff and merely notes it, so that branch
                // was reachable and would have paused the colony. The stuff is
                // dropped before the call instead.
                var cost = entDef.CostListAdjusted(CostStuff(entDef, stuffDef), false);
                if (cost == null)
                    return rows;
                foreach (var item in cost)
                {
                    if (item == null || item.thingDef == null)
                        continue;
                    rows.Add(new Dictionary<string, object>
                    {
                        { "defName", item.thingDef.defName },
                        { "label", item.thingDef.label },
                        { "count", item.count }
                    });
                }
            }
            catch { }
            return rows;
        }

        /// <summary>
        /// Can the colony actually build this, right now, with what is lying on
        /// the map? Answered at placement time, for a dry run as much as for a
        /// real placement, because "the game will accept this blueprint here" and
        /// "there is steel to finish it" are two different questions and only the
        /// first was ever answered.
        ///
        /// ONE pass over the map's things for the whole call, not one per
        /// rotation: the cost list does not depend on which way the thing faces.
        ///
        /// Computed BEFORE anything is placed (this runs while the payload is
        /// built, and the real placement happens further down), so on a real run
        /// `reservedByOtherBlueprints` genuinely means OTHER blueprints -- the
        /// one this call is about to create is not counted against itself.
        /// </summary>
        private static Dictionary<string, object> Materials(Map map, BuildableDef entDef, ThingDef stuffDef, int forCells = 1)
        {
            var block = new Dictionary<string, object>(StringComparer.Ordinal);
            var rows = new List<object>();
            // A batch costs its cell count: one wall's worth of blocks read
            // beside a 64-cell ask is how a caller starts a line it cannot
            // finish. perCellNeeded keeps the single-building number readable.
            if (forCells < 1)
                forCells = 1;

            List<ThingDefCountClass> cost = null;
            try
            {
                // errorOnNullStuff: false, and the stuff dropped for a def that
                // is not made from stuff -- for both reasons CostList states.
                // Either Log.Error would pause the colony.
                cost = entDef == null ? null : entDef.CostListAdjusted(CostStuff(entDef, stuffDef), false);
            }
            catch { cost = null; }

            if (cost == null)
            {
                block["rows"] = rows;
                block["forCells"] = forCells;
                block["canBuildNow"] = null;
                block["missing"] = null;
                block["unreadable"] = true;
                block["note"] = "CostListAdjusted returned nothing, so what this needs could not be read. "
                              + "canBuildNow null means NOT KNOWN, never 'yes'.";
                return block;
            }

            // Faction.OfPlayerSilentFail, never OfPlayer: OfPlayer ends in
            // Log.Error and Log.Error pauses the colony.
            Faction player;
            try { player = Faction.OfPlayerSilentFail; }
            catch { player = null; }

            var reserved = BridgeCommon.OutstandingConstructionDeficit(map);

            var shortfalls = new List<string>();
            var unscanned = new List<string>();
            var canBuildNow = true;

            foreach (var item in cost)
            {
                if (item == null || item.thingDef == null)
                    continue;

                var perCell = item.count;
                var needed = perCell * forCells;
                var onMap = 0;
                var forbidden = 0;

                // ListerThings.ThingsOfDef opens with a Log.ErrorOnce for
                // ThingDefOf.MinifiedThing, and Log.Error pauses the colony --
                // once per session is still once. Nothing vanilla costs a
                // MinifiedThing, so this is defensive; when it does fire the row
                // is left at zero on hand, which reports a shortfall rather than
                // a confident "you have it", and the block says so out loud.
                var unscannable = BridgeCommon.Try(() => item.thingDef == ThingDefOf.MinifiedThing, false);
                if (unscannable)
                    unscanned.Add(item.thingDef.defName);

                try
                {
                    if (unscannable)
                        throw new InvalidOperationException("not scanned");
                    var stacks = map.listerThings.ThingsOfDef(item.thingDef);
                    if (stacks != null)
                    {
                        for (var i = 0; i < stacks.Count; i++)
                        {
                            var t = stacks[i];
                            if (t == null || !t.Spawned)
                                continue;
                            // Ours or nobody's. A trader's crate of steel and a
                            // raider's dropped weapon are on the map and are not
                            // material this colony can spend.
                            var owner = BridgeCommon.Try(() => t.Faction, (Faction)null);
                            if (owner != null && owner != player)
                                continue;
                            var count = Math.Max(1, BridgeCommon.Try(() => t.stackCount, 1));
                            onMap += count;
                            // ThingWithComps.compForbiddable is a plain public
                            // field cached at InitializeComps, and
                            // CompForbiddable.Forbidden is a pure field read. The
                            // obvious call, ForbidUtility.IsForbidden(Thing,
                            // Faction), opens with a Faction.OfPlayer comparison,
                            // and OfPlayer ends in Log.Error, which pauses.
                            var withComps = t as ThingWithComps;
                            if (withComps != null)
                            {
                                var comp = BridgeCommon.Try(() => withComps.GetComp<CompForbiddable>(), (CompForbiddable)null);
                                if (comp != null && BridgeCommon.Try(() => comp.Forbidden, false))
                                    forbidden += count;
                            }
                        }
                    }
                }
                catch { /* onMap/forbidden stay at what was counted before the throw */ }

                int claimed;
                if (!reserved.TryGetValue(item.thingDef, out claimed))
                    claimed = 0;

                var available = onMap - forbidden - claimed;
                if (available < 0)
                    available = 0;
                var shortfall = needed - available;
                if (shortfall < 0)
                    shortfall = 0;

                if (shortfall > 0)
                {
                    canBuildNow = false;
                    var why = new List<string>();
                    why.Add("have " + available);
                    if (forbidden > 0)
                        why.Add(forbidden + " forbidden");
                    if (claimed > 0)
                        why.Add(claimed + " reserved");
                    shortfalls.Add(shortfall + " " + (item.thingDef.label ?? item.thingDef.defName)
                                   + " (" + string.Join(", ", why.ToArray()) + ")");
                }

                rows.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    { "defName", item.thingDef.defName },
                    { "label", item.thingDef.label },
                    { "needed", needed },
                    { "perCellNeeded", perCell },
                    { "onMap", onMap },
                    { "forbidden", forbidden },
                    { "reservedByOtherBlueprints", claimed },
                    { "available", available },
                    { "shortfall", shortfall }
                });
            }

            block["rows"] = rows;
            block["forCells"] = forCells;
            block["canBuildNow"] = canBuildNow;
            // Empty string, not null, when nothing is missing: "" and "not asked"
            // must not read the same.
            block["missing"] = shortfalls.Count == 0 ? string.Empty
                             : "missing: " + string.Join(", ", shortfalls.ToArray());
            block["unreadable"] = false;
            block["note"] = unscanned.Count == 0 ? null
                          : "Not counted on the map, so reported as absent rather than as present: "
                            + string.Join(", ", unscanned.ToArray())
                            + ". ListerThings.ThingsOfDef logs (and therefore pauses) for MinifiedThing.";
            return block;
        }

        private static Dictionary<string, object> Notes()
        {
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "rotationSweep", "Designator_Build asks CanPlaceBlueprintAt with its protected placingRot field, so every bridge dry run through the architect menu answers for one fixed rotation. This tool takes the rotation as a parameter and reports all four by default." },
                { "coolerSides", "Building_Cooler.TickRare cools Position + IntVec3.South.RotatedBy(Rotation) and pushes heat to Position + IntVec3.North.RotatedBy(Rotation). Rot4.North is the identity rotation, so an unrotated cooler chills the cell to its south." },
                { "ventSides", "Building_Vent equalises the rooms at cell +/- Rotation.FacingCell (via GenTemperature.EqualizeTemperaturesThroughBuilding), which for a 1x1 vent is the same pair of cells. Neither side is hot or cold." },
                { "theWipe", "A real placement does what Designator_Build.DesignateSingleCell does, in order: cancel any Frame in the anchor cell whose replaceTags overlap the blueprint's (DestroyMode.Cancel, which refunds it), then GenSpawn.WipeExistingThings(center, rot, entDef.blueprintDef, map, DestroyMode.Deconstruct), then place. Skipping the wipe does not spare anything -- PlaceBlueprintForBuild's GenSpawn.Spawn defaults to WipeMode.Vanish and wipes anyway, without the refund. Everything removed is reported under wiped and framesCancelled." },
                { "wouldBeWiped", "Computed as GenSpawn.SpawningWipes(entDef.blueprintDef, thing.def) -- the BLUEPRINT's def, which is what actually spawns. Passing entDef instead reports loose items as doomed: an Impassable building with surfaceType None wipes items, a blueprint does not. An item the finished building would displace but the blueprint will not is flagged mustBeHauledFirst instead, which is what happens to it: a builder carries it out before work starts." },
                { "noDesignators", "Find.DesignatorManager is never touched. Placement is GenConstruct.PlaceBlueprintForBuild directly, so the player's selected designator, stuff and rotation are left alone." },
                { "identicalBlueprint", "identicalBlueprintExists is reported per rotation and a real run treats it as alreadyPlaced:true with success:true, because the caller's desired end state already holds." },
                { "effectOnPlace", "Per blocking thing, what an ACCEPTED placement does to it: wiped (GenSpawn.SpawningWipes(blueprintDef, thing.def) -- gone the moment the blueprint lands), frameCancelled, hauled (a builder carries it out before work starts), replaced (a Building the FINISHED thing wipes -- GenSpawn.SpawningWipes(entDef, thing.def); stone-over-wood is legal, so an accepted rotation over one of our own walls is a replacement, not empty ground), crop (a sown Plant, destroyed when the thing is built), cleared (a wild plant or anything else the finished building removes at no cost), none. A caller can put the first four on its verdict line and leave cleared where it belongs." },
                { "logErrorAvoided", "Rot4.FromString and CostListAdjusted's default errorOnNullStuff both call Verse.Log.Error, which calls TickManager.Pause(). Neither is used on its logging path here, and Faction.OfPlayer (whose body ends in Log.Error) is replaced by Faction.OfPlayerSilentFail." },
                { "materialsAreNotAPlacementCheck", "materials.canBuildNow false does NOT stop a placement and is not meant to. A blueprint standing while the haulers bring steel is ordinary play; the point is that the shortfall is on screen when the blueprint is made rather than discovered later. rotations[].accepted answers whether the GAME will take the blueprint, which is a separate question and the only one that refuses." },
                { "materialsOwnership", "onMap counts spawned stacks whose Faction is the player or null. Forbidden ones are counted in onMap AND in forbidden, then subtracted -- so onMap stays comparable with home/list_buildings resourceDeficit.onMapTotal, which also counts everything spawned. CompForbiddable.Forbidden is read directly: ForbidUtility.IsForbidden(Thing, Faction) reaches Faction.OfPlayer, which pauses the game." },
                { "batching", "cells=\"x,z;x,z\" places a whole wall line or room in ONE call: one def resolution, one materials scan of the map, one camera move and ONE 1.5 s watch lead for the batch, where a call per cell paid all four every time (~1.8 s a cell, 1.5 s of it the watch lead). The placements are chunked 8 cells per main-thread hop with 20 ms released between hops, because a long synchronous loop inside one hop stalls Verse.Root.Update -- the game's tick and the queue every other bridge call is pumped from -- which is the 'a 64-cell build delays every event' complaint moved rather than fixed. Every cell still goes through the same CanPlaceBlueprintAt and the same PlaceOne a single-cell call uses; a refused cell is reported in batch.rows and does not stop the rest. materials.needed is the cost of ALL the requested cells (materials.forCells, rows[].perCellNeeded)." },
                { "positionIs", "x,z is the anchor cell RimWorld reports as Thing.Position, not the min corner. For a multi-cell def see occupiedRect, which is GenAdj.OccupiedRect(center, rot, def.Size)." }
            };
        }

        /// <summary>
        /// The game's own frame-replacement test:
        /// <c>entDef.blueprintDef.replaceTags.NotNullAndContainsAnyElement(frame.def.replaceTags)</c>.
        /// Written out by hand so a null or empty list on either side is simply
        /// "no overlap" rather than an exception.
        /// </summary>
        private static bool TagsIntersect(ThingDef blueprintDef, ThingDef other)
        {
            try
            {
                if (blueprintDef == null || other == null)
                    return false;
                var a = blueprintDef.replaceTags;
                var b = other.replaceTags;
                if (a == null || b == null || a.Count == 0 || b.Count == 0)
                    return false;
                for (var i = 0; i < a.Count; i++)
                    for (var j = 0; j < b.Count; j++)
                        if (string.Equals(a[i], b[j], StringComparison.Ordinal))
                            return true;
                return false;
            }
            catch { return false; }
        }

        private static Dictionary<string, object> ThingRow(Thing thing)
        {
            return new Dictionary<string, object>
            {
                { "defName", thing.def != null ? thing.def.defName : null },
                { "label", SafeThingLabel(thing) },
                { "thingIDNumber", SafeInt(() => thing.thingIDNumber) },
                { "category", thing.def != null ? thing.def.category.ToString() : null },
                { "position", PositionOf(thing) },
                { "stackCount", SafeInt(() => thing.stackCount) }
            };
        }

        private static bool IsClass(ThingDef def, Type type)
        {
            try { return def.thingClass != null && type.IsAssignableFrom(def.thingClass); }
            catch { return false; }
        }

        private static bool SameRotation(Thing t, Rot4 rot)
        {
            try { return t.Rotation == rot; }
            catch { return false; }
        }

        private static Dictionary<string, object> SizeBlock(BuildableDef def)
        {
            try
            {
                // A SIZE, not a cell: IntVec2, and the keys happen to match a
                // position's. Not BridgeCommon.Pos.
                var s = def.Size;
                return new Dictionary<string, object> { { "x", s.x }, { "z", s.z } };
            }
            catch { return null; }
        }

        private static bool SafeMadeFromStuff(BuildableDef def)
        {
            try { return def.MadeFromStuff; }
            catch { return false; }
        }

        private static ThingDef SafeDefaultStuff(BuildableDef def)
        {
            try { return GenStuff.DefaultStuffFor(def); }
            catch { return null; }
        }

        private static bool? StuffAllowed(BuildableDef def, ThingDef stuffDef)
        {
            try
            {
                if (def.stuffCategories == null || stuffDef.stuffProps == null || stuffDef.stuffProps.categories == null)
                    return null;
                return def.stuffCategories.Any(cat => stuffDef.stuffProps.categories.Contains(cat));
            }
            catch { return null; }
        }

        private static bool SafeRotatable(ThingDef def)
        {
            try { return def.rotatable; }
            catch { return false; }
        }

        private static bool SafeResearchFinished(BuildableDef def)
        {
            try { return def.IsResearchFinished; }
            catch { return true; }
        }

        private static bool SafeBuildableByPlayer(BuildableDef def)
        {
            try { return def.BuildableByPlayer; }
            catch { return false; }
        }

        private static ThingDef SafeBlueprintDef(BuildableDef def)
        {
            try { return def.blueprintDef; }
            catch { return null; }
        }

        private static string SafeRotationHuman(Thing thing)
        {
            try { return thing.Rotation.ToStringHuman(); }
            catch { return null; }
        }

        /// <summary>
        /// A crop the colony sowed, as `Plant.sown` and `Plant.IsCrop` answer it.
        /// A wild plant under a footprint costs nothing; a sown one is food.
        /// </summary>
        private static bool IsCrop(Thing thing)
        {
            var plant = thing as Plant;
            if (plant == null)
                return false;
            try { if (plant.sown) return true; }
            catch { }
            try { return plant.IsCrop; }
            catch { return false; }
        }

        /// <summary>
        /// What an ACCEPTED placement does to one thing in the footprint, in one
        /// word: wiped (the blueprint destroys it the moment it is placed),
        /// frameCancelled, hauled (a builder carries it out first), replaced (a
        /// building the finished thing takes the place of -- stone over wood is
        /// legal and reads as empty ground without this), crop (a sown plant,
        /// destroyed when the thing is built), cleared (a wild plant or anything
        /// else the finished building removes at no cost), or none.
        /// </summary>
        private static string EffectOnPlace(Thing thing, bool wipes, bool frameCancel,
                                            bool haulFirst, bool finishedWipes)
        {
            if (wipes)
                return "wiped";
            if (frameCancel)
                return "frameCancelled";
            if (haulFirst)
                return "hauled";
            if (!finishedWipes)
                return "none";
            var category = ThingCategory.None;
            try { category = thing.def.category; }
            catch { }
            if (category == ThingCategory.Building)
                return "replaced";
            if (category == ThingCategory.Plant)
                return IsCrop(thing) ? "crop" : "cleared";
            return "cleared";
        }

        private static string SafeThingLabel(Thing thing)
        {
            try { return thing.LabelCapNoCount.ToString(); }
            catch
            {
                try { return thing.def != null ? thing.def.label : null; }
                catch { return null; }
            }
        }

        private static string SafeFactionName(Faction faction)
        {
            try { return faction == null ? null : faction.Name; }
            catch { return null; }
        }

        /// <summary>See BridgeCommon.PositionOf: {x, z}, or null if the getter throws.</summary>
        private static Dictionary<string, object> PositionOf(Thing thing)
        {
            return BridgeCommon.PositionOf(thing);
        }

        // Nullable guards: a throwing read becomes null, never a value that
        // cannot be told apart from a real one.
        private static int? SafeInt(Func<int> read)
        {
            return BridgeCommon.TryN(read);
        }

        private static bool? SafeBool(Func<bool> read)
        {
            return BridgeCommon.TryN(read);
        }

        private static string SafeString(Func<string> read)
        {
            return BridgeCommon.SafeString(read);
        }

        private static double? SafeFloat(Func<float> read)
        {
            try
            {
                var v = read();
                if (float.IsNaN(v) || float.IsInfinity(v))
                    return null;
                return Math.Round((double)v, 2);
            }
            catch { return null; }
        }

        /// <summary>The shared map gate; see BridgeCommon.TryGetMap. The error
        /// text names this tool.</summary>
        private static bool TryGetMap(out Map map, out string error)
        {
            return BridgeCommon.TryGetMap(ToolName, out map, out error);
        }

        /// <summary>The shared refusal shape; see BridgeCommon.Failure.</summary>
        private static object Failure(string error)
        {
            return BridgeCommon.Failure(ToolName, error);
        }
    }
}
