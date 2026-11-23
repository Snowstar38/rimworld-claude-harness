using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using RimBridgeServer.Sdk;
using RimWorld;
using RimWorld.Planet;
using UnityEngine;
using Verse;

namespace HomeBridge.BridgeTools
{
    /// <summary>
    /// home/world — the world-map tile readout for the colony's own tile,
    /// without opening the world view.
    ///
    /// The bridge has no world tool at all, and the world tab cannot be reached
    /// through the UI tools: `list_main_tabs` gives World `type: ""` because it
    /// is a MainButtonDef whose worker toggles the planet renderer rather than
    /// opening a tab window, so `open_main_tab` null-refs on it and
    /// `click_ui_target` has no ui-element id for it. Every fact the world
    /// inspect pane shows — biome, temperature, growing period, rainfall,
    /// elevation, hilliness, nearby settlements — is readable off
    /// `Find.WorldGrid` and `Find.WorldObjects` with the map still on screen.
    ///
    /// `show: true` with `dryRun: false` is the only write: it toggles the
    /// planet view on for `watchSeconds` and then hides it again.
    ///
    /// Verified against the installed Assembly-CSharp.dll:
    ///   RimWorld.Planet.WorldGrid.LongLatOf(PlanetTile) -> Vector2 (x = longitude)
    ///   WorldGrid.get_Item(PlanetTile) -> RimWorld.Planet.Tile, whose fields are
    ///     biome, elevation, hilliness, temperature, rainfall, swampiness,
    ///     pollution, and whose PrimaryBiome property is the 1.6 multi-biome read
    ///   WorldGrid.ApproxDistanceInTiles(PlanetTile, PlanetTile) -> float
    ///   Verse.GenTemperature.GetTemperatureFromSeasonAtTile(int absTicks, PlanetTile)
    ///   GenTemperature.TwelfthsInAverageTemperatureRange(PlanetTile, float, float)
    ///   GenTemperature.GetAverageTemperatureLabel(PlanetTile) -> string
    ///   RimWorld.Plant.DefaultMinOptimalGrowthTemperature / ...Max... (float fields)
    ///   RimWorld.GenDate.DaysPerTwelfth (int), TwelfthsPerYear (int)
    ///   Verse.CameraJumper.TryShowWorld() / TryHideWorld() -> bool
    ///   RimWorld.Planet.World.renderer.wantedMode (WorldRenderMode field)
    ///
    /// TickManager.TicksAbs is read only when gameStartAbsTick is non-zero: its
    /// getter reaches Log.Error, which calls TickManager.Pause(). See
    /// home/get_time. Without it the seasonal temperature is null rather than
    /// computed at tick 0.
    /// </summary>
    public sealed class HomeWorldTools
    {
        private const string ToolName = "home/world";

        /// <summary>Plant.DefaultMin/MaxOptimalGrowthTemperature, the range the
        /// game's own growing-period readout uses.</summary>
        private const float GrowMinDefault = 6f;
        private const float GrowMaxDefault = 42f;

