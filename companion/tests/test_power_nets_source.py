"""Offline contract checks for the whole-map power-net read.

2026-09-07, turns 37-39: `attention.notConnectedToPower = 0` was read as a
clean grid for three consecutive turns while the whole hill pocket was cut off,
and chat caught it rather than any instrument. The reason is structural, not a
counting mistake: a battery is a `CompPowerBattery`, not a `CompPowerTrader`,
so it has no `PowerOn` that could be false and an isolated one can NEVER read
as unpowered. `powerNets[]` is the read that can see it.
"""
from pathlib import Path
import unittest


SOURCE = (Path(__file__).parents[1] / "src" / "ListBuildingsTool.cs").read_text(encoding="utf-8")
CONFIG = (Path(__file__).parents[1] / "src" / "BuildingConfigTool.cs").read_text(encoding="utf-8")


class PowerNetSourceTests(unittest.TestCase):
    def test_every_reply_carries_the_grid_not_only_the_buildings(self):
        # Not behind an opt-in: the whole point is that the DEFAULT check --
        # the one that read clean three turns running -- can now see it.
        self.assertIn('{ "powerNets", powerNets },', SOURCE)
        self.assertIn('{ "powerSummary", powerSummary },', SOURCE)
        self.assertIn('[ToolResponse("powerNets", "array"', SOURCE)
        self.assertIn('[ToolResponse("powerSummary", "object"', SOURCE)

    def test_the_four_flags_are_emitted_by_name(self):
        for flag in ("noProducer", "noConsumer", "isolatedBattery",
                     "isolatedTransmitter"):
            self.assertIn('flags.Add("%s")' % flag, SOURCE)
        # The orphaned battery: batteries present, no generator anywhere on it.
        self.assertIn("if (batteries.Count > 0 && producerCount == 0)", SOURCE)

    def test_a_flagged_net_names_its_buildings_and_a_foreign_net_is_quiet(self):
        self.assertIn("if (playerCount == 0) flags.Clear();", SOURCE)
        self.assertIn("private const int MaxPowerNetBuildings = 12;", SOURCE)
        self.assertIn('{ "buildingsNotListed"', SOURCE)

    def test_the_reads_are_the_pure_ones_and_the_player_faction_is_silent(self):
        # AllNetsListForReading is a field getter; CurrentStoredEnergy sums
        # StoredEnergy and writes nothing; PowerConsumption reads research only.
        self.assertIn("map.powerNetManager.AllNetsListForReading", SOURCE)
        self.assertIn("SafeFloat(net.CurrentStoredEnergy)", SOURCE)
        self.assertIn("trader.Props.PowerConsumption < 0f", SOURCE)
        self.assertIn("Faction.OfPlayerSilentFail", SOURCE)
        self.assertNotIn("Faction.OfPlayer;", SOURCE)
        # An unreadable manager is said out loud, never mistaken for a clean grid.
        self.assertIn('{ "readable", false }', SOURCE)

    def test_a_generator_is_decided_by_its_def_not_by_its_current_output(self):
        # PowerOutput is 0 on an idle or unpowered generator, which would file
        # every dark solar panel as a consumer -- the exact misreading here.
        self.assertIn("if (IsProducerDef(t))", SOURCE)
        self.assertIn("if (output > 0f) generationW += output; else idleProducerCount++;",
                      SOURCE)


class BuildingIdFormsTests(unittest.TestCase):
    """turn 37: `buildings.py gizmo <id> "Cancel"` refused an id build.py had
    returned seconds earlier. build.py prints `placed.thingIDNumber` -- a bare
    number -- and the resolver only ever matched the ThingID string."""

    def test_bare_number_and_thing_prefix_both_resolve(self):
        self.assertIn('wanted.StartsWith("Thing_", StringComparison.OrdinalIgnoreCase)', CONFIG)
        self.assertIn("t.thingIDNumber, 0) == number", CONFIG)
        self.assertIn("the bare thingIDNumber build.py prints (1234)", CONFIG)

    def test_a_cell_still_matches_the_whole_occupied_rect(self):
        # A wooden shelf is two cells; matching only the anchor is what made
        # `--inspect "Shelf@115,154"` disagree with `gizmos` on the same string.
        self.assertIn("colony.Where(t => Covers(t, bareCell))", CONFIG)


