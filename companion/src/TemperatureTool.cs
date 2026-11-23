using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using RimBridgeServer.Sdk;
using RimWorld;
using Verse;

namespace HomeBridge.BridgeTools
{
    /// <summary>
    /// home/get_temperatures — per-ROOM (default) or per-CELL temperature over a
    /// rectangle, in degrees Celsius.
    ///
    /// ## Celsius, confirmed
    ///
    /// RimWorld stores temperature in Celsius internally and converts only at the
    /// UI boundary: `GenTemperature.ConvertTemperatureOffset(float, TemperatureDisplayMode,
    /// TemperatureDisplayMode)` exists precisely because the display mode is a
    /// display concern. `Room.Temperature`, `MapTemperature.OutdoorTemp` and
    /// `GenTemperature.TryGetTemperatureForCell` are all raw Celsius. Nothing here
    /// converts anything; `unit: "C"` is emitted so a caller never has to assume.
    ///
    /// ## Why rooms is the default (M, 2026-08-31: "just always do per room
    /// if that's an option")
    ///
    /// RimWorld does not simulate a temperature per cell. It simulates one per
    /// ROOM — `RoomTempTracker` — and every per-cell getter is a lookup of the
    /// containing room's number. So "per cell" is a rendering convenience, not
    /// extra information, and a 128x128 cells-mode payload is ~16k copies of at
    /// most a few dozen distinct values. Rooms mode returns those few dozen values
    /// once, plus a compact index grid that says which room each cell belongs to,
    /// which is strictly less data for strictly the same picture.
    ///
    /// ## The grid, and why an index grid rather than per-room cell lists
    ///
    /// Two ways to make rooms mode paintable were considered:
    ///
    ///   a) each room carries the list of its cells inside the rect, as [x,z] pairs
    ///   b) one row-major grid of room INDEXES, null where a cell has no room
    ///
    /// (b) wins on both counts. (a) writes two coordinates per cell, i.e. roughly
    /// twice the tokens of (b) for exactly the same information, and it makes the
    /// consumer reconstruct raster order it already knows. (b) is a small integer
    /// per cell in the same row-major order as `temps` in cells mode, so a caller
    /// can render either mode with one loop.
    ///
    /// The grid carries the RESPONSE-LOCAL index into `rooms[]` (0..n-1), not
    /// `Room.ID`. Room IDs are 6-8 digit ints; repeating one 16,384 times costs
    /// several times what an index does, and the real id is on the room row for
    /// anyone who needs to correlate between calls.
    ///
    /// ## What comes back null, exactly
    ///
    /// Never omitted, per the discipline this companion exists to enforce: every
    /// cell of the rect has an entry in `temps` / `roomGrid`, and a cell with no
    /// answer is an explicit `null`. A short row would be indistinguishable from a
    /// cold room.
    ///
    /// A caveat worth stating because a naive reading of "walls have no room" gets
    /// it wrong: `GenTemperature.TryGetTemperatureForCell` falls back to
    /// `TryGetAirTemperatureAroundThing` when the cell itself has no room, so a
    /// CONSTRUCTED WALL between two rooms returns the average of the air around it
    /// rather than null. Null in cells mode means "no room here and nothing at this
    /// cell touching air" — deep natural rock inside a mountain, and cells outside
    /// any region. `roomGrid` is stricter: it is null for every cell with no room
    /// at all, walls included. The two nulls answer two different questions on
    /// purpose.
    ///
    /// ## Verified by reflection against the installed Assembly-CSharp.dll (1.6)
    ///
    ///   Verse.GenTemperature.TryGetTemperatureForCell(IntVec3, Map, out float)  -> bool
    ///   Verse.MapTemperature.OutdoorTemp                                        -> float
    ///   Verse.GridsUtility.GetRoom(IntVec3, Map)                                -> Room
    ///        (two args in 1.6, no RegionType overload; it forwards to
    ///         RegionAndRoomQuery.RoomAt, so both routes are the same code path)
    ///   Verse.Room.ID (public int FIELD, not a property), .Temperature (float),
    ///        .CellCount (int), .UsesOutdoorTemperature (bool),
    ///        .PsychologicallyOutdoors (bool), .ProperRoom (bool), .Role (RoomRoleDef),
    ///        .ContainsCell(IntVec3), .ContainedAndAdjacentThings (List&lt;Thing&gt;)
    ///   RimWorld.BuildingProperties.isWall (bool FIELD) — exists in 1.6, so the
    ///        wall test is a def flag rather than a defName string match
    ///   RimWorld.Building_Door, RimWorld.Building_Bed.OwnersForReading (List&lt;Pawn&gt;),
    ///        RimWorld.Building_WorkTable
    ///
    /// Every one of those getters was IL-scanned for field stores and none writes
    /// anything, so this tool is read-only in the same sense list_buildings is —
    /// the lesson from `Bill_Production.ShouldDoNow()`, which does mutate.
    ///
    /// One reflection finding that matters at runtime rather than at compile time:
    /// **`Room.ContainedAndAdjacentThings` returns a CACHED buffer.** Its IL calls
    /// `HashSet.Clear` and `List.Clear` and then refills, so the list a caller is
    /// handed is invalidated by the next read of the same property. This tool
    /// therefore reads it once per room, scans it by index in a single pass, and
    /// never retains the reference. It also, as the name says, includes ADJACENT
    /// things — the room's own bounding walls and doors — which is why the notable
    /// building filter drops walls and doors explicitly rather than trusting
    /// containment.
    ///
    /// ## Where the room walk lives now
    ///
    /// The rect-to-rooms walk itself is `BridgeCommon.RoomWalk`, shared with
    /// `home/list_rooms`. This file keeps only what is a temperature question: the
    /// per-room temperature, the outdoor temperature, and the legend fields
    /// (`closestPawn`, `notableBuilding`). The move changed no key, no key order
    /// and no value in this tool's reply — `rooms[]` is still numbered in raster
    /// order of first appearance, so `roomGrid`'s first non-null value is still 0.
    /// This is a temperature tool; it should not also be the room registry, and
    /// the room registry should not be rect-capped just because temperature
    /// readings are.
    /// </summary>
    public sealed class HomeTemperatureTools
    {
        /// <summary>
        /// 16,384 cells (128x128), sixteen times the 1024-cell cap on
        /// home/get_cells_plus. The cap exists to bound payload, and the payload
        /// here is one number per cell — a rounded temperature serializes to about
        /// 6 bytes against the ~150 bytes of a get_cells_plus cell object. A full
        /// 16,384-cell cells-mode response therefore lands near 100 KB, below the
        /// 152 KB that a 1024-cell get_cells_plus call already measured at on
        /// 2026-08-30. Rooms mode over the same rect is smaller again. 128x128 also
        /// happens to cover an entire fort in one call, which is the question being
        /// asked.
        /// </summary>
        private const int MaxCells = 16384;

