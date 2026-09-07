"""Mock-only tests for order.py; no running game is contacted.

The point of this file is the exit code and the refusal text. `order.py` is a
thin CLI, and the way it fails is the whole reason it exists: on 2026-09-04 a
fork read one refusal as a permanent limitation and stopped acting. A refusal
here must always name the game's own errorKind and always exit non-zero.

A read and a write must also never print the same words: only a real order
says ORDER ISSUED, and a dry run or a resolve says DRY RUN.
"""
import io
import unittest
from unittest import mock

import order


def reply(success=True, **kw):
    r = {"success": success, "action": "attack",
         "pawn": {"thingId": "Thing_Human1", "name": "Longhoff",
                  "drafted": True, "autoDrafted": False, "downed": False,
                  "dead": False, "incapableOfViolence": False,
                  "canBeDrafted": True, "weapon": None},
         "target": {"thingId": "Thing_Wolf_Timber334862", "name": "timber wolf",
                    "idForms": ["Thing_Wolf_Timber334862", "334862"],
                    "hostileToPlayer": False, "downed": False, "predator": True,
                    "faction": None, "distance": 3, "reachable": True},
         "job": {"def": "AttackMelee", "verified": True},
         "watch": {"shown": True, "selected": True, "closesAfterSeconds": 6}}
    r.update(kw)
    return r


def clock_reply(paused=False):
    """What `home/status` says about the clock. Running unless asked otherwise.

    Every order report now reads this before it claims anything happened; see
    clock.py. The default is a RUNNING clock so the tests below go on asserting
    what an order that really lands prints.
    """
    return {"success": True,
            "time": {"paused": paused, "forcePaused": False,
                     "timeSpeed": "Normal", "ticksGame": 5000}}


