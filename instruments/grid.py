"""A coordinate grid drawn on the map, in the game, where the stream can see it.

  python grid.py                     # read the state, change nothing
  python grid.py on                  # DRY RUN -- says what it would set
  python grid.py on --do             # actually draw the grid
  python grid.py on --step 5 --do    # a line every 5 cells instead of 10
  python grid.py on --no-labels --do # lines only, no x,z text
  python grid.py off --do            # take it down
  python grid.py --color yellow --alpha 0.6 --do   # restyle without toggling
  python grid.py --json              # the whole payload

  import grid
  grid.state()          grid.on(step=10, do=True)          grid.off(do=True)

## What it is for

Chat can see the map and not the cell numbers; we read the cell numbers and
never see the map. The grid is the shared vocabulary: a viewer says "104,127"
and hands can act on it with no screenshot round-trip, and a cell we name can be
checked against the picture.

**This is visible to the stream.** It draws inside the game, over the terrain
and under every piece of UI, so the capture carries it. Turning it on changes
what viewers see; turning it off costs nothing to leave off -- the per-frame
hook is one flag read and a return while the grid is down.

## Reading the numbers

Lines sit on cell BOUNDARIES, so the label `100,120` marks the corner of cell
100,120 and the cell it names is up and to the right of the text.

`step` is what you ask for. `effectiveStep` is what the last frame drew: zoomed
out, the spacing doubles until the view holds a readable number of lines, and
the labels thin out further still. Both are in the payload, so a grid that looks
coarser than you asked for is explained rather than mysterious.

`framesDrawn` climbing is the proof the draw hook is alive. `patch.installed`
false is the one way the grid can be switched on and invisible; the reason is in
`patch.error`.
"""
import json
import sys

import rim

TOOL = "home/grid"


def state():
    """The overlay state, read and nothing else."""
    return rim.game(TOOL, {}, strict=False)


def _write(do=False, **fields):
    args = dict((k, v) for k, v in fields.items() if v is not None)
    args["dryRun"] = not do
    return rim.game(TOOL, args, strict=False)


def on(step=None, labels=None, alpha=None, color=None, do=False):
    """Turn the grid on. A dry run unless do=True."""
    return _write(do=do, enabled=True, step=step, labels=labels,
                  alpha=alpha, color=color)


def off(do=False):
    """Take the grid down. A dry run unless do=True."""
    return _write(do=do, enabled=False)


def restyle(step=None, labels=None, alpha=None, color=None, do=False):
    """Change how it looks without touching whether it is on."""
    return _write(do=do, step=step, labels=labels, alpha=alpha, color=color)


def _line(s):
    """The one-line state: `GRID ON  step 10  labels on  white @ 0.35`."""
    return "%s  step %s  labels %s  %s @ %s" % (
        "ON " if s.get("enabled") else "OFF",
        s.get("step"), "on" if s.get("labels") else "off",
        s.get("color"), s.get("alpha"))


