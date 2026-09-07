using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using RimWorld;
using RimBridgeServer.Sdk;
using Verse;

namespace HomeBridge.BridgeTools
{
    /// <summary>
    /// home/list_things — an item CENSUS of the whole map, aggregated by kind,
    /// without reading a single cell, and knowing WHOSE each item is.
    ///
    /// ## Why this exists (M, 2026-08-31)
    ///
    ///     "speaking of, items - are we getting item lists tile by tile or are
    ///      we actually pulling a list? like for searching. i know its tile by
    ///      tile for the map right now"
    ///
    /// Tile by tile, and she drew exactly the right line. The MAP has to read
    /// cells: it is drawing a grid, and a grid is a spatial question. A SEARCH
    /// is not. "How much steel do we have, and where" was being answered by
    /// pulling every cell of a rectangle -- terrain, roof, designations, zone
    /// ids and all -- and then throwing all of it away except `things[]`.
    ///
    /// RimWorld already keeps the index: `map.listerThings`. Asking it is
    /// O(things of that kind) instead of O(cells), and the reply is a census
    /// rather than a haystack.
    ///
    /// ## The ownership bug (M, 2026-09-01) — and what was actually true
    ///
    ///     "whoa, big bug we will have to fix. they think 'we' have thousands of
    ///      silver. we don't but they might be reading it from the list of TOTAL
    ///      items - either the traders' or hidden caravan stuff."
    ///
    /// She was right that the number was not the colony's, and the first version
    /// of this tool had no faction, no carrier and no holder field at all. The
    /// mechanism, though, is worth stating exactly, because the obvious guess is
    /// wrong in both directions:
    ///
    ///   * `map.listerThings` is populated ONLY from `Thing.SpawnSetup`
    ///     (IL-verified against Assembly-CSharp 1.6.9676.17735:
    ///     `SpawnSetup -> Map.listerThings -> ListerThings.Add`). It holds
    ///     SPAWNED things and nothing else.
    ///   * A visiting trade caravan's stock — including everything on the pack
    ///     animals — lives in `Pawn.inventory.innerContainer`, which is NOT
    ///     spawned. Verified against the save `The Lamplighters.rws`: the pack
    ///     muffalo of Faction_2 carried 863 Silver, 180 Pemmican, 72 Plasteel,
    ///     50 Neutroamine, 49 Beer and 38 Gold, none of it in the map's own
    ///     `things` list. So the old census never saw trader stock at all.
    ///   * What it DID over-count is spawned things that are not ours: loot
    ///     sitting in still-FOGGED ancient ruins (the same save's colony had
    ///     16 Luciferium and a cooking neurotrainer behind unopened rock),
    ///     anything lying outside the home area, and anything belonging to
    ///     another faction.
    ///
    /// So this version does two things the old one did not:
    ///
    ///   1. It walks the holders as well as the map — pawn inventories and carry
    ///      trackers, corpse inventories, and container things (ancient caskets,
    ///      crates, minified things) — so trader stock is VISIBLE and LABELLED
    ///      rather than merely absent.
    ///   2. It classifies every unit it counts, and `ownership` defaults to
    ///      `"ours"`, so the headline number is what the colony can actually use.
    ///
    /// ## What "ours" means here, exactly
    ///
    /// A unit is `ours` when the colony could go and use it right now:
    ///
    ///   * lying on the map (`Thing.Spawned`), in a cell that is NOT fogged, and
    ///     with `Faction` either null or the player's; or
    ///   * in the inventory or carry tracker of a PLAYER-faction pawn (a
    ///     colonist hauling steel still has our steel); or
    ///   * inside a container thing that we own (`Faction` == player) and that
    ///     is itself spawned and unfogged.
    ///
    /// Fogged is deliberately disqualifying. Ancient-ruin loot is genuinely
    /// spawned and genuinely unreachable until somebody breaches the room, and
    /// counting it as stock is exactly the misread this tool exists to end.
    ///
    /// `ours` is NOT the same as "offerable in a trade". The trade dialog's
    /// colony column counts what is in range of the trade spot or the beacons,
    /// which on 2026-09-01 was 703 silver out of 785 spawned. When money is
    /// about to be spent, the dialog is still the authority.
    ///
    /// ## The fields that are never omitted
    ///
    /// Every row carries the WHOLE-MAP breakdown for its def, in units, even in
    /// `ownership: "ours"` mode: `total`, `ours`, `oursUnforbidden`, `forbidden`,
    /// `inStockpile`, `inHomeArea`, `fogged`, `carried`, `traderStock`,
    /// `otherFaction`, `inContainer`, `reserved`, and a `holders` summary. A
    /// caller must always be able to tell "we have none" from "we have some and
    /// nobody may touch it" from "someone else has some". Those three answers
    /// being one number is the starvation misread of 2026-08-31 in new clothes.
    ///
    /// ## What is deliberately not counted
    ///
    /// Worn apparel and wielded weapons (`Pawn.apparel` / `Pawn.equipment`) are
    /// NOT walked. They are haulable defs, so walking them would silently add
    /// every colonist's parka to the clothing count. `notes.wornGearExcluded`
    /// says so in the payload.
    ///
    /// ## Corpses (WANTED 6)
    ///
    /// A corpse is a `Verse.Corpse` with a generated def, `Corpse_&lt;race&gt;`,
    /// and `alwaysHaulable = true` -- so corpses were always IN this census,
    /// aggregated by def like anything else, and a row said only "3 x human
    /// corpse". M's ask was to *"mark meat-or-skeleton clearly"*, and
    /// meat-or-skeleton is a per-INSTANCE fact: one grave holds a fresh raider
    /// and the field beside it holds last year's bones, and both are the same
    /// def and therefore the same row.
    ///
    /// So the aggregation contract is untouched -- `count`, `stacks` and every
    /// ownership bucket keep meaning exactly what they meant -- and the
    /// per-corpse facts hang off the row in a `corpses[]` sub-list, one entry
    /// per corpse, in the order the census met them. `corpse: true|false` is on
    /// EVERY row so a caller never has to infer the kind of row from the
    /// presence of a key, and `corpses[]` exists on exactly the rows where
    /// `corpse` is true.
    ///
    /// The rot stage is `CompRottable.Stage`, which is a pure read of the
    /// `rotProgressInt` field against two thresholds (IL-checked: the getter
    /// stores nothing). `skeleton` is `RotStage.Dessicated` and nothing else --
    /// that is RimWorld's own name for the bones stage. A MECHANOID corpse has
    /// no `CompRottable` at all (ThingDefGenerator_Corpses adds it only when
    /// `!race.IsMechanoid`), so its stage is null with a reason, never a
    /// fabricated "fresh".
    ///
    /// `corpses: true` restricts the whole census to corpses. It is a filter on
    /// the one tool, not a second tool: every other argument still applies, the
    /// ownership breakdown is the same breakdown, and `skipped.byCorpsesOnly`
    /// says how many non-corpses it removed.
    ///
    /// Orbital trade ships (`map.passingShipManager.passingShips`) are NOT
    /// walked either. They are reachable — `Map.GetChildHolders` includes them —
    /// but their stock is not on the map in any sense, and adding it would
    /// inflate `total` with goods no pawn can reach. `notes.orbitalShipsExcluded`.
    /// </summary>
    public sealed class HomeThingTools
    {
        private const int DefaultMaxPositions = 8;
        private const int MaxHolderDepth = 6;
        private const int MaxHoldersPerRow = 6;
        private const int DefaultMaxCorpsesPerRow = 50;

