"""Presentation regressions for entry-scene selection state."""

import unittest

import ui


def _element(kind, label, checked, y):
    return {
        "targetId": "ui-element:1:0:%d" % y,
        "kind": kind,
        "source": "test",
        "label": label,
        "actionable": True,
        "isChecked": checked,
        "screenRect": {"x": 10, "y": y, "width": 120, "height": 24},
    }


class EntrySelectionPresentationTests(unittest.TestCase):
    def test_radio_selection_renders_checked_and_unchecked(self):
        payload = {"captureId": 1, "surfaces": [{
            "surfaceTargetId": "ui-surface:1:0",
            "surfaceKind": "window",
            "type": "RimWorld.Page_SelectStoryteller",
            "label": "Choose storyteller",
            "screenRect": {"x": 0, "y": 0, "width": 800, "height": 600},
            "elements": [
                _element("radio_button", "Adventure story", True, 20),
                _element("radio_button", "Strive to survive", False, 50),
            ],
        }]}

        text = ui.render(ui.slim(payload=payload))

        self.assertIn("[x] Adventure story", text)
        self.assertIn("[ ] Strive to survive", text)
        self.assertNotIn("RADIO rows read [?]", text)

    def test_text_free_icon_selection_renders_checked(self):
        payload = {"captureId": 1, "surfaces": [{
            "surfaceTargetId": "ui-surface:1:0",
            "surfaceKind": "window",
            "type": "RimWorld.Page_SelectStoryteller",
            "label": "Choose storyteller",
            "screenRect": {"x": 0, "y": 0, "width": 800, "height": 600},
            "elements": [_element("icon_button", None, True, 20)],
        }]}

        text = ui.render(ui.slim(payload=payload))

        self.assertIn("icon_button    [x]", text)


if __name__ == "__main__":
    unittest.main()
