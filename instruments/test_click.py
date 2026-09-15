"""Mock-only regressions for click.py and for winctl's verified focus.

Both of the 2026-09-11 bugs live here. The first is that `winctl.focus` asked
for the foreground and slept 0.2 s without ever reading it back, so the first
real-mouse click was spent activating the window. The second is that a real map
click with a targeter open was swallowed -- `Targeter.ProcessInputEvents`
answers a false `ValidateTarget` with `Event.current.Use(); return;`, and a
target computed at a STALE mouse position is exactly that false.

Nothing here touches a window: winctl's Win32 calls are mocked out.
"""
import io
import sys
import unittest
from unittest import mock

import click
import winctl


HWND = 42
WINDOW = [{"class": "UnityWndClass", "title": "RimWorld by Ludeon Studios",
           "hwnd": HWND}]


def _run(argv, focus=(True, "already"), to_px=(2044, 1122), targeter=None,
         moves=None, buttons=None, presses=None):
    """click.main(argv) with every Win32 call and every bridge read mocked.

    `targeter` is the list of states `read_targeter` hands back, in order.
    `focus` is what `winctl.focus_verified` returns, or an exception to raise.
    """
    moves = moves if moves is not None else []
    buttons = buttons if buttons is not None else []
    presses = presses if presses is not None else []
    reads = list(targeter or [])
    seen_reads = []

    def read():
        seen_reads.append(1)
        return reads.pop(0) if reads else (None, "no read scripted")

    fv = (mock.Mock(side_effect=focus) if isinstance(focus, Exception)
          else mock.Mock(return_value=focus))
    px = (mock.Mock(side_effect=to_px) if isinstance(to_px, Exception)
          else mock.Mock(return_value=to_px))
    import cell2px
    with mock.patch.object(click.winctl, "list_windows", return_value=WINDOW), \
         mock.patch.object(click.winctl, "focus_verified", fv), \
         mock.patch.object(click.winctl, "move_screen",
                           side_effect=lambda x, y: moves.append((x, y))), \
         mock.patch.object(click.winctl, "button",
                           side_effect=lambda d, right=False: buttons.append(
                               ("down" if d else "up", right))), \
         mock.patch.object(click.winctl, "press",
                           side_effect=lambda n: presses.append(n)), \
         mock.patch.object(cell2px, "to_px", px), \
         mock.patch.object(click, "read_targeter", side_effect=read), \
         mock.patch.object(click.time, "sleep"), \
         mock.patch("sys.stdout", new_callable=io.StringIO) as out:
        code = click.main(argv)
    return code, out.getvalue(), {"moves": moves, "buttons": buttons,
                                  "presses": presses, "focus": fv,
                                  "reads": len(seen_reads)}


def _state(active, source="Deploy turret", caster="Thing_Human618"):
    return ({"active": active, "source": source, "caster": caster}, None)


# ------------------------------------------------------------------ BUG 1

