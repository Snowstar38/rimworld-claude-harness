"""Offline invariants for opt-in play_until_event combat probes."""
from pathlib import Path
import re
import unittest


SOURCE = (Path(__file__).parent.parent / "src" / "PlayUntilEventTool.cs").read_text(encoding="utf-8")


class CombatWatchSourceTests(unittest.TestCase):
    def test_combat_watches_default_off(self):
        for name in ("watchMeleeThreats", "watchPawnOrders", "watchInjuries", "watchInjuryHook"):
            self.assertRegex(SOURCE, rf"bool\s+{name}\s*=\s*false")
        self.assertRegex(SOURCE, r"bool\s+injuryStopOnNew\s*=\s*false")
        self.assertRegex(SOURCE, r"int\s+meleeThreatWithin\s*=\s*1")
        self.assertRegex(SOURCE, r'string\s+watchedPawnIds\s*=\s*""')
        self.assertRegex(SOURCE, r'string\s+meleeThreatPawnIds\s*=\s*""')
        self.assertRegex(SOURCE, r'string\s+injuryPawnIds\s*=\s*""')

    def test_default_probe_short_circuits_combat_reads(self):
        guard = re.search(
            r"var combatEnabled = \(watch\.MeleeThreats && watch\.MeleeThreatPawnIds\.Count > 0\)",
            SOURCE,
        )
        self.assertIsNotNone(guard)
        self.assertIn("|| combatEnabled", SOURCE[guard.end():guard.end() + 500])
        # The costly combat snapshot occurs only inside that explicit guard.
        self.assertIn("if (combatEnabled", SOURCE[guard.end():])
        self.assertIn("CaptureCombatPawn(pawn)", SOURCE[guard.end():])

    def test_melee_and_injury_scopes_are_independent(self):
        self.assertIn("watch.MeleeThreatPawnIds.Contains(target.thingIDNumber)", SOURCE)
        self.assertIn("PawnDistance(pawn, target) <= watch.MeleeThreatWithin", SOURCE)
        self.assertIn("PawnDistance(attacker, target) <= watch.MeleeThreatWithin", SOURCE)
        self.assertIn("watch.InjuryPawnIds.Contains(pawn.thingIDNumber)", SOURCE)
        self.assertIn('{ "meleeThreatPawnIds", watch.MeleeThreatPawnIds.OrderBy', SOURCE)
        self.assertIn('{ "injuryPawnIds", watch.InjuryPawnIds.OrderBy', SOURCE)
        self.assertIn('{ "meleeThreatWithin", watch.MeleeThreatWithin }', SOURCE)

    def test_combat_edges_are_entry_baselined(self):
        self.assertIn("b.WatchedPawns[pawn.thingIDNumber] = state", SOURCE)
        self.assertIn("baseline.WatchedPawns.TryGetValue", SOURCE)
        self.assertIn("SameOrder(before, after)", SOURCE)
        self.assertIn("MeaningfulInjuryChange(before, after, watch)", SOURCE)

    def test_new_injury_does_not_unconditionally_stop(self):
        body = re.search(
            r"private static bool MeaningfulInjuryChange\([^\)]*\)\s*\{(?P<body>.*?)\n\s*\}",
            SOURCE,
            re.DOTALL,
        )
        self.assertIsNotNone(body)
        text = body.group("body")
        self.assertIn("watch.InjuryStopOnNew && after.InjuryCount > before.InjuryCount", text)
        self.assertNotIn("return after.InjuryCount > before.InjuryCount", text)
        self.assertIn("after.TotalInjurySeverity - before.TotalInjurySeverity", text)
        self.assertIn("after.BleedRate - before.BleedRate", text)
        self.assertIn('{ "injuryStopOnNew", watch.InjuryStopOnNew }', SOURCE)

    def test_injury_hook_is_opt_in_and_event_driven(self):
        hook = (Path(__file__).parent.parent / "src" / "CombatInjuryHook.cs").read_text(encoding="utf-8")
        self.assertIn('typeof(Thing), "TakeDamage"', hook)
        self.assertIn('var armed = Volatile.Read(ref _armed); // normal play: this read + return', hook)
        self.assertIn('if (__state.Armed == null) return;', hook)
        self.assertIn('tm.Pause()', hook)
        self.assertIn('CombatInjuryHook.Disarm();', SOURCE)
        self.assertIn('new Hit("pawn_injury_hook"', SOURCE)

    def test_verbose_hook_diagnostics_are_debug_gated(self):
        self.assertRegex(SOURCE, r"bool\s+injuryHookDebug\s*=\s*false")
        self.assertIn("if (watch.InjuryHookDebug)", SOURCE)
        self.assertIn('["injuryHookStatus"] = CombatInjuryHook.Status()', SOURCE)
        self.assertIn('{ "injuryHookAvailable", !watch.InjuryHook || CombatInjuryHook.IsInstalled() }', SOURCE)

    def test_combat_camera_is_opt_in_server_side_and_yields_to_manual_input(self):
        self.assertRegex(SOURCE, r"bool\s+combatCamera\s*=\s*false")
        self.assertIn("UpdateCombatCamera(watch.Camera, clock.ElapsedMilliseconds)", SOURCE)
        self.assertIn("camera.ManualOverride = true", SOURCE)
        self.assertIn('{ "manualOverride", ManualOverride }', SOURCE)
        self.assertIn("Math.Max(rawWidth, rawHeight) < camera.CompactSpan", SOURCE)
        self.assertIn("13.3315439f", SOURCE)
        self.assertIn("25.5292435f", SOURCE)
        self.assertIn("UpdateCombatCamera(watch.Camera, 0, forceFrame: true)", SOURCE)
        self.assertIn("forceFrame: true), cancellationToken", SOURCE)
        self.assertIn("if (!forceFrame && elapsedMs - camera.LastFrameMs", SOURCE)
        self.assertIn("combatCameraCooldownMs = 3500", SOURCE)
        self.assertIn("now - camera.LastFrameUnixMs < camera.CooldownMs", SOURCE)
        self.assertIn('{ "lastFrameUnixMs", LastFrameUnixMs }', SOURCE)
        self.assertIn("combatCameraManualSuppressMs = 20000", SOURCE)
        self.assertIn("camera.ManualSuppressUntilUnixMs = now + camera.ManualSuppressMs", SOURCE)


if __name__ == "__main__":
    unittest.main()
