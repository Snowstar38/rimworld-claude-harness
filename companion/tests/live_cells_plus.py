"""Live load test for `home/get_cells_plus`' three narrowings (WANTED 16).

READ-ONLY. Every call it makes is a read tool: list_colonists, get_cell_info
(through rim.map_size) and get_cells_plus itself. It never saves, never
unpauses, never places or removes anything, and never touches the selection.
Run it with the game already loaded and PAUSED; it leaves the game exactly as it
found it.

    cd rimworld\\instruments
    python ..\\companion\\tests\\live_cells_plus.py

Nothing below has ever been run against a live game. The DLL was built and the
Python side was exercised against a synthetic reply with no bridge at all -- so
every assertion here is a PROMISE being checked for the first time, not a
regression test. A failure is information.

What it checks, and what a failure of each one would mean:

  1  default call, 32x32 around a colonist
       Every cell carries walkable AND passable, cellsOmitted is 0, and
       len(cells) == cellCount == 1024. fieldsApplied names all nine fields and
       thingFieldsApplied all ten. FAILURE = the default payload changed, which
       it was not allowed to do: field selection is opt-in, and a caller that
       sends only a rectangle must get exactly what it got yesterday.

  2  fields:"terrain,things"
       Cells carry x, z, terrainDefName and things and NOTHING else, and
       fieldsApplied is exactly ["terrain","things"]. FAILURE = either the
       selection was ignored (a caller who thinks they narrowed and did not) or
       it dropped a field it was asked for.

  3  both rect forms
       {x,z,width,height} and {x0,z0,x1,z1} over the same rectangle return
       byte-identical cells[]. FAILURE = the corner form and the extent form
       disagree, which is the 2026-09-02 silent-zero defect coming back.

  4  sparse:true
       len(cells) + cellsOmitted == cellCount, and every returned cell carries
       at least one of things / designations / zoneId / areaIds. FAILURE =
       either the arithmetic does not close (a cell went missing without being
       counted) or sparse dropped something that had content.

  5  summary:true
       Reconciled against the FULL call over the same rectangle: terrain
       counts, roof counts (+ unroofed), fogged, walkable/passable, zone and
       area cellsInRect, the set of thing defNames, the set of designation
       defNames, and the pawn count. FAILURE = the aggregate and the cell walk
       disagree about the same rectangle, so one of them is wrong.
       Note that summary de-duplicates by THING IDENTITY and cells[] does not,
       so the checks that cross that line are inequalities (summary <= raw)
       rather than equalities, and both numbers are printed.

  6  an unknown field name
       success:false, naming the bad word. FAILURE = the tool silently ignored
       a field name, which is the failure mode this whole design is against.

  7  a bogus argument key
       lands in unknownArguments[]. FAILURE = the unknown-argument reporting
       regressed.

  8  payload sizes
       Prints the byte length of each reply for WANTED 16's table. Not a check.

  9  summary:true over the WHOLE map (WANTED 17)
       success, cellCount == map area, cells[] empty, cellsOmitted == cellCount,
       cellCap == the map area, capReason set, mapSize matching what
       rim.map_size() found, summary.things aggregated by def with an integer
       forbidden count on every row, and summary.terrain totalling the map. The
       elapsed time and the payload size are printed: a summary reply is a few KB
       whatever the rectangle, which is why the cap can be lifted at all.
       FAILURE = either the lift did not happen or an aggregate does not cover
       the rectangle it claims.

  9b the 1024-cell cap that did NOT move
       A 1025-cell rect with no summary must still be refused, naming 1024.
       FAILURE = the summary lift leaked into the cells[] modes.
"""
import sys, os, json, time, collections

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "instruments"))
import rim

TOOL = "home/get_cells_plus"
FAILURES = []
CHECKS = [0]
SIZES = []

ALL_CELL_FIELDS = ["areas", "designations", "fogged", "passable", "roof",
                   "terrain", "things", "walkable", "zone"]
ALL_THING_FIELDS = ["build", "className", "defName", "forbidden", "hitPoints",
                    "label", "owner", "plant", "stackCount", "stuff"]


def check(ok, label, detail=""):
    CHECKS[0] += 1
    if ok:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}   {detail}")
        FAILURES.append(label + ("   " + detail if detail else ""))


