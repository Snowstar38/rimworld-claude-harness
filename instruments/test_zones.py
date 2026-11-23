"""Mock-only tests for zones.py; no running game is contacted.

`crop` is a WRITE, so the thing worth asserting is that it is a dry run until
`--do` is typed, and that a zone named by a cell is resolved to a zone id
rather than sent to the tool as a coordinate.
"""
import io
import unittest
from unittest import mock

import zones


def crop_reply(dry=True, changed=True, **kw):
    r = {"success": True, "tool": "home/zone_cells", "op": "crop",
         "dryRun": dry, "changed": changed,
         "zone": {"label": "Growing zone 1", "id": 3, "type": "Zone_Growing"},
         "before": {"plantDef": None, "plantLabel": None,
                    "plantDefExplicitlySet": False, "allowSow": True},
         "after": {"plantDef": "Plant_Potato", "plantLabel": "potato plant",
                   "plantDefExplicitlySet": True, "allowSow": True},
         "cells": [], "changes": ["Set the crop of \"Growing zone 1\" to "
                                  "Plant_Potato (it had none set)."],
         "zonesRemoved": [], "presetDefinition": {}, "filter": None,
         "watch": {"shown": False, "reason": "dry run"}}
    r.update(kw)
    return r


ZONE_LIST = {"success": True, "zoneCount": 1, "zones": [
    {"label": "Growing zone 1", "id": 3, "type": "Zone_Growing",
     "cells": [{"x": 118, "z": 133}, {"x": 119, "z": 133}],
     "gridCells": [{"x": 118, "z": 133}]}]}


class ZonesCropTests(unittest.TestCase):
    def run_cli(self, argv, answer=None):
        calls = []

        def game(tool, args=None, strict=True):
            calls.append((tool, args))
            if tool == "home/list_zones":
                return ZONE_LIST
            return answer if answer is not None else crop_reply()

        with mock.patch.object(zones.rim, "game", side_effect=game), \
             mock.patch.object(zones.rim, "init"), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = zones.main(argv)
        return code, out.getvalue(), calls

    def test_crop_is_a_dry_run_unless_do_is_typed(self):
        code, text, calls = self.run_cli(
            ["crop", "Growing zone 1", "Plant_Potato"])
        self.assertIn(code, (0, None))
        sent = [a for t, a in calls if t == "home/zone_cells"][0]
        self.assertEqual("crop", sent["op"])
        self.assertTrue(sent["dryRun"])
        self.assertEqual("Plant_Potato", sent["plant"])
        self.assertIn("DRY RUN", text)

    def test_do_sends_the_write(self):
        code, text, calls = self.run_cli(
            ["crop", "Growing zone 1", "Plant_Potato", "--do"],
            crop_reply(dry=False))
        sent = [a for t, a in calls if t == "home/zone_cells"][0]
        self.assertFalse(sent["dryRun"])
        self.assertIn("CROP DONE", text)

    def test_a_cell_names_the_zone_that_holds_it(self):
        code, _, calls = self.run_cli(["crop", "118,133", "Plant_Rice"])
        sent = [a for t, a in calls if t == "home/zone_cells"][0]
        self.assertEqual("3", sent["zone"])

    def test_a_cell_no_zone_holds_is_refused_before_any_write(self):
        with self.assertRaises(SystemExit):
            self.run_cli(["crop", "9,9", "Plant_Rice"])

    def test_the_before_and_after_crop_are_both_printed(self):
        code, text, _ = self.run_cli(
            ["crop", "Growing zone 1", "Plant_Potato"])
        self.assertIn("(no crop set)", text)
        self.assertIn("Plant_Potato", text)

    def test_an_unchanged_crop_says_so(self):
        code, text, _ = self.run_cli(
            ["crop", "Growing zone 1", "Plant_Potato"],
            crop_reply(changed=False))
        self.assertIn("unchanged", text)

    def test_a_refusal_exits_non_zero_and_names_the_reason(self):
        code, text, _ = self.run_cli(
            ["crop", "Stockpile 1", "Plant_Potato"],
            {"success": False, "op": "crop",
             "error": "op=crop needs a growing zone."})
        self.assertEqual(1, code)
        self.assertIn("CROP REFUSED", text)
        self.assertIn("needs a growing zone", text)

    def test_crop_with_no_plant_is_refused_before_the_bridge(self):
        with self.assertRaises(SystemExit):
            self.run_cli(["crop", "Growing zone 1"])


class ZonePositionalTests(unittest.TestCase):
    """2026-09-07: `zones.py "Growing zone 1"` printed the whole help text."""

    def run_cli(self, argv):
        def game(tool, args=None, strict=True):
            if tool != "home/list_zones":
                return {}
            # The tool's own `match` is a label substring; honour it here so
            # "nothing matched" is a real state in this fixture.
            want = (args or {}).get("match")
            rows = [z for z in ZONE_LIST["zones"]
                    if not want or want.lower() in (z["label"] or "").lower()]
            return dict(ZONE_LIST, zones=rows, zoneCount=len(rows))

        with mock.patch.object(zones.rim, "game", side_effect=game),              mock.patch.object(zones.rim, "init"),              mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = zones.main(argv)
        return code, out.getvalue()

    def test_a_label_shows_the_zone_not_the_help(self):
        code, text = self.run_cli(["Growing zone 1"])
        self.assertEqual(0, code)
        self.assertIn("Growing zone 1", text)
        self.assertNotIn("python zones.py repair", text)

    def test_a_bare_id_shows_that_zone(self):
        code, text = self.run_cli(["3"])
        self.assertEqual(0, code)
        self.assertIn("id 3", text)

    def test_a_name_nothing_matches_names_the_zones_that_exist(self):
        code, text = self.run_cli(["Nowhere"])
        self.assertEqual(1, code)
        self.assertIn("no zone matches", text)
        self.assertIn("Growing zone 1", text)


