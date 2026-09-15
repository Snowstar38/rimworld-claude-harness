"""Offline regressions for `debug.py`, with the bridge mocked.

Nothing here talks to a game. What is being pinned is the part that decides
WHAT would be fired: the path grammar (backslash-separated, `T: ` prefixes,
`...` suffixes), the refuse-with-candidates rule, and the promise that nothing
executes without `--do`. The paths asserted below are the ones read out of the
decompiled 1.6 source -- if a future RimWorld renames a label, these tests
still pass and the live resolver falls through to `search_debug_actions`, which
is the whole point of resolving at runtime.
"""
import io
import unittest
from unittest import mock

import debug


def node(path, kind="Direct", supported=True, target=None, label=None,
         children=False, visible=True):
    return {"path": path, "label": label if label is not None else path.rsplit("\\", 1)[-1],
            "visible": visible, "hasChildren": children,
            "execution": {"kind": kind, "supported": supported,
                          "reason": None if supported else "a submenu",
                          "requiredTargetKind": target}}


PAWN = node("Actions\\T: Damage Until Down", kind="PawnTarget", target="pawn")
SCRATCH = node("Actions\\T: 10 damage", kind="MapTarget", target="map")
FIGHT = node("Actions\\Mental state...\\SocialFighting", kind="PawnTarget",
             target="pawn")

# The live `Add Game Condition...\Solar flare` subtree, read off the running
# game on 2026-09-12: `Permanent`, then 1..23 hours and `1 day` -- one child
# per 2500-tick step, which is what `AddGameCondition` builds.
FLARE_PATH = "Actions\\Add Game Condition...\\Solar flare"
FLARE_TREE = ([node(FLARE_PATH, kind="BrowseOnly", supported=False, children=True),
               node(FLARE_PATH + "\\Permanent")]
              + [node(FLARE_PATH + "\\%d hour%s" % (h, "" if h == 1 else "s"))
                 for h in range(1, 24)]
              + [node(FLARE_PATH + "\\1 day"),
                 node(debug.INCIDENTS + "\\SolarFlare", label="SolarFlare [NO]")])


class Bridge(object):
    """A stand-in for `rim.game` keyed by tool name.

    `nodes` is the whole live tree as {path: node}; get/children/search are
    answered off it, so a test says what EXISTS rather than what each call
    returns, and a wrong path in debug.py shows up as a refusal.
    """

    def __init__(self, nodes=(), pawns=(), alerts=(), execute=None):
        self.nodes = {n["path"]: n for n in nodes}
        self.pawns = list(pawns)
        self.alerts = list(alerts)
        self.calls = []
        self.execute_reply = execute or {"success": True, "path": "?"}

    def __call__(self, name, args=None, strict=True, timeout=600):
        args = args or {}
        self.calls.append((name, args))
        if name == debug.GET:
            got = self.nodes.get(args["path"])
            if got is None:
                return {"success": False, "message": "Could not find debug action."}
            out = {"success": True, "node": got, "devModeEnabled": False}
            if args.get("includeChildren"):
                out["children"] = self._kids(args["path"])
                out["childCount"] = len(out["children"])
            return out
        if name == debug.CHILDREN:
            kids = self._kids(args["path"])
            if args["path"] not in self.nodes and not kids:
                return {"success": False, "message": "no such node"}
            return {"success": True, "children": kids}
        if name == debug.SEARCH:
            q = args["query"].lower()
            hits = [n for p, n in sorted(self.nodes.items()) if q in p.lower()]
            return {"success": True, "matches": [{"node": n} for n in hits],
                    "totalMatchCount": len(hits)}
        if name == debug.EXECUTE:
            reply = dict(self.execute_reply)
            reply.setdefault("path", args.get("path"))
            return reply
        if name == debug.PAWNS:
            return {"success": True, "pawns": self.pawns}
        if name == debug.ALERTS:
            return {"success": True, "alerts": self.alerts}
        if name == debug.ROOTS:
            return {"success": True, "devModeEnabled": True, "roots": []}
        if name == debug.CLICK:
            return {"success": True}
        raise AssertionError("unexpected tool %r" % name)

    def _kids(self, path):
        pre = path + "\\"
        return [n for p, n in sorted(self.nodes.items())
                if p.startswith(pre) and "\\" not in p[len(pre):]]

    def fired(self):
        return [a for n, a in self.calls if n == debug.EXECUTE]


