"""Source guards for home/dialog_text against the real naming dialogs.

The shapes checked here are read off RimWorld 1.6's own assembly:

  Verse.Dialog_NamePawn        names : List<NameContext>, plus three plain
                               strings that are focus bookkeeping --
                               focusControlOverride, currentControl, genderText
  Verse.Dialog_NamePawn+NameContext
                               current : string   (the box)
                               textboxName : string
                               label : TaggedString   (not a string field)
                               maximumNameLength : int
                               editable : bool
  RimWorld.Dialog_GiveName     curName, curSecondName, and four message keys;
                               RimWorld.Dialog_NamePlayerSettlement is one of
                               these, and adds no string of its own.

So a name-pawn dialog must offer its list rows and NOT its bookkeeping, a
give-name dialog must land on curName by the preferred-name rule, and a write
longer than a box's own cap must be refused rather than silently cut back by the
next redraw.
"""
from pathlib import Path
import unittest


SRC = Path(__file__).parents[1] / "src"
DIALOG = (SRC / "DialogTextTool.cs").read_text(encoding="utf-8")


class DialogTextSourceTests(unittest.TestCase):
    def test_the_tool_is_registered_under_its_own_name(self):
        self.assertIn('private const string ToolName = "home/dialog_text"', DIALOG)

    def test_the_box_and_its_label_are_looked_up_by_the_real_field_names(self):
        self.assertIn('ContextTextFieldNames = { "current", "name" }', DIALOG)
        self.assertIn('ContextLabelFieldNames = { "textboxName"', DIALOG)

    def test_a_name_context_list_wins_over_the_windows_plain_strings(self):
        """Dialog_NamePawn declares three strings that are not boxes."""
        self.assertIn("return slots.Any(s => s.FromList) "
                      "? slots.Where(s => s.FromList).ToList() : slots;", DIALOG)
        self.assertIn("FromList = true", DIALOG)
        self.assertIn("currentControl", DIALOG)

    def test_give_name_still_lands_on_curname_without_a_field(self):
        """Dialog_GiveName has seven strings and only two are boxes."""
        self.assertIn('PreferredFieldNames = { "curName", "name", "text", "newName" }',
                      DIALOG)

    def test_the_length_cap_is_read_and_a_longer_write_is_refused(self):
        self.assertIn('ContextLimitFieldNames = { "maximumNameLength"', DIALOG)
        self.assertIn("chosen.MaxLength > 0 && text.Length > chosen.MaxLength",
                      DIALOG)
        self.assertIn("The game would cut it back on the next frame.", DIALOG)
        self.assertIn("Nothing was written; ", DIALOG)

    def test_the_cap_rides_on_every_listed_field(self):
        self.assertIn('{ "maxLength", s.MaxLength > 0 ? (object)s.MaxLength : null }',
                      DIALOG)
        self.assertIn('{ "label", s.Label }', DIALOG)

    def test_a_window_with_no_box_says_what_it_does_declare(self):
        """"declares no string field" was a dead end; the next call needs to
        know whether the box lives somewhere this does not look."""
        self.assertIn("private static string NothingToTypeInto(Window window,"
                      " string typeName)", DIALOG)
        self.assertIn("What it does declare: ", DIALOG)
        self.assertIn("A box held in a property rather than a field would not be"
                      " found here.", DIALOG)
        self.assertNotIn("declares no string field of its own, so there is nothing"
                         " to type into.", DIALOG)

    def test_the_windows_own_chrome_is_never_offered(self):
        self.assertIn("for (var t = type; t != null && t != typeof(Window); "
                      "t = t.BaseType)", DIALOG)

    def test_a_value_type_row_is_skipped_rather_than_written_to_a_copy(self):
        self.assertIn("element == typeof(string) || element.IsValueType", DIALOG)

    def test_an_uneditable_row_is_a_drawn_label_and_is_left_out(self):
        self.assertIn('element.GetField("editable"', DIALOG)

    def test_accept_goes_to_the_window_that_was_written_not_the_top_one(self):
        self.assertIn("window.OnAcceptKeyPressed();", DIALOG)

    def test_the_write_and_the_read_back_share_one_main_thread_hop(self):
        self.assertIn("MarshalToMainThread = false", DIALOG)
        self.assertIn(".InvokeAsync(() => Run(text, field, list, accept, dryRun),"
                      " cancellationToken)", DIALOG)


if __name__ == "__main__":
    unittest.main()
