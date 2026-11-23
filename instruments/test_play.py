import argparse
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import play
import play_service


class PlayServiceTests(unittest.TestCase):
    def test_status_probe_leaves_a_real_child_alive(self):
        child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"],
                                 stdin=subprocess.PIPE,
                                 creationflags=play.NO_WINDOW)
        try:
            for _ in range(3):
                self.assertTrue(play_service.pid_alive(child.pid))
                self.assertIsNone(child.poll())
        finally:
            child.communicate(timeout=5)
        self.assertEqual(0, child.returncode)

    def test_routine_notification_delivers_without_waking(self):
        publisher = mock.Mock(return_value={"accepted": True})
        self.assertTrue(play_service.publish_event("e", "g", {
            "cursor": 1, "kind": "notification_new", "detail": "Visitors leaving"}, publisher))
        self.assertEqual({"epoch": "e", "wake": False}, publisher.call_args.kwargs["meta"])

    def test_liveness_never_signals_the_process(self):
        with mock.patch("os.kill", side_effect=AssertionError("must not signal")), \
             mock.patch("runtime_binding.process_identity", return_value="birth") as identity:
            self.assertTrue(play_service.pid_alive(123))
            identity.assert_called_once_with(123)
        with mock.patch("runtime_binding.process_identity", return_value=None):
            self.assertFalse(play_service.pid_alive(123))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.runtime = self.root / "runtime.json"
        self.state = self.root / "service.json"
        self.stop = self.root / "stop"
        self.binding = {"session_id": "s1", "host_pid": 44,
                        "host_started": "filetime-1"}
        self.runtime.write_text(json.dumps(self.binding), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_host_disconnect_pauses_and_exits_without_renewing(self):
        calls = []
        alive = mock.Mock(side_effect=[True, False])
        def call(args):
            calls.append(args)
            if args["op"] == "status":
                return {"success": True, "newestCursor": 0}
            if args["op"] == "events":
                return {"success": True, "events": [], "nextCursor": 1}
            return {"success": True, "active": True, "epoch": "e1", "cursor": 0}
        config = {"binding": self.binding, "generation": "g1",
                  "owner": "owner", "speed": "Normal",
                  "leaseMs": 30000}
        play_service.run(config, call=call, runtime_path=self.runtime,
                         state_path=self.state, stop_path=self.stop, alive=alive,
                         sleeper=lambda _: None)
        self.assertEqual(["status", "start", "events", "status", "pause"],
                         [x["op"] for x in calls])
        self.assertFalse(play_service.read_json(self.state)["ready"])

    def test_rejected_event_does_not_advance_cursor(self):
        def call(args):
            return {"success": True, "events": [{"cursor": 6, "kind": "alert"}],
                    "nextCursor": 6}
        cursor, reply = play_service.relay_events(
            "e1", "g1", 5, call, publisher=lambda *a, **k: {"accepted": False})
        self.assertEqual(5, cursor)
        self.assertIn("relayError", reply)

    def test_publish_uses_stable_epoch_cursor_id_and_exact_api(self):
        publisher = mock.Mock(return_value={"accepted": True})
        self.assertTrue(play_service.publish_event(
            "e1", "g1", {"cursor": 7, "kind": "hostile", "capturedAt": 12}, publisher))
        publisher.assert_called_once_with(
            "hostile", {"cursor": 7, "kind": "hostile", "capturedAt": 12},
            event_id="supervised:g1:e1:7", captured_at=12, generation="g1",
            source="supervised_play", meta={"epoch": "e1"})

    def test_routine_events_do_not_notify_bus(self):
        publisher = mock.Mock()
        for cursor, kind in enumerate(("started", "injury_observed",
                                       "speed_changed", "requested_pause"), 1):
            self.assertTrue(play_service.publish_event(
                "e1", "g1", {"cursor": cursor, "kind": kind}, publisher))
        publisher.assert_not_called()

    def test_a_new_alert_is_relayed_and_only_critical_wakes(self):
        publisher = mock.Mock(return_value={"accepted": True})
        for cursor, priority in enumerate(("High", "Critical"), 1):
            self.assertTrue(play_service.publish_event(
                "e1", "g1", {"cursor": cursor, "kind": "alert_new",
                             "detail": "Low food",
                             "event": {"label": "Low food", "priority": priority}},
                publisher))
        self.assertEqual(2, publisher.call_count)
        self.assertEqual({"epoch": "e1", "wake": False},
                         publisher.call_args_list[0].kwargs["meta"])
        self.assertEqual({"epoch": "e1", "wake": True},
                         publisher.call_args_list[1].kwargs["meta"])

    def test_only_a_negative_notification_wakes_a_parked_hands(self):
        """A short circuit, a dead crop, a departed visitor: the companion
        marks those `negative` and nothing pauses, so the relay is the only
        thing that can get them looked at. Neutral and positive ride along."""
        publisher = mock.Mock(return_value={"accepted": True})
        rows = [{"cursor": 1, "kind": "notification_new", "detail": "Power off",
                 "event": {"id": "l1", "letterDef": "NegativeEvent",
                           "negative": True}},
                {"cursor": 2, "kind": "notification_new", "detail": "Caravan",
                 "event": {"id": "l2", "letterDef": "PositiveEvent",
                           "negative": False}},
                # A flattened row (no event{}) is read the same way.
                {"cursor": 3, "kind": "notification_new", "detail": "Short circuit",
                 "negative": True}]
        for row in rows:
            self.assertTrue(play_service.publish_event("e1", "g1", row, publisher))
        self.assertEqual([True, False, True],
                         [c.kwargs["meta"]["wake"]
                          for c in publisher.call_args_list])

    def test_hostiles_cleared_is_relayed_and_wakes_a_parked_hands(self):
        publisher = mock.Mock(return_value={"accepted": True})
        self.assertTrue(play_service.publish_event(
            "e1", "g1", {"cursor": 7, "kind": "hostiles_cleared",
                         "detail": "No conscious hostiles remain: ...",
                         "event": {"downedHostiles": [], "draftedColonists": []}},
            publisher))
        self.assertEqual({"epoch": "e1", "wake": True},
                         publisher.call_args.kwargs["meta"])

    def test_standing_alerts_are_recorded_for_the_start_line(self):
        rows = [{"alertKey": "RimWorld.Alert_LowFood|High|Low food",
                 "label": "Low food", "priority": "High"}]
        def call(args):
            if args["op"] == "status":
                return {"success": True, "newestCursor": 0}
            if args["op"] == "start":
                return {"success": True, "active": True, "epoch": "e1",
                        "baselineAlerts": rows}
            if args["op"] == "events":
                return {"success": True, "events": [], "nextCursor": 1}
            return {"success": True}
        config = {"binding": self.binding, "generation": "g1", "owner": "owner",
                  "speed": "Normal", "leaseMs": 30000}
        play_service.run(config, call=call, runtime_path=self.runtime,
                         state_path=self.state, stop_path=self.stop,
                         alive=mock.Mock(side_effect=[True, False]),
                         sleeper=lambda _: None)
        self.assertEqual(rows, play_service.read_json(self.state)["baselineAlerts"])
        self.assertEqual("standing alerts (1): Low food", play.standing_alerts(rows))
        self.assertEqual("standing alerts: none", play.standing_alerts([]))

    def test_new_letter_and_message_events_are_delivered(self):
        publisher = mock.Mock(return_value={"accepted": True})
        for cursor, kind in enumerate(("letter", "message"), 1):
            self.assertTrue(play_service.publish_event(
                "e1", "g1", {"cursor": cursor, "kind": kind}, publisher))
        self.assertEqual(2, publisher.call_count)

    def test_pause_is_idempotent_after_safety_stop(self):
        with mock.patch.object(play.play_service, "read_json", return_value={
                "epoch": "e1", "owner": "o1"}), \
             mock.patch.object(play.rim, "init"), \
             mock.patch.object(play.rim, "game", return_value={
                 "success": True, "active": False, "paused": True}) as game, \
             mock.patch.object(play, "STOP", self.stop):
            self.assertEqual(0, play.pause())
        game.assert_called_once_with(play_service.TOOL, {"op": "status"},
                                     strict=False, timeout=8)

    def test_pause_without_owned_session_uses_emergency_pause(self):
        with mock.patch.object(play.play_service, "read_json", return_value={}), \
             mock.patch.object(play.rim, "init"), \
             mock.patch.object(play.rim, "game", return_value={
                 "success": True, "paused": True}) as game:
            self.assertEqual(0, play.pause())
        game.assert_called_once_with("rimworld/pause_game", {"pause": True},
                                     strict=False, timeout=8)

    def test_safety_stop_is_relayed_and_never_renewed_again(self):
        calls = []
        now = [0.0]
        def clock():
            now[0] += 11.0
            return now[0]
        def call(args):
            calls.append(args)
            if args["op"] == "status":
                return {"success": True, "newestCursor": 0}
            if args["op"] == "start":
                return {"success": True, "active": True, "epoch": "e1", "cursor": 0}
            if args["op"] == "heartbeat":
                return {"success": True, "active": False, "running": False}
            if args["op"] == "events":
                return {"success": True, "events": [], "nextCursor": 1}
            return {"success": True}
        config = {"binding": self.binding, "generation": "g1",
                  "owner": "owner", "speed": "Normal",
                  "leaseMs": 30000}
        play_service.run(config, call=call, runtime_path=self.runtime,
                         state_path=self.state, stop_path=self.stop,
                         alive=lambda _: True, clock=clock,
                         sleeper=lambda _: None,
                         publisher=lambda *a, **k: {"accepted": True})
        self.assertEqual(1, len([x for x in calls if x["op"] == "heartbeat"]))
        self.assertEqual("pause", calls[-1]["op"])

    def test_stop_during_start_is_published_before_refusal(self):
        published = []
        def call(args):
            if args["op"] == "status":
                return {"success": True, "newestCursor": 4}
            if args["op"] == "start":
                return {"success": True, "active": False, "epoch": "e2",
                        "stopReason": "hostile", "stopDetail": "raccoon",
                        "newestCursor": 5}
            if args["op"] == "events":
                self.assertEqual(4, args["afterCursor"])
                return {"success": True,
                        "events": [{"cursor": 5, "kind": "hostile",
                                    "detail": "raccoon"}], "nextCursor": 5}
        config = {"binding": self.binding, "generation": "g1",
                  "owner": "owner", "speed": "Normal", "leaseMs": 30000}
        with self.assertRaisesRegex(RuntimeError, "stopped during start"):
            play_service.run(
                config, call=call, runtime_path=self.runtime,
                state_path=self.state, stop_path=self.stop,
                alive=lambda _: True,
                publisher=lambda *a, **k: published.append((a, k)) or
                                           {"accepted": True})
        self.assertEqual("hostile", published[0][0][0])


    def test_state_write_survives_one_permission_error(self):
        seen, real = [], play_service.game_session.os.replace
        def flaky(src, dst):
            seen.append(str(src))
            if len(seen) == 1:
                raise PermissionError(5, "Access is denied")
            return real(src, dst)
        with mock.patch.object(play_service.game_session.os, "replace", flaky),              mock.patch.object(play_service.game_session.time, "sleep"):
            play_service.atomic_json(self.state, {"epoch": "e1"})
        self.assertEqual({"epoch": "e1"}, play_service.read_json(self.state))
        self.assertEqual(2, len(seen))

    def test_a_state_write_that_fails_after_start_pauses_the_game(self):
        calls = []
        def call(args):
            calls.append(args)
            if args["op"] == "status":
                return {"success": True, "newestCursor": 0}
            if args["op"] == "start":
                return {"success": True, "active": True, "epoch": "e9"}
            return {"success": True}
        writes, real = [], play_service.atomic_json
        def flaky(path, value):
            writes.append(value.get("epoch"))
            if len(writes) == 2:
                raise PermissionError(5, "Access is denied")
            return real(path, value)
        config = {"binding": self.binding, "generation": "g1", "owner": "owner",
                  "speed": "Normal", "leaseMs": 30000}
        with mock.patch.object(play_service, "atomic_json", flaky),              self.assertRaisesRegex(RuntimeError, "python play.py pause"):
            play_service.run(config, call=call, runtime_path=self.runtime,
                             state_path=self.state, stop_path=self.stop,
                             alive=lambda _: True, sleeper=lambda _: None)
        self.assertEqual(["status", "start", "pause"], [x["op"] for x in calls])
        self.assertEqual({"op": "pause", "epoch": "e9", "owner": "owner"}, calls[-1])

    def test_identity_is_on_disk_before_the_game_call(self):
        order = []
        def call(args):
            order.append("call:" + args["op"])
            if args["op"] == "status":
                return {"success": True, "newestCursor": 0}
            if args["op"] == "start":
                return {"success": True, "active": True, "epoch": "e1"}
            if args["op"] == "events":
                return {"success": True, "events": [], "nextCursor": 1}
            return {"success": True}
        real = play_service.atomic_json
        def watched(path, value):
            order.append("write:%s" % value.get("epoch"))
            return real(path, value)
        config = {"binding": self.binding, "generation": "g1", "owner": "owner",
                  "speed": "Normal", "leaseMs": 30000}
        with mock.patch.object(play_service, "atomic_json", watched):
            play_service.run(config, call=call, runtime_path=self.runtime,
                             state_path=self.state, stop_path=self.stop,
                             alive=mock.Mock(side_effect=[True, False]),
                             sleeper=lambda _: None)
        self.assertLess(order.index("write:None"), order.index("call:start"))
        row = play_service.read_json(self.state)
        self.assertEqual("e1", row["epoch"])
        self.assertIsNotNone(row["processStarted"])

    def test_a_failed_run_keeps_the_epoch_for_recovery(self):
        play_service.atomic_json(self.state, {"owner": "owner", "epoch": "e7",
                                              "ready": True})
        with mock.patch.object(play_service, "SERVICE_STATE", self.state),              mock.patch.object(play_service, "run",
                               side_effect=RuntimeError("boom")),              mock.patch.object(play_service, "read_json",
                               wraps=play_service.read_json):
            config = self.root / "config.json"
            config.write_text(json.dumps({"owner": "owner"}), encoding="utf-8")
            self.assertEqual(1, play_service.main([str(config)]))
        row = play_service.read_json(self.state)
        self.assertEqual("e7", row["epoch"])
        self.assertEqual("boom", row["error"])
        self.assertFalse(row["ready"])


    def test_a_guard_stop_is_recorded_in_the_service_state(self):
        """The service exits on the refused heartbeat; the file must say why."""
        now = [0.0]
        def clock():
            now[0] += 11.0
            return now[0]
        def call(args):
            if args["op"] == "status":
                return {"success": True, "newestCursor": 0, "epoch": "e1",
                        "active": False, "stopReason": "colonist_injury",
                        "stopDetail": "Longhoff was injured (injuries 2 -> 3)."}
            if args["op"] == "start":
                return {"success": True, "active": True, "epoch": "e1"}
            if args["op"] == "heartbeat":
                return {"success": False, "message": "no active supervisor"}
            if args["op"] == "events":
                return {"success": True, "events": [], "nextCursor": 1}
            return {"success": True}
        config = {"binding": self.binding, "generation": "g1", "owner": "owner",
                  "speed": "Normal", "leaseMs": 30000}
        play_service.run(config, call=call, runtime_path=self.runtime,
                         state_path=self.state, stop_path=self.stop,
                         alive=lambda _: True, clock=clock, sleeper=lambda _: None,
                         publisher=lambda *a, **k: {"accepted": True})
        row = play_service.read_json(self.state)
        self.assertEqual("guard", row["stopKind"])
        self.assertEqual("colonist_injury", row["stopReason"])
        self.assertIn("Longhoff was injured", row["stopDetail"])
        self.assertFalse(row["ready"])
        self.assertIsNotNone(row["stoppedAt"])

    def test_a_stale_epoch_reason_is_not_borrowed(self):
        def call(args):
            if args["op"] == "status":
                return {"success": True, "newestCursor": 0, "epoch": "e9",
                        "stopReason": "hostile", "stopDetail": "someone else"}
            if args["op"] == "start":
                return {"success": True, "active": True, "epoch": "e1"}
            if args["op"] == "events":
                return {"success": True, "events": [], "nextCursor": 1}
            return {"success": True}
        config = {"binding": self.binding, "generation": "g1", "owner": "owner",
                  "speed": "Normal", "leaseMs": 30000}
        play_service.run(config, call=call, runtime_path=self.runtime,
                         state_path=self.state, stop_path=self.stop,
                         alive=mock.Mock(side_effect=[True, False]),
                         sleeper=lambda _: None)
        row = play_service.read_json(self.state)
        self.assertIsNone(row["stopReason"])
        self.assertEqual("binding-lost", row["stopKind"])

    def test_a_stop_file_exit_is_recorded_as_such(self):
        self.stop.touch()
        def call(args):
            if args["op"] == "status":
                return {"success": True, "newestCursor": 0}
            if args["op"] == "start":
                return {"success": True, "active": True, "epoch": "e1"}
            if args["op"] == "events":
                return {"success": True, "events": [], "nextCursor": 1}
            return {"success": True}
        config = {"binding": self.binding, "generation": "g1", "owner": "owner",
                  "speed": "Normal", "leaseMs": 30000}
        play_service.run(config, call=call, runtime_path=self.runtime,
                         state_path=self.state, stop_path=self.stop,
                         alive=lambda _: True, sleeper=lambda _: None)
        self.assertEqual("stop-file",
                         play_service.read_json(self.state)["stopKind"])

    def test_stop_kind_prefers_the_companion_reason(self):
        self.assertEqual("requested",
                         play_service.stop_kind("requested_pause", "stop-file"))
        self.assertEqual("lease", play_service.stop_kind("lease_expired", "guard"))
        self.assertEqual("guard", play_service.stop_kind("colonist_injury", "stop-file"))
        self.assertEqual("stop-file", play_service.stop_kind(None, "stop-file"))
        self.assertEqual("error", play_service.stop_kind(None, "error"))

    def test_start_call_carries_the_injury_cooldown_and_acknowledgement(self):
        seen = []
        rows = [{"pawnId": 77, "pawnName": "Longhoff", "reason": "cooldown",
                 "secondsRemaining": 170}]
        def call(args):
            seen.append(args)
            if args["op"] == "status":
                return {"success": True, "newestCursor": 0}
            if args["op"] == "start":
                return {"success": True, "active": True, "epoch": "e1",
                        "suppressedInjuryPawns": rows}
            if args["op"] == "events":
                return {"success": True, "events": [], "nextCursor": 1}
            return {"success": True}
        config = {"binding": self.binding, "generation": "g1", "owner": "owner",
                  "speed": "Normal", "leaseMs": 30000,
                  "ignoredInjuredColonistIds": "77", "injuryStopCooldownMs": 180000}
        play_service.run(config, call=call, runtime_path=self.runtime,
                         state_path=self.state, stop_path=self.stop,
                         alive=mock.Mock(side_effect=[True, False]),
                         sleeper=lambda _: None)
        started = [x for x in seen if x["op"] == "start"][0]
        self.assertEqual("77", started["ignoredInjuredColonistIds"])
        self.assertEqual(180000, started["injuryStopCooldownMs"])
        self.assertEqual(rows,
                         play_service.read_json(self.state)["suppressedInjuryPawns"])

    def test_an_old_config_still_gets_the_default_cooldown(self):
        seen = []
        def call(args):
            seen.append(args)
            if args["op"] == "status":
                return {"success": True, "newestCursor": 0}
            if args["op"] == "start":
                return {"success": True, "active": True, "epoch": "e1"}
            if args["op"] == "events":
                return {"success": True, "events": [], "nextCursor": 1}
            return {"success": True}
        config = {"binding": self.binding, "generation": "g1", "owner": "owner",
                  "speed": "Normal", "leaseMs": 30000}
        play_service.run(config, call=call, runtime_path=self.runtime,
                         state_path=self.state, stop_path=self.stop,
                         alive=mock.Mock(side_effect=[True, False]),
                         sleeper=lambda _: None)
        started = [x for x in seen if x["op"] == "start"][0]
        self.assertEqual(play_service.DEFAULT_INJURY_COOLDOWN_MS,
                         started["injuryStopCooldownMs"])
        self.assertEqual("", started["ignoredInjuredColonistIds"])


class PlayCliTests(unittest.TestCase):
    def test_start_refuses_without_registered_runtime(self):
        args = argparse.Namespace(speed="Normal", mode="colony",
                                  ignore_hostile=[], allow_downed=[])
        with mock.patch.object(play.play_service, "binding", return_value=None), \
             self.assertRaisesRegex(RuntimeError, "runtime binding unavailable"):
            play.start(args)

    def test_start_is_idempotent_and_spawns_nothing_when_the_clock_runs(self):
        args = argparse.Namespace(speed="Normal", mode="colony",
                                  ignore_hostile=[], allow_downed=[])
        existing = {"ready": True, "pid": 99, "processStarted": "ft",
                    "owner": "o", "epoch": "e",
                    "binding": self_binding()}
        out = io.StringIO()
        with mock.patch.object(play.play_service, "binding",
             return_value=self_binding()), \
             mock.patch.object(play.play_service, "binding_alive", return_value=True), \
             mock.patch.object(play.play_service, "pid_alive", return_value=True), \
             mock.patch("runtime_binding.process_identity", return_value="ft"), \
             mock.patch.object(play.play_service, "read_json", return_value=existing), \
             mock.patch("sys.stdout", out), \
             mock.patch.object(play.subprocess, "Popen") as popen:
            self.assertEqual(0, play.start(
                args, status=lambda: {"active": True, "requestedSpeed": "Fast"}))
        popen.assert_not_called()
        self.assertIn("the clock is RUNNING at Fast", out.getvalue())

    def test_start_over_an_owned_but_paused_session_starts_a_new_epoch(self):
        # WEIRD 10: `combat.py` pauses as stage 1, the guard stops, and the
        # service holds the epoch until its next heartbeat. `already owned`
        # over a paused game is the answer that cost the stream its dead air.
        existing = {"ready": True, "pid": 99, "processStarted": "ft",
                    "owner": "o", "epoch": 12, "binding": self_binding()}
        out = io.StringIO()
        with mock.patch.object(play, "companion_status",
                               return_value={"active": False,
                                             "stopReason": "external_pause"}), \
             mock.patch.object(play, "retire_service", return_value=True) as retire, \
             mock.patch("sys.stdout", out):
            self.assertIs(False, play.owned_session(existing))
        retire.assert_called_once()
        self.assertIn("left the clock PAUSED", out.getvalue())
        self.assertIn("Retiring it and starting a new epoch", out.getvalue())

    def test_a_service_that_will_not_retire_names_the_two_commands(self):
        existing = {"ready": True, "pid": 99, "owner": "o", "epoch": 12}
        with mock.patch.object(play, "companion_status",
                               return_value={"active": False}), \
             mock.patch.object(play, "retire_service", return_value=False), \
             mock.patch("sys.stdout", new_callable=io.StringIO), \
             self.assertRaisesRegex(RuntimeError, "python play.py pause"):
            play.owned_session(existing)

    def test_an_unread_companion_is_never_reported_as_running(self):
        existing = {"ready": True, "pid": 99, "owner": "o", "epoch": 12}
        with self.assertRaisesRegex(RuntimeError, "whether the clock is RUNNING "
                                                  "is unknown"):
            play.owned_session(existing, status=lambda: None)

    def test_retire_service_stops_the_file_and_waits_for_the_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            with mock.patch.object(play, "STATE", state), \
                 mock.patch.object(play, "STOP", state / "play-service.stop"), \
                 mock.patch.object(play.play_service, "read_json",
                                   return_value={"ready": True, "pid": 1}), \
                 mock.patch.object(play, "service_alive",
                                   side_effect=[True, False]):
                self.assertTrue(play.retire_service(clock=lambda: 0.0,
                                                    sleeper=lambda _s: None))
            self.assertTrue((state / "play-service.stop").exists())

    def test_a_service_that_never_exits_is_reported_not_waited_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            ticks = iter([0.0, 0.0, 9.0])
            with mock.patch.object(play, "STATE", state), \
                 mock.patch.object(play, "STOP", state / "play-service.stop"), \
                 mock.patch.object(play.play_service, "read_json",
                                   return_value={"ready": True, "pid": 1}), \
                 mock.patch.object(play, "service_alive", return_value=True):
                self.assertFalse(play.retire_service(clock=lambda: next(ticks),
                                                     sleeper=lambda _s: None))

    def test_speed_accepts_the_numbers_rimworld_itself_uses(self):
        self.assertEqual("Normal", play.speed_name("1"))
        self.assertEqual("Fast", play.speed_name("2"))
        self.assertEqual("Superfast", play.speed_name("3"))
        self.assertEqual("Superfast", play.speed_name("superfast"))
        self.assertEqual("Fast", play.speed_name("Fast"))
        with self.assertRaisesRegex(argparse.ArgumentTypeError, "1=Normal"):
            play.speed_name("4")

    def test_ignore_hostile_all_names_every_current_threat(self):
        rows = [{"thingId": "Wolf1", "category": "hostile", "blocks": True,
                 "name": "Kade"},
                {"thingId": "Bear2", "category": "predator_hunt", "blocks": True,
                 "name": "bear"}]
        ids, line = play.acknowledged_hostiles(["all"], threats=rows)
        self.assertEqual(["Wolf1", "Bear2"], ids)
        self.assertIn("acknowledging 2 current threat(s)", line)
        self.assertIn("Wolf1 (hostile)", line)
        self.assertIn("One that arrives later still stops the clock.", line)
        # An explicit id alongside `all` is kept, and nothing is duplicated.
        ids, _ = play.acknowledged_hostiles(["Wolf1", "all"], threats=rows)
        self.assertEqual(["Wolf1", "Bear2"], ids)
        # No `all`: no read, no line.
        self.assertEqual((["Wolf1"], None), play.acknowledged_hostiles(["Wolf1"]))
        # `all` with nothing on the map says so rather than claiming a list.
        ids, line = play.acknowledged_hostiles(["all"], threats=[])
        self.assertEqual([], ids)
        self.assertIn("no hostile and no hunting predator", line)
        self.assertIn("nothing was acknowledged", line)

    def test_a_non_blocking_predator_is_not_acknowledged_by_all(self):
        """`all` means "everything that would stop me", not "every animal".

        A wolverine eating a hare is listed by `blocking_threats` so the
        operator can see it was considered, and must not end up in the
        acknowledged set: acknowledging it would suppress the stop if it
        LATER turned on a colonist.
        """
        rows = [{"thingId": "Kade1", "category": "hostile", "blocks": True},
                {"thingId": "Wolverine9", "category": "predator", "blocks": False}]
        ids, line = play.acknowledged_hostiles(["all"], threats=rows)
        self.assertEqual(["Kade1"], ids)
        self.assertIn("acknowledging 1 current threat(s)", line)
        self.assertNotIn("Wolverine9", line)


class IgnorePredatorFlagTests(unittest.TestCase):
    """2026-09-08, Threadneedle: "a single wolverine stopped the clock on four
    separate nights", and `--ignore-hostile` with no value was an argparse
    error -- so the one flag the refusal printed could not be typed bare."""

    ROWS = [{"thingId": "Thing_Wolverine9", "category": "predator_hunt",
             "blocks": True, "name": "wolverine", "defName": "Wolverine"},
            {"thingId": "Thing_Lynx4", "category": "predator", "blocks": False,
             "name": "lynx", "defName": "Lynx"},
            {"thingId": "Thing_Human7", "category": "hostile", "blocks": True,
             "name": "Kade", "defName": "Human"}]

    def parse(self, argv):
        return play.parser().parse_args(argv)

    def test_bare_ignore_hostile_parses_and_means_all(self):
        self.assertEqual(["all"], self.parse(
            ["start", "--ignore-hostile"]).ignore_hostile)
        # The named form is untouched, and repeats still accumulate.
        self.assertEqual(["Thing_Human7", "all"], self.parse(
            ["start", "--ignore-hostile", "Thing_Human7",
             "--ignore-hostile", "all"]).ignore_hostile)
        # A bare flag followed by another option does not eat it.
        args = self.parse(["start", "--ignore-hostile", "--speed", "Fast"])
        self.assertEqual(["all"], args.ignore_hostile)
        self.assertEqual("Fast", args.speed)

    def test_bare_ignore_predator_parses_and_means_all(self):
        self.assertEqual(["all"], self.parse(
            ["start", "--ignore-predator"]).ignore_predator)
        self.assertEqual(["wolverine"], self.parse(
            ["start", "--ignore-predator", "wolverine"]).ignore_predator)
        self.assertEqual([], self.parse(["start"]).ignore_predator)

    def test_an_id_is_anything_ending_in_digits_like_the_companion_parses_it(self):
        self.assertTrue(play.is_thing_id("Thing_Wolverine9"))
        self.assertTrue(play.is_thing_id("9"))
        self.assertFalse(play.is_thing_id("wolverine"))
        self.assertFalse(play.is_thing_id("all"))

    def test_all_predators_is_passed_through_as_the_literal_word(self):
        """Unlike hostiles, this is a whole-epoch decision about a CATEGORY:
        a snapshot of ids would not cover the wolverine that wanders back."""
        ids, line = play.acknowledged_predators(["all"], threats=self.ROWS)
        self.assertEqual(["all"], ids)
        self.assertIn("including ones that arrive later", line)
        self.assertIn("manhunter still", line)

    def test_a_predator_named_on_the_command_line_resolves_to_its_id(self):
        ids, line = play.acknowledged_predators(["wolverine"], threats=self.ROWS)
        self.assertEqual(["Thing_Wolverine9"], ids)
        self.assertIn("Thing_Wolverine9", line)
        # An id is passed straight through, and needs no map read at all.
        self.assertEqual((["Thing_Wolverine9"], None)[0],
                         play.acknowledged_predators(["Thing_Wolverine9"],
                                                     threats=self.ROWS)[0])
        # Nothing asked for, nothing read, nothing said.
        self.assertEqual(([], None), play.acknowledged_predators([]))

    def test_ignore_predator_refuses_to_acknowledge_a_raider_by_name(self):
        with self.assertRaisesRegex(RuntimeError, "no such thing on the map"):
            play.acknowledged_predators(["Kade"], threats=self.ROWS)

    def test_a_name_that_matches_nothing_lists_what_is_there(self):
        with self.assertRaisesRegex(RuntimeError, "wolverine Thing_Wolverine9"):
            play.acknowledged_hostiles(["bear"], threats=self.ROWS)

    def test_an_ambiguous_name_refuses_rather_than_picking_one(self):
        rows = [{"thingId": "W1", "category": "predator_hunt", "blocks": True,
                 "name": "wolverine", "defName": "Wolverine"},
                {"thingId": "W2", "category": "predator_hunt", "blocks": True,
                 "name": "wolverine", "defName": "Wolverine"}]
        with self.assertRaisesRegex(RuntimeError, "matches 2 things"):
            play.acknowledged_predators(["wolverine"], threats=rows)

    def test_start_plumbs_both_ignore_sets_into_the_service_config(self):
        args = argparse.Namespace(speed="Normal", mode="colony",
                                  ignore_hostile=[], ignore_predator=["all"],
                                  allow_downed=[], allow_injured=[],
                                  injury_cooldown=180.0, min_health=0.5)
        written = []
        def read_json(_path):
            if not written:
                return {}
            return {"owner": written[-1]["owner"], "ready": True, "epoch": 3,
                    "pid": 1, "baselineAlerts": []}
        with mock.patch.object(play.play_service, "binding",
                               return_value=self_binding()), \
             mock.patch.object(play.play_service, "binding_alive", return_value=True), \
             mock.patch.object(play.play_service, "atomic_json",
                               side_effect=lambda _p, v: written.append(v)), \
             mock.patch.object(play.play_service, "read_json", read_json), \
             mock.patch.object(play.game_session, "current",
                               return_value={"generation": "g1"}), \
             mock.patch.object(play, "blocking_threats") as read_map, \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out, \
             mock.patch.object(play.subprocess, "Popen"):
            self.assertEqual(0, play.start(args, clock=lambda: 0.0,
                                           sleeper=lambda _s: None))
        # `all` needs no map read on the predator path: it is the literal word.
        read_map.assert_not_called()
        self.assertEqual("all", written[0]["ignoredPredatorIds"])
        self.assertEqual("", written[0]["ignoredHostileIds"])
        self.assertIn("--ignore-predator all", out.getvalue())

    def test_speed_uses_recorded_owner_and_epoch(self):
        state = {"ready": True, "pid": 99, "processStarted": "ft",
                 "owner": "owner-1", "epoch": 12, "binding": self_binding()}
        with mock.patch.object(play.play_service, "read_json", return_value=state), \
             mock.patch.object(play, "service_alive", return_value=True), \
             mock.patch.object(play.rim, "init"), \
             mock.patch.object(play.rim, "game",
                               return_value={"success": True, "active": True}) as game, \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(0, play.speed("Fast"))
        self.assertEqual({"op": "speed", "epoch": 12,
                          "owner": "owner-1", "speed": "Fast"},
                         game.call_args.args[1])


    def test_start_plumbs_acknowledged_injuries_and_the_cooldown(self):
        args = argparse.Namespace(speed="Normal", mode="colony", ignore_hostile=[],
                                  allow_downed=[],
                                  allow_injured=["Pawn_Longhoff77", "88"],
                                  injury_cooldown=180.0)
        rows = [{"pawnId": 77, "pawnName": "Longhoff", "reason": "cooldown",
                 "secondsRemaining": 170}]
        written = []
        out = io.StringIO()
        def read_json(_path):
            if not written:
                return {}
            return {"owner": written[-1]["owner"], "ready": True, "epoch": 52,
                    "pid": 1, "baselineAlerts": [], "suppressedInjuryPawns": rows}
        with mock.patch.object(play.play_service, "binding",
                               return_value=self_binding()), \
             mock.patch.object(play.play_service, "binding_alive", return_value=True), \
             mock.patch.object(play.play_service, "atomic_json",
                               side_effect=lambda _p, v: written.append(v)), \
             mock.patch.object(play.play_service, "read_json", read_json), \
             mock.patch.object(play.game_session, "current",
                               return_value={"generation": "g1"}), \
             mock.patch.object(play.subprocess, "Popen"), \
             mock.patch("sys.stdout", out):
            self.assertEqual(0, play.start(args, clock=lambda: 0.0,
                                           sleeper=lambda _: None))
        config = written[-1]
        self.assertEqual("Pawn_Longhoff77,88", config["ignoredInjuredColonistIds"])
        self.assertEqual(180000, config["injuryStopCooldownMs"])
        self.assertIn("injury stops suppressed for 170 s: Longhoff (a stop for "
                      "that pawn is still in cooldown; going down or dying still "
                      "stops)", out.getvalue())

    def test_suppressed_injury_line_reads_for_a_stream(self):
        self.assertEqual("", play.suppressed_injuries(None))
        self.assertEqual("", play.suppressed_injuries([]))
        self.assertEqual(
            "injury stops suppressed for 170 s: Longhoff (a stop for that pawn "
            "is still in cooldown; going down or dying still stops)",
            play.suppressed_injuries([{"pawnName": "Longhoff", "pawnId": 77,
                                       "reason": "cooldown",
                                       "secondsRemaining": 170}]))
        self.assertEqual(
            "injury stops acknowledged: Longhoff",
            play.suppressed_injuries([{"pawnName": "Longhoff", "pawnId": 77,
                                       "reason": "acknowledged",
                                       "secondsRemaining": 0}]))
        # WEIRD 3: a pawn already under the floor used to refuse the start
        # outright. They are acknowledged at start and the line says so.
        self.assertEqual(
            "already under the 0.50 health floor at start, so their injuries "
            "are acknowledged: Ian (going down or dying still stops the clock)",
            play.suppressed_injuries([{"pawnName": "Ian", "pawnId": 88,
                                       "reason": "below_health_floor",
                                       "minHealthFraction": 0.5,
                                       "healthFraction": 0.43,
                                       "secondsRemaining": 0}]))

    def test_status_summary_names_the_guard_after_the_service_exits(self):
        self.assertEqual(
            "STOPPED by guard colonist_injury: Longhoff was injured. "
            "(service exited; epoch 51)",
            play.summary_line({"active": False, "epoch": 51,
                               "stopReason": "colonist_injury",
                               "stopDetail": "Longhoff was injured."},
                              {"epoch": 51, "stopKind": "guard"}, False))
        # The companion forgets on a reload; the service file still knows.
        self.assertIn("colonist_injury", play.summary_line(
            None, {"epoch": 51, "stopKind": "guard",
                   "stopReason": "colonist_injury", "stopDetail": "d"}, False))
        self.assertEqual("RUNNING (epoch 7, Fast)", play.summary_line(
            {"active": True, "epoch": 7, "requestedSpeed": "Fast"}, {}, True))
        self.assertIn("RUNNING UNSUPERVISED", play.summary_line(
            {"active": True, "epoch": 7, "requestedSpeed": "Fast"}, {}, False))
        self.assertEqual("IDLE (no supervised-play epoch on record)",
                         play.summary_line({}, {}, False))
        # A service that died before the companion had a reason of its own.
        self.assertEqual(
            "STOPPED by error service error: host is gone (service exited; epoch 51)",
            play.summary_line("tool not found", {"epoch": 51, "stopKind": "error",
                                                "error": "host is gone"}, False))

    def test_status_prints_the_human_line_above_the_json(self):
        service = {"epoch": 51, "stopKind": "guard", "ready": False,
                   "stopReason": "colonist_injury", "stopDetail": "Longhoff hurt"}
        out = io.StringIO()
        with mock.patch.object(play.rim, "init"), \
             mock.patch.object(play.rim, "game", return_value={"active": False}), \
             mock.patch.object(play.play_service, "read_json", return_value=service), \
             mock.patch.object(play.play_service, "binding", return_value=None), \
             mock.patch("sys.stdout", out):
            self.assertEqual(0, play.status())
        lines = out.getvalue().splitlines()
        self.assertTrue(lines[0].startswith("STOPPED by guard colonist_injury:"))
        self.assertEqual("colonist_injury",
                         json.loads("\n".join(lines[1:]))["service"]["stopReason"])

    def test_service_liveness_tolerates_a_numeric_identity_and_says_why(self):
        row = {"ready": True, "pid": 99, "processStarted": 134332130617102431}
        with mock.patch("runtime_binding.process_identity",
                        return_value="134332130617102431"):
            alive, why = play.service_state(row)
        self.assertTrue(alive)
        self.assertIn("alive", why)
        with mock.patch("runtime_binding.process_identity", return_value=None):
            alive, why = play.service_state(row)
        self.assertFalse(alive)
        self.assertIn("is gone", why)
        with mock.patch("runtime_binding.process_identity", return_value="other"):
            alive, why = play.service_state(row)
        self.assertFalse(alive)
        self.assertIn("was reused", why)
        self.assertIn("ready: false", play.service_state({"ready": False})[1])
        self.assertIn("no service state", play.service_state(None)[1])
        self.assertIn("no process identity",
                      play.service_state({"ready": True, "pid": 1})[1])


def self_binding():
    return {"session_id": "s1", "host_pid": 99, "host_started": "ft"}


if __name__ == "__main__":
    unittest.main()
