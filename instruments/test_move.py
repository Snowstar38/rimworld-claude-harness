"""Deterministic tests for move order issue/wait separation.

All bridge, camera, and time functions are mocked; this file never contacts a
running RimWorld session.
"""
import unittest
from unittest import mock

import letters
import move


def pawn(drafted=False, position=(10, 20), job="Wait_Combat", **changes):
    row = {
        "pawnId": "Pawn_1",
        "name": "Lucas",
        "dead": False,
        "downed": False,
        "drafted": drafted,
        "mentalState": None,
        "position": {"x": position[0], "z": position[1]},
        "job": job,
    }
    row.update(changes)
    return row


class MoveIssueTests(unittest.TestCase):
    def setUp(self):
        self.patches = [
            mock.patch.object(move, "blocking_window", return_value=None),
            mock.patch.object(move.camlock, "claim"),
            mock.patch.object(move.time, "sleep"),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _game(self, before, after, click=None):
        colonists = iter((before, after))

        def call(tool, args, strict=True):
            if tool == "rimworld/get_cell_info":
                return {"cell": {"walkable": True}}
            if tool == "rimworld/list_colonists":
                return {"colonists": [next(colonists)]}
            if tool == "rimworld/set_draft":
                return {"drafted": True}
            if tool == "rimworld/right_click_cell":
                return click or {"actionKind": "direct"}
            return {"success": True}

        return mock.patch.object(move.rim, "game", side_effect=call)

    def test_issue_returns_metadata_and_never_advances_time(self):
        before = pawn(drafted=False)
        after = pawn(drafted=True, job="Goto")
        ledger = mock.Mock()
        with self._game(before, after), mock.patch.object(move.run, "until") as wait:
            result = move.issue_goto("Pawn_1", 15, 25, before_draft=ledger)

        self.assertTrue(result["accepted"])
        self.assertTrue(result["autoDrafted"])
        self.assertFalse(result["arrived"])
        self.assertEqual(result["destination"], {"x": 15, "z": 25})
        self.assertEqual(result["delivery"]["actionKind"], "direct")
        ledger.assert_called_once_with(before)
        wait.assert_not_called()

    def test_issue_returns_camera_to_pawn_at_combat_zoom(self):
        before = pawn(drafted=True, position=(10, 20))
        after = pawn(drafted=True, position=(10, 20), job="Goto")
        with self._game(before, after) as game:
            move.issue_goto("Pawn_1", 80, 90)
        game.assert_any_call("rimworld/set_camera_zoom", {"rootSize": 24},
                             strict=False)
        self.assertEqual(
            mock.call("rimworld/jump_camera_to_cell", {"x": 10, "z": 20},
                      strict=False),
            [c for c in game.call_args_list
            if c.args and c.args[0] == "rimworld/jump_camera_to_cell"][-1])

    def test_each_camera_view_dwells_half_a_second(self):
        before = pawn(drafted=True)
        after = pawn(drafted=True, job="Goto")
        with self._game(before, after), \
                mock.patch.object(move.time, "sleep") as sleep:
            move.issue_goto("Pawn_1", 80, 90)
        self.assertEqual([mock.call(0.5), mock.call(0.5)],
                         sleep.call_args_list)

    def test_already_drafted_skips_write_ahead_hook(self):
        before = pawn(drafted=True)
        after = pawn(drafted=True, job="Goto")
        ledger = mock.Mock()
        with self._game(before, after):
            result = move.issue_goto("Pawn_1", 15, 25, before_draft=ledger)
        self.assertFalse(result["autoDrafted"])
        ledger.assert_not_called()

    def test_unverified_order_is_rejected(self):
        before = pawn(drafted=True)
        after = pawn(drafted=True, job="Wait_Combat")
        with self._game(before, after):
            with self.assertRaisesRegex(RuntimeError, "did not show a Goto"):
                move.issue_goto("Pawn_1", 15, 25)

    def test_occupied_cell_executes_enabled_go_here(self):
        before = pawn(drafted=True)
        after = pawn(drafted=True, job="Goto")
        click = {"actionKind": "menu_opened", "options": [
            {"index": 3, "label": "Go here", "disabled": False},
            {"index": 4, "label": "Consume meal", "disabled": False},
        ]}
        with self._game(before, after, click=click) as game:
            move.issue_goto("Pawn_1", 15, 25)
        game.assert_any_call("rimworld/execute_context_menu_option",
                             {"optionIndex": 3})

    def test_goto_uses_issue_then_waits_for_arrival(self):
        issued = {"accepted": True}
        with mock.patch.object(move, "issue_goto", return_value=issued) as issue, \
                mock.patch.object(move.run, "until", return_value=("done", None)) as wait, \
                mock.patch.object(move, "pos", return_value={"x": 15, "z": 25}):
            position, reason = move.goto("Pawn_1", 15, 25, seconds=4,
                                         zoom=9, speed="Fast")
        issue.assert_called_once_with("Pawn_1", 15, 25, zoom=9)
        wait.assert_called_once()
        self.assertEqual((position, reason), ({"x": 15, "z": 25}, "done"))

    def test_unknown_modal_state_refuses_before_any_game_call(self):
        with mock.patch.object(move, "blocking_window", return_value=letters.UNKNOWN), \
                mock.patch.object(move.rim, "game") as game:
            with self.assertRaisesRegex(RuntimeError, "UNKNOWN"):
                move.issue_goto("Pawn_1", 15, 25)
        game.assert_not_called()


if __name__ == "__main__":
    unittest.main()
