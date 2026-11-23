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
    /// home/list_rooms — every room on the map in ONE call, with what is in it.
    ///
    /// WANTED 15, in M's words: *"An external file where the shape/location
    /// of a room is recorded, along with its name. When a room is checked (or a
    /// coordinate is sent out with the intent of checking the room it covers), I'd
    /// like it to reply with the room name, location w/coordinates, pawns in it,
    /// furniture/buildings in it, and the names of any stockpiles in it."*
    ///
    /// ## Why there is no external key file
    ///
    /// A room in RimWorld has no player-given name and no stable identity. `Room.ID`
    /// is handed out by a static counter and a room is DESTROYED and remade every
    /// time a wall changes — `RegionAndRoomUpdater` rebuilds them, so knocking one
    /// hole in a wall renumbers both rooms involved. A hand-maintained key file
    /// would therefore be a file of stale ids pointing at rooms that no longer
    /// exist, and reading it would be worse than having nothing, because it would
    /// look authoritative.
    ///
    /// What the game DOES have is a name it computes itself: `Room.Role` (a
    /// `RoomRoleDef` chosen by scoring every role worker against the room's
    /// contents) plus the owners of any bed inside it. That is `name` below —
    /// "Bedroom (Lucas)" — recomputed fresh on every call, so it is never stale.
    /// The key characters a caller paints a map with are a RESPONSE-LOCAL index,
    /// for the same reason: they mean something for one reply and nothing after it.
    ///
    /// ## No rect cap
    ///
    /// `home/get_temperatures` walks a rectangle and is capped at 16,384 cells,
    /// so covering a 250x250 map with it takes ~7 calls and ~260 KB. This tool
    /// does not walk cells to FIND rooms; it reads `RegionGrid.AllRooms`, which is
    /// a public `IReadOnlyList&lt;Room&gt;` the game maintains anyway. Whole map,
    /// one property access, no cap.
    ///
    /// The cells ARE walked, once, per LISTED room — for `center`, for the
    /// stockpile intersection and for the optional `cells[]`. That is why the
    /// outdoors mega-room is off by default: on Lampblack it is 49,347 cells, and
    /// nobody asking "which rooms exist" means that one.
    ///
    /// ## Verified against the installed Assembly-CSharp.dll (RimWorld 1.6)
    ///
    ///   Verse.RegionGrid.AllRooms          -> IReadOnlyList&lt;Room&gt;   (public)
    ///   Verse.RegionGrid.RoomLookup        -> IReadOnlyDictionary&lt;int, Room&gt;
    ///   Verse.Room.ID (public int FIELD), .ExtentsClose (CellRect), .Cells
    ///        (IEnumerable&lt;IntVec3&gt;, fresh iterator per read), .CellCount,
    ///        .RegionCount, .Role, .GetRoomRoleLabel(), .Owners, .ContainedBeds,
    ///        .ContainedAndAdjacentThings, .ProperRoom, .PsychologicallyOutdoors,
    ///        .UsesOutdoorTemperature, .Fogged, .IsDoorway, .Door, .TouchesMapEdge,
    ///        .OpenRoofCount, .Temperature, .ContainsCell(IntVec3),
    ///        .GetStat(RoomStatDef)
    ///   Verse.RoomStatDef.GetScoreStage(float) -> RoomStatScoreStage {label,
    ///        minScore}; .ScoreToString(float); .displayRounded; .isHidden
    ///   RimWorld.RoomStatDefOf.{Cleanliness, Wealth, Space, Beauty, Impressiveness}
    ///   Verse.RoomRoleDef.PostProcessedLabelCap(Room)
    ///   Verse.ZoneManager.ZoneAt(IntVec3), RimWorld.Zone_Stockpile
    ///   RimWorld.Building_Bed.OwnersForReading (List&lt;Pawn&gt;), .Medical,
    ///        .ForPrisoners
    ///
    /// ## Four hazards the decompile turned up, and what is done about each
    ///
    ///   1. **`Room.ContainedAndAdjacentThings` returns a CACHED buffer.** The
    ///      getter clears one HashSet and one List on the room and refills them, so
    ///      the next read of the property invalidates the list you were handed. It
    ///      is therefore COPIED into a local list the instant it is read, and every
    ///      later question (beds, contents, boundary) is asked of the copy. Note
    ///      that `Room.ContainedBeds` and `Room.Owners` both re-read the property
    ///      lazily while yielding, which is exactly the interleave that would bite;
    ///      `Owners` is read to completion before any other read, and the beds come
    ///      off the local copy rather than out of `ContainedBeds`.
    ///   2. **`Room.Regions` returns a cached `tmpRegions` list on the room.**
    ///      `ExtentsClose` and `ContainedAndAdjacentThings` both consume it fully
    ///      before returning, so they are safe one at a time — but nothing here may
    ///      hold an `ExtentsClose` enumerator open across another read, and nothing
    ///      does.
    ///   3. **`Zone.Cells` (capital C) SHUFFLES the zone's cell list in place.**
    ///      `home/list_zones` records that too. This tool never touches it: the
    ///      stockpile answer comes from `ZoneManager.ZoneAt(cell)` walking the
    ///      LISTED ROOM's cells, which is the authoritative zone grid rather than
    ///      the zone's own list, and the two are known to disagree.
    ///   4. **`Room.Role`, `GetRoomRoleLabel()` and `GetStat()` fill a lazy
    ///      cache.** All three call `UpdateRoomStatsAndRole()` when
    ///      `statsAndRoleDirty`, which writes `stats` and `role` on the room. That
    ///      is the game's own memoisation — the same thing that happens the moment
    ///      the player's cursor enters the room — not a change to the colony, and
    ///      `home/get_temperatures` has read `Role` since it was written. Nothing
    ///      here calls `Notify_RoomShapeChanged` (which DOES mutate: it invokes
    ///      `autoBuildRoofAreaSetter.TryGenerateAreaFor`) or any setter.
    ///
    /// ## What comes back null, and what is omitted
    ///
    /// A read that throws becomes `null` on the row and its name is added to that
    /// room's `skipped[]`, so "the getter failed" is never dressed as "there is
    /// none". A room whose whole row could not be built still appears, with
    /// `skipped` naming what went wrong. Rooms that are not listed at all are
    /// COUNTED in the summary (`roomsOmitted`, `outdoorRoomsOmitted`,
    /// `doorwaysOmitted`, `dereferencedRoomsOmitted`) and, for the big ones,
    /// named in `omitted[]` — a filter that does not state itself is the failure
    /// this whole companion is written against.
    /// </summary>
    public sealed class HomeRoomTools
    {
        private const string ToolName = "home/list_rooms";

        /// <summary>Distinct building defs listed per room. A room with more
        /// kinds of thing in it than this reports the remainder as a count rather
        /// than growing the payload without limit.</summary>
        private const int MaxContentRows = 60;

        /// <summary>Safety ceiling on `cells[]` per room. It exists for the case
        /// somebody asks `cells:true` together with `includeOutdoors:true` on a
        /// 49,000-cell outdoors room. When it bites, `cellsNotListed` carries the
        /// remainder and `cellsComplete` is false: cellCount == cells.length +
        /// cellsNotListed always holds, and cellCount == cells.length holds
        /// whenever cellsComplete is true.</summary>
        private const int MaxCellsPerRoom = 20000;

        [Tool(
            ToolName,
            Title = "Every room on the map, with contents, occupants and stockpiles",
            Description =
                "Read-only census of every room the game currently recognises, in ONE call over the whole map with no rectangle and "
                + "no cap. Per room: the game's own role label plus bed owner as a name (\"Bedroom (Lucas)\"), extents, centre, cell "
                + "count, temperature, the five room stats with the game's own quality word, owners, beds, buildings grouped by def, "
                + "the pawns standing in it and the stockpile zones that overlap it. The outdoors mega-room and doorways are counted "
                + "but not listed unless includeOutdoors is set. Pass x,z alone to ask which room covers one cell; pass x,z,width,"
                + "height to add a row-major roomGrid over that rectangle, in the same shape home/get_temperatures emits, so a "
                + "renderer can paint it.",
            ResultDescription =
                "success, tool, mapName, roomCountTotal, roomCount (listed), roomsOmitted with a breakdown, rooms[], omitted[], "
                + "then cell{} when x,z was asked without a rectangle, and rect{} + roomGrid[][] when a rectangle was asked. "
                + "notes explains every flag and every null.")]
        [ToolResponse("rooms", "array", "One row per LISTED room: index, id, name, gameLabel, role, roleLabel, extents, center, cellCount, properRoom, psychologicallyOutdoors, outdoors, fogged, isDoorway, touchesMapEdge, openRoofCount, temperature, stats, owners[], beds[], contents[], pawns[], stockpiles[], skipped[]. Empty array = this map has no room that passes the filter, not that the read failed.", Always = true)]
        [ToolResponse("omitted", "array", "One row per room that exists but was not listed (the outdoors mega-room, doorways, dereferenced rooms), with id, name, cellCount and the reason. Always present so the filter states itself.", Always = true)]
        [ToolResponse("roomGrid", "array", "Only when a rectangle was asked (width and height both > 0): row-major rows of indexes into rooms[], null where the cell has no room OR its room was not listed. roomGrid[0] is the row at rect.z.", Nullable = true)]
        [ToolResponse("cell", "object", "Only when x and z were given without a rectangle: which room covers that cell. Carries found (bool), x, z and roomIndex - the index into rooms[], or null when the cell has no room or its room was not listed.", Nullable = true)]
        [ToolResponse("unknownArguments", "array", "Every argument key the caller sent that this tool does not declare, sorted, case-sensitively. Empty array = every key was recognised. The host's own _rimBridgeTimeoutMs is never listed.", Always = true)]
        [ToolResponse("unknownArgumentsWarning", "string", "Present only when unknownArguments is non-empty, or when the caller's raw keys could not be read at all - in which case the empty unknownArguments means 'not known', not 'nothing unknown'.", Nullable = true)]
        public async Task<object> ListRooms(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            [ToolParameter(Description = "Cell x. Alone with z: answer which room covers that cell. With width and height: the left edge of the rectangle a roomGrid is built over. -1 = not asked.", DefaultValue = -1)] int x = -1,
            [ToolParameter(Description = "Cell z. See x. -1 = not asked.", DefaultValue = -1)] int z = -1,
            [ToolParameter(Description = "Rectangle width in cells. Both width and height must be > 0 to get a roomGrid; 0 means no rectangle was asked for.", DefaultValue = 0)] int width = 0,
            [ToolParameter(Description = "Rectangle height in cells. See width.", DefaultValue = 0)] int height = 0,
            [ToolParameter(Description = "List every cell of every listed room under 'cells'. Off by default: the outdoors room alone is roughly 49000 cells. cellCount is exact either way.", DefaultValue = false)] bool cells = false,
            [ToolParameter(Description = "Also list the outdoors mega-room and doorway rooms. Off by default; they are always COUNTED in roomsOmitted and named in omitted[] whichever way this is set.", DefaultValue = false)] bool includeOutdoors = false,
            [ToolParameter(Description = "Include the room's bounding walls and adjacent things in contents[]. Off by default: Room.ContainedAndAdjacentThings includes the walls and doors around the room, which are not furniture in it.", DefaultValue = false)] bool includeBoundary = false)
        {
            return BridgeCommon.WithUnknownArguments(
                await ListRoomsCore(ctx, cancellationToken, x, z, width, height, cells, includeOutdoors, includeBoundary)
                    .ConfigureAwait(false),
                ctx, typeof(HomeRoomTools), ToolName);
        }

        private async Task<object> ListRoomsCore(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            int x,
            int z,
            int width,
            int height,
            bool cells,
            bool includeOutdoors,
            bool includeBoundary)
        {
            if (ctx?.MainThread == null)
                return Failure("No RimBridge main-thread dispatcher is available for this invocation.");

            // Companion tools are dispatched with MarshalToMainThread = false
            // (AnnotatedExtensionCapabilityProvider.InvokeAsync), so the region
            // grid, the room stat workers, the zone grid and mapPawns all have to
            // be read on RimWorld's own thread. The WHOLE census goes inside ONE
            // hop: rooms are destroyed and remade when a wall changes, so a census
            // smeared over several ticks could name a room in rooms[] that no
            // longer exists by the time roomGrid is built.
            return await ctx.MainThread
                .InvokeAsync(() => Build(x, z, width, height, cells, includeOutdoors, includeBoundary),
                             cancellationToken)
                .ConfigureAwait(false);
        }

        // ------------------------------------------------------------------
        // the census
        // ------------------------------------------------------------------

        private static object Build(int x, int z, int width, int height,
                                    bool wantCells, bool includeOutdoors, bool includeBoundary)
        {
            if (!TryGetMap(out var map, out var mapError))
                return Failure(mapError);

            var wantRect = width > 0 && height > 0;
            var wantCell = !wantRect && x >= 0 && z >= 0;

            if (wantRect && (x < 0 || z < 0))
                return Failure("A rectangle needs x and z as its bottom-left corner; both must be >= 0.");
            if ((width > 0) != (height > 0))
                return Failure("width and height must be given together: both > 0 for a rectangle, or neither.");

            IReadOnlyList<Room> allRooms;
            try
            {
                var regionGrid = map.regionGrid;
                allRooms = regionGrid == null ? null : regionGrid.AllRooms;
            }
            catch (Exception e)
            {
                return Failure("Could not read map.regionGrid.AllRooms: " + e.Message);
            }

            if (allRooms == null)
                return Failure("This map has no region grid, so it has no rooms to list.");

            // One pass over RimWorld's own spawned-pawn list, bucketed by the room
            // each pawn is standing in. O(pawns), never a cell sweep — the lesson
            // home/list_pawns was built on.
            var pawnsByRoomId = BuildPawnIndex(map, out var pawnIndexFailed);

            // --- decide what is listed, in a stable reading order -------------
            var listed = new List<Room>();
            var omitted = new List<object>();
            var outdoorsOmitted = 0;
            var doorwaysOmitted = 0;
            var dereferencedOmitted = 0;

            var ordered = new List<Room>(allRooms.Count);
            for (var i = 0; i < allRooms.Count; i++)
            {
                if (allRooms[i] != null)
                    ordered.Add(allRooms[i]);
            }
            // Top-left first, the order the ASCII map prints in: z descending,
            // then x ascending. Deterministic, so two calls over an unchanged map
            // hand out the same key characters.
            ordered.Sort(CompareByReadingOrder);

            foreach (var room in ordered)
            {
                var dereferenced = BridgeCommon.Try(() => room.RegionCount == 0, false);
                var doorway = BridgeCommon.Try(() => room.IsDoorway, false);
                var outdoors = BridgeCommon.Try(() => room.PsychologicallyOutdoors, false);

                string reason = null;
                if (dereferenced)
                {
                    reason = "dereferenced: the room has no regions left, so it is a corpse of a room the game has not collected yet";
                    dereferencedOmitted++;
                }
                else if (!includeOutdoors && outdoors)
                {
                    reason = "psychologicallyOutdoors: the open-air mega-room. Pass includeOutdoors:true to list it.";
                    outdoorsOmitted++;
                }
                else if (!includeOutdoors && doorway)
                {
                    reason = "isDoorway: a one-tile room the game makes for a door. Pass includeOutdoors:true to list it.";
                    doorwaysOmitted++;
                }

                if (reason == null)
                {
                    listed.Add(room);
                    continue;
                }

                omitted.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    ["id"] = RoomWalk.RoomId(room),
                    ["name"] = SafeName(room, null),
                    ["cellCount"] = BridgeCommon.Try<object>(() => room.CellCount, null),
                    ["reason"] = reason
                });
            }

            // --- build one row per listed room --------------------------------
            var indexByRoomId = new Dictionary<int, int>();
            var rows = new List<object>(listed.Count);
            for (var i = 0; i < listed.Count; i++)
            {
                var room = listed[i];
                var id = RoomWalk.RoomId(room);
                if (id != null && !indexByRoomId.ContainsKey(id.Value))
                    indexByRoomId[id.Value] = i;

                rows.Add(RoomRow(map, room, i, id, wantCells, includeBoundary, pawnsByRoomId));
            }

            var payload = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["success"] = true,
                ["tool"] = ToolName,
                ["mapName"] = BridgeCommon.SafeString(() => map.Parent == null ? null : map.Parent.Label),
                ["roomCountTotal"] = ordered.Count,
                ["roomCount"] = listed.Count,
                ["roomsOmitted"] = omitted.Count,
                ["outdoorRoomsOmitted"] = outdoorsOmitted,
                ["doorwaysOmitted"] = doorwaysOmitted,
                ["dereferencedRoomsOmitted"] = dereferencedOmitted,
                ["includeOutdoors"] = includeOutdoors,
                ["includeBoundary"] = includeBoundary,
                ["cellsListed"] = wantCells,
                ["unit"] = "C",
                ["rooms"] = rows,
                ["omitted"] = omitted
            };

            if (pawnIndexFailed != null)
                payload["pawnIndexWarning"] = pawnIndexFailed;

            // --- the cell answer ----------------------------------------------
            if (wantCell)
                payload["cell"] = CellAnswer(map, x, z, indexByRoomId);

            // --- the rect's roomGrid ------------------------------------------
            if (wantRect)
                AddRoomGrid(payload, map, x, z, width, height, indexByRoomId);

            payload["notes"] = Notes(wantRect, wantCell);
            return payload;
        }

        /// <summary>Reading order: top row first (z descending), then left to
        /// right. A room whose extents cannot be read sorts last rather than
        /// throwing out of the comparison.</summary>
        private static int CompareByReadingOrder(Room a, Room b)
        {
            var ea = SafeExtents(a);
            var eb = SafeExtents(b);
            if (ea == null && eb == null) return 0;
            if (ea == null) return 1;
            if (eb == null) return -1;

            var za = ea.Value.maxZ;
            var zb = eb.Value.maxZ;
            if (za != zb) return zb.CompareTo(za);          // higher z first
            var xa = ea.Value.minX;
            var xb = eb.Value.minX;
            if (xa != xb) return xa.CompareTo(xb);          // then left to right
            var ia = RoomWalk.RoomId(a) ?? int.MaxValue;
            var ib = RoomWalk.RoomId(b) ?? int.MaxValue;
            return ia.CompareTo(ib);                        // stable tiebreak
        }

        // ------------------------------------------------------------------
        // one room's row
        // ------------------------------------------------------------------

        private static object RoomRow(Map map, Room room, int index, int? id,
                                      bool wantCells, bool includeBoundary,
                                      Dictionary<int, List<object>> pawnsByRoomId)
        {
            var skipped = new List<string>();

            // The cell walk: ONE pass over Room.Cells, feeding the centroid, the
            // stockpile intersection and the optional cells[]. Room.Cells returns
            // a fresh iterator per read (districts -> regions -> a bounded scan of
            // each region's extents), so it is walked once and never twice.
            var walk = WalkRoomCells(map, room, wantCells, skipped);

            var extents = SafeExtents(room);
            if (extents == null)
                skipped.Add("extents (Room.ExtentsClose threw or the room has no regions)");

            // ONE read of the cached buffer, copied immediately. See hazard 1.
            var contained = SnapshotContainedThings(room, skipped);

            var owners = SafeOwners(room, skipped);
            var roleLabel = SafeRoleLabel(room);

            var row = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["index"] = index,
                ["id"] = id,
                ["name"] = SafeName(room, owners),
                ["gameLabel"] = BridgeCommon.SafeString(() => room.GetRoomRoleLabel()),
                ["role"] = BridgeCommon.SafeString(() => room.Role == null ? null : room.Role.defName),
                ["roleLabel"] = roleLabel,
                ["extents"] = extents == null ? null : new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    ["x"] = extents.Value.minX,
                    ["z"] = extents.Value.minZ,
                    ["width"] = extents.Value.Width,
                    ["height"] = extents.Value.Height
                },
                ["center"] = walk.Center == null ? null : BridgeCommon.Pos(walk.Center.Value),
                ["cellCount"] = BridgeCommon.Try<object>(() => room.CellCount, null),
                ["cellsWalked"] = walk.Walked,
                ["properRoom"] = BridgeCommon.Try<object>(() => room.ProperRoom, null),
                ["psychologicallyOutdoors"] = BridgeCommon.Try<object>(() => room.PsychologicallyOutdoors, null),
                ["outdoors"] = BridgeCommon.Try<object>(() => room.UsesOutdoorTemperature, null),
                ["fogged"] = BridgeCommon.Try<object>(() => room.Fogged, null),
                ["isDoorway"] = BridgeCommon.Try<object>(() => room.IsDoorway, null),
                ["doorDef"] = BridgeCommon.SafeString(() =>
                {
                    var door = room.Door;
                    return door == null || door.def == null ? null : door.def.defName;
                }),
                ["touchesMapEdge"] = BridgeCommon.Try<object>(() => room.TouchesMapEdge, null),
                ["openRoofCount"] = BridgeCommon.Try<object>(() => room.OpenRoofCount, null),
                ["temperature"] = BridgeCommon.Try<object>(() => Round(room.Temperature), null),
                ["stats"] = Stats(room, skipped),
                ["owners"] = owners ?? new List<string>(),
                ["ownersRead"] = owners != null
            };

            AddBeds(row, contained, room, includeBoundary, skipped);
            AddContents(row, contained, room, includeBoundary, skipped);

            List<object> pawns = null;
            if (id != null)
                pawnsByRoomId.TryGetValue(id.Value, out pawns);
            row["pawns"] = pawns ?? new List<object>();
            row["pawnCount"] = (pawns ?? new List<object>()).Count;

            row["stockpiles"] = walk.Stockpiles;
            row["stockpileCellsInRoom"] = walk.StockpileCellsTotal;

            if (wantCells)
            {
                row["cells"] = walk.Cells ?? new List<object>();
                row["cellsNotListed"] = walk.CellsNotListed;
                // cellCount == cells.length + cellsNotListed, always. When this is
                // true the stronger cellCount == cells.length holds too.
                row["cellsComplete"] = walk.CellsNotListed == 0 && walk.CellsFailed == null;
            }

            if (walk.CellsFailed != null)
                skipped.Add(walk.CellsFailed);

            row["skipped"] = skipped;
            return row;
        }

        // ------------------------------------------------------------------
        // the cell walk
        // ------------------------------------------------------------------

        private sealed class CellWalk
        {
            internal int Walked;
            internal IntVec3? Center;
            internal List<object> Cells;
            internal int CellsNotListed;
            internal string CellsFailed;
            internal List<object> Stockpiles = new List<object>();
            internal int StockpileCellsTotal;
        }

        /// <summary>
        /// One pass over the room's own cells. Produces the centroid snapped to a
        /// real cell (an L-shaped room must not label itself in the notch), the
        /// stockpile zones the room overlaps and how many cells of each, and the
        /// optional cells[] listing.
        ///
        /// The stockpile answer reads `ZoneManager.ZoneAt(cell)` — the zone GRID —
        /// rather than `Zone.cells`. The two are known to disagree (that is what
        /// home/list_zones exists to report) and the grid is what `ZoneAt` and the
        /// renderer follow. `Zone.Cells` with a capital C is never touched: its
        /// getter shuffles the list in place.
        /// </summary>
        private static CellWalk WalkRoomCells(Map map, Room room, bool wantCells, List<string> skipped)
        {
            var result = new CellWalk();
            var zoneCells = new Dictionary<Zone, int>();
            var order = new List<Zone>();
            long sumX = 0, sumZ = 0;
            var snapshot = new List<IntVec3>();

            ZoneManager zones = null;
            try { zones = map.zoneManager; }
            catch { skipped.Add("stockpiles (map.zoneManager could not be read)"); }

            try
            {
                foreach (var cell in room.Cells)
                {
                    result.Walked++;
                    sumX += cell.x;
                    sumZ += cell.z;
                    snapshot.Add(cell);

                    if (wantCells)
                    {
                        if (result.Cells == null)
                            result.Cells = new List<object>();
                        if (result.Cells.Count < MaxCellsPerRoom)
                            result.Cells.Add(BridgeCommon.Pos(cell));
                        else
                            result.CellsNotListed++;
                    }

                    if (zones == null)
                        continue;
                    Zone zone;
                    try { zone = zones.ZoneAt(cell); }
                    catch { continue; }
                    if (!(zone is Zone_Stockpile))
                        continue;
                    int had;
                    if (!zoneCells.TryGetValue(zone, out had))
                        order.Add(zone);
                    zoneCells[zone] = had + 1;
                    result.StockpileCellsTotal++;
                }
            }
            catch (Exception e)
            {
                result.CellsFailed =
                    "cells (Room.Cells threw " + e.GetType().Name + " after " + result.Walked
                    + " cells; every count from the cell walk is a floor, not a total)";
            }

            if (snapshot.Count > 0)
                result.Center = SnapCenter(snapshot, sumX, sumZ);

            foreach (var zone in order)
            {
                result.Stockpiles.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    ["label"] = BridgeCommon.SafeString(() => zone.label),
                    ["id"] = BridgeCommon.Try<object>(() => zone.ID, null),
                    ["cellsInRoom"] = zoneCells[zone],
                    ["zoneCellCount"] = BridgeCommon.Try<object>(() => zone.cells == null ? 0 : zone.cells.Count, null)
                });
            }

            return result;
        }

        /// <summary>Centroid of the room's cells, snapped to the nearest cell that
        /// is actually in the room. Same rule the temperature tool's legend anchor
        /// uses, for the same reason.</summary>
        private static IntVec3 SnapCenter(List<IntVec3> cells, long sumX, long sumZ)
        {
            var centroidX = (int)Math.Round((double)sumX / cells.Count, MidpointRounding.AwayFromZero);
            var centroidZ = (int)Math.Round((double)sumZ / cells.Count, MidpointRounding.AwayFromZero);

            var best = cells[0];
            var bestScore = long.MaxValue;
            for (var i = 0; i < cells.Count; i++)
            {
                long dx = cells[i].x - centroidX;
                long dz = cells[i].z - centroidZ;
                var score = dx * dx + dz * dz;
                if (score == 0)
                    return cells[i];
                if (score < bestScore)
                {
                    bestScore = score;
                    best = cells[i];
                }
            }
            return best;
        }

        // ------------------------------------------------------------------
        // contents, beds, owners, stats
        // ------------------------------------------------------------------

        /// <summary>The cached buffer, copied on the spot. Never hand the property
        /// itself anywhere: the next read of it clears the list.</summary>
        private static List<Thing> SnapshotContainedThings(Room room, List<string> skipped)
        {
            try
            {
                var things = room.ContainedAndAdjacentThings;
                if (things == null)
                {
                    skipped.Add("contents (Room.ContainedAndAdjacentThings was null)");
                    return null;
                }
                var copy = new List<Thing>(things.Count);
                for (var i = 0; i < things.Count; i++)
                    copy.Add(things[i]);
                return copy;
            }
            catch (Exception e)
            {
                skipped.Add("contents (Room.ContainedAndAdjacentThings threw " + e.GetType().Name + ")");
                return null;
            }
        }

        /// <summary>True when the thing actually stands inside this room, as
        /// opposed to being one of the walls or doors around it.
        /// `Room.ContainsCell` is `cell.GetRoom(Map) == this`, a region-grid
        /// lookup, so this is cheap per thing.</summary>
        private static bool InRoom(Room room, Thing thing)
        {
            try { return room.ContainsCell(thing.Position); }
            catch { return false; }
        }

        private static void AddBeds(IDictionary<string, object> row, List<Thing> contained,
                                    Room room, bool includeBoundary, List<string> skipped)
        {
            var beds = new List<object>();
            if (contained == null)
            {
                row["beds"] = beds;
                row["bedCount"] = null;      // not zero: it was never counted
                return;
            }

            for (var i = 0; i < contained.Count; i++)
            {
                var bed = contained[i] as Building_Bed;
                if (bed == null)
                    continue;
                if (!includeBoundary && !InRoom(room, bed))
                    continue;

                var owners = new List<string>();
                try
                {
                    var assigned = bed.OwnersForReading;
                    if (assigned != null)
                    {
                        for (var j = 0; j < assigned.Count; j++)
                        {
                            var name = PawnName(assigned[j]);
                            if (!string.IsNullOrEmpty(name))
                                owners.Add(name);
                        }
                    }
                }
                catch
                {
                    skipped.Add("bed owners (Building_Bed.OwnersForReading threw)");
                }

                beds.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    ["defName"] = BridgeCommon.SafeString(() => bed.def == null ? null : bed.def.defName),
                    ["label"] = BridgeCommon.SafeString(() => bed.def == null ? null : bed.def.label),
                    ["position"] = BridgeCommon.PositionOf(bed),
                    ["owners"] = owners,
                    ["medical"] = BridgeCommon.Try<object>(() => bed.Medical, null),
                    ["forPrisoners"] = BridgeCommon.Try<object>(() => bed.ForPrisoners, null)
                });
            }

            row["beds"] = beds;
            row["bedCount"] = beds.Count;
        }

        private static void AddContents(IDictionary<string, object> row, List<Thing> contained,
                                        Room room, bool includeBoundary, List<string> skipped)
        {
            if (contained == null)
            {
                row["contents"] = new List<object>();
                row["contentsNotListed"] = null;
                row["looseThingCount"] = null;
                row["boundaryThingsExcluded"] = null;
                return;
            }

            var counts = new Dictionary<string, int>(StringComparer.Ordinal);
            var labels = new Dictionary<string, string>(StringComparer.Ordinal);
            var order = new List<string>();
            var loose = 0;
            var boundary = 0;

            for (var i = 0; i < contained.Count; i++)
            {
                var thing = contained[i];
                if (thing == null)
                    continue;
                if (thing is Pawn)
                    continue;                       // pawns[] answers that question

                var inside = InRoom(room, thing);
                if (!inside)
                {
                    boundary++;
                    if (!includeBoundary)
                        continue;
                }

                if (!(thing is Building))
                {
                    loose++;
                    continue;
                }

                var defName = BridgeCommon.SafeString(() => thing.def == null ? null : thing.def.defName) ?? "(unnamed def)";
                int had;
                if (!counts.TryGetValue(defName, out had))
                {
                    order.Add(defName);
                    labels[defName] = BridgeCommon.SafeString(() => thing.def == null ? null : thing.def.label);
                }
                counts[defName] = had + 1;
            }

            // Most of a thing first: a room's identity is its stove and its beds,
            // not the fourteenth floor light.
            order.Sort((a, b) =>
            {
                var byCount = counts[b].CompareTo(counts[a]);
                return byCount != 0 ? byCount : string.CompareOrdinal(a, b);
            });

            var contents = new List<object>();
            var notListed = 0;
            for (var i = 0; i < order.Count; i++)
            {
                if (contents.Count >= MaxContentRows)
                {
                    notListed++;
                    continue;
                }
                var defName = order[i];
                contents.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    ["defName"] = defName,
                    ["label"] = labels[defName],
                    ["count"] = counts[defName]
                });
            }

            row["contents"] = contents;
            row["contentsNotListed"] = notListed;
            row["looseThingCount"] = loose;
            row["boundaryThingsExcluded"] = includeBoundary ? 0 : boundary;
        }

        /// <summary>`Room.Owners` yields the pawns whose bed makes this their room.
        /// It is read to COMPLETION here before anything else touches the room,
        /// because its enumerator re-reads ContainedAndAdjacentThings lazily.
        /// Null (not empty) means the read failed.</summary>
        private static List<string> SafeOwners(Room room, List<string> skipped)
        {
            try
            {
                var names = new List<string>();
                foreach (var pawn in room.Owners)
                {
                    var name = PawnName(pawn);
                    if (!string.IsNullOrEmpty(name))
                        names.Add(name);
                }
                return names;
            }
            catch (Exception e)
            {
                skipped.Add("owners (Room.Owners threw " + e.GetType().Name + ")");
                return null;
            }
        }

        /// <summary>The five stats M named, each with the game's own quality
        /// word for that score ("Impressive", "Decent"). `RoomStatDef.GetScoreStage`
        /// throws when a def carries no score stages, so that is caught per stat
        /// rather than losing the whole block.</summary>
        private static object Stats(Room room, List<string> skipped)
        {
            var stats = new Dictionary<string, object>(StringComparer.Ordinal);
            AddStat(stats, room, "cleanliness", TryStatDef(() => RoomStatDefOf.Cleanliness), skipped);
            AddStat(stats, room, "wealth", TryStatDef(() => RoomStatDefOf.Wealth), skipped);
            AddStat(stats, room, "space", TryStatDef(() => RoomStatDefOf.Space), skipped);
            AddStat(stats, room, "beauty", TryStatDef(() => RoomStatDefOf.Beauty), skipped);
            AddStat(stats, room, "impressiveness", TryStatDef(() => RoomStatDefOf.Impressiveness), skipped);
            return stats;
        }

        private static RoomStatDef TryStatDef(Func<RoomStatDef> read)
        {
            try { return read(); }
            catch { return null; }
        }

        private static void AddStat(IDictionary<string, object> stats, Room room, string key,
                                    RoomStatDef def, List<string> skipped)
        {
            if (def == null)
            {
                stats[key] = null;
                skipped.Add("stat " + key + " (RoomStatDefOf." + key + " was not resolvable)");
                return;
            }

            float score;
            try { score = room.GetStat(def); }
            catch (Exception e)
            {
                stats[key] = null;
                skipped.Add("stat " + key + " (Room.GetStat threw " + e.GetType().Name + ")");
                return;
            }

            string label = null;
            try
            {
                var stage = def.GetScoreStage(score);
                label = stage == null ? null : stage.label;
            }
            catch
            {
                // A def with no score stages has no word for its score. The number
                // is still true, and `label: null` says the word is missing rather
                // than inventing one.
            }

            stats[key] = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["value"] = Math.Round((double)score, 2, MidpointRounding.AwayFromZero),
                ["label"] = label,
                ["display"] = BridgeCommon.SafeString(() => def.ScoreToString(score))
            };
        }

        /// <summary>
        /// "Bedroom (Lucas)". The role label is the game's own
        /// `RoomRoleDef.PostProcessedLabelCap`, and the owners are whoever the beds
        /// inside assign it to — which is the only naming a room has, because a
        /// player cannot name one. `gameLabel` on the row carries RimWorld's own
        /// phrasing of the same thing ("Lucas' bedroom") for anyone who wants it.
        /// </summary>
        private static string SafeName(Room room, List<string> owners)
        {
            var role = SafeRoleLabel(room);
            if (string.IsNullOrEmpty(role))
                role = "Room";

            if (owners == null)
            {
                owners = BridgeCommon.Try(() =>
                {
                    var names = new List<string>();
                    foreach (var pawn in room.Owners)
                    {
                        var name = PawnName(pawn);
                        if (!string.IsNullOrEmpty(name))
                            names.Add(name);
                    }
                    return names;
                }, null);
            }

            if (owners == null || owners.Count == 0)
                return role;
            return role + " (" + string.Join(", ", owners.ToArray()) + ")";
        }

        private static string SafeRoleLabel(Room room)
        {
            return BridgeCommon.SafeString(() =>
            {
                var role = room.Role;
                if (role == null)
                    return null;
                var label = role.PostProcessedLabelCap(room);
                return string.IsNullOrEmpty(label) ? role.defName : label;
            });
        }

        // ------------------------------------------------------------------
        // pawns
        // ------------------------------------------------------------------

        private static Dictionary<int, List<object>> BuildPawnIndex(Map map, out string failure)
        {
            failure = null;
            var byRoom = new Dictionary<int, List<object>>();
            try
            {
                var spawned = map.mapPawns == null ? null : map.mapPawns.AllPawnsSpawned;
                if (spawned == null)
                {
                    failure = "map.mapPawns.AllPawnsSpawned was null, so every room's pawns[] is empty because it was never read, not because the room is empty.";
                    return byRoom;
                }

                for (var i = 0; i < spawned.Count; i++)
                {
                    var pawn = spawned[i];
                    if (pawn == null)
                        continue;

                    var room = BridgeCommon.Try(() => RoomWalk.RoomAt(map, pawn.Position), null);
                    if (room == null)
                        continue;
                    var id = RoomWalk.RoomId(room);
                    if (id == null)
                        continue;

                    List<object> list;
                    if (!byRoom.TryGetValue(id.Value, out list))
                    {
                        list = new List<object>();
                        byRoom[id.Value] = list;
                    }

                    list.Add(new Dictionary<string, object>(StringComparer.Ordinal)
                    {
                        ["name"] = PawnName(pawn),
                        ["isColonist"] = BridgeCommon.Try<object>(() => pawn.IsColonist, null),
                        ["position"] = BridgeCommon.PositionOf(pawn)
                    });
                }
            }
            catch (Exception e)
            {
                failure = "The pawn sweep threw " + e.GetType().Name
                          + ", so pawns[] is a floor rather than a total on every room.";
            }
            return byRoom;
        }

        private static string PawnName(Pawn pawn)
        {
            if (pawn == null)
                return null;
            try { return pawn.LabelShortCap.ToString(); }
            catch
            {
                try { return pawn.LabelCap.ToString(); }
                catch { return null; }
            }
        }

        // ------------------------------------------------------------------
        // the cell answer and the rect grid
        // ------------------------------------------------------------------

        private static object CellAnswer(Map map, int x, int z, Dictionary<int, int> indexByRoomId)
        {
            var cell = new IntVec3(x, 0, z);
            var inBounds = BridgeCommon.Try(() => cell.InBounds(map), false);
            var answer = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["x"] = x,
                ["z"] = z,
                ["inBounds"] = inBounds
            };

            if (!inBounds)
            {
                answer["found"] = false;
                answer["roomIndex"] = null;
                answer["why"] = "That cell is outside the current map.";
                return answer;
            }

            var room = RoomWalk.RoomAt(map, cell);
            var id = room == null ? null : RoomWalk.RoomId(room);
            answer["roomId"] = id;

            int index;
            if (id != null && indexByRoomId.TryGetValue(id.Value, out index))
            {
                answer["found"] = true;
                answer["roomIndex"] = index;
                answer["why"] = null;
            }
            else
            {
                answer["found"] = false;
                answer["roomIndex"] = null;
                answer["why"] = room == null
                    ? "That cell is in no room at all — a wall, deep rock, or an unregioned cell."
                    : "That cell's room exists but was not listed (the outdoors mega-room, a doorway, or a dereferenced room). Pass includeOutdoors:true to list it.";
            }

            return answer;
        }

        private static void AddRoomGrid(IDictionary<string, object> payload, Map map,
                                        int x, int z, int width, int height,
                                        Dictionary<int, int> indexByRoomId)
        {
            payload["rect"] = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["x"] = x,
                ["z"] = z,
                ["width"] = width,
                ["height"] = height
            };

            // The same walk home/get_temperatures {mode:rooms} makes, so a caller
            // can compare the two calls cell for cell. The values are remapped from
            // the walk's own rect-local index onto the index into THIS reply's
            // rooms[], which is the whole-map census — so a cell whose room was
            // filtered out reads null, and every non-null value is a valid index
            // into rooms[].
            var walk = RoomWalk.OverRect(map, x, z, width, height);
            var remap = new Dictionary<int, object>();
            var unlisted = 0;
            foreach (var entry in walk.Rooms)
            {
                int listedIndex;
                remap[entry.Index] = indexByRoomId.TryGetValue(entry.Id, out listedIndex)
                    ? (object)listedIndex
                    : null;
            }

            var rows = new List<List<object>>(walk.Grid.Count);
            var outOfBounds = 0;
            foreach (var sourceRow in walk.Grid)
            {
                var row = new List<object>(sourceRow.Count);
                foreach (var value in sourceRow)
                {
                    if (value == null)
                    {
                        row.Add(null);
                        continue;
                    }
                    var mapped = remap[(int)value];
                    if (mapped == null)
                        unlisted++;
                    row.Add(mapped);
                }
                rows.Add(row);
            }

            // Cells outside the map read as "no room" through GetRoom rather than
            // failing the call, which is deliberate — but a caller has to be able
            // to see that it asked for a rect off the edge of the world.
            try
            {
                for (var oz = 0; oz < height; oz++)
                    for (var ox = 0; ox < width; ox++)
                        if (!new IntVec3(x + ox, 0, z + oz).InBounds(map))
                            outOfBounds++;
            }
            catch
            {
                outOfBounds = -1;
            }

            payload["roomGrid"] = rows;
            payload["gridCellsWithNoRoom"] = walk.CellsWithNoRoom;
            payload["gridCellsInUnlistedRooms"] = unlisted;
            payload["gridCellsOutOfBounds"] = outOfBounds;
            payload["gridRoomsInRect"] = walk.Rooms.Count;
        }

        // ------------------------------------------------------------------
        // notes
        // ------------------------------------------------------------------

        private static object Notes(bool wantRect, bool wantCell)
        {
            var notes = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["naming"] = "Rooms have no player-given name. `name` is the game's own role label plus the owners any bed inside assigns it, recomputed on every call — \"Bedroom (Lucas)\". `gameLabel` is RimWorld's own phrasing of the same thing. `role` is the RoomRoleDef defName.",
                ["ids"] = "`id` is Room.ID, a counter handed out by the game. It is NOT stable across a wall change: RimWorld destroys and remakes a room whenever its shape changes, so an id recorded in a file will point at nothing. `index` is response-local: it is the index into rooms[] for THIS reply and means nothing in the next one.",
                ["order"] = "rooms[] is sorted top-left first — highest extents.maxZ, then lowest extents.minX — so the indexes read across a printed map in reading order.",
                ["omitted"] = "By default the outdoors mega-room (psychologicallyOutdoors), doorway rooms and dereferenced rooms are counted but not listed. roomsOmitted plus its three breakdown counts and omitted[] say exactly what was dropped. includeOutdoors:true lists them.",
                ["outdoorsFlag"] = "outdoors = Room.UsesOutdoorTemperature, the flag that slaves the room's temperature to the weather. psychologicallyOutdoors = Room.PsychologicallyOutdoors, RimWorld's separate 'does it FEEL outdoors' mood flag. A large roofed hall can be one and not the other.",
                ["contents"] = "contents[] is BUILDINGS standing inside the room, grouped by def with a count, biggest group first. Room.ContainedAndAdjacentThings also returns the walls and doors AROUND the room; those are dropped and counted in boundaryThingsExcluded unless includeBoundary:true. Things that are not buildings are counted in looseThingCount, not listed — home/list_things and inv.py answer that question. contentsNotListed is how many further distinct defs were over the row cap.",
                ["stockpiles"] = "Stockpile zones whose cells overlap the room, with how many of the room's cells each covers. Read from the zone GRID (ZoneManager.ZoneAt), not from Zone.cells — the two are known to disagree, which is what home/list_zones exists to report. zoneCellCount is the zone's own list length, for comparison.",
                ["pawns"] = "Every SPAWNED pawn whose own cell resolves to this room — colonists, animals, visitors, raiders alike. isColonist separates them. A pawn standing in a doorway belongs to the doorway room, which is not listed by default.",
                ["stats"] = "The five room stats, each with the raw value, the game's own quality word for that score (`label`, from RoomStatDef.GetScoreStage) and the game's own formatting of it (`display`). A room that is not a proper room scores the def's roomlessScore.",
                ["nullMeans"] = "A null field means the game's getter threw and the reason is named in that room's skipped[]. It never means zero or absent. An empty rooms[] means no room passed the filter; the reply would have failed outright if the room list itself could not be read.",
                ["cost"] = "The room list itself is one property access (RegionGrid.AllRooms) with no rectangle and no cap. The per-room cell walk is what costs, which is why the ~49000-cell outdoors room is off by default and cells:true is opt-in."
            };

            notes["cellAnswer"] = wantCell
                ? "cell{} answers which listed room covers x,z. found:false with roomIndex null means the cell is in no room, or in a room this call did not list; `why` says which."
                : "Pass x and z (and no width/height) to ask which room covers one cell.";

            notes["grid"] = wantRect
                ? "roomGrid is row-major; roomGrid[0] is the row at rect.z and roomGrid[i][j] is cell (rect.x + j, rect.z + i). Values are indexes into rooms[] — this reply's whole-map census — or null when the cell has no room OR its room was not listed. gridCellsInUnlistedRooms counts the second kind. Every non-null value is a valid index into rooms[]."
                : "Pass x, z, width and height to add a roomGrid over that rectangle, in the same row-major shape home/get_temperatures {mode:rooms} emits.";

            return notes;
        }

        // ------------------------------------------------------------------
        // safe accessors
        // ------------------------------------------------------------------

        /// <summary>`Room.ExtentsClose` starts from an inverted CellRect and folds
        /// every region into it, so a room with no regions comes back with
        /// minX == int.MaxValue. That is not an extent; it is null.</summary>
        private static CellRect? SafeExtents(Room room)
        {
            try
            {
                var rect = room.ExtentsClose;
                if (rect.minX > rect.maxX || rect.minZ > rect.maxZ)
                    return null;
                return rect;
            }
            catch
            {
                return null;
            }
        }

        private static double Round(float celsius)
        {
            return Math.Round((double)celsius, 1, MidpointRounding.AwayFromZero);
        }

        /// <summary>The shared map gate; see BridgeCommon.TryGetMap.</summary>
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
