"""Refusals that name every blocker, not the first one the loop tripped over.

2026-09-07: "`play.py start` refuses on hostiles one at a time, so a two-raider
raid costs two refusals to learn both ThingIDs." The companion's `PawnHit` puts
the pawn's ID in the payload and only its NAME in the message, so the refusal
did not even carry the id the fix needs.
"""
import io
import sys
import unittest
from unittest import mock

import play
import trade


THREATS = {"threats": {
    "hostiles": [
        {"name": "Raider Kade", "thingId": "Thing_Human1001",
         "distanceToNearestColonist": 12, "downed": False},
        {"name": "Raider Vey", "thingId": "Thing_Human1002",
         "distanceToNearestColonist": 31, "downed": False},
    ],
    "huntingPredators": [
        {"name": "lynx", "thingId": "Thing_Lynx2003",
         "distanceToNearestColonist": 22, "downed": False},
    ]}}


class BlockingThreats(unittest.TestCase):
    def rows(self, reply=THREATS):
        return play.blocking_threats(caller=lambda: reply)

    def test_it_lists_hostiles_and_hunting_predators_together(self):
        self.assertEqual(["Thing_Human1001", "Thing_Human1002", "Thing_Lynx2003"],
                         [r["thingId"] for r in self.rows()])

    def test_the_paste_line_acknowledges_every_one_of_them(self):
        line = [l for l in play.blocking_threat_lines(self.rows())
                if l.strip().startswith("python play.py start")][0]
        self.assertIn("--ignore-hostile Thing_Human1001", line)
        self.assertIn("--ignore-hostile Thing_Human1002", line)
        # PLAYBOOK claim, confirmed against SupervisedPlayTool: the hostile
        # check and the PredatorHunt check share one IgnoredHostiles set, so
        # --ignore-hostile covers a predator_hunt too.
        self.assertIn("--ignore-hostile Thing_Lynx2003", line)

    def test_a_start_refusal_is_enriched_with_the_whole_list(self):
        row = {"error": "supervised_play stopped during start: hostile: "
                        "Raider Kade (hostile to player faction)"}
        text = play.enrich_start_error(row, threats=self.rows())
        self.assertIn("Raider Kade", text)
        self.assertIn("3 thing(s) will block a start", text)
        self.assertIn("Thing_Lynx2003", text)

    def test_an_unrelated_start_error_is_left_exactly_as_it_was(self):
        row = {"error": "loaded-game generation unavailable"}
        self.assertEqual(row["error"], play.enrich_start_error(row, threats=[]))

    def test_a_dead_binding_refusal_carries_the_same_repair_line_as_the_claim(self):
        """`PLAY REFUSED -- runtime binding unavailable` named the cause but
        not the cure. It now prints the one command that fixes it, and exits 1."""
        with mock.patch.object(play.play_service, "binding", return_value=None), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = play.main(["start"])
        text = out.getvalue()
        self.assertEqual(1, code)
        self.assertIn("PLAY REFUSED", text)
        self.assertIn("runtime binding unavailable", text)
        self.assertIn("bind-check --repair", text)


class TradePausesOnPurpose(unittest.TestCase):
    """`trade.py accept` used to leave the game paused and the service dead.

    The dialog force-pauses, the companion's watcher stops on any force pause,
    and nothing said a word. Found only by checking.
    """

    def accept(self, argv, reply, service_alive=True):
        svc = mock.Mock()
        svc.SERVICE_STATE = "state"
        svc.read_json.return_value = {"ready": True}
        p = mock.Mock()
        p.service_alive.return_value = service_alive
        p.pause.return_value = 0
        with mock.patch.dict(sys.modules, {"play": p, "play_service": svc}), \
             mock.patch.object(trade, "call", return_value=reply), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = trade.cmd_accept(argv)
        return code, out.getvalue(), p

    def test_it_pauses_before_the_dialog_and_prints_the_restart_line(self):
        reply = {"success": True, "actuallyTraded": True, "executed": True,
                 "traderName": "Sanguine caravan", "negotiator": "Finn",
                 "watch": {"shown": True, "dialogShown": True, "secondsShown": 8}}
        code, text, p = self.accept([], reply)
        self.assertEqual(0, code)
        p.pause.assert_called_once_with()
        self.assertIn("PAUSED on purpose before the trade dialog", text)
        self.assertIn("python play.py start", text)

    def test_no_watch_opens_no_dialog_and_pauses_nothing(self):
        reply = {"success": True, "actuallyTraded": True, "executed": True,
                 "traderName": "Sanguine caravan", "negotiator": "Finn",
                 "watch": {"shown": False, "reason": "--no-watch"}}
        code, text, p = self.accept(["--no-watch"], reply)
        self.assertEqual(0, code)
        p.pause.assert_not_called()
        self.assertNotIn("PAUSED on purpose", text)

    def test_a_service_that_was_not_running_is_said_so_not_silently_skipped(self):
        reply = {"success": True, "actuallyTraded": True, "executed": True,
                 "traderName": "T", "negotiator": "F",
                 "watch": {"shown": True, "dialogShown": True, "secondsShown": 8}}
        code, text, p = self.accept([], reply, service_alive=False)
        self.assertEqual(0, code)
        p.pause.assert_not_called()
        self.assertIn("was not running", text)
        self.assertIn("python play.py start", text)


if __name__ == "__main__":
    unittest.main()
