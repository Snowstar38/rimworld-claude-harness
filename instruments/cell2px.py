"""Cell -> screen pixel, so real mouse input can address the map.

`right_click_cell` dispatches a click through RimWorld's UI path but does NOT
produce a drafted move order; real SendInput does. RimWorld is fullscreen
4096x2160 here, and the camera's viewRect is inclusive on both ends.
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
    return int(round(px)), int(round(py))
