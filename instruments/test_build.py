"""Offline regressions for build.py's refusal path, on faked place_building replies.

`BridgeCommon.Failure` returns `{success, tool, error}`; build.py read `message`
and printed `FAILED: None`. All fixtures are plain dicts; no game required.
"""
import io
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import build


def unknown(spec):
    """Exactly what PlaceBuildingTool.Build refuses an unresolvable name with."""
    return {"success": False, "tool": "home/place_building",
            "error": 'No ThingDef or TerrainDef matches "%s" by defName or label.' % spec}


def placed_ok():
    """A dry run that resolved, so the failure branch must not touch it."""
    return {"success": True, "tool": "home/place_building", "dryRun": True,
            "def": {"defName": "PowerConduit", "label": "power conduit",
                    "kind": "ThingDef"},
            "position": {"x": 114, "z": 145}, "rotatable": False,
            "researchFinished": True, "costList": [{"defName": "Steel", "count": 1}],
            "materials": {"rows": [{"defName": "Steel", "label": "steel",
                                    "needed": 1, "onMap": 300, "forbidden": 0,
                                    "reservedByOtherBlueprints": 0,
                                    "available": 300, "shortfall": 0}],
                          "canBuildNow": True, "missing": "", "unreadable": False},
            "rotations": [{"rotation": "north", "accepted": True, "reason": "",
                           "blockingThings": []}]}


def render(reply, asked=None, menu=()):
    """`report` with the architect menu stubbed out (these tests are offline)."""
    with mock.patch.object(build, "_menu_names", return_value=list(menu)):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            build.report(reply, asked)
    return out.getvalue()


class ReasonTests(unittest.TestCase):
    def test_the_bridge_reason_is_read_off_error_not_message(self):
        text = render(unknown("Conduit"), "Conduit")
        self.assertNotIn("FAILED: None", text)
        self.assertIn("No ThingDef or TerrainDef matches", text)
        self.assertIn('"Conduit"', text)

    def test_message_still_wins_where_a_stock_tool_uses_it(self):
        self.assertEqual(build.reason({"success": False, "message": "no map loaded",
                                       "error": "unused"}), "no map loaded")

    def test_a_reply_with_no_reason_at_all_never_renders_as_none(self):
        text = build.reason({"success": False, "tool": "home/place_building"})
        self.assertNotIn("None", text)
        self.assertIn("no reason given", text)
        self.assertIn("tool", text)

    def test_a_non_dict_reply_is_reported_as_one(self):
        self.assertIn("did not return a payload", build.reason("truncated json"))


class AliasTests(unittest.TestCase):
    def test_conduit_names_powerconduit(self):
        text = render(unknown("Conduit"), "Conduit")
        self.assertIn("PowerConduit", text)
        self.assertIn("known miss", text)

    def test_sculptorstable_names_tablesculpting(self):
        self.assertIn("TableSculpting", render(unknown("SculptorsTable"), "SculptorsTable"))

    def test_the_alias_is_case_and_space_insensitive(self):
        self.assertIn("TableButcher", render(unknown("butcher table"), "butcher table"))

    def test_an_ambiguous_alias_prints_both_defs_and_picks_neither(self):
        text = render(unknown("Stove"), "Stove")
        self.assertIn("ElectricStove", text)
        self.assertIn("FueledStove", text)
        self.assertIn("name the one you meant", text)

    def test_nothing_is_substituted_for_what_was_asked_for(self):
        # A refusal, not a quiet correction. (The verdict line says
        # "== NOTHING WAS PLACED." like every other refusal in this file, so
        # the thing to assert is that nothing claims a placement -- not that
        # the word never appears.)
        text = render(unknown("Conduit"), "Conduit")
        self.assertIn("FAILED:", text)
        self.assertIn("== NOTHING WAS PLACED.", text)
        self.assertNotIn("== PLACED", text)
        self.assertNotIn("blueprint id", text)


class SuggestionTests(unittest.TestCase):
    def test_a_typo_with_no_alias_still_gets_near_misses(self):
        text = render(unknown("SolarGenerater"), "SolarGenerater")
        self.assertIn("closest:", text)
        self.assertIn("SolarGenerator", text)

    def test_no_more_than_eight_are_named(self):
        with mock.patch.object(build, "_menu_names", return_value=[]):
            lines = build.unknown_def_lines("Table", limit=8)
        closest = [l for l in lines if "closest:" in l]
        self.assertTrue(closest)
        self.assertLessEqual(len(closest[0].split("closest:")[1].split(",")), 8)

    def test_the_architect_menu_joins_the_pool_when_the_game_answers(self):
        # Menu labels resolve as well as defNames, so they are valid suggestions.
        text = render(unknown("sculptors table"), "sculptors table",
                      menu=["sculptor's table", "power conduit"])
        self.assertIn("sculptor's table", text)

    def test_an_unreachable_game_falls_back_to_the_static_pool(self):
        import act
        with mock.patch.object(act, "labels", side_effect=ConnectionError("down")):
            self.assertEqual(build._menu_names(), [])
            self.assertTrue(any("PowerConduit" in l
                                for l in build.unknown_def_lines("Conduit")))


class HealthyPathTests(unittest.TestCase):
    def test_a_def_that_resolves_is_untouched_by_any_of_this(self):
        text = render(placed_ok(), "PowerConduit")
        self.assertIn("power conduit (PowerConduit) at 114,145 -- DRY RUN", text)
        self.assertIn("north", text)
        self.assertNotIn("FAILED", text)
        self.assertNotIn("closest:", text)
        self.assertNotIn("known miss", text)

    def test_a_refusal_that_is_not_about_the_def_gets_no_alias_block(self):
        text = render({"success": False, "tool": "home/place_building",
                       "error": "(999,999) is not a cell on this map."}, "Conduit")
        self.assertIn("not a cell on this map", text)
        self.assertNotIn("closest:", text)
        self.assertNotIn("known miss", text)


