"""Offline invariants for the home/bills write path and ingredient scope."""
from pathlib import Path
import re
import unittest


SRC = Path(__file__).parent.parent / "src"
TOOL = (SRC / "BillsTool.cs").read_text(encoding="utf-8")
COMMON = (SRC / "BillCommon.cs").read_text(encoding="utf-8")
# BridgeCommon.Num is read here rather than in a file of its own because the two
# callers that broke on it are both in BillCommon, and a helper that rots while
# the tests on its callers stay green is exactly what this file exists to stop.
BRIDGE = (SRC / "BridgeCommon.cs").read_text(encoding="utf-8")

# The comments name the wrong turns on purpose ("NoPassClosedDoors is the
# opposite mistake"), so a check that a name is absent has to read the code
# without them.
COMMON_CODE = "\n".join(l for l in COMMON.splitlines()
                        if not l.lstrip().startswith("//"))


class TargetCountWriteTests(unittest.TestCase):
    """`set --target N` printed a bill sheet and left the bill alone. The write
    path must set the mode AND the count, and must say when it moved nothing."""

    def test_a_target_count_implies_the_target_count_mode(self):
        block = re.search(r"if \(request\.TargetCount >= 0\)\s*\{(?P<body>.*?)\n            \}",
                          TOOL, re.DOTALL)
        self.assertIsNotNone(block)
        body = block.group("body")
        self.assertIn("options.TargetCount = request.TargetCount", body)
        self.assertIn("BillRepeatModeDefOf.TargetCount", body)

    def test_both_fields_are_written_to_the_production_bill(self):
        self.assertIn("production.repeatMode = options.RepeatMode", TOOL)
        self.assertIn("production.targetCount = options.TargetCount.Value", TOOL)
        self.assertIn("production.repeatCount = options.RepeatCount.Value", TOOL)

    def test_the_reply_carries_the_ask_beside_the_read_back(self):
        self.assertIn('{ "requestedOptions", RequestedOptions(request) }', TOOL)
        self.assertIn('{ "optionsNotApplied", predicted ? new List<object>() '
                      ': NotApplied(request, after) }', TOOL)

    def test_every_settable_option_appears_in_the_ask(self):
        block = re.search(r"private static Dictionary<string, object> RequestedOptions"
                          r".*?\n        \}", TOOL, re.DOTALL).group(0)
        for key in ("repeatMode", "repeatCount", "targetCount", "unpauseWhenYouHave",
                    "pauseWhenSatisfied", "suspended", "ingredientSearchRadius",
                    "skillMin", "skillMax", "worker", "storeMode",
                    "allow", "only", "disallow"):
            self.assertIn('asked["%s"]' % key, block)

    def test_the_mismatch_check_reads_the_bill_not_the_request(self):
        block = re.search(r"private static List<object> NotApplied"
                          r".*?\n        \}", TOOL, re.DOTALL).group(0)
        # Every comparison is against the config read back from the stack.
        self.assertIn('Mismatch(missed, config, "targetCount", request.TargetCount)', block)
        self.assertIn('Mismatch(missed, config, "repeatCount", request.RepeatCount)', block)
        self.assertIn('wantedMode = "TargetCount"', block)
        self.assertIn("asked for, the bill reads", block)

    def test_a_dry_run_claims_no_read_back(self):
        self.assertIn("predicted ? new List<object>()", TOOL)


