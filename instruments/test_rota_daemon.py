import contextlib
import io
import subprocess
import unittest
from unittest import mock

import rota


def fresh_state():
    return {
        "scout": {"lastWall": 0.0, "lastTick": None, "turn": 0, "reader": 0},
        "lookout": {"lastWall": 0.0, "lastTick": None, "turn": 0},
        "inflight": None, "scoutInflight": None,
        "delivered": 0.0, "scoutDelivered": 0.0,
        "generation": "g",
    }


class RotaDaemonTests(unittest.TestCase):
    def test_legacy_daemon_cannot_poll_or_spawn_seeded_scouts(self):
        out = io.StringIO()
        with mock.patch.object(rota, "spawn_scout") as scout, \
             mock.patch.object(rota, "spawn_look") as lookout, \
             mock.patch.object(rota, "ticks") as ticks, \
             contextlib.redirect_stdout(out):
            rota.cmd_daemon()
        scout.assert_not_called()
        lookout.assert_not_called()
        ticks.assert_not_called()
        self.assertIn("fixed wall slots", out.getvalue())

    def test_snapshot_embeds_each_reader_failure_in_packet(self):
        failed = mock.Mock(returncode=7, stdout=b"policy denied")
        timed_out = subprocess.TimeoutExpired(["python", "x"], 15)
        with mock.patch.object(rota.subprocess, "run",
                               side_effect=[failed, timed_out, failed, failed]):
            packet = rota.scout_snapshot()
        self.assertIn("READ FAILED (exit 7): policy denied", packet)
        self.assertIn("READ FAILED: Command", packet)
        self.assertIn("explicitly report NOT CHECKED", packet)


if __name__ == "__main__":
    unittest.main()
