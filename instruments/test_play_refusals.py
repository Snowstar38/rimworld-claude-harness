"""Refusals that name every blocker, not the first one the loop tripped over.

2026-09-07: "`play.py start` refuses on hostiles one at a time, so a two-raider
raid costs two refusals to learn both ThingIDs." The companion's `PawnHit` puts
the pawn's ID in the payload and only its NAME in the message, so the refusal
did not even carry the id the fix needs.
"""
import io
import sys
import unittest
from unittest import mock

import play
import trade


THREATS = {"threats": {
    "hostiles": [
        {"name": "Raider Kade", "thingId": "Thing_Human1001",
         "distanceToNearestColonist": 12, "downed": False},
        {"name": "Raider Vey", "thingId": "Thing_Human1002",
         "distanceToNearestColonist": 31, "downed": False},
    ],
    "huntingPredators": [
        {"name": "lynx", "thingId": "Thing_Lynx2003",
         "distanceToNearestColonist": 22, "downed": False},
    ]}}

# A lynx hunting one of ours, a bear standing 26 cells out and a wolverine
# eating a hare. Only the lynx is a threat.
PREDATORS = {"threats": {
    "hostiles": [],
    "huntingPredators": [
        {"name": "lynx", "defName": "Lynx", "thingId": "Thing_Lynx2003",
         "distanceToNearestColonist": 22, "downed": False},
    ],
    "huntersIgnored": [
        {"name": "bear", "defName": "Bear_Grizzly", "thingId": "Thing_Bear5",
         "distanceToNearestColonist": 26, "downed": False,
         "ignoredReason": "not hunting"},
        {"name": "wolverine", "defName": "Wolverine", "thingId": "Thing_Wolverine88",
         "distanceToNearestColonist": 41, "downed": False,
         "ignoredReason": "prey is not a colonist, a colony animal or a colony prisoner"},
    ]}}


