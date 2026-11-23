"""Live load test for `home/bills`, `bills.py` and list_buildings' billIngredients.

    cd rimworld\\instruments
    python ..\\companion\\tests\\live_bills.py

Run it against a loaded, PAUSED colony. It never saves, never unpauses, never
opens a tab it does not close, and never touches a bill that was already there.

## The one real write, and why it is safe

A bill that is ADDED and then DELETED again leaves the stack exactly as it was:
`BillStack.AddBill` appends and `BillStack.Delete` removes, and nothing else on
the bench is read or written. So the write test is

    add <bench> <the first recipe the bench offers>   dryRun:false
    delete <bench> <that bill's index>                dryRun:false

and it is skipped, loudly, when there is no bill giver, when the bench offers no
recipe, or when the stack is already at BillStack.MaxCount (15) -- in which case
adding would be refused anyway. The bill stack is counted before and after and
the two must match; a mismatch is a NEEDS HUMAN with the index to delete by hand.

Everything else here is a read or a dry run. `watch:false` is passed on the real
write so the test does not move the camera or the selection.

Nothing below has been run against a live game. Every assertion is a PROMISE
being checked for the first time; a failure is information.

## What it checks, and what a failure would mean

  1  action:'list' with no bench answers, and every bench row carries bills[]
        FAIL = the tool is not registered, or the bill-giver sweep is empty on a
        colony that plainly has a stove. Check `rimbridge/get_bridge_status` and
        that RimWorld was restarted since the DLL was copied.
  2  every bill on every bench carries canRunNow, blockedBy[], ingredients[]
        FAIL = the verdict is missing on some bills, which is worse than absent
        everywhere: it would be read as "this one is fine".
  I1 shortfall == max(0, needed - available) on EVERY ingredient row
        FAIL = the three numbers have come apart, and a shortfall of 0 no longer
        means "enough". This is the invariant the whole payload rests on.
  I2 canRunNow false implies blockedBy[] is non-empty, and true implies empty
        FAIL = a bill reported as blocked with no reason, which is exactly the
        unexplained refusal this toolkit exists to prevent.
  I3 a satisfied ingredient has shortfall 0, and vice versa
        FAIL = `satisfied` is being computed from something other than the
        arithmetic above.
  I4 available <= availableTotal on every ingredient row
        FAIL = the best-single-def count exceeds the sum over every def, which
        is arithmetically impossible and means one of them is not what it says.
  3  action:'list' with a bench narrows to exactly that bench
        FAIL = the bench filter is not applied.
  4  action:'recipes' answers with recipes[], each with ingredients[]
        FAIL = the "what could I add here" half is missing.
  5  a bogus argument key lands in unknownArguments[]
        FAIL = a misspelled `dryRun` could be dropped in silence, which on this
        tool is the difference between a plan and a changed colony.
  R1 a bench name that matches nothing is refused with candidates
  R2 an ambiguous bench name is refused with the candidates listed
        FAIL = an ambiguous name is being resolved by guessing.
  R3 a recipe the bench does not offer is refused, naming it
  R4 `allow` naming something outside the recipe's fixedIngredientFilter is
     refused, and nothing is written
        FAIL = a half-applied ingredient filter, which produces a bill that
        cannot run and looks configured.
  R5 an out-of-range index is refused with the valid range
  6  billIngredients:true adds ingredients[] to home/list_buildings' bill rows,
     and the DEFAULT call adds none
        FAIL = either the opt-in does nothing, or the default payload grew --
        and a default that silently carries the block is a default that costs
        the scan on every Scout tick.
  7  a dry-run add changes no bill count
        FAIL = the dry run is not dry. Stop and run nothing with --do.
  8  THE ONE REAL WRITE: add a bill, read it back, delete it, read that back
        FAIL = AddBill did not land, `after` is echoed rather than read back, or
        the delete did not put the stack back. Read the NEEDS HUMAN line.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "instruments"))

import rim                                                    # noqa: E402

TOOL = "home/bills"
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


def call(args, label, tool=TOOL):
    print("\n--- %s: %s %s" % (label, tool, json.dumps(args)))
    try:
        r = rim.game(tool, args, strict=False)   # a refusal is an answer here
    except Exception as e:
        check(False, label + " returned at all", "%s: %s" % (type(e).__name__, e))
        return None
    if not isinstance(r, dict):
        check(False, label + " returned a dict", str(r)[:200])
        return None
    print("      success %s; payload ~%d KB" % (r.get("success"), len(json.dumps(r)) // 1024))
    return r


def refusal(args, label, must_say=None, want_candidates=True):
    """A call that MUST be refused. Returns True when it was."""
    r = call(args, label)
    if r is None:
        return False
    ok = r.get("success") is False and bool(r.get("error"))
    check(ok, label + " is refused with a reason", json.dumps(r)[:300])
    if ok:
        print("      reason: %s" % r["error"])
        if must_say:
            check(must_say.lower() in r["error"].lower(),
                  label + " names what was wrong",
                  "the reason does not mention %r" % must_say)
        if want_candidates:
            check(bool(r.get("candidates")),
                  label + " lists candidates",
                  "no candidates[], so a caller cannot act on the refusal")
    return ok


# ------------------------------------------------------------- invariants

def ingredient_invariants(r, label):
    rows = 0
    bad_shortfall, bad_satisfied, bad_total, bad_reason = [], [], [], []
    for bench in r.get("benches") or []:
        for bill in bench.get("bills") or []:
            can = bill.get("canRunNow")
            why = bill.get("blockedBy")
            if can is False and not why:
                bad_reason.append("%s bill %s: canRunNow false with no blockedBy"
                                  % (bench.get("thingId"), bill.get("index")))
            if can is True and why:
                bad_reason.append("%s bill %s: canRunNow true with blockedBy %r"
                                  % (bench.get("thingId"), bill.get("index"), why))
            for ing in bill.get("ingredients") or []:
                rows += 1
                need, have, short = ing.get("needed"), ing.get("available"), ing.get("shortfall")
                if None in (need, have, short):
                    bad_shortfall.append("%r has a null in needed/available/shortfall" % ing.get("label"))
                    continue
                if short != max(0, need - have):
                    bad_shortfall.append("%s: %d != max(0, %d - %d)"
                                         % (ing.get("label"), short, need, have))
                if ing.get("satisfied") != (short == 0) and ing.get("filterAllowsAny") is not False:
                    bad_satisfied.append("%s: satisfied %r with shortfall %d"
                                         % (ing.get("label"), ing.get("satisfied"), short))
                total = ing.get("availableTotal")
                if total is not None and have > total:
                    bad_total.append("%s: available %d > availableTotal %d"
                                     % (ing.get("label"), have, total))
    print("      %d ingredient row(s) examined" % rows)
    check(not bad_shortfall, "I1 %s: shortfall == max(0, needed - available)" % label,
          "; ".join(bad_shortfall[:4]))
    check(not bad_reason, "I2 %s: canRunNow and blockedBy[] agree" % label,
          "; ".join(bad_reason[:4]))
    check(not bad_satisfied, "I3 %s: satisfied agrees with shortfall" % label,
          "; ".join(bad_satisfied[:4]))
    check(not bad_total, "I4 %s: available <= availableTotal" % label,
          "; ".join(bad_total[:4]))
    return rows


# ------------------------------------------------------------------- main

def main():
    rim.init()
    print("=" * 72)
    print("home/bills -- live load test. ONE real write: an add, deleted again.")
    print("=" * 72)

    # 1 -----------------------------------------------------------------
    listing = call({}, "1 default call (action defaults to list)")
    if listing is None:
        return report()
    check(listing.get("success") is True, "1 the default call succeeds",
          str(listing.get("error"))[:200])
    check(listing.get("action") == "list", "1 action echoes as 'list'",
          repr(listing.get("action")))
    benches = listing.get("benches") or []
    print("      %d bench(es) in scope, %d on the map, %d haulable stack(s) scanned"
          % (len(benches), listing.get("benchesOnMap"), listing.get("ingredientsScanned")))
    check(isinstance(listing.get("attention"), dict), "1 attention{} is present")
    if not benches:
        human("no bill giver on this map at all -- every bench test below is "
              "skipped. That is a fact about the colony, not a failure.")
        return report()

    # 2 -----------------------------------------------------------------
    missing = []
    total_bills = 0
    for b in benches:
        for bill in b.get("bills") or []:
            total_bills += 1
            for key in ("canRunNow", "blockedBy", "ingredients", "filter", "config"):
                if key not in bill:
                    missing.append("%s bill %s has no %s" % (b.get("thingId"), bill.get("index"), key))
    check(not missing, "2 every bill carries the verdict and its configuration",
          "; ".join(missing[:4]))
    print("      %d bill(s) across %d bench(es)" % (total_bills, len(benches)))

    ingredient_invariants(listing, "list")

    # 3 -----------------------------------------------------------------
    target = benches[0]
    bench_id = target.get("thingId")
    one = call({"bench": bench_id}, "3 list one bench by ThingID")
    if one is not None:
        rows = one.get("benches") or []
        check(len(rows) == 1 and rows[0].get("thingId") == bench_id,
              "3 the bench filter narrows to exactly that bench",
              "%d row(s) back" % len(rows))
        ingredient_invariants(one, "one bench")

    # 4 -----------------------------------------------------------------
    rec = call({"action": "recipes", "bench": bench_id}, "4 recipes on that bench")
    offered = []
    if rec is not None:
        offered = rec.get("recipes") or []
        check(rec.get("recipes") is not None, "4 recipes[] is present on action:'recipes'")
        print("      %d recipe(s) available on %s" % (len(offered), target.get("label")))
        bad = [x.get("defName") for x in offered if "ingredients" not in x
               or "ingredientsOnHand" not in x]
        check(not bad, "4 every recipe row carries ingredients[] and ingredientsOnHand",
              ", ".join(str(x) for x in bad[:4]))
        onhand = sum(1 for x in offered if x.get("ingredientsOnHand"))
        print("      %d of %d could run right now" % (onhand, len(offered)))

    # 5 -----------------------------------------------------------------
    bogus = call({"bogusKeyXYZ": 1}, "5 a bogus argument key")
    if bogus is not None:
        check(bogus.get("unknownArguments") == ["bogusKeyXYZ"],
              "5 the bogus key is named in unknownArguments[]",
              repr(bogus.get("unknownArguments")))
        check(bool(bogus.get("unknownArgumentsWarning")),
              "5 unknownArgumentsWarning is set")
    clean = call({"action": "list"}, "5b a clean call")
    if clean is not None:
        check(clean.get("unknownArguments") == [],
              "5b a clean call reports no unknown arguments",
              repr(clean.get("unknownArguments")))

    # R1..R5 ------------------------------------------------------------
    refusal({"bench": "NoSuchBenchZZZ"}, "R1 a bench name that matches nothing",
            must_say="NoSuchBenchZZZ")

    # An ambiguous name: the shortest substring shared by two bench labels. When
    # the colony has only one bill giver there is nothing ambiguous to send, and
    # the check is skipped rather than faked.
    labels = [str(b.get("label") or "") for b in benches]
    ambiguous = None
    for size in range(1, 6):
        seeds = {}
        for lab in labels:
            key = lab[:size].lower()
            seeds[key] = seeds.get(key, 0) + 1
        for key, n in seeds.items():
            if n > 1 and key.strip():
                ambiguous = key
                break
        if ambiguous:
            break
    if ambiguous:
        refusal({"bench": ambiguous}, "R2 an ambiguous bench name (%r)" % ambiguous,
                must_say="ambiguous")
    else:
        human("no two bill givers share a label prefix, so R2 (ambiguous bench) "
              "could not be exercised on this colony.")

    refusal({"action": "add", "bench": bench_id, "recipe": "NoSuchRecipeZZZ"},
            "R3 a recipe this bench does not offer", must_say="NoSuchRecipeZZZ")

    if offered:
        first = offered[0]
        refusal({"action": "add", "bench": bench_id, "recipe": first.get("defName"),
                 "allow": "Silver"},
                "R4 allow naming something outside the recipe's fixedIngredientFilter",
                must_say="fixedIngredientFilter", want_candidates=False)
    else:
        human("this bench offers no recipe, so R4 (allow outside the fixed "
              "filter) could not be exercised.")

    refusal({"action": "set", "bench": bench_id, "index": 999, "suspended": "off"},
            "R5 an out-of-range index", must_say="999")

    # 6 -----------------------------------------------------------------
    plain = call({}, "6 home/list_buildings, DEFAULT", tool="home/list_buildings")
    opted = call({"billIngredients": True}, "6 home/list_buildings, billIngredients:true",
                 tool="home/list_buildings")
    if plain is not None and opted is not None:
        def bill_rows(r):
            return [bill for b in (r.get("buildings") or []) for bill in (b.get("bills") or [])]
        plain_bills, opted_bills = bill_rows(plain), bill_rows(opted)
        check(all("ingredients" not in b for b in plain_bills),
              "6 the DEFAULT list_buildings carries no ingredients[] on any bill row",
              "%d of %d rows carry it" % (sum(1 for b in plain_bills if "ingredients" in b),
                                          len(plain_bills)))
        check((plain.get("filters") or {}).get("billIngredients") is False,
              "6 filters.billIngredients is present and false on a default call",
              repr((plain.get("filters") or {}).get("billIngredients")))
        check(bool(opted_bills) is False or all(
                  "ingredients" in b and "canRunNow" in b and "blockedBy" in b
                  for b in opted_bills),
              "6 billIngredients:true adds all three keys to EVERY bill row",
              "%d of %d rows carry ingredients" % (sum(1 for b in opted_bills if "ingredients" in b),
                                                   len(opted_bills)))
        check("billsShortOfIngredients" in (opted.get("attention") or {}),
              "6 attention gains billsShortOfIngredients under the opt-in")
        check("billsShortOfIngredients" not in (plain.get("attention") or {}),
              "6 and the DEFAULT attention does not carry it -- a zero without "
              "the scan would read as 'every bill can run'")
        print("      default ~%d KB, billIngredients ~%d KB"
              % (len(json.dumps(plain)) // 1024, len(json.dumps(opted)) // 1024))

    # 7, 8: the write ---------------------------------------------------
    stack_before = target.get("billCount")
    if not offered:
        human("this bench offers no recipe, so the add/delete write test is "
              "skipped. Nothing was written.")
        return report()
    if (stack_before or 0) >= (target.get("maxBills") or 15):
        human("this bench's stack is already at BillStack.MaxCount, so an add "
              "would be refused. The write test is skipped; nothing was written.")
        return report()

    add_recipe = offered[0].get("defName")
    dry = call({"action": "add", "bench": bench_id, "recipe": add_recipe,
                "repeatCount": 1, "dryRun": True},
               "7 a DRY RUN add")
    if dry is not None:
        w = dry.get("write") or {}
        check(dry.get("applied") is False, "7 a dry run reports applied:false")
        check(w.get("afterIsPredicted") is True, "7 and says its `after` is predicted")
        check((dry.get("watch") or {}).get("shown") is False,
              "7 a dry run shows nothing on screen",
              repr(dry.get("watch")))
        print("      the bill it WOULD create: canRunNow %s%s"
              % (w.get("canRunNow"),
                 "" if w.get("canRunNow") else " -- " + "; ".join(w.get("blockedBy") or [])))
    recheck = call({"bench": bench_id}, "7b re-read the bench after the dry run")
    if recheck is not None:
        now = (recheck.get("benches") or [{}])[0].get("billCount")
        check(now == stack_before, "7b the dry run changed no bill count",
              "%r -> %r" % (stack_before, now))

    # 8: the real write, and the delete that puts it back.
    real = call({"action": "add", "bench": bench_id, "recipe": add_recipe,
                 "repeatCount": 1, "dryRun": False, "watch": False},
                "8 THE REAL WRITE: add one bill")
    if real is None or not real.get("success"):
        human("the real add did not answer; check the bench by hand before "
              "assuming nothing was written.")
        return report()

    w = real.get("write") or {}
    check(real.get("applied") is True, "8 the add reports applied:true", json.dumps(w)[:300])
    check(w.get("afterIsPredicted") is False,
          "8 `after` is read back from the stack, not predicted")
    after = w.get("after") or {}
    new_index = (after.get("bill") or {}).get("index")
    check(after.get("billCount") == (stack_before or 0) + 1,
          "8 the stack grew by exactly one",
          "%r -> %r" % (stack_before, after.get("billCount")))
    print("      the new bill is at index %r; canRunNow %s%s"
          % (new_index, w.get("canRunNow"),
             "" if w.get("canRunNow") else " -- " + "; ".join(w.get("blockedBy") or [])))

    if new_index is None:
        human("the add landed but its index could not be read. Open the bench's "
              "Bills tab and delete the bill named %r by hand." % add_recipe)
        return report()

    back = call({"action": "delete", "bench": bench_id, "index": new_index,
                 "dryRun": False, "watch": False},
                "8b PUT IT BACK: delete the bill just added")
    if back is None or not back.get("success") or not back.get("applied"):
        human("the delete did not land. Open %s's Bills tab and delete the bill "
              "at index %d (%r) BY HAND."
              % (target.get("label"), new_index, add_recipe))
        return report()

    final = call({"bench": bench_id}, "8c re-read the bench after the delete")
    if final is not None:
        now = (final.get("benches") or [{}])[0].get("billCount")
        ok = check(now == stack_before, "8c the stack is back where it started",
                   "%r -> %r" % (stack_before, now))
        if not ok:
            human("the bill stack on %s is %r and started at %r. Check it by hand."
                  % (target.get("label"), now, stack_before))
        ingredient_invariants(final, "after the write")

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

    python bills.py                          every bench, every bill, the verdict
    python bills.py <a bench label>          one bench
    python bills.py recipes <a bench label>  what could be added, and could it run
    python bills.py --json                   the raw reply

    python bills.py add <bench> "<a recipe>" --repeat 4      DRY RUN -- read it
    python bills.py set <bench> 0 --forever                  DRY RUN -- read it
    python bills.py move <bench> 0 --down                    DRY RUN -- read it

What to look for:
  * a bill whose bench is unpowered says `bench unpowered`, not a shortfall;
  * a bill whose filter excludes what is on the floor prints
    `[this bill's filter excludes N on the map]` on the ingredient line --
    that is the insect-meat case, and it is the reason this tool exists;
  * `available` below `availableTotal` on an ingredient means the colony has
    enough in total but not enough of any ONE kind, which is what the game
    actually requires;
  * a dry-run write prints PREDICTED and changes nothing when you re-read;
  * a real write opens the bench's Bills tab, lands inside it, and closes it.
""")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
