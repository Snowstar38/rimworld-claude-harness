"""Click UI by visible text -- and READ a surface without the geometry.

  python ui.py                    # slim read of everything on screen
  python ui.py main-tab:Research  # slim read of ONE surface
  python ui.py --surfaces         # what surface ids exist right now, + the main tab bar
  python ui.py --selection        # what is selected on the map, read back
  python ui.py --probe E          # why does click("E") not find a button? (geometry)
  python ui.py tab Research       # OPEN the tab, read it, CLOSE it again
  python ui.py tab Research --keep-open   # ... and leave it standing
  python ui.py close              # close whatever main tab is open (close_main_tab)
  python ui.py click "OK"         # click by visible text; prints what changed
  python ui.py main-tab:Work click "Firefighter"   # ... scoped to one surface
  python ui.py click --index 2    # click the 3rd TEXT-FREE button (see the read)
  python ui.py targeter           # is a map click being read as a TARGET?
  python ui.py targeter --cancel  # close one (by deselecting), and confirm
  python ui.py click --rect 1200,800,180,180      # ... or by its rectangle
  (--full = no text truncation, --json = the structure, --raw = raw byte count)

## Buttons with no text

An image button -- a storyteller portrait -- draws no label, so there is no text
to click it by. The read lists every such element with its rectangle and a
number, and `click --index N` / `click --rect x,y,w,h` press one through the
bridge from the same capture that numbered it. `click.py <x> <y>` is the real
mouse, for the two surfaces `get_ui_layout` cannot see at all.

A radio or option-tile row prints `[x]` or `[ ]` when the bridge reports its
selection state. Older bridge builds leave the state unknown as `[?]`.

## Opening a tab to READ it

`rimworld/open_main_tab` is NOT a toggle: called on an open tab it re-opens it
and calling it twice does nothing (BUGS 2026-09-05, the Research tab, 482
elements drawn over the map; the Quests tab got stuck the same way and had no
`Close` element on it). `rimworld/close_main_tab` is the real close, and
`ui.py tab <name>` opens, reads and closes in one call so nothing is left over
the map. `--keep-open` is the opt-out; `ui.py close` is the manual close.

`--surfaces` lists the OPEN surfaces and then the bottom main-tab bar, which is
not a surface at all until its tab is open. A tab whose `type` is empty is a
TOGGLE, not a tab window -- World is one -- so `open_main_tab` has nothing to
open for it and `click_ui_target` has no ui-element id to click. The row says
so rather than leaving the bar invisible.

RimWorld's IMGUI draws a label and an invisible button as separate elements, so
`get_ui_layout` reports the readable text with `actionable: false` and the thing
you can actually click with `label: null`. Matching on the label alone silently
finds nothing clickable. This pairs them by rectangle overlap.

## The gizmo bar goes by the real mouse

`rimworld/click_ui_target` fires a gizmo perfectly well -- it injects a
MouseDown/MouseUp inside the patched `Widgets.ButtonInvisible` and forces the
return to true, `Command.GizmoOnGUIInt` sets `Interacted`, and
`GizmoGridDrawer` calls `ProcessInput`. What it does NOT do is move the OS
cursor. So a gizmo that opens a TARGETER -- `Command_VerbTarget` /
`Command_Target`, e.g. "Deploy turret" on a worn turret pack -- comes up with
its placement ghost wherever the physical mouse was parked, because
`Targeter.CurrentTargetUnderMouse` reads `UI.MousePositionOnUIInverted`. And a
targeter is not a Window, so the window-stack diff on both sides of the click
(the bridge's own `changed`, and `_signature` here) is identical before and
after: the click reported "nothing opened or closed" while the targeter sat
open and invisible. That cost fourteen turns live on 2026-09-08.

So: **an element on the `selection-gizmos` surface is clicked with the real
mouse** (`click.py`, one click, no synthesis), and every click now reads
`home/status`'s `ui.targeter` back when it might matter. `--bridge` forces the
old path, `--pixel` forces the mouse for anything else.

Once a targeter is open, **`order.py deploy` is the route that places things**.
Live on Threadneedle 2026-09-11 a synthetic Escape and a synthetic right-click
both left the targeter standing -- neither reaches
`Targeter.ProcessInputEvents`, where the cancel keybinding and the right-button
test live -- and a real map click at a cell that passed both of `order.py
deploy`'s gates was swallowed. That third one had two causes under it, both now
addressed in `click.py`: the window was not verifiably in the foreground, and
Unity reads the target cell from the mouse position it last received, so a
`ValidateTarget` computed at a stale cell fails and `ProcessInputEvents` answers
with `Event.current.Use(); return;` -- eaten, targeter still open. `click.py
--cell` now verifies focus, moves twice, and reads `ui.targeter` back on both
sides, so it says `TARGETER CLOSED` or `TARGETER STILL OPEN` instead of
guessing. Until that is confirmed live, deploy with `order.py deploy`.

`ui.py targeter --cancel` is the route that closes one, and it works by clearing
the selection, because `Targeter.ConfirmStillValid` calls `StopTargeting()` the
moment the caster stops being selected.

## The slim read

One main tab is 50-200 KB of JSON and about 97% of it is machinery for CLICKING
-- targetIds, two copies of every rectangle, crop flags, scroll offsets -- while
a reader asking *"what are my options here?"* wants the visible texts and which
of them do something. `slim()` returns the second thing.

What it drops is safe to drop because **every id in the payload is per-capture**:
the surface counter increments on each `get_ui_layout`, so a stored targetId is
dead the moment you fetch again (PLAYBOOK 5a). The handle that survives is the
**visible text**, which is what `find` / `click` / `row_toggle` already take. So
the contract is:

    read with slim(), act with click("<the text you read>") in a FRESH capture.

Which is one round trip either way, because a click has to re-capture regardless.
The text you read is the text that clicks: `_norm` produces the printed form and
both sides match on it, so markup and line joins cannot drift (a raw label still
matches too, and a line cut at `_TRUNC` still clicks by prefix). Pass `surface=`
to `click` / `find` / `row_toggle` to scope the act to the surface you read.
"""
import json, re, sys, time, rim
# The OS-pixel size of the RimWorld window, shared with the cell->pixel
# converter so the two never disagree about what screen they aim at.
from cell2px import SCREEN

# Rich-text markup RimWorld puts in pawn labels ("Finn<color=#999999FF>, Illu...").
# Nothing else in the stack strips it; a reader should not have to read it.
_RICH = re.compile(r"<(?:/?[a-zA-Z]+)(?:=[^<>]*)?>")
# Two labels count as the same draw if the text matches and both corners land in
# the same 4px cell -- RimWorld draws outlined/shadowed text 2-6 times over.
_DUP_CELL = 4.0
# Same row if the vertical centres are within this many pixels. Deliberately
# tight: the Work tab's staggered two-row column headers are 20px apart and that
# stagger is load-bearing (an incapable pawn has no cell at all -- PLAYBOOK 5b).
_ROW_TOL = 6.0
_TRUNC = 160
# The tail slim appends to a text it cut; a caller may paste it back.
_TRUNC_MARK = re.compile(r"\.\.\.\(\+\d+ chars\)$")


def _norm(label):
    """The printed form of a label: markup stripped, whitespace collapsed, lines
    joined with " / ". Reading and clicking both go through this, so the text a
    reader is shown is the text that matches. Truncation is NOT part of it."""
    lines = [re.sub(r"\s+", " ", p).strip()
             for p in _RICH.sub("", label or "").splitlines()]
    return " / ".join(p for p in lines if p)


def printed_text(label):
    """The printed form of a label, under a public name.

    `slim` prints this and `find` matches it, so a caller holding a raw string
    from somewhere else -- a letter's `DiaOption.text`, say -- passes it through
    here to get the text a click will land on.
    """
    return _norm(label)


def supervised_play_alive():
    """Is the supervised-play service running? True / False / None (unreadable).

    A disk read, no bridge call. It lives beside the things that force-pause the
    game -- a main tab, a letter dialog -- because the watcher stops on any
    force pause and nothing else says so.
    """
    try:
        import play
        import play_service
        row = play_service.read_json(play_service.SERVICE_STATE) or {}
        return bool(play.service_alive(row))
    except Exception:
        return None


def _on_row(row_rect, tog_rect):
    """Does an unlabelled toggle belong to the row at `row_rect`?

    To the right of the row, and on the same line -- the row's vertical centre
    inside the toggle, or the two centres within `_ROW_TOL`. slim's read and
    row_toggle's click share this rule so they agree about what a row carries.
    """
    if tog_rect["x"] <= row_rect["x"]:
        return False
    rc = row_rect["y"] + row_rect["height"] / 2
    tc = tog_rect["y"] + tog_rect["height"] / 2
    return (tog_rect["y"] <= rc <= tog_rect["y"] + tog_rect["height"]
            or abs(rc - tc) <= _ROW_TOL)


_MOUSE = None
_MOUSE_ERR = None