class BlockingThreats(unittest.TestCase):
    def rows(self, reply=THREATS):
        return play.blocking_threats(caller=lambda: reply)

    def test_it_lists_hostiles_and_hunting_predators_together(self):
        self.assertEqual(["Thing_Human1001", "Thing_Human1002", "Thing_Lynx2003"],
                         [r["thingId"] for r in self.rows()])

    def test_the_paste_line_acknowledges_every_one_of_them(self):
        line = [l for l in play.blocking_threat_lines(self.rows())
                if l.strip().startswith("python play.py start")][0]
        self.assertIn("--ignore-hostile Thing_Human1001", line)
        self.assertIn("--ignore-hostile Thing_Human1002", line)
        # An animal is offered --ignore-predator: the companion takes either,
        # but only that one can never wave a raid through.
        self.assertIn("--ignore-predator Thing_Lynx2003", line)
        self.assertNotIn("--ignore-hostile Thing_Lynx2003", line)

    def test_a_start_refusal_is_enriched_with_the_whole_list(self):
        row = {"error": "supervised_play stopped during start: hostile: "
                        "Raider Kade (hostile to player faction)"}
        text = play.enrich_start_error(row, threats=self.rows())
        self.assertIn("Raider Kade", text)
        self.assertIn("3 thing(s) will block a start", text)
        self.assertIn("Thing_Lynx2003", text)

    def test_an_unrelated_start_error_is_left_exactly_as_it_was(self):
        row = {"error": "loaded-game generation unavailable"}
        self.assertEqual(row["error"], play.enrich_start_error(row, threats=[]))

    def test_only_a_predator_hunting_ours_blocks_and_the_rest_are_listed(self):
        """Distance is not a category. A predator blocks when its prey is ours
        and never otherwise; the others are listed so the operator can see they
        were considered."""
        rows = play.blocking_threats(caller=lambda: PREDATORS)
        by_id = {r["thingId"]: r for r in rows}
        self.assertEqual("predator_hunt", by_id["Thing_Lynx2003"]["category"])
        self.assertTrue(by_id["Thing_Lynx2003"]["blocks"])
        self.assertEqual("predator", by_id["Thing_Bear5"]["category"])
        self.assertFalse(by_id["Thing_Bear5"]["blocks"])
        self.assertEqual("predator", by_id["Thing_Wolverine88"]["category"])
        self.assertFalse(by_id["Thing_Wolverine88"]["blocks"])
        # Blockers sort first, so the eye lands on what has to be answered.
        self.assertTrue(rows[0]["blocks"])

    def test_a_manhunter_is_its_own_category_so_the_refusal_says_so(self):
        reply = {"threats": {"hostiles": [
            {"name": "muffalo", "thingId": "Thing_Muffalo3",
             "hostileReason": "manhunter:ManhunterPermanent",
             "distanceToNearestColonist": 4, "downed": False},
            {"name": "Raider Kade", "thingId": "Thing_Human1",
             "hostileReason": "faction:Pirates",
             "distanceToNearestColonist": 20, "downed": False}]}}
        rows = {r["thingId"]: r["category"]
                for r in play.blocking_threats(caller=lambda: reply)}
        self.assertEqual("manhunter", rows["Thing_Muffalo3"])
        self.assertEqual("hostile", rows["Thing_Human1"])

    def test_an_all_predator_map_offers_the_one_flag_that_covers_it(self):
        rows = play.blocking_threats(caller=lambda: PREDATORS)
        text = "\n".join(play.blocking_threat_lines(rows))
        self.assertIn("Every blocker is a wild predator", text)
        self.assertIn("python play.py start --ignore-predator", text)
        # The non-blockers are still on screen, marked as such, so nobody
        # reaches for --ignore-hostile all to make them go away.
        self.assertIn("does NOT stop the clock", text)
        self.assertIn("1 thing(s) will block a start", text)
        self.assertIn("2 other(s) were classified and will not", text)

    def test_a_raid_is_never_answered_with_the_blanket_predator_flag(self):
        """Two raiders and a lynx on our sheep. The lynx can be named; the
        sentence that covers EVERYTHING must not appear over a raid."""
        lines = play.blocking_threat_lines(self.rows())
        text = "\n".join(lines)
        self.assertNotIn("Every blocker is a wild predator", text)
        self.assertNotIn("start --ignore-predator" + chr(10), text + chr(10))
        self.assertIn("--ignore-hostile Thing_Human1001", text)
        self.assertIn("--ignore-predator lynx", text)

    def test_insectoids_asleep_across_the_map_do_not_block_a_start(self):
        """Threadneedle, live: five Sorne Geneline insectoids asleep in a cave
        74-78 cells from every colonist came back `hostile / blocks true`, so a
        bare `play.py start` refused every night until somebody typed
        `--ignore-hostile all` -- a real decision about a raid, made nightly
        about five animals that were asleep in a hole."""
        reply = {"threats": {"hostiles": [
            {"name": "Sorne Geneline insectoid", "thingId": "Thing_Insect%d" % i,
             "hostileReason": "faction:Sorne Geneline", "job": "Wait_AsleepDormancy",
             "distanceToNearestColonist": 74 + i, "downed": False}
            for i in range(5)]}}
        rows = play.blocking_threats(caller=lambda: reply)
        self.assertEqual(5, len(rows))
        self.assertEqual({"hostile_dormant"}, set(r["category"] for r in rows))
        self.assertFalse(any(r["blocks"] for r in rows))
        text = "\n".join(play.blocking_threat_lines(rows))
        self.assertIn("0 thing(s) will block a start", text)
        self.assertIn("5 other(s) were classified and will not", text)
        self.assertIn("dormant 74 cells away; wakes -> stops", text)
        self.assertIn("does NOT stop the clock", text)
        # Nothing to acknowledge, so no paste line claims otherwise.
        self.assertNotIn("--ignore-hostile Thing_Insect0", text)
        # And `all` must not pre-acknowledge them: when one wakes it has to stop.
        ids, line = play.acknowledged_hostiles(["all"], threats=rows)
        self.assertEqual([], ids)
        self.assertIn("nothing was acknowledged", line)

    def test_awake_or_close_is_a_blocker_again_and_a_manhunter_always_is(self):
        def one(**over):
            row = {"name": "insectoid", "thingId": "Thing_Insect1",
                   "hostileReason": "faction:Sorne Geneline",
                   "job": "Wait_AsleepDormancy",
                   "distanceToNearestColonist": 76, "downed": False}
            row.update(over)
            return play.blocking_threats(
                caller=lambda: {"threats": {"hostiles": [row]}})[0]
        self.assertEqual("hostile_dormant", one()["category"])
        # It woke up: LayDownAwake is not a dormant job, and neither is Goto.
        self.assertEqual("hostile", one(job="Goto")["category"])
        self.assertEqual("hostile", one(job="LayDownAwake")["category"])
        self.assertTrue(one(job="Goto")["blocks"])
        # It walked closer. 50 is the boundary and is NOT dormant.
        self.assertEqual("hostile", one(distanceToNearestColonist=50)["category"])
        self.assertEqual("hostile_dormant", one(distanceToNearestColonist=51)["category"])
        # A distance that could not be read is not evidence of safety.
        self.assertEqual("hostile", one(distanceToNearestColonist=None)["category"])
        # A manhunter is never dormant, whatever its job says.
        self.assertEqual("manhunter", one(hostileReason="manhunter:Manhunter")["category"])
        self.assertTrue(one(hostileReason="manhunter:Manhunter")["blocks"])

    def test_the_refusal_prefers_the_guards_own_classification(self):
        """`home/status` and the guard disagreed for a week. When the companion
        says which category it stopped on, that is what the refusal prints."""
        row = {"error": "supervised_play stopped during start: predator_hunt: "
                        "lynx [predator_hunting_ours] hunting Rosie",
               "stopThreats": [
                   {"thingId": "Thing_Lynx4", "pawnId": 4, "name": "lynx",
                    "category": "predator_hunting_ours",
                    "reason": "hunting Rosie, a colony animal (19 cells from Finn)",
                    "stops": True, "acknowledged": False,
                    "distanceToNearestColonist": 19},
                   {"thingId": "Thing_Wolverine77", "pawnId": 77,
                    "name": "wolverine", "category": "predator",
                    "reason": "wild predator hunting wildlife, 8 cells from "
                              "Finn; not a threat to the colony",
                    "stops": False, "acknowledged": False,
                    "distanceToNearestColonist": 8}]}
        with mock.patch.object(play, "blocking_threats") as reread:
            text = play.enrich_start_error(row)
        reread.assert_not_called()
        self.assertIn("the guard classified 2 thing(s)", text)
        self.assertIn("1 of them stop a start", text)
        self.assertIn("Thing_Wolverine77", text)
        self.assertIn("8 cells from Finn", text)  # the row that did not stop
        self.assertIn("does NOT stop the clock", text)
        self.assertIn("python play.py start --ignore-predator", text)

    def test_a_dead_binding_refusal_carries_the_same_repair_line_as_the_claim(self):
        """`PLAY REFUSED -- runtime binding unavailable` named the cause but
        not the cure. It now prints the one command that fixes it, and exits 1."""
        with mock.patch.object(play.play_service, "binding", return_value=None), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = play.main(["start"])
        text = out.getvalue()
        self.assertEqual(1, code)
        self.assertIn("PLAY REFUSED", text)
        self.assertIn("runtime binding unavailable", text)
        self.assertIn("bind-check --repair", text)