def run(bridge, argv, mode="workshop"):
    """`debug.main(argv)` against a mocked bridge. -> (exit code, output)."""
    with mock.patch.object(debug.rim, "game", side_effect=bridge), \
         mock.patch.object(debug.rim, "init"), \
         mock.patch("stream.current_mode", return_value=mode), \
         mock.patch("sys.stdout", new_callable=io.StringIO) as out:
        code = debug.main(argv)
    return code, out.getvalue()


class PathGrammarTests(unittest.TestCase):
    """Paths are node LABELS joined with a backslash, and the category is not
    in them (`DebugTabMenu_Actions.GenerateCacheForMethod`, 1.6)."""

    def test_separator_is_a_backslash(self):
        self.assertEqual("\\", debug.SEP)
        self.assertEqual("Actions\\Do incident", debug.INCIDENTS)

    def test_a_tool_action_carries_the_T_prefix(self):
        b = Bridge([PAWN, node("Actions\\Do incident")])
        code, out = run(b, ["down", "Ian"])
        self.assertEqual(0, code)
        self.assertIn("Actions\\T: Damage Until Down", out)

    def test_a_forward_slash_path_is_read_as_a_backslash_one(self):
        b = Bridge([PAWN])
        code, out = run(b, ["run", "Actions/T: Damage Until Down", "--pawn", "Ian"])
        self.assertEqual(0, code)
        self.assertIn("read", out)
        self.assertIn("backslash-separated", out)

    def test_a_path_that_resolves_as_typed_is_never_rewritten(self):
        """A label MAY contain a slash -- the game rewrites a backslash in a
        name to one -- so the swap only happens when the literal path misses."""
        odd = node("Actions\\Log map/world stats")
        b = Bridge([odd])
        code, out = run(b, ["run", "Actions\\Log map/world stats"])
        self.assertEqual(0, code)
        self.assertNotIn("read", out.split("path")[0])


class DryRunTests(unittest.TestCase):
    def test_nothing_fires_without_do(self):
        b = Bridge([PAWN])
        code, out = run(b, ["down", "Ian"])
        self.assertEqual([], b.fired())
        self.assertIn("DRY RUN", out)
        self.assertIn("Add --do", out)

    def test_the_dry_run_prints_the_path_and_the_target(self):
        b = Bridge([PAWN])
        _, out = run(b, ["down", "Ian"])
        self.assertIn("path   : Actions\\T: Damage Until Down", out)
        self.assertIn("pawn Ian", out)

    def test_do_fires_exactly_once_with_the_pawn_name(self):
        b = Bridge([PAWN])
        code, out = run(b, ["down", "Ian", "--do"])
        self.assertEqual(0, code)
        self.assertEqual([{"path": "Actions\\T: Damage Until Down",
                           "pawnName": "Ian"}], b.fired())
        self.assertIn("FIRED", out)

    def test_a_stable_id_goes_in_as_pawnId_not_pawnName(self):
        b = Bridge([PAWN])
        run(b, ["down", "Pawn_Ian77", "--do"])
        self.assertEqual("Pawn_Ian77", b.fired()[0]["pawnId"])
        self.assertNotIn("pawnName", b.fired()[0])

    def test_a_map_target_goes_in_as_x_and_z(self):
        b = Bridge([SCRATCH], pawns=[{"name": "Ian", "position": {"x": 12, "z": 34}}])
        run(b, ["wound", "Ian", "--do"])
        self.assertEqual({"path": "Actions\\T: 10 damage", "x": 12, "z": 34},
                         b.fired()[0])


