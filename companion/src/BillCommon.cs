using System;
using System.Collections.Generic;
using System.Linq;
using RimWorld;
using Verse;
using Verse.AI;

namespace HomeBridge.BridgeTools
{
    /// <summary>
    /// Whether a bill can actually run, computed from the map rather than from
    /// the bill's own label. Shared by home/bills and by home/list_buildings
    /// under billIngredients:true.
    ///
    /// ## What it reproduces
    ///
    /// <c>WorkGiver_DoBill.TryFindBestBillIngredients</c> minus the pawn. That
    /// method walks regions out from the bench and hands the things it finds to
    /// <c>TryFindBestBillIngredientsInSet</c>, whose per-ingredient gate is:
    ///
    ///     ing.filter.Allows(def)
    ///     &amp;&amp; (ing.IsFixedIngredient || bill.ingredientFilter.Allows(def))
    ///     &amp;&amp; countRequiredOfFor(def) &lt;= availableCounts[def]
    ///
    /// with the candidate set pre-filtered by <c>IsUsableIngredient</c>, i.e.
    /// <c>bill.IsFixedOrAllowedIngredient(thing)</c> — which is the same pair of
    /// filters plus <c>recipe.fixedIngredientFilter</c>. Both filters are applied
    /// here at the THING level (the overload that also weighs hit points, quality
    /// and the special filters), which is what IsUsableIngredient does.
    ///
    /// Three details that decide the numbers:
    ///
    ///   * **Defs do not mix.** The count test above is against ONE def's total,
    ///     not the sum over every allowed def, unless
    ///     <c>recipe.allowMixingIngredients</c> is set. Five rice and five corn do
    ///     not cook a meal that wants ten of something. So `available` is the best
    ///     single def's count, and `availableTotal` is the sum, and they are
    ///     different numbers on purpose.
    ///   * **The required amount is per def.** <c>CountRequiredOfFor</c> divides
    ///     the recipe's base count by <c>IngredientValueGetter.ValuePerUnitOf</c>
    ///     — <c>ThingDef.VolumePerUnit</c> (0.1 for a smallVolume def) for the
    ///     default volume getter, nutrition for food — so ten steel and a hundred
    ///     silver can be the same requirement.
    ///   * **The radius is measured from the bench's own cell**, not its
    ///     interaction cell: the game's predicate is
    ///     <c>(t.Position - billGiver.Position).LengthHorizontalSquared &lt; radius²</c>.
    ///     A radius of 999 (<c>Bill.MaxIngredientSearchRadius</c>, and the default)
    ///     is unlimited: the region-entry condition is dropped entirely at 999, and
    ///     999 cells exceeds the diagonal of the largest map, so the distance test
    ///     cannot bite either.
    ///
    /// ## What it does not reproduce
    ///
    /// The pawn's own forbidden rules for the ITEM, which need the real worker.
    /// Reachability and reservation ARE modelled: a stack no route reaches from
    /// the bench's interaction cell is subtracted and tallied in
    /// <c>excludedUnreachable</c>, and a stack somebody has already claimed is
    /// tallied in <c>excludedReserved</c> and LEFT IN the count, because the
    /// claimant is usually fetching it for this very bill.
    ///
    /// The route question is the one place a pawn IS used, and it has to be: the
    /// scan ran <c>TraverseMode.PassDoors</c> until 2026-09-12, and
    /// <c>Verse.Region.Allows</c> ignores the door entirely in that mode, so a
    /// locked door could never strand an ingredient. It now asks
    /// <c>TraverseMode.ByPawn</c> for the bill's allowed worker when it has one
    /// and otherwise for the first spawned free colonist —
    /// <c>MapItems.TraverserFor</c> — which is the door test the hauler would
    /// actually face. A map with no colonist on it falls back to the old
    /// PassDoors call.
    ///
    /// A stack a pawn is CARRYING is despawned, so it is in neither the count nor
    /// any exclusion tally; that, and hauling in flight, is why two reads seconds
    /// apart can disagree. Every row carries the radius it counted within, so the
    /// scope is never the unexplained half.
    ///
    /// Things inside a container that is not itself spawned on the map
    /// (a caravan's inventory, a transport pod) are not counted: the scan is
    /// <c>ThingRequestGroup.HaulableEver</c>, the same group the region walk uses,
    /// which is every haulable spawned on the map including everything in a
    /// stockpile or on a shelf.
    ///
    /// ## Hazards dodged
    ///
    ///   * <c>Bill_Production.ShouldDoNow()</c> writes <c>paused</c>. Nothing here
    ///     calls it; `active` is derived from the fields.
    ///   * <c>ForbidUtility.IsForbidden(Thing, Faction)</c> calls
    ///     <c>Faction.OfPlayer</c>, whose failure path is <c>Log.Error</c> and a
    ///     paused colony. <c>CompForbiddable.Forbidden</c> is read directly.
    ///   * <c>RecipeDef.AvailableNow</c> reaches <c>Faction.OfPlayer</c> through
    ///     <c>memePrerequisitesAny</c>, <c>factionPrerequisiteTags</c> and
    ///     <c>fromIdeoBuildingPreceptOnly</c>. It is called only when a player
    ///     faction exists or when all three are absent; otherwise it reports null.
    ///   * <c>IngredientCount.FixedIngredient</c> <c>Log.Error</c>s when the
    ///     ingredient is not fixed. <c>filter.AllowedThingDefs</c> is walked instead.
    /// </summary>
    internal static class BillCommon
    {
        /// <summary>Per-ingredient defs listed in availableDefs[] before the rest
        /// become a count. A row that prints thirty meat defs is unreadable.</summary>
        internal const int MaxAvailableDefs = 5;

        /// <summary>Categories named in the filter summary before the rest become
        /// a count.</summary>
        internal const int MaxCategories = 12;

        /// <summary>Bill.MaxIngredientSearchRadius. At or above this the game
        /// drops its region-entry condition and the search is map-wide.</summary>
        internal const float UnlimitedRadius = 999f;

        // =============================================================== index

        /// <summary>One spawned haulable, flattened so a per-bill pass costs no
        /// further game reads.</summary>
        internal struct Item
        {
            internal Thing Thing;
            internal int Stack;
            internal int X;
            internal int Z;
            internal bool Forbidden;
        }

        /// <summary>
        /// One walk of the map's haulables, bucketed by def and reused by every
        /// bill on every bench in a call. Built once per tool invocation, on the
        /// main thread, inside the caller's hop.
        /// </summary>
        internal sealed class MapItems
        {
            internal readonly Dictionary<ThingDef, List<Item>> ByDef =
                new Dictionary<ThingDef, List<Item>>();
            internal int Scanned;
            internal string Error;

            /// <summary>The map the index was taken from, for the reachability
            /// test. Null means no reachability test is possible.</summary>
            internal Map Map;

            /// <summary>Every thing somebody has a reservation on, materialised
            /// once. Null = the reservation manager could not be read, which is
            /// reported as "not checked", never as "nothing reserved".</summary>
            internal HashSet<Thing> Reserved;

            /// <summary>The tick the scan was taken at. Two listings that
            /// disagree disagree at two different ticks; this is how a reader
            /// tells that apart from two different scopes.</summary>
            internal int? Tick;