class TradePausesOnPurpose(unittest.TestCase):
    """`trade.py accept` used to leave the game paused and the service dead.

    The dialog force-pauses, the companion's watcher stops on any force pause,
    and nothing said a word. Found only by checking.
    """

    def accept(self, argv, reply, service_alive=True):
        svc = mock.Mock()
        svc.SERVICE_STATE = "state"
        svc.read_json.return_value = {"ready": True}
        p = mock.Mock()
        p.service_alive.return_value = service_alive
        p.pause.return_value = 0
        with mock.patch.dict(sys.modules, {"play": p, "play_service": svc}), \
             mock.patch.object(trade, "call", return_value=reply), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = trade.cmd_accept(argv)
        return code, out.getvalue(), p

    def test_it_pauses_before_the_dialog_and_prints_the_restart_line(self):
        reply = {"success": True, "actuallyTraded": True, "executed": True,
                 "traderName": "Sanguine caravan", "negotiator": "Finn",
                 "watch": {"shown": True, "dialogShown": True, "secondsShown": 8}}
        code, text, p = self.accept([], reply)
        self.assertEqual(0, code)
        p.pause.assert_called_once_with()
        self.assertIn("PAUSED on purpose before the trade dialog", text)
        self.assertIn("python play.py start", text)

    def test_no_watch_opens_no_dialog_and_pauses_nothing(self):
        reply = {"success": True, "actuallyTraded": True, "executed": True,
                 "traderName": "Sanguine caravan", "negotiator": "Finn",
                 "watch": {"shown": False, "reason": "--no-watch"}}
        code, text, p = self.accept(["--no-watch"], reply)
        self.assertEqual(0, code)
        p.pause.assert_not_called()
        self.assertNotIn("PAUSED on purpose", text)

    def test_a_service_that_was_not_running_is_said_so_not_silently_skipped(self):
        reply = {"success": True, "actuallyTraded": True, "executed": True,
                 "traderName": "T", "negotiator": "F",
                 "watch": {"shown": True, "dialogShown": True, "secondsShown": 8}}
        code, text, p = self.accept([], reply, service_alive=False)
        self.assertEqual(0, code)
        p.pause.assert_not_called()
        self.assertIn("was not running", text)
        self.assertIn("python play.py start", text)