        [Tool(
            "home/list_things",
            Title = "Census every item on the map by kind, and whose it is",
            Description =
                "Count every thing on the current map, grouped by def, with stack totals and an OWNERSHIP breakdown: "
                + "ours, forbidden, in a stockpile, in the home area, in a fogged (unopened ancient-ruin) cell, carried in a "
                + "pawn's inventory, trader stock, another faction's, or inside a container. Reads RimWorld's own thing lister "
                + "plus the holders it does not cover, so the cost is the number of things rather than the number of cells. "
                + "Defaults to ownership='ours' — what the colony can actually use; pass ownership='all' for the whole map.",
            ResultDescription =
                "success, counts, and things[]: defName, label, stacks, total, ours, oursUnforbidden, forbidden, inStockpile, "
                + "inHomeArea, fogged, carried, traderStock, otherFaction, inContainer, reserved, holders, positions[].")]
        [ToolResponse("things", "array", "One row per thing def that passed the filters, most numerous first. Every row carries corpse (bool); a corpse row also carries corpses[] with one entry per dead body.", Always = true)]
        [ToolResponse("corpseTotal", "number", "How many corpses were counted, in units, after every filter. Always present; 0 means the map was checked and has none.", Always = true)]
        [ToolResponse("corpseSkeletonTotal", "number", "How many of those are skeletons (RotStage.Dessicated). Always present.", Always = true)]
        [ToolResponse("corpsesSkipped", "array", "One entry per corpse DEF whose rot stage could not be given: defName, count, reason ('noRotComp' for mechanoid corpses, otherwise the exception). Empty array = every corpse's stage was read.", Always = true)]
        [ToolResponse("unknownArguments", "array", "Every argument key the caller sent that this tool does not declare, sorted, case-sensitively. Empty array = every key was recognised. The host's own _rimBridgeTimeoutMs is never listed.", Always = true)]
        [ToolResponse("unknownArgumentsWarning", "string", "Present only when unknownArguments is non-empty, or when the caller's raw keys could not be read at all - in which case the empty unknownArguments means 'not known', not 'nothing unknown'.", Nullable = true)]
        public async Task<object> ListThings(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            [ToolParameter(Description = "Only defs whose defName or label contains this text (case-insensitive).")] string match = null,
            [ToolParameter(Description = "What to count: 'haulable' (items you can carry, the default), 'food' (the game's nutrition-giving ingestibles, including animal feed and nutrition-carrying drugs), 'all' (every thing including walls, plants and filth), or 'buildings'.", DefaultValue = "haulable")] string category = "haulable",
            [ToolParameter(Description = "Whose things to report: 'ours' (the default — only defs the colony actually has some of, and only our stacks' positions) or 'all' (every def on the map, trader stock and ancient-ruin loot included). Either way every row carries the full ownership breakdown.", DefaultValue = "ours")] string ownership = "ours",
            [ToolParameter(Description = "Also walk pawn inventories, carry trackers, corpses and container things. Worn apparel and wielded weapons are never walked. False = spawned things only, the pre-2026-09-01 source.", DefaultValue = true)] bool includeHeld = true,
            [ToolParameter(Description = "Only things that are currently forbidden.", DefaultValue = false)] bool forbiddenOnly = false,
            [ToolParameter(Description = "Exclude rock chunks. inv.py's default filter, made explicit and reported.", DefaultValue = false)] bool excludeChunks = false,
            [ToolParameter(Description = "Centre cell x for a radius filter. Requires z and radius.", DefaultValue = -1)] int x = -1,
            [ToolParameter(Description = "Centre cell z for a radius filter. Requires x and radius.", DefaultValue = -1)] int z = -1,
            [ToolParameter(Description = "Only things within this many cells (Chebyshev) of x,z. 0 = whole map.", DefaultValue = 0)] int radius = 0,
            [ToolParameter(Description = "Maximum sample positions listed per def row.", DefaultValue = DefaultMaxPositions)] int maxPositionsPerDef = DefaultMaxPositions,
            [ToolParameter(Description = "Count ONLY corpses. Every other filter still applies and skipped.byCorpsesOnly says how many non-corpses were removed. The per-corpse detail is on the rows either way; this only narrows what is counted.", DefaultValue = false)] bool corpses = false,
            [ToolParameter(Description = "Maximum corpses detailed in one row's corpses[] list. The row always reports corpseCount, corpsesListed, corpsesNotListed and corpsesTruncated, so the cap can never hide bodies in silence.", DefaultValue = DefaultMaxCorpsesPerRow)] int maxCorpsesPerRow = DefaultMaxCorpsesPerRow)
        {
            return BridgeCommon.WithUnknownArguments(
                await ListThingsCore(
                    ctx, cancellationToken, match, category, ownership, includeHeld, forbiddenOnly,
                    excludeChunks, x, z, radius, maxPositionsPerDef, corpses, maxCorpsesPerRow).ConfigureAwait(false),
                ctx, typeof(HomeThingTools), "home/list_things");
        }

