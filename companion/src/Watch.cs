using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using RimBridgeServer.Sdk;
using RimWorld;
using RimWorld.Planet;
using Verse;

namespace HomeBridge.BridgeTools
{
    /// <summary>
    /// The decorative half of a write. A viewer should see what a player would
    /// see: the thing selected and its menu open FIRST, then the change landing
    /// inside that menu, then the menu going away. The write itself stays
    /// direct; nothing here is load-bearing and nothing here can fail a call.
    ///
    /// Sequence, from a write tool:
    ///   hop 1 (main thread): resolve + validate; if the write is real and
    ///          watch is on, `session = Watch.Open(...)`.
    ///   off-thread: `await Watch.Lead(session, ct)` — a short pause so the
    ///          open menu is on screen for a moment before the change.
    ///   hop 2 (main thread): apply the write, read it back, then
    ///          `watch = Watch.Finish(session, watchSeconds)` schedules the close.
    ///   A dry run, a refusal or watch:false skips all three and emits
    ///   `Watch.Skipped(reason)`.
    ///
    /// ## Verified against the installed Assembly-CSharp.dll (RimWorld 1.6)
    ///
    ///   RimWorld.Selector.ClearSelection(), .Select(object, bool playSound,
    ///     bool forceDesignatorDeselect), .IsSelected(object), .NumSelected,
    ///     .FirstSelectedObject
    ///   RimWorld.MainTabsRoot.OpenTab (get), .SetCurrentTab(MainButtonDef, bool),
    ///     .EscapeCurrentTab(bool)
    ///   RimWorld.MainButtonDef.TabWindow -> MainTabWindow
    ///   RimWorld.MainTabWindow_Inspect.OpenTabType (get/set, Type)
    ///   RimWorld.InspectPaneUtility.OpenTab(Type) -> InspectTabBase
    ///   Verse.CameraDriver.JumpToCurrentMapLoc(IntVec3)
    ///   Verse.Zone.Position (cells[0], or IntVec3.Invalid on an empty zone)
    ///   RimWorld.Planet.WorldPawnsUtility.IsWorldPawn(this Pawn)
    ///   Verse.DefDatabase&lt;MainButtonDef&gt;.GetNamedSilentFail(string)
    ///
    /// ## The hazards this helper dodges
    ///
    /// `Selector.Select` reaches `Verse.Log.Error` — which calls
    /// `TickManager.Pause()` — for a null target, a target that is not a Thing,
    /// Zone or Plan, a destroyed Thing, and a world pawn. All four are
    /// pre-checked here, not caught. Unspawned things and things on another map
    /// are also refused: selecting across maps switches `Current.Game.CurrentMap`
    /// and indexes `Zone.Cells[0]`, neither of which a decorative step may do.
    ///
    /// The close runs from a `Task.Delay` continuation through the context's
    /// `IRimBridgeMainThread`, which is a stateless wrapper over a static queue
    /// (`RimBridgeMainThread.Pending`) pumped from a Harmony postfix on
    /// `Verse.Root.Update` — a Unity Update, so it pumps while the game is
    /// paused and long after the tool call that scheduled it returned. The
    /// operation's CancellationToken is therefore NOT used for that hop; it is
    /// cancelled when the call ends and would strand the menu on screen.
    ///
    /// The close undoes only what the session did: the main tab is escaped only
    /// while it is still the one this session switched to, the inspect
    /// `OpenTabType` cleared only while it is still ours, the selection cleared
    /// only while it is still exactly our one target. Anything else means a
    /// person or another call moved on, and their state is left alone. No
    /// Dialog or Window is ever closed.
    /// </summary>
    internal static class Watch
    {
        internal const int DefaultSeconds = 8;
        internal const int DefaultLeadMs = 1500;

        /// <summary>Close-after bounds. A zero would close inside the same frame
        /// the write landed in; a minute is already longer than any turn.</summary>
        private const int MinSeconds = 1;
        private const int MaxSeconds = 60;

        private static readonly object Sync = new object();

        /// <summary>The one session that may be open. Opening a second closes
        /// this one first, so two write tools in a row never stack menus.</summary>
        private static Session Current;

        /// <summary>What was opened, so Finish can close exactly that.</summary>
        internal sealed class Session
        {
            internal object Target;
            internal Type InspectTab;
            internal MainButtonDef MainTab;
            internal bool CameraMoved;
            internal bool Selected;
            internal bool TabOpened;
            internal bool AnythingShown;
            internal string Note;

            /// <summary>The main tab this session actually switched TO. Null when
            /// the tab was already open, which is the case the close must not
            /// undo.</summary>
            internal MainButtonDef OpenedMainTab;

