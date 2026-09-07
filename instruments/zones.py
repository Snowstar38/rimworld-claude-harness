"""Zones and storage, from the game's own zone manager -- one call, whole map.

  python zones.py                    # every zone: cells, occupancy, contents, anomalies
  python zones.py "Growing zone 1"   # one zone: label, numeric id, or a cell in it
  python zones.py --match Stockpile  # only zones whose label contains the text
  python zones.py --cells            # also list every cell per zone
  python zones.py --filter           # + each stockpile's storage filter
  python zones.py repair             # dry-run: what a repair would change
  python zones.py repair --do        # ...and do it
  python zones.py add "Stockpile zone 2" 113 140 4 3        # dry-run a 4x3 rect
  python zones.py add "Stockpile zone 2" 113 140 4 3 --do   # and apply it
  python zones.py create stockpile "Pantry" 113 140 4 3 --do
  python zones.py create stockpile "Meals" 120 140 3 3 --preset food --priority Preferred --do
  python zones.py remove "Pantry" 113 140 --do
  python zones.py delete "Pantry" --do
  python zones.py filter "Pantry" --preset perishables --do
  python zones.py filter "Pantry" --allow Silver,Steel --disallow Chunks --priority Important --do
  python zones.py crop "Growing zone 1" Plant_Potato        # dry-run the crop change
  python zones.py crop 118,133 Plant_Rice --do              # ...by a cell in the zone

`filter` sets a stockpile's storage filter: `--preset` first (everything,
nothing, food, perishables, nonperishables, outdoorSafe), then `--allow`, then
`--disallow` (comma-separated ThingCategoryDef or ThingDef names, defName or
label), then `--priority`. At least one of the four is required. The reply
defines every preset it used, so the numbers are checkable rather than trusted.

`crop` sets a growing zone's plant. The zone is named by label, by numeric id,
or by a cell inside it (`x,z`). The plant is a ThingDef defName (`Plant_Potato`)
or its label (`potato plant`); a name that matches nothing lists what the zone
will take. The reply reads the crop back off the zone, and a zone that has never
been given one reports `plantDef: null` rather than the potatoes RimWorld would
default to -- the property that would answer "potato" also WRITES it.

Every write selects the zone and opens the tab a player would use while the
change lands, then closes it. `--no-watch` turns that off.

Backed by `home/list_zones` and `home/zone_cells`.

**Every write is a dry run unless you pass `--do`.** The dry run computes the
exact plan the real call would execute; read it, then run it again with `--do`.

**Add cells here, never with the designator.** RimWorld's stockpile drag skips
any cell that already has a zone, and `apply_architect_designator` calls
`zone.AddCell` straight on the cells it accepted, which overwrites the grid
pointer without telling the old owner -- leaving one zone listing cells the grid
gave to another, and the game logging `overwriting slot group square` at every
load. `add` here removes the cell from its owner first and says so
(`takenFrom`); `repair` fixes a zone whose list and grid disagree, the one state
the game's own delete makes worse.

**A growing zone's plants are counted twice, and this prints both.**
`home/list_zones` counts plants over the zone's own cell list AND over the cells
the map's zone grid gives it, reports them separately, and never merges them --
it is the same list-vs-grid divergence seen from the crop side. When they
disagree, quoting either number alone answers a narrower question than the one
that was asked, so the disagreement gets its own line here in words. `repair` is
the fix; after it the two agree and either number is safe.

Storage occupancy answers "Stockpile 1: 30 cells, 26 occupied by a building" --
`cellsNotStandable` counts cells under a bench or wall, `cellsWithItems` the
ones holding a stack, `cellsFree` the rest. The colony's Low-food alert counts
STORED food only, so a colony with pemmican on the floor and two free stockpile
cells reads as starving. Look here before believing that alert.
"""
import json
import re
import sys

import rim


def _rect_or_cells(argv):
    """x z [w h] -> dict of rect args; w,h default 1."""
    nums = [int(a) for a in argv if a.lstrip("-").isdigit()]
    if len(nums) < 2:
        raise SystemExit("need x z [width height]")
    x, z = nums[0], nums[1]
    w = nums[2] if len(nums) > 2 else 1
    h = nums[3] if len(nums) > 3 else 1
    return {"x": x, "z": z, "width": w, "height": h}


_CELL = re.compile(r"^(-?\d+)\s*,\s*(-?\d+)$")


