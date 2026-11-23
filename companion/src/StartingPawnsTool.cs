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
    /// <summary>Read the pawn-configuration page from GameInitData. Unlike
    /// home/list_pawns this deliberately requires no map.</summary>
    public sealed class HomeStartingPawnsTools
    {
        private const string ToolName = "home/starting_pawns";

        [Tool(ToolName, Title = "Read the new-colony starting pawns",
            Description = "Read Page_ConfigureStartingPawns without an active map: each selected and optional pawn, name, backstories, traits, incapable work tags, skills and passions, plus the team-skill summary and Randomize/Start controls. Read-only.",
            ResultDescription = "success, pageOpen, scenario, startingPawnCount, pawnCount, pawns[], teamSkills[], controls, notes.")]
        [ToolResponse("pageOpen", "boolean", "True only while Page_ConfigureStartingPawns is in the window stack.", Always = true)]
        [ToolResponse("pawns", "array", "GameInitData pawns in page order. selected is true for the pawns that will start; each row has name, childhood, adulthood, traits, incapableOf and skills.", Always = true)]
        [ToolResponse("teamSkills", "array", "The page's visible team-skill summary: the best selected pawn for each skill, comparing enabled state, level, then passion.", Always = true)]
        [ToolResponse("controls", "object", "Availability of Randomize (once per pawn) and Start on this page.", Always = true)]
        [ToolResponse("unknownArguments", "array", "Unrecognised argument names.", Always = true)]
        [ToolResponse("error", "string", "On a refusal only: why the page could not be read (no GameInitData outside a new-colony start).", Nullable = true)]
        public async Task<object> StartingPawns(IRimBridgeContext ctx, CancellationToken cancellationToken)
        {
            object reply;
            if (ctx?.MainThread == null)
                reply = BridgeCommon.Failure(ToolName, "No RimBridge main-thread dispatcher is available.");
            else
                reply = await ctx.MainThread.InvokeAsync(Build, cancellationToken).ConfigureAwait(false);
            return BridgeCommon.WithUnknownArguments(reply, ctx, typeof(HomeStartingPawnsTools), ToolName);
        }

        private static object Build()
        {
            var windows = Find.WindowStack?.Windows;
            bool pageOpen = windows != null && windows.Any(w => w is Page_ConfigureStartingPawns);
            var data = Find.GameInitData;
            if (data == null)
                return Payload(false, pageOpen, null, 0, new List<object>(), new List<object>(),
                    "No new-game pawn data exists yet.");

            var source = data.startingAndOptionalPawns ?? new List<Pawn>();
            int selectedCount = Math.Max(0, Math.Min(data.startingPawnCount, source.Count));
            var rows = new List<object>();
            for (int i = 0; i < source.Count; i++)
                rows.Add(PawnRow(source[i], i, i < selectedCount));

            return Payload(true, pageOpen, null, selectedCount,
                rows, TeamSkills(source.Take(selectedCount).Where(p => p != null).ToList()),
                pageOpen ? null : "Pawn data exists, but Page_ConfigureStartingPawns is not open; controls are not actionable.");
        }

        private static object Payload(bool success, bool pageOpen, string scenario, int selectedCount,
            List<object> pawns, List<object> team, string note)
        {
            var payload = new Dictionary<string, object> {
                { "success", success }, { "tool", ToolName }, { "pageOpen", pageOpen },
                { "scenario", scenario }, { "startingPawnCount", selectedCount },
                { "pawnCount", pawns.Count }, { "pawns", pawns }, { "teamSkills", team },
                { "controls", new Dictionary<string, object> {
                    { "randomize", pageOpen }, { "randomizePawnIndexes", pageOpen ? Enumerable.Range(0, pawns.Count).ToArray() : new int[0] },
                    { "start", pageOpen }
                }},
                { "notes", note == null ? new string[0] : new[] { note } }
            };
            if (!success)
                payload["error"] = note ?? "Page_ConfigureStartingPawns could not be read.";
            return payload;
        }

        private static object PawnRow(Pawn pawn, int index, bool selected)
        {
            if (pawn == null)
                return new Dictionary<string, object> { { "index", index }, { "selected", selected }, { "name", null }, { "error", "null pawn" } };
            var story = SafeValue(() => pawn.story);
            var gender = SafeValue(() => pawn.gender, Gender.None);
            var traits = new List<object>();
            var traitSet = story == null ? null : SafeValue(() => story.traits);
            if (traitSet?.allTraits != null)
                foreach (var t in traitSet.allTraits.Where(t => t != null))
                    traits.Add(new Dictionary<string, object> { { "label", Safe(() => t.LabelCap) }, { "defName", Safe(() => t.def?.defName) }, { "degree", SafeValue<object>(() => t.Degree) } });

            var skills = SkillRows(pawn);
            var incapable = SplitTags(SafeValue(() => pawn.CombinedDisabledWorkTags, WorkTags.None));
            return new Dictionary<string, object> {
                { "index", index }, { "selected", selected }, { "name", Safe(() => pawn.Name?.ToStringFull) ?? Safe(() => pawn.LabelShort) },
                { "childhood", story == null ? null : Safe(() => story.Childhood?.TitleCapFor(gender)) },
                { "adulthood", story == null ? null : Safe(() => story.Adulthood?.TitleCapFor(gender)) },
                { "traits", traits }, { "incapableOf", incapable }, { "skills", skills }
            };
        }

        private static List<object> SkillRows(Pawn pawn)
        {
            var result = new List<object>();
            var records = SafeValue(() => pawn.skills?.skills?.ToList()) ?? new List<SkillRecord>();
            var defs = SafeValue(() => DefDatabase<SkillDef>.AllDefsListForReading?.ToList()) ?? new List<SkillDef>();
            foreach (var def in defs.Where(d => d != null)) {
                var rec = records.FirstOrDefault(r => r != null && r.def == def);
                result.Add(new Dictionary<string, object> { { "name", def.defName }, { "label", def.skillLabel },
                    { "level", rec == null ? null : SafeValue<object>(() => rec.Level) },
                    { "passion", rec == null ? "None" : Safe(() => rec.passion.ToString()) },
                    { "disabled", rec != null && SafeValue(() => rec.TotallyDisabled, false) } });
            }
            return result;
        }

        private static List<object> TeamSkills(List<Pawn> pawns)
        {
            var result = new List<object>();
            var defs = SafeValue(() => DefDatabase<SkillDef>.AllDefsListForReading?.ToList()) ?? new List<SkillDef>();
            foreach (var def in defs.Where(d => d != null && d.pawnCreatorSummaryVisible)) {
                Pawn best = null; SkillRecord bestRec = null;
                foreach (var pawn in pawns) {
                    var rec = SafeValue(() => pawn.skills?.skills?.FirstOrDefault(r => r != null && r.def == def));
                    if (rec == null) continue;
                    bool better = bestRec == null || (!rec.TotallyDisabled && (bestRec.TotallyDisabled || rec.Level > bestRec.Level || (rec.Level == bestRec.Level && rec.passion > bestRec.passion)));
                    if (better) { best = pawn; bestRec = rec; }
                }
                result.Add(new Dictionary<string, object> { { "name", def.defName }, { "label", def.skillLabel },
                    { "level", bestRec == null ? null : SafeValue<object>(() => bestRec.Level) },
                    { "passion", bestRec == null ? "None" : Safe(() => bestRec.passion.ToString()) },
                    { "disabled", bestRec != null && SafeValue(() => bestRec.TotallyDisabled, false) },
                    { "pawn", best == null ? null : Safe(() => best.Name?.ToStringFull) } });
            }
            return result;
        }

        private static List<object> SplitTags(WorkTags tags)
        {
            var rows = new List<object>();
            foreach (WorkTags tag in Enum.GetValues(typeof(WorkTags)))
                if (tag != WorkTags.None && Enum.IsDefined(typeof(WorkTags), tag) && (tags & tag) == tag)
                    rows.Add(Safe(() => WorkTypeDefsUtility.LabelTranslated(tag)) ?? tag.ToString());
            return rows;
        }

        private static string Safe(Func<string> read) { try { return read(); } catch { return null; } }
        private static T SafeValue<T>(Func<T> read, T fallback = default(T)) { try { return read(); } catch { return fallback; } }
    }
}
