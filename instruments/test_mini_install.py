import contextlib
import io
import unittest
from unittest.mock import patch

import buildings
import mini_install as mini


class MiniInstallTests(unittest.TestCase):
    def run_install(self, *, do=True, kind="RimWorld.MinifiedThing", armed=False,
                    ids=None, to=None, placed=None, armed_after=None):
        calls = []
        # The designator is read twice in one operation: once BEFORE any click
        # (an armed one refuses outright) and once after a placing click that
        # was not confirmed, where a surviving designator means the game
        # refused the cell. `armed_after` is that second answer.
        designator_reads = []
        ids = iter(ids if ids is not None else ["Thing_MinifiedThing42"] * 2)
        # placed: what `home/list_buildings` reports at the destination, one
        # entry per read -- the first is the "before" snapshot, the rest are
        # the polls after the placing click.
        placed = iter(placed if placed is not None
                      else [[], [{"isBlueprint": True, "label": "turret",
                                  "thingId": "Thing_Blueprint9",
                                  "position": {"x": 120, "z": 140},
                                  "workLeftText": "3"}]])

        def game(tool, args, **kw):
            calls.append((tool, args))
            if tool == "home/list_buildings":
                try:
                    rows = next(placed)
                except StopIteration:
                    rows = []
                return {"success": True, "buildings": rows}
            if tool.endswith("get_map_target_info"):
                return {"success": True, "target": {"className": kind, "spawned": True,
                        "thingId": "Thing_MinifiedThing42", "position": {"x": 3, "z": 4}}}
            if tool.endswith("get_designator_state"):
                designator_reads.append(1)
                held = (armed if len(designator_reads) == 1 or armed_after is None
                        else armed_after)
                return {"success": True, "designatorState": {"hasSelection": held}}
            if tool.endswith("get_selection_semantics"):
                return {"success": True, "selectedCount": 1,
                        "selectedObjects": [{"id": next(ids)}]}
            if tool.endswith("list_selected_gizmos"):
                return {"success": True, "gizmos": [{"id": "g1", "label": "Install"},
                                                     {"id": "g2", "label": "Uninstall"}]}
            return {"success": True}

        error = None
        out = io.StringIO()
        moved = []
        with patch.object(mini.rim, "game", side_effect=game), \
             patch.object(buildings.rim, "game", side_effect=game), \
             patch.object(mini.time, "sleep"), \
             patch.object(buildings.time, "sleep"), \
             patch.object(mini.pick, "ensure_camera",
                          side_effect=lambda x, z, **kw: moved.append((x, z))), \
             contextlib.redirect_stdout(out):
            try:
                mini.install("Thing_MinifiedThing42", do, to)
            except RuntimeError as exc:
                error = str(exc)
        self.moved = moved
        self.printed = out.getvalue()
        return calls, error

    def test_default_is_read_only(self):
        calls, error = self.run_install(do=False)
        self.assertIsNone(error)
        self.assertEqual(1, len(calls))

    def test_installed_building_is_rejected_without_clicks(self):
        calls, error = self.run_install(kind="Verse.Building")
        self.assertIsNotNone(error)
        self.assertEqual(1, len(calls))

    def test_active_designator_prevents_clicks(self):
        calls, error = self.run_install(armed=True)
        self.assertIsNotNone(error)
        self.assertEqual(2, len(calls))

    def test_cycles_then_fires_exact_install(self):
        calls, error = self.run_install(ids=["Thing_Steel1", "Thing_MinifiedThing42",
                                            "Thing_MinifiedThing42"])
        self.assertIsNone(error)
        self.assertEqual(("rimworld/execute_gizmo", {"gizmoId": "g1"}), calls[-1])
        clicks = [args for tool, args in calls if tool.endswith("click_cell")]
        self.assertEqual(3, len(clicks))
        self.assertEqual("right", clicks[0]["button"])

    def test_wrong_object_never_fires(self):
        calls, error = self.run_install(ids=["Thing_Steel1"] * 8)
        self.assertIn("8 clicks", error)
        self.assertFalse(any(t.endswith("execute_gizmo") for t, _ in calls))

    def test_changed_selection_never_fires(self):
        calls, error = self.run_install(ids=["Thing_MinifiedThing42", "Thing_Steel1"])
        self.assertIn("Selection changed", error)
        self.assertFalse(any(t.endswith("execute_gizmo") for t, _ in calls))

    def test_the_camera_moves_onto_the_item_before_the_selecting_click(self):
        self.run_install()
        self.assertEqual([(3, 4)], self.moved)

    def test_two_camera_positions_in_one_operation(self):
        """Turn 13: the item's own cell to select, the destination to place."""
        calls, error = self.run_install(to=[120, 140])
        self.assertIsNone(error)
        self.assertEqual([(3, 4), (120, 140)], self.moved)
        clicks = [args for tool, args in calls if tool.endswith("click_cell")]
        self.assertEqual({"x": 120, "z": 140}, clicks[-1])
        self.assertIn("PLACED -- CONFIRMED", self.printed)

    def test_a_blueprint_the_first_read_missed_is_still_confirmed(self):
        """WEIRD 12/17/35/43/59: the click returns before the game has spawned
        the blueprint, so one read straight after it saw an empty cell."""
        calls, error = self.run_install(
            to=[120, 140],
            placed=[[], [], [], [{"isBlueprint": True, "label": "turret",
                                  "thingId": "Thing_Blueprint9",
                                  "position": {"x": 120, "z": 140},
                                  "workLeftText": "3"}]])
        self.assertIsNone(error)
        self.assertIn("PLACED -- CONFIRMED", self.printed)
        self.assertIn("Seen on read 3 of 4", self.printed)

    def test_nothing_seen_never_says_the_cell_is_empty(self):
        calls, error = self.run_install(to=[120, 140], placed=[[], [], [], [], []])
        self.assertIn("not confirmed", error)
        self.assertIn("NOT VISIBLE YET", self.printed)
        self.assertIn("--near 120 140 2 --every", self.printed)
        self.assertNotIn("the cell reads empty", self.printed)
        # Nothing armed afterwards: this really is "not seen", so the refusal
        # sentence must NOT appear.
        self.assertNotIn("REFUSED BY THE GAME", self.printed)

    def test_a_surviving_designator_is_named_as_a_refusal_and_cleared(self):
        # Live 2026-09-12: a 3x3 holding platform sent to a cell whose footprint
        # overlapped a blueprint. The game refused the click and the old code
        # called it "not visible yet" while leaving the designator armed.
        cleared = []
        with patch.object(mini, "drop_designator",
                          side_effect=lambda: cleared.append(True)):
            calls, error = self.run_install(to=[120, 140], armed=False,
                                            armed_after=True,
                                            placed=[[], [], [], [], []])
        self.assertIn("not confirmed", error)
        self.assertIn("REFUSED BY THE GAME", self.printed)
        self.assertIn("STILL ARMED", self.printed)
        self.assertIn("build.py <def> 120 140", self.printed)
        self.assertEqual([True], cleared)

    def test_something_else_at_the_cell_gets_its_own_sentence(self):
        wall = [{"defName": "Wall", "label": "sandstone wall",
                 "thingId": "Thing_Wall3", "status": "built",
                 "position": {"x": 120, "z": 140}}]
        calls, error = self.run_install(to=[120, 140],
                                        placed=[wall, wall, wall, wall, wall])
        self.assertIn("not confirmed", error)
        self.assertIn("NOT PLACED -- SOMETHING ELSE IS THERE", self.printed)
        self.assertIn("sandstone wall", self.printed)
        self.assertNotIn("NOT VISIBLE YET", self.printed)

    def test_the_destination_dry_run_writes_nothing(self):
        calls, error = self.run_install(do=False, to=[120, 140])
        self.assertIsNone(error)
        self.assertEqual(1, len(calls))
        self.assertIn("DRY RUN - would select", self.printed)
        self.assertEqual([], self.moved)


if __name__ == "__main__":
    unittest.main()