def zone_at(spec):
    """A zone spec, with `x,z` resolved to the id of the zone at that cell.

    home/zone_cells takes a label or a numeric id; a cell is the handle a
    caller actually has after reading the map.
    """
    m = _CELL.match(str(spec or "").strip())
    if not m:
        return spec
    x, z = int(m.group(1)), int(m.group(2))
    listing = rim.game("home/list_zones", {"includeCells": True,
                                           "includeContents": False})
    for zone in listing.get("zones") or []:
        for group in ("cells", "gridCells"):
            for c in zone.get(group) or []:
                if c.get("x") == x and c.get("z") == z:
                    return str(zone.get("id"))
    raise SystemExit("no zone holds cell %s,%s (checked every zone's own cell "
                     "list and the map's zone grid)" % (x, z))


def print_crop(r):
    """The before/after crop, in one line each. op=crop only."""
    b, a = r.get("before") or {}, r.get("after") or {}
    print("  before: %s%s" % (b.get("plantDef") or "(no crop set)",
                              "" if b.get("allowSow", True) else ", sowing off"))
    print("  after%s:  %s" % ("" if r.get("dryRun") is False else " (planned)",
                              a.get("plantDef") or "(no crop set)"))


def list_zones(match=None, cells=False, contents=True, filt=False):
    args = {"includeCells": cells, "includeContents": contents, "filter": filt}
    if match:
        args["match"] = match
    return rim.game("home/list_zones", args)


def _take(argv, name):
    """Pull `--name value` out of argv. Returns (value or None, remaining)."""
    if name not in argv:
        return None, argv
    i = argv.index(name)
    if i + 1 >= len(argv):
        raise SystemExit("%s needs a value" % name)
    return argv[i + 1], argv[:i] + argv[i + 2:]


def _n(v):
    """A count that was never taken prints as `?`, never as 0."""
    return "?" if v is None else str(v)


def print_plants(z):
    """A growing zone's plants, over both cell sets (2026-09-02).

    One compact line when the zone's own cell list and the map's zone grid hold
    the same plants; when they do not, the counts get a line each and the
    disagreement gets said out loud, because 7-vs-43 is precisely the bug that
    was reported and a reader should not have to spot it in two numbers.
    Silent for a zone that is not a growing zone."""
    if not ("plantScanRan" in z or "allowSow" in z or "plantDef" in z):
        return
    if "plantScanRan" not in z:
        # Old DLL: no plant fields at all. Saying so beats printing None, and
        # beats printing nothing -- silence here would read as "no crops".
        print("      plants: NOT REPORTED -- this DLL predates the growing-zone plant "
              "count (2026-09-02). Uncounted, not zero; rebuild the companion DLL.")
        return
    if not z.get("plantScanRan"):
        # includeContents was off, so every count in the block is null.
        print("      plants: NOT COUNTED -- the plant scan did not run (contents off), "
              "so there is no number here. This is not zero plants.")
        return

    listed, grid = z.get("plantsInListedCells"), z.get("plantsInGridCells")
    rows = z.get("plants") or []
    tally = ", ".join("%s %s%s" % (p.get("label") or p.get("defName"),
                                   _n(p.get("inEitherCellSet")),
                                   " (set crop)" if p.get("isSetCrop") else "")
                      for p in rows[:6])
    if len(rows) > 6:
        tally += " ..."
    if z.get("plantRowsNotListed"):
        tally += " (+%s more def(s) not listed)" % z["plantRowsNotListed"]

    if z.get("plantCountsDisagree"):
        print("      plants: %s in the zone's own cells, %s in the grid's cells, %s in "
              "either set | crop %s/%s | sown cells %s/%s"
              % (_n(listed), _n(grid), _n(z.get("plantsInEitherCellSet")),
                 _n(z.get("cropPlantsInListedCells")), _n(z.get("cropPlantsInGridCells")),
                 _n(z.get("cellsSownInListedCells")), _n(z.get("cellsSownInGridCells"))))
        if tally:
            print("      by def: " + tally)
        print("      !! PLANT COUNTS DISAGREE -- this zone's own cell list and the map's "
              "zone grid do not hold the same plants, so neither number on its own "
              "answers \"how many crops are in this zone\".")
        if z.get("plantsOnlyOnGridCells"):
            print("      !! %s plant(s) stand in cells the grid gives this zone but the "
                  "zone's own list has forgotten. That is what is invisible, in one "
                  "number. Run `zones.py repair` and the two agree."
                  % z["plantsOnlyOnGridCells"])
    elif not listed and not grid:
        print("      plants: none standing in either cell set (checked -- counted, not "
              "assumed)%s" % ((" | " + tally) if tally else ""))
    else:
        print("      plants: %s in both cell sets | crop %s in %s sown cell(s)%s"
              % (_n(listed), _n(z.get("cropPlantsInListedCells")),
                 _n(z.get("cellsSownInListedCells")), (" | " + tally) if tally else ""))
    if z.get("plantScanFailed"):
        print("      !! at least one cell would not read -- the plant counts above are a "
              "floor, not a total.")


