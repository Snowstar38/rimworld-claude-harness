"""Offline contract checks for home/place_building and its Python client."""
from pathlib import Path
from contextlib import redirect_stdout
import importlib.util
import io
import sys
import types
import unittest


ROOT = Path(__file__).parents[2]
SOURCE = (ROOT / "companion" / "src" / "PlaceBuildingTool.cs").read_text(encoding="utf-8")


class PlaceBuildingSourceTests(unittest.TestCase):
    def test_write_intent_must_be_explicit(self):
        self.assertIn("bool? dryRun = null", SOURCE)
        self.assertIn("if (!dryRun.HasValue)", SOURCE)
        self.assertIn("dryRun is required: pass true to inspect or false to place", SOURCE)

    def test_def_name_is_canonical_and_legacy_def_is_guarded(self):
        self.assertIn("string defName = null", SOURCE)
        self.assertIn("def and defName were both supplied but do not match", SOURCE)
        self.assertIn("var requestedDef = string.IsNullOrWhiteSpace(defName) ? def : defName", SOURCE)

    def test_preview_all_remains_but_write_requires_one_rotation(self):
        self.assertIn('DefaultValue = "all"', SOURCE)
        self.assertIn('text == "all"', SOURCE)
        self.assertIn("if (rotations.Count != 1)", SOURCE)
        self.assertIn("A real placement needs exactly one rotation", SOURCE)

    def test_top_level_outcome_cannot_be_confused_with_execution_success(self):
        self.assertIn('{ "canPlace", acceptedRotations.Count > 0 }', SOURCE)
        self.assertIn('{ "applied", false }', SOURCE)
        self.assertIn('"PREVIEW ONLY: no blueprint placed; "', SOURCE)
        self.assertIn('payload["applied"] = true', SOURCE)
        self.assertIn('payload["outcome"] = "placed"', SOURCE)


class BlockingThingEffectTests(unittest.TestCase):
    """What an ACCEPTED placement removes has to reach the verdict line."""

    def test_every_blocker_carries_what_the_placement_does_to_it(self):
        self.assertIn('{ "effectOnPlace", EffectOnPlace(t, wipes, frameCancel, haulFirst, finishedWipes) }',
                      SOURCE)
        self.assertIn('{ "wipedWhenBuilt", finishedWipes }', SOURCE)
        self.assertIn('{ "isCrop", IsCrop(t) }', SOURCE)

    def test_the_effect_words_are_the_ones_the_client_reads(self):
        for word in ("wiped", "frameCancelled", "hauled", "replaced", "none"):
            self.assertIn('return "%s";' % word, SOURCE)
        self.assertIn('IsCrop(thing) ? "crop" : "cleared"', SOURCE)

    def test_replacement_is_measured_against_the_finished_building(self):
        """A blueprint wipes nothing; stone-over-wood is a replacement."""
        self.assertIn("finishedWipes = GenSpawn.SpawningWipes(entDef, t.def)", SOURCE)
        self.assertIn("if (category == ThingCategory.Building)", SOURCE)

    def test_a_crop_is_the_games_own_test_not_a_label_guess(self):
        self.assertIn("var plant = thing as Plant;", SOURCE)
        self.assertIn("plant.sown", SOURCE)
        self.assertIn("plant.IsCrop", SOURCE)


