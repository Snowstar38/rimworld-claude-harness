"""One map click, with the camera where the click needs to be, read back.

`rimworld/click_cell` injects a click at a CELL, but the game resolves that
click against what is drawn on screen. If the camera is not looking at the cell,
the click lands nowhere -- and the reply still says `success: true`, carrying a
`selectionAfter` that is just the selection it already had. Every caller that
clicked in order to select something therefore had a silent-success path: turn
12 of the 2026-09-07 stream lost a turret to it, and nothing said so.

**Camera position is part of the operation, not a viewing preference.** So every
click_cell in these instruments goes through `click()` here, which

  1. drops an armed architect designator -- one swallows click_cell whole and
     returns an empty selection with no error (`act.py clear`'s lesson),
  2. moves the camera onto the cell when it is off screen or within MARGIN of
     the edge, and refuses to click at all if the camera would not go there,
  3. clicks,
  4. READS THE SELECTION BACK, and says
     `CLICK MISSED -- selection is X, expected Y` when what got selected is not
     what was asked for.

A cell can hold several things -- 12 plainleather lying on a turret's tile made
the write path see one gizmo where the read path saw seven (turn 14). RimWorld
cycles the selection through the things under the cursor on repeated clicks, so
`select_thing()` clicks up to CLICK_TRIES times, stops the moment the wanted id
is the selected one, and raises rather than let a caller fire a gizmo on
whatever else happened to be lying there.

**A PAWN is never selected by a click here.** Cycling a cell cannot separate two
pawns standing on the same tile -- Samantha stood on Ernst's square for fourteen
turns of the 2026-09-08 stream and every read of his gizmo bar came back hers,
hiding a wearable turret pack the whole time. `select_pawn()` sends
`rimworld/select_pawn {pawnId}` and verifies the selection by id, with no pixel
in the path at all; `select_thing()` hands over to it whenever the target
resolves as a pawn.

Read-only from the command line:

  python pick.py where 118 144      # would a click at 118,144 land? camera only
  python pick.py selection          # what is selected right now
"""
import sys
import time

import cam
import camlock
import rim

# Cells kept between the target and the edge of the view. A cell at the very
# edge of the viewRect is under the architect menu, the inspect pane or the
# bottom bar as often as not, and a click there hits the UI instead of the map.
MARGIN = 3
# A cell holding several things cycles the selection one thing per click.
CLICK_TRIES = 8
CLICK_PAUSE = 0.35          # between cycling clicks, so the game processes each
SETTLE = 0.25               # after a camera jump, before the click


class ClickMissed(RuntimeError):
    """The click landed, and selected something other than what was asked for."""


class CameraStuck(RuntimeError):
    """The camera would not go to the target cell, so no click was sent."""


class Unreadable(RuntimeError):
    """A state read did not answer. NOT the same as "the state is clear"."""


# ------------------------------------------------------------- reading back --

def _norm_id(value):
    """`Thing_Turret123`, `turret123` and `Turret123` are one id."""
    if value is None:
        return None
    text = str(value).strip().lower()
    return text[6:] if text.startswith("thing_") else text


def selection():
    """What the game says is selected. Never raises: a read that failed is said.

    -> {"ok", "count", "objects", "ids", "text"}. `ids` holds every spelling of
    every selected thing's id, so a caller can test membership without caring
    which prefix the payload used.
    """
    r = rim.game("rimworld/get_selection_semantics", {}, strict=False)
    if not isinstance(r, dict) or r.get("success") is False:
        # Upstream: get_selection_semantics throws ArgumentNullException on a
        # selected turret (live 2026-09-06). The gizmo list still names its
        # owners, which is enough to know what is selected.
        via = _selection_via_gizmos()
        if via is not None:
            return via
        return {"ok": False, "count": None, "objects": [], "ids": set(),
                "text": "UNREADABLE (%s)" % str(r)[:120]}
    rows = r.get("selectedObjects") or []
    ids = set()
    for o in rows:
        n = _norm_id(o.get("id"))
        if n:
            ids.add(n)
    out = {"ok": True, "count": r.get("selectedCount") or 0, "objects": rows,
           "ids": ids, "text": ""}
    if not r.get("hasSelection") or not out["count"]:
        out["text"] = "nothing selected"
    elif not rows:
        out["text"] = "%s object(s), none described" % out["count"]
    else:
        out["text"] = "; ".join(
            "%s %s [%s]" % (o.get("kind") or "?",
                            o.get("label") or o.get("inspectLabel") or "?",
                            o.get("id") or "no id") for o in rows[:6])
    return out


