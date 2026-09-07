"""Offline contract checks for the raw byPlayerOnly compatibility spelling."""
from pathlib import Path
import unittest


SOURCE = (Path(__file__).parents[1] / "src" / "ListBuildingsTool.cs").read_text(encoding="utf-8")


class ListBuildingsAliasSourceTests(unittest.TestCase):
    def test_raw_alias_is_declared_and_drives_existing_filter(self):
        self.assertIn("bool? byPlayerOnly = null", SOURCE)
        self.assertIn("var colonyOnly = byPlayerOnly ?? playerOnly", SOURCE)
        self.assertIn("category, colonyOnly, aggregate", SOURCE)
        self.assertIn("if (playerOnly && !IsPlayerFaction(thing))", SOURCE)

    def test_explicit_conflicting_scope_is_refused(self):
        self.assertIn("playerOnly=true and byPlayerOnly=false disagree", SOURCE)


if __name__ == "__main__":
    unittest.main()
