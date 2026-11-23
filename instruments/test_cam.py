"""Mock-only tests for cam.py; no running game and no bridge is contacted.

Two things are asserted here that a live run cannot check cheaply: that
`--help` prints usage WITHOUT touching the bridge (it used to print camera
state, which needs a game), and that the zoom commands send the root size the
bridge's own schema names.
"""
import io
import os
import tempfile
import time
import unittest
from unittest import mock

import cam


CAMERA = {"rootSize": 30.0, "position": {"x": 120, "z": 140},
          "viewRect": {"minX": 100, "maxX": 140, "minZ": 120, "maxZ": 160}}


class CamHelpTests(unittest.TestCase):
    def test_help_prints_usage_and_never_starts_a_bridge_call(self):
        for flag in ("--help", "-h", "help"):
            with mock.patch.object(cam.sys, "argv", ["cam.py", flag]), \
                 mock.patch.object(cam.rim, "init",
                                   side_effect=AssertionError("touched the bridge")), \
                 mock.patch.object(cam.rim, "game",
                                   side_effect=AssertionError("touched the bridge")), \
                 mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                self.assertEqual(0, cam.main())
            self.assertIn("python cam.py zoom <rootSize>", out.getvalue())
            self.assertNotIn("root size :", out.getvalue())

    def test_the_docstring_says_what_a_root_size_means(self):
        self.assertIn("24-35", cam.__doc__)


class CamZoomTests(unittest.TestCase):
    def run_cli(self, argv, camera=None):
        calls = []

        def game(tool, args=None, strict=True):
            calls.append((tool, args))
            if tool == "rimworld/get_camera_state":
                return dict(camera or CAMERA, success=True)
            return {"success": True}

        with mock.patch.object(cam.sys, "argv", ["cam.py"] + argv), \
             mock.patch.object(cam.rim, "init"), \
             mock.patch.object(cam, "schema",
                               return_value={"rootSize": "float", "x": "int",
                                             "z": "int"}), \
             mock.patch.object(cam, "map_size", return_value=(250, 250)), \
             mock.patch.object(cam.rim, "game", side_effect=game), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = cam.main()
        return code, out.getvalue(), calls

    def test_zoom_sets_the_root_size_the_schema_names(self):
        code, text, calls = self.run_cli(["zoom", "28"])
        self.assertEqual(0, code)
        self.assertIn(("rimworld/set_camera_zoom", {"rootSize": 28.0}), calls)
        self.assertIn("covering", text)

    def test_zoom_says_so_when_the_camera_clamped(self):
        code, text, _ = self.run_cli(["zoom", "127"])
        self.assertEqual(0, code)
        self.assertIn("CLAMPED", text)

    def test_zoom_with_no_argument_prints_usage_and_exits_two(self):
        code, text, calls = self.run_cli(["zoom"])
        self.assertEqual(2, code)
        self.assertIn("usage: cam.py zoom", text)
        self.assertEqual([], [t for t, _ in calls
                              if t == "rimworld/set_camera_zoom"])

    def test_go_takes_a_zoom_and_still_jumps_to_the_cell(self):
        with mock.patch.object(cam.camlock, "claim"), \
             mock.patch.object(cam.camlock, "claimed", return_value=(None, 330.0)):
            code, text, calls = self.run_cli(["go", "118", "133", "--zoom", "26"])
        self.assertEqual(0, code)
        self.assertIn(("rimworld/set_camera_zoom", {"rootSize": 26.0}), calls)
        self.assertIn(("rimworld/jump_camera_to_cell", {"x": 118, "z": 133}),
                      calls)
        self.assertIn("root size 26", text)

    def test_go_without_a_zoom_leaves_the_zoom_alone(self):
        with mock.patch.object(cam.camlock, "claim"), \
             mock.patch.object(cam.camlock, "claimed", return_value=(None, 330.0)):
            code, _, calls = self.run_cli(["go", "118", "133"])
        self.assertEqual(0, code)
        self.assertEqual([], [t for t, _ in calls
                              if t == "rimworld/set_camera_zoom"])


class ShootFreshnessTests(unittest.TestCase):
    """`cam.shoot` must never hand back a screenshot taken before it ran.

    The same rule `see.py._fresh_file` applies -- lifted here because
    `cam.shoot` is the shared path `look.py` and the Lookout rota take, and a
    picture of the wrong moment reads as evidence.
    """

    def _shoot(self, reply, started):
        with mock.patch.object(cam.rim, "game", return_value=reply),              mock.patch.object(cam.time, "time", return_value=started):
            return cam.shoot("frame")

    def test_a_file_older_than_the_call_is_refused_not_returned(self):
        with tempfile.TemporaryDirectory() as d:
            stale = os.path.join(d, "old.png")
            with open(stale, "wb") as f:
                f.write(b"x")
            os.utime(stale, (1000, 1000))
            with self.assertRaises(RuntimeError) as caught:
                self._shoot({"success": True, "path": stale},
                            1000 + cam.FRESH_SLACK_S + 60)
            self.assertIn("PREVIOUS screenshot", str(caught.exception))

    def test_a_file_written_by_this_call_is_returned(self):
        with tempfile.TemporaryDirectory() as d:
            shot = os.path.join(d, "new.png")
            with open(shot, "wb") as f:
                f.write(b"x")
            os.utime(shot, (2000, 2000))
            got = self._shoot({"success": True, "path": shot}, 2000)
            self.assertEqual(os.path.realpath(shot), os.path.realpath(got))

    def test_a_missing_path_falls_back_only_to_a_png_written_since(self):
        with tempfile.TemporaryDirectory() as d:
            named = os.path.join(d, "never-landed.png")
            old = os.path.join(d, "old.png")
            with open(old, "wb") as f:
                f.write(b"x")
            os.utime(old, (1000, 1000))
            with self.assertRaises(RuntimeError) as caught:
                self._shoot({"success": True, "path": named}, 5000)
            self.assertIn("is NOT this frame", str(caught.exception))
            fresh = os.path.join(d, "fresh.png")
            with open(fresh, "wb") as f:
                f.write(b"x")
            os.utime(fresh, (5000, 5000))
            got = self._shoot({"success": True, "path": named}, 5000)
            self.assertEqual(os.path.realpath(fresh), os.path.realpath(got))


if __name__ == "__main__":
    unittest.main()
