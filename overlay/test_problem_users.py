import os
import tempfile
import unittest

from problem_users import ProblemUsers


class ProblemUsersTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tempdir.name, "problem-usernames.txt")
        self.users = ProblemUsers(self.path)

    def tearDown(self):
        self.tempdir.cleanup()

    def lines(self):
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8") as handle:
            return handle.readlines()

    def test_third_strike_appends_once_per_hour(self):
        self.assertFalse(self.users.record("alice", "prompt_injection", now=100))
        self.assertFalse(self.users.record("alice", "malice", now=200))
        self.assertTrue(self.users.record("alice", "prompt_injection", now=300))
        self.assertFalse(self.users.record("alice", "malice", now=301))
        self.assertEqual(
            self.lines(),
            ["1970-01-01T00:05:00Z username=alice strikes=3 category=prompt_injection\n"],
        )

    def test_only_fixed_egregious_categories_count(self):
        for category in ("safe", "uncertain", "error", "PROMPT_INJECTION", "prompt_injection\nmalice", None):
            self.assertFalse(self.users.record("alice", category, now=100))
        self.assertFalse(self.users.record("alice", "malice", now=101))
        self.assertFalse(self.users.record("alice", "prompt_injection", now=102))
        self.assertTrue(self.users.record("alice", "malice", now=103))

    def test_rejects_filename_spoofs_and_control_characters_in_login(self):
        invalid = ("../problem-usernames.txt", "alice/bob", "alice\\bob", "alice\nmallory", "alice\x00", "", "a" * 26)
        for username in invalid:
            with self.subTest(username=repr(username)):
                self.assertFalse(self.users.record(username, "malice", now=100))
        self.assertFalse(os.path.exists(self.path))

    def test_preserves_existing_human_edits_and_only_appends(self):
        original = "# M: reviewed alice; no ban\nmanual note stays here\n"
        with open(self.path, "w", encoding="utf-8", newline="") as handle:
            handle.write(original)
        for now in (100, 101):
            self.assertFalse(self.users.record("bob", "malice", now=now))
        self.assertTrue(self.users.record("bob", "malice", now=102))
        with open(self.path, encoding="utf-8") as handle:
            result = handle.read()
        self.assertTrue(result.startswith(original))
        self.assertEqual(result.count("username=bob"), 1)

    def test_strikes_expire_from_the_rolling_hour(self):
        self.assertFalse(self.users.record("alice", "malice", now=0))
        self.assertFalse(self.users.record("alice", "malice", now=1800))
        self.assertFalse(self.users.record("alice", "malice", now=3601))
        self.assertFalse(self.lines())
        self.assertTrue(self.users.record("alice", "malice", now=3602))

    def test_separate_users_have_separate_thresholds(self):
        for now in (10, 20):
            self.assertFalse(self.users.record("alice", "malice", now=now))
            self.assertFalse(self.users.record("bob", "prompt_injection", now=now))
        self.assertTrue(self.users.record("alice", "malice", now=30))
        self.assertTrue(self.users.record("bob", "prompt_injection", now=30))
        self.assertEqual(len(self.lines()), 2)

    def test_tracked_user_memory_is_bounded(self):
        for number in range(2049):
            self.assertFalse(self.users.record(f"user_{number}", "malice", now=number))
        self.assertEqual(len(self.users._users), 2048)
        self.assertNotIn("user_0", self.users._users)
        self.assertIn("user_2048", self.users._users)

    def test_append_error_is_raised(self):
        directory_path = os.path.join(self.tempdir.name, "is-a-directory")
        os.mkdir(directory_path)
        users = ProblemUsers(directory_path)
        users.record("alice", "malice", now=1)
        users.record("alice", "malice", now=2)
        with self.assertRaises(OSError):
            users.record("alice", "malice", now=3)


if __name__ == "__main__":
    unittest.main()
