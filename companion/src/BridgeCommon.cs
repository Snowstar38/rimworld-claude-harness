using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Threading;
using RimBridgeServer.Sdk;
using Verse;

namespace HomeBridge.BridgeTools
{
    /// <summary>
    /// Helpers shared by every home/ tool. Nothing here knows about a specific
    /// tool: it is the floor the tool files stand on.
    ///
    /// The one non-obvious member is <see cref="RawArguments"/>. The SDK binder
    /// (RimBridgeServer.AnnotatedExtensionCapabilityProvider.BindArguments) hands
    /// a tool method only the parameters it declares, by name, and drops every
    /// other key the caller sent without a word. IRimBridgeContext carries no
    /// argument dictionary, so the raw keys are unreachable through the SDK's
    /// public surface. They ARE reachable one step to the side: the capability
    /// registry writes the whole caller dictionary into the operation journal as
    /// metadata["arguments"] BEFORE it invokes the handler, and the journal is a
    /// process-wide singleton. Reading it back by the operation id in
    /// IRimBridgeContext.OperationId is how a tool learns what it was actually
    /// sent. Every step is reflection into another mod's internals, so every step
    /// is guarded and a failure is reported rather than defaulted.
    /// </summary>
    internal static class BridgeCommon
    {
        /// <summary>
        /// The host's own execution-timeout key. It rides along on every call
        /// made through IRimBridgeToolClient with a TimeoutMs option and is not
        /// a caller mistake, so it is never reported as unknown.
        /// </summary>
        private const string HostTimeoutArgument = "_rimBridgeTimeoutMs";

        // ------------------------------------------------------------------
        // Unknown-argument detection
        // ------------------------------------------------------------------

        /// <summary>
        /// What a tool was sent that it does not understand. Unknown is always a
        /// list, never null. Unavailable is null when the caller's raw keys were
        /// actually read; when it is set, an empty Unknown means "not known",
        /// NOT "nothing unknown", and the reply says so out loud.
        /// </summary>
        internal sealed class ArgumentReport
        {
            internal List<string> Unknown = new List<string>();
            internal string Unavailable;
        }

        private static readonly object CacheGate = new object();
        private static readonly Dictionary<string, string[]> DeclaredByToolName =
            new Dictionary<string, string[]>(StringComparer.Ordinal);

        private static bool _journalProbed;
        private static PropertyInfo _journalProperty;

        /// <summary>
        /// The names a tool actually declares as [ToolParameter]s, found by
        /// locating the [Tool] method on the tool class rather than by a hand
        /// written list, so the check cannot drift from the schema. Injected
        /// parameters (IRimBridgeContext, CancellationToken) are not caller
        /// arguments and are excluded, matching the binder's own IsInjectedParameter.
        /// </summary>
        internal static string[] DeclaredParameters(Type toolClass, string toolName)
        {
            if (toolClass == null || string.IsNullOrEmpty(toolName))
                return new string[0];

            lock (CacheGate)
            {
                string[] cached;
                if (DeclaredByToolName.TryGetValue(toolName, out cached))
                    return cached;
            }

            string[] names;
            try
            {
                var method = toolClass
                    .GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly)
                    .FirstOrDefault(m =>
                    {
                        var attribute = m.GetCustomAttribute<ToolAttribute>(false);
                        return attribute != null && string.Equals(attribute.Name, toolName, StringComparison.Ordinal);
                    });

                names = method == null
                    ? new string[0]
                    : method.GetParameters()
                        .Where(p => !typeof(IRimBridgeContext).IsAssignableFrom(p.ParameterType)
                                    && p.ParameterType != typeof(CancellationToken))
                        .Select(p => p.Name ?? string.Empty)
                        .ToArray();
            }
            catch
            {
                names = new string[0];
            }

            lock (CacheGate)
            {
                DeclaredByToolName[toolName] = names;
            }

            return names;
        }

