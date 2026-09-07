"""Centre the camera and capture an image for Hands to open themselves.

  python see.py                       # base, normal base zoom
  python see.py 118 144 --zoom 24      # optional cell and zoom (smaller = closer)
  python see.py --zoom 24              # base with a wider view

Prints the image path. Open it with Read/view_image; a path alone is not vision.
Hands: one at turn start, at most one additional capture during the turn.
"""
import argparse
import math
from pathlib import Path
import sys
import time
import uuid

import cam
import camlock
import rim


def _success(reply, action):
    if not isinstance(reply, dict) or reply.get("success") is not True:
        raise RuntimeError("%s failed: %s" % (action, reply))


# How far before this call's start a file may be stamped and still count as
# this call's output. Slack for clock granularity, not for a stale shot.
FRESH_SLACK_S = 5.0


def _fresh_file(path, started):
    """The image THIS call wrote, or a RuntimeError. Never an older shot.

    `rimworld/take_screenshot` returns `sourcePath: null` (BUGS, upstream) and
    the usable field is `path`, which `cam.shoot` returns and raises without.
    What neither of them checks is WHEN the file was written -- and a picture
    of the wrong moment handed to a blind reviewer is the one failure this
    stack cannot catch downstream: it reads as evidence.

    So the named path counts only if it is newer than the call that asked for
    it. If it has not landed, the newest PNG in the same folder is accepted on
    the same test -- newest AFTER the call, never "newest", which is how a
    stale shot gets picked up. Anything else raises, naming what it found.
    """
    cutoff = started - FRESH_SLACK_S
    if path.is_file() and path.stat().st_size:
        age = started - path.stat().st_mtime
        if path.stat().st_mtime >= cutoff:
            return path
        raise RuntimeError(
            "%s is %.0f s older than this capture, so it is a PREVIOUS "
            "screenshot, not this one. No image is being returned; re-run "
            "`python see.py`." % (path, age))
    shots = [p for p in path.parent.glob("*.png")
             if p.stat().st_mtime >= cutoff and p.stat().st_size]
    if shots:
        return max(shots, key=lambda p: p.stat().st_mtime).resolve()
    raise RuntimeError(
        "the screenshot was reported at %s and no file written since this "
        "call started is in %s. Nothing was captured; an older shot in that "
        "folder is NOT this frame and is not being returned." % (path, path.parent))


def capture(x=None, z=None, zoom=None):
    if (x is None) != (z is None):
        raise ValueError("Supply both x and z, or neither for base.")
    zoom = cam.BASE_ROOT if zoom is None else float(zoom)
    if not math.isfinite(zoom) or zoom <= 0:
        raise ValueError("Zoom must be a finite positive root size; smaller is closer.")
    if x is None:
        x, z, source = cam.base_cell()
    else:
        source = "explicit coordinates"
    camlock.claim("hands", "see.py: capture at %d,%d" % (x, z))
    _success(cam.set_zoom(zoom), "set zoom")
    _success(cam.jump_to(x, z), "centre camera")
    time.sleep(0.2)  # let the rendered frame catch up with the camera command
    state = cam.state()
    _success(state, "read camera")
    centre = cam._centre(state)
    if centre is None or abs(centre[0] - x) > 3 or abs(centre[1] - z) > 3:
        raise RuntimeError("Camera did not reach %s,%s (read back %s); no screenshot taken." %
                           (x, z, centre))
    started = time.time()
    path = _fresh_file(Path(cam.shoot("hands-view-" + uuid.uuid4().hex)).resolve(),
                       started)
    print("Camera: %s,%s (%s); zoom %s, requested %s" %
          (centre[0], centre[1], source, cam._root(state), zoom))
    print("IMAGE: %s" % path)
    print("Open this image with Read (or view_image) NOW and inspect it yourself.")
    return str(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("coords", nargs="*", type=int, metavar="CELL")
    parser.add_argument("--zoom", type=float)
    args = parser.parse_args()
    if len(args.coords) not in (0, 2):
        parser.error("give x z, or no coordinates to centre on base")
    try:
        rim.init()
        capture(*(args.coords or [None, None]), zoom=args.zoom)
        return 0
    except Exception as exc:
        print("see.py FAILED: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
