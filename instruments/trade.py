"""Trade without the trade screen.

  python trade.py                       # who will trade with us right now
  python trade.py open Aardvark         # set up the deal, print the whole sheet
  python trade.py sheet                 # the sheet again, by category
  python trade.py sheet medicine        # only rows matching "medicine"
  python trade.py sheet --staged        # only what is staged
  python trade.py sheet --all           # + the rows this trader will not trade
  python trade.py open Aardvark --all   # same, from the top
  python trade.py buy pemmican 180      # stage a purchase
  python trade.py sell "wooden club" 3  # stage a sale
  python trade.py sell Warg 1 --allow-pawns   # selling a PAWN needs this flag
  python trade.py set "#42" -12         # stage by row index, signed
  python trade.py preview               # totals, nothing executed
  python trade.py accept                # execute, then close the session
  python trade.py accept --no-watch     # execute without selecting the trader

The watched `accept` opens the real trade dialog, which force-pauses the
game. Supervised play is therefore PAUSED ON PURPOSE first and said so,
rather than being killed silently by the force pause; restart it with
`python play.py start` once the trade is settled.
  python trade.py cancel                # drop the session, trade nothing
  python trade.py status                # is a session open, and whose
  python trade.py close-dialog          # a Dialog_Trade left open on screen
  python trade.py --help

Add `--say "..."` and `--mood <mood>` to any of these to narrate to the stream.

`home/trade` sets up RimWorld's own `TradeSession` directly, and `Dialog_Trade`
is a pure view over that session -- so nothing is faked and nothing is skipped.
The whole stock arrives in one call and any row is addressable by name. Do not
drive the trade dialog by clicking: every row's `<` / `<<` / `>` / `>>` button
acts on whichever row is at the top of the viewport, and reports success either
way.

## Selling a pawn needs `--allow-pawns`

`TradeUtility.AllSellableColonyPawns` feeds the sheet, so colonists, prisoners
and colony animals are ordinary rows with ordinary prices, and rows here are
addressed by fuzzy name: `sell finn 1` is a plausible typo with an irreversible
result. So `set` refuses any line that would hand a PAWN over unless
`allowPawns: true` is passed, and names the row it refused:

    REJECTED finn: Refusing to SELL 1 x Finn -- that row is a pawn
    (colonist Finn (Human)). Pass allowPawns:true to do it deliberately.

**Buying is never blocked.** Buying animals from a trader is ordinary play, and
a guard that gets in the way of that is a guard somebody turns off. The guard
follows the deal's own direction, so in gift mode (where positive means the
colony gives) it is the positive counts that are refused.

## Signs

`count > 0` means the colony BUYS. `count < 0` means the colony SELLS. That is
RimWorld's own `Tradeable.CountToTransfer` convention, and `buy`/`sell` are just
the two friendly faces of it. The **silver row is computed**, never set: it
follows from every other line, and trying to set it is refused rather than
silently overwritten.

## The sheet is the authoritative "what we can offer"

PLAYBOOK rule 4a: `inv.py` counts the whole map, traders' stock included, so it
does not know what is ours. The `OURS` column here is the deal's own count --
what the trader is willing to buy from us and can actually reach -- which is the
number to spend against. `trade.py` with no arguments also prints the map's
total silver beside the deal's silver, so the gap is visible rather than
inferred.
"""
import json
import sys

import rim

TOOL = "home/trade"

MOOD_HINT = """
home/trade is a companion-DLL tool. If this failed with an unknown-tool error,
the DLL is built but not installed, or RimWorld has not been restarted since it
was installed -- companions are discovered once, at bridge startup. See
C:\\Home\\rimworld\\companion\\INSTALL.md.
""".strip()


def call(**args):
    """One home/trade call. Structured failures come back, they do not raise."""
    return rim.game(TOOL, args, strict=False)


def _fail(r):
    """Print a structured failure. Returns True if the payload WAS a failure."""
    if not isinstance(r, dict):
        print("trade.py: the bridge returned %r, not a payload." % (r,))
        print(MOOD_HINT)
        return True
    if r.get("success"):
        return False
    print("FAILED (%s): %s" % (r.get("errorKind") or "?", r.get("error") or r))
    for key in ("hint", "note"):
        if r.get(key):
            print("  %s" % r[key])
    if r.get("candidates"):
        print("  candidates:")
        for c in r["candidates"]:
            print("    #%-4s %s" % (c.get("index"), c.get("label")))
    if r.get("errorKind") in (None, "") and "message" in r:
        print(MOOD_HINT)
    if r.get("distance") is not None:
        # move.py is import-only -- there is no CLI to point at.
        p = r.get("traderPosition") or {}
        print('  walk there first:  python -c "import move; move.goto(%r, %s, %s)"'
              % (r.get("negotiatorId"), p.get("x"), p.get("z")))
        print("  (or just re-run `open` without --adjacent: the game does not require it)")
    if r.get("balance"):
        show_balance(r["balance"])
    if r.get("staged"):
        show_staged(r["staged"])
    return True


