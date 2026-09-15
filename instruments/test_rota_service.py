import subprocess
import io
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

import rota_service
import stream
import event_bus
import runtime_binding
import look


class RotaServiceTests(unittest.TestCase):
    def setUp(self):
        self.binding = mock.patch.object(
            runtime_binding, "load",
            return_value={"session_id": "s1", "host_pid": 10, "host_started": "birth"})
        self.alive = mock.patch.object(runtime_binding, "alive", return_value=True)
        self.binding.start(); self.alive.start()
        self.addCleanup(self.binding.stop); self.addCleanup(self.alive.stop)

    def test_existing_service_is_not_spawned_again(self):
        spawn = mock.Mock()
        with mock.patch.object(rota_service, "running", return_value=True), \
             mock.patch.object(rota_service, "state", return_value={"sessionId": "s1"}):
            self.assertEqual((True, "already running"), rota_service.ensure_started(spawn))
        spawn.assert_not_called()

    def test_start_cancels_a_pending_stop_before_accepting_live_service(self):
        with tempfile.TemporaryDirectory() as td:
            stop = Path(td) / "stop"
            stop.write_text("stop", encoding="ascii")
            with mock.patch.object(rota_service, "STATE", Path(td)), \
                 mock.patch.object(rota_service, "STOPFILE", stop), \
                 mock.patch.object(rota_service, "running", return_value=True), \
                 mock.patch.object(rota_service, "state", return_value={"sessionId": "s1"}):
                self.assertEqual((True, "already running"),
                                 rota_service.ensure_started(mock.Mock()))
                self.assertFalse(stop.exists(),
                                 "hands-start must cancel reset's pending stop")

    def test_start_is_detached_hidden_and_noninteractive(self):
        spawn = mock.Mock(return_value=object())
        with mock.patch.object(rota_service, "running", return_value=False), \
             mock.patch.object(rota_service.os, "name", "nt"):
            self.assertEqual((True, "started"), rota_service.ensure_started(spawn))
        args, kw = spawn.call_args
        self.assertEqual("serve", args[0][-1])
        self.assertIs(kw["stdin"], subprocess.DEVNULL)
        self.assertIs(kw["stdout"], subprocess.DEVNULL)
        self.assertTrue(kw["creationflags"] & rota_service.NO_WINDOW)
        self.assertTrue(kw["creationflags"] & rota_service.DETACHED)

    def test_start_failure_is_reported_without_raising(self):
        with mock.patch.object(rota_service, "running", return_value=False):
            ok, reason = rota_service.ensure_started(mock.Mock(side_effect=OSError("nope")))
        self.assertFalse(ok)
        self.assertIn("nope", reason)

    def test_start_waits_for_matching_ready_record(self):
        child = mock.Mock(pid=321)
        child.poll.return_value = None
        ready = {"pid": 321, "sessionId": "s1", "processStarted": "daemon-birth"}
        with mock.patch.object(rota_service, "running", side_effect=[False, True]), \
             mock.patch.object(rota_service, "state", return_value=ready):
            self.assertEqual((True, "started"), rota_service.ensure_started(mock.Mock(return_value=child)))

    def test_liveness_uses_exact_process_birth_identity(self):
        with mock.patch.object(runtime_binding, "process_identity", return_value="birth"):
            self.assertTrue(rota_service._pid_alive(42, "birth"))
            self.assertFalse(rota_service._pid_alive(42, "different-birth"))

    def test_start_requires_live_controlling_binding(self):
        with mock.patch.object(runtime_binding, "alive", return_value=False):
            ok, reason = rota_service.ensure_started(mock.Mock())
        self.assertFalse(ok)
        self.assertIn("no live controlling session", reason)

    def test_live_other_session_service_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            stop = Path(td) / "stop"; stop.write_text("stop")
            with mock.patch.object(rota_service, "STOPFILE", stop), \
                 mock.patch.object(rota_service, "running", return_value=True), \
                 mock.patch.object(rota_service, "state", return_value={"sessionId": "s2"}):
                ok, reason = rota_service.ensure_started(mock.Mock())
            self.assertTrue(stop.exists())
        self.assertFalse(ok)
        self.assertIn("another session", reason)

    def test_stop_uses_flag_and_never_signals_process(self):
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(rota_service, "STATE", Path(td)), \
             mock.patch.object(rota_service, "STOPFILE", Path(td) / "stop"), \
             mock.patch.object(rota_service, "running", return_value=True), \
             mock.patch.object(rota_service.os, "kill") as kill:
            self.assertEqual((True, "stop requested"), rota_service.stop())
            self.assertTrue((Path(td) / "stop").exists())
        kill.assert_not_called()

    def test_stream_go_ensures_service(self):
        # cmd_go also resets the overlay's turn counter; ov.post stubbed so
        # the reset is not a real POST.
        with mock.patch.object(rota_service, "ensure_started", return_value=(True, "started")) as start, \
             mock.patch.object(stream.ov, "post", return_value=(True, None)), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(0, stream.cmd_go(None))
        start.assert_called_once_with()

    def test_human_check_does_not_poll_rota_reports(self):
        with mock.patch("subprocess.run") as run, \
             mock.patch.object(stream.ov, "get", return_value=({"feed": []}, None)), \
             mock.patch.object(stream, "load", return_value={}), \
             mock.patch.object(stream, "save"):
            self.assertEqual(0, stream.cmd_human_check(None))
        run.assert_not_called()

    def test_fixed_180_second_slots_do_not_drift_with_completion(self):
        now = [0.0]
        launches = []
        child = mock.Mock()
        child.poll.return_value = 0
        def clock(): return now[0]
        def sleep(delay): now[0] += delay
        def launch(slot, index):
            launches.append((slot, index))
            return child
        def stopped(): return len(launches) >= 3
        rota_service.schedule(clock, sleep, launch, mock.Mock(), stopped)
        self.assertEqual([(0.0, 0), (180.0, 1), (360.0, 2)], launches)

    def test_slow_review_is_terminated_and_next_slot_still_launches(self):
        times = iter([0.0, 0.0, 180.0, 180.0])
        child = mock.Mock()
        child.poll.return_value = None
        launch = mock.Mock(return_value=child)
        terminate = mock.Mock()
        published = []
        calls = [0]
        def clock(): return next(times)
        def stopped():
            calls[0] += 1
            return calls[0] > 2
        rota_service.schedule(clock, lambda delay: None, launch,
                              lambda event_id, body: published.append((event_id, body)), stopped,
                              terminate=terminate)
        self.assertEqual(2, launch.call_count)
        self.assertEqual(180.0, launch.call_args_list[1].args[0])
        self.assertEqual(2, terminate.call_count)  # overdue first + shutdown second
        self.assertEqual(1, len(published))
        self.assertIn("REVIEW TIMED OUT", published[0][1])

    def test_automatic_launch_is_blind_and_alternates_reader_and_frame(self):
        spawn = mock.Mock(return_value=mock.Mock())
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(rota_service, "STATE", Path(td)), \
             mock.patch.object(rota_service, "LOG", Path(td) / "log"):
            rota_service._launch_review(0, 0, spawn)
            rota_service._launch_review(180, 1, spawn)
        first = spawn.call_args_list[0].args[0]
        second = spawn.call_args_list[1].args[0]
        self.assertIn("--no-verify", first)
        self.assertEqual("luna", first[first.index("--who") + 1])
        self.assertNotIn("--no-wide", first)
        self.assertEqual("sol", second[second.index("--who") + 1])
        self.assertIn("--no-wide", second)

    def test_refused_event_publish_is_saved_for_same_session_retry(self):
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(rota_service, "PENDING", Path(td) / "pending.json"), \
             mock.patch.object(event_bus, "publish", return_value={"accepted": False}), \
             mock.patch.object(runtime_binding, "load", return_value={"session_id": "s1"}):
            rota_service._publish_skip("e1", "timeout")
            rows = __import__("json").loads((Path(td) / "pending.json").read_text())
        self.assertEqual("e1", rows[0]["event_id"])
        self.assertEqual("timeout", rows[0]["body"])
        self.assertEqual("s1", rows[0]["session"])

    def test_windows_timeout_kills_only_owned_reviewer_tree(self):
        child = mock.Mock(pid=4321)
        child.poll.return_value = None
        with mock.patch.object(rota_service.os, "name", "nt"), \
             mock.patch.object(rota_service.subprocess, "run") as run:
            rota_service._terminate_owned(child)
        argv = run.call_args.args[0]
        self.assertEqual(["taskkill", "/PID", "4321", "/T", "/F"], argv)

    def test_review_from_ended_session_is_never_published_or_requeued(self):
        bus = mock.Mock()
        old = (look.REPORT_EVENT_ID, look.REPORT_SESSION, look.REPORT_CONTEXT)
        look.REPORT_EVENT_ID = "review-old"
        look.REPORT_SESSION = "s1"
        look.REPORT_CONTEXT = {"generation": "g1", "tick": 10, "capturedAt": 1.0}
        try:
            with mock.patch.object(runtime_binding, "load", return_value={"session_id": "s2"}), \
                 mock.patch.dict(sys.modules, {"event_bus": bus}), \
                 mock.patch.object(rota_service, "_queue_publish") as queue, \
                 mock.patch("sys.stdout", new_callable=io.StringIO):
                look.record("old evidence")
            bus.publish.assert_not_called()
            queue.assert_not_called()
        finally:
            look.REPORT_EVENT_ID, look.REPORT_SESSION, look.REPORT_CONTEXT = old


if __name__ == "__main__":
    unittest.main()