        [Tool(
            ToolName,
            Title = "Read the world-map tile without opening the world view",
            Description =
                "The world inspect pane's facts for one planet tile, read off the world grid with the colony map still on "
                + "screen: biome, hilliness, elevation, rainfall, swampiness, pollution, longitude/latitude, the current "
                + "seasonal temperature, the tile's average/min/max temperatures, the growing period in days, and the "
                + "settlements within settlementRadius tiles with their factions and distances. Defaults to the current "
                + "map's tile. Read-only unless show:true is sent with dryRun:false, which toggles the planet view on for "
                + "watchSeconds and then hides it again.",
            ResultDescription =
                "success, tool, status ('game_loaded' or 'no_game'), hasMap, tile, tileValid, longitude, latitude; biome, "
                + "biomeDefName, hilliness, elevation, rainfall, swampiness, pollution, coastal; temperature, "
                + "averageTemperatureLabel, minTemperature, maxTemperature; growingPeriodDays, growingTwelfths, "
                + "growingPeriodLabel, growingRangeC; settlements[], settlementRadius, settlementsNotListed; worldView; "
                + "dryRun, notes.")]
        [ToolResponse("status", "string", "'game_loaded' when a game is loaded, 'no_game' when none is. Never absent.", Always = true)]
        [ToolResponse("tile", "number", "The planet tile id this readout is for. Null when there is no map and no tile was named.", Always = true, Nullable = true)]
        [ToolResponse("biome", "string", "The tile's primary biome, in the game's own words. Null when the tile could not be read.", Always = true, Nullable = true)]
        [ToolResponse("hilliness", "string", "Flat, SmallHills, LargeHills, Mountainous or Impassable. Null when the tile could not be read.", Always = true, Nullable = true)]
        [ToolResponse("temperature", "number", "The tile's temperature right now for the season, in Celsius. Null when absolute ticks are unavailable (see home/get_time).", Always = true, Nullable = true)]
        [ToolResponse("growingPeriodDays", "number", "Days per year the tile's average temperature is inside the growing range. 0 is a real answer: nothing grows outdoors here. Null when it could not be computed.", Always = true, Nullable = true)]
        [ToolResponse("growingTwelfths", "array", "The twelfths of the year inside the growing range, by name. Empty means none; each is DaysPerTwelfth (5) days.", Always = true)]
        [ToolResponse("settlements", "array", "Settlements within settlementRadius tiles: label, faction, factionDef, relation, goodwill, tile, distanceTiles, isPlayer. Sorted nearest first.", Always = true)]
        [ToolResponse("worldView", "object", "What the planet view was made to do: shown, wantedMode, watchSeconds, hidden, and a reason when nothing was shown. shown is false on every read-only call.", Always = true)]
        [ToolResponse("dryRun", "boolean", "True = the planet view was not toggled. Defaults to TRUE; show:true needs dryRun:false to actually run.", Always = true)]
        [ToolResponse("unknownArguments", "array", "Every argument key the caller sent that this tool does not declare, sorted, case-sensitively. Empty array = the call was clean. The host's own _rimBridgeTimeoutMs is never listed.", Always = true)]
        [ToolResponse("unknownArgumentsWarning", "string", "Present only when unknownArguments is non-empty, or when the caller's raw keys could not be read at all - in which case the empty unknownArguments means 'not known', not 'nothing unknown'.", Nullable = true)]
        public async Task<object> World(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            [ToolParameter(Description = "Planet tile id to read. Omit (or -1) for the current map's own tile.", DefaultValue = -1)] int tile = -1,
            [ToolParameter(Description = "List settlements within this many tiles of the read tile. 0 lists none.", DefaultValue = 15)] int settlementRadius = 15,
            [ToolParameter(Description = "Show the planet view for watchSeconds and then hide it again. Needs dryRun:false; ignored on a dry run.", DefaultValue = false)] bool show = false,
            [ToolParameter(Description = "How long the planet view stays up, in seconds (1..60).", DefaultValue = Watch.DefaultSeconds)] int watchSeconds = Watch.DefaultSeconds,
            [ToolParameter(Description = "TRUE by default. The readout is read-only either way; this gates show:true only.", DefaultValue = true)] bool dryRun = true)
        {
            return BridgeCommon.WithUnknownArguments(
                await WorldCore(ctx, cancellationToken, tile, settlementRadius,
                                show, watchSeconds, dryRun).ConfigureAwait(false),
                ctx, typeof(HomeWorldTools), ToolName);
        }

