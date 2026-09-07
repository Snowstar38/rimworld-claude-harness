using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using RimWorld;
using RimBridgeServer.Sdk;
using Verse;

namespace HomeBridge.BridgeTools
{
    /// <summary>
    /// home/list_pawns — every spawned pawn on the current map, with the one
    /// fact the whole play stack could not read without clicking: whether it is
    /// HOSTILE.
    ///
    /// ## Why this exists (M, 2026-08-31)
    ///
    /// The threat sweep and the Lookout's creature check were both answering
    /// "is there something dangerous near us?" by pulling every CELL in a
    /// 61x61 box around each colonist and looking at what was standing on it:
    /// 3,721 cells per colonist, 8 bridge calls, 1.7 MB of terrain, roofs,
    /// designations and zone ids, to find at most a handful of pawns. M,
    /// on being shown the number:
    ///
    ///     "oh okay well goddamn it's that high on 30? what the heck are we
    ///      doing to check??? it should just be 'pawns within these
    ///      coordinates, are you hostile (red nametag)' is it that long?"
    ///     "i figured the list of pawns in the game wouldn't be that long to
    ///      run through"
    ///     "are we doing an area search tile by tile or smth?"
    ///
    /// Yes, tile by tile, and she is right that it is absurd. RimWorld already
    /// keeps the list: `map.mapPawns.AllPawnsSpawned`. Reading it is O(pawns),
    /// not O(cells), and the answer is a few KB instead of megabytes.
    ///
    /// The bridge could not do this before: of its 127 tools the only pawn
    /// lister is `rimworld/list_colonists`, which is documented
    /// player-controlled-colonists-only and has no parameter that widens it. So
    /// allegiance had to come from `click_cell`, and a click is a mutation —
    /// which is why the read-only Lookout check could only ever say "the game
    /// raised no alert", never "that animal is not hostile".
    ///
    /// ## What "hostile" means here, exactly
    ///
    /// Two independent things put a red nametag over a pawn, and reporting only
    /// the first would be the same class of silent gap this companion exists to
    /// close:
    ///
    ///   * `faction.HostileTo(Faction.OfPlayer)` — raiders, hostile tribes,
    ///     mechanoids.
    ///   * a manhunter MENTAL STATE — a manhunter pack is ordinary wildlife
    ///     with no faction at all. Faction alone would report a pack of
    ///     enraged boars as neutral.
    ///
    /// `hostile` is true for either, and `hostileReason` says which, so a
    /// caller is never guessing why. Both are always present, never omitted.
    ///
    /// A predator that has not yet begun hunting is genuinely NOT hostile by
    /// either test, and that is the honest answer rather than a limitation:
    /// `job` carries what it is actually doing, so a caller can see
    /// `PredatorHunt` and decide for itself.
    ///
    /// ## health / needs -- added 2026-09-01 (M, on camera, twice)
    ///
    ///     "sometimes I seen hands rapidly flip through all the colonist's
    ///      health tabs. does the thing that gives the list of pawns not have an
    ///      option for that? if not make a note of it please"
    ///
    /// It did not. `health.py` answered "what is wrong with everyone" by, per
    /// colonist, clearing the selection, selecting the pawn, listing the inspect
    /// tabs, opening the Health tab and regexing a full-screen `get_ui_layout`
    /// dump -- five bridge calls and two visible UI mutations EACH, which is
    /// exactly the tab-flipping she watched happen on stream. The stock bridge
    /// had no other door: a repo-wide grep of the upstream server for
    /// `hediffSet`, `needs.`, `mood` returns nothing, and `open_inspect_tab`
    /// never calls the tab's `FillTab` or reads anything off its subject.
    ///
    /// So these are OPT-IN parameters on the pawn list rather than a separate
    /// tool, deliberately: the reason a fork went to the UI is that it reached
    /// for the pawn list, found no health column, and left. A separate tool
    /// reproduces the gap.
    ///
    /// `health: true` adds a `health` block per pawn; `needs: true` adds a
    /// `needs` block. Both are off by default so the ordinary threat sweep --
    /// the call `watch.py` makes every step -- does not grow.
    ///
    /// **No watch list, ever.** Every hediff is returned; the caller filters.
    /// `health.py` used to carry a curated tuple of 13 condition names and it
    /// silently dropped "Food poisoning" while a colonist had it. A reader that
    /// drops the category the answer is in, and says nothing, is worse than no
    /// reader.
    ///
    /// ### Mutation hazards, IL-scanned against Assembly-CSharp 1.6.9676.17735
    ///
    ///   * `HediffSet.PainTotal` and `.BleedRateTotal` write `cachedPain` /
    ///     `cachedBleedRate`. They are memos behind an explicit dirty flag that
    ///     RimWorld's own health tab refreshes every frame it draws; they change
    ///     no game state. Same class as `SummaryHealthPercent`. Reported here
    ///     rather than avoided, because the alternative is not reporting pain.
    ///   * `PawnCapacitiesHandler.GetLevel` fills a per-capacity cache AND
    ///     contains a `Verse.Log.Error` -- which pauses the game. That branch is
    ///     the infinite-recursion guard (`status == Caching`), which cannot fire
    ///     from a plain read on the main thread with nothing else re-entering
    ///     it. It also short-circuits on a dead pawn, so capacities are not read
    ///     for the dead at all.
    ///   * Everything else on the list -- `Hediff.LabelCap`, `.SeverityLabel`,
    ///     `.Severity`, `.Part`, `.Bleeding`, `.Visible`, `.TendableNow()`,
    ///     `HediffUtility.IsTended()`,
    ///     `HealthUtility.TicksUntilDeathDueToBloodLoss()`,
    ///     `Need.CurLevelPercentage`, `MentalBreaker.BreakThreshold*`,
    ///     `RestUtility.InBed()` -- was IL-scanned and stores nothing.
    ///
    /// ## equipment -- added 2026-09-02, after the gap killed somebody
    ///
    /// Five sessions of Lampblack's chronicle called Lucas "the colony's only
    /// fighter, charge rifle". On day 40 a warg took her at the map edge and
    /// M watched her fight it WITH HER HANDS. She had never carried a
    /// weapon. The save file agrees: only Longhoff has a gun.
    ///
    /// Hands had been asking. `home/list_pawns {equipment: true}` was called for
    /// five sessions and came back `equipment: None` every single time, and the
    /// note that went into `state\hands-last.md` read it as a limitation of the
    /// tool rather than an answer:
    ///
    ///     "`home/list_pawns {equipment:true}` returns `equipment: None` for
    ///      every colonist -- there is no way to read a weapon from it, which is
    ///      exactly the blind spot that killed Lucas."
    ///
    /// ### The defect
    ///
    /// The flattest one available: **there was no `equipment` parameter and no
    /// `equipment` key.** Not a parse bug, not a guard that never fired, not a
    /// serialiser dropping a field -- it was never written. The SDK binds only
    /// the parameters a tool DECLARES with `[ToolParameter]`, so `equipment:
    /// true` was discarded at the door without complaint; the per-pawn row
    /// dictionary then never grew the key; and `pawns.py` reads pawn fields with
    /// `.get()`, which turns an absent key into Python `None`.
    ///
    /// So "this build cannot answer that" and "the game says no weapon" arrived
    /// as the same four characters, and the reader picked the one that matched
    /// what the chronicle already said. That is the silent-absence failure this
    /// whole companion exists to end, and it cost a colonist.
    ///
    /// ### The rule this block is written under
    ///
    /// **Never null.** `health` and `needs` may return null for a pawn with no
    /// such tracker, because "a mechanoid has no needs" is a true and harmless
    /// answer. `equipment` may not: a null there is the exact shape that got
    /// read as "armed". So when `equipment: true` is passed the block is ALWAYS
    /// a dictionary --
    ///
    ///   * `armed` is a plain bool, always present;
    ///   * `primary` is null ONLY next to `armed: false`, and `primaryLabel`
    ///     spells that out as the literal string "unarmed";
    ///   * a pawn with no equipment tracker at all (every animal) says so in
    ///     `hasEquipmentTracker: false` rather than by being absent, and still
    ///     reports `armed: false` -- a warg is genuinely not carrying a rifle.
    ///
    /// `unarmedColonists` is lifted to the top level for the same reason: the
    /// question that was never asked was "who has nothing?", and a caller should
    /// get that answer without walking the list itself.
    ///
    /// ### Mutation hazards, IL-scanned against the same assembly
    ///
    /// `Pawn_EquipmentTracker.Primary` (indexes its ThingOwner),
    /// `Pawn_ApparelTracker.WornApparel` (returns the inner list),
    /// `ThingDef.IsWeapon` / `.IsRangedWeapon` / `.IsMeleeWeapon` (read
    /// `verbs` and `tools`) and `QualityUtility.TryGetQuality` (reads
    /// `CompQuality.Quality`, unwrapping a MinifiedThing first) call nothing
    /// that writes and nothing on the `Verse.Log.Error` -> `TickManager.Pause`
    /// path. This block is a pure read.
    ///
    /// ### bodyPartGroups / apparelLayers -- added 2026-09-02
    ///
    /// The Scout's gear section had to infer "nothing covering the legs" from
    /// GARMENT NAMES and printed a warning admitting it was guessing.
    /// `ThingDef.apparel` is a public `ApparelProperties` whose `bodyPartGroups`
    /// (`List&lt;BodyPartGroupDef&gt;`) and `layers` are both public fields --
    /// verified by reflection, not assumed -- so every gear row now carries what
    /// the garment actually covers. Both lists are ALWAYS present and empty for
    /// a non-apparel item, so "covers nothing" and "this is a rifle" are told
    /// apart by `isApparel`, never by a missing key.
    /// `BodyPartGroupDef.LabelShortCap` memoises `cachedLabelShortCap` on the
    /// shared def; same class of memo as `PainTotal`, no game state changes.
    ///
    /// ## bio -- added 2026-09-02 (M, on what a Hands session opens with)
    ///
    ///     "when a new hands session starts, i think they should automatically
    ///      be handed a list of colony pawns (human ones not animals) with: bio
    ///      (skills/traits), gear, needs, thoughts, and mood."
    ///
    /// Before this, `home/list_pawns` could answer health, needs and (as of this
    /// morning) equipment, and contained the strings "skill", "trait", "thought"
    /// and "backstory" exactly zero times. The whole left-hand column of the
    /// character card -- who this person IS -- was unreadable from the bridge,
    /// so every question about it went to the UI or went unanswered.
    ///
    /// ### `incapableOf` is the field this block exists for
    ///
    /// It is the one that stops an alarm nobody can act on. M, watching
    /// the Scout's new gear section run live:
    ///
    ///     "the scout should have pawns incapable of violence marked otherwise
    ///      theyll always have a line in their report about finn being unarmed."
    ///
    /// Finn is incapable of violence. Arming him is not a thing anyone can do.
    /// Without this field the Scout reports him UNARMED on every brief forever
    /// -- an alarm that never clears, which is worse than no alarm because it
    /// teaches the reader to skim the names that DO matter. It is also the
    /// answer to "why is this colonist idle?", which `rota.py`'s Scout brief has
    /// had to file as an unanswerable question, because the Work tab cannot be
    /// read from the bridge at all.
    ///
    /// **Both spellings are emitted.** `incapableOf[]` is the labels the game
    /// draws ("Violent", "Intellectual") because that is what a reader and a
    /// stream viewer recognise; `incapableOfTags[]` is the raw `Verse.WorkTags`
    /// names for a caller that wants to match on something stable. Neither is
    /// ever the only one present.
    ///
    /// **Empty and unreadable are different values.** `incapableOfRead` is a
    /// bool: false means the work tags could not be obtained at all (an animal
    /// has no story tracker), and `incapableOfNote` says why in words. A
    /// consumer told to treat an absent `bio` as "filter not applied" rather
    /// than "everyone is capable" needs that distinction to be real rather than
    /// hoped for -- it is the same silent-absence trap that killed Lucas, one
    /// field over.
    ///
    /// ### Where "incapable" actually comes from, IL-scanned
    ///
    /// `Verse.Pawn.CombinedDisabledWorkTags` is the complete union, and is what
    /// the character card itself reads. Its IL ORs together, in order:
    /// `Pawn_StoryTracker.DisabledWorkTagsBackstoryTraitsAndGenes` (itself
    /// `DisabledWorkTagsBackstoryAndTraits | Pawn_GeneTracker.DisabledWorkTags`
    /// -- that is the real 1.6 member name, read off the assembly, not guessed),
    /// every royal title in `royalty.AllTitlesForReading`, the pawn's ideoligion
    /// role, every hediff stage's disabled tags, any `QuestPart_WorkDisabled`
    /// naming the pawn, and the mutant tracker. A backstory-disabled tag and a
    /// trait-disabled tag both land here, and so does a temporary one from a
    /// quest -- which is why the narrower story-only property is reported
    /// alongside it as `incapableOfFromBackstoryTraitsGenes[]`, so a caller can
    /// tell a permanent incapability from a quest-imposed one.
    ///
    /// A Brawler is deliberately NOT in this list: a brawler can fight, he just
    /// cannot be handed a ranged weapon. That is a trait, and it is in `traits[]`.
    ///
    /// ### Mutation hazards in bio, IL-scanned against Assembly-CSharp 1.6.9676.17735
    ///
    ///   * **`TraitSet.TraitsSorted` is not used, deliberately.** Its body is
    ///     `tmpTraits.Clear()`, refill, `SortBy` -- it rewrites a per-pawn
    ///     scratch list and hands back a reference to it, so two readers overlap
    ///     and the second wins. `TraitSet.allTraits` is a public field holding
    ///     the real list, and is copied instead.
    ///   * `Pawn.CombinedDisabledWorkTags` calls
    ///     `QuestUtility.GetWorkDisabledQuestPart`, which does `Clear()` +
    ///     `Add()` on a static scratch list. Transient, main-thread only, no
    ///     game state; the character card does the same every frame it draws.
    ///   * `SkillRecord.Level` -> `GetLevel(true)` fills `cachedTotallyDisabled`,
    ///     `cachedPermanentlyDisabled` and `aptitudeCached`, and reaches
    ///     `TraitDef.DataAtDegree`, whose out-of-range arm is a `Verse.Log.Error`.
    ///     That arm needs a trait carrying a degree its own def does not define,
    ///     which no valid save produces -- the same argument the health block
    ///     already makes for `PawnCapacitiesHandler.GetLevel`. `levelStored`
    ///     carries the raw `levelInt` beside it, so a caller has the un-aptituded
    ///     number regardless.
    ///   * **`Pawn_SkillTracker.GetSkill(def)` is not used, deliberately.** Its
    ///     miss branch is a `Verse.Log.Error`, and Log.Error pauses the colony.
    ///     The skill list is built by walking
    ///     `DefDatabase&lt;SkillDef&gt;.AllDefsListForReading` -- the exact order
    ///     `SkillUI.DrawSkillsOf` draws, confirmed in its IL -- and matching
    ///     against `pawn.skills.skills` by reference here, so a def with no
    ///     record reports `present: false` instead of pausing the game.
    ///   * `WorkTypeDefsUtility.LabelTranslated` ends in a `Verse.Log.Error` for
    ///     an unknown or mixed tag. Its switch covers all 21 values of
    ///     `Verse.WorkTags` (counted in the IL), so single-bit splitting plus an
    ///     `Enum.IsDefined` guard makes that arm unreachable.
    ///   * `Trait.LabelCap` and `BodyPartGroupDef.LabelShortCap` memoise a
    ///     capitalised string on the shared def. Memos, not state.
    ///
    /// ## thoughts -- added 2026-09-02, and the nastiest hazard in this file
    ///
    /// What the Mood tab shows: every memory and situational thought with the
    /// mood it contributes, worst first.
    ///
    /// ### The API the UI uses can DELETE a colonist's memories
    ///
    /// `NeedsCardUtility` draws the Mood tab with
    /// `ThoughtHandler.GetDistinctMoodThoughtGroups` / `GetMoodThoughts` /
    /// `MoodOffsetOfGroup`. Reaching for those is the obvious move and it is
    /// wrong. IL, top to bottom:
    ///
    ///     GetDistinctMoodThoughtGroups -> GetAllMoodThoughts
    ///       -> SituationalThoughtHandler.AppendMoodThoughts
    ///       -> UpdateAllMoodThoughts             (stores thoughtsDirty = false)
    ///       -> Thought_Situational.RecalculateState
    ///       -> Thought_Situational.Notify_BecameActive
    ///       -> MemoryThoughtHandler.RemoveMemoriesOfDef
    ///
    /// `Notify_BecameActive` is one branch long and its whole body is: if
    /// `def.producesMemoryThought` is set, **remove every memory of that def
    /// from the pawn**. That is not a cache refresh and not a memo -- it is a
    /// permanent edit to a colonist's mind, fired as a side effect of asking
    /// what their mood is. RimWorld does it to itself on its own tick
    /// (`Need_Mood` -> `TotalMoodOffset` walks the same path), which is exactly
    /// why it reads as harmless in a decompiler; doing it from a bridge call on
    /// a PAUSED game is an out-of-band edit to the save.
    ///
    /// ### What is read instead
    ///
    ///   * memories: `thoughts.memories.Memories` -- a property whose entire IL
    ///     is `ldarg.0; ldfld; ret`. There is no purer read in the game.
    ///   * situational: the private `SituationalThoughtHandler.cachedThoughts`
    ///     list, by reflection, WITHOUT calling the recalculating getter. That
    ///     is precisely what the Mood tab drew on the last frame the game
    ///     computed mood -- and since `Need_Mood` recomputes on its own need
    ///     interval, on a paused game it is the number the player is looking at
    ///     on screen. `situationalCacheStale` carries the handler's own
    ///     `thoughtsDirty` flag so a caller is never guessing how fresh it is,
    ///     and `situationalCacheReadable` says outright whether the private
    ///     field was found at all, rather than reporting an empty list.
    ///   * grouping: done here with `Thought.GroupsWith`, the same predicate
    ///     `GetDistinctMoodThoughtGroups` uses, which reads `CurStageIndex`,
    ///     compares labels, and stores nothing.
    ///
    /// `Thought.MoodOffset()` opens with `if (CurStage == null) Log.Error(..)`,
    /// so `CurStage` is tested HERE before every call and that arm is dead.
    ///
    /// ### moodOffset is the STACKED TOTAL
    ///
    /// Said once, plainly, because half the value of this block is that a caller
    /// can add it up: in every `memories[]` and `situational[]` row,
    /// **`moodOffset` is the total the whole stack contributes**, not the
    /// per-instance value. `count` is how many identical thoughts were stacked
    /// and `moodOffsetEach` is the per-instance number. `moodOffsetTotal` is the
    /// sum of every row in both lists, so `sum(rows) == moodOffsetTotal` is a
    /// check a caller can actually run.
    ///
    /// ## work / schedule / settings / relations -- added 2026-09-02
    ///
    /// The configuration half of a colonist: the Work tab, the Assign tab and
    /// the Social tab. `WANTED.md` item 1 called the work tab *"top priority
    /// among the tools themselves"*; item 11 listed schedule, allowed area,
    /// medical care, self-tend and hostility response as having **no read or
    /// write path at all**. Both were true until these four blocks: the only
    /// way to see a work priority was `ui.py`'s pixel-pairing over an open Work
    /// tab, and `PLAYBOOK.md` said in so many words that the numbers were an
    /// OCR read rather than an assertion.
    ///
    /// `relations` is M's, 2026-09-02: *"family/relationships, since that
    /// can be relevant."*
    ///
    /// They are blocks on THIS tool rather than a second read tool because they
    /// hang off the same `Pawn` object as the five above -- the house pattern
    /// this file is the example of. The WRITE half is `home/pawn_config`, which
    /// is a separate tool because a write needs a plan, a refusal channel and a
    /// dry run that a filter argument cannot carry. **Both use the same
    /// builders** (`PawnSettingsRead`), so a write tool cannot report an "after"
    /// this tool would not show.
    ///
    /// All four take the never-null contract of `equipment{}` / `bio{}`: an
    /// animal gets a `work{}` saying `applies: false`, never a missing key.
    ///
    /// ## animals -- added 2026-09-03 (WANTED 7, the Animals tab)
    ///
    /// The last whole TAB with no read path. `list_pawns` has returned every
    /// animal on the map since the day it was written -- with `animal: true`, a
    /// position and a job -- and could not say whether one was tame, what it had
    /// been trained to do, who it was bonded to, or that somebody had marked it
    /// for slaughter. So "which of our animals can haul?" and "is the muffalo
    /// about to be butchered?" were both questions that had to be answered by
    /// opening the tab on stream.
    ///
    /// `animals: true` adds an `animals{}` block. Same never-null contract as
    /// the seven above: a humanlike gets `applies: false` and `isAnimal: false`,
    /// not a missing key. An absent key and a build that predates this are the
    /// same four characters to a caller, and that confusion has already cost
    /// this colony a colonist once (see `equipment` above).
    ///
    /// ### It does NOT narrow the list, and that is deliberate
    ///
    /// Every other block is a colonist question, so `pawns.py` narrows to
    /// colonists whenever one is asked for. This one is an ANIMAL question and
    /// narrowing it the same way would return nothing. The narrowing is a
    /// `pawns.py` behaviour and not a DLL one -- the tool's row set is
    /// unchanged, `animals:true` adds a block and filters nothing -- so
    /// `pawns.py --animals` narrows to living animals instead, and says so in
    /// its footer. The two top-level counts `animalCount` and `tameAnimalCount`
    /// are there so a caller doing its own narrowing can check its arithmetic
    /// against the tool's.
    ///
    /// ### Nothing in it is a second copy of something else
    ///
    /// `master`, `followDrafted`, `followFieldwork` and `allowedArea` are on the
    /// Animals tab and are NOT repeated in this block -- they are in
    /// `settings{}`, from the reader `home/pawn_config` writes through. Hunger
    /// is in `needs{}`. `animals.settingsBlockCarries` says so in the payload,
    /// because a field that exists in two places is a field whose two copies
    /// will eventually disagree.
    ///
    /// **The hazards in these five are documented on `PawnSettingsRead`**, and
    /// two of them are the same class as the memory-deletion trap above:
    /// `Pawn_WorkSettings.GetPriority` writes a whole priority table onto a pawn
    /// that never had one (and Log.Errors on the way, which pauses the colony),
    /// and `Pawn_RelationsTracker.OpinionOf` recalculates situational social
    /// thoughts, which DELETES memories. Neither is called on those paths. Read
    /// that class before touching any of them.
    /// </summary>
    public sealed class HomePawnTools
    {
        [Tool(
            "home/list_pawns",
            Title = "List every pawn on the map with allegiance",
            Description =
                "List every spawned pawn on the current map with position, faction, and whether it is hostile to the player "
                + "(hostile faction OR manhunter mental state). Reads RimWorld's own pawn list, so the cost is the number of "
                + "pawns rather than the number of cells. Optional filters narrow it to hostiles, to non-colonists, or to a "
                + "radius around the colonists. Set health:true for every hediff with the severity word the game itself "
                + "shows, plus pain, bleeding, impaired capacities and the self-tend setting; needs:true for food, rest, "
                + "joy and mood against the pawn's own mental-break thresholds; equipment:true for the weapon actually in "
                + "each pawn's hands, plus worn apparel and any weapon stashed in the inventory. All three answer for EVERY "
                + "pawn in one call, so nothing has to open a Health tab or click a colonist. bio:true adds age, backstory, every trait, every skill with its passion, any title, and -- the field that answers \"why is this colonist idle\" and stops a pawn who CANNOT hold a weapon being reported unarmed forever -- incapableOf[], the work tags the pawn cannot do. thoughts:true adds the Mood tab: every memory and situational thought with the mood it contributes, worst first. work:true adds every work type in the Work tab's own order with its priority (0 = never) and whether it is disabled for this pawn; schedule:true the 24 hour assignments as one letter each plus a key; settings:true medical care, hostility response, self-tend, the follow toggles, the allowed area and the master; relations:true every family relation with the label the game draws, bonded animals, partners, and what this pawn thinks of every other colonist. animals:true adds the Animals tab for every animal on the map in one call -- tame or wild, wildness, trainability, age, gender, body size, the colonists it is bonded to, EVERY trainable with wanted/learned/can-train and its step count, what it is producing, and the slaughter / release-to-wild / tame / hunt designations standing on it, so \"marked for slaughter\" is a read. NARROWING is server-side: wildOnly, tameOnly, animalsOnly, humanlikeOnly, mechanoidsOnly, colonistsOnly, prisonersOnly, downedOnly, draftedOnly and nameFilter (substring on name / defName / kindDef) combine by AND, and a pair that cannot both hold is refused rather than answered with an empty list. The WRITE side of work/schedule/settings/animals is home/pawn_config.",
            ResultDescription =
                "success, counts, and pawns[]: name, defName, kindDef, position, faction, hostile, hostileReason, animal, "
                + "mechanoid, downed, job, mentalState, distance to the nearest colonist, and -- when asked for -- health{}, "
                + "needs{} and equipment{}. Every row also carries humanlike, tame, wild, drafted, isFreeColonist and "
                + "isPrisoner, so a narrowed list can be checked field for field against an unnarrowed one. pawnsListed "
                + "and pawnsFiltered say how many rows survived the filters; spawnedPawnTotal, colonistCount and "
                + "hostileCount are NOT narrowed by them. equipment{} is never null: armed is always a bool and primaryLabel says "
                + "\"unarmed\" in words, so an unarmed colonist can never be mistaken for an unread one. With equipment:true "
                + "the top level also carries unarmedColonists[]. bio{} and thoughts{} are never null either: an animal gets a bio{} saying hasStory:false and incapableOfRead:false, so an empty incapableOf[] can never be read as \"capable of everything\". incapableOf[] holds the labels the game draws (\"Violent\") and incapableOfTags[] the raw WorkTags names. work{}, schedule{}, settings{}, relations{} and animals{} take the same never-null contract and each carry applies:false rather than being absent -- a humanlike gets an animals{} saying isAnimal:false, so an empty training list is never read as \"untrained\". With settings:true the top level also carries pawnConfigOptions{} -- the areas and enum values home/pawn_config will accept on this map; with animals:true it carries animalCount and tameAnimalCount. animals:true does NOT narrow the list: it adds a block and filters nothing.")]
        [ToolResponse("pawns", "array", "One entry per spawned pawn that passed the filters. Every row carries predator (Verse.RaceProperties.predator, the GAME'S OWN flag -- no tool in this stack keeps a hardcoded list of predator defNames any more) and manhunterOnDamageChance (Verse.RaceProperties.manhunterOnDamageChance, 0..1, the raw race field), alongside name, defName, kindDef, position, faction, hostile, hostileReason, isColonist, isFreeColonist, isPrisoner, animal, humanlike, tame, wild, mechanoid, downed, drafted, dead, job, mentalState, nearestColonist and nearestColonistDistance.", Always = true)]
        [ToolResponse("pawnsListed", "integer", "Rows in pawns[] AFTER every filter -- the same number as pawnCount, under the name that says what it counts. Compare it with spawnedPawnTotal, which is never filtered.", Always = true)]
        [ToolResponse("pawnsFiltered", "integer", "Spawned pawns dropped by the filters: spawnedPawnTotal - pawnsListed. It counts every narrowing together -- includeDead, includeColonists, withinOfColonists, hostileOnly and the category filters -- so it is non-zero on a default call whenever a corpse is lying on the map.", Always = true)]
        [ToolResponse("unknownArguments", "array", "Every argument key the caller sent that this tool does not declare, sorted, case-sensitively. Empty array = every key was recognised. This is the tool where a silently ignored filter cost five sessions of believing a colonist was armed: {skills:true} is NOT a block name and now says so. The host's own _rimBridgeTimeoutMs is never listed.", Always = true)]
        [ToolResponse("unknownArgumentsWarning", "string", "Present only when unknownArguments is non-empty, or when the caller's raw keys could not be read at all - in which case the empty unknownArguments means 'not known', not 'nothing unknown'.", Nullable = true)]
        public async Task<object> ListPawns(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            [ToolParameter(Description = "Only return pawns hostile to the player (hostile faction or manhunter).", DefaultValue = false)] bool hostileOnly = false,
            [ToolParameter(Description = "Include the player's own colonists in the list.", DefaultValue = true)] bool includeColonists = true,
            [ToolParameter(Description = "Only return pawns within this many cells (Chebyshev) of any live colonist. 0 = no distance filter.", DefaultValue = 0)] int withinOfColonists = 0,
            [ToolParameter(Description = "Include dead-but-still-spawned pawns.", DefaultValue = false)] bool includeDead = false,
            [ToolParameter(Description = "Add a health{} block per pawn: every hediff with the label and severity word the game shows, body part, bleeding, tend state, plus pain, blood loss, hours until death from blood loss, impaired capacities, inBed and the selfTend setting. No condition is filtered out. Off by default so the ordinary threat sweep does not grow.", DefaultValue = false)] bool health = false,
            [ToolParameter(Description = "Add a needs{} block per pawn: food, rest, joy and mood as 0..1, the hunger category the UI shows, every other need the pawn has, and the pawn's own mental-break thresholds with the risk band the current mood falls in. Off by default.", DefaultValue = false)] bool needs = false,
            [ToolParameter(Description = "Add an equipment{} block per pawn: the weapon actually in the pawn's hands (defName, the label the game draws including quality, stuff, condition, ranged/melee), every other equipped item, weapons carried in the inventory, and worn apparel. NEVER null: 'armed' is always a bool and 'primaryLabel' is the literal word \"unarmed\" when there is no weapon, so nobody can read a missing answer as an armed colonist. Also adds unarmedColonists[] at the top level. Off by default.", DefaultValue = false)] bool equipment = false,
            [ToolParameter(Description = "Add a bio{} block per pawn: biological and chronological age, childhood and adulthood backstory titles, every trait with its degree-specific label and description, EVERY skill in RimWorld's own order with level, passion word and whether it is disabled, any royal or ideoligion title, and incapableOf[] -- the work tags the pawn cannot do, as the labels the game draws (\"Violent\", \"Intellectual\"). NEVER null: an animal gets a bio{} that says hasStory:false and incapableOfRead:false, so an empty incapableOf[] is never mistaken for 'capable of everything'. Off by default.", DefaultValue = false)] bool bio = false,
            [ToolParameter(Description = "Add a thoughts{} block per pawn: what the Mood tab shows. mood 0..1 and moodPercent 0..100, every memory and every active situational thought with the mood it contributes, stacked the way the game stacks them, sorted worst first, plus moodOffsetTotal so the parts can be checked against the whole. Read from RimWorld's own cache rather than through the recalculating UI getter, which can delete a colonist's memories. NEVER null. Off by default.", DefaultValue = false)] bool thoughts = false,
            [ToolParameter(Description = "Add a work{} block per pawn: every work type in the Work tab's own column order (naturalPriority descending) with priority (0 = never, 1 most urgent, 4 least), the raw stored priority beside it, whether the type is disabled for this pawn, and manualPriorities -- whether the tab is in numbered or checkbox mode, which changes what priority MEANS. NEVER null: a pawn with no work settings says applies:false, so an empty work list is never read as 'no jobs assigned'. Off by default.", DefaultValue = false)] bool work = false,
            [ToolParameter(Description = "Add a schedule{} block per pawn: the 24 hour assignments as one compact string, hour 0 first, one letter per TimeAssignmentDef, with key{} mapping every letter to its def name, plus what the pawn is assigned right now. NEVER null. Off by default.", DefaultValue = false)] bool schedule = false,
            [ToolParameter(Description = "Add a settings{} block per pawn: medical care, hostility response, self-tend, followDrafted, followFieldwork, the allowed area (null = unrestricted, said out loud as a bool), the master, and the pawn's ThingID for home/pawn_config to address it by. Also adds pawnConfigOptions{} at the top level. NEVER null. Off by default.", DefaultValue = false)] bool settings = false,
            [ToolParameter(Description = "Add a relations{} block per pawn: every direct relation with the label the game draws (\"wife\", \"daughter\", \"bond\") and whether the other pawn is a colonist, on this map or dead; partners and bonded animals lifted out; and what this pawn thinks of every other living colonist on the -100..100 scale the Social tab shows, worst first. The opinion is RECONSTRUCTED rather than read from OpinionOf, which deletes memories -- see opinionMethod in the reply. NEVER null. Off by default.", DefaultValue = false)] bool relations = false,
            [ToolParameter(Description = "Add an animals{} block per pawn: the Animals tab. tame (ours) and wild (no faction) as separate bools because they are not complements -- a trader's pack muffalo is neither; wildness (a STAT in 1.6, not a race field), trainability, petness, gender, age, body size, race, the given name if it has one, and the colonists it is bonded to. training{} carries EVERY trainable in the tab's own order with wanted / learned / canTrain (with RimWorld's own refusal sentence) / canBeTrainedNow / steps \"x/y\", plus nextToTrain. designations{} hoists slaughter, releaseToWild, tame and hunt as bools and lists every other designation on the pawn. produce{} carries egg, milk and wool fullness and pregnancy. NEVER null: a humanlike gets applies:false and isAnimal:false. This block does NOT narrow the list. Off by default.", DefaultValue = false)] bool animals = false,
            [ToolParameter(Description = "With health:true, only report hediffs the game itself shows on the Health tab (Hediff.Visible). False returns hidden ones too -- mostly implants' internal bookkeeping and unrevealed diseases.", DefaultValue = true)] bool visibleHediffsOnly = true,
            [ToolParameter(Description = "Only animals with no faction -- the wild ones. Combines with every other filter by AND.", DefaultValue = false)] bool wildOnly = false,
            [ToolParameter(Description = "Only animals of the player faction -- the tame ones.", DefaultValue = false)] bool tameOnly = false,
            [ToolParameter(Description = "Only animals, tame or wild or somebody else's.", DefaultValue = false)] bool animalsOnly = false,
            [ToolParameter(Description = "Only humanlikes (RaceProps.Humanlike): colonists, prisoners, visitors, raiders.", DefaultValue = false)] bool humanlikeOnly = false,
            [ToolParameter(Description = "Only mechanoids.", DefaultValue = false)] bool mechanoidsOnly = false,
            [ToolParameter(Description = "Only free colonists (Pawn.IsFreeColonist): ours, humanlike, not a prisoner or slave of anyone.", DefaultValue = false)] bool colonistsOnly = false,
            [ToolParameter(Description = "Only prisoners.", DefaultValue = false)] bool prisonersOnly = false,
            [ToolParameter(Description = "Only downed pawns.", DefaultValue = false)] bool downedOnly = false,
            [ToolParameter(Description = "Only drafted pawns.", DefaultValue = false)] bool draftedOnly = false,
            [ToolParameter(Description = "Case-insensitive substring on the pawn's name, defName or kindDef. Applied on the server, so the reply is already narrowed. Whitespace-only is refused.")] string nameFilter = null)
        {
            return BridgeCommon.WithUnknownArguments(
                await ListPawnsCore(
                    ctx, cancellationToken, hostileOnly, includeColonists, withinOfColonists, includeDead,
                    health, needs, equipment, bio, thoughts, work, schedule, settings, relations, animals,
                    visibleHediffsOnly,
                    new PawnFilters
                    {
                        WildOnly = wildOnly,
                        TameOnly = tameOnly,
                        AnimalsOnly = animalsOnly,
                        HumanlikeOnly = humanlikeOnly,
                        MechanoidsOnly = mechanoidsOnly,
                        ColonistsOnly = colonistsOnly,
                        PrisonersOnly = prisonersOnly,
                        DownedOnly = downedOnly,
                        DraftedOnly = draftedOnly,
                        NameFilter = nameFilter
                    }).ConfigureAwait(false),
                ctx, typeof(HomePawnTools), "home/list_pawns");
        }

