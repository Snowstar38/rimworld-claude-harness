"""Live load test for corpses in `home/list_things` (WANTED 6).

READ-ONLY. Every call it makes is a read tool. It never saves, never unpauses,
never selects anything, and never touches a body. Run it with the game loaded
and PAUSED; it leaves the game exactly as it found it.

    cd rimworld\\instruments
    python ..\\companion\\tests\\live_list_things_corpses.py

Nothing below has ever been run against a live game. Every assertion here is a
PROMISE being checked for the first time. A failure is information.

**If the save has no corpses, this test says so and marks the affected checks
VACUOUS rather than passing them.** A green run over an empty set is exactly the
"nothing there" / "nothing survived the filter" confusion the whole toolkit is
built against, and it must not be available here either.

What it checks, and what a failure of each one would mean:

  1  corpse (bool) is on EVERY row, corpse rows included
     -- a failure means a caller has to infer the kind of row from the presence
        of another key, which is the absent-field bug.
  2  every corpses[] entry's rotStage is fresh / rotting / dessicated, OR null
     with its def named in corpsesSkipped[]
     -- a failure means a stage was invented, or a null was left unexplained.
  3  skeleton == (rotStage == "dessicated") on every body
     -- a failure means "skeleton" has drifted from RimWorld's own Dessicated
        stage into somebody's guess.
  4  corpsesListed + corpsesNotListed == corpseCount on every corpse row, and
     len(corpses[]) == corpsesListed
     -- a failure means the per-body cap is dropping bodies in silence.
  5  corpseTotal == the sum of the rows' corpseCount, and corpseSkeletonTotal
     == the bodies whose skeleton is true
     -- a failure means two accumulators for one fact.
  6  corpses:true returns ONLY corpse rows
     -- a failure means the filter leaks, so a "corpses only" answer is not one.
  7  corpses:true and the default call agree, def by def, about how many bodies
     there are
     -- a failure means the filter is not merely a narrowing: it changed the
        census. That would make the corpse count depend on how you asked.
  8  a bogus argument key lands in unknownArguments[]
     -- a failure means the argument declaration is out of step with the binder.
"""
import sys, os, json

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "instruments"))
import rim

TOOL = "home/list_things"
STAGES = ("fresh", "rotting", "dessicated")
FAILURES = []
VACUOUS = []
CHECKS = [0]


def check(ok, label, detail=""):
    CHECKS[0] += 1
    if ok:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s   %s" % (label, detail))
        FAILURES.append(label + ("   " + detail if detail else ""))


def vacuous(label, why):
    """Not a pass. A check that had nothing to run against says so by name."""
    VACUOUS.append("%s -- %s" % (label, why))
    print("  ....  VACUOUS %s   (%s)" % (label, why))