class MainTests(unittest.TestCase):
    def run_main(self, argv, reply):
        fake = mock.MagicMock()
        fake.game.return_value = reply
        with mock.patch.object(build, "rim", fake):
            with mock.patch.object(build, "_menu_names", return_value=[]):
                with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                    build.main(argv)
        return out.getvalue()

    def test_the_reported_live_case_end_to_end(self):
        text = self.run_main(["Conduit", "114", "145"], unknown("Conduit"))
        self.assertNotIn("FAILED: None", text)
        self.assertIn("PowerConduit", text)

    def test_do_with_no_rotation_reports_the_bad_def_not_the_rotation(self):
        # The rotation probe fails too; that must not be read as "unknown rotatable".
        text = self.run_main(["SculptorsTable", "114", "145", "--do"],
                             unknown("SculptorsTable"))
        self.assertIn("TableSculpting", text)
        self.assertNotIn("needs one rotation", text)


class OneVerdictTests(unittest.TestCase):
    """2026-09-07 turn 33: a real placement printed `-- PLACED` and
    `north REFUSED -- Identical thing already exists` in one output."""

    def refused_write(self):
        return {"success": False, "tool": "home/place_building", "dryRun": False,
                "outcome": "refused",
                "error": "The game refuses this placement: Identical thing already exists here.",
                "def": {"defName": "Wall", "label": "wall", "kind": "ThingDef"},
                "position": {"x": 122, "z": 140}, "rotatable": False,
                "researchFinished": True, "materials": {"rows": []},
                "rotations": [{"rotation": "north", "accepted": False,
                               "reason": "Identical thing already exists here.",
                               "blockingThings": []}]}

    def test_a_refused_write_never_says_placed(self):
        text = render(self.refused_write())
        self.assertNotIn("-- PLACED", text)
        self.assertIn("-- REFUSED", text)
        self.assertIn("== REFUSED -- NOTHING was placed.", text)
        self.assertIn("Identical thing already exists", text)

    def test_the_rotation_row_is_detail_not_a_second_verdict(self):
        text = render(self.refused_write())
        self.assertEqual(1, text.count("=="))
        self.assertIn("north no --", text)

    def test_a_real_placement_carries_the_blueprint_id(self):
        reply = dict(placed_ok(), dryRun=False, outcome="placed",
                     placed={"thingIDNumber": 407231, "label": "power conduit",
                             "position": {"x": 114, "z": 145}, "rotation": "North"})
        text = render(reply)
        self.assertIn("== PLACED -- blueprint id 407231", text)

    def test_already_present_is_its_own_verdict(self):
        reply = dict(placed_ok(), dryRun=False, outcome="already_present",
                     alreadyPlaced=True, placed=None)
        text = render(reply)
        self.assertIn("== ALREADY THERE", text)
        self.assertNotIn("== PLACED", text)


class BlockerTests(unittest.TestCase):
    """Floor filth blocks nothing; on turn 26 it buried a torch lamp."""

    def row(self):
        return {"rotation": "north", "accepted": False,
                "reason": "Space already occupied.",
                "blockingThings": [
                    {"label": "dirt", "defName": "Filth_Dirt", "category": "Filth",
                     "position": {"x": 122, "z": 140}},
                    {"label": "blood", "defName": "Filth_Blood", "category": "Filth",
                     "position": {"x": 123, "z": 140}},
                    {"label": "torch lamp", "defName": "TorchLamp",
                     "category": "Building", "position": {"x": 122, "z": 140}},
                ]}

    def test_filth_is_dropped_and_counted_and_the_building_leads(self):
        line = build._blocker_line(self.row())
        self.assertTrue(line.strip().startswith("in the way: torch lamp"))
        self.assertNotIn("dirt", line)
        self.assertIn("2 filth ignored", line)


class RotationRefusalTests(unittest.TestCase):
    """`build.py Battery ... --do` refused on STDERR and read as doing nothing."""

    def test_the_refusal_is_on_stdout_with_the_four_verdicts(self):
        sweep = {"success": True, "dryRun": True, "outcome": "preview",
                 "rotations": [{"rotation": "north", "accepted": False,
                                "reason": "Space already occupied."},
                               {"rotation": "east", "accepted": True, "reason": ""},
                               {"rotation": "south", "accepted": False, "reason": "x"},
                               {"rotation": "west", "accepted": True, "reason": ""}]}
        with mock.patch.object(build, "place", return_value=sweep):
            with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                code = build.needs_rotation("Battery", 120, 140)
        text = out.getvalue()
        self.assertEqual(1, code)
        self.assertIn("BUILD REFUSED", text)
        self.assertIn("== NOTHING WAS PLACED", text)
        self.assertIn("build.py Battery 120 140 east --do", text)


def blocker(label, x=120, z=136, **kw):
    row = {"defName": label, "label": label, "category": "Plant",
           "position": {"x": x, "z": z}}
    row.update(kw)
    return row


def sweep(accepted=True, blockers=(), rotations=("north", "east", "south", "west")):
    """A dry-run reply: every rotation with the same footprint under it."""
    return {"success": True, "tool": "home/place_building", "dryRun": True,
            "outcome": "preview",
            "def": {"defName": "Wall", "label": "wall", "kind": "ThingDef"},
            "position": {"x": 120, "z": 136}, "rotatable": False,
            "rotations": [{"rotation": r, "accepted": accepted, "reason": "",
                           "blockingThings": list(blockers)} for r in rotations]}


