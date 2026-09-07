"""Worktable bills: what is queued, whether it can actually run, and the writes.

  python bills.py                          # every bench: bills, config, verdict
  python bills.py <bench>                  # one bench
  python bills.py recipes <bench>          # what could be added, and could it run
  python bills.py --json                   # the raw reply
  python bills.py --help                   # this text

  python bills.py add <bench> <recipe> [options]        # DRY RUN
  python bills.py add <bench> <recipe> [options] --do   # and do it
  python bills.py set <bench> <index> [options] --do
  python bills.py delete <bench> <index> --do
  python bills.py move <bench> <index> --to 0 --do
  python bills.py move <bench> <index> --up --do

  options: --forever | --repeat N | --target N [--unpause N] [--pause on|off]
           --allow X,Y  --only X,Y  --disallow X,Y  --radius N  --skill MIN-MAX
           --worker NAME|anyone  --store best|floor|<stockpile label>
           --suspend | --unsuspend      --no-watch      --json

  import bills
  bills.state()                bills.state(bench="stove")
  bills.recipes("stove")       bills.add("stove", "Simple meal", do=False, repeat=10)

## The thing this exists for

A queued bill that cannot run looks identical to one that can. `0x` finished,
suspended, paused, an unpowered bench -- those were readable already. The one
that was not is the INGREDIENT: a stove bill over a larder that is all insect
meat cooks nothing, because RimWorld's default meal filter excludes insect meat,
and every tool in this stack reported the queue as live. Every bill row here
carries `CAN RUN` or `CANNOT RUN` with the reason, and an `add` prints the same
verdict for the bill it is about to create -- so "added a bill that cannot run"
is said at the moment of adding, not discovered a day later.

The numbers come from the map, not from the bill: for each ingredient slot,
every spawned haulable the recipe's filter AND the bill's own filter allow,
unforbidden, inside the bill's ingredient search radius of the bench.

  needed / available / shortfall     shortfall == max(0, needed - available)
  availableTotal                     every allowed def summed
  excludedByFilter                   units the BILL's filter turned away

A slot nothing on the map matched is named by the recipe's own summary
(`corpses`), never by one representative def: `human corpse` on a bill whose
filter excludes human corpses was read as fact by three sessions. An ingredient
that IS on the map but forbidden says so ahead of any missing-ingredient line.

`available` is the best SINGLE def's count, not the sum. RimWorld satisfies one
ingredient slot from one def unless the recipe allows mixing, so five rice and
five corn do not cook a meal that wants ten of something -- and `availableTotal`
beside it is what makes that visible instead of puzzling.

Not modelled: reachability, reservation, and a pawn's own forbidden rules. A
thing behind a locked door counts here and does not count to the game.

## Naming a bench

A ThingID (`ElectricStove123`), `defName@x,z` (`TableButcher@120,140`), a bare
`x,z` (`120,140`, spaces allowed), or a unique substring of the label or
defName. An ambiguous name is REFUSED with the candidates printed, never
resolved by guessing. Every listing line prints the ThingID, so the
unambiguous form is always one call away.

The companion's own resolver takes only `defName@x,z`, so a bare cell is
resolved here, with one extra listing call -- for the LISTING, `recipes` and
every write alike, so no two verbs can address different benches. Bench rows
carry the anchor cell, so a bench answers to the cell its listing row prints,
and every write echoes the bench it actually resolved to.

An unknown `--flag` is an argument error naming the flag that exists (the
repeat count is `--repeat N`); it is never folded into the recipe name.

## "until you have N"

The `have M` beside it is the game's own `RecipeWorkerCounter.CountProducts`:
the product in the storage THIS bill counts, at its hp and quality range. Not
carried, not on the floor, not outside that storage. It prints as `have M in
counted storage`, never as how much exists. Beside it, when the companion is
current, the two wider counts: `(58 stored, 98 total)` -- every stack in any
storage, and every spawned stack on the map. A pemmican bill reading `have 0`
with 98 pemmican in existence is all three numbers doing their job.

## The writes

`add` / `set` / `delete` / `move` are DRY RUNS until `--do`, the same convention
`pawns.py set`, `zones.py` and `research.py set` use. A real write reports the
before, the after READ BACK from the bill stack, and what changed. `allow` and
`allow`, `only`, and `disallow` are bounded by the recipe's own fixed ingredient
filter. `allow` adds entries; `only` clears existing allowances and makes the
named defs/categories the complete whitelist; `disallow` removes entries. A name
outside it refuses the whole call rather than half-applying a filter.

A real write selects the bench and opens its Bills tab while the change lands,
then closes it. `--no-watch` skips that; the write is identical either way.
"""
import json
import re
import sys