        /// <summary>
        /// The caller's raw argument dictionary for the operation currently
        /// running, or null with a reason. Pure reflection: the companion does
        /// not reference RimBridgeServer.dll or RimBridgeServer.Core.dll, and
        /// RimBridgeCapabilities is internal to the mod assembly.
        /// Chain: RimBridgeServer.RimBridgeCapabilities.Journal (public static
        /// on an internal type) -> OperationJournal.GetOperation(id, false) ->
        /// OperationEnvelope.Metadata["arguments"].
        /// </summary>
        internal static IDictionary<string, object> RawArguments(IRimBridgeContext ctx, out string unavailable)
        {
            unavailable = null;

            string operationId;
            try
            {
                operationId = ctx == null ? null : ctx.OperationId;
            }
            catch (Exception ex)
            {
                unavailable = "the invocation context could not be read (" + ex.GetType().Name + ")";
                return null;
            }

            if (string.IsNullOrEmpty(operationId))
            {
                unavailable = "this invocation carries no operation id";
                return null;
            }

            try
            {
                var journalProperty = JournalProperty();
                if (journalProperty == null)
                {
                    unavailable = "RimBridgeServer.RimBridgeCapabilities.Journal was not found in the loaded bridge assembly";
                    return null;
                }

                var journal = journalProperty.GetValue(null, null);
                if (journal == null)
                {
                    unavailable = "the bridge operation journal is not initialised";
                    return null;
                }

                var getOperation = journal.GetType().GetMethod(
                    "GetOperation",
                    BindingFlags.Public | BindingFlags.Instance,
                    null,
                    new[] { typeof(string), typeof(bool) },
                    null);
                if (getOperation == null)
                {
                    unavailable = "OperationJournal.GetOperation(string, bool) was not found";
                    return null;
                }

                var envelope = getOperation.Invoke(journal, new object[] { operationId, false });
                if (envelope == null)
                {
                    unavailable = "operation " + operationId + " was not in the journal";
                    return null;
                }

                var metadataProperty = envelope.GetType().GetProperty("Metadata", BindingFlags.Public | BindingFlags.Instance);
                var metadata = metadataProperty == null
                    ? null
                    : metadataProperty.GetValue(envelope, null) as IDictionary<string, object>;
                if (metadata == null)
                {
                    unavailable = "the journalled operation carried no metadata dictionary";
                    return null;
                }

                object raw;
                if (!metadata.TryGetValue("arguments", out raw))
                {
                    unavailable = "the journalled operation carried no arguments entry";
                    return null;
                }

                var arguments = raw as IDictionary<string, object>;
                if (arguments == null)
                {
                    unavailable = "the journalled arguments were not a string-keyed dictionary";
                    return null;
                }

                return arguments;
            }
            catch (Exception ex)
            {
                unavailable = "reading the bridge operation journal threw " + ex.GetType().Name;
                return null;
            }
        }

        private static PropertyInfo JournalProperty()
        {
            lock (CacheGate)
            {
                if (_journalProbed)
                    return _journalProperty;

                _journalProbed = true;
                try
                {
                    var assembly = AppDomain.CurrentDomain
                        .GetAssemblies()
                        .FirstOrDefault(a =>
                        {
                            try { return string.Equals(a.GetName().Name, "RimBridgeServer", StringComparison.Ordinal); }
                            catch { return false; }
                        });
                    if (assembly == null)
                        return null;

                    var type = assembly.GetType("RimBridgeServer.RimBridgeCapabilities", false);
                    if (type == null)
                        return null;

                    _journalProperty = type.GetProperty("Journal", BindingFlags.Public | BindingFlags.Static);
                }
                catch
                {
                    _journalProperty = null;
                }

                return _journalProperty;
            }
        }