            /// <summary>CanReach answers, keyed traverser -> root cell index ->
            /// target cell index. Bills on one bench share a root, so a busy
            /// larder costs one call per occupied cell, not one per stack per
            /// bill. The traverser is part of the key because two bills on one
            /// bench can name two different allowed workers, and a door one of
            /// them may open is a door the other may not.</summary>
            private readonly Dictionary<int, Dictionary<int, Dictionary<int, bool>>> _reach =
                new Dictionary<int, Dictionary<int, Dictionary<int, bool>>>();

            /// <summary>
            /// A spawned, living free colonist on this map, found once per tool
            /// invocation and used as the representative worker for any bill that
            /// names no allowed worker of its own. Null when the map holds none,
            /// which is what makes the PassDoors fallback in
            /// <see cref="CanReach"/> necessary rather than decorative.
            ///
            /// Walked by hand off <c>AllPawnsSpawned</c>: <c>MapPawns.FreeColonists</c>
            /// and <c>.FreeColonistsSpawned</c> both reach <c>Faction.OfPlayer</c>,
            /// whose failure path is <c>Log.Error</c>, which calls
            /// <c>TickManager.Pause()</c>.
            /// </summary>
            internal Pawn Colonist;

            /// <summary>Reachability calls left in this tool invocation. A read
            /// must not become the expensive thing on the frame.</summary>
            internal int ReachBudget = MaxReachChecks;

            /// <summary>True once the budget ran out: rows taken after that say
            /// reachabilityChecked:false rather than counting nothing.</summary>
            internal bool ReachBudgetSpent;

            /// <summary>
            /// The colonist whose reach this bill's ingredients are measured by:
            /// the bill's own allowed worker when it has one — a bill restricted
            /// to one pawn is fetched for by nobody else — otherwise the map's
            /// representative colonist. Null means the ByPawn question cannot be
            /// asked at all and <see cref="CanReach"/> falls back to PassDoors.
            /// </summary>
            internal Pawn TraverserFor(Bill bill)
            {
                var restricted = bill == null
                    ? null
                    : BridgeCommon.Try(() => bill.PawnRestriction, (Pawn)null);
                if (UsableTraverser(restricted))
                    return restricted;
                return UsableTraverser(Colonist) ? Colonist : null;
            }

            /// <summary><c>Reachability.CanReach</c> <c>Log.Error</c>s — and so
            /// pauses the colony — when it is handed a pawn spawned on another
            /// map, and answers a flat <c>false</c> for one that is not spawned
            /// at all. Both are screened here rather than risked.</summary>
            private bool UsableTraverser(Pawn pawn)
            {
                if (pawn == null || Map == null)
                    return false;
                if (!BridgeCommon.Try(() => pawn.Spawned, false))
                    return false;
                if (BridgeCommon.Try(() => pawn.Dead, true))
                    return false;
                return BridgeCommon.Try(() => pawn.Map, (Map)null) == Map;
            }

            /// <summary>
            /// Whether a route exists from `root` to `cell` for
            /// <paramref name="traverser"/>, or null when the question could not
            /// be asked (no map, an invalid cell, the budget spent, or the call
            /// threw). Null is never read as "unreachable".
            /// </summary>
            internal bool? CanReach(IntVec3 root, IntVec3 cell, Pawn traverser)
            {
                if (Map == null || !root.IsValid || !cell.IsValid)
                    return null;
                if (!UsableTraverser(traverser))
                    traverser = null;

                int rootIndex, cellIndex;
                try
                {
                    rootIndex = Map.cellIndices.CellToIndex(root);
                    cellIndex = Map.cellIndices.CellToIndex(cell);
                }
                catch { return null; }

                var pawnKey = traverser == null
                    ? 0
                    : BridgeCommon.Try(() => traverser.thingIDNumber, 0);
                Dictionary<int, Dictionary<int, bool>> byRoot;
                if (!_reach.TryGetValue(pawnKey, out byRoot))
                {
                    byRoot = new Dictionary<int, Dictionary<int, bool>>();
                    _reach[pawnKey] = byRoot;
                }
                Dictionary<int, bool> byCell;
                if (!byRoot.TryGetValue(rootIndex, out byCell))
                {
                    byCell = new Dictionary<int, bool>();
                    byRoot[rootIndex] = byCell;
                }
                bool cached;
                if (byCell.TryGetValue(cellIndex, out cached))
                    return cached;

                if (ReachBudget <= 0)
                {
                    ReachBudgetSpent = true;
                    return null;
                }
                ReachBudget--;

                // ByPawn is the ONLY mode that consults the doors, and the doors
                // are the whole question. Verse.Region.Allows answers
                // `return !flag` for TraverseMode.PassDoors -- a fence test, the
                // door ignored entirely -- so under PassDoors a locked door could
                // never make an ingredient unreachable, however loudly a comment
                // said it did. NoPassClosedDoors is the opposite mistake: its arm
                // is `door == null || door.FreePassage`, which refuses every
                // ordinary closed door in the colony.
                //
                // Under ByPawn the door arm runs Building_Door.CanPhysicallyPass
                // (FreePassage, or PawnCanOpen, or standing open) and then
                // IsForbiddenToPass (the door forbidden to this pawn's faction)
                // against THIS pawn -- which is exactly the hauler's question.
                // canBashDoors:false because a colonist fetching steel does not
                // bash a door; Danger.Deadly because (int)maxDanger < 3 is what
                // gates Allows' danger arm, and a stranded stack is a route fact,
                // not a nerve one.
                //
                // With no colonist to ask for, the old PassDoors call stands: it
                // is wrong about doors but it is an answer, and
                // TraverseParms.For(pawn: null) is itself a Log.Error.
                var pawn = traverser;
                var answer = pawn == null
                    ? BridgeCommon.TryN(() => Map.reachability.CanReach(
                        root, cell, PathEndMode.ClosestTouch,
                        TraverseParms.For(TraverseMode.PassDoors, Danger.Deadly, false)))
                    : BridgeCommon.TryN(() => Map.reachability.CanReach(
                        root, cell, PathEndMode.ClosestTouch,
                        TraverseParms.For(pawn, Danger.Deadly, TraverseMode.ByPawn, canBashDoors: false)));
                if (answer == null)
                    return null;
                byCell[cellIndex] = answer.Value;
                return answer.Value;
            }

            /// <summary>The first spawned, living free colonist on the map, or
            /// null. <c>Pawn.IsFreeColonist</c> is <c>IsColonist &amp;&amp;
            /// HostFaction == null</c> and reaches no faction singleton, unlike
            /// the <c>MapPawns</c> properties that would answer the same
            /// question.</summary>
            private static Pawn RepresentativeColonist(Map map)
            {
                if (map == null)
                    return null;
                // AllPawnsSpawned is IReadOnlyList<Pawn> in 1.6, not List<Pawn>.
                IReadOnlyList<Pawn> spawned;
                try { spawned = map.mapPawns.AllPawnsSpawned; }
                catch { return null; }
                if (spawned == null)
                    return null;
                for (var i = 0; i < spawned.Count; i++)
                {
                    var pawn = spawned[i];
                    if (pawn == null)
                        continue;
                    if (!BridgeCommon.Try(() => pawn.IsFreeColonist, false))
                        continue;
                    if (BridgeCommon.Try(() => pawn.Dead, true))
                        continue;
                    if (!BridgeCommon.Try(() => pawn.Spawned, false))
                        continue;
                    if (BridgeCommon.Try(() => pawn.Map, (Map)null) != map)
                        continue;
                    return pawn;
                }
                return null;
            }