class ResolvePawnTests(unittest.TestCase):
    """Found live 2026-09-12: `--pawn Ibex45901` was refused by the bridge
    while `--pawn "Ibex doe"` fired, though the help promises `<name|id>`.
    An id is now swapped for the row's name off one `home/list_pawns` read."""

    DOE = {"name": "Ibex doe", "thingId": "Ibex45901",
           "position": {"x": 1, "z": 2}}
    BUCK = {"name": "Ibex buck", "thingId": "Ibex45902",
            "position": {"x": 3, "z": 4}}

    def test_a_thing_id_is_resolved_to_the_name_before_firing(self):
        b = Bridge([PAWN], pawns=[self.DOE, self.BUCK])
        code, out = run(b, ["run", "Actions\\T: Damage Until Down",
                            "--pawn", "Ibex45901", "--do"])
        self.assertEqual(0, code)
        self.assertEqual("Ibex doe", b.fired()[0]["pawnName"])
        self.assertNotIn("pawnId", b.fired()[0])
        self.assertIn("resolved to 'Ibex doe'", out)

    def test_a_bare_number_matches_the_ids_numeric_tail(self):
        b = Bridge([PAWN], pawns=[self.DOE, self.BUCK])
        code, _ = run(b, ["run", "Actions\\T: Damage Until Down",
                          "--pawn", "45901", "--do"])
        self.assertEqual(0, code)
        self.assertEqual("Ibex doe", b.fired()[0]["pawnName"])

    def test_an_exact_name_is_passed_through_without_a_note(self):
        b = Bridge([PAWN], pawns=[self.DOE])
        code, out = run(b, ["run", "Actions\\T: Damage Until Down",
                            "--pawn", "Ibex doe", "--do"])
        self.assertEqual(0, code)
        self.assertEqual("Ibex doe", b.fired()[0]["pawnName"])
        self.assertNotIn("resolved", out)

    def test_an_id_whose_name_is_shared_refuses_before_firing(self):
        twin = dict(self.BUCK, name="Ibex doe")
        b = Bridge([PAWN], pawns=[self.DOE, twin])
        code, out = run(b, ["run", "Actions\\T: Damage Until Down",
                            "--pawn", "Ibex45901", "--do"])
        self.assertEqual(2, code)
        self.assertEqual([], b.fired())
        self.assertIn("share that name", out)

    def test_an_unknown_id_still_goes_to_the_bridge_as_given(self):
        b = Bridge([PAWN], pawns=[self.DOE])
        code, _ = run(b, ["run", "Actions\\T: Damage Until Down",
                          "--pawn", "Ibex99999", "--do"])
        self.assertEqual(0, code)
        self.assertEqual("Ibex99999", b.fired()[0]["pawnName"])

    def test_an_older_companion_id_inside_the_animals_block_still_resolves(self):
        old = {"name": "Ibex doe", "position": {"x": 1, "z": 2},
               "animals": {"thingId": "Ibex45901"}}
        b = Bridge([PAWN], pawns=[old])
        code, _ = run(b, ["run", "Actions\\T: Damage Until Down",
                          "--pawn", "Ibex45901", "--do"])
        self.assertEqual(0, code)
        self.assertEqual("Ibex doe", b.fired()[0]["pawnName"])


class LiveSessionTests(unittest.TestCase):
    """Workshop only: `modes\\live.md` forbids exactly this."""

    def test_a_live_session_refuses_the_do(self):
        b = Bridge([PAWN])
        code, out = run(b, ["down", "Ian", "--do"], mode="live")
        self.assertEqual(2, code)
        self.assertEqual([], b.fired())
        self.assertIn("workshop-only", out)
        self.assertIn("stream.py mode workshop", out)

    def test_a_live_session_still_allows_the_dry_run(self):
        b = Bridge([PAWN])
        code, out = run(b, ["down", "Ian"], mode="live")
        self.assertEqual(0, code)
        self.assertIn("DRY RUN", out)

    def test_an_unset_mode_warns_and_runs(self):
        b = Bridge([PAWN])
        code, out = run(b, ["down", "Ian", "--do"], mode=None)
        self.assertEqual(0, code)
        self.assertIn("no session mode is set", out)
        self.assertEqual(1, len(b.fired()))

    def test_every_write_says_the_save_is_now_dirty(self):
        b = Bridge([PAWN])
        _, out = run(b, ["down", "Ian", "--do"])
        self.assertIn("do NOT save over a real save", out.replace("Do NOT", "do NOT"))