        /// <summary>Diff the caller's keys against what the tool declares.</summary>
        internal static ArgumentReport Inspect(IRimBridgeContext ctx, Type toolClass, string toolName)
        {
            var report = new ArgumentReport();

            string unavailable;
            var arguments = RawArguments(ctx, out unavailable);
            if (arguments == null)
            {
                report.Unavailable = unavailable ?? "the caller's argument keys could not be read";
                return report;
            }

            var declared = new HashSet<string>(DeclaredParameters(toolClass, toolName), StringComparer.Ordinal);
            foreach (var key in arguments.Keys)
            {
                if (string.IsNullOrEmpty(key))
                    continue;
                if (string.Equals(key, HostTimeoutArgument, StringComparison.Ordinal))
                    continue;
                if (declared.Contains(key))
                    continue;

                report.Unknown.Add(key);
            }

            report.Unknown.Sort(StringComparer.Ordinal);
            return report;
        }

        /// <summary>
        /// Attach unknownArguments[] (always) and unknownArgumentsWarning (only
        /// when there is something to say) to a finished reply. Wrapped around a
        /// tool's whole body so failures carry it too. Argument binding is
        /// case-sensitive in the binder, so "MaxRows" really is an unknown key
        /// and saying so is the point.
        /// </summary>
        internal static object WithUnknownArguments(object reply, IRimBridgeContext ctx, Type toolClass, string toolName)
        {
            Dictionary<string, object> payload;
            try
            {
                payload = AsDictionary(reply);
            }
            catch
            {
                return reply;
            }

            if (payload == null)
                return reply;

            try
            {
                var report = Inspect(ctx, toolClass, toolName);
                payload["unknownArguments"] = report.Unknown;

                if (report.Unavailable != null)
                {
                    payload["unknownArgumentsWarning"] =
                        "Unrecognised arguments could NOT be checked on this call (" + report.Unavailable
                        + "), so the empty unknownArguments list means 'not known', not 'nothing unknown'.";
                }
                else if (report.Unknown.Count > 0)
                {
                    payload["unknownArgumentsWarning"] =
                        toolName + " ignored " + report.Unknown.Count + " unrecognised argument"
                        + (report.Unknown.Count == 1 ? "" : "s") + ": " + string.Join(", ", report.Unknown.ToArray())
                        + ". Names are case-sensitive. This tool accepts: "
                        + string.Join(", ", DeclaredParameters(toolClass, toolName)) + ".";
                }
            }
            catch (Exception ex)
            {
                payload["unknownArguments"] = new List<string>();
                payload["unknownArgumentsWarning"] =
                    "The unknown-argument check itself threw " + ex.GetType().Name
                    + ", so the empty unknownArguments list means 'not known', not 'nothing unknown'.";
            }

            return payload;
        }

        /// <summary>
        /// A reply as a mutable dictionary. A Dictionary is returned as-is so key
        /// order and values are untouched; anything else (an anonymous type) is
        /// flattened over its public readable properties in declaration order —
        /// which is exactly what RimBridgeServer.LegacyToolExecution does to it
        /// one layer up, so the JSON on the wire is unchanged.
        /// </summary>
        internal static Dictionary<string, object> AsDictionary(object reply)
        {
            if (reply == null)
                return null;

            var already = reply as Dictionary<string, object>;
            if (already != null)
                return already;

            var typed = reply as IDictionary<string, object>;
            if (typed != null)
            {
                var copy = new Dictionary<string, object>(StringComparer.Ordinal);
                foreach (var pair in typed)
                    copy[pair.Key] = pair.Value;
                return copy;
            }

            var type = reply.GetType();
            if (type.IsPrimitive || type.IsEnum || type == typeof(string) || type == typeof(decimal)
                || type == typeof(DateTime) || type == typeof(DateTimeOffset) || type == typeof(Guid)
                || type == typeof(TimeSpan))
            {
                return null;
            }

            var flattened = new Dictionary<string, object>(StringComparer.Ordinal);
            foreach (var property in type.GetProperties(BindingFlags.Instance | BindingFlags.Public))
            {
                if (!property.CanRead || property.GetIndexParameters().Length != 0)
                    continue;

                try { flattened[property.Name] = property.GetValue(reply, null); }
                catch { flattened[property.Name] = null; }
            }

            return flattened.Count == 0 ? null : flattened;
        }

