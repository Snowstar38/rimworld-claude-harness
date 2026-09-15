import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import see


class SeeTests(unittest.TestCase):
    def run_capture(self, coords=(), zoom=None, moved=True):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "shot.png"
            image.write_bytes(b"test image")
            calls = []
            def record(name, result):
                def action(*args):
                    calls.append((name, args))
                    return result
                return action
            with patch.object(see, "_entry_scene", return_value=False), \
                 patch.object(see.cam, "base_cell", return_value=(118, 144, "pinned")), \
                 patch.object(see.camlock, "claim", side_effect=record("claim", None)), \
                 patch.object(see.cam, "set_zoom", side_effect=record("zoom", {"success": True})), \
                 patch.object(see.cam, "jump_to", side_effect=record("jump", {"success": moved})), \
                 patch.object(see.cam, "state", return_value={"success": True,
                     "rootSize": zoom or see.cam.BASE_ROOT, "viewRect": {
                         "minX": 108, "maxX": 128, "minZ": 134, "maxZ": 154}}), \
                 patch.object(see.cam, "shoot", side_effect=record("shoot", str(image))), \
                 patch.object(see.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
                error = None
                try:
                    see.capture(*coords, zoom=zoom)
                except (RuntimeError, ValueError) as exc:
                    error = str(exc)
            return calls, error

    def test_entry_scene_skips_camera_and_still_captures(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "entry.png"
            image.write_bytes(b"entry")
            calls = []
            with patch.object(see, "_entry_scene", return_value=True), \
                 patch.object(see.cam, "set_zoom", side_effect=AssertionError("zoom")), \
                 patch.object(see.cam, "jump_to", side_effect=AssertionError("jump")), \
                 patch.object(see.cam, "shoot", side_effect=lambda name: (calls.append(name), str(image))[1]), \
                 contextlib.redirect_stdout(io.StringIO()):
                result = see.capture(118, 144)
        self.assertEqual(str(image), result)
        self.assertEqual(1, len(calls))

    def test_entry_probe_accepts_real_screen_targets_envelope(self):
        reply = {"success": True, "targets": {"uiState": {
            "programState": "Entry", "inEntryScene": True}}}
        with patch.object(see.rim, "game", return_value=reply):
            self.assertTrue(see._entry_scene())

    def test_default_claims_then_centres_base_then_takes_one_image(self):
        calls, error = self.run_capture()
        self.assertIsNone(error)
        self.assertEqual(["claim", "zoom", "jump", "shoot"], [name for name, _ in calls])
        self.assertEqual((118, 144), calls[2][1])

    def test_coordinates_and_zoom_override_defaults(self):
        calls, error = self.run_capture((119, 145), zoom=24)
        self.assertIsNone(error)
        self.assertEqual((24.0,), calls[1][1])
        self.assertEqual((119, 145), calls[2][1])

    def test_failed_move_does_not_capture_wrong_scene(self):
        calls, error = self.run_capture(moved=False)
        self.assertIsNotNone(error)
        self.assertNotIn("shoot", [n for n, _ in calls])

    def test_camera_mismatch_does_not_capture_wrong_scene(self):
        calls, error = self.run_capture((50, 50))
        self.assertIn("did not reach", error)
        self.assertNotIn("shoot", [n for n, _ in calls])

    def test_invalid_zoom_does_not_move_camera(self):
        for zoom in [0, -1, float("nan"), float("inf")]:
            calls, error = self.run_capture(zoom=zoom)
            self.assertIsNotNone(error)
            self.assertEqual([], calls)


class FreshnessTests(unittest.TestCase):
    """`take_screenshot` returns `sourcePath: null`, and an OLD image handed to
    a blind reviewer reads as evidence. A stale file must fail, not return."""

    def test_a_shot_older_than_the_call_is_refused(self):
        import os, time
        with tempfile.TemporaryDirectory() as temp:
            old = Path(temp) / "old.png"
            old.write_bytes(b"stale")
            os.utime(old, (time.time() - 600, time.time() - 600))
            with self.assertRaises(RuntimeError) as raised:
                see._fresh_file(old, time.time())
        self.assertIn("PREVIOUS screenshot", str(raised.exception))

    def test_a_missing_path_falls_back_only_to_a_file_written_after_the_call(self):
        import os, time
        with tempfile.TemporaryDirectory() as temp:
            stale = Path(temp) / "stale.png"
            stale.write_bytes(b"stale")
            os.utime(stale, (time.time() - 600, time.time() - 600))
            started = time.time()
            with self.assertRaises(RuntimeError) as raised:
                see._fresh_file(Path(temp) / "never-written.png", started)
            self.assertIn("is NOT this frame", str(raised.exception))
            fresh = Path(temp) / "fresh.png"
            fresh.write_bytes(b"new")
            self.assertEqual(fresh.resolve(),
                             see._fresh_file(Path(temp) / "never-written.png", started))


class CoordinateFormTests(unittest.TestCase):
    """`x z` and `x,z` are the same cell. Every instrument here takes one of the
    two and refuses the other, so the form a turn types is a coin flip."""

    def test_both_forms_parse_to_the_same_cell(self):
        self.assertEqual([118, 144], see.cells(["118", "144"]))
        self.assertEqual([118, 144], see.cells(["118,144"]))
        self.assertEqual([118, 144], see.cells(["118, 144"]))
        self.assertEqual([], see.cells([]))

    def test_a_word_is_not_a_coordinate(self):
        with self.assertRaises(ValueError):
            see.cells(["middle"])

    def _main(self, argv):
        import sys
        from unittest.mock import patch
        with patch.object(sys, "argv", ["see.py"] + argv),              patch.object(see.rim, "init"),              patch.object(see, "capture", return_value="x.png") as capture,              contextlib.redirect_stderr(io.StringIO()),              contextlib.redirect_stdout(io.StringIO()):
            code = see.main()
        return code, capture

    def test_the_comma_form_reaches_capture(self):
        code, capture = self._main(["118,144"])
        self.assertEqual(0, code)
        self.assertEqual((118, 144), capture.call_args.args)

    def test_the_space_form_reaches_capture_unchanged(self):
        code, capture = self._main(["118", "144"])
        self.assertEqual(0, code)
        self.assertEqual((118, 144), capture.call_args.args)

    def test_no_coordinates_still_means_base(self):
        code, capture = self._main([])
        self.assertEqual(0, code)
        self.assertEqual((None, None), capture.call_args.args)

    def test_a_half_cell_is_refused_rather_than_guessed_at(self):
        with self.assertRaises(SystemExit):
            self._main(["118"])


if __name__ == "__main__":
    unittest.main()
