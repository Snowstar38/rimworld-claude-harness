"""What the colony is researching, what it could research, and why it isn't.

  python research.py                     # current project, benches, people, available
  python research.py --locked            # + everything blocked, and what blocks it
  python research.py --finished          # + the defNames already done
  python research.py --unlocks           # + what each listed project unlocks
  python research.py --filter cooling    # narrow every list, case-insensitive
  python research.py --json              # the raw reply
  python research.py --help              # this text

  python research.py set "Electricity"          # DRY RUN -- says what would change
  python research.py set Electricity --do       # and actually select it
  python research.py set Electricity --do --no-watch   # and without opening the tab

  import research
  research.state(locked=True)      research.choose("Electricity", do=False)

## What the default view says, top to bottom

  CURRENT      the project being researched, with points done / points needed, a
               bar, the percentage, its tech level and its tab. **CURRENT: NONE
               is the game's `Need research project` alert** -- nothing else has
               to be checked to confirm it, and nobody researches anything until
               it is set.
  BENCHES      how many research benches the colony owns, how many are POWERED,
               and which facilities (multi-analyzer and friends) are linked and
               active. A bench with no power comp needs none and counts as
               powered.
  RESEARCHERS  the colonists with the Research work type switched on, their
               priority, their Intellectual level and its passion. `!! nobody`
               is printed loudly, because it is the second most common reason
               research is not happening.
  AVAILABLE    every project that can be started right now, cheapest first:
               cost, label, tech level, tab. Cost is the raw cost; when the
               colony's tech level is below the project's, the number the game
               charges is higher and is printed beside it as `apparent`.
  the footer   available / locked / finished / hidden / total, ALWAYS over the
               whole database whatever filter was passed, and how to see the
               lists that were not printed.

## The four buckets

Every project in the game is in exactly one of them, and they sum to the total:

  finished   already researched
  hidden     an Anomaly project the entity codex has not revealed; the game
             itself will not show it, so neither does this
  available  `CanStartNow` -- prerequisites done, a bench that will host it,
             techprints applied
  locked     everything else, and `--locked` prints the reason per project:
             which prerequisites are unfinished, which building or facility is
             missing, how many techprints of how many are applied

## set -- the write side

`set <name>` is `home/research`, and **it is a dry run until `--do`**, the same
convention `pawns.py set` and `zones.py` use. The name is an exact defName, an
exact label (case-insensitive), or a unique substring of either; an ambiguous
substring is REFUSED with the candidates printed, never resolved by guessing. A
finished project, or one that cannot start yet, is refused with the requirement
that is missing.

On a real run the "after" is READ BACK out of the game, not echoed from the
request, and before -> after is printed either way. Setting the project that is
already current is a legal no-op: `applied` is true, `changed` is false.

## Nothing here opens the Research tab

The whole point. Reading research used to mean opening the tab on stream and
trimming a 204 KB layout; every number above comes from one `home/research`
call that selects nothing and opens nothing. A failed read is LOUD -- there is
no fallback, because an empty list would read as "there is nothing to research".
"""
import json
import sys

import rim

TOOL = "home/research"
BAR_WIDTH = 20

HELP = (
    "\n  There is no stock fallback for this -- the bridge has no research tool,"
    "\n  which is why the companion one exists. Check, in order:"
    "\n    1. Is RimWorld running and connected?   python setup.py"
    "\n    2. Is the companion DLL installed?"
    "\n       <game root>\\BridgeTools\\HomeBridge\\HomeBridge.BridgeTools.dll"
    "\n    3. Was RimWorld RESTARTED since it was installed? Companions are"
    "\n       discovered once, at bridge startup."
    "\n    4. rimbridge/get_bridge_status -> companions.diagnostics should show"
    "\n       status 'registered' and no warnings."
    "\n  Full procedure: rimworld\\companion\\INSTALL.md"
)


# --------------------------------------------------------------- the calls

def state(locked=False, finished=False, unlocks=False, filter=None):
    """One `home/research` read. Raises rather than returning a half answer."""
    args = {}
    if locked:
        args["locked"] = True
    if finished:
        args["finished"] = True
    if unlocks:
        args["unlocks"] = True
    if filter:
        args["filter"] = filter
    r = rim.game(TOOL, args)
    if not isinstance(r, dict) or "totalCount" not in r:
        raise rim.BridgeError(
            "%s returned no totalCount -- this is not a research payload: %.300r"
            % (TOOL, r))
    return r


