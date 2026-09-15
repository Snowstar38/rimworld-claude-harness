"""Offline regressions for act.py argument and animal resolution contracts."""
import io
import unittest
from unittest import mock

import act


def animal(x=10, z=20, tid="Ibex404123", name="ibex"):
    return {"name": name, "defName": "Ibex", "kindDef": "Ibex",
            "position": {"x": x, "z": z},
            "animals": {"thingId": tid}}


def field_row(field, before, after, refused=None, also_removed=()):
    """One `home/pawn_config` fields[] row, in the shape the tool emits."""
    return {"field": field, "requested": after,
            "before": before, "after": before if refused else after,
            "changed": (not refused) and before != after,
            "refused": bool(refused), "reason": refused,
            "alsoRemoved": list(also_removed)}


def config_reply(rows, success=True, unknown=(), **extra):
    """A `home/pawn_config` reply. `unknown` is what an OLDER companion DLL
    puts in unknownArguments for a field it does not declare."""
    rows = list(rows)
    reply = {"success": success, "tool": "home/pawn_config",
             "fields": [r for r in rows if str(r["field"]) not in unknown],
             "refused": [r for r in rows if r["refused"]],
             "unknownArguments": list(unknown)}
    reply.update(extra)
    return reply


def bridge(reply, tool="home/pawn_config"):
    """Mock `rim.game` so only `tool` is answered; anything else is a fault in
    the test, not a silent fallthrough to a real socket."""
    def answer(name, args=None, **kw):
        if name != tool:
            raise AssertionError("unexpected bridge call %r" % name)
        answer.calls.append(args or {})
        return reply(args or {}) if callable(reply) else reply
    answer.calls = []
    return mock.patch.object(act.rim, "game", side_effect=answer), answer


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
        patch, _ = bridge(config_reply([field_row("hunt", False, True)]))
        with mock.patch("pawns.all_pawns", return_value=[animal()]) as all_pawns, \
             patch:
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

    def test_the_mark_goes_on_the_animal_by_id_not_at_a_cell(self):
        # The whole point of the 2026-09-11 rewrite: the designation hangs on
        # the ANIMAL, so the write carries a ThingID and no coordinates. An
        # animal that walked between the read and the write is still the right
        # animal, with no retry loop to get there.
        patch, sent = bridge(config_reply([field_row("hunt", False, True)]))
        with mock.patch("pawns.all_pawns", return_value=[animal(10, 20)]), \
             mock.patch.object(act, "apply") as apply, patch:
            act.hunt("Ibex404123", do=True)
        apply.assert_not_called()
        self.assertEqual([{"pawn": "Ibex404123", "dryRun": False, "hunt": "on"}],
                         sent.calls)

    def test_it_is_a_dry_run_until_do(self):
        patch, sent = bridge(config_reply([field_row("hunt", False, True)]))
        with mock.patch("pawns.all_pawns", return_value=[animal()]), patch:
            act.hunt("Ibex404123")
        self.assertIs(True, sent.calls[0]["dryRun"])

    def test_an_old_companion_dll_falls_back_to_the_cell_designator(self):
        # The host DROPS an argument a tool does not declare and answers
        # success:true with no field row -- which reads exactly like a write
        # that landed. unknownArguments is the only thing that says otherwise.
        old = config_reply([field_row("hunt", False, True)], unknown=("hunt",))
        reads = iter(([animal(10, 20)], [animal(12, 23)]))
        patch, _ = bridge(old)
        with mock.patch("pawns.all_pawns", side_effect=lambda **kw: next(reads)), \
             mock.patch.object(act, "apply", return_value={"success": True}) as apply, \
             patch:
            act.hunt("Ibex404123", do=True)
        self.assertEqual(("Hunt", 12, 23), apply.call_args.args[:3])

    def test_the_fallback_still_retries_the_stale_cell_refusal(self):
        old = config_reply([field_row("hunt", False, True)], unknown=("hunt",))
        reads = iter(([animal(10, 20)], [animal(11, 21)], [animal(12, 22)]))
        stale = {"success": False, "message": "Must designate huntable animals",
                 "rejectedCells": [{"x": 11, "z": 21,
                                     "reason": "Must designate huntable animals"}]}
        patch, _ = bridge(old)
        with mock.patch("pawns.all_pawns", side_effect=lambda **kw: next(reads)), \
             mock.patch.object(act, "apply", side_effect=[stale, {"success": True}]) as apply, \
             patch:
            result = act.hunt("Ibex404123", do=True)
        self.assertTrue(result[0][1]["success"])
        self.assertEqual(("Hunt", 12, 22), apply.call_args.args[:3])

    def test_an_already_marked_animal_is_a_no_op_not_a_refusal(self):
        # A second AddDesignation is a Verse.Log.Error, which pauses the
        # colony; the tool answers with a no-op row and so does this.
        marked = animal()
        marked["animals"]["designations"] = {"hunt": True, "tame": False}
        out = io.StringIO()
        patch, _ = bridge(config_reply([field_row("hunt", True, True)]))
        with mock.patch.object(act.rim, "init"), \
             mock.patch("pawns.all_pawns", return_value=[marked]), \
             mock.patch.object(act, "apply") as apply, \
             patch, mock.patch("sys.stdout", out):
            self.assertEqual(0, act.main(["hunt", "Ibex404123", "--do"]))
        apply.assert_not_called()
        self.assertIn("NO-OP", out.getvalue())

    def test_what_the_designator_also_cleared_is_printed(self):
        # Designator_Hunt.DesignateThing calls RemoveAllDesignationsOn FIRST,
        # so a hunt mark silently eats a tame one unless it is said out loud.
        out = io.StringIO()
        patch, _ = bridge(config_reply(
            [field_row("hunt", False, True, also_removed=["Tame"])],
            dryRun=False, applied=True))
        with mock.patch.object(act.rim, "init"), \
             mock.patch("pawns.all_pawns", return_value=[animal()]), \
             patch, mock.patch("sys.stdout", out):
            self.assertEqual(0, act.main(["hunt", "Ibex404123", "--do"]))
        text = out.getvalue()
        self.assertIn("APPLIED hunt to this ANIMAL", text)
        self.assertIn("also cleared Tame", text)
        self.assertNotIn("cell(s)", text)

    def test_a_refused_field_is_a_refusal_with_the_games_own_reason(self):
        why = ("This animal belongs to \"Player\", a humanlike faction ... the "
               "write you want is slaughter")
        out = io.StringIO()
        patch, _ = bridge(config_reply([field_row("hunt", False, True, refused=why)]))
        with mock.patch.object(act.rim, "init"), \
             mock.patch("pawns.all_pawns", return_value=[animal()]), \
             patch, mock.patch("sys.stdout", out):
            self.assertEqual(1, act.main(["hunt", "Ibex404123", "--do"]))
        self.assertIn("REFUSED", out.getvalue())
        self.assertIn("the write you want is slaughter", out.getvalue())

    def test_tame_writes_the_tame_field_and_names_the_cell_mates(self):
        fox = animal(49, 96, "Fox112", "fox")
        donkey = animal(49, 96, "Donkey405307", "donkey")
        patch, sent = bridge(config_reply([field_row("tame", False, True)]))
        with mock.patch("pawns.all_pawns", return_value=[fox, donkey]), patch:
            out = act.tame("Fox112", do=True)
        self.assertEqual("on", sent.calls[0]["tame"])
        self.assertEqual([donkey], out[0][2])

    def test_tame_cli_prints_the_target_id_and_the_other_animals(self):
        fox = animal(49, 96, "Fox112", "fox")
        donkey = animal(49, 96, "Donkey405307", "donkey")
        out = io.StringIO()
        patch, _ = bridge(config_reply(
            [field_row("tame", False, True, refused="Wildness is 1.0")]))
        with mock.patch.object(act.rim, "init"), \
             mock.patch("pawns.all_pawns", return_value=[fox, donkey]), \
             patch, mock.patch("sys.stdout", out):
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
             mock.patch.object(act.pick, "clear_designator"), \
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
             mock.patch.object(act.pick, "clear_designator"), \
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
             mock.patch.object(act.pick, "clear_designator"), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(1, act.main(["apply", "Hunt", "49", "96", "--do"]))

    def test_forbid_on_already_forbidden_things_is_a_no_op(self):
        refused = {"success": False, "message": "Must designate unforbidden items",
                   "rejectedCells": [{"x": 20, "z": 30, "reason": "nothing here"}]}
        grid = [{"x": 20, "z": 30,
                 "things": [{"defName": "Steel", "forbidden": True}]}]
        out = io.StringIO()
        # designation mocked because _rejected_notes consults the designation
        # table, and loading it is a live architect-menu fetch. None is its
        # real answer for Forbid, which is a CompForbiddable toggle, not a
        # designation.
        with mock.patch.object(act.rim, "init"), \
             mock.patch.object(act, "apply", return_value=refused), \
             mock.patch.object(act, "designation", return_value=None), \
             mock.patch.object(act, "cells", return_value=grid), \
             mock.patch.object(act.pick, "clear_designator"), \
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
        self.assertIn("nothing to cancel at 113,140", text)
        # ... and what IS there, with the command that removes it.
        self.assertIn("a finished wall stands at 113,140", text)
        self.assertIn("act.py deconstruct 113 140 --do", text)

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
        self.assertIn("act.py undesignate <animal> --do", text)

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


