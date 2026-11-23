"""What the colony HAS, and where. A census, not a tile-by-tile search.

  python inv.py                       # what is OURS, by kind (the default)
  python inv.py steel                 # just what matches "steel"
  python inv.py food                  # every nutrition-giving ingestible,
                                      #   with NUTRITION and days of food
  python inv.py --food                # the same thing
  python inv.py food meat             # ... narrowed to a word as well
  python inv.py food --colonists 5    # ... against a colony size you name
  python inv.py --all-owners          # every item on the map, whoever owns it
  python inv.py --forbidden           # only things nobody is allowed to touch
  python inv.py --corpses             # ONLY corpses, one line per dead body
  python inv.py --near 120 140 30     # only within 30 cells of 120,140
  python inv.py --all                 # plants, filth, rock, buildings AND
                                      #   everyone's things: the whole-map view
  python inv.py --chunks-out          # drop Chunk*, and say how many it dropped
  python inv.py --spawned-only        # skip pawn inventories and containers
  python inv.py --json                # the raw reply
  python inv.py --help                # this text
  python inv.py --cells 100 120 150 170   # the tile-by-tile sweep, per cell

## `food` counts NUTRITION, not units

Units are not food. Raw meat and raw vegetables are **0.05 nutrition each** and
a simple meal is **0.9**, so `76 horse meat` is 3.8 nutrition -- barely two and
a half colonist-meals -- while `20 simple meals` is 18. Seven turns of the
2026-09-06 stream read the unit column as food security and were wrong by a
factor of twenty in the direction that starves a colony.

So `inv.py food` prints a nutrition summary under the census: ready-to-eat,
raw, animal-only feed, and the things it will NOT count -- corpses (nutrition
arrives after butchering, not before) and human/insect meat (edible, and a
mood penalty most colonies cannot afford). Days of food are
`nutrition / (colonists x 1.6)`; 1.6/day is a RimWorld colonist's full intake.

The per-unit numbers are a **vanilla table in this file**, not a read: the
game's `IngestibleProperties.CachedNutrition` is not on `home/list_things`, and
no bridge tool exposes a def's stats. Every summary line says so, and a def the
table does not know is NAMED and left out of the totals rather than guessed at.

## Ownership

`map.listerThings` only ever holds **spawned** things, and a trade caravan's
stock -- the trader's own and everything on the pack animals -- lives unspawned
in `Pawn.inventory.innerContainer`. So the lister alone counts ancient-ruin loot
behind fogged rock, anything outside the home area and anything belonging to
another faction as ours, while missing trader goods entirely. This walks the
holders too -- pawn inventories, carry trackers, corpses, caskets and crates --
and labels every unit. `ownership` defaults to `ours`, and OURS is the headline
number with the rest of the map beside it.

**`ours` is not `tradeable`.** The trade dialog counts only what is in range of
the trade spot or the beacons. Before spending, read the dialog (`trade.py`).

## Corpses are marked, and can be listed on their own

A corpse is an item like any other and aggregates by def, so a row reads
`3  human corpse  Corpse_Human`. Meat-or-skeleton is a per-BODY fact, though --
one grave holds a fresh raider and the field beside it holds last year's bones --
so a corpse row also carries a `[CORPSE ...]` tag counting its bodies by rot
stage, **and every position on that row is labelled with the stage of the
bodies on that cell** -- the row tag alone cannot tell two corpse stacks apart,
and `1 fresh, 1 SKELETON` across two cells is two different decisions.
`--corpses` narrows the census to corpses and names every one: race, name if it
had one, stage, ours or not, forbidden, and where it lies.

`--corpses` reads the WHOLE MAP, not just what is ours (2026-09-04): a raider
dead in the killbox is not "ours" and is very much a corpse on this map, and
scoping the listing to `ours` while the header counted everything is what made
the two disagree. Add `--ours` to narrow it back.

`SKELETON` is RimWorld's `Dessicated` stage and nothing looser. A mechanoid
corpse has no rot component at all, so its stage is `stage unknown` with a
reason, never a fabricated "fresh".

## A word that matches nothing says so, and says what is near

`match` is a case-insensitive SUBSTRING of defName or label, and RimWorld's
labels are singular -- `Component`, not `Components`. So a plural is retried as
its singular (and back), loudly -- `berries` as `berry`, `bushes` as `bush` --
and a word that still matches nothing prints the nearest labels this scope
actually holds rather than a bare `0 kinds`.

A zero in the default scope is then re-asked of the WHOLE map before anything
is said about it, because most of these zeroes are the CATEGORY, not the map: a
built sculpture is a Building and a berry bush is a plant, and neither is
haulable. When the wider scope holds matches, the answer names them and the
flag that reaches them -- `0 haulable matches; 98 plants (berry bush) match
'berry' -- add --all` -- and the `nearest:` guess is not printed at all: it is
for a word this map does not have, and this word it has.

## An unknown flag is refused

A flag this tool does not know is not a filter it forgot to apply, it is a
question it never asked -- and an unfiltered census in the shape of a filtered
one is a wrong answer wearing a right one's clothes. Every flag is in `FLAGS`;
anything else stops the call and names the nearest flag that does exist
(`--radius` is not one: the radius is the third number of `--near`).

## The ids on a row, and when they stop working

`[Thing_Steel407476]` is `GetUniqueLoadID()`, the first form `order.py`
resolves, so the FORM is always accepted. The stack is what expires: hauling a
stack into another ABSORBS and destroys it, and eating destroys it. A
`target_not_found` one call later means the stack merged or went -- re-read, or
use `DefName@x,z`, which order.py, bills.py and buildings.py all take. A
position marked `{held}` is not spawned and is in no id pool at all.

## One row per KIND, not per thing

`Steel x270 in 5 stacks, 0 forbidden, 0 in a stockpile, at 133,143 138,148 ...`
Sample positions are capped per row and each row says how many it did not list:
a filter that hides things has to say so.

## The numbers that are never omitted

Per row: `ours`, `oursUnforbidden`, `forbidden`, `inStockpile`, `inHomeArea`,
`fogged`, `carried`, `traderStock`, `otherFaction`, `inContainer`, `reserved`,
and a `holders` map naming who has the rest. A census that cannot tell "we have
none" from "we have some and nobody may touch it" from "someone else has some"
is a starvation misread waiting to happen.

## The old sweep is still there, and still honest

`--cells x0 z0 x1 z1` runs the tile-by-tile scan. It is slower by two orders of
magnitude and it is the right tool for exactly one job: per-cell questions about
things the thing-lister does not group, like filth and plant cover. It prints
what it skipped, and it knows nothing about ownership. It goes through
`map.block()`, so it gets `home/get_cells_plus` when the companion is there and
the stock tool under a loud DEGRADED line when it is not.
"""
import collections
import difflib
import json
import sys

