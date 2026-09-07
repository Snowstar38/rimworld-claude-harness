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
        self.assertIn("s.Injuries[p.thingIDNumber] = InjurySnapshot.Capture(p)", SOURCE)
        self.assertIn("after.Count > before.Count", SOURCE)
        self.assertIn("after.Severity > before.Severity + 0.01f", SOURCE)
        self.assertIn("after.BleedRate > before.BleedRate + 0.001f", SOURCE)
        self.assertIn("after.BloodLoss > before.BloodLoss + 0.001f", SOURCE)
        self.assertIn('new Hit("colonist_injury"', SOURCE)
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
                       SOURCE.index("// An alert never stops play")]
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
        self.assertIn("var suppressed = !threshold && (owedMs > 0 || acknowledged);", probe)
        self.assertIn('if (((s.Mode == "colony" && newWound) || threshold) && !suppressed)', probe)
        # blood-loss / severity creep on a known wound is an observation, never a stop
        self.assertIn('var newWound = after.Count > before.Count', probe)
        self.assertIn('known wound worsening', probe)
        self.assertIn('Add("injury_observed"', probe)
        # The same wound must not be re-reported every frame.
        self.assertIn("s.Injuries[p.thingIDNumber] = after;", probe)
        self.assertIn('payload["suppressedBy"] = suppressed ? (acknowledged ? "acknowledged" : "cooldown") : null;',
                      probe)
        self.assertIn('payload["cooldownRemainingMs"] = owedMs;', probe)

    def test_severity_downs_and_deaths_still_stop_a_suppressed_pawn(self):
        probe = SOURCE[SOURCE.index("private static Hit Probe(State s)"):
                       SOURCE.index("private static long InjuryCooldownRemainingMs(")]
        self.assertIn("var threshold = after.Health <= s.MinHealthFraction", probe)
        self.assertIn("|| before.Health - after.Health >= s.HealthDropFraction;", probe)
        # The downed/dead check is a separate hit and runs before the injury
        # delta, so no cooldown can swallow it.
        downed = probe.index('return PawnHit("colonist_downed", p,')
        injury = probe.index("var threshold = after.Health <= s.MinHealthFraction")
        self.assertLess(downed, injury)

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
        self.assertIn('{ "reason", owed > 0 ? "cooldown" : "acknowledged" }', start)
        self.assertIn('{ "secondsRemaining", (int)((owed + 999) / 1000) }', start)
        self.assertIn('{ "pawnName", HomePlayUntilEventTools.SafeName(p) }', start)
        self.assertIn('{ "suppressedInjuryPawns", s != null ? (object)s.SuppressedInjuries : null }',
                      SOURCE)

    def test_the_injury_stop_detail_is_one_actionable_line(self):
        detail = SOURCE[SOURCE.index("private static string InjuryDetail("):
                        SOURCE.index("private static string Num(float value)")]
        self.assertIn('" was injured (injuries " + before.Count + " -> " + after.Count', detail)
        self.assertIn('", bleed " + Num(before.BleedRate) + " -> " + Num(after.BleedRate)', detail)
        self.assertIn('", health " + Num(after.Health)', detail)
        self.assertIn('"). If this repeats, break the contact: "', detail)
        self.assertIn('"move them away or undraft them so they seek care; "', detail)
        self.assertIn('"a restart within " + seconds + " s will not stop again on minor injuries for "',
                      detail)
        self.assertIn('", or pass --allow-injured " + p.thingIDNumber', detail)
        # One line on screen: no newline may reach the detail string.
        self.assertNotIn(chr(92) + "n", detail)
        self.assertIn('return new Hit("colonist_injury", InjuryDetail(s, p, before, after), payload);',
                      SOURCE)
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