class FlagsAreNeverPositionalTests(unittest.TestCase):
    """`build.py <def> x z --dry-run` read the flag as the rotation."""

    def test_a_flag_after_the_coordinates_is_not_the_rotation(self):
        pos, flags, values, unknown = build.split_args(
            ["Grave", "120", "136", "--dry-run"])
        self.assertEqual(["Grave", "120", "136"], pos)
        self.assertIn("--dry-run", flags)
        self.assertEqual([], unknown)

    def test_stuff_takes_its_value_either_spelling(self):
        for argv in (["Wall", "1", "2", "--stuff", "BlocksSandstone"],
                     ["Wall", "1", "2", "--stuff=BlocksSandstone"]):
            pos, _, values, unknown = build.split_args(argv)
            self.assertEqual(["Wall", "1", "2"], pos)
            self.assertEqual("BlocksSandstone", values["--stuff"])
            self.assertEqual([], unknown)

    def test_an_unknown_flag_is_named_not_dropped(self):
        _, _, _, unknown = build.split_args(["Wall", "1", "2", "--rooms"])
        self.assertEqual(["--rooms"], unknown)


class DryRunSaysSoFirstTests(unittest.TestCase):
    """Two graves read as placed because a dry run looked like one."""

    def run_cli(self, argv, reply=None):
        # _menu_names stubbed like render(): the unknown-def path asks the
        # live architect menu for suggestions, and these tests are offline.
        with mock.patch.object(build.rim, "init"), \
             mock.patch.object(build, "_menu_names", return_value=[]), \
             mock.patch.object(build, "place",
                               return_value=reply or sweep()) as place, \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = build.main(argv)
        return code, out.getvalue(), place

    def test_dry_run_is_the_first_line_and_placed_appears_nowhere(self):
        code, text, place = self.run_cli(["Wall", "120", "136"])
        self.assertIn(code, (0, None))
        self.assertEqual("DRY RUN -- nothing placed; add --do",
                         text.splitlines()[0])
        self.assertNotIn("PLACED", text)
        self.assertIs(False, place.call_args.args[5] if len(place.call_args.args) > 5
                      else place.call_args.kwargs.get("do"))

    def test_dry_run_flag_is_accepted_and_never_read_as_a_rotation(self):
        code, text, place = self.run_cli(["Wall", "120", "136", "--dry-run"])
        self.assertIn(code, (0, None))
        self.assertNotIn("rotation must be", text)
        self.assertIn("DRY RUN -- nothing placed; add --do", text)
        self.assertEqual("all", place.call_args.args[3])

    def test_do_and_dry_run_together_are_refused(self):
        code, text, place = self.run_cli(["Wall", "120", "136", "north",
                                          "--do", "--dry-run"])
        self.assertEqual(1, code)
        self.assertIn("== NOTHING WAS PLACED", text)
        place.assert_not_called()

    def test_a_dry_run_names_what_it_would_wipe(self):
        """`wipeOnPlace` is a ROTATION ROW key in the companion
        (PlaceBuildingTool.cs:1291), not a top-level one. Read at the top level
        it was a branch that could never fire, so the detail under the
        `would wipe/replace:` verdict tail was never printed."""
        reply = placed_ok()
        reply["rotations"] = [{
            "rotation": "north", "accepted": True, "reason": "",
            "blockingThings": [],
            "wipeOnPlace": [{"defName": "Plant_Rice", "label": "rice plant",
                             "position": {"x": 114, "z": 145}}],
            "framesCancelledOnPlace": []}]
        text = render(reply, "Wall")
        self.assertIn("would wipe: rice plant at 114,145", text)

    def test_a_single_cell_refusal_exits_non_zero(self):
        """build.py's own closing comment: "A refusal exits non-zero so a script
        that chained on this one stops." The batch path returns its reporter's
        code; the single-cell path dropped it and exited 0 on every REFUSED."""
        refused = {"success": True, "tool": "home/place_building",
                   "dryRun": False, "outcome": "refused",
                   "error": "Space already occupied.",
                   "def": {"defName": "Wall", "label": "wall"},
                   "position": {"x": 110, "z": 146},
                   "rotations": [{"rotation": "north", "accepted": False,
                                  "reason": "Space already occupied."}]}
        code, text, _ = self.run_cli(["Wall", "110", "146", "north", "--do"],
                                     reply=refused)
        self.assertEqual(1, code)
        self.assertIn("== REFUSED", text)

    def test_an_unknown_def_ends_with_a_verdict_line_and_exits_non_zero(self):
        """PLAYBOOK: "one verdict, on the LAST line, starting ==". The hard
        failure branch printed the aliases and stopped, so the one call most
        likely to be misread ended with no verdict at all."""
        code, text, _ = self.run_cli(["Conduit", "114", "145"],
                                     reply=unknown("Conduit"))
        self.assertEqual(1, code)
        self.assertTrue(text.rstrip().splitlines()[-1].startswith("=="),
                        "last line was %r" % text.rstrip().splitlines()[-1])
        self.assertIn("== NOTHING WAS PLACED.", text)

    def test_an_unknown_def_with_do_and_no_rotation_exits_non_zero(self):
        """Found live 2026-09-12: `build.py <unknown def> x z --do` printed
        `== NOTHING WAS PLACED.` and exited 0. The rotation-defaulting probe
        recognised the unknown def, called `report()` for the near-misses and
        DROPPED its return code -- the only refusal in main that did."""
        code, text, _ = self.run_cli(["Conduit", "114", "145", "--do"],
                                     reply=unknown("Conduit"))
        self.assertEqual(1, code)
        self.assertIn("== NOTHING WAS PLACED.", text)

    def test_a_single_cell_do_says_a_non_payload_reply_rather_than_raising(self):
        """`reason()`, `is_unknown_def()` and `report_batch()` all guard the
        not-a-dict shape explicitly, so it is a shape this file expects.
        `report()` did not, and read `rotationDefaulted` off the string."""
        code, text, _ = self.run_cli(["Wall", "120", "136", "north", "--do"],
                                     reply="<html>504 Gateway Timeout</html>")
        self.assertEqual(1, code)
        self.assertIn("did not return a payload", text)
        self.assertIn("== NOTHING WAS PLACED.", text)

    def test_an_unknown_flag_refuses_instead_of_being_ignored(self):
        code, text, place = self.run_cli(["Wall", "120", "136", "--rooms"])
        self.assertEqual(1, code)
        self.assertIn("--rooms", text)
        place.assert_not_called()

    def test_a_material_in_the_rotation_slot_names_the_stuff_flag(self):
        code, text, place = self.run_cli(["Wall", "120", "136", "BlocksSandstone"])
        self.assertEqual(1, code)
        self.assertIn("--stuff BlocksSandstone", text)
        place.assert_not_called()


