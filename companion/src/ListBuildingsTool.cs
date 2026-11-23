using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using RimWorld;
using RimBridgeServer.Sdk;
using Verse;

namespace HomeBridge.BridgeTools
{
    /// <summary>
    /// home/list_buildings — every building, blueprint and frame on the map in
    /// ONE call, with the construction deficit and the bill queue attached.
    ///
    /// ## Why this exists (M's to-do list, 2026-08-31, items 2 and 3)
    ///
    ///     2. "A construction-deficit check in the 5-minute Scout rotation --
    ///         list every building/blueprint/frame and what it still needs, so a
    ///         thing you are waiting on that is not being built gets reported
    ///         instead of silently sitting."
    ///
    ///     3. "A whole-map list of buildings and jobs for Scouts, background
    ///         calls only, no clicking. Seeded with colony context they could
    ///         then say things like 'we are low on food but the butcher spot has
    ///         no queued tasks.'"
    ///
    /// The design note recorded them as one missing tool, because the session
    /// that wrote them hit the gap twice in an hour: `home/list_things` returns
    /// items ONLY -- 27 kinds, zero buildings -- so finding the tailoring bench
    /// and reading the generator frame both fell back to sweeping raw cells,
    /// which is exactly the tile-by-tile pattern the companion was built to end.
    ///
    /// Three failures from that same day are the reason for three of the fields:
    ///
    ///   * A **mini-turret with no power source** sat there with no alert.
    ///     Nothing in this stack could see it; M caught it off the screen.
    ///     Hence `power`, and hence an unpowered building never gets aggregated
    ///     away -- it is always its own row.
    ///   * A **finished bill** ("Do X times", 0 left) is drawn identically to a
    ///     live one, and a colony once ran out of food behind one. Hence
    ///     `bills[].finished`, computed rather than trusted to the label.
    ///   * A **worktable with an empty bill queue** is invisible in every
    ///     instrument we have. Hence every bill giver gets an individual row even
    ///     when `billCount` is 0 -- that is the case M's item 3 asks for
    ///     by name.
    ///
    /// ## Payload discipline
    ///
    /// A whole-map building list is mostly wall segments; a naive dump is
    /// hundreds of identical rows. So built structures are **aggregated by def**
    /// (count + sample positions, the shape `home/list_things` already uses)
    /// while anything a caller could act on is **promoted** to a full individual
    /// row: every blueprint, every frame, every bill giver, every bed, every
    /// turret, and anything unpowered, switched off, broken down or out of fuel.
    /// Each promoted row carries `reasons[]` saying why it escaped aggregation,
    /// and each aggregate row carries `promotedOut` saying how many of that def
    /// left it. Nothing is dropped in silence; a filter that hides things states
    /// what it hid, and an empty answer is never ambiguous between "nothing
    /// there" and "nothing survived the filter".
    ///
    /// ## The aggregate row's count, and the day it stopped being trusted
    ///
    /// BUGS.md, 2026-09-02: *"`home/list_buildings` reports `count: 11` for
    /// PowerConduit but returns 8 positions."* Eight is `maxPositionsPerDef`'s
    /// default. Nothing was miscounted — but `count` was incremented on every
    /// instance while `positions[]` was appended to only while it was under the
    /// cap, which is **two accumulators for one fact**, and the only trace of
    /// the cap was a `positionsNotListed` a caller had to think to read. A row
    /// that needs the reader to already suspect truncation is the same failure
    /// as a silently absent field.
    ///
    /// So the row now keeps every instance's anchor cell and reads BOTH numbers
    /// off that one list at emit time: `count` is its length, `positions[]` is
    /// its first `maxPositionsPerDef` entries, and `positionsListed`,
    /// `positionsNotListed`, `positionsTruncated` and `positionsCap` state the
    /// cap in the row it truncated. `distinctCells` separates the other reason a
    /// count can exceed its coordinates — several things of one def on one cell
    /// — from the cap, because raising the cap fixes one and not the other.
    /// `promotedOut` (promised by these remarks since the tool was written, and
    /// never actually emitted until that same day) closes the last gap: `count +
    /// promotedOut` is the def's whole surviving population.
    ///
    /// The other half of that BUGS.md line — *"Growing zone 1 lists 7 cells
    /// while 43 crops stand in it"* — is not answerable here in any mode. A
    /// plant is `ThingCategory.Plant` and this tool enumerates Building /
    /// Blueprint / BuildingFrame only, so its silence about crops is not
    /// evidence about crops. `notes.plantsAndZonesNotIncluded` says so in the
    /// payload and points at `home/list_zones`, which counts plants over the
    /// zone's own cell list AND over the cells the zone grid actually gives it.
    ///
    /// ## Three things reflection said that guessing would have got wrong
    ///
    /// (All checked against the installed Assembly-CSharp.dll, RimWorld
    /// 1.6.9676.17735.)
    ///
    ///   1. **`Bill_Production.ShouldDoNow()` MUTATES the bill.** Its IL writes
    ///      the `paused` field at three separate sites. It is the obvious way to
    ///      ask "is this bill live", and calling it from a read-only listing tool
    ///      would silently un-pause bills every time a Scout looked at the map.
    ///      This tool never calls it. `finished` and `active` are derived from
    ///      fields only.
    ///   2. **`Blueprint_Build.BuildDef` and `Frame.BuildDef` are casts** --
    ///      `(ThingDef)def.entityDefToBuild`. Floors are blueprints too and their
    ///      `entityDefToBuild` is a `TerrainDef`, so reading `BuildDef` throws
    ///      `InvalidCastException` on any flooring job. We read the
    ///      `BuildableDef` field directly and never cast.
    ///   3. **`Blueprint` and `Frame` live in `RimWorld`, not `Verse`** (the
    ///      cell payload's `isBlueprint` / `isFrame` naming suggests otherwise),
    ///      and they are NOT in `ThingRequestGroup.BuildingArtificial`: they have
    ///      their own groups, `Blueprint` and `BuildingFrame`. Asking only for
    ///      BuildingArtificial -- the obvious single query -- returns a list with
    ///      no blueprint and no frame in it, which is precisely the half of the
    ///      question M asked for. We query all three groups and de-
    ///      duplicate by reference, so a group-membership change upstream cannot
    ///      silently lose a status.
    ///
    /// ## The inspect pane, opt-in (`inspect: true`, WANTED 14)
    ///
    /// `Thing.GetInspectString()` is the text the inspect pane draws, and it has
    /// no relationship whatever to the selection -- which dissolves WANTED 14's
    /// stated problem ("without needing the selection to succeed on a multi-cell
    /// building") rather than working around it. It is OFF by default because a
    /// whole-map call is measured at 17.2 KB and the strings roughly double a
    /// worktable row; when it is off, no row carries the key at all.
    ///
    /// Three things about it that were checked in the decompile, not assumed:
    ///
    ///   1. **`ThingWithComps.GetInspectString()` concatenates every comp's
    ///      `CompInspectStringExtra()`**, which is where the power line lives
    ///      (`CompPowerTrader` -> "Power output: 100 W", and its base
    ///      `CompPower` -> **"Not connected to a power grid"**), the fuel line,
    ///      and "Broken down". That comp text is the whole point of the item,
    ///      so it is included by construction, not by a separate read.
    ///   2. **`GetInspectStringLowPriority()` is NOT called, deliberately.**
    ///      `Verse.Building` overrides it and its first act is
    ///      `DeconstructibleBy(Faction.OfPlayer)` -- the banned property, whose
    ///      body is `OfPlayerSilentFail` followed by `Log.Error`, and
    ///      `Log.Error`'s call path contains `TickManager.Pause()`. Reading a
    ///      building's low-priority line on a map with no player faction would
    ///      PAUSE the colony. The pane's deterioration / "attack to destroy"
    ///      text is the only thing lost, and neither is worth that.
    ///   3. **The strings carry Verse rich text** -- `Building_Door` colorizes
    ///      "must be enclosed by walls" red, `CompMannable` colorizes its
    ///      warning. `&lt;color=...&gt;`, `&lt;b&gt;`, `&lt;i&gt;`, `&lt;size=...&gt;`
    ///      and their closers are stripped; a `&lt;` that is not a tag is left alone.
    ///
    /// The pane's own newlines become `" | "` so a row stays one line: this is
    /// read by an agent that prints it, and an array of four one-clause strings
    /// costs about twice the JSON of the joined string for the same words.
    ///
    /// **Aggregated rows get nothing.** An aggregate row is a def, not a thing;
    /// there is no single inspect string for eleven conduits, and picking one
    /// instance's would be a sample presented as a fact. Pass `aggregate:false`
    /// to get an inspect string for every wall.
    ///
    /// Known hazard, not fixable from here: `InspectStringPartsFromComps` calls
    /// `Log.ErrorOnce` when `Prefs.DevMode` is on AND a comp's string ends in
    /// whitespace (a modded comp, in practice -- vanilla trims). That is the
    /// `Log.Error` -> `TickManager.Pause()` path again, once per offending comp
    /// type. `notes.inspectDevModeWarning` appears when dev mode is actually on.
    ///
    /// ## Which cell is `position`
    ///
    /// `Thing.Position` -- RimWorld's own anchor cell for the building, the same
    /// one `get_cells_info` and `click_cell` use, so a coordinate from here can
    /// be handed straight to any other bridge tool. For a multi-cell building
    /// that is NOT the min corner, so the full footprint is reported separately
    /// as `occupies` (min/max/width/height from `GenAdj.OccupiedRect`). `occupies`
    /// is omitted for 1x1 buildings; absence there means "one cell, at position".
    /// </summary>
    public sealed class HomeBuildingTools
    {
        private const int DefaultMaxPositions = 8;
        private const int DefaultMaxDetailed = 400;
        // Buildings named per FLAGGED power net. Enough to say which
        // batteries sit on a dead net without pasting a conduit run.
        private const int MaxPowerNetBuildings = 12;