# ---------------------------------------------------------------- formatting

def money(v):
    if v is None:
        return "-"
    return ("%.1f" % v).rstrip("0").rstrip(".") or "0"


def show_traders(r):
    neg = r.get("negotiator")
    if neg:
        print("negotiator: %s (social %s, trade price %+.0f%%, talking %.2f/hearing %.2f)"
              % (neg.get("name"), neg.get("socialSkill"),
                 (neg.get("tradePriceImprovement") or 0) * 100,
                 neg.get("talkingCapacity") or 0, neg.get("hearingCapacity") or 0))
    else:
        print("negotiator: NOBODY -- no colonist can negotiate (downed, mental, or Social disabled)")
    others = [c["name"] for c in (r.get("negotiatorCandidates") or [])[1:]]
    if others:
        print("  also able: %s" % ", ".join(others))

    print("silver: %s on the map in total" % r.get("silverOnMapTotal"))
    print()

    rows = r.get("traders") or []
    if not rows:
        print("No trader caravan is on the map.")
    for t in rows:
        flag = "" if t.get("canTradeNow") else "   [CANNOT TRADE NOW]"
        print("%s -- %s, %s%s" % (t.get("name"), t.get("traderKindLabel") or t.get("traderKind"),
                                  t.get("faction") or "no faction", flag))
        p = t.get("position") or {}
        print("   id %s" % t.get("id"))
        print("   at (%s,%s), %s cells from %s%s"
              % (p.get("x"), p.get("z"), t.get("negotiatorDistance"),
                 (neg or {}).get("name"),
                 "  ADJACENT" if t.get("negotiatorAdjacent") else ""))
        print("   %s stacks of stock; our silver as this deal counts it: %s"
              % (t.get("goodsStacks"), t.get("colonySilverForThisTrade")))
        if t.get("hasQuest"):
            print("   carries a QUEST -- it is handed over when the session closes")
        print()

    ships = r.get("orbitalTraders") or []
    print("orbital: %s ship(s), %s comms console(s) (%s usable), %s powered trade beacon(s)"
          % (len(ships), r.get("commsConsoles"), r.get("usableCommsConsoles"),
             r.get("orbitalTradeBeaconsPowered")))
    for s in ships:
        if s.get("error"):
            print("   %s" % s["error"])
            continue
        print("   %s -- %s, leaves in %s ticks, silver %s, our sellable silver %s%s"
              % (s.get("name"), s.get("traderKindLabel") or s.get("traderKind"),
                 s.get("ticksUntilDeparture"), s.get("traderSilver"),
                 s.get("colonySilverForThisTrade"),
                 "" if s.get("canTradeNow") else "   [CANNOT TRADE NOW]"))
    if ships and not r.get("orbitalTradeBeaconsPowered"):
        print("   NOTE: with no powered orbital trade beacon an orbital deal sees NOTHING of ours to sell.")
    print()
    print(r.get("silverNote") or "")


def show_balance(b):
    if not b:
        return
    if b.get("giftMode"):
        print("GIFT MODE -- nothing is bought, everything staged is given away.")
    net = b.get("netSilverToColony")
    print("silver: %s now -> %s after   (net %s)   trader: %s -> %s"
          % (b.get("colonySilverNow"), b.get("colonySilverAfter"),
             ("%+d" % net) if net is not None else "-",
             b.get("traderSilverNow"), b.get("traderSilverAfter")))
    print("buying %s line(s) worth %s; selling %s line(s) worth %s"
          % (b.get("buyLines"), money(b.get("buyValue")),
             b.get("sellLines"), money(b.get("sellValue"))))
    if not b.get("colonyCanAfford") and not b.get("giftMode"):
        print("*** THE COLONY CANNOT AFFORD THIS. accept will refuse. ***")
    if not b.get("traderHasEnoughSilver"):
        print("*** the TRADER cannot afford this -- sell less, or take goods instead. ***")


