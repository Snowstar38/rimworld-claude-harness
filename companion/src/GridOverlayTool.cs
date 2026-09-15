using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using HarmonyLib;
using RimBridgeServer.Sdk;
using RimWorld;
using RimWorld.Planet;
using UnityEngine;
using Verse;

namespace HomeBridge.BridgeTools
{
    /// <summary>
    /// home/grid — a coordinate grid drawn over the play area, in the game, so
    /// the OBS capture carries it.
    ///
    /// ## Why it exists
    ///
    /// Chat can see the map and not the cell numbers; the agents read the cell
    /// numbers and never see the map. A labelled grid is the shared vocabulary:
    /// a viewer says "104,127" and the hands can act on it with no screenshot
    /// round-trip, and a cell an agent names can be checked against the picture.
    ///
    /// ## Where the drawing happens
    ///
    /// A Harmony **Prefix** on `RimWorld.MapInterface.MapInterfaceOnGUI_BeforeMainTabs`.
    /// That method draws, in order, the thing overlays, the map's own OnGUI, the
    /// colonist bar, the drag box, the designator, the targeter, tooltips,
    /// flecks, the mouseover readout, the global controls, the resource readout
    /// and the map gizmos — and the main tabs, alerts and every window come
    /// after it. IMGUI is painter's-algorithm, so a Prefix puts the grid over
    /// the terrain and **under** every piece of UI; a Postfix would put it over
    /// the colonist bar. The original method's own guards are replicated here
    /// (`Find.CurrentMap != null`, `WorldRendererUtility.DrawingMap`), because a
    /// Prefix runs before them.
    ///
    /// The patch is installed on the first `home/grid` call, not at load: a DLL
    /// nobody calls patches nothing. When the overlay is off the patch body is
    /// one volatile read and a return.
    ///
    /// ## Cell to screen
    ///
    /// Copied from `Verse.GenMapUI.LabelDrawPosFor`, IL-read off the installed
    /// Assembly-CSharp:
    ///
    ///   Vector2 p = Find.Camera.WorldToScreenPoint(new Vector3(x, 0f, z)) / Prefs.UIScale;
    ///   p.y = UI.screenHeight - p.y;
    ///
    /// Unity's screen origin is bottom-left and IMGUI's is top-left, which is
    /// the flip; `Prefs.UIScale` is the division `UI.screenHeight` is already
    /// divided by. Gridlines are drawn on cell BOUNDARIES (world x, not x+0.5),
    /// so the label at 104,127 sits on the corner of cell 104,127.
    ///
    /// ## Verified against the installed Assembly-CSharp.dll (1.6.9676.17735)
    ///
    ///   RimWorld.MapInterface.MapInterfaceOnGUI_BeforeMainTabs() — instance, void
    ///   RimWorld.Planet.WorldRendererUtility.DrawingMap (static bool property)
    ///     — note WorldRenderedNow does NOT exist in 1.6; the pair here is
    ///       WorldRendered / DrawingMap
    ///   Verse.CameraDriver.CurrentViewRect -> Verse.CellRect (minX/maxX/minZ/maxZ
    ///     public int fields, INCLUSIVE on both ends)
    ///   Verse.Find.Camera -> UnityEngine.Camera, .CameraDriver, .CurrentMap, .UIRoot
    ///   Verse.Prefs.UIScale (static float), Verse.UI.screenWidth/.screenHeight
    ///     (static int FIELDS)
    ///   Verse.Widgets.DrawBoxSolid(Rect, Color) — saves and restores GUI.color
    ///   Verse.GenMapUI.DrawText(Vector2 worldPos, string, Color) — takes a
    ///     WORLD (x, z), does the same conversion, draws GameFont.Tiny centred,
    ///     and puts GUI.color and Text.Anchor back
    ///   Verse.ScreenshotModeHandler.FiltersCurrentEvent (false whenever
    ///     screenshot mode is off)
    ///
    /// `Widgets.Label` and `UnityEngine.Event` are deliberately unused: both
    /// need UnityEngine.IMGUIModule, which this csproj does not reference.
    /// Everything above resolves through Assembly-CSharp and UnityEngine.CoreModule.
    ///
    /// ## Cost
    ///
    /// Off: one volatile read per OnGUI call. On: one `WorldToScreenPoint` per
    /// gridline plus one `DrawBoxSolid` each, and a `DrawText` per label. The
    /// step doubles until the view holds at most MaxLines lines per axis, and
    /// the label step grows until the view holds at most MaxLabelColumns by
    /// MaxLabelRows labels, so zooming all the way out thins the grid instead of
    /// drawing thousands of lines. Both are reported back as
    /// render.effectiveStep and render.labelStep.
    ///
    /// The state is static and belongs to the DLL, not to the save: it survives
    /// a reload and is not written to any file. Nothing here touches game state,
    /// so there is no watch session — turning the grid on IS the visible thing.
    /// </summary>
    public sealed class HomeGridTools
    {
        private const string ToolName = "home/grid";