import rim
import map as rimmap

TOOL = "home/list_things"

# Every flag this tool reads. Anything else starting with `-` stops the call:
# an unknown flag narrows nothing, and a whole-inventory answer to a filtered
# question reads exactly like a filtered one.
FLAGS = ("--help", "-h", "--json", "--cells", "--all", "--all-owners",
         "--everyone", "--ours", "--buildings", "--spawned-only",
         "--forbidden", "--corpses", "--chunks-out", "--food", "--near",
         "--colonists", "--match")

# Flags this tool does not have, and the one it does. Named because the miss is
# specific enough to answer rather than just reject.
FLAG_HINTS = {"--radius": "the radius is the third number of `--near x z r`",
              "--kind": "a bare word is the match: `inv.py steel`, or --match"}

# The old sweep's filters, unchanged. Kept because --cells still uses them, and
# kept LOUD because the pantry full of boulders was invisible for two days
# behind exactly this list.
SKIP_PREFIX = ("Plant_", "Chunk")
SKIP = {"Filth_RubbleRock", "Filth_Dirt", "Filth_Blood", "Filth_Ash",
        "SteamGeyser", "Filth_Vomit", "Filth_AnimalFilth"}

# ------------------------------------------------------------- nutrition ---
#
# VANILLA DEFAULTS, TYPED HERE, NOT READ FROM THE GAME. `home/list_things`
# carries a `food` bool per row (ThingDef.IsNutritionGivingIngestible) and
# nothing else about nutrition; no tool in the bridge or the companion exposes
# a ThingDef's stats, so there is nowhere to read CachedNutrition from. Every
# line that uses these numbers says `(vanilla table, not read from game)`.
#
# A def NOT in here is never guessed at. It is named under `not counted` and
# left out of every total -- a modded meal silently valued at 0.9 would be the
# same class of error this block exists to end.

NUTRITION_PER_UNIT = {
    # Ready to eat. A meal is one meal whatever it is made of.
    "MealNutrientPaste": 0.9,
    "MealSimple": 0.9, "MealSimple_Meat": 0.9, "MealSimple_Veg": 0.9,
    "MealFine": 0.9, "MealFine_Meat": 0.9, "MealFine_Veg": 0.9,
    "MealLavish": 1.0, "MealLavish_Meat": 1.0, "MealLavish_Veg": 1.0,
    "MealSurvivalPack": 0.9,
    # Keeps, travels, no raw-food mood penalty.
    "Pemmican": 0.05,
    # Raw crops.
    "RawPotatoes": 0.05, "RawRice": 0.05, "RawCorn": 0.05, "RawFungus": 0.05,
    "RawBerries": 0.05, "RawAgave": 0.05,
    # Other raw.
    "Milk": 0.05, "InsectJelly": 0.05,
    # Animals only.
    "Hay": 0.05, "Kibble": 0.05,
}

# Prefix rules for the families that are too long to list and too regular to
# get wrong: every Meat_* is 0.05/unit and every Egg* is 0.25.
MEAT_NUTRITION = 0.05
EGG_NUTRITION = 0.25

ANIMAL_FEED = frozenset(("Hay", "Kibble"))

# Edible by a colonist, and a mood hit that costs more than the calories.
# Counted, named, and kept OUT of the days-of-food number.
TABOO = frozenset(("Meat_Human", "Meat_Megaspider", "Meat_Megascarab",
                   "Meat_Spelopede", "Meat_Insect"))

COLONIST_NUTRITION_PER_DAY = 1.6    # RimWorld: a full-fed colonist's intake
TABLE_NOTE = "(vanilla table, not read from game)"


def nutrition_per_unit(def_name):
    """Nutrition for one unit of `def_name`, or None if the table has no entry.

    None is an answer -- `not counted, named below` -- never a zero.
    """
    d = def_name or ""
    if d in NUTRITION_PER_UNIT:
        return NUTRITION_PER_UNIT[d]
    if d.startswith("Corpse_"):
        return None                 # nutrition arrives after butchering
    if d.startswith("Meat_"):
        return MEAT_NUTRITION
    if d.startswith("Egg"):
        return EGG_NUTRITION
    return None


def food_tier(row):
    """Which bucket a food row belongs in. Corpses first: a corpse is a
    nutrition-giving ingestible by the game's own test and is the single
    biggest way to overstate a larder."""
    d = row.get("defName") or ""
    if row.get("corpse") or d.startswith("Corpse_"):
        return "corpse"
    if d in TABOO:
        return "taboo"
    if nutrition_per_unit(d) is None:
        return "unknown"
    if d in ANIMAL_FEED:
        return "animal"
    if d.startswith("Meal"):
        return "ready"
    return "raw"