class ArgumentSpellingTests(unittest.TestCase):
    """`x,z` is one token to a person; every label is a verb."""

    def test_a_comma_pair_becomes_two_coordinates(self):
        self.assertEqual(["apply", "Mine", "106", "127"],
                         act.expand_argv(["apply", "Mine", "106,127"]))

    def test_a_bare_label_becomes_apply(self):
        self.assertEqual(["apply", "mine", "106", "127"],
                         act.expand_argv(["mine", "106", "127"]))

    def test_a_real_command_is_left_alone(self):
        for argv in (["hunt", "Ibex404123"], ["labels", "wall"],
                     ["undesignate", "20", "30"]):
            self.assertEqual(argv, act.expand_argv(list(argv)))

    def test_the_bare_verb_reaches_apply_with_that_label(self):
        out = io.StringIO()
        with mock.patch.object(act.rim, "init"), \
             mock.patch.object(act, "apply", return_value={"success": True,
                                                          "acceptedCellCount": 1}) as apply, \
             mock.patch.object(act, "paints", return_value=None), \
             mock.patch("sys.stdout", out):
            act.main(["mine", "106,127"])
        self.assertEqual(("mine", 106, 127, 1, 1), apply.call_args.args)
        self.assertIn("DRY RUN", out.getvalue())