        [Tool(
            ToolName,
            Title = "Coordinate grid overlay on the map",
            Description =
                "Draw a labelled coordinate grid over the play area, inside the game, so anyone watching the capture can "
                + "read cell numbers off the map. Send no parameters at all to read the current state. enabled turns it on "
                + "or off, step sets the spacing in cells, labels turns the x,z text on or off, alpha and color set how it "
                + "looks. Every write defaults to dryRun:true. The state is the DLL's, not the save's: it survives a reload "
                + "and touches no game state.",
            ResultDescription =
                "success, tool, enabled; before{} and after{} with enabled/step/labels/alpha/color; changed, applied, "
                + "dryRun, clamped[]; patch{} (whether the per-frame draw hook is installed), render{} (what the last "
                + "drawn frame actually did), map{}, notes{}.")]
        [ToolResponse("enabled", "boolean", "Whether the overlay is on AFTER this call. On a dry run this is the state that was left in place, not the state that was asked for; after.enabled is the same number and before.enabled is what it was.", Always = true)]
        [ToolResponse("before", "object", "The overlay state as it was when the call arrived: enabled, step, labels, alpha, color.", Always = true)]
        [ToolResponse("after", "object", "The overlay state read back out of the DLL after the call: enabled, step, labels, alpha, color. Identical to before on a dry run and on a read.", Always = true)]
        [ToolResponse("wouldBe", "object", "The state this call asked for, clamps applied: enabled, step, labels, alpha, color. On a real write it equals after; on a dry run it is what after would have become.", Always = true)]
        [ToolResponse("changed", "boolean", "True when after differs from before in any field. False on every dry run and every read.", Always = true)]
        [ToolResponse("applied", "boolean", "True when this call actually wrote the state. False on a dry run, on a read, on a refusal, and on a real write that asked for the state the overlay was already in.", Always = true)]
        [ToolResponse("dryRun", "boolean", "TRUE by default, like every other write tool here. A dry run reports the state it would set and sets nothing.", Always = true)]
        [ToolResponse("clamped", "array", "One entry per argument that was pulled into range rather than taken as sent: field, asked, used, range. Empty when nothing was clamped.", Always = true)]
        [ToolResponse("patch", "object", "The per-frame draw hook: attempted, installed, target, owner, error. installed:false with an error is the one way the grid can be on and invisible.", Always = true)]
        [ToolResponse("render", "object", "What the hook did on its last frame: framesDrawn, lastDrawnMsAgo, linesLastFrame, labelsLastFrame, effectiveStep, labelStep, viewRect, errors, lastError. framesDrawn stays 0 while the overlay is off; errors counts draw failures since the grid was last switched on, and enough of them switch it off again.", Always = true)]
        [ToolResponse("map", "object", "hasMap, mapName, sizeX, sizeZ - the coordinate range the grid can label. Null sizes when no map is loaded; the overlay state is readable either way.", Always = true)]
        [ToolResponse("unknownArguments", "array", "Every argument key the caller sent that this tool does not declare, sorted, case-sensitively. Empty array = the call was clean. The host's own _rimBridgeTimeoutMs is never listed.", Always = true)]
        [ToolResponse("unknownArgumentsWarning", "string", "Present only when unknownArguments is non-empty, or when the caller's raw keys could not be read at all - in which case the empty unknownArguments means 'not known', not 'nothing unknown'.", Nullable = true)]
        public async Task<object> Grid(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            [ToolParameter(Description = "WRITE: turn the overlay on (true) or off (false). Omit to leave it alone, which with no other argument makes the call a plain read.")] bool? enabled = null,
            [ToolParameter(Description = "WRITE: gridline spacing in cells. Omit or 0 to leave it alone; the stored spacing starts at 10. Clamped to 2..50.", DefaultValue = 0)] int step = 0,
            [ToolParameter(Description = "WRITE: draw the x,z text at the line crossings. Omit to leave it alone; it starts on.")] bool? labels = null,
            [ToolParameter(Description = "WRITE: line opacity, 0.05..1. Omit or 0 to leave it alone; it starts at 0.35. Labels are drawn at a floor of 0.85 whatever this is, or they cannot be read on the capture.", DefaultValue = 0)] double alpha = 0,
            [ToolParameter(Description = "WRITE: line colour, one of white, black, grey, red, orange, yellow, green, cyan, blue, magenta, or #rrggbb. Omit to leave it alone; it starts white. An unrecognised name is refused with the list.")] string color = null,
            [ToolParameter(Description = "TRUE by default. A dry run reports the state it would set and sets nothing.", DefaultValue = true)] bool dryRun = true)
        {
            return BridgeCommon.WithUnknownArguments(
                await GridCore(ctx, cancellationToken, enabled, step, labels, alpha, color, dryRun).ConfigureAwait(false),
                ctx, typeof(HomeGridTools), ToolName);
        }

