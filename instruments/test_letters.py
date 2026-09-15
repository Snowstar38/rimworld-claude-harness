"""Offline regressions for the letter stack's classification and its age line.

Both come from the 2026-09-07 live stream: an opportunity quest that three
instruments could neither answer nor clear, and an 8.6-hour-old bear letter
that drove three Lookout passes about a corpse.
"""
import io
import unittest
from unittest import mock

import letters


def _letter(**kw):
    row = {"id": "Letter_96", "label": "Opportunity: item stash",
           "letterDef": "NeutralEvent", "type": "RimWorld.NewQuestLetter",
           "ageTicks": 21500, "arrivalTick": 100, "choiceCount": 2,
           "auto": True, "dismissible": True, "text": "A stash out east.",
           "choices": [{"index": 0, "text": "View quest", "disabled": False,
                        "disabledReason": None},
                       {"index": 1, "text": "Close", "disabled": False,
                        "disabledReason": None}]}
    row.update(kw)
    return row


ACCEPT = {"index": 0, "text": "Accept", "disabled": False, "disabledReason": None}
CLOSE = {"index": 1, "text": "Close", "disabled": False, "disabledReason": None}
VIEW = {"index": 2, "text": "View quest", "disabled": False, "disabledReason": None}


class OpportunityQuestTests(unittest.TestCase):
    """`Letter_96`: CHOICE with [View quest / Close] and no dialog behind it."""

    def test_jump_and_close_only_is_INFO_and_not_a_decision(self):
        row = _letter()
        self.assertTrue(letters.navigate_only(row))
        self.assertFalse(letters.is_decision(row))
        self.assertEqual("INFO", letters._kind(row).strip())

    def test_the_sweep_may_take_it(self):
        """It was pinned by two separate keeps: `auto`, and class NewQuestLetter."""
        self.assertIsNone(letters.keep_reason(_letter()))

    def test_a_real_offer_is_still_kept_even_beside_a_jump_button(self):
        row = _letter(choices=[ACCEPT, VIEW, CLOSE])
        self.assertFalse(letters.navigate_only(row))
        self.assertTrue(letters.is_decision(row))
        self.assertEqual("somebody has to answer it", letters.keep_reason(row))

    def test_a_letter_with_no_reported_buttons_is_still_kept(self):
        """`choices` is null for anything that is not a ChoiceLetter: unread
        options, not absent ones."""
        row = _letter(choices=[], type="Verse.StandardLetter", auto=False)
        self.assertFalse(letters.navigate_only(row))
        self.assertIn("no buttons reported", letters.keep_reason(row))

    def test_the_line_says_opportunity_quest_nothing_to_answer(self):
        note = letters.opportunity_note(_letter())
        self.assertIn("opportunity quest: nothing to answer", note)
        self.assertIn("letters.py dismiss Letter_96", note)

    def test_no_dialog_opened_names_the_opportunity_quest(self):
        row = _letter()
        with mock.patch.object(letters.rim, "game", return_value={"success": True}), \
             mock.patch.object(letters, "dialog_window", return_value=None), \
             mock.patch.object(letters.time, "sleep"), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertIsNone(letters.open_letter("Letter_96", hold=False,
                                                  timeout=0.0, letter=row))
        self.assertIn("nothing to answer", out.getvalue())


