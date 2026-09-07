"""Offline regressions for build.py's refusal path, on faked place_building replies.

`BridgeCommon.Failure` returns `{success, tool, error}`; build.py read `message`
and printed `FAILED: None`. All fixtures are plain dicts; no game required.
"""
import io
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import build


def unknown(spec):
    """Exactly what PlaceBuildingTool.Build refuses an unresolvable name with."""
    return {"success": False, "tool": "home/place_building",
            "error": 'No ThingDef or TerrainDef matches "%s" by defName or label.' % spec}


def placed_ok():
    """A dry run that resolved, so the failure branch must not touch it."""
    return {"success": True, "tool": "home/place_building", "dryRun": True,
            "def": {"defName": "PowerConduit", "label": "power conduit",
                    "kind": "ThingDef"},
            "position": {"x": 114, "z": 145}, "rotatable": False,
            "researchFinished": True, "costList": [{"defName": "Steel", "count": 1}],
            "materials": {"rows": [{"defName": "Steel", "label": "steel",
                                    "needed": 1, "onMap": 300, "forbidden": 0,
                                    "reservedByOtherBlueprints": 0,
                                    "available": 300, "shortfall": 0}],
                          "canBuildNow": True, "missing": "", "unreadable": False},
            "rotations": [{"rotation": "north", "accepted": True, "reason": "",
                           "blockingThings": []}]}


def render(reply, asked=None, menu=()):
    """`report` with the architect menu stubbed out (these tests are offline)."""
    with mock.patch.object(build, "_menu_names", return_value=list(menu)):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            build.report(reply, asked)
    return out.getvalue()


class ReasonTests(unittest.TestCase):
    def test_the_bridge_reason_is_read_off_error_not_message(self):
        text = render(unknown("Conduit"), "Conduit")
        self.assertNotIn("FAILED: None", text)
        self.assertIn("No ThingDef or TerrainDef matches", text)
        self.assertIn('"Conduit"', text)

    def test_message_still_wins_where_a_stock_tool_uses_it(self):
        self.assertEqual(build.reason({"success": False, "message": "no map loaded",
                                       "error": "unused"}), "no map loaded")

    def test_a_reply_with_no_reason_at_all_never_renders_as_none(self):
        text = build.reason({"success": False, "tool": "home/place_building"})
        self.assertNotIn("None", text)
        self.assertIn("no reason given", text)
        self.assertIn("tool", text)

    def test_a_non_dict_reply_is_reported_as_one(self):
        self.assertIn("did not return a payload", build.reason("truncated json"))


class AliasTests(unittest.TestCase):
    def test_conduit_names_powerconduit(self):
        text = render(unknown("Conduit"), "Conduit")
        self.assertIn("PowerConduit", text)
        self.assertIn("known miss", text)

    def test_sculptorstable_names_tablesculpting(self):
        self.assertIn("TableSculpting", render(unknown("SculptorsTable"), "SculptorsTable"))

    def test_the_alias_is_case_and_space_insensitive(self):
        self.assertIn("TableButcher", render(unknown("butcher table"), "butcher table"))

    def test_an_ambiguous_alias_prints_both_defs_and_picks_neither(self):
        text = render(unknown("Stove"), "Stove")
        self.assertIn("ElectricStove", text)
        self.assertIn("FueledStove", text)
        self.assertIn("name the one you meant", text)

    def test_nothing_is_substituted_for_what_was_asked_for(self):
        # A refusal, not a quiet correction.
        text = render(unknown("Conduit"), "Conduit")
        self.assertIn("FAILED:", text)
        self.assertNotIn("PLACED", text)


class SuggestionTests(unittest.TestCase):
    def test_a_typo_with_no_alias_still_gets_near_misses(self):
        text = render(unknown("SolarGenerater"), "SolarGenerater")
        self.assertIn("closest:", text)
        self.assertIn("SolarGenerator", text)

    def test_no_more_than_eight_are_named(self):
        lines = build.unknown_def_lines("Table", limit=8)
        closest = [l for l in lines if "closest:" in l]
        self.assertTrue(closest)
        self.assertLessEqual(len(closest[0].split("closest:")[1].split(",")), 8)

    def test_the_architect_menu_joins_the_pool_when_the_game_answers(self):
        # Menu labels resolve as well as defNames, so they are valid suggestions.
        text = render(unknown("sculptors table"), "sculptors table",
                      menu=["sculptor's table", "power conduit"])
        self.assertIn("sculptor's table", text)

    def test_an_unreachable_game_falls_back_to_the_static_pool(self):
        import act
        with mock.patch.object(act, "labels", side_effect=ConnectionError("down")):
            self.assertEqual(build._menu_names(), [])
            self.assertTrue(any("PowerConduit" in l
                                for l in build.unknown_def_lines("Conduit")))