class VerdictCarriesTheCostTests(unittest.TestCase):
    """"4 of 4 rotations accepted" over a rice crop and over our own wall."""

    def test_a_crop_and_a_replaced_wall_reach_the_verdict_line(self):
        r = sweep(blockers=[blocker("rice plant", effectOnPlace="crop"),
                            blocker("wooden wall", category="Building",
                                    effectOnPlace="replaced")])
        line = build.verdict_line(r)
        self.assertIn("4 of 4 rotation(s) accepted", line)
        self.assertIn("would wipe/replace: rice plant, wooden wall", line)

    def test_wild_grass_is_not_a_cost_and_stays_off_the_verdict(self):
        r = sweep(blockers=[blocker("grass", effectOnPlace="cleared")])
        self.assertNotIn("wipe/replace", build.verdict_line(r))
        self.assertEqual([], build.costs(r))

    def test_a_refused_rotation_never_contributes_a_cost(self):
        r = sweep(accepted=False,
                  blockers=[blocker("rice plant", effectOnPlace="crop")])
        self.assertEqual([], build.costs(r))

    def test_an_older_dll_still_reports_what_it_can(self):
        """No `effectOnPlace`: `wouldBeWiped` is all there is, and it counts."""
        r = sweep(blockers=[blocker("steel", category="Item", wouldBeWiped=True)])
        self.assertEqual(["steel"], build.costs(r))

    def test_an_accepted_rotation_does_not_say_in_the_way(self):
        r = sweep(blockers=[blocker("grass", effectOnPlace="cleared")])
        line = build._blocker_line(r["rotations"][0])
        self.assertIn("accepted over: grass", line)
        self.assertNotIn("in the way", line)
        self.assertIn("cleared when built, no effect", line)

    def test_a_refused_rotation_still_says_in_the_way(self):
        r = sweep(accepted=False,
                  blockers=[blocker("wooden wall", category="Building",
                                    effectOnPlace="replaced")])
        self.assertIn("in the way: wooden wall", build._blocker_line(r["rotations"][0]))


def batch_rows(cells, outcomes=None, do=True, rotation="north", first_id=40700,
               blockers=()):
    """Per-cell rows exactly as PlaceBuildingTool.RunChunk builds them."""
    rows, ids = [], []
    n = dict(placed=0, alreadyPresent=0, refused=0, errors=0, accepted=0)
    for i, (x, z) in enumerate(cells):
        out = (outcomes or {}).get((x, z), "placed" if do else "preview")
        row = {"rotation": rotation, "rotationInt": 0,
               "accepted": out in ("placed", "preview"),
               "reason": "" if out in ("placed", "preview") else "Space already occupied.",
               "occupiedRect": None, "occupiedCells": [],
               "blockingThings": list(blockers), "blockingThingCount": len(blockers),
               "wipeOnPlace": [], "framesCancelledOnPlace": [],
               "identicalBlueprintExists": out == "already_present",
               "x": x, "z": z, "outcome": out, "placed": None,
               "wiped": [], "framesCancelled": [], "error": None}
        if out in ("placed", "preview"):
            n["accepted"] += 1
        if out == "placed":
            pid = first_id + i
            ids.append(pid)
            n["placed"] += 1
            row["placed"] = {"thingIDNumber": pid, "defName": "Blueprint_Wall",
                             "label": "wall (blueprint)",
                             "position": {"x": x, "z": z}, "rotation": "North",
                             "rotationWord": rotation}
        elif out == "already_present":
            n["alreadyPresent"] += 1
            row["accepted"] = False
        elif out == "refused":
            n["refused"] += 1
        elif out == "error":
            n["errors"] += 1
            row["error"] = "PlaceBlueprintForBuild returned nothing."
        rows.append(row)
    return rows, ids, n


