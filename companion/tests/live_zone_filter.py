"""Live load test for `home/zone_cells` op=filter and `home/list_zones {filter:true}`.

    cd rimworld\\instruments
    python ..\\companion\\tests\\live_zone_filter.py

Run it against a loaded, PAUSED colony. It never saves and never unpauses. It
does select a zone and open its storage tab for a moment -- that is the watch
step of the one real write, and it closes itself again.

## The one real write, and the one it does not attempt

**Done: a priority round trip.** `op=filter` with only `priority`, set to a
value the stockpile does not currently have, then set straight back to the value
it had. Both halves are real writes down the real path, `after` is read back off
the game, and the stockpile ends where it started. This is also the only call
here that exercises the watch step.

**Also done, and free: allowing a def the stockpile already allows.** A real
`op=filter` with `allow` naming a label out of `sampleAllowed`. It goes through
`ThingFilter.SetAllow(def, true)` for real and cannot change anything, so
`changed` must come back false and `after` must equal `before`.

**NOT done: a `preset: nothing` round trip.** Restoring one exactly would need
the stockpile's full allowed-def list, and the payload carries counts, category
roll-ups and a ten-label sample -- not the list. A `preset: nothing` here could
not be undone, so it is only ever run as a dry run. Every preset is checked that
way instead, against arithmetic the reply itself supplies.

## What it checks, and what a failure would mean

  1  list_zones {filter:true} gives every stockpile a filter{} block
        FAIL = the opt-in block is missing, or is landing on non-stockpiles.
  2  the default list_zones payload carries no filter{} block
        FAIL = the opt-in is not opt-in, and every read got bigger.
  3  op=filter before{} matches what list_zones independently reports
        FAIL = the write tool and the read tool describe one filter differently,
        which is the exact failure `filterSummary` vs `filter` exists to avoid.
  P  every preset, dry run, with its counts recomputed from the reply
        I1 everything: allowedDefCount == storableDefCount
        I2 nothing: allowedDefCount == 0 and nothing rottable is allowed
        I3 perishables: every allowed def is rottable, and the count equals the
           number of rottable defs the `everything` run reported
        I4 nonperishables: the exact complement -- perishables + nonperishables
           == storableDefCount, and no rottable def is allowed
        I5 outdoorSafe: nothing rottable, and a subset of nonperishables
        I6 food: non-empty and inside the universe
        FAIL on any of these = a preset is not the set its presetDefinition
        claims, so the reply's own definition is not checkable.
  7  presetDefinition names exactly the preset that was used
  8  refusals: a growing zone, an unknown def name, a call with no field at
     all, a bad priority, a bad preset, filter arguments on op=add
        FAIL = a bad argument is being clipped into an answer.
  9  op=create with a preset, dry run: a filter{} block on a zone that does not
     exist yet, and nothing registered
        FAIL = a dry run consumed a zone ID, or planned no filter.
 10  a bogus argument key lands in unknownArguments[]
 11  THE REAL WRITES (see above)

Nothing below has been run against a live game. Every assertion is a promise
being checked for the first time; a failure is information.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "instruments"))

import rim                                                    # noqa: E402

WRITE = "home/zone_cells"
READ = "home/list_zones"
FAILURES = []
NEEDS_HUMAN = []
PRESETS = ["everything", "nothing", "food", "perishables", "nonperishables",
           "outdoorSafe"]


def check(ok, label, detail=""):
    if ok:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s   %s" % (label, detail))
        FAILURES.append(label + ("   " + detail if detail else ""))
    return ok


def human(msg):
    print("  NEEDS HUMAN: %s" % msg)
    NEEDS_HUMAN.append(msg)


def call(tool, args, label):
    """Every call is strict=False: a refusal is one of the answers under test."""
    print("\n--- %s: %s %s" % (label, tool, json.dumps(args)))
    try:
        r = rim.game(tool, args, strict=False)
    except Exception as e:
        check(False, label + " returned at all", "%s: %s" % (type(e).__name__, e))
        return None
    if not isinstance(r, dict):
        check(False, label + " returned a payload", str(r)[:200])
        return None
    return r


def summarize(f, prefix="      "):
    if not f:
        print(prefix + "(no filter block)")
        return
    print("%s%s of %s storable defs | priority %s | rottable allowed %s"
          % (prefix, f.get("allowedDefCount"), f.get("storableDefCount"),
             f.get("priority"), f.get("allowedRottableCount")))
    print("%scategories all=[%s] some=[%s]"
          % (prefix, ", ".join(f.get("categoriesFullyAllowed") or []),
             ", ".join(f.get("categoriesPartlyAllowed") or [])))
    print("%se.g. %s%s" % (prefix, ", ".join(f.get("sampleAllowed") or []),
                           " ..." if f.get("sampleTruncated") else ""))


def block_shape(f, label):
    """Every key the block promises is present, with the right kind of value.
    A count that is null is a count nobody took, and says so."""
    if not check(isinstance(f, dict), label + ": is an object", str(f)[:120]):
        return False
    ok = True
    for key in ("allowedDefCount", "storableDefCount", "allowedRottableCount"):
        ok &= check(isinstance(f.get(key), int), "%s: %s is a number" % (label, key),
                    repr(f.get(key)))
    for key in ("categoriesFullyAllowed", "categoriesPartlyAllowed", "sampleAllowed"):
        ok &= check(isinstance(f.get(key), list), "%s: %s is a list" % (label, key),
                    repr(f.get(key)))
    ok &= check(isinstance(f.get("allowsRottable"), bool),
                label + ": allowsRottable is a bool", repr(f.get("allowsRottable")))
    ok &= check(f.get("priority") is not None, label + ": priority is not null")
    return ok


def main():
    rim.init()
    print("=" * 74)
    print("home/zone_cells op=filter -- live load test.")
    print("One real write (a priority round trip) and one real no-op write.")
    print("=" * 74)

    # ---------------------------------------------------------------- 1 and 2
    plain = call(READ, {}, "1 list_zones, default")
    if plain is None or not plain.get("success"):
        print("\nNo zone census, so nothing below can run.")
        return 1
    zones = plain.get("zones") or []
    stockpiles_plain = [z for z in zones if z.get("type") == "Zone_Stockpile"]
    check(all("filter" not in z for z in zones),
          "2 the default payload carries no filter{} block on any zone",
          "found one on: " + ", ".join(z.get("label") or "?" for z in zones
                                       if "filter" in z))

    withf = call(READ, {"filter": True}, "1 list_zones, filter:true")
    if withf is None or not withf.get("success"):
        print("\nfilter:true did not answer; stopping.")
        return 1
    rows = withf.get("zones") or []
    stockpiles = [z for z in rows if z.get("type") == "Zone_Stockpile"]
    others = [z for z in rows if z.get("type") != "Zone_Stockpile"]
    check(len(stockpiles) == len(stockpiles_plain),
          "1 the same stockpiles come back with and without filter:true",
          "%d vs %d" % (len(stockpiles), len(stockpiles_plain)))
    check(all("filter" in z for z in stockpiles),
          "1 every stockpile row has a filter{} block",
          "missing on: " + ", ".join(z.get("label") or "?" for z in stockpiles
                                     if "filter" not in z))
    check(all("filter" not in z for z in others),
          "1 no non-stockpile zone has one",
          "found on: " + ", ".join(z.get("label") or "?" for z in others
                                   if "filter" in z))

    if not stockpiles:
        human("this colony has no stockpile zone, so op=filter cannot be exercised")
        print("\n%d failure(s)." % len(FAILURES))
        return 1 if FAILURES else 0

    target = stockpiles[0]
    name = target.get("label")
    print("\n    using stockpile \"%s\" (id %s)" % (name, target.get("id")))
    summarize(target.get("filter"))
    block_shape(target.get("filter"), "1 list_zones filter{}")

    # -------------------------------------------------------------------- 3
    seed = call(WRITE, {"op": "filter", "zone": name, "priority": target.get("priority"),
                        "dryRun": True},
                "3 op=filter dry run, priority set to its current value")
    if seed and seed.get("success"):
        before = seed.get("before") or {}
        summarize(before)
        block_shape(before, "3 op=filter before{}")
        live = target.get("filter") or {}
        for key in ("allowedDefCount", "storableDefCount", "allowedRottableCount",
                    "priority", "allowsRottable"):
            check(before.get(key) == live.get(key),
                  "3 before{}.%s agrees with list_zones" % key,
                  "%r vs %r" % (before.get(key), live.get(key)))
        check(seed.get("changed") is False,
              "3 re-writing the priority it already has reports changed:false",
              repr(seed.get("changed")))
        check((seed.get("watch") or {}).get("shown") is False,
              "3 a dry run shows nothing on screen",
              json.dumps(seed.get("watch")))

    # -------------------------------------------------------------- P: presets
    after = {}
    for preset in PRESETS:
        r = call(WRITE, {"op": "filter", "zone": name, "preset": preset, "dryRun": True},
                 "P preset=%s, dry run" % preset)
        if r is None or not r.get("success"):
            check(False, "P preset=%s was accepted" % preset,
                  (r or {}).get("error", "no reply"))
            continue
        a = r.get("after") or {}
        after[preset] = a
        summarize(a)
        block_shape(a, "P %s after{}" % preset)
        definition = r.get("presetDefinition") or {}
        check(list(definition.keys()) == [preset],
              "7 presetDefinition names exactly \"%s\"" % preset,
              repr(list(definition.keys())))
        for text in definition.values():
            print("      def: %s" % text)

    universe = None
    for a in after.values():
        universe = a.get("storableDefCount")
        break
    check(all(a.get("storableDefCount") == universe for a in after.values()),
          "P every preset is measured over the same universe",
          repr([a.get("storableDefCount") for a in after.values()]))

    if "everything" in after:
        check(after["everything"].get("allowedDefCount") == universe,
              "I1 everything allows every storable def",
              "%s of %s" % (after["everything"].get("allowedDefCount"), universe))
    if "nothing" in after:
        check(after["nothing"].get("allowedDefCount") == 0,
              "I2 nothing allows nothing", repr(after["nothing"].get("allowedDefCount")))
        check(after["nothing"].get("allowsRottable") is False
              and after["nothing"].get("allowedRottableCount") == 0,
              "I2 nothing allows nothing rottable either")
        check(not after["nothing"].get("categoriesFullyAllowed"),
              "I2 nothing leaves no category fully allowed",
              repr(after["nothing"].get("categoriesFullyAllowed")))

    rottable_total = (after.get("everything") or {}).get("allowedRottableCount")
    if "perishables" in after and rottable_total is not None:
        p = after["perishables"]
        check(p.get("allowedDefCount") == p.get("allowedRottableCount"),
              "I3 every def perishables allows is rottable",
              "%s allowed, %s rottable" % (p.get("allowedDefCount"),
                                           p.get("allowedRottableCount")))
        check(p.get("allowedDefCount") == rottable_total,
              "I3 perishables allows EVERY rottable def in the universe",
              "%s vs %s" % (p.get("allowedDefCount"), rottable_total))
    if "nonperishables" in after:
        n = after["nonperishables"]
        check(n.get("allowedRottableCount") == 0,
              "I4 nonperishables allows nothing rottable",
              repr(n.get("allowedRottableCount")))
        if "perishables" in after and universe is not None:
            total = n.get("allowedDefCount") + after["perishables"].get("allowedDefCount")
            check(total == universe,
                  "I4 perishables + nonperishables == storableDefCount",
                  "%s != %s" % (total, universe))
    if "outdoorSafe" in after:
        o = after["outdoorSafe"]
        check(o.get("allowedRottableCount") == 0,
              "I5 outdoorSafe allows nothing rottable", repr(o.get("allowedRottableCount")))
        if "nonperishables" in after:
            check(o.get("allowedDefCount") <= after["nonperishables"].get("allowedDefCount"),
                  "I5 outdoorSafe is a subset of nonperishables",
                  "%s > %s" % (o.get("allowedDefCount"),
                               after["nonperishables"].get("allowedDefCount")))
    if "food" in after and universe is not None:
        f = after["food"]
        check(0 < f.get("allowedDefCount") <= universe,
              "I6 food is non-empty and inside the universe",
              "%s of %s" % (f.get("allowedDefCount"), universe))
        check(f.get("allowsRottable") is True,
              "I6 food allows something rottable (raw food rots)",
              repr(f.get("allowedRottableCount")))

    # -------------------------------------------------- preset then allow/disallow
    combo = call(WRITE, {"op": "filter", "zone": name, "preset": "nothing",
                         "allow": "Foods", "dryRun": True},
                 "P preset=nothing then allow the Foods category, dry run")
    if combo and combo.get("success"):
        a = combo.get("after") or {}
        summarize(a)
        check(a.get("allowedDefCount") > 0,
              "P allow runs AFTER the preset, so Foods survives preset=nothing",
              repr(a.get("allowedDefCount")))
        check("Foods" in [c for c in (a.get("categoriesFullyAllowed") or [])]
              or a.get("allowedDefCount") > 0,
              "P the Foods category cascaded to its descendants")

    # ---------------------------------------------------------------- 8 refusals
    growing = [z for z in rows if z.get("type") == "Zone_Growing"]
    if growing:
        r = call(WRITE, {"op": "filter", "zone": growing[0].get("label"),
                         "preset": "nothing", "dryRun": True},
                 "8 op=filter on a growing zone")
        check(r is not None and r.get("success") is False,
              "8 a growing zone is refused", json.dumps(r)[:200] if r else "")
        if r:
            print("      %s" % r.get("error"))
    else:
        human("no growing zone on this map, so the non-stockpile refusal is untested")

    for label, args, why in (
        ("8 unknown def name",
         {"op": "filter", "zone": name, "allow": "NotARealDefXYZ", "dryRun": True},
         "an allow name that matches nothing"),
        ("8 no field at all", {"op": "filter", "zone": name, "dryRun": True},
         "a call with no preset, allow, disallow or priority"),
        ("8 bad priority",
         {"op": "filter", "zone": name, "priority": "Ludicrous", "dryRun": True},
         "a priority the enum has no value for"),
        ("8 bad preset",
         {"op": "filter", "zone": name, "preset": "everythingish", "dryRun": True},
         "a preset name that is not one of the six"),
        ("8 filter args on op=add",
         {"op": "add", "zone": name, "preset": "food", "x": 0, "z": 0, "dryRun": True},
         "preset on an op that cannot use it"),
    ):
        r = call(WRITE, args, label)
        check(r is not None and r.get("success") is False,
              "%s is refused (%s)" % (label, why), json.dumps(r)[:200] if r else "")
        if r and r.get("error"):
            print("      %s" % r["error"])
            if "NotARealDefXYZ" in json.dumps(args):
                check("NotARealDefXYZ" in r["error"],
                      "8 the refusal names the def it could not find", r["error"][:160])

    # ------------------------------------------------------------- 9 create
    created = call(WRITE, {"op": "create", "zoneType": "stockpile", "label": "ZFilterProbe",
                           "x": 0, "z": 0, "width": 1, "height": 1,
                           "preset": "outdoorSafe", "priority": "Preferred",
                           "dryRun": True},
                   "9 op=create with a preset, dry run")
    if created is not None:
        # Cell (0,0) is inside the map-edge no-zone band, so this plans and
        # refuses the cell -- which is the point: nothing may be registered.
        check(created.get("dryRun") is True, "9 the create call stayed a dry run")
        if created.get("filter"):
            summarize(created["filter"])
            block_shape(created["filter"], "9 create filter{}")
            check(created["filter"].get("priority") == "Preferred",
                  "9 the planned filter carries the priority asked for",
                  repr(created["filter"].get("priority")))
        recheck = call(READ, {}, "9 re-read the zone census after the dry run")
        if recheck and recheck.get("success"):
            check(not [z for z in (recheck.get("zones") or [])
                       if z.get("label") == "ZFilterProbe"],
                  "9 the dry run registered no zone")

    # ------------------------------------------------------------- 10 unknown key
    bogus = call(WRITE, {"op": "filter", "zone": name, "preset": "nothing",
                         "bogusKeyXYZ": 1, "dryRun": True}, "10 a bogus argument key")
    if bogus:
        check(bogus.get("unknownArguments") == ["bogusKeyXYZ"],
              "10 the bogus key is named in unknownArguments[]",
              repr(bogus.get("unknownArguments")))

    # ------------------------------------------------------ 11 THE REAL WRITES
    print("\n" + "=" * 74)
    print("11 THE REAL WRITES. Everything above was a dry run.")
    print("=" * 74)

    sample = (target.get("filter") or {}).get("sampleAllowed") or []
    if sample:
        noop = call(WRITE, {"op": "filter", "zone": name, "allow": sample[0],
                            "dryRun": False, "watch": False},
                    "11a REAL: allow \"%s\", which it already allows" % sample[0])
        if noop is not None:
            check(noop.get("success") is True, "11a the write succeeded",
                  repr(noop.get("error")))
            check(noop.get("changed") is False,
                  "11a allowing an already-allowed def changes nothing",
                  repr(noop.get("changed")))
            b, a = noop.get("before") or {}, noop.get("after") or {}
            check(b.get("allowedDefCount") == a.get("allowedDefCount"),
                  "11a after{} read back equals before{}",
                  "%s vs %s" % (b.get("allowedDefCount"), a.get("allowedDefCount")))
    else:
        human("the stockpile allows nothing, so the no-op write has no def to name")

    was = target.get("priority")
    other = "Important" if was != "Important" else "Preferred"
    if not was:
        human("the stockpile's priority did not read, so the round trip is skipped")
    else:
        out = call(WRITE, {"op": "filter", "zone": name, "priority": other,
                           "dryRun": False},
                   "11b REAL: priority %s -> %s (watch on)" % (was, other))
        if out is not None and check(out.get("success") is True,
                                     "11b the write succeeded", repr(out.get("error"))):
            check(out.get("changed") is True, "11b it reports changed",
                  repr(out.get("changed")))
            check((out.get("after") or {}).get("priority") == other,
                  "11b after{} is read back off the game",
                  repr((out.get("after") or {}).get("priority")))
            check((out.get("before") or {}).get("allowedDefCount")
                  == (out.get("after") or {}).get("allowedDefCount"),
                  "11b a priority write leaves the allowed defs alone")
            w = out.get("watch") or {}
            print("      watch: %s" % json.dumps(w))
            if not w.get("shown"):
                human("the watch step showed nothing: %s" % w.get("reason"))

        back = call(WRITE, {"op": "filter", "zone": name, "priority": was,
                            "dryRun": False},
                    "11b REAL: priority %s -> %s (putting it back)" % (other, was))
        if back is not None:
            check(back.get("success") is True and
                  (back.get("after") or {}).get("priority") == was,
                  "11b the stockpile is back where it started",
                  repr((back.get("after") or {}).get("priority")))

        final = call(READ, {"filter": True}, "11b independent re-read")
        if final and final.get("success"):
            row = [z for z in (final.get("zones") or []) if z.get("label") == name]
            if row:
                summarize(row[0].get("filter"))
                check(row[0].get("filter", {}).get("priority") == was,
                      "11b list_zones agrees the priority is restored",
                      repr(row[0].get("filter", {}).get("priority")))
                check(row[0].get("filter", {}).get("allowedDefCount")
                      == (target.get("filter") or {}).get("allowedDefCount"),
                      "11b the allowed-def count is exactly where it started",
                      "%s vs %s" % (row[0].get("filter", {}).get("allowedDefCount"),
                                    (target.get("filter") or {}).get("allowedDefCount")))

    # ------------------------------------------------------------------ report
    print("\n" + "=" * 74)
    print("%d failure(s), %d needing a human." % (len(FAILURES), len(NEEDS_HUMAN)))
    for f in FAILURES:
        print("  FAIL " + f)
    for h in NEEDS_HUMAN:
        print("  NEEDS HUMAN: " + h)
    print("""
Then run these by hand, in rimworld\\instruments, and read them:

    python zones.py --filter                          every stockpile's filter
    python zones.py filter "<a stockpile>" --preset perishables       DRY RUN
    python zones.py filter "<a stockpile>" --allow Silver --disallow Chunks
    python zones.py filter "<a stockpile>" --priority Important --do

What to look for:
  * `--filter` prints "N of M storable defs" for every stockpile, and the
    category roll-up matches what the storage tab's tree shows when you open it;
  * a dry run prints before and after and the stockpile is unchanged when you
    re-read it;
  * the `--do` call selects the zone and opens its storage tab while the change
    lands, then closes it -- and the tab shows the new priority;
  * every refusal names what it could not resolve.
""")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
