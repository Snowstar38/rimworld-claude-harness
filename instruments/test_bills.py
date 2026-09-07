"""Offline regressions for bills.py formatting, on faked home/bills payloads."""
import io
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bills


def corpse_ingredient(**kw):
    """The ButcherCorpseFlesh slot: one generic 'corpses', nothing counted."""
    row = {"summary": "1x corpses", "label": "corpses", "generic": True,
           "filterSummary": "corpses", "representativeDefName": "Corpse_Human",
           "needed": 1, "available": 0, "shortfall": 1, "satisfied": False,
           "availableTotal": 0, "availableDefs": [], "availableDefsNotListed": 0,
           "filterAllowsAny": True, "excludedByFilter": 0, "excludedForbidden": 0,
           "excludedOutOfRadius": 0, "isFixedIngredient": False}
    row.update(kw)
    return row


def bill(**kw):
    row = {"index": 0, "label": "Butcher creature", "recipe": "ButcherCorpseFlesh",
           "repeatInfo": "1x", "suspended": False, "paused": False, "finished": False,
           "active": True, "canRunNow": False,
           "blockedBy": ["missing corpses (0/1)"],
           "ingredients": [corpse_ingredient()], "ingredientsSatisfied": False,
           "filter": {"allowedDefCount": 61},
           "config": {"repeatMode": "RepeatCount", "repeatCount": 1}}
    row.update(kw)
    return row


def render(row):
    with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
        bills.print_bench({"label": "butcher spot", "position": {"x": 111, "z": 141},
                           "thingId": "T1", "usableForBills": True, "bills": [row]})
    return out.getvalue()


class GenericIngredientTests(unittest.TestCase):
    def test_generic_slot_is_named_by_the_recipe_not_a_representative_def(self):
        text = render(bill())
        self.assertIn("corpses", text)
        self.assertNotIn("human corpse", text)

    def test_old_payload_without_the_generic_flag_is_still_not_named_by_one_def(self):
        # What the installed DLL sent for three sessions: label from DisplayDef.
        ing = corpse_ingredient(label="human corpse")
        del ing["generic"]
        text = render(bill(ingredients=[ing]))
        self.assertNotIn("human corpse", text)
        self.assertIn("missing corpses (0/1)", text)

    def test_unread_counts_print_as_question_marks_not_zero(self):
        ing = corpse_ingredient(needed=None, available=None, shortfall=None)
        text = render(bill(ingredients=[ing]))
        self.assertIn("need ?, have ?", text)
        self.assertIn("CANNOT RUN: missing corpses (counts NOT READ)", text)
        self.assertNotIn("(0/1)", text)

    def test_a_counted_slot_keeps_its_own_def_label(self):
        ing = corpse_ingredient(label="deer corpse", generic=False, available=3,
                                availableTotal=3, satisfied=True,
                                availableDefs=[{"defName": "Corpse_Deer", "count": 3}])
        self.assertEqual("deer corpse", bills._noun(ing))


class ExclusionSummaryTests(unittest.TestCase):
    def test_corpse_exclusions_print_beside_the_meat_ones(self):
        line = bills._filter_line({"allowedDefCount": 61,
                                   "allowsHumanCorpses": False,
                                   "allowsInsectCorpses": True})
        self.assertIn("human corpses excluded", line)
        self.assertIn("insect corpses ALLOWED", line)

    def test_human_meat_summary_is_unchanged(self):
        line = bills._filter_line({"allowedDefCount": 40, "allowsHumanMeat": False,
                                   "allowsInsectMeat": False})
        self.assertIn("human meat excluded", line)
        self.assertIn("insect meat EXCLUDED", line)

    def test_a_payload_without_the_corpse_keys_says_nothing_about_corpses(self):
        line = bills._filter_line({"allowedDefCount": 61})
        self.assertNotIn("corpses", line)


class FinishedBillTests(unittest.TestCase):
    def test_zero_repeat_count_reads_as_finished_not_as_a_verdict(self):
        text = render(bill(repeatInfo="0x", canRunNow=False,
                           config={"repeatMode": "RepeatCount", "repeatCount": 0}))
        self.assertIn("FINISHED (0 left)", text)
        self.assertIn("FINISHED", text.splitlines()[1])
        self.assertNotIn("CANNOT RUN", text)
        self.assertNotIn("CAN RUN NOW", text)

    def test_a_finished_bill_that_reads_as_runnable_is_still_marked_finished(self):
        text = render(bill(canRunNow=True, blockedBy=[],
                           ingredients=[corpse_ingredient(available=3, satisfied=True)],
                           config={"repeatMode": "RepeatCount", "repeatCount": 0}))
        self.assertIn("FINISHED (0 left)", text)
        self.assertNotIn("CAN RUN NOW", text)

    def test_a_live_repeat_count_bill_is_not_marked_finished(self):
        self.assertFalse(bills._finished(bill()))

    def test_an_unread_repeat_count_is_not_finished(self):
        self.assertFalse(bills._finished(
            bill(config={"repeatMode": "RepeatCount", "repeatCount": None})))


