"""Offline regressions for act.py argument and animal resolution contracts."""
import io
import unittest
from unittest import mock

import act


def animal(x=10, z=20, tid="Ibex404123", name="ibex"):
    return {"name": name, "defName": "Ibex", "kindDef": "Ibex",
            "position": {"x": x, "z": z},
            "animals": {"thingId": tid}}


def row(label, ident, category="Floors", **kw):
    """One `list_architect_designators` row, with the fields _pick reads."""
    d = {"label": label, "id": ident, "kind": kw.pop("kind", "designator"),
         "supportsCellApplication": kw.pop("supportsCellApplication", True),
         "className": kw.pop("className", "RimWorld.Designator_Build")}
    d.update(kw)
    return (category, d)


def menu(*rows):
    """Install a fake architect menu without touching the bridge."""
    act._rows[:] = list(rows)
    act._index()


class HuntTests(unittest.TestCase):
    def test_printed_short_and_full_animal_ids_both_resolve(self):
        rows = [animal()]
        self.assertEqual(rows, act._animal_matches("Ibex404123", rows))
        self.assertEqual(rows, act._animal_matches("Thing_Ibex404123", rows))

    def test_thing_id_resolves_from_settings_or_the_top_level_too(self):
        for pawn in ({"settings": {"thingId": "WildBoar334597"}},
                     {"thingId": "Thing_WildBoar334597"}):
            self.assertEqual([pawn], act._animal_matches("WildBoar334597", [pawn]))
            self.assertEqual([pawn], act._animal_matches("Thing_WildBoar334597", [pawn]))

    def test_hunt_asks_for_the_animals_block_that_carries_the_id(self):
        with mock.patch("pawns.all_pawns", return_value=[animal()]) as all_pawns, \
             mock.patch.object(act, "apply", return_value={"success": True}):
            act.hunt("Ibex404123", do=True)
        self.assertEqual({"animalsOnly": True, "animals": True},
                         all_pawns.call_args.kwargs)

    def test_disambiguation_names_the_real_thing_id(self):
        rows = [animal(49, 96, "Ibex404123"), animal(50, 96, "Ibex404124")]
        with mock.patch("pawns.all_pawns", return_value=rows):
            with self.assertRaises(KeyError) as caught:
                act.hunt("Ibex")
        message = str(caught.exception.args[0])
        self.assertIn("Ibex404123", message)
        self.assertNotIn("None", message)

    def test_hunt_refreshes_the_cell_immediately_before_apply(self):
        reads = iter(([animal(10, 20)], [animal(12, 23)]))
        with mock.patch("pawns.all_pawns", side_effect=lambda **kw: next(reads)), \
             mock.patch.object(act, "apply", return_value={"success": True}) as apply:
            act.hunt("Ibex404123", do=True)
        self.assertEqual(("Hunt", 12, 23), apply.call_args.args[:3])

    def test_hunt_retries_the_stale_cell_refusal(self):
        reads = iter(([animal(10, 20)], [animal(11, 21)], [animal(12, 22)]))
        stale = {"success": False, "message": "Must designate huntable animals",
                 "rejectedCells": [{"x": 11, "z": 21,
                                     "reason": "Must designate huntable animals"}]}
        with mock.patch("pawns.all_pawns", side_effect=lambda **kw: next(reads)), \
             mock.patch.object(act, "apply", side_effect=[stale, {"success": True}]) as apply:
            result = act.hunt("Ibex404123", do=True)
        self.assertTrue(result[0][1]["success"])
        self.assertEqual(("Hunt", 12, 22), apply.call_args.args[:3])

    def test_an_already_marked_animal_is_a_no_op_not_a_refusal(self):
        marked = animal()
        marked["animals"]["designations"] = {"hunt": True, "tame": False}
        out = io.StringIO()
        with mock.patch.object(act.rim, "init"), \
             mock.patch("pawns.all_pawns", return_value=[marked]), \
             mock.patch.object(act, "apply") as apply, \
             mock.patch("sys.stdout", out):
            self.assertEqual(0, act.main(["hunt", "Ibex404123", "--do"]))
        apply.assert_not_called()
        self.assertIn("NO-OP", out.getvalue())

    def test_an_unmarked_animal_still_reaches_the_designator(self):
        unmarked = animal()
        unmarked["animals"]["designations"] = {"hunt": False}
        with mock.patch("pawns.all_pawns", return_value=[unmarked]), \
             mock.patch.object(act, "apply", return_value={"success": True}) as apply:
            act.hunt("Ibex404123", do=True)
        self.assertEqual(("Hunt", 10, 20), apply.call_args.args[:3])

    def test_tame_uses_the_tame_label_and_names_the_cell_mates(self):
        fox = animal(49, 96, "Fox112", "fox")
        donkey = animal(49, 96, "Donkey405307", "donkey")
        with mock.patch("pawns.all_pawns", return_value=[fox, donkey]), \
             mock.patch.object(act, "apply", return_value={"success": True}) as apply:
            out = act.tame("Fox112", do=True)
        self.assertEqual("Tame", apply.call_args.args[0])
        self.assertEqual([donkey], out[0][2])

    def test_tame_cli_prints_the_target_id_and_the_other_animals(self):
        fox = animal(49, 96, "Fox112", "fox")
        donkey = animal(49, 96, "Donkey405307", "donkey")
        out = io.StringIO()
        with mock.patch.object(act.rim, "init"), \
             mock.patch("pawns.all_pawns", return_value=[fox, donkey]), \
             mock.patch.object(act, "apply", return_value={
                 "success": False, "message": "Cannot tame donkey: Not enough food"}), \
             mock.patch("sys.stdout", out):
            act.main(["tame", "Fox112"])
        self.assertIn("(Fox112)", out.getvalue())
        self.assertIn("Donkey405307", out.getvalue())