def food_summary(rows, key="oursUnforbidden"):
    """Nutrition by tier over `rows`, using the count under `key`.

    `oursUnforbidden` is the default on purpose: forbidden food is food nobody
    is allowed to eat, and this number is asked as "will we starve".
    """
    tiers = {t: {"nutrition": 0.0, "units": 0, "rows": []}
             for t in ("ready", "raw", "animal", "taboo", "corpse", "unknown")}
    for row in rows or []:
        if not row.get("food"):
            continue
        tier = food_tier(row)
        units = _n(row, key)
        per = nutrition_per_unit(row.get("defName"))
        bucket = tiers[tier]
        bucket["units"] += units
        if per is not None:
            bucket["nutrition"] += per * units
        bucket["rows"].append((row.get("label") or row.get("defName"), units, per))
    edible = tiers["ready"]["nutrition"] + tiers["raw"]["nutrition"]
    return {"tiers": tiers, "edible": edible,
            "withTaboo": edible + tiers["taboo"]["nutrition"]}


def _food_row_names(bucket, cap=6):
    names = ["%s x%d" % (label, units) for label, units, _ in
             sorted(bucket["rows"], key=lambda t: -t[1])[:cap]]
    more = len(bucket["rows"]) - len(names)
    return ", ".join(names) + (", +%d more" % more if more > 0 else "")


def food_block(r, colonists=None, key="oursUnforbidden"):
    """The lines printed under `inv.py food`. Pure: give it a reply."""
    s = food_summary(r.get("things") or [], key=key)
    t = s["tiers"]
    out = ["", "FOOD -- nutrition, not units. %s" % TABLE_NOTE,
           "   a colonist eats %.1f nutrition/day; a simple meal is 0.9, raw "
           "meat and raw veg are 0.05 EACH." % COLONIST_NUTRITION_PER_DAY]
    for tier, word in (("ready", "ready to eat "),
                       ("raw", "raw          "),
                       ("animal", "animal feed  ")):
        b = t[tier]
        if not b["rows"]:
            continue
        out.append("   %s %8.1f nutrition  (%d unit(s): %s)"
                   % (word, b["nutrition"], b["units"], _food_row_names(b)))
    out.append("   %s %8.1f nutrition  EDIBLE BY COLONISTS (ready + raw; "
               "animal feed excluded)" % ("TOTAL        ", s["edible"]))

    if colonists:
        days = s["edible"] / (colonists * COLONIST_NUTRITION_PER_DAY)
        out.append("   %s %8.1f DAYS for %d colonist(s) at %.1f/day"
                   % ("DAYS OF FOOD ", days, colonists,
                      COLONIST_NUTRITION_PER_DAY))
        if days < 3:
            out.append("   !! under three days. Cook, hunt or harvest now.")
    else:
        out.append("   DAYS OF FOOD  not computed -- colony size unread. "
                   "Pass `--colonists N`.")

    # The three things deliberately kept out of the total, each stated even
    # when it is zero-rowed, because an absent line reads as "there was none".
    b = t["corpse"]
    out.append("   not counted:  %d corpse row(s)%s -- a corpse is food to the "
               "game's own test, but the nutrition arrives after butchering "
               "(`bills.py`), not before."
               % (len(b["rows"]),
                  "" if not b["rows"] else " (%s)" % _food_row_names(b, 4)))
    b = t["taboo"]
    if b["rows"]:
        out.append("   not counted:  %.1f nutrition of human/insect meat (%s) "
                   "-- edible, and a mood penalty most colonies cannot afford."
                   % (b["nutrition"], _food_row_names(b, 4)))
    b = t["unknown"]
    if b["rows"]:
        out.append("   not counted:  %d def(s) this table does not know (%s). "
                   "NAMED, not valued at zero -- add them to "
                   "inv.NUTRITION_PER_UNIT if they matter."
                   % (len(b["rows"]), _food_row_names(b, 6)))
    out.append("   counted from `%s` (what the colony may actually take); "
               "forbidden and other-faction stock are not in these numbers."
               % key)
    return out


def colony_size():
    """Living colonists, or None. One small `home/status` read with every block
    switched off; a failure is None, never a made-up divisor."""
    try:
        import status as colony_status
        # colonists=False zeroes colonistCount in the reply; keep that block on.
        r = colony_status.read(threats=False)
        n = (r.get("counts") or {}).get("colonistCount")
        return n if isinstance(n, int) and n > 0 else None
    except Exception:
        return None


# ------------------------------------------------------------- the census ---

def census(**kw):
    """One call. Returns the raw reply; every filter it applied is in it.

    Defaults to ownership='ours'. Pass ownership='all' for the whole map.
    """
    kw.setdefault("ownership", "ours")
    r = rim.game(TOOL, kw)
    if not isinstance(r, dict) or not r.get("success") or "things" not in r:
        raise rim.BridgeError(
            "%s did not answer (%s). Is the HomeBridge companion DLL installed, "
            "and was RimWorld restarted since? See "
            "rimworld\\companion\\INSTALL.md"
            % (TOOL, (isinstance(r, dict) and r.get("error")) or r))
    return r


def ours(**kw):
    """Only what the colony can actually use. The default."""
    kw["ownership"] = "ours"
    return census(**kw)


def everything(**kw):
    """Every item on the map, whoever owns it. The pre-2026-09-01 view."""
    kw["ownership"] = "all"
    return census(**kw)


def _n(row, key):
    """A count that may be missing because an OLD companion DLL is installed."""
    v = row.get(key)
    return v if isinstance(v, int) else 0


# The bucket counts TAG each unit and therefore overlap: a stimulant in an
# ancient soldier's pocket inside an unopened casket is fogged AND other-faction
# AND carried, all three counting the same one unit. `holders` does not overlap
# -- every not-ours unit lands under exactly one holder -- so that is what gets
# printed per row, and the tag line says out loud that it double-counts.
_TAGS = (("traderStock", "trader"), ("fogged", "fogged"),
         ("otherFaction", "other faction"), ("carried", "carried"),
         ("inContainer", "in containers"))


