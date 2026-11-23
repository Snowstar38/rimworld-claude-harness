using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using RimBridgeServer.Sdk;
using RimWorld;
using Verse;

namespace HomeBridge.BridgeTools
{
    /// <summary>
    /// home/trade — the whole trade, headless. No dialog, no scrolling, no clicking.
    ///
    /// ## Why this exists (M, 2026-09-01)
    ///
    ///     "the trading screen was a fiasco and we definitely need a custom
    ///      bridge part for trading"
    ///
    /// The UI path was not merely slow, it was WRONG in a way that reported
    /// success. `Dialog_Trade`'s per-row `&lt;` / `&lt;&lt;` / `&gt;` / `&gt;&gt;` buttons each
    /// carry their own `targetId` and `click_ui_target` returns `success: true`
    /// on any of them — but every one of them acts on whichever row is at the
    /// TOP of the viewport. A session bought a warg twice while trying to buy a
    /// parka. The workaround (scroll the wanted row to the top, then click any
    /// button) is bounded by `maxOffsetY`, which on the live screen was 248px
    /// of a ~25-row stock: about nine rows. Everything below the cut was
    /// literally unbuyable, and the `Sort by` header buttons did not respond to
    /// `click_ui_target` at all, so the list could not be reordered to bring an
    /// item into range.
    ///
    /// None of that is a limitation of RimWorld. It is a limitation of driving
    /// a scroll view through a screen-reader. RimWorld's actual trade model is
    /// a flat list of `Tradeable` objects hanging off one static
    /// `TradeSession`, and `Dialog_Trade` is a pure VIEW over it: its
    /// constructor's only non-cosmetic act is `TradeSession.SetupWith(trader,
    /// negotiator, giftsOnly)` (verified in IL, Assembly-CSharp
    /// 1.6.9676.17735). So this tool does what the dialog does and skips the
    /// dialog.
    ///
    /// ## Sign convention — verified, not assumed
    ///
    /// `Tradeable.CountToTransfer` is signed. **Positive = the colony gains =
    /// the player BUYS. Negative = the player SELLS.** That is not folklore; it
    /// is read out of three IL bodies:
    ///
    ///   * `Tradeable.get_PositiveCountDirection` returns `Source` (0) unless
    ///     `TradeSession.giftMode`.
    ///   * `Transferable.get_CountToTransferToSource` returns `+CountToTransfer`
    ///     when the direction is `Source`, so `CountPostDealFor(Colony)` is
    ///     `CountHeldBy(Colony) + CountToTransfer`.
    ///   * `Tradeable.get_ActionToDo` returns `PlayerBuys` when
    ///     `CountToTransferToDestination &lt;= 0`, i.e. when `CountToTransfer &gt; 0`.
    ///
    /// `GetMinimumToTransfer()` is `-CountHeldBy(Colony)` (sell everything) and
    /// `GetMaximumToTransfer()` is `CountHeldBy(Trader)` (buy everything), so
    /// the legal range of a line is `[-colonyCount, +traderCount]`.
    ///
    /// ## Selling a PAWN needs `allowPawns: true`
    ///
    /// `TradeUtility.AllSellableColonyPawns` feeds the sheet, so colonists,
    /// prisoners and colony animals appear as ordinary rows with ordinary
    /// prices. Nothing in RimWorld stops a negative count on one, and this tool
    /// can address every row by name -- which makes "sell 3 of the thing whose
    /// label happened to match" a way to sell a person by typo. A previous
    /// session bought a warg twice while trying to buy a parka through the
    /// dialog; the same class of mistake here is irreversible and it is a
    /// colonist.
    ///
    /// So `set` REFUSES any row whose tradeable is a pawn when the count would
    /// make the colony part with it, unless `allowPawns: true` is passed. The
    /// refusal names the row and says exactly which flag lifts it.
    ///
    /// **The guard is on SELLING only.** Buying animals from a trader is
    /// ordinary play -- this colony's tame warg was bought from a war merchant
    /// -- and a guard that blocked it would be a guard nobody keeps on.
    /// "Selling" is `CountToTransfer &lt; 0` in a trade session and
    /// `CountToTransfer &gt; 0` in a GIFT session, because gift mode flips
    /// `PositiveCountDirection` to `Destination`; the check follows the
    /// direction rather than the raw sign.
    ///
    /// ## Three mutation hazards this tool refuses to walk into
    ///
    /// 1. **`Transferable.AdjustTo` calls `Log.Error` when `CanAdjustTo` fails,
    ///    and `Log.Error` calls `TickManager.Pause()`** when
    ///    `DebugSettings.pauseOnError` is on and the program state is Playing
    ///    (both verified in IL). A rejected line would therefore PAUSE THE
    ///    GAME as a side effect of a failed set. So every line is checked with
    ///    `CanAdjustTo` first and `AdjustTo` is only ever called on a value the
    ///    game has already accepted. A rejected line comes back as a structured
    ///    per-line failure and changes nothing.
    ///
    /// 2. **`TradeDeal.TryExecute` dereferences the dialog.** Its
    ///    cannot-afford branch is
    ///    `Find.WindowStack.WindowOfType&lt;Dialog_Trade&gt;().FlashSilver()` with no
    ///    null check. Headless, `WindowOfType` returns null and the call throws
    ///    NullReferenceException across the bridge. So `accept` evaluates
    ///    exactly the same predicate first —
    ///    `CurrencyTradeable != null &amp;&amp; CurrencyTradeable.CountPostDealFor(Colony) &gt;= 0` —
    ///    and returns `cannot_afford` without ever calling `TryExecute`.
    ///
    /// 3. **`TradeSession.SetupWith` calls `Log.Warning` when the trader is not
    ///    willing to trade.** `Log.Warning` does not pause, but it can open the
    ///    log window on a streaming screen if `Prefs.OpenLogOnWarnings` is set.
    ///    So `open` refuses on `!CanTradeNow` before calling `SetupWith`.
    ///
    /// ## Does the negotiator have to be standing next to the trader?
    ///
    /// **No, and this is the honest answer rather than a convenient one.**
    /// Nothing on the trade path checks distance: not `TradeSession.SetupWith`,
    /// not the `TradeDeal` constructor, not `Tradeable.ResolveTrade`, not
    /// `TradeDeal.TryExecute`. `Pawn_TraderTracker.CanTradeNow` checks dead,
    /// spawned, `wantsToTradeWithColony`, `CanCasuallyInteractNow`, downed,
    /// prisoner, faction and stock — no distance term. The adjacency in vanilla
    /// comes from the INPUT path only: right-clicking a trader queues
    /// `JobDefOf.TradeWithPawn`, the colonist walks, and the job driver opens
    /// the dialog on arrival. Skipping the walk skips the walk; it does not
    /// skip a rule.
    ///
    /// Two consequences worth knowing, both verified:
    ///
    ///   * The negotiator's Social skill and `TradePriceImprovement` stat still
    ///     set the prices, so WHO negotiates matters even though WHERE they
    ///     stand does not.
    ///   * `Pawn_TraderTracker.GiveSoldThingToPlayer` places bought goods at the
    ///     TRADER's cell, not the negotiator's, and adds them to the trader
    ///     lord's `extraForbiddenThings` — so a purchase lands on the ground
    ///     next to the caravan, forbidden, until the caravan leaves. That is
    ///     vanilla, and it is the same either way.
    ///
    /// `requireAdjacent: true` restores the vanilla feel on purpose: `open`
    /// then fails loudly with the measured distance so the caller can walk
    /// there first. It defaults to FALSE because the game does not require it
    /// and a tool that invents a rule is a tool that lies.
    ///
    /// ## What this tool does NOT do
    ///
    ///   * It never opens `Dialog_Trade`, and it refuses to mutate a session
    ///     while one is open on screen, because the dialog caches its own row
    ///     list in `cachedTradeables` at `PostOpen` and would draw a stale one.
    ///     `action: "close_dialog"` is there for exactly the fiasco case: a
    ///     dialog left open by a previous session.
    ///   * It does not walk anybody anywhere. Use `move.py`.
    ///   * `accept` does not create a caravan, launch drop pods, or handle the
    ///     player-is-a-caravan case. Colony-side trading only.
    /// </summary>
    public sealed class HomeTradeTools
    {
        private const string ToolName = "home/trade";

        // The bridge runs one tool call at a time, and every mutation below
        // happens inside a single ctx.MainThread hop, so the game state is only
        // ever touched from RimWorld's own thread. This guard exists for the
        // one case that is not covered by either fact: two overlapping bridge
        // invocations racing on the static session bookkeeping below.
        private static int _running;

        // Session bookkeeping. The DEAL itself is not stored here — it lives in
        // RimWorld's own static TradeSession, which is what makes the session
        // survive across bridge calls for free. These fields only record what
        // THIS tool did, so a session opened by the vanilla dialog can be told
        // apart from one we opened.
        private static string _sessionTraderId;
        private static string _sessionTraderName;
        private static string _sessionNegotiatorName;
        private static int _sessionOpenedTick;
        private static bool _sessionOpenedByUs;

        private static readonly FieldInfo LiveMessagesField =
            BridgeCommon.PrivateStaticField(typeof(Messages), "liveMessages");