def print_filter(f, indent="      "):
    """A stockpile's storage filter, in three lines at most.

    Counts are printed against `storableDefCount` -- the defs a stockpile can
    ever hold -- because "41 defs allowed" alone says nothing about whether that
    is most of them or a handful."""
    if not f:
        return
    rot = ("rottable %s" % f.get("allowedRottableCount")) if f.get("allowsRottable")         else "nothing rottable"
    print("%sfilter: %s of %s storable defs | priority %s | %s"
          % (indent, _n(f.get("allowedDefCount")), _n(f.get("storableDefCount")),
             f.get("priority"), rot))
    full = f.get("categoriesFullyAllowed") or []
    part = f.get("categoriesPartlyAllowed") or []
    if full or part:
        bits = []
        if full:
            bits.append("all of [%s]" % ", ".join(full))
        if part:
            bits.append("some of [%s]" % ", ".join(part))
        print("%s  categories: %s" % (indent, "; ".join(bits)))
    sample = f.get("sampleAllowed") or []
    if sample:
        print("%s  e.g. %s%s" % (indent, ", ".join(sample),
                                 " ..." if f.get("sampleTruncated") else ""))


def print_zones(r):
    t = r.get("totals") or {}
    print("ZONES %d | listed %s grid %s | %s"
          % (r.get("zoneCount", 0), t.get("listedCells"), t.get("gridCells"),
             "consistent" if t.get("consistent") else "INCONSISTENT -- run `zones.py repair`"))
    for a in r.get("anomalies") or []:
        print("  !! " + a)
    for z in r.get("zones") or []:
        head = "  %s (id %s, %s) %s cells" % (z.get("label"), z.get("id"),
                                             (z.get("type") or "").replace("Zone_", ""),
                                             z.get("listedCellCount"))
        if z.get("listedCellCount") != z.get("gridCellCount"):
            head += " [grid says %s]" % z.get("gridCellCount")
        if z.get("contiguous") is False:
            head += " NOT CONTIGUOUS"
        if "priority" in z:
            # 2026-09-02: this said "under-building N" while reading
            # `cellsNotStandable`, and then the loop below listed every building
            # in `blockingBuildings` regardless. Live on Lampblack day 39 the
            # Storeroom printed "under-building 0" and then EIGHT "under:" lines,
            # because a power conduit stands in a stockpile without blocking a
            # cell of it -- passable, standable, stores fine. Both numbers were
            # right and together they read as a contradiction. Say what the
            # count actually counts, and let the loop say which buildings block.
            head += " | %s | blocked cells %s, items %s, free %s" % (
                z.get("priority"), z.get("cellsNotStandable"),
                z.get("cellsWithItems"), z.get("cellsFree"))
        if z.get("plantDef") is not None or "allowSow" in z:
            head += " | %s%s" % (z.get("plantDef") or "(no crop set)",
                                  "" if z.get("allowSow", True) else ", sowing off")
        print(head)
        # `blockingBuildings` is every building STANDING in the zone, not every
        # building blocking it -- the payload has carried a per-building
        # `impassable` flag since it was written and nothing here read it.
        standing = z.get("blockingBuildings") or []
        if standing:
            hard = [b for b in standing if b.get("impassable")]
            if not hard:
                print("      %d building(s) stand in this zone, none impassable"
                      " -- they do not block storage" % len(standing))
            else:
                print("      %d building(s) stand in this zone, %d impassable"
                      % (len(standing), len(hard)))
        for b in standing:
            print("      %s %s at %s,%s (%s cells)"
                  % ("BLOCKS:" if b.get("impassable") else "  under:",
                     b.get("label"), (b.get("position") or {}).get("x"),
                     (b.get("position") or {}).get("z"), b.get("cellsOverlapped")))
        print_filter(z.get("filter"))
        cont = z.get("contents") or []
        if cont:
            print("      holds: " + ", ".join("%s x%s" % (c.get("label") or c.get("defName"), c.get("count"))
                                              for c in cont[:12])
                  + (" ..." if len(cont) > 12 else ""))
        print_plants(z)
        if z.get("cells"):
            print("      cells: " + " ".join("%s,%s" % (c["x"], c["z"]) for c in z["cells"]))
            if z.get("cellsNotListed"):
                print("      (+%s more cell(s) past the per-zone cap, not shown)"
                      % z["cellsNotListed"])
        # 2026-09-02: `cells` is the zone's OWN list -- the short half of a
        # disagreement this same block reports two lines up. The grid's set is
        # listed beside it now, under a name that says which one it is.
        if z.get("gridCells"):
            print("      grid cells: "
                  + " ".join("%s,%s" % (c["x"], c["z"]) for c in z["gridCells"])
                  + ("  (+%s not shown)" % z["gridCellsNotListed"]
                     if z.get("gridCellsNotListed") else ""))