def batch_reply(cells, outcomes=None, do=True, rotation="north", first_id=40700,
                blockers=(), for_cells=None):
    """A `home/place_building` reply carrying a `batch` block."""
    rows, ids, n = batch_rows(cells, outcomes, do, rotation, first_id, blockers)
    if do:
        outcome = ("placed" if n["placed"] and not (n["refused"] + n["errors"])
                   else "partial" if n["placed"]
                   else "already_present" if n["alreadyPresent"] and not (n["refused"] + n["errors"])
                   else "error" if n["errors"] else "refused")
    else:
        outcome = "preview"
    first_refusal = None
    for row in rows:
        if row["outcome"] == "refused":
            first_refusal = "(%d,%d): %s" % (row["x"], row["z"], row["reason"])
            break
    return {"success": True, "tool": "home/place_building", "dryRun": not do,
            "def": {"defName": "Wall", "label": "wall", "kind": "ThingDef"},
            "position": {"x": cells[0][0], "z": cells[0][1]},
            "rotatable": False, "researchFinished": True,
            "costList": [{"defName": "BlocksSandstone", "label": "sandstone blocks",
                          "count": 5}],
            "materials": {"rows": [{"defName": "BlocksSandstone",
                                    "label": "sandstone blocks",
                                    "needed": 5 * (for_cells or len(cells)),
                                    "perCellNeeded": 5, "onMap": 600, "forbidden": 0,
                                    "reservedByOtherBlueprints": 0,
                                    "available": 600, "shortfall": 0}],
                          "forCells": for_cells or len(cells),
                          "canBuildNow": True, "missing": "", "unreadable": False},
            "rotations": [rows[0]], "rotationsEvaluated": 1,
            "acceptedRotations": [rotation] if n["accepted"] else [],
            "canPlace": n["accepted"] > 0, "applied": n["placed"] > 0,
            "outcome": outcome,
            "placed": rows[0].get("placed"),
            "alreadyPlaced": n["alreadyPresent"] == len(cells),
            "wiped": [], "framesCancelled": [],
            "watch": {"shown": True, "selected": True, "inspectTab": None,
                      "mainTab": None, "cameraMoved": True, "leadMs": 1500,
                      "closesAfterSeconds": 8, "note": None, "reason": None},
            "batch": {"requested": len(cells), "rotation": rotation,
                      "cellsPerHop": 8, "hops": (len(cells) + 7) // 8,
                      "hopGapMs": 20,
                      "placed": n["placed"], "alreadyPresent": n["alreadyPresent"],
                      "refused": n["refused"], "errors": n["errors"],
                      "accepted": n["accepted"], "placedIds": ids,
                      "firstRefusal": first_refusal, "rows": rows}}


def listing(rows):
    return {"success": True, "tool": "home/list_buildings", "buildings": list(rows)}


def bp_row(x, z, thing_id, label="wall (blueprint)"):
    return {"thingId": thing_id, "defName": "Blueprint_Wall", "label": label,
            "position": {"x": x, "z": z}, "isBlueprint": True, "isFrame": False,
            "workLeftText": "Work left: 18"}


class FakeRim(object):
    """Records every bridge call. `route` picks the reply from (tool, args)."""

    def __init__(self, route):
        self.calls = []
        self.route = route

    def init(self):
        self.calls.append(("init", {}))

    def game(self, tool, args=None, strict=True):
        self.calls.append((tool, dict(args or {})))
        return self.route(tool, dict(args or {}))

    def named(self, tool):
        return [a for (t, a) in self.calls if t == tool]


def run_batch_cli(argv, route, reads=()):
    """`build.main` with the bridge faked. -> (exit code, output, FakeRim)."""
    fake = FakeRim(route)
    with mock.patch.object(build, "rim", fake), \
            mock.patch.object(build, "_menu_names", return_value=[]), \
            mock.patch.object(build.time, "sleep"), \
            mock.patch("sys.stdout", new_callable=io.StringIO) as out:
        code = build.main(argv)
    return code, out.getvalue(), fake


def wall_route(cells, outcomes=None, confirmed=True, rotatable=False,
               listing_rows=None):
    """probe -> batch -> the read-back, in the order build.py makes them."""
    rows = listing_rows
    if rows is None:
        rows = ([bp_row(x, z, "Blueprint_Wall%d" % (40700 + i))
                 for i, (x, z) in enumerate(cells)] if confirmed else [])

    def route(tool, args):
        if tool == "home/list_buildings":
            return listing(rows)
        if args.get("cells"):
            return batch_reply(cells, outcomes, do=not args.get("dryRun"))
        # the rotatable probe
        return {"success": True, "dryRun": True, "outcome": "preview",
                "rotatable": rotatable,
                "def": {"defName": "Wall", "label": "wall", "kind": "ThingDef"},
                "position": {"x": args.get("x"), "z": args.get("z")},
                "rotations": [{"rotation": "north", "accepted": True,
                               "reason": "", "blockingThings": []}]}
    return route


def wall_line(x, z, to_x):
    return [(c, z) for c in range(x, to_x + 1)]


class LineAndCellParsingTests(unittest.TestCase):
    def test_a_line_is_inclusive_at_both_ends(self):
        self.assertEqual(12, len(build.line_cells(141, 130, 152, 130)))
        self.assertEqual((141, 130), build.line_cells(141, 130, 152, 130)[0])
        self.assertEqual((152, 130), build.line_cells(141, 130, 152, 130)[-1])

    def test_a_line_runs_backwards_too(self):
        self.assertEqual([(5, 9), (4, 9), (3, 9)], build.line_cells(5, 9, 3, 9))

    def test_a_diagonal_is_not_a_line(self):
        self.assertIsNone(build.line_cells(141, 130, 152, 131))

    def test_cells_parse_and_dedupe_in_order(self):
        cells, why = build.parse_cells("141,130; 142,130 ;141,130")
        self.assertIsNone(why)
        self.assertEqual([(141, 130), (142, 130)], cells)

    def test_a_cell_that_is_not_a_cell_is_refused_by_name(self):
        cells, why = build.parse_cells("141,130;banana")
        self.assertIsNone(cells)
        self.assertIn("banana", why)

    def test_the_wire_spelling_is_the_companions(self):
        self.assertEqual("141,130;142,130",
                         build.cells_arg([(141, 130), (142, 130)]))