        private async Task<object> WorldCore(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            int tile,
            int settlementRadius,
            bool show,
            int watchSeconds,
            bool dryRun)
        {
            if (ctx?.MainThread == null)
                return Failure("No RimBridge main-thread dispatcher is available for this invocation.");

            // Companion tools are dispatched with MarshalToMainThread = false, so
            // every world-grid read is hopped by hand. One hop = one consistent
            // instant of a clock that may be running.
            var payload = await ctx.MainThread
                .InvokeAsync(() => BuildResponse(tile, settlementRadius, dryRun),
                             cancellationToken)
                .ConfigureAwait(false);

            var wantShow = show && !dryRun;
            if (!wantShow)
            {
                payload["worldView"] = ViewBlock(false, null, 0, false,
                    !show ? "show:false" : "dry run: pass dryRun:false to toggle the planet view");
                return payload;
            }

            var seconds = watchSeconds < 1 ? 1 : (watchSeconds > 60 ? 60 : watchSeconds);
            var shown = await ctx.MainThread
                .InvokeAsync(() => ShowWorld(), cancellationToken)
                .ConfigureAwait(false);
            if (!shown.Ok)
            {
                payload["worldView"] = ViewBlock(false, shown.Mode, seconds, false, shown.Error);
                return payload;
            }

            // Off the main thread, so the planet is actually on screen for the
            // duration rather than blocking the tick that draws it.
            try
            {
                await Task.Delay(seconds * 1000, cancellationToken).ConfigureAwait(false);
            }
            catch (Exception)
            {
                // A cancelled wait must still hide the world again.
            }

            var hidden = await ctx.MainThread
                .InvokeAsync(() => HideWorld(), cancellationToken)
                .ConfigureAwait(false);
            payload["worldView"] = ViewBlock(true, shown.Mode, seconds, hidden.Ok,
                                             hidden.Ok ? null : hidden.Error);
            return payload;
        }

        // ------------------------------------------------------------------
        // the readout
        // ------------------------------------------------------------------

        private static Dictionary<string, object> BuildResponse(int wantTile, int settlementRadius, bool dryRun)
        {
            // Every key is written unconditionally, including the nulls, so a
            // caller can never mistake "the tool did not look" for a real zero.
            var payload = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["success"] = true,
                ["tool"] = ToolName,
                ["dryRun"] = dryRun
            };

            if (BridgeCommon.Try(() => Current.Game, (Game)null) == null)
            {
                payload["status"] = "no_game";
                WriteEmpty(payload, settlementRadius);
                payload["notes"] = Notes("Current.Game is null: no save is loaded. Every field is null, and success is still true because 'no game' is an answer, not a tool failure.");
                return payload;
            }

            payload["status"] = "game_loaded";

            var map = SafeMap();
            payload["hasMap"] = map != null;

            PlanetTile tile;
            if (!TryTile(wantTile, map, out tile))
            {
                WriteEmpty(payload, settlementRadius);
                payload["notes"] = Notes("No valid planet tile: no map is loaded and no tile id was given.");
                return payload;
            }

            payload["tile"] = BridgeCommon.Try(() => tile.tileId, -1);
            payload["tileValid"] = BridgeCommon.Try(() => tile.Valid, false);

            WriteLongLat(payload, tile);
            WriteTerrain(payload, tile);
            WriteTemperature(payload, tile);
            WriteGrowing(payload, tile);
            WriteSettlements(payload, tile, settlementRadius);

            payload["notes"] = Notes(null);
            return payload;
        }

        private static void WriteEmpty(IDictionary<string, object> payload, int settlementRadius)
        {
            payload["hasMap"] = payload.ContainsKey("hasMap") && (bool)payload["hasMap"];
            payload["tile"] = null;
            payload["tileValid"] = false;
            WriteLongLat(payload, null);
            WriteTerrain(payload, null);
            WriteTemperature(payload, null);
            WriteGrowing(payload, null);
            payload["settlements"] = new List<object>();
            payload["settlementRadius"] = settlementRadius;
            payload["settlementsNotListed"] = 0;
        }

        private static void WriteLongLat(IDictionary<string, object> payload, PlanetTile? tile)
        {
            Vector2 longLat;
            if (tile.HasValue && TryLongLat(tile.Value, out longLat))
            {
                payload["longitude"] = Round(longLat.x);
                payload["latitude"] = Round(longLat.y);
            }
            else
            {
                payload["longitude"] = null;
                payload["latitude"] = null;
            }
        }