def zone_cells(op, zone=None, do=False, watch=True, **kw):
    args = {"op": op, "dryRun": not do, "watch": watch}
    if zone:
        args["zone"] = zone
    args.update(kw)
    # strict=False: a refused cell is the answer, not a fault.
    return rim.game("home/zone_cells", args, strict=False)


def print_plan(r):
    tag = "DRY RUN" if r.get("dryRun") else "DONE"
    if not r.get("success"):
        # The tool names its refusal in `error`; `message` is the transport's.
        print("%s REFUSED: %s" % (r.get("op", "?").upper(),
                                  r.get("error") or r.get("message")))
        return
    z = r.get("zone") or {}
    print("%s %s%s" % (r.get("op", "?").upper(), tag,
                       " -- %s (id %s)" % (z.get("label"), z.get("id")) if z.get("label") else " -- every zone"))
    for c in r.get("cells") or []:
        line = "  %s,%s %s" % (c.get("x"), c.get("z"), "ok" if c.get("accepted") else "REFUSED")
        if c.get("takenFrom"):
            line += " (taken from %s)" % c["takenFrom"]
        if c.get("reason"):
            line += " -- " + c["reason"]
        print(line)
    for c in r.get("changes") or []:
        print("  > " + c)
    for zr in r.get("zonesRemoved") or []:
        print("  removed zone: %s -- %s" % (zr.get("label"), zr.get("reason")))
    for s in r.get("skippedNotifies") or []:
        print("  skipped %s at %s,%s: %s" % (s.get("call"), s.get("x"), s.get("z"), s.get("why")))
    for name, text in sorted((r.get("presetDefinition") or {}).items()):
        print("  preset %s = %s" % (name, text))
    b, a = r.get("before") or {}, r.get("after") or {}
    if r.get("op") == "crop":
        print_crop(r)
        if r.get("changed") is False:
            print("  unchanged -- the zone already grows exactly this.")
    elif r.get("op") == "filter":
        # For op=filter the before/after blocks ARE the filter summary, not cell
        # counts -- the op writes settings, not cells.
        print("  before:")
        print_filter(b, "    ")
        print("  after%s:" % ("" if r.get("dryRun") is False else " (planned)"))
        print_filter(a, "    ")
        if r.get("changed") is False:
            print("  unchanged -- the stockpile already had exactly this filter and priority.")
    elif b or a:
        print("  before: listed %s grid %s phantom %s | after: listed %s grid %s phantom %s"
              % (b.get("listedCells", b.get("listedCellCount")),
                 b.get("gridCells", b.get("gridCellCount")), b.get("phantomCellCount"),
                 a.get("listedCells", a.get("listedCellCount")),
                 a.get("gridCells", a.get("gridCellCount")), a.get("phantomCellCount")))
    if r.get("filter"):
        print("  the new stockpile's filter:")
        print_filter(r["filter"], "    ")
    w = r.get("watch") or {}
    if w and not w.get("shown"):
        print("  (not shown on screen: %s)" % w.get("reason"))


# The verbs. Anything else in the first position is a zone to show.
OPS = ("repair", "add", "remove", "create", "delete", "crop", "filter")


