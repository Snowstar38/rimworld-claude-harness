"""Offline contract checks for home/status's ui.targeter block.

2026-09-08, Threadneedle turns 12-17: `ui.py click "Deploy turret"` reported
success and "nothing opened or closed", fourteen turns in a row. The click had
worked every time. A RimWorld targeter (`Command_VerbTarget`, `Command_Target`)
is not a Window, so the bridge's own before/after -- `RimWorldInput.GetUiState`,
which reads `Find.WindowStack` and nothing else -- and ui.py's surface signature
were both identical across the click. Nothing in 18 tools read `Find.Targeter`
at all. These checks pin the three fields and the two hazards their reads carry.
"""
from pathlib import Path
import re
import unittest


SOURCE = (Path(__file__).parents[1] / "src" / "StatusTool.cs").read_text(encoding="utf-8")


class TargeterBlockSourceTests(unittest.TestCase):
    def test_the_block_hangs_off_ui_and_is_always_present(self):
        # ui{} is Always = true, so targeter rides in on every status call and a
        # caller never has to tell "not targeting" from "not asked".
        self.assertIn('block["targeter"] = TargeterBlock(skipped);', SOURCE)
        self.assertIn("private static Dictionary<string, object> TargeterBlock(", SOURCE)

    def test_all_three_fields_are_seeded_before_anything_can_throw(self):
        block = SOURCE.split("TargeterBlock(List<object> skipped)", 1)[1]
        for field in ('{ "active", false }', '{ "source", null }', '{ "caster", null }'):
            self.assertIn(field, block[:900])

    def test_find_targeter_is_never_read_bare(self):
        # Find.Targeter is ((UIRoot_Play)UIRoot).mapUI.targeter -- a hard cast
        # that throws InvalidCastException outside a play UI root.
        reads = 0
        for hit in re.finditer(r"Find\.Targeter", SOURCE):
            line = SOURCE[SOURCE.rfind("\n", 0, hit.start()) + 1:
                          SOURCE.find("\n", hit.end())]
            bare = line.strip()
            if bare.startswith("//") or bare.startswith("[") or bare.startswith("skipped."):
                continue  # prose, the tool schema and the skip reason, not reads
            reads += 1
            self.assertIn("BridgeCommon.Try", line, bare)
        self.assertEqual(1, reads)

    def test_an_unreadable_targeter_is_reported_as_skipped_not_as_closed(self):
        self.assertIn('Skip("ui.targeter"', SOURCE)
        self.assertIn("active is false because it was not read", SOURCE)

    def test_the_two_targeter_shapes_are_both_covered(self):
        # Command_VerbTarget sets targetingSource to the Verb; Command_Target
        # leaves it null and keeps the pawn in a private field.
        self.assertIn("targeter.targetingSource", SOURCE)
        self.assertIn("targeter.targetingSourceParent", SOURCE)
        self.assertIn("TargeterDelegateLabel(targeter)", SOURCE)
        self.assertIn("TargeterPrivateCaster(targeter)", SOURCE)

    def test_the_private_caster_field_is_reached_by_reflection_not_guessed(self):
        self.assertIn('BridgeCommon.PrivateInstanceField(typeof(Targeter), "caster")', SOURCE)

    def test_the_gizmo_label_comes_from_the_delegate_that_owns_it(self):
        # Command_Target passes its own Update METHOD GROUP as onUpdateAction,
        # so that delegate's Target is the gizmo; it is tried first.
        self.assertIn('"onUpdateAction", "highlightAction", "onGuiAction", "action"', SOURCE)
        self.assertIn("private static string DelegateOwnerLabel(Delegate del)", SOURCE)
        self.assertIn("private static Gizmo OwningGizmo(object target)", SOURCE)

    def test_the_plain_default_label_field_is_preferred_over_the_virtual_labelcap(self):
        owner = SOURCE.split("DelegateOwnerLabel(Delegate del)", 1)[1]
        owner = owner[:owner.find("private static Gizmo OwningGizmo")]
        self.assertLess(owner.index("command.defaultLabel"), owner.index("command.LabelCap"))

    def test_a_verb_source_is_named_by_its_verb_label(self):
        self.assertIn("private static string TargetingSourceLabel(ITargetingSource source)", SOURCE)
        self.assertIn("verb.verbProps == null ? null : verb.verbProps.label", SOURCE)
        self.assertIn("as Verb_CastAbility", SOURCE)

    def test_the_caster_is_an_id_not_an_object(self):
        self.assertIn('block["caster"] = caster == null ? null : '
                      'BridgeCommon.SafeString(() => caster.GetUniqueLoadID());', SOURCE)

    def test_the_response_contract_and_the_notes_both_name_the_field(self):
        # A reader who never opens this file learns it from the tool schema.
        self.assertIn("targeter{active,source,caster}", SOURCE)
        self.assertIn('["targeter"] = "ui.targeter.active true means', SOURCE)
        self.assertIn("It opens no window", SOURCE)

    def test_it_stays_inside_the_one_main_thread_hop(self):
        # UiBlock is called from Run(), which is the body of the single
        # ctx.MainThread.InvokeAsync -- no new hop, and no read off the thread.
        run = SOURCE.split("private static object Run(", 1)[1]
        self.assertIn('payload["ui"] = UiBlock(skipped);', run)
        self.assertNotIn("MainThread", SOURCE.split("TargeterBlock(List<object> skipped)", 1)[1][:4000])


if __name__ == "__main__":
    unittest.main()
