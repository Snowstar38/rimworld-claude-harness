"""combat.py may not print VERIFIED unless the clock is running. Mock-only.

2026-09-07, turn 18: a wolf survived four separate orders that each printed
VERIFIED. Turn 16: `combat.py flee` printed `FLEE VERIFIED` and the pawn never
moved while a manhunter was on him. Nothing was wrong with the ordering -- the
jobs were on the pawns. The clock was stopped.

Stage 1 pauses on purpose (`_live_snapshot(pause=True)`), so in a combat
session the honest word is almost always QUEUED and `advance` is what turns it
into something that happened. This file pins that: the word, the remedy, and
the fact that the clock verdict is taken from the snapshot already in hand
rather than from a second bridge call.
"""
import io
import unittest
from unittest import mock

import clock
import combat


def snapshot(paused=True, tick=100):
    return {"status": "game_loaded",
            "time": {"ticksGame": tick, "paused": paused,
                     "forcePaused": False, "timeSpeed": "Normal"},
            "colonists": [{"thingId": "p1", "name": "Lucas", "drafted": True}],
            "threats": {"hostileCount": 0, "hostiles": []}}


class HeadlineTests(unittest.TestCase):
    def headline(self, kind, detail, paused=True):
        with mock.patch.object(combat.rim, "game",
                               side_effect=AssertionError("touched the game")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            state = combat.order_headline(kind, detail, snapshot(paused))
        return state, out.getvalue()

    def test_a_paused_clock_prints_queued_and_never_the_word_verified(self):
        _, text = self.headline("MOVE", "Lucas -> 140,152")
        self.assertIn("MOVE QUEUED  Lucas -> 140,152", text)
        self.assertNotIn("VERIFIED", text)

    def test_a_running_clock_keeps_the_verified_wording_exactly(self):
        state, text = self.headline("MOVE", "Lucas -> 140,152", paused=False)
        self.assertTrue(clock.is_running(state))
        self.assertEqual("MOVE VERIFIED  Lucas -> 140,152\n", text)

    def test_the_verdict_costs_no_extra_bridge_call(self):
        # The snapshot was read after the pause and before the order, and an
        # order cannot start the clock -- so a second read would buy nothing
        # and cost a call on the one path that runs mid-firefight.
        with mock.patch.object(combat.clock, "state",
                               wraps=combat.clock.state) as spy, \
             mock.patch.object(combat.rim, "game",
                               side_effect=AssertionError("touched the game")), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            combat.order_headline("MOVE", "Lucas -> 1,1", snapshot())
        self.assertEqual(1, spy.call_count)
        self.assertIsNotNone(spy.call_args.kwargs.get("reader"))

    def run_cli(self, argv, order):
        with mock.patch.object(combat, "inherited_warning", return_value=None), \
             mock.patch.object(combat.rim, "init"), \
             mock.patch.object(combat, "_identity", return_value={"save": "s"}), \
             mock.patch.object(combat, "_live_snapshot",
                               return_value=snapshot()), \
             mock.patch.object(combat, "issue_move", return_value=order), \
             mock.patch.object(combat, "issue_flee", return_value=order), \
             mock.patch.object(combat, "issue_attack", return_value=order), \
             mock.patch.object(combat, "issue_tend", return_value=order), \
             mock.patch.object(combat, "issue_rescue", return_value=order), \
             mock.patch.object(combat, "watch_line"), \
             mock.patch.object(combat.rim, "game",
                               side_effect=AssertionError("touched the game")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = combat.main(argv)
        return code, out.getvalue()

    ORDER = {"pawnId": "p1", "pawnName": "Lucas", "attackerId": "p1",
             "targetId": "Thing_Wolf1", "targetName": "timber wolf",
             "job": "AttackMelee", "target": {}, "pawn": {}, "arrived": False,
             "watch": {}}

    def test_no_combat_command_claims_verified_on_a_paused_clock(self):
        for argv in (["move", "Lucas", "140", "152"],
                     ["flee", "Lucas", "140", "152"],
                     ["attack", "Lucas", "Thing_Wolf1"],
                     ["tend", "Finn", "Octave"],
                     ["rescue", "Finn", "Octave"]):
            with self.subTest(command=argv[0]):
                code, text = self.run_cli(argv, self.ORDER)
                self.assertEqual(0, code, text)
                self.assertNotIn("VERIFIED", text)
                self.assertIn("%s QUEUED" % argv[0].upper(), text)
                self.assertIn("CLOCK IS STOPPED", text)
