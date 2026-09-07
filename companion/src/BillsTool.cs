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
    /// home/bills — the Bills tab without the Bills tab: read every bench's
    /// queue with the ingredient verdict attached, list what could be added, and
    /// add / configure / delete / reorder a bill.
    ///
    /// The read half answers the question no tool in this stack could answer: a
    /// queued bill that cannot run for want of steel, or over a larder that is
    /// all insect meat while the bill's own filter excludes it, looks exactly
    /// like a live one everywhere else. `canRunNow` and `blockedBy[]` are
    /// computed per bill from the map; the arithmetic and the hazards it dodges
    /// are in <see cref="BillCommon"/>.
    ///
    /// The write half is the Bills tab's own three statements and nothing else:
    /// `recipe.MakeNewBill()`, `billStack.AddBill(bill)`, and the field writes the
    /// config dialog performs. `BillStack.MaxCount` is 15 and is enforced. The
    /// sound, the no-skilled-pawn dialog and the mechanitor dialog are UI and are
    /// skipped; what they would have said is in the reply instead.
    ///
    /// Verified against the installed Assembly-CSharp.dll (RimWorld 1.6.9676.17735):
    ///
    ///   RimWorld.Bill              .recipe .suspended .ingredientFilter
    ///                              .ingredientSearchRadius .allowedSkillRange
    ///                              .PawnRestriction .SlavesOnly .MechsOnly
    ///                              .NonMechsOnly .SetPawnRestriction(Pawn)
    ///                              .SetAnyPawnRestriction() .GetStoreMode()
    ///                              .GetSlotGroup() .SetStoreMode(mode, group)
    ///                              .LabelCap .CompletableEver
    ///                              MaxIngredientSearchRadius == 999
    ///   RimWorld.Bill_Production   .repeatMode .repeatCount .targetCount .paused
    ///                              .pauseWhenSatisfied .unpauseWhenYouHave
    ///                              .hpRange .qualityRange .limitToAllowedStuff
    ///                              .includeEquipped .includeTainted
    ///                              .RepeatInfoText .Clone()
    ///   RimWorld.BillStack         .Bills .Count .AddBill .Delete .Reorder
    ///                              .IndexOf  MaxCount == 15
    ///   RimWorld.BillUtility       .MakeNewBill(this RecipeDef, Precept_ThingStyle)
    ///   RimWorld.IBillGiver        .BillStack .CurrentlyUsableForBills()
    ///   Verse.ThingDef.AllRecipes, Verse.RecipeDef.AvailableNow / .AvailableOnNow
    ///   Verse.ThingFilter          .SetAllow(ThingDef, bool) .Allows .AllowedDefCount
    ///                              .CopyAllowancesFrom
    ///   Verse.ThingCategoryDef.DescendantThingDefs
    ///   RimWorld.BillRepeatModeDefOf, RimWorld.BillStoreModeDefOf
    ///   Verse.ThingRequestGroup.PotentialBillGiver
    ///
    /// Three write hazards, all pre-checked rather than caught:
    ///
    ///   * **`Bill.SetStoreMode` on a non-production bill is a `Log.ErrorOnce`**
    ///     ("Tried to set store mode of a non-production bill"), and `Log.Error`
    ///     pauses the colony. The store mode is only ever written on a
    ///     `Bill_Production`. On one, `SetStoreMode` itself logs when
    ///     `mode == SpecificStockpile` disagrees with `group != null`, so the
    ///     group is always passed with SpecificStockpile and never with the other
    ///     two.
    ///   * **`ThingFilter.SetAllow(ThingCategoryDef, ...)` `Log.Error`s** when
    ///     `ThingCategoryNodeDatabase.initialized` is false. A category name is
    ///     expanded to its `DescendantThingDefs` here and applied one def at a
    ///     time, so that overload is never called.
    ///   * **`Bill.ValidateSettings()` calls `Faction.OfPlayer`.** It is never
    ///     called; a worker restriction is checked against
    ///     `map.mapPawns.FreeColonists` before it is written.
    ///
    /// Watchability: `add`/`set`/`delete`/`move` select the bench and open
    /// `ITab_Bills` before the write and close it after, exactly where a player
    /// would have been looking. `list` and `recipes` never watch.
    /// </summary>
    public sealed class HomeBillsTools
    {
        private const string ToolName = "home/bills";

        /// <summary>Candidates listed when a name is ambiguous.</summary>
        private const int MaxCandidates = 20;

        /// <summary>BillStack.MaxCount in 1.6. The Add button greys out here.</summary>
        private const int MaxBillsPerBench = 15;

        [Tool(
            ToolName,
            Title = "Read, add, configure, delete and reorder worktable bills",
            Description =
                "The Bills tab in one call, with the thing the tab does not show: whether each bill can actually run right now. "
                + "action:'list' (default) walks every bill giver on the map -- or just `bench` -- and gives each bill canRunNow, "
                + "blockedBy[] and a per-ingredient needed/available/shortfall computed from the map the way "
                + "WorkGiver_DoBill.TryFindBestBillIngredients computes it, so 'a stove bill over a larder of insect meat the "
                + "filter excludes' reads as a shortfall instead of a live queue. action:'recipes' lists what can be added to a "
                + "bench and whether its ingredients are on hand. action:'add' / 'set' / 'delete' / 'move' are the writes and are a "
                + "DRY RUN until dryRun:false; add and set report the resulting bill's own verdict, so 'added a bill that cannot "
                + "run' is said at the moment of adding. `bench` takes a ThingID, a defName@x,z, or a unique label substring; an "
                + "ambiguous one is refused with the candidates.",
            ResultDescription =
                "success, tool, action, benches[] (each with bills[] carrying canRunNow / blockedBy[] / ingredients[] / filter{} / "
                + "config{}), attention{}, recipes[] on action:'recipes', write{} with before/after/changed on a write, dryRun, "
                + "applied, watch{}, notes{}.")]
        [ToolResponse("action", "string", "Which action ran: list, recipes, add, set, delete or move. Echoed on every reply including refusals.", Always = true)]
        [ToolResponse("benches", "array", "One row per bill giver in scope: thingId, defName, label, position, usableForBills with the reason when it is not, billCount, activeBillCount, billsCanRunNow, maxBills, and bills[]. Present on every action; on a write it is the single bench that was written.", Always = true)]
        [ToolResponse("attention", "object", "Counts worth acting on across the benches in scope: benchesWithNoBills, benchesWithNoActiveBill, benchesUnusable, billsShortOfIngredients, finishedBills, suspendedBills, pausedBills. Always present, zeros included.", Always = true)]
        [ToolResponse("recipes", "array", "Only on action:'recipes'. Every recipe this bench's def offers that passes AvailableNow AND AvailableOnNow(bench), each with ingredients[] under the recipe's DEFAULT filter, ingredientsOnHand, blockedBy[] and workAmount. NULL on every other action.", Nullable = true)]
        [ToolResponse("write", "object", "NULL on a read. On add/set/delete/move: requested, resolved, refused, reason, before{billCount,bills[],bill}, after{...} (READ BACK from the stack on a real run), changed[], and for add/set the resulting bill's canRunNow and blockedBy[].", Always = true, Nullable = true)]
        [ToolResponse("dryRun", "boolean", "True = nothing was written. TRUE by default; a caller must pass dryRun:false deliberately. Reported on reads too, so it is never mistaken for absent.", Always = true)]
        [ToolResponse("applied", "boolean", "True only when the stack was actually changed. False on every dry run, every refusal and every read.", Always = true)]
        [ToolResponse("watch", "object", "What was shown on screen while the write landed: shown, and either the tab/selection/camera detail or the reason nothing was shown ('dry run', 'refused', 'watch:false'). Always present, including on reads.", Always = true)]
        [ToolResponse("candidates", "array", "Present only when a name was ambiguous or unresolved: what it could have meant. A refusal that lists nothing is a refusal nobody can act on.", Nullable = true)]
        [ToolResponse("ingredientsScanned", "number", "How many spawned haulable stacks the ingredient index looked at. 0 with a non-empty map means the scan failed, and notes.ingredientScan says so.", Always = true)]
        [ToolResponse("notes", "object", "What this payload's silences mean: what the ingredient scan does and does not model, and why ShouldDoNow is never called.", Always = true)]
        [ToolResponse("unknownArguments", "array", "Every argument key the caller sent that this tool does not declare, sorted, case-sensitively. Empty array = every key was recognised. On a write tool this matters twice over: a misspelled dryRun is the difference between a plan and a changed colony. The host's own _rimBridgeTimeoutMs is never listed.", Always = true)]
        [ToolResponse("unknownArgumentsWarning", "string", "Present only when unknownArguments is non-empty, or when the caller's raw keys could not be read at all - in which case the empty unknownArguments means 'not known', not 'nothing unknown'.", Nullable = true)]
        public async Task<object> Bills(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            [ToolParameter(Description = "list (default) | recipes | add | set | delete | move. Everything but list and recipes writes and is a dry run until dryRun:false.", DefaultValue = "list")] string action = "list",
            [ToolParameter(Description = "Which bill giver: an exact ThingID ('ElectricStove123'), 'defName@x,z' ('TableButcher@120,140'), or a unique substring of the label or defName. Ambiguous is REFUSED with the candidates listed. Omit with action:'list' for every bill giver on the map; required for every other action.")] string bench = null,
            [ToolParameter(Description = "action:'add' only. Which recipe: an exact defName, an exact label (case-insensitive), or a unique substring of either, resolved against this bench's own AllRecipes. Ambiguous is refused with the candidates; a recipe this bench does not offer is refused naming it.")] string recipe = null,
            [ToolParameter(Description = "0-based position in the bench's bill stack. Required by set, delete and move. Out of range is refused with the range.", DefaultValue = -1)] int index = -1,
            [ToolParameter(Description = "action:'move' only. The 0-based position to move the bill TO. Use this or `direction`, not both.", DefaultValue = -1)] int to = -1,
            [ToolParameter(Description = "action:'move' only. 'up' or 'down' -- one place, the way the tab's arrows do it. Use this or `to`, not both.")] string direction = null,
            [ToolParameter(Description = "Forever | RepeatCount | TargetCount. RepeatCount is 'Do X times' and pairs with repeatCount; TargetCount is 'Do until you have X' and pairs with targetCount.")] string repeatMode = null,
            [ToolParameter(Description = "How many times to do it. Sets repeatMode to RepeatCount when repeatMode was not given. -1 leaves it alone.", DefaultValue = -1)] int repeatCount = -1,
            [ToolParameter(Description = "How many of the product to keep in store. Sets repeatMode to TargetCount when repeatMode was not given. -1 leaves it alone.", DefaultValue = -1)] int targetCount = -1,
            [ToolParameter(Description = "TargetCount bills only: resume when the count falls to this. -1 leaves it alone.", DefaultValue = -1)] int unpauseWhenYouHave = -1,
            [ToolParameter(Description = "'on' or 'off'. TargetCount bills only: pause instead of repeating once the target is met. Omit to leave it alone.")] string pauseWhenSatisfied = null,
            [ToolParameter(Description = "'on' or 'off'. Suspend the bill without deleting it. Omit to leave it alone.")] string suspended = null,
            [ToolParameter(Description = "How far from the bench ingredients may be fetched, in cells. 999 is unlimited and is the default for a new bill. -1 leaves it alone.", DefaultValue = -1)] int ingredientSearchRadius = -1,
            [ToolParameter(Description = "Lowest skill level allowed to do this bill, 0-20. -1 leaves it alone.", DefaultValue = -1)] int skillMin = -1,
            [ToolParameter(Description = "Highest skill level allowed to do this bill, 0-20. -1 leaves it alone.", DefaultValue = -1)] int skillMax = -1,
            [ToolParameter(Description = "Restrict the bill to one colonist by name (a unique substring is enough), or 'anyone' to clear the restriction. Omit to leave it alone.")] string worker = null,
            [ToolParameter(Description = "Where the product goes: 'bestStockpile', 'dropOnFloor', or the exact label of a stockpile zone. Omit to leave it alone.")] string storeMode = null,
            [ToolParameter(Description = "Comma-separated ThingDef or ThingCategoryDef names to add to this bill's ingredient filter ('ChunkSlate' or 'StoneChunks'). Existing allowances remain enabled. A category is expanded to its descendant defs. A name outside the recipe's own fixedIngredientFilter REFUSES the whole call rather than being half applied.")] string allow = null,
            [ToolParameter(Description = "Comma-separated ThingDef or ThingCategoryDef names to make the bill's complete ingredient whitelist. Existing allowances are cleared first. Use this to narrow a bill to ChunkSlate; `allow` remains additive.")] string only = null,
            [ToolParameter(Description = "Comma-separated ThingDef or ThingCategoryDef names to DISALLOW in this bill's ingredient filter. Same resolution and the same refusal as `allow`.")] string disallow = null,
            [ToolParameter(Description = "action:'list' with no bench: include bill givers that are not ours. Off by default, so a ruin's worktable does not pad the reply.", DefaultValue = false)] bool allFactions = false,
            [ToolParameter(Description = "TRUE by default. Resolve everything, validate everything, report the before and the PREDICTED after, and touch nothing. Pass false to actually write.", DefaultValue = true)] bool dryRun = true,
            [ToolParameter(Description = "On a real write, select the bench and open its Bills tab before the change lands, then close it. Decorative only: the write is the same either way.", DefaultValue = true)] bool watch = true,
            [ToolParameter(Description = "How long the Bills tab stays open after the write, in seconds.", DefaultValue = Watch.DefaultSeconds)] int watchSeconds = Watch.DefaultSeconds)
        {
            return BridgeCommon.WithUnknownArguments(
                await BillsCore(ctx, cancellationToken, action, bench, recipe, index, to, direction,
                                repeatMode, repeatCount, targetCount, unpauseWhenYouHave,
                                pauseWhenSatisfied, suspended, ingredientSearchRadius,
                                skillMin, skillMax, worker, storeMode, allow, only, disallow,
                                allFactions, dryRun, watch, watchSeconds).ConfigureAwait(false),
                ctx, typeof(HomeBillsTools), ToolName);
        }

        // ============================================================== plumbing

        private async Task<object> BillsCore(
            IRimBridgeContext ctx, CancellationToken cancellationToken,
            string action, string bench, string recipe, int index, int to, string direction,
            string repeatMode, int repeatCount, int targetCount, int unpauseWhenYouHave,
            string pauseWhenSatisfied, string suspended, int ingredientSearchRadius,
            int skillMin, int skillMax, string worker, string storeMode,
            string allow, string only, string disallow, bool allFactions, bool dryRun,
            bool watch, int watchSeconds)
        {
            if (ctx?.MainThread == null)
                return Refuse("list", true, "No RimBridge main-thread dispatcher is available for this invocation.", null);

            // Everything parseable is parsed OUT here, before the hop.
            var act = Clean(action) ?? "list";
            act = act.ToLowerInvariant();
            if (act != "list" && act != "recipes" && act != "add" &&
                act != "set" && act != "delete" && act != "move")
            {
                return Refuse(act, dryRun,
                    "action must be one of: list, recipes, add, set, delete, move. Got: " + action, null);
            }

            var request = new Request
            {
                Action = act,
                BenchSpec = Clean(bench),
                RecipeSpec = Clean(recipe),
                Index = index,
                To = to,
                Direction = Clean(direction)?.ToLowerInvariant(),
                RepeatModeSpec = Clean(repeatMode),
                RepeatCount = repeatCount,
                TargetCount = targetCount,
                UnpauseWhenYouHave = unpauseWhenYouHave,
                PauseWhenSatisfiedSpec = Clean(pauseWhenSatisfied)?.ToLowerInvariant(),
                SuspendedSpec = Clean(suspended)?.ToLowerInvariant(),
                SearchRadius = ingredientSearchRadius,
                SkillMin = skillMin,
                SkillMax = skillMax,
                WorkerSpec = Clean(worker),
                StoreModeSpec = Clean(storeMode),
                AllowNames = SplitNames(allow),
                OnlyNames = SplitNames(only),
                DisallowNames = SplitNames(disallow),
                AllFactions = allFactions,
                DryRun = dryRun,
                Watch = watch,
                WatchSeconds = watchSeconds < 0 ? 0 : watchSeconds
            };

            if (act == "list" || act == "recipes")
            {
                return await ctx.MainThread
                    .InvokeAsync(() => RunRead(request), cancellationToken)
                    .ConfigureAwait(false);
            }

            // Hop 1: resolve, validate, and -- only for a real write -- open the
            // Bills tab so a viewer sees the change land inside it.
            var plan = await ctx.MainThread
                .InvokeAsync(() => PlanWrite(ctx, request), cancellationToken)
                .ConfigureAwait(false);

            if (plan.Payload != null)
                return plan.Payload;                    // refused, or a dry run

            await Watch.Lead(plan.Session, cancellationToken).ConfigureAwait(false);

            // Hop 2: apply, read back, close the tab.
            return await ctx.MainThread
                .InvokeAsync(() => Commit(request, plan), cancellationToken)
                .ConfigureAwait(false);
        }

        /// <summary>Arguments after parsing, carried across the hop.</summary>
        private sealed class Request
        {
            internal string Action;
            internal string BenchSpec;
            internal string RecipeSpec;
            internal int Index;
            internal int To;
            internal string Direction;
            internal string RepeatModeSpec;
            internal int RepeatCount;
            internal int TargetCount;
            internal int UnpauseWhenYouHave;
            internal string PauseWhenSatisfiedSpec;
            internal string SuspendedSpec;
            internal int SearchRadius;
            internal int SkillMin;
            internal int SkillMax;
            internal string WorkerSpec;
            internal string StoreModeSpec;
            internal List<string> AllowNames;
            internal List<string> OnlyNames;
            internal List<string> DisallowNames;
            internal bool AllFactions;
            internal bool DryRun;
            internal bool Watch;
            internal int WatchSeconds;
        }

        /// <summary>What hop 1 hands hop 2. A non-null Payload means hop 2 never
        /// runs: the call was refused, or it was a dry run and is already
        /// answered.</summary>
        private sealed class WritePlan
        {
            internal Dictionary<string, object> Payload;
            internal string BenchId;
            internal RecipeDef Recipe;
            internal Options Opt;
            internal Watch.Session Session;
            internal Dictionary<string, object> Before;
        }

        /// <summary>Validated field writes. Every member is null / -1 when the
        /// caller did not ask for it, and a null field is never written.</summary>
        private sealed class Options
        {
            internal BillRepeatModeDef RepeatMode;
            internal int? RepeatCount;
            internal int? TargetCount;
            internal int? UnpauseWhenYouHave;
            internal bool? PauseWhenSatisfied;
            internal bool? Suspended;
            internal float? SearchRadius;
            internal int? SkillMin;
            internal int? SkillMax;
            internal bool WorkerGiven;
            internal Pawn Worker;                 // null with WorkerGiven = "anyone"
            internal BillStoreModeDef StoreMode;
            internal Zone_Stockpile StoreZone;
            internal bool ReplaceAllowances;
            internal List<ThingDef> Allow = new List<ThingDef>();
            internal List<ThingDef> Disallow = new List<ThingDef>();

            internal bool Any
            {
                get
                {
                    return RepeatMode != null || RepeatCount != null || TargetCount != null
                           || UnpauseWhenYouHave != null || PauseWhenSatisfied != null
                           || Suspended != null || SearchRadius != null || SkillMin != null
                           || SkillMax != null || WorkerGiven || StoreMode != null
                           || Allow.Count > 0 || Disallow.Count > 0;
                }
            }
        }

        // ================================================================ reads

        private static object RunRead(Request request)
        {
            Map map;
            string mapError;
            if (!BridgeCommon.TryGetMap(ToolName, out map, out mapError))
                return Refuse(request.Action, request.DryRun, mapError, null);

            var items = BillCommon.MapItems.Build(map);

            List<Thing> givers;
            string giversError;
            if (!TryGetBillGivers(map, out givers, out giversError))
                return Refuse(request.Action, request.DryRun, giversError, null);

            List<Thing> scope;
            if (request.BenchSpec != null)
            {
                Thing resolved;
                List<object> candidates;
                string error;
                if (!ResolveBench(givers, request.BenchSpec, out resolved, out candidates, out error))
                    return Refuse(request.Action, request.DryRun, error, candidates);
                scope = new List<Thing> { resolved };
            }
            else if (request.Action == "recipes")
            {
                return Refuse(request.Action, request.DryRun,
                    "action:'recipes' needs a bench. Pass bench:'<ThingID, defName@x,z or a label substring>'; "
                    + "action:'list' with no bench names every bill giver on the map.", null);
            }
            else
            {
                scope = givers
                    .Where(t => request.AllFactions || IsPlayerFaction(t))
                    .OrderBy(t => BridgeCommon.SafeString(() => t.def.defName) ?? "")
                    .ToList();
            }
            var skippedByFaction = request.BenchSpec != null ? 0 : givers.Count - scope.Count;

            var attention = new Attention();
            var benchRows = scope.Select(t => BenchRow(t, items, attention, true)).ToList();

            var payload = Base(request, map);
            payload["benches"] = benchRows;
            payload["benchCount"] = benchRows.Count;
            payload["benchesOnMap"] = givers.Count;
            // A filter that removed everything must say so: an empty benches[]
            // on a colony with worktables is otherwise indistinguishable from a
            // colony with none.
            payload["benchesSkippedByFaction"] = skippedByFaction;
            payload["attention"] = attention.ToPayload();
            payload["ingredientsScanned"] = items.Scanned;
            payload["recipes"] = null;
            payload["write"] = null;

            if (request.Action == "recipes")
                payload["recipes"] = RecipeRows(scope[0], items);

            if (items.Error != null)
                ((Dictionary<string, object>)payload["notes"])["ingredientScanFailed"] = items.Error;
            if (benchRows.Count == 0 && skippedByFaction > 0)
                ((Dictionary<string, object>)payload["notes"])["everyBenchWasSomebodyElse"] =
                    "All " + skippedByFaction + " bill giver(s) on this map belong to another faction (or to nobody), "
                    + "so the colony-only default removed every one. Pass allFactions:true to see them. An empty "
                    + "benches[] here is NOT 'this colony has no worktable'.";

            return payload;
        }

        // ------------------------------------------------------------ bench row

        private static Dictionary<string, object> BenchRow(
            Thing thing, BillCommon.MapItems items, Attention attention, bool withBills)
        {
            var giver = thing as IBillGiver;
            var usable = BridgeCommon.TryN(() => giver == null ? true : giver.CurrentlyUsableForBills());
            var power = SafeComp<CompPowerTrader>(thing);

            var row = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "thingId", BridgeCommon.SafeString(() => thing.ThingID) },
                { "defName", BridgeCommon.SafeString(() => thing.def.defName) },
                { "label", BridgeCommon.SafeString(() => thing.LabelCap) },
                { "position", BridgeCommon.PositionOf(thing) },
                { "faction", BridgeCommon.SafeString(() => thing.Faction == null ? null : thing.Faction.Name) },
                { "usableForBills", usable },
                { "needsPower", power != null },
                { "powered", power == null ? (object)true : BridgeCommon.Try(() => power.PowerOn, false) },
                { "maxBills", MaxBillsPerBench }
            };

            if (usable == false)
                row["unusableReason"] = UnusableReason(thing);
            else
                row["unusableReason"] = null;

            if (usable != true)
                attention.BenchesUnusable++;

            if (!withBills)
                return row;

            var bills = new List<object>();
            var stack = giver == null ? null : BridgeCommon.Try(() => giver.BillStack, (BillStack)null);
            var list = stack == null ? null : BridgeCommon.Try(() => stack.Bills, (List<Bill>)null);
            var canRun = 0;

            if (list != null)
            {
                for (var i = 0; i < list.Count; i++)
                {
                    var bill = list[i];
                    if (bill == null)
                        continue;
                    var billRow = BillRow(thing, giver, bill, i, items, attention);
                    if (BridgeCommon.Bool(billRow, "canRunNow"))
                        canRun++;
                    bills.Add(billRow);
                }
            }

            row["bills"] = bills;
            row["billCount"] = bills.Count;
            row["activeBillCount"] = bills.Count(b => BridgeCommon.Bool((Dictionary<string, object>)b, "active"));
            row["billsCanRunNow"] = canRun;
            row["billStackEmpty"] = bills.Count == 0;
            row["billStackFull"] = bills.Count >= MaxBillsPerBench;
            row["billStackUnreadable"] = list == null;

            if (bills.Count == 0) attention.BenchesWithNoBills++;
            else if ((int)row["activeBillCount"] == 0) attention.BenchesWithNoActiveBill++;

            return row;
        }

        private static string UnusableReason(Thing thing)
        {
            var power = SafeComp<CompPowerTrader>(thing);
            if (power != null && !BridgeCommon.Try(() => power.PowerOn, false))
                return "unpowered";
            var broken = SafeComp<CompBreakdownable>(thing);
            if (broken != null && BridgeCommon.Try(() => broken.BrokenDown, false))
                return "broken down";
            var fuel = SafeComp<CompRefuelable>(thing);
            if (fuel != null && !BridgeCommon.Try(() => fuel.HasFuel, true))
                return "out of fuel";
            return "not usable for bills";
        }

        // ------------------------------------------------------------- bill row

        private static Dictionary<string, object> BillRow(
            Thing bench, IBillGiver giver, Bill bill, int index,
            BillCommon.MapItems items, Attention attention)
        {
            var production = bill as Bill_Production;
            var verdict = BillCommon.Judge(bench, giver, bill, items);

            var suspended = BridgeCommon.Try(() => bill.suspended, false);
            var paused = production != null && BridgeCommon.Try(() => production.paused, false);
            var finished = BillCommon.IsFinished(production);

            if (finished && attention != null) attention.FinishedBills++;
            if (suspended && attention != null) attention.SuspendedBills++;
            if (paused && attention != null) attention.PausedBills++;
            if (!verdict.IngredientsSatisfied && attention != null) attention.BillsShortOfIngredients++;

            var row = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "index", index },
                { "label", BillCommon.Label(bill) },
                { "recipe", BridgeCommon.SafeString(() => bill.recipe == null ? null : bill.recipe.defName) },
                { "recipeLabel", BridgeCommon.SafeString(() => bill.recipe == null ? null : bill.recipe.label) },
                { "billClass", BridgeCommon.SafeString(() => bill.GetType().Name) },
                { "repeatInfo", BillCommon.RepeatInfo(production) },
                // All five present on every bill, false included.
                { "suspended", suspended },
                { "paused", paused },
                { "finished", finished },
                { "active", !suspended && !paused && !finished },
                { "completableEver", BridgeCommon.Try(() => bill.CompletableEver, true) },
                { "canRunNow", verdict.CanRunNow },
                { "blockedBy", verdict.BlockedBy },
                { "ingredients", verdict.Ingredients },
                { "ingredientsSatisfied", verdict.IngredientsSatisfied },
                { "filter", BillCommon.FilterBlock(bill) },
                { "config", BillCommon.ConfigBlock(bill) }
            };
            return row;
        }

        // ----------------------------------------------------------- recipe rows

        private static List<object> RecipeRows(Thing bench, BillCommon.MapItems items)
        {
            var rows = new List<object>();
            List<RecipeDef> all;
            try { all = bench.def.AllRecipes; }
            catch { all = null; }
            if (all == null)
                return rows;

            foreach (var recipe in all)
            {
                if (recipe == null)
                    continue;

                // The Bills tab's own gate, both halves.
                var availableNow = BillCommon.RecipeAvailableNow(recipe);
                var availableOnNow = BillCommon.RecipeAvailableOnNow(recipe, bench);
                if (availableNow == false || availableOnNow == false)
                    continue;

                var filter = BridgeCommon.Try(
                    () => recipe.defaultIngredientFilter ?? recipe.fixedIngredientFilter, (ThingFilter)null);

                bool satisfied;
                int excluded;
                List<object> missing;
                var ingredients = BillCommon.IngredientRows(
                    recipe, filter, null, bench, BillCommon.UnlimitedRadius, items,
                    out satisfied, out excluded, out missing);

                var blocked = new List<object>(missing);
                if (!satisfied && excluded > 0)
                    blocked.Add("filter excludes " + excluded + " on map");

                rows.Add(new Dictionary<string, object>
                {
                    { "defName", BridgeCommon.SafeString(() => recipe.defName) },
                    { "label", BridgeCommon.SafeString(() => recipe.label) },
                    { "availableNow", availableNow },
                    { "availableOnNow", availableOnNow },
                    { "workAmount", BridgeCommon.TryN(() => recipe.WorkAmountTotal(null)) },
                    { "workSkill", BridgeCommon.SafeString(() => recipe.workSkill == null ? null : recipe.workSkill.defName) },
                    { "minSkill", MinSkill(recipe) },
                    { "products", Products(recipe) },
                    { "ingredients", ingredients },
                    { "ingredientsOnHand", satisfied },
                    { "blockedBy", blocked },
                    { "filter", BillCommon.FilterBlock(recipe, filter) }
                });
            }

            return rows.OrderBy(r => (string)((Dictionary<string, object>)r)["label"] ?? "").ToList();
        }

        private static object MinSkill(RecipeDef recipe)
        {
            List<SkillRequirement> requirements;
            try { requirements = recipe.skillRequirements; }
            catch { return null; }
            if (requirements == null || requirements.Count == 0)
                return null;
            return requirements
                .Where(r => r != null)
                .Select(r => (object)new Dictionary<string, object>
                {
                    { "skill", BridgeCommon.SafeString(() => r.skill == null ? null : r.skill.defName) },
                    { "minLevel", BridgeCommon.TryN(() => r.minLevel) }
                })
                .ToList();
        }

        private static object Products(RecipeDef recipe)
        {
            List<ThingDefCountClass> products;
            try { products = recipe.products; }
            catch { return new List<object>(); }
            if (products == null)
                return new List<object>();
            return products
                .Where(p => p != null && p.thingDef != null)
                .Select(p => (object)new Dictionary<string, object>
                {
                    { "defName", BridgeCommon.SafeString(() => p.thingDef.defName) },
                    { "label", BridgeCommon.SafeString(() => p.thingDef.label) },
                    { "count", BridgeCommon.TryN(() => p.count) }
                })
                .ToList();
        }

        // =============================================================== writes

        private static WritePlan PlanWrite(IRimBridgeContext ctx, Request request)
        {
            var plan = new WritePlan();

            Map map;
            string mapError;
            if (!BridgeCommon.TryGetMap(ToolName, out map, out mapError))
            {
                plan.Payload = Refuse(request.Action, request.DryRun, mapError, null);
                return plan;
            }

            if (request.BenchSpec == null)
            {
                plan.Payload = Refuse(request.Action, request.DryRun,
                    "action:'" + request.Action + "' needs a bench. Pass bench:'<ThingID, defName@x,z or a label substring>'.",
                    null);
                return plan;
            }

            List<Thing> givers;
            string giversError;
            if (!TryGetBillGivers(map, out givers, out giversError))
            {
                plan.Payload = Refuse(request.Action, request.DryRun, giversError, null);
                return plan;
            }

            Thing bench;
            List<object> candidates;
            string benchError;
            if (!ResolveBench(givers, request.BenchSpec, out bench, out candidates, out benchError))
            {
                plan.Payload = Refuse(request.Action, request.DryRun, benchError, candidates);
                return plan;
            }

            var giver = bench as IBillGiver;
            var stack = giver == null ? null : BridgeCommon.Try(() => giver.BillStack, (BillStack)null);
            var list = stack == null ? null : BridgeCommon.Try(() => stack.Bills, (List<Bill>)null);
            if (stack == null || list == null)
            {
                plan.Payload = Refuse(request.Action, request.DryRun,
                    "That bill giver's BillStack could not be read, so nothing can be written to it.", null);
                return plan;
            }

            // ------------------------------------------------------- the recipe
            RecipeDef recipe = null;
            if (request.Action == "add")
            {
                if (request.RecipeSpec == null)
                {
                    plan.Payload = Refuse(request.Action, request.DryRun,
                        "action:'add' needs a recipe. Pass recipe:'<defName, label or a unique substring>'; "
                        + "action:'recipes' lists what this bench offers.", null);
                    return plan;
                }
                string recipeError;
                if (!ResolveRecipe(bench, request.RecipeSpec, out recipe, out candidates, out recipeError))
                {
                    plan.Payload = Refuse(request.Action, request.DryRun, recipeError, candidates);
                    return plan;
                }
                if (list.Count >= MaxBillsPerBench)
                {
                    plan.Payload = Refuse(request.Action, request.DryRun,
                        "That bench already holds " + list.Count + " bills and BillStack.MaxCount is "
                        + MaxBillsPerBench + "; the game's own Add button is greyed out at this point. "
                        + "Delete one first.", null);
                    return plan;
                }
            }

            // -------------------------------------------------------- the index
            Bill target = null;
            if (request.Action == "set" || request.Action == "delete" || request.Action == "move")
            {
                if (request.Index < 0 || request.Index >= list.Count)
                {
                    plan.Payload = Refuse(request.Action, request.DryRun,
                        "index " + request.Index + " is outside this bench's bill stack, which holds "
                        + list.Count + " bill(s)"
                        + (list.Count == 0 ? "." : " (valid: 0.." + (list.Count - 1) + ")."),
                        list.Select((b, i) => (object)new Dictionary<string, object>
                        {
                            { "index", i }, { "label", BillCommon.Label(b) }
                        }).ToList());
                    return plan;
                }
                target = list[request.Index];
                recipe = BridgeCommon.Try(() => target.recipe, (RecipeDef)null);
            }

            // ------------------------------------------------------ move target
            var moveTo = request.To;
            if (request.Action == "move")
            {
                if (request.Direction != null && request.To >= 0)
                {
                    plan.Payload = Refuse(request.Action, request.DryRun,
                        "action:'move' takes `to` OR `direction`, not both. Got to=" + request.To
                        + " and direction='" + request.Direction + "'.", null);
                    return plan;
                }
                if (request.Direction != null)
                {
                    if (request.Direction == "up") moveTo = request.Index - 1;
                    else if (request.Direction == "down") moveTo = request.Index + 1;
                    else
                    {
                        plan.Payload = Refuse(request.Action, request.DryRun,
                            "direction must be 'up' or 'down'. Got: " + request.Direction, null);
                        return plan;
                    }
                }
                if (moveTo < 0 || moveTo >= list.Count)
                {
                    plan.Payload = Refuse(request.Action, request.DryRun,
                        "the bill would land at position " + moveTo + ", which is outside a stack of "
                        + list.Count + " (valid: 0.." + (list.Count - 1) + ").", null);
                    return plan;
                }
                request.To = moveTo;
            }

            // ----------------------------------------------------- the options
            Options options;
            string optionError;
            List<object> optionCandidates;
            if (!BuildOptions(map, request, recipe, out options, out optionCandidates, out optionError))
            {
                plan.Payload = Refuse(request.Action, request.DryRun, optionError, optionCandidates);
                return plan;
            }
            if (request.Action == "set" && !options.Any)
            {
                plan.Payload = Refuse(request.Action, request.DryRun,
                    "action:'set' was given no option to change. Pass at least one of repeatMode, repeatCount, "
                    + "targetCount, unpauseWhenYouHave, pauseWhenSatisfied, suspended, ingredientSearchRadius, "
                    + "skillMin, skillMax, worker, storeMode, allow, only, disallow.", null);
                return plan;
            }

            var items = BillCommon.MapItems.Build(map);
            plan.Before = StackSnapshot(bench, giver, list, target, items);
            plan.BenchId = BridgeCommon.SafeString(() => bench.ThingID);
            plan.Recipe = recipe;
            plan.Opt = options;

            if (request.DryRun)
            {
                plan.Payload = DryRunPayload(map, request, bench, giver, list, target, recipe, options, items, plan.Before);
                return plan;
            }

            // Only a real write that nothing refused gets to open a menu.
            if (request.Watch)
            {
                plan.Session = Watch.Open(ctx, bench,
                    bench is Building_WorkTable ? typeof(ITab_Bills) : null, null, true);
            }

            return plan;
        }

        private static object Commit(Request request, WritePlan plan)
        {
            Map map;
            string mapError;
            if (!BridgeCommon.TryGetMap(ToolName, out map, out mapError))
                return Refuse(request.Action, false, mapError, null);

            List<Thing> givers;
            string giversError;
            if (!TryGetBillGivers(map, out givers, out giversError))
                return Refuse(request.Action, false, giversError, null);

            // Re-resolved rather than carried: a reference across the hop gap is a
            // reference to whatever the game did in between.
            var bench = givers.FirstOrDefault(
                t => string.Equals(BridgeCommon.SafeString(() => t.ThingID), plan.BenchId, StringComparison.Ordinal));
            if (bench == null)
            {
                var payload = Refuse(request.Action, false,
                    "The bench " + plan.BenchId + " was gone by the time the write ran; nothing was changed.", null);
                payload["watch"] = plan.Session == null
                    ? Watch.Skipped("watch:false")
                    : Watch.Finish(plan.Session, request.WatchSeconds);
                return payload;
            }

            var giver = (IBillGiver)bench;
            var stack = BridgeCommon.Try(() => giver.BillStack, (BillStack)null);
            var list = stack == null ? null : BridgeCommon.Try(() => stack.Bills, (List<Bill>)null);
            if (stack == null || list == null)
            {
                var payload = Refuse(request.Action, false,
                    "That bill giver's BillStack could not be read at write time; nothing was changed.", null);
                payload["watch"] = plan.Session == null
                    ? Watch.Skipped("watch:false")
                    : Watch.Finish(plan.Session, request.WatchSeconds);
                return payload;
            }

            var refusal = (string)null;
            var applied = false;
            var newIndex = -1;

            switch (request.Action)
            {
                case "add":
                    if (list.Count >= MaxBillsPerBench)
                    {
                        refusal = "The stack filled to BillStack.MaxCount (" + MaxBillsPerBench
                                  + ") before the write ran; nothing was added.";
                        break;
                    }
                    Bill created = null;
                    try { created = plan.Recipe.MakeNewBill(null); }
                    catch (Exception e) { refusal = "RecipeDef.MakeNewBill threw " + e.GetType().Name + "; nothing was added."; }
                    if (created != null)
                    {
                        ApplyOptions(created, plan.Opt);
                        try { stack.AddBill(created); applied = true; }
                        catch (Exception e) { refusal = "BillStack.AddBill threw " + e.GetType().Name + "."; }
                        if (applied)
                            newIndex = BridgeCommon.Try(() => stack.IndexOf(created), list.Count - 1);
                    }
                    break;

                case "set":
                    if (request.Index >= list.Count)
                    {
                        refusal = "The stack shrank before the write ran; index " + request.Index + " no longer exists.";
                        break;
                    }
                    ApplyOptions(list[request.Index], plan.Opt);
                    applied = true;
                    newIndex = request.Index;
                    break;

                case "delete":
                    if (request.Index >= list.Count)
                    {
                        refusal = "The stack shrank before the write ran; index " + request.Index + " no longer exists.";
                        break;
                    }
                    var doomed = list[request.Index];
                    try { stack.Delete(doomed); applied = true; }
                    catch (Exception e) { refusal = "BillStack.Delete threw " + e.GetType().Name + "."; }
                    break;

                case "move":
                    if (request.Index >= list.Count)
                    {
                        refusal = "The stack shrank before the write ran; index " + request.Index + " no longer exists.";
                        break;
                    }
                    var moving = list[request.Index];
                    var offset = request.To - request.Index;
                    // BillStack.Reorder(bill, offset) removes and re-inserts; a
                    // target beyond the end would insert past it, so it is clamped
                    // to the stack that exists NOW.
                    var clamped = Math.Max(0, Math.Min(list.Count - 1, request.To));
                    offset = clamped - request.Index;
                    if (offset == 0)
                    {
                        applied = true;             // a legal no-op, not a failure
                        newIndex = request.Index;
                        break;
                    }
                    try { stack.Reorder(moving, offset); applied = true; newIndex = clamped; }
                    catch (Exception e) { refusal = "BillStack.Reorder threw " + e.GetType().Name + "."; }
                    break;
            }

            var items = BillCommon.MapItems.Build(map);
            var after = StackSnapshot(bench, giver, BridgeCommon.Try(() => stack.Bills, new List<Bill>()),
                                      newIndex >= 0 ? BillAt(stack, newIndex) : null, items);

            var reply = Base(request, map);
            var attention = new Attention();
            reply["benches"] = new List<object> { BenchRow(bench, items, attention, true) };
            reply["benchCount"] = 1;
            reply["benchesOnMap"] = givers.Count;
            reply["attention"] = attention.ToPayload();
            reply["ingredientsScanned"] = items.Scanned;
            reply["recipes"] = null;
            reply["applied"] = applied;
            reply["write"] = WriteBlock(request, plan.Recipe, plan.Before, after, refusal, false);
            // A write-time refusal is an ANSWER, not a tool failure: the reply
            // stays success:true with applied:false and write.refused:true.
            reply["watch"] = plan.Session == null
                ? Watch.Skipped("watch:false")
                : Watch.Finish(plan.Session, request.WatchSeconds);
            return reply;
        }

        private static Bill BillAt(BillStack stack, int index)
        {
            try
            {
                var list = stack.Bills;
                return list != null && index >= 0 && index < list.Count ? list[index] : null;
            }
            catch { return null; }
        }

        // ------------------------------------------------------------- dry run

        private static Dictionary<string, object> DryRunPayload(
            Map map, Request request, Thing bench, IBillGiver giver, List<Bill> list,
            Bill target, RecipeDef recipe, Options options, BillCommon.MapItems items,
            Dictionary<string, object> before)
        {
            // Nothing below touches the game. `add` predicts the new bill's
            // filter with a standalone ThingFilter rather than MakeNewBill,
            // because MakeNewBill's InitializeAfterClone consumes a bill id;
            // `set` predicts on Bill.Clone(), which copies and mutates nothing.
            var labels = list.Select((b, i) => (object)new Dictionary<string, object>
            {
                { "index", i }, { "label", BillCommon.Label(b) }
            }).ToList();

            var afterLabels = new List<object>(labels);
            Dictionary<string, object> afterBill = null;

            if (request.Action == "add")
            {
                var filter = PredictedFilter(recipe, options);
                bool satisfied; int excluded; List<object> missing;
                var ingredients = BillCommon.IngredientRows(
                    recipe, filter, null, bench,
                    options.SearchRadius ?? BillCommon.UnlimitedRadius, items,
                    out satisfied, out excluded, out missing);

                var blocked = new List<object>(missing);
                if (!satisfied && excluded > 0)
                    blocked.Add("filter excludes " + excluded + " on map");
                var usable = BridgeCommon.TryN(() => giver.CurrentlyUsableForBills());
                if (usable == false) blocked.Add(UnusableReason(bench));
                if (options.Suspended == true) blocked.Add("suspended");
                if (BillCommon.RecipeAvailableNow(recipe) == false)
                    blocked.Add("recipe not available (research, meme or faction requirement)");

                afterBill = new Dictionary<string, object>
                {
                    { "index", list.Count },
                    { "label", BridgeCommon.SafeString(() => recipe.label) },
                    { "recipe", BridgeCommon.SafeString(() => recipe.defName) },
                    { "canRunNow", blocked.Count == 0 },
                    { "blockedBy", blocked },
                    { "ingredients", ingredients },
                    { "ingredientsSatisfied", satisfied },
                    { "filter", BillCommon.FilterBlock(recipe, filter) },
                    { "config", PredictedConfig(options) },
                    { "predicted", true }
                };
                afterLabels.Add(new Dictionary<string, object>
                {
                    { "index", list.Count },
                    { "label", BridgeCommon.SafeString(() => recipe.label) }
                });
            }
            else if (request.Action == "set")
            {
                Bill clone = null;
                try { clone = target.Clone(); }
                catch { clone = null; }
                if (clone != null)
                {
                    ApplyOptions(clone, options);
                    afterBill = BillRow(bench, giver, clone, request.Index, items, null);
                    afterBill["predicted"] = true;
                }
            }
            else if (request.Action == "delete")
            {
                afterLabels = labels.Where((x, i) => i != request.Index)
                                    .Select((x, i) => (object)new Dictionary<string, object>
                                    {
                                        { "index", i },
                                        { "label", ((Dictionary<string, object>)x)["label"] }
                                    }).ToList();
            }
            else if (request.Action == "move")
            {
                var reordered = new List<object>(labels);
                var moving = reordered[request.Index];
                reordered.RemoveAt(request.Index);
                reordered.Insert(Math.Max(0, Math.Min(reordered.Count, request.To)), moving);
                afterLabels = reordered.Select((x, i) => (object)new Dictionary<string, object>
                {
                    { "index", i },
                    { "label", ((Dictionary<string, object>)x)["label"] }
                }).ToList();
                afterBill = new Dictionary<string, object>
                {
                    { "index", request.To },
                    { "label", BillCommon.Label(target) },
                    { "predicted", true }
                };
            }

            var after = new Dictionary<string, object>
            {
                { "billCount", afterLabels.Count },
                { "bills", afterLabels },
                { "bill", afterBill }
            };

            var attention = new Attention();
            var payload = Base(request, map);
            payload["benches"] = new List<object> { BenchRow(bench, items, attention, true) };
            payload["benchCount"] = 1;
            payload["attention"] = attention.ToPayload();
            payload["ingredientsScanned"] = items.Scanned;
            payload["recipes"] = null;
            payload["applied"] = false;
            payload["write"] = WriteBlock(request, recipe, before, after, null, true);
            payload["watch"] = Watch.Skipped("dry run");
            return payload;
        }

        /// <summary>What a new bill's ingredient filter would hold: the recipe's
            /// own default allowances, or only the explicit `only` list when
            /// one was supplied, followed by additive allow and disallow. A
        /// standalone ThingFilter, attached to nothing.</summary>
        private static ThingFilter PredictedFilter(RecipeDef recipe, Options options)
        {
            ThingFilter filter;
            try
            {
                filter = new ThingFilter();
                var source = recipe.defaultIngredientFilter ?? recipe.fixedIngredientFilter;
                if (source != null)
                    filter.CopyAllowancesFrom(source);
            }
            catch { return recipe.defaultIngredientFilter ?? recipe.fixedIngredientFilter; }

            if (options.ReplaceAllowances)
                BridgeCommon.Try<object>(() => { filter.SetDisallowAll(); return null; }, null);
            foreach (var def in options.Allow)
                BridgeCommon.Try<object>(() => { filter.SetAllow(def, true); return null; }, null);
            foreach (var def in options.Disallow)
                BridgeCommon.Try<object>(() => { filter.SetAllow(def, false); return null; }, null);
            return filter;
        }

        private static Dictionary<string, object> PredictedConfig(Options options)
        {
            return new Dictionary<string, object>
            {
                { "repeatMode", options.RepeatMode == null ? "RepeatCount (the game's default for a new bill)" : options.RepeatMode.defName },
                { "repeatCount", options.RepeatCount },
                { "targetCount", options.TargetCount },
                { "pauseWhenSatisfied", options.PauseWhenSatisfied },
                { "unpauseWhenYouHave", options.UnpauseWhenYouHave },
                { "ingredientSearchRadius", options.SearchRadius },
                { "skillRange", options.SkillMin == null && options.SkillMax == null ? null
                    : new Dictionary<string, object> { { "min", options.SkillMin }, { "max", options.SkillMax } } },
                { "pawnRestriction", options.Worker == null ? null : BridgeCommon.SafeString(() => options.Worker.LabelShortCap) },
                { "storeMode", options.StoreMode == null ? null : options.StoreMode.defName },
                { "storeZone", options.StoreZone == null ? null : BridgeCommon.SafeString(() => options.StoreZone.label) },
                { "predicted", true }
            };
        }

        // --------------------------------------------------------- write block

        private static Dictionary<string, object> WriteBlock(
            Request request, RecipeDef recipe,
            Dictionary<string, object> before, Dictionary<string, object> after,
            string refusal, bool predicted)
        {
            return new Dictionary<string, object>
            {
                { "action", request.Action },
                { "requestedBench", request.BenchSpec },
                { "requestedRecipe", request.RecipeSpec },
                { "resolvedRecipe", recipe == null ? null : BridgeCommon.SafeString(() => recipe.defName) },
                { "index", request.Index },
                { "to", request.Action == "move" ? (object)request.To : null },
                { "refused", refusal != null },
                { "reason", refusal },
                { "before", before },
                { "after", after },
                { "afterIsPredicted", predicted },
                { "changed", Changed(before, after) },
                { "canRunNow", AfterBool(after, "canRunNow") },
                { "blockedBy", AfterList(after, "blockedBy") }
            };
        }

        private static object AfterBool(Dictionary<string, object> after, string key)
        {
            var bill = after == null ? null : after.ContainsKey("bill") ? after["bill"] as Dictionary<string, object> : null;
            if (bill == null || !bill.ContainsKey(key))
                return null;
            return bill[key];
        }

        private static object AfterList(Dictionary<string, object> after, string key)
        {
            var value = AfterBool(after, key);
            return value ?? new List<object>();
        }

        /// <summary>Which named things differ between the two snapshots. Compared
        /// on the read-back, never on the request.</summary>
        private static List<object> Changed(Dictionary<string, object> before, Dictionary<string, object> after)
        {
            var changed = new List<object>();
            if (before == null || after == null)
                return changed;

            if (!Equals(before["billCount"], after["billCount"]))
                changed.Add("billCount " + before["billCount"] + " -> " + after["billCount"]);

            var beforeOrder = string.Join("|", (before["bills"] as List<object> ?? new List<object>())
                .Select(b => (string)((Dictionary<string, object>)b)["label"] ?? "?").ToArray());
            var afterOrder = string.Join("|", (after["bills"] as List<object> ?? new List<object>())
                .Select(b => (string)((Dictionary<string, object>)b)["label"] ?? "?").ToArray());
            if (!string.Equals(beforeOrder, afterOrder, StringComparison.Ordinal))
                changed.Add("order " + beforeOrder + " -> " + afterOrder);

            var beforeBill = before["bill"] as Dictionary<string, object>;
            var afterBill = after["bill"] as Dictionary<string, object>;
            var beforeConfig = beforeBill == null ? null : beforeBill.ContainsKey("config") ? beforeBill["config"] as Dictionary<string, object> : null;
            var afterConfig = afterBill == null ? null : afterBill.ContainsKey("config") ? afterBill["config"] as Dictionary<string, object> : null;
            if (beforeConfig != null && afterConfig != null)
            {
                foreach (var key in beforeConfig.Keys)
                {
                    if (!afterConfig.ContainsKey(key))
                        continue;
                    var a = Flatten(beforeConfig[key]);
                    var b = Flatten(afterConfig[key]);
                    if (!string.Equals(a, b, StringComparison.Ordinal))
                        changed.Add(key + " " + a + " -> " + b);
                }
            }
            if (beforeBill != null && afterBill != null)
            {
                var beforeFilter = beforeBill.ContainsKey("filter")
                    ? beforeBill["filter"] as Dictionary<string, object> : null;
                var afterFilter = afterBill.ContainsKey("filter")
                    ? afterBill["filter"] as Dictionary<string, object> : null;
                if (beforeFilter != null && afterFilter != null)
                {
                    var a = Flatten(beforeFilter);
                    var b = Flatten(afterFilter);
                    if (!string.Equals(a, b, StringComparison.Ordinal))
                        changed.Add("ingredientFilter " + a + " -> " + b);
                }
                foreach (var key in new[] { "suspended", "canRunNow" })
                {
                    if (!beforeBill.ContainsKey(key) || !afterBill.ContainsKey(key))
                        continue;
                    if (!Equals(beforeBill[key], afterBill[key]))
                        changed.Add(key + " " + beforeBill[key] + " -> " + afterBill[key]);
                }
            }
            return changed;
        }

        private static string Flatten(object value)
        {
            if (value == null) return "null";
            var dict = value as Dictionary<string, object>;
            if (dict != null)
                return "{" + string.Join(",", dict.Select(p => p.Key + "=" + Flatten(p.Value)).ToArray()) + "}";
            var list = value as IEnumerable<object>;
            if (list != null)
                return "[" + string.Join(",", list.Select(Flatten).ToArray()) + "]";
            return Convert.ToString(value, System.Globalization.CultureInfo.InvariantCulture);
        }

        private static Dictionary<string, object> StackSnapshot(
            Thing bench, IBillGiver giver, List<Bill> list, Bill target, BillCommon.MapItems items)
        {
            list = list ?? new List<Bill>();
            return new Dictionary<string, object>
            {
                { "billCount", list.Count },
                { "bills", list.Select((b, i) => (object)new Dictionary<string, object>
                    {
                        { "index", i }, { "label", BillCommon.Label(b) }
                    }).ToList() },
                { "bill", target == null ? null : BillRow(bench, giver, target, list.IndexOf(target), items, null) }
            };
        }

        // ============================================================== options

        private static bool BuildOptions(
            Map map, Request request, RecipeDef recipe,
            out Options options, out List<object> candidates, out string error)
        {
            options = new Options();
            candidates = null;
            error = null;

            if (request.RepeatModeSpec != null)
            {
                var mode = RepeatModeByName(request.RepeatModeSpec);
                if (mode == null)
                {
                    error = "repeatMode must be Forever, RepeatCount or TargetCount. Got: " + request.RepeatModeSpec;
                    return false;
                }
                options.RepeatMode = mode;
            }

            if (request.RepeatCount >= 0)
            {
                options.RepeatCount = request.RepeatCount;
                if (options.RepeatMode == null)
                    options.RepeatMode = BridgeCommon.Try(() => BillRepeatModeDefOf.RepeatCount, (BillRepeatModeDef)null);
            }
            if (request.TargetCount >= 0)
            {
                options.TargetCount = request.TargetCount;
                if (options.RepeatMode == null)
                    options.RepeatMode = BridgeCommon.Try(() => BillRepeatModeDefOf.TargetCount, (BillRepeatModeDef)null);
            }
            if (request.UnpauseWhenYouHave >= 0)
                options.UnpauseWhenYouHave = request.UnpauseWhenYouHave;

            if (request.PauseWhenSatisfiedSpec != null)
            {
                var value = OnOff(request.PauseWhenSatisfiedSpec);
                if (value == null) { error = "pauseWhenSatisfied must be 'on' or 'off'. Got: " + request.PauseWhenSatisfiedSpec; return false; }
                options.PauseWhenSatisfied = value;
            }
            if (request.SuspendedSpec != null)
            {
                var value = OnOff(request.SuspendedSpec);
                if (value == null) { error = "suspended must be 'on' or 'off'. Got: " + request.SuspendedSpec; return false; }
                options.Suspended = value;
            }

            if (request.SearchRadius >= 0)
            {
                if (request.SearchRadius > (int)BillCommon.UnlimitedRadius)
                {
                    error = "ingredientSearchRadius must be between 0 and " + (int)BillCommon.UnlimitedRadius
                            + " (Bill.MaxIngredientSearchRadius, which means unlimited). Got: " + request.SearchRadius;
                    return false;
                }
                options.SearchRadius = request.SearchRadius;
            }

            if (request.SkillMin >= 0 || request.SkillMax >= 0)
            {
                if (request.SkillMin > 20 || request.SkillMax > 20)
                {
                    error = "skillMin and skillMax must be between 0 and 20. Got skillMin=" + request.SkillMin
                            + ", skillMax=" + request.SkillMax + ".";
                    return false;
                }
                if (request.SkillMin >= 0 && request.SkillMax >= 0 && request.SkillMin > request.SkillMax)
                {
                    error = "skillMin (" + request.SkillMin + ") is above skillMax (" + request.SkillMax + ").";
                    return false;
                }
                if (request.SkillMin >= 0) options.SkillMin = request.SkillMin;
                if (request.SkillMax >= 0) options.SkillMax = request.SkillMax;
            }

            if (request.WorkerSpec != null)
            {
                options.WorkerGiven = true;
                if (!string.Equals(request.WorkerSpec, "anyone", StringComparison.OrdinalIgnoreCase))
                {
                    Pawn pawn;
                    if (!ResolveColonist(map, request.WorkerSpec, out pawn, out candidates, out error))
                        return false;
                    options.Worker = pawn;
                }
            }

            if (request.StoreModeSpec != null)
            {
                if (string.Equals(request.StoreModeSpec, "bestStockpile", StringComparison.OrdinalIgnoreCase))
                    options.StoreMode = BridgeCommon.Try(() => BillStoreModeDefOf.BestStockpile, (BillStoreModeDef)null);
                else if (string.Equals(request.StoreModeSpec, "dropOnFloor", StringComparison.OrdinalIgnoreCase))
                    options.StoreMode = BridgeCommon.Try(() => BillStoreModeDefOf.DropOnFloor, (BillStoreModeDef)null);
                else
                {
                    Zone_Stockpile zone;
                    if (!ResolveStockpile(map, request.StoreModeSpec, out zone, out candidates, out error))
                        return false;
                    options.StoreZone = zone;
                    options.StoreMode = BridgeCommon.Try(() => BillStoreModeDefOf.SpecificStockpile, (BillStoreModeDef)null);
                }
                if (options.StoreMode == null)
                {
                    error = "BillStoreModeDefOf was not readable, so storeMode could not be resolved.";
                    return false;
                }
            }

            if (request.AllowNames.Count > 0 || request.OnlyNames.Count > 0 || request.DisallowNames.Count > 0)
            {
                if (recipe == null)
                {
                    error = "allow/only/disallow need a recipe to bound them, and this bill's recipe could not be read.";
                    return false;
                }
                if (request.AllowNames.Count > 0 && request.OnlyNames.Count > 0)
                {
                    error = "Use either allow (additive) or only (replace the whitelist), not both. Nothing was changed.";
                    return false;
                }
                if (!ResolveFilterNames(recipe, request.AllowNames, options.Allow, out candidates, out error))
                    return false;
                if (!ResolveFilterNames(recipe, request.OnlyNames, options.Allow, out candidates, out error))
                    return false;
                options.ReplaceAllowances = request.OnlyNames.Count > 0;
                if (!ResolveFilterNames(recipe, request.DisallowNames, options.Disallow, out candidates, out error))
                    return false;
            }

            return true;
        }

        /// <summary>
        /// A def or category name, expanded to the defs the recipe could actually
        /// take. A name that resolves to nothing the recipe's own
        /// fixedIngredientFilter allows REFUSES the call: half an ingredient
        /// filter is worse than none, and the caller has to be told which name
        /// was wrong rather than discovering it in a bill that will not run.
        /// </summary>
        private static bool ResolveFilterNames(
            RecipeDef recipe, List<string> names, List<ThingDef> into,
            out List<object> candidates, out string error)
        {
            candidates = null;
            error = null;

            foreach (var name in names)
            {
                var def = BridgeCommon.Try(() => DefDatabase<ThingDef>.GetNamedSilentFail(name), (ThingDef)null);
                if (def != null)
                {
                    if (!BridgeCommon.Try(() => recipe.fixedIngredientFilter == null
                                                || recipe.fixedIngredientFilter.Allows(def), true))
                    {
                        error = "'" + name + "' is outside " + recipe.defName
                                + "'s own fixedIngredientFilter, so the game would strip the allowance on the next save. "
                                + "Nothing was changed.";
                        candidates = RecipeIngredientDefNames(recipe);
                        return false;
                    }
                    into.Add(def);
                    continue;
                }

                var category = BridgeCommon.Try(() => DefDatabase<ThingCategoryDef>.GetNamedSilentFail(name), (ThingCategoryDef)null);
                if (category != null)
                {
                    List<ThingDef> descendants;
                    try { descendants = category.DescendantThingDefs.ToList(); }
                    catch { descendants = null; }
                    var kept = (descendants ?? new List<ThingDef>())
                        .Where(d => d != null && BridgeCommon.Try(
                            () => recipe.fixedIngredientFilter == null || recipe.fixedIngredientFilter.Allows(d), true))
                        .ToList();
                    if (kept.Count == 0)
                    {
                        error = "the category '" + name + "' holds nothing " + recipe.defName
                                + "'s own fixedIngredientFilter allows. Nothing was changed.";
                        candidates = RecipeIngredientDefNames(recipe);
                        return false;
                    }
                    into.AddRange(kept);
                    continue;
                }

                error = "'" + name + "' is neither a ThingDef nor a ThingCategoryDef. Nothing was changed.";
                candidates = RecipeIngredientDefNames(recipe);
                return false;
            }

            return true;
        }

        private static List<object> RecipeIngredientDefNames(RecipeDef recipe)
        {
            var names = new List<object>();
            List<IngredientCount> ingredients;
            try { ingredients = recipe.ingredients; }
            catch { return names; }
            if (ingredients == null)
                return names;
            var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (var ing in ingredients)
            {
                if (ing == null) continue;
                List<ThingDef> defs;
                try { defs = ing.filter.AllowedThingDefs.ToList(); }
                catch { continue; }
                foreach (var def in defs)
                {
                    if (def == null) continue;
                    var name = BridgeCommon.SafeString(() => def.defName);
                    if (name != null && seen.Add(name) && names.Count < MaxCandidates)
                        names.Add(name);
                }
            }
            return names;
        }

        /// <summary>
        /// Write the validated options onto one bill. Only ever called on a bill
        /// this tool owns the decision for: the real one in hop 2, or a clone /
        /// a freshly made bill on the way in.
        /// </summary>
        private static void ApplyOptions(Bill bill, Options options)
        {
            var production = bill as Bill_Production;

            if (options.Suspended != null)
                BridgeCommon.Try<object>(() => { bill.suspended = options.Suspended.Value; return null; }, null);

            if (options.SearchRadius != null)
                BridgeCommon.Try<object>(() => { bill.ingredientSearchRadius = options.SearchRadius.Value; return null; }, null);

            if (options.SkillMin != null || options.SkillMax != null)
            {
                BridgeCommon.Try<object>(() =>
                {
                    var range = bill.allowedSkillRange;
                    if (options.SkillMin != null) range.min = options.SkillMin.Value;
                    if (options.SkillMax != null) range.max = options.SkillMax.Value;
                    if (range.min > range.max) range.max = range.min;
                    bill.allowedSkillRange = range;
                    return null;
                }, null);
            }

            if (options.WorkerGiven)
            {
                // SetPawnRestriction / SetAnyPawnRestriction, the two the tab's
                // own dropdown calls. Neither logs.
                BridgeCommon.Try<object>(() =>
                {
                    if (options.Worker == null) bill.SetAnyPawnRestriction();
                    else bill.SetPawnRestriction(options.Worker);
                    return null;
                }, null);
            }

            if (production != null)
            {
                if (options.RepeatMode != null)
                    BridgeCommon.Try<object>(() => { production.repeatMode = options.RepeatMode; return null; }, null);
                if (options.RepeatCount != null)
                    BridgeCommon.Try<object>(() => { production.repeatCount = options.RepeatCount.Value; return null; }, null);
                if (options.TargetCount != null)
                    BridgeCommon.Try<object>(() => { production.targetCount = options.TargetCount.Value; return null; }, null);
                if (options.UnpauseWhenYouHave != null)
                    BridgeCommon.Try<object>(() => { production.unpauseWhenYouHave = options.UnpauseWhenYouHave.Value; return null; }, null);
                if (options.PauseWhenSatisfied != null)
                    BridgeCommon.Try<object>(() => { production.pauseWhenSatisfied = options.PauseWhenSatisfied.Value; return null; }, null);

                // SetStoreMode on a NON-production bill is a Log.ErrorOnce, and on
                // a production bill it logs when the mode and the group disagree.
                // Both are why this is inside the production branch and why the
                // group is passed only with SpecificStockpile.
                if (options.StoreMode != null)
                {
                    BridgeCommon.Try<object>(() =>
                    {
                        var group = options.StoreZone == null ? null : options.StoreZone.GetSlotGroup();
                        var specific = options.StoreMode == BillStoreModeDefOf.SpecificStockpile;
                        if (specific && group == null)
                            production.SetStoreMode(BillStoreModeDefOf.BestStockpile);
                        else if (specific)
                            production.SetStoreMode(options.StoreMode, group);
                        else
                            production.SetStoreMode(options.StoreMode);
                        return null;
                    }, null);
                }
            }

            if (options.Allow.Count > 0 || options.Disallow.Count > 0)
            {
                BridgeCommon.Try<object>(() =>
                {
                    if (options.ReplaceAllowances)
                        bill.ingredientFilter.SetDisallowAll();
                    foreach (var def in options.Allow)
                        bill.ingredientFilter.SetAllow(def, true);
                    foreach (var def in options.Disallow)
                        bill.ingredientFilter.SetAllow(def, false);
                    return null;
                }, null);
            }
        }

        // ============================================================ resolution

        /// <summary>
        /// Every bench-type bill giver on the map (pawns and corpses excluded). ThingRequestGroup.PotentialBillGiver is
        /// the game's own index; BuildingArtificial is swept afterwards for
        /// anything implementing IBillGiver that the group does not hold, so a
        /// group-membership change upstream cannot silently lose a bench.
        /// </summary>
        private static bool TryGetBillGivers(Map map, out List<Thing> givers, out string error)
        {
            givers = new List<Thing>();
            error = null;
            try
            {
                var seen = new HashSet<Thing>();
                foreach (var group in new[] { ThingRequestGroup.PotentialBillGiver,
                                              ThingRequestGroup.BuildingArtificial })
                {
                    var list = map.listerThings.ThingsInGroup(group);
                    if (list == null)
                        continue;
                    for (var i = 0; i < list.Count; i++)
                    {
                        var thing = list[i];
                        // Pawns and corpses are bill givers too (surgery), but
                        // not benches; they are never listed or matched here.
                        if (thing is IBillGiver && !(thing is Pawn) && !(thing is Corpse)
                            && thing.def != null && seen.Add(thing))
                            givers.Add(thing);
                    }
                }
                return true;
            }
            catch (Exception e)
            {
                error = "Could not read map.listerThings for bill givers: " + e.Message;
                givers = new List<Thing>();
                return false;
            }
        }

        private static bool ResolveBench(
            List<Thing> givers, string spec, out Thing bench, out List<object> candidates, out string error)
        {
            bench = null;
            candidates = null;
            error = null;

            // 1. an exact ThingID.
            bench = givers.FirstOrDefault(
                t => string.Equals(BridgeCommon.SafeString(() => t.ThingID), spec, StringComparison.Ordinal));
            if (bench != null)
                return true;

            // 2. defName@x,z.
            var at = spec.IndexOf('@');
            if (at > 0)
            {
                var defPart = spec.Substring(0, at).Trim();
                var cellPart = spec.Substring(at + 1).Trim();
                var bits = cellPart.Split(',');
                int cx, cz;
                if (bits.Length != 2
                    || !int.TryParse(bits[0].Trim(), out cx)
                    || !int.TryParse(bits[1].Trim(), out cz))
                {
                    error = "bench '" + spec + "' looks like the defName@x,z form but '" + cellPart
                            + "' is not an 'x,z' cell.";
                    return false;
                }
                var hits = givers.Where(t =>
                    string.Equals(BridgeCommon.SafeString(() => t.def.defName), defPart, StringComparison.OrdinalIgnoreCase)
                    && SameCell(t, cx, cz)).ToList();
                if (hits.Count == 1) { bench = hits[0]; return true; }
                error = hits.Count == 0
                    ? "no bill giver named '" + defPart + "' stands at " + cx + "," + cz + "."
                    : "several bill givers named '" + defPart + "' occupy " + cx + "," + cz + "; use the ThingID.";
                candidates = Candidates(givers);
                return false;
            }

            // 3. exact defName, then exact label, then a unique substring of either.
            var byDefName = givers.Where(t => string.Equals(
                BridgeCommon.SafeString(() => t.def.defName), spec, StringComparison.OrdinalIgnoreCase)).ToList();
            if (byDefName.Count == 1) { bench = byDefName[0]; return true; }

            var byLabel = givers.Where(t => string.Equals(
                BridgeCommon.SafeString(() => t.LabelCap), spec, StringComparison.OrdinalIgnoreCase)).ToList();
            if (byLabel.Count == 1) { bench = byLabel[0]; return true; }

            var bySubstring = givers.Where(t => Contains(BridgeCommon.SafeString(() => t.def.defName), spec)
                                                || Contains(BridgeCommon.SafeString(() => t.LabelCap), spec)).ToList();
            if (bySubstring.Count == 1) { bench = bySubstring[0]; return true; }

            if (bySubstring.Count == 0)
            {
                error = "no bill giver matches bench '" + spec + "'. It is matched against the ThingID, the defName, "
                        + "the label, and the defName@x,z form.";
                candidates = Candidates(givers);
                return false;
            }

            error = "bench '" + spec + "' is ambiguous -- it matches " + bySubstring.Count
                    + " bill givers. Use the ThingID or defName@x,z; both are on every row of action:'list'.";
            candidates = Candidates(bySubstring);
            return false;
        }

        private static bool SameCell(Thing thing, int x, int z)
        {
            return BridgeCommon.Try(() =>
            {
                var rect = GenAdj.OccupiedRect(thing);
                return rect.Contains(new IntVec3(x, 0, z));
            }, false);
        }

        private static List<object> Candidates(List<Thing> givers)
        {
            return givers.Take(MaxCandidates).Select(t => (object)new Dictionary<string, object>
            {
                { "thingId", BridgeCommon.SafeString(() => t.ThingID) },
                { "defName", BridgeCommon.SafeString(() => t.def.defName) },
                { "label", BridgeCommon.SafeString(() => t.LabelCap) },
                { "position", BridgeCommon.PositionOf(t) }
            }).ToList();
        }

        private static bool ResolveRecipe(
            Thing bench, string spec, out RecipeDef recipe, out List<object> candidates, out string error)
        {
            recipe = null;
            candidates = null;
            error = null;

            List<RecipeDef> all;
            try { all = bench.def.AllRecipes; }
            catch { all = null; }
            if (all == null)
            {
                error = "that bench's def.AllRecipes could not be read.";
                return false;
            }

            var offered = all.Where(r => r != null
                                         && BillCommon.RecipeAvailableNow(r) != false
                                         && BillCommon.RecipeAvailableOnNow(r, bench) != false).ToList();

            var exact = offered.Where(r => string.Equals(
                BridgeCommon.SafeString(() => r.defName), spec, StringComparison.OrdinalIgnoreCase)).ToList();
            if (exact.Count == 1) { recipe = exact[0]; return true; }

            var byLabel = offered.Where(r => string.Equals(
                BridgeCommon.SafeString(() => r.label), spec, StringComparison.OrdinalIgnoreCase)).ToList();
            if (byLabel.Count == 1) { recipe = byLabel[0]; return true; }

            var bySubstring = offered.Where(r => Contains(BridgeCommon.SafeString(() => r.defName), spec)
                                                 || Contains(BridgeCommon.SafeString(() => r.label), spec)).ToList();
            if (bySubstring.Count == 1) { recipe = bySubstring[0]; return true; }

            candidates = (bySubstring.Count > 0 ? bySubstring : offered)
                .Take(MaxCandidates)
                .Select(r => (object)new Dictionary<string, object>
                {
                    { "defName", BridgeCommon.SafeString(() => r.defName) },
                    { "label", BridgeCommon.SafeString(() => r.label) }
                }).ToList();

            if (bySubstring.Count == 0)
            {
                var known = all.Any(r => r != null && (
                    string.Equals(BridgeCommon.SafeString(() => r.defName), spec, StringComparison.OrdinalIgnoreCase)
                    || Contains(BridgeCommon.SafeString(() => r.label), spec)));
                error = known
                    ? "recipe '" + spec + "' is on this bench's def but is NOT available right now (its research, "
                      + "meme or faction requirement is unmet, or its worker refuses this bench). "
                      + "action:'recipes' lists what can be added."
                    : "recipe '" + spec + "' is not one this bench offers. action:'recipes' lists what it does.";
                return false;
            }

            error = "recipe '" + spec + "' is ambiguous -- it matches " + bySubstring.Count
                    + " recipes on this bench. Use the exact defName.";
            return false;
        }

        private static bool ResolveColonist(
            Map map, string spec, out Pawn pawn, out List<object> candidates, out string error)
        {
            pawn = null;
            candidates = null;
            error = null;

            List<Pawn> colonists;
            try { colonists = map.mapPawns.FreeColonists.ToList(); }
            catch { colonists = null; }
            if (colonists == null)
            {
                error = "map.mapPawns.FreeColonists could not be read, so worker could not be resolved.";
                return false;
            }

            var exact = colonists.Where(p => string.Equals(
                BridgeCommon.SafeString(() => p.LabelShortCap), spec, StringComparison.OrdinalIgnoreCase)).ToList();
            if (exact.Count == 1) { pawn = exact[0]; return true; }

            var bySubstring = colonists.Where(p => Contains(BridgeCommon.SafeString(() => p.LabelShortCap), spec)
                                                   || Contains(BridgeCommon.SafeString(() => p.LabelCap), spec)
                                                   || string.Equals(BridgeCommon.SafeString(() => p.ThingID), spec, StringComparison.Ordinal)).ToList();
            if (bySubstring.Count == 1) { pawn = bySubstring[0]; return true; }

            candidates = colonists.Take(MaxCandidates).Select(p => (object)new Dictionary<string, object>
            {
                { "name", BridgeCommon.SafeString(() => p.LabelShortCap) },
                { "thingId", BridgeCommon.SafeString(() => p.ThingID) }
            }).ToList();
            error = bySubstring.Count == 0
                ? "no free colonist matches worker '" + spec + "'. Pass 'anyone' to clear the restriction."
                : "worker '" + spec + "' is ambiguous -- it matches " + bySubstring.Count + " colonists.";
            return false;
        }

        private static bool ResolveStockpile(
            Map map, string spec, out Zone_Stockpile zone, out List<object> candidates, out string error)
        {
            zone = null;
            candidates = null;
            error = null;

            List<Zone_Stockpile> zones;
            try { zones = map.zoneManager.AllZones.OfType<Zone_Stockpile>().ToList(); }
            catch { zones = null; }
            if (zones == null)
            {
                error = "map.zoneManager.AllZones could not be read, so storeMode could not be resolved.";
                return false;
            }

            var exact = zones.Where(z => string.Equals(
                BridgeCommon.SafeString(() => z.label), spec, StringComparison.OrdinalIgnoreCase)).ToList();
            if (exact.Count == 1) { zone = exact[0]; return true; }

            var bySubstring = zones.Where(z => Contains(BridgeCommon.SafeString(() => z.label), spec)).ToList();
            if (bySubstring.Count == 1) { zone = bySubstring[0]; return true; }

            candidates = zones.Take(MaxCandidates).Select(z => (object)new Dictionary<string, object>
            {
                { "label", BridgeCommon.SafeString(() => z.label) },
                { "cellCount", BridgeCommon.TryN(() => z.CellCount) }
            }).ToList();
            error = bySubstring.Count == 0
                ? "storeMode '" + spec + "' is not 'bestStockpile', not 'dropOnFloor', and matches no stockpile zone."
                : "storeMode '" + spec + "' matches " + bySubstring.Count + " stockpile zones; use the exact label.";
            return false;
        }

        // ================================================================ pieces

        private static Dictionary<string, object> Base(Request request, Map map)
        {
            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "success", true },
                { "tool", ToolName },
                { "action", request.Action },
                { "mapName", BridgeCommon.SafeString(() => map.Parent == null ? null : map.Parent.LabelCap) },
                { "dryRun", request.DryRun },
                { "applied", false },
                { "watch", Watch.Skipped(request.Action == "list" || request.Action == "recipes"
                                             ? "read-only action" : "dry run") },
                { "filters", new Dictionary<string, object>
                    {
                        { "bench", request.BenchSpec },
                        { "recipe", request.RecipeSpec },
                        { "allFactions", request.AllFactions },
                        { "watch", request.Watch },
                        { "watchSeconds", request.WatchSeconds }
                    } },
                { "notes", new Dictionary<string, object>
                    {
                        { "ingredientScan", "canRunNow and ingredients[] reproduce WorkGiver_DoBill.TryFindBestBillIngredients WITHOUT a pawn: every spawned haulable that both the recipe's ingredient filter and this bill's own filter allow, unforbidden, inside ingredientSearchRadius of the bench's cell. Reachability, reservation and a pawn's own forbidden rules are NOT modelled, so a thing behind a locked door counts here and does not count to the game." },
                        { "defsDoNotMix", "available is the best SINGLE def's count, not the sum: the game satisfies one ingredient slot from one def unless recipe.allowMixingIngredients is set. availableTotal is the sum, and the two differ on purpose. shortfall == max(0, needed - available) always." },
                        { "radius999", "ingredientSearchRadius 999 is Bill.MaxIngredientSearchRadius and means UNLIMITED: at 999 the game drops its region-entry condition entirely, and 999 cells exceeds the diagonal of the largest map. config.ingredientSearchRadiusUnlimited says so as a bool." },
                        { "shouldDoNowNotCalled", "Bill_Production.ShouldDoNow() writes the bill's paused field, so it is never called here; finished, paused and active are read from the fields." }
                    } }
            };
        }

        private static Dictionary<string, object> Refuse(
            string action, bool dryRun, string error, List<object> candidates)
        {
            var payload = BridgeCommon.Failure(ToolName, error);
            payload["action"] = action;
            payload["dryRun"] = dryRun;
            payload["applied"] = false;
            payload["watch"] = Watch.Skipped("refused");
            payload["benches"] = new List<object>();
            payload["benchCount"] = 0;
            payload["attention"] = new Attention().ToPayload();
            payload["ingredientsScanned"] = 0;
            payload["recipes"] = null;
            payload["write"] = null;
            payload["notes"] = new Dictionary<string, object>
            {
                { "refusal", "Nothing was written. A refusal names what was parsed; it is not a tool failure to be retried blindly." }
            };
            if (candidates != null)
                payload["candidates"] = candidates;
            return payload;
        }

        private sealed class Attention
        {
            public int BenchesWithNoBills;
            public int BenchesWithNoActiveBill;
            public int BenchesUnusable;
            public int BillsShortOfIngredients;
            public int FinishedBills;
            public int SuspendedBills;
            public int PausedBills;

            public Dictionary<string, object> ToPayload()
            {
                return new Dictionary<string, object>
                {
                    { "benchesWithNoBills", BenchesWithNoBills },
                    { "benchesWithNoActiveBill", BenchesWithNoActiveBill },
                    { "benchesUnusable", BenchesUnusable },
                    { "billsShortOfIngredients", BillsShortOfIngredients },
                    { "finishedBills", FinishedBills },
                    { "suspendedBills", SuspendedBills },
                    { "pausedBills", PausedBills }
                };
            }
        }

        // ------------------------------------------------------------- helpers

        private static string Clean(string value)
        {
            if (value == null)
                return null;
            var trimmed = value.Trim();
            return trimmed.Length == 0 ? null : trimmed;
        }

        private static List<string> SplitNames(string value)
        {
            var names = new List<string>();
            if (value == null)
                return names;
            foreach (var part in value.Split(','))
            {
                var name = part.Trim();
                if (name.Length > 0)
                    names.Add(name);
            }
            return names;
        }

        private static bool Contains(string haystack, string needle)
        {
            return haystack != null && needle != null
                   && haystack.IndexOf(needle, StringComparison.OrdinalIgnoreCase) >= 0;
        }

        private static bool? OnOff(string value)
        {
            if (value == "on" || value == "true" || value == "yes") return true;
            if (value == "off" || value == "false" || value == "no") return false;
            return null;
        }

        private static BillRepeatModeDef RepeatModeByName(string name)
        {
            return BridgeCommon.Try(() =>
            {
                if (string.Equals(name, "Forever", StringComparison.OrdinalIgnoreCase))
                    return BillRepeatModeDefOf.Forever;
                if (string.Equals(name, "RepeatCount", StringComparison.OrdinalIgnoreCase))
                    return BillRepeatModeDefOf.RepeatCount;
                if (string.Equals(name, "TargetCount", StringComparison.OrdinalIgnoreCase))
                    return BillRepeatModeDefOf.TargetCount;
                return null;
            }, (BillRepeatModeDef)null);
        }

        private static bool IsPlayerFaction(Thing thing)
        {
            // Faction.OfPlayer is never called: its body is OfPlayerSilentFail
            // followed by Log.Error, and Log.Error pauses the colony.
            return BridgeCommon.Try(() =>
            {
                var player = Faction.OfPlayerSilentFail;
                return player != null && thing.Faction != null && thing.Faction == player;
            }, false);
        }

        private static T SafeComp<T>(Thing thing) where T : ThingComp
        {
            try { return (thing as ThingWithComps)?.GetComp<T>(); }
            catch { return null; }
        }
    }
}