class UnforbidRedirectTests(unittest.TestCase):
    """`apply "Unforbid"` names a designator that does not exist."""

    def test_it_says_what_it_is_doing_and_runs_allow(self):
        out = io.StringIO()
        with mock.patch.object(act.rim, "init"), \
             mock.patch.object(act, "_resolves", return_value=False), \
             mock.patch.object(act, "allow", return_value={
                 "dryRun": True, "found": [], "unforbidden": [],
                 "stillForbidden": [], "failed": []}) as allow, \
             mock.patch("sys.stdout", out):
            self.assertEqual(0, act.main(["apply", "Unforbid", "120", "140", "4", "4"]))
        self.assertEqual((120, 140, 4, 4), allow.call_args.args)
        self.assertIn("REDIRECT", out.getvalue())
        self.assertIn("act.py allow 120 140 4 4", out.getvalue())

    def test_a_label_that_does_resolve_is_left_to_the_designator(self):
        with mock.patch.object(act.rim, "init"), \
             mock.patch.object(act, "_resolves", return_value=True), \
             mock.patch.object(act, "apply", return_value={"success": True}) as apply, \
             mock.patch.object(act, "paints", return_value=None), \
             mock.patch.object(act, "allow") as allow, \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            act.main(["apply", "Allow", "120", "140"])
        allow.assert_not_called()
        self.assertEqual("Allow", apply.call_args.args[0])