def show_staged(staged):
    if not staged:
        print("nothing staged.")
        return
    print("staged:")
    for s in staged:
        print("  %-30s %+6d  @ %-8s = %s   [%s]"
              % ((s.get("label") or "")[:30], s.get("count") or 0,
                 money(s.get("unitPrice")), money(s.get("lineValue")),
                 s.get("action")))


def show_sheet(r, staged_only=False):
    print("%s -- %s%s   negotiator %s"
          % (r.get("traderName"), r.get("traderKind") or "?",
             "  [GIFT MODE]" if r.get("giftMode") else "",
             r.get("negotiator")))
    if r.get("negotiatorDistance") is not None:
        print("negotiator is %s cells away%s. The game does not require adjacency; see INSTALL.md."
              % (r["negotiatorDistance"], " (adjacent)" if r.get("negotiatorAdjacent") else ""))
    for reason in (r.get("cannotSellReasons") or []):
        print("cannot sell: %s" % reason)
    print()

    rows = r.get("rows") or []
    if staged_only:
        rows = [x for x in rows if x.get("countToTransfer")]
    groups = {}
    for row in rows:
        groups.setdefault(row.get("category") or "(uncategorised)", []).append(row)

    print("  %-4s %-32s %7s %7s %8s %8s %7s" % ("#", "ITEM", "OURS", "THEIRS", "BUY", "SELL", "STAGED"))
    for cat in sorted(groups):
        print("  -- %s %s" % (cat, "-" * max(0, 70 - len(cat))))
        for row in sorted(groups[cat], key=lambda x: (x.get("label") or "").lower()):
            ours = row.get("colonyCount") or 0
            theirs = row.get("traderCount") or 0
            staged = row.get("countToTransfer") or 0
            # 2026-09-04: this column used to print a bare `x` for a row the
            # trader REFUSES, in the STAGED column, where it was
            # indistinguishable from a staged quantity until the sell was
            # rejected -- and a combat supplier refuses all leather, so the
            # sheet said "staged" for everything we were trying to sell. The
            # server omits those rows now; `--all` brings them back SAID, not
            # marked.
            mark = "" if row.get("traderWillTrade", True) else "  [WILL NOT TRADE]"
            print("  %-4s %-32s %7d %7d %8s %8s %7s%s"
                  % ("#%d" % row.get("index", -1),
                     (row.get("label") or "?")[:32], ours, theirs,
                     money(row.get("buyPrice")) if theirs else "-",
                     money(row.get("sellPrice")) if ours else "-",
                     ("%+d" % staged) if staged else "", mark))

    omitted = (r.get("omittedByFilter") or 0) + (r.get("omittedByRowCap") or 0)
    print()
    print("%s of %s rows shown%s."
          % (len(rows), r.get("rowCount"),
             (", %d hidden by filter/cap" % omitted) if omitted else ""))
    untradeable = r.get("omittedUntradeable") or 0
    if untradeable:
        print("%d row(s) this trader will not trade omitted (--all shows them)."
              % untradeable)
    print()
    show_balance(r.get("balance"))


# -------------------------------------------------------------------- verbs

def cmd_list():
    r = call(action="list_traders")
    if _fail(r):
        return 1
    show_traders(r)
    return 0


def cmd_open(argv):
    if not argv:
        print("open needs a trader id or a unique piece of its name. `python trade.py` lists them.")
        return 1
    args = {"action": "open", "traderId": argv[0]}
    rest = [a for a in argv[1:] if not a.startswith("-")]
    if rest:
        args["negotiator"] = rest[0]
    if "--gift" in argv:
        args["giftMode"] = True
    if "--adjacent" in argv:
        args["requireAdjacent"] = True
    if "--all" in argv:
        args["includeUntradeable"] = True
    r = call(**args)
    if _fail(r):
        return 1
    show_sheet(r)
    return 0


def cmd_sheet(argv):
    args = {"action": "sheet"}
    words = [a for a in argv if not a.startswith("-")]
    if words:
        args["match"] = words[0]
    if "--staged" in argv or "--changed" in argv:
        args["onlyChanged"] = True
    if "--all" in argv:
        args["includeUntradeable"] = True
    r = call(**args)
    if _fail(r):
        return 1
    show_sheet(r)
    return 0