class DesignatorResolutionTests(unittest.TestCase):
    def tearDown(self):
        act._rows[:] = []
        act._index()

    def test_a_dropdown_never_wins_over_its_own_active_child(self):
        menu(row("sandstone tile", "drop:floors:sandstone", kind="dropdown",
                 supportsCellApplication=False,
                 className="RimWorld.Designator_Dropdown"),
             row("sandstone tile", "floors:sandstone"))
        self.assertEqual("floors:sandstone", act.designator("sandstone tile"))
        self.assertEqual([], [n for _, n in act.labels() if n])

    def test_the_pair_form_also_skips_the_dropdown(self):
        menu(row("slate tile", "drop:floors:slate", kind="dropdown",
                 supportsCellApplication=False,
                 className="RimWorld.Designator_Dropdown"),
             row("slate tile", "floors:slate"))
        self.assertEqual("floors:slate", act.designator(("Floors", "slate tile")))

    def test_one_designator_per_category_resolves_to_the_preferred_one(self):
        same = {"className": "RimWorld.Designator_Cancel",
                "designationDefName": None, "buildableDefName": None}
        menu(row("Cancel", "arch:structure:cancel", "Structure", **same),
             row("Cancel", "arch:orders:cancel", "Orders", **same),
             row("Cancel", "arch:floors:cancel", "Floors", **same))
        self.assertEqual("arch:orders:cancel", act.designator("Cancel"))
        self.assertEqual("arch:orders:cancel", act.designator(act.CLEAR_DESIGNATOR))

    def test_deconstruct_prefers_orders_then_structure(self):
        same = {"className": "RimWorld.Designator_Deconstruct",
                "designationDefName": "Deconstruct"}
        menu(row("Deconstruct", "arch:structure:decon", "Structure", **same),
             row("Deconstruct", "arch:furniture:decon", "Furniture", **same))
        self.assertEqual("arch:structure:decon", act.designator("Deconstruct"))

    def test_two_different_tools_sharing_a_label_stay_ambiguous(self):
        menu(row("Wall", "structure:wall", "Structure",
                 buildableDefName="Wall"),
             row("Wall", "ship:wall", "Ship", buildableDefName="ShipWall"))
        with self.assertRaises(KeyError) as caught:
            act.designator("Wall")
        message = str(caught.exception.args[0])
        self.assertIn("Ship", message)
        self.assertIn("--category", message)
        self.assertEqual([" (ambiguous: Ship, Structure -- pass --category)"],
                         [n for _, n in act.labels() if n])

    def test_designation_defname_comes_back_for_the_resolved_id(self):
        menu(row("Hunt", "orders:hunt", "Orders", designationDefName="Hunt",
                 className="RimWorld.Designator_Hunt"))
        self.assertEqual("Hunt", act.designation("Hunt"))