        // ------------------------------------------------------------------
        // Reply pieces
        // ------------------------------------------------------------------

        /// <summary>The refusal shape every tool returns: success false, which
        /// tool refused, and why. Not an exception: a refusal is an answer.</summary>
        internal static Dictionary<string, object> Failure(string toolName, string error)
        {
            return new Dictionary<string, object>
            {
                { "success", false },
                { "tool", toolName },
                { "error", error }
            };
        }

        /// <summary>A cell as {x, z}. The only position shape any tool emits.</summary>
        internal static Dictionary<string, object> Pos(IntVec3 cell)
        {
            return new Dictionary<string, object> { { "x", cell.x }, { "z", cell.z } };
        }

        /// <summary>A thing's cell as {x, z}, or null if the getter throws (an
        /// unspawned or despawning thing does).</summary>
        internal static Dictionary<string, object> PositionOf(Thing thing)
        {
            try
            {
                var p = thing.Position;
                return new Dictionary<string, object> { { "x", p.x }, { "z", p.z } };
            }
            catch { return null; }
        }

        /// <summary>The standard "is there a map to read" gate. The error text is
        /// the caller's, not an exception's, and names the tool that refused.</summary>
        internal static bool TryGetMap(string toolName, out Map map, out string error)
        {
            map = null;
            error = string.Empty;

            if (Current.Game == null)
            {
                error = "No game is currently loaded.";
                return false;
            }

            if (Current.ProgramState != ProgramState.Playing || Find.CurrentMap == null)
            {
                error = toolName + " requires an active map.";
                return false;
            }

            map = Find.CurrentMap;
            return true;
        }

        // ------------------------------------------------------------------
        // Guards
        // ------------------------------------------------------------------

        /// <summary>
        /// Swallow-and-return-default. Every game read in this companion is
        /// behind one: an exception thrown inside a companion read reaches
        /// Verse.Log.Error, which calls TickManager.Pause(), which the harness
        /// reads as a person pausing the game.
        /// </summary>
        internal static T Try<T>(Func<T> read, T fallback)
        {
            try { return read(); }
            catch { return fallback; }
        }

        /// <summary>Same guard, but a throwing read becomes null rather than a
        /// value that cannot be told apart from a real one.</summary>
        internal static T? TryN<T>(Func<T> read) where T : struct
        {
            try { return read(); }
            catch { return null; }
        }

        /// <summary>A string read that becomes null rather than throwing.</summary>
        internal static string SafeString(Func<string> read)
        {
            try { return read(); }
            catch { return null; }
        }

        // ------------------------------------------------------------------
        // Reflection into RimWorld's private state
        // ------------------------------------------------------------------

        /// <summary>A private instance field, or null if the game renamed it.
        /// Callers report the null rather than emitting an empty result that
        /// would read as "nothing there".</summary>
        internal static FieldInfo PrivateInstanceField(Type type, string name)
        {
            try { return type == null ? null : type.GetField(name, BindingFlags.NonPublic | BindingFlags.Instance); }
            catch { return null; }
        }

        /// <summary>A private static field, same contract.</summary>
        internal static FieldInfo PrivateStaticField(Type type, string name)
        {
            try { return type == null ? null : type.GetField(name, BindingFlags.NonPublic | BindingFlags.Static); }
            catch { return null; }
        }

        /// <summary>A private instance property, same contract.</summary>
        internal static PropertyInfo PrivateInstanceProperty(Type type, string name)
        {
            try { return type == null ? null : type.GetProperty(name, BindingFlags.NonPublic | BindingFlags.Instance); }
            catch { return null; }
        }

        // ------------------------------------------------------------------
        // Reading values back out of a payload dictionary
        // ------------------------------------------------------------------

        /// <summary>A bool out of a payload dictionary; absent or another type
        /// reads as false.</summary>
        internal static bool Bool(Dictionary<string, object> d, string key)
        {
            object v;
            return d != null && d.TryGetValue(key, out v) && v is bool && (bool)v;
        }