class BatchIsOneCallTests(unittest.TestCase):
    """The bug: a 64-cell build issued as one call delays every event for its
    duration. One companion call, one watch, one read-back -- whatever N is."""

    def counts(self, cells, argv):
        code, text, fake = run_batch_cli(argv, wall_route(cells))
        place = fake.named("home/place_building")
        reads = fake.named("home/list_buildings")
        return code, text, place, reads

    def test_twelve_cells_are_one_placing_call_and_one_read_back(self):
        cells = wall_line(141, 130, 152)
        code, text, place, reads = self.counts(
            cells, ["Wall", "141", "130", "--to", "152", "130",
                    "--stuff", "BlocksSandstone", "--do"])
        writes = [a for a in place if a.get("dryRun") is False]
        self.assertEqual(1, len(writes))
        self.assertEqual(12, len(writes[0]["cells"].split(";")))
        self.assertEqual(2, len(place))       # the rotatable probe, then the batch
        self.assertEqual(1, len(reads))       # ONE read-back for twelve cells
        self.assertIn("== PLACED -- 12 of 12 cell(s) placed", text)

    def test_sixty_four_cells_cost_the_same_number_of_calls(self):
        cells = wall_line(100, 130, 163)
        code, text, place, reads = self.counts(
            cells, ["Wall", "100", "130", "--to", "163", "130", "--do"])
        self.assertEqual(64, len(cells))
        self.assertEqual(2, len(place))
        self.assertEqual(1, len(reads))
        writes = [a for a in place if a.get("dryRun") is False]
        self.assertEqual(64, len(writes[0]["cells"].split(";")))

    def test_the_batch_write_asks_for_one_rotation_and_one_watch(self):
        cells = wall_line(141, 130, 152)
        _, _, place, _ = self.counts(
            cells, ["Wall", "141", "130", "--to", "152", "130", "--do"])
        write = [a for a in place if a.get("dryRun") is False][0]
        self.assertEqual("north", write["rotation"])
        self.assertIs(True, write["watch"])
        self.assertEqual(141, write["x"])
        self.assertEqual(130, write["z"])

    def test_a_dry_run_batch_places_nothing_and_says_so_first(self):
        cells = wall_line(141, 130, 152)
        code, text, place, reads = self.counts(
            cells, ["Wall", "141", "130", "--to", "152", "130"])
        self.assertEqual("DRY RUN -- nothing placed; add --do", text.splitlines()[0])
        self.assertEqual([], [a for a in place if a.get("dryRun") is False])
        self.assertEqual([], reads)           # nothing was placed, nothing to confirm
        self.assertIn("== DRY RUN -- NOTHING placed (add --do). 12 of 12 cell(s) accepted",
                      text)

    def test_explicit_cells_need_no_coordinates(self):
        cells = [(141, 130), (141, 131)]
        code, text, place, _ = self.counts(
            cells, ["Wall", "--cells", "141,130;141,131", "--do"])
        write = [a for a in place if a.get("dryRun") is False][0]
        self.assertEqual("141,130;141,131", write["cells"])

    def test_one_verdict_line_for_the_whole_batch(self):
        cells = wall_line(141, 130, 152)
        _, text, _, _ = self.counts(
            cells, ["Wall", "141", "130", "--to", "152", "130", "--do"])
        self.assertEqual(1, len([l for l in text.splitlines() if l.startswith("==")]))


class BatchVerdictTests(unittest.TestCase):
    def render(self, cells, outcomes=None, do=True):
        r = batch_reply(cells, outcomes, do=do)
        with mock.patch.object(build, "rim", FakeRim(lambda t, a: listing([]))), \
                mock.patch.object(build.time, "sleep"), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = build.report_batch(r, cells, "Wall", dry=not do, confirm=False)
        return code, out.getvalue()

    def test_a_refused_cell_does_not_stop_the_rest_and_reaches_the_verdict(self):
        cells = wall_line(141, 130, 144)
        code, text = self.render(cells, {(143, 130): "refused"})
        self.assertEqual(1, code)
        self.assertIn("PARTLY PLACED", text)
        self.assertIn("3 of 4 cell(s) placed", text)
        self.assertIn("1 REFUSED", text)
        self.assertIn("REFUSED 143,130 -- Space already occupied.", text)

    def test_every_cell_already_there_is_its_own_verdict(self):
        cells = wall_line(141, 130, 142)
        code, text = self.render(cells, dict(((c, "already_present") for c in cells)))
        self.assertIn("== ALREADY THERE -- all 2 cell(s)", text)
        self.assertNotIn("== PLACED", text)

    def live_rerun(self):
        """Threadneedle, 2026-09-11: the identical 12-cell wall run twice. The
        research bench holds 142-144,130; the other nine cells already carry
        the blueprints the first run made."""
        cells = wall_line(141, 130, 152)
        outcomes = {}
        for c in cells:
            outcomes[c] = "refused" if c[0] in (142, 143, 144) else "already_present"
        return cells, outcomes

    def test_a_rerun_leads_with_what_already_stands_there(self):
        cells, outcomes = self.live_rerun()
        r = batch_reply(cells, outcomes)
        # The companion still calls the mix "refused"; the verdict must not.
        self.assertEqual("refused", r["outcome"])
        line = build.batch_verdict_line(r, cells)
        self.assertTrue(line.startswith(
            "== ALREADY THERE -- 9 of 12 cell(s) already had this blueprint, "
            "0 placed, 3 REFUSED"), line)
        self.assertIn("(first: 142,130 Space already occupied)", line)
        self.assertNotIn("NOTHING was placed", line)

    def test_the_header_word_agrees_with_that_verdict(self):
        cells, outcomes = self.live_rerun()
        code, text = self.render(cells, outcomes)
        head = [l for l in text.splitlines() if l.startswith("wall (Wall)")][0]
        self.assertIn("-- ALREADY THERE", head)
        self.assertNotIn("-- REFUSED", head)
        # Still one verdict, and the cells a builder has to fix are still named.
        self.assertEqual(1, len([l for l in text.splitlines() if l.startswith("==")]))
        self.assertIn("REFUSED 142,130", text)

    def test_the_refused_cells_are_not_buried_by_the_already_there_ones(self):
        cells, outcomes = self.live_rerun()
        code, text = self.render(cells, outcomes)
        named = [l for l in text.splitlines() if l.startswith("    ")]
        self.assertEqual(3, len([l for l in named if "REFUSED" in l]))
        first_already = [i for i, l in enumerate(named) if "ALREADY THERE" in l][0]
        last_refused = [i for i, l in enumerate(named) if "REFUSED" in l][-1]
        self.assertLess(last_refused, first_already)

    def test_a_rerun_with_holes_still_exits_non_zero(self):
        cells, outcomes = self.live_rerun()
        code, text = self.render(cells, outcomes)
        self.assertEqual(1, code)

    def test_nothing_of_ours_there_keeps_the_old_refusal(self):
        """`REFUSED -- NOTHING was placed` is reserved for exactly this."""
        cells = wall_line(141, 130, 143)
        code, text = self.render(cells, dict(((c, "refused") for c in cells)))
        self.assertIn("== REFUSED -- NOTHING was placed", text)
        self.assertNotIn("ALREADY THERE", text)

    def test_every_cell_refused_says_nothing_was_placed(self):
        cells = wall_line(141, 130, 142)
        code, text = self.render(cells, dict(((c, "refused") for c in cells)))
        self.assertEqual(1, code)
        self.assertIn("== REFUSED -- NOTHING was placed", text)
        self.assertIn("First refusal (141,130)", text)

    def test_the_ids_of_a_whole_line_are_on_the_verdict(self):
        cells = wall_line(141, 130, 144)
        code, text = self.render(cells)
        self.assertEqual(0, code)
        self.assertIn("blueprint ids 40700..40703 (4)", text)

    def test_materials_are_costed_for_every_cell(self):
        cells = wall_line(141, 130, 152)
        code, text = self.render(cells, do=False)
        self.assertIn("materials are costed for all 12 cells", text)
        self.assertIn("sandstone blocks 5 per cell", text)   # one wall, said out loud
        self.assertIn("sandstone blocks 60 of 600", text)    # the whole line

    def test_a_crop_under_an_accepted_cell_reaches_the_verdict_line(self):
        cells = wall_line(141, 130, 142)
        r = batch_reply(cells, blockers=[blocker("rice plant", effectOnPlace="crop")])
        self.assertIn("would wipe/replace: rice plant",
                      build.batch_verdict_line(r, cells))

    def test_sixty_four_ordinary_cells_do_not_print_sixty_four_lines(self):
        cells = wall_line(100, 130, 163)
        code, text = self.render(cells)
        self.assertLess(len(text.splitlines()), 12)
        self.assertIn("64 of 64 cell(s) placed", text)


