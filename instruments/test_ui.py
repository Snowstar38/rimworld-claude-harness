"""Mock-only regressions for ui.py command-line actions, and for dialog.py's
rendering of what home/dialog_text reports."""
import io
import unittest
from unittest import mock

import dialog
import ui


TABS = {"success": True, "count": 3, "tabs": [
    {"targetId": "main-tab:Work", "defName": "Work", "label": "Work",
     "type": "RimWorld.MainTabWindow_Work"},
    {"targetId": "main-tab:World", "defName": "World", "label": "World",
     "type": ""},
    {"targetId": "main-tab:Menu", "defName": "Menu", "label": "Menu",
     "type": "RimWorld.MainTabWindow_Menu"}]}


class UiCliTests(unittest.TestCase):
    def test_surface_first_click_calls_public_api_with_correct_argument_order(self):
        with mock.patch.object(ui.sys, "argv", ["ui.py", "Research", "click", "OK"]), \
             mock.patch.object(ui.rim, "init"), \
             mock.patch.object(ui, "click", return_value=ui.Clicked(True)) as click, \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(0, ui.main())
        click.assert_called_once_with("OK", surface="Research", path=None)

    def test_unscoped_positional_click(self):
        with mock.patch.object(ui.sys, "argv", ["ui.py", "click", "View", "quest"]), \
             mock.patch.object(ui.rim, "init"), \
             mock.patch.object(ui, "click", return_value=ui.Clicked(True)) as click, \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(0, ui.main())
        click.assert_called_once_with("View quest", surface=None, path=None)


class UiSurfaceTests(unittest.TestCase):
    def run_cli(self, argv, game):
        with mock.patch.object(ui.sys, "argv", ["ui.py"] + argv), \
             mock.patch.object(ui.rim, "init"), \
             mock.patch.object(ui.rim, "game", side_effect=game), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            ui.main()
        return out.getvalue()

    def test_surfaces_also_lists_the_bottom_main_tab_bar(self):
        """The tab bar is not a surface, so --surfaces used to hide the only
        route to World entirely."""
        def game(tool, args=None, strict=True):
            if tool == "rimworld/list_main_tabs":
                return TABS
            return {"surfaces": []}
        text = self.run_cli(["--surfaces"], game)
        self.assertIn("main-tab:World", text)
        self.assertIn("main-tab:Work", text)

    def test_a_typeless_main_tab_is_named_a_toggle(self):
        def game(tool, args=None, strict=True):
            if tool == "rimworld/list_main_tabs":
                return TABS
            return {"surfaces": []}
        text = self.run_cli(["--surfaces"], game)
        world = [l for l in text.splitlines() if "main-tab:World" in l][0]
        work = [l for l in text.splitlines() if "main-tab:Work" in l][0]
        self.assertIn("TOGGLE", world)
        self.assertIn("open_main_tab cannot open it", world)
        self.assertIn("tabWindow", work)

    def test_an_open_tab_is_marked_as_standing_over_the_map(self):
        """A tab left open draws over the map and nothing said which one."""
        tabs = {"tabs": [dict(TABS["tabs"][0], isOpen=True), TABS["tabs"][2]]}

        def game(tool, args=None, strict=True):
            if tool == "rimworld/list_main_tabs":
                return tabs
            return {"surfaces": []}
        text = self.run_cli(["--surfaces"], game)
        work = [l for l in text.splitlines() if "main-tab:Work" in l][0]
        menu = [l for l in text.splitlines() if "main-tab:Menu" in l][0]
        self.assertIn("OPEN over the map", work)
        self.assertNotIn("OPEN over the map", menu)

    def test_selection_reads_back_what_is_actually_selected(self):
        """click_cell on a cell holding a corpse and a stockpile selects the
        ZONE; the click reply calls both "cell x,z"."""
        def game(tool, args=None, strict=True):
            self.assertEqual("rimworld/get_selection_semantics", tool)
            return {"hasSelection": True, "selectedCount": 1,
                    "selectedObjects": [{"kind": "Zone", "label": "Stockpile 1",
                                         "id": "Zone_3"}]}
        text = self.run_cli(["--selection"], game)
        self.assertIn("Zone Stockpile 1", text)
        self.assertIn("Zone_3", text)

    def test_an_empty_selection_says_so_rather_than_printing_nothing(self):
        def game(tool, args=None, strict=True):
            return {"hasSelection": False, "selectedCount": 0,
                    "selectedObjects": []}
        self.assertIn("nothing selected", self.run_cli(["--selection"], game))


def _button(text, y=20):
    """A RimWorld button: an inert label with an invisible button over it."""
    rect = {"x": 10, "y": y, "width": 90, "height": 24}
    return [{"label": text, "actionable": False, "kind": "label",
             "screenRect": dict(rect), "targetId": "ui-element:1:0:0"},
            {"label": None, "actionable": True, "kind": "button",
             "screenRect": dict(rect), "targetId": "ui-element:1:0:1"}]