class HuntManyTests(unittest.TestCase):
    """Three ids on one line failed, naming the 2nd and 3rd."""

    def test_every_id_on_the_line_is_designated_and_reported(self):
        rows = [animal(10, 20, "Ibex404123", "ibex"),
                animal(11, 21, "Hare1", "hare"),
                animal(12, 22, "Deer2", "deer")]
        out = io.StringIO()
        patch, sent = bridge(config_reply([field_row("hunt", False, True)],
                                          dryRun=False, applied=True))
        with mock.patch.object(act.rim, "init"), \
             mock.patch("pawns.all_pawns", return_value=rows), \
             patch, mock.patch("sys.stdout", out):
            rc = act.main(["hunt", "Ibex404123", "Hare1", "Deer2", "--do"])
        self.assertEqual(0, rc)
        self.assertEqual(["Ibex404123", "Hare1", "Deer2"],
                         [c["pawn"] for c in sent.calls])
        text = out.getvalue()
        for name in ("Ibex404123", "Hare1", "Deer2"):
            self.assertIn(name, text)

    def test_one_miss_does_not_stop_the_others(self):
        rows = [animal(10, 20, "Ibex404123", "ibex")]
        out = io.StringIO()
        patch, sent = bridge(config_reply([field_row("hunt", False, True)],
                                          dryRun=False, applied=True))
        with mock.patch.object(act.rim, "init"), \
             mock.patch("pawns.all_pawns", return_value=rows), \
             patch, mock.patch("sys.stdout", out):
            rc = act.main(["hunt", "Gone999", "Ibex404123", "--do"])
        self.assertEqual(1, rc)
        self.assertEqual(["Ibex404123"], [c["pawn"] for c in sent.calls])
        self.assertIn("Gone999", out.getvalue())
        self.assertIn("Ibex404123", out.getvalue())


class LabelMissTests(unittest.TestCase):
    """`act.py labels <word>` printed the filtered list -- empty -- and stopped,
    so a typo was answered with a blank screen (BUGS.md, 2026-09-11)."""

    def tearDown(self):
        act._rows[:] = []
        act._index()

    def run_cli(self, word):
        menu(row("wall...", "d1", "Structure"), row("Cooler", "d2", "Temperature"))
        out = io.StringIO()
        with mock.patch.object(act.rim, "init"), mock.patch("sys.stdout", out):
            rc = act.main(["labels", word])
        return rc, out.getvalue()

    def test_a_miss_says_so_and_lists_the_nearest(self):
        rc, text = self.run_cli("coolar")
        self.assertEqual(1, rc)
        self.assertIn("no label matches 'coolar'", text)
        self.assertIn("Cooler", text)

    def test_a_miss_with_nothing_close_names_the_full_listing(self):
        rc, text = self.run_cli("zzzzqqq")
        self.assertEqual(1, rc)
        self.assertIn("no label matches 'zzzzqqq'", text)
        self.assertIn("python act.py labels", text)

    def test_a_hit_still_just_prints_the_rows(self):
        rc, text = self.run_cli("wall")
        self.assertEqual(0, rc)
        self.assertIn("wall...", text)
        self.assertNotIn("no label matches", text)


class UninstallTests(unittest.TestCase):
    """Uninstall keeps a minifiable building whole; deconstruct does not."""

    def test_a_cell_goes_straight_to_the_orders_designator(self):
        out = io.StringIO()
        with mock.patch.object(act.rim, "init"), \
             mock.patch.object(act, "apply", return_value={
                 "success": True, "acceptedCellCount": 1}) as apply, \
             mock.patch("sys.stdout", out):
            rc = act.main(["uninstall", "106,127", "--do"])
        self.assertEqual(0, rc)
        self.assertEqual((("Orders", "Uninstall"), 106, 127), apply.call_args.args)
        self.assertIs(False, apply.call_args.kwargs["dry"])

    def test_a_thing_id_is_resolved_to_its_cell(self):
        out = io.StringIO()
        with mock.patch.object(act.rim, "init"), \
             mock.patch.object(act.pick, "resolve", return_value={
                 "label": "cooler", "position": {"x": 113, "z": 146}}), \
             mock.patch.object(act, "apply", return_value={
                 "success": True, "acceptedCellCount": 1}) as apply, \
             mock.patch("sys.stdout", out):
            act.main(["uninstall", "Thing_Cooler123"])
        self.assertEqual((("Orders", "Uninstall"), 113, 146), apply.call_args.args)
        self.assertIs(True, apply.call_args.kwargs["dry"])
        self.assertIn("cooler is at 113,146", out.getvalue())

    def test_a_menu_without_the_designator_names_the_gizmo_route(self):
        out = io.StringIO()
        with mock.patch.object(act.rim, "init"), \
             mock.patch.object(act, "apply", side_effect=KeyError("no designator")), \
             mock.patch("sys.stdout", out):
            rc = act.main(["uninstall", "106", "127", "--do"])
        text = out.getvalue()
        self.assertEqual(1, rc)
        self.assertIn('buildings.py gizmo <thingId> "Uninstall" --do', text)
        self.assertIn("REVERSE designator", text)
        self.assertIn("not minifiable", text)
        self.assertNotIn("act.py deconstruct", text)