            internal static MapItems Build(Map map)
            {
                var index = new MapItems();
                index.Map = map;
                index.Colonist = RepresentativeColonist(map);
                index.Reserved = ReservedThings(map);
                index.Tick = BridgeCommon.TryN(() => Find.TickManager.TicksGame);
                List<Thing> source;
                try { source = map.listerThings.ThingsInGroup(ThingRequestGroup.HaulableEver); }
                catch (Exception e)
                {
                    index.Error = "map.listerThings.ThingsInGroup(HaulableEver) threw " + e.GetType().Name
                                  + "; no ingredient counts could be taken.";
                    return index;
                }
                if (source == null)
                {
                    index.Error = "map.listerThings.ThingsInGroup(HaulableEver) returned null.";
                    return index;
                }

                for (var i = 0; i < source.Count; i++)
                {
                    var thing = source[i];
                    if (thing == null || thing.def == null)
                        continue;
                    if (!BridgeCommon.Try(() => thing.Spawned, false))
                        continue;

                    var pos = BridgeCommon.TryN(() => thing.Position);
                    if (pos == null)
                        continue;

                    var item = new Item
                    {
                        Thing = thing,
                        Stack = BridgeCommon.Try(() => thing.stackCount, 0),
                        X = pos.Value.x,
                        Z = pos.Value.z,
                        Forbidden = IsForbidden(thing)
                    };
                    if (item.Stack <= 0)
                        continue;

                    List<Item> bucket;
                    if (!index.ByDef.TryGetValue(thing.def, out bucket))
                    {
                        bucket = new List<Item>();
                        index.ByDef[thing.def] = bucket;
                    }
                    bucket.Add(item);
                    index.Scanned++;
                }

                return index;
            }

            internal List<Item> Of(ThingDef def)
            {
                List<Item> bucket;
                return def != null && ByDef.TryGetValue(def, out bucket) ? bucket : null;
            }
        }

        /// <summary>How many reachability calls one tool invocation may make.
        /// Past it every further row reports reachabilityChecked:false.</summary>
        internal const int MaxReachChecks = 4000;

        /// <summary>Everything under a standing reservation, as a set. Null when
        /// the reservation manager could not be read: "not checked", not
        /// "nothing reserved". AllReservedThings() is a read-only Select over
        /// the reservation list and touches no faction.</summary>
        private static HashSet<Thing> ReservedThings(Map map)
        {
            try
            {
                var set = new HashSet<Thing>();
                var all = map.reservationManager.AllReservedThings();
                if (all == null)
                    return null;
                foreach (var t in all)
                    if (t != null) set.Add(t);
                return set;
            }
            catch { return null; }
        }

        /// <summary>CompForbiddable directly. ForbidUtility.IsForbidden(Thing,
        /// Faction) is never used: it calls Faction.OfPlayer.</summary>
        private static bool IsForbidden(Thing thing)
        {
            try
            {
                var comp = (thing as ThingWithComps)?.GetComp<CompForbiddable>();
                return comp != null && comp.Forbidden;
            }
            catch { return false; }
        }

        // ============================================================ verdict

        /// <summary>What the ingredient pass concluded about one bill.</summary>
        internal sealed class Verdict
        {
            internal List<object> Ingredients = new List<object>();
            internal List<object> BlockedBy = new List<object>();
            internal bool CanRunNow;
            /// <summary>Every ingredient slot has enough of one usable def.</summary>
            internal bool IngredientsSatisfied;
            /// <summary>Units on the map this bill's own filter turned away.</summary>
            internal int ExcludedByFilter;
        }

        /// <summary>
        /// The whole verdict for one bill on one bench. Never throws; a read that
        /// fails becomes a blockedBy entry naming what could not be read, so an
        /// unreadable bill is never reported as a runnable one.
        /// </summary>
        internal static Verdict Judge(Thing bench, IBillGiver giver, Bill bill, MapItems items)
        {
            var verdict = new Verdict();
            if (bill == null)
            {
                verdict.BlockedBy.Add("bill unreadable");
                return verdict;
            }

            var recipe = BridgeCommon.Try(() => bill.recipe, (RecipeDef)null);
            if (recipe == null)
            {
                verdict.BlockedBy.Add("recipe unreadable");
                return verdict;
            }

            var production = bill as Bill_Production;
            var suspended = BridgeCommon.Try(() => bill.suspended, false);
            var paused = production != null && BridgeCommon.Try(() => production.paused, false);
            var finished = IsFinished(production);

            if (suspended) verdict.BlockedBy.Add("suspended");
            if (finished) verdict.BlockedBy.Add("finished");
            if (paused) verdict.BlockedBy.Add("paused");

            // "Do until you have X" with X already in store. ShouldDoNow's whole
            // TargetCount branch reduces to `CountProducts(bill) < targetCount`
            // once the pause bookkeeping it WRITES is left out, so the count is
            // taken directly. CountProducts stores nothing; CanCountProducts is
            // asked first because CountProducts opens on products[0].
            var stocked = ProductCount(production);
            if (stocked != null)
            {
                var target = BridgeCommon.TryN(() => production.targetCount);
                if (target != null && stocked.Value >= target.Value)
                    verdict.BlockedBy.Add("target already met (" + stocked.Value + "/" + target.Value + ")");
            }

            // The bench itself. IBillGiver.CurrentlyUsableForBills() is the game's
            // own answer and covers power, fuel and breakdown together; the comps
            // are then read only to say WHICH of the three it was.
            var usable = BridgeCommon.TryN(() => giver == null ? true : giver.CurrentlyUsableForBills());
            if (usable == null)
                verdict.BlockedBy.Add("bench usability unreadable");
            else if (!usable.Value)
                verdict.BlockedBy.Add(BenchBlockReason(bench));

            // RecipeDef.AvailableNow reaches Faction.OfPlayer on three fields.
            var available = RecipeAvailableNow(recipe);
            if (available == false)
                verdict.BlockedBy.Add("recipe not available (research, meme or faction requirement)");
            else if (available == null)
                verdict.BlockedBy.Add("recipe availability unreadable (no player faction)");

            // ------------------------------------------------------ ingredients
            List<IngredientCount> ingredients;
            try { ingredients = recipe.ingredients; }
            catch { ingredients = null; }

            if (ingredients == null)
            {
                verdict.BlockedBy.Add("recipe ingredient list unreadable");
                verdict.IngredientsSatisfied = false;
            }
            else if (items == null || items.Error != null)
            {
                verdict.BlockedBy.Add("ingredient scan unavailable ("
                                      + (items == null ? "no index" : items.Error) + ")");
                verdict.IngredientsSatisfied = false;
            }
            else
            {
                var radius = BridgeCommon.Try(() => bill.ingredientSearchRadius, UnlimitedRadius);
                var filter = BridgeCommon.Try(() => bill.ingredientFilter, (ThingFilter)null);
                bool allSatisfied;
                int excluded;
                List<object> missing;
                verdict.Ingredients = IngredientRows(recipe, filter, bill, bench, radius, items,
                                                     out allSatisfied, out excluded, out missing);
                verdict.IngredientsSatisfied = allSatisfied;
                verdict.ExcludedByFilter = excluded;
                verdict.BlockedBy.AddRange(missing);
                if (!allSatisfied && excluded > 0)
                    verdict.BlockedBy.Add("filter excludes " + excluded + " on map");
            }

            verdict.CanRunNow = verdict.BlockedBy.Count == 0;
            return verdict;
        }