class BatchConfirmationTests(unittest.TestCase):
    """The day-68 three sentences, batched: one read covers every cell."""

    def confirm(self, placed, route):
        fake = FakeRim(route)
        with mock.patch.object(build, "rim", fake), \
                mock.patch.object(build.time, "sleep"), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = build.confirm_batch(placed)
        return code, out.getvalue(), fake

    def placed(self, cells):
        return [(x, z, 40700 + i, "wall") for i, (x, z) in enumerate(cells)]

    def test_confirmed_in_one_read_for_the_whole_batch(self):
        cells = wall_line(141, 130, 152)
        rows = [bp_row(x, z, "Blueprint_Wall%d" % (40700 + i))
                for i, (x, z) in enumerate(cells)]
        code, text, fake = self.confirm(self.placed(cells),
                                        lambda t, a: listing(rows))
        self.assertEqual(0, code)
        self.assertEqual(1, len(fake.named("home/list_buildings")))
        self.assertIn("PLACED -- CONFIRMED: 12 of 12 blueprints", text)
        self.assertIn("read back in ONE call on read 1 of 4", text)
        self.assertIn("buildings.py --pending", text)

    def test_the_id_is_matched_not_merely_the_cell(self):
        """A wall that was already standing is not this call's blueprint."""
        cells = [(141, 130)]
        other = {"thingId": "Wall9", "defName": "Wall", "label": "wooden wall",
                 "position": {"x": 141, "z": 130}, "isBlueprint": False,
                 "isFrame": False}
        code, text, _ = self.confirm(self.placed(cells),
                                     lambda t, a: listing([other]))
        self.assertEqual(1, code)
        self.assertIn("NOT PLACED -- SOMETHING ELSE IS THERE", text)
        self.assertIn("141,130 holds wooden wall", text)
        self.assertNotIn("reads empty", text)

    def test_an_empty_cell_is_not_visible_yet_never_empty(self):
        cells = [(141, 130)]
        code, text, fake = self.confirm(self.placed(cells),
                                        lambda t, a: listing([]))
        self.assertEqual(1, code)
        self.assertIn("NOT VISIBLE YET", text)
        self.assertIn("'not seen', NOT 'the cells are empty'", text)
        self.assertIn("buildings.py --near", text)
        # Four reads, not four reads per cell.
        self.assertEqual(build.CONFIRM_READS, len(fake.named("home/list_buildings")))

    def test_polling_stops_as_soon_as_the_batch_is_whole(self):
        cells = wall_line(141, 130, 142)
        rows = [bp_row(x, z, "Blueprint_Wall%d" % (40700 + i))
                for i, (x, z) in enumerate(cells)]
        state = {"n": 0}

        def route(tool, args):
            state["n"] += 1
            return listing([] if state["n"] == 1 else rows)

        code, text, fake = self.confirm(self.placed(cells), route)
        self.assertEqual(0, code)
        self.assertEqual(2, len(fake.named("home/list_buildings")))
        self.assertIn("on read 2 of 4", text)

    def test_one_read_covers_every_cell_of_the_line(self):
        cells = wall_line(141, 130, 152)
        rows = [bp_row(x, z, "Blueprint_Wall%d" % (40700 + i))
                for i, (x, z) in enumerate(cells)]
        code, text, fake = self.confirm(self.placed(cells),
                                        lambda t, a: listing(rows))
        args = fake.named("home/list_buildings")[0]
        self.assertEqual(146, args["x"])
        self.assertEqual(130, args["z"])
        self.assertGreaterEqual(args["radius"], 6)
        self.assertEqual("all", args["status"])


