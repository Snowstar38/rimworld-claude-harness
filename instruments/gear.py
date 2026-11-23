"""What one pawn wears, wields and carries -- and dropping one piece of it.

  python gear.py Lucas                     # what Lucas has on and in his pack
  python gear.py Lucas --json              # the raw reply

  python gear.py Lucas drop parka          # DRY RUN -- what would fall, where
  python gear.py Lucas drop parka --do     # actually drop it
  python gear.py Lucas drop "Apparel_Parka12345" --do    # by exact ThingID
  python gear.py Lucas drop parka --do --no-watch        # write with no UI

  import gear
  gear.carried("Lucas")        gear.drop("Lucas", "parka", do=False)

## The drop is direct, and works while paused

The game's own Gear-tab drop button queues a job for apparel, so on a paused
game nothing happens and nothing says why. `home/pawn_config drop=` calls the
tracker itself: the item lands on the pawn's own cell immediately, paused or
not, and is left UNFORBIDDEN, so a hauler may pick it up.

## Naming the item

A case-insensitive substring of the label, or the exact ThingID. A substring
matching more than one carried item is REFUSED with every match named -- this
writes, so it will not pick one. Refused too for a pawn that is not ours, a
dead one, locked apparel, a quest lodger's bonded gear, and anything the game
destroys on drop.

## The two listings

The plain view reads `home/list_pawns equipment:true`: worn apparel, equipment,
inventory weapons, and a COUNT of the other things in the pack. A dry-run drop
prints `carried[]`, which is every one of those pack items by name -- so run
the dry run first when you need to see what is in there.
"""
import json
import sys

import rim
from pawns import watch_line

TOOL = "home/pawn_config"


# ------------------------------------------------------------------- reads

def carried(name):
    """The pawn row home/list_pawns returns with equipment:true, or None."""
    r = rim.game("home/list_pawns", {"nameFilter": name, "equipment": True})
    rows = [p for p in r.get("pawns") or [] if p.get("name")]
    exact = [p for p in rows if (p["name"] or "").lower() == name.lower()]
    if len(exact) == 1:
        return exact[0]
    if len(rows) == 1:
        return rows[0]
    return rows or None


def _item(g):
    quality = (" " + g["quality"]) if g.get("quality") else ""
    stuff = (" " + g["stuff"]) if g.get("stuff") else ""
    cond = ""
    if g.get("conditionPct") is not None:
        cond = "  %d%%" % round(100 * g["conditionPct"])
    stack = ("  x%d" % g["stackCount"]) if (g.get("stackCount") or 1) > 1 else ""
    return "%-34s %s%s%s%s%s" % ((g.get("label") or "?")[:34],
                                 g.get("defName") or "?", quality, stuff,
                                 stack, cond)


def show(p):
    """Everything the Gear tab would draw for one pawn."""
    eq = p.get("equipment") or {}
    print("GEAR  %s" % (p.get("name") or "?"))
    print("  wielding: %s" % (eq.get("primaryLabel") or "unarmed"))
    for label, key in (("worn", "apparel"), ("equipment", "equipped"),
                       ("pack weapons", "inventoryWeapons")):
        rows = eq.get(key) or []
        print("  %s (%d)%s" % (label, len(rows), "" if rows else "  --"))
        for g in rows:
            print("    " + _item(g))
    other = (eq.get("inventoryItemCount") or 0) - (eq.get("inventoryWeaponCount") or 0)
    if other > 0:
        print("  pack: %d more item(s), not named here. `gear.py %s drop <label>` "
              "as a dry run lists them all." % (other, p.get("name") or "?"))
    return 0


# ------------------------------------------------------------------ writes

def drop(name, item, do=False, watch=True):
    """Drop one carried item. Dry run unless do=True. Returns the reply."""
    return rim.game(TOOL, {"pawn": name, "drop": item,
                           "dryRun": not do, "watch": watch}, strict=False)


def _carried_list(rows):
    for c in rows or []:
        print("    %-11s %-34s %s" % (c.get("slot") or "?",
                                      (c.get("label") or "?")[:34],
                                      c.get("thingId") or "?"))


def print_drop(r):
    """The plan on a dry run, the before/after on a real write."""
    if not isinstance(r, dict) or not r.get("success"):
        print("REFUSED: %s" % ((r or {}).get("error") or (r or {}).get("message") or r))
        return 1
    who = (r.get("pawn") or {}).get("name") or "?"
    rows = [f for f in r.get("fields") or [] if f.get("field") == "drop"]
    if not rows:
        print("no drop row came back for %s -- nothing was asked for." % who)
        return 1
    f = rows[0]
    if f.get("refused"):
        print("REFUSED: %s" % f.get("reason"))
        if f.get("carried"):
            print("  %s carries:" % who)
            _carried_list(f["carried"])
        return 1

    it = f.get("item") or {}
    at = f.get("droppedAt") or {}
    where = ("%s,%s" % (at.get("x"), at.get("z"))) if at else "?"
    print("DROP %s %s" % (who, "DRY RUN" if r.get("dryRun") else "DONE"))
    print("  %-34s %s -> %s at %s"
          % ((it.get("label") or "?")[:34], f.get("before"), f.get("after"), where))
    if f.get("note"):
        print("  note: " + f["note"])
    if r.get("dryRun") and f.get("carried"):
        print("  %s carries:" % who)
        _carried_list(f["carried"])
    watch_line(r, on=who)
    return 0


# -------------------------------------------------------------------- main

def main():
    argv = sys.argv[1:]
    if not argv or "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0

    as_json = "--json" in argv
    do = "--do" in argv
    watch = "--no-watch" not in argv
    argv = [a for a in argv if a not in ("--json", "--do", "--no-watch")]

    try:
        rim.init()
        if len(argv) >= 2 and argv[1] == "drop":
            if len(argv) < 3:
                print('usage: gear.py <pawn> drop "<label or ThingID>" [--do] [--no-watch]')
                print("       a dry run without --do; run that first and read it.")
                return 1
            r = drop(argv[0], " ".join(argv[2:]).strip(), do=do, watch=watch)
            if as_json:
                print(json.dumps(r, indent=1))
                return 0
            return print_drop(r)

        p = carried(argv[0])
    except Exception as e:
        print("gear.py FAILED -- NO GEAR WAS READ.")
        print("%s: %s" % (type(e).__name__, e))
        return 1

    if as_json:
        print(json.dumps(p, indent=1))
        return 0
    if not p:
        print("no pawn on this map matches %r." % argv[0])
        return 1
    if isinstance(p, list):
        print("%r matches %d pawns: %s. Be exact."
              % (argv[0], len(p), ", ".join(x.get("name") or "?" for x in p)))
        return 1
    return show(p)


if __name__ == "__main__":
    sys.exit(main())