class UndesignateAnimalTests(unittest.TestCase):
    """Hunt sits on the ANIMAL, and the animal has moved since the last read."""

    def marked(self, **marks):
        p = animal(10, 20)
        p["animals"]["designations"] = dict({"hunt": False, "tame": False,
                                             "slaughter": False,
                                             "releaseToWild": False}, **marks)
        return p

    def test_a_hunt_is_cleared_through_pawn_config_and_read_back(self):
        # The bug: `act.py hunt <id> --do` then `act.py undesignate <id> --do`
        # left the hunt standing, because Cancel is a CELL designator and Hunt
        # sits on the animal. The `after` here is a fresh read, not the reply.
        before, after = self.marked(hunt=True), self.marked()
        after["position"] = {"x": 12, "z": 23}
        reads = iter(([before], [after]))
        out = io.StringIO()
        patch, sent = bridge(config_reply([field_row("hunt", True, False)],
                                          dryRun=False, applied=True))
        with mock.patch.object(act.rim, "init"), \
             mock.patch("pawns.all_pawns", side_effect=lambda **kw: next(reads)), \
             mock.patch.object(act, "apply") as apply, \
             patch, mock.patch("sys.stdout", out):
            rc = act.main(["undesignate", "Ibex404123", "--do"])
        self.assertEqual(0, rc)
        apply.assert_not_called()
        self.assertEqual([{"pawn": "Ibex404123", "dryRun": False, "hunt": "off"}],
                         sent.calls)
        self.assertIn("CANCELLED hunt", out.getvalue())

    def test_every_standing_mark_is_cleared_in_one_call(self):
        before = self.marked(hunt=True, slaughter=True)
        reads = iter(([before], [self.marked()]))
        out = io.StringIO()
        patch, sent = bridge(config_reply(
            [field_row("hunt", True, False), field_row("slaughter", True, False)],
            dryRun=False, applied=True))
        with mock.patch.object(act.rim, "init"), \
             mock.patch("pawns.all_pawns", side_effect=lambda **kw: next(reads)), \
             patch, mock.patch("sys.stdout", out):
            rc = act.main(["undesignate", "Ibex404123", "--do"])
        self.assertEqual(0, rc)
        self.assertEqual({"hunt": "off", "slaughter": "off"},
                         {k: v for k, v in sent.calls[0].items()
                          if k in ("hunt", "slaughter")})
        self.assertIn("CANCELLED hunt, slaughter", out.getvalue())

    def test_a_mark_the_tool_refused_is_named_with_its_reason(self):
        still = self.marked(slaughter=True)
        out = io.StringIO()
        patch, _ = bridge(config_reply(
            [field_row("slaughter", True, False,
                       refused="The designation manager was not reachable.")]))
        with mock.patch.object(act.rim, "init"), \
             mock.patch("pawns.all_pawns", return_value=[still]), \
             patch, mock.patch("sys.stdout", out):
            rc = act.main(["undesignate", "Ibex404123", "--do"])
        self.assertEqual(1, rc)
        self.assertIn("slaughter still stands", out.getvalue())
        self.assertIn("designation manager was not reachable", out.getvalue())

    def test_an_old_companion_dll_says_so_and_names_the_reinstall(self):
        still = self.marked(slaughter=True)
        out = io.StringIO()
        patch, _ = bridge(config_reply([field_row("slaughter", True, False)],
                                       unknown=("slaughter",)))
        with mock.patch.object(act.rim, "init"), \
             mock.patch("pawns.all_pawns", return_value=[still]), \
             mock.patch.object(act, "apply", return_value={"success": True}) as apply, \
             patch, mock.patch("sys.stdout", out):
            rc = act.main(["undesignate", "Ibex404123", "--do"])
        self.assertEqual(1, rc)
        self.assertEqual((("Orders", "Cancel"), 10, 20), apply.call_args.args)
        self.assertIn("Reinstall the companion DLL", out.getvalue())

    def test_an_unmarked_animal_is_a_no_op_and_never_designates(self):
        out = io.StringIO()
        patch, sent = bridge(config_reply([]))
        with mock.patch.object(act.rim, "init"), \
             mock.patch("pawns.all_pawns", return_value=[self.marked()]), \
             mock.patch.object(act, "apply") as apply, \
             patch, mock.patch("sys.stdout", out):
            rc = act.main(["undesignate", "Ibex404123", "--do"])
        self.assertEqual(0, rc)
        apply.assert_not_called()
        self.assertEqual([], sent.calls)
        self.assertIn("NO-OP", out.getvalue())

    def test_it_is_a_dry_run_until_do(self):
        out = io.StringIO()
        patch, sent = bridge(config_reply([field_row("hunt", True, False)]))
        with mock.patch.object(act.rim, "init"), \
             mock.patch("pawns.all_pawns", return_value=[self.marked(hunt=True)]), \
             patch, mock.patch("sys.stdout", out):
            act.main(["undesignate", "Ibex404123"])
        self.assertIs(True, sent.calls[0]["dryRun"])
        self.assertIn("DRY RUN", out.getvalue())