        /// <summary>
        /// How many of the product the colony holds, for a TargetCount bill only,
        /// or null. Read through the game's own RecipeWorkerCounter so the number
        /// matches the one the bill's "N/M" label shows. It needs a bill that is
        /// attached to a bench (bill.Map), so a clone or an unadded bill answers
        /// null rather than a wrong number.
        /// </summary>
        internal static int? ProductCount(Bill_Production production)
        {
            if (production == null)
                return null;
            var isTarget = BridgeCommon.Try(
                () => production.repeatMode == BillRepeatModeDefOf.TargetCount, false);
            if (!isTarget)
                return null;
            var canCount = BridgeCommon.Try(
                () => production.recipe.WorkerCounter.CanCountProducts(production), false);
            if (!canCount)
                return null;
            return BridgeCommon.TryN(() => production.recipe.WorkerCounter.CountProducts(production));
        }

        /// <summary>
        /// The single def a TargetCount bill counts toward, or null. A recipe with
        /// no product, or several, has no one number to widen, and guessing one
        /// would be worse than the null.
        /// </summary>
        private static ThingDef ProductDef(Bill_Production production)
        {
            try
            {
                if (production == null || production.recipe == null)
                    return null;
                var products = production.recipe.products;
                if (products == null || products.Count != 1 || products[0] == null)
                    return null;
                return products[0].thingDef;
            }
            catch { return null; }
        }

        private static string ProductDefName(Bill_Production production)
        {
            var def = ProductDef(production);
            return def == null ? null : BridgeCommon.SafeString(() => def.defName);
        }

        /// <summary>The product sitting in any storage, bill zone or not.</summary>
        private static int? ProductCountStored(Bill_Production production)
        {
            return ProductTotal(production, storedOnly: true);
        }

        /// <summary>Every spawned stack of the product on the map.</summary>
        private static int? ProductCountOnMap(Bill_Production production)
        {
            return ProductTotal(production, storedOnly: false);
        }

        private static int? ProductTotal(Bill_Production production, bool storedOnly)
        {
            try
            {
                var def = ProductDef(production);
                if (def == null)
                    return null;
                var map = production.Map;
                if (map == null || map.listerThings == null)
                    return null;
                var things = map.listerThings.ThingsOfDef(def);
                if (things == null)
                    return null;
                var total = 0;
                for (var i = 0; i < things.Count; i++)
                {
                    var t = things[i];
                    if (t == null || !t.Spawned)
                        continue;
                    if (storedOnly && !t.IsInAnyStorage())
                        continue;
                    total += Math.Max(1, t.stackCount);
                }
                return total;
            }
            catch { return null; }
        }

        /// <summary>Which of power, fuel or breakdown stopped the bench. Only ever
        /// reached when the game already said the bench is unusable.</summary>
        private static string BenchBlockReason(Thing bench)
        {
            var power = SafeComp<CompPowerTrader>(bench);
            if (power != null && !BridgeCommon.Try(() => power.PowerOn, false))
                return "bench unpowered";
            var broken = SafeComp<CompBreakdownable>(bench);
            if (broken != null && BridgeCommon.Try(() => broken.BrokenDown, false))
                return "bench broken down";
            var fuel = SafeComp<CompRefuelable>(bench);
            if (fuel != null && !BridgeCommon.Try(() => fuel.HasFuel, true))
                return "bench out of fuel";
            return "bench not usable for bills";
        }

        private static T SafeComp<T>(Thing thing) where T : ThingComp
        {
            try { return (thing as ThingWithComps)?.GetComp<T>(); }
            catch { return null; }
        }

        // ========================================================= one ingredient

        /// <summary>
        /// One row per ingredient slot, for a bill that exists OR for a recipe
        /// being previewed. `bill` may be null: CountRequiredOfFor falls back to
        /// the ingredient's own base count, which is what the default
        /// RecipeWorker.GetIngredientCount returns anyway.
        /// </summary>
        internal static List<object> IngredientRows(
            RecipeDef recipe, ThingFilter billFilter, Bill bill, Thing bench,
            float radius, MapItems items,
            out bool allSatisfied, out int excludedByFilter, out List<object> missing)
        {
            var rows = new List<object>();
            allSatisfied = true;
            excludedByFilter = 0;
            missing = new List<object>();

            List<IngredientCount> ingredients;
            try { ingredients = recipe.ingredients; }
            catch { ingredients = null; }
            if (ingredients == null)
            {
                allSatisfied = false;
                missing.Add("recipe ingredient list unreadable");
                return rows;
            }

            var benchCell = BridgeCommon.TryN(() => bench == null ? IntVec3.Invalid : bench.Position);
            // The radius is measured from the bench's own cell; a route is
            // measured from the cell a worker stands in, which is the game's own
            // distinction. A bench with no interaction cell falls back to its own.
            var reachRoot = ReachRoot(bench, benchCell);
            // Whose reach. One lookup for every slot on this bill; see
            // MapItems.TraverserFor.
            var traverser = items == null ? null : items.TraverserFor(bill);
            for (var i = 0; i < ingredients.Count; i++)
            {
                var row = IngredientRow(recipe, ingredients[i], billFilter, bill, items, radius,
                                        benchCell, reachRoot, traverser);
                rows.Add(row);
                excludedByFilter += (int)BridgeCommon.Num(row, "excludedByFilter");
                if (!BridgeCommon.Bool(row, "satisfied"))
                {
                    allSatisfied = false;
                    missing.Add("missing " + row["label"] + " ("
                                + row["available"] + "/" + row["needed"] + ")");
                    var stranded = (int)BridgeCommon.Num(row, "excludedUnreachable");
                    if (stranded > 0)
                        missing.Add(stranded + " " + row["label"] + " on the map have NO ROUTE from this "
                                    + "bench's interaction cell -- a door, a wall or an unbuilt bridge, not "
                                    + "a shortage. Open the way or move the bench.");
                }
            }
            return rows;
        }

        /// <summary>Where a worker would stand: Thing.InteractionCell when the
        /// def has one and it is in bounds, else the bench's own cell.</summary>
        private static IntVec3 ReachRoot(Thing bench, IntVec3? benchCell)
        {
            var fallback = benchCell == null ? IntVec3.Invalid : benchCell.Value;
            if (bench == null)
                return fallback;
            var cell = BridgeCommon.TryN(() => bench.InteractionCell);
            if (cell == null || !cell.Value.IsValid)
                return fallback;
            var map = BridgeCommon.Try(() => bench.Map, (Map)null);
            if (map != null && !BridgeCommon.Try(() => cell.Value.InBounds(map), false))
                return fallback;
            return cell.Value;
        }