        [Tool(
            ToolName,
            Title = "Trade with a caravan or orbital trader without the dialog",
            Description =
                "One tool for a whole trade, driven by an 'action' parameter: list_traders, open, sheet, set, preview, "
                + "accept, cancel, close_dialog, status. Sets up RimWorld's own TradeSession directly, so the full stock "
                + "sheet is readable in one call and any row can be bought or sold by name — neither of which the trade "
                + "dialog's scroll view allows. Counts are signed: POSITIVE means the colony buys, NEGATIVE means the "
                + "colony sells. SELLING a pawn (colonist, prisoner or animal) is refused unless allowPawns:true is "
                + "passed; buying one is always allowed.",
            ResultDescription =
                "success, action, and an action-specific payload. Errors are structured results with success:false, "
                + "error and errorKind — never exceptions.")]
        [ToolResponse("action", "string", "The action that ran. Always present.", Always = true)]
        [ToolResponse("sessionActive", "boolean", "Whether a TradeSession is open after this call. Always present.", Always = true)]
        [ToolResponse("unknownArguments", "array", "Every argument key the caller sent that this tool does not declare, sorted, case-sensitively. Empty array = every key was recognised. The host's own _rimBridgeTimeoutMs is never listed.", Always = true)]
        [ToolResponse("unknownArgumentsWarning", "string", "Present only when unknownArguments is non-empty, or when the caller's raw keys could not be read at all - in which case the empty unknownArguments means 'not known', not 'nothing unknown'. On a WRITE tool this matters twice over: a misspelled allowPawns or relative changes what the colony hands over.", Nullable = true)]
        [ToolResponse("omittedUntradeable", "integer", "On 'sheet' and 'open': how many rows were dropped because this trader REFUSES to trade them (Tradeable.TraderWillTrade false). Present on those two actions only. 0 with includeUntradeable:true, which keeps them. rowCount is the trader's WHOLE sheet and COUNTS these rows; rowsReturned is what came back, and rowCount - rowsReturned = omittedUntradeable + omittedByFilter + omittedByRowCap. Row indices stay absolute either way.")]
        [ToolResponse("rows", "array", "On 'sheet' and 'open': index (ABSOLUTE, unaffected by any filter), label, defName, stuff, category, colonyCount, traderCount, buyPrice, sellPrice, buyPriceType, sellPriceType, baseMarketValue, traderWillTrade, isCurrency, isPawn, pawnDescription, countToTransfer, actionToDo, minCount, maxCount. traderWillTrade is on EVERY emitted row; by default rows where it is false are not emitted at all and are counted in omittedUntradeable, because a row the trader refuses read as 'staged' until the sell bounced.")]
        [ToolResponse("watch", "object", "What a person watching actually saw. The nine standard keys - shown (bool), selected, inspectTab, mainTab, cameraMoved, leadMs, closesAfterSeconds, note, reason - plus TWO that only this tool has: dialogShown (bool) and secondsShown (int). On a real accept the trader is selected and the camera jumps to them, then the REAL Dialog_Trade opens showing the staged rows, is held for secondsShown, the deal executes while it is on screen, and the window closes. dialogShown:false with secondsShown:0 on every other action, on a refusal, when watch:false, and when a person already had a trade window open. A trade that nobody could see happen was the whole reason this exists.", Always = true)]
        public async Task<object> Trade(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            [ToolParameter(Description = "list_traders | open | sheet | set | preview | accept | cancel | close_dialog | status", DefaultValue = "list_traders")] string action = "list_traders",
            [ToolParameter(Description = "For 'open': the trader's id from list_traders, or a unique substring of its name. Required for open.")] string traderId = null,
            [ToolParameter(Description = "For 'open': the negotiating colonist's name or stable pawn id. Omit to pick the best available colonist by TradePriceImprovement, then Social skill.")] string negotiator = null,
            [ToolParameter(Description = "For 'open': set up a GIFT session instead of a trade. Gifts flip the sign convention (positive then means the colony gives) and buy nothing.", DefaultValue = false)] bool giftMode = false,
            [ToolParameter(Description = "For 'set': one row, addressed by defName, exact label, unique label substring, or '#N' where N is the row index from the sheet.")] string item = null,
            [ToolParameter(Description = "For 'set' with 'item': the signed count. POSITIVE = the colony BUYS that many. NEGATIVE = the colony SELLS that many. 0 clears the row.", DefaultValue = 0)] int count = 0,
            [ToolParameter(Description = "For 'set': several rows at once. Entries separated by ';' or newline, each 'name:count' or 'name=count' (split at the LAST separator), e.g. 'Pemmican:180; MedicineHerbal:19; MeleeWeapon_Club:-3'. The silver row is computed, never set.")] string lines = null,
            [ToolParameter(Description = "For 'set': add to the row's current count instead of replacing it.", DefaultValue = false)] bool relative = false,
            [ToolParameter(Description = "For 'set': permit SELLING a pawn — a colonist, prisoner or colony animal. Without it any line that would hand a pawn over is refused by name and nothing is staged. BUYING a pawn from a trader is always allowed and never needs this.", DefaultValue = false)] bool allowPawns = false,
            [ToolParameter(Description = "For 'sheet': only rows whose label or defName contains this text, case-insensitive. Row indices stay absolute.")] string match = null,
            [ToolParameter(Description = "For 'sheet': only rows with a non-zero staged count.", DefaultValue = false)] bool onlyChanged = false,
            [ToolParameter(Description = "For 'sheet': maximum rows to return. The payload always reports how many were omitted.", DefaultValue = 500)] int maxRows = 500,
            [ToolParameter(Description = "For 'sheet' and 'open': keep rows this trader REFUSES to trade (Tradeable.TraderWillTrade false). FALSE by default, because a refused row cannot be bought or sold and a caller that stages one gets the line rejected. The count dropped is always reported as omittedUntradeable, never a silent drop.", DefaultValue = false)] bool includeUntradeable = false,
            [ToolParameter(Description = "For 'open': refuse unless the negotiator is adjacent to a map trader, the way the vanilla right-click job would leave them. The game itself does NOT require this; default false.", DefaultValue = false)] bool requireAdjacent = false,
            [ToolParameter(Description = "For 'accept': allow executing a deal in which nothing is staged. Normally that is refused as a caller mistake.", DefaultValue = false)] bool allowEmpty = false,
            [ToolParameter(Description = "For 'accept', 'cancel' and 'close_dialog': mirror the vanilla dialog by handing over a trader's quest (TradeUtility.ReceiveQuestFromTrader) when the trader has one.", DefaultValue = true)] bool receiveQuest = true,
            [ToolParameter(Description = "TRUE by default. For 'accept' only: select the trader, put the camera on them, then OPEN THE REAL TRADE WINDOW showing the staged rows, hold it watchSeconds, execute the deal while it is on screen, and close it. It never changes what is traded - the deal is verified against the preview before and after the window goes up, and a change refuses with restage_mismatch rather than trading something else. Pass false to accept headlessly, with no window and no camera move.", DefaultValue = true)] bool watch = true,
            [ToolParameter(Description = "How long the REAL trade window is held on screen before the deal executes, and how long the trader stays selected after it. Clamped 1..60. The tool call blocks for this long and the bridge runs one call at a time, so an accept occupies the bridge for about this many seconds - that is the price of the trade being visible. Ignored on every action but 'accept', and when watch is false.", DefaultValue = 8)] int watchSeconds = 8)
        {
            return BridgeCommon.WithUnknownArguments(
                await TradeCore(
                    ctx, cancellationToken, action, traderId, negotiator, giftMode, item, count, lines,
                    relative, allowPawns, match, onlyChanged, maxRows, includeUntradeable, requireAdjacent, allowEmpty,
                    receiveQuest, watch, watchSeconds).ConfigureAwait(false),
                ctx, typeof(HomeTradeTools), ToolName);
        }

        private async Task<object> TradeCore(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            string action,
            string traderId,
            string negotiator,
            bool giftMode,
            string item,
            int count,
            string lines,
            bool relative,
            bool allowPawns,
            string match,
            bool onlyChanged,
            int maxRows,
            bool includeUntradeable,
            bool requireAdjacent,
            bool allowEmpty,
            bool receiveQuest,
            bool watch,
            int watchSeconds)
        {
            if (ctx?.MainThread == null)
                return Failure("No RimBridge main-thread dispatcher is available for this invocation.", "no_dispatcher", action);

            if (Interlocked.CompareExchange(ref _running, 1, 0) != 0)
                return Failure("Another " + ToolName + " call is already running.", "busy", action);

            try
            {
                var args = new Args
                {
                    Action = (action ?? string.Empty).Trim(),
                    TraderId = traderId,
                    Negotiator = negotiator,
                    GiftMode = giftMode,
                    Item = item,
                    Count = count,
                    Lines = lines,
                    Relative = relative,
                    AllowPawns = allowPawns,
                    Match = match,
                    OnlyChanged = onlyChanged,
                    MaxRows = maxRows < 1 ? 1 : (maxRows > 5000 ? 5000 : maxRows),
                    IncludeUntradeable = includeUntradeable,
                    RequireAdjacent = requireAdjacent,
                    AllowEmpty = allowEmpty,
                    ReceiveQuest = receiveQuest
                };

                // Companion tools dispatch with MarshalToMainThread = false, so
                // every read and every write below has to be hopped onto
                // RimWorld's main thread by hand. Removing this hop still
                // compiles and fails intermittently, which is the worst
                // failure mode available.
                if (!string.Equals(args.Action, "accept", StringComparison.OrdinalIgnoreCase))
                {
                    var plain = await ctx.MainThread
                        .InvokeAsync(() => Dispatch(args), cancellationToken)
                        .ConfigureAwait(false);
                    Stamp(plain, Watch.Skipped("action is not watched"));
                    return plain;
                }

                // accept is the one action a viewer should see land. Hop 1
                // selects the trader and puts the camera on them; hop 2 executes
                // the deal, so the goods move while they are on screen.
                var session = await ctx.MainThread
                    .InvokeAsync(() => WatchTrader(ctx, watch), cancellationToken)
                    .ConfigureAwait(false);

                await Watch.Lead(session.Session, cancellationToken).ConfigureAwait(false);

                var live = session.Session;
                var reason = session.SkipReason;

                // Hop 2: put the REAL trade window on screen over the deal that
                // is already staged. See ShowTradeDialog for why this swaps the
                // TradeDeal object rather than re-applying counts.
                var shownSeconds = ClampSeconds(watchSeconds);
                var dialog = await ctx.MainThread
                    .InvokeAsync(() => ShowTradeDialog(watch), cancellationToken)
                    .ConfigureAwait(false);

                if (dialog.Mismatch != null)
                {
                    // Nothing executed, nothing closed, session untouched.
                    var refused = Failure(dialog.Mismatch, "restage_mismatch", "accept");
                    refused["stagedExpected"] = dialog.Expected;
                    refused["stagedActual"] = dialog.Actual;
                    refused["note"] =
                        "Dialog_Trade's constructor calls TradeSession.SetupWith, which builds a new TradeDeal and "
                        + "wipes every staged count. The original deal is put back before the window is shown; this "
                        + "refusal means that did not hold, so nothing was traded that was not previewed.";
                    AddSessionBlock(refused);
                    Stamp(refused, WithDialog(
                        live == null ? Watch.Skipped(reason) : Watch.Finish(live, watchSeconds), false, 0));
                    return refused;
                }

                // The hold. This BLOCKS the tool call, deliberately: the deal must
                // execute AFTER the window has been seen, and keeping the execute
                // inside the call is what lets the reply say honestly what
                // happened. The bridge runs one call at a time, so an accept now
                // occupies the bridge for about watchSeconds -- the cost of the
                // trade being visible at all. It does not block the GAME thread:
                // this wait is off the main thread, between two hops, exactly the
                // shape Watch.Lead already uses.
                if (dialog.Shown)
                {
                    try
                    {
                        await Task.Delay(TimeSpan.FromSeconds(shownSeconds), cancellationToken).ConfigureAwait(false);
                    }
                    catch (Exception)
                    {
                        // A cancelled hold is cosmetic. The deal still executes,
                        // and the window is still closed below.
                    }
                }

                var wasShown = dialog.Shown;
                return await ctx.MainThread
                    .InvokeAsync(() =>
                    {
                        var reply = Dispatch(args);
                        // Always, on every outcome: executed, refused or threw.
                        // After Dispatch, so TryExecute still had a real
                        // Dialog_Trade to reach for on its cannot-afford branch.
                        CloseOurTradeDialog();
                        Stamp(reply, WithDialog(
                            live == null ? Watch.Skipped(reason) : Watch.Finish(live, watchSeconds),
                            wasShown, wasShown ? shownSeconds : 0));
                        return reply;
                    }, cancellationToken)
                    .ConfigureAwait(false);
            }
            catch (Exception e)
            {
                // Nothing crosses the bridge as an exception. Ever.
                return Failure(e.GetType().Name + ": " + e.Message, "exception", action);
            }
            finally
            {
                // A Dialog_Trade left on screen would be the worst thing this
                // tool could do: its constructor sets forcePause AND
                // absorbInputAroundWindow, so a stranded one pauses the colony
                // and eats every click, with no X and no close button drawn. If
                // the call was cancelled during the hold, or hop 3 never ran, the
                // close below is the only thing that takes it away.
                //
                // CancellationToken.None deliberately: the operation's own token
                // is already cancelled in exactly the case this exists for. The
                // hop is fire-and-forget through the same main-thread queue
                // Watch's deferred close uses, which is pumped from Root.Update
                // and so runs even while the game is paused.
                EmergencyCloseDialog(ctx);
                Interlocked.Exchange(ref _running, 0);
            }
        }

        /// <summary>Put the watch block on a reply, so `watch` is present on
        /// every action and every refusal and never has to be tested for.</summary>
        private static void Stamp(object reply, Dictionary<string, object> watch)
        {
            var payload = reply as Dictionary<string, object>;
            if (payload != null)
                payload["watch"] = watch;
        }

        private sealed class WatchPass
        {
            internal Watch.Session Session;
            internal string SkipReason;
        }

