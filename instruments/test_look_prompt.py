"""What the blind Lookout is and is not allowed to call a lead."""
import inspect
import unittest

import look


class LookoutPromptTests(unittest.TestCase):
    def test_the_learning_helper_panel_is_named_and_excluded(self):
        self.assertIn("RIGHT EDGE", look.PROMPT)
        self.assertIn("'Learning helper'", look.PROMPT)
        self.assertIn("NOT the alert list", look.PROMPT)
        # The wide frame asks the same question about a different picture.
        self.assertIn("'Learning helper'", look.WIDE_PROMPT)
        self.assertIn("zoomed out", look.WIDE_PROMPT)

    def test_posture_and_full_shelves_are_banned_inferences(self):
        for phrase in ("collapsed, downed or unconscious",
                       "ordinary work postures",
                       "Full shelves",
                       "cluttered, messy or unfinished"):
            self.assertIn(phrase, look.PROMPT)

    def test_zero_leads_stays_the_expected_outcome(self):
        self.assertIn("zero leads is the normal outcome", look.PROMPT)

    def test_the_report_separates_seen_from_evidence_and_names_the_checks(self):
        rules = look.LEAD_RULES % "luna"
        self.assertIn("SEEN:", rules)
        self.assertIn("EVIDENCE:", rules)
        self.assertIn("luna's inference from the screenshot", rules)
        for tool in ("alerts.py", "pawns.py --health", "status.py --brief"):
            self.assertIn(tool, rules)
        self.assertIn("drop any lead the instrument contradicts", rules)

    def test_every_report_carries_the_rules(self):
        self.assertIn("LEAD_RULES % who", inspect.getsource(look.main))

    def test_the_prompt_stayed_compact(self):
        # It was 962 characters before the tightening; do not let it double.
        self.assertLess(len(look.PROMPT), 1924)


if __name__ == "__main__":
    unittest.main()
