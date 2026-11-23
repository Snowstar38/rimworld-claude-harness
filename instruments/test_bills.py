"""Offline regressions for bills.py formatting, on faked home/bills payloads."""
import io
import json
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


class VerbOrderTests(unittest.TestCase):
    """`bills.py <bench> set 1 --target 30 --do` printed a bill sheet and wrote
    nothing: the bench sat where the verb was looked for."""

    def test_the_verb_may_follow_the_bench(self):
        self.assertEqual(("set", "ElectricStove123", ["1"], None),
                         bills._split_verb(["ElectricStove123", "set", "1"]))

    def test_the_verb_may_lead(self):
        self.assertEqual(("set", "ElectricStove123", ["1"], None),
                         bills._split_verb(["set", "ElectricStove123", "1"]))

    def test_a_bench_first_cell_still_costs_only_its_own_words(self):
        self.assertEqual(("set", "120,140", ["1"], None),
                         bills._split_verb(["120,", "140", "set", "1"]))

    def test_allow_parses_after_the_bench(self):
        self.assertEqual(("allow", "T1", ["0", "Corpse_Human"], None),
                         bills._split_verb(["T1", "allow", "0", "Corpse_Human"]))

    def test_a_bare_bench_is_still_a_listing(self):
        verb, bench, rest, refusal = bills._split_verb(["ElectricStove123"])
        self.assertIsNone(verb)
        self.assertEqual("ElectricStove123", bench)
        self.assertEqual([], rest)
        self.assertIsNone(refusal)

    def test_nothing_at_all_is_still_the_whole_map(self):
        self.assertEqual((None, None, [], None), bills._split_verb([]))

    def test_an_unrecognised_verb_is_refused_and_names_the_verbs(self):
        verb, bench, rest, refusal = bills._split_verb(["T1", "sett", "1"])
        self.assertIsNone(verb)
        self.assertIsNotNone(refusal)
        self.assertIn("'sett'", refusal)
        for word in bills.VERBS:
            self.assertIn(word, refusal)
        self.assertIn("nothing was read", refusal)

    def test_an_unconsumed_argument_names_the_verbs_too(self):
        text = bills._unconsumed("set", ["Corpse_Human"])
        self.assertIn("'Corpse_Human'", text)
        self.assertIn("bills.py set <bench> <index>", text)
        for word in bills.VERBS:
            self.assertIn(word, text)


