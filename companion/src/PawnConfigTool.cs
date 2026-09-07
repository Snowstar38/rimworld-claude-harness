using System;
using System.Collections;
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
    /// The five read blocks that hang off a pawn's CONFIGURATION rather than
    /// their body or their mind: work priorities, the schedule, the pawn-settings
    /// row (medical care, hostility response, self-tend, follow, allowed area,
    /// master), relationships, and -- for an animal -- the Animals tab: what it
    /// is, how wild, who it is bonded to, every trainable with its progress, the
    /// slaughter/release designations standing on it and what it produces.
    ///
    /// They are built here and consumed twice: `home/list_pawns` emits them as
    /// opt-in blocks, and `home/pawn_config` uses the SAME builders for its
    /// `before` and `after`. There is one reader, so a write tool can never
    /// report an "after" the read tool would not have shown.
    ///
    /// ## Hazards, IL-scanned against Assembly-CSharp 1.6.9676.17735
    ///
    /// ### `Pawn_WorkSettings.GetPriority` is NOT a pure read
    ///
    /// Its first line is `ConfirmInitializedDebug()`, whose whole body is:
    ///
    ///     if (priorities == null) { Log.Error(pawn + " did not have work
    ///                                         settings initialized.");
    ///                               EnableAndInitialize(); }
    ///
    /// `Verse.Log.Error` calls `TickManager.Pause()`, and `EnableAndInitialize`
    /// WRITES a fresh priority table onto the pawn. So asking an uninitialised
    /// pawn what its work priorities are would pause the colony AND assign it
    /// six jobs. `EverWork` / `Initialized` are both `priorities != null` and
    /// neither calls the guard, so they are tested FIRST here and `GetPriority`
    /// is never reached otherwise. `WorkIsActive` and `SetPriority` open with the
    /// same guard and are gated the same way.
    ///
    /// ### `GetPriority` LIES in simple mode, on purpose
    ///
    /// Its tail is `if (Humanlike && num > 0 && !Find.PlaySettings.useWorkPriorities)
    /// return 3;` — with the Work tab in checkbox mode, every active job reports
    /// priority 3 whatever is stored. Both numbers are emitted: `priority` is
    /// what the game acts on, `priorityStored` is the raw cell out of the private
    /// `DefMap&lt;WorkTypeDef,int&gt; priorities`, read through the map's own
    /// public indexer (`values[def.index]`, no guard, no masking). They differ
    /// only in simple mode, and `manualPriorities` says which mode is on.
    ///
    /// ### `Pawn_RelationsTracker.OpinionOf` can DELETE and CREATE memories
    ///
    /// The same trap as `ThoughtHandler.GetDistinctMoodThoughtGroups`, one field
    /// over, and it is not marked anywhere in the game:
    ///
    ///     OpinionOf -> ThoughtHandler.TotalOpinionOffset
    ///       -> GetDistinctSocialThoughtGroups -> GetSocialThoughts
    ///       -> SituationalThoughtHandler.AppendSocialThoughts
    ///       -> CheckRecalculateSocialThoughts   (creates the per-pawn cache
    ///                                            entry if absent)
    ///       -> Thought_SituationalSocial.RecalculateState
    ///       -> Thought_Situational.Notify_BecameActive   -> RemoveMemoriesOfDef
    ///          Thought_Situational.Notify_BecameInactive -> TryGainMemory
    ///
    /// A cache entry that does not exist yet is created with
    /// `lastRecalculationTick = -99999`, so `ShouldRecalculateState` is true on
    /// the FIRST query and every social situational thought transitions
    /// inactive -> active — which is exactly the branch that removes memories.
    /// Asking "what does Ada think of Bo?" would edit both colonists' minds.
    ///
    /// So `opinion` is RECONSTRUCTED from `OpinionOf`'s own arithmetic over pure
    /// reads, and says so in `opinionMethod`:
    ///
    ///   * relation offsets: `pawn.GetRelations(other)` summing
    ///     `PawnRelationDef.opinionOffset` — the same enumeration the social card
    ///     labels rows with. Its workers only read.
    ///   * memory offsets: `memories.Memories` filtered to `ISocialThought` whose
    ///     `OtherPawn()` is the other pawn, grouped with `Thought.GroupsWith`
    ///     (RimWorld's own predicate) and summed with
    ///     `Thought_MemorySocial.OpinionOffset()`, which is a pure read.
    ///   * situational-social offsets: the handler's private
    ///     `cachedSocialThoughts` dictionary, read by reflection WITHOUT
    ///     recalculating. When it holds no entry for the other pawn — normal, it
    ///     is only filled when something asked — `situationalSocialCached` is
    ///     false and the number is a floor, said out loud rather than passed off
    ///     as a total.
    ///   * then `OpinionOf`'s own tail: multiply by every hediff stage's
    ///     `opinionOfOthersFactor`, zero a positive opinion toward someone this
    ///     pawn is hostile to, clamp to -100..100.
    ///
    /// ### The animal block's own hazards are on AnimalBlock
    ///
    /// Three of them are worth knowing before reading that method: `GetSteps` is
    /// **internal** and reached by reflection; `Wildness` is a **stat**, not a
    /// `RaceProperties` field, and is only read for animals because
    /// `StatWorker` has a dev-mode `Log.ErrorOnce` for a disabled stat; and
    /// `DesignationManager.DesignationOn(t, def)` is **not** used, because its
    /// wrong-target-type arm is a `Log.Error` — `AllDesignationsOn(t)` answers
    /// the same question with no error arm.
    ///
    /// ### Everything else on these blocks stores nothing
    ///
    /// `Pawn_TimetableTracker.times` / `.GetAssignment(h)` / `.CurrentAssignment`
    /// (indexes the list), every `Pawn_PlayerSettings` field (`medCare`,
    /// `hostilityResponse`, `selfTend`, `followDrafted`, `followFieldwork` are
    /// public fields; `Master`'s getter is `return master;`),
    /// `AreaRestrictionInPawnCurrentMap`'s getter (a dictionary lookup),
    /// `AreaManager.AllAreas`, `Pawn.WorkTypeIsDisabled` (which memoises
    /// `cachedDisabledWorkTypes` — a memo behind a Scribe-mode reset, the same
    /// class as `PainTotal`) and `Pawn_TrainingTracker.HasLearned` were all
    /// IL-scanned and write no game state.
    ///
    /// **`EffectiveAreaRestrictionInPawnCurrentMap` is deliberately not read.**
    /// Its `RespectsAllowedArea` branch calls `Faction.OfPlayer`, whose body is
    /// `OfPlayerSilentFail` followed by `Log.Error` — the pause again. The plain
    /// `AreaRestrictionInPawnCurrentMap` is what the Assign tab shows and needs
    /// no faction lookup.
    /// </summary>
    internal static class PawnSettingsRead
    {
        // ==================================================================
        // work{}
        // ==================================================================

        /// <summary>
        /// The private `DefMap&lt;WorkTypeDef,int&gt; priorities`. Null if
        /// RimWorld ever renames it, and every caller reports the null rather
        /// than emitting a stored priority of 0 that would read as "never do
        /// this".
        /// </summary>
        private static readonly FieldInfo PrioritiesField =
            BridgeCommon.PrivateInstanceField(typeof(Pawn_WorkSettings), "priorities");

        /// <summary>
        /// Every work type in the order the Work tab lays its columns out:
        /// `WorkTypeDefsUtility.WorkTypeDefsInPriorityOrder` is
        /// `AllDefs.OrderByDescending(naturalPriority)`, reproduced here so the
        /// enumeration is materialised once instead of per pawn.
        /// </summary>
        internal static List<WorkTypeDef> WorkTypesInTabOrder()
        {
            try
            {
                var all = DefDatabase<WorkTypeDef>.AllDefsListForReading;
                if (all == null)
                    return new List<WorkTypeDef>();
                return all.Where(w => w != null)
                          .OrderByDescending(w => w.naturalPriority)
                          .ToList();
            }
            catch { return new List<WorkTypeDef>(); }
        }

        /// <summary>Is the Work tab in numbered-priority mode? Null when the
        /// setting could not be read at all, which is not the same as "simple
        /// mode" and is never collapsed into it.</summary>
        internal static bool? ManualPriorities()
        {
            try
            {
                if (Current.Game == null || Current.Game.playSettings == null)
                    return null;
                return Current.Game.playSettings.useWorkPriorities;
            }
            catch { return null; }
        }

        /// <summary>The raw cell of the private priority table, unmasked by
        /// simple mode. Null when the table or the field is unreadable.</summary>
        internal static int? StoredPriority(Pawn_WorkSettings settings, WorkTypeDef def)
        {
            if (settings == null || def == null || PrioritiesField == null)
                return null;
            try
            {
                var map = PrioritiesField.GetValue(settings) as DefMap<WorkTypeDef, int>;
                if (map == null)
                    return null;
                return map[def];
            }
            catch { return null; }
        }

        /// <summary>
        /// The Work tab for one pawn. **Never null**, on the same contract as
        /// `bio{}`: a pawn with no work settings gets a block saying
        /// `applies: false`, because an absent work block read as "no jobs
        /// assigned" is the Lucas bug wearing a work tab.
        /// </summary>
        internal static Dictionary<string, object> WorkBlock(Pawn pawn)
        {
            var block = new Dictionary<string, object>();

            Pawn_WorkSettings settings = null;
            try { settings = pawn.workSettings; }
            catch { settings = null; }

            var everWork = settings != null && BridgeCommon.Try(() => settings.EverWork, false);
            var initialized = settings != null && BridgeCommon.Try(() => settings.Initialized, false);

            block["applies"] = everWork;
            block["hasWorkSettings"] = settings != null;
            block["everWork"] = everWork;
            block["initialized"] = initialized;

            var manual = ManualPriorities();
            block["manualPriorities"] = manual;
            block["priorityMin"] = 0;
            block["priorityMax"] = 4;

            var rows = new List<object>();
            var active = new List<object>();
            var disabledNames = new List<object>();
            var storedReadable = true;

            foreach (var def in WorkTypesInTabOrder())
            {
                var row = new Dictionary<string, object>();
                row["name"] = BridgeCommon.SafeString(() => def.defName);
                row["label"] = BridgeCommon.SafeString(() => def.labelShort) ??
                               BridgeCommon.SafeString(() => def.label);
                row["naturalPriority"] = BridgeCommon.Try(() => def.naturalPriority, 0);
                row["visibleInTab"] = BridgeCommon.Try(() => def.visible, true);

                var disabled = BridgeCommon.Try(() => pawn.WorkTypeIsDisabled(def), false);
                row["disabled"] = disabled;
                if (disabled)
                    disabledNames.Add(row["name"]);

                // GetPriority is only safe once EverWork is true; see the class
                // remarks. Null here means NOT READ, never 0.
                int? effective = null;
                if (everWork)
                    effective = BridgeCommon.TryN(() => settings.GetPriority(def));
                var stored = everWork ? StoredPriority(settings, def) : null;
                if (everWork && stored == null)
                    storedReadable = false;

                row["priority"] = effective;
                row["priorityStored"] = stored;
                row["active"] = effective != null && effective.Value > 0;
                if (effective != null && effective.Value > 0 && !disabled)
                    active.Add(row["name"]);
                rows.Add(row);
            }

            block["types"] = rows;
            block["typeCount"] = rows.Count;
            block["activeWorkTypes"] = active;
            block["disabledWorkTypes"] = disabledNames;
            block["priorityStoredReadable"] = !everWork || storedReadable;
            block["note"] = everWork
                ? (manual == false
                    ? "The Work tab is in SIMPLE (checkbox) mode: Pawn_WorkSettings.GetPriority returns 3 for every active job whatever is stored, so `priority` is 3-or-0 here and `priorityStored` is the real cell. A write of any priority above 0 becomes 3, which is what clicking the checkbox does."
                    : "Numbered priorities are on. priority == priorityStored; 0 means never, 1 is most urgent, 4 is least.")
                : (settings == null
                    ? "This pawn has no work settings at all (animals, mechanoids, other factions). Not read, not empty."
                    : "Pawn_WorkSettings.EverWork is false: this pawn has never had a priority table. Reading one would have called Log.Error AND assigned it six jobs, so nothing was read. Not empty — not looked at.");
            return block;
        }

        // ==================================================================
        // schedule{}
        // ==================================================================

        private static readonly object LetterGate = new object();
        private static Dictionary<string, TimeAssignmentDef> _byLetter;
        private static Dictionary<TimeAssignmentDef, string> _letterOf;

        /// <summary>
        /// One letter per TimeAssignmentDef, assigned deterministically from the
        /// def names so a 24-character schedule string means the same thing on
        /// every call and on both sides of a write. First choice is the def's
        /// initial (Anything -> A, Work -> W, Joy -> J, Sleep -> S, Meditate ->
        /// M); a collision walks the rest of the name, then the alphabet. The key
        /// is returned in every reply, so a caller never has to know this rule.
        /// </summary>
        private static void EnsureLetters()
        {
            lock (LetterGate)
            {
                if (_byLetter != null)
                    return;

                var byLetter = new Dictionary<string, TimeAssignmentDef>(StringComparer.OrdinalIgnoreCase);
                var letterOf = new Dictionary<TimeAssignmentDef, string>();

                List<TimeAssignmentDef> defs;
                try
                {
                    var all = DefDatabase<TimeAssignmentDef>.AllDefsListForReading;
                    defs = all != null ? all.Where(d => d != null).ToList() : new List<TimeAssignmentDef>();
                }
                catch { defs = new List<TimeAssignmentDef>(); }

                foreach (var def in defs)
                {
                    var name = BridgeCommon.SafeString(() => def.defName) ?? "?";
                    string chosen = null;
                    foreach (var c in name.ToUpperInvariant())
                    {
                        if (c < 'A' || c > 'Z')
                            continue;
                        var s = c.ToString();
                        if (!byLetter.ContainsKey(s))
                        {
                            chosen = s;
                            break;
                        }
                    }
                    if (chosen == null)
                    {
                        for (var c = 'A'; c <= 'Z'; c++)
                        {
                            var s = c.ToString();
                            if (!byLetter.ContainsKey(s))
                            {
                                chosen = s;
                                break;
                            }
                        }
                    }
                    if (chosen == null)
                        continue;
                    byLetter[chosen] = def;
                    letterOf[def] = chosen;
                }

                _byLetter = byLetter;
                _letterOf = letterOf;
            }
        }

        internal static string LetterFor(TimeAssignmentDef def)
        {
            if (def == null)
                return "?";
            EnsureLetters();
            string s;
            return _letterOf.TryGetValue(def, out s) ? s : "?";
        }

        internal static TimeAssignmentDef AssignmentForLetter(string letter)
        {
            if (string.IsNullOrEmpty(letter))
                return null;
            EnsureLetters();
            TimeAssignmentDef def;
            return _byLetter.TryGetValue(letter, out def) ? def : null;
        }

        /// <summary>letter -> defName, emitted with every schedule so the
        /// 24-character string is self-describing.</summary>
        internal static Dictionary<string, object> LetterKey()
        {
            EnsureLetters();
            var key = new Dictionary<string, object>();
            foreach (var pair in _byLetter)
                key[pair.Key] = BridgeCommon.SafeString(() => pair.Value.defName);
            return key;
        }

        /// <summary>
        /// The Assign tab's 24 hours as one string, hour 0 first. **Never
        /// null**: a pawn with no timetable says `applies: false` rather than
        /// being absent.
        /// </summary>
        internal static Dictionary<string, object> ScheduleBlock(Pawn pawn)
        {
            var block = new Dictionary<string, object>();

            Pawn_TimetableTracker timetable = null;
            try { timetable = pawn.timetable; }
            catch { timetable = null; }

            block["applies"] = timetable != null;
            block["hasTimetable"] = timetable != null;
            block["key"] = LetterKey();

            List<TimeAssignmentDef> times = null;
            if (timetable != null)
            {
                try { times = timetable.times != null ? timetable.times.ToList() : null; }
                catch { times = null; }
            }

            block["hourCount"] = times == null ? (object)null : times.Count;

            if (times == null)
            {
                block["hours"] = null;
                block["assignments"] = new List<object>();
                block["current"] = null;
                block["currentHour"] = null;
                block["note"] = timetable == null
                    ? "This pawn has no timetable tracker (animals, other factions). Not read, not empty."
                    : "Pawn_TimetableTracker.times was not readable. Nothing here is a schedule.";
                return block;
            }

            var letters = new System.Text.StringBuilder(times.Count);
            var names = new List<object>();
            foreach (var t in times)
            {
                letters.Append(LetterFor(t));
                names.Add(t != null ? BridgeCommon.SafeString(() => t.defName) : null);
            }
            block["hours"] = letters.ToString();
            block["assignments"] = names;

            var current = BridgeCommon.Try<TimeAssignmentDef>(() => timetable.CurrentAssignment, null);
            block["current"] = current != null ? BridgeCommon.SafeString(() => current.defName) : null;
            block["currentLetter"] = current != null ? LetterFor(current) : null;
            block["currentHour"] = BridgeCommon.TryN(() => GenLocalDate.HourOfDay(pawn));
            block["note"] = times.Count == 24
                ? "hours[] is one letter per hour, hour 0 first; key{} maps every letter to its TimeAssignmentDef. current is what the pawn is assigned RIGHT NOW, which is a prisoner-and-colonist-aware read and can differ from the letter at currentHour."
                : "This timetable does not hold 24 hours (" + times.Count + "). Reported as found; a schedule write refuses on it rather than reshaping it.";
            return block;
        }

        // ==================================================================
        // settings{}
        // ==================================================================

        /// <summary>
        /// The Assign tab's row for one pawn: medical care, hostility response,
        /// self-tend, the two follow toggles, the allowed area and the master.
        /// **Never null.**
        /// </summary>
        internal static Dictionary<string, object> SettingsBlock(Pawn pawn)
        {
            var block = new Dictionary<string, object>();

            block["thingId"] = BridgeCommon.SafeString(() => pawn.ThingID);

            Pawn_PlayerSettings ps = null;
            try { ps = pawn.playerSettings; }
            catch { ps = null; }

            block["applies"] = ps != null;
            block["hasPlayerSettings"] = ps != null;

            if (ps == null)
            {
                block["medCare"] = null;
                block["hostilityResponse"] = null;
                block["selfTend"] = null;
                block["followDrafted"] = null;
                block["followFieldwork"] = null;
                block["allowedArea"] = null;
                block["allowedAreaIsUnrestricted"] = null;
                block["supportsAllowedAreas"] = null;
                block["master"] = null;
                block["masterThingId"] = null;
                block["note"] = "This pawn has no playerSettings at all (wild animals, other factions). Every field above is NOT READ, not a default.";
                return block;
            }

            block["medCare"] = BridgeCommon.SafeString(() => ps.medCare.ToString());
            block["hostilityResponse"] = BridgeCommon.SafeString(() => ps.hostilityResponse.ToString());
            block["selfTend"] = BridgeCommon.TryN(() => ps.selfTend);
            block["followDrafted"] = BridgeCommon.TryN(() => ps.followDrafted);
            block["followFieldwork"] = BridgeCommon.TryN(() => ps.followFieldwork);

            // The plain getter, not EffectiveAreaRestrictionInPawnCurrentMap:
            // the "effective" one routes through Faction.OfPlayer, which pauses
            // the colony on a map with no player faction. See the class remarks.
            var area = BridgeCommon.Try<Area>(() => ps.AreaRestrictionInPawnCurrentMap, null);
            block["allowedArea"] = area != null ? BridgeCommon.SafeString(() => area.Label) : null;
            block["allowedAreaIsUnrestricted"] = area == null;
            block["allowedAreaCellCount"] = area != null ? BridgeCommon.TryN(() => area.TrueCount) : null;
            block["supportsAllowedAreas"] = BridgeCommon.TryN(() => ps.SupportsAllowedAreas);

            var master = BridgeCommon.Try<Pawn>(() => ps.Master, null);
            block["master"] = master != null ? BridgeCommon.SafeString(() => master.LabelShortCap.ToString()) : null;
            block["masterThingId"] = master != null ? BridgeCommon.SafeString(() => master.ThingID) : null;
            block["note"] = "allowedArea null means UNRESTRICTED — the whole map — which is a real setting, not a missing read; allowedAreaIsUnrestricted says so as a bool. selfTend is OFF by default for every colonist including new joiners.";
            return block;
        }

        /// <summary>
        /// What a `home/pawn_config` write may be handed, computed once per call
        /// rather than repeated inside every pawn row: the areas on this map that
        /// can be assigned, and the two enums spelled out.
        /// </summary>
        internal static Dictionary<string, object> Options(Map map)
        {
            var options = new Dictionary<string, object>();

            var areas = new List<object>();
            try
            {
                var manager = map != null ? map.areaManager : null;
                var all = manager != null ? manager.AllAreas : null;
                if (all != null)
                {
                    for (var i = 0; i < all.Count; i++)
                    {
                        var a = all[i];
                        if (a == null)
                            continue;
                        var row = new Dictionary<string, object>();
                        row["label"] = BridgeCommon.SafeString(() => a.Label);
                        row["assignable"] = BridgeCommon.Try(() => a.AssignableAsAllowed(), false);
                        row["cellCount"] = BridgeCommon.TryN(() => a.TrueCount);
                        areas.Add(row);
                    }
                }
            }
            catch { }
            options["allowedAreas"] = areas;
            options["allowedAreaUnrestricted"] = "Pass allowedArea=\"none\" (or \"unrestricted\") to clear the restriction. Omitting it leaves the setting alone.";
            options["medCare"] = Enum.GetNames(typeof(MedicalCareCategory)).Cast<object>().ToList();
            options["hostilityResponse"] = Enum.GetNames(typeof(HostilityResponseMode)).Cast<object>().ToList();
            options["scheduleKey"] = LetterKey();
            return options;
        }

        // ==================================================================
        // animals{} -- the Animals tab, read
        // ==================================================================

        /// <summary>
        /// The player faction, or null. NOT `Faction.OfPlayer`: its body is
        /// `get_OfPlayerSilentFail` followed by `Verse.Log.Error`, and
        /// `Log.Error` calls `TickManager.Pause()`. Every "is this one of ours"
        /// test below goes through this.
        /// </summary>
        internal static Faction PlayerFactionSilent()
        {
            try { return Faction.OfPlayerSilentFail; }
            catch { return null; }
        }

        /// <summary>
        /// `Pawn_TrainingTracker.GetSteps(TrainableDef)` is **internal**, so an
        /// external assembly cannot call it. The Animals tab draws
        /// "steps / td.steps" with it (TrainingCardUtility), and a training row
        /// without its progress is the difference between "started" and "nearly
        /// done". It is reached by reflection, and when the lookup fails the
        /// reply says `stepsReadable: false` and every `steps` is null -- never
        /// 0, which would read as "no progress at all".
        /// </summary>
        private static readonly MethodInfo TrainingGetSteps = FindTrainingGetSteps();

        private static MethodInfo FindTrainingGetSteps()
        {
            try
            {
                return typeof(Pawn_TrainingTracker).GetMethod(
                    "GetSteps",
                    BindingFlags.NonPublic | BindingFlags.Instance,
                    null,
                    new[] { typeof(TrainableDef) },
                    null);
            }
            catch { return null; }
        }

        /// <summary>`CompEggLayer.eggProgress` is private and `CanLayNow` only
        /// says "full". Same contract as the steps above: null and a readable
        /// flag, never a 0 that means "not looked at".</summary>
        private static readonly FieldInfo EggProgressField =
            BridgeCommon.PrivateInstanceField(typeof(CompEggLayer), "eggProgress");

        /// <summary>
        /// The trainables in the order the Animals tab draws them.
        /// `TrainableUtility.TrainableDefsInListOrder` is the game's own sorted
        /// list (by `listPriority` descending, prerequisites indented) and is
        /// what `NextTrainableToTrain` walks, so a caller reading this list top
        /// to bottom sees what the game will train next. It hands back the live
        /// static list, so it is COPIED. DefDatabase order is the fallback, and
        /// is not the same order.
        /// </summary>
        internal static List<TrainableDef> TrainableDefsInOrder()
        {
            try
            {
                var ordered = TrainableUtility.TrainableDefsInListOrder;
                if (ordered != null && ordered.Count > 0)
                    return ordered.ToList();
            }
            catch { }
            try { return DefDatabase<TrainableDef>.AllDefsListForReading.ToList(); }
            catch { return new List<TrainableDef>(); }
        }

        /// <summary>
        /// The four designations the Animals tab and its designators put on a
        /// pawn. Named here once so the read and `home/pawn_config`'s write
        /// cannot disagree about which they are.
        /// </summary>
        internal static List<KeyValuePair<string, DesignationDef>> AnimalDesignationDefs()
        {
            var list = new List<KeyValuePair<string, DesignationDef>>();
            AddDesignationDef(list, "Slaughter", BridgeCommon.Try<DesignationDef>(() => DesignationDefOf.Slaughter, null));
            AddDesignationDef(list, "ReleaseAnimalToWild", BridgeCommon.Try<DesignationDef>(() => DesignationDefOf.ReleaseAnimalToWild, null));
            AddDesignationDef(list, "Tame", BridgeCommon.Try<DesignationDef>(() => DesignationDefOf.Tame, null));
            AddDesignationDef(list, "Hunt", BridgeCommon.Try<DesignationDef>(() => DesignationDefOf.Hunt, null));
            return list;
        }

        private static void AddDesignationDef(List<KeyValuePair<string, DesignationDef>> list, string name, DesignationDef def)
        {
            if (def != null)
                list.Add(new KeyValuePair<string, DesignationDef>(name, def));
        }

        /// <summary>The payload key each of those four designations is reported
        /// under, on the read and in a write's field rows.</summary>
        internal static string DesignationKey(string defName)
        {
            if (string.Equals(defName, "ReleaseAnimalToWild", StringComparison.Ordinal))
                return "releaseToWild";
            if (string.IsNullOrEmpty(defName))
                return defName;
            return char.ToLowerInvariant(defName[0]) + defName.Substring(1);
        }

        /// <summary>
        /// The designation of one def on one pawn, or null.
        ///
        /// **Not `DesignationManager.DesignationOn(t, def)`**, deliberately: its
        /// first branch is `if (def.targetType == TargetType.Cell) Log.Error(...)`,
        /// and `Log.Error` pauses the colony. A mod that re-declares one of these
        /// defs as cell-targeted would turn a read into a pause. Walking
        /// `AllDesignationsOn(t)` -- which returns the live indexed list, or a
        /// shared empty one -- asks the same question with no error arm at all.
        /// The list is READ ONLY here; `RemoveDesignation` mutates it, so the
        /// write side takes the reference first and removes afterwards.
        /// </summary>
        internal static Designation DesignationOnSafe(Pawn pawn, DesignationDef def)
        {
            if (pawn == null || def == null)
                return null;
            try
            {
                var map = pawn.MapHeld;
                var manager = map != null ? map.designationManager : null;
                if (manager == null)
                    return null;
                var all = manager.AllDesignationsOn(pawn);
                if (all == null)
                    return null;
                for (var i = 0; i < all.Count; i++)
                {
                    var d = all[i];
                    if (d != null && d.def == def)
                        return d;
                }
                return null;
            }
            catch { return null; }
        }

        /// <summary>
        /// Everything the Animals tab shows about one animal: what it is, how
        /// wild, who it is bonded to, every trainable with its progress, the
        /// designations standing on it and what it is producing.
        ///
        /// **Never null**, and never absent. A humanlike gets a block that says
        /// `applies: false` and `isAnimal: false` rather than no key at all --
        /// the same contract `work{}` and `settings{}` take, and for the same
        /// reason: an absent key and an old build read identically to a caller.
        ///
        /// **What is deliberately NOT here**, because `settings{}` already has
        /// it and two copies drift: `master`, `masterThingId`, `followDrafted`,
        /// `followFieldwork`, `allowedArea`. `settingsBlockCarries` says so in
        /// the payload. The hunger level is in `needs{}` for the same reason.
        ///
        /// ## Hazards, IL-scanned against Assembly-CSharp 1.6.9676.17735
        ///
        ///   * `Pawn_TrainingTracker.GetWanted` / `.HasLearned` /
        ///     `.CanBeTrained` / `.NextTrainableToTrain` / `.CanAssignToTrain`
        ///     read `DefMap` cells and def fields and store nothing.
        ///     `HasLearned` and `CanAssignToTrain` both route through
        ///     `TrainableUtility.GetTrainability`, whose Odyssey branch reads a
        ///     hediff set and sorts a static def cache once. No writes.
        ///   * **`Pawn.GetStatValue(StatDefOf.Wildness)` is only read for
        ///     ANIMALS.** `StatWorker.GetValueUnfinalized` opens with a
        ///     `Log.ErrorOnce` for a stat that is disabled on the subject --
        ///     guarded by `Prefs.DevMode`, which is a setting a player can have
        ///     on, so the guard is not enough to rely on. Wildness is never
        ///     disabled on an animal. `StatWorker.GetValue` also fills
        ///     `temporaryStatCache`; that is a memo behind a tick stamp, the same
        ///     class as `HediffSet.PainTotal`.
        ///   * `TrainableUtility.GetAllColonistBondsFor` filters
        ///     `relations.DirectRelations` -- a plain field read -- and touches
        ///     no opinion path, so it is nowhere near
        ///     `Pawn_RelationsTracker.OpinionOf`'s memory deletion.
        ///   * `CompHasGatherableBodyResource.Fullness` and `.ActiveAndFull` are
        ///     field reads. `.Active` is **protected**, so "is it producing right
        ///     now" is not readable from outside the assembly at all; that is
        ///     said in `activeIsNotReadable` rather than guessed at.
        ///   * `CompEggLayer.CanLayNow` reads `Pawn.Sterile()` (hediffs and
        ///     genes) and `ageTracker.CurLifeStage`. Both pure.
        ///   * `Hediff_Pregnant.GestationProgress` is `Severity`, a field.
        /// </summary>
        internal static Dictionary<string, object> AnimalBlock(Pawn pawn)
        {
            var block = new Dictionary<string, object>();

            var isAnimal = BridgeCommon.Try(() => pawn.RaceProps != null && pawn.RaceProps.Animal, false);
            block["isAnimal"] = isAnimal;
            block["applies"] = isAnimal;

            if (!isAnimal)
            {
                block["note"] = "This pawn is not an animal (RaceProps.Animal is false), so the Animals tab has no row for it. "
                              + "Every animal field is NOT APPLICABLE here, not unread. Humanlikes and mechanoids land here.";
                return block;
            }

            var race = BridgeCommon.Try<RaceProperties>(() => pawn.RaceProps, null);
            var player = PlayerFactionSilent();
            var faction = BridgeCommon.Try<Faction>(() => pawn.Faction, null);

            // tame / wild are NOT complements: a visiting trader's muffalo is
            // neither ours nor wild, and calling it either would be a lie.
            block["tame"] = player != null && faction != null && faction == player;
            block["wild"] = faction == null;
            block["otherFaction"] = faction != null && (player == null || faction != player);
            block["faction"] = faction != null ? BridgeCommon.SafeString(() => faction.Name) : null;

            block["race"] = BridgeCommon.SafeString(() => pawn.def != null ? pawn.def.defName : null);
            block["raceLabel"] = BridgeCommon.SafeString(() => pawn.def != null ? pawn.def.label : null);
            block["kindLabel"] = BridgeCommon.SafeString(() => pawn.KindLabel);

            // `name` is the GIVEN name and is null for an unnamed animal, which
            // is a real state, not a failed read: `named` says which.
            var hasName = BridgeCommon.Try(() => pawn.Name != null, false);
            block["named"] = hasName;
            block["name"] = hasName ? BridgeCommon.SafeString(() => pawn.Name.ToStringShort) : null;
            block["label"] = BridgeCommon.SafeString(() => pawn.LabelShortCap.ToString());
            // Animals share a race name -- five muffalo are all "Muffalo" -- so
            // a name is not an address for one. This is, and it is the same
            // field settings{} carries for a colonist.
            block["thingId"] = BridgeCommon.SafeString(() => pawn.ThingID);

            block["gender"] = BridgeCommon.SafeString(() => pawn.gender.ToString());
            block["ageYears"] = BridgeCommon.TryN(() => pawn.ageTracker.AgeBiologicalYears);
            block["ageYearsFloat"] = Round3(BridgeCommon.TryN(() => pawn.ageTracker.AgeBiologicalYearsFloat));
            block["lifeStage"] = BridgeCommon.SafeString(
                () => pawn.ageTracker.CurLifeStage != null ? pawn.ageTracker.CurLifeStage.defName : null);

            block["bodySize"] = Round3(BridgeCommon.TryN(() => pawn.BodySize));
            block["baseBodySize"] = Round3(race != null ? BridgeCommon.TryN(() => race.baseBodySize) : null);
            block["baseHungerRate"] = Round3(race != null ? BridgeCommon.TryN(() => race.baseHungerRate) : null);
            block["petness"] = Round3(race != null ? BridgeCommon.TryN(() => race.petness) : null);
            block["predator"] = race != null && BridgeCommon.Try(() => race.predator, false);
            block["packAnimal"] = race != null && BridgeCommon.Try(() => race.packAnimal, false);
            block["canReleaseToWild"] = race != null && BridgeCommon.Try(() => race.canReleaseToWild, false);

            // Wildness is a STAT in 1.6, not a RaceProperties field -- there is
            // no RaceProps.wildness. Only read for animals; see the remarks.
            var wildness = BridgeCommon.TryN(() => pawn.GetStatValue(StatDefOf.Wildness));
            block["wildness"] = Round3(wildness);
            block["wildnessRead"] = wildness != null;

            var trainability = BridgeCommon.Try<TrainabilityDef>(() => TrainableUtility.GetTrainability(pawn), null);
            block["trainability"] = trainability != null
                ? BridgeCommon.SafeString(() => trainability.defName)
                : null;
            block["trainabilityFromRace"] = race != null && race.trainability != null
                ? BridgeCommon.SafeString(() => race.trainability.defName)
                : null;
            block["minimumHandlingSkill"] = BridgeCommon.TryN(() => TrainableUtility.MinimumHandlingSkill(pawn));

            // Bonds: the colonists this animal is BONDED to, which is a
            // different relationship from having a master and is the one the
            // slaughter warning is about.
            var bonded = new List<object>();
            try
            {
                var bonds = TrainableUtility.GetAllColonistBondsFor(pawn);
                if (bonds != null)
                {
                    foreach (var b in bonds)
                    {
                        if (b == null)
                            continue;
                        bonded.Add(BridgeCommon.SafeString(() => b.LabelShortCap.ToString()));
                    }
                }
            }
            catch { }
            block["bonded"] = bonded;
            block["bondedCount"] = bonded.Count;

            block["training"] = TrainingSubBlock(pawn);
            block["designations"] = DesignationsSubBlock(pawn);
            block["produce"] = ProduceSubBlock(pawn);

            block["settingsBlockCarries"] =
                "master, masterThingId, followDrafted, followFieldwork and allowedArea are NOT repeated here -- they are in "
                + "settings{} (pass settings:true), from the same reader. The hunger level is in needs{} (pass needs:true). "
                + "Two copies of one field is how two answers start disagreeing.";
            return block;
        }

        /// <summary>
        /// Every trainable in the Animals tab's own order, with what the tab
        /// draws in the cell: whether the player has ticked it (`wanted`),
        /// whether the animal has it (`learned`), whether it can be ticked at
        /// all (`canTrain`, with the game's own `reason` when it cannot) and how
        /// far along it is (`steps`).
        ///
        /// Four different questions live in this row and they are NOT the same:
        ///
        ///   * `canTrain` -- `CanAssignToTrain(td, out visible)`. Can this
        ///     animal EVER learn it: body size, trainability, race tags. The
        ///     `reason` is RimWorld's own translated sentence
        ///     ("CannotTrainNotSmartEnough"), not one written here.
        ///   * `visible` -- the same call's out parameter. False means the game
        ///     does not draw the column at all for this race (an untrainable
        ///     tag, or a DLC-only special trainable on a race without it). A row
        ///     with `visible:false` is not a refusal, it is not a thing.
        ///   * `learned` -- `HasLearned(td)`. Already trained.
        ///   * `canBeTrainedNow` -- `CanBeTrained(td)`. There are steps left AND
        ///     every prerequisite is finished. This is what
        ///     `NextTrainableToTrain` walks, so a `wanted` trainable with
        ///     `canBeTrainedNow:false` is the reason a handler is not working.
        /// </summary>
        internal static Dictionary<string, object> TrainingSubBlock(Pawn pawn)
        {
            var block = new Dictionary<string, object>();

            Pawn_TrainingTracker training = null;
            try { training = pawn.training; }
            catch { training = null; }

            block["applies"] = training != null;
            block["stepsReadable"] = TrainingGetSteps != null;
            if (TrainingGetSteps == null)
            {
                block["stepsNote"] = "Pawn_TrainingTracker.GetSteps(TrainableDef) is internal to Assembly-CSharp and was not found by "
                                   + "reflection in this build, so every steps/stepsDone below is null. Null means NOT READ, not zero.";
            }

            if (training == null)
            {
                block["trainables"] = new List<object>();
                block["trainableCount"] = 0;
                block["nextToTrain"] = null;
                block["wantedCount"] = 0;
                block["learnedCount"] = 0;
                block["note"] = "This pawn has no training tracker at all. Every field is NOT APPLICABLE, not unread.";
                return block;
            }

            var rows = new List<object>();
            var wantedCount = 0;
            var learnedCount = 0;
            foreach (var td in TrainableDefsInOrder())
            {
                if (td == null)
                    continue;

                var row = new Dictionary<string, object>();
                row["name"] = BridgeCommon.SafeString(() => td.defName);
                row["label"] = BridgeCommon.SafeString(() => td.LabelCap.ToString());
                row["indent"] = BridgeCommon.TryN(() => td.indent);

                var wanted = BridgeCommon.Try(() => training.GetWanted(td), false);
                var learned = BridgeCommon.Try(() => training.HasLearned(td), false);
                row["wanted"] = wanted;
                row["learned"] = learned;
                if (wanted) wantedCount++;
                if (learned) learnedCount++;

                var visible = false;
                string reason = null;
                var canTrain = false;
                try
                {
                    bool vis;
                    var report = training.CanAssignToTrain(td, out vis);
                    visible = vis;
                    canTrain = report.Accepted;
                    if (!report.Accepted)
                    {
                        var text = report.Reason;
                        reason = string.IsNullOrEmpty(text)
                            ? (vis
                                ? "RimWorld returned a rejection with no reason text."
                                : "Not shown for this race at all (an untrainable tag, or a DLC-only special trainable).")
                            : text;
                    }
                }
                catch
                {
                    canTrain = false;
                    visible = false;
                    reason = "Pawn_TrainingTracker.CanAssignToTrain threw; treat canTrain as NOT READ.";
                }
                row["canTrain"] = canTrain;
                row["visible"] = visible;
                row["reason"] = reason;

                row["canBeTrainedNow"] = BridgeCommon.Try(() => training.CanBeTrained(td), false);

                var total = BridgeCommon.TryN(() => td.steps);
                int? done = null;
                if (TrainingGetSteps != null)
                {
                    try { done = (int)TrainingGetSteps.Invoke(training, new object[] { td }); }
                    catch { done = null; }
                }
                row["stepsDone"] = done;
                row["stepsTotal"] = total;
                row["steps"] = done != null && total != null
                    ? done.Value + "/" + total.Value
                    : null;

                row["requiredTrainability"] = td.requiredTrainability != null
                    ? BridgeCommon.SafeString(() => td.requiredTrainability.defName)
                    : null;
                row["minBodySize"] = Round3(BridgeCommon.TryN(() => td.minBodySize));

                rows.Add(row);
            }

            block["trainables"] = rows;
            block["trainableCount"] = rows.Count;
            block["wantedCount"] = wantedCount;
            block["learnedCount"] = learnedCount;

            var next = BridgeCommon.Try<TrainableDef>(() => training.NextTrainableToTrain(), null);
            block["nextToTrain"] = next != null ? BridgeCommon.SafeString(() => next.defName) : null;
            block["note"] = "Rows are in TrainableUtility.TrainableDefsInListOrder -- the order the Animals tab draws and the order "
                          + "NextTrainableToTrain walks. wanted is the tick box; learned is HasLearned; canTrain is CanAssignToTrain "
                          + "with RimWorld's own reason when it says no; canBeTrainedNow is CanBeTrained (steps left AND prerequisites "
                          + "done). visible:false means the game draws no column for this race, which is not the same as a refusal.";
            return block;
        }

        /// <summary>
        /// The designations standing on this pawn. `slaughter`, `tame`, `hunt`
        /// and `releaseToWild` are hoisted as plain bools -- "marked for
        /// slaughter" should be one field, not a list a caller has to search --
        /// and `all[]` carries every designation on the pawn including any this
        /// list does not name, so nothing is hidden by being unlisted.
        /// </summary>
        internal static Dictionary<string, object> DesignationsSubBlock(Pawn pawn)
        {
            var block = new Dictionary<string, object>();

            Map map = null;
            try { map = pawn.MapHeld; }
            catch { map = null; }
            var manager = map != null ? BridgeCommon.Try<DesignationManager>(() => map.designationManager, null) : null;

            block["readable"] = manager != null;
            if (manager == null)
            {
                block["all"] = new List<object>();
                block["count"] = 0;
                block["slaughter"] = null;
                block["releaseToWild"] = null;
                block["tame"] = null;
                block["hunt"] = null;
                block["note"] = "No map designation manager was reachable for this pawn (MapHeld was null -- carried, or in a caravan). "
                              + "Every flag here is NULL meaning NOT READ, never false meaning 'not marked'.";
                return block;
            }

            var all = new List<object>();
            try
            {
                var list = manager.AllDesignationsOn(pawn);
                if (list != null)
                {
                    for (var i = 0; i < list.Count; i++)
                    {
                        var d = list[i];
                        if (d == null || d.def == null)
                            continue;
                        all.Add(BridgeCommon.SafeString(() => d.def.defName));
                    }
                }
            }
            catch { }
            block["all"] = all;
            block["count"] = all.Count;

            foreach (var pair in AnimalDesignationDefs())
                block[DesignationKey(pair.Key)] = DesignationOnSafe(pawn, pair.Value) != null;

            // A DefOf a mod removed would leave the key missing; emitting false
            // there would say "not marked" about something never looked at.
            foreach (var key in new[] { "slaughter", "releaseToWild", "tame", "hunt" })
            {
                if (!block.ContainsKey(key))
                    block[key] = null;
            }

            block["note"] = "slaughter / releaseToWild / tame / hunt are the four the Animals tab draws; all[] is EVERY designation on "
                          + "the pawn, so one this list does not name is still visible. A null flag means the DesignationDef itself was "
                          + "not found in this build -- not that the pawn is unmarked.";
            return block;
        }

        /// <summary>
        /// Eggs, milk, wool and pregnancy. Every fullness is 0..1.
        ///
        /// `CompHasGatherableBodyResource.Active` -- "is it producing at all
        /// right now", which folds in gender, life stage and shambler state --
        /// is `protected`, so it cannot be read from outside Assembly-CSharp.
        /// That is stated in `activeIsNotReadable` rather than approximated: a
        /// full udder on a comp that is not Active yields nothing, and guessing
        /// which is the kind of confident wrong answer this companion exists to
        /// stop. `activeAndFull` IS public and is emitted.
        /// </summary>
        internal static Dictionary<string, object> ProduceSubBlock(Pawn pawn)
        {
            var block = new Dictionary<string, object>();
            var comps = pawn as ThingWithComps;

            var egg = comps != null ? BridgeCommon.Try<CompEggLayer>(() => comps.GetComp<CompEggLayer>(), null) : null;
            block["hasEggLayer"] = egg != null;
            block["eggFullness"] = null;
            block["eggFullnessReadable"] = false;
            block["eggCanLayNow"] = null;
            block["eggFullyFertilized"] = null;
            if (egg != null)
            {
                if (EggProgressField != null)
                {
                    var progress = BridgeCommon.TryN(() => (float)EggProgressField.GetValue(egg));
                    block["eggFullness"] = Round3(progress);
                    block["eggFullnessReadable"] = progress != null;
                }
                block["eggCanLayNow"] = BridgeCommon.TryN(() => egg.CanLayNow);
                block["eggFullyFertilized"] = BridgeCommon.TryN(() => egg.FullyFertilized);
            }

            var milk = comps != null ? BridgeCommon.Try<CompMilkable>(() => comps.GetComp<CompMilkable>(), null) : null;
            block["hasMilkable"] = milk != null;
            block["milkFullness"] = milk != null ? Round3(BridgeCommon.TryN(() => milk.Fullness)) : null;
            block["milkFull"] = milk != null ? BridgeCommon.TryN(() => milk.ActiveAndFull) : null;

            var wool = comps != null ? BridgeCommon.Try<CompShearable>(() => comps.GetComp<CompShearable>(), null) : null;
            block["hasShearable"] = wool != null;
            block["woolFullness"] = wool != null ? Round3(BridgeCommon.TryN(() => wool.Fullness)) : null;
            block["woolFull"] = wool != null ? BridgeCommon.TryN(() => wool.ActiveAndFull) : null;

            var pregnant = false;
            double? gestation = null;
            var hediffsRead = false;
            try
            {
                var set = pawn.health != null ? pawn.health.hediffSet : null;
                var hediffs = set != null ? set.hediffs : null;
                if (hediffs != null)
                {
                    hediffsRead = true;
                    for (var i = 0; i < hediffs.Count; i++)
                    {
                        var hp = hediffs[i] as Hediff_Pregnant;
                        if (hp == null)
                            continue;
                        pregnant = true;
                        gestation = RoundD(BridgeCommon.TryN(() => hp.GestationProgress));
                        break;
                    }
                }
            }
            catch { }
            block["pregnant"] = hediffsRead ? (object)pregnant : null;
            block["pregnancyReadable"] = hediffsRead;
            block["gestationProgress"] = gestation;
            block["gestationPeriodDays"] = Round3(BridgeCommon.TryN(
                () => pawn.RaceProps != null ? pawn.RaceProps.gestationPeriodDays : -1f));

            block["activeIsNotReadable"] =
                "CompHasGatherableBodyResource.Active is protected in Assembly-CSharp, so whether a comp is producing RIGHT NOW "
                + "(gender, life stage, shambler state) cannot be read from outside the game assembly. activeAndFull IS readable and "
                + "is milkFull / woolFull. A fullness of 1.0 with activeAndFull false means the comp is not active.";
            return block;
        }

        /// <summary>Three decimals, or null. A raw float serialises as
        /// 0.30000001192092896 and every number here is one somebody reads.</summary>
        private static double? RoundD(float? v)
        {
            if (v == null || float.IsNaN(v.Value) || float.IsInfinity(v.Value))
                return null;
            return Math.Round((double)v.Value, 3);
        }

        private static object Round3(float? v)
        {
            return RoundD(v);
        }

        // ==================================================================
        // relations{}
        // ==================================================================

        /// <summary>The private per-other-pawn social-thought cache. Read
        /// WITHOUT calling AppendSocialThoughts, which recalculates and can
        /// delete memories. See the class remarks.</summary>
        private static readonly FieldInfo SocialCacheField =
            BridgeCommon.PrivateInstanceField(typeof(SituationalThoughtHandler), "cachedSocialThoughts");

        /// <summary>The `activeThoughts` list inside a cache entry. The entry
        /// type is a private nested class; the list it holds is
        /// `List&lt;Thought_SituationalSocial&gt;`, which is public.</summary>
        private static readonly FieldInfo SocialActiveField = FindSocialActiveField();

        private static FieldInfo FindSocialActiveField()
        {
            try
            {
                var nested = typeof(SituationalThoughtHandler)
                    .GetNestedType("CachedSocialThoughts", BindingFlags.NonPublic | BindingFlags.Public);
                if (nested == null)
                    return null;
                return nested.GetField("activeThoughts", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance);
            }
            catch { return null; }
        }

        /// <summary>
        /// Family, partners, bonded animals and what this pawn thinks of every
        /// other living colonist. **Never null.**
        ///
        /// `colonists` is the already-walked list of living colonists on the map,
        /// passed in so the opinion pass is O(colonists) per pawn rather than a
        /// second traversal of `mapPawns`.
        /// </summary>
        internal static Dictionary<string, object> RelationsBlock(Pawn pawn, List<Pawn> colonists)
        {
            var block = new Dictionary<string, object>();

            Pawn_RelationsTracker tracker = null;
            try { tracker = pawn.relations; }
            catch { tracker = null; }

            block["applies"] = tracker != null;
            block["hasRelationsTracker"] = tracker != null;

            var direct = new List<object>();
            var partners = new List<object>();
            var bonded = new List<object>();

            if (tracker != null)
            {
                List<DirectPawnRelation> raw;
                try
                {
                    var list = tracker.DirectRelations;
                    raw = list != null ? list.ToList() : new List<DirectPawnRelation>();
                }
                catch { raw = new List<DirectPawnRelation>(); }

                foreach (var r in raw)
                {
                    if (r == null || r.def == null)
                        continue;
                    var other = r.otherPawn;
                    var row = new Dictionary<string, object>();
                    row["defName"] = BridgeCommon.SafeString(() => r.def.defName);
                    // The label the social card draws, gendered on the OTHER
                    // pawn: "wife", not "spouse"; "daughter", not "child".
                    row["label"] = other != null
                        ? BridgeCommon.SafeString(() => r.def.GetGenderSpecificLabelCap(other))
                        : BridgeCommon.SafeString(() => r.def.LabelCap);
                    row["otherName"] = other != null
                        ? BridgeCommon.SafeString(() => other.LabelShortCap.ToString())
                        : null;
                    row["otherThingId"] = other != null ? BridgeCommon.SafeString(() => other.ThingID) : null;
                    row["otherIsColonist"] = other != null && BridgeCommon.Try(() => other.IsColonist, false);
                    row["otherIsAnimal"] = other != null && BridgeCommon.Try(() => other.RaceProps != null && other.RaceProps.Animal, false);
                    row["otherDead"] = other == null ? (object)null : BridgeCommon.Try(() => other.Dead, false);
                    row["otherOnThisMap"] = other != null && BridgeCommon.Try(() => other.Spawned && other.Map == pawn.Map, false);
                    row["otherMissing"] = other == null;
                    row["startTicks"] = BridgeCommon.Try(() => r.startTicks, 0);
                    direct.Add(row);

                    var defName = row["defName"] as string;
                    if (defName == "Spouse" || defName == "Lover" || defName == "Fiance")
                        partners.Add(row);
                    if (defName == "Bond")
                        bonded.Add(row);
                }
            }

            block["direct"] = direct;
            block["directCount"] = direct.Count;
            // Lifted out because they are the two questions anybody asks of this
            // block: who is this colonist's partner, and which animal is bonded
            // to them. Both are rows from direct[], not a second read.
            block["partners"] = partners;
            block["bondedAnimals"] = bonded;

            // -------------------------------------------------------- opinions
            var opinions = new List<object>();
            var anyCached = false;
            if (tracker != null && colonists != null)
            {
                foreach (var other in colonists)
                {
                    if (other == null || other == pawn)
                        continue;
                    if (!BridgeCommon.Try(() => other.RaceProps != null && other.RaceProps.Humanlike, false))
                        continue;

                    bool cached;
                    var row = OpinionRow(pawn, other, out cached);
                    if (cached)
                        anyCached = true;
                    opinions.Add(row);
                }
                // Worst first. NOT BridgeCommon.Num: it unboxes a double and
                // every opinion here is an int, so that comparison would have
                // sorted every row as 0 and looked like it worked.
                try
                {
                    opinions = opinions
                        .Cast<Dictionary<string, object>>()
                        .OrderBy(r =>
                        {
                            object v;
                            if (!r.TryGetValue("opinion", out v) || v == null)
                                return 0.0;
                            try { return Convert.ToDouble(v); }
                            catch { return 0.0; }
                        })
                        .Cast<object>()
                        .ToList();
                }
                catch { }
            }

            block["colonistOpinions"] = opinions;
            block["colonistOpinionCount"] = opinions.Count;
            block["anySituationalSocialCached"] = anyCached;
            block["opinionMethod"] =
                "opinion is RECONSTRUCTED, not read from Pawn_RelationsTracker.OpinionOf. That call recalculates situational social thoughts, and Thought_Situational.Notify_BecameActive DELETES every memory of the def it produces (Notify_BecameInactive grants one) -- asking what two colonists think of each other would edit both their minds. The parts are: relation offsets from Pawn.GetRelations, social memories from memories.Memories grouped with Thought.GroupsWith, and situational social thoughts read out of the handler's own cache without recalculating. Each row carries situationalSocialCached: when it is false RimWorld has never filled that cache entry, the situational term is 0 by absence, and the opinion is a FLOOR rather than a total.";
            block["opinionScale"] = "-100..100, the same scale the social tab shows. Below -20 is a rival, above 20 a friend.";
            return block;
        }

        private static Dictionary<string, object> OpinionRow(Pawn pawn, Pawn other, out bool situationalCached)
        {
            situationalCached = false;

            var row = new Dictionary<string, object>();
            row["name"] = BridgeCommon.SafeString(() => other.LabelShortCap.ToString());
            row["thingId"] = BridgeCommon.SafeString(() => other.ThingID);

            // ---- relation offsets, and the labels the social card would draw
            var labels = new List<object>();
            double relationOffset = 0.0;
            try
            {
                foreach (var def in pawn.GetRelations(other))
                {
                    if (def == null)
                        continue;
                    relationOffset += BridgeCommon.Try(() => def.opinionOffset, 0);
                    var label = BridgeCommon.SafeString(() => def.GetGenderSpecificLabelCap(other));
                    if (!string.IsNullOrEmpty(label))
                        labels.Add(label);
                }
            }
            catch { }
            row["relations"] = labels;

            // ---- social memories. memories.Memories is `ldarg.0; ldfld; ret`.
            double memoryOffset = 0.0;
            var memoryRows = new List<object>();
            ThoughtHandler handler = null;
            try
            {
                var mood = pawn.needs != null ? pawn.needs.mood : null;
                handler = mood != null ? mood.thoughts : null;
            }
            catch { handler = null; }

            var social = new List<ISocialThought>();
            if (handler != null)
            {
                try
                {
                    var memories = handler.memories != null ? handler.memories.Memories : null;
                    if (memories != null)
                    {
                        for (var i = 0; i < memories.Count; i++)
                        {
                            var st = memories[i] as ISocialThought;
                            if (st == null)
                                continue;
                            if (BridgeCommon.Try<Pawn>(() => st.OtherPawn(), null) != other)
                                continue;
                            social.Add(st);
                        }
                    }
                }
                catch { }
            }

            // ---- situational social, from the cache, never recalculated
            var situational = new List<ISocialThought>();
            if (handler != null && SocialCacheField != null && SocialActiveField != null)
            {
                try
                {
                    var sit = handler.situational;
                    var dict = sit != null ? SocialCacheField.GetValue(sit) as IDictionary : null;
                    if (dict != null && dict.Contains(other))
                    {
                        situationalCached = true;
                        var entry = dict[other];
                        var list = entry != null ? SocialActiveField.GetValue(entry) as IEnumerable : null;
                        if (list != null)
                        {
                            foreach (var item in list)
                            {
                                var st = item as ISocialThought;
                                if (st != null)
                                    situational.Add(st);
                            }
                        }
                    }
                }
                catch { situationalCached = false; }
            }

            // GetDistinctSocialThoughtGroups keeps the FIRST of each group and
            // drops the later duplicates; the same predicate, the same order.
            double situationalOffset = 0.0;
            var combined = new List<ISocialThought>();
            combined.AddRange(social);
            combined.AddRange(situational);
            var kept = new List<ISocialThought>();
            foreach (var candidate in combined)
            {
                var dup = false;
                foreach (var already in kept)
                {
                    var a = already as Thought;
                    var b = candidate as Thought;
                    if (a == null || b == null)
                        continue;
                    if (BridgeCommon.Try(() => a.GroupsWith(b), false))
                    {
                        dup = true;
                        break;
                    }
                }
                if (dup)
                    continue;
                kept.Add(candidate);

                var offset = BridgeCommon.Try(() => candidate.OpinionOffset(), 0f);
                if (float.IsNaN(offset) || float.IsInfinity(offset))
                    offset = 0f;
                if (situational.Contains(candidate))
                    situationalOffset += offset;
                else
                    memoryOffset += offset;

                if (Math.Abs(offset) >= 0.5)
                {
                    var mr = new Dictionary<string, object>();
                    var asThought = candidate as Thought;
                    mr["label"] = asThought != null ? BridgeCommon.SafeString(() => asThought.LabelCap) : null;
                    mr["opinionOffset"] = Math.Round((double)offset, 1);
                    memoryRows.Add(mr);
                }
            }

            // ---- OpinionOf's own tail, reproduced
            var total = relationOffset + memoryOffset + situationalOffset;
            if (Math.Abs(total) > 0.0001)
            {
                var factor = 1.0;
                try
                {
                    var hediffs = pawn.health != null && pawn.health.hediffSet != null
                        ? pawn.health.hediffSet.hediffs
                        : null;
                    if (hediffs != null)
                    {
                        for (var i = 0; i < hediffs.Count; i++)
                        {
                            var stage = BridgeCommon.Try<HediffStage>(() => hediffs[i].CurStage, null);
                            if (stage != null)
                                factor *= stage.opinionOfOthersFactor;
                        }
                    }
                }
                catch { }
                // Mathf.RoundToInt is Math.Round's default (to even), so this is
                // the same rounding OpinionOf itself does.
                total = Math.Round(total * factor);
            }
            if (total > 0 && BridgeCommon.Try(() => pawn.HostileTo(other), false))
                total = 0;
            if (total > 100) total = 100;
            if (total < -100) total = -100;

            row["opinion"] = (int)total;
            row["opinionParts"] = new Dictionary<string, object>
            {
                { "relations", Math.Round(relationOffset, 1) },
                { "memories", Math.Round(memoryOffset, 1) },
                { "situational", Math.Round(situationalOffset, 1) }
            };
            row["situationalSocialCached"] = situationalCached;
            row["drivers"] = memoryRows;
            return row;
        }
    }

    /// <summary>
    /// home/pawn_config — the WRITE half of the Work tab and the Assign tab.
    /// `dryRun` defaults to TRUE.
    ///
    /// ## Why this exists
    ///
    /// `WANTED.md` item 1 calls the work tab *"top priority among the tools
    /// themselves"*; item 11 lists schedule, allowed area, medical care,
    /// self-tend and hostility response and says *"no read or write path exists
    /// at all today"*. Until this tool, changing any of them meant driving the
    /// UI: open the tab, pair pixels out of a 200 KB `get_ui_layout`, click a
    /// cell whose state the layout does not carry (`isChecked: null` on every
    /// one), and hope. The read half is on `home/list_pawns` as four opt-in
    /// blocks, because the data hangs off the same `Pawn`; this is the write
    /// half, one tool, because a write needs a plan, a refusal channel and a
    /// dry run that a filter argument cannot give it.
    ///
    /// ## dryRun is TRUE by default and the after is READ BACK
    ///
    /// `WANTED.md`'s build note: *"give the first write tool a dry-run mode with
    /// a verified before and after."* So:
    ///
    ///   * `before` is the four read blocks, built by `PawnSettingsRead` — the
    ///     same code `home/list_pawns` uses, so the numbers cannot disagree.
    ///   * on a REAL run `after` is those blocks built AGAIN, after the writes.
    ///     It is never the values that were requested. A field the game refused
    ///     to keep shows up as an `after` that does not match, rather than as a
    ///     success.
    ///   * on a DRY run `after` is what the plan would produce, and
    ///     `afterIsPredicted` is true so nobody reads a simulation as a
    ///     measurement.
    ///
    /// `fields[]` carries one row per field the caller NAMED, with before,
    /// after, changed and — for anything refused — why. A refusal is an answer,
    /// never a silent skip: `refused[]` is the list `changed[]` is not.
    ///
    /// ## The Log.Error landmines, all of which pause the colony
    ///
    ///   1. `Pawn_WorkSettings.SetPriority(w, p)` opens with
    ///      `ConfirmInitializedDebug()`, which on a pawn with no priority table
    ///      logs an error AND writes a fresh table with six jobs on it. So
    ///      `EverWork` is checked first and a pawn without one is refused.
    ///   2. The same method logs an error when `priority != 0` and
    ///      `pawn.WorkTypeIsDisabled(w)`. So the disabled test is done HERE and
    ///      the write is refused with the reason, rather than pausing the game
    ///      to find out.
    ///   3. `Pawn_PlayerSettings.Master`'s setter calls
    ///      `pawn.training.HasLearned(Obedience)` — which throws on any pawn
    ///      with no training tracker (every human) — and `Log.ErrorOnce`s when
    ///      the animal has not learned obedience. Both are pre-checked.
    ///   4. `SetPriority` also `Log.Message`s an out-of-range priority; 0..4 is
    ///      validated here so it never gets one.
    ///
    /// ## Simple mode is obeyed, not overridden
    ///
    /// With `Current.Game.playSettings.useWorkPriorities` false the Work tab is
    /// a checkbox grid: `WidgetsWork.DrawWorkBoxFor`'s else-branch writes
    /// exactly 0 or 3. So a requested priority above 0 is applied as 3 and the
    /// reply says so in `manualPriorities: false` and in the field row's
    /// `note` — the tool does what the tab would do, and does not pretend the
    /// number landed.
    /// </summary>
    public sealed class HomePawnConfigTools
    {
        private const string ToolName = "home/pawn_config";

        [Tool(
            ToolName,
            Title = "Set a pawn's work priorities, schedule, assign-tab settings and animal training",
            Description =
                "Write tool for one pawn's configuration, with dryRun defaulting to TRUE. Sets any subset of: work priorities "
                + "(work=\"Cooking=1,Hauling=3\", 0 = never, 1 most urgent, 4 least), the 24-hour schedule (schedule=\"SSSSSSAAWWWWAAWWWWJJAASS\", "
                + "one letter per hour from the key home/list_pawns returns), medCare, hostilityResponse, selfTend, followDrafted, "
                + "followFieldwork, allowedArea (by name; \"none\" clears it), master, and -- for animals -- training "
                + "(training=\"Obedience=on,Release=off\", the Animals tab's tick boxes, which cascade the way the tab does), "
                + "slaughter and releaseToWild (the two Animals tab designations, on/off, mutually exclusive the way the game's own "
                + "designators make them). nickname renames the pawn: on a colonist only the nick between the first and last "
                + "name changes, an animal or a mech is renamed outright. Every field the caller names gets a before "
                + "and an after; on a real run the after is READ BACK from the game, never the value that was asked for. A value the "
                + "game will not accept is REFUSED with the reason -- a disabled work type, an out-of-range priority, an unknown area "
                + "-- and never silently skipped. The matching READ is home/list_pawns with "
                + "work/schedule/settings/relations/animals:true.",
            ResultDescription =
                "success, dryRun, applied, pawn{name,thingId}, fields[] (one row per field named: field, requested, before, after, "
                + "changed, refused, reason; plus cascades[] on a training row, alsoRemoved[] on a designation row, and "
                + "{name, nick} as before/after on a nickname row), changed[] "
                + "(English sentences), refused[], before{work,schedule,settings,animals} and "
                + "after{...} as the same blocks home/list_pawns emits, afterIsPredicted (true on a dry run), options{} (the areas "
                + "and enum values this pawn will accept) and notes.")]
        [ToolResponse("dryRun", "boolean", "True = nothing was written. Defaults to TRUE; a caller must pass dryRun:false deliberately.", Always = true)]
        [ToolResponse("applied", "boolean", "True only when at least one field was actually written to the game. False on every dry run.", Always = true)]
        [ToolResponse("fields", "array", "One row per field the caller named: field, requested, before, after, changed, refused, reason. A field that is absent here was never asked for. A drop row adds item (what was identified), carried (every item the pawn holds) and droppedAt (the cell it landed on, null when nothing was dropped).", Always = true)]
        [ToolResponse("refused", "array", "Every field this tool would not write, with the reason. Empty means nothing was refused - a refusal is never a silent skip.", Always = true)]
        [ToolResponse("before", "object", "work{}, schedule{}, settings{} and animals{} as they were, built by the same reader home/list_pawns uses. animals{} says applies:false on a humanlike rather than going missing.", Always = true)]
        [ToolResponse("after", "object", "The same four blocks after the writes. On a real run they are READ BACK from the game; on a dry run they are the predicted result and afterIsPredicted is true.", Always = true)]
        [ToolResponse("unknownArguments", "array", "Every argument key the caller sent that this tool does not declare, sorted, case-sensitively. Empty array = every key was recognised. On a WRITE tool this matters twice over: a misspelled dryRun is the difference between a plan and a changed colonist. The host's own _rimBridgeTimeoutMs is never listed.", Always = true)]
        [ToolResponse("unknownArgumentsWarning", "string", "Present only when unknownArguments is non-empty, or when the caller's raw keys could not be read at all - in which case the empty unknownArguments means 'not known', not 'nothing unknown'.", Nullable = true)]
        [ToolResponse("watch", "object", "The decorative half of the write: shown (bool), selected, inspectTab (ITab class name), mainTab (MainButtonDef defName), cameraMoved, leadMs, closesAfterSeconds, note and reason. On a real write the tab a player would use for the fields being written is opened BEFORE they are written and closes itself afterwards; note says which tab and why. shown:false with a reason on a dry run, when every field was refused, or when watch:false.", Always = true)]
        public async Task<object> PawnConfig(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            [ToolParameter(Description = "Which pawn: the name home/list_pawns prints (case-insensitive, a unique substring is enough) or the exact ThingID from settings.thingId.")] string pawn = null,
            [ToolParameter(Description = "Work priorities as \"WorkTypeDefName=priority\" pairs, comma separated: \"Cooking=1,Hauling=3,Doctor=0\". 0 = never do this, 1 = most urgent, 4 = least. Names are the defNames work{} returns (Cooking, PlantCutting, Doctor...). A work type this pawn cannot do is REFUSED with the reason, never skipped.")] string work = null,
            [ToolParameter(Description = "The whole 24-hour schedule as one letter per hour, hour 0 first, e.g. \"SSSSSSAAWWWWAAWWWWJJAASS\". The letters are the key{} that schedule{} returns (A Anything, W Work, J Joy, S Sleep, M Meditate). Must be exactly 24 characters; an unknown letter refuses the WHOLE schedule rather than writing half a day.")] string schedule = null,
            [ToolParameter(Description = "Medical care: NoCare, NoMeds, HerbalOrWorse, NormalOrWorse or Best.")] string medCare = null,
            [ToolParameter(Description = "Hostility response when attacked: Ignore, Attack or Flee.")] string hostilityResponse = null,
            [ToolParameter(Description = "Self-tend: \"on\" or \"off\". Omit to leave it alone. It is OFF by default for every colonist, which in a colony of one is fatal.")] string selfTend = null,
            [ToolParameter(Description = "Follow the drafted master: \"on\" or \"off\". Omit to leave it alone.")] string followDrafted = null,
            [ToolParameter(Description = "Follow the master doing fieldwork: \"on\" or \"off\". Omit to leave it alone.")] string followFieldwork = null,
            [ToolParameter(Description = "Allowed area by its exact label (case-insensitive), or \"none\"/\"unrestricted\" to clear the restriction. Omit to leave it alone. options.allowedAreas lists what this map has.")] string allowedArea = null,
            [ToolParameter(Description = "Master, by name or ThingID, or \"none\" to clear. Only an animal that has learned Obedience can have one; anything else is refused rather than logged.")] string master = null,
            [ToolParameter(Description = "Animal training, as \"TrainableDefName=on/off\" pairs, comma separated: \"Obedience=on,Release=off\". The names are the defNames home/list_pawns animals{}.training.trainables[].name returns (Tameness, Obedience, Release...). This is the Animals tab's tick box: it calls SetWantedRecursive, so turning one ON also turns its prerequisites on and turning one OFF turns everything depending on it off -- every def that will move is named in the field row's cascades[], on a dry run too. A trainable this animal cannot be assigned is REFUSED with RimWorld's own reason, in both directions, because that is the box the tab draws disabled. A pawn with no training tracker (every humanlike) is refused outright.")] string training = null,
            [ToolParameter(Description = "Mark or unmark this animal for slaughter: \"on\" or \"off\". Omit to leave it alone. Does what Designator_Slaughter does minus the sound and the popup, INCLUDING clearing any release-to-wild designation on the same animal (the game never leaves both standing). Refused for a non-animal, an animal that is not ours, a dead one, one in an aggressive mental state, or a non-flesh race. Already-marked is a no-op row, not a second designation -- a double add is a Log.Error, which pauses the colony.")] string slaughter = null,
            [ToolParameter(Description = "Mark or unmark this animal for release to the wild: \"on\" or \"off\". Omit to leave it alone. The mirror of slaughter, and clears a slaughter designation the same way. Additionally refused when RaceProps.canReleaseToWild is false for the race.")] string releaseToWild = null,
            [ToolParameter(Description = "Drop ONE item this pawn is carrying -- worn apparel, the wielded weapon, or something in the pack -- named by a case-insensitive substring of its label or by its exact ThingID. The drop is direct, so it lands IMMEDIATELY even on a paused game, and the item is left UNFORBIDDEN. A substring matching more than one carried item is refused with every match named; the field row's carried[] lists everything the pawn holds. Refused for a pawn that is not ours, a dead one, locked apparel, a quest lodger's bonded gear and anything with destroyOnDrop.")] string drop = null,
            [ToolParameter(Description = "Rename this pawn: the nickname the game shows. On a colonist with a first/nick/last name only the NICK is replaced, keeping the first and last name; an animal or a mech, which has a single name, is renamed outright. 1 to 32 characters, non-whitespace. Omit to leave the name alone. Refused for a pawn that is not ours.")] string nickname = null,
            [ToolParameter(Description = "TRUE by default. Read the before, plan every write and return it WITHOUT touching the game. Pass false to actually apply it.", DefaultValue = true)] bool dryRun = true,
            [ToolParameter(Description = "TRUE by default. On a real write, open the tab a player would use for these fields a moment BEFORE they are written - the Work, Schedule or Assign tab, or the pawn selected with its Health or Training tab - then close it again. Decorative only: it never changes what is written. Pass false to write with no UI.", DefaultValue = true)] bool watch = true,
            [ToolParameter(Description = "How long the tab stays open after the write, in seconds. Clamped 1..60. Ignored when watch is false or the write is a dry run.", DefaultValue = 8)] int watchSeconds = 8)
        {
            return BridgeCommon.WithUnknownArguments(
                await PawnConfigCore(
                    ctx, cancellationToken, pawn, work, schedule, medCare, hostilityResponse,
                    selfTend, followDrafted, followFieldwork, allowedArea, master,
                    training, slaughter, releaseToWild, drop, nickname, dryRun, watch, watchSeconds).ConfigureAwait(false),
                ctx, typeof(HomePawnConfigTools), ToolName);
        }

        private async Task<object> PawnConfigCore(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            string pawn,
            string work,
            string schedule,
            string medCare,
            string hostilityResponse,
            string selfTend,
            string followDrafted,
            string followFieldwork,
            string allowedArea,
            string master,
            string training,
            string slaughter,
            string releaseToWild,
            string drop,
            string nickname,
            bool dryRun,
            bool watch,
            int watchSeconds)
        {
            if (ctx?.MainThread == null)
                return Failure("No RimBridge main-thread dispatcher is available for this invocation.");

            // Companion tools are dispatched with MarshalToMainThread = false
            // (AnnotatedExtensionCapabilityProvider.InvokeAsync). Read, write and
            // read-back all go inside ONE hop, exactly as home/zone_cells does:
            // a tick between the write and the read-back would make the "after"
            // a different question from the one that was asked.
            if (dryRun)
            {
                var planned = await ctx.MainThread
                    .InvokeAsync(() => Run(pawn, work, schedule, medCare, hostilityResponse, selfTend,
                                           followDrafted, followFieldwork, allowedArea, master,
                                           training, slaughter, releaseToWild, drop, nickname, true),
                                 cancellationToken)
                    .ConfigureAwait(false);
                Stamp(planned, Watch.Skipped("dry run"));
                return planned;
            }

            // Hop 1: resolve the pawn, plan every field without writing, and -
            // when at least one field will really be written - open the tab a
            // player would use, so the change is seen landing inside it.
            var pass = await ctx.MainThread
                .InvokeAsync(() => Pass1(ctx, pawn, work, schedule, medCare, hostilityResponse, selfTend,
                                         followDrafted, followFieldwork, allowedArea, master,
                                         training, slaughter, releaseToWild, drop, nickname, watch),
                             cancellationToken)
                .ConfigureAwait(false);

            if (pass.Failure != null)
            {
                Stamp(pass.Failure, Watch.Skipped("refused"));
                return pass.Failure;
            }

            // Off the main thread: a moment with the tab open and nothing
            // changed yet.
            await Watch.Lead(pass.Session, cancellationToken).ConfigureAwait(false);

            // Hop 2: the real write. The pawn is resolved again here rather than
            // carried across the gap, and every field is re-planned against the
            // game as it is now.
            var session = pass.Session;
            var reason = pass.SkipReason;
            return await ctx.MainThread
                .InvokeAsync(() =>
                {
                    var reply = Run(pawn, work, schedule, medCare, hostilityResponse, selfTend,
                                    followDrafted, followFieldwork, allowedArea, master,
                                    training, slaughter, releaseToWild, drop, nickname, false);
                    Stamp(reply, session == null ? Watch.Skipped(reason) : Watch.Finish(session, watchSeconds));
                    return reply;
                }, cancellationToken)
                .ConfigureAwait(false);
        }

        /// <summary>Put the watch block on a reply, so `watch` is present on a
        /// refusal and a tool failure too and never has to be tested for.</summary>
        private static void Stamp(object reply, Dictionary<string, object> watch)
        {
            var payload = reply as Dictionary<string, object>;
            if (payload != null)
                payload["watch"] = watch;
        }

        /// <summary>What hop 1 hands to hop 2. Failure non-null means stop.</summary>
        private sealed class Pass1Result
        {
            internal object Failure;
            internal Watch.Session Session;
            internal string SkipReason;
        }

        /// <summary>Main thread. Resolve the pawn, plan every field as a dry run
        /// purely to learn whether anything will really be written, and open the
        /// tab that covers the most of those fields. Writes nothing.</summary>
        private static Pass1Result Pass1(IRimBridgeContext ctx, string pawnSpec, string workSpec, string scheduleSpec,
                                         string medCareSpec, string hostilitySpec, string selfTendSpec,
                                         string followDraftedSpec, string followFieldworkSpec,
                                         string areaSpec, string masterSpec,
                                         string trainingSpec, string slaughterSpec, string releaseToWildSpec,
                                         string dropSpec, string nicknameSpec, bool wantWatch)
        {
            Map map;
            string mapError;
            if (!BridgeCommon.TryGetMap(ToolName, out map, out mapError))
                return new Pass1Result { Failure = Failure(mapError) };

            var everyone = SafeAllPawns(map);
            if (everyone == null)
                return new Pass1Result { Failure = Failure("map.mapPawns.AllPawnsSpawned was not readable.") };

            Pawn target;
            string resolveError;
            if (!TryResolvePawn(everyone, pawnSpec, out target, out resolveError))
                return new Pass1Result { Failure = Failure(resolveError) };

            if (!wantWatch)
                return new Pass1Result { SkipReason = "watch:false" };

            var fields = new List<object>();
            var changes = new List<object>();
            var refused = new List<object>();
            PlanAll(target, map, everyone, workSpec, scheduleSpec, medCareSpec, hostilitySpec, selfTendSpec,
                    followDraftedSpec, followFieldworkSpec, areaSpec, masterSpec,
                    trainingSpec, slaughterSpec, releaseToWildSpec, dropSpec, nicknameSpec, true,
                    fields, changes, refused);

            if (fields.Count == 0)
                return new Pass1Result { SkipReason = "nothing to write" };
            if (fields.Count == refused.Count)
                return new Pass1Result { SkipReason = "refused" };

            var view = ChooseView(target, workSpec, scheduleSpec, medCareSpec, hostilitySpec, selfTendSpec,
                                  followDraftedSpec, followFieldworkSpec, areaSpec, masterSpec,
                                  trainingSpec, slaughterSpec, releaseToWildSpec, dropSpec, nicknameSpec);
            if (view == null)
                return new Pass1Result { SkipReason = "no tab covers these fields" };

            var session = Watch.Open(ctx, view.Target, view.InspectTab, Watch.Tab(view.MainTabName), view.Camera);
            session.Note = string.IsNullOrEmpty(session.Note) ? view.Note : view.Note + " " + session.Note;
            return new Pass1Result { Session = session };
        }

        // =================================================================== run

        private static object Run(string pawnSpec, string workSpec, string scheduleSpec,
                                  string medCareSpec, string hostilitySpec, string selfTendSpec,
                                  string followDraftedSpec, string followFieldworkSpec,
                                  string areaSpec, string masterSpec,
                                  string trainingSpec, string slaughterSpec, string releaseToWildSpec,
                                  string dropSpec, string nicknameSpec, bool dryRun)
        {
            Map map;
            string mapError;
            if (!BridgeCommon.TryGetMap(ToolName, out map, out mapError))
                return Failure(mapError);

            var everyone = SafeAllPawns(map);
            if (everyone == null)
                return Failure("map.mapPawns.AllPawnsSpawned was not readable.");

            Pawn target;
            string resolveError;
            if (!TryResolvePawn(everyone, pawnSpec, out target, out resolveError))
                return Failure(resolveError);

            var before = Blocks(target);

            var fields = new List<object>();
            var changes = new List<object>();
            var refused = new List<object>();
            var applied = PlanAll(target, map, everyone, workSpec, scheduleSpec, medCareSpec, hostilitySpec,
                                  selfTendSpec, followDraftedSpec, followFieldworkSpec, areaSpec, masterSpec,
                                  trainingSpec, slaughterSpec, releaseToWildSpec, dropSpec, nicknameSpec, dryRun,
                                  fields, changes, refused);

            // The after. On a real run this is a fresh read of the game, so a
            // write the game quietly declined shows up as a mismatch rather than
            // as a success. On a dry run nothing was written, so `after` is
            // `before` and every field row carries its own predicted value.
            var after = Blocks(target);

            var payload = new Dictionary<string, object>
            {
                { "success", true },
                { "tool", ToolName },
                { "dryRun", dryRun },
                { "applied", applied },
                { "afterIsPredicted", dryRun },
                { "pawn", new Dictionary<string, object>
                    {
                        { "name", BridgeCommon.SafeString(() => target.LabelShortCap.ToString()) },
                        { "thingId", BridgeCommon.SafeString(() => target.ThingID) },
                        { "isColonist", BridgeCommon.Try(() => target.IsColonist, false) },
                        { "isAnimal", BridgeCommon.Try(() => target.RaceProps != null && target.RaceProps.Animal, false) }
                    } },
                { "fields", fields },
                { "fieldCount", fields.Count },
                { "changed", changes },
                { "changeCount", changes.Count },
                { "refused", refused },
                { "refusedCount", refused.Count },
                { "before", before },
                { "after", after },
                { "options", PawnSettingsRead.Options(map) },
                { "notes", new Dictionary<string, object>
                    {
                        { "dryRunMeaning", dryRun
                            ? "NOTHING WAS WRITTEN. after{} is identical to before{}; each fields[] row carries the value the write WOULD produce. Run again with dryRun:false to apply."
                            : "Applied. after{} was READ BACK from the game after the writes -- it is not an echo of what was requested, so a field whose after does not match its request was declined by the game." },
                        { "refusalsAreAnswers", "A field this tool will not write appears in refused[] with a reason and does NOT appear in changed[]. Nothing is ever silently skipped; a field absent from fields[] was never asked for." },
                        { "workPriorityScale", "0 = never do this job. 1 is the most urgent, 4 the least. With manualPriorities false the Work tab is a checkbox grid and any request above 0 is applied as 3, which is what clicking the box does." },
                        { "readSide", "The matching read is home/list_pawns with work:true / schedule:true / settings:true / relations:true / animals:true. before{} and after{} here are those very blocks, from the same builders." },
                        { "trainingCascades", "training= calls Pawn_TrainingTracker.SetWantedRecursive, which is what the Animals tab's checkbox calls. Turning a trainable ON turns every prerequisite ON; turning one OFF turns everything that depends on it OFF. Each training row names the defs it will move in cascades[], on a dry run as well as a real one, and on a real run any def the cascade moved that the caller did not name gets its own line in changed[]." },
                        { "designationsExclude", "slaughter and releaseToWild are mutually exclusive: setting either one ON clears the other, exactly as Designator_Slaughter.DesignateThing and Designator_ReleaseAnimalToWild.DesignateThing do. The field row's alsoRemoved[] names what was cleared. Setting a designation that is already set is a NO-OP row (changed:false), never a second AddDesignation -- that call is a Verse.Log.Error, which pauses the colony." }
                    } }
            };

            return payload;
        }

        /// <summary>Every requested field, planned and - unless dryRun - applied,
        /// in the order the tabs sit in. Returns whether the game was touched.
        /// Hop 1 calls it with dryRun true purely to learn what a real run would
        /// do; hop 2 calls it again for real.</summary>
        private static bool PlanAll(Pawn target, Map map, List<Pawn> everyone,
                                    string workSpec, string scheduleSpec, string medCareSpec, string hostilitySpec,
                                    string selfTendSpec, string followDraftedSpec, string followFieldworkSpec,
                                    string areaSpec, string masterSpec, string trainingSpec,
                                    string slaughterSpec, string releaseToWildSpec, string dropSpec,
                                    string nicknameSpec, bool dryRun,
                                    List<object> fields, List<object> changes, List<object> refused)
        {
            var applied = false;

            // ---------------------------------------------------------- work
            if (!string.IsNullOrEmpty(workSpec))
                applied |= PlanWork(target, workSpec, dryRun, fields, changes, refused);

            // ------------------------------------------------------ schedule
            if (!string.IsNullOrEmpty(scheduleSpec))
                applied |= PlanSchedule(target, scheduleSpec, dryRun, fields, changes, refused);

            // -------------------------------------------------- playerSettings
            applied |= PlanEnum<MedicalCareCategory>(target, "medCare", medCareSpec, dryRun, fields, changes, refused,
                ps => ps.medCare.ToString(), (ps, v) => ps.medCare = v);
            applied |= PlanEnum<HostilityResponseMode>(target, "hostilityResponse", hostilitySpec, dryRun, fields, changes, refused,
                ps => ps.hostilityResponse.ToString(), (ps, v) => ps.hostilityResponse = v);
            applied |= PlanBool(target, "selfTend", selfTendSpec, dryRun, fields, changes, refused,
                ps => ps.selfTend, (ps, v) => ps.selfTend = v);
            applied |= PlanBool(target, "followDrafted", followDraftedSpec, dryRun, fields, changes, refused,
                ps => ps.followDrafted, (ps, v) => ps.followDrafted = v);
            applied |= PlanBool(target, "followFieldwork", followFieldworkSpec, dryRun, fields, changes, refused,
                ps => ps.followFieldwork, (ps, v) => ps.followFieldwork = v);

            // --------------------------------------------------- allowed area
            if (!string.IsNullOrEmpty(areaSpec))
                applied |= PlanArea(target, map, areaSpec, dryRun, fields, changes, refused);

            // --------------------------------------------------------- master
            if (!string.IsNullOrEmpty(masterSpec))
                applied |= PlanMaster(target, everyone, masterSpec, dryRun, fields, changes, refused);

            // -------------------------------------------------------- animals
            if (!string.IsNullOrEmpty(trainingSpec))
                applied |= PlanTraining(target, trainingSpec, dryRun, fields, changes, refused);

            var slaughterDef = BridgeCommon.Try<DesignationDef>(() => DesignationDefOf.Slaughter, null);
            var releaseDef = BridgeCommon.Try<DesignationDef>(() => DesignationDefOf.ReleaseAnimalToWild, null);
            if (!string.IsNullOrEmpty(slaughterSpec))
                applied |= PlanDesignation(target, "slaughter", slaughterDef, releaseDef, slaughterSpec, dryRun,
                                           fields, changes, refused);
            if (!string.IsNullOrEmpty(releaseToWildSpec))
                applied |= PlanDesignation(target, "releaseToWild", releaseDef, slaughterDef, releaseToWildSpec, dryRun,
                                           fields, changes, refused);

            // ----------------------------------------------------------- gear
            if (!string.IsNullOrEmpty(dropSpec))
                applied |= PlanDrop(target, dropSpec, dryRun, fields, changes, refused);

            // ----------------------------------------------------------- name
            if (!string.IsNullOrEmpty(nicknameSpec))
                applied |= PlanNickname(target, nicknameSpec, dryRun, fields, changes, refused);

            return applied;
        }

        // =============================================================== watch

        /// <summary>Which menu a player would have open to make these changes.</summary>
        private sealed class View
        {
            internal object Target;
            internal Type InspectTab;
            internal string MainTabName;
            internal bool Camera;
            internal string Note;
        }

        /// <summary>
        /// Pick the one tab that covers the most of the fields being written,
        /// counting each work priority and each trainable separately, and say so
        /// in note. Five views, in the order they win a tie:
        ///   Work tab       work priorities
        ///   Schedule tab   the 24-hour schedule
        ///   Assign tab     the allowed area
        ///   Health ITab    medCare, hostilityResponse, selfTend
        ///   Gear ITab      drop
        ///   Character ITab nickname - the Training ITab instead on an animal,
        ///                  and nothing but the selection when the def has
        ///                  neither
        ///   Training ITab  training, slaughter, releaseToWild, master and the
        ///                  follow toggles - the tab that draws all of them, and
        ///                  the only one of the five that needs the pawn selected
        ///                  along with the Health one.
        /// </summary>
        private static View ChooseView(Pawn target, string workSpec, string scheduleSpec,
                                       string medCareSpec, string hostilitySpec, string selfTendSpec,
                                       string followDraftedSpec, string followFieldworkSpec,
                                       string areaSpec, string masterSpec,
                                       string trainingSpec, string slaughterSpec, string releaseToWildSpec,
                                       string dropSpec, string nicknameSpec)
        {
            var work = Pairs(workSpec);
            var schedule = string.IsNullOrEmpty(scheduleSpec) ? 0 : 1;
            var assign = string.IsNullOrEmpty(areaSpec) ? 0 : 1;
            var health = One(medCareSpec) + One(hostilitySpec) + One(selfTendSpec);
            var training = Pairs(trainingSpec) + One(slaughterSpec) + One(releaseToWildSpec)
                           + One(masterSpec) + One(followDraftedSpec) + One(followFieldworkSpec);

            var gear = One(dropSpec);
            var bio = One(nicknameSpec);

            var total = work + schedule + assign + health + gear + bio + training;
            if (total == 0)
                return null;

            var name = BridgeCommon.SafeString(() => target.LabelShortCap.ToString()) ?? "the pawn";
            var best = work;
            if (schedule > best) best = schedule;
            if (assign > best) best = assign;
            if (health > best) best = health;
            if (gear > best) best = gear;
            if (bio > best) best = bio;
            if (training > best) best = training;

            if (work == best)
                return new View { MainTabName = "Work", Note = Covers("the Work tab", work, total, name) };
            if (schedule == best)
                return new View { MainTabName = "Schedule", Note = Covers("the Schedule tab", schedule, total, name) };
            if (assign == best)
                return new View { MainTabName = "Assign", Note = Covers("the Assign tab", assign, total, name) };
            if (health == best)
                return new View
                {
                    Target = target,
                    InspectTab = typeof(ITab_Pawn_Health),
                    Camera = true,
                    Note = Covers(name + "'s Health tab", health, total, name)
                };
            if (gear == best)
                return new View
                {
                    Target = target,
                    InspectTab = typeof(ITab_Pawn_Gear),
                    Camera = true,
                    Note = Covers(name + "'s Gear tab", gear, total, name)
                };
            if (bio == best)
            {
                var nameTab = NameTab(target);
                return new View
                {
                    Target = target,
                    InspectTab = nameTab,
                    Camera = true,
                    Note = nameTab == null
                        ? "Selected " + name + " with the camera on them; this pawn's def draws no character or training tab."
                        : Covers(name + "'s " + nameTab.Name.Replace("ITab_Pawn_", "") + " tab", bio, total, name)
                };
            }
            return new View
            {
                Target = target,
                InspectTab = typeof(ITab_Pawn_Training),
                Camera = true,
                Note = Covers(name + "'s Training tab", training, total, name)
            };
        }

        private static string Covers(string what, int covered, int total, string name)
        {
            if (covered == total)
                return "Opened " + what + ", which is where a player would set this.";
            return "Opened " + what + ", which covers " + covered + " of the " + total
                   + " fields written on " + name + "; the rest are set from other tabs and are not shown.";
        }

        /// <summary>Comma-separated NAME=VALUE pairs, counted.</summary>
        private static int Pairs(string spec)
        {
            if (string.IsNullOrEmpty(spec))
                return 0;
            return spec.Split(new[] { ',', ';' }, StringSplitOptions.RemoveEmptyEntries).Length;
        }

        private static int One(string spec)
        {
            return string.IsNullOrEmpty(spec) ? 0 : 1;
        }

        /// <summary>The four writable blocks. relations{} is not here: nothing in
        /// it can be written, so it has no before and no after. animals{} IS
        /// here, and is `applies: false` on every humanlike rather than absent.</summary>
        private static Dictionary<string, object> Blocks(Pawn pawn)
        {
            return new Dictionary<string, object>
            {
                { "work", PawnSettingsRead.WorkBlock(pawn) },
                { "schedule", PawnSettingsRead.ScheduleBlock(pawn) },
                { "settings", PawnSettingsRead.SettingsBlock(pawn) },
                { "animals", PawnSettingsRead.AnimalBlock(pawn) }
            };
        }

        // ================================================================ work

        private static bool PlanWork(Pawn pawn, string spec, bool dryRun,
                                     List<object> fields, List<object> changes, List<object> refused)
        {
            Pawn_WorkSettings settings = null;
            try { settings = pawn.workSettings; }
            catch { settings = null; }

            var everWork = settings != null && BridgeCommon.Try(() => settings.EverWork, false);
            var manual = PawnSettingsRead.ManualPriorities();
            var byName = new Dictionary<string, WorkTypeDef>(StringComparer.OrdinalIgnoreCase);
            foreach (var def in PawnSettingsRead.WorkTypesInTabOrder())
            {
                var name = BridgeCommon.SafeString(() => def.defName);
                if (!string.IsNullOrEmpty(name))
                    byName[name] = def;
            }

            var applied = false;
            foreach (var pair in (spec ?? string.Empty).Split(new[] { ',', ';' }, StringSplitOptions.RemoveEmptyEntries))
            {
                var bits = pair.Split('=');
                var name = bits[0].Trim();
                if (name.Length == 0)
                    continue;

                var field = "work." + name;
                if (bits.Length != 2)
                {
                    Refuse(fields, refused, field, pair.Trim(), null,
                        "Not a NAME=PRIORITY pair. Write work as \"Cooking=1,Hauling=3\".");
                    continue;
                }

                int priority;
                if (!int.TryParse(bits[1].Trim(), out priority))
                {
                    Refuse(fields, refused, field, bits[1].Trim(), null,
                        "Priority \"" + bits[1].Trim() + "\" is not a number. 0 = never, 1 = most urgent, 4 = least.");
                    continue;
                }

                WorkTypeDef def2;
                if (!byName.TryGetValue(name, out def2))
                {
                    Refuse(fields, refused, field, priority, null,
                        "No WorkTypeDef named \"" + name + "\". The names are the defNames in home/list_pawns work{}.types[].name.");
                    continue;
                }

                if (!everWork)
                {
                    Refuse(fields, refused, field, priority, null,
                        settings == null
                            ? "This pawn has no work settings at all (animals, mechanoids, other factions)."
                            : "Pawn_WorkSettings.EverWork is false -- this pawn has never had a priority table, and writing one would have called Log.Error (which pauses the colony) and assigned six jobs.");
                    continue;
                }

                if (priority < 0 || priority > 4)
                {
                    Refuse(fields, refused, field, priority, BridgeCommon.TryN(() => settings.GetPriority(def2)),
                        "Priority must be 0..4 (0 = never, 1 = most urgent, 4 = least). RimWorld would log a message and store it anyway.");
                    continue;
                }

                if (priority != 0 && BridgeCommon.Try(() => pawn.WorkTypeIsDisabled(def2), false))
                {
                    Refuse(fields, refused, field, priority, BridgeCommon.TryN(() => settings.GetPriority(def2)),
                        "This pawn cannot do " + name + " at all -- Pawn.WorkTypeIsDisabled is true (backstory, trait, gene, hediff, age or quest). SetPriority would have called Log.Error, which pauses the colony. Only priority 0 is accepted for a disabled work type.");
                    continue;
                }

                var want = priority;
                string note = null;
                if (manual == false && want > 0 && want != 3)
                {
                    want = 3;
                    note = "The Work tab is in simple (checkbox) mode, where clicking a box writes exactly 3. Applied as 3; turn numbered priorities on in the tab to store " + priority + ".";
                }

                var beforeValue = BridgeCommon.TryN(() => settings.GetPriority(def2));
                object afterValue = want;
                if (!dryRun)
                {
                    var wrote = false;
                    try
                    {
                        settings.SetPriority(def2, want);
                        wrote = true;
                    }
                    catch { wrote = false; }
                    if (!wrote)
                    {
                        Refuse(fields, refused, field, priority, beforeValue,
                            "Pawn_WorkSettings.SetPriority threw. Nothing was written for this work type.");
                        continue;
                    }
                    applied = true;
                    // Read back rather than echo: the game masks priorities in
                    // simple mode and this is where that would show.
                    afterValue = BridgeCommon.TryN(() => settings.GetPriority(def2));
                }

                var changed = !Equals(beforeValue, afterValue);
                Record(fields, field, priority, beforeValue, afterValue, changed, note);
                if (changed)
                    changes.Add(name + ": priority " + Show(beforeValue) + " -> " + Show(afterValue)
                                + (note != null ? " (" + note + ")" : ""));
            }
            return applied;
        }

        // ============================================================ schedule

        private static bool PlanSchedule(Pawn pawn, string spec, bool dryRun,
                                         List<object> fields, List<object> changes, List<object> refused)
        {
            var trimmed = (spec ?? string.Empty).Trim();
            var beforeBlock = PawnSettingsRead.ScheduleBlock(pawn);
            var beforeValue = beforeBlock.ContainsKey("hours") ? beforeBlock["hours"] : null;

            Pawn_TimetableTracker timetable = null;
            try { timetable = pawn.timetable; }
            catch { timetable = null; }

            if (timetable == null)
            {
                Refuse(fields, refused, "schedule", trimmed, beforeValue,
                    "This pawn has no timetable tracker (animals, other factions). There is no schedule to set.");
                return false;
            }

            List<TimeAssignmentDef> times;
            try { times = timetable.times; }
            catch { times = null; }
            if (times == null || times.Count != 24)
            {
                Refuse(fields, refused, "schedule", trimmed, beforeValue,
                    "This pawn's timetable does not hold 24 hours (" + (times == null ? "unreadable" : times.Count.ToString())
                    + "). Refused rather than reshaped.");
                return false;
            }

            if (trimmed.Length != 24)
            {
                Refuse(fields, refused, "schedule", trimmed, beforeValue,
                    "A schedule is exactly 24 characters, one per hour starting at hour 0. Got " + trimmed.Length
                    + ". The letters are in home/list_pawns schedule{}.key.");
                return false;
            }

            // Parse the WHOLE string before writing anything: half a day of new
            // schedule and half of old is worse than either.
            var wanted = new List<TimeAssignmentDef>(24);
            for (var h = 0; h < 24; h++)
            {
                var letter = trimmed[h].ToString();
                var def = PawnSettingsRead.AssignmentForLetter(letter);
                if (def == null)
                {
                    var keys = string.Join(", ", PawnSettingsRead.LetterKey()
                        .Select(kv => kv.Key + "=" + kv.Value).ToArray());
                    Refuse(fields, refused, "schedule", trimmed, beforeValue,
                        "Hour " + h + " is \"" + letter + "\", which is not a time assignment. Valid letters: " + keys
                        + ". The whole schedule was refused -- nothing was written.");
                    return false;
                }
                wanted.Add(def);
            }

            var predicted = string.Concat(wanted.Select(PawnSettingsRead.LetterFor).ToArray());
            object afterValue = predicted;
            var applied = false;
            if (!dryRun)
            {
                try
                {
                    for (var h = 0; h < 24; h++)
                        timetable.SetAssignment(h, wanted[h]);
                    applied = true;
                }
                catch
                {
                    Refuse(fields, refused, "schedule", trimmed, beforeValue,
                        "Pawn_TimetableTracker.SetAssignment threw partway. Re-read the schedule before trusting it.");
                    return false;
                }
                var reread = PawnSettingsRead.ScheduleBlock(pawn);
                afterValue = reread.ContainsKey("hours") ? reread["hours"] : null;
            }

            var changed = !Equals(beforeValue, afterValue);
            Record(fields, "schedule", trimmed, beforeValue, afterValue, changed, null);
            if (changed)
                changes.Add("schedule: " + Show(beforeValue) + " -> " + Show(afterValue));
            return applied;
        }

        // ====================================================== playerSettings

        private static bool PlanEnum<T>(Pawn pawn, string field, string spec, bool dryRun,
                                        List<object> fields, List<object> changes, List<object> refused,
                                        Func<Pawn_PlayerSettings, string> read,
                                        Action<Pawn_PlayerSettings, T> write) where T : struct
        {
            if (string.IsNullOrEmpty(spec))
                return false;

            Pawn_PlayerSettings ps;
            if (!Settings(pawn, out ps))
            {
                Refuse(fields, refused, field, spec, null,
                    "This pawn has no playerSettings at all (wild animals, other factions).");
                return false;
            }

            var wanted = spec.Trim();
            T value;
            var names = Enum.GetNames(typeof(T));
            var match = names.FirstOrDefault(n => string.Equals(n, wanted, StringComparison.OrdinalIgnoreCase));
            if (match == null)
            {
                Refuse(fields, refused, field, wanted, BridgeCommon.SafeString(() => read(ps)),
                    "\"" + wanted + "\" is not one of: " + string.Join(", ", names) + ".");
                return false;
            }
            value = (T)Enum.Parse(typeof(T), match);

            var beforeValue = BridgeCommon.SafeString(() => read(ps));
            object afterValue = match;
            var applied = false;
            if (!dryRun)
            {
                try { write(ps, value); applied = true; }
                catch
                {
                    Refuse(fields, refused, field, wanted, beforeValue, "Writing " + field + " threw. Nothing changed.");
                    return false;
                }
                afterValue = BridgeCommon.SafeString(() => read(ps));
            }

            var changed = !Equals(beforeValue, afterValue);
            Record(fields, field, match, beforeValue, afterValue, changed, null);
            if (changed)
                changes.Add(field + ": " + Show(beforeValue) + " -> " + Show(afterValue));
            return applied;
        }

        private static bool PlanBool(Pawn pawn, string field, string spec, bool dryRun,
                                     List<object> fields, List<object> changes, List<object> refused,
                                     Func<Pawn_PlayerSettings, bool> read,
                                     Action<Pawn_PlayerSettings, bool> write)
        {
            if (string.IsNullOrEmpty(spec))
                return false;

            Pawn_PlayerSettings ps;
            if (!Settings(pawn, out ps))
            {
                Refuse(fields, refused, field, spec, null,
                    "This pawn has no playerSettings at all (wild animals, other factions).");
                return false;
            }

            bool wanted;
            if (!TryParseOnOff(spec, out wanted))
            {
                Refuse(fields, refused, field, spec.Trim(), BridgeCommon.TryN(() => read(ps)),
                    "\"" + spec.Trim() + "\" is not on/off. Accepted: on, off, true, false, yes, no, 1, 0.");
                return false;
            }

            var beforeValue = BridgeCommon.TryN(() => read(ps));
            object afterValue = wanted;
            var applied = false;
            if (!dryRun)
            {
                try { write(ps, wanted); applied = true; }
                catch
                {
                    Refuse(fields, refused, field, wanted, beforeValue, "Writing " + field + " threw. Nothing changed.");
                    return false;
                }
                afterValue = BridgeCommon.TryN(() => read(ps));
            }

            var changed = !Equals(beforeValue, afterValue);
            Record(fields, field, wanted, beforeValue, afterValue, changed, null);
            if (changed)
                changes.Add(field + ": " + Show(beforeValue) + " -> " + Show(afterValue));
            return applied;
        }

        private static bool PlanArea(Pawn pawn, Map map, string spec, bool dryRun,
                                     List<object> fields, List<object> changes, List<object> refused)
        {
            const string field = "allowedArea";

            Pawn_PlayerSettings ps;
            if (!Settings(pawn, out ps))
            {
                Refuse(fields, refused, field, spec, null,
                    "This pawn has no playerSettings at all (wild animals, other factions).");
                return false;
            }

            var beforeArea = BridgeCommon.Try<Area>(() => ps.AreaRestrictionInPawnCurrentMap, null);
            var beforeValue = beforeArea != null ? BridgeCommon.SafeString(() => beforeArea.Label) : null;

            var wanted = spec.Trim();
            var clearing = string.Equals(wanted, "none", StringComparison.OrdinalIgnoreCase)
                        || string.Equals(wanted, "unrestricted", StringComparison.OrdinalIgnoreCase);

            Area area = null;
            if (!clearing)
            {
                List<Area> all;
                try
                {
                    var manager = map != null ? map.areaManager : null;
                    all = manager != null && manager.AllAreas != null ? manager.AllAreas.ToList() : new List<Area>();
                }
                catch { all = new List<Area>(); }

                var candidates = all
                    .Where(a => a != null && BridgeCommon.Try(() => a.AssignableAsAllowed(), false))
                    .ToList();
                area = candidates.FirstOrDefault(a =>
                    string.Equals(BridgeCommon.SafeString(() => a.Label), wanted, StringComparison.OrdinalIgnoreCase));

                if (area == null)
                {
                    var names = string.Join(", ", candidates
                        .Select(a => BridgeCommon.SafeString(() => a.Label) ?? "?").ToArray());
                    Refuse(fields, refused, field, wanted, beforeValue,
                        "No assignable area on this map is called \"" + wanted + "\". Assignable areas: "
                        + (names.Length == 0 ? "(none)" : names)
                        + ". Pass \"none\" to clear the restriction instead.");
                    return false;
                }
            }

            if (BridgeCommon.Try(() => !ps.SupportsAllowedAreas, false))
            {
                Refuse(fields, refused, field, wanted, beforeValue,
                    "This pawn does not support area control (a roamer, or a race with disableAreaControl). The setting would not stick.");
                return false;
            }

            if (BridgeCommon.Try<Map>(() => pawn.MapHeld, null) == null)
            {
                Refuse(fields, refused, field, wanted, beforeValue,
                    "This pawn is not held by any map, and the area setter keys off MapHeld -- the write would have gone nowhere silently.");
                return false;
            }

            object afterValue = clearing ? null : BridgeCommon.SafeString(() => area.Label);
            var applied = false;
            if (!dryRun)
            {
                try { ps.AreaRestrictionInPawnCurrentMap = area; applied = true; }
                catch
                {
                    Refuse(fields, refused, field, wanted, beforeValue, "Setting the allowed area threw. Nothing changed.");
                    return false;
                }
                var reread = BridgeCommon.Try<Area>(() => ps.AreaRestrictionInPawnCurrentMap, null);
                afterValue = reread != null ? BridgeCommon.SafeString(() => reread.Label) : null;
            }

            var changed = !Equals(beforeValue, afterValue);
            Record(fields, field, clearing ? "none" : wanted, beforeValue, afterValue, changed,
                clearing ? "null after = UNRESTRICTED, the whole map. That is a setting, not a failed read." : null);
            if (changed)
                changes.Add("allowedArea: " + Show(beforeValue) + " -> " + Show(afterValue));
            return applied;
        }

        private static bool PlanMaster(Pawn pawn, List<Pawn> everyone, string spec, bool dryRun,
                                       List<object> fields, List<object> changes, List<object> refused)
        {
            const string field = "master";

            Pawn_PlayerSettings ps;
            if (!Settings(pawn, out ps))
            {
                Refuse(fields, refused, field, spec, null,
                    "This pawn has no playerSettings at all (wild animals, other factions).");
                return false;
            }

            var beforeMaster = BridgeCommon.Try<Pawn>(() => ps.Master, null);
            var beforeValue = beforeMaster != null
                ? BridgeCommon.SafeString(() => beforeMaster.LabelShortCap.ToString())
                : null;

            var wanted = spec.Trim();
            var clearing = string.Equals(wanted, "none", StringComparison.OrdinalIgnoreCase);

            Pawn newMaster = null;
            if (!clearing)
            {
                // The Master SETTER calls pawn.training.HasLearned(Obedience).
                // A human has no training tracker at all, so that is a
                // NullReferenceException, and an untrained animal is a
                // Log.ErrorOnce -- which pauses the colony. Both are checked
                // before the setter is ever reached.
                var training = BridgeCommon.Try<Pawn_TrainingTracker>(() => pawn.training, null);
                if (training == null)
                {
                    Refuse(fields, refused, field, wanted, beforeValue,
                        "Only an animal can have a master: this pawn has no training tracker, and RimWorld's own setter would throw reading it.");
                    return false;
                }
                if (!BridgeCommon.Try(() => training.HasLearned(TrainableDefOf.Obedience), false))
                {
                    Refuse(fields, refused, field, wanted, beforeValue,
                        "This animal has not learned Obedience, and the setter Log.ErrorOnce's on that -- which pauses the colony. Train obedience first.");
                    return false;
                }

                string masterError;
                if (!TryResolvePawn(everyone, wanted, out newMaster, out masterError))
                {
                    Refuse(fields, refused, field, wanted, beforeValue, masterError);
                    return false;
                }
                if (!BridgeCommon.Try(() => newMaster.IsColonist, false))
                {
                    Refuse(fields, refused, field, wanted, beforeValue,
                        "\"" + wanted + "\" resolved to a pawn who is not a colonist. A master must be one of ours.");
                    return false;
                }
            }

            object afterValue = newMaster != null
                ? BridgeCommon.SafeString(() => newMaster.LabelShortCap.ToString())
                : null;
            var applied = false;
            if (!dryRun)
            {
                try { ps.Master = newMaster; applied = true; }
                catch
                {
                    Refuse(fields, refused, field, wanted, beforeValue, "Setting the master threw. Nothing changed.");
                    return false;
                }
                var reread = BridgeCommon.Try<Pawn>(() => ps.Master, null);
                afterValue = reread != null ? BridgeCommon.SafeString(() => reread.LabelShortCap.ToString()) : null;
            }

            var changed = !Equals(beforeValue, afterValue);
            Record(fields, field, clearing ? "none" : wanted, beforeValue, afterValue, changed, null);
            if (changed)
                changes.Add("master: " + Show(beforeValue) + " -> " + Show(afterValue));
            return applied;
        }

        // ============================================================ animals

        /// <summary>
        /// Tick and untick the Animals tab's training boxes.
        ///
        /// Spec shape is `work`'s, deliberately: `"Obedience=on,Release=off"`,
        /// comma separated, `on/off/true/false/yes/no/1/0`. Every parameter this
        /// DLL declares is a string, an int or a bool — the SDK binder does
        /// accept a `Dictionary&lt;string,object&gt;` parameter, but no tool here
        /// has ever used one, and a caller that already writes
        /// `work="Cooking=1,Hauling=3"` should not have to learn a second
        /// argument grammar one field over.
        ///
        /// ## What the write actually is
        ///
        /// `Pawn_TrainingTracker.SetWanted` is **private**;
        /// `SetWantedRecursive(td, checkOn)` is the public one, and is exactly
        /// what `TrainingCardUtility.DoTrainableCheckbox` calls when the box
        /// changes. So it is what is called here, and it **cascades**: ticking
        /// one on ticks every prerequisite on, and ticking one off ticks
        /// everything that depends on it off. That is the game's rule, not this
        /// tool's, and every def it will touch is named in the field row's
        /// `cascades[]` on a dry run as well as a real one — a write that
        /// silently changed three other boxes would be the exact failure this
        /// toolkit is built against.
        ///
        /// ## Refusals
        ///
        /// A trainable whose `CanAssignToTrain` is rejected is refused **in both
        /// directions**, with RimWorld's own reason text, because that is what
        /// the tab does: `PawnColumnWorker_Trainable` draws no cell at all when
        /// the report is not accepted, and `DoTrainableCheckbox` passes
        /// `disabled: !canTrain.Accepted`. A humanlike (no training tracker at
        /// all) is refused once, for the whole field.
        /// </summary>
        private static bool PlanTraining(Pawn pawn, string spec, bool dryRun,
                                         List<object> fields, List<object> changes, List<object> refused)
        {
            Pawn_TrainingTracker training = null;
            try { training = pawn.training; }
            catch { training = null; }

            if (training == null)
            {
                Refuse(fields, refused, "training", spec.Trim(), null,
                    "This pawn has no training tracker at all -- only animals have one. Training cannot be set on a humanlike, "
                    + "a mechanoid or anything else without RaceProps.Animal.");
                return false;
            }

            var defs = PawnSettingsRead.TrainableDefsInOrder();
            var byName = new Dictionary<string, TrainableDef>(StringComparer.OrdinalIgnoreCase);
            foreach (var td in defs)
            {
                if (td == null)
                    continue;
                var name = BridgeCommon.SafeString(() => td.defName);
                if (!string.IsNullOrEmpty(name))
                    byName[name] = td;
            }

            // Snapshot every wanted flag before anything is written, so the
            // cascade SetWantedRecursive performs can be REPORTED rather than
            // discovered later by a confused reader.
            var wantedBefore = WantedSnapshot(training, defs);
            var named = new HashSet<string>(StringComparer.Ordinal);

            var applied = false;
            foreach (var pair in (spec ?? string.Empty).Split(new[] { ',', ';' }, StringSplitOptions.RemoveEmptyEntries))
            {
                var bits = pair.Split('=');
                var name = bits[0].Trim();
                if (name.Length == 0)
                    continue;

                var field = "training." + name;
                if (bits.Length != 2)
                {
                    Refuse(fields, refused, field, pair.Trim(), null,
                        "Not a NAME=on/off pair. Write training as \"Obedience=on,Release=off\".");
                    continue;
                }

                bool want;
                if (!TryParseOnOff(bits[1], out want))
                {
                    Refuse(fields, refused, field, bits[1].Trim(), null,
                        "\"" + bits[1].Trim() + "\" is not on/off. Accepted: on, off, true, false, yes, no, 1, 0.");
                    continue;
                }

                TrainableDef td;
                if (!byName.TryGetValue(name, out td))
                {
                    var names = string.Join(", ", byName.Keys.ToArray());
                    Refuse(fields, refused, field, want, null,
                        "No TrainableDef named \"" + name + "\". This game has: " + (names.Length == 0 ? "(none)" : names)
                        + ". The names are the defNames in home/list_pawns animals{}.training.trainables[].name.");
                    continue;
                }

                var beforeValue = BridgeCommon.TryN(() => training.GetWanted(td));

                bool visible;
                string reason;
                if (!CanAssign(training, td, out visible, out reason))
                {
                    Refuse(fields, refused, field, want, beforeValue,
                        (visible
                            ? "RimWorld will not let this animal be assigned " + name + ": "
                            : "RimWorld does not show " + name + " for this race at all (an untrainable tag, or a DLC-only special "
                              + "trainable): ")
                        + (reason ?? "no reason given")
                        + " The Animals tab draws this box disabled, in both directions, so it is refused in both directions.");
                    continue;
                }

                named.Add(BridgeCommon.SafeString(() => td.defName) ?? name);

                var cascades = CascadeNames(defs, td, want);
                string note = null;
                if (cascades.Count > 0)
                {
                    note = "SetWantedRecursive also sets " + string.Join(", ", cascades.ToArray()) + " to "
                         + (want ? "on (they are prerequisites of " : "off (they depend on ") + name
                         + "). That is RimWorld's own rule, not this tool's.";
                }

                object afterValue = want;
                if (!dryRun)
                {
                    var wrote = false;
                    try
                    {
                        training.SetWantedRecursive(td, want);
                        wrote = true;
                    }
                    catch { wrote = false; }
                    if (!wrote)
                    {
                        Refuse(fields, refused, field, want, beforeValue,
                            "Pawn_TrainingTracker.SetWantedRecursive threw. Nothing was written for this trainable.");
                        continue;
                    }
                    applied = true;
                    // Read back rather than echo.
                    afterValue = BridgeCommon.TryN(() => training.GetWanted(td));
                }

                var changed = !Equals(beforeValue, afterValue);
                var row = RecordRow(field, want, beforeValue, afterValue, changed, note);
                row["cascades"] = cascades.Cast<object>().ToList();
                fields.Add(row);
                if (changed)
                    changes.Add(name + ": wanted " + Show(beforeValue) + " -> " + Show(afterValue));
            }

            // Anything the cascade moved that the caller did not name, said out
            // loud. On a dry run nothing moved, so this loop finds nothing and
            // the per-row cascades[] is the only warning -- which is why it is
            // there.
            if (applied)
            {
                var wantedAfter = WantedSnapshot(training, defs);
                foreach (var pair in wantedAfter)
                {
                    bool was;
                    if (!wantedBefore.TryGetValue(pair.Key, out was) || was == pair.Value)
                        continue;
                    if (named.Contains(pair.Key))
                        continue;
                    changes.Add(pair.Key + ": wanted " + Show(was) + " -> " + Show(pair.Value)
                                + " (cascaded by SetWantedRecursive, not named in the request)");
                }
            }

            return applied;
        }

        private static Dictionary<string, bool> WantedSnapshot(Pawn_TrainingTracker training, List<TrainableDef> defs)
        {
            var map = new Dictionary<string, bool>(StringComparer.Ordinal);
            foreach (var td in defs)
            {
                if (td == null)
                    continue;
                var name = BridgeCommon.SafeString(() => td.defName);
                if (string.IsNullOrEmpty(name))
                    continue;
                map[name] = BridgeCommon.Try(() => training.GetWanted(td), false);
            }
            return map;
        }

        /// <summary>
        /// The defs `SetWantedRecursive(td, on)` will also touch, by RimWorld's
        /// own rule read off its IL: turning one ON walks `td.prerequisites`
        /// recursively; turning one OFF walks every def whose `prerequisites`
        /// contain it, recursively. Computed here so a DRY RUN can name them.
        /// </summary>
        private static List<string> CascadeNames(List<TrainableDef> defs, TrainableDef td, bool on)
        {
            var found = new List<string>();
            var seen = new HashSet<TrainableDef>();
            var queue = new List<TrainableDef> { td };
            seen.Add(td);

            try
            {
                for (var i = 0; i < queue.Count; i++)
                {
                    var current = queue[i];
                    if (on)
                    {
                        var prereqs = current.prerequisites;
                        if (prereqs == null)
                            continue;
                        foreach (var p in prereqs)
                        {
                            if (p == null || !seen.Add(p))
                                continue;
                            queue.Add(p);
                            found.Add(p.defName);
                        }
                    }
                    else
                    {
                        foreach (var other in defs)
                        {
                            if (other == null || other.prerequisites == null
                                || !other.prerequisites.Contains(current) || !seen.Add(other))
                                continue;
                            queue.Add(other);
                            found.Add(other.defName);
                        }
                    }
                }
            }
            catch { }
            return found;
        }

        private static bool CanAssign(Pawn_TrainingTracker training, TrainableDef td, out bool visible, out string reason)
        {
            visible = false;
            reason = null;
            try
            {
                bool vis;
                var report = training.CanAssignToTrain(td, out vis);
                visible = vis;
                if (report.Accepted)
                    return true;
                reason = string.IsNullOrEmpty(report.Reason) ? "no reason text was returned." : report.Reason;
                return false;
            }
            catch
            {
                reason = "Pawn_TrainingTracker.CanAssignToTrain threw, so this cannot be assigned safely.";
                return false;
            }
        }

        /// <summary>
        /// Add or remove one of the Animals tab's designations — Slaughter or
        /// ReleaseAnimalToWild — doing exactly what the designator does minus
        /// the sound, the mouse icon and the bonded-animal popup.
        ///
        /// ## What the designator does, read off its IL
        ///
        /// `Designator_Slaughter.CanDesignateThing` accepts a pawn that
        /// `IsAnimal`, is in the player faction, carries no Slaughter
        /// designation already, and is not `InAggroMentalState`.
        /// `DesignateThing` then adds the designation **and removes any
        /// ReleaseAnimalToWild on the same pawn**.
        /// `Designator_ReleaseAnimalToWild` is the mirror image and additionally
        /// requires `RaceProps.canReleaseToWild` and a living pawn. Both
        /// exclusions are performed here and reported in the field row's
        /// `alsoRemoved`; leaving both designations standing is a state the game
        /// itself never produces.
        ///
        /// ## The Log.Error that would pause the colony
        ///
        /// `DesignationManager.AddDesignation` opens with
        /// `if (DesignationOn(thing, def) != null) { Log.Error("Tried to
        /// double-add designation on Thing"); return; }`, and `Log.Error` calls
        /// `TickManager.Pause()`. So the presence check happens HERE and an
        /// already-marked pawn is a no-op row (`changed: false`), never a
        /// second add. `RemoveDesignation` on something not indexed is a
        /// `Log.Warning`, which does not pause, but the same check makes it
        /// unreachable anyway.
        ///
        /// `AddDesignation` also calls `SetForbidden(false, warnOnFail: false)`
        /// on the target and throws meta puffs at it. The first does nothing to
        /// a pawn with no `CompForbiddable` and cannot log with `warnOnFail`
        /// false; the second is the visual feedback a stream viewer should see.
        /// </summary>
        private static bool PlanDesignation(Pawn pawn, string field, DesignationDef def, DesignationDef excludes,
                                            string spec, bool dryRun,
                                            List<object> fields, List<object> changes, List<object> refused)
        {
            if (string.IsNullOrEmpty(spec))
                return false;

            if (def == null)
            {
                Refuse(fields, refused, field, spec.Trim(), null,
                    "The DesignationDef this field writes was not found in this build's DefDatabase. Nothing was attempted.");
                return false;
            }

            bool want;
            if (!TryParseOnOff(spec, out want))
            {
                Refuse(fields, refused, field, spec.Trim(), null,
                    "\"" + spec.Trim() + "\" is not on/off. Accepted: on, off, true, false, yes, no, 1, 0.");
                return false;
            }

            var beforeValue = PawnSettingsRead.DesignationOnSafe(pawn, def) != null;

            Map map = null;
            try { map = pawn.MapHeld; }
            catch { map = null; }
            var manager = map != null ? BridgeCommon.Try<DesignationManager>(() => map.designationManager, null) : null;
            if (manager == null)
            {
                Refuse(fields, refused, field, want, beforeValue,
                    "This pawn is not held by any map, so there is no designation manager to write to. The designator keys off "
                    + "MapHeld too -- the write would have gone nowhere silently.");
                return false;
            }

            if (want)
            {
                if (!BridgeCommon.Try(() => pawn.RaceProps != null && pawn.RaceProps.Animal, false))
                {
                    Refuse(fields, refused, field, want, beforeValue,
                        "Only an animal can be marked: RaceProps.Animal is false. The designator's own CanDesignateThing "
                        + "requires Pawn.IsAnimal, so this would never have been accepted in the UI either.");
                    return false;
                }
                var player = PawnSettingsRead.PlayerFactionSilent();
                var faction = BridgeCommon.Try<Faction>(() => pawn.Faction, null);
                if (player == null || faction == null || faction != player)
                {
                    Refuse(fields, refused, field, want, beforeValue,
                        "This animal is not ours (faction is " + (faction == null ? "none -- it is wild" : "\"" + BridgeCommon.SafeString(() => faction.Name) + "\"")
                        + "). The designator requires pawn.Faction == the player faction. Tame it first.");
                    return false;
                }
                if (BridgeCommon.Try(() => pawn.Dead, false))
                {
                    Refuse(fields, refused, field, want, beforeValue, "This animal is dead. There is nothing to mark.");
                    return false;
                }
                if (BridgeCommon.Try(() => pawn.InAggroMentalState, false))
                {
                    Refuse(fields, refused, field, want, beforeValue,
                        "This animal is in an aggressive mental state (manhunter, berserk). Designator_Slaughter refuses that, "
                        + "and so does this.");
                    return false;
                }
                if (def == BridgeCommon.Try<DesignationDef>(() => DesignationDefOf.Slaughter, null)
                    && !BridgeCommon.Try(() => pawn.RaceProps.IsFlesh, false))
                {
                    Refuse(fields, refused, field, want, beforeValue,
                        "This animal is not flesh, so the Animals tab draws no slaughter box for it (PawnColumnWorker_Slaughter "
                        + "requires RaceProps.IsFlesh).");
                    return false;
                }
                if (def == BridgeCommon.Try<DesignationDef>(() => DesignationDefOf.ReleaseAnimalToWild, null)
                    && !BridgeCommon.Try(() => pawn.RaceProps.canReleaseToWild, false))
                {
                    Refuse(fields, refused, field, want, beforeValue,
                        "RaceProps.canReleaseToWild is false for this race -- Designator_ReleaseAnimalToWild refuses it.");
                    return false;
                }
            }

            var alsoRemoved = new List<object>();
            object afterValue = want;
            var applied = false;

            if (want == beforeValue)
            {
                // Nothing to do, and saying so is the answer. Adding a second
                // designation is the Log.Error that pauses the colony.
                afterValue = beforeValue;
            }
            else if (!dryRun)
            {
                try
                {
                    if (want)
                    {
                        manager.AddDesignation(new Designation(pawn, def));
                        if (excludes != null)
                        {
                            var opposite = PawnSettingsRead.DesignationOnSafe(pawn, excludes);
                            if (opposite != null)
                            {
                                manager.RemoveDesignation(opposite);
                                alsoRemoved.Add(BridgeCommon.SafeString(() => excludes.defName));
                            }
                        }
                    }
                    else
                    {
                        var existing = PawnSettingsRead.DesignationOnSafe(pawn, def);
                        if (existing != null)
                            manager.RemoveDesignation(existing);
                    }
                    applied = true;
                }
                catch
                {
                    Refuse(fields, refused, field, want, beforeValue,
                        "Writing the designation threw. Re-read animals{}.designations before trusting anything.");
                    return false;
                }
                afterValue = PawnSettingsRead.DesignationOnSafe(pawn, def) != null;
            }
            else if (want && excludes != null && PawnSettingsRead.DesignationOnSafe(pawn, excludes) != null)
            {
                // Dry run: the exclusion is part of the plan and must be in it.
                alsoRemoved.Add(BridgeCommon.SafeString(() => excludes.defName));
            }

            var changed = !Equals(beforeValue, afterValue);
            var row = RecordRow(field, want, beforeValue, afterValue, changed,
                want == beforeValue
                    ? "Already " + (want ? "marked" : "unmarked") + " -- nothing was written. A second AddDesignation is a "
                      + "Verse.Log.Error, which pauses the colony."
                    : null);
            row["alsoRemoved"] = alsoRemoved;
            fields.Add(row);
            if (changed)
                changes.Add(field + ": " + Show(beforeValue) + " -> " + Show(afterValue)
                            + (alsoRemoved.Count > 0
                                ? " (and cleared " + string.Join(", ", alsoRemoved.Select(x => Show(x)).ToArray()) + ")"
                                : ""));
            return applied;
        }

        // ================================================================ gear

        /// <summary>One thing the pawn is carrying, with the tracker holding
        /// it. The three slots are the three the Gear tab draws.</summary>
        private sealed class Carried
        {
            internal Thing Thing;
            internal string Slot;
        }

        /// <summary>Worn apparel, wielded equipment and the pack, in the order
        /// the Gear tab lists them. Never throws; a tracker that is not there
        /// contributes nothing.</summary>
        private static List<Carried> CarriedItems(Pawn pawn)
        {
            var list = new List<Carried>();

            try
            {
                var worn = pawn.apparel != null ? pawn.apparel.WornApparel : null;
                if (worn != null)
                {
                    for (var i = 0; i < worn.Count; i++)
                    {
                        if (worn[i] != null)
                            list.Add(new Carried { Thing = worn[i], Slot = "apparel" });
                    }
                }
            }
            catch { }

            try
            {
                var equipped = pawn.equipment != null ? pawn.equipment.AllEquipmentListForReading : null;
                if (equipped != null)
                {
                    for (var i = 0; i < equipped.Count; i++)
                    {
                        if (equipped[i] != null)
                            list.Add(new Carried { Thing = equipped[i], Slot = "equipment" });
                    }
                }
            }
            catch { }

            try
            {
                var pack = pawn.inventory != null ? pawn.inventory.innerContainer : null;
                if (pack != null)
                {
                    for (var i = 0; i < pack.Count; i++)
                    {
                        if (pack[i] != null)
                            list.Add(new Carried { Thing = pack[i], Slot = "inventory" });
                    }
                }
            }
            catch { }

            return list;
        }

        /// <summary>The label a substring is matched against and the row a
        /// caller reads. LabelCap is what the Gear tab prints, quality and
        /// stuff included.</summary>
        private static string ItemLabel(Thing thing)
        {
            return BridgeCommon.SafeString(() => thing.LabelCap.ToString())
                   ?? BridgeCommon.SafeString(() => thing.def != null ? thing.def.label : null);
        }

        private static Dictionary<string, object> ItemRow(Carried carried)
        {
            var thing = carried.Thing;
            return new Dictionary<string, object>
            {
                { "slot", carried.Slot },
                { "label", ItemLabel(thing) },
                { "defName", BridgeCommon.SafeString(() => thing.def != null ? thing.def.defName : null) },
                { "thingId", BridgeCommon.SafeString(() => thing.ThingID) },
                { "stackCount", BridgeCommon.TryN(() => thing.stackCount) },
                { "isApparel", thing is Apparel },
                { "isWeapon", BridgeCommon.Try(() => thing.def != null && thing.def.IsWeapon, false) }
            };
        }

        private static string Named(Carried carried)
        {
            return (ItemLabel(carried.Thing) ?? "?") + " [" + carried.Slot + "] "
                   + (BridgeCommon.SafeString(() => carried.Thing.ThingID) ?? "?");
        }

        /// <summary>Where a thing is right now, read back after the write:
        /// "map" once it is lying on the ground, otherwise the slot still
        /// holding it, otherwise "gone".</summary>
        private static string HolderNow(Pawn pawn, Thing thing)
        {
            if (thing == null)
                return "gone";
            if (BridgeCommon.Try(() => thing.Spawned, false))
                return "map";
            foreach (var carried in CarriedItems(pawn))
            {
                if (ReferenceEquals(carried.Thing, thing))
                    return carried.Slot;
            }
            return "gone";
        }

        /// <summary>
        /// Drop ONE carried item where the pawn stands.
        ///
        /// Direct, not a job. `ITab_Pawn_Gear.InterfaceDrop` queues
        /// `JobDefOf.RemoveApparel` for apparel and calls the tracker directly
        /// only for equipment and inventory, so the Gear tab's own drop button
        /// does nothing at all on a paused game. These three calls are what the
        /// job would eventually reach, so a paused colony still drops.
        ///
        /// Verified against Assembly-CSharp 1.6.9676.17735:
        /// `Pawn_ApparelTracker.WornApparel`/`.IsLocked(Apparel)`/
        /// `.TryDrop(Apparel, out Apparel, IntVec3, bool)`,
        /// `Pawn_EquipmentTracker.AllEquipmentListForReading`/
        /// `.TryDropEquipment(ThingWithComps, out ThingWithComps, IntVec3, bool)`,
        /// `Pawn_InventoryTracker.innerContainer`,
        /// `ThingOwner.TryDrop(Thing, IntVec3, Map, ThingPlaceMode, out Thing, ...)`,
        /// `EquipmentUtility.QuestLodgerCanUnequip(Thing, Pawn)` — the test the
        /// Gear tab uses to grey its own button out — and `ThingDef.destroyOnDrop`.
        /// `forbid: false` on the two tracker calls, so nothing lands forbidden.
        /// </summary>
        private static bool PlanDrop(Pawn pawn, string spec, bool dryRun,
                                     List<object> fields, List<object> changes, List<object> refused)
        {
            var wanted = (spec ?? string.Empty).Trim();
            if (wanted.Length == 0)
                return false;

            var carried = CarriedItems(pawn);
            var carriedRows = carried.Select(c => (object)ItemRow(c)).ToList();

            var player = PawnSettingsRead.PlayerFactionSilent();
            var faction = BridgeCommon.Try<Faction>(() => pawn.Faction, null);
            if (player == null || faction == null || faction != player)
            {
                RefuseDrop(fields, refused, wanted, carriedRows,
                    "This pawn is not ours (faction is "
                    + (faction == null ? "none" : "\"" + BridgeCommon.SafeString(() => faction.Name) + "\"")
                    + "). The Gear tab draws no drop button for anyone the colony does not control.");
                return false;
            }

            if (BridgeCommon.Try(() => pawn.Dead, false))
            {
                RefuseDrop(fields, refused, wanted, carriedRows,
                    "This pawn is dead. Strip the corpse instead; dropping through the living pawn's trackers is not what "
                    + "the game does to a body.");
                return false;
            }

            var map = BridgeCommon.Try<Map>(() => pawn.MapHeld, null);
            var cell = BridgeCommon.Try(() => pawn.PositionHeld, IntVec3.Invalid);
            if (map == null || !cell.IsValid)
            {
                RefuseDrop(fields, refused, wanted, carriedRows,
                    "This pawn is not held by any map (a caravan, or in transit), so there is no cell to drop onto.");
                return false;
            }

            if (carried.Count == 0)
            {
                RefuseDrop(fields, refused, wanted, carriedRows,
                    "This pawn carries nothing: no worn apparel, no equipment and an empty pack.");
                return false;
            }

            var chosen = carried.FirstOrDefault(c =>
                string.Equals(BridgeCommon.SafeString(() => c.Thing.ThingID), wanted, StringComparison.Ordinal));
            if (chosen == null)
            {
                var matches = carried.Where(c =>
                {
                    var label = ItemLabel(c.Thing);
                    return label != null && label.IndexOf(wanted, StringComparison.OrdinalIgnoreCase) >= 0;
                }).ToList();

                if (matches.Count == 0)
                {
                    RefuseDrop(fields, refused, wanted, carriedRows,
                        "Nothing this pawn carries has \"" + wanted + "\" in its label. The " + carried.Count
                        + " item(s) held: " + string.Join(", ", carried.Select(Named).ToArray()) + ".");
                    return false;
                }
                if (matches.Count > 1)
                {
                    RefuseDrop(fields, refused, wanted, carriedRows,
                        "\"" + wanted + "\" matches " + matches.Count + " carried items: "
                        + string.Join(", ", matches.Select(Named).ToArray())
                        + ". This tool writes, so it will not pick one. Be exact, or pass one of those ThingIDs.");
                    return false;
                }
                chosen = matches[0];
            }

            var thing = chosen.Thing;
            var itemRow = ItemRow(chosen);
            var before = chosen.Slot;

            if (BridgeCommon.Try(() => thing.def != null && thing.def.destroyOnDrop, false))
            {
                RefuseDrop(fields, refused, wanted, carriedRows, itemRow, before,
                    "ThingDef.destroyOnDrop is true for this item: dropping it deletes it, which is why the Gear tab draws no "
                    + "drop button for it.");
                return false;
            }

            var worn = chosen.Slot == "apparel" ? thing as Apparel : null;
            if (worn != null && BridgeCommon.Try(() => pawn.apparel.IsLocked(worn), false))
            {
                RefuseDrop(fields, refused, wanted, carriedRows, itemRow, before,
                    "This apparel is LOCKED onto the pawn (Pawn_ApparelTracker.IsLocked). The game will not take it off "
                    + "either.");
                return false;
            }

            if (!BridgeCommon.Try(() => EquipmentUtility.QuestLodgerCanUnequip(thing, pawn), true))
            {
                RefuseDrop(fields, refused, wanted, carriedRows, itemRow, before,
                    "EquipmentUtility.QuestLodgerCanUnequip says no: this is a quest lodger's own bonded or biocoded gear, "
                    + "and taking it would break the quest's terms.");
                return false;
            }

            object after = "map";
            object droppedAt = BridgeCommon.Pos(cell);
            var applied = false;

            if (!dryRun)
            {
                Thing landed = null;
                var ok = false;
                try
                {
                    if (worn != null)
                    {
                        Apparel resulting;
                        ok = pawn.apparel.TryDrop(worn, out resulting, cell, false);
                        landed = resulting;
                    }
                    else if (chosen.Slot == "equipment")
                    {
                        ThingWithComps resulting;
                        ok = pawn.equipment.TryDropEquipment((ThingWithComps)thing, out resulting, cell, false);
                        landed = resulting;
                    }
                    else
                    {
                        Thing resulting;
                        ok = pawn.inventory.innerContainer.TryDrop(thing, cell, map, ThingPlaceMode.Near, out resulting);
                        landed = resulting;
                    }
                }
                catch (Exception ex)
                {
                    RefuseDrop(fields, refused, wanted, carriedRows, itemRow, before,
                        "The drop threw " + ex.GetType().Name + ". Re-read the pawn's gear before trusting anything.");
                    return false;
                }

                if (!ok)
                {
                    RefuseDrop(fields, refused, wanted, carriedRows, itemRow, before,
                        "The game's own tracker refused the drop and gave no reason. Nothing moved.");
                    return false;
                }

                applied = true;
                after = HolderNow(pawn, landed ?? thing);
                var restingCell = landed != null && BridgeCommon.Try(() => landed.Spawned, false)
                    ? BridgeCommon.Try(() => landed.Position, IntVec3.Invalid)
                    : IntVec3.Invalid;
                droppedAt = restingCell.IsValid ? BridgeCommon.Pos(restingCell) : null;
                // item{} stays the item as it was CARRIED. A near-place can
                // merge the drop into a stack already on the ground, and
                // reporting that stack's count as the dropped one would be a
                // number nobody asked for.
            }

            var changed = !Equals(before, after);
            var row = RecordRow("drop", wanted, before, after, changed,
                dryRun
                    ? "Nothing was dropped. On a real run this lands on the pawn's own cell immediately, paused or not, and "
                      + "is not forbidden."
                    : null);
            row["item"] = itemRow;
            row["droppedAt"] = droppedAt;
            row["carried"] = carriedRows;
            fields.Add(row);
            if (changed)
                changes.Add("drop: " + (ItemLabel(thing) ?? wanted) + " " + Show(before) + " -> " + Show(after)
                            + (droppedAt == null ? "" : " at " + CellText(droppedAt)));
            return applied;
        }

        private static string CellText(object pos)
        {
            var d = pos as Dictionary<string, object>;
            if (d == null)
                return "?";
            object x, z;
            d.TryGetValue("x", out x);
            d.TryGetValue("z", out z);
            return "(" + Show(x) + ", " + Show(z) + ")";
        }

        /// <summary>A refused drop still carries carried[] — the list a caller
        /// needs to name the item properly next time — and item/droppedAt, so
        /// every drop row has one shape.</summary>
        private static void RefuseDrop(List<object> fields, List<object> refused, string requested,
                                       List<object> carried, string reason)
        {
            RefuseDrop(fields, refused, requested, carried, null, null, reason);
        }

        private static void RefuseDrop(List<object> fields, List<object> refused, string requested,
                                       List<object> carried, Dictionary<string, object> item, string before,
                                       string reason)
        {
            var countBefore = refused.Count;
            Refuse(fields, refused, "drop", requested, before, reason);
            for (var i = countBefore; i < refused.Count; i++)
            {
                var row = refused[i] as Dictionary<string, object>;
                if (row == null)
                    continue;
                row["item"] = item;
                row["droppedAt"] = null;
                row["carried"] = carried;
            }
        }

        // ================================================================ name

        /// <summary>The longest nickname this tool will write.</summary>
        private const int NicknameMaxLength = 32;

        /// <summary>
        /// Replace the pawn's nickname. A NameTriple keeps its first and last
        /// name and gets a new nick; a NameSingle (animals, mechs) and a null
        /// Name (an unnamed animal) become a NameSingle. `Pawn.Name` is a plain
        /// field setter, so the write lands immediately, paused or not.
        /// </summary>
        private static bool PlanNickname(Pawn pawn, string spec, bool dryRun,
                                         List<object> fields, List<object> changes, List<object> refused)
        {
            const string field = "nickname";

            var wanted = (spec ?? string.Empty).Trim();
            var before = NameRow(pawn);

            if (wanted.Length == 0)
            {
                Refuse(fields, refused, field, spec, before,
                    "A nickname must hold at least one non-whitespace character. Omit nickname to leave the name alone.");
                return false;
            }
            if (wanted.Length > NicknameMaxLength)
            {
                Refuse(fields, refused, field, wanted, before,
                    "\"" + wanted + "\" is " + wanted.Length + " characters; the limit here is " + NicknameMaxLength + ".");
                return false;
            }

            var player = PawnSettingsRead.PlayerFactionSilent();
            var faction = BridgeCommon.Try<Faction>(() => pawn.Faction, null);
            if (player == null || faction == null || faction != player)
            {
                Refuse(fields, refused, field, wanted, before,
                    "This pawn is not ours (faction is "
                    + (faction == null ? "none -- it is wild" : "\"" + BridgeCommon.SafeString(() => faction.Name) + "\"")
                    + "). The game draws a rename button only for a pawn the colony controls.");
                return false;
            }

            var current = BridgeCommon.Try<Verse.Name>(() => pawn.Name, null);
            Verse.Name renamed;
            var triple = current as NameTriple;
            if (triple != null)
            {
                // The NameTriple constructor Trims first and last, so a null on
                // either side would throw inside the game's own code.
                var first = BridgeCommon.SafeString(() => triple.First) ?? string.Empty;
                var last = BridgeCommon.SafeString(() => triple.Last) ?? string.Empty;
                renamed = BridgeCommon.Try<Verse.Name>(() => new NameTriple(first, wanted, last), null);
            }
            else if (current == null || current is NameSingle)
            {
                renamed = BridgeCommon.Try<Verse.Name>(() => new NameSingle(wanted), null);
            }
            else
            {
                Refuse(fields, refused, field, wanted, before,
                    "This pawn's Name is a " + current.GetType().Name
                    + ", which is neither NameTriple nor NameSingle. Nothing was attempted.");
                return false;
            }

            if (renamed == null)
            {
                Refuse(fields, refused, field, wanted, before,
                    "Building the new Name threw. Nothing changed.");
                return false;
            }

            object after = NameRow(renamed);
            var applied = false;
            if (!dryRun)
            {
                try
                {
                    pawn.Name = renamed;
                    applied = true;
                }
                catch (Exception ex)
                {
                    Refuse(fields, refused, field, wanted, before,
                        "Writing the name threw " + ex.GetType().Name + ". Nothing changed.");
                    return false;
                }
                after = NameRow(pawn);
            }

            var changed = !string.Equals(Part(before, "name"), Part(after, "name"), StringComparison.Ordinal);
            Record(fields, field, wanted, before, after, changed,
                dryRun
                    ? "Nothing was written. On a real run the whole Name object is replaced; a first and last name are kept."
                    : null);
            if (changed)
                changes.Add("nickname: " + Show(Part(before, "nick")) + " -> " + Show(Part(after, "nick")));
            return applied;
        }

        /// <summary>A name as the two strings the game itself prints: the full
        /// name, and the short one it labels the pawn with.</summary>
        private static Dictionary<string, object> NameRow(Pawn pawn)
        {
            return NameRow(BridgeCommon.Try<Verse.Name>(() => pawn.Name, null));
        }

        private static Dictionary<string, object> NameRow(Verse.Name name)
        {
            return new Dictionary<string, object>
            {
                { "name", name == null ? null : BridgeCommon.SafeString(() => name.ToStringFull) },
                { "nick", name == null ? null : BridgeCommon.SafeString(() => name.ToStringShort) }
            };
        }

        private static string Part(object row, string key)
        {
            var d = row as Dictionary<string, object>;
            object value;
            if (d == null || !d.TryGetValue(key, out value))
                return null;
            return value as string;
        }

        /// <summary>The tab a player renames from: the bio card on a humanlike,
        /// the training card on an animal. Null when the pawn's def draws
        /// neither, in which case the watch only selects the pawn.</summary>
        private static Type NameTab(Pawn pawn)
        {
            if (BridgeCommon.Try(() => pawn.RaceProps != null && pawn.RaceProps.Humanlike, false))
                return typeof(ITab_Pawn_Character);
            return HasInspectTab(pawn, typeof(ITab_Pawn_Training)) ? typeof(ITab_Pawn_Training) : null;
        }

        private static bool HasInspectTab(Pawn pawn, Type tab)
        {
            var tabs = BridgeCommon.Try<List<InspectTabBase>>(
                () => pawn.def == null ? null : pawn.def.inspectorTabsResolved, null);
            if (tabs == null)
                return false;
            for (var i = 0; i < tabs.Count; i++)
                if (tabs[i] != null && tab.IsInstanceOfType(tabs[i]))
                    return true;
            return false;
        }

        // ============================================================= helpers

        private static bool Settings(Pawn pawn, out Pawn_PlayerSettings ps)
        {
            ps = BridgeCommon.Try<Pawn_PlayerSettings>(() => pawn.playerSettings, null);
            return ps != null;
        }

        private static bool TryParseOnOff(string spec, out bool value)
        {
            value = false;
            var s = (spec ?? string.Empty).Trim().ToLowerInvariant();
            if (s == "on" || s == "true" || s == "yes" || s == "1") { value = true; return true; }
            if (s == "off" || s == "false" || s == "no" || s == "0") { value = false; return true; }
            return false;
        }

        private static void Record(List<object> fields, string field, object requested,
                                   object before, object after, bool changed, string note)
        {
            fields.Add(RecordRow(field, requested, before, after, changed, note));
        }

        /// <summary>The same row, handed back instead of appended, for the two
        /// animal fields that carry an extra key (`cascades`, `alsoRemoved`).
        /// One builder, so no field row can grow a different shape.</summary>
        private static Dictionary<string, object> RecordRow(string field, object requested,
                                                            object before, object after, bool changed, string note)
        {
            var row = new Dictionary<string, object>
            {
                { "field", field },
                { "requested", requested },
                { "before", before },
                { "after", after },
                { "changed", changed },
                { "refused", false },
                { "reason", null }
            };
            if (note != null)
                row["note"] = note;
            return row;
        }

        private static void Refuse(List<object> fields, List<object> refused, string field,
                                   object requested, object before, string reason)
        {
            var row = new Dictionary<string, object>
            {
                { "field", field },
                { "requested", requested },
                { "before", before },
                // A refused field's `after` is its `before`: nothing happened.
                // Emitting null here would read as "cleared".
                { "after", before },
                { "changed", false },
                { "refused", true },
                { "reason", reason }
            };
            fields.Add(row);
            refused.Add(row);
        }

        private static string Show(object v)
        {
            if (v == null)
                return "(none)";
            if (v is bool)
                return (bool)v ? "on" : "off";
            return v.ToString();
        }

        private static List<Pawn> SafeAllPawns(Map map)
        {
            try { return map.mapPawns?.AllPawnsSpawned?.ToList(); }
            catch { return null; }
        }

        /// <summary>
        /// A pawn by exact ThingID, exact name, or a unique case-insensitive
        /// substring of the name. An AMBIGUOUS name is refused with every match
        /// named, never resolved to the first one: this tool writes.
        /// </summary>
        private static bool TryResolvePawn(List<Pawn> everyone, string spec, out Pawn pawn, out string error)
        {
            pawn = null;
            error = string.Empty;

            var wanted = (spec ?? string.Empty).Trim();
            if (wanted.Length == 0)
            {
                error = "No pawn given. Pass pawn=\"<name>\" or the ThingID from home/list_pawns settings{}.thingId.";
                return false;
            }

            var byId = everyone.FirstOrDefault(p => p != null &&
                string.Equals(BridgeCommon.SafeString(() => p.ThingID), wanted, StringComparison.Ordinal));
            if (byId != null)
            {
                pawn = byId;
                return true;
            }

            var exact = everyone.Where(p => p != null &&
                string.Equals(BridgeCommon.SafeString(() => p.LabelShortCap.ToString()), wanted, StringComparison.OrdinalIgnoreCase))
                .ToList();
            if (exact.Count == 1)
            {
                pawn = exact[0];
                return true;
            }
            if (exact.Count > 1)
            {
                // Animals share a race name, so this is the ORDINARY case for
                // them, not the odd one. Counting them and stopping would leave
                // a caller with no way forward; the ThingIDs are the way.
                var ids = string.Join(", ", exact
                    .Select(pn => (BridgeCommon.SafeString(() => pn.LabelShortCap.ToString()) ?? "?")
                                  + " " + (BridgeCommon.SafeString(() => pn.ThingID) ?? "?")).ToArray());
                error = "\"" + wanted + "\" names " + exact.Count + " pawns on this map: " + ids
                      + ". This tool writes, so it will not pick one. Pass the exact ThingID "
                      + "(home/list_pawns settings{}.thingId, or animals{}.thingId).";
                return false;
            }

            var partial = everyone.Where(p =>
            {
                var name = BridgeCommon.SafeString(() => p.LabelShortCap.ToString());
                return name != null && name.IndexOf(wanted, StringComparison.OrdinalIgnoreCase) >= 0;
            }).ToList();

            if (partial.Count == 1)
            {
                pawn = partial[0];
                return true;
            }
            if (partial.Count > 1)
            {
                var names = string.Join(", ", partial
                    .Select(pn => (BridgeCommon.SafeString(() => pn.LabelShortCap.ToString()) ?? "?")
                                  + " " + (BridgeCommon.SafeString(() => pn.ThingID) ?? "?")).ToArray());
                error = "\"" + wanted + "\" matches " + partial.Count + " pawns: " + names
                      + ". Be exact, or use one of those ThingIDs.";
                return false;
            }

            error = "No spawned pawn on this map matches \"" + wanted + "\".";
            return false;
        }

        private static object Failure(string error)
        {
            return BridgeCommon.Failure(ToolName, error);
        }
    }
}