class FocusLadderTests(unittest.TestCase):
    """`winctl.focus_verified`: ask, then READ GetForegroundWindow back."""

    def _ladder(self, sticks_at):
        """Run the ladder; `sticks_at` is which SetForegroundWindow call takes."""
        state = {"tries": 0, "fg": 999}
        log = []

        def set_fg(h):
            state["tries"] += 1
            log.append("SetForegroundWindow")
            if sticks_at == state["tries"]:
                state["fg"] = HWND
            return 1

        u = mock.Mock()
        u.SetForegroundWindow.side_effect = set_fg
        u.IsIconic.return_value = 0
        u.AttachThreadInput.side_effect = lambda a, b, on: log.append(
            "Attach %s" % ("on" if on else "off")) or 1
        u.AllowSetForegroundWindow.side_effect = lambda w: log.append("Allow")
        u.BringWindowToTop.side_effect = lambda h: log.append("BringToTop")
        with mock.patch.object(winctl, "user32", u), \
             mock.patch.object(winctl, "foreground_hwnd",
                               side_effect=lambda: state["fg"]), \
             mock.patch.object(winctl, "_window_thread", return_value=7), \
             mock.patch.object(winctl, "_wait_foreground",
                               side_effect=lambda h, s: state["fg"] == int(h)), \
             mock.patch.object(winctl, "_send_key",
                               side_effect=lambda *a, **k: log.append("ALT")):
            ok, how = winctl.focus_verified(HWND, timeout=0.1)
        return ok, how, log

    def test_a_window_already_in_front_is_not_touched(self):
        with mock.patch.object(winctl, "user32") as u, \
             mock.patch.object(winctl, "foreground_hwnd", return_value=HWND):
            self.assertEqual((True, "already"), winctl.focus_verified(HWND))
        u.SetForegroundWindow.assert_not_called()

    def test_the_plain_ask_is_confirmed_not_assumed(self):
        ok, how, log = self._ladder(sticks_at=1)
        self.assertEqual((True, "SetForegroundWindow"), (ok, how))
        self.assertEqual(["SetForegroundWindow"], log)

    def test_a_refused_ask_falls_through_to_attachthreadinput(self):
        ok, how, log = self._ladder(sticks_at=2)
        self.assertEqual((True, "AttachThreadInput"), (ok, how))
        self.assertIn("Attach on", log)
        self.assertIn("Attach off", log)   # always detached again
        self.assertNotIn("ALT", log)       # not reached

    def test_the_alt_tap_is_the_last_rung(self):
        ok, how, log = self._ladder(sticks_at=3)
        self.assertEqual((True, "an ALT tap"), (ok, how))
        self.assertEqual("ALT", log[log.index("Attach off") + 1])

    def test_every_rung_failing_is_reported_as_failure_not_as_success(self):
        ok, how, log = self._ladder(sticks_at=None)
        self.assertFalse(ok)
        self.assertIn("AttachThreadInput", how)
        self.assertIn("ALT tap", how)
        self.assertIn("Attach off", log)

    def test_the_poll_gives_the_window_time_to_arrive(self):
        clock = {"t": 0.0}
        fg = {"v": 0}

        def tick(_):
            clock["t"] += 0.1
            if clock["t"] > 0.25:
                fg["v"] = HWND

        fake = mock.Mock()
        fake.time.side_effect = lambda: clock["t"]
        fake.sleep.side_effect = tick
        with mock.patch.object(winctl, "time", fake), \
             mock.patch.object(winctl, "foreground_hwnd",
                               side_effect=lambda: fg["v"]):
            self.assertTrue(winctl._wait_foreground(HWND, 1.0))
            fg["v"] = 0
            clock["t"] = 0.0
            self.assertFalse(winctl._wait_foreground(HWND, 0.05))


class ClickFocusTests(unittest.TestCase):
    def test_focus_already_held_is_said_and_the_click_goes_out(self):
        code, text, got = _run(["3412", "1902"])
        self.assertEqual(0, code)
        self.assertIn("focus: RimWorld already held the foreground.", text)
        self.assertIn("clicked 3412,1902", text)
        self.assertEqual([("down", False), ("up", False)], got["buttons"])

    def test_focus_acquired_says_how_and_that_no_click_was_spent_on_it(self):
        code, text, got = _run(["3412", "1902"], focus=(True, "AttachThreadInput"))
        self.assertEqual(0, code)
        self.assertIn("acquired via AttachThreadInput", text)
        self.assertIn("No focusing click was sent", text)
        # One click went out, not two: the focusing click is not a thing here.
        self.assertEqual([("down", False), ("up", False)], got["buttons"])

    def test_focus_that_cannot_be_confirmed_sends_nothing_and_exits_nonzero(self):
        code, text, got = _run(["3412", "1902"], focus=(False, "everything"))
        self.assertEqual(1, code)
        self.assertIn("click.py REFUSED", text)
        self.assertIn("did not come to the foreground", text)
        self.assertIn("NOTHING WAS CLICKED OR PRESSED", text)
        self.assertIn("order.py deploy", text)   # the refusal names the next route
        self.assertEqual([], got["moves"])
        self.assertEqual([], got["buttons"])

    def test_a_key_is_not_pressed_into_whatever_window_is_in_front(self):
        code, text, got = _run(["--key", "esc"], focus=(False, "everything"))
        self.assertEqual(1, code)
        self.assertEqual([], got["presses"])
        self.assertIn("NOTHING WAS CLICKED OR PRESSED", text)

    def test_a_key_with_focus_held_is_pressed(self):
        code, text, got = _run(["--key", "esc"])
        self.assertEqual(0, code)
        self.assertEqual(["esc"], got["presses"])
        self.assertIn("pressed esc", text)

    def test_no_focus_skips_the_check_and_says_so(self):
        code, text, got = _run(["3412", "1902", "--no-focus"])
        self.assertEqual(0, code)
        self.assertIn("NOT CHECKED", text)
        got["focus"].assert_not_called()
        self.assertEqual([("down", False), ("up", False)], got["buttons"])

    def test_the_cursor_lands_on_the_target_after_two_moves(self):
        # One SendInput move to the point the cursor already holds produces no
        # WM_MOUSEMOVE, and Unity reads the target cell from the last position
        # it was given. Approach one pixel off, then land exactly.
        code, text, got = _run(["3412", "1902"])
        self.assertEqual([(3412, 1903), (3412, 1902)], got["moves"])