        private static Dictionary<string, object> IngredientRow(
            RecipeDef recipe, IngredientCount ing, ThingFilter billFilter, Bill bill,
            MapItems items, float radius, IntVec3? benchCell, IntVec3 reachRoot,
            Pawn traverser)
        {
            var row = new Dictionary<string, object>(StringComparer.Ordinal);
            if (ing == null)
            {
                row["summary"] = null;
                row["label"] = "?";
                row["needed"] = 0;
                row["available"] = 0;
                row["shortfall"] = 0;
                row["satisfied"] = false;
                row["availableTotal"] = 0;
                row["availableDefs"] = new List<object>();
                row["availableDefsNotListed"] = 0;
                row["filterAllowsAny"] = false;
                row["excludedByFilter"] = 0;
                row["excludedForbidden"] = 0;
                row["excludedOutOfRadius"] = 0;
                row["excludedNotFresh"] = 0;
                row["excludedUnreachable"] = 0;
                row["excludedReserved"] = 0;
                row["reachabilityChecked"] = false;
                row["reservationChecked"] = false;
                row["searchRadius"] = null;
                row["radiusUnlimited"] = false;
                row["isFixedIngredient"] = false;
                row["unreadable"] = true;
                return row;
            }

            var isFixed = BridgeCommon.Try(() => ing.IsFixedIngredient, false);
            var mixing = BridgeCommon.Try(() => recipe.allowMixingIngredients, false);
            var wholeStacks = BridgeCommon.Try(() => recipe.ignoreIngredientCountTakeEntireStacks, false);
            var unlimited = radius >= UnlimitedRadius || benchCell == null || !benchCell.Value.IsValid;
            var radiusSq = (double)radius * radius;

            List<ThingDef> allowedDefs;
            try { allowedDefs = ing.filter.AllowedThingDefs.ToList(); }
            catch { allowedDefs = null; }
            if (allowedDefs == null)
                allowedDefs = new List<ThingDef>();

            var haveByDef = new Dictionary<ThingDef, int>();
            var requiredByDef = new Dictionary<ThingDef, int>();
            var filterAllowsAny = false;
            var excludedByFilter = 0;
            var excludedForbidden = 0;
            var excludedOutOfRadius = 0;
            var excludedNotFresh = 0;
            var excludedUnreachable = 0;
            var excludedReserved = 0;
            var reachAsked = 0;
            var reachAnswered = 0;

            foreach (var def in allowedDefs)
            {
                if (def == null)
                    continue;

                // The def-level half of the gate, exactly as the game states it.
                var defPasses = isFixed || (FixedAllows(recipe, def) && FilterAllows(billFilter, def));
                if (defPasses)
                    filterAllowsAny = true;

                requiredByDef[def] = RequiredOf(ing, recipe, bill, def);

                var bucket = items.Of(def);
                if (bucket == null)
                    continue;

                var have = 0;
                for (var i = 0; i < bucket.Count; i++)
                {
                    var item = bucket[i];
                    if (!BridgeCommon.Try(() => ing.filter.Allows(item.Thing), false))
                        continue;

                    // CAUSE ORDER, and it is not cosmetic. Until 2026-09-07 the
                    // FORBIDDEN test ran first, so a thing this bill could never
                    // use was reported as "on the map but forbidden" and the
                    // advice was "unforbid them". Live on 2026-09-07 a butcher
                    // bill said exactly that about six corpses that were all
                    // SKELETONS: ButcherCorpseFlesh's fixedIngredientFilter
                    // carries specialFiltersToDisallow AllowRotten, whose worker
                    // is `CompRottable.Stage != Fresh`, so rotting AND dessicated
                    // corpses are refused by the game itself. Unforbidding them
                    // would have yielded nothing.
                    //
                    // So: can this bill EVER use it (filter) -> is it in REACH
                    // (radius) -> is it FORBIDDEN. Only the last one is fixed by
                    // unforbidding, and it is now the only one that says so.
                    if (!isFixed && !(FixedAllows(recipe, item.Thing) && FilterAllows(billFilter, item.Thing)))
                    {
                        // The insect-meat case: the recipe would take it, this
                        // bill's own filter will not. And the skeleton case: the
                        // def is allowed and this particular body is not, which
                        // is always a rot stage, so it is counted separately --
                        // "6 corpses, all rotten or dessicated" is a different
                        // sentence from "your filter excludes insect meat".
                        excludedByFilter += item.Stack;
                        if (FilterAllows(billFilter, item.Thing.def)
                            && FixedAllows(recipe, item.Thing.def)
                            && NotFresh(item.Thing))
                            excludedNotFresh += item.Stack;
                        continue;
                    }

                    if (!unlimited)
                    {
                        double dx = item.X - benchCell.Value.x;
                        double dz = item.Z - benchCell.Value.z;
                        if (dx * dx + dz * dz >= radiusSq)
                        {
                            excludedOutOfRadius += item.Stack;
                            continue;
                        }
                    }

                    if (item.Forbidden) { excludedForbidden += item.Stack; continue; }

                    // A route a COLONIST has, from where a worker would stand.
                    // Only a definite NO subtracts; an unasked or failed question
                    // leaves the stack counted and lowers reachabilityChecked
                    // instead.
                    reachAsked++;
                    var reachable = items.CanReach(reachRoot, new IntVec3(item.X, 0, item.Z), traverser);
                    if (reachable != null)
                    {
                        reachAnswered++;
                        if (!reachable.Value) { excludedUnreachable += item.Stack; continue; }
                    }

                    // Reserved stacks stay IN the count: the claimant is usually
                    // fetching this very stack for this very bill. The tally is
                    // the "somebody got there first" tell, not a blocker.
                    if (items.Reserved != null && items.Reserved.Contains(item.Thing))
                        excludedReserved += item.Stack;

                    have += item.Stack;
                }

                if (have > 0)
                    haveByDef[def] = have;
            }

            // ------------------------------------------------- reference def
            // Defs do not mix, so "available" is one def's count. The reference
            // is the def closest to satisfying the slot; with nothing on the map
            // it is the def the game itself would name (CountFor's rule).
            ThingDef best = null;
            var bestSatisfied = false;
            long bestMargin = long.MinValue;
            foreach (var pair in haveByDef)
            {
                var required = requiredByDef.ContainsKey(pair.Key) ? requiredByDef[pair.Key] : 0;
                var satisfiedHere = pair.Value >= required;
                long margin = (long)pair.Value - required;
                if (best == null
                    || (satisfiedHere && !bestSatisfied)
                    || (satisfiedHere == bestSatisfied && margin > bestMargin))
                {
                    best = pair.Key;
                    bestSatisfied = satisfiedHere;
                    bestMargin = margin;
                }
            }
            // Whether `best` names something actually on the map decides how the
            // slot may be NAMED below.
            var fromMap = best != null;
            if (best == null)
                best = DisplayDef(recipe, isFixed ? null : billFilter, allowedDefs);
            // Nothing on the map matched and the slot takes many defs: naming one
            // of them reads as a specific missing item the recipe never asked for
            // ("human corpse" for a bill whose filter excludes human corpses).
            var generic = !fromMap && allowedDefs.Count(d => d != null) > 1;

            var needed = best != null && requiredByDef.ContainsKey(best)
                ? requiredByDef[best]
                : (int)Math.Ceiling(BridgeCommon.Try(() => (double)ing.GetBaseCount(), 0.0));
            var availableTotal = haveByDef.Values.Sum();
            var available = best != null && haveByDef.ContainsKey(best) ? haveByDef[best] : 0;

            if (mixing && best != null)
            {
                // With mixing on the game spends VALUE, not units, so every
                // allowed def contributes. Re-expressed in the reference def's
                // own units so `needed` and `available` stay comparable.
                var perUnit = ValuePerUnit(recipe, best);
                if (perUnit > 0.0001)
                {
                    var value = haveByDef.Sum(p => p.Value * ValuePerUnit(recipe, p.Key));
                    var inRefUnits = (int)Math.Floor(value / perUnit);
                    if (inRefUnits > available)
                        available = inRefUnits;
                }
            }

            // "Take the whole stack" recipes (butchering, smelting) need one stack
            // of anything, whatever the count says.
            if (wholeStacks && availableTotal > 0 && available < needed)
                available = needed;

            var shortfall = Math.Max(0, needed - available);

            var listed = haveByDef
                .OrderByDescending(p => p.Value)
                .Take(MaxAvailableDefs)
                .Select(p => (object)new Dictionary<string, object>
                {
                    { "defName", BridgeCommon.SafeString(() => p.Key.defName) },
                    { "label", BridgeCommon.SafeString(() => p.Key.label) },
                    { "count", p.Value },
                    { "needed", requiredByDef.ContainsKey(p.Key) ? requiredByDef[p.Key] : 0 }
                })
                .ToList();

            row["summary"] = BridgeCommon.SafeString(() => ing.SummaryFor(recipe));
            row["label"] = generic ? GenericLabel(ing, best) : IngredientLabel(ing, best);
            // True = `label` is the slot's own noun, not a def anyone counted.
            row["generic"] = generic;
            row["filterSummary"] = BridgeCommon.SafeString(() => ing.filter.Summary);
            row["representativeDefName"] = best == null ? null : BridgeCommon.SafeString(() => best.defName);
            row["needed"] = needed;
            row["available"] = available;
            row["shortfall"] = shortfall;
            // shortfall == max(0, needed - available) by construction, and
            // `satisfied` is read off it so the two can never disagree.
            row["satisfied"] = shortfall == 0 && filterAllowsAny;
            row["availableTotal"] = availableTotal;
            row["availableDefs"] = listed;
            row["availableDefsNotListed"] = Math.Max(0, haveByDef.Count - listed.Count);
            row["filterAllowsAny"] = filterAllowsAny;
            row["excludedByFilter"] = excludedByFilter;
            row["excludedForbidden"] = excludedForbidden;
            row["excludedOutOfRadius"] = excludedOutOfRadius;
            // Of excludedByFilter, the units refused for their ROT STAGE alone.
            row["excludedNotFresh"] = excludedNotFresh;
            // Subtracted from available: no route from the bench's interaction cell.
            row["excludedUnreachable"] = excludedUnreachable;
            // NOT subtracted: already claimed, usually for this bill.
            row["excludedReserved"] = excludedReserved;
            // False = the question was not asked of every candidate stack, so 0
            // unreachable means "not known", not "all reachable".
            row["reachabilityChecked"] = reachAsked == 0 || reachAnswered == reachAsked;
            row["reservationChecked"] = items.Reserved != null;
            // The scope every count on this row was taken in, so a number can be
            // read without knowing the bill's radius.
            row["searchRadius"] = unlimited ? (object)null : (int)radius;
            row["radiusUnlimited"] = unlimited;
            row["isFixedIngredient"] = isFixed;
            row["mixingAllowed"] = mixing;
            row["wholeStacks"] = wholeStacks;
            return row;
        }