class ResolutionTests(unittest.TestCase):
    """A shortcut never fires a path it has not confirmed exists."""

    def test_a_missing_action_refuses_and_names_find(self):
        b = Bridge([])                       # an empty tree
        code, out = run(b, ["manhunter", "--do"])
        self.assertEqual(2, code)
        self.assertEqual([], b.fired())
        self.assertIn("REFUSED, nothing fired", out)
        self.assertIn("python debug.py find ManhunterPack", out)

    def test_a_renamed_label_is_found_by_search_and_said_so(self):
        moved = node("Actions\\Incidents\\ManhunterPack")
        b = Bridge([moved])
        code, out = run(b, ["manhunter"])
        self.assertEqual(0, code)
        self.assertIn("resolved by search", out)
        self.assertIn("Actions\\Incidents\\ManhunterPack", out)

    def test_two_matches_refuse_with_both_listed(self):
        b = Bridge([node("Actions\\Mod A\\ManhunterPack"),
                    node("Actions\\Mod B\\ManhunterPack")])
        code, out = run(b, ["manhunter", "--do"])
        self.assertEqual(2, code)
        self.assertEqual([], b.fired())
        self.assertIn("ambiguous", out)
        self.assertIn("Actions\\Mod B\\ManhunterPack", out)

    def test_a_submenu_is_refused_rather_than_executed(self):
        sub = node("Actions\\Do incident", kind="BrowseOnly", supported=False,
                   children=True)
        b = Bridge([sub])
        code, out = run(b, ["run", "Actions\\Do incident", "--do"])
        self.assertEqual(2, code)
        self.assertEqual([], b.fired())
        self.assertIn("BrowseOnly", out)
        self.assertIn("debug.py show", out)

    def test_a_pawn_action_with_no_pawn_names_the_flag(self):
        b = Bridge([PAWN])
        code, out = run(b, ["run", "Actions\\T: Damage Until Down", "--do"])
        self.assertEqual(2, code)
        self.assertIn("needs a pawn target", out)
        self.assertIn("--pawn <name>", out)

    def test_a_map_action_with_no_cell_names_the_flag(self):
        b = Bridge([SCRATCH])
        code, out = run(b, ["run", "Actions\\T: 10 damage", "--do"])
        self.assertEqual(2, code)
        self.assertIn("needs a map cell", out)
        self.assertIn("--cell <x> <z>", out)


class IncidentTests(unittest.TestCase):
    """`Actions\\Do incident\\<IncidentDef>` -- the 1.6 IncidentsYielder."""

    def test_each_map_targeted_shortcut_reaches_its_own_incident_def(self):
        """Only `Map_PlayerHome` defs go through `Do incident`. The flare and
        the quest letter are World-targeted and route elsewhere -- see
        ConditionRouteTests and QuestLetterRouteTests."""
        tree = [node(debug.INCIDENTS + "\\" + d) for d in
                ("ManhunterPack", "TraderCaravanArrival", "OrbitalTraderArrival")]
        for argv, want in ((["manhunter"], "ManhunterPack"),
                           (["trader"], "TraderCaravanArrival"),
                           (["trader", "--orbital"], "OrbitalTraderArrival")):
            code, out = run(Bridge(tree), argv)
            self.assertEqual(0, code, argv)
            self.assertIn(debug.INCIDENTS + "\\" + want, out)


class NoLabelTests(unittest.TestCase):
    """`[NO]` is the only evidence that a node will do nothing.

    Live, 2026-09-12: `flare --do` printed `FIRED Actions\\Do incident\\SolarFlare`
    and no flare happened -- the bridge reported success and the game logged
    "Incident target is null or not allowed". `execution.supported` was true
    throughout, because it is about the delegate, not the target.
    """

    NO = node(debug.INCIDENTS + "\\ManhunterPack", label="ManhunterPack [NO]")

    def test_a_NO_node_refuses_before_the_dry_run_even_prints(self):
        b = Bridge([self.NO])
        code, out = run(b, ["manhunter"])
        self.assertEqual(2, code)
        self.assertEqual([], b.fired())
        self.assertNotIn("DRY RUN", out)
        self.assertIn("ManhunterPack [NO]", out)
        self.assertIn("cannot fire against the current target", out)
        self.assertIn("python debug.py find ManhunterPack", out)

    def test_a_NO_node_never_fires_with_do(self):
        b = Bridge([self.NO])
        code, out = run(b, ["manhunter", "--do"])
        self.assertEqual(2, code)
        self.assertEqual([], b.fired())

    def test_the_quest_menus_not_now_marker_counts_too(self):
        n = node("Actions\\Generate quest...\\*Natural random",
                 label="*Natural random [not now]")
        code, out = run(Bridge([n]), ["letter", "--do"])
        self.assertEqual(2, code)
        self.assertIn("[not now]", out)

    def test_anyway_overrides_it_and_says_so(self):
        b = Bridge([self.NO])
        code, out = run(b, ["manhunter", "--do", "--anyway"])
        self.assertEqual(0, code)
        self.assertEqual(1, len(b.fired()))
        self.assertIn("very likely a no-op", out)

    def test_a_raw_run_is_guarded_the_same_way(self):
        b = Bridge([self.NO])
        code, out = run(b, ["run", debug.INCIDENTS + "\\ManhunterPack", "--do"])
        self.assertEqual(2, code)
        self.assertEqual([], b.fired())


