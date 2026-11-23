"""The session-runtime binding: the warm start that cost three turns.

2026-09-07: M launched `errata.bat` at a running game while the previous
session's `claude.exe` was still exiting. SessionStart's `bind` lost to a
binding that was alive for a few more seconds; the failure went to a log nobody
reads; the old host then died, and for the rest of the night `alive()` was
False, `current_recipient` stayed `core`, and every fork's `hands-claim`
refused -- naming the hook, which had run perfectly.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import runtime_binding as rb
import runtime_hook as h


class BindingDiagnosis(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        for name, value in (("PATH", self.dir / "session-runtime.json"),
                            ("NOTE", self.dir / "note.json")):
            p = mock.patch.object(rb, name, value)
            p.start()
            self.addCleanup(p.stop)

    def write(self, **row):
        (self.dir / "session-runtime.json").write_text(json.dumps(row))

    def test_missing_binding_says_missing(self):
        state, line = rb.describe("s1")
        self.assertEqual("missing", state)
        self.assertIn("no runtime binding", line)

    def test_a_dead_host_pid_is_named_stale_with_its_pid_and_session(self):
        self.write(session_id="old-1", host_pid=33188, host_started="99")
        with mock.patch.object(rb, "process_identity", return_value=None):
            state, line = rb.describe("s1")
        self.assertEqual("dead", state)
        self.assertIn("old-1", line)
        self.assertIn("33188", line)
        self.assertIn("GONE", line)

    def test_a_live_binding_for_another_session_is_a_mismatch_not_a_hook_bug(self):
        self.write(session_id="other", host_pid=7, host_started="99")
        with mock.patch.object(rb, "process_identity", return_value="99"):
            state, line = rb.describe("s1")
        self.assertEqual("mismatch", state)
        self.assertIn("DIFFERENT live session", line)

    def test_rebind_adopts_a_dead_binding(self):
        self.write(session_id="old-1", host_pid=33188, host_started="99")
        with mock.patch.object(rb, "process_identity",
                               side_effect=lambda pid: "1234" if int(pid) == 55 else None):
            done, why = rb.rebind_if_stale("s1", ancestor=lambda: 55)
        self.assertTrue(done, why)
        row = json.loads((self.dir / "session-runtime.json").read_text())
        self.assertEqual({"session_id": "s1", "host_pid": 55,
                          "host_started": "1234"}, row)

    def test_rebind_adopts_our_own_live_host_under_a_stale_session_id(self):
        # `bind-check --repair` binds whatever session id it has on record; if
        # that is the previous session's, the binding is alive (our own
        # claude.exe) but mismatched. Same host pid and identity = still us.
        self.write(session_id="old-1", host_pid=55, host_started="1234")
        with mock.patch.object(rb, "process_identity",
                               side_effect=lambda pid: "1234" if int(pid) == 55 else None):
            done, why = rb.rebind_if_stale("s1", ancestor=lambda: 55)
        self.assertTrue(done, why)
        self.assertEqual("s1", rb.load()["session_id"])

    def test_rebind_refuses_while_another_session_is_actually_alive(self):
        self.write(session_id="other", host_pid=7, host_started="99")
        with mock.patch.object(rb, "process_identity", return_value="99"):
            done, why = rb.rebind_if_stale("s1", ancestor=lambda: 55)
        self.assertFalse(done)
        self.assertIn("another live session", why)
        self.assertEqual("other", json.loads(
            (self.dir / "session-runtime.json").read_text())["session_id"])

    def test_a_failing_ancestor_probe_is_recorded_and_then_throttled(self):
        """The PowerShell probe has a 5 s timeout; a retry storm is not the fix."""
        self.write(session_id="old-1", host_pid=33188, host_started="99")
        def boom():
            raise RuntimeError("No controlling Claude process in hook ancestry")
        with mock.patch.object(rb, "process_identity", return_value=None):
            first = rb.rebind_if_stale("s1", ancestor=boom)
            second = rb.rebind_if_stale("s1", ancestor=boom)
        self.assertFalse(first[0])
        self.assertIn("ancestry", first[1])
        self.assertIn("throttled", second[1])
        self.assertIn("ancestry", rb.note()["rebindError"])


class HookSelfHeal(unittest.TestCase):
    """Any Core hook event re-binds a dead binding; a fork never does."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        for target, name, value in ((h, "STATE", self.dir),
                                    (rb, "NOTE", self.dir / "note.json")):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_a_core_tool_boundary_rebinds_a_dead_binding(self):
        with mock.patch.object(rb, "rebind_if_stale",
                               return_value=(True, "rebound")) as rebind, \
             mock.patch.object(rb, "load", return_value={"session_id": "s1"}), \
             mock.patch.object(rb, "alive", return_value=True):
            h.handle("register", {"session_id": "s1", "tool_input": {}})
        rebind.assert_called_once_with("s1")

    def test_a_hands_fork_never_rebinds(self):
        """A fork shares the host process but not the session id: it hands back."""
        with mock.patch.object(rb, "rebind_if_stale") as rebind, \
             mock.patch.object(rb, "load", return_value={"session_id": "s1"}), \
             mock.patch.object(rb, "alive", return_value=True):
            h.handle("register", {"session_id": "s1", "agent_id": "fork-9",
                                  "tool_input": {}})
        rebind.assert_not_called()

    def test_the_core_session_id_is_recorded_for_a_manual_repair(self):
        with mock.patch.object(rb, "rebind_if_stale", return_value=(False, "live")), \
             mock.patch.object(rb, "load", return_value={"session_id": "s1"}), \
             mock.patch.object(rb, "alive", return_value=True):
            h.handle("deliver", {"session_id": "s1", "tool_input": {}})
        self.assertEqual("s1", rb.note()["lastCoreSession"])

    def test_a_failed_start_bind_is_recorded_rather_than_only_logged(self):
        with mock.patch.object(rb, "bind", side_effect=RuntimeError("already controls")), \
             mock.patch.object(rb, "claude_ancestor", return_value=5):
            with self.assertRaises(RuntimeError):
                h.handle("start", {"session_id": "s1"})
        self.assertIn("already controls", rb.note()["startBindError"])


if __name__ == "__main__":
    unittest.main()