def _tags(d):
    return ["%d %s" % (_n(d, k), word) for k, word in _TAGS if _n(d, k)]


def _stage_bits(row):
    """A corpse row's bodies counted by rot stage, loudest word for the bones."""
    st = row.get("rotStages") or {}
    bits = []
    for key, word in (("fresh", "fresh"), ("rotting", "ROTTING"),
                      ("dessicated", "SKELETON"), ("unknown", "stage unknown")):
        if _n(st, key):
            bits.append("%d %s" % (_n(st, key), word))
    return bits


def _corpse_tag(row):
    """`[CORPSE 2 fresh, 1 SKELETON]` on an aggregated corpse row.

    On the row, not only in --corpses, because "3 human corpse" and "3 human
    skeleton" are different facts about the colony and the default listing used
    to print the same line for both."""
    if not row.get("corpse"):
        return ""
    bits = _stage_bits(row)
    if row.get("corpsesTruncated"):
        bits.append("+%d not detailed" % _n(row, "corpsesNotListed"))
    return "  [CORPSE %s]" % (", ".join(bits) or "no stage read")


def corpses_block(r):
    """One line per dead body. Only from `--corpses`, which is also the filter
    that makes the census corpses-only -- so the block and the narrowing are the
    same request and cannot disagree about what was counted."""
    rows = [row for row in r["things"] if row.get("corpse")]
    if not rows:
        sk = r.get("skipped") or {}
        removed = sum(v for k, v in sk.items() if isinstance(v, int) and v)
        print("\nCORPSES: none in this census. %s"
              % ("checked -- nothing was filtered out, the map has none."
                 if not removed else
                 "%d thing(s) were removed by filters (%s), so this is NOT "
                 "'no corpses on the map'."
                 % (removed, ", ".join("%s %d" % kv for kv in sorted(sk.items()) if kv[1]))))
        return
    listed = sum(_n(x, "corpseCount") for x in rows)
    counted = r.get("corpseTotal", listed)
    print("\nCORPSES -- %d body/bodies in %d kind(s)" % (counted, len(rows)))
    # The header and the detail below are two different sums, and 2026-09-03
    # caught them disagreeing. They cannot disagree silently again: the header
    # is the census's own corpseTotal (every row it built) and this is the sum
    # over the rows it EMITTED, which the ownership filter can cut. --corpses
    # now asks for the whole map (see main), so a gap here means some other
    # filter did it -- say which, rather than printing two numbers and letting
    # the reader pick.
    if counted != listed:
        scope = (r.get("filters") or {})
        print("   !! %d counted but %d on the rows below. The census dropped "
              "rows the filters excluded (ownership=%s, match=%r, radius=%s). "
              "The missing bodies are real; widen the filter to see them."
              % (counted, listed, scope.get("ownership"), scope.get("match"),
                 scope.get("radius")))
    for row in rows:
        print("   %s  (%s)  %s"
              % ((row.get("label") or "?"), row.get("defName"),
                 ", ".join(_stage_bits(row)) or "no stage read"))
        for c in row.get("corpses") or []:
            stage = c.get("rotStage")
            mark = "SKELETON" if c.get("skeleton") else (stage or "no stage").upper()
            who = c.get("name") or ("unnamed " + (c.get("race") or "?"))
            p = c.get("position") or {}
            bits = []
            bits.append("OURS" if c.get("ours") else "not ours")
            if c.get("wasColonist"):
                bits.append("was ours (%s)" % ("person" if c.get("humanlike") else "animal"))
            if c.get("forbidden"):
                bits.append("FORBIDDEN")
            elif c.get("forbidden") is None:
                bits.append("forbidden unreadable")
            if c.get("holder"):
                bits.append("in %s" % c["holder"])
            print("       %-9s %-28s %-14s %s,%s   %s"
                  % (mark, who[:28], (c.get("race") or "?")[:14],
                     p.get("x", "?"), p.get("z", "?"), "  ".join(bits)))
        if row.get("corpsesTruncated"):
            print("       !! %d more bodies in this row were NOT detailed (cap "
                  "maxCorpsesPerRow=%s). They ARE counted in corpseCount %s."
                  % (_n(row, "corpsesNotListed"),
                     (r.get("filters") or {}).get("maxCorpsesPerRow"),
                     _n(row, "corpseCount")))
    for miss in r.get("corpsesSkipped") or []:
        print("   !! %s x%s: no rot stage -- %s"
              % (miss.get("defName"), miss.get("count"), miss.get("reason")))


def _stage_marks(marks):
    """`SKELETON`, or `2 SKELETON`, or `FRESH, SKELETON` for one cell."""
    counted = collections.Counter(marks)
    return ", ".join("%s%s" % ("" if n == 1 else "%d " % n, word)
                     for word, n in sorted(counted.items()))


def _corpse_stages_by_cell(row):
    """(x, z) -> the rot stages of the bodies on that cell.

    The row's `[CORPSE ...]` tag sums the whole row, so two corpse stacks read
    as one blur. `corpses[]` is on the row in every census, not only under
    --corpses, so each position can name its own bodies.
    """
    out = {}
    for c in row.get("corpses") or []:
        p = c.get("position") or {}
        if p.get("x") is None:
            continue
        stage = c.get("rotStage")
        out.setdefault((p["x"], p["z"]), []).append(
            "SKELETON" if c.get("skeleton") else (stage or "no stage").upper())
    return out


