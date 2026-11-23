"""Offline regressions for say.py, on a mocked HTTP call.

2026-09-08, Threadneedle: `say.py "<line>"` with no `--mood` printed `sent
thought` and posted a thought with no mood at all. That is not "no face" -- the
overlay's big face keeps whatever the LAST mood set, so a line about a bandaged
colonist went out under a grin from two turns ago, and the feed card lost its
emoji. Nothing said so.

The HTTP call is the mock, not `overlay_client.say`, so the payload these tests
assert on is the JSON body that would really go to the overlay. `_start` runs
its worker inline instead of on a daemon thread, so there is nothing to join.
"""
import io
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import overlay_client as ov
import say


class FakeHttp:
    """Stands in for urllib.request.urlopen. Records the decoded JSON body."""

    def __init__(self, fail=False):
        self.posts = []
        self.fail = fail

    def urlopen(self, req, timeout=None):
        self.posts.append(json.loads(req.data.decode("utf-8")))
        if self.fail:
            raise OSError("connection refused")
        return io.BytesIO(b'{"ok": true}')


_RAN = object()   # what a real _start returns: a live thread, truthy


def run_say(argv, http=None, start=None):
    """(exit code, posted payloads, stdout, stderr)."""
    http = http or FakeHttp()

    def inline(worker, path, payload):
        worker()
        return _RAN

    with mock.patch.object(ov.urllib.request, "urlopen", http.urlopen), \
            mock.patch.object(ov, "_start", start or inline), \
            mock.patch.object(ov, "log_drop", lambda *a, **k: None), \
            mock.patch("sys.stdout", new_callable=io.StringIO) as out, \
            mock.patch("sys.stderr", new_callable=io.StringIO) as err:
        code = say.main(argv)
    return code, http.posts, out.getvalue(), err.getvalue()


class DefaultMoodTests(unittest.TestCase):
    """A thought always carries a face now, and the line says which."""

    def test_an_omitted_mood_sends_the_default_face(self):
        code, posts, out, _ = run_say(["Back to the stonecutters."])
        self.assertEqual(0, code)
        self.assertEqual(1, len(posts))
        self.assertEqual("thought", posts[0]["kind"])
        self.assertEqual(say.DEFAULT_MOOD, posts[0]["mood"])
        self.assertIn("mood: happy (default)", out)

    def test_the_default_is_one_of_the_eight_the_server_validates(self):
        self.assertIn(say.DEFAULT_MOOD, ov.MOODS)


class UnknownFlagTests(unittest.TestCase):
    """This is the one tool whose swallowed argument is PUBLISHED to viewers."""

    def test_a_mistyped_flag_is_refused_rather_than_read_out_on_stream(self):
        code, posts, _, err = run_say(["Back to work.", "--moood", "welp"])
        self.assertEqual(2, code)
        self.assertEqual([], posts)
        self.assertIn("--moood", err)

    def test_a_single_dash_typo_is_caught_too(self):
        code, posts, _, err = run_say(["Back to work.", "-mood", "happy"])
        self.assertEqual(2, code)
        self.assertEqual([], posts)
        self.assertIn("-mood", err)

    def test_mood_with_no_value_is_named_rather_than_quietly_defaulted(self):
        """`--mood` with nothing after it left want_mood true and posted on the
        default face, telling the caller `(default)` when they had asked for a
        specific one."""
        code, posts, _, err = run_say(["A line.", "--mood"])
        self.assertEqual(2, code)
        self.assertEqual([], posts)
        self.assertIn("--mood", err)

    def test_a_real_line_that_merely_starts_with_a_dash_still_posts(self):
        """Narration is prose. A dash that is not a flag shape must not be
        mistaken for one."""
        code, posts, _, _ = run_say(["-- and then the wall went up."])
        self.assertEqual(0, code)
        self.assertEqual(1, len(posts))

    def test_an_explicit_mood_is_not_labelled_a_default(self):
        code, posts, out, _ = run_say(["A wolverine.", "--mood", "scared"])
        self.assertEqual(0, code)
        self.assertEqual("scared", posts[0]["mood"])
        self.assertIn("mood: scared", out)
        self.assertNotIn("default", out)

    def test_the_equals_form_is_the_same_flag(self):
        _, posts, out, _ = run_say(["Well.", "--mood=welp"])
        self.assertEqual("welp", posts[0]["mood"])
        self.assertNotIn("default", out)

    def test_the_words_are_joined_in_order(self):
        _, posts, _, _ = run_say(["Finn", "made", "a", "parka."])
        self.assertEqual("Finn made a parka.", posts[0]["text"])


class NoFaceTests(unittest.TestCase):
    """`--mood none` is the deliberate faceless post -- and it says so."""

    def test_mood_none_posts_no_mood_key_at_all(self):
        code, posts, out, _ = run_say(["Still thinking.", "--mood", "none"])
        self.assertEqual(0, code)
        self.assertNotIn("mood", posts[0])
        self.assertIn("mood: none", out)
        self.assertIn("keeps the face it had", out)

    def test_mood_none_with_no_words_posts_nothing(self):
        code, posts, out, _ = run_say(["--mood", "none"])
        self.assertEqual(0, code)
        self.assertEqual([], posts)
        self.assertEqual("", out)


class BadMoodTests(unittest.TestCase):
    def test_a_bad_mood_is_loud_and_the_thought_keeps_a_face(self):
        code, posts, out, err = run_say(["It held.", "--mood", "smug"])
        self.assertEqual(0, code)
        self.assertIn("'smug' is not a mood", err)
        self.assertIn("none for no face", err)
        for m in ov.MOODS:
            self.assertIn(m, err)
        self.assertEqual(say.DEFAULT_MOOD, posts[0]["mood"])
        self.assertIn("(default)", out)

    def test_a_bad_mood_with_no_words_still_posts_nothing(self):
        code, posts, _, err = run_say(["--mood", "smug"])
        self.assertEqual(0, code)
        self.assertEqual([], posts)
        self.assertIn("not a mood", err)


class MoodOnlyTests(unittest.TestCase):
    def test_a_mood_alone_is_a_mood_event_and_says_face_not_thought(self):
        code, posts, out, _ = run_say(["--mood", "scared"])
        self.assertEqual(0, code)
        self.assertEqual("mood", posts[0]["kind"])
        self.assertEqual("scared", posts[0]["mood"])
        self.assertIn("sent face", out)
        self.assertNotIn("sent thought", out)


class SilenceTests(unittest.TestCase):
    """Nothing here may fail a turn, and an empty call is documented silence."""

    def test_no_arguments_posts_nothing_and_exits_zero(self):
        code, posts, out, err = run_say([])
        self.assertEqual(0, code)
        self.assertEqual([], posts)
        self.assertEqual("", out + err)

    def test_help_prints_the_doc_and_posts_nothing(self):
        code, posts, out, _ = run_say(["--help"])
        self.assertEqual(0, code)
        self.assertEqual([], posts)
        self.assertIn("--mood none", out)

    def test_a_post_that_could_not_start_is_reported_not_swallowed(self):
        code, posts, out, err = run_say(
            ["A line."], start=lambda worker, path, payload: None)
        self.assertEqual(1, code)
        self.assertEqual([], posts)
        self.assertIn("could not start the overlay post", err)
        self.assertNotIn("sent", out)


if __name__ == "__main__":
    unittest.main()
