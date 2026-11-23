"""Live load test for `home/building_config` and `buildings.py set / gizmos`.

    cd rimworld\\instruments
    python ..\\companion\\tests\\live_building_config.py

Run it against a loaded, PAUSED colony. It never saves, never unpauses, and
never leaves anything changed.

## The one real write, and why it is safe

`forbidden` is the only field with a guaranteed way back: the value is a
boolean on a comp, `CompForbiddable.Forbidden`'s setter early-returns when the
value is unchanged, and writing it back restores exactly the state that was
there. So the script toggles `forbidden` on ONE colony building and writes it
straight back, and refuses to leave without doing so -- if the restore fails it
says PUT IT BACK BY HAND and names the thingId.

It picks the safest target that exists, in this order: a bed, then any other
colony building that carries CompForbiddable. If none does, it prints
`NEEDS HUMAN: nothing forbiddable on this map, skipping the write test` and
carries on. Everything else here is a read or a dry run.

`power`, `medical`, `forPrisoners` and `owner` are NEVER written for real:
power places a designation a colonist then acts on, and the three bed fields
drop the bed's owners on the way through. All four are exercised as dry runs.

Nothing below has ever been run against a live game. Every assertion is a
PROMISE being checked for the first time; a failure is information.

## What it checks, and what a failure would mean

  1  gizmos:true on a BED answers, with rows carrying type and disabled
        FAIL = the gizmo read is not working, or the bar is empty where the
        game draws Set-owner and As-medical.
  2  gizmos:true on a POWERED building answers
        FAIL = same, on the other half of the surface. A flickable building
        should show a Command_Toggle whose isActive is the want-switch.
  I1 every gizmo row has a type, and every Command_Toggle has isActive
        FAIL = isActive is being reported null for a toggle, so a caller
        cannot tell a checked box from an unchecked one.
  I2 the reply's before{} matches home/list_buildings' own read
        FAIL = the two tools disagree about the same building. `ownerNames`
        and `medical` on list_buildings and `assignedPawns` / `medical` here
        come off the same objects and must agree.
  3  an unknown thing is refused
  4  an ambiguous substring is refused WITH candidates[]
        FAIL = an ambiguous selector is being resolved by guessing, on a tool
        that writes.
  5  owner=<not a colonist> is refused with the candidate list in the reason
  6  power on a thing with no CompFlickable is refused
  7  medical on a non-bed is refused
        FAIL on any of 3-7 = a refusal is being turned into a silent skip or a
        write.
  8  a dry run of every field leaves the game alone
        FAIL = the dry run is not dry. Stop and do not run anything with --do.
  9  a bogus argument key lands in unknownArguments[]
        FAIL = a misspelled dryRun could be dropped silently, which on this
        tool is the difference between a plan and a changed colony.
 10  THE ONE REAL WRITE: forbidden toggled and put straight back
        FAIL = the write did not land, or `after` is echoing the request
        rather than being read back from the game.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "instruments"))

import rim                                                    # noqa: E402

TOOL = "home/building_config"
LIST = "home/list_buildings"
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
    """One call, refusals included -- a refusal is an answer with a reason in
    it and half this script is about reading those reasons."""
    print("\n--- %s: %s %s" % (label, TOOL, json.dumps(args)))
    try:
        r = rim.game(TOOL, args, strict=False)
    except Exception as e:
        check(False, label + " returned at all", "%s: %s" % (type(e).__name__, e))
        return None
    if not isinstance(r, dict):
        check(False, label + " returned a payload", str(r)[:200])
        return None
    if r.get("success"):
        t = r.get("thing") or {}
        p = t.get("position") or {}
        print("      %s (%s) at %s,%s   %d field(s), %d changed, %d refused, dryRun %s"
              % (t.get("label"), t.get("defName"), p.get("x"), p.get("z"),
                 r.get("fieldCount"), r.get("changeCount"), r.get("refusedCount"),
                 r.get("dryRun")))
    else:
        print("      REFUSED: %s" % str(r.get("error"))[:200])
    return r


def selector(row):
    """The "DefName@x,z" form, which home/list_buildings gives us directly --
    it prints defName and position and no thingId, so this is the handle that
    always exists."""
    p = row.get("position") or {}
    return "%s@%d,%d" % (row.get("defName"), p.get("x", -1), p.get("z", -1))


def survey():
    """The colony's own detailed building rows, from the READ tool."""
    r = rim.game(LIST, {"playerOnly": True, "aggregate": False})
    if not isinstance(r, dict) or not r.get("success"):
        return []
    return r.get("buildings") or []