def _surface(label, wtype, texts):
    els = []
    for n, text in enumerate(texts):
        els.extend(_button(text, y=20 + 30 * n))
    return {"surfaceTargetId": "ui-surface:1:0", "label": label, "type": wtype,
            "surfaceKind": "window", "elementCount": len(els),
            "actionableElementCount": len(els) // 2, "elements": els,
            "screenRect": {"x": 0, "y": 0, "width": 800, "height": 600}}


class ClickTests(unittest.TestCase):
    """A click that hit nothing named every label on screen and not the
    clickable ones; a click that fired proved nothing about the screen."""

    def test_a_click_that_matched_nothing_lists_the_clickable_texts(self):
        modal = {"surfaces": [_surface("Research finished",
                                       "RimWorld.Dialog_NodeTree",
                                       ["OK", "Jump to location"])]}
        with mock.patch.object(ui.rim, "game", return_value=modal):
            with self.assertRaises(LookupError) as raised:
                ui.click("Close")
        msg = str(raised.exception)
        self.assertIn("NOTHING WAS CLICKED", msg)
        self.assertIn("'OK'", msg)
        self.assertIn("'Jump to location'", msg)

    def test_a_click_that_fired_reads_back_the_window_that_went_away(self):
        modal = {"surfaces": [_surface("Research finished",
                                       "RimWorld.Dialog_NodeTree", ["OK"])]}
        clicked = []

        def game(tool, args=None, strict=True):
            if tool == "rimworld/click_ui_target":
                clicked.append(args)
                return {"success": True}
            return {"surfaces": []} if clicked else modal

        with mock.patch.object(ui.rim, "game", side_effect=game), \
             mock.patch.object(ui.time, "sleep"):
            got = ui.click("OK")
        self.assertTrue(got)
        self.assertEqual([], got.opened)
        self.assertIn("RimWorld.Dialog_NodeTree 'Research finished'",
                      "".join(got.closed))
        self.assertIn("closed", got.line())

    def test_a_surface_id_passed_as_the_text_is_named_not_hunted_for(self):
        with self.assertRaises(ValueError) as raised:
            ui.click("main-tab:Research")
        self.assertIn("surface id", str(raised.exception))


class TabTests(unittest.TestCase):
    """open_main_tab does not toggle, so a tab opened to read it stayed over
    the map (Research, 482 elements; Quests, with no Close element on it)."""

    def _game(self, calls, paused=False):
        def game(tool, args=None, strict=True):
            calls.append((tool, args))
            if tool == "rimworld/list_main_tabs":
                return TABS
            if tool == "home/status":
                return {"time": {"paused": paused, "forcePaused": paused,
                                 "timeSpeed": "Normal", "ticksGame": 5000}}
            if tool == "rimworld/open_main_tab":
                return {"success": True, "changed": True,
                        "after": {"mainTabOpen": True,
                                  "openMainTabId": "main-tab:Work"}}
            if tool == "rimworld/close_main_tab":
                return {"success": True, "changed": True,
                        "after": {"mainTabOpen": False, "openMainTabId": None}}
            return {"surfaces": []}
        return game

    def test_reading_a_tab_closes_it_again(self):
        calls = []
        with mock.patch.object(ui.sys, "argv", ["ui.py", "tab", "Work"]), \
             mock.patch.object(ui.rim, "init"), \
             mock.patch.object(ui.rim, "game", side_effect=self._game(calls)), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(0, ui.main())
        tools = [t for t, _ in calls]
        self.assertIn("rimworld/open_main_tab", tools)
        self.assertIn("rimworld/close_main_tab", tools)
        self.assertIn("opened, read and CLOSED again", out.getvalue())

    def test_keep_open_says_the_tab_is_still_over_the_map(self):
        calls = []
        with mock.patch.object(ui.sys, "argv",
                               ["ui.py", "tab", "Work", "--keep-open"]), \
             mock.patch.object(ui.rim, "init"), \
             mock.patch.object(ui.rim, "game", side_effect=self._game(calls)), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(0, ui.main())
        self.assertNotIn("rimworld/close_main_tab", [t for t, _ in calls])
        self.assertIn("LEFT OPEN over the map", out.getvalue())

    def test_close_uses_close_main_tab(self):
        calls = []
        with mock.patch.object(ui.sys, "argv", ["ui.py", "close"]), \
             mock.patch.object(ui.rim, "init"), \
             mock.patch.object(ui.rim, "game", side_effect=self._game(calls)), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(0, ui.main())
        self.assertEqual(["rimworld/close_main_tab"], [t for t, _ in calls])
        self.assertIn("Open now: none", out.getvalue())

    def test_the_world_toggle_is_refused_with_a_pointer_at_world_py(self):
        calls = []
        with mock.patch.object(ui.rim, "game", side_effect=self._game(calls)):
            with self.assertRaises(ValueError) as raised:
                ui.open_tab("World")
        self.assertIn("world.py", str(raised.exception))
        self.assertNotIn("rimworld/open_main_tab", [t for t, _ in calls])


