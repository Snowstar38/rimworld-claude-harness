import io
import unittest
from unittest import mock
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import run


class RunSafetyTests(unittest.TestCase):
    def test_long_request_yields_after_review_slice(self):
        now = [0.0]
        def clock():
            value = now[0]
            now[0] += 0.6
            return value
        calls = []
        def game(name, args=None, strict=True, timeout=600):
            calls.append((name, args))
            if name == run.TOOL:
                return {"stopReason": "budget_elapsed", "endTick": 100,
                        "pausedByThisTool": False}
            return {"success": True, "paused": True}
        with mock.patch.object(run.time, "time", side_effect=clock), \
             mock.patch.object(run, "_letters", return_value=set()), \
             mock.patch.object(run, "_ticks", side_effect=[100, 100]), \
             mock.patch.object(run.rim, "game", side_effect=game):
            reason, _ = run._companion(None, 120, "Superfast", 20, False)
        guard_calls = [args for name, args in calls if name == run.TOOL]
        self.assertEqual("review needed", reason)
        self.assertLessEqual(len(guard_calls), 5)
        self.assertTrue(all(args["maxDurationMs"] <= 1000 for args in guard_calls))

    def test_companion_uses_short_pulses_and_carries_message_watermark(self):
        calls = []
        replies = [
            {"stopReason": "budget_elapsed", "endTick": 123,
             "pausedByThisTool": False},
            {"stopReason": "message", "endTick": 124, "messages": [],
             "pausedByThisTool": True},
        ]

        def game(name, args=None, strict=True, timeout=600):
            calls.append((name, args, timeout))
            if name == run.TOOL:
                return replies.pop(0)
            return {"success": True, "paused": True}

        with mock.patch.object(run, "_letters", return_value=set()), \
             mock.patch.object(run, "_ticks", side_effect=[100, 124]), \
             mock.patch.object(run.rim, "game", side_effect=game):
            reason, _ = run._companion(None, 10, "Superfast", 9, False,
                                       ["Pawn_Megasloth42"], ["Pawn_Ian77"])

        self.assertEqual("message", reason)
        guard_calls = [c for c in calls if c[0] == run.TOOL]
        self.assertEqual(2, len(guard_calls))
        self.assertLessEqual(guard_calls[0][1]["maxDurationMs"], 1000)
        self.assertEqual(-1, guard_calls[0][1]["messageSinceTick"])
        self.assertEqual(123, guard_calls[1][1]["messageSinceTick"])
        self.assertEqual("Pawn_Megasloth42",
                         guard_calls[0][1]["ignoredHostileIds"])
        self.assertFalse(guard_calls[0][1]["stopOnCurrentAlerts"])
        self.assertFalse(guard_calls[0][1]["watchAlerts"])
        self.assertNotIn("ignoredAlertLabels", guard_calls[0][1])
        self.assertTrue(guard_calls[0][1]["stopOnCurrentDownedColonists"])
        self.assertEqual("Pawn_Ian77",
                         guard_calls[0][1]["ignoredDownedColonistIds"])
        self.assertTrue(all(c[2] == run.GUARD_RPC_TIMEOUT for c in guard_calls))

    def test_alerts_never_stop_a_pulse_and_new_ones_are_reported(self):
        alerts = [[{"active": True, "label": "Low food", "priority": "High",
                    "type": "RimWorld.Alert_LowFood"}],
                  [{"active": True, "label": "Low food", "priority": "High",
                    "type": "RimWorld.Alert_LowFood"},
                   {"active": True, "label": "Minor break risk x3", "priority": "High",
                    "type": "RimWorld.Alert_BreakRiskMinor"},
                   {"active": True, "label": "Tattered apparel", "priority": "Medium",
                    "type": "RimWorld.Alert_TatteredApparel"}]]

        def game(name, args=None, strict=True, timeout=600):
            if name == run.TOOL:
                return {"stopReason": "budget_elapsed", "endTick": 10,
                        "pausedByThisTool": False}
            if name == "rimworld/list_alerts":
                return {"alerts": alerts.pop(0)}
            return {"success": True, "paused": True}

        printed = []
        with mock.patch.object(run, "_letters", return_value=set()),              mock.patch.object(run, "_ticks", side_effect=[100, 200]),              mock.patch.object(run.turnclock, "print_budget_line"),              mock.patch.object(run.rim, "game", side_effect=game),              mock.patch("builtins.print", side_effect=lambda *a, **k: printed.append(" ".join(str(x) for x in a))):
            reason, _ = run._companion(None, 0, "Superfast", 1, True)

        self.assertEqual("time up", reason)
        # A count suffix moving is not a new alert; a Medium one is not reported.
        self.assertEqual(["   NEW ALERT: Minor break risk x3"],
                         [x for x in printed if "NEW ALERT" in x])

    def test_a_standing_alert_with_a_moved_count_is_not_new(self):
        alerts = [[{"active": True, "label": "Minor break risk x2", "priority": "High",
                    "type": "RimWorld.Alert_BreakRiskMinor"}],
                  [{"active": True, "label": "Minor break risk x4", "priority": "High",
                    "type": "RimWorld.Alert_BreakRiskMinor"}]]

        def game(name, args=None, strict=True, timeout=600):
            if name == run.TOOL:
                return {"stopReason": "budget_elapsed", "endTick": 10}
            if name == "rimworld/list_alerts":
                return {"alerts": alerts.pop(0)}
            return {"success": True, "paused": True}

        with mock.patch.object(run, "_letters", return_value=set()),              mock.patch.object(run, "_ticks", side_effect=[100, 200]),              mock.patch.object(run.rim, "game", side_effect=game),              mock.patch.object(run, "_report") as report:
            run._companion(None, 0, "Superfast", 1, True)
        self.assertEqual([], report.call_args.args[5])

    def test_unavailable_watcher_fails_closed_without_polling(self):
        with mock.patch.object(run, "_supervised_session", return_value=None), \
             mock.patch.object(run, "_companion",
                               side_effect=run._NoCompanion("busy")), \
             mock.patch.object(run, "_polling") as polling, \
             mock.patch.object(run.rim, "game",
                               return_value={"success": True, "paused": True}) as game, \
             mock.patch.object(run.letters, "auto_dismiss"):
            reason, detail = run.until(seconds=30, verbose=False)

        self.assertEqual(("watcher unavailable", []), (reason, detail))
        polling.assert_not_called()
        game.assert_called_once_with("rimworld/pause_game", {}, strict=False,
                                     timeout=run.GUARD_RPC_TIMEOUT)

    def test_pause_requires_explicit_success_and_paused_postcondition(self):
        self.assertTrue(run._pause_verified({"success": True, "paused": True}))
        for reply in ({}, {"success": True}, {"paused": True},
                      {"success": False, "paused": True}, "ok", None):
            self.assertFalse(run._pause_verified(reply))

    def test_previously_reported_hostile_still_blocks_without_explicit_id(self):
        hostile = {"name": "Megasloth", "defName": "Megasloth",
                   "position": {"x": 10, "z": 20}}
        reply = {"stopReason": "hostile", "endTick": 101,
                 "hostiles": [hostile], "pausedByThisTool": True}
        def game(name, args=None, strict=True, timeout=600):
            return (reply if name == run.TOOL
                    else {"success": True, "paused": True})
        with mock.patch.object(run, "_letters", return_value=set()), \
             mock.patch.object(run, "_ticks", side_effect=[100, 101]), \
             mock.patch.object(run.rim, "game", side_effect=game) as game_mock:
            reason, _ = run._companion(None, 10, "Superfast", 1, False)

        self.assertEqual("threat", reason)
        self.assertEqual(1, sum(c.args[0] == run.TOOL for c in game_mock.call_args_list))


