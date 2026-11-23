"""Live check for `home/pawn_config drop=` -- the Gear tab's drop, direct.

    python rimworld\\companion\\tests\\live_drop.py
    python rimworld\\companion\\tests\\live_drop.py --apply "<label>"

Run it against a loaded, PAUSED colony. By default it writes NOTHING: every
call is a dry run or a refusal. It never saves and never unpauses.

## What it asserts

  1. A dry-run drop of a colonist's worn apparel returns one `drop` row:
     `before: "apparel"`, `after: "map"`, `droppedAt` equal to the pawn's own
     cell, `changed: true`, `applied: false`, and `watch.shown: false` with
     reason "dry run" -- a plan opens no menu.
  2. `carried[]` on that row holds exactly `apparelCount + equippedCount +
     inventoryItemCount` items as `home/list_pawns equipment:true` counts them.
     The two readers walk the same three trackers, so they cannot disagree.
  3. The same item addressed by its exact ThingID resolves to the same row.
  4. A label nothing carries is REFUSED with a reason and still carries
     `carried[]`, which is the list a caller needs to name it properly.
  5. A substring two carried items share is REFUSED naming both -- this tool
     writes, so it will not pick one. Skipped when no such substring exists.
  6. A pawn that is not ours is REFUSED. Skipped when the map holds none.
  7. A bogus argument key comes back in `unknownArguments[]`.

## The one real write, and why it is opt-in

`--apply "<label>"` drops that item for real off the same colonist. Nothing in
this stack puts a dropped item back on a pawn: a colonist re-equips a weapon
and re-wears apparel only through their own jobs, which need an unpaused game.
So a real drop is NOT undoable from here, and is never run by default. The
dropped item is left unforbidden and lies on the pawn's own cell.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "instruments"))

import rim                                                    # noqa: E402

TOOL = "home/pawn_config"

_fails = []
_checks = 0


def check(ok, what, detail=""):
    global _checks
    _checks += 1
    if ok:
        print("  ok   %s" % what)
    else:
        print("  FAIL %s%s" % (what, ("  -- " + str(detail)) if detail else ""))
        _fails.append(what)
    return ok


def skip(what, why):
    print("  ---- SKIPPED %s: %s" % (what, why))


def drop(name, item, do=False, **kw):
    args = {"pawn": name, "drop": item, "dryRun": not do}
    args.update(kw)
    return rim.game(TOOL, args, strict=False)


def row_of(r):
    """The `drop` row, or None."""
    for f in (r or {}).get("fields") or []:
        if f.get("field") == "drop":
            return f
    return None


def main():
    apply_label = None
    argv = sys.argv[1:]
    if "--apply" in argv:
        i = argv.index("--apply")
        if i + 1 >= len(argv):
            print("--apply needs the label of the item to really drop.")
            return 1
        apply_label = argv[i + 1]

    rim.init()

    # ------------------------------------------------------------- probe
    r = rim.game("home/list_pawns", {"equipment": True, "settings": True})
    pawns = r.get("pawns") or []
    subject = None
    for p in pawns:
        eq = p.get("equipment") or {}
        if (p.get("isColonist") and not p.get("dead")
                and (eq.get("apparel") or [])):
            subject = p
            break
    if subject is None:
        print("no living colonist on this map is wearing anything -- nothing "
              "to check. This is a colony state, not a failure.")
        return 0

    name = subject["name"]
    eq = subject["equipment"] or {}
    worn = eq["apparel"][0]
    pos = subject.get("position") or {}
    expect_carried = ((eq.get("apparelCount") or 0)
                      + (eq.get("equippedCount") or 0)
                      + (eq.get("inventoryItemCount") or 0))
    print("subject: %s at %s,%s wearing %r"
          % (name, pos.get("x"), pos.get("z"), worn.get("label")))

    # ------------------------------------------- 1 & 2. the dry-run plan
    print("\na dry-run drop of worn apparel")
    r = drop(name, worn["label"])
    check(r.get("success") is True, "the call succeeded", r.get("error"))
    check(r.get("dryRun") is True and r.get("applied") is False,
          "dryRun true, applied false -- nothing was written")
    f = row_of(r)
    if not check(f is not None, "a drop row came back"):
        return summary()
    check(f.get("refused") is False, "not refused", f.get("reason"))
    check(f.get("before") == "apparel", "before is the apparel slot",
          f.get("before"))
    check(f.get("after") == "map", "after is the map", f.get("after"))
    check(f.get("changed") is True, "changed is true")
    at = f.get("droppedAt") or {}
    check(at.get("x") == pos.get("x") and at.get("z") == pos.get("z"),
          "droppedAt is the pawn's own cell", at)
    # list_pawns apparel rows carry no thingId; match on label, then take the
    # ThingID from the tool's own item{} for the exact-ID call below.
    item = f.get("item") or {}
    check(item.get("label") == worn.get("label"),
          "item names the apparel that was asked for", item.get("label"))
    worn_id = item.get("thingId")
    w = r.get("watch") or {}
    check(w.get("shown") is False and w.get("reason") == "dry run",
          "a dry run opens no menu", w)

    carried = f.get("carried") or []
    check(len(carried) == expect_carried,
          "carried[] holds %d item(s), the apparel + equipment + inventory "
          "count list_pawns reads" % expect_carried,
          "carried %d, list_pawns %d" % (len(carried), expect_carried))
    check(any(c.get("label") == worn.get("label") for c in carried),
          "carried[] contains the apparel")

    # ------------------------------------------------- 3. by exact ThingID
    print("\nthe same item by its exact ThingID")
    r2 = drop(name, worn_id or "")
    f2 = row_of(r2)
    check(f2 is not None and f2.get("refused") is False,
          "a ThingID resolves without ambiguity",
          (f2 or {}).get("reason"))
    check(f2 is not None and worn_id is not None
          and (f2.get("item") or {}).get("thingId") == worn_id,
          "it is the same item the label found")

    # --------------------------------------------------- 4. no such label
    print("\na label nothing carries")
    r3 = drop(name, "liveDropNoSuchItemZZZ")
    f3 = row_of(r3)
    check(f3 is not None and f3.get("refused") is True,
          "refused rather than answered")
    check(bool((f3 or {}).get("reason")), "the refusal says why")
    check(len((f3 or {}).get("carried") or []) == expect_carried,
          "a refusal still lists what the pawn does carry")

    # ---------------------------------------------------- 5. an ambiguity
    print("\na substring two carried items share")
    shared = None
    labels = [(c.get("label") or "").lower() for c in carried]
    for a in labels:
        for word in set(a.split()):
            if len(word) < 4:
                continue
            if sum(1 for b in labels if word in b) > 1:
                shared = word
                break
        if shared:
            break
    if shared is None:
        skip("the ambiguity refusal",
             "no word of four or more characters appears in two of this "
             "pawn's item labels")
    else:
        r4 = drop(name, shared)
        f4 = row_of(r4)
        check(f4 is not None and f4.get("refused") is True,
              "%r matches more than one item and is refused" % shared,
              (f4 or {}).get("reason"))
        check("matches" in ((f4 or {}).get("reason") or ""),
              "the reason names the tie")

    # ------------------------------------------------- 6. not our pawn
    print("\na pawn that is not ours")
    outsider = next((p for p in pawns
                     if not p.get("isColonist") and not p.get("dead")
                     and not p.get("isPrisoner")
                     and (p.get("equipment") or {}).get("apparelCount")),
                    None)
    if outsider is None:
        skip("the not-ours refusal", "no non-colonist on this map wears "
                                     "anything")
    else:
        r5 = drop((outsider.get("settings") or {}).get("thingId")
                  or outsider["name"], "a")
        f5 = row_of(r5)
        refused_at_tool = r5.get("success") is False
        check(refused_at_tool or (f5 is not None and f5.get("refused") is True),
              "%s is refused" % outsider.get("name"),
              r5.get("error") or (f5 or {}).get("reason"))

    # -------------------------------------------------- 7. a bogus key
    print("\nan unknown argument key")
    r6 = rim.game(TOOL, {"pawn": name, "drop": worn["label"], "dryRun": True,
                         "liveDropBogusKey": 1}, strict=False)
    check(r6.get("unknownArguments") == ["liveDropBogusKey"],
          "unknownArguments names it", r6.get("unknownArguments"))

    # ------------------------------------------------ the opt-in real write
    if apply_label:
        print("\n--apply: ONE real drop, which nothing here can undo")
        r7 = drop(name, apply_label, do=True)
        f7 = row_of(r7)
        check(r7.get("success") is True, "the call succeeded", r7.get("error"))
        if f7 is not None and f7.get("refused"):
            check(False, "the drop was refused", f7.get("reason"))
        else:
            check(r7.get("applied") is True, "applied is true")
            check((f7 or {}).get("after") == "map",
                  "after is the map, read back from the game",
                  (f7 or {}).get("after"))
            after = rim.game("home/list_pawns",
                             {"nameFilter": name, "equipment": True})
            rows = [p for p in after.get("pawns") or []
                    if p.get("name") == name]
            left = 0
            if rows:
                e = rows[0].get("equipment") or {}
                left = ((e.get("apparelCount") or 0)
                        + (e.get("equippedCount") or 0)
                        + (e.get("inventoryItemCount") or 0))
            check(left == expect_carried - 1,
                  "list_pawns independently sees one item fewer",
                  "was %d, now %d" % (expect_carried, left))

    return summary()


def summary():
    print("")
    if _fails:
        print("%d of %d checks FAILED:" % (len(_fails), _checks))
        for f in _fails:
            print("  - " + f)
        return 1
    print("all %d checks passed" % _checks)
    return 0


# ## The gear.py commands to run alongside this
#
#   python gear.py <name>                     what they wear, wield and carry
#   python gear.py <name> drop <label>        the dry run, with carried[]
#   python gear.py <name> drop <thingId>      the same item, unambiguously
#   python gear.py <name> drop zzz            must be REFUSED, and list the rest
#
# Only add `--do` to a drop you have read the dry run of.

if __name__ == "__main__":
    sys.exit(main())