def _pos_bits(row):
    """One row's sample positions: the cell, the instance id, and what it is.

    `[Thing_X]` is `Thing.GetUniqueLoadID()` -- order.py's id form 1, and the
    first form `OrderTool.Resolve` tries, so the form is always accepted. What
    can fail is the thing: order.py resolves against `map.listerThings`, so a
    stack absorbed into another (or eaten) takes its id with it, and a held
    stack is not spawned and is not in that pool at all. Both are marked.
    Corpse rows also carry the per-cell rot stage.
    """
    stages = _corpse_stages_by_cell(row) if row.get("corpse") else {}
    bits = []
    for p in row.get("positions") or []:
        bit = "%s,%s" % (p.get("x"), p.get("z"))
        if p.get("thingId"):
            bit += "[%s]" % p["thingId"]
        # `spawned` is absent on an old DLL; only an explicit False is a claim.
        if p.get("spawned") is False:
            bit += "{held: no id reaches it}"
        if row.get("corpse"):
            here = stages.get((p.get("x"), p.get("z")))
            bit += "{%s}" % (_stage_marks(here) if here else "stage not listed")
        bits.append(bit)
    return " ".join(bits)


# The companion caps `holders` at six per row and reports the overflow as the
# key "(+ others)" whose VALUE is the number of HOLDERS it did not name, not a
# unit count. Sorting the map by value and taking three, as this did until
# 2026-09-07, could therefore print "(+ others) 2" beside real holders as if two
# units were somewhere, AND drop named holders that were holding stock. Every
# named holder is printed now, and the overflow is said in its own words.
_HOLDER_OVERFLOW = "(+ others)"


def _elsewhere(row):
    """One phrase for 'and the rest of it is over there', or '' if it is all ours.

    Names EVERY holder the payload carries. A count that quietly stops naming
    who has the rest is how 428 pemmican becomes unexplainable two turns later.
    """
    gap = _n(row, "total") - _n(row, "ours")
    if gap <= 0:
        return ""
    holders = dict(row.get("holders") or {})
    overflow = holders.pop(_HOLDER_OVERFLOW, None)
    if holders:
        who = ", ".join("%s %d" % (k, v)
                        for k, v in sorted(holders.items(), key=lambda kv: -kv[1]))
        if overflow:
            who += ("; and %s further holder(s) the companion did not name "
                    "(its per-row cap) -- `--json` has the row" % overflow)
    else:
        who = ", ".join(_tags(row)) or "not ours"
    return "  (+%d NOT OURS: %s)" % (gap, who)


# ------------------------------------------------------------- what is out of
#
# 2026-09-05: `inv.py` returned a confident "0 of 0" for fire with a Critical
# Fire alert standing, because the default category is HAULABLE and fire is not
# haulable. A zero that comes from the scope rather than from the map has to say
# so; anything else is the tool answering a question it never asked.
#
# 2026-09-05, the other half: 428 pemmican "vanished" between turns. Nothing
# here can silently drop a stack that is on this map -- spawned things and the
# holders the companion walks (pawn inventories, carry trackers, corpses,
# containers) are all counted, in both ownership modes. What is OUTSIDE the
# census entirely is listed below, and that list is where a stack that was here
# and is now nowhere actually went.

# Words that are not haulable items, so a zero for them means the scope, not the
# map. Not a taxonomy -- the ones a turn has actually typed.
NON_HAULABLE_WORDS = ("fire", "flame", "burning", "smoke", "blight", "roof",
                      "terrain", "alert", "breakdown", "wall", "door")


def _non_haulable_word(wanted):
    w = (wanted or "").strip().lower()
    return bool(w) and any(w == n or n in w for n in NON_HAULABLE_WORDS)


def scope_notes(r, wanted=None):
    """The two lines that keep a zero honest. Printed under the census."""
    f = r.get("filters") or {}
    cat = f.get("category") or "haulable"
    zero = not r.get("defCount")
    if (cat == "haulable" and zero) or _non_haulable_word(wanted):
        print("   SCOPE:   this census counts HAULABLE ITEMS only. Fire, filth, "
              "terrain, plants and buildings are NOT in it, so a 0 here is not "
              "'none on the map'. Fire and every other alert are `python "
              "alerts.py`; `inv.py --all` widens the category; `--buildings` "
              "lists buildings.")
    if zero or f.get("ownership") == "all":
        print("   NOT SEEN: this map only -- spawned things plus the holders the "
              "companion walks (pawn inventories, carry trackers, corpses, "
              "caskets and crates). OUTSIDE it: a caravan that has LEFT the map, "
              "an orbital or passing trader's hold, worn apparel and wielded "
              "weapons, resources already delivered to a blueprint or frame "
              "(`buildings.py --pending` has those), and any other map. A stack "
              "that was counted here and is now absent left with somebody or was "
              "consumed; it was not dropped by this count.")