        private const string ToolName = "home/get_temperatures";

        [Tool(
            ToolName,
            Title = "Read room or per-cell temperatures over a rectangle",
            Description =
                "Temperatures in degrees Celsius over a rectangle of up to 16384 cells (128x128). Default mode 'rooms' returns "
                + "one entry per distinct room intersecting the rectangle plus a compact row-major grid of room indexes, which is "
                + "the cheap way to answer 'how cold is it inside the fort'. Mode 'cells' returns a row-major 2D array of per-cell "
                + "temperatures instead. Both modes report the map's outdoor temperature.",
            ResultDescription =
                "success, tool, mode, the echoed rect, cellCount, outdoorTemp, unit; then rooms[] + roomGrid in rooms mode, or "
                + "temps[][] in cells mode. Each room also carries a legend line's worth of context: center, closestPawn, "
                + "closestPawnDistance, notableBuilding and notableBuildingOwner.")]
        [ToolResponse("outdoorTemp", "number", "The map's current outdoor temperature in Celsius. Present in both modes.", Always = true)]
        [ToolResponse("rooms", "array", "Rooms mode only: one entry per distinct room intersecting the rectangle.")]
        [ToolResponse("roomGrid", "array", "Rooms mode only: row-major rows of indexes into rooms[], null where a cell has no room.")]
        [ToolResponse("temps", "array", "Cells mode only: row-major rows of per-cell Celsius temperatures, null where there is no answer.")]
        [ToolResponse("unknownArguments", "array", "Every argument key the caller sent that this tool does not declare, sorted, case-sensitively. Empty array = every key was recognised. The host's own _rimBridgeTimeoutMs is never listed.", Always = true)]
        [ToolResponse("unknownArgumentsWarning", "string", "Present only when unknownArguments is non-empty, or when the caller's raw keys could not be read at all - in which case the empty unknownArguments means 'not known', not 'nothing unknown'.", Nullable = true)]
        public async Task<object> GetTemperatures(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            [ToolParameter(Description = "Top-left cell x coordinate", Required = true)] int x,
            [ToolParameter(Description = "Top-left cell z coordinate", Required = true)] int z,
            [ToolParameter(Description = "Rectangle width in cells; width * height must not exceed 16384. Rooms mode refuses a 1x1 rect - it would answer with whichever single room covers that cell.", DefaultValue = 1)] int width = 1,
            [ToolParameter(Description = "Rectangle height in cells; width * height must not exceed 16384. Rooms mode refuses a 1x1 rect.", DefaultValue = 1)] int height = 1,
            [ToolParameter(Description = "'rooms' (default) for one entry per room plus a room-index grid, or 'cells' for a per-cell temperature grid.", DefaultValue = "rooms")] string mode = "rooms")
        {
            return BridgeCommon.WithUnknownArguments(
                await GetTemperaturesCore(ctx, cancellationToken, x, z, width, height, mode).ConfigureAwait(false),
                ctx, typeof(HomeTemperatureTools), ToolName);
        }