class ForcePauseTabTests(unittest.TestCase):
    """The Menu tab force-pauses the game, twice on 2026-09-07, and supervised
    play stopped with it and said nothing."""

    def _read(self, alive, paused=False):
        calls = []
        game = TabTests()._game(calls, paused=paused)
        with mock.patch.object(ui.rim, "game", side_effect=game), \
             mock.patch.object(ui, "supervised_play_alive", side_effect=alive), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            ui.read_tab("Menu")
        return out.getvalue()

    def test_a_force_pausing_tab_is_named_before_it_is_opened(self):
        text = self._read(alive=[True, True])
        self.assertIn("FORCE-PAUSES the game", text)
        self.assertIn("supervised play stops on any force pause", text)

    def test_a_service_that_died_over_the_read_prints_the_restart_line(self):
        text = self._read(alive=[True, False])
        self.assertIn("supervised play STOPPED", text)
        self.assertIn("python play.py start", text)

    def test_a_clock_left_stopped_prints_the_restart_line(self):
        """Nothing was supervising, so only the clock can say what it cost."""
        text = self._read(alive=[False, False], paused=True)
        self.assertNotIn("supervised play STOPPED", text)
        self.assertIn("the clock is STOPPED", text)
        self.assertIn("python play.py start", text)

    def test_a_tab_that_does_not_pause_says_nothing_and_reads_no_clock(self):
        calls = []
        game = TabTests()._game(calls)
        with mock.patch.object(ui.rim, "game", side_effect=game), \
             mock.patch.object(ui, "supervised_play_alive", return_value=False), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            ui.read_tab("Work")
        self.assertNotIn("FORCE-PAUSES", out.getvalue())
        self.assertNotIn("home/status", [t for t, _ in calls])

    def test_the_tab_bar_marks_which_tab_holds_the_clock(self):
        def game(tool, args=None, strict=True):
            return TABS
        with mock.patch.object(ui.rim, "game", side_effect=game), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            ui.print_main_tabs()
        lines = out.getvalue().splitlines()
        menu = [l for l in lines if "main-tab:Menu" in l][0]
        work = [l for l in lines if "main-tab:Work" in l][0]
        self.assertIn("FORCE-PAUSES the game while open", menu)
        self.assertNotIn("FORCE-PAUSES", work)


def _icon(x, y, size=180, tid="ui-element:1:0:9"):
    """A storyteller portrait: an image button with no label at all."""
    return {"label": None, "actionable": True, "kind": "icon_button",
            "screenRect": {"x": x, "y": y, "width": size, "height": size},
            "targetId": tid}


def _label(text, x, y, w=200, h=24):
    return {"label": text, "actionable": False, "kind": "label",
            "screenRect": {"x": x, "y": y, "width": w, "height": h},
            "targetId": "ui-element:1:0:%d" % (x + y)}


def _wrap(els, label="Choose your storyteller"):
    return {"captureId": 7, "surfaces": [
        {"surfaceTargetId": "ui-surface:1:0", "label": label,
         "type": "RimWorld.Page_SelectStoryteller", "surfaceKind": "window",
         "elementCount": len(els), "elements": els,
         "actionableElementCount": sum(1 for e in els if e["actionable"]),
         "screenRect": {"x": 0, "y": 0, "width": 4096, "height": 2160}}]}


class CompositeRowTests(unittest.TestCase):
    """`--probe "Phoebe Chillax Community builder"` found 0 labels for a string
    ui.py had listed as clickable seconds earlier: the row is a merge of two
    separate draws and no single label carries it."""

    def _screen(self):
        btn = {"label": None, "actionable": True, "kind": "button",
               "screenRect": {"x": 100, "y": 400, "width": 300, "height": 40},
               "targetId": "ui-element:1:0:42"}
        return _wrap([btn,
                      _label("Phoebe Chillax", 100, 400, w=280),
                      _label("Community builder", 500, 402, w=260)])

    def test_the_read_prints_the_merge_and_says_which_half_is_the_button(self):
        text = ui.render(ui.slim(payload=self._screen()))
        self.assertIn("Phoebe Chillax  Community builder", text)
        self.assertIn("MERGED 1 of 1 rows put a button and a separate label", text)
        self.assertIn("The button half of each: 'Phoebe Chillax'", text)

    def test_the_composite_as_printed_clicks_the_row(self):
        got = ui.find("Phoebe Chillax  Community builder", payload=self._screen())
        self.assertEqual("ui-element:1:0:42", got)

    def test_the_button_half_on_its_own_clicks_it_too(self):
        self.assertEqual("ui-element:1:0:42",
                         ui.find("Phoebe Chillax", payload=self._screen()))

    def test_probe_explains_the_merge_instead_of_saying_zero_labels(self):
        with mock.patch.object(ui.rim, "game", return_value=self._screen()):
            text = ui.probe("Phoebe Chillax  Community builder")
        self.assertIn("0 label(s) match", text)
        self.assertIn("built from 2 separate text draws", text)
        self.assertIn("'Community builder'", text)
        self.assertIn("find() would return: 'ui-element:1:0:42'", text)