            /// <summary>The ITab type actually showing on the inspect pane after
            /// Open, which is not always the one asked for: a pane whose target
            /// has no such tab opens nothing.</summary>
            internal Type OpenedInspectTab;

            /// <summary>The main tab on screen once Open has run, whether this
            /// session switched to it or found it already there. What the reply
            /// reports; OpenedMainTab is what the close is allowed to undo.</summary>
            internal MainButtonDef ShowingMainTab;

            /// <summary>Kept so the delayed close can hop back onto the main
            /// thread after the tool call has returned.</summary>
            internal IRimBridgeMainThread MainThread;

            internal int Seconds;
            internal bool Closed;
        }

        // ================================================================= open

        /// <summary>
        /// Main thread only. Select `target` (a Thing, Zone or Plan; null =
        /// select nothing), open `inspectTab` (an ITab type) on the inspect pane
        /// or `mainTab` (a MainButtonDef) as a player would, and jump the camera
        /// to the target when `camera` is true. Never throws; a session that
        /// could show nothing has AnythingShown false and a Note saying why.
        /// </summary>
        internal static Session Open(IRimBridgeContext ctx, object target, Type inspectTab, MainButtonDef mainTab, bool camera)
        {
            return OpenCore(ctx, target, inspectTab, mainTab, camera, IntVec3.Invalid);
        }

        /// <summary>
        /// Main thread. A session that shows a bare map cell: nothing is selected
        /// and no menu opens, the camera just goes there. For a write whose
        /// target does not exist yet - a blueprint about to be placed - so the
        /// viewer sees the empty spot before it fills. Hand the new thing to
        /// SelectNow once the write has made it.
        /// </summary>
        internal static Session OpenAtCell(IRimBridgeContext ctx, IntVec3 cell)
        {
            return OpenCore(ctx, null, null, null, true, cell);
        }

        private static Session OpenCore(IRimBridgeContext ctx, object target, Type inspectTab, MainButtonDef mainTab,
                                        bool camera, IntVec3 explicitCell)
        {
            var session = new Session
            {
                Target = target,
                InspectTab = inspectTab,
                MainTab = mainTab,
                MainThread = ctx == null ? null : ctx.MainThread
            };

            Session previous;
            lock (Sync)
            {
                previous = Current;
                Current = session;
            }
            CloseNow(previous);

            var notes = new List<string>();
            try
            {
                if (target != null)
                    Select(session, target, notes);

                var rootBefore = BridgeCommon.Try(() => Find.MainTabsRoot == null ? null : Find.MainTabsRoot.OpenTab, (MainButtonDef)null);

                if (inspectTab != null)
                    OpenInspectTab(session, inspectTab, notes);

                if (mainTab != null)
                {
                    if (inspectTab != null)
                        notes.Add("An inspect tab and a main tab were both asked for; the main tab is opened last and is what shows.");
                    OpenMainTab(session, mainTab, notes);
                }

                // InspectPaneUtility.OpenTab switches the main tab to Inspect on
                // its own. Record that as ours so the close puts it back.
                if (session.OpenedMainTab == null && inspectTab != null)
                {
                    var rootAfter = BridgeCommon.Try(() => Find.MainTabsRoot == null ? null : Find.MainTabsRoot.OpenTab, (MainButtonDef)null);
                    if (rootAfter != null && !ReferenceEquals(rootAfter, rootBefore))
                        session.OpenedMainTab = rootAfter;
                }

                if (camera)
                    JumpCamera(session, explicitCell.IsValid ? explicitCell : CellOf(target), notes);
            }
            catch (Exception ex)
            {
                notes.Add("The watch step threw " + ex.GetType().Name + " and was abandoned; the write itself is unaffected.");
            }

            if (session.TabOpened)
                session.ShowingMainTab = BridgeCommon.Try(
                    () => Find.MainTabsRoot == null ? null : Find.MainTabsRoot.OpenTab, (MainButtonDef)null);

            session.AnythingShown = session.Selected || session.TabOpened || session.CameraMoved;
            if (!session.AnythingShown && notes.Count == 0)
                notes.Add("Nothing was asked to be shown: no target, no tab and no camera move.");
            session.Note = notes.Count == 0 ? null : string.Join(" ", notes.ToArray());
            return session;
        }