        private async Task<object> ListThingsCore(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            string match,
            string category,
            string ownership,
            bool includeHeld,
            bool forbiddenOnly,
            bool excludeChunks,
            int x,
            int z,
            int radius,
            int maxPositionsPerDef,
            bool corpsesOnly,
            int maxCorpsesPerRow)
        {
            if (ctx?.MainThread == null)
                return Failure("No RimBridge main-thread dispatcher is available for this invocation.");

            // Companion tools are dispatched with MarshalToMainThread = false, so
            // every read of listerThings / zoneManager / fogGrid has to be hopped
            // onto RimWorld's main thread by hand. Removing this hop still
            // compiles and fails intermittently, which is the worst failure mode
            // available.
            return await ctx.MainThread
                .InvokeAsync(() => Build(match, category, ownership, includeHeld, forbiddenOnly, excludeChunks,
                                         x, z, radius, maxPositionsPerDef, corpsesOnly, maxCorpsesPerRow),
                             cancellationToken)
                .ConfigureAwait(false);
        }

        // ------------------------------------------------------------- build ---

        private static object Build(string match, string category, string ownership, bool includeHeld,
                                    bool forbiddenOnly, bool excludeChunks, int cx, int cz, int radius,
                                    int maxPositions, bool corpsesOnly, int maxCorpsesPerRow)
        {
            if (!TryGetMap(out var map, out var mapError))
                return Failure(mapError);

            if (maxPositions < 0)
                maxPositions = 0;
            if (maxCorpsesPerRow < 0)
                maxCorpsesPerRow = 0;
            var useRadius = radius > 0 && cx >= 0 && cz >= 0;
            var cat = (category ?? "haulable").Trim().ToLowerInvariant();
            if (cat != "all" && cat != "buildings" && cat != "food")
                cat = "haulable";
            var oursOnly = !string.Equals((ownership ?? "ours").Trim(), "all", StringComparison.OrdinalIgnoreCase);

            // Faction.OfPlayer routes through Log.Error when there is no player
            // faction, and Log.Error pauses the game (TickManager.Pause is in its
            // call path). OfPlayerSilentFail is the same lookup without the trap.
            var player = SafePlayerFaction();

            List<Thing> spawned;
            try
            {
                if (cat == "all")
                    spawned = map.listerThings.AllThings.ToList();
                else if (cat == "buildings")
                    spawned = map.listerThings.ThingsInGroup(ThingRequestGroup.BuildingArtificial).ToList();
                else
                    spawned = map.listerThings.ThingsInGroup(ThingRequestGroup.HaulableEver).ToList();
                if (cat == "food")
                    spawned = spawned.Where(t => PassesCategory(t, cat)).ToList();
            }
            catch (Exception e)
            {
                return Failure("Could not read map.listerThings: " + e.Message);
            }

            // Held things: everything the thing lister structurally cannot see,
            // because it only ever holds spawned things. This is where trader
            // stock lives.
            var held = new List<HeldThing>();
            var holderRootsWalked = 0;
            if (includeHeld)
            {
                try { holderRootsWalked = CollectHeld(map, cat, held); }
                catch (Exception e) { return Failure("Could not walk thing holders: " + e.Message); }
            }

            var reserved = ReservedThings(map);

            var rows = new Dictionary<string, Row>();
            var scanned = 0;
            var skippedRadius = 0;
            var skippedChunks = 0;
            var skippedMatch = 0;
            var skippedNotForbidden = 0;
            var skippedNotOurs = 0;
            var skippedNotCorpse = 0;
            // Deduplicated by def: a battlefield of forty mechanoid corpses is
            // one finding, not forty. A null rotStage is ALWAYS named here.
            var corpseIssues = new Dictionary<string, CorpseIssue>(StringComparer.Ordinal);

            foreach (var entry in Everything(spawned, held))
            {
                var thing = entry.Thing;
                if (thing == null || thing.def == null)
                    continue;
                scanned++;

                var corpse = thing as Corpse;
                if (corpsesOnly && corpse == null)
                {
                    skippedNotCorpse++;
                    continue;
                }

                var defName = thing.def.defName ?? "?";
                if (excludeChunks && defName.StartsWith("Chunk", StringComparison.OrdinalIgnoreCase))
                {
                    skippedChunks++;
                    continue;
                }

                if (!string.IsNullOrEmpty(match))
                {
                    var label = SafeLabel(thing) ?? string.Empty;
                    if (defName.IndexOf(match, StringComparison.OrdinalIgnoreCase) < 0 &&
                        label.IndexOf(match, StringComparison.OrdinalIgnoreCase) < 0)
                    {
                        skippedMatch++;
                        continue;
                    }
                }

                // PositionHeld is the holder's cell for anything unspawned, so a
                // radius filter still catches the alpaca standing in our stockpile.
                var pos = SafePositionHeld(thing);
                if (useRadius &&
                    Math.Max(Math.Abs(pos.x - cx), Math.Abs(pos.z - cz)) > radius)
                {
                    skippedRadius++;
                    continue;
                }

                var forbidden = IsForbidden(thing);
                if (forbiddenOnly && forbidden != true)
                {
                    skippedNotForbidden++;
                    continue;
                }

                var units = Math.Max(1, thing.stackCount);
                var c = Classify(map, thing, entry.Holder, pos);

                if (!rows.TryGetValue(defName, out var row))
                {
                    // A corpse's own label names the dead pawn ("Ada's corpse"), and
                    // a row aggregates every body of the def, so the row takes the
                    // def's label ("human corpse"); corpses[] names each body.
                    row = new Row { DefName = defName,
                                    Label = thing is Corpse ? SafeDefLabel(thing) : SafeLabel(thing),
                                    Food = IsFood(thing) };
                    rows[defName] = row;
                }
                if (corpse != null)
                    row.IsCorpse = true;

                // The whole-map breakdown is accumulated for EVERY unit, in both
                // ownership modes. A row must never be able to hide the 863
                // silver on the trader's muffalo just because it is not ours.
                row.Stacks++;
                row.Total += units;
                if (c.Ours) { row.Ours += units; row.OursStacks++; }
                if (c.Ours && forbidden != true) row.OursUnforbidden += units;
                if (forbidden == true) row.Forbidden += units;
                if (c.InStockpile) row.InStockpile += units;
                if (c.InHomeArea) row.InHomeArea += units;
                if (c.Fogged) row.Fogged += units;
                if (c.Carried) row.Carried += units;
                if (c.TraderStock) row.TraderStock += units;
                if (c.OtherFaction) row.OtherFaction += units;
                if (c.InContainer) row.InContainer += units;
                if (c.Ours && reserved != null && reserved.Contains(thing)) row.Reserved += units;
                if (!c.Ours && !string.IsNullOrEmpty(c.Holder))
                {
                    row.Holders.TryGetValue(c.Holder, out var had);
                    row.Holders[c.Holder] = had + units;
                }

                // BEFORE the ownership gate, like every other whole-map figure
                // on the row: a raider's body in our killbox is not "ours" and
                // is still a corpse on this map, and a row that could only
                // describe our own dead would be the silence this tool exists
                // against. Each entry carries its own `ours`.
                if (corpse != null)
                {
                    row.CorpseCount++;
                    if (row.Corpses.Count < maxCorpsesPerRow)
                        row.Corpses.Add(CorpseRow(corpse, pos, c, forbidden, corpseIssues, row));
                    else
                        CountCorpseStage(corpse, corpseIssues, row);
                }

                if (oursOnly && !c.Ours)
                {
                    skippedNotOurs++;
                    continue;                   // no position, no stack credit
                }

                if (row.Positions.Count < maxPositions)
                    row.Positions.Add(PositionRow(thing, pos));
                else
                    row.PositionsNotListed++;
            }

            var emitted = rows.Values.Where(r => !oursOnly || r.Ours > 0);

            var things = emitted
                .OrderByDescending(r => oursOnly ? r.Ours : r.Total)
                .Select(r => WithCorpses(new Dictionary<string, object>
                {
                    { "defName", r.DefName },
                    { "label", r.Label },
                    { "food", r.Food },
                    // Whole-map figures, both modes. `stacks`/`total` are every
                    // stack of this def anywhere; `oursStacks`/`ours` are the
                    // colony's share of them.
                    { "stacks", r.Stacks },
                    { "total", r.Total },
                    { "oursStacks", r.OursStacks },
                    { "ours", r.Ours },
                    { "oursUnforbidden", r.OursUnforbidden },
                    // Never omitted: "we have none", "we have some and it is
                    // forbidden" and "someone else has some" are the three
                    // answers this tool exists to keep apart.
                    { "forbidden", r.Forbidden },
                    { "inStockpile", r.InStockpile },
                    { "inHomeArea", r.InHomeArea },
                    { "fogged", r.Fogged },
                    { "carried", r.Carried },
                    { "traderStock", r.TraderStock },
                    { "otherFaction", r.OtherFaction },
                    { "inContainer", r.InContainer },
                    { "reserved", r.Reserved },
                    { "holders", TopHolders(r.Holders) },
                    { "positions", r.Positions },
                    { "positionsNotListed", r.PositionsNotListed }
                }, r))
                .ToList();

            var all = rows.Values.ToList();
            var anyCorpse = all.Any(r => r.IsCorpse);
            var payload = new Dictionary<string, object>
            {
                { "success", true },
                { "tool", "home/list_things" },
                { "defCount", things.Count },
                { "defCountOnMap", all.Count },
                { "thingsScanned", scanned },
                { "spawnedScanned", spawned.Count },
                { "heldScanned", held.Count },
                { "holderRootsWalked", holderRootsWalked },
                { "stackTotal", all.Sum(r => r.Stacks) },
                // itemTotal follows the ownership mode; mapTotal never does, so
                // the two modes can always be reconciled against each other.
                { "itemTotal", oursOnly ? all.Sum(r => r.Ours) : all.Sum(r => r.Total) },
                { "mapTotal", all.Sum(r => r.Total) },
                { "oursTotal", all.Sum(r => r.Ours) },
                { "oursUnforbiddenTotal", all.Sum(r => r.OursUnforbidden) },
                { "forbiddenTotal", all.Sum(r => r.Forbidden) },
                { "foggedTotal", all.Sum(r => r.Fogged) },
                { "carriedTotal", all.Sum(r => r.Carried) },
                { "traderStockTotal", all.Sum(r => r.TraderStock) },
                { "otherFactionTotal", all.Sum(r => r.OtherFaction) },
                { "inContainerTotal", all.Sum(r => r.InContainer) },
                { "playerFaction", player != null ? player.Name : null },
                // Always present, zeros included: "this map has no corpses" and
                // "nobody looked for corpses" must never be the same answer.
                { "corpseTotal", rows.Values.Sum(r => r.CorpseCount) },
                { "corpseSkeletonTotal", rows.Values.Sum(r => r.Skeletons) },
                { "corpsesSkipped", corpseIssues.Values
                    .OrderByDescending(i => i.Count)
                    .Select(i => i.ToPayload())
                    .ToList() },
                // Every filter reports what it removed. An empty answer must never
                // be ambiguous between "nothing there" and "nothing survived".
                { "skipped", new Dictionary<string, object>
                    {
                        { "byRadius", skippedRadius },
                        { "byChunkFilter", skippedChunks },
                        { "byMatch", skippedMatch },
                        { "byForbiddenOnly", skippedNotForbidden },
                        { "byOwnership", skippedNotOurs },
                        { "byCorpsesOnly", skippedNotCorpse }
                    } },
                { "filters", new Dictionary<string, object>
                    {
                        { "match", match },
                        { "category", cat },
                        { "ownership", oursOnly ? "ours" : "all" },
                        { "includeHeld", includeHeld },
                        { "forbiddenOnly", forbiddenOnly },
                        { "excludeChunks", excludeChunks },
                        { "x", cx }, { "z", cz }, { "radius", radius },
                        { "maxPositionsPerDef", maxPositions },
                        // Present in both modes: it is what separates "corpseTotal
                        // is 3 because the map has 3" from "because that is all we
                        // were asked to look at".
                        { "corpses", corpsesOnly },
                        { "maxCorpsesPerRow", maxCorpsesPerRow }
                    } },
                { "notes", new Dictionary<string, object>
                    {
                        { "oursMeans", "spawned and unfogged with no other faction's claim, or held by a player pawn, or inside a player-owned container" },
                        { "oursIsNotTradeable", "the trade dialog's colony column counts only what is in range of the trade spot or beacons; read it before spending" },
                        { "foggedExcludedFromOurs", true },
                        { "wornGearExcluded", "Pawn.apparel and Pawn.equipment are never walked" },
                        { "orbitalShipsExcluded", "map.passingShipManager.passingShips is never walked" },
                        { "constructionSitesExcluded", "resources already delivered to a Blueprint or Frame are spent, not stock" },
                        { "countsAreUnits", "every count except stacks/oursStacks is stackCount units" }
                    } },
                { "things", things }
            };

            // The corpse notes ride with the corpse rows. On a map with no
            // bodies they would be a page of prose explaining fields nobody
            // received; corpseTotal 0 and filters.corpses already say what
            // happened there.
            if (anyCorpse || corpsesOnly)
            {
                var notes = (Dictionary<string, object>)payload["notes"];
                notes["corpseRowsAggregateByDef"] = "Corpses aggregate by def like everything else -- every human corpse is one Corpse_Human row -- because meat-or-skeleton is a per-BODY fact and a def row cannot carry it. corpses[] on the row is that per-body detail, one entry per corpse, and count/stacks/the ownership buckets are untouched. corpse:true|false is on every row; corpses[] exists on exactly the rows where it is true.";
                notes["corpseStageSource"] = "CompRottable.Stage: Fresh below TicksToRotStart, Rotting below TicksToDessicated, Dessicated after. skeleton is Dessicated and nothing else. A mechanoid corpse has no CompRottable (ThingDefGenerator_Corpses adds it only for non-mechanoids), so its rotStage is null with reason 'noRotComp' and it is named in corpsesSkipped -- never guessed as fresh.";
                notes["wasColonistIsAFactionTest"] = "wasColonist is InnerPawn.Faction == the player faction, so a dead colony ANIMAL is wasColonist:true too. Read humanlike beside it before calling anything a dead colonist.";
            }

            return payload;
        }

