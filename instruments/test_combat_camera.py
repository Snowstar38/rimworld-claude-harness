import unittest
from unittest import mock

import combat_camera


def pawn(pid, x, z, drafted=True):
    return {"thingId": pid, "position": {"x": x, "z": z},
            "drafted": drafted}


class CombatBoundsTests(unittest.TestCase):
    def test_participants_combine_orders_obligations_and_original_draftees(self):
        ledger = {"orders": {"A": {}}, "draftObligations": {"B": {}},
                  "pawns": {"C": {"originalDrafted": True},
                            "D": {"originalDrafted": False}}}
        self.assertEqual(["A", "B", "C"],
                         combat_camera.participant_ids(ledger))

    def test_bounds_include_drafted_participants_and_only_near_hostiles(self):
        snap = {"colonists": [pawn("A", 10, 10), pawn("B", 20, 15),
                               pawn("C", 50, 50, False)],
                "threats": {"hostiles": [pawn("near", 29, 15),
                                           pawn("far", 80, 80)]}}
        self.assertEqual({"minX": 10, "maxX": 29, "minZ": 10, "maxZ": 15},
                         combat_camera.combat_bounds(snap, ["A", "B", "C"]))

    def test_no_drafted_participant_means_no_camera_move(self):
        snap = {"colonists": [pawn("A", 10, 10, False)], "threats": {}}
        self.assertIsNone(combat_camera.combat_bounds(snap, ["A"]))


class DirectorTests(unittest.TestCase):
    def test_root_size_uses_measured_compact_zoom_under_strict_span(self):
        director = combat_camera.CombatCameraDirector(["A"])
        self.assertEqual(combat_camera.COMPACT_ROOT_SIZE, director._root_size(
            {"minX": 10, "maxX": 11, "minZ": 10, "maxZ": 11}))

    def test_span_of_exactly_fifteen_uses_measured_wide_zoom(self):
        director = combat_camera.CombatCameraDirector(["A"])
        self.assertEqual(combat_camera.WIDE_ROOT_SIZE, director._root_size(
            {"minX": 0, "maxX": 15, "minZ": 0, "maxZ": 1}))

    def test_large_bounds_can_zoom_farther_than_wide_floor(self):
        director = combat_camera.CombatCameraDirector(["A"])
        self.assertGreater(director._root_size(
            {"minX": 0, "maxX": 300, "minZ": 0, "maxZ": 10}),
            combat_camera.WIDE_ROOT_SIZE)

    @mock.patch.object(combat_camera.camlock, "interrupted", return_value=(False, None))
    def test_manual_camera_change_relinquishes_without_framing(
            self, _interrupted):
        states = iter([{"x": 1, "z": 1, "rootSize": 20},
                       {"x": 3, "z": 1, "rootSize": 20}])
        framed = []
        director = combat_camera.CombatCameraDirector(
            ["A"], interval=.01, camera_reader=lambda: next(states),
            snapshot_reader=lambda: {}, framer=framed.append).start()
        director._thread.join(1)
        director.stop()
        self.assertTrue(director.relinquished)
        self.assertTrue(director.manual_override)
        self.assertEqual([], framed)


if __name__ == "__main__":
    unittest.main()
