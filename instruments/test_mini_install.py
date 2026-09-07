import contextlib
import io
import unittest
from unittest.mock import patch

import mini_install as mini


class MiniInstallTests(unittest.TestCase):
    def run_install(self, *, do=True, kind="RimWorld.MinifiedThing", armed=False,
                    ids=None, to=None, placed=None):
        calls = []
        ids = iter(ids if ids is not None else ["Thing_MinifiedThing42"] * 2)
        # placed: what `home/get_cells_plus` reports at the destination,
        # before and after the placing click.
        placed = iter(placed if placed is not None
                      else [[], [{"isBlueprint": True, "label": "turret (blueprint)"}]])

        def game(tool, args, **kw):
            calls.append((tool, args))
            if tool == "home/get_cells_plus":
                return {"success": True,
                        "cells": [{"x": args["x"], "z": args["z"],
                                   "things": next(placed)}]}
            if tool.endswith("get_map_target_info"):
                return {"success": True, "target": {"className": kind, "spawned": True,
                        "thingId": "Thing_MinifiedThing42", "position": {"x": 3, "z": 4}}}
            if tool.endswith("get_designator_state"):
                return {"success": True, "designatorState": {"hasSelection": armed}}
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
             patch.object(mini.time, "sleep"), \
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
        self.assertIn("INSTALL PLACED", self.printed)

    def test_a_placing_click_that_left_no_blueprint_is_a_failure(self):
        calls, error = self.run_install(to=[120, 140], placed=[[], []])
        self.assertIn("left NO blueprint", error)

    def test_the_destination_dry_run_writes_nothing(self):
        calls, error = self.run_install(do=False, to=[120, 140])
        self.assertIsNone(error)
        self.assertEqual(1, len(calls))
        self.assertIn("DRY RUN - would select", self.printed)
        self.assertEqual([], self.moved)


if __name__ == "__main__":
    unittest.main()