        /// <summary>Select one target, pre-checking every case whose only report
        /// inside Selector.Select is a Log.Error.</summary>
        private static void Select(Session session, object target, List<string> notes)
        {
            var selector = BridgeCommon.Try(() => Find.Selector, (Selector)null);
            if (selector == null)
            {
                notes.Add("Find.Selector was not readable, so nothing was selected.");
                return;
            }

            var currentMap = BridgeCommon.Try(() => Find.CurrentMap, (Map)null);
            var thing = target as Thing;
            if (thing != null)
            {
                if (BridgeCommon.Try(() => thing.Destroyed, true))
                {
                    notes.Add("The target is destroyed, so nothing was selected.");
                    return;
                }
                if (!BridgeCommon.Try(() => thing.Spawned, false))
                {
                    notes.Add("The target is not spawned on a map, so nothing was selected.");
                    return;
                }
                var pawn = thing as Pawn;
                if (pawn != null && BridgeCommon.Try(() => pawn.IsWorldPawn(), false))
                {
                    notes.Add("The target is a world pawn, which cannot be selected.");
                    return;
                }
                if (currentMap != null && !ReferenceEquals(BridgeCommon.Try(() => thing.Map, (Map)null), currentMap))
                {
                    notes.Add("The target is on another map; selecting it would switch the viewed map, so nothing was selected.");
                    return;
                }
            }
            else
            {
                var zone = target as Zone;
                if (zone != null)
                {
                    if (BridgeCommon.Try(() => zone.CellCount, 0) == 0)
                    {
                        notes.Add("The target zone has no cells, so nothing was selected.");
                        return;
                    }
                    if (currentMap != null && !ReferenceEquals(BridgeCommon.Try(() => zone.Map, (Map)null), currentMap))
                    {
                        notes.Add("The target zone is on another map, so nothing was selected.");
                        return;
                    }
                }
                else if (!(target is Plan))
                {
                    notes.Add("The target is a " + target.GetType().Name + ", which the game cannot select; only a Thing, Zone or Plan can be.");
                    return;
                }
            }

            try
            {
                selector.ClearSelection();
                selector.Select(target, playSound: true, forceDesignatorDeselect: true);
                session.Selected = selector.IsSelected(target);
                session.Target = target;
                if (!session.Selected)
                    notes.Add("The game accepted the target but did not select it.");
            }
            catch (Exception ex)
            {
                notes.Add("Selecting the target threw " + ex.GetType().Name + ".");
            }
        }

        /// <summary>Open an ITab on the inspect pane the way a player clicking
        /// its tab does. A target with no such tab opens nothing, and says so.</summary>
        private static void OpenInspectTab(Session session, Type inspectTab, List<string> notes)
        {
            try
            {
                var opened = InspectPaneUtility.OpenTab(inspectTab);
                if (opened == null)
                {
                    notes.Add("The selected target has no " + inspectTab.Name + ", so no inspect tab was opened.");
                    return;
                }
                var pane = InspectPane();
                session.OpenedInspectTab = pane == null ? opened.GetType() : pane.OpenTabType;
                session.TabOpened = session.OpenedInspectTab != null;
            }
            catch (Exception ex)
            {
                notes.Add("Opening " + inspectTab.Name + " threw " + ex.GetType().Name + ".");
            }
        }

        /// <summary>Switch to a main tab. A tab already open is left alone and
        /// not recorded, so the close cannot take away a tab a person opened.</summary>
        private static void OpenMainTab(Session session, MainButtonDef mainTab, List<string> notes)
        {
            var root = BridgeCommon.Try(() => Find.MainTabsRoot, (MainTabsRoot)null);
            if (root == null)
            {
                notes.Add("Find.MainTabsRoot was not readable, so no main tab was opened.");
                return;
            }
            try
            {
                var before = root.OpenTab;
                if (ReferenceEquals(before, mainTab))
                {
                    session.TabOpened = true;
                    notes.Add("The " + DefNameOf(mainTab) + " tab was already open, so it is left open when this watch closes.");
                    return;
                }
                if (Find.TickManager != null && !Find.TickManager.Paused
                    && mainTab.TabWindow != null && mainTab.TabWindow.forcePause)
                {
                    notes.Add("Skipped the " + DefNameOf(mainTab)
                        + " watch tab because it would force-pause the running game; the settings write still applies.");
                    return;
                }
                root.SetCurrentTab(mainTab, playSound: true);
                if (ReferenceEquals(root.OpenTab, mainTab))
                {
                    session.OpenedMainTab = mainTab;
                    session.TabOpened = true;
                }
                else
                {
                    notes.Add("The game did not switch to the " + DefNameOf(mainTab) + " tab.");
                }
            }
            catch (Exception ex)
            {
                notes.Add("Opening the " + DefNameOf(mainTab) + " tab threw " + ex.GetType().Name + ".");
            }
        }