def show(r):
    mode = (r.get("filters") or {}).get("ownership", "ours")
    # An OLD companion DLL answers without a single ownership field. Printing
    # `ours` as 0 in that case would be the same silent-absence bug this column
    # exists to end, so say it out loud and fall back to the map total.
    legacy = "oursTotal" not in r
    if legacy:
        mode = "all"
        print("!! The installed HomeBridge DLL predates the ownership fields "
              "(2026-09-01). These are WHOLE-MAP totals -- traders', ruins' and "
              "corpses' things included. Rebuild + reinstall + restart RimWorld: "
              r"rimworld\companion\INSTALL.md")
    header = "  OURS" if mode == "ours" else "   MAP"
    print("%6s  %-30s %-22s" % (header, "LABEL", "DEFNAME"))
    for row in r["things"]:
        pos = _pos_bits(row)
        more = " (+%d more)" % row["positionsNotListed"] if row["positionsNotListed"] else ""
        headline = _n(row, "ours") if mode == "ours" else _n(row, "total")
        stacks = _n(row, "oursStacks") if mode == "ours" else _n(row, "stacks")
        flags = ""
        if _n(row, "forbidden"):
            flags += "  !%d FORBIDDEN" % _n(row, "forbidden")
        if _n(row, "inStockpile"):
            flags += "  [%d stored]" % _n(row, "inStockpile")
        if _n(row, "reserved"):
            flags += "  {%d reserved}" % _n(row, "reserved")
        if not legacy:
            flags += _elsewhere(row)
        flags += _corpse_tag(row)
        print("%6d  %-30s %-22s %2d stack%s%s\n            %s%s"
              % (headline, (row["label"] or "")[:30], row["defName"],
                 stacks, "" if stacks == 1 else "s", flags, pos, more))

    sk = r["skipped"]
    print("-- %d kinds shown, %s ours of %s on the map, %d forbidden "
          "(scanned %d things: %s spawned + %s held)"
          % (r["defCount"], r.get("oursTotal", "?"), r.get("mapTotal", "?"),
             r["forbiddenTotal"], r["thingsScanned"],
             r.get("spawnedScanned", "?"), r.get("heldScanned", "?")))
    notmine = _tags({k[:-5]: v for k, v in r.items() if k.endswith("Total")})
    if notmine:
        # These are TAGS, not a partition: one unit can carry several, and
        # `carried` includes our own colonists' packs, which ARE ours.
        print("   where: %s  (tags overlap; carried includes our own pawns)"
              % ", ".join(notmine))
    # Every filter states itself, even when it removed nothing.
    print("   filters: %s" % ", ".join("%s %d" % (k, v) for k, v in sorted(sk.items())))
    f = r["filters"]
    print("   scope:   category=%s ownership=%s includeHeld=%s match=%r%s"
          % (f["category"], f.get("ownership"), f.get("includeHeld"), f["match"],
             "" if not f["radius"] else " within %d of %d,%d" % (f["radius"], f["x"], f["z"])))
    if any(row.get("positions") for row in r["things"]):
        # An id that worked a call ago and fails now is a dead stack, not a
        # wrong form -- name the address that survives instead.
        print("   ids:     [Thing_X] is order.py's id form 1 and is accepted, "
              "but a stack id dies with its stack: hauling one into another "
              "ABSORBS it, eating destroys it. A target_not_found one call "
              "later means the stack merged or went -- re-read, or address the "
              "cell: DefName@x,z (Steel@133,143), which order.py, bills.py and "
              "buildings.py all take. {held} = not spawned, so no id reaches it.")
    # Corpses get a footer line whether or not there are any: 0 stated is an
    # answer, an absent line is not.
    if "corpseTotal" in r:
        print("   corpses: %d counted, %d of them SKELETONS (dessicated)%s"
              % (r["corpseTotal"], r.get("corpseSkeletonTotal", 0),
                 "" if not r.get("corpsesSkipped") else
                 "; %d def(s) could not give a stage -- see --corpses"
                 % len(r["corpsesSkipped"])))
        if (r.get("filters") or {}).get("corpses"):
            print("            CORPSES ONLY (--corpses): %d non-corpse thing(s) "
                  "were skipped. This is not the whole inventory."
                  % _n(sk, "byCorpsesOnly"))
    else:
        print("   corpses: this DLL predates the corpse fields (2026-09-03), so "
              "corpse rows are unmarked. Rebuild and reinstall it.")
    if mode == "ours":
        print("   NOTE:    'ours' is not 'tradeable' -- the trade dialog's own "
              "colony column is what you can offer.")


# ------------------------------------------------------------ a missed word ---

def _variants(word):
    """The word as typed, then the other of singular/plural.

    The bridge matches `match` as a case-insensitive substring of defName or
    label, and RimWorld's labels are singular (`Component`), so `components`
    matches nothing and the honest answer is a useless 0.
    """
    w = (word or "").strip()
    low = w.lower()
    alts = []
    if len(w) > 4 and low.endswith("ies"):
        alts.append(w[:-3] + "y")           # berries -> berry
    if len(w) > 3 and low.endswith("es"):
        alts.append(w[:-2])                 # bushes -> bush
    if len(w) > 2 and low.endswith("s"):
        alts.append(w[:-1])                 # components -> component
    if len(w) > 2 and low.endswith("y"):
        alts.append(w[:-1] + "ies")         # berry -> berries
    elif w and not low.endswith("s"):
        alts.append(w + "s")
    out = [w]
    for alt in alts:
        if alt not in out:
            out.append(alt)
    return out


def _retry_word(kw, r, wanted):
    """A zero that is only a plural gets one more call, out loud."""
    for alt in _variants(wanted)[1:]:
        kw["match"] = alt
        try:
            alt_r = census(**kw)
        except Exception:
            break
        if alt_r.get("defCount"):
            print("!! nothing matched %r; retried as %r and found %d kind(s) "
                  "(RimWorld's labels are singular)."
                  % (wanted, alt, alt_r["defCount"]))
            return alt_r
    kw["match"] = wanted
    return r


# What the WIDE scope holds that the default one structurally cannot. The
# default census counts HAULABLE ITEMS: a built sculpture is a Building, a
# berry bush is a plant, and a zero for either is the category answering, not
# the map.
WIDER_KINDS = (("Plant_", "plants"), ("Chunk", "chunks"),
               ("Filth_", "filth"), ("Corpse_", "corpses"),
               ("Mineable", "rock"))

CATEGORY_WORDS = {"haulable": "HAULABLE ITEMS", "food": "FOOD",
                  "buildings": "BUILDINGS", "all": "everything"}


def _wider_kind(def_name, buildings):
    d = def_name or ""
    for prefix, word in WIDER_KINDS:
        if d.startswith(prefix):
            return word
    return "buildings" if d in buildings else "other non-haulable things"