def _selection_via_gizmos():
    g = rim.game("rimworld/list_selected_gizmos", {}, strict=False)
    if not isinstance(g, dict) or g.get("success") is False or "gizmos" not in g:
        return None
    ids, labels = set(), []
    for gz in g.get("gizmos") or []:
        for o in gz.get("owners") or []:
            n = _norm_id(o.get("id"))
            if n and n not in ids:
                ids.add(n)
                labels.append("%s [%s]" % (o.get("label") or o.get("kind") or "?",
                                           o.get("id")))
    text = "; ".join(labels) if labels else "nothing selected"
    return {"ok": True, "count": len(ids), "objects": [], "ids": ids,
            "text": text + " (read from gizmo owners; the selection read threw)"}


def holds(sel, thing_id):
    """Is `thing_id` the (or an) object in this selection?"""
    want = _norm_id(thing_id)
    return bool(want) and want in sel.get("ids", set())


def _target(reply):
    """`get_map_target_info`'s `target`, carrying the ids the ENVELOPE holds.

    The envelope always names `thingId`, `pawnId` and `kind`. The `target`
    payload does not: a thing is described by `DescribeThing`, which has
    `thingId`, and a PAWN by `DescribePawn`, which has `pawnId` and **no
    `thingId` at all**. Callers read `target["thingId"]`, so every pawn came
    back with a None id, and a None id verifies against nothing -- live
    2026-09-08, `buildings.py gizmo` refused itself with "selection is pawn
    Ernst [Thing_Human618], expected Ernst [None]".
    """
    target = dict(reply.get("target") or {})
    for key in ("thingId", "pawnId", "kind", "position", "label"):
        if not target.get(key) and reply.get(key):
            target[key] = reply[key]
    if not target.get("thingId") and target.get("pawnId"):
        target["thingId"] = target["pawnId"]
    if not target.get("kind"):
        target["kind"] = "pawn" if target.get("pawnId") else "thing"
    return target


def is_pawn(target):
    """Is this resolved target a pawn? Pawns are selected by id, never clicked."""
    t = target or {}
    return t.get("kind") == "pawn" or bool(t.get("pawnId"))


def resolve(thing_id):
    """`rimworld/get_map_target_info` for any thing on the map, or None.

    Both id spellings are tried, because `pawns.py` and `inv.py` print them
    with and without the `Thing_` prefix and both are handed straight to tools.
    """
    tried = []
    raw = str(thing_id).strip()
    for spelling in (raw, "Thing_" + raw if not raw.lower().startswith("thing_")
                     else raw[6:]):
        if spelling in tried:
            continue
        tried.append(spelling)
        r = rim.game("rimworld/get_map_target_info", {"thingId": spelling},
                     strict=False)
        if isinstance(r, dict) and r.get("target"):
            return _target(r)
    return None


def resolve_pawn(name_or_id):
    """A pawn on the current map, by NAME or by id, or None.

    `get_map_target_info {thingId:...}` matches an exact thing id and nothing
    else, so "Ernst" -- which is how a person names a pawn, and the only handle
    a stream chat ever says out loud -- resolved to nothing. The same tool takes
    `pawnId` and `pawnName` against every pawn on the map; both are tried, id
    first, because a name can be ambiguous and an id never is.
    """
    text = str(name_or_id or "").strip()
    if not text:
        return None
    for args in ({"pawnId": text}, {"pawnName": text}):
        r = rim.game("rimworld/get_map_target_info", args, strict=False)
        if isinstance(r, dict) and r.get("target"):
            return _target(r)
    return None


# ----------------------------------------------------------------- designator --

