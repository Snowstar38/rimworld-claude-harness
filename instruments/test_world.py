"""Mock-only tests for world.py; no running game is contacted.

The point of this file is that a read stays a read: `world.py` with no flags
must never send `show`, and `--show` must be the only thing that does.
"""
import io
import unittest
from unittest import mock

import world


def payload(**kw):
    r = {"success": True, "tool": "home/world", "status": "game_loaded",
         "hasMap": True, "dryRun": True, "tile": 42371, "tileValid": True,
         "longitude": -12.5, "latitude": 41.2,
         "biome": "Temperate forest", "biomeDefName": "TemperateForest",
         "hilliness": "SmallHills", "elevation": 320.0, "rainfall": 1240.0,
         "swampiness": 0.0, "pollution": 0.0, "coastal": False,
         "temperature": 17.4, "averageTemperatureLabel": "12 C",
         "minTemperature": -8.0, "maxTemperature": 34.0,
         "growingPeriodDays": 40, "growingPeriodLabel": "40 days",
         "growingTwelfths": ["Fourth", "Fifth", "Sixth", "Seventh",
                             "Eighth", "Ninth", "Tenth", "Eleventh"],
         "growingRangeC": {"min": 6.0, "max": 42.0},
         "settlements": [{"label": "Kavla", "faction": "Ashen tribe",
                          "relation": "Neutral", "goodwill": 0,
                          "tile": 42410, "distanceTiles": 6.2,
                          "isPlayer": False}],
         "settlementRadius": 15, "settlementsNotListed": 0,
         "worldView": {"shown": False, "wantedMode": None, "watchSeconds": 0,
                       "hidden": False, "reason": "show:false"},
         "unknownArguments": []}
    r.update(kw)
    return r


class WorldCliTests(unittest.TestCase):
    def run_cli(self, argv, answer=None):
        calls = []

        def game(tool, args=None, strict=True):
            calls.append((tool, args))
            return answer if answer is not None else payload()

        with mock.patch.object(world.rim, "game", side_effect=game), \
             mock.patch.object(world.rim, "init"), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = world.main(argv)
        return code, out.getvalue(), calls

    def test_the_plain_readout_never_asks_to_show_the_planet(self):
        code, text, calls = self.run_cli([])
        self.assertEqual(0, code)
        self.assertEqual("home/world", calls[0][0])
        self.assertNotIn("show", calls[0][1])
        self.assertNotIn("dryRun", calls[0][1])
        self.assertIn("Temperate forest", text)
        self.assertIn("40 days", text)

    def test_show_is_the_only_thing_that_sends_a_write(self):
        code, _, calls = self.run_cli(["--show", "8"])
        self.assertEqual(0, code)
        self.assertTrue(calls[0][1]["show"])
        self.assertEqual(8, calls[0][1]["watchSeconds"])
        self.assertFalse(calls[0][1]["dryRun"])

    def test_a_tile_and_a_radius_reach_the_tool(self):
        code, _, calls = self.run_cli(["--tile", "42371", "--near", "30"])
        self.assertEqual(0, code)
        self.assertEqual(42371, calls[0][1]["tile"])
        self.assertEqual(30, calls[0][1]["settlementRadius"])

    def test_zero_growing_days_is_called_out_rather_than_printed_as_a_number(self):
        code, text, _ = self.run_cli(
            [], payload(growingPeriodDays=0, growingTwelfths=[],
                        growingPeriodLabel="never: no twelfth of the year is "
                                           "inside the growing range"))
        self.assertEqual(0, code)
        self.assertIn("ZERO growing days", text)

    def test_settlements_print_their_faction_and_distance(self):
        code, text, _ = self.run_cli([])
        self.assertIn("Kavla", text)
        self.assertIn("Ashen tribe", text)
        self.assertIn("6.2 tiles", text)

    def test_no_game_is_an_answer_and_a_non_zero_exit(self):
        code, text, _ = self.run_cli([], payload(status="no_game", tile=None))
        self.assertEqual(1, code)
        self.assertIn("no game is loaded", text)

    def test_a_refusal_names_its_reason(self):
        code, text, _ = self.run_cli(
            [], {"success": False, "error": "No RimBridge main-thread dispatcher"})
        self.assertEqual(1, code)
        self.assertIn("WORLD REFUSED", text)
        self.assertIn("main-thread", text)

    def test_a_planet_that_was_shown_says_whether_it_was_hidden_again(self):
        code, text, _ = self.run_cli(
            ["--show", "8"],
            payload(worldView={"shown": True, "wantedMode": "Planet",
                               "watchSeconds": 8, "hidden": False,
                               "reason": "CameraJumper.TryHideWorld() refused"}))
        self.assertEqual(0, code)
        self.assertIn("NOT HIDDEN", text)

    def test_an_unknown_flag_is_refused_rather_than_ignored(self):
        with mock.patch.object(world.rim, "init"), \
             mock.patch.object(world.rim, "game",
                               side_effect=AssertionError("called the game")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = world.main(["--zoom", "4"])
        self.assertEqual(2, code)
        self.assertIn("does not take", out.getvalue())

    def test_help_prints_usage_without_a_bridge(self):
        with mock.patch.object(world.rim, "init",
                               side_effect=AssertionError("touched the bridge")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(0, world.main(["--help"]))
        self.assertIn("python world.py", out.getvalue())


if __name__ == "__main__":
    unittest.main()
