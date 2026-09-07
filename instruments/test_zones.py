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


if __name__ == "__main__":
    unittest.main()