        private async Task<object> ListPawnsCore(
            IRimBridgeContext ctx,
            CancellationToken cancellationToken,
            bool hostileOnly,
            bool includeColonists,
            int withinOfColonists,
            bool includeDead,
            bool health,
            bool needs,
            bool equipment,
            bool bio,
            bool thoughts,
            bool work,
            bool schedule,
            bool settings,
            bool relations,
            bool animals,
            bool visibleHediffsOnly,
            PawnFilters narrow)
        {
            if (ctx?.MainThread == null)
                return Failure("No RimBridge main-thread dispatcher is available for this invocation.");

            // Argument validation before the hop: it is string and bool work that
            // touches no game state, and a contradictory request should never
            // reach the map sweep at all.
            var narrowError = narrow.Validate(includeColonists);
            if (narrowError != null)
                return narrowError;

            // Companion tools are dispatched with MarshalToMainThread = false
            // (AnnotatedExtensionCapabilityProvider.InvokeAsync), so every read of
            // mapPawns / Faction / the job tracker has to be hopped onto RimWorld's
            // main thread by hand. Removing this hop still compiles and fails
            // intermittently, which is the worst failure mode available.
            return await ctx.MainThread
                .InvokeAsync(() => Build(hostileOnly, includeColonists, withinOfColonists, includeDead,
                                         health, needs, equipment, bio, thoughts,
                                         work, schedule, settings, relations, animals,
                                         visibleHediffsOnly, narrow), cancellationToken)
                .ConfigureAwait(false);
        }