def watch_line(r, on=None):
    """One line about the menu the write opened, if any. Claims the camera for
    Hands when the watch moved it, so the Lookout stays out of the shot."""
    w = (r or {}).get("watch") or {}
    if not w.get("shown"):
        print("watch: skipped (%s)" % (w.get("reason") or "not shown"))
        return
    if w.get("cameraMoved"):
        try:
            import camlock
            camlock.claim("hands", "watch")
        except Exception:
            pass
    tab = (w.get("inspectTab") or "").replace("ITab_Pawn_", "").replace("ITab_", "") or w.get("mainTab")
    who = (" on " + on) if on else ""
    if tab:
        print("watch: %s tab open%s, closes in %s s" % (tab, who, w.get("closesAfterSeconds")))
    else:
        print("watch: %s%s, clears in %s s"
              % ("selected" if w.get("selected") else "camera moved", who,
                 w.get("closesAfterSeconds")))

def choose(name, do=False, watch=True, **kw):
    """`set`, a dry run unless do=True. Same reply shape as state().

    watch=False writes with no UI; otherwise a real set opens the Research tab
    a moment before the project changes and closes it a few seconds after."""
    args = dict(kw)
    args["set"] = name
    args["dryRun"] = not do
    args["watch"] = watch
    r = rim.game(TOOL, args)
    if not isinstance(r, dict) or "write" not in r:
        raise rim.BridgeError(
            "%s returned no write{} for set=%r: %.300r" % (TOOL, name, r))
    return r


# ------------------------------------------------------------- formatting

def _num(v, fmt="%.0f"):
    return "?" if v is None else fmt % v


def _bar(pct):
    if pct is None:
        return "[" + "?" * BAR_WIDTH + "]"
    filled = int(round(max(0.0, min(1.0, pct)) * BAR_WIDTH))
    return "[" + "#" * filled + "." * (BAR_WIDTH - filled) + "]"


def _name(p):
    return (p.get("label") or p.get("defName") or "(unnamed)")


def _cost_text(p):
    """Raw cost, plus the apparent cost when the game will charge more."""
    cost, app = p.get("cost"), p.get("costApparent")
    if app is None:
        if p.get("costApparentReadable") is False and cost is not None:
            return "%s (apparent cost NOT READ -- no player faction)" % _num(cost)
        return _num(cost)
    if cost is not None and abs(app - cost) > 0.5:
        return "%s  (costs %s at our tech level)" % (_num(cost), _num(app))
    return _num(cost)


def project_line(p, indent=3):
    return "%s%8s  %-32s %-12s %s" % (
        " " * indent, _num(p.get("cost")), _name(p)[:32],
        p.get("techLevel") or "?", p.get("tabLabel") or p.get("tab") or "?")


def print_unlocks(p, indent=8):
    if "unlocks" not in p:
        return
    rows = p.get("unlocks")
    if rows is None:
        print("%s!! unlocks NOT READ for this project (%s)"
              % (" " * indent, p.get("defName")))
        return
    if not rows:
        print("%sunlocks: nothing the game lists" % (" " * indent))
        return
    names = ", ".join((u.get("label") or u.get("defName") or "?") for u in rows)
    more = p.get("unlocksNotListed") or 0
    print("%sunlocks: %s%s" % (" " * indent, names,
                               ("  (+%d more)" % more) if more else ""))


# ------------------------------------------------------------- the blocks

def current_block(r):
    cur = r.get("current")
    if cur is None:
        print("CURRENT: NONE -- nothing is being researched.")
        print("   %s" % (r.get("currentNote") or
                         "no currentNote in the reply, which is itself a bug"))
    else:
        print("CURRENT: %s   %s / %s  %s %s"
              % (_name(cur), _num(cur.get("progress")), _cost_text(cur),
                 _bar(cur.get("progressPercent")),
                 "%d%%" % round((cur.get("progressPercent") or 0) * 100)))
        print("   %-12s %s   techprints %s/%s"
              % (cur.get("techLevel") or "?",
                 cur.get("tabLabel") or cur.get("tab") or "?",
                 cur.get("techprintsApplied"), cur.get("techprintsNeeded")))

    by_cat = r.get("currentByCategory")
    if by_cat:
        for key in sorted(by_cat):
            proj = by_cat[key]
            print("   [%s] %s" % (key, "none selected" if proj is None else _name(proj)))
    elif r.get("anomalyActive"):
        print("   (Anomaly is active but no knowledge categories were readable)")
    print()