        /// <summary>Main thread. Select the trader this session is with, if it is
        /// a pawn standing on the map. Nothing here reads or touches the deal:
        /// accept re-checks every precondition itself in hop 2, and this must not
        /// mutate a thing.</summary>
        private static WatchPass WatchTrader(IRimBridgeContext ctx, bool wantWatch)
        {
            if (!wantWatch)
                return new WatchPass { SkipReason = "watch:false" };

            string why;
            if (!RequireSession(out why))
                return new WatchPass { SkipReason = "refused" };
            if (OpenTradeDialog() != null)
                return new WatchPass { SkipReason = "refused" };

            var traderPawn = SafeTraderPawn();
            if (traderPawn == null)
                return new WatchPass { SkipReason = "the trader is not a pawn on this map" };

            return new WatchPass { Session = Watch.Open(ctx, traderPawn, null, null, true) };
        }

        private static Pawn SafeTraderPawn()
        {
            try { return TradeSession.trader as Pawn; }
            catch { return null; }
        }

        // ==================================================== the trade window
        //
        // M did not know a trade had happened last session. Selecting the
        // trader and moving the camera is not enough: a deal executed headless
        // looks like nothing at all. So `accept` now opens the REAL Dialog_Trade,
        // holds it on screen, executes while it is up, and closes it.
        //
        // ## The wrinkle, and why this is not a "re-stage"
        //
        // `Dialog_Trade`'s only constructor is
        // `Dialog_Trade(Pawn playerNegotiator, ITrader trader, bool giftsOnly)`
        // and its body calls `TradeSession.SetupWith(...)`, which ends with an
        // unconditional `deal = new TradeDeal()`. `TradeDeal`'s constructor calls
        // `Reset()`, which clears the tradeable list and rebuilds it from
        // scratch. So merely constructing the window WIPES EVERY STAGED COUNT,
        // and `Tradeable` instance identity is not stable across two deals.
        //
        // The obvious repair -- snapshot (thing, count) pairs and re-apply them
        // to the new deal with `TransferableUtility.TradeableMatching` -- is
        // reachable but lossy: `TransferAsOne` groups on hit points within 10,
        // quality, stuff and `tradeNeverStack`, and the synthetic zero-stack
        // silver row is a brand new Thing on every deal and so matches nothing.
        // Any of those turns "the same trade" into "a similar trade", which is
        // the one thing a deal a person previewed may never become.
        //
        // `TradeSession.deal` is a PUBLIC STATIC FIELD, so there is an exact
        // route: keep the original `TradeDeal` OBJECT, let the constructor
        // install its throwaway, then put the original back BEFORE the window is
        // added to the stack. `Window.PostOpen` -> `Dialog_Trade.CacheTradeables`
        // reads `TradeSession.deal.AllTradeables` and stores REFERENCES to those
        // same `Tradeable` objects, so the dialog caches the original deal and
        // draws the original staged counts. Nothing is re-applied, nothing is
        // matched, and the rows on screen are the same objects `preview` costed.
        //
        // `Dialog_Trade.Close(bool)` neither nulls `TradeSession.trader` nor
        // calls `TradeSession.Close()` (which has no callers anywhere in
        // Assembly-CSharp), and it pops NO confirmation for pending changes --
        // the class does not override PreClose/PostClose/OnCancelKeyPressed, and
        // its only Dialog_MessageBoxes are the impaired-negotiator notice and the
        // trader-short-funds prompt. So the session and its counts survive the
        // window closing, which is what makes this sequence possible at all.
        //
        // The deal is still verified after the swap and again before it executes.
        // A mismatch REFUSES with errorKind "restage_mismatch" and executes
        // nothing: nothing is traded that was not previewed.

        /// <summary>The Dialog_Trade this tool put on screen, so `accept` can
        /// tell its own window from one a person opened -- which it still
        /// refuses to execute over.</summary>
        private static Window _ourTradeDialog;

        /// <summary>The staged signature at the moment the window went up. The
        /// deal is checked against this again before it executes, so a change
        /// during the hold refuses instead of trading something else.</summary>
        private static string _ourDialogSignature;

        /// <summary>Same 1..60 clamp Watch applies to closesAfterSeconds, so the
        /// window is held for exactly as long as watch{} says it was.</summary>
        private static int ClampSeconds(int seconds)
        {
            return seconds < 1 ? 1 : (seconds > 60 ? 60 : seconds);
        }

        /// <summary>Add the two trade-only keys to a watch block, so every
        /// accept reply carries them and a caller never has to test for
        /// presence.</summary>
        private static Dictionary<string, object> WithDialog(
            Dictionary<string, object> block, bool shown, int seconds)
        {
            if (block == null)
                block = new Dictionary<string, object>();
            block["dialogShown"] = shown;
            block["secondsShown"] = seconds;
            return block;
        }

        private sealed class DialogPass
        {
            internal bool Shown;
            internal string SkipReason;
            /// <summary>Set when the deal did not survive the swap. The call
            /// refuses and executes nothing.</summary>
            internal string Mismatch;
            internal string Expected;
            internal string Actual;
        }

        /// <summary>
        /// A compact, comparable description of what is staged and what it costs.
        /// Two of these being equal is the check that the deal on screen is the
        /// deal that was previewed.
        /// </summary>
        private static string StageSignature()
        {
            var parts = new List<string>();
            foreach (var row in StagedLines())
            {
                var d = row as Dictionary<string, object>;
                if (d == null)
                    continue;
                object defName, count;
                d.TryGetValue("defName", out defName);
                d.TryGetValue("count", out count);
                parts.Add((defName ?? "?") + "=" + (count ?? "?"));
            }
            parts.Sort(StringComparer.Ordinal);

            var net = "?";
            var balance = Balance();
            if (balance != null)
            {
                object n;
                if (balance.TryGetValue("netSilverToColony", out n) && n != null)
                    net = n.ToString();
            }
            return string.Join(";", parts.ToArray()) + "|net=" + net;
        }

        /// <summary>
        /// Main thread. Open the real trade window over the CURRENT deal. See the
        /// block comment above for why this swaps the deal object rather than
        /// re-applying counts.
        /// </summary>
        private static DialogPass ShowTradeDialog(bool wantWatch)
        {
            _ourTradeDialog = null;
            _ourDialogSignature = null;
            if (!wantWatch)
                return new DialogPass { SkipReason = "watch:false" };

            string why;
            if (!RequireSession(out why))
                return new DialogPass { SkipReason = "refused" };
            if (OpenTradeDialog() != null)
                return new DialogPass { SkipReason = "a trade dialog is already open" };

            var stack = Find.WindowStack;
            if (stack == null)
                return new DialogPass { SkipReason = "there is no window stack" };

            var oldDeal = TradeSession.deal;
            var trader = TradeSession.trader;
            var negotiator = TradeSession.playerNegotiator;
            var giftMode = TradeSession.giftMode;
            if (oldDeal == null || trader == null || negotiator == null)
                return new DialogPass { SkipReason = "the session has no deal, trader or negotiator to show" };

            // TradeSession.SetupWith opens with a Log.Warning when the trader is
            // unwilling, which can pop the dev log window on stream. `open`
            // already guards this; the constructor is a second call to it.
            if (!SafeBool(() => trader.CanTradeNow))
                return new DialogPass { SkipReason = "the trader cannot trade now" };

            var before = StageSignature();

            Window dialog;
            try
            {
                // Constructing it REPLACES TradeSession.deal. That is expected.
                dialog = new Dialog_Trade(negotiator, trader, giftMode);
            }
            catch (Exception e)
            {
                return new DialogPass { SkipReason = "Dialog_Trade could not be constructed: " + e.Message };
            }

            // Put the original deal back before anything reads it. PostOpen ->
            // CacheTradeables runs inside Add() below and caches whatever
            // TradeSession.deal is AT THAT MOMENT, so the order here is the
            // whole trick.
            TradeSession.deal = oldDeal;
            TradeSession.trader = trader;
            TradeSession.playerNegotiator = negotiator;
            TradeSession.giftMode = giftMode;

            var after = StageSignature();
            if (!string.Equals(before, after, StringComparison.Ordinal)
                || !ReferenceEquals(TradeSession.deal, oldDeal))
            {
                return new DialogPass
                {
                    Mismatch = "The staged deal did not survive opening the trade window, so nothing was executed.",
                    Expected = before,
                    Actual = after
                };
            }

            try
            {
                stack.Add(dialog);
            }
            catch (Exception e)
            {
                return new DialogPass { SkipReason = "the trade window could not be added to the stack: " + e.Message };
            }

            // Adding it ran PostOpen -> CacheTradeables. Check the deal one more
            // time: if caching it disturbed anything, refuse before the hold
            // rather than after it.
            var cached = StageSignature();
            if (!string.Equals(before, cached, StringComparison.Ordinal))
            {
                try { dialog.Close(false); } catch { }
                return new DialogPass
                {
                    Mismatch = "The staged deal changed when the trade window cached its rows, so nothing was executed.",
                    Expected = before,
                    Actual = cached
                };
            }

            _ourTradeDialog = dialog;
            _ourDialogSignature = before;
            ScrollToFirstStagedRow(dialog);
            return new DialogPass { Shown = true };
        }

        /// <summary>
        /// Scroll the window so a staged row is actually in frame. Everything
        /// needed is private, so this is reflection and entirely best-effort: a
        /// failure leaves the window at the top, which still shows the deal.
        /// `cachedTradeables` is the drawn order; `FillMainRect` lays rows out at
        /// y = 6 + i*30 (RowInterval 30).
        /// </summary>
        private static void ScrollToFirstStagedRow(Window dialog)
        {
            try
            {
                var type = typeof(Dialog_Trade);
                var rowsField = BridgeCommon.PrivateInstanceField(type, "cachedTradeables");
                var scrollField = BridgeCommon.PrivateInstanceField(type, "scrollPosition");
                if (rowsField == null || scrollField == null)
                    return;

                var rows = rowsField.GetValue(dialog) as List<Tradeable>;
                if (rows == null)
                    return;

                var index = -1;
                for (var i = 0; i < rows.Count; i++)
                {
                    if (rows[i] != null && SafeInt(() => rows[i].CountToTransfer) != 0)
                    {
                        index = i;
                        break;
                    }
                }
                // Nothing staged, or the first staged row is already on screen.
                if (index <= 6)
                    return;

                var y = 6f + (index * 30f) - 150f;
                if (y < 0f)
                    y = 0f;
                scrollField.SetValue(dialog, new UnityEngine.Vector2(0f, y));
            }
            catch
            {
                // Decorative. A window that did not scroll is still a window.
            }
        }

        /// <summary>
        /// Last resort, off the main thread, on every exit path. Does nothing at
        /// all in the normal case, because hop 3 has already closed the window
        /// and nulled the field.
        /// </summary>
        private static void EmergencyCloseDialog(IRimBridgeContext ctx)
        {
            try
            {
                if (_ourTradeDialog == null || ctx == null || ctx.MainThread == null)
                {
                    // No way back to the main thread; better a null field than a
                    // stale one pointing at a window nothing will ever close.
                    _ourTradeDialog = null;
                    _ourDialogSignature = null;
                    return;
                }
                var pending = ctx.MainThread.InvokeAsync(
                    () => CloseOurTradeDialog(), CancellationToken.None);
                if (pending != null)
                    pending.ContinueWith(t => { var ignored = t.Exception; }, TaskScheduler.Default);
            }
            catch
            {
                // The game may be shutting down. Nothing useful is left to do.
            }
        }

