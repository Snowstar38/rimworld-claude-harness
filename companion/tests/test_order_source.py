"""Source guards for target-address regressions that require RimWorld types."""
from pathlib import Path
import unittest


SRC = (Path(__file__).parents[1] / "src" / "OrderTool.cs").read_text(encoding="utf-8")
THINGS = (Path(__file__).parents[1] / "src" / "ListThingsTool.cs").read_text(encoding="utf-8")


class OrderResolutionSourceTests(unittest.TestCase):
    def test_bare_cell_prefers_exactly_one_pawn(self):
        self.assertIn('matchedBy = "cellPawn"', SRC)
        self.assertIn("pawnsAtCell.Count == 1", SRC)

    def test_live_name_beats_hidden_pawns_from_corpses(self):
        self.assertIn('matchedBy = "liveName"', SRC)
        self.assertIn("liveSpawned.Count == 1", SRC)

    def test_defname_at_cell_is_an_emitted_id_form(self):
        self.assertIn('forms.Add(defName + "@"', SRC)

    def test_inventory_positions_expose_stable_ids(self):
        self.assertIn('{ "thingId", loadId }', THINGS)
        self.assertIn('{ "idForms", ids }', THINGS)

    def test_undesignated_chunks_get_an_actionable_refusal(self):
        self.assertIn('plan.Refuse("missing_haul_designation"', SRC)
        self.assertIn("act.py apply \\\"Haul things\\\"", SRC)
        self.assertIn('"globalHaulCandidateCount"', SRC)
        self.assertIn('"targetInGlobalHaulList"', SRC)

    def test_raw_ground_tend_requires_explicit_cleanup_owner(self):
        self.assertIn("allowPersistentDraft", SRC)
        self.assertIn('plan.Refuse("draft_cleanup_required"', SRC)

    def test_an_id_that_does_not_resolve_gets_a_census_of_its_own_defname(self):
        """WEIRD 2: `Alpaca470153` -> target_not_found while pawns.py printed
        that exact id. Both readers see only the SPAWNED map, so the refusal
        has to say how many of that defName ARE here."""
        self.assertIn("MissingThingHint(map, request.TargetArg, false)", SRC)
        self.assertIn("MissingThingHint(map, request.PawnArg, true)", SRC)
        self.assertIn("has LEFT the map", SRC)

    def test_a_resolved_but_unspawned_pawn_is_not_reported_as_not_found(self):
        self.assertIn("but is NOT SPAWNED on this map", SRC)
        self.assertIn("UnspawnedPawn(map, request.PawnArg)", SRC)
        self.assertIn("UnspawnedPawn(map, request.TargetArg)", SRC)
        # Explicit id forms only: a name must never reach the off-map list, or
        # a unique substring on the spawned map could start tying.
        self.assertIn("map.mapPawns.AllPawns.ToList()", SRC)

    def test_is_player_faction_is_a_bool_because_faction_name_is_a_colony_name(self):
        self.assertIn('{ "isPlayerFaction"', SRC)
        self.assertIn("Faction.OfPlayerSilentFail", SRC)
        self.assertNotIn("Faction.OfPlayer;", SRC)


class RestAndUndraftedMoveSourceTests(unittest.TestCase):
    """WEIRD 4 and 64: there was no way to put a pawn in a bed, and `goto`
    drafted and never undrafted."""

    def test_rest_is_a_declared_action(self):
        self.assertIn('"work", "rest"', SRC)
        self.assertIn('case "rest": PrepareRest(plan); break;', SRC)

    def test_rest_issues_the_laydown_job_jobgiver_getrest_builds(self):
        self.assertIn("JobDefOf.LayDown", SRC)
        self.assertIn("RestUtility.FindBedFor(plan.Pawn)", SRC)

    def test_rest_undrafts_because_a_drafted_pawn_will_not_lie_down(self):
        self.assertIn("EnsureDraft(plan, false)", SRC)

    def test_rest_finds_the_bed_standing_on_a_given_cell(self):
        self.assertIn("OfType<Building_Bed>()", SRC)
        self.assertIn("Covers(b, plan.Cell)", SRC)

    def test_goto_with_draft_false_moves_undrafted_instead_of_refusing(self):
        self.assertIn("plan.UndraftedGoto = true;", SRC)
        self.assertIn("This move is UNDRAFTED", SRC)