def armed():
    """The label of the armed architect designator, "(unnamed)", or None.

    None means nothing is armed. An armed designator swallows click_cell.

    A reply that is not a payload raises rather than returning None: "the read
    did not happen" and "nothing is armed" are different answers, and reading
    the first as the second is the silent-success bug this module exists for.
    A dropped game connection returns the prose string
    `Tool '...' not found for game 'rimworld'`, and that is exactly what it
    looked like the first time.
    """
    r = rim.game("rimworld/get_designator_state", {}, strict=False)
    if not isinstance(r, dict) or r.get("success") is False:
        raise Unreadable("rimworld/get_designator_state did not answer: %s"
                         % str(r)[:200])
    state = r.get("designatorState") or {}
    if not state.get("hasSelection"):
        return None
    d = state.get("selectedDesignator") or state.get("unmappedSelectedDesignator")
    if isinstance(d, dict):
        return d.get("label") or d.get("defName") or d.get("className") or "(unnamed)"
    return str(d) if d else "(unnamed)"


def clear_designator(announce=True):
    """Drop an armed placement designator. -> the label dropped, or None.

    `build.py` leaves one armed, and an armed designator makes the next gizmo
    call fail (turn 10). `act.clear()` is the call that drops it; it is imported
    lazily so `act` can import this module.
    """
    try:
        label = armed()
    except Unreadable as e:
        if announce:
            print("   !! could not read whether a designator is armed (%s) -- "
                  "if the click below selects nothing, that is the first thing "
                  "to check" % str(e)[:160])
        return None
    if label is None:
        return None
    import act
    try:
        act.clear()
    except Exception as e:                      # a clear that fails is still news
        if announce:
            print("   !! could not clear the armed designator (%s): %s"
                  % (label, str(e)[:120]))
        return label
    try:
        still = armed()
    except Unreadable:
        still = None
    if announce:
        if still is None:
            print("   cleared an armed placement designator (%s) first -- one "
                  "swallows the click" % label)
        else:
            print("   !! a placement designator is STILL armed (%s); the click "
                  "below may be swallowed" % still)
    return label


# --------------------------------------------------------------------- camera --

def view(state=None):
    """The camera's viewRect, or None if this build did not report one."""
    st = state if isinstance(state, dict) else cam.state()
    v = st.get("viewRect")
    return v if isinstance(v, dict) else None


