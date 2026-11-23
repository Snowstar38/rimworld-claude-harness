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
  python bills.py allow <bench> <index> <Def[,Def]> --do
  python bills.py only <bench> <index> <Def[,Def]> --do
  python bills.py disallow <bench> <index> <Def[,Def]> --do

  python bills.py <bench> --forever --do               # its ONLY bill
  python bills.py "<recipe>" --forever --do            # the one bench queueing it

  The bench may come before the verb instead: `bills.py <bench> set 1 --target
  30 --do` and `bills.py set <bench> 1 --target 30 --do` are the same call. A
  word that is neither is REFUSED naming the verbs; it never becomes a listing.

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
unforbidden, reachable, inside the bill's ingredient search radius of the bench.

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

A stack with no route from the bench's interaction cell is SUBTRACTED and shown
as `{N unreachable}`; a stack somebody has already claimed is shown as
`{N reserved}` and left IN the count, because the claimant is usually fetching
it for this bill. Not modelled: a pawn's own forbidden rules.

Every count is one tick's worth and says its scope -- `map-wide` or `within N
cells`. A stack a pawn is CARRYING is despawned and is in no count at all, so
two readings seconds apart can differ by a haul in flight; the listing header
prints the tick both should be compared at.

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

`add` / `set` / `delete` / `move` / `allow` / `only` / `disallow` are DRY RUNS
until `--do`, the same convention `pawns.py set`, `zones.py` and `research.py
set` use. A real write reports the before, the after READ BACK from the bill
stack, what it ASKED for, and what changed. A write that lands and changes
nothing prints `WROTE NOTHING:` and names the field the bill still disagrees on
-- an unchanged bill sheet is not an answer.

`allow`, `only` and `disallow` are bounded by the recipe's own fixed ingredient
filter. `allow` adds entries; `only` clears existing allowances and makes the
named defs/categories the complete whitelist; `disallow` removes entries. A name
outside it refuses the whole call rather than half-applying a filter. They are
verbs and flags alike: `bills.py <bench> allow 0 Corpse_Human --do` is
`bills.py set <bench> 0 --allow Corpse_Human --do`.

A real write selects the bench and opens its Bills tab while the change lands,
scrolls a real `add` to the new bill at the bottom of the stack, then closes it.
`--no-watch` skips that; the write is identical either way.

## A write with no verb

An option is an intention to change something, so a call that carries one and no
verb is a `set` whose bill has not been named yet -- never a listing. `bills.py
<bench> --forever --do` applies to that bench's ONE bill; a bench with several
REFUSES with them numbered and the exact `bills.py set <bench> <n> ... --do`
line per bill. A positional that is not a bench is looked up as a queued bill
(every word of it must appear in the bill's label, its recipe, or its bench's
name), so `bills.py "steel stonecutter" --forever --do` resolves to the one
bench cutting steel blocks -- or refuses naming every bench that matched.
**A `--do` that reaches no write says `WROTE NOTHING` and why.** A bill sheet
printed instead is the failure a caller cannot see.
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

    Every verb takes the same bench forms, bare cell included: a bench answers
    to the cell its listing row prints, whichever verb is asking.
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


def _scope(ing):
    """What the counts on this row cover. None for a payload that does not say
    -- a scope guessed at is worse than a scope missing."""
    if ing.get("radiusUnlimited"):
        return "map-wide"
    radius = ing.get("searchRadius")
    return None if radius is None else "within %s cells" % radius


def _ing_line(ing, indent=8):
    mark = "   " if ing.get("satisfied") else "!! "
    scope = _scope(ing)
    bits = "%s%s%s  need %s, have %s%s" % (
        " " * indent, mark, _noun(ing),
        _num(ing.get("needed")), _num(ing.get("available")),
        " %s" % scope if scope else "")
    if ing.get("excludedReserved"):
        # Someone got there first. Still counted: they are usually fetching it
        # for this bill.
        bits += "  {%d reserved}" % ing["excludedReserved"]
    # Two independent facts, and they were an `elif`: the scan can confirm N
    # unreachable AND still run out of its call budget before asking about the
    # rest. Suppressing the caveat because something WAS found prints a partial
    # answer as a complete one -- the "0 unreachable means not known, not all
    # reachable" failure `reachabilityChecked` exists to prevent.
    if ing.get("excludedUnreachable"):
        bits += "  {%d unreachable, not counted}" % ing["excludedUnreachable"]
    if ing.get("reachabilityChecked") is False:
        bits += "  {reachability NOT checked}"
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
            # The scope rides with the number, so two readings of one bill can
            # be compared without knowing its radius.
            scope = _scope(ing)
            other.append("missing %s (%s/%s%s)" % (
                noun, ing["available"], ing["needed"],
                " %s" % scope if scope else ""))
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
    tick = r.get("countedAtTick")
    print("BENCHES: %d in scope, %d bill giver(s) on the map. %d haulable stack(s) scanned%s."
          % (len(benches), r.get("benchesOnMap", len(benches)), r.get("ingredientsScanned", 0),
             " at tick %s" % tick if tick is not None else ""))
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


def _nothing_changed(w, applied):
    """The loud line for a write that moved nothing, and why.

    A bill sheet printed unchanged is the failure shape a caller cannot see:
    `optionsNotApplied` is the tool comparing what was asked against the bill
    read back, so a field that did not take is named here rather than inferred.
    """
    predicted = bool(w.get("afterIsPredicted"))
    # A prediction has no read-back, so it can never name a field that did not
    # take -- only that the change is a no-op.
    missed = [] if predicted else [str(m) for m in (w.get("optionsNotApplied") or [])]
    asked = w.get("requestedOptions") or {}
    lead = "NOTHING WOULD CHANGE" if predicted else (
        "WROTE NOTHING" if applied else "NOTHING CHANGED")
    if missed:
        return ("%s: %s. The call reached the bill and the field did not take. "
                "Re-read with `python bills.py <bench>`; if it still disagrees, "
                "the installed DLL is older than this script."
                % (lead, "; ".join(missed)))
    action = w.get("action")
    if action == "move":
        return ("%s: the stack came back in the same order. Either the bill already "
                "sat at that position or the reorder did not take -- this line does "
                "not know which. `python bills.py <bench>` prints the order."
                % lead)
    if action in ("add", "delete"):
        count = (w.get("after") or {}).get("billCount")
        return ("%s: the stack still holds %s bill(s), so the %s did not land. "
                "Re-read with `python bills.py <bench>`."
                % (lead, "?" if count is None else count, action))
    if asked:
        return ("%s: the bill already holds every value asked for (%s), so there "
                "was nothing to change."
                % (lead, ", ".join("%s=%s" % (k, v) for k, v in sorted(asked.items()))))
    return ("%s: no field was asked for and nothing moved. "
            "`python bills.py --help` lists the options." % lead)


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
    asked = w.get("requestedOptions") or {}
    if asked:
        print("asked:     %s" % ", ".join("%s=%s" % (k, v) for k, v in sorted(asked.items())))
    print("before:    %d bill(s): %s"
          % (before.get("billCount", 0),
             ", ".join(b.get("label") or "?" for b in (before.get("bills") or [])) or "none"))
    print("after:     %d bill(s): %s   (%s)"
          % (after.get("billCount", 0),
             ", ".join(b.get("label") or "?" for b in (after.get("bills") or [])) or "none",
             "PREDICTED -- nothing was written" if w.get("afterIsPredicted")
             else "read back from the bill stack"))
    changed = w.get("changed") or []
    for c in changed:
        print("changed:   %s" % c)
    if not changed:
        print("changed:   nothing")
    print("applied:   %s" % r.get("applied"))
    if not changed:
        print(_nothing_changed(w, bool(r.get("applied"))))
    watch = r.get("watch") or {}
    if watch.get("shown"):
        print("watch:     %s open on the bench, closes in %s s"
              % (watch.get("inspectTab") or watch.get("mainTab") or "inspect pane",
                 watch.get("closesAfterSeconds")))
        if watch.get("scrolledToNewBill"):
            print("watch:     Bills tab scrolled to the new bill at the bottom")
        elif watch.get("scrollNote"):
            print("watch:     the tab was NOT scrolled (%s); the new bill is at the "
                  "bottom of the stack" % watch["scrollNote"])
        if watch.get("cameraMoved"):
            try:
                import camlock
                camlock.claim("hands", "watch")
            except Exception:
                pass
    else:
        print("watch:     nothing shown (%s)" % (watch.get("reason") or "not shown"))

    bill = after.get("bill")
    if bill:
        print("")
        print(_verdict_line(bill, indent=0))
        for ing in bill.get("ingredients") or []:
            print(_ing_line(ing, indent=2))
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


VERBS = ("recipes", "add", "set", "delete", "move", "allow", "only", "disallow")

# `allow 0 X` is `set 0 --allow X`: one bill-config write, named the way the
# tab's own filter buttons are.
FILTER_VERBS = ("allow", "only", "disallow")

USAGE = {
    "recipes": 'bills.py recipes <bench>',
    "add": 'bills.py add <bench> "<recipe>" [options] [--do]',
    "set": "bills.py set <bench> <index> [options] [--do]",
    "delete": "bills.py delete <bench> <index> [--do]",
    "move": "bills.py move <bench> <index> --to N|--up|--down [--do]",
    "allow": "bills.py allow <bench> <index> <Def[,Def]> [--do]",
    "only": "bills.py only <bench> <index> <Def[,Def]> [--do]",
    "disallow": "bills.py disallow <bench> <index> <Def[,Def]> [--do]",
}

ORDERS = ("Both orders parse: `bills.py set <bench> 1 --target 30 --do` and "
          "`bills.py <bench> set 1 --target 30 --do`.")


def _verbs_line():
    return "verbs: %s. %s" % (", ".join(sorted(VERBS)), ORDERS)


def _split_verb(positional):
    """(verb, bench, rest, refusal).

    The verb may come before the bench or after it. A first word that is
    neither a verb nor a bench followed by one is REFUSED naming the verbs --
    an unread verb must never fall through to the listing, which prints a bill
    sheet that looks like an answer.
    """
    if not positional:
        return None, None, [], None
    if positional[0] in VERBS:
        bench, rest = _bench_and_rest(positional[1:])
        return positional[0], bench, rest, None
    bench, rest = _bench_and_rest(positional)
    if not rest:
        return None, bench, [], None
    if rest[0] in VERBS:
        return rest[0], bench, list(rest[1:]), None
    return None, bench, rest, (
        "%r is not a bills.py verb, so nothing was read and nothing was written."
        "\n  %s"
        "\n  With no verb, `bills.py <bench>` lists that bench." % (rest[0], _verbs_line()))


def _unconsumed(verb, extra):
    return ("bills.py %s did not use %s, so nothing was read and nothing was written."
            "\n  usage: %s"
            "\n  %s" % (verb, " ".join(repr(w) for w in extra), USAGE[verb], _verbs_line()))


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


# ------------------------------------------- a write that named no bill

# 2026-09-08, Threadneedle: `bills.py <bench> --forever --do` printed the bench's
# bill sheet and wrote nothing, silently -- the sheet looks like an answer, so
# the turn moved on believing the bill was now Forever. The day-68 pass taught
# both orders to parse, but a call with NO verb at all still fell through to the
# listing. An option is an intention to change something; it can only ever end in
# a write or a refusal.

def _quote(word):
    return '"%s"' % word if " " in str(word) else str(word)


def _flag_words(argv):
    """The argv words that are flags or flag VALUES -- the complement of
    `_positionals`, so a suggested command echoes what the caller typed."""
    out, take = [], False
    for a in argv:
        if take:
            out.append(a)
            take = False
        elif a in VALUED:
            out.append(a)
            take = True
        elif a.startswith("--"):
            out.append(a)
    return out


def _set_line(bench, index, argv):
    """The exact line to type. Paste-ready, including the flags already given."""
    return ("python bills.py set %s %s %s"
            % (_quote(bench), index, " ".join(_flag_words(argv)))).rstrip()


def _name(row):
    return "%s %s [%s]" % (row.get("label") or row.get("defName") or "?",
                           _pos(row), row.get("thingId"))


def _bill_line(bill):
    flags = [k.upper() for k in ("suspended", "paused") if bill.get(k)]
    return "[%s] %s%s   %s" % (bill.get("index"), bill.get("label") or "?",
                               ("  " + ", ".join(flags)) if flags else "",
                               bill.get("repeatInfo") or "")


def _refusal(text):
    return {"success": False, "error": text, "candidates": []}


def _words_match(needle, *fields):
    """Every word of `needle` appears somewhere in `fields`, in any order.

    A substring match is too tight for how a bill is actually named out loud:
    "steel stonecutter" is the bench's word and the material's word, and the
    bill's own label is "Make steel blocks". All-words-present matches that and
    still cannot match two unrelated bills by accident -- and where it does
    match two, the caller is shown both rather than one being picked.
    """
    hay = " ".join(str(f or "") for f in fields).lower()
    words = [w for w in re.split(r"\s+", str(needle or "").strip().lower()) if w]
    return bool(words) and all(w in hay for w in words)


def _bills_matching(reply, needle):
    """[(bench row, bill row)] for every queued bill `needle` could name."""
    hits = []
    for b in (reply or {}).get("benches") or []:
        for bill in b.get("bills") or []:
            if _words_match(needle, bill.get("label"), bill.get("recipe"),
                            b.get("label"), b.get("defName")):
                hits.append((b, bill))
    return hits


def _implicit_write(bench, argv):
    """Which bill a verb-less `bills.py <name> --forever --do` means.

    (bench spec to write to, bill index, a note to print, refusal or None).
    Nothing here guesses: one bill is applied to, several are printed numbered
    with the line to type, and none at all says so.
    """
    if not bench:
        return None, None, None, _refusal(
            "an option was given with no bench and no verb, so nothing was written."
            "\n  usage: %s\n  %s" % (USAGE["set"], _verbs_line()))
    if not _options(argv):
        return None, None, None, _refusal(
            "--do was given with no verb and no option, so there was nothing to write."
            "\n  usage: %s\n  %s" % (USAGE["set"], _verbs_line()))

    try:
        r = state(bench=bench)
    except Exception as e:
        # A bench name the tool throws on is still worth trying as a recipe; a
        # bridge that is really down raises again on the whole-map read below,
        # where main()'s handler says NO BILL DATA WAS READ.
        r = {"success": False, "error": "%s: %s" % (type(e).__name__, e)}
    benches = (r.get("benches") or []) if isinstance(r, dict) else []
    if isinstance(r, dict) and r.get("success") and len(benches) == 1:
        row = benches[0]
        if row.get("billStackUnreadable"):
            return None, None, None, _refusal(
                "%s's bill stack could not be READ, so no bill could be named. "
                "That is NOT 'no bills'." % _name(row))
        rows = row.get("bills") or []
        if not rows:
            return None, None, None, _refusal(
                "%s has no bills at all, so there is nothing to set."
                "\n  Add one:  python bills.py add %s \"<recipe>\" %s"
                % (_name(row), _quote(bench), " ".join(_flag_words(argv))))
        if len(rows) == 1:
            index = rows[0].get("index")
            return bench, index, (
                "resolved:  no verb given, and %s has one bill -- this is "
                "`bills.py set %s %s`\n           %s"
                % (_name(row), _quote(bench), index, _bill_line(rows[0]))), None
        lines = ["%s has %d bills, so a call with no verb names none of them. "
                 "Nothing was written. Pick one:" % (_name(row), len(rows))]
        for b in rows:
            lines.append("   %s" % _bill_line(b))
            lines.append("       %s" % _set_line(bench, b.get("index"), argv))
        return None, None, None, _refusal("\n".join(lines))

    # Not a bench. The word is very often the RECIPE -- that is the other half of
    # this bug: `bills.py "<recipe>" --forever --do` was refused because the
    # positional is read as the bench.
    hits = _bills_matching(state(), bench)
    if len(hits) == 1:
        row, bill = hits[0]
        spec = row.get("thingId") or bench
        return spec, bill.get("index"), (
            "resolved:  %r is not a bench; it is a bill on %s -- this is "
            "`bills.py set %s %s`\n           %s"
            % (bench, _name(row), _quote(spec), bill.get("index"),
               _bill_line(bill))), None
    if hits:
        lines = ["%r names %d queued bills, on %d bench(es). Nothing was written. "
                 "Pick one:" % (bench, len(hits),
                                len({id(b) for b, _ in hits}))]
        for row, bill in hits:
            lines.append("   %s  on %s" % (_bill_line(bill), _name(row)))
            lines.append("       %s"
                         % _set_line(row.get("thingId") or bench,
                                     bill.get("index"), argv))
        return None, None, None, _refusal("\n".join(lines))

    why = (r or {}).get("error") if isinstance(r, dict) else None
    return None, None, None, _refusal(
        "%r is neither a bench nor a bill queued on one, so nothing was written.%s"
        "\n  `python bills.py` lists every bench with its bills and ThingIDs."
        "\n  usage: %s" % (bench, ("\n  the bench lookup said: %s" % why) if why
                           else "", USAGE["set"]))


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
    verb, bench, rest, refusal = _split_verb(positional)
    if refusal:
        print("REFUSED: %s" % refusal)
        return 1

    args, note = {}, None
    try:
        rim.init()
        if verb == "recipes":
            if not bench:
                print("usage: %s" % USAGE["recipes"])
                return 1
            if rest:
                print("REFUSED: %s" % _unconsumed(verb, rest))
                return 1
            r = recipes(bench)
        elif verb:
            if not bench:
                print("usage: %s" % USAGE[verb])
                return 1
            args = _options(argv)
            action = "set" if verb in FILTER_VERBS else verb
            if verb == "add":
                if not rest:
                    print("usage: %s" % USAGE["add"])
                    return 1
                args["recipe"] = " ".join(rest)
            else:
                if not rest:
                    print("usage: %s" % USAGE[verb])
                    return 1
                try:
                    args["index"] = int(rest[0])
                except ValueError:
                    print("index must be a whole number, got %r" % rest[0])
                    return 1
                names = rest[1:]
                if verb in FILTER_VERBS:
                    if not names:
                        print("usage: %s" % USAGE[verb])
                        return 1
                    if args.get(verb):
                        print("REFUSED: %s was given twice, as the verb and as --%s. "
                              "Nothing was sent to the game." % (verb, verb))
                        return 1
                    args[verb] = ",".join(n.strip(",") for n in names if n.strip(","))
                elif names:
                    print("REFUSED: %s" % _unconsumed(verb, names))
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
            r = write(action, bench, do=do, watch=watch, **args)
        elif _options(argv) or do:
            # An option -- or a bare --do -- is an intention to CHANGE something.
            # It ends in a write or in a refusal, never in a bill sheet.
            spec, index, note, refused = _implicit_write(bench, argv)
            if refused:
                if as_json:
                    print(json.dumps(refused, indent=1))
                    return 1
                print("%s: %s" % ("WROTE NOTHING" if do else "REFUSED",
                                  refused["error"]))
                return 1
            verb, args = "set", dict(_options(argv), index=index)
            r = write("set", spec, do=do, watch=watch, **args)
        else:
            r = state(bench=bench, all_factions="--all" in argv)
    except Exception as e:
        print("bills.py FAILED -- NO BILL DATA WAS READ.")
        print("%s: %s" % (type(e).__name__, e))
        print(HELP)
        return 1

    if as_json:
        print(json.dumps(r, indent=1))
        # The document is the output; the EXIT CODE is the only thing a script
        # chaining on this can read without parsing. Returning 0 on a refusal
        # made `--json` the one spelling that could not tell a refused write
        # from a done one -- every other path here returns 1.
        return 0 if r.get("success") is not False else 1

    if verb and verb != "recipes":
        if note:
            print(note)
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