def wider_look(kw, wanted, category="haulable"):
    """The same word asked of the WHOLE map. -> lines, or [] if it is empty too.

    Two reads on a dead end, and only on a dead end: `category=all` for what is
    there and `category=buildings` to say which of it is a building.
    """
    probe = dict(kw, category="all", ownership="all")
    try:
        wide = census(**probe)
    except Exception:
        return []
    rows = wide.get("things") or []
    if not rows:
        return []
    try:
        buildings = {row.get("defName") for row in
                     (census(**dict(probe, category="buildings")).get("things") or [])}
    except Exception:
        buildings = set()
    counts, names = collections.Counter(), {}
    for row in rows:
        k = _wider_kind(row.get("defName"), buildings)
        counts[k] += _n(row, "total")
        names.setdefault(k, []).append(row.get("label") or row.get("defName") or "?")
    parts = ["%d %s (%s)" % (n, k, ", ".join(sorted(set(names[k]))[:3]))
             for k, n in counts.most_common()]
    return ["   0 %s matches; %s match %r -- add --all"
            % (CATEGORY_WORDS.get(category, category), "; ".join(parts), wanted),
            "   --all widens the category AND the owner; --buildings is "
            "buildings only. Nothing is missing from the map."]


def miss(kw, r, wanted):
    """What a word that matched nothing gets told.

    The wide scope first: when it holds the thing, `nearest:` is a guess at a
    question that already has an answer, and is not printed.
    """
    cat = (r.get("filters") or {}).get("category") or "haulable"
    lines = wider_look(kw, wanted, cat) if cat in ("haulable", "food") else []
    if not lines:
        _suggest(kw, wanted)
        return
    print("   no kind matched %s in this census."
          % " / ".join(repr(v) for v in _variants(wanted)))
    for l in lines:
        print(l)


def _suggest(kw, wanted, limit=8):
    """The nearest labels this scope DOES hold. A bare 0 is a dead end."""
    probe = dict(kw)
    probe.pop("match", None)
    try:
        pool = census(**probe)
    except Exception as e:
        print("   (could not read the nearest labels: %s)" % e)
        return
    names = {}
    for row in pool.get("things") or []:
        for n in (row.get("label"), row.get("defName")):
            if n:
                names.setdefault(n.lower(), n)
    want = (wanted or "").lower()
    keys = sorted(names)
    hits = [k for k in keys if want and (want in k or k in want)]
    hits += [k for k in difflib.get_close_matches(want, keys, n=limit)
             if k not in hits]
    print("   no kind matched %s among the %d kind(s) in this scope."
          % (" / ".join(repr(v) for v in _variants(wanted)),
             pool.get("defCount", 0)))
    print("   nearest: %s"
          % (", ".join(names[k] for k in hits[:limit])
             or "nothing close -- widen the scope (--all-owners, --all) or "
                "drop the filters"))


# --------------------------------------------------- the old tile-by-tile ---

def scan(x0, z0, x1, z1, keep_all=False):
    """The pre-2026-08-31 sweep. Slow, spatial, and still the right tool for
    per-cell questions the thing-lister does not group (filth, plant cover).
    It knows nothing about ownership."""
    found = collections.defaultdict(list)
    for bx in range(x0, x1, 32):
        for bz in range(z0, z1, 32):
            w, h = min(32, x1 - bx), min(32, z1 - bz)
            # `map.block()`, not `rimworld/get_cells_info` directly: it asks the
            # companion's `home/get_cells_plus` first and falls back to the
            # stock tool with a LOUD degraded line. Same answer, 152 KB per
            # 1024 cells instead of 649 KB (measured 2026-08-31) -- this was
            # the last caller in the tree still paying the 4x on the stock
            # tool, and the cell/thing shape is identical either way
            # (`map.normalise_plus`).
            #
            # Narrowed 2026-09-03 to exactly what the two lines below read: the
            # things on the cell, and each thing's defName (always emitted) and
            # label. No terrain, no roof, no walkable/passable, no zone, no
            # areas, no designations -- this is a thing census, not a picture.
            # `sparse` then drops every cell that holds no thing at all, which
            # on an ordinary map is most of them. Fog is NOT content, so an
            # empty fogged cell is dropped too; a fogged cell that holds
            # something is still returned, which matches what this sweep has
            # always counted (it has never filtered on fog).
            #
            # If the companion is missing, block() falls back to the stock tool,
            # which has neither argument: the loop then sees every cell and
            # every field. That is more data, not less, so the census is the
            # same -- it is only the bytes that go back up, and the DEGRADED
            # line above says so.
            for c in rimmap.block(bx, bz, w, h, fields="things",
                                  thing_fields="label", sparse=True):
                for t in c.get("things") or []:
                    d = t.get("defName") or ""
                    if not keep_all and (d.startswith(SKIP_PREFIX) or d in SKIP):
                        continue
                    found[(d, t.get("label"))].append((c["x"], c["z"]))
    return found


def unknown_flags(argv, known=FLAGS):
    """The tokens argv offers as flags that `known` does not hold.

    A number is a value, not a flag: `--cells 100 120 150 170` and a negative
    coordinate both pass through.
    """
    out = []
    for a in argv:
        if not a.startswith("-") or a in known:
            continue
        try:
            float(a)
        except ValueError:
            out.append(a)
    return out


def refuse_unknown(argv, known=FLAGS):
    """True, and said out loud, when argv carries a flag this tool cannot apply."""
    bad = unknown_flags(argv, known)
    if not bad:
        return False
    real = [f for f in known if f != "-h"]
    for a in bad:
        hint = FLAG_HINTS.get(a)
        if not hint:
            near = difflib.get_close_matches(a, real, n=2)
            hint = "nearest: %s" % ", ".join(near) if near else None
        print("inv.py: no such flag %s%s" % (a, " -- %s" % hint if hint else ""))
    print("   nothing was read. Flags: %s" % " ".join(real))
    return True