class ScrollToNewBillTests(unittest.TestCase):
    def test_the_scroll_is_reflected_guarded_and_decorative(self):
        block = re.search(r"private static string ScrollBillsTabToNewest"
                          r".*?\n        \}", TOOL, re.DOTALL).group(0)
        self.assertIn('PrivateInstanceField(typeof(ITab_Bills), "scrollPosition")', block)
        self.assertIn("new UnityEngine.Vector2(0f, 100000f)", block)
        self.assertIn("catch (Exception e)", block)
        # Every failure path returns a sentence, never throws into the write.
        self.assertIn('return "this bench has no ITab_Bills"', block)

    def test_only_a_real_add_with_an_open_tab_scrolls(self):
        guard = re.search(r'if \(request\.Action == "add" && applied '
                          r'&& plan\.Session != null && plan\.Session\.TabOpened\)', TOOL)
        self.assertIsNotNone(guard)
        self.assertIn('watchBlock["scrolledToNewBill"] = scrolled;', TOOL)
        self.assertIn('watchBlock["scrollNote"] = scrollNote;', TOOL)


class ReachAndReserveTests(unittest.TestCase):
    """The scan said CAN RUN for a stack behind a locked door."""

    def test_reachability_is_measured_from_the_interaction_cell(self):
        self.assertIn("private static IntVec3 ReachRoot(Thing bench, IntVec3? benchCell)", COMMON)
        self.assertIn("bench.InteractionCell", COMMON)

    def test_the_traverse_parms_are_the_hauling_ones(self):
        self.assertIn("PathEndMode.ClosestTouch", COMMON)
        self.assertIn(
            "TraverseParms.For(pawn, Danger.Deadly, TraverseMode.ByPawn, canBashDoors: false)",
            COMMON)

    def test_only_a_definite_no_subtracts(self):
        block = re.search(r"reachAsked\+\+;(?P<body>.*?)have \+= item\.Stack;", COMMON, re.DOTALL)
        self.assertIsNotNone(block)
        body = block.group("body")
        self.assertIn("if (reachable != null)", body)
        self.assertIn("if (!reachable.Value) { excludedUnreachable += item.Stack; continue; }", body)
        # A reserved stack is tallied and stays in the count.
        self.assertIn("excludedReserved += item.Stack;", body)
        self.assertNotIn("excludedReserved += item.Stack; continue;", body)

    def test_an_unasked_question_is_not_reported_as_all_reachable(self):
        self.assertIn('row["reachabilityChecked"] = reachAsked == 0 || reachAnswered == reachAsked;',
                      COMMON)
        self.assertIn('row["reservationChecked"] = items.Reserved != null;', COMMON)

    def test_the_reach_check_is_cached_and_budgeted(self):
        self.assertIn("internal const int MaxReachChecks = 4000;", COMMON)
        self.assertIn("ReachBudgetSpent = true;", COMMON)
        self.assertIn("Map.cellIndices.CellToIndex(root)", COMMON)
        self.assertIn("byCell.TryGetValue(cellIndex, out cached)", COMMON)

    def test_reservations_are_read_without_touching_a_faction(self):
        block = re.search(r"private static HashSet<Thing> ReservedThings"
                          r".*?\n        \}", COMMON, re.DOTALL).group(0)
        self.assertIn("map.reservationManager.AllReservedThings()", block)
        self.assertNotIn("Faction.OfPlayer", block)
        # Unreadable is null, which the row reports as "not checked".
        self.assertIn("catch { return null; }", block)

    def test_an_unreachable_shortfall_names_the_route_not_a_shortage(self):
        self.assertIn("have NO ROUTE from this", COMMON)


class CountScopeTests(unittest.TestCase):
    """Two listings printed 4/15 and 14/15 for one bill; neither said what it
    counted."""

    def test_every_row_carries_the_scope_it_counted_in(self):
        self.assertIn('row["searchRadius"] = unlimited ? (object)null : (int)radius;', COMMON)
        self.assertIn('row["radiusUnlimited"] = unlimited;', COMMON)

    def test_every_payload_carries_the_tick_it_was_taken_at(self):
        self.assertEqual(3, TOOL.count('countedAtTick"] = items.Tick;'))
        self.assertIn("index.Tick = BridgeCommon.TryN(() => Find.TickManager.TicksGame);", COMMON)

    def test_the_notes_say_a_carried_stack_is_in_no_count(self):
        self.assertIn("A stack a pawn is CARRYING is despawned", TOOL)
        self.assertIn("excludedUnreachable", TOOL)
        self.assertIn("excludedReserved", TOOL)