        [Tool(
            "home/list_buildings",
            Title = "List every building, blueprint and frame on the map",
            Description =
                "Whole-map building census in one call, no clicking and no cell sweeps. Covers three statuses: built buildings, "
                + "blueprints, and frames under construction. Blueprints and frames carry the construction deficit (work left plus "
                + "per-resource have/need/stillNeeded); worktables carry their bill queue including whether each bill is finished; "
                + "anything with a power component reports whether it is actually powered. Mundane built structures are aggregated "
                + "by def to keep the payload small, and every row says what was collapsed.",
            ResultDescription =
                "success, counts, attention (the things worth acting on, counted), resourceDeficit (rolled up across all pending "
                + "construction), buildings[] (individually detailed rows) and aggregated[] (built structures grouped by def).")]
        [ToolResponse("buildings", "array", "One entry per building that was promoted to an individual row: every blueprint, frame, bill giver, bed, turret, and anything unpowered / switched off / broken down / out of fuel. Each carries thingId (the handle home/building_config and home/bills accept) and reasons[]. With billIngredients=true every bills[] row also carries ingredients[], canRunNow and blockedBy[].", Always = true)]
        [ToolResponse("aggregated", "array", "Built structures grouped by def: count (every instance), a SAMPLE of positions capped at maxPositionsPerDef with positionsListed / positionsNotListed / positionsTruncated / positionsCap saying so outright, distinctCells, promotedOut, and a damaged count. Empty when aggregate=false.", Always = true)]
        [ToolResponse("powerNets", "array", "One row per PowerNet on the map: transmitterCount, connectorCount, producerCount, consumerCount, batteryCount, playerBuildingCount, buildingCount, generationW and consumptionW (THIS TICK), generationCapacityW and consumptionCapacityW (what the defs could do), idleProducerCount, poweredConsumerCount, unpoweredConsumerCount, netW, storedWd, storedMaxWd, hasPowerSource, hasActivePowerSource, flags[] (noProducer, noConsumer, isolatedBattery, isolatedTransmitter) and, when the net is flagged or anything on it is dark or idle, buildings[] naming them (each with capacityW, poweredOn, switchedOn, brokenDown, hasFuel) plus buildingsNotListed. This is the read that can see an orphaned battery; the per-building power block structurally cannot.", Always = true)]
        [ToolResponse("powerSummary", "object", "netCount, flaggedNetCount, readable, error, a flags{} tally across every net, activeConditions[] (the map's game conditions by defName) and solarFlare. readable false with an error means the power-net manager could not be read at all, which is not the same as a clean grid.", Always = true)]
        [ToolResponse("resourceDeficit", "array", "Every resource still needed by any blueprint or frame, summed across the map, with how much of it exists.", Always = true)]
        [ToolResponse("attention", "object", "Counts of the actionable states: blueprints, frames, pending short of materials, unpowered, broken down, switched off, out of fuel, worktables with no bills, finished bills, suspended bills, damaged. Always present, zeros included. billsShortOfIngredients is added by billIngredients=true and only then.", Always = true)]
        [ToolResponse("inspectSkipped", "array", "Present only when inspect=true. One entry per DEF whose GetInspectString() threw: defName, count, error. An empty array with inspect=true means every row's string was read; the key's ABSENCE means inspect was off and nothing was attempted. filters.inspect says which.", Nullable = true)]
        [ToolResponse("unknownArguments", "array", "Every argument key the caller sent that this tool does not declare, sorted, case-sensitively. Empty array = every key was recognised. The host's own _rimBridgeTimeoutMs is never listed.", Always = true)]
        [ToolResponse("unknownArgumentsWarning", "string", "Present only when unknownArguments is non-empty, or when the caller's raw keys could not be read at all - in which case the empty unknownArguments means 'not known', not 'nothing unknown'.", Nullable = true)]
        public async Task<object> ListBuildings(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            [ToolParameter(Description = "Only rows whose defName, label, or the def they will become contains this text (case-insensitive).")] string match = null,
            [ToolParameter(Description = "Which statuses to return: 'all' (default), 'built', 'blueprint', 'frame', or 'pending' (blueprints and frames together).", DefaultValue = "all")] string status = "all",
            [ToolParameter(Description = "Which built things count as buildings: 'artificial' (default; RimWorld's own BuildingArtificial group, which excludes natural rock), 'all' (adds natural and resource rock) or 'rock' (ONLY natural rock, smoothed rock and mineable ore; requires playerOnly=false, because rock belongs to no faction).", DefaultValue = "artificial")] string category = "artificial",
            [ToolParameter(Description = "Only buildings belonging to the player faction.", DefaultValue = false)] bool playerOnly = false,
            [ToolParameter(Description = "Group mundane built structures by def instead of listing each one. Blueprints, frames, bill givers, beds, turrets and anything unpowered are always listed individually regardless.", DefaultValue = true)] bool aggregate = true,
            [ToolParameter(Description = "Promote a damaged building to its own row when its hit points are below this percentage of maximum. 0 = never promote on damage (aggregate rows still report a damaged count).", DefaultValue = 0)] int damagedBelowPct = 0,
            [ToolParameter(Description = "Centre cell x for a radius filter. Requires z and radius.", DefaultValue = -1)] int x = -1,
            [ToolParameter(Description = "Centre cell z for a radius filter. Requires x and radius.", DefaultValue = -1)] int z = -1,
            [ToolParameter(Description = "Only buildings within this many cells (Chebyshev) of x,z. 0 = whole map.", DefaultValue = 0)] int radius = 0,
            [ToolParameter(Description = "Maximum sample positions listed per aggregated def row.", DefaultValue = DefaultMaxPositions)] int maxPositionsPerDef = DefaultMaxPositions,
            [ToolParameter(Description = "Safety cap on individually detailed rows. Blueprints and frames are never truncated; the count of anything dropped is reported.", DefaultValue = DefaultMaxDetailed)] int maxDetailed = DefaultMaxDetailed,
            [ToolParameter(Description = "Add inspectString to every individually detailed row: the text the inspect pane would show, comp lines included (power output, 'Not connected to a power grid', fuel, broken down), rich-text tags stripped and the pane's lines joined with ' | '. Aggregated rows never get one. Off by default because it roughly doubles a worktable row.", DefaultValue = false)] bool inspect = false,
            [ToolParameter(Description = "Add the ingredient verdict to every bills[] row: ingredients[] (needed / available / shortfall per slot, plus what the bill's own filter excluded), canRunNow, and blockedBy[]. attention gains billsShortOfIngredients. This is the answer to 'the queue looks live and nothing is being cooked'. Off by default because it scans every haulable on the map; home/bills is the same verdict with the whole bill configuration, and is the tool for changing one.", DefaultValue = false)] bool billIngredients = false,
            [ToolParameter(Description = "Compatibility alias for playerOnly. Raw callers historically used byPlayerOnly; when supplied it controls the same colony-faction filter.")] bool? byPlayerOnly = null)
        {
            if (playerOnly && byPlayerOnly.HasValue && !byPlayerOnly.Value)
                return Failure("playerOnly=true and byPlayerOnly=false disagree; pass one colony-scope spelling.");
            var colonyOnly = byPlayerOnly ?? playerOnly;
            return BridgeCommon.WithUnknownArguments(
                await ListBuildingsCore(
                    ctx, cancellationToken, match, status, category, colonyOnly, aggregate,
                    damagedBelowPct, x, z, radius, maxPositionsPerDef, maxDetailed, inspect,
                    billIngredients).ConfigureAwait(false),
                ctx, typeof(HomeBuildingTools), "home/list_buildings");
        }

        private async Task<object> ListBuildingsCore(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            string match,
            string status,
            string category,
            bool playerOnly,
            bool aggregate,
            int damagedBelowPct,
            int x,
            int z,
            int radius,
            int maxPositionsPerDef,
            int maxDetailed,
            bool inspect,
            bool billIngredients)
        {
            if (ctx?.MainThread == null)
                return Failure("No RimBridge main-thread dispatcher is available for this invocation.");

            // Companion tools are dispatched with MarshalToMainThread = false
            // (AnnotatedExtensionCapabilityProvider.InvokeAsync), so every read of
            // listerThings / the bill stacks / the power net has to be hopped onto
            // RimWorld's main thread by hand. Removing this hop still compiles and
            // fails intermittently, which is the worst failure mode available.
            return await ctx.MainThread
                .InvokeAsync(() => Build(match, status, category, playerOnly, aggregate,
                                         damagedBelowPct, x, z, radius,
                                         maxPositionsPerDef, maxDetailed, inspect,
                                         billIngredients), cancellationToken)
                .ConfigureAwait(false);
        }