class TextFreeTests(unittest.TestCase):
    """ui.py could not click any text-free icon button, and the storyteller
    portraits are all three of them."""

    def _screen(self):
        return _wrap([_icon(200, 300, tid="ui-element:1:0:1"),
                      _icon(600, 300, tid="ui-element:1:0:2"),
                      _icon(1000, 300, tid="ui-element:1:0:3")])

    def test_the_read_numbers_them_and_prints_their_rectangles(self):
        text = ui.render(ui.slim(payload=self._screen()))
        self.assertIn("click --index N", text)
        self.assertIn("[0] icon_button    x=200 y=300 w=180 h=180  centre 290,390",
                      text)
        self.assertIn("[2] icon_button", text)

    def test_click_index_presses_the_one_the_read_numbered(self):
        clicked = []

        def game(tool, args=None, strict=True):
            if tool == "rimworld/click_ui_target":
                clicked.append(args["targetId"])
                return {"success": True}
            return self._screen()

        with mock.patch.object(ui.rim, "game", side_effect=game), \
             mock.patch.object(ui.time, "sleep"):
            got = ui.click_index(1)
        self.assertEqual(["ui-element:1:0:2"], clicked)
        self.assertEqual([600, 300, 180, 180], got.target["rect"])

    def test_an_index_off_the_end_clicks_nothing_and_says_the_range(self):
        with mock.patch.object(ui.rim, "game", return_value=self._screen()):
            with self.assertRaises(LookupError) as raised:
                ui.click_index(9)
        msg = str(raised.exception)
        self.assertIn("NOTHING WAS CLICKED", msg)
        self.assertIn("numbered 0-2", msg)

    def test_click_rect_presses_the_element_at_that_rectangle(self):
        clicked = []

        def game(tool, args=None, strict=True):
            if tool == "rimworld/click_ui_target":
                clicked.append(args["targetId"])
                return {"success": True}
            return self._screen()

        with mock.patch.object(ui.rim, "game", side_effect=game), \
             mock.patch.object(ui.time, "sleep"):
            ui.click_rect(1000, 300, 180, 180)
        self.assertEqual(["ui-element:1:0:3"], clicked)

    def test_two_numbers_are_a_point_and_take_the_smallest_element_over_it(self):
        clicked = []

        def game(tool, args=None, strict=True):
            if tool == "rimworld/click_ui_target":
                clicked.append(args["targetId"])
                return {"success": True}
            return self._screen()

        with mock.patch.object(ui.rim, "game", side_effect=game), \
             mock.patch.object(ui.time, "sleep"):
            ui.click_rect(290, 390)
        self.assertEqual(["ui-element:1:0:1"], clicked)

    def test_a_rect_with_nothing_on_it_names_the_real_mouse(self):
        with mock.patch.object(ui.rim, "game", return_value=self._screen()):
            with self.assertRaises(LookupError) as raised:
                ui.click_rect(4000, 2000, 10, 10)
        msg = str(raised.exception)
        self.assertIn("NOTHING WAS CLICKED", msg)
        self.assertIn("python click.py 4000 2000", msg)

    def test_a_failed_text_click_names_the_text_free_route(self):
        with mock.patch.object(ui.rim, "game", return_value=self._screen()):
            with self.assertRaises(LookupError) as raised:
                ui.click("Phoebe Chillax")
        msg = str(raised.exception)
        self.assertIn("carry NO text", msg)
        self.assertIn("--index N", msg)
        self.assertIn("python click.py 290 390", msg)

    def test_the_cli_takes_index_as_a_flag_with_a_value(self):
        clicked = []

        def game(tool, args=None, strict=True):
            if tool == "rimworld/click_ui_target":
                clicked.append(args["targetId"])
                return {"success": True}
            return self._screen()

        with mock.patch.object(ui.sys, "argv", ["ui.py", "click", "--index", "2"]), \
             mock.patch.object(ui.rim, "init"), \
             mock.patch.object(ui.rim, "game", side_effect=game), \
             mock.patch.object(ui.time, "sleep"), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(0, ui.main())
        self.assertEqual(["ui-element:1:0:3"], clicked)
        self.assertIn("clicked the icon_button at x=1000", out.getvalue())

    def test_the_cli_takes_a_rect(self):
        clicked = []

        def game(tool, args=None, strict=True):
            if tool == "rimworld/click_ui_target":
                clicked.append(args["targetId"])
                return {"success": True}
            return self._screen()

        with mock.patch.object(ui.sys, "argv",
                               ["ui.py", "click", "--rect", "600,300,180,180"]), \
             mock.patch.object(ui.rim, "init"), \
             mock.patch.object(ui.rim, "game", side_effect=game), \
             mock.patch.object(ui.time, "sleep"), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(0, ui.main())
        self.assertEqual(["ui-element:1:0:2"], clicked)


