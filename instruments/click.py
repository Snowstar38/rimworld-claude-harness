"""Real mouse clicks on the RimWorld window, for the places the bridge can't
reach: the world globe, the Page_SelectStartingSite bottom bar (that page has
a zero-size window rect and draws its buttons in ExtraOnGUI, so get_ui_layout
returns an empty surface for it), and the gizmo bar.

Coordinates are screen pixels, and RimWorld here is fullscreen 4096x2160, so a
pixel in a bridge screenshot is the same pixel here.

  python click.py 3412 1902           # click a screen pixel
  python click.py 3412 1902 --right   # right-click it
  python click.py --cell 139 118      # click the CELL at 139,118 (via cell2px)
  python click.py --key esc           # tap a real key at the window
  --no-focus                          # skip the focus check (you focused it)

Every form prints ONE focus line first: whether RimWorld already held the
foreground, or how it was taken. If it cannot be confirmed, nothing is sent.

## Why the gizmo bar needs this

`rimworld/click_ui_target` synthesises a MouseDown/MouseUp inside the patched
`Widgets.ButtonInvisible` and forces its return to true, which is enough to make
a gizmo fire -- but it never moves the OS cursor. A gizmo that opens a TARGETER
(`Command_VerbTarget`, `Command_Target`: "Deploy turret" on a turret pack) then
draws its placement ghost wherever the real mouse happens to be sitting, because
`Targeter.CurrentTargetUnderMouse` reads `UI.MousePositionOnUIInverted`. The
targeter is open and the ghost is somewhere off the map. `ui.py` uses this
module for gizmos for that reason; see its `_fire`.

## Focus, and why no click is ever spent on it

Windows refuses `SetForegroundWindow` to a process that is not already the
foreground process: the call returns 0, flashes the taskbar button and changes
nothing. A python script run from a terminal is that process, so the old
`winctl.focus` + 0.2 s was a wish. `winctl.focus_verified` asks, polls
`GetForegroundWindow` back, and falls through AttachThreadInput and an ALT tap
before giving up; this module REFUSES and sends nothing if it still cannot see
the window in front.

**No "focusing" click is sent, deliberately.** Two reasons, and they are not
guesses. Win32: `DefWindowProc` answers `WM_MOUSEACTIVATE` with `MA_ACTIVATE`,
which activates the window and *keeps* the mouse message -- only
`MA_ACTIVATEANDEAT` discards it, and a Unity player window does not return it.
RimWorld: this window is 4096x2160 of map with the UI drawn over it, so there
is no harmless pixel. A left click on the map runs `Selector.SelectUnderMouse`
(it changes the selection, and `Targeter.ConfirmStillValid` then calls
`StopTargeting()` the moment the caster is deselected -- that is exactly how
`ui.py targeter --cancel` works), or, with a targeter open, it IS the target
click. A sacrificial click would destroy the thing it was meant to enable.
What is sent instead is a mouse MOVE, which only changes what is hovered.

## Why a --cell click moves twice

Unity builds `Input.mousePosition` from the mouse messages it receives and
samples it once per frame; `UI.MouseCell()`, which is what
`Targeter.CurrentTargetUnderMouse` reads, comes off that. A SendInput move to
the point the cursor already occupies generates no WM_MOUSEMOVE at all, and a
move that arrived before focus did can be dropped. So a cell click approaches
one pixel off, settles, lands exactly, settles again, and only then presses.

`Verse.Verb_LaunchProjectileStaticOneUse.ValidateTarget` (decompiled 1.6) is
`target.Cell.GetFirstBuilding(map) == null && target.Cell.Standable(map)`, and
`Targeter.ProcessInputEvents` answers a false from it with `Event.current.Use();
return;` -- the click is eaten and the targeter stays open. A STALE mouse
position produces exactly that: the target is computed at the cell under
wherever the cursor was parked. So `--cell` reads `home/status`'s `ui.targeter`
on both sides of the click and says which happened.
"""
import sys, time
sys.path.insert(0, r"C:\Home\tools\petz")
import winctl


FOCUS_TIMEOUT = 1.8
MOVE_SETTLE = 0.25       # Unity samples the cursor once a frame; give it some.
LAND_SETTLE = 0.12
HOLD = 0.06
AFTER = 0.25
NO_FOCUS_LINE = "focus: NOT CHECKED (--no-focus); input goes wherever it goes."


class FocusRefused(RuntimeError):
    """RimWorld would not come to the foreground, so nothing was sent."""


def rimworld_hwnd():
    for w in winctl.list_windows():
        if w["class"] == "UnityWndClass" and "RimWorld" in (w["title"] or ""):
            return w["hwnd"]
    raise SystemExit("RimWorld window not found")


def ensure_focus(hwnd=None, timeout=FOCUS_TIMEOUT):
    """Put RimWorld in front and confirm it. -> the one line to print.

    Raises `FocusRefused`, having sent no input at all, when the foreground
    window is still not RimWorld after the whole budget. A click must never be
    spent on focusing (see the module head), so the caller refuses too.
    """
    hwnd = rimworld_hwnd() if hwnd is None else hwnd
    ok, how = winctl.focus_verified(hwnd, timeout=timeout)
    if not ok:
        raise FocusRefused(
            "the RimWorld window did not come to the foreground within %.1f s"
            " (tried %s). NOTHING WAS CLICKED OR PRESSED. Click the game window"
            " once by hand and re-run, or take a route that needs no mouse:"
            " `python order.py deploy <pawn> <x> <z> --do` places, `python ui.py"
            " targeter --cancel` closes a targeter." % (timeout, how))
    if how == "already":
        return "focus: RimWorld already held the foreground."
    return ("focus: acquired via %s. No focusing click was sent -- every pixel"
            " of this window is map, where a left click changes the selection or"
            " is taken by an open targeter." % how)


