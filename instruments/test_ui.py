"""Mock-only regressions for ui.py command-line actions."""
import io
import unittest
from unittest import mock

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
        click.assert_called_once_with("OK", surface="Research")

    def test_unscoped_positional_click(self):
        with mock.patch.object(ui.sys, "argv", ["ui.py", "click", "View", "quest"]), \
             mock.patch.object(ui.rim, "init"), \
             mock.patch.object(ui, "click", return_value=ui.Clicked(True)) as click, \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(0, ui.main())
        click.assert_called_once_with("View quest", surface=None)


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

    def _game(self, calls):
        def game(tool, args=None, strict=True):
            calls.append((tool, args))
            if tool == "rimworld/list_main_tabs":
                return TABS
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


if __name__ == "__main__":
    unittest.main()