        private async Task<object> GetTemperaturesCore(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            int x,
            int z,
            int width,
            int height,
            string mode)
        {
            if (ctx?.MainThread == null)
                return Failure("No RimBridge main-thread dispatcher is available for this invocation.");

            // Companion tools are dispatched with MarshalToMainThread = false
            // (AnnotatedExtensionCapabilityProvider.InvokeAsync), so every read of
            // the region grid, the room temp trackers and mapTemperature has to be
            // hopped onto RimWorld's main thread by hand. The whole rectangle goes
            // inside ONE hop: removing it still compiles and then fails
            // intermittently and unreproducibly, which is the worst failure mode
            // available. Keep the hop.
            return await ctx.MainThread
                .InvokeAsync(() => BuildResponse(x, z, width, height, mode), cancellationToken)
                .ConfigureAwait(false);
        }

        private static object BuildResponse(int x, int z, int width, int height, string mode)
        {
            var normalizedMode = string.IsNullOrEmpty(mode) ? "rooms" : mode.Trim().ToLowerInvariant();
            if (normalizedMode != "rooms" && normalizedMode != "cells")
                return Failure($"Unknown mode '{mode}'. Valid modes are 'rooms' (default) and 'cells'.");

            if (!TryGetMap(out var map, out var mapError))
                return Failure(mapError);

            if (width <= 0 || height <= 0)
                return Failure("Width and height must be positive.");

            var requestedCellCount = (long)width * height;
            if (requestedCellCount > MaxCells)
                return Failure($"Requested rectangle contains {requestedCellCount} cells, which exceeds the limit of {MaxCells}.");