class DialogListingTests(unittest.TestCase):
    """A pawn-naming box has a label and its own length cap; neither showed."""

    def _show(self, reply):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            dialog.show(reply)
        return out.getvalue()

    def test_each_box_prints_its_label_and_its_cap(self):
        text = self._show({"success": True, "window": "Verse.Dialog_NamePawn",
                           "fields": [{"name": "names[1].current",
                                       "label": "NickName", "maxLength": 12,
                                       "before": "Finn"}]})
        self.assertIn("names[1].current", text)
        self.assertIn("[NickName, max 12 chars]", text)
        self.assertIn("Finn", text)

    def test_a_box_with_no_cap_prints_no_brackets(self):
        text = self._show({"success": True, "window": "RimWorld.Dialog_GiveName",
                           "fields": [{"name": "curName", "label": None,
                                       "maxLength": None, "before": ""}]})
        self.assertNotIn("[", text)
        self.assertIn("(empty)", text)

    def test_a_window_with_no_box_says_what_the_bridge_said(self):
        text = self._show({"success": True, "window": "RimWorld.Dialog_Trade",
                           "fields": [],
                           "error": "RimWorld.Dialog_Trade declares no writable"
                                    " string field"})
        self.assertIn("no string field -- nothing to type into", text)
        self.assertIn("declares no writable string field", text)


class RadioTests(unittest.TestCase):
    """Difficulty names merge into the adjacent row and which option is SELECTED
    is not readable from text at all."""

    def _screen(self):
        radio = {"label": "Community builder", "actionable": True,
                 "kind": "radio_button", "isChecked": None,
                 "screenRect": {"x": 100, "y": 800, "width": 300, "height": 30},
                 "targetId": "ui-element:1:0:7"}
        return _wrap([radio])

    def test_a_radio_row_reads_unknown_rather_than_unchecked(self):
        text = ui.render(ui.slim(payload=self._screen()))
        self.assertIn("[?] Community builder", text)

    def test_the_read_says_the_payload_carries_no_chosen_flag(self):
        text = ui.render(ui.slim(payload=self._screen()))
        self.assertIn("no chosen flag", text)
        self.assertIn("see.py", text)

    def test_a_reported_selection_is_printed_as_selected(self):
        screen = self._screen()
        screen["surfaces"][0]["elements"][0]["isChecked"] = True
        text = ui.render(ui.slim(payload=screen))
        self.assertIn("[x] Community builder", text)
        self.assertNotIn("no chosen flag", text)



def _gizmo_screen(label="Deploy turret"):
    """The gizmo bar with one 75x75 command on it, as get_ui_layout draws it.

    The button is the patched `Widgets.ButtonInvisible` and the label is a
    separate `Widgets.Label` drawn INSIDE it, which is why find() pairs them.
    """
    button = {"label": None, "actionable": True, "kind": "button",
              "screenRect": {"x": 3000, "y": 1900, "width": 75, "height": 75},
              "targetId": "ui-element:2:0:4"}
    text = {"label": label, "actionable": False, "kind": "label",
            "screenRect": {"x": 3000, "y": 1955, "width": 75, "height": 20},
            "targetId": "ui-element:2:0:5"}
    return {"captureId": 9, "surfaces": [
        {"surfaceTargetId": "selection-gizmos", "surfaceKind": "selection_gizmos",
         "label": "Selected gizmos", "type": "Verse.GizmoGridDrawer",
         "elementCount": 2, "actionableElementCount": 1,
         "elements": [button, text],
         "screenRect": {"x": 0, "y": 0, "width": 4096, "height": 2160}}]}


def _status(active, source=None, caster=None, field=True):
    ui_block = {"modalOpen": False, "windows": [], "windowCount": 0}
    if field:
        ui_block["targeter"] = {"active": active, "source": source,
                                "caster": caster}
    return {"success": True, "ui": ui_block}


