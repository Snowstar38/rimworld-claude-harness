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

    def test_dry_run_names_the_rescue_route_and_issues_nothing(self):
        # BUGS 2026-09-12: --dry-run printed only "would be accepted" and
        # never said the real call would rescue first.
        code, text, calls = self.tend_cli(["tend", "Finn", "Octave",
                                           "--dry-run"])
        self.assertEqual(0, code)
        self.assertIn("ROUTE: RESCUE", text)
        self.assertIn("DRY RUN", text)
        sent = self.ordered(calls)
        self.assertEqual(["tend"], [p["action"] for p in sent])
        self.assertTrue(all(p.get("dryRun") for p in sent))

    def test_dry_run_on_a_drafted_doctor_names_the_lie_route_and_stays_dry(self):
        code, text, calls = self.tend_cli(["tend", "Finn", "Octave",
                                           "--dry-run"], drafted=True)
        self.assertEqual(0, code)
        self.assertIn("TEND WHERE THEY LIE", text)
        sent = self.ordered(calls)
        self.assertTrue(all(p.get("dryRun") for p in sent))
        self.assertTrue(sent[-1]["allowPersistentDraft"])

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

    def menu_cli(self, argv, options, menu_id=7, target_row=None, paused=False,
                 extra=None):
        """Run a menu/do/force command against a mocked float menu."""
        row = target_row or {"thingId": "Thing_Human9", "name": "Octave",
                             "kindDef": "Colonist", "position": {"x": 5, "z": 6}}
        calls = []

        def game(tool, params=None, strict=True):
            calls.append((tool, params))
            if tool == order.TOOL:
                return reply(target=row)
            if tool == "rimworld/open_context_menu":
                menu = {"success": True, "menuId": menu_id, "options": options,
                        "optionCount": len(options), "target": "cell 5,6"}
                menu.update(extra or {})
                return menu
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

    def test_a_verified_job_with_a_reason_prints_the_reason_not_the_job(self):
        """A drafted pawn equips inside the frame, so the companion verifies
        by EQUIPMENT and says so in verifiedReason (OrderTool, 2026-09-13).
        Printing "the pawn's job is now Equip" there would be a lie -- the
        job already ran."""
        why = ("Verified by EQUIPMENT, not by job: a drafted pawn equips "
               "instantly, and bolt-action rifle is now the pawn's primary "
               "weapon.")
        code, text, _ = self.run_cli(
            ["equip", "Longhoff", "334862"],
            reply(action="equip",
                  job={"def": "Equip", "verified": True, "verifiedReason": why}))
        self.assertEqual(0, code)
        self.assertIn(why, text)
        self.assertNotIn("the pawn's job is now", text)

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
    def diagnose(self, work, standing, at=(122, 140), types=None):
        out = io.StringIO()
        with mock.patch.object(order, "_work_at",
                               return_value=(work, standing)), \
             mock.patch("pawns.live_work_types", return_value=types or []), \
             mock.patch("sys.stdout", out):
            order.explain_empty_menu({"name": "Finn"}, at)
        return out.getvalue()

    def test_it_names_the_work_type_and_that_the_pawn_is_incapable(self):
        text = self.diagnose([("blueprint power conduit", "Construction")], [],
                             types=[{"name": "Construction", "disabled": True}])
        self.assertIn("at 122,140: blueprint power conduit", text)
        self.assertIn("Finn is INCAPABLE of it", text)

    def test_a_truly_empty_cell_says_the_cell_is_empty_too(self):
        text = self.diagnose([], [], at=(10, 20))
        self.assertIn("NO designation, blueprint or frame at 10,20", text)
        self.assertIn("the cell is EMPTY", text)

    def test_a_finished_building_is_named_instead_of_reading_as_nothing(self):
        # WEIRD 7 / 13 / 16, three sightings: "there is NO designation,
        # blueprint or frame at 128,147" seconds after build.py returned
        # PLACED. The blueprint had finished into a wall.
        text = self.diagnose([], [("wall", "Building", False)], at=(128, 147))
        self.assertIn("the cell is NOT empty", text)
        self.assertIn("a built wall stands at 128,147", text)
        self.assertIn("the blueprint FINISHED", text)
        self.assertIn("buildings.py 128,147", text)

    def test_a_bed_cell_names_the_rest_verb_rather_than_an_empty_menu(self):
        # WEIRD 4: RimWorld auto-takes a click on a bed, so there is no menu
        # and no "Rest" label anywhere in the game to match.
        text = self.diagnose([], [("double bed", "Building_Bed", False)],
                             at=(113, 139))
        self.assertIn("AUTO-TAKES a click on a bed", text)
        self.assertIn("python order.py rest <pawn> 113 139", text)

    def test_a_loose_item_is_named_and_pointed_at_haul(self):
        text = self.diagnose([], [("steel", "ThingWithComps", True)])
        self.assertIn("steel (forbidden)", text)
        self.assertIn("order.py haul", text)


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