        private static void WriteTerrain(IDictionary<string, object> payload, PlanetTile? planetTile)
        {
            var row = planetTile.HasValue ? SafeTile(planetTile.Value) : null;
            if (row == null)
            {
                payload["biome"] = null;
                payload["biomeDefName"] = null;
                payload["hilliness"] = null;
                payload["elevation"] = null;
                payload["rainfall"] = null;
                payload["swampiness"] = null;
                payload["pollution"] = null;
                payload["coastal"] = null;
                return;
            }

            // PrimaryBiome, not the private `biome` field: a 1.6 tile can carry
            // more than one biome and the property is the game's own answer.
            var biome = BridgeCommon.Try(() => row.PrimaryBiome, (BiomeDef)null);
            payload["biome"] = biome == null ? null : BridgeCommon.SafeString(() => biome.LabelCap.ToString());
            payload["biomeDefName"] = biome == null ? null : BridgeCommon.SafeString(() => biome.defName);
            payload["hilliness"] = BridgeCommon.SafeString(() => row.hilliness.ToString());
            payload["elevation"] = RoundN(BridgeCommon.TryN(() => row.elevation));
            payload["rainfall"] = RoundN(BridgeCommon.TryN(() => row.rainfall));
            payload["swampiness"] = RoundN(BridgeCommon.TryN(() => row.swampiness));
            payload["pollution"] = RoundN(BridgeCommon.TryN(() => row.pollution));
            payload["coastal"] = BridgeCommon.TryN(() => row.IsCoastal);
        }

        private static void WriteTemperature(IDictionary<string, object> payload, PlanetTile? planetTile)
        {
            if (!planetTile.HasValue)
            {
                payload["temperature"] = null;
                payload["averageTemperatureLabel"] = null;
                payload["minTemperature"] = null;
                payload["maxTemperature"] = null;
                return;
            }

            var tile = planetTile.Value;
            var abs = AbsTicks();
            payload["temperature"] = abs.HasValue
                ? RoundN(BridgeCommon.TryN(() => GenTemperature.GetTemperatureFromSeasonAtTile(abs.Value, tile)))
                : null;
            payload["averageTemperatureLabel"] =
                BridgeCommon.SafeString(() => GenTemperature.GetAverageTemperatureLabel(tile));

            var row = SafeTile(tile);
            payload["minTemperature"] = row == null ? null : RoundN(BridgeCommon.TryN(() => row.MinTemperature));
            payload["maxTemperature"] = row == null ? null : RoundN(BridgeCommon.TryN(() => row.MaxTemperature));
        }