class NothingToCancelSaysWhatIsThereTests(unittest.TestCase):
    """"nothing to cancel" 90 s after a blueprint id came back at that cell."""

    def run_cli(self, grid):
        out = io.StringIO()
        with mock.patch.object(act.rim, "init"), \
             mock.patch.object(act, "cells", return_value=grid), \
             mock.patch("sys.stdout", out):
            rc = act.main(["undesignate", "128", "147"])
        return rc, out.getvalue()

    def test_a_finished_wall_is_named_with_the_command_that_removes_it(self):
        rc, text = self.run_cli([{"x": 128, "z": 147, "designations": [],
                                  "things": [{"className": "Building_Door",
                                              "label": "wall"}]}])
        self.assertEqual(0, rc)
        self.assertIn("a finished wall stands at 128,147", text)
        self.assertIn("python act.py deconstruct 128 147 --do", text)
        self.assertIn("act.py uninstall 128 147 --do", text)

    def test_an_animal_is_named_with_the_animal_form(self):
        rc, text = self.run_cli([{"x": 128, "z": 147, "designations": [],
                                  "things": [{"className": "Pawn", "label": "ibex"}]}])
        self.assertIn("Hunt, Tame and Slaughter sit on the ANIMAL", text)
        self.assertIn("act.py undesignate ibex --do", text)

    def test_an_empty_cell_still_says_it_is_empty(self):
        rc, text = self.run_cli([])
        self.assertEqual(0, rc)
        self.assertIn("every cell read is empty", text)



class SlaughterRouteTest(unittest.TestCase):
    """Our own animal refuses hunt -- and the way out must be pasteable.

    Live 2026-09-12: the companion's own sentence ends "the write you want is
    slaughter -- home/pawn_config {pawn, slaughter:\"on\", dryRun:false}",
    which is a bridge payload. PLAYBOOK and BUGS.md promise the CLI spelling.
    """

    REFUSAL = {"success": False,
               "message": ('This animal belongs to "The Narrow Way", a '
                           "humanlike faction ... the write you want is "
                           "slaughter -- home/pawn_config {pawn, "
                           'slaughter:"on", dryRun:false} -- not hunt.')}

    def printed(self, result, animal_id=None):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = act._print_result(result, dry=False, animal_id=animal_id)
        return code, out.getvalue()

    def test_the_refusal_names_the_pawns_py_command_with_the_id(self):
        code, text = self.printed(self.REFUSAL, animal_id="GuineaPig45908")
        self.assertEqual(1, code)
        self.assertIn("python pawns.py set GuineaPig45908 --slaughter on --do",
                      text)

    def test_without_an_id_it_still_gives_the_shape(self):
        _, text = self.printed(self.REFUSAL)
        self.assertIn("python pawns.py set <animal-id> --slaughter on --do",
                      text)

    def test_an_unrelated_refusal_gets_no_slaughter_line(self):
        _, text = self.printed({"success": False,
                                "message": "Must designate haulable items"})
        self.assertNotIn("slaughter", text)


if __name__ == "__main__":
    unittest.main()
