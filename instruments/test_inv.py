"""Mock-only regressions for inventory CLI argument and ID display."""
import io
import sys
import unittest
from unittest import mock

import inv


def answer():
    return {"success": True, "things": [], "skipped": {}, "filters": {},
            "defCount": 0, "forbiddenTotal": 0, "thingsScanned": 0,
            "spawnedScanned": 0, "heldScanned": 0}


class InventoryCliTests(unittest.TestCase):
    def run_cli(self, argv):
        with mock.patch.object(inv.rim, "init"), \
             mock.patch.object(inv, "census", return_value=answer()) as census, \
             mock.patch.object(inv, "show"), \
             mock.patch.object(sys, "argv", ["inv.py"] + argv), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = inv.main()
        return rc, out.getvalue(), census

    def test_near_accepts_space_form_and_does_not_become_match(self):
        rc, _, census = self.run_cli(["--near", "120", "140", "30"])
        self.assertEqual(0, rc)
        self.assertEqual({"x": 120, "z": 140, "radius": 30},
                         census.call_args.kwargs)

    def test_near_accepts_comma_form(self):
        rc, _, census = self.run_cli(["--near", "120,140,30"])
        self.assertEqual(0, rc)
        self.assertEqual({"x": 120, "z": 140, "radius": 30},
                         census.call_args.kwargs)

    def test_near_two_coordinates_has_documented_default_radius(self):
        rc, _, census = self.run_cli(["--near", "120,140"])
        self.assertEqual(0, rc)
        self.assertEqual(12, census.call_args.kwargs["radius"])

    def test_rows_print_instance_thing_ids(self):
        row = {"label": "sandstone chunk", "defName": "ChunkSandstone",
               "positions": [{"x": 1, "z": 2,
                               "thingId": "Thing_ChunkSandstone99"}],
               "positionsNotListed": 0, "ours": 1, "total": 1,
               "oursStacks": 1, "stacks": 1}
        payload = answer()
        payload.update(things=[row], filters={"ownership": "ours",
                                              "radius": 0, "category": "haulable",
                                              "includeHeld": True, "match": None},
                       oursTotal=1, mapTotal=1)
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            inv.show(payload)
        self.assertIn("1,2[Thing_ChunkSandstone99]", out.getvalue())

    def test_food_uses_the_bridges_semantic_category(self):
        _, _, census = self.run_cli(["food"])
        self.assertNotIn("match", census.call_args.kwargs)
        self.assertEqual("food", census.call_args.kwargs["category"])

    def test_bridge_food_category_uses_rimworlds_nutrition_predicate(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / "companion" / "src" /
                  "ListThingsTool.cs").read_text(encoding="utf-8")
        self.assertIn("t.def.IsNutritionGivingIngestible", source)
        self.assertIn('cat == "food"', source)

    def test_old_dll_cannot_return_every_haulable_as_food(self):
        payload = answer()
        payload["filters"] = {"category": "haulable"}
        with mock.patch.object(inv.rim, "init"), \
             mock.patch.object(inv, "census", return_value=payload), \
             mock.patch.object(sys, "argv", ["inv.py", "food"]), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(1, inv.main())
        self.assertIn("does not support category='food'", out.getvalue())

    def test_fire_widens_the_bridge_category(self):
        # call_args_list[0]: a zero-kind reply now retries and then probes for
        # near labels, so the LAST call is no longer the asked-for one.
        _, _, census = self.run_cli(["fire"])
        first = census.call_args_list[0].kwargs
        self.assertEqual("all", first["category"])
        self.assertEqual("fire", first["match"])

    # ------------------------------------------------ plural/singular match ---

    def test_variants_try_the_singular_of_a_plural(self):
        self.assertEqual(["components", "component"],
                         inv._variants("components"))
        self.assertEqual(["component", "components"],
                         inv._variants("component"))

    def test_plural_miss_is_retried_as_the_singular(self):
        hit = answer()
        hit.update(defCount=1, things=[])
        replies = [answer(), hit]
        with mock.patch.object(inv.rim, "init"), \
             mock.patch.object(inv, "census",
                               side_effect=lambda **kw: replies.pop(0)) as census, \
             mock.patch.object(inv, "show"), \
             mock.patch.object(sys, "argv", ["inv.py", "components"]), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(0, inv.main())
        self.assertEqual("components", census.call_args_list[0].kwargs["match"])
        self.assertEqual("component", census.call_args_list[1].kwargs["match"])
        self.assertIn("retried as 'component'", out.getvalue())

    def test_a_real_miss_prints_the_nearest_labels(self):
        pool = answer()
        pool.update(defCount=2,
                    things=[{"label": "component", "defName": "ComponentIndustrial"},
                            {"label": "steel", "defName": "Steel"}])
        # miss, singular retry, the whole-map probe (empty), then the label pool.
        replies = [answer(), answer(), answer(), pool]
        with mock.patch.object(inv.rim, "init"), \
             mock.patch.object(inv, "census",
                               side_effect=lambda **kw: replies.pop(0)), \
             mock.patch.object(inv, "show"), \
             mock.patch.object(sys, "argv", ["inv.py", "componnts"]), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(0, inv.main())
        text = out.getvalue()
        self.assertIn("no kind matched", text)
        self.assertIn("component", text)

    def test_common_words_are_substrings_of_real_labels(self):
        # The words a starving colony actually types. Each must be a substring
        # of a vanilla defName or label, since that is all `match` does.
        for word, label in (("meal", "MealSimple"), ("medicine", "MedicineHerbal"),
                            ("steel", "Steel"), ("wood", "WoodLog"),
                            ("component", "ComponentIndustrial")):
            self.assertIn(word, label.lower())
        self.assertIn("component", inv._variants("components"))

    # ------------------------------------------------------- ids and corpses ---

    def test_a_held_position_is_marked_as_unreachable_by_id(self):
        row = {"label": "pemmican", "defName": "Pemmican", "corpse": False,
               "positions": [{"x": 5, "z": 6, "thingId": "Thing_Pemmican407476",
                              "spawned": False}],
               "positionsNotListed": 0}
        self.assertIn("{held: no id reaches it}", inv._pos_bits(row))

    def test_a_spawned_position_keeps_the_plain_id_form(self):
        row = {"label": "pemmican", "defName": "Pemmican", "corpse": False,
               "positions": [{"x": 5, "z": 6, "thingId": "Thing_Pemmican407476",
                              "spawned": True}],
               "positionsNotListed": 0}
        self.assertEqual("5,6[Thing_Pemmican407476]", inv._pos_bits(row))

    def test_footer_warns_that_stack_ids_die_with_their_stacks(self):
        payload = answer()
        payload.update(things=[{"label": "steel", "defName": "Steel",
                                "positions": [{"x": 1, "z": 2}],
                                "positionsNotListed": 0, "ours": 1, "total": 1,
                                "oursStacks": 1, "stacks": 1}],
                       filters={"ownership": "ours", "radius": 0,
                                "category": "haulable", "includeHeld": True,
                                "match": None},
                       oursTotal=1, mapTotal=1)
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            inv.show(payload)
        text = out.getvalue()
        self.assertIn("DefName@x,z", text)
        self.assertIn("ABSORBS", text)

    def test_corpse_positions_say_which_body_is_which(self):
        row = {"label": "human corpse", "defName": "Corpse_Human", "corpse": True,
               "positions": [{"x": 10, "z": 10, "thingId": "Thing_Corpse_Human1"},
                             {"x": 20, "z": 20, "thingId": "Thing_Corpse_Human2"}],
               "positionsNotListed": 0,
               "corpses": [{"position": {"x": 10, "z": 10}, "rotStage": "fresh",
                            "skeleton": False},
                           {"position": {"x": 20, "z": 20},
                            "rotStage": "dessicated", "skeleton": True}]}
        bits = inv._pos_bits(row)
        self.assertIn("10,10[Thing_Corpse_Human1]{FRESH}", bits)
        self.assertIn("20,20[Thing_Corpse_Human2]{SKELETON}", bits)

    def test_a_corpse_cell_with_no_detail_says_so_rather_than_guessing(self):
        row = {"label": "human corpse", "defName": "Corpse_Human", "corpse": True,
               "positions": [{"x": 10, "z": 10}], "positionsNotListed": 0,
               "corpses": []}
        self.assertIn("{stage not listed}", inv._pos_bits(row))

    def test_two_bodies_on_one_cell_are_counted_not_collapsed(self):
        row = {"label": "human corpse", "defName": "Corpse_Human", "corpse": True,
               "positions": [{"x": 3, "z": 4}], "positionsNotListed": 0,
               "corpses": [{"position": {"x": 3, "z": 4}, "skeleton": True},
                           {"position": {"x": 3, "z": 4}, "skeleton": True}]}
        self.assertIn("{2 SKELETON}", inv._pos_bits(row))

    def test_corpse_stages_reach_the_plain_food_listing(self):
        payload = answer()
        payload.update(
            things=[{"label": "human corpse", "defName": "Corpse_Human",
                     "corpse": True, "ours": 2, "total": 2, "oursStacks": 2,
                     "stacks": 2, "positionsNotListed": 0,
                     "positions": [{"x": 10, "z": 10}, {"x": 20, "z": 20}],
                     "rotStages": {"fresh": 1, "dessicated": 1},
                     "corpses": [{"position": {"x": 10, "z": 10},
                                  "rotStage": "fresh", "skeleton": False},
                                 {"position": {"x": 20, "z": 20},
                                  "rotStage": "dessicated", "skeleton": True}]}],
            filters={"ownership": "ours", "radius": 0, "category": "food",
                     "includeHeld": True, "match": None},
            oursTotal=2, mapTotal=2, corpseTotal=2, corpseSkeletonTotal=1)
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            inv.show(payload)
        text = out.getvalue()
        self.assertIn("10,10{FRESH}", text)
        self.assertIn("20,20{SKELETON}", text)
