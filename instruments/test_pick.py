"""Offline regressions for pick.py -- the camera-correct click.

The bug these guard: `rimworld/click_cell` returns `success: true` when the
camera is not on the target cell and nothing happened at all (turn 12 of the
2026-09-07 stream), and a cell holding several things hands the click to
whichever one is on top (turn 14).
"""
import io
import unittest
from unittest import mock

import pick


def camera(minx=100, maxx=140, minz=100, maxz=130):
    return {"success": True, "viewRect": {"minX": minx, "maxX": maxx,
                                          "minZ": minz, "maxZ": maxz,
                                          "width": maxx - minx + 1,
                                          "height": maxz - minz + 1}}


def sel(*ids):
    return {"success": True, "hasSelection": bool(ids), "selectedCount": len(ids),
            "selectedObjects": [{"id": i, "kind": "thing", "label": i}
                                for i in ids]}


class ViewTests(unittest.TestCase):
    def test_a_cell_well_inside_the_frame_is_clickable(self):
        self.assertTrue(pick.in_view(120, 115, camera()))

    def test_a_cell_outside_the_frame_is_not(self):
        self.assertFalse(pick.in_view(200, 115, camera()))

    def test_a_cell_on_the_very_edge_is_not(self):
        # The edge of the viewRect is under the architect menu and the bottom
        # bar as often as not, so MARGIN keeps a click off it.
        self.assertFalse(pick.in_view(140, 115, camera()))
        self.assertTrue(pick.in_view(140 - pick.MARGIN, 115, camera()))

    def test_no_viewrect_is_never_read_as_in_frame(self):
        self.assertFalse(pick.in_view(120, 115, {"success": True}))


class EnsureCameraTests(unittest.TestCase):
    def test_a_cell_already_in_frame_does_not_move_the_camera(self):
        with mock.patch.object(pick.cam, "state", return_value=camera()), \
             mock.patch.object(pick.cam, "jump_to") as jump:
            self.assertFalse(pick.ensure_camera(120, 115))
        jump.assert_not_called()

    def test_an_off_screen_cell_moves_the_camera_and_says_so(self):
        out = io.StringIO()
        states = iter([camera(), camera(180, 220, 180, 220)])
        with mock.patch.object(pick.cam, "state", side_effect=lambda: next(states)), \
             mock.patch.object(pick.cam, "jump_to") as jump, \
             mock.patch.object(pick.camlock, "claim"), \
             mock.patch.object(pick.time, "sleep"), \
             mock.patch("sys.stdout", out):
            self.assertTrue(pick.ensure_camera(200, 200))
        jump.assert_called_once_with(200, 200)
        self.assertIn("camera moved to 200,200 for the click", out.getvalue())

    def test_a_camera_that_will_not_go_there_refuses_rather_than_clicking(self):
        states = iter([camera(), camera()])
        with mock.patch.object(pick.cam, "state", side_effect=lambda: next(states)), \
             mock.patch.object(pick.cam, "jump_to"), \
             mock.patch.object(pick.camlock, "claim"), \
             mock.patch.object(pick.time, "sleep"):
            with self.assertRaises(pick.CameraStuck) as caught:
                pick.ensure_camera(200, 200, announce=False)
        self.assertIn("NO CLICK WAS SENT", str(caught.exception))


class SelectThingTests(unittest.TestCase):
    def _run(self, picks, tries=4):
        got = iter([sel(*([p] if p else [])) for p in picks] + [sel()] * 20)
        calls = []

        def game(tool, args=None, **kw):
            calls.append((tool, args))
            if tool == "rimworld/get_selection_semantics":
                return next(got)
            if tool == "rimworld/get_designator_state":
                return {"success": True, "designatorState": {"hasSelection": False}}
            return {"success": True}

        out = io.StringIO()
        error = None
        with mock.patch.object(pick.rim, "game", side_effect=game), \
             mock.patch.object(pick.cam, "state", return_value=camera()), \
             mock.patch.object(pick.time, "sleep"), \
             mock.patch("sys.stdout", out):
            try:
                pick.select_thing("Turret1", 120, 115, label="mini-turret",
                                  tries=tries)
            except pick.ClickMissed as e:
                error = str(e)
        return calls, error, out.getvalue()

    def test_the_first_click_landing_on_the_thing_stops_there(self):
        calls, error, text = self._run(["Turret1"])
        self.assertIsNone(error)
        self.assertEqual(1, len([1 for t, _ in calls if t.endswith("click_cell")]))
        self.assertIn("selected: thing Turret1", text)

    def test_an_item_on_the_tile_is_cycled_past(self):
        calls, error, text = self._run(["Leather900", "Turret1"])
        self.assertIsNone(error)
        self.assertEqual(2, len([1 for t, _ in calls if t.endswith("click_cell")]))

    def test_never_reaching_the_thing_raises_and_names_both(self):
        calls, error, text = self._run(["Leather900"] * 8)
        self.assertIn("CLICK MISSED -- selection is", error)
        self.assertIn("Leather900", error)
        self.assertIn("Turret1", error)

    def test_the_selection_is_cleared_before_the_first_click(self):
        calls, _, _ = self._run(["Turret1"])
        tools = [t for t, _ in calls]
        self.assertLess(tools.index("rimworld/clear_selection"),
                        tools.index("rimworld/click_cell"))


class IdSpellingTests(unittest.TestCase):
    def test_the_thing_prefix_is_not_a_different_thing(self):
        self.assertTrue(pick.holds(pick_sel("Thing_Turret1"), "Turret1"))
        self.assertTrue(pick.holds(pick_sel("Turret1"), "Thing_Turret1"))
        self.assertFalse(pick.holds(pick_sel("Turret2"), "Turret1"))


def pick_sel(*ids):
    with mock.patch.object(pick.rim, "game", return_value=sel(*ids)):
        return pick.selection()


class DesignatorTests(unittest.TestCase):
    def test_nothing_armed_means_nothing_is_cleared(self):
        with mock.patch.object(pick.rim, "game", return_value={
                "success": True, "designatorState": {"hasSelection": False}}):
            self.assertIsNone(pick.clear_designator(announce=False))

    def test_an_unreadable_state_is_not_read_as_nothing_armed(self):
        """A dropped game connection answers in prose, not a payload."""
        prose = "Tool 'rimworld/get_designator_state' not found for game"
        with mock.patch.object(pick.rim, "game", return_value=prose):
            with self.assertRaises(pick.Unreadable):
                pick.armed()
        out = io.StringIO()
        with mock.patch.object(pick.rim, "game", return_value=prose), \
             mock.patch("sys.stdout", out):
            self.assertIsNone(pick.clear_designator())
        self.assertIn("could not read whether a designator is armed",
                      out.getvalue())

    def test_an_armed_designator_is_dropped_and_named(self):
        states = iter([{"success": True, "designatorState": {
                            "hasSelection": True,
                            "selectedDesignator": {"label": "wall"}}},
                       {"success": True, "designatorState": {"hasSelection": False}}])
        out = io.StringIO()
        with mock.patch.object(pick.rim, "game", side_effect=lambda *a, **k: next(states)), \
             mock.patch("act.clear") as clear, \
             mock.patch("sys.stdout", out):
            self.assertEqual("wall", pick.clear_designator())
        clear.assert_called_once()
        self.assertIn("cleared an armed placement designator (wall)", out.getvalue())


if __name__ == "__main__":
    unittest.main()