class WriteDispatchTests(unittest.TestCase):
    """What main() actually sends for each order and each verb."""

    def run_cli(self, argv):
        sent = []

        def fake_call(args):
            sent.append(args)
            if args.get("action") == "list" and "bench" not in args:
                return {"action": "list", "success": True, "benches": [bench_row()]}
            return {"action": args["action"], "success": True,
                    "benches": [bench_row()], "applied": not args.get("dryRun"),
                    "write": {"action": args["action"], "before": {}, "after": {},
                              "changed": ["targetCount 80 -> 30"],
                              "requestedOptions": {}, "optionsNotApplied": []},
                    "watch": {"shown": False, "reason": "test"}}

        with mock.patch.object(bills, "call", fake_call), \
                mock.patch.object(bills.rim, "init", lambda *a, **k: None), \
                mock.patch.object(sys, "argv", ["bills.py"] + argv), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = bills.main()
        return code, sent, out.getvalue()

    def test_a_refused_write_exits_non_zero_under_json_too(self):
        """Every other path returns 1 for success:false -- print_write, the
        listing refusal, and the implicit-write refusal, which was special-cased
        by hand. The general --json path returned 0, so a scripted caller could
        not tell a refused write from a done one."""
        sent = []

        def fake_call(args):
            sent.append(args)
            if args.get("action") == "list" and "bench" not in args:
                return {"action": "list", "success": True,
                        "benches": [bench_row()]}
            return {"action": args["action"], "success": False,
                    "error": "index 99 is out of range for this bench"}

        with mock.patch.object(bills, "call", fake_call), \
                mock.patch.object(bills.rim, "init", lambda *a, **k: None), \
                mock.patch.object(sys, "argv",
                                  ["bills.py", "ElectricStove123", "set", "99",
                                   "--target", "30", "--do", "--json"]), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = bills.main()
        body = json.loads(out.getvalue())
        self.assertIs(False, body["success"])
        self.assertEqual(1, code)

    def test_bench_first_set_target_sends_the_write(self):
        code, sent, text = self.run_cli(
            ["ElectricStove123", "set", "1", "--target", "30", "--do"])
        self.assertEqual(0, code)
        self.assertEqual("set", sent[-1]["action"])
        self.assertEqual(30, sent[-1]["targetCount"])
        self.assertEqual(1, sent[-1]["index"])
        self.assertFalse(sent[-1]["dryRun"])
        self.assertNotIn("BENCHES:", text)

    def test_verb_first_set_target_sends_the_same_write(self):
        _, sent, _ = self.run_cli(
            ["set", "ElectricStove123", "1", "--target", "30", "--do"])
        self.assertEqual(30, sent[-1]["targetCount"])
        self.assertEqual(1, sent[-1]["index"])

    def test_bench_first_add_sends_the_recipe(self):
        _, sent, _ = self.run_cli(
            ["ElectricStove123", "add", "Simple", "meal", "--target", "12", "--do"])
        self.assertEqual("add", sent[-1]["action"])
        self.assertEqual("Simple meal", sent[-1]["recipe"])
        self.assertEqual(12, sent[-1]["targetCount"])

    def test_allow_as_a_verb_is_a_set_with_the_filter_names(self):
        _, sent, _ = self.run_cli(
            ["ElectricStove123", "allow", "0", "Corpse_Human", "--do"])
        self.assertEqual("set", sent[-1]["action"])
        self.assertEqual(0, sent[-1]["index"])
        self.assertEqual("Corpse_Human", sent[-1]["allow"])

    def test_disallow_takes_several_names(self):
        _, sent, _ = self.run_cli(
            ["only", "ElectricStove123", "0", "Meat_Squirrel,Meat_Deer", "--do"])
        self.assertEqual("Meat_Squirrel,Meat_Deer", sent[-1]["only"])

    def test_an_unknown_verb_writes_and_reads_nothing(self):
        code, sent, text = self.run_cli(["ElectricStove123", "sett", "1", "--do"])
        self.assertEqual(1, code)
        self.assertEqual([], sent)
        self.assertIn("REFUSED", text)
        self.assertNotIn("BENCHES:", text)

    def test_a_trailing_word_on_set_is_refused_not_swallowed(self):
        code, sent, text = self.run_cli(
            ["ElectricStove123", "set", "1", "Corpse_Human", "--do"])
        self.assertEqual(1, code)
        self.assertEqual([], sent)
        self.assertIn("REFUSED", text)


class NothingChangedTests(unittest.TestCase):
    """A bill sheet printed unchanged is the failure a caller cannot see."""

    def test_a_field_that_did_not_take_is_named_loudly(self):
        text = bills._nothing_changed(
            {"requestedOptions": {"targetCount": 30},
             "optionsNotApplied": ["targetCount 30 asked for, the bill reads 80"]},
            True)
        self.assertTrue(text.startswith("WROTE NOTHING:"))
        self.assertIn("the bill reads 80", text)
        self.assertIn("python bills.py <bench>", text)

    def test_an_already_correct_bill_says_so_instead_of_alarming(self):
        text = bills._nothing_changed(
            {"requestedOptions": {"targetCount": 30}, "optionsNotApplied": []}, True)
        self.assertIn("already holds every value asked for", text)
        self.assertIn("targetCount=30", text)

    def test_an_add_that_left_the_count_alone_says_it_did_not_land(self):
        text = bills._nothing_changed(
            {"action": "add", "after": {"billCount": 3},
             "requestedOptions": {}, "optionsNotApplied": []}, True)
        self.assertIn("still holds 3 bill(s)", text)
        self.assertIn("the add did not land", text)

    def test_a_move_to_where_it_already_is_is_not_alarming(self):
        text = bills._nothing_changed(
            {"action": "move", "requestedOptions": {}, "optionsNotApplied": []}, True)
        self.assertIn("same order", text)
        self.assertIn("does not know which", text)

    def test_a_dry_run_says_would_and_never_blames_a_field(self):
        text = bills._nothing_changed(
            {"afterIsPredicted": True, "requestedOptions": {"targetCount": 30},
             "optionsNotApplied": ["targetCount 30 asked for, the bill reads 80"]},
            False)
        self.assertTrue(text.startswith("NOTHING WOULD CHANGE:"))
        self.assertNotIn("did not take", text)

    def test_the_write_printer_prints_the_ask_and_the_loud_line(self):
        r = {"success": True, "applied": True,
             "benches": [bench_row()],
             "write": {"action": "set", "before": {"billCount": 1, "bills": []},
                       "after": {"billCount": 1, "bills": []},
                       "changed": [],
                       "requestedOptions": {"targetCount": 30},
                       "optionsNotApplied":
                           ["targetCount 30 asked for, the bill reads 80"]},
             "watch": {"shown": False, "reason": "watch:false"}}
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            bills.print_write(r)
        text = out.getvalue()
        self.assertIn("asked:     targetCount=30", text)
        self.assertIn("changed:   nothing", text)
        self.assertIn("WROTE NOTHING:", text)

    def test_a_write_that_moved_something_prints_no_loud_line(self):
        r = {"success": True, "applied": True,
             "benches": [bench_row()],
             "write": {"action": "set", "before": {"billCount": 1, "bills": []},
                       "after": {"billCount": 1, "bills": []},
                       "changed": ["targetCount 80 -> 30"],
                       "requestedOptions": {"targetCount": 30},
                       "optionsNotApplied": []},
             "watch": {"shown": False, "reason": "watch:false"}}
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            bills.print_write(r)
        text = out.getvalue()
        self.assertIn("changed:   targetCount 80 -> 30", text)
        self.assertNotIn("WROTE NOTHING", text)