        /// <summary>Jump the camera to the target, or leave it where it is when
        /// there is no target cell to jump to.</summary>
        private static void JumpCamera(Session session, IntVec3 cell, List<string> notes)
        {
            if (!cell.IsValid)
            {
                notes.Add("No map cell could be read for the target, so the camera did not move.");
                return;
            }
            var driver = BridgeCommon.Try(() => Find.CameraDriver, (CameraDriver)null);
            if (driver == null)
            {
                notes.Add("Find.CameraDriver was not readable, so the camera did not move.");
                return;
            }
            try
            {
                driver.JumpToCurrentMapLoc(cell);
                session.CameraMoved = true;
            }
            catch (Exception ex)
            {
                notes.Add("Moving the camera threw " + ex.GetType().Name + ".");
            }
        }

        // ================================================================= lead

        /// <summary>
        /// Off the main thread. Waits DefaultLeadMs when the session showed
        /// something, so the open menu is visible before the change lands.
        /// Returns immediately for a null session or one that showed nothing.
        /// A cancelled wait is swallowed: the write behind it still has to run.
        /// </summary>
        internal static async Task Lead(Session session, CancellationToken cancellationToken)
        {
            if (session == null || !session.AnythingShown)
                return;
            try
            {
                await Task.Delay(DefaultLeadMs, cancellationToken).ConfigureAwait(false);
            }
            catch (Exception)
            {
                // A cancelled or failed pause is cosmetic; never fail the write.
            }
        }

        // =============================================================== finish

        /// <summary>
        /// Main thread only, after the write. Schedules the close of everything
        /// the session opened after `seconds` (clamped 1..60), and returns the
        /// reply's `watch{}` block.
        /// </summary>
        internal static Dictionary<string, object> Finish(Session session, int seconds)
        {
            if (session == null)
                return Skipped("no session");

            var clamped = seconds < MinSeconds ? MinSeconds : (seconds > MaxSeconds ? MaxSeconds : seconds);
            session.Seconds = clamped;

            if (session.AnythingShown)
            {
                if (session.MainThread == null)
                {
                    // Nothing can hop back to close it later, so close it now
                    // rather than leave a menu on screen for good.
                    CloseNow(session);
                    session.Note = Join(session.Note, "No main-thread dispatcher was available to schedule the close, so it was closed immediately.");
                    clamped = 0;
                }
                else
                {
                    ScheduleClose(session, clamped);
                }
            }
            else
            {
                lock (Sync)
                {
                    if (ReferenceEquals(Current, session))
                        Current = null;
                }
            }

            var block = Block();
            block["shown"] = session.AnythingShown;
            block["selected"] = session.Selected;
            block["inspectTab"] = session.OpenedInspectTab == null ? null : session.OpenedInspectTab.Name;
            block["mainTab"] = OpenMainTabName(session);
            block["cameraMoved"] = session.CameraMoved;
            block["leadMs"] = session.AnythingShown ? DefaultLeadMs : 0;
            block["closesAfterSeconds"] = session.AnythingShown ? clamped : 0;
            block["note"] = session.Note;
            block["reason"] = session.AnythingShown ? null : (session.Note ?? "nothing to show");
            return block;
        }

        /// <summary>The `watch{}` block when nothing was shown: a dry run, a
        /// refusal, or watch:false. Same key set as Finish, so a caller reading
        /// watch.shown never has to ask which shape it got.</summary>
        internal static Dictionary<string, object> Skipped(string reason)
        {
            var block = Block();
            block["reason"] = reason;
            return block;
        }