        private static object Build(bool hostileOnly, bool includeColonists, int withinOfColonists, bool includeDead,
                                    bool wantHealth, bool wantNeeds, bool wantEquipment, bool wantBio, bool wantThoughts,
                                    bool wantWork, bool wantSchedule, bool wantSettings, bool wantRelations,
                                    bool wantAnimals, bool visibleHediffsOnly, PawnFilters narrow)
        {
            if (!TryGetMap(out var map, out var mapError))
                return Failure(mapError);

            var all = SafeAllPawns(map);
            if (all == null)
                return Failure("map.mapPawns.AllPawnsSpawned was not readable.");

            // Colonist positions first: the distance filter and the reported
            // distance both hang off them, and they are the reason anybody is
            // asking. A map with no live colonist has no distances, and says so
            // by emitting null rather than a made-up number.
            var colonists = new List<KeyValuePair<string, IntVec3>>();
            // The same living colonists as Pawn objects. relations{} needs them
            // to answer "what does this pawn think of everyone else" without a
            // second traversal of mapPawns, and it is the SAME list, so the two
            // answers cannot be about different sets of people.
            var colonistPawns = new List<Pawn>();
            foreach (var p in all)
            {
                if (p == null || !SafeIsColonist(p) || SafeDead(p))
                    continue;
                colonists.Add(new KeyValuePair<string, IntVec3>(SafeName(p), p.Position));
                colonistPawns.Add(p);
            }

            var pawns = new List<object>();
            var hostileCount = 0;
            var skippedByDistance = 0;
            // Collected while walking, so the answer to "who has nothing?" costs
            // nothing extra. Only meaningful when equipment was asked for; see
            // the class remarks for why it is hoisted to the top level at all.
            var unarmedColonists = new List<object>();
            // Counted while walking, for the same reason: pawns.py narrows to
            // animals on its own and a caller must be able to check its
            // arithmetic against the tool's rather than trusting its own filter.
            var animalCount = 0;
            var tameAnimalCount = 0;

            foreach (var pawn in all)
            {
                if (pawn == null)
                    continue;
                var dead = SafeDead(pawn);
                if (dead && !includeDead)
                    continue;

                var isColonist = SafeIsColonist(pawn);
                if (!includeColonists && isColonist)
                    continue;

                string hostileReason;
                var hostile = IsHostile(pawn, out hostileReason);
                if (hostile)
                    hostileCount++;
                if (hostileOnly && !hostile)
                    continue;

                // The category and name narrowings, all AND, all read through
                // the same Safe* accessors the row itself uses -- so a pawn in
                // the list always satisfies the filter its own row reports.
                var isAnimal = SafeAnimal(pawn);
                var isHumanlike = SafeHumanlike(pawn);
                var isMechanoid = SafeMechanoid(pawn);
                var isPrisoner = SafeIsPrisoner(pawn);
                var isDowned = SafeDowned(pawn);
                var isDrafted = SafeDrafted(pawn);
                var isFreeColonist = SafeIsFreeColonist(pawn);
                if (!narrow.Accepts(pawn, isAnimal, isHumanlike, isMechanoid, isPrisoner,
                                    isDowned, isDrafted, isFreeColonist, SafeName(pawn)))
                    continue;

                string nearestName = null;
                int? nearest = null;
                foreach (var c in colonists)
                {
                    if (isColonist && c.Value == pawn.Position && c.Key == SafeName(pawn))
                        continue;                       // don't measure a pawn against itself
                    var d = Math.Max(Math.Abs(c.Value.x - pawn.Position.x),
                                     Math.Abs(c.Value.z - pawn.Position.z));
                    if (nearest == null || d < nearest.Value)
                    {
                        nearest = d;
                        nearestName = c.Key;
                    }
                }

                if (withinOfColonists > 0 && (nearest == null || nearest.Value > withinOfColonists))
                {
                    skippedByDistance++;
                    continue;
                }

                var row = new Dictionary<string, object>
                {
                    { "name", SafeName(pawn) },
                    { "defName", pawn.def != null ? pawn.def.defName : null },
                    { "kindDef", pawn.kindDef != null ? pawn.kindDef.defName : null },
                    { "position", BridgeCommon.Pos(pawn.Position) },
                    { "faction", SafeFactionName(pawn) },
                    // Never omitted. A missing hostility flag is exactly the
                    // silent-absence bug this companion was built to end.
                    { "hostile", hostile },
                    { "hostileReason", hostileReason },
                    { "isColonist", isColonist },
                    // The free-colonist test the colonistsOnly filter uses.
                    // isColonist is true for a colonist held by another faction;
                    // this one is not, and the two are different questions.
                    { "isFreeColonist", isFreeColonist },
                    { "isPrisoner", isPrisoner },
                    { "animal", isAnimal },
                    { "humanlike", isHumanlike },
                    // The GAME'S OWN predator flag, Verse.RaceProperties.predator
                    // (a public bool field on the race def, XML-loaded). It
                    // replaces every hardcoded defName list: a hardcoded set is
                    // wrong the moment a mod adds a race, and it was wrong here
                    // for wild boars, which the game calls predators and a list
                    // written from memory did not. False for humanlikes and
                    // mechanoids; false, never null, when the race cannot be read.
                    { "predator", SafePredator(pawn) },
                    // Verse.RaceProperties.manhunterOnDamageChance, the 0..1
                    // chance this race goes manhunter when it is hurt. This is
                    // what makes shooting a predator a combat decision rather
                    // than a hunting one. The RAW race field: RimWorld's own
                    // stat display routes through
                    // PawnUtility.GetManhunterOnDamageChance, which applies
                    // modifiers this does not.
                    { "manhunterOnDamageChance", SafeManhunterOnDamageChance(pawn) },
                    // A tame animal is one of ours; a wild one has no faction at
                    // all. They are NOT complements -- a trader's pack muffalo is
                    // neither -- so both are emitted rather than one and a negation.
                    { "tame", isAnimal && SafeIsPlayerFaction(pawn) },
                    { "wild", isAnimal && SafeFactionless(pawn) },
                    { "mechanoid", isMechanoid },
                    { "downed", isDowned },
                    { "drafted", isDrafted },
                    { "dead", dead },
                    { "job", SafeJob(pawn) },
                    { "mentalState", SafeMentalState(pawn) },
                    { "nearestColonist", nearestName },
                    { "nearestColonistDistance", nearest }
                };

                // Opt-in blocks. When asked for, the key is ALWAYS present --
                // null only when the pawn genuinely has no such tracker (a
                // mechanoid has no needs), never absent, because an absent key
                // and an empty one read identically to a caller.
                if (wantHealth)
                    row["health"] = HealthBlock(pawn, visibleHediffsOnly);
                if (wantNeeds)
                    row["needs"] = NeedsBlock(pawn);
                // equipment{} is the one exception to the sentence above: it is
                // never null, for any pawn, ever. A null here is the shape that
                // got read as "armed" for five sessions. See the class remarks.
                if (wantEquipment)
                {
                    var gear = EquipmentBlock(pawn);
                    row["equipment"] = gear;
                    if (isColonist && !dead && !AsBool(gear, "armed"))
                        unarmedColonists.Add(SafeName(pawn));
                }
                // bio{} and thoughts{} take the same never-null contract as
                // equipment{}, for the same reason: a caller reading an absent
                // incapableOf[] as "this colonist is capable of everything"
                // is the Lucas bug in a new costume.
                if (wantBio)
                    row["bio"] = BioBlock(pawn);
                if (wantThoughts)
                    row["thoughts"] = ThoughtsBlock(pawn);
                // The configuration blocks, same never-null contract. Built by
                // PawnSettingsRead so home/pawn_config's before/after and this
                // read can never disagree about what a setting is.
                if (wantWork)
                    row["work"] = PawnSettingsRead.WorkBlock(pawn);
                if (wantSchedule)
                    row["schedule"] = PawnSettingsRead.ScheduleBlock(pawn);
                if (wantSettings)
                    row["settings"] = PawnSettingsRead.SettingsBlock(pawn);
                if (wantRelations)
                    row["relations"] = PawnSettingsRead.RelationsBlock(pawn, colonistPawns);
                // animals{} is emitted for EVERY pawn, humanlikes included, where
                // it says isAnimal:false and applies:false. Omitting it on a
                // humanlike would make "this build cannot answer" and "not an
                // animal" the same missing key, which is the equipment bug.
                if (wantAnimals)
                {
                    var beast = PawnSettingsRead.AnimalBlock(pawn);
                    row["animals"] = beast;
                    if (AsBool(beast, "applies"))
                    {
                        animalCount++;
                        if (AsBool(beast, "tame"))
                            tameAnimalCount++;
                    }
                }

                pawns.Add(row);
            }

            var payload = new Dictionary<string, object>
            {
                { "success", true },
                { "tool", "home/list_pawns" },
                { "pawnCount", pawns.Count },
                // The same number under the name that says what it counts, and
                // its complement. Every other count here is UNFILTERED; see
                // notes.whatEachCountCounts.
                { "pawnsListed", pawns.Count },
                { "pawnsFiltered", Math.Max(0, all.Count - pawns.Count) },
                { "spawnedPawnTotal", all.Count },
                { "colonistCount", colonists.Count },
                { "hostileCount", hostileCount },
                // A filter that hides things states what it hid; the caller must
                // never have to guess whether an empty list means "nothing there"
                // or "nothing survived the filter".
                { "skippedByDistance", skippedByDistance },
                { "filters", new Dictionary<string, object>
                    {
                        { "hostileOnly", hostileOnly },
                        { "includeColonists", includeColonists },
                        { "withinOfColonists", withinOfColonists },
                        { "includeDead", includeDead },
                        { "health", wantHealth },
                        { "needs", wantNeeds },
                        // Present since 2026-09-02. A caller that sees no
                        // `equipment` key here is talking to a build that
                        // predates the fix and CANNOT answer about weapons --
                        // which is the distinction the old None never made.
                        { "equipment", wantEquipment },
                        // Present since 2026-09-02. A caller that sees no `bio`
                        // key here is talking to a build that CANNOT answer
                        // about skills, traits or incapabilities -- which is a
                        // different thing from a colonist who has none.
                        { "bio", wantBio },
                        { "thoughts", wantThoughts },
                        // Present since 2026-09-02. A caller that sees no `work`
                        // key here is talking to a build that CANNOT answer about
                        // work priorities at all -- which is a different thing
                        // from a colonist with none set.
                        { "work", wantWork },
                        { "schedule", wantSchedule },
                        { "settings", wantSettings },
                        { "relations", wantRelations },
                        // Present since 2026-09-03. A caller that sees no
                        // `animals` key here is talking to a build that CANNOT
                        // answer about training, tameness or slaughter marks --
                        // which is a different thing from an animal with none.
                        { "animals", wantAnimals },
                        { "visibleHediffsOnly", visibleHediffsOnly },
                        // The category narrowings. Every one is echoed whether or
                        // not it was sent, so a caller that sees a name missing
                        // here is talking to a build that CANNOT narrow that way
                        // -- which is a different thing from nothing matching.
                        { "wildOnly", narrow.WildOnly },
                        { "tameOnly", narrow.TameOnly },
                        { "animalsOnly", narrow.AnimalsOnly },
                        { "humanlikeOnly", narrow.HumanlikeOnly },
                        { "mechanoidsOnly", narrow.MechanoidsOnly },
                        { "colonistsOnly", narrow.ColonistsOnly },
                        { "prisonersOnly", narrow.PrisonersOnly },
                        { "downedOnly", narrow.DownedOnly },
                        { "draftedOnly", narrow.DraftedOnly },
                        { "nameFilter", narrow.NameFilter }
                    } },
                { "notes", new Dictionary<string, object>
                    {
                        { "whatEachCountCounts", "pawnCount and pawnsListed are rows in pawns[], AFTER every filter; pawnsFiltered is spawnedPawnTotal minus that. spawnedPawnTotal, colonistCount and hostileCount are NOT narrowed by the category filters: spawnedPawnTotal is every spawned pawn on the map, colonistCount every live colonist, and hostileCount every hostile that survived includeDead / includeColonists -- so a hostileCount above pawnsListed is a filtered list, not a missing pawn." },
                        { "filtersCombineByAnd", narrow.Any
                            ? "Every filter that is on must be satisfied. Contradictory pairs (wildOnly with tameOnly, animalsOnly with colonistsOnly) are REFUSED rather than answered with an empty list; a filter that is merely rare comes back empty and says so through pawnsFiltered."
                            : "no category filter was set; pawns[] is narrowed only by hostileOnly / includeColonists / includeDead / withinOfColonists" },
                        { "everyFilterIsReadableOnTheRow", "wildOnly, tameOnly, animalsOnly, humanlikeOnly, mechanoidsOnly, colonistsOnly, prisonersOnly, downedOnly and draftedOnly each have a matching bool on every row (wild, tame, animal, humanlike, mechanoid, isFreeColonist, isPrisoner, downed, drafted), so a narrowed list can be checked against an unnarrowed one field for field." },
                        { "hediffFilter", wantHealth
                            ? (visibleHediffsOnly
                                ? "Every hediff whose Hediff.Visible is true. No condition list, no severity floor."
                                : "Every hediff, visible or not. No condition list, no severity floor.")
                            : "health block not requested" },
                        { "capacitiesImpairedMeans", "PawnCapacitiesHandler.GetLevel below 1.0. A capacity at full is left out of the map; capacitiesRead names every one that was asked, so an empty map means 'all fine', never 'not looked at'." },
                        { "scaleIsZeroToOne", "needs levels, the mood break thresholds, pain, bloodLoss and hediff severity fractions are all 0..1, so they compare directly." },
                        { "cachesRefreshed", wantHealth
                            ? "Reading pain, bleed rate and capacities refills RimWorld's own memo fields (cachedPain, cachedBleedRate, the capacity cache). No game state changes; the health tab does the same thing every frame it draws."
                            : "none" },
                        { "equipmentIsNeverNull", wantEquipment
                            ? "Every pawn has an equipment{} object. armed is always a bool; primary is null only alongside armed:false and primaryLabel then reads \"unarmed\". A pawn with no equipment tracker (animals) reports hasEquipmentTracker:false and armed:false. There is no value meaning 'not looked at'."
                            : "equipment block not requested" },
                        { "unarmedColonistsCovers", wantEquipment
                            ? "The live colonists in pawns[] carrying no primary weapon. It is drawn from the RETURNED list, so hostileOnly / includeColonists:false / withinOfColonists narrow it too -- ask with the default filters if you want the whole colony. Cross-check it against bio.incapableOf: a pawn incapable of Violent can never be armed, so reporting them as unarmed is an alarm nobody can act on."
                            : "equipment block not requested" },
                        { "bioIsNeverNull", wantBio
                            ? "Every pawn has a bio{} object. incapableOf[] holds the labels the game draws and incapableOfTags[] the raw WorkTags names, always the same length. incapableOfRead is the bool that separates 'no incapabilities' from 'could not be read' -- when it is false the empty list means NOTHING WAS LOOKED AT, and incapableOfNote says why in words. skills[] carries every skill in the game, with present:false for one the pawn has no record of."
                            : "bio block not requested" },
                        { "incapableOfSources", wantBio
                            ? "incapableOf comes from Pawn.CombinedDisabledWorkTags: backstory, traits, genes, royal titles, ideoligion role, hediffs, active quests and mutation, unioned. incapableOfFromBackstoryTraitsGenes is the permanent subset, so a quest-imposed incapability can be told from a lifelong one. A Brawler is NOT here -- a brawler can fight, he just cannot be handed a ranged weapon; that is in traits[]."
                            : "bio block not requested" },
                        { "thoughtsAreReadNotRecalculated", wantThoughts
                            ? "memories[] comes from ThoughtHandler.memories.Memories and situational[] from the handler's own cache, read directly. RimWorld's UI getters (GetDistinctMoodThoughtGroups / MoodOffsetOfGroup) were NOT used: they recalculate situational thoughts, and Thought_Situational.Notify_BecameActive deletes every memory of a def that produces one. situationalCacheStale carries the game's own thoughtsDirty flag; situationalCacheReadable is false if the private field could not be found, which is different from there being no situational thoughts."
                            : "thoughts block not requested" },
                        { "moodOffsetIsStackedTotal", wantThoughts
                            ? "In every memories[] and situational[] row, moodOffset is the TOTAL the whole stack contributes; count is how many identical thoughts stacked and moodOffsetEach is the per-instance value. moodOffsetTotal is the sum over both lists, so sum(rows) == moodOffsetTotal holds. Both lists are sorted by moodOffset ASCENDING -- worst first."
                            : "thoughts block not requested" },
                        { "workPriorityMeaning", wantWork
                            ? "priority 0 = never do this job; 1 is the most urgent and 4 the least. It is NULL, never 0, for a pawn whose priority table was not read -- reading one that does not exist calls Log.Error (which pauses the colony) and writes six jobs onto the pawn, so it is not read. manualPriorities false means the Work tab is a checkbox grid, in which GetPriority reports 3 for every active job whatever is stored; priorityStored carries the real cell. disabled:true is a job this pawn can never do -- cross-check bio.incapableOf."
                            : "work block not requested" },
                        { "scheduleIsLetters", wantSchedule
                            ? "schedule.hours is one letter per hour, hour 0 first, 24 characters. schedule.key maps every letter to its TimeAssignmentDef name, so the string is self-describing and home/pawn_config takes the same alphabet. current is what the pawn is assigned RIGHT NOW."
                            : "schedule block not requested" },
                        { "settingsAreWritable", wantSettings
                            ? "Every field in settings{} is writable through home/pawn_config, addressed by settings.thingId or by name. allowedArea null means UNRESTRICTED -- the whole map -- and allowedAreaIsUnrestricted says so as a bool, so a null is never a failed read. pawnConfigOptions at the top level lists the areas and enum values this map will accept."
                            : "settings block not requested" },
                        { "opinionIsReconstructed", wantRelations
                            ? "relations.colonistOpinions[].opinion is NOT Pawn_RelationsTracker.OpinionOf. That call recalculates situational social thoughts, and Thought_Situational.Notify_BecameActive deletes every memory of the def it produces -- the same hazard as the mood tab, one field over. The opinion here is rebuilt from relation offsets, social memories and the situational cache read without recalculating; each row's situationalSocialCached says whether that last term was available, and when it is false the number is a FLOOR. relations.opinionMethod spells out the whole chain."
                            : "relations block not requested" },
                        { "animalsIsNotAFilter", wantAnimals
                            ? "animals:true ADDS a block; it narrows nothing. Every pawn in pawns[] carries animals{}, and a humanlike\'s says isAnimal:false and applies:false rather than being absent. animalCount and tameAnimalCount at the top level are counted over the RETURNED list, so hostileOnly / includeColonists:false / withinOfColonists narrow them too."
                            : "animals block not requested" },
                        { "animalsFieldsNotDuplicated", wantAnimals
                            ? "master, followDrafted, followFieldwork and allowedArea are Animals-tab fields that live in settings{} (pass settings:true), not here; the hunger level lives in needs{} (pass needs:true). One field, one place. tame and wild are NOT complements -- a visiting faction\'s pack animal is neither. wildness is a StatDef in 1.6, read only for animals. steps come from an INTERNAL method reached by reflection: training.stepsReadable false means every steps is null meaning NOT READ, never zero."
                            : "animals block not requested" }
                    } },
                { "pawns", pawns }
            };

            // Only when asked. An empty list would otherwise say "everyone is
            // armed" on a call that never looked -- the same confusion in the
            // other direction.
            if (wantEquipment)
                payload["unarmedColonists"] = unarmedColonists;

            // The areas and enum values home/pawn_config will accept on this
            // map, hoisted here rather than repeated inside every settings{}
            // block: it is a property of the map, not of a pawn.
            if (wantSettings)
                payload["pawnConfigOptions"] = PawnSettingsRead.Options(map);

            // Only when asked, for the same reason as unarmedColonists: a 0 on a
            // call that never looked would read as "no animals on this map".
            if (wantAnimals)
            {
                payload["animalCount"] = animalCount;
                payload["tameAnimalCount"] = tameAnimalCount;
            }

            return payload;
        }

