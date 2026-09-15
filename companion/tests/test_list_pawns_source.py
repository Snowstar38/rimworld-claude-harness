"""Offline contract checks for `home/list_pawns`, from the C# source.

Two callers depend on these fields being there without being asked for:

* `pawns.py --animals` prints the animal's ThingID on the NAME line, because
  five muffalo share a name and the id is the only address a write takes. It
  comes from `animals{}`, with `settings{}` carrying the same one.
* `look.py` grounds every creature claim in a Lookout report against this
  payload -- species, insect, and "beside a colonist" -- so `animal`,
  `isColonist`, `defName`, `kindDef`, `nearestColonist` and
  `nearestColonistDistance` have to ride on every row, unasked.
"""
from pathlib import Path
import unittest


SRC = Path(__file__).parents[1] / "src"
PAWNS = (SRC / "ListPawnsTool.cs").read_text(encoding="utf-8")
CONFIG = (SRC / "PawnConfigTool.cs").read_text(encoding="utf-8")


class AnimalIdSourceTests(unittest.TestCase):
    def test_the_animal_block_carries_a_thing_id(self):
        block = CONFIG.split("AnimalBlock(Pawn pawn)")[1]
        self.assertIn('block["thingId"] = BridgeCommon.SafeString(() => pawn.ThingID);',
                      block[:4000])

    def test_the_settings_block_carries_the_same_id(self):
        self.assertIn('block["thingId"] = BridgeCommon.SafeString(() => pawn.ThingID);',
                      CONFIG.split("SettingsBlock")[1][:2000])

    def test_the_animals_block_is_attached_to_the_row(self):
        self.assertIn('row["animals"] = beast;', PAWNS)

    def test_every_row_carries_the_thing_id_without_any_block(self):
        # Live, 2026-09-11: `pawns.py --wild --json` rows had no id at all,
        # because the id only ever arrived inside settings{} / animals{}. Five
        # ibex share the name "Ibex"; a row with a name and no id names a thing
        # nothing can be written to, and act.py hunt takes an id.
        row = PAWNS.split('row["animals"] = beast;')[0]
        self.assertIn('{ "thingId", BridgeCommon.SafeString(() => pawn.ThingID) }',
                      row)

    def test_the_row_id_is_the_same_call_the_blocks_make(self):
        # One spelling of the id, from one source: the block and the row can
        # never disagree about how to address the same pawn.
        self.assertIn("pawn.ThingID", PAWNS)
        self.assertIn('block["thingId"] = BridgeCommon.SafeString(() => pawn.ThingID);',
                      CONFIG)


class GhoulSourceTests(unittest.TestCase):
    """`Verse.Pawn.IsColonist` ends in `!IsSubhuman` and a ghoul's MutantDef is
    consideredSubhuman, so a colony ghoul is humanlike, ours, and
    isColonist:FALSE. Nothing on the row said so, so every caller that filtered
    on isColonist dropped the colony's ghoul in silence -- Ben Cooper was in
    Threadneedle for its whole run and never once in `pawns.py --roster`."""

    def test_every_row_carries_ghoul_and_player_faction(self):
        row = PAWNS.split('row["animals"] = beast;')[0]
        for field in ("ghoul", "playerFaction"):
            self.assertIn('{ "%s"' % field, row, field)

    def test_the_ghoul_test_is_the_games_own_and_is_wrapped(self):
        self.assertIn("private static bool SafeIsGhoul(Pawn pawn)", PAWNS)
        body = PAWNS.split("private static bool SafeIsGhoul(Pawn pawn)")[1][:200]
        self.assertIn("pawn.IsGhoul", body)
        # Every game read in this file is behind a try: a throw inside a
        # companion read reaches Verse.Log.Error, which pauses the colony.
        self.assertIn("try", body)
        self.assertIn("catch", body)

    def test_the_player_faction_test_never_reaches_faction_of_player(self):
        # Faction.OfPlayer's body is OfPlayerSilentFail followed by Log.Error,
        # and Log.Error's call path contains TickManager.Pause().
        self.assertNotIn("Faction.OfPlayer;", PAWNS)
        self.assertIn("Faction.OfPlayerSilentFail", PAWNS)

    def test_the_ghoul_count_is_reported_and_is_not_folded_into_colonists(self):
        self.assertIn('{ "ghoulCount", ghoulCount }', PAWNS)
        self.assertIn('{ "colonistCount", colonists.Count }', PAWNS)

    def test_the_colonists_only_filter_still_means_free_colonists(self):
        # rota.py, status.py and combat.py read isColonist off the row; nothing
        # here may quietly widen what colonistsOnly selects.
        self.assertIn("ColonistsOnly = colonistsOnly", PAWNS)
        self.assertIn("pawn.IsFreeColonist", PAWNS)

    def test_the_reply_says_out_loud_that_a_ghoul_is_not_a_colonist(self):
        self.assertIn("ghoulIsNotAColonist", PAWNS)


class SkillsSourceTests(unittest.TestCase):
    """`pawns.py --roster` and `--skills` turn "Construction skill too low"
    into a number. Every field they read has to be on the bio{} block."""

    def test_the_bio_block_carries_every_skill_with_level_and_passion(self):
        block = PAWNS.split("// --------------------------------------------------------- skills")[1][:4000]
        for field in ("level", "levelStored", "passion", "disabled", "present"):
            self.assertIn('r["%s"]' % field, block, field)

    def test_the_passion_is_the_word_not_the_enum_ordinal(self):
        block = PAWNS.split("// --------------------------------------------------------- skills")[1][:4000]
        self.assertIn("rec2.passion.ToString()", block)

    def test_skills_is_not_an_argument_the_tool_declares(self):
        # {skills:true} is the argument that made unknownArguments necessary;
        # the block is bio:true and nothing else.
        self.assertNotIn("bool skills = false", PAWNS)


class TickSourceTests(unittest.TestCase):
    """`pawns.py --json` prints the tick the rows were read AT. Comparing a
    position from one moment against one from another is what turned 100 cells
    into "8 from Longhoff" on 2026-09-07."""

    def test_the_reply_carries_the_tick_of_the_read(self):
        self.assertIn('{ "ticksGame"', PAWNS)
        self.assertIn("Find.TickManager.TicksGame", PAWNS)

    def test_the_tick_read_is_wrapped_so_no_game_loaded_is_null(self):
        line = [l for l in PAWNS.splitlines() if '{ "ticksGame"' in l][0]
        self.assertIn("BridgeCommon.TryN", line)


class GroundingFieldsSourceTests(unittest.TestCase):
    def test_every_row_carries_what_a_creature_claim_is_checked_against(self):
        for field in ("defName", "kindDef", "isColonist", "animal", "dead",
                      "nearestColonist", "nearestColonistDistance"):
            self.assertIn('{ "%s"' % field, PAWNS, field)

    def test_those_fields_are_not_behind_an_opt_in_block(self):
        # They are in the row itself, above the first optional block.
        row = PAWNS.split('row["animals"] = beast;')[0]
        for field in ("animal", "nearestColonistDistance"):
            self.assertIn('{ "%s"' % field, row, field)


if __name__ == "__main__":
    unittest.main()
