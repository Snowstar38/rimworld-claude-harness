import io
import os
import tempfile
import unittest
from unittest import mock

import combat
import pawns
import run


def snap(tick=100, drafted=False, hostiles=0, pawn=True, id_key="thingId"):
    return {"status": "game_loaded", "time": {"ticksGame": tick},
            "colonists": ([{id_key: "p1", "name": "Lucas", "drafted": drafted}]
                           if pawn else []),
            "threats": {"hostileCount": hostiles, "hostiles": []}}


def hostile_snap(tick=100, drafted=False, downed=False):
    result = snap(tick, drafted, hostiles=1)
    result["threats"]["hostiles"] = [{"thingId": "t1", "name": "raccoon",
                                         "hostile": True, "downed": downed,
                                         "dead": False}]
    return result


def order_pawn(drafted=False, auto=False, **kw):
    row = {"thingId": "p1", "name": "Lucas", "drafted": drafted,
           "autoDrafted": auto, "downed": False, "dead": False,
           "mentalState": None, "playerControlled": True,
           "incapableOfViolence": False, "canBeDrafted": True,
           "weapon": {"thingId": "W1", "label": "knife", "ranged": False},
           "position": {"x": 1, "z": 1}}
    row.update(kw)
    return row


def order_target(**kw):
    row = {"thingId": "Thing_Wolf_Timber334862",
           "idForms": ["Thing_Wolf_Timber334862", "Wolf_Timber334862",
                       "334862", "timber wolf"],
           "name": "timber wolf", "defName": "Wolf_Timber",
           "kindDef": "Wolf_Timber", "faction": None,
           "hostileToPlayer": False, "mentalState": None, "downed": False,
           "dead": False, "predator": True, "position": {"x": 4, "z": 5},
           "distance": 3, "reachable": True}
    row.update(kw)
    return row


def order_reply(success=True, **kw):
    reply = {"success": success, "action": "attack",
             "pawn": order_pawn(), "target": order_target(),
             "job": {"def": "AttackMelee", "verified": True,
                     "targetA": "Thing_Wolf_Timber334862",
                     "killIncappedTarget": False, "verb": "fists"},
             "watch": {"shown": True, "selected": True, "cameraMoved": True,
                       "closesAfterSeconds": 6}}
    reply.update(kw)
    return reply


class FakeOrderTool(object):
    """One scripted `home/order`. Records every payload it was handed.

    The dry run and the real call are separate replies on purpose: the whole
    point of the 2026-09-04 rewrite is that the resolve happens BEFORE anything
    is drafted, and a test that could not tell the two calls apart could not
    prove it.
    """

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, params):
        self.calls.append(params)
        return self.replies.pop(0) if self.replies else order_reply()

    @property
    def dry(self):
        return self.calls[0]

    @property
    def real(self):
        return self.calls[1]


class CombatLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "combat.json")
        self.ident = {"save": "fixture", "loadedAt": 1, "loadedAtTick": 50, "pid": 9}
        combat.begin(snap(), self.ident, self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def obligate(self):
        combat.record_draft_intent("p1", "Lucas", self.path)
        combat.confirm_drafted("p1", 101, self.path)

    def contents(self):
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def test_begin_is_idempotent(self):
        old = combat.load_ledger(self.path)
        got, made = combat.begin(snap(200), self.ident, self.path)
        self.assertFalse(made)
        self.assertEqual(old["startTick"], got["startTick"])

    def test_advance_refuses_persistent_supervisor_without_touching_waiter(self):
        with self.assertRaisesRegex(combat.SupervisedPlayRefusal,
                                    "already owns the clock"):
            combat.refuse_if_supervised(
                lambda p: {"success": True, "active": True, "epoch": 7,
                           "requestedSpeed": "Fast"})

    def test_combat_cleanup_pauses_supervisor_first(self):
        calls = []
        paused = combat.pause_supervised_for_cleanup(
            pause_func=lambda: calls.append("pause") or 0,
            status_call=lambda p: {"active": True, "epoch": 7})
        self.assertTrue(paused)
        self.assertEqual(["pause"], calls)

    def test_combat_cleanup_does_nothing_when_supervisor_inactive(self):
        paused = combat.pause_supervised_for_cleanup(
            pause_func=lambda: self.fail("pause called"),
            status_call=lambda p: {"active": False})
        self.assertFalse(paused)

    def test_begin_accepts_list_colonists_pawn_id_alias(self):
        other = os.path.join(self.tmp.name, "alias.json")
        ledger, _ = combat.begin(snap(id_key="pawnId"), self.ident, other)
        self.assertIn("p1", ledger["pawns"])

    def test_begin_records_the_harness_turn_owner(self):
        other = os.path.join(self.tmp.name, "owned.json")
        owner = {"turn": 7, "handsStartedAt": 123.5}
        ledger, _ = combat.begin(snap(), self.ident, other, turn_owner=owner)
        self.assertEqual(owner, ledger["turnOwner"])

    def test_inherited_warning_names_adopt_and_preserves_cleanup(self):
        self.obligate()
        ledger = combat.load_ledger(self.path)
        ledger["turnOwner"] = {"turn": 6, "handsStartedAt": 100.0}
        warning = combat.inherited_warning(
            ledger, {"turn": 7, "handsStartedAt": 200.0})
        self.assertIn("INHERITED from turn 6", warning)
        self.assertIn("python combat.py adopt", warning)
        self.assertIn("p1", ledger["draftObligations"])

    def test_adopt_changes_only_owner_and_preserves_obligations(self):
        self.obligate()
        before = combat.load_ledger(self.path)["draftObligations"]
        owner = {"turn": 8, "handsStartedAt": 300.0}
        ledger = combat.adopt_turn(self.path, owner)
        self.assertEqual(owner, ledger["turnOwner"])
        self.assertEqual(before, ledger["draftObligations"])

    def test_inherited_cli_refusal_touches_neither_game_nor_ledger(self):
        self.obligate()
        before = self.contents()
        warning = "!! COMBAT LEDGER INHERITED; run `python combat.py adopt`"
        with mock.patch.object(combat, "inherited_warning", return_value=warning), \
             mock.patch.object(combat.rim, "init") as init, \
             mock.patch.object(combat, "_pause_now") as pause, \
             mock.patch.object(combat, "record_order_failure") as record, \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = combat.main(["attack", "Lucas", "wolf"])
        self.assertEqual(1, rc)
        self.assertIn("INHERITED", out.getvalue())
        init.assert_not_called()
        pause.assert_not_called()
        record.assert_not_called()
        self.assertEqual(before, self.contents())

    def test_write_ahead_intent_is_cleanup_obligation(self):
        combat.record_draft_intent("p1", "Lucas", self.path)
        row = combat.load_ledger(self.path)
        self.assertEqual("intent", row["draftObligations"]["p1"]["state"])
        self.assertTrue(row["cleanupPending"])

    def test_tick_regression_refuses_without_mutation(self):
        self.obligate()
        before = self.contents()
        with self.assertRaisesRegex(combat.CombatRefusal, "regressed"):
            combat.reconcile(snap(99, True), self.ident, path=self.path,
                             set_draft=lambda *_: self.fail("mutated"))
        self.assertEqual(before, self.contents())

    def test_changed_session_refuses(self):
        self.obligate()
        other = dict(self.ident, save="other")
        with self.assertRaisesRegex(combat.CombatRefusal, "identity changed"):
            combat.reconcile(snap(102, True), other, path=self.path)

    def test_hostiles_refuse_dangerous_undraft(self):
        self.obligate()
        with self.assertRaisesRegex(combat.CombatRefusal, "hostile"):
            combat.reconcile(snap(102, True, 1), self.ident, path=self.path)

    def test_downed_only_hostiles_let_end_proceed_with_a_reminder(self):
        # 2026-09-07: one crawling raider kept five colonists drafted until
        # somebody typed --force.
        self.obligate()
        calls = []
        lines = combat.reconcile(
            hostile_snap(102, drafted=True, downed=True), self.ident,
            path=self.path,
            set_draft=lambda pid, val: calls.append((pid, val)) or {"drafted": val})
        self.assertEqual([("p1", False)], calls)
        text = chr(10).join(lines)
        self.assertIn("every remaining hostile is DOWNED", text)
        self.assertIn("raccoon", text)
        self.assertIn("Finish them off or capture", text)
        self.assertIn("Still drafted after this cleanup", text)

    def test_a_conscious_hostile_still_refuses_and_names_force(self):
        self.obligate()
        with self.assertRaisesRegex(combat.CombatRefusal, "--force"):
            combat.reconcile(hostile_snap(102, drafted=True, downed=False),
                             self.ident, path=self.path,
                             set_draft=lambda *_: self.fail("mutated"))

    def test_dry_run_never_calls_setter_or_changes_ledger(self):
        self.obligate()
        before = self.contents()
        lines = combat.reconcile(snap(102, True), self.ident, dry_run=True,
                                 path=self.path,
                                 set_draft=lambda *_: self.fail("mutated"))
        self.assertEqual("DRY RUN", lines[0])
        self.assertEqual(before, self.contents())

    def test_dry_run_reports_hostiles_without_refusing_or_mutating(self):
        self.obligate()
        before = self.contents()
        lines = combat.reconcile(snap(102, True, 2), self.ident, dry_run=True,
                                 path=self.path,
                                 set_draft=lambda *_: self.fail("mutated"))
        self.assertIn("2 hostile(s) remain", lines[1])
        self.assertEqual(before, self.contents())

    def test_force_end_restores_and_removes_ledger(self):
        self.obligate()
        calls = []
        combat.reconcile(snap(102, True, 2), self.ident, force=True,
                         path=self.path,
                         set_draft=lambda pid, val: calls.append((pid, val)) or {"drafted": val})
        self.assertEqual([("p1", False)], calls)
        self.assertFalse(os.path.exists(self.path))

    def test_release_only_removes_named_obligation(self):
        self.obligate()
        combat.reconcile(snap(102, True), self.ident, pawn="Lucas", path=self.path,
                         set_draft=lambda pid, val: {"drafted": val})
        row = combat.load_ledger(self.path)
        self.assertEqual({}, row["draftObligations"])
        self.assertTrue(row["active"])

    def test_missing_pawn_preserves_obligation(self):
        self.obligate()
        with self.assertRaisesRegex(combat.CombatRefusal, "not a current colonist"):
            combat.reconcile(snap(102, pawn=False), self.ident, path=self.path)
        self.assertIn("p1", combat.load_ledger(self.path)["draftObligations"])

    def test_warning_names_cleanup_and_stale(self):
        self.obligate()
        session = os.path.join(self.tmp.name, "session.json")
        combat._atomic_write(self.ident, session)
        self.assertIn("UNFINISHED COMBAT", combat.status_warning(snap(102), self.path, session))
        self.assertIn("STALE", combat.status_warning(snap(99), self.path, session))

    def test_move_write_ahead_confirms_and_records_verified_order(self):
        seen = []
        def issue(pid, x, z, before_draft):
            before_draft({"name": "Lucas"})
            seen.append(combat.load_ledger(self.path)["draftObligations"]["p1"]["state"])
            return {"pawnId": pid, "pawnName": "Lucas", "accepted": True,
                    "autoDrafted": True, "arrived": False,
                    "destination": {"x": x, "z": z}}
        order = combat.issue_move("Lucas", 12, 34, snap(100), self.ident,
                                  self.path, issuer=issue)
        ledger = combat.load_ledger(self.path)
        self.assertEqual(["intent"], seen)
        self.assertEqual("confirmed", ledger["draftObligations"]["p1"]["state"])
        self.assertEqual(order, ledger["orders"]["p1"])

    def test_move_already_drafted_records_without_obligation(self):
        def issue(pid, x, z, before_draft):
            return {"pawnId": pid, "pawnName": "Lucas", "accepted": True,
                    "autoDrafted": False, "arrived": False,
                    "destination": {"x": x, "z": z}}
        combat.issue_move("p1", 12, 34, snap(100, drafted=True), self.ident,
                          self.path, issuer=issue)
        ledger = combat.load_ledger(self.path)
        self.assertEqual({}, ledger["draftObligations"])
        self.assertEqual("move", ledger["orders"]["p1"]["kind"])

    def test_move_failure_after_intent_keeps_obligation(self):
        def issue(pid, x, z, before_draft):
            before_draft({"name": "Lucas"})
            raise RuntimeError("order refused")
        with self.assertRaisesRegex(RuntimeError, "order refused"):
            combat.issue_move("Lucas", 12, 34, snap(), self.ident,
                              self.path, issuer=issue)
        ledger = combat.load_ledger(self.path)
        self.assertEqual("intent", ledger["draftObligations"]["p1"]["state"])
        self.assertNotIn("p1", ledger["orders"])

    def test_failed_move_latches_decision_until_replaced(self):
        combat.record_move_failure("Lucas", 12, 34, "cell blocked", self.path)
        ledger = combat.load_ledger(self.path)
        self.assertEqual("move failed",
                         ledger["pendingDecisions"]["p1"]["reason"])
        with self.assertRaisesRegex(combat.CombatRefusal, "decision required"):
            combat.advance(1, snap(), self.ident, self.path,
                           waiter=lambda _: self.fail("advanced after failure"))

    def test_stale_move_refuses_before_issuer(self):
        issuer = lambda *a, **k: self.fail("issuer called")
        with self.assertRaisesRegex(combat.CombatRefusal, "STALE"):
            combat.issue_move("Lucas", 12, 34, snap(99), self.ident,
                              self.path, issuer=issuer)

    def test_equip_records_accepted_but_incomplete_ticket(self):
        def issue(pid, x, z, weapon_thing_id, weapon_label):
            return {"kind": "equip", "pawnId": pid, "accepted": True,
                    "completed": False, "weaponThingId": weapon_thing_id,
                    "weaponLabel": weapon_label, "job": "Equip"}
        order = combat.issue_equip("Lucas", 8, 9, snap(), self.ident,
                                   weapon_label="revolver", path=self.path,
                                   issuer=issue)
        self.assertFalse(order["completed"])
        self.assertEqual("Equip", combat.load_ledger(self.path)["orders"]["p1"]["job"])

    def test_finish_equip_uses_combat_advance_as_waiter(self):
        ticket = {"kind": "equip", "pawnId": "p1", "accepted": True,
                  "completed": False, "weaponLabel": "revolver"}
        calls = []
        def equipment_waiter(got, waiter, seconds):
            reason, _ = waiter(lambda: False, seconds)
            calls.append(reason)
            return dict(got, completed=True, waitReason=reason)
        result = combat.finish_equip(
            ticket, 12, snap(), self.ident, self.path,
            equipment_waiter=equipment_waiter,
            advance_waiter=lambda args: {"stopReason": "budget_elapsed",
                                         "ticksGame": 110, "baseline": {}})
        self.assertTrue(result["completed"])
        self.assertEqual(["budget_elapsed"], calls)
        self.assertEqual(110, combat.load_ledger(self.path)["watermarkTick"])

    def test_attack_resolves_dry_first_then_orders_without_watching_the_probe(self):
        tool = FakeOrderTool(order_reply(pawn=order_pawn(drafted=True)),
                             order_reply(pawn=order_pawn(drafted=True)))
        order = combat.issue_attack("Lucas", "334862", hostile_snap(),
                                    self.ident, self.path, issuer=tool)
        self.assertEqual(2, len(tool.calls))
        self.assertTrue(tool.dry["dryRun"])
        self.assertFalse(tool.dry["watch"])   # the probe never moves the screen
        self.assertNotIn("dryRun", tool.real)
        self.assertNotIn("watch", tool.real)  # server default: the order shows
        self.assertEqual("attack", tool.real["action"])
        self.assertEqual("334862", tool.real["target"])
        self.assertEqual("AttackMelee", order["job"])
        self.assertEqual("attack", combat.load_ledger(self.path)["orders"]["p1"]["kind"])

    def test_attack_preview_is_one_read_and_does_not_touch_ledger(self):
        before = self.contents()
        tool = FakeOrderTool(order_reply(pawn=order_pawn(drafted=False)))
        reply = combat.preview_targeted_order(
            "attack", "Lucas", "114,141", hostile_snap(), self.ident,
            self.path, issuer=tool, mode="melee")
        self.assertTrue(reply["success"])
        self.assertEqual(1, len(tool.calls))
        self.assertEqual("114,141", tool.dry["target"])
        self.assertTrue(tool.dry["dryRun"])
        self.assertFalse(tool.dry["watch"])
        self.assertEqual("melee", tool.dry["mode"])
        self.assertEqual(before, self.contents())

    def test_attack_accepts_every_id_form_the_companion_accepts(self):
        for token in ("Thing_Wolf_Timber334862", "Wolf_Timber334862", "334862",
                      "timber wolf"):
            tool = FakeOrderTool(order_reply(pawn=order_pawn(drafted=True)),
                                 order_reply(pawn=order_pawn(drafted=True)))
            order = combat.issue_attack("Lucas", token, snap(100, drafted=True),
                                        self.ident, self.path, issuer=tool)
            self.assertEqual(token, tool.real["target"])
            self.assertEqual("Thing_Wolf_Timber334862", order["targetId"])

    def test_attack_accepts_a_pawn_by_id_form_as_well_as_by_name(self):
        ledger = combat.load_ledger(self.path)
        ledger["pawns"] = {"Thing_Human1049": {"name": "Lucas",
                                               "originalDrafted": False}}
        combat._atomic_write(ledger, self.path)
        for token in ("Thing_Human1049", "Human1049", "1049", "lucas"):
            self.assertEqual("Thing_Human1049",
                             combat._resolve_session_pawn(token, ledger))

    def test_ambiguous_target_prints_candidates_and_never_drafts(self):
        candidates = [{"thingId": "Thing_Rat361788", "name": "Rat",
                       "defName": "Rat", "position": {"x": 3, "z": 4},
                       "distance": 2},
                      {"thingId": "Thing_Rat361999", "name": "Rat",
                       "defName": "Rat", "position": {"x": 9, "z": 9},
                       "distance": 8}]
        tool = FakeOrderTool(order_reply(success=False, errorKind="ambiguous",
                                         error="'Rat' names 2 things",
                                         target=None, job=None,
                                         candidates=candidates))
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            with self.assertRaisesRegex(combat.CombatRefusal, "ambiguous"):
                combat.issue_attack("Lucas", "Rat", hostile_snap(), self.ident,
                                    self.path, issuer=tool)
        self.assertIn("Thing_Rat361788", out.getvalue())
        self.assertIn("Thing_Rat361999", out.getvalue())
        # One call only: an ambiguity is settled before anything is drafted.
        self.assertEqual(1, len(tool.calls))
        self.assertEqual({}, combat.load_ledger(self.path)["draftObligations"])

    def test_a_downed_target_is_attacked_and_reported_as_a_finish(self):
        target = order_target(downed=True, hostileToPlayer=True,
                              name="muffalo", defName="Muffalo", distance=1)
        job = {"def": "AttackMelee", "verified": True,
               "killIncappedTarget": True, "verb": "fists"}
        tool = FakeOrderTool(order_reply(pawn=order_pawn(drafted=True), target=target, job=job),
                             order_reply(pawn=order_pawn(drafted=True), target=target, job=job))
        order = combat.issue_attack("Lucas", "Muffalo", hostile_snap(),
                                    self.ident, self.path, issuer=tool)
        self.assertTrue(order["finishing"])
        self.assertTrue(order["target"]["downed"])

    def test_a_non_hostile_predator_is_a_legal_target(self):
        # The wolf that killed Finn and Octave stopped being hostile between
        # the read and the order, and the old attack path refused it. Vanilla
        # does not, and neither does this: hostility is reported, not required.
        tool = FakeOrderTool(order_reply(pawn=order_pawn(drafted=True)),
                             order_reply(pawn=order_pawn(drafted=True)))
        order = combat.issue_attack("Lucas", "timber wolf",
                                    snap(100, drafted=True), self.ident,
                                    self.path, issuer=tool)
        self.assertFalse(order["target"]["hostileToPlayer"])
        self.assertTrue(order["target"]["predator"])
        self.assertIn("NOT hostile", combat.describe_target(order["target"]))
        self.assertIn("PREDATOR", combat.describe_target(order["target"]))

    def test_incapable_of_violence_is_the_games_refusal_with_its_own_kind(self):
        pawn = order_pawn(incapableOfViolence=True, canBeDrafted=True)
        tool = FakeOrderTool(order_reply(
            success=False, errorKind="incapable_of_violence",
            error="Finn is incapable of Violence", pawn=pawn, job=None))
        with self.assertRaisesRegex(combat.CombatRefusal,
                                    "incapable_of_violence"):
            combat.issue_attack("Lucas", "timber wolf", hostile_snap(),
                                self.ident, self.path, issuer=tool)
        # ...and the pawn is still draftable, which is the distinction the
        # Lampblack fork guessed wrong and never checked.
        self.assertIn("canBeDrafted=True", combat.describe_pawn(pawn))
        self.assertEqual({}, combat.load_ledger(self.path)["draftObligations"])

    def test_a_refusal_after_an_auto_draft_still_owes_the_undraft(self):
        # The dangerous case: the game drafted them, THEN refused the job.
        # Nothing but the ledger will ever undraft that colonist.
        tool = FakeOrderTool(
            order_reply(pawn=order_pawn(drafted=False)),
            order_reply(success=False, errorKind="job_refused",
                        error="the pawn would not take the job",
                        pawn=order_pawn(drafted=True, auto=True), job=None))
        with self.assertRaisesRegex(combat.CombatRefusal, "job_refused"):
            combat.issue_attack("Lucas", "timber wolf", hostile_snap(),
                                self.ident, self.path, issuer=tool)
        obligation = combat.load_ledger(self.path)["draftObligations"]["p1"]
        self.assertEqual("confirmed", obligation["state"])
        self.assertTrue(combat.load_ledger(self.path)["cleanupPending"])

    def test_attack_write_ahead_intent_precedes_the_ordering_call(self):
        states = []
        class Watching(FakeOrderTool):
            def __call__(self, params):
                if not params.get("dryRun"):
                    states.append((combat.load_ledger(self.path_ref)
                                   ["draftObligations"].get("p1") or {}).get("state"))
                return FakeOrderTool.__call__(self, params)
        tool = Watching(order_reply(pawn=order_pawn(drafted=False)),
                        order_reply(pawn=order_pawn(drafted=True, auto=True)))
        tool.path_ref = self.path
        order = combat.issue_attack("Lucas", "timber wolf", hostile_snap(),
                                    self.ident, self.path, issuer=tool)
        self.assertEqual(["intent"], states)
        self.assertTrue(order["autoDrafted"])
        self.assertEqual("confirmed", combat.load_ledger(self.path)
                         ["draftObligations"]["p1"]["state"])

    def test_tend_drafts_the_doctor_and_the_ledger_owes_the_undraft(self):
        # Ground tending is real and is DRAFTED tending, so the doctor who
        # knelt over Octave is a cleanup obligation like any other.
        patient = order_target(name="Octave", defName="Human", downed=True,
                               hostileToPlayer=False, predator=False)
        job = {"def": "TendPatient", "verified": True}
        tool = FakeOrderTool(
            order_reply(action="tend", pawn=order_pawn(drafted=False),
                        target=patient, job=job),
            order_reply(action="tend", draftedTend=True, target=patient, job=job,
                        pawn=order_pawn(drafted=True, auto=True,
                                        incapableOfViolence=True)))
        order = combat.issue_tend("Lucas", "Octave", snap(100), self.ident,
                                  self.path, issuer=tool)
        self.assertEqual("tend", tool.real["action"])
        self.assertTrue(tool.dry["dryRun"])
        self.assertTrue(order["autoDrafted"])
        self.assertTrue(order["draftedTend"])
        ledger = combat.load_ledger(self.path)
        self.assertEqual("confirmed", ledger["draftObligations"]["p1"]["state"])
        self.assertTrue(ledger["cleanupPending"])
        # A doctor being shot at is news: no attacker threshold for tending.
        args = combat._combat_watch_args(ledger, 5, "Normal")
        self.assertEqual("p1", args["injuryPawnIds"])
        self.assertEqual(combat.COMBAT_INJURY_SEVERITY_DELTA,
                         args["injuryMinSeverityDelta"])

    def test_rescue_is_recorded_without_creating_an_obligation(self):
        patient = order_target(name="Octave", defName="Human", downed=True)
        job = {"def": "Rescue", "verified": True}
        tool = FakeOrderTool(
            order_reply(action="rescue", pawn=order_pawn(drafted=False),
                        target=patient, job=job),
            order_reply(action="rescue", pawn=order_pawn(drafted=False),
                        target=patient, job=job))
        order = combat.issue_rescue("Lucas", "Octave", snap(100), self.ident,
                                    self.path, issuer=tool)
        self.assertFalse(order["autoDrafted"])
        ledger = combat.load_ledger(self.path)
        self.assertEqual("rescue", ledger["orders"]["p1"]["kind"])
        # Recorded but undrafted: no obligation, and yet still watched, because
        # Octave bled out being carried 110 cells toward a bed.
        self.assertEqual({}, ledger["draftObligations"])
        self.assertEqual("p1", combat._combat_watch_args(ledger, 5, "Normal")
                         ["injuryPawnIds"])

    def test_a_tend_refused_after_the_draft_still_owes_the_undraft(self):
        tool = FakeOrderTool(
            order_reply(action="tend", pawn=order_pawn(drafted=False)),
            order_reply(action="tend", success=False, errorKind="job_refused",
                        error="no medicine within reach", job=None,
                        pawn=order_pawn(drafted=True, auto=True)))
        with self.assertRaisesRegex(combat.CombatRefusal, "job_refused"):
            combat.issue_tend("Lucas", "Octave", snap(100), self.ident,
                              self.path, issuer=tool)
        self.assertEqual("confirmed", combat.load_ledger(self.path)
                         ["draftObligations"]["p1"]["state"])

    def test_stale_attack_refuses_before_the_companion_is_called(self):
        with self.assertRaisesRegex(combat.CombatRefusal, "STALE"):
            combat.issue_attack("Lucas", "timber wolf", hostile_snap(99),
                                self.ident, self.path,
                                issuer=lambda params: self.fail("called the game"))

    def test_draft_records_the_obligation_and_undraft_of_it_is_a_release(self):
        tool = FakeOrderTool(
            order_reply(action="resolve", pawn=order_pawn(drafted=False),
                        target=None, job=None),
            order_reply(action="draft", pawn=order_pawn(drafted=True, auto=True),
                        target=None, job=None))
        combat.issue_draft("Lucas", snap(100), self.ident, self.path,
                           issuer=tool)
        self.assertEqual("confirmed", combat.load_ledger(self.path)
                         ["draftObligations"]["p1"]["state"])
        result = combat.issue_undraft(
            "Lucas", snap(101, drafted=True), self.ident, self.path,
            issuer=lambda params: self.fail("went round the ledger"),
            set_draft=lambda pid, val: {"drafted": val})
        self.assertTrue(result["released"])
        after = combat.load_ledger(self.path)
        self.assertEqual({}, after["draftObligations"])
        self.assertTrue(after["active"])

    def test_undraft_of_an_unobligated_pawn_goes_straight_to_the_game(self):
        tool = FakeOrderTool(order_reply(action="undraft",
                                         pawn=order_pawn(drafted=False),
                                         target=None, job=None))
        result = combat.issue_undraft("Lucas", snap(100, drafted=True),
                                      self.ident, self.path, issuer=tool)
        self.assertFalse(result["released"])
        self.assertEqual("undraft", tool.calls[0]["action"])
        self.assertEqual("undraft",
                         combat.load_ledger(self.path)["orders"]["p1"]["kind"])

    def test_already_drafted_pawn_is_not_given_a_phantom_obligation(self):
        tool = FakeOrderTool(order_reply(action="resolve",
                                         pawn=order_pawn(drafted=True),
                                         target=None, job=None))
        order = combat.issue_draft("Lucas", snap(100, drafted=True), self.ident,
                                   self.path, issuer=tool)
        self.assertTrue(order["alreadyDrafted"])
        self.assertEqual(1, len(tool.calls))
        self.assertEqual({}, combat.load_ledger(self.path)["draftObligations"])

    def test_flee_records_intent_and_injury_hook_stops(self):
        def issue(pid, x, z, before_draft):
            return {"pawnId": pid, "pawnName": "Lucas", "accepted": True,
                    "autoDrafted": False, "arrived": False}
        combat.issue_flee("Lucas", 12, 34, snap(100, drafted=True), self.ident,
                          self.path, issuer=issue)
        seen = []
        fleeing_snap = snap(100, drafted=True)
        fleeing_snap["colonists"][0].update({"position": {"x": 1, "z": 1},
                                             "job": "Goto"})
        result = combat.advance(5, fleeing_snap, self.ident,
                                self.path,
                                waiter=lambda args: seen.append(args) or {
                                    "stopReason": "pawn_injury_hook", "ticksGame": 104,
                                    "baseline": {"watchedPawns": []},
                                    "injuryHookEvent": {"pawnId": "p1", "damageDef": "Bite"}})
        self.assertEqual("pawn_injury_hook", result["stopReason"])
        self.assertEqual("", seen[0]["watchedPawnIds"])
        self.assertEqual("p1", seen[0]["meleeThreatPawnIds"])
        self.assertEqual("p1", seen[0]["injuryPawnIds"])
        self.assertFalse(seen[0]["watchMeleeThreats"])
        self.assertTrue(seen[0]["watchInjuryHook"])
        self.assertFalse(seen[0]["watchPawnOrders"])
        self.assertFalse(seen[0]["injuryStopOnNew"])
        # Edge-triggered: a hostile arriving mid-pulse stops the clock, the
        # ones baselined at entry do not.
        self.assertTrue(seen[0]["watchHostiles"])
        self.assertTrue(seen[0]["ignoreCurrentHostiles"])
        self.assertEqual(0, seen[0]["huntWithin"])
        still_fleeing = snap(104, drafted=True)
        still_fleeing["colonists"][0].update({"position": {"x": 2, "z": 2},
                                               "job": "Goto"})
        with self.assertRaisesRegex(combat.CombatRefusal, "decision required"):
            combat.advance(5, still_fleeing, self.ident, self.path,
                           waiter=lambda _: self.fail("hot-looped companion"))

    def test_arrived_flee_is_retired_before_watching(self):
        def issue(pid, x, z, before_draft):
            return {"pawnId": pid, "pawnName": "Lucas", "accepted": True,
                    "autoDrafted": False, "arrived": False,
                    "destination": {"x": x, "z": z}, "job": "Goto"}
        combat.issue_flee("Lucas", 12, 34, snap(drafted=True), self.ident,
                          self.path, issuer=issue)
        arrived = snap(101, drafted=True)
        arrived["colonists"][0].update({"position": {"x": 12, "z": 34},
                                        "job": "Wait_Combat"})
        seen = []
        combat.advance(1, arrived, self.ident, self.path,
                       waiter=lambda args: seen.append(args) or {
                           "stopReason": "budget_elapsed", "ticksGame": 102,
                           "baseline": {}})
        order = combat.load_ledger(self.path)["orders"]["p1"]
        self.assertEqual("flee_complete", order["kind"])
        self.assertTrue(order["completed"])
        self.assertFalse(seen[0]["watchMeleeThreats"])
        self.assertFalse(seen[0]["watchInjuryHook"])
        self.assertFalse(seen[0]["watchPawnOrders"])

    def test_flee_order_change_pauses_and_requires_new_decision(self):
        ledger = combat.load_ledger(self.path)
        ledger["orders"] = {"p1": {"kind": "flee"}}
        combat._atomic_write(ledger, self.path)
        active = snap(100, drafted=True)
        active["colonists"][0].update({"position": {"x": 1, "z": 1},
                                       "job": "Goto"})
        combat.advance(5, active, self.ident, self.path,
                       waiter=lambda _: {
                           "stopReason": "pawn_order_changed", "ticksGame": 103,
                           "baseline": {}, "pawnOrderChanges": [
                               {"pawnId": "p1", "newJob": "Wait_Combat"}]},
                       snapshot_reader=lambda: active)
        final = combat.load_ledger(self.path)
        self.assertEqual("flee_complete", final["orders"]["p1"]["kind"])
        self.assertIn("p1", final["pendingDecisions"])

    def test_flee_order_change_at_destination_is_normal_arrival(self):
        ledger = combat.load_ledger(self.path)
        ledger["orders"] = {"p1": {"kind": "flee",
                                      "destination": {"x": 12, "z": 34}}}
        combat._atomic_write(ledger, self.path)
        active = snap(100, drafted=True)
        active["colonists"][0].update({"position": {"x": 1, "z": 1},
                                       "job": "Goto"})
        arrived = snap(103, drafted=True)
        arrived["colonists"][0].update({"position": {"x": 12, "z": 34},
                                        "job": "Wait_Combat"})
        combat.advance(5, active, self.ident, self.path,
                       waiter=lambda _: {
                           "stopReason": "pawn_order_changed", "ticksGame": 103,
                           "baseline": {}, "pawnOrderChanges": [
                               {"pawnId": "p1", "completed": True}]},
                       snapshot_reader=lambda: arrived)
        final = combat.load_ledger(self.path)
        self.assertTrue(final["orders"]["p1"]["completed"])
        self.assertNotIn("p1", final.get("pendingDecisions", {}))

    def test_advance_human_pause_is_returned_and_never_restarted(self):
        calls = []
        result = combat.advance(5, snap(), self.ident, self.path,
                                waiter=lambda args: calls.append(args) or {
                                    "stopReason": "external_pause", "ticksGame": 101,
                                    "baseline": {"watchedPawns": []}})
        self.assertEqual("external_pause", result["stopReason"])
        self.assertEqual(1, len(calls))
        # The next advance would lift the person's pause: refuse until the
        # operator says --resume, then run exactly once.
        with self.assertRaisesRegex(combat.CombatRefusal, "resume"):
            combat.advance(5, snap(101), self.ident, self.path,
                           waiter=lambda _: self.fail("lifted a human pause"))
        combat.advance(5, snap(101), self.ident, self.path, resume=True,
                       waiter=lambda args: calls.append(args) or {
                           "stopReason": "budget_elapsed", "endTick": 400,
                           "success": True})
        self.assertEqual(2, len(calls))
        combat.advance(5, snap(400), self.ident, self.path,
                       waiter=lambda args: calls.append(args) or {
                           "stopReason": "budget_elapsed", "endTick": 700,
                           "success": True})
        self.assertEqual(3, len(calls))

    def test_advance_refuses_when_pause_did_not_take(self):
        paused = []
        with self.assertRaisesRegex(combat.CombatRefusal, "PAUSE DID NOT TAKE"):
            combat.advance(5, snap(), self.ident, self.path,
                           waiter=lambda _: {"stopReason": "letter", "success": False,
                                             "endTick": 300,
                                             "error": "PAUSE DID NOT TAKE. letter fired"},
                           pause=lambda: paused.append(True) or True)
        self.assertEqual([True], paused)
        ledger = combat.load_ledger(self.path)
        self.assertEqual(100, ledger["watermarkTick"])
        self.assertNotIn("lastStop", ledger)

    def test_advance_treats_cancelled_and_session_changed_as_failures(self):
        for reason in ("cancelled", "session_changed"):
            paused = []
            with self.assertRaisesRegex(combat.CombatRefusal, reason):
                combat.advance(5, snap(), self.ident, self.path,
                               waiter=lambda _: {"stopReason": reason, "success": False,
                                                 "endTick": 999},
                               pause=lambda: paused.append(True) or True)
            self.assertEqual([True], paused)
            self.assertEqual(100, combat.load_ledger(self.path)["watermarkTick"])

    def test_advance_over_the_cap_is_clamped_and_says_so(self):
        # 2026-09-08: `advance 25` was refused for being over the 20 s cap and
        # the refusal PAUSED the game. A number too big is a number to clamp.
        calls = []
        result = combat.advance(25, snap(), self.ident, self.path,
                                waiter=lambda args: calls.append(args) or {
                                    "stopReason": "budget_elapsed",
                                    "endTick": 400, "success": True})
        self.assertEqual(1, len(calls))
        self.assertEqual(20000, calls[0]["maxDurationMs"])
        self.assertEqual(25.0, result["advanceClamped"]["asked"])
        self.assertEqual(20.0, result["advanceClamped"]["used"])
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            combat.print_advance_result(result)
        self.assertIn("advance 25 -> 20 (cap)", out.getvalue())

    def test_a_pulse_inside_the_cap_carries_no_clamp_note(self):
        result = combat.advance(20, snap(), self.ident, self.path,
                                waiter=lambda args: {
                                    "stopReason": "budget_elapsed",
                                    "endTick": 400, "success": True,
                                    "askedMs": args["maxDurationMs"]})
        self.assertEqual(20000, result["askedMs"])
        self.assertNotIn("advanceClamped", result)

    def test_a_non_positive_advance_refuses_without_a_pulse_or_a_pause(self):
        with self.assertRaisesRegex(combat.ArgumentRefusal, "must be positive"):
            combat.advance(0, snap(), self.ident, self.path,
                           waiter=lambda _: self.fail("ran a zero pulse"))
        self.assertTrue(issubclass(combat.ArgumentRefusal, combat.CombatRefusal))

    def test_the_cli_never_pauses_over_a_number_it_did_not_like(self):
        with mock.patch.object(combat.rim, "init"), \
             mock.patch.object(combat, "_live_snapshot", return_value=snap()), \
             mock.patch.object(combat, "_identity", return_value=self.ident), \
             mock.patch.object(combat, "_pause_now") as pause, \
             mock.patch.object(combat, "LEDGER", self.path), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = combat.main(["advance", "0"])
        self.assertEqual(1, rc)
        pause.assert_not_called()
        self.assertIn("must be positive", out.getvalue())
        self.assertNotIn("CLOCK LEFT PAUSED", out.getvalue())

    def test_interrupted_flee_latches_a_decision_instead_of_clearing_it(self):
        ledger = combat.load_ledger(self.path)
        ledger["orders"] = {"p1": {"kind": "flee",
                                      "destination": {"x": 12, "z": 34}}}
        ledger["pendingDecisions"] = {"p1": {"reason": "injured while fleeing"}}
        combat._atomic_write(ledger, self.path)
        downed = snap(104, drafted=True)
        downed["colonists"][0].update({"position": {"x": 3, "z": 3},
                                       "job": "Wait_Downed", "downed": True})
        with self.assertRaisesRegex(combat.CombatRefusal, "injured while fleeing"):
            combat.advance(5, downed, self.ident, self.path,
                           waiter=lambda _: self.fail("advanced over a downed escapee"))
        final = combat.load_ledger(self.path)
        self.assertEqual("flee_complete", final["orders"]["p1"]["kind"])
        self.assertFalse(final["orders"]["p1"]["completed"])
        # A Goto that ended short of the destination with no prior latch is
        # itself a decision point.
        ledger = combat.load_ledger(self.path)
        ledger["orders"] = {"p1": {"kind": "flee",
                                      "destination": {"x": 12, "z": 34}}}
        ledger["pendingDecisions"] = {}
        combat._atomic_write(ledger, self.path)
        with self.assertRaisesRegex(combat.CombatRefusal, "flee interrupted"):
            combat.advance(5, downed, self.ident, self.path,
                           waiter=lambda _: self.fail("advanced over an interrupted flee"))

    def test_force_end_drops_obligation_for_a_pawn_who_is_gone(self):
        self.obligate()
        lines = combat.reconcile(snap(102, pawn=False), self.ident, force=True,
                                 path=self.path,
                                 set_draft=lambda *_: self.fail("mutated"))
        self.assertTrue(any("no longer a colonist" in line for line in lines))
        self.assertFalse(os.path.exists(self.path))

    def test_force_end_discards_a_stale_ledger_without_touching_the_game(self):
        self.obligate()
        lines = combat.reconcile(snap(99), self.ident, force=True, path=self.path,
                                 set_draft=lambda *_: self.fail("mutated"))
        self.assertIn("STALE ledger discarded", lines[0])
        self.assertFalse(os.path.exists(self.path))

    def test_begin_refuses_a_stale_ledger_instead_of_returning_it(self):
        with self.assertRaisesRegex(combat.CombatRefusal, "end --force"):
            combat.begin(snap(99), self.ident, self.path)

    def test_unknown_pawn_token_never_latches_a_decision(self):
        combat.record_move_failure("Jon", 12, 34, "typo", self.path)
        self.assertEqual({}, combat.load_ledger(self.path).get("pendingDecisions", {}))

    def test_numeric_companion_id_maps_to_full_ledger_id(self):
        ledger = combat.load_ledger(self.path)
        ledger["orders"] = {"Thing_Human1049": {"kind": "flee"}}
        self.assertEqual("Thing_Human1049",
                         combat._ledger_pawn_id(ledger, 1049))

    def test_advance_refuses_unavailable_and_forces_pause(self):
        paused = []
        with self.assertRaisesRegex(combat.CombatRefusal, "unavailable"):
            combat.advance(5, snap(), self.ident, self.path,
                           waiter=lambda _: {"stopReason": "unavailable",
                                             "stopDetail": "old DLL"},
                           pause=lambda: paused.append(True))
        self.assertEqual([True], paused)

    def test_advance_is_one_edge_triggered_call_not_a_hot_loop(self):
        calls = []
        combat.advance(20, snap(), self.ident, self.path,
                       waiter=lambda args: calls.append(args) or {
                           "stopReason": "hostile", "ticksGame": 100,
                           "baseline": {"hostiles": ["same"]}})
        self.assertEqual(1, len(calls))

    def test_combat_camera_is_server_side_and_manual_override_persists(self):
        calls = []
        combat.advance(20, snap(), self.ident, self.path,
                       waiter=lambda args: calls.append(args) or {
                           "stopReason": "budget_elapsed", "ticksGame": 100,
                           "combatCamera": {"manualOverride": True}})
        self.assertTrue(calls[0]["combatCamera"])
        self.assertEqual(8000, calls[0]["combatCameraIntervalMs"])
        self.assertEqual(20.0, calls[0]["combatCameraHostileRadius"])
        self.assertEqual(3500, calls[0]["combatCameraCooldownMs"])
        self.assertEqual(0, combat.load_ledger(self.path)["ui"].get(
            "cameraManualSuppressUntilUnixMs", 0))
        calls.clear()
        combat.advance(20, snap(), self.ident, self.path,
                       waiter=lambda args: calls.append(args) or {
                           "stopReason": "budget_elapsed", "ticksGame": 102})
        self.assertTrue(calls[0]["combatCamera"])

    def test_manual_camera_suppression_round_trips_without_disabling_director(self):
        calls = []
        combat.advance(5, snap(), self.ident, self.path,
                       waiter=lambda args: calls.append(args) or {
                           "stopReason": "budget_elapsed", "ticksGame": 100,
                           "combatCamera": {"manualOverride": True,
                                            "manualSuppressUntilUnixMs": 987654321}})
        combat.advance(5, snap(), self.ident, self.path,
                       waiter=lambda args: calls.append(args) or {
                           "stopReason": "budget_elapsed", "ticksGame": 100})
        self.assertTrue(calls[1]["combatCamera"])
        self.assertEqual(987654321,
                         calls[1]["combatCameraManualSuppressUntilUnixMs"])
        self.assertEqual(20000, calls[1]["combatCameraManualSuppressMs"])

    def test_combat_camera_frame_timestamp_round_trips_between_pulses(self):
        calls = []
        combat.advance(5, snap(), self.ident, self.path,
                       waiter=lambda args: calls.append(args) or {
                           "stopReason": "budget_elapsed", "ticksGame": 100,
                           "combatCamera": {"lastFrameUnixMs": 123456789}})
        combat.advance(5, snap(), self.ident, self.path,
                       waiter=lambda args: calls.append(args) or {
                           "stopReason": "budget_elapsed", "ticksGame": 100})
        self.assertEqual(0, calls[0]["combatCameraLastFrameUnixMs"])
        self.assertEqual(123456789, calls[1]["combatCameraLastFrameUnixMs"])

    def test_normal_run_call_omits_combat_probe_parameters(self):
        payloads = []
        def game(tool, args, strict=True):
            if tool == run.TOOL:
                payloads.append(args)
                return {"stopReason": "budget_elapsed", "ticksGame": 101,
                        "pausedByThisTool": False}
            if tool == "rimworld/list_letters":
                return {"letters": []}
            if tool == "rimworld/get_game_info":
                return {"ticksGame": 100}
            return {}
        # _ticks mocked as in test_run.py: the real one hands the tick to the
        # overlay on a fire-and-forget POST.
        with mock.patch.object(run.rim, "game", side_effect=game), \
             mock.patch.object(run, "_ticks", return_value=100):
            run._companion(None, .02, "Normal", .01, False)
        self.assertTrue(payloads)
        for key in ("watchedPawnIds", "watchMeleeThreats", "watchPawnOrders",
                    "watchInjuries", "watchInjuryHook"):
            self.assertNotIn(key, payloads[0])


    def test_advance_debounces_alerts_that_were_already_standing(self):
        calls = []
        result = combat.advance(5, snap(), self.ident, self.path,
                                waiter=lambda args: calls.append(args) or {
                                    "stopReason": "budget_elapsed",
                                    "ticksGame": 105, "baseline": {},
                                    "alertsDebounced": ["Low food"]})
        self.assertEqual(combat.COMBAT_ALERT_DEBOUNCE_MS,
                         calls[0]["alertDebounceMs"])
        self.assertEqual(["Low food"], result["alertsDebounced"])

    def test_attackers_get_the_higher_injury_threshold_and_fleers_do_not(self):
        ledger = combat.load_ledger(self.path)
        ledger["orders"] = {"p1": {"kind": "attack"}}
        combat._atomic_write(ledger, self.path)
        args = combat._combat_watch_args(combat.load_ledger(self.path), 5, "Normal")
        self.assertEqual(combat.COMBAT_ATTACK_INJURY_SEVERITY_DELTA,
                         args["injuryMinSeverityDelta"])
        self.assertTrue(args["watchInjuries"])
        # A mixed set keeps the LOWER threshold: the pawn who was not sent into
        # the fight is the one whose first wound is news.
        ledger["orders"] = {"p1": {"kind": "attack"}, "p2": {"kind": "move"}}
        self.assertEqual(combat.COMBAT_INJURY_SEVERITY_DELTA,
                         combat._combat_watch_args(ledger, 5, "Normal")
                         ["injuryMinSeverityDelta"])
        # Fleeing still arms the per-hit hook at the low threshold.
        ledger["orders"] = {"p1": {"kind": "flee"}, "p2": {"kind": "attack"}}
        fleeing = combat._combat_watch_args(ledger, 5, "Normal")
        self.assertEqual("p1", fleeing["injuryPawnIds"])
        self.assertEqual(combat.COMBAT_INJURY_SEVERITY_DELTA,
                         fleeing["injuryMinSeverityDelta"])

    def test_an_undrafted_pawn_is_no_longer_watched_for_injuries(self):
        ledger = combat.load_ledger(self.path)
        ledger["orders"] = {"p1": {"kind": "undraft"}}
        self.assertEqual("", combat._combat_watch_args(ledger, 5, "Normal")
                         ["injuryPawnIds"])

    def test_equip_still_walking_is_not_a_failure_and_keeps_the_order(self):
        ticket = {"kind": "equip", "pawnId": "p1", "accepted": True,
                  "completed": False, "weaponLabel": "revolver"}
        def equipment_waiter(got, waiter, seconds):
            reason, _ = waiter(lambda: False, seconds)
            # What combat_actions.wait_for_equipment now returns for a pawn
            # who is still walking to the weapon when the budget runs out.
            return dict(got, completed=False, stillWalking=True,
                        waitReason=reason, job="Equip")
        result = combat.finish_equip(
            ticket, 12, snap(), self.ident, self.path,
            equipment_waiter=equipment_waiter,
            advance_waiter=lambda args: {"stopReason": "budget_elapsed",
                                         "ticksGame": 110, "baseline": {}})
        self.assertTrue(result["stillWalking"])
        self.assertFalse(result["completed"])
        ledger = combat.load_ledger(self.path)
        self.assertEqual("Equip", ledger["orders"]["p1"]["job"])
        self.assertEqual({}, ledger.get("pendingDecisions", {}))


def pawn_row(name="Ben Cooper", thing_id="Human618", **kw):
    """One `home/list_pawns` row, the shape ListPawnsTool actually emits."""
    row = {"name": name, "defName": "Human", "kindDef": "Ghoul",
           "position": {"x": 120, "z": 118}, "faction": "Threadneedle",
           "hostile": False, "hostileReason": "none",
           "isColonist": False, "isFreeColonist": False, "isPrisoner": False,
           "animal": False, "humanlike": True, "predator": False,
           "tame": False, "wild": False, "mechanoid": False,
           "downed": False, "drafted": False, "dead": False,
           "job": "Wait_Wander", "mentalState": None,
           "nearestColonist": "Lucas", "nearestColonistDistance": 4,
           "settings": {"thingId": thing_id, "applies": True}}
    row.update(kw)
    return row


def colonist_row(name="Lucas", thing_id="Human1", **kw):
    return pawn_row(name, thing_id, kindDef="Colonist", isColonist=True,
                    isFreeColonist=True, **kw)


class FakeListPawns(object):
    """A scripted `home/list_pawns`. Answers each category query in turn."""

    def __init__(self, humanlikes=(), mechs=()):
        self.humanlikes = list(humanlikes)
        self.mechs = list(mechs)
        self.calls = []

    def __call__(self, params):
        self.calls.append(params)
        rows = self.mechs if params.get("mechanoidsOnly") else self.humanlikes
        return {"pawns": list(rows), "pawnsListed": len(rows)}


def asleep_snap(tick=100, drafted=True, count=5, distance=62, job="LayDown"):
    """A map with hostiles on it that are not a threat to anybody."""
    result = snap(tick, drafted, hostiles=count)
    result["threats"]["hostiles"] = [
        {"thingId": "bug%d" % i, "name": "Megascarab", "defName": "Megascarab",
         "hostile": True, "downed": False, "dead": False, "job": job,
         "distanceToNearestColonist": distance + i}
        for i in range(count)]
    return result


class CombatRosterTests(unittest.TestCase):
    """Threadneedle, 2026-09-08: `draft Ben` refused with 0 matches while the
    same read said canBeDrafted=True. Ben Cooper is a ghoul -- a colony pawn
    that is structurally not a colonist, so home/status cannot list him."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "combat.json")
        self.ident = {"save": "fixture", "loadedAt": 1, "loadedAtTick": 50,
                      "pid": 9}
        # Nothing in this module may reach the bridge, and a live reader left
        # armed by an earlier CLI test would do exactly that -- silently, and
        # only when the game happens to be running. Asserted, not just reset:
        # `main` is supposed to disarm it itself.
        self.assertEqual([], combat._ROSTER_READER)

    def tearDown(self):
        combat._ROSTER_READER[:] = []
        self.tmp.cleanup()

    def test_begin_enumerates_a_ghoul_the_colonist_read_cannot_see(self):
        reader = FakeListPawns([colonist_row(), pawn_row()])
        ledger, made = combat.begin(snap(), self.ident, self.path,
                                    roster_reader=reader)
        self.assertTrue(made)
        self.assertIn("Thing_Human618", ledger["pawns"])
        row = ledger["pawns"]["Thing_Human618"]
        self.assertEqual("Ben Cooper", row["name"])
        self.assertEqual("ghoul", row["kind"])
        self.assertFalse(row["originalDrafted"])
        # Two queries, humanlikes and mechanoids, and both ask for settings{}
        # because that is where list_pawns carries a ThingID at all.
        self.assertEqual(2, len(reader.calls))
        self.assertTrue(all(c["settings"] for c in reader.calls))

    def test_begin_never_adopts_a_prisoner_an_animal_or_a_raider(self):
        reader = FakeListPawns([
            colonist_row(),
            pawn_row("Sarah", "Human700", isPrisoner=True),
            pawn_row("muffalo", "Muffalo9", animal=True, humanlike=False,
                     tame=True),
            pawn_row("raider", "Human900", faction="Pirates", hostile=True)])
        ledger, _ = combat.begin(snap(), self.ident, self.path,
                                 roster_reader=reader)
        self.assertEqual(["Thing_Human1", "p1"], sorted(ledger["pawns"]))

    def test_begin_adopts_a_colony_mech(self):
        reader = FakeListPawns(
            [colonist_row()],
            [pawn_row("Militor", "Mech55", kindDef="Mech_Militor",
                      humanlike=False, mechanoid=True)])
        ledger, _ = combat.begin(snap(), self.ident, self.path,
                                 roster_reader=reader)
        self.assertEqual("colony mech",
                         ledger["pawns"]["Thing_Mech55"]["kind"])

    def test_a_verb_adopts_the_pawn_the_roster_missed_and_says_so(self):
        combat.begin(snap(), self.ident, self.path)
        reader = FakeListPawns([colonist_row(), pawn_row()])
        combat._ROSTER_READER[:] = [reader]
        orders = FakeOrderTool(
            order_reply(action="resolve",
                        pawn=order_pawn(thingId="Thing_Human618",
                                        name="Ben Cooper")),
            order_reply(action="draft",
                        pawn=order_pawn(thingId="Thing_Human618",
                                        name="Ben Cooper", drafted=True)))
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            order = combat.issue_draft("Ben", snap(), self.ident, self.path,
                                       issuer=orders)
        self.assertIn("ADOPTED Ben Cooper into the ledger", out.getvalue())
        self.assertEqual("Thing_Human618", order["pawnId"])
        ledger = combat.load_ledger(self.path)
        self.assertTrue(ledger["pawns"]["Thing_Human618"]["adopted"])
        self.assertIn("Thing_Human618", ledger["draftObligations"])

    def test_a_library_caller_with_no_reader_refuses_without_a_bridge_call(self):
        combat.begin(snap(), self.ident, self.path)
        with mock.patch.object(combat.rim, "game",
                               side_effect=AssertionError("reached the bridge")):
            with self.assertRaisesRegex(combat.PawnNotInRoster,
                                        "combat.py adopt"):
                combat._checked_session("Ben", snap(), self.ident, self.path,
                                        "draft")

    def test_the_cli_disarms_its_live_reader_on_the_way_out(self):
        # The leak this catches: `main` armed _ROSTER_READER with the live
        # home/list_pawns caller and left it armed, so the NEXT caller in the
        # process reached the bridge through a function it gave no reader to.
        for argv, patches in (
                (["advance", "0"], {}),
                (["status"], {"_identity": lambda: self.ident})):
            with mock.patch.object(combat.rim, "init"), \
                 mock.patch.object(combat.rim, "game", return_value={}), \
                 mock.patch.object(combat, "LEDGER", self.path), \
                 mock.patch.object(combat, "_identity",
                                   return_value=self.ident), \
                 mock.patch.object(combat, "_live_snapshot",
                                   side_effect=lambda *a, **k: snap()), \
                 mock.patch("sys.stdout", new_callable=io.StringIO):
                combat.main(argv)
            self.assertEqual([], combat._ROSTER_READER, argv)
            self.assertEqual([], combat._CLOCK_PAUSED_BY, argv)

    def test_adopt_refuses_somebody_the_colony_does_not_control(self):
        combat.begin(snap(), self.ident, self.path)
        reader = FakeListPawns([colonist_row(),
                                pawn_row("Dagon", "Human900",
                                         faction="Pirates", hostile=True)])
        with self.assertRaisesRegex(combat.CombatRefusal,
                                    "not one colony-controlled draftable"):
            combat.adopt_pawn("Dagon", self.path, reader)
        self.assertNotIn("Thing_Human900",
                         combat.load_ledger(self.path)["pawns"])

    def test_adopt_records_the_draft_state_it_found_them_in(self):
        combat.begin(snap(), self.ident, self.path)
        reader = FakeListPawns([colonist_row(), pawn_row(drafted=True)])
        pid, _ = combat.adopt_pawn("Ben Cooper", self.path, reader,
                                   announce=False)
        self.assertEqual("Thing_Human618", pid)
        self.assertTrue(
            combat.load_ledger(self.path)["pawns"][pid]["originalDrafted"])

    def test_an_adopted_pawn_is_restored_by_end_without_force(self):
        # home/status cannot list a ghoul, so cleanup asks home/order for that
        # one pawn instead of calling them dead and demanding --force.
        combat.begin(snap(), self.ident, self.path)
        reader = FakeListPawns([colonist_row(), pawn_row()])
        combat.adopt_pawn("Ben", self.path, reader, announce=False)
        combat.record_draft_intent("Thing_Human618", "Ben Cooper", self.path)
        combat.confirm_drafted("Thing_Human618", 101, self.path)
        calls = []
        lines = combat.reconcile(
            snap(102), self.ident, path=self.path,
            resolver=lambda pid: {"pawn": {"name": "Ben Cooper",
                                           "spawned": True, "dead": False,
                                           "drafted": True}},
            set_draft=lambda pid, val: calls.append((pid, val)) or {"drafted": val})
        self.assertEqual([("Thing_Human618", False)], calls)
        self.assertIn("Ben Cooper: drafted -> undrafted", chr(10).join(lines))
        self.assertFalse(os.path.exists(self.path))

    def test_an_adopted_pawn_is_undrafted_through_home_order(self):
        orders = FakeOrderTool({"success": True, "action": "undraft",
                                "after": {"drafted": False}})
        self.assertEqual({"drafted": False, "error": None},
                         combat._restore_draft("Thing_Human618", False,
                                               adopted=True, caller=orders))
        self.assertEqual({"action": "undraft", "pawn": "Thing_Human618"},
                         orders.calls[0])

    def test_the_companions_own_ghoul_and_playerFaction_bools_are_honoured(self):
        # Added to list_pawns rows 2026-09-11, and read through the one shared
        # predicate `pawns.is_colony_member`. When the bools are there they
        # decide it; when they are not, the faction NAME still has to.
        row = pawn_row(ghoul=True, playerFaction=True, faction="Threadneedle")
        self.assertTrue(pawns.is_colony_member(row))
        self.assertTrue(combat.is_colony_draftable(row))
        self.assertEqual("ghoul", combat._roster_kind(row))
        old = pawn_row(faction="Threadneedle")          # a pre-2026-09-11 DLL
        self.assertFalse(pawns.is_colony_member(old))
        self.assertTrue(combat.is_colony_draftable(old, "Threadneedle"))
        theirs = pawn_row("Zed", "Human900", ghoul=True, playerFaction=False,
                          faction="Threadneedle")
        self.assertFalse(combat.is_colony_draftable(theirs, "Threadneedle"))

    def test_the_ledger_id_is_the_status_id_form(self):
        self.assertEqual("Thing_Human618", combat.roster_id(pawn_row()))
        self.assertEqual("Thing_Human618",
                         combat.roster_id({"thingId": "Thing_Human618"}))
        self.assertIsNone(combat.roster_id({"name": "nobody"}))


class CombatEndThreatTests(unittest.TestCase):
    """Threadneedle, 2026-09-08: `end` paused the clock and THEN refused, over
    insectoids asleep 60+ cells away; only --force closed it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "combat.json")
        self.ident = {"save": "fixture", "loadedAt": 1, "loadedAtTick": 50,
                      "pid": 9}
        combat.begin(snap(), self.ident, self.path)
        combat.record_draft_intent("p1", "Lucas", self.path)
        combat.confirm_drafted("p1", 101, self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_sleeping_hostiles_do_not_block_end_and_are_named_once(self):
        calls = []
        lines = combat.reconcile(
            asleep_snap(102), self.ident, path=self.path,
            set_draft=lambda pid, val: calls.append((pid, val)) or {"drafted": val})
        self.assertEqual([("p1", False)], calls)
        text = chr(10).join(lines)
        self.assertIn("still on the map, not a threat: 5 Megascarab asleep "
                      "62+ cells away", text)
        self.assertEqual(1, text.count("still on the map"))
        self.assertFalse(os.path.exists(self.path))

    def test_a_dormant_hostile_two_cells_away_does_not_block_end(self):
        lines = combat.reconcile(
            asleep_snap(102, count=1, distance=2, job="Wait_AsleepDormancy"),
            self.ident, path=self.path,
            set_draft=lambda pid, val: {"drafted": val})
        self.assertIn("1 Megascarab dormant 2+ cells away", chr(10).join(lines))

    def test_a_hostile_beyond_weapon_range_does_not_block_end(self):
        lines = combat.reconcile(
            asleep_snap(102, count=1, distance=80, job="Goto"), self.ident,
            path=self.path, set_draft=lambda pid, val: {"drafted": val})
        self.assertIn("1 Megascarab far off 80+ cells away",
                      chr(10).join(lines))

    def test_an_awake_hostile_inside_the_threshold_still_refuses(self):
        with self.assertRaisesRegex(combat.CombatRefusal, "--force"):
            combat.reconcile(
                asleep_snap(102, count=1, distance=9, job="AttackMelee"),
                self.ident, path=self.path,
                set_draft=lambda *_: self.fail("mutated"))

    def test_a_refusal_never_touches_the_clock(self):
        with self.assertRaisesRegex(combat.CombatRefusal, "hostile"):
            combat.reconcile(
                hostile_snap(102, drafted=True), self.ident, path=self.path,
                before_write=lambda: self.fail("paused before refusing"),
                set_draft=lambda *_: self.fail("mutated"))

    def test_the_pause_happens_once_and_only_before_a_real_write(self):
        order = []
        combat.reconcile(
            asleep_snap(102), self.ident, path=self.path,
            before_write=lambda: order.append("pause"),
            set_draft=lambda pid, val: order.append("write") or {"drafted": val})
        self.assertEqual(["pause", "write"], order)

    def test_a_dry_run_pauses_nothing_at_all(self):
        lines = combat.reconcile(
            asleep_snap(102), self.ident, dry_run=True, path=self.path,
            before_write=lambda: self.fail("a dry run paused the game"),
            set_draft=lambda *_: self.fail("mutated"))
        self.assertEqual("DRY RUN", lines[0])
        self.assertIn("still on the map", chr(10).join(lines))

    def test_a_downed_raider_still_gets_its_own_reminder(self):
        lines = combat.reconcile(
            hostile_snap(102, drafted=True, downed=True), self.ident,
            path=self.path, set_draft=lambda pid, val: {"drafted": val})
        self.assertIn("every remaining hostile is DOWNED", chr(10).join(lines))

    def test_the_downed_reminder_does_not_depend_on_undrafting_anybody(self):
        """PLAYBOOK: "a downed one proceeds with the finish-off/capture
        reminder" -- unconditionally. It was gated on there being a pawn this
        session had to put BACK to undrafted, so a session whose fighters were
        already drafted at `begin` closed the ledger over a downed raider and
        said nothing at all about it."""
        # Lucas was drafted before `begin`, so cleanup leaves him drafted and
        # there is nothing being undrafted into the danger.
        path = os.path.join(self.tmp.name, "already-drafted.json")
        combat.begin(snap(100, drafted=True), self.ident, path)
        combat.record_draft_intent("p1", "Lucas", path)
        combat.confirm_drafted("p1", 101, path)
        lines = combat.reconcile(
            hostile_snap(102, drafted=True, downed=True), self.ident,
            path=path, set_draft=lambda pid, val: {"drafted": val})
        text = chr(10).join(lines)
        self.assertIn("every remaining hostile is DOWNED", text)
        self.assertIn("raccoon", text)
        self.assertIn("Still drafted after this cleanup: Lucas", text)

    def test_the_dry_run_carries_the_downed_reminder_too(self):
        lines = combat.reconcile(
            hostile_snap(102, drafted=True, downed=True), self.ident,
            dry_run=True, path=self.path, set_draft=lambda *_: self.fail("mutated"))
        self.assertIn("every remaining hostile is DOWNED", chr(10).join(lines))

    def test_the_last_line_always_says_whether_the_ledger_closed(self):
        self.assertIn("STILL OPEN", combat.end_closure_line(True, self.path))
        self.assertIn("DRY RUN", combat.end_closure_line(True, self.path))
        self.assertIn("STILL OPEN", combat.end_closure_line(False, self.path))
        os.unlink(self.path)
        self.assertIn("CLOSED", combat.end_closure_line(False, self.path))

    def test_the_cli_refusal_says_still_open_and_leaves_the_clock_alone(self):
        with mock.patch.object(combat.rim, "init"), \
             mock.patch.object(combat, "LEDGER", self.path), \
             mock.patch.object(combat, "_identity", return_value=self.ident), \
             mock.patch.object(combat, "_live_snapshot",
                               side_effect=lambda *a, **k: hostile_snap(102, True)), \
             mock.patch.object(combat, "reconcile",
                               side_effect=combat.CombatRefusal("1 hostile(s) remain")), \
             mock.patch.object(combat, "pause_supervised_for_cleanup") as sup, \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = combat.main(["end"])
        text = out.getvalue()
        self.assertEqual(1, rc)
        self.assertIn("COMBAT REFUSED", text)
        self.assertIn("COMBAT SESSION STILL OPEN", text)
        self.assertNotIn("CLOCK LEFT PAUSED", text)
        sup.assert_not_called()
        self.assertEqual(1, text.count("COMBAT SESSION"))


if __name__ == "__main__":
    unittest.main()