class NumericPayloadReadbackTests(unittest.TestCase):
    """`excludedUnreachable:1` sat on the row and `blockedBy` never mentioned it.

    BridgeCommon.Num tested `v is double`, and a count is boxed as an `int`, so
    every count a payload row was asked for read back as 0. Two live branches
    were dead on that: the NO ROUTE line and the "filter excludes N on map"
    line, both of which read an int back off the row just built.
    """

    def _num_body(self):
        block = re.search(r"internal static double Num\(Dictionary<string, object> d, string key\)"
                          r".*?\n        \}", BRIDGE, re.DOTALL)
        self.assertIsNotNone(block)
        return block.group(0)

    def _is_numeric_body(self):
        block = re.search(r"private static bool IsNumericBox\(object v\).*?\n        \}",
                          BRIDGE, re.DOTALL)
        self.assertIsNotNone(block)
        return block.group(0)

    def test_num_no_longer_demands_a_boxed_double(self):
        body = self._num_body()
        self.assertNotIn("d.TryGetValue(key, out v) && v is double", body)
        # The three shapes this codebase actually stores, unboxed on the fast path.
        self.assertIn("if (v is double) return (double)v;", body)
        self.assertIn("if (v is int) return (int)v;", body)
        self.assertIn("if (v is float) return (float)v;", body)

    def test_every_other_numeric_box_converts_rather_than_reading_as_zero(self):
        body = self._num_body()
        self.assertIn("IsNumericBox(v)", body)
        self.assertIn("v as IConvertible", body)
        self.assertIn("convertible.ToDouble(CultureInfo.InvariantCulture)", body)
        guard = self._is_numeric_body()
        for numeric in ("sbyte", "byte", "short", "ushort", "int", "uint",
                        "long", "ulong", "float", "double", "decimal"):
            self.assertIn("v is " + numeric, guard)

    def test_a_bool_or_a_string_is_not_a_number(self):
        """Both are IConvertible. Answering 1.0 for `true` would dress a wrong
        key up as a number somebody read."""
        guard = self._is_numeric_body()
        self.assertNotIn("v is bool", guard)
        self.assertNotIn("v is char", guard)
        self.assertNotIn("v is string", guard)

    def test_num_never_throws_and_never_guesses(self):
        body = self._num_body()
        self.assertIn("if (d == null || !d.TryGetValue(key, out v) || v == null)", body)
        self.assertIn("catch { return 0.0; }", body)

    def test_the_two_int_payloads_that_were_dead_are_still_read_through_num(self):
        self.assertIn('excludedByFilter += (int)BridgeCommon.Num(row, "excludedByFilter");',
                      COMMON)
        self.assertIn('var stranded = (int)BridgeCommon.Num(row, "excludedUnreachable");',
                      COMMON)
        # And both are written as plain ints, which is the half that made the
        # `is double` test wrong.
        self.assertIn('row["excludedByFilter"] = excludedByFilter;', COMMON)
        self.assertIn('row["excludedUnreachable"] = excludedUnreachable;', COMMON)


