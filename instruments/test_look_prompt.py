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


class GroundedLeadTests(unittest.TestCase):
    """A creature claim is checked against the pawn payload the survey already
    holds. The lead is never deleted; what the data says goes under it."""

    def _pawns(self, *rows):
        out = [{"name": "Octave", "defName": "Human", "isColonist": True,
                "humanlike": True, "animal": False}]
        out.extend(rows)
        return out

    def _beast(self, defName, dist, name=None):
        return {"name": name, "defName": defName, "kindDef": defName,
                "animal": True, "isColonist": False,
                "nearestColonistDistance": dist, "nearestColonist": "Octave"}

    def test_beside_a_colonist_is_refused_when_the_nearest_is_seventy_cells(self):
        note = look.ground_lead("A large animal is indoors beside Octave.",
                                self._pawns(self._beast("Hare", 70)))
        self.assertIsNotNone(note)
        self.assertIn("unverified", note)
        self.assertIn("70 cells", note)

    def test_an_animal_that_is_actually_beside_someone_passes(self):
        self.assertIsNone(
            look.ground_lead("An animal is standing beside a colonist.",
                             self._pawns(self._beast("Hare", 3))))

    def test_a_species_no_pawn_carries_is_named_as_not_there(self):
        note = look.ground_lead("A grizzly bear is by the wall.",
                                self._pawns(self._beast("Hare", 4)))
        self.assertIn("bear", note)
        self.assertIn("no such pawn", note)

    def test_a_species_that_is_on_the_map_passes(self):
        self.assertIsNone(
            look.ground_lead("A muffalo is grazing.",
                             self._pawns(self._beast("Muffalo", 20))))

    def test_an_insect_needs_an_insectoid_on_the_map(self):
        note = look.ground_lead("Insects are massing near the freezer.",
                                self._pawns(self._beast("Hare", 4)))
        self.assertIn("not one insect", note)
        self.assertIsNone(
            look.ground_lead("Insects are massing near the freezer.",
                             self._pawns(self._beast("Megaspider", 4))))

    def test_an_empty_map_says_nothing_on_screen_is_an_animal(self):
        note = look.ground_lead("A creature is moving in the dark.", self._pawns())
        self.assertIn("no animal on this map at all", note)

    def test_a_lead_about_no_creature_is_left_alone(self):
        self.assertIsNone(look.ground_lead("Items are piling up outside.",
                                           self._pawns(self._beast("Hare", 4))))

    def test_the_lead_survives_and_the_note_goes_under_it(self):
        body = "\n".join(("-- near frame --",
                          "LEAD: A large animal is indoors beside Octave.",
                          "WEIRD: nothing unusual seen"))
        text = look.ground_creatures(body, self._pawns(self._beast("Hare", 70)))
        lines = text.splitlines()
        self.assertEqual("LEAD: A large animal is indoors beside Octave.", lines[1])
        self.assertIn("unverified", lines[2])
        self.assertIn("grounding: 0 of 1 creature claim(s)", text)

    def test_no_payload_leaves_the_leads_alone_and_says_they_are_unchecked(self):
        body = "LEAD: A large animal is indoors beside Octave."
        text = look.ground_creatures(body, None)
        self.assertIn(body, text)
        self.assertNotIn("unverified", text)
        self.assertIn("unchecked", text)

    def test_grounding_costs_no_bridge_call(self):
        # The payload comes from the survey look.py already ran.
        self.assertIn("box.get(\"s\")", inspect.getsource(look.main))
        self.assertIn("ground_creatures", inspect.getsource(look.main))


class UnverifiedPromptTests(unittest.TestCase):
    def test_the_prompt_forbids_naming_what_the_screen_does_not_label(self):
        for phrase in ("its own on-screen label", "unverified:"):
            self.assertIn(phrase, look.PROMPT)
            self.assertIn(phrase, look.WIDE_PROMPT)


if __name__ == "__main__":
    unittest.main()