        private static object Build(string match, string status, string category, bool playerOnly,
                                    bool aggregate, int damagedBelowPct, int cx, int cz, int radius,
                                    int maxPositions, int maxDetailed, bool inspect,
                                    bool billIngredients)
        {
            if (!TryGetMap(out var map, out var mapError))
                return Failure(mapError);

            // One walk of the map's haulables, shared by every bill on every
            // bench. Built only when asked for: it is the whole cost of the
            // billIngredients option.
            var billItems = billIngredients ? BillCommon.MapItems.Build(map) : null;

            var wantStatus = (status ?? "all").Trim().ToLowerInvariant();
            if (wantStatus != "all" && wantStatus != "built" && wantStatus != "blueprint" &&
                wantStatus != "frame" && wantStatus != "pending")
                return Failure("status must be one of: all, built, blueprint, frame, pending. Got: " + status);

            var wantCategory = (category ?? "artificial").Trim().ToLowerInvariant();
            if (wantCategory != "artificial" && wantCategory != "all" && wantCategory != "rock")
                return Failure("category must be one of: artificial, all, rock. Got: " + category);
            var rockOnly = wantCategory == "rock";
            // Natural rock belongs to no faction, so the colony filter removes
            // every row of it. Saying so beats returning worktables.
            if (rockOnly && playerOnly)
                return Failure("category=rock with playerOnly=true returns nothing: natural rock, "
                               + "smoothed rock and ore belong to no faction. Pass playerOnly=false.");

            if (maxPositions < 0) maxPositions = 0;
            if (maxDetailed < 0) maxDetailed = 0;
            if (damagedBelowPct < 0) damagedBelowPct = 0;
            if (damagedBelowPct > 100) damagedBelowPct = 100;
            var useRadius = radius > 0 && cx >= 0 && cz >= 0;

            // Three separate lister groups, de-duplicated by reference. Blueprints
            // and frames are NOT in BuildingArtificial (see the class remarks), and
            // relying on one group would silently lose a whole status.
            List<Thing> source;
            try
            {
                var seen = new HashSet<Thing>();
                source = new List<Thing>();
                foreach (var group in new[] { ThingRequestGroup.Blueprint,
                                              ThingRequestGroup.BuildingFrame,
                                              ThingRequestGroup.BuildingArtificial,
                                              // Keep bill-giver discovery aligned with
                                              // BillsTool. Some modded benches and spots
                                              // are indexed here but not as artificial.
                                              ThingRequestGroup.PotentialBillGiver })
                {
                    foreach (var t in map.listerThings.ThingsInGroup(group))
                        if (t != null && !(t is Pawn) && !(t is Corpse) && seen.Add(t))
                            source.Add(t);
                }

                if (wantCategory == "all" || rockOnly)
                {
                    foreach (var t in map.listerThings.AllThings)
                        if (t != null && t.def != null &&
                            t.def.category == ThingCategory.Building && seen.Add(t))
                            source.Add(t);
                }
            }
            catch (Exception e)
            {
                return Failure("Could not read map.listerThings: " + e.Message);
            }

            var detailed = new List<object>();
            var rows = new Dictionary<string, AggRow>();
            // 2026-09-02: the class remarks and the `aggregated` ToolResponse
            // have promised `promotedOut` on every aggregate row since the tool
            // was written, and no code ever produced it -- so an aggregate row's
            // count could not be reconciled against the def's real population
            // without hand-counting buildings[]. Same family as the count/
            // positions defect: a payload asserting a number it does not emit.
            var promotedByDef = new Dictionary<string, int>(StringComparer.Ordinal);
            var deficit = new Dictionary<string, Deficit>();
            var att = new Attention { ReportBillIngredients = billIngredients };
            // One entry per DEF whose GetInspectString() threw, so a map full of
            // one broken modded def does not produce four hundred rows of the
            // same complaint. A null inspectString only ever means "listed here".
            var inspectFailures = new Dictionary<string, InspectFailure>(StringComparer.Ordinal);

            var scanned = 0;
            var skippedMatch = 0;
            var skippedStatus = 0;
            var skippedRadius = 0;
            var skippedFaction = 0;
            var skippedCategory = 0;
            var detailTruncated = 0;

            // Blueprints and frames first, so the maxDetailed cap -- if it ever
            // bites -- can only ever drop ordinary built structures. The pending
            // half of the answer is the half M asked for.
            foreach (var thing in source.OrderBy(t => IsPending(t) ? 0 : 1))
            {
                if (thing == null || thing.def == null)
                    continue;
                scanned++;

                if (rockOnly && !IsRockDef(thing.def))
                {
                    skippedCategory++;
                    continue;
                }

                var isBlueprint = thing is Blueprint;
                var isFrame = thing is Frame;
                var rowStatus = isBlueprint ? "blueprint" : isFrame ? "frame" : "built";

                if (wantStatus != "all" && wantStatus != rowStatus &&
                    !(wantStatus == "pending" && (isBlueprint || isFrame)))
                {
                    skippedStatus++;
                    continue;
                }

                if (playerOnly && !IsPlayerFaction(thing))
                {
                    skippedFaction++;
                    continue;
                }

                var pos = thing.Position;
                if (useRadius && Math.Max(Math.Abs(pos.x - cx), Math.Abs(pos.z - cz)) > radius)
                {
                    skippedRadius++;
                    continue;
                }

                var buildDef = SafeEntityToBuild(thing);
                if (!string.IsNullOrEmpty(match))
                {
                    var defName = thing.def.defName ?? string.Empty;
                    var label = SafeLabel(thing) ?? string.Empty;
                    var builtName = buildDef != null ? (buildDef.defName ?? string.Empty) : string.Empty;
                    var builtLabel = buildDef != null ? (buildDef.label ?? string.Empty) : string.Empty;
                    if (defName.IndexOf(match, StringComparison.OrdinalIgnoreCase) < 0 &&
                        label.IndexOf(match, StringComparison.OrdinalIgnoreCase) < 0 &&
                        builtName.IndexOf(match, StringComparison.OrdinalIgnoreCase) < 0 &&
                        builtLabel.IndexOf(match, StringComparison.OrdinalIgnoreCase) < 0)
                    {
                        skippedMatch++;
                        continue;
                    }
                }

                var reasons = PromotionReasons(thing, isBlueprint, isFrame, damagedBelowPct);
                var promote = !aggregate || reasons.Count > 0;

                if (promote && detailed.Count >= maxDetailed && !isBlueprint && !isFrame)
                {
                    detailTruncated++;
                    continue;
                }

                if (promote)
                {
                    detailed.Add(DetailRow(map, thing, rowStatus, isBlueprint, isFrame,
                                           buildDef, reasons, deficit, att,
                                           inspect, inspectFailures, billItems));
                    var promotedKey = thing.def.defName ?? "?";
                    int already;
                    promotedByDef.TryGetValue(promotedKey, out already);
                    promotedByDef[promotedKey] = already + 1;
                }
                else
                {
                    Aggregate(rows, thing, att);
                }
            }

            // 2026-09-02, BUGS.md: "reports count: 11 for PowerConduit but
            // returns 8 positions." Both numbers below are now DERIVED from the
            // rows instead of being accumulated beside them.
            //
            //   * The positions cap is applied HERE, at emit time, over the
            //     row's full cell list -- so `count` and `positions[]` come out
            //     of one list in one pass and cannot drift apart. Previously the
            //     row incremented `Count` on every instance and appended to
            //     `Positions` only while it was under the cap, which is two
            //     accumulators for one fact.
            //   * `aggregatedThings` was a third accumulator that merely
            //     happened to equal the sum of the row counts. A number that
            //     only happens to agree is a number that can stop agreeing.
            foreach (var pair in rows)
            {
                int promotedOut;
                promotedByDef.TryGetValue(pair.Key, out promotedOut);
                pair.Value.PromotedOut = promotedOut;
            }

            var aggregated = rows.Values
                .OrderByDescending(r => r.Count)
                .Select(r => r.ToPayload(maxPositions))
                .ToList();
            var aggregatedThings = rows.Values.Sum(r => r.Count);

            // Whole-map power topology. Emitted on EVERY call, not behind an
            // opt-in: `attention.notConnectedToPower = 0` was read as proof of a
            // healthy grid three turns running while an orphaned battery pocket
            // sat cut off, and a check that cannot see the failure it is used to
            // rule out is worse than no check.
            Dictionary<string, object> powerSummary;
            var powerNets = PowerNets(map, SafePlayerFaction(), MaxPowerNetBuildings, out powerSummary);

            var deficitRows = deficit.Values
                .OrderByDescending(d => d.StillNeeded)
                .Select(d => d.ToPayload(map))
                .ToList();

            var payload = new Dictionary<string, object>
            {
                { "success", true },
                { "tool", "home/list_buildings" },
                { "counts", new Dictionary<string, object>
                    {
                        { "scanned", scanned },
                        { "detailed", detailed.Count },
                        { "aggregatedRows", aggregated.Count },
                        { "aggregatedBuildings", aggregatedThings },
                        { "blueprints", att.Blueprints },
                        { "frames", att.Frames },
                        { "built", scanned - att.Blueprints - att.Frames }
                    } },
                // The actionable summary. Always present, zeros included: a caller
                // must be able to tell "nothing needs attention" from "the tool did
                // not look".
                { "attention", att.ToPayload() },
                { "powerNets", powerNets },
                { "powerSummary", powerSummary },
                { "resourceDeficit", deficitRows },
                // Every filter reports what it removed. An empty answer must never
                // be ambiguous between "nothing there" and "nothing survived".
                { "skipped", new Dictionary<string, object>
                    {
                        { "byMatch", skippedMatch },
                        { "byStatus", skippedStatus },
                        { "byRadius", skippedRadius },
                        { "byPlayerOnly", skippedFaction },
                        { "byCategory", skippedCategory },
                        { "byMaxDetailed", detailTruncated }
                    } },
                { "notes", new Dictionary<string, object>
                    {
                        // Not a filter over enumerated things -- a source choice --
                        // so it gets a stated flag rather than a fabricated count.
                        { "naturalRockExcluded", wantCategory == "artificial" },
                        { "rockCategory", "category=rock keeps ONLY def.building.isNaturalRock, def.building.isResourceRock and def.mineable things -- natural stone, smoothed rock and ore. It reads the whole ThingCategory.Building population like category=all and then drops everything else, counted as skipped.byCategory. It requires playerOnly=false: rock belongs to no faction." },
                        { "aggregatedRowsOmittedWhenEmpty", "A def whose every instance was promoted to buildings[] has no aggregated[] row; nothing is hidden, each one appears there in full." },
                        { "positionIs", "Thing.Position, RimWorld's anchor cell. For multi-cell buildings see occupies." },
                        // 2026-09-02: BUGS.md reported "count: 11 for PowerConduit
                        // but returns 8 positions" as a defect. It was the default
                        // cap doing exactly what it says, but nothing in the row
                        // said so in words, so the row now explains itself and this
                        // note explains the row.
                        { "countVsPositions", "On an aggregated[] row, count is every instance of that def; positions[] is a SAMPLE of at most maxPositionsPerDef of them (default " + DefaultMaxPositions + "). count == positionsListed + positionsNotListed by construction -- both are read off one list -- and positionsTruncated says outright when the cap bit. Raise maxPositionsPerDef, or pass aggregate:false, to get every coordinate. If distinctCells is below count, several instances share a cell and no cap setting will yield count distinct coordinates." },
                        // The growing-zone half of the same BUGS.md line ("Growing
                        // zone 1 lists 7 cells while 43 crops stand in it") is not
                        // answerable from here at all, so this payload says so
                        // rather than letting its silence read as "no crops".
                        { "plantsAndZonesNotIncluded", "Plants, crops and zones are NOT in this payload in any mode. A plant is ThingCategory.Plant and this tool enumerates Building / Blueprint / BuildingFrame only, so an empty answer here is never evidence about crops. Growing and stockpile zones, their cell counts and the plants standing in them are home/list_zones." },
                        { "billShouldDoNowNotCalled", "Bill_Production.ShouldDoNow() writes the bill's paused field, so this read-only tool never calls it; 'finished' and 'active' are derived from fields." },
                        { "powerNetsSeeWhatPerBuildingPowerCannot", "powerNets[] is the whole-map grid topology, one row per PowerNet, and it is the ONLY thing here that can see an orphaned battery: a battery is a CompPowerBattery, not a CompPowerTrader, so it has no powered flag to be false and attention.notConnectedToPower can read 0 while a whole pocket of the base is cut off. flags[] are noProducer, noConsumer, isolatedBattery and isolatedTransmitter; a net with no player-faction building on it is never flagged, and a flagged net names its buildings (capped at " + MaxPowerNetBuildings + ", buildingsNotListed says how many were cut)." },
                        { "billIngredientsAreOptIn", "A bills[] row carries ingredients[], canRunNow and blockedBy[] only under billIngredients:true; with it off, an active-looking queue is NOT evidence that a bill can run. home/bills is the same verdict plus the whole bill configuration, and is the tool for changing one." }
                    } },
                { "filters", new Dictionary<string, object>
                    {
                        { "match", match },
                        { "status", wantStatus },
                        { "category", wantCategory },
                        { "playerOnly", playerOnly },
                        { "aggregate", aggregate },
                        { "damagedBelowPct", damagedBelowPct },
                        { "x", cx }, { "z", cz }, { "radius", radius },
                        { "maxPositionsPerDef", maxPositions },
                        { "maxDetailed", maxDetailed },
                        // Present in BOTH modes on purpose. It is the only thing
                        // that separates "inspectSkipped is empty because every
                        // string read cleanly" from "the key is absent because
                        // nobody asked for inspect strings".
                        { "inspect", inspect },
                        // Present in BOTH modes, for the same reason inspect is:
                        // it separates "no bill row carries ingredients because
                        // every bill is fine" from "nobody asked".
                        { "billIngredients", billIngredients }
                    } },
                { "buildings", detailed },
                { "aggregated", aggregated }
            };

            if (inspect)
            {
                // These three notes describe the inspect option, so they ride
                // WITH it. On a default call the whole feature costs the payload
                // one boolean -- filters.inspect -- and not a page of prose.
                var notes = (Dictionary<string, object>)payload["notes"];
                notes["inspectOnDetailRowsOnly"] = "inspectString is added by inspect:true to buildings[] rows ONLY. An aggregated[] row is a def, not a thing, and there is no one inspect string for eleven conduits; pass aggregate:false to get one per building. With inspect:false no row carries the key at all -- read filters.inspect, not the absence.";
                notes["inspectIsNotTheWholePane"] = "Thing.GetInspectString() only. GetInspectStringLowPriority() is deliberately never called: Verse.Building overrides it and its first act is DeconstructibleBy(Faction.OfPlayer), whose body reaches Log.Error, whose call path contains TickManager.Pause(). The deterioration and 'attack to destroy' lines are the only text this loses.";
                notes["billIngredientShortfallIsNotInHere"] = "A worktable's inspect string does NOT contain its bills' ingredient shortfall: Building_WorkTable does not override GetInspectString and Bill has no inspect text at all, so a bench's string is quest lines plus comp lines (power, fuel, broken down). The 'Component: 0 / 3' text lives on Frame.GetInspectString() -- a CONSTRUCTION site's delivered resources -- and this tool already reports that structurally as resources[] have/need/stillNeeded on every blueprint and frame row.";
                payload["inspectSkipped"] = inspectFailures.Values
                    .OrderByDescending(f => f.Count)
                    .Select(f => f.ToPayload())
                    .ToList();
                payload["inspectSkippedThings"] = inspectFailures.Values.Sum(f => f.Count);
                if (SafeDevMode())
                {
                    notes["inspectDevModeWarning"] =
                        "Prefs.DevMode is ON. ThingWithComps.InspectStringPartsFromComps calls Log.ErrorOnce when a comp's inspect text ends in whitespace, and Log.Error's call path contains TickManager.Pause(). Vanilla comps trim; a modded one might not. Turn dev mode off before a long inspect:true run if the colony must not be paused.";
                }
            }

            return payload;
        }