class SizeFlagTests(unittest.TestCase):
    def apply_argv(self, argv):
        with mock.patch.object(act.rim, "init"), \
             mock.patch.object(act, "apply", return_value={"success": True,
                                                           "dryRun": True}) as apply, \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            act.main(["apply", "Mine", "10", "20"] + argv)
        return apply.call_args.args

    def test_positional_sizes_still_work(self):
        self.assertEqual(("Mine", 10, 20, 4, 3), self.apply_argv(["4", "3"]))

    def test_width_and_height_flags_are_accepted(self):
        self.assertEqual(("Mine", 10, 20, 4, 3),
                         self.apply_argv(["--width", "4", "--height", "3"]))

    def test_size_flag_takes_both(self):
        self.assertEqual(("Mine", 10, 20, 4, 3),
                         self.apply_argv(["--size", "4", "3"]))

    def test_one_cell_is_still_the_default(self):
        self.assertEqual(("Mine", 10, 20, 1, 1), self.apply_argv([]))

    def test_two_spellings_at_once_is_refused(self):
        with mock.patch.object(act.rim, "init"), \
             mock.patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaises(SystemExit):
                act.main(["apply", "Mine", "10", "20", "4", "3", "--size", "2", "2"])


class ApplyCliTests(unittest.TestCase):
    def test_apply_cli_is_dry_by_default(self):
        with mock.patch.object(act.rim, "init"), \
             mock.patch.object(act, "apply", return_value={"success": True,
                                                            "dryRun": True}) as apply, \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(0, act.main(["apply", "Harvest", "1", "2"]))
        self.assertTrue(apply.call_args.kwargs["dry"])

    def test_apply_cli_do_opts_into_mutation(self):
        with mock.patch.object(act.rim, "init"), \
             mock.patch.object(act, "apply", return_value={"success": True,
                                                            "dryRun": False}) as apply, \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(0, act.main(["apply", "Harvest", "1", "2", "--do"]))
        self.assertFalse(apply.call_args.kwargs["dry"])

    def test_already_designated_reads_as_a_no_op_not_a_refusal(self):
        refused = {"success": False, "message": "Must designate huntable animals",
                   "rejectedCells": [{"x": 49, "z": 96,
                                      "reason": "Must designate huntable animals"}]}
        grid = [{"x": 49, "z": 96, "designations": [{"defName": "Hunt"}]}]
        out = io.StringIO()
        with mock.patch.object(act.rim, "init"), \
             mock.patch.object(act, "apply", return_value=refused), \
             mock.patch.object(act, "designation", return_value="Hunt"), \
             mock.patch.object(act, "cells", return_value=grid), \
             mock.patch.dict(act._designation, {}, clear=False), \
             mock.patch("sys.stdout", out):
            rc = act.main(["apply", "Hunt", "49", "96", "--do"])
        self.assertEqual(0, rc)
        self.assertIn("NO-OP", out.getvalue())

    def test_a_real_refusal_is_still_a_refusal(self):
        refused = {"success": False, "message": "Must designate huntable animals",
                   "rejectedCells": [{"x": 49, "z": 96, "reason": "no animal"}]}
        with mock.patch.object(act.rim, "init"), \
             mock.patch.object(act, "apply", return_value=refused), \
             mock.patch.object(act, "designation", return_value="Hunt"), \
             mock.patch.object(act, "cells", return_value=[]), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(1, act.main(["apply", "Hunt", "49", "96", "--do"]))

    def test_forbid_on_already_forbidden_things_is_a_no_op(self):
        refused = {"success": False, "message": "Must designate unforbidden items",
                   "rejectedCells": [{"x": 20, "z": 30, "reason": "nothing here"}]}
        grid = [{"x": 20, "z": 30,
                 "things": [{"defName": "Steel", "forbidden": True}]}]
        out = io.StringIO()
        with mock.patch.object(act.rim, "init"), \
             mock.patch.object(act, "apply", return_value=refused), \
             mock.patch.object(act, "cells", return_value=grid), \
             mock.patch("sys.stdout", out):
            rc = act.main(["apply", "Forbid", "20", "30", "--do"])
        self.assertEqual(0, rc)
        self.assertIn("already forbidden", out.getvalue())