class ConditionRouteTests(unittest.TestCase):
    """The flare goes through `Add Game Condition...`, never `Do incident`.

    `SolarFlare` is `<targetTags><li>World</li></targetTags>`, and
    `DebugActionsIncidents.GetTarget()` can only hand it `Find.CurrentMap`
    through the bridge. `AddGameCondition` registers the GameConditionDef on
    the map's own `GameConditionManager` instead.
    """

    FLARE = FLARE_PATH
    TREE = FLARE_TREE

    def test_the_default_is_one_hour_of_solar_flare(self):
        code, out = run(Bridge(self.TREE), ["flare"])
        self.assertEqual(0, code)
        self.assertIn(self.FLARE + "\\1 hour", out)

    def test_hours_picks_the_nearest_offered(self):
        _, out = run(Bridge(self.TREE), ["flare", "--hours", "6"])
        self.assertIn(self.FLARE + "\\6 hours", out)

    def test_a_day_is_read_as_twenty_four_hours(self):
        _, out = run(Bridge(self.TREE), ["flare", "--hours", "30"])
        self.assertIn(self.FLARE + "\\1 day", out)
        self.assertIn("nearest offered is 24", out)

    def test_permanent_is_never_picked(self):
        for hours in ("1", "24", "100"):
            _, out = run(Bridge(self.TREE), ["flare", "--hours", hours])
            self.assertNotIn("Permanent", out)

    def test_the_dead_incident_route_is_not_used(self):
        b = Bridge(self.TREE)
        code, out = run(b, ["flare", "--do"])
        self.assertEqual(0, code)
        self.assertNotIn(debug.INCIDENTS + "\\SolarFlare", out)
        self.assertEqual(self.FLARE + "\\1 hour", b.fired()[0]["path"])

    def test_zero_hours_refuses(self):
        code, out = run(Bridge(self.TREE), ["flare", "--hours", "0"])
        self.assertEqual(2, code)
        self.assertIn("above zero", out)

    def test_a_condition_with_no_duration_children_refuses(self):
        b = Bridge([node(self.FLARE, kind="BrowseOnly", supported=False,
                         children=True),
                    node(self.FLARE + "\\Permanent")])
        code, out = run(b, ["flare", "--do"])
        self.assertEqual(2, code)
        self.assertEqual([], b.fired())
        self.assertIn("no `<n> hours` child", out)


class QuestLetterRouteTests(unittest.TestCase):
    """`GiveQuestBase` inherits `targetTags = [World]`, so
    `Do incident\\GiveQuest_Random` reads `[NO]` through the bridge; the one
    Direct leaf of `Generate quest...` is the route that sends a letter."""

    NATURAL = "Actions\\Generate quest...\\*Natural random"

    def test_letter_uses_the_natural_random_quest_leaf(self):
        b = Bridge([node(self.NATURAL),
                    node(debug.INCIDENTS + "\\GiveQuest_Random",
                         label="GiveQuest_Random [NO]")])
        code, out = run(b, ["letter", "--do"])
        self.assertEqual(0, code)
        self.assertEqual(self.NATURAL, b.fired()[0]["path"])
        self.assertIn("letters.py decide", out)


class RaidTests(unittest.TestCase):
    """`Execute raid with points...` has one `<n> points` child per entry in
    `DebugActionsUtility.PointsOptions(extended: true)`."""

    TREE = [node(debug.RAID_POINTS, kind="BrowseOnly", supported=False,
                 children=True)] + [
        node(debug.RAID_POINTS + "\\%d points" % p) for p in (20, 100, 200, 500, 1000)]

    def test_the_default_is_a_small_raid(self):
        code, out = run(Bridge(self.TREE), ["raid"])
        self.assertEqual(0, code)
        self.assertIn("200 points", out)

    def test_points_picks_the_nearest_offered_and_says_when_it_moved(self):
        code, out = run(Bridge(self.TREE), ["raid", "--points", "460"])
        self.assertIn("500 points", out)
        self.assertIn("nearest offered is 500", out)

    def test_an_exact_point_value_is_not_flagged_as_moved(self):
        _, out = run(Bridge(self.TREE), ["raid", "--points", "100"])
        self.assertIn("100 points", out)
        self.assertNotIn("nearest offered", out)