        // ---------------------------------------------------------------- rows

        /// <summary>
        /// Why this building escaped aggregation. Empty list = nothing to act on,
        /// so it may be collapsed into a def row.
        /// </summary>
        private static List<object> PromotionReasons(Thing thing, bool isBlueprint, bool isFrame, int damagedBelowPct)
        {
            var reasons = new List<object>();
            if (isBlueprint) reasons.Add("blueprint");
            if (isFrame) reasons.Add("frame");
            if (isBlueprint || isFrame) return reasons;

            if (thing is IBillGiver) reasons.Add("billGiver");
            if (thing is Building_Bed) reasons.Add("bed");
            if (thing.def.building != null && thing.def.building.turretGunDef != null) reasons.Add("turret");

            var power = SafeComp<CompPowerTrader>(thing);
            if (power != null && !SafePowerOn(power)) reasons.Add("unpowered");

            var flick = SafeComp<CompFlickable>(thing);
            if (flick != null && !SafeSwitchOn(flick)) reasons.Add("switchedOff");

            var broken = SafeComp<CompBreakdownable>(thing);
            if (broken != null && SafeBrokenDown(broken)) reasons.Add("brokenDown");

            var fuel = SafeComp<CompRefuelable>(thing);
            if (fuel != null && !SafeHasFuel(fuel)) reasons.Add("outOfFuel");

            if (damagedBelowPct > 0 && thing.def.useHitPoints)
            {
                var max = thing.MaxHitPoints;
                if (max > 0 && thing.HitPoints * 100 < damagedBelowPct * max)
                    reasons.Add("damaged");
            }

            return reasons;
        }

        /// Natural stone, smoothed rock and ore -- what category=rock keeps.
        /// isNaturalRock covers stone and its smoothed walls, isResourceRock and
        /// mineable cover ore; a def is rock if any of the three says so.
        private static bool IsRockDef(ThingDef def)
        {
            try
            {
                if (def == null) return false;
                if (def.mineable) return true;
                var b = def.building;
                return b != null && (b.isNaturalRock || b.isResourceRock);
            }
            catch { return false; }
        }

        private static Dictionary<string, object> DetailRow(
            Map map, Thing thing, string status, bool isBlueprint, bool isFrame,
            BuildableDef buildDef, List<object> reasons,
            Dictionary<string, Deficit> deficit, Attention att,
            bool inspect, Dictionary<string, InspectFailure> inspectFailures,
            BillCommon.MapItems billItems)
        {
            var pos = thing.Position;
            var row = new Dictionary<string, object>
            {
                { "thingId", BridgeCommon.SafeString(() => thing.ThingID) },
                { "defName", thing.def.defName },
                { "label", SafeLabel(thing) },
                { "position", BridgeCommon.Pos(pos) },
                { "status", status },
                // M's item 3 named these two by hand; both are present on
                // every row, false included, so a caller never has to infer a
                // status from an absent key.
                { "isBlueprint", isBlueprint },
                { "isFrame", isFrame },
                // On EVERY detailed row, blueprints and frames included. A blueprint
                // used to come back with no rotation key at all, which a Python
                // caller reads as None -- indistinguishable from "faces north" and
                // from "this thing cannot be rotated". Both questions now have their
                // own answer on every row.
                { "rotation", SafeRotationHuman(thing) },
                { "rotatable", SafeRotatable(thing) },
                { "faction", SafeFactionName(thing) },
                { "stuff", SafeStuffName(thing, isBlueprint || isFrame) },
                { "reasons", reasons }
            };

            var rect = SafeRect(thing);
            if (rect != null && (rect.Value.Width > 1 || rect.Value.Height > 1))
            {
                row["occupies"] = new Dictionary<string, object>
                {
                    { "minX", rect.Value.minX }, { "minZ", rect.Value.minZ },
                    { "maxX", rect.Value.maxX }, { "maxZ", rect.Value.maxZ },
                    { "width", rect.Value.Width }, { "height", rect.Value.Height }
                };
            }

            // Hit points only where the def actually has them. An absent key means
            // "this thing has no HP concept" (blueprints), never "we did not look".
            if (thing.def.useHitPoints)
            {
                row["hitPoints"] = SafeHitPoints(thing);
                row["maxHitPoints"] = SafeMaxHitPoints(thing);
            }

            if (isBlueprint) att.Blueprints++;
            if (isFrame) att.Frames++;

            if (isBlueprint || isFrame)
            {
                row["buildDefName"] = buildDef != null ? buildDef.defName : null;
                row["buildLabel"] = buildDef != null ? buildDef.label : null;
                AddConstructionDeficit(row, thing, isFrame, buildDef, deficit, att);
            }
            else if (thing.def.useHitPoints)
            {
                var max = SafeMaxHitPoints(thing);
                if (max > 0 && SafeHitPoints(thing) < max)
                    att.Damaged++;
            }

            var power = SafeComp<CompPowerTrader>(thing);
            if (power != null)
                row["power"] = PowerBlock(power, thing, att);

            var fuel = SafeComp<CompRefuelable>(thing);
            if (fuel != null)
            {
                var hasFuel = SafeHasFuel(fuel);
                if (!hasFuel) att.OutOfFuel++;
                row["fuel"] = new Dictionary<string, object>
                {
                    { "hasFuel", hasFuel },
                    { "fuel", SafeFuel(fuel) },
                    { "targetFuelLevel", SafeTargetFuel(fuel) }
                };
            }

            var giver = thing as IBillGiver;
            if (giver != null)
                AddBills(row, thing, giver, att, billItems);

            var bed = thing as Building_Bed;
            if (bed != null)
            {
                // Cheap: OwnersForReading is a plain list read, no allocation of
                // note. Emitted as an array that is empty (never absent) when
                // nobody owns the bed.
                row["ownerNames"] = SafeBedOwners(bed);
                row["medical"] = SafeMedical(bed);
            }

            // Last, so it reads at the bottom of a row the way the pane draws at
            // the bottom of the screen. Only ever added when it was asked for:
            // with inspect:false the key does not exist, and filters.inspect is
            // what says which of the two silences this is.
            if (inspect)
                row["inspectString"] = InspectString(thing, inspectFailures);

            return row;
        }

        // -------------------------------------------------------- construction