class ByPawnTraverseTests(unittest.TestCase):
    """The scan still said CAN RUN for a stack behind a locked door.

    TraverseMode.PassDoors makes Verse.Region.Allows answer `return !flag` --
    a fence test with the door ignored entirely -- so no door could ever make an
    ingredient unreachable. NoPassClosedDoors is the opposite mistake: its arm
    is `door == null || door.FreePassage`, refusing every ordinary closed door.
    """

    def _can_reach(self):
        block = re.search(r"internal bool\? CanReach\(IntVec3 root, IntVec3 cell, Pawn traverser\)"
                          r".*?\n            \}", COMMON, re.DOTALL)
        self.assertIsNotNone(block)
        return block.group(0)

    def test_the_route_is_asked_as_a_pawn_walks_it(self):
        body = self._can_reach()
        self.assertIn(
            "TraverseParms.For(pawn, Danger.Deadly, TraverseMode.ByPawn, canBashDoors: false)",
            body)
        self.assertNotIn("NoPassClosedDoors", COMMON_CODE)

    def test_a_map_with_no_colonist_falls_back_rather_than_passing_null(self):
        """TraverseParms.For(pawn: null) is itself a Log.Error, and Log.Error
        calls TickManager.Pause()."""
        body = self._can_reach()
        self.assertIn("var pawn = traverser;", body)
        self.assertIn("pawn == null", body)
        self.assertIn("TraverseParms.For(TraverseMode.PassDoors, Danger.Deadly, false)", body)

    def test_the_traverser_is_the_bills_own_worker_when_it_names_one(self):
        block = re.search(r"internal Pawn TraverserFor\(Bill bill\).*?\n            \}",
                          COMMON, re.DOTALL).group(0)
        self.assertIn("bill.PawnRestriction", block)
        self.assertIn("UsableTraverser(restricted)", block)
        self.assertIn("UsableTraverser(Colonist) ? Colonist : null", block)

    def test_the_fallback_colonist_never_touches_faction_of_player(self):
        """MapPawns.FreeColonists and .FreeColonistsSpawned both reach
        Faction.OfPlayer; AllPawnsSpawned filtered on IsFreeColonist does not."""
        block = re.search(r"private static Pawn RepresentativeColonist\(Map map\)"
                          r".*?\n            \}", COMMON, re.DOTALL).group(0)
        self.assertIn("map.mapPawns.AllPawnsSpawned", block)
        self.assertIn("pawn.IsFreeColonist", block)
        self.assertNotIn("Faction.OfPlayer", block)
        self.assertNotIn("FreeColonistsSpawned", COMMON_CODE)

    def test_a_traverser_on_another_map_is_screened_before_can_reach_sees_it(self):
        """Reachability.CanReach Log.Errors on a pawn spawned on another map and
        answers a flat false for an unspawned one."""
        block = re.search(r"private bool UsableTraverser\(Pawn pawn\).*?\n            \}",
                          COMMON, re.DOTALL).group(0)
        self.assertIn("pawn.Spawned", block)
        self.assertIn("pawn.Dead", block)
        self.assertIn("pawn.Map, (Map)null) == Map", block)
        self.assertIn("if (!UsableTraverser(traverser))", self._can_reach())

    def test_the_cache_is_keyed_on_the_traverser_too(self):
        """Two bills on one bench can name two different workers, and a door one
        may open is a door the other may not."""
        self.assertIn("Dictionary<int, Dictionary<int, Dictionary<int, bool>>> _reach",
                      COMMON)
        body = self._can_reach()
        self.assertIn("traverser.thingIDNumber", body)
        self.assertIn("_reach.TryGetValue(pawnKey, out byRoot)", body)

    def test_the_reach_budget_survived_the_change(self):
        body = self._can_reach()
        self.assertIn("if (ReachBudget <= 0)", body)
        self.assertIn("ReachBudgetSpent = true;", body)
        self.assertIn("ReachBudget--;", body)

    def test_one_traverser_is_resolved_per_bill_not_per_ingredient(self):
        self.assertIn("var traverser = items == null ? null : items.TraverserFor(bill);",
                      COMMON)
        self.assertIn("items.CanReach(reachRoot, new IntVec3(item.X, 0, item.Z), traverser)",
                      COMMON)

    def test_the_payload_note_no_longer_claims_the_scan_is_pawnless(self):
        self.assertNotIn("TryFindBestBillIngredients WITHOUT a pawn", TOOL)
        self.assertIn("TraverseMode.ByPawn", TOOL)


if __name__ == "__main__":
    unittest.main()
