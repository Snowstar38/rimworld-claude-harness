"""Temporary packed-furniture helper; existing building tools are untouched.

  python mini_install.py Thing_MinifiedThing123              # read-only target check
  python mini_install.py Thing_MinifiedThing123 --do         # select and arm Install
  python mini_install.py Thing_MinifiedThing123 --to 120 140       # dry, both cells
  python mini_install.py Thing_MinifiedThing123 --to 120 140 --do  # and place it

Get IDs with inv.py minified --json.

**Two camera positions live inside one operation** (turn 13): the item's own
cell, because a click only selects what is drawn on screen, and then the
destination, because the placement click is a click too. Both go through
`pick.py`, which moves the camera and reads the selection back rather than
trusting `click_cell`'s `success: true`. Without `--to`, Install is armed and
the destination click is left to you -- the camera is on the ITEM at that
point, so move it before clicking.

**The placing click is confirmed by polling, not by one read.** `click_cell`
returns before RimWorld has spawned the blueprint, so a single read straight
after it printed *"the placing click left NO blueprint there (the cell reads
empty)"* for five statues that were in fact placed. The destination is now read
up to four times over ~2 s and the outcome is one of three different sentences:
confirmed (with the id and the work left), something else is standing there, or
not visible yet with the command to check again.
"""
import argparse
import sys
import time

import buildings
import pick
import rim


def call(tool, **args):
    result = rim.game("rimworld/" + tool, args, strict=False)
    if not isinstance(result, dict) or result.get("success") is not True:
        raise RuntimeError("%s refused: %s" % (tool, result))
    return result


def selected(thing_id):
    s = call("get_selection_semantics")
    rows = s.get("selectedObjects") or []
    return (s.get("selectedCount") == 1 and len(rows) == 1 and
            rows[0].get("id") == thing_id)


class NotConfirmed(RuntimeError):
    """The placing click was sent and no new blueprint was seen. Its own type
    so the caller can say which of the two failures this is."""


def still_armed():
    """True when RimWorld is STILL holding the placement designator.

    The game drops a designator the moment a cell is accepted, so a designator
    that survives the placing click is the game saying no. That is a different
    fact from "the blueprint is not drawn yet", and until 2026-09-12 both
    printed the same NOT VISIBLE YET sentence: a 3x3 holding platform whose
    footprint overlapped a blueprint two cells away read as a slow draw. Never
    raises -- an unreadable state is not evidence of a refusal.
    """
    try:
        state = call("get_designator_state").get("designatorState") or {}
    except Exception:
        return False
    return state.get("hasSelection") is True


def drop_designator():
    """Clear an Install designator the game refused, and say which happened.

    An armed designator swallows or misdirects the next click, so leaving one
    behind turns one refused install into a surprise placement later.
    """
    try:
        import act
        act.clear()
    except Exception as exc:
        print("   the designator could NOT be cleared (%s) -- run "
              "`python act.py clear` before the next click." % exc)
        return False
    print("   the armed designator was CLEARED, so the next click cannot drop "
          "the item somewhere unintended.")
    return True


def install(thing_id, do=False, to=None):
    t = call("get_map_target_info", thingId=thing_id).get("target") or {}
    if t.get("className") != "RimWorld.MinifiedThing" or not t.get("spawned"):
        raise RuntimeError("Only spawned packed furniture is supported; nothing changed.")
    identity = t["thingId"]
    p = t["position"]
    print("%s | %s | %s,%s" % (t.get("label"), identity, p["x"], p["z"]), flush=True)
    if to is not None:
        print("destination %s,%s -- the camera moves there for the placing click."
              % (to[0], to[1]), flush=True)
    if not do:
        print("READ ONLY: target found. Add --do to select it and arm Install."
              if to is None else
              "DRY RUN - would select %s at %s,%s, fire Install and click %s,%s "
              "(nothing applied; add --do)."
              % (identity, p["x"], p["z"], to[0], to[1]))
        return
    state = call("get_designator_state").get("designatorState") or {}
    if state.get("hasSelection") is not False:
        raise RuntimeError("Cancel active placement first (python act.py clear); nothing changed.")
    call("clear_selection")
    # Camera position is part of the operation: a click at a cell the camera is
    # not looking at does nothing and still reports success (turn 12).
    pick.ensure_camera(p["x"], p["z"], reason="mini_install: select %s" % identity)
    # With no selected pawn or armed designator, right-click safely cancels
    # an ability targeter before a left-click could trigger its action.
    call("click_cell", x=p["x"], z=p["z"], button="right")
    for attempt in range(8):
        if attempt:
            time.sleep(0.4)
        call("click_cell", x=p["x"], z=p["z"])
        if selected(identity):
            break
    else:
        raise RuntimeError("Could not select exact furniture after 8 clicks; Install not fired.")
    rows = call("list_selected_gizmos").get("gizmos") or []
    matches = [g for g in rows if (g.get("label") or "").strip().lower() == "install"]
    if not matches:
        matches = [g for g in rows if (g.get("label") or "").strip().lower().startswith("install ")]
    if len(matches) != 1 or matches[0].get("disabled") or not matches[0].get("id"):
        raise RuntimeError("No unique enabled Install gizmo; nothing fired. Bar: %s" %
                           [(g.get("label"), g.get("disabledReason")) for g in rows])
    if not selected(identity):
        raise RuntimeError("Selection changed; Install not fired.")
    call("execute_gizmo", gizmoId=matches[0]["id"])
    if to is None:
        print("Install fired. Click the destination, then check buildings.py --pending.")
        print("NOTE: the camera is on the ITEM at %s,%s. Move it to the "
              "destination before clicking, or the click will not land."
              % (p["x"], p["z"]))
        return
    # The second camera position of the operation.
    dx, dz = int(to[0]), int(to[1])
    before = buildings.occupant_ids(dx, dz)
    pick.ensure_camera(dx, dz, reason="mini_install: place at %d,%d" % (dx, dz))
    rim.game("rimworld/click_cell", {"x": dx, "z": dz}, strict=False)
    # The click returns before the game has spawned the blueprint, so this
    # polls the cell instead of reading it once. Three outcomes, three
    # sentences: confirmed, something else is there, not visible yet.
    if buildings.report_placement(dx, dz, t.get("label"), before, pad="") == 0:
        return
    if still_armed():
        print("REFUSED BY THE GAME: the Install designator is STILL ARMED, and "
              "the game drops it as soon as a cell is accepted -- so %d,%d was "
              "REJECTED, not slow to draw. The footprint is usually bigger than "
              "the one cell asked for: `python build.py <def> %d %d` previews "
              "every cell it would take and names what is in the way."
              % (dx, dz, dx, dz))
        drop_designator()
    raise NotConfirmed("the Install was fired and the placing click at %d,%d is "
                       "not confirmed -- see the lines above for which case it "
                       "is. Nothing else was changed." % (dx, dz))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("thing_id")
    parser.add_argument("--to", nargs=2, type=int, metavar=("X", "Z"),
                        help="destination cell; the placing click happens here")
    parser.add_argument("--do", action="store_true")
    args = parser.parse_args()
    try:
        rim.init()
        install(args.thing_id, args.do, args.to)
        return 0
    except Exception as exc:
        print("STOP: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