        // ------------------------------------------------------- the two sources ---

        private struct Entry
        {
            public Thing Thing;
            public HolderInfo Holder;
        }

        private static IEnumerable<Entry> Everything(List<Thing> spawned, List<HeldThing> held)
        {
            foreach (var t in spawned)
                yield return new Entry { Thing = t, Holder = null };
            foreach (var h in held)
                yield return new Entry { Thing = h.Thing, Holder = h.Holder };
        }

        /// <summary>
        /// Where the item lives when it is not lying on the map. Built once per
        /// root so the per-thing classifier is a field read, not a chain walk.
        /// </summary>
        private sealed class HolderInfo
        {
            public string Kind;             // pawnInventory | carried | corpse | container
            public string Label;            // "Muffalo (Tribe of X)", "ancient cryptosleep casket"
            public Pawn Pawn;               // set for pawnInventory / carried / corpse
            public Thing RootThing;         // the spawned thing the chain hangs off
            public bool PlayerHeld;         // holder pawn or container belongs to us
            public bool TraderStock;        // non-player trade caravan Trader/Carrier
            public bool OtherFaction;       // holder belongs to a non-player faction
        }

        private struct HeldThing
        {
            public Thing Thing;
            public HolderInfo Holder;
        }

        /// <summary>
        /// Walk every holder that hangs off a SPAWNED thing: pawn inventories and
        /// carry trackers, corpses (and the inventory of the pawn inside them),
        /// and container things — ancient caskets, crates, minified things,
        /// anything with a CompThingContainer.
        ///
        /// Deliberately NOT via ThingOwnerUtility.GetAllThingsRecursively(map,…):
        /// that one goes through Map.GetChildHolders, which includes
        /// passingShipManager.passingShips, i.e. orbital trader stock that is not
        /// on the map at all. It also walks apparel and equipment. Starting from
        /// spawned things and naming each root keeps both out and gives every
        /// held unit a holder label.
        /// </summary>
        private static int CollectHeld(Map map, string cat, List<HeldThing> outThings)
        {
            var roots = 0;
            var seenOwners = new HashSet<ThingOwner>();
            List<Thing> allSpawned;
            try { allSpawned = map.listerThings.AllThings.ToList(); }
            catch { return 0; }

            foreach (var thing in allSpawned)
            {
                if (!(thing is IThingHolder))
                    continue;
                roots++;
                WalkHolder(map, cat, thing, thing, null, outThings, seenOwners, 0);
            }
            return roots;
        }

