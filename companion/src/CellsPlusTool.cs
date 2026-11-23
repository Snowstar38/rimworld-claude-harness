using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Threading;
using System.Threading.Tasks;
using RimBridgeServer.Sdk;
using RimWorld;
using Verse;

namespace HomeBridge.BridgeTools
{
    /// <summary>
    /// home/get_cells_plus — same rectangle semantics as rimworld/get_cells_info,
    /// plus the two fields that provably are NOT in the stock payload:
    ///
    ///   * per-thing `forbidden`  (CompForbiddable.Forbidden — the same bit the
    ///     save file writes as &lt;forbidden&gt;True&lt;/forbidden&gt;)
    ///   * per-bed  `ownerNames` / `ownerName` (Building_Bed.OwnersForReading)
    ///
    /// Verified 2026-08-30: 846 cells rescanned after items were forbidden and
    /// zero bytes of get_cells_info's payload changed, while the save XML showed
    /// 19 forbidden flags. So this is an addition, not a re-derivation.
    ///
    /// It is also deliberately more compact than get_cells_info, because the
    /// measured cost of the map sweep is payload size, not latency:
    ///   - the three duplicated def-name arrays (blueprintBuildDefs,
    ///     frameBuildDefs, solidThingDefs) are dropped; things[] already has them
    ///   - zone and area descriptors are emitted ONCE in top-level dictionaries
    ///     and referenced per cell by id, instead of repeated per cell
    ///   - className uses the short type name rather than the full namespace
    ///   - fields that are null/false/empty are omitted
    ///
    /// The one field that is NEVER omitted for a thing that could carry it is
    /// `forbidden`: a silently-absent forbidden flag is the exact bug this tool
    /// exists to fix, so any thing with a CompForbiddable reports true OR false.
    /// Things with no CompForbiddable (plants, filth, terrain-likes) omit the key
    /// entirely — absence there means "cannot be forbidden", not "unknown".
    ///
    /// ============================ 2026-09-02 ============================
    /// Two defects, both of them the same mistake in two places — treating an
    /// ABSENT key as if it were a VALUE. Written out in full because the second
    /// one is the compaction rule above biting its own author.
    ///
    /// 1. ARGUMENT SHAPE — a silent zero.
    ///    This tool declared only {x, z, width, height}. RimBridge's binder
    ///    (upstream AnnotatedExtensionCapabilityProvider.BuildArguments) does
    ///    NOT reject argument names a tool has not declared: it looks up each
    ///    DECLARED parameter by name, and anything the caller sent that does not
    ///    match one is dropped without a word. A declared parameter that nothing
    ///    supplied is then filled with `Activator.CreateInstance(typeof(int))`,
    ///    i.e. zero.
    ///    So a caller sending the corner form {x0, z0, x1, z1} — which is what
    ///    callers here have actually been sending — got x = 0, z = 0, and the
    ///    declared defaults width = 1, height = 1: a perfectly successful 1x1
    ///    scan of cell (0, 0), the map's bottom-left corner, which is fogged and
    ///    empty. Every thing in the requested rectangle vanished and
    ///    `success` stayed true. That is precisely the silent zero rim.py.game()
    ///    was hardened against on Aug 29 for refusals — except a refusal at
    ///    least says success:false, and this said success:true.
    ///    Fix: declare all eight names, default every one of them to a sentinel
    ///    no map coordinate can be, and refuse — loudly, naming the shape that
    ///    was parsed AND the shapes that are accepted — whenever the arguments
    ///    do not describe exactly one rectangle of at least one cell. There is
    ///    no path left through this tool that produces an empty or degenerate
    ///    rect and calls it success.
    ///
    /// 2. `passable` / `walkable` NULL ON EVERY CELL.
    ///    `passable` was never emitted at all — the field simply did not exist,
    ///    and a reader asking for it got None forever. `walkable` was emitted
    ///    ONLY when false, under the omit-falsey compaction above, so on every
    ///    walkable cell (the large majority) the key was absent and a reader
    ///    doing cell.get("walkable") got None there too — with no way to tell
    ///    "this cell is walkable" from "this tool did not report it". map.py
    ///    papered over its half of that in normalise_plus(); nothing else did.
    ///    Fix: both are emitted on EVERY cell, always, as real booleans.
    ///      walkable = IntVec3.Walkable(map)   — the PATH grid. A wall, a filled
    ///                 cell, an impassable building all read false. This is the
    ///                 question "can a pawn stand a path through here".
    ///      passable = !IntVec3.Impassable(map) — terrain/edifice passability,
    ///                 the same question ZonesTool asks with
    ///                 GetEdifice().def.passability == Traversability.Impassable.
    ///    They are NOT the same question and both are reported: a cell can be
    ///    passable terrain and still unwalkable to the path grid (and the
    ///    reverse, over a passable-but-unstandable edifice). If RimWorld throws
    ///    while answering either, the field carries the STRING "unknown" — never
    ///    null, because null and absent are the same thing once this is JSON,
    ///    and that confusion is the whole subject of this comment.
    ///    Cost: roughly 30 KB per 1024-cell block on top of the measured 152 KB
    ///    (against 649 KB for the stock tool). A boolean that is actually there
    ///    is worth 20%. `fogged` is deliberately left omit-when-false: every
    ///    reader of it already treats absence as false and gets that right.
    /// ====================================================================
    ///
    /// ============================ 2026-09-03 ============================
    /// THREE OPT-IN NARROWINGS, on this one tool rather than three tools
    /// (WANTED 16: a 32x32 block measured 197.9 KB on day 39, against 697.8 KB
    /// for the stock reader, and "compress — field selection, or a summary
    /// mode"). Every one of them is OFF by default, so a caller that sends only
    /// a rectangle gets exactly the payload it got yesterday, byte for byte.
    ///
    ///   `fields`       — which per-cell keys to emit.
    ///   `thingFields`  — which per-thing keys to emit inside things[].
    ///   `sparse`       — drop the cells that carry none of the selected
    ///                    optional content.
    ///   `summary`      — do not emit cells[] at all; emit aggregates instead.
///                    The cap on the rectangle under it is the whole map and
///                    not 1024 cells: nothing is emitted per cell, so the
///                    reply is a few KB whatever the rectangle. `cellCap` and
///                    `capReason` on every reply say which cap applied.
    ///
    /// The rule the whole design turns on is the one this file already learned
    /// twice: A READER MUST BE ABLE TO TELL "DESELECTED" FROM "ABSENT". So
    ///   * `fieldsApplied[]` and `thingFieldsApplied[]` are on EVERY reply,
    ///     including the default one, naming exactly what was emitted. A key
    ///     missing from a cell is explained by that list or it is a bug.
    ///   * an unrecognised field name is REFUSED (success:false, naming the bad
    ///     name and listing the accepted ones). It is never ignored, because an
    ///     ignored name is a caller who thinks they narrowed and did not, or
    ///     thinks they asked for something and did not get it.
    ///   * `cellsOmitted` is on every reply, and
    ///     len(cells) + cellsOmitted == cellCount holds in all three modes.
    ///     `cellCount` stays the number of cells in the RECTANGLE, never the
    ///     number of entries in cells[] — a short answer that looks complete is
    ///     the failure this file exists against.
    ///
    /// Two judgement calls, written down because they are not derivable:
    ///
    ///   FOG IS NOT CONTENT. Under `sparse`, a fogged cell that holds nothing is
    ///   omitted like any other empty cell. `fogged` is a property OF the cell,
    ///   in the same class as terrain/roof/walkable/passable, not a thing lying
    ///   IN it; and a 32x32 block on the frontier is mostly fog, which is
    ///   exactly the payload sparse exists to remove. A fogged cell that holds a
    ///   thing or a designation is still returned, with its `fogged` flag, so
    ///   nothing that is actually there disappears. Callers that need the fog
    ///   SHAPE (map.py's layers all draw '?') must not use sparse — map.py does
    ///   not.
    ///
    ///   THE SUMMARY DE-DUPLICATES BY THING IDENTITY; cells[] DOES NOT. In
    ///   cells[] a 2x2 workbench appears in four cells, because the question
    ///   there is "what is on this cell". In summary.things it is counted ONCE,
    ///   because the question there is "what is in this rectangle". Designations
    ///   are de-duplicated the same way and for the same reason:
    ///   DesignationManager.AllDesignationsAt walks thingGrid, so a designation
    ///   on a multi-cell thing is genuinely reported at every one of its cells.
    /// ====================================================================
    /// </summary>
    public sealed class HomeMapTools
    {
        /// <summary>
        /// The cap on cells[] modes. It protects the payload, not the sweep: a
        /// full cell row is roughly 150 bytes and 1024 of them is already a
        /// 150 KB reply.
        /// </summary>
        private const int MaxCells = 1024;

