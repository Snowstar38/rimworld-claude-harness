using System;
using System.Collections.Generic;
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
    /// home/research — the whole research screen in one call, plus the one write
    /// that screen exists for: choosing what to research next.
    ///
    /// ## Why it exists
    ///
    /// Until now nothing in the stack could read research at all. The documented
    /// path was: open the Research tab on stream (a visible mutation), then trim
    /// a 204 KB `get_ui_layout` payload down to something readable. `Need
    /// research project` has been a standing Medium alert on this save, and the
    /// tool that would answer it was the tab itself.
    ///
    /// ONE tool with opt-in blocks, per the house pattern: the current project,
    /// what can be started, the benches and the people, always; `locked`,
    /// `finished` and `unlocks` on request; `filter` narrows every list; `set`
    /// is the write side and is a dry run until `dryRun:false`.
    ///
    /// ## Verified against the installed Assembly-CSharp.dll (RimWorld 1.6)
    ///
    ///   RimWorld.ResearchManager
    ///     .GetProject(KnowledgeCategoryDef category = null) -> ResearchProjectDef
    ///          — with a NULL category it returns the private `currentProj`.
    ///     .CurrentAnomalyKnowledgeProjects -> List&lt;KnowledgeCategoryProject&gt;
    ///     .IsCurrentProject(proj), .GetProgress(proj), .GetTechprints(proj),
    ///     .AnyProjectIsAvailable, .SetCurrentProject(proj), .StopProject(proj)
    ///   Verse.ResearchProjectDef
    ///     .Cost, .ProgressReal, .ProgressPercent, .IsFinished, .CanStartNow,
    ///     .PrerequisitesCompleted, .TechprintCount, .TechprintsApplied,
    ///     .TechprintRequirementMet, .PlayerHasAnyAppropriateResearchBench,
    ///     .PlayerMechanitorRequirementMet, .AnalyzedThingsRequirementsMet,
    ///     .InspectionRequirementsMet, .IsHidden, .UnlockedDefs (List&lt;Def&gt;),
    ///     .CostFactor(TechLevel), .baseCost, .knowledgeCost, .knowledgeCategory,
    ///     .prerequisites, .hiddenPrerequisites, .requiredResearchBuilding,
    ///     .requiredResearchFacilities, .requiredAnalyzed, .techLevel, .tab,
    ///     .techprintCount, .hideWhen
    ///   Verse.DefDatabase&lt;ResearchProjectDef&gt;.AllDefsListForReading
    ///   Verse.ListerBuildings.allBuildingsColonist (public readonly List),
    ///     .ColonistsHaveResearchBench()
    ///   RimWorld.Building_ResearchBench, RimWorld.CompAffectedByFacilities
    ///     .LinkedFacilitiesListForReading, .IsFacilityActive(Thing)
    ///   RimWorld.CompPowerTrader.PowerOn
    ///   RimWorld.WorkTypeDefOf.Research, RimWorld.SkillDefOf.Intellectual
    ///   RimWorld.TutorSystem.Notify_Event(EventPack), RimWorld.EventPack(string)
    ///   RimWorld.Difficulty.AllowedBy(DifficultyConditionConfig)
    ///
    /// ## The hazards this tool had to dodge
    ///
    /// ### `ResearchProjectDef.CostApparent` and `.ProgressApparent` call
    /// `Faction.OfPlayer`
    ///
    /// Their bodies are `Cost * CostFactor(Faction.OfPlayer.def.techLevel)`.
    /// `Faction.OfPlayer` is `OfPlayerSilentFail` followed by `Log.Error`, and
    /// `Verse.Log.Error` calls `TickManager.Pause()` — the same trap
    /// `home/list_buildings` and `home/pawn_config` document. Neither property is
    /// read here. `costApparent` is reconstructed from the def's own public
    /// `CostFactor(TechLevel)` over `Faction.OfPlayerSilentFail.def.techLevel`,
    /// and is **null** with `costApparentReadable:false` when there is no player
    /// faction, rather than silently falling back to the raw cost.
    ///
    /// ### `Pawn_SkillTracker.GetSkill` calls `Log.Error` on a miss
    ///
    /// Its tail is `Log.Error("Did not find skill of def ...")` followed by
    /// `return skills[0]` — so a pawn whose skill list does not contain
    /// Intellectual would pause the colony AND report the WRONG skill's level.
    /// `pawn.skills.skills` is walked by reference here instead, exactly as
    /// `home/list_pawns` walks it, and a pawn with no record reports
    /// `intellectual: null`.
    ///
    /// ### `Pawn_WorkSettings.WorkIsActive` is not a pure read
    ///
    /// `WorkIsActive` opens with `ConfirmInitializedDebug()`, whose whole body is
    /// `if (priorities == null) { Log.Error(...); EnableAndInitialize(); }` — it
    /// pauses the colony and assigns the pawn six jobs. `EverWork` is
    /// `priorities != null` and calls no guard, so it is tested FIRST and
    /// `WorkIsActive` is never reached otherwise. This is the same rule
    /// `home/pawn_config` states for `GetPriority`.
    ///
    /// ### `ResearchManager.CurrentAnomalyKnowledgeProjects` can be NULL
    ///
    /// Its getter calls `EnsureKnowledgeProjectsInitialized()`, which **returns
    /// immediately when `ModsConfig.AnomalyActive` is false** — leaving the
    /// backing list null. `GetProject(someCategory)` then `foreach`es over null
    /// and throws. So the per-category slots are only touched when Anomaly is
    /// active; without it `currentByCategory` is an empty object and
    /// `anomalyActive` is false, which is a different thing from "no category
    /// has a project".
    ///
    /// ### `ResearchManager.GetProgress` INSERTS a zero
    ///
    /// Read the source: on a miss it does `progress.Add(proj, 0f); return 0f;` —
    /// and `progress` is scribed into the save. `IsFinished`, `CanStartNow` and
    /// `ProgressPercent` all route through it, so **any** honest read of research
    /// state adds a zero row per project the colony has never touched. This is
    /// exactly what vanilla does the first time a player opens the Research tab
    /// (the tree draws every node's `ProgressPercent`), and a zero row is
    /// semantically identical to an absent one. It is written down here because
    /// "this read is free" would be false, and `notes.progressDictionaryNote`
    /// says it in the payload too. The progress NUMBER itself is read from the
    /// private `progress` dictionary by reflection, with no insert, so at least
    /// the reported value costs nothing; `progressReadMethod` says which path
    /// answered.
    ///
    /// ### The write does what the Research tab's button does, minus the UI
    ///
    /// `MainTabWindow_Research.DoBeginResearch` is exactly three statements:
    ///
    ///     SoundDefOf.ResearchStart.PlayOneShotOnCamera();
    ///     Find.ResearchManager.SetCurrentProject(projectToStart);
    ///     TutorSystem.Notify_Event("StartResearchProject");
    ///     (+ a Messages.Message when the colony has no bench)
    ///
    /// This tool calls the middle two. The sound is UI and is skipped — WANTED 0
    /// (watchability) is the item that will decide whether a write should be
    /// audible or visible, and guessing now would prejudge it. The
    /// no-bench message is skipped because `researchBenches{}` answers the same
    /// question in the reply, in writing. `AttemptBeginResearch`'s Ideology
    /// confirmation dialog (unlocked defs whose memes the colony lacks) is also
    /// skipped: it is a confirmation, and a caller who passed `dryRun:false`
    /// has confirmed. `missingMemeUnlocks[]` on the write row reports what that
    /// dialog would have listed, so nothing is hidden by not asking.
    ///
    /// `SetCurrentProject` only assigns `currentProj` when `proj.baseCost > 0`;
    /// an Anomaly knowledge project (baseCost 0, knowledgeCost > 0) goes into its
    /// knowledge-category slot instead. The `after` here is read back from the
    /// game through `GetProject()` and `GetProject(category)`, so which slot
    /// moved is visible rather than assumed.
    /// </summary>
    public sealed class HomeResearchTools
    {
        private const string ToolName = "home/research";

        /// <summary>Candidates listed when a `set` name is ambiguous. A refusal
        /// that prints two hundred names is a refusal nobody reads.</summary>
        private const int MaxCandidates = 25;

        /// <summary>Unlocked defs listed per project under `unlocks:true`.
        /// The remainder is reported as a count, never dropped in silence.</summary>
        private const int MaxUnlocksPerProject = 40;

        [Tool(
            ToolName,
            Title = "Read research state, and choose the current project",
            Description =
                "The whole Research tab in one call, with no tab opened and nothing selected. Always: the project being researched "
                + "with its progress, every project that CanStartNow, and researchBenches{} — how many benches the colony has, "
                + "whether any is powered, which facilities are linked, and which colonists have the Research work type switched on "
                + "with their Intellectual level (that block is the answer to 'why is nobody researching'). Opt in to locked:true "
                + "(every project that cannot start yet, each with the prerequisites, building, facilities or techprints it is still "
                + "missing), finished:true (the defNames already researched) and unlocks:true (what each listed project unlocks). "
                + "filter:'<substring>' narrows every list by label or defName, case-insensitively; the four counts are of the WHOLE "
                + "database either way, so a narrowed list is still accounted for. set:'<defName or label>' is the write side and is "
                + "a DRY RUN until dryRun:false; it refuses a project that is finished or cannot start and says which requirement is "
                + "missing, and refuses an ambiguous name listing the candidates.",
            ResultDescription =
                "success, tool, mapName, current{} (or null with currentNote), currentByCategory{} + anomalyActive, available[], "
                + "locked[] and finished[] when asked, availableCount/lockedCount/finishedCount/hiddenCount/totalCount over the whole "
                + "database plus the *Listed counts after the filter, researchBenches{count,anyPowered,benches[],facilities[],"
                + "researchers[]}, anyProjectIsAvailable, blocks{} and filter{} saying what was asked, write{} (null when no set was "
                + "given) with requested/resolved/refused/reason/candidates/before/after/changed, dryRun, applied, and notes.")]
        [ToolResponse("current", "object", "The project being researched: defName, label, progress (points), cost, progressPercent, techLevel, tab and the rest of a project row. NULL when no project is selected - which is exactly what the game's 'Need research project' alert means. currentNote always says which case this is.", Always = true, Nullable = true)]
        [ToolResponse("currentNote", "string", "Why current is what it is, in words. Never absent, so a null current is never a silent one.", Always = true)]
        [ToolResponse("currentByCategory", "object", "Anomaly's per-knowledge-category current projects, keyed by KnowledgeCategoryDef defName; the value is a project row or null for a category with nothing selected. EMPTY OBJECT when the Anomaly DLC is not active - see anomalyActive, which separates 'no categories exist' from 'no category has a project'.", Always = true)]
        [ToolResponse("available", "array", "Every project whose CanStartNow is true, cost ascending. Each row carries prerequisitesCompleted (always true here, by definition). Narrowed by filter; availableCount is the unfiltered total.", Always = true)]
        [ToolResponse("locked", "array", "Only when locked:true. Every project that is not finished, not hidden and cannot start yet, each with missingPrerequisites[], missingBuilding, missingFacilities[], techprintsNeeded/techprintsApplied and lockReasons[]. Absent when not asked - blocks.locked says which.", Nullable = true)]
        [ToolResponse("finished", "array", "Only when finished:true. The defNames of every completed project, sorted. Absent when not asked.", Nullable = true)]
        [ToolResponse("researchBenches", "object", "count, anyPowered, poweredCount, benches[] (defName, label, pos, powered, facilities[]), facilities[] (distinct linked+active facility defNames), colonistsHaveResearchBench, researchers[] (colonists with the Research work type switched on: name, thingId, priority, intellectual level, passion) and researcherCount. Never null: a colony with no bench reports count 0, not an absent block.", Always = true)]
        [ToolResponse("totalCount", "number", "Every ResearchProjectDef in the database. availableCount + lockedCount + finishedCount + hiddenCount == totalCount always; the four buckets are disjoint and exhaustive, and hiddenCount is the Anomaly entity-codex projects the game itself will not show.", Always = true)]
        [ToolResponse("write", "object", "NULL when no `set` was given. Otherwise: requested, resolved{defName,label}, refused (bool), reason, candidates[] when the name was ambiguous, before{} and after{} (the current project, after being READ BACK from the game on a real run), changed (bool) and appliedNote.", Always = true, Nullable = true)]
        [ToolResponse("watch", "object", "The decorative half of the write: shown (bool), selected, inspectTab, mainTab (the MainButtonDef defName - Research on a real set), cameraMoved, leadMs, closesAfterSeconds, note and reason. shown:false with a reason on a dry run, a refusal, a read-only call or watch:false. The Research tab is opened BEFORE the project is chosen and closes itself afterwards; it never changes what is written.", Always = true)]
        [ToolResponse("dryRun", "boolean", "True = nothing was written. Defaults to TRUE; a caller must pass dryRun:false deliberately. Meaningless without `set`, and reported anyway so it is never mistaken for absent.", Always = true)]
        [ToolResponse("applied", "boolean", "True only when SetCurrentProject was actually called. False on every dry run, on every refusal, and on every read-only call.", Always = true)]
        [ToolResponse("unknownArguments", "array", "Every argument key the caller sent that this tool does not declare, sorted, case-sensitively. Empty array = every key was recognised. On a tool with a write side this matters twice over: a misspelled dryRun is the difference between a plan and a changed colony. The host's own _rimBridgeTimeoutMs is never listed.", Always = true)]
        [ToolResponse("unknownArgumentsWarning", "string", "Present only when unknownArguments is non-empty, or when the caller's raw keys could not be read at all - in which case the empty unknownArguments means 'not known', not 'nothing unknown'.", Nullable = true)]
        public async Task<object> Research(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            [ToolParameter(Description = "Also list every project that cannot start yet, with what each one is still missing: unfinished prerequisites, the research building it needs, unlinked facilities, techprints not yet applied. Off by default; lockedCount is reported either way.", DefaultValue = false)] bool locked = false,
            [ToolParameter(Description = "Also list the defNames of every finished project. Cheap (names only). Off by default; finishedCount is reported either way.", DefaultValue = false)] bool finished = false,
            [ToolParameter(Description = "Add unlocks[] to every listed project: the buildings, recipes, plants, terrain and surgeries it makes available, from the game's own ResearchProjectDef.UnlockedDefs. Off by default because it roughly doubles the payload.", DefaultValue = false)] bool unlocks = false,
            [ToolParameter(Description = "Case-insensitive substring on label or defName. Narrows available[], locked[] and finished[] together. The *Count fields stay unfiltered totals and the *Listed fields say how many survived, so a filter can never look like an empty database.")] string filter = null,
            [ToolParameter(Description = "WRITE: make this the current research project. Takes an exact defName, an exact label (case-insensitive), or a unique substring of either; an ambiguous substring is REFUSED with the candidates listed. A finished project, or one that cannot start yet, is refused with the requirement that is missing. Omit for a read-only call.")] string set = null,
            [ToolParameter(Description = "TRUE by default. Resolve the `set` name, check every requirement and report what WOULD happen without touching the game. Pass false to actually select the project.", DefaultValue = true)] bool dryRun = true,
            [ToolParameter(Description = "TRUE by default. On a real `set`, open the Research tab a moment before the project is chosen so a viewer sees it land, then close it again. Decorative only: it never changes what is written. Pass false to write with no UI.", DefaultValue = true)] bool watch = true,
            [ToolParameter(Description = "How long the Research tab stays open after the write, in seconds. Clamped 1..60. Ignored when watch is false or the write is a dry run.", DefaultValue = 8)] int watchSeconds = 8)
        {
            return BridgeCommon.WithUnknownArguments(
                await ResearchCore(ctx, cancellationToken, locked, finished, unlocks, filter, set, dryRun, watch, watchSeconds)
                    .ConfigureAwait(false),
                ctx, typeof(HomeResearchTools), ToolName);
        }

        private async Task<object> ResearchCore(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            bool locked,
            bool finished,
            bool unlocks,
            string filter,
            string set,
            bool dryRun,
            bool watch,
            int watchSeconds)
        {
            if (ctx?.MainThread == null)
                return Failure("No RimBridge main-thread dispatcher is available for this invocation.");

            // Arguments are parsed OUT here, before the hop, per the house rule.
            var needle = string.IsNullOrEmpty(filter) ? null : filter.Trim();
            if (needle != null && needle.Length == 0)
                needle = null;
            var setSpec = string.IsNullOrEmpty(set) ? null : set.Trim();
            if (setSpec != null && setSpec.Length == 0)
                setSpec = null;

            // Companion tools are dispatched with MarshalToMainThread = false, so
            // every def read, every bench walk, the write and the read-back all go
            // inside ONE hop. It matters most for the write: a tick between
            // SetCurrentProject and the read-back would make `after` the answer to
            // a different question.
            // Hop 1: the whole read, the name resolved, every requirement
            // checked, and - on a real write with watch on - the Research tab
            // opened BEFORE anything is chosen, so a viewer sees the change land
            // inside the open tab rather than after it.
            var pass = await ctx.MainThread
                .InvokeAsync(() => Pass1(ctx, locked, finished, unlocks, needle, setSpec, dryRun, watch), cancellationToken)
                .ConfigureAwait(false);

            if (pass.Failure != null)
            {
                var failed = pass.Failure as Dictionary<string, object>;
                if (failed != null)
                    failed["watch"] = Watch.Skipped("refused");
                return pass.Failure;
            }

            if (!pass.WriteWanted)
            {
                pass.Payload["watch"] = Watch.Skipped(pass.SkipReason);
                return pass.Payload;
            }

            // Off the main thread: a moment with the tab open and nothing
            // changed yet.
            await Watch.Lead(pass.Session, cancellationToken).ConfigureAwait(false);

            // Hop 2: the write, the read-back and the scheduled close. The name
            // is resolved again here rather than carried across the gap.
            var session = pass.Session;
            var payload = pass.Payload;
            var manager = pass.Manager;
            var all = pass.All;
            var tech = pass.PlayerTech;
            return await ctx.MainThread
                .InvokeAsync(() =>
                {
                    bool applied;
                    payload["write"] = PlanSet(manager, all, setSpec, false, tech, out applied);
                    payload["applied"] = applied;
                    payload["watch"] = session == null ? Watch.Skipped("watch:false") : Watch.Finish(session, watchSeconds);
                    return (object)payload;
                }, cancellationToken)
                .ConfigureAwait(false);
        }

        /// <summary>What hop 1 hands to hop 2: the reply so far, and the handles
        /// the write needs. Failure non-null means stop and return it.</summary>
        private sealed class Pass1Result
        {
            internal object Failure;
            internal Dictionary<string, object> Payload;
            internal ResearchManager Manager;
            internal List<ResearchProjectDef> All;
            internal TechLevel? PlayerTech;
            internal bool WriteWanted;
            internal Watch.Session Session;
            internal string SkipReason;
        }

        // =================================================================== run

        private static Pass1Result Pass1(IRimBridgeContext ctx, bool wantLocked, bool wantFinished, bool wantUnlocks,
                                         string needle, string setSpec, bool dryRun, bool wantWatch)
        {
            Map map;
            string mapError;
            if (!BridgeCommon.TryGetMap(ToolName, out map, out mapError))
                return new Pass1Result { Failure = Failure(mapError) };

            var manager = SafeManager();
            if (manager == null)
                return new Pass1Result { Failure = Failure("Find.ResearchManager was not readable; there is no research state to report.") };

            List<ResearchProjectDef> all;
            try { all = DefDatabase<ResearchProjectDef>.AllDefsListForReading; }
            catch { all = null; }
            if (all == null)
                return new Pass1Result { Failure = Failure("DefDatabase<ResearchProjectDef>.AllDefsListForReading was not readable.") };

            var anomalyActive = BridgeCommon.Try(() => ModsConfig.AnomalyActive, false);
            var playerTech = PlayerTechLevel();

            // ------------------------------------------------ classify, once
            // Four disjoint buckets, in this order, so every def lands in
            // exactly one and the counts always sum to totalCount:
            //   finished  -> IsFinished
            //   hidden    -> IsHidden (Anomaly entity-codex; IsHidden is false
            //                for a finished project, so this cannot overlap)
            //   available -> CanStartNow
            //   locked    -> everything else
            var finishedDefs = new List<ResearchProjectDef>();
            var hiddenDefs = new List<ResearchProjectDef>();
            var availableDefs = new List<ResearchProjectDef>();
            var lockedDefs = new List<ResearchProjectDef>();
            var unreadable = new List<object>();

            for (var i = 0; i < all.Count; i++)
            {
                var def = all[i];
                if (def == null)
                    continue;

                bool? isFinished = BridgeCommon.TryN(() => def.IsFinished);
                if (isFinished == null)
                {
                    unreadable.Add(new Dictionary<string, object>
                    {
                        { "defName", BridgeCommon.SafeString(() => def.defName) },
                        { "reason", "ResearchProjectDef.IsFinished threw; the project is in totalCount and in no bucket." }
                    });
                    continue;
                }
                if (isFinished.Value) { finishedDefs.Add(def); continue; }

                if (BridgeCommon.Try(() => def.IsHidden, false)) { hiddenDefs.Add(def); continue; }

                bool? canStart = BridgeCommon.TryN(() => def.CanStartNow);
                if (canStart == null)
                {
                    unreadable.Add(new Dictionary<string, object>
                    {
                        { "defName", BridgeCommon.SafeString(() => def.defName) },
                        { "reason", "ResearchProjectDef.CanStartNow threw; the project is in totalCount and in no bucket." }
                    });
                    continue;
                }
                if (canStart.Value) availableDefs.Add(def); else lockedDefs.Add(def);
            }

            // -------------------------------------------------------- current
            var currentProj = BridgeCommon.Try(() => manager.GetProject(null), (ResearchProjectDef)null);
            var current = currentProj == null
                ? null
                : Row(manager, currentProj, playerTech, wantUnlocks, false);

            var byCategory = new Dictionary<string, object>(StringComparer.Ordinal);
            var categoryNote = anomalyActive
                ? "Anomaly is active, so each KnowledgeCategoryDef has its own current-project slot; GetProject(category) reads them. A category with nothing chosen is null."
                : "The Anomaly DLC is not active, so no knowledge categories exist and ResearchManager.CurrentAnomalyKnowledgeProjects is null - it is NOT read here, because GetProject(category) would enumerate that null and throw. An empty object means 'no such thing on this install', not 'nothing selected'.";
            if (anomalyActive)
            {
                List<KnowledgeCategoryDef> categories;
                try { categories = DefDatabase<KnowledgeCategoryDef>.AllDefsListForReading.ToList(); }
                catch { categories = null; }
                if (categories == null)
                {
                    categoryNote = "Anomaly is active but DefDatabase<KnowledgeCategoryDef> was not readable; the empty object means 'not looked at'.";
                }
                else
                {
                    foreach (var category in categories)
                    {
                        if (category == null)
                            continue;
                        var key = BridgeCommon.SafeString(() => category.defName) ?? "unnamed";
                        var proj = BridgeCommon.Try(() => manager.GetProject(category), (ResearchProjectDef)null);
                        byCategory[key] = proj == null ? null : Row(manager, proj, playerTech, wantUnlocks, false);
                    }
                }
            }

            // --------------------------------------------------------- lists
            var availableRows = availableDefs
                .Where(d => Matches(d, needle))
                .OrderBy(d => BridgeCommon.Try(() => d.Cost, 0f))
                .ThenBy(d => BridgeCommon.SafeString(() => d.label) ?? string.Empty, StringComparer.OrdinalIgnoreCase)
                .Select(d => (object)Row(manager, d, playerTech, wantUnlocks, false))
                .ToList();

            var payload = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "success", true },
                { "tool", ToolName },
                { "mapName", BridgeCommon.SafeString(() => map.Parent == null ? null : map.Parent.Label) },
                { "anomalyActive", anomalyActive },
                { "playerTechLevel", playerTech == null ? null : playerTech.Value.ToString() },
                { "current", current },
                { "currentNote", currentProj == null
                    ? "NO PROJECT IS SELECTED. ResearchManager.GetProject() returned null - this is exactly the state the game's `Need research project` alert reports, and researchers will do no research until something is set. Use set:\"<name>\" (dry run first) to choose one."
                    : "current is ResearchManager.GetProject() with a null category: the main research slot. On Anomaly, knowledge projects live in currentByCategory{} instead and never appear here." },
                { "currentByCategory", byCategory },
                { "currentByCategoryNote", categoryNote },
                { "anyProjectIsAvailable", BridgeCommon.Try(() => manager.AnyProjectIsAvailable, false) },
                { "available", availableRows },
                { "availableCount", availableDefs.Count },
                { "availableListed", availableRows.Count },
                { "lockedCount", lockedDefs.Count },
                { "finishedCount", finishedDefs.Count },
                { "hiddenCount", hiddenDefs.Count },
                { "totalCount", all.Count },
                { "unreadableProjects", unreadable },
                { "blocks", new Dictionary<string, object>
                    {
                        { "locked", wantLocked },
                        { "finished", wantFinished },
                        { "unlocks", wantUnlocks }
                    } },
                { "filter", needle },
                { "filterApplied", needle != null }
            };

            if (wantLocked)
            {
                var lockedRows = lockedDefs
                    .Where(d => Matches(d, needle))
                    .OrderBy(d => BridgeCommon.Try(() => d.Cost, 0f))
                    .ThenBy(d => BridgeCommon.SafeString(() => d.label) ?? string.Empty, StringComparer.OrdinalIgnoreCase)
                    .Select(d => (object)Row(manager, d, playerTech, wantUnlocks, true))
                    .ToList();
                payload["locked"] = lockedRows;
                payload["lockedListed"] = lockedRows.Count;
            }

            if (wantFinished)
            {
                var finishedNames = finishedDefs
                    .Where(d => Matches(d, needle))
                    .Select(d => BridgeCommon.SafeString(() => d.defName))
                    .Where(n => n != null)
                    .OrderBy(n => n, StringComparer.Ordinal)
                    .Cast<object>()
                    .ToList();
                payload["finished"] = finishedNames;
                payload["finishedListed"] = finishedNames.Count;
            }

            payload["researchBenches"] = BenchBlock(map);

            // --------------------------------------------------------- write
            // The write is PLANNED here and applied in hop 2, so the Research
            // tab can be opened in between and the change is seen landing
            // inside it. A dry run and a refusal stop at the plan.
            var applied = false;
            object write = null;
            var result = new Pass1Result
            {
                Payload = payload,
                Manager = manager,
                All = all,
                PlayerTech = playerTech,
                SkipReason = "read-only call"
            };

            if (setSpec != null)
            {
                var plan = PlanSet(manager, all, setSpec, true, playerTech, out applied);
                write = plan;
                var refused = BridgeCommon.Bool(plan, "refused");
                if (refused)
                {
                    result.SkipReason = "refused";
                }
                else if (dryRun)
                {
                    result.SkipReason = "dry run";
                }
                else
                {
                    result.WriteWanted = true;
                    result.SkipReason = "watch:false";
                    if (wantWatch)
                        result.Session = Watch.Open(ctx, null, null, Watch.Tab("Research"), false);
                }
            }

            payload["write"] = write;
            payload["dryRun"] = dryRun;
            payload["applied"] = applied;
            payload["notes"] = Notes(dryRun, setSpec != null, needle);
            return result;
        }

        // ================================================================ rows

        /// <summary>One project. `lockDetail` adds the "what is still missing"
        /// fields, which are only meaningful for a project that cannot start.</summary>
        private static Dictionary<string, object> Row(ResearchManager manager, ResearchProjectDef def,
                                                      TechLevel? playerTech, bool wantUnlocks, bool lockDetail)
        {
            var row = new Dictionary<string, object>(StringComparer.Ordinal);
            row["defName"] = BridgeCommon.SafeString(() => def.defName);
            row["label"] = BridgeCommon.SafeString(() => def.label);

            var cost = BridgeCommon.TryN(() => def.Cost);
            row["cost"] = Round(cost);
            row["baseCost"] = Round(BridgeCommon.TryN(() => def.baseCost));
            row["knowledgeCost"] = Round(BridgeCommon.TryN(() => def.knowledgeCost));

            string progressMethod;
            var progress = Progress(manager, def, out progressMethod);
            row["progress"] = Round(progress);
            row["progressReadMethod"] = progressMethod;
            row["progressPercent"] = Round(BridgeCommon.TryN(() => def.ProgressPercent));

            // NOT CostApparent: its body calls Faction.OfPlayer, which calls
            // Log.Error, which pauses the game. CostFactor is public and pure.
            if (playerTech.HasValue && cost.HasValue)
            {
                var factor = BridgeCommon.TryN(() => def.CostFactor(playerTech.Value));
                row["costApparent"] = factor.HasValue ? Round(cost.Value * factor.Value) : null;
                row["costFactor"] = Round(factor);
                row["costApparentReadable"] = factor.HasValue;
            }
            else
            {
                row["costApparent"] = null;
                row["costFactor"] = null;
                row["costApparentReadable"] = false;
            }

            row["techLevel"] = BridgeCommon.SafeString(() => def.techLevel.ToString());
            row["tab"] = BridgeCommon.SafeString(() => def.tab == null ? null : def.tab.defName);
            row["tabLabel"] = BridgeCommon.SafeString(() => def.tab == null ? null : def.tab.label);
            row["knowledgeCategory"] = BridgeCommon.SafeString(
                () => def.knowledgeCategory == null ? null : def.knowledgeCategory.defName);

            row["finished"] = BridgeCommon.Try(() => def.IsFinished, false);
            row["canStartNow"] = BridgeCommon.Try(() => def.CanStartNow, false);
            row["prerequisitesCompleted"] = BridgeCommon.Try(() => def.PrerequisitesCompleted, false);
            row["isCurrent"] = BridgeCommon.Try(() => manager.IsCurrentProject(def), false);
            row["hidden"] = BridgeCommon.Try(() => def.IsHidden, false);
            row["allowedByDifficulty"] = AllowedByDifficulty(def);

            var techprintsNeeded = BridgeCommon.Try(() => def.TechprintCount, 0);
            row["techprintsNeeded"] = techprintsNeeded;
            row["techprintsApplied"] = techprintsNeeded > 0
                ? (object)BridgeCommon.Try(() => manager.GetTechprints(def), 0)
                : 0;
            row["techprintRequirementMet"] = BridgeCommon.Try(() => def.TechprintRequirementMet, true);

            row["prerequisites"] = Names(BridgeCommon.Try(() => def.prerequisites, null));
            row["hiddenPrerequisites"] = Names(BridgeCommon.Try(() => def.hiddenPrerequisites, null));
            row["requiredResearchBuilding"] = BridgeCommon.SafeString(
                () => def.requiredResearchBuilding == null ? null : def.requiredResearchBuilding.defName);
            row["requiredResearchFacilities"] = ThingNames(
                BridgeCommon.Try(() => def.requiredResearchFacilities, null));

            if (lockDetail)
                AddLockDetail(row, def);

            if (wantUnlocks)
            {
                List<Def> unlocked;
                try { unlocked = def.UnlockedDefs; }
                catch { unlocked = null; }
                if (unlocked == null)
                {
                    row["unlocks"] = null;
                    row["unlocksReadable"] = false;
                    row["unlockCount"] = null;
                }
                else
                {
                    var rows = new List<object>();
                    for (var i = 0; i < unlocked.Count && rows.Count < MaxUnlocksPerProject; i++)
                    {
                        var u = unlocked[i];
                        if (u == null)
                            continue;
                        rows.Add(new Dictionary<string, object>
                        {
                            { "defName", BridgeCommon.SafeString(() => u.defName) },
                            { "label", BridgeCommon.SafeString(() => u.label) },
                            { "type", u.GetType().Name }
                        });
                    }
                    row["unlocks"] = rows;
                    row["unlockCount"] = unlocked.Count;
                    row["unlocksNotListed"] = Math.Max(0, unlocked.Count - rows.Count);
                    row["unlocksReadable"] = true;
                }
            }

            return row;
        }

        /// <summary>Why this project cannot start, in the same order the Research
        /// tab's own locked-reason list builds them, and with the specific things
        /// that are missing rather than the tab's one-line translations.</summary>
        private static void AddLockDetail(IDictionary<string, object> row, ResearchProjectDef def)
        {
            var reasons = new List<object>();

            var missingPrereqs = new List<object>();
            foreach (var list in new[] { BridgeCommon.Try(() => def.prerequisites, null),
                                         BridgeCommon.Try(() => def.hiddenPrerequisites, null) })
            {
                if (list == null)
                    continue;
                foreach (var p in list)
                {
                    if (p == null)
                        continue;
                    if (BridgeCommon.Try(() => p.IsFinished, false))
                        continue;
                    var name = BridgeCommon.SafeString(() => p.defName);
                    if (name != null && !missingPrereqs.Contains(name))
                        missingPrereqs.Add(name);
                }
            }
            row["missingPrerequisites"] = missingPrereqs;
            if (missingPrereqs.Count > 0)
                reasons.Add("prerequisites not completed: " + string.Join(", ", missingPrereqs.Select(o => o.ToString()).ToArray()));

            // The building. requiredResearchBuilding names the def a bench must
            // BE; PlayerHasAnyAppropriateResearchBench is the game's own answer to
            // "does the colony have one that would do", and it ignores power on
            // purpose (a bench with the breaker off still unlocks the project).
            var buildingDef = BridgeCommon.SafeString(
                () => def.requiredResearchBuilding == null ? null : def.requiredResearchBuilding.defName);
            var hasBench = BridgeCommon.Try(() => def.PlayerHasAnyAppropriateResearchBench, false);
            row["missingBuilding"] = hasBench ? null : (object)(buildingDef ?? "ResearchBench");
            if (!hasBench)
                reasons.Add("no research bench the colony owns can host it"
                            + (buildingDef == null ? "" : " (needs " + buildingDef + ")"));

            // Facilities are a property of the BENCH, not of the colony, so the
            // honest answer is "these are required and no owned bench has them
            // all linked and active" - which is what !hasBench already says when
            // requiredResearchFacilities is the reason. They are listed either
            // way so a caller can go and build the multi-analyzer.
            var facilities = ThingNames(BridgeCommon.Try(() => def.requiredResearchFacilities, null));
            row["missingFacilities"] = hasBench ? new List<object>() : facilities;
            row["requiredFacilitiesAll"] = facilities;

            var techprintsNeeded = BridgeCommon.Try(() => def.TechprintCount, 0);
            if (techprintsNeeded > 0 && !BridgeCommon.Try(() => def.TechprintRequirementMet, true))
                reasons.Add("techprints applied " + row["techprintsApplied"] + " of " + techprintsNeeded);

            var mechanitor = BridgeCommon.Try(() => def.PlayerMechanitorRequirementMet, true);
            row["missingMechanitor"] = !mechanitor;
            if (!mechanitor)
                reasons.Add("no mechanitor in the player faction");

            var analyzed = BridgeCommon.Try(() => def.AnalyzedThingsRequirementsMet, true);
            row["missingAnalyzed"] = analyzed
                ? new List<object>()
                : ThingNames(BridgeCommon.Try(() => def.requiredAnalyzed, null));
            if (!analyzed)
                reasons.Add("required things have not been studied");

            var inspection = BridgeCommon.Try(() => def.InspectionRequirementsMet, true);
            row["missingInspection"] = !inspection;
            if (!inspection)
                reasons.Add("a grav engine has not been inspected");

            if (reasons.Count == 0)
                reasons.Add("CanStartNow is false but no individual requirement reported as unmet; the game itself logs this case as an error. Treat it as unknown, not as satisfied.");

            row["lockReasons"] = reasons;
        }

        // =========================================================== benches

        private static Dictionary<string, object> BenchBlock(Map map)
        {
            var block = new Dictionary<string, object>(StringComparer.Ordinal);
            var benches = new List<object>();
            var facilityNames = new List<string>();
            var powered = 0;
            var skipped = new List<object>();

            List<Building> buildings = null;
            try { buildings = map.listerBuildings == null ? null : map.listerBuildings.allBuildingsColonist; }
            catch { buildings = null; }

            if (buildings == null)
            {
                block["count"] = null;
                block["benches"] = null;
                block["readable"] = false;
                skipped.Add("map.listerBuildings.allBuildingsColonist was not readable; bench data is NOT READ, not zero.");
            }
            else
            {
                // allBuildingsColonist is the player's own list, so no faction
                // lookup is needed - and Faction.OfPlayer is the one that pauses.
                for (var i = 0; i < buildings.Count; i++)
                {
                    var bench = buildings[i] as Building_ResearchBench;
                    if (bench == null)
                        continue;

                    var row = new Dictionary<string, object>(StringComparer.Ordinal);
                    row["defName"] = BridgeCommon.SafeString(() => bench.def == null ? null : bench.def.defName);
                    row["label"] = BridgeCommon.SafeString(() => bench.LabelCap.ToString());
                    row["pos"] = BridgeCommon.PositionOf(bench);

                    var trader = SafeComp<CompPowerTrader>(bench);
                    if (trader == null)
                    {
                        // A hand research bench has no power comp at all. That is
                        // "needs no power", not "unpowered".
                        row["needsPower"] = false;
                        row["powered"] = true;
                        row["powerNote"] = "This bench has no CompPowerTrader: it needs no power.";
                        powered++;
                    }
                    else
                    {
                        var on = BridgeCommon.Try(() => trader.PowerOn, false);
                        row["needsPower"] = true;
                        row["powered"] = on;
                        row["powerNote"] = on ? "Powered." : "NOT POWERED - nobody can research at this bench.";
                        if (on) powered++;
                    }

                    var linked = new List<object>();
                    var affected = SafeComp<CompAffectedByFacilities>(bench);
                    if (affected != null)
                    {
                        List<Thing> facilities = null;
                        try { facilities = affected.LinkedFacilitiesListForReading; }
                        catch { facilities = null; }
                        if (facilities != null)
                        {
                            foreach (var facility in facilities)
                            {
                                if (facility == null)
                                    continue;
                                var name = BridgeCommon.SafeString(() => facility.def == null ? null : facility.def.defName);
                                var active = BridgeCommon.Try(() => affected.IsFacilityActive(facility), false);
                                linked.Add(new Dictionary<string, object>
                                {
                                    { "defName", name },
                                    { "label", BridgeCommon.SafeString(() => facility.LabelCap.ToString()) },
                                    { "active", active }
                                });
                                if (active && name != null && !facilityNames.Contains(name))
                                    facilityNames.Add(name);
                            }
                        }
                    }
                    row["facilities"] = linked;
                    row["facilityCount"] = linked.Count;
                    benches.Add(row);
                }

                block["count"] = benches.Count;
                block["benches"] = benches;
                block["readable"] = true;
            }

            block["poweredCount"] = buildings == null ? (object)null : powered;
            block["anyPowered"] = buildings == null ? (object)null : powered > 0;
            facilityNames.Sort(StringComparer.Ordinal);
            block["facilities"] = facilityNames.Cast<object>().ToList();
            block["colonistsHaveResearchBench"] = BridgeCommon.Try(
                () => map.listerBuildings != null && map.listerBuildings.ColonistsHaveResearchBench(), false);

            // ------------------------------------------------------ the people
            var researchers = new List<object>();
            var researcherCount = 0;
            WorkTypeDef researchWork = null;
            try { researchWork = WorkTypeDefOf.Research; }
            catch { researchWork = null; }

            SkillDef intellectual = null;
            try { intellectual = SkillDefOf.Intellectual; }
            catch { intellectual = null; }

            List<Pawn> colonists = null;
            try { colonists = map.mapPawns == null ? null : map.mapPawns.FreeColonistsSpawned.ToList(); }
            catch { colonists = null; }

            if (colonists == null || researchWork == null)
            {
                block["researchers"] = null;
                block["researcherCount"] = null;
                skipped.Add(colonists == null
                    ? "map.mapPawns.FreeColonistsSpawned was not readable; researchers are NOT READ, not none."
                    : "WorkTypeDefOf.Research was not resolvable; researchers are NOT READ, not none.");
            }
            else
            {
                foreach (var pawn in colonists)
                {
                    if (pawn == null)
                        continue;

                    var row = new Dictionary<string, object>(StringComparer.Ordinal);
                    row["name"] = BridgeCommon.SafeString(() => pawn.LabelShortCap.ToString());
                    row["thingId"] = BridgeCommon.SafeString(() => pawn.ThingID);

                    Pawn_WorkSettings settings = null;
                    try { settings = pawn.workSettings; }
                    catch { settings = null; }

                    // EverWork FIRST. GetPriority/WorkIsActive open with
                    // ConfirmInitializedDebug, which on a pawn with no priority
                    // table calls Log.Error (pausing the colony) and then writes
                    // one. See the class remarks.
                    var everWork = settings != null && BridgeCommon.Try(() => settings.EverWork, false);
                    var disabled = BridgeCommon.Try(() => pawn.WorkTypeIsDisabled(researchWork), false);
                    row["everWork"] = everWork;
                    row["disabled"] = disabled;

                    int? priority = null;
                    if (everWork && !disabled)
                        priority = BridgeCommon.TryN(() => settings.GetPriority(researchWork));
                    row["priority"] = priority;
                    var active = priority.HasValue && priority.Value > 0;
                    row["active"] = active;
                    row["priorityNote"] = everWork
                        ? (disabled ? "This colonist can never do Research; priority is null because it was not read."
                                    : "Pawn_WorkSettings.GetPriority(Research). 0 = never, 1 most urgent, 4 least.")
                        : "Pawn_WorkSettings.EverWork is false: this pawn has no priority table and reading one would have paused the game and assigned six jobs. Not zero - not looked at.";

                    // NOT skills.GetSkill: its miss path is Log.Error + return
                    // skills[0], i.e. pause the colony and answer with the wrong
                    // skill. Walk the list.
                    int? level = null;
                    string passion = null;
                    if (intellectual != null)
                    {
                        List<SkillRecord> records = null;
                        try { records = pawn.skills == null ? null : pawn.skills.skills; }
                        catch { records = null; }
                        if (records != null)
                        {
                            foreach (var record in records)
                            {
                                if (record == null || record.def != intellectual)
                                    continue;
                                level = BridgeCommon.TryN(() => record.Level);
                                passion = BridgeCommon.SafeString(() => record.passion.ToString());
                                break;
                            }
                        }
                    }
                    row["intellectual"] = level;
                    row["passion"] = passion;

                    if (active)
                        researcherCount++;
                    researchers.Add(row);
                }

                block["researchers"] = researchers;
                block["researcherCount"] = researchers.Count;
                block["activeResearcherCount"] = researcherCount;
            }

            block["skipped"] = skipped;
            block["note"] =
                "This block is the answer to 'why is nobody researching'. A colony needs: a project selected (see current), a bench "
                + "the project will accept (colonistsHaveResearchBench, and requiredResearchBuilding on the project row), that bench "
                + "POWERED if it has a power comp, and at least one colonist with Research switched on (activeResearcherCount > 0). "
                + "Any null here means NOT READ and says so in skipped[]; it never means zero.";
            return block;
        }

        // ============================================================== write

        /// <summary>Resolve the name, check the requirements, and on a real run
        /// do what the Research tab's Start button does minus the UI. Returns the
        /// write{} object; `applied` says whether the game was actually touched.</summary>
        private static Dictionary<string, object> PlanSet(ResearchManager manager, List<ResearchProjectDef> all,
                                                          string spec, bool dryRun, TechLevel? playerTech,
                                                          out bool applied)
        {
            applied = false;
            var write = new Dictionary<string, object>(StringComparer.Ordinal);
            write["requested"] = spec;

            var beforeProj = BridgeCommon.Try(() => manager.GetProject(null), (ResearchProjectDef)null);
            write["before"] = beforeProj == null ? null : Row(manager, beforeProj, playerTech, false, false);

            List<ResearchProjectDef> candidates;
            var target = Resolve(all, spec, out candidates);

            if (target == null)
            {
                var names = candidates
                    .Take(MaxCandidates)
                    .Select(d => (object)new Dictionary<string, object>
                    {
                        { "defName", BridgeCommon.SafeString(() => d.defName) },
                        { "label", BridgeCommon.SafeString(() => d.label) }
                    })
                    .ToList();
                write["resolved"] = null;
                write["candidates"] = names;
                write["candidateCount"] = candidates.Count;
                write["refused"] = true;
                write["reason"] = candidates.Count == 0
                    ? "No research project matches " + Quote(spec) + ". `set` takes an exact defName, an exact label "
                      + "(case-insensitive) or a unique substring of either. available[] in this same reply lists what can be started."
                    : candidates.Count + " projects match " + Quote(spec)
                      + " and none of them matches it exactly, so nothing was chosen. Pass a defName from candidates[]"
                      + (candidates.Count > MaxCandidates ? " (the first " + MaxCandidates + " are listed)" : "") + ".";
                write["after"] = write["before"];
                write["changed"] = false;
                write["appliedNote"] = "Refused before anything was read back; after{} is the same object as before{}.";
                return write;
            }

            write["resolved"] = new Dictionary<string, object>
            {
                { "defName", BridgeCommon.SafeString(() => target.defName) },
                { "label", BridgeCommon.SafeString(() => target.label) }
            };
            write["candidates"] = new List<object>();
            write["candidateCount"] = 0;

            var alreadyCurrent = BridgeCommon.Try(() => manager.IsCurrentProject(target), false);
            write["alreadyCurrent"] = alreadyCurrent;

            // The refusals, in the order the tab evaluates them.
            string refusal = null;
            if (BridgeCommon.Try(() => target.IsFinished, false))
            {
                refusal = Label(target) + " is already FINISHED. A finished project cannot be selected; "
                          + "finished:true lists every completed project.";
            }
            else if (!alreadyCurrent && !BridgeCommon.Try(() => target.CanStartNow, false))
            {
                var detail = new Dictionary<string, object>();
                AddLockDetail(detail, target);
                object reasons;
                detail.TryGetValue("lockReasons", out reasons);
                var list = reasons as List<object>;
                refusal = Label(target) + " CANNOT START YET: "
                          + (list == null || list.Count == 0
                                ? "CanStartNow is false and no reason could be read."
                                : string.Join("; ", list.Select(o => o.ToString()).ToArray()))
                          + ". Nothing was written.";
                write["lockDetail"] = detail;
            }

            if (refusal != null)
            {
                write["refused"] = true;
                write["reason"] = refusal;
                write["after"] = write["before"];
                write["changed"] = false;
                write["appliedNote"] = "Refused, so the game was not touched on this call whatever dryRun said.";
                return write;
            }

            write["refused"] = false;
            write["reason"] = null;
            write["missingMemeUnlocks"] = MissingMemeUnlocks(target);

            if (dryRun)
            {
                write["after"] = write["before"];
                write["changed"] = false;
                write["wouldChange"] = !alreadyCurrent;
                write["afterIsPredicted"] = true;
                write["appliedNote"] = alreadyCurrent
                    ? "DRY RUN. " + Label(target) + " is ALREADY the current project, so a real run would be a no-op."
                    : "DRY RUN. NOTHING WAS WRITTEN. A real run would make " + Label(target)
                      + " the current project. Run again with dryRun:false to apply.";
                return write;
            }

            // What MainTabWindow_Research.DoBeginResearch does, minus the sound,
            // minus the no-bench Message, minus the Ideology confirmation dialog.
            var wrote = false;
            string writeError = null;
            try
            {
                manager.SetCurrentProject(target);
                wrote = true;
            }
            catch (Exception ex)
            {
                writeError = "ResearchManager.SetCurrentProject threw " + ex.GetType().Name + ".";
            }

            if (wrote)
            {
                // The tab's third statement. Notify_Event returns immediately
                // unless TutorialMode, so on a normal game it is a no-op; it is
                // called anyway so the write path is the game's, not ours.
                try { TutorSystem.Notify_Event(new EventPack("StartResearchProject")); }
                catch { /* the tutor system is not worth failing a write over */ }

                HighlightInResearchTab(target);
            }

            applied = wrote;

            // READ BACK. Never an echo of the request: if the game declined the
            // assignment (SetCurrentProject ignores a project with baseCost 0),
            // the mismatch shows here rather than being reported as success.
            var afterProj = BridgeCommon.Try(() => manager.GetProject(null), (ResearchProjectDef)null);
            write["after"] = afterProj == null ? null : Row(manager, afterProj, playerTech, false, false);
            write["afterIsPredicted"] = false;
            write["changed"] = !SameDef(beforeProj, afterProj);
            write["wouldChange"] = !alreadyCurrent;

            var landed = SameDef(afterProj, target);
            write["landedInMainSlot"] = landed;
            if (!landed)
            {
                var category = BridgeCommon.SafeString(
                    () => target.knowledgeCategory == null ? null : target.knowledgeCategory.defName);
                write["landedInCategory"] = category;
            }

            write["appliedNote"] = writeError != null
                ? writeError + " applied is false and after{} was still read back from the game."
                : (landed
                    ? "Applied. after{} was READ BACK from ResearchManager.GetProject() after the write, not echoed from the request."
                      + (alreadyCurrent ? " It was already the current project, so before and after are the same and changed is false." : "")
                    : "SetCurrentProject was called but GetProject() does NOT return this project. That is what happens to a project "
                      + "with baseCost 0 (an Anomaly knowledge project): it goes to its knowledge-category slot instead, which "
                      + "currentByCategory{} in this same reply shows. Check landedInCategory.");
            return write;
        }

        /// <summary>Move the open Research tab's left pane onto the project that
        /// was just chosen, so the change is visible and not merely current. Does
        /// nothing unless the Research tab is the open main tab, which is only
        /// the case when the watch step opened it.
        ///
        /// `MainTabWindow_Research.Select(ResearchProjectDef)` is public and sets
        /// CurTab + selectedProject; the scroll-to needs the private
        /// `scrollPositioner` re-armed, because PreOpen armed it once and the
        /// frames drawn during the watch lead have already spent it. Entirely
        /// decorative and entirely guarded.</summary>
        private static void HighlightInResearchTab(ResearchProjectDef target)
        {
            if (target == null)
                return;
            try
            {
                var def = Watch.Tab("Research");
                if (def == null)
                    return;
                var root = Find.MainTabsRoot;
                if (root == null || !ReferenceEquals(root.OpenTab, def))
                    return;
                var window = def.TabWindow as MainTabWindow_Research;
                if (window == null)
                    return;
                window.Select(target);
                var field = BridgeCommon.PrivateInstanceField(typeof(MainTabWindow_Research), "scrollPositioner");
                var positioner = field == null ? null : field.GetValue(window) as ScrollPositioner;
                if (positioner != null)
                    positioner.Arm();
            }
            catch
            {
                // The tab is decoration; a research write never fails over it.
            }
        }

        /// <summary>Exact defName, then exact label, then a unique substring.
        /// A null return with a non-empty candidate list is an ambiguous name;
        /// a null return with an empty one is no match at all.</summary>
        private static ResearchProjectDef Resolve(List<ResearchProjectDef> all, string spec,
                                                  out List<ResearchProjectDef> candidates)
        {
            candidates = new List<ResearchProjectDef>();
            if (string.IsNullOrEmpty(spec))
                return null;

            foreach (var def in all)
            {
                if (def == null)
                    continue;
                var name = BridgeCommon.SafeString(() => def.defName);
                if (name != null && string.Equals(name, spec, StringComparison.OrdinalIgnoreCase))
                    return def;
            }

            var byLabel = all.Where(d => d != null
                    && string.Equals(BridgeCommon.SafeString(() => d.label) ?? string.Empty, spec,
                                     StringComparison.OrdinalIgnoreCase))
                .ToList();
            if (byLabel.Count == 1)
                return byLabel[0];
            if (byLabel.Count > 1)
            {
                candidates = byLabel;
                return null;
            }

            var bySubstring = all.Where(d => d != null && Matches(d, spec)).ToList();
            if (bySubstring.Count == 1)
                return bySubstring[0];
            candidates = bySubstring
                .OrderBy(d => BridgeCommon.SafeString(() => d.label) ?? string.Empty, StringComparer.OrdinalIgnoreCase)
                .ToList();
            return null;
        }

        /// <summary>The Ideology dialog's content, reported instead of shown.
        /// `Faction.OfPlayerSilentFail`, never `Faction.OfPlayer`.</summary>
        private static List<object> MissingMemeUnlocks(ResearchProjectDef def)
        {
            var rows = new List<object>();
            if (!BridgeCommon.Try(() => ModsConfig.IdeologyActive, false))
                return rows;

            Faction player;
            try { player = Faction.OfPlayerSilentFail; }
            catch { player = null; }
            if (player == null)
                return rows;

            Ideo primary;
            try { primary = player.ideos == null ? null : player.ideos.PrimaryIdeo; }
            catch { primary = null; }
            if (primary == null)
                return rows;

            List<Def> unlocked;
            try { unlocked = def.UnlockedDefs; }
            catch { return rows; }
            if (unlocked == null)
                return rows;

            foreach (var d in unlocked)
            {
                var buildable = d as BuildableDef;
                if (buildable == null)
                    continue;
                var missing = new List<object>();
                try
                {
                    foreach (var meme in DefDatabase<MemeDef>.AllDefsListForReading)
                    {
                        if (meme == null)
                            continue;
                        if (BridgeCommon.Try(() => player.ideos.HasAnyIdeoWithMeme(meme), true))
                            continue;
                        if (!BridgeCommon.Try(() => meme.AllDesignatorBuildables.Contains(buildable), false))
                            continue;
                        var label = BridgeCommon.SafeString(() => meme.defName);
                        if (label != null)
                            missing.Add(label);
                    }
                }
                catch { continue; }

                if (missing.Count > 0)
                {
                    rows.Add(new Dictionary<string, object>
                    {
                        { "defName", BridgeCommon.SafeString(() => buildable.defName) },
                        { "label", BridgeCommon.SafeString(() => buildable.label) },
                        { "memesTheColonyLacks", missing }
                    });
                }
            }
            return rows;
        }

        // ============================================================= notes

        private static Dictionary<string, object> Notes(bool dryRun, bool hadSet, string needle)
        {
            var notes = new Dictionary<string, object>(StringComparer.Ordinal)
            {
                { "countsAreUnfiltered",
                    "availableCount, lockedCount, finishedCount, hiddenCount and totalCount are over the WHOLE ResearchProjectDef "
                    + "database and ignore `filter`. The four buckets are disjoint and exhaustive in this order - finished, then "
                    + "hidden, then CanStartNow, then everything else - so available + locked + finished + hidden == totalCount, "
                    + "minus anything in unreadableProjects[]. availableListed / lockedListed / finishedListed are what survived the "
                    + "filter." },
                { "hiddenMeans",
                    "hiddenCount is ResearchProjectDef.IsHidden: an Anomaly project the entity codex has not revealed. The game will "
                    + "not show it and CanStartNow is false for it, so it is counted and never listed." },
                { "currentIsTheAlert",
                    "current: null IS the game's `Need research project` alert. Nothing else has to be inspected to confirm it." },
                { "progressDictionaryNote",
                    "ResearchManager.GetProgress INSERTS a zero into its saved progress dictionary for a project it has no row for, "
                    + "and IsFinished / CanStartNow / ProgressPercent all route through it - so reading research state at all adds a "
                    + "zero row per untouched project, exactly as vanilla does when the Research tab first draws the tree. A zero row "
                    + "means the same thing as no row. The progress NUMBER reported here is read from the private dictionary by "
                    + "reflection with no insert; progressReadMethod on each row says which path answered." },
                { "costApparent",
                    "costApparent is cost * ResearchProjectDef.CostFactor(the player faction's tech level) - the number the Research "
                    + "tab shows for a project above the colony's tech level. The def's own CostApparent property is NOT used: its "
                    + "body calls Faction.OfPlayer, whose failure path is Log.Error, which pauses the game. costApparentReadable is "
                    + "false when there is no player faction, and costApparent is null rather than the raw cost." },
                { "benchesAndPeople",
                    "researchBenches{} carries the people too, because 'nothing is being researched' has four possible causes and "
                    + "three of them are not the project: no bench, an unpowered bench, or nobody with Research switched on." },
                { "readOnlyExceptSet",
                    "Without `set` this tool writes nothing, opens no tab and changes no selection. With `set` it is still a DRY RUN "
                    + "until dryRun:false." }
            };

            if (hadSet)
            {
                notes["writeSemantics"] = dryRun
                    ? "DRY RUN: write.after is write.before and write.afterIsPredicted is true. Nothing was written."
                    : "Applied: write.after was READ BACK from ResearchManager.GetProject(), never echoed from the request. "
                      + "`applied` means SetCurrentProject was called; `changed` means the current project is a different def than "
                      + "before, which is false when you set the project that was already current.";
                notes["writeDoesWhatTheTabDoes"] =
                    "MainTabWindow_Research's Start button is SetCurrentProject + TutorSystem.Notify_Event(\"StartResearchProject\") "
                    + "plus a sound, a no-bench Message and an Ideology confirmation dialog. The first two are done here; the sound is "
                    + "left to WANTED 0 (watchability), the message is redundant with researchBenches{}, and the dialog's content is "
                    + "reported as write.missingMemeUnlocks[] instead of being popped.";
            }

            if (needle != null)
                notes["filter"] = "filter " + Quote(needle) + " is a case-insensitive substring on label OR defName, applied to "
                                  + "available[], locked[] and finished[] alike. It never touches the counts.";

            return notes;
        }

        // ========================================================== plumbing

        /// <summary>The private `Dictionary&lt;ResearchProjectDef,float&gt; progress`,
        /// so the reported number does not itself insert a row. Null if RimWorld
        /// ever renames it, in which case the caller falls back to GetProgress and
        /// says so.</summary>
        private static readonly FieldInfo ProgressField =
            BridgeCommon.PrivateInstanceField(typeof(ResearchManager), "progress");

        private static readonly FieldInfo AnomalyKnowledgeField =
            BridgeCommon.PrivateInstanceField(typeof(ResearchManager), "anomalyKnowledge");

        private static float? Progress(ResearchManager manager, ResearchProjectDef def, out string method)
        {
            var baseCost = BridgeCommon.Try(() => def.baseCost, 0f);
            var field = baseCost > 0f ? ProgressField : AnomalyKnowledgeField;
            if (field != null)
            {
                try
                {
                    var dict = field.GetValue(manager) as Dictionary<ResearchProjectDef, float>;
                    if (dict != null)
                    {
                        float stored;
                        method = "private dictionary, no insert";
                        return dict.TryGetValue(def, out stored) ? stored : 0f;
                    }
                }
                catch { /* fall through to the public accessor */ }
            }

            method = "ResearchManager.GetProgress (inserts a zero row for an untouched project)";
            return BridgeCommon.TryN(() => manager.GetProgress(def));
        }

        private static ResearchManager SafeManager()
        {
            try { return Find.ResearchManager; }
            catch { return null; }
        }

        private static TechLevel? PlayerTechLevel()
        {
            try
            {
                var player = Faction.OfPlayerSilentFail;
                if (player == null || player.def == null)
                    return null;
                return player.def.techLevel;
            }
            catch { return null; }
        }

        private static bool AllowedByDifficulty(ResearchProjectDef def)
        {
            try
            {
                var storyteller = Find.Storyteller;
                if (storyteller == null || storyteller.difficulty == null)
                    return true;
                return storyteller.difficulty.AllowedBy(def.hideWhen);
            }
            catch { return true; }
        }

        private static T SafeComp<T>(Thing thing) where T : ThingComp
        {
            try { return thing.TryGetComp<T>(); }
            catch { return null; }
        }

        private static bool Matches(ResearchProjectDef def, string needle)
        {
            if (needle == null)
                return true;
            var label = BridgeCommon.SafeString(() => def.label) ?? string.Empty;
            var name = BridgeCommon.SafeString(() => def.defName) ?? string.Empty;
            return label.IndexOf(needle, StringComparison.OrdinalIgnoreCase) >= 0
                || name.IndexOf(needle, StringComparison.OrdinalIgnoreCase) >= 0;
        }

        private static bool SameDef(ResearchProjectDef a, ResearchProjectDef b)
        {
            if (a == null && b == null)
                return true;
            if (a == null || b == null)
                return false;
            return ReferenceEquals(a, b);
        }

        private static string Label(ResearchProjectDef def)
        {
            var label = BridgeCommon.SafeString(() => def.label);
            var name = BridgeCommon.SafeString(() => def.defName);
            if (label == null)
                return name ?? "(unnamed project)";
            return name == null ? label : label + " (" + name + ")";
        }

        private static List<object> Names(List<ResearchProjectDef> defs)
        {
            var rows = new List<object>();
            if (defs == null)
                return rows;
            foreach (var def in defs)
            {
                if (def == null)
                    continue;
                var name = BridgeCommon.SafeString(() => def.defName);
                if (name != null)
                    rows.Add(name);
            }
            return rows;
        }

        private static List<object> ThingNames(List<ThingDef> defs)
        {
            var rows = new List<object>();
            if (defs == null)
                return rows;
            foreach (var def in defs)
            {
                if (def == null)
                    continue;
                var name = BridgeCommon.SafeString(() => def.defName);
                if (name != null)
                    rows.Add(name);
            }
            return rows;
        }

        private static object Round(float? value)
        {
            if (!value.HasValue)
                return null;
            return Math.Round((double)value.Value, 3, MidpointRounding.AwayFromZero);
        }

        private static string Quote(string s)
        {
            return "\"" + (s ?? string.Empty) + "\"";
        }

        /// <summary>The shared refusal shape; see BridgeCommon.Failure.</summary>
        private static object Failure(string error)
        {
            return BridgeCommon.Failure(ToolName, error);
        }
    }
}