def main():
    argv = sys.argv[1:]

    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    if refuse_unknown(argv):
        return 1

    rim.init()

    if "--cells" in argv:
        nums = [int(v) for v in argv if v.lstrip("-").isdigit()]
        if len(nums) < 4:
            print("--cells needs x0 z0 x1 z1")
            return 1
        keep_all = "--all" in argv
        f = scan(*nums[:4], keep_all=keep_all)
        for (d, label), pos in sorted(f.items(), key=lambda kv: -len(kv[1])):
            where = " ".join("%d,%d" % p for p in pos[:6])
            if len(pos) > 6:
                where += " (+%d)" % (len(pos) - 6)
            print("%4d  %-44s %-28s %s" % (len(pos), label, d, where))
        if not keep_all:
            print("   (tile sweep: Plant_*, Chunk* and filth were DROPPED -- "
                  "pass --all to keep them)")
        print("   (this is the slow path: ~200x the bytes of the default "
              "census. Use it only for per-cell questions. It has no OURS "
              "column -- it cannot tell whose things these are.)")
        return 0

    kw = {}
    # --all keeps exactly the meaning it always had: everything on the map,
    # every category AND everyone's. It is the old whole-map view, unchanged.
    if "--all" in argv:
        kw["category"] = "all"
        kw["ownership"] = "all"
    if "--all-owners" in argv or "--everyone" in argv:
        kw["ownership"] = "all"
    if "--ours" in argv:
        kw["ownership"] = "ours"
    if "--buildings" in argv:
        kw["category"] = "buildings"
    if "--spawned-only" in argv:
        kw["includeHeld"] = False
    if "--forbidden" in argv:
        kw["forbiddenOnly"] = True
    if "--corpses" in argv:
        kw["corpses"] = True
        # THE 2026-09-03 HEADER-VS-DETAIL BUG, found offline 2026-09-04.
        # `corpses: true` is a category filter and says nothing about
        # ownership, so this call kept census()'s default `ownership="ours"` --
        # and in ListThingsTool.cs the two numbers are scoped differently:
        #   corpseTotal = rows.Values.Sum(r => r.CorpseCount)     (ALL rows)
        #   things      = rows.Values.Where(r => !oursOnly || r.Ours > 0)
        # A raider's body in the killbox, a wild animal dead in a field and
        # anything on a trader's muffalo have Ours == 0, so their rows were
        # dropped from `things` while their bodies stayed in `corpseTotal`.
        # The header said "7 counted" and the block listed two. Every corpse
        # row already carries a per-body `ours` flag precisely because not-ours
        # corpses are expected here, so the scope this filter always meant is
        # the whole map. `--ours` still narrows it deliberately.
        if "--ours" not in argv:
            kw.setdefault("ownership", "all")
    if "--chunks-out" in argv:
        kw["excludeChunks"] = True
    if "--near" in argv:
        i = argv.index("--near")
        tail = argv[i + 1:]
        consumed, nums = 0, []
        if tail:
            comma = tail[0].split(",")
            if len(comma) in (2, 3):
                try:
                    nums, consumed = [int(v.strip()) for v in comma], 1
                except ValueError:
                    nums = []
            if not nums:
                for token in tail[:3]:
                    try:
                        nums.append(int(token))
                        consumed += 1
                    except ValueError:
                        break
        if len(nums) == 2:
            nums.append(12)
        if len(nums) != 3:
            print("--near needs x,z or x,z,radius (commas or spaces)")
            return 1
        kw["x"], kw["z"], kw["radius"] = nums
        del argv[i:i + 1 + consumed]
    colonists = None
    if "--colonists" in argv:
        i = argv.index("--colonists")
        try:
            colonists = int(argv[i + 1])
        except (IndexError, ValueError):
            print("--colonists needs a number")
            return 1
        del argv[i:i + 2]
    if "--match" in argv:
        i = argv.index("--match")
        if i + 1 >= len(argv):
            print("--match needs a word")
            return 1
        kw["match"] = argv[i + 1]
        del argv[i:i + 2]
    words = [a for a in argv if not a.startswith("-")]
    # `food` is a CATEGORY, not a word to match, and it is the same request
    # spelled either way. Any word left over still narrows it: `inv.py food
    # meat` is the food census matched on "meat".
    food_word = bool(words and words[0].lower() == "food")
    food_alias = food_word or "--food" in argv
    if food_alias:
        kw["category"] = "food"
        words = words[1:] if food_word else words
    if words and "match" not in kw:
        kw["match"] = words[0]
    if words and not food_alias and words[0].lower() == "fire":
        # Fire is not haulable, so the default bridge category cannot see it.
        kw["category"] = "all"

    try:
        r = census(**kw)
        wanted = kw.get("match")
        if wanted and not r.get("defCount"):
            r = _retry_word(kw, r, wanted)
        if food_alias and (r.get("filters") or {}).get("category") != "food":
            raise rim.BridgeError(
                "the installed HomeBridge DLL does not support category='food'; "
                "refusing its broader haulable census rather than calling wood food. "
                "Rebuild, reinstall, and restart RimWorld")
        if "--json" in argv:
            print(json.dumps(r, indent=1))
            return 0
        show(r)
        scope_notes(r, wanted)
        if food_alias:
            # Units are not food. This block is the whole reason the alias
            # exists; it is printed after the census, never instead of it.
            for l in food_block(r, colonists if colonists is not None
                                else colony_size()):
                print(l)
        if wanted and not r.get("defCount"):
            miss(kw, r, wanted)
        if "--corpses" in argv:
            corpses_block(r)
    except Exception as e:
        print("inv.py FAILED: %s: %s" % (type(e).__name__, e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