# ------------------------------------------------------------------ BUG 2

class CellClickTargeterTests(unittest.TestCase):
    """A map click with a targeter open is reported from a read-back."""

    def test_a_targeter_that_closed_is_reported_as_closed(self):
        code, text, got = _run(["--cell", "139", "118"],
                               targeter=[_state(True), _state(False, None)])
        self.assertEqual(0, code)
        self.assertIn("targeter before: OPEN -- Deploy turret", text)
        self.assertIn("clicked cell 139,118 at pixel 2044,1122", text)
        self.assertIn("TARGETER CLOSED", text)
        # Closed is not "the job started"; the line says which it is not.
        self.assertIn("line of sight", text)
        self.assertEqual([(2044, 1123), (2044, 1122)], got["moves"])
        self.assertEqual([("down", False), ("up", False)], got["buttons"])

    def test_a_targeter_still_open_is_the_swallowed_click_and_names_the_route(self):
        code, text, got = _run(["--cell", "139", "118"],
                               targeter=[_state(True), _state(True)])
        self.assertEqual(1, code)
        self.assertIn("TARGETER STILL OPEN -- Deploy turret", text)
        self.assertIn("the click did not land", text)
        self.assertIn("order.py deploy Thing_Human618 139 118 --do", text)

    def test_a_targeter_that_cannot_be_read_back_says_so(self):
        code, text, got = _run(["--cell", "139", "118"],
                               targeter=[_state(True), (None, "no ui block")])
        self.assertEqual(1, code)
        self.assertIn("could not be read back", text)
        self.assertIn("python ui.py targeter", text)

    def test_no_targeter_open_means_no_second_read(self):
        code, text, got = _run(["--cell", "139", "118"],
                               targeter=[_state(False, None)])
        self.assertEqual(0, code)
        self.assertEqual(1, got["reads"])
        self.assertIn("ordinary map click", text)
        self.assertNotIn("TARGETER", text)

    def test_focus_is_taken_before_the_camera_is_read(self):
        # to_px settles and claims the camera; a focus refusal must land before
        # any of that, and before the bridge is asked anything.
        code, text, got = _run(["--cell", "139", "118"],
                               focus=(False, "everything"),
                               targeter=[_state(True)])
        self.assertEqual(1, code)
        self.assertEqual(0, got["reads"])
        self.assertEqual([], got["moves"])
        self.assertEqual([], got["buttons"])

    def test_an_off_screen_cell_refuses_instead_of_clicking_the_desktop(self):
        code, text, got = _run(["--cell", "5", "5"],
                               to_px=ValueError("cell 5,5 is OFF SCREEN: ..."),
                               targeter=[_state(True)])
        self.assertEqual(1, code)
        self.assertIn("click.py REFUSED", text)
        self.assertIn("OFF SCREEN", text)
        self.assertEqual([], got["buttons"])

    def test_a_right_click_cell_still_right_clicks(self):
        code, text, got = _run(["--cell", "139", "118", "--right"],
                               targeter=[_state(True), _state(False, None)])
        self.assertEqual(0, code)
        self.assertEqual([("down", True), ("up", True)], got["buttons"])


class Cell2PxBoundsTests(unittest.TestCase):
    def test_a_cell_inside_the_view_maps_to_a_pixel(self):
        import cell2px
        view = {"viewRect": {"minX": 100, "maxZ": 140, "width": 80, "height": 42}}
        with mock.patch.object(cell2px.camlock, "settle"), \
             mock.patch.object(cell2px.camlock, "claim"):
            px, py = cell2px.to_px(139, 118, cam=view)
        self.assertTrue(0 <= px < 4096 and 0 <= py < 2160)

    def test_a_cell_outside_the_view_is_refused_with_the_camera_command(self):
        import cell2px
        view = {"viewRect": {"minX": 100, "maxZ": 140, "width": 80, "height": 42}}
        with mock.patch.object(cell2px.camlock, "settle"), \
             mock.patch.object(cell2px.camlock, "claim"):
            with self.assertRaises(ValueError) as caught:
                cell2px.to_px(5, 5, cam=view)
        self.assertIn("OFF SCREEN", str(caught.exception))
        self.assertIn("NOTHING WAS CLICKED", str(caught.exception))
        self.assertIn("cam.py go 5 5", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