def nbytes(r):
    return len(json.dumps(r, separators=(",", ":")))


def call(args, label, note=None):
    print(f"\n--- {label}: {TOOL} {json.dumps(args)}")
    try:
        r = rim.game(TOOL, args)
    except Exception as e:
        check(False, label + " returned at all", f"{type(e).__name__}: {e}")
        return None
    if not isinstance(r, dict) or "cells" not in r:
        check(False, label + " returned a cells[]", str(r)[:200])
        return None
    n = nbytes(r)
    if note:
        SIZES.append((note, n))
    print(f"      cellCount {r.get('cellCount')}  cells {len(r.get('cells') or [])}"
          f"  cellsOmitted {r.get('cellsOmitted')}"
          f"  zones {len(r.get('zones') or {})}  areas {len(r.get('areas') or {})}"
          f"  payload {n} bytes ({n / 1024.0:.1f} KB)")
    print(f"      fieldsApplied {r.get('fieldsApplied')}")
    print(f"      thingFieldsApplied {r.get('thingFieldsApplied')}")
    return r


def structural(r, label):
    """The invariants that hold on EVERY reply, whatever was asked."""
    total = r.get("cellCount")
    listed = len(r.get("cells") or [])
    omitted = r.get("cellsOmitted")
    check(isinstance(omitted, int),
          f"{label}: cellsOmitted is present and an integer", repr(omitted))
    check(isinstance(total, int) and isinstance(omitted, int)
          and listed + omitted == total,
          f"{label}: len(cells) + cellsOmitted == cellCount",
          f"{listed} + {omitted} != {total}")
    check(isinstance(r.get("fieldsApplied"), list) and r["fieldsApplied"],
          f"{label}: fieldsApplied[] is present and non-empty",
          repr(r.get("fieldsApplied")))
    check(isinstance(r.get("thingFieldsApplied"), list)
          and "defName" in (r.get("thingFieldsApplied") or []),
          f"{label}: thingFieldsApplied[] is present and includes defName",
          repr(r.get("thingFieldsApplied")))
    check("unknownArguments" in r, f"{label}: unknownArguments[] is present")
    check(isinstance(r.get("zones"), dict) and isinstance(r.get("areas"), dict),
          f"{label}: zones{{}} and areas{{}} are present (possibly empty)")


def cell_keys(r):
    keys = set()
    for c in r.get("cells") or []:
        keys |= set(c)
    return keys


def thing_keys(r):
    keys = set()
    for c in r.get("cells") or []:
        for t in c.get("things") or []:
            keys |= set(t)
    return keys