# =========================================================== the checks

def gizmo_rows(r, label):
    """I1. Every row names its class, and a toggle says which way it is set."""
    rows = r.get("gizmos")
    if rows is None:
        check(False, "I1 %s: gizmos[] present when gizmos:true" % label,
              "the key was null")
        return
    typed = [g for g in rows if g.get("type")]
    check(len(typed) == len(rows), "I1 %s: every gizmo row names its type" % label,
          "%d of %d had none" % (len(rows) - len(typed), len(rows)))
    toggles = [g for g in rows if g.get("type") == "Command_Toggle"]
    bad = [g for g in toggles if g.get("isActive") is None]
    check(not bad, "I1 %s: every Command_Toggle carries isActive" % label,
          "%d toggle(s) reported null" % len(bad))
    print("      %d gizmo(s), %d toggle(s): %s"
          % (r.get("gizmoCount"), len(toggles),
             ", ".join("%s=%s" % (g.get("label"), g.get("isActive")) for g in toggles)))


def cross_check(r, row, label):
    """I2. before{} here and the row home/list_buildings independently read
    have to agree; they come off the same game objects."""
    before = r.get("before") or {}
    if "medical" in row:
        check(before.get("medical") == row.get("medical"),
              "I2 %s: medical agrees with home/list_buildings" % label,
              "%r vs %r" % (before.get("medical"), row.get("medical")))
    if "ownerNames" in row:
        mine = [p.get("name") for p in (before.get("assignedPawns") or [])]
        check(sorted(mine) == sorted(row.get("ownerNames") or []),
              "I2 %s: owners agree with home/list_buildings" % label,
              "%r vs %r" % (mine, row.get("ownerNames")))
    power = row.get("power") or {}
    if power:
        check(before.get("powered") == power.get("powered"),
              "I2 %s: powered agrees with home/list_buildings" % label,
              "%r vs %r" % (before.get("powered"), power.get("powered")))
        check(before.get("switchIsOn") in (None, power.get("switchedOn")),
              "I2 %s: switchIsOn agrees with home/list_buildings" % label,
              "%r vs %r" % (before.get("switchIsOn"), power.get("switchedOn")))


