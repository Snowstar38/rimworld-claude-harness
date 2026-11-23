using System;
using System.Collections.Generic;
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
    /// home/get_time — one cheap read-only snapshot of the game clock and the
    /// in-game calendar. No parameters, no rectangle, no sweep.
    ///
    /// ## Why it exists
    ///
    /// Every scheduling question a caller has — "is it night", "how long until
    /// the next quadrum", "did the clock actually advance between these two
    /// calls" — currently needs either a full `rimworld/get_game_info` payload
    /// or arithmetic on raw ticks done by the caller with RimWorld's calendar
    /// constants hardcoded on the Python side. This returns the game's own
    /// answers, including RimWorld's own formatted date string, in one call
    /// that touches no map cells.
    ///
    /// ## The hazard this tool had to dodge (IL-verified, 2026-09-01)
    ///
    /// **`TickManager.TicksAbs` can PAUSE the game.** Its getter calls
    /// `Verse.Log.ErrorOnce` when `gameStartAbsTick == 0`, `Log.ErrorOnce` calls
    /// `Log.Error`, and `Log.Error` calls `TickManager.Pause()`. That is the same
    /// shape as the `GameCondition.TicksLeft` hazard `home/play_until_event`
    /// documents: an innocent-looking READ with a side effect that stops the
    /// colony. Confirmed by scanning the IL of all three methods against the
    /// installed `Assembly-CSharp.dll` (1.6.9676.17735):
    ///
    ///   TickManager.get_TicksAbs -> Verse.Log.ErrorOnce
    ///   Log.ErrorOnce            -> Log.Error
    ///   Log.Error                -> Find.get_TickManager, TickManager.Pause
    ///
    /// So `gameStartAbsTick` (a public int FIELD on TickManager) is checked
    /// first, and `TicksAbs` is only read when it is non-zero. When it is zero
    /// the tool reports `ticksAbsAvailable: false` and `ticksAbs: null` rather
    /// than reading the property "just to see" — and because the whole calendar
    /// is a function of absolute ticks, the calendar fields go null with it.
    ///
    /// Nothing else here writes anything: `TicksGame`, `CurTimeSpeed` and
    /// `ForcePaused` are plain reads (`ForcePaused` walks the window stack and
    /// the gravship/tile-picker state, all getters), and every `GenDate` entry
    /// point below is pure arithmetic over (absTicks, longitude).
    ///
    /// ## Verified against the installed Assembly-CSharp.dll (1.6.9676.17735)
    ///
    ///   Verse.TickManager.TicksGame (int), .TicksAbs (int), .CurTimeSpeed
    ///        (Verse.TimeSpeed: Paused/Normal/Fast/Superfast/Ultrafast),
    ///        .Paused (bool), .ForcePaused (bool),
    ///        .gameStartAbsTick (public int FIELD)
    ///   RimWorld.GenDate — note the namespace is **RimWorld**, not Verse, and
    ///        every calendar entry point takes (long absTicks, float longitude):
    ///          GenDate.HourInteger(long, float)   -> int
    ///          GenDate.DayOfQuadrum(long, float)  -> int
    ///          GenDate.DayOfYear(long, float)     -> int
    ///          GenDate.Quadrum(long, float)       -> RimWorld.Quadrum
    ///          GenDate.Year(long, float)          -> int
    ///        and the two that take a Vector2 longLat instead:
    ///          GenDate.Season(long, Vector2)          -> RimWorld.Season
    ///          GenDate.DateFullStringAt(long, Vector2) -> string
    ///        (a Season(long, float latitude, float longitude) overload also
    ///         exists — the argument order differs from every other method here,
    ///         which is exactly the sort of thing to get wrong by guessing, so
    ///         the Vector2 overload is used instead.)
    ///   RimWorld.Quadrum: Aprimay, Jugust, Septober, Decembary, Undefined
    ///   RimWorld.Season:  Undefined, Spring, Summer, Fall, Winter,
    ///                     PermanentSummer, PermanentWinter
    ///   Verse.Map.Tile -> RimWorld.Planet.PlanetTile (a struct with .Valid and
    ///        a public int FIELD .tileId; its .Tile PROPERTY is a different
    ///        thing, the RimWorld.Planet.Tile data object),
    ///        RimWorld.Planet.WorldGrid.LongLatOf(PlanetTile) -> Vector2
    ///        where x = longitude, y = latitude.
    ///
    /// ## dayOfQuadrum is 0-based, and the UI is not
    ///
    /// Measured by invoking the real method: `GenDate.DayOfQuadrum(0, 0f)` is
    /// **0**, one day later it is 1, and fifteen days later it wraps to 0. The
    /// date RimWorld prints on screen is that number **plus one** (1..15). Both
    /// are emitted — `dayOfQuadrum` raw as the API returns it, and
    /// `dayOfQuadrumDisplay` as the game shows it — because silently picking one
    /// is how a caller's "day 5" ends up meaning two different days.
    ///
    /// ## No game, no map
    ///
    /// `Current.Game == null` is an answer, not a failure: `success: true`,
    /// `status: "no_game"`, and every field still present as null / false / 0.
    /// Same for a game with no map (the world view, or mid-load): the tick and
    /// speed fields are real, the calendar fields are null, and `hasMap` says
    /// which case you are in. The calendar needs a longitude, and inventing one
    /// would produce a plausible date for nowhere.
    /// </summary>
    public sealed class HomeTimeTools
    {
        private const string ToolName = "home/get_time";
        private static Game observedGame;
        private static string sessionId;

        [Tool(
            ToolName,
            Title = "Read the game clock and in-game date",
            Description =
                "One cheap read-only snapshot of RimWorld's clock: game/absolute ticks, paused and force-paused flags, the "
                + "current time speed, and the in-game calendar (hour, day of quadrum, quadrum, season, year) plus RimWorld's "
                + "own formatted date string for the current map's longitude. No parameters, no map cells read.",
            ResultDescription =
                "success, tool, status ('game_loaded' or 'no_game'), hasMap; ticksGame, ticksAbs, ticksAbsAvailable; paused, "
                + "forcePaused, timeSpeed; hourInteger, dayOfQuadrum (0-based) and dayOfQuadrumDisplay (1-based), dayOfYear, "
                + "quadrum, season, year, dateFull; mapName, tile, longitude, latitude; notes.")]
        [ToolResponse("status", "string", "'game_loaded' when a game is loaded, 'no_game' when none is. Never absent.", Always = true)]
        [ToolResponse("sessionId", "string", "Runtime identity of the loaded Game object; changes on every load, including same-tick reloads. Null with no game.", Always = true, Nullable = true)]
        [ToolResponse("ticksGame", "number", "Ticks since the game began. 0 when no game is loaded.", Always = true)]
        [ToolResponse("ticksAbs", "number", "Absolute ticks, the number the calendar is computed from. Null when unavailable; see ticksAbsAvailable.", Always = true)]
        [ToolResponse("timeSpeed", "string", "Paused / Normal / Fast / Superfast / Ultrafast. Null when no game is loaded.", Always = true)]
        [ToolResponse("dateFull", "string", "RimWorld's own formatted date string for the current map's longitude. Null when there is no map.", Always = true)]
        [ToolResponse("unknownArguments", "array", "Every argument key the caller sent that this tool does not declare, sorted, case-sensitively. This tool declares none, so any key at all is listed. Empty array = the call was clean. The host's own _rimBridgeTimeoutMs is never listed.", Always = true)]
        [ToolResponse("unknownArgumentsWarning", "string", "Present only when unknownArguments is non-empty, or when the caller's raw keys could not be read at all - in which case the empty unknownArguments means 'not known', not 'nothing unknown'.", Nullable = true)]
        public async Task<object> GetTime(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken)
        {
            return BridgeCommon.WithUnknownArguments(
                await GetTimeCore(ctx, cancellationToken).ConfigureAwait(false),
                ctx, typeof(HomeTimeTools), ToolName);
        }

        private async Task<object> GetTimeCore(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken)
        {
            if (ctx?.MainThread == null)
                return Failure("No RimBridge main-thread dispatcher is available for this invocation.");

            // Companion tools are dispatched with MarshalToMainThread = false
            // (AnnotatedExtensionCapabilityProvider.InvokeAsync), so every read of
            // TickManager, the world grid and the calendar has to be hopped onto
            // RimWorld's main thread by hand. ALL of it goes inside ONE hop:
            // removing the hop still compiles and then fails intermittently and
            // unreproducibly, which is the worst failure mode available. It also
            // matters here for a second reason — the clock is being read while it
            // may be running, and a single hop is a single consistent instant
            // rather than several ticks smeared together. Keep the hop.
            return await ctx.MainThread
                .InvokeAsync(() => (object)BuildResponse(), cancellationToken)
                .ConfigureAwait(false);
        }

        private static Dictionary<string, object> BuildResponse()
        {
            // Every key below is written unconditionally, including the zeros and
            // the falses, so a caller can never mistake "the tool did not look"
            // for "the game is not paused".
            var payload = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["success"] = true,
                ["tool"] = ToolName
            };
            if (!ReferenceEquals(observedGame, Current.Game))
            {
                observedGame = Current.Game;
                sessionId = Current.Game == null ? null : Guid.NewGuid().ToString("N");
            }
            payload["sessionId"] = sessionId;

            var game = SafeGame();
            if (game == null)
            {
                payload["status"] = "no_game";
                payload["hasMap"] = false;
                WriteClock(payload, null);
                WriteCalendar(payload, null, null);
                WriteMap(payload, null);
                payload["notes"] = BuildNotes("Current.Game is null: no save is loaded. Every game field is null/0/false, and success is still true because 'no game' is an answer, not a tool failure.");
                return payload;
            }

            payload["status"] = "game_loaded";

            var tickManager = SafeTickManager();
            WriteClock(payload, tickManager);

            // Find.CurrentMap is null on the world view and during a load. Fall
            // back to the first loaded map so a caller on the world map still
            // gets a real date rather than a null one; mapSource says which was
            // used, so nobody has to guess whose longitude the date is for.
            string mapSource;
            var map = SafeMap(out mapSource);
            payload["hasMap"] = map != null;
            payload["mapSource"] = mapSource;
            WriteMap(payload, map);

            var absTicks = payload["ticksAbs"] as int?;
            WriteCalendar(payload, absTicks, map);

            payload["notes"] = BuildNotes(null);
            return payload;
        }

        // ------------------------------------------------------------------
        // clock
        // ------------------------------------------------------------------

        private static void WriteClock(IDictionary<string, object> payload, TickManager tickManager)
        {
            if (tickManager == null)
            {
                payload["ticksGame"] = 0;
                payload["ticksAbs"] = null;
                payload["ticksAbsAvailable"] = false;
                payload["paused"] = false;
                payload["forcePaused"] = false;
                payload["timeSpeed"] = null;
                return;
            }

            payload["ticksGame"] = SafeInt(() => tickManager.TicksGame) ?? 0;

            // THE HAZARD. TickManager.get_TicksAbs calls Log.ErrorOnce when
            // gameStartAbsTick is 0, Log.ErrorOnce calls Log.Error, and Log.Error
            // calls TickManager.Pause(). Reading this property to find out
            // whether it is readable would pause the colony. gameStartAbsTick is
            // a public int field; check it, and only then read the property.
            var gameStartAbsTick = SafeInt(() => tickManager.gameStartAbsTick);
            if (gameStartAbsTick.HasValue && gameStartAbsTick.Value != 0)
            {
                var abs = SafeInt(() => tickManager.TicksAbs);
                payload["ticksAbs"] = abs;
                payload["ticksAbsAvailable"] = abs.HasValue;
            }
            else
            {
                payload["ticksAbs"] = null;
                payload["ticksAbsAvailable"] = false;
            }

            // Paused and ForcePaused are read separately on purpose, exactly as
            // home/play_until_event reads them: TickManager.Paused is
            // (curTimeSpeed == Paused || ForcePaused), so a modal window makes
            // `paused` true without anyone having touched the speed control.
            // `timeSpeed` plus `forcePaused` is what tells the two apart.
            payload["paused"] = SafeBool(() => tickManager.Paused) ?? false;
            payload["forcePaused"] = SafeBool(() => tickManager.ForcePaused) ?? false;
            payload["timeSpeed"] = SafeString(() => tickManager.CurTimeSpeed.ToString());
        }

        // ------------------------------------------------------------------
        // calendar
        // ------------------------------------------------------------------

        private static void WriteCalendar(IDictionary<string, object> payload, int? absTicks, Map map)
        {
            // The calendar is a function of (absolute ticks, longitude). Without
            // both, every field below is null rather than computed at longitude
            // 0 — a plausible date for nowhere is worse than no date.
            float? longitude = null;
            Vector2 longLat = default(Vector2);
            var haveLongLat = false;

            if (map != null && TryGetLongLat(map, out longLat))
            {
                longitude = longLat.x;
                haveLongLat = true;
            }

            if (!absTicks.HasValue || !haveLongLat)
            {
                payload["hourInteger"] = null;
                payload["dayOfQuadrum"] = null;
                payload["dayOfQuadrumDisplay"] = null;
                payload["dayOfYear"] = null;
                payload["quadrum"] = null;
                payload["season"] = null;
                payload["year"] = null;
                payload["dateFull"] = null;
                return;
            }

            long abs = absTicks.Value;
            float lon = longitude.Value;

            payload["hourInteger"] = SafeInt(() => GenDate.HourInteger(abs, lon));

            // 0-based, straight from the API. Measured: DayOfQuadrum(0, 0f) == 0.
            var dayOfQuadrum = SafeInt(() => GenDate.DayOfQuadrum(abs, lon));
            payload["dayOfQuadrum"] = dayOfQuadrum;
            // What the game prints on screen: the same number plus one, 1..15.
            payload["dayOfQuadrumDisplay"] = dayOfQuadrum.HasValue ? (int?)(dayOfQuadrum.Value + 1) : null;

            payload["dayOfYear"] = SafeInt(() => GenDate.DayOfYear(abs, lon));
            payload["quadrum"] = SafeString(() => GenDate.Quadrum(abs, lon).ToString());

            var capturedLongLat = longLat;
            payload["season"] = SafeString(() => GenDate.Season(abs, capturedLongLat).ToString());
            // RimWorld's own formatting, not ours: whatever the game would print,
            // in the active language, is what a caller gets.
            payload["dateFull"] = SafeString(() => GenDate.DateFullStringAt(abs, capturedLongLat));
        }

        // ------------------------------------------------------------------
        // map / location
        // ------------------------------------------------------------------

        private static void WriteMap(IDictionary<string, object> payload, Map map)
        {
            if (map == null)
            {
                payload["mapName"] = null;
                payload["tile"] = null;
                payload["longitude"] = null;
                payload["latitude"] = null;
                return;
            }

            payload["mapName"] = SafeString(() => map.Parent == null ? null : map.Parent.Label);
            // PlanetTile.tileId is a public int FIELD on the struct itself. Its
            // .Tile property is something else entirely (the RimWorld.Planet.Tile
            // data object), which is the easy wrong turn here.
            payload["tile"] = SafeInt(() => map.Tile.tileId);

            Vector2 longLat;
            if (TryGetLongLat(map, out longLat))
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

        /// <summary>
        /// WorldGrid.LongLatOf(PlanetTile) -> Vector2 where x is longitude and y
        /// is latitude. PlanetTile is a struct with a .Valid flag; a pocket map
        /// or a map mid-generation can hold an invalid one, so it is checked
        /// before the lookup rather than after the exception.
        /// </summary>
        private static bool TryGetLongLat(Map map, out Vector2 longLat)
        {
            longLat = default(Vector2);
            try
            {
                var grid = Find.WorldGrid;
                if (grid == null)
                    return false;
                var tile = map.Tile;
                if (!tile.Valid)
                    return false;
                longLat = grid.LongLatOf(tile);
                return true;
            }
            catch
            {
                return false;
            }
        }

        private static Map SafeMap(out string mapSource)
        {
            mapSource = null;
            try
            {
                if (Current.ProgramState != ProgramState.Playing)
                    return null;

                var current = Find.CurrentMap;
                if (current != null)
                {
                    mapSource = "currentMap";
                    return current;
                }

                // World view or mid-load: CurrentMap is null while a colony map
                // exists. Use the first one and say so.
                var maps = Find.Maps;
                if (maps != null && maps.Count > 0 && maps[0] != null)
                {
                    mapSource = "firstMap";
                    return maps[0];
                }
            }
            catch
            {
                // fall through to "no map"
            }
            return null;
        }

        // ------------------------------------------------------------------
        // notes
        // ------------------------------------------------------------------

        private static Dictionary<string, object> BuildNotes(string statusNote)
        {
            var notes = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["dayOfQuadrum"] = "0-based, exactly as GenDate.DayOfQuadrum returns it. dayOfQuadrumDisplay is the same number + 1, which is what the game prints (1..15).",
                ["ticksAbs"] = "Null when TickManager.gameStartAbsTick is 0. The property is NOT read in that case: its getter calls Log.ErrorOnce -> Log.Error -> TickManager.Pause(), so probing it would pause the game. The calendar is derived from ticksAbs, so it goes null with it.",
                ["pausedVsTimeSpeed"] = "paused = TickManager.Paused = (timeSpeed == 'Paused' || forcePaused). A modal window or long event sets forcePaused without changing timeSpeed; compare the two rather than reading either alone.",
                ["calendarNeedsALongitude"] = "hourInteger, dayOfQuadrum, dayOfYear, quadrum, season, year and dateFull are all functions of (ticksAbs, the map's longitude). With no map they are null rather than computed at longitude 0.",
                ["mapSource"] = "'currentMap' when Find.CurrentMap answered, 'firstMap' when it was null and Find.Maps[0] was used instead (world view, mid-load), null when there is no map at all.",
                ["readOnly"] = "Every read here was IL-scanned for field stores and for Log.Error paths. Nothing in this tool mutates game state or touches map cells.",
                ["rounding"] = "longitude and latitude to 3 decimal places; every other number is an exact integer from the game."
            };

            if (!string.IsNullOrEmpty(statusNote))
                notes["status"] = statusNote;

            return notes;
        }

        // ------------------------------------------------------------------
        // safe accessors
        // ------------------------------------------------------------------

        private static Game SafeGame()
        {
            try
            {
                return Current.Game;
            }
            catch
            {
                return null;
            }
        }

        private static TickManager SafeTickManager()
        {
            try
            {
                return Find.TickManager;
            }
            catch
            {
                return null;
            }
        }

        private static int? SafeInt(Func<int> read)
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

        private static bool? SafeBool(Func<bool> read)
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

        private static string SafeString(Func<string> read)
        {
            return BridgeCommon.SafeString(read);
        }

        private static double Round(float value)
        {
            return Math.Round((double)value, 3, MidpointRounding.AwayFromZero);
        }

        /// <summary>The shared refusal shape; see BridgeCommon.Failure.</summary>
        private static object Failure(string error)
        {
            return BridgeCommon.Failure(ToolName, error);
        }
    }
}