        /// <summary>A double out of a payload dictionary; absent or another type
        /// reads as 0.</summary>
        internal static double Num(Dictionary<string, object> d, string key)
        {
            object v;
            if (d != null && d.TryGetValue(key, out v) && v is double)
                return (double)v;
            return 0.0;
        }

        // ------------------------------------------------------------------
        // Construction deficit — ONE definition, two tools
        // ------------------------------------------------------------------

        /// <summary>
        /// How much of <paramref name="def"/> this blueprint or frame is still
        /// waiting to be delivered. <c>IConstructible.ThingCountNeeded</c> is
        /// implemented by both <c>Blueprint</c> and <c>Frame</c> (and so by
        /// <c>Blueprint_Install</c> and <c>Blueprint_Storage</c>, which subclass
        /// them), which is why one call covers every construction site.
        ///
        /// This lives here rather than in either tool because
        /// <c>home/list_buildings</c> reports it per site as
        /// <c>resources[].stillNeeded</c> and <c>home/place_building</c> sums it
        /// map-wide as <c>materials[].reservedByOtherBlueprints</c>. Two copies
        /// of this arithmetic would eventually disagree, and the whole point of
        /// the second number is that it agrees with the first.
        ///
        /// A throwing read falls back to the full <paramref name="need"/> — the
        /// pessimistic answer, never a confident zero.
        /// </summary>
        internal static int ConstructibleStillNeeded(RimWorld.IConstructible constructible, ThingDef def, int need)
        {
            var stillNeeded = need;
            try
            {
                if (constructible != null && def != null && !IsInstallBlueprint(constructible))
                    stillNeeded = constructible.ThingCountNeeded(def);
            }
            catch { stillNeeded = need; }
            return stillNeeded < 0 ? 0 : stillNeeded;
        }

        /// <summary>
        /// Every resource every blueprint and frame on the map is still waiting
        /// for, summed by def. This is the same number
        /// <c>home/list_buildings</c> rolls up as <c>resourceDeficit</c>, built
        /// from the same two calls, so the two tools cannot drift apart.
        ///
        /// <c>Blueprint</c> and <c>Frame</c> are NOT in
        /// <c>ThingRequestGroup.BuildingArtificial</c>; they have their own
        /// groups, <c>Blueprint</c> and <c>BuildingFrame</c>. Asking only for the
        /// first would silently miss half the sites.
        ///
        /// One pass over two lister groups. The caller does this ONCE per call,
        /// never per rotation.
        /// </summary>
        /// <summary>
        /// A reinstall blueprint, whose material cost MUST NOT be asked for.
        ///
        /// <c>Blueprint_Install.TotalMaterialCost()</c> is, in full,
        /// <c>Log.Error("Called MaterialsNeededTotal on a Blueprint_Install."); return new List&lt;&gt;();</c>
        /// — and <c>Verse.Log.Error</c> calls <c>TickManager.Pause()</c>. So
        /// asking a reinstall blueprint what it needs PAUSES THE COLONY, which
        /// the harness reads as a person pressing space. Vanilla guards the same
        /// way: <c>GenConstruct.CanGetResources_NewTemp</c> opens with
        /// <c>if (thing is Blueprint_Install) return true;</c>.
        ///
        /// A reinstall costs nothing anyway — it moves a minified thing that
        /// already exists — so skipping it loses no information.
        /// </summary>
        internal static bool IsInstallBlueprint(object constructible)
        {
            try { return constructible is RimWorld.Blueprint_Install; }
            catch { return false; }
        }