class BatchRefusalTests(unittest.TestCase):
    def refuse(self, argv):
        fake = FakeRim(lambda t, a: {"success": True})
        with mock.patch.object(build, "rim", fake), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = build.main(argv)
        return code, out.getvalue(), fake

    def test_a_diagonal_line_is_refused_and_names_cells(self):
        code, text, fake = self.refuse(["Wall", "141", "130", "--to", "152", "131",
                                        "--do"])
        self.assertEqual(1, code)
        self.assertIn("must be straight", text)
        self.assertIn("--cells", text)
        self.assertIn("== NOTHING WAS PLACED.", text)
        self.assertEqual([], fake.named("home/place_building"))

    def test_to_and_cells_together_are_refused(self):
        code, text, fake = self.refuse(["Wall", "141", "130", "--to", "152", "130",
                                        "--cells", "1,2", "--do"])
        self.assertEqual(1, code)
        self.assertIn("pass one", text)
        self.assertEqual([], fake.named("home/place_building"))

    def test_to_without_an_anchor_cell_is_refused(self):
        code, text, fake = self.refuse(["Wall", "--to", "152", "130", "--do"])
        self.assertEqual(1, code)
        self.assertIn("--to needs the cell the line starts at", text)

    def test_a_batch_over_the_cap_is_refused_with_the_cap(self):
        code, text, fake = self.refuse(["Wall", "1", "130", "--to", "%d" % (build.MAX_BATCH + 5),
                                        "130", "--do"])
        self.assertEqual(1, code)
        self.assertIn("%d-cell cap" % build.MAX_BATCH, text)
        self.assertEqual([], fake.named("home/place_building"))

    def test_a_rotatable_def_refuses_the_whole_batch_before_placing(self):
        cells = wall_line(141, 130, 152)
        code, text, fake = run_batch_cli(
            ["Cooler", "141", "130", "--to", "152", "130", "--do"],
            wall_route(cells, rotatable=True))
        self.assertEqual(1, code)
        self.assertIn("batch of 12 cells needs ONE rotation", text)
        self.assertIn('--cells "141,130;142,130', text)
        self.assertEqual([], [a for a in fake.named("home/place_building")
                              if a.get("dryRun") is False])

    def test_a_reply_that_is_not_a_payload_is_said_as_itself(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = build.report_batch("truncated json", [(1, 2)], "Wall")
        self.assertEqual(1, code)
        self.assertIn("did not return a payload", out.getvalue())
        self.assertIn("== NOTHING WAS PLACED.", out.getvalue())

    def test_the_rotation_advice_keeps_the_material_that_was_asked_for(self):
        """needs_rotation tells the caller the exact command to re-run. It
        dropped --stuff, so the advice built from GenStuff.DefaultStuffFor
        instead of the material the caller named."""
        cells = wall_line(141, 130, 152)
        code, text, _ = run_batch_cli(
            ["Cooler", "141", "130", "--to", "152", "130", "--stuff", "Steel",
             "--do"],
            wall_route(cells, rotatable=True))
        self.assertEqual(1, code)
        self.assertIn("== NOTHING WAS PLACED.", text)
        self.assertIn("--stuff Steel", text)

    def test_a_read_back_that_cannot_confirm_exits_non_zero(self):
        """PLAYBOOK: the batch "exits non-zero while any cell is missing".
        confirm_batch computes exactly that code ("-> 0 only when every
        blueprint is confirmed") and main threw it away, so a batch the tool
        claimed and the read-back could not find exited 0."""
        cells = wall_line(141, 130, 144)
        code, text, _ = run_batch_cli(
            ["Wall", "141", "130", "--to", "144", "130", "north", "--do"],
            wall_route(cells, confirmed=False))
        self.assertIn("NOT VISIBLE YET", text)
        self.assertEqual(1, code)

    def test_a_batch_do_survives_a_non_payload_reply_all_the_way_out(self):
        """`report_batch` names a non-payload reply and returns 1 -- and then
        `watch_line(r)` read `watch` off the same string one line later. The
        guard two functions up is worth nothing if the crash is after it."""
        cells = wall_line(141, 130, 142)

        def route(tool, args):
            if tool == "home/list_buildings":
                return listing([])
            if args.get("cells") and args.get("dryRun") is False:
                return "<html>504 Gateway Timeout</html>"
            return wall_route(cells)(tool, args)

        code, text, _ = run_batch_cli(
            ["Wall", "141", "130", "--to", "142", "130", "north", "--do"], route)
        self.assertEqual(1, code)
        self.assertIn("did not return a payload", text)
        self.assertIn("== NOTHING WAS PLACED.", text)

    def test_an_old_dll_with_no_batch_block_is_named_not_assumed(self):
        cells = wall_line(141, 130, 142)

        def route(tool, args):
            if args.get("cells"):
                return {"success": True, "tool": "home/place_building",
                        "dryRun": False, "outcome": "placed",
                        "def": {"defName": "Wall"}, "rotations": []}
            return wall_route(cells)(tool, args)

        code, text, fake = run_batch_cli(
            ["Wall", "141", "130", "--to", "142", "130", "--do"], route)
        self.assertEqual(1, code)
        self.assertIn("predates batching", text)
        self.assertIn("== NOTHING WAS PLACED.", text)


if __name__ == "__main__":
    unittest.main()