        private static void WalkHolder(Map map, string cat, Thing rootThing, Thing holderThing,
                                       HolderInfo inherited, List<HeldThing> outThings,
                                       HashSet<ThingOwner> seenOwners, int depth)
        {
            if (depth > MaxHolderDepth || holderThing == null)
                return;

            var owners = new List<ThingOwner>();
            var info = inherited;

            var pawn = holderThing as Pawn;
            if (pawn != null)
            {
                // Only inventory and the carry tracker. Apparel and equipment are
                // worn, not stock; walking them would add every colonist's parka
                // to the clothing count.
                var inv = SafeOwner(() => pawn.inventory?.innerContainer);
                var carry = SafeOwner(() => pawn.carryTracker?.innerContainer);
                var pawnInfo = DescribePawnHolder(pawn, rootThing, inherited);
                if (inv != null && seenOwners.Add(inv))
                    Emit(map, cat, inv, WithKind(pawnInfo, "pawnInventory"), outThings, seenOwners, depth, rootThing);
                if (carry != null && seenOwners.Add(carry))
                    Emit(map, cat, carry, WithKind(pawnInfo, "carried"), outThings, seenOwners, depth, rootThing);
                return;
            }

            // A corpse holds the dead pawn; the dead pawn holds their inventory.
            var corpse = holderThing as Corpse;
            if (corpse != null)
            {
                var inner = SafePawn(() => corpse.InnerPawn);
                info = new HolderInfo
                {
                    Kind = "corpse",
                    Label = "corpse of " + (inner != null ? SafePawnName(inner) : "?"),
                    Pawn = inner,
                    RootThing = rootThing,
                    PlayerHeld = false,
                    TraderStock = false,
                    OtherFaction = inner != null && IsOtherFaction(inner.Faction)
                };
                if (inner != null)
                {
                    var inv = SafeOwner(() => inner.inventory?.innerContainer);
                    if (inv != null && seenOwners.Add(inv))
                        Emit(map, cat, inv, info, outThings, seenOwners, depth, rootThing);
                }
                return;
            }

            // Resources already delivered to a blueprint or a construction
            // frame are SPENT, not stock. list_buildings reports them as the
            // site's `have`; counting them here would put the same steel in two
            // places and inflate what the colony can still build with.
            if (holderThing is Frame || holderThing is Blueprint)
                return;

            // Everything else that holds things: caskets, crates, minified
            // things, CompThingContainer, book cases.
            var direct = SafeOwner(() => ThingOwnerUtility.TryGetInnerInteractableThingOwner(holderThing))
                         ?? SafeOwner(() => ((IThingHolder)holderThing).GetDirectlyHeldThings());
            if (direct == null)
                return;
            if (!seenOwners.Add(direct))
                return;

            info = new HolderInfo
            {
                Kind = "container",
                Label = SafeLabel(holderThing),
                Pawn = null,
                RootThing = rootThing,
                PlayerHeld = IsPlayerFaction(SafeFaction(holderThing)),
                TraderStock = false,
                OtherFaction = IsOtherFaction(SafeFaction(holderThing))
            };
            Emit(map, cat, direct, info, outThings, seenOwners, depth, rootThing);
        }

