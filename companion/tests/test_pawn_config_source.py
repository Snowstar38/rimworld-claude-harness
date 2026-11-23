"""Offline contract checks for home/pawn_config's hunt and tame writes.

2026-09-11, the bug pass. `act.py hunt <id> --do` followed by `act.py
undesignate <id> --do` printed the hunt still standing: the Cancel designator
reaches a designation that sits on a CELL and the game hangs Hunt, Tame and
Slaughter on the ANIMAL. `home/pawn_config` already had the slaughter /
releaseToWild pair; BUGS.md said in so many words that if the hunt survives,
"`home/pawn_config` needs `hunt` / `tame` writes beside its `slaughter` one".

These are NOT a copy of the slaughter path. Both halves are inverted:

  * `Designator_Hunt.CanDesignateThing` and `TameUtility.CanTame` want a WILD
    animal -- `Faction == null` or a faction whose def is not
    `humanlikeFaction` -- where the slaughter designator wants one of OURS.
  * `Designator_Hunt.DesignateThing` and `Designator_Tame.DesignateThing` both
    open with `Map.designationManager.RemoveAllDesignationsOn(t)`, so turning
    either ON clears EVERY designation on the animal, not one named opposite.

Read off the IL of the installed Assembly-CSharp.dll (RimWorld 1.6).
"""
from pathlib import Path
import unittest


SOURCE = (Path(__file__).parents[1] / "src" / "PawnConfigTool.cs").read_text(encoding="utf-8")


class HuntTameFieldTests(unittest.TestCase):
    def test_both_fields_are_declared_as_tool_parameters(self):
        self.assertIn("string hunt = null,", SOURCE)
        self.assertIn("string tame = null,", SOURCE)

    def test_they_are_threaded_through_every_hop_of_the_call_chain(self):
        # PawnConfig -> PawnConfigCore -> Pass1 / Run -> PlanAll / ChooseView.
        # A field that reaches Run but not Pass1 would write without a watch;
        # one that reaches ChooseView but not PlanAll would open a menu over
        # nothing.
        self.assertIn("training, slaughter, releaseToWild, hunt, tame, drop, nickname, dryRun, watch, watchSeconds",
                      SOURCE)
        self.assertIn("training, slaughter, releaseToWild, hunt, tame, drop, nickname, true)", SOURCE)
        self.assertIn("training, slaughter, releaseToWild, hunt, tame, drop, nickname, watch)", SOURCE)
        self.assertIn("training, slaughter, releaseToWild, hunt, tame, drop, nickname, false)", SOURCE)
        self.assertIn("trainingSpec, slaughterSpec, releaseToWildSpec, huntSpec, tameSpec, dropSpec, nicknameSpec)",
                      SOURCE)

    def test_the_two_defs_are_the_ones_the_designators_place(self):
        self.assertIn("BridgeCommon.Try<DesignationDef>(() => DesignationDefOf.Hunt, null)", SOURCE)
        self.assertIn("BridgeCommon.Try<DesignationDef>(() => DesignationDefOf.Tame, null)", SOURCE)

    def test_they_go_through_their_own_planner_not_the_slaughter_one(self):
        # PlanDesignation's exclusion names ONE opposite def; hunt and tame
        # clear everything, so sharing the planner would under-report.
        self.assertIn("private static bool PlanWildDesignation(", SOURCE)
        self.assertIn('PlanWildDesignation(target, "tame", tameDef, tameSpec, dryRun, fields, changes, refused)',
                      SOURCE)
        self.assertIn('PlanWildDesignation(target, "hunt", huntDef, huntSpec, dryRun, fields, changes, refused)',
                      SOURCE)