def mouse():
    """click.py, the real-mouse module beside this one, or None.

    Loaded by PATH rather than by `import click`, because `click` is also a
    popular pip package and whichever of the two wins would depend on where
    the caller was run from. It imports winctl, so it can legitimately fail on
    a machine without it; the reason is kept and printed rather than raised.
    """
    global _MOUSE, _MOUSE_ERR
    if _MOUSE is not None or _MOUSE_ERR is not None:
        return _MOUSE
    import importlib.util, os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "click.py")
    try:
        spec = importlib.util.spec_from_file_location("_rimworld_click", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        _MOUSE_ERR = "%s: %s" % (type(e).__name__, e)
        return None
    _MOUSE = mod
    return _MOUSE


def layout(surface=None, timeout_ms=None):
    """One `get_ui_layout` call. `surface` is a surfaceTargetId or None for all."""
    args = {}
    if surface:
        args["surfaceId"] = surface
    if timeout_ms:
        args["timeoutMs"] = timeout_ms
    return rim.game("rimworld/get_ui_layout", args)


def elements(surface=None):
    for s in (layout(surface).get("surfaces") or []):
        for e in s.get("elements") or []:
            yield e


def _covers(btn, lab):
    b, l = btn.get("screenRect") or {}, lab.get("screenRect") or {}
    if not b or not l:
        return False
    cx, cy = l["x"] + l["width"] / 2, l["y"] + l["height"] / 2
    return b["x"] <= cx <= b["x"] + b["width"] and b["y"] <= cy <= b["y"] + b["height"]


def _captions(btn, lab):
    """A gizmo draws its NAME under the icon, so no rect contains the text.

    `Draft`'s label sits across the bottom edge of its 75x75 button, which
    `_covers` misses by six pixels -- and a reader who saw "Draft" listed as
    clickable would then be told there is no such element. Same test both sides.

    The caption must OVERLAP the bottom edge, not merely sit under it: in a
    table, every cell of row n+1 sits exactly under a cell of row n, and a
    "under it" rule ate the Wildlife tab's percentage columns.
    """
    b, l = btn.get("screenRect") or {}, lab.get("screenRect") or {}
    if not b or not l:
        return False
    lcx = l["x"] + l["width"] / 2
    gap = l["y"] - (b["y"] + b["height"])
    return (b["x"] <= lcx <= b["x"] + b["width"] and l["width"] <= b["width"]
            and -l["height"] < gap < 0)


_SAME_SPOT = 4.0


def _coincident(btn, lab):
    """Last resort: the button and the label are drawn in the SAME PLACE.

    Added 2026-09-02, after `ui.click('E')` raised "no clickable UI element for
    'E'" while listing `E` among the visible labels.

    **Measured live the same day, and it was not the geometry.** `ui.py --probe
    E` with the Architect open on Temperature and the cooler designator armed:

        LABEL 'E'    kind=label       act=False  x=105 y=264 w=64 h=64
        0.0px        kind=icon_button act=True   x=105 y=264 w=64 h=64

    Identical rect. `_covers` matched it perfectly -- and `find()` never asked,
    because its candidate list was filtered to `kind == "button"` and a rotate
    button is an **`icon_button`**. The geometry was never the problem; the kind
    filter was. So the widening that actually fixes it is dropping that filter,
    which this test carries because it is the one that runs unrestricted.

    The looser geometry is kept anyway, for the case the first reading of this
    bug guessed at: same centre within a few px, identical rect, or either rect
    wholly containing the other. It costs nothing and it is last, so it can only
    catch what the two stricter tests dropped.

    Deliberately last -- it is looser than the other two and would otherwise
    steal pairs from them -- and deliberately NOT limited to `kind == "button"`,
    which is the whole point.
    """
    b, l = btn.get("screenRect") or {}, lab.get("screenRect") or {}
    if not b or not l:
        return False
    bcx, bcy = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
    lcx, lcy = l["x"] + l["width"] / 2, l["y"] + l["height"] / 2
    if abs(bcx - lcx) <= _SAME_SPOT and abs(bcy - lcy) <= _SAME_SPOT:
        return True
    return _holds(b, l) or _holds(l, b)


def _holds(outer, inner):
    return (outer["x"] <= inner["x"] and outer["y"] <= inner["y"]
            and outer["x"] + outer["width"] >= inner["x"] + inner["width"]
            and outer["y"] + outer["height"] >= inner["y"] + inner["height"])


def _matches(els, text, exact=True):
    """Labels carrying `text`, matched on the printed form AND on the raw one.

    Raw stays accepted so nothing that matched before stops matching. A text as
    long as slim's cut is a truncated line: fall back to a prefix match, but
    only when nothing matched outright, so it can never steal a real hit.
    """
    hits = []
    for e in els:
        raw, norm = (e.get("label") or "").strip(), _norm(e.get("label"))
        if raw == text or norm == text:
            hits.append(e)
        elif not exact and (text.lower() in raw.lower() or text.lower() in norm.lower()):
            hits.append(e)
    if hits:
        return hits
    stem = _TRUNC_MARK.sub("", text).rstrip()
    if len(stem) >= _TRUNC - 3:
        return [e for e in els if _norm(e.get("label")).startswith(stem)]
    return []


def _composite_target(payload, text, exact=True):
    """The actionable behind a row text that slim PRINTED but no label carries.

    slim joins a row's own labels with " " and an inert neighbour on the same
    line with "  ", so the text a reader copies out of the listing is often a
    composite of two or three draws and matches no single `label`. Match it
    against the same rows the reader saw and hand back that row's actionable.
    `own` is the row before the neighbour was merged in, so both halves work.
    """
    want = _norm(text)
    stem = _TRUNC_MARK.sub("", want).rstrip()
    for s in (payload.get("surfaces") or []):
        for row in slim_surface(s, full=True, keep_targets=True).get("rows") or []:
            if not row.get("_tid"):
                continue
            for cand in (row.get("text"), row.get("own")):
                got = _norm(cand)
                if not got:
                    continue
                if got == want:
                    return row["_tid"]
                if len(stem) >= _TRUNC - 3 and got.startswith(stem):
                    return row["_tid"]
                if not exact and want and want in got.lower():
                    return row["_tid"]
    return None


def find(text, exact=True, els=None, surface=None, payload=None):
    """The targetId to click for `text`, or None. Ids are per-capture.

    Pass `payload` (a whole `get_ui_layout` reply) to let a composite row text
    resolve as well as a single label -- `click` does, so anything the read
    printed as clickable can be pasted straight back.
    """
    if payload is None and els is None:
        payload = layout(surface)
    if els is None:
        els = [e for s in (payload.get("surfaces") or [])
               for e in (s.get("elements") or [])]
    labs = _matches(els, text, exact)
    for lab in labs:
        if lab.get("actionable"):
            return lab["targetId"]
        # The first two tests are kind-restricted and stay that way; the third
        # is the loose one and takes any actionable element.
        for test, kinds in ((_covers, ("button",)), (_captions, ("button",)),
                            (_coincident, None)):
            best = [e for e in els
                    if e.get("actionable") and (kinds is None or e.get("kind") in kinds)
                    and e.get("screenRect") and test(e, lab)]
            if best:
                return min(best, key=lambda e: e["screenRect"]["width"] * e["screenRect"]["height"])["targetId"]
    if payload is not None:
        return _composite_target(payload, text, exact)
    return None


def probe(text, radius=40.0, exact=True):
    """Every element within `radius` px of a label, with kind/actionable/rect.

    Written 2026-09-02 for exactly the failure above: when `click` says a label
    exists but nothing clickable pairs with it, this is the ONE call that shows
    why -- the geometry, not a guess about it. `python ui.py --probe E`.
    """
    raw = layout(None)
    els = [e for s in (raw.get("surfaces") or []) for e in (s.get("elements") or [])]
    labs = _matches(els, text, exact)
    L = ["PROBE %r: %d label(s) match" % (text, len(labs))]
    if not labs:
        L.append("  no element carries that text: checked, not assumed")
        for line in _composite_report(raw, text):
            L.append(line)
    for n, lab in enumerate(labs):
        lr = _rect(lab)
        L.append("  [%d] LABEL %r kind=%s actionable=%s rect=%s"
                 % (n, (lab.get("label") or "").strip(), lab.get("kind"),
                    bool(lab.get("actionable")), _fmt(lr)))
        lcx, lcy = lr["x"] + lr["width"] / 2, lr["y"] + lr["height"] / 2
        near = []
        for e in els:
            if e is lab:
                continue
            r = _rect(e)
            d = max(0.0, max(r["x"] - (lr["x"] + lr["width"]), lr["x"] - (r["x"] + r["width"])))
            d2 = max(0.0, max(r["y"] - (lr["y"] + lr["height"]), lr["y"] - (r["y"] + r["height"])))
            gap = (d * d + d2 * d2) ** 0.5
            if gap <= radius:
                near.append((gap, e, r))
        near.sort(key=lambda t: t[0])
        if not near:
            L.append("      nothing at all within %gpx" % radius)
        for gap, e, r in near:
            tests = [name for name, fn in (("covers", _covers), ("captions", _captions),
                                           ("coincident", _coincident))
                     if e.get("screenRect") and fn(e, lab)]
            L.append("      %5.1fpx %-22s act=%-5s %s%s%s"
                     % (gap, e.get("kind"), bool(e.get("actionable")), _fmt(r),
                        ("  text=%r" % (e.get("label") or "").strip()) if e.get("label") else "",
                        ("  PAIRS BY: " + ",".join(tests)) if tests else ""))
        L.append("      centre of the label is %.1f,%.1f" % (lcx, lcy))
    L.append("find() would return: %r" % find(text, exact, els=els, payload=raw))
    free = free_actionables(raw)
    if free:
        L.append("  %d actionable element(s) on screen carry NO text at all; "
                 "click one by its place in the read: `python ui.py click "
                 "--index N`, or `python ui.py click --rect x,y,w,h`." % len(free))
    return '\n'.join(L)


def _composite_report(payload, text):
    """Lines explaining a text slim printed that no single label carries."""
    want = _norm(text)
    stem = _TRUNC_MARK.sub("", want).rstrip()
    for s in (payload.get("surfaces") or []):
        for row in slim_surface(s, full=True, keep_targets=True).get("rows") or []:
            got = _norm(row.get("text"))
            if got != want and not (len(stem) >= _TRUNC - 3 and got.startswith(stem)):
                continue
            out = ["  the READ prints it as one row, built from %d separate text"
                   " draws: %s" % (len(row.get("parts") or [row.get("text")]),
                                   " | ".join(repr(p) for p in
                                              (row.get("parts") or [row["text"]])))]
            if row.get("own"):
                out.append("  the part that belongs to the button is %r; the rest"
                           " is a separate label sitting on the same line"
                           % row["own"])
            out.append("  it resolves to %s, so click() takes it as printed"
                       % (row.get("_tid") or "NOTHING ACTIONABLE"))
            return out
    return []


def _fmt(r):
    return "x=%g y=%g w=%g h=%g" % (r.get("x", 0), r.get("y", 0),
                                    r.get("width", 0), r.get("height", 0))


# Surface ids, not visible texts. `click()` takes the TEXT first and the
# surface as a keyword; the command line takes the surface first. Every one of
# these prefixes is a surface id (`ScreenTargetIds.cs`, `UiLayoutTargetIds.cs`),
# so a call that swapped the two can be caught and named instead of hunting the
# screen for a label nothing will ever carry.
_SURFACE_IDS = ("ui-surface:", "ui-element:", "main-tab:", "window:",
                "selection-gizmos")


def clickable_texts(payload):
    """Every text a click can actually land on, from one layout payload.

    The pairing rule is slim's, which is find()'s, so this list is exactly the
    set of texts `click` would resolve -- not "every label on screen", which is
    what the old refusal printed and which is mostly inert scenery.
    """
    out = []
    for s in (payload.get("surfaces") or []):
        for row in slim_surface(s).get("rows") or []:
            if row.get("act") and row.get("text"):
                out.append(row["text"])
    return sorted(set(out))


def free_actionables(payload):
    """Every actionable element carrying no text, numbered as the read prints them.

    Rows: index, kind, rect, centre, `_tid`. This is the only handle on a
    text-free button -- the storyteller portraits are three of them -- and the
    ids are per-capture, so the caller must click from the SAME payload.
    """
    sl = slim(payload=payload, full=True, keep_targets=True)
    return [i for s in sl["surfaces"] for i in s["textFree"]["items"]]


def targeter_state():
    """Find.Targeter as the game reports it. -> (state, why_not).

    `state` is `{"active": bool, "source": <label or None>, "caster": <pawn
    thingId or None>}` from `home/status`'s ui block, or None with a reason in
    `why_not` -- an older DLL has no such field, and saying so beats printing
    "no targeter" for a thing that was never read.

    This is THE read that the window-stack diff cannot do. A targeter opens no
    window: `modalOpen`, the window list and `_signature` are all blind to it.
    """
    try:
        r = rim.game("home/status", {"colonists": False, "threats": False},
                     strict=False)
    except Exception as e:
        return None, "home/status did not answer (%s: %s)" % (type(e).__name__, e)
    if not isinstance(r, dict):
        return None, "home/status returned %s, not an object" % type(r).__name__
    block = r.get("ui")
    if not isinstance(block, dict):
        return None, "home/status returned no ui block"
    state = block.get("targeter")
    if not isinstance(state, dict):
        return None, ("this bridge build's home/status has no ui.targeter field."
                      " Install the current HomeBridge.BridgeTools.dll"
                      " (rimworld\\companion\\INSTALL.md) and the targeter"
                      " becomes readable")
    return state, None


def targeter_line(state, why_not=None):
    """One human line for a targeter state, for `ui.py targeter` and for a click."""
    if state is None:
        return "targeter: NOT READ -- %s" % (why_not or "no reason given")
    if not state.get("active"):
        return ("targeter: closed -- nothing on the map is waiting for a target"
                " click.")
    who = state.get("caster")
    return ("targeter: OPEN -- %s%s. The map is in placement mode, and a raw map"
            " click is swallowed by it -- `python order.py deploy <x> <z> --do`"
            " is the route that places. `python ui.py targeter --cancel` closes"
            " it (by clearing the selection)."
            % (state.get("source") or "unnamed targeting command",
               (", caster %s" % who) if who else ""))


_CANCEL_SETTLE = 0.4


def cancel_targeter():
    """Close an open targeter and read it back. -> (ok, text).

    **Deselecting the caster is the cancel that works.** Live on Threadneedle
    2026-09-11, with the targeter confirmed open by `ui.targeter`: a synthetic
    Escape (SendInput) left it standing, and so did a synthetic right-click on
    the map. Neither reaches `Targeter.ProcessInputEvents`, which is the only
    place `KeyBindingDefOf.Cancel.KeyDownEvent` and the right-button test are
    read. `Targeter.ConfirmStillValid` runs on the same pass and calls
    `StopTargeting()` the instant the caster stops being selected, so
    `rimworld/clear_selection` closes it deterministically.

    **It costs the selection**, which is said out loud rather than left to be
    discovered by the next gizmo read. A `Command_Target` that recorded no
    caster at all has nothing to deselect -- `ConfirmStillValid` returns early
    when both `caster` and `targetingSource` are null -- so the real Escape is
    still tried after, and reported honestly either way.

    The state is read FIRST because Escape with no targeter open opens the game
    MENU, which force-pauses.
    """
    state, why = targeter_state()
    if state is None:
        return False, ("ui.py targeter --cancel REFUSED: the targeter could not"
                       " be read, so there is nothing to confirm against and"
                       " Escape with nothing targeting opens the game MENU."
                       " NOTHING WAS CLEARED OR PRESSED. %s" % (why or ""))
    if not state.get("active"):
        return True, ("targeter: already closed; nothing to cancel. NOTHING WAS"
                      " CLEARED OR PRESSED (clearing the selection and pressing"
                      " Escape both cost something when there is no targeter).")
    label = state.get("source") or "unnamed targeting command"
    who = state.get("caster")
    lost = ("  The selection is now EMPTY -- the caster was %s; reselect it"
            " before the next gizmo read (`pick.select_pawn(%r)`, or `python"
            " pick.py selection` to look)." % (who, who) if who else
            "  The selection is now EMPTY; reselect before the next gizmo read"
            " (`python pick.py selection` to look).")

    try:
        rim.game("rimworld/clear_selection", {}, strict=False)
    except Exception as e:
        cleared = "rimworld/clear_selection did not answer (%s: %s)" % (
            type(e).__name__, e)
    else:
        cleared = None
    time.sleep(_CANCEL_SETTLE)

    after, why2 = targeter_state()
    if after is None:
        return False, ("cleared the selection, but the targeter could not be read"
                       " back (%s). Check it: python ui.py targeter" % (why2 or ""))
    if not after.get("active"):
        return True, ("cancelled the targeter (%s): cleared the selection and"
                      " ConfirmStillValid closed it." % label + lost)

    # Still open: either clear_selection failed, or this targeter has no caster
    # to lose. Try the real key, and say that it is the long shot.
    tail = ("" if cleared is None else "  (%s)" % cleared)
    mod = mouse()
    if mod is None:
        return False, ("%s is STILL open after clearing the selection%s, and the"
                       " real-keyboard fallback is not available (%s). A"
                       " targeter with no caster cannot be deselected out of."
                       % (label, tail, _MOUSE_ERR or "click.py did not load"))
    try:
        mod.key("esc")
    except Exception as e:
        return False, ("%s is STILL open after clearing the selection%s, and the"
                       " Escape fallback raised %s: %s."
                       % (label, tail, type(e).__name__, e))
    time.sleep(_CANCEL_SETTLE)
    last, why3 = targeter_state()
    if last is None:
        return False, ("cleared the selection and pressed Escape at %s, but the"
                       " targeter could not be read back (%s). Check it: python"
                       " ui.py targeter" % (label, why3 or ""))
    if last.get("active"):
        return False, ("%s is STILL open: clearing the selection did not close it"
                       " and a synthetic Escape does not reach"
                       " Targeter.ProcessInputEvents%s. Nothing left here reaches"
                       " it -- a HUMAN keypress at the window does, and so does"
                       " selecting something else."
                       % (last.get("source") or label, tail))
    return True, ("cancelled the targeter (%s): the selection clear did not take"
                  " but Escape did." % label + lost)


def _signature(payload=None):
    """What is on screen, as things that survive a re-capture.

    NOT the surface ids: those regenerate on every `get_ui_layout` (PLAYBOOK
    5a), so an id-to-id diff says everything changed every time. Type plus
    label is what a viewer would call "the same window".
    """
    raw = layout(None) if payload is None else payload
    return sorted("%s %r" % (s.get("type") or s.get("surfaceKind") or "?",
                             s.get("label") or s.get("title") or "")
                  for s in (raw.get("surfaces") or []))


class Clicked(int):
    """The click's success flag, and what it did to the screen.

    An int subclass so every existing `if ui.click(...)` keeps working, with
    the read-back attached: `opened` / `closed` are the surfaces that appeared
    and vanished across the click. A click that fires and changes nothing
    visible is a real outcome (a checkbox, an in-place edit) and says so rather
    than reading as success-in-general.
    """

    def __new__(cls, ok, opened=(), closed=(), verified=True, via="bridge",
                targeter=None, targeter_note=None, note=None):
        o = int.__new__(cls, 1 if ok else 0)
        o.ok = bool(ok)
        o.opened = list(opened)
        o.closed = list(closed)
        o.verified = bool(verified)
        # "bridge" = rimworld/click_ui_target, "mouse" = a real click.py click.
        o.via = via
        o.targeter = targeter
        o.targeter_note = targeter_note
        o.note = note
        return o

    def line(self):
        if not self.verified:
            return "not read back"
        bits = []
        if self.closed:
            bits.append("closed " + "; ".join(self.closed))
        if self.opened:
            bits.append("opened " + "; ".join(self.opened))
        state = self.targeter
        if state is not None and state.get("active"):
            bits.append("TARGETER OPEN -- %s; next: `python order.py deploy"
                        " <x> <z> --do` places it -- a raw map click is SWALLOWED"
                        " (the targeter stays open and no job starts). `python"
                        " ui.py targeter --cancel` closes it, by clearing the"
                        " selection."
                        % (state.get("source") or "unnamed targeting command"))
        if bits:
            return " / ".join(bits)
        if state is not None:
            # Both halves checked, both empty. The old line said only the first
            # half and read as "the click worked, probably".
            return ("nothing opened or closed AND no targeter -- the click landed"
                    " inside whatever was already on screen")
        tail = ("; the targeter was not read (%s)" % self.targeter_note
                if self.targeter_note else "")
        return ("nothing opened or closed -- the click landed inside whatever was"
                " already on screen" + tail)

    def __repr__(self):
        return "Clicked(%s, %s)" % ("success" if self.ok else "unconfirmed",
                                    self.line())


def click(text, exact=True, settle=0.6, surface=None, verify=True, path=None):
    """Click the element carrying `text`. TEXT FIRST, surface as a keyword.

    Returns a `Clicked` -- truthy on success, carrying the surfaces that opened
    and closed across the click AND the targeter state when that is the only
    thing the click could have changed, so a caller never has to take
    "success: true" on faith. `verify=False` skips the read-back capture.

    `path` is "bridge" or "mouse"; by default an element on the gizmo bar goes
    by the real mouse and everything else by the bridge (see the module head).
    """
    if surface is None and isinstance(text, str) and text.startswith(_SURFACE_IDS):
        raise ValueError(
            "click() takes the visible TEXT first and the surface as a keyword:"
            " ui.click(%r, surface=%r) -- %r is a surface id. (The command line"
            " is the other way round: `python ui.py <surface> click <text>`.)"
            % ("<the text>", text, text))
    raw = layout(surface)
    els = [e for s in (raw.get("surfaces") or []) for e in (s.get("elements") or [])]
    tid = find(text, exact, els=els, payload=raw)
    if not tid:
        # Name the labels that DID match. "no clickable element for 'E'" beside
        # a list containing 'E' reads as a contradiction; the truth is narrower
        # and more useful -- the text is on screen, nothing actionable pairs
        # with it, and `--probe` shows the geometry that decided that.
        hits = _matches(els, text, exact)
        detail = ("; %d label(s) matched but had no actionable partner: %s"
                  % (len(hits), ", ".join("%s at %s" % (e.get("kind"), _fmt(_rect(e)))
                                          for e in hits[:4]))
                  if hits else "; no element carries that text at all")
        # The CLICKABLE texts, not every label: the answer to "then what CAN I
        # press?" is a short list, and the old dump of every visible string was
        # mostly scenery. `--full` on a read prints the untruncated forms.
        opts = clickable_texts(raw)
        shown = ", ".join(repr(t) for t in opts[:40])
        raise LookupError(
            "no clickable UI element for %r%s. NOTHING WAS CLICKED. `python "
            "ui.py --probe %r` prints every element within 40px of it. The %d "
            "clickable text(s) on %s: %s%s%s"
            % (text, detail, text, len(opts),
               ("surface " + surface) if surface else "screen",
               shown or "(none -- nothing on screen is clickable by text)",
               "  ...+%d more" % (len(opts) - 40) if len(opts) > 40 else "",
               _free_hint(raw)))
    element, home = _locate(raw, tid)
    return _fire(tid, raw, surface, settle, verify, element=element, home=home,
                 path=path)


def _free_hint(payload):
    """The tail of a refusal: what to press when the button carries no text."""
    free = free_actionables(payload)
    if not free:
        return ""
    first = free[0]
    return ("  %d element(s) here carry NO text and cannot be reached by it -- "
            "the storyteller portraits are that shape. `python ui.py click "
            "--index N` clicks one (0-%d; [0] is a %s centred at %g,%g), `python "
            "ui.py click --rect x,y,w,h` clicks one by its rectangle, and "
            "`python click.py %g %g` is a real mouse click at that point."
            % (len(free), len(free) - 1, first["kind"], first["centre"][0],
               first["centre"][1], first["centre"][0], first["centre"][1]))


# The gizmo bar's surface, under both names the payload uses for it
# (`surfaceTargetId` is hyphenated, `surfaceKind` is not).
_GIZMO_SURFACE = ("selection-gizmos", "selection_gizmos")


def _locate(raw, tid):
    """The element carrying `tid`, and the surface row it sits on."""
    for s in (raw.get("surfaces") or []):
        for e in (s.get("elements") or []):
            if e.get("targetId") == tid:
                return e, s
    return None, None


def _is_gizmo(home):
    """Is this surface the selected-thing gizmo bar?"""
    if not home:
        return False
    return (home.get("surfaceKind") in _GIZMO_SURFACE
            or str(home.get("surfaceTargetId") or "").split(":")[0] in _GIZMO_SURFACE)


# RimWorld draws its UI in its OWN coordinate space: `UI.screenWidth` is
# `Screen.width / Prefs.UIScale`, and every rect in a `get_ui_layout` capture --
# `rect` and `screenRect` alike -- is in that space, not in OS pixels. On this
# machine the window is 4096x2160 and the UI space is 1638.4x864, a scale of
# 2.5, so the gizmo bar's own rect says y=740 for a button the real mouse has to
# be told is at y=1850. Live 2026-09-12: `ui.py click "Deploy turret"` handed
# `_centre`'s 643,777 straight to `click.py`, the real mouse clicked bare map in
# the top-left quarter of the screen, the selection was cleared and the read-back
# said `closed RimWorld.MainTabWindow_Inspect` -- no targeter, no error, and the
# 2026-09-08 turret-pack trap wearing a different hat. The bridge path is
# unaffected (it takes a targetId, not a pixel); only the real mouse needs this.
#
# The scale is not in any payload, so it is MEASURED off the `selection_gizmos`
# surface, which `GizmoGridDrawer` draws at (0, 0, UI.screenWidth,
# UI.screenHeight) -- the one surface in a capture that is the whole screen.
# Both axes must agree, or nothing is clicked: a wrong scale is a click on the
# map, which is exactly the failure this exists to stop.
_SCALE_TOL = 0.02
# Prefs.UIScale's own list. A measured ratio that is not one of these did not
# come off a full-screen surface, whatever its aspect ratio looks like: a
# 432x230 inspect pane measures 9.48 and is within 1% on both axes.
_UI_SCALES = (1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 3.5, 4.0)


def _ui_scale(surface):
    """OS pixels per UI unit, measured off a full-screen surface. -> float|None.

    `surface` is the capture's own surface dict for the element being clicked.
    None means it could not be measured and no pixel may be computed from it.
    """
    r = (surface or {}).get("screenRect") or (surface or {}).get("rect") or {}
    w, h = r.get("width") or 0, r.get("height") or 0
    if r.get("x") or r.get("y") or w <= 0 or h <= 0:
        return None
    sx, sy = SCREEN[0] / float(w), SCREEN[1] / float(h)
    if sx <= 0 or abs(sx - sy) / sx > _SCALE_TOL:
        return None
    # Snap to the setting the game actually offers: the rect is an int and
    # 4096/1638 is 2.5006, which would put a click half a pixel out at the
    # bottom of the screen and, more to the point, hides a bad measurement.
    near = min(_UI_SCALES, key=lambda c: abs(c - sx))
    return near if abs(near - sx) / near <= _SCALE_TOL else None


def _centre(element, scale=1.0):
    """The screen pixel at the middle of an element, or None.

    `scale` converts RimWorld's UI units to OS pixels; see `_ui_scale`.
    """
    r = element.get("screenRect") if element else None
    if not r or not r.get("width") or not r.get("height"):
        return None
    return (int(round((r["x"] + r["width"] / 2.0) * scale)),
            int(round((r["y"] + r["height"] / 2.0) * scale)))


def _fire(tid, raw, surface, settle, verify, element=None, home=None, path=None):
    """Click one per-capture targetId and read the screen back. -> Clicked.

    Two ways in. The bridge path (`rimworld/click_ui_target`) is the default
    and the only one for anything inside a window. The mouse path is a real
    `click.py` click at the element's own rectangle, used for the gizmo bar
    because a synthetic click there leaves the OS cursor behind and a targeter
    draws itself at the cursor -- see the module head. `path` forces either.
    """
    scale = _ui_scale(home)
    point = _centre(element, scale) if scale else None
    on_gizmo = _is_gizmo(home)
    if on_gizmo and scale is None and _centre(element) is not None:
        # Refuse rather than click at the UI coordinate: on a scaled UI that
        # pixel is somewhere else entirely, and on the gizmo bar "somewhere
        # else" is the map. Falling back to the bridge is not the answer either
        # -- it fires the gizmo without moving the cursor, which is the trap
        # the real-mouse path exists to avoid.
        return Clicked(False, verified=False, via="none", note=(
            "NOTHING WAS CLICKED. The real-mouse path needs the UI-to-pixel"
            " scale and it could not be measured off the %r surface (its rect"
            " is %r, which is not the whole %dx%d screen). A gizmo is never"
            " clicked at an unscaled UI coordinate -- that lands on the map."
            % ((home or {}).get("surfaceTargetId"),
               (home or {}).get("screenRect") or (home or {}).get("rect"),
               SCREEN[0], SCREEN[1])))
    want = path or ("mouse" if (on_gizmo and point) else "bridge")
    note = None
    if want == "mouse" and point is None:
        note = ("the real-mouse path needs a screenRect and this element has"
                " none; the bridge click was sent instead")
        want = "bridge"

    before = _signature(raw if surface is None else None) if verify else None
    via, ok = "bridge", None
    if want == "mouse":
        mod = mouse()
        if mod is None:
            note = ("the real-mouse path could not load (%s); the bridge click"
                    " was sent instead" % (_MOUSE_ERR or "click.py did not load"))
        else:
            try:
                mod.click(point[0], point[1])
                via, ok = "mouse", True
            except Exception as e:
                # A focus refusal on the GIZMO bar is not a reason to fall back:
                # the bridge fires the gizmo without moving the OS cursor, and a
                # targeting gizmo then opens pointed at wherever the cursor was
                # parked -- the 2026-09-08 trap. Refuse and say so.
                if type(e).__name__ == "FocusRefused" and on_gizmo:
                    return Clicked(False, verified=False, via="none", note=(
                        "NOTHING WAS CLICKED. The real mouse is the only path"
                        " for a gizmo -- a bridge click fires it without moving"
                        " the cursor, and a targeting gizmo then opens wherever"
                        " the cursor was left. %s" % e))
                note = ("the real mouse could not click %d,%d (%s: %s); the"
                        " bridge click was sent instead"
                        % (point[0], point[1], type(e).__name__, e))
    if via == "bridge":
        r = rim.game("rimworld/click_ui_target", {"targetId": tid})
        ok = r.get("success") if isinstance(r, dict) else r
    time.sleep(settle)
    if not verify:
        return Clicked(ok, verified=False, via=via, note=note)
    try:
        after = _signature()
    except Exception:
        return Clicked(ok, verified=False, via=via, note=note)
    b, a = list(before), list(after)
    closed = [x for x in b if x not in a]
    opened = [x for x in a if x not in b]
    # The targeter is the only other thing a click can change, and it is the
    # only one a window diff cannot see. Read it when the diff came back empty,
    # and always for a gizmo -- that is where targeting commands live.
    state, why = None, None
    if on_gizmo or not (opened or closed):
        state, why = targeter_state()
    return Clicked(ok, opened, closed, via=via, note=note, targeter=state,
                   targeter_note=why)


def click_index(index, surface=None, settle=0.6, verify=True):
    """Click the Nth TEXT-FREE actionable, numbered as the read prints them.

    The only bridge route to a button that draws an image and no label. The
    capture that numbers them is the capture that clicks, so the index cannot
    go stale between reading and pressing -- but it can differ from an EARLIER
    read, so the return says what was pressed.
    """
    raw = layout(surface)
    free = free_actionables(raw)
    if not free:
        raise LookupError(
            "no text-free actionable element is on screen. NOTHING WAS CLICKED."
            " `python ui.py` lists what is there; a button carrying text is"
            " clicked by that text.")
    if not 0 <= index < len(free):
        raise LookupError(
            "--index %d: there are %d text-free actionable element(s) on screen,"
            " numbered 0-%d. NOTHING WAS CLICKED. `python ui.py` lists them with"
            " their rectangles." % (index, len(free), len(free) - 1))
    item = free[index]
    element, home = _locate(raw, item["_tid"])
    got = _fire(item["_tid"], raw, surface, settle, verify, element=element,
                home=home)
    got.target = item
    return got


_RECT_TOL = 2.0


def click_rect(x, y, width=None, height=None, surface=None, settle=0.6, verify=True):
    """Click the actionable element AT a rectangle, or the smallest one over a point.

    Four numbers are a rectangle, matched against `screenRect` within a couple
    of pixels. Two are a screen point, and the smallest actionable containing it
    wins. Read and click share one capture; ids do not survive a re-read.
    """
    raw = layout(surface)
    els = [e for s in (raw.get("surfaces") or []) for e in (s.get("elements") or [])
           if e.get("actionable") and e.get("screenRect") and e.get("targetId")]
    if width is None or height is None:
        point = {"x": x, "y": y, "width": 0, "height": 0}
        hits = [e for e in els if _holds(_rect(e), point)]
        what = "point %g,%g" % (x, y)
    else:
        want = {"x": x, "y": y, "width": width, "height": height}
        hits = [e for e in els
                if all(abs(_rect(e)[k] - want[k]) <= _RECT_TOL
                       for k in ("x", "y", "width", "height"))]
        if not hits:
            hits = [e for e in els if _inside(want, _rect(e))]
        what = "rect %g,%g,%g,%g" % (x, y, width, height)
    if not hits:
        raise LookupError(
            "no actionable UI element at %s. NOTHING WAS CLICKED. `python ui.py`"
            " lists the text-free elements with their rectangles; `python "
            "click.py %g %g` is a real mouse click at that point instead."
            % (what, x, y))
    best = min(hits, key=lambda e: _rect(e)["width"] * _rect(e)["height"])
    element, home = _locate(raw, best["targetId"])
    got = _fire(best["targetId"], raw, surface, settle, verify, element=element,
                home=home)
    r = _rect(best)
    got.target = {"kind": best.get("kind"), "rect": [r["x"], r["y"],
                                                     r["width"], r["height"]],
                  "centre": [r["x"] + r["width"] / 2, r["y"] + r["height"] / 2],
                  "matched": what}
    return got


def row_toggle(text, col=0, surface=None):
    """Click the checkbox on the same row as a filter label.

    ThingFilter rows put the label on the left and an unlabelled toggle far to
    the right, so `_covers` never pairs them; the row test is `_on_row`, the same
    rule slim uses to print `+togs`, and the label matches on its printed form.
    Pass `surface=` to scope to the surface just read -- unscoped, a label
    repeated elsewhere on screen can win the row. Element ids are per-capture,
    so fetch labels and toggles in one pass.

    `col` picks among several toggles on one row, left to right, 0-based --
    a Wildlife row has two (tame, hunt). `slim()` prints the index to pass.
    """
    els = list(elements(surface))
    lab = next(iter(_matches(els, text)), None)
    if lab is None:
        raise LookupError(f"no label {text!r}")
    lr = _rect(lab)
    cands = [e for e in els
             if e.get("actionable") and e.get("kind") in ("checkbox", "button")
             and e.get("screenRect") and _on_row(lr, _rect(e))]
    if not cands:
        raise LookupError(f"no toggle on the row for {text!r}")
    cands.sort(key=lambda e: e["screenRect"]["x"])
    if col >= len(cands):
        raise LookupError(f"row for {text!r} has {len(cands)} toggle(s); asked for col={col}")
    t = cands[col]
    r = rim.game("rimworld/click_ui_target", {"targetId": t["targetId"]})
    time.sleep(0.5)
    return r.get("success") if isinstance(r, dict) else r


# --- the slim read -------------------------------------------------------

_CONTAINER = ("group", "scroll_view")


def _text(e):
    # Inspect panes put multi-line strings in one label; a row is one line here.
    # `_norm` is the one place that transformation lives -- find/click match on
    # its output, so the printed text and the clickable text stay the same text.
    return _norm(e.get("label"))


def _rect(e):
    return e.get("screenRect") or e.get("rect") or {"x": 0, "y": 0, "width": 0, "height": 0}


def _cy(e):
    r = _rect(e)
    return r["y"] + r["height"] / 2


def _inside(outer, inner_rect):
    cx = inner_rect["x"] + inner_rect["width"] / 2
    cy = inner_rect["y"] + inner_rect["height"] / 2
    return (outer["x"] <= cx <= outer["x"] + outer["width"] and
            outer["y"] <= cy <= outer["y"] + outer["height"])


def slim_surface(s, full=False, keep_targets=False):
    """One surface's raw dict -> the reader's view. Pure; no bridge calls.

    `keep_targets` leaves each row's `_tid` and each text-free element's `_tid`
    on the output, for a caller clicking out of the same capture it read.
    """
    els = list(s.get("elements") or [])
    srect = s.get("screenRect") or s.get("rect") or {}
    notes = []

    # 1. drop the containers; their scroll data is reported once, per surface.
    containers = [e for e in els if e.get("kind") in _CONTAINER]
    els = [e for e in els if e.get("kind") not in _CONTAINER]

    # 2. collapse repeated text draws (outline/shadow passes).
    seen, labels, dupes = {}, [], 0
    for e in els:
        t = _text(e)
        if not t:
            continue
        r = _rect(e)
        key = (t, round(r["x"] / _DUP_CELL), round(r["y"] / _DUP_CELL))
        if key in seen:
            seen[key]["dupes"] += 1
            dupes += 1
            continue
        rec = {"el": e, "text": t, "dupes": 0}
        seen[key] = rec
        labels.append(rec)
    rich = sum(1 for rec in labels if _RICH.search(rec["el"].get("label") or ""))
    labels.sort(key=lambda r: (round(_rect(r["el"])["y"]), _rect(r["el"])["x"]))

    acts = [e for e in els if e.get("actionable")]

    # 3. each label belongs to the SMALLEST actionable whose rect contains it --
    #    that is the invisible button drawn over it (the float-menu triplet, a
    #    research node, a Wildlife name cell).
    owner = {}
    for i, rec in enumerate(labels):
        cands = [a for a in acts if _inside(_rect(a), _rect(rec["el"]))]
        if not cands:  # ...or the gizmo caption drawn under the icon.
            cands = [a for a in acts if a.get("kind") == "button"
                     and _captions(a, rec["el"])]
        if cands:
            owner[i] = min(cands, key=lambda a: _rect(a)["width"] * _rect(a)["height"])

    rows, by_act = [], {}
    for i, rec in enumerate(labels):
        a = owner.get(i)
        if a is None:
            row = {"text": rec["text"], "act": False, "role": None, "checked": None,
                   "disabled": None, "toggles": [], "dupes": rec["dupes"],
                   "parts": [rec["text"]], "own": None, "_tid": None,
                   "_rect": _rect(rec["el"])}
            rows.append(row)
            continue
        key = id(a)
        row = by_act.get(key)
        if row is None:
            row = {"text": rec["text"], "act": True, "role": a.get("kind"),
                   "checked": a.get("isChecked"), "disabled": a.get("disabled"),
                   "toggles": [], "dupes": rec["dupes"], "parts": [rec["text"]],
                   "own": None, "_tid": a.get("targetId"), "_rect": _rect(a)}
            by_act[key] = row
            rows.append(row)
        else:
            row["text"] += " " + rec["text"]
            row["parts"].append(rec["text"])
            row["dupes"] += rec["dupes"]

    # (A labelled actionable -- `checkbox_labeled` and friends -- needs no case of
    # its own: it is its own label, so step 3 makes it the owner of itself.)

    rows.sort(key=lambda r: (round(r["_rect"]["y"]), r["_rect"]["x"]))

    # 4. an unowned text on the same row as a row to its left is a COLUMN of that
    #    row (Wildlife's "50%"), not a line of its own.
    merged = []
    for row in rows:
        if merged and not row["act"]:
            prev = merged[-1]
            pc = prev["_rect"]["y"] + prev["_rect"]["height"] / 2
            rc = row["_rect"]["y"] + row["_rect"]["height"] / 2
            if abs(pc - rc) <= _ROW_TOL and prev["_rect"]["x"] <= row["_rect"]["x"]:
                # `own` is the row before a neighbour was folded in, so a reader
                # -- and find() -- can still address the button on its own.
                if prev["own"] is None:
                    prev["own"] = prev["text"]
                prev["text"] += "  " + row["text"]
                prev["parts"].extend(row["parts"])
                prev["dupes"] += row["dupes"]
                continue
        merged.append(row)
    rows = merged

    # 5. text-free actionables: attach as a toggle to the nearest row on the left
    #    that `_on_row` puts them on (row_toggle's own rule), else bucket them.
    free = []
    for a in acts:
        if id(a) in by_act:
            continue
        ar = _rect(a)
        host = None
        for row in rows:
            if _on_row(row["_rect"], ar):
                if host is None or row["_rect"]["x"] > host["_rect"]["x"]:
                    host = row
        if host is not None:
            host["toggles"].append({"x": ar["x"], "role": a.get("kind"),
                                    "checked": a.get("isChecked"),
                                    "disabled": a.get("disabled")})
        else:
            free.append(a)
    for row in rows:
        row["toggles"].sort(key=lambda t: t["x"])
        for n, t in enumerate(row["toggles"]):
            t["col"] = n
            t.pop("x")
            for k in ("checked", "disabled"):
                if t[k] is None:
                    del t[k]

    # 6. flags: repeated text (click-by-text is ambiguous), off the surface rect.
    counts = {}
    for row in rows:
        counts[row["text"]] = counts.get(row["text"], 0) + 1
    trunc = 0
    for row in rows:
        row["ambiguous"] = counts[row["text"]] > 1
        row["offscreen"] = bool(srect) and not _inside(srect, row["_rect"])
        if not full and len(row["text"]) > _TRUNC:
            row["text"] = row["text"][:_TRUNC] + "...(+%d chars)" % (len(row["text"]) - _TRUNC)
            trunc += 1
        row.pop("_rect")
        if not keep_targets:
            row.pop("_tid")
        # Prune the falsey keys: absent means no, and `checked: null` is not the
        # same as absent -- it is the indeterminate a parent checkbox reports, so
        # that one key survives when the element is a checkbox at all.
        for k in ("act", "ambiguous", "offscreen", "disabled", "dupes"):
            if not row[k]:
                del row[k]
        for k in ("role", "toggles"):
            if not row[k]:
                del row[k]
        if len(row["parts"]) < 2:
            del row["parts"]
        if row["own"] is None:
            del row["own"]
        # A radio row keeps `checked` even when it is null: the layout carries no
        # chosen flag for `Widgets.RadioButtonLabeled`, and "not reported" is a
        # thing a reader has to be told, not a key to drop.
        if row["checked"] is None and row.get("role") not in ("checkbox",
                                                              "radio_button"):
            del row["checked"]

    scroll = []
    for c in containers:
        sc = c.get("scroll") or {}
        if sc.get("canScrollX") or sc.get("canScrollY"):
            scroll.append({
                "axis": ("x" if sc.get("canScrollX") else "") + ("y" if sc.get("canScrollY") else ""),
                "offsetX": sc.get("offsetX"), "maxOffsetX": sc.get("maxOffsetX"),
                "offsetY": sc.get("offsetY"), "maxOffsetY": sc.get("maxOffsetY"),
                "content": [sc.get("contentRect", {}).get("width"),
                            sc.get("contentRect", {}).get("height")],
                "viewport": [sc.get("viewportRect", {}).get("width"),
                             sc.get("viewportRect", {}).get("height")]})

    freekinds = {}
    for a in free:
        freekinds[a.get("kind")] = freekinds.get(a.get("kind"), 0) + 1
    freerows = len({round(_cy(a) / _ROW_TOL) for a in free})
    # The rects, not just the count: a text-free button has no other handle, and
    # a bare "3 elements carry no text" left the storyteller portraits unclickable.
    freeitems = []
    for a in sorted(free, key=lambda e: (round(_rect(e)["y"]), _rect(e)["x"])):
        r = _rect(a)
        item = {"kind": a.get("kind"),
                "rect": [r["x"], r["y"], r["width"], r["height"]],
                "centre": [r["x"] + r["width"] / 2, r["y"] + r["height"] / 2],
                "checked": a.get("isChecked"), "disabled": bool(a.get("disabled"))}
        if keep_targets:
            item["_tid"] = a.get("targetId")
        freeitems.append(item)

    if dupes:
        notes.append("%d duplicate text draws collapsed (same text within %gpx)"
                     % (dupes, _DUP_CELL))
    if rich:
        notes.append("%d labels had rich-text markup stripped" % rich)
    if trunc:
        notes.append("%d texts truncated at %d chars (pass full=True)" % (trunc, _TRUNC))
    if containers:
        notes.append("%d layout containers dropped (geometry only)" % len(containers))
    notes.append("dropped from every row: targetId, rect, screenRect, clipCapable, "
                 "depth, source -- ids are per-capture, re-find by text")

    out = {"id": s.get("surfaceTargetId"), "label": s.get("label") or s.get("title"),
           "kind": s.get("surfaceKind"), "type": s.get("type"),
           "elements": s.get("elementCount"), "actionable": s.get("actionableElementCount"),
           "rows": rows, "scroll": scroll,
           "textFree": {"count": len(free), "kinds": freekinds, "rows": freerows,
                        "items": freeitems},
           "hidden": notes}
    sem = s.get("semanticDetails") or {}
    if sem.get("tabs"):
        out["tabs"] = [{"label": t.get("label"), "open": t.get("isOpen")}
                       for t in sem["tabs"]]
    return out


def slim(surface=None, payload=None, full=False, keep_targets=False):
    """Reader's view of one surface (or of everything on screen).

    Returns {capture, surfaces:[...]}. Pass `payload` to re-slim a layout you
    already fetched (that is how the self-measure below avoids a second call).

    Text-free elements are numbered ACROSS surfaces, in the order they print,
    because `click --index` takes one number and the screen is one screen.
    """
    raw = payload if payload is not None else layout(surface)
    out = {"capture": raw.get("captureId"),
           "surfaces": [slim_surface(s, full, keep_targets)
                        for s in (raw.get("surfaces") or [])]}
    n = 0
    for s in out["surfaces"]:
        for item in s["textFree"]["items"]:
            item["index"] = n
            n += 1
    return out


def render(sl, raw_bytes=None):
    """The slim view as text. This is what a reader actually loads."""
    L = []
    if not sl["surfaces"]:
        L.append("no UI surfaces open: checked, not assumed")
    for s in sl["surfaces"]:
        L.append("")
        L.append("SURFACE %s  %r  %s  %s"
                 % (s["id"], s["label"], s["kind"], s["type"]))
        L.append("  %s elements -> %d rows, %s actionable"
                 % (s["elements"], len(s["rows"]), s["actionable"]))
        if s.get("tabs"):
            L.append("  tabs: " + "  ".join(("[%s]" if t["open"] else "%s") % t["label"]
                                            for t in s["tabs"]))
        for sc in s["scroll"]:
            L.append("  scroll %s: at x %s/%s y %s/%s, content %sx%s in viewport %sx%s"
                     % (sc["axis"], sc["offsetX"], sc["maxOffsetX"], sc["offsetY"],
                        sc["maxOffsetY"], sc["content"][0], sc["content"][1],
                        sc["viewport"][0], sc["viewport"][1]))
        if not s["rows"]:
            L.append("  no readable text on this surface: checked, not assumed")
        for row in s["rows"]:
            if "checked" in row:
                mark = {True: "[x]", False: "[ ]"}.get(row["checked"], "[?]")
            elif row.get("act"):
                mark = " * "
            else:
                mark = " . "
            line = "  %s %s" % (mark, row["text"])
            togs = row.get("toggles") or []
            if togs:
                # Run-length: a Work-tab pawn row is 20 identical unread cells,
                # and "0-19[?]" says that in eight characters.
                runs, marks = [], [{True: "[x]", False: "[ ]"}.get(t.get("checked"), "[?]")
                                   for t in togs]
                i = 0
                while i < len(marks):
                    j = i
                    while j + 1 < len(marks) and marks[j + 1] == marks[i]:
                        j += 1
                    runs.append(("%d" % i if i == j else "%d-%d" % (i, j)) + marks[i])
                    i = j + 1
                line += "  +togs: " + " ".join(runs)
            if row.get("disabled"):
                line += "  DISABLED"
            if row.get("ambiguous"):
                line += "  (text not unique)"
            if row.get("offscreen"):
                line += "  ~offsurface"
            L.append(line)
        # A row can be a button plus a separate label that happens to sit on the
        # same line -- a difficulty name beside a storyteller. Say which rows and
        # which half is the button; both forms click.
        joined = [r for r in s["rows"] if r.get("own")]
        if joined:
            L.append("  MERGED %d of %d rows put a button and a separate label on"
                     " one line. The button half of each: %s%s. Either form"
                     " clicks."
                     % (len(joined), len(s["rows"]),
                        ", ".join(repr(r["own"]) for r in joined[:8]),
                        "  ...+%d more" % (len(joined) - 8) if len(joined) > 8
                        else ""))
        if any(r.get("role") == "radio_button" and r.get("checked") is None
               for r in s["rows"]):
            L.append("  RADIO rows read [?]: no chosen flag was reported by"
                     " this bridge build. Update the bridge or use"
                     " `python see.py` for the highlight.")
        tf = s["textFree"]
        if tf["count"]:
            L.append("  TEXT-FREE %d actionable elements carry no text (%s) on %d rows"
                     " -- not reachable by text. Click one by number:"
                     " `python ui.py click --index N`"
                     % (tf["count"],
                        ", ".join("%s x%d" % kv for kv in sorted(tf["kinds"].items())),
                        tf["rows"]))
            for item in tf["items"][:12]:
                L.append("    [%d] %-14s%s x=%g y=%g w=%g h=%g  centre %g,%g%s"
                         % (item["index"], item["kind"],
                            {True: " [x]", False: " [ ]"}.get(item.get("checked"), ""),
                            item["rect"][0],
                            item["rect"][1], item["rect"][2], item["rect"][3],
                            item["centre"][0], item["centre"][1],
                            "  DISABLED" if item["disabled"] else ""))
            if len(tf["items"]) > 12:
                L.append("    ...+%d more (--json for all)"
                         % (len(tf["items"]) - 12))
        for n in s["hidden"]:
            L.append("  HIDDEN " + n)
    L.append("")
    L.append("LEGEND  * clickable: ui.click(\"text\")   [ ]/[x]/[?] checkbox state"
             "   +togN: unlabelled toggle on that row: ui.row_toggle(\"text\", col=N)"
             "   [?] = indeterminate, expand it   ~offsurface = outside the surface"
             " rect, scroll first   . = text only")
    L.append("ACT IN A FRESH CAPTURE: every targetId regenerates per get_ui_layout"
             " (PLAYBOOK 5a), so ids are not carried here; click/row_toggle re-read"
             " and match the text above. A row printed above clicks by the text as"
             " printed, joins and all; a TEXT-FREE element clicks by --index or"
             " --rect, both of which re-read too.")
    body = "\n".join(L)
    head = "UI SLIM  capture %s  surfaces %d" % (sl.get("capture"), len(sl["surfaces"]))
    if raw_bytes:
        # Measure the thing a reader pays for -- the rendered text, not the JSON.
        # Two passes because the size is printed inside the text it measures.
        for _ in range(2):
            n = len(body) + len(head) + 1
            head = ("UI SLIM  capture %s  surfaces %d  raw %d B -> slim %d B (%.1f%%)"
                    % (sl.get("capture"), len(sl["surfaces"]), raw_bytes, n,
                       100.0 * n / raw_bytes))
    return head + "\n" + body


def main_tabs():
    """The bottom tab bar, which `get_ui_layout` never lists as a surface.

    A row with no `type` is a MainButtonDef whose worker toggles something
    (World) rather than opening a tab window; open_main_tab null-refs on those.
    """
    r = rim.game("rimworld/list_main_tabs", {"includeHidden": True},
                 strict=False)
    if not isinstance(r, dict):
        return []
    return r.get("tabs") or r.get("mainTabs") or []


def print_main_tabs():
    rows = main_tabs()
    if not rows:
        print("main tab bar: not reported by rimworld/list_main_tabs")
        return
    print("-- main tab bar (%d) --" % len(rows))
    for t in rows:
        kind = t.get("type") or ""
        # A tab left OPEN is drawn over the map and is the thing that gets
        # stuck (Research, Quests -- BUGS 2026-09-05). Say so here, where a
        # reader is already asking what is on screen.
        print("%-44s %-18s %s%s"
              % (t.get("targetId") or t.get("mainTabId") or "?",
                 "TOGGLE" if not kind else "tabWindow",
                 (t.get("label") or t.get("defName") or "?")
                 + ("" if kind else
                    "  -- a toggle, not a tab window: open_main_tab cannot open"
                    " it and it has no clickable ui-element id")
                 + ("  -- FORCE-PAUSES the game while open" if _force_pauses(t)
                    else ""),
                 "  [OPEN over the map -- `python ui.py close`]"
                 if t.get("isOpen") else ""))


# --- main tabs: open, read, CLOSE -------------------------------------------
#
# `rimworld/open_main_tab` is not a toggle. Live on 2026-09-05 it opened the
# Research tab (482 elements over the map), and calling it again did nothing --
# the tab stays up until `rimworld/close_main_tab` escapes it. The Quests tab
# got stuck open the same way and carried no `Close` element to click. So every
# path here that OPENS a tab to read it closes it again unless asked not to.

OPEN_TOOL = "rimworld/open_main_tab"
CLOSE_TOOL = "rimworld/close_main_tab"

# Main tab windows that set `forcePause`: while one is open the clock is held,
# and supervised play's watcher stops on any force pause. Matched as a substring
# of the tab's `type`. The list is the warning BEFORE the read; the check after
# it is the fact, and it catches anything not named here.
FORCE_PAUSE_TABS = ("MainTabWindow_Menu",)

RESTART_LINE = "python play.py start"


def _force_pauses(row):
    t = ((row or {}).get("type") or "")
    return any(name in t for name in FORCE_PAUSE_TABS)


def _clock_state():
    """The clock, or None when clock.py could not answer. Never raises."""
    try:
        import clock
        return clock.state()
    except Exception:
        return None


def _tab_row(name):
    """The `list_main_tabs` row `name` names, or None. Id, defName or label."""
    want = (name or "").strip().lower()
    if not want:
        return None
    for t in main_tabs():
        for key in ("targetId", "mainTabId", "defName", "label"):
            v = (t.get(key) or "").strip().lower()
            if v and (v == want or v == "main-tab:" + want):
                return t
    return None


def open_tab(name):
    """Open one main tab. Refuses a TOGGLE tab rather than null-reffing on it.

    A row whose `type` is empty is a MainButtonDef whose worker toggles
    something instead of opening a tab window -- World is the one -- and
    `open_main_tab` throws a NullReferenceException inside the bridge on those.
    Named here with the instrument that answers the question instead.
    """
    row = _tab_row(name)
    if row is not None and not (row.get("type") or ""):
        raise ValueError(
            "%r is a TOGGLE main tab, not a tab window: it has no tab window "
            "for open_main_tab to open (it null-refs) and no ui-element id to "
            "click. The World map is read by `python world.py` instead."
            % (row.get("label") or row.get("defName") or name))
    tid = (row or {}).get("targetId") or (row or {}).get("mainTabId") or name
    return rim.game(OPEN_TOOL, {"mainTabId": tid}, strict=False)


def close_tab(name=None):
    """Close the open main tab. `name` asserts WHICH tab is expected open.

    This is the real close -- calling `open_main_tab` again does not toggle.
    """
    args = {}
    if name:
        row = _tab_row(name)
        args["mainTabId"] = ((row or {}).get("targetId")
                             or (row or {}).get("mainTabId") or name)
    return rim.game(CLOSE_TOOL, args, strict=False)


def _open_tab_name(reply):
    """`openMainTabId` out of a main-tab command reply's after-state, or None."""
    after = (reply or {}).get("after") or {}
    return after.get("openMainTabId") or after.get("openMainTabDefName")


def read_tab(name, full=False, close=True):
    """Open a main tab, slim-read it, and close it again. -> (text, closed_ok).

    `close=False` leaves it standing -- the only way a tab should ever be left
    over the map, and then deliberately.

    Some tab windows force-pause the game -- Menu does, twice on 2026-09-07 --
    and supervised play stops on any force pause, silently. So this warns before
    it opens one, and afterwards checks whether the service is still there and
    whether the clock is still running, printing the line that restarts it.
    """
    row = _tab_row(name)
    label = (row or {}).get("label") or name
    warn = _force_pauses(row)
    alive = supervised_play_alive()
    if warn:
        print("   ~~ %s FORCE-PAUSES the game while it is open%s. Opening it now;"
              " it is closed again after the read and the clock is checked then."
              % (label, " and supervised play stops on any force pause"
                        if alive else ""))
    opened = open_tab(name)
    if isinstance(opened, dict) and opened.get("success") is False:
        raise RuntimeError("open_main_tab %r refused: %s"
                           % (name, opened.get("message") or opened))
    try:
        raw = layout(None)
        text = render(slim(payload=raw, full=full),
                      raw_bytes=len(json.dumps(raw, separators=(",", ":"))))
    finally:
        shut = close_tab(name) if close else None
    _report_pause(label, warn, alive)
    if not close:
        return text, None
    ok = bool(isinstance(shut, dict) and shut.get("success")
              and not _open_tab_name(shut))
    return text, ok


def _report_pause(label, warn, alive):
    """Say what opening this tab cost the clock, or nothing when it cost nothing.

    The service check is a disk read and catches ANY tab that force-pauses, named
    in FORCE_PAUSE_TABS or not; the clock read is the second opinion and is only
    paid for a tab already known to hold it.
    """
    if alive and supervised_play_alive() is False:
        print("   !! supervised play STOPPED while %s was open -- the watcher"
              " stops on any force pause, and nothing restarts it: %s"
              % (label, RESTART_LINE))
        return
    if not warn:
        return
    st = _clock_state()
    if st is None:
        print("   ~~ %s force-pauses, and the clock could not be read after it"
              " closed. Check it: python status.py" % label)
    elif st.get("state") == "paused":
        print("   !! the clock is STOPPED now (%s). %s"
              % (st.get("why") or "reason unread", RESTART_LINE))


def selection():
    """What is selected on the map, as the game reports it."""
    r = rim.game("rimworld/get_selection_semantics", {}, strict=False)
    if not isinstance(r, dict):
        return "UNREADABLE (%s)" % str(r)[:80]
    if not r.get("hasSelection"):
        return "nothing selected"
    rows = r.get("selectedObjects") or []
    if not rows:
        return "%s object(s), none described" % r.get("selectedCount")
    return "; ".join("%s %s [%s]"
                     % (o.get("kind") or "?",
                        o.get("label") or o.get("inspectLabel") or "?",
                        o.get("id") or "no id") for o in rows[:6])


def _do_click(text, surface, path=None):
    """One click from the command line, with its read-back. -> exit code."""
    try:
        result = click(text, surface=surface, path=path)
    except Exception as e:
        # A refusal is not a crash: the message names the condition and lists
        # what could have been clicked instead. NOTHING WAS CLICKED is in it.
        print("ui.py click REFUSED -- %s: %s" % (type(e).__name__, e))
        return 1
    print("clicked %r%s: %s"
          % (text, (" on " + surface) if surface else "", _verdict(result)))
    if result.note:
        print("  !! %s" % result.note)
    print("  read back: %s" % result.line())
    return 0 if result.ok else 1


def _verdict(result):
    """How the click was delivered, and whether it was confirmed."""
    if result.via == "mouse":
        return "the REAL MOUSE clicked it (the gizmo bar goes that way)"
    if result.via == "none":
        return "NOTHING WAS CLICKED"
    return "success" if result.ok else "the bridge did not confirm success"


def _do_geometry_click(kind, value, surface):
    """`click --index N` / `click --rect x,y[,w,h]`. -> exit code."""
    try:
        if kind == "index":
            result = click_index(int(value), surface=surface)
        else:
            nums = [float(n) for n in str(value).replace(" ", "").split(",") if n]
            if len(nums) not in (2, 4):
                print("python ui.py click --rect x,y,w,h   (or --rect x,y for a"
                      " point: the smallest actionable over it wins)")
                return 1
            result = click_rect(*nums, surface=surface)
    except ValueError as e:
        print("ui.py click --%s REFUSED -- %s" % (kind, e))
        return 1
    except Exception as e:
        print("ui.py click --%s REFUSED -- %s: %s" % (kind, type(e).__name__, e))
        return 1
    t = getattr(result, "target", None) or {}
    print("clicked the %s at x=%g y=%g w=%g h=%g (centre %g,%g): %s"
          % (t.get("kind") or "element", t.get("rect", [0, 0, 0, 0])[0],
             t.get("rect", [0, 0, 0, 0])[1], t.get("rect", [0, 0, 0, 0])[2],
             t.get("rect", [0, 0, 0, 0])[3], t.get("centre", [0, 0])[0],
             t.get("centre", [0, 0])[1], _verdict(result)))
    if result.note:
        print("  !! %s" % result.note)
    print("  read back: %s" % result.line())
    return 0 if result.ok else 1


def _path_flag(flags):
    """`--bridge` / `--pixel` -> the click path to force, or None for the default."""
    if "--bridge" in flags:
        return "bridge"
    if "--pixel" in flags:
        return "mouse"
    return None


def _take_option(argv, flag):
    """Pull `--flag value` (or `--flag=value`) out of argv. -> (value, rest)."""
    for i, a in enumerate(argv):
        if a == flag:
            return (argv[i + 1] if i + 1 < len(argv) else ""), argv[:i] + argv[i + 2:]
        if a.startswith(flag + "="):
            return a[len(flag) + 1:], argv[:i] + argv[i + 1:]
    return None, argv


def main():
    argv = sys.argv[1:]
    geom = None
    for name in ("index", "rect"):
        got, argv = _take_option(argv, "--" + name)
        if got is not None:
            geom = (name, got)
    args = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}
    surface = args[0] if args else None
    # Every other instrument's CLI opens with this and ui.py did not. A read
    # that runs before the MCP handshake is the shape the "silent no-op from
    # the CLI while import ui works" bug had: in-process callers had already
    # initialised, the command line never did.
    try:
        rim.init()
    except Exception as e:
        print("ui.py: rim.init() FAILED (%s: %s) -- the bridge is not answering;"
              " nothing below can read or click." % (type(e).__name__, e))
        return 1
    # Accept the natural surface-first form used by the rest of this module's
    # API: `ui.py <surface> click <text>`, plus `ui.py click <text>`.
    if geom is not None:
        # A text-free button has no text to click by; --index and --rect are the
        # two handles the layout does give.
        i = args.index("click") if "click" in args else len(args)
        return _do_geometry_click(geom[0], geom[1], args[0] if i > 0 else None)
    if args and args[0] == "targeter":
        if "--cancel" in flags:
            # cancel_targeter reads the state itself; reading it here too
            # would put a round trip between the look and the keypress.
            ok, text = cancel_targeter()
            print(text)
            return 0 if ok else 1
        state, why = targeter_state()
        print(targeter_line(state, why))
        return 0 if state is not None else 1
    if "click" in args:
        i = args.index("click")
        click_surface = args[0] if i > 0 else None
        click_text = " ".join(args[i + 1:])
        if not click_text:
            print("python ui.py [surface] click <visible text>")
            return 1
        return _do_click(click_text, click_surface, path=_path_flag(flags))
    if "--click" in flags:
        if not args:
            print("python ui.py --click <visible text>")
            return 1
        # Keep the command-line path exactly aligned with the public Python API:
        # capture a fresh layout, resolve the visible text, and click its
        # per-capture target id.  Previously "OK" was mistaken for a surface id.
        return _do_click(args[0], None, path=_path_flag(flags))
    if args and args[0] in ("close", "tab"):
        name = " ".join(args[1:]) or None
        if args[0] == "close" or "--close" in flags:
            r = close_tab(name)
            if not (isinstance(r, dict) and r.get("success")):
                print("close_main_tab REFUSED: %s"
                      % ((r or {}).get("message") if isinstance(r, dict) else r))
                return 1
            still = _open_tab_name(r)
            print("closed the main tab%s. Open now: %s"
                  % ((" " + name) if name else "", still or "none"))
            return 0 if not still else 1
        if not name:
            print("python ui.py tab <name> [--keep-open] [--close]")
            return 1
        keep = "--keep-open" in flags
        try:
            text, closed = read_tab(name, full="--full" in flags, close=not keep)
        except Exception as e:
            print("ui.py tab REFUSED -- %s: %s" % (type(e).__name__, e))
            return 1
        print(text)
        if keep:
            print("-- %s LEFT OPEN over the map (--keep-open). Close it with"
                  " `python ui.py close`." % name)
        elif closed:
            print("-- %s was opened, read and CLOSED again." % name)
        else:
            print("-- !! %s was opened and read, but close_main_tab did not "
                  "confirm it closed. It may still be over the map: `python "
                  "ui.py close`." % name)
            return 1
        return 0
    if "--probe" in flags:
        # `--probe <text>`: the label's neighbourhood, for the case where a
        # label is plainly visible and nothing clickable pairs with it.
        if not args:
            print("python ui.py --probe <visible text>")
            return
        print(probe(args[0]))
        return
    if "--selection" in flags:
        print("selected: %s" % selection())
        return
    if "--surfaces" in flags:
        raw = layout(None)
        for s in raw.get("surfaces") or []:
            print("%-44s %-18s els=%-5s act=%-5s %s"
                  % (s.get("surfaceTargetId"), s.get("surfaceKind"),
                     s.get("elementCount"), s.get("actionableElementCount"),
                     s.get("label") or s.get("type")))
        if not (raw.get("surfaces") or []):
            print("no UI surfaces open: checked, not assumed")
        print_main_tabs()
        return
    raw = layout(surface)
    sl = slim(payload=raw, full="--full" in flags)
    if "--json" in flags:
        print(json.dumps(sl, indent=1))
        return
    print(render(sl, raw_bytes=len(json.dumps(raw, separators=(",", ":")))))


if __name__ == "__main__":
    main()