class TargetCountLineTests(unittest.TestCase):
    """`have N` is the game's zone-scoped count, and must say so."""

    def config(self, **kw):
        cfg = {"repeatMode": "TargetCount", "targetCount": 40, "productCount": 0}
        cfg.update(kw)
        return cfg

    def test_the_have_count_names_what_it_counts(self):
        line = bills._config_line(self.config())
        self.assertIn("until you have 40", line)
        self.assertIn("have 0 in counted storage", line)

    def test_stored_and_total_print_when_the_payload_carries_them(self):
        line = bills._config_line(self.config(productCountStored=58,
                                              productCountOnMap=98))
        self.assertIn("have 0 in counted storage (58 stored, 98 total)", line)

    def test_companion_emits_the_two_wider_product_counts(self):
        from pathlib import Path
        source = (Path(HERE).parent / "companion" / "src" /
                  "BillCommon.cs").read_text(encoding="utf-8")
        self.assertIn('{ "productCountStored", ProductCountStored(production) }',
                      source)
        self.assertIn('{ "productCountOnMap", ProductCountOnMap(production) }',
                      source)
        # Stored is storage membership, not ResourceCounter: a def RimWorld does
        # not count as a resource would silently read 0 there.
        self.assertIn("t.IsInAnyStorage()", source)

    def test_a_half_filled_payload_still_prints_the_plain_form(self):
        line = bills._config_line(self.config(productCountStored=58))
        self.assertIn("have 0 in counted storage", line)
        self.assertNotIn("stored,", line)

    def test_an_unread_count_says_nothing_about_storage(self):
        line = bills._config_line(self.config(productCount=None))
        self.assertIn("until you have 40", line)
        self.assertNotIn("have 0", line)
        self.assertNotIn("counted storage", line)


def bench_row(**kw):
    row = {"thingId": "ElectricStove123", "defName": "ElectricStove",
           "label": "electric stove", "position": {"x": 120, "z": 140}}
    row.update(kw)
    return row


class BenchAddressTests(unittest.TestCase):
    """A bare `x,z` is resolved here; the companion takes only defName@x,z."""

    def calls_for(self, spec, benches):
        seen = []

        def fake_call(args):
            seen.append(args)
            if args.get("action") == "list":
                return {"action": "list", "success": True, "benches": benches}
            return {"action": "recipes", "success": True, "benches": benches,
                    "recipes": []}

        with mock.patch.object(bills, "call", fake_call):
            return bills.recipes(spec), seen

    def test_a_bare_cell_resolves_to_the_thing_id(self):
        r, seen = self.calls_for("120,140", [bench_row()])
        self.assertTrue(r.get("success"))
        self.assertEqual([{"action": "list"},
                          {"action": "recipes", "bench": "ElectricStove123"}], seen)

    def test_spaces_around_the_comma_are_the_same_address(self):
        self.assertEqual((120, 140), bills._cell("120, 140"))
        _, seen = self.calls_for(" 120 , 140 ", [bench_row()])
        self.assertEqual("ElectricStove123", seen[-1]["bench"])

    def test_a_cell_split_by_the_shell_is_rejoined(self):
        self.assertEqual("120,140", bills._bench_spec(["120,", "140"]))
        self.assertEqual("120,140", bills._bench_spec(["120", ",", "140"]))

    def test_a_label_is_never_rejoined_or_resolved_locally(self):
        self.assertEqual("stove", bills._bench_spec(["stove", "north"]))
        _, seen = self.calls_for("stove", [bench_row()])
        self.assertEqual([{"action": "recipes", "bench": "stove"}], seen)

    def test_the_defname_at_cell_form_still_goes_straight_to_the_tool(self):
        _, seen = self.calls_for("ElectricStove@120,140", [bench_row()])
        self.assertEqual([{"action": "recipes", "bench": "ElectricStove@120,140"}], seen)

    def test_an_empty_cell_is_refused_with_candidates_not_guessed(self):
        r, seen = self.calls_for("1,2", [bench_row()])
        self.assertFalse(r.get("success"))
        self.assertIn("no bill giver stands at 1,2", r.get("error"))
        self.assertEqual([{"action": "list"}], seen)

    def test_two_benches_on_one_cell_are_refused(self):
        r, _ = self.calls_for("120,140", [bench_row(), bench_row(thingId="T2")])
        self.assertFalse(r.get("success"))
        self.assertIn("2 bill givers occupy 120,140", r.get("error"))
        self.assertEqual(2, len(r.get("candidates")))