def find_rect():
    """A 32x32 block centred on a colonist, clamped to the map.

    rim.map_size() binary-searches the edge with rimworld/get_cell_info -- all
    reads. If there is no colonist to centre on, the map's middle is used and
    the run says so, because a rect nobody lives in still exercises every
    branch here except the pawn one.
    """
    mw, mh = rim.map_size()
    cx, cz, who = mw // 2, mh // 2, None
    try:
        for c in rim.game("rimworld/list_colonists", {}).get("colonists") or []:
            if c.get("dead"):
                continue
            p = c.get("position") or {}
            if p.get("x") is not None:
                cx, cz, who = p["x"], p["z"], c.get("name")
                break
    except Exception as e:
        print(f"  ** list_colonists failed ({e}) -- centring on the map middle **")
    size = 32
    x = max(0, min(cx - size // 2, mw - size))
    z = max(0, min(cz - size // 2, mh - size))
    print(f"MAP {mw}x{mh};  centring on "
          + (f"{who} at ({cx},{cz})" if who else f"the middle ({cx},{cz})")
          + f";  rect x {x}..{x + size - 1}  z {z}..{z + size - 1}")
    return x, z, size


def main():
    rim.init()
    print("=" * 72)
    print("home/get_cells_plus -- field selection, sparse and summary.")
    print("READ ONLY. Nothing is saved, nothing is selected, nothing moves.")
    print("=" * 72)

    x, z, size = find_rect()
    rect = {"x": x, "z": z, "width": size, "height": size}
    cells_in_rect = size * size

    # ------------------------------------------------------------------ 1 ---
    full = call(dict(rect), "1 default (every field)", note="default")
    if full is None:
        report()
        return
    structural(full, "default")
    check(full.get("cellCount") == cells_in_rect,
          "default: cellCount is the whole rectangle",
          f"{full.get('cellCount')} != {cells_in_rect}")
    check(len(full.get("cells") or []) == cells_in_rect,
          "default: every cell of the rectangle is returned")
    check(full.get("cellsOmitted") == 0,
          "default: cellsOmitted is 0", repr(full.get("cellsOmitted")))
    check(full.get("fieldsApplied") == ALL_CELL_FIELDS,
          "default: fieldsApplied names all nine per-cell fields",
          repr(full.get("fieldsApplied")))
    check(full.get("thingFieldsApplied") == ALL_THING_FIELDS,
          "default: thingFieldsApplied names all ten per-thing fields",
          repr(full.get("thingFieldsApplied")))
    missing = [(c.get("x"), c.get("z")) for c in full["cells"]
               if "walkable" not in c or "passable" not in c]
    check(not missing,
          "default: walkable AND passable are on EVERY cell",
          f"{len(missing)} cells without one, e.g. {missing[:5]}")
    bad = [(c.get("x"), c.get("z"), c.get("walkable"), c.get("passable"))
           for c in full["cells"]
           if not isinstance(c.get("walkable"), bool)
           or not isinstance(c.get("passable"), bool)]
    check(not bad,
          "default: both are real booleans (never null, never a string)",
          f"{len(bad)} cells, e.g. {bad[:5]} -- 'unknown' means the game threw")
    print(f"      cell keys seen: {sorted(cell_keys(full))}")
    print(f"      thing keys seen: {sorted(thing_keys(full))}")

    # ------------------------------------------------------------------ 2 ---
    narrow = call(dict(rect, fields="terrain,things"), "2 fields=terrain,things")
    if narrow is not None:
        structural(narrow, "fields")
        check(narrow.get("fieldsApplied") == ["terrain", "things"],
              "fields: fieldsApplied is exactly what was asked for",
              repr(narrow.get("fieldsApplied")))
        keys = cell_keys(narrow)
        check(keys <= {"x", "z", "terrainDefName", "things"},
              "fields: no cell carries a key that was not selected",
              str(sorted(keys)))
        check("terrainDefName" in keys and "things" in keys,
              "fields: the two selected fields ARE present somewhere",
              str(sorted(keys)))
        check(len(narrow.get("cells") or []) == cells_in_rect,
              "fields: narrowing the fields does not drop a cell")
        check(not (narrow.get("zones") or {}),
              "fields: zones{} is empty when `zone` was not selected",
              str(list((narrow.get("zones") or {}).keys())[:4]))
        # Case and whitespace tolerance, same rect, same answer.
        sloppy = call(dict(rect, fields="  TERRAIN , things "),
                      "2b fields, sloppily spelled")
        if sloppy is not None:
            check(sloppy.get("fieldsApplied") == ["terrain", "things"],
                  "fields: case and whitespace are tolerated",
                  repr(sloppy.get("fieldsApplied")))

    # thingFields on its own.
    tf = call(dict(rect, thingFields="defName,forbidden"),
              "2c thingFields=defName,forbidden", note='thingFields="defName,forbidden"')
    if tf is not None:
        check(tf.get("thingFieldsApplied") == ["defName", "forbidden"],
              "thingFields: thingFieldsApplied is exactly what was asked for",
              repr(tf.get("thingFieldsApplied")))
        tk = thing_keys(tf)
        check(tk <= {"defName", "forbidden"},
              "thingFields: no thing carries an unselected key", str(sorted(tk)))

    # ------------------------------------------------------------------ 3 ---
    corners = call({"x0": x, "z0": z, "x1": x + size - 1, "z1": z + size - 1},
                   "3 the corner form of the same rectangle")
    if corners is not None:
        same = json.dumps(corners.get("cells"), sort_keys=True) == \
            json.dumps(full.get("cells"), sort_keys=True)
        check(same, "corners: cells[] is identical to the {x,z,width,height} form",
              "the two rect shapes disagree about the same rectangle")
        check(corners.get("rect", {}).get("argumentShape") == "{x0,z0,x1,z1}",
              "corners: the reply echoes the argument shape it was sent",
              repr(corners.get("rect", {}).get("argumentShape")))

    # ------------------------------------------------------------------ 4 ---
    sp = call(dict(rect, fields="things", sparse=True),
              "4 sparse, things only", note='fields="things" sparse')
    if sp is not None:
        structural(sp, "sparse")
        check(sp.get("cellCount") == cells_in_rect,
              "sparse: cellCount is still the whole rectangle",
              f"{sp.get('cellCount')} != {cells_in_rect}")
        empties = [(c.get("x"), c.get("z")) for c in sp.get("cells") or []
                   if not (c.get("things") or c.get("designations")
                           or c.get("zoneId") or c.get("areaIds"))]
        check(not empties,
              "sparse: every returned cell carries selected content",
              f"{len(empties)} empty cells, e.g. {empties[:5]}")
        with_things = sum(1 for c in full.get("cells") or [] if c.get("things"))
        check(len(sp.get("cells") or []) == with_things,
              "sparse+fields=things: exactly the cells that hold a thing",
              f"{len(sp.get('cells') or [])} != {with_things} in the full call")
        # And with every field selected, sparse keeps zone and designation cells
        # too -- fog on its own is NOT content, which is the documented choice.
        sp_all = call(dict(rect, sparse=True), "4b sparse, every field")
        if sp_all is not None:
            structural(sp_all, "sparse all")
            expect = sum(1 for c in full.get("cells") or []
                         if c.get("things") or c.get("designations")
                         or c.get("zoneId") or c.get("areaIds"))
            check(len(sp_all.get("cells") or []) == expect,
                  "sparse: with every field, exactly the cells with any content",
                  f"{len(sp_all.get('cells') or [])} != {expect}")
            fogged_only = [c for c in full.get("cells") or []
                           if c.get("fogged") and not (
                               c.get("things") or c.get("designations")
                               or c.get("zoneId") or c.get("areaIds"))]
            print(f"      {len(fogged_only)} empty FOGGED cell(s) in this rect;"
                  " sparse drops them, by design -- fog is a property of the"
                  " cell, not content in it.")

    # ------------------------------------------------------------------ 5 ---
    summ = call(dict(rect, summary=True), "5 summary, every field", note="summary")
    if summ is not None:
        structural(summ, "summary")
        s = summ.get("summary")
        check(isinstance(s, dict), "summary: summary{} is present", repr(s)[:120])
        check(summ.get("cells") == [], "summary: cells[] is empty",
              str(len(summ.get("cells") or [])))
        check(summ.get("cellsOmitted") == cells_in_rect,
              "summary: cellsOmitted accounts for the whole rectangle",
              f"{summ.get('cellsOmitted')} != {cells_in_rect}")
        if isinstance(s, dict):
            reconcile(s, full, cells_in_rect)

        cheap = call(dict(rect, summary=True, fields="things"),
                     "5b summary, fields=things -- the cheapest 'what is here'")
        if cheap is not None and isinstance(cheap.get("summary"), dict):
            cs = cheap["summary"]
            check(set(cs) == {"things", "pawns"},
                  "summary+fields: only the selected section is computed",
                  str(sorted(cs)))
            if isinstance(s, dict) and "things" in s:
                check(cs.get("things") == s.get("things"),
                      "summary+fields: the things section is the same either way")

    # ------------------------------------------------------------------ 6 ---
    print(f"\n--- 6 an unknown field name: {TOOL}"
          f' {{"fields": "terrain,bogusField"}}')
    try:
        bad = rim.game(TOOL, dict(rect, fields="terrain,bogusField"),
                       strict=False)
    except Exception as e:
        bad = {"success": None, "error": f"{type(e).__name__}: {e}"}
    print("      " + json.dumps(bad)[:400])
    check(isinstance(bad, dict) and bad.get("success") is False,
          "unknown field: success is false", repr(bad)[:160])
    check(isinstance(bad, dict) and "bogusField" in str(bad.get("error") or ""),
          "unknown field: the message names the bad word",
          str(bad.get("error"))[:160] if isinstance(bad, dict) else "")
    check(isinstance(bad, dict) and isinstance(bad.get("accepted"), list)
          and "terrain" in (bad.get("accepted") or []),
          "unknown field: the refusal lists the accepted names",
          repr(bad.get("accepted")) if isinstance(bad, dict) else "")
    check(isinstance(bad, dict) and "cells" not in bad,
          "unknown field: nothing was answered -- no cells[] in the refusal")

    print(f"\n--- 6b an unknown THING field: thingFields=\"defName,bogusThing\"")
    try:
        bad2 = rim.game(TOOL, dict(rect, thingFields="defName,bogusThing"),
                        strict=False)
    except Exception as e:
        bad2 = {"success": None, "error": f"{type(e).__name__}: {e}"}
    check(isinstance(bad2, dict) and bad2.get("success") is False
          and "bogusThing" in str(bad2.get("error") or ""),
          "unknown thing field: refused, naming the bad word",
          str(bad2.get("error"))[:160] if isinstance(bad2, dict) else "")

    # ------------------------------------------------------------------ 7 ---
    bogus = call(dict(rect, bogusKeyXYZ=True, MaxRows=3), "7 a bogus key")
    if bogus is not None:
        ua = bogus.get("unknownArguments")
        print(f"      unknownArguments: {ua}")
        print(f"      warning: {bogus.get('unknownArgumentsWarning')}")
        check(ua == ["MaxRows", "bogusKeyXYZ"],
              "bogus: unknownArguments names both keys, sorted, case-sensitively",
              str(ua))
        check(bool(bogus.get("unknownArgumentsWarning")),
              "bogus: a warning sentence is present")

    # ------------------------------------------------------------------ 8 ---
    call(dict(rect, fields="terrain,roof,walkable"),
         "8 fields=terrain,roof,walkable", note='fields="terrain,roof,walkable"')

    # ------------------------------------------------------------------ 9 ---
    whole_map()

    report()


def whole_map():
    """WANTED 17: summary:true over the WHOLE map, and the cap that stays.

    summary:true emits no cells[], so the reply is a few KB whatever the
    rectangle and the 1024-cell cap becomes the map area. The cells[] modes keep
    the 1024 cap, so a 1025-cell rect without summary must still be refused -- if
    it is not, the lift leaked into the mode it was never for.
    """
    mw, mh = rim.map_size()
    area = mw * mh
    args = {"x": 0, "z": 0, "width": mw, "height": mh, "summary": True}
    print(f"\n--- 9 whole-map summary: {TOOL} {json.dumps(args)}  ({area} cells)")
    t0 = time.time()
    try:
        r = rim.game(TOOL, args, strict=False)
    except Exception as e:
        check(False, "9 whole-map summary returned at all",
              f"{type(e).__name__}: {e}")
        return
    dt = time.time() - t0
    n = nbytes(r) if isinstance(r, dict) else 0
    SIZES.append((f"summary over the whole map ({area} cells)", n))
    print(f"      {dt:.2f}s, payload {n} bytes ({n / 1024.0:.1f} KB)")
    check(isinstance(r, dict) and r.get("success") is True,
          "9 whole-map summary succeeds",
          str(r.get("error") if isinstance(r, dict) else r)[:200])
    if not (isinstance(r, dict) and r.get("success")):
        return
    check(r.get("cellCount") == area,
          "9 cellCount is the whole map area",
          f"{r.get('cellCount')} != {mw}x{mh}={area}")
    check(len(r.get("cells") or []) == 0 and r.get("cellsOmitted") == area,
          "9 cells[] is empty and cellsOmitted is the whole rectangle",
          f"cells {len(r.get('cells') or [])}, omitted {r.get('cellsOmitted')}")
    check(r.get("cellCap") == area,
          "9 cellCap is the map area under summary", repr(r.get("cellCap")))
    check(isinstance(r.get("capReason"), str) and r["capReason"],
          "9 capReason says which cap applied", repr(r.get("capReason")))
    ms = r.get("mapSize") or {}
    check(ms.get("x") == mw and ms.get("z") == mh and ms.get("cells") == area,
          "9 mapSize matches the map rim.map_size() found", repr(ms))

    summ = r.get("summary") or {}
    things = summ.get("things") or {}
    check(isinstance(summ.get("things"), dict) and things,
          "9 summary.things aggregates the whole map by def",
          repr(list(things)[:5]))
    forb = {d: t.get("forbidden") for d, t in things.items()}
    check(all(isinstance(v, int) for v in forb.values()),
          "9 every summary.things row carries an integer forbidden count",
          repr({d: v for d, v in forb.items() if not isinstance(v, int)}))
    print(f"      {len(things)} thing defs,"
          f" {sum(v for v in forb.values() if isinstance(v, int))} forbidden,"
          f" {len(summ.get('pawns') or [])} pawns,"
          f" {len(summ.get('terrain') or {})} terrain defs")
    check(sum((summ.get("terrain") or {}).values()) == area,
          "9 summary.terrain totals the whole map",
          f"{sum((summ.get('terrain') or {}).values())} != {area}")

    # The cap that did NOT move: 1025 cells with no summary.
    big = {"x": 0, "z": 0, "width": 41, "height": 25}          # 41 * 25 == 1025
    print(f"\n--- 9b the 1024-cell cap still holds: {TOOL} {json.dumps(big)}")
    try:
        r2 = rim.game(TOOL, big, strict=False)
    except Exception as e:
        check(False, "9b the 1025-cell rect answered at all",
              f"{type(e).__name__}: {e}")
        return
    check(isinstance(r2, dict) and r2.get("success") is False,
          "9b a 1025-cell non-summary rect is REFUSED",
          f"success={r2.get('success') if isinstance(r2, dict) else r2}")
    err = str(r2.get("error") if isinstance(r2, dict) else r2)
    print("      " + err[:240])
    check("1024" in err, "9b the refusal names the 1024-cell cap it applied")


def reconcile(s, full, cells_in_rect):
    """Every summary section against the same rectangle's cells[].

    The two disagree by design in exactly one way: cells[] reports a multi-cell
    thing once per cell and the summary counts it once. So anything that crosses
    that line is checked as an inequality with BOTH numbers printed, and
    anything that does not (terrain, roof, fog, walkability, zone cells) is
    checked for exact equality.
    """
    cells = full.get("cells") or []

    terrain = collections.Counter(
        c.get("terrainDefName") or "(none)" for c in cells)
    check(dict(s.get("terrain") or {}) == dict(terrain),
          "I1 summary.terrain matches the cell walk exactly",
          f"summary {dict(s.get('terrain') or {})} vs cells {dict(terrain)}")

    roof = collections.Counter(c.get("roofDefName") or "unroofed" for c in cells)
    check(dict(s.get("roof") or {}) == dict(roof),
          "I2 summary.roof matches, with 'unroofed' for the roofless cells",
          f"summary {dict(s.get('roof') or {})} vs cells {dict(roof)}")
    check(sum((s.get("roof") or {}).values()) == cells_in_rect,
          "I2 summary.roof totals the whole rectangle",
          f"{sum((s.get('roof') or {}).values())} != {cells_in_rect}")

    check(s.get("fogged") == sum(1 for c in cells if c.get("fogged")),
          "I3 summary.fogged matches the cell walk",
          f"{s.get('fogged')} vs {sum(1 for c in cells if c.get('fogged'))}")

    for key in ("walkable", "passable"):
        got = s.get(key) or {}
        want = {"true": sum(1 for c in cells if c.get(key) is True),
                "false": sum(1 for c in cells if c.get(key) is False),
                "unknown": sum(1 for c in cells
                               if not isinstance(c.get(key), bool))}
        check({k: got.get(k) for k in want} == want,
              f"I4 summary.{key} true/false/unknown match the cell walk",
              f"summary {got} vs cells {want}")

    zone_cells = collections.Counter(c["zoneId"] for c in cells if c.get("zoneId"))
    got = {k: v.get("cellsInRect") for k, v in (s.get("zones") or {}).items()}
    check(got == dict(zone_cells),
          "I5 summary.zones cellsInRect matches the cell walk",
          f"summary {got} vs cells {dict(zone_cells)}")

    area_cells = collections.Counter(
        a for c in cells for a in (c.get("areaIds") or []))
    got = {k: v.get("cellsInRect") for k, v in (s.get("areas") or {}).items()}
    check(got == dict(area_cells),
          "I6 summary.areas cellsInRect matches the cell walk",
          f"summary {got} vs cells {dict(area_cells)}")

    # --- the two that cross the de-duplication line ------------------------
    raw_stacks = collections.Counter()
    raw_count = collections.Counter()
    raw_labels = {}
    pawn_entries = 0
    for c in cells:
        for t in c.get("things") or []:
            d = t.get("defName") or "(none)"
            raw_stacks[d] += 1
            raw_count[d] += t.get("stackCount") or 1
            raw_labels.setdefault(d, t.get("label"))
            if (t.get("className") or "").rsplit(".", 1)[-1] == "Pawn":
                pawn_entries += 1
    things = s.get("things") or {}
    check(set(things) == set(raw_stacks),
          "I7 summary.things names exactly the defNames the cells hold",
          f"only in summary {sorted(set(things) - set(raw_stacks))};"
          f" only in cells {sorted(set(raw_stacks) - set(things))}")
    over = {d: (things[d].get("stacks"), raw_stacks[d]) for d in things
            if things[d].get("stacks", 0) > raw_stacks.get(d, 0)}
    check(not over,
          "I8 summary.things stacks <= the per-cell entry count (de-duplicated)",
          f"these count MORE than the cells do, which is impossible: {over}")
    overc = {d: (things[d].get("count"), raw_count[d]) for d in things
             if (things[d].get("count") or 0) > raw_count.get(d, 0)}
    check(not overc,
          "I8 summary.things count <= the per-cell stackCount total", str(overc))
    multi = {d: (things[d].get("stacks"), raw_stacks[d]) for d in things
             if things[d].get("stacks") != raw_stacks.get(d)}
    print(f"      {len(multi)} defName(s) counted fewer times in the summary than"
          " in cells[] -- these are the multi-cell things, and that difference"
          f" is the de-duplication working: {dict(list(multi.items())[:6])}")

    pawns = s.get("pawns")
    check(isinstance(pawns, list) and len(pawns) == pawn_entries,
          "I9 summary.pawns has one entry per pawn in the cell walk",
          f"summary {len(pawns or [])} ({pawns}) vs cells {pawn_entries}")

    raw_desig = collections.Counter()
    for c in cells:
        for d in c.get("designations") or []:
            raw_desig[d.get("defName") or "(none)"] += 1
    desig = s.get("designations") or {}
    check(set(desig) == set(raw_desig),
          "I10 summary.designations names exactly the defNames the cells hold",
          f"summary {sorted(desig)} vs cells {sorted(raw_desig)}")
    overd = {d: (desig[d], raw_desig[d]) for d in desig
             if desig[d] > raw_desig.get(d, 0)}
    check(not overd,
          "I10 summary.designations counts <= the per-cell totals", str(overd))
    print(f"      designations: summary {dict(desig)}  vs per-cell"
          f" {dict(raw_desig)}  (a designation on a multi-cell thing is"
          " reported at every one of its cells and counted once here)")


def report():
    print("\n" + "=" * 72)
    print("PAYLOAD SIZES over the same 32x32 rectangle"
          " (json.dumps separators=(',',':')):")
    base = dict(SIZES).get("default")
    for note, n in SIZES:
        share = f"   {100.0 * n / base:5.1f}% of default" if base else ""
        print(f"  {note:<38s} {n:>8d} bytes  {n / 1024.0:6.1f} KB{share}")
    print("  (for WANTED 16's table. rimworld/get_cells_info measured 697.8 KB"
          " and home/get_cells_plus 197.9 KB on day 39.)")
    print("=" * 72)
    print(f"{CHECKS[0] - len(FAILURES)} of {CHECKS[0]} checks passed.")
    for f in FAILURES:
        print("  FAIL " + f)
    print("""
Then run these by hand, in rimworld\\instruments, and read them:

    python map.py 112 140              # the ordinary view, on the narrowed sweep
    python map.py 112 140 --full       # all seven layers off the same payload
    python inv.py --cells 100 130 130 160 --all    # the sparse things-only sweep
    python watch.py --once             # the threat sweep, if the companion is up

What to look for:
  * ACCOUNTING's `fields asked for:` line names the two lists, and the layer
    footers below it are unchanged from a --stock run's shape;
  * no DEGRADED line -- if one appears, normalise_plus() found a cell with no
    `walkable`, which now raises instead of defaulting to True;
  * `inv.py --cells` finds the same things it found before the narrowing.
""")


if __name__ == "__main__":
    main()
    sys.exit(1 if FAILURES else 0)