def bench_block(r):
    b = r.get("researchBenches")
    if b is None:
        print("BENCHES: !! researchBenches{} was NOT REPORTED by this build -- "
              "not zero benches, not looked at.")
        print()
        return

    count = b.get("count")
    if count is None:
        print("BENCHES: !! NOT READ -- %s" % "; ".join(b.get("skipped") or ["no reason given"]))
    elif count == 0:
        print("BENCHES: !! NONE. The colony owns no research bench, so most "
              "projects cannot be started at all.")
    else:
        powered = b.get("poweredCount")
        bang = "!! " if powered == 0 else ""
        print("BENCHES: %s%d bench(es), %s powered"
              % (bang, count, "?" if powered is None else powered))
        for bench in b.get("benches") or []:
            facs = [f.get("defName") for f in (bench.get("facilities") or [])
                    if f.get("active")]
            pos = bench.get("pos") or {}
            print("   %-24s at (%s,%s)  %s%s"
                  % (bench.get("defName") or "?", pos.get("x"), pos.get("z"),
                     "powered" if bench.get("powered") else "!! UNPOWERED",
                     ("  facilities: " + ", ".join(facs)) if facs else ""))

    people = b.get("researchers")
    if people is None:
        print("RESEARCHERS: !! NOT READ -- %s"
              % "; ".join(b.get("skipped") or ["no reason given"]))
    else:
        active = [p for p in people if p.get("active")]
        if not active:
            print("RESEARCHERS: !! NOBODY has the Research work type switched on "
                  "(%d colonist(s) checked)." % len(people))
        else:
            print("RESEARCHERS: %d of %d colonist(s) have Research on"
                  % (len(active), len(people)))
        for p in sorted(active, key=lambda p: -(p.get("intellectual") or -1)):
            print("   %-16s priority %s   Intellectual %s (%s)"
                  % (p.get("name") or "?", p.get("priority"),
                     "not read" if p.get("intellectual") is None else p["intellectual"],
                     p.get("passion") or "?"))
    print()


def available_block(r):
    rows = r.get("available")
    if rows is None:
        print("AVAILABLE: !! available[] was NOT REPORTED by this build.")
        print()
        return
    if not rows:
        if r.get("availableCount"):
            print("AVAILABLE: none of the %d startable project(s) matched the "
                  "filter %r." % (r["availableCount"], r.get("filter")))
        else:
            print("AVAILABLE: NOTHING can be started right now. That is a real "
                  "answer -- check --locked for what is in the way.")
        print()
        return
    print("AVAILABLE -- %d project(s) that can be started now, cheapest first"
          % len(rows))
    for p in rows:
        print(project_line(p))
        print_unlocks(p)
    print()


def locked_block(r):
    if "locked" not in r:
        return
    rows = r.get("locked")
    if rows is None:
        print("LOCKED: !! locked[] was asked for and NOT REPORTED by this build.")
        print()
        return
    if not rows:
        print("LOCKED: no locked project matched (%d locked in total)."
              % (r.get("lockedCount") or 0))
        print()
        return
    print("LOCKED -- %d project(s), and what each one is waiting for" % len(rows))
    for p in rows:
        print(project_line(p))
        for reason in p.get("lockReasons") or ["(no reason reported)"]:
            print("           %s" % reason)
        print_unlocks(p, indent=11)
    print()


def finished_block(r):
    if "finished" not in r:
        return
    rows = r.get("finished")
    if rows is None:
        print("FINISHED: !! finished[] was asked for and NOT REPORTED by this build.")
        print()
        return
    print("FINISHED -- %d project(s)%s"
          % (len(rows), " matching the filter" if r.get("filterApplied") else ""))
    line = ""
    for name in rows:
        if len(line) + len(name) > 70:
            print("   " + line)
            line = ""
        line += name + "  "
    if line:
        print("   " + line)
    print()


