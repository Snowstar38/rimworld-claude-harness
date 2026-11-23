import contextlib
import io
import unittest
from unittest import mock

import newgame


def target(top=None, entry=True):
    return {"uiState": {"programState": "Entry" if entry else "Playing",
                         "topWindowType": top}}


class NewGameTests(unittest.TestCase):
    def test_page_names_blind_main_menu(self):
        self.assertEqual("main-menu", newgame.page(target()))
        self.assertEqual("main-menu", newgame.page({"success": True, "targets": target()}))
        self.assertEqual("site", newgame.page(target(newgame.PAGES["site"])))

    def test_begin_uses_the_calibrated_click_only_on_main_menu(self):
        states = [target(), target(newgame.PAGES["scenario"])]
        with mock.patch.object(newgame, "screen", side_effect=states), \
             mock.patch.object(newgame.real_mouse, "click") as click, \
             contextlib.redirect_stdout(io.StringIO()):
            newgame.begin()
        click.assert_called_once_with(3103, 745)

    def test_blind_site_click_refuses_on_the_wrong_page(self):
        with mock.patch.object(newgame, "screen", return_value=target(newgame.PAGES["world"])), \
             mock.patch.object(newgame.real_mouse, "click") as click:
            with self.assertRaises(RuntimeError):
                newgame.site_random()
        click.assert_not_called()

    def test_storyteller_name_maps_to_verified_portrait_index(self):
        clicked = mock.Mock(ok=True)
        with mock.patch.object(newgame, "screen", return_value=target(newgame.PAGES["storyteller"])), \
             mock.patch.object(newgame.ui, "click_index", return_value=clicked) as index, \
             mock.patch.object(newgame, "text_click"), \
             mock.patch.object(newgame.ui, "slim", return_value={"surfaces": []}), \
             mock.patch.object(newgame.ui, "render", return_value="readback"), \
             contextlib.redirect_stdout(io.StringIO()):
            newgame.storyteller("Phoebe", "Strive to survive", "reload")
        index.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
