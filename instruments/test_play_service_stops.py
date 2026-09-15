"""The two 2026-09-07 supervised-play defects, at the service layer.

1. A queued long event -- the daily autosave -- refuses `start` with nothing on
   screen to dismiss. Reporting that as PLAY REFUSED cost a manual restart each
   time; the honest answer is to retry, because it clears in about a second.
2. A stop that records no reason. The board printed `no supervised play is
   running` with no guard named (turn 35, twice, after fire events). Whatever
   the loop knows must reach `state/play-service.json`.
"""
from pathlib import Path
import sys
import unittest
import unittest.mock as mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import play_service


BINDING = {"session_id": "s", "host_pid": 1, "host_started": "t"}
CONFIG = {"binding": BINDING, "generation": 1, "owner": "agent",
          "speed": "Superfast", "leaseMs": 15000}


class Recorder(object):
    """A scripted `home/supervised_play`, and a note of every call made."""

    def __init__(self, starts, status_reply=None, events=None):
        self.starts = list(starts)
        self.status_reply = status_reply
        self.events = events or {"success": True, "events": [], "nextCursor": 0}
        self.calls = []

    def __call__(self, args):
        self.calls.append(dict(args))
        op = args.get("op")
        if op == "start":
            return self.starts.pop(0) if self.starts else {"success": False}
        if op == "status":
            return self.status_reply
        if op == "events":
            return self.events
        if op == "heartbeat":
            return {"success": False}
        return {"success": True}


def run(call, **kw):
    slept = []
    state = play_service.run(
        CONFIG, call=call, runtime_path=Path("runtime.json"),
        state_path=Path(kw.pop("state_path")), stop_path=Path("no-stop-file"),
        alive=lambda pid: True, publisher=lambda *a, **k: {"accepted": True},
        clock=lambda: 1000.0, sleeper=lambda s: slept.append(s),
        max_cycles=1, **kw)
    return state, slept


class LongEventRetryTests(unittest.TestCase):
    RETRY = {"success": False, "retryable": True, "refusal": "long_event",
             "message": "A long event (autosave, map generation) is running"}
    OK = {"success": True, "active": True, "epoch": 4, "baselineAlerts": []}

    def _binding(self):
        return mock.patch.object(play_service, "binding_alive",
                                 return_value=True)

    def test_a_long_event_refusal_is_retried_until_it_clears(self):
        call = Recorder([self.RETRY, self.RETRY, self.OK],
                        status_reply={"epoch": 4, "stopReason": None})
        with self._binding(), mock.patch.object(
                play_service, "atomic_json", lambda *a, **k: None):
            state, slept = run(call, state_path="state.json")
        self.assertEqual(sum(1 for c in call.calls if c["op"] == "start"), 3)
        self.assertEqual(state["epoch"], 4)
        self.assertIn(play_service.START_RETRY_SECONDS, slept)

    def test_a_refusal_that_never_clears_says_it_was_retried(self):
        call = Recorder([self.RETRY] * play_service.START_ATTEMPTS)
        with self._binding(), mock.patch.object(
                play_service, "atomic_json", lambda *a, **k: None):
            with self.assertRaises(RuntimeError) as caught:
                run(call, state_path="state.json")
        self.assertIn("retried %d times" % play_service.START_ATTEMPTS,
                      str(caught.exception))

    def test_a_real_window_is_refused_on_the_first_attempt(self):
        call = Recorder([{"success": False, "retryable": False,
                          "message": "Game is force-paused by Dialog_NodeTree"}])
        with self._binding(), mock.patch.object(
                play_service, "atomic_json", lambda *a, **k: None):
            with self.assertRaises(RuntimeError) as caught:
                run(call, state_path="state.json")
        self.assertEqual(sum(1 for c in call.calls if c["op"] == "start"), 1)
        self.assertNotIn("retried", str(caught.exception))