        private async Task<object> GridCore(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            bool? enabled,
            int step,
            bool? labels,
            double alpha,
            string color,
            bool dryRun)
        {
            if (ctx?.MainThread == null)
                return Failure("No RimBridge main-thread dispatcher is available for this invocation.");

            string colorError;
            Color? wantColor = null;
            if (!string.IsNullOrEmpty(color))
            {
                Color parsed;
                if (!GridOverlay.TryParseColor(color, out parsed, out colorError))
                    return Failure(colorError);
                wantColor = parsed;
            }

            // Companion tools are dispatched with MarshalToMainThread = false, so
            // the Harmony install, the state write and the map read all go inside
            // ONE main-thread hop. Harmony patching off the main thread while the
            // game is drawing is the kind of failure that never reproduces.
            return await ctx.MainThread
                .InvokeAsync(() => (object)Apply(enabled, step, labels, alpha, color, wantColor, dryRun),
                             cancellationToken)
                .ConfigureAwait(false);
        }

        private static Dictionary<string, object> Apply(
            bool? enabled, int step, bool? labels, double alpha,
            string colorAsked, Color? wantColor, bool dryRun)
        {
            GridOverlay.EnsurePatched();

            var before = GridOverlay.Current;
            var clamped = new List<object>();

            var wantStep = before.Step;
            if (step != 0)
            {
                wantStep = Clamp(step, GridOverlay.MinStep, GridOverlay.MaxStep);
                if (wantStep != step)
                    clamped.Add(Clamp("step", step, wantStep,
                                      GridOverlay.MinStep + ".." + GridOverlay.MaxStep));
            }

            var wantAlpha = before.Alpha;
            if (alpha != 0)
            {
                wantAlpha = Mathf.Clamp((float)alpha, GridOverlay.MinAlpha, 1f);
                if (Math.Abs(wantAlpha - (float)alpha) > 0.0001f)
                    clamped.Add(Clamp("alpha", Round(alpha), Round(wantAlpha),
                                      GridOverlay.MinAlpha.ToString("0.00", CultureInfo.InvariantCulture) + "..1"));
            }

            var wanted = new GridSettings(
                enabled ?? before.Enabled,
                wantStep,
                labels ?? before.Labels,
                wantAlpha,
                wantColor.HasValue ? GridOverlay.NameOf(colorAsked) : before.ColorName,
                wantColor ?? before.LineColor);

            var applied = !dryRun && !wanted.SameAs(before);
            if (applied)
                GridOverlay.Set(wanted);

            var after = GridOverlay.Current;

            var payload = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["success"] = true,
                ["tool"] = ToolName,
                ["enabled"] = after.Enabled,
                ["before"] = before.ToPayload(),
                ["after"] = after.ToPayload(),
                ["wouldBe"] = wanted.ToPayload(),
                ["changed"] = !after.SameAs(before),
                ["applied"] = applied,
                ["dryRun"] = dryRun,
                ["clamped"] = clamped,
                ["patch"] = GridOverlay.PatchPayload(),
                ["render"] = GridOverlay.RenderPayload(),
                ["map"] = MapPayload(),
                ["notes"] = Notes()
            };
            return payload;
        }