        /// <summary>The noun a blockedBy line names: the reference def's label
        /// when there is one, otherwise the filter's own summary.</summary>
        private static string IngredientLabel(IngredientCount ing, ThingDef best)
        {
            if (best != null)
            {
                var label = BridgeCommon.SafeString(() => best.label);
                if (!string.IsNullOrEmpty(label))
                    return label;
            }
            return BridgeCommon.SafeString(() => ing.filter.Summary) ?? "ingredient";
        }

        /// <summary>The noun for a slot nothing on the map matched: the
        /// ingredient filter's own summary ("corpses"), never one representative
        /// def ("human corpse") the bill may not even accept.</summary>
        private static string GenericLabel(IngredientCount ing, ThingDef best)
        {
            var summary = BridgeCommon.SafeString(() => ing.filter.Summary);
            return string.IsNullOrEmpty(summary) ? IngredientLabel(ing, best) : summary;
        }

        /// <summary>The def the game names for an ingredient nothing satisfies:
        /// IngredientCount.CountFor's own rule — the first allowed def the recipe's
        /// fixed filter admits, preferring one that is not smallVolume — narrowed
        /// to what THIS bill's filter would take, since a def the bill turns away
        /// is not what it is short of.</summary>
        private static ThingDef DisplayDef(RecipeDef recipe, ThingFilter billFilter, List<ThingDef> allowedDefs)
        {
            ThingDef fallback = null;
            ThingDef outsideBill = null;
            foreach (var def in allowedDefs)
            {
                if (def == null || !FixedAllows(recipe, def))
                    continue;
                if (!FilterAllows(billFilter, def))
                {
                    if (outsideBill == null)
                        outsideBill = def;
                    continue;
                }
                if (!BridgeCommon.Try(() => def.smallVolume, false))
                    return def;
                if (fallback == null)
                    fallback = def;
            }
            return fallback ?? outsideBill ?? allowedDefs.FirstOrDefault(d => d != null);
        }

        private static int RequiredOf(IngredientCount ing, RecipeDef recipe, Bill bill, ThingDef def)
        {
            // CountRequiredOfFor = ceil(recipe.Worker.GetIngredientCount(ing, bill)
            //                           / IngredientValueGetter.ValuePerUnitOf(def)).
            var count = BridgeCommon.TryN(() => ing.CountRequiredOfFor(def, recipe, bill));
            if (count != null)
                return count.Value < 0 ? 0 : count.Value;
            return (int)Math.Ceiling(BridgeCommon.Try(() => (double)ing.GetBaseCount(), 0.0));
        }

        private static double ValuePerUnit(RecipeDef recipe, ThingDef def)
        {
            return BridgeCommon.Try(() => (double)recipe.IngredientValueGetter.ValuePerUnitOf(def), 1.0);
        }

        /// <summary>
        /// True when this thing has rotted at all -- RimWorld's own
        /// SpecialThingFilterWorker_Rotten test, `CompRottable.Stage != Fresh`,
        /// which covers Rotting AND Dessicated (a skeleton). A def whose rot
        /// DESTROYS it (rotDestroys) never matches, exactly as the game's
        /// worker does not match it.
        /// </summary>
        private static bool NotFresh(Thing thing)
        {
            return BridgeCommon.Try(() =>
            {
                var rot = thing == null ? null : thing.TryGetComp<CompRottable>();
                if (rot == null || rot.PropsRot == null || rot.PropsRot.rotDestroys)
                    return false;
                return rot.Stage != RotStage.Fresh;
            }, false);
        }

