"""Source guards for home/world and home/zone_cells op=crop.

Both depend on RimWorld types, so the hazards are checked in the source rather
than at runtime: a getter that writes, and a getter that pauses the colony.
"""
from pathlib import Path
import unittest


SRC = Path(__file__).parents[1] / "src"
WORLD = (SRC / "WorldTool.cs").read_text(encoding="utf-8")
ZONE_CELLS = (SRC / "ZoneCellsTool.cs").read_text(encoding="utf-8")
CONTRACT = (Path(__file__).parent / "contract_test.py").read_text(encoding="utf-8")


class WorldToolSourceTests(unittest.TestCase):
    def test_the_tool_is_registered_under_its_own_name(self):
        self.assertIn('private const string ToolName = "home/world"', WORLD)

    def test_ticksabs_is_only_read_once_gamestartabstick_is_non_zero(self):
        """TickManager.get_TicksAbs reaches Log.Error, which calls Pause()."""
        self.assertIn("gameStartAbsTick", WORLD)
        guard = WORLD.index("gameStartAbsTick")
        read = WORLD.index("ticks.TicksAbs")
        self.assertLess(guard, read)

    def test_the_biome_is_the_property_not_the_private_field(self):
        self.assertIn("row.PrimaryBiome", WORLD)
        self.assertNotIn("row.biome", WORLD)

    def test_the_growing_period_uses_the_games_own_range_and_twelfths(self):
        self.assertIn("TwelfthsInAverageTemperatureRange", WORLD)
        self.assertIn("GenDate.DaysPerTwelfth", WORLD)
        self.assertIn("DefaultMinOptimalGrowthTemperature", WORLD)

    def test_showing_the_planet_always_tries_to_hide_it_again(self):
        self.assertIn("CameraJumper.TryShowWorld()", WORLD)
        self.assertIn("CameraJumper.TryHideWorld()", WORLD)
        # The delay is awaited inside a try so a cancelled wait still hides.
        self.assertIn("// A cancelled wait must still hide the world again.",
                      WORLD)

    def test_show_needs_dry_run_false(self):
        self.assertIn("var wantShow = show && !dryRun;", WORLD)

    def test_the_bool_sweep_can_never_turn_show_on(self):
        self.assertIn('"home/world"', CONTRACT)
        block = CONTRACT[CONTRACT.index("EXPLICIT_ONLY"):
                         CONTRACT.index("# This read-only tool promises")]
        self.assertIn('"home/world"', block)


class ZoneCropSourceTests(unittest.TestCase):
    def test_crop_is_an_op_the_tool_accepts(self):
        self.assertIn('wantOp != "crop"', ZONE_CELLS)
        self.assertIn('case "crop":', ZONE_CELLS)

    def test_the_crop_is_set_with_the_games_own_method(self):
        self.assertIn("growing.SetPlantDefToGrow(wanted)", ZONE_CELLS)

    def test_the_crop_is_read_off_the_field_because_the_getter_writes(self):
        """Zone_Growing.PlantDefToGrow's getter assigns plantDefToGrow when it
        is null, so reading it to report the crop would set one."""
        self.assertIn('PrivateInstanceField(typeof(Zone_Growing), "plantDefToGrow")',
                      ZONE_CELLS)
        self.assertNotIn("growing.PlantDefToGrow", ZONE_CELLS)
        self.assertNotIn("growing.GetPlantDefToGrow()", ZONE_CELLS)

    def test_an_unmatched_plant_lists_what_the_zone_will_take(self):
        self.assertIn("No sowable plant matches", ZONE_CELLS)
        self.assertIn("PlantUtility.CanSowOnGrower", ZONE_CELLS)

    def test_plant_on_any_other_op_is_refused_rather_than_ignored(self):
        self.assertIn("plant applies to op=crop only", ZONE_CELLS)


if __name__ == "__main__":
    unittest.main()