        /// <summary>Main thread. Close the window this tool opened, if it is
        /// still the one on screen. Never closes a window somebody else put
        /// up.</summary>
        private static bool CloseOurTradeDialog()
        {
            var dialog = _ourTradeDialog;
            _ourTradeDialog = null;
            _ourDialogSignature = null;
            if (dialog == null)
                return false;
            try
            {
                // Close(bool) is Dialog_Trade's own override and pops no
                // confirmation -- the class overrides neither PreClose nor
                // OnCancelKeyPressed, and its only Dialog_MessageBoxes are the
                // impaired-negotiator notice and the trader-short-funds prompt.
                // It also leaves TradeSession alone, which is why the deal is
                // still executable after this returns.
                dialog.Close(false);
            }
            catch { /* fall through to the check and the forced removal */ }

            // CONFIRM it is gone. A Dialog_Trade left up sets forcePause and
            // absorbInputAroundWindow, so "probably closed" is not good enough:
            // it would pause the colony and eat every click, with no X and no
            // close button drawn.
            try
            {
                var stack = Find.WindowStack;
                if (stack == null || stack.Windows == null)
                    return true;
                if (!stack.Windows.Any(w => ReferenceEquals(w, dialog)))
                    return true;
                return stack.TryRemove(dialog, false);
            }
            catch { return false; }
        }

        // ------------------------------------------------------------------
        // Everything below runs on RimWorld's main thread, inside the one hop.
        // ------------------------------------------------------------------

        private sealed class Args
        {
            public string Action;
            public string TraderId;
            public string Negotiator;
            public bool GiftMode;
            public string Item;
            public int Count;
            public string Lines;
            public bool Relative;
            public bool AllowPawns;
            public string Match;
            public bool OnlyChanged;
            public int MaxRows;
            public bool IncludeUntradeable;
            public bool RequireAdjacent;
            public bool AllowEmpty;
            public bool ReceiveQuest;
        }

        private static object Dispatch(Args a)
        {
            try
            {
                switch ((a.Action ?? string.Empty).ToLowerInvariant())
                {
                    case "":
                    case "list_traders":
                    case "list":
                        return ListTraders();
                    case "open":
                        return Open(a);
                    case "sheet":
                        return Sheet(a);
                    case "set":
                        return Set(a);
                    case "preview":
                        return Preview();
                    case "accept":
                        return Accept(a);
                    case "cancel":
                        return Cancel(a);
                    case "close_dialog":
                        return CloseDialog(a);
                    case "status":
                        return Status();
                    default:
                        return Failure(
                            "Unknown action '" + a.Action + "'. Valid: list_traders, open, sheet, set, preview, accept, cancel, close_dialog, status.",
                            "bad_action", a.Action);
                }
            }
            catch (Exception e)
            {
                return Failure(e.GetType().Name + ": " + e.Message, "exception", a.Action);
            }
        }

        // ---------------------------------------------------------------- list

        private static object ListTraders()
        {
            Map map;
            string mapError;
            if (!TryGetMap(out map, out mapError))
                return Failure(mapError, "no_map", "list_traders");

            var candidates = NegotiatorCandidates(map);
            var best = candidates.Count > 0 ? candidates[0] : null;

            var traders = new List<object>();

            // Caravan traders standing on the map.
            foreach (var pawn in SafeSpawnedPawns(map))
            {
                if (pawn == null)
                    continue;
                Pawn_TraderTracker tracker;
                try { tracker = pawn.trader; }
                catch { tracker = null; }
                if (tracker == null || tracker.traderKind == null)
                    continue;
                traders.Add(DescribeMapTrader(pawn, tracker, best));
            }

            // Orbital ships. Listed whether or not a comms console exists,
            // because "no ship" and "no console" are different answers and a
            // caller must never have to guess which one it got.
            var ships = new List<object>();
            try
            {
                var manager = map.passingShipManager;
                var list = manager != null ? manager.passingShips : null;
                if (list != null)
                {
                    foreach (var ship in list)
                    {
                        var trade = ship as TradeShip;
                        if (trade == null)
                            continue;
                        ships.Add(DescribeShip(trade, best));
                    }
                }
            }
            catch (Exception e)
            {
                ships.Add(new Dictionary<string, object> { { "error", "passingShipManager unreadable: " + e.Message } });
            }

            int consoles, usableConsoles;
            CountCommsConsoles(map, out consoles, out usableConsoles);

            var payload = Ok("list_traders");
            payload["mapTraderCount"] = traders.Count;
            payload["orbitalTraderCount"] = ships.Count;
            payload["traders"] = traders;
            payload["orbitalTraders"] = ships;
            payload["commsConsoles"] = consoles;
            payload["usableCommsConsoles"] = usableConsoles;
            payload["orbitalTradeBeaconsPowered"] = PoweredBeaconCount(map);
            payload["negotiator"] = best != null ? DescribeNegotiator(best) : null;
            payload["negotiatorCandidates"] = candidates.Select(DescribeNegotiator).ToList();
            payload["silverOnMapTotal"] = SilverOnMap(map);
            payload["silverNote"] =
                "silverOnMapTotal is EVERY silver stack on the map, traders' and pack animals' included — the number "
                + "inv.py gets wrong. Each trader's 'colonySilverForThisTrade' is what the deal itself would count.";
            return payload;
        }

        private static Dictionary<string, object> DescribeMapTrader(Pawn pawn, Pawn_TraderTracker tracker, Pawn negotiator)
        {
            var row = new Dictionary<string, object>();
            row["id"] = SafeId(pawn);
            row["kind"] = "caravan";
            row["name"] = SafeTraderName(pawn);
            row["pawnName"] = SafeName(pawn);
            row["faction"] = SafeFactionName(pawn.Faction);
            row["traderKind"] = tracker.traderKind != null ? tracker.traderKind.defName : null;
            row["traderKindLabel"] = tracker.traderKind != null ? tracker.traderKind.label : null;
            row["traderCategory"] = tracker.traderKind != null ? tracker.traderKind.category : null;
            row["position"] = Pos(pawn.Position);
            row["canTradeNow"] = SafeCanTradeNow(pawn);
            row["downed"] = SafeBool(() => pawn.Downed);
            row["dead"] = SafeBool(() => pawn.Dead);
            row["hasQuest"] = SafeBool(() => pawn.mindState != null && pawn.mindState.hasQuest);
            row["goodsStacks"] = SafeGoodsCount(pawn);

            if (negotiator != null)
            {
                var d = Chebyshev(negotiator.Position, pawn.Position);
                row["negotiatorDistance"] = d;
                row["negotiatorAdjacent"] = d <= 1;
                row["colonySilverForThisTrade"] = ColonySilverForTrade(pawn, negotiator);
            }
            else
            {
                row["negotiatorDistance"] = null;
                row["negotiatorAdjacent"] = null;
                row["colonySilverForThisTrade"] = null;
            }
            return row;
        }

        private static Dictionary<string, object> DescribeShip(TradeShip ship, Pawn negotiator)
        {
            var row = new Dictionary<string, object>();
            row["id"] = SafeString(ship.GetUniqueLoadID);
            row["kind"] = "orbital";
            row["name"] = SafeString(() => ship.TraderName);
            row["fullTitle"] = SafeString(() => ship.FullTitle);
            row["faction"] = SafeFactionName(SafeFaction(ship));
            row["traderKind"] = ship.def != null ? ship.def.defName : null;
            row["traderKindLabel"] = ship.def != null ? ship.def.label : null;
            row["traderCategory"] = ship.def != null ? ship.def.category : null;
            row["canTradeNow"] = SafeBool(() => ship.CanTradeNow);
            row["departed"] = SafeBool(() => ship.Departed);
            row["ticksUntilDeparture"] = SafeInt(() => ship.ticksUntilDeparture);
            row["traderSilver"] = SafeInt(() => ship.Silver);
            row["colonySilverForThisTrade"] = negotiator != null ? ColonySilverForTrade(ship, negotiator) : (int?)null;
            row["note"] = "An orbital deal can only see colony goods on cells covered by a POWERED orbital trade beacon.";
            return row;
        }

        // ---------------------------------------------------------------- open

        private static object Open(Args a)
        {
            Map map;
            string mapError;
            if (!TryGetMap(out map, out mapError))
                return Failure(mapError, "no_map", "open");

            var dialog = OpenTradeDialog();
            if (dialog != null)
                return Failure(
                    "A Dialog_Trade window is already open on screen. Close it first with action:'close_dialog' — "
                    + "mutating the session underneath the dialog would leave it drawing a stale cached row list.",
                    "dialog_open", "open");

            if (TradeSession.Active)
                return Failure(
                    "A TradeSession is already open with '" + SafeString(() => TradeSession.trader.TraderName)
                    + "'. Finish it with action:'accept' or drop it with action:'cancel' before opening another.",
                    "session_open", "open");

            if (string.IsNullOrEmpty(a.TraderId))
                return Failure("open needs a traderId (from action:'list_traders'), or a unique substring of the trader's name.", "no_trader_id", "open");

            ITrader trader;
            Pawn traderPawn;
            string traderError;
            if (!ResolveTrader(map, a.TraderId, out trader, out traderPawn, out traderError))
                return Failure(traderError, "trader_not_found", "open");

            if (!SafeBool(() => trader.CanTradeNow))
            {
                // Calling SetupWith anyway would fire Log.Warning, which can pop
                // the log window open on a streaming screen.
                var f = Failure(
                    "Trader '" + SafeString(() => trader.TraderName) + "' reports CanTradeNow:false, so the session was not opened.",
                    "cannot_trade_now", "open");
                f["hint"] = traderPawn != null
                    ? "A map trader is not tradeable while downed, in a mental state, asleep, hostile, or out of stock."
                    : "An orbital ship is not tradeable once it has departed.";
                return f;
            }

            Pawn chosen;
            string negotiatorError;
            if (!ResolveNegotiator(map, a.Negotiator, out chosen, out negotiatorError))
                return Failure(negotiatorError, "no_negotiator", "open");

            int? distance = null;
            if (traderPawn != null)
            {
                distance = Chebyshev(chosen.Position, traderPawn.Position);
                if (a.RequireAdjacent && distance.Value > 1)
                {
                    var f = Failure(
                        "requireAdjacent is set and " + SafeName(chosen) + " is " + distance.Value
                        + " cells from " + SafeTraderName(traderPawn) + ".",
                        "not_adjacent", "open");
                    f["negotiator"] = SafeName(chosen);
                    f["negotiatorId"] = SafeId(chosen);
                    f["negotiatorPosition"] = Pos(chosen.Position);
                    f["traderPosition"] = Pos(traderPawn.Position);
                    f["distance"] = distance.Value;
                    f["hint"] = "Walk there first, then re-open. The GAME does not require this; requireAdjacent does.";
                    return f;
                }
            }

            try
            {
                TradeSession.SetupWith(trader, chosen, a.GiftMode);
            }
            catch (Exception e)
            {
                return Failure("TradeSession.SetupWith threw: " + e.GetType().Name + ": " + e.Message, "setup_failed", "open");
            }

            if (!TradeSession.Active || TradeSession.deal == null)
                return Failure("TradeSession.SetupWith returned but the session is not active.", "setup_failed", "open");

            _sessionTraderId = traderPawn != null ? SafeId(traderPawn) : SafeString(() => ((TradeShip)trader).GetUniqueLoadID());
            _sessionTraderName = SafeString(() => trader.TraderName);
            _sessionNegotiatorName = SafeName(chosen);
            _sessionOpenedTick = CurrentTick();
            _sessionOpenedByUs = true;

            var payload = (Dictionary<string, object>)Sheet(a);
            payload["action"] = "open";
            payload["negotiatorDistance"] = distance;
            payload["negotiatorAdjacent"] = distance.HasValue ? (bool?)(distance.Value <= 1) : null;
            payload["adjacencyNote"] =
                "Verified in Assembly-CSharp 1.6.9676.17735: nothing on the trade path checks distance. Adjacency in "
                + "vanilla comes from the right-click JobDefOf.TradeWithPawn walk, not from the trade rules.";
            payload["cannotSellReasons"] = SafeCannotSellReasons();
            return payload;
        }