class TargeterReportingTests(unittest.TestCase):
    """2026-09-08, Threadneedle: `ui.py click "Deploy turret"` said "success"
    and "nothing opened or closed" fourteen turns running. The click had worked
    every time -- a targeter is not a Window, so nothing on either side of the
    click could see it."""

    def _run(self, argv, targeter, screen=None, clicks=None, presses=None):
        screen = screen if screen is not None else _gizmo_screen()
        clicks = [] if clicks is None else clicks
        presses = [] if presses is None else presses

        calls = self.calls = []

        def game(tool, args=None, strict=True):
            calls.append(tool)
            if tool == "home/status":
                return targeter
            if tool in ("rimworld/click_ui_target", "rimworld/clear_selection"):
                return {"success": True}
            return screen

        fake = mock.Mock()
        fake.click.side_effect = lambda x, y: clicks.append((x, y))
        fake.key.side_effect = lambda name: presses.append(name)
        with mock.patch.object(ui.sys, "argv", ["ui.py"] + argv), \
             mock.patch.object(ui.rim, "init"), \
             mock.patch.object(ui.rim, "game", side_effect=game), \
             mock.patch.object(ui, "mouse", return_value=fake), \
             mock.patch.object(ui.time, "sleep"), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = ui.main()
        return code, out.getvalue()

    def test_an_open_targeter_is_reported_instead_of_nothing_opened_or_closed(self):
        code, text = self._run(["click", "Deploy turret"],
                               _status(True, "Deploy turret", "Thing_Human618"))
        self.assertEqual(0, code)
        self.assertIn("TARGETER OPEN -- Deploy turret", text)
        self.assertIn("order.py deploy", text)
        self.assertIn("a raw map click is SWALLOWED", text)
        self.assertIn("ui.py targeter --cancel", text)
        self.assertNotIn("nothing opened or closed --", text)

    def test_a_gizmo_is_clicked_with_the_real_mouse_at_its_own_rectangle(self):
        # The bridge click fires the gizmo but never moves the OS cursor, and a
        # targeter draws its ghost at the cursor. One click, not two.
        clicks = []
        self._run(["click", "Deploy turret"], _status(True, "Deploy turret"),
                  clicks=clicks)
        self.assertEqual([(3038, 1938)], clicks)

    def test_bridge_forces_the_old_path_and_pixel_forces_the_new_one(self):
        clicks = []
        self._run(["click", "Deploy turret", "--bridge"],
                  _status(True, "Deploy turret"), clicks=clicks)
        self.assertEqual([], clicks)
        clicks = []
        self._run(["click", "Community builder", "--pixel"], _status(False),
                  screen=_wrap([{"label": "Community builder", "actionable": True,
                                 "kind": "button", "targetId": "ui-element:1:0:7",
                                 "screenRect": {"x": 100, "y": 800,
                                                "width": 300, "height": 30}}]),
                  clicks=clicks)
        self.assertEqual([(250, 815)], clicks)

    def test_a_click_that_changed_nothing_says_the_targeter_was_checked_too(self):
        code, text = self._run(["click", "Deploy turret"], _status(False))
        self.assertIn("nothing opened or closed AND no targeter", text)
        self.assertEqual(0, code)

    def test_an_old_dll_says_the_field_is_missing_rather_than_no_targeter(self):
        code, text = self._run(["click", "Deploy turret"],
                               _status(False, field=False))
        self.assertNotIn("AND no targeter", text)
        self.assertIn("the targeter was not read", text)
        self.assertIn("INSTALL.md", text)

    def test_targeter_prints_the_state(self):
        code, text = self._run(["targeter"],
                               _status(True, "Deploy turret", "Thing_Human618"))
        self.assertEqual(0, code)
        self.assertIn("targeter: OPEN -- Deploy turret", text)
        self.assertIn("caster Thing_Human618", text)
        self.assertIn("order.py deploy", text)
        self.assertIn("swallowed", text)
        code, text = self._run(["targeter"], _status(False))
        self.assertEqual(0, code)
        self.assertIn("targeter: closed", text)

    def _cancel(self, states):
        """`ui.py targeter --cancel` against a scripted series of reads."""
        seen, presses = [], []
        queue = list(states)

        def game(tool, args=None, strict=True):
            seen.append(tool)
            if tool == "home/status":
                return queue.pop(0) if queue else _status(False)
            return {"success": True}

        fake = mock.Mock()
        fake.key.side_effect = lambda name: presses.append(name)
        with mock.patch.object(ui.sys, "argv", ["ui.py", "targeter", "--cancel"]), \
             mock.patch.object(ui.rim, "init"), \
             mock.patch.object(ui.rim, "game", side_effect=game), \
             mock.patch.object(ui, "mouse", return_value=fake), \
             mock.patch.object(ui.time, "sleep"), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = ui.main()
        return code, out.getvalue(), seen, presses

    def test_cancel_clears_the_selection_and_that_is_what_closes_it(self):
        """Live 2026-09-11: a synthetic Escape and a synthetic right-click both
        left the targeter standing. Deselecting the caster closed it, because
        Targeter.ConfirmStillValid calls StopTargeting on an unselected caster."""
        code, text, seen, presses = self._cancel(
            [_status(True, "Deploy turret", "Thing_Human618"), _status(False)])
        self.assertEqual(0, code)
        self.assertIn("rimworld/clear_selection", seen)
        self.assertEqual([], presses)   # the key is a fallback, not the route
        self.assertIn("cancelled the targeter (Deploy turret)", text)
        self.assertIn("ConfirmStillValid", text)

    def test_a_cancel_that_worked_says_the_selection_is_gone(self):
        code, text, _, _ = self._cancel(
            [_status(True, "Deploy turret", "Thing_Human618"), _status(False)])
        self.assertIn("selection is now EMPTY", text)
        self.assertIn("Thing_Human618", text)

    def test_escape_is_the_fallback_when_deselecting_did_not_close_it(self):
        # A Command_Target with no caster has nothing to deselect.
        code, text, seen, presses = self._cancel(
            [_status(True, "Deploy turret"), _status(True, "Deploy turret"),
             _status(False)])
        self.assertEqual(0, code)
        self.assertLess(seen.index("rimworld/clear_selection"),
                        len(seen) - 1)
        self.assertEqual(["esc"], presses)
        self.assertIn("the selection clear did not take but Escape did", text)

    def test_neither_route_taking_is_reported_as_neither_route_taking(self):
        code, text, _, presses = self._cancel(
            [_status(True, "Deploy turret")] * 3)
        self.assertEqual(1, code)
        self.assertEqual(["esc"], presses)
        self.assertIn("STILL open", text)
        self.assertIn("a synthetic Escape does not reach", text)
        self.assertIn("HUMAN keypress", text)

    def test_cancel_touches_nothing_when_no_targeter_is_open(self):
        # Escape with nothing targeting opens the MENU, which force-pauses, and
        # clearing the selection costs a selection for no reason.
        code, text, seen, presses = self._cancel([_status(False)])
        self.assertEqual([], presses)
        self.assertNotIn("rimworld/clear_selection", seen)
        self.assertIn("NOTHING WAS CLEARED OR PRESSED", text)
        self.assertEqual(0, code)

    def test_cancel_refuses_when_the_targeter_cannot_be_read_at_all(self):
        code, text, seen, presses = self._cancel([_status(False, field=False)])
        self.assertEqual(1, code)
        self.assertEqual([], presses)
        self.assertNotIn("rimworld/clear_selection", seen)
        self.assertIn("NOTHING WAS CLEARED OR PRESSED", text)
        self.assertIn("game MENU", text)


