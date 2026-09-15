"""Mock-only tests for grid.py; no running game is contacted.

The grid is visible to the stream, so the thing worth guarding is that nothing
turns it on by accident: a bare `grid.py` must send no write keys at all, and
every write must be a dry run until `--do`.
"""
import io
import unittest
from unittest import mock

import grid


def payload(**kw):
    r = {"success": True, "tool": "home/grid", "enabled": False,
         "before": {"enabled": False, "step": 10, "labels": True,
                    "alpha": 0.35, "color": "white"},
         "after": {"enabled": False, "step": 10, "labels": True,
                   "alpha": 0.35, "color": "white"},
         "wouldBe": {"enabled": False, "step": 10, "labels": True,
                     "alpha": 0.35, "color": "white"},
         "changed": False, "applied": False, "dryRun": True, "clamped": [],
         "patch": {"attempted": True, "installed": True,
                   "target": "RimWorld.MapInterface.MapInterfaceOnGUI_BeforeMainTabs",
                   "owner": "homebridge.grid-overlay",
                   "owners": ["homebridge.grid-overlay"], "error": None},
         "render": {"framesDrawn": 0, "lastDrawnMsAgo": None,
                    "linesLastFrame": 0, "labelsLastFrame": 0,
                    "effectiveStep": 0, "labelStep": 0, "viewRect": None,
                    "errors": 0, "lastError": None},
         "map": {"hasMap": True, "mapName": "Riverbend", "sizeX": 250,
                 "sizeZ": 250},
         "notes": {}, "unknownArguments": []}
    r.update(kw)
    return r


def drawn(**over):
    """A payload with the grid on and a frame actually drawn."""
    on = {"enabled": True, "step": 10, "labels": True, "alpha": 0.35,
          "color": "white"}
    r = payload(enabled=True, before=dict(on, enabled=False), after=on,
                wouldBe=on, changed=True, applied=True, dryRun=False,
                render={"framesDrawn": 412, "lastDrawnMsAgo": 16,
                        "linesLastFrame": 12, "labelsLastFrame": 20,
                        "effectiveStep": 10, "labelStep": 20,
                        "viewRect": {"x": 90, "z": 110, "width": 62,
                                     "height": 34},
                        "errors": 0, "lastError": None})
    r.update(over)
    return r


