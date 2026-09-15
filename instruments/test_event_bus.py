import tempfile
import unittest
from pathlib import Path
from unittest import mock

import event_bus
import runtime_binding

# The message that reached the agent nearly six minutes late on 2026-09-07.
WOLF = "downed wolf needs finished off and butchered. @Errata"


class EventBusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "events.sqlite3"
        self.patches = [
            mock.patch.object(event_bus, "DB", self.db),
            mock.patch.object(event_bus.runtime_binding, "load",
                              return_value={"session_id": "s1"}),
            mock.patch.object(event_bus.game_session, "current",
                              return_value={"generation": "g1"}),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def publish(self, event_id="e1", **kw):
        return event_bus.publish("alert", {"label": "Danger"}, event_id,
                                 captured_at=0, **kw)

    def test_hands_route_blocks_core_until_atomic_handback(self):
        event_bus.set_hands("s1", "hands-1", 2)
        self.publish()
        self.assertIsNone(event_bus.receive("s1", "core"))
        packet = event_bus.receive("s1", "hands-1")
        self.assertEqual({"label": "Danger"}, packet["events"][0]["body"])
        event_bus.handback("s1", "hands-1")
        self.assertEqual(0, event_bus.acknowledge(packet["receipt"], "s1", "hands-1"))
        self.assertIsNone(event_bus.receive("s1", "hands-1"))
        core = event_bus.receive("s1", "core")
        self.assertEqual("e1", core["events"][0]["id"])

    def test_core_offer_is_requeued_when_hands_acquires_turn(self):
        self.publish()
        self.assertIsNotNone(event_bus.receive("s1", "core"))
        event_bus.set_hands("s1", "hands-1", 1)
        packet = event_bus.receive("s1", "hands-1")
        self.assertEqual("e1", packet["events"][0]["id"])

    def test_unknown_generation_does_not_stale_existing_event(self):
        self.publish()
        with mock.patch.object(event_bus.game_session, "current", return_value={}):
            packet = event_bus.receive("s1", "core")
        self.assertEqual("e1", packet["events"][0]["id"])

    def test_publish_rejects_missing_or_stale_generation(self):
        with mock.patch.object(event_bus.game_session, "current", return_value={}):
            self.assertEqual("no game generation", self.publish()["reason"])
        self.assertEqual("stale game generation",
                         self.publish(generation="old")["reason"])

    def test_reload_marks_old_pending_event_stale(self):
        self.publish()
        with mock.patch.object(event_bus.game_session, "current",
                               return_value={"generation": "g2"}):
            self.assertIsNone(event_bus.receive("s1", "core"))
        with event_bus.connect() as con:
            status = con.execute("SELECT status FROM events WHERE id='e1'").fetchone()[0]
        self.assertEqual("stale", status)

    def test_duplicate_keeps_original_route_and_zero_capture_time(self):
        first = self.publish()
        event_bus.set_hands("s1", "hands-1", 1)
        second = self.publish()
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual("core", second["recipient"])
        with event_bus.connect() as con:
            row = con.execute("SELECT captured FROM events WHERE id='e1'").fetchone()
        self.assertEqual(0, row["captured"])

    def test_high_alert_does_not_wake_a_parked_agent_but_rides_along(self):
        event_bus.publish("alert_new", {"event": {"label": "Low food",
                                                  "priority": "High"}},
                          "a1", meta={"wake": False})
        self.assertFalse(event_bus.wake_worthy("s1"))
        with mock.patch.object(event_bus.runtime_binding, "alive", return_value=True):
            self.assertIsNone(event_bus.wait("s1", "core", timeout=0.05))
            event_bus.publish("letter", {"label": "Quest"}, "l1")
            packet = event_bus.wait("s1", "core", timeout=0.05)
        self.assertEqual(["a1", "l1"], [e["id"] for e in packet["events"]])

    def test_critical_alert_wakes_a_parked_agent(self):
        event_bus.publish("alert_new", {"event": {"label": "Fire",
                                                  "priority": "Critical"}},
                          "a2", meta={"wake": True})
        self.assertTrue(event_bus.wake_worthy("s1"))
        with mock.patch.object(event_bus.runtime_binding, "alive", return_value=True):
            packet = event_bus.wait("s1", "core", timeout=0.05)
        self.assertEqual("a2", packet["events"][0]["id"])
        self.assertIn("[ALERT_NEW] Fire (Critical) — game still running",
                      event_bus.format_packet(packet))
        self.assertIn("never pauses the game", event_bus.format_packet(packet))

    def test_a_message_from_a_person_is_work_inside_the_turn(self):
        event_bus.publish("human", "pause and look at the freezer", "h1")
        with mock.patch.object(event_bus.runtime_binding, "alive", return_value=True):
            packet = event_bus.wait("s1", "core", timeout=0.05)
        rendered = event_bus.format_packet(packet)
        self.assertIn("[HUMAN] pause and look at the freezer", rendered)
        self.assertIn("not the turn itself", rendered)
        self.assertIn("continue the brief until the turn budget reminder arrives",
                      rendered)

    def test_the_turn_note_rides_only_on_human_events(self):
        event_bus.publish("alert_new", {"event": {"label": "Fire",
                                                  "priority": "Critical"}}, "a3")
        with mock.patch.object(event_bus.runtime_binding, "alive", return_value=True):
            packet = event_bus.wait("s1", "core", timeout=0.05)
        self.assertNotIn("not the turn itself", event_bus.format_packet(packet))

    def test_event_id_collision_across_generation_is_rejected(self):
        self.publish()
        with mock.patch.object(event_bus.game_session, "current",
                               return_value={"generation": "g2"}):
            result = self.publish(generation="g2")
        self.assertFalse(result["accepted"])
        self.assertIn("another session/generation", result["reason"])

    def test_ack_of_a_superseded_receipt_explains_itself(self):
        """`acknowledged 0 event(s)` read as lost events; nothing was lost.

        The first Lookout reports of the 2026-09-07 session acked 0 and later
        ones acked 1 or 2. A Core receipt dies the moment a Hands turn opens:
        `set_hands` pushes every offered event back to `pending`.
        """
        self.publish()
        packet = event_bus.receive("s1", "core")
        event_bus.set_hands("s1", "fork-1", 1)
        self.assertEqual(0, event_bus.acknowledge(packet["receipt"], "s1"))
        fate = event_bus.receipt_fate(packet["receipt"], "s1")
        self.assertIn("superseded", fate)
        self.assertIn("Nothing is lost", fate)
        self.assertIn("1 event(s) are pending", fate)

    def test_ack_twice_says_already_acknowledged(self):
        self.publish()
        packet = event_bus.receive("s1", "core")
        self.assertEqual(1, event_bus.acknowledge(packet["receipt"], "s1"))
        self.assertEqual(0, event_bus.acknowledge(packet["receipt"], "s1"))
        self.assertIn("already acknowledged",
                      event_bus.receipt_fate(packet["receipt"], "s1"))




class RuntimeBindingTests(unittest.TestCase):
    def test_live_same_session_cannot_be_stolen_by_new_process(self):
        old = {"session_id": "s1", "host_pid": 10, "host_started": "birth-a"}
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(runtime_binding, "PATH", Path(td) / "binding.json"), \
             mock.patch.object(runtime_binding, "load", return_value=old), \
             mock.patch.object(runtime_binding, "alive", return_value=True), \
             mock.patch.object(runtime_binding, "process_identity", return_value="birth-b"), \
             self.assertRaisesRegex(RuntimeError, "different live process/session"):
            runtime_binding.bind("s1", 11)

    def test_identical_live_binding_is_idempotent(self):
        old = {"session_id": "s1", "host_pid": 10, "host_started": "birth-a"}
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(runtime_binding, "PATH", Path(td) / "binding.json"), \
             mock.patch.object(runtime_binding, "load", return_value=old), \
             mock.patch.object(runtime_binding, "alive", return_value=True), \
             mock.patch.object(runtime_binding, "process_identity", return_value="birth-a"), \
             mock.patch.object(runtime_binding.game_session, "write") as write:
            result = runtime_binding.bind("s1", 10)
        self.assertEqual(old, result)
        write.assert_called_once()


class PriorityAndRouteRecoveryTests(unittest.TestCase):
    """Queue-jumping for a named mention, and recovering a dead fork's route."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for target, name, value in (
                (event_bus, "DB", Path(self.tmp.name) / "events.sqlite3"),
                (event_bus.runtime_binding, "load", None),
                (event_bus.game_session, "current", None)):
            kw = {} if value is None else {"new": value}
            if name == "load":
                kw = {"return_value": {"session_id": "s1"}}
            elif name == "current":
                kw = {"return_value": {"generation": "g1"}}
            p = mock.patch.object(target, name, **kw)
            p.start()
            self.addCleanup(p.stop)

    def test_a_named_mention_jumps_a_backlog_of_older_events(self):
        for n in range(9):
            event_bus.publish("review", {"line": n}, "review-%d" % n,
                              captured_at=n)
        event_bus.publish("chat", {"username": "rygger_dracora", "text": WOLF},
                          "chat-wolf", captured_at=99,
                          meta={"mention": True, "priority": 1})
        packet = event_bus.receive("s1", "core")
        self.assertEqual("chat-wolf", packet["events"][0]["id"])
        # ...and the ordinary backlog keeps its own chronological order.
        self.assertEqual(["review-%d" % n for n in range(7)],
                         [e["id"] for e in packet["events"][1:]])

    def test_an_unprioritised_event_keeps_plain_chronology(self):
        for n in range(3):
            event_bus.publish("review", {"line": n}, "review-%d" % n, captured_at=n)
        packet = event_bus.receive("s1", "core")
        self.assertEqual(["review-0", "review-1", "review-2"],
                         [e["id"] for e in packet["events"]])

    def test_a_mention_wakes_a_parked_agent_and_plain_chat_does_not(self):
        event_bus.publish("chat", {"text": "build a cooler"}, "chat-plain",
                          meta={"wake": False})
        self.assertFalse(event_bus.wake_worthy("s1"))
        event_bus.publish("chat", {"text": WOLF}, "chat-wolf",
                          meta={"wake": True, "mention": True, "priority": 1})
        self.assertTrue(event_bus.wake_worthy("s1"))

    def test_a_route_left_by_an_earlier_turn_is_taken_over_not_refused(self):
        event_bus.set_hands("s1", "fork-19", 19)
        # Turn 19's fork died without its stop hook clearing the route; turn 20
        # claims. Refusing here is what left turn 20 with no recorded owner.
        event_bus.set_hands("s1", "fork-20", 20)
        self.assertEqual("fork-20", event_bus.current_recipient("s1"))

    def test_a_second_fork_inside_the_same_turn_is_still_refused(self):
        event_bus.set_hands("s1", "fork-20", 20)
        with self.assertRaises(RuntimeError):
            event_bus.set_hands("s1", "intruder", 20)
        self.assertEqual("fork-20", event_bus.current_recipient("s1"))

    def test_a_takeover_returns_the_dead_forks_offered_events_to_the_queue(self):
        event_bus.set_hands("s1", "fork-19", 19)
        event_bus.publish("alert", {"label": "Danger"}, "e1", captured_at=0)
        self.assertIsNotNone(event_bus.receive("s1", "fork-19"))
        event_bus.set_hands("s1", "fork-20", 20)
        packet = event_bus.receive("s1", "fork-20")
        self.assertEqual(["e1"], [e["id"] for e in packet["events"]])


if __name__ == "__main__":
    unittest.main()