class ThreatClassificationTests(unittest.TestCase):
    """The guard's classification has to survive the trip out to the refusal.

    `start` answers with a status snapshot and nothing else, so before
    2026-09-11 a threat stop reached `play.py` as one name and no ThingID --
    and `play.py`'s own `home/status` re-read disagreed with the guard about
    wild predators, which is how a wolverine could block a start that
    `--ignore-hostile all` could not unblock.
    """

    STOPPED = {"success": True, "active": False, "epoch": 7,
               "stopReason": "predator_hunt",
               "stopDetail": "lynx [predator_hunting_ours] hunting Rosie, "
                             "a colony animal (19 cells from Finn)",
               "stopThreats": [{"thingId": "Thing_Lynx4", "pawnId": 4,
                                "name": "lynx",
                                "category": "predator_hunting_ours",
                                "reason": "hunting Rosie, a colony animal",
                                "stops": True},
                               {"thingId": "Thing_Wolverine88", "pawnId": 88,
                                "name": "wolverine", "category": "predator",
                                "reason": "hunting wildlife, 41 cells from Finn",
                                "stops": False}]}

    def test_the_predator_ignore_list_reaches_the_companion(self):
        call = Recorder([{"success": True, "active": True, "epoch": 4,
                          "baselineAlerts": []}],
                        status_reply={"epoch": 4, "stopReason": None})
        config = dict(CONFIG, ignoredPredatorIds="all")
        with mock.patch.object(play_service, "binding_alive", return_value=True), \
             mock.patch.object(play_service, "atomic_json", lambda *a, **k: None):
            play_service.run(
                config, call=call, runtime_path=Path("runtime.json"),
                state_path=Path("state.json"), stop_path=Path("no-stop-file"),
                alive=lambda pid: True, publisher=lambda *a, **k: {"accepted": True},
                clock=lambda: 1000.0, sleeper=lambda s: None, max_cycles=1)
        started = [c for c in call.calls if c["op"] == "start"][0]
        self.assertEqual("all", started["ignoredPredatorIds"])
        self.assertEqual("", started["ignoredHostileIds"])

    def test_a_start_that_stopped_writes_the_threat_rows_to_the_state_file(self):
        written = {}
        call = Recorder([self.STOPPED],
                        status_reply={"epoch": 7, "stopReason": "predator_hunt"})
        with mock.patch.object(play_service, "binding_alive", return_value=True), \
             mock.patch.object(play_service, "atomic_json",
                               lambda path, value: written.update(value)):
            with self.assertRaisesRegex(RuntimeError, "stopped during start"):
                play_service.run(
                    CONFIG, call=call, runtime_path=Path("runtime.json"),
                    state_path=Path("state.json"), stop_path=Path("no-stop-file"),
                    alive=lambda pid: True,
                    publisher=lambda *a, **k: {"accepted": True},
                    clock=lambda: 1000.0, sleeper=lambda s: None, max_cycles=1)
        self.assertEqual("Thing_Lynx4", written["stopThreats"][0]["thingId"])
        self.assertEqual("predator_hunting_ours",
                         written["stopThreats"][0]["category"])
        # The non-stopping row travels too: the refusal lists what was
        # considered, not only what blocked.
        self.assertEqual("predator", written["stopThreats"][1]["category"])
        self.assertIs(False, written["stopThreats"][1]["stops"])
        # It is not READY: the rows are evidence for the refusal, not a start.
        self.assertIs(False, written["ready"])


class UnnamedStopTests(unittest.TestCase):
    OK = {"success": True, "active": True, "epoch": 9, "baselineAlerts": []}

    def _run(self, status_reply, events):
        written = {}
        call = Recorder([self.OK], status_reply=status_reply, events=events)
        with mock.patch.object(play_service, "binding_alive",
                                        return_value=True), \
             mock.patch.object(
                 play_service, "atomic_json",
                 lambda path, value: written.update(value)):
            run(call, state_path="state.json")
        return written

    def test_a_closing_status_read_that_fails_still_records_what_is_known(self):
        rows = {"success": True, "nextCursor": 3, "events": [
            {"cursor": 3, "epoch": 9, "kind": "alert_new",
             "detail": "Fire in home area"}]}
        written = self._run(None, rows)
        self.assertIsNone(written["stopReason"])
        self.assertIn("the companion recorded no stop reason for epoch 9",
                      written["stopDetail"])
        self.assertIn("the read did not answer at all", written["stopDetail"])
        self.assertIn("alert_new: Fire in home area", written["lastEvents"])

    def test_a_status_read_for_another_epoch_names_that_as_the_reason(self):
        written = self._run({"epoch": 11, "stopReason": "colonist_downed"},
                            {"success": True, "nextCursor": 0, "events": []})
        self.assertIsNone(written["stopReason"])
        self.assertIn("it answered for epoch 11, not 9", written["stopDetail"])

    def test_a_named_stop_is_left_exactly_as_the_companion_gave_it(self):
        written = self._run({"epoch": 9, "stopReason": "colonist_downed",
                             "stopDetail": "Lucas (downed)"},
                            {"success": True, "nextCursor": 0, "events": []})
        self.assertEqual(written["stopReason"], "colonist_downed")
        self.assertEqual(written["stopDetail"], "Lucas (downed)")
        self.assertEqual(written["stopKind"], "guard")


if __name__ == "__main__":
    unittest.main()
