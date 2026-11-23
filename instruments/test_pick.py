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


def pawn_sel(*ids):
    return {"success": True, "hasSelection": bool(ids), "selectedCount": len(ids),
            "selectedObjects": [{"id": i, "kind": "pawn", "label": i}
                                for i in ids]}


class PawnIdResolutionTests(unittest.TestCase):
    """Live 2026-09-08: every pawn resolved with `thingId` None, so the click
    check compared against a blank -- "expected Ernst [None]". The envelope of
    `get_map_target_info` always names the id; `DescribePawn` never does."""

    ENVELOPE = {"success": True, "kind": "pawn", "label": "Ernst",
                "thingId": "Thing_Human618", "pawnId": "Thing_Human618",
                "position": {"x": 138, "z": 110},
                "target": {"pawnId": "Thing_Human618", "name": "Ernst",
                           "label": "Ernst", "className": "Verse.Pawn",
                           "defName": "Human", "spawned": True,
                           "position": {"x": 138, "z": 110}}}

    def test_a_resolved_pawn_carries_an_id_the_check_can_use(self):
        with mock.patch.object(pick.rim, "game", return_value=self.ENVELOPE):
            target = pick.resolve("Thing_Human618")
        self.assertEqual("Thing_Human618", target["thingId"])
        self.assertEqual("Thing_Human618", target["pawnId"])
        self.assertEqual("pawn", target["kind"])
        self.assertTrue(pick.is_pawn(target))

    def test_a_thing_is_still_a_thing(self):
        envelope = {"success": True, "kind": "thing", "thingId": "Shelf99",
                    "pawnId": None,
                    "target": {"thingId": "Shelf99", "label": "shelf",
                               "className": "Verse.Building"}}
        with mock.patch.object(pick.rim, "game", return_value=envelope):
            target = pick.resolve("Shelf99")
        self.assertEqual("Shelf99", target["thingId"])
        self.assertFalse(pick.is_pawn(target))

    def test_a_pawn_is_resolvable_by_name_not_only_by_id(self):
        """`get_map_target_info {thingId:"Ernst"}` matches nothing; the same
        tool takes pawnName, which is how a person says it."""
        seen = []

        def game(tool, args=None, **kw):
            seen.append(args)
            if args.get("pawnName") == "Ernst":
                return self.ENVELOPE
            return {"success": False, "message": "Could not find thing id"}

        with mock.patch.object(pick.rim, "game", side_effect=game):
            target = pick.resolve_pawn("Ernst")
        self.assertEqual("Thing_Human618", target["thingId"])
        self.assertIn({"pawnId": "Ernst"}, seen)      # id first, name second
        self.assertIn({"pawnName": "Ernst"}, seen)

    def test_a_name_that_is_nobody_resolves_to_nothing(self):
        with mock.patch.object(pick.rim, "game",
                               return_value={"success": False}):
            self.assertIsNone(pick.resolve_pawn("Nobody"))
            self.assertIsNone(pick.resolve_pawn(""))


class SelectPawnTests(unittest.TestCase):
    """Turns 1-14 of 2026-09-08: Samantha stood on Ernst's tile and every read
    of his gizmo bar came back hers. A cell click cannot separate two pawns."""

    def _run(self, selected="Thing_Human618", refuse=False, reply_extra=None):
        calls = []

        def game(tool, args=None, **kw):
            calls.append((tool, args))
            if tool == "rimworld/get_designator_state":
                return {"success": True, "designatorState": {"hasSelection": False}}
            if tool == "rimworld/get_selection_semantics":
                if selected is None:
                    return "Tool not found"
                return pawn_sel(selected)
            if tool == "rimworld/select_pawn":
                if refuse:
                    return {"success": False,
                            "message": "No player-controlled colonist matches"}
                out = {"success": True, "selectedCount": 1}
                out.update(reply_extra or {})
                return out
            return {"success": True}

        out = io.StringIO()
        error = None
        sel = None
        with mock.patch.object(pick.rim, "game", side_effect=game), \
             mock.patch.object(pick.cam, "state", return_value=camera()), \
             mock.patch.object(pick.time, "sleep"), \
             mock.patch("sys.stdout", out):
            try:
                sel = pick.select_pawn("Thing_Human618", label="Ernst")
            except pick.ClickMissed as e:
                error = str(e)
        return calls, error, out.getvalue(), sel

    def test_a_pawn_is_selected_by_id_and_never_clicked(self):
        calls, error, text, sel = self._run()
        self.assertIsNone(error)
        tools = [t for t, _ in calls]
        self.assertIn("rimworld/select_pawn", tools)
        self.assertNotIn("rimworld/click_cell", tools)
        self.assertIn(("rimworld/select_pawn", {"pawnId": "Thing_Human618"}),
                      calls)
        self.assertIn("selected by id, no click", text)

    def test_the_neighbour_on_the_tile_is_refused_not_returned(self):
        calls, error, text, sel = self._run(selected="Thing_Human702")
        self.assertIn("SELECT MISSED", error)
        self.assertIn("Thing_Human702", error)
        self.assertIn("Thing_Human618", error)
        self.assertIn("not by a click", error)

    def test_a_non_colonist_is_refused_naming_why(self):
        calls, error, text, sel = self._run(refuse=True)
        self.assertIn("SELECT REFUSED", error)
        self.assertIn("PLAYER COLONISTS only", error)

    def test_no_id_is_refused_before_any_call(self):
        with mock.patch.object(pick.rim, "game") as game:
            with self.assertRaises(pick.ClickMissed) as caught:
                pick.select_pawn(None, label="Ernst")
        game.assert_not_called()
        self.assertIn("pawnId", str(caught.exception))

    def test_an_unreadable_selection_falls_back_to_select_pawns_own_read(self):
        calls, error, text, sel = self._run(
            selected=None,
            reply_extra={"selected": {"pawnId": "Thing_Human618",
                                      "name": "Ernst"}})
        self.assertIsNone(error)
        self.assertIn("select_pawn's own", text)
        self.assertTrue(pick.holds(sel, "Thing_Human618"))

    def test_select_thing_hands_a_pawn_straight_over(self):
        calls = []

        def game(tool, args=None, **kw):
            calls.append((tool, args))
            if tool == "rimworld/get_designator_state":
                return {"success": True, "designatorState": {"hasSelection": False}}
            if tool == "rimworld/get_selection_semantics":
                return pawn_sel("Thing_Human618")
            return {"success": True}

        out = io.StringIO()
        with mock.patch.object(pick.rim, "game", side_effect=game), \
             mock.patch.object(pick.cam, "state", return_value=camera()), \
             mock.patch.object(pick.time, "sleep"), \
             mock.patch("sys.stdout", out):
            pick.select_thing("Thing_Human618", 138, 110, label="Ernst",
                              kind="pawn")
        tools = [t for t, _ in calls]
        self.assertIn("rimworld/select_pawn", tools)
        self.assertNotIn("rimworld/click_cell", tools)


if __name__ == "__main__":
    unittest.main()