        /// <summary>
        /// Under summary:true nothing is emitted per cell -- the reply is
        /// aggregates and stays a few KB whatever the rectangle -- so the cap
        /// becomes the map itself, applied in BuildResponse where map.Size is
        /// readable. This is only the arithmetic guard the argument parse can
        /// apply without a map: a span past it is out of bounds on any map
        /// RimWorld generates, and an in-bounds rectangle is never refused here.
        /// </summary>
        private const int MaxSummaryAxis = 100000;

        /// <summary>
        /// "This argument was not supplied." int.MinValue and not -1: an
        /// unclamped `centre - radius` really does send a negative coordinate
        /// (temps.py's rect_for() exists because of one, and its comment quotes
        /// the resulting `Cell (-3, 118) is out of bounds`). A negative
        /// coordinate has to come back as OUT OF BOUNDS, which is a true and
        /// useful answer, not as "you sent nothing", which is a lie about the
        /// caller. No map coordinate can be int.MinValue.
        /// </summary>
        private const int Unset = int.MinValue;

        // ------------------------------------------------------- field names
        // Canonical, lower-camel, and sorted here so fieldsApplied[] comes back
        // in a stable order without a sort at the end of every call.

        private const string FieldAreas = "areas";
        private const string FieldDesignations = "designations";
        private const string FieldFogged = "fogged";
        private const string FieldPassable = "passable";
        private const string FieldRoof = "roof";
        private const string FieldTerrain = "terrain";
        private const string FieldThings = "things";
        private const string FieldWalkable = "walkable";
        private const string FieldZone = "zone";

        private static readonly string[] CellFieldNames =
        {
            FieldAreas, FieldDesignations, FieldFogged, FieldPassable, FieldRoof,
            FieldTerrain, FieldThings, FieldWalkable, FieldZone
        };

        private const string ThingBuild = "build";
        private const string ThingClassName = "className";
        private const string ThingDefName = "defName";
        private const string ThingForbidden = "forbidden";
        private const string ThingHitPoints = "hitPoints";
        private const string ThingLabel = "label";
        private const string ThingOwner = "owner";
        private const string ThingPlant = "plant";
        private const string ThingStackCount = "stackCount";
        private const string ThingStuff = "stuff";

        private static readonly string[] ThingFieldNames =
        {
            ThingBuild, ThingClassName, ThingDefName, ThingForbidden,
            ThingHitPoints, ThingLabel, ThingOwner, ThingPlant, ThingStackCount,
            ThingStuff
        };

