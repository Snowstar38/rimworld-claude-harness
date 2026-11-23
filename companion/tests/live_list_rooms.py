"""Live load test for `home/list_rooms` and the seventh map layer.

READ-ONLY. It never saves, never unpauses, never places or removes anything, and
never touches the selection: every call it makes is a read tool. Run it with the
game already loaded and PAUSED; it leaves the game exactly as it found it.

    cd rimworld\\instruments
    python ..\\companion\\tests\\live_list_rooms.py

Nothing below has ever been run against a live game. The DLL was built and
decompiled, and `map.py`'s layer was exercised against a synthetic reply with no
bridge at all -- so every assertion here is a PROMISE being checked for the first
time, not a regression test. A failure is information.

What it checks, and why each one:

  1. clean call            -- the whole-map census answers at all
  2. with a rect           -- roomGrid comes back and every index is valid
  3. with a cell           -- x,z alone answers "which room covers this"
  4. includeOutdoors       -- the mega-room appears and the omitted counts move
  5. cells:true, one room  -- the cell listing matches the cell count
  6. a bogus key           -- unknownArguments[] actually reports it

  I1  every non-null roomGrid value is a valid index into rooms[]
  I2  cellCount == len(cells) + cellsNotListed, and == len(cells) when complete
  I3  `home/get_temperatures {mode:rooms}` over the SAME rect agrees with
      `home/list_rooms` about which ROOM IDS cover which cells -- the two tools
      now share BridgeCommon.RoomWalk, so a disagreement means the refactor
      changed behaviour, which is the one thing it was not allowed to do
  I4  roomCount + roomsOmitted == roomCountTotal
  I5  the three omission counts add up to roomsOmitted
"""
import sys, os, json

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "instruments"))
import rim

TOOL = "home/list_rooms"
FAILURES = []
CHECKS = [0]


def check(ok, label, detail=""):
    CHECKS[0] += 1
    if ok:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}   {detail}")
        FAILURES.append(label + ("   " + detail if detail else ""))


def call(args, label):
    print(f"\n--- {label}: {TOOL} {json.dumps(args)}")
    try:
        r = rim.game(TOOL, args)
    except Exception as e:
        check(False, label + " returned at all", f"{type(e).__name__}: {e}")
        return None
    if not isinstance(r, dict) or "rooms" not in r:
        check(False, label + " returned a rooms[]", str(r)[:200])
        return None
    print(f"      {r.get('roomCount')} listed / {r.get('roomCountTotal')} total;"
          f" omitted {r.get('roomsOmitted')}"
          f" ({r.get('outdoorRoomsOmitted')} outdoors,"
          f" {r.get('doorwaysOmitted')} doorways,"
          f" {r.get('dereferencedRoomsOmitted')} dereferenced);"
          f" payload ~{len(json.dumps(r)) // 1024} KB")
    return r


def summarise(r, limit=8):
    for room in (r.get("rooms") or [])[:limit]:
        e = room.get("extents") or {}
        print(f"      [{room.get('index')}] {room.get('name')}"
              f"  id {room.get('id')}"
              f"  at ({e.get('x')},{e.get('z')}) {e.get('width')}x{e.get('height')}"
              f"  {room.get('cellCount')} cells  {room.get('temperature')}C"
              f"  pawns {room.get('pawnCount')}"
              f"  beds {room.get('bedCount')}"
              f"  stockpiles {len(room.get('stockpiles') or [])}"
              f"  contents {len(room.get('contents') or [])}")
        if room.get("skipped"):
            print("        ** skipped: " + "; ".join(room["skipped"]))
    extra = len(r.get("rooms") or []) - limit
    if extra > 0:
        print(f"      (+{extra} more rooms)")


