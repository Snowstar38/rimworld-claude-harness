"""Live load test for `home/status` and `status.py`.

    cd rimworld\\instruments
    python ..\\companion\\tests\\live_status.py

Run it against a loaded, PAUSED colony. `home/status` is READ-ONLY -- it never
writes, never selects, never opens a tab and never moves the clock -- so this
script is read-only end to end. There is no write test to skip and no state to
put back.

Nothing below has ever been run against a live game. Every assertion is a
PROMISE being checked for the first time; a failure is information.

## What it checks, and what a failure would mean

  1  the default call answers, and every Always key is present
        FAIL = the tool is not registered, or a block was dropped. Check
        `rimbridge/get_bridge_status` and that RimWorld was restarted after
        the DLL was copied.
  2  the default payload is under 10 KB (the size is printed either way)
        FAIL = the between-turns read costs more than the six calls it
        replaces, which is the whole point of it.
  A1 time.ticksGame equals rimworld/get_game_info's
        FAIL = the clock block is reading a different number from the one the
        stock tool reports, so nothing downstream can trust it.
  A2 letters[] ids are exactly rimworld/list_letters' set
        FAIL = the letter stack is being read from somewhere else, or filtered.
        Ids must match because they are what open_letter takes.
  A3 alerts[] labels are exactly rimworld/list_alerts' set
        FAIL = an alert is being missed or invented. Both directions are
        reported, because a MISSING alert is the dangerous one.
  A4 colonists[] names are exactly rimworld/list_colonists' current-map set
        FAIL = the free-colonist filter disagrees with the stock tool. Note the
        stock tool lists ALL maps unless currentMapOnly is set, and prisoners
        and slaves are excluded here by IsFreeColonist -- a mismatch is
        reported with both lists so the reason is visible.
  I1 {colonists:false, threats:false} is SMALLER than the default
        FAIL = an opt-out is not opting anything out.
  I2 every Always block is still present with colonists:false, and
     colonists[] is empty while blocks.colonists is false
        FAIL = an omitted block would be indistinguishable from an empty one.
  I3 counts{} agrees with the arrays it counts
        FAIL = the summary a --brief caller reads disagrees with the payload.
  I4 explanations:true puts a non-null explanation on at least one alert
     (skipped, loudly, when no alert is active)
        FAIL = the opt-in was asked for and not honoured.
  I5 colonistDetail:true adds needs{} and hediffs[] to every colonist row, and
     the plain call adds neither
        FAIL = a block leaking into the default payload, or missing from the
        detailed one.
  I6 every alert row with targets[] has a name on every target
        FAIL = a culprit list that cannot name anybody is worse than an empty
        one: alerts.py reads names ONLY from targets[].
  I7 ui.modalOpen agrees with a hand-scan of ui.windows[]
        FAIL = the modal test is not reproducible from the payload, so a caller
        cannot check it. This is the `move.blocking_window()` blind spot.
  3  a bogus argument key lands in unknownArguments[]
        FAIL = a mis-cased `colonists` would be silently dropped and the caller
        would get a payload it did not ask for.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "instruments"))

import rim                                                    # noqa: E402

TOOL = "home/status"
FAILURES = []
NEEDS_HUMAN = []
CHECKS = [0]

# Every key the tool annotates Always = true.
ALWAYS = ("status", "time", "letters", "messages", "alerts", "colonists",
          "threats", "ui", "counts", "blocks", "skipped", "unknownArguments")

# The same prefixes StatusTool.cs treats as never-blocking, so I7 reproduces
# the tool's own rule rather than a different one.
NON_BLOCKING = ("Verse.ImmediateWindow", "RimWorld.MainTabWindow",
                "LudeonTK.EditWindow", "Verse.EditWindow")


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


def size_of(payload):
    """Bytes of the JSON that actually crossed the wire, near enough."""
    return len(json.dumps(payload, separators=(",", ":")))


def call(args, label):
    r = rim.game(TOOL, args, strict=False)
    if not isinstance(r, dict):
        check(False, label, "reply was %r, not a dict" % type(r).__name__)
        return None
    if r.get("success") is not True:
        check(False, label, "success was %r: %s" % (r.get("success"), r.get("error")))
        return None
    return r


def main():
    rim.init()

    print("\n1  the default call, and every Always key")
    default = call({}, "default call answers")
    if default is None:
        return report()
    missing = [k for k in ALWAYS if k not in default]
    check(not missing, "every Always key present", "missing: %s" % missing)
    check(default.get("unknownArguments") == [],
          "a clean call reports no unknown arguments",
          repr(default.get("unknownArguments")))

    print("\n2  payload size")
    size = size_of(default)
    print("     default payload: %d bytes (%.1f KB)" % (size, size / 1024.0))
    check(size < 10240, "default payload under 10 KB", "%d bytes" % size)

    print("\nA1 time.ticksGame against rimworld/get_game_info")
    info = rim.game("rimworld/get_game_info", {}, strict=False)
    ours = (default.get("time") or {}).get("ticksGame")
    theirs = info.get("ticksGame", info.get("currentGameTick"))
    if theirs is None:
        human("rimworld/get_game_info carried no tick field this build knows; "
              "compared nothing. Keys were: %s" % sorted(info.keys()))
    else:
        # The game is paused, so the two reads are the same instant. On a
        # RUNNING game a one-tick drift would be legal and this would be wrong.
        check(ours == theirs, "ticksGame matches get_game_info",
              "home/status %r vs %r" % (ours, theirs))

    print("\nA2 letters[] ids against rimworld/list_letters")
    stock = rim.game("rimworld/list_letters", {"limit": 200}, strict=False)
    stock_ids = {l.get("id") for l in (stock.get("letters") or [])}
    our_ids = {l.get("id") for l in (default.get("letters") or [])}
    check(stock_ids == our_ids, "letter id sets are equal",
          "only stock: %s | only ours: %s"
          % (sorted(stock_ids - our_ids), sorted(our_ids - stock_ids)))

    print("\nA3 alerts[] labels against rimworld/list_alerts")
    stock = rim.game("rimworld/list_alerts", {"limit": 200}, strict=False)
    stock_labels = {a.get("label") for a in (stock.get("alerts") or [])}
    our_labels = {a.get("label") for a in (default.get("alerts") or [])}
    check(stock_labels == our_labels, "alert label sets are equal",
          "only stock (MISSED BY US): %s | only ours: %s"
          % (sorted(stock_labels - our_labels), sorted(our_labels - stock_labels)))

    print("\nA4 colonists[] names against rimworld/list_colonists")
    stock = rim.game("rimworld/list_colonists", {"currentMapOnly": True},
                     strict=False)
    stock_names = {c.get("name") for c in (stock.get("colonists") or [])}
    our_names = {c.get("name") for c in (default.get("colonists") or [])}
    check(stock_names == our_names, "colonist name sets are equal",
          "only stock: %s | only ours: %s  (prisoners and slaves are excluded "
          "here by Pawn.IsFreeColonist)"
          % (sorted(stock_names - our_names), sorted(our_names - stock_names)))

    print("\nI1/I2 the opt-outs")
    lean = call({"colonists": False, "threats": False}, "opt-out call answers")
    if lean is not None:
        lean_size = size_of(lean)
        print("     lean payload:    %d bytes (%.1f KB)" % (lean_size, lean_size / 1024.0))
        check(lean_size < size, "colonists:false, threats:false is smaller",
              "%d vs %d bytes" % (lean_size, size))
        missing = [k for k in ALWAYS if k not in lean]
        check(not missing, "every Always key still present when opted out",
              "missing: %s" % missing)
        check(lean.get("colonists") == [], "colonists[] is an empty array, not absent",
              repr(lean.get("colonists")))
        check((lean.get("blocks") or {}).get("colonists") is False,
              "blocks.colonists says the empty array was not asked for",
              repr(lean.get("blocks")))
        threats = lean.get("threats")
        check(isinstance(threats, dict) and threats.get("hostileCount") == 0,
              "threats{} is an empty object, not null", repr(threats))

    print("\nI3 counts{} against the arrays")
    c = default.get("counts") or {}
    check(c.get("letterCount") == len(default.get("letters") or []),
          "counts.letterCount matches letters[]")
    check(c.get("messageCount") == len(default.get("messages") or []),
          "counts.messageCount matches messages[]")
    check(c.get("alertCount") == len(default.get("alerts") or []),
          "counts.alertCount matches alerts[]")
    check(c.get("colonistCount") == len(default.get("colonists") or []),
          "counts.colonistCount matches colonists[]")
    check(c.get("hostileCount") == (default.get("threats") or {}).get("hostileCount"),
          "counts.hostileCount matches threats.hostileCount")

    print("\nI4 explanations:true")
    if not (default.get("alerts") or []):
        human("no alert is active, so the explanation opt-in could not be "
              "exercised. Re-run when something is alerting.")
    else:
        explained = call({"explanations": True}, "explanations:true answers")
        if explained is not None:
            rows = explained.get("alerts") or []
            check(any(a.get("explanation") for a in rows),
                  "at least one alert carries a non-null explanation",
                  "labels: %s" % [a.get("label") for a in rows])
            check(all(a.get("explanation") is None
                      for a in (default.get("alerts") or [])),
                  "and the default call carries none")
            print("     with explanations: %d bytes" % size_of(explained))

    print("\nI5 colonistDetail:true")
    if not (default.get("colonists") or []):
        human("no colonist on this map, so colonistDetail could not be "
              "exercised.")
    else:
        check(all("needs" not in row and "hediffs" not in row
                  for row in default["colonists"]),
              "the default colonist row carries neither needs{} nor hediffs[]")
        detailed = call({"colonistDetail": True}, "colonistDetail:true answers")
        if detailed is not None:
            rows = detailed.get("colonists") or []
            check(rows and all(isinstance(r.get("needs"), dict) for r in rows),
                  "every detailed row has needs{}")
            check(rows and all(isinstance(r.get("hediffs"), list) for r in rows),
                  "every detailed row has hediffs[]")
            print("     with colonistDetail: %d bytes" % size_of(detailed))

    print("\nI6 every alert target is named")
    unnamed = [(a.get("label"), t) for a in (default.get("alerts") or [])
               for t in (a.get("targets") or []) if not t.get("name")]
    check(not unnamed, "every listed culprit carries a name", repr(unnamed[:3]))

    print("\nI7 ui.modalOpen reproduces from ui.windows[]")
    ui = default.get("ui") or {}
    candidates = [w for w in (ui.get("windows") or [])
                  if not str(w.get("type") or "").startswith(NON_BLOCKING)
                  and (w.get("absorbInputAroundWindow") or w.get("forcePause")
                       or w.get("layer") == "Dialog")]
    check(bool(candidates) == bool(ui.get("modalOpen")),
          "modalOpen agrees with a hand-scan of windows[]",
          "modalOpen=%r, candidates=%s"
          % (ui.get("modalOpen"), [w.get("type") for w in candidates]))
    if ui.get("modalOpen"):
        human("a modal window (%s) is open. Map orders would be eaten; close it "
              "before playing." % ui.get("modalWindow"))

    print("\n3  the unknown-argument contract")
    bogus = call({"bogusKeyXYZ": 1}, "a bogus key still answers")
    if bogus is not None:
        check(bogus.get("unknownArguments") == ["bogusKeyXYZ"],
              "the bogus key is named in unknownArguments[]",
              repr(bogus.get("unknownArguments")))
        check(bool(bogus.get("unknownArgumentsWarning")),
              "and the warning is set")

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

    python status.py                the board -- about twenty lines
    python status.py --brief        three lines
    python status.py --explain      + each alert's explanation
    python status.py --detail       + every need and hediff per colonist
    python status.py --json         the raw reply

What to look for:
  * the CLOCK line matches the game's own top-right readout, and says
    `PAUSED (by player)` when you paused it and `PAUSED (forced)` when a
    window is holding the clock;
  * the LETTERS marks agree with `python letters.py` row for row -- CHOICE
    is read from the button TEXTS, never from choiceCount;
  * the ALERTS lines name the same culprits `python alerts.py` names;
  * the COLONISTS block agrees with `python pawns.py --roster`;
  * MESSAGES ages tick up in REAL seconds while the game stays paused, and
    a row vanishes at about 13s.
""")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
