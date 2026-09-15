from pathlib import Path
import unittest


SOURCE = (Path(__file__).parent.parent / "src" / "SupervisedPlayTool.cs").read_text(encoding="utf-8")


class SupervisedPlaySourceTests(unittest.TestCase):
    def test_api_is_immediate_state_control(self):
        self.assertIn('"start, pause, speed, status, heartbeat, or events.', SOURCE)
        self.assertIn('if (action == "status")', SOURCE)
        self.assertIn('if (action == "events")', SOURCE)
        self.assertIn('if (action == "heartbeat")', SOURCE)
        self.assertIn('if (action == "speed")', SOURCE)
        self.assertNotIn("Task.Run", SOURCE)
        self.assertIn("BridgeCommon.WithUnknownArguments(", SOURCE)
        self.assertIn("ctx, typeof(HomeSupervisedPlayTools), ToolName", SOURCE)
        self.assertIn('[ToolResponse("unknownArguments"', SOURCE)
        self.assertIn('[ToolResponse("unknownArgumentsWarning"', SOURCE)

    def test_main_thread_hook_owns_persistent_monitoring(self):
        self.assertIn('typeof(TickManager), "TickManagerUpdate"', SOURCE)
        self.assertIn('postfix: new HarmonyMethod(typeof(Supervisor), nameof(OnUpdate))', SOURCE)
        self.assertIn('new Harmony("homebridge.supervised-play")', SOURCE)

    def test_lease_and_epoch_are_bounded_and_fenced(self):
        self.assertIn("Clamp(leaseMs, 1000, 30000)", SOURCE)
        self.assertIn("s.Epoch == epoch", SOURCE)
        self.assertIn('"lease_expired"', SOURCE)
        self.assertIn("ReferenceEquals(Current.Game, s.Session)", SOURCE)
        self.assertIn('{ "sessionChanged", s != null && s.StopReason == "session_changed" }', SOURCE)

    def test_external_control_wins_and_events_do_not_restart(self):
        pause = SOURCE.index('tm.CurTimeSpeed == TimeSpeed.Paused')
        lease = SOURCE.index('NowMs() >= s.LeaseExpiresMs')
        self.assertLess(pause, lease)
        self.assertIn('s.Active = false; s.StopReason = kind', SOURCE)
        # A guard STOP never restarts play. The one in-epoch write of the
        # requested speed is ResumeAfterForcePause, which puts it back after a
        # long event (an autosave) cleared and play was never stopped at all.
        self.assertEqual(SOURCE.count("CurTimeSpeed = s.RequestedSpeed"), 1)
        self.assertGreater(SOURCE.index("tm.CurTimeSpeed = s.RequestedSpeed"),
                           SOURCE.index("private static bool ResumeAfterForcePause"))

    def test_force_pause_detail_names_the_windows_or_says_there_are_none(self):
        self.assertIn("private static List<string> ForcePausingWindows()", SOURCE)
        self.assertIn("w != null && w.forcePause", SOURCE)
        self.assertIn('"Game is force-paused by " + string.Join(", ", names)', SOURCE)
        self.assertIn('"no force-pausing window found; clock was paused by "', SOURCE)
        self.assertIn('"TimeSpeed." + tm.CurTimeSpeed + ", ForcePaused=" + tm.ForcePaused', SOURCE)
        self.assertIn("WindowStack.WindowsForcePause", SOURCE)

    def test_start_refuses_only_a_dismissable_window_or_a_long_event(self):
        self.assertNotIn("Find.TickManager.ForcePaused || LongEventHandler.AnyEventNowOrWaiting",
                         SOURCE)
        guard = SOURCE.index("if (ForcePausingWindows().Count > 0)")
        long_event = SOURCE.index("if (LongEventHandler.AnyEventNowOrWaiting)")
        speed = SOURCE.index("Find.TickManager.CurTimeSpeed = speed;")
        self.assertLess(long_event, guard)
        self.assertLess(guard, speed)

    def test_ring_reports_retention_gap(self):
        self.assertIn("private const int Capacity = 128", SOURCE)
        self.assertIn('Ring.Count > Capacity', SOURCE)
        self.assertIn('{ "gap", gap }', SOURCE)
        self.assertIn('{ "lostCount", gap ?', SOURCE)

    def test_guard_covers_all_notification_and_threat_channels(self):
        for text in ("Letters()", "LiveMessages()", "ActiveAlertKeys(",
                     "IsHostile(p, out why)", "SafeDowned(p)",
                     "PredatorHunting(p)"):
            self.assertIn(text, SOURCE)

    def test_injuries_are_baselined_and_emit_actionable_deltas(self):
        self.assertIn("var snapshot = InjurySnapshot.Capture(p);", SOURCE)
        self.assertIn("s.Injuries[p.thingIDNumber] = snapshot;", SOURCE)
        self.assertIn("after.Count > before.Count", SOURCE)
        self.assertIn("after.Severity > before.Severity + 0.01f", SOURCE)
        self.assertIn("after.BleedRate > before.BleedRate + 0.001f", SOURCE)
        self.assertIn("after.BloodLoss > before.BloodLoss + 0.001f", SOURCE)
        self.assertIn('? "colonist_injury" : "colonist_health"', SOURCE)
        self.assertIn('{ "pawnId", p.thingIDNumber }', SOURCE)
        self.assertIn('{ "position", Position(p) }', SOURCE)

    def test_unsafe_start_pauses_and_session_change_does_not_pause_new_game(self):
        self.assertIn("Stop(s, hit.Kind, hit.Detail, true, hit.Payload)", SOURCE)
        self.assertIn('Stop(s, "session_changed", "Loaded game changed.", false, null)', SOURCE)

    def test_event_ring_contains_structured_payload(self):
        self.assertIn('{ "event", payload }', SOURCE)

    def test_combat_profile_records_small_wounds_and_stops_on_health(self):
        self.assertIn('profile != "colony" && profile != "combat"', SOURCE)
        self.assertIn('Add("injury_observed"', SOURCE)
        self.assertIn('after.Health <= s.MinHealthFraction', SOURCE)
        self.assertIn('before.Health - after.Health >= s.HealthDropFraction', SOURCE)
        self.assertIn('after.Health = before.Health', SOURCE)

    def test_speed_change_is_owned_and_atomic(self):
        self.assertIn("s.RequestedSpeed = speed;", SOURCE)
        self.assertIn("Find.TickManager.CurTimeSpeed = speed;", SOURCE)
        self.assertIn("s.RequestedSpeed = old;", SOURCE)

    def test_new_notifications_pause_once_without_wording_guesses(self):
        self.assertIn('if (!s.Letters.Add(id)) continue;', SOURCE)
        self.assertIn('newLetters.Add(new Dictionary<string, object>', SOURCE)
        self.assertIn('newMessages.Add(payload);', SOURCE)
        self.assertIn('return new Hit("notification_batch"', SOURCE)

    def test_negative_announcements_inform_without_stopping(self):
        # 2026-09-07: "Cotton plant has died because of cold" stopped the clock
        # 30 s into a stream test. Negative letters and messages are
        # notification_new rows flagged `negative`; threats and deaths still stop.
        self.assertIn('"NeutralEvent", "PositiveEvent", "NegativeEvent" }', SOURCE)
        self.assertIn('"NegativeEvent", "NegativeHealthEvent", "SituationResolved" }', SOURCE)
        self.assertIn('{ "negative", l.def.defName == "NegativeEvent" }', SOURCE)
        self.assertIn('payload["negative"] = type == "NegativeEvent" || type == "NegativeHealthEvent";', SOURCE)
        self.assertNotIn('"ThreatBig"', SOURCE.split('NonStoppingMessageTypes = ')[1].split(';')[0])
        self.assertIn('{ "letters", newLetters }', SOURCE)
        self.assertIn('{ "messages", newMessages }', SOURCE)
        self.assertNotIn("EmergencyText", SOURCE)
        # Input feedback and routine task completion remain non-stopping.
        for kind in ("RejectInput", "CautionInput", "SilentInput", "TaskCompletion"):
            self.assertIn('"%s"' % kind, SOURCE)

    def test_all_simultaneous_notifications_are_collected_before_stop(self):
        batch = SOURCE[SOURCE.index("var newLetters"):
                       SOURCE.index("var pawns =")]
        self.assertNotIn('return new Hit("letter"', batch)
        self.assertNotIn('return new Hit("message"', batch)
        self.assertIn("if (newLetters.Count > 0 || newMessages.Count > 0)", batch)

    def test_standing_alerts_are_baselined_at_start_and_reported(self):
        start = SOURCE[SOURCE.index("internal static object Start("):
                       SOURCE.index("internal static object Heartbeat(")]
        self.assertIn("if (s.AlertKeys.Add(a.Key)) s.BaselineAlerts.Add(AlertRow(a));", start)
        self.assertIn('{ "baselineAlerts", s != null ? (object)s.BaselineAlerts : null }', SOURCE)
        self.assertIn('{ "alertKey", a.Key }, { "label", a.Value }', SOURCE)

    def test_a_new_alert_is_a_non_stopping_event(self):
        self.assertIn('if (s.AlertKeys.Add(a.Key)) Add("alert_new", a.Value, s, AlertRow(a));', SOURCE)
        # No alert of any priority pauses the game any more.
        self.assertNotIn('new Hit("alert"', SOURCE)
        self.assertNotIn("IgnoredAlerts", SOURCE)
        # The key set only grows, so a flapping alert cannot re-report.
        self.assertIn("public readonly HashSet<string> AlertKeys", SOURCE)

    def test_modal_and_external_speed_changes_fail_closed(self):
        self.assertIn('Stop(s, "force_paused", ForcePauseDetail(), true, null)', SOURCE)
        self.assertIn('Stop(s, "external_speed_changed", "Speed changed outside the supervisor.", true,', SOURCE)
        self.assertIn('Add("speed_changed", "Supervisor owner changed speed', SOURCE)

    def test_deduped_notice_cannot_cancel_a_failed_pause_retry(self):
        self.assertIn("s.PendingKind = kind; s.PendingDetail = detail", SOURCE)
        self.assertIn("if (s.PendingKind != null)", SOURCE)
        self.assertIn("Stop(s, s.PendingKind, s.PendingDetail, true, s.PendingPayload)", SOURCE)

    def test_an_injury_stop_is_remembered_across_epochs(self):
        # Start() re-baselines injuries, so without a memory that outlives the
        # epoch the same arriving wound reads as new and stops play forever.
        self.assertIn("private static readonly Dictionary<int, InjuryStop> InjuryStops", SOURCE)
        self.assertIn("private static object _injuryStopsSession;", SOURCE)
        self.assertIn("RecordInjuryStop(p);", SOURCE)
        self.assertIn("InjuryStops[pawn.thingIDNumber] = new InjuryStop {", SOURCE)
        self.assertIn("Tick = Find.TickManager != null ? Find.TickManager.TicksGame : 0", SOURCE)
        # A different loaded game must not inherit another colony's pawn IDs.
        self.assertIn("if (!ReferenceEquals(_injuryStopsSession, Current.Game))", SOURCE)
        self.assertIn("InjuryStops.Clear(); _priorEpochConsciousHostiles = 0; _injuryStopsSession = Current.Game; }", SOURCE)

    def test_the_cooldown_is_a_clamped_start_argument(self):
        self.assertIn("int injuryStopCooldownMs = 180000", SOURCE)
        self.assertIn("InjuryStopCooldownMs = Clamp(injuryStopCooldownMs, 0, 1800000)", SOURCE)
        self.assertIn("if (cooldownMs <= 0 || !InjuryStops.TryGetValue(pawnId, out stop)) return 0;",
                      SOURCE)
        self.assertIn("var remaining = stop.AtMs + cooldownMs - NowMs();", SOURCE)
        self.assertIn('{ "injuryStopCooldownMs", s != null ? s.InjuryStopCooldownMs : 0 }', SOURCE)

    def test_a_repeat_injury_inside_the_cooldown_observes_instead_of_stopping(self):
        probe = SOURCE[SOURCE.index("private static Hit Probe(State s)"):
                       SOURCE.index("private static long InjuryCooldownRemainingMs(")]
        self.assertIn("var owedMs = InjuryCooldownRemainingMs(p.thingIDNumber, s.InjuryStopCooldownMs);",
                      probe)
        # The cooldown, the acknowledgement and a social fight suppress EVERY
        # injury and health threshold for that pawn. Nothing about the stop
        # condition can cancel a suppression -- that inversion is what let one
        # fistfight stop four times inside its own cooldown window.
        self.assertIn("var suppressed = acknowledged || owedMs > 0 || socialFight;", probe)
        self.assertIn("if (severe && !suppressed)", probe)
        self.assertNotIn("!threshold &&", probe)
        # blood-loss / severity creep on a known wound is an observation, never a stop
        self.assertIn("var newWound = after.Count > before.Count", probe)
        self.assertIn("known wound worsening", probe)
        self.assertIn('Add("injury_observed"', probe)
        # The same wound must not be re-reported every frame.
        self.assertIn("s.Injuries[p.thingIDNumber] = after;", probe)
        self.assertIn('{ "suppressedBy", !suppressed ? null', probe)
        self.assertIn('                                : acknowledged ? "acknowledged"', probe)
        self.assertIn('                                : owedMs > 0 ? "cooldown" : "social_fight" }', probe)
        self.assertIn('{ "cooldownRemainingMs", owedMs } };', probe)

    def test_a_scratch_a_punch_and_an_animal_nip_never_stop_the_clock(self):
        probe = SOURCE[SOURCE.index("private static Hit Probe(State s)"):
                       SOURCE.index("private static long InjuryCooldownRemainingMs(")]
        # Hediff_Injury.Severity is hit points; a scratch or a punch is 2-6.
        self.assertIn("private const float SeriousWoundSeverity = 10f;", SOURCE)
        self.assertIn("private const float SeriousWoundBleedRate = 0.5f;", SOURCE)
        self.assertIn("var woundSeverity = after.Severity - before.Severity;", probe)
        self.assertIn("var seriousWound = newWound", probe)
        self.assertIn("&& (woundSeverity >= SeriousWoundSeverity", probe)
        self.assertIn("|| woundBleed >= SeriousWoundBleedRate);", probe)
        # A bare new wound is no longer a colony stop on its own.
        self.assertNotIn('s.Mode == "colony" && newWound', probe)
        self.assertIn('|| (s.Mode == "colony" && seriousWound);', probe)

    def test_a_social_fight_never_stops_the_clock(self):
        probe = SOURCE[SOURCE.index("private static Hit Probe(State s)"):
                       SOURCE.index("private static long InjuryCooldownRemainingMs(")]
        self.assertIn("var socialFight = InSocialFight(p);", probe)
        self.assertIn("private static bool InSocialFight(Pawn p)", SOURCE)
        self.assertIn('p.MentalStateDef.defName == "SocialFighting"', SOURCE)
        self.assertIn('{ "socialFight", socialFight },', probe)
        self.assertIn("is in a social fight; play continues", probe)

    def test_a_threshold_stop_rebaselines_so_it_cannot_repeat(self):
        probe = SOURCE[SOURCE.index("private static Hit Probe(State s)"):
                       SOURCE.index("private static long InjuryCooldownRemainingMs(")]
        # Returning without writing the snapshot left the epoch baseline in
        # place, so the same drop stayed true and stopped on every later tick.
        stop = probe.index("if (severe && !suppressed)")
        self.assertIn("s.Injuries[p.thingIDNumber] = after;",
                      probe[stop:probe.index("Add(\"injury_observed\"")])
        self.assertLess(probe.index("RecordInjuryStop(p);"),
                        probe.index('return new Hit(condition == "serious_wound"'))
        # A SUPPRESSED threshold re-baselines too, or the observation row
        # fires ten times a second for as long as the pawn stays down there.
        self.assertIn("if (!severe) after.Health = before.Health;", probe)

    def test_downs_and_deaths_still_stop_a_suppressed_pawn(self):
        probe = SOURCE[SOURCE.index("private static Hit Probe(State s)"):
                       SOURCE.index("private static long InjuryCooldownRemainingMs(")]
        self.assertIn("var crossedFloor = before.Health > s.MinHealthFraction", probe)
        self.assertIn("&& after.Health <= s.MinHealthFraction;", probe)
        self.assertIn("var bigDrop = before.Health - after.Health >= s.HealthDropFraction;", probe)
        # The downed/dead check is a separate hit and runs before the injury
        # delta, so no cooldown, acknowledgement or social fight can swallow it.
        downed = probe.index('return PawnHit("colonist_downed", p,')
        injury = probe.index("var crossedFloor = before.Health > s.MinHealthFraction")
        self.assertLess(downed, injury)

    def test_minimum_health_is_an_edge_not_a_pause_loop(self):
        probe = SOURCE[SOURCE.index("private static Hit Probe(State s)"):
                       SOURCE.index("private static long InjuryCooldownRemainingMs(")]
        # A pawn who starts an epoch below the floor must not stop again on
        # every ordinary blood-loss or severity tick. Only crossing the floor
        # from above is a minimum-health event -- in BOTH modes. The old
        # combat-mode read was level-triggered and refused the start outright
        # for a colonist already under 0.5.
        crossing = ("var crossedFloor = before.Health > s.MinHealthFraction\n"
                    "                        && after.Health <= s.MinHealthFraction;")
        self.assertIn(crossing, probe)
        self.assertNotIn('s.Mode == "combat"', probe)
        self.assertNotIn("(after.Health <= s.MinHealthFraction", probe)

    def test_a_pawn_below_the_floor_at_start_is_acknowledged_there(self):
        start = SOURCE[SOURCE.index("internal static object Start("):
                       SOURCE.index("internal static object Heartbeat(")]
        self.assertIn("var below = snapshot.Health <= s.MinHealthFraction;", start)
        self.assertIn("if (below) s.IgnoredInjured.Add(p.thingIDNumber);", start)
        self.assertIn('{ "reason", below ? "below_health_floor"', start)
        self.assertIn('{ "healthFraction", snapshot.Health },', start)

    def test_acknowledging_an_injured_colonist_is_explicit_and_id_based(self):
        self.assertIn("string ignoredInjuredColonistIds", SOURCE)
        self.assertIn("IgnoredInjured = PawnIds(ignoredInjured)", SOURCE)
        self.assertIn("var acknowledged = s.IgnoredInjured.Contains(p.thingIDNumber);", SOURCE)
        self.assertIn("public HashSet<int> IgnoredInjured;", SOURCE)

    def test_start_reports_which_pawns_have_injury_stops_suppressed(self):
        start = SOURCE[SOURCE.index("internal static object Start("):
                       SOURCE.index("internal static object Heartbeat(")]
        self.assertIn("var owed = InjuryCooldownRemainingMs(p.thingIDNumber, s.InjuryStopCooldownMs);",
                      start)
        self.assertIn('                            : owed > 0 ? "cooldown" : "acknowledged" }', start)
        self.assertIn('{ "secondsRemaining", (int)((owed + 999) / 1000) }', start)
        self.assertIn('{ "pawnName", HomePlayUntilEventTools.SafeName(p) }', start)
        self.assertIn('{ "suppressedInjuryPawns", s != null ? (object)s.SuppressedInjuries : null }',
                      SOURCE)

    def test_the_injury_stop_detail_names_the_condition_that_fired(self):
        detail = SOURCE[SOURCE.index("private static string InjuryDetail("):
                        SOURCE.index("/// Two colonists brawling.")]
        # Which of the three conditions fired, in words, before the numbers.
        self.assertIn('var why = condition == "health_floor"', detail)
        self.assertIn('"health fell to " + Num(after.Health) + ", at or under the "', detail)
        self.assertIn(': condition == "health_drop"', detail)
        self.assertIn('"health fell " + Num(before.Health - after.Health) + " from "', detail)
        self.assertIn('"took a new wound worth " + Num(after.Severity - before.Severity)', detail)
        self.assertIn('" stopped play: " + why + " (injuries " + before.Count + " -> " + after.Count',
                      detail)
        self.assertIn('", bleed " + Num(before.BleedRate) + " -> " + Num(after.BleedRate)', detail)
        self.assertIn('", health " + Num(after.Health)', detail)
        self.assertIn('"). If this repeats, break the contact: "', detail)
        self.assertIn('"move them away or undraft them so they seek care; "', detail)
        self.assertIn('"a restart within " + seconds + " s will not stop again on any injury to "',
                      detail)
        self.assertIn('", or pass --allow-injured " + p.thingIDNumber', detail)
        # One line on screen: no newline may reach the detail string.
        self.assertNotIn(chr(92) + "n", detail)
        # The kind matches the condition, so a health slide is not filed as a wound.
        self.assertIn('return new Hit(condition == "serious_wound" ? "colonist_injury" : "colonist_health",',
                      SOURCE)
        self.assertIn("InjuryDetail(s, p, before, after, condition), payload);", SOURCE)
        # Culture-invariant, so a locale cannot put a comma inside a number.
        self.assertIn('value.ToString("0.00", CultureInfo.InvariantCulture)', SOURCE)

    def test_hostiles_cleared_reminds_once_and_survives_a_restart(self):
        body = SOURCE[SOURCE.index("private static void CheckHostilesCleared("):
                      SOURCE.index("private static bool SafeDrafted(")]
        # Ignored hostiles still count; dead ones do not; downed ones are listed.
        self.assertNotIn("IgnoredHostiles", body)
        self.assertIn("if (HomePlayUntilEventTools.SafeDead(p)) continue;", body)
        self.assertIn("p.RaceProps.Animal && colonistsNear.Any(c => Distance(p, c) <= 30)", SOURCE)
        self.assertIn("if (hostile) conscious++;",
                      body)
        # Fires only on the >0 -> 0 edge, once, and remembers across epochs.
        self.assertIn("var before = s.ConsciousHostiles ?? _priorEpochConsciousHostiles;", body)
        self.assertIn("if (conscious > 0) { s.HostilesCleared = false; return; }", body)
        self.assertIn("if (before <= 0 || s.HostilesCleared) return;", body)
        self.assertIn("if (first && downed.Count == 0 && drafted.Count == 0) return;", body)
        self.assertIn("_priorEpochConsciousHostiles = conscious;", body)
        # One line on screen, naming both jobs, with the ids Hands needs.
        self.assertIn('Add("hostiles_cleared", "No conscious hostiles remain: "', body)
        self.assertIn('") -- finish off or capture"', body)
        self.assertIn('" still drafted ("', body)
        self.assertIn('") -- undraft them"', body)
        self.assertNotIn(chr(92) + "n", body)
        self.assertIn('{ "downedHostiles", downed.Select(', body)
        self.assertIn('{ "draftedColonists", drafted.Select(', body)
        # Counted before the per-pawn loop, so a stop cannot swallow the row,
        # and the cross-epoch memory is cleared with the save.
        self.assertIn("CheckHostilesCleared(s, pawns);" + chr(10)
                      + " " * 12 + "foreach (var p in pawns)", SOURCE)
        self.assertIn("InjuryStops.Clear(); _priorEpochConsciousHostiles = 0;", SOURCE)

    def test_a_wild_predator_is_classified_before_it_can_stop_the_clock(self):
        """Threadneedle, 2026-09-08: a single wolverine stopped the clock on
        four separate nights. The whole predator test was the job name plus a
        40-cell radius -- `PredatorHunting(p) && p.Faction != Faction.OfPlayer
        && colonists.Any(c => Distance(p, c) <= 40)` -- so an animal eating a
        hare inside the home area was a stop, while `home/status` had excluded
        exactly that case (`huntersIgnored[]`) since 2026-09-01."""
        self.assertNotIn("PredatorHunt within 40 cells", SOURCE)
        self.assertNotIn("colonists.Any(c => Distance(p, c) <= 40)", SOURCE)
        body = SOURCE[SOURCE.index("private static string ClassifyThreat("):
                      SOURCE.index("private static string StopKind(")]
        # Manhunter and aggro are tested FIRST, before the player-faction
        # short-circuit: our own tame animal going berserk is still a threat.
        aggro = body.index("if (hostile || aggro)")
        ours = body.index("if (IsPlayerFactionPawn(p)) return null;")
        self.assertLess(aggro, ours)
        self.assertIn("var aggro = SafeAggro(p);", body)
        self.assertIn('return manhunter ? "manhunter" : "hostile";', body)
        # Hunting one of ours is a stop at any distance; a hunt of wildlife is
        # not a threat at any distance.
        self.assertIn("TargetBelongsToPlayer(p, out prey)", body)
        self.assertIn('return "predator_hunting_ours";', body)
        # A predator that is merely near a colonist is not a category at all.
        self.assertNotIn("predator_near", body)
        self.assertNotIn("PredatorNearCells", SOURCE)
        # The residual: classified, reported, and NOT a stop.
        self.assertIn('return "predator";', body)

    def test_a_hostile_asleep_across_the_map_is_listed_and_does_not_stop(self):
        """Threadneedle, live: five insectoids asleep in a cave 74-78 cells from
        every colonist refused a bare `start` every night. Asleep or dormant AND
        past every weapon's range is "still on the map, not a threat" -- the
        same rule combat.py's `end` uses, with the same JobDefs and the same 50
        cells."""
        self.assertIn("private const int DormantHostileCells = 50;", SOURCE)
        for job in ("LayDown", "LayDownResting", "Wait_Asleep", "RevenantSleep",
                    "Wait_AsleepDormancy", "ActivityDormant"):
            self.assertIn('{ "%s", ' % job, SOURCE)
        # Awake is awake: named in the comment, never a key.
        self.assertNotIn('{ "LayDownAwake"', SOURCE)
        body = SOURCE[SOURCE.index("private static string ClassifyThreat("):
                      SOURCE.index("private static string StopKind(")]
        # Both halves are required, and a manhunter is never dormant.
        self.assertIn("if (!manhunter && DormantJobs.TryGetValue(SafeJobDef(p) ?? \"\", out sleeping)",
                      body)
        self.assertIn("&& away.HasValue && away.Value > DormantHostileCells)", body)
        self.assertIn('reason = sleeping + " " + away.Value + " cells away; wakes -> stops";',
                      body)
        self.assertIn("stops = false;" + chr(10) + " " * 20 + 'return "hostile_dormant";',
                      body)
        # It is inside the hostile branch, so waking up or walking closer falls
        # straight through to the ordinary `hostile` stop on the next probe.
        self.assertLess(body.index('return "hostile_dormant";'),
                        body.index('return manhunter ? "manhunter" : "hostile";'))
        self.assertIn("private static string SafeJobDef(Pawn p)", SOURCE)

    def test_the_stop_carries_the_classified_threats(self):
        self.assertNotIn("predatorRadius", SOURCE)
        self.assertIn('{ "stopThreats", s != null ? s.StopThreats : null }', SOURCE)
        self.assertIn('if (payload != null && payload.ContainsKey("threats")) '
                      "s.StopThreats = payload[" + chr(34) + "threats" + chr(34) + "];",
                      SOURCE)

    def test_every_stop_carries_a_category_and_the_whole_classified_list(self):
        self.assertIn("return ThreatHit(s, p, category, reason, pawns, colonists);", SOURCE)
        hit = SOURCE[SOURCE.index("private static Hit ThreatHit("):
                     SOURCE.index("/// \"8 cells from Finn\"")]
        for field in ('{ "category", category }', '{ "reason", reason }',
                      '{ "threats", ThreatRows(s, pawns, colonists) }',
                      '{ "thingId", BridgeCommon.SafeString(() => p.GetUniqueLoadID()) }'):
            self.assertIn(field, hit)
        rows = SOURCE[SOURCE.index("private static List<object> ThreatRows("):
                      SOURCE.index("private static Hit ThreatHit(")]
        # Non-stopping predators are in the list too: "why did it stop for that
        # one and not this one" is the operator's actual question.
        self.assertIn("if (category == null) continue;", rows)
        self.assertIn('{ "stops", stops && !acked }', rows)
        self.assertIn('{ "acknowledged", acked }', rows)
        self.assertIn("if (rows.Count >= 40) break;", rows)

    def test_ignore_predator_can_never_wave_through_a_raid(self):
        body = SOURCE[SOURCE.index("private static bool Acknowledged("):
                      SOURCE.index("/// Every classified non-colonist")]
        # --ignore-hostile still covers everything, as PLAYBOOK promises.
        self.assertIn("if (s.IgnoredHostiles.Contains(p.thingIDNumber)) return true;", body)
        # --ignore-predator covers a hunt on one of ours and nothing else.
        self.assertIn('if (category != "predator_hunting_ours") return false;', body)
        self.assertIn("return s.IgnorePredatorsAll || s.IgnoredPredators.Contains", body)
        self.assertIn('IgnorePredatorsAll = Csv(ignoredPredators).Contains("all")', SOURCE)
        self.assertIn("IgnoredPredators = PawnIds(ignoredPredators)", SOURCE)
        self.assertIn('string ignoredPredatorIds = ""', SOURCE)

    def test_the_guard_never_asks_who_the_player_is_the_pausing_way(self):
        """`Faction.OfPlayer` is `get_OfPlayerSilentFail` followed by
        `Log.Error`, whose call path contains `TickManager.Pause()`. A guard
        whose job is noticing pauses must not be able to cause one."""
        import re
        code = "\n".join(
            line for line in SOURCE[SOURCE.index("internal static class Supervisor"):].splitlines()
            if not line.strip().startswith("//"))
        self.assertIsNone(re.search(r"Faction\.OfPlayer(?!SilentFail)", code))
        self.assertIn("return Faction.OfPlayerSilentFail;", code)

    def test_short_and_persistent_guards_refuse_to_compete(self):
        play = (Path(__file__).parent.parent / "src" / "PlayUntilEventTool.cs").read_text(encoding="utf-8")
        self.assertIn("HomePlayUntilEventTools.ShortGuardRunning", SOURCE)
        self.assertIn("if (Supervisor.IsActive)", play)