def food_rows():
    """The real 2026-09-07 larder, read live off the colony that turn."""
    rows = [
        {"defName": "RawPotatoes", "label": "Potatoes", "food": True,
         "ours": 98, "oursUnforbidden": 98},
        {"defName": "RawBerries", "label": "Berries", "food": True,
         "ours": 85, "oursUnforbidden": 85},
        {"defName": "Meat_Horse", "label": "Horse meat", "food": True,
         "ours": 76, "oursUnforbidden": 76},
        {"defName": "MealSimple", "label": "Simple meal", "food": True,
         "ours": 20, "oursUnforbidden": 20},
        {"defName": "MealFine_Meat", "label": "Carnivore fine meal",
         "food": True, "ours": 6, "oursUnforbidden": 6},
        {"defName": "Pemmican", "label": "Pemmican", "food": True,
         "ours": 4, "oursUnforbidden": 4},
        {"defName": "Corpse_Human", "label": "Human corpse", "food": True,
         "corpse": True, "ours": 7, "oursUnforbidden": 6},
        {"defName": "Meat_Human", "label": "Human meat", "food": True,
         "ours": 10, "oursUnforbidden": 10},
        {"defName": "Hay", "label": "Hay", "food": True,
         "ours": 300, "oursUnforbidden": 300},
        {"defName": "Steel", "label": "Steel", "food": False,
         "ours": 400, "oursUnforbidden": 400},
    ]
    for r in rows:
        r.setdefault("positions", [])
        r.setdefault("positionsNotListed", 0)
    return rows


