"""Mock-only tests for clock.py; no running game is contacted.

The whole file exists because of one sentence: **a VERIFIED line on a paused
clock proves nothing.** On the 2026-09-07 stream a wolf survived four separate
orders that each read a job back and printed success, and `combat.py flee`
printed FLEE VERIFIED while the pawn stood still through a manhunter attack.
Every job had landed. The clock was stopped, so no job ever ran.

So what is tested here is not plumbing, it is the three-way answer:
``running`` is the only state that may be reported as VERIFIED, ``paused`` is
QUEUED, and ``unknown`` is **not** a synonym for running.
"""
import io
import unittest
from unittest import mock

import clock


def status(paused=False, forced=False, tick=5000, speed="Normal"):
    return {"success": True,
            "time": {"paused": paused, "forcePaused": forced,
                     "ticksGame": tick, "timeSpeed": speed}}


class ClockStateTests(unittest.TestCase):
    def test_a_running_clock_is_the_only_state_that_earns_verified(self):
        st = clock.state(reader=lambda: status())
        self.assertEqual(clock.RUNNING, st["state"])
        self.assertTrue(clock.is_running(st))
        self.assertEqual("VERIFIED", clock.word(st))
        self.assertEqual([], clock.notes(st))

    def test_a_paused_clock_is_queued_and_says_how_to_run_it(self):
        st = clock.state(reader=lambda: status(paused=True), hint="combat.py")
        self.assertEqual(clock.PAUSED, st["state"])
        self.assertEqual("QUEUED", clock.word(st))
        text = "\n".join(clock.notes(st))
        self.assertIn("ORDER QUEUED -- CLOCK IS STOPPED", text)
        self.assertIn("paused by combat.py", text)
        self.assertIn("python play.py start", text)

    def test_an_unreadable_clock_is_unknown_and_never_verified(self):
        blind = lambda: (clock.UNKNOWN, None)
        for bad in (lambda: "no game is connected",
                    lambda: {"success": True},
                    lambda: None):
            with self.subTest(reply=bad()):
                st = clock.state(reader=bad, sampler=blind)
                self.assertEqual(clock.UNKNOWN, st["state"])
                self.assertNotEqual("VERIFIED", clock.word(st))
                self.assertIn("CLOCK UNKNOWN", "\n".join(clock.notes(st)))

    def test_a_missing_home_status_still_measures_the_ticks(self):
        # 2026-09-06, on this machine: the bridge exposed ZERO game tools, so
        # `home/status` was "not found". A companion that is not loaded must
        # not turn every order report into CLOCK UNKNOWN when the older
        # `rimworld/get_game_info` can still answer.
        def missing():
            raise RuntimeError("Tool 'home/status' not found for game 'rimworld'")
        st = clock.state(reader=missing, sampler=lambda: (clock.PAUSED, 0))
        self.assertEqual(clock.PAUSED, st["state"])
        self.assertEqual("QUEUED", clock.word(st))
        self.assertIn("measured by two tick reads", st["why"])

    def sampler(self, ticks):
        seen = iter(ticks)
        return lambda: {"ticksGame": next(seen)}

    def test_advancing_ticks_read_as_running(self):
        state, delta = clock.sample(reader=self.sampler([100, 160]),
                                    sleeper=lambda _: None)
        self.assertEqual(clock.RUNNING, state)
        self.assertEqual(60, delta)

    def test_identical_ticks_read_as_paused(self):
        state, delta = clock.sample(reader=self.sampler([100, 100]),
                                    sleeper=lambda _: None)
        self.assertEqual(clock.PAUSED, state)
        self.assertEqual(0, delta)

    def test_the_self_test_runs_and_contacts_nothing(self):
        with mock.patch.object(clock.rim, "game",
                               side_effect=AssertionError("touched the game")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(0, clock._self_test())
        text = out.getvalue()
        self.assertIn("no game was read", text)
        self.assertIn("CLOCK IS STOPPED", text)


if __name__ == "__main__":
    unittest.main()