            // The binder fills an omitted int with 0, so a rooms-mode call that
            // named no rectangle arrives here as a 1x1 at (0,0) and answers
            // "one room, the outdoors" — a complete-looking reply to a question
            // nobody asked. Cells mode keeps its 1x1: one cell is one
            // temperature there, which is a real answer.
            if (normalizedMode == "rooms" && requestedCellCount == 1)
                return Failure("Rooms mode needs a rectangle: width and height are both 1, which reports whichever single room "
                             + $"covers cell ({x}, {z}) and nothing else. Pass width and height, or call home/list_rooms for the "
                             + "whole-map census (it takes x/z alone to say which room a single cell is in).");

            // Same out-of-bounds semantics as home/get_cells_plus: fail loudly
            // rather than silently clipping, so a caller can never mistake a
            // clipped rect for a smaller fort.
            for (var offsetZ = 0; offsetZ < height; offsetZ++)
            {
                for (var offsetX = 0; offsetX < width; offsetX++)
                {
                    var probe = new IntVec3(x + offsetX, 0, z + offsetZ);
                    if (!probe.InBounds(map))
                        return Failure($"Cell ({probe.x}, {probe.z}) is out of bounds for the current map.");
                }
            }

            var payload = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["success"] = true,
                ["tool"] = ToolName,
                ["mode"] = normalizedMode,
                ["mapName"] = SafeMapName(map),
                ["rect"] = new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    ["x"] = x,
                    ["z"] = z,
                    ["width"] = width,
                    ["height"] = height
                },
                ["cellCount"] = (int)requestedCellCount,
                // Always present, both modes, so "is the fort warmer than outside"
                // never needs a second call.
                ["outdoorTemp"] = SafeOutdoorTemp(map),
                ["unit"] = "C"
            };

            if (normalizedMode == "cells")
                AddCellsMode(payload, map, x, z, width, height);
            else
                AddRoomsMode(payload, map, x, z, width, height);