class FoodNutritionTests(unittest.TestCase):
    """THE FACTOR OF TWENTY. Seven turns of the 2026-09-06 stream read the
    unit column as food security: 76 horse meat is 3.8 nutrition, not 76."""

    def summary(self):
        return inv.food_summary(food_rows())

    def test_raw_meat_is_five_hundredths_of_a_nutrition_per_unit(self):
        self.assertEqual(0.05, inv.nutrition_per_unit("Meat_Horse"))
        self.assertEqual(0.05, inv.nutrition_per_unit("RawPotatoes"))
        self.assertEqual(0.9, inv.nutrition_per_unit("MealSimple"))

    def test_meals_and_raw_are_counted_in_separate_tiers(self):
        t = self.summary()["tiers"]
        # 20 simple (0.9) + 6 fine (0.9) = 23.4
        self.assertAlmostEqual(23.4, t["ready"]["nutrition"], places=3)
        # 98 + 85 potatoes/berries + 76 meat + 4 pemmican, all 0.05
        self.assertAlmostEqual(13.15, t["raw"]["nutrition"], places=3)

    def test_hay_is_animal_feed_and_never_reaches_the_colonist_total(self):
        s = self.summary()
        self.assertAlmostEqual(15.0, s["tiers"]["animal"]["nutrition"], places=3)
        self.assertNotIn("Hay", str(s["tiers"]["raw"]["rows"]))
        self.assertAlmostEqual(36.55, s["edible"], places=3)

    def test_a_corpse_is_food_to_the_game_and_not_to_this_summary(self):
        s = self.summary()
        self.assertEqual(0.0, s["tiers"]["corpse"]["nutrition"])
        self.assertEqual(1, len(s["tiers"]["corpse"]["rows"]))
        self.assertNotIn("corpse", str(s["tiers"]["raw"]["rows"]).lower())

    def test_human_meat_is_counted_apart_and_left_out_of_the_total(self):
        s = self.summary()
        self.assertAlmostEqual(0.5, s["tiers"]["taboo"]["nutrition"], places=3)
        self.assertAlmostEqual(36.55, s["edible"], places=3)
        self.assertAlmostEqual(37.05, s["withTaboo"], places=3)

    def test_a_def_the_table_does_not_know_is_named_not_valued_at_zero(self):
        s = inv.food_summary([{"defName": "Chocolate", "label": "Chocolate",
                               "food": True, "oursUnforbidden": 3}])
        self.assertEqual(1, len(s["tiers"]["unknown"]["rows"]))
        self.assertEqual(0.0, s["edible"])
        self.assertIsNone(inv.nutrition_per_unit("Chocolate"))

    def test_non_food_rows_are_ignored_entirely(self):
        self.assertNotIn("Steel", str(self.summary()["tiers"]))

    def test_forbidden_food_is_out_by_default_and_countable_on_request(self):
        rows = [{"defName": "MealSimple", "label": "Simple meal", "food": True,
                 "ours": 10, "oursUnforbidden": 2}]
        self.assertAlmostEqual(1.8, inv.food_summary(rows)["edible"], places=3)
        self.assertAlmostEqual(
            9.0, inv.food_summary(rows, key="ours")["edible"], places=3)

    def test_days_of_food_divides_by_colony_size(self):
        text = "\n".join(inv.food_block({"things": food_rows()}, colonists=5))
        self.assertIn("4.6 DAYS for 5 colonist(s)", text)     # 36.55 / (5*1.6)
        self.assertIn("(vanilla table, not read from game)", text)

    def test_a_short_larder_is_loud(self):
        rows = [{"defName": "MealSimple", "label": "Simple meal", "food": True,
                 "oursUnforbidden": 4}]
        text = "\n".join(inv.food_block({"things": rows}, colonists=5))
        self.assertIn("under three days", text)

    def test_an_unread_colony_size_says_so_rather_than_guessing(self):
        text = "\n".join(inv.food_block({"things": food_rows()}, colonists=None))
        self.assertIn("colony size unread", text)
        self.assertNotIn("DAYS for", text)

    def test_the_block_always_states_the_corpse_line_even_at_zero(self):
        text = "\n".join(inv.food_block({"things": []}, colonists=2))
        self.assertIn("not counted:  0 corpse row(s)", text)

    def test_colony_size_returns_none_rather_than_a_made_up_divisor(self):
        with mock.patch.dict("sys.modules", {"status": None}):
            self.assertIsNone(inv.colony_size())