        /// <summary>Every key, at their nothing-happened values.</summary>
        private static Dictionary<string, object> Block()
        {
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "shown", false },
                { "selected", false },
                { "inspectTab", null },
                { "mainTab", null },
                { "cameraMoved", false },
                { "leadMs", 0 },
                { "closesAfterSeconds", 0 },
                { "note", null },
                { "reason", null }
            };
        }

        // ================================================== additions for hop 2

        /// <summary>
        /// Main thread only. Select something that did not exist when Open ran —
        /// a blueprint the write just placed — and hand the close the job of
        /// deselecting it. Returns whether it is now selected.
        /// </summary>
        internal static bool SelectNow(Session session, object target)
        {
            if (session == null || target == null)
                return false;
            var notes = new List<string>();
            Select(session, target, notes);
            if (notes.Count > 0)
                session.Note = Join(session.Note, string.Join(" ", notes.ToArray()));
            if (session.Selected)
                session.AnythingShown = true;
            return session.Selected;
        }

        /// <summary>
        /// The MainButtonDef with this defName, or null. MainButtonDefOf carries
        /// only Inspect, Architect, Research, Menu, World, Quests, Factions and
        /// Ideos — Work, Schedule, Assign, Animals and Wildlife are real defs
        /// with no DefOf field, so they are looked up by name. Main thread only.
        /// </summary>
        internal static MainButtonDef Tab(string defName)
        {
            if (string.IsNullOrEmpty(defName))
                return null;
            return BridgeCommon.Try(() => DefDatabase<MainButtonDef>.GetNamedSilentFail(defName), (MainButtonDef)null);
        }

        // ================================================================ close

        /// <summary>Wait, then hop back onto the main thread and close. The
        /// operation token is deliberately not passed: it is cancelled the
        /// moment the tool call returns, which is before this is due.</summary>
        private static void ScheduleClose(Session session, int seconds)
        {
            var mainThread = session.MainThread;
            Task.Delay(TimeSpan.FromSeconds(seconds)).ContinueWith(
                delegate
                {
                    try
                    {
                        var pending = mainThread.InvokeAsync(() => CloseNow(session), CancellationToken.None);
                        if (pending != null)
                            pending.ContinueWith(t => { var ignored = t.Exception; }, TaskScheduler.Default);
                    }
                    catch (Exception)
                    {
                        // The game may be shutting down; a menu left open is not
                        // worth an unhandled exception on a background thread.
                    }
                },
                TaskScheduler.Default);
        }

        /// <summary>Main thread. Undo exactly what this session did, and only
        /// while the game still shows it. Runs at most once per session.</summary>
        private static void CloseNow(Session session)
        {
            if (session == null)
                return;
            lock (Sync)
            {
                if (session.Closed)
                    return;
                session.Closed = true;
                if (ReferenceEquals(Current, session))
                    Current = null;
            }

            try
            {
                if (session.OpenedInspectTab != null)
                {
                    var pane = InspectPane();
                    if (pane != null && pane.OpenTabType == session.OpenedInspectTab)
                        pane.OpenTabType = null;
                }

                if (session.OpenedMainTab != null)
                {
                    var root = BridgeCommon.Try(() => Find.MainTabsRoot, (MainTabsRoot)null);
                    if (root != null && ReferenceEquals(root.OpenTab, session.OpenedMainTab))
                        root.EscapeCurrentTab(playSound: true);
                }

                if (session.Selected && session.Target != null)
                {
                    var selector = BridgeCommon.Try(() => Find.Selector, (Selector)null);
                    if (selector != null && selector.NumSelected == 1
                        && ReferenceEquals(selector.FirstSelectedObject, session.Target))
                        selector.ClearSelection();
                }
            }
            catch (Exception)
            {
                // Closing is decorative too. A stuck menu is a cosmetic fault;
                // an exception out of a main-thread work item is a Log.Error.
            }
        }

        // =============================================================== helpers

        /// <summary>The inspect pane instance, through the def that owns it.</summary>
        private static MainTabWindow_Inspect InspectPane()
        {
            var def = Tab("Inspect");
            if (def == null)
                return null;
            return BridgeCommon.Try(() => def.TabWindow as MainTabWindow_Inspect, (MainTabWindow_Inspect)null);
        }

        /// <summary>The cell the camera should jump to for a target.</summary>
        private static IntVec3 CellOf(object target)
        {
            var thing = target as Thing;
            if (thing != null)
                return BridgeCommon.Try(() => thing.PositionHeld, IntVec3.Invalid);
            var zone = target as Zone;
            if (zone != null)
                return BridgeCommon.Try(() => zone.Position, IntVec3.Invalid);
            var plan = target as Plan;
            if (plan != null)
                return BridgeCommon.Try(() => plan.Cells.Count == 0 ? IntVec3.Invalid : plan.Cells[0], IntVec3.Invalid);
            return IntVec3.Invalid;
        }

        /// <summary>The main tab named in the reply: whatever is on screen
        /// because this session opened a tab, whether it switched to it or found
        /// it there. Null when the session opened no tab at all - a camera-only
        /// session never claims a tab a person left open.</summary>
        private static string OpenMainTabName(Session session)
        {
            return session.TabOpened ? DefNameOf(session.ShowingMainTab) : null;
        }

        private static string DefNameOf(MainButtonDef def)
        {
            return def == null ? null : BridgeCommon.SafeString(() => def.defName);
        }

        private static string Join(string existing, string addition)
        {
            if (string.IsNullOrEmpty(existing))
                return addition;
            return string.IsNullOrEmpty(addition) ? existing : existing + " " + addition;
        }
    }
}