if __name__ == "__main__":
    unittest.main()


class WindowlessForcePauseTests(unittest.TestCase):
    """2026-09-07: a dozen-plus FORCE_PAUSED stops in one stream, each costing a
    manual `play.py start`, every one with no dialog behind it.

    Decompiled 1.6: `TickManager.ForcePaused` ORs `WindowStack.WindowsForcePause`
    with `LongEventHandler.ForcePause`, and `LongEventHandler.ForcePause` is
    simply `AnyEventNowOrWaiting`. `Autosaver.AutosaverTick` queues `DoAutosave`
    through `QueueLongEvent`, and `Root_Play.Update` still runs `UpdatePlay` --
    and so this patch -- while that event is only QUEUED, because
    `ShouldWaitForEvent` is false for a standard-window event. With
    autosaveIntervalDays=1 that fires every in-game day."""

    def test_a_windowless_force_pause_is_waited_out_not_stopped(self):
        self.assertIn("private static void HandleForcePause(State s)", SOURCE)
        self.assertIn("if (tm.ForcePaused) { HandleForcePause(s); return; }", SOURCE)
        # A window a human can close still stops immediately and is named.
        self.assertIn("if (windows.Count > 0) { Stop(s, \"force_paused\", ForcePauseDetail(), true, null); return; }",
                      SOURCE)
        # Windowless: one non-stopping observation row, then a bounded wait.
        self.assertIn('? "long_event" : "transient_force_pause"', SOURCE)
        self.assertIn("private const int ForcePauseGraceMs = 20000;", SOURCE)
        self.assertIn("if (now - s.ForcePauseSinceMs < ForcePauseGraceMs) return;", SOURCE)

    def test_the_requested_speed_comes_back_when_it_clears(self):
        self.assertIn("private static bool ResumeAfterForcePause(State s)", SOURCE)
        self.assertIn('Add("force_pause_cleared"', SOURCE)
        self.assertIn("if (s.ForcePauseSinceMs != 0 && !ResumeAfterForcePause(s)) return;", SOURCE)
        # Checked BEFORE the paused/speed tests: a long event can hold the clock
        # without ever touching CurTimeSpeed, and it is the specific diagnosis.
        self.assertLess(SOURCE.index("if (tm.ForcePaused) { HandleForcePause(s); return; }"),
                        SOURCE.index("tm.CurTimeSpeed == TimeSpeed.Paused"))

    def test_start_marks_a_long_event_refusal_retryable(self):
        self.assertIn('Retryable("A long event (autosave, map generation)', SOURCE)
        self.assertIn('{ "retryable", true }, { "refusal", refusal }', SOURCE)
        self.assertIn('{ "retryable", false }, { "refusal", null }', SOURCE)
        # A real window is still an honest, non-retryable refusal.
        start = SOURCE.index("internal static object Start(")
        window_refusal = SOURCE.index("if (ForcePausingWindows().Count > 0)", start)
        self.assertIn("return Failure(ForcePauseDetail());",
                      SOURCE[window_refusal:window_refusal + 200])

    def test_external_pause_does_not_claim_a_person_pressed_space(self):
        self.assertIn("private static string ExternalPauseDetail()", SOURCE)
        self.assertIn("This is NOT evidence of ", SOURCE)
        self.assertIn("human input -- nothing here can see a keypress", SOURCE)
        self.assertIn("Restart play unless a person says otherwise.", SOURCE)
        # When vanilla's own auto-pause explains it, say so instead of guessing.
        self.assertIn("private static List<string> RecentAutoPauseLetters()", SOURCE)
        self.assertIn("(int)Prefs.AutomaticPauseMode < (int)l.def.pauseMode", SOURCE)
        self.assertIn('"Game was paused by VANILLA, not by a person: "', SOURCE)
