import contextlib
import io
import unittest
from unittest import mock

import status


class PauseReportingTests(unittest.TestCase):
    def test_nearby_wild_hunt_is_visible_even_if_prey_is_not_ours(self):
        rows = [{"name": "Grizzly bear", "thingId": "Thing_GrizzlyBear123",
                 "predatorIsOurs": False, "prey": "Deer",
                 "distanceToNearestColonist": 39},
                {"predatorIsOurs": True, "distanceToNearestColonist": 2},
                {"predatorIsOurs": False, "distanceToNearestColonist": 41}]
        threats = {"huntersIgnored": rows}
        self.assertEqual([rows[0]], status.guard_hunters(threats))
        self.assertIn("Grizzly bear", "\n".join(status.brief_lines({"threats": threats})))

    def test_brief_counts_decision_letters_not_buttons(self):
        rows = [{"choices": [{"text": "Close"}, {"text": "Jump to location"}]},
                {"choices": [{"text": "Accept"}, {"text": "Reject"}]}]
        result = status.brief_lines({"letters": rows, "counts": {
            "letterCount": 2, "letterChoiceCount": 4}})
        self.assertIn("2 letter(s) (1 choice)", "\n".join(result))

    def _no_service(self):
        return mock.patch.object(status, "_pause_note",
                                 return_value="(no supervised play running)")

    def test_speed_pause_does_not_claim_human_input(self):
        with self._no_service():
            line = status.clock_line({"time": {"paused": True,
                                               "pausedByPlayer": True}})
        self.assertIn("PAUSED (no supervised play running", line)
        self.assertNotIn("by player", line)

    def test_forced_pause_is_visible_even_when_speed_is_paused(self):
        line = status.clock_line({"time": {"paused": True,
                                           "pausedByPlayer": True,
                                           "forcePaused": True}})
        self.assertIn("PAUSED (forced", line)

    def test_running_speed_is_preserved(self):
        self.assertIn("running Normal", status.clock_line(
            {"time": {"paused": False, "timeSpeed": "Normal"}}))

    def test_a_paused_clock_with_no_service_names_the_command(self):
        import play_service
        with mock.patch.object(play_service, "read_json", return_value=None):
            note = status._pause_note(probe=lambda: None)
        self.assertIn("python play.py start", note)
        self.assertIn("nothing recorded a stop", note)

    def test_a_paused_clock_under_a_live_service_says_so(self):
        import play
        import play_service
        with mock.patch.object(play_service, "read_json",
                               return_value={"pid": 4321}), \
             mock.patch.object(play, "service_alive", return_value=True):
            note = status._pause_note()
        self.assertIn("supervised play IS running", note)
        self.assertIn("4321", note)