def in_view(x, z, state=None, margin=MARGIN):
    """Is the cell far enough inside the frame for a click to reach the map?"""
    v = view(state)
    if not v:
        return False
    # A margin wider than the frame would refuse every cell; clamp it so a
    # deeply zoomed-in camera still gets an answer rather than a deadlock.
    mx = min(margin, max(0, (v["maxX"] - v["minX"]) // 2))
    mz = min(margin, max(0, (v["maxZ"] - v["minZ"]) // 2))
    return (v["minX"] + mx <= x <= v["maxX"] - mx and
            v["minZ"] + mz <= z <= v["maxZ"] - mz)


def where(x, z):
    """Read-only: would a click at this cell land? -> (ok, one line of prose)."""
    st = cam.state()
    v = view(st)
    if not v:
        return False, "the camera reports no viewRect, so nothing here can say"
    centre = cam._centre(st) or ("?", "?")
    if in_view(x, z, st):
        return True, ("camera is on %s,%s; %s,%s is inside the frame "
                      "x %d..%d z %d..%d -- a click would land"
                      % (centre[0], centre[1], x, z,
                         v["minX"], v["maxX"], v["minZ"], v["maxZ"]))
    return False, ("camera is on %s,%s, frame x %d..%d z %d..%d -- %s,%s is "
                   "OFF SCREEN or within %d cells of the edge, so a click "
                   "there would do nothing and still report success"
                   % (centre[0], centre[1], v["minX"], v["maxX"],
                      v["minZ"], v["maxZ"], x, z, MARGIN))


def ensure_camera(x, z, margin=MARGIN, announce=True, reason=None):
    """Put the camera where a click at (x, z) can reach the map.

    -> True if it moved, False if it was already there. Raises CameraStuck
    rather than clicking into a frame that does not contain the cell.
    """
    st = cam.state()
    if view(st) is None:
        # Nothing to correct against. Jumping blind would move the camera off
        # whatever is being watched on no evidence at all, so say it instead.
        if announce:
            print("   !! the camera reports no viewRect, so this click cannot "
                  "be checked against the frame -- if it lands on nothing, "
                  "the camera is the first thing to suspect")
        return False
    if in_view(x, z, st, margin):
        return False
    before = cam._centre(st)
    camlock.claim("hands", reason or "click at %d,%d" % (x, z))
    cam.jump_to(x, z)
    time.sleep(SETTLE)
    st = cam.state()
    if view(st) is None:
        return True                 # it moved; this build just cannot confirm
    if not in_view(x, z, st, 0):
        raise CameraStuck(
            "the camera would not move onto %d,%d (it reads %s, frame %s) -- "
            "NO CLICK WAS SENT, because a click outside the frame does nothing "
            "and reports success" % (x, z, cam._centre(st), view(st)))
    if announce:
        print("   camera moved to %d,%d for the click (it was on %s)"
              % (x, z, "%s,%s" % before if before else "an unread position"))
    return True


# ---------------------------------------------------------------------- click --

def click(x, z, button="left", move=True, clear=True, announce=True):
    """One camera-correct click at a cell. -> the bridge reply (may be a refusal).

    This is the only place these instruments call `rimworld/click_cell`.
    """
    if clear:
        clear_designator(announce=announce)
    if move:
        ensure_camera(x, z, announce=announce)
    args = {"x": int(x), "z": int(z)}
    if button and button != "left":
        args["button"] = button
    return rim.game("rimworld/click_cell", args, strict=False)


def select_cell(x, z, expect=None, expect_label=None, button="left",
                move=True, clear=True, announce=True):
    """Click a cell and return what is selected afterwards.

    `expect` is a thing id; when given and the selection does not carry it, this
    raises ClickMissed. With no `expect` it still returns the read-back, so a
    caller can print `selected: ...` instead of assuming.
    """
    click(x, z, button=button, move=move, clear=clear, announce=announce)
    sel = selection()
    if expect is not None and not holds(sel, expect):
        raise ClickMissed("CLICK MISSED -- selection is %s, expected %s"
                          % (sel["text"], expect_label or expect))
    return sel


def select_pawn(pawn_id, label=None, announce=True, clear=True):
    """Select a pawn BY ID. No click, no camera move, no cell involved.

    **Two pawns standing on one tile are one cell**, and a cell click cycles
    whatever is drawn there in whatever order the game draws it. Reading Ernst's
    gizmo bar through a click returned Samantha's for fourteen turns of the
    2026-09-08 stream because she was standing on his square, and a wearable
    turret pack sat unseen on his bar the whole time. A pawn has a stable id and
    the bridge has `rimworld/select_pawn {pawnId}`, so there is no reason to
    aim a pixel at one -- `order.py`, `move.py` and `combat_actions.py` have
    always selected pawns this way.

    Upstream `select_pawn` resolves PLAYER COLONISTS only (`ResolveColonist`),
    so an animal, a raider or a visitor is refused here -- and the caller can
    fall back to the cell path, which at least verifies what it selected.

    Returns the selection read-back. Raises ClickMissed on a refusal or on a
    selection that came back as someone else.
    """
    who = label or "that pawn"
    if not pawn_id:
        raise ClickMissed(
            "SELECT REFUSED -- %s was resolved without an id, so there is "
            "nothing to select by and nothing to verify against. A pawn "
            "payload names the id `pawnId`, not `thingId`." % who)
    if clear:
        clear_designator(announce=announce)
    rim.game("rimworld/clear_selection", {}, strict=False)
    r = rim.game("rimworld/select_pawn", {"pawnId": str(pawn_id)}, strict=False)
    if not isinstance(r, dict) or r.get("success") is False:
        why = ((r.get("message") or r.get("error")) if isinstance(r, dict)
               else str(r)[:160]) or "no reason given"
        raise ClickMissed(
            "SELECT REFUSED -- rimworld/select_pawn would not select %s [%s]: "
            "%s. It resolves PLAYER COLONISTS only, so an animal, a prisoner, "
            "a visitor or a raider has to be reached by clicking their cell."
            % (who, pawn_id, why))
    sel = selection()
    if holds(sel, pawn_id):
        if announce:
            print("   selected by id, no click: %s" % sel["text"])
        return sel
    if not sel.get("ok"):
        # The selection read is the one that throws on a selected turret
        # (live 2026-09-06). select_pawn's own read-back is then the evidence.
        confirmed = _norm_id((r.get("selected") or {}).get("pawnId"))
        if confirmed and confirmed == _norm_id(pawn_id):
            if announce:
                print("   selected by id, no click: %s [%s] -- the selection "
                      "read did not answer, so this is select_pawn's own "
                      "read-back" % (who, pawn_id))
            return {"ok": True, "count": 1, "objects": [r["selected"]],
                    "ids": {_norm_id(pawn_id)},
                    "text": "%s [%s] (from select_pawn's read-back)"
                            % (who, pawn_id)}
    raise ClickMissed(
        "SELECT MISSED -- selection is %s, expected %s [%s]. This was selected "
        "BY ID and not by a click, so a cell holding two pawns is not the "
        "explanation; the game is disagreeing with its own selection read."
        % (sel["text"], who, pawn_id))


def select_thing(thing_id, x=None, z=None, label=None, tries=CLICK_TRIES,
                 announce=True, clear=True, kind=None):
    """Select exactly this thing, cycling the cell's stack until it is the one.

    A cell can hold a turret and twelve plainleather; the first click can select
    either. Returns the selection read-back. Raises ClickMissed naming what got
    selected instead, so nothing downstream fires on the wrong thing.

    `kind="pawn"` -- or a resolve that comes back a pawn -- hands over to
    `select_pawn()`, which selects by id and never clicks: no amount of cycling
    can separate two pawns sharing a cell if the game hands back the same one.
    """
    if kind == "pawn":
        return select_pawn(thing_id, label=label, announce=announce, clear=clear)
    if x is None or z is None:
        target = resolve(thing_id)
        if not target:
            raise ClickMissed("CLICK MISSED -- selection is nothing, expected "
                              "%s (the map does not resolve that thing id)"
                              % (label or thing_id))
        p = target.get("position") or {}
        x, z = p.get("x"), p.get("z")
        label = label or target.get("label")
        if kind is None and is_pawn(target):
            return select_pawn(target.get("thingId") or target.get("pawnId")
                               or thing_id, label=label, announce=announce,
                               clear=clear)
        if x is None or z is None:
            raise ClickMissed("CLICK MISSED -- selection is nothing, expected "
                              "%s (it reports no map position, so there is no "
                              "cell to click)" % (label or thing_id))
    if clear:
        clear_designator(announce=announce)
    ensure_camera(x, z, announce=announce)
    rim.game("rimworld/clear_selection", {}, strict=False)
    sel = {"text": "nothing selected", "ids": set(), "objects": [], "count": 0,
           "ok": True}
    seen = []
    for attempt in range(max(1, tries)):
        if attempt:
            time.sleep(CLICK_PAUSE)
        click(int(x), int(z), move=False, clear=False, announce=False)
        sel = selection()
        if holds(sel, thing_id):
            if announce:
                print("   selected: %s" % sel["text"])
            return sel
        if sel["text"] not in seen:
            seen.append(sel["text"])
    raise ClickMissed(
        "CLICK MISSED -- selection is %s, expected %s [%s] at %s,%s (%d click(s) "
        "cycled the cell through: %s)"
        % (sel["text"], label or "that thing", thing_id, x, z, tries,
           " | ".join(seen) or "nothing at all"))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else ""
    if cmd not in ("selection", "where", "armed"):
        print(__doc__)
        return 0
    rim.init()
    try:
        if cmd == "selection":
            print(selection()["text"])
            return 0
        if cmd == "armed":
            label = armed()
            print("armed: %s" % label if label else "no designator armed")
            return 0
        if len(argv) < 3:
            print("python pick.py where <x> <z>")
            return 2
        ok, note = where(int(argv[1]), int(argv[2]))
        print(("OK   " if ok else "MISS ") + note)
        return 0 if ok else 1
    except (ValueError, Unreadable, RuntimeError) as e:
        # A dropped game connection must not come back as a traceback: this is
        # the command a fork runs to ask "why did my click do nothing".
        print("pick.py %s FAILED: %s" % (cmd, e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