class GridCliTests(unittest.TestCase):
    def run_cli(self, argv, answer=None):
        calls = []

        def game(tool, args=None, strict=True):
            calls.append((tool, args))
            return answer if answer is not None else payload()

        with mock.patch.object(grid.rim, "game", side_effect=game), \
             mock.patch.object(grid.rim, "init"), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = grid.main(argv)
        return code, out.getvalue(), calls

    def test_a_bare_call_sends_no_write_keys_at_all(self):
        code, text, calls = self.run_cli([])
        self.assertEqual(0, code)
        self.assertEqual("home/grid", calls[0][0])
        self.assertEqual({}, calls[0][1])
        self.assertIn("GRID OFF", text)

    def test_on_is_a_dry_run_until_do(self):
        off = {"enabled": False, "step": 10, "labels": True, "alpha": 0.35,
               "color": "white"}
        code, text, calls = self.run_cli(
            ["on"], payload(wouldBe=dict(off, enabled=True)))
        self.assertEqual(0, code)
        self.assertTrue(calls[0][1]["enabled"])
        self.assertTrue(calls[0][1]["dryRun"])
        self.assertIn("DRY RUN", text)
        self.assertIn("--do", text)

    def test_on_with_do_writes(self):
        code, _, calls = self.run_cli(["on", "--do"], drawn())
        self.assertEqual(0, code)
        self.assertTrue(calls[0][1]["enabled"])
        self.assertFalse(calls[0][1]["dryRun"])

    def test_off_with_do_writes_enabled_false(self):
        code, _, calls = self.run_cli(["off", "--do"])
        self.assertEqual(0, code)
        self.assertIs(False, calls[0][1]["enabled"])
        self.assertFalse(calls[0][1]["dryRun"])

    def test_step_and_labels_and_style_reach_the_tool(self):
        code, _, calls = self.run_cli(
            ["on", "--step", "5", "--no-labels", "--alpha", "0.6",
             "--color", "yellow", "--do"])
        self.assertEqual(0, code)
        args = calls[0][1]
        self.assertEqual(5, args["step"])
        self.assertIs(False, args["labels"])
        self.assertEqual(0.6, args["alpha"])
        self.assertEqual("yellow", args["color"])

    def test_a_restyle_never_sends_enabled(self):
        """--color alone must not toggle the grid on or off."""
        code, _, calls = self.run_cli(["--color", "cyan", "--do"])
        self.assertEqual(0, code)
        self.assertNotIn("enabled", calls[0][1])
        self.assertEqual("cyan", calls[0][1]["color"])

    def test_do_with_nothing_to_set_is_refused_rather_than_sent(self):
        with mock.patch.object(grid.rim, "init"), \
             mock.patch.object(grid.rim, "game",
                               side_effect=AssertionError("called the game")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = grid.main(["--do"])
        self.assertEqual(2, code)
        self.assertIn("nothing to set", out.getvalue())

    def test_a_drawing_grid_reports_frames_and_the_view(self):
        code, text, _ = self.run_cli(["on", "--do"], drawn())
        self.assertEqual(0, code)
        self.assertIn("412 frames", text)
        self.assertIn("12 lines", text)
        self.assertIn("90,110", text)

    def test_an_enabled_grid_that_has_never_drawn_says_so(self):
        code, text, _ = self.run_cli(
            ["on", "--do"], drawn(render={"framesDrawn": 0,
                                          "lastDrawnMsAgo": None,
                                          "linesLastFrame": 0,
                                          "labelsLastFrame": 0,
                                          "effectiveStep": 0, "labelStep": 0,
                                          "viewRect": None, "errors": 0,
                                          "lastError": None}))
        self.assertEqual(0, code)
        self.assertIn("no frame yet", text)

    def test_a_missing_draw_hook_is_shouted_about(self):
        code, text, _ = self.run_cli(
            ["on", "--do"],
            drawn(patch={"attempted": True, "installed": False,
                         "target": None, "owner": "homebridge.grid-overlay",
                         "owners": [],
                         "error": "MissingMethodException: MapInterfaceOnGUI_BeforeMainTabs()"}))
        self.assertEqual(0, code)
        self.assertIn("draw hook is NOT installed", text)
        self.assertIn("MissingMethodException", text)

    def test_a_clamped_step_is_printed_rather_than_swallowed(self):
        code, text, _ = self.run_cli(
            ["on", "--step", "500", "--do"],
            drawn(clamped=[{"field": "step", "asked": 500, "used": 50,
                            "range": "2..50"}]))
        self.assertEqual(0, code)
        self.assertIn("clamped", text)
        self.assertIn("500 -> 50", text)

    def test_draw_errors_are_printed(self):
        code, text, _ = self.run_cli(
            ["on", "--do"],
            drawn(render={"framesDrawn": 9, "lastDrawnMsAgo": 40,
                          "linesLastFrame": 0, "labelsLastFrame": 0,
                          "effectiveStep": 10, "labelStep": 20,
                          "viewRect": {"x": 0, "z": 0, "width": 1,
                                       "height": 1},
                          "errors": 3, "lastError": "NullReferenceException: x"}))
        self.assertEqual(0, code)
        self.assertIn("3 draw errors", text)

    def test_a_refusal_names_its_reason(self):
        code, text, _ = self.run_cli(
            ["--color", "puce", "--do"],
            {"success": False,
             "error": "color puce is not one of white, black, grey"})
        self.assertEqual(1, code)
        self.assertIn("GRID REFUSED", text)
        self.assertIn("puce", text)

    def test_no_map_is_an_answer_not_a_failure(self):
        code, text, _ = self.run_cli(
            [], payload(map={"hasMap": False, "mapName": None, "sizeX": None,
                             "sizeZ": None}))
        self.assertEqual(0, code)
        self.assertIn("none loaded", text)

    def test_an_unknown_flag_is_refused_rather_than_ignored(self):
        with mock.patch.object(grid.rim, "init"), \
             mock.patch.object(grid.rim, "game",
                               side_effect=AssertionError("called the game")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = grid.main(["--thickness", "4"])
        self.assertEqual(2, code)
        self.assertIn("does not take", out.getvalue())

    def test_a_bad_step_value_never_reaches_the_bridge(self):
        with mock.patch.object(grid.rim, "init"), \
             mock.patch.object(grid.rim, "game",
                               side_effect=AssertionError("called the game")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = grid.main(["on", "--step", "wide", "--do"])
        self.assertEqual(2, code)
        self.assertIn("FAILED", out.getvalue())

    def test_help_prints_usage_without_a_bridge(self):
        with mock.patch.object(grid.rim, "init",
                               side_effect=AssertionError("touched the bridge")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(0, grid.main(["--help"]))
        self.assertIn("python grid.py", out.getvalue())

    def test_json_prints_the_payload_and_nothing_else(self):
        code, text, _ = self.run_cli(["--json"])
        self.assertEqual(0, code)
        self.assertIn('"home/grid"', text)
        self.assertNotIn("GRID OFF", text)


class GridApiTests(unittest.TestCase):
    def call(self, fn, *a, **kw):
        calls = []
        with mock.patch.object(grid.rim, "game",
                               side_effect=lambda t, args=None, strict=True:
                               calls.append((t, args)) or payload()):
            fn(*a, **kw)
        return calls[0][1]

    def test_on_defaults_to_a_dry_run(self):
        self.assertTrue(self.call(grid.on)["dryRun"])

    def test_off_defaults_to_a_dry_run(self):
        self.assertTrue(self.call(grid.off)["dryRun"])

    def test_state_sends_nothing(self):
        self.assertEqual({}, self.call(grid.state))

    def test_none_fields_are_dropped_rather_than_sent_as_null(self):
        args = self.call(grid.on, step=None, color=None, do=True)
        self.assertEqual({"enabled": True, "dryRun": False}, args)


if __name__ == "__main__":
    unittest.main()
