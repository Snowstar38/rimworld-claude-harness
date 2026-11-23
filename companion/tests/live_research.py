"""Live load test for `home/research` and `research.py`.

    cd rimworld\\instruments
    python ..\\companion\\tests\\live_research.py

Run it against a loaded, PAUSED colony. It never saves, never unpauses, never
opens a tab and never touches the selection.

## The one real write, and why it is safe

Vanilla RimWorld has **no "unset the current project"** for the player — the
Research tab offers Start and Stop, and `ResearchManager.StopProject` is not
exposed by this tool. So the only write that can be put straight back is
**setting the current project to the project that is ALREADY current**: the
value written equals the value that was there, `applied` comes back true,
`changed` comes back false, and `before` equals `after`.

If **no project is currently selected**, that write has no way back, so it is
NOT performed: the script prints `NEEDS HUMAN: no current project, skipping
write test` and carries on. Everything else here is a read or a dry run.

Nothing below has ever been run against a live game. Every assertion is a
PROMISE being checked for the first time; a failure is information.

## What it checks, and what a failure would mean

  1  default call answers at all
        FAIL = the tool is not registered, or the map gate refused. Check
        `rimbridge/get_bridge_status` and that RimWorld was restarted.
  2  researchBenches{} is present, with benches[] and researchers[]
        FAIL = the block that answers "why is nobody researching" is missing or
        was not read; a null here must carry a reason in skipped[].
  I1 availableCount + lockedCount + finishedCount + hiddenCount == totalCount,
     allowing for unreadableProjects[]
        FAIL = the four buckets are not disjoint or not exhaustive, so a
        filtered list can no longer be reconciled against the database. This is
        counted over the WHOLE database, not over what was listed; hiddenCount
        is separated out because the brief asks for the three-way sum and the
        hidden bucket is what makes the difference.
  I2 every row in available[] has prerequisitesCompleted true
        FAIL = something is being offered as startable that the game would not
        start; CanStartNow and PrerequisitesCompleted have come apart.
  I3 every row in locked[] names at least one thing it is missing
        FAIL = a locked project with an empty lockReasons[] and no missing
        prerequisite/building/facility/techprint is an unexplained refusal,
        which is the exact failure this toolkit exists to prevent.
  I4 filter narrows the lists and leaves the counts alone
        FAIL = either the filter is not applied, or it leaked into the counts,
        and a narrowed answer would look like an empty database.
  I5 unlocks:true adds unlocks[] to a listed project
        FAIL = the block was asked for and not emitted.
  6  a bogus argument key lands in unknownArguments[]
        FAIL = a misspelled `dryRun` could be silently dropped, which on this
        tool is the difference between a plan and a changed colony.
  7  `set` with an ambiguous substring is refused, with candidates listed
        FAIL = an ambiguous name is being resolved by guessing.
  8  `set` of a FINISHED project is refused
        FAIL = the tool would try to research something already researched.
  9  `set` dry run leaves `current` unchanged on a fresh read
        FAIL = the dry run is not dry. Stop and do not run anything with --do.
 10  THE ONE REAL WRITE: set the current project to itself
        FAIL = SetCurrentProject did not land, or `after` is being echoed from
        the request rather than read back from the game.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "instruments"))

import rim                                                    # noqa: E402

TOOL = "home/research"
FAILURES = []
NEEDS_HUMAN = []
CHECKS = [0]


def check(ok, label, detail=""):
    CHECKS[0] += 1
    if ok:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s   %s" % (label, detail))
        FAILURES.append(label + ("   " + detail if detail else ""))
    return ok


def human(msg):
    print("  NEEDS HUMAN: %s" % msg)
    NEEDS_HUMAN.append(msg)


def call(args, label):
    print("\n--- %s: %s %s" % (label, TOOL, json.dumps(args)))
    try:
        r = rim.game(TOOL, args)
    except Exception as e:
        check(False, label + " returned at all", "%s: %s" % (type(e).__name__, e))
        return None
    if not isinstance(r, dict) or "totalCount" not in r:
        check(False, label + " returned a research payload", str(r)[:200])
        return None
    print("      %s available / %s locked / %s finished / %s hidden of %s total;"
          " current %s; payload ~%d KB"
          % (r.get("availableCount"), r.get("lockedCount"), r.get("finishedCount"),
             r.get("hiddenCount"), r.get("totalCount"),
             (r.get("current") or {}).get("defName") if r.get("current") else "NONE",
             len(json.dumps(r)) // 1024))
    return r


def buckets(r, label):
    """I1. The four buckets are disjoint and exhaustive over the database."""
    parts = [r.get("availableCount"), r.get("lockedCount"),
             r.get("finishedCount"), r.get("hiddenCount")]
    total = r.get("totalCount")
    unread = len(r.get("unreadableProjects") or [])
    ok = None not in parts and total is not None and sum(parts) + unread == total
    check(ok, "I1 %s: available + locked + finished + hidden + unreadable "
              "== totalCount" % label,
          "%s (+%d unreadable) != %s" % (parts, unread, total))
    # Stated separately, because the brief asks for the three-way sum: the
    # difference between it and the total is exactly the hidden bucket.
    if None not in parts and total is not None:
        three = parts[0] + parts[1] + parts[2]
        print("      the three-way sum is %d; totalCount - hidden - unreadable "
              "is %d" % (three, total - parts[3] - unread))
        check(three == total - parts[3] - unread,
              "I1b %s: the three listed buckets account for everything that is "
              "not hidden" % label,
              "%d != %d" % (three, total - parts[3] - unread))


def main():
    rim.init()
    print("=" * 72)
    print("home/research -- live load test. ONE real write, put straight back.")
    print("=" * 72)

    # ---------------------------------------------------------------- 1 ----
    clean = call({}, "1 clean")
    if clean is None:
        return report()
    buckets(clean, "clean")
    check("current" in clean and "currentNote" in clean,
          "1 clean: current and currentNote are both present")
    check(isinstance(clean.get("available"), list),
          "1 clean: available[] is a list")
    check("locked" not in clean and "finished" not in clean,
          "1 clean: the opt-in blocks are ABSENT when not asked for",
          "locked/finished appeared without being requested")
    check(clean.get("write") is None and clean.get("applied") is False,
          "1 clean: a read-only call reports write null and applied false")

    current = clean.get("current")
    if current is None:
        print("      current is NONE -- this is the game's `Need research "
              "project` alert, and it is a legitimate colony state.")
    else:
        print("      current: %s  %.0f / %.0f  (%.0f%%)"
              % (current.get("label"), current.get("progress") or 0,
                 current.get("cost") or 0,
                 100 * (current.get("progressPercent") or 0)))

    # ---------------------------------------------------------------- 2 ----
    bench = clean.get("researchBenches")
    if not check(isinstance(bench, dict),
                 "2: researchBenches{} is an object", repr(bench)[:120]):
        bench = {}
    if bench.get("count") is None:
        check(bool(bench.get("skipped")),
              "2: a null bench count carries a reason in skipped[]")
    else:
        print("      %s bench(es), %s powered, facilities %s"
              % (bench.get("count"), bench.get("poweredCount"),
                 bench.get("facilities")))
        check(isinstance(bench.get("benches"), list),
              "2: benches[] is a list when count is readable")
    people = bench.get("researchers")
    if people is None:
        check(bool(bench.get("skipped")),
              "2: a null researchers[] carries a reason in skipped[]")
    else:
        active = [p for p in people if p.get("active")]
        print("      %d colonist(s) checked, %d with Research on: %s"
              % (len(people), len(active),
                 ", ".join("%s(pri %s, Int %s)"
                           % (p.get("name"), p.get("priority"),
                              p.get("intellectual")) for p in active) or "-"))
        check(all("everWork" in p for p in people),
              "2: every researcher row states everWork",
              "a row without it means GetPriority may have been reached unguarded")

    # ---------------------------------------------------------------- I2 ---
    bad = [p.get("defName") for p in clean.get("available") or []
           if p.get("prerequisitesCompleted") is not True]
    check(not bad,
          "I2: every available project has prerequisitesCompleted true",
          "these do not: %s" % bad[:5])
    bad = [p.get("defName") for p in clean.get("available") or []
           if p.get("canStartNow") is not True or p.get("finished") is not False]
    check(not bad,
          "I2b: every available project is canStartNow and not finished",
          "these are not: %s" % bad[:5])

    # ---------------------------------------------------------------- I3 ---
    lock = call({"locked": True}, "3 locked:true")
    if lock is not None:
        buckets(lock, "locked")
        rows = lock.get("locked")
        check(isinstance(rows, list), "I3: locked[] is a list when asked for")
        check(lock.get("lockedListed") == len(rows or []),
              "I3: lockedListed == len(locked[])")
        unexplained = []
        for p in rows or []:
            named = (p.get("missingPrerequisites")
                     or p.get("missingBuilding")
                     or p.get("missingFacilities")
                     or p.get("missingAnalyzed")
                     or p.get("missingMechanitor")
                     or p.get("missingInspection")
                     or (p.get("techprintsNeeded") or 0) > (p.get("techprintsApplied") or 0))
            if not named:
                unexplained.append(p.get("defName"))
        check(not unexplained,
              "I3: every locked project names at least one thing it is missing",
              "unexplained: %s" % unexplained[:5])
        for p in (rows or [])[:5]:
            print("      %-28s %s" % (p.get("defName"),
                                      "; ".join(p.get("lockReasons") or [])))

    # ---------------------------------------------------------------- I4 ---
    # Pick a needle from a real label so the narrowing is guaranteed non-empty.
    needle = None
    for p in (clean.get("available") or []) + ((lock or {}).get("locked") or []):
        label = (p.get("label") or "").strip()
        if len(label) >= 4:
            needle = label[:4]
            break
    if needle is None:
        human("no project label was long enough to build a filter needle from")
    else:
        filt = call({"locked": True, "finished": True, "filter": needle},
                    "4 filter %r" % needle)
        if filt is not None:
            buckets(filt, "filtered")
            check(filt.get("filter") == needle and filt.get("filterApplied") is True,
                  "I4: the reply states the filter it applied")
            check((filt.get("availableCount"), filt.get("lockedCount"),
                   filt.get("finishedCount"), filt.get("totalCount"))
                  == (clean.get("availableCount"), clean.get("lockedCount"),
                      clean.get("finishedCount"), clean.get("totalCount")),
                  "I4: the counts are IDENTICAL to the unfiltered call",
                  "a filter that moves the counts makes them unreconcilable")
            listed = len(filt.get("available") or [])
            check(listed == filt.get("availableListed"),
                  "I4: availableListed == len(available[])")
            check(listed <= (clean.get("availableListed") or 0),
                  "I4: the filter did not GROW the available list")
            hits = [p.get("defName") for p in filt.get("available") or []
                    if needle.lower() not in (p.get("label") or "").lower()
                    and needle.lower() not in (p.get("defName") or "").lower()]
            check(not hits,
                  "I4: every listed project actually contains the needle",
                  "these do not: %s" % hits[:5])

    # ---------------------------------------------------------------- I5 ---
    unl = call({"unlocks": True}, "5 unlocks:true")
    if unl is not None:
        rows = unl.get("available") or []
        if not rows:
            human("nothing is startable, so unlocks[] had nothing to attach to")
        else:
            check(all("unlocks" in p for p in rows),
                  "I5: every listed project carries an unlocks key")
            withany = [p for p in rows if p.get("unlocks")]
            print("      %d of %d available project(s) unlock something; e.g. %s"
                  % (len(withany), len(rows),
                     [u.get("defName") for u in (withany[0]["unlocks"][:4]
                                                 if withany else [])]))

    # ---------------------------------------------------------------- 6 ----
    bogus = call({"lockd": True, "Filter": "x"}, "6 bogus keys")
    if bogus is not None:
        unknown = bogus.get("unknownArguments")
        check(isinstance(unknown, list) and "lockd" in unknown and "Filter" in unknown,
              "6: both misspelled keys are reported in unknownArguments[]",
              repr(unknown))
        check(bool(bogus.get("unknownArgumentsWarning")),
              "6: the warning string is present when a key was unrecognised")

    # ---------------------------------------------------------------- 7 ----
    # An ambiguous substring. Built from the shortest common prefix that the
    # filter itself proves matches more than one project.
    amb = None
    for candidate in ("re", "e", "a", "in", "o"):
        try:
            probe = rim.game(TOOL, {"locked": True, "finished": True,
                                    "filter": candidate})
        except Exception as e:
            print("      probe %r failed: %s: %s" % (candidate, type(e).__name__, e))
            continue
        if not isinstance(probe, dict):
            continue
        hits = ((probe.get("availableListed") or 0)
                + (probe.get("lockedListed") or 0)
                + (probe.get("finishedListed") or 0))
        if hits >= 2:
            amb = candidate
            break
    if amb is None:
        human("no substring matched two or more projects; ambiguity untested")
    else:
        r = call({"set": amb}, "7 set %r (ambiguous, dry run)" % amb)
        if r is not None:
            w = r.get("write") or {}
            check(w.get("refused") is True,
                  "7: an ambiguous name is REFUSED", json.dumps(w)[:200])
            check(len(w.get("candidates") or []) >= 2,
                  "7: the refusal lists the candidates",
                  "candidateCount %s" % w.get("candidateCount"))
            check(r.get("applied") is False,
                  "7: a refused set never reports applied")
            print("      reason: %s" % w.get("reason"))

    # ---------------------------------------------------------------- 8 ----
    fin = call({"finished": True}, "8 finished:true")
    done = (fin or {}).get("finished") or []
    if not done:
        human("no project is finished yet, so the finished-refusal is untested")
    else:
        r = call({"set": done[0]}, "8 set %r (finished, dry run)" % done[0])
        if r is not None:
            w = r.get("write") or {}
            check(w.get("refused") is True,
                  "8: setting a FINISHED project is refused", json.dumps(w)[:200])
            check("FINISHED" in (w.get("reason") or "").upper(),
                  "8: the refusal says the project is finished",
                  w.get("reason"))

    # ---------------------------------------------------------------- 9 ----
    startable = [p for p in clean.get("available") or []
                 if not p.get("isCurrent")]
    if not startable:
        human("nothing startable that is not already current; dry run untested")
    else:
        target = startable[0]["defName"]
        r = call({"set": target}, "9 set %r (dry run, must not write)" % target)
        if r is not None:
            w = r.get("write") or {}
            check(r.get("dryRun") is True and r.get("applied") is False,
                  "9: dryRun defaults to true and nothing was applied")
            check(w.get("refused") is False,
                  "9: a startable project is not refused", w.get("reason"))
            check(w.get("afterIsPredicted") is True,
                  "9: after{} is marked predicted on a dry run")
        after = call({}, "9b re-read after the dry run")
        if after is not None:
            was = (current or {}).get("defName") if current else None
            now = (after.get("current") or {}).get("defName") if after.get("current") else None
            check(was == now,
                  "9b: the DRY RUN CHANGED NOTHING -- current is still %r" % was,
                  "current moved from %r to %r; the dry run is not dry" % (was, now))

    # --------------------------------------------------------------- 10 ----
    print("\n--- 10: THE ONE REAL WRITE")
    if current is None:
        human("no current project, skipping write test -- vanilla has no way "
              "to unset one, so a real write here could not be put back")
    else:
        name = current.get("defName")
        r = call({"set": name, "dryRun": False},
                 "10 set %r to itself (REAL WRITE, a no-op)" % name)
        if r is not None:
            w = r.get("write") or {}
            check(r.get("applied") is True,
                  "10: applied is true -- SetCurrentProject was called",
                  json.dumps(w)[:200])
            check(w.get("refused") is False,
                  "10: the write was not refused", w.get("reason"))
            before = (w.get("before") or {}).get("defName")
            aft = (w.get("after") or {}).get("defName")
            check(before == name and aft == name,
                  "10: before == after == %r" % name,
                  "before %r, after %r" % (before, aft))
            check(w.get("changed") is False,
                  "10: changed is FALSE -- the same project was written back")
            check(w.get("afterIsPredicted") is False,
                  "10: after{} is marked as read back, not predicted")
            check(w.get("alreadyCurrent") is True,
                  "10: the tool knew it was already current")
        final = call({}, "10b re-read after the real write")
        if final is not None:
            now = (final.get("current") or {}).get("defName") if final.get("current") else None
            check(now == name,
                  "10b: the colony is researching exactly what it was before "
                  "(%r)" % name,
                  "it is now %r -- PUT IT BACK BY HAND" % now)

    return report()


def report():
    print("\n" + "=" * 72)
    print("%d of %d checks passed." % (CHECKS[0] - len(FAILURES), CHECKS[0]))
    for f in FAILURES:
        print("  FAIL " + f)
    for h in NEEDS_HUMAN:
        print("  NEEDS HUMAN: " + h)
    print("""
Then run these by hand, in rimworld\\instruments, and read them:

    python research.py                      current, benches, people, available
    python research.py --locked             what is blocked and why
    python research.py --finished           what is done
    python research.py --unlocks            what each project unlocks
    python research.py --filter cool        narrowed, with the filter stated
    python research.py --json               the raw reply

    python research.py set "<a startable project>"        DRY RUN -- read it
    python research.py set "<the CURRENT project>" --do   a safe no-op

What to look for:
  * CURRENT: NONE is printed loudly and matches whether `alerts.py` is showing
    `Need research project`;
  * BENCHES and RESEARCHERS together explain any colony that is researching
    nothing -- an unpowered bench and nobody assigned both get a `!!`;
  * the footer's five counts add up, and stay the same under `--filter`;
  * a dry-run `set` prints before -> after with `(predicted)` and changes
    nothing when you re-run the plain read.
""")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