class ClickedShapeTests(unittest.TestCase):
    def test_the_old_positional_constructor_still_works(self):
        got = ui.Clicked(True, ["a window"], [])
        self.assertTrue(got.ok)
        self.assertEqual(1, int(got))
        self.assertEqual("bridge", got.via)
        self.assertIsNone(got.targeter)
        self.assertIn("opened a window", got.line())

    def test_an_unverified_click_says_so_before_anything_else(self):
        self.assertEqual("not read back", ui.Clicked(True, verified=False).line())

    def test_a_window_change_and_a_targeter_are_both_printed(self):
        got = ui.Clicked(True, ["a window"], [],
                         targeter={"active": True, "source": "Deploy turret"})
        self.assertIn("opened a window", got.line())
        self.assertIn("TARGETER OPEN -- Deploy turret", got.line())
        self.assertIn("order.py deploy", got.line())


class GizmoSurfaceTests(unittest.TestCase):
    def test_both_spellings_of_the_gizmo_surface_are_recognised(self):
        self.assertTrue(ui._is_gizmo({"surfaceKind": "selection_gizmos"}))
        self.assertTrue(ui._is_gizmo({"surfaceTargetId": "selection-gizmos"}))
        self.assertTrue(ui._is_gizmo({"surfaceTargetId": "selection-gizmos:3"}))
        self.assertFalse(ui._is_gizmo({"surfaceKind": "window"}))
        self.assertFalse(ui._is_gizmo(None))

    def test_the_element_and_its_surface_are_found_by_target_id(self):
        raw = _gizmo_screen()
        el, home = ui._locate(raw, "ui-element:2:0:4")
        self.assertEqual("button", el["kind"])
        self.assertTrue(ui._is_gizmo(home))
        self.assertEqual((None, None), ui._locate(raw, "ui-element:9:9:9"))

    def test_an_element_with_no_rectangle_has_no_pixel_to_click(self):
        self.assertIsNone(ui._centre({"screenRect": None}))
        self.assertIsNone(ui._centre({"screenRect": {"x": 1, "y": 2,
                                                     "width": 0, "height": 0}}))


