import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import game_session
import stream
import turnclock


class StreamLifecycleTests(unittest.TestCase):
    def test_reset_explicitly_clears_unresolved_native_turn(self):
        state = {"turn": 4, "handsStartedAt": 100, "handsAgent": "gone"}
        saved = []
        with mock.patch.object(stream, "load", return_value=state), \
             mock.patch.object(stream, "save", side_effect=saved.append), \
             mock.patch.object(stream.ov, "post", return_value=(True, None)), \
             mock.patch.dict(sys.modules, {"rota_service": mock.Mock()}), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(0, stream.cmd_reset(None))
        self.assertEqual(0, saved[0]["turn"])
        self.assertNotIn("handsStartedAt", saved[0])
        self.assertNotIn("handsAgent", saved[0])

    def test_hands_start_refuses_while_native_turn_is_open(self):
        args = mock.Mock(goal=None)
        state = {"turn": 4, "handsStartedAt": 100, "handsAgent": "agent-7"}
        with mock.patch.object(stream, "load", return_value=state), \
             mock.patch.object(stream.time, "time", return_value=120), \
             mock.patch.object(stream, "save") as save, \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(1, stream.cmd_hands_start(args))
        save.assert_not_called()
        self.assertIn("still has a native Hands running", out.getvalue())

    def test_hands_start_does_not_treat_age_as_proof_of_exit(self):
        args = mock.Mock(goal=None)
        state = {"turn": 4, "handsStartedAt": 100, "handsAgent": "agent-7"}
        with mock.patch.object(stream, "load", return_value=state), \
             mock.patch.object(stream.time, "time", return_value=100 + 10 * 3600), \
             mock.patch.object(stream, "save") as save, \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(1, stream.cmd_hands_start(args))
        save.assert_not_called()

    def test_core_handback_refuses_intermediate_report(self):
        args = mock.Mock(summary="draft", mood=None, short=None, long=None)
        state = {"turn": 4, "handsStartedAt": 100, "handsAgent": "agent-7"}
        with mock.patch.object(stream, "load", return_value=state), \
             mock.patch.object(stream.time, "time", return_value=120), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(1, stream.cmd_handback(args))
        self.assertIn("intermediate report", out.getvalue())

    def test_hands_check_ignores_intermediate_report_write(self):
        with mock.patch.object(stream.turnclock, "handed_back", return_value=(False, 42)), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(0, stream.cmd_hands_check(None))
        self.assertIn("fork still running", out.getvalue())

    def test_hands_check_reports_native_stop(self):
        with mock.patch.object(stream.turnclock, "handed_back", return_value=(True, 3)), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(0, stream.cmd_hands_check(None))
        self.assertIn("native stop completed", out.getvalue())

    def test_hands_check_reports_old_unclosed_turn_as_unresolved(self):
        old = stream.turnclock.MAX_AGE + 1
        with mock.patch.object(stream.turnclock, "handed_back", return_value=(False, old)), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(0, stream.cmd_hands_check(None))
        self.assertIn("STATE UNRESOLVED", out.getvalue())
        self.assertIn("do not", out.getvalue())

    @staticmethod
    def binding_mock(state="live", line="runtime binding is live: session s1"):
        return mock.Mock(load=lambda: {"session_id": "s1"},
                         note=lambda: {"lastCoreSession": "s1"},
                         describe=lambda session=None: (state, line))

    def test_claim_verifies_hook_owned_route(self):
        bus = mock.Mock(current_recipient=lambda session: "agent-7")
        with mock.patch.dict(sys.modules, {"event_bus": bus,
                                           "runtime_binding": self.binding_mock()}), \
             mock.patch.object(stream, "load", return_value={"turn": 3}), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(0, stream.cmd_hands_claim(None))
        self.assertIn("agent-7", out.getvalue())

    def test_claim_blames_the_stale_binding_not_the_hook(self):
        """The 2026-09-07 refusal named the hook; the cause was a dead PID."""
        bus = mock.Mock(current_recipient=lambda session: "core")
        dead = self.binding_mock("dead", "runtime binding is STALE: it names "
                                         "session old-1 on host pid 33188, and "
                                         "that process is GONE.")
        with mock.patch.dict(sys.modules, {"event_bus": bus,
                                           "runtime_binding": dead}), \
             mock.patch.object(stream, "load", return_value={"turn": 3}), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(1, stream.cmd_hands_claim(None))
        text = out.getvalue()
        self.assertIn("STALE", text)
        self.assertIn("33188", text)
        self.assertNotIn("did not bind this agent", text)
        self.assertIn("Hands CANNOT fix this", text)
        self.assertIn("bind-check --repair", text)

    def test_claim_blames_the_hook_only_when_the_binding_is_live(self):
        bus = mock.Mock(current_recipient=lambda session: "core")
        with mock.patch.dict(sys.modules, {"event_bus": bus,
                                           "runtime_binding": self.binding_mock()}), \
             mock.patch.object(stream, "load", return_value={"turn": 3}), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(1, stream.cmd_hands_claim(None))
        text = out.getvalue()
        self.assertIn("route to `core`", text)
        self.assertIn("register", text)

    def test_release_pauses_before_clearing_route(self):
        order = []
        bus = mock.Mock(current_recipient=lambda session: "agent-7",
                        handback=lambda *a: order.append("handback"))
        binding = mock.Mock(load=lambda: {"session_id": "s1"})
        play = mock.Mock()
        play.SERVICE_STATE = "state"
        play.play_service.read_json.return_value = {"ready": True}
        play.pause.side_effect = lambda: order.append("pause") or 0
        with mock.patch.dict(sys.modules, {"event_bus": bus,
                                           "runtime_binding": binding,
                                           "play": play}), \
             mock.patch.object(stream, "load", return_value={"turn": 3}), \
             mock.patch.object(stream, "save"), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(0, stream.cmd_hands_release(None))
        self.assertEqual(["pause", "handback"], order)

    def test_release_does_not_claim_the_still_live_fork_has_stopped(self):
        bus = mock.Mock(current_recipient=lambda session: "agent-7")
        binding = mock.Mock(load=lambda: {"session_id": "s1"})
        play = mock.Mock(pause=lambda: 0)
        with mock.patch.dict(sys.modules, {"event_bus": bus,
                                           "runtime_binding": binding,
                                           "play": play}), \
             mock.patch.object(stream, "load", return_value={"turn": 3,
                                                               "handsStartedAt": 10}), \
             mock.patch.object(stream, "save") as save, \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(0, stream.cmd_hands_release(None))
        save.assert_not_called()

    def test_release_does_not_clear_route_when_pause_fails(self):
        bus = mock.Mock(current_recipient=lambda session: "agent-7")
        binding = mock.Mock(load=lambda: {"session_id": "s1"})
        play = mock.Mock()
        play.SERVICE_STATE = "state"
        play.play_service.read_json.return_value = {"ready": True}
        play.pause.return_value = 1
        with mock.patch.dict(sys.modules, {"event_bus": bus,
                                           "runtime_binding": binding,
                                           "play": play}), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(1, stream.cmd_hands_release(None))
        bus.handback.assert_not_called()

    def test_release_pauses_even_when_daemon_is_dead(self):
        order = []
        bus = mock.Mock(current_recipient=lambda session: "agent-7",
                        handback=lambda *a: order.append("handback"))
        binding = mock.Mock(load=lambda: {"session_id": "s1"})
        play = mock.Mock()
        play.pause.side_effect = lambda: order.append("pause") or 0
        with mock.patch.dict(sys.modules, {"event_bus": bus,
                                           "runtime_binding": binding,
                                           "play": play}), \
             mock.patch.object(stream, "load", return_value={"turn": 3}), \
             mock.patch.object(stream, "save"), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(0, stream.cmd_hands_release(None))
        self.assertEqual(["pause", "handback"], order)

    def test_core_handback_recovers_route_before_closing(self):
        order = []
        bus = mock.Mock(current_recipient=lambda session: "dead-hands",
                        handback=lambda *a: order.append("route"))
        binding = mock.Mock(load=lambda: {"session_id": "s1"})
        play = mock.Mock()
        play.pause.side_effect = lambda: order.append("pause") or 0
        args = mock.Mock(summary="done", mood="happy", short=None, long=None)
        with mock.patch.dict(sys.modules, {"event_bus": bus,
                                           "runtime_binding": binding,
                                           "play": play}), \
             mock.patch.object(stream, "load", return_value={"turn": 3}), \
             mock.patch.object(stream, "save", side_effect=lambda *_: order.append("save")), \
             mock.patch.object(stream.turnclock, "turn_elapsed", return_value=1), \
             mock.patch.object(stream.ov, "post", return_value=(True, None)), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(0, stream.cmd_handback(args))
        self.assertEqual(["pause", "route"], order[:2])