class AreaPreviewTests(unittest.TestCase):
    def test_area_apply_names_the_cells_and_flags_a_built_neighbour(self):
        result = {"success": True, "dryRun": True, "acceptedCellCount": 2,
                  "rejectedCellCount": 0,
                  "acceptedCells": [{"x": 90, "z": 120}, {"x": 91, "z": 120}]}
        border = [{"x": 92, "z": 120,
                   "things": [{"className": "Building", "label": "wall"}]}]
        out = io.StringIO()
        with mock.patch.object(act.rim, "init"), \
             mock.patch.object(act, "apply", return_value=result), \
             mock.patch.object(act, "cells", return_value=border), \
             mock.patch("sys.stdout", out):
            act.main(["apply", "Mine", "90", "120", "2", "1"])
        printed = out.getvalue()
        # No --do, so the applied wording must not appear anywhere.
        self.assertIn("DRY RUN - would designate 2 cell(s) (nothing applied;"
                      " add --do): 90,120 91,120", printed)
        self.assertNotIn("newly designated", printed)
        self.assertIn("!! touches built structure at 92,120 (wall)", printed)

    def test_a_real_apply_says_applied_again_under_the_warnings(self):
        """Turn 5: a Cancel sweep printed only `!!` lines and had cancelled 12."""
        result = {"success": True, "dryRun": False,
                  "acceptedCells": [{"x": 90, "z": 120}, {"x": 91, "z": 120}]}
        border = [{"x": 92, "z": 120,
                   "things": [{"className": "Building", "label": "wall"}]}]
        out = io.StringIO()
        with mock.patch.object(act.rim, "init"), \
             mock.patch.object(act, "apply", return_value=result), \
             mock.patch.object(act.pick, "clear_designator"), \
             mock.patch.object(act, "cells", return_value=border), \
             mock.patch("sys.stdout", out):
            act.main(["apply", "Cancel", "90", "120", "2", "1", "--do"])
        printed = out.getvalue()
        # The count fields are absent from this reply on purpose.
        self.assertIn("APPLIED 2 cell(s); 0 rejected", printed)
        self.assertIn("2 cell(s) newly designated", printed)
        self.assertIn("-- APPLIED 2 cell(s) (the 1 !! line(s) above are"
                      " warnings", printed)

    def test_a_reply_without_a_dryrun_field_is_not_read_as_applied(self):
        result = {"success": True, "acceptedCells": [{"x": 90, "z": 120}]}
        out = io.StringIO()
        with mock.patch.object(act.rim, "init"), \
             mock.patch.object(act, "apply", return_value=result), \
             mock.patch("sys.stdout", out):
            act.main(["apply", "Mine", "90", "120"])
        printed = out.getvalue()
        self.assertIn("DRY RUN - would designate 1 cell(s) (nothing applied;"
                      " add --do)", printed)
        self.assertNotIn("APPLIED 1", printed)

    def test_a_reply_that_contradicts_the_call_is_said_out_loud(self):
        result = {"success": True, "dryRun": True,
                  "acceptedCellCount": 1, "rejectedCellCount": 0}
        out = io.StringIO()
        with mock.patch.object(act.rim, "init"), \
             mock.patch.object(act, "apply", return_value=result), \
             mock.patch.object(act.pick, "clear_designator"), \
             mock.patch("sys.stdout", out):
            act.main(["apply", "Mine", "90", "120", "--do"])
        self.assertIn("the bridge reply says dryRun=True but this was called"
                      " as a real apply", out.getvalue())

    def test_natural_rock_is_not_a_built_structure(self):
        self.assertFalse(act._is_built({"className": "Mineable", "label": "sandstone"}))
        self.assertTrue(act._is_built({"className": "Blueprint_Build",
                                       "isBlueprint": True}))

    def test_no_preview_skips_the_extra_read(self):
        result = {"success": True, "dryRun": True, "acceptedCellCount": 2,
                  "acceptedCells": [{"x": 90, "z": 120}]}
        with mock.patch.object(act.rim, "init"), \
             mock.patch.object(act, "apply", return_value=result), \
             mock.patch.object(act, "cells") as cells, \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            act.main(["apply", "Mine", "90", "120", "2", "1", "--no-preview"])
        cells.assert_not_called()


