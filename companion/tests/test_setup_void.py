"""Offline proof that a voided save is skipped by `--newest` and refused by
`--load`. No game, no GABS, no network, and no real save is touched.

    cd rimworld\\instruments
    python ..\\companion\\tests\\test_setup_void.py

It builds a throwaway Saves folder in the system temp directory, fills it with
empty `.rws` files whose modification times are set by hand, points setup.py's
`SAVES` at it for the length of the run, and puts the real one back afterwards
in a `finally` -- so a failure part-way through cannot leave the module aimed at
a fake directory.

What each case proves, and what its failure would mean:

  1  saves_matching sorts by mtime            -- the ordering everything else
                                                 rests on; a failure here means
                                                 "newest" is alphabetical again
  2  void_reason reads the FIRST line only    -- a multi-line sidecar must not
                                                 spill its second paragraph into
                                                 a 14-line status block
  3  an empty sidecar still voids             -- presence is the mark; content
                                                 is only the explanation
  4  --newest skips the voided newest         -- THE case. A failure means the
                                                 repudiated colony gets loaded
  5  the skip is named, with its reason       -- a silent skip is the same bug
                                                 wearing different clothes
  6  --unvoid restores it                     -- the mark is reversible
  7  every match voided -> None, and a note   -- never fall through to a voided
                                                 save because nothing else was
                                                 left
  8  --load of a voided name refuses          -- with the reason in the sentence
  9  --force-void loads it anyway             -- an escape hatch that has to be
                                                 typed
  10 lampblack_saves hides voided saves       -- the offer list never offers
                                                 something --load would refuse
  11 warn_if_stale ignores a voided newer save -- it must not point at a save
                                                 the documented path skips
  12 --void refuses a name with no save       -- a typo would create a mark that
                                                 protects nothing
"""
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "instruments"))

import setup                                                      # noqa: E402

FAILS = []
CHECKS = [0]


def check(ok, what, detail=""):
    CHECKS[0] += 1
    if ok:
        print("  ok   %s" % what)
    else:
        print("  FAIL %s%s" % (what, ("  -- " + str(detail)) if detail else ""))
        FAILS.append(what)
    return ok


def write_save(name, age_seconds):
    p = os.path.join(setup.SAVES, name + ".rws")
    with open(p, "w", encoding="utf-8") as f:
        f.write("<savegame/>\n")
    t = time.time() - age_seconds
    os.utime(p, (t, t))
    return p


def main():
    real = setup.SAVES
    tmp = tempfile.mkdtemp(prefix="void_test_")
    setup.SAVES = tmp
    try:
        run(tmp)
    finally:
        setup.SAVES = real
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%d of %d checks passed." % (CHECKS[0] - len(FAILS), CHECKS[0]))
    for f in FAILS:
        print("  FAIL " + f)
    print("\nThe real Saves folder was never opened: SAVES pointed at %s for "
          "the whole run,\nand is back to %s now." % (tmp, real))
    return 1 if FAILS else 0