class RestTests(unittest.TestCase):
    """WEIRD 4: `force <pawn> <bed cell> "Rest"` returns 0 options, because
    RimWorld auto-takes a click on a bed and there is no "Rest" label anywhere
    in the game. `rest` is the verb that exists instead."""

    run_cli = OrderCliTests.run_cli
    ordered = OrderCliTests.ordered

    def rested(self, **kw):
        return reply(action="rest", job={"def": "LayDown", "verified": True},
                     **kw)

    def test_a_bed_cell_is_sent_as_x_z_not_as_an_ambiguous_target(self):
        code, text, calls = self.run_cli(["rest", "Ian", "113", "139"],
                                         self.rested())
        self.assertEqual(0, code)
        sent = self.ordered(calls)[0]
        self.assertEqual("rest", sent["action"])
        self.assertEqual((113, 139), (sent["x"], sent["z"]))
        self.assertNotIn("target", sent)
        self.assertIn("ORDER ISSUED", text)

    def test_a_comma_cell_is_the_same_call_as_a_bare_pair(self):
        _, _, calls = self.run_cli(["rest", "Ian", "113,139"], self.rested())
        sent = self.ordered(calls)[0]
        self.assertEqual((113, 139), (sent["x"], sent["z"]))

    def test_a_named_bed_stays_a_target(self):
        _, _, calls = self.run_cli(["rest", "Ian", "Bed@113,139"], self.rested())
        sent = self.ordered(calls)[0]
        self.assertEqual("Bed@113,139", sent["target"])
        self.assertNotIn("x", sent)

    def test_no_target_at_all_lets_the_game_find_the_pawns_own_bed(self):
        _, _, calls = self.run_cli(["rest", "Ian"], self.rested())
        sent = self.ordered(calls)[0]
        self.assertEqual({"action", "pawn"}, set(sent) - {"watch", "draft"})

    def test_rest_belongs_to_the_ledger_during_a_combat_session(self):
        with mock.patch.object(order.rim, "init"), \
             mock.patch.object(order, "combat_session", return_value={"active": True}), \
             mock.patch.object(order.rim, "game",
                               side_effect=AssertionError("called the game")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = order.main(["rest", "Ian", "113", "139"])
        self.assertEqual(1, code)
        self.assertIn("python combat.py end", out.getvalue())


class MoveAndDraftTests(unittest.TestCase):
    """WEIRD 4 and 64: `goto` auto-drafted and never undrafted, and there was
    no `move`. Both halves are one verb with two names now, and what happens to
    the draft is printed before the order is sent."""

    run_cli = OrderCliTests.run_cli
    ordered = OrderCliTests.ordered

    def moved(self):
        return reply(action="goto", job={"def": "Goto", "verified": True})

    def test_move_is_the_same_verb_as_goto(self):
        code, text, calls = self.run_cli(["move", "Ada", "140", "152"],
                                         self.moved())
        self.assertEqual(0, code)
        self.assertEqual("goto", self.ordered(calls)[0]["action"])
        self.assertEqual((140, 152), (self.ordered(calls)[0]["x"],
                                      self.ordered(calls)[0]["z"]))

    def test_the_default_says_the_pawn_stays_drafted(self):
        _, text, calls = self.run_cli(["goto", "Ada", "140", "152"],
                                      self.moved())
        self.assertIn("DRAFTED MOVE", text)
        self.assertIn("STAYS drafted", text)
        self.assertIn("python order.py undraft Ada", text)
        self.assertNotIn("draft", self.ordered(calls)[0])

    def test_undraft_moves_them_without_drafting_at_all(self):
        _, text, calls = self.run_cli(["move", "Ada", "140", "152", "--undraft"],
                                      self.moved())
        self.assertIn("UNDRAFTED MOVE", text)
        self.assertIn("Nothing is left drafted", text)
        self.assertFalse(self.ordered(calls)[0]["draft"])

    def test_the_two_draft_flags_are_opposites_and_are_refused_together(self):
        with mock.patch.object(order.rim, "init",
                               side_effect=AssertionError("touched the game")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = order.main(["goto", "Ada", "1", "2", "--undraft",
                               "--stay-drafted"])
        self.assertEqual(1, code)
        self.assertIn("opposites", out.getvalue())


class PawnFirstTests(unittest.TestCase):
    """WEIRD 64: `order.py force` needs a pawn id first, or it reads the
    x-coordinate as a pawn name."""

    def refuse(self, argv):
        with mock.patch.object(order.rim, "init",
                               side_effect=AssertionError("touched the game")), \
             mock.patch.object(order.rim, "game",
                               side_effect=AssertionError("touched the game")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = order.main(argv)
        return code, out.getvalue()

    def test_a_cell_with_no_pawn_is_refused_with_the_shape_it_wanted(self):
        code, text = self.refuse(["force", "128", "147", "--do"])
        self.assertEqual(1, code)
        self.assertIn("takes the PAWN first", text)
        self.assertIn("python order.py force <pawn> 128 147", text)

    def test_a_real_bare_number_pawn_id_is_never_refused(self):
        # A thingIDNumber is 5+ digits; the guard must not eat one.
        with mock.patch.object(order.rim, "init",
                               side_effect=RuntimeError("parsed")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = order.main(["force", "334862", "128", "147"])
        self.assertEqual(1, code)
        self.assertNotIn("takes the PAWN first", out.getvalue())

    def test_a_named_pawn_is_never_refused(self):
        with mock.patch.object(order.rim, "init",
                               side_effect=RuntimeError("parsed")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = order.main(["force", "Finn", "128", "147"])
        self.assertEqual(1, code)
        self.assertNotIn("takes the PAWN first", out.getvalue())


class SessionRefusalTests(unittest.TestCase):
    """WEIRD 64 read "order.py refuses while a combat ledger is open" as the
    whole tool being shut. Only the draft-changing verbs are."""

    def test_the_refusal_names_what_still_works_and_what_closes_the_ledger(self):
        with mock.patch.object(order.rim, "init"), \
             mock.patch.object(order, "combat_session", return_value={"active": True}), \
             mock.patch.object(order.rim, "game",
                               side_effect=AssertionError("called the game")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = order.main(["goto", "Ada", "140", "152"])
        text = out.getvalue()
        self.assertEqual(1, code)
        self.assertIn("ONLY the draft-changing verbs are refused", text)
        for verb in ("haul", "work", "force", "menu"):
            self.assertIn(verb, text)
        self.assertIn("python combat.py end", text)


class BenchLabelTests(unittest.TestCase):
    """WEIRD 1: a recipe name is not a float-menu label. The stove's option is
    "Prioritize cooking at fueled stove"."""

    menu_cli = OrderCliTests.menu_cli

    STOVE = [{"index": 1, "label": "Prioritize cooking at fueled stove",
              "disabled": False},
             {"index": 2, "label": "Go here", "disabled": False}]

    def test_a_recipe_name_falls_back_to_the_one_work_at_bench_option(self):
        code, text, _, calls = self.menu_cli(
            ["force", "Ada", "126", "140", "Cook simple meal", "--do"],
            self.STOVE)
        self.assertEqual(0, code)
        self.assertIn("is not a float-menu label", text)
        self.assertIn("bills.py", text)
        ran = [p for t, p in calls
               if t == "rimworld/execute_context_menu_option"]
        self.assertEqual([{"label": "Prioritize cooking at fueled stove"}], ran)

    def test_two_work_at_bench_options_are_never_guessed_between(self):
        options = self.STOVE + [{"index": 3,
                                 "label": "Prioritize hauling at fueled stove",
                                 "disabled": False}]
        code, text, _, calls = self.menu_cli(
            ["force", "Ada", "126", "140", "Cook simple meal", "--do"], options)
        self.assertEqual(1, code)
        self.assertNotIn("rimworld/execute_context_menu_option",
                         [t for t, _ in calls])

    def test_a_miss_prints_every_label_and_names_the_menu_command(self):
        code, text, _, _ = self.menu_cli(
            ["force", "Ada", "126", "140", "Cook simple meal", "--do"],
            [{"index": 1, "label": "Go here", "disabled": False}])
        self.assertEqual(1, code)
        self.assertIn("'Go here'", text)
        self.assertIn("python order.py menu Ada 126,140", text)


class ReservedTargetTests(unittest.TestCase):
    """WEIRD 30: "Prioritize tending to Lucas: Reserved by Longhoff" was
    offered enabled and issued without a word. It is issued, loudly."""

    menu_cli = OrderCliTests.menu_cli

    OPTIONS = [{"index": 1,
                "label": "Prioritize tending to Lucas: Reserved by Longhoff",
                "disabled": False}]

    def test_it_issues_and_says_the_other_pawn_loses_the_job(self):
        code, text, _, calls = self.menu_cli(
            ["force", "Finn", "Lucas", "--do"], self.OPTIONS)
        self.assertEqual(0, code)
        self.assertIn("!! PRE-EMPT", text)
        self.assertIn("Longhoff", text)
        self.assertIn("TWO pawns re-tasked", text)
        self.assertTrue([p for t, p in calls
                         if t == "rimworld/execute_context_menu_option"])

    def test_the_dry_run_warns_before_the_do(self):
        code, text, _, _ = self.menu_cli(["force", "Finn", "Lucas"],
                                         self.OPTIONS)
        self.assertEqual(0, code)
        self.assertIn("!! PRE-EMPT", text)
        self.assertIn("DRY RUN", text)

    def test_an_unreserved_option_says_nothing_about_pre_empting(self):
        code, text, _, _ = self.menu_cli(
            ["force", "Finn", "Lucas"],
            [{"index": 1, "label": "Prioritize tending to Lucas",
              "disabled": False}])
        self.assertNotIn("PRE-EMPT", text)


class RefusalCarriesTheLabelsTests(unittest.TestCase):
    """WEIRD 6: "3 enabled options" and "'conduit' matched 2 enabled options"
    are refusals nobody can act on without the labels."""

    menu_cli = OrderCliTests.menu_cli

    def test_an_ambiguous_substring_names_both_labels(self):
        options = [{"index": 1, "label": "Prioritize building conduit (A)",
                    "disabled": False},
                   {"index": 2, "label": "Prioritize building conduit (B)",
                    "disabled": False}]
        code, text, _, _ = self.menu_cli(
            ["force", "Ada", "131", "139", "conduit", "--do"], options)
        self.assertEqual(1, code)
        self.assertIn("'Prioritize building conduit (A)'", text)
        self.assertIn("'Prioritize building conduit (B)'", text)

    def test_three_prioritize_options_with_no_substring_name_all_three(self):
        options = [{"index": i, "label": "Prioritize thing %d" % i,
                    "disabled": False} for i in (1, 2, 3)]
        code, text, _, _ = self.menu_cli(
            ["force", "Ada", "131", "139", "--do"], options)
        self.assertEqual(1, code)
        self.assertIn("3 enabled", text)
        for i in (1, 2, 3):
            self.assertIn("'Prioritize thing %d'" % i, text)

    def test_no_prioritize_option_at_all_still_names_what_was_offered(self):
        code, text, _, _ = self.menu_cli(
            ["force", "Ada", "131", "139", "--do"],
            [{"index": 1, "label": "Go here", "disabled": False}])
        self.assertEqual(1, code)
        self.assertIn("'Go here'", text)


class EmptyMenuIsAnAnswerTests(unittest.TestCase):
    """WEIRD 33 and 53: zero options is the game answering. It must not read
    as the tool failing, and the payload's own reason must be printed."""

    menu_cli = OrderCliTests.menu_cli

    def test_the_bridge_reason_and_the_cell_it_clicked_are_printed(self):
        code, text, _, _ = self.menu_cli(
            ["menu", "Finn", "132", "142"], [], menu_id=0,
            extra={"message": "no options were offered",
                   "provider": "ui_event",
                   "clickCell": {"x": 132, "z": 142}})
        self.assertEqual(1, code)
        self.assertIn("the click landed at 132,142", text)
        self.assertIn("no options were offered", text)
        self.assertIn("ANSWERED", text)

    def test_a_caravan_member_is_named_as_the_wrong_person_not_a_failure(self):
        row = {"thingId": "Thing_Human77", "name": "Kesi", "kindDef": "Trader",
               "isPawn": True, "isPlayerFaction": False,
               "faction": "Nathanite tribe", "hostileToPlayer": False,
               "position": {"x": 20, "z": 30}}
        code, text, _, _ = self.menu_cli(["menu", "Finn", "Thing_Human77"], [],
                                         menu_id=0, target_row=row)
        self.assertEqual(1, code)
        self.assertIn("no options for this pawn", text)
        self.assertIn("Nathanite tribe", text)
        self.assertIn("python trade.py", text)

    def test_one_of_our_own_pawns_gets_no_caravan_advice(self):
        row = {"thingId": "Thing_Human9", "name": "Octave", "kindDef": "Colonist",
               "isPawn": True, "isPlayerFaction": True, "faction": "New Arrival",
               "position": {"x": 5, "z": 6}}
        code, text, _, _ = self.menu_cli(["menu", "Finn", "Octave"], [],
                                         menu_id=0, target_row=row)
        self.assertNotIn("trade.py", text)

    def test_an_older_companion_that_sends_no_flag_says_nothing_about_it(self):
        row = {"thingId": "Thing_Human77", "name": "Kesi", "kindDef": "Trader",
               "faction": "Nathanite tribe", "position": {"x": 20, "z": 30}}
        code, text, _, _ = self.menu_cli(["menu", "Finn", "Thing_Human77"], [],
                                         menu_id=0, target_row=row)
        self.assertNotIn("trade.py", text)



class DeployCliTests(unittest.TestCase):
    """`order.py deploy` -- the worn-pack gizmo, WEIRD of 2026-09-08.

    Two colonists wore turret packs and four map clicks were swallowed without
    a word. The verdict lines below are the whole point of the verb: a refusal
    must name WHICH of the two gates failed and print cells that would work.
    """

    def deploy_reply(self, success=True, error=None, kind=None, deploy=None,
                     job=None):
        block = {
            "ok": success,
            "reason": error,
            "reasonKind": kind,
            "rule": ("A pack deploy is a THROWN GRENADE, not a build order. "
                     "The cell must (1) hold NO Building at all ... and (2) be "
                     "inside the verb's range AND in LINE OF SIGHT of the pawn."),
            "cell": {"x": 139, "z": 118},
            "pack": {"thingId": "Thing_Apparel_PackTurret501", "oneUse": True,
                     "defName": "Apparel_PackTurret", "label": "Turret pack",
                     "gizmoLabel": "deploy turret", "charges": 1,
                     "maxCharges": 1, "verbClass": "Verb_LaunchProjectileStaticOneUse"},
            "checks": {"standable": True, "buildingOnCell": None,
                       "distance": 11.2, "range": 22.9, "lineOfSight": True,
                       "inRange": True, "canHitTarget": True, "canReserve": True},
            "validCellsNearby": [],
            "validCellsOrigin": None,
        }
        block.update(deploy or {})
        return {
            "success": success,
            "action": "deploy",
            "error": error,
            "errorKind": kind,
            "pawn": {"thingId": "Thing_Human618", "name": "Ernst",
                     "drafted": False, "downed": False, "dead": False},
            "target": {"thingId": "Thing_Apparel_PackTurret501",
                       "name": "Turret pack", "hostileToPlayer": False,
                       "downed": False},
            "job": job,
            "diagnostics": {"deploy": block},
            "watch": {"shown": False},
        }

    def run_cli(self, argv, answer):
        calls = []

        def game(tool, params=None, strict=True):
            calls.append((tool, params))
            if tool == order.TOOL:
                return answer
            if tool == order.clock.TOOL:
                return {"success": True,
                        "time": {"paused": False, "forcePaused": False,
                                 "timeSpeed": "Normal", "ticksGame": 5000}}
            return {"success": True}

        with mock.patch.object(order.rim, "game", side_effect=game), \
             mock.patch.object(order.rim, "init"), \
             mock.patch.object(order, "check_modal"), \
             mock.patch.object(order, "combat_session", return_value=None), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = order.main(argv)
        return code, out.getvalue(), [p for t, p in calls if t == order.TOOL]

    def test_a_bare_call_is_a_dry_run_because_the_pack_has_one_charge(self):
        code, text, calls = self.run_cli(
            ["deploy", "Ernst", "139", "118"], self.deploy_reply())
        self.assertEqual(0, code)
        self.assertIs(True, calls[0]["dryRun"])
        self.assertEqual("deploy", calls[0]["action"])
        self.assertEqual(139, calls[0]["x"])
        self.assertEqual(118, calls[0]["z"])
        self.assertIn("DRY RUN", text)
        self.assertNotIn("ORDER ISSUED", text)

    def test_do_is_what_spends_the_pack_and_says_so_before_sending(self):
        code, text, calls = self.run_cli(
            ["deploy", "Ernst", "139", "118", "--do"],
            self.deploy_reply(job={"def": "UseVerbOnThingStaticReserve",
                                   "verified": True}))
        self.assertEqual(0, code)
        self.assertNotIn("dryRun", calls[0])
        self.assertIn("SPENDING THE PACK", text)
        self.assertIn("ORDER ISSUED", text)
        self.assertIn("UseVerbOnThingStaticReserve", text)

    def test_dry_run_wins_over_do(self):
        code, text, calls = self.run_cli(
            ["deploy", "Ernst", "139", "118", "--do", "--dry-run"],
            self.deploy_reply())
        self.assertIs(True, calls[0]["dryRun"])
        self.assertIn("DRY RUN", text)

    def test_the_rule_is_printed_even_when_the_cell_is_accepted(self):
        code, text, _ = self.run_cli(
            ["deploy", "Ernst", "139", "118"], self.deploy_reply())
        self.assertIn("THE RULE", text)
        self.assertIn("THROWN GRENADE", text)
        self.assertIn("deploy turret", text)

    def test_a_dry_run_names_the_job_it_would_issue_and_says_it_did_not(self):
        reply = self.deploy_reply()
        reply["wouldIssue"] = {"def": "UseVerbOnThingStaticReserve",
                               "issued": False, "verified": False}
        code, text, _ = self.run_cli(["deploy", "Ernst", "139", "118"], reply)
        self.assertEqual(0, code)
        self.assertIn("UseVerbOnThingStaticReserve", text)
        self.assertIn("nothing issued", text)
        self.assertIn("job.verbToUse", text)

    def test_no_line_of_sight_names_the_gate_and_exits_non_zero(self):
        code, text, _ = self.run_cli(
            ["deploy", "Ernst", "139", "118"],
            self.deploy_reply(
                success=False,
                kind="deploy_no_line_of_sight",
                error="Ernst cannot throw Turret pack to (139,118): it is IN "
                      "range (11.2 of 22.9) but there is NO LINE OF SIGHT.",
                deploy={"checks": {"standable": True, "buildingOnCell": None,
                                   "distance": 11.2, "range": 22.9,
                                   "lineOfSight": False, "inRange": True,
                                   "canHitTarget": False, "canReserve": True},
                        "validCellsNearby": [
                            {"x": 139, "z": 124, "distanceFromAsked": 6,
                             "distanceFromPawn": 7},
                            {"x": 140, "z": 124, "distanceFromAsked": 6,
                             "distanceFromPawn": 8}],
                        "validCellsOrigin": "askedCell"}))
        self.assertEqual(1, code)
        self.assertIn("ORDER REFUSED  deploy_no_line_of_sight", text)
        self.assertIn("NO LINE OF SIGHT", text)
        self.assertIn("line of sight NO", text)
        self.assertIn("2 cell(s) this pawn CAN deploy onto", text)
        self.assertIn("139,124", text)

    def test_a_building_on_the_cell_is_named_not_merely_refused(self):
        code, text, _ = self.run_cli(
            ["deploy", "Reikguard", "141", "119"],
            self.deploy_reply(
                success=False,
                kind="deploy_cell_blocked",
                error="(141,119) holds sandstone wall (Thing_Wall33). ANY "
                      "Building refuses the cell.",
                deploy={"checks": {"standable": False,
                                   "buildingOnCell": "sandstone wall (Thing_Wall33)",
                                   "distance": 3.0, "range": 22.9,
                                   "lineOfSight": True, "canReserve": True},
                        "validCellsNearby": [],
                        "validCellsOrigin": None}))
        self.assertEqual(1, code)
        self.assertIn("ORDER REFUSED  deploy_cell_blocked", text)
        self.assertIn("building sandstone wall", text)
        self.assertIn("standable NO", text)

    def test_an_old_companion_without_the_op_says_so_rather_than_going_quiet(self):
        reply = self.deploy_reply(success=False, kind="bad_arguments",
                                  error="'deploy' is not an action.")
        reply["diagnostics"] = {}
        code, text, _ = self.run_cli(["deploy", "Ernst", "139", "118"], reply)
        self.assertEqual(1, code)
        self.assertIn("has no `deploy` op", text)
        self.assertIn("INSTALL.md", text)

    def test_deploy_survives_an_open_combat_session_because_it_never_drafts(self):
        self.assertIn("deploy", order.SESSION_SAFE)
        self.assertNotIn("deploy", order.DRAFTING_VERBS)


class ForeignTargetTest(unittest.TestCase):
    """A caravan member who is not the trader must NAME the trader.

    Live 2026-09-12 (Threadneedle, Allalljalor visitors): `order.py menu
    Reikguard Paulo` answered correctly -- "a visiting caravan has exactly one
    member you can act on" -- and then pointed at `python trade.py` instead of
    saying "Kat". BUGS.md promises the name, and `home/trade` is a read the
    refusal can afford.
    """

    ROW = {"isPawn": True, "name": "Paulo", "faction": "Allalljalor",
           "isPlayerFaction": False, "hostileToPlayer": False}

    TRADE = {"success": True,
             "traders": [{"name": "Kat", "id": "Thing_Human49495",
                          "faction": "Allalljalor", "canTradeNow": True}]}

    def explain(self, game):
        with mock.patch.object(order.rim, "game", side_effect=game),              mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            order.explain_foreign_target(self.ROW)
        return out.getvalue()

    def test_the_trader_is_named_on_the_refusal(self):
        text = self.explain(lambda tool, args, **kw: self.TRADE)
        self.assertIn("Kat", text)
        self.assertIn("Thing_Human49495", text)
        self.assertIn("belongs to Allalljalor", text)

    def test_a_trader_of_another_faction_is_not_claimed_as_this_caravan(self):
        other = {"success": True,
                 "traders": [{"name": "Somebody", "id": "Thing_Human1",
                              "faction": "Elsewhere", "canTradeNow": True}]}
        text = self.explain(lambda tool, args, **kw: other)
        self.assertNotIn("Somebody", text)
        self.assertIn("python trade.py", text)

    def test_a_dead_home_trade_falls_back_instead_of_raising(self):
        def boom(tool, args, **kw):
            raise RuntimeError("no such tool")
        text = self.explain(boom)
        self.assertIn("python trade.py", text)
        self.assertIn("scenery", text)


if __name__ == "__main__":
    unittest.main()
