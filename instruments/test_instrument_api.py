import contextlib
import importlib.util
import io
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import act
import map as mapmod
import rim
import zones


class RimGameNameTests(unittest.TestCase):
    def test_short_bridge_name_matches_canonical_cli_name(self):
        rim._short_names.clear()
        with mock.patch.object(rim, "tool_names", return_value=["rimworld/set_draft"]), \
             mock.patch.object(rim, "tool", return_value={"success": True}) as call:
            rim.game("set_draft", {"pawn": "Ada"})
        self.assertEqual("rimworld/set_draft",
                         call.call_args.args[1]["tool"])

    def test_short_companion_name_resolves_to_home_namespace(self):
        rim._short_names.clear()
        with mock.patch.object(rim, "tool_names", return_value=["home/list_things"]), \
             mock.patch.object(rim, "tool", return_value={"success": True}) as call:
            rim.game("list_things")
        self.assertEqual("home/list_things", call.call_args.args[1]["tool"])

    def test_ambiguous_short_name_refuses_with_candidates(self):
        rim._short_names.clear()
        with mock.patch.object(rim, "tool_names", return_value=[
                "home/status", "rimworld/status"]), \
             self.assertRaisesRegex(rim.BridgeError, "home/status, rimworld/status"):
            rim.game("status")

    def test_failed_discovery_is_not_cached(self):
        rim._short_names.clear()
        with mock.patch.object(rim, "tool_names", side_effect=[[], ["home/list_things"]]), \
             mock.patch.object(rim, "tool", return_value={"success": True}) as call:
            rim.game("list_things")
            rim.game("list_things")
        self.assertEqual("rimworld/list_things", call.call_args_list[0].args[1]["tool"])
        self.assertEqual("home/list_things", call.call_args_list[1].args[1]["tool"])

    def test_home_and_canonical_names_are_preserved(self):
        with mock.patch.object(rim, "tool", return_value={"success": True}) as call:
            rim.game("home/list_things")
            rim.game("rimworld/click_cell")
        self.assertEqual("home/list_things", call.call_args_list[0].args[1]["tool"])
        self.assertEqual("rimworld/click_cell", call.call_args_list[1].args[1]["tool"])


