"""Live load test for `inspect: true` on `home/list_buildings` (WANTED 14).

READ-ONLY. Every call it makes is a read tool. It never saves, never unpauses,
never selects anything and never places or removes anything -- which is the
point of the feature under test: `Thing.GetInspectString()` has no relationship
to the selection, so reading the inspect pane no longer needs the click that
fails on a multi-cell building anyway. Run it with the game loaded and PAUSED;
it leaves the game exactly as it found it.

    cd rimworld\\instruments
    python ..\\companion\\tests\\live_list_buildings_inspect.py

Nothing below has ever been run against a live game. The DLL was built and the
API decompiled; every assertion here is a PROMISE being checked for the first
time. A failure is information, not necessarily a bug in the caller.

What it checks, and what a failure of each one would mean:

  1  default call has NO inspectString on any row, and no inspectSkipped[]
     -- a failure means the option is not opt-in and the 17.2 KB default call
        has silently grown.
  2  filters.inspect is present in BOTH modes
     -- a failure means a caller cannot tell "inspectSkipped is empty because
        every string read" from "the key is absent because nobody looked".
  3  inspect:true puts the key on EVERY buildings[] row
     -- a failure means some rows are silently without it, which is the absent-
        field bug this whole companion is built against.
  4  a null inspectString appears ONLY for a def named in inspectSkipped[]
     -- a failure means a read threw and said nothing, i.e. null is ambiguous
        between "nothing to say" and "we could not ask". Empty string is the
        legitimate "nothing to say".
  5  no aggregated[] row carries inspectString
     -- a failure means a def row is being given one thing's text as if it
        described all eleven conduits.
  6  the two calls return the SAME rows in the SAME order, and every row is
     byte-identical once inspectString is removed
     -- a failure means inspect:true changed the census itself, not just added
        a field. That would make the option unusable in a Scout brief.
  7  a building with a power block has 'power' (case-insensitively) in its
     inspect string
     -- this is the WANTED 14 ask by name. A failure means CompInspectStringExtra
        text is NOT reaching the payload, i.e. we are getting Thing's base
        string and not ThingWithComps'.
  8  a bogus argument key lands in unknownArguments[]
     -- a failure means the argument declaration is out of step with the binder.
  9  inspectSkippedThings equals the sum of the inspectSkipped[] counts
     -- a failure means two accumulators for one fact, the count/positions bug
        of 2026-09-02 in a new place.
"""
import sys, os, json

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "instruments"))
import rim

TOOL = "home/list_buildings"
FAILURES = []
CHECKS = [0]


def check(ok, label, detail=""):
    CHECKS[0] += 1
    if ok:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s   %s" % (label, detail))
        FAILURES.append(label + ("   " + detail if detail else ""))