        /// <summary>
        /// Red nametag, both of the ways a pawn gets one. See the class remarks.
        /// </summary>
        private static bool IsHostile(Pawn pawn, out string reason)
        {
            reason = "none";
            try
            {
                var mental = SafeMentalState(pawn);
                if (!string.IsNullOrEmpty(mental) &&
                    mental.IndexOf("Manhunter", StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    reason = "manhunter:" + mental;
                    return true;
                }
            }
            catch
            {
                // fall through to the faction test
            }

            try
            {
                var faction = pawn.Faction;
                if (faction == null)
                    return false;
                var player = PlayerFaction();
                if (player == null)
                    return false;
                if (faction == player)
                    return false;
                if (faction.HostileTo(player))
                {
                    reason = "faction:" + faction.Name;
                    return true;
                }
            }
            catch
            {
                reason = "unknown";
            }
            return false;
        }

        /// <summary>
        /// The player faction, or null. NOT `Faction.OfPlayer`: its whole body is
        /// `get_OfPlayerSilentFail` followed by `Verse.Log.Error`, and
        /// `Log.Error`'s call path contains `TickManager.Pause()`. So on a map
        /// with no player faction, asking who the player is would PAUSE the
        /// colony -- and the harness reads a pause as a person pressing space.
        /// This file used the unsafe one until 2026-09-01.
        /// </summary>
        private static Faction PlayerFaction()
        {
            try { return Faction.OfPlayerSilentFail; }
            catch { return null; }
        }

        // ==================================================================
        // The category narrowings
        // ==================================================================

        /// <summary>
        /// The nine category filters and the name substring, parsed off the main
        /// thread and then applied once per pawn. They combine by AND.
        ///
        /// A pair that cannot both hold -- wildOnly with tameOnly, animalsOnly
        /// with colonistsOnly -- is REFUSED by <see cref="Validate"/> rather than
        /// answered with an empty pawns[], because an empty list is also what a
        /// safe map looks like and the two must never share a shape.
        /// </summary>
        private sealed class PawnFilters
        {
            public bool WildOnly;
            public bool TameOnly;
            public bool AnimalsOnly;
            public bool HumanlikeOnly;
            public bool MechanoidsOnly;
            public bool ColonistsOnly;
            public bool PrisonersOnly;
            public bool DownedOnly;
            public bool DraftedOnly;

            /// <summary>The substring as the caller sent it, for the echo.</summary>
            public string NameFilter;

            private string _nameLower;

            public bool Any
            {
                get
                {
                    return WildOnly || TameOnly || AnimalsOnly || HumanlikeOnly || MechanoidsOnly
                        || ColonistsOnly || PrisonersOnly || DownedOnly || DraftedOnly
                        || !string.IsNullOrEmpty(_nameLower);
                }
            }

            /// <summary>
            /// Null when the request is answerable, otherwise a ready refusal.
            /// Also lowercases the name substring once.
            /// </summary>
            public object Validate(bool includeColonists)
            {
                if (NameFilter != null)
                {
                    var trimmed = NameFilter.Trim();
                    if (NameFilter.Length > 0 && trimmed.Length == 0)
                    {
                        return Refusal(
                            "nameFilter is whitespace only. A substring that matches everything is not a "
                            + "narrowing; omit the argument for every pawn.",
                            "nameFilter=\"" + NameFilter + "\"");
                    }
                    _nameLower = trimmed.Length == 0 ? null : trimmed.ToLowerInvariant();
                }

                if (ColonistsOnly && !includeColonists)
                {
                    return Refusal(
                        "colonistsOnly:true asks for nothing but colonists and includeColonists:false removes "
                        + "them; together they can only ever return an empty list.",
                        "colonistsOnly=true, includeColonists=false");
                }

                foreach (var pair in ConflictingPairs())
                {
                    if (!pair.Item1 || !pair.Item2)
                        continue;
                    return Refusal(
                        "these two filters cannot both hold: " + pair.Item4,
                        pair.Item3);
                }
                return null;
            }

            /// <summary>
            /// The pairs no pawn can satisfy, each with the race or faction fact
            /// that makes it impossible. Animal is intelligence &lt; 1 and organic
            /// flesh, Humanlike is intelligence &gt;= 2 and mechanoid is a flesh
            /// type, so those three are disjoint by definition; IsColonist
            /// requires Humanlike and the player faction; IsFreeColonist adds no
            /// host faction, which a prisoner always has.
            /// </summary>
            private IEnumerable<Tuple<bool, bool, string, string>> ConflictingPairs()
            {
                yield return Pair(WildOnly, TameOnly, "wildOnly", "tameOnly",
                    "wild means no faction at all, tame means the player's.");
                yield return Pair(WildOnly, ColonistsOnly, "wildOnly", "colonistsOnly",
                    "a colonist is of the player faction; a wild animal has none.");
                yield return Pair(WildOnly, HumanlikeOnly, "wildOnly", "humanlikeOnly",
                    "wildOnly is an animal filter, and no race is both an animal and humanlike.");
                yield return Pair(WildOnly, MechanoidsOnly, "wildOnly", "mechanoidsOnly",
                    "wildOnly is an animal filter, and an animal is organic flesh where a mechanoid is not.");
                yield return Pair(WildOnly, PrisonersOnly, "wildOnly", "prisonersOnly",
                    "a prisoner is a humanlike guest; an animal has no guest tracker.");
                yield return Pair(TameOnly, ColonistsOnly, "tameOnly", "colonistsOnly",
                    "a colonist is humanlike; tameOnly is an animal filter.");
                yield return Pair(TameOnly, HumanlikeOnly, "tameOnly", "humanlikeOnly",
                    "no race is both an animal and humanlike.");
                yield return Pair(TameOnly, MechanoidsOnly, "tameOnly", "mechanoidsOnly",
                    "an animal is organic flesh where a mechanoid is not.");
                yield return Pair(TameOnly, PrisonersOnly, "tameOnly", "prisonersOnly",
                    "a prisoner is a humanlike guest; an animal has no guest tracker.");
                yield return Pair(AnimalsOnly, HumanlikeOnly, "animalsOnly", "humanlikeOnly",
                    "Animal is intelligence below tool-user and Humanlike is intelligence at or above humanlike.");
                yield return Pair(AnimalsOnly, MechanoidsOnly, "animalsOnly", "mechanoidsOnly",
                    "an animal is organic flesh where a mechanoid is not.");
                yield return Pair(AnimalsOnly, ColonistsOnly, "animalsOnly", "colonistsOnly",
                    "a colonist is humanlike.");
                yield return Pair(AnimalsOnly, PrisonersOnly, "animalsOnly", "prisonersOnly",
                    "a prisoner is a humanlike guest; an animal has no guest tracker.");
                yield return Pair(HumanlikeOnly, MechanoidsOnly, "humanlikeOnly", "mechanoidsOnly",
                    "Humanlike is an intelligence level and mechanoid a flesh type; no race in the game is both.");
                yield return Pair(MechanoidsOnly, ColonistsOnly, "mechanoidsOnly", "colonistsOnly",
                    "a colonist is humanlike, which a mechanoid is not.");
                yield return Pair(MechanoidsOnly, PrisonersOnly, "mechanoidsOnly", "prisonersOnly",
                    "a prisoner is a humanlike guest.");
                yield return Pair(ColonistsOnly, PrisonersOnly, "colonistsOnly", "prisonersOnly",
                    "a free colonist has no host faction and a prisoner always has one.");
            }

            private static Tuple<bool, bool, string, string> Pair(
                bool a, bool b, string nameA, string nameB, string why)
            {
                return Tuple.Create(a, b, nameA + "=true, " + nameB + "=true", nameB + " -- " + why);
            }

            /// <summary>
            /// True when this pawn survives every filter that is on. The booleans
            /// are passed in rather than re-read so the row a caller receives and
            /// the test that let it through cannot disagree.
            /// </summary>
            public bool Accepts(Pawn pawn, bool isAnimal, bool isHumanlike, bool isMechanoid,
                                bool isPrisoner, bool isDowned, bool isDrafted, bool isFreeColonist,
                                string name)
            {
                if (WildOnly && !(isAnimal && SafeFactionless(pawn)))
                    return false;
                if (TameOnly && !(isAnimal && SafeIsPlayerFaction(pawn)))
                    return false;
                if (AnimalsOnly && !isAnimal)
                    return false;
                if (HumanlikeOnly && !isHumanlike)
                    return false;
                if (MechanoidsOnly && !isMechanoid)
                    return false;
                if (ColonistsOnly && !isFreeColonist)
                    return false;
                if (PrisonersOnly && !isPrisoner)
                    return false;
                if (DownedOnly && !isDowned)
                    return false;
                if (DraftedOnly && !isDrafted)
                    return false;

                if (_nameLower != null)
                {
                    if (!Contains(name, _nameLower)
                        && !Contains(BridgeCommon.Try(() => pawn.def != null ? pawn.def.defName : null, null), _nameLower)
                        && !Contains(BridgeCommon.Try(() => pawn.kindDef != null ? pawn.kindDef.defName : null, null), _nameLower))
                        return false;
                }
                return true;
            }

            private static bool Contains(string haystack, string needleLower)
            {
                return haystack != null
                    && haystack.IndexOf(needleLower, StringComparison.OrdinalIgnoreCase) >= 0;
            }

            /// <summary>
            /// A filter refusal, in this DLL's shape: what was parsed, why it
            /// cannot be answered, and what to send instead.
            /// </summary>
            private static object Refusal(string detail, string seen)
            {
                return new Dictionary<string, object>
                {
                    { "success", false },
                    { "tool", "home/list_pawns" },
                    { "error", "Contradictory filters: " + detail },
                    { "filtersSeen", seen },
                    { "expected", "Send one of the pair, or neither. The filters combine by AND, so a pawn has to satisfy every one that is on; nothing here is answered with an empty list when it could be answered with a reason." }
                };
            }
        }

        private static List<Pawn> SafeAllPawns(Map map)
        {
            try
            {
                return map.mapPawns?.AllPawnsSpawned?.ToList();
            }
            catch
            {
                return null;
            }
        }

        private static string SafeName(Pawn pawn)
        {
            try
            {
                return pawn.LabelShortCap.ToString();
            }
            catch
            {
                try { return pawn.LabelCap.ToString(); }
                catch { return null; }
            }
        }

        private static string SafeFactionName(Pawn pawn)
        {
            try { return pawn.Faction?.Name; }
            catch { return null; }
        }

        private static bool SafeIsColonist(Pawn pawn)
        {
            try { return pawn.IsColonist; }
            catch { return false; }
        }

        private static bool SafeIsPrisoner(Pawn pawn)
        {
            try { return pawn.IsPrisoner; }
            catch { return false; }
        }

        /// <summary>
        /// `IsFreeColonist` = IsColonist (humanlike, player faction, not an
        /// insecure slave) AND no host faction. Narrower than IsColonist, which
        /// stays true for a colonist another faction is holding.
        /// </summary>
        private static bool SafeIsFreeColonist(Pawn pawn)
        {
            try { return pawn.IsFreeColonist; }
            catch { return false; }
        }

        private static bool SafeHumanlike(Pawn pawn)
        {
            try { return pawn.RaceProps != null && pawn.RaceProps.Humanlike; }
            catch { return false; }
        }

        /// <summary>`Pawn.Drafted` is drafter != null &amp;&amp; drafter.Drafted;
        /// a pawn with no drafter (an animal) is false, not an error.</summary>
        private static bool SafeDrafted(Pawn pawn)
        {
            try { return pawn.Drafted; }
            catch { return false; }
        }

        /// <summary>Faction.OfPlayerSilentFail, never OfPlayer: see
        /// PlayerFaction. A null player faction makes this false, which is the
        /// honest answer on a map with no colony.</summary>
        private static bool SafeIsPlayerFaction(Pawn pawn)
        {
            try
            {
                var player = PlayerFaction();
                return player != null && pawn.Faction == player;
            }
            catch { return false; }
        }

        private static bool SafeFactionless(Pawn pawn)
        {
            try { return pawn.Faction == null; }
            catch { return false; }
        }

        private static bool SafeAnimal(Pawn pawn)
        {
            try { return pawn.RaceProps != null && pawn.RaceProps.Animal; }
            catch { return false; }
        }

        private static bool SafeMechanoid(Pawn pawn)
        {
            try { return pawn.RaceProps != null && pawn.RaceProps.IsMechanoid; }
            catch { return false; }
        }

        /// <summary>
        /// Verse.RaceProperties.predator -- a plain public bool FIELD on the
        /// race def, so this reads nothing and cannot Log.Error. It is the
        /// game's own answer to "is this thing a predator", and it is why no
        /// tool in this stack needs a hardcoded list of defNames. Humanlikes and
        /// mechanoids leave it at its default, false.
        /// </summary>
        private static bool SafePredator(Pawn pawn)
        {
            try { return pawn != null && pawn.RaceProps != null && pawn.RaceProps.predator; }
            catch { return false; }
        }

        /// <summary>
        /// Verse.RaceProperties.manhunterOnDamageChance, a public float field,
        /// 0..1. 0f for anything that never turns on its attacker.
        /// </summary>
        private static float SafeManhunterOnDamageChance(Pawn pawn)
        {
            try { return pawn != null && pawn.RaceProps != null ? pawn.RaceProps.manhunterOnDamageChance : 0f; }
            catch { return 0f; }
        }

        private static bool SafeDowned(Pawn pawn)
        {
            try { return pawn.Downed; }
            catch { return false; }
        }

        private static bool SafeDead(Pawn pawn)
        {
            try { return pawn.Dead; }
            catch { return false; }
        }

        private static string SafeJob(Pawn pawn)
        {
            try { return pawn.CurJobDef?.defName; }
            catch { return null; }
        }

        private static string SafeMentalState(Pawn pawn)
        {
            try { return pawn.MentalStateDef?.defName; }
            catch { return null; }
        }

        // ==================================================================
        // health{} and needs{} -- the blocks that ended the Health-tab flipping
        // ==================================================================

        /// <summary>
        /// The capacities worth reporting, in the order the Health tab draws
        /// them. `PawnCapacityDefOf` is a DefOf, so these are resolved defs, not
        /// name lookups -- but a DefOf field can still be null if a def was
        /// removed by a mod, hence the null test on every one.
        /// </summary>
        private static IEnumerable<KeyValuePair<string, PawnCapacityDef>> Capacities()
        {
            yield return Cap("Consciousness", PawnCapacityDefOf.Consciousness);
            yield return Cap("Moving", PawnCapacityDefOf.Moving);
            yield return Cap("Manipulation", PawnCapacityDefOf.Manipulation);
            yield return Cap("Sight", PawnCapacityDefOf.Sight);
            yield return Cap("Hearing", PawnCapacityDefOf.Hearing);
            yield return Cap("Talking", PawnCapacityDefOf.Talking);
            yield return Cap("Breathing", PawnCapacityDefOf.Breathing);
            yield return Cap("BloodFiltration", PawnCapacityDefOf.BloodFiltration);
            yield return Cap("BloodPumping", PawnCapacityDefOf.BloodPumping);
        }

        private static KeyValuePair<string, PawnCapacityDef> Cap(string name, PawnCapacityDef def)
        {
            return new KeyValuePair<string, PawnCapacityDef>(name, def);
        }

        /// <summary>
        /// Everything the Health tab would show, without opening the Health tab.
        /// Null only when the pawn has no health tracker at all.
        /// </summary>
        private static Dictionary<string, object> HealthBlock(Pawn pawn, bool visibleOnly)
        {
            Pawn_HealthTracker tracker;
            try { tracker = pawn.health; }
            catch { return null; }
            if (tracker == null)
                return null;

            var block = new Dictionary<string, object>();
            var dead = SafeDead(pawn);

            block["downed"] = SafeDowned(pawn);
            block["dead"] = dead;
            block["state"] = Try<string>(() => tracker.State.ToString(), null);
            block["inBed"] = Try<bool>(() => RestUtility.InBed(pawn), false);
            block["summaryPct"] = Round3(Try<float?>(
                () => tracker.summaryHealth != null ? (float?)tracker.summaryHealth.SummaryHealthPercent : null, null));

            var set = Try<HediffSet>(() => tracker.hediffSet, null);

            // PainTotal / BleedRateTotal write cachedPain / cachedBleedRate.
            // That is a memo behind a dirty flag, refreshed by RimWorld's own
            // health tab every frame it draws -- not a game-state change. See
            // the class remarks.
            block["painTotal"] = Round3(set == null ? (float?)null : Try<float?>(() => set.PainTotal, null));
            var bleed = set == null ? (float?)null : Try<float?>(() => set.BleedRateTotal, null);
            block["bleedRatePerDay"] = Round3(bleed);
            block["bleeding"] = bleed != null && bleed.Value > 0f;

            // TicksUntilDeathDueToBloodLoss returns int.MaxValue when nothing is
            // bleeding. Emitting that as a number would read as "dies in 35,791
            // hours", which is the kind of confident nonsense this toolset keeps
            // getting caught by; it is null instead, and only computed when the
            // pawn is actually losing blood.
            double? hoursToBleedOut = null;
            if (bleed != null && bleed.Value > 0f && !dead)
            {
                var ticks = Try<int>(() => HealthUtility.TicksUntilDeathDueToBloodLoss(pawn), int.MaxValue);
                if (ticks > 0 && ticks < int.MaxValue)
                    hoursToBleedOut = Math.Round(ticks / 2500.0, 1);   // 2500 ticks = 1 in-game hour
            }
            block["hoursUntilDeathFromBloodLoss"] = hoursToBleedOut;

            block["needsTend"] = Try<bool>(() => tracker.HasHediffsNeedingTend(false), false);
            // PLAYBOOK sharp edge: self-tend is OFF by default for everyone
            // including new joiners, and in a colony of one that is fatal --
            // nobody else can doctor them. Null means the pawn has no
            // playerSettings at all (wild animals, other factions).
            block["selfTend"] = Try<bool?>(
                () => pawn.playerSettings != null ? (bool?)pawn.playerSettings.selfTend : null, null);
            block["medicalCare"] = Try<string>(
                () => pawn.playerSettings != null ? pawn.playerSettings.medCare.ToString() : null, null);

            // Capacities. GetLevel short-circuits on a dead pawn, so the dead are
            // not asked at all. capacitiesRead is emitted whether or not anything
            // is impaired, so an empty impaired map means "all fine" rather than
            // "not looked at".
            var impaired = new Dictionary<string, object>();
            var read = new List<object>();
            if (!dead)
            {
                var caps = Try<PawnCapacitiesHandler>(() => tracker.capacities, null);
                if (caps != null)
                {
                    foreach (var entry in Capacities())
                    {
                        if (entry.Value == null)
                            continue;
                        var have = Try<float?>(() => (float?)caps.GetLevel(entry.Value), null);
                        if (have == null)
                            continue;
                        read.Add(entry.Key);
                        if (have.Value < 0.999f)
                            impaired[entry.Key] = Round3(have);
                    }
                }
            }
            block["capacitiesImpaired"] = impaired;
            block["capacitiesRead"] = read;

            // The hediffs. NO WATCH LIST -- see the class remarks. Sorted worst
            // first so the caller that prints one line gets the right one.
            var rows = new List<object>();
            var hidden = 0;
            float? bloodLoss = null;
            var lifeThreatening = false;
            var tendable = 0;
            if (set != null)
            {
                List<Hediff> list;
                try { list = set.hediffs != null ? set.hediffs.ToList() : new List<Hediff>(); }
                catch { list = new List<Hediff>(); }

                foreach (var h in list)
                {
                    if (h == null)
                        continue;
                    var visible = Try<bool>(() => h.Visible, true);
                    if (visibleOnly && !visible)
                    {
                        hidden++;
                        continue;
                    }

                    var defName = Try<string>(() => h.def != null ? h.def.defName : null, null);
                    var severity = Try<float?>(() => (float?)h.Severity, null);
                    if (string.Equals(defName, "BloodLoss", StringComparison.Ordinal))
                        bloodLoss = severity;

                    var threat = Try<bool>(() => h.IsCurrentlyLifeThreatening, false);
                    if (threat)
                        lifeThreatening = true;
                    var tendNow = Try<bool>(() => h.TendableNow(false), false);
                    if (tendNow)
                        tendable++;

                    var row = new Dictionary<string, object>();
                    // `label` is RimWorld's OWN full label -- "Heatstroke
                    // (extreme)", "Cut (moderate)". It is the exact string
                    // health.py used to scrape off the screen with a regex, and
                    // the reason severity is legible at a glance.
                    row["label"] = Try<string>(() => h.LabelCap.ToString(), null);
                    row["labelBase"] = Try<string>(() => h.LabelBaseCap.ToString(), null);
                    row["severityLabel"] = Try<string>(() => h.SeverityLabel, null);
                    row["defName"] = defName;
                    row["severity"] = Round3(severity);
                    row["part"] = Try<string>(() => h.Part != null ? h.Part.Label : null, null);
                    row["partDefName"] = Try<string>(
                        () => h.Part != null && h.Part.def != null ? h.Part.def.defName : null, null);
                    row["bleeding"] = Try<bool>(() => h.Bleeding, false);
                    row["bleedRate"] = Round3(Try<float?>(() => (float?)h.BleedRate, null));
                    row["tendableNow"] = tendNow;
                    row["isTended"] = Try<bool>(() => HediffUtility.IsTended(h), false);
                    row["tendQuality"] = Round3(TendQuality(h));
                    row["permanent"] = Try<bool>(() => h.IsPermanent(), false);
                    row["lifeThreatening"] = threat;
                    row["isBad"] = Try<bool>(() => h.def != null && h.def.isBad, false);
                    row["visible"] = visible;
                    rows.Add(row);
                }

                // Worst first: life-threatening, then bleeding, then severity.
                // A caller printing one line per colonist gets the line that
                // matters rather than whatever RimWorld happened to add last.
                try
                {
                    rows = rows
                        .Cast<Dictionary<string, object>>()
                        .OrderByDescending(r => AsBool(r, "lifeThreatening") ? 1 : 0)
                        .ThenByDescending(r => AsBool(r, "bleeding") ? 1 : 0)
                        .ThenByDescending(r => AsDouble(r, "severity"))
                        .Cast<object>()
                        .ToList();
                }
                catch { }
            }

            block["hediffs"] = rows;
            block["hediffCount"] = rows.Count;
            block["hediffsHiddenByVisibleFilter"] = hidden;
            block["bloodLoss"] = Round3(bloodLoss);
            block["anyLifeThreatening"] = lifeThreatening;
            block["tendableHediffCount"] = tendable;
            return block;
        }

        private static float? TendQuality(Hediff h)
        {
            try
            {
                var withComps = h as HediffWithComps;
                if (withComps == null || withComps.comps == null)
                    return null;
                foreach (var c in withComps.comps)
                {
                    var tend = c as HediffComp_TendDuration;
                    if (tend != null)
                        return tend.IsTended ? (float?)tend.tendQuality : null;
                }
                return null;
            }
            catch { return null; }
        }

        /// <summary>
        /// Food, rest, joy, mood -- and the mood thresholds the pawn actually
        /// breaks at, which is the number stock `list_colonists` cannot give.
        ///
        /// `mentalState` (already a top-level field) is only non-null AFTER the
        /// break has happened. `mood` against `BreakThresholdMinor` is the number
        /// that lets a caller see one coming.
        ///
        /// Null for a pawn with no needs tracker at all (mechanoids). `mood` is
        /// null on most animals, which is not the same thing and is reported
        /// separately.
        /// </summary>
        private static Dictionary<string, object> NeedsBlock(Pawn pawn)
        {
            Pawn_NeedsTracker tracker;
            try { tracker = pawn.needs; }
            catch { return null; }
            if (tracker == null)
                return null;

            var block = new Dictionary<string, object>();

            var all = new Dictionary<string, object>();
            try
            {
                var list = tracker.AllNeeds;
                if (list != null)
                {
                    foreach (var n in list)
                    {
                        if (n == null || n.def == null)
                            continue;
                        all[n.def.defName] = Round3(Try<float?>(() => (float?)n.CurLevelPercentage, null));
                    }
                }
            }
            catch { }
            block["all"] = all;

            // The four named ones are hoisted out because they are what anyone
            // asks for, and because a caller should not have to know the defName
            // spelling to find whether somebody is starving.
            block["food"] = Round3(Try<float?>(
                () => tracker.food != null ? (float?)tracker.food.CurLevelPercentage : null, null));
            block["hungerCategory"] = Try<string>(
                () => tracker.food != null ? tracker.food.CurCategory.ToString() : null, null);
            block["rest"] = Round3(Try<float?>(
                () => tracker.rest != null ? (float?)tracker.rest.CurLevelPercentage : null, null));
            block["joy"] = Round3(Try<float?>(
                () => tracker.joy != null ? (float?)tracker.joy.CurLevelPercentage : null, null));

            var mood = RoundD(Try<float?>(
                () => tracker.mood != null ? (float?)tracker.mood.CurLevelPercentage : null, null));
            block["mood"] = mood;

            double? minor = null, major = null, extreme = null;
            try
            {
                var breaker = pawn.mindState != null ? pawn.mindState.mentalBreaker : null;
                if (breaker != null)
                {
                    minor = RoundD(Try<float?>(() => (float?)breaker.BreakThresholdMinor, null));
                    major = RoundD(Try<float?>(() => (float?)breaker.BreakThresholdMajor, null));
                    extreme = RoundD(Try<float?>(() => (float?)breaker.BreakThresholdExtreme, null));
                }
            }
            catch { }

            block["breakThresholdMinor"] = minor;
            block["breakThresholdMajor"] = major;
            block["breakThresholdExtreme"] = extreme;

            string risk;
            if (mood == null)
                risk = null;                                    // most animals: no mood need at all
            else if (extreme != null && mood.Value <= extreme.Value) risk = "extreme";
            else if (major != null && mood.Value <= major.Value) risk = "major";
            else if (minor != null && mood.Value <= minor.Value) risk = "minor";
            else risk = "none";
            block["breakRisk"] = risk;

            block["mentalState"] = SafeMentalState(pawn);
            return block;
        }

        // ==================================================================
        // equipment{} -- the block whose absence read as "armed"
        // ==================================================================

        /// <summary>
        /// What the pawn is actually holding and wearing.
        ///
        /// **Returns a dictionary unconditionally.** Not null on an animal, not
        /// null on a corpse, not null on a mechanoid, not null if every read
        /// inside it throws. The bug this replaces was `equipment: None` for
        /// five sessions being read as "the tool cannot tell me", then as
        /// "presumably armed"; there is no such value here. A pawn with no
        /// equipment tracker at all still answers `armed: false`, because a warg
        /// is in fact not carrying a rifle.
        /// </summary>
        private static Dictionary<string, object> EquipmentBlock(Pawn pawn)
        {
            var block = new Dictionary<string, object>();

            Pawn_EquipmentTracker tracker;
            try { tracker = pawn.equipment; }
            catch { tracker = null; }
            block["hasEquipmentTracker"] = tracker != null;

            // Primary is the weapon in the hands -- the one thing five sessions
            // of notes got wrong. Everything else in this block is context.
            Thing primary = null;
            if (tracker != null)
                primary = Try<Thing>(() => tracker.Primary, null);

            block["armed"] = primary != null;
            block["primary"] = primary != null ? GearRow(primary, "primary") : null;
            // The word, spelled out. A caller printing one line per colonist
            // gets "unarmed" rather than a blank where a weapon should be.
            block["primaryLabel"] = primary != null
                ? Try<string>(() => primary.LabelCap.ToString(), "unknown weapon")
                : "unarmed";

            // AllEquipmentListForReading is normally the primary and nothing
            // else; it is walked anyway so a modded second slot cannot go
            // unreported the way the whole block used to.
            var equipped = new List<object>();
            if (tracker != null)
            {
                List<ThingWithComps> list;
                try
                {
                    var all = tracker.AllEquipmentListForReading;
                    list = all != null ? all.ToList() : new List<ThingWithComps>();
                }
                catch { list = new List<ThingWithComps>(); }

                foreach (var eq in list)
                {
                    if (eq == null)
                        continue;
                    equipped.Add(GearRow(eq, "equipped"));
                }
            }
            block["equipped"] = equipped;
            block["equippedCount"] = equipped.Count;

            // A rifle in the pack is not a rifle in the hands, but it is the
            // difference between "arm somebody" and "haul a gun across the map",
            // so it is reported separately rather than folded into `armed`.
            var invWeapons = new List<object>();
            var invItems = 0;
            var hasInventory = false;
            try
            {
                var inv = pawn.inventory;
                // ThingOwner is an IList<Thing>; index it rather than pick an
                // enumerator, because ThingOwner<T> hides a second
                // GetEnumerator. Same reason as ListThingsTool.Emit.
                ThingOwner owner = inv != null ? inv.innerContainer : null;
                hasInventory = owner != null;
                if (owner != null)
                {
                    invItems = owner.Count;
                    for (var i = 0; i < owner.Count; i++)
                    {
                        var t = owner[i];
                        if (t == null || t.def == null)
                            continue;
                        if (Try<bool>(() => t.def.IsWeapon, false))
                            invWeapons.Add(GearRow(t, "inventory"));
                    }
                }
            }
            catch { }
            block["hasInventoryTracker"] = hasInventory;
            block["inventoryWeapons"] = invWeapons;
            block["inventoryWeaponCount"] = invWeapons.Count;
            block["inventoryItemCount"] = invItems;

            var worn = new List<object>();
            Pawn_ApparelTracker apparel;
            try { apparel = pawn.apparel; }
            catch { apparel = null; }
            block["hasApparelTracker"] = apparel != null;
            if (apparel != null)
            {
                List<Apparel> list;
                try
                {
                    var all = apparel.WornApparel;
                    list = all != null ? all.ToList() : new List<Apparel>();
                }
                catch { list = new List<Apparel>(); }

                foreach (var a in list)
                {
                    if (a == null)
                        continue;
                    worn.Add(GearRow(a, "apparel"));
                }
            }
            block["apparel"] = worn;
            block["apparelCount"] = worn.Count;

            return block;
        }

        /// <summary>
        /// One item of gear. `label` is RimWorld's OWN label, which already
        /// carries the quality and the damage state -- "charge rifle (normal)",
        /// "flak vest (good, tattered)" -- for the same reason the hediff rows
        /// use `Hediff.LabelCap`: it is the string a person would read off the
        /// screen, so a caller and a screenshot cannot disagree.
        /// </summary>
        private static Dictionary<string, object> GearRow(Thing t, string slot)
        {
            var row = new Dictionary<string, object>();
            row["slot"] = slot;
            // 2026-09-04: the stable ID, so combat's equip verification can
            // compare the requested weapon by identity instead of by label.
            row["thingId"] = Try<string>(() => t.GetUniqueLoadID(), null);
            row["defName"] = Try<string>(() => t.def != null ? t.def.defName : null, null);
            row["label"] = Try<string>(() => t.LabelCap.ToString(), null);
            row["labelBase"] = Try<string>(() => t.def != null ? t.def.label : null, null);
            row["quality"] = QualityOf(t);
            row["stuff"] = Try<string>(() => t.Stuff != null ? t.Stuff.defName : null, null);
            row["stackCount"] = Try<int>(() => t.stackCount, 1);

            row["isWeapon"] = Try<bool>(() => t.def != null && t.def.IsWeapon, false);
            row["ranged"] = Try<bool>(() => t.def != null && t.def.IsRangedWeapon, false);
            row["melee"] = Try<bool>(() => t.def != null && t.def.IsMeleeWeapon, false);
            row["isApparel"] = Try<bool>(() => t.def != null && t.def.IsApparel, false);

            // 2026-09-02: what this garment actually COVERS. The Scout's gear
            // section had to infer "nothing on the legs" from garment names and
            // printed a warning saying it was guessing. ThingDef.apparel is a
            // public ApparelProperties whose bodyPartGroups and layers are
            // public fields (verified by reflection). Both lists are ALWAYS
            // emitted and are empty for a weapon, so a caller tells "covers
            // nothing" from "not apparel" with isApparel, never with a missing
            // key. BodyPartGroupDef.LabelShortCap memoises a capitalised string
            // on the shared def -- a memo, not game state.
            var covers = new List<object>();
            var layers = new List<object>();
            try
            {
                var ap = t.def != null ? t.def.apparel : null;
                if (ap != null)
                {
                    var groups = ap.bodyPartGroups;
                    if (groups != null)
                    {
                        for (var i = 0; i < groups.Count; i++)
                        {
                            var g = groups[i];
                            if (g == null)
                                continue;
                            var gr = new Dictionary<string, object>();
                            gr["defName"] = g.defName;
                            gr["label"] = Try<string>(() => g.LabelShortCap, g.defName);
                            covers.Add(gr);
                        }
                    }
                    var ls = ap.layers;
                    if (ls != null)
                    {
                        for (var i = 0; i < ls.Count; i++)
                        {
                            var l = ls[i];
                            if (l != null)
                                layers.Add(l.defName);
                        }
                    }
                }
            }
            catch { }
            row["bodyPartGroups"] = covers;
            row["apparelLayers"] = layers;

            // Condition, only where the def uses hit points at all. A weapon at
            // 40% is still a weapon, so this never affects `armed`.
            var usesHp = Try<bool>(() => t.def != null && t.def.useHitPoints, false);
            if (usesHp)
            {
                var hp = Try<int>(() => t.HitPoints, -1);
                var max = Try<int>(() => t.MaxHitPoints, -1);
                row["hitPoints"] = hp >= 0 ? (object)hp : null;
                row["maxHitPoints"] = max > 0 ? (object)max : null;
                row["conditionPct"] = hp >= 0 && max > 0 ? Round3((float)hp / max) : null;
            }
            else
            {
                row["hitPoints"] = null;
                row["maxHitPoints"] = null;
                row["conditionPct"] = null;
            }

            return row;
        }

        /// <summary>
        /// The quality word the game shows, or null for a def that has no
        /// quality at all (most raw weapons a tribal makes do; a wooden club
        /// does not). Null here means "this item has no quality", never "not
        /// read" -- the item is present in the list either way.
        /// </summary>
        private static string QualityOf(Thing t)
        {
            try
            {
                QualityCategory qc;
                return QualityUtility.TryGetQuality(t, out qc) ? qc.ToString() : null;
            }
            catch { return null; }
        }

        // ==================================================================
        // bio{} -- who this colonist is, and what they cannot be asked to do
        // ==================================================================

        /// <summary>
        /// Age, backstory, traits, every skill, the work tags the pawn CANNOT
        /// do, and any title.
        ///
        /// **Returns a dictionary unconditionally**, on the same contract as
        /// `equipment{}`: an animal, a mechanoid and a corpse all get a `bio{}`
        /// object, with the emptiness spelled out inside it
        /// (`hasStory: false`, `hasSkills: false`, `incapableOfRead: false`)
        /// rather than by the block being null. A null here would read as "this
        /// colonist has no incapabilities", which is precisely the wrong answer
        /// for the pawn the field exists for.
        /// </summary>
        private static Dictionary<string, object> BioBlock(Pawn pawn)
        {
            var block = new Dictionary<string, object>();

            // ---------------------------------------------------------- age
            Pawn_AgeTracker age;
            try { age = pawn.ageTracker; }
            catch { age = null; }
            block["hasAgeTracker"] = age != null;
            block["ageBiological"] = age == null
                ? null
                : Try<object>(() => (object)age.AgeBiologicalYears, null);
            block["ageChronological"] = age == null
                ? null
                : Try<object>(() => (object)age.AgeChronologicalYears, null);

            // ------------------------------------------------------ backstory
            Pawn_StoryTracker story;
            try { story = pawn.story; }
            catch { story = null; }
            block["hasStory"] = story != null;

            // Backstory titles are gender-specific in RimWorld ("Nurse" /
            // "Male nurse"), so the pawn's own gender is passed rather than
            // reading the raw `title` field, which would silently show the male
            // form for everybody.
            var gender = Try<Gender>(() => pawn.gender, Gender.None);

            // adulthood is genuinely null for a pawn who never grew up in this
            // colony's terms -- a child, or a backstory set with a childhood
            // only. The key is emitted as an explicit null, never omitted,
            // because "no adulthood" and "not asked" must not look alike.
            block["childhood"] = story == null ? null : Try<string>(
                () => story.Childhood != null ? story.Childhood.TitleCapFor(gender) : null, null);
            block["adulthood"] = story == null ? null : Try<string>(
                () => story.Adulthood != null ? story.Adulthood.TitleCapFor(gender) : null, null);
            block["childhoodDefName"] = story == null ? null : Try<string>(
                () => story.Childhood != null ? story.Childhood.defName : null, null);
            block["adulthoodDefName"] = story == null ? null : Try<string>(
                () => story.Adulthood != null ? story.Adulthood.defName : null, null);

            // --------------------------------------------------------- traits
            // NOT TraitSet.TraitsSorted: that property clears and refills the
            // per-pawn `tmpTraits` scratch list and returns a reference to it,
            // so a second reader overwrites the first one's answer. allTraits is
            // the real list and is copied here. See the class remarks.
            var traits = new List<object>();
            TraitSet traitSet = null;
            if (story != null)
                traitSet = Try<TraitSet>(() => story.traits, null);
            block["hasTraits"] = traitSet != null;
            if (traitSet != null)
            {
                List<Trait> raw;
                try
                {
                    raw = traitSet.allTraits != null
                        ? traitSet.allTraits.ToList()
                        : new List<Trait>();
                }
                catch { raw = new List<Trait>(); }

                foreach (var t in raw)
                {
                    if (t == null)
                        continue;
                    var r = new Dictionary<string, object>();
                    // `label` is the DEGREE-SPECIFIC name the game draws --
                    // "Too smart", not "Intelligence" -- which is the string a
                    // person reads off the character card.
                    r["label"] = Try<string>(() => t.LabelCap, null);
                    r["defName"] = Try<string>(() => t.def != null ? t.def.defName : null, null);
                    r["degree"] = Try<object>(() => (object)t.Degree, null);
                    r["description"] = Try<string>(
                        () => { var d = t.CurrentData; return d != null ? d.description : null; }, null);
                    // A trait suppressed by a gene is still ON the pawn but is
                    // not in effect. Reported rather than filtered, because
                    // dropping it silently is how a reader ends up surprised.
                    r["suppressed"] = Try<bool>(() => t.Suppressed, false);
                    traits.Add(r);
                }
            }
            block["traits"] = traits;
            block["traitCount"] = traits.Count;

            // --------------------------------------------------------- skills
            // Every skill in the game, in the order the Skills tab draws them,
            // whether or not the pawn has a record for it. `present: false` is
            // the honest answer for a def with no record; calling
            // Pawn_SkillTracker.GetSkill would have been shorter and would have
            // hit a Verse.Log.Error, which pauses the colony.
            var skills = new List<object>();
            Pawn_SkillTracker skillTracker;
            try { skillTracker = pawn.skills; }
            catch { skillTracker = null; }
            block["hasSkills"] = skillTracker != null;
            if (skillTracker != null)
            {
                List<SkillRecord> records;
                try
                {
                    records = skillTracker.skills != null
                        ? skillTracker.skills.ToList()
                        : new List<SkillRecord>();
                }
                catch { records = new List<SkillRecord>(); }

                List<SkillDef> order;
                try
                {
                    var defs = DefDatabase<SkillDef>.AllDefsListForReading;
                    order = defs != null ? defs.ToList() : new List<SkillDef>();
                }
                catch { order = new List<SkillDef>(); }

                // If the DefDatabase is unreadable for any reason, fall back to
                // the pawn's own record order rather than returning no skills at
                // all -- an empty skills[] on a colonist is a lie.
                if (order.Count == 0)
                {
                    foreach (var rec in records)
                        if (rec != null && rec.def != null)
                            order.Add(rec.def);
                }

                foreach (var def in order)
                {
                    if (def == null)
                        continue;

                    SkillRecord found = null;
                    foreach (var candidate in records)
                    {
                        if (candidate != null && candidate.def == def)
                        {
                            found = candidate;
                            break;
                        }
                    }
                    var rec2 = found;

                    var r = new Dictionary<string, object>();
                    // defName ("Shooting") rather than skillLabel ("shooting"),
                    // because a caller matches on this and a reader prints it.
                    r["name"] = Try<string>(() => def.defName, null);
                    r["label"] = Try<string>(() => def.skillLabel, null);
                    r["present"] = rec2 != null;
                    // `level` is the effective level the game uses, aptitudes
                    // folded in; `levelStored` is the raw saved levelInt. Both,
                    // because they differ on a pawn with an aptitude gene and a
                    // caller should not have to wonder which one it got.
                    r["level"] = rec2 == null ? null : Try<object>(() => (object)rec2.Level, null);
                    r["levelStored"] = rec2 == null ? null : Try<object>(() => (object)rec2.levelInt, null);
                    // The WORD, not the enum ordinal: "None" / "Minor" / "Major".
                    r["passion"] = rec2 == null
                        ? "None"
                        : Try<string>(() => rec2.passion.ToString(), "None");
                    // true when the pawn cannot do this skill at all -- the
                    // greyed-out row on the Skills tab.
                    r["disabled"] = rec2 != null && Try<bool>(() => rec2.TotallyDisabled, false);
                    skills.Add(r);
                }
            }
            block["skills"] = skills;
            block["skillCount"] = skills.Count;

            // ---------------------------------------------------- incapableOf
            // The reason this whole block exists. See the class remarks: without
            // it the Scout reports a pawn who CANNOT hold a weapon as unarmed on
            // every brief forever.
            //
            // Read as a nullable so that "no incapabilities" and "could not be
            // read" are different values rather than the same empty list.
            var combined = Try<WorkTags?>(() => (WorkTags?)pawn.CombinedDisabledWorkTags, null);

            var labels = new List<object>();
            var tagNames = new List<object>();
            if (combined != null)
                SplitWorkTags(combined.Value, labels, tagNames);

            block["incapableOf"] = labels;
            block["incapableOfTags"] = tagNames;
            block["incapableOfCount"] = labels.Count;
            block["incapableOfRead"] = combined != null;
            block["incapableOfNote"] = combined != null
                ? (labels.Count > 0
                    ? "Pawn.CombinedDisabledWorkTags: backstory, traits, genes, royal titles, ideoligion role, hediffs, quests and mutation, all unioned. An empty list here means the pawn is capable of everything."
                    : "Read successfully and empty: this pawn has no disabled work tags at all.")
                : "NOT READ. Pawn.CombinedDisabledWorkTags was not obtainable for this pawn -- animals and mechanoids have no story tracker. Do not read the empty list above as 'capable of everything'.";
            block["disabledWorkTagsFlags"] = combined != null ? (object)(int)combined.Value : null;

            // The narrower, permanent half: backstory + traits + genes, without
            // the quest- and title-imposed tags. A caller comparing the two can
            // tell "born this way" from "for the duration of this quest".
            var storyOnly = story == null
                ? null
                : Try<WorkTags?>(() => (WorkTags?)story.DisabledWorkTagsBackstoryTraitsAndGenes, null);
            var storyLabels = new List<object>();
            var storyTagNames = new List<object>();
            if (storyOnly != null)
                SplitWorkTags(storyOnly.Value, storyLabels, storyTagNames);
            block["incapableOfFromBackstoryTraitsGenes"] = storyLabels;
            block["incapableOfFromBackstoryTraitsGenesTags"] = storyTagNames;
            block["incapableOfFromBackstoryTraitsGenesRead"] = storyOnly != null;

            // ---------------------------------------------------------- title
            // Royal title first (Empire), then ideoligion role, then the raw
            // story title a scenario can set. `titleSource` says which one it is
            // so "Count" and "Moral guide" are not silently the same field.
            string title = null;
            var titleSource = "none";

            try
            {
                var royalty = pawn.royalty;
                if (royalty != null)
                {
                    var main = Try<RoyalTitleDef>(() => royalty.MainTitle(), null);
                    if (main != null)
                    {
                        title = Try<string>(() => main.GetLabelCapFor(pawn), null);
                        if (string.IsNullOrEmpty(title))
                            title = Try<string>(() => main.defName, null);
                        if (!string.IsNullOrEmpty(title))
                            titleSource = "royalty";
                    }
                }
            }
            catch { }

            if (string.IsNullOrEmpty(title))
            {
                try
                {
                    var ideo = pawn.Ideo;
                    if (ideo != null)
                    {
                        var role = Try<Precept_Role>(() => ideo.GetRole(pawn), null);
                        if (role != null)
                        {
                            title = Try<string>(() => role.LabelForPawn(pawn), null);
                            if (!string.IsNullOrEmpty(title))
                                titleSource = "ideoRole";
                        }
                    }
                }
                catch { }
            }

            if (string.IsNullOrEmpty(title) && story != null)
            {
                title = Try<string>(() => story.title, null);
                if (!string.IsNullOrEmpty(title))
                    titleSource = "storyTitle";
            }

            block["title"] = string.IsNullOrEmpty(title) ? null : title;
            block["titleSource"] = titleSource;

            return block;
        }

        /// <summary>
        /// Split a WorkTags bitfield into the labels the game draws and the raw
        /// enum names, appending to both lists.
        ///
        /// The bits are walked from `Enum.GetValues` rather than through
        /// `CharacterCardUtility.WorkTagsFrom`, which does the same job in the
        /// game's own UI but is NOT public -- reflection says so, and calling it
        /// would not have compiled.
        ///
        /// `WorkTypeDefsUtility.LabelTranslated` ends in a `Verse.Log.Error` for
        /// an unknown or mixed tag, and Log.Error pauses the colony. Its switch
        /// covers all 21 values of the enum, so passing only single defined bits
        /// -- which `Enum.IsDefined` plus the `!= None` test guarantees -- makes
        /// that arm unreachable rather than merely unlikely.
        /// </summary>
        private static void SplitWorkTags(WorkTags tags, List<object> labels, List<object> tagNames)
        {
            if (tags == WorkTags.None)
                return;

            Array values;
            try { values = Enum.GetValues(typeof(WorkTags)); }
            catch { return; }

            foreach (var raw in values)
            {
                WorkTags tag;
                try { tag = (WorkTags)raw; }
                catch { continue; }
                if (tag == WorkTags.None)
                    continue;
                if ((tags & tag) != tag)
                    continue;

                var name = Try<string>(() => tag.ToString(), null);
                if (string.IsNullOrEmpty(name))
                    continue;
                tagNames.Add(name);

                // The label falls back to the enum name rather than to null, so
                // incapableOf[] and incapableOfTags[] always have the same
                // length and a caller can zip them.
                var label = name;
                if (Enum.IsDefined(typeof(WorkTags), tag))
                    label = Try<string>(() => WorkTypeDefsUtility.LabelTranslated(tag), name);
                if (string.IsNullOrEmpty(label))
                    label = name;
                labels.Add(label);
            }
        }

        // ==================================================================
        // thoughts{} -- the Mood tab, read without editing the colonist's mind
        // ==================================================================

        /// <summary>
        /// The private cache the Mood tab draws from. Read directly, because the
        /// public getter that fills it can DELETE memories -- see the class
        /// remarks. Null if RimWorld ever renames the field, and
        /// `situationalCacheReadable` reports that rather than emitting an empty
        /// list that would read as "no situational thoughts".
        /// </summary>
        private static readonly System.Reflection.FieldInfo SituationalCacheField =
            BridgeCommon.PrivateInstanceField(typeof(SituationalThoughtHandler), "cachedThoughts");

        /// <summary>
        /// The handler's own dirty flag, so freshness is reported rather than
        /// assumed. On a paused game this is normally false and the cache is
        /// exactly what is on screen.
        /// </summary>
        private static readonly System.Reflection.FieldInfo SituationalDirtyField =
            BridgeCommon.PrivateInstanceField(typeof(SituationalThoughtHandler), "thoughtsDirty");

        /// <summary>
        /// What the Mood tab shows. **Returns a dictionary unconditionally**,
        /// same contract as `equipment{}` and `bio{}`: a mechanoid has no mood
        /// and says so in `hasMood: false` with empty lists beside it, rather
        /// than by being null.
        /// </summary>
        private static Dictionary<string, object> ThoughtsBlock(Pawn pawn)
        {
            var block = new Dictionary<string, object>();

            Need_Mood mood = null;
            try { mood = pawn.needs != null ? pawn.needs.mood : null; }
            catch { mood = null; }
            block["hasMood"] = mood != null;

            var moodLevel = RoundD(Try<float?>(
                () => mood != null ? (float?)mood.CurLevelPercentage : null, null));
            block["mood"] = moodLevel;
            // The number the UI writes on the tab. Same fact as `mood`, in the
            // units a person reads, so a caller never has to remember to
            // multiply and a screenshot and a payload cannot disagree.
            block["moodPercent"] = moodLevel == null
                ? null
                : (object)Math.Round(moodLevel.Value * 100.0, 1);

            ThoughtHandler handler = null;
            try { handler = mood != null ? mood.thoughts : null; }
            catch { handler = null; }
            block["hasThoughtHandler"] = handler != null;

            // -------------------------------------------------------- memories
            // thoughts.memories.Memories is `ldarg.0; ldfld; ret` -- the purest
            // read available. The stacking is done here with Thought.GroupsWith
            // rather than by asking RimWorld to group them, because RimWorld's
            // grouping call recalculates situational thoughts on the way past
            // and can delete memories. See the class remarks.
            var memoryRows = new List<object>();
            double memoryTotal = 0.0;
            MemoryThoughtHandler memHandler = null;
            if (handler != null)
                memHandler = Try<MemoryThoughtHandler>(() => handler.memories, null);
            block["hasMemoryHandler"] = memHandler != null;
            if (memHandler != null)
            {
                List<Thought> mem;
                try
                {
                    var list = memHandler.Memories;
                    mem = list != null ? list.Cast<Thought>().ToList() : new List<Thought>();
                }
                catch { mem = new List<Thought>(); }
                memoryRows = GroupThoughts(mem, out memoryTotal);
            }
            block["memories"] = memoryRows;
            block["memoryCount"] = memoryRows.Count;

            // ----------------------------------------------------- situational
            var situationalRows = new List<object>();
            double situationalTotal = 0.0;
            object cacheStale = null;

            SituationalThoughtHandler sitHandler = null;
            if (handler != null)
                sitHandler = Try<SituationalThoughtHandler>(() => handler.situational, null);
            block["hasSituationalHandler"] = sitHandler != null;

            var readable = sitHandler != null && SituationalCacheField != null;
            block["situationalCacheReadable"] = readable;
            if (readable)
            {
                List<Thought_Situational> cached;
                try { cached = SituationalCacheField.GetValue(sitHandler) as List<Thought_Situational>; }
                catch { cached = null; }

                var live = new List<Thought>();
                if (cached != null)
                {
                    // Indexed, not foreach'd: the list is RimWorld's own and a
                    // second reader must not be holding an enumerator over it.
                    // Same reason ListThingsTool indexes its ThingOwner.
                    for (var i = 0; i < cached.Count; i++)
                    {
                        var s = cached[i];
                        if (s == null)
                            continue;
                        // Active is `curStageIndex >= 0` -- an inactive cached
                        // thought is one the game is keeping around, not one the
                        // colonist is feeling.
                        if (!Try<bool>(() => s.Active, false))
                            continue;
                        live.Add(s);
                    }
                }
                situationalRows = GroupThoughts(live, out situationalTotal);

                if (SituationalDirtyField != null)
                    cacheStale = Try<object>(() => SituationalDirtyField.GetValue(sitHandler), null);
            }

            block["situational"] = situationalRows;
            block["situationalCount"] = situationalRows.Count;
            // The handler's own thoughtsDirty. true = RimWorld has flagged the
            // situational thoughts for recalculation and has not yet done it, so
            // the rows above are from before that flag. null = not readable.
            block["situationalCacheStale"] = cacheStale;

            // memories + situational, so a caller can check the parts against
            // the whole. NOT the pawn's mood: mood is this offset applied to a
            // baseline and then clamped, which is why both are reported.
            block["moodOffsetTotal"] = Math.Round(memoryTotal + situationalTotal, 3);
            block["memoryMoodOffsetTotal"] = Math.Round(memoryTotal, 3);
            block["situationalMoodOffsetTotal"] = Math.Round(situationalTotal, 3);
            block["mentalState"] = SafeMentalState(pawn);
            return block;
        }

        /// <summary>
        /// Stack identical thoughts the way the Mood tab does and emit one row
        /// per group, worst first.
        ///
        /// **`moodOffset` on each row is the STACKED TOTAL** -- the sum over
        /// every thought in the group -- because that is the number that adds up
        /// to `moodOffsetTotal`. `moodOffsetEach` is the per-instance value.
        /// Said in the class remarks too; it is the one thing about this block
        /// that a caller can get quietly wrong.
        ///
        /// Sorted ASCENDING, so the most negative entry is first: the useful end
        /// of a mood breakdown is the end that is about to cause a break.
        /// </summary>
        private static List<object> GroupThoughts(List<Thought> source, out double total)
        {
            total = 0.0;
            var rows = new List<object>();
            if (source == null || source.Count == 0)
                return rows;

            var used = new bool[source.Count];
            for (var i = 0; i < source.Count; i++)
            {
                if (used[i])
                    continue;
                used[i] = true;
                var head = source[i];
                if (head == null)
                    continue;

                var count = 1;
                var sum = (double)MoodOffsetOf(head);

                for (var j = i + 1; j < source.Count; j++)
                {
                    if (used[j])
                        continue;
                    var other = source[j];
                    if (other == null)
                        continue;
                    // GroupsWith is RimWorld's own stacking predicate, the same
                    // one GetDistinctMoodThoughtGroups uses. It reads
                    // CurStageIndex and compares labels; it stores nothing.
                    var head2 = head;
                    var other2 = other;
                    if (!Try<bool>(() => head2.GroupsWith(other2), false))
                        continue;
                    used[j] = true;
                    count++;
                    sum += MoodOffsetOf(other);
                }

                var row = new Dictionary<string, object>();
                row["label"] = Try<string>(() => head.LabelCap, null);
                row["defName"] = Try<string>(() => head.def != null ? head.def.defName : null, null);
                row["count"] = count;
                row["moodOffset"] = Math.Round(sum, 3);
                row["moodOffsetEach"] = Math.Round(sum / count, 3);
                rows.Add(row);
                total += sum;
            }

            try
            {
                rows = rows
                    .Cast<Dictionary<string, object>>()
                    .OrderBy(r => AsDouble(r, "moodOffset"))
                    .Cast<object>()
                    .ToList();
            }
            catch { }

            total = Math.Round(total, 3);
            return rows;
        }

        /// <summary>
        /// One thought's mood contribution.
        ///
        /// `Thought.MoodOffset()` opens with `if (CurStage == null) { Log.Error(..); }`,
        /// and `Log.Error` calls `TickManager.Pause()`, which the harness reads
        /// as a person pressing space. So CurStage is tested HERE first and the
        /// call is never made on a thought that would trip it. A thought with no
        /// stage contributes 0, which is what RimWorld returns after logging.
        /// </summary>
        private static float MoodOffsetOf(Thought t)
        {
            if (t == null)
                return 0f;
            var stage = Try<ThoughtStage>(() => t.CurStage, null);
            if (stage == null)
                return 0f;
            var v = Try<float>(() => t.MoodOffset(), 0f);
            if (float.IsNaN(v) || float.IsInfinity(v))
                return 0f;
            return v;
        }

        // ------------------------------------------------------------- helpers

        /// <summary>
        /// Swallow-and-return-default. Every game read below is behind one: a
        /// thrown exception inside a companion read reaches Verse.Log.Error,
        /// which calls TickManager.Pause(), which the harness reads as a person
        /// pausing the game.
        /// </summary>
        private static T Try<T>(Func<T> f, T fallback)
        {
            return BridgeCommon.Try(f, fallback);
        }

        /// <summary>Three decimals, or null for absent / NaN / infinity. Never a
        /// float: a raw float serialises as 0.30000001192092896 and every number
        /// in this payload is a fraction somebody has to read.</summary>
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

        private static bool AsBool(Dictionary<string, object> d, string key)
        {
            return BridgeCommon.Bool(d, key);
        }

        private static double AsDouble(Dictionary<string, object> d, string key)
        {
            return BridgeCommon.Num(d, key);
        }

        /// <summary>The shared map gate; see BridgeCommon.TryGetMap. The error
        /// text names this tool.</summary>
        private static bool TryGetMap(out Map map, out string error)
        {
            return BridgeCommon.TryGetMap("home/list_pawns", out map, out error);
        }

        /// <summary>The shared refusal shape; see BridgeCommon.Failure.</summary>
        private static object Failure(string error)
        {
            return BridgeCommon.Failure("home/list_pawns", error);
        }
    }
}