class SupervisedPlayTests(unittest.TestCase):
    """WEIRD 66: run.py under supervised play failed, paused the clock and
    killed the play service; recovery took a full `play.py start`."""

    def test_a_live_play_service_refuses_before_any_bridge_call(self):
        out = io.StringIO()
        with mock.patch.object(run, "_supervised_session",
                               return_value={"epoch": 12, "pid": 4242}), \
             mock.patch.object(run, "_companion") as companion, \
             mock.patch.object(run.rim, "game") as game, \
             mock.patch("sys.stdout", out):
            reason, detail = run.until(None, 10, "Superfast", 1.5)
        self.assertEqual(("supervised play", []), (reason, detail))
        companion.assert_not_called()
        game.assert_not_called()
        text = out.getvalue()
        self.assertIn("SUPERVISED PLAY OWNS THE CLOCK (epoch 12, service pid 4242)",
                      text)
        self.assertIn("python play.py status", text)
        self.assertIn("python play.py pause", text)
        self.assertIn("Nothing was touched here", text)

    def test_the_companion_busy_refusal_does_not_pause_the_clock(self):
        reply = {"stopReason": "busy", "stopDetail":
                 "A supervised_play session is active; use its status/pause API "
                 "instead of starting a competing clock guard."}
        def game(name, args=None, strict=True, timeout=600):
            if name == run.TOOL:
                return reply
            raise AssertionError("run.py must not call %s here" % name)
        out = io.StringIO()
        with mock.patch.object(run, "_supervised_session", return_value=None), \
             mock.patch.object(run, "_letters", return_value=set()), \
             mock.patch.object(run, "_ticks", return_value=100), \
             mock.patch.object(run.rim, "game", side_effect=game), \
             mock.patch("sys.stdout", out):
            reason, _ = run.until(None, 10, "Superfast", 1.5)
        self.assertEqual("supervised play", reason)
        self.assertIn("SUPERVISED PLAY OWNS THE CLOCK", out.getvalue())
        self.assertIn("the companion said:", out.getvalue())
        self.assertNotIn("WATCHER FAILED", out.getvalue())

    def test_a_dead_service_state_is_not_an_owner(self):
        with mock.patch.object(run, "_supervised_session", return_value=None), \
             mock.patch.object(run, "_companion",
                               return_value=("done", [])) as companion:
            self.assertEqual(("done", []), run.until(None, 1, "Normal", 1.5))
        companion.assert_called_once()


if __name__ == "__main__":
    unittest.main()
