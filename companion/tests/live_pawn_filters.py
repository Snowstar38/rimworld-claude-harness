"""Live load test for `home/list_pawns`' category filters (WANTED 2).

READ-ONLY. Every call is `home/list_pawns`, which reads RimWorld's own spawned
pawn list and touches nothing. It never saves, never unpauses, never selects,
never writes. Run it with the game loaded and PAUSED; it leaves the colony
exactly as it found it.

    cd rimworld\\instruments
    python ..\\companion\\tests\\live_pawn_filters.py

The filters exist so a caller stops doing the narrowing by eye over a whole-map
list. That only helps if a narrowed list is the SAME list, so every check here
is against one unfiltered payload read once at the start: the tool's answer must
equal the answer computed from the unfiltered rows, not merely be a subset of
them. A filter that drops one pawn too many is as wrong as one that drops none.

What it checks, and what a failure of each one would mean:

  1  the unfiltered baseline
       Every row carries the nine bools the filters key off (wild, tame, animal,
       humanlike, mechanoid, isFreeColonist, isPrisoner, downed, drafted) and
       filters{} echoes all ten filter names. FAILURE = a filter cannot be
       verified from the payload at all, which is the absent-vs-false trap this
       tool has paid for twice.

  2  each filter alone
       The returned set is EXACTLY the unfiltered rows satisfying that filter's
       own row bool, every returned row satisfies it, filters{} echoes that one
       flag true and the others false, and pawnsListed / pawnsFiltered close
       against spawnedPawnTotal. FAILURE = the filter and the row disagree about
       the same pawn, or a count says something the list does not.

  3  two filters together
       animalsOnly + downedOnly returns the intersection, not the union.
       FAILURE = the filters do not combine by AND.

  4  the conflicting pairs
       success:false with a reason naming both. FAILURE = a contradiction was
       answered with an empty list, which is also what a safe map looks like.

  5  nameFilter
       A substring taken from a real pawn narrows to exactly the unfiltered rows
       whose name / defName / kindDef contain it, case-insensitively; a
       whitespace-only nameFilter is refused. FAILURE = the server-side filter
       and the client-side one the instruments used to do disagree.

  6  a bogus key
       lands in unknownArguments[].

A filter with no matching pawn on this map (no mechanoid, nobody drafted in a
paused colony) still passes: the check is an equality against the unfiltered
rows, and the run prints "0 == 0" so it is visible that nothing was exercised.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "instruments"))
import rim

TOOL = "home/list_pawns"
FAILURES = []
CHECKS = [0]

# filter argument -> the row bool it must agree with, on every pawn.
FILTERS = [
    ("wildOnly", "wild"),
    ("tameOnly", "tame"),
    ("animalsOnly", "animal"),
    ("humanlikeOnly", "humanlike"),
    ("mechanoidsOnly", "mechanoid"),
    ("colonistsOnly", "isFreeColonist"),
    ("prisonersOnly", "isPrisoner"),
    ("downedOnly", "downed"),
    ("draftedOnly", "drafted"),
]

FILTER_NAMES = [name for name, _ in FILTERS]

# Pairs the tool must REFUSE. Each is a fact about race or faction, not a
# preference: see PawnFilters.ConflictingPairs in ListPawnsTool.cs.
CONFLICTS = [
    {"wildOnly": True, "tameOnly": True},
    {"animalsOnly": True, "colonistsOnly": True},
    {"colonistsOnly": True, "prisonersOnly": True},
    {"humanlikeOnly": True, "mechanoidsOnly": True},
    {"tameOnly": True, "prisonersOnly": True},
    {"colonistsOnly": True, "includeColonists": False},
]


def check(ok, label, detail=""):
    CHECKS[0] += 1
    if ok:
        print("  ok   " + label)
    else:
        print("  FAIL %s   %s" % (label, detail))
        FAILURES.append(label + ("   " + detail if detail else ""))


def call(args, label):
    print("\n--- %s: %s %s" % (label, TOOL, json.dumps(args)))
    try:
        return rim.game(TOOL, args, strict=False)
    except Exception as e:
        check(False, label + " returned at all", "%s: %s" % (type(e).__name__, e))
        return None


def key(p):
    """A pawn's identity across two calls. Nothing in the default payload is a
    unique id, but name + position + defName is: two pawns cannot share a cell.
    """
    pos = p.get("position") or {}
    return (p.get("name"), p.get("defName"), pos.get("x"), pos.get("z"))


def counts_close(r, label):
    listed, filtered = r.get("pawnsListed"), r.get("pawnsFiltered")
    total = r.get("spawnedPawnTotal")
    check(listed == len(r.get("pawns") or []),
          "%s: pawnsListed == len(pawns)" % label,
          "%s vs %s" % (listed, len(r.get("pawns") or [])))
    check(isinstance(listed, int) and isinstance(filtered, int)
          and isinstance(total, int) and listed + filtered == total,
          "%s: pawnsListed + pawnsFiltered == spawnedPawnTotal" % label,
          "%s + %s != %s" % (listed, filtered, total))


def main():
    rim.init()
    print("=" * 72)
    print("home/list_pawns -- the category filters. READ ONLY.")
    print("=" * 72)

    # ------------------------------------------------------------------ 1 ---
    base = call({}, "1 the unfiltered baseline")
    if not isinstance(base, dict) or not base.get("success"):
        check(False, "1 the unfiltered call succeeded", str(base)[:200])
        return report()
    rows = base.get("pawns") or []
    print("      %d pawn(s); spawnedPawnTotal %s, colonistCount %s, hostileCount %s"
          % (len(rows), base.get("spawnedPawnTotal"), base.get("colonistCount"),
             base.get("hostileCount")))
    counts_close(base, "baseline")

    echo = base.get("filters") or {}
    missing = [n for n in FILTER_NAMES + ["nameFilter"] if n not in echo]
    check(not missing, "1 filters{} echoes every filter name", str(missing))
    check(all(echo.get(n) is False for n in FILTER_NAMES),
          "1 every filter echoes false on an unfiltered call",
          str({n: echo.get(n) for n in FILTER_NAMES if echo.get(n) is not False}))
    check(echo.get("nameFilter") is None,
          "1 nameFilter echoes null when it was not sent", repr(echo.get("nameFilter")))

    for _, field in FILTERS:
        bad = [p.get("name") for p in rows if not isinstance(p.get(field), bool)]
        check(not bad, "1 every row carries a boolean %r" % field, str(bad[:5]))

    tally = dict((f, sum(1 for p in rows if p.get(f))) for _, f in FILTERS)
    print("      of those: " + ", ".join("%s=%d" % (k, v) for k, v in tally.items()))
    both = [p.get("name") for p in rows if p.get("tame") and p.get("wild")]
    check(not both, "1 no pawn is both tame and wild", str(both[:5]))

    # ------------------------------------------------------------------ 2 ---
    for arg, field in FILTERS:
        r = call({arg: True}, "2 %s alone" % arg)
        if not isinstance(r, dict) or not r.get("success"):
            check(False, "2 %s succeeded" % arg, str(r)[:200])
            continue
        got = r.get("pawns") or []
        want = [p for p in rows if p.get(field)]
        print("      %d returned, %d expected from the unfiltered rows"
              % (len(got), len(want)))
        wrong = [p.get("name") for p in got if not p.get(field)]
        check(not wrong, "2 %s: every returned row has %s:true" % (arg, field),
              str(wrong[:5]))
        check(set(map(key, got)) == set(map(key, want)),
              "2 %s: the returned set is exactly the unfiltered rows with %s"
              % (arg, field),
              "only returned %s; only expected %s"
              % (sorted(set(map(key, got)) - set(map(key, want)))[:3],
                 sorted(set(map(key, want)) - set(map(key, got)))[:3]))
        counts_close(r, "2 " + arg)
        e = r.get("filters") or {}
        check(e.get(arg) is True and all(e.get(n) is False
                                        for n in FILTER_NAMES if n != arg),
              "2 %s: filters{} echoes it true and the rest false" % arg,
              str({n: e.get(n) for n in FILTER_NAMES}))
        check(r.get("spawnedPawnTotal") == base.get("spawnedPawnTotal"),
              "2 %s: spawnedPawnTotal is NOT narrowed by the filter" % arg,
              "%s vs %s" % (r.get("spawnedPawnTotal"), base.get("spawnedPawnTotal")))

    # ------------------------------------------------------------------ 3 ---
    r = call({"animalsOnly": True, "downedOnly": True}, "3 two filters together")
    if isinstance(r, dict) and r.get("success"):
        got = r.get("pawns") or []
        want = [p for p in rows if p.get("animal") and p.get("downed")]
        print("      %d returned, %d expected (the INTERSECTION)"
              % (len(got), len(want)))
        check(set(map(key, got)) == set(map(key, want)),
              "3 animalsOnly + downedOnly is the intersection, not the union",
              "returned %s, expected %s" % (len(got), len(want)))
    else:
        check(False, "3 animalsOnly + downedOnly succeeded", str(r)[:200])

    # ------------------------------------------------------------------ 4 ---
    for pair in CONFLICTS:
        label = "4 " + " + ".join("%s=%s" % kv for kv in sorted(pair.items()))
        r = call(pair, label)
        ok = isinstance(r, dict) and r.get("success") is False
        check(ok, label + " is REFUSED, not answered empty",
              "success=%s, %d pawn(s)"
              % (r.get("success") if isinstance(r, dict) else r,
                 len(r.get("pawns") or []) if isinstance(r, dict) else -1))
        if ok:
            err = str(r.get("error") or "")
            print("      " + err[:200])
            check(all(k in err or k in str(r.get("filtersSeen") or "")
                      for k in pair),
                  label + ": the refusal names both filters",
                  "%r / %r" % (err[:120], r.get("filtersSeen")))

    # ------------------------------------------------------------------ 5 ---
    sub = None
    for p in rows:
        n = p.get("name") or ""
        if len(n) >= 3:
            sub = n[1:4]
            break
    if sub is None:
        print("\n  ** no pawn with a 3-character name -- nameFilter case skipped **")
    else:
        r = call({"nameFilter": sub}, "5 nameFilter %r" % sub)
        if isinstance(r, dict) and r.get("success"):
            got = r.get("pawns") or []
            low = sub.lower()
            want = [p for p in rows
                    if low in (p.get("name") or "").lower()
                    or low in (p.get("defName") or "").lower()
                    or low in (p.get("kindDef") or "").lower()]
            print("      %d returned, %d expected; names: %s"
                  % (len(got), len(want), [p.get("name") for p in got][:8]))
            check(got, "5 nameFilter matched at least the pawn it was taken from")
            check(len(got) <= len(rows), "5 nameFilter narrows",
                  "%d of %d" % (len(got), len(rows)))
            check(set(map(key, got)) == set(map(key, want)),
                  "5 nameFilter matches name / defName / kindDef, case-insensitively",
                  "only returned %s; only expected %s"
                  % (sorted(set(map(key, got)) - set(map(key, want)))[:3],
                     sorted(set(map(key, want)) - set(map(key, got)))[:3]))
            check((r.get("filters") or {}).get("nameFilter") == sub,
                  "5 filters{} echoes nameFilter as it was sent",
                  repr((r.get("filters") or {}).get("nameFilter")))
            counts_close(r, "5 nameFilter")

        upper = call({"nameFilter": sub.upper()}, "5b nameFilter uppercased")
        if isinstance(upper, dict) and upper.get("success") and isinstance(r, dict):
            check(set(map(key, upper.get("pawns") or []))
                  == set(map(key, r.get("pawns") or [])),
                  "5b nameFilter is case-insensitive",
                  "%d vs %d" % (len(upper.get("pawns") or []),
                                len(r.get("pawns") or [])))

    blank = call({"nameFilter": "   "}, "5c a whitespace-only nameFilter")
    check(isinstance(blank, dict) and blank.get("success") is False,
          "5c a whitespace-only nameFilter is refused",
          str(blank)[:200])

    # ------------------------------------------------------------------ 6 ---
    bogus = call({"bogusKeyXYZ": True, "WildOnly": True}, "6 a bogus key")
    if isinstance(bogus, dict):
        ua = bogus.get("unknownArguments")
        print("      unknownArguments: %s" % ua)
        check(isinstance(ua, list) and "bogusKeyXYZ" in ua and "WildOnly" in ua,
              "6 both unknown keys are named, the mis-cased one included", str(ua))
        check((bogus.get("filters") or {}).get("wildOnly") is False,
              "6 the mis-cased WildOnly did NOT narrow anything",
              repr((bogus.get("filters") or {}).get("wildOnly")))

    report()


def report():
    print("\n" + "=" * 72)
    print("%d of %d checks passed." % (CHECKS[0] - len(FAILURES), CHECKS[0]))
    for f in FAILURES:
        print("  FAIL " + f)
    print("""
Then, in rimworld\\instruments:

    python pawns.py --wild
    python pawns.py --tame --animals
    python pawns.py --prisoners
    python pawns.py --wild --tame        # must print the tool's refusal, not ""
    python pawns.py --name muffalo
""")


if __name__ == "__main__":
    main()
    sys.exit(1 if FAILURES else 0)