        [Tool(
            "home/get_cells_plus",
            Title = "Inspect map cells with forbidden state and bed ownership",
            Description =
                "Inspect every map cell in a rectangle: terrain, roof, walkable/passable, things, designations, "
                + "zones and areas, plus per-thing forbidden state and per-bed owner names that rimworld/get_cells_info does not carry. "
                + "The rectangle may be given as {x, z, width, height} (origin plus extent) or as {x0, z0, x1, z1} "
                + "(INCLUSIVE corner to corner, so x0=10, x1=41 is 32 cells wide); x0/z0 are aliases of x/z. "
                + "Arguments that do not describe exactly one rectangle are refused, never silently reduced to one cell. "
                + "Zone and area descriptors are de-duplicated into top-level dictionaries to keep the payload small. "
                + "THREE OPT-IN NARROWINGS, all off by default, for callers that do not want the whole payload: "
                + "`fields` picks the per-cell keys, `thingFields` picks the per-thing keys, `sparse` drops cells with no "
                + "selected content, and `summary` replaces cells[] with per-rectangle aggregates and lifts the cell cap to the "
                + "whole map. Every reply says which cap applied in cellCap/capReason and what it "
                + "applied in fieldsApplied[]/thingFieldsApplied[], and an unrecognised field name is refused rather than ignored.",
            ResultDescription =
                "success, the echoed rect in BOTH shapes plus the argument shape that was parsed, mapSize, cellCount (cells in the "
                + "RECTANGLE), cellCap and capReason, cellsOmitted, fieldsApplied[], thingFieldsApplied[], cells[], the zones/areas lookup "
                + "dictionaries referenced by each cell, and summary{} when summary was asked for.")]
        [ToolResponse("cells", "array", "One entry per cell in the rectangle, row-major from the top-left corner. Every entry carries walkable and passable WHEN THOSE FIELDS ARE SELECTED (they are by default). Empty when summary:true; shorter than cellCount when sparse:true.", Always = true)]
        [ToolResponse("cellCount", "integer", "Cells in the requested RECTANGLE. This is NOT len(cells): under sparse or summary it stays the full rectangle, and len(cells) + cellsOmitted == cellCount always.", Always = true)]
        [ToolResponse("cellCap", "integer", "The most cells THIS call was allowed to ask for: 1024 in the cells[] modes, the whole map area under summary:true. A rectangle past it is refused, never truncated.", Always = true)]
        [ToolResponse("capReason", "string", "Why cellCap is what it is, in words, so a caller never has to remember which mode lifts it.", Always = true)]
        [ToolResponse("mapSize", "object", "The current map as {x, z, cells}. Send {x:0, z:0, width:x, height:z} with summary:true for a whole-map aggregate in one call.", Always = true)]
        [ToolResponse("cellsOmitted", "integer", "Cells inside the rectangle that were scanned but not emitted in cells[]. 0 unless sparse or summary was asked for; equal to cellCount under summary, where cells[] is deliberately empty.", Always = true)]
        [ToolResponse("fieldsApplied", "array", "The per-cell field names this reply actually emitted, sorted. Present on every reply, including the default one, so a reader who sees a key missing from a cell can tell 'deselected' from 'absent'. x and z are always emitted and are not listed.", Always = true)]
        [ToolResponse("thingFieldsApplied", "array", "The per-thing field names emitted inside things[], sorted; defName is always among them. Present even when `things` was not selected, in which case it describes what WOULD have been emitted.", Always = true)]
        [ToolResponse("summary", "object", "Present only when summary:true. Aggregates over the whole rectangle: terrain/roof/fogged/walkable/passable counts, things (de-duplicated by thing identity, so a 2x2 building counts once — unlike cells[], where it appears in four cells), pawns[], designations, zones and areas with cellsInRect. Only the sections whose field is selected are computed.", Nullable = true)]
        [ToolResponse("zones", "object", "Zone id -> zone descriptor, for every zone referenced by any returned cell. Empty when `zone` is not selected, and empty under summary (nothing references it there; summary.zones carries the descriptors with cellsInRect).", Always = true)]
        [ToolResponse("areas", "object", "Area id -> area descriptor, for every area referenced by any returned cell. Empty when `areas` is not selected, and empty under summary (see summary.areas).", Always = true)]
        [ToolResponse("unknownArguments", "array", "Every argument key the caller sent that this tool does not declare, sorted, case-sensitively. Empty array = every key was recognised. The host's own _rimBridgeTimeoutMs is never listed.", Always = true)]
        [ToolResponse("unknownArgumentsWarning", "string", "Present only when unknownArguments is non-empty, or when the caller's raw keys could not be read at all - in which case the empty unknownArguments means 'not known', not 'nothing unknown'.", Nullable = true)]
        public async Task<object> GetCellsPlus(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            [ToolParameter(Description = "Origin (top-left) cell x. Alias of x0 — send either, not both with different values.")] int x = Unset,
            [ToolParameter(Description = "Origin (top-left) cell z. Alias of z0.")] int z = Unset,
            [ToolParameter(Description = "Rectangle width in cells. width * height must not exceed 1024, or the whole map area under summary:true. Omit for 1, or send x1 instead. Not both.")] int width = Unset,
            [ToolParameter(Description = "Rectangle height in cells. width * height must not exceed 1024, or the whole map area under summary:true. Omit for 1, or send z1 instead. Not both.")] int height = Unset,
            [ToolParameter(Description = "Origin cell x under its corner-form name. Identical to x.")] int x0 = Unset,
            [ToolParameter(Description = "Origin cell z under its corner-form name. Identical to z.")] int z0 = Unset,
            [ToolParameter(Description = "Far-corner cell x, INCLUSIVE: x0=10 with x1=12 is three cells wide. Send this or width, not both.")] int x1 = Unset,
            [ToolParameter(Description = "Far-corner cell z, INCLUSIVE. Send this or height, not both.")] int z1 = Unset,
            [ToolParameter(Description = "Alias of x0 used by rectangle APIs.")] int minX = Unset,
            [ToolParameter(Description = "Alias of z0 used by rectangle APIs.")] int minZ = Unset,
            [ToolParameter(Description = "Alias of x1, INCLUSIVE.")] int maxX = Unset,
            [ToolParameter(Description = "Alias of z1, INCLUSIVE.")] int maxZ = Unset,
            [ToolParameter(Description = "Which per-cell keys to emit, comma separated, case-insensitive, spaces ignored: terrain, roof, fogged, walkable, passable, zone, areas, things, designations. Omit (or send \"all\") for every one of them, which is byte-for-byte the payload this tool has always returned. x and z are always emitted. An unrecognised name is REFUSED, not ignored. fieldsApplied[] in the reply says what was actually emitted.")] string fields = null,
            [ToolParameter(Description = "Which per-thing keys to emit inside things[], comma separated, case-insensitive: label, className, stackCount, stuff, hitPoints, forbidden, owner (ownerName/ownerNames/medical), plant (growth/harvestableNow), build (isBlueprint/blueprintBuildDefName/isFrame/frameBuildDefName). defName is always emitted whatever you send. Omit (or \"all\") for all of them. Only takes effect when `things` is selected. An unrecognised name is REFUSED.")] string thingFields = null,
            [ToolParameter(Description = "Omit cells that carry none of the SELECTED optional content — no things, no designations, no zone, no areas. A cell whose only keys would be x, z and the scalars (terrain/roof/walkable/passable/fogged) is dropped and counted in cellsOmitted. FOG IS NOT CONTENT: an empty fogged cell is dropped like any other empty cell, so a caller that needs the fog shape must not use this. Default false.")] bool sparse = false,
            [ToolParameter(Description = "Return aggregates instead of cells: cells[] comes back EMPTY and summary{} carries counts over the whole rectangle. The reply is a few KB whatever the rectangle, so this mode LIFTS the 1024-cell cap to the whole map -- send {x:0, z:0, width:mapSize.x, height:mapSize.z} for a whole-map census in one call. `fields` still governs which sections are computed, so {summary:true, fields:\"things\"} is the cheapest 'what is in this rectangle' read. Things and designations are de-duplicated by identity here, unlike cells[]. Default false.")] bool summary = false)
        {
            if ((x0 != Unset && minX != Unset && x0 != minX)
                || (z0 != Unset && minZ != Unset && z0 != minZ)
                || (x1 != Unset && maxX != Unset && x1 != maxX)
                || (z1 != Unset && maxZ != Unset && z1 != maxZ))
                return ArgFailure("the x0/z0/x1/z1 and minX/minZ/maxX/maxZ aliases disagree.",
                                  new List<string>());
            return BridgeCommon.WithUnknownArguments(
                await GetCellsPlusCore(ctx, cancellationToken, x, z, width, height,
                                       x0 != Unset ? x0 : minX, z0 != Unset ? z0 : minZ,
                                       x1 != Unset ? x1 : maxX, z1 != Unset ? z1 : maxZ,
                                       fields, thingFields, sparse, summary).ConfigureAwait(false),
                ctx, typeof(HomeMapTools), "home/get_cells_plus");
        }

        private async Task<object> GetCellsPlusCore(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            int x,
            int z,
            int width,
            int height,
            int x0,
            int z0,
            int x1,
            int z1,
            string fields,
            string thingFields,
            bool sparse,
            bool summary)
        {
            if (ctx?.MainThread == null)
                return Failure("No RimBridge main-thread dispatcher is available for this invocation.");

            // Parsed BEFORE the main-thread hop on purpose: this is arithmetic on
            // eight ints and two strings and touches no game state, so an argument
            // mistake should not cost RimWorld a frame, and should not be able to
            // reach the map sweep at all.
            RectSpec parsed;
            var argError = ParseRect(x, z, width, height, x0, z0, x1, z1,
                                     summary ? MaxSummaryAxis : MaxCells, summary, out parsed);
            if (argError != null)
                return argError;

            Selection selection;
            var fieldError = ParseSelection(fields, thingFields, sparse, summary, out selection);
            if (fieldError != null)
                return fieldError;

            // Copied out of the `out` locals before the closure: an out variable
            // captured by a lambda inside an async method is legal but subtle, and
            // this file has already cost one debugging session.
            var spec = parsed;
            var sel = selection;

            // Companion tools are dispatched with MarshalToMainThread = false
            // (AnnotatedExtensionCapabilityProvider.InvokeAsync), so every read of
            // thingGrid / zoneManager / areaManager has to be hopped onto RimWorld's
            // main thread by hand. Doing the whole rectangle inside one hop keeps it
            // to a single frame's worth of work.
            return await ctx.MainThread
                .InvokeAsync(() => BuildResponse(spec, sel), cancellationToken)
                .ConfigureAwait(false);
        }

        // ===================================================== rectangle parsing

        /// <summary>
        /// The rectangle a caller actually asked for, plus how they asked, so the
        /// reply can echo their own shape back at them.
        /// </summary>
        private sealed class RectSpec
        {
            public int X;
            public int Z;
            public int Width;
            public int Height;
            public bool CornersSwapped;
            public string Shape;
        }