class AllowTests(unittest.TestCase):
    grid = [{"x": 20, "z": 30, "things": [{"defName": "Steel", "label": "steel",
                                           "forbidden": True},
                                          {"defName": "Wood", "forbidden": False}]}]

    def test_dry_run_lists_and_writes_nothing(self):
        with mock.patch.object(act, "cells", return_value=self.grid), \
             mock.patch.object(act.rim, "game") as game:
            report = act.allow(20, 30, 1, 1)
        game.assert_not_called()
        self.assertTrue(report["dryRun"])
        self.assertEqual([(20, 30, ["steel"])], report["found"])

    def test_do_clicks_the_cell_and_fires_the_allow_gizmo(self):
        calls = []

        def game(tool, args=None, **kw):
            calls.append((tool, args))
            if tool == "rimworld/list_selected_gizmos":
                return {"success": True, "gizmos": [
                    {"id": "sel:1:forbid", "label": "Allow", "disabled": False},
                    {"id": "sel:2:haul", "label": "Haul", "disabled": False}]}
            return {"success": True}

        with mock.patch.object(act, "cells", side_effect=[self.grid, []]), \
             mock.patch.object(act.pick, "clear_designator"), \
             mock.patch.object(act.pick, "ensure_camera"), \
             mock.patch.object(act.rim, "game", side_effect=game):
            report = act.allow(20, 30, 1, 1, do=True)
        self.assertEqual(["rimworld/click_cell", "rimworld/get_selection_semantics",
                          "rimworld/list_selected_gizmos",
                          "rimworld/execute_gizmo"], [c[0] for c in calls])
        self.assertEqual({"gizmoId": "sel:1:forbid"}, calls[-1][1])
        self.assertEqual([(20, 30, ["steel"])], report["unforbidden"])
        self.assertEqual([], report["stillForbidden"])

    def test_a_missing_allow_gizmo_is_reported_not_swallowed(self):
        def game(tool, args=None, **kw):
            if tool == "rimworld/list_selected_gizmos":
                return {"success": True, "gizmos": []}
            return {"success": True}

        with mock.patch.object(act, "cells", side_effect=[self.grid, self.grid]), \
             mock.patch.object(act.pick, "clear_designator"), \
             mock.patch.object(act.pick, "ensure_camera"), \
             mock.patch.object(act.rim, "game", side_effect=game):
            report = act.allow(20, 30, 1, 1, do=True)
        self.assertEqual([], report["unforbidden"])
        self.assertEqual(1, len(report["failed"]))

    def test_an_unread_rectangle_raises_instead_of_reporting_nothing(self):
        with mock.patch.object(act, "cells", return_value=None):
            with self.assertRaises(act.rim.BridgeError):
                act.forbidden_cells(20, 30, 1, 1)

class CancelNoopTests(unittest.TestCase):
    """`Cancel` on a finished blueprint refused exactly like a real refusal."""

    refused = {"success": False, "acceptedCellCount": 0,
               "rejectedCellCount": 1,
               "rejectedCells": [{"x": 113, "z": 140,
                                  "reason": "the designator rejected this cell"}]}

    def _main(self, grid):
        out = io.StringIO()
        with mock.patch.object(act.rim, "init"), \
             mock.patch.object(act, "apply", return_value=self.refused), \
             mock.patch.object(act, "designator", return_value="d1"), \
             mock.patch.object(act.pick, "clear_designator"), \
             mock.patch.object(act, "cells", return_value=grid), \
             mock.patch("sys.stdout", out):
            rc = act.main(["apply", "Cancel", "113", "140", "--do"])
        return rc, out.getvalue()

    def test_a_finished_building_says_there_was_nothing_to_cancel(self):
        grid = [{"x": 113, "z": 140, "designations": [],
                 "things": [{"className": "Building", "label": "wall"}]}]
        rc, text = self._main(grid)
        self.assertEqual(0, rc)
        self.assertIn("nothing to cancel at 113,140 (built, no "
                      "blueprint/designation)", text)

    def test_a_standing_blueprint_is_still_a_real_refusal(self):
        grid = [{"x": 113, "z": 140, "designations": [],
                 "things": [{"isBlueprint": True, "label": "wall (blueprint)"}]}]
        rc, text = self._main(grid)
        self.assertEqual(1, rc)
        self.assertNotIn("nothing to cancel", text)

    def test_a_standing_designation_is_still_a_real_refusal(self):
        grid = [{"x": 113, "z": 140, "designations": [{"defName": "Mine"}],
                 "things": []}]
        rc, text = self._main(grid)
        self.assertEqual(1, rc)
        self.assertNotIn("nothing to cancel", text)