class WoundTests(unittest.TestCase):
    PAWNS = [{"name": "Ian", "position": {"x": 5, "z": 6}},
             {"name": "Lucas", "position": {"x": 9, "z": 9}}]

    def test_a_scratch_is_ten_damage_at_the_pawns_own_cell(self):
        b = Bridge([SCRATCH], pawns=self.PAWNS)
        code, out = run(b, ["wound", "Ian"])
        self.assertEqual(0, code)
        self.assertIn("Actions\\T: 10 damage", out)
        self.assertIn("cell 5,6", out)

    def test_the_scratch_warns_that_the_cell_is_not_just_the_pawn(self):
        _, out = run(Bridge([SCRATCH], pawns=self.PAWNS), ["wound", "Ian"])
        self.assertIn("hits EVERYTHING standing there", out)

    def test_the_scratch_says_it_will_stop_supervised_play(self):
        # Live 2026-09-12: `wound <pawn> --severity scratch --do` fired
        # `T: 10 damage` and the guard stopped with `colonist_injury ... took a
        # new wound worth 10.00 hit points`. The guard's serious test is
        # `>= 10f`, so vanilla's smallest damage action sits exactly ON it and
        # the old note promised the opposite.
        _, out = run(Bridge([SCRATCH], pawns=self.PAWNS), ["wound", "Ian"])
        self.assertIn("exactly the injury guard's serious threshold", out)
        self.assertIn("STOPS supervised play", out)
        self.assertIn("no smaller damage action", out)

    def test_serious_is_the_hidden_submenu_action(self):
        n = node(debug.MORE + "\\T: Damage Until Incapable Of Manipulation",
                 kind="PawnTarget", target="pawn")
        code, out = run(Bridge([n]), ["wound", "Ian", "--severity", "serious"])
        self.assertEqual(0, code)
        self.assertIn("Show more actions", out)
        self.assertIn("pawn Ian", out)

    def test_an_unknown_severity_refuses(self):
        code, out = run(Bridge([SCRATCH]), ["wound", "Ian", "--severity", "fatal"])
        self.assertEqual(2, code)
        self.assertIn("scratch or serious", out)

    def test_an_unknown_pawn_refuses_and_lists_who_is_there(self):
        code, out = run(Bridge([SCRATCH], pawns=self.PAWNS), ["wound", "Nobody"])
        self.assertEqual(2, code)
        self.assertIn("on the map: Ian, Lucas", out)


class HealTests(unittest.TestCase):
    HEAL = node("Actions\\T: Heal random injury (10)", kind="PawnTarget",
                target="pawn")

    def test_times_repeats_the_same_call(self):
        b = Bridge([self.HEAL])
        code, out = run(b, ["heal", "Ian", "--times", "3", "--do"])
        self.assertEqual(0, code)
        self.assertEqual(3, len(b.fired()))

    def test_a_silly_times_refuses_before_anything_fires(self):
        b = Bridge([self.HEAL])
        code, out = run(b, ["heal", "Ian", "--times", "500", "--do"])
        self.assertEqual(2, code)
        self.assertEqual([], b.fired())


