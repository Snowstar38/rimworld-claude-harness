import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import turnclock


class TurnclockLifecycleTests(unittest.TestCase):
    def test_reminder_cadence_has_independent_budget_boundary(self):
        state = {}
        line, state = turnclock.next_budget_reminder(299, state)
        self.assertIsNone(line)
        line, state = turnclock.next_budget_reminder(305, state)
        self.assertIn("5:05", line)
        line, state = turnclock.next_budget_reminder(350, state)
        self.assertIsNone(line)
        line, state = turnclock.next_budget_reminder(360, state)
        self.assertIn("0:00 OVER", line)

    def test_overdue_reminder_recurs_each_approximately_45_seconds(self):
        line, state = turnclock.next_budget_reminder(360, {})
        self.assertIn("6:00 elapsed", line)
        line, state = turnclock.next_budget_reminder(404, state)
        self.assertIsNone(line)
        line, state = turnclock.next_budget_reminder(405, state)
        self.assertIn("6:45 elapsed", line)
        self.assertIn("0:45 OVER", line)
        line, state = turnclock.next_budget_reminder(449, state)
        self.assertIsNone(line)
        line, state = turnclock.next_budget_reminder(450, state)
        self.assertIn("1:30 OVER", line)

    def test_first_late_observation_reports_current_overage_once(self):
        line, state = turnclock.next_budget_reminder(497, {})
        self.assertIn("8:17 elapsed", line)
        self.assertIn("2:17 OVER", line)
        line, _ = turnclock.next_budget_reminder(500, state)
        self.assertIsNone(line)

    def test_late_first_overdue_does_not_repeat_at_next_old_bucket(self):
        line, state = turnclock.next_budget_reminder(404, {})
        self.assertIn("6:44 elapsed", line)
        line, state = turnclock.next_budget_reminder(405, state)
        self.assertIsNone(line)
        line, state = turnclock.next_budget_reminder(448, state)
        self.assertIsNone(line)
        line, state = turnclock.next_budget_reminder(449, state)
        self.assertIn("7:29 elapsed", line)

    def test_missed_intervals_do_not_create_catchup_bursts(self):
        line, state = turnclock.next_budget_reminder(360, {})
        self.assertIsNotNone(line)
        line, state = turnclock.next_budget_reminder(600, state)
        self.assertIn("4:00 OVER", line)
        line, _ = turnclock.next_budget_reminder(601, state)
        self.assertIsNone(line)

    def test_report_mtime_cannot_mark_running_fork_done(self):
        state = {"handsStartedAt": time.time() - 30}
        with mock.patch.object(turnclock, "_state", return_value=state):
            done, elapsed = turnclock.handed_back()
        self.assertFalse(done)
        self.assertGreaterEqual(elapsed, 29)

    def test_native_stop_timestamp_marks_fork_done(self):
        now = time.time()
        state = {"handsStartedAt": now - 30, "handsEndedAt": now - 4}
        with mock.patch.object(turnclock, "_state", return_value=state):
            done, seconds = turnclock.handed_back()
        self.assertTrue(done)
        self.assertGreaterEqual(seconds, 3)

    def test_old_unclosed_turn_is_still_not_handed_back(self):
        state = {"handsStartedAt": time.time() - turnclock.MAX_AGE - 60}
        with mock.patch.object(turnclock, "_state", return_value=state):
            done, elapsed = turnclock.handed_back()
        self.assertFalse(done)
        self.assertGreater(elapsed, turnclock.MAX_AGE)


class LivenessTokenTests(unittest.TestCase):
    """The fork's own heartbeat, and what may be concluded from its absence."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        for name, value in (("STATE", self.dir / "stream.json"),
                            ("HEARTBEAT", self.dir / "hands-heartbeat.json"),
                            ("REPORT", self.dir / "hands-last.md")):
            p = mock.patch.object(turnclock, name, str(value))
            p.start()
            self.addCleanup(p.stop)

    def write(self, **state):
        (self.dir / "stream.json").write_text(json.dumps(state))

    def beat(self, **fields):
        (self.dir / "hands-heartbeat.json").write_text(json.dumps(fields))

    def report(self, mtime):
        path = self.dir / "hands-last.md"
        path.write_text("report")
        os.utime(path, (mtime, mtime))

    def test_a_missing_heartbeat_is_unknown_and_never_dead(self):
        self.write(turn=20, handsStartedAt=1000)
        self.assertEqual((None, {}), turnclock.hands_alive())
        self.assertIsNone(turnclock.liveness_line())

    def test_a_fresh_heartbeat_is_proof_of_life(self):
        self.write(turn=20, handsStartedAt=1000)
        self.beat(agent="fork-1", turn=20, at=time.time() - 5)
        alive, info = turnclock.hands_alive()
        self.assertTrue(alive)
        self.assertEqual("fork-1", info["agent"])
        self.assertIn("it is alive", turnclock.liveness_line())

    def test_a_stale_heartbeat_names_the_recovery_command(self):
        self.write(turn=20, handsStartedAt=1000)
        self.beat(agent="fork-1", turn=20, at=time.time() - turnclock.LIVE_WITHIN - 60)
        alive, _info = turnclock.hands_alive()
        self.assertFalse(alive)
        self.assertIn("hands-close", turnclock.liveness_line())

    def test_a_heartbeat_from_before_this_turn_is_not_this_turn(self):
        self.write(turn=21, handsStartedAt=2000)
        self.beat(agent="fork-1", turn=20, at=1500)
        self.assertEqual((None, {}), turnclock.hands_alive())

    def test_report_written_before_the_stop_is_intermediate(self):
        self.write(turn=20, handsStartedAt=1000, handsEndedAt=1400,
                   handsEndedBy="fork-1")
        self.report(1360)
        stage, secs, _ = turnclock.report_stage()
        self.assertEqual("INTERMEDIATE", stage)
        self.assertEqual(40, int(secs))
        self.assertIn("hands-last.md is INTERMEDIATE (written 40 s before the "
                      "fork stopped)", turnclock.report_line())

    def test_report_written_after_the_stop_is_final(self):
        self.write(turn=20, handsStartedAt=1000, handsEndedAt=1400,
                   handsEndedBy="fork-1")
        self.report(1403)
        self.assertEqual("FINAL", turnclock.report_stage()[0])
        self.assertIn("is FINAL (written 3 s after the fork stopped)",
                      turnclock.report_line())

    def test_a_report_older_than_the_turn_belongs_to_an_earlier_turn(self):
        self.write(turn=20, handsStartedAt=1000, handsEndedAt=1400,
                   handsEndedBy="fork-1")
        self.report(900)
        self.assertEqual("STALE", turnclock.report_stage()[0])

    def test_a_forced_close_dates_the_report_from_the_last_heartbeat(self):
        self.write(turn=20, handsStartedAt=1000, handsEndedAt=9000,
                   handsEndedBy="core-forced")
        self.beat(agent="fork-1", turn=20, at=1400)
        self.report(1360)
        self.assertEqual("INTERMEDIATE", turnclock.report_stage()[0])
        self.assertIn("last tool boundary", turnclock.report_line())
        self.report(1450)
        self.assertEqual("FINAL", turnclock.report_stage()[0])

    def test_a_forced_close_without_a_heartbeat_refuses_to_guess(self):
        self.write(turn=20, handsStartedAt=1000, handsEndedAt=9000,
                   handsEndedBy="core-forced")
        self.report(1360)
        self.assertEqual("UNDATED", turnclock.report_stage()[0])
        self.assertIn("treat it as a draft", turnclock.report_line())

if __name__ == "__main__":
    unittest.main()
