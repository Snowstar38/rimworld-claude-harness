from pathlib import Path
import unittest


SOURCE = (Path(__file__).parent.parent / "src" / "PlayUntilEventTool.cs").read_text(encoding="utf-8")


class RunSafetySourceTests(unittest.TestCase):
    def test_message_watermark_keeps_gap_messages_out_of_baseline(self):
        self.assertIn("int messageSinceTick = -1", SOURCE)
        self.assertIn("SafeMessageTick(message) < watch.MessageSinceTick", SOURCE)
        self.assertIn('{ "messageSinceTick", watch.MessageSinceTick }', SOURCE)

    def test_hostile_allowlist_is_id_scoped(self):
        self.assertIn('string ignoredHostileIds = ""', SOURCE)
        self.assertIn("watch.IgnoredHostileIds.Contains(pawn.thingIDNumber)", SOURCE)
        self.assertIn('{ "ignoredHostileIds", watch.IgnoredHostileIds.OrderBy', SOURCE)

    def test_requested_missing_watchers_refuse_to_run(self):
        self.assertIn("Alert watcher is unavailable on this build; refusing to run", SOURCE)
        self.assertIn("Transient-message watcher is unavailable on this build; refusing to run", SOURCE)

    def test_current_alerts_and_downed_can_fail_closed_at_entry(self):
        self.assertIn("bool stopOnCurrentAlerts = false", SOURCE)
        self.assertIn("if (!watch.StopOnCurrentAlerts || watch.IgnoredAlertLabels.Contains(key.Value))", SOURCE)
        self.assertIn("bool stopOnCurrentDownedColonists = false", SOURCE)
        self.assertIn("if (!watch.StopOnCurrentDowned", SOURCE)
        self.assertIn("watch.IgnoredAlertLabels.Contains(key.Value)", SOURCE)
        self.assertIn("watch.IgnoredDownedColonistIds.Contains(pawn.thingIDNumber)", SOURCE)


if __name__ == "__main__":
    unittest.main()