class FoodCliTests(unittest.TestCase):
    """`inv.py food` is a CATEGORY, not the literal substring "food" -- that
    match returned 0 kinds with meals and meat in store."""

    def _run(self, argv, reply):
        with mock.patch.object(inv.rim, "init"), \
             mock.patch.object(inv, "census", return_value=reply) as census, \
             mock.patch.object(inv, "colony_size", return_value=5), \
             mock.patch.object(sys, "argv", ["inv.py"] + argv), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = inv.main()
        return rc, out.getvalue(), census

    def _reply(self):
        r = answer()
        r.update({"things": food_rows(), "defCount": len(food_rows()),
                  "filters": {"category": "food", "ownership": "ours",
                              "includeHeld": True, "match": None,
                              "radius": 0},
                  "oursTotal": 1, "mapTotal": 1})
        return r

    def test_food_asks_for_the_category_and_never_for_the_word(self):
        rc, text, census = self._run(["food"], self._reply())
        self.assertEqual(0, rc)
        self.assertEqual("food", census.call_args.kwargs.get("category"))
        self.assertNotIn("match", census.call_args.kwargs)

    def test_food_lists_the_kinds_and_then_the_nutrition_summary(self):
        _, text, _ = self._run(["food"], self._reply())
        for label in ("Simple meal", "Horse meat", "Potatoes", "Pemmican"):
            self.assertIn(label, text)
        self.assertIn("FOOD -- nutrition, not units", text)
        self.assertIn("DAYS for 5 colonist(s)", text)

    def test_colonists_flag_overrides_the_read_and_is_not_taken_as_a_match(self):
        _, text, census = self._run(["food", "--colonists", "3"], self._reply())
        self.assertNotIn("match", census.call_args.kwargs)
        self.assertIn("for 3 colonist(s)", text)

    def test_colonists_before_the_word_does_not_swallow_the_category(self):
        _, text, census = self._run(["--colonists", "3", "food"], self._reply())
        self.assertEqual("food", census.call_args.kwargs.get("category"))
        self.assertNotIn("match", census.call_args.kwargs)

    def test_a_plain_match_still_gets_no_nutrition_block(self):
        r = self._reply()
        r["filters"] = dict(r["filters"], category="haulable", match="steel")
        _, text, _ = self._run(["steel"], r)
        self.assertNotIn("FOOD -- nutrition", text)