        // --------------------------------------------------------------- sheet

        private static object Sheet(Args a)
        {
            string why;
            if (!RequireSession(out why))
                return Failure(why, "no_session", "sheet");

            var deal = TradeSession.deal;
            var all = deal.AllTradeables;

            var rows = new List<object>();
            var omittedByFilter = 0;
            var omittedByCap = 0;
            var omittedUntradeable = 0;
            var needle = string.IsNullOrEmpty(a.Match) ? null : a.Match.Trim();

            for (var i = 0; i < all.Count; i++)
            {
                var t = all[i];
                if (t == null)
                    continue;

                // A row the trader refuses is not a row: `set` rejects it and
                // the vanilla dialog never draws it. Printed with a staged
                // marker it read as "staged" until the sell bounced. Counted,
                // never silently dropped, and row indices stay ABSOLUTE so an
                // index from an includeUntradeable:true sheet still addresses
                // the same row on a default one.
                if (!a.IncludeUntradeable && !SafeBool(() => t.TraderWillTrade))
                {
                    omittedUntradeable++;
                    continue;
                }

                if (a.OnlyChanged && SafeInt(() => t.CountToTransfer) == 0)
                {
                    omittedByFilter++;
                    continue;
                }
                if (needle != null && !Matches(t, needle))
                {
                    omittedByFilter++;
                    continue;
                }
                if (rows.Count >= a.MaxRows)
                {
                    omittedByCap++;
                    continue;
                }
                rows.Add(DescribeTradeable(t, i));
            }

            var payload = Ok("sheet");
            AddSessionBlock(payload);
            payload["rowCount"] = all.Count;
            payload["rowsReturned"] = rows.Count;
            // A filter that hides things says what it hid. An empty list must
            // never be readable as "the trader has nothing".
            payload["omittedByFilter"] = omittedByFilter;
            payload["omittedByRowCap"] = omittedByCap;
            payload["omittedUntradeable"] = omittedUntradeable;
            payload["filters"] = new Dictionary<string, object>
            {
                { "match", a.Match },
                { "onlyChanged", a.OnlyChanged },
                { "maxRows", a.MaxRows },
                { "includeUntradeable", a.IncludeUntradeable }
            };
            payload["rows"] = rows;
            payload["balance"] = Balance();
            payload["signConvention"] =
                "count > 0 = the colony BUYS that many; count < 0 = the colony SELLS that many. Legal range per row is "
                + "[-colonyCount, +traderCount].";
            return payload;
        }

        private static Dictionary<string, object> DescribeTradeable(Tradeable t, int index)
        {
            var row = new Dictionary<string, object>();
            row["index"] = index;
            row["label"] = SafeString(() => t.Label);
            var def = SafeDef(t);
            row["defName"] = def != null ? def.defName : null;
            row["stuff"] = SafeString(() => t.StuffDef != null ? t.StuffDef.defName : null);
            row["category"] = def != null && def.FirstThingCategory != null ? def.FirstThingCategory.label : null;
            row["colonyCount"] = SafeInt(() => t.CountHeldBy(Transactor.Colony));
            row["traderCount"] = SafeInt(() => t.CountHeldBy(Transactor.Trader));
            // GetPriceFor memoises the price factors inside the Tradeable the
            // first time it is asked. That is the only write a read of this
            // sheet can cause, it is the session's own scratch state, and the
            // vanilla dialog does the same thing every frame it draws.
            row["buyPrice"] = Round2(SafeFloat(() => t.GetPriceFor(TradeAction.PlayerBuys)));
            row["sellPrice"] = Round2(SafeFloat(() => t.GetPriceFor(TradeAction.PlayerSells)));
            row["buyPriceType"] = SafeString(() => t.PriceTypeFor(TradeAction.PlayerBuys).ToString());
            row["sellPriceType"] = SafeString(() => t.PriceTypeFor(TradeAction.PlayerSells).ToString());
            row["baseMarketValue"] = Round2(SafeFloat(() => t.BaseMarketValue));
            row["traderWillTrade"] = SafeBool(() => t.TraderWillTrade);
            row["isCurrency"] = SafeBool(() => t.IsCurrency);
            // Never omitted. A caller that cannot tell a parka row from a
            // colonist row by reading the sheet is one typo from selling a
            // person.
            row["isPawn"] = IsPawnRow(t);
            row["pawnDescription"] = PawnRowDescription(t);
            row["countToTransfer"] = SafeInt(() => t.CountToTransfer);
            row["actionToDo"] = SafeString(() => t.ActionToDo.ToString());
            row["minCount"] = SafeInt(() => t.GetMinimumToTransfer());
            row["maxCount"] = SafeInt(() => t.GetMaximumToTransfer());
            return row;
        }

        // ----------------------------------------------------------------- set

        private static object Set(Args a)
        {
            string why;
            if (!RequireSession(out why))
                return Failure(why, "no_session", "set");
            if (OpenTradeDialog() != null)
                return Failure(
                    "A Dialog_Trade window is open on screen; its cached row list would go stale. Close it with action:'close_dialog' first.",
                    "dialog_open", "set");

            List<Req> reqs;
            string parseError;
            if (!ParseRequests(a, out reqs, out parseError))
                return Failure(parseError, "bad_lines", "set");
            if (reqs.Count == 0)
                return Failure("set needs either item+count, or lines. Nothing was parsed.", "bad_lines", "set");

            var deal = TradeSession.deal;
            var all = deal.AllTradeables;
            var results = new List<object>();
            var applied = 0;

            foreach (var req in reqs)
            {
                var line = new Dictionary<string, object>();
                line["request"] = req.Name;
                line["requestedCount"] = req.Count;

                List<int> hits;
                var resolveError = ResolveRow(all, req.Name, out hits);
                if (resolveError != null)
                {
                    line["ok"] = false;
                    line["error"] = resolveError;
                    if (hits != null && hits.Count > 1)
                        line["candidates"] = hits.Take(8).Select(i => (object)(new Dictionary<string, object>
                        {
                            { "index", i },
                            { "label", SafeString(() => all[i].Label) },
                            { "defName", SafeDef(all[i]) != null ? SafeDef(all[i]).defName : null }
                        })).ToList();
                    results.Add(line);
                    continue;
                }

                var idx = hits[0];
                var t = all[idx];
                line["index"] = idx;
                line["label"] = SafeString(() => t.Label);
                line["defName"] = SafeDef(t) != null ? SafeDef(t).defName : null;

                var current = SafeInt(() => t.CountToTransfer);
                var target = a.Relative ? current + req.Count : req.Count;
                line["previousCount"] = current;
                line["targetCount"] = target;

                if (!SafeBool(() => t.TraderWillTrade) && target != 0)
                {
                    line["ok"] = false;
                    line["traderWillTrade"] = false;
                    // Name the row, not just the rule. The default sheet omits
                    // these rows entirely (includeUntradeable), so a caller that
                    // reached one addressed it from somewhere else and needs to
                    // be told which row bounced and why it was not on the sheet.
                    line["error"] =
                        "'" + SafeString(() => t.Label) + "' (row #" + idx + ", "
                        + (SafeDef(t) != null ? SafeDef(t).defName : "unknown defName")
                        + "): this trader will not trade that thing (Tradeable.TraderWillTrade is false, from "
                        + "TraderKindDef.WillTrade). Nothing was staged for it. It is omitted from the sheet unless "
                        + "includeUntradeable:true.";
                    line["countToTransfer"] = current;
                    results.Add(line);
                    continue;
                }

                // The silver row is not a row you set. In trade mode
                // `Tradeable.Interactive` is false for currency, and
                // `TradeDeal.UpdateCurrencyCount` recomputes it from every
                // other line the moment anything changes — so a "successful"
                // set here would be silently reverted two lines later, which
                // is exactly the kind of confident lie this stack keeps
                // getting caught by.
                if (SafeBool(() => t.IsCurrency) && !TradeSession.giftMode)
                {
                    line["ok"] = false;
                    line["error"] =
                        "The silver row is computed from the other lines, not set. Change what you are buying or "
                        + "selling and the silver follows.";
                    line["countToTransfer"] = current;
                    results.Add(line);
                    continue;
                }

                // A pawn row, and this line would hand the pawn over. See the
                // class remarks: the sheet carries colonists, prisoners and
                // colony animals as ordinary rows, and this tool addresses rows
                // by fuzzy name. Refuse by default, name the row, and say the
                // exact flag that lifts it. Buying is untouched.
                if (IsPawnRow(t) && !a.AllowPawns && WouldGiveAway(t, target))
                {
                    line["ok"] = false;
                    line["errorKind"] = "pawn_sale_refused";
                    line["isPawn"] = true;
                    line["pawnDescription"] = PawnRowDescription(t);
                    line["error"] =
                        "Refusing to SELL " + Math.Abs(target) + " x " + SafeString(() => t.Label)
                        + " — that row is a pawn (" + (PawnRowDescription(t) ?? "unknown kind")
                        + "). Pass allowPawns:true to do it deliberately. Buying a pawn from the trader needs no flag.";
                    line["countToTransfer"] = current;
                    results.Add(line);
                    continue;
                }

                // CanAdjustTo FIRST, always. AdjustTo logs Log.Error on a
                // rejected value and Log.Error pauses the game.
                AcceptanceReport report;
                try { report = t.CanAdjustTo(target); }
                catch (Exception e)
                {
                    line["ok"] = false;
                    line["error"] = "CanAdjustTo threw: " + e.Message;
                    results.Add(line);
                    continue;
                }

                if (!report.Accepted)
                {
                    line["ok"] = false;
                    line["error"] = string.IsNullOrEmpty(report.Reason)
                        ? "The game rejected that count."
                        : report.Reason;
                    line["minCount"] = SafeInt(() => t.GetMinimumToTransfer());
                    line["maxCount"] = SafeInt(() => t.GetMaximumToTransfer());
                    line["countToTransfer"] = current;
                    results.Add(line);
                    continue;
                }

                try
                {
                    t.AdjustTo(target);
                    applied++;
                    line["ok"] = true;
                    line["countToTransfer"] = SafeInt(() => t.CountToTransfer);
                    line["actionToDo"] = SafeString(() => t.ActionToDo.ToString());
                    line["lineValue"] = Round2(SafeFloat(() => t.CurTotalCurrencyCostForSource));
                }
                catch (Exception e)
                {
                    line["ok"] = false;
                    line["error"] = "AdjustTo threw: " + e.Message;
                }
                results.Add(line);
            }

            // The dialog calls this on every count change; the silver row is
            // meaningless without it.
            SafeUpdateCurrency(deal);

            var payload = Ok("set");
            AddSessionBlock(payload);
            payload["linesRequested"] = reqs.Count;
            payload["linesApplied"] = applied;
            payload["linesRejected"] = reqs.Count - applied;
            // Present whether or not it was used: a caller must be able to tell
            // "no pawn line was attempted" from "the guard was off".
            payload["allowPawns"] = a.AllowPawns;
            payload["lines"] = results;
            payload["balance"] = Balance();
            payload["staged"] = StagedLines();
            return payload;
        }

        // ------------------------------------------------------------- preview

        private static object Preview()
        {
            string why;
            if (!RequireSession(out why))
                return Failure(why, "no_session", "preview");

            SafeUpdateCurrency(TradeSession.deal);