        private static void Emit(Map map, string cat, ThingOwner owner, HolderInfo info,
                                 List<HeldThing> outThings, HashSet<ThingOwner> seenOwners,
                                 int depth, Thing rootThing)
        {
            // ThingOwner is an IList<Thing>; index it rather than pick an
            // enumerator, because ThingOwner<T> hides a second GetEnumerator.
            var contents = new List<Thing>();
            try
            {
                for (var i = 0; i < owner.Count; i++)
                    contents.Add(owner[i]);
            }
            catch { return; }

            foreach (var t in contents)
            {
                if (t == null || t.def == null)
                    continue;
                if (PassesCategory(t, cat))
                    outThings.Add(new HeldThing { Thing = t, Holder = info });
                // A pawn inside a casket, a corpse inside a crate: keep going.
                if (t is IThingHolder)
                    WalkHolder(map, cat, rootThing, t, info, outThings, seenOwners, depth + 1);
            }
        }

        private static bool PassesCategory(Thing t, string cat)
        {
            try
            {
                if (cat == "all") return true;
                if (cat == "buildings") return false;      // nothing held is a spawned building
                if (cat == "food") return IsFood(t);
                return t.def.EverHaulable;
            }
            catch { return false; }
        }

        private static HolderInfo DescribePawnHolder(Pawn pawn, Thing rootThing, HolderInfo inherited)
        {
            var faction = SafeFaction(pawn);
            var role = SafeTraderRole(pawn);
            var isPlayer = IsPlayerFaction(faction);
            var label = SafePawnName(pawn);
            if (faction != null)
                label += " (" + faction.Name + ")";
            if (role == TraderCaravanRole.Trader || role == TraderCaravanRole.Carrier)
                label += role == TraderCaravanRole.Trader ? " [trader]" : " [pack animal]";
            if (inherited != null && inherited.Kind == "corpse")
                label = inherited.Label;
            return new HolderInfo
            {
                Kind = "pawnInventory",
                Label = label,
                Pawn = pawn,
                RootThing = rootThing,
                // A dead pawn's kit is not ours until somebody strips it.
                PlayerHeld = isPlayer && !SafeDead(pawn) && (inherited == null || inherited.Kind != "corpse"),
                TraderStock = !isPlayer && (role == TraderCaravanRole.Trader || role == TraderCaravanRole.Carrier),
                OtherFaction = IsOtherFaction(faction)
            };
        }

        private static HolderInfo WithKind(HolderInfo src, string kind)
        {
            return new HolderInfo
            {
                Kind = kind,
                Label = src.Label,
                Pawn = src.Pawn,
                RootThing = src.RootThing,
                PlayerHeld = src.PlayerHeld,
                TraderStock = src.TraderStock,
                OtherFaction = src.OtherFaction
            };
        }

        // ------------------------------------------------------- classification ---

        private struct Ownership
        {
            public bool Ours;
            public bool InStockpile;
            public bool InHomeArea;
            public bool Fogged;
            public bool Carried;
            public bool TraderStock;
            public bool OtherFaction;
            public bool InContainer;
            public string Holder;
        }

        private static Ownership Classify(Map map, Thing thing, HolderInfo holder, IntVec3 pos)
        {
            var o = new Ownership();
            var inBounds = InBounds(map, pos);
            o.Fogged = inBounds && IsFogged(map, pos);
            o.InHomeArea = inBounds && InHomeArea(map, pos);

            if (holder == null)
            {
                // Lying on the map (or on a shelf: Building_Storage holds its
                // contents SPAWNED, so a shelf's stack arrives down this path
                // and is ours like any other).
                o.InStockpile = inBounds && InStockpile(map, pos);
                var f = SafeFaction(thing);
                o.OtherFaction = IsOtherFaction(f);
                o.Ours = !o.Fogged && !o.OtherFaction;
                if (!o.Ours)
                    o.Holder = o.Fogged ? "fogged (unopened ruin)" : ("faction: " + (f != null ? f.Name : "?"));
                return o;
            }

            o.Holder = holder.Label;
            o.Carried = holder.Kind == "pawnInventory" || holder.Kind == "carried";
            o.InContainer = holder.Kind == "container" || holder.Kind == "corpse";
            o.TraderStock = holder.TraderStock;
            o.OtherFaction = holder.OtherFaction;
            o.InStockpile = false;              // held things are in no zone
            o.Ours = holder.PlayerHeld && !o.Fogged;
            return o;
        }

        // ------------------------------------------------------------- the row ---