        private static bool FixedAllows(RecipeDef recipe, ThingDef def)
        {
            return BridgeCommon.Try(() => recipe.fixedIngredientFilter == null
                                          || recipe.fixedIngredientFilter.Allows(def), true);
        }

        private static bool FixedAllows(RecipeDef recipe, Thing thing)
        {
            return BridgeCommon.Try(() => recipe.fixedIngredientFilter == null
                                          || recipe.fixedIngredientFilter.Allows(thing), true);
        }

        private static bool FilterAllows(ThingFilter filter, ThingDef def)
        {
            return BridgeCommon.Try(() => filter == null || filter.Allows(def), true);
        }

        private static bool FilterAllows(ThingFilter filter, Thing thing)
        {
            return BridgeCommon.Try(() => filter == null || filter.Allows(thing), true);
        }

        // ========================================================= filter block

        /// <summary>
        /// What this bill's own ingredient filter allows, measured against what
        /// the recipe would allow. `allowsHumanMeat` and `allowsInsectMeat` are
        /// NULL when the recipe's fixed filter could never admit that meat — the
        /// question does not apply to a steel bill — and a bool when it could.
        /// </summary>
        internal static Dictionary<string, object> FilterBlock(Bill bill)
        {
            if (bill == null)
                return FilterBlock(null, null);
            return FilterBlock(BridgeCommon.Try(() => bill.recipe, (RecipeDef)null),
                               BridgeCommon.Try(() => bill.ingredientFilter, (ThingFilter)null));
        }

        /// <summary>The same block for a recipe being previewed, whose filter is
        /// whatever a new bill would start with.</summary>
        internal static Dictionary<string, object> FilterBlock(RecipeDef recipe, ThingFilter filter)
        {
            var block = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "allowedDefCount", null },
                { "allowedDefNames", new List<object>() },
                { "allowsHumanMeat", null },
                { "allowsInsectMeat", null },
                { "allowsHumanCorpses", null },
                { "allowsInsectCorpses", null },
                { "categoriesFullyAllowed", new List<object>() },
                { "categoriesPartlyAllowed", new List<object>() },
                { "categoriesNotListed", 0 }
            };
            block["allowedDefCount"] = filter == null ? null : BridgeCommon.TryN(() => filter.AllowedDefCount);
            if (recipe == null)
                return block;

            // Candidates: every def any ingredient slot could take, narrowed by
            // the recipe's own fixed filter. This is the population the bill's
            // filter is a subset of.
            var candidates = new HashSet<ThingDef>();
            List<IngredientCount> ingredients;
            try { ingredients = recipe.ingredients; }
            catch { ingredients = null; }
            if (ingredients != null)
            {
                foreach (var ing in ingredients)
                {
                    if (ing == null)
                        continue;
                    List<ThingDef> defs;
                    try { defs = ing.filter.AllowedThingDefs.ToList(); }
                    catch { continue; }
                    foreach (var def in defs)
                        if (def != null && FixedAllows(recipe, def))
                            candidates.Add(def);
                }
            }
            if (candidates.Count == 0)
                return block;

            var humanTotal = 0; var humanAllowed = 0;
            var insectTotal = 0; var insectAllowed = 0;
            var humanCorpseTotal = 0; var humanCorpseAllowed = 0;
            var insectCorpseTotal = 0; var insectCorpseAllowed = 0;
            var byCategory = new Dictionary<ThingCategoryDef, int[]>();

            foreach (var def in candidates)
            {
                var allowed = FilterAllows(filter, def);

                if (IsHumanMeat(def)) { humanTotal++; if (allowed) humanAllowed++; }
                if (IsInsectMeat(def)) { insectTotal++; if (allowed) insectAllowed++; }
                if (IsHumanCorpse(def)) { humanCorpseTotal++; if (allowed) humanCorpseAllowed++; }
                if (IsInsectCorpse(def)) { insectCorpseTotal++; if (allowed) insectCorpseAllowed++; }

                List<ThingCategoryDef> cats;
                try { cats = def.thingCategories; }
                catch { cats = null; }
                if (cats == null)
                    continue;
                foreach (var cat in cats)
                {
                    if (cat == null)
                        continue;
                    int[] tally;
                    if (!byCategory.TryGetValue(cat, out tally))
                    {
                        tally = new int[2];
                        byCategory[cat] = tally;
                    }
                    tally[0]++;
                    if (allowed) tally[1]++;
                }
            }

            block["allowedDefNames"] = candidates
                .Where(def => FilterAllows(filter, def))
                .Select(def => BridgeCommon.SafeString(() => def.defName) ?? "?")
                .OrderBy(name => name, StringComparer.Ordinal)
                .Select(name => (object)name)
                .ToList();

            if (humanTotal > 0) block["allowsHumanMeat"] = humanAllowed > 0;
            if (insectTotal > 0) block["allowsInsectMeat"] = insectAllowed > 0;
            if (humanCorpseTotal > 0) block["allowsHumanCorpses"] = humanCorpseAllowed > 0;
            if (insectCorpseTotal > 0) block["allowsInsectCorpses"] = insectCorpseAllowed > 0;

            var full = new List<object>();
            var partly = new List<object>();
            foreach (var pair in byCategory.OrderByDescending(p => p.Value[0]))
            {
                var name = BridgeCommon.SafeString(() => pair.Key.label)
                           ?? BridgeCommon.SafeString(() => pair.Key.defName) ?? "?";
                if (pair.Value[1] == pair.Value[0]) full.Add(name);
                else if (pair.Value[1] > 0)
                    partly.Add(name + " (" + pair.Value[1] + "/" + pair.Value[0] + ")");
            }