def show_one(spec, cells=False, filt=False, as_json=False):
    """`zones.py <zone>` -- one zone by label, numeric id, or a cell in it.

    The label goes to the tool's own `match` (a substring, the same one
    `--match` uses); an id or an `x,z` is resolved here and the listing is
    narrowed to it. Nothing matched is said in one line with the zones that
    exist, never by printing the help text at somebody who typed a real name.
    """
    text = str(spec).strip()
    want_id = None
    if _CELL.match(text):
        want_id = zone_at(text)                 # raises with its own message
    elif text.lstrip("-").isdigit():
        want_id = text

    r = list_zones(match=None if want_id else text, cells=cells, filt=filt)
    if want_id is not None:
        r = dict(r, zones=[z for z in (r.get("zones") or [])
                           if str(z.get("id")) == str(want_id)])
        r["zoneCount"] = len(r["zones"])
    if as_json:
        print(json.dumps(r, indent=1))
        return 0
    if not r.get("zones"):
        every = list_zones(cells=False, contents=False)
        names = ", ".join("%s (id %s)" % (z.get("label"), z.get("id"))
                          for z in (every.get("zones") or [])) or "none"
        print("no zone matches %r. Zones on this map: %s" % (text, names))
        print("(%r was read as a zone, not a command. The commands are: %s)"
              % (text, ", ".join(OPS)))
        return 1
    print_zones(r)
    return 0


def main(argv):
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    do = "--do" in argv
    watch = "--no-watch" not in argv
    as_json = "--json" in argv
    argv = [a for a in argv if a not in ("--do", "--no-watch", "--json")]
    # Flags that carry a value come out first, so the positional rect parser
    # below never sees "Important" and tries to read it as a coordinate.
    match, argv = _take(argv, "--match")
    preset, argv = _take(argv, "--preset")
    allow, argv = _take(argv, "--allow")
    disallow, argv = _take(argv, "--disallow")
    priority, argv = _take(argv, "--priority")
    filt = {}
    for key, value in (("preset", preset), ("allow", allow),
                       ("disallow", disallow), ("priority", priority)):
        if value:
            filt[key] = value

    rim.init()
    if not argv or argv[0].startswith("--"):
        r = list_zones(match=match, cells="--cells" in argv, filt="--filter" in argv)
        if as_json:
            print(json.dumps(r, indent=1))
        else:
            print_zones(r)
        return

    op = argv[0]
    if op not in OPS:
        # 2026-09-07: `zones.py "Growing zone 1"` printed the whole help text --
        # the name was not an op, and an unknown op fell through to __doc__, so
        # a perfectly good zone label read as a usage error. A positional that
        # is not an op is a ZONE: a label (substring), a numeric id, or a cell.
        return show_one(argv[0], cells="--cells" in argv, filt="--filter" in argv,
                        as_json=as_json)

    if op == "repair":
        r = zone_cells("repair", argv[1] if len(argv) > 1 else None, do, watch)
    elif op in ("add", "remove"):
        r = zone_cells(op, argv[1], do, watch, **_rect_or_cells(argv[2:]))
    elif op == "create":
        ztype, label = argv[1], argv[2]
        r = zone_cells("create", None, do, watch, zoneType=ztype, label=label,
                       **dict(_rect_or_cells(argv[3:]), **filt))
    elif op == "delete":
        r = zone_cells("delete", argv[1], do, watch)
    elif op == "crop":
        if len(argv) < 3:
            raise SystemExit("crop needs a zone and a plant: "
                             "zones.py crop <zone-id|label|x,z> <PlantDefName> [--do]")
        r = zone_cells("crop", zone_at(argv[1]), do, watch, plant=argv[2])
    elif op == "filter":
        if len(argv) < 2:
            raise SystemExit("filter needs a zone: zones.py filter <zone> --preset ...")
        if not filt:
            raise SystemExit("filter needs at least one of --preset, --allow, "
                             "--disallow, --priority")
        r = zone_cells("filter", argv[1], do, watch, **filt)
    else:
        # OPS and this dispatch are the same list by construction.
        raise SystemExit("unreachable: op %r is in OPS with no branch" % op)

    if as_json:
        print(json.dumps(r, indent=1))
    else:
        print_plan(r)
    # A refusal is an answer worth reading, and a non-zero exit so a script
    # that chained on this one stops.
    return 1 if r.get("success") is False else 0


if __name__ == "__main__":
    try:
        import overlay_client as _ov
        sys.argv[1:], _say, _mood = _ov.take_flags(sys.argv[1:])
    except Exception:
        _ov, _say, _mood = None, None, None
    _rc = main(sys.argv[1:])
    if _ov is not None:
        _ov.say_flags(_say, _mood)
    if _rc:
        sys.exit(_rc)