        private static object PositionRow(Thing thing, IntVec3 pos)
        {
            var defName = BridgeCommon.SafeString(
                () => thing.def == null ? null : thing.def.defName);
            var spawned = BridgeCommon.Try(() => thing.Spawned, false);
            var ids = new List<object>();
            var loadId = BridgeCommon.SafeString(() => thing.GetUniqueLoadID());
            var thingId = BridgeCommon.SafeString(() => thing.ThingID);
            var number = BridgeCommon.TryN(() => thing.thingIDNumber);
            if (loadId != null) ids.Add(loadId);
            if (thingId != null) ids.Add(thingId);
            if (number != null) ids.Add(number.Value.ToString(CultureInfo.InvariantCulture));
            if (spawned && !string.IsNullOrEmpty(defName))
                ids.Add(defName + "@" + pos.x.ToString(CultureInfo.InvariantCulture)
                        + "," + pos.z.ToString(CultureInfo.InvariantCulture));
            return new Dictionary<string, object>
            {
                { "x", pos.x }, { "z", pos.z },
                { "thingId", loadId }, { "idForms", ids },
                { "stackCount", BridgeCommon.Try(() => thing.stackCount, 0) },
                { "spawned", spawned }
            };
        }

        private static bool IsFood(Thing t)
        {
            return BridgeCommon.Try(
                () => t != null && t.def != null && t.def.IsNutritionGivingIngestible,
                false);
        }

        private sealed class Row
        {
            public string DefName;
            public string Label;
            public bool Food;
            public int Stacks;
            public int OursStacks;
            public int Total;
            public int Ours;
            public int OursUnforbidden;
            public int Forbidden;
            public int InStockpile;
            public int InHomeArea;
            public int Fogged;
            public int Carried;
            public int TraderStock;
            public int OtherFaction;
            public int InContainer;
            public int Reserved;
            public int PositionsNotListed;
            public readonly Dictionary<string, int> Holders = new Dictionary<string, int>();
            public readonly List<object> Positions = new List<object>();

            // ---- corpses. IsCorpse is set from the THING (a Verse.Corpse), not
            // from the def name, so a renamed or modded corpse def cannot slip
            // past a string test.
            public bool IsCorpse;
            public int CorpseCount;
            public int Skeletons;
            public int Fresh;
            public int Rotting;
            public int StageUnknown;
            public readonly List<object> Corpses = new List<object>();
        }

        /// <summary>
        /// The corpse block, appended to a row's payload only when the row is a
        /// corpse row. `corpse` itself is on every row, false included, so the
        /// presence of this block is never the only way to tell.
        /// </summary>
        private static Dictionary<string, object> WithCorpses(
            Dictionary<string, object> row, Row r)
        {
            row["corpse"] = r.IsCorpse;
            if (!r.IsCorpse)
                return row;
            row["corpseCount"] = r.CorpseCount;
            row["skeletons"] = r.Skeletons;
            row["rotStages"] = new Dictionary<string, object>
            {
                { "fresh", r.Fresh },
                { "rotting", r.Rotting },
                { "dessicated", r.Skeletons },
                { "unknown", r.StageUnknown }
            };
            row["corpses"] = r.Corpses;
            // The cap states itself in the row it truncated -- the lesson
            // list_buildings' aggregate rows learned on 2026-09-02.
            row["corpsesListed"] = r.Corpses.Count;
            row["corpsesNotListed"] = r.CorpseCount - r.Corpses.Count;
            row["corpsesTruncated"] = r.CorpseCount > r.Corpses.Count;
            return row;
        }

        /// <summary>One corpse def's worth of unreadable rot stages.</summary>
        private sealed class CorpseIssue
        {
            public string DefName;
            public string Reason;
            public int Count;

            public Dictionary<string, object> ToPayload()
            {
                return new Dictionary<string, object>
                {
                    { "defName", DefName },
                    { "count", Count },
                    { "reason", Reason }
                };
            }
        }

        // ------------------------------------------------------------ corpses ---

        /// <summary>
        /// One dead body. Everything here is per-instance and therefore cannot
        /// live on the aggregate row: two human corpses in one row can be a
        /// fresh colonist and a raider's skeleton.
        /// </summary>
        private static Dictionary<string, object> CorpseRow(
            Corpse corpse, IntVec3 pos, Ownership c, bool? forbidden,
            Dictionary<string, CorpseIssue> issues, Row row)
        {
            var stage = CountCorpseStage(corpse, issues, row);
            var inner = SafePawn(() => corpse.InnerPawn);
            return new Dictionary<string, object>
            {
                { "race", inner != null ? SafeRaceDefName(inner) : null },
                { "name", inner != null ? SafeGivenName(inner) : null },
                { "humanlike", inner != null && SafeHumanlike(inner) },
                // Present when false: the corpse of a colony muffalo is
                // wasColonist too, which is why humanlike sits beside it.
                { "wasColonist", inner != null && IsPlayerFaction(SafeFaction(inner)) },
                { "rotStage", stage },
                { "skeleton", stage == "dessicated" },
                { "ours", c.Ours },
                { "forbidden", forbidden },
                { "position", BridgeCommon.Pos(pos) },
                // null = lying on the map. Anything else is a grave, a casket,
                // a pawn's pack, a container.
                { "holder", c.Holder }
            };
        }

        /// <summary>
        /// The rot stage as a lower-case word, or null with the def named in
        /// corpsesSkipped. Called for EVERY corpse including the ones the
        /// corpses[] cap cut, so the row's stage tallies cover every body.
        ///
        /// CompRottable.Stage is a pure read: the getter compares the
        /// rotProgressInt field against two thresholds and stores nothing.
        /// </summary>
        private static string CountCorpseStage(
            Corpse corpse, Dictionary<string, CorpseIssue> issues, Row row)
        {
            CompRottable rot;
            try { rot = corpse.GetComp<CompRottable>(); }
            catch (Exception e)
            {
                row.StageUnknown++;
                Note(issues, corpse, e.GetType().Name + ": " + e.Message);
                return null;
            }

            if (rot == null)
            {
                row.StageUnknown++;
                Note(issues, corpse, "noRotComp");
                return null;
            }

            RotStage stage;
            try { stage = rot.Stage; }
            catch (Exception e)
            {
                row.StageUnknown++;
                Note(issues, corpse, e.GetType().Name + ": " + e.Message);
                return null;
            }

            switch (stage)
            {
                case RotStage.Fresh: row.Fresh++; return "fresh";
                case RotStage.Rotting: row.Rotting++; return "rotting";
                case RotStage.Dessicated: row.Skeletons++; return "dessicated";
                default:
                    row.StageUnknown++;
                    Note(issues, corpse, "unknown RotStage value " + (int)stage);
                    return null;
            }
        }