            return payload;
        }

        // ------------------------------------------------------------------
        // cells mode
        // ------------------------------------------------------------------

        private static void AddCellsMode(IDictionary<string, object> payload, Map map, int x, int z, int width, int height)
        {
            var rows = new List<List<object>>(height);
            var withoutTemperature = 0;

            for (var offsetZ = 0; offsetZ < height; offsetZ++)
            {
                var row = new List<object>(width);
                for (var offsetX = 0; offsetX < width; offsetX++)
                {
                    var cell = new IntVec3(x + offsetX, 0, z + offsetZ);
                    var temperature = SafeCellTemperature(map, cell);
                    if (temperature == null)
                        withoutTemperature++;
                    // Explicit null, never a short row. A missing entry would be
                    // indistinguishable from a cold cell.
                    row.Add(temperature);
                }
                rows.Add(row);
            }

            payload["temps"] = rows;
            payload["cellsWithoutTemperature"] = withoutTemperature;
            payload["notes"] = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["order"] = "row-major; temps[0] is the row at rect.z, temps[i][j] is cell (rect.x + j, rect.z + i)",
                ["nullMeans"] = "GenTemperature.TryGetTemperatureForCell returned false: no room at the cell and nothing there touching air (deep natural rock). A constructed wall usually DOES return a number, averaged from the air around it.",
                ["rounding"] = "1 decimal place"
            };
        }

        // ------------------------------------------------------------------
        // rooms mode (default)
        // ------------------------------------------------------------------

        private static void AddRoomsMode(IDictionary<string, object> payload, Map map, int x, int z, int width, int height)
        {
            // The rect walk itself lives in BridgeCommon.RoomWalk, shared with
            // home/list_rooms so both tools number and order rooms identically.
            // Everything below is what this tool adds on top of it: a temperature
            // and a legend line. The payload it produces is unchanged by the
            // move — same keys, same order, same values.
            var walk = RoomWalk.OverRect(map, x, z, width, height);

            // RimWorld's own pawn list, read ONCE for the whole call and reused by
            // every room. The nearest-pawn answer is O(rooms * pawns), never a
            // tile-by-tile sweep — the same lesson home/list_pawns was built on.
            var pawns = NearestPawnIndex.Build(map);

            var roomPayloads = new List<object>(walk.Rooms.Count);
            foreach (var entry in walk.Rooms)
                roomPayloads.Add(RoomPayload(entry, pawns));

            payload["roomCount"] = walk.Rooms.Count;
            payload["cellsWithNoRoom"] = walk.CellsWithNoRoom;
            payload["rooms"] = roomPayloads;
            payload["roomGrid"] = walk.Grid;
            payload["notes"] = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["order"] = "row-major; roomGrid[0] is the row at rect.z, roomGrid[i][j] is cell (rect.x + j, rect.z + i)",
                ["gridValues"] = "index into rooms[] (rooms[k].index == k), or null when the cell is in no room at all — walls, doors' solid neighbours, deep rock, unregioned cells.",
                ["outdoorsFlag"] = "outdoors = Room.UsesOutdoorTemperature, the flag that governs whether the room's temperature is slaved to outdoorTemp. psychologicallyOutdoors = Room.PsychologicallyOutdoors, RimWorld's separate 'does it FEEL outdoors' mood flag; a large under-roof room can be one and not the other.",
                ["cellCountVsCellsInRect"] = "cellCount is the whole room's size on the map; cellsInRect counts only the cells inside the requested rectangle.",
                ["center"] = "rough centroid of the room's cells INSIDE the rect, snapped to an actual room cell so an L-shaped room never labels itself in the notch. Always present; use it to place a legend marker.",
                ["legendFields"] = "closestPawn / closestPawnDistance / notableBuilding / notableBuildingOwner are omitted when there is no answer, so a legend line can be built by testing presence.",
                ["rounding"] = "1 decimal place"
            };
        }

        /// <summary>
        /// One room's row in rooms mode. The key order here IS the wire order, so
        /// it is not to be tidied: a caller diffing two calls diffs the JSON.
        /// </summary>
        private static object RoomPayload(RoomWalk.Entry entry, NearestPawnIndex pawns)
        {
            var room = entry.Room;
            var center = entry.Center();

            var payload = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["index"] = entry.Index,
                ["id"] = entry.Id,
                // Never omitted. Null here would mean the getter threw, which
                // is a different thing from a room at 0 C.
                ["temperature"] = SafeRoomTemperature(room),
                ["cellsInRect"] = entry.CellsInRect,
                ["cellCount"] = SafeCellCount(room),
                ["outdoors"] = SafeBool(() => room.UsesOutdoorTemperature),
                ["psychologicallyOutdoors"] = SafeBool(() => room.PsychologicallyOutdoors),
                ["properRoom"] = SafeBool(() => room.ProperRoom),
                ["role"] = SafeRole(room),
                // A cell guaranteed to be both inside this room and inside the
                // requested rect, so it can be handed straight to another
                // bridge tool (get_cells_plus, click_cell) without a search.
                ["representativeCell"] = new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    ["x"] = entry.RepresentativeCell.x,
                    ["z"] = entry.RepresentativeCell.z
                },
                // Always present: this is where a legend marker goes.
                ["center"] = new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    ["x"] = center.x,
                    ["z"] = center.z
                }
            };

            // --- legend fields: short strings, omitted when there is no answer
            var nearest = pawns.Nearest(center);
            if (nearest != null)
            {
                payload["closestPawn"] = nearest.Name;
                payload["closestPawnDistance"] = nearest.Distance;
            }

            string owner;
            var notable = FindNotableBuilding(room, out owner);
            if (!string.IsNullOrEmpty(notable))
            {
                payload["notableBuilding"] = notable;
                if (!string.IsNullOrEmpty(owner))
                    payload["notableBuildingOwner"] = owner;
            }

            return payload;
        }

        /// <summary>
        /// The nearest-pawn answer for a legend line. Built once per call from
        /// RimWorld's own `map.mapPawns.AllPawnsSpawned` — O(pawns), never a cell
        /// sweep.
        ///
        /// Colonists are preferred whenever the map has a live one, because
        /// "closest pawn: Finn" is the legend M asked for and "closest pawn:
        /// Muffalo" is not; `closestPawnDistance` is emitted alongside so a caller
        /// can see when that colonist is nowhere near the room.
        /// </summary>
        private sealed class NearestPawnIndex
        {
            private readonly List<KeyValuePair<string, IntVec3>> _colonists = new List<KeyValuePair<string, IntVec3>>();
            private readonly List<KeyValuePair<string, IntVec3>> _all = new List<KeyValuePair<string, IntVec3>>();

            internal static NearestPawnIndex Build(Map map)
            {
                var index = new NearestPawnIndex();
                try
                {
                    var spawned = map.mapPawns == null ? null : map.mapPawns.AllPawnsSpawned;
                    if (spawned == null)
                        return index;

                    for (var i = 0; i < spawned.Count; i++)
                    {
                        var pawn = spawned[i];
                        if (pawn == null)
                            continue;
                        var name = SafePawnName(pawn);
                        if (string.IsNullOrEmpty(name))
                            continue;
                        var entry = new KeyValuePair<string, IntVec3>(name, pawn.Position);
                        index._all.Add(entry);
                        if (SafeIsLiveColonist(pawn))
                            index._colonists.Add(entry);
                    }
                }
                catch
                {
                    // A legend line is not worth failing the temperature read over.
                }
                return index;
            }

            internal NearestPawn Nearest(IntVec3 from)
            {
                return NearestIn(_colonists, from) ?? NearestIn(_all, from);
            }

            private static NearestPawn NearestIn(List<KeyValuePair<string, IntVec3>> candidates, IntVec3 from)
            {
                NearestPawn best = null;
                for (var i = 0; i < candidates.Count; i++)
                {
                    // Chebyshev, the same metric home/list_pawns reports, so the two
                    // tools' distances mean the same thing.
                    var distance = Math.Max(
                        Math.Abs(candidates[i].Value.x - from.x),
                        Math.Abs(candidates[i].Value.z - from.z));
                    if (best == null || distance < best.Distance)
                        best = new NearestPawn(candidates[i].Key, distance);
                }
                return best;
            }
        }

        private sealed class NearestPawn
        {
            internal NearestPawn(string name, int distance)
            {
                Name = name;
                Distance = distance;
            }

            internal string Name { get; private set; }
            internal int Distance { get; private set; }
        }

        /// <summary>
        /// One short defName naming what the room obviously IS, for a legend line.
        /// Priority: a bed (with its owner if assigned — "Finn's bed" pins a bedroom
        /// better than anything else on the map), then a worktable, then any other
        /// real building.
        ///
        /// `Room.ContainedAndAdjacentThings` includes the room's bounding walls and
        /// doors, so both are filtered out explicitly — via
        /// `ThingDef.building.isWall` / `isNaturalRock` and a `Building_Door` type
        /// test, not a defName string match. Scanned by index in one pass and the
        /// list reference is never retained, because it is a cached buffer that the
        /// next read of the property clears.
        /// </summary>
        private static string FindNotableBuilding(Room room, out string ownerName)
        {
            ownerName = null;
            try
            {
                var things = room.ContainedAndAdjacentThings;
                if (things == null)
                    return null;

                string worktable = null;
                string other = null;

                for (var i = 0; i < things.Count; i++)
                {
                    var thing = things[i];
                    var building = thing as Building;
                    if (building == null || building.def == null)
                        continue;
                    if (building is Building_Door)
                        continue;

                    var props = building.def.building;
                    if (props != null && (props.isWall || props.isNaturalRock))
                        continue;

                    var bed = building as Building_Bed;
                    if (bed != null)
                    {
                        ownerName = FirstOwnerName(bed);
                        return building.def.defName;    // highest priority, stop here
                    }

                    if (worktable == null && (building is Building_WorkTable || building is IBillGiver))
                        worktable = building.def.defName;
                    else if (other == null)
                        other = building.def.defName;
                }

                return worktable ?? other;
            }
            catch
            {
                return null;
            }
        }

        private static string FirstOwnerName(Building_Bed bed)
        {
            try
            {
                var owners = bed.OwnersForReading;
                if (owners == null)
                    return null;
                for (var i = 0; i < owners.Count; i++)
                {
                    if (owners[i] == null)
                        continue;
                    var name = SafePawnName(owners[i]);
                    if (!string.IsNullOrEmpty(name))
                        return name;
                }
            }
            catch
            {
                // unassigned or unreadable; the key is simply omitted
            }
            return null;
        }

        private static string SafePawnName(Pawn pawn)
        {
            try
            {
                return pawn.LabelShortCap.ToString();
            }
            catch
            {
                try { return pawn.LabelCap.ToString(); }
                catch { return null; }
            }
        }

        private static bool SafeIsLiveColonist(Pawn pawn)
        {
            try
            {
                return pawn.IsColonist && !pawn.Dead;
            }
            catch
            {
                return false;
            }
        }

        // ------------------------------------------------------------------
        // safe accessors
        // ------------------------------------------------------------------

        private static object SafeCellTemperature(Map map, IntVec3 cell)
        {
            try
            {
                float temperature;
                if (!GenTemperature.TryGetTemperatureForCell(cell, map, out temperature))
                    return null;
                return Round(temperature);
            }
            catch
            {
                return null;
            }
        }

        private static object SafeRoomTemperature(Room room)
        {
            try
            {
                return Round(room.Temperature);
            }
            catch
            {
                return null;
            }
        }

        private static object SafeOutdoorTemp(Map map)
        {
            try
            {
                return map.mapTemperature == null ? null : (object)Round(map.mapTemperature.OutdoorTemp);
            }
            catch
            {
                return null;
            }
        }

        private static object SafeCellCount(Room room)
        {
            try
            {
                return room.CellCount;
            }
            catch
            {
                return null;
            }
        }

        private static object SafeBool(Func<bool> read)
        {
            try
            {
                return read();
            }
            catch
            {
                return null;
            }
        }

        private static string SafeRole(Room room)
        {
            try
            {
                return room.Role == null ? null : room.Role.defName;
            }
            catch
            {
                return null;
            }
        }

        private static string SafeMapName(Map map)
        {
            try
            {
                return map.Parent == null ? null : map.Parent.Label;
            }
            catch
            {
                return null;
            }
        }

        /// <summary>
        /// Celsius, one decimal. Rounded as a double rather than a float so the
        /// serialized number reads 21.3 instead of 21.299999237060547.
        /// </summary>
        private static double Round(float celsius)
        {
            return Math.Round((double)celsius, 1, MidpointRounding.AwayFromZero);
        }

        /// <summary>The shared map gate; see BridgeCommon.TryGetMap. The error
        /// text names this tool.</summary>
        private static bool TryGetMap(out Map map, out string error)
        {
            return BridgeCommon.TryGetMap(ToolName, out map, out error);
        }

        /// <summary>The shared refusal shape, plus `message`. BridgeCommon
        /// writes `error`; every reader of a bridge reply — rim.py's own
        /// BridgeError included — looks for `message`, so a refusal that
        /// carries only `error` reads as a refusal with no reason at all.
        /// Both keys hold the same sentence.</summary>
        private static object Failure(string error)
        {
            var payload = BridgeCommon.Failure(ToolName, error);
            payload["message"] = error;
            return payload;
        }
    }
}
