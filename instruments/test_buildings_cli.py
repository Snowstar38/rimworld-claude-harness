import contextlib
import io
from pathlib import Path
import sys
import unittest
import unittest.mock

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
        self.assertIn("counted 2 gizmo(s) and the selection carries 1", text)

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


if __name__ == "__main__":
    unittest.main()
