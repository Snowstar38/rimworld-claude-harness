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
        self.assertIn("DRY RUN", lines[0])
        self.assertEqual(
            "PREVIEW ONLY: no blueprint placed; 0/1 rotations accepted.",
            lines[1].strip(),
        )


if __name__ == "__main__":
    unittest.main()