def footer(r):
    avail, lock = r.get("availableCount"), r.get("lockedCount")
    fin, hid = r.get("finishedCount"), r.get("hiddenCount")
    total = r.get("totalCount")
    print("-- %s available, %s locked, %s finished, %s hidden, %s total "
          "(counts are of the WHOLE database and ignore any filter)"
          % (avail, lock, fin, hid, total))
    parts = [avail, lock, fin, hid]
    if None not in parts and total is not None and sum(parts) != total:
        unread = len(r.get("unreadableProjects") or [])
        print("   !! the four buckets sum to %d, not %d -- %d project(s) were "
              "unreadable" % (sum(parts), total, unread))
    blocks = r.get("blocks") or {}
    if r.get("filterApplied"):
        shown = ["%s of %s available" % (r.get("availableListed"), avail)]
        if blocks.get("locked"):
            shown.append("%s of %s locked" % (r.get("lockedListed"), lock))
        if blocks.get("finished"):
            shown.append("%s of %s finished" % (r.get("finishedListed"), fin))
        print("   FILTER %r narrowed the lists above to %s. It did not touch "
              "the counts." % (r.get("filter"), ", ".join(shown)))
    hint = []
    if not blocks.get("locked"):
        hint.append("--locked (%s blocked project(s), each with its reason)" % lock)
    if not blocks.get("finished"):
        hint.append("--finished (%s done)" % fin)
    if not blocks.get("unlocks"):
        hint.append("--unlocks (what each project makes available)")
    if hint:
        print("   not shown: " + "; ".join(hint))
    if r.get("anyProjectIsAvailable") is False:
        print("   the game itself reports AnyProjectIsAvailable == false: there "
              "is nothing startable at all right now.")
    bad = r.get("unreadableProjects") or []
    if bad:
        print("   !! %d project(s) could not be classified: %s"
              % (len(bad), ", ".join(str(b.get("defName")) for b in bad[:5])))


def show(r):
    current_block(r)
    bench_block(r)
    available_block(r)
    locked_block(r)
    finished_block(r)
    footer(r)


# ---------------------------------------------------------------- the set

def print_set(r):
    w = r.get("write")
    if w is None:
        print("research.py set: the reply carried no write{} -- nothing was "
              "planned and nothing was written.")
        return 1

    print("requested: %r" % w.get("requested"))
    if w.get("refused"):
        print("REFUSED: %s" % w.get("reason"))
        for c in w.get("candidates") or []:
            print("   candidate  %-32s %s" % (c.get("label") or "?", c.get("defName")))
        if (w.get("candidateCount") or 0) > len(w.get("candidates") or []):
            print("   (%d candidates in total; the first %d are listed)"
                  % (w["candidateCount"], len(w.get("candidates") or [])))
        return 1

    res = w.get("resolved") or {}
    print("resolved:  %s (%s)" % (res.get("label"), res.get("defName")))
    before, after = w.get("before"), w.get("after")
    print("before:    %s" % ("NONE -- no project was selected" if before is None
                             else _name(before)))
    print("after:     %s%s"
          % ("NONE" if after is None else _name(after),
             "   (predicted -- nothing was written)" if w.get("afterIsPredicted")
             else "   (read back from the game)"))
    print("applied:   %s      changed: %s" % (r.get("applied"), w.get("changed")))
    if w.get("alreadyCurrent"):
        print("note:      it was ALREADY the current project, so before == after "
              "and changed is false. That is a legal no-op, not a failure.")
    if w.get("landedInMainSlot") is False:
        print("note:      !! it did NOT land in the main slot -- knowledge "
              "category %s. See currentByCategory."
              % w.get("landedInCategory"))
    for row in w.get("missingMemeUnlocks") or []:
        print("   !! %s unlocks %s, which the colony's ideoligion has no meme for "
              "(%s). The Research tab would have asked to confirm; this did not."
              % (res.get("label"), row.get("label"),
                 ", ".join(row.get("memesTheColonyLacks") or [])))
    print(w.get("appliedNote") or "")
    watch_line(r)
    return 0


# ------------------------------------------------------------------- main

def main_set(argv):
    """`research.py set <name> [--do] [--no-watch]`."""
    do = "--do" in argv
    watch = "--no-watch" not in argv
    argv = [a for a in argv if a not in ("--do", "--no-watch")]
    if not argv or argv[0].startswith("--"):
        print("usage: research.py set \"<project name or defName>\" [--do] [--no-watch]")
        print("       a dry run without --do; run that first and read it.")
        return 1
    name = " ".join(argv).strip()
    rim.init()
    return print_set(choose(name, do=do, watch=watch))


def main():
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0

    def opt(flag, default=None):
        return (argv[argv.index(flag) + 1]
                if flag in argv and len(argv) > argv.index(flag) + 1 else default)

    try:
        if argv and argv[0] == "set":
            return main_set(argv[1:])

        rim.init()
        r = state(locked="--locked" in argv,
                  finished="--finished" in argv,
                  unlocks="--unlocks" in argv,
                  filter=opt("--filter"))
    except Exception as e:
        print("research.py FAILED -- NO RESEARCH DATA WAS READ.")
        print("%s: %s" % (type(e).__name__, e))
        print(HELP)
        return 1

    if "--json" in argv:
        print(json.dumps(r, indent=1))
        return 0

    show(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