def _focus_line(focus, hwnd=None):
    return ensure_focus(hwnd) if focus else NO_FOCUS_LINE


def _place(x, y):
    """Park the cursor at x,y with TWO moves, the second landing exactly on it.

    One move to the point the cursor already holds is no WM_MOUSEMOVE at all,
    and Unity reads the cell under the mouse from the position it last got.
    """
    winctl.move_screen(x, y + 1)
    time.sleep(MOVE_SETTLE)
    winctl.move_screen(x, y)
    time.sleep(LAND_SETTLE)


def click(x, y, right=False, focus=True, announce=True):
    """One real click at a screen pixel, with the foreground verified first."""
    line = _focus_line(focus)
    if announce:
        print(line)
    _place(x, y)
    winctl.button(True, right=right)
    time.sleep(HOLD)
    winctl.button(False, right=right)
    time.sleep(AFTER)


def key(name, focus=True, announce=True):
    """Tap one real key at the RimWorld window. Names are winctl.VK's.

    The bridge's `rimworld/press_cancel` is `WindowStack.Notify_PressedCancel()`
    and closes a WINDOW; it never reaches `Targeter.ProcessInputEvents`, which
    cancels on `KeyBindingDefOf.Cancel.KeyDownEvent`. So an open targeter can
    only be escaped by a real key (or a real right-click), which is this.
    """
    line = _focus_line(focus)
    if announce:
        print(line)
    winctl.press(name)
    time.sleep(AFTER)


def click_cell(x, z, right=False, focus=True, announce=True):
    """Click the map CELL at x,z with the real mouse. -> the pixel used.

    `cell2px.to_px` settles and claims the camera first. It aims at the tile on
    the ground plane, NOT at what is standing on it -- a pawn is drawn taller
    than their tile and is selected by a pixel several above this one, so never
    address a pawn this way (cell2px's own docstring, and BUGS 2026-09-08).

    Focus is taken BEFORE the viewRect is read, so nothing can move the camera
    between the pixel being computed and the click landing on it.
    """
    import cell2px
    line = _focus_line(focus)
    if announce:
        print(line)
    px, py = cell2px.to_px(x, z)
    click(px, py, right=right, focus=False, announce=False)
    return px, py


# ------------------------------------------------------------- the targeter

def read_targeter():
    """`home/status`'s ui.targeter, through ui.py's own reader. -> (state, why).

    One reader, one wording: `ui.py targeter` prints the same field.
    """
    try:
        import ui
    except Exception as e:
        return None, "ui.py did not import (%s: %s)" % (type(e).__name__, e)
    try:
        return ui.targeter_state()
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)


def before_line(state, why):
    """What was open before a cell click, said as what was read."""
    if state is None:
        return "targeter before: NOT READ -- %s" % (why or "no reason given")
    if not state.get("active"):
        return "targeter before: closed. This is an ordinary map click."
    return "targeter before: OPEN -- %s." % (state.get("source")
                                             or "unnamed targeting command")


def after_line(before, after, why, x, z):
    """Did the click reach the targeter? -> (ok, line). Read back, not assumed."""
    label = (before.get("source") or "unnamed targeting command")
    who = before.get("caster") or "<pawn>"
    if after is None:
        return False, ("the targeter could not be read back after the click (%s)."
                       " Check it: python ui.py targeter" % (why or ""))
    if after.get("active"):
        return False, ("TARGETER STILL OPEN -- %s; the click did not land; next:"
                       " python order.py deploy %s %d %d --do"
                       % (after.get("source") or label, who, x, z))
    return True, ("TARGETER CLOSED -- the click reached it. Not proof a job"
                  " started: a target out of range or out of line of sight"
                  " closes it and places nothing. Read the map back.")


def _usage():
    print(__doc__.split("## ")[0].strip())
    return 1


def _cell(nums, right, focus):
    """`--cell x z`: focus, read the targeter, click, read it back."""
    try:
        print(_focus_line(focus))
    except FocusRefused as e:
        print("click.py REFUSED: %s" % e)
        return 1
    x, z = int(nums[0]), int(nums[1])
    before, why = read_targeter()
    print(before_line(before, why))
    try:
        px, py = click_cell(x, z, right=right, focus=False, announce=False)
    except ValueError as e:
        print("click.py REFUSED: %s" % e)
        return 1
    print("clicked cell %d,%d at pixel %d,%d" % (x, z, px, py))
    if before is None or not before.get("active"):
        return 0
    after, why2 = read_targeter()
    ok, line = after_line(before, after, why2, x, z)
    print(line)
    return 0 if ok else 1


def main(argv):
    right = "--right" in argv
    focus = "--no-focus" not in argv
    rest = [a for a in argv if a not in ("--right", "--no-focus")]
    if "--key" in rest:
        i = rest.index("--key")
        name = rest[i + 1] if i + 1 < len(rest) else ""
        if name not in winctl.VK:
            print("python click.py --key <%s>" % "|".join(sorted(winctl.VK)))
            return 1
        try:
            key(name, focus=focus)
        except FocusRefused as e:
            print("click.py REFUSED: %s" % e)
            return 1
        print("pressed %s at the RimWorld window" % name)
        return 0
    if "--cell" in rest:
        i = rest.index("--cell")
        nums = rest[i + 1:i + 3]
        if len(nums) != 2:
            print("python click.py --cell <x> <z>")
            return 1
        return _cell(nums, right, focus)
    if len(rest) < 2:
        return _usage()
    try:
        click(int(rest[0]), int(rest[1]), right=right, focus=focus)
    except FocusRefused as e:
        print("click.py REFUSED: %s" % e)
        return 1
    print("clicked %s,%s" % (rest[0], rest[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