class ForbiddenBlockerTests(unittest.TestCase):
    def test_all_forbidden_leads_the_cannot_run_line(self):
        ing = corpse_ingredient(excludedForbidden=3)
        text = render(bill(ingredients=[ing]))
        self.assertIn("CANNOT RUN: 3 corpses on the map but all forbidden "
                      "-- unforbid them", text)
        self.assertNotIn("missing corpses", text)

    def test_forbidden_comes_before_the_other_reasons(self):
        ing = corpse_ingredient(excludedForbidden=3)
        reasons = bills._reasons(bill(ingredients=[ing],
                                      blockedBy=["missing corpses (0/1)",
                                                 "bench unpowered"]))
        self.assertEqual(2, len(reasons))
        self.assertIn("all forbidden", reasons[0])
        self.assertEqual("bench unpowered", reasons[1])

    def test_something_usable_on_the_map_is_not_reported_as_all_forbidden(self):
        ing = corpse_ingredient(excludedForbidden=3, available=1, needed=2)
        reasons = bills._reasons(bill(ingredients=[ing]))
        self.assertEqual(["missing corpses (1/2)"], reasons)

    def test_a_bill_with_no_ingredient_rows_keeps_the_payload_reasons(self):
        self.assertEqual(["missing corpses (0/1)"],
                         bills._reasons(bill(ingredients=[])))


class WriteBenchTests(unittest.TestCase):
    """2026-09-07: `bills.py add 128,139 ...` refused a cell `recipes 128,139`
    accepted. Every verb takes the same bench forms."""

    def calls_for(self, action, spec, benches, **kw):
        seen = []

        def fake_call(args):
            seen.append(args)
            if args.get("action") == "list":
                return {"action": "list", "success": True, "benches": benches}
            return {"action": args["action"], "success": True,
                    "benches": benches, "write": {}}

        with mock.patch.object(bills, "call", fake_call):
            return bills.write(action, spec, **kw), seen

    def test_add_resolves_a_bare_cell_to_the_thing_id(self):
        r, seen = self.calls_for("add", "120,140", [bench_row()],
                                 recipe="Simple meal")
        self.assertTrue(r.get("success"))
        self.assertEqual("ElectricStove123", seen[-1]["bench"])

    def test_an_empty_cell_is_refused_before_any_write(self):
        r, seen = self.calls_for("add", "1,2", [bench_row()], recipe="x")
        self.assertFalse(r.get("success"))
        self.assertIn("no bill giver stands at 1,2", r.get("error"))
        self.assertEqual([{"action": "list"}], seen)


class FlagTests(unittest.TestCase):
    """`--count 1` was swallowed into the recipe name."""

    def test_an_unknown_flag_is_an_argument_error_naming_repeat(self):
        with self.assertRaises(SystemExit) as caught:
            bills.check_flags(["add", "stove", "make rifle", "--count", "1"])
        self.assertIn("--count", str(caught.exception))
        self.assertIn("--repeat N", str(caught.exception))

    def test_the_flags_that_exist_pass_through(self):
        bills.check_flags(["add", "stove", "meal", "--repeat", "4", "--do",
                           "--no-watch"])

    def test_the_bench_and_the_recipe_are_split_by_the_cell_not_by_count(self):
        self.assertEqual(("128,139", ["make", "bolt-action", "rifle"]),
                         bills._bench_and_rest(["128,139", "make", "bolt-action",
                                                "rifle"]))


class RottenIngredientTests(unittest.TestCase):
    """Six SKELETONS were reported as "forbidden -- unforbid them"."""

    def test_rot_stage_is_named_and_unforbidding_is_not_advised(self):
        ing = corpse_ingredient(excludedByFilter=6, excludedNotFresh=6)
        reasons = bills._reasons(bill(ingredients=[ing]))
        text = "; ".join(reasons)
        self.assertIn("ROTTEN or DESSICATED", text)
        self.assertIn("unforbidding them changes nothing", text)
        self.assertNotIn("unforbid them", text.replace(
            "unforbidding them changes nothing", ""))

    def test_a_genuinely_forbidden_stack_still_says_unforbid(self):
        ing = corpse_ingredient(excludedForbidden=3)
        self.assertIn("unforbid them", "; ".join(bills._reasons(bill(ingredients=[ing]))))


if __name__ == "__main__":
    unittest.main()