class GuardStopTests(unittest.TestCase):
    """THE 2026-09-07 PAUSE STORM.

    A guard stop and a dead service printed two different lines for the same
    state, and the second one -- `no supervised play running -- python play.py
    start` -- named no guard. Six turns read it as a crashed service and
    restarted straight back into `colonist_injury`.
    """

    GUARD = ("colonist_injury", "Longhoff was injured.")

    def _note(self, row, probe, now=1000.0):
        import play
        import play_service
        with mock.patch.object(play_service, "read_json", return_value=row), \
             mock.patch.object(play, "service_alive", return_value=False), \
             mock.patch.object(status, "_probe_stop_reason", return_value=probe):
            return status.stop_note(status.stop_record(row, now=now))

    def test_the_guard_is_named_and_start_is_not_the_first_advice(self):
        row = {"pid": 1, "ready": False, "stoppedAt": 957.0}
        note = self._note(row, self.GUARD)
        self.assertIn("by guard colonist_injury", note)
        self.assertIn("Longhoff was injured.", note)
        self.assertIn("service exited 43 s ago", note)
        self.assertIn("fix the cause", note)
        self.assertLess(note.index("fix the cause"),
                        note.index("python play.py start"))

    def test_the_full_clock_line_reads_the_way_the_playbook_says(self):
        row = {"pid": 1, "ready": False, "stoppedAt": 957.0}
        import play
        import play_service
        with mock.patch.object(play_service, "read_json", return_value=row), \
             mock.patch.object(play, "service_alive", return_value=False), \
             mock.patch.object(status, "_probe_stop_reason",
                               return_value=self.GUARD), \
             mock.patch("time.time", return_value=1000.0):
            line = status.clock_line({"time": {"paused": True, "ticksGame": 7}})
        self.assertIn("PAUSED by guard colonist_injury -- Longhoff was injured.",
                      line)
        self.assertNotIn("PAUSED (no supervised play running", line)

    def test_a_requested_pause_says_who_asked_for_it(self):
        row = {"stoppedAt": 940.0}
        note = self._note(row, ("requested_pause", "Paused by the supervisor owner."))
        self.assertIn("by `play.py pause`", note)
        self.assertNotIn("by guard", note)

    def test_a_lease_expiry_is_named_as_a_lease_not_a_guard(self):
        note = self._note({"stoppedAt": 940.0},
                          ("lease_expired", "Heartbeat lease expired."))
        self.assertIn("by lease", note)

    def test_a_guard_this_file_has_never_heard_of_is_still_a_guard(self):
        note = self._note({"stoppedAt": 999.0}, ("meteor_incoming", "A rock."))
        self.assertIn("by guard meteor_incoming", note)
        self.assertIn("fix the cause", note)

    def test_the_companion_outranks_a_stale_file_record(self):
        row = {"stopKind": "requested", "stopReason": "requested_pause",
               "stopDetail": "stale", "stoppedAt": 900.0}
        rec = status.stop_record(row, probe=lambda: self.GUARD, now=1000.0)
        self.assertEqual("guard", rec["kind"])
        self.assertEqual("colonist_injury", rec["reason"])
        self.assertEqual("companion", rec["source"])

    def test_the_file_answers_on_its_own_when_the_companion_cannot(self):
        row = {"stopKind": "guard", "stopReason": "hostile",
               "stopDetail": "Raider arrived.", "stoppedAt": 940.0}
        rec = status.stop_record(row, probe=lambda: None, now=1000.0)
        self.assertEqual("guard", rec["kind"])
        self.assertEqual("state/play-service.json", rec["source"])
        self.assertIn("by guard hostile -- Raider arrived.",
                      status.stop_note(rec))

    def test_no_record_anywhere_is_the_only_bare_start_again(self):
        self.assertIsNone(status.stop_record({"pid": 3}, probe=lambda: None))

    def test_a_crash_with_only_an_error_field_is_reported_as_an_error(self):
        rec = status.stop_record({"error": "boom", "stoppedAt": 999.0},
                                 probe=lambda: None, now=1000.0)
        self.assertEqual("error", rec["kind"])
        self.assertIn("read the detail before restarting",
                      status.stop_note(rec))

    def test_the_probe_is_skipped_entirely_when_asked(self):
        calls = []

        def probe():
            calls.append(1)
            return self.GUARD

        status.stop_record({"stoppedAt": 1.0}, probe=False)
        self.assertEqual([], calls)

    def test_an_age_older_than_a_minute_and_a_half_is_printed_in_minutes(self):
        rec = {"kind": "guard", "reason": "hostile", "ageSeconds": 600}
        self.assertIn("service exited 10 min ago", status.stop_note(rec))


class UnnamedGuardTests(unittest.TestCase):
    """turn 35: the clock stopped twice with no guard reason named at all.

    The path: play_service's closing `op:status` read fails or answers for
    another epoch, so `stopReason` is None while `stopKind` is still "guard".
    The board used to print `by guard unnamed`, which names nothing. It now
    says what is actually known and shows the supervisor's own last rows.
    """

    def _note(self, row):
        return status.stop_note(status.stop_record(row, probe=False, now=1000.0))

    def test_a_guard_with_no_reason_says_so_and_names_the_last_events(self):
        note = self._note({"stopKind": "guard", "stopReason": None,
                           "stopDetail": "the service loop ended 'guard' and the "
                                         "companion recorded no stop reason for "
                                         "epoch 7 (the read did not answer at all)",
                           "lastEvents": ["alert_new: Fire in home area",
                                          "notification_new: Zzztt"],
                           "stoppedAt": 990.0})
        self.assertNotIn("unnamed", note)
        self.assertIn("recorded NO reason", note)
        self.assertIn("the read did not answer at all", note)
        self.assertIn("last supervisor events: alert_new: Fire in home area", note)

    def test_an_empty_ring_is_said_out_loud_rather_than_left_blank(self):
        note = self._note({"stopKind": "guard", "stoppedAt": 990.0})
        self.assertIn("recorded NO reason", note)
        self.assertIn("event ring recorded nothing either", note)

    def test_a_named_guard_is_untouched(self):
        note = self._note({"stopKind": "guard", "stopReason": "colonist_downed",
                           "stopDetail": "Lucas (downed)", "stoppedAt": 990.0})
        self.assertIn("by guard colonist_downed", note)
        self.assertNotIn("recorded NO reason", note)
        self.assertNotIn("last supervisor events", note)