class FightTests(unittest.TestCase):
    PAWNS = [{"name": "Ian", "position": {"x": 5, "z": 6}},
             {"name": "Lucas", "position": {"x": 9, "z": 9}}]

    def test_the_dry_run_names_both_stages(self):
        code, out = run(Bridge([FIGHT], pawns=self.PAWNS),
                        ["fight", "Ian", "Lucas"])
        self.assertEqual(0, code)
        self.assertIn("SocialFighting", out)
        self.assertIn("9,9", out)

    def test_the_second_stage_clicks_pawn_Bs_cell(self):
        b = Bridge([FIGHT], pawns=self.PAWNS,
                   execute={"success": True,
                            "followUp": {"tool": "rimworld/click_cell",
                                         "message": "another map-target stage"},
                            "effects": {"debugToolActiveAfter": True}})
        code, out = run(b, ["fight", "Ian", "Lucas", "--do"])
        self.assertEqual(0, code)
        clicks = [a for n, a in b.calls if n == debug.CLICK]
        self.assertEqual([{"x": 9, "z": 9, "button": "left"}], clicks)
        self.assertIn("stage 2", out)

    def test_no_second_stage_armed_clicks_nothing(self):
        b = Bridge([FIGHT], pawns=self.PAWNS,
                   execute={"success": True, "effects": {"debugToolActiveAfter": False}})
        code, out = run(b, ["fight", "Ian", "Lucas", "--do"])
        self.assertEqual([], [a for n, a in b.calls if n == debug.CLICK])
        self.assertIn("no second stage was armed", out)

    def test_one_pawn_is_refused(self):
        code, out = run(Bridge([FIGHT]), ["fight", "Ian", "--do"])
        self.assertEqual(2, code)
        self.assertIn("needs two pawns", out)


class SpawnTests(unittest.TestCase):
    TREE = [node("Actions\\Spawn thing...", kind="BrowseOnly", supported=False,
                 children=True),
            node("Actions\\Spawn thing...\\Silver", kind="MapTarget", target="map"),
            node("Actions\\Spawn Pawn...", kind="BrowseOnly", supported=False,
                 children=True),
            node("Actions\\Spawn Pawn...\\Wolverine", kind="MapTarget", target="map")]

    def test_a_thing_def_resolves_under_spawn_thing(self):
        code, out = run(Bridge(self.TREE), ["spawn", "Silver", "10", "20"])
        self.assertEqual(0, code)
        self.assertIn("Actions\\Spawn thing...\\Silver", out)
        self.assertIn("cell 10,20", out)

    def test_a_pawn_kind_falls_through_to_spawn_pawn(self):
        """The wolverine `play.py --ignore-predator` needs is a PawnKindDef."""
        code, out = run(Bridge(self.TREE), ["spawn", "Wolverine", "10", "20"])
        self.assertEqual(0, code)
        self.assertIn("Actions\\Spawn Pawn...\\Wolverine", out)

    def test_a_missing_def_refuses_without_firing(self):
        b = Bridge(self.TREE)
        code, out = run(b, ["spawn", "Skeleton", "10", "20", "--do"])
        self.assertEqual(2, code)
        self.assertEqual([], b.fired())

    def test_a_non_numeric_cell_refuses(self):
        code, out = run(Bridge(self.TREE), ["spawn", "Silver", "x", "20"])
        self.assertEqual(2, code)
        self.assertIn("whole numbers", out)


class AlertTests(unittest.TestCase):
    def test_a_live_critical_is_reported_and_nothing_is_raised(self):
        b = Bridge([PAWN], alerts=[{"label": "Colonist needs rescue",
                                    "priority": "Critical"}])
        code, out = run(b, ["alert", "--do"])
        self.assertEqual(2, code)
        self.assertEqual([], b.fired())
        self.assertIn("already up", out)
        self.assertIn("alerts.py", out)

    def test_with_no_critical_and_no_pawn_it_names_the_command(self):
        b = Bridge([PAWN], alerts=[{"label": "Idle", "priority": "Medium"}])
        code, out = run(b, ["alert", "--do"])
        self.assertEqual(2, code)
        self.assertIn("debug.py alert --pawn <colonist> --do", out)

    def test_with_a_pawn_it_downs_them_to_raise_the_critical(self):
        b = Bridge([PAWN], alerts=[])
        code, out = run(b, ["alert", "--pawn", "Ian", "--do"])
        self.assertEqual(0, code)
        self.assertEqual("Actions\\T: Damage Until Down", b.fired()[0]["path"])
        self.assertIn("Alert_ColonistNeedsRescuing", out)


class RenameTests(unittest.TestCase):
    """1.6 has no debug action that opens `Dialog_NamePawn`; the pencil does."""

    def test_rename_always_refuses_and_names_the_route(self):
        b = Bridge([])
        code, out = run(b, ["rename", "Ian"])
        self.assertEqual(2, code)
        self.assertEqual([], b.fired())
        self.assertIn("no debug action", out)
        self.assertIn("NamePawnDialog", out)
        self.assertIn("dialog.py", out)