            var payload = Ok("preview");
            AddSessionBlock(payload);
            payload["balance"] = Balance();
            payload["staged"] = StagedLines();
            var b = Balance();
            payload["wouldSucceed"] = TradeSession.giftMode || Convert.ToBoolean(b["colonyCanAfford"], CultureInfo.InvariantCulture);
            return payload;
        }

        // -------------------------------------------------------------- accept

        private static object Accept(Args a)
        {
            string why;
            if (!RequireSession(out why))
                return Failure(why, "no_session", "accept");
            // A window this tool opened is expected and is executed over; the
            // deal underneath it is the one that was staged. A window a PERSON
            // opened is still refused, because it runs its own Accept over its
            // own deal.
            var onScreen = OpenTradeDialog();
            if (onScreen != null && !ReferenceEquals(onScreen, _ourTradeDialog))
                return Failure(
                    "A Dialog_Trade window is open on screen; it runs its own Accept. Close it with action:'close_dialog' first.",
                    "dialog_open", "accept");

            var deal = TradeSession.deal;
            SafeUpdateCurrency(deal);

            var staged = StagedLines();
            if (staged.Count == 0 && !a.AllowEmpty)
                return Failure(
                    "Nothing is staged, so this trade would move nothing. Set some counts first, or pass allowEmpty:true.",
                    "nothing_staged", "accept");

            // The exact predicate TradeDeal.TryExecute tests before it reaches
            // for the dialog it assumes is there. Evaluating it here is what
            // keeps a headless cannot-afford from becoming a
            // NullReferenceException on Dialog_Trade.FlashSilver().
            var balanceBefore = Balance();
            if (!TradeSession.giftMode)
            {
                var currency = SafeCurrencyTradeable(deal);
                var affordable = currency != null && SafeInt(() => currency.CountPostDealFor(Transactor.Colony)) >= 0;
                if (!affordable)
                {
                    var f = Failure(
                        currency == null
                            ? "This deal has no silver row, so TradeDeal.TryExecute would treat it as unaffordable."
                            : "The colony cannot afford this deal.",
                        "cannot_afford", "accept");
                    f["balance"] = balanceBefore;
                    f["staged"] = staged;
                    f["note"] =
                        "TryExecute was NOT called. Its cannot-afford branch dereferences Dialog_Trade without a null "
                        + "check and would have thrown headless.";
                    AddSessionBlock(f);
                    return f;
                }
            }

            // Last gate before anything moves. The window was on screen for
            // several seconds and a person could have clicked in it, so the deal
            // is compared against what was previewed one final time. A change
            // refuses; it never trades the new thing.
            if (_ourDialogSignature != null)
            {
                var now = StageSignature();
                if (!string.Equals(_ourDialogSignature, now, StringComparison.Ordinal))
                {
                    var drift = Failure(
                        "The staged deal changed while the trade window was on screen, so nothing was executed.",
                        "restage_mismatch", "accept");
                    drift["stagedExpected"] = _ourDialogSignature;
                    drift["stagedActual"] = now;
                    drift["staged"] = staged;
                    drift["balance"] = balanceBefore;
                    drift["note"] =
                        "The session is untouched and still open: nothing was traded that was not previewed. "
                        + "Re-read the sheet and accept again.";
                    AddSessionBlock(drift);
                    return drift;
                }
            }

            var traderPawn = TradeSession.trader as Pawn;
            var faction = SafeFaction(TradeSession.trader);
            int? goodwillBefore = faction != null ? SafeInt(() => faction.PlayerGoodwill) : (int?)null;
            var messagesBefore = LiveMessageKeys();

            bool actuallyTraded;
            bool executed;
            string executeError = null;
            try
            {
                executed = deal.TryExecute(out actuallyTraded);
            }
            catch (Exception e)
            {
                executed = false;
                actuallyTraded = false;
                executeError = e.GetType().Name + ": " + e.Message;
            }

            var newMessages = LiveMessagesSince(messagesBefore);

            var payload = executeError == null ? Ok("accept") : Failure("TradeDeal.TryExecute threw: " + executeError, "execute_threw", "accept");
            payload["executed"] = executed;
            payload["actuallyTraded"] = actuallyTraded;
            payload["moved"] = staged;
            payload["balanceBefore"] = balanceBefore;
            payload["traderResponse"] = newMessages;
            payload["goodwillBefore"] = goodwillBefore;
            payload["goodwillAfter"] = faction != null ? SafeInt(() => faction.PlayerGoodwill) : (int?)null;
            payload["traderName"] = _sessionTraderName;
            payload["negotiator"] = _sessionNegotiatorName;

            // Whether it traded or not, the session is finished here: TryExecute
            // has already called TradeDeal.Reset(), so every row index the
            // caller was holding is now stale.
            var quest = CloseSession(traderPawn, a.ReceiveQuest);
            payload["questReceived"] = quest;
            payload["sessionActive"] = TradeSession.Active;
            payload["indicesInvalidated"] = true;
            return payload;
        }

        // -------------------------------------------------------------- cancel

        private static object Cancel(Args a)
        {
            var wasActive = TradeSession.Active;
            var trader = wasActive ? _sessionTraderName : null;
            var staged = wasActive ? StagedLines() : new List<object>();
            var traderPawn = wasActive ? TradeSession.trader as Pawn : null;

            var quest = CloseSession(traderPawn, a.ReceiveQuest && wasActive);

            var payload = Ok("cancel");
            payload["wasActive"] = wasActive;
            payload["traderName"] = trader;
            payload["discarded"] = staged;
            payload["questReceived"] = quest;
            payload["sessionActive"] = TradeSession.Active;
            return payload;
        }

        // -------------------------------------------------------- close_dialog

        private static object CloseDialog(Args a)
        {
            var stack = Find.WindowStack;
            if (stack == null)
                return Failure("Find.WindowStack is not available.", "no_window_stack", "close_dialog");

            List<Window> targets;
            try
            {
                targets = stack.Windows == null
                    ? new List<Window>()
                    : stack.Windows.OfType<Window>().Where(IsTradeDialog).ToList();
            }
            catch (Exception e)
            {
                return Failure("Reading the window stack failed: " + e.Message, "no_window_stack", "close_dialog");
            }

            var closed = new List<object>();
            foreach (var w in targets)
            {
                var entry = new Dictionary<string, object>
                {
                    { "type", w.GetType().FullName ?? w.GetType().Name },
                    { "id", SafeInt(() => w.ID) }
                };
                try
                {
                    // Window.Close is virtual and Dialog_Trade overrides it —
                    // going through Close rather than TryRemove is what keeps a
                    // trader's quest handoff working exactly as it does when a
                    // person clicks the X.
                    w.Close(false);
                    entry["closed"] = true;
                }
                catch (Exception e)
                {
                    entry["closed"] = false;
                    entry["error"] = e.Message;
                    try { entry["closed"] = stack.TryRemove(w, false); }
                    catch (Exception e2) { entry["fallbackError"] = e2.Message; }
                }
                closed.Add(entry);
            }

            var wasActive = TradeSession.Active;
            var traderPawn = wasActive ? TradeSession.trader as Pawn : null;
            // A dialog left open by a crashed session leaves TradeSession.trader
            // set behind it; Dialog_Trade.Close does NOT clear it (verified).
            var quest = CloseSession(traderPawn, a.ReceiveQuest && wasActive);

            var payload = Ok("close_dialog");
            payload["dialogsFound"] = targets.Count;
            payload["dialogs"] = closed;
            payload["sessionWasActive"] = wasActive;
            payload["questReceived"] = quest;
            payload["sessionActive"] = TradeSession.Active;
            payload["openWindows"] = OpenWindowSummary(stack);
            return payload;
        }

        // -------------------------------------------------------------- status

        private static object Status()
        {
            var payload = Ok("status");
            AddSessionBlock(payload);
            var dialog = OpenTradeDialog();
            payload["tradeDialogOpen"] = dialog != null;
            payload["tradeDialogType"] = dialog != null ? (dialog.GetType().FullName ?? dialog.GetType().Name) : null;
            if (TradeSession.Active && TradeSession.deal != null)
            {
                payload["rowCount"] = SafeInt(() => TradeSession.deal.TradeableCount);
                payload["balance"] = Balance();
                payload["staged"] = StagedLines();
            }
            return payload;
        }

        // ------------------------------------------------------------- helpers

        private struct Req
        {
            public string Name;
            public int Count;
        }

        private static bool ParseRequests(Args a, out List<Req> reqs, out string error)
        {
            reqs = new List<Req>();
            error = null;

            if (!string.IsNullOrEmpty(a.Item))
                reqs.Add(new Req { Name = a.Item.Trim(), Count = a.Count });

            if (!string.IsNullOrEmpty(a.Lines))
            {
                var entries = a.Lines.Split(new[] { ';', '\n', '\r' }, StringSplitOptions.RemoveEmptyEntries);
                foreach (var raw in entries)
                {
                    var entry = raw.Trim();
                    if (entry.Length == 0)
                        continue;
                    var cut = Math.Max(entry.LastIndexOf(':'), entry.LastIndexOf('='));
                    if (cut <= 0 || cut == entry.Length - 1)
                    {
                        error = "Could not parse line '" + entry + "'. Expected 'name:count' or 'name=count'.";
                        return false;
                    }
                    var name = entry.Substring(0, cut).Trim();
                    var num = entry.Substring(cut + 1).Trim();
                    if (num.StartsWith("+", StringComparison.Ordinal))
                        num = num.Substring(1);
                    int n;
                    if (!int.TryParse(num, NumberStyles.Integer, CultureInfo.InvariantCulture, out n))
                    {
                        error = "Could not parse the count in '" + entry + "'. Expected a signed integer.";
                        return false;
                    }
                    if (name.Length == 0)
                    {
                        error = "Line '" + entry + "' has no name.";
                        return false;
                    }
                    reqs.Add(new Req { Name = name, Count = n });
                }
            }
            return true;
        }

        /// <summary>Returns null on success, with `hits[0]` the row. Otherwise an error string.</summary>
        private static string ResolveRow(List<Tradeable> all, string name, out List<int> hits)
        {
            hits = new List<int>();
            if (string.IsNullOrEmpty(name))
                return "Empty row name.";

            var trimmed = name.Trim();
            if (trimmed.StartsWith("#", StringComparison.Ordinal))
            {
                int idx;
                if (!int.TryParse(trimmed.Substring(1), NumberStyles.Integer, CultureInfo.InvariantCulture, out idx))
                    return "'" + trimmed + "' is not a row index.";
                if (idx < 0 || idx >= all.Count)
                    return "Row index " + idx + " is out of range (0.." + (all.Count - 1) + ").";
                hits.Add(idx);
                return null;
            }

            // Tier 1: exact defName. Tier 2: exact label. Tier 3: substring of
            // either. Quality and stuff variants share a defName, so tier 1 can
            // legitimately be ambiguous — say so instead of guessing.
            for (var i = 0; i < all.Count; i++)
            {
                var def = SafeDef(all[i]);
                if (def != null && string.Equals(def.defName, trimmed, StringComparison.OrdinalIgnoreCase))
                    hits.Add(i);
            }
            if (hits.Count == 0)
            {
                for (var i = 0; i < all.Count; i++)
                {
                    var label = SafeString(() => all[i].Label);
                    if (label != null && string.Equals(label, trimmed, StringComparison.OrdinalIgnoreCase))
                        hits.Add(i);
                }
            }
            if (hits.Count == 0)
            {
                for (var i = 0; i < all.Count; i++)
                {
                    if (Matches(all[i], trimmed))
                        hits.Add(i);
                }
            }

            if (hits.Count == 0)
                return "No row matches '" + trimmed + "'.";
            if (hits.Count > 1)
                return "'" + trimmed + "' matches " + hits.Count + " rows. Address one by '#index' from the sheet.";
            return null;
        }