class DeployWornPackSourceTests(unittest.TestCase):
    """The turret pack that was refused silently on 2026-09-08 (Threadneedle).

    Each of these pins one decompiled fact. If a RimWorld update moves the
    rule, the test that breaks names which half moved.
    """

    def test_deploy_is_a_declared_action(self):
        self.assertIn('"work", "rest",', SRC)
        self.assertIn('"deploy"', SRC)
        self.assertIn('case "deploy": PrepareDeploy(plan); break;', SRC)

    def test_the_cell_rule_is_validate_targets_own_two_lines(self):
        """Verb_LaunchProjectileStaticOneUse.ValidateTarget is
        `cell.GetFirstBuilding(map) == null` then `cell.Standable(map)`."""
        self.assertIn("cell.GetFirstBuilding(plan.Map) != null", SRC)
        self.assertIn("plan.Cell.GetFirstBuilding(plan.Map)", SRC)
        self.assertIn("plan.Cell.Standable(plan.Map)", SRC)
        self.assertIn('plan.Refuse("deploy_cell_blocked"', SRC)

    def test_validate_target_is_never_called_because_it_reads_event_current(self):
        """Verb_LaunchProjectileStaticOneUse.ValidateTarget chains to
        ReloadableUtility.CanUseConsideringQueuedJobs, which reads
        Event.current.shift -- and Event.current is null off the GUI thread, so
        calling it from a main-thread hop would throw. Its checks are run one at
        a time instead. (assertFalse, not assertNotIn: a failure here must not
        print 100 KB of source.)"""
        self.assertFalse("verb.ValidateTarget(" in SRC,
                         "deploy must not call Verb.ValidateTarget: it reads "
                         "Event.current, which is null outside OnGUI.")
        self.assertIn("Event.current", SRC)

    def test_line_of_sight_is_the_gate_that_closes_the_targeter(self):
        self.assertIn("verb.CanHitTarget(plan.Cell)", SRC)
        self.assertIn("GenSight.LineOfSight(plan.Pawn.Position, plan.Cell, plan.Map, true)", SRC)
        self.assertIn('plan.Refuse("deploy_no_line_of_sight"', SRC)
        self.assertIn('plan.Refuse("deploy_out_of_range"', SRC)

    def test_the_job_is_the_one_order_force_target_builds(self):
        self.assertIn('"UseVerbOnThingStaticReserve"', SRC)
        self.assertIn('"UseVerbOnThingStatic"', SRC)
        self.assertIn("job.verbToUse = plan.Verb;", SRC)
        self.assertIn("plan.SetVerbToUse = true;", SRC)

    def test_the_reserved_cell_is_pre_checked_because_the_driver_reserves_it(self):
        """JobDriver_CastVerbOnceStaticReserve.TryMakePreToilReservations
        reserves TargetIndex.A, so a claimed cell takes the job and drops it."""
        self.assertIn("plan.Pawn.CanReserve(plan.Cell)", SRC)
        self.assertIn('plan.Refuse("deploy_cell_reserved"', SRC)

    def test_a_worn_pack_is_resolved_off_the_pawn_not_off_the_map(self):
        self.assertIn("ResolveWornPack(plan, request.TargetArg)", SRC)
        self.assertIn("GetComp<CompApparelVerbOwner>()", SRC)
        self.assertIn('plan.TargetMatchedBy = "wornPack"', SRC)
        self.assertIn('plan.Refuse("no_deploy_pack"', SRC)

    def test_the_rule_is_stated_in_plain_words_on_every_deploy_payload(self):
        self.assertIn("THROWN GRENADE, not a build order", SRC)
        self.assertIn('{ "rule", DeployRule }', SRC)
        self.assertIn('{ "validCellsNearby"', SRC)

    def test_faction_of_player_is_pre_checked_before_verb_available(self):
        """Verb_LaunchProjectile.Available() opens with
        `casterPawn.Faction != Faction.OfPlayer`, whose null arm is a Log.Error
        and therefore a TickManager.Pause()."""
        self.assertIn("playerFaction != null && !BridgeCommon.Try(() => verb.Available()", SRC)
        self.assertIn("Faction.OfPlayerSilentFail", SRC)
        self.assertFalse("Faction.OfPlayer;" in SRC,
                         "OrderTool.cs must never read Faction.OfPlayer directly.")



if __name__ == "__main__":
    unittest.main()