def render(r):
    if r.get("success") is False:
        print("GRID REFUSED: %s" % (r.get("error") or r.get("message")
                                    or "no reason given"))
        return 1

    before, after = r.get("before") or {}, r.get("after") or {}
    would = r.get("wouldBe") or {}
    if r.get("applied"):
        print("GRID %s" % _line(after))
        if _line(before) != _line(after):
            print("  was     : %s" % _line(before))
    elif r.get("dryRun") and _line(would) != _line(before):
        print("GRID %s   (DRY RUN -- nothing was set)" % _line(would))
        print("  now     : %s" % _line(before))
        print("  add --do to draw it")
    else:
        print("GRID %s" % _line(after))

    for c in r.get("clamped") or []:
        print("  clamped : %s %s -> %s (range %s)"
              % (c.get("field"), c.get("asked"), c.get("used"), c.get("range")))

    patch = r.get("patch") or {}
    if not patch.get("installed"):
        print("  !! the draw hook is NOT installed, so nothing will appear: %s"
              % (patch.get("error") or "no reason given"))

    render_block = r.get("render") or {}
    frames = render_block.get("framesDrawn") or 0
    if after.get("enabled"):
        if frames:
            view = render_block.get("viewRect") or {}
            print("  drawing : %s frames, last %s ms ago | %s lines, %s labels"
                  % (frames, render_block.get("lastDrawnMsAgo"),
                     render_block.get("linesLastFrame"),
                     render_block.get("labelsLastFrame")))
            print("  in view : %s,%s + %sx%s cells | spacing %s, labels every %s"
                  % (view.get("x"), view.get("z"), view.get("width"),
                     view.get("height"), render_block.get("effectiveStep"),
                     render_block.get("labelStep")))
        else:
            print("  drawing : no frame yet -- the game may be paused on a "
                  "window, on the world view, or have no map")
    if render_block.get("errors"):
        print("  !! %s draw errors, last: %s"
              % (render_block["errors"], render_block.get("lastError")))

    m = r.get("map") or {}
    if m.get("hasMap"):
        print("  map     : %s, %s x %s cells"
              % (m.get("mapName") or "?", m.get("sizeX"), m.get("sizeZ")))
    else:
        print("  map     : none loaded (the overlay state is still readable)")

    for arg in r.get("unknownArguments") or []:
        print("  !! %s ignored an argument it does not know: %s" % (TOOL, arg))
    return 0


def _take(argv, name, default=None):
    """Pull `--name value` out of argv. Returns (value or default, remaining)."""
    if name not in argv:
        return default, argv
    i = argv.index(name)
    if i + 1 >= len(argv):
        raise SystemExit("%s needs a value" % name)
    return argv[i + 1], argv[:i] + argv[i + 2:]


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    as_json = "--json" in argv
    do = "--do" in argv
    labels = False if "--no-labels" in argv else (True if "--labels" in argv else None)
    argv = [a for a in argv
            if a not in ("--json", "--do", "--labels", "--no-labels")]
    step, argv = _take(argv, "--step")
    alpha, argv = _take(argv, "--alpha")
    color, argv = _take(argv, "--color")

    verb = None
    if argv and argv[0] in ("on", "off"):
        verb, argv = argv[0], argv[1:]
    if argv:
        print("grid.py does not take %r; see --help" % " ".join(argv))
        return 2

    try:
        step = int(step) if step is not None else None
        alpha = float(alpha) if alpha is not None else None
    except ValueError as e:
        print("grid.py FAILED  %s" % e)
        return 2

    wants_write = (verb is not None or step is not None or alpha is not None
                   or color is not None or labels is not None)
    if do and not wants_write:
        print("grid.py --do with nothing to set; say `on`, `off`, or a "
              "--step/--alpha/--color/--no-labels to change")
        return 2

    rim.init()
    try:
        if verb == "on":
            r = on(step=step, labels=labels, alpha=alpha, color=color, do=do)
        elif verb == "off":
            r = off(do=do)
        elif wants_write:
            r = restyle(step=step, labels=labels, alpha=alpha, color=color, do=do)
        else:
            r = state()
    except (rim.BridgeError, ValueError) as e:
        print("GRID FAILED  %s" % e)
        return 1
    if not isinstance(r, dict):
        print("GRID FAILED  %s did not answer with a payload (%r)"
              % (TOOL, str(r)[:200]))
        return 1
    if as_json:
        print(json.dumps(r, indent=1))
        return 0
    return render(r)


if __name__ == "__main__":
    try:
        import overlay_client as _ov
        sys.argv[1:], _say, _mood = _ov.take_flags(sys.argv[1:])
    except Exception:
        _ov, _say, _mood = None, None, None
    _rc = main()
    if _ov is not None:
        _ov.say_flags(_say, _mood)
    sys.exit(_rc)