        private static bool Matches(Tradeable t, string needle)
        {
            var label = SafeString(() => t.Label);
            if (label != null && label.IndexOf(needle, StringComparison.OrdinalIgnoreCase) >= 0)
                return true;
            var def = SafeDef(t);
            return def != null && def.defName != null
                && def.defName.IndexOf(needle, StringComparison.OrdinalIgnoreCase) >= 0;
        }

        private static Dictionary<string, object> Balance()
        {
            var b = new Dictionary<string, object>();
            var deal = TradeSession.deal;
            if (deal == null)
                return b;

            var currency = SafeCurrencyTradeable(deal);
            b["giftMode"] = TradeSession.giftMode;
            b["currencyRowPresent"] = currency != null;
            if (currency != null)
            {
                var held = SafeInt(() => currency.CountHeldBy(Transactor.Colony));
                var toTransfer = SafeInt(() => currency.CountToTransfer);
                var after = SafeInt(() => currency.CountPostDealFor(Transactor.Colony));
                b["colonySilverNow"] = held;
                b["silverCountToTransfer"] = toTransfer;
                b["colonySilverAfter"] = after;
                b["netSilverToColony"] = toTransfer;   // negative = we pay
                b["traderSilverNow"] = SafeInt(() => currency.CountHeldBy(Transactor.Trader));
                b["traderSilverAfter"] = SafeInt(() => currency.CountPostDealFor(Transactor.Trader));
                b["colonyCanAfford"] = after >= 0;
            }
            else
            {
                b["colonySilverNow"] = null;
                b["silverCountToTransfer"] = null;
                b["colonySilverAfter"] = null;
                b["netSilverToColony"] = null;
                b["traderSilverNow"] = null;
                b["traderSilverAfter"] = null;
                b["colonyCanAfford"] = false;
            }
            b["traderHasEnoughSilver"] = SafeBool(() => deal.DoesTraderHaveEnoughSilver());

            float buyValue = 0f, sellValue = 0f;
            var buyLines = 0;
            var sellLines = 0;
            try
            {
                foreach (var t in deal.AllTradeables)
                {
                    if (t == null || SafeBool(() => t.IsCurrency))
                        continue;
                    var act = SafeString(() => t.ActionToDo.ToString());
                    if (act == "PlayerBuys")
                    {
                        buyLines++;
                        buyValue += SafeFloat(() => t.CurTotalCurrencyCostForSource);
                    }
                    else if (act == "PlayerSells")
                    {
                        sellLines++;
                        sellValue += -SafeFloat(() => t.CurTotalCurrencyCostForSource);
                    }
                }
            }
            catch
            {
                // leave the totals at whatever was summed
            }
            b["buyLines"] = buyLines;
            b["sellLines"] = sellLines;
            b["buyValue"] = Round2(buyValue);
            b["sellValue"] = Round2(sellValue);
            return b;
        }

        private static List<object> StagedLines()
        {
            var outp = new List<object>();
            var deal = TradeSession.deal;
            if (deal == null)
                return outp;
            List<Tradeable> all;
            try { all = deal.AllTradeables; }
            catch { return outp; }
            for (var i = 0; i < all.Count; i++)
            {
                var t = all[i];
                if (t == null)
                    continue;
                var n = SafeInt(() => t.CountToTransfer);
                if (n == 0)
                    continue;
                var def = SafeDef(t);
                outp.Add(new Dictionary<string, object>
                {
                    { "index", i },
                    { "label", SafeString(() => t.Label) },
                    { "defName", def != null ? def.defName : null },
                    { "count", n },
                    { "action", SafeString(() => t.ActionToDo.ToString()) },
                    { "unitPrice", Round2(SafeFloat(() => t.GetPriceFor(t.ActionToDo))) },
                    { "lineValue", Round2(SafeFloat(() => t.CurTotalCurrencyCostForSource)) },
                    { "isCurrency", SafeBool(() => t.IsCurrency) }
                });
            }
            return outp;
        }

        private static bool RequireSession(out string error)
        {
            error = null;
            if (!TradeSession.Active)
            {
                error = "No TradeSession is open. Run action:'open' with a traderId from action:'list_traders' first.";
                return false;
            }
            if (TradeSession.deal == null)
            {
                error = "TradeSession is active but its deal is null; the session is unusable. Run action:'cancel'.";
                return false;
            }
            return true;
        }

        private static void AddSessionBlock(Dictionary<string, object> payload)
        {
            payload["sessionActive"] = TradeSession.Active;
            payload["traderId"] = _sessionTraderId;
            payload["traderName"] = TradeSession.Active ? SafeString(() => TradeSession.trader.TraderName) : null;
            payload["traderKind"] = TradeSession.Active
                ? SafeString(() => TradeSession.trader.TraderKind != null ? TradeSession.trader.TraderKind.defName : null)
                : null;
            payload["negotiator"] = TradeSession.Active ? SafeName(TradeSession.playerNegotiator) : null;
            payload["giftMode"] = TradeSession.giftMode;
            payload["openedByThisTool"] = _sessionOpenedByUs && TradeSession.Active;
            payload["openedAtTick"] = _sessionOpenedByUs && TradeSession.Active ? (int?)_sessionOpenedTick : null;
        }

        private static bool CloseSession(Pawn traderPawn, bool receiveQuest)
        {
            var quest = false;
            if (receiveQuest && traderPawn != null)
            {
                try
                {
                    if (traderPawn.mindState != null && traderPawn.mindState.hasQuest && TradeSession.playerNegotiator != null)
                    {
                        TradeUtility.ReceiveQuestFromTrader(traderPawn, TradeSession.playerNegotiator);
                        quest = true;
                    }
                }
                catch
                {
                    quest = false;
                }
            }
            try { TradeSession.Close(); }
            catch { /* Close only nulls the static trader; nothing here can fail usefully */ }
            _sessionTraderId = null;
            _sessionTraderName = null;
            _sessionNegotiatorName = null;
            _sessionOpenedTick = 0;
            _sessionOpenedByUs = false;
            return quest;
        }

        private static void SafeUpdateCurrency(TradeDeal deal)
        {
            try { deal.UpdateCurrencyCount(); }
            catch { /* a deal with no currency row returns early anyway */ }
        }

        private static List<object> SafeCannotSellReasons()
        {
            try
            {
                var reasons = TradeSession.deal.cannotSellReasons;
                return reasons == null ? new List<object>() : reasons.Cast<object>().ToList();
            }
            catch { return new List<object>(); }
        }

        private static Tradeable SafeCurrencyTradeable(TradeDeal deal)
        {
            try { return deal.CurrencyTradeable; }
            catch { return null; }
        }

        // --------------------------------------------------- trader resolution

        private static bool ResolveTrader(Map map, string key, out ITrader trader, out Pawn traderPawn, out string error)
        {
            trader = null;
            traderPawn = null;
            error = null;
            var needle = key.Trim();

            var byId = new List<KeyValuePair<ITrader, Pawn>>();
            var byName = new List<KeyValuePair<ITrader, Pawn>>();

            foreach (var pawn in SafeSpawnedPawns(map))
            {
                if (pawn == null)
                    continue;
                Pawn_TraderTracker tracker;
                try { tracker = pawn.trader; }
                catch { tracker = null; }
                if (tracker == null || tracker.traderKind == null)
                    continue;

                if (string.Equals(SafeId(pawn), needle, StringComparison.OrdinalIgnoreCase))
                    byId.Add(new KeyValuePair<ITrader, Pawn>(pawn, pawn));
                else if (NameContains(SafeTraderName(pawn), needle) || NameContains(SafeName(pawn), needle))
                    byName.Add(new KeyValuePair<ITrader, Pawn>(pawn, pawn));
            }

            try
            {
                var manager = map.passingShipManager;
                var ships = manager != null ? manager.passingShips : null;
                if (ships != null)
                {
                    foreach (var ship in ships)
                    {
                        var ts = ship as TradeShip;
                        if (ts == null)
                            continue;
                        if (string.Equals(SafeString(ts.GetUniqueLoadID), needle, StringComparison.OrdinalIgnoreCase))
                            byId.Add(new KeyValuePair<ITrader, Pawn>(ts, null));
                        else if (NameContains(SafeString(() => ts.TraderName), needle) || NameContains(SafeString(() => ts.FullTitle), needle))
                            byName.Add(new KeyValuePair<ITrader, Pawn>(ts, null));
                    }
                }
            }
            catch
            {
                // an unreadable ship list is not a reason to fail an id match
            }

            var pick = byId.Count > 0 ? byId : byName;
            if (pick.Count == 0)
            {
                error = "No trader matches '" + needle + "'. Run action:'list_traders' for the current ids.";
                return false;
            }
            if (pick.Count > 1)
            {
                error = "'" + needle + "' matches " + pick.Count + " traders. Use the exact id from action:'list_traders'.";
                return false;
            }
            trader = pick[0].Key;
            traderPawn = pick[0].Value;
            return true;
        }

        private static bool NameContains(string haystack, string needle)
        {
            return haystack != null && haystack.IndexOf(needle, StringComparison.OrdinalIgnoreCase) >= 0;
        }

        // ----------------------------------------------- negotiator resolution

        private static bool ResolveNegotiator(Map map, string key, out Pawn chosen, out string error)
        {
            chosen = null;
            error = null;
            var candidates = NegotiatorCandidates(map);

            if (string.IsNullOrEmpty(key))
            {
                if (candidates.Count == 0)
                {
                    error = "No colonist on this map can negotiate: every one is dead, downed, in a mental state, or has "
                            + "the Social work tag disabled.";
                    return false;
                }
                chosen = candidates[0];
                return true;
            }

            var needle = key.Trim();
            var hits = candidates
                .Where(p => string.Equals(SafeId(p), needle, StringComparison.OrdinalIgnoreCase))
                .ToList();
            if (hits.Count == 0)
                hits = candidates.Where(p => NameContains(SafeName(p), needle)).ToList();
            if (hits.Count == 0)
            {
                // Say whether the pawn exists but was filtered out — "not found"
                // and "found but cannot negotiate" are different answers.
                var anywhere = SafeSpawnedPawns(map)
                    .Where(p => p != null && (NameContains(SafeName(p), needle) || string.Equals(SafeId(p), needle, StringComparison.OrdinalIgnoreCase)))
                    .ToList();
                error = anywhere.Count > 0
                    ? "'" + needle + "' is on the map but cannot negotiate (dead, downed, in a mental state, not a free colonist, or Social disabled)."
                    : "No colonist matches '" + needle + "'.";
                return false;
            }
            if (hits.Count > 1)
            {
                error = "'" + needle + "' matches " + hits.Count + " colonists. Use a stable pawn id.";
                return false;
            }
            chosen = hits[0];
            return true;
        }

        private static List<Pawn> NegotiatorCandidates(Map map)
        {
            var outp = new List<Pawn>();
            try
            {
                foreach (var p in map.mapPawns.FreeColonistsSpawned)
                {
                    if (p == null)
                        continue;
                    if (SafeBool(() => p.Dead) || SafeBool(() => p.Downed) || SafeBool(() => p.InMentalState))
                        continue;
                    if (SafeBool(() => p.WorkTagIsDisabled(WorkTags.Social)))
                        continue;
                    outp.Add(p);
                }
            }
            catch
            {
                return outp;
            }

            outp.Sort((x, y) =>
            {
                var c = TradeStat(y).CompareTo(TradeStat(x));
                if (c != 0)
                    return c;
                c = SocialLevel(y).CompareTo(SocialLevel(x));
                if (c != 0)
                    return c;
                return string.Compare(SafeName(x) ?? string.Empty, SafeName(y) ?? string.Empty, StringComparison.Ordinal);
            });
            return outp;
        }