class HealthyPathTests(unittest.TestCase):
    def test_a_def_that_resolves_is_untouched_by_any_of_this(self):
        text = render(placed_ok(), "PowerConduit")
        self.assertIn("power conduit (PowerConduit) at 114,145 -- DRY RUN", text)
        self.assertIn("north", text)
        self.assertNotIn("FAILED", text)
        self.assertNotIn("closest:", text)
        self.assertNotIn("known miss", text)

    def test_a_refusal_that_is_not_about_the_def_gets_no_alias_block(self):
        text = render({"success": False, "tool": "home/place_building",
                       "error": "(999,999) is not a cell on this map."}, "Conduit")
        self.assertIn("not a cell on this map", text)
        self.assertNotIn("closest:", text)
        self.assertNotIn("known miss", text)


class MainTests(unittest.TestCase):
    def run_main(self, argv, reply):
        fake = mock.MagicMock()
        fake.game.return_value = reply
        with mock.patch.object(build, "rim", fake):
            with mock.patch.object(build, "_menu_names", return_value=[]):
                with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                    build.main(argv)
        return out.getvalue()

    def test_the_reported_live_case_end_to_end(self):
        text = self.run_main(["Conduit", "114", "145"], unknown("Conduit"))
        self.assertNotIn("FAILED: None", text)
        self.assertIn("PowerConduit", text)

    def test_do_with_no_rotation_reports_the_bad_def_not_the_rotation(self):
        # The rotation probe fails too; that must not be read as "unknown rotatable".
        text = self.run_main(["SculptorsTable", "114", "145", "--do"],
                             unknown("SculptorsTable"))
        self.assertIn("TableSculpting", text)
        self.assertNotIn("needs one rotation", text)


class OneVerdictTests(unittest.TestCase):
    """2026-09-07 turn 33: a real placement printed `-- PLACED` and
    `north REFUSED -- Identical thing already exists` in one output."""

    def refused_write(self):
        return {"success": False, "tool": "home/place_building", "dryRun": False,
                "outcome": "refused",
                "error": "The game refuses this placement: Identical thing already exists here.",
                "def": {"defName": "Wall", "label": "wall", "kind": "ThingDef"},
                "position": {"x": 122, "z": 140}, "rotatable": False,
                "researchFinished": True, "materials": {"rows": []},
                "rotations": [{"rotation": "north", "accepted": False,
                               "reason": "Identical thing already exists here.",
                               "blockingThings": []}]}

    def test_a_refused_write_never_says_placed(self):
        text = render(self.refused_write())
        self.assertNotIn("-- PLACED", text)
        self.assertIn("-- REFUSED", text)
        self.assertIn("== REFUSED -- NOTHING was placed.", text)
        self.assertIn("Identical thing already exists", text)

    def test_the_rotation_row_is_detail_not_a_second_verdict(self):
        text = render(self.refused_write())
        self.assertEqual(1, text.count("=="))
        self.assertIn("north no --", text)

    def test_a_real_placement_carries_the_blueprint_id(self):
        reply = dict(placed_ok(), dryRun=False, outcome="placed",
                     placed={"thingIDNumber": 407231, "label": "power conduit",
                             "position": {"x": 114, "z": 145}, "rotation": "North"})
        text = render(reply)
        self.assertIn("== PLACED -- blueprint id 407231", text)

    def test_already_present_is_its_own_verdict(self):
        reply = dict(placed_ok(), dryRun=False, outcome="already_present",
                     alreadyPlaced=True, placed=None)
        text = render(reply)
        self.assertIn("== ALREADY THERE", text)
        self.assertNotIn("== PLACED", text)


class BlockerTests(unittest.TestCase):
    """Floor filth blocks nothing; on turn 26 it buried a torch lamp."""

    def row(self):
        return {"rotation": "north", "accepted": False,
                "reason": "Space already occupied.",
                "blockingThings": [
                    {"label": "dirt", "defName": "Filth_Dirt", "category": "Filth",
                     "position": {"x": 122, "z": 140}},
                    {"label": "blood", "defName": "Filth_Blood", "category": "Filth",
                     "position": {"x": 123, "z": 140}},
                    {"label": "torch lamp", "defName": "TorchLamp",
                     "category": "Building", "position": {"x": 122, "z": 140}},
                ]}

    def test_filth_is_dropped_and_counted_and_the_building_leads(self):
        line = build._blocker_line(self.row())
        self.assertTrue(line.strip().startswith("in the way: torch lamp"))
        self.assertNotIn("dirt", line)
        self.assertIn("2 filth ignored", line)


class RotationRefusalTests(unittest.TestCase):
    """`build.py Battery ... --do` refused on STDERR and read as doing nothing."""

    def test_the_refusal_is_on_stdout_with_the_four_verdicts(self):
        sweep = {"success": True, "dryRun": True, "outcome": "preview",
                 "rotations": [{"rotation": "north", "accepted": False,
                                "reason": "Space already occupied."},
                               {"rotation": "east", "accepted": True, "reason": ""},
                               {"rotation": "south", "accepted": False, "reason": "x"},
                               {"rotation": "west", "accepted": True, "reason": ""}]}
        with mock.patch.object(build, "place", return_value=sweep):
            with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                code = build.needs_rotation("Battery", 120, 140)
        text = out.getvalue()
        self.assertEqual(1, code)
        self.assertIn("BUILD REFUSED", text)
        self.assertIn("== NOTHING WAS PLACED", text)
        self.assertIn("build.py Battery 120 140 east --do", text)


if __name__ == "__main__":
    unittest.main()
