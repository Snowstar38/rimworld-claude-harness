"""Live check for WANTED 0, watchability: the menu that opens around a write.

    cd rimworld\\instruments
    python ..\\companion\\tests\\live_watch.py

Run it against a loaded, PAUSED colony. It never saves and never unpauses. It
DOES open a menu, select a pawn and move the camera - that is the thing under
test - and it waits for all of it to close itself again before it finishes.

## The one real write, and why it is safe

`selfTend` on a single colonist, toggled and then put straight back, which is
the same write `live_pawn_config.py` uses and the same reason: it is a boolean
on Pawn_PlayerSettings, it changes nothing else, and the second call restores
it. The value is asserted equal to its starting value at the end. If the
restore fails the script says so loudly; nothing else here writes at all.

## What it checks, and what a failure would mean

  1  a dry run reports watch.shown false, reason "dry run"
        FAIL = the watch step is running on a call that writes nothing, which
        means a menu opens for a plan. Every reply must carry `watch`, so an
        absent block fails here too.
  2  the real write reports shown:true with the tab a player would use
        selfTend lives on Pawn_PlayerSettings and the game draws it on the
        pawn's Health tab, so this expects inspectTab ITab_Pawn_Health, the
        Inspect main tab (InspectPaneUtility.OpenTab switches to it), selected
        true and cameraMoved true.
        FAIL = the write landed with nothing on screen, or in the wrong menu.
  3  the write still applied, and after{} was read back
        FAIL = the watch step interfered with the write it is supposed to
        decorate. This is the check that matters most: watch is decoration and
        must never be load-bearing.
  4  leadMs is 1500 and closesAfterSeconds is what was asked for
        FAIL = the lead or the clamp is not what the contract promises.
  5  after watchSeconds + 2 seconds nothing is selected and no main tab is open
        Read from the bridge's own `rimworld/list_selected_gizmos`
        (selectedCount) and `rimworld/get_ui_state` (mainTabOpen), not from our
        own reply, so this is the game answering rather than the tool.
        FAIL = a menu is left on screen after the action, which is the one rule
        M stated: the menu must not stay.
  6  the same write with watch:false reports shown:false, reason "watch:false"
        FAIL = watch cannot be turned off, so there is no quiet write.
  7  selfTend is back to the value it started at
        FAIL = this script changed the colony. Put it back by hand:
        `python pawns.py set <name> --selftend on|off --do`.

Not checkable from here: that the menu was open BEFORE the write landed rather
than after it. The ordering is a wall-clock claim about a UI frame, and only a
person watching (or a recording) can confirm it. Everything this script can
reach is the state on either side of the gap.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "instruments"))

import rim                                                    # noqa: E402

TOOL = "home/pawn_config"
WATCH_SECONDS = 4
FAILURES = []
NEEDS_HUMAN = []


def check(ok, label, detail=""):
    if ok:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s   %s" % (label, detail))
        FAILURES.append(label + ("   " + str(detail) if detail else ""))
    return ok


def human(msg):
    print("  NEEDS HUMAN: %s" % msg)
    NEEDS_HUMAN.append(msg)


def watch_of(r, label):
    """The watch block, insisted upon: it is Always = true on every reply."""
    w = (r or {}).get("watch")
    if not isinstance(w, dict):
        check(False, "%s: the reply carries a watch{} block" % label, r)
        return {}
    return w


def selected_count():
    """The bridge's own count, not ours."""
    try:
        r = rim.game("rimworld/list_selected_gizmos", {})
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)
    return r.get("selectedCount"), None


def main_tab_open():
    """(open?, id) from rimworld/get_ui_state, whichever casing it uses."""
    try:
        r = rim.game("rimworld/get_ui_state", {})
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)
    if not isinstance(r, dict):
        return None, str(r)[:200]
    for key in ("mainTabOpen", "MainTabOpen"):
        if key in r:
            ident = r.get("openMainTabId") or r.get("OpenMainTabId")
            return bool(r[key]), ident
    return None, "no mainTabOpen key in get_ui_state: %s" % sorted(r)[:20]


def config(**args):
    return rim.game(TOOL, args, strict=False)