def structural(r, label):
    """The invariants that hold on every reply, whatever was asked."""
    total = r.get("roomCountTotal")
    listed = r.get("roomCount")
    omitted = r.get("roomsOmitted")
    check(listed is not None and omitted is not None and total == listed + omitted,
          f"I4 {label}: roomCount + roomsOmitted == roomCountTotal",
          f"{listed} + {omitted} != {total}")
    parts = (r.get("outdoorRoomsOmitted"), r.get("doorwaysOmitted"),
             r.get("dereferencedRoomsOmitted"))
    check(None not in parts and sum(parts) == omitted,
          f"I5 {label}: the three omission counts sum to roomsOmitted",
          f"{parts} != {omitted}")
    check(len(r.get("rooms") or []) == listed,
          f"{label}: rooms[] length == roomCount")
    check(len(r.get("omitted") or []) == omitted,
          f"{label}: omitted[] length == roomsOmitted")
    for i, room in enumerate(r.get("rooms") or []):
        if room.get("index") != i:
            check(False, f"{label}: rooms[{i}].index == {i}",
                  f"got {room.get('index')}")
            return
    check(True, f"{label}: every rooms[i].index == i")
    check("unknownArguments" in r, f"{label}: unknownArguments[] is present")


def main():
    rim.init()
    print("=" * 72)
    print("home/list_rooms -- live load test. READ ONLY. Nothing is saved.")
    print("=" * 72)

    # ---------------------------------------------------------------- 1 ----
    clean = call({}, "1 clean")
    if clean is None:
        report()
        return
    summarise(clean)
    structural(clean, "clean")
    check(bool(clean.get("rooms")),
          "clean: at least one room is listed",
          "no room passed the filter -- is the colony one open field?")
    check("roomGrid" not in clean,
          "clean: NO roomGrid when no rectangle was asked for")
    check("cell" not in clean,
          "clean: NO cell answer when no x,z was asked for")

    rooms = clean.get("rooms") or []
    if not rooms:
        report()
        return

    # A small, real room to aim the cells:true and cell answers at.
    small = min(rooms, key=lambda q: q.get("cellCount") or 1 << 30)
    print(f"\n      smallest listed room: [{small.get('index')}]"
          f" {small.get('name')} at {small.get('center')}"
          f" with {small.get('cellCount')} cells")

    # ---------------------------------------------------------------- 2 ----
    # A rect around that room's extents, clipped to something modest.
    e = small.get("extents") or {}
    rx, rz = max(0, (e.get("x") or 0) - 2), max(0, (e.get("z") or 0) - 2)
    rw, rh = min(24, (e.get("width") or 1) + 4), min(24, (e.get("height") or 1) + 4)
    rect = call({"x": rx, "z": rz, "width": rw, "height": rh}, "2 with a rect")
    if rect is not None:
        structural(rect, "rect")
        grid = rect.get("roomGrid")
        check(isinstance(grid, list) and len(grid) == rh,
              "rect: roomGrid has one row per rect row",
              f"{None if grid is None else len(grid)} != {rh}")
        if isinstance(grid, list):
            check(all(isinstance(row, list) and len(row) == rw for row in grid),
                  "rect: every roomGrid row is rect.width long")
            n = len(rect.get("rooms") or [])
            bad = [v for row in grid for v in row
                   if v is not None and not (isinstance(v, int) and 0 <= v < n)]
            check(not bad,
                  "I1 rect: every non-null roomGrid index is a valid rooms[] index",
                  f"{len(bad)} bad values, e.g. {bad[:5]} against {n} rooms")
            drawn = sum(1 for row in grid for v in row if v is not None)
            print(f"      grid: {drawn} cells claimed by a listed room,"
                  f" {rect.get('gridCellsInUnlistedRooms')} by an omitted one,"
                  f" {rect.get('gridCellsWithNoRoom')} by none,"
                  f" {rect.get('gridCellsOutOfBounds')} off the map")

    # ---------------------------------------------------------------- 3 ----
    c = small.get("center") or {}
    cell = call({"x": c.get("x"), "z": c.get("z")}, "3 with a cell")
    if cell is not None:
        structural(cell, "cell")
        ans = cell.get("cell") or {}
        print(f"      cell answer: {json.dumps(ans)}")
        check("roomGrid" not in cell,
              "cell: NO roomGrid when only x,z was asked for")
        check(ans.get("found") is True,
              "cell: the centre of a listed room resolves to a listed room",
              json.dumps(ans))
        idx = ans.get("roomIndex")
        got = (cell.get("rooms") or [])[idx] if isinstance(idx, int) and \
            0 <= idx < len(cell.get("rooms") or []) else None
        check(got is not None and got.get("id") == small.get("id"),
              "cell: the room it names is the room we aimed at",
              f"expected id {small.get('id')}, got {None if got is None else got.get('id')}")

    # ---------------------------------------------------------------- 4 ----
    out = call({"includeOutdoors": True}, "4 includeOutdoors")
    if out is not None:
        structural(out, "includeOutdoors")
        check((out.get("roomCount") or 0) >= (clean.get("roomCount") or 0),
              "includeOutdoors: lists at least as many rooms as the clean call")
        check((out.get("roomsOmitted") or 0) <= (clean.get("roomsOmitted") or 0),
              "includeOutdoors: omits no more than the clean call")
        biggest = max(out.get("rooms") or [],
                      key=lambda q: q.get("cellCount") or 0, default=None)
        if biggest is not None:
            print(f"      biggest room now listed: {biggest.get('name')}"
                  f" {biggest.get('cellCount')} cells"
                  f" psychologicallyOutdoors={biggest.get('psychologicallyOutdoors')}")
        check(any(q.get("psychologicallyOutdoors") for q in out.get("rooms") or []),
              "includeOutdoors: at least one listed room is psychologicallyOutdoors",
              "the flag never turned up -- is this colony entirely enclosed?")

    # ---------------------------------------------------------------- 5 ----
    # cells:true on the one small room, by asking for the rect that contains it.
    # There is no per-room selector, so this asks the whole census with cells on
    # and then checks the invariant on EVERY room it got back.
    withcells = call({"cells": True}, "5 cells:true")
    if withcells is not None:
        structural(withcells, "cells")
        ok = True
        worst = None
        for room in withcells.get("rooms") or []:
            listed = len(room.get("cells") or [])
            missing = room.get("cellsNotListed")
            total = room.get("cellCount")
            if None in (missing, total) or listed + missing != total:
                ok = False
                worst = (room.get("name"), listed, missing, total)
                break
        check(ok, "I2 cells: cellCount == len(cells) + cellsNotListed on every room",
              str(worst))
        target = next((q for q in withcells.get("rooms") or []
                       if q.get("id") == small.get("id")), None)
        if target is not None:
            check(target.get("cellsComplete") is True
                  and len(target.get("cells") or []) == target.get("cellCount"),
                  "I2 cells: the small room lists exactly cellCount cells",
                  f"complete={target.get('cellsComplete')}"
                  f" listed={len(target.get('cells') or [])}"
                  f" cellCount={target.get('cellCount')}")
            cs = {(p.get("x"), p.get("z")) for p in target.get("cells") or []}
            check(len(cs) == len(target.get("cells") or []),
                  "cells: no cell is listed twice")

    # ---------------------------------------------------------------- 6 ----
    bogus = call({"bogusKeyXYZ": True, "MaxRows": 3}, "6 a bogus key")
    if bogus is not None:
        ua = bogus.get("unknownArguments")
        print(f"      unknownArguments: {ua}")
        print(f"      warning: {bogus.get('unknownArgumentsWarning')}")
        check(ua == ["MaxRows", "bogusKeyXYZ"],
              "bogus: unknownArguments names both keys, sorted, case-sensitively",
              str(ua))
        check(bool(bogus.get("unknownArgumentsWarning")),
              "bogus: a warning sentence is present")

    # ---------------------------------------------------------------- I3 ---
    # The refactor check. Both tools now walk the rect through the same
    # BridgeCommon.RoomWalk, so they must agree cell for cell about room IDS.
    if rect is not None and rect.get("roomGrid"):
        print(f"\n--- I3: home/get_temperatures {{mode:rooms}} over the same rect")
        try:
            t = rim.game("home/get_temperatures",
                         {"x": rx, "z": rz, "width": rw, "height": rh,
                          "mode": "rooms"})
        except Exception as ex:
            check(False, "I3: get_temperatures answered", f"{type(ex).__name__}: {ex}")
            t = None
        if t is not None:
            tid = {q.get("index"): q.get("id") for q in t.get("rooms") or []}
            lid = {q.get("index"): q.get("id") for q in rect.get("rooms") or []}
            tg, lg = t.get("roomGrid") or [], rect.get("roomGrid") or []
            print(f"      temperatures saw {len(tid)} rooms in the rect;"
                  f" list_rooms' grid covers {rect.get('gridRoomsInRect')}")
            mismatches = []
            for i in range(min(len(tg), len(lg))):
                for j in range(min(len(tg[i]), len(lg[i]))):
                    a = tid.get(tg[i][j]) if tg[i][j] is not None else None
                    b = lid.get(lg[i][j]) if lg[i][j] is not None else None
                    # list_rooms nulls a cell whose room it did not LIST; the
                    # temperature tool lists every room it meets. So a null on
                    # the list_rooms side against an id on the other is the
                    # filter working, not a disagreement. Anything else is one.
                    if b is None:
                        continue
                    if a != b:
                        mismatches.append(((rx + j, rz + i), a, b))
            check(not mismatches,
                  "I3: the two tools agree on the room ID of every cell"
                  " list_rooms claims",
                  f"{len(mismatches)} disagreements, e.g. {mismatches[:3]}")
            filtered = sum(
                1 for i in range(min(len(tg), len(lg)))
                for j in range(min(len(tg[i]), len(lg[i])))
                if lg[i][j] is None and tg[i][j] is not None)
            print(f"      {filtered} cell(s) have a room in get_temperatures and"
                  " none in list_rooms -- that is the outdoors/doorway filter,"
                  " and it should equal gridCellsInUnlistedRooms"
                  f" ({rect.get('gridCellsInUnlistedRooms')})")
            check(filtered == rect.get("gridCellsInUnlistedRooms"),
                  "I3: gridCellsInUnlistedRooms counts exactly the filtered cells",
                  f"{filtered} != {rect.get('gridCellsInUnlistedRooms')}")

    report()


def report():
    print("\n" + "=" * 72)
    print(f"{CHECKS[0] - len(FAILURES)} of {CHECKS[0]} checks passed.")
    for f in FAILURES:
        print("  FAIL " + f)
    print("""
Then run these by hand, in rimworld\\instruments, and read them:

    python map.py rooms                     # the whole-map census
    python map.py rooms --outdoors          # with the mega-room and doorways
    python map.py room <x> <z>              # one cell's room, in full
    python map.py <x> <z>                   # default view: ONE room key line
    python map.py <x> <z> --layers rooms    # the room grid itself
    python map.py <x> <z> --full            # all seven pure layers
    python map.py --legend                  # layer 7 in the key

What to look for:
  * the key characters in `--layers rooms` match the indexes `map.py rooms`
    prints, and the same room wears the same character in both;
  * walls draw '#', the outdoors and every doorway draw BLANK, and the footer
    says how many cells that filter accounted for;
  * `cells drawn as a room` plus the three grid counts in the layer-7 footer
    cover every cell in the bounds;
  * `map.py room <x> <z>` on a wall says "no LISTED room covers that cell" and
    gives a reason rather than an empty answer.
""")


if __name__ == "__main__":
    main()