class GizmoFocusRefusalTests(unittest.TestCase):
    """click.py now REFUSES rather than clicking a window it cannot confirm is
    in front (2026-09-11). On the gizmo bar that refusal must not fall back to
    the bridge: a bridge click fires the gizmo without moving the OS cursor, and
    a targeting gizmo then opens pointed wherever the cursor was left -- the
    2026-09-08 trap this whole path exists to avoid."""

    def test_a_focus_refusal_on_a_gizmo_sends_no_bridge_click_either(self):
        class FocusRefused(RuntimeError):
            pass

        calls = []

        def game(tool, args=None, strict=True):
            calls.append(tool)
            if tool == "home/status":
                return _status(False)
            if tool == "rimworld/click_ui_target":
                return {"success": True}
            return _gizmo_screen()

        fake = mock.Mock()
        fake.click.side_effect = FocusRefused(
            "the RimWorld window did not come to the foreground within 1.8 s")
        with mock.patch.object(ui.sys, "argv", ["ui.py", "click", "Deploy turret"]), \
             mock.patch.object(ui.rim, "init"), \
             mock.patch.object(ui.rim, "game", side_effect=game), \
             mock.patch.object(ui, "mouse", return_value=fake), \
             mock.patch.object(ui.time, "sleep"), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            code = ui.main()
        text = out.getvalue()
        self.assertEqual(1, code)
        self.assertNotIn("rimworld/click_ui_target", calls)
        self.assertIn("NOTHING WAS CLICKED", text)
        self.assertIn("did not come to the foreground", text)

if __name__ == "__main__":
    unittest.main()


class UiToPixelScaleTests(unittest.TestCase):
    """Live 2026-09-12, fullscreen 4096x2160: `ui.py click "Deploy turret"`
    reported `the REAL MOUSE clicked it` and `closed RimWorld.MainTabWindow_
    Inspect`. The gizmo's own rect is x=606 y=740 w=75 h=75 in RimWorld's UI
    space (1638.4x864 at UI scale 2.5); `_centre` handed 643,777 to click.py as
    if they were OS pixels, the mouse clicked bare map, and the selection was
    cleared. 1609,1944 -- the same centre times 2.5 -- opened the targeter."""

    GIZMO_SURFACE = {"surfaceTargetId": "selection-gizmos",
                     "surfaceKind": "selection_gizmos",
                     "rect": {"x": 0, "y": 0, "width": 1638, "height": 864},
                     "screenRect": {"x": 0, "y": 0,
                                    "width": 1638, "height": 864}}
    GIZMO = {"screenRect": {"x": 606, "y": 740, "width": 75, "height": 75}}

    def test_the_scale_is_measured_off_the_full_screen_gizmo_surface(self):
        self.assertAlmostEqual(2.5, ui._ui_scale(self.GIZMO_SURFACE), places=2)

    def test_a_gizmo_centre_is_converted_to_os_pixels(self):
        scale = ui._ui_scale(self.GIZMO_SURFACE)
        self.assertEqual((1609, 1944), ui._centre(self.GIZMO, scale))

    def test_an_unscaled_centre_is_the_bug_this_fixes(self):
        """The old behaviour, kept as the thing the fix is measured against."""
        self.assertEqual((644, 778), ui._centre(self.GIZMO))

    def test_a_one_to_one_ui_needs_no_conversion(self):
        surface = {"screenRect": {"x": 0, "y": 0,
                                  "width": ui.SCREEN[0], "height": ui.SCREEN[1]}}
        self.assertAlmostEqual(1.0, ui._ui_scale(surface), places=6)
        self.assertEqual((644, 778), ui._centre(self.GIZMO,
                                                ui._ui_scale(surface)))

    def test_a_surface_that_is_not_the_whole_screen_yields_no_scale(self):
        """A window's rect is its own, not the screen's; guessing a scale off
        it would put the click somewhere arbitrary."""
        self.assertIsNone(ui._ui_scale(
            {"screenRect": {"x": 0, "y": 634, "width": 432, "height": 230}}))
        self.assertIsNone(ui._ui_scale(
            {"screenRect": {"x": 0, "y": 0, "width": 432, "height": 230}}))
        self.assertIsNone(ui._ui_scale(None))
        self.assertIsNone(ui._ui_scale({}))

    def test_a_gizmo_click_with_no_measurable_scale_clicks_nothing(self):
        """Falling back to the bridge is wrong here (the cursor never moves and
        a targeter opens where it was left); clicking unscaled is worse."""
        home = {"surfaceTargetId": "selection-gizmos",
                "surfaceKind": "selection_gizmos",
                "screenRect": {"x": 0, "y": 0, "width": 432, "height": 230}}
        with mock.patch.object(ui, "mouse") as m,              mock.patch.object(ui.rim, "game") as g:
            got = ui._fire("tid", {}, None, 0, False,
                           element=self.GIZMO, home=home)
        self.assertFalse(got.ok)
        self.assertEqual("none", got.via)
        self.assertIn("NOTHING WAS CLICKED", got.note)
        m.assert_not_called()
        g.assert_not_called()