if __name__ == "__main__":
    unittest.main()


class GuardPredatorCategory(unittest.TestCase):
    """The guard spells a hunting predator `predator_hunting_ours`; `home/status`
    spells the same animal `predator_hunt`. `--ignore-predator` covers both
    (SupervisedPlayTool `IsIgnoredPredator`), so the refusal built from the
    GUARD's own rows has to offer it -- and offered nothing at all."""

    STOP = {"error": "supervised_play stopped during start: predator_hunt: "
                     "lynx [predator_hunting_ours] hunting Rosie",
            "stopThreats": [
                {"thingId": "Thing_Lynx4", "pawnId": 4, "name": "lynx",
                 "category": "predator_hunting_ours",
                 "reason": "hunting Rosie, a colony animal",
                 "stops": True, "acknowledged": False,
                 "distanceToNearestColonist": 19}]}

    def test_the_guards_spelling_is_a_predator_category(self):
        self.assertIn("predator_hunting_ours", play.PREDATOR_CATEGORIES)
        self.assertIn("predator_hunt", play.PREDATOR_CATEGORIES)

    def test_a_hunting_predator_stop_offers_the_predator_flag(self):
        text = play.enrich_start_error(self.STOP, threats=[])
        self.assertIn("Every blocker is a wild predator", text)
        self.assertIn("python play.py start --ignore-predator", text)

    def test_the_per_thing_offer_names_the_predator_flag_and_the_thing_id(self):
        """A predator row is offered --ignore-predator with its ThingID, not
        --ignore-hostile, and the line parses back exactly as printed."""
        row = dict(self.STOP)
        row["stopThreats"] = self.STOP["stopThreats"] + [
            {"thingId": "Thing_Human1", "pawnId": 1, "name": "Raider Kade",
             "category": "hostile", "reason": "hostile to player faction",
             "stops": True, "distanceToNearestColonist": 12}]
        text = play.enrich_start_error(row, threats=[])
        offer = [l for l in text.splitlines()
                 if l.strip().startswith("python play.py start")][0]
        self.assertIn("--ignore-predator Thing_Lynx4", offer)
        self.assertNotIn("--ignore-hostile Thing_Lynx4", offer)
        self.assertIn("--ignore-hostile Thing_Human1", offer)
        args = play.parser().parse_args(offer.split()[2:])
        self.assertEqual(["Thing_Lynx4"], args.ignore_predator)
        self.assertEqual(["Thing_Human1"], args.ignore_hostile)
        # IDs need no `home/status` read.
        self.assertFalse(play.needs_map_read(args.ignore_hostile,
                                             args.ignore_predator))

    def test_a_raid_alongside_it_still_only_names_the_predator(self):
        row = dict(self.STOP)
        row["stopThreats"] = self.STOP["stopThreats"] + [
            {"thingId": "Thing_Human1", "pawnId": 1, "name": "Raider Kade",
             "category": "hostile", "reason": "hostile to player faction",
             "stops": True, "distanceToNearestColonist": 12}]
        text = play.enrich_start_error(row, threats=[])
        self.assertNotIn("Every blocker is a wild predator", text)
        self.assertIn("--ignore-predator lynx", text)
        self.assertIn("--ignore-hostile Thing_Human1", text)
