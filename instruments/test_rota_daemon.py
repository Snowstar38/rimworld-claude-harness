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


class RotaLineBudgetTests(unittest.TestCase):
    """scout_brief()'s per-section line budget -- see its docstring."""

    def section(self, groups, rows):
        """A section shaped like the real ones: header, headline, indented rows."""
        lines = ["SECTION 00:00:00 -- header line, counts live here."]
        for g in range(groups):
            lines.append("  GROUP %d: %d flagged" % (g, rows))
            lines += ["    !! row %d of group %d" % (r, g) for r in range(rows)]
        return lines

    def test_a_section_inside_its_budget_is_returned_untouched(self):
        lines = self.section(2, 1)          # 1 + 2*(1+1) = 5 lines
        self.assertEqual(lines, rota._cap(lines, 8, "ROOMS"))
        self.assertNotIn("and 0 more", "\n".join(rota._cap(lines, 8, "ROOMS")))

    def test_a_long_section_is_capped_and_says_how_many_lines_went(self):
        lines = self.section(4, 5)          # 1 + 4*6 = 25 lines
        out = rota._cap(lines, 12, "BUILDINGS")
        self.assertEqual(13, len(out), "12 budgeted lines plus the one that says so")
        self.assertIn("... and 13 more buildings line(s) not shown", out[-1])
        self.assertIn("`python rota.py buildings`", out[-1],
                      "the cap must name the command that prints the rest")
        self.assertEqual(25, len(lines), "_cap must not mutate its argument")

    def test_sample_rows_are_shed_before_any_group_headline_is_dropped(self):
        # The whole point of a budget rather than a slice: a headline carries a
        # complete count, a row carries one name, so the rows go first.
        out = "\n".join(rota._cap(self.section(5, 4), 12, "BUILDINGS"))
        for g in range(5):
            self.assertIn("  GROUP %d: 4 flagged" % g, out,
                          "a dropped group reads to a Scout as 'nothing here'")
        self.assertIn("    !! row 0 of group 0", out, "the top group keeps its rows")
        self.assertNotIn("row 0 of group 4", out, "the bottom group sheds them first")

    def test_a_did_not_run_row_is_never_shed_for_space(self):
        lines = self.section(4, 5)
        lines.insert(len(lines) - 1, "    !! the BODY COVERAGE check DID NOT RUN")
        out = "\n".join(rota._cap(lines, 12, "COLONISTS"))
        self.assertIn("DID NOT RUN", out,
                      "an absent 'did not run' row reads as an all-clear")

    def test_a_cut_never_leaves_a_headline_standing_over_missing_rows(self):
        # Every row here is protected, so none can be shed and the section is
        # cut from the bottom instead -- and the cut must not end on a heading
        # whose rows are no longer under it.
        lines = ["SECTION 00:00:00 -- header line, counts live here."]
        for g in range(20):
            lines += ["  GROUP %d: 1 flagged" % g,
                      "    !! group %d's check DID NOT RUN" % g]
        out = rota._cap(lines, 8, "ROOMS")
        self.assertFalse(rota._is_head(out[-2]),
                         "the last surviving line before the tail is a dangling "
                         "heading: %r" % out[-2])
        self.assertEqual(8, len(out), "backing up must not overrun the budget")
        self.assertIn("... and 34 more", out[-1])

    def test_a_headline_with_no_rows_left_anywhere_is_not_a_dangling_one(self):
        # The counterpart: once every row is shed, a section of bare headlines
        # is exactly what a Scout should get, and the cut may end on one.
        out = rota._cap(self.section(20, 1), 8, "ROOMS")
        self.assertEqual(9, len(out))
        self.assertTrue(rota._is_head(out[-2]))
        self.assertIn("... and 33 more rooms line(s) not shown", out[-1])

    def test_every_section_of_a_brief_is_held_to_its_budget(self):
        long = "\n".join(self.section(6, 6))        # 1 + 6*7 = 43 lines
        with mock.patch.object(rota, "HANDS", r"C:\no\such\hands.md"), \
             mock.patch.object(rota, "buildings_section", return_value=long), \
             mock.patch.object(rota, "colonists_section", return_value=long), \
             mock.patch.object(rota, "rooms_section", return_value=long):
            brief = rota.scout_brief(0)
        for label, cap in rota.SECTION_CAPS.items():
            with self.subTest(label):
                self.assertIn("... and %d more %s line(s) not shown"
                              % (43 - cap, label.lower()), brief)
        self.assertEqual(3, brief.count("is budgeted at"),
                         "one budget line per capped section, no more")

    def test_the_budget_keeps_the_real_flood_fixtures_inside_it(self):
        cases = (("BUILDINGS",
                  rota.buildings_summary(rota._fixture("flood"), when="00:00:00")),
                 ("COLONISTS",
                  rota.colonists_summary(rota._pawn_fixture("flood"),
                                         rota._alert_fixture("truncated"),
                                         when="00:00:00")),
                 ("ROOMS",
                  rota.rooms_summary(rota._room_fixture("flood"), when="00:00:00")))
        for label, text in cases:
            with self.subTest(label):
                out = rota._capped(text, label).splitlines()
                self.assertLessEqual(len(out), rota.SECTION_CAPS[label] + 1)
                # Whatever else went, the section still states its own scope.
                self.assertTrue(any(l.startswith("  scope:") for l in out),
                                "the scope line is what says the counts are complete")

    def test_a_failed_read_marker_fits_every_budget_intact(self):
        # A truncated failure marker is the one truncation that could be read
        # as an all-clear, so each of these must survive whole.
        for text, label in ((rota.build_unavailable("URLError: refused"), "BUILDINGS"),
                            (rota.colonists_unavailable("URLError: refused"), "COLONISTS"),
                            (rota.rooms_unavailable("URLError: refused"), "ROOMS")):
            with self.subTest(label):
                self.assertEqual(text, rota._capped(text, label))


if __name__ == "__main__":
    unittest.main()
