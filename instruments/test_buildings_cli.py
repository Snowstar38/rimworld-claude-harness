import contextlib
import io
from pathlib import Path
import sys
import unittest
import unittest.mock
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import buildings
import bills


class PendingOutputTests(unittest.TestCase):
    @staticmethod
    def full_reply(status="pending"):
        return {
            "filters": {"status": status, "category": "artificial", "match": None,
                        "playerOnly": True, "aggregate": True, "radius": 0,
                        "inspect": False},
            "skipped": {"byStatus": 497, "byMatch": 0, "byRadius": 0,
                        "byPlayerOnly": 0},
            "buildings": [], "aggregated": [], "resourceDeficit": [],
            "attention": {"blueprints": 0, "frames": 0,
                          "pendingMissingResources": 0, "unpowered": 0,
                          "outOfFuel": 0, "brokenDown": 0, "finishedBills": 0,
                          "billGiversWithNoBills": 0},
            "counts": {"scanned": 497, "detailed": 0,
                       "aggregatedBuildings": 0, "aggregatedRows": 0},
            "notes": {},
        }

    def test_pending_view_does_not_treat_built_rows_as_filtered_pending(self):
        reply = {
            "filters": {"status": "pending"},
            "skipped": {"byStatus": 475, "byMatch": 0, "byRadius": 0,
                        "byPlayerOnly": 0},
            "buildings": [],
        }
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            buildings.pending_block(reply)
        self.assertIn("nothing was filtered out, the map has none", out.getvalue())
        self.assertNotIn("none matched", out.getvalue())

    def test_pending_view_reports_filters_that_can_remove_pending_rows(self):
        reply = {
            "filters": {"status": "pending"},
            "skipped": {"byStatus": 475, "byMatch": 3, "byRadius": 0,
                        "byPlayerOnly": 0},
            "buildings": [],
        }
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            buildings.pending_block(reply)
        self.assertIn("none matched", out.getvalue())
        self.assertIn("byMatch 3", out.getvalue())
        self.assertNotIn("475", out.getvalue())

    def test_pending_show_omits_every_built_only_section(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            buildings.show(self.full_reply())
        text = out.getvalue()
        self.assertIn("PENDING CONSTRUCTION", text)
        self.assertIn("RESOURCES STILL NEEDED", text)
        self.assertNotIn("WORKTABLES", text)
        self.assertNotIn("POWER", text)
        self.assertNotIn("BUILT (aggregated)", text)

    def test_pending_footer_does_not_claim_status_filtered_buildings_are_ours(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            buildings.footer(self.full_reply())
        text = out.getvalue()
        self.assertIn("were not faction-checked", text)
        self.assertNotIn("every building scanned is ours", text)
        self.assertIn("nothing flagged in status=pending", text)

    def test_site_have_is_named_delivered_and_colony_stock_is_separate(self):
        reply = self.full_reply()
        reply["buildings"] = [{"isBlueprint": True, "isFrame": False,
                               "resourcesComplete": False, "status": "blueprint",
                               "buildLabel": "wooden wall", "position": {"x": 1, "z": 2},
                               "workLeft": 5, "resources": [{"defName": "WoodLog",
                                                               "label": "wood", "have": 0,
                                                               "need": 5, "stillNeeded": 5}]}]
        reply["attention"]["blueprints"] = 1
        reply["resourceDeficit"] = [{"defName": "WoodLog", "countedAsResource": 259}]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            buildings.pending_block(reply)
        self.assertIn("delivered 0 / need 5", out.getvalue())
        # Same fact, new words: with 259 in the colony this site is queued, not
        # starved, and an old DLL's countedAsResource is the only stock number
        # there is to fall back on.
        self.assertIn("259 available in colony", out.getvalue())
        self.assertNotIn("have 0 / need", out.getvalue())

    def test_companion_does_not_turn_unreadable_bill_stack_into_empty(self):
        source = (Path(__file__).resolve().parents[1] / "companion" / "src" /
                  "ListBuildingsTool.cs").read_text(encoding="utf-8")
        self.assertIn('row["billStackUnreadable"] = billStackUnreadable', source)
        self.assertIn("if (!billStackUnreadable)", source)
        self.assertIn("billStackUnreadable ? null", source)
        self.assertIn("ThingRequestGroup.PotentialBillGiver", source)


class BuildingConfigCliTests(unittest.TestCase):
    def test_temperature_is_parsed_as_celsius_number(self):
        thing, fields = buildings._config_argv(
            ["109,134", "--temperature", "-9.5"])
        self.assertEqual("109,134", thing)
        self.assertEqual({"temperature": -9.5}, fields)

    def test_temperature_rejects_non_number(self):
        thing, error = buildings._config_argv(
            ["Cooler123", "--temperature", "cold"])
        self.assertIsNone(thing)
        self.assertIn("takes a number", error)


def deficit(**kw):
    row = {"defName": "Steel", "label": "steel", "stillNeeded": 20, "sites": 4,
           "onMapTotal": 359, "unforbiddenOnMap": 248, "countedAsResource": 0}
    row.update(kw)
    return row


def site(**kw):
    row = {"status": "blueprint", "isBlueprint": True, "isFrame": False,
           "buildLabel": "sandstone floor", "buildDefName": "SandstoneFloor",
           "position": {"x": 10, "z": 12}, "percentComplete": 0.0,
           "workLeft": 100, "resourcesComplete": False,
           "materialCostUnreadable": False, "isInstallBlueprint": False,
           "resources": [{"defName": "Steel", "label": "steel", "have": 0,
                          "need": 5, "stillNeeded": 5}]}
    row.update(kw)
    return row


def pending_reply(sites, deficits):
    r = PendingOutputTests.full_reply()
    r["buildings"] = sites
    r["resourceDeficit"] = deficits
    r["attention"]["blueprints"] = len(sites)
    return r


def render(reply, block):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        block(reply)
    return out.getvalue()


class ShortVsUndeliveredTests(unittest.TestCase):
    """An undelivered blueprint is a queue, not a shortage."""

    def test_stock_in_hand_is_not_short_it_is_undelivered(self):
        text = render(pending_reply([site()], [deficit()]), buildings.pending_block)
        self.assertIn("not yet delivered (248 available in colony)", text)
        self.assertNotIn("!! SHORT", text)
        self.assertIn("   waiting", text)

    def test_short_is_printed_when_the_colony_really_cannot_supply_it(self):
        text = render(
            pending_reply([site()],
                          [deficit(stillNeeded=900, unforbiddenOnMap=3)]),
            buildings.pending_block)
        self.assertIn("!! SHORT", text)
        self.assertIn("colony has 3", text)

    def test_storage_only_count_no_longer_decides_short(self):
        # countedAsResource is 0 for 248 loose steel; it must not drive SHORT.
        text = render(pending_reply([site()], [deficit(countedAsResource=0)]),
                      buildings.pending_block)
        self.assertNotIn("!! SHORT", text)

    def test_unknown_stock_keeps_the_loud_word(self):
        text = render(
            pending_reply([site()],
                          [deficit(unforbiddenOnMap=None, onMapTotal=None,
                                   countedAsResource=None)]),
            buildings.pending_block)
        self.assertIn("!! SHORT", text)

    def test_a_delivered_site_is_still_ok(self):
        done = site(resourcesComplete=True,
                    resources=[{"defName": "Steel", "label": "steel",
                                "have": 5, "need": 5, "stillNeeded": 0}])
        text = render(pending_reply([done], []), buildings.pending_block)
        self.assertIn("   ok    ", text)
        self.assertIn("(delivered)", text)

    def test_rolled_up_block_prints_all_three_availability_numbers(self):
        text = render(pending_reply([site()], [deficit()]), buildings.deficit_block)
        self.assertIn("usable 248 / on map 359 / in storage 0", text)
        self.assertNotIn("*** SHORT BY", text)

    def test_rolled_up_block_flags_a_real_shortage(self):
        text = render(
            pending_reply([site()], [deficit(stillNeeded=900, unforbiddenOnMap=3)]),
            buildings.deficit_block)
        self.assertIn("*** SHORT BY 897 ***", text)

    def test_companion_emits_the_unforbidden_total(self):
        source = (Path(__file__).resolve().parents[1] / "companion" / "src" /
                  "ListBuildingsTool.cs").read_text(encoding="utf-8")
        self.assertIn('{ "unforbiddenOnMap", UnforbiddenOnMap(map) }', source)
        self.assertIn("GetComp<CompForbiddable>()", source)


class InstallBlueprintTests(unittest.TestCase):
    def test_a_reinstall_names_what_it_is_moving(self):
        install = site(isInstallBlueprint=True, buildLabel=None,
                       buildDefName=None, installOfDefName="Turret_MiniTurret",
                       installOfLabel="mini-turret", resources=[],
                       resourcesComplete=True)
        text = render(pending_reply([install], []), buildings.pending_block)
        self.assertIn("reinstall of mini-turret", text)
        self.assertIn("a reinstall costs no materials", text)

    def test_a_reinstall_falls_back_to_the_defname(self):
        install = site(isInstallBlueprint=True, buildLabel=None,
                       buildDefName=None, installOfLabel=None,
                       installOfDefName="Turret_MiniTurret", resources=[],
                       resourcesComplete=True)
        text = render(pending_reply([install], []), buildings.pending_block)
        self.assertIn("reinstall of Turret_MiniTurret", text)

    def test_companion_reads_the_install_target_off_the_instance(self):
        source = (Path(__file__).resolve().parents[1] / "companion" / "src" /
                  "ListBuildingsTool.cs").read_text(encoding="utf-8")
        self.assertIn("MiniToInstallOrBuildingToReinstall", source)
        self.assertIn('row["installOfDefName"]', source)


class SelectionNoiseTests(unittest.TestCase):
    def test_a_named_thing_does_not_report_other_sections_as_none_matched(self):
        r = PendingOutputTests.full_reply(status="all")
        r["filters"]["match"] = "bench"
        r["counts"]["detailed"] = 1
        r["skipped"]["byMatch"] = 4700
        text = render(r, buildings.bills_block)
        self.assertIn("nothing matching 'bench' belongs in this section", text)
        self.assertNotIn("none matched", text)

    def test_an_unmatched_word_still_says_the_filter_removed_things(self):
        r = PendingOutputTests.full_reply(status="all")
        r["filters"]["match"] = "nosuchthing"
        r["skipped"]["byMatch"] = 4700
        text = render(r, buildings.bills_block)
        self.assertIn("none matched", text)
        self.assertIn("byMatch 4700", text)


class GizmoOneProcessTests(unittest.TestCase):
    """The ids execute_gizmo takes die with the selection that made them."""

    @staticmethod
    def _thing():
        return {"success": True, "gizmoCount": 2,
                "thing": {"label": "mini-turret", "defName": "Turret_MiniTurret",
                          "thingId": "Turret_MiniTurret1",
                          "position": {"x": 5, "z": 6}},
                "after": {}}

    def _run(self, label, do=False, gizmos=None, calls=None,
             selects="Turret_MiniTurret1", configure=None):
        rows = gizmos if gizmos is not None else [
            {"id": "g1", "label": "Uninstall", "disabled": False},
            {"id": "g2", "label": "Reinstall at...", "disabled": False}]
        # What the click actually selects. Turn 14: 12 plainleather lying on the
        # turret's tile, so the click selected the leather and the bar it
        # listed was the leather's.
        picked = selects if isinstance(selects, list) else [selects]
        seq = iter(picked + [picked[-1]] * 32)

        def fake_game(tool, params=None, **kw):
            (calls if calls is not None else []).append((tool, params))
            if tool == "rimworld/list_selected_gizmos":
                return {"success": True, "gizmos": rows}
            if tool == "rimworld/get_selection_semantics":
                got = next(seq)
                if got is None:
                    return {"success": True, "hasSelection": False,
                            "selectedCount": 0, "selectedObjects": []}
                return {"success": True, "hasSelection": True,
                        "selectedCount": 1,
                        "selectedObjects": [{"id": got, "kind": "thing",
                                             "label": got}]}
            return {"success": True}

        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
             unittest.mock.patch.object(buildings, "configure",
                                        return_value=configure or self._thing()), \
             unittest.mock.patch.object(buildings.pick.time, "sleep"), \
             unittest.mock.patch.object(buildings.rim, "game", fake_game):
            rc = buildings.gizmo_cmd("Turret_MiniTurret1", label, do=do)
        return rc, out.getvalue()

    def test_dry_run_selects_lists_and_fires_nothing(self):
        calls = []
        rc, text = self._run("Uninstall", calls=calls)
        self.assertEqual(0, rc)
        self.assertIn("WOULD FIRE 'Uninstall'", text)
        self.assertIn("DRY RUN", text)
        tools = [t for t, _ in calls]
        self.assertIn("rimworld/clear_selection", tools)
        self.assertIn("rimworld/click_cell", tools)
        self.assertIn("rimworld/list_selected_gizmos", tools)
        self.assertNotIn("rimworld/execute_gizmo", tools)

    def test_do_fires_the_gizmo_in_the_same_process(self):
        calls = []
        rc, text = self._run("Uninstall", do=True, calls=calls)
        self.assertEqual(0, rc)
        self.assertIn("FIRED 'Uninstall'", text)
        self.assertIn(("rimworld/execute_gizmo", {"gizmoId": "g1"}), calls)

    def test_selection_is_cleared_before_the_click(self):
        calls = []
        self._run("Uninstall", calls=calls)
        tools = [t for t, _ in calls]
        self.assertLess(tools.index("rimworld/clear_selection"),
                        tools.index("rimworld/click_cell"))

    def test_an_ambiguous_label_refuses_and_fires_nothing(self):
        calls = []
        rc, text = self._run("in", do=True, calls=calls)
        self.assertEqual(1, rc)
        self.assertIn("matched 2 enabled gizmo(s)", text)
        self.assertNotIn("rimworld/execute_gizmo", [t for t, _ in calls])

    def test_a_disabled_gizmo_is_not_fired(self):
        rows = [{"id": "g1", "label": "Uninstall", "disabled": True,
                 "disabledReason": "being deconstructed"}]
        rc, text = self._run("Uninstall", do=True, gizmos=rows)
        self.assertEqual(1, rc)
        self.assertIn("DISABLED: being deconstructed", text)

    def test_an_empty_bar_is_a_loud_failure_not_a_silent_zero(self):
        rc, text = self._run("Uninstall", do=True, gizmos=[])
        self.assertEqual(1, rc)
        self.assertIn("NO gizmos", text)

    def test_an_item_on_the_tile_never_fires_and_says_what_was_selected(self):
        """Turn 14: 12 plainleather on the turret's tile hijacked the click."""
        calls = []
        rc, text = self._run("Uninstall", do=True, calls=calls,
                             selects="Thing_Leather_Plain900")
        self.assertEqual(1, rc)
        self.assertIn("CLICK MISSED", text)
        self.assertIn("Thing_Leather_Plain900", text)
        self.assertIn("Turret_MiniTurret1", text)
        self.assertNotIn("rimworld/execute_gizmo", [t for t, _ in calls])

    def test_cycling_the_stack_reaches_the_building_and_fires(self):
        calls = []
        rc, text = self._run("Uninstall", do=True, calls=calls,
                             selects=["Thing_Leather_Plain900",
                                      "Turret_MiniTurret1"])
        self.assertEqual(0, rc)
        self.assertIn("selected: thing Turret_MiniTurret1", text)
        self.assertIn(("rimworld/execute_gizmo", {"gizmoId": "g1"}), calls)

    def test_the_read_and_write_bars_disagreeing_is_printed(self):
        rc, text = self._run("Uninstall", do=False)
        self.assertEqual(0, rc)
        self.assertNotIn("counted", text)
        rc, text = self._run("Uninstall", do=False, gizmos=[
            {"id": "g1", "label": "Uninstall", "disabled": False}])
        self.assertIn("counted 2 gizmo(s) and the selection carries only 1", text)

    def test_a_minified_thing_is_accepted_not_refused(self):
        """`gizmo`/`gizmos` used to answer 'No colony building matches'."""
        refusal = {"success": False, "error": "No colony building matches "
                   "\"Thing_MinifiedThing42\"."}
        target = {"success": True, "target": {
            "thingId": "Thing_MinifiedThing42", "label": "mini-turret (packed)",
            "defName": "MinifiedThing", "className": "RimWorld.MinifiedThing",
            "spawned": True, "position": {"x": 7, "z": 8}}}

        def fake_game(tool, params=None, **kw):
            if tool == "rimworld/get_map_target_info":
                return target
            if tool == "rimworld/list_selected_gizmos":
                return {"success": True,
                        "gizmos": [{"id": "g9", "label": "Install"}]}
            if tool == "rimworld/get_selection_semantics":
                return {"success": True, "hasSelection": True, "selectedCount": 1,
                        "selectedObjects": [{"id": "Thing_MinifiedThing42",
                                             "kind": "thing", "label": "packed"}]}
            return {"success": True}

        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
             unittest.mock.patch.object(buildings, "configure",
                                        return_value=refusal), \
             unittest.mock.patch.object(buildings.pick.time, "sleep"), \
             unittest.mock.patch.object(buildings.rim, "game", fake_game):
            rc = buildings.gizmos_cmd("Thing_MinifiedThing42")
        text = out.getvalue()
        self.assertEqual(0, rc)
        self.assertNotIn("No colony building matches", text)
        self.assertIn("mini_install.py", text)
        self.assertIn("Install", text)


def net(index=0, **kw):
    row = {"index": index, "transmitterCount": 4, "connectorCount": 2,
           "producerCount": 1, "consumerCount": 2, "batteryCount": 0,
           "playerBuildingCount": 3, "buildingCount": 3,
           "generationW": 1700.0, "consumptionW": 200.0, "netW": 1500.0,
           "storedWd": 0.0, "storedMaxWd": 0.0, "hasPowerSource": True,
           "hasActivePowerSource": True, "flags": [], "buildings": [],
           "buildingsNotListed": 0}
    row.update(kw)
    return row


def power_reply(nets, flagged=0):
    r = PendingOutputTests.full_reply(status="all")
    r["powerNets"] = nets
    r["powerSummary"] = {"netCount": len(nets), "flaggedNetCount": flagged,
                         "readable": True, "error": None,
                         "flags": {"noProducer": flagged, "noConsumer": 0,
                                   "isolatedBattery": flagged,
                                   "isolatedTransmitter": 0}}
    return r


ORPHAN = net(
    index=2, producerCount=0, consumerCount=0, batteryCount=2,
    generationW=0.0, consumptionW=0.0, netW=0.0, storedWd=413.5,
    storedMaxWd=1200.0, hasActivePowerSource=False,
    flags=["noProducer", "noConsumer", "isolatedBattery"],
    buildings=[{"thingId": "Battery1", "defName": "Battery", "label": "battery",
                "position": {"x": 130, "z": 150}, "role": "battery",
                "faction": "Player", "powerOutputW": None, "storedWd": 200.0},
               {"thingId": "Battery2", "defName": "Battery", "label": "battery",
                "position": {"x": 131, "z": 150}, "role": "battery",
                "faction": "Player", "powerOutputW": None, "storedWd": 213.5}])


class OrphanedBatteryTests(unittest.TestCase):
    """turns 37-39: `notConnectedToPower=0` cannot see an orphaned battery.

    A battery has no power draw, so it is never "unpowered" -- three turns of
    clean per-building power checks passed while the hill pocket was cut off.
    The grid read is the one that can see it, and the DEFAULT summary has to
    carry the warning or the same clean-looking check happens again."""

    def test_the_default_summary_warns_and_names_the_batteries(self):
        text = render(power_reply([net(), ORPHAN], flagged=1),
                      buildings.power_block)
        self.assertIn("POWER NET 2", text)
        self.assertIn("NO GENERATOR on this net", text)
        self.assertIn("battery at 130,150", text)
        self.assertIn("battery at 131,150", text)

    def test_a_clean_grid_says_it_checked_rather_than_saying_nothing(self):
        text = render(power_reply([net()]), buildings.power_block)
        self.assertIn("1 net(s), none flagged", text)
        self.assertIn("(checked", text)

    def test_an_older_dll_admits_it_cannot_see_this_at_all(self):
        reply = PendingOutputTests.full_reply(status="all")
        text = render(reply, buildings.power_block)
        self.assertIn("ORPHANED BATTERY CANNOT BE SEEN", text)

    def test_an_unreadable_grid_is_not_a_clean_grid(self):
        reply = power_reply([])
        reply["powerSummary"] = {"netCount": 0, "flaggedNetCount": 0,
                                 "readable": False, "error": "boom", "flags": {}}
        text = render(reply, buildings.power_block)
        self.assertIn("POWER GRID UNREADABLE", text)
        self.assertIn("NOT 'the grid is fine'", text)

    def test_the_full_table_prints_every_net_with_its_flags(self):
        text = render(power_reply([net(), ORPHAN], flagged=1),
                      buildings.power_nets_block)
        self.assertIn("2 net(s), 1 flagged", text)
        self.assertIn("isolatedBattery", text)
        self.assertIn("net 0", text)


class TargetAddressTests(unittest.TestCase):
    """turns 34/39: a coordinate was read as a --match SUBSTRING, so misuse
    came back as the full "none matched, 637 removed by filters" block; and
    `Shelf@115,154` missed a two-cell shelf whose anchor is 114,154 while
    `buildings.py gizmos` on the same string resolved it instantly."""

    SHELF = {"thingId": "Shelf99", "defName": "Shelf", "label": "shelf",
             "position": {"x": 114, "z": 154}, "status": "built",
             "rotation": "East", "stuff": "wood", "reasons": [],
             "occupies": {"minX": 114, "minZ": 154, "maxX": 115, "maxZ": 154,
                          "width": 2, "height": 1}}

    def test_a_bare_cell_is_an_address_not_a_substring(self):
        self.assertEqual(buildings.parse_target("115,154"),
                         {"kind": "cell", "x": 115, "z": 154, "match": None,
                          "spec": "115,154"})
        self.assertIsNone(buildings.parse_target("Shelf"))

    def test_def_at_cell_and_a_bare_id_are_addresses_too(self):
        self.assertEqual(buildings.parse_target("Shelf@115,154")["match"], "Shelf")
        self.assertEqual(buildings.parse_target("1234"),
                         {"kind": "thing", "id": "1234", "spec": "1234"})
        self.assertEqual(buildings.parse_target("Thing_Shelf99")["kind"], "thing")

    def test_a_malformed_at_form_is_an_argument_error_not_a_search(self):
        bad = buildings.parse_target("Shelf@nowhere")
        self.assertEqual(bad["kind"], "bad")
        self.assertIn("is not a cell", bad["why"])

    def test_any_occupied_cell_matches_the_way_the_gizmo_resolver_does(self):
        reply = {"buildings": [self.SHELF]}
        for spec in ("115,154", "114,154", "Shelf@115,154"):
            rows = buildings.target_rows(reply, buildings.parse_target(spec))
            self.assertEqual([b["thingId"] for b in rows], ["Shelf99"], spec)

    def test_an_id_matches_the_bare_number_build_py_prints(self):
        reply = {"buildings": [self.SHELF]}
        for spec in ("Shelf99", "Thing_Shelf99", "99"):
            rows = buildings.target_rows(reply, buildings.parse_target(spec)
                                         or {"kind": "thing", "id": spec,
                                             "spec": spec})
            self.assertEqual([b["thingId"] for b in rows], ["Shelf99"], spec)

    def test_an_address_that_finds_nothing_says_so_as_an_address(self):
        reply = power_reply([])
        reply["buildings"] = []
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = buildings.target_block(reply,
                                          buildings.parse_target("115,154"), [])
        text = out.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("NOTHING AT cell 115,154", text)
        self.assertIn("not a filter that hid something", text)
        self.assertNotIn("removed by filters", text)

    def test_a_hit_prints_the_footprint_and_the_handle(self):
        reply = power_reply([])
        reply["buildings"] = [self.SHELF]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = buildings.target_block(reply,
                                          buildings.parse_target("115,154"),
                                          [self.SHELF])
        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("AT cell 115,154 -- 1 building", text)
        self.assertIn("occupies 114,154..115,154", text)
        self.assertIn("thingId Shelf99", text)


class EveryOrderingTests(unittest.TestCase):
    """turn: `--inspect "Shelf" --every` printed its detail rows ABOVE the
    `BUILT (aggregated)` header, so grepping from that header found nothing."""

    def _reply(self, aggregate):
        r = power_reply([])
        r["filters"]["aggregate"] = aggregate
        r["filters"]["status"] = "built"
        r["buildings"] = [{"defName": "Shelf", "label": "shelf",
                           "position": {"x": 114, "z": 154}, "status": "built",
                           "reasons": ["bed"], "thingId": "Shelf99"}]
        return r

    def test_with_every_the_header_comes_before_the_rows(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            buildings.show(self._reply(False))
        text = out.getvalue()
        self.assertLess(text.index("BUILT (aggregated)"),
                        text.index("OTHER DETAILED ROWS"))

    def test_with_aggregation_on_the_table_still_comes_last(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            buildings.show(self._reply(True))
        text = out.getvalue()
        self.assertLess(text.index("OTHER DETAILED ROWS"),
                        text.index("BUILT (aggregated)"))


class ReinstallTests(unittest.TestCase):
    """A shelf has no `Uninstall` gizmo, only `Reinstall at...`, which arms a
    placement designator that needs a click at the DESTINATION."""

    THING = {"thingId": "Shelf99", "defName": "Shelf", "label": "shelf",
             "position": {"x": 114, "z": 154}}

    def _reply(self, gizmos):
        return {"success": True, "thing": self.THING, "gizmos": gizmos}

    def test_a_dry_run_fires_nothing_and_sends_no_click(self):
        with unittest.mock.patch.object(
                buildings, "configure",
                return_value=self._reply([{"label": "Reinstall at...",
                                           "disabled": False}])), \
             unittest.mock.patch.object(buildings.rim, "game") as game, \
             unittest.mock.patch.object(buildings.pick, "select_thing") as sel:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = buildings.reinstall_cmd("Shelf99", 120, 150, do=False)
        self.assertEqual(code, 0)
        self.assertIn("DRY RUN", out.getvalue())
        game.assert_not_called()
        sel.assert_not_called()

    def test_a_thing_with_no_reinstall_gizmo_is_refused_with_its_bar(self):
        with unittest.mock.patch.object(
                buildings, "configure",
                return_value=self._reply([{"label": "Deconstruct"}])):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = buildings.reinstall_cmd("Shelf99", 120, 150, do=True)
        self.assertEqual(code, 1)
        self.assertIn("no `Reinstall at...` gizmo", out.getvalue())
        self.assertIn("Deconstruct", out.getvalue())


class BillsCliTests(unittest.TestCase):
    def test_only_is_forwarded_as_whitelist(self):
        self.assertEqual("ChunkSlate",
                         bills._options(["--only", "ChunkSlate"])["only"])

    def test_only_value_is_not_a_positional(self):
        argv = ["set", "TableStonecutter123", "0", "--only", "ChunkSlate"]
        self.assertEqual(["set", "TableStonecutter123", "0"],
                         bills._positionals(argv))

    def test_companion_change_summary_compares_filter_readback(self):
        source = (Path(__file__).resolve().parents[1] / "companion" / "src" /
                  "BillsTool.cs").read_text(encoding="utf-8")
        self.assertIn('beforeBill.ContainsKey("filter")', source)
        self.assertIn('changed.Add("ingredientFilter "', source)
        common = (Path(__file__).resolve().parents[1] / "companion" / "src" /
                  "BillCommon.cs").read_text(encoding="utf-8")
        self.assertIn('{ "allowedDefNames", new List<object>() }', common)



class PlacementConfirmationTests(unittest.TestCase):
    """WEIRD 12/17/35/43/59, nine sightings: `the placing click left NO
    blueprint there (the cell reads empty)` was printed while the blueprint was
    there, and the identical sentence was true for a neighbouring cell."""

    BP = {"thingId": "Thing_Blueprint7", "defName": "Blueprint_Wall",
          "label": "wall (blueprint)", "isBlueprint": True,
          "position": {"x": 130, "z": 146}, "workLeftText": "3",
          "workLeft": 180.0, "status": "blueprint"}
    WALL = {"thingId": "Thing_Wall2", "defName": "Wall", "label": "sandstone wall",
            "position": {"x": 130, "z": 146}, "status": "built"}

    def run_report(self, reads, before=()):
        seen = iter(reads)
        out = io.StringIO()
        with unittest.mock.patch.object(
                buildings.rim, "game",
                side_effect=lambda *a, **k: {"success": True,
                                             "buildings": next(seen, [])}), \
             unittest.mock.patch.object(buildings.time, "sleep"), \
             contextlib.redirect_stdout(out):
            code = buildings.report_placement(130, 146, "wall", before)
        return code, out.getvalue()

    def test_a_blueprint_the_first_reads_missed_is_confirmed_not_denied(self):
        code, text = self.run_report([[], [], [self.BP]])
        self.assertEqual(0, code)
        self.assertIn("PLACED -- CONFIRMED", text)
        self.assertIn("Thing_Blueprint7", text)
        self.assertIn("work left 3", text)

    def test_nothing_seen_says_not_seen_and_names_the_next_command(self):
        code, text = self.run_report([[], [], [], []])
        self.assertEqual(1, code)
        self.assertIn("NOT VISIBLE YET", text)
        self.assertIn("--near 130 146 2 --every", text)
        self.assertNotIn("reads empty", text)
        self.assertNotIn("NOT PLACED", text)

    def test_a_cell_that_holds_something_else_gets_a_different_sentence(self):
        code, text = self.run_report([[self.WALL]] * 4, before={"wall2"})
        self.assertEqual(1, code)
        self.assertIn("NOT PLACED -- SOMETHING ELSE IS THERE", text)
        self.assertIn("sandstone wall", text)
        self.assertNotIn("NOT VISIBLE YET", text)

    def test_the_three_outcomes_share_no_sentence(self):
        heads = set()
        for reads, before in (([[], [], [self.BP]], ()),
                              ([[], [], [], []], ()),
                              ([[self.WALL]] * 4, {"wall2"})):
            heads.add(self.run_report(reads, before)[1].splitlines()[0])
        self.assertEqual(3, len(heads))

    def test_a_blueprint_that_was_already_there_is_not_a_confirmation(self):
        code, text = self.run_report([[self.BP]] * 4, before={"blueprint7"})
        self.assertEqual(1, code)
        self.assertIn("NOT PLACED -- SOMETHING ELSE IS THERE", text)


class PendingCensusTests(unittest.TestCase):
    """WEIRD 14/21: `blueprints=0, frames=1` seconds after a wall was placed as
    a blueprint. A touched blueprint IS a frame; the queue is the two."""

    def test_the_header_counts_both_and_says_why(self):
        r = PendingOutputTests.full_reply()
        r["attention"]["blueprints"] = 0
        r["attention"]["frames"] = 1
        r["buildings"] = [{"defName": "Frame_Wall", "isFrame": True,
                           "status": "frame", "position": {"x": 116, "z": 130},
                           "workLeftText": "12", "resourcesComplete": True}]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            buildings.pending_block(r)
        text = out.getvalue()
        self.assertIn("1 queued (0 blueprint(s), 1 frame(s))", text)
        self.assertIn("becomes a FRAME", text)

    def test_an_empty_queue_still_says_a_blueprint_becomes_a_frame(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            buildings.pending_block(PendingOutputTests.full_reply())
        self.assertIn("becomes a FRAME", out.getvalue())


class WorkLeftOneSourceTests(unittest.TestCase):
    """WEIRD 32: a row said "work left 800" while its own inspect line in the
    same output said "Work left: 14"."""

    def test_the_pane_scale_wins_when_the_reply_carries_it(self):
        self.assertEqual("13", buildings._work_left(
            {"workLeft": 800.0, "workLeftText": "13"}))

    def test_a_raw_number_is_labelled_raw_so_it_is_never_read_as_the_pane(self):
        text = buildings._work_left({"workLeft": 800.0})
        self.assertIn("raw", text)
        self.assertIn("800", text)

    def test_a_pending_row_prints_the_pane_number(self):
        r = PendingOutputTests.full_reply()
        r["attention"]["frames"] = 1
        r["buildings"] = [{"defName": "Frame_Grave", "isFrame": True,
                           "status": "frame", "buildLabel": "grave",
                           "position": {"x": 121, "z": 134},
                           "percentComplete": 0.004, "workLeft": 800.0,
                           "workLeftText": "13", "resourcesComplete": True}]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            buildings.pending_block(r)
        self.assertIn("work left 13", out.getvalue())
        self.assertNotIn("work left 800", out.getvalue())


class KindColumnTests(unittest.TestCase):
    """WEIRD 31: a built Grave and a blueprint grave in one block with no
    column distinguishing them."""

    def test_every_promoted_row_carries_its_kind(self):
        r = power_reply([])
        r["filters"]["status"] = "built"
        r["buildings"] = [{"defName": "Grave", "label": "grave", "status": "built",
                           "position": {"x": 121, "z": 133}, "reasons": []}]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            buildings.other_detail_block(r)
        text = out.getvalue()
        self.assertIn("kind |", text)
        self.assertIn("built ", text)

    def test_the_three_kinds_have_three_words(self):
        self.assertEqual("built", buildings._kind({"status": "built"}))
        self.assertEqual("blueprint", buildings._kind({"isBlueprint": True}))
        self.assertEqual("frame", buildings._kind({"isFrame": True}))
        self.assertEqual("install-bp", buildings._kind(
            {"isBlueprint": True, "isInstallBlueprint": True}))


class UnknownFlagTests(unittest.TestCase):
    """WEIRD 38: `--rooms` printed the full worktable and power report and no
    rooms, with no complaint about the unknown flag."""

    def test_rooms_is_refused_and_named_to_its_own_tool(self):
        self.assertEqual(["--rooms"], buildings.unknown_flags(["--rooms"]))

    def test_the_flags_it_does_know_are_not_refused(self):
        self.assertEqual([], buildings.unknown_flags(
            ["--power", "--near", "116", "130", "10", "--every", "--json"]))

    def test_a_negative_number_is_not_a_flag(self):
        self.assertEqual([], buildings.unknown_flags(["--near", "-5", "10", "3"]))

    def test_the_refusal_reads_nothing(self):
        with unittest.mock.patch.object(buildings.rim, "init") as init, \
             unittest.mock.patch.object(buildings, "survey") as survey, \
             unittest.mock.patch.object(buildings.sys, "argv",
                                        ["buildings.py", "--rooms"]):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = buildings.main()
        self.assertEqual(2, code)
        self.assertIn("map.py --rooms", out.getvalue())
        init.assert_not_called()
        survey.assert_not_called()


class ScopeFlagTests(unittest.TestCase):
    """WEIRD 54 (`--match "door" --center X,Z --radius 12 --all` was silently
    three unknown flags) and WEIRD 68 (a cell with --radius is a SCAN)."""

    def run_main(self, argv):
        seen = {}

        def survey(**kw):
            seen.update(kw)
            r = PendingOutputTests.full_reply(status="all")
            r["filters"].update({k: v for k, v in kw.items()
                                 if k in r["filters"]})
            r["filters"].setdefault("x", kw.get("x", -1))
            r["filters"].setdefault("z", kw.get("z", -1))
            r["filters"]["radius"] = kw.get("radius", 0)
            return r

        with unittest.mock.patch.object(buildings.rim, "init"), \
             unittest.mock.patch.object(buildings, "survey", side_effect=survey), \
             unittest.mock.patch.object(buildings.sys, "argv",
                                        ["buildings.py"] + argv):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = buildings.main()
        return code, seen, out.getvalue()

    def test_match_center_radius_all_reach_the_tool(self):
        code, kw, _ = self.run_main(["--match", "door", "--center", "120,140",
                                     "--radius", "12", "--all"])
        self.assertEqual(0, code)
        self.assertEqual("door", kw["match"])
        self.assertEqual((120, 140, 12), (kw["x"], kw["z"], kw["radius"]))
        self.assertFalse(kw["playerOnly"])

    def test_a_centre_as_two_words_is_the_same_centre(self):
        _, kw, _ = self.run_main(["--center", "120", "140", "--radius", "9"])
        self.assertEqual((120, 140, 9), (kw["x"], kw["z"], kw["radius"]))

    def test_a_centre_without_a_radius_is_refused(self):
        code, _, text = self.run_main(["--center", "120,140"])
        self.assertEqual(1, code)
        self.assertIn("--center needs --radius", text)

    def test_a_cell_with_a_radius_scans_instead_of_addressing(self):
        code, kw, text = self.run_main(["106,127", "--radius", "9", "--rock"])
        self.assertEqual(0, code)
        self.assertEqual((106, 127, 9), (kw["x"], kw["z"], kw["radius"]))
        self.assertIn("SCAN of 9 cell(s)", text)
        self.assertNotIn("NOTHING AT", text)

    def test_a_cell_with_no_radius_is_still_an_address(self):
        code, kw, text = self.run_main(["106,127"])
        self.assertEqual(buildings.TARGET_PAD, kw["radius"])
        self.assertIn("NOTHING AT cell 106,127", text)


class RockScopeTests(unittest.TestCase):
    """WEIRD 46/49: 12,468 natural rock rows removed by `byPlayerOnly`, so
    `--rock` returned worktables and there was no way to read natural rock."""

    def run_main(self, argv):
        seen = {}

        def survey(**kw):
            seen.update(kw)
            r = PendingOutputTests.full_reply(status="all")
            r["filters"].update({k: v for k, v in kw.items() if k in r["filters"]})
            r["filters"]["x"] = kw.get("x", -1)
            r["filters"]["z"] = kw.get("z", -1)
            r["filters"]["radius"] = kw.get("radius", 0)
            r["filters"]["category"] = kw.get("category", "artificial")
            r["aggregated"] = [
                {"defName": "Sandstone", "label": "sandstone", "count": 4102,
                 "positions": [{"x": 106, "z": 127}]},
                {"defName": "MineableSteel", "label": "compacted steel",
                 "count": 31, "positions": [{"x": 110, "z": 130}]}]
            r["counts"]["aggregatedBuildings"] = 4133
            return r

        with unittest.mock.patch.object(buildings.rim, "init"), \
             unittest.mock.patch.object(buildings, "survey", side_effect=survey), \
             unittest.mock.patch.object(buildings.sys, "argv",
                                        ["buildings.py"] + argv):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = buildings.main()
        return code, seen, out.getvalue()

    def test_rock_asks_for_the_rock_category_and_drops_the_colony_filter(self):
        code, kw, text = self.run_main(["--rock"])
        self.assertEqual(0, code)
        self.assertEqual("rock", kw["category"])
        self.assertFalse(kw["playerOnly"])

    def test_it_prints_counts_by_def_and_not_the_worktable_report(self):
        _, _, text = self.run_main(["--rock"])
        self.assertIn("ROCK -- 4133 thing(s) across 2 def(s)", text)
        self.assertIn("Sandstone", text)
        self.assertIn("MineableSteel", text)
        self.assertNotIn("WORKTABLES", text)

    def test_cells_are_printed_only_under_a_scope(self):
        _, _, wide = self.run_main(["--rock"])
        self.assertIn("Add `--near x z r`", wide)
        _, _, near = self.run_main(["--rock", "--near", "106", "127", "9"])
        self.assertIn("106,127", near)

    def test_the_companion_takes_the_rock_category(self):
        source = (Path(__file__).resolve().parents[1] / "companion" / "src" /
                  "ListBuildingsTool.cs").read_text(encoding="utf-8")
        self.assertIn('wantCategory != "rock"', source)
        self.assertIn("if (rockOnly && !IsRockDef(thing.def))", source)
        self.assertIn("b.isNaturalRock || b.isResourceRock", source)


class InspectScopeTests(unittest.TestCase):
    """WEIRD 28/69: `--inspect` returns nothing when rows aggregate, and
    `--inspect Fence` routed every match into the wrong section."""

    def run_main(self, argv):
        seen = {}

        def survey(**kw):
            seen.update(kw)
            r = PendingOutputTests.full_reply(status="all")
            r["filters"].update({k: v for k, v in kw.items() if k in r["filters"]})
            r["filters"]["x"] = kw.get("x", -1)
            r["filters"]["z"] = kw.get("z", -1)
            r["filters"]["radius"] = kw.get("radius", 0)
            return r

        with unittest.mock.patch.object(buildings.rim, "init"), \
             unittest.mock.patch.object(buildings, "survey", side_effect=survey), \
             unittest.mock.patch.object(buildings.sys, "argv",
                                        ["buildings.py"] + argv):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                buildings.main()
        return seen, out.getvalue()

    def test_inspect_with_a_match_promotes_every_matching_row(self):
        kw, text = self.run_main(["--inspect", "Fence"])
        self.assertFalse(kw["aggregate"])
        self.assertIn("aggregation off", text)

    def test_inspect_with_a_scope_promotes_too(self):
        kw, _ = self.run_main(["--inspect", "--near", "116", "130", "10"])
        self.assertFalse(kw["aggregate"])

    def test_a_whole_map_inspect_is_left_aggregated(self):
        kw, _ = self.run_main(["--inspect"])
        self.assertNotIn("aggregate", kw)

    def test_zero_of_zero_says_the_rows_are_aggregated(self):
        r = PendingOutputTests.full_reply(status="built")
        r["filters"]["inspect"] = True
        r["counts"]["aggregatedBuildings"] = 40
        r["counts"]["aggregatedRows"] = 3
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            buildings.footer(r)
        text = out.getvalue()
        self.assertIn("NO ROW WAS DETAILED", text)
        self.assertIn("Add --every", text)
        self.assertNotIn("0 of 0 detailed", text)


class ReinstallAddressTests(unittest.TestCase):
    """WEIRD 44: `reinstall <x> <z>` refused bare coordinates, and a minified
    item is not a building at all."""

    def test_four_bare_numbers_are_a_source_cell_and_a_destination(self):
        self.assertEqual(("108,127", (120, 140)),
                         buildings.reinstall_argv(["108", "127", "120", "140"]))

    def test_three_words_keep_the_thing_form(self):
        self.assertEqual(("Shelf99", (120, 140)),
                         buildings.reinstall_argv(["Shelf99", "120", "140"]))

    def test_a_line_it_cannot_read_says_both_forms(self):
        spec, why = buildings.reinstall_argv(["Shelf99"])
        self.assertIsNone(spec)
        self.assertIn("<sx> <sz> <x> <z>", why)

    def test_a_packed_up_item_at_a_cell_is_installed_not_refused(self):
        found = {"thingId": "Thing_MinifiedThing42", "label": "cooler",
                 "defName": "MinifiedThing", "minified": True}
        import mini_install
        with unittest.mock.patch.object(buildings, "resolve_at_cell",
                                        return_value=found), \
             unittest.mock.patch.object(mini_install, "install") as install:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = buildings.reinstall_cmd("108,127", 120, 140, do=True)
        self.assertEqual(0, code)
        install.assert_called_once_with("Thing_MinifiedThing42", True, [120, 140])
        self.assertIn("PACKED UP", out.getvalue())

    def test_an_empty_cell_says_so_as_an_address(self):
        with unittest.mock.patch.object(buildings, "resolve_at_cell",
                                        return_value=None):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = buildings.reinstall_cmd("108,127", 120, 140, do=True)
        self.assertEqual(1, code)
        self.assertIn("nothing at 108,127 can be moved", out.getvalue())


class RotateTests(unittest.TestCase):
    """WEIRD 58: there is no rotation verb anywhere on this bridge; rotation
    exists only as a positional on `build.py`, which places a NEW building."""

    SOURCE = (Path(__file__).resolve().parents[1] / "companion" / "src" /
              "BuildingConfigTool.cs").read_text(encoding="utf-8")

    def test_the_verb_sends_the_rotation_field(self):
        with unittest.mock.patch.object(buildings.rim, "init"), \
             unittest.mock.patch.object(buildings, "set_cmd",
                                        return_value=0) as set_cmd, \
             unittest.mock.patch.object(
                 buildings.sys, "argv",
                 ["buildings.py", "rotate", "Thing_Blueprint7", "east", "--do"]):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = buildings.main()
        self.assertEqual(0, code)
        self.assertEqual(("Thing_Blueprint7", {"rotation": "east"}),
                         set_cmd.call_args[0])
        self.assertTrue(set_cmd.call_args[1]["do"])

    def test_a_bare_rotate_names_both_the_forms_and_the_limit(self):
        with unittest.mock.patch.object(buildings.sys, "argv",
                                        ["buildings.py", "rotate"]):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = buildings.main()
        self.assertEqual(1, code)
        self.assertIn("Blueprints and frames only", out.getvalue())

    def test_the_companion_guards_the_write_three_ways(self):
        self.assertIn("private static FieldPlan PlanRotation", self.SOURCE)
        self.assertIn("!thing.def.rotatable", self.SOURCE)
        self.assertIn("if (!isBlueprint && !isFrame)", self.SOURCE)
        self.assertIn("RotatedFootprintIsClear", self.SOURCE)

    def test_a_standing_building_is_refused_with_the_route(self):
        self.assertIn("act.py uninstall", self.SOURCE)


class DesignatorNoteTests(unittest.TestCase):
    """WEIRD 18: `gizmos` on a bed lists no Deconstruct and no Uninstall,
    because both are REVERSE designators -- the inspect grid adds them to a
    selected thing's bar, and Thing.GetGizmos() never yields them."""

    def test_the_bar_listing_names_the_designator_route(self):
        reply = {"success": True,
                 "thing": {"thingId": "Bed1", "defName": "Bed", "label": "bed",
                           "position": {"x": 62, "z": 141}},
                 "gizmos": [{"label": "Set owner", "type": "Command_Action"}],
                 "gizmoCount": 1, "after": {}}
        with unittest.mock.patch.object(buildings, "configure",
                                        return_value=reply):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                buildings.gizmos_cmd("Bed1")
        text = out.getvalue()
        self.assertIn("REVERSE designators", text)
        self.assertIn('act.py apply "Deconstruct"', text)
        self.assertIn('buildings.py gizmo <thing> "Uninstall" --do', text)
        self.assertIn("not minifiable", text)
        self.assertNotIn("Uninstall has NO route", text)


class PowerCapacityTests(unittest.TestCase):
    """WEIRD 5/9/55: the `gen` row is live output, not capacity, and
    `gen 5400W ... stored 1173.7Wd` was printed above eleven UNPOWERED
    consumers during a solar flare."""

    LAMP = {"thingId": "Lamp1", "defName": "StandingLamp", "label": "standing lamp",
            "position": {"x": 130, "z": 145}, "role": "consumer",
            "faction": "Player", "powerOutputW": 0.0, "capacityW": 100.0,
            "poweredOn": False, "switchedOn": True, "brokenDown": False}
    PANEL = {"thingId": "Panel1", "defName": "SolarGenerator",
             "label": "solar generator", "position": {"x": 128, "z": 145},
             "role": "producer", "faction": "Player", "powerOutputW": 0.0,
             "capacityW": 1700.0, "poweredOn": True, "hasFuel": None}

    def flare_reply(self):
        r = power_reply([net(
            index=0, producerCount=3, consumerCount=11, batteryCount=2,
            generationW=0.0, consumptionW=0.0, storedWd=1173.7,
            storedMaxWd=2000.0, flags=[],
            buildings=[self.PANEL, self.LAMP])])
        r["powerNets"][0]["generationCapacityW"] = 5400.0
        r["powerNets"][0]["consumptionCapacityW"] = 1420.0
        r["powerNets"][0]["idleProducerCount"] = 3
        r["powerNets"][0]["poweredConsumerCount"] = 0
        r["powerNets"][0]["unpoweredConsumerCount"] = 11
        r["powerSummary"]["solarFlare"] = True
        r["powerSummary"]["activeConditions"] = ["SolarFlare"]
        return r

    def test_capacity_output_and_draw_are_three_separate_numbers(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            buildings.power_nets_block(self.flare_reply())
        text = out.getvalue()
        self.assertIn("capacity gen 5400W / draw 1420W", text)
        self.assertIn("now gen 0W / draw 0W", text)

    def test_a_solar_flare_is_named_as_the_reason(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            buildings.power_nets_block(self.flare_reply())
        text = out.getvalue()
        self.assertIn("SOLAR FLARE is active", text)
        self.assertIn("11 UNPOWERED", text)
        self.assertIn("standing lamp", text)

    def test_an_unfuelled_generator_is_named_as_idle_with_its_reason(self):
        r = self.flare_reply()
        r["powerSummary"]["solarFlare"] = False
        r["powerSummary"]["activeConditions"] = []
        r["powerNets"][0]["buildings"] = [
            dict(self.PANEL, defName="WoodFiredGenerator",
                 label="wood-fired generator", hasFuel=False),
            self.LAMP]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            buildings.power_nets_block(r)
        text = out.getvalue()
        self.assertIn("idle generator", text)
        self.assertIn("no fuel", text)
        self.assertIn("every generator on this net is producing nothing", text)

    def test_the_per_building_line_says_flare_not_no_power_available(self):
        r = self.flare_reply()
        r["buildings"] = [{"defName": "StandingLamp", "label": "standing lamp",
                           "thingId": "Lamp1", "position": {"x": 130, "z": 145},
                           "status": "built", "reasons": ["unpowered"],
                           "power": {"powered": False, "connected": True,
                                     "switchedOn": True, "brokenDown": False}}]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            buildings.power_block(r)
        text = out.getvalue()
        self.assertIn("SOLAR FLARE", text)
        self.assertNotIn("no power available on its net", text)

    def test_an_old_dll_says_gen_is_output_not_capacity(self):
        r = power_reply([net(index=0, producerCount=1, consumerCount=1,
                             generationW=0.0, consumptionW=0.0, flags=[])])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            buildings.power_nets_block(r)
        self.assertIn("predates the capacity figures", out.getvalue())

    def test_a_map_with_no_power_net_does_not_ask_for_a_dll_rebuild(self):
        """The capacity probe reads the FIRST net's keys. With no nets there is
        no first net, and an empty dict has no capacity key either -- so a map
        that simply has no power on it was told to reinstall the companion,
        which costs a game-closed restart."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            buildings.power_nets_block(power_reply([]))
        text = out.getvalue()
        self.assertNotIn("predates the capacity figures", text)
        self.assertIn("the map has no power network at all", text)

    def test_the_companion_reports_capacity_and_the_flare(self):
        source = (Path(__file__).resolve().parents[1] / "companion" / "src" /
                  "ListBuildingsTool.cs").read_text(encoding="utf-8")
        self.assertIn('{ "generationCapacityW", generationCapacityW },', source)
        self.assertIn('{ "unpoweredConsumerCount", unpoweredConsumerCount },', source)
        self.assertIn('{ "solarFlare", conditions.Contains("SolarFlare") },', source)


class CellScopeTests(unittest.TestCase):
    """The WRITE side's cell lookups, and how wide they actually look.

    `position` is Thing.Position, which for a multi-cell building is NOT the
    min corner (INSTALL.md, Payload shapes), and the companion's radius filter
    is anchor-only (ListBuildingsTool.cs). So a radius that only covers the
    asked-for cell cannot return a building whose anchor is further off -- and
    the `_covers` predicate underneath never gets a list to filter.
    """

    def sent_args(self, fn, *a):
        seen = []

        def game(tool, args=None, strict=True):
            seen.append((tool, args))
            return {"success": True, "buildings": [], "things": []}

        with mock.patch.object(buildings.rim, "game", side_effect=game):
            fn(*a)
        return seen

    def test_a_cell_read_looks_as_wide_as_the_biggest_footprint(self):
        seen = self.sent_args(buildings.cell_contents, 115, 155)
        args = seen[0][1]
        self.assertGreaterEqual(args["radius"], buildings.TARGET_PAD)

    def test_a_non_anchor_cell_of_a_big_building_still_resolves(self):
        """A 4x4 solar generator anchored at 113,153 occupies 112..115 --
        two cells from its anchor. Under an anchor-only radius:1 filter the row
        never came back, and `reinstall 115 155 ...` refused with 'nothing at
        115,155 can be moved'."""
        big = {"thingId": "SolarGenerator77", "defName": "SolarGenerator",
               "label": "solar generator", "position": {"x": 113, "z": 153},
               "occupies": {"minX": 112, "minZ": 152, "maxX": 115, "maxZ": 155},
               "status": "built"}

        def game(tool, args=None, strict=True):
            if tool != buildings.TOOL:
                return {"success": True, "things": []}
            # The companion's own filter: Chebyshev, against the ANCHOR.
            r = max(abs(big["position"]["x"] - args["x"]),
                    abs(big["position"]["z"] - args["z"]))
            return {"success": True,
                    "buildings": [big] if r <= args["radius"] else []}

        with mock.patch.object(buildings.rim, "game", side_effect=game):
            found = buildings.resolve_at_cell(115, 155)
        self.assertIsNotNone(found)
        self.assertEqual("SolarGenerator77", found["thingId"])

    def test_the_packed_item_lookup_asks_for_more_than_eight_positions(self):
        """home/list_things aggregates by defName and caps positions[] at 8 by
        default, so in a furniture stockpile the packed item ON the cell can be
        in the unlisted remainder and the cell reads empty."""
        seen = self.sent_args(buildings.resolve_at_cell, 115, 155)
        things = [a for t, a in seen if t == "home/list_things"][0]
        self.assertGreater(things.get("maxPositionsPerDef", 8), 8)


class ReverseDesignatorCountTests(unittest.TestCase):
    def test_a_reverse_designator_on_the_bar_is_not_blamed_on_a_shared_tile(self):
        """`gizmoCount` is Thing.GetGizmos() alone; the selected bar is that
        PLUS the reverse designators (Cancel, Deconstruct, Uninstall). The two
        can never match on exactly the call BUGS names -- `gizmo <n> "Cancel"`
        on a blueprint, whose GetGizmos() is empty -- so the warning fired
        every time and sent the reader to inv.py after a hauling problem that
        does not exist."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            buildings._fire_from_bar(
                [{"id": "g1", "label": "Cancel"}], None, False,
                "wall blueprint", "Blueprint_Wall4519", expect_count=0)
        text = out.getvalue()
        self.assertNotIn("Something else may share this tile", text)


class PowerAliasTests(unittest.TestCase):
    """WEIRD 64: `power.py` does not exist."""

    def test_it_adds_the_power_flag_and_delegates(self):
        import power
        with unittest.mock.patch.object(buildings, "main",
                                        return_value=0) as main, \
             unittest.mock.patch.object(power.sys, "argv",
                                        ["power.py", "--near", "1", "2", "3"]):
            self.assertEqual(0, power.main())
            self.assertEqual(["power.py", "--near", "1", "2", "3", "--power"],
                             power.sys.argv)
        main.assert_called_once_with()

    def test_it_does_not_double_the_flag(self):
        import power
        with unittest.mock.patch.object(buildings, "main", return_value=0), \
             unittest.mock.patch.object(power.sys, "argv",
                                        ["power.py", "--power"]):
            power.main()
            self.assertEqual(["power.py", "--power"], power.sys.argv)

class MatchLocatorTests(unittest.TestCase):
    """WEIRD 69: `--inspect Fence` routed every match into the wrong section
    three times before printing the rows."""

    def test_a_match_says_where_its_rows_are_before_the_sections_do(self):
        r = PendingOutputTests.full_reply(status="all")
        r["filters"]["match"] = "Fence"
        r["filters"]["aggregate"] = False
        r["counts"]["detailed"] = 4
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            buildings.show(r)
        text = out.getvalue()
        self.assertIn("MATCH 'Fence' -- 4 thing(s) matched", text)
        self.assertIn("OTHER DETAILED ROWS at the bottom", text)
        self.assertLess(text.index("MATCH 'Fence'"), text.index("nothing matching"))

    def test_a_call_with_no_match_prints_no_locator(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            buildings.show(PendingOutputTests.full_reply(status="all"))
        self.assertNotIn("MATCH", out.getvalue())


class PawnGizmoBarTests(unittest.TestCase):
    """Live 2026-09-08. Two bugs, both silent:

    * a pawn payload names its id `pawnId` and has no `thingId`, so the click
      check compared against a blank -- "selection is pawn Ernst
      [Thing_Human618], expected Ernst [None]";
    * the bar was read by clicking the pawn's CELL, and Samantha stood on
      Ernst's square for fourteen turns, so every read of his bar was hers and
      a wearable turret pack on it was never seen.
    """

    ENVELOPE = {"success": True, "kind": "pawn", "label": "Ernst",
                "thingId": "Thing_Human618", "pawnId": "Thing_Human618",
                "position": {"x": 138, "z": 110},
                "target": {"pawnId": "Thing_Human618", "name": "Ernst",
                           "label": "Ernst", "className": "Verse.Pawn",
                           "defName": "Human", "spawned": True,
                           "position": {"x": 138, "z": 110}}}

    REFUSAL = {"success": False,
               "error": 'No colony building matches "Ernst".'}

    GIZMOS = [{"id": "g1", "label": "Wearable turret pack", "disabled": False},
              {"id": "g2", "label": "Draft", "disabled": False}]

    def _run(self, spec="Ernst", label=None, do=False, selected="Thing_Human618",
             by_name=True, select_ok=True, gizmos=None, calls=None):
        calls = calls if calls is not None else []

        def fake_game(tool, params=None, **kw):
            calls.append((tool, params))
            if tool == "rimworld/get_map_target_info":
                params = params or {}
                if by_name and params.get("pawnName"):
                    return self.ENVELOPE
                if params.get("pawnId") and not by_name:
                    return self.ENVELOPE
                if str(params.get("thingId") or "").endswith("Human618"):
                    return self.ENVELOPE
                return {"success": False, "message": "Could not find thing id"}
            if tool == "rimworld/select_pawn":
                if not select_ok:
                    return {"success": False,
                            "message": "No player-controlled colonist matches"}
                return {"success": True, "selectedCount": 1}
            if tool == "rimworld/list_selected_gizmos":
                return {"success": True,
                        "gizmos": self.GIZMOS if gizmos is None else gizmos}
            if tool == "rimworld/get_selection_semantics":
                return {"success": True, "hasSelection": True,
                        "selectedCount": 1,
                        "selectedObjects": [{"id": selected, "kind": "pawn",
                                             "label": selected}]}
            if tool == "rimworld/get_designator_state":
                return {"success": True,
                        "designatorState": {"hasSelection": False}}
            return {"success": True}

        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
             unittest.mock.patch.object(buildings, "configure",
                                        return_value=self.REFUSAL), \
             unittest.mock.patch.object(buildings.pick.time, "sleep"), \
             unittest.mock.patch.object(buildings.rim, "game", fake_game):
            if label is None:
                rc = buildings.gizmos_cmd(spec)
            else:
                rc = buildings.gizmo_cmd(spec, label, do=do)
        return rc, out.getvalue(), calls

    def test_a_pawn_bar_reads_by_name_and_never_says_expected_none(self):
        rc, text, calls = self._run()
        self.assertEqual(0, rc)
        self.assertIn("Thing_Human618", text)
        self.assertNotIn("[None]", text)
        self.assertIn("Wearable turret pack", text)

    def test_the_pawn_is_selected_by_id_and_the_cell_is_never_clicked(self):
        rc, text, calls = self._run()
        tools = [t for t, _ in calls]
        self.assertIn(("rimworld/select_pawn", {"pawnId": "Thing_Human618"}),
                      calls)
        self.assertNotIn("rimworld/click_cell", tools)
        self.assertIn("selected by id, no click", text)

    def test_the_neighbour_on_the_tile_can_no_longer_answer_for_them(self):
        """Samantha on Ernst's square. Refused, not answered with her bar."""
        rc, text, calls = self._run(selected="Thing_Human702")
        self.assertEqual(1, rc)
        self.assertIn("SELECT MISSED", text)
        self.assertIn("Thing_Human702", text)
        self.assertNotIn("Wearable turret pack", text)

    def test_an_id_resolves_the_same_pawn_as_the_name(self):
        rc, text, calls = self._run(spec="Thing_Human618", by_name=False)
        self.assertEqual(0, rc)
        self.assertIn("Wearable turret pack", text)

    def test_firing_one_gizmo_on_a_pawn_goes_through_the_same_check(self):
        rc, text, calls = self._run(label="Wearable turret pack", do=True)
        self.assertEqual(0, rc)
        self.assertIn(("rimworld/execute_gizmo", {"gizmoId": "g1"}), calls)
        self.assertNotIn("rimworld/click_cell", [t for t, _ in calls])

    def test_a_non_colonist_falls_back_to_the_verified_cell_click(self):
        rc, text, calls = self._run(select_ok=False)
        tools = [t for t, _ in calls]
        self.assertIn("PLAYER COLONISTS only", text)
        self.assertIn("rimworld/click_cell", tools)
        self.assertEqual(0, rc)

    def test_a_payload_with_no_id_at_all_refuses_before_selecting(self):
        target = {"label": "Ernst", "className": "Verse.Pawn", "kind": "pawn",
                  "spawned": True, "position": {"x": 138, "z": 110}}
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
             unittest.mock.patch.object(buildings.rim, "game") as game:
            rc = buildings._bar_via_selection(target, None)
        self.assertEqual(1, rc)
        self.assertIn("no id at all", out.getvalue())
        game.assert_not_called()


class NearRadiusTests(unittest.TestCase):
    """Live 2026-09-08: `--near 139 123 --radius 4` was refused with "--near
    needs x z radius" although --radius says exactly that, and
    `--near 139 123 4 --radius 8` silently used 4."""

    def run_main(self, argv):
        return ScopeFlagTests.run_main(self, argv)

    def test_near_takes_the_radius_flag(self):
        code, kw, text = self.run_main(["--near", "139", "123", "--radius", "4"])
        self.assertEqual(0, code)
        self.assertEqual((139, 123, 4), (kw["x"], kw["z"], kw["radius"]))

    def test_the_positional_third_number_still_works(self):
        code, kw, _ = self.run_main(["--near", "139", "123", "4"])
        self.assertEqual(0, code)
        self.assertEqual((139, 123, 4), (kw["x"], kw["z"], kw["radius"]))

    def test_a_cell_with_no_radius_anywhere_is_refused_naming_both_forms(self):
        code, kw, text = self.run_main(["--near", "139", "123"])
        self.assertEqual(1, code)
        self.assertIn("--near 139 123 --radius 4", text)
        self.assertIn("--near 139 123 4", text)
        self.assertIn("NOTHING WAS READ", text)
        self.assertEqual({}, kw)

    def test_two_different_radii_are_a_contradiction_not_a_preference(self):
        code, kw, text = self.run_main(["--near", "139", "123", "4",
                                        "--radius", "8"])
        self.assertEqual(1, code)
        self.assertIn("says radius 4 and --radius says 8", text)
        self.assertEqual({}, kw)

    def test_the_same_radius_twice_is_not_a_contradiction(self):
        code, kw, _ = self.run_main(["--near", "139", "123", "4",
                                     "--radius", "4"])
        self.assertEqual(0, code)
        self.assertEqual((139, 123, 4), (kw["x"], kw["z"], kw["radius"]))

    def test_near_with_one_number_is_refused(self):
        code, _, text = self.run_main(["--near", "139"])
        self.assertEqual(1, code)
        self.assertIn("--near needs a cell", text)


class AtCoordinateTests(unittest.TestCase):
    """Live 2026-09-08: `buildings.py at 139,123` read `at` as a --match
    SUBSTRING -- it is inside Gate, Battery, Water -- and answered with a
    confident row for a building nowhere near that cell."""

    def run_main(self, argv):
        return ScopeFlagTests.run_main(self, argv)

    def test_at_is_a_coordinate_not_a_name(self):
        code, kw, text = self.run_main(["at", "139,123"])
        self.assertEqual((139, 123), (kw["x"], kw["z"]))
        self.assertEqual(buildings.TARGET_PAD, kw["radius"])
        self.assertIsNone(kw.get("match"))
        self.assertIn("NOTHING AT cell 139,123", text)

    def test_at_takes_two_words_as_one_cell(self):
        code, kw, text = self.run_main(["at", "139", "123"])
        self.assertEqual((139, 123), (kw["x"], kw["z"]))
        self.assertIsNone(kw.get("match"))

    def test_at_with_a_radius_is_still_a_scan(self):
        code, kw, text = self.run_main(["at", "139,123", "--radius", "6"])
        self.assertEqual((139, 123, 6), (kw["x"], kw["z"], kw["radius"]))
        self.assertIn("SCAN of 6 cell(s)", text)

    def test_at_a_word_is_refused_and_names_match(self):
        code, kw, text = self.run_main(["at", "bench"])
        self.assertEqual(2, code)
        self.assertIn("--match bench", text)
        self.assertIn("NOTHING WAS READ", text)
        self.assertEqual({}, kw)

    def test_a_bare_at_is_refused(self):
        code, _, text = self.run_main(["at"])
        self.assertEqual(2, code)
        self.assertIn("at 139,123", text)


class GizmoLabellessTests(unittest.TestCase):
    def test_gizmo_with_no_label_reads_the_bar_and_fires_nothing(self):
        calls = []

        def fake_game(tool, params=None, **kw):
            calls.append((tool, params))
            if tool == "rimworld/list_selected_gizmos":
                return {"success": True,
                        "gizmos": [{"id": "g1", "label": "Uninstall"}]}
            if tool == "rimworld/get_selection_semantics":
                return {"success": True, "hasSelection": True,
                        "selectedCount": 1,
                        "selectedObjects": [{"id": "Turret_MiniTurret1",
                                             "kind": "thing", "label": "t"}]}
            return {"success": True}

        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
             unittest.mock.patch.object(
                 buildings, "configure",
                 return_value=GizmoOneProcessTests._thing()), \
             unittest.mock.patch.object(buildings.pick.time, "sleep"), \
             unittest.mock.patch.object(buildings.rim, "game", fake_game):
            rc = buildings.gizmo_cmd("Turret_MiniTurret1", None)
        self.assertEqual(0, rc)
        self.assertIn("Nothing", out.getvalue())
        self.assertNotIn("rimworld/execute_gizmo", [t for t, _ in calls])

    def test_do_with_no_label_is_refused_before_anything_is_read(self):
        with unittest.mock.patch.object(buildings.rim, "init") as init, \
             unittest.mock.patch.object(buildings.sys, "argv",
                                        ["buildings.py", "gizmo", "Ernst",
                                         "--do"]):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = buildings.main()
        self.assertEqual(1, code)
        self.assertIn("names no gizmo to fire", out.getvalue())
        init.assert_not_called()


if __name__ == "__main__":
    unittest.main()
