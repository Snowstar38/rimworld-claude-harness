import contextlib
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

import game_session as gs
import rota


class GameSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch = mock.patch.object(gs, "STATE", self.root)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_backward_load_clears_future_letter_and_threat_memos(self):
        before = gs.observe(5000, "save-a")
        gs.write(self.root / "overlay-letters.json", {"seen": ["Letter_12|4500"]})
        gs.write(self.root / "run-last-stop.json", {"seen": {"hostile": "wolf"}})
        after = gs.observe(4000, "save-a")
        self.assertNotEqual(before["generation"], after["generation"])
        self.assertEqual({"seen": []}, gs.read(self.root / "overlay-letters.json"))
        self.assertEqual({}, gs.read(self.root / "run-last-stop.json"))

    def test_same_tick_reload_invalidates_inflight_report(self):
        before = gs.observe(100, "game-instance-a")
        report = gs.stamp("LOOKOUT old world", before)
        self.assertTrue(gs.report_is_current(report))
        gs.observe(100, "game-instance-b")
        self.assertFalse(gs.report_is_current(report))

    def test_forward_ticks_preserve_dedupe_and_generation(self):
        before = gs.observe(100, "a")
        gs.write(self.root / "overlay-letters.json", {"seen": ["x|90"]})
        after = gs.observe(101)
        self.assertEqual(before["generation"], after["generation"])
        self.assertEqual("a", after["sessionId"])
        self.assertEqual({"seen": ["x|90"]}, gs.read(self.root / "overlay-letters.json"))

    def test_expired_unversioned_and_future_reports_are_rejected(self):
        c = gs.observe(100, "a")
        self.assertFalse(gs.report_is_current("LOOKOUT unversioned"))
        self.assertFalse(gs.report_is_current(gs.stamp("old", dict(c, capturedAt=time.time()-301))))
        self.assertFalse(gs.report_is_current(gs.stamp("future", dict(c, tick=101))))

    def test_reports_does_not_schedule_or_replay_stale_report(self):
        gs.observe(100, "a")
        path = self.root / "lookout.txt"
        path.write_text("LOOKOUT discarded timeline", encoding="utf-8")
        out = io.StringIO()
        with mock.patch.object(rota, "STATE", str(self.root)), \
             mock.patch.object(rota, "LOOKOUT", str(path)), \
             mock.patch.object(rota, "SCOUTOUT", str(self.root / "absent")), \
             mock.patch.object(rota, "ticks", return_value=100), \
             mock.patch.object(rota, "spawn_scout") as scout, \
             mock.patch.object(rota, "spawn_look") as look, \
             contextlib.redirect_stdout(out):
            rota.cmd_reports()
            rota.cmd_reports()
        scout.assert_not_called()
        look.assert_not_called()
        self.assertNotIn("discarded timeline", out.getvalue())
        self.assertEqual(1, out.getvalue().count("discarded a stale"))


    def test_write_retries_a_windows_permission_error_with_a_new_temp_name(self):
        path = self.root / "state.json"
        seen, real = [], gs.os.replace
        def flaky(src, dst):
            seen.append(str(src))
            if len(seen) == 1:
                raise PermissionError(5, "Access is denied")
            return real(src, dst)
        with mock.patch.object(gs.os, "replace", flaky),              mock.patch.object(gs.time, "sleep") as slept:
            gs.write(path, {"epoch": "e1"})
        self.assertEqual({"epoch": "e1"}, gs.read(path))
        self.assertEqual(2, len(seen))
        self.assertNotEqual(seen[0], seen[1])
        self.assertTrue(slept.called)
        self.assertEqual([], list(self.root.glob("*.tmp")))

    def test_write_gives_up_after_the_attempt_budget(self):
        def denied(src, dst):
            raise PermissionError(5, "Access is denied")
        with mock.patch.object(gs.os, "replace", denied),              mock.patch.object(gs.time, "sleep") as slept,              self.assertRaises(PermissionError):
            gs.write(self.root / "state.json", {"a": 1})
        self.assertEqual(gs.WRITE_ATTEMPTS - 1, slept.call_count)
        self.assertEqual([], list(self.root.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