class PowerCapacitySourceTests(unittest.TestCase):
    """2026-09-07: a net's `gen` row is LIVE OUTPUT, so an unfuelled or
    disconnected generator adds a body to the generator count and zero watts;
    and `gen 5400W ... stored 1173.7Wd` printed above eleven UNPOWERED
    consumers during a solar flare reads as though power exists."""

    def test_capacity_is_reported_beside_this_tick_s_output(self):
        self.assertIn('{ "generationCapacityW", generationCapacityW },', SOURCE)
        self.assertIn('{ "consumptionCapacityW", consumptionCapacityW },', SOURCE)
        # Capacity comes off the DEF, output off the comp. Two sources, and the
        # payload keeps them apart rather than picking one.
        self.assertIn("generationCapacityW += -basis;", SOURCE)
        self.assertIn("private static float BaseWatts(CompPowerTrader trader)", SOURCE)

    def test_dark_consumers_and_idle_generators_are_counted(self):
        self.assertIn('{ "idleProducerCount", idleProducerCount },', SOURCE)
        self.assertIn('{ "poweredConsumerCount", poweredConsumerCount },', SOURCE)
        self.assertIn('{ "unpoweredConsumerCount", unpoweredConsumerCount },', SOURCE)
        self.assertIn("if (SafePowerOn(t)) poweredConsumerCount++; else unpoweredConsumerCount++;",
                      SOURCE)

    def test_a_net_with_something_dark_on_it_names_its_buildings(self):
        # A flag alone cannot answer "which lamp is out", which is the question
        # a power table is read to answer.
        self.assertIn("var nameThem = flags.Count > 0 || unpoweredConsumerCount > 0 "
                      "|| idleProducerCount > 0;", SOURCE)
        self.assertIn('{ "capacityW", trader == null', SOURCE)
        self.assertIn('{ "poweredOn", trader == null', SOURCE)
        self.assertIn('{ "hasFuel", refuel == null', SOURCE)

    def test_the_solar_flare_is_named_rather_than_inferred(self):
        self.assertIn('{ "solarFlare", conditions.Contains("SolarFlare") },', SOURCE)
        self.assertIn('{ "activeConditions", conditions.Cast<object>().ToList() } };', SOURCE)
        self.assertIn("private static List<string> ActiveConditionNames(Map map)", SOURCE)


class RockCategorySourceTests(unittest.TestCase):
    """2026-09-07: `--rock` did not defeat the player-only default -- 12,468
    natural rock rows removed by byPlayerOnly -- so there was no way to read
    natural rock at all."""

    def test_the_category_exists_and_is_validated(self):
        self.assertIn('wantCategory != "rock"', SOURCE)
        self.assertIn("category must be one of: artificial, all, rock", SOURCE)

    def test_it_refuses_the_colony_filter_instead_of_returning_nothing(self):
        # Rock belongs to no faction, so playerOnly removes every row of it.
        self.assertIn("if (rockOnly && playerOnly)", SOURCE)
        self.assertIn("category=rock with playerOnly=true returns nothing", SOURCE)

    def test_the_predicate_is_the_three_def_flags(self):
        self.assertIn("private static bool IsRockDef(ThingDef def)", SOURCE)
        self.assertIn("if (def.mineable) return true;", SOURCE)
        self.assertIn("return b != null && (b.isNaturalRock || b.isResourceRock);", SOURCE)
        self.assertIn("if (rockOnly && !IsRockDef(thing.def))", SOURCE)

    def test_what_it_dropped_is_counted_not_silent(self):
        self.assertIn('{ "byCategory", skippedCategory },', SOURCE)


class WorkLeftScaleSourceTests(unittest.TestCase):
    """2026-09-07: a row printed "0% built, work left 800" while its own
    inspect line in the same output said "Work left: 14". One field, two
    scales -- raw work ticks and the pane's ToStringWorkAmount of them."""

    def test_the_pane_s_own_rendering_rides_with_the_raw_number(self):
        self.assertIn('row["workLeftText"] = workLeft.HasValue', SOURCE)
        self.assertIn("workLeft.Value.ToStringWorkAmount()", SOURCE)
        self.assertIn('row["workToBuildText"] = workToBuild.HasValue', SOURCE)

if __name__ == "__main__":
    unittest.main()