            var notListed = Math.Max(0, full.Count - MaxCategories) + Math.Max(0, partly.Count - MaxCategories);
            block["categoriesFullyAllowed"] = full.Take(MaxCategories).ToList();
            block["categoriesPartlyAllowed"] = partly.Take(MaxCategories).ToList();
            block["categoriesNotListed"] = notListed;
            return block;
        }

        /// <summary>ThingDef.IsMeat is a category test; the source creature's
        /// flesh type is what separates the three kinds.</summary>
        private static bool IsInsectMeat(ThingDef def)
        {
            return BridgeCommon.Try(() =>
                def.IsMeat
                && def.ingestible != null
                && def.ingestible.sourceDef != null
                && def.ingestible.sourceDef.race != null
                && def.ingestible.sourceDef.race.FleshType == FleshTypeDefOf.Insectoid, false);
        }

        /// <summary>A corpse def whose race is humanlike / insectoid. Category
        /// ancestry, because a corpse def carries no race pointer.</summary>
        private static bool IsHumanCorpse(ThingDef def)
        {
            return BridgeCommon.Try(() => def.IsCorpse, false) && InCategory(def, "CorpsesHumanlike");
        }

        private static bool IsInsectCorpse(ThingDef def)
        {
            return BridgeCommon.Try(() => def.IsCorpse, false) && InCategory(def, "CorpsesInsect");
        }

        /// <summary>Category membership, ancestors included.</summary>
        private static bool InCategory(ThingDef def, string categoryDefName)
        {
            return BridgeCommon.Try(() =>
            {
                var cats = def.thingCategories;
                if (cats == null)
                    return false;
                foreach (var cat in cats)
                    for (var c = cat; c != null; c = c.parent)
                        if (string.Equals(c.defName, categoryDefName, StringComparison.Ordinal))
                            return true;
                return false;
            }, false);
        }

        private static bool IsHumanMeat(ThingDef def)
        {
            return BridgeCommon.Try(() =>
                def.IsMeat
                && def.ingestible != null
                && def.ingestible.sourceDef != null
                && def.ingestible.sourceDef.race != null
                && def.ingestible.sourceDef.race.Humanlike, false);
        }

        // ========================================================= config block

        /// <summary>
        /// Every field the bill-config dialog writes, read off the bill. A field
        /// that only exists on Bill_Production is null on any other bill kind
        /// rather than absent.
        /// </summary>
        internal static Dictionary<string, object> ConfigBlock(Bill bill)
        {
            var production = bill as Bill_Production;
            var skill = BridgeCommon.TryN(() => bill.allowedSkillRange);
            var hp = production == null ? null : BridgeCommon.TryN(() => production.hpRange);
            var quality = production == null ? null : BridgeCommon.TryN(() => production.qualityRange);
            var restriction = BridgeCommon.Try(() => bill.PawnRestriction, (Pawn)null);

            return new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "repeatMode", production == null ? null
                    : BridgeCommon.SafeString(() => production.repeatMode == null ? null : production.repeatMode.defName) },
                { "repeatCount", production == null ? null : BridgeCommon.TryN(() => production.repeatCount) },
                { "targetCount", production == null ? null : BridgeCommon.TryN(() => production.targetCount) },
                // The "N/M" the bill's own label draws. NULL for any bill that is
                // not TargetCount, and for one the counter cannot count.
                { "productCount", ProductCount(production) },
                // productCount is CountProducts: the bill's own counted storage,
                // which excludes carried, floor-lying and out-of-zone stock. On
                // their own the reader cannot tell "we have none" from "none is
                // where the bill looks", so both wider counts go beside it.
                { "productDefName", ProductDefName(production) },
                { "productCountStored", ProductCountStored(production) },
                { "productCountOnMap", ProductCountOnMap(production) },
                { "pauseWhenSatisfied", production == null ? null : (object)BridgeCommon.Try(() => production.pauseWhenSatisfied, false) },
                { "unpauseWhenYouHave", production == null ? null : BridgeCommon.TryN(() => production.unpauseWhenYouHave) },
                { "ingredientSearchRadius", BridgeCommon.TryN(() => bill.ingredientSearchRadius) },
                { "ingredientSearchRadiusUnlimited",
                    BridgeCommon.Try(() => bill.ingredientSearchRadius >= UnlimitedRadius, false) },
                { "skillRange", skill == null ? null : new Dictionary<string, object>
                    { { "min", skill.Value.min }, { "max", skill.Value.max } } },
                { "pawnRestriction", restriction == null ? null : BridgeCommon.SafeString(() => restriction.LabelShortCap) },
                { "slavesOnly", BridgeCommon.Try(() => bill.SlavesOnly, false) },
                { "mechsOnly", BridgeCommon.Try(() => bill.MechsOnly, false) },
                { "nonMechsOnly", BridgeCommon.Try(() => bill.NonMechsOnly, false) },
                { "storeMode", StoreModeName(bill) },
                { "storeZone", StoreZoneName(bill) },
                { "hpRange", hp == null ? null : new Dictionary<string, object>
                    { { "min", hp.Value.min }, { "max", hp.Value.max } } },
                { "qualityRange", quality == null ? null : new Dictionary<string, object>
                    { { "min", quality.Value.min.ToString() }, { "max", quality.Value.max.ToString() } } },
                { "limitToAllowedStuff", production == null ? null : (object)BridgeCommon.Try(() => production.limitToAllowedStuff, false) },
                { "includeEquipped", production == null ? null : (object)BridgeCommon.Try(() => production.includeEquipped, false) },
                { "includeTainted", production == null ? null : (object)BridgeCommon.Try(() => production.includeTainted, false) }
            };
        }

        internal static string StoreModeName(Bill bill)
        {
            return BridgeCommon.SafeString(() =>
            {
                var mode = bill.GetStoreMode();
                return mode == null ? null : mode.defName;
            });
        }

        internal static string StoreZoneName(Bill bill)
        {
            return BridgeCommon.SafeString(() =>
            {
                var group = bill.GetSlotGroup();
                if (group == null)
                    return null;
                var slot = group as SlotGroup;
                if (slot != null && slot.parent is Zone_Stockpile)
                    return ((Zone_Stockpile)slot.parent).label;
                return SlotGroup.GetGroupLabel(group);
            });
        }

        // ============================================================ small reads

        /// <summary>A "Do X times" bill whose remaining count has reached zero.
        /// Derived from the fields; ShouldDoNow() would write `paused`.</summary>
        internal static bool IsFinished(Bill_Production production)
        {
            if (production == null)
                return false;
            return BridgeCommon.Try(() =>
                production.repeatMode == BillRepeatModeDefOf.RepeatCount
                && production.repeatCount <= 0, false);
        }

        internal static bool IsActive(Bill bill)
        {
            var production = bill as Bill_Production;
            return !BridgeCommon.Try(() => bill.suspended, true)
                   && !(production != null && BridgeCommon.Try(() => production.paused, false))
                   && !IsFinished(production);
        }

        internal static string Label(Bill bill)
        {
            return BridgeCommon.SafeString(() => bill.LabelCap)
                   ?? BridgeCommon.SafeString(() => bill.Label)
                   ?? BridgeCommon.SafeString(() => bill.recipe == null ? null : bill.recipe.defName);
        }

        internal static string RepeatInfo(Bill_Production production)
        {
            return production == null ? null : BridgeCommon.SafeString(() => production.RepeatInfoText);
        }

        /// <summary>
        /// RecipeDef.AvailableNow, or null when asking would call
        /// Faction.OfPlayer with no player faction to answer. Three fields reach
        /// it: memePrerequisitesAny, factionPrerequisiteTags and
        /// fromIdeoBuildingPreceptOnly. With a player faction present the getter
        /// is safe; without one it is only safe when all three are absent.
        /// </summary>
        internal static bool? RecipeAvailableNow(RecipeDef recipe)
        {
            if (recipe == null)
                return null;

            var hasPlayer = BridgeCommon.Try(() => Faction.OfPlayerSilentFail != null, false);
            if (!hasPlayer)
            {
                var reachesFaction = BridgeCommon.Try(() =>
                    recipe.memePrerequisitesAny != null
                    || recipe.factionPrerequisiteTags != null
                    || recipe.fromIdeoBuildingPreceptOnly, true);
                if (reachesFaction)
                    return null;
            }

            return BridgeCommon.TryN(() => recipe.AvailableNow);
        }

        /// <summary>RecipeDef.AvailableOnNow(thing) — the second half of the Bills
        /// tab's own filter. Its default worker returns true; a surgery worker
        /// looks at the pawn.</summary>
        internal static bool? RecipeAvailableOnNow(RecipeDef recipe, Thing bench)
        {
            if (recipe == null || bench == null)
                return null;
            return BridgeCommon.TryN(() => recipe.AvailableOnNow(bench, null));
        }
    }
}
