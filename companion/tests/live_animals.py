"""Live check for the animals{} block on home/list_pawns and the animal writes
on home/pawn_config.

    python rimworld\\companion\\tests\\live_animals.py

Run it against a loaded, PAUSED colony. It reads; it makes exactly ONE real
write, which it undoes; it never saves, never unpauses and never selects
anything. Every other `pawn_config` call it makes is a dry run.

## What it asserts

  1. `animals: true` puts an `animals{}` block on EVERY pawn row -- animals and
     humanlikes both -- and `applies` is true for exactly the pawns the row's
     own `animal` flag calls animals. A missing block on a humanlike and a
     build that cannot answer are the same absent key to a caller, which is the
     bug that cost this colony a colonist; the block is present and says
     `isAnimal: false` instead.
  2. `animals.tame` agrees with the row's `faction`, using a live colonist's
     faction name as the player's. A tame flag that disagrees with the faction
     is the tool guessing.
  3. Every animal with a training tracker reports the SAME set of trainables,
     the base three (Tameness, Obedience, Release) are in it, and
     `trainableCount` equals the length of the list. A short list would be a
     silently dropped column.
  4. Every `learned: true` row also carries `canTrain: true` OR a `reason`.
     A row that says "learned, cannot train, no reason" is unreadable.
  5. `steps` is null exactly when `training.stepsReadable` is false -- so a
     null is always NOT READ and never a zero.
  6. A designation the read reports is confirmed against
     `rimworld/get_cell_info` at the pawn's cell. If that stock payload carries
     no per-cell designations, the check SKIPS and says so rather than passing.
  7. `slaughter: "on"` as a DRY RUN on a tame animal reports `applied: false`
     and the designation is still absent on a fresh read.
  8. `training` on a humanlike is REFUSED with a reason.
  9. An ambiguous `set` name is refused and the refusal names the candidates --
     animals share a race name, so this is the ordinary case for them.
 10. A bogus argument key lands in `unknownArguments[]` on both tools.

## The one real write

One `training` wanted flag on a tame animal, flipped and put straight back,
with a read between each step. It is chosen so RimWorld's own cascade
(`SetWantedRecursive` turns prerequisites on and dependents off) moves nothing
else: the dry run names the cascade in `cascades[]`, and a trainable is only
used when every def in that list is already in the state the cascade would set.
The whole wanted map is snapshotted before and compared after, so a cascade
that moved something anyway is a FAIL and not a shrug.

If the save has no tame animals, the write checks are skipped and the script
prints NO TAME ANIMALS -- write checks vacuous, rather than passing silently.

## What it does NOT do

No slaughter, release-to-wild, tame or hunt designation is ever written for
real. Those are visible colony changes with a job attached; a test that leaves
one behind costs somebody an animal.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "instruments"))

import rim                                                    # noqa: E402

BASE_TRAINABLES = ("Tameness", "Obedience", "Release")

_fails = []
_skips = []
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
    print("  skip %s  -- %s" % (what, why))
    _skips.append("%s (%s)" % (what, why))


def pawns_with(**kw):
    r = rim.game("home/list_pawns", kw)
    if not isinstance(r, dict) or not r.get("success") or "pawns" not in r:
        raise rim.BridgeError("home/list_pawns did not answer: %r" % (r,))
    return r, r["pawns"]


def designation_names(cell_payload):
    """Designation names out of a stock get_cell_info reply, whatever shape it
    uses. Returns None when the payload carries no designations key at all --
    which is a DIFFERENT answer from an empty list and is why this is not [].
    """
    cell = cell_payload.get("cell") if isinstance(cell_payload, dict) else None
    if not isinstance(cell, dict) or "designations" not in cell:
        return None
    out = []
    for d in cell.get("designations") or []:
        if isinstance(d, str):
            out.append(d)
        elif isinstance(d, dict):
            for k in ("defName", "designationDefName", "name", "label", "def"):
                v = d.get(k)
                if isinstance(v, str) and v:
                    out.append(v)
                    break
    return out


def main():
    rim.init()

    # ------------------------------------------------- 1. the block is there
    print("animals:true -- one block on every row")
    r, ps = pawns_with(animals=True)
    check(r.get("success") is True, "call succeeded", r.get("error"))
    check((r.get("filters") or {}).get("animals") is True,
          "filters names animals (a build that cannot answer says so here)")
    check(isinstance(r.get("animalCount"), int) and isinstance(r.get("tameAnimalCount"), int),
          "animalCount and tameAnimalCount are at the top level",
          (r.get("animalCount"), r.get("tameAnimalCount")))

    missing = [p.get("name") for p in ps if not isinstance(p.get("animals"), dict)]
    check(not missing, "every pawn row carries animals{} -- humanlikes too", missing)

    disagree = [p.get("name") for p in ps
                if isinstance(p.get("animals"), dict)
                and bool(p["animals"].get("applies")) != bool(p.get("animal"))]
    check(not disagree, "applies matches the row's own animal flag", disagree)

    humanlikes = [p for p in ps if not p.get("animal")]
    bad = [p.get("name") for p in humanlikes
           if (p.get("animals") or {}).get("isAnimal") is not False]
    check(not bad, "no humanlike claims to be an animal", bad)

    beasts = [p for p in ps if (p.get("animals") or {}).get("applies")]
    check(r.get("animalCount") == len(beasts),
          "animalCount equals the animals in pawns[]",
          (r.get("animalCount"), len(beasts)))
    print("  %d animal(s) of %d pawn(s)" % (len(beasts), len(ps)))

    if not beasts:
        print("\nNO ANIMALS ON THIS MAP -- every animal check below is vacuous.")

    # ------------------------------------------------------ 2. tame/faction
    print("tame agrees with faction")
    player = next((p.get("faction") for p in ps if p.get("isColonist") and p.get("faction")), None)
    if player is None:
        skip("tame vs faction", "no colonist with a faction name to compare against")
    else:
        wrong = [(p.get("name"), p.get("faction"), p["animals"].get("tame"))
                 for p in beasts
                 if bool(p["animals"].get("tame")) != (p.get("faction") == player)]
        check(not wrong, "tame is true for exactly the player-faction animals", wrong)
        wild_wrong = [(p.get("name"), p.get("faction"))
                      for p in beasts
                      if bool(p["animals"].get("wild")) != (p.get("faction") is None)]
        check(not wild_wrong, "wild is true for exactly the faction-less animals", wild_wrong)

    # ---------------------------------------------------------- 3, 4, 5 training
    print("training rows")
    sets = {}
    for p in beasts:
        tr = (p["animals"] or {}).get("training") or {}
        if not tr.get("applies"):
            continue
        names = tuple(t.get("name") for t in tr.get("trainables") or [])
        sets.setdefault(names, []).append(p.get("name"))
        check(tr.get("trainableCount") == len(tr.get("trainables") or []),
              "%s: trainableCount matches the list length" % p.get("name"),
              (tr.get("trainableCount"), len(tr.get("trainables") or [])))

    if not sets:
        skip("training set", "no animal on this map has a training tracker")
    else:
        check(len(sets) == 1,
              "every animal reports the SAME set of trainables",
              {k: v[:3] for k, v in sets.items()})
        names = list(sets.keys())[0]
        for base in BASE_TRAINABLES:
            check(base in names, "the trainable list contains %s" % base, names)

    for p in beasts:
        tr = (p["animals"] or {}).get("training") or {}
        for t in tr.get("trainables") or []:
            if t.get("learned"):
                check(bool(t.get("canTrain")) or bool(t.get("reason")),
                      "%s/%s: learned rows carry canTrain or a reason"
                      % (p.get("name"), t.get("name")), t)
            readable = tr.get("stepsReadable")
            if readable is True:
                check(t.get("steps") is not None,
                      "%s/%s: steps present when stepsReadable"
                      % (p.get("name"), t.get("name")), t.get("steps"))
            elif readable is False:
                check(t.get("steps") is None,
                      "%s/%s: steps NULL (not 0) when the getter was unreachable"
                      % (p.get("name"), t.get("name")), t.get("steps"))

    # -------------------------------------------- 6. designations cross-check
    print("designations cross-checked against the stock cell reader")
    marked = [p for p in beasts
              if any((p["animals"].get("designations") or {}).get(k)
                     for k in ("slaughter", "releaseToWild", "tame", "hunt"))]
    if not marked:
        skip("designation cross-check", "no animal on this map carries one of the four")
    else:
        subject = marked[0]
        pos = subject.get("position") or {}
        cell = rim.game("rimworld/get_cell_info",
                        {"x": pos.get("x"), "z": pos.get("z")}, strict=False)
        names = designation_names(cell)
        if names is None:
            skip("designation cross-check",
                 "rimworld/get_cell_info carries no per-cell designations key in this build")
        else:
            ours = (subject["animals"].get("designations") or {}).get("all") or []
            check(all(d in names for d in ours),
                  "every designation animals{} reports is at the pawn's cell too",
                  (ours, names))

    # -------------------------------------------------- 7. the dry-run write
    tame = [p for p in beasts if p["animals"].get("tame")]
    if not tame:
        print("")
        print("NO TAME ANIMALS -- write checks vacuous")
        print("  (the map has %d animal(s), none in the player faction; nothing below "
              "was exercised)" % len(beasts))
    else:
        subject = tame[0]
        name = subject.get("name")
        tid = subject["animals"].get("thingId")
        print("write subject: %s (%s)" % (name, tid))
        check(bool(tid), "animals{} carries the thingId pawn_config addresses it by")
        target = tid or name

        print("slaughter dry run -- must not mark anything")
        before = bool((subject["animals"].get("designations") or {}).get("slaughter"))
        w = rim.game("home/pawn_config",
                     {"pawn": target, "slaughter": "on", "dryRun": True}, strict=False)
        check(w.get("success") is True, "dry slaughter: call succeeded", w.get("error"))
        check(w.get("dryRun") is True and w.get("applied") is False,
              "dry slaughter: applied is false")
        row = next((f for f in w.get("fields") or [] if f.get("field") == "slaughter"), None)
        check(row is not None, "dry slaughter: the field has a row",
              [f.get("field") for f in w.get("fields") or []])
        if row is not None and not row.get("refused"):
            check(row.get("before") == before,
                  "dry slaughter: before matches the read", (before, row.get("before")))
        _, again = pawns_with(animals=True)
        live = next((p for p in again if (p.get("animals") or {}).get("thingId") == tid), None)
        check(live is not None
              and bool((live["animals"].get("designations") or {}).get("slaughter")) == before,
              "dry slaughter: the designation is UNCHANGED on a fresh read",
              "LEAVE THE COLONY AS FOUND -- unmark it by hand if this failed")

        # ------------------------------------------- 8. the one real write
        print("the one real write: a training box, flipped and put back")
        tr = subject["animals"].get("training") or {}
        wanted_before = dict((t.get("name"), bool(t.get("wanted")))
                             for t in tr.get("trainables") or [])

        pick = None
        for t in tr.get("trainables") or []:
            if not t.get("canTrain") or not t.get("wanted"):
                continue
            plan = rim.game("home/pawn_config",
                            {"pawn": target, "training": "%s=off" % t.get("name"),
                             "dryRun": True}, strict=False)
            prow = next((f for f in plan.get("fields") or []
                         if f.get("field") == "training." + (t.get("name") or "")), None)
            if prow is None or prow.get("refused"):
                continue
            cascade = prow.get("cascades") or []
            # Only usable when the cascade would move nothing: every def it
            # names must already be off, so turning this one off is a clean
            # single-field change that flipping back restores exactly.
            if all(wanted_before.get(c) is False for c in cascade):
                pick = t.get("name")
                break

        if pick is None:
            skip("the real write",
                 "no trainable on %s is both ticked and free of a cascade that would "
                 "move something else -- refusing to leave the colony changed" % name)
        else:
            print("  chosen: %s (currently ticked)" % pick)
            w = rim.game("home/pawn_config",
                         {"pawn": target, "training": "%s=off" % pick, "dryRun": False},
                         strict=False)
            check(w.get("applied") is True and w.get("dryRun") is False,
                  "training: the write applied", w.get("refused"))
            row = next((f for f in w.get("fields") or []
                        if f.get("field") == "training." + pick), None)
            check(row is not None and row.get("after") is False,
                  "training: the after was READ BACK as off", row)

            _, again = pawns_with(animals=True)
            live = next((p for p in again
                         if (p.get("animals") or {}).get("thingId") == tid), None)
            now = dict((t.get("name"), bool(t.get("wanted")))
                       for t in (((live or {}).get("animals") or {}).get("training") or {})
                       .get("trainables") or [])
            check(now.get(pick) is False, "training: list_pawns agrees the write landed", now)

            rim.game("home/pawn_config",
                     {"pawn": target, "training": "%s=on" % pick, "dryRun": False},
                     strict=False)
            _, restored = pawns_with(animals=True)
            live = next((p for p in restored
                         if (p.get("animals") or {}).get("thingId") == tid), None)
            after = dict((t.get("name"), bool(t.get("wanted")))
                         for t in (((live or {}).get("animals") or {}).get("training") or {})
                         .get("trainables") or [])
            check(after == wanted_before,
                  "training: EVERY wanted flag is back where it was",
                  "LEAVE THE COLONY AS FOUND -- fix %s by hand if this failed. before=%r after=%r"
                  % (name, wanted_before, after))

    # ------------------------------------------------- 9. refusals
    print("refusals")
    human = next((p for p in ps if p.get("isColonist") and not p.get("dead")), None)
    if human is None:
        skip("humanlike refusal", "no living colonist to refuse")
    else:
        hid = (human.get("animals") or {}).get("thingId") or human.get("name")
        w = rim.game("home/pawn_config",
                     {"pawn": hid, "training": "Obedience=on", "dryRun": True}, strict=False)
        check(any(f.get("field") == "training" and f.get("refused") and f.get("reason")
                  for f in w.get("fields") or []),
              "training on a humanlike is REFUSED with a reason",
              [(f.get("field"), f.get("refused")) for f in w.get("fields") or []])
        w = rim.game("home/pawn_config",
                     {"pawn": hid, "slaughter": "on", "dryRun": True}, strict=False)
        check(any(f.get("field") == "slaughter" and f.get("refused") and f.get("reason")
                  for f in w.get("fields") or []),
              "slaughter on a humanlike is REFUSED with a reason")

    # An ambiguous name. Animals share a race name, so look for one that is
    # shared and assert the refusal names the candidates.
    labels = {}
    for p in ps:
        labels.setdefault(p.get("name"), []).append(p)
    shared = next((n for n, v in labels.items() if n and len(v) > 1), None)
    if shared is None:
        skip("ambiguous name refusal", "no two pawns on this map share a name")
    else:
        w = rim.game("home/pawn_config",
                     {"pawn": shared, "training": "Obedience=on", "dryRun": True},
                     strict=False)
        err = (w.get("error") or w.get("message") or "")
        check(w.get("success") is False, "an ambiguous name is REFUSED, not resolved", w)
        check("Thing" in err or "ThingID" in err,
              "the refusal names the candidates / the ThingID to use instead", err)

    # a trainable this animal cannot learn, and a nonsense one
    if tame:
        w = rim.game("home/pawn_config",
                     {"pawn": target, "training": "NotATrainableXYZ=on", "dryRun": True},
                     strict=False)
        check(any(f.get("refused") and f.get("reason")
                  for f in w.get("fields") or []),
              "an unknown TrainableDef is REFUSED with the list of real ones")
        w = rim.game("home/pawn_config",
                     {"pawn": target, "slaughter": "sideways", "dryRun": True},
                     strict=False)
        check(any(f.get("field") == "slaughter" and f.get("refused")
                  for f in w.get("fields") or []),
              "a non on/off slaughter value is REFUSED")

    # ------------------------------------------------ 10. unknown arguments
    print("unknown arguments")
    r = rim.game("home/list_pawns", {"animals": True, "bogusKeyXYZ": True})
    check("bogusKeyXYZ" in (r.get("unknownArguments") or []),
          "list_pawns reports a bogus key", r.get("unknownArguments"))
    r = rim.game("home/list_pawns", {"Animals": True})
    check("Animals" in (r.get("unknownArguments") or []),
          "a case-wrong `Animals` is reported, not silently dropped",
          r.get("unknownArguments"))
    who = (tame[0]["animals"].get("thingId") if tame
           else (human or {}).get("name"))
    if who:
        r = rim.game("home/pawn_config",
                     {"pawn": who, "bogusKeyXYZ": True, "dryRun": True}, strict=False)
        check("bogusKeyXYZ" in (r.get("unknownArguments") or []),
              "pawn_config reports a bogus key", r.get("unknownArguments"))

    return summary()


def summary():
    print("")
    for s in _skips:
        print("  skipped: " + s)
    if _fails:
        print("%d of %d checks FAILED:" % (len(_fails), _checks))
        for f in _fails:
            print("  - " + f)
        return 1
    print("all %d checks passed%s"
          % (_checks, (", %d skipped" % len(_skips)) if _skips else ""))
    return 0


# ## The pawns.py commands to run alongside this
#
#   python pawns.py --animals                   every animal, one line each
#   python pawns.py --animals --all             + humanlikes, which say so
#   python pawns.py --animals --needs           hunger next to the training
#   python pawns.py --animals --json            the raw block
#   python pawns.py --animals --name muffalo    one race
#
#   python pawns.py set <thingId> --train Obedience=on          dry run
#   python pawns.py set <thingId> --train NotAThing=on          must be REFUSED
#   python pawns.py set <thingId> --slaughter on                dry run only
#   python pawns.py set <a shared race name> --train Obedience=on
#                                               must be REFUSED with candidates
#   python pawns.py set <a colonist> --train Obedience=on       must be REFUSED
#
# Only add `--do` to a `set` you have read the dry run of, and do not `--do` a
# slaughter or a release unless you actually mean it.

if __name__ == "__main__":
    sys.exit(main())
