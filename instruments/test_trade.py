"""Mock-only tests for trade.py; no running game is contacted.

`accept` is the only verb here that spends the colony's silver, and the watched
path is the one that opens the real `Dialog_Trade`. What is worth asserting
offline is the order of operations around that window: supervised play is
paused BEFORE the call (the dialog force-pauses the game and the watcher dies
on any force pause), the restart line is printed on both the success and the
refusal path, a `restage_mismatch` is named rather than swallowed, and a reply
that is not a payload at all is reported rather than turned into a traceback.
"""
import io
import unittest
from unittest import mock

import trade


def accept_reply(**kw):
    """What `home/trade action=accept watch=true` answers on a good deal."""
    r = {"success": True, "tool": "home/trade", "action": "accept",
         "traderName": "Aardvark", "negotiator": "Finn",
         "executed": True, "actuallyTraded": True,
         "moved": [{"label": "pemmican", "count": 180, "unitPrice": 1.2,
                    "lineValue": 216, "action": "buy"}],
         "goodwillBefore": 0, "goodwillAfter": 0,
         "traderResponse": [], "questReceived": False,
         "watch": {"shown": True, "selected": True, "inspectTab": None,
                   "mainTab": None, "cameraMoved": True, "leadMs": 1500,
                   "closesAfterSeconds": 8, "note": None, "reason": None,
                   "dialogShown": True, "secondsShown": 8}}
    r.update(kw)
    return r


def restage_reply():
    """The refusal the server raises when the staged deal changed under the
    window. Nothing was executed and the session is untouched."""
    return {"success": False, "tool": "home/trade", "action": "accept",
            "errorKind": "restage_mismatch",
            "error": "The staged deal changed while the trade window was on "
                     "screen, so nothing was executed.",
            "watch": {"shown": True, "selected": True, "inspectTab": None,
                      "mainTab": None, "cameraMoved": True, "leadMs": 1500,
                      "closesAfterSeconds": 8, "note": None, "reason": None,
                      "dialogShown": True, "secondsShown": 8}}


class AcceptTests(unittest.TestCase):
    def run_accept(self, argv, reply, paused="paused"):
        calls = []

        def game(tool, args=None, strict=True):
            calls.append((tool, args))
            return reply

        with mock.patch.object(trade.rim, "game", side_effect=game), \
             mock.patch.object(trade, "pause_supervised_play",
                               return_value=paused), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = trade.cmd_accept(argv)
        return code, out.getvalue(), calls

    def test_a_watched_accept_pauses_play_first_and_prints_the_restart_line(self):
        code, text, calls = self.run_accept([], accept_reply())
        self.assertEqual(0, code)
        self.assertIn("PAUSED on purpose", text)
        self.assertIn(trade.RESTART_LINE, text)
        sent = calls[0][1]
        self.assertTrue(sent["watch"])
        # 8 s is the server's own default; sending it again would only be
        # another way to get it wrong.
        self.assertNotIn("watchSeconds", sent)

    def test_no_watch_neither_pauses_nor_asks_for_a_restart(self):
        calls = []

        def game(tool, args=None, strict=True):
            calls.append((tool, args))
            return accept_reply(watch={"shown": False, "reason": "watch:false",
                                       "dialogShown": False, "secondsShown": 0})

        with mock.patch.object(trade.rim, "game", side_effect=game), \
             mock.patch.object(trade, "pause_supervised_play") as pause, \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = trade.cmd_accept(["--no-watch"])
        self.assertEqual(0, code)
        pause.assert_not_called()
        self.assertFalse(calls[0][1]["watch"])
        self.assertNotIn(trade.RESTART_LINE, out.getvalue())

    def test_a_restage_mismatch_is_named_exits_non_zero_and_still_restarts(self):
        code, text, _ = self.run_accept([], restage_reply())
        self.assertEqual(1, code)
        self.assertIn("restage_mismatch", text)
        self.assertIn("nothing was executed", text)
        # Play was paused for a trade that did not happen; the line that
        # brings it back must still be on screen.
        self.assertIn(trade.RESTART_LINE, text)

    def test_a_reply_that_is_not_a_payload_is_reported_not_raised(self):
        """rim.game(strict=False) hands a non-JSON string straight back --
        that is exactly the shape `_fail` exists to catch. Crashing one line
        later in watch_line() throws the guard away."""
        code, text, _ = self.run_accept([], "<html>gateway timeout</html>")
        self.assertEqual(1, code)
        self.assertIn("not a payload", text)
        self.assertIn(trade.RESTART_LINE, text)


class PauseSupervisedPlayTests(unittest.TestCase):
    def test_a_dead_service_is_not_running_rather_than_an_error(self):
        play = mock.Mock(service_alive=mock.Mock(return_value=False))
        svc = mock.Mock(SERVICE_STATE="state.json",
                        read_json=mock.Mock(return_value={}))
        with mock.patch.dict("sys.modules", {"play": play,
                                             "play_service": svc}):
            self.assertEqual("not running", trade.pause_supervised_play())

    def test_a_live_service_is_paused(self):
        play = mock.Mock(service_alive=mock.Mock(return_value=True),
                         pause=mock.Mock(return_value=0))
        svc = mock.Mock(SERVICE_STATE="state.json",
                        read_json=mock.Mock(return_value={"pid": 1}))
        with mock.patch.dict("sys.modules", {"play": play,
                                             "play_service": svc}):
            self.assertEqual("paused", trade.pause_supervised_play())


if __name__ == "__main__":
    unittest.main()