        private static Dictionary<string, object> MapPayload()
        {
            var map = BridgeCommon.Try(() => Find.CurrentMap, null);
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["hasMap"] = map != null,
                ["mapName"] = map == null ? null : BridgeCommon.SafeString(() => map.Parent == null ? null : map.Parent.Label),
                ["sizeX"] = map == null ? (object)null : BridgeCommon.TryN(() => map.Size.x),
                ["sizeZ"] = map == null ? (object)null : BridgeCommon.TryN(() => map.Size.z)
            };
        }

        private static Dictionary<string, object> Notes()
        {
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["whatItCosts"] = "Off, the per-frame hook is one volatile read and a return. On, it is one screen-position read and one box per gridline plus one text per label, all inside the visible view rect.",
                ["stateIsTheDllsNotTheSaves"] = "The overlay state lives in this DLL. It survives a save reload and a map change, it is written to no file, and it resets when RimWorld restarts.",
                ["itIsVisibleToTheStream"] = "This draws inside the game, under every piece of UI and over the terrain, so the capture carries it. Turning it on changes what viewers see.",
                ["linesAreOnCellBoundaries"] = "A line at 100 runs along the edge of cell 100, and the label 100,120 sits on that corner. The cell a viewer means is up and to the right of its label.",
                ["densityIsCappedByZoom"] = "render.effectiveStep is the spacing actually drawn: it doubles off step until at most " + GridOverlay.MaxLines + " lines per axis are in view. render.labelStep grows the same way until at most " + GridOverlay.MaxLabelColumns + " by " + GridOverlay.MaxLabelRows + " labels are in view.",
                ["noWatchSession"] = "No watch block: this tool selects nothing and opens no menu, because the grid itself is what a viewer sees.",
                ["screenshotMode"] = "RimWorld's own hide-UI screenshot mode hides the grid with the rest of the UI, exactly as it hides the colonist bar.",
                ["howToTellItIsDrawing"] = "render.framesDrawn climbing and render.lastDrawnMsAgo small is proof the hook ran. framesDrawn stuck at 0 with patch.installed true means the game is paused on a window, on the world view, or has no map."
            };
        }

        private static Dictionary<string, object> Clamp(string field, object asked, object used, string range)
        {
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["field"] = field,
                ["asked"] = asked,
                ["used"] = used,
                ["range"] = range
            };
        }

        private static int Clamp(int value, int min, int max)
        {
            return value < min ? min : (value > max ? max : value);
        }

        private static double Round(double v)
        {
            return Math.Round(v, 3, MidpointRounding.AwayFromZero);
        }

        private static object Failure(string error)
        {
            return BridgeCommon.Failure(ToolName, error);
        }
    }

    /// <summary>One immutable snapshot of the overlay's settings.</summary>
    internal sealed class GridSettings
    {
        internal readonly bool Enabled;
        internal readonly int Step;
        internal readonly bool Labels;
        internal readonly float Alpha;
        internal readonly string ColorName;
        internal readonly Color LineColor;

        internal GridSettings(bool enabled, int step, bool labels, float alpha, string colorName, Color lineColor)
        {
            Enabled = enabled;
            Step = step;
            Labels = labels;
            Alpha = alpha;
            ColorName = colorName;
            LineColor = lineColor;
        }

        internal bool SameAs(GridSettings other)
        {
            return other != null
                && other.Enabled == Enabled
                && other.Step == Step
                && other.Labels == Labels
                && Math.Abs(other.Alpha - Alpha) < 0.0001f
                && string.Equals(other.ColorName, ColorName, StringComparison.Ordinal);
        }

        internal Dictionary<string, object> ToPayload()
        {
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["enabled"] = Enabled,
                ["step"] = Step,
                ["labels"] = Labels,
                ["alpha"] = Math.Round((double)Alpha, 3, MidpointRounding.AwayFromZero),
                ["color"] = ColorName
            };
        }
    }

    /// <summary>
    /// The overlay itself: the settings other code writes, the Harmony patch
    /// that draws them, and the counters that prove the draw ran.
    /// </summary>
    internal static class GridOverlay
    {
        internal const int MinStep = 2;
        internal const int MaxStep = 50;
        internal const float MinAlpha = 0.05f;

        /// <summary>Most gridlines drawn per axis; the step doubles past it.</summary>
        internal const int MaxLines = 64;
        internal const int MaxLabelColumns = 14;
        internal const int MaxLabelRows = 10;

        private const string HarmonyId = "homebridge.grid-overlay";

        /// <summary>Enough consecutive draw failures to turn the overlay off by
        /// itself. A hook that throws every frame on a live stream is worse than
        /// no grid.</summary>
        private const int ErrorBudget = 60;

        private static GridSettings _settings = new GridSettings(
            false, 10, true, 0.35f, "white", Color.white);

        private static int _patched;
        private static MethodInfo _target;
        private static string _patchError;
        private static bool _attempted;

        private static int _frames;
        private static int _lastDrawTick;
        private static int _lines;
        private static int _labels;
        private static int _effectiveStep;
        private static int _labelStep;
        private static int _viewX, _viewZ, _viewW, _viewH;
        private static int _errors;
        private static string _lastError;

        internal static GridSettings Current
        {
            get { return Volatile.Read(ref _settings); }
        }

        /// <summary>
        /// Turning the grid on clears the error count, so the budget that
        /// switched it off last time cannot switch it off again on frame one.
        /// </summary>
        internal static void Set(GridSettings settings)
        {
            if (settings != null && settings.Enabled)
            {
                Volatile.Write(ref _errors, 0);
                _lastError = null;
            }
            Volatile.Write(ref _settings, settings);
        }

        // ------------------------------------------------------------------
        // the patch
        // ------------------------------------------------------------------

        /// <summary>
        /// Installs the draw hook once. A missing patch target is reported in
        /// the payload rather than thrown: the tool still answers, and
        /// patch.installed false is what says the grid cannot be drawn.
        /// </summary>
        internal static void EnsurePatched()
        {
            if (Interlocked.CompareExchange(ref _patched, 1, 0) != 0)
                return;
            _attempted = true;
            try
            {
                _target = AccessTools.Method(typeof(MapInterface),
                                             "MapInterfaceOnGUI_BeforeMainTabs",
                                             Type.EmptyTypes);
                if (_target == null)
                    throw new MissingMethodException(typeof(MapInterface).FullName,
                                                     "MapInterfaceOnGUI_BeforeMainTabs()");
                new Harmony(HarmonyId).Patch(
                    _target,
                    prefix: new HarmonyMethod(typeof(DrawPatch), nameof(DrawPatch.Prefix)));
                var info = Harmony.GetPatchInfo(_target);
                if (info == null || !info.Owners.Contains(HarmonyId))
                    throw new InvalidOperationException("Harmony did not report the grid patch owner after Patch().");
                _patchError = null;
            }
            catch (Exception ex)
            {
                _patchError = ex.GetType().Name + ": " + ex.Message;
                _target = null;
                // Let the next call try again rather than reporting a failure
                // that was never retried.
                Interlocked.Exchange(ref _patched, 0);
            }
        }

        internal static Dictionary<string, object> PatchPayload()
        {
            var target = _target;
            var info = target == null ? null : Harmony.GetPatchInfo(target);
            var owners = info == null ? new List<string>() : info.Owners.OrderBy(x => x).ToList();
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["attempted"] = _attempted,
                ["installed"] = owners.Contains(HarmonyId),
                ["target"] = target == null ? null : target.DeclaringType.FullName + "." + target.Name,
                ["owner"] = HarmonyId,
                ["owners"] = owners,
                ["error"] = _patchError
            };
        }

        internal static Dictionary<string, object> RenderPayload()
        {
            var frames = Volatile.Read(ref _frames);
            var last = Volatile.Read(ref _lastDrawTick);
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                ["framesDrawn"] = frames,
                ["lastDrawnMsAgo"] = frames == 0 ? (object)null : unchecked(Environment.TickCount - last),
                ["linesLastFrame"] = Volatile.Read(ref _lines),
                ["labelsLastFrame"] = Volatile.Read(ref _labels),
                ["effectiveStep"] = Volatile.Read(ref _effectiveStep),
                ["labelStep"] = Volatile.Read(ref _labelStep),
                ["viewRect"] = frames == 0 ? null : new Dictionary<string, object>(StringComparer.Ordinal)
                {
                    ["x"] = Volatile.Read(ref _viewX),
                    ["z"] = Volatile.Read(ref _viewZ),
                    ["width"] = Volatile.Read(ref _viewW),
                    ["height"] = Volatile.Read(ref _viewH)
                },
                ["errors"] = Volatile.Read(ref _errors),
                ["lastError"] = _lastError
            };
        }

        // ------------------------------------------------------------------
        // colour
        // ------------------------------------------------------------------

        private static readonly string[] ColorNames =
        {
            "white", "black", "grey", "red", "orange",
            "yellow", "green", "cyan", "blue", "magenta"
        };

        internal static string NameOf(string asked)
        {
            return asked == null ? "white" : asked.Trim().ToLowerInvariant();
        }

        internal static bool TryParseColor(string asked, out Color color, out string error)
        {
            color = Color.white;
            error = null;
            var name = NameOf(asked);
            switch (name)
            {
                case "white": color = Color.white; return true;
                case "black": color = Color.black; return true;
                case "grey": color = new Color(0.6f, 0.6f, 0.6f); return true;
                case "red": color = new Color(1f, 0.25f, 0.2f); return true;
                case "orange": color = new Color(1f, 0.6f, 0.15f); return true;
                case "yellow": color = new Color(1f, 0.95f, 0.3f); return true;
                case "green": color = new Color(0.35f, 1f, 0.4f); return true;
                case "cyan": color = new Color(0.35f, 0.95f, 1f); return true;
                case "blue": color = new Color(0.4f, 0.6f, 1f); return true;
                case "magenta": color = new Color(1f, 0.45f, 0.95f); return true;
            }
            if (name.Length == 7 && name[0] == '#')
            {
                int r, g, b;
                if (Hex(name, 1, out r) && Hex(name, 3, out g) && Hex(name, 5, out b))
                {
                    color = new Color(r / 255f, g / 255f, b / 255f);
                    return true;
                }
            }
            error = "color " + (asked ?? "null") + " is not one of "
                    + string.Join(", ", ColorNames) + " and is not a #rrggbb hex value.";
            return false;
        }

        private static bool Hex(string s, int at, out int value)
        {
            return int.TryParse(s.Substring(at, 2), NumberStyles.HexNumber,
                                CultureInfo.InvariantCulture, out value);
        }

        // ------------------------------------------------------------------
        // the draw
        // ------------------------------------------------------------------

        private static class DrawPatch
        {
            /// <summary>
            /// Runs before MapInterfaceOnGUI_BeforeMainTabs, so the grid lands
            /// over the terrain and under every piece of map UI. A void Prefix
            /// never skips the original. An exception here would surface as a
            /// RimWorld Log.Error, which calls TickManager.Pause() and stops the
            /// colony with nothing saying why, so nothing escapes the catch.
            /// </summary>
            internal static void Prefix()
            {
                var settings = Volatile.Read(ref _settings);
                if (settings == null || !settings.Enabled)
                    return; // ordinary play: this read and this return
                try
                {
                    Draw(settings);
                }
                catch (Exception ex)
                {
                    _lastError = ex.GetType().Name + ": " + ex.Message;
                    if (Interlocked.Increment(ref _errors) >= ErrorBudget)
                    {
                        Volatile.Write(ref _settings, new GridSettings(
                            false, settings.Step, settings.Labels, settings.Alpha,
                            settings.ColorName, settings.LineColor));
                        _lastError = "turned itself off after " + ErrorBudget
                                     + " failed frames; last was " + _lastError;
                    }
                }
            }
        }

        private static void Draw(GridSettings settings)
        {
            var map = Find.CurrentMap;
            if (map == null || !WorldRendererUtility.DrawingMap)
                return;
            var uiRoot = Find.UIRoot;
            if (uiRoot != null && uiRoot.screenshotMode != null && uiRoot.screenshotMode.FiltersCurrentEvent)
                return;
            var driver = Find.CameraDriver;
            var camera = Find.Camera;
            if (driver == null || camera == null)
                return;

            var scale = Prefs.UIScale;
            if (scale <= 0f)
                scale = 1f;

            // CurrentViewRect is inclusive on both ends, so the far boundary of
            // the last visible cell is maxX + 1. Clamped to the map: a gridline
            // off the edge labels a cell that does not exist.
            var view = driver.CurrentViewRect;
            var x0 = Math.Max(0, view.minX);
            var z0 = Math.Max(0, view.minZ);
            var x1 = Math.Min(map.Size.x, view.maxX + 1);
            var z1 = Math.Min(map.Size.z, view.maxZ + 1);
            if (x1 <= x0 || z1 <= z0)
                return;

            var drawStep = settings.Step;
            while (drawStep < 100000 && ((x1 - x0) / drawStep > MaxLines || (z1 - z0) / drawStep > MaxLines))
                drawStep *= 2;

            var topLeft = GuiPos(camera, scale, x0, z1);
            var bottomRight = GuiPos(camera, scale, x1, z0);
            var left = Mathf.Max(topLeft.x, 0f);
            var right = Mathf.Min(bottomRight.x, UI.screenWidth);
            var top = Mathf.Max(topLeft.y, 0f);
            var bottom = Mathf.Min(bottomRight.y, UI.screenHeight);
            if (right <= left || bottom <= top)
                return;

            // One cell in GUI pixels, so the line stays proportionate as the
            // camera zooms instead of vanishing into a hairline.
            var cellPx = (bottomRight.x - topLeft.x) / (x1 - x0);
            var width = Mathf.Clamp(cellPx * 0.06f, 1f, 3f);

            var line = settings.LineColor;
            line.a = settings.Alpha;
            var text = settings.LineColor;
            text.a = Mathf.Max(settings.Alpha, 0.85f);

            var lines = 0;
            for (var x = FirstMultiple(x0, drawStep); x <= x1; x += drawStep)
            {
                var px = GuiPos(camera, scale, x, z0).x;
                Widgets.DrawBoxSolid(new Rect(px - width * 0.5f, top, width, bottom - top), line);
                lines++;
            }
            for (var z = FirstMultiple(z0, drawStep); z <= z1; z += drawStep)
            {
                var py = GuiPos(camera, scale, x0, z).y;
                Widgets.DrawBoxSolid(new Rect(left, py - width * 0.5f, right - left, width), line);
                lines++;
            }

            var labelStep = drawStep;
            if (settings.Labels)
            {
                while (labelStep < 100000
                       && ((x1 - x0) / labelStep > MaxLabelColumns || (z1 - z0) / labelStep > MaxLabelRows))
                    labelStep += drawStep;
            }

            var labels = 0;
            if (settings.Labels)
            {
                // STRICTLY less than the far edge, unlike the line loops above.
                // x1/z1 is a BOUNDARY, not a cell: on a 250-wide map with the
                // east edge in view it is 250, and cells run 0..249. A line at
                // 250 is the correct east wall of cell 249; a LABEL at 250 names
                // a cell that does not exist, and a viewer reading it back says
                // "250,250" for a coordinate no tool will accept. The last real
                // cell still gets its label whenever it falls on the step --
                // x1-1 passes this test.
                for (var x = FirstMultiple(x0, labelStep); x < x1; x += labelStep)
                {
                    for (var z = FirstMultiple(z0, labelStep); z < z1; z += labelStep)
                    {
                        // GenMapUI.DrawText takes a WORLD (x, z) and centres the
                        // text just under it, so the label hangs off the corner
                        // it names.
                        GenMapUI.DrawText(new Vector2(x, z),
                                          x.ToString(CultureInfo.InvariantCulture) + ","
                                          + z.ToString(CultureInfo.InvariantCulture),
                                          text);
                        labels++;
                    }
                }
            }

            Volatile.Write(ref _lines, lines);
            Volatile.Write(ref _labels, labels);
            Volatile.Write(ref _effectiveStep, drawStep);
            Volatile.Write(ref _labelStep, settings.Labels ? labelStep : 0);
            Volatile.Write(ref _viewX, x0);
            Volatile.Write(ref _viewZ, z0);
            Volatile.Write(ref _viewW, x1 - x0);
            Volatile.Write(ref _viewH, z1 - z0);
            Volatile.Write(ref _lastDrawTick, Environment.TickCount);
            Interlocked.Increment(ref _frames);
        }

        /// <summary>
        /// Cell corner to GUI pixel, the same arithmetic Verse.GenMapUI does:
        /// Unity's screen origin is bottom-left, IMGUI's is top-left, and
        /// UI.screenHeight is already divided by Prefs.UIScale.
        /// </summary>
        private static Vector2 GuiPos(Camera camera, float scale, float x, float z)
        {
            var v = camera.WorldToScreenPoint(new Vector3(x, 0f, z)) / scale;
            return new Vector2(v.x, UI.screenHeight - v.y);
        }

        private static int FirstMultiple(int from, int step)
        {
            if (from <= 0)
                return 0;
            return ((from + step - 1) / step) * step;
        }
    }
}