def cmd_set(argv, sign):
    """sign: +1 for buy, -1 for sell, 0 for a signed `set`."""
    allow_pawns = "--allow-pawns" in argv or "--allowpawns" in argv
    # Strip flags before the name/count split -- the item name is everything but
    # the last word, so a stray flag would end up inside it.
    argv = [a for a in argv if a not in ("--allow-pawns", "--allowpawns")]
    if len(argv) < 2:
        print("needs an item and a count, e.g. `buy pemmican 180`")
        return 1
    name = " ".join(argv[:-1])
    try:
        n = int(argv[-1])
    except ValueError:
        print("'%s' is not a number." % argv[-1])
        return 1
    if sign:
        n = sign * abs(n)
    r = call(action="set", item=name, count=n, allowPawns=allow_pawns)
    if _fail(r):
        return 1
    for line in r.get("lines") or []:
        if line.get("ok"):
            print("#%s %s -> %+d (%s)"
                  % (line.get("index"), line.get("label"),
                     line.get("countToTransfer") or 0, line.get("actionToDo")))
        else:
            print("REJECTED %s: %s" % (line.get("request"), line.get("error")))
            if line.get("errorKind") == "pawn_sale_refused":
                print("  that row is %s. Re-run with --allow-pawns if you mean it."
                      % (line.get("pawnDescription") or "a pawn"))
            if line.get("minCount") is not None:
                print("  legal range for that row: %s .. %s" % (line["minCount"], line["maxCount"]))
            for c in (line.get("candidates") or []):
                print("    #%-4s %s" % (c.get("index"), c.get("label")))
    print()
    show_balance(r.get("balance"))
    return 0 if not r.get("linesRejected") else 1


def cmd_preview():
    r = call(action="preview")
    if _fail(r):
        return 1
    print("%s -- negotiator %s" % (r.get("traderName"), r.get("negotiator")))
    show_staged(r.get("staged"))
    print()
    show_balance(r.get("balance"))
    print()
    print("would succeed: %s" % r.get("wouldSucceed"))
    return 0


def watch_line(r, on=None):
    """One line about the menu the write opened, if any. Claims the camera for
    Hands when the watch moved it, so the Lookout stays out of the shot."""
    w = (r or {}).get("watch") or {}
    if not w.get("shown"):
        print("watch: skipped (%s)" % (w.get("reason") or "not shown"))
        return
    if w.get("cameraMoved"):
        try:
            import camlock
            camlock.claim("hands", "watch")
        except Exception:
            pass
    tab = (w.get("inspectTab") or "").replace("ITab_Pawn_", "").replace("ITab_", "") or w.get("mainTab")
    who = (" on " + on) if on else ""
    if tab:
        print("watch: %s tab open%s, closes in %s s" % (tab, who, w.get("closesAfterSeconds")))
    else:
        print("watch: %s%s, clears in %s s"
              % ("selected" if w.get("selected") else "camera moved", who,
                 w.get("closesAfterSeconds")))


def pause_supervised_play():
    """Stop the supervised-play service on purpose, before the dialog does.

    `Dialog_Trade` force-pauses the game, and the companion's watcher stops on
    any force pause -- so on 2026-09-05 `trade.py accept` left the game paused
    and the service dead with no letter, no message and no warning. It was
    found by checking. Doing it deliberately means the stop is announced, and
    the line that restarts it is printed where the person reading the trade
    result is already looking.

    Returns a short word for the report: "paused", "not running", or a reason.
    """
    try:
        import play
        import play_service
        row = play_service.read_json(play_service.SERVICE_STATE) or {}
        if not play.service_alive(row):
            return "not running"
        if play.pause() != 0:
            return "PAUSE REFUSED"
        return "paused"
    except Exception as ex:
        return "%s: %s" % (type(ex).__name__, ex)


RESTART_LINE = ("Supervised play does NOT resume itself. When the trade is "
                "settled:  python play.py start")