class ScopeAndReachTests(unittest.TestCase):
    """One bill read 4/15 and 14/15 seconds apart with nothing saying what
    either number covered."""

    def test_an_unlimited_radius_is_labelled_map_wide(self):
        ing = corpse_ingredient(available=4, needed=15, shortfall=11,
                                radiusUnlimited=True, searchRadius=None)
        self.assertIn("map-wide", bills._ing_line(ing))
        self.assertIn("(4/15 map-wide)", "; ".join(bills._reasons(bill(ingredients=[ing]))))

    def test_a_limited_radius_names_its_cells(self):
        ing = corpse_ingredient(available=4, needed=15, shortfall=11,
                                radiusUnlimited=False, searchRadius=24)
        self.assertIn("within 24 cells", bills._ing_line(ing))
        self.assertIn("(4/15 within 24 cells)",
                      "; ".join(bills._reasons(bill(ingredients=[ing]))))

    def test_a_payload_without_the_scope_keys_says_nothing_about_scope(self):
        text = bills._ing_line(corpse_ingredient())
        self.assertNotIn("map-wide", text)
        self.assertNotIn("cells", text)
        self.assertEqual(["missing corpses (0/1)"],
                         bills._reasons(bill(ingredients=[corpse_ingredient()])))

    def test_reserved_stacks_are_told_and_still_counted(self):
        ing = corpse_ingredient(available=3, needed=1, shortfall=0, satisfied=True,
                                excludedReserved=2)
        self.assertIn("{2 reserved}", bills._ing_line(ing))

    def test_unreachable_stacks_say_they_are_not_counted(self):
        ing = corpse_ingredient(excludedUnreachable=6)
        self.assertIn("{6 unreachable, not counted}", bills._ing_line(ing))

    def test_an_unchecked_reachability_is_not_reported_as_all_reachable(self):
        ing = corpse_ingredient(excludedUnreachable=0, reachabilityChecked=False)
        self.assertIn("{reachability NOT checked}", bills._ing_line(ing))

    def test_a_partial_scan_says_both_what_it_found_and_what_it_missed(self):
        """The two facts are independent: the scan can confirm 6 unreachable
        AND run out of its call budget before asking about the rest
        (BillCommon.cs: reachabilityChecked = reachAnswered == reachAsked).
        An `elif` printed the 6 as if it were the whole answer -- the exact
        "0 unreachable means not known, not all reachable" failure the field
        exists to prevent."""
        ing = corpse_ingredient(excludedUnreachable=6, reachabilityChecked=False)
        line = bills._ing_line(ing)
        self.assertIn("{6 unreachable, not counted}", line)
        self.assertIn("NOT checked", line)

    def test_the_listing_header_prints_the_tick_the_counts_were_taken_at(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            bills.show({"benches": [], "benchesOnMap": 0, "ingredientsScanned": 12,
                        "countedAtTick": 987654, "attention": {}})
        self.assertIn("at tick 987654", out.getvalue())


class ScrollTests(unittest.TestCase):
    def test_a_scrolled_tab_is_reported(self):
        r = {"success": True, "applied": True, "benches": [bench_row()],
             "write": {"action": "add", "before": {}, "after": {},
                       "changed": ["billCount 1 -> 2"], "requestedOptions": {},
                       "optionsNotApplied": []},
             "watch": {"shown": True, "inspectTab": "ITab_Bills",
                       "closesAfterSeconds": 8, "scrolledToNewBill": True}}
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            bills.print_write(r)
        self.assertIn("scrolled to the new bill", out.getvalue())

    def test_a_tab_that_would_not_scroll_says_where_the_bill_is(self):
        r = {"success": True, "applied": True, "benches": [bench_row()],
             "write": {"action": "add", "before": {}, "after": {},
                       "changed": ["billCount 1 -> 2"], "requestedOptions": {},
                       "optionsNotApplied": []},
             "watch": {"shown": True, "inspectTab": "ITab_Bills",
                       "closesAfterSeconds": 8, "scrolledToNewBill": False,
                       "scrollNote": "this bench has no ITab_Bills"}}
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            bills.print_write(r)
        text = out.getvalue()
        self.assertIn("NOT scrolled", text)
        self.assertIn("bottom of the stack", text)


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


def queued(index=0, label="Make stone blocks", recipe="StonecuttingSandstone", **kw):
    row = {"index": index, "label": label, "recipe": recipe, "repeatInfo": "1x",
           "suspended": False, "paused": False}
    row.update(kw)
    return row


class VerblessWriteTests(unittest.TestCase):
    """2026-09-08, Threadneedle: `bills.py <bench> --forever --do` printed the
    bench's bill sheet and WROTE NOTHING, silently -- and a sheet reads as an
    answer. An option is an intention to change something; it ends in a write or
    in a refusal, never in a listing."""

    def run_cli(self, argv, benches):
        sent = []

        def fake_call(args):
            sent.append(dict(args))
            if args.get("action") == "list":
                rows = benches
                if args.get("bench"):
                    rows = [b for b in benches
                            if args["bench"] in (b.get("thingId"), b.get("label"))]
                    if not rows:
                        return {"action": "list", "success": False, "benches": [],
                                "error": "no bill giver matches %r" % args["bench"]}
                return {"action": "list", "success": True, "benches": rows}
            return {"action": args["action"], "success": True,
                    "benches": [benches[0]], "applied": not args.get("dryRun"),
                    "write": {"action": args["action"], "before": {}, "after": {},
                              "changed": ["repeatMode RepeatCount -> Forever"],
                              "requestedOptions": {}, "optionsNotApplied": []},
                    "watch": {"shown": False, "reason": "test"}}

        with mock.patch.object(bills, "call", fake_call), \
                mock.patch.object(bills.rim, "init", lambda *a, **k: None), \
                mock.patch.object(sys, "argv", ["bills.py"] + argv), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = bills.main()
        return code, [a for a in sent if a.get("action") != "list"], out.getvalue()

    def stonecutter(self, *rows, **kw):
        return bench_row(thingId="TableStonecutter1", defName="TableStonecutter",
                         label="stonecutter's table",
                         position={"x": 138, "z": 127}, bills=list(rows), **kw)

    # --- the bench form -------------------------------------------------

    def test_one_bill_and_a_mode_flag_is_a_set_on_that_bill(self):
        code, sent, text = self.run_cli(
            ["TableStonecutter1", "--forever", "--do"],
            [self.stonecutter(queued())])
        self.assertEqual(0, code)
        self.assertEqual(1, len(sent))
        self.assertEqual("set", sent[0]["action"])
        self.assertEqual(0, sent[0]["index"])
        self.assertEqual("Forever", sent[0]["repeatMode"])
        self.assertFalse(sent[0]["dryRun"])
        self.assertIn("resolved:", text)
        self.assertIn("has one bill", text)
        self.assertNotIn("BENCHES:", text)

    def test_without_do_it_is_still_a_dry_run_of_that_set(self):
        code, sent, text = self.run_cli(
            ["TableStonecutter1", "--forever"], [self.stonecutter(queued())])
        self.assertEqual(0, code)
        self.assertTrue(sent[0]["dryRun"])
        self.assertIn("DRY RUN", text)

    def test_several_bills_refuse_with_the_numbered_list_and_the_line_to_type(self):
        code, sent, text = self.run_cli(
            ["TableStonecutter1", "--forever", "--do"],
            [self.stonecutter(queued(), queued(1, "Make granite blocks"))])
        self.assertEqual(1, code)
        self.assertEqual([], sent)
        self.assertIn("WROTE NOTHING", text)
        self.assertIn("[0] Make stone blocks", text)
        self.assertIn("[1] Make granite blocks", text)
        self.assertIn("python bills.py set TableStonecutter1 1 --forever --do", text)
        self.assertNotIn("BENCHES:", text)

    def test_a_bench_with_no_bills_says_so_and_names_add(self):
        code, sent, text = self.run_cli(
            ["TableStonecutter1", "--forever", "--do"], [self.stonecutter()])
        self.assertEqual(1, code)
        self.assertEqual([], sent)
        self.assertIn("WROTE NOTHING", text)
        self.assertIn("no bills at all", text)
        self.assertIn("python bills.py add TableStonecutter1 ", text)

    def test_an_unreadable_bill_stack_is_not_reported_as_no_bills(self):
        code, _, text = self.run_cli(
            ["TableStonecutter1", "--suspend", "--do"],
            [self.stonecutter(billStackUnreadable=True)])
        self.assertEqual(1, code)
        self.assertIn("could not be READ", text)
        self.assertNotIn("no bills at all", text)

    # --- the recipe form ------------------------------------------------

    def test_a_recipe_resolves_to_the_one_bench_queueing_it(self):
        code, sent, text = self.run_cli(
            ["steel stonecutter", "--forever", "--do"],
            [self.stonecutter(queued(label="Make steel blocks")),
             bench_row(thingId="ElectricStove123",
                       bills=[queued(label="Simple meal")])])
        self.assertEqual(0, code)
        self.assertEqual("TableStonecutter1", sent[0]["bench"])
        self.assertEqual(0, sent[0]["index"])
        self.assertEqual("Forever", sent[0]["repeatMode"])
        self.assertIn("is not a bench; it is a bill on stonecutter's table", text)

    def test_a_recipe_on_two_benches_refuses_naming_both(self):
        code, sent, text = self.run_cli(
            ["blocks", "--forever", "--do"],
            [self.stonecutter(queued(label="Make stone blocks")),
             bench_row(thingId="TableStonecutter2", label="stonecutter's table",
                       bills=[queued(label="Make granite blocks")])])
        self.assertEqual(1, code)
        self.assertEqual([], sent)
        self.assertIn("WROTE NOTHING", text)
        self.assertIn("TableStonecutter1", text)
        self.assertIn("TableStonecutter2", text)
        self.assertNotIn("BENCHES:", text)

    def test_a_word_that_is_neither_writes_nothing_and_prints_no_sheet(self):
        code, sent, text = self.run_cli(
            ["bolt-action rifle", "--forever", "--do"],
            [self.stonecutter(queued())])
        self.assertEqual(1, code)
        self.assertEqual([], sent)
        self.assertIn("WROTE NOTHING", text)
        self.assertIn("neither a bench nor a bill", text)
        self.assertNotIn("BENCHES:", text)

    # --- nothing to write at all ----------------------------------------

    def test_an_option_with_no_bench_refuses_before_any_call(self):
        code, sent, text = self.run_cli(["--forever", "--do"],
                                        [self.stonecutter(queued())])
        self.assertEqual(1, code)
        self.assertEqual([], sent)
        self.assertIn("WROTE NOTHING", text)
        self.assertIn("no bench", text)

    def test_a_bare_do_on_a_bench_is_not_a_listing(self):
        code, sent, text = self.run_cli(["TableStonecutter1", "--do"],
                                        [self.stonecutter(queued())])
        self.assertEqual(1, code)
        self.assertEqual([], sent)
        self.assertIn("WROTE NOTHING", text)
        self.assertIn("nothing to write", text)
        self.assertNotIn("BENCHES:", text)

    def test_a_plain_bench_with_no_option_is_still_the_listing(self):
        code, sent, text = self.run_cli(["TableStonecutter1"],
                                        [self.stonecutter(queued())])
        self.assertEqual(0, code)
        self.assertEqual([], sent)
        self.assertIn("BENCHES:", text)


class SuggestedLineTests(unittest.TestCase):
    """The refusal has to be paste-ready, or it is just a complaint."""

    def test_the_line_echoes_every_flag_the_caller_typed(self):
        self.assertEqual(
            "python bills.py set stove 2 --target 30 --pause on --do",
            bills._set_line("stove", 2,
                            ["stove", "--target", "30", "--pause", "on", "--do"]))

    def test_a_bench_with_a_space_is_quoted(self):
        self.assertIn('set "butcher spot" 0',
                      bills._set_line("butcher spot", 0, ["--forever"]))

    def test_a_flag_value_is_never_mistaken_for_a_positional(self):
        self.assertEqual(["--target", "30", "--do"],
                         bills._flag_words(["stove", "--target", "30", "--do"]))

    def test_every_word_must_appear_but_order_does_not_matter(self):
        self.assertTrue(bills._words_match(
            "steel stonecutter", "Make steel blocks", "StonecuttingSteel",
            "stonecutter's table"))
        self.assertFalse(bills._words_match(
            "steel stove", "Make steel blocks", "", "stonecutter's table"))
        self.assertFalse(bills._words_match("  ", "Make steel blocks"))


if __name__ == "__main__":
    unittest.main()