        private static void WriteGrowing(IDictionary<string, object> payload, PlanetTile? planetTile)
        {
            payload["growingRangeC"] = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "min", GrowMin() },
                { "max", GrowMax() }
            };

            if (!planetTile.HasValue)
            {
                payload["growingTwelfths"] = new List<object>();
                payload["growingPeriodDays"] = null;
                payload["growingPeriodLabel"] = null;
                return;
            }

            var tile = planetTile.Value;
            List<Twelfth> twelfths = null;
            try
            {
                twelfths = GenTemperature.TwelfthsInAverageTemperatureRange(tile, GrowMin(), GrowMax());
            }
            catch
            {
                twelfths = null;
            }

            if (twelfths == null)
            {
                payload["growingTwelfths"] = new List<object>();
                payload["growingPeriodDays"] = null;
                payload["growingPeriodLabel"] = null;
                return;
            }

            var names = twelfths.Select(t => (object)t.ToString()).ToList();
            var perTwelfth = BridgeCommon.Try(() => GenDate.DaysPerTwelfth, 5);
            var perYear = BridgeCommon.Try(() => GenDate.TwelfthsPerYear, 12);
            var days = twelfths.Count * perTwelfth;

            payload["growingTwelfths"] = names;
            payload["growingPeriodDays"] = days;
            payload["growingPeriodLabel"] =
                twelfths.Count >= perYear ? "year round (" + days + " days)"
                : twelfths.Count == 0 ? "never: no twelfth of the year is inside the growing range"
                : days + " days";
        }

        private static void WriteSettlements(IDictionary<string, object> payload, PlanetTile tile, int radius)
        {
            payload["settlementRadius"] = radius;
            var rows = new List<object>();
            var notListed = 0;

            if (radius <= 0)
            {
                payload["settlements"] = rows;
                payload["settlementsNotListed"] = 0;
                return;
            }

            try
            {
                var holder = Find.WorldObjects;
                var grid = Find.WorldGrid;
                if (holder == null || grid == null)
                {
                    payload["settlements"] = rows;
                    payload["settlementsNotListed"] = 0;
                    return;
                }

                var near = new List<KeyValuePair<float, object>>();
                foreach (var settlement in holder.Settlements)
                {
                    if (settlement == null || BridgeCommon.Try(() => settlement.Destroyed, false))
                        continue;
                    var at = BridgeCommon.TryN(() => settlement.Tile);
                    if (!at.HasValue)
                        continue;
                    var distance = BridgeCommon.TryN(() => grid.ApproxDistanceInTiles(tile, at.Value));
                    if (!distance.HasValue || distance.Value > radius)
                        continue;
                    near.Add(new KeyValuePair<float, object>(distance.Value,
                                                             Describe(settlement, at.Value, distance.Value)));
                }

                near.Sort((a, b) => a.Key.CompareTo(b.Key));
                foreach (var entry in near)
                {
                    if (rows.Count >= 40)
                    {
                        notListed++;
                        continue;
                    }
                    rows.Add(entry.Value);
                }
            }
            catch
            {
                // A world with no settlement list is an empty list, not a failure.
            }

            payload["settlements"] = rows;
            payload["settlementsNotListed"] = notListed;
        }

        private static object Describe(Settlement settlement, PlanetTile at, float distance)
        {
            var faction = BridgeCommon.Try(() => settlement.Faction, (Faction)null);
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "label", BridgeCommon.SafeString(() => settlement.Label) },
                { "tile", BridgeCommon.Try(() => at.tileId, -1) },
                { "distanceTiles", Round(distance) },
                { "faction", faction == null ? null : BridgeCommon.SafeString(() => faction.Name) },
                { "factionDef", faction == null ? null : BridgeCommon.SafeString(() => faction.def.defName) },
                { "relation", faction == null ? null : BridgeCommon.SafeString(() => faction.PlayerRelationKind.ToString()) },
                { "goodwill", faction == null ? null : (object)BridgeCommon.TryN(() => faction.PlayerGoodwill) },
                { "isPlayer", faction != null && BridgeCommon.Try(() => faction.IsPlayer, false) }
            };
        }

        // ------------------------------------------------------------------
        // the planet view
        // ------------------------------------------------------------------

        private sealed class Toggle
        {
            internal bool Ok;
            internal string Mode;
            internal string Error;
        }

        private static Toggle ShowWorld()
        {
            try
            {
                if (!CameraJumper.TryShowWorld())
                    return new Toggle { Ok = false, Error = "CameraJumper.TryShowWorld() refused." };
                var world = Find.World;
                if (world != null && world.renderer != null)
                    world.renderer.wantedMode = WorldRenderMode.Planet;
                return new Toggle { Ok = true, Mode = WorldRenderMode.Planet.ToString() };
            }
            catch (Exception e)
            {
                return new Toggle { Ok = false, Error = "Showing the world threw " + e.GetType().Name + ": " + e.Message };
            }
        }

        private static Toggle HideWorld()
        {
            try
            {
                var ok = CameraJumper.TryHideWorld();
                return new Toggle
                {
                    Ok = ok,
                    Error = ok ? null : "CameraJumper.TryHideWorld() refused; the planet view is still up."
                };
            }
            catch (Exception e)
            {
                return new Toggle { Ok = false, Error = "Hiding the world threw " + e.GetType().Name + ": " + e.Message };
            }
        }

        private static Dictionary<string, object> ViewBlock(bool shown, string mode, int seconds, bool hidden, string reason)
        {
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "shown", shown },
                { "wantedMode", mode },
                { "watchSeconds", seconds },
                { "hidden", hidden },
                { "reason", reason }
            };
        }

        // ------------------------------------------------------------------
        // safe accessors
        // ------------------------------------------------------------------

        private static Map SafeMap()
        {
            try
            {
                if (Current.ProgramState != ProgramState.Playing)
                    return null;
                var current = Find.CurrentMap;
                if (current != null)
                    return current;
                var maps = Find.Maps;
                return maps != null && maps.Count > 0 ? maps[0] : null;
            }
            catch
            {
                return null;
            }
        }

        private static bool TryTile(int wantTile, Map map, out PlanetTile tile)
        {
            tile = default(PlanetTile);
            try
            {
                if (wantTile >= 0)
                {
                    var grid = Find.WorldGrid;
                    if (grid == null)
                        return false;
                    // A bare tile id belongs to the surface layer, which is what
                    // the current map's own PlanetTile carries.
                    if (map != null)
                    {
                        var here = map.Tile;
                        tile = new PlanetTile(wantTile, here.Layer);
                        return tile.Valid;
                    }
                    return false;
                }

                if (map == null)
                    return false;
                tile = map.Tile;
                return tile.Valid;
            }
            catch
            {
                return false;
            }
        }

        private static Tile SafeTile(PlanetTile tile)
        {
            try
            {
                var grid = Find.WorldGrid;
                if (grid == null || !tile.Valid)
                    return null;
                return grid[tile];
            }
            catch
            {
                return null;
            }
        }

        private static bool TryLongLat(PlanetTile tile, out Vector2 longLat)
        {
            longLat = default(Vector2);
            try
            {
                var grid = Find.WorldGrid;
                if (grid == null || !tile.Valid)
                    return false;
                longLat = grid.LongLatOf(tile);
                return true;
            }
            catch
            {
                return false;
            }
        }

        /// <summary>Absolute ticks, or null. TickManager.TicksAbs is only read
        /// once gameStartAbsTick is non-zero: its getter reaches Log.Error,
        /// which pauses the colony.</summary>
        private static int? AbsTicks()
        {
            try
            {
                var ticks = Find.TickManager;
                if (ticks == null)
                    return null;
                var start = BridgeCommon.TryN(() => ticks.gameStartAbsTick);
                if (!start.HasValue || start.Value == 0)
                    return null;
                return BridgeCommon.TryN(() => ticks.TicksAbs);
            }
            catch
            {
                return null;
            }
        }

        private static float GrowMin()
        {
            return BridgeCommon.Try(() => Plant.DefaultMinOptimalGrowthTemperature, GrowMinDefault);
        }

        private static float GrowMax()
        {
            return BridgeCommon.Try(() => Plant.DefaultMaxOptimalGrowthTemperature, GrowMaxDefault);
        }

        private static Dictionary<string, object> Notes(string statusNote)
        {
            var notes = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["whyThisToolExists"] = "The World main tab is a MainButtonDef toggle, not a tab window: list_main_tabs reports type \"\" for it, open_main_tab has no tab window to open, and click_ui_target has no ui-element id. This reads the same facts off the world grid instead.",
                ["readOnly"] = "Every field above is a read. The only write in this tool is show:true with dryRun:false, which toggles the planet view and hides it again.",
                ["temperature"] = "temperature is GetTemperatureFromSeasonAtTile for the current absolute tick, so it is the tile's seasonal temperature, not the colony map's local one. Null when TickManager.gameStartAbsTick is 0 (see home/get_time).",
                ["growingPeriod"] = "growingPeriodDays counts the twelfths whose AVERAGE temperature falls inside growingRangeC, times GenDate.DaysPerTwelfth. 0 days is a real answer. This is the number the world inspect pane shows.",
                ["settlements"] = "Distances are WorldGrid.ApproxDistanceInTiles, the same measure the world map uses; they are not travel times. Destroyed settlements are skipped.",
                ["rounding"] = "longitude, latitude, temperatures, elevation, rainfall, swampiness, pollution and distances to 3 decimal places."
            };
            if (!string.IsNullOrEmpty(statusNote))
                notes["status"] = statusNote;
            return notes;
        }

        private static double Round(float value)
        {
            return Math.Round((double)value, 3, MidpointRounding.AwayFromZero);
        }

        private static object RoundN(float? value)
        {
            return value.HasValue ? (object)Round(value.Value) : null;
        }

        /// <summary>The shared refusal shape; see BridgeCommon.Failure.</summary>
        private static object Failure(string error)
        {
            return BridgeCommon.Failure(ToolName, error);
        }
    }
}