def cmd_accept(argv):
    args = {"action": "accept", "watch": "--no-watch" not in argv}
    if "--allow-empty" in argv:
        args["allowEmpty"] = True
    stopped = None
    if args["watch"]:
        # Only the watched path opens the real dialog. --no-watch executes
        # without one, so there is nothing to force-pause and nothing to stop.
        stopped = pause_supervised_play()
        if stopped == "paused":
            print("supervised play PAUSED on purpose before the trade dialog "
                  "(the dialog force-pauses the game and the watcher stops on "
                  "any force pause).")
        elif stopped == "not running":
            print("supervised play was not running; nothing to pause.")
        else:
            print("*** could not pause supervised play (%s). The trade dialog "
                  "will force-pause the game and the service will stop by "
                  "itself, without a message. ***" % stopped)
    r = call(**args)
    if _fail(r):
        watch_line(r)
        if stopped == "paused":
            print(RESTART_LINE)
        return 1
    # Before the result, because it is the answer to "did anything visible
    # happen" -- last session a whole trade went through and nobody watching
    # knew one had.
    w = r.get("watch") or {}
    if w.get("dialogShown"):
        print("trade window shown for %s s, then executed" % w.get("secondsShown"))
    print("traded: %s (TryExecute returned %s)" % (r.get("actuallyTraded"), r.get("executed")))
    print("with %s, negotiated by %s" % (r.get("traderName"), r.get("negotiator")))
    show_staged(r.get("moved"))
    gb, ga = r.get("goodwillBefore"), r.get("goodwillAfter")
    if gb is not None and ga is not None and gb != ga:
        print("faction goodwill %s -> %s" % (gb, ga))
    for m in (r.get("traderResponse") or []):
        print("game says: %s  [%s]" % (m.get("text"), m.get("type")))
    if r.get("questReceived"):
        print("the trader handed over a quest.")
    print("session closed. Row indices from before this call are dead.")
    print("Check bought goods with `python inv.py <item> --all-owners`; ownership, forbidden flags and storage must be read back.")
    if stopped is not None:
        print(RESTART_LINE)
    elif w.get("dialogShown"):
        print("The trade dialog force-pauses the game and can stop supervised play. "
              "Check `python play.py status`, then `python play.py start` after handling any stop.")
    watch_line(r, r.get("traderName"))
    return 0


def cmd_cancel():
    r = call(action="cancel")
    if _fail(r):
        return 1
    print("session with %s dropped, nothing traded." % (r.get("traderName") or "(none)"))
    show_staged(r.get("discarded"))
    return 0


def cmd_status():
    r = call(action="status")
    if _fail(r):
        return 1
    print("session active: %s   dialog open: %s" % (r.get("sessionActive"), r.get("tradeDialogOpen")))
    if r.get("sessionActive"):
        print("with %s (%s), negotiator %s, opened by this tool: %s"
              % (r.get("traderName"), r.get("traderKind"),
                 r.get("negotiator"), r.get("openedByThisTool")))
        print("%s rows" % r.get("rowCount"))
        show_staged(r.get("staged"))
        print()
        show_balance(r.get("balance"))
    return 0


def cmd_close_dialog():
    r = call(action="close_dialog")
    if _fail(r):
        return 1
    print("closed %s Dialog_Trade window(s); session was active: %s"
          % (r.get("dialogsFound"), r.get("sessionWasActive")))
    for d in (r.get("dialogs") or []):
        print("  %s -> closed=%s %s" % (d.get("type"), d.get("closed"), d.get("error") or ""))
    left = [w["type"] for w in (r.get("openWindows") or [])]
    if left:
        print("still open: %s" % ", ".join(left))
    return 0


def main():
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0

    try:
        rim.init()
    except Exception as e:
        # A dead bridge must not print a traceback at a session that is trying
        # to trade. Say which end is down and stop.
        print("trade.py: could not reach the bridge (%s: %s)." % (type(e).__name__, e))
        print("Is GABS up and RimWorld loaded?  python setup.py --status")
        return 1

    cmd = argv[0] if argv else "list"
    rest = argv[1:]

    if cmd in ("list", "traders"):
        return cmd_list()
    if cmd == "open":
        return cmd_open(rest)
    if cmd == "sheet":
        return cmd_sheet(rest)
    if cmd == "buy":
        return cmd_set(rest, +1)
    if cmd == "sell":
        return cmd_set(rest, -1)
    if cmd == "set":
        return cmd_set(rest, 0)
    if cmd == "preview":
        return cmd_preview()
    if cmd == "accept":
        return cmd_accept(rest)
    if cmd == "cancel":
        return cmd_cancel()
    if cmd == "status":
        return cmd_status()
    if cmd in ("close-dialog", "close_dialog"):
        return cmd_close_dialog()
    if cmd == "json":
        # escape hatch: `python trade.py json '{"action":"sheet"}'`
        print(json.dumps(call(**json.loads(rest[0] if rest else "{}")), indent=1))
        return 0

    print("unknown command %r. `python trade.py --help`" % cmd)
    return 1


if __name__ == "__main__":
    # Stream narration: --say/--mood are pulled out of argv before main() reads
    # it positionally, and posted after the call returns. Same helper as
    # rim.py / run.py / watch.py / cam.py.
    try:
        import overlay_client as _ov
        sys.argv[1:], _say, _mood = _ov.take_flags(sys.argv[1:])
    except Exception:
        _ov, _say, _mood = None, None, None
    _code = main()
    if _ov is not None:
        _ov.say_flags(_say, _mood)
    sys.exit(_code)