def call(args, label):
    print("\n--- %s: %s %s" % (label, TOOL, json.dumps(args)))
    try:
        r = rim.game(TOOL, args)
    except Exception as e:
        check(False, label + " returned at all", "%s: %s" % (type(e).__name__, e))
        return None
    if not isinstance(r, dict) or "buildings" not in r:
        check(False, label + " returned a buildings[]", str(r)[:200])
        return None
    print("      %d detailed, %d aggregated rows, %d scanned; payload ~%d KB"
          % (len(r.get("buildings") or []), len(r.get("aggregated") or []),
             (r.get("counts") or {}).get("scanned", -1),
             len(json.dumps(r)) // 1024))
    return r


def identity(row):
    """What makes a row THAT building, for the same-rows-same-order check."""
    p = row.get("position") or {}
    return (row.get("defName"), p.get("x"), p.get("z"), row.get("status"))


def main():
    rim.init()
    print("=" * 72)
    print("home/list_buildings inspect:true -- live load test. READ ONLY.")
    print("Nothing is saved, nothing is selected, the game is not unpaused.")
    print("=" * 72)

    # ------------------------------------------------------------------ 1 --
    plain = call({}, "1 default (no inspect)")
    if plain is None:
        return report()
    rows = plain.get("buildings") or []
    check(all("inspectString" not in b for b in rows),
          "1 default: NO row carries inspectString",
          "%d of %d rows did" % (sum(1 for b in rows if "inspectString" in b), len(rows)))
    check("inspectSkipped" not in plain,
          "1 default: inspectSkipped[] is absent (nothing was attempted)")
    check((plain.get("filters") or {}).get("inspect") is False,
          "2 default: filters.inspect is present and False",
          json.dumps((plain.get("filters") or {}).get("inspect")))

    # ------------------------------------------------------------------ 3 --
    ins = call({"inspect": True}, "3 inspect:true")
    if ins is None:
        return report()
    irows = ins.get("buildings") or []
    check((ins.get("filters") or {}).get("inspect") is True,
          "2 inspect: filters.inspect is present and True")
    missing = [identity(b) for b in irows if "inspectString" not in b]
    check(not missing,
          "3 inspect: every detailed row has an inspectString key",
          "%d without it, e.g. %s" % (len(missing), missing[:3]))

    # ------------------------------------------------------------------ 4 --
    skipped = ins.get("inspectSkipped")
    check(isinstance(skipped, list),
          "4 inspect: inspectSkipped[] is present as a list",
          repr(skipped)[:120])
    skipped_defs = {e.get("defName") for e in (skipped or []) if isinstance(e, dict)}
    nulls = [b for b in irows if b.get("inspectString") is None
             and "inspectString" in b]
    orphan = [identity(b) for b in nulls if b.get("defName") not in skipped_defs]
    check(not orphan,
          "4 inspect: a null inspectString is always named in inspectSkipped[]",
          "%d orphan null(s), e.g. %s; skipped defs %s"
          % (len(orphan), orphan[:3], sorted(skipped_defs)))
    empties = sum(1 for b in irows if b.get("inspectString") == "")
    texts = sum(1 for b in irows if b.get("inspectString"))
    print("      %d rows with text, %d with an empty string (nothing to say), "
          "%d null (threw)" % (texts, empties, len(nulls)))
    for e in skipped or []:
        print("      ** inspectSkipped: %s" % json.dumps(e))

    # ------------------------------------------------------------------ 9 --
    if isinstance(skipped, list):
        check(ins.get("inspectSkippedThings") == sum(e.get("count", 0) for e in skipped),
              "9 inspect: inspectSkippedThings == sum of inspectSkipped[].count",
              "%s vs %s" % (ins.get("inspectSkippedThings"),
                            sum(e.get("count", 0) for e in skipped)))

    # ------------------------------------------------------------------ 5 --
    agg = ins.get("aggregated") or []
    check(all("inspectString" not in a for a in agg),
          "5 inspect: NO aggregated row carries inspectString",
          "%d did" % sum(1 for a in agg if "inspectString" in a))

    # ------------------------------------------------------------------ 6 --
    check([identity(b) for b in rows] == [identity(b) for b in irows],
          "6 inspect: the same rows in the same order as the default call",
          "%d rows vs %d rows" % (len(rows), len(irows)))
    if len(rows) == len(irows):
        diffs = []
        for a, b in zip(rows, irows):
            stripped = {k: v for k, v in b.items() if k != "inspectString"}
            if stripped != a:
                diffs.append((identity(a),
                              sorted(set(stripped) ^ set(a)),
                              [k for k in set(stripped) & set(a) if stripped[k] != a[k]]))
        check(not diffs,
              "6 inspect: every row is identical once inspectString is removed",
              "%d differ, e.g. %s" % (len(diffs), diffs[:2]))
    check([a.get("defName") for a in (plain.get("aggregated") or [])]
          == [a.get("defName") for a in agg],
          "6 inspect: aggregated[] is the same defs in the same order")

    # ------------------------------------------------------------------ 7 --
    powered = [b for b in irows
               if isinstance(b.get("power"), dict)
               and b["power"].get("powered") is True
               and isinstance(b.get("inspectString"), str)]
    any_power = [b for b in irows if isinstance(b.get("power"), dict)
                 and isinstance(b.get("inspectString"), str)]
    if not any_power:
        check(True, "7 power: VACUOUS -- no row on this map has a power block "
                    "AND a readable inspect string, so the comp-text check "
                    "could not run. This is NOT a pass of the WANTED 14 ask.")
    else:
        pick = powered[0] if powered else any_power[0]
        print("      chose %s at %s: %r"
              % (pick.get("defName"), identity(pick)[1:3], pick.get("inspectString")))
        check("power" in (pick.get("inspectString") or "").lower(),
              "7 power: a powered building's inspect string mentions power "
              "(CompInspectStringExtra reached the payload)",
              "%r -- if this fails we are getting Thing.GetInspectString and "
              "not ThingWithComps'" % pick.get("inspectString"))
        if not powered:
            print("      (nothing on this map is currently POWERED; the check ran "
                  "against an unpowered one, whose string should say so)")
        hits = sum(1 for b in any_power
                   if "power" in (b.get("inspectString") or "").lower())
        print("      %d of %d rows with a power block mention power" % (hits, len(any_power)))

    # ------------------------------------------------------------------ 8 --
    bogus = call({"inspect": True, "bogusKeyXYZ": True, "Inspect": 1},
                 "8 a bogus key")
    if bogus is not None:
        ua = bogus.get("unknownArguments")
        print("      unknownArguments: %s" % ua)
        check(ua == ["Inspect", "bogusKeyXYZ"],
              "8 bogus: unknownArguments names both keys, sorted, "
              "case-sensitively (Inspect != inspect)", str(ua))
        check(bool(bogus.get("unknownArgumentsWarning")),
              "8 bogus: a warning sentence is present")

    return report()


def report():
    print("\n" + "=" * 72)
    print("%d of %d checks passed." % (CHECKS[0] - len(FAILURES), CHECKS[0]))
    for f in FAILURES:
        print("  FAIL " + f)
    print("""
Then run these by hand, in rimworld\\instruments, and read them:

    python buildings.py --inspect
    python buildings.py --inspect --every        # one row per wall, each with text
    python buildings.py --inspect --pending      # frames: "Steel: 12 / 25"

What to look for:
  * an unpowered thing's line says "Not connected to a power grid" or a wattage;
  * a FRAME's line carries its contained resources, which is the same fact the
    row's resources[] already gives structurally -- if they disagree, the
    structured one is the one that was computed, and that is a finding;
  * no line contains a `<color=...>` or `</color>` tag;
  * the footer's inspect line accounts for every detailed row.
""")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