class HandsCloseTests(unittest.TestCase):
    """`hands-close`: stamp the lifecycle, and be certain nothing else moves.

    The whole point of the command is what it does NOT do. `reset` was the only
    cure for a stuck turn before this, and it also zeroed the counter, cleared
    the pin and the feed and stopped the rota -- three separate repairs after
    every recovery, twice in one stream.
    """

    LIVE = {"turn": 20, "last_human_ts": 1788588022.478, "mode": "live",
            "mode_ts": 1788732246.687, "handsStartedAt": 1788739417.975}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.file = self.dir / "stream.json"
        for target, name, value in (
                (stream, "STATE", self.file),
                (turnclock, "STATE", str(self.file)),
                (turnclock, "HEARTBEAT", str(self.dir / "hands-heartbeat.json")),
                (turnclock, "REPORT", str(self.dir / "hands-last.md")),
                (game_session, "STATE", self.dir)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.write(**self.LIVE)
        # hands-close pushes the overlay phase back to `core` and NOTHING else:
        # the label used to survive the close, so `status` printed `no turn
        # open` and `phase : hands (turn 1)` in one output. Any other post is
        # still the `reset` behaviour this command exists to avoid.
        self.posts = []

        def record(path, **kw):
            self.posts.append((path, kw))
            if path != "/status" or kw.get("phase") != "core":
                raise AssertionError("hands-close must post only the phase: %s %r"
                                     % (path, kw))
            return True, None

        self.post = mock.patch.object(stream.ov, "post", side_effect=record)
        self.post.start()
        self.addCleanup(self.post.stop)

    def write(self, **state):
        self.file.write_text(json.dumps(state), encoding="utf-8")

    def beat(self, age, agent="abcc45a81727a5e5c"):
        (self.dir / "hands-heartbeat.json").write_text(json.dumps(
            {"session": "s", "agent": agent, "turn": 20, "at": time.time() - age}))

    def run_close(self, reason=None, force=False):
        args = mock.Mock(reason=reason, force=force)
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = stream.cmd_hands_close(args)
        return code, out.getvalue()

    def test_it_stamps_the_lifecycle_and_leaves_every_other_key_alone(self):
        code, out = self.run_close("fork gone: TaskStop, no SubagentStop")
        self.assertEqual(0, code)
        state = json.loads(self.file.read_text())
        self.assertGreaterEqual(state["handsEndedAt"], state["handsStartedAt"])
        self.assertEqual("core-forced", state["handsEndedBy"])
        self.assertEqual("fork gone: TaskStop, no SubagentStop",
                         state["handsClosedReason"])
        # Everything reset would have destroyed:
        for key, value in self.LIVE.items():
            self.assertEqual(value, state[key])
        self.assertIn("CLOSED turn 20", out)
        self.assertIn("turn counter (still 20)", out)

    def test_it_pushes_only_the_phase_and_never_the_rota_service(self):
        rota = mock.Mock()
        with mock.patch.dict(sys.modules, {"rota_service": rota}):
            self.assertEqual(0, self.run_close()[0])
        rota.stop.assert_not_called()          # any other `ov.post` raises
        self.assertEqual([("/status", {"timeout": stream.FAST,
                                       "phase": "core", "turn": 20})], self.posts)

    def test_a_closed_turn_is_reported_not_reclosed(self):
        self.write(**dict(self.LIVE, handsEndedAt=self.LIVE["handsStartedAt"] + 5,
                          handsEndedBy="fork-20"))
        code, out = self.run_close()
        self.assertEqual(0, code)
        self.assertIn("NOTHING TO CLOSE", out)
        self.assertIn("fork-20", out)
        self.assertEqual(self.LIVE["handsStartedAt"] + 5,
                         json.loads(self.file.read_text())["handsEndedAt"])

    def test_no_open_turn_says_which_turn_would_be_next(self):
        self.write(turn=20, mode="live")
        code, out = self.run_close()
        self.assertEqual(0, code)
        self.assertIn("no turn is open", out)
        self.assertIn("turn 21", out)

    def test_it_refuses_against_positive_evidence_that_the_fork_is_alive(self):
        self.beat(age=8)
        code, out = self.run_close("it looks stuck")
        self.assertEqual(1, code)
        self.assertIn("HANDS CLOSE REFUSED", out)
        self.assertIn("that fork is ALIVE", out)
        self.assertNotIn("handsEndedAt", json.loads(self.file.read_text()))

    def test_force_overrides_a_live_heartbeat_and_says_so_in_the_state(self):
        self.beat(age=8)
        self.assertEqual(0, self.run_close("M says it is gone", force=True)[0])
        self.assertEqual("core-forced",
                         json.loads(self.file.read_text())["handsEndedBy"])

    def test_a_stale_heartbeat_closes_and_names_the_dead_agent(self):
        self.beat(age=turnclock.LIVE_WITHIN + 300)
        code, out = self.run_close()
        self.assertEqual(0, code)
        self.assertIn("abcc45a81727a5e5c had touched nothing", out)

    def test_hands_start_refusal_points_at_the_recovery(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(1, stream.cmd_hands_start(mock.Mock(goal=None)))
        self.assertIn("hands-close", out.getvalue())

    def test_handback_refusal_points_at_the_recovery(self):
        args = mock.Mock(summary="draft", mood=None, short=None, long=None)
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(1, stream.cmd_handback(args))
        self.assertIn("hands-close", out.getvalue())

    def test_hands_check_on_a_stale_turn_names_the_recovery(self):
        self.write(**dict(self.LIVE, handsStartedAt=time.time() - turnclock.MAX_AGE - 60))
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(0, stream.cmd_hands_check(None))
        self.assertIn("STATE UNRESOLVED", out.getvalue())
        self.assertIn("hands-close", out.getvalue())


class HandsReleasePauseTests(unittest.TestCase):
    """Releasing must not freeze the picture for a fork that is already gone."""

    def release(self, no_pause=False, alive=(None, {})):
        order = []
        bus = mock.Mock(current_recipient=lambda session: "agent-7",
                        handback=lambda *a: order.append("handback"))
        binding = mock.Mock(load=lambda: {"session_id": "s1"})
        play = mock.Mock()
        play.pause.side_effect = lambda: order.append("pause") or 0
        with mock.patch.dict(sys.modules, {"event_bus": bus,
                                           "runtime_binding": binding,
                                           "play": play}), \
             mock.patch.object(stream.turnclock, "hands_alive", return_value=alive), \
             mock.patch.object(stream, "load", return_value={"turn": 3}), \
             mock.patch.object(stream, "save"), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = stream.cmd_hands_release(mock.Mock(no_pause=no_pause))
        return code, order, out.getvalue()

    def test_a_stale_heartbeat_still_pauses_but_names_the_flag(self):
        """Release pauses; --no-pause is the only skip.

        A 2026-09-06 pass made a stale heartbeat skip the pause implicitly,
        which contradicted both these tests and the PLAYBOOK. A turn boundary
        is a paused boundary; the stale heartbeat is now a printed hint.
        """
        code, order, out = self.release(alive=(False, {"agent": "agent-7", "age": 900}))
        self.assertEqual(0, code)
        self.assertEqual(["pause", "handback"], order)
        self.assertIn("--no-pause", out)
        self.assertIn("touched nothing", out)

    def test_no_pause_is_honoured_explicitly(self):
        code, order, out = self.release(no_pause=True)
        self.assertEqual(0, code)
        self.assertEqual(["handback"], order)
        self.assertIn("--no-pause", out)

    def test_an_unknown_heartbeat_still_pauses_first(self):
        code, order, _ = self.release()
        self.assertEqual(0, code)
        self.assertEqual(["pause", "handback"], order)


class StatusOneTruthTests(unittest.TestCase):
    """`status` printed `no turn open` and `phase : hands (turn 1)` together.

    Both as fact, in one output, on 2026-09-07. One turn's diagnosis went down
    the wrong path entirely. The turn clock is the truth; the overlay label is
    a picture that lags, and a disagreement is now named as a disagreement.
    """

    def status(self, elapsed, phase, live_line="[hands] agent x ... 257:32"):
        state = {"status": {"phase": phase, "turn": 1}, "goals": {},
                 "feed": [], "mood": None}
        with mock.patch.object(stream.turnclock, "turn_elapsed", return_value=elapsed), \
             mock.patch.object(stream.turnclock, "liveness_line", return_value=live_line), \
             mock.patch.object(stream, "current_mode", return_value="live"), \
             mock.patch.object(stream, "binding_warning", return_value=None), \
             mock.patch.object(stream, "load", return_value={"turn": 1}), \
             mock.patch.object(stream.ov, "get", return_value=(state, None)), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(0, stream.cmd_status(None))
        return out.getvalue()

    def test_a_stale_overlay_label_is_flagged_not_printed_as_fact(self):
        text = self.status(elapsed=None, phase="hands")
        self.assertIn("no turn open", text)
        self.assertIn("OVERLAY still shows", text)
        self.assertNotIn("phase : hands (turn 1)", text)

    def test_a_closed_turn_does_not_report_a_stale_heartbeat_as_a_problem(self):
        self.assertNotIn("257:32", self.status(elapsed=None, phase="core"))

    def test_an_open_turn_still_reports_its_heartbeat(self):
        self.assertIn("257:32", self.status(elapsed=90, phase="hands"))

    def test_agreement_prints_one_plain_line(self):
        text = self.status(elapsed=90, phase="hands")
        self.assertIn("phase : hands (turn 1)", text)
        self.assertNotIn("OVERLAY", text)


class GoResetsTheCounterTests(unittest.TestCase):
    """A new session starts at turn 0 without M having to say "reset".

    2026-09-07: three dead turns at the door were numbered 1-3, and she had to
    ask for a reset out loud. `reset` is the sledgehammer -- it also clears the
    pin, the goals bar, the feed and the rota service -- so `go` reuses the
    `hands-close` path and then zeroes only the number.
    """

    def go(self, state, keep_turns=False, beat=None):
        saved = {}
        rota = mock.Mock()
        rota.ensure_started.return_value = (True, "started")
        posts = []
        with mock.patch.dict(sys.modules, {"rota_service": rota}), \
             mock.patch.object(stream, "load", return_value=dict(state)), \
             mock.patch.object(stream, "update",
                               side_effect=lambda fn: fn(saved) or True), \
             mock.patch.object(stream, "binding_warning", return_value=None), \
             mock.patch.object(stream.turnclock, "hands_alive",
                               return_value=beat or (None, {})), \
             mock.patch.object(stream.ov, "post",
                               side_effect=lambda path, **kw: (posts.append((path, kw))
                                                               or (True, None))), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = stream.cmd_go(mock.Mock(keep_turns=keep_turns))
        return code, saved, posts, out.getvalue()

    def test_go_zeroes_the_counter_and_pushes_turn_0_to_the_overlay(self):
        code, saved, posts, text = self.go({"turn": 3, "mode": "live"})
        self.assertEqual(0, code)
        self.assertEqual(0, saved["turn"])
        self.assertIn(("/status", {"timeout": stream.FAST, "phase": "core",
                                   "turn": 0}), posts)
        self.assertIn("turn counter reset to 0", text)
        # Everything `reset` would also have destroyed:
        self.assertNotIn("/goals", [path for path, _ in posts])
        self.assertNotIn("/event", [path for path, _ in posts])
        self.assertNotIn("/reset", [path for path, _ in posts])

    def test_a_stale_open_turn_is_closed_first_not_left_open(self):
        code, saved, _posts, text = self.go(
            {"turn": 3, "handsStartedAt": 10.0, "mode": "live"})
        self.assertEqual(0, code)
        self.assertIn("CLOSED turn 3", text)
        self.assertEqual(0, saved["turn"])
        self.assertNotIn("handsStartedAt", saved)

    def test_a_live_fork_stops_the_reset_but_not_the_services(self):
        code, saved, _posts, text = self.go(
            {"turn": 3, "handsStartedAt": 10.0, "mode": "live"},
            beat=(True, {"agent": "fork-3", "age": 5}))
        self.assertEqual(0, code)
        self.assertIn("TURN COUNTER NOT RESET", text)
        self.assertIn("STREAM GO", text)
        self.assertEqual({}, saved)

    def test_keep_turns_opts_out(self):
        code, saved, posts, text = self.go({"turn": 3}, keep_turns=True)
        self.assertEqual(0, code)
        self.assertEqual({}, saved)
        self.assertNotIn("turn counter reset", text)
        self.assertEqual([], posts)


if __name__ == "__main__":
    unittest.main()