class StaleTests(unittest.TestCase):
    """An 8.6h "Grizzly bear hunting Longhoff" read as an attack in progress."""

    def test_age_is_in_game_hours(self):
        self.assertAlmostEqual(8.6, letters.age_hours(_letter()))

    def test_the_printed_row_carries_the_age_and_the_stale_flag(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            letters.show([_letter(label="Grizzly bear hunting Longhoff")])
        text = out.getvalue()
        self.assertIn("8.6h", text)
        self.assertIn("STALE", text)
        # the explanation is printed once, in the footer, not under every row
        self.assertNotIn("not what is happening now", text)

    def test_a_fresh_letter_is_not_flagged(self):
        fresh = _letter(ageTicks=1200, choices=[CLOSE], auto=False,
                        type="Verse.StandardLetter")
        self.assertFalse(letters.is_stale(fresh))
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            letters.show([fresh])
        self.assertNotIn("STALE", out.getvalue())


DEAD_ACCEPT = {"index": 1, "text": "Accept", "disabled": True,
               "disabledReason": "This quest has expired.", "closesDialog": True,
               "hasAction": True, "hasLink": False}
LIVE_CLOSE = {"index": 2, "text": "Close", "disabled": False,
              "disabledReason": None, "closesDialog": True,
              "hasAction": True, "hasLink": False}
LINKS_ON = {"index": 3, "text": "Tell me more", "disabled": False,
            "disabledReason": None, "closesDialog": False,
            "hasAction": False, "hasLink": True}
INERT = {"index": 4, "text": "Nothing", "disabled": False, "disabledReason": None,
         "closesDialog": False, "hasAction": False, "hasLink": False}


class ExpiredQuestTests(unittest.TestCase):
    """`decide "Close"` on an expired quest opened a Dialog_NodeTreeWithFaction
    Info that force-paused the game and killed the play service."""

    def test_a_letter_whose_every_decision_is_disabled_is_expired(self):
        row = _letter(choices=[DEAD_ACCEPT, LIVE_CLOSE])
        self.assertTrue(letters.is_expired(row))
        self.assertEqual([], letters.answerable_choices(row))

    def test_one_live_decision_is_not_expired(self):
        row = _letter(choices=[DEAD_ACCEPT, ACCEPT, LIVE_CLOSE])
        self.assertFalse(letters.is_expired(row))

    def test_the_note_names_the_reason_and_does_not_order_a_dismissal(self):
        note = letters.expired_note(_letter(choices=[DEAD_ACCEPT, LIVE_CLOSE]))
        self.assertIn("This quest has expired.", note)
        self.assertIn("letters.py dismiss Letter_96", note)
        self.assertIn("if it can lift, leave the letter standing", note)

    def test_opening_an_expired_letter_is_refused_before_the_dialog_goes_up(self):
        row = _letter(choices=[DEAD_ACCEPT, LIVE_CLOSE])
        calls = []
        with mock.patch.object(letters.rim, "game",
                               side_effect=lambda *a, **k: calls.append(a) or {}), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertIsNone(letters.open_letter("Letter_96", hold=False,
                                                  letter=row))
        self.assertEqual([], calls)
        self.assertIn("REFUSED, nothing was opened", out.getvalue())
        self.assertIn("force-pausing dialog", out.getvalue())

    def test_the_stack_listing_carries_the_expired_note(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            letters.show([_letter(choices=[DEAD_ACCEPT, LIVE_CLOSE])])
        self.assertIn("every decision on this letter is DISABLED", out.getvalue())


class DecideTests(unittest.TestCase):
    """A decision must never leave a modal standing: a letter dialog is a
    Dialog_NodeTree and holds the clock."""

    def _decide(self, windows, opts, alive=(True, True), text="Close",
                letter=None, scrape=None):
        clicked = []
        with mock.patch.object(letters, "dialog_window", side_effect=list(windows)), \
             mock.patch.object(letters, "_scrape_options",
                               return_value=list(scrape or [])), \
             mock.patch.object(letters.ui, "supervised_play_alive",
                               side_effect=list(alive)), \
             mock.patch.object(letters.ui, "click",
                               side_effect=lambda t, **k: clicked.append(t)), \
             mock.patch.object(letters.rim, "game", return_value={}), \
             mock.patch.object(letters.time, "sleep"), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            ok = letters.decide(text, hold=False,
                                letter=letter or _letter(choices=opts))
        return ok, clicked, out.getvalue()

    def test_a_dialog_that_opened_behind_the_decision_is_closed_and_named(self):
        ok, clicked, text = self._decide(
            windows=["Verse.Dialog_NodeTree",
                     "RimWorld.Dialog_NodeTreeWithFactionInfo",
                     "RimWorld.Dialog_NodeTreeWithFactionInfo", None],
            opts=[LIVE_CLOSE],
            scrape=[letters.Option("Close", True)])
        self.assertTrue(ok)
        self.assertIn("opened RimWorld.Dialog_NodeTreeWithFactionInfo on top of",
                      text)
        self.assertIn("closed RimWorld.Dialog_NodeTreeWithFactionInfo behind the"
                      " decision", text)
        self.assertEqual(["Close", "Close"], clicked)

    def test_a_modal_nothing_could_close_is_said_out_loud(self):
        ok, _, text = self._decide(
            windows=["Verse.Dialog_NodeTree", "RimWorld.Dialog_Trade",
                     "RimWorld.Dialog_Trade"],
            opts=[LIVE_CLOSE], scrape=[])
        self.assertFalse(ok)
        self.assertIn("STILL OPEN and it force-pauses the game", text)
        self.assertIn('python ui.py click', text)

    def test_the_dialog_the_decision_aimed_at_is_never_closed_for_you(self):
        """Pressing Close on it would take the letter off the stack unanswered."""
        ok, clicked, text = self._decide(
            windows=["Verse.Dialog_NodeTree", "Verse.Dialog_NodeTree"],
            opts=[ACCEPT, LIVE_CLOSE], text="Accept",
            scrape=[letters.Option("Close", True)])
        self.assertFalse(ok)
        self.assertEqual(["Accept"], clicked)
        self.assertIn("STILL OPEN and it force-pauses the game", text)

    def test_a_service_that_died_over_the_decision_prints_the_restart_line(self):
        _, _, text = self._decide(
            windows=["Verse.Dialog_NodeTree", None],
            opts=[LIVE_CLOSE], alive=(True, False))
        self.assertIn("supervised play STOPPED", text)
        self.assertIn("python play.py start", text)

    def test_nothing_is_said_about_the_service_when_it_was_never_running(self):
        _, _, text = self._decide(
            windows=["Verse.Dialog_NodeTree", None],
            opts=[LIVE_CLOSE], alive=(False, False))
        self.assertNotIn("supervised play STOPPED", text)

    def test_an_option_that_does_not_close_the_dialog_says_so_first(self):
        _, clicked, text = self._decide(
            windows=["Verse.Dialog_NodeTree", None],
            opts=[LINKS_ON], text="Tell me more")
        self.assertIn("does NOT close the dialog", text)
        self.assertEqual(["Tell me more"], clicked)

    def test_an_option_with_no_action_and_no_link_is_refused(self):
        ok, clicked, text = self._decide(
            windows=["Verse.Dialog_NodeTree"], opts=[INERT], text="Nothing")
        self.assertFalse(ok)
        self.assertEqual([], clicked)
        self.assertIn("clicking it does nothing at all", text)

    def test_a_bare_decide_uses_the_letters_own_buttons_not_the_screen(self):
        """`options()` with no letter always fell through to the scrape, so the
        payload's Close was never the one clicked."""
        pressed = []
        with mock.patch.object(letters, "dialog_window",
                               side_effect=["Verse.Dialog_NodeTree", None]), \
             mock.patch.object(letters, "_scrape_options", return_value=[]), \
             mock.patch.object(letters.ui, "supervised_play_alive",
                               return_value=False), \
             mock.patch.object(letters.ui, "click",
                               side_effect=lambda t, **k: pressed.append(t)), \
             mock.patch.object(letters.rim, "game") as game, \
             mock.patch.object(letters.time, "sleep"), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertTrue(letters.decide(hold=False,
                                           letter=_letter(choices=[DEAD_ACCEPT,
                                                                   LIVE_CLOSE])))
        self.assertEqual(["Close"], pressed)
        self.assertEqual([], [c for c in game.call_args_list
                              if c[0][0] == "rimworld/press_accept"])

    def test_a_text_no_button_on_screen_carries_clicks_nothing(self):
        def boom(*a, **k):
            raise LookupError("no clickable UI element for 'Close'")
        with mock.patch.object(letters, "dialog_window",
                               return_value="Verse.Dialog_NodeTree"), \
             mock.patch.object(letters.ui, "supervised_play_alive",
                               return_value=False), \
             mock.patch.object(letters.ui, "click", side_effect=boom), \
             mock.patch.object(letters.time, "sleep"), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertFalse(letters.decide("Close", hold=False,
                                            letter=_letter(choices=[LIVE_CLOSE])))
        self.assertIn("NOTHING WAS CLICKED", out.getvalue())


class ForcePauseReadTests(unittest.TestCase):
    def test_force_pausing_names_the_windows_holding_the_clock(self):
        state = {"windows": [{"type": "Verse.ImmediateWindow", "forcePause": False},
                             {"type": "Verse.Dialog_NodeTree", "forcePause": True}]}
        self.assertEqual(["Verse.Dialog_NodeTree"], letters.force_pausing(state))

    def test_an_unread_bridge_is_not_an_empty_screen(self):
        self.assertEqual([], letters.force_pausing(letters.UNKNOWN))
        with mock.patch.object(letters.rim, "game", return_value="garbled"):
            self.assertIs(letters.UNKNOWN, letters.ui_state())


if __name__ == "__main__":
    unittest.main()


class CliWiringTests(unittest.TestCase):
    """The CLI surface BUGS names. `letters.py` keeps its command dispatch
    inline under `if __name__`, so there is no main() to drive; these pin the
    pieces the dispatch calls, plus the dispatch source itself."""

    SRC = None

    @classmethod
    def setUpClass(cls):
        import pathlib
        cls.SRC = pathlib.Path(letters.__file__).read_text(encoding="utf-8")

    def test_dismiss_lets_a_refusal_come_back_instead_of_raising(self):
        """A letter the game will not right-click away answers success:false.
        `dismiss()` was a STRICT rim.game call, and rim.game RAISES on that --
        so the one case the CLI line above it promises to handle
        ("the call below will answer dismissed: false") came out as a
        BridgeError traceback, and the exit-1 below it never ran."""
        seen = {}

        def game(name, args=None, strict=True, **kw):
            seen["strict"] = strict
            if strict:
                raise letters.rim.BridgeError("%s: refused" % name)
            return {"success": False, "dismissed": False,
                    "message": "cannot be dismissed through the letter stack."}

        with mock.patch.object(letters.rim, "game", side_effect=game):
            r = letters.dismiss("Letter_96")
        self.assertIs(False, seen["strict"])
        self.assertIs(False, r.get("dismissed"))

    def test_the_windows_diagnostic_reads_through_the_guarded_helper(self):
        """--windows went straight to rim.game(strict=False) and then to
        `.get` -- so a split or truncated reply (a bare string: the exact
        flakiness you open --windows to diagnose) was an AttributeError, and a
        refusal printed "windows: none reported (checked, not assumed)", an
        affirmative claim about a read that failed. `ui_state()` exists for
        precisely this and returns UNKNOWN for both shapes."""
        block = self.SRC[self.SRC.index('if cmd in ("--windows"'):]
        block = block[:block.index("if cmd == \"sweep\"")]
        self.assertIn("ui_state()", block)
        self.assertNotIn('rim.game("rimworld/get_ui_state"', block)

    def test_ui_state_answers_unknown_for_both_unreadable_shapes(self):
        for reply in ("...truncated json...", {"success": False, "message": "x"}):
            with mock.patch.object(letters.rim, "game", return_value=reply):
                self.assertIs(letters.UNKNOWN, letters.ui_state())

    def test_the_decide_command_hands_the_letter_row_to_decide(self):
        """`decide()` takes `letter=` for the game's own `disabled` flag;
        without it `options()` falls through to the screen scrape, which builds
        every Option with disabled=False. The CLI passed no row, so the guard
        against clicking a greyed-out decision -- the whole point of reading
        the payload -- was dead on the only path a person uses. `open` two
        branches up already looks the row up."""
        block = self.SRC[self.SRC.index('elif cmd == "decide":'):]
        block = block[:block.index("    else:")]
        self.assertIn("letter=", block)


class CloseReadBackTests(unittest.TestCase):
    def test_a_close_that_did_not_close_is_not_reported_as_closed(self):
        """`closed.append(win)` fired on the strength of the click not raising.
        A Close that links to another page of the same node tree was then
        reported closed three times AND reported still open, in one breath."""
        stuck = "RimWorld.Dialog_NodeTreeWithFactionInfo"
        with mock.patch.object(letters, "dialog_window",
                               side_effect=[stuck] * 8),                 mock.patch.object(letters, "_scrape_options",
                                  return_value=[letters.Option("Close", True)]),                 mock.patch.object(letters.ui, "click", lambda t, **k: None),                 mock.patch.object(letters.time, "sleep"),                 mock.patch("sys.stdout", new_callable=io.StringIO):
            win, closed = letters.close_open_dialog()
        self.assertEqual(stuck, win)
        self.assertEqual([], closed)


class ForcePauseReportTests(unittest.TestCase):
    """Live 2026-09-12: `letters.py open <id>` on a running service printed
    nothing, and `play.py status` said `service exited` moments later. The
    watcher needs ~9.5 s to give up on a force pause, and report_force_pause
    read `supervised_play_alive()` once, immediately, and got True."""

    def _say(self, alive, now):
        with mock.patch.object(letters.ui, "supervised_play_alive",
                               return_value=now),              mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            said = letters.report_force_pause(alive)
        return said, out.getvalue()

    def test_a_service_still_up_at_this_instant_still_gets_the_restart_line(self):
        said, txt = self._say(alive=True, now=True)
        self.assertTrue(said)
        self.assertIn("WILL STOP", txt)
        self.assertIn("python play.py start", txt)

    def test_a_service_already_gone_reads_stopped(self):
        said, txt = self._say(alive=True, now=False)
        self.assertTrue(said)
        self.assertIn("STOPPED", txt)
        self.assertNotIn("WILL STOP", txt)
        self.assertIn("python play.py start", txt)

    def test_nothing_is_said_when_no_service_was_running(self):
        said, txt = self._say(alive=False, now=False)
        self.assertFalse(said)
        self.assertEqual("", txt)

    def test_an_unreadable_service_state_says_nothing(self):
        said, txt = self._say(alive=None, now=None)
        self.assertFalse(said)
        self.assertEqual("", txt)
