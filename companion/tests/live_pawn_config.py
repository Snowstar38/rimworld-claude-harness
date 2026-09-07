"""Live check for the work/schedule/settings/relations blocks and home/pawn_config.

    python rimworld\\companion\\tests\\live_pawn_config.py

Run it against a loaded, PAUSED colony. It reads; it makes exactly ONE real
write, which it undoes; it never saves and never unpauses. Every other
`pawn_config` call it makes is a dry run.

## What it asserts

  1. `home/list_pawns` answers with each new block alone, and with all nine
     blocks in one call.
  2. On a living colonist none of the four is null, and each carries the
     `applies` flag that separates "does not apply" from "was not looked at".
  3. `filters{}` in the reply names all four, so a caller can tell a build that
     cannot answer from a colonist who has nothing set.
  4. A dry-run `pawn_config` on one colonist, one field at a time, reports a
     `before` EQUAL to what `list_pawns` reads for that same field. If those two
     ever disagree the write tool is planning against a different colony than
     the read tool is showing.
  5. The one real write: `selfTend` off -> on -> off on a single colonist, with
     a read between each step. Reversible, invisible, and the read between is
     what proves the write landed rather than the reply saying so.
  6. A bogus argument key comes back in `unknownArguments[]` on both tools.

## What it does NOT do

No work priority, schedule, area or master is ever written for real. Those are
visible colony changes, and a test that leaves one behind is a test that costs
somebody a day. If a real write of one is wanted, do it by hand, on camera,
having read the dry run first.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "instruments"))

import rim                                                    # noqa: E402

BLOCKS = ("work", "schedule", "settings", "relations")
ALL_NINE = ("health", "needs", "equipment", "bio", "thoughts",
            "work", "schedule", "settings", "relations")

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


def colonists(**kw):
    r = rim.game("home/list_pawns", kw)
    return r, [p for p in r.get("pawns") or []
               if p.get("isColonist") and not p.get("dead")]


def main():
    rim.init()

    # ---------------------------------------------------- 1. block by block
    print("each new block alone")
    for b in BLOCKS:
        r, cs = colonists(**{b: True})
        check(r.get("success") is True, "%s: call succeeded" % b, r.get("error"))
        check((r.get("filters") or {}).get(b) is True,
              "%s: filters names it (a build that cannot answer says so here)" % b)
        check(bool(cs), "%s: at least one living colonist came back" % b,
              "no colonists -- this is a failed read, not an empty colony")
        for p in cs:
            blk = p.get(b)
            check(isinstance(blk, dict),
                  "%s: %s got a block, never null" % (b, p.get("name")), blk)
            if isinstance(blk, dict):
                check("applies" in blk,
                      "%s: %s block says whether it applies" % (b, p.get("name")))

    # ------------------------------------------------- 2. all nine together
    print("all nine blocks in one call")
    kw = dict((b, True) for b in ALL_NINE)
    r, cs = colonists(**kw)
    check(r.get("success") is True, "nine blocks: call succeeded", r.get("error"))
    for b in ALL_NINE:
        check((r.get("filters") or {}).get(b) is True, "nine blocks: filters.%s" % b)
    for p in cs:
        missing = [b for b in ALL_NINE if not isinstance(p.get(b), dict)]
        check(not missing, "nine blocks: %s carries all nine" % p.get("name"), missing)
    check(isinstance(r.get("pawnConfigOptions"), dict),
          "settings:true adds pawnConfigOptions at the top level")

    if not cs:
        print("\nno colonists -- stopping before the write checks.")
        return summary()

    subject = cs[0]
    name = subject.get("name")
    tid = (subject.get("settings") or {}).get("thingId")
    print("subject: %s (%s)" % (name, tid))
    check(bool(tid), "settings carries a thingId for pawn_config to address")

    # ------------------------------------------ 3. dry runs, field by field
    #
    # Each one asserts `before` == what list_pawns just read. The requested
    # values are deliberately either no-ops or things the tool should refuse;
    # nothing here is applied.
    print("pawn_config dry runs -- before must match the read")
    st = subject.get("settings") or {}
    wk = subject.get("work") or {}
    sch = subject.get("schedule") or {}

    first_work = next((t for t in (wk.get("types") or [])
                       if not t.get("disabled") and t.get("priority") is not None), None)

    cases = []
    if first_work:
        cases.append(("work.%s" % first_work["name"],
                      {"work": "%s=%d" % (first_work["name"], first_work["priority"])},
                      first_work["priority"]))
    if sch.get("hours"):
        cases.append(("schedule", {"schedule": sch["hours"]}, sch["hours"]))
    for field, key in (("medCare", "medCare"),
                       ("hostilityResponse", "hostilityResponse")):
        if st.get(key):
            cases.append((field, {key: st[key]}, st[key]))
    for field in ("selfTend", "followDrafted", "followFieldwork"):
        if st.get(field) is not None:
            cases.append((field, {field: "on" if st[field] else "off"}, st[field]))
    cases.append(("allowedArea",
                  {"allowedArea": st.get("allowedArea") or "none"},
                  st.get("allowedArea")))
    cases.append(("master", {"master": st.get("master") or "none"}, st.get("master")))

    for field, args, expected in cases:
        a = dict(args)
        a["pawn"] = tid or name
        a["dryRun"] = True
        r = rim.game("home/pawn_config", a, strict=False)
        check(r.get("success") is True, "dry %s: call succeeded" % field, r.get("error"))
        check(r.get("dryRun") is True and r.get("applied") is False,
              "dry %s: nothing was applied" % field)
        row = next((f for f in r.get("fields") or [] if f.get("field") == field), None)
        check(row is not None, "dry %s: the field has a row" % field,
              [f.get("field") for f in r.get("fields") or []])
        if row is not None:
            check(row.get("before") == expected,
                  "dry %s: before matches the read" % field,
                  "read %r, pawn_config %r" % (expected, row.get("before")))

    # A refusal must be a row with a reason, never a silent skip.
    r = rim.game("home/pawn_config",
                 {"pawn": tid or name, "medCare": "NotACareLevel", "dryRun": True},
                 strict=False)
    check(any(f.get("field") == "medCare" and f.get("refused") and f.get("reason")
              for f in r.get("fields") or []),
          "a bad enum value is REFUSED with a reason, not skipped")

    # ------------------------------------------------ 4. the one real write
    #
    # selfTend off -> on -> off, reading between. Reversible, invisible on
    # camera, and the only thing in this file that touches the colony.
    print("the one real write: selfTend, toggled and put back")
    start = st.get("selfTend")
    if start is None:
        check(False, "selfTend was not readable -- skipping the real write")
    else:
        flip = "off" if start else "on"
        back = "on" if start else "off"

        r = rim.game("home/pawn_config",
                     {"pawn": tid or name, "selfTend": flip, "dryRun": False},
                     strict=False)
        check(r.get("applied") is True and r.get("dryRun") is False,
              "selfTend: the write applied")
        row = next((f for f in r.get("fields") or [] if f.get("field") == "selfTend"), None)
        check(row is not None and row.get("after") == (not start),
              "selfTend: the after was read back as the new value", row)

        _, again = colonists(settings=True)
        live = next((p for p in again if p.get("name") == name), None)
        check(live is not None
              and (live.get("settings") or {}).get("selfTend") == (not start),
              "selfTend: list_pawns agrees the write landed")

        r = rim.game("home/pawn_config",
                     {"pawn": tid or name, "selfTend": back, "dryRun": False},
                     strict=False)
        _, restored = colonists(settings=True)
        live = next((p for p in restored if p.get("name") == name), None)
        check(live is not None
              and (live.get("settings") or {}).get("selfTend") == start,
              "selfTend: PUT BACK to what it was (%s)" % start,
              "LEAVE THE COLONY AS FOUND -- set it by hand if this failed")

    # ----------------------------------------------------- 5. a bogus key
    print("unknown arguments")
    r = rim.game("home/list_pawns", {"work": True, "bogusKeyXYZ": True})
    check("bogusKeyXYZ" in (r.get("unknownArguments") or []),
          "list_pawns reports a bogus key", r.get("unknownArguments"))
    r = rim.game("home/pawn_config",
                 {"pawn": tid or name, "bogusKeyXYZ": True, "dryRun": True},
                 strict=False)
    check("bogusKeyXYZ" in (r.get("unknownArguments") or []),
          "pawn_config reports a bogus key", r.get("unknownArguments"))
    # A misspelled dryRun on a write tool is the difference between a plan and
    # a changed colonist, so it must be LOUD -- and it must not have written.
    r = rim.game("home/pawn_config",
                 {"pawn": tid or name, "selfTend": "on", "DryRun": False},
                 strict=False)
    check("DryRun" in (r.get("unknownArguments") or []),
          "pawn_config reports a case-wrong dryRun", r.get("unknownArguments"))
    check(r.get("dryRun") is True and r.get("applied") is False,
          "a case-wrong dryRun still defaults to a DRY RUN -- nothing written")

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


# ## The pawns.py commands to run alongside this
#
#   python pawns.py --work                      the Work tab, every colonist
#   python pawns.py --schedule                  24 hours each, with the key
#   python pawns.py --settings                  the assign-tab row, and thingIds
#   python pawns.py --relations                 family, partners, opinions
#   python pawns.py --work --schedule --settings --relations
#   python pawns.py --roster                    unchanged: the four old blocks
#   python pawns.py --work --all                animals get "does not apply"
#   python pawns.py --work --json               the raw block
#
#   python pawns.py set <name> --work Cooking=1               dry run
#   python pawns.py set <name> --schedule <the string --schedule printed>
#   python pawns.py set <name> --selftend on --medcare Best --area none
#   python pawns.py set <name> --work NotAJob=1               must be REFUSED
#   python pawns.py set <name> --schedule SSS                 must be REFUSED
#   python pawns.py set <name>                                must say so
#
# Only add `--do` to a `set` you have read the dry run of.

if __name__ == "__main__":
    sys.exit(main())