class BatchSourceTests(unittest.TestCase):
    """A 64-cell wall was 64 calls, each paying its own 1.5 s watch lead."""

    def test_the_tool_takes_a_cell_list(self):
        self.assertIn("string cells = null", SOURCE)
        self.assertIn("if (!string.IsNullOrWhiteSpace(cells))", SOURCE)
        self.assertIn("TryParseCellList(cells, x, z, out batchCells, out cellsError)",
                      SOURCE)

    def test_a_batch_is_chunked_across_main_thread_hops(self):
        """One long synchronous loop inside a hop stalls Root.Update, which is
        the tick AND the queue every other bridge call is pumped from."""
        self.assertIn("private const int CellsPerHop = 8;", SOURCE)
        self.assertIn("private const int HopGapMs = 20;", SOURCE)
        self.assertIn("start += CellsPerHop", SOURCE)
        self.assertIn("await Task.Delay(HopGapMs).ConfigureAwait(false)", SOURCE)
        self.assertIn("RunChunk(run, from, CellsPerHop)", SOURCE)

    def test_one_watch_lead_for_the_whole_batch(self):
        """Not one 1.5 s lead per cell: that was 96 s of the two minutes."""
        lead = SOURCE.count("await Watch.Lead(")
        self.assertEqual(2, lead)          # the single-cell path, and the batch
        self.assertIn("Watch.OpenAtCell(ctx, Midpoint(cells))", SOURCE)

    def test_one_materials_scan_for_the_whole_batch(self):
        self.assertIn("Materials(map, entDef, stuffDef, cells.Count)", SOURCE)
        self.assertIn("int forCells = 1", SOURCE)
        self.assertIn("var needed = perCell * forCells;", SOURCE)
        self.assertIn('{ "perCellNeeded", perCell }', SOURCE)

    def test_one_placement_path_for_a_cell_and_for_a_batch(self):
        self.assertIn("private static PlacementResult PlaceOne(", SOURCE)
        self.assertIn("var single = PlaceOne(map, entDef, center, chosen, stuffDef, player);",
                      SOURCE)
        self.assertIn("var result = PlaceOne(map, run.EntDef, cell, run.Rot, run.StuffDef, run.Player);",
                      SOURCE)
        # One call site in the code; the other hit is the class comment.
        self.assertEqual(1, len([line for line in SOURCE.splitlines()
                                 if "GenConstruct.PlaceBlueprintForBuild(" in line
                                 and not line.strip().startswith("///")]))

    def test_a_batch_needs_one_rotation_and_is_capped(self):
        self.assertIn("private const int MaxBatchCells = 200;", SOURCE)
        self.assertIn("A batch needs exactly one rotation", SOURCE)
        self.assertIn("and the cap is ", SOURCE)

    def test_a_refused_cell_does_not_stop_the_batch(self):
        self.assertIn('outcome = "refused";', SOURCE)
        self.assertIn('run.Refused++;', SOURCE)
        self.assertIn('outcome = "partial"', SOURCE)
        self.assertIn('{ "firstRefusal", run.FirstRefusal }', SOURCE)

    def test_every_cell_row_keeps_the_same_shape(self):
        for key in ('row["x"] = cell.x;', 'row["z"] = cell.z;',
                    'row["placed"] = null;', 'row["error"] = null;',
                    'row["outcome"] = outcome;'):
            self.assertIn(key, SOURCE)

    def test_an_unreadable_cell_list_refuses_the_whole_call(self):
        self.assertIn('is not a cell.', SOURCE)
        self.assertIn("cells was given but named no cell", SOURCE)


class BuildClientArgumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calls = []
        fake_rim = types.ModuleType("rim")
        fake_rim.game = lambda tool, args, strict=False: cls.calls.append(
            (tool, args, strict)
        ) or {"success": True}
        fake_rim.init = lambda: None
        cls.old_rim = sys.modules.get("rim")
        sys.modules["rim"] = fake_rim
        spec = importlib.util.spec_from_file_location(
            "build_under_test", ROOT / "instruments" / "build.py"
        )
        cls.build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.build)

    @classmethod
    def tearDownClass(cls):
        if cls.old_rim is None:
            sys.modules.pop("rim", None)
        else:
            sys.modules["rim"] = cls.old_rim

    def setUp(self):
        self.calls.clear()

    def test_preview_sends_canonical_name_and_explicit_dry_run(self):
        self.build.place("Bed", 113, 139, "east")
        tool, args, strict = self.calls[-1]
        self.assertEqual("home/place_building", tool)
        self.assertEqual("Bed", args["defName"])
        self.assertNotIn("def", args)
        self.assertIs(args["dryRun"], True)
        self.assertEqual("east", args["rotation"])
        self.assertIs(strict, False)

    def test_write_sends_explicit_false_and_selected_rotation(self):
        self.build.place("Bed", 113, 139, "south", do=True)
        _, args, _ = self.calls[-1]
        self.assertIs(args["dryRun"], False)
        self.assertEqual("south", args["rotation"])

    def test_report_prints_preview_only_outcome_prominently(self):
        reply = {
            "success": True, "dryRun": True,
            "def": {"defName": "Bed", "label": "bed"},
            "position": {"x": 113, "z": 139},
            "detail": "PREVIEW ONLY: no blueprint placed; 0/1 rotations accepted.",
            "rotations": [],
        }
        output = io.StringIO()
        with redirect_stdout(output):
            self.build.report(reply)
        lines = output.getvalue().splitlines()
        # The first line is the one a reader who reads nothing else will read.
        self.assertEqual("DRY RUN -- nothing placed; add --do", lines[0])
        self.assertIn("DRY RUN", lines[1])
        self.assertEqual(
            "PREVIEW ONLY: no blueprint placed; 0/1 rotations accepted.",
            lines[2].strip(),
        )
        self.assertNotIn("PLACED --", output.getvalue())


if __name__ == "__main__":
    unittest.main()