        private static Dictionary<string, object> DescribeNegotiator(Pawn p)
        {
            return new Dictionary<string, object>
            {
                { "name", SafeName(p) },
                { "id", SafeId(p) },
                { "socialSkill", SocialLevel(p) },
                { "tradePriceImprovement", Round2(TradeStat(p)) },
                { "position", Pos(p.Position) },
                { "drafted", SafeBool(() => p.Drafted) },
                // Dialog_Trade.PostOpen warns when either of these is below
                // 0.95; the deal still works, the prices are just worse.
                { "talkingCapacity", Round2(Capacity(p, PawnCapacityDefOf.Talking)) },
                { "hearingCapacity", Round2(Capacity(p, PawnCapacityDefOf.Hearing)) }
            };
        }

        private static float TradeStat(Pawn p)
        {
            try { return p.GetStatValue(StatDefOf.TradePriceImprovement); }
            catch { return 0f; }
        }

        private static int SocialLevel(Pawn p)
        {
            try
            {
                if (p.skills == null)
                    return 0;
                var rec = p.skills.GetSkill(SkillDefOf.Social);
                return rec == null || rec.TotallyDisabled ? 0 : rec.Level;
            }
            catch { return 0; }
        }

        private static float Capacity(Pawn p, PawnCapacityDef def)
        {
            try { return p.health.capacities.GetLevel(def); }
            catch { return 0f; }
        }

        // ------------------------------------------------------- map utilities

        private static int? ColonySilverForTrade(ITrader trader, Pawn negotiator)
        {
            // The only honest answer to "how much silver does this deal see?"
            // is to ask the trader the same question the TradeDeal constructor
            // asks. For a caravan that filters by reachability from the
            // TRADER's cell; for an orbital ship it filters to powered trade
            // beacon cells, which is why an orbital deal can read zero in a
            // colony that is visibly rich.
            try
            {
                var silver = ThingDefOf.Silver;
                var total = 0;
                var seen = 0;
                foreach (var thing in trader.ColonyThingsWillingToBuy(negotiator))
                {
                    if (++seen > 20000)
                        break;
                    if (thing != null && thing.def == silver)
                        total += thing.stackCount;
                }
                return total;
            }
            catch
            {
                return null;
            }
        }

        private static int SilverOnMap(Map map)
        {
            try
            {
                var total = 0;
                foreach (var t in map.listerThings.ThingsOfDef(ThingDefOf.Silver))
                    total += t != null ? t.stackCount : 0;
                return total;
            }
            catch { return 0; }
        }

        private static void CountCommsConsoles(Map map, out int total, out int usable)
        {
            total = 0;
            usable = 0;
            try
            {
                foreach (var b in map.listerBuildings.AllBuildingsColonistOfClass<Building_CommsConsole>())
                {
                    total++;
                    if (SafeBool(() => b.CanUseCommsNow))
                        usable++;
                }
            }
            catch
            {
                // leave the counts where they got to
            }
        }

        private static int PoweredBeaconCount(Map map)
        {
            try { return Building_OrbitalTradeBeacon.AllPowered(map).Count(); }
            catch { return 0; }
        }

        private static int SafeGoodsCount(Pawn pawn)
        {
            try
            {
                var n = 0;
                foreach (var t in pawn.trader.Goods)
                {
                    if (t != null)
                        n++;
                    if (n > 5000)
                        break;
                }
                return n;
            }
            catch { return -1; }
        }

        private static IEnumerable<Pawn> SafeSpawnedPawns(Map map)
        {
            try
            {
                var list = map.mapPawns != null ? map.mapPawns.AllPawnsSpawned : null;
                return list == null ? new List<Pawn>() : list.ToList();
            }
            catch { return new List<Pawn>(); }
        }

        // ------------------------------------------------------ window helpers

        private static bool IsTradeDialog(Window w)
        {
            if (w == null)
                return false;
            var t = w.GetType();
            while (t != null)
            {
                if (t == typeof(Dialog_Trade))
                    return true;
                t = t.BaseType;
            }
            return false;
        }

        private static Window OpenTradeDialog()
        {
            try
            {
                var stack = Find.WindowStack;
                if (stack == null || stack.Windows == null)
                    return null;
                return stack.Windows.OfType<Window>().FirstOrDefault(IsTradeDialog);
            }
            catch { return null; }
        }

        private static List<object> OpenWindowSummary(WindowStack stack)
        {
            var outp = new List<object>();
            try
            {
                if (stack.Windows == null)
                    return outp;
                foreach (var w in stack.Windows.OfType<Window>())
                {
                    outp.Add(new Dictionary<string, object>
                    {
                        { "type", w.GetType().FullName ?? w.GetType().Name },
                        { "id", SafeInt(() => w.ID) },
                        { "layer", SafeString(() => w.layer.ToString()) }
                    });
                }
            }
            catch
            {
                // partial list beats no list
            }
            return outp;
        }

        // ---------------------------------------------------- message capture

        private static HashSet<string> LiveMessageKeys()
        {
            var keys = new HashSet<string>(StringComparer.Ordinal);
            foreach (var m in LiveMessages())
                keys.Add(MessageKey(m));
            return keys;
        }

        private static List<object> LiveMessagesSince(HashSet<string> before)
        {
            var outp = new List<object>();
            foreach (var m in LiveMessages())
            {
                var key = MessageKey(m);
                if (before.Contains(key))
                    continue;
                outp.Add(new Dictionary<string, object>
                {
                    { "text", SafeString(() => m.text) },
                    { "type", SafeString(() => m.def != null ? m.def.defName : null) },
                    { "tick", SafeInt(() => m.startingTick) }
                });
            }
            return outp;
        }

        private static List<Message> LiveMessages()
        {
            try
            {
                if (LiveMessagesField == null)
                    return new List<Message>();
                var raw = LiveMessagesField.GetValue(null) as List<Message>;
                return raw == null ? new List<Message>() : raw.ToList();
            }
            catch { return new List<Message>(); }
        }

        private static string MessageKey(Message m)
        {
            if (m == null)
                return "null";
            return SafeInt(() => m.startingTick) + "|" + (SafeString(() => m.text) ?? string.Empty);
        }

        // ----------------------------------------------------- generic safety

        /// <summary>The shared map gate; see BridgeCommon.TryGetMap. The error
        /// text names this tool.</summary>
        private static bool TryGetMap(out Map map, out string error)
        {
            return BridgeCommon.TryGetMap(ToolName, out map, out error);
        }

        private static int CurrentTick()
        {
            try { return Find.TickManager != null ? Find.TickManager.TicksGame : 0; }
            catch { return 0; }
        }

        private static Dictionary<string, object> Pos(IntVec3 c)
        {
            return BridgeCommon.Pos(c);
        }

        private static int Chebyshev(IntVec3 a, IntVec3 b)
        {
            return Math.Max(Math.Abs(a.x - b.x), Math.Abs(a.z - b.z));
        }

        /// <summary>
        /// Is this sheet row a PAWN rather than an item? Two independent tests,
        /// because either alone has a hole: the `ThingDef.category` flag is what
        /// RimWorld itself sorts by, and the concrete `AnyThing is Pawn` test
        /// catches a row whose def is missing or unreadable.
        /// </summary>
        private static bool IsPawnRow(Tradeable t)
        {
            try
            {
                var def = SafeDef(t);
                if (def != null && def.category == ThingCategory.Pawn)
                    return true;
            }
            catch { }
            try { return t.AnyThing is Pawn; }
            catch { return false; }
        }

        /// <summary>
        /// "colonist Lucas", "prisoner Aardvark", "colony animal (Warg)", or null
        /// when the row is not a pawn. This is the string the refusal quotes back,
        /// so it has to name the individual, not the def.
        /// </summary>
        private static string PawnRowDescription(Tradeable t)
        {
            try
            {
                if (!IsPawnRow(t))
                    return null;
                var pawn = t.AnyThing as Pawn;
                if (pawn == null)
                    return "pawn";

                string kind;
                if (SafeBool(() => pawn.IsColonist)) kind = "colonist";
                else if (SafeBool(() => pawn.IsPrisonerOfColony)) kind = "prisoner of the colony";
                else if (SafeBool(() => pawn.IsPrisoner)) kind = "prisoner";
                else if (SafeBool(() => pawn.RaceProps != null && pawn.RaceProps.Animal)) kind = "animal";
                else kind = "pawn";

                var name = SafeString(() => pawn.LabelShortCap.ToString());
                var def = SafeString(() => pawn.def != null ? pawn.def.defName : null);
                return kind + " " + (name ?? "?") + (def != null ? " (" + def + ")" : string.Empty);
            }
            catch { return "pawn"; }
        }

        /// <summary>
        /// Would this target count make the colony PART with the thing?
        ///
        /// In a trade session positive means the colony gains, so giving away is
        /// a negative count. Gift mode flips `PositiveCountDirection` to
        /// `Destination`, so there positive is the giving direction. Reading
        /// `TradeSession.giftMode` rather than hardcoding the sign is what keeps
        /// the guard correct in the one mode nobody has tested.
        /// </summary>
        private static bool WouldGiveAway(Tradeable t, int target)
        {
            bool gift;
            try { gift = TradeSession.giftMode; }
            catch { gift = false; }
            return gift ? target > 0 : target < 0;
        }

        private static ThingDef SafeDef(Tradeable t)
        {
            try { return t.ThingDef; }
            catch { return null; }
        }

        private static Faction SafeFaction(ITrader trader)
        {
            try { return trader != null ? trader.Faction : null; }
            catch { return null; }
        }

        private static string SafeFactionName(Faction f)
        {
            try { return f != null ? f.Name : null; }
            catch { return null; }
        }

        private static string SafeName(Pawn p)
        {
            if (p == null)
                return null;
            try { return p.LabelShortCap; }
            catch
            {
                try { return p.LabelCap; }
                catch { return null; }
            }
        }

        private static string SafeId(Thing t)
        {
            try { return t != null ? t.GetUniqueLoadID() : null; }
            catch { return null; }
        }

        private static string SafeTraderName(Pawn p)
        {
            try { return p.TraderName; }
            catch { return SafeName(p); }
        }

        private static bool SafeCanTradeNow(Pawn p)
        {
            try { return p.CanTradeNow; }
            catch { return false; }
        }

        // These fall back to a VALUE, not to null, which is this tool's own
        // convention and differs from home/place_building's identically named
        // helpers. The fallback stays here so it stays visible.
        private static bool SafeBool(Func<bool> f)
        {
            return BridgeCommon.Try(f, false);
        }

        private static int SafeInt(Func<int> f)
        {
            return BridgeCommon.Try(f, 0);
        }

        private static float SafeFloat(Func<float> f)
        {
            return BridgeCommon.Try(f, 0f);
        }

        private static string SafeString(Func<string> f)
        {
            return BridgeCommon.SafeString(f);
        }

        private static double Round2(float v)
        {
            if (float.IsNaN(v) || float.IsInfinity(v))
                return 0d;
            return Math.Round((double)v, 2);
        }

        private static Dictionary<string, object> Ok(string action)
        {
            return new Dictionary<string, object>
            {
                { "success", true },
                { "tool", ToolName },
                { "action", action },
                { "sessionActive", TradeSession.Active }
            };
        }

        private static Dictionary<string, object> Failure(string error, string kind, string action)
        {
            var d = new Dictionary<string, object>
            {
                { "success", false },
                { "tool", ToolName },
                { "action", action },
                { "error", error },
                { "errorKind", kind }
            };
            try { d["sessionActive"] = TradeSession.Active; }
            catch { d["sessionActive"] = false; }
            return d;
        }
    }
}