        private static void Note(Dictionary<string, CorpseIssue> issues, Corpse corpse, string reason)
        {
            string key;
            try { key = (corpse.def != null ? corpse.def.defName : null) ?? "?"; }
            catch { key = "?"; }
            CorpseIssue issue;
            if (!issues.TryGetValue(key, out issue))
            {
                issue = new CorpseIssue { DefName = key, Reason = reason };
                issues[key] = issue;
            }
            issue.Count++;
        }

        private static string SafeRaceDefName(Pawn p)
        {
            try { return p.def != null ? p.def.defName : null; }
            catch { return null; }
        }

        /// <summary>The pawn's own name, or null if it never had one (most
        /// animals). NOT the label, which would read "dead muffalo" for
        /// everything and make "did this one have a name" unanswerable.</summary>
        private static string SafeGivenName(Pawn p)
        {
            try { return p.Name != null ? p.Name.ToStringFull : null; }
            catch { return null; }
        }

        private static bool SafeHumanlike(Pawn p)
        {
            try { return p.RaceProps != null && p.RaceProps.Humanlike; }
            catch { return false; }
        }

        private static object TopHolders(Dictionary<string, int> holders)
        {
            var top = new Dictionary<string, object>();
            foreach (var kv in holders.OrderByDescending(k => k.Value).Take(MaxHoldersPerRow))
                top[kv.Key] = kv.Value;
            if (holders.Count > MaxHoldersPerRow)
                top["(+ others)"] = holders.Count - MaxHoldersPerRow;
            return top;
        }

        // ------------------------------------------------------- safe game reads ---

        private static Faction SafePlayerFaction()
        {
            // NOT Faction.OfPlayer: its IL is
            //   get_OfPlayer -> get_OfPlayerSilentFail -> Log.Error
            // and Log.Error's call path contains TickManager.Pause, so the
            // no-player-faction case would stop the colony from a read.
            try { return Faction.OfPlayerSilentFail; }
            catch { return null; }
        }

        private static bool IsPlayerFaction(Faction f)
        {
            try
            {
                var p = Faction.OfPlayerSilentFail;
                return f != null && p != null && f == p;
            }
            catch { return false; }
        }

        private static bool IsOtherFaction(Faction f)
        {
            try
            {
                if (f == null) return false;
                var p = Faction.OfPlayerSilentFail;
                return p == null || f != p;
            }
            catch { return false; }
        }

        private static Faction SafeFaction(Thing t)
        {
            try { return t.Faction; }
            catch { return null; }
        }

        private static TraderCaravanRole SafeTraderRole(Pawn p)
        {
            // GetTraderCaravanRole reads kindDef.trader, RaceProps.packAnimal and
            // inventory.Any only — no stores, verified in IL.
            try { return TraderCaravanUtility.GetTraderCaravanRole(p); }
            catch { return TraderCaravanRole.None; }
        }

        private static bool SafeDead(Pawn p)
        {
            try { return p.Dead; }
            catch { return false; }
        }

        private static string SafePawnName(Pawn p)
        {
            try { return p.LabelShortCap; }
            catch
            {
                try { return p.def?.label; }
                catch { return "?"; }
            }
        }

        private static Pawn SafePawn(Func<Pawn> f)
        {
            try { return f(); }
            catch { return null; }
        }

        private static ThingOwner SafeOwner(Func<ThingOwner> f)
        {
            try { return f(); }
            catch { return null; }
        }

        private static IntVec3 SafePositionHeld(Thing t)
        {
            try { return t.PositionHeld; }
            catch
            {
                try { return t.Position; }
                catch { return IntVec3.Invalid; }
            }
        }

        private static HashSet<Thing> ReservedThings(Map map)
        {
            // AllReservedThings() is a Select over the reservation list — read
            // only, verified in IL. Materialised once so the per-thing test is a
            // hash lookup rather than a scan of every reservation.
            try
            {
                var set = new HashSet<Thing>();
                foreach (var t in map.reservationManager.AllReservedThings())
                    if (t != null) set.Add(t);
                return set;
            }
            catch { return null; }
        }

        private static bool InBounds(Map map, IntVec3 c)
        {
            try { return c.IsValid && c.InBounds(map); }
            catch { return false; }
        }

        private static bool IsFogged(Map map, IntVec3 c)
        {
            try { return map.fogGrid != null && map.fogGrid.IsFogged(c); }
            catch { return false; }
        }

        private static bool InHomeArea(Map map, IntVec3 c)
        {
            try
            {
                var home = map.areaManager?.Home;
                return home != null && home[c];
            }
            catch { return false; }
        }

        private static bool? IsForbidden(Thing thing)
        {
            try
            {
                var comp = (thing as ThingWithComps)?.GetComp<CompForbiddable>();
                return comp?.Forbidden;
            }
            catch
            {
                return null;
            }
        }

        private static bool InStockpile(Map map, IntVec3 pos)
        {
            try
            {
                return map.zoneManager?.ZoneAt(pos) is Zone_Stockpile;
            }
            catch
            {
                return false;
            }
        }

        private static string SafeDefLabel(Thing thing)
        {
            try { return thing.def?.LabelCap.ToString() ?? thing.def?.label; }
            catch { return SafeLabel(thing); }
        }

        private static string SafeLabel(Thing thing)
        {
            try { return thing.LabelCapNoCount.ToString(); }
            catch
            {
                try { return thing.def?.label; }
                catch { return null; }
            }
        }

        /// <summary>The shared map gate; see BridgeCommon.TryGetMap. The error
        /// text names this tool.</summary>
        private static bool TryGetMap(out Map map, out string error)
        {
            return BridgeCommon.TryGetMap("home/list_things", out map, out error);
        }

        /// <summary>The shared refusal shape; see BridgeCommon.Failure.</summary>
        private static object Failure(string error)
        {
            return BridgeCommon.Failure("home/list_things", error);
        }
    }
}