        /// <summary>
        /// The construction deficit: what this blueprint or frame still needs, and
        /// how much work is left. Both Blueprint and Frame implement
        /// <c>IConstructible</c>, so one code path covers both -- and it also
        /// covers <c>Blueprint_Install</c> and <c>Blueprint_Storage</c>, which
        /// subclass them and would otherwise be a silently missing case.
        /// </summary>
        private static void AddConstructionDeficit(
            Dictionary<string, object> row, Thing thing, bool isFrame,
            BuildableDef buildDef, Dictionary<string, Deficit> deficit, Attention att)
        {
            var constructible = thing as IConstructible;
            var frame = thing as Frame;

            float? workLeft = null;
            float? workToBuild = null;
            float? percentComplete = null;

            if (frame != null)
            {
                workLeft = SafeFloat(() => frame.WorkLeft);
                workToBuild = SafeFloat(() => frame.WorkToBuild);
                percentComplete = SafeFloat(() => frame.PercentComplete);
            }
            else if (buildDef != null)
            {
                // A blueprint has no work fields of its own -- no work has been
                // done. Frame.WorkToBuild is itself this stat lookup, so this is
                // the same number the frame will report the moment it becomes one.
                var stuff = SafeStuffDef(thing, true);
                workToBuild = SafeFloat(() => buildDef.GetStatValueAbstract(StatDefOf.WorkToBuild, stuff));
                workLeft = workToBuild;
                percentComplete = 0f;
            }

            row["workLeft"] = workLeft;
            row["workToBuild"] = workToBuild;
            row["percentComplete"] = percentComplete;
            // The same number on the two scales it is read on. workLeft is raw
            // work ticks; the inspect pane draws GenText.ToStringWorkAmount of
            // it, which is that divided by 60 -- so "work left 800" and
            // "Work left: 13" are one job, and were printed as two.
            row["workLeftText"] = workLeft.HasValue
                ? BridgeCommon.SafeString(() => workLeft.Value.ToStringWorkAmount()) : null;
            row["workToBuildText"] = workToBuild.HasValue
                ? BridgeCommon.SafeString(() => workToBuild.Value.ToStringWorkAmount()) : null;

            var resources = new List<object>();
            var short_ = false;
            List<ThingDefCountClass> cost = null;
            // NOT on a Blueprint_Install. Its TotalMaterialCost() body is, in
            // full, Log.Error("Called MaterialsNeededTotal on a
            // Blueprint_Install.") + an empty list -- and Verse.Log.Error calls
            // TickManager.Pause(). So asking a reinstall blueprint what it needs
            // PAUSES THE COLONY, which the harness reads as a person pressing
            // space; a single minified bed waiting to be reinstalled would have
            // paused the game on every call of this tool. Vanilla guards the same
            // way (GenConstruct.CanGetResources_NewTemp opens with
            // `if (thing is Blueprint_Install) return true;`). A reinstall costs
            // nothing -- it moves a thing that already exists -- so the empty
            // resources[] below is the true answer, not a degraded one.
            var isInstall = BridgeCommon.IsInstallBlueprint(constructible);
            try { cost = constructible != null && !isInstall ? constructible.TotalMaterialCost() : null; }
            catch { cost = null; }

            if (cost != null)
            {
                foreach (var item in cost)
                {
                    if (item == null || item.thingDef == null)
                        continue;
                    var need = item.count;
                    // Shared with home/place_building's materials[] block, which
                    // sums this same call map-wide as reservedByOtherBlueprints.
                    // One definition so the two tools cannot disagree about what
                    // a site is still owed.
                    var stillNeeded = BridgeCommon.ConstructibleStillNeeded(constructible, item.thingDef, need);

                    int have;
                    if (frame != null && frame.resourceContainer != null)
                    {
                        // Authoritative for a frame: what is physically inside it.
                        try { have = frame.resourceContainer.TotalStackCountOfDef(item.thingDef); }
                        catch { have = Math.Max(0, need - stillNeeded); }
                    }
                    else
                    {
                        // A blueprint holds nothing -- delivering the first resource
                        // is what turns it into a frame -- so this is 0, derived
                        // rather than asserted.
                        have = Math.Max(0, need - stillNeeded);
                    }

                    if (stillNeeded > 0)
                    {
                        short_ = true;
                        var key = item.thingDef.defName ?? "?";
                        if (!deficit.TryGetValue(key, out var d))
                        {
                            d = new Deficit { DefName = key, Label = item.thingDef.label, Def = item.thingDef };
                            deficit[key] = d;
                        }
                        d.StillNeeded += stillNeeded;
                        d.Sites++;
                    }

                    resources.Add(new Dictionary<string, object>
                    {
                        { "defName", item.thingDef.defName },
                        { "label", item.thingDef.label },
                        { "have", have },
                        { "need", need },
                        { "stillNeeded", stillNeeded }
                    });
                }
            }

            row["resources"] = resources;
            // Present-when-false. "This thing has everything it needs and is just
            // waiting for a builder" and "this thing is starved of steel" are the
            // two answers item 2 exists to keep apart.
            row["resourcesComplete"] = !short_;
            // A reinstall's empty resources[] is the ANSWER (it needs nothing),
            // not a failed read, so it must not be flagged unreadable -- the two
            // mean opposite things to a caller deciding whether to haul.
            row["materialCostUnreadable"] = cost == null && !isInstall;
            row["isInstallBlueprint"] = isInstall;
            if (isInstall)
            {
                // ThingDef.entityDefToBuild is null on an install blueprint, so
                // buildDefName/buildLabel above are null and the row has no name
                // for what it is moving. The instance knows.
                var moving = InstallTarget(thing);
                row["installOfDefName"] = moving != null && moving.def != null
                    ? moving.def.defName : null;
                row["installOfLabel"] = moving != null
                    ? BridgeCommon.SafeString(() => moving.LabelCap) : null;
            }
            if (short_) att.PendingMissingResources++;
        }

        // --------------------------------------------------------------- bills

        private static void AddBills(Dictionary<string, object> row, Thing bench,
                                     IBillGiver giver, Attention att,
                                     BillCommon.MapItems billItems)
        {
            var bills = new List<object>();
            BillStack stack = null;
            try { stack = giver.BillStack; }
            catch { stack = null; }

            var billStackUnreadable = stack == null;

            if (stack != null)
            {
                List<Bill> list = null;
                try { list = stack.Bills; }
                catch { list = null; }

                if (list == null)
                    billStackUnreadable = true;

                if (list != null)
                {
                    for (var i = 0; i < list.Count; i++)
                    {
                        var bill = list[i];
                        if (bill == null)
                            continue;

                        var prod = bill as Bill_Production;
                        var repeatMode = prod != null && prod.repeatMode != null ? prod.repeatMode.defName : null;
                        var repeatCount = prod != null ? (int?)prod.repeatCount : null;
                        var targetCount = prod != null ? (int?)prod.targetCount : null;
                        var paused = prod != null && prod.paused;

                        // THE BUG THIS FIELD EXISTS FOR. A "Do X times" bill whose
                        // remaining count has reached zero is drawn exactly like a
                        // live one, and a colony once sat in front of a stove with
                        // a finished bill and no food. Derived from fields, never
                        // from ShouldDoNow() -- that call writes `paused`.
                        var finished = prod != null &&
                                       prod.repeatMode == BillRepeatModeDefOf.RepeatCount &&
                                       prod.repeatCount <= 0;

                        var suspended = bill.suspended;
                        var active = !suspended && !paused && !finished;

                        if (finished) att.FinishedBills++;
                        if (suspended) att.SuspendedBills++;

                        var billRow = new Dictionary<string, object>
                        {
                            { "index", i },
                            { "label", SafeBillLabel(bill) },
                            { "recipe", bill.recipe != null ? bill.recipe.defName : null },
                            { "repeatMode", repeatMode },
                            { "repeatCount", repeatCount },
                            { "targetCount", targetCount },
                            { "repeatInfo", SafeRepeatInfo(prod) },
                            // All four present on every bill, false included.
                            { "suspended", suspended },
                            { "paused", paused },
                            { "finished", finished },
                            { "active", active },
                            { "completableEver", SafeCompletableEver(bill) }
                        };

                        // The opt-in half. Off, no row carries the keys at all
                        // and filters.billIngredients is what says which silence
                        // that is; on, every row carries all three.
                        if (billItems != null)
                        {
                            var verdict = BillCommon.Judge(bench, giver, bill, billItems);
                            billRow["ingredients"] = verdict.Ingredients;
                            billRow["canRunNow"] = verdict.CanRunNow;
                            billRow["blockedBy"] = verdict.BlockedBy;
                            if (!verdict.IngredientsSatisfied)
                                att.BillsShortOfIngredients++;
                        }

                        bills.Add(billRow);
                    }
                }
            }

            row["bills"] = bills;
            row["billCount"] = bills.Count;
            row["billStackUnreadable"] = billStackUnreadable;
            row["activeBillCount"] = bills.Count(b => Equals(((Dictionary<string, object>)b)["active"], true));
            // M's item 3 by name: "the butcher spot has no queued tasks".
            // An empty queue is a finding, not an absence.
            row["billStackEmpty"] = billStackUnreadable ? null : (object)(bills.Count == 0);
            if (!billStackUnreadable)
            {
                if (bills.Count == 0) att.BillGiversWithNoBills++;
                else if (!bills.Any(b => Equals(((Dictionary<string, object>)b)["active"], true)))
                    att.BillGiversWithNoActiveBill++;
            }
        }

        // ---------------------------------------------------------------- power

        private static Dictionary<string, object> PowerBlock(CompPowerTrader power, Thing thing, Attention att)
        {
            var powered = SafePowerOn(power);
            var connected = SafeConnected(power);
            var flick = SafeComp<CompFlickable>(thing);
            var broken = SafeComp<CompBreakdownable>(thing);
            var switchedOn = flick == null ? true : SafeSwitchOn(flick);
            var brokenDown = broken != null && SafeBrokenDown(broken);

            if (!powered) att.Unpowered++;
            if (!connected) att.NotConnectedToPower++;
            if (!switchedOn) att.SwitchedOff++;
            if (brokenDown) att.BrokenDown++;

            return new Dictionary<string, object>
            {
                // The Aug 31 mini-turret: powered false, connected false, switch on,
                // not broken -- "there is no power source", which is exactly what
                // the game said once a human looked at it and nothing here could.
                { "powered", powered },
                { "connected", connected },
                { "switchedOn", switchedOn },
                { "brokenDown", brokenDown },
                // Negative = drawing power, positive = producing it.
                { "powerOutput", SafeFloat(() => power.PowerOutput) }
            };
        }


        // ------------------------------------------------------ power networks