def main():
    rim.init()
    print("=" * 72)
    print("live_building_config.py -- read-only apart from ONE forbidden "
          "toggle put straight back.")
    print("=" * 72)

    rows = survey()
    if not rows:
        print("home/list_buildings returned no colony buildings at all. "
              "Nothing here can be checked.")
        return 1
    print("%d colony building row(s) to choose targets from." % len(rows))

    beds = [b for b in rows if "ownerNames" in b or "medical" in b]
    powered = [b for b in rows if b.get("power")]
    plain = [b for b in rows if not b.get("power") and "medical" not in b]

    # ---------------------------------------------------------------- 1, 2
    bed_reply = None
    if beds:
        bed_reply = call({"thing": selector(beds[0]), "gizmos": True}, "1 gizmos on a bed")
        if bed_reply and check(bed_reply.get("success"), "1 gizmos on a bed answered",
                               str(bed_reply.get("error"))[:160]):
            gizmo_rows(bed_reply, "bed")
            cross_check(bed_reply, beds[0], "bed")
            check((bed_reply.get("before") or {}).get("isBed") is True,
                  "1 the bed reports isBed true")
    else:
        human("no bed on this map, skipping the bed half of checks 1/I1/I2")

    powered_reply = None
    if powered:
        powered_reply = call({"thing": selector(powered[0]), "gizmos": True},
                             "2 gizmos on a powered building")
        if powered_reply and check(powered_reply.get("success"),
                                   "2 gizmos on a powered building answered",
                                   str(powered_reply.get("error"))[:160]):
            gizmo_rows(powered_reply, "powered")
            cross_check(powered_reply, powered[0], "powered")
    else:
        human("nothing with a power comp on this map, skipping check 2")

    # A gizmos-only call is a read: it must never claim to have written.
    for name, reply in (("bed", bed_reply), ("powered", powered_reply)):
        if reply and reply.get("success"):
            check(reply.get("applied") is False and reply.get("changeCount") == 0,
                  "1/2 the %s gizmos-only call wrote nothing" % name,
                  "applied=%r changed=%r" % (reply.get("applied"), reply.get("changeCount")))
            check((reply.get("watch") or {}).get("shown") is False,
                  "1/2 the %s gizmos-only call opened no watch" % name)

    # ------------------------------------------------------------ 3 unknown
    r = call({"thing": "definitelyNotABuildingXYZ"}, "3 unknown thing")
    if r is not None:
        check(r.get("success") is False and r.get("error"),
              "3 an unknown thing is refused with a reason", str(r)[:160])

    # ---------------------------------------------------------- 4 ambiguous
    counts = {}
    for b in rows:
        counts[b.get("defName")] = counts.get(b.get("defName"), 0) + 1
    dupes = [d for d, n in counts.items() if n > 1]
    if dupes:
        r = call({"thing": dupes[0]}, "4 ambiguous substring")
        if r is not None:
            ok = check(r.get("success") is False,
                       "4 an ambiguous substring is refused", str(r)[:160])
            if ok:
                cand = r.get("candidates") or []
                check(len(cand) > 1, "4 the refusal lists candidates[]",
                      "%d candidate(s)" % len(cand))
                check(all(c.get("thingId") and c.get("position") for c in cand),
                      "4 every candidate carries a thingId and a position")
    else:
        human("no def appears twice among the colony's buildings, skipping "
              "the ambiguity check")

    # --------------------------------------------------------- 5 bad owner
    if beds:
        r = call({"thing": selector(beds[0]), "owner": "NotAColonistXYZ"},
                 "5 owner that is not a colonist")
        if r is not None and r.get("success"):
            field = next((f for f in r["fields"] if f["field"] == "owner"), None)
            check(field is not None and field.get("refused") is True,
                  "5 owner=<not a colonist> is refused",
                  json.dumps(field)[:200] if field else "no owner row at all")
            if field:
                print("      reason: %s" % str(field.get("reason"))[:200])
                check(field.get("after") == field.get("before"),
                      "5 a refused field's after equals its before")

    # ------------------------------------------------- 6 power with no comp
    target = None
    for b in rows:
        if not b.get("power"):
            target = b
            break
    if target is not None:
        r = call({"thing": selector(target), "power": "off"},
                 "6 power on something with no switch")
        if r is not None and r.get("success"):
            field = next((f for f in r["fields"] if f["field"] == "power"), None)
            flickable = (r.get("before") or {}).get("flickable")
            if flickable:
                human("the building picked for check 6 (%s) turned out to have a "
                      "CompFlickable after all; the no-switch refusal was not "
                      "exercised" % selector(target))
            else:
                check(field is not None and field.get("refused") is True,
                      "6 power on a thing with no CompFlickable is refused",
                      json.dumps(field)[:200] if field else "no power row at all")

    # ----------------------------------------------------- 7 medical non-bed
    nonbed = next((b for b in rows if "medical" not in b), None)
    if nonbed is not None:
        r = call({"thing": selector(nonbed), "medical": True},
                 "7 medical on a non-bed")
        if r is not None and r.get("success"):
            field = next((f for f in r["fields"] if f["field"] == "medical"), None)
            check(field is not None and field.get("refused") is True,
                  "7 medical on a non-bed is refused",
                  json.dumps(field)[:200] if field else "no medical row at all")

    # ------------------------------------------------------- 8 the dry runs
    if beds:
        b = beds[0]
        before = call({"thing": selector(b)}, "8 state before the dry runs")
        r = call({"thing": selector(b), "forbidden": True, "medical": True,
                  "forPrisoners": True, "owner": "none", "power": "off",
                  "dryRun": True}, "8 dry run of every field")
        if r is not None and r.get("success"):
            check(r.get("dryRun") is True and r.get("applied") is False,
                  "8 the dry run reports dryRun true and applied false")
            check(r.get("afterIsPredicted") is True,
                  "8 afterIsPredicted is true on a dry run")
            check(r.get("before") == r.get("after"),
                  "8 before{} == after{} on a dry run")
            check((r.get("watch") or {}).get("shown") is False,
                  "8 a dry run opens no watch",
                  json.dumps(r.get("watch"))[:160])
            for f in r["fields"]:
                print("      %-12s refused=%-5s %s"
                      % (f["field"], f.get("refused"),
                         str(f.get("reason") or "")[:110]))
        after = call({"thing": selector(b)}, "8 state after the dry runs")
        if before is not None and after is not None and \
                before.get("success") and after.get("success"):
            check(before.get("after") == after.get("after"),
                  "8 THE DRY RUN CHANGED NOTHING",
                  "the building's configuration moved during a dry run")

    # --------------------------------------------------------- 9 bogus key
    r = call({"thing": selector(rows[0]), "contractTestBogusKey": 1}, "9 a bogus key")
    if r is not None:
        check(r.get("unknownArguments") == ["contractTestBogusKey"],
              "9 the bogus key is named in unknownArguments[]",
              json.dumps(r.get("unknownArguments")))
        check(bool(r.get("unknownArgumentsWarning")),
              "9 the warning is set alongside it")

    # ------------------------------------------------ 10 THE ONE REAL WRITE
    print("\n" + "=" * 72)
    print("10  THE ONE REAL WRITE: forbidden, toggled and put straight back.")
    print("=" * 72)

    victim = None
    for pool in (beds, plain, rows):
        for b in pool:
            probe = call({"thing": selector(b)}, "10 probing %s" % selector(b))
            if probe is not None and probe.get("success") \
                    and (probe.get("before") or {}).get("forbiddable"):
                victim = (b, probe)
                break
        if victim:
            break

    if not victim:
        human("nothing forbiddable on this map, skipping the write test -- "
              "CompForbiddable is on doors, blueprints, frames and a handful of "
              "buildings, not on all of them")
        return report()

    b, probe = victim
    spec = selector(b)
    was = (probe.get("before") or {}).get("forbidden")
    if not isinstance(was, bool):
        human("forbidden read back as %r on %s, not a boolean; not writing"
              % (was, spec))
        return report()
    print("  target %s, forbidden is currently %s" % (spec, was))

    wrote = call({"thing": spec, "forbidden": not was, "dryRun": False},
                 "10 write forbidden=%s" % (not was))
    if wrote is not None and check(bool(wrote.get("success")),
                                   "10 the real write answered",
                                   str(wrote.get("error"))[:200]):
        field = next((f for f in wrote["fields"] if f["field"] == "forbidden"), None)
        check(wrote.get("applied") is True, "10 applied is true on the real write")
        check(wrote.get("dryRun") is False, "10 dryRun is false on the real write")
        check(field is not None and field.get("before") == was,
              "10 the write's before matches what was read a moment ago")
        check(field is not None and field.get("after") == (not was),
              "10 the write's after is the new value, READ BACK",
              json.dumps(field)[:200] if field else "no forbidden row")
        check(field is not None and field.get("changed") is True,
              "10 the row says changed")
        check((wrote.get("after") or {}).get("forbidden") == (not was),
              "10 after{} reports the new value too")
        print("      watch: %s" % json.dumps(wrote.get("watch"))[:200])

        fresh = call({"thing": spec}, "10 independent re-read")
        if fresh is not None and fresh.get("success"):
            check((fresh.get("before") or {}).get("forbidden") == (not was),
                  "10 a FRESH call sees the new value -- the write really landed")

    # Put it back, whatever happened above.
    back = call({"thing": spec, "forbidden": was, "dryRun": False},
                "10 put it straight back")
    restored = back is not None and back.get("success") \
        and (back.get("after") or {}).get("forbidden") == was
    check(restored, "10 THE BUILDING WAS PUT BACK",
          "forbidden on %s is NOT %r any more -- PUT IT BACK BY HAND "
          "(thingId %s)" % (spec, was, (probe.get("thing") or {}).get("thingId")))

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

    python buildings.py gizmos <a bed>              the bar, nothing fired
    python buildings.py gizmos <a powered building>
    python buildings.py set <a bed> --medical on           DRY RUN -- read it
    python buildings.py set <a bed> --owner "<colonist>"   DRY RUN -- read it
    python buildings.py set <a lamp> --power off           DRY RUN -- read it
    python buildings.py set <a bed> --owner "<colonist>" --do

What to look for:
  * `gizmos` prints [x] / [ ] against every toggle, and the box matches what
    the game draws when you click that building;
  * a dry run prints before -> after with `(no change)` where nothing moves,
    and never says APPLIED;
  * a refused field prints its reason in the game's own words -- the prisoner
    refusal should read like the greyed-out tooltip;
  * `--power off --do` places a FLICK designation and leaves switchIsOn alone:
    the building keeps running until a colonist walks over. The reply says so
    and the map shows the flick marker;
  * with the watch helper in, `--do` selects the building and moves the camera
    to it before the change lands, then clears the selection.
""")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
