import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import human_events


class HumanEventPumpTests(unittest.TestCase):
    def setUp(self):
        self.binding = mock.patch.object(
            human_events.runtime_binding, "load", return_value={"session_id": "s1"})
        self.generation = mock.patch.object(
            human_events.game_session, "current", return_value={"generation": "g1"})
        self.binding.start(); self.generation.start()
        self.addCleanup(self.binding.stop); self.addCleanup(self.generation.stop)

    def test_only_human_messages_publish_and_are_idempotent(self):
        feed = {"feed": [
            {"kind": "thought", "ts": time.time() + 10, "text": "ignore"},
            {"kind": "human", "ts": time.time() + 10, "text": "pause please"},
        ]}
        publish = mock.Mock(return_value={"accepted": True, "recipient": "hands-7"})
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(human_events, "STATE", Path(td) / "seen.json"):
            self.assertEqual(1, human_events.pump_once(lambda *a, **k: (feed, None), publish))
            self.assertEqual(0, human_events.pump_once(lambda *a, **k: (feed, None), publish))
        publish.assert_called_once()
        self.assertEqual("human", publish.call_args.kwargs["kind"])
        self.assertEqual("pause please", publish.call_args.kwargs["body"])
        self.assertEqual("overlay-human", publish.call_args.kwargs["source"])

    def test_refused_publish_is_retried_not_watermarked(self):
        feed = {"feed": [{"kind": "human", "ts": time.time() + 10, "text": "hello"}]}
        publish = mock.Mock(side_effect=[{"accepted": False}, {"accepted": True}])
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(human_events, "STATE", Path(td) / "seen.json"):
            self.assertEqual(0, human_events.pump_once(lambda *a, **k: (feed, None), publish))
            # Even if it has already rolled out of the overlay feed, the local
            # spool retries the exact same durable event.
            self.assertEqual(1, human_events.pump_once(lambda *a, **k: (None, "down"), publish))
        self.assertEqual(2, publish.call_count)
        self.assertEqual(publish.call_args_list[0].kwargs["event_id"],
                         publish.call_args_list[1].kwargs["event_id"])

    def test_overlay_timeout_returns_without_touching_bus(self):
        publish = mock.Mock()
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(human_events, "STATE", Path(td) / "seen.json"):
            self.assertEqual(0, human_events.pump_once(
                lambda *a, **k: (None, "timeout"), publish))
        publish.assert_not_called()

    def test_retained_message_from_before_session_binding_is_baselined(self):
        feed = {"feed": [{"kind": "human", "ts": 1, "text": "old command"}]}
        publish = mock.Mock(return_value={"accepted": True})
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(human_events, "STATE", Path(td) / "seen.json"):
            self.assertEqual(0, human_events.pump_once(lambda *a, **k: (feed, None), publish))
        publish.assert_not_called()

    def test_pending_command_is_discarded_after_session_generation_changes(self):
        future = time.time() + 10
        feed = {"feed": [{"kind": "human", "ts": future, "text": "session one"}]}
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(human_events, "STATE", Path(td) / "seen.json"):
            human_events.pump_once(lambda *a, **k: (feed, None),
                                   mock.Mock(return_value={"accepted": False}))
            with mock.patch.object(human_events.runtime_binding, "load",
                                   return_value={"session_id": "s2"}), \
                 mock.patch.object(human_events.game_session, "current",
                                   return_value={"generation": "g2"}):
                publish = mock.Mock(return_value={"accepted": True})
                human_events.pump_once(lambda *a, **k: (None, "down"), publish)
        publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
