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
"""
import argparse
import sys
import time

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


def placed_at(x, z):
    """A blueprint or frame standing on the destination cell, or None.

    Read off the map, never inferred from the click reply: a placement click
    that missed reports success exactly like one that landed.
    """
    r = rim.game("home/get_cells_plus",
                 {"x": int(x), "z": int(z), "width": 1, "height": 1,
                  "fields": "things", "thingFields": "defName,label,isBlueprint,isFrame",
                  "sparse": True}, strict=False)
    if not isinstance(r, dict) or not r.get("success"):
        return None
    for c in r.get("cells") or []:
        for t in c.get("things") or []:
            if t.get("isBlueprint") or t.get("isFrame"):
                return t.get("label") or t.get("defName") or "a blueprint"
    return None


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
    before = placed_at(dx, dz)
    pick.ensure_camera(dx, dz, reason="mini_install: place at %d,%d" % (dx, dz))
    rim.game("rimworld/click_cell", {"x": dx, "z": dz}, strict=False)
    time.sleep(0.4)
    after = placed_at(dx, dz)
    if after and after != before:
        print("INSTALL PLACED: %s now stands at %d,%d as %s. Check "
              "`buildings.py --pending`." % (t.get("label"), dx, dz, after))
        return
    raise RuntimeError(
        "Install was fired and the placing click at %d,%d left NO blueprint "
        "there (the cell reads %s). The targeter may still be armed -- `python "
        "act.py clear` drops it. Nothing else was changed."
        % (dx, dz, after or "empty"))


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