class ScopeNoteTests(unittest.TestCase):
    """2026-09-05: a confident `0 of 0` for fire with a Critical Fire alert up."""

    def render(self, reply, wanted=None):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            inv.scope_notes(reply, wanted)
        return out.getvalue()

    def test_a_zero_names_the_haulable_scope_and_alerts_py(self):
        text = self.render(dict(answer(), filters={"category": "haulable",
                                                   "ownership": "ours"}), "fire")
        self.assertIn("HAULABLE ITEMS only", text)
        self.assertIn("alerts.py", text)

    def test_a_non_zero_haulable_answer_stays_quiet(self):
        text = self.render(dict(answer(), defCount=3,
                                filters={"category": "haulable",
                                         "ownership": "ours"}), "steel")
        self.assertEqual("", text)

    def test_all_owners_says_what_the_census_cannot_see(self):
        text = self.render(dict(answer(), defCount=2,
                                filters={"category": "haulable",
                                         "ownership": "all"}), "pemmican")
        self.assertIn("LEFT the map", text)


class HolderTests(unittest.TestCase):
    def test_every_named_holder_is_printed_and_the_cap_is_said(self):
        row = {"total": 100, "ours": 0,
               "holders": {"trader Ama": 40, "muffalo": 30, "casket": 20,
                           "crate": 5, "(+ others)": 3}}
        text = inv._elsewhere(row)
        for who in ("trader Ama", "muffalo", "casket", "crate"):
            self.assertIn(who, text)
        self.assertIn("3 further holder(s)", text)


class FilterFlagTests(unittest.TestCase):
    """Every documented filter has to reach the bridge. `--food` was parsed by
    nobody and the census answered the whole inventory in a filtered answer's
    shape; the table below is what stops a second one hiding."""

    FILTERS = (
        ("--all", {"category": "all", "ownership": "all"}),
        ("--all-owners", {"ownership": "all"}),
        ("--everyone", {"ownership": "all"}),
        ("--ours", {"ownership": "ours"}),
        ("--buildings", {"category": "buildings"}),
        ("--spawned-only", {"includeHeld": False}),
        ("--forbidden", {"forbiddenOnly": True}),
        ("--corpses", {"corpses": True, "ownership": "all"}),
        ("--chunks-out", {"excludeChunks": True}),
        ("--food", {"category": "food"}),
    )

    def _run(self, argv, reply=None):
        reply = reply if reply is not None else answer()
        with mock.patch.object(inv.rim, "init"),              mock.patch.object(inv, "census", return_value=reply) as census,              mock.patch.object(inv, "show"),              mock.patch.object(inv, "colony_size", return_value=5),              mock.patch.object(sys, "argv", ["inv.py"] + argv),              mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = inv.main()
        return rc, out.getvalue(), census

    def test_every_documented_filter_reaches_the_bridge(self):
        for flag, expected in self.FILTERS:
            reply = answer()
            reply["filters"] = dict(expected)
            rc, text, census = self._run([flag], reply)
            self.assertEqual(0, rc, "%s: %s" % (flag, text))
            sent = census.call_args.kwargs
            for key, value in expected.items():
                self.assertEqual(value, sent.get(key),
                                 "%s did not send %s" % (flag, key))

    def test_food_flag_is_the_food_word(self):
        reply = answer()
        reply["filters"] = {"category": "food"}
        for argv in (["--food"], ["food"]):
            _, _, census = self._run(argv, reply)
            self.assertEqual("food", census.call_args.kwargs.get("category"), argv)
            self.assertNotIn("match", census.call_args.kwargs)

    def test_a_dll_that_ignores_the_food_flag_is_refused_not_printed(self):
        # The failure this whole item is about: a filter nobody applied, and a
        # whole-inventory answer in a filtered one's shape.
        reply = answer()
        reply["filters"] = {"category": "haulable"}
        rc, text, _ = self._run(["--food"], reply)
        self.assertEqual(1, rc)
        self.assertIn("does not support category='food'", text)

    def test_food_still_takes_a_word_to_narrow_by(self):
        # A zero-kind reply probes for near labels afterwards, so the asked-for
        # call is the FIRST one, not the last.
        reply = answer()
        reply["filters"] = {"category": "food"}
        for argv in (["food", "meat"], ["--food", "meat"]):
            _, _, census = self._run(argv, reply)
            first = census.call_args_list[0].kwargs
            self.assertEqual("food", first.get("category"), argv)
            self.assertEqual("meat", first.get("match"), argv)

    def test_match_flag_is_the_bare_word(self):
        _, _, census = self._run(["--match", "fire"])
        self.assertEqual("fire", census.call_args_list[0].kwargs.get("match"))