class LetterMarkTests(unittest.TestCase):
    """One classifier, in letters.py. status.py's private copy predated the
    2026-09-07 opportunity-quest fix and still called a jump/close-only quest a
    CHOICE, which is what sent three turns looking for a decision that was
    never there. The board also printed no age, while an 8.6-hour-old bear
    letter drove three consecutive Lookout passes about a corpse."""

    QUEST = {"label": "Nibbler's Stash", "letterDef": "PositiveEvent",
             "type": "RimWorld.ChoiceLetter_Quest", "ageTicks": 21600,
             "shouldAutomaticallyOpenLetter": True,
             "choices": [{"text": "Jump to item stash"}, {"text": "Close"}]}
    RAID = {"label": "Raid", "ageTicks": 60, "shouldAutomaticallyOpenLetter": True,
            "choices": [{"text": "Accept"}, {"text": "Close"}]}

    def test_it_delegates_rather_than_keeping_a_copy(self):
        import letters
        self.assertIs(status.letters, letters)
        self.assertEqual(status.letter_mark(self.QUEST), letters._kind(self.QUEST))
        self.assertEqual("INFO", status.letter_mark(self.QUEST).strip())
        self.assertEqual("CHOICE", status.letter_mark(self.RAID).strip())
        self.assertFalse(hasattr(status, "ACKNOWLEDGE"))

    def test_the_letters_block_prints_the_age_and_the_stale_mark(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status.show({"status": "game_loaded",
                         "time": {"paused": False, "timeSpeed": "Superfast"},
                         "counts": {}, "letters": [self.QUEST, self.RAID],
                         "messages": [], "alerts": [], "colonists": [],
                         "ui": {}})
        text = out.getvalue()
        self.assertIn("INFO", text)
        self.assertIn("8.6h", text)
        self.assertIn("STALE", text)
        # The fresh one is not stale, and only one row carries the word.
        self.assertEqual(1, text.count("STALE"))


class DownedHostileTests(unittest.TestCase):
    """turn 18: `--brief` said 0 hostiles with a DOWNED manhunter four cells
    from three colonists. A manhunter that goes down loses its mental state,
    leaves hostiles[] by the companion's own design, and lands in
    downedNear[] -- which --brief never read."""

    def _reply(self):
        return {"counts": {"hostileCount": 1, "colonistCount": 3},
                "threats": {
                    "hostileCount": 1,
                    "hostiles": [{"name": "Raider", "thingId": "a",
                                  "downed": False,
                                  "distanceToNearestColonist": 20}],
                    "downedNearCount": 1,
                    "downedNear": [{"name": "Timber wolf", "thingId": "b",
                                    "downed": True,
                                    "distanceToNearestColonist": 4}]}}

    def test_a_downed_manhunter_counts_as_a_hostile_and_says_it_gets_up(self):
        s = status.threat_summary(self._reply())
        self.assertEqual(2, s["total"])
        self.assertEqual(1, s["downed"])
        self.assertEqual("2 hostile(s) (1 downed -- gets back up)",
                         status.hostile_phrase(s))

    def test_brief_names_the_downed_row(self):
        text = "\n".join(status.brief_lines(self._reply()))
        self.assertIn("2 hostile(s) (1 downed -- gets back up)", text)
        self.assertIn("Timber wolf [DOWNED, 4 cells]", text)

    def test_a_downed_row_already_in_hostiles_is_not_counted_twice(self):
        r = {"threats": {"hostileCount": 1,
                         "hostiles": [{"name": "Boar", "thingId": "x",
                                       "downed": True}],
                         "downedNearCount": 1,
                         "downedNear": [{"name": "Boar", "thingId": "x",
                                         "downed": True}]}}
        s = status.threat_summary(r)
        self.assertEqual(1, s["total"])
        self.assertEqual(1, s["downed"])

    def test_a_quiet_map_still_prints_a_plain_zero(self):
        s = status.threat_summary({"counts": {}, "threats": {}})
        self.assertEqual("0 hostile(s)", status.hostile_phrase(s))

    def test_the_downed_count_can_never_exceed_the_total(self):
        s = status.threat_summary(
            {"threats": {"hostileCount": 0, "hostiles": [],
                         "downedNearCount": 2,
                         "downedNear": [{"name": "Hare", "downed": True}]}})
        self.assertEqual(2, s["total"])
        self.assertEqual(2, s["downed"])


class GuardHuntTests(unittest.TestCase):
    """`THREATS 0 hunting predator` while the guard refused on
    `predator_hunt: Grizzly bear`. home/status counts a hunt only when the PREY
    is ours; the guard counts every non-player PredatorHunt within 40 cells."""

    def test_a_hunt_on_a_colonist_is_a_guard_hunt_too(self):
        # This row is in huntingPredators[], not huntersIgnored[]: the prey IS
        # ours, which is the worst case and the one the old filter missed.
        threats = {"huntingPredatorCount": 1,
                   "huntingPredators": [{"name": "Cougar", "thingId": "c",
                                         "predatorIsOurs": False, "prey": "Ada",
                                         "distanceToNearestColonist": 5}]}
        self.assertEqual(1, len(status.guard_hunters(threats)))

    def test_the_two_lists_are_deduped_on_thing_id(self):
        row = {"name": "Cougar", "thingId": "c", "predatorIsOurs": False,
               "distanceToNearestColonist": 5}
        self.assertEqual(1, len(status.guard_hunters(
            {"huntingPredators": [row], "huntersIgnored": [dict(row)]})))

    def test_a_guard_hunt_the_board_does_not_count_is_stated_as_extra(self):
        s = status.threat_summary(
            {"threats": {"huntingPredatorCount": 0,
                         "huntersIgnored": [
                             {"name": "Grizzly bear", "thingId": "g",
                              "predatorIsOurs": False, "prey": "Deer",
                              "distanceToNearestColonist": 12}]}})
        self.assertIn("WOULD stop supervised play", status.hunter_phrase(s))

    def test_our_own_predator_is_never_a_guard_hunt(self):
        self.assertEqual([], status.guard_hunters(
            {"huntingPredators": [{"predatorIsOurs": True,
                                   "distanceToNearestColonist": 1}]}))

    def test_a_hunt_past_forty_cells_is_outside_the_guard(self):
        self.assertEqual([], status.guard_hunters(
            {"huntersIgnored": [{"predatorIsOurs": False,
                                 "distanceToNearestColonist": 41}]}))


class DownedTests(unittest.TestCase):
    def test_a_downed_colonist_says_it_is_not_a_corpse(self):
        line = status.colonist_line({"name": "Ian", "downed": True})
        self.assertIn("DOWNED (alive, not a corpse)", line)

    def test_pawns_names_a_colony_animal_and_a_wild_one(self):
        import pawns
        self.assertIn("colony animal",
                      pawns.line({"name": "Bramble", "animal": True,
                                  "tame": True, "downed": True}))
        self.assertIn("alive, not a corpse; wild",
                      pawns.line({"name": "Alpaca", "animal": True,
                                  "wild": True, "downed": True}))

    def test_a_drafted_pawn_is_marked_even_with_no_combat_ledger(self):
        line = status.colonist_line({"name": "Finn", "drafted": True,
                                     "job": "LayDown"})
        self.assertIn("undraft when safe", line)


if __name__ == "__main__":
    unittest.main()