        /// <summary>
        /// One row per PowerNet on the map. 2026-09-07: three turns of clean
        /// `notConnectedToPower=0` checks while the whole hill pocket was cut
        /// off. A battery is a `CompPowerBattery`, not a `CompPowerTrader`, so
        /// it has no `PowerOn` that could be false and an ISOLATED battery can
        /// never read as unpowered -- the per-building view is structurally
        /// blind to it. This is the read that is not.
        ///
        /// Every call is a pure read, checked against 1.6:
        /// `PowerNetManager.AllNetsListForReading` returns the backing field;
        /// `PowerNet.transmitters/connectors/powerComps/batteryComps` are public
        /// fields; `CurrentStoredEnergy()` sums `StoredEnergy` and writes
        /// nothing; `CompProperties_Power.PowerConsumption` reads research state
        /// only; `CompPowerTrader.PowerOutput` and `CompPowerBattery.StoredEnergy`
        /// are plain getters.
        /// </summary>
        private static List<object> PowerNets(Map map, Faction player, int maxNamed,
                                              out Dictionary<string, object> summary)
        {
            var nets = new List<object>();
            var flagged = 0;
            var flagCounts = new Dictionary<string, object>(StringComparer.Ordinal) {
                { "noProducer", 0 }, { "noConsumer", 0 },
                { "isolatedBattery", 0 }, { "isolatedTransmitter", 0 } };
            List<PowerNet> all = null;
            string readError = null;
            try { all = map.powerNetManager == null ? null : map.powerNetManager.AllNetsListForReading; }
            catch (Exception e) { readError = e.GetType().Name + ": " + e.Message; }
            if (all == null)
            {
                summary = new Dictionary<string, object> {
                    { "netCount", 0 }, { "flaggedNetCount", 0 }, { "readable", false },
                    { "error", readError ?? "map.powerNetManager returned no net list" },
                    { "flags", flagCounts } };
                return nets;
            }
            for (var i = 0; i < all.Count; i++)
            {
                var net = all[i];
                if (net == null) continue;
                var traders = net.powerComps ?? new List<CompPowerTrader>();
                var batteries = net.batteryComps ?? new List<CompPowerBattery>();
                var transmitters = net.transmitters ?? new List<CompPower>();
                var connectors = net.connectors ?? new List<CompPower>();
                var producerCount = 0;
                var consumerCount = 0;
                var generationW = 0f;
                var consumptionW = 0f;
                // Capacity is what the DEFS could do; generationW/consumptionW is
                // what they are doing this tick. An unfuelled generator counts in
                // the first and contributes nothing to the second, and printing
                // only the second read as "5400W of power exists" while eleven
                // consumers sat dark (2026-09-07).
                var generationCapacityW = 0f;
                var consumptionCapacityW = 0f;
                var idleProducerCount = 0;
                var poweredConsumerCount = 0;
                var unpoweredConsumerCount = 0;
                for (var j = 0; j < traders.Count; j++)
                {
                    var t = traders[j];
                    if (t == null) continue;
                    var output = SafeFloat(() => t.PowerOutput) ?? 0f;
                    var basis = BaseWatts(t);
                    // The DEF decides which side a thing is on. PowerOutput is 0
                    // on an idle or unpowered generator and would misfile it as
                    // a consumer, which is the very reading being fixed here.
                    if (IsProducerDef(t))
                    {
                        producerCount++;
                        generationCapacityW += -basis;
                        if (output > 0f) generationW += output; else idleProducerCount++;
                    }
                    else
                    {
                        consumerCount++;
                        consumptionCapacityW += basis;
                        if (output < 0f) consumptionW += -output;
                        if (SafePowerOn(t)) poweredConsumerCount++; else unpoweredConsumerCount++;
                    }
                }
                var storedMax = 0f;
                for (var j = 0; j < batteries.Count; j++)
                {
                    var b = batteries[j];
                    if (b == null) continue;
                    storedMax += SafeFloat(() => b.Props == null ? 0f : b.Props.storedEnergyMax) ?? 0f;
                }

                var flags = new List<object>();
                if (producerCount == 0 && (batteries.Count > 0 || consumerCount > 0))
                    flags.Add("noProducer");
                if (consumerCount == 0 && (producerCount > 0 || batteries.Count > 0))
                    flags.Add("noConsumer");
                if (batteries.Count > 0 && producerCount == 0)
                    flags.Add("isolatedBattery");
                if (transmitters.Count > 0 && traders.Count == 0 && batteries.Count == 0)
                    flags.Add("isolatedTransmitter");

                // Only a net holding something of OURS is flagged: an ancient
                // ruin's dead conduit run is not a colony problem, and flagging
                // it would bury the one that is.
                var members = NetMembers(net, player);
                var playerCount = 0;
                for (var j = 0; j < members.Count; j++) if (members[j].Player) playerCount++;
                if (playerCount == 0) flags.Clear();
                if (flags.Count > 0)
                {
                    flagged++;
                    for (var j = 0; j < flags.Count; j++)
                    {
                        var key = Convert.ToString(flags[j]);
                        object had;
                        flagCounts[key] = (flagCounts.TryGetValue(key, out had)
                            ? Convert.ToInt32(had) : 0) + 1;
                    }
                }

                // Named on a flagged net, and on any net where something is dark
                // or idle: "which lamp is out" is the question a power table is
                // read to answer, and a flag alone cannot answer it.
                var nameThem = flags.Count > 0 || unpoweredConsumerCount > 0 || idleProducerCount > 0;
                var named = new List<object>();
                if (nameThem)
                    for (var j = 0; j < members.Count && j < maxNamed; j++) named.Add(members[j].Row);

                nets.Add(new Dictionary<string, object>
                {
                    { "index", i },
                    { "transmitterCount", transmitters.Count },
                    { "connectorCount", connectors.Count },
                    { "producerCount", producerCount },
                    { "consumerCount", consumerCount },
                    { "batteryCount", batteries.Count },
                    { "playerBuildingCount", playerCount },
                    { "buildingCount", members.Count },
                    { "generationW", generationW },
                    { "consumptionW", consumptionW },
                    { "generationCapacityW", generationCapacityW },
                    { "consumptionCapacityW", consumptionCapacityW },
                    { "idleProducerCount", idleProducerCount },
                    { "poweredConsumerCount", poweredConsumerCount },
                    { "unpoweredConsumerCount", unpoweredConsumerCount },
                    { "netW", generationW - consumptionW },
                    { "storedWd", SafeFloat(net.CurrentStoredEnergy) },
                    { "storedMaxWd", storedMax },
                    { "hasPowerSource", SafeBool(() => net.hasPowerSource) },
                    { "hasActivePowerSource", SafeBool(() => net.HasActivePowerSource) },
                    { "flags", flags },
                    // Named only on a flagged net, and capped: the point is to be
                    // able to say WHICH batteries sit on a dead net. A healthy
                    // net's membership is already answerable from buildings[].
                    { "buildings", named },
                    { "buildingsNotListed", nameThem ? Math.Max(0, members.Count - named.Count) : 0 }
                });
            }
            var conditions = ActiveConditionNames(map);
            summary = new Dictionary<string, object> {
                { "netCount", nets.Count }, { "flaggedNetCount", flagged },
                { "readable", true }, { "error", null }, { "flags", flagCounts },
                // A solar flare switches every powered building off while the
                // generators still read their full capacity. Without this the
                // table says "gen 5400W" beside eleven dark consumers and gives
                // the reader no way to tell why.
                { "solarFlare", conditions.Contains("SolarFlare") },
                { "activeConditions", conditions.Cast<object>().ToList() } };
            return nets;
        }

        /// The map's active game conditions by defName. [] when unreadable --
        /// which is why solarFlare is derived from this list and not guessed.
        private static List<string> ActiveConditionNames(Map map)
        {
            var names = new List<string>();
            try
            {
                var mgr = map == null ? null : map.gameConditionManager;
                var active = mgr == null ? null : mgr.ActiveConditions;
                if (active == null) return names;
                for (var i = 0; i < active.Count; i++)
                {
                    var c = active[i];
                    if (c != null && c.def != null && c.def.defName != null)
                        names.Add(c.def.defName);
                }
            }
            catch { }
            return names;
        }

        /// The def's own wattage: negative for a generator, positive for a draw.
        /// This is capacity, not this tick's output.
        private static float BaseWatts(CompPowerTrader trader)
        {
            try { return trader.Props == null ? 0f : trader.Props.PowerConsumption; }
            catch { return 0f; }
        }

        /// A generator: its def's base draw is negative. PowerNet.IsPowerSource
        /// uses exactly this test.
        private static bool IsProducerDef(CompPowerTrader trader)
        {
            try { return trader.Props != null && trader.Props.PowerConsumption < 0f; }
            catch { return false; }
        }

        private sealed class NetMember
        {
            public bool Player;
            public Dictionary<string, object> Row;
        }

        /// Every distinct building on one net, with the role it plays there.
        private static List<NetMember> NetMembers(PowerNet net, Faction player)
        {
            var seen = new HashSet<Thing>();
            var rows = new List<NetMember>();
            AddNetMembers(rows, seen, player, net.batteryComps, "battery");
            AddNetMembers(rows, seen, player, net.powerComps, null);
            AddNetMembers(rows, seen, player, net.transmitters, "transmitter");
            AddNetMembers(rows, seen, player, net.connectors, "connector");
            return rows;
        }

        private static void AddNetMembers<T>(List<NetMember> rows, HashSet<Thing> seen,
                                             Faction player, List<T> comps, string role)
            where T : CompPower
        {
            if (comps == null) return;
            for (var i = 0; i < comps.Count; i++)
            {
                var comp = comps[i];
                if (comp == null) continue;
                Thing parent;
                try { parent = comp.parent; } catch { continue; }
                if (parent == null || parent.def == null || !seen.Add(parent)) continue;
                var trader = comp as CompPowerTrader;
                var battery = comp as CompPowerBattery;
                var thisRole = role ?? (trader != null && IsProducerDef(trader) ? "producer" : "consumer");
                var isPlayer = false;
                try { isPlayer = parent.Faction == player; } catch { }
                // Enough per building to say WHY it is dark without a second
                // call: a lamp that is off because the net is browning out and
                // one that is off because somebody flicked it read identically
                // from powerOutputW alone.
                var flick = SafeComp<CompFlickable>(parent);
                var broken = SafeComp<CompBreakdownable>(parent);
                var refuel = SafeComp<CompRefuelable>(parent);
                rows.Add(new NetMember {
                    Player = isPlayer,
                    Row = new Dictionary<string, object> {
                        { "thingId", BridgeCommon.SafeString(() => parent.ThingID) },
                        { "defName", parent.def.defName },
                        { "label", SafeLabel(parent) },
                        { "position", BridgeCommon.Pos(parent.Position) },
                        { "role", thisRole },
                        { "faction", SafeFactionName(parent) },
                        { "powerOutputW", trader == null ? (object)null : SafeFloat(() => trader.PowerOutput) },
                        { "capacityW", trader == null ? (object)null : (object)Math.Abs(BaseWatts(trader)) },
                        { "poweredOn", trader == null ? (object)null : (object)SafePowerOn(trader) },
                        { "switchedOn", flick == null ? (object)null : (object)SafeSwitchOn(flick) },
                        { "brokenDown", broken == null ? (object)null : (object)SafeBrokenDown(broken) },
                        { "hasFuel", refuel == null ? (object)null : (object)SafeHasFuel(refuel) },
                        { "storedWd", battery == null ? (object)null : SafeFloat(() => battery.StoredEnergy) }
                    } });
            }
        }