class OrderCliTests(unittest.TestCase):
    def run_cli(self, argv, answer, paused=False):
        calls = []
        def game(tool, params=None, strict=True):
            calls.append((tool, params))
            if tool == order.TOOL:
                return answer(params) if callable(answer) else answer
            if tool == order.clock.TOOL:
                return clock_reply(paused)
            return {"success": True}
        with mock.patch.object(order.rim, "game", side_effect=game), \
             mock.patch.object(order.rim, "init"), \
             mock.patch.object(order, "check_modal"), \
             mock.patch.object(order, "combat_session", return_value=None), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = order.main(argv)
        return code, out.getvalue(), calls

    def ordered(self, calls):
        """Only the home/order calls -- the clock read is not one of them."""
        return [p for t, p in calls if t == order.TOOL]

    def test_a_non_hostile_predator_is_ordered_and_reported_as_such(self):
        code, text, calls = self.run_cli(
            ["attack", "Longhoff", "334862"], reply())
        self.assertEqual(0, code)
        self.assertIn("ORDER ISSUED", text)
        self.assertNotIn("DRY RUN", text)
        self.assertIn("hostile no", text)
        self.assertEqual("attack", calls[0][1]["action"])
        self.assertEqual("334862", calls[0][1]["target"])

    def test_every_refusal_names_its_kind_and_exits_non_zero(self):
        code, text, _ = self.run_cli(
            ["attack", "Finn", "wolf"],
            reply(success=False, errorKind="incapable_of_violence",
                  error="Finn is incapable of Violence", job=None))
        self.assertEqual(1, code)
        self.assertIn("ORDER REFUSED  incapable_of_violence", text)

    def test_ambiguity_prints_the_candidates_with_their_ids(self):
        code, text, _ = self.run_cli(
            ["attack", "Longhoff", "Rat"],
            reply(success=False, errorKind="ambiguous", error="2 things",
                  target=None, job=None,
                  candidates=[{"thingId": "Thing_Rat361788", "name": "Rat",
                               "defName": "Rat", "position": {"x": 1, "z": 2},
                               "distance": 4}]))
        self.assertEqual(1, code)
        self.assertIn("Thing_Rat361788", text)

    def test_dry_run_asks_and_never_watches_or_mutates(self):
        code, text, calls = self.run_cli(
            ["attack", "Longhoff", "wolf", "--dry-run", "--no-watch"],
            reply(wouldIssue=True))
        self.assertEqual(0, code)
        self.assertIn("DRY RUN", text)
        self.assertNotIn("ORDER ISSUED", text)
        self.assertTrue(calls[0][1]["dryRun"])
        self.assertFalse(calls[0][1]["watch"])

    def test_an_open_combat_session_sends_drafting_verbs_to_combat_py(self):
        with mock.patch.object(order.rim, "init"), \
             mock.patch.object(order, "combat_session", return_value={"active": True}), \
             mock.patch.object(order.rim, "game",
                               side_effect=AssertionError("called the game")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = order.main(["attack", "Longhoff", "wolf"])
        self.assertEqual(1, code)
        self.assertIn("python combat.py attack", out.getvalue())

    def test_a_work_verb_inside_a_session_can_never_draft(self):
        code, _, calls = self.run_cli(["haul", "Ada", "steel"], reply())
        self.assertNotIn("draft", self.ordered(calls)[0])
        sent = []

        def game(tool, params=None, strict=True):
            sent.append((tool, params))
            return clock_reply() if tool == order.clock.TOOL else reply()

        with mock.patch.object(order, "combat_session", return_value={"active": True}), \
             mock.patch.object(order.rim, "init"), \
             mock.patch.object(order, "check_modal"), \
             mock.patch.object(order.rim, "game", side_effect=game), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            order.main(["haul", "Ada", "steel"])
        self.assertFalse(self.ordered(sent)[0]["draft"])

    def test_tend_goes_to_combat_py_because_ground_tending_drafts(self):
        # `FloatMenuOptionProvider_DraftedTend`: the option only exists while
        # the doctor is drafted, so tend creates a cleanup obligation and the
        # ledger has to be the one to hold it.
        with mock.patch.object(order.rim, "init"), \
             mock.patch.object(order, "combat_session", return_value={"active": True}), \
             mock.patch.object(order.rim, "game",
                               side_effect=AssertionError("called the game")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = order.main(["tend", "Finn", "Octave"])
        self.assertEqual(1, code)
        self.assertIn("python combat.py tend", out.getvalue())

    def test_rescue_also_routes_to_the_ledger_during_a_session(self):
        with mock.patch.object(order.rim, "init"), \
             mock.patch.object(order, "combat_session", return_value={"active": True}), \
             mock.patch.object(order.rim, "game",
                               side_effect=AssertionError("called the game")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = order.main(["rescue", "Longhoff", "Octave"])
        self.assertEqual(1, code)
        self.assertIn("python combat.py rescue", out.getvalue())

    def ground_probe(self, drafted=False):
        """A dry `tend` probe that says: patient on the ground, drafted path."""
        row = reply(action="tend", job=None,
                    wouldIssue={"def": "TendPatient", "tendPath": "drafted",
                                "draftedTend": True})
        row["pawn"] = dict(row["pawn"], name="Finn", drafted=drafted)
        row["target"] = dict(row["target"], name="Octave")
        return row

    def tend_cli(self, argv, drafted=False, rescue_ok=True, paused=False):
        """`order.py tend` against a patient lying on the ground."""
        probe = self.ground_probe(drafted)

        def answer(params):
            if params.get("action") == "rescue":
                if not rescue_ok:
                    return reply(success=False, action="rescue",
                                 errorKind="not_reachable",
                                 error="Finn cannot reach Octave.")
                return reply(action="rescue",
                             job={"def": "Rescue", "verified": True})
            if params.get("dryRun"):
                return probe
            return reply(action="tend",
                         job={"def": "TendPatient", "verified": True})

        return self.run_cli(argv, answer, paused=paused)

    def test_ground_tend_carries_the_patient_to_a_bed_instead_of_refusing(self):
        # Turn 18: this printed a refusal and the patient went untended. Rescue
        # needs no draft either way, so it is the route with nothing to undo.
        code, text, calls = self.tend_cli(["tend", "Finn", "Octave"])
        self.assertEqual(0, code)
        self.assertIn("ROUTE: RESCUE", text)
        actions = [p["action"] for p in self.ordered(calls)]
        self.assertEqual(["tend", "rescue"], actions)
        probe, rescue = self.ordered(calls)
        self.assertTrue(probe["dryRun"])
        self.assertFalse(probe["watch"])
        self.assertNotIn("dryRun", rescue)
        self.assertFalse(rescue["draft"])
        self.assertIn("python order.py tend Finn Octave", text)

    def test_an_already_drafted_doctor_tends_where_they_lie(self):
        # No draft change means no new obligation: this is exactly the job
        # vanilla's own Tend option issues.
        code, text, calls = self.tend_cli(["tend", "Finn", "Octave"],
                                          drafted=True)
        self.assertEqual(0, code)
        self.assertIn("TEND WHERE THEY LIE", text)
        self.assertIn("ALREADY drafted", text)
        issued = self.ordered(calls)[-1]
        self.assertEqual("tend", issued["action"])
        self.assertTrue(issued["allowPersistentDraft"])

    def test_no_rescue_refuses_but_still_prints_every_route(self):
        code, text, calls = self.tend_cli(
            ["tend", "Finn", "Octave", "--no-rescue"])
        self.assertEqual(1, code)
        self.assertEqual(["tend"], [p["action"] for p in self.ordered(calls)])
        self.assertIn("python combat.py begin", text)
        self.assertIn("python order.py rescue Finn Octave", text)
        self.assertIn("--draft", text)

    def test_in_bed_tend_outside_session_probes_then_issues(self):
        def answer(params):
            if params.get("dryRun"):
                return reply(action="tend", job=None,
                             wouldIssue={"def": "TendPatient",
                                         "tendPath": "work",
                                         "draftedTend": False})
            return reply(action="tend", job={"def": "TendPatient",
                                              "verified": True})
        code, text, calls = self.run_cli(["tend", "Finn", "Octave"], answer)
        self.assertEqual(0, code)
        sent = self.ordered(calls)
        self.assertEqual(2, len(sent))
        self.assertTrue(sent[0]["dryRun"])
        self.assertNotIn("dryRun", sent[1])
        self.assertNotIn("allowPersistentDraft", sent[1])
        self.assertIn("ORDER ISSUED", text)

    def test_work_prints_the_bill_it_landed_on(self):
        code, text, calls = self.run_cli(
            ["work", "Ada", "TableStonecutter@126,140"],
            reply(action="work", job={"def": "DoBill", "verified": True,
                                      "bill": "Cut stone blocks"}))
        self.assertEqual(0, code)
        self.assertIn("bill Cut stone blocks", text)
        self.assertEqual("TableStonecutter@126,140", calls[0][1]["target"])

    def menu_cli(self, argv, options, menu_id=7, target_row=None, paused=False):
        """Run a menu/do/force command against a mocked float menu."""
        row = target_row or {"thingId": "Thing_Human9", "name": "Octave",
                             "kindDef": "Colonist", "position": {"x": 5, "z": 6}}
        calls = []

        def game(tool, params=None, strict=True):
            calls.append((tool, params))
            if tool == order.TOOL:
                return reply(target=row)
            if tool == "rimworld/open_context_menu":
                return {"success": True, "menuId": menu_id, "options": options,
                        "optionCount": len(options), "target": "cell 5,6"}
            if tool == "rimworld/get_selection_semantics":
                return {"hasSelection": True, "selectedCount": 1,
                        "selectedObjects": [{"kind": "Zone", "label": "Stockpile 1",
                                             "id": "Zone_3"}]}
            if tool == order.clock.TOOL:
                return clock_reply(paused)
            return {"success": True}

        with mock.patch.object(order.rim, "game", side_effect=game) as g, \
             mock.patch.object(order.rim, "init"), \
             mock.patch.object(order, "check_modal"), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = order.main(argv)
        return code, out.getvalue(), g, calls

    def test_menu_reads_every_option_with_its_reason_and_closes_after(self):
        options = [{"index": 1, "label": "Rescue Octave", "disabled": False},
                   {"index": 2, "label": "Tend Octave (without medicine)",
                    "disabled": True, "disabledReason": "no free hands"}]
        code, text, g, _ = self.menu_cli(["menu", "Finn", "Octave"], options)
        self.assertEqual(0, code)
        self.assertIn("no free hands", text)
        g.assert_any_call("rimworld/close_context_menu", {}, strict=False)

    def test_menu_never_falls_back_to_a_raw_click(self):
        """A read may not become a write: right_click_cell takes the DIRECT
        action for the click, so menu must never reach for it."""
        code, text, _, calls = self.menu_cli(["menu", "Finn", "Octave"], [])
        self.assertEqual(0, code)
        tools = [t for t, _ in calls]
        self.assertIn("rimworld/open_context_menu", tools)
        self.assertNotIn("rimworld/right_click_cell", tools)
        self.assertNotIn("rimworld/click_cell", tools)

    def test_menu_stops_when_the_game_would_take_the_direct_action(self):
        code, text, _, calls = self.menu_cli(["menu", "Finn", "Octave"], [],
                                             menu_id=0)
        self.assertEqual(1, code)
        self.assertIn("NO MENU OPENED", text)
        self.assertNotIn("rimworld/right_click_cell", [t for t, _ in calls])

    def test_a_full_label_is_never_truncated(self):
        long_label = ("Cannot work on slate tile (blueprint): Construction "
                      "work has been disabled for this colonist")
        code, text, _, _ = self.menu_cli(
            ["menu", "Finn", "Octave"],
            [{"index": 1, "label": long_label, "disabled": True}])
        self.assertEqual(0, code)
        self.assertIn(long_label, text)

    def test_a_bare_cell_pair_is_one_target_and_needs_no_resolve(self):
        code, text, _, calls = self.menu_cli(
            ["menu", "Finn", "140", "152"],
            [{"index": 1, "label": "Prioritize hauling", "disabled": False}])
        self.assertEqual(0, code)
        opened = [p for t, p in calls if t == "rimworld/open_context_menu"][0]
        self.assertEqual((140, 152), (opened["x"], opened["z"]))

    def test_defname_at_cell_addresses_the_menu_by_that_cell(self):
        code, _, _, calls = self.menu_cli(
            ["menu", "Finn", "Wall@126,140"],
            [{"index": 1, "label": "Prioritize deconstructing wall",
              "disabled": False}])
        self.assertEqual(0, code)
        opened = [p for t, p in calls if t == "rimworld/open_context_menu"][0]
        self.assertEqual((126, 140), (opened["x"], opened["z"]))

    def test_a_thing_target_is_resolved_to_a_cell_not_passed_as_an_id(self):
        """open_context_menu declares no targetId; an id it does not know is
        read as cell 0,0."""
        row = {"thingId": "Thing_Wall12", "name": "wooden wall",
               "position": {"x": 118, "z": 133}}
        code, _, _, calls = self.menu_cli(
            ["menu", "Finn", "Thing_Wall12"],
            [{"index": 1, "label": "Prioritize deconstructing wooden wall",
              "disabled": False}], target_row=row)
        self.assertEqual(0, code)
        opened = [p for t, p in calls if t == "rimworld/open_context_menu"][0]
        self.assertEqual((118, 133), (opened["x"], opened["z"]))
        self.assertNotIn("targetId", opened)

    def test_menu_prints_what_actually_got_selected(self):
        code, text, _, _ = self.menu_cli(["menu", "Finn", "140", "152"], [])
        self.assertIn("selected: Zone Stockpile 1", text)

    def test_force_is_a_dry_run_by_default(self):
        options = [{"index": 1, "label": "Prioritize deconstructing wooden wall",
                    "disabled": False}]
        code, text, _, calls = self.menu_cli(
            ["force", "Finn", "118", "133"], options)
        self.assertEqual(0, code)
        self.assertIn("DRY RUN", text)
        self.assertNotIn("rimworld/execute_context_menu_option",
                         [t for t, _ in calls])

    def test_force_do_runs_the_single_prioritize_option_by_label(self):
        options = [{"index": 1, "label": "Prioritize deconstructing wooden wall",
                    "disabled": False},
                   {"index": 2, "label": "Go here", "disabled": False}]
        code, text, _, calls = self.menu_cli(
            ["force", "Finn", "118", "133", "--do"], options)
        self.assertEqual(0, code)
        self.assertIn("ORDER ISSUED", text)
        ran = [p for t, p in calls
               if t == "rimworld/execute_context_menu_option"]
        self.assertEqual([{"label": "Prioritize deconstructing wooden wall"}], ran)

    def test_force_matches_a_label_substring(self):
        options = [{"index": 1, "label": "Prioritize mining", "disabled": False},
                   {"index": 2, "label": "Prioritize hauling", "disabled": False}]
        code, text, _, calls = self.menu_cli(
            ["force", "Finn", "118", "133", "hauling", "--do"], options)
        self.assertEqual(0, code)
        ran = [p for t, p in calls
               if t == "rimworld/execute_context_menu_option"]
        self.assertEqual([{"label": "Prioritize hauling"}], ran)

    def test_force_refuses_two_prioritize_options_rather_than_guessing(self):
        options = [{"index": 1, "label": "Prioritize mining", "disabled": False},
                   {"index": 2, "label": "Prioritize hauling", "disabled": False}]
        code, text, _, calls = self.menu_cli(
            ["force", "Finn", "118", "133", "--do"], options)
        self.assertEqual(1, code)
        self.assertNotIn("rimworld/execute_context_menu_option",
                         [t for t, _ in calls])

    def test_menu_do_is_an_alias_for_force(self):
        options = [{"index": 1, "label": "Prioritize hauling", "disabled": False}]
        code, text, _, _ = self.menu_cli(["menu-do", "Finn", "118", "133"],
                                         options)
        self.assertEqual(0, code)
        self.assertIn("DRY RUN", text)

    def test_do_refuses_an_ambiguous_option_and_closes_the_menu(self):
        options = [{"index": 1, "label": "Tend Octave", "disabled": False},
                   {"index": 2, "label": "Tend Octave (without medicine)",
                    "disabled": False}]
        code, text, g, _ = self.menu_cli(
            ["do", "Finn", "Octave", "tend octave"], options)
        self.assertEqual(1, code)
        self.assertIn("matched 2 enabled options", text)
        g.assert_any_call("rimworld/close_context_menu", {}, strict=False)

    def test_an_accepted_order_with_no_job_is_flagged_not_reported_as_done(self):
        code, text, _ = self.run_cli(["haul", "Ada", "steel"],
                                     reply(action="haul", job=None))
        self.assertEqual(0, code)
        self.assertIn("ORDER ISSUED", text)
        self.assertIn("NO JOB", text)

    def test_resolve_is_always_a_dry_run_and_never_says_issued(self):
        code, text, calls = self.run_cli(["resolve", "Ada", "steel"], reply())
        self.assertEqual(0, code)
        self.assertTrue(calls[0][1]["dryRun"])
        self.assertIn("DRY RUN", text)
        self.assertNotIn("ORDER ISSUED", text)

    def test_work_on_a_non_bill_giver_points_at_force(self):
        code, text, _ = self.run_cli(
            ["work", "Ada", "Thing_Wall12"],
            reply(success=False, errorKind="bad_arguments", job=None,
                  error="wooden wall is not a bill giver (it does not implement "
                        "IBillGiver), so no bills can be done at it."))
        self.assertEqual(1, code)
        self.assertIn("order.py force", text)


class StoppedClockTests(unittest.TestCase):
    """A VERIFIED line on a stopped clock proves nothing. 2026-09-07.

    Four attack orders read back a job, printed success, and the wolf lived,
    because the clock was not running. These tests are about the WORDS: an
    order that queued must never be reported in the words of an order that
    happened.
    """

    # The harness lives on OrderCliTests; borrowing the methods rather than
    # subclassing it keeps its 33 tests from being re-run under three names.
    run_cli = OrderCliTests.run_cli
    menu_cli = OrderCliTests.menu_cli
    ordered = OrderCliTests.ordered


    def test_a_paused_clock_says_queued_not_issued(self):
        code, text, _ = self.run_cli(["attack", "Longhoff", "334862"],
                                     reply(), paused=True)
        self.assertEqual(0, code)                # it queued; that is not a failure
        self.assertIn("ORDER QUEUED", text)
        self.assertNotIn("ORDER ISSUED", text)
        self.assertIn("CLOCK IS STOPPED", text)
        self.assertIn("python play.py start", text)

    def test_a_running_clock_keeps_the_old_wording_exactly(self):
        _, text, _ = self.run_cli(["attack", "Longhoff", "334862"], reply())
        self.assertIn("ORDER ISSUED", text)
        self.assertIn("verified by the game: the pawn's job is now AttackMelee",
                      text)
        self.assertNotIn("CLOCK IS STOPPED", text)

    def test_force_do_on_a_paused_clock_says_queued(self):
        options = [{"index": 1, "label": "Prioritize hauling", "disabled": False}]
        code, text, _, _ = self.menu_cli(
            ["force", "Finn", "118", "133", "--do"], options, paused=True)
        self.assertEqual(0, code)
        self.assertIn("ORDER QUEUED", text)
        self.assertIn("CLOCK IS STOPPED", text)


class DoFlagTests(unittest.TestCase):
    """`--do` is accepted by every subcommand. 2026-09-07 cost three turns to it."""

    # The harness lives on OrderCliTests; borrowing the methods rather than
    # subclassing it keeps its 33 tests from being re-run under three names.
    run_cli = OrderCliTests.run_cli
    menu_cli = OrderCliTests.menu_cli
    ordered = OrderCliTests.ordered


    ISSUING = (["goto", "Ada", "140", "152"],
               ["haul", "Ada", "steel"],
               ["tend", "Finn", "Octave"],
               ["attack", "Longhoff", "334862"],
               ["rescue", "Finn", "Octave"],
               ["equip", "Ada", "rifle"],
               ["work", "Ada", "TableStonecutter@126,140"],
               ["draft", "Ada"],
               ["undraft", "Ada"])

    def test_every_issuing_verb_accepts_do_without_being_refused(self):
        for argv in self.ISSUING:
            with self.subTest(verb=argv[0]):
                answer = reply(action=argv[0],
                               job={"def": "Goto", "verified": True},
                               wouldIssue=None)
                code, text, _ = self.run_cli(argv + ["--do"], answer)
                self.assertEqual(0, code, text)
                self.assertIn("ORDER ISSUED", text)
                self.assertIn("--do noted", text)

    def test_do_is_never_a_parser_error(self):
        # The actual 2026-09-07 failure was argparse, BEFORE any bridge call:
        # `unrecognized arguments: --do`, SystemExit(2), not a refusal. So this
        # asserts on the parser alone, with the bridge stubbed out entirely --
        # a test that reaches a live game to prove a flag parses is a test that
        # can issue an order by accident.
        stop = RuntimeError("parsed")
        for argv in self.ISSUING + (["menu", "Finn", "Octave"],
                                    ["resolve", "Ada"],
                                    ["do", "Finn", "Octave", "Go here"]):
            with self.subTest(verb=argv[0]):
                with mock.patch.object(order.rim, "init", side_effect=stop), \
                     mock.patch.object(order.rim, "game",
                                       side_effect=AssertionError("touched the game")), \
                     mock.patch("sys.stdout", new_callable=io.StringIO):
                    try:
                        code = order.main(argv + ["--do"])
                    except SystemExit as e:
                        self.fail("%s --do was refused by argparse (%s)"
                                  % (argv[0], e))
                # rim.init raised, so parsing was the only thing that ran.
                self.assertEqual(1, code)

    def test_dry_run_beats_do_when_both_are_given(self):
        code, text, calls = self.run_cli(
            ["haul", "Ada", "steel", "--do", "--dry-run"], reply(action="haul"))
        self.assertEqual(0, code)
        self.assertIn("--dry-run wins", text)
        self.assertIn("DRY RUN", text)
        self.assertTrue(self.ordered(calls)[0]["dryRun"])


class MenuMatcherTests(unittest.TestCase):
    """A disabled option is the game ANSWERING. Reporting it as 0 options is
    throwing the answer away -- turn 18, `force <doctor> <cell> "Tend"`."""

    # The harness lives on OrderCliTests; borrowing the methods rather than
    # subclassing it keeps its 33 tests from being re-run under three names.
    run_cli = OrderCliTests.run_cli
    menu_cli = OrderCliTests.menu_cli
    ordered = OrderCliTests.ordered


    def test_a_disabled_match_is_reported_with_the_games_own_reason(self):
        options = [{"index": 1, "label": "Tend Octave (without medicine)",
                    "disabled": True, "disabledReason": "Finn cannot reach Octave"},
                   {"index": 2, "label": "Go here", "disabled": False}]
        code, text, _, _ = self.menu_cli(
            ["force", "Finn", "118", "133", "Tend", "--do"], options)
        self.assertEqual(1, code)
        self.assertIn("RimWorld IS offering it, disabled", text)
        self.assertIn("Finn cannot reach Octave", text)

    def test_no_match_at_all_names_the_drafted_tend_rule(self):
        options = [{"index": 1, "label": "Rescue Octave", "disabled": False},
                   {"index": 2, "label": "Strip Octave", "disabled": False}]
        code, text, _, _ = self.menu_cli(
            ["force", "Finn", "118", "133", "Tend", "--do"], options)
        self.assertEqual(1, code)
        self.assertIn("appears in no option on this menu", text)
        self.assertIn("FloatMenuOptionProvider_DraftedTend", text)
        self.assertIn("offered Rescue instead", text)


class CameraBeforeTheClickTests(unittest.TestCase):
    """A float menu is built from where the mouse is over the MAP.

    `FloatMenuContext` takes a `Vector3 clickPosition` and its `ClickedThings`
    is `GenUI.ThingsUnderMouse(clickPosition, ...)` over
    `thingGrid.ThingsAt(IntVec3.FromVector3(clickPosition))`, so a cell that is
    not on screen resolves to a different cell entirely. `order.py` was the one
    click path in these instruments not going through `pick`.
    """

    def test_open_menu_clears_the_designator_and_moves_the_camera_first(self):
        order_of_calls = []
        with mock.patch.object(order.pick, "clear_designator",
                               side_effect=lambda: order_of_calls.append("clear")),              mock.patch.object(order.pick, "ensure_camera",
                               side_effect=lambda *a, **k: order_of_calls.append("camera")) as camera,              mock.patch.object(order.rim, "game",
                               side_effect=lambda t, a=None, **k: (
                                   order_of_calls.append(t) or {"menuId": 7})):
            order.open_menu({"thingId": "Thing_Human1"},
                            {"x": 122, "z": 140, "button": "right"}, (122, 140))
        self.assertEqual(["clear", "camera"], order_of_calls[:2])
        self.assertEqual((122, 140), camera.call_args.args)
        self.assertIn("rimworld/open_context_menu", order_of_calls)

    def test_a_camera_that_will_not_go_refuses_instead_of_clicking(self):
        with mock.patch.object(order.pick, "clear_designator"),              mock.patch.object(order.pick, "ensure_camera",
                               side_effect=order.pick.CameraStuck("no")),              mock.patch.object(order.rim, "game",
                               side_effect=AssertionError("clicked anyway")):
            with self.assertRaises(RuntimeError) as caught:
                order.open_menu({"thingId": "Thing_Human1"},
                                {"x": 1, "z": 2, "button": "right"}, (1, 2))
        self.assertIn("would be resolved against whatever IS on screen",
                      str(caught.exception))


class EmptyMenuDiagnosisTests(unittest.TestCase):
    def test_it_names_the_work_type_and_that_the_pawn_is_incapable(self):
        out = io.StringIO()
        with mock.patch.object(order, "_work_at",
                               return_value=[("blueprint power conduit",
                                              "Construction")]),              mock.patch("pawns.live_work_types",
                        return_value=[{"name": "Construction",
                                       "disabled": True}]),              mock.patch("sys.stdout", out):
            order.explain_empty_menu({"name": "Finn"}, (122, 140))
        text = out.getvalue()
        self.assertIn("at 122,140: blueprint power conduit", text)
        self.assertIn("Finn is INCAPABLE of it", text)

    def test_an_empty_cell_says_there_is_no_work_there_at_all(self):
        out = io.StringIO()
        with mock.patch.object(order, "_work_at", return_value=[]),              mock.patch("sys.stdout", out):
            order.explain_empty_menu({"name": "Finn"}, (10, 20))
        self.assertIn("NO designation, blueprint or frame at 10,20",
                      out.getvalue())


class HaulStorageVersusReachTests(unittest.TestCase):
    """`no_storage` is raised when TryFindBestBetterStorageFor finds nothing --
    but that search runs WITH the pawn as carrier, and IsGoodStoreCell drops
    every cell the carrier cannot reach. Turn 31 played around a storage
    problem it did not have."""

    def refusal(self, **checks):
        return {"success": False, "errorKind": "no_storage",
                "error": "There is nowhere better to put it",
                "pawn": {"name": "Lucas"},
                "target": {"name": "steel", "thingId": "Thing_Steel1"},
                "diagnostics": {"checks": checks}}

    def test_storage_found_without_the_carrier_reads_as_access_not_storage(self):
        lines = order.storage_or_reach(
            self.refusal(betterStorageFoundIgnoringCarrier=True,
                         storageCellIgnoringCarrier="118,150"),
            {"name": "Lucas"}, {"name": "steel"})
        self.assertTrue(any("NOT a storage problem" in l for l in lines))
        self.assertTrue(any("118,150" in l for l in lines))

    def test_an_unreachable_item_is_named_even_on_an_older_dll(self):
        lines = order.storage_or_reach(self.refusal(reachableNormalDanger=False),
                                       {"name": "Lucas"}, {"name": "steel"})
        self.assertTrue(any("cannot even REACH" in l for l in lines))
        self.assertTrue(any("did not report the carrier-free" in l
                            for l in lines))

    def test_a_genuine_no_storage_says_it_was_confirmed(self):
        lines = order.storage_or_reach(
            self.refusal(betterStorageFoundIgnoringCarrier=False),
            {"name": "Lucas"}, {"name": "steel"})
        self.assertTrue(any("confirmed storage-side" in l for l in lines))


if __name__ == "__main__":
    unittest.main()