class UnknownFlagTests(unittest.TestCase):
    def _run(self, argv):
        with mock.patch.object(inv.rim, "init") as init,              mock.patch.object(inv, "census", return_value=answer()) as census,              mock.patch.object(sys, "argv", ["inv.py"] + argv),              mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = inv.main()
        return rc, out.getvalue(), init, census

    def test_a_flag_this_tool_has_not_got_stops_the_call(self):
        rc, text, init, census = self._run(["berry", "--radius", "40"])
        self.assertEqual(1, rc)
        self.assertIn("no such flag --radius", text)
        self.assertIn("--near", text)
        init.assert_not_called()
        census.assert_not_called()

    def test_a_typo_names_the_nearest_real_flag(self):
        rc, text, _, _ = self._run(["--corpse"])
        self.assertEqual(1, rc)
        self.assertIn("--corpses", text)

    def test_every_documented_flag_is_accepted(self):
        for flag in inv.FLAGS:
            self.assertEqual([], inv.unknown_flags([flag]), flag)

    def test_numbers_are_values_not_flags(self):
        self.assertEqual([], inv.unknown_flags(["--cells", "100", "120", "-4"]))


class WiderScopeTests(unittest.TestCase):
    """A zero in the HAULABLE census is usually the category, not the map: a
    built sculpture is a building and a berry bush is a plant."""

    def _miss(self, wide_rows, building_defs=()):
        wide = answer()
        wide.update(defCount=len(wide_rows), things=wide_rows)
        builds = answer()
        builds.update(defCount=len(building_defs),
                      things=[{"defName": d, "label": d} for d in building_defs])
        replies = [answer(), answer(), wide, builds]
        with mock.patch.object(inv.rim, "init"),              mock.patch.object(inv, "census",
                               side_effect=lambda **kw: replies.pop(0)),              mock.patch.object(inv, "show"),              mock.patch.object(sys, "argv", ["inv.py", self.word]),              mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(0, inv.main())
        return out.getvalue()

    word = "berry"

    def test_plants_that_match_are_counted_and_named(self):
        text = self._miss([{"defName": "Plant_Berry", "label": "berry bush",
                            "total": 98, "ours": 0}])
        self.assertIn("0 HAULABLE ITEMS matches", text)
        self.assertIn("98 plants", text)
        self.assertIn("berry bush", text)
        self.assertIn("add --all", text)

    def test_nothing_close_is_not_printed_when_the_thing_is_right_there(self):
        text = self._miss([{"defName": "Plant_Berry", "label": "berry bush",
                            "total": 98, "ours": 0}])
        self.assertNotIn("nothing close", text)
        self.assertNotIn("nearest:", text)

    def test_a_built_sculpture_is_named_as_a_building(self):
        self.word = "sculpture"
        text = self._miss([{"defName": "SculptureSmall",
                            "label": "Sandstone small sculpture",
                            "total": 8, "ours": 8}],
                          building_defs=("SculptureSmall",))
        self.assertIn("8 buildings", text)
        self.assertIn("Sandstone small sculpture", text)
        self.assertIn("add --all", text)

    def test_a_word_nothing_on_the_map_has_still_gets_the_nearest_labels(self):
        text = self._miss([])
        self.assertIn("no kind matched", text)
        self.assertIn("nearest:", text)

    def test_a_plural_of_a_y_word_is_retried_as_its_singular(self):
        self.assertIn("berry", inv._variants("berries"))
        self.assertIn("bush", inv._variants("bushes"))
        self.assertIn("berries", inv._variants("berry"))


if __name__ == "__main__":
    unittest.main()