        /// <summary>
        /// Returns null and fills <paramref name="spec"/> on success, or a ready
        /// failure payload. Never returns a rectangle of zero cells: that state
        /// is the 2026-09-02 defect and there is no longer a code path to it.
        /// </summary>
        private static object ParseRect(
            int x, int z, int width, int height,
            int x0, int z0, int x1, int z1,
            int axisCap, bool summary,
            out RectSpec spec)
        {
            spec = null;

            // Recorded before anything is derived, so the failure text can quote
            // what the caller sent rather than what we made of it.
            var seen = new List<string>(8);
            AddIfSupplied(seen, "x", x);
            AddIfSupplied(seen, "z", z);
            AddIfSupplied(seen, "width", width);
            AddIfSupplied(seen, "height", height);
            AddIfSupplied(seen, "x0", x0);
            AddIfSupplied(seen, "z0", z0);
            AddIfSupplied(seen, "x1", x1);
            AddIfSupplied(seen, "z1", z1);

            if (seen.Count == 0)
            {
                return ArgFailure(
                    "no rectangle arguments at all were supplied. Note that this is also what a request "
                    + "made entirely of MISSPELLED or unsupported argument names looks like from in here.",
                    seen);
            }

            int originX, sizeX, originZ, sizeZ;
            bool swappedX, swappedZ;
            string axisError;

            if (!TryAxis("x", x, "x0", x0, "width", width, "x1", x1, axisCap, summary,
                         out originX, out sizeX, out swappedX, out axisError))
                return ArgFailure(axisError, seen);

            if (!TryAxis("z", z, "z0", z0, "height", height, "z1", z1, axisCap, summary,
                         out originZ, out sizeZ, out swappedZ, out axisError))
                return ArgFailure(axisError, seen);

            spec = new RectSpec
            {
                X = originX,
                Z = originZ,
                Width = sizeX,
                Height = sizeZ,
                CornersSwapped = swappedX || swappedZ,
                // The caller's own argument names, in canonical order — so the
                // echo says "{x0,z0,x1,z1}" to whoever sent that, and they can
                // see at a glance that it was understood as a rectangle and not
                // dropped on the floor.
                Shape = "{" + string.Join(",", seen.Select(NameOf).ToArray()) + "}"
            };
            return null;
        }

        /// <summary>
        /// One axis of the rectangle. x and z are the same problem twice, so this
        /// is written once and called twice with the x-names and then the z-names;
        /// having two copies of it is how the two axes drift apart.
        ///
        /// originArg / altOriginArg are the SAME edge under two names (x and x0).
        /// extentArg is a count of cells; farArg is an INCLUSIVE coordinate.
        /// </summary>
        private static bool TryAxis(
            string originName, int originArg,
            string altOriginName, int altOriginArg,
            string extentName, int extentArg,
            string farName, int farArg,
            int axisCap, bool summary,
            out int origin, out int size, out bool swapped, out string error)
        {
            origin = 0;
            size = 0;
            swapped = false;
            error = null;

            if (originArg != Unset && altOriginArg != Unset && originArg != altOriginArg)
            {
                error = string.Format(
                    "{0}={1} and {2}={3} name the same edge of the rectangle and disagree.",
                    originName, originArg, altOriginName, altOriginArg);
                return false;
            }

            origin = originArg != Unset ? originArg : altOriginArg;
            if (origin == Unset)
            {
                error = string.Format(
                    "no origin on the {0} axis: neither {0} nor {1} was supplied (the other arguments alone "
                    + "cannot place the rectangle).",
                    originName, altOriginName);
                return false;
            }

            if (extentArg != Unset && extentArg <= 0)
            {
                error = string.Format(
                    "{0}={1} is not a positive number of cells. A rectangle of zero cells is never a "
                    + "legitimate request here; it is what the old silent-zero bug looked like.",
                    extentName, extentArg);
                return false;
            }

            if (farArg != Unset)
            {
                var far = farArg;
                if (far < origin)
                {
                    // Inclusive corners given the other way round. That is
                    // unambiguous — a rect has no orientation — so normalise it
                    // rather than refuse, but say so in the echoed rect so nobody
                    // has to wonder which corner won.
                    var swap = origin;
                    origin = far;
                    far = swap;
                    swapped = true;
                }

                // long, because (far - origin) over two ints from JSON can overflow
                // an int on its own before anything gets a chance to check the cap.
                var span = (long)far - origin + 1L;
                if (span > axisCap)
                {
                    error = string.Format(
                        "{0}={1} with origin {2}={3} spans {4} cells on the {2} axis alone, past the "
                        + "{5}-cell limit for the whole rectangle. {6}",
                        farName, farArg, originName, origin, span, axisCap,
                        summary
                            ? "Under summary:true the cap is the map area, so this rectangle is larger than any map; "
                              + "check the coordinates."
                            : "Split the request, or ask with summary:true, which lifts the cap to the whole map. "
                              + "Nothing is truncated here.");
                    return false;
                }

                if (extentArg != Unset && extentArg != (int)span)
                {
                    error = string.Format(
                        "{0}={1} and {2}={3} describe different rectangles: from origin {4}={5}, {2}={3} is "
                        + "{6} cells (the far corner is INCLUSIVE) but {0} says {1}. Send one or the other.",
                        extentName, extentArg, farName, farArg, originName, origin, span);
                    return false;
                }

                size = (int)span;
                return true;
            }

            // No far corner: the documented shape. width/height default to 1,
            // which is the pre-existing declared default and a legitimate
            // single-cell probe — unlike an origin of 0,0 nobody asked for.
            size = extentArg != Unset ? extentArg : 1;
            return true;
        }

        private static void AddIfSupplied(ICollection<string> into, string name, int value)
        {
            if (value != Unset)
                into.Add(name + "=" + value);
        }

        private static string NameOf(string suppliedPair)
        {
            var eq = suppliedPair.IndexOf('=');
            return eq > 0 ? suppliedPair.Substring(0, eq) : suppliedPair;
        }

        /// <summary>
        /// A rectangle-argument refusal. It names what was parsed, what was
        /// received, and what would have worked — because the failure mode being
        /// replaced here gave a caller a successful-looking answer about the wrong
        /// cell, and the cure for that is a refusal nobody can misread.
        /// </summary>
        private static object ArgFailure(string detail, IList<string> seen)
        {
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["success"] = false,
                ["tool"] = "home/get_cells_plus",
                ["error"] = "Could not build a rectangle from these arguments: " + detail,
                ["argumentsSeen"] = seen != null && seen.Count > 0
                    ? string.Join(", ", seen.ToArray())
                    : "(none)",
                ["expected"] =
                    "Either {x, z, width, height} — origin plus a count of cells, width/height default to 1 — "
                    + "or {x0, z0, x1, z1} — INCLUSIVE corner to corner, so x0=10, x1=41 is 32 cells wide. "
                    + "x0/z0 are accepted as aliases of x/z, so {x, z, x1, z1} works too. Reversed corners "
                    + "(x1 < x0) are normalised and reported as cornersSwapped.",
                ["note"] =
                    "RimBridge's argument binder silently DROPS argument names a tool does not declare, and "
                    + "fills a declared-but-unsupplied int with 0, so a misspelled or unsupported key arrives "
                    + "here as 'not supplied'. Before 2026-09-02 that turned {x0,z0,x1,z1} into a successful "
                    + "1x1 scan of cell (0,0) with every requested thing missing. This refusal is that bug's "
                    + "replacement."
            };
        }

        // ====================================================== field selection

        /// <summary>
        /// What this call was asked to emit. Built once, before the main-thread
        /// hop, and read by everything below it.
        /// </summary>
        private sealed class Selection
        {
            public HashSet<string> Cell;
            public HashSet<string> Thing;
            public List<string> CellApplied;
            public List<string> ThingApplied;
            public bool Sparse;
            public bool Summary;

            public bool Want(string field)
            {
                return Cell.Contains(field);
            }

            public bool WantThing(string field)
            {
                return Thing.Contains(field);
            }
        }

