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
    /// home/zone_cells — add, remove, create, delete, REPAIR zone cells, set a
    /// stockpile's storage FILTER and set a growing zone's CROP, following the
    /// game's own mechanics rather than the shortest code that compiles.
    /// `dryRun` defaults to TRUE.
    ///
    /// ## op=crop, and the getter that writes
    ///
    /// `op=crop` calls `Zone_Growing.SetPlantDefToGrow`. The before/after read is
    /// the private `plantDefToGrow` FIELD, never the `PlantDefToGrow` property:
    /// that getter assigns the field when it is null, so reading it to report the
    /// current crop would set one. A zone that has never been told what to grow
    /// therefore reports null rather than the potatoes the property would invent.
    ///
    /// ## op=filter, and why it runs twice
    ///
    /// `op=filter` writes `Zone_Stockpile.settings`: the `ThingFilter` and the
    /// `StoragePriority`. A preset, then `allow`, then `disallow`, then the
    /// priority. The whole plan is first applied to a scratch `ThingFilter`
    /// seeded with `CopyAllowancesFrom(the live one)` — the game's own code doing
    /// the game's own work on a copy — which is what a dry run returns and what
    /// `changed` is computed from. A real run then replays the identical calls
    /// against the live filter and reads the answer back off it. See
    /// <see cref="StockpileFilter"/> for the preset definitions.
    ///
    /// ## Two hops, because the menu opens before the write
    ///
    /// Hop 1 resolves the zone and opens the watch (the storage tab for
    /// `op=filter`, the zone's inspect pane otherwise); a lead runs off-thread so
    /// the open menu is on screen; hop 2 plans, applies, reads back and closes.
    /// The planner and the write stay inside hop 2 together, so nothing can
    /// change between deciding and doing. The watch is decorative: hop 1's
    /// resolve is only there to aim the camera, and hop 2 re-resolves and is the
    /// only thing that can refuse.
    ///
    /// ## The bug this tool exists to not repeat, and to clean up after
    ///
    /// `Zone.AddCell(c)` appends to the zone's own `cells` list and then calls
    /// `zoneManager.AddZoneGridCell(this, c)`, which OVERWRITES the grid pointer.
    /// It does not remove the cell from the zone that had it. The game never hits
    /// that case: `Designator_ZoneAdd.DesignateMultiCell` drops every cell where
    /// `ZoneAt(c) != null` before adding any. The bridge's upstream
    /// `apply_architect_designator` calls `AddCell` directly on cells that
    /// `CanDesignateCell` accepted — and `CanDesignateCell` accepts a cell that is
    /// already in a same-type zone — so it produced a stockpile whose list holds
    /// two cells the grid gives to a different stockpile. On load the game prints
    /// `Stockpile zone 2 overwriting slot group square (113, 0, 145) of Stockpile
    /// zone 1`, twice.
    ///
    /// So `op=add` does the step that was skipped: `owner.RemoveCell(c)` FIRST,
    /// then `target.AddCell(c)`, and every row says what it was taken from.
    ///
    /// ## Why the repair cannot just call Delete()
    ///
    /// `Zone.RemoveCell(c)` removes from the list and then calls
    /// `zoneManager.ClearZoneGridCell(c)` — it clears whoever the GRID says owns
    /// the cell, not itself. `Zone.Delete()` is a loop of `RemoveCell`. So
    /// deleting the phantom zone would blank the other zone's grid cells and turn
    /// one inconsistency into two. `op=repair` therefore edits the `cells` list
    /// directly and never touches the grid.
    ///
    /// ## Four Log.Error landmines, all of which pause the colony
    ///
    /// `Verse.Log.Error` calls `TickManager.Pause()`. Every one of these is an
    /// ordinary-looking call on a plausible repair path, and every one is
    /// pre-checked here rather than caught afterwards (an exception is not thrown
    /// — the game logs and carries on, having already paused):
    ///
    ///   1. `Zone.AddCell(c)` logs when `cells.Contains(c)` is already true, and
    ///      when a thing at the cell has `!def.CanOverlapZones`.
    ///   2. `Zone.RemoveCell(c)` logs when `!cells.Contains(c)`.
    ///   3. `SlotGroup.Notify_LostCell(c)` -> `HaulDestinationManager.ClearCellFor`
    ///      logs when the haul group grid at that cell is NOT this slot group.
    ///      That is exactly the state a phantom cell is in, so the repair checks
    ///      `SlotGroupAt(c) == slotGroup` first and reports "notify skipped" when
    ///      it does not hold, rather than pausing the game to be tidy.
    ///   4. `SlotGroup.Notify_AddedCell(c)` -> `SetCellFor` logs when the haul
    ///      group grid at that cell is already non-null.
    ///
    /// `Rot4.FromString` and `CostListAdjusted(def, null)` are on the same list and
    /// are avoided in home/place_building for the same reason.
    ///
    /// ## Dry run is a simulation, not a guess
    ///
    /// Every op is planned against an in-memory copy of the affected zones' cell
    /// lists, the zone grid and the haul group grid, cell by cell in order, so a
    /// later cell sees the effect of an earlier one (including a previous owner
    /// emptying out and deregistering). `dryRun: true` returns that plan and stops;
    /// `dryRun: false` replays exactly the same recorded actions against the game.
    /// The two cannot drift, because there is only one planner.
    ///
    /// ## Zone.cells is PUBLIC in this build
    ///
    /// The brief called for reflection on a private `cells` field. In RimWorld 1.6
    /// (Assembly-CSharp 1.6.9676.17735) `Verse.Zone.cells` is a **public**
    /// `List&lt;IntVec3&gt;`, so the repair writes it directly. `Zone.Cells` (capital
    /// C) is the one to stay away from: its getter shuffles the list in place.
    /// </summary>
    public sealed class HomeZoneCellTools
    {
        private const string ToolName = "home/zone_cells";

        /// <summary>Stands in for the not-yet-created zone during an op=create plan.</summary>
        private static readonly object NewZoneKey = new object();

        [Tool(
            ToolName,
            Title = "Add, remove, create, delete or repair zone cells, set a stockpile filter, or set a growing zone's crop",
            Description =
                "Write tool for zones, with dryRun defaulting to TRUE. op=add moves cells into a zone the way the game does it "
                + "(removing them from their previous owner FIRST, which is the step a direct AddCell skips and the reason zones end "
                + "up claiming cells the grid gives to someone else). op=remove takes cells out. op=create makes a stockpile, dumping "
                + "stockpile or growing zone and fills it, optionally already filtered. op=delete removes a whole zone. op=repair fixes "
                + "a zone whose cell list and the map's zone grid disagree, by editing the list only and never the grid. op=filter sets "
                + "a stockpile's storage filter and priority: preset (everything, nothing, food, perishables, nonperishables, "
                + "outdoorSafe), then allow, then disallow, then priority; the reply defines every preset it used. op=crop sets a "
                + "growing zone's plant with the `plant` argument (a ThingDef defName or label) and reads the crop back off the "
                + "zone. Every op reports a before/after summary, and a dry run computes exactly what a real run would do.",
            ResultDescription =
                "success, dryRun, op, changed, zone (label/id/type after the op), cells[] (per-cell accepted / reason / takenFrom), "
                + "before and after summaries (cell counts, or the filter summary for op=filter), presetDefinition, filter (op=create), "
                + "zonesRemoved[], changes[] (English sentences), watch, notes.")]
        [ToolResponse("dryRun", "boolean", "True = nothing was written. Defaults to TRUE; a caller must pass dryRun:false deliberately.", Always = true)]
        [ToolResponse("cells", "array", "One row per requested cell: x, z, accepted, reason (empty when accepted), takenFrom (the zone it was moved out of, null otherwise).", Always = true)]
        [ToolResponse("before", "object", "listedCellCount, gridCellCount, phantomCellCount for the target zone before the op. For op=filter it is the stockpile's filter summary instead: allowedDefCount, storableDefCount, priority, categoriesFullyAllowed[], categoriesPartlyAllowed[], allowsRottable, allowedRottableCount, sampleAllowed[], sampleTruncated. For op=crop it is plantDef, plantLabel, plantDefExplicitlySet, allowSow.", Always = true)]
        [ToolResponse("after", "object", "The same block after the op - simulated when dryRun, READ BACK off the game when not. Equal cell counts with zero phantoms is the healthy state.", Always = true)]
        [ToolResponse("changed", "boolean", "Whether the op changed anything (or would, on a dry run). A filter call that re-applies what the stockpile already had reports false.", Always = true)]
        [ToolResponse("presetDefinition", "object", "One sentence per preset this call used, defining exactly which defs it covers. Empty object when no preset was named.", Always = true)]
        [ToolResponse("filter", "object", "op=create only: the new stockpile's filter summary, in the same shape as before/after. Null for a growing zone and when no zone was created.", Nullable = true)]
        [ToolResponse("watch", "object", "What was shown on screen while the write landed: shown, and either the selection/tab/camera detail or a reason it was skipped (dry run, refused, watch:false).", Always = true)]
        [ToolResponse("changes", "array", "Every mutation as an English sentence, in the order it was (or would be) applied.", Always = true)]
        [ToolResponse("unknownArguments", "array", "Every argument key the caller sent that this tool does not declare, sorted, case-sensitively. Empty array = every key was recognised. The host's own _rimBridgeTimeoutMs is never listed.", Always = true)]
        [ToolResponse("unknownArgumentsWarning", "string", "Present only when unknownArguments is non-empty, or when the caller's raw keys could not be read at all - in which case the empty unknownArguments means 'not known', not 'nothing unknown'. On a WRITE tool this matters twice over: a misspelled dryRun is the difference between a plan and a changed map.", Nullable = true)]
        public async Task<object> ZoneCells(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            [ToolParameter(Description = "What to do: add, remove, create, delete, repair, filter, crop.")] string op = null,
            [ToolParameter(Description = "Target zone: its exact label (case-insensitive), or its numeric id as a string. Optional for op=repair (repairs every zone) and required for everything else except op=create.")] string zone = null,
            [ToolParameter(Description = "Rect origin x. Combined with z/width/height to name a block of cells.", DefaultValue = -1)] int x = -1,
            [ToolParameter(Description = "Rect origin z.", DefaultValue = -1)] int z = -1,
            [ToolParameter(Description = "Rect width in cells.", DefaultValue = 1)] int width = 1,
            [ToolParameter(Description = "Rect height in cells.", DefaultValue = 1)] int height = 1,
            [ToolParameter(Description = "Explicit cell list, e.g. \"113,145;113,144\". Combined with the rect if both are given; duplicates are collapsed.")] string cells = null,
            [ToolParameter(Description = "For op=create: stockpile, dumping, or growing.", DefaultValue = "stockpile")] string zoneType = "stockpile",
            [ToolParameter(Description = "For op=create: the label for the new zone. Null = the game's own auto-generated name.")] string label = null,
            [ToolParameter(Description = "Storage priority for a stockpile (Low, Normal, Preferred, Important, Critical). Applies to op=create, op=filter and, when given, to op=add on an existing stockpile.")] string priority = null,
            [ToolParameter(Description = "For op=filter and op=create: a prebuilt filter to start from. One of everything, nothing, food, perishables, nonperishables, outdoorSafe. The reply's presetDefinition says exactly what each covers.")] string preset = null,
            [ToolParameter(Description = "For op=filter and op=create: extra ThingCategoryDef or ThingDef names to allow, comma-separated, applied after the preset. defName or label, case-insensitive. A name that matches nothing refuses the whole call.")] string allow = null,
            [ToolParameter(Description = "For op=filter and op=create: ThingCategoryDef or ThingDef names to disallow, comma-separated, applied last. Same matching and the same refusal.")] string disallow = null,
            [ToolParameter(Description = "For op=crop: the plant to grow, as a ThingDef defName (Plant_Potato) or label (potato plant), case-insensitive. A name that matches no sowable plant refuses and lists what the zone will take.")] string plant = null,
            [ToolParameter(Description = "After op=add, call Zone.CheckContiguous() when the zone has become non-contiguous. CheckContiguous DELETES the cells it cannot reach, so this is off by default: the tool reports contiguous:false and leaves the zone alone.", DefaultValue = false)] bool allowSplit = false,
            [ToolParameter(Description = "Show the write happening: select the zone, open the tab a player would use, then close it again. Decorative only.", DefaultValue = true)] bool watch = true,
            [ToolParameter(Description = "How long the menu stays open after the write, in seconds.", DefaultValue = Watch.DefaultSeconds)] int watchSeconds = Watch.DefaultSeconds,
            [ToolParameter(Description = "TRUE by default. Plan the whole operation and return it without writing anything. Pass false to actually apply it.", DefaultValue = true)] bool dryRun = true)
        {
            return BridgeCommon.WithUnknownArguments(
                await ZoneCellsCore(
                    ctx, cancellationToken, op, zone, x, z, width, height, cells,
                    zoneType, label, priority, preset, allow, disallow, plant,
                    allowSplit, watch, watchSeconds, dryRun).ConfigureAwait(false),
                ctx, typeof(HomeZoneCellTools), ToolName);
        }

        private async Task<object> ZoneCellsCore(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            string op,
            string zone,
            int x,
            int z,
            int width,
            int height,
            string cells,
            string zoneType,
            string label,
            string priority,
            string preset,
            string allow,
            string disallow,
            string plant,
            bool allowSplit,
            bool watch,
            int watchSeconds,
            bool dryRun)
        {
            if (ctx?.MainThread == null)
                return Failure("No RimBridge main-thread dispatcher is available for this invocation.");

            // HOP 1. Open the menu a player would open, before anything is
            // written, so a viewer sees the change land inside it. Nothing here
            // decides the call: it resolves the zone only to aim the camera, and a
            // dry run or watch:false never gets here at all.
            Watch.Session session = null;
            var watchBlock = Watch.Skipped(!watch ? "watch:false" : "dry run");
            if (watch && !dryRun)
            {
                var preflight = await ctx.MainThread
                    .InvokeAsync(() => OpenWatch(ctx, op, zone), cancellationToken)
                    .ConfigureAwait(false);
                session = preflight.Session;
                if (session == null)
                    watchBlock = Watch.Skipped(preflight.Skipped ?? "nothing to show");
            }

            // Off the main thread, so the open menu is on screen for a moment.
            if (session != null)
                await Watch.Lead(session, cancellationToken).ConfigureAwait(false);

            // HOP 2. Companion tools are dispatched with MarshalToMainThread =
            // false (AnnotatedExtensionCapabilityProvider.InvokeAsync). This one
            // WRITES game state, so the hop is not merely about consistent reads:
            // mutating zone lists, the zone grid and the haul destination grid off
            // the main thread would race the tick. Plan, apply and read back all
            // go inside ONE hop, so nothing can change between deciding and doing.
            var applied = await ctx.MainThread
                .InvokeAsync(() =>
                {
                    var reply = Run(op, zone, x, z, width, height, cells, zoneType,
                                    label, priority, preset, allow, disallow, plant,
                                    allowSplit, dryRun);

                    // op=create could not select a zone that did not exist when
                    // the menu opened, so the new one is selected here instead.
                    // The order is the weaker one -- the viewer sees where it
                    // landed rather than watching it land -- and the note says so.
                    if (session != null)
                    {
                        var made = CreatedZone(reply);
                        if (made != null)
                            session = Watch.Open(ctx, made,
                                                 made is Zone_Stockpile ? typeof(ITab_Storage) : null,
                                                 null, true);
                    }

                    var block = session == null ? null : Watch.Finish(session, watchSeconds);
                    return new Hop2 { Reply = reply, Watch = block };
                }, cancellationToken)
                .ConfigureAwait(false);

            if (applied.Watch != null)
                watchBlock = applied.Watch;

            var payload = BridgeCommon.AsDictionary(applied.Reply);
            if (payload == null)
                return applied.Reply;
            payload["watch"] = watchBlock;
            return payload;
        }

        /// <summary>What hop 2 carries back: the reply, and the watch block that
        /// could only be built on the main thread beside it.</summary>
        private sealed class Hop2
        {
            internal object Reply;
            internal Dictionary<string, object> Watch;
        }

        /// <summary>
        /// Main thread. The zone a real op=create just registered, found by the
        /// id in its own reply rather than by holding a reference, or null for
        /// every other case. Never throws.
        /// </summary>
        private static Zone CreatedZone(object reply)
        {
            try
            {
                var payload = BridgeCommon.AsDictionary(reply);
                if (payload == null || !BridgeCommon.Bool(payload, "success"))
                    return null;
                object opValue;
                if (!payload.TryGetValue("op", out opValue) || !"create".Equals(opValue as string))
                    return null;
                if (BridgeCommon.Bool(payload, "dryRun"))
                    return null;

                object zoneValue;
                if (!payload.TryGetValue("zone", out zoneValue))
                    return null;
                var zoneBlock = zoneValue as Dictionary<string, object>;
                if (zoneBlock == null)
                    return null;
                object idValue;
                if (!zoneBlock.TryGetValue("id", out idValue) || !(idValue is int))
                    return null;

                Map map;
                string mapError;
                if (!TryGetMap(out map, out mapError) || map.zoneManager == null)
                    return null;
                return map.zoneManager.AllZones.FirstOrDefault(zz => zz != null && SafeId(zz) == (int)idValue);
            }
            catch { return null; }
        }

        /// <summary>The watch session hop 1 opened, or the reason there is
        /// none.</summary>
        private sealed class Preflight
        {
            internal Watch.Session Session;
            internal string Skipped;
        }

        /// <summary>
        /// Main thread. Select the zone and open the tab a player would use for
        /// this op: the storage tab for op=filter, the plain inspect pane
        /// otherwise. Never throws and never refuses the call — a watch that
        /// cannot find its target is a skipped watch, not a failure.
        /// </summary>
        private static Preflight OpenWatch(IRimBridgeContext ctx, string op, string zoneSpec)
        {
            try
            {
                Map map;
                string mapError;
                if (!TryGetMap(out map, out mapError))
                    return new Preflight { Skipped = "no map to show it on" };

                var wantOp = (op ?? string.Empty).Trim().ToLowerInvariant();
                if (wantOp == "create")
                {
                    // The zone does not exist yet and Watch has no way to be
                    // handed a rectangle, so this session shows nothing; hop 2
                    // selects the zone once it has been made.
                    return new Preflight { Session = Watch.Open(ctx, null, null, null, true) };
                }

                if (wantOp == "repair" && string.IsNullOrEmpty(zoneSpec))
                    return new Preflight { Skipped = "a whole-map repair has no single zone to select" };

                Zone target;
                string resolveError;
                if (!TryResolveZone(map.zoneManager, zoneSpec, out target, out resolveError))
                    return new Preflight { Skipped = "the zone could not be resolved: " + resolveError };

                var inspectTab = wantOp == "filter" ? typeof(ITab_Storage) : null;
                return new Preflight { Session = Watch.Open(ctx, target, inspectTab, null, true) };
            }
            catch (Exception e)
            {
                return new Preflight { Skipped = "opening the watch threw " + e.GetType().Name };
            }
        }

        // =================================================================== run

        private static object Run(string op, string zoneSpec, int x, int z, int width, int height,
                                  string cellSpec, string zoneType, string newLabel, string priority,
                                  string preset, string allowSpec, string disallowSpec, string plantSpec,
                                  bool allowSplit, bool dryRun)
        {
            if (!TryGetMap(out var map, out var mapError))
                return Failure(mapError);

            var wantOp = (op ?? string.Empty).Trim().ToLowerInvariant();
            if (wantOp != "add" && wantOp != "remove" && wantOp != "create" &&
                wantOp != "delete" && wantOp != "repair" && wantOp != "filter" &&
                wantOp != "crop")
                return Failure("op must be one of: add, remove, create, delete, repair, filter, crop. Got: " + (op ?? "(null)"));

            // `plant` does nothing on the other ops, so a call that sends it
            // there is refused rather than silently ignoring it.
            if (wantOp != "crop" && !string.IsNullOrEmpty(plantSpec))
                return Failure("plant applies to op=crop only. Run op=crop on the growing zone instead.");

            // The filter arguments do nothing on the other ops, so a call that
            // sends them there is refused rather than silently ignoring them.
            if (wantOp != "filter" && wantOp != "create" &&
                (!string.IsNullOrEmpty(preset) || !string.IsNullOrEmpty(allowSpec) || !string.IsNullOrEmpty(disallowSpec)))
                return Failure("preset, allow and disallow apply to op=filter and op=create only. Run op=filter on the zone instead.");

            var zoneManager = map.zoneManager;
            if (zoneManager == null || zoneManager.AllZones == null)
                return Failure("This map has no zone manager.");

            var sim = new Sim(map, zoneManager);

            switch (wantOp)
            {
                case "repair":
                    return Repair(map, sim, zoneSpec, dryRun);
                case "delete":
                    return Delete(map, sim, zoneSpec, dryRun);
                case "filter":
                {
                    Zone target;
                    string resolveError;
                    if (!TryResolveZone(zoneManager, zoneSpec, out target, out resolveError))
                        return Failure(resolveError);
                    return Filter(target, preset, allowSpec, disallowSpec, priority, dryRun);
                }
                case "crop":
                {
                    Zone target;
                    string resolveError;
                    if (!TryResolveZone(zoneManager, zoneSpec, out target, out resolveError))
                        return Failure(resolveError);
                    return Crop(target, plantSpec, dryRun);
                }
                case "create":
                    return AddOrCreate(map, sim, null, zoneType, newLabel, priority,
                                       preset, allowSpec, disallowSpec,
                                       x, z, width, height, cellSpec, allowSplit, dryRun, true);
                case "add":
                {
                    Zone target;
                    string resolveError;
                    if (!TryResolveZone(zoneManager, zoneSpec, out target, out resolveError))
                        return Failure(resolveError);
                    return AddOrCreate(map, sim, target, zoneType, newLabel, priority,
                                       preset, allowSpec, disallowSpec,
                                       x, z, width, height, cellSpec, allowSplit, dryRun, false);
                }
                default:
                {
                    Zone target;
                    string resolveError;
                    if (!TryResolveZone(zoneManager, zoneSpec, out target, out resolveError))
                        return Failure(resolveError);
                    return Remove(map, sim, target, x, z, width, height, cellSpec, dryRun);
                }
            }
        }

        // ================================================================ filter

        /// <summary>A parsed preset/allow/disallow/priority request, or the
        /// refusal that stopped it before anything was written.</summary>
        private sealed class FilterRequest
        {
            internal string Preset;
            internal StockpileFilter.Resolved Allow = new StockpileFilter.Resolved();
            internal StockpileFilter.Resolved Disallow = new StockpileFilter.Resolved();
            internal StoragePriority? Priority;
            internal string Error;

            internal bool Any
            {
                get { return Preset != null || Allow.Any || Disallow.Any || Priority.HasValue; }
            }
        }

        /// <summary>
        /// Parse and resolve everything the filter write needs, refusing on any
        /// name that matches no def and on a category name while the game's
        /// thing-category tree is down (SetAllow on a ThingCategoryDef calls
        /// Log.Error there, and Log.Error pauses the colony).
        /// </summary>
        private static FilterRequest ParseFilterRequest(string preset, string allowSpec,
                                                        string disallowSpec, string priority)
        {
            var request = new FilterRequest();

            if (!string.IsNullOrEmpty(preset))
            {
                request.Preset = StockpileFilter.NormalizePreset(preset);
                if (request.Preset == null)
                {
                    request.Error = "preset must be one of: " + string.Join(", ", StockpileFilter.PresetNames)
                                    + ". Got: " + preset;
                    return request;
                }
            }

            if (!string.IsNullOrEmpty(priority))
            {
                StoragePriority parsed;
                if (!TryParsePriority(priority, out parsed))
                {
                    request.Error = "priority must be one of: Low, Normal, Preferred, Important, Critical. Got: " + priority;
                    return request;
                }
                request.Priority = parsed;
            }

            request.Allow = StockpileFilter.Resolve(allowSpec);
            request.Disallow = StockpileFilter.Resolve(disallowSpec);

            var unresolved = request.Allow.Unresolved.Concat(request.Disallow.Unresolved).ToList();
            if (unresolved.Count > 0)
            {
                request.Error = "No ThingCategoryDef or ThingDef matches " + string.Join(", ",
                    unresolved.Select(n => "\"" + n + "\"").ToArray())
                    + ". Names are matched against defName first, then label, case-insensitively. Nothing was written.";
                return request;
            }

            if ((request.Allow.Categories.Count > 0 || request.Disallow.Categories.Count > 0)
                && !StockpileFilter.CategoryTreeReady())
            {
                request.Error = "The game's thing-category tree is not initialised, so a category name cannot be expanded "
                                + "without ThingFilter.SetAllow calling Log.Error (which pauses the game). Name ThingDefs instead.";
                return request;
            }

            return request;
        }

        /// <summary>
        /// op=filter. The plan runs first against a scratch ThingFilter seeded
        /// from the live one, which is what a dry run returns and what `changed`
        /// is computed from; a real run then replays the identical calls against
        /// the live filter and reads the result back off it.
        /// </summary>
        private static object Filter(Zone zone, string preset, string allowSpec, string disallowSpec,
                                     string priority, bool dryRun)
        {
            var stockpile = zone as Zone_Stockpile;
            if (stockpile == null)
                return Failure("op=filter needs a stockpile. \"" + SafeLabel(zone) + "\" is a "
                               + SafeTypeName(zone) + ", which has no storage settings.");

            var settings = BridgeCommon.Try(() => stockpile.settings, (StorageSettings)null);
            if (settings == null || settings.filter == null)
                return Failure("\"" + SafeLabel(zone) + "\" has no storage settings to filter.");

            var request = ParseFilterRequest(preset, allowSpec, disallowSpec, priority);
            if (request.Error != null)
                return Failure(request.Error);
            if (!request.Any)
                return Failure("op=filter needs at least one of preset, allow, disallow or priority. "
                               + "A call with none of them would write nothing.");

            var universe = StockpileFilter.StorableDefs(stockpile);
            var parent = StockpileFilter.ParentFilter(stockpile);
            var priorityBefore = CurrentPriority(stockpile);
            var before = StockpileFilter.Summary(settings.filter, priorityBefore, universe);
            var allowedBefore = StockpileFilter.AllowedSet(settings.filter);

            var changes = new List<object>();
            string applyError = null;
            Dictionary<string, object> after;
            HashSet<ThingDef> allowedAfter;

            // The scratch copy carries no settingsChangedCallback, so planning on
            // it cannot notify the haulables lister or touch the slot group.
            var scratch = new ThingFilter();
            try
            {
                scratch.CopyAllowancesFrom(settings.filter);
                StockpileFilter.Apply(scratch, request.Preset, request.Allow, request.Disallow,
                                      parent, universe, changes);
            }
            catch (Exception e)
            {
                return Failure("Planning the filter threw " + e.GetType().Name + ": " + e.Message + ". Nothing was written.");
            }

            if (request.Priority.HasValue && request.Priority.Value != priorityBefore)
                changes.Add("Set priority to " + request.Priority.Value + ".");

            if (!dryRun)
            {
                try
                {
                    StockpileFilter.Apply(settings.filter, request.Preset, request.Allow, request.Disallow,
                                          parent, universe, null);
                    if (request.Priority.HasValue)
                        settings.Priority = request.Priority.Value;
                }
                catch (Exception e)
                {
                    applyError = "Applying the filter threw " + e.GetType().Name + ": " + e.Message;
                }
                after = StockpileFilter.Summary(settings.filter, CurrentPriority(stockpile), universe);
                allowedAfter = StockpileFilter.AllowedSet(settings.filter);
            }
            else
            {
                after = StockpileFilter.Summary(scratch, request.Priority ?? priorityBefore, universe);
                allowedAfter = StockpileFilter.AllowedSet(scratch);
            }

            var changed = !allowedBefore.SetEquals(allowedAfter)
                          || (request.Priority.HasValue && request.Priority.Value != priorityBefore);
            if (!changed)
                changes.Add("Nothing changed: the stockpile already had exactly this filter and priority.");

            return new Dictionary<string, object>
            {
                { "success", applyError == null },
                { "tool", ToolName },
                { "op", "filter" },
                { "dryRun", dryRun },
                { "changed", changed },
                { "zone", new Dictionary<string, object>
                    {
                        { "label", SafeLabel(zone) },
                        { "id", SafeId(zone) },
                        { "type", SafeTypeName(zone) }
                    } },
                { "before", before },
                { "after", after },
                { "presetDefinition", StockpileFilter.Definition(request.Preset) },
                { "filter", null },
                { "cells", new List<object>() },
                { "cellsAccepted", 0 },
                { "cellsRequested", 0 },
                { "zonesRemoved", new List<object>() },
                { "changes", changes },
                { "error", applyError },
                { "notes", Notes() }
            };
        }

        // ================================================================== crop

        /// <summary>
        /// op=crop. Sets a growing zone's plant with the game's own
        /// `SetPlantDefToGrow`, and reads the crop back off the zone afterwards.
        /// The before/after read is the private field, never the property whose
        /// getter writes.
        /// </summary>
        private static object Crop(Zone zone, string plantSpec, bool dryRun)
        {
            var growing = zone as Zone_Growing;
            if (growing == null)
                return Failure("op=crop needs a growing zone. \"" + SafeLabel(zone) + "\" is a "
                               + SafeTypeName(zone) + ", which grows nothing.");

            if (string.IsNullOrWhiteSpace(plantSpec))
                return Failure("op=crop needs a plant: a ThingDef defName (Plant_Potato) or label (potato plant).");

            ThingDef wanted;
            string plantError;
            if (!TryResolvePlant(growing, plantSpec, out wanted, out plantError))
                return Failure(plantError);

            var before = CropSummary(growing);
            var current = ReadPlantDef(growing);
            var changed = !ReferenceEquals(current, wanted);

            var changes = new List<object>();
            if (changed)
                changes.Add("Set the crop of \"" + SafeLabel(zone) + "\" to " + wanted.defName
                            + (current == null ? " (it had none set)." : " (was " + current.defName + ")."));
            else
                changes.Add("Nothing changed: the zone already grows " + wanted.defName + ".");

            string applyError = null;
            Dictionary<string, object> after;
            if (!dryRun && changed)
            {
                try
                {
                    growing.SetPlantDefToGrow(wanted);
                }
                catch (Exception e)
                {
                    applyError = "SetPlantDefToGrow threw " + e.GetType().Name + ": " + e.Message;
                }
                after = CropSummary(growing);
            }
            else if (!dryRun)
            {
                after = CropSummary(growing);
            }
            else
            {
                after = new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    { "plantDef", wanted.defName },
                    { "plantLabel", BridgeCommon.SafeString(() => wanted.label) },
                    { "plantDefExplicitlySet", true },
                    { "allowSow", before.ContainsKey("allowSow") ? before["allowSow"] : null }
                };
            }

            return new Dictionary<string, object>
            {
                { "success", applyError == null },
                { "tool", ToolName },
                { "op", "crop" },
                { "dryRun", dryRun },
                { "changed", changed },
                { "zone", new Dictionary<string, object>
                    {
                        { "label", SafeLabel(zone) },
                        { "id", SafeId(zone) },
                        { "type", SafeTypeName(zone) }
                    } },
                { "before", before },
                { "after", after },
                { "presetDefinition", new Dictionary<string, object>() },
                { "filter", null },
                { "cells", new List<object>() },
                { "cellsAccepted", 0 },
                { "cellsRequested", 0 },
                { "zonesRemoved", new List<object>() },
                { "changes", changes },
                { "error", applyError },
                { "notes", Notes() }
            };
        }

        /// <summary>The private plantDefToGrow field. The PlantDefToGrow property
        /// assigns it when null, so reading the property to report the crop would
        /// set one.</summary>
        private static ThingDef ReadPlantDef(Zone_Growing growing)
        {
            try
            {
                var field = BridgeCommon.PrivateInstanceField(typeof(Zone_Growing), "plantDefToGrow");
                return field == null ? null : field.GetValue(growing) as ThingDef;
            }
            catch
            {
                return null;
            }
        }

        private static Dictionary<string, object> CropSummary(Zone_Growing growing)
        {
            var plant = ReadPlantDef(growing);
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "plantDef", plant == null ? null : plant.defName },
                { "plantLabel", plant == null ? null : BridgeCommon.SafeString(() => plant.label) },
                { "plantDefExplicitlySet", plant != null },
                { "allowSow", BridgeCommon.TryN(() => growing.allowSow) }
            };
        }

        /// <summary>
        /// A sowable plant this zone will actually take, matched on defName then
        /// label. A miss lists what the zone accepts rather than refusing blankly.
        /// </summary>
        private static bool TryResolvePlant(Zone_Growing growing, string spec, out ThingDef plant, out string error)
        {
            plant = null;
            error = null;
            var wanted = (spec ?? string.Empty).Trim();

            List<ThingDef> sowable;
            try
            {
                sowable = DefDatabase<ThingDef>.AllDefsListForReading
                    .Where(d => d != null && d.plant != null
                                && BridgeCommon.Try(() => d.plant.Sowable, false)
                                && BridgeCommon.Try(() => PlantUtility.CanSowOnGrower(d, growing), false))
                    .ToList();
            }
            catch (Exception e)
            {
                error = "Listing sowable plants threw " + e.GetType().Name + ": " + e.Message;
                return false;
            }

            plant = sowable.FirstOrDefault(d => string.Equals(d.defName, wanted, StringComparison.OrdinalIgnoreCase))
                    ?? sowable.FirstOrDefault(d => string.Equals(BridgeCommon.SafeString(() => d.label), wanted, StringComparison.OrdinalIgnoreCase));
            if (plant != null)
                return true;

            var names = sowable.Select(d => d.defName).OrderBy(n => n, StringComparer.Ordinal).Take(24).ToList();
            error = "No sowable plant matches \"" + wanted + "\" for this zone. It will take: "
                    + (names.Count == 0 ? "nothing (the zone reports no sowable plant at all)"
                                        : string.Join(", ", names.ToArray())
                                          + (sowable.Count > names.Count ? ", ... (" + sowable.Count + " in all)" : ""));
            return false;
        }

        /// <summary>The stockpile's priority, or null when the read throws.</summary>
        private static StoragePriority? CurrentPriority(Zone_Stockpile stockpile)
        {
            return BridgeCommon.TryN(() => stockpile.settings.Priority);
        }

        // =============================================================== add/create

        private static object AddOrCreate(Map map, Sim sim, Zone target, string zoneType, string newLabel,
                                          string priority, string filterPreset, string allowSpec, string disallowSpec,
                                          int x, int z, int width, int height,
                                          string cellSpec, bool allowSplit, bool dryRun, bool creating)
        {
            List<IntVec3> requested;
            string cellError;
            if (!TryParseCells(x, z, width, height, cellSpec, out requested, out cellError))
                return Failure(cellError);
            if (requested.Count == 0)
                return Failure("No cells given. Provide x/z (plus width/height) and/or cells=\"x,z;x,z\".");

            StorageSettingsPreset preset = StorageSettingsPreset.DefaultStockpile;
            var makeGrowing = false;
            if (creating)
            {
                var wantType = (zoneType ?? "stockpile").Trim().ToLowerInvariant();
                if (wantType == "stockpile") preset = StorageSettingsPreset.DefaultStockpile;
                else if (wantType == "dumping") preset = StorageSettingsPreset.DumpingStockpile;
                else if (wantType == "growing") makeGrowing = true;
                else return Failure("zoneType must be one of: stockpile, dumping, growing. Got: " + zoneType);
            }

            // Every filter name is resolved BEFORE the zone is registered, so an
            // unknown def name cannot leave a half-configured stockpile behind.
            var filterRequest = ParseFilterRequest(filterPreset, allowSpec, disallowSpec, priority);
            if (filterRequest.Error != null)
                return Failure(filterRequest.Error);
            var wantsFilter = filterRequest.Preset != null || filterRequest.Allow.Any || filterRequest.Disallow.Any;
            if (wantsFilter && makeGrowing)
                return Failure("preset, allow and disallow need a stockpile; zoneType=growing has no storage settings.");

            var wantPriority = filterRequest.Priority;

            // The target key: either the real zone, or the sentinel standing in for
            // the zone op=create has not made yet (a dry run must not consume a
            // zone ID or a generated name).
            var targetKey = creating ? NewZoneKey : (object)target;
            var targetIsStockpile = creating ? !makeGrowing : target is Zone_Stockpile;
            var targetSlotGroup = creating ? null : SlotGroupOf(target);

            var before = Summarize(map, sim, target);
            var results = new List<object>();
            var actions = new List<PlannedAction>();
            var changes = new List<object>();
            var zonesRemoved = new List<object>();
            var accepted = 0;

            foreach (var c in requested)
            {
                string reason;
                string takenFrom = null;

                if (!c.InBounds(map))
                {
                    results.Add(CellRow(c, false, "out of bounds for this map", null));
                    continue;
                }

                // The game's own zoneability test, verbatim: in bounds, not fogged,
                // not in the no-zone map-edge band, and nothing standing on it whose
                // def refuses to overlap zones.
                var zoneable = SafeIsZoneable(c, map, out reason);
                if (!zoneable)
                {
                    results.Add(CellRow(c, false, reason, null));
                    continue;
                }

                var owner = sim.GridOwner(c);
                var inTargetList = sim.Listed(targetKey).Contains(c);

                if (ReferenceEquals(owner, targetKey) && inTargetList)
                {
                    results.Add(CellRow(c, false, "already in this zone", null));
                    continue;
                }

                if (inTargetList)
                {
                    // Zone.AddCell would hit `cells.Contains(c)` and Log.Error,
                    // which pauses the game. This is the phantom state.
                    results.Add(CellRow(c, false,
                        "phantom: this zone's list already holds the cell but the grid gives it to "
                        + OwnerLabel(owner) + "; run op=repair first", null));
                    continue;
                }

                if (owner != null)
                {
                    if (!sim.Listed(owner).Contains(c))
                    {
                        results.Add(CellRow(c, false,
                            "orphan: the grid gives this cell to " + OwnerLabel(owner)
                            + " but that zone's own list does not contain it; RemoveCell would Log.Error (and pause the game). Run op=repair first.", null));
                        continue;
                    }

                    var ownerZone = owner as Zone;
                    var ownerSlotGroup = SlotGroupOf(ownerZone);
                    if (ownerSlotGroup != null && sim.HaulOwner(c) != ownerSlotGroup)
                    {
                        results.Add(CellRow(c, false,
                            "the haul group grid at this cell does not point at " + OwnerLabel(owner)
                            + ", so RemoveCell's Notify_LostCell would Log.Error (and pause the game). Run op=repair first.", null));
                        continue;
                    }

                    takenFrom = OwnerLabel(owner);
                    actions.Add(PlannedAction.RemoveCell(ownerZone, c));
                    changes.Add("Remove (" + c.x + "," + c.z + ") from " + takenFrom + ".");
                    sim.RemoveFromList(owner, c);
                    sim.SetGridOwner(c, null);
                    if (ownerSlotGroup != null)
                        sim.SetHaulOwner(c, null);

                    if (sim.Listed(owner).Count == 0 && !sim.IsDeregistered(owner))
                    {
                        // RemoveCell deregisters a zone whose list has emptied. Not
                        // a separate action -- the game does it for us -- but the
                        // caller must be told the zone is gone.
                        sim.MarkDeregistered(owner);
                        zonesRemoved.Add(new Dictionary<string, object>
                        {
                            { "label", OwnerLabel(owner) },
                            { "id", owner is Zone oz ? SafeId(oz) : -1 },
                            { "reason", "its last cell was taken; Zone.RemoveCell deregisters a zone when its cell list empties" }
                        });
                        changes.Add(OwnerLabel(owner) + " is deregistered: it has no cells left.");
                    }
                }

                if (targetIsStockpile && sim.HaulOwner(c) != null && sim.HaulOwner(c) != targetSlotGroup)
                {
                    results.Add(CellRow(c, false,
                        "the haul group grid already assigns this cell to another storage ("
                        + SafeSlotGroupName(sim.HaulOwner(c)) + "), so Notify_AddedCell would Log.Error (and pause the game)", takenFrom));
                    continue;
                }

                actions.Add(PlannedAction.AddCell(creating ? null : target, c, creating));
                changes.Add("Add (" + c.x + "," + c.z + ") to "
                            + (creating ? "the new zone" : OwnerLabel(targetKey)) + ".");
                sim.AddToList(targetKey, c);
                sim.SetGridOwner(c, targetKey);
                if (targetIsStockpile)
                    sim.SetHaulOwner(c, targetSlotGroup);
                accepted++;
                results.Add(CellRow(c, true, string.Empty, takenFrom));
            }

            // Contiguity is checked against the simulated post-op list, and
            // CheckContiguous is only ever called when the caller opted in --
            // it DELETES every cell it cannot reach from cells[0].
            var finalCells = sim.Listed(targetKey).ToList();
            var contiguous = IsContiguous(finalCells);
            var splitApplied = false;
            if (!contiguous && allowSplit && accepted > 0 && !creating)
            {
                actions.Add(PlannedAction.CheckContiguous(target));
                changes.Add("Call CheckContiguous() on " + OwnerLabel(targetKey)
                            + ", which will DELETE every cell not reachable from its first cell.");
                splitApplied = true;
            }

            if (creating && accepted == 0)
            {
                // Never register an empty zone: it would sit in the zone list with
                // no cells and a consumed ID.
                return Payload("create", dryRun, null, results, before,
                               Summarize(map, sim, null), changes, zonesRemoved,
                               contiguous, splitApplied, accepted,
                               "No cell was acceptable, so no zone was created.");
            }

            Zone created = null;
            string applyError = null;
            Dictionary<string, object> filterSummary = null;
            if (!dryRun)
            {
                if (creating)
                {
                    created = CreateZone(map, makeGrowing, preset, newLabel, wantPriority, out applyError);
                    if (created == null)
                        return Failure(applyError ?? "Could not create the zone.");
                    target = created;
                    changes.Insert(0, "Create " + SafeTypeName(created) + " \"" + SafeLabel(created) + "\" (id " + SafeId(created) + ").");

                    var newStockpile = created as Zone_Stockpile;
                    if (newStockpile != null)
                    {
                        var universe = StockpileFilter.StorableDefs(newStockpile);
                        if (wantsFilter)
                        {
                            try
                            {
                                StockpileFilter.Apply(newStockpile.settings.filter, filterRequest.Preset,
                                                      filterRequest.Allow, filterRequest.Disallow,
                                                      StockpileFilter.ParentFilter(newStockpile), universe, changes);
                            }
                            catch (Exception e)
                            {
                                applyError = "The zone was created but its filter threw " + e.GetType().Name + ": " + e.Message;
                            }
                        }
                        filterSummary = StockpileFilter.Summary(
                            BridgeCommon.Try(() => newStockpile.settings.filter, (ThingFilter)null),
                            CurrentPriority(newStockpile), universe);
                    }
                }
                else if (wantPriority.HasValue)
                {
                    if (SetPriority(target, wantPriority.Value))
                        changes.Add("Set " + OwnerLabel(target) + " priority to " + wantPriority.Value + ".");
                }

                applyError = Apply(map, actions, target);

                // The game does this after its own zone adds, so a stockpile that
                // just grew over a pile of stuff stops asking for it to be hauled
                // somewhere else.
                var sp = target as Zone_Stockpile;
                if (sp != null && sp.slotGroup != null && accepted > 0)
                {
                    try { sp.slotGroup.RemoveHaulDesignationOnStoredThings(); }
                    catch { }
                }
            }
            else if (creating)
            {
                changes.Insert(0, "Create a " + (makeGrowing ? "Zone_Growing" : "Zone_Stockpile")
                                  + (newLabel != null ? " labelled \"" + newLabel + "\"" : " with an auto-generated label") + ".");
                if (!makeGrowing)
                {
                    // The zone does not exist yet -- a dry run must not consume an
                    // ID -- so the filter is planned on the same scratch a new
                    // Zone_Stockpile would start from: SetFromPreset(zoneType).
                    var universe = StockpileFilter.StorableDefs(null);
                    var scratch = new ThingFilter();
                    try
                    {
                        scratch.SetFromPreset(preset);
                        if (wantsFilter)
                            StockpileFilter.Apply(scratch, filterRequest.Preset, filterRequest.Allow,
                                                  filterRequest.Disallow, StockpileFilter.ParentFilter(null),
                                                  universe, changes);
                        filterSummary = StockpileFilter.Summary(scratch, wantPriority ?? StoragePriority.Normal, universe);
                    }
                    catch { filterSummary = null; }
                }
            }
            else if (wantPriority.HasValue)
            {
                changes.Add("Set " + OwnerLabel(target) + " priority to " + wantPriority.Value + ".");
            }

            Dictionary<string, object> after;
            if (dryRun && creating)
            {
                // There is no zone object to summarize -- deliberately, because a
                // dry run must not consume a zone ID or an auto-generated name --
                // so the after block is read straight off the simulation.
                after = new Dictionary<string, object>
                {
                    { "listedCellCount", accepted },
                    { "gridCellCount", accepted },
                    { "phantomCellCount", 0 },
                    { "orphanGridCellCount", 0 },
                    { "exists", true }
                };
            }
            else
            {
                after = dryRun
                    ? Summarize(map, sim, target)
                    : Summarize(map, new Sim(map, map.zoneManager), target);
            }

            var payload = Payload(creating ? "create" : "add", dryRun, target, results, before, after,
                                  changes, zonesRemoved, contiguous, splitApplied, accepted, applyError);
            payload["allowSplit"] = allowSplit;
            payload["filter"] = filterSummary;
            payload["presetDefinition"] = StockpileFilter.Definition(filterRequest.Preset);
            if (creating && target == null)
            {
                payload["zone"] = new Dictionary<string, object>
                {
                    { "label", newLabel },
                    { "id", null },
                    { "type", makeGrowing ? "Zone_Growing" : "Zone_Stockpile" },
                    { "notYetCreated", true }
                };
            }
            return payload;
        }

        // ================================================================ remove

        private static object Remove(Map map, Sim sim, Zone target, int x, int z, int width, int height,
                                     string cellSpec, bool dryRun)
        {
            List<IntVec3> requested;
            string cellError;
            if (!TryParseCells(x, z, width, height, cellSpec, out requested, out cellError))
                return Failure(cellError);
            if (requested.Count == 0)
                return Failure("No cells given. Provide x/z (plus width/height) and/or cells=\"x,z;x,z\".");

            var targetKey = (object)target;
            var slotGroup = SlotGroupOf(target);
            var before = Summarize(map, sim, target);
            var results = new List<object>();
            var actions = new List<PlannedAction>();
            var changes = new List<object>();
            var zonesRemoved = new List<object>();
            var accepted = 0;

            foreach (var c in requested)
            {
                var inList = sim.Listed(targetKey).Contains(c);
                var owner = sim.GridOwner(c);

                if (!inList && !ReferenceEquals(owner, targetKey))
                {
                    results.Add(CellRow(c, false, "not in this zone at all (neither its list nor the grid)", null));
                    continue;
                }

                if (!inList)
                {
                    results.Add(CellRow(c, false,
                        "orphan: the grid gives this cell to the zone but its list does not contain it, so RemoveCell would Log.Error (and pause the game). Run op=repair.", null));
                    continue;
                }

                if (!ReferenceEquals(owner, targetKey))
                {
                    // THE refusal that matters. RemoveCell calls ClearZoneGridCell,
                    // which clears whoever the GRID says owns the cell -- so
                    // removing a phantom cell would blank another zone's grid entry
                    // and make things worse.
                    results.Add(CellRow(c, false,
                        "phantom: the zone lists this cell but the grid gives it to " + OwnerLabel(owner)
                        + ". RemoveCell clears the GRID's owner, so this would blank that zone's cell. Run op=repair.", null));
                    continue;
                }

                if (slotGroup != null && sim.HaulOwner(c) != slotGroup)
                {
                    results.Add(CellRow(c, false,
                        "the haul group grid at this cell does not point at this stockpile, so Notify_LostCell would Log.Error (and pause the game). Run op=repair.", null));
                    continue;
                }

                actions.Add(PlannedAction.RemoveCell(target, c));
                changes.Add("Remove (" + c.x + "," + c.z + ") from " + OwnerLabel(targetKey) + ".");
                sim.RemoveFromList(targetKey, c);
                sim.SetGridOwner(c, null);
                if (slotGroup != null)
                    sim.SetHaulOwner(c, null);
                accepted++;
                results.Add(CellRow(c, true, string.Empty, null));
            }

            var emptied = sim.Listed(targetKey).Count == 0 && accepted > 0;
            if (emptied)
            {
                zonesRemoved.Add(new Dictionary<string, object>
                {
                    { "label", OwnerLabel(targetKey) },
                    { "id", SafeId(target) },
                    { "reason", "every cell was removed; Zone.RemoveCell deregisters a zone when its cell list empties" }
                });
                changes.Add(OwnerLabel(targetKey) + " is deregistered: its last cell was removed.");
            }

            string applyError = null;
            if (!dryRun)
                applyError = Apply(map, actions, target);

            var after = dryRun ? Summarize(map, sim, target) : Summarize(map, new Sim(map, map.zoneManager), target);
            var payload = Payload("remove", dryRun, target, results, before, after, changes,
                                  zonesRemoved, IsContiguous(sim.Listed(targetKey).ToList()),
                                  false, accepted, applyError);
            payload["zoneDeregistered"] = emptied;
            return payload;
        }

        // ================================================================ delete

        private static object Delete(Map map, Sim sim, string zoneSpec, bool dryRun)
        {
            Zone target;
            string resolveError;
            if (!TryResolveZone(map.zoneManager, zoneSpec, out target, out resolveError))
                return Failure(resolveError);

            var before = Summarize(map, sim, target);
            var phantomCount = (int)before["phantomCellCount"];
            var changes = new List<object>();

            if (phantomCount > 0)
            {
                return Refusal("delete", dryRun, target, before,
                    "This zone has " + phantomCount + " phantom cell(s): cells its list holds that the grid gives to another zone. "
                    + "Zone.Delete() is a loop of RemoveCell, and RemoveCell clears the GRID's owner -- so deleting this zone would "
                    + "blank another zone's cells. Run op=repair on it first, then delete.");
            }

            var slotGroup = SlotGroupOf(target);
            if (slotGroup != null)
            {
                foreach (var c in sim.Listed(target))
                {
                    if (sim.HaulOwner(c) != slotGroup)
                    {
                        return Refusal("delete", dryRun, target, before,
                            "The haul group grid at (" + c.x + "," + c.z + ") does not point at this stockpile, so Delete()'s "
                            + "Notify_LostCell would Log.Error and pause the game. Run op=repair first.");
                    }
                }
            }

            changes.Add("Delete " + OwnerLabel(target) + " (" + before["listedCellCount"] + " cells), releasing every cell.");

            string applyError = null;
            if (!dryRun)
            {
                try
                {
                    // Delete(false): no sound. The parameterless Delete() plays the
                    // zone-delete sound on the camera, which is a UI event nobody
                    // asked for from a bridge call.
                    target.Delete(false);
                }
                catch (Exception e)
                {
                    applyError = "Delete failed: " + e.Message;
                }
            }

            var after = dryRun
                ? new Dictionary<string, object>
                    {
                        { "listedCellCount", 0 }, { "gridCellCount", 0 }, { "phantomCellCount", 0 },
                        { "orphanGridCellCount", 0 }, { "exists", false }
                    }
                : Summarize(map, new Sim(map, map.zoneManager), target);

            var payload = Payload("delete", dryRun, target, new List<object>(), before, after,
                                  changes, new List<object>
                                  {
                                      new Dictionary<string, object>
                                      {
                                          { "label", OwnerLabel(target) },
                                          { "id", SafeId(target) },
                                          { "reason", "deleted by request" }
                                      }
                                  }, true, false, 0, applyError);
            payload["zoneDeregistered"] = true;
            return payload;
        }

        // ================================================================ repair

        private static object Repair(Map map, Sim sim, string zoneSpec, bool dryRun)
        {
            var zoneManager = map.zoneManager;
            List<Zone> targets;
            if (string.IsNullOrEmpty(zoneSpec))
            {
                targets = zoneManager.AllZones.Where(zz => zz != null).ToList();
            }
            else
            {
                Zone one;
                string resolveError;
                if (!TryResolveZone(zoneManager, zoneSpec, out one, out resolveError))
                    return Failure(resolveError);
                targets = new List<Zone> { one };
            }

            var beforeTotals = Totals(map, sim, targets);
            var actions = new List<PlannedAction>();
            var changes = new List<object>();
            var zonesRemoved = new List<object>();
            var skippedNotifies = new List<object>();
            var phantomFixed = 0;
            var orphanFixed = 0;

            // PASS 1 -- phantoms. A cell the zone's list holds that the grid gives
            // elsewhere is removed from the LIST only. The grid is not touched:
            // that is the whole difference between this and Delete().
            foreach (var zone in targets)
            {
                var key = (object)zone;
                var slotGroup = SlotGroupOf(zone);
                foreach (var c in sim.Listed(key).ToList())
                {
                    var owner = sim.GridOwner(c);
                    if (ReferenceEquals(owner, key))
                        continue;

                    var notify = false;
                    if (slotGroup != null)
                    {
                        // Notify_LostCell -> ClearCellFor logs an error (and pauses
                        // the game) unless the haul grid really does point here. A
                        // phantom cell usually does NOT, so the notify is skipped
                        // and said out loud rather than being fired for tidiness.
                        notify = sim.HaulOwner(c) == slotGroup;
                        if (!notify)
                        {
                            skippedNotifies.Add(new Dictionary<string, object>
                            {
                                { "zone", SafeLabel(zone) },
                                { "x", c.x }, { "z", c.z },
                                { "call", "SlotGroup.Notify_LostCell" },
                                { "why", "the haul group grid at this cell points at " + SafeSlotGroupName(sim.HaulOwner(c)) + ", not at this stockpile; calling it would Log.Error and pause the game" }
                            });
                        }
                    }

                    actions.Add(PlannedAction.ListRemove(zone, c, notify));
                    sim.RemoveFromList(key, c);
                    if (notify)
                        sim.SetHaulOwner(c, null);
                    phantomFixed++;
                    changes.Add("Drop (" + c.x + "," + c.z + ") from " + SafeLabel(zone)
                                + "'s cell list; the grid gives that cell to " + OwnerLabel(owner)
                                + (notify ? " (slot group notified)" : " (slot group notify skipped, see skippedNotifies)") + ".");
                }

                if (sim.Listed(key).Count == 0 && !sim.IsDeregistered(key) && sim.HadCells(key))
                {
                    actions.Add(PlannedAction.Deregister(zone));
                    sim.MarkDeregistered(key);
                    zonesRemoved.Add(new Dictionary<string, object>
                    {
                        { "label", SafeLabel(zone) },
                        { "id", SafeId(zone) },
                        { "reason", "every cell it listed was a phantom; the zone owns nothing and is deregistered" }
                    });
                    changes.Add(SafeLabel(zone) + " is deregistered: every cell it listed belonged to another zone.");
                }
            }

            // PASS 2 -- orphans, after every phantom has gone, so ownership is
            // settled before anything is added back.
            foreach (var zone in targets)
            {
                var key = (object)zone;
                if (sim.IsDeregistered(key))
                    continue;
                var slotGroup = SlotGroupOf(zone);
                foreach (var c in sim.GridCellsOf(key))
                {
                    if (sim.Listed(key).Contains(c))
                        continue;

                    var notify = false;
                    if (slotGroup != null)
                    {
                        notify = sim.HaulOwner(c) == null;
                        if (!notify && sim.HaulOwner(c) != slotGroup)
                        {
                            skippedNotifies.Add(new Dictionary<string, object>
                            {
                                { "zone", SafeLabel(zone) },
                                { "x", c.x }, { "z", c.z },
                                { "call", "SlotGroup.Notify_AddedCell" },
                                { "why", "the haul group grid at this cell already points at " + SafeSlotGroupName(sim.HaulOwner(c)) + "; calling it would Log.Error and pause the game" }
                            });
                        }
                    }

                    actions.Add(PlannedAction.ListAdd(zone, c, notify));
                    sim.AddToList(key, c);
                    if (notify)
                        sim.SetHaulOwner(c, slotGroup);
                    orphanFixed++;
                    changes.Add("Add (" + c.x + "," + c.z + ") back to " + SafeLabel(zone)
                                + "'s cell list; the grid already gives that cell to it"
                                + (notify ? " (slot group notified)" : " (slot group notify skipped, see skippedNotifies)") + ".");
                }
            }

            string applyError = null;
            if (!dryRun && actions.Count > 0)
                applyError = Apply(map, actions, null);

            // The planning sim is the only thing that knows which zones the run
            // deregistered; a freshly read Sim just would not find them any more.
            // So deregistration comes from `sim` either way, and only the CELL
            // counts are re-read from the game after a real run.
            var afterSim = dryRun ? sim : new Sim(map, map.zoneManager);
            var afterTargets = targets.Where(zz => !sim.IsDeregistered(zz)).ToList();
            var afterTotals = Totals(map, afterSim, afterTargets);

            var perZone = targets.Select(zz => (object)new Dictionary<string, object>
            {
                { "label", SafeLabel(zz) },
                { "id", SafeId(zz) },
                { "type", SafeTypeName(zz) },
                { "deregistered", sim.IsDeregistered(zz) },
                { "listedCellCount", sim.IsDeregistered(zz) ? 0 : afterSim.Listed(zz).Count },
                { "contiguous", sim.IsDeregistered(zz) || IsContiguous(afterSim.Listed(zz).ToList()) }
            }).ToList();

            return new Dictionary<string, object>
            {
                { "success", applyError == null },
                { "tool", ToolName },
                { "op", "repair" },
                { "dryRun", dryRun },
                { "changed", changes.Count > 0 },
                { "presetDefinition", new Dictionary<string, object>(StringComparer.Ordinal) },
                { "filter", null },
                { "zonesExamined", targets.Count },
                { "phantomCellsFixed", phantomFixed },
                { "orphanGridCellsFixed", orphanFixed },
                { "zonesRemoved", zonesRemoved },
                { "skippedNotifies", skippedNotifies },
                { "before", beforeTotals },
                { "after", afterTotals },
                { "zones", perZone },
                { "changes", changes },
                { "cells", new List<object>() },
                { "error", applyError },
                { "notes", Notes() }
            };
        }

        // ================================================================= apply

        /// <summary>
        /// Replays the recorded plan against the real game, in order. Every
        /// precondition that could reach Log.Error was checked during planning, and
        /// nothing between the two can have changed: planning and applying happen
        /// inside the same main-thread hop.
        /// </summary>
        private static string Apply(Map map, List<PlannedAction> actions, Zone createdTarget)
        {
            var errors = new List<string>();
            foreach (var a in actions)
            {
                try
                {
                    var zone = a.Zone ?? createdTarget;
                    if (zone == null)
                    {
                        errors.Add("internal: an action had no zone to apply to");
                        continue;
                    }

                    switch (a.Kind)
                    {
                        case ActionKind.AddCell:
                            zone.AddCell(a.Cell);
                            break;
                        case ActionKind.RemoveCell:
                            zone.RemoveCell(a.Cell);
                            break;
                        case ActionKind.ListRemove:
                            // Zone.cells is a PUBLIC List<IntVec3> in 1.6, so the
                            // repair edits it directly. The zone GRID is left
                            // exactly as it is -- that is the entire point.
                            zone.cells.Remove(a.Cell);
                            if (a.Notify)
                            {
                                var sp = zone as Zone_Stockpile;
                                if (sp != null && sp.slotGroup != null)
                                    sp.slotGroup.Notify_LostCell(a.Cell);
                            }
                            Redraw(map, a.Cell);
                            break;
                        case ActionKind.ListAdd:
                            if (!zone.cells.Contains(a.Cell))
                                zone.cells.Add(a.Cell);
                            if (a.Notify)
                            {
                                var sp2 = zone as Zone_Stockpile;
                                if (sp2 != null && sp2.slotGroup != null)
                                    sp2.slotGroup.Notify_AddedCell(a.Cell);
                            }
                            Redraw(map, a.Cell);
                            break;
                        case ActionKind.Deregister:
                            zone.Deregister();
                            break;
                        case ActionKind.CheckContiguous:
                            zone.CheckContiguous();
                            break;
                    }
                }
                catch (Exception e)
                {
                    errors.Add(a.Kind + " at (" + a.Cell.x + "," + a.Cell.z + "): " + e.Message);
                }
            }
            return errors.Count == 0 ? null : string.Join("; ", errors.ToArray());
        }

        private static void Redraw(Map map, IntVec3 c)
        {
            try { map.mapDrawer.MapMeshDirty(c, MapMeshFlagDefOf.Zone); }
            catch { }
        }

        private static Zone CreateZone(Map map, bool growing, StorageSettingsPreset preset,
                                       string label, StoragePriority? priority, out string error)
        {
            error = null;
            try
            {
                Zone zone;
                if (growing)
                    zone = new Zone_Growing(map.zoneManager);
                else
                    zone = new Zone_Stockpile(preset, map.zoneManager);

                map.zoneManager.RegisterZone(zone);
                if (!string.IsNullOrEmpty(label))
                    zone.label = label;
                if (priority.HasValue)
                    SetPriority(zone, priority.Value);
                return zone;
            }
            catch (Exception e)
            {
                error = "Could not create the zone: " + e.Message;
                return null;
            }
        }

        private static bool SetPriority(Zone zone, StoragePriority priority)
        {
            try
            {
                var sp = zone as Zone_Stockpile;
                if (sp == null || sp.settings == null)
                    return false;
                sp.settings.Priority = priority;
                return true;
            }
            catch { return false; }
        }

        // ================================================================== sim

        /// <summary>
        /// A copy of everything the ops read and write: each affected zone's cell
        /// list, the zone grid, and the haul destination manager's group grid.
        /// Planning mutates the copy; a real run replays the plan against the game.
        /// One planner, two outcomes, so a dry run cannot drift from the real one.
        /// </summary>
        private sealed class Sim
        {
            private readonly Map map;
            private readonly ZoneManager zoneManager;
            private readonly Dictionary<object, HashSet<IntVec3>> listed = new Dictionary<object, HashSet<IntVec3>>();
            private readonly Dictionary<object, int> originalCount = new Dictionary<object, int>();
            private readonly Dictionary<IntVec3, object> gridOverride = new Dictionary<IntVec3, object>();
            private readonly Dictionary<IntVec3, SlotGroup> haulOverride = new Dictionary<IntVec3, SlotGroup>();
            private readonly HashSet<object> deregistered = new HashSet<object>();
            private Dictionary<object, List<IntVec3>> gridIndex;

            public Sim(Map map, ZoneManager zoneManager)
            {
                this.map = map;
                this.zoneManager = zoneManager;
            }

            public HashSet<IntVec3> Listed(object key)
            {
                if (key == null)
                    return new HashSet<IntVec3>();
                HashSet<IntVec3> set;
                if (listed.TryGetValue(key, out set))
                    return set;

                set = new HashSet<IntVec3>();
                var zone = key as Zone;
                if (zone != null)
                {
                    // The `cells` FIELD. Zone.Cells (capital C) shuffles in place.
                    try
                    {
                        if (zone.cells != null)
                            foreach (var c in zone.cells)
                                set.Add(c);
                    }
                    catch { }
                }
                listed[key] = set;
                originalCount[key] = set.Count;
                return set;
            }

            public bool HadCells(object key)
            {
                Listed(key);
                int n;
                return originalCount.TryGetValue(key, out n) && n > 0;
            }

            public object GridOwner(IntVec3 c)
            {
                object o;
                if (gridOverride.TryGetValue(c, out o))
                    return o;
                try { return zoneManager.ZoneAt(c); }
                catch { return null; }
            }

            public void SetGridOwner(IntVec3 c, object owner)
            {
                gridOverride[c] = owner;
                gridIndex = null;
            }

            public SlotGroup HaulOwner(IntVec3 c)
            {
                SlotGroup g;
                if (haulOverride.TryGetValue(c, out g))
                    return g;
                try
                {
                    return map.haulDestinationManager == null ? null : map.haulDestinationManager.SlotGroupAt(c);
                }
                catch { return null; }
            }

            public void SetHaulOwner(IntVec3 c, SlotGroup g) { haulOverride[c] = g; }

            public void AddToList(object key, IntVec3 c) { Listed(key).Add(c); }
            public void RemoveFromList(object key, IntVec3 c) { Listed(key).Remove(c); }

            public bool IsDeregistered(object key) { return key != null && deregistered.Contains(key); }
            public void MarkDeregistered(object key) { if (key != null) deregistered.Add(key); }

            /// <summary>
            /// Every map cell the (simulated) zone grid assigns to this zone.
            /// Orphan cells are by definition NOT in the zone's list, so the only
            /// honest way to find them is a whole-map sweep. It is done once and
            /// cached; any SetGridOwner invalidates the cache.
            /// </summary>
            public List<IntVec3> GridCellsOf(object key)
            {
                var zone = key as Zone;
                if (zone == null)
                    return new List<IntVec3>();
                EnsureGridIndex();
                List<IntVec3> result;
                return gridIndex.TryGetValue(key, out result) ? result : new List<IntVec3>();
            }

            private void EnsureGridIndex()
            {
                if (gridIndex != null)
                    return;
                gridIndex = new Dictionary<object, List<IntVec3>>();
                try
                {
                    foreach (var c in map.AllCells)
                    {
                        var owner = GridOwner(c);
                        if (owner == null)
                            continue;
                        List<IntVec3> list;
                        if (!gridIndex.TryGetValue(owner, out list))
                        {
                            list = new List<IntVec3>();
                            gridIndex[owner] = list;
                        }
                        list.Add(c);
                    }
                }
                catch { }
            }
        }

        // ============================================================== plan rows

        private enum ActionKind { AddCell, RemoveCell, ListAdd, ListRemove, Deregister, CheckContiguous }

        private sealed class PlannedAction
        {
            public ActionKind Kind;
            public Zone Zone;
            public IntVec3 Cell;
            public bool Notify;

            public static PlannedAction AddCell(Zone zone, IntVec3 c, bool deferredZone)
            {
                return new PlannedAction { Kind = ActionKind.AddCell, Zone = deferredZone ? null : zone, Cell = c };
            }
            public static PlannedAction RemoveCell(Zone zone, IntVec3 c)
            {
                return new PlannedAction { Kind = ActionKind.RemoveCell, Zone = zone, Cell = c };
            }
            public static PlannedAction ListAdd(Zone zone, IntVec3 c, bool notify)
            {
                return new PlannedAction { Kind = ActionKind.ListAdd, Zone = zone, Cell = c, Notify = notify };
            }
            public static PlannedAction ListRemove(Zone zone, IntVec3 c, bool notify)
            {
                return new PlannedAction { Kind = ActionKind.ListRemove, Zone = zone, Cell = c, Notify = notify };
            }
            public static PlannedAction Deregister(Zone zone)
            {
                return new PlannedAction { Kind = ActionKind.Deregister, Zone = zone };
            }
            public static PlannedAction CheckContiguous(Zone zone)
            {
                return new PlannedAction { Kind = ActionKind.CheckContiguous, Zone = zone };
            }
        }

        // ============================================================== payloads

        private static Dictionary<string, object> Payload(
            string op, bool dryRun, Zone target, List<object> cellRows,
            Dictionary<string, object> before, Dictionary<string, object> after,
            List<object> changes, List<object> zonesRemoved,
            bool contiguous, bool splitApplied, int accepted, string error)
        {
            return new Dictionary<string, object>
            {
                { "success", error == null },
                { "tool", ToolName },
                { "op", op },
                { "dryRun", dryRun },
                // One recorded mutation is one change. A run that accepted no
                // cell and deregistered nothing says so in one field.
                { "changed", changes.Count > 0 },
                { "presetDefinition", new Dictionary<string, object>(StringComparer.Ordinal) },
                { "filter", null },
                { "zone", target == null
                    ? null
                    : new Dictionary<string, object>
                        {
                            { "label", SafeLabel(target) },
                            { "id", SafeId(target) },
                            { "type", SafeTypeName(target) }
                        } },
                { "cellsAccepted", accepted },
                { "cellsRequested", cellRows.Count },
                { "cells", cellRows },
                { "before", before },
                { "after", after },
                { "contiguous", contiguous },
                { "checkContiguousCalled", splitApplied },
                { "zonesRemoved", zonesRemoved },
                { "changes", changes },
                { "error", error },
                { "notes", Notes() }
            };
        }

        private static object Refusal(string op, bool dryRun, Zone target,
                                      Dictionary<string, object> before, string why)
        {
            return new Dictionary<string, object>
            {
                { "success", false },
                { "tool", ToolName },
                { "op", op },
                { "dryRun", dryRun },
                { "refused", true },
                { "changed", false },
                { "presetDefinition", new Dictionary<string, object>(StringComparer.Ordinal) },
                { "filter", null },
                { "error", why },
                { "zone", target == null ? null : new Dictionary<string, object>
                    {
                        { "label", SafeLabel(target) },
                        { "id", SafeId(target) },
                        { "type", SafeTypeName(target) }
                    } },
                { "cells", new List<object>() },
                { "before", before },
                { "after", before },
                { "changes", new List<object>() },
                { "zonesRemoved", new List<object>() },
                { "notes", Notes() }
            };
        }

        private static Dictionary<string, object> CellRow(IntVec3 c, bool accepted, string reason, string takenFrom)
        {
            return new Dictionary<string, object>
            {
                { "x", c.x }, { "z", c.z },
                // All four keys on every row, including the empty reason on an
                // accepted cell: a caller must never have to infer a verdict from
                // an absent key.
                { "accepted", accepted },
                { "reason", reason ?? string.Empty },
                { "takenFrom", takenFrom }
            };
        }

        private static Dictionary<string, object> Summarize(Map map, Sim sim, Zone zone)
        {
            if (zone == null)
            {
                return new Dictionary<string, object>
                {
                    { "listedCellCount", 0 }, { "gridCellCount", 0 },
                    { "phantomCellCount", 0 }, { "orphanGridCellCount", 0 }, { "exists", false }
                };
            }

            var key = (object)zone;
            var listed = sim.Listed(key);
            var gridCells = sim.GridCellsOf(key);
            var phantom = listed.Count(c => !ReferenceEquals(sim.GridOwner(c), key));
            var orphan = gridCells.Count(c => !listed.Contains(c));

            return new Dictionary<string, object>
            {
                { "listedCellCount", listed.Count },
                { "gridCellCount", gridCells.Count },
                { "phantomCellCount", phantom },
                { "orphanGridCellCount", orphan },
                { "exists", !sim.IsDeregistered(key) }
            };
        }

        private static Dictionary<string, object> Totals(Map map, Sim sim, List<Zone> zones)
        {
            long listed = 0, grid = 0;
            var phantom = 0;
            var orphan = 0;
            foreach (var zone in zones)
            {
                var s = Summarize(map, sim, zone);
                listed += (int)s["listedCellCount"];
                grid += (int)s["gridCellCount"];
                phantom += (int)s["phantomCellCount"];
                orphan += (int)s["orphanGridCellCount"];
            }
            return new Dictionary<string, object>
            {
                { "zones", zones.Count },
                { "listedCells", listed },
                { "gridCells", grid },
                { "phantomCellCount", phantom },
                { "orphanGridCellCount", orphan },
                { "consistent", phantom == 0 && orphan == 0 }
            };
        }

        private static Dictionary<string, object> Notes()
        {
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "dryRunDefault", "dryRun is TRUE unless the caller passes false. A dry run plans the whole operation against an in-memory copy of the cell lists, the zone grid and the haul group grid, and returns exactly what a real run would do." },
                { "addRemovesFirst", "op=add calls owner.RemoveCell(c) before target.AddCell(c). Zone.AddCell only overwrites the grid pointer and leaves the cell in the old zone's list, which is how a zone ends up claiming cells the grid gives to somebody else." },
                { "removeRefusesPhantoms", "op=remove refuses any cell where the zone's list and the grid disagree, because Zone.RemoveCell calls ClearZoneGridCell -- it clears whoever the GRID says owns the cell, so removing a phantom would blank a different zone's entry." },
                { "repairTouchesTheListOnly", "op=repair edits Zone.cells (a public List<IntVec3> in 1.6) and never the zone grid. Slot-group notifies are fired only when the haul destination grid agrees; where it does not, the notify is skipped and listed under skippedNotifies, because ClearCellFor / SetCellFor call Log.Error, and Log.Error calls TickManager.Pause()." },
                { "checkContiguousDeletes", "Zone.CheckContiguous() removes every cell it cannot reach from cells[0]. It is called only when allowSplit is true; otherwise the tool reports contiguous:false and leaves the zone intact." },
                { "deleteUsesNoSound", "op=delete calls Zone.Delete(false). The parameterless Delete() plays the zone-delete sound on the camera." },
                { "priority", "Setting StorageSettings.Priority re-sorts the haul destination manager, which is what the game does too. Unstored is rejected: the UI cannot produce it and it would make the stockpile inert." },
                { "filterIsPlannedOnACopy", "op=filter applies the whole plan to a scratch ThingFilter seeded with CopyAllowancesFrom(the live one) first. That is what a dry run returns and what `changed` is computed from; a real run replays the identical calls against the live filter and reads `after` back off it." },
                { "filterPresetUniverse", "Every preset is defined over the defs the stockpile's PARENT filter allows -- StorageSettings.EverStorableFixedSettings(), the Root category restricted to ThingDef.EverStorable(true). storableDefCount is that number, so perishables + nonperishables sum to it exactly and preset=everything reaches it." },
                { "filterNamesResolveFirst", "allow and disallow names are resolved to a ThingCategoryDef or a ThingDef before anything is written, defName before label, and a name matching nothing refuses the whole call. A category is expanded by the game's own SetAllow(ThingCategoryDef, ...), which cascades over DescendantThingDefs exactly as the storage tab's tree checkbox does." },
                { "watchOnCreate", "op=create cannot open a menu on a zone that does not exist yet, so its watch selects the new zone AFTER it is registered rather than before. Every other op opens the menu first and lets the change land inside it." },
                { "emptyZonesDeregister", "Zone.RemoveCell deregisters a zone whose cell list has emptied. That happens as a side effect of ordinary adds and removes, and every occurrence is listed in zonesRemoved." }
            };
        }

        // =============================================================== helpers

        private static bool SafeIsZoneable(IntVec3 c, Map map, out string reason)
        {
            reason = string.Empty;
            try
            {
                var report = Designator_ZoneAdd.IsZoneableCell(c, map);
                if (report.Accepted)
                    return true;
                // AcceptanceReport.Reason is empty for a bare `false`, so the
                // fallback names the three things the game actually tests.
                reason = string.IsNullOrEmpty(report.Reason)
                    ? "not zoneable: fogged, inside the no-zone map-edge band, or occupied by a thing whose def cannot overlap zones"
                    : report.Reason;
                return false;
            }
            catch (Exception e)
            {
                reason = "IsZoneableCell threw: " + e.Message;
                return false;
            }
        }

        private static bool TryResolveZone(ZoneManager zoneManager, string spec, out Zone zone, out string error)
        {
            zone = null;
            error = null;

            if (string.IsNullOrEmpty(spec))
            {
                error = "A zone is required for this op. Pass its exact label or its numeric id as a string.";
                return false;
            }

            List<Zone> all;
            try { all = zoneManager.AllZones.Where(zz => zz != null).ToList(); }
            catch (Exception e) { error = "Could not read the zone list: " + e.Message; return false; }

            var trimmed = spec.Trim();

            var exact = all.Where(zz => string.Equals(SafeLabel(zz), trimmed, StringComparison.OrdinalIgnoreCase)).ToList();
            if (exact.Count == 1) { zone = exact[0]; return true; }
            if (exact.Count > 1)
            {
                error = "More than one zone is labelled \"" + trimmed + "\". Use the numeric id instead: "
                        + string.Join(", ", exact.Select(zz => SafeId(zz).ToString()).ToArray());
                return false;
            }

            int id;
            if (int.TryParse(trimmed, out id))
            {
                var byId = all.FirstOrDefault(zz => SafeId(zz) == id);
                if (byId != null) { zone = byId; return true; }
            }

            var partial = all.Where(zz => (SafeLabel(zz) ?? string.Empty)
                              .IndexOf(trimmed, StringComparison.OrdinalIgnoreCase) >= 0).ToList();
            if (partial.Count == 1) { zone = partial[0]; return true; }
            if (partial.Count > 1)
            {
                error = "\"" + trimmed + "\" matches " + partial.Count + " zones: "
                        + string.Join(", ", partial.Select(zz => SafeLabel(zz) + " (id " + SafeId(zz) + ")").ToArray())
                        + ". Use an exact label or the numeric id.";
                return false;
            }

            error = "No zone matches \"" + trimmed + "\". Known zones: "
                    + (all.Count == 0 ? "(none)" : string.Join(", ", all.Select(zz => SafeLabel(zz) + " (id " + SafeId(zz) + ")").ToArray()));
            return false;
        }

        private static bool TryParseCells(int x, int z, int width, int height, string cellSpec,
                                          out List<IntVec3> cells, out string error)
        {
            cells = new List<IntVec3>();
            error = null;
            var seen = new HashSet<IntVec3>();

            if (x >= 0 && z >= 0)
            {
                if (width < 1) width = 1;
                if (height < 1) height = 1;
                if ((long)width * height > 10000)
                {
                    error = "The rect covers " + ((long)width * height) + " cells; the cap is 10000.";
                    return false;
                }
                for (var dz = 0; dz < height; dz++)
                    for (var dx = 0; dx < width; dx++)
                    {
                        var c = new IntVec3(x + dx, 0, z + dz);
                        if (seen.Add(c)) cells.Add(c);
                    }
            }

            if (!string.IsNullOrEmpty(cellSpec))
            {
                foreach (var part in cellSpec.Split(new[] { ';', '|', '\n', '\r' }, StringSplitOptions.RemoveEmptyEntries))
                {
                    var bits = part.Split(',');
                    if (bits.Length != 2)
                    {
                        error = "Could not read cell \"" + part.Trim() + "\". Expected \"x,z\" pairs separated by ';'.";
                        return false;
                    }
                    int cxv, czv;
                    if (!int.TryParse(bits[0].Trim(), out cxv) || !int.TryParse(bits[1].Trim(), out czv))
                    {
                        error = "Could not read cell \"" + part.Trim() + "\" as two integers.";
                        return false;
                    }
                    var c = new IntVec3(cxv, 0, czv);
                    if (seen.Add(c)) cells.Add(c);
                }
            }

            return true;
        }

        private static bool TryParsePriority(string text, out StoragePriority priority)
        {
            priority = StoragePriority.Normal;
            if (string.IsNullOrEmpty(text))
                return false;
            foreach (StoragePriority candidate in Enum.GetValues(typeof(StoragePriority)))
            {
                if (!string.Equals(candidate.ToString(), text.Trim(), StringComparison.OrdinalIgnoreCase))
                    continue;
                // Unstored is the "no storage here" sentinel; the game's own UI
                // never offers it and a stockpile set to it would accept nothing.
                if (candidate == StoragePriority.Unstored)
                    return false;
                priority = candidate;
                return true;
            }
            return false;
        }

        /// <summary>
        /// 4-connected flood fill over a copy. Deliberately not
        /// <c>Zone.CheckContiguous()</c>, which answers the question by deleting
        /// the cells that make it false.
        /// </summary>
        private static bool IsContiguous(List<IntVec3> cells)
        {
            if (cells == null || cells.Count <= 1)
                return true;
            var remaining = new HashSet<IntVec3>(cells);
            var total = remaining.Count;
            var stack = new Stack<IntVec3>();
            var start = cells[0];
            stack.Push(start);
            remaining.Remove(start);
            var found = 1;
            while (stack.Count > 0)
            {
                var c = stack.Pop();
                for (var i = 0; i < 4; i++)
                {
                    var n = c + GenAdj.CardinalDirections[i];
                    if (remaining.Remove(n))
                    {
                        found++;
                        stack.Push(n);
                    }
                }
            }
            return found >= total;
        }

        private static SlotGroup SlotGroupOf(Zone zone)
        {
            try
            {
                var sp = zone as Zone_Stockpile;
                return sp == null ? null : sp.slotGroup;
            }
            catch { return null; }
        }

        private static string SafeSlotGroupName(SlotGroup group)
        {
            if (group == null)
                return "nothing";
            try { return group.GetName(); }
            catch { return "an unnamed slot group"; }
        }

        private static string OwnerLabel(object key)
        {
            if (key == null) return "no zone";
            if (ReferenceEquals(key, NewZoneKey)) return "the new zone";
            var zone = key as Zone;
            return zone == null ? "an unknown zone" : (SafeLabel(zone) ?? "an unnamed zone");
        }

        private static string SafeLabel(Zone zone)
        {
            try
            {
                if (!string.IsNullOrEmpty(zone.label))
                    return zone.label;
                return zone.BaseLabel;
            }
            catch { return null; }
        }

        private static int SafeId(Zone zone)
        {
            try { return zone.ID; }
            catch { return -1; }
        }

        private static string SafeTypeName(Zone zone)
        {
            try { return zone.GetType().Name; }
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

    /// <summary>
    /// The stockpile storage filter, read and written through the game's own
    /// ThingFilter API. Shared by home/zone_cells (op=filter, op=create) and
    /// home/list_zones (filter: true) so both describe a filter with one set of
    /// words and one set of counts.
    ///
    /// Every preset is defined over the same universe: the defs the stockpile's
    /// PARENT filter allows, which is StorageSettings.EverStorableFixedSettings()
    /// -- the Root thing category restricted to ThingDef.EverStorable(true).
    /// That is the game's own answer to "what can a stockpile ever hold", so it
    /// includes minifiable furniture, and the storage tab's Allow All button
    /// produces exactly this set (ThingFilterUI: SetAllowAll(parentFilter)).
    ///
    /// Two Log.Error landmines, both pre-checked rather than caught, because
    /// Verse.Log.Error calls TickManager.Pause():
    ///   1. ThingFilter.SetAllow(ThingCategoryDef, ...) logs when
    ///      ThingCategoryNodeDatabase.initialized is false. A call naming a
    ///      category is refused before it is made when the tree is not up.
    ///   2. StatWorker.GetValueUnfinalized logs once for a disabled stat, but
    ///      only for a StatRequest carrying a Pawn. GetStatValueAbstract on a
    ///      ThingDef passes a null Thing, so that branch is unreachable here.
    /// </summary>
    internal static class StockpileFilter
    {
        internal const int SampleSize = 10;

        internal static readonly string[] PresetNames =
            { "everything", "nothing", "food", "perishables", "nonperishables", "outdoorSafe" };

        // ------------------------------------------------------------ universe

        /// <summary>
        /// The stockpile's parent filter -- what it could ever be told to hold.
        /// Zone_Stockpile.GetParentStoreSettings() returns the shared
        /// EverStorableFixedSettings singleton; null when the read throws.
        /// </summary>
        internal static ThingFilter ParentFilter(Zone_Stockpile stockpile)
        {
            try
            {
                var parent = stockpile == null
                    ? StorageSettings.EverStorableFixedSettings()
                    : stockpile.GetParentStoreSettings();
                return parent == null ? null : parent.filter;
            }
            catch { return null; }
        }

        /// <summary>
        /// Every def the universe holds, in DefDatabase order so a sample is
        /// stable between calls. Falls back to the DefDatabase sweep the parent
        /// filter itself performs if the parent cannot be read.
        /// </summary>
        internal static List<ThingDef> StorableDefs(Zone_Stockpile stockpile)
        {
            var universe = new List<ThingDef>();
            var parent = ParentFilter(stockpile);
            var allowed = new HashSet<ThingDef>();
            if (parent != null)
            {
                try
                {
                    foreach (var def in parent.AllowedThingDefs)
                        if (def != null)
                            allowed.Add(def);
                }
                catch { allowed.Clear(); }
            }

            try
            {
                foreach (var def in DefDatabase<ThingDef>.AllDefs)
                {
                    if (def == null)
                        continue;
                    var local = def;
                    var storable = allowed.Count > 0
                        ? allowed.Contains(local)
                        : BridgeCommon.Try(() => local.EverStorable(true), false);
                    if (storable)
                        universe.Add(local);
                }
            }
            catch { }

            return universe;
        }

        // ------------------------------------------------------------- presets

        /// <summary>The canonical preset name, or null when the text names none.</summary>
        internal static string NormalizePreset(string text)
        {
            if (string.IsNullOrEmpty(text))
                return null;
            var trimmed = text.Trim();
            foreach (var name in PresetNames)
                if (string.Equals(name, trimmed, StringComparison.OrdinalIgnoreCase))
                    return name;
            return null;
        }

        /// <summary>Carries a rot timer. HasComp&lt;T&gt; matches subclasses of
        /// CompRottable too; HasComp(Type) compares compClass by reference and
        /// would miss them.</summary>
        internal static bool IsRottable(ThingDef def)
        {
            return BridgeCommon.Try(() => def.HasComp<CompRottable>(), false);
        }

        /// <summary>ThingDef.IsNutritionGivingIngestible: an ingestible whose
        /// cached nutrition is above zero.</summary>
        internal static bool IsFood(ThingDef def)
        {
            return BridgeCommon.Try(() => def.IsNutritionGivingIngestible, false);
        }

        /// <summary>Neither rots nor weathers. CanEverDeteriorate is checked
        /// alongside the rate because a def with useHitPoints false never
        /// deteriorates whatever its stat says.</summary>
        internal static bool IsOutdoorSafe(ThingDef def)
        {
            if (IsRottable(def))
                return false;
            if (!BridgeCommon.Try(() => def.CanEverDeteriorate, true))
                return true;
            return BridgeCommon.Try(() => def.GetStatValueAbstract(StatDefOf.DeteriorationRate, null), 1f) <= 0f;
        }

        /// <summary>The defs a computed preset allows. Null for the two presets
        /// that are the game's own buttons rather than a def set.</summary>
        internal static List<ThingDef> PresetDefs(string preset, List<ThingDef> universe)
        {
            if (preset == "food")
                return universe.Where(IsFood).ToList();
            if (preset == "perishables")
                return universe.Where(IsRottable).ToList();
            if (preset == "nonperishables")
                return universe.Where(d => !IsRottable(d)).ToList();
            if (preset == "outdoorSafe")
                return universe.Where(IsOutdoorSafe).ToList();
            return null;
        }

        /// <summary>One sentence per preset the call used, so the reply defines
        /// what it did rather than leaving a caller to guess.</summary>
        internal static Dictionary<string, object> Definition(string preset)
        {
            var d = new Dictionary<string, object>(StringComparer.Ordinal);
            if (preset == null)
                return d;
            switch (preset)
            {
                case "everything":
                    d["everything"] = "Every def a stockpile can ever hold: ThingFilter.SetAllowAll(parent filter), which is the storage tab's own Allow All button. The parent filter is StorageSettings.EverStorableFixedSettings() -- the Root thing category restricted to ThingDef.EverStorable(true), so minifiable furniture is in.";
                    break;
                case "nothing":
                    d["nothing"] = "Nothing at all: ThingFilter.SetDisallowAll(), which is the storage tab's own Clear All button. It also re-enables every configurable special filter, exactly as that button does.";
                    break;
                case "food":
                    d["food"] = "Storable defs where ThingDef.IsNutritionGivingIngestible is true -- an ingestible whose nutrition is above zero. That is the game's own 'has food value' test, so kibble, hay and other animal feed ARE included, as are nutrition-carrying drugs; it is not the human-meals subset.";
                    break;
                case "perishables":
                    d["perishables"] = "Storable defs carrying a CompRottable (ThingDef.HasComp<CompRottable>(), which matches subclasses): raw food, meals, corpses, anything with a rot timer.";
                    break;
                case "nonperishables":
                    d["nonperishables"] = "Every storable def that is NOT perishable -- the exact complement of the perishables set over the same universe, so the two counts sum to storableDefCount.";
                    break;
                case "outdoorSafe":
                    d["outdoorSafe"] = "Storable defs that neither rot nor weather: no CompRottable, and either ThingDef.CanEverDeteriorate is false or GetStatValueAbstract(StatDefOf.DeteriorationRate) is at most zero -- the game's own deterioration number. A subset of nonperishables.";
                    break;
            }
            return d;
        }

        // ------------------------------------------------------- name resolving

        /// <summary>What an allow/disallow list parsed into. Unresolved names are
        /// the reason a call is refused before anything is written.</summary>
        internal sealed class Resolved
        {
            internal readonly List<ThingCategoryDef> Categories = new List<ThingCategoryDef>();
            internal readonly List<ThingDef> Defs = new List<ThingDef>();
            internal readonly List<string> Unresolved = new List<string>();
            internal bool Any { get { return Categories.Count > 0 || Defs.Count > 0; } }
        }

        /// <summary>
        /// Split on commas, semicolons and newlines, then resolve each name to a
        /// ThingCategoryDef or a ThingDef. Search order is fixed so the same text
        /// always resolves to the same def: category defName, thing defName,
        /// category label, thing label.
        /// </summary>
        internal static Resolved Resolve(string spec)
        {
            var result = new Resolved();
            if (string.IsNullOrEmpty(spec))
                return result;

            foreach (var raw in spec.Split(new[] { ',', ';', '\n', '\r' }, StringSplitOptions.RemoveEmptyEntries))
            {
                var name = raw.Trim();
                if (name.Length == 0)
                    continue;

                var category = FindCategory(name, true);
                if (category != null) { result.Categories.Add(category); continue; }

                var def = FindThing(name, true);
                if (def != null) { result.Defs.Add(def); continue; }

                category = FindCategory(name, false);
                if (category != null) { result.Categories.Add(category); continue; }

                def = FindThing(name, false);
                if (def != null) { result.Defs.Add(def); continue; }

                result.Unresolved.Add(name);
            }
            return result;
        }

        private static ThingCategoryDef FindCategory(string name, bool byDefName)
        {
            try
            {
                foreach (var c in DefDatabase<ThingCategoryDef>.AllDefs)
                {
                    if (c == null)
                        continue;
                    var text = byDefName ? c.defName : c.label;
                    if (!string.IsNullOrEmpty(text) && string.Equals(text, name, StringComparison.OrdinalIgnoreCase))
                        return c;
                }
            }
            catch { }
            return null;
        }

        private static ThingDef FindThing(string name, bool byDefName)
        {
            try
            {
                foreach (var d in DefDatabase<ThingDef>.AllDefs)
                {
                    if (d == null)
                        continue;
                    var text = byDefName ? d.defName : d.label;
                    if (!string.IsNullOrEmpty(text) && string.Equals(text, name, StringComparison.OrdinalIgnoreCase))
                        return d;
                }
            }
            catch { }
            return null;
        }

        /// <summary>Whether the thing-category tree is up. SetAllow on a
        /// ThingCategoryDef calls Log.Error when it is not, and Log.Error pauses
        /// the game, so a call naming a category is refused instead.</summary>
        internal static bool CategoryTreeReady()
        {
            return BridgeCommon.Try(() => ThingCategoryNodeDatabase.initialized, false);
        }

        // --------------------------------------------------------------- apply

        /// <summary>
        /// Preset first, then allow, then disallow -- the order a caller reads
        /// the arguments in. `changes` collects one English sentence per step and
        /// may be null. The filter is either the live one or a scratch copy; this
        /// method cannot tell and does not need to.
        /// </summary>
        internal static void Apply(ThingFilter filter, string preset, Resolved allow, Resolved disallow,
                                   ThingFilter parent, List<ThingDef> universe, List<object> changes)
        {
            if (preset == "everything")
            {
                filter.SetAllowAll(parent);
                if (changes != null)
                    changes.Add("Allow everything a stockpile can hold (" + universe.Count + " defs) -- the storage tab's Allow All.");
            }
            else if (preset == "nothing")
            {
                filter.SetDisallowAll();
                if (changes != null)
                    changes.Add("Disallow everything -- the storage tab's Clear All.");
            }
            else if (preset != null)
            {
                var defs = PresetDefs(preset, universe);
                filter.SetDisallowAll();
                foreach (var def in defs)
                    filter.SetAllow(def, true);
                if (changes != null)
                    changes.Add("Set the filter from preset \"" + preset + "\": " + defs.Count
                                + " of " + universe.Count + " storable defs allowed.");
            }

            foreach (var category in allow.Categories)
            {
                filter.SetAllow(category, true);
                if (changes != null)
                    changes.Add("Allow category " + Label(category) + " and everything under it.");
            }
            foreach (var def in allow.Defs)
            {
                filter.SetAllow(def, true);
                if (changes != null)
                    changes.Add("Allow " + Label(def) + ".");
            }
            foreach (var category in disallow.Categories)
            {
                filter.SetAllow(category, false);
                if (changes != null)
                    changes.Add("Disallow category " + Label(category) + " and everything under it.");
            }
            foreach (var def in disallow.Defs)
            {
                filter.SetAllow(def, false);
                if (changes != null)
                    changes.Add("Disallow " + Label(def) + ".");
            }
        }

        /// <summary>The allowed defs as a set, for a before/after comparison that
        /// does not depend on enumeration order.</summary>
        internal static HashSet<ThingDef> AllowedSet(ThingFilter filter)
        {
            var set = new HashSet<ThingDef>();
            if (filter == null)
                return set;
            try
            {
                foreach (var def in filter.AllowedThingDefs)
                    if (def != null)
                        set.Add(def);
            }
            catch { }
            return set;
        }

        // ------------------------------------------------------------- summary

        /// <summary>
        /// The block home/list_zones and home/zone_cells both emit for a
        /// stockpile filter. Every count is over the same universe, so
        /// allowedDefCount and storableDefCount are directly comparable and
        /// perishables + nonperishables sum to storableDefCount exactly.
        /// </summary>
        internal static Dictionary<string, object> Summary(ThingFilter filter, StoragePriority? priority,
                                                           List<ThingDef> universe)
        {
            var summary = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "allowedDefCount", null },
                { "storableDefCount", universe == null ? 0 : universe.Count },
                { "priority", priority.HasValue ? priority.Value.ToString() : null },
                { "categoriesFullyAllowed", new List<object>() },
                { "categoriesPartlyAllowed", new List<object>() },
                { "allowsRottable", false },
                { "allowedRottableCount", 0 },
                { "sampleAllowed", new List<object>() },
                { "sampleTruncated", false }
            };
            if (filter == null || universe == null)
                return summary;

            var allowed = AllowedSet(filter);
            var inUniverse = universe.Where(allowed.Contains).ToList();
            summary["allowedDefCount"] = BridgeCommon.Try(() => filter.AllowedDefCount, inUniverse.Count);

            var rottable = inUniverse.Count(IsRottable);
            summary["allowedRottableCount"] = rottable;
            summary["allowsRottable"] = rottable > 0;

            var sample = new List<object>();
            foreach (var def in inUniverse)
            {
                if (sample.Count >= SampleSize)
                    break;
                sample.Add(Label(def));
            }
            summary["sampleAllowed"] = sample;
            summary["sampleTruncated"] = inUniverse.Count > sample.Count;

            var full = new List<object>();
            var part = new List<object>();
            foreach (var category in TopLevelCategories())
            {
                var storableChildren = StorableDescendants(category, universe);
                if (storableChildren.Count == 0)
                    continue;
                var allowedChildren = storableChildren.Count(allowed.Contains);
                if (allowedChildren == storableChildren.Count)
                    full.Add(Label(category));
                else if (allowedChildren > 0)
                    part.Add(Label(category));
            }
            summary["categoriesFullyAllowed"] = full;
            summary["categoriesPartlyAllowed"] = part;
            return summary;
        }

        /// <summary>The categories the storage tab shows at the top of its tree:
        /// the children of ThingCategoryDefOf.Root.</summary>
        private static List<ThingCategoryDef> TopLevelCategories()
        {
            try
            {
                var root = ThingCategoryDefOf.Root;
                if (root == null || root.childCategories == null)
                    return new List<ThingCategoryDef>();
                return root.childCategories.Where(c => c != null).ToList();
            }
            catch { return new List<ThingCategoryDef>(); }
        }

        private static List<ThingDef> StorableDescendants(ThingCategoryDef category, List<ThingDef> universe)
        {
            try
            {
                var inCategory = new HashSet<ThingDef>(category.DescendantThingDefs.Where(d => d != null));
                return universe.Where(inCategory.Contains).ToList();
            }
            catch { return new List<ThingDef>(); }
        }

        internal static string Label(Def def)
        {
            if (def == null)
                return "(null)";
            var label = BridgeCommon.SafeString(() => def.label);
            if (!string.IsNullOrEmpty(label))
                return label;
            return BridgeCommon.SafeString(() => def.defName) ?? "(unnamed)";
        }
    }

}
