"""Offline regressions for dialog.py; no running game is contacted.

This is the only route to a typed name -- a colony, a pawn, a settlement -- so
the two things worth pinning offline are that a refusal reads as a refusal
(the companion answers an over-long name with success:TRUE and an `error`, so
the obvious check is the wrong one) and that nothing that is not the text ends
up typed into the box.
"""
import io
import unittest
from unittest import mock

import dialog


def name_dialog(**kw):
    """What home/dialog_text answers on a Dialog_NamePawn."""
    r = {"success": True, "tool": "home/dialog_text",
         "window": "Verse.Dialog_NamePawn", "dryRun": False, "applied": True,
         "fields": [{"name": "names[0].current", "label": "FirstName",
                     "maxLength": 12, "before": "Finn"}],
         "set": {"field": "names[0].current", "before": "Finn",
                 "after": "Finnegan"},
         "accepted": False, "error": None}
    r.update(kw)
    return r


def too_long():
    """The over-long refusal: Payload() hard-codes success true, so the only
    thing that says it was refused is `set: None` beside an `error`."""
    return name_dialog(applied=False, set=None,
                       error='"names[0].current" holds at most 12 characters; '
                             '"Finnegan the Third" is 18. Nothing was written.')


class RefusalTests(unittest.TestCase):
    def test_an_over_long_name_reads_as_a_refusal_and_exits_non_zero(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = dialog.show(too_long(), wrote=True)
        text = out.getvalue()
        self.assertEqual(1, code)
        self.assertIn("REFUSED", text)
        self.assertIn("at most 12 characters", text)

    def test_a_real_write_still_reports_done_and_exits_zero(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = dialog.show(name_dialog(), wrote=True)
        self.assertEqual(0, code)
        self.assertIn("SET DONE", out.getvalue())

    def test_a_plain_listing_is_not_mistaken_for_a_refused_write(self):
        """A listing comes back in the SAME shape as the refusal -- success
        true, no `set`, a sentence in `error` ("No text was given, so this is a
        listing only.") -- so only the caller's own intent separates them."""
        listing = name_dialog(applied=False, set=None, dryRun=True,
                              error="No text was given, so this is a listing "
                                    "only. Pass text (and field when fields[] "
                                    "has more than one row).")
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = dialog.show(listing, wrote=False)
        self.assertEqual(0, code)
        self.assertNotIn("REFUSED", out.getvalue())


class ArgumentTests(unittest.TestCase):
    def run_cli(self, argv, reply=None):
        sent = []

        def game(tool, args=None, strict=True):
            sent.append(args)
            return reply or name_dialog()

        with mock.patch.object(dialog.sys, "argv", ["dialog.py"] + argv), \
                mock.patch.object(dialog.rim, "init"), \
                mock.patch.object(dialog.rim, "game", side_effect=game), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = dialog.main()
        return code, sent, out.getvalue()

    def test_a_mistyped_flag_is_refused_not_typed_into_the_box(self):
        """Only --json/--do/--accept were stripped and everything left was
        joined into the text, so `dialog.py "Lampblack" --do --acept` WROTE
        the literal string `Lampblack --acept` into the colony name."""
        code, sent, text = self.run_cli(["Lampblack", "--do", "--acept"])
        self.assertEqual(2, code)
        self.assertEqual([], sent)
        self.assertIn("--acept", text)

    def test_accept_on_its_own_is_refused_rather_than_quietly_listing(self):
        """`--accept --do` with the name already right parsed accept=True and
        then, with no text left, called fields() -- a read. The companion
        cannot press accept without text (`if (list || text == null)` returns
        the listing before OnAcceptKeyPressed), so the honest answer is a
        refusal naming the route, not a read that looks like it worked."""
        code, sent, text = self.run_cli(["--accept", "--do"])
        self.assertEqual(2, code)
        self.assertEqual([], sent)
        self.assertIn("--do --accept", text)

    def test_a_bare_call_still_lists_the_boxes(self):
        code, sent, _ = self.run_cli([])
        self.assertEqual(0, code)
        self.assertIs(True, sent[0].get("list"))

    def test_the_text_reaches_the_tool_unchanged(self):
        code, sent, _ = self.run_cli(["Lampblack", "--do"])
        self.assertEqual("Lampblack", sent[0]["text"])
        self.assertIs(False, sent[0]["dryRun"])


if __name__ == "__main__":
    unittest.main()


class AcceptVerificationTests(unittest.TestCase):
    """Live 2026-09-12 on `Verse.Dialog_NamePawn`: `dialog.py "Sammy" --field
    nick --accept --do` printed `SET DONE / Samantha -> Sammy / accepted: yes`,
    the dialog stayed on screen, and `home/list_pawns` still said Samantha.
    `ui.py click "Accept"` then closed it and the rename landed. The companion
    hard-codes the accept's success, so the claim is checked against the window
    stack instead of reprinted."""

    REPLY = {"success": True, "dryRun": False, "accepted": True,
             "window": "Verse.Dialog_NamePawn", "fields": [],
             "set": {"field": "names[1].current",
                     "before": "Samantha", "after": "Sammy"}}

    def _show(self, after):
        with mock.patch.object(dialog, "fields", return_value=after),              mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            dialog.show(self.REPLY, wrote=True, verify_accept=True)
        return out.getvalue()

    def test_a_dialog_still_on_screen_is_reported_as_NOT_accepted(self):
        txt = self._show({"success": True, "window": "Verse.Dialog_NamePawn",
                          "fields": [{"name": "names[1].current"}]})
        self.assertIn("accepted: NO", txt)
        self.assertIn("STILL OPEN", txt)
        self.assertIn('ui.py click "Accept"', txt)

    def test_a_dialog_that_closed_reads_accepted(self):
        txt = self._show({"success": False, "window": None,
                          "error": "No dialog is open beneath the overlays."})
        self.assertIn("accepted: yes", txt)
        self.assertNotIn("accepted: NO", txt)

    def test_a_different_window_on_top_also_reads_accepted(self):
        txt = self._show({"success": True, "window": "Verse.Dialog_MessageBox"})
        self.assertIn("accepted: yes", txt)

    def test_an_unreadable_stack_is_UNVERIFIED_not_success(self):
        with mock.patch.object(dialog, "fields", side_effect=RuntimeError("no")),              mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            dialog.show(self.REPLY, wrote=True, verify_accept=True)
        txt = out.getvalue()
        self.assertIn("UNVERIFIED", txt)
        self.assertNotIn("accepted: yes", txt)

    def test_a_read_only_call_does_not_pay_for_the_check(self):
        """No accept was asked for, so no extra bridge call is made."""
        with mock.patch.object(dialog, "fields") as f,              mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            dialog.show(dict(self.REPLY, accepted=False), wrote=True)
        f.assert_not_called()
        self.assertIn("accepted: no", out.getvalue())