        /// <summary>
        /// Parse both field lists, or return a ready refusal. An unrecognised name
        /// is a refusal and not a warning: the point of asking for three fields is
        /// that you get three fields, and a typo that silently widens or narrows
        /// the answer is the same class of bug as the 2026-09-02 silent zero.
        /// </summary>
        private static object ParseSelection(
            string fields, string thingFields, bool sparse, bool summary, out Selection selection)
        {
            selection = null;

            HashSet<string> cellSet;
            List<string> cellApplied;
            var error = ParseFieldSpec("fields", fields, CellFieldNames, null,
                                       "per-cell field", out cellSet, out cellApplied);
            if (error != null)
                return error;

            HashSet<string> thingSet;
            List<string> thingApplied;
            error = ParseFieldSpec("thingFields", thingFields, ThingFieldNames, ThingDefName,
                                   "per-thing field", out thingSet, out thingApplied);
            if (error != null)
                return error;

            selection = new Selection
            {
                Cell = cellSet,
                Thing = thingSet,
                CellApplied = cellApplied,
                ThingApplied = thingApplied,
                Sparse = sparse,
                Summary = summary
            };
            return null;
        }

        /// <summary>
        /// One comma-separated field list. Null, empty, all-whitespace or "all"
        /// mean every accepted name — that is the default, and it is the payload
        /// this tool returned before field selection existed.
        ///
        /// <paramref name="alwaysOn"/> is a name that is added whatever the caller
        /// sent (defName: a thing with no defName is not identifiable at all, and
        /// no caller has ever wanted one).
        /// </summary>
        private static object ParseFieldSpec(
            string parameterName,
            string spec,
            string[] accepted,
            string alwaysOn,
            string whatItIs,
            out HashSet<string> selected,
            out List<string> applied)
        {
            selected = new HashSet<string>(StringComparer.Ordinal);
            applied = null;

            var trimmed = (spec ?? string.Empty).Trim();
            if (trimmed.Length == 0)
            {
                foreach (var name in accepted)
                    selected.Add(name);
            }
            else
            {
                var named = 0;
                foreach (var raw in trimmed.Split(','))
                {
                    var token = raw.Trim();
                    if (token.Length == 0)
                        continue;
                    named++;

                    if (string.Equals(token, "all", StringComparison.OrdinalIgnoreCase))
                    {
                        foreach (var name in accepted)
                            selected.Add(name);
                        continue;
                    }

                    var canonical = accepted.FirstOrDefault(
                        name => string.Equals(name, token, StringComparison.OrdinalIgnoreCase));
                    if (canonical == null)
                        return FieldFailure(parameterName, spec, token, accepted, whatItIs, false);
                    selected.Add(canonical);
                }

                if (named == 0)
                    return FieldFailure(parameterName, spec, null, accepted, whatItIs, true);
            }

            if (alwaysOn != null)
                selected.Add(alwaysOn);

            applied = selected.OrderBy(name => name, StringComparer.Ordinal).ToList();
            return null;
        }