        private static bool SafeBool(Func<bool> read)
        {
            try { return read(); }
            catch { return false; }
        }

        // ---------------------------------------------------------- aggregation

        /// <summary>
        /// Fold one built structure into its def's row. Records the anchor cell
        /// of EVERY instance; the row's count and its emitted positions are both
        /// read back off that one list at payload time (see ToPayload), so the
        /// pair cannot disagree the way `count: 11` / 8 positions did.
        /// </summary>
        private static void Aggregate(Dictionary<string, AggRow> rows, Thing thing, Attention att)
        {
            var defName = thing.def.defName ?? "?";
            if (!rows.TryGetValue(defName, out var row))
            {
                row = new AggRow { DefName = defName, Label = SafeLabel(thing) };
                rows[defName] = row;
            }
            // One append per instance, cap or no cap -- and this IS the count:
            // AggRow.Count is Cells.Count. The cap is a rendering decision and
            // is applied in ToPayload; applying it here is what let the count
            // and the positions list be computed from two different sets.
            row.Cells.Add(SafePositionCell(thing));

            var stuff = SafeStuffName(thing, false);
            if (row.Count == 1) row.Stuff = stuff;
            else if (row.Stuff != stuff) { row.Stuff = null; row.StuffVaried = true; }

            if (thing.def.useHitPoints)
            {
                var max = SafeMaxHitPoints(thing);
                var hp = SafeHitPoints(thing);
                if (max > 0)
                {
                    var pct = 100f * hp / max;
                    if (row.WorstHitPointsPct == null || pct < row.WorstHitPointsPct.Value)
                        row.WorstHitPointsPct = pct;
                    if (hp < max) { row.Damaged++; att.Damaged++; }
                }
            }
        }

        private sealed class AggRow
        {
            public string DefName;
            public string Label;
            public string Stuff;
            public bool StuffVaried;
            public int Damaged;
            public float? WorstHitPointsPct;
            /// <summary>
            /// How many instances of this def left the aggregate for a row of
            /// their own. count + promotedOut is every instance of the def that
            /// survived the filters, so an unpowered conduit cannot make the
            /// collapsed row look like the whole population.
            /// </summary>
            public int PromotedOut;

            /// <summary>
            /// The anchor cell of every instance folded into this row, capped by
            /// nothing. `count` is this list's Count and `positions[]` is its
            /// first N entries, so the two are the same fact rendered twice
            /// rather than two facts that have to be kept in step.
            /// </summary>
            public readonly List<IntVec3> Cells = new List<IntVec3>();

            public int Count { get { return Cells.Count; } }

            public Dictionary<string, object> ToPayload(int maxPositions)
            {
                var positions = new List<object>();
                var distinct = new HashSet<IntVec3>();
                var unreadable = 0;
                foreach (var c in Cells)
                {
                    // Position threw for this one (SafePositionCell's sentinel).
                    // It still counts -- it exists -- but emitting (-1000,-1000)
                    // as a coordinate a caller could hand to click_cell would be
                    // worse than saying nothing, so it is named instead.
                    if (c == IntVec3.Invalid) { unreadable++; continue; }
                    distinct.Add(c);
                    if (positions.Count < maxPositions)
                        positions.Add(BridgeCommon.Pos(c));
                }

                return new Dictionary<string, object>
                {
                    { "defName", DefName },
                    { "label", Label },
                    { "status", "built" },
                    // count == positionsListed + positionsNotListed, always,
                    // because all three are read off Cells in this one method.
                    { "count", Cells.Count },
                    { "stuff", Stuff },
                    { "stuffVaried", StuffVaried },
                    // Never omitted: this is the only thing standing between a
                    // collapsed row and a silently ignored damaged wall.
                    { "damaged", Damaged },
                    { "worstHitPointsPct", WorstHitPointsPct },
                    // Promised by this tool's docs since it was written, emitted
                    // for the first time 2026-09-02. count + promotedOut is the
                    // def's whole surviving population; the promoted ones are in
                    // buildings[] in full.
                    { "promotedOut", PromotedOut },
                    { "positions", positions },
                    // 2026-09-02: the four fields the old payload was missing.
                    // `positions` has always been a SAMPLE, and the only hint of
                    // that was a positionsNotListed a caller had to think to
                    // read. A cap now states itself in the row it truncated:
                    // positionsTruncated is the flag, positionsCap is the number
                    // that did it, and count is the real total.
                    { "positionsListed", positions.Count },
                    { "positionsNotListed", Cells.Count - positions.Count },
                    { "positionsTruncated", Cells.Count > positions.Count },
                    { "positionsCap", maxPositions },
                    // The other way count and positions can differ, and the one
                    // that is a real finding rather than a rendering cap: two
                    // things of the same def standing on ONE cell (a conduit
                    // under a wall-mounted thing, a re-placed floor). When
                    // distinctCells < count, raising maxPositionsPerDef will
                    // NOT produce `count` distinct coordinates, and that is the
                    // answer, not a bug in the caller.
                    { "distinctCells", distinct.Count },
                    { "positionsUnreadable", unreadable }
                };
            }
        }

        private sealed class Deficit
        {
            public string DefName;
            public string Label;
            public ThingDef Def;
            public int StillNeeded;
            public int Sites;

            public Dictionary<string, object> ToPayload(Map map)
            {
                return new Dictionary<string, object>
                {
                    { "defName", DefName },
                    { "label", Label },
                    { "stillNeeded", StillNeeded },
                    { "sites", Sites },
                    // Two different numbers on purpose, both named for what they
                    // are. onMapTotal sums every spawned stack of this def, so it
                    // includes forbidden and unstored ones. countedAsResource is
                    // RimWorld's own resource readout, the number in the top-left
                    // of the screen. When they disagree, the difference is the
                    // steel nobody is allowed to touch -- the exact shape of the
                    // misread that started this whole instrument set.
                    { "onMapTotal", OnMapTotal(map) },
                    // What a construction hauler could actually fetch. Neither
                    // number above answers that: onMapTotal counts forbidden
                    // stacks, and countedAsResource is ResourceCounter, which
                    // only sums storage -- so 359 loose steel reads as 0 and a
                    // site that is merely undelivered reads as starved.
                    { "unforbiddenOnMap", UnforbiddenOnMap(map) },
                    { "countedAsResource", CountedAsResource(map) }
                };
            }

            private int? OnMapTotal(Map map)
            {
                try
                {
                    if (Def == null) return null;
                    var total = 0;
                    foreach (var t in map.listerThings.ThingsOfDef(Def))
                        total += Math.Max(1, t.stackCount);
                    return total;
                }
                catch { return null; }
            }

            /// <summary>Spawned, unforbidden stacks of this def. CompForbiddable
            /// directly: ForbidUtility.IsForbidden(Thing, Faction) calls
            /// Faction.OfPlayer.</summary>
            private int? UnforbiddenOnMap(Map map)
            {
                try
                {
                    if (Def == null) return null;
                    var total = 0;
                    foreach (var t in map.listerThings.ThingsOfDef(Def))
                    {
                        if (t == null || !t.Spawned)
                            continue;
                        var twc = t as ThingWithComps;
                        var comp = twc != null ? twc.GetComp<CompForbiddable>() : null;
                        if (comp != null && comp.Forbidden)
                            continue;
                        total += Math.Max(1, t.stackCount);
                    }
                    return total;
                }
                catch { return null; }
            }

            private int? CountedAsResource(Map map)
            {
                try
                {
                    if (Def == null || map.resourceCounter == null) return null;
                    return map.resourceCounter.GetCount(Def);
                }
                catch { return null; }
            }
        }

        /// <summary>
        /// The actionable summary. Every field is emitted even at zero -- the
        /// difference between "nothing needs attention" and "the tool did not
        /// look" is the whole point of this companion.
        /// </summary>
        private sealed class Attention
        {
            public int Blueprints;
            public int Frames;
            public int PendingMissingResources;
            public int Unpowered;
            public int NotConnectedToPower;
            public int SwitchedOff;
            public int BrokenDown;
            public int OutOfFuel;
            public int BillGiversWithNoBills;
            public int BillGiversWithNoActiveBill;
            public int FinishedBills;
            public int SuspendedBills;
            public int Damaged;
            public int BillsShortOfIngredients;
            /// <summary>billsShortOfIngredients is emitted ONLY under
            /// billIngredients=true. A zero without the scan would read as
            /// "every bill can run", which is the opposite of what it means.</summary>
            public bool ReportBillIngredients;

            public Dictionary<string, object> ToPayload()
            {
                var payload = new Dictionary<string, object>
                {
                    { "blueprints", Blueprints },
                    { "frames", Frames },
                    { "pendingMissingResources", PendingMissingResources },
                    { "unpowered", Unpowered },
                    { "notConnectedToPower", NotConnectedToPower },
                    { "switchedOff", SwitchedOff },
                    { "brokenDown", BrokenDown },
                    { "outOfFuel", OutOfFuel },
                    { "billGiversWithNoBills", BillGiversWithNoBills },
                    { "billGiversWithNoActiveBill", BillGiversWithNoActiveBill },
                    { "finishedBills", FinishedBills },
                    { "suspendedBills", SuspendedBills },
                    { "damagedBuildings", Damaged }
                };
                if (ReportBillIngredients)
                    payload["billsShortOfIngredients"] = BillsShortOfIngredients;
                return payload;
            }
        }

        // ------------------------------------------------------- inspect text

        /// <summary>
        /// One def's worth of GetInspectString() failures. Deduplicated by def:
        /// four hundred identical complaints is not four hundred findings.
        /// </summary>
        private sealed class InspectFailure
        {
            public string DefName;
            public string Error;
            public int Count;

            public Dictionary<string, object> ToPayload()
            {
                return new Dictionary<string, object>
                {
                    { "defName", DefName },
                    { "count", Count },
                    { "error", Error }
                };
            }
        }

