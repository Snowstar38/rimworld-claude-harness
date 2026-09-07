"""Source guards for target-address regressions that require RimWorld types."""
from pathlib import Path
import unittest


SRC = (Path(__file__).parents[1] / "src" / "OrderTool.cs").read_text(encoding="utf-8")
THINGS = (Path(__file__).parents[1] / "src" / "ListThingsTool.cs").read_text(encoding="utf-8")


class OrderResolutionSourceTests(unittest.TestCase):
    def test_bare_cell_prefers_exactly_one_pawn(self):
        self.assertIn('matchedBy = "cellPawn"', SRC)
        self.assertIn("pawnsAtCell.Count == 1", SRC)

    def test_live_name_beats_hidden_pawns_from_corpses(self):
        self.assertIn('matchedBy = "liveName"', SRC)
        self.assertIn("liveSpawned.Count == 1", SRC)

    def test_defname_at_cell_is_an_emitted_id_form(self):
        self.assertIn('forms.Add(defName + "@"', SRC)

    def test_inventory_positions_expose_stable_ids(self):
        self.assertIn('{ "thingId", loadId }', THINGS)
        self.assertIn('{ "idForms", ids }', THINGS)

    def test_undesignated_chunks_get_an_actionable_refusal(self):
        self.assertIn('plan.Refuse("missing_haul_designation"', SRC)
        self.assertIn("act.py apply \\\"Haul things\\\"", SRC)
        self.assertIn('"globalHaulCandidateCount"', SRC)
        self.assertIn('"targetInGlobalHaulList"', SRC)

    def test_raw_ground_tend_requires_explicit_cleanup_owner(self):
        self.assertIn("allowPersistentDraft", SRC)
        self.assertIn('plan.Refuse("draft_cleanup_required"', SRC)


if __name__ == "__main__":
    unittest.main()
