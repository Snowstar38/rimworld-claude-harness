"""The world map, read without opening it.

  python world.py                  # the colony tile: biome, temperature, growing period
  python world.py --tile 42371     # some other tile on the same planet layer
  python world.py --near 30        # settlements within 30 tiles instead of 15
  python world.py --show 8         # ALSO show the planet for 8 s, then hide it
  python world.py --json           # the whole payload

## Why this exists

The World tab cannot be reached through the UI tools. `rimworld/list_main_tabs`
reports World with `"type": ""` because it is a `MainButtonDef` whose worker
toggles the planet renderer rather than opening a tab window, so
`open_main_tab` null-refs on both `main-tab:World` and `World`,
`click_ui_target` has no ui-element id to click, and `ui.py --surfaces` never
listed the tab bar at all.

Every fact the world inspect pane shows is readable off `Find.WorldGrid` with
the colony map still on screen, so `home/world` reads it there instead. Nothing
is opened, nothing is clicked, and the answer arrives in one call.

`--show` is the only thing here that changes anything: it toggles the planet
view on for N seconds and hides it again. Everything else is a read.

## Reading the numbers

`growingPeriodDays` counts the twelfths of the year whose AVERAGE temperature
falls inside the growing range (6-42 C), times five days each -- the same
number the world inspect pane prints. **0 days is a real answer**: nothing grows
outdoors on that tile, ever, and a hydroponics bay is the only crop there is.

`temperature` is the tile's seasonal temperature, not the colony map's local
one. A room's temperature is `temp.py` / `home/get_temperatures`; this is the
weather the tile gets.
"""
import json
import sys

import rim

TOOL = "home/world"


def read(tile=None, near=15, show=0):
    args = {"settlementRadius": int(near)}
    if tile is not None:
        args["tile"] = int(tile)
    if show:
        args["show"] = True
        args["watchSeconds"] = int(show)
        args["dryRun"] = False
    return rim.game(TOOL, args, strict=False)


def _n(v, unit=""):
    """A number that was never read prints as `?`, never as 0."""
    return "?" if v is None else ("%s%s" % (v, unit))


def render(r):
    if r.get("success") is False:
        print("WORLD REFUSED: %s" % (r.get("error") or r.get("message")
                                     or "no reason given"))
        return 1
    if r.get("status") == "no_game":
        print("no game is loaded, so there is no tile to read")
        return 1

    print("TILE %s  %s, %s" % (_n(r.get("tile")), r.get("biome") or "?",
                               r.get("hilliness") or "?"))
    print("  at        : long %s, lat %s"
          % (_n(r.get("longitude")), _n(r.get("latitude"))))
    print("  temperature: %s C now (season) | average %s | range %s..%s C"
          % (_n(r.get("temperature")),
             r.get("averageTemperatureLabel") or "?",
             _n(r.get("minTemperature")), _n(r.get("maxTemperature"))))
    growing = r.get("growingPeriodLabel")
    twelfths = r.get("growingTwelfths") or []
    rng = r.get("growingRangeC") or {}
    print("  growing   : %s  (%s of 12 twelfths inside %s..%s C)"
          % (growing or "?", len(twelfths),
             _n(rng.get("min")), _n(rng.get("max"))))
    if r.get("growingPeriodDays") == 0:
        print("     !! ZERO growing days. Nothing grows outdoors on this tile at "
              "any time of year; hydroponics is the only crop there is.")
    print("  rainfall  : %s mm/yr | elevation %s m | swampiness %s | pollution %s"
          % (_n(r.get("rainfall")), _n(r.get("elevation")),
             _n(r.get("swampiness")), _n(r.get("pollution"))))
    if r.get("coastal"):
        print("  coastal   : yes")

    settlements = r.get("settlements") or []
    print("  settlements within %s tiles: %d"
          % (_n(r.get("settlementRadius")), len(settlements)))
    for st in settlements:
        print("     %-28s %-22s %-12s goodwill %-5s %s tiles"
              % (st.get("label") or "?", st.get("faction") or "no faction",
                 st.get("relation") or "?", _n(st.get("goodwill")),
                 _n(st.get("distanceTiles"))))
    if r.get("settlementsNotListed"):
        print("     (+%s more past the listing cap)" % r["settlementsNotListed"])

    view = r.get("worldView") or {}
    if view.get("shown"):
        print("  planet view: shown for %s s, then %s"
              % (_n(view.get("watchSeconds")),
                 "hidden again" if view.get("hidden")
                 else "NOT HIDDEN -- %s" % (view.get("reason") or "no reason")))
    elif view.get("reason") and view.get("reason") != "show:false":
        print("  planet view: not shown (%s)" % view["reason"])
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
    argv = [a for a in argv if a != "--json"]
    tile, argv = _take(argv, "--tile")
    near, argv = _take(argv, "--near", "15")
    show, argv = _take(argv, "--show", "0")
    if argv:
        print("world.py does not take %r; see --help" % " ".join(argv))
        return 2

    rim.init()
    try:
        r = read(tile=tile, near=near, show=int(show))
    except (rim.BridgeError, ValueError) as e:
        print("WORLD FAILED  %s" % e)
        return 1
    if not isinstance(r, dict):
        print("WORLD FAILED  %s did not answer with a payload (%r)"
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
