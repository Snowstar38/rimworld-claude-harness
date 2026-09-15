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

class ClockLeftPausedTests(unittest.TestCase):
    """WEIRD 10: every combat.py call pauses as stage 1, and nothing said so.
    An order followed by `play.py start` left the game stopped with both
    programs reporting success."""

    def test_the_last_line_names_the_pause_and_both_ways_back(self):
        lines = combat.clock_left_paused_lines([combat.STAGE_ONE_PAUSE])
        self.assertIn("CLOCK LEFT PAUSED by combat.py -- stage 1 pauses before "
                      "it issues any order, by design.", lines[0])
        self.assertIn("python combat.py advance 20", "\n".join(lines))
        self.assertIn("python play.py start", "\n".join(lines))

    def test_a_supervised_session_it_ended_is_named(self):
        lines = combat.clock_left_paused_lines([combat.STAGE_ONE_PAUSE],
                                               supervised={"epoch": 42})
        self.assertIn("supervised play (epoch 42) was running and this pause "
                      "ended it.", "\n".join(lines))

    def test_nothing_is_said_when_the_clock_was_never_touched(self):
        self.assertEqual([], combat.clock_left_paused_lines([]))

    def test_a_stage_one_pause_is_recorded_once(self):
        combat._CLOCK_PAUSED_BY[:] = []
        with mock.patch.object(combat, "supervised_service_row", return_value=None), \
             mock.patch.object(combat.rim, "game", return_value={"paused": True}), \
             mock.patch.object(combat.colony_status, "read", return_value=snapshot()):
            combat._live_snapshot(pause=True)
            combat._live_snapshot(pause=True)
        self.assertEqual([combat.STAGE_ONE_PAUSE], combat._CLOCK_PAUSED_BY)
        combat._CLOCK_PAUSED_BY[:] = []


class PendingDecisionTests(unittest.TestCase):
    """WEIRD 19: `flee` returned only its advice line twice and `advance` then
    refused for a pawn nobody had re-ordered, because the bear was out of his
    weapon's range."""

    LEDGER = {"pawns": {"Human1": {"name": "Lucas"}},
              "pendingDecisions": {"Human1": {
                  "reason": "attack failed",
                  "error": "target is outside Lucas's weapon range"}}}

    def test_each_decision_names_the_pawn_and_the_order_that_clears_it(self):
        lines = combat.pending_decision_lines(self.LEDGER)
        text = "\n".join(lines)
        self.assertIn("Lucas (attack failed): order Lucas again", text)
        self.assertIn("python combat.py move Human1 <x> <z>", text)
        self.assertIn("python combat.py flee Human1 <x> <z>", text)
        self.assertIn("python combat.py attack Human1 <target>", text)

    def test_out_of_range_offers_the_two_concrete_moves(self):
        text = "\n".join(combat.pending_decision_lines(self.LEDGER))
        self.assertIn("out of range is a POSITION problem", text)
        self.assertIn("--mode melee", text)

    def test_no_decisions_and_no_ledger_produce_nothing(self):
        self.assertEqual([], combat.pending_decision_lines({}))
        self.assertEqual([], combat.pending_decision_lines(None))

    def test_the_advance_refusal_carries_the_clearing_commands(self):
        with mock.patch.object(combat, "load_ledger",
                               return_value=dict(self.LEDGER, active=True)), \
             mock.patch.object(combat, "stale_reason", return_value=None), \
             mock.patch.object(combat, "_reconcile_flee_orders", return_value=[]), \
             mock.patch.object(combat, "refuse_if_supervised"), \
             self.assertRaises(combat.CombatRefusal) as raised:
            combat.advance(5, snapshot(), {"save": "s"})
        message = str(raised.exception)
        self.assertIn("tactical decision required for Lucas", message)
        self.assertIn("no time runs until each of them has a new order", message)
        self.assertIn("python combat.py move Human1 <x> <z>", message)

    def test_flee_says_the_retreat_is_queued_and_what_blocks_it(self):
        text = "\n".join(combat.flee_next_step_lines(self.LEDGER, 5))
        self.assertIn("the retreat is QUEUED on the pawn", text)
        self.assertIn("python combat.py advance 5", text)
        self.assertIn("--advance 5", text)
        self.assertIn("THE NEXT ADVANCE WILL REFUSE FIRST -- 1 pawn(s)", text)
        self.assertIn("order Lucas again", text)

    def test_a_clear_ledger_leaves_flee_with_only_the_next_command(self):
        text = "\n".join(combat.flee_next_step_lines({"pendingDecisions": {}}))
        self.assertIn("python combat.py advance 20", text)
        self.assertNotIn("WILL REFUSE FIRST", text)


class FleeCliTests(unittest.TestCase):
    ORDER = {"pawnId": "Human1", "pawnName": "Lucas", "arrived": False}

    def run_flee(self, argv, ledger, advance=None):
        with mock.patch.object(combat, "inherited_warning", return_value=None), \
             mock.patch.object(combat.rim, "init"), \
             mock.patch.object(combat, "_identity", return_value={"save": "s"}), \
             mock.patch.object(combat, "supervised_service_row",
                               return_value={"epoch": 42}), \
             mock.patch.object(combat.rim, "game", return_value={"paused": True}), \
             mock.patch.object(combat.colony_status, "read",
                               return_value=snapshot()), \
             mock.patch.object(combat, "issue_flee", return_value=self.ORDER), \
             mock.patch.object(combat, "load_ledger", return_value=ledger), \
             mock.patch.object(combat, "advance",
                               side_effect=advance or AssertionError("advanced")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = combat.main(argv)
        return code, out.getvalue()

    def test_flee_prints_the_queued_retreat_and_the_paused_clock(self):
        code, text = self.run_flee(["flee", "Lucas", "140", "152"],
                                   {"pendingDecisions": {}})
        self.assertEqual(0, code, text)
        self.assertIn("FLEE QUEUED", text)
        self.assertIn("the retreat is QUEUED on the pawn", text)
        self.assertIn("CLOCK LEFT PAUSED by combat.py", text)
        self.assertIn("supervised play (epoch 42) was running", text)

    def test_flee_advance_runs_the_pulse_in_the_same_call(self):
        code, text = self.run_flee(
            ["flee", "Lucas", "140", "152", "--advance", "5"],
            {"pendingDecisions": {}},
            advance=lambda *a, **k: {"stopReason": "budget_elapsed"})
        self.assertEqual(0, code, text)
        self.assertIn("COMBAT STOP  budget_elapsed", text)

    def test_a_refused_advance_leaves_the_flee_order_standing(self):
        def refuse(*_a, **_k):
            raise combat.CombatRefusal("tactical decision required for Octave")
        code, text = self.run_flee(
            ["flee", "Lucas", "140", "152", "--advance", "5"],
            {"pendingDecisions": {}}, advance=refuse)
        self.assertEqual(0, code, text)
        self.assertIn("FLEE ORDER STANDS -- the advance was refused", text)
        self.assertIn("python combat.py advance 5", text)
