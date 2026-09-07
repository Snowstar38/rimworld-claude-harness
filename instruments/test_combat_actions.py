"""Mock-only tests; no running game is contacted.

The melee-attack tests that lived here went with `issue_melee_attack` on
2026-09-04 -- attacks are `home/order` now, and their tests are in
`test_combat.py` beside the ledger bookkeeping they have to survive."""
import unittest
from unittest import mock

import combat_actions as actions


def pawn(pid, name, job="Wait", **kw):
    row = {"thingId": pid, "name": name, "job": job, "dead": False,
           "downed": False, "hostile": False,
           "equipment": {"armed": False, "primary": None,
                         "primaryLabel": "unarmed"}}
    row.update(kw)
    return row


class CombatActionTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(actions.letters, "dialog_window", return_value=None)
        p.start(); self.addCleanup(p.stop)

    def test_equip_is_accepted_but_not_called_complete_for_equip_job(self):
        unarmed = pawn("P1", "Sammy")
        equipping = pawn("P1", "Sammy", job="Equip")
        status_reads = iter((unarmed, equipping))
        gear_reads = iter((unarmed, equipping))
        def game(tool, args, strict=True):
            if tool == "home/status": return {"colonists": [next(status_reads)]}
            if tool == "home/list_pawns": return {"pawns": [next(gear_reads)]}
            if tool == "rimworld/right_click_cell":
                return {"actionKind": "menu_opened", "options": [
                    {"index": 2, "label": "Equip bolt-action rifle", "disabled": False}]}
            return {"success": True}
        with mock.patch.object(actions.rim, "game", side_effect=game):
            result = actions.issue_equip("P1", 4, 5, weapon_label="bolt-action rifle")
        self.assertTrue(result["accepted"]); self.assertFalse(result["completed"])

    def test_wait_requires_weapon_in_hands(self):
        armed = pawn("P1", "Sammy", equipment={"armed": True,
            "primary": {"thingId": "Gun1", "label": "bolt-action rifle"},
            "primaryLabel": "bolt-action rifle"})
        ticket = {"pawnId": "P1", "weaponThingId": "Gun1",
                  "weaponLabel": "bolt-action rifle", "completed": False}
        def waiter(predicate, seconds):
            self.assertTrue(predicate()); return "done", {"ticks": 900}
        with mock.patch.object(actions, "_pawn", return_value=armed):
            result = actions.wait_for_equipment(ticket, waiter, seconds=20)
        self.assertTrue(result["completed"])

    def test_still_walking_to_the_weapon_is_not_a_failure(self):
        """2026-09-04: this used to raise, `combat.py equip` printed EQUIP
        FAILED, and the identical retry then worked -- twice, on two pawns."""
        walking = pawn("P1", "Sammy", job="Equip")
        ticket = {"pawnId": "P1", "weaponLabel": "bolt-action rifle",
                  "completed": False}
        with mock.patch.object(actions, "_pawn", return_value=walking):
            result = actions.wait_for_equipment(
                ticket, lambda predicate, seconds: (predicate() or
                                                    ("budget_elapsed", None)),
                seconds=15)
        self.assertFalse(result["completed"])
        self.assertTrue(result["stillWalking"])
        self.assertEqual("Equip", result["job"])

    def test_dropping_the_equip_job_without_the_weapon_still_raises(self):
        idle = pawn("P1", "Sammy", job="Wait_Combat")
        ticket = {"pawnId": "P1", "weaponLabel": "bolt-action rifle",
                  "completed": False}
        with mock.patch.object(actions, "_pawn", return_value=idle):
            with self.assertRaisesRegex(RuntimeError, "no longer on an Equip job"):
                actions.wait_for_equipment(
                    ticket, lambda predicate, seconds: (predicate() or
                                                        ("budget_elapsed", None)),
                    seconds=15)


if __name__ == "__main__":
    unittest.main()
