"""Offline regressions for the letter stack's classification and its age line.

Both come from the 2026-09-07 live stream: an opportunity quest that three
instruments could neither answer nor clear, and an 8.6-hour-old bear letter
that drove three Lookout passes about a corpse.
"""
import io
import unittest
from unittest import mock

import letters


def _letter(**kw):
    row = {"id": "Letter_96", "label": "Opportunity: item stash",
           "letterDef": "NeutralEvent", "type": "RimWorld.NewQuestLetter",
           "ageTicks": 21500, "arrivalTick": 100, "choiceCount": 2,
           "auto": True, "dismissible": True, "text": "A stash out east.",
           "choices": [{"index": 0, "text": "View quest", "disabled": False,
                        "disabledReason": None},
                       {"index": 1, "text": "Close", "disabled": False,
                        "disabledReason": None}]}
    row.update(kw)
    return row


ACCEPT = {"index": 0, "text": "Accept", "disabled": False, "disabledReason": None}
CLOSE = {"index": 1, "text": "Close", "disabled": False, "disabledReason": None}
VIEW = {"index": 2, "text": "View quest", "disabled": False, "disabledReason": None}


class OpportunityQuestTests(unittest.TestCase):
    """`Letter_96`: CHOICE with [View quest / Close] and no dialog behind it."""

    def test_jump_and_close_only_is_INFO_and_not_a_decision(self):
        row = _letter()
        self.assertTrue(letters.navigate_only(row))
        self.assertFalse(letters.is_decision(row))
        self.assertEqual("INFO", letters._kind(row).strip())

    def test_the_sweep_may_take_it(self):
        """It was pinned by two separate keeps: `auto`, and class NewQuestLetter."""
        self.assertIsNone(letters.keep_reason(_letter()))

    def test_a_real_offer_is_still_kept_even_beside_a_jump_button(self):
        row = _letter(choices=[ACCEPT, VIEW, CLOSE])
        self.assertFalse(letters.navigate_only(row))
        self.assertTrue(letters.is_decision(row))
        self.assertEqual("somebody has to answer it", letters.keep_reason(row))

    def test_a_letter_with_no_reported_buttons_is_still_kept(self):
        """`choices` is null for anything that is not a ChoiceLetter: unread
        options, not absent ones."""
        row = _letter(choices=[], type="Verse.StandardLetter", auto=False)
        self.assertFalse(letters.navigate_only(row))
        self.assertIn("no buttons reported", letters.keep_reason(row))

    def test_the_line_says_opportunity_quest_nothing_to_answer(self):
        note = letters.opportunity_note(_letter())
        self.assertIn("opportunity quest: nothing to answer", note)
        self.assertIn("letters.py dismiss Letter_96", note)

    def test_no_dialog_opened_names_the_opportunity_quest(self):
        row = _letter()
        with mock.patch.object(letters.rim, "game", return_value={"success": True}), \
             mock.patch.object(letters, "dialog_window", return_value=None), \
             mock.patch.object(letters.time, "sleep"), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertIsNone(letters.open_letter("Letter_96", hold=False,
                                                  timeout=0.0, letter=row))
        self.assertIn("nothing to answer", out.getvalue())


class StaleTests(unittest.TestCase):
    """An 8.6h "Grizzly bear hunting Longhoff" read as an attack in progress."""

    def test_age_is_in_game_hours(self):
        self.assertAlmostEqual(8.6, letters.age_hours(_letter()))

    def test_the_printed_row_carries_the_age_and_the_stale_flag(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            letters.show([_letter(label="Grizzly bear hunting Longhoff")])
        text = out.getvalue()
        self.assertIn("8.6h", text)
        self.assertIn("STALE", text)
        # the explanation is printed once, in the footer, not under every row
        self.assertNotIn("not what is happening now", text)

    def test_a_fresh_letter_is_not_flagged(self):
        fresh = _letter(ageTicks=1200, choices=[CLOSE], auto=False,
                        type="Verse.StandardLetter")
        self.assertFalse(letters.is_stale(fresh))
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            letters.show([fresh])
        self.assertNotIn("STALE", out.getvalue())


if __name__ == "__main__":
    unittest.main()