def zone(label, id, cells, listed=None):
    return {"label": label, "id": id, "type": "Zone_Stockpile",
            "listedCellCount": len(cells) if listed is None else listed,
            "gridCellCount": len(cells) if listed is None else listed,
            "cells": [{"x": x, "z": z} for x, z in cells],
            "gridCells": [{"x": x, "z": z} for x, z in cells]}


CREATED = {"success": True, "tool": "home/zone_cells", "op": "create",
           "dryRun": True, "changed": True,
           "zone": {"label": "Pantry", "id": None, "type": "Zone_Stockpile"},
           "cells": [], "changes": [], "zonesRemoved": [], "presetDefinition": {},
           "filter": None, "watch": {"shown": False, "reason": "dry run"}}


class CreateOverlapTests(unittest.TestCase):
    """`create` took cells off a standing zone without saying so."""

    def run_cli(self, argv, zones_on_map):
        calls = []

        def game(tool, args=None, strict=True):
            calls.append((tool, args))
            if tool == "home/list_zones":
                return {"success": True, "zoneCount": len(zones_on_map),
                        "zones": zones_on_map}
            return CREATED

        with mock.patch.object(zones.rim, "game", side_effect=game), \
             mock.patch.object(zones.rim, "init"), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = zones.main(argv)
        return code, out.getvalue(), calls

    def test_an_overlap_refuses_and_writes_nothing(self):
        existing = zone("Pantry", 4, [(113, 140), (114, 140)], listed=8)
        code, text, calls = self.run_cli(
            ["create", "stockpile", "Meals", "113", "140", "2", "2"], [existing])
        self.assertEqual(1, code)
        self.assertIn("CREATE REFUSED", text)
        self.assertIn("Pantry (id 4): 2 cell(s)", text)
        self.assertIn("--merge", text)
        self.assertEqual([], [t for t, _ in calls if t == "home/zone_cells"])

    def test_merge_takes_them_and_says_which(self):
        existing = zone("Pantry", 4, [(113, 140), (114, 140)], listed=8)
        code, text, calls = self.run_cli(
            ["create", "stockpile", "Meals", "113", "140", "2", "2", "--merge"],
            [existing])
        self.assertIn(code, (0, None))
        self.assertIn("TAKING 2 cell(s)", text)
        self.assertIn("it keeps 6 cell(s)", text)
        self.assertEqual(1, len([t for t, _ in calls if t == "home/zone_cells"]))

    def test_emptying_a_zone_needs_the_louder_flag(self):
        existing = zone("Pantry", 4, [(113, 140), (114, 140)])
        code, text, _ = self.run_cli(
            ["create", "stockpile", "Meals", "113", "140", "2", "2", "--merge"],
            [existing])
        self.assertEqual(1, code)
        self.assertIn("that is EVERY cell it has", text)
        self.assertIn("--replace", text)

    def test_replace_goes_through(self):
        existing = zone("Pantry", 4, [(113, 140), (114, 140)])
        code, text, calls = self.run_cli(
            ["create", "stockpile", "Meals", "113", "140", "2", "2", "--replace"],
            [existing])
        self.assertIn(code, (0, None))
        self.assertEqual(1, len([t for t, _ in calls if t == "home/zone_cells"]))

    def test_no_overlap_asks_for_no_flag(self):
        existing = zone("Pantry", 4, [(200, 200)])
        code, text, calls = self.run_cli(
            ["create", "stockpile", "Meals", "113", "140", "2", "2"], [existing])
        self.assertIn(code, (0, None))
        self.assertNotIn("REFUSED", text)
        self.assertEqual(1, len([t for t, _ in calls if t == "home/zone_cells"]))


class UsageInsteadOfATracebackTests(unittest.TestCase):
    """`zones.py create` with no arguments died on an IndexError."""

    def usage(self, argv):
        with mock.patch.object(zones.rim, "init"), \
             mock.patch.object(zones.rim, "game") as game, \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as caught:
                zones.main(argv)
        game.assert_not_called()
        return str(caught.exception)

    def test_create_with_no_arguments_prints_its_usage(self):
        self.assertIn("zones.py create <stockpile|growing>", self.usage(["create"]))

    def test_create_with_a_type_and_no_label_prints_its_usage(self):
        self.assertIn("zones.py create", self.usage(["create", "stockpile"]))

    def test_add_and_delete_say_what_they_need(self):
        self.assertIn("zones.py add", self.usage(["add"]))
        self.assertIn("zones.py delete", self.usage(["delete"]))


if __name__ == "__main__":
    unittest.main()