class UndesignateTests(unittest.TestCase):
    grid = [{"x": 20, "z": 30, "designations": [{"defName": "Mine"}], "things": []},
            {"x": 21, "z": 30, "designations": [{"defName": "Tame"}], "things": []}]

    def test_dry_run_lists_and_writes_nothing(self):
        with mock.patch.object(act, "cells", return_value=self.grid) as cells, \
             mock.patch.object(act, "apply") as apply_:
            report = act.undesignate(20, 30, 2, 1)
        apply_.assert_not_called()
        self.assertEqual(1, cells.call_count)
        self.assertEqual(2, len(report["found"]))

    def test_dry_run_never_uses_the_applied_wording(self):
        out = io.StringIO()
        with mock.patch.object(act.rim, "init"), \
             mock.patch.object(act, "cells", return_value=self.grid), \
             mock.patch("sys.stdout", out):
            rc = act.main(["undesignate", "20", "30", "2", "1"])
        self.assertEqual(0, rc)
        self.assertIn("DRY RUN - would cancel 2 designation(s) in 2 cell(s) "
                      "and 0 blueprint(s)/frame(s) (nothing applied; add --do)",
                      out.getvalue())
        self.assertNotIn("CANCELLED", out.getvalue())

    def test_a_surviving_designation_is_named_not_swallowed(self):
        after = [{"x": 21, "z": 30, "designations": [{"defName": "Tame"}],
                  "things": []}]
        out = io.StringIO()
        with mock.patch.object(act.rim, "init"), \
             mock.patch.object(act, "cells", side_effect=[self.grid, after]), \
             mock.patch.object(act, "apply",
                               return_value={"success": True}) as apply_, \
             mock.patch.object(act.pick, "clear_designator"), \
             mock.patch("sys.stdout", out):
            rc = act.main(["undesignate", "20", "30", "2", "1", "--do"])
        text = out.getvalue()
        self.assertEqual(1, rc)
        self.assertEqual(("Orders", "Cancel"), apply_.call_args.args[0])
        self.assertIn("CANCELLED 1 of 2 designation(s)", text)
        self.assertIn("20,30 -- removed mine", text)
        self.assertIn("!! still designated at 21,30 -- tame", text)
        self.assertIn("sits on the ANIMAL", text)

    def test_an_unread_rectangle_raises_instead_of_reporting_nothing(self):
        with mock.patch.object(act, "cells", return_value=None):
            with self.assertRaises(act.rim.BridgeError):
                act.designations_in(20, 30, 1, 1)


class MinePartialRefusalTests(unittest.TestCase):
    """A Mine sweep that takes some cells and refuses already-designated ones.

    `Designator_Mine.CanDesignateCell` returns `AcceptanceReport.WasRejected`
    -- rejected with an EMPTY reason -- when `DesignationAt(c, Mine)` is
    already there, so the bridge prints the same "the designator rejected this
    cell" a solid floor gets. The 2026-09-06 fix looked only when the
    designator accepted NOTHING and only when EVERY rejected cell was a no-op;
    the live case (turns 22/24/36) failed both tests.
    """
    result = {"success": True, "acceptedCellCount": 1,
              "acceptedCells": [{"x": 100, "z": 100}],
              "rejectedCellCount": 2,
              "rejectedCells": [
                  {"x": 101, "z": 100,
                   "reason": "the designator rejected this cell"},
                  {"x": 102, "z": 100,
                   "reason": "the designator rejected this cell"}]}
    grid = [{"x": 101, "z": 100, "designations": [{"defName": "Mine"}],
             "things": []},
            {"x": 102, "z": 100, "designations": [], "things": []}]

    def test_the_already_designated_cell_is_a_noop_and_the_other_is_not(self):
        out = io.StringIO()
        with mock.patch.object(act.rim, "init"), \
             mock.patch.object(act, "apply", return_value=self.result), \
             mock.patch.object(act, "designator", return_value="d1"), \
             mock.patch.object(act, "designation", return_value="Mine"), \
             mock.patch.object(act, "paints", return_value=None), \
             mock.patch.object(act, "cells", return_value=self.grid), \
             mock.patch.object(act.pick, "clear_designator"), \
             mock.patch("sys.stdout", out):
            rc = act.main(["apply", "Mine", "100", "100", "3", "1", "--do",
                           "--no-preview"])
        text = out.getvalue()
        self.assertEqual(0, rc)
        self.assertIn("1 of them already designated -- no-ops, not failures",
                      text)
        self.assertIn("101,100 -- NO-OP already carries the Mine designation",
                      text)
        self.assertIn("102,100 -- REFUSED: the designator rejected this cell",
                      text)