class FindTests(unittest.TestCase):
    def test_find_prints_the_kind_and_whether_it_is_supported(self):
        b = Bridge([PAWN, node("Actions\\Do incident", kind="BrowseOnly",
                               supported=False, children=True)])
        code, out = run(b, ["find", "incident"])
        self.assertEqual(0, code)
        self.assertIn("BrowseOnly", out)
        self.assertIn("Actions\\Do incident", out)

    def test_a_miss_exits_non_zero_and_says_so(self):
        code, out = run(Bridge([PAWN]), ["find", "nonesuch"])
        self.assertEqual(1, code)
        self.assertIn("no debug action matches", out)

    def test_find_with_no_words_refuses(self):
        code, out = run(Bridge([]), ["find"])
        self.assertEqual(2, code)
        self.assertIn("needs something to search for", out)

    def test_a_node_hidden_by_game_state_is_marked_not_assumed_absent(self):
        b = Bridge([node("Actions\\T: Kill", visible=False)])
        _, out = run(b, ["find", "Kill"])
        self.assertIn("not available in this game state", out)


class ShowTests(unittest.TestCase):
    def test_show_lists_the_children(self):
        b = Bridge([node("Actions\\Do incident", kind="BrowseOnly",
                         supported=False, children=True),
                    node("Actions\\Do incident\\SolarFlare"),
                    node("Actions\\Do incident\\ManhunterPack")])
        code, out = run(b, ["show", "Actions\\Do incident"])
        self.assertEqual(0, code)
        self.assertIn("SolarFlare", out)
        self.assertIn("ManhunterPack", out)
        self.assertIn("2 children", out)

    def test_show_on_a_miss_refuses_and_names_find(self):
        code, out = run(Bridge([]), ["show", "Actions\\Nope"])
        self.assertEqual(2, code)
        self.assertIn("debug.py find", out)


class CliTests(unittest.TestCase):
    def test_an_unknown_flag_refuses_rather_than_being_swallowed(self):
        code, out = run(Bridge([PAWN]), ["down", "Ian", "--force"])
        self.assertEqual(2, code)
        self.assertIn("unknown flag", out)

    def test_a_flag_with_no_value_refuses(self):
        code, out = run(Bridge([PAWN]), ["raid", "--points"])
        self.assertEqual(2, code)
        self.assertIn("needs a value", out)

    def test_bare_invocation_prints_the_docstring(self):
        code, out = run(Bridge([]), [])
        self.assertEqual(0, code)
        self.assertIn("Workshop only", out)

    def test_the_docstring_says_it_is_workshop_only_and_not_for_a_live_save(self):
        self.assertIn("Workshop only", debug.__doc__)
        self.assertIn("Live", debug.__doc__)
        self.assertIn("a test fixture", debug.__doc__)
        self.assertIn("without `rimworld/save_game`", debug.__doc__)

    def test_devmode_reports_the_flag_and_the_manual_route(self):
        code, out = run(Bridge([]), ["devmode"])
        self.assertEqual(0, code)
        self.assertIn("Prefs.DevMode: True", out)
        self.assertIn("Development mode", out)


class ReplyTests(unittest.TestCase):
    def test_a_bridge_refusal_is_reported_and_exits_non_zero(self):
        b = Bridge([PAWN], execute={"success": False, "message": "no such pawn"})
        code, out = run(b, ["down", "Ian", "--do"])
        self.assertEqual(1, code)
        self.assertIn("REFUSED by the bridge", out)
        self.assertIn("no such pawn", out)

    def test_an_opened_window_is_named_with_the_tool_that_answers_it(self):
        b = Bridge([PAWN], execute={
            "success": True,
            "effects": {"openedWindowTypes": ["LudeonTK.Dialog_DebugOptionListLister"]}})
        _, out = run(b, ["down", "Ian", "--do"])
        self.assertIn("Dialog_DebugOptionListLister", out)
        self.assertIn("python ui.py", out)

    def test_a_string_reply_is_named_rather_than_crashing(self):
        b = Bridge([PAWN], execute={})
        with mock.patch.object(debug, "execute", return_value="not json"), \
             mock.patch.object(debug.rim, "game", side_effect=b), \
             mock.patch.object(debug.rim, "init"), \
             mock.patch("stream.current_mode", return_value="workshop"), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = debug.main(["down", "Ian", "--do"])
        self.assertEqual(1, code)
        self.assertIn("not a payload", out.getvalue())


if __name__ == "__main__":
    unittest.main()
