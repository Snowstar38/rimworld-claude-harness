"""Cell -> screen pixel, so real mouse input can address the map.

`right_click_cell` dispatches a click through RimWorld's UI path but does NOT
produce a drafted move order; real SendInput does. RimWorld is fullscreen
4096x2160 here, and the camera's viewRect is inclusive on both ends.

**This aims at a CELL, not at what is standing on it.** The pixel returned is
the centre of the tile on the ground plane; a pawn is drawn taller than their
tile, so the click that selects them is several pixels ABOVE the one this
returns. Live 2026-09-08 at cell 138,110: y=1545 selected the pawn and y=1538
selected the sandstone floor under them -- seven pixels apart, with nothing in
either reply saying which of the two you had got. **A pawn must never be
addressed this way.** `pick.select_pawn()` sends `rimworld/select_pawn
{pawnId}` and verifies the selection by id, with no pixel in the path at all;
two pawns sharing a tile have one cell between them and no pixel can tell them
apart. Use this for a cell qua cell -- a move order, a designator drag, a
placement -- and read the selection back afterwards either way.

A cell the camera is not showing is REFUSED, not converted: the pixel would
fall outside the window and the real mouse would click the desktop.
"""
import camlock
import rim

SCREEN = (4096, 2160)


def to_px(x, z, cam=None):
    """Cell -> screen pixel, against a camera that is guaranteed to be still.

    A real click happens AFTER this returns, so the viewRect read here must not
    go stale in between. `settle()` waits out any Lookout glide in flight and the
    claim keeps it away for the rest of the turn; without both, a click computed
    here can land on a different cell than the one asked for.
    """
    camlock.settle()
    camlock.claim("hands", "pixel click near %d,%d" % (x, z))
    v = (cam or rim.game("rimworld/get_camera_state", {}, strict=False))["viewRect"]
    px = (x + 0.5 - v["minX"]) / v["width"] * SCREEN[0]
    py = (v["maxZ"] - z + 0.5) / v["height"] * SCREEN[1]
    px, py = int(round(px)), int(round(py))
    if not (0 <= px < SCREEN[0] and 0 <= py < SCREEN[1]):
        # An off-screen cell used to come back as a pixel like it was fine, and
        # the real mouse then clicked outside the window -- on the desktop, or
        # on a second monitor. PLAYBOOK's "click_cell reports success on an
        # off-screen cell and does nothing" is this, said before the click.
        raise ValueError(
            "cell %d,%d is OFF SCREEN: it maps to pixel %d,%d, outside the"
            " %dx%d window (the camera shows x %d-%d, z %d-%d). NOTHING WAS"
            " CLICKED. Bring it into view first: python cam.py go %d %d"
            % (x, z, px, py, SCREEN[0], SCREEN[1], v["minX"],
               v["minX"] + v["width"], v["maxZ"] - v["height"], v["maxZ"], x, z))
    return px, py