def run(tmp):
    # Newest first: day 41, day 40, day 39. Plus one of Sol's, to prove the
    # prefix filter still holds with sidecars in the folder.
    write_save("Lampblack - day 39, freezer built", 3 * 3600)
    write_save("Lampblack - day 40, wall breached", 2 * 3600)
    write_save("Lampblack - day 41, the void one", 1 * 3600)
    write_save("Wayside - day 12", 30 * 60)

    print("\n1-3  the primitives")
    fs = setup.saves_matching("Lampblack")
    check([n for _, n in fs] == ["Lampblack - day 41, the void one",
                                 "Lampblack - day 40, wall breached",
                                 "Lampblack - day 39, freezer built"],
          "saves_matching returns the three Lampblack saves, newest first",
          [n for _, n in fs])

    with open(os.path.join(tmp, "Lampblack - day 41, the void one.void"),
              "w", encoding="utf-8") as f:
        f.write("CHRONICLE calls this run void: Lucas was resurrected by a bug\n"
                "second line that must never reach the status block\n")
    check(setup.void_reason("Lampblack - day 41, the void one")
          == "CHRONICLE calls this run void: Lucas was resurrected by a bug",
          "void_reason returns the FIRST line only",
          setup.void_reason("Lampblack - day 41, the void one"))
    check(setup.void_reason("Lampblack - day 40, wall breached") is None,
          "a save with no sidecar is not void")

    with open(os.path.join(tmp, "Wayside - day 12.void"), "w",
              encoding="utf-8") as f:
        f.write("")
    check(setup.void_reason("Wayside - day 12") == "(no reason given)",
          "an EMPTY sidecar still voids the save, with a stated reason",
          setup.void_reason("Wayside - day 12"))

    print("\n4-5  --newest skips it, and says so")
    notes = {}
    picked = setup.resolve_newest("Lampblack", notes)
    check(picked == "Lampblack - day 40, wall breached",
          "--newest skips the voided newest save and picks the next one",
          picked)
    check("newest of 2 matching" in notes.get("save", ""),
          "the save line counts the LOADABLE matches, not all of them",
          notes.get("save"))
    check("voided" in notes, "a voided line was produced at all")
    check("day 41" in notes.get("voided", "")
          and "Lucas was resurrected" in notes.get("voided", ""),
          "the skip names the save AND its reason -- never silent",
          notes.get("voided"))
    lines = [ln for ln in setup.status_block(dict(notes), set())
             if ln.startswith("voided")]
    check(len(lines) == 1 and len(lines[0]) <= 130,
          "the STATUS block carries exactly one voided line, within budget",
          lines)

    print("\n6  --unvoid puts it back")
    check(setup.unmark_void("Lampblack - day 41, the void one") == 0,
          "--unvoid removes the sidecar and reports 0")
    notes2 = {}
    check(setup.resolve_newest("Lampblack", notes2)
          == "Lampblack - day 41, the void one",
          "--newest picks it again once the mark is gone")
    check("voided" not in notes2, "and says nothing about voided saves",
          notes2.get("voided"))
    check(setup.unmark_void("Lampblack - day 41, the void one") == 1,
          "a second --unvoid reports 1: there was nothing to remove")

    print("\n7  every match voided")
    for n in ("Lampblack - day 39, freezer built",
              "Lampblack - day 40, wall breached",
              "Lampblack - day 41, the void one"):
        setup.mark_void(n, "all of them, for this case")
    notes3 = {}
    check(setup.resolve_newest("Lampblack", notes3) is None,
          "--newest resolves to NOTHING rather than loading a voided save")
    check("VOIDED" in notes3.get("save", ""),
          "and the save line says why there is nothing to load",
          notes3.get("save"))
    for n in ("Lampblack - day 39, freezer built",
              "Lampblack - day 40, wall breached"):
        setup.unmark_void(n)

    print("\n8-9  --load refuses, --force-void does not")
    refusal = setup.refuse_if_void("Lampblack - day 41, the void one", False)
    check(refusal is not None and "REFUSED" in refusal
          and "all of them, for this case" in refusal,
          "--load of a voided save is refused, with the reason in the sentence",
          refusal)
    check(setup.refuse_if_void("Lampblack - day 41, the void one", True) is None,
          "--force-void lets it through")
    check(setup.refuse_if_void("Lampblack - day 40, wall breached", False) is None,
          "a save that is not void is not refused")

    print("\n10-11 the offer list and the staleness warning")
    check(setup.lampblack_saves() == ["Lampblack - day 40, wall breached",
                                      "Lampblack - day 39, freezer built"],
          "lampblack_saves leaves the voided save out of the offer list",
          setup.lampblack_saves())
    notes4 = {}
    setup.warn_if_stale("Lampblack - day 40, wall breached", notes4)
    check("NEWER" not in notes4.get("save", ""),
          "warn_if_stale does not point at a NEWER save that is voided",
          notes4.get("save"))
    notes5 = {}
    setup.unmark_void("Lampblack - day 41, the void one")
    setup.warn_if_stale("Lampblack - day 40, wall breached", notes5)
    check("NEWER" in notes5.get("save", ""),
          "... but still warns once that save is unvoided",
          notes5.get("save"))

    print("\n12  --void on a name with no save")
    check(setup.mark_void("Lampblack - day 99, typo", "nope") == 1,
          "--void refuses a save name that is not on disk")
    check(not os.path.exists(setup.void_path("Lampblack - day 99, typo")),
          "and wrote no sidecar for it")
    check(setup.mark_void("Lampblack - day 39, freezer built", None) == 0
          and setup.void_reason("Lampblack - day 39, freezer built")
          == "(no reason given)",
          "--void with no --reason still marks it, and says the reason is missing")

    left = sorted(f for f in os.listdir(tmp) if f.endswith(".rws"))
    check(len(left) == 4, "all four .rws files are still there, unmodified",
          left)


if __name__ == "__main__":
    sys.exit(main())