        /// <summary>
        /// The inspect pane's text for one thing, or null if reading it threw --
        /// in which case the def is named in <c>inspectSkipped[]</c>. An empty
        /// string is a real answer ("this wall has nothing to say"); null is
        /// always a failure, and always listed.
        ///
        /// Only <c>Thing.GetInspectString()</c>. NOT GetInspectStringLowPriority:
        /// Verse.Building overrides it and calls DeconstructibleBy(Faction.OfPlayer),
        /// which reaches Log.Error, whose call path pauses the game.
        /// </summary>
        private static string InspectString(Thing thing, Dictionary<string, InspectFailure> failures)
        {
            string raw;
            try
            {
                raw = thing.GetInspectString();
            }
            catch (Exception e)
            {
                var key = SafeDefName(thing);
                InspectFailure f;
                if (!failures.TryGetValue(key, out f))
                {
                    f = new InspectFailure { DefName = key, Error = e.GetType().Name + ": " + e.Message };
                    failures[key] = f;
                }
                f.Count++;
                return null;
            }

            if (string.IsNullOrEmpty(raw))
                return string.Empty;

            var flat = StripRichText(raw);
            var kept = new List<string>();
            foreach (var line in flat.Split('\n'))
            {
                var t = line.Trim();
                if (t.Length > 0)
                    kept.Add(t);
            }
            // " | " and not a newline: a row is printed on one line by every
            // consumer we have, and an array of four one-clause strings costs
            // about twice the JSON of the joined string for the same words.
            return string.Join(" | ", kept.ToArray());
        }

        /// <summary>
        /// Strip Verse/Unity rich text -- colour, bold, italic and size tags and
        /// their closers. Building_Door colorizes "must be enclosed by walls"
        /// red and CompMannable colorizes its warning, so these really do turn
        /// up. A '&lt;' that does not open a well-formed tag is left where it is.
        /// </summary>
        private static string StripRichText(string s)
        {
            if (string.IsNullOrEmpty(s) || s.IndexOf('<') < 0)
                return s;
            var sb = new StringBuilder(s.Length);
            var i = 0;
            while (i < s.Length)
            {
                if (s[i] != '<')
                {
                    sb.Append(s[i]);
                    i++;
                    continue;
                }
                var close = s.IndexOf('>', i + 1);
                if (close < 0 || close - i > 64 || !IsRichTextTag(s.Substring(i + 1, close - i - 1)))
                {
                    sb.Append(s[i]);
                    i++;
                    continue;
                }
                i = close + 1;
            }
            return sb.ToString();
        }

        private static bool IsRichTextTag(string inner)
        {
            if (inner.Length == 0)
                return false;
            var k = inner[0] == '/' ? 1 : 0;
            if (k >= inner.Length || !char.IsLetter(inner[k]))
                return false;
            for (; k < inner.Length; k++)
            {
                if (inner[k] == '=')
                    return true;            // colour and size tags: the rest is free
                if (!char.IsLetterOrDigit(inner[k]))
                    return false;
            }
            return true;
        }

        private static string SafeDefName(Thing thing)
        {
            try { return (thing.def != null ? thing.def.defName : null) ?? "?"; }
            catch { return "?"; }
        }

        /// <summary>Dev mode changes what GetInspectString can do (it enables a
        /// Log.ErrorOnce path that reaches TickManager.Pause), so the payload
        /// says when it is on rather than leaving a caller to wonder.</summary>
        private static bool SafeDevMode()
        {
            try { return Prefs.DevMode; }
            catch { return false; }
        }

        // ------------------------------------------------------------- helpers

        private static bool IsPending(Thing thing)
        {
            return thing is Blueprint || thing is Frame;
        }

        /// <summary>
        /// The def this thing will become. Read off the plain
        /// <c>ThingDef.entityDefToBuild</c> field rather than
        /// <c>Blueprint_Build.BuildDef</c> / <c>Frame.BuildDef</c>, both of which
        /// cast to ThingDef and throw on a floor (a TerrainDef). Null for an
        /// already-built building.
        /// </summary>
        /// <summary>
        /// The thing a <c>Blueprint_Install</c> is moving, unwrapped from its
        /// <c>MinifiedThing</c> crate when it is in one. Null for anything else.
        /// Never touches TotalMaterialCost, which Log.Errors on an install
        /// blueprint and so pauses the game.
        /// </summary>
        private static Thing InstallTarget(Thing thing)
        {
            try
            {
                var install = thing as Blueprint_Install;
                if (install == null)
                    return null;
                var held = install.MiniToInstallOrBuildingToReinstall;
                var mini = held as MinifiedThing;
                return mini != null && mini.InnerThing != null ? mini.InnerThing : held;
            }
            catch { return null; }
        }

        private static BuildableDef SafeEntityToBuild(Thing thing)
        {
            try { return thing.def != null ? thing.def.entityDefToBuild : null; }
            catch { return null; }
        }

        private static ThingDef SafeStuffDef(Thing thing, bool pending)
        {
            try
            {
                if (pending)
                {
                    var c = thing as IConstructible;
                    if (c != null)
                    {
                        var s = c.EntityToBuildStuff();
                        if (s != null) return s;
                    }
                }
                return thing.Stuff;
            }
            catch { return null; }
        }

        private static string SafeStuffName(Thing thing, bool pending)
        {
            var s = SafeStuffDef(thing, pending);
            return s != null ? s.defName : null;
        }

        private static T SafeComp<T>(Thing thing) where T : ThingComp
        {
            try
            {
                var twc = thing as ThingWithComps;
                return twc != null ? twc.GetComp<T>() : null;
            }
            catch { return null; }
        }

        private static bool SafePowerOn(CompPowerTrader power)
        {
            try { return power.PowerOn; }
            catch { return false; }
        }

        private static bool SafeConnected(CompPowerTrader power)
        {
            try { return power.PowerNet != null; }
            catch { return false; }
        }

        private static bool SafeSwitchOn(CompFlickable flick)
        {
            try { return flick.SwitchIsOn; }
            catch { return true; }
        }

        private static bool SafeBrokenDown(CompBreakdownable broken)
        {
            try { return broken.BrokenDown; }
            catch { return false; }
        }

        private static bool SafeHasFuel(CompRefuelable fuel)
        {
            try { return fuel.HasFuel; }
            catch { return false; }
        }

        private static float? SafeFuel(CompRefuelable fuel)
        {
            return SafeFloat(() => fuel.Fuel);
        }

        private static float? SafeTargetFuel(CompRefuelable fuel)
        {
            return SafeFloat(() => fuel.TargetFuelLevel);
        }

        private static float? SafeFloat(Func<float> read)
        {
            try
            {
                var v = read();
                if (float.IsNaN(v) || float.IsInfinity(v))
                    return null;
                return (float)Math.Round(v, 2);
            }
            catch { return null; }
        }

        /// <summary>
        /// The anchor cell, or <c>IntVec3.Invalid</c> if reading it threw.
        /// Added 2026-09-02 with the count/positions fix: an aggregate row's
        /// count is now the length of its cell list, so a cell that cannot be
        /// read must still occupy a slot in that list or the count would quietly
        /// go down by one. The sentinel is filtered out of `positions[]` and
        /// counted in `positionsUnreadable`.
        /// </summary>
        private static IntVec3 SafePositionCell(Thing thing)
        {
            try { return thing.Position; }
            catch { return IntVec3.Invalid; }
        }

        private static CellRect? SafeRect(Thing thing)
        {
            try { return GenAdj.OccupiedRect(thing); }
            catch { return null; }
        }

        private static int SafeHitPoints(Thing thing)
        {
            try { return thing.HitPoints; }
            catch { return -1; }
        }

        private static int SafeMaxHitPoints(Thing thing)
        {
            try { return thing.MaxHitPoints; }
            catch { return -1; }
        }

        private static string SafeLabel(Thing thing)
        {
            try { return thing.LabelCapNoCount.ToString(); }
            catch
            {
                try { return thing.def != null ? thing.def.label : null; }
                catch { return null; }
            }
        }

        private static string SafeFactionName(Thing thing)
        {
            try { return thing.Faction != null ? thing.Faction.Name : null; }
            catch { return null; }
        }

        /// <summary>
        /// RimWorld's own word for the facing -- North / East / South / West,
        /// translated. A blueprint has a real Rotation like anything else; it was
        /// simply never emitted, so a caller reading a blueprint's facing got None
        /// and could not tell that from a thing with no facing at all.
        /// </summary>
        private static string SafeRotationHuman(Thing thing)
        {
            try { return thing.Rotation.ToStringHuman(); }
            catch { return null; }
        }

        private static bool SafeRotatable(Thing thing)
        {
            try { return thing.def != null && thing.def.rotatable; }
            catch { return false; }
        }

        // NOT Faction.OfPlayer: its whole body is get_OfPlayerSilentFail
        // followed by Verse.Log.Error, and Log.Error's call path contains
        // TickManager.Pause() -- so on a map with no player faction, asking who
        // the player is would PAUSE the colony, which the harness reads as a
        // person pressing space.
        /// The player faction, or null. Faction.OfPlayer is banned everywhere in
        /// this DLL (its failure path reaches Log.Error, which pauses the game).
        private static Faction SafePlayerFaction()
        {
            try { return Faction.OfPlayerSilentFail; }
            catch { return null; }
        }

        private static bool IsPlayerFaction(Thing thing)
        {
            try
            {
                var player = Faction.OfPlayerSilentFail;
                return player != null && thing.Faction != null && thing.Faction == player;
            }
            catch { return false; }
        }

        private static List<object> SafeBedOwners(Building_Bed bed)
        {
            var names = new List<object>();
            try
            {
                var owners = bed.OwnersForReading;
                if (owners != null)
                    foreach (var p in owners)
                        if (p != null)
                            names.Add(SafePawnName(p));
            }
            catch
            {
                // An unreadable owner list is reported as empty plus the key being
                // present; callers wanting certainty have get_cells_plus.
            }
            return names;
        }

        private static string SafePawnName(Pawn pawn)
        {
            try { return pawn.LabelShortCap.ToString(); }
            catch { return null; }
        }

        private static bool SafeMedical(Building_Bed bed)
        {
            try { return bed.Medical; }
            catch { return false; }
        }

        private static string SafeBillLabel(Bill bill)
        {
            try { return bill.LabelCap; }
            catch
            {
                try { return bill.recipe != null ? bill.recipe.defName : null; }
                catch { return null; }
            }
        }

        private static string SafeRepeatInfo(Bill_Production prod)
        {
            if (prod == null)
                return null;
            // Verified side-effect free: the getter's IL contains no field store.
            try { return prod.RepeatInfoText; }
            catch { return null; }
        }

        private static bool SafeCompletableEver(Bill bill)
        {
            try { return bill.CompletableEver; }
            catch { return true; }
        }

        /// <summary>The shared map gate; see BridgeCommon.TryGetMap. The error
        /// text names this tool.</summary>
        private static bool TryGetMap(out Map map, out string error)
        {
            return BridgeCommon.TryGetMap("home/list_buildings", out map, out error);
        }

        /// <summary>The shared refusal shape; see BridgeCommon.Failure.</summary>
        private static object Failure(string error)
        {
            return BridgeCommon.Failure("home/list_buildings", error);
        }
    }
}
