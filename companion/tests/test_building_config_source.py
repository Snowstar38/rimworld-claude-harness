"""Offline contract checks for home/building_config's rotate field.

2026-09-07, day 68 turn 34: "there is no rotation verb anywhere on this bridge
-- 147 tools, zero matching 'key' or 'rot'." Rotation existed only as a
positional on the tool that places a NEW building, so three turns of moving a
cooler failed on facing alone. RimWorld itself only rotates while the placement
designator is held; once the blueprint is down that moment is gone, and writing
`Thing.Rotation` on the blueprint is what the designator would have written.
"""
from pathlib import Path
import unittest


SOURCE = (Path(__file__).parents[1] / "src" / "BuildingConfigTool.cs").read_text(encoding="utf-8")


class RotationSourceTests(unittest.TestCase):
    def test_the_field_is_declared_and_threaded_to_the_plan(self):
        self.assertIn("string rotation = null", SOURCE)
        self.assertIn("if (rotationSpec != null)", SOURCE)
        self.assertIn("plans.Add(PlanRotation(target, map, rotationSpec));", SOURCE)

    def test_only_a_blueprint_or_a_frame_can_be_turned(self):
        # A standing building has no rotate in vanilla either, and writing
        # Rotation on one moves its footprint without moving the thing.
        self.assertIn("if (!isBlueprint && !isFrame)", SOURCE)
        self.assertIn("RimWorld can only rotate a thing while it is", SOURCE)

    def test_a_non_rotatable_def_is_refused_before_the_setter_can_log(self):
        # Thing.Rotation's setter calls Log.Error on a non-rotatable thing, and
        # Log.Error's call path contains TickManager.Pause().
        self.assertIn("!thing.def.rotatable", SOURCE)
        self.assertIn("Log.Error pauses the colony", SOURCE)

    def test_the_turned_footprint_is_checked_before_the_write(self):
        self.assertIn("RotatedFootprintIsClear", SOURCE)
        self.assertIn("GenAdj.OccupiedRect(thing.Position, rot, thing.def.size)", SOURCE)
        self.assertIn("off the map", SOURCE)
        self.assertIn("map.thingGrid.ThingsListAtFast(cell)", SOURCE)

    def test_the_four_facings_and_the_four_numbers_both_parse(self):
        for word in ("north", "east", "south", "west"):
            self.assertIn('case "%s"' % word, SOURCE)
        for number in ("0", "1", "2", "3"):
            self.assertIn('case "%s"' % number, SOURCE)

    def test_it_is_a_dry_run_like_every_other_field(self):
        # PlanRotation returns a FieldPlan, so it goes through the same
        # dryRun / before / after / refused machinery as the rest.
        self.assertIn("private static FieldPlan PlanRotation", SOURCE)
        self.assertIn('var plan = NewPlan("rotation", wanted, thing);', SOURCE)


if __name__ == "__main__":
    unittest.main()