def main():
    rim.init()
    print("=" * 72)
    print("WANTED 0 watchability -- live check. ONE real write, put straight back.")
    print("=" * 72)

    r = rim.game("home/list_pawns", {"settings": True})
    people = [p for p in r.get("pawns") or []
              if p.get("isColonist") and not p.get("dead")]
    if not people:
        human("no living colonist on this map; nothing to write to")
        return summary()

    # Check 5 asks the game whether anything is open AFTER the close. It can
    # only mean something if nothing was open BEFORE: the close deliberately
    # leaves alone a tab or a selection it did not make.
    before_count, _ = selected_count()
    before_tab, before_id = main_tab_open()
    if before_count:
        human("%s object(s) are already selected; the close-check below cannot "
              "tell our selection from that one. Click empty ground and re-run."
              % before_count)
    if before_tab:
        human("the %s main tab is already open; the close deliberately leaves a "
              "tab it did not open, so check 5 will read as a failure. Press "
              "Escape and re-run." % (before_id or "?"))

    subject = people[0]
    name = subject.get("name")
    tid = (subject.get("settings") or {}).get("thingId") or name
    start = (subject.get("settings") or {}).get("selfTend")
    print("subject: %s (%s), selfTend %s" % (name, tid, start))
    if not isinstance(start, bool):
        human("selfTend was not readable on %s; nothing safe to toggle" % name)
        return summary()
    flip = not start

    # ---------------------------------------------------------- 1. dry run
    print("\n1. a dry run shows nothing")
    d = config(pawn=tid, selfTend="on" if flip else "off", dryRun=True)
    w = watch_of(d, "dry run")
    check(w.get("shown") is False, "dry run: watch.shown is false", w)
    check(w.get("reason") == "dry run", "dry run: reason is \"dry run\"", w.get("reason"))
    check(d.get("applied") is False, "dry run: applied is false", d.get("applied"))

    # ----------------------------------------------- 2-4. the one real write
    print("\n2. the real write, watched")
    a = config(pawn=tid, selfTend="on" if flip else "off", dryRun=False,
               watch=True, watchSeconds=WATCH_SECONDS)
    w = watch_of(a, "real write")
    check(w.get("shown") is True, "real write: watch.shown is true", w)
    check(w.get("inspectTab") == "ITab_Pawn_Health",
          "real write: the pawn's Health tab is what opened", w.get("inspectTab"))
    check(w.get("mainTab") == "Inspect",
          "real write: the Inspect main tab carries it", w.get("mainTab"))
    check(w.get("selected") is True, "real write: the pawn is selected", w.get("selected"))
    check(w.get("cameraMoved") is True, "real write: the camera moved to them",
          w.get("cameraMoved"))
    check(w.get("leadMs") == 1500, "real write: leadMs is the 1500 ms lead",
          w.get("leadMs"))
    check(w.get("closesAfterSeconds") == WATCH_SECONDS,
          "real write: closesAfterSeconds is what was asked for",
          w.get("closesAfterSeconds"))
    if w.get("note"):
        print("      note: %s" % w["note"])

    print("\n3. the write itself is untouched by the decoration")
    check(a.get("applied") is True, "real write: applied is true", a.get("applied"))
    check(a.get("afterIsPredicted") is False,
          "real write: after{} was read back, not predicted", a.get("afterIsPredicted"))
    row = next((f for f in a.get("fields") or [] if f.get("field") == "selfTend"), None)
    check(bool(row) and row.get("after") is flip,
          "real write: selfTend reads back as the new value", row)

    # ------------------------------------------------------ 5. it closes itself
    wait = WATCH_SECONDS + 2
    print("\n4. waiting %d s for the menu to close itself" % wait)
    time.sleep(wait)

    count, err = selected_count()
    if err:
        human("rimworld/list_selected_gizmos did not answer (%s); "
              "check the selection on screen by hand" % err)
    else:
        check(count == 0, "after the close: nothing is selected", count)

    open_now, ident = main_tab_open()
    if open_now is None:
        human("rimworld/get_ui_state did not report a main tab (%s); "
              "check the screen by hand" % ident)
    else:
        check(open_now is False, "after the close: no main tab is open", ident)

    # --------------------------------------------- 6-7. put it back, unwatched
    print("\n5. putting it back with watch:false")
    b = config(pawn=tid, selfTend="on" if start else "off", dryRun=False, watch=False)
    w = watch_of(b, "watch:false")
    check(w.get("shown") is False, "watch:false: watch.shown is false", w)
    check(w.get("reason") == "watch:false",
          "watch:false: reason names the flag", w.get("reason"))
    check(b.get("applied") is True, "watch:false: the write still applied",
          b.get("applied"))

    again = rim.game("home/list_pawns", {"settings": True})
    live = next((p for p in again.get("pawns") or []
                 if (p.get("settings") or {}).get("thingId") == tid
                 or p.get("name") == name), None)
    now = (live or {}).get("settings", {}).get("selfTend")
    check(now is start, "selfTend is back to what it was (%s)" % start, now)
    if now is not start:
        human("selfTend on %s is %s and started %s -- put it back with "
              "`python pawns.py set \"%s\" --selftend %s --do`"
              % (name, now, start, name, "on" if start else "off"))

    return summary()


def summary():
    print("\n" + "=" * 72)
    if FAILURES:
        print("%d FAILED:" % len(FAILURES))
        for f in FAILURES:
            print("  - %s" % f)
    else:
        print("all checks passed")
    for h in NEEDS_HUMAN:
        print("  NEEDS HUMAN: %s" % h)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except rim.BridgeError as e:
        print("bridge error: %s" % e)
        print(json.dumps({"hint": "is GABS up and a colony loaded and paused?"}))
        sys.exit(2)