        /// <summary>
        /// A field-list refusal, in the same register as the rectangle one: it
        /// quotes what was sent, names the offending word, and lists every name
        /// that would have worked. Nothing is narrowed on the way out.
        /// </summary>
        private static object FieldFailure(
            string parameterName, string spec, string badName, string[] accepted,
            string whatItIs, bool empty)
        {
            var detail = empty
                ? string.Format(
                    "{0}=\"{1}\" named no {2} at all — it is punctuation and whitespace only. Send the names "
                    + "you want, or omit the argument entirely (or send \"all\") for every one of them. An "
                    + "empty selection is never answered as a successful narrow answer.",
                    parameterName, spec, whatItIs)
                : string.Format(
                    "{0}=\"{1}\": \"{2}\" is not a {3} this tool emits. Nothing was narrowed and nothing was "
                    + "ignored — an unrecognised name is refused, because a name that is quietly dropped is a "
                    + "caller who believes they asked for something and did not get it.",
                    parameterName, spec, badName, whatItIs);

            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["success"] = false,
                ["tool"] = "home/get_cells_plus",
                ["error"] = detail,
                ["parameter"] = parameterName,
                ["accepted"] = accepted.ToList(),
                ["expected"] =
                    "A comma-separated list of the accepted names above, case-insensitive, whitespace ignored "
                    + "(\"terrain, things\" and \"TERRAIN,things\" are the same request). Omit the argument, "
                    + "send an empty string, or send \"all\" for every field — which is the default and is "
                    + "byte-for-byte the payload this tool returned before field selection existed.",
                ["note"] =
                    "fieldsApplied[] and thingFieldsApplied[] on a successful reply name exactly what was "
                    + "emitted, so a key missing from a cell is always explained by that list."
            };
        }

        // ============================================================== the sweep

        private static object BuildResponse(RectSpec spec, Selection sel)
        {
            Map map;
            string mapError;
            if (!TryGetMap(out map, out mapError))
                return Failure(mapError);

            // Unreachable via ParseRect, which refuses a non-positive extent.
            // Kept because "the rectangle came out empty and we returned success"
            // is the defect this file was rewritten for, and a guard that can
            // never fire is cheaper than that happening twice.
            if (spec.Width <= 0 || spec.Height <= 0)
            {
                return Failure(string.Format(
                    "Internal: parsed rectangle x={0} z={1} width={2} height={3} has no cells. "
                    + "No request should be able to reach here; this is a bug in ParseRect, not in the call.",
                    spec.X, spec.Z, spec.Width, spec.Height));
            }

            var requestedCellCount = (long)spec.Width * spec.Height;

            // The cap is the payload's, not the sweep's: cells[] modes emit a row
            // per cell and are held to 1024, while summary:true emits none and is
            // held only to the map. mapArea is computed here because map.Size is
            // a main-thread read the argument parse cannot make.
            var mapArea = (long)map.Size.x * map.Size.z;
            var cellCap = sel.Summary ? mapArea : MaxCells;
            var capReason = sel.Summary
                ? string.Format(
                    "summary:true emits no cells[], so the cap is the whole map ({0} x {1} = {2} cells). "
                    + "A rectangle outside the map is still refused.",
                    map.Size.x, map.Size.z, mapArea)
                : string.Format(
                    "cells[] carries one row per cell, so the cap is {0}. Ask with summary:true for aggregates "
                    + "over any rectangle up to the whole map.",
                    MaxCells);

            if (requestedCellCount > cellCap)
            {
                // Loudly, with the numbers, and nothing truncated: a short answer
                // that looks complete is worse than no answer. map.py sweeps in
                // 32x32 blocks for exactly this reason.
                return Failure(string.Format(
                    "Requested rectangle x={0} z={1} width={2} height={3} (corners {0},{1} to {4},{5}) contains "
                    + "{6} cells, which exceeds the limit of {7} that applies to this call. {8} Nothing was truncated.",
                    spec.X, spec.Z, spec.Width, spec.Height,
                    spec.X + spec.Width - 1, spec.Z + spec.Height - 1,
                    requestedCellCount, cellCap, capReason));
            }

            // Walked lazily and twice rather than materialised: a whole-map
            // summary is tens of thousands of cells and the list would be the one
            // per-cell allocation on a path whose whole point is not to have any.
            foreach (var cell in EnumerateCells(spec))
            {
                if (!cell.InBounds(map))
                {
                    return Failure(string.Format(
                        "Cell ({0}, {1}) is out of bounds for the current map. Requested rectangle x={2} z={3} "
                        + "width={4} height={5}; the map is {6} x {7} cells. Clamp the rectangle to the map — "
                        + "this tool refuses an out-of-bounds rect rather than clipping it, so that a clipped "
                        + "answer never passes for a complete one.",
                        cell.x, cell.z, spec.X, spec.Z, spec.Width, spec.Height,
                        map.Size.x, map.Size.z));
                }
            }

            var totalCells = (int)requestedCellCount;
            var zoneLookup = new Dictionary<string, object>(StringComparer.Ordinal);
            var areaLookup = new Dictionary<string, object>(StringComparer.Ordinal);
            var aggregate = sel.Summary ? new SummaryBuilder(sel) : null;
            var payloads = new List<Dictionary<string, object>>(aggregate != null ? 0 : totalCells);
            var omitted = 0;

            foreach (var cell in EnumerateCells(spec))
            {
                if (aggregate != null)
                {
                    aggregate.Add(map, cell);
                    continue;
                }

                bool hasContent;
                var payload = DescribeCell(map, cell, sel, zoneLookup, areaLookup, out hasContent);
                if (sel.Sparse && !hasContent)
                {
                    omitted++;
                    continue;
                }
                payloads.Add(payload);
            }

            // Under summary every cell was scanned and none was emitted, so the
            // reconciliation len(cells) + cellsOmitted == cellCount stays true in
            // all three modes rather than being a rule with an exception.
            if (aggregate != null)
                omitted = totalCells;

            // Echoed in BOTH shapes. A caller who sent corners can read its
            // corners back and see they were understood; a caller who sent
            // width/height can read those back; `argumentShape` says which set of
            // names actually arrived, which is the one thing the old silent zero
            // never told anybody.
            var rect = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["x"] = spec.X,
                ["z"] = spec.Z,
                ["width"] = spec.Width,
                ["height"] = spec.Height,
                ["x0"] = spec.X,
                ["z0"] = spec.Z,
                ["x1"] = spec.X + spec.Width - 1,
                ["z1"] = spec.Z + spec.Height - 1,
                ["argumentShape"] = spec.Shape
            };
            if (spec.CornersSwapped)
                rect["cornersSwapped"] = true;

            var reply = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["success"] = true,
                ["tool"] = "home/get_cells_plus",
                ["mapName"] = map.Parent?.Label,
                ["rect"] = rect,
                // So a caller can ask for the whole map in one call without a
                // second tool: {x:0, z:0, width:mapSize.x, height:mapSize.z}.
                ["mapSize"] = new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    ["x"] = map.Size.x,
                    ["z"] = map.Size.z,
                    ["cells"] = mapArea
                },
                ["cellCount"] = totalCells,
                ["cellsOmitted"] = omitted,
                ["cellCap"] = cellCap,
                ["capReason"] = capReason,
                ["fieldsApplied"] = sel.CellApplied,
                ["thingFieldsApplied"] = sel.ThingApplied,
                ["zones"] = zoneLookup,
                ["areas"] = areaLookup,
                ["cells"] = payloads
            };
            if (aggregate != null)
                reply["summary"] = aggregate.Build();
            return reply;
        }

        /// <summary>
        /// One cell. <paramref name="hasContent"/> is what `sparse` filters on:
        /// true when the cell carries at least one piece of SELECTED optional
        /// content (a thing, a designation, a zone, an area). Fog is not content
        /// — see the 2026-09-03 block at the top of this file.
        /// </summary>
        private static Dictionary<string, object> DescribeCell(
            Map map,
            IntVec3 cell,
            Selection sel,
            IDictionary<string, object> zoneLookup,
            IDictionary<string, object> areaLookup,
            out bool hasContent)
        {
            hasContent = false;

            var payload = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["x"] = cell.x,
                ["z"] = cell.z
            };

            if (sel.Want(FieldTerrain))
                AddIfPresent(payload, "terrainDefName", map.terrainGrid?.TerrainAt(cell)?.defName);
            if (sel.Want(FieldRoof))
                AddIfPresent(payload, "roofDefName", map.roofGrid?.RoofAt(cell)?.defName);

            // `fogged` keeps the omit-when-false rule: every reader of it already
            // reads absence as false and is right to.
            if (sel.Want(FieldFogged) && cell.Fogged(map))
                payload["fogged"] = true;

            // --- 2026-09-02: always present, both of them, on every cell -------
            // `walkable` used to be written only when FALSE and `passable` was
            // never written at all, so both read as None on every walkable cell
            // and "walkable" was indistinguishable from "not reported". These are
            // two different questions and both get answered:
            //   walkable — the path grid (IntVec3.Walkable), what a pawn can move
            //              through; a wall or a filled cell is false
            //   passable — terrain/edifice passability (!IntVec3.Impassable), the
            //              same test ZonesTool runs via GetEdifice().def.passability
            // A cell can be one and not the other, which is why neither is
            // derived from the other. 2026-09-03: still ALWAYS present when the
            // field is selected — the contract is unchanged, and fieldsApplied[]
            // is how a reader learns it was not.
            if (sel.Want(FieldWalkable))
                payload["walkable"] = SafeCellBool(() => cell.Walkable(map));
            if (sel.Want(FieldPassable))
                payload["passable"] = SafeCellBool(() => !cell.Impassable(map));
            // ------------------------------------------------------------------

            if (sel.Want(FieldZone))
            {
                var zone = map.zoneManager?.ZoneAt(cell);
                if (zone != null)
                {
                    var zoneId = SafeLoadId(zone);
                    if (zoneId != null)
                    {
                        if (!zoneLookup.ContainsKey(zoneId))
                            zoneLookup[zoneId] = DescribeZone(zone, zoneId);
                        payload["zoneId"] = zoneId;
                        hasContent = true;
                    }
                }
            }

            if (sel.Want(FieldAreas))
            {
                var areas = map.areaManager?.AllAreas
                    ?.Where(area => area != null && area[cell])
                    .OrderBy(area => area.ListPriority)
                    .ThenBy(area => area.Label, StringComparer.Ordinal)
                    .ToList();
                if (areas != null && areas.Count > 0)
                {
                    var areaIds = new List<string>(areas.Count);
                    foreach (var area in areas)
                    {
                        var areaId = SafeLoadId(area);
                        if (areaId == null)
                            continue;
                        if (!areaLookup.ContainsKey(areaId))
                            areaLookup[areaId] = DescribeArea(area, areaId);
                        areaIds.Add(areaId);
                    }

                    if (areaIds.Count > 0)
                    {
                        payload["areaIds"] = areaIds;
                        hasContent = true;
                    }
                }
            }

            if (sel.Want(FieldThings))
            {
                var things = map.thingGrid?.ThingsListAt(cell)?.Where(thing => thing != null).ToList()
                    ?? new List<Thing>();
                if (things.Count > 0)
                {
                    payload["things"] = things.Select(thing => DescribeThing(thing, sel)).ToList();
                    hasContent = true;
                }
            }

            if (sel.Want(FieldDesignations))
            {
                // AllDesignationsAt hands back a SHARED scratch list that the next
                // call clears, so it is materialised here and not deferred.
                var designations = map.designationManager?.AllDesignationsAt(cell)
                    ?.Where(designation => designation != null)
                    .ToList();
                if (designations != null && designations.Count > 0)
                {
                    payload["designations"] = designations.Select(DescribeDesignation).ToList();
                    hasContent = true;
                }
            }

            return payload;
        }

        private static Dictionary<string, object> DescribeThing(Thing thing, Selection sel)
        {
            var payload = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["defName"] = thing.def?.defName
            };
            if (sel.WantThing(ThingLabel))
                payload["label"] = SafeLabel(thing);
            if (sel.WantThing(ThingClassName))
                payload["className"] = thing.GetType().Name;

            if (sel.WantThing(ThingStackCount) && thing.stackCount != 1)
                payload["stackCount"] = thing.stackCount;
            if (sel.WantThing(ThingStuff))
                AddIfPresent(payload, "stuffDefName", thing.Stuff?.defName);
            if (sel.WantThing(ThingHitPoints) && thing.HitPoints >= 0)
                payload["hitPoints"] = thing.HitPoints;

            // --- the whole point of this tool -------------------------------
            // Always emitted (true OR false) for anything that can be forbidden,
            // whenever the field is selected at all.
            if (sel.WantThing(ThingForbidden))
            {
                var forbiddable = (thing as ThingWithComps)?.GetComp<CompForbiddable>();
                if (forbiddable != null)
                    payload["forbidden"] = forbiddable.Forbidden;
            }

            if (sel.WantThing(ThingOwner) && thing is Building_Bed bed)
            {
                var owners = bed.OwnersForReading;
                var names = owners?
                    .Where(pawn => pawn != null)
                    .Select(SafePawnName)
                    .Where(name => !string.IsNullOrEmpty(name))
                    .ToList() ?? new List<string>();

                // Always emitted for a bed, explicitly null when unassigned, so
                // "no owner" is distinguishable from "field not reported".
                payload["ownerName"] = names.Count > 0 ? names[0] : null;
                if (names.Count > 1)
                    payload["ownerNames"] = names;
                if (bed.Medical)
                    payload["medical"] = true;
            }
            // ----------------------------------------------------------------

            if (sel.WantThing(ThingPlant) && thing is Plant plant)
            {
                payload["growth"] = (float)Math.Round(plant.Growth, 3);
                if (plant.HarvestableNow)
                    payload["harvestableNow"] = true;
            }

            if (sel.WantThing(ThingBuild))
            {
                if (thing is Blueprint)
                    payload["isBlueprint"] = true;
                if (thing is Blueprint_Build blueprintBuild)
                    AddIfPresent(payload, "blueprintBuildDefName", blueprintBuild.BuildDef?.defName);
                if (thing is Frame frame)
                {
                    payload["isFrame"] = true;
                    AddIfPresent(payload, "frameBuildDefName", frame.BuildDef?.defName);
                }
            }

            return payload;
        }

        private static Dictionary<string, object> DescribeDesignation(Designation designation)
        {
            var payload = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["defName"] = designation.def?.defName
            };

            if (designation.target.HasThing)
                AddIfPresent(payload, "targetThingDefName", designation.target.Thing?.def?.defName);
            else if (designation.target.Cell.IsValid)
                payload["targetCell"] = new { x = designation.target.Cell.x, z = designation.target.Cell.z };

            return payload;
        }

        private static Dictionary<string, object> DescribeZone(Zone zone, string id)
        {
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["id"] = id,
                ["label"] = BridgeCommon.SafeString(() => zone.RenamableLabel),
                ["baseLabel"] = BridgeCommon.SafeString(() => zone.BaseLabel),
                ["className"] = zone.GetType().Name,
                ["cellCount"] = BridgeCommon.Try(() => zone.CellCount, 0),
                ["hidden"] = BridgeCommon.Try(() => zone.Hidden, false)
            };
        }

        private static Dictionary<string, object> DescribeArea(Area area, string id)
        {
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["id"] = id,
                ["label"] = BridgeCommon.SafeString(() => area.Label),
                ["className"] = area.GetType().Name,
                ["cellCount"] = BridgeCommon.Try(() => area.TrueCount, 0)
            };
        }

        // ================================================================ summary

        /// <summary>
        /// Reference identity, for the two de-duplications the summary needs.
        /// Verse.Thing overrides GetHashCode (it returns thingIDNumber) and that
        /// is enough to make a plain HashSet behave, but "enough by accident" is
        /// how this file has been bitten before, so identity is stated outright.
        /// </summary>
        private sealed class ReferenceComparer<T> : IEqualityComparer<T> where T : class
        {
            internal static readonly ReferenceComparer<T> Instance = new ReferenceComparer<T>();

            public bool Equals(T a, T b)
            {
                return ReferenceEquals(a, b);
            }

            public int GetHashCode(T obj)
            {
                return RuntimeHelpers.GetHashCode(obj);
            }
        }

        private sealed class ThingTally
        {
            public long Count;      // total stackCount
            public int Stacks;      // thing entries
            public int Forbidden;   // entries whose CompForbiddable says true
            public string Label;    // WITHOUT the stack count; see Build()
        }

        /// <summary>
        /// The aggregates behind `summary:true`. One pass over the same cells the
        /// cell walk would take, emitting nothing per cell.
        ///
        /// `fields` governs which sections are computed as well as which are
        /// emitted: {summary:true, fields:"things"} really does skip the terrain
        /// grid reads, which is the point of it.
        /// </summary>
        private sealed class SummaryBuilder
        {
            private readonly Selection _sel;

            private readonly Dictionary<string, int> _terrain = new Dictionary<string, int>(StringComparer.Ordinal);
            private readonly Dictionary<string, int> _roof = new Dictionary<string, int>(StringComparer.Ordinal);
            private int _fogged;
            private int _walkTrue, _walkFalse, _walkUnknown;
            private int _passTrue, _passFalse, _passUnknown;

            private readonly HashSet<Thing> _seenThings =
                new HashSet<Thing>(ReferenceComparer<Thing>.Instance);
            private readonly Dictionary<string, ThingTally> _things =
                new Dictionary<string, ThingTally>(StringComparer.Ordinal);
            private readonly List<string> _pawns = new List<string>();

            private readonly HashSet<Designation> _seenDesignations =
                new HashSet<Designation>(ReferenceComparer<Designation>.Instance);
            private readonly Dictionary<string, int> _designations =
                new Dictionary<string, int>(StringComparer.Ordinal);

            private readonly Dictionary<string, Dictionary<string, object>> _zones =
                new Dictionary<string, Dictionary<string, object>>(StringComparer.Ordinal);
            private readonly Dictionary<string, int> _zoneCells =
                new Dictionary<string, int>(StringComparer.Ordinal);
            private readonly Dictionary<string, Dictionary<string, object>> _areas =
                new Dictionary<string, Dictionary<string, object>>(StringComparer.Ordinal);
            private readonly Dictionary<string, int> _areaCells =
                new Dictionary<string, int>(StringComparer.Ordinal);

            internal SummaryBuilder(Selection sel)
            {
                _sel = sel;
            }

            internal void Add(Map map, IntVec3 cell)
            {
                if (_sel.Want(FieldTerrain))
                {
                    var terrain = map.terrainGrid?.TerrainAt(cell)?.defName;
                    // "(none)" and not a dropped cell: a terrain that could not be
                    // read is a fact about the rectangle, and the counts have to
                    // add up to cellCount.
                    Bump(_terrain, string.IsNullOrEmpty(terrain) ? "(none)" : terrain);
                }

                if (_sel.Want(FieldRoof))
                {
                    var roof = map.roofGrid?.RoofAt(cell)?.defName;
                    // "unroofed" cannot collide with a RoofDef defName
                    // (RoofConstructed, RoofRockThin, RoofRockThick).
                    Bump(_roof, string.IsNullOrEmpty(roof) ? "unroofed" : roof);
                }

                if (_sel.Want(FieldFogged) && cell.Fogged(map))
                    _fogged++;

                if (_sel.Want(FieldWalkable))
                    Tri(SafeCellBool(() => cell.Walkable(map)), ref _walkTrue, ref _walkFalse, ref _walkUnknown);
                if (_sel.Want(FieldPassable))
                    Tri(SafeCellBool(() => !cell.Impassable(map)), ref _passTrue, ref _passFalse, ref _passUnknown);

                if (_sel.Want(FieldZone))
                {
                    var zone = map.zoneManager?.ZoneAt(cell);
                    if (zone != null)
                    {
                        var id = SafeLoadId(zone);
                        if (id != null)
                        {
                            if (!_zones.ContainsKey(id))
                                _zones[id] = DescribeZone(zone, id);
                            Bump(_zoneCells, id);
                        }
                    }
                }

                if (_sel.Want(FieldAreas))
                {
                    var areas = map.areaManager?.AllAreas;
                    if (areas != null)
                    {
                        foreach (var area in areas)
                        {
                            if (area == null || !area[cell])
                                continue;
                            var id = SafeLoadId(area);
                            if (id == null)
                                continue;
                            if (!_areas.ContainsKey(id))
                                _areas[id] = DescribeArea(area, id);
                            Bump(_areaCells, id);
                        }
                    }
                }

                if (_sel.Want(FieldThings))
                {
                    var things = map.thingGrid?.ThingsListAt(cell);
                    if (things != null)
                    {
                        foreach (var thing in things)
                        {
                            if (thing == null || !_seenThings.Add(thing))
                                continue;   // a 2x2 building, on its second cell
                            AddThing(thing);
                        }
                    }
                }

                if (_sel.Want(FieldDesignations))
                {
                    var designations = map.designationManager?.AllDesignationsAt(cell);
                    if (designations != null)
                    {
                        // Materialised: AllDesignationsAt returns a shared scratch
                        // list, and it is walked here before anything else calls it.
                        foreach (var designation in designations.ToList())
                        {
                            if (designation == null || !_seenDesignations.Add(designation))
                                continue;
                            Bump(_designations, designation.def?.defName ?? "(none)");
                        }
                    }
                }
            }

            private void AddThing(Thing thing)
            {
                var defName = thing.def?.defName ?? "(none)";
                ThingTally tally;
                if (!_things.TryGetValue(defName, out tally))
                {
                    tally = new ThingTally { Label = SafeLabelNoCount(thing) };
                    _things[defName] = tally;
                }
                tally.Stacks++;
                tally.Count += Math.Max(1, BridgeCommon.Try(() => thing.stackCount, 1));

                var forbiddable = (thing as ThingWithComps)?.GetComp<CompForbiddable>();
                if (forbiddable != null && BridgeCommon.Try(() => forbiddable.Forbidden, false))
                    tally.Forbidden++;

                if (thing is Pawn pawn)
                    _pawns.Add(SafePawnName(pawn) ?? defName);
            }

            internal Dictionary<string, object> Build()
            {
                var summary = new Dictionary<string, object>(StringComparer.Ordinal);

                if (_sel.Want(FieldTerrain))
                    summary["terrain"] = _terrain;
                if (_sel.Want(FieldRoof))
                    summary["roof"] = _roof;
                if (_sel.Want(FieldFogged))
                    summary["fogged"] = _fogged;
                if (_sel.Want(FieldWalkable))
                    summary["walkable"] = Counts(_walkTrue, _walkFalse, _walkUnknown);
                if (_sel.Want(FieldPassable))
                    summary["passable"] = Counts(_passTrue, _passFalse, _passUnknown);

                if (_sel.Want(FieldThings))
                {
                    var things = new Dictionary<string, object>(StringComparer.Ordinal);
                    foreach (var entry in _things.OrderBy(e => e.Key, StringComparer.Ordinal))
                    {
                        things[entry.Key] = new Dictionary<string, object>(StringComparer.Ordinal)
                        {
                            ["count"] = entry.Value.Count,
                            ["stacks"] = entry.Value.Stacks,
                            ["forbidden"] = entry.Value.Forbidden,
                            ["label"] = entry.Value.Label
                        };
                    }
                    summary["things"] = things;
                    // A pawn in the rectangle is the first thing anybody asks
                    // about it, so the labels are listed rather than counted.
                    summary["pawns"] = _pawns.OrderBy(n => n, StringComparer.Ordinal).ToList();
                }

                if (_sel.Want(FieldDesignations))
                    summary["designations"] = _designations;

                if (_sel.Want(FieldZone))
                    summary["zones"] = WithCellsInRect(_zones, _zoneCells);
                if (_sel.Want(FieldAreas))
                    summary["areas"] = WithCellsInRect(_areas, _areaCells);

                return summary;
            }

            private static Dictionary<string, object> WithCellsInRect(
                Dictionary<string, Dictionary<string, object>> descriptors,
                Dictionary<string, int> cellCounts)
            {
                var built = new Dictionary<string, object>(StringComparer.Ordinal);
                foreach (var entry in descriptors)
                {
                    int n;
                    cellCounts.TryGetValue(entry.Key, out n);
                    entry.Value["cellsInRect"] = n;
                    built[entry.Key] = entry.Value;
                }
                return built;
            }

            private static Dictionary<string, object> Counts(int yes, int no, int unknown)
            {
                return new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    ["true"] = yes,
                    ["false"] = no,
                    ["unknown"] = unknown
                };
            }

            private static void Tri(object value, ref int yes, ref int no, ref int unknown)
            {
                if (value is bool b)
                {
                    if (b)
                        yes++;
                    else
                        no++;
                }
                else
                {
                    unknown++;
                }
            }

            private static void Bump(IDictionary<string, int> into, string key)
            {
                int n;
                into.TryGetValue(key, out n);
                into[key] = n + 1;
            }
        }

        private static void AddIfPresent(IDictionary<string, object> payload, string key, string value)
        {
            if (!string.IsNullOrEmpty(value))
                payload[key] = value;
        }

        /// <summary>
        /// A per-cell boolean that is a real boolean, or the STRING "unknown" if
        /// RimWorld threw while answering. Deliberately not bool? — a null in this
        /// payload is indistinguishable from an absent key once it is JSON, and a
        /// reader who cannot tell those apart is the 2026-09-02 defect. A field
        /// that cannot be computed says so in a word.
        /// </summary>
        private static object SafeCellBool(Func<bool> read)
        {
            try
            {
                return read();
            }
            catch
            {
                return "unknown";
            }
        }

        private static string SafeLabel(Thing thing)
        {
            try
            {
                return thing.LabelCap.ToString();
            }
            catch
            {
                return thing.def?.defName ?? thing.GetType().Name;
            }
        }

        /// <summary>
        /// The label WITHOUT the stack count. `LabelCap` on a stack ends in
        /// " x30" (GenLabel), and map.py's forbid_line already had to strip that
        /// once; in an aggregate where `count` is a separate field, a label
        /// carrying one stack's count would be actively wrong.
        /// </summary>
        private static string SafeLabelNoCount(Thing thing)
        {
            try
            {
                return thing.LabelCapNoCount;
            }
            catch
            {
                return SafeLabel(thing);
            }
        }

        private static string SafePawnName(Pawn pawn)
        {
            try
            {
                return pawn.LabelShortCap.ToString();
            }
            catch
            {
                try
                {
                    return pawn.LabelCap.ToString();
                }
                catch
                {
                    return pawn.def?.defName;
                }
            }
        }

        private static string SafeLoadId(ILoadReferenceable referenceable)
        {
            try
            {
                return referenceable.GetUniqueLoadID();
            }
            catch
            {
                return null;
            }
        }

        private static IEnumerable<IntVec3> EnumerateCells(RectSpec spec)
        {
            for (var offsetZ = 0; offsetZ < spec.Height; offsetZ++)
            {
                for (var offsetX = 0; offsetX < spec.Width; offsetX++)
                    yield return new IntVec3(spec.X + offsetX, 0, spec.Z + offsetZ);
            }
        }

        /// <summary>The shared map gate; see BridgeCommon.TryGetMap. The error
        /// text names this tool.</summary>
        private static bool TryGetMap(out Map map, out string error)
        {
            return BridgeCommon.TryGetMap("home/get_cells_plus", out map, out error);
        }

        /// <summary>The shared refusal shape; see BridgeCommon.Failure.</summary>
        private static object Failure(string error)
        {
            return BridgeCommon.Failure("home/get_cells_plus", error);
        }
    }
}