def call(args, label):
    print("\n--- %s: %s %s" % (label, TOOL, json.dumps(args)))
    try:
        r = rim.game(TOOL, args)
    except Exception as e:
        check(False, label + " returned at all", "%s: %s" % (type(e).__name__, e))
        return None
    if not isinstance(r, dict) or "things" not in r:
        check(False, label + " returned a things[]", str(r)[:200])
        return None
    print("      %d def rows, corpseTotal %s (%s skeletons); payload ~%d KB"
          % (len(r.get("things") or []), r.get("corpseTotal"),
             r.get("corpseSkeletonTotal"), len(json.dumps(r)) // 1024))
    return r


def corpse_rows(r):
    return [row for row in (r.get("things") or []) if row.get("corpse")]


def bodies(r):
    for row in corpse_rows(r):
        for c in row.get("corpses") or []:
            yield row, c


def main():
    rim.init()
    print("=" * 72)
    print("home/list_things corpses -- live load test. READ ONLY.")
    print("Nothing is saved, nothing is selected, the game is not unpaused.")
    print("=" * 72)

    # Whole map, everyone's, so an enemy body in the killbox is in scope too.
    base = call({"ownership": "all"}, "1 default census (ownership:all)")
    if base is None:
        return report()

    rows = base.get("things") or []
    check(all("corpse" in row for row in rows),
          "1 every row carries a corpse flag, false included",
          "%d of %d rows without it" % (sum(1 for r_ in rows if "corpse" not in r_), len(rows)))
    check(isinstance(base.get("corpsesSkipped"), list),
          "1 corpsesSkipped[] is present as a list",
          repr(base.get("corpsesSkipped"))[:120])
    check((base.get("filters") or {}).get("corpses") is False,
          "1 filters.corpses is present and False on the default call")

    crows = corpse_rows(base)
    all_bodies = list(bodies(base))
    print("\n      %d corpse def row(s), %d body/bodies detailed"
          % (len(crows), len(all_bodies)))
    for row in crows:
        print("      %-24s x%-3s  %s  listed %s of %s"
              % (row.get("defName"), row.get("corpseCount"),
                 json.dumps(row.get("rotStages")),
                 row.get("corpsesListed"), row.get("corpseCount")))
    for miss in base.get("corpsesSkipped") or []:
        print("      ** corpsesSkipped: %s" % json.dumps(miss))

    no_corpses = not crows
    if no_corpses:
        print("\n      NO CORPSES ON MAP -- checks (2, 3, 4, 5-detail, 6, 7) vacuous")
        check(base.get("corpseTotal") == 0,
              "corpseTotal is 0 and stated, not absent",
              str(base.get("corpseTotal")))

    skipped_defs = {e.get("defName") for e in (base.get("corpsesSkipped") or [])
                    if isinstance(e, dict)}

    # ------------------------------------------------------------------ 2 --
    if no_corpses:
        vacuous("2 rotStage is one of fresh/rotting/dessicated, or null + skipped",
                "NO CORPSES ON MAP")
        vacuous("3 skeleton == (rotStage == 'dessicated')", "NO CORPSES ON MAP")
    else:
        bad = [(row.get("defName"), c.get("rotStage"))
               for row, c in all_bodies
               if c.get("rotStage") not in STAGES
               and not (c.get("rotStage") is None
                        and row.get("defName") in skipped_defs)]
        check(not bad,
              "2 every body's rotStage is fresh/rotting/dessicated, or null with "
              "its def in corpsesSkipped[]",
              "%d bad, e.g. %s" % (len(bad), bad[:4]))

        # -------------------------------------------------------------- 3 --
        mismatch = [(row.get("defName"), c.get("name"), c.get("rotStage"),
                     c.get("skeleton"))
                    for row, c in all_bodies
                    if bool(c.get("skeleton")) != (c.get("rotStage") == "dessicated")]
        check(not mismatch,
              "3 skeleton == (rotStage == 'dessicated') on every body",
              "%d disagree, e.g. %s" % (len(mismatch), mismatch[:4]))

    # ------------------------------------------------------------------ 4 --
    if no_corpses:
        vacuous("4 corpsesListed + corpsesNotListed == corpseCount", "NO CORPSES ON MAP")
    else:
        broken = [(row.get("defName"), row.get("corpsesListed"),
                   row.get("corpsesNotListed"), row.get("corpseCount"),
                   len(row.get("corpses") or []))
                  for row in crows
                  if row.get("corpsesListed") is None
                  or row.get("corpsesNotListed") is None
                  or row["corpsesListed"] + row["corpsesNotListed"] != row.get("corpseCount")
                  or len(row.get("corpses") or []) != row["corpsesListed"]]
        check(not broken,
              "4 every corpse row: listed + notListed == corpseCount == the "
              "length of corpses[] plus what the cap cut",
              str(broken[:3]))

    # ------------------------------------------------------------------ 5 --
    summed = sum(row.get("corpseCount") or 0 for row in crows)
    check(base.get("corpseTotal") == summed,
          "5 corpseTotal == the sum of the rows' corpseCount",
          "%s != %s" % (base.get("corpseTotal"), summed))
    if no_corpses:
        check(base.get("corpseSkeletonTotal") == 0,
              "5 corpseSkeletonTotal is 0 and stated")
    else:
        stage_sk = sum((row.get("rotStages") or {}).get("dessicated") or 0
                       for row in crows)
        check(base.get("corpseSkeletonTotal") == stage_sk,
              "5 corpseSkeletonTotal == the rows' dessicated tallies",
              "%s != %s" % (base.get("corpseSkeletonTotal"), stage_sk))
        listed_sk = sum(1 for _, c in all_bodies if c.get("skeleton"))
        if any(row.get("corpsesTruncated") for row in crows):
            print("      (a per-row cap bit, so the DETAILED skeletons (%d) may be "
                  "fewer than the tallied ones (%d) -- both are correct)"
                  % (listed_sk, stage_sk))
        else:
            check(listed_sk == stage_sk,
                  "5 the detailed bodies' skeleton flags match the row tallies",
                  "%d != %d" % (listed_sk, stage_sk))

    # ------------------------------------------------------------------ 6 --
    only = call({"ownership": "all", "corpses": True}, "6 corpses:true")
    if only is not None:
        check((only.get("filters") or {}).get("corpses") is True,
              "6 filters.corpses is present and True")
        strays = [row.get("defName") for row in (only.get("things") or [])
                  if not row.get("corpse")]
        check(not strays,
              "6 corpses:true returns ONLY corpse rows",
              "%d non-corpse row(s): %s" % (len(strays), strays[:5]))
        print("      skipped.byCorpsesOnly = %s"
              % ((only.get("skipped") or {}).get("byCorpsesOnly")))
        check(isinstance((only.get("skipped") or {}).get("byCorpsesOnly"), int),
              "6 skipped.byCorpsesOnly states how many non-corpses were removed")

        # -------------------------------------------------------------- 7 --
        if no_corpses:
            vacuous("7 the two calls agree def by def about the body count",
                    "NO CORPSES ON MAP")
            check(only.get("corpseTotal") == 0,
                  "7 corpses:true also reports corpseTotal 0",
                  str(only.get("corpseTotal")))
        else:
            a = {row.get("defName"): row.get("corpseCount") for row in crows}
            b = {row.get("defName"): row.get("corpseCount")
                 for row in corpse_rows(only)}
            check(a == b,
                  "7 corpses:true and the default call agree, def by def, on "
                  "how many bodies there are",
                  "default %s vs corpses-only %s" % (a, b))
            check(only.get("corpseTotal") == base.get("corpseTotal"),
                  "7 corpseTotal is the same in both calls",
                  "%s != %s" % (only.get("corpseTotal"), base.get("corpseTotal")))

    # ------------------------------------------------------------------ 8 --
    bogus = call({"corpses": True, "bogusKeyXYZ": True, "Corpses": 1},
                 "8 a bogus key")
    if bogus is not None:
        ua = bogus.get("unknownArguments")
        print("      unknownArguments: %s" % ua)
        check(ua == ["Corpses", "bogusKeyXYZ"],
              "8 bogus: unknownArguments names both keys, sorted, "
              "case-sensitively (Corpses != corpses)", str(ua))
        check(bool(bogus.get("unknownArgumentsWarning")),
              "8 bogus: a warning sentence is present")

    return report()


def report():
    print("\n" + "=" * 72)
    print("%d of %d checks passed." % (CHECKS[0] - len(FAILURES), CHECKS[0]))
    for f in FAILURES:
        print("  FAIL " + f)
    if VACUOUS:
        print("\n%d check(s) did NOT run -- they are not passes:" % len(VACUOUS))
        for v in VACUOUS:
            print("  VACUOUS " + v)
        print("  Re-run this after something on the map has died, or on a save "
              "that has bodies, before believing the corpse fields work.")
    print("""
Then run these by hand, in rimworld\\instruments, and read them:

    python inv.py --corpses              # every body: race, name, stage, where
    python inv.py                        # corpse rows carry a [CORPSE ...] tag
    python inv.py --corpses --all-owners # raiders' dead as well as ours

What to look for:
  * a body you know is bones reads SKELETON and not "fresh";
  * a mechanoid corpse reads "no stage" with reason noRotComp, never "fresh";
  * a corpse in a grave names the grave in its holder column;
  * the footer's corpse count matches the number of lines --corpses printed.
""")
    sys.exit(1 if FAILURES else 0)


if __name__ == "__main__":
    main()