class WildGatesTests(unittest.TestCase):
    def test_the_faction_test_is_the_designators_and_not_the_slaughter_one(self):
        # Designator_Hunt: (pawn.Faction == null || !pawn.Faction.def.humanlikeFaction).
        # PlanDesignation asks the opposite question -- faction == the player's.
        self.assertIn("faction.def != null && faction.def.humanlikeFaction", SOURCE)
        self.assertIn("a humanlike faction", SOURCE)

    def test_our_own_animal_is_sent_to_the_slaughter_field_by_name(self):
        self.assertIn("the write you want is slaughter", SOURCE)

    def test_the_hunt_only_gates_are_there(self):
        self.assertIn("pawn.AnimalOrWildMan()", SOURCE)
        self.assertIn("pawn.IsPrisonerInPrisonCell()", SOURCE)
        self.assertIn("which Designator_Hunt refuses outright", SOURCE)

    def test_the_tame_only_gates_are_there(self):
        # TameUtility.CanTame: wildness < 1, not a dryad, no Scaria. The
        # wildness number is read out so the refusal can print it; CanTame is
        # then the authority on the two this bridge cannot read.
        self.assertIn("pawn.GetStatValue(StatDefOf.Wildness)", SOURCE)
        self.assertIn("TameUtility.CanTame requires it under 1.0", SOURCE)
        self.assertIn("TameUtility.CanTame(pawn)", SOURCE)
        self.assertIn("Scaria", SOURCE)

    def test_a_dead_animal_is_refused_before_anything_is_written(self):
        self.assertIn("This animal is dead.", SOURCE)

    def test_the_warning_toasts_are_deliberately_skipped_and_say_why(self):
        # TameUtility.ShowDesignationWarnings reads Faction.OfPlayer, which is
        # the hazard this whole file is written around.
        self.assertIn("ShowDesignationWarnings", SOURCE)
        self.assertIn("Faction.OfPlayer", SOURCE)


class RemoveAllDesignationsTests(unittest.TestCase):
    def test_turning_one_on_clears_every_designation_the_way_the_game_does(self):
        self.assertIn("manager.RemoveAllDesignationsOn(pawn);", SOURCE)
        self.assertIn("manager.AddDesignation(new Designation(pawn, def));", SOURCE)

    def test_what_it_will_clear_is_named_on_a_dry_run_too(self):
        self.assertIn("private static List<string> OtherDesignationsOn(", SOURCE)
        self.assertIn("what RemoveAllDesignationsOn would take is part of", SOURCE)
        self.assertIn('row["alsoRemoved"] = alsoRemoved;', SOURCE)

    def test_both_on_in_one_call_is_refused_rather_than_resolved(self):
        self.assertIn("private static bool WantsOn(string spec)", SOURCE)
        self.assertIn("var bothOn = WantsOn(huntSpec) && WantsOn(tameSpec);", SOURCE)
        self.assertIn("hunt and tame were both asked for ON in one call", SOURCE)

    def test_an_already_marked_animal_is_a_no_op_not_a_second_add(self):
        # DesignationManager.AddDesignation answers a double-add with
        # Verse.Log.Error, and Log.Error calls TickManager.Pause().
        self.assertIn("A second AddDesignation is a ", SOURCE)
        self.assertIn("Verse.Log.Error, which pauses the colony.", SOURCE)


class WildWriteContractTests(unittest.TestCase):
    def test_it_keeps_the_dry_run_default_and_the_on_off_grammar(self):
        self.assertIn("if (!TryParseOnOff(spec, out want))", SOURCE)
        self.assertIn("is not on/off. Accepted: on, off, true, false, yes, no, 1, 0.", SOURCE)

    def test_the_after_is_read_back_off_the_game_not_echoed(self):
        self.assertIn("afterValue = PawnSettingsRead.DesignationOnSafe(pawn, def) != null;", SOURCE)

    def test_an_unmapped_pawn_is_refused_rather_than_written_into_nothing(self):
        self.assertIn("there is no designation manager to write to", SOURCE)

    def test_the_watch_selects_the_animal_because_a_wild_one_has_no_tab(self):
        # ITab_Pawn_Training is not drawn for a wild animal, and the Animals
        # tab has no row for one: the only thing to watch is the animal.
        self.assertIn("var wild = One(huntSpec) + One(tameSpec);", SOURCE)
        self.assertIn("if (wild > best) best = wild;", SOURCE)
        self.assertIn("A wild animal draws no Animals tab row and no ", SOURCE)

    def test_the_reply_explains_the_total_exclusion_in_notes(self):
        self.assertIn('{ "designationsWild",', SOURCE)
        self.assertIn("their exclusion is TOTAL", SOURCE)


if __name__ == "__main__":
    unittest.main()