        internal static Dictionary<ThingDef, int> OutstandingConstructionDeficit(Map map)
        {
            var totals = new Dictionary<ThingDef, int>();
            if (map == null)
                return totals;

            var sites = new List<Thing>();
            try
            {
                foreach (var group in new[] { ThingRequestGroup.Blueprint, ThingRequestGroup.BuildingFrame })
                {
                    var found = map.listerThings.ThingsInGroup(group);
                    if (found == null)
                        continue;
                    for (var i = 0; i < found.Count; i++)
                        if (found[i] != null && !sites.Contains(found[i]))
                            sites.Add(found[i]);
                }
            }
            catch { return totals; }

            foreach (var thing in sites)
            {
                var constructible = thing as RimWorld.IConstructible;
                if (constructible == null || IsInstallBlueprint(constructible))
                    continue;

                List<ThingDefCountClass> cost;
                try { cost = constructible.TotalMaterialCost(); }
                catch { continue; }
                if (cost == null)
                    continue;

                foreach (var item in cost)
                {
                    if (item == null || item.thingDef == null)
                        continue;
                    var stillNeeded = ConstructibleStillNeeded(constructible, item.thingDef, item.count);
                    if (stillNeeded <= 0)
                        continue;
                    int running;
                    totals[item.thingDef] = totals.TryGetValue(item.thingDef, out running)
                        ? running + stillNeeded
                        : stillNeeded;
                }
            }
            return totals;
        }
    }

    /// <summary>
    /// One walk of a rectangle, resolving each cell to the room that covers it.
    /// Lifted out of home/get_temperatures {mode:rooms} so home/list_rooms and
    /// the temperature tool resolve rooms by exactly the same code, in exactly
    /// the same order.
    ///
    /// What it produces, and why in this shape:
    ///
    ///   * <see cref="Rooms"/> — one entry per DISTINCT room touching the rect,
    ///     numbered 0..n-1 in RASTER ORDER OF FIRST APPEARANCE. That ordering is
    ///     load-bearing: get_temperatures' documented `roomGrid` promises that
    ///     its first non-null value is always 0, and a caller correlating two
    ///     calls over the same rect relies on the numbering being a function of
    ///     the rect, not of a dictionary's iteration order.
    ///   * <see cref="Grid"/> — row-major rows of those indexes, `null` where the
    ///     cell is in no room at all. Never a short row: an omitted entry would
    ///     be indistinguishable from an unroomed cell.
    ///   * <see cref="CellsWithNoRoom"/> — how many nulls, so a caller can tell
    ///     "the rect is mostly wall" from "the room lookup failed".
    ///
    /// Reads only <see cref="GridsUtility.GetRoom(IntVec3, Map)"/> (two args in
    /// 1.6; it forwards to RegionAndRoomQuery.RoomAt) and <see cref="Room.ID"/>,
    /// which is a public FIELD, not a property. Both are behind a guard: a room
    /// lookup that throws becomes an unroomed cell rather than a failed reply.
    ///
    /// It does NOT read Room.Cells. A rect walk asks "what is in these cells";
    /// walking each room's own cell list would answer a different question and
    /// would leave the rect's own coverage unaccounted for.
    /// </summary>
    internal sealed class RoomWalk
    {
        /// <summary>One distinct room found inside the rectangle, with the
        /// bookkeeping a legend line needs: how many of its cells fell inside
        /// the rect, a cell guaranteed to be in both the room and the rect, and
        /// a centroid snapped to a real in-rect cell.</summary>
        internal sealed class Entry
        {
            // In-rect cells, kept internally only. Deliberately NOT emitted by
            // either consumer: an index grid says the same thing in half the
            // tokens. It exists so Center() can snap to a real cell without a
            // second pass over the rect.
            private readonly List<IntVec3> _cellsInRect = new List<IntVec3>();
            private long _sumX;
            private long _sumZ;

            internal Entry(int index, int id, Room room, IntVec3 representativeCell)
            {
                Index = index;
                Id = id;
                Room = room;
                RepresentativeCell = representativeCell;
            }

            internal int Index { get; private set; }
            internal int Id { get; private set; }
            internal Room Room { get; private set; }
            internal IntVec3 RepresentativeCell { get; private set; }
            internal int CellsInRect { get; private set; }

            internal void AddCell(IntVec3 cell)
            {
                CellsInRect++;
                _cellsInRect.Add(cell);
                _sumX += cell.x;
                _sumZ += cell.z;
            }

            /// <summary>
            /// Rough centroid of the in-rect cells, snapped to the in-rect cell
            /// nearest it. M asked for "a rough center coordinate (or just
            /// the top left corner)"; the centroid is the better legend anchor, and
            /// the snap is what stops an L-shaped or ring-shaped room from placing
            /// its label in the hole.
            /// </summary>
            internal IntVec3 Center()
            {
                if (_cellsInRect.Count == 0)
                    return RepresentativeCell;

                var centroidX = (int)Math.Round((double)_sumX / _cellsInRect.Count, MidpointRounding.AwayFromZero);
                var centroidZ = (int)Math.Round((double)_sumZ / _cellsInRect.Count, MidpointRounding.AwayFromZero);

                var best = _cellsInRect[0];
                var bestScore = long.MaxValue;
                for (var i = 0; i < _cellsInRect.Count; i++)
                {
                    var cell = _cellsInRect[i];
                    long dx = cell.x - centroidX;
                    long dz = cell.z - centroidZ;
                    var score = dx * dx + dz * dz;
                    if (score == 0)
                        return cell;                    // the centroid is itself a room cell
                    if (score < bestScore)
                    {
                        bestScore = score;
                        best = cell;
                    }
                }
                return best;
            }
        }

        private RoomWalk() { }

        internal List<Entry> Rooms { get; private set; }
        internal List<List<object>> Grid { get; private set; }
        internal int CellsWithNoRoom { get; private set; }

        /// <summary>Walk the rectangle row-major, bottom row (rect.z) first, the
        /// same order both consumers document for their grids.</summary>
        internal static RoomWalk OverRect(Map map, int x, int z, int width, int height)
        {
            var walk = new RoomWalk
            {
                Rooms = new List<Entry>(),
                Grid = new List<List<object>>(height < 0 ? 0 : height)
            };

            // Response-local index per distinct room, assigned in raster order so
            // the grid's first non-null value is always 0.
            var indexByRoomId = new Dictionary<int, int>();

            for (var offsetZ = 0; offsetZ < height; offsetZ++)
            {
                var row = new List<object>(width < 0 ? 0 : width);
                for (var offsetX = 0; offsetX < width; offsetX++)
                {
                    var cell = new IntVec3(x + offsetX, 0, z + offsetZ);
                    var room = RoomAt(map, cell);
                    if (room == null)
                    {
                        walk.CellsWithNoRoom++;
                        row.Add(null);          // explicit, never omitted
                        continue;
                    }

                    var roomId = RoomId(room);
                    if (roomId == null)
                    {
                        walk.CellsWithNoRoom++;
                        row.Add(null);
                        continue;
                    }

                    int index;
                    if (!indexByRoomId.TryGetValue(roomId.Value, out index))
                    {
                        index = walk.Rooms.Count;
                        indexByRoomId[roomId.Value] = index;
                        walk.Rooms.Add(new Entry(index, roomId.Value, room, cell));
                    }

                    walk.Rooms[index].AddCell(cell);
                    row.Add(index);
                }
                walk.Grid.Add(row);
            }

            return walk;
        }

        /// <summary>The room covering a cell, or null. GridsUtility.GetRoom takes
        /// (IntVec3, Map) in 1.6 — there is no RegionType overload — and forwards
        /// to RegionAndRoomQuery.RoomAt, so this is that call.</summary>
        internal static Room RoomAt(Map map, IntVec3 cell)
        {
            try { return cell.GetRoom(map); }
            catch { return null; }
        }

        /// <summary>Room.ID is a public FIELD. Null here means the read threw,
        /// which is a different thing from a room whose id happens to be 0.</summary>
        internal static int? RoomId(Room room)
        {
            try { return room.ID; }
            catch { return null; }
        }

    }
}