import rim

TOOL = "home/bills"

HELP = (
    "\n  The exception above is the failure evidence. This message does NOT mean"
    "\n  the companion is unregistered; every failed call prints this help."
    "\n  Check registration with: python rim.py call rimbridge/get_bridge_status"
    "\n  If registered with no errors, keep the exact command and exception for"
    "\n  diagnosis. Do not reinstall or restart solely because this help appeared."
    "\n  If registration is missing or failed, check, in order:"
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

def call(args):
    """One home/bills call. Raises rather than returning a half answer."""
    r = rim.game(TOOL, args)
    if not isinstance(r, dict) or "action" not in r:
        raise rim.BridgeError(
            "%s returned no action -- this is not a bills payload: %.300r" % (TOOL, r))
    return r


def state(bench=None, all_factions=False):
    """The listing. A named bench takes the same forms every write takes -- the
    listing and the writes must never be able to address different benches."""
    args = {"action": "list"}
    if bench:
        spec, refused = _resolve_bench(bench)
        if refused:
            return refused
        args["bench"] = spec
    if all_factions:
        args["allFactions"] = True
    return call(args)


_CELL = re.compile(r"^\s*(-?\d+)\s*,\s*(-?\d+)\s*$")


def _cell(spec):
    """'120,140' or '120, 140' -> (120, 140). None for every other bench form."""
    m = _CELL.match(spec or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def _resolve_bench(spec):
    """(what to send the tool, refusal payload or None).

    The companion resolves a ThingID, `defName@x,z` or a label substring, but
    not a bare cell, so a bare cell is turned into the ThingID standing there
    with one `list` call. Every other form is sent untouched. No match or
    several is REFUSED in the tool's own shape, never guessed at.
    """
    cell = _cell(spec)
    if cell is None:
        return spec, None
    r = state()
    hits = [b for b in (r.get("benches") or [])
            if (b.get("position") or {}).get("x") == cell[0]
            and (b.get("position") or {}).get("z") == cell[1]]
    if len(hits) == 1 and hits[0].get("thingId"):
        return hits[0]["thingId"], None
    candidates = [{"thingId": b.get("thingId"), "defName": b.get("defName"),
                   "label": b.get("label"), "position": _pos(b)}
                  for b in (hits or r.get("benches") or [])]
    if not hits:
        return spec, {"success": False, "candidates": candidates,
                      "error": "no bill giver stands at %d,%d. A bench answers to the cell "
                               "its listing row prints, which for a multi-cell bench is one "
                               "of the cells it covers." % cell}
    return spec, {"success": False, "candidates": candidates,
                  "error": "%d bill givers occupy %d,%d -- use a ThingID from candidates[]."
                           % (len(hits), cell[0], cell[1])}


def recipes(bench):
    spec, refused = _resolve_bench(bench)
    return refused or call({"action": "recipes", "bench": spec})


def write(action, bench, do=False, watch=True, **kw):
    """add / set / delete / move. A dry run unless do=True.

    2026-09-07: `bills.py recipes 128,139` accepted a bare cell and
    `bills.py add 128,139 ...` refused it (`no bill giver matches bench
    '128,139'`), because only `recipes` went through `_resolve_bench`. Every
    verb takes the same bench forms now; a bench answers to the cell its
    listing row prints, whichever verb is asking.
    """
    spec, refused = _resolve_bench(bench)
    if refused:
        return refused
    args = {"action": action, "bench": spec, "dryRun": not do}
    if not watch:
        args["watch"] = False
    for key, value in kw.items():
        if value is not None:
            args[key] = value
    r = call(args)
    # What the CALLER typed, beside what the tool resolved. A client-side key,
    # named with a leading underscore so it cannot be mistaken for the game's.
    if isinstance(r, dict):
        r["_askedBench"] = bench
    return r


def add(bench, recipe, do=False, **kw):
    return write("add", bench, do=do, recipe=recipe, **kw)


# ------------------------------------------------------------- formatting

def _pos(row):
    p = row.get("position") or {}
    return "%s,%s" % (p.get("x", "?"), p.get("z", "?"))


_SUMMARY_COUNT = re.compile(r"^\s*\d+(\.\d+)?\s*x\s*", re.I)


def _summary_noun(summary):
    """'1x corpses' -> 'corpses'. The recipe's own name for the slot."""
    if not summary:
        return None
    return _SUMMARY_COUNT.sub("", str(summary)).strip() or None


def _noun(ing):
    """What to CALL this slot.

    `label` is a representative def whenever nothing on the map matched, and a
    representative the bill's own filter excludes is a lie: ButcherCorpseFlesh
    printed `human corpse` for a bill whose filter allows only animal and insect
    corpses. New payloads say so in `generic`; older ones are recognised by an
    empty availableDefs with nothing counted.
    """
    generic = ing.get("generic")
    if generic is None:
        generic = not (ing.get("availableDefs") or ing.get("availableTotal")
                       or ing.get("available"))
    if generic:
        return (_summary_noun(ing.get("summary")) or ing.get("filterSummary")
                or ing.get("label") or "ingredient")
    return ing.get("label") or "ingredient"


def _num(value):
    """A count that was NOT READ prints as ?, never as 0."""
    return "?" if value is None else value


def _finished(bill):
    """A 'do Nx' bill with nothing left to do. Flag first, config second, so a
    payload older than the `finished` flag is still read correctly."""
    if bill.get("finished"):
        return True
    cfg = bill.get("config") or {}
    count = cfg.get("repeatCount")
    return cfg.get("repeatMode") == "RepeatCount" and count is not None and count <= 0


def _ing_line(ing, indent=8):
    mark = "   " if ing.get("satisfied") else "!! "
    bits = "%s%s%s  need %s, have %s" % (
        " " * indent, mark, _noun(ing),
        _num(ing.get("needed")), _num(ing.get("available")))
    if ing.get("shortfall"):
        bits += "  SHORT %s" % ing["shortfall"]
    total = ing.get("availableTotal")
    if total is not None and total != ing.get("available"):
        bits += "  (%s across every allowed kind; the game will not mix them)" % total
    if ing.get("excludedByFilter"):
        stale = ing.get("excludedNotFresh") or 0
        bits += "  [this bill's filter excludes %d on the map%s]" % (
            ing["excludedByFilter"],
            ", %d of them ROTTEN or DESSICATED" % stale if stale else "")
    if ing.get("filterAllowsAny") is False:
        bits += "  [the filter allows NOTHING for this slot]"
    if ing.get("excludedForbidden"):
        bits += "  [%d forbidden]" % ing["excludedForbidden"]
    if ing.get("excludedOutOfRadius"):
        bits += "  [%d outside the search radius]" % ing["excludedOutOfRadius"]
    return bits


def _reasons(bill):
    """Why it cannot run, worst-named-first.

    The companion's own `missing X (a/b)` lines are rebuilt from the ingredient
    rows so the noun and the numbers come from one place, and so an ingredient
    that is on the map but FORBIDDEN says that instead of reading as absent.
    """
    reasons = [r for r in (bill.get("blockedBy") or [])]
    rows = bill.get("ingredients") or []
    if not rows:
        return reasons

    reasons = [r for r in reasons if not str(r).startswith("missing ")]
    forbidden, other = [], []
    for ing in rows:
        if ing.get("satisfied"):
            continue
        noun = _noun(ing)
        blocked = ing.get("excludedForbidden") or 0
        stale = ing.get("excludedNotFresh") or 0
        avail = ing.get("available") or 0
        if stale and not avail:
            # 2026-09-07: "6 corpses on the map but all forbidden -- unforbid
            # them" about six SKELETONS 100-200 cells out. A butcher recipe's
            # fixed filter disallows the AllowRotten special filter, whose test
            # is `CompRottable.Stage != Fresh` -- rotting AND dessicated. They
            # were never usable, and unforbidding them yields nothing.
            other.append("%d %s on the map are ROTTEN or DESSICATED (skeletons), "
                         "which this recipe cannot use at all -- unforbidding "
                         "them changes nothing; only a FRESH one counts"
                         % (stale, noun))
        if blocked and not avail:
            forbidden.append("%d %s on the map but all forbidden -- unforbid them"
                             % (blocked, noun))
        elif ing.get("filterAllowsAny") is False:
            other.append("this bill's filter allows nothing for %s" % noun)
        elif ing.get("needed") is None or ing.get("available") is None:
            other.append("missing %s (counts NOT READ)" % noun)
        else:
            other.append("missing %s (%s/%s)" % (noun, ing["available"], ing["needed"]))
    return forbidden + other + reasons


def _verdict_line(bill, indent=6):
    if _finished(bill):
        count = (bill.get("config") or {}).get("repeatCount")
        return "%sFINISHED (%s left) -- it does not run again until the count is raised" % (
            " " * indent, 0 if count is None else count)
    if bill.get("canRunNow") is None:
        return "%s?? verdict NOT READ for this bill" % (" " * indent)
    if bill.get("canRunNow"):
        return "%sCAN RUN NOW" % (" " * indent)
    return "%sCANNOT RUN: %s" % (" " * indent,
                                 "; ".join(_reasons(bill) or ["no reason given"]))


def _config_line(cfg, indent=6):
    if not cfg:
        return None
    bits = []
    mode = cfg.get("repeatMode")
    if mode == "RepeatCount":
        bits.append("do %sx" % cfg.get("repeatCount"))
    elif mode == "TargetCount":
        bits.append("until you have %s" % cfg.get("targetCount"))
        # The game's own count (RecipeWorkerCounter.CountProducts): the product
        # in the storage this bill counts, at its hp/quality range -- not
        # carried, not on the floor, not outside it. Named, so it is not read
        # as a map total. The wider numbers print only if the payload has them.
        if cfg.get("productCount") is not None:
            have = "have %s in counted storage" % cfg["productCount"]
            stored, total = cfg.get("productCountStored"), cfg.get("productCountOnMap")
            if stored is not None and total is not None:
                have += " (%s stored, %s total)" % (stored, total)
            bits.append(have)
        if cfg.get("pauseWhenSatisfied"):
            bits.append("pause when satisfied, unpause at %s" % cfg.get("unpauseWhenYouHave"))
    elif mode:
        bits.append(mode.lower())
    radius = cfg.get("ingredientSearchRadius")
    if radius is not None:
        bits.append("radius %s" % ("unlimited" if cfg.get("ingredientSearchRadiusUnlimited")
                                   else int(radius)))
    skill = cfg.get("skillRange") or {}
    if skill and (skill.get("min") not in (0, None) or skill.get("max") not in (20, None)):
        bits.append("skill %s-%s" % (skill.get("min"), skill.get("max")))
    if cfg.get("pawnRestriction"):
        bits.append("only %s" % cfg["pawnRestriction"])
    store = cfg.get("storeMode")
    if store:
        bits.append("store %s%s" % (store, (" (%s)" % cfg["storeZone"]) if cfg.get("storeZone") else ""))
    return ("%s%s" % (" " * indent, ", ".join(bits))) if bits else None


def _filter_line(flt, indent=6):
    if not flt:
        return None
    bits = ["%s defs allowed" % flt.get("allowedDefCount")]
    # A key the installed DLL does not send stays absent, and prints nothing.
    for key, noun, no in (("allowsHumanMeat", "human meat", "excluded"),
                          ("allowsInsectMeat", "insect meat", "EXCLUDED"),
                          ("allowsHumanCorpses", "human corpses", "excluded"),
                          ("allowsInsectCorpses", "insect corpses", "EXCLUDED")):
        if flt.get(key) is not None:
            bits.append("%s %s" % (noun, "ALLOWED" if flt[key] else no))
    partly = flt.get("categoriesPartlyAllowed") or []
    if partly:
        bits.append("partly: " + ", ".join(partly[:4]))
    return "%sfilter: %s" % (" " * indent, "; ".join(bits))


def print_bench(b, verbose=True):
    head = "%s %s  [%s]" % (b.get("label") or b.get("defName"), _pos(b), b.get("thingId"))
    if b.get("usableForBills") is False:
        head += "   !! %s" % (b.get("unusableReason") or "not usable for bills")
    elif b.get("usableForBills") is None:
        head += "   ?? usability NOT READ"
    print(head)

    bills = b.get("bills") or []
    if b.get("billStackUnreadable"):
        print("    !! this bench's bill stack could not be READ. Not 'no bills'.")
        return
    if not bills:
        print("    no bills at all -- this bench is idle by configuration, not by chance.")
        return

    for bill in bills:
        flags = [k for k in ("suspended", "paused") if bill.get(k)]
        if _finished(bill):
            flags.append("finished")
        print("    [%d] %s%s   %s" % (bill.get("index"), bill.get("label") or "?",
                                      ("  " + ", ".join(f.upper() for f in flags)) if flags else "",
                                      bill.get("repeatInfo") or ""))
        print(_verdict_line(bill))
        if not verbose:
            continue
        line = _config_line(bill.get("config"))
        if line:
            print(line)
        line = _filter_line(bill.get("filter"))
        if line:
            print(line)
        for ing in bill.get("ingredients") or []:
            print(_ing_line(ing))


def show(r):
    benches = r.get("benches") or []
    att = r.get("attention") or {}
    print("BENCHES: %d in scope, %d bill giver(s) on the map. %d haulable stack(s) scanned."
          % (len(benches), r.get("benchesOnMap", len(benches)), r.get("ingredientsScanned", 0)))
    note = (r.get("notes") or {}).get("ingredientScanFailed")
    if note:
        print("!! THE INGREDIENT SCAN FAILED: %s" % note)
        print("   Every 'available 0' below is UNREAD, not empty.")
    if not benches:
        print("   none. `python bills.py --json` for the raw reply; "
              "pass allFactions if you meant somebody else's worktable.")
        return
    for b in benches:
        print("")
        print_bench(b)
    print("")
    print("attention: %d bench(es) with no bills, %d with no active bill, %d unusable; "
          "%d bill(s) SHORT OF INGREDIENTS, %d finished, %d suspended, %d paused."
          % (att.get("benchesWithNoBills", 0), att.get("benchesWithNoActiveBill", 0),
             att.get("benchesUnusable", 0), att.get("billsShortOfIngredients", 0),
             att.get("finishedBills", 0), att.get("suspendedBills", 0),
             att.get("pausedBills", 0)))


def show_recipes(r):
    rows = r.get("recipes")
    bench = (r.get("benches") or [{}])[0]
    if rows is None:
        print("!! no recipes[] in the reply -- the installed DLL is older than this script.")
        return 1
    print("RECIPES on %s %s  [%s] -- %d available now (%d bill(s) queued, max %s)"
          % (bench.get("label") or bench.get("defName"), _pos(bench), bench.get("thingId"),
             len(rows), bench.get("billCount", 0), bench.get("maxBills")))
    if bench.get("billStackFull"):
        print("!! the stack is FULL; the game's own Add button is greyed out. Delete one first.")
    for row in rows:
        mark = "  " if row.get("ingredientsOnHand") else "!!"
        print("  %s %-34s work %-7s %s"
              % (mark, (row.get("label") or row.get("defName"))[:34],
                 "?" if row.get("workAmount") is None else int(row["workAmount"]),
                 "ingredients on hand" if row.get("ingredientsOnHand")
                 else "; ".join(row.get("blockedBy") or ["short of ingredients"])))
    print("")
    print("An '!!' row can still be ADDED -- it just cannot run yet. "
          "`python bills.py add <bench> \"<recipe>\"` dry-runs it.")
    return 0


def print_write(r):
    """The write reply. Returns the process exit code."""
    if not r.get("success"):
        print("REFUSED: %s" % r.get("error"))
        for c in r.get("candidates") or []:
            print("   candidate  %s" % ", ".join(
                "%s=%s" % (k, v) for k, v in sorted(c.items()) if v is not None))
        return 1

    w = r.get("write") or {}
    if w.get("refused"):
        print("REFUSED at write time: %s" % w.get("reason"))
        print("applied: %s" % r.get("applied"))
        return 1

    before, after = w.get("before") or {}, w.get("after") or {}
    # The bench it RESOLVED, not the string that was typed. 2026-09-07: a
    # listing called a butcher spot "no bills at all" while `add` on the same
    # words read "before: 1 bill(s)", and nothing in either output said which
    # bench each had actually addressed.
    bench = (r.get("benches") or [{}])[0]
    print("action:    %s on %s %s [%s]  (asked for %r)"
          % (w.get("action"), bench.get("label") or bench.get("defName") or "?",
             _pos(bench), bench.get("thingId"),
             r.get("_askedBench") or w.get("requestedBench")))
    if w.get("resolvedRecipe"):
        print("recipe:    %s" % w["resolvedRecipe"])
    print("before:    %d bill(s): %s"
          % (before.get("billCount", 0),
             ", ".join(b.get("label") or "?" for b in (before.get("bills") or [])) or "none"))
    print("after:     %d bill(s): %s   (%s)"
          % (after.get("billCount", 0),
             ", ".join(b.get("label") or "?" for b in (after.get("bills") or [])) or "none",
             "PREDICTED -- nothing was written" if w.get("afterIsPredicted")
             else "read back from the bill stack"))
    for c in w.get("changed") or []:
        print("changed:   %s" % c)
    if not w.get("changed"):
        print("changed:   nothing")
    print("applied:   %s" % r.get("applied"))
    w = r.get("watch") or {}
    if w.get("shown"):
        print("watch:     %s open on the bench, closes in %s s"
              % (w.get("inspectTab") or w.get("mainTab") or "inspect pane",
                 w.get("closesAfterSeconds")))
        if w.get("cameraMoved"):
            try:
                import camlock
                camlock.claim("hands", "watch")
            except Exception:
                pass
    else:
        print("watch:     skipped (%s)" % (w.get("reason") or "not shown"))

    bill = after.get("bill")
    if bill:
        print("")
        print(_verdict_line(bill, indent=0))
        for ing in bill.get("ingredients") or []:
            print(_ing_line(ing, indent=2))

    watch = r.get("watch") or {}
    if not watch.get("shown"):
        print("")
        print("watch:     nothing shown (%s)" % watch.get("reason"))
    return 0


# ------------------------------------------------------------------- main

def _opt(argv, flag, default=None):
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def _int_opt(argv, flag):
    value = _opt(argv, flag)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        raise SystemExit("%s wants a whole number, got %r" % (flag, value))


def _options(argv):
    """Every write option, as the tool's argument names."""
    args = {}
    if "--forever" in argv:
        args["repeatMode"] = "Forever"
    n = _int_opt(argv, "--repeat")
    if n is not None:
        args["repeatCount"] = n
    n = _int_opt(argv, "--target")
    if n is not None:
        args["targetCount"] = n
    n = _int_opt(argv, "--unpause")
    if n is not None:
        args["unpauseWhenYouHave"] = n
    if _opt(argv, "--pause"):
        args["pauseWhenSatisfied"] = _opt(argv, "--pause")
    n = _int_opt(argv, "--radius")
    if n is not None:
        args["ingredientSearchRadius"] = n
    skill = _opt(argv, "--skill")
    if skill:
        bits = skill.split("-")
        try:
            args["skillMin"] = int(bits[0])
            args["skillMax"] = int(bits[1]) if len(bits) > 1 else 20
        except ValueError:
            raise SystemExit("--skill wants MIN or MIN-MAX, got %r" % skill)
    if _opt(argv, "--worker"):
        args["worker"] = _opt(argv, "--worker")
    store = _opt(argv, "--store")
    if store:
        args["storeMode"] = {"best": "bestStockpile", "floor": "dropOnFloor"}.get(store, store)
    if _opt(argv, "--allow"):
        args["allow"] = _opt(argv, "--allow")
    if _opt(argv, "--only"):
        args["only"] = _opt(argv, "--only")
    if _opt(argv, "--disallow"):
        args["disallow"] = _opt(argv, "--disallow")
    if "--suspend" in argv:
        args["suspended"] = "on"
    if "--unsuspend" in argv:
        args["suspended"] = "off"
    return args


# Flags that take a value, so their value is never mistaken for a positional.
VALUED = ("--repeat", "--target", "--unpause", "--pause", "--radius", "--skill",
          "--worker", "--store", "--allow", "--only", "--disallow", "--to")

# Flags that stand alone.
BARE = ("--do", "--no-watch", "--json", "--help", "-h", "--all", "--forever",
        "--suspend", "--unsuspend", "--up", "--down")

# What a turn typed that this tool does not have. 2026-09-07: `--count 1` was
# not rejected -- the flag was skipped as unknown and its VALUE fell through to
# the positionals, so the recipe became `make bolt-action rifle 1` and the
# refusal blamed the bench's recipe list.
MISTAKES = {"--count": "--repeat N (how many times to run it) or --target N "
                       "(run until you have N)",
            "--times": "--repeat N",
            "--repeats": "--repeat N",
            "--n": "--repeat N",
            "--quantity": "--repeat N",
            "--amount": "--repeat N",
            "--priority": "--worker NAME (bills have no priority; move the bill "
                          "with `bills.py move`)",
            "--dry-run": "nothing -- every write here is a dry run until --do",
            "--recipe": "nothing -- the recipe is a positional: "
                        "bills.py add <bench> \"<recipe>\"",
            "--bench": "nothing -- the bench is a positional"}


def check_flags(argv):
    """Refuse an unknown --flag instead of swallowing its value.

    Raises SystemExit naming the flag, what it probably meant, and the flags
    that exist. An argument error is an argument error; it must never arrive
    disguised as a recipe the bench does not offer.
    """
    for a in argv:
        if not a.startswith("-") or a in VALUED or a in BARE:
            continue
        if _CELL.match(a) or a.lstrip("-").isdigit():
            continue                      # a negative number, not a flag
        hint = MISTAKES.get(a.lower())
        raise SystemExit(
            "bills.py: unknown option %s.%s"
            "\n  options: %s"
            "\n  Its value was NOT read as part of the recipe; nothing was sent to the game."
            % (a, ("  Use %s." % hint) if hint else "",
               ", ".join(sorted(VALUED + BARE))))


def _bench_spec(words):
    """The bench address as typed, and the words it consumed.

    A shell splits `120, 140` into two words, so a leading pair that spells a
    cell is rejoined; everything else is one word. Returns just the spec, so
    old callers are unchanged; `_bench_and_rest` is the two-value form."""
    return _bench_and_rest(words)[0]


def _bench_and_rest(words):
    """(bench spec, the words after it). The cell forms `120,140`, `120, 140`
    and `120 ,140` all cost the words they occupy and no more, so the recipe
    that follows a bench is never eaten by the address in front of it."""
    if not words:
        return None, []
    if _cell(words[0]):
        return words[0], list(words[1:])
    for n in (3, 2):
        if len(words) >= n and _cell("".join(words[:n])):
            return "".join(words[:n]), list(words[n:])
    return words[0], list(words[1:])


def _positionals(argv):
    out, skip = [], False
    for i, a in enumerate(argv):
        if skip:
            skip = False
            continue
        if a in VALUED:
            skip = True
            continue
        if a.startswith("--"):
            continue
        out.append(a)
    return out


def main():
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0

    as_json = "--json" in argv
    do = "--do" in argv
    watch = "--no-watch" not in argv
    check_flags(argv)
    positional = _positionals(argv)
    verb = positional[0] if positional else None

    try:
        rim.init()
        if verb == "recipes":
            if len(positional) < 2:
                print("usage: bills.py recipes <bench>")
                return 1
            r = recipes(_bench_spec(positional[1:]))
        elif verb in ("add", "set", "delete", "move"):
            if len(positional) < 2:
                print("usage: bills.py %s <bench> ..." % verb)
                return 1
            args = _options(argv)
            # The bench takes the same forms every verb takes, bare cell
            # included; `_bench_and_rest` says how many words it cost so the
            # recipe behind it is never truncated or padded.
            bench, rest = _bench_and_rest(positional[1:])
            args["bench"] = bench
            if verb == "add":
                if not rest:
                    print('usage: bills.py add <bench> "<recipe>" [options] [--do]')
                    return 1
                args["recipe"] = " ".join(rest)
            else:
                if not rest:
                    print("usage: bills.py %s <bench> <index> [options] [--do]" % verb)
                    return 1
                try:
                    args["index"] = int(rest[0])
                except ValueError:
                    print("index must be a whole number, got %r" % rest[0])
                    return 1
                if verb == "move":
                    to = _int_opt(argv, "--to")
                    if to is not None:
                        args["to"] = to
                    elif "--up" in argv:
                        args["direction"] = "up"
                    elif "--down" in argv:
                        args["direction"] = "down"
                    else:
                        print("bills.py move wants --to N, --up or --down")
                        return 1
            # Through write(), so the CLI and the import API resolve a bench
            # by exactly the same code.
            r = write(verb, args.pop("bench"), do=do, watch=watch, **args)
        else:
            r = state(bench=_bench_spec(positional) if positional else None,
                      all_factions="--all" in argv)
    except Exception as e:
        print("bills.py FAILED -- NO BILL DATA WAS READ.")
        print("%s: %s" % (type(e).__name__, e))
        print(HELP)
        return 1

    if as_json:
        print(json.dumps(r, indent=1))
        return 0

    if verb in ("add", "set", "delete", "move"):
        code = print_write(r)
        if args.get("allow"):
            print("allow is additive; a def already allowed changes nothing. "
                  "Use --only to narrow the bill to a whitelist.")
        if not do and code == 0:
            print("")
            print("DRY RUN -- nothing was written. Add --do to apply it.")
        return code

    if not r.get("success"):
        print("REFUSED: %s" % r.get("error"))
        for c in r.get("candidates") or []:
            print("   candidate  %s" % ", ".join(
                "%s=%s" % (k, v) for k, v in sorted(c.items()) if v is not None))
        return 1

    if verb == "recipes":
        return show_recipes(r)
    show(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
