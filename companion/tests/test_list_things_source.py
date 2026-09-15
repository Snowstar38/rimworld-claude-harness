"""Offline contract checks for `home/list_things`, from the C# source.

`inv.py` leans on two things this file pins down, and both are the reason a
word that IS on the map can come back as a zero:

* `match` is a case-insensitive SUBSTRING of BOTH defName and label. The client
  retries a plural as its singular on top of that; if the server ever matched
  only defName, "sculpture" would miss "Sandstone small sculpture" for a
  reason no retry could reach.
* the DEFAULT category is HAULABLE, and `all` / `buildings` are the widenings
  that see a plant or a built sculpture. `inv.py` re-asks a zero of the whole
  map and reports what it finds there, which only works while these three
  category words mean what they say.
"""
from pathlib import Path
import unittest


SOURCE = (Path(__file__).parents[1] / "src" /
          "ListThingsTool.cs").read_text(encoding="utf-8")


class MatchSourceTests(unittest.TestCase):
    def test_match_is_a_case_insensitive_substring_of_defname_and_label(self):
        self.assertIn("defName.IndexOf(match, StringComparison.OrdinalIgnoreCase) < 0", SOURCE)
        self.assertIn("label.IndexOf(match, StringComparison.OrdinalIgnoreCase) < 0", SOURCE)
        # Both have to miss before a thing is dropped.
        self.assertIn("&&", SOURCE.split("defName.IndexOf(match")[1][:120])

    def test_a_dropped_thing_is_counted_rather_than_vanishing(self):
        self.assertIn("skippedMatch++", SOURCE)
        self.assertIn('{ "match", match },', SOURCE)


class CategorySourceTests(unittest.TestCase):
    def test_haulable_is_the_default_and_the_three_widenings_exist(self):
        self.assertIn('(category ?? "haulable")', SOURCE)
        for word in ('cat != "all"', 'cat != "buildings"', 'cat != "food"'):
            self.assertIn(word, SOURCE)

    def test_all_and_buildings_read_different_thing_groups(self):
        self.assertIn("map.listerThings.AllThings", SOURCE)
        self.assertIn("ThingRequestGroup.BuildingArtificial", SOURCE)
        self.assertIn("ThingRequestGroup.HaulableEver", SOURCE)


if __name__ == "__main__":
    unittest.main()