class ActTests(unittest.TestCase):
    def setUp(self):
        act._cache.clear()
        act._ambiguous.clear()
        act._exact.clear()

    def test_same_label_and_id_across_categories_is_not_ambiguous(self):
        categories = {"categories": [{"id": "a", "label": "Orders"},
                                      {"id": "b", "label": "Zone"}]}
        designs = {"designators": [{"id": "cancel", "label": "Cancel"}]}
        with mock.patch.object(act.rim, "game", side_effect=[categories, designs, designs]):
            self.assertEqual("cancel", act.designator("Cancel"))

    def test_ambiguity_explains_category_pair(self):
        act._cache.update({("A", "Wall"): "one", ("B", "Wall"): "two"})
        act._ambiguous["Wall"] = ["A", "B"]
        with self.assertRaisesRegex(KeyError, r"\('A', 'Wall'\)"):
            act.designator("Wall")

    def _menu(self):
        """Two categories, one of them holding the real `wall...` spelling."""
        categories = {"categories": [{"id": "s", "label": "Structure"},
                                     {"id": "o", "label": "Orders"}]}
        structure = {"designators": [{"id": "wall", "label": "wall..."},
                                     {"id": "door", "label": "door..."},
                                     {"id": "wall-cooler", "label": "wall cooler"}]}
        orders = {"designators": [{"id": "hunt", "label": "Hunt"},
                                  {"id": "harvest", "label": "Harvest"}]}
        return mock.patch.object(act.rim, "game",
                                 side_effect=[categories, structure, orders])

    def test_lookup_is_case_and_ellipsis_insensitive(self):
        with self._menu():
            self.assertEqual("wall", act.designator("Wall"))
        self.assertEqual("wall", act.designator("wall..."))
        self.assertEqual("wall", act.designator("WALL"))
        self.assertEqual("hunt", act.designator("hunt"))
        self.assertEqual("wall", act.designator(("STRUCTURE", "Wall")))

    def test_miss_names_only_the_closest_labels(self):
        with self._menu():
            with self.assertRaises(KeyError) as caught:
                act.designator("Walll")
        message = caught.exception.args[0]
        self.assertIn("wall...", message)
        self.assertNotIn("Harvest", message)
        self.assertLessEqual(message.count(","), act.SUGGEST)
        self.assertIn("act.py labels", message)

    def test_a_retry_after_a_miss_does_not_grow_the_message(self):
        with self._menu():
            with self.assertRaises(KeyError) as first:
                act.designator("Walll")
        with self.assertRaises(KeyError) as second:
            act.designator("Walll")
        self.assertEqual(first.exception.args[0], second.exception.args[0])

    def test_refusal_prints_each_cell_reason(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = act._print_result({"success": False,
                                    "rejectedCells": [{"x": 2, "z": 3,
                                                       "reason": "Blocked"}]})
        self.assertEqual(1, rc)
        self.assertIn("2,3 -- REFUSED: Blocked", out.getvalue())


class HuntTests(unittest.TestCase):
    """act.py hunt reads the animal's cell and designates in ONE process."""

    def setUp(self):
        act._cache.clear()
        act._ambiguous.clear()
        act._exact.clear()
        act._cache["hunt"] = "hunt-id"
        self.alpacas = [
            {"name": "Alpaca", "defName": "Alpaca", "kindDef": "Alpaca",
             "thingId": "Thing_Alpaca1", "position": {"x": 120, "z": 143}},
            {"name": "Alpaca", "defName": "Alpaca", "kindDef": "Alpaca",
             "thingId": "Thing_Alpaca2", "position": {"x": 121, "z": 144}},
        ]

    def _pawns(self, rows):
        module = mock.MagicMock()
        module.all_pawns.return_value = rows
        return mock.patch.dict(sys.modules, {"pawns": module})

    def test_one_match_designates_its_current_cell(self):
        with self._pawns(self.alpacas[:1]),              mock.patch.object(act.rim, "game",
                               return_value={"success": True}) as call:
            act.hunt("Thing_Alpaca1", do=True)
        args = call.call_args.args[1]
        self.assertEqual(("hunt-id", 120, 143, False),
                         (args["designatorId"], args["x"], args["z"],
                          args["dryRun"]))

    def test_default_is_a_dry_run(self):
        with self._pawns(self.alpacas[:1]),              mock.patch.object(act.rim, "game",
                               return_value={"success": True}) as call:
            act.hunt("alpaca")
        self.assertTrue(call.call_args.args[1]["dryRun"])

    def test_several_matches_refuse_and_name_their_thing_ids(self):
        with self._pawns(self.alpacas),              mock.patch.object(act.rim, "game") as call:
            with self.assertRaises(KeyError) as caught:
                act.hunt("alpaca")
        call.assert_not_called()
        self.assertIn("Thing_Alpaca2", caught.exception.args[0])

    def test_all_designates_every_match(self):
        with self._pawns(self.alpacas),              mock.patch.object(act.rim, "game",
                               return_value={"success": True}) as call:
            act.hunt("alpaca", do=True, every=True)
        self.assertEqual([(120, 143), (121, 144)],
                         [(c.args[1]["x"], c.args[1]["z"])
                          for c in call.call_args_list])

    def test_no_match_says_so_without_touching_the_game(self):
        with self._pawns([]), mock.patch.object(act.rim, "game") as call:
            with self.assertRaises(KeyError):
                act.hunt("wombat")
        call.assert_not_called()


class MapLayerTests(unittest.TestCase):
    def test_beds_and_friends_are_the_build_layer(self):
        for word in ("beds", "bed", "buildings", "furniture", "build", "5"):
            self.assertEqual(({5}, [], {}), mapmod.parse_layers(word), word)

    def test_unknown_layer_comes_back_rather_than_being_dropped(self):
        self.assertEqual((set(), ["nope"], {}), mapmod.parse_layers("nope"))

    def test_the_help_string_lists_every_name_and_alias(self):
        help_text = mapmod.layer_names_help()
        for name in mapmod.LAYER_NAMES.values():
            self.assertIn(name, help_text)
        for alias in mapmod.LAYER_ALIAS:
            self.assertIn(alias, help_text)


class HelpTests(unittest.TestCase):
    def test_zones_help_does_not_initialize_or_list_live_game(self):
        out = io.StringIO()
        with mock.patch.object(zones.rim, "init") as init, \
             contextlib.redirect_stdout(out):
            rc = zones.main(["--help"])
        self.assertEqual(0, rc)
        init.assert_not_called()
        self.assertIn("python zones.py", out.getvalue())


class ContractDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.path.join(HERE, "..", "companion", "tests", "contract_test.py")
        spec = importlib.util.spec_from_file_location("contract_test_local", path)
        cls.contract = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.contract)

    def test_dialog_refusal_exercises_its_always_keys(self):
        live = self.contract.Live(None, lambda unused="": None)
        tool = {"name": "home/dialog_text", "promised": [
            {"key": "dryRun", "always": True, "nullable": False},
            {"key": "window", "always": True, "nullable": True},
        ]}
        reply = {"success": False, "error": "No window is open.",
                 "dryRun": True, "window": None, "unknownArguments": []}
        self.assertEqual("ok", live.assert_contract(tool, "defaults", {}, reply))
        self.assertEqual([], live.fails)


if __name__ == "__main__":
    unittest.main()
