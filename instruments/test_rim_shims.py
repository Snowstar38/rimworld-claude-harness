import unittest
from unittest import mock
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import rim


class SetDraftShimTests(unittest.TestCase):
    def call_args(self, args):
        with mock.patch.object(rim, "tool", return_value={"success": True}) as tool:
            rim.game("rimworld/set_draft", args)
        return tool.call_args.args[1]["arguments"]

    def test_plain_pawn_alias_becomes_name(self):
        self.assertEqual({"pawnName": "Finn", "drafted": False},
                         self.call_args({"pawn": "Finn", "drafted": False}))

    def test_stable_pawn_alias_becomes_id(self):
        self.assertEqual({"pawnId": "Pawn_Finn123", "drafted": True},
                         self.call_args({"pawn": "Pawn_Finn123", "drafted": True}))
        self.assertEqual({"pawnId": "123", "drafted": True},
                         self.call_args({"pawn": 123, "drafted": True}))

    def test_explicit_schema_fields_pass_unchanged(self):
        self.assertEqual({"pawnName": "Finn", "drafted": False},
                         self.call_args({"pawnName": "Finn", "drafted": False}))

    def test_alias_and_explicit_selector_conflict(self):
        with self.assertRaisesRegex(rim.BridgeError, "pass exactly one pawn selector"):
            rim.game("rimworld/set_draft", {"pawn": "Finn", "pawnName": "Finn"})


class ParseSizeTests(unittest.TestCase):
    """Every shape a bridge has been seen to spell the map size in."""

    def test_object_with_x_and_z(self):
        self.assertEqual((250, 250),
                         rim.parse_size({"x": 250, "z": 250, "cells": 62500}))

    def test_object_with_width_and_height(self):
        self.assertEqual((275, 250), rim.parse_size({"width": 275,
                                                     "height": 250}))

    def test_bare_number_is_a_square_map(self):
        self.assertEqual((250, 250), rim.parse_size(250))

    def test_list_pair(self):
        self.assertEqual((250, 275), rim.parse_size([250, 275]))

    def test_string_forms(self):
        self.assertEqual((250, 275), rim.parse_size("250x275"))
        self.assertEqual((250, 250), rim.parse_size("(250, 250)"))
        self.assertEqual((250, 250), rim.parse_size("250"))

    def test_nothing_parseable_is_none_not_a_guess(self):
        for v in (None, True, "", {}, [], {"foo": 1}, 0):
            self.assertIsNone(rim.parse_size(v), repr(v))

    def test_a_rect_echo_is_never_read_as_the_map_size(self):
        # The top level of a get_cells_plus reply carries the requested rect's
        # own x/z. Only a named size key counts.
        self.assertIsNone(rim.size_in_reply({"x": 0, "z": 0, "width": 1,
                                             "height": 1}))

    def test_nested_under_map(self):
        self.assertEqual((250, 250),
                         rim.size_in_reply({"map": {"size": {"x": 250,
                                                             "z": 250}}}))


class MapSizeTests(unittest.TestCase):
    def setUp(self):
        rim._size[:] = []

    tearDown = setUp

    def test_one_call_when_get_cells_plus_reports_mapsize(self):
        calls = []

        def fake(name, args=None, strict=True, timeout=600):
            calls.append(name)
            return {"success": True, "x": 0, "z": 0,
                    "mapSize": {"x": 250, "z": 275, "cells": 68750}}

        with mock.patch.object(rim, "game", side_effect=fake):
            self.assertEqual((250, 275), rim.map_size())
        self.assertEqual(["home/get_cells_plus"], calls)

    def test_the_answer_is_cached(self):
        with mock.patch.object(rim, "game", return_value={
                "mapSize": {"x": 250, "z": 250}}) as g:
            rim.map_size()
            rim.map_size()
        self.assertEqual(1, g.call_count)

    def test_a_string_reply_falls_back_to_the_probe(self):
        # A companion that is not loaded answers in prose, not a payload.
        def fake(name, args=None, strict=True, timeout=600):
            if name == "home/get_cells_plus":
                return "Tool not found: home/get_cells_plus"
            return {"success": args["x"] < 250 and args["z"] < 250}

        with mock.patch.object(rim, "game", side_effect=fake):
            self.assertEqual((250, 250), rim.map_size())

    def test_a_string_reply_to_the_probe_raises_naming_the_shape(self):
        def fake(name, args=None, strict=True, timeout=600):
            return "attn_1 requires acknowledgement"

        with mock.patch.object(rim, "game", side_effect=fake):
            with self.assertRaises(rim.BridgeError) as e:
                rim.map_size()
        self.assertIn("str", str(e.exception))
        self.assertIn("get_cell_info", str(e.exception))
        self.assertEqual([], rim._size)

    def test_a_list_reply_to_the_probe_raises_too(self):
        with mock.patch.object(rim, "game", side_effect=lambda *a, **k: [{}, {}]):
            with self.assertRaises(rim.BridgeError):
                rim.map_size()


if __name__ == "__main__":
    unittest.main()
