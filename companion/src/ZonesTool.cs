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
    /// home/list_zones — every zone on the map, read-only, with the one thing no
    /// other instrument reports: whether the zone's own cell LIST and the map's
    /// zone GRID still agree about which cells it owns.
    ///
    /// ## Why the list/grid split is the whole point of this tool
    ///
    /// A zone is stored twice. `Zone.cells` is a plain `List&lt;IntVec3&gt;` on the
    /// zone; `ZoneManager.zoneGrid` is one `Zone` pointer per map cell. Every
    /// normal path writes both. `Zone.AddCell(c)` appends to its own list and then
    /// calls `zoneManager.AddZoneGridCell(this, c)` which OVERWRITES the grid
    /// pointer — it does NOT remove the cell from whatever zone had it before.
    /// The game's own `Designator_ZoneAdd.DesignateMultiCell` never hits that case
    /// because it drops every cell where `ZoneAt(c) != null` before adding. A
    /// caller that adds a cell directly does not, and the result is a zone whose
    /// list claims cells the grid gives to somebody else.
    ///
    /// That is not cosmetic. `SlotGroup.CellsList` is `parent.AllSlotCellsList()`,
    /// which for a stockpile is the zone's **list** — so hauling follows the list
    /// while rendering and `ZoneAt` follow the grid, and the save file logs
    /// `... overwriting slot group square (x, 0, z) of ...` on every load. So both
    /// counts are reported separately, every disagreement is named in `anomalies`
    /// as an English sentence, and `home/zone_cells` op=repair is the fix.
    ///
    /// ## Three reads that a plausible implementation would get wrong
    ///
    /// (Checked against the installed Assembly-CSharp.dll, RimWorld 1.6.)
    ///
    ///   1. **`Zone.Cells` (capital C) SHUFFLES the list in place.** Its getter
    ///      calls `cells.Shuffle()` the first time it is touched. Reading it from
    ///      a listing tool would reorder the colony's stockpile cells as a side
    ///      effect of looking at them. Everything here reads the `cells` FIELD.
    ///      Same trap in `Zone_Growing`'s `IPlantToGrowSettable.Cells`.
    ///   2. **`Zone_Growing.PlantDefToGrow` WRITES `plantDefToGrow`** when it is
    ///      null: the getter lazily assigns Potato (or Toxipotato on polluted
    ///      ground) and stores it. So this tool reads the private field by
    ///      reflection instead and reports `plantDef: null` with
    ///      `plantDefExplicitlySet: false`, which is the honest answer — the zone
    ///      has not been told what to grow yet.
    ///   3. **`Zone.CheckContiguous()` DELETES CELLS.** It flood-fills from
    ///      `cells[0]` and calls `RemoveCell` on everything it did not reach. It
    ///      is the obvious way to answer "is this zone contiguous" and it is a
    ///      destructive write. `contiguous` here is computed by a local 4-connected
    ///      flood fill over a copy of the list; nothing is touched.
    ///
    /// ## Why a growing zone counts its plants twice (2026-09-02)
    ///
    /// BUGS.md filed *"Growing zone 1 lists 7 cells while 43 crops stand in
    /// it"* against `home/list_buildings`. It is not a `list_buildings` defect —
    /// a plant is `ThingCategory.Plant` and that tool enumerates buildings,
    /// blueprints and frames, so it can never see a crop. It is the list/grid
    /// split above, seen from the crop side: seven listed cells cannot hold 43
    /// plants, and a grid that gives the zone 43 cells can. This tool was
    /// already reporting the divergence correctly in `listedCellCount` /
    /// `gridCellCount` — but it carried **no plant count at all**, so the 43 had
    /// to be counted somewhere else and there was nothing here to reconcile it
    /// against.
    ///
    /// A growing zone now reports `plantsInListedCells` and `plantsInGridCells`
    /// side by side, plus `cropPlantsIn*` for the def it was actually told to
    /// grow and a per-def `plants[]`. They are never merged: one plant count for
    /// a zone whose two cell sets disagree is a number that is quietly the
    /// answer to a different question. `plantCountsDisagree` says so in one
    /// field, and `home/zone_cells op=repair` is what makes the two agree.
    ///
    /// Read-only in the strict sense: no field on any game object is written, and
    /// no path that reaches `Verse.Log.Error` (which calls `TickManager.Pause()`)
    /// is entered. The plant sweep reads `map.thingGrid.ThingsListAtFast` and
    /// `ThingDef` fields only — the same reads the stockpile block has been
    /// making since this tool was written — and never touches `Plant` state or
    /// `Zone_Growing.PlantDefToGrow`, whose getter writes.
    /// </summary>
    public sealed class HomeZoneTools
    {
        private const string ToolName = "home/list_zones";
        private const int DefaultMaxContents = 30;
        private const int DefaultMaxCells = 400;
        private const int FilterLabelSample = 12;

        [Tool(
            ToolName,
            Title = "List every zone on the map, with list/grid integrity",
            Description =
                "Read-only census of every zone (stockpiles, growing zones, dumping stockpiles and anything else registered with the "
                + "map's ZoneManager). Reports the zone's own cell count AND the number of map cells the zone grid actually assigns to "
                + "it, names every cell where the two disagree, and computes contiguity without calling the game's destructive "
                + "CheckContiguous. filter:true gives every stockpile the full storage-filter block home/zone_cells op=filter reports. "
                + "Stockpiles additionally report priority, filter summary, how many of their cells are blocked by a "
                + "building or already hold an item, what is stored in them, and which buildings overlap them. Growing zones report the "
                + "crop they are set to and how many plants actually stand in them, counted separately over the zone's own cell list and "
                + "over the cells the grid gives it, because those two answers can be wildly different numbers.",
            ResultDescription =
                "success, tool, mapName, zoneCount, totals (listed vs grid cells across all zones), zones[] (one row per zone), "
                + "anomalies[] (one English sentence per phantom or orphan cell found), filters, notes.")]
        [ToolResponse("zones", "array", "One row per zone: label, id, type, hidden, listedCellCount, gridCellCount, phantomCells[], orphanGridCells[], contiguous, bounds, plus stockpile/growing blocks. A growing zone's block carries plantsInListedCells and plantsInGridCells side by side — never one merged plant count — plus plants[] per def.", Always = true)]
        [ToolResponse("anomalies", "array", "One human sentence per list/grid disagreement found anywhere on the map. Empty array = the zone data is internally consistent.", Always = true)]
        [ToolResponse("filter", "object", "Opt-in (filter: true), stockpile rows only: the same storage-filter block home/zone_cells op=filter reports as its before and after - how many of the storable defs are allowed, the priority, which top-level categories are fully or partly allowed, whether anything rottable is allowed, and a ten-label sample. Absent without filter: true, and never on a zone that is not a stockpile.", Nullable = true)]
        [ToolResponse("totals", "object", "listedCells and gridCells summed over every zone, plus phantomCells and orphanGridCells. When listedCells != gridCells something is wrong.", Always = true)]
        [ToolResponse("unknownArguments", "array", "Every argument key the caller sent that this tool does not declare, sorted, case-sensitively. Empty array = every key was recognised. The host's own _rimBridgeTimeoutMs is never listed.", Always = true)]
        [ToolResponse("unknownArgumentsWarning", "string", "Present only when unknownArguments is non-empty, or when the caller's raw keys could not be read at all - in which case the empty unknownArguments means 'not known', not 'nothing unknown'.", Nullable = true)]
        public async Task<object> ListZones(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            [ToolParameter(Description = "Only zones whose label contains this text (case-insensitive). Null or empty = every zone.")] string match = null,
            [ToolParameter(Description = "Centre cell x for a radius filter. Requires z and radius.", DefaultValue = -1)] int x = -1,
            [ToolParameter(Description = "Centre cell z for a radius filter. Requires x and radius.", DefaultValue = -1)] int z = -1,
            [ToolParameter(Description = "Keep a zone when ANY of its cells is within this many cells (Chebyshev) of x,z. 0 = whole map.", DefaultValue = 0)] int radius = 0,
            [ToolParameter(Description = "List every cell of every zone under 'cells' (the zone's own list) and 'gridCells' (what the zone grid assigns it). Off by default because a large stockpile is a long list.", DefaultValue = false)] bool includeCells = false,
            [ToolParameter(Description = "For stockpiles, report occupancy (blocked / occupied / free cells), stored contents and overlapping buildings. For growing zones, count the plants standing in the zone, over both cell sets. When false the counts are emitted as null with plantScanRan false, never as zero.", DefaultValue = true)] bool includeContents = true,
            [ToolParameter(Description = "Maximum stored-item rows per stockpile, and plant-def rows per growing zone.", DefaultValue = DefaultMaxContents)] int maxContentRows = DefaultMaxContents,
            [ToolParameter(Description = "Maximum cells listed per zone when includeCells is true. The count is always exact; only the listing is capped.", DefaultValue = DefaultMaxCells)] int maxCellsPerZone = DefaultMaxCells,
            [ToolParameter(Description = "Give every stockpile row a filter block: allowedDefCount out of storableDefCount, priority, which top-level categories are fully or partly allowed, whether anything rottable is allowed, and a sample. The same block home/zone_cells op=filter reports.", DefaultValue = false)] bool filter = false)
        {
            return BridgeCommon.WithUnknownArguments(
                await ListZonesCore(
                    ctx, cancellationToken, match, x, z, radius, includeCells, includeContents,
                    maxContentRows, maxCellsPerZone, filter).ConfigureAwait(false),
                ctx, typeof(HomeZoneTools), ToolName);
        }

        private async Task<object> ListZonesCore(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            string match,
            int x,
            int z,
            int radius,
            bool includeCells,
            bool includeContents,
            int maxContentRows,
            int maxCellsPerZone,
            bool filter)
        {
            if (ctx?.MainThread == null)
                return Failure("No RimBridge main-thread dispatcher is available for this invocation.");

            // Companion tools are dispatched with MarshalToMainThread = false
            // (AnnotatedExtensionCapabilityProvider.InvokeAsync), so the zone
            // manager, the zone grid, the thing grid and the haul destination
            // manager all have to be read on RimWorld's own thread. ALL of it goes
            // inside ONE hop: a whole-map grid sweep smeared across several ticks
            // would produce exactly the kind of list/grid disagreement this tool
            // exists to detect, and inventing one would be worse than useless.
            return await ctx.MainThread
                .InvokeAsync(() => Build(match, x, z, radius, includeCells, includeContents,
                                         maxContentRows, maxCellsPerZone, filter), cancellationToken)
                .ConfigureAwait(false);
        }

        private static object Build(string match, int cx, int cz, int radius, bool includeCells,
                                    bool includeContents, int maxContentRows, int maxCellsPerZone,
                                    bool includeFilter)
        {
            if (!TryGetMap(out var map, out var mapError))
                return Failure(mapError);

            if (maxContentRows < 0) maxContentRows = 0;
            if (maxCellsPerZone < 0) maxCellsPerZone = 0;
            var useRadius = radius > 0 && cx >= 0 && cz >= 0;

            ZoneManager zoneManager;
            List<Zone> allZones;
            try
            {
                zoneManager = map.zoneManager;
                allZones = zoneManager == null ? null : zoneManager.AllZones;
            }
            catch (Exception e)
            {
                return Failure("Could not read map.zoneManager: " + e.Message);
            }

            if (zoneManager == null || allZones == null)
                return Failure("This map has no zone manager.");

            // ONE sweep of the whole zone grid, not one per zone. Builds the
            // authoritative "which cells does the grid think belong to whom" map
            // that every phantom / orphan answer below is measured against.
            var gridCells = new Dictionary<Zone, List<IntVec3>>();
            var gridOwnerOf = new Dictionary<IntVec3, Zone>();
            var gridSweepFailed = false;
            try
            {
                foreach (var c in map.AllCells)
                {
                    var owner = zoneManager.ZoneAt(c);
                    if (owner == null)
                        continue;
                    gridOwnerOf[c] = owner;
                    if (!gridCells.TryGetValue(owner, out var list))
                    {
                        list = new List<IntVec3>();
                        gridCells[owner] = list;
                    }
                    list.Add(c);
                }
            }
            catch
            {
                gridSweepFailed = true;
            }

            var anomalies = new List<object>();
            var rows = new List<object>();
            var skippedMatch = 0;
            var skippedRadius = 0;
            long totalListed = 0;
            long totalGrid = 0;
            var totalPhantom = 0;
            var totalOrphan = 0;

            foreach (var zone in allZones)
            {
                if (zone == null)
                    continue;

                // The `cells` FIELD, never the `Cells` property: the property
                // shuffles the list in place the first time it is read.
                var listed = SafeCells(zone);
                List<IntVec3> grid;
                if (!gridCells.TryGetValue(zone, out grid))
                    grid = new List<IntVec3>();

                // Whole-map totals are computed before the filters, so a filtered
                // call still tells the truth about the map's overall integrity.
                totalListed += listed.Count;
                totalGrid += grid.Count;

                var listedSet = new HashSet<IntVec3>(listed);
                var phantoms = new List<object>();
                var phantomCount = 0;
                foreach (var c in listed)
                {
                    Zone owner;
                    gridOwnerOf.TryGetValue(c, out owner);
                    if (owner == zone)
                        continue;
                    phantomCount++;
                    phantoms.Add(new Dictionary<string, object>
                    {
                        { "x", c.x }, { "z", c.z },
                        { "gridOwner", owner == null ? null : SafeLabel(owner) },
                        { "gridOwnerId", owner == null ? (int?)null : SafeId(owner) }
                    });
                    anomalies.Add(SafeLabel(zone) + " lists (" + c.x + "," + c.z + ") but the grid "
                                  + (owner == null ? "assigns it to no zone at all." : "gives it to " + SafeLabel(owner) + "."));
                }

                var orphans = new List<object>();
                foreach (var c in grid)
                {
                    if (listedSet.Contains(c))
                        continue;
                    orphans.Add(BridgeCommon.Pos(c));
                    anomalies.Add("The grid gives (" + c.x + "," + c.z + ") to " + SafeLabel(zone)
                                  + " but that zone's own cell list does not contain it.");
                }

                totalPhantom += phantomCount;
                totalOrphan += orphans.Count;

                if (!string.IsNullOrEmpty(match))
                {
                    var label = SafeLabel(zone) ?? string.Empty;
                    if (label.IndexOf(match, StringComparison.OrdinalIgnoreCase) < 0)
                    {
                        skippedMatch++;
                        continue;
                    }
                }

                if (useRadius && !listed.Any(c => Math.Max(Math.Abs(c.x - cx), Math.Abs(c.z - cz)) <= radius))
                {
                    skippedRadius++;
                    continue;
                }

                var row = new Dictionary<string, object>
                {
                    { "label", SafeLabel(zone) },
                    { "id", SafeId(zone) },
                    { "type", SafeTypeName(zone) },
                    { "hidden", SafeHidden(zone) },
                    { "listedCellCount", listed.Count },
                    { "gridCellCount", grid.Count },
                    // Present even when empty, and that is the point: an empty
                    // array is the tool saying "checked, consistent", not "not
                    // looked at".
                    { "phantomCells", phantoms },
                    { "phantomCellCount", phantomCount },
                    { "orphanGridCells", orphans },
                    { "orphanGridCellCount", orphans.Count },
                    { "consistent", phantomCount == 0 && orphans.Count == 0 },
                    { "contiguous", IsContiguous(listed) },
                    { "bounds", Bounds(listed) }
                };

                if (includeCells)
                {
                    var cellRows = new List<object>();
                    for (var i = 0; i < listed.Count && i < maxCellsPerZone; i++)
                        cellRows.Add(BridgeCommon.Pos(listed[i]));
                    row["cells"] = cellRows;
                    row["cellsNotListed"] = Math.Max(0, listed.Count - cellRows.Count);

                    // 2026-09-02: `cells` was the zone's own list only, so a
                    // caller asking "which cells IS this zone" got the short
                    // half of a disagreement the same payload was reporting two
                    // fields higher up (listedCellCount vs gridCellCount). Both
                    // sets are listed now, under names that say which is which.
                    var gridRows = new List<object>();
                    for (var i = 0; i < grid.Count && i < maxCellsPerZone; i++)
                        gridRows.Add(BridgeCommon.Pos(grid[i]));
                    row["gridCells"] = gridRows;
                    row["gridCellsNotListed"] = Math.Max(0, grid.Count - gridRows.Count);
                }

                var stockpile = zone as Zone_Stockpile;
                if (stockpile != null)
                    AddStockpileBlock(row, map, stockpile, listed, includeContents, maxContentRows, includeFilter);

                var growing = zone as Zone_Growing;
                if (growing != null)
                    AddGrowingBlock(row, map, growing, listed, grid, includeContents, maxContentRows);

                rows.Add(row);
            }

            return new Dictionary<string, object>
            {
                { "success", true },
                { "tool", ToolName },
                { "mapName", SafeMapName(map) },
                { "zoneCount", rows.Count },
                { "zoneCountOnMap", allZones.Count },
                { "totals", new Dictionary<string, object>
                    {
                        // Summed over EVERY zone on the map, filters or no filters.
                        // listedCells != gridCells is the headline symptom.
                        { "listedCells", totalListed },
                        { "gridCells", totalGrid },
                        { "phantomCells", totalPhantom },
                        { "orphanGridCells", totalOrphan },
                        { "consistent", totalPhantom == 0 && totalOrphan == 0 },
                        { "gridSweepFailed", gridSweepFailed }
                    } },
                { "anomalies", anomalies },
                { "skipped", new Dictionary<string, object>
                    {
                        { "byMatch", skippedMatch },
                        { "byRadius", skippedRadius }
                    } },
                { "filters", new Dictionary<string, object>
                    {
                        { "match", match },
                        { "x", cx }, { "z", cz }, { "radius", radius },
                        { "includeCells", includeCells },
                        { "includeContents", includeContents },
                        { "maxContentRows", maxContentRows },
                        { "maxCellsPerZone", maxCellsPerZone },
                        { "filter", includeFilter }
                    } },
                { "zones", rows },
                { "notes", Notes() }
            };
        }

        // ------------------------------------------------------------ stockpile

        private static void AddStockpileBlock(Dictionary<string, object> row, Map map,
                                              Zone_Stockpile stockpile, List<IntVec3> listed,
                                              bool includeContents, int maxContentRows, bool includeFilter)
        {
            row["priority"] = SafePriority(stockpile);
            row["filterSummary"] = FilterSummary(stockpile);
            if (includeFilter)
            {
                // The same block home/zone_cells op=filter reports as before/after,
                // built by the same code, so the two tools cannot describe one
                // filter differently.
                var settings = BridgeCommon.Try(() => stockpile.settings, (StorageSettings)null);
                row["filter"] = StockpileFilter.Summary(
                    settings == null ? null : settings.filter,
                    BridgeCommon.TryN(() => stockpile.settings.Priority),
                    StockpileFilter.StorableDefs(stockpile));
            }
            // The slot group's cell list IS the zone's cell list (SlotGroup.CellsList
            // -> parent.AllSlotCellsList() -> Zone_Stockpile.cells), so hauling
            // follows the list while rendering follows the grid. That is why a
            // phantom cell is a live bug and not a drawing glitch.
            row["slotGroupCellCount"] = SafeSlotGroupCellCount(stockpile);
            row["haulGridCellCount"] = HaulGridCellCount(map, stockpile, listed);

            if (!includeContents)
                return;

            var impassable = 0;
            var notStandable = 0;
            var withItems = 0;
            var free = 0;
            var contents = new Dictionary<string, ContentRow>(StringComparer.Ordinal);
            var blockers = new Dictionary<Thing, int>();

            foreach (var c in listed)
            {
                var cellImpassable = false;
                var cellStandable = true;
                var cellHasItem = false;

                try
                {
                    var edifice = c.GetEdifice(map);
                    if (edifice != null && edifice.def != null && edifice.def.passability == Traversability.Impassable)
                        cellImpassable = true;
                }
                catch { }

                try { cellStandable = c.Standable(map); }
                catch { cellStandable = true; }

                try
                {
                    foreach (var t in map.thingGrid.ThingsListAtFast(c))
                    {
                        if (t == null || t.def == null)
                            continue;

                        if (t.def.category == ThingCategory.Item)
                        {
                            cellHasItem = true;
                            var key = t.def.defName ?? "?";
                            if (!contents.TryGetValue(key, out var cr))
                            {
                                cr = new ContentRow { DefName = key, Label = t.def.label };
                                contents[key] = cr;
                            }
                            cr.Count += Math.Max(1, t.stackCount);
                            cr.Stacks++;
                        }
                        else if (t.def.category == ThingCategory.Building)
                        {
                            int n;
                            blockers.TryGetValue(t, out n);
                            blockers[t] = n + 1;
                        }
                    }
                }
                catch { }

                if (cellImpassable) impassable++;
                if (!cellStandable) notStandable++;
                if (cellHasItem) withItems++;
                // "Usable and empty": standable, not blocked by an impassable
                // edifice, and holding nothing. This is the number that would have
                // said "Stockpile 1: 30 cells, 26 occupied by a building".
                if (cellStandable && !cellImpassable && !cellHasItem) free++;
            }

            row["cellsImpassable"] = impassable;
            row["cellsNotStandable"] = notStandable;
            row["cellsWithItems"] = withItems;
            row["cellsFree"] = free;
            row["cellsBlocked"] = Math.Max(impassable, notStandable);

            var contentRows = contents.Values
                .OrderByDescending(r => r.Count)
                .Take(maxContentRows)
                .Select(r => (object)new Dictionary<string, object>
                {
                    { "defName", r.DefName },
                    { "label", r.Label },
                    { "count", r.Count },
                    { "stacks", r.Stacks }
                })
                .ToList();
            row["contents"] = contentRows;
            row["contentRowsNotListed"] = Math.Max(0, contents.Count - contentRows.Count);
            row["contentDefCount"] = contents.Count;

            row["blockingBuildings"] = blockers
                .OrderByDescending(kv => kv.Value)
                .Select(kv => (object)new Dictionary<string, object>
                {
                    { "defName", kv.Key.def != null ? kv.Key.def.defName : null },
                    { "label", SafeThingLabel(kv.Key) },
                    { "cellsOverlapped", kv.Value },
                    { "position", PositionOf(kv.Key) },
                    { "impassable", kv.Key.def != null && kv.Key.def.passability == Traversability.Impassable }
                })
                .ToList();
        }

        private sealed class ContentRow
        {
            public string DefName;
            public string Label;
            public int Count;
            public int Stacks;
        }

        // -------------------------------------------------------------- growing

        /// <summary>
        /// The growing-zone block: what the zone is set to grow, and — added
        /// 2026-09-02 — how many plants actually stand in it.
        ///
        /// BUGS.md, same day: *"Growing zone 1 lists 7 cells while 43 crops
        /// stand in it."* That is this file's own headline failure mode seen
        /// from the crop side. A zone is stored twice (`Zone.cells` and
        /// `ZoneManager.zoneGrid`) and a direct `AddCell` writes only the grid
        /// pointer, so the zone's own list can be a small fraction of what the
        /// grid gives it. Seven listed cells cannot hold 43 plants; 43 grid
        /// cells can. `listedCellCount` and `gridCellCount` already reported
        /// that divergence honestly — nothing here was counting wrong — but the
        /// payload carried NO plant count at all, so the 43 had to come from
        /// somewhere else (the screen, or `home/list_things`) and there was no
        /// number in this tool to reconcile it against.
        ///
        /// So the counts below are computed over BOTH cell sets and reported
        /// separately, never merged into one number that could be the wrong
        /// one. `plantsInListedCells` far under `plantsInGridCells` is the
        /// signature of exactly this bug, and `home/zone_cells op=repair` is the
        /// fix. Counting over the union rather than either set alone means no
        /// plant standing in a disputed cell is missed by both.
        /// </summary>
        private static void AddGrowingBlock(Dictionary<string, object> row, Map map,
                                            Zone_Growing growing, List<IntVec3> listed,
                                            List<IntVec3> grid, bool includeContents,
                                            int maxPlantRows)
        {
            // NOT the PlantDefToGrow property: its getter writes plantDefToGrow when
            // the field is null (Potato, or Toxipotato on polluted ground), so
            // reading it from a listing tool would set the colony's crop as a side
            // effect of looking. The private field is read instead and a zone that
            // has never been told what to grow reports null.
            ThingDef plant = null;
            var explicitlySet = false;
            try
            {
                var field = BridgeCommon.PrivateInstanceField(typeof(Zone_Growing), "plantDefToGrow");
                if (field != null)
                {
                    plant = field.GetValue(growing) as ThingDef;
                    explicitlySet = plant != null;
                }
            }
            catch { }

            row["plantDef"] = plant != null ? plant.defName : null;
            row["plantLabel"] = plant != null ? plant.label : null;
            row["plantDefExplicitlySet"] = explicitlySet;
            row["allowSow"] = SafeAllowSow(growing);
            row["allowCut"] = SafeAllowCut(growing);

            // Emitted even when the scan did not run, so "no plants" and "not
            // counted" can never be read off the same shape.
            row["plantScanRan"] = includeContents;
            if (!includeContents)
            {
                row["plantsInListedCells"] = null;
                row["plantsInGridCells"] = null;
                row["plantsInEitherCellSet"] = null;
                row["cropPlantsInListedCells"] = null;
                row["cropPlantsInGridCells"] = null;
                row["cellsSownInListedCells"] = null;
                row["cellsSownInGridCells"] = null;
                row["plantCountsDisagree"] = null;
                row["plantsOnlyOnGridCells"] = null;
                row["plants"] = new List<object>();
                row["plantRowsNotListed"] = 0;
                row["plantDefCount"] = null;
                row["plantScanFailed"] = false;
                return;
            }

            var listedSet = new HashSet<IntVec3>(listed);
            var gridSet = new HashSet<IntVec3>(grid);
            // The union, not either set: a plant in a cell the grid gives this
            // zone but the zone's list has forgotten belongs to exactly this
            // zone's story, and sweeping only the list is how it went missing.
            var union = new HashSet<IntVec3>(listedSet);
            union.UnionWith(gridSet);

            var plants = new Dictionary<string, PlantRow>(StringComparer.Ordinal);
            var inListed = 0;
            var inGrid = 0;
            var inEither = 0;
            var cropInListed = 0;
            var cropInGrid = 0;
            var sownListed = 0;
            var sownGrid = 0;
            var scanFailed = false;

            foreach (var c in union)
            {
                var here = 0;
                var cropHere = 0;
                var onListed = listedSet.Contains(c);
                var onGrid = gridSet.Contains(c);

                try
                {
                    foreach (var t in map.thingGrid.ThingsListAtFast(c))
                    {
                        if (t == null || t.def == null || t.def.category != ThingCategory.Plant)
                            continue;
                        here++;
                        var isCrop = plant != null && t.def == plant;
                        if (isCrop) cropHere++;

                        var key = t.def.defName ?? "?";
                        if (!plants.TryGetValue(key, out var pr))
                        {
                            pr = new PlantRow
                            {
                                DefName = key,
                                Label = t.def.label,
                                // The def's own field, not a growth read: whether
                                // this def is what the zone was TOLD to grow. A
                                // wild bush inside a rice zone is a finding, and
                                // rolling it into one "plants" number would hide
                                // it.
                                IsSetCrop = isCrop
                            };
                            plants[key] = pr;
                        }
                        if (onListed) pr.InListed++;
                        if (onGrid) pr.InGrid++;
                        pr.InEither++;
                    }
                }
                catch
                {
                    // One unreadable cell must not turn the whole count into a
                    // confident lie; the flag below says the number is a floor.
                    scanFailed = true;
                }

                if (here == 0)
                    continue;
                if (onListed) { inListed += here; cropInListed += cropHere; sownListed++; }
                if (onGrid) { inGrid += here; cropInGrid += cropHere; sownGrid++; }
                inEither += here;
            }

            row["plantsInListedCells"] = inListed;
            row["plantsInGridCells"] = inGrid;
            row["plantsInEitherCellSet"] = inEither;
            row["cropPlantsInListedCells"] = cropInListed;
            row["cropPlantsInGridCells"] = cropInGrid;
            row["cellsSownInListedCells"] = sownListed;
            row["cellsSownInGridCells"] = sownGrid;
            // The one-word version of the whole block. True means the two cell
            // sets do not agree about this zone's crop, so any single "how many
            // plants" number quoted from here is the answer to a narrower
            // question than the one that was asked.
            row["plantCountsDisagree"] = inListed != inGrid;
            // How many more plants the grid's cell set holds than the zone's own
            // list does -- on the reported case, 43 against 7. This is the size
            // of what was invisible, in one number, and it goes to 0 after a
            // home/zone_cells op=repair. Floored at 0: a list that is somehow
            // the LARGER set is a phantom-cell problem, already named cell by
            // cell in phantomCells[], not something to report as negative crops.
            row["plantsOnlyOnGridCells"] = Math.Max(0, inGrid - inListed);
            row["plantScanFailed"] = scanFailed;

            var plantRows = plants.Values
                .OrderByDescending(p => p.InEither)
                .Take(maxPlantRows)
                .Select(p => (object)new Dictionary<string, object>
                {
                    { "defName", p.DefName },
                    { "label", p.Label },
                    { "isSetCrop", p.IsSetCrop },
                    { "inListedCells", p.InListed },
                    { "inGridCells", p.InGrid },
                    { "inEitherCellSet", p.InEither }
                })
                .ToList();
            row["plants"] = plantRows;
            row["plantRowsNotListed"] = Math.Max(0, plants.Count - plantRows.Count);
            row["plantDefCount"] = plants.Count;
        }

        private sealed class PlantRow
        {
            public string DefName;
            public string Label;
            public bool IsSetCrop;
            public int InListed;
            public int InGrid;
            public int InEither;
        }

        // -------------------------------------------------------------- helpers

        /// <summary>
        /// 4-connected flood fill over a COPY of the cell list. Deliberately not
        /// <c>Zone.CheckContiguous()</c>, which removes every cell it cannot reach
        /// from cells[0] — a destructive answer to a read-only question. Cardinal
        /// connectivity matches the game's own zone-growing rule
        /// (Designator_ZoneAdd walks GenAdj.CardinalDirections).
        /// </summary>
        private static bool IsContiguous(List<IntVec3> cells)
        {
            if (cells == null || cells.Count <= 1)
                return true;

            var remaining = new HashSet<IntVec3>(cells);
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

            // Duplicates in the list (which is itself a defect) would make
            // cells.Count exceed the set size; compare against the set instead.
            return found >= new HashSet<IntVec3>(cells).Count;
        }

        private static Dictionary<string, object> Bounds(List<IntVec3> cells)
        {
            if (cells == null || cells.Count == 0)
                return null;
            int minX = int.MaxValue, minZ = int.MaxValue, maxX = int.MinValue, maxZ = int.MinValue;
            foreach (var c in cells)
            {
                if (c.x < minX) minX = c.x;
                if (c.x > maxX) maxX = c.x;
                if (c.z < minZ) minZ = c.z;
                if (c.z > maxZ) maxZ = c.z;
            }
            return new Dictionary<string, object>
            {
                { "minX", minX }, { "minZ", minZ },
                { "maxX", maxX }, { "maxZ", maxZ },
                { "width", maxX - minX + 1 }, { "height", maxZ - minZ + 1 }
            };
        }

        private static Dictionary<string, object> Notes()
        {
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "listedVsGrid", "listedCellCount is Zone.cells.Count (the zone's own list, which is also SlotGroup.CellsList for a stockpile, so hauling follows it). gridCellCount is how many map cells ZoneManager.ZoneAt() actually returns this zone for. They must be equal; when they are not, phantomCells and orphanGridCells name every cell involved and home/zone_cells op=repair fixes it." },
                { "cellsPropertyShuffles", "Zone.Cells (capital C) shuffles the underlying list on first read. This tool reads the `cells` field, so calling it does not reorder anything." },
                { "contiguity", "Computed by a local 4-connected flood fill over a copy of the list. Zone.CheckContiguous() is NOT called: it deletes every cell it cannot reach from cells[0]." },
                { "plantDefRead", "Zone_Growing.PlantDefToGrow's getter assigns the field when it is null, so the private plantDefToGrow field is read instead. plantDef null with plantDefExplicitlySet false means the zone has never been given a crop and the game will default it to potatoes the first time anything asks." },
                { "occupancyCellSet", "Stockpile occupancy, contents and blockingBuildings are computed over the zone's LISTED cells, because that is the set the slot group and the hauling lister use." },
                // 2026-09-02, BUGS.md: "Growing zone 1 lists 7 cells while 43
                // crops stand in it."
                { "growingPlantCounts", "A growing zone reports plantsInListedCells and plantsInGridCells SEPARATELY, plus cropPlantsIn* for the def the zone is actually set to grow, and never merges them into one number. Seven listed cells cannot hold 43 plants; the grid giving that zone 43 cells can, and that is the same list/grid divergence listedCellCount vs gridCellCount reports. plantCountsDisagree true, or plantsOnlyOnGridCells above 0, means quoting either figure alone answers a narrower question than the one asked -- run home/zone_cells op=repair, then the two agree and either number is safe. Plants are counted over the UNION of both cell sets so a plant in a disputed cell is missed by neither. Requires includeContents; when it is false every count is null and plantScanRan is false." },
                { "cellsBlocked", "cellsBlocked is max(cellsImpassable, cellsNotStandable) and is a convenience only; the two underlying counts are reported separately because they are different questions (a standable cell can still be inside a passable building, and an impassable edifice is not the only way to be unstandable)." },
                { "readOnly", "Nothing here writes game state and no path reaching Verse.Log.Error (which calls TickManager.Pause) is entered." }
            };
        }

        private static List<IntVec3> SafeCells(Zone zone)
        {
            try
            {
                var cells = zone.cells;
                return cells == null ? new List<IntVec3>() : new List<IntVec3>(cells);
            }
            catch { return new List<IntVec3>(); }
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

        private static bool SafeHidden(Zone zone)
        {
            try { return zone.Hidden; }
            catch { return false; }
        }

        private static string SafePriority(Zone_Stockpile stockpile)
        {
            try { return stockpile.settings == null ? null : stockpile.settings.Priority.ToString(); }
            catch { return null; }
        }

        private static Dictionary<string, object> FilterSummary(Zone_Stockpile stockpile)
        {
            var summary = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "allowedDefCount", null },
                { "allowedSample", new List<object>() },
                { "allowedSampleTruncated", false }
            };
            try
            {
                var settings = stockpile.settings;
                if (settings == null || settings.filter == null)
                    return summary;
                summary["allowedDefCount"] = settings.filter.AllowedDefCount;

                var sample = new List<object>();
                var total = 0;
                foreach (var def in settings.filter.AllowedThingDefs)
                {
                    if (def == null)
                        continue;
                    total++;
                    if (sample.Count < FilterLabelSample)
                        sample.Add(def.label ?? def.defName);
                }
                summary["allowedSample"] = sample;
                summary["allowedSampleTruncated"] = total > sample.Count;
            }
            catch { }
            return summary;
        }

        private static int? SafeSlotGroupCellCount(Zone_Stockpile stockpile)
        {
            try
            {
                if (stockpile.slotGroup == null)
                    return null;
                var cells = stockpile.slotGroup.CellsList;
                return cells == null ? (int?)null : cells.Count;
            }
            catch { return null; }
        }

        /// <summary>
        /// How many of the zone's listed cells the HaulDestinationManager's own
        /// group grid also assigns to this stockpile's slot group. It is a third
        /// grid, written by SlotGroup.Notify_AddedCell / Notify_LostCell, and it
        /// can disagree with both the list and the zone grid after a bad add.
        /// Worth knowing before any repair, because ClearCellFor / SetCellFor call
        /// Log.Error (and therefore pause the game) when they disagree.
        /// </summary>
        private static int? HaulGridCellCount(Map map, Zone_Stockpile stockpile, List<IntVec3> listed)
        {
            try
            {
                var manager = map.haulDestinationManager;
                if (manager == null || stockpile.slotGroup == null)
                    return null;
                var n = 0;
                foreach (var c in listed)
                    if (manager.SlotGroupAt(c) == stockpile.slotGroup)
                        n++;
                return n;
            }
            catch { return null; }
        }

        private static bool SafeAllowSow(Zone_Growing growing)
        {
            try { return growing.allowSow; }
            catch { return true; }
        }

        private static bool SafeAllowCut(Zone_Growing growing)
        {
            try { return growing.allowCut; }
            catch { return true; }
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

        /// <summary>See BridgeCommon.PositionOf: {x, z}, or null if the getter throws.</summary>
        private static Dictionary<string, object> PositionOf(Thing thing)
        {
            return BridgeCommon.PositionOf(thing);
        }

        private static string SafeMapName(Map map)
        {
            try { return map.Parent == null ? null : map.Parent.Label; }
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