class AreaDesignatorTests(unittest.TestCase):
    def test_an_area_apply_says_the_desig_layer_cannot_show_it(self):
        act._rows[:] = [("Zone", {"label": "Build roof area", "id": "a1",
                                  "className": "RimWorld.Designator_AreaBuildRoof",
                                  "applicationKind": "area",
                                  "supportsCellApplication": True})]
        act._index()
        self.assertIn("map.py areas", act.paints("Build roof area"))
        self.assertIn("AREA, not a designation", act.paints("Build roof area"))


class HaulDesignationTests(unittest.TestCase):
    def test_a_refused_haul_cell_names_the_chunks_only_rule(self):
        note = act._cell_note("haul things", "Haul",
                              {"x": 1, "z": 2, "designations": [], "things": []},
                              "Must designate haulable items")
        self.assertEqual("refused", note[0])
        self.assertIn("ROCK CHUNKS", note[1])
        self.assertIn("order.py haul", note[1])


class UndesignateBlueprintTests(unittest.TestCase):
    """Cancel destroys blueprints and frames, so they are counted as well."""
    grid = [{"x": 20, "z": 30, "designations": [],
             "things": [{"isBlueprint": True, "label": "power conduit"}]}]

    def test_a_blueprint_alone_is_not_nothing_to_cancel(self):
        out = io.StringIO()
        with mock.patch.object(act.rim, "init"),              mock.patch.object(act, "cells", return_value=self.grid),              mock.patch("sys.stdout", out):
            rc = act.main(["undesignate", "20", "30"])
        text = out.getvalue()
        self.assertEqual(0, rc)
        self.assertNotIn("nothing to cancel", text)
        self.assertIn("would cancel 0 designation(s) in 0 cell(s) and 1 "
                      "blueprint(s)/frame(s)", text)
        self.assertIn("DESTROYED by Cancel", text)

    def test_the_blueprint_is_read_back_and_reported_as_destroyed(self):
        out = io.StringIO()
        after = [{"x": 20, "z": 30, "designations": [], "things": []}]
        with mock.patch.object(act.rim, "init"),              mock.patch.object(act, "cells",
                               side_effect=[self.grid, after]), \
                                    mock.patch.object(act, "apply", return_value={"success": True}), \
                                    mock.patch.object(act.pick, "clear_designator"), \
                                    mock.patch("sys.stdout", out):
            rc = act.main(["undesignate", "20", "30", "--do"])
        text = out.getvalue()
        self.assertEqual(0, rc)
        self.assertIn("and 1 of 1 blueprint(s)/frame(s)", text)
        self.assertIn("20,30 -- destroyed blueprint power conduit", text)

    def test_an_empty_rectangle_says_all_three_were_looked_for(self):
        out = io.StringIO()
        with mock.patch.object(act.rim, "init"),              mock.patch.object(act, "cells", return_value=[]),              mock.patch("sys.stdout", out):
            rc = act.main(["undesignate", "20", "30", "--do"])
        self.assertEqual(0, rc)
        self.assertIn("no designations, no blueprints, no frames",
                      out.getvalue())


class AllowAliasTests(unittest.TestCase):
    def test_unforbid_is_an_alias_for_allow(self):
        with mock.patch.object(act.rim, "init"), \
             mock.patch.object(act, "allow", return_value={
                 "dryRun": True, "found": [], "unforbidden": [],
                 "stillForbidden": [], "failed": []}) as allow, \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(0, act.main(["unforbid", "20", "30", "--size", "4", "4"]))
        self.assertEqual((20, 30, 4, 4), allow.call_args.args)


if __name__ == "__main__":
    unittest.main()
