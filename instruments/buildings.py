"""What the colony has BUILT, what it is still building, and what is stuck.

  python buildings.py                    # OUR structures: pending, then built
  python buildings.py --pending          # only blueprints and frames
  python buildings.py --blueprints       # only blueprints
  python buildings.py --frames           # only frames under construction
  python buildings.py --built            # only finished buildings
  python buildings.py bench              # only things matching "bench"
  python buildings.py --near 120 140 30  # only within 30 cells of 120,140
  python buildings.py --all              # every faction: ruins, tribes, unclaimed
  python buildings.py --rock             # include natural rock as buildings
  python buildings.py --every            # no aggregation: every wall, one row
  python buildings.py --damaged-below 50 # give damaged buildings their own rows
  python buildings.py --inspect          # + the inspect-pane text on every row
  python buildings.py --power            # every power NET, and what is flagged
  python buildings.py 115,154            # what is AT that cell (any occupied cell)
  python buildings.py "Shelf@115,154"    # that def at that cell
  python buildings.py 1234               # one thing by id (build.py's number too)
  python buildings.py --json             # the raw reply, unformatted
  python buildings.py --help             # this text

  python buildings.py gizmos <thing>     # what the gizmo bar holds, nothing fired
  python buildings.py gizmo <thing> "<gizmo label>" [--do]
                                         # select, list and fire one, in one
                                         #   process. Dry run without --do.
  python buildings.py set <thing> [--forbid on|off] [--power on|off]
      [--temperature C] [--medical on|off] [--owner NAME|none]
      [--prisoners on|off] [--do]
  python buildings.py reinstall <thing> <x> <z> [--do]
                                         # fire `Reinstall at...` and click the
                                         #   destination. Dry run without --do.

`<thing>` is a ThingID, an `x,z` cell, a "DefName@x,z" pair, or a unique
label/defName substring among colony buildings; an ambiguous one is refused with the
candidates. `set` is a DRY RUN until `--do`, prints before -> after per field,
and `--power` places a flick designation -- a colonist walks over and flicks
it, so switchIsOn does not move until they do. `--no-watch` skips selecting
the building while the write lands.

## An address is not a substring

A bare positional is a `--match` substring, so `115,154` matched nothing and
answered with the full "none matched, 637 removed by filters" block -- misuse
reading as data (turns 34, 39). Three spellings are now ADDRESSES instead, the
same three `buildings.py gizmos` takes: `x,z`, `DefName@x,z`, and a thing id
(including the bare number `build.py` prints). A cell matches any cell the
building **occupies**, not just its anchor: a wooden shelf is two cells, which
is why `--inspect "Shelf@115,154"` said none matched while `gizmos` on the same
string resolved it at 114,154. An address that finds nothing says so as an
address ("NOTHING AT cell 115,154"), never as a filter result.

## The power GRID, not just the building

`notConnectedToPower=0` cannot see an orphaned battery: a battery has no power
draw, so it is never "unpowered", and three turns of clean power checks passed
while the whole hill pocket was cut off (turns 37-39). `home/list_buildings`
now returns `powerNets[]` -- one row per PowerNet with its generators,
consumers, batteries, generation, draw and stored Wd -- and flags the ones that
cannot work: **noProducer**, **noConsumer**, **isolatedBattery**,
**isolatedTransmitter**. The default summary prints one warning line per
flagged net naming the buildings on it; `--power` prints the whole table. A net
carrying no building of ours is never flagged, so ancient ruins stay quiet.

## Reinstalls are pending construction too

A `Blueprint_Install` is a blueprint, so it is in `--pending` -- but its
`ThingDef.entityDefToBuild` is null, so it used to have no name for what it was
moving and read as `?`. The row now says `reinstall of <thing>`, from the
instance's own `MiniToInstallOrBuildingToReinstall`. It costs no materials, and
its empty resources[] says so rather than reading as an unreadable cost.

## `gizmo` fires one; `gizmos` only reads

`gizmos` reads the bar through `home/building_config`, which returns no ids and
fires nothing. `rimworld/execute_gizmo` takes ids from
`rimworld/list_selected_gizmos`, and those are only valid while the selection
that produced them stands -- which no process can hand to the next. So `gizmo`
selects, lists and executes in ONE process, dry-running until `--do`. Turret
`Uninstall` and `Reinstall at...` are why it exists.

**The write path selects by thing id, not by cell.** It can only click a cell,
and a cell holds whatever is lying on it: 12 plainleather on a turret's tile
gave `gizmo --do` a one-gizmo bar while `gizmos` on the same id listed seven
(turn 14, found by chat and by no instrument). Every click here goes through
`pick.py`, which drops an armed designator, moves the camera onto the cell --
a click outside the frame does nothing and still reports success -- cycles the
things in the cell, prints `selected: <label> [Thing_X]`, and REFUSES to fire
unless the selected id is the one asked for.

A **MinifiedThing** (packed-up furniture) is an item, not a building, so
`home/building_config` cannot address it and both subcommands used to refuse it
with "No colony building matches" (turns 12, 13). Its id now resolves through
`rimworld/get_map_target_info` and its bar is read through the selection
instead; `mini_install.py` is the tool that installs one.

## SHORT means the colony cannot supply it, not "not delivered yet"

A blueprint holds nothing until the first haul turns it into a frame, so it
owes its FULL cost the moment it is placed. That is a delivery queue, and
calling it SHORT flagged 51 floor blueprints against 900 sandstone in stock. A
site is now **SHORT** only when the usable stock is below what is owed, and
`not yet delivered (N available in colony)` otherwise.

"Usable" is `unforbiddenOnMap` -- spawned stacks nobody has forbidden. The old
`colony available` was `countedAsResource`, RimWorld's ResourceCounter, which
sums STORAGE only: 359 loose steel reads there as 0. All three numbers are
printed side by side in the rolled-up block.

## The four things it prints loudly

  * **SHORT** -- a blueprint or frame the colony cannot currently supply. The
    rolled-up RESOURCES STILL NEEDED block says whether the colony owns it.
  * **NO BILLS / NO ACTIVE BILL** -- a finished "Do 8x" bill is drawn
    identically to a live one, so `finished` is computed from the bill's own
    fields and cannot be misread off a label.
  * **UNPOWERED** -- every unpowered building gets its own row and a bang.
  * **POSITIONS TRUNCATED**, and the shared-cell line beside it. The row carries
    `positionsListed`, `positionsNotListed`, `positionsTruncated`,
    `positionsCap`, `distinctCells`, `positionsUnreadable` and `promotedOut`.
    The cap and the shared cell get DIFFERENT words on purpose: raising
    `maxPositionsPerDef` fixes the first and can never fix the second.

## `--inspect`: the inspect pane, without clicking anything

`--inspect` adds `inspectString` to every DETAILED row -- the text RimWorld's
inspect pane would draw for that thing, with the comp lines in it: power output,
**"Not connected to a power grid"**, fuel, "Broken down". Reading it needs no
selection, which is the whole point: selecting a multi-cell building to read its
pane does not reliably work, and selection is a visible mutation on stream.

It is OFF by default because the strings cost bytes and the default whole-map
call is a measured 17.2 KB. AGGREGATED rows never get one: an aggregate row is a
def, not a thing, and there is no single inspect string for eleven conduits.
`--inspect --every` gives one per building.

It is not the whole pane. `GetInspectStringLowPriority()` -- the deterioration
and "attack to destroy" lines -- is deliberately never read, because RimWorld's
implementation of it calls `Faction.OfPlayer`, which reaches `Log.Error`, which
pauses the game.

## Colony structures by default

The default scope is the colony's own buildings (`playerOnly`), because what is
built, what is stuck and what has no bills are questions about our stuff. Most
of this map's walls are ancient ruins in the far corners, and counting them
buries the frame that is starving. `--all` widens to every faction.

The filter is never silent: the footer says how many buildings it skipped
(`byPlayerOnly` in the reply's own accounting). Under `--all`, the aggregated
rows group by DEF only -- they do not separate factions -- and only the detail
rows carry `faction`.

## There is no fallback, on purpose

`home/list_buildings` is the only path to this data, so a failure here is LOUD
and prints what to check. It never degrades into an empty list, because an empty
list reads as "nothing is under construction".
"""
import json
import sys
import time

import pick
import rim

TOOL = "home/list_buildings"

HELP = (
    "\n  There is no stock fallback for this -- the bridge has no building lister,"
    "\n  which is why the companion tool exists. Check, in order:"
    "\n    1. Is RimWorld running and connected?   python setup.py"
    "\n    2. Is the companion DLL installed?"
    "\n       <game root>\\BridgeTools\\HomeBridge\\HomeBridge.BridgeTools.dll"
    "\n    3. Was RimWorld RESTARTED since it was installed? Companions are"
    "\n       discovered once, at bridge startup."
    "\n    4. rimbridge/get_bridge_status -> companions.diagnostics should show"
    "\n       status 'registered' and no warnings."
    "\n  Full procedure: rimworld\\companion\\INSTALL.md"
)


def survey(**kw):
    """One call. Colony structures unless `playerOnly=False`.

    The default is here rather than in `main()` so every importer gets it: a
    Scout brief and a base centroid both want OUR buildings, and the ancient
    ruins on this map outnumber them. Returns the raw reply; every filter is
    inside it, and the footer prints what each one removed.
    """
    kw.setdefault("playerOnly", True)
    try:
        r = rim.game(TOOL, kw)
    except Exception as e:
        raise rim.BridgeError("%s failed: %s: %s%s" % (TOOL, type(e).__name__, e, HELP))
    if not isinstance(r, dict) or not r.get("success"):
        why = (isinstance(r, dict) and (r.get("error") or r.get("message"))) or repr(r)[:200]
        raise rim.BridgeError("%s refused: %s%s" % (TOOL, why, HELP))
    for key in ("buildings", "aggregated", "attention", "resourceDeficit", "counts", "skipped"):
        if key not in r:
            raise rim.BridgeError(
                "%s answered without %r -- the installed DLL is older than this "
                "script expects. Rebuild and reinstall it.%s" % (TOOL, key, HELP))
    return r


# ------------------------------------------------------------- formatting ---

def _pos(row):
    p = row.get("position") or {}
    return "%d,%d" % (p.get("x", -1), p.get("z", -1))


def _num(v, fmt="%.0f"):
    return "?" if v is None else fmt % v


def _inspect(b, pad="       "):
    """The inspect-pane line under a row, when --inspect asked for one.

    Three states, three different lines, because they are three different
    facts: a string (printed), an empty string (the thing genuinely has nothing
    to say -- printed as such rather than as a blank), and None (the read
    THREW; its def is in the reply's `inspectSkipped`). Silence here would put
    "no power problem" and "we could not ask" in the same place."""
    if "inspectString" not in b:
        return
    text = b["inspectString"]
    if text is None:
        print("%s!! inspect: UNREADABLE -- GetInspectString() threw for this def "
              "(see inspectSkipped in --json)." % pad)
    elif text:
        print("%sinspect: %s" % (pad, text))


def _fac(r, b):
    """`[faction]` on a row, but only when the scope lets in more than ours."""
    if (r["filters"] or {}).get("playerOnly"):
        return ""
    return "  [%s]" % (b.get("faction") or "no faction")


def _why_empty(r, excluded_by_status):
    """Why a block has nothing in it. Never "none" when the honest answer is
    "none survived the filter" -- that substitution is the whole class of bug
    this toolset was rebuilt around."""
    f, s = r["filters"], r["skipped"]
    if f["status"] == excluded_by_status:
        return "not requested (status=%s), so this says nothing about the map." % f["status"]
    # A match filter names ONE thing, so every section it is not belongs to
    # something else. "none matched, 4700 removed by filters" reads as a fault.
    match = f.get("match")
    if match:
        counts = r.get("counts") or {}
        found = ((counts.get("detailed") or 0)
                 + (counts.get("aggregatedBuildings") or 0))
        if found:
            return ("nothing matching %r belongs in this section. The %d thing(s) "
                    "it matched are in the section that fits them." % (match, found))
    # byStatus is the population deliberately excluded by the requested view.
    # `--pending` skipping finished walls says nothing about whether pending
    # construction exists; only later filters can explain an empty block.
    removed = [(k, v) for k, v in sorted(s.items()) if v and k != "byStatus"]
    if removed:
        return ("none matched. %s were removed by filters (%s) -- so this is NOT "
                "'none on the map'." % (sum(v for _, v in removed),
                                        ", ".join("%s %d" % kv for kv in removed)))
    return "none. (checked -- nothing was filtered out, the map has none.)"


def _availability(r):
    """defName -> (what a hauler could fetch, the rolled-up amount still owed).

    Prefers `unforbiddenOnMap`; falls back to `onMapTotal`, then to
    `countedAsResource`. `countedAsResource` is RimWorld's ResourceCounter,
    which sums STORAGE only -- 359 loose steel reads there as 0.
    """
    out = {}
    for d in r.get("resourceDeficit") or []:
        usable = d.get("unforbiddenOnMap")
        if usable is None:
            usable = d.get("onMapTotal")
        if usable is None:
            usable = d.get("countedAsResource")
        out[d.get("defName")] = (usable, d.get("stillNeeded") or 0)
    return out


def _short(avail, res):
    """True only when the colony cannot supply what this site still needs.

    A blueprint holds nothing until the first haul turns it into a frame, so
    `stillNeeded` is its FULL cost the moment it is placed. Undelivered is not
    starved, and calling both SHORT flagged 51 floor blueprints against 900
    sandstone in stock.
    """
    still = res.get("stillNeeded") or 0
    if not still:
        return False
    usable, owed = avail.get(res.get("defName"), (None, 0))
    if usable is None:
        return True             # stock unknown -- keep the loud word
    return usable < max(owed, still)


def pending_block(r):
    """Blueprints and frames, deficit first. The point of item 2."""
    rows = [b for b in r["buildings"] if b.get("isBlueprint") or b.get("isFrame")]
    if not rows:
        # Checked, not assumed -- and "none exist" is never allowed to stand in
        # for "none survived the filter". An absent block would be
        # indistinguishable from a block that was never rendered.
        print("PENDING CONSTRUCTION: %s" % _why_empty(r, "built"))
        print()
        return
    # Short-of-materials first, then least complete first: the ones that are
    # stuck should never be below the ones that are merely slow.
    rows.sort(key=lambda b: (b.get("resourcesComplete", True),
                             b.get("percentComplete") if b.get("percentComplete") is not None else 1.0))
    print("PENDING CONSTRUCTION -- %d blueprint(s), %d frame(s)"
          % (r["attention"]["blueprints"], r["attention"]["frames"]))
    avail = _availability(r)
    for b in rows:
        owed = not b.get("resourcesComplete", True)
        short = owed and any(_short(avail, res) for res in b.get("resources") or [])
        mark = "!! SHORT " if short else ("   waiting" if owed else "   ok    ")
        if b.get("isInstallBlueprint"):
            what = "reinstall of %s" % (b.get("installOfLabel")
                                        or b.get("installOfDefName")
                                        or b.get("buildLabel") or "?")
        else:
            what = b.get("buildLabel") or b.get("buildDefName") or b.get("label") or "?"
        pct = b.get("percentComplete")
        prog = "" if pct is None else "  %d%% built" % round(100 * pct)
        print("%s %-9s %-30s %-9s%s  work left %s"
              % (mark, b.get("status", "?"), what[:30], _pos(b), prog,
                 _num(b.get("workLeft"))))
        if b.get("stuff"):
            print("               of %s" % b["stuff"])
        for res in b.get("resources") or []:
            still = res.get("stillNeeded", 0)
            usable = avail.get(res.get("defName"), (None, 0))[0]
            have_text = "?" if usable is None else str(usable)
            if not still:
                flag = "  (delivered)"
            elif _short(avail, res):
                flag = "  <-- SHORT: needs %d, colony has %s" % (still, have_text)
            else:
                # The blueprint owes its whole cost because nothing has been
                # hauled yet. That is a queue, not a shortage.
                flag = "  <-- not yet delivered (%s available in colony)" % have_text
            print("               %-24s delivered %d / need %d%s"
                  % (res.get("label") or res.get("defName"),
                     res.get("have", 0), res.get("need", 0), flag))
        if not (b.get("resources") or []):
            if b.get("isInstallBlueprint"):
                note = "a reinstall costs no materials -- it moves a thing that exists"
            elif b.get("materialCostUnreadable"):
                note = "cost list unreadable"
            else:
                note = "no material cost"
            print("               (%s)" % note)
        _inspect(b, "               ")
    print()


def deficit_block(r):
    """What the whole map still needs, and whether the colony owns it."""
    rows = r["resourceDeficit"]
    if not rows:
        pending = r["attention"]["blueprints"] + r["attention"]["frames"]
        if pending:
            print("RESOURCES STILL NEEDED: none -- all %d pending job(s) have their "
                  "materials. (checked)" % pending)
        else:
            print("RESOURCES STILL NEEDED: nothing is pending, so there is no "
                  "deficit to report. %s" % _why_empty(r, "built"))
        print()
        return
    print("RESOURCES STILL NEEDED (rolled up across all pending construction)")
    print("   'still needed' is what the SITES are owed. A blueprint owes its "
          "whole cost until the first haul, so this is a delivery queue unless "
          "the usable column is below it.")
    for d in rows:
        need = d.get("stillNeeded", 0)
        have = d.get("onMapTotal")
        usable = d.get("unforbiddenOnMap")
        counted = d.get("countedAsResource")
        # Only the number a hauler could actually fetch decides SHORT.
        decide = usable if usable is not None else have
        gap = ""
        if decide is not None and decide < need:
            gap = "   *** SHORT BY %d ***" % (need - decide)
        mark = "!!" if gap else "  "
        # Three numbers, three different questions: what a hauler can fetch,
        # what exists at all, and what RimWorld's own storage readout says.
        print("   %s %-24s needs %-6d across %d site(s)   usable %s / on map %s "
              "/ in storage %s%s"
              % (mark, d.get("label") or d.get("defName"), need, d.get("sites", 0),
                 "?" if usable is None else str(usable),
                 "?" if have is None else str(have),
                 "?" if counted is None else str(counted), gap))
        if usable is None:
            print("      (this DLL predates unforbiddenOnMap -- 'usable' is "
                  "unknown, so SHORT falls back to the whole-map total.)")
    print()


def bills_block(r):
    """Worktables and their queues. Item 3: an empty queue is a finding."""
    rows = [b for b in r["buildings"] if "bills" in b]
    if not rows:
        print("WORKTABLES: %s" % _why_empty(r, "blueprint"))
        print()
        return
    print("WORKTABLES -- %d" % len(rows))
    for b in rows:
        bills = b.get("bills") or []
        if not bills:
            head = "!! NO BILLS   "
        elif b.get("activeBillCount", 0) == 0:
            head = "!! NO ACTIVE  "
        else:
            head = "   %d active   " % b["activeBillCount"]
        print("%s %-30s %-9s  %d bill(s)"
              % (head, (b.get("label") or b.get("defName") or "?")[:30], _pos(b), len(bills)))
        for bill in bills:
            flags = []
            if bill.get("finished"):
                flags.append("FINISHED (0 left -- looks live, is not)")
            if bill.get("suspended"):
                flags.append("SUSPENDED")
            if bill.get("paused"):
                flags.append("paused")
            if bill.get("completableEver") is False:
                flags.append("NEVER COMPLETABLE")
            mark = "!!" if (bill.get("finished") or bill.get("suspended")) else "  "
            print("           %s  %-38s %-12s %s %s"
                  % (mark, (bill.get("label") or "?")[:38],
                     bill.get("repeatMode") or "?",
                     bill.get("repeatInfo") or "",
                     ("  " + ", ".join(flags)) if flags else ""))
        _inspect(b, "           ")
    print()


def power_block(r, warn=True):
    """Anything with a power component that is not actually powered.

    `warn=False` when `--power` has already printed the whole net table above
    and the one-line warnings would only repeat it."""
    rows = [b for b in r["buildings"]
            if b.get("power") and not b["power"].get("powered", True)]
    fuelless = [b for b in r["buildings"]
                if b.get("fuel") and not b["fuel"].get("hasFuel", True)]
    warnings = net_warning_lines(r) if warn else []
    if not rows and not fuelless:
        if r["counts"]["scanned"]:
            print("POWER: nothing unpowered, nothing out of fuel, across %d building(s) "
                  "scanned. (checked)" % r["counts"]["scanned"])
        else:
            print("POWER: %s" % _why_empty(r, "blueprint"))
        # An unpowered-building count of zero is NOT a clean grid: an isolated
        # battery draws nothing and so can never read unpowered.
        for line in warnings:
            print(line)
        if warn and not warnings and (r.get("powerSummary") or {}).get("readable"):
            print("   power grid: %d net(s), none flagged -- every net with a "
                  "building of ours on it has a generator and something to feed. "
                  "(checked; `--power` for the table)"
                  % (r["powerSummary"].get("netCount") or 0))
        print()
        return
    print("POWER / FUEL")
    for line in warnings:
        print(line)
    for b in rows:
        p = b["power"]
        why = []
        if not p.get("connected"):
            why.append("NOT CONNECTED TO POWER")
        if not p.get("switchedOn"):
            why.append("switched off")
        if p.get("brokenDown"):
            why.append("BROKEN DOWN")
        if not why:
            why.append("no power available on its net")
        print("   !! UNPOWERED  %-30s %-9s  %s%s"
              % ((b.get("label") or b.get("defName") or "?")[:30], _pos(b),
                 ", ".join(why), _fac(r, b)))
        _inspect(b, "                 ")
    for b in fuelless:
        print("   !! NO FUEL    %-30s %-9s  fuel %s / target %s%s"
              % ((b.get("label") or b.get("defName") or "?")[:30], _pos(b),
                 _num(b["fuel"].get("fuel"), "%.1f"),
                 _num(b["fuel"].get("targetFuelLevel"), "%.1f"), _fac(r, b)))
        _inspect(b, "                 ")
    print()


NET_FLAG_TEXT = {
    "noProducer": "NO GENERATOR on this net",
    "noConsumer": "nothing on this net draws power",
    "isolatedBattery": "battery/batteries with NO generator to charge them",
    "isolatedTransmitter": "conduits running to nothing at all",
}


def _net_names(net, role=None, cap=4):
    rows = [b for b in net.get("buildings") or []
            if role is None or b.get("role") == role]
    out = ", ".join("%s at %s,%s" % (b.get("label") or b.get("defName"),
                                     (b.get("position") or {}).get("x"),
                                     (b.get("position") or {}).get("z"))
                    for b in rows[:cap])
    if len(rows) > cap:
        out += " (+%d more)" % (len(rows) - cap)
    return out


def flagged_nets(r):
    """Every power net with a flag on it. [] when the read was clean."""
    return [n for n in r.get("powerNets") or [] if n.get("flags")]


def net_warning_lines(r):
    """The one-line-per-net warning the DEFAULT summary owes the reader.

    2026-09-07: `notConnectedToPower=0` was read as a clean grid for three
    turns while the hill pocket was cut off. A battery is a CompPowerBattery,
    not a CompPowerTrader -- it has no `powered` flag to be false -- so no
    per-building check on this map could ever have seen it. This one can."""
    summary = r.get("powerSummary")
    if summary is None:
        return ["   POWER GRID: this DLL predates powerNets (2026-09-07), so an "
                "ORPHANED BATTERY CANNOT BE SEEN by any check here. Rebuild and "
                "reinstall the companion."]
    if not summary.get("readable"):
        return ["   !! POWER GRID UNREADABLE -- %s. This is NOT 'the grid is fine'."
                % (summary.get("error") or "no reason given")]
    out = []
    for net in flagged_nets(r):
        why = "; ".join(NET_FLAG_TEXT.get(f, f) for f in net.get("flags"))
        who = _net_names(net, "battery") or _net_names(net)
        out.append("   !! POWER NET %d: %s -- %s. gen %sW / draw %sW, %s Wd stored."
                   % (net.get("index", -1), why, who or "no buildings named",
                      _num(net.get("generationW")), _num(net.get("consumptionW")),
                      _num(net.get("storedWd"), "%.1f")))
    return out


def power_nets_block(r):
    """`--power`: every net on the map, one row each, flags spelled out."""
    summary = r.get("powerSummary") or {}
    nets = r.get("powerNets")
    if nets is None:
        print("POWER NETWORKS: this DLL predates powerNets (2026-09-07). Rebuild "
              "and reinstall the companion; until then no check here can see an "
              "orphaned battery.")
        print()
        return
    if not summary.get("readable"):
        print("POWER NETWORKS: UNREADABLE -- %s. Not the same as 'no problems'."
              % (summary.get("error") or "no reason given"))
        print()
        return
    print("POWER NETWORKS -- %d net(s), %d flagged"
          % (summary.get("netCount", 0), summary.get("flaggedNetCount", 0)))
    if not nets:
        print("   none. (checked -- the map has no power network at all.)")
        print()
        return
    for net in nets:
        mark = "!!" if net.get("flags") else "  "
        print("   %s net %-3d  gen %-8s draw %-8s stored %-8s  "
              "%d gen / %d draw / %d battery / %d conduit  (%d ours of %d)"
              % (mark, net.get("index", -1),
                 _num(net.get("generationW")) + "W",
                 _num(net.get("consumptionW")) + "W",
                 _num(net.get("storedWd"), "%.1f") + "Wd",
                 net.get("producerCount", 0), net.get("consumerCount", 0),
                 net.get("batteryCount", 0), net.get("transmitterCount", 0),
                 net.get("playerBuildingCount", 0), net.get("buildingCount", 0)))
        for f in net.get("flags") or []:
            print("        !! %s -- %s" % (f, NET_FLAG_TEXT.get(f, "see INSTALL.md")))
        named = _net_names(net, cap=12)
        if named:
            print("        on it: %s" % named)
        if net.get("buildingsNotListed"):
            print("        (+%d more not listed)" % net["buildingsNotListed"])
    print("   a net with no building of OURS on it is never flagged: ancient "
          "ruins have dead conduit runs and flagging them would bury ours.")
    print()


def other_detail_block(r):
    """Detailed rows nothing above printed -- beds, turrets, damaged things.
    Present so that `buildings[]` is never partly rendered in silence."""
    shown = set()
    for b in r["buildings"]:
        if b.get("isBlueprint") or b.get("isFrame") or "bills" in b:
            shown.add(id(b))
        elif b.get("power") and not b["power"].get("powered", True):
            shown.add(id(b))
        elif b.get("fuel") and not b["fuel"].get("hasFuel", True):
            shown.add(id(b))
    rest = [b for b in r["buildings"] if id(b) not in shown]
    if not rest:
        return
    print("OTHER DETAILED ROWS -- %d (promoted out of the aggregate, listed so "
          "nothing is collapsed in silence)" % len(rest))
    for b in rest:
        bits = []
        if b.get("ownerNames") is not None:
            bits.append("owners: " + (", ".join(str(n) for n in b["ownerNames"]) or "UNASSIGNED"))
        if b.get("power"):
            bits.append("powered")
        hp, mx = b.get("hitPoints"), b.get("maxHitPoints")
        if hp is not None and mx:
            bits.append("hp %d/%d" % (hp, mx))
        print("   %-30s %-9s  %-24s %s%s"
              % ((b.get("label") or b.get("defName") or "?")[:30], _pos(b),
                 ",".join(str(x) for x in b.get("reasons") or []), "  ".join(bits),
                 _fac(r, b)))
        _inspect(b, "      ")
    print()


def _agg_notes(a):
    """The lines an aggregated row owes the reader when its count and its
    coordinates do not tell the same story (2026-09-02).

    `count: 11, 8 positions` was filed as a miscount and was not one -- it was
    the sample cap doing exactly what it says, with nothing in the row saying
    so. There are two ways a count can outrun its coordinates and they need
    different words, because raising `maxPositionsPerDef` fixes one of them and
    can never fix the other:

      * the cap cut the list  -- `positionsTruncated`, `positionsCap`
      * several buildings share one cell -- `count` above `distinctCells`

    Silent in the ordinary case: a row whose count, cap and cells all agree and
    that promoted nothing out gets no extra line at all."""
    out = []
    count, cap = a.get("count"), a.get("positionsCap")
    listed, distinct = a.get("positionsListed"), a.get("distinctCells")
    unreadable, promoted = a.get("positionsUnreadable") or 0, a.get("promotedOut") or 0
    if a.get("positionsTruncated"):
        if listed is not None and cap is not None and listed >= cap:
            out.append("       !! POSITIONS TRUNCATED -- %s of %s shown. The rest were cut by "
                       "the sample cap (maxPositionsPerDef=%s); they are on the map. "
                       "Pass --every for one row per building."
                       % (_num(listed), _num(count), _num(cap)))
        else:
            out.append("       !! POSITIONS TRUNCATED -- %s of %s shown, and NOT by the cap "
                       "(%s allowed): the rest had no readable position."
                       % (_num(listed), _num(count), _num(cap)))
    # distinctCells counts READABLE cells only -- an unreadable one is in count
    # and in neither list -- so the shared-cell arithmetic has to take the
    # unreadable ones out first or it reports sharing that isn't there.
    readable = None if count is None else count - unreadable
    if readable is not None and distinct is not None and readable > distinct:
        out.append("       !! %d of these share a cell with another -- %d building(s) standing "
                   "on %d distinct cell(s). Raising the cap will NEVER list %d "
                   "coordinates; that is the answer, not a shortfall."
                   % (readable - distinct, readable, distinct, readable))
    if unreadable:
        out.append("       !! %d position(s) UNREADABLE -- counted in the %s, but the game "
                   "would not give a cell for them, so no coordinate is printed."
                   % (unreadable, _num(count)))
    if promoted:
        # Not a fault, so no bang: the promoted ones are printed in full above.
        out.append("       + %d more of this def promoted to a row of its own above; %s in "
                   "total." % (promoted, _num((count or 0) + promoted)))
    return out


def built_block(r):
    rows = r["aggregated"]
    if not rows:
        if not r["filters"]["aggregate"]:
            print("BUILT (aggregated): aggregation is OFF (--every); every building "
                  "is an individual row above.")
        elif r["counts"]["detailed"] and not r["counts"]["aggregatedBuildings"]:
            print("BUILT (aggregated): no rows -- every built structure that matched "
                  "was promoted to a detail row above. Nothing is hidden.")
        else:
            print("BUILT (aggregated): %s" % _why_empty(r, "blueprint"))
        print()
        return
    print("BUILT -- grouped by def, %d row(s) covering %d building(s)"
          % (r["counts"]["aggregatedRows"], r["counts"]["aggregatedBuildings"]))
    if "positionsTruncated" not in rows[0]:
        # An older DLL: the row cannot say whether its positions were capped, so
        # this says it instead of printing None and letting the silence pass for
        # "nothing was cut". That substitution is the bug this block was fixed for.
        print("   (this DLL predates positionsTruncated / distinctCells, 2026-09-02 --"
              " a position list below may be a silently capped sample.)")
    for a in rows:
        pos = " ".join("%d,%d" % (p["x"], p["z"]) for p in a.get("positions") or [])
        more = " (+%d more)" % a["positionsNotListed"] if a.get("positionsNotListed") else ""
        dmg = ""
        if a.get("damaged"):
            dmg = "  !! %d damaged (worst %s%%)" % (a["damaged"], _num(a.get("worstHitPointsPct")))
        stuff = ""
        if a.get("stuff"):
            stuff = " of %s" % a["stuff"]
        elif a.get("stuffVaried"):
            stuff = " of mixed stuff"
        print("%5d  %-30s %-24s%s%s" % (a.get("count", 0),
                                        (a.get("label") or "")[:30],
                                        a.get("defName") or "?", stuff, dmg))
        if pos:
            print("       %s%s" % (pos, more))
        for line in _agg_notes(a):
            print(line)
    print()


def footer(r):
    a, c, s, f = r["attention"], r["counts"], r["skipped"], r["filters"]
    print("-- scanned %d, %d detailed, %d aggregated into %d rows"
          % (c["scanned"], c["detailed"], c["aggregatedBuildings"], c["aggregatedRows"]))
    # Every filter states itself, even when it removed nothing. The starvation
    # bug was never the filter -- it was the silence.
    print("   filters removed: %s" % ", ".join("%s %d" % (k, v) for k, v in sorted(s.items())))
    print("   scope: status=%s category=%s match=%r player=%s aggregate=%s%s"
          % (f["status"], f["category"], f["match"], f["playerOnly"], f["aggregate"],
             "" if not f["radius"] else " within %d of %d,%d" % (f["radius"], f["x"], f["z"])))
    # The faction filter gets its own line because it is the DEFAULT one: a
    # skipped ancient ruin has to be a printed number, never an absence.
    not_ours = s.get("byPlayerOnly")
    if f["playerOnly"]:
        if not_ours:
            print("   COLONY ONLY (the default): %d building(s) that are not ours were "
                  "SKIPPED -- ancient ruins, other factions, unclaimed. `--all` "
                  "includes them." % not_ours)
        elif not_ours == 0:
            if s.get("byStatus"):
                print("   COLONY ONLY (the default): it removed nothing among the "
                      "building(s) that matched status=%s. The %d removed by status "
                      "were not faction-checked." % (f["status"], s["byStatus"]))
            else:
                print("   COLONY ONLY (the default): it removed nothing -- every building "
                      "scanned is ours. (checked)")
        else:
            print("   COLONY ONLY (the default), but this DLL reported no byPlayerOnly "
                  "count, so how many were skipped is UNKNOWN -- not zero.")
    else:
        print("   WHOLE MAP (--all): ancient ruins, other factions and unclaimed "
              "structures are INCLUDED. The aggregated rows group by def only and "
              "do NOT separate factions; the detail rows carry [faction].")
    if (r.get("notes") or {}).get("naturalRockExcluded"):
        print("   natural rock is NOT included (pass --rock); it is excluded at the "
              "source, so there is no count of it to give.")
    print("   attention: %s" % ", ".join("%s=%d" % (k, v) for k, v in sorted(a.items())))
    # The --inspect accounting. A filter/option that changes the output states
    # itself even when it found nothing, and the three states of an inspect
    # string are counted separately because they mean three different things.
    if f.get("inspect"):
        rows = r["buildings"]
        got = sum(1 for b in rows if b.get("inspectString"))
        empty = sum(1 for b in rows if b.get("inspectString") == "")
        threw = sum(1 for b in rows if "inspectString" in b and b["inspectString"] is None)
        print("   inspect: %d of %d detailed row(s) had inspect-pane text, %d had "
              "none to give, %d could not be read. Aggregated rows never get one "
              "(--every for one per building)." % (got, len(rows), empty, threw))
        for miss in r.get("inspectSkipped") or []:
            print("   !! inspect UNREADABLE for %s x%s: %s"
                  % (miss.get("defName"), miss.get("count"), miss.get("error")))
    elif "inspect" in f:
        print("   inspect: NOT requested -- no row carries inspectString, which "
              "is not the same as 'nothing to show'. Pass --inspect.")
    else:
        print("   inspect: this DLL predates the inspect option (2026-09-03); "
              "--inspect will do nothing until it is rebuilt and reinstalled.")
    hot = sum(a[k] for k in ("pendingMissingResources", "unpowered", "outOfFuel",
                             "brokenDown", "finishedBills", "billGiversWithNoBills"))
    print("   ==> %d thing(s) flagged !! above." % hot if hot
          else "   ==> nothing flagged in status=%s. Checked, not assumed." % f["status"])


# ------------------------------------------------------- addressing one thing --
#
# 2026-09-07, turns 34/39: `buildings.py --inspect "x,z"` returned the full
# "none matched, 637 removed by filters" block for a coordinate, so MISUSE READ
# AS DATA; and `--inspect "Shelf@115,154"` said none matched for a shelf
# `buildings.py gizmos` resolved instantly at 114,154. Both had the same cause:
# a bare positional is a --match SUBSTRING, and `115,154` is not a substring of
# anything. The listing side now understands the same `<thing>` spellings the
# gizmo side does, and a cell matches any cell the building OCCUPIES -- a wooden
# shelf is two cells and its anchor is only one of them.

TARGET_PAD = 6          # cells searched around a target cell; the largest
                        # vanilla building footprint fits well inside this.


def _cell(text):
    """`"115,154"` -> (115, 154). None when it is not a bare cell."""
    parts = str(text).split(",")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0].strip()), int(parts[1].strip())
    except ValueError:
        return None


def parse_target(word):
    """A positional as an ADDRESS rather than a substring, or None.

    Three forms, exactly the ones `buildings.py gizmos` accepts:
      `115,154`         anything at that cell
      `Shelf@115,154`   that def/label at that cell
      `1234` / `Thing_Shelf1234`   one thing by id
    Anything else is None, and stays a --match substring as it always was.
    """
    text = str(word).strip()
    cell = _cell(text)
    if cell:
        return {"kind": "cell", "x": cell[0], "z": cell[1], "match": None,
                "spec": text}
    if "@" in text:
        head, _, tail = text.rpartition("@")
        cell = _cell(tail)
        if head and cell:
            return {"kind": "cell", "x": cell[0], "z": cell[1], "match": head,
                    "spec": text}
        return {"kind": "bad", "spec": text,
                "why": "%r looks like DefName@x,z but %r is not a cell. Two "
                       "integers, e.g. Shelf@115,154." % (text, tail)}
    if text.isdigit() or text.lower().startswith("thing_"):
        return {"kind": "thing", "id": text, "spec": text}
    return None


def _norm_id(value):
    """`Thing_Shelf123`, `shelf123` and `Shelf123` are one id (pick.py's rule)."""
    if value is None:
        return None
    text = str(value).strip().lower()
    return text[6:] if text.startswith("thing_") else text


def _covers(b, x, z):
    """Does this building occupy that cell? Its ANCHOR is not its footprint.

    `occupies` is present only when the footprint exceeds 1x1; its absence
    means one cell at `position`, which the payload says outright."""
    pos = b.get("position") or {}
    if pos.get("x") == x and pos.get("z") == z:
        return True
    occ = b.get("occupies")
    if not occ:
        return False
    return (occ.get("minX", 1) <= x <= occ.get("maxX", -1)
            and occ.get("minZ", 1) <= z <= occ.get("maxZ", -1))


def _matches_text(b, text):
    want = text.lower()
    for key in ("defName", "label", "buildDefName", "buildLabel"):
        if want in str(b.get(key) or "").lower():
            return True
    return False


def target_rows(r, target):
    """The rows of `r` this target addresses. Never guesses."""
    if target["kind"] == "thing":
        want = _norm_id(target["id"])
        return [b for b in r["buildings"]
                if _norm_id(b.get("thingId")) == want
                or _id_number(b) == target["id"]]
    rows = [b for b in r["buildings"] if _covers(b, target["x"], target["z"])]
    if target.get("match"):
        rows = [b for b in rows if _matches_text(b, target["match"])]
    return rows


def _id_number(b):
    """The trailing digits of a ThingID -- what build.py prints as an id."""
    text = str(b.get("thingId") or "")
    digits = ""
    for ch in reversed(text):
        if not ch.isdigit():
            break
        digits = ch + digits
    return digits


def target_block(r, target, rows):
    """One thing, in full. The answer to "what is at this cell"."""
    where = ("cell %d,%d" % (target["x"], target["z"])
             if target["kind"] == "cell" else "thing %s" % target["spec"])
    if target.get("match"):
        where += " matching %r" % target["match"]
    if not rows:
        scanned = r["counts"]["scanned"]
        print("NOTHING AT %s -- no building of ours was found there." % where)
        status = (r.get("filters") or {}).get("status", "all")
        print("   %d building(s) were scanned in this call%s. This is an "
              "ADDRESS that resolved to nothing, not a filter that hid "
              "something: `--all` widens to other factions and ruins, `--rock` "
              "adds natural rock.%s"
              % (scanned, "" if target["kind"] == "thing"
                 else " (everything within %d cells of %d,%d)"
                      % (TARGET_PAD, target["x"], target["z"]),
                 "" if status == "all" else
                 " status=%s was also asked for, so anything there of another "
                 "status was excluded." % status))
        print()
        return 1
    print("AT %s -- %d building(s)" % (where, len(rows)))
    for b in rows:
        occ = b.get("occupies")
        foot = ("  occupies %d,%d..%d,%d" % (occ["minX"], occ["minZ"],
                                             occ["maxX"], occ["maxZ"])
                if occ else "  one cell")
        print("   %-30s %-24s %-9s %-9s%s"
              % ((b.get("label") or "?")[:30], b.get("defName") or "?",
                 _pos(b), b.get("status") or "?", foot))
        print("       thingId %s   rotation %s   stuff %s   reasons %s"
              % (b.get("thingId"), b.get("rotation"), b.get("stuff") or "-",
                 ",".join(str(x) for x in b.get("reasons") or []) or "-"))
        hp, mx = b.get("hitPoints"), b.get("maxHitPoints")
        if hp is not None and mx:
            print("       hp %d/%d" % (hp, mx))
        if b.get("power"):
            pw = b["power"]
            print("       power: powered %s, connected %s, switch %s, broken %s, "
                  "output %sW"
                  % (pw.get("powered"), pw.get("connected"), pw.get("switchedOn"),
                     pw.get("brokenDown"), _num(pw.get("powerOutput"))))
        if b.get("ownerNames") is not None:
            print("       owners: %s"
                  % (", ".join(str(n) for n in b["ownerNames"]) or "UNASSIGNED"))
        for bill in b.get("bills") or []:
            print("       bill: %s  %s %s"
                  % (bill.get("label"), bill.get("repeatMode"),
                     bill.get("repeatInfo") or ""))
        for res in b.get("resources") or []:
            print("       %-24s delivered %d / need %d"
                  % (res.get("label") or res.get("defName"),
                     res.get("have", 0), res.get("need", 0)))
        _inspect(b, "       ")
    print()
    for line in net_warning_lines(r):
        print(line)
    return 0


def show(r):
    status = r["filters"]["status"]
    if status in ("all", "pending", "blueprint", "frame"):
        pending_block(r)
        deficit_block(r)
    if status in ("all", "built"):
        bills_block(r)
        power_block(r)
        # 2026-09-07: with --every the per-building rows printed ABOVE the
        # `BUILT (aggregated)` header, so grepping from that header found
        # nothing. With aggregation off the header comes first and the rows
        # belong under it; with it on there is a real aggregate table below.
        if r["filters"]["aggregate"]:
            other_detail_block(r)
            built_block(r)
        else:
            built_block(r)
            other_detail_block(r)
    footer(r)



# ------------------------------------------------------- the write side ---

CONFIG_TOOL = "home/building_config"

CONFIG_HELP = (
    "\n  home/building_config is the write side of home/list_buildings. Same"
    "\n  checks as above: game running, companion DLL installed, RimWorld"
    "\n  RESTARTED since it was installed." + HELP)


def configure(**kw):
    """One call to home/building_config. Returns the raw reply, refusals and all.

    Called with strict=False on purpose: a refusal here is an answer with a
    reason in it ("that name matches four buildings", "this room cannot be a
    prison cell"), and the reason is the whole point of asking.
    """
    try:
        r = rim.game(CONFIG_TOOL, kw, strict=False)
    except Exception as e:
        raise rim.BridgeError("%s failed: %s: %s%s"
                              % (CONFIG_TOOL, type(e).__name__, e, CONFIG_HELP))
    if not isinstance(r, dict):
        raise rim.BridgeError("%s answered with %r, not a payload.%s"
                              % (CONFIG_TOOL, type(r).__name__, CONFIG_HELP))
    return r


def _val(v):
    """One field value as a line. before/after are not all booleans: `power`
    is the flick trio and `owner` is a list of pawn rows, and printing either
    as its repr is how a reader stops reading."""
    if v is None:
        return "?"
    if isinstance(v, bool):
        return "on" if v else "off"
    if isinstance(v, list):
        return ", ".join(_val(x) for x in v) if v else "(nobody)"
    if isinstance(v, dict):
        if "name" in v:
            return str(v["name"])
        return " ".join("%s=%s" % (k, _val(v[k])) for k in sorted(v))
    return str(v)


def _refusal(r):
    """A tool-level refusal, with its reason and any candidates. Returns 1 so a
    caller can `return _refusal(r)`."""
    print("REFUSED: %s" % (r.get("error") or r.get("message") or r))
    for c in r.get("candidates") or []:
        p = c.get("position") or {}
        print("   candidate  %-28s %-24s %s,%s   thingId %s"
              % ((c.get("label") or "?")[:28], c.get("defName") or "?",
                 p.get("x", "?"), p.get("z", "?"), c.get("thingId")))
    return 1


def _config_state(c):
    """The writable configuration in four lines. A capability the thing does
    not have prints as such -- never as a false setting."""
    print("   forbidden: %s" % (_val(c.get("forbidden")) if c.get("forbiddable")
                                else "n/a (no CompForbiddable)"))
    powerbit = ("" if not c.get("hasPower") else
                "   powered %s, connected %s"
                % (_val(c.get("powered")), _val(c.get("connected"))))
    if c.get("flickable"):
        print("   power:     want %s, switch %s, flick designated %s%s"
              % (_val(c.get("wantSwitchOn")), _val(c.get("switchIsOn")),
                 _val(c.get("flickDesignated")), powerbit))
    else:
        print("   power:     n/a (no switch)%s" % powerbit)
    if c.get("temperatureControl"):
        print("   temperature: %s C" % _val(c.get("targetTemperature")))
    if c.get("isBed"):
        print("   bed:       medical %s (canBeMedical %s), forPrisoners %s, ownerType %s"
              % (_val(c.get("medical")), _val(c.get("canBeMedical")),
                 _val(c.get("forPrisoners")), _val(c.get("bedOwnerType"))))
    if c.get("assignable"):
        print("   owners:    %s  (max %s)"
              % (_val(c.get("assignedPawns")), _val(c.get("maxAssignedPawns"))))


MINIFIED = "minifiedthing"


def _map_target(spec):
    """Any thing on the map, by id -- for what building_config's scope misses.

    `home/building_config` addresses the player faction's BUILDINGS, blueprints
    and frames. A packed-up turret is a `MinifiedThing`: an ITEM lying on the
    floor. So both `gizmos` and `gizmo` refused a MinifiedThing id with "No
    colony building matches" (turns 12 and 13) although selecting one is
    exactly what `mini_install.py` does. `rimworld/get_map_target_info`
    resolves any thing on the map, both id spellings. -> the target, or None.
    """
    if not spec or any(ch in str(spec) for ch in "@,"):
        return None                      # a cell or DefName@x,z is not a thing id
    return pick.resolve(spec)


def _is_minified(target):
    return MINIFIED in str((target or {}).get("className") or "").lower()


def _fire_from_bar(rows, label, do, name, thing_id, expect_count=None):
    """Print the selected thing's bar and, with a label, fire exactly one.

    `rows` came from `rimworld/list_selected_gizmos` on a selection that has
    already been verified to BE `thing_id` -- that check is the fix for turn
    14, where 12 plainleather on a turret's tile gave the write path a
    one-gizmo bar while the read path listed seven.
    """
    if not rows:
        print("   !! the selection carries NO gizmos even though it is the "
              "thing asked for. Nothing was fired.")
        return 1
    if expect_count is not None and expect_count != len(rows):
        print("   !! the read path (home/building_config) counted %s gizmo(s) "
              "and the selection carries %d. Something else may share this "
              "tile -- `inv.py` lists what is lying on it."
              % (expect_count, len(rows)))
    want = (label or "").strip().lower()
    hits = [g for g in rows if want in (g.get("label") or "").lower()] if label else []
    for g in rows:
        mark = "->" if g in hits else "  "
        print("   %s %-34s %s%s"
              % (mark, (g.get("label") or "(unlabelled)")[:34], g.get("id"),
                 "   DISABLED: %s" % (g.get("disabledReason") or "no reason given")
                 if g.get("disabled") else ""))
    if label is None:
        print("-- %d gizmo(s) on the bar, read through the selection. Nothing "
              "was fired." % len(rows))
        return 0
    enabled = [g for g in hits if not g.get("disabled")]
    if len(enabled) != 1:
        print("   REFUSED: %r matched %d enabled gizmo(s) of %d on the bar; "
              "name one exactly (the bar is printed above). The selection is "
              "still up." % (label, len(enabled), len(rows)))
        return 1
    hit = enabled[0]
    if not do:
        print("   DRY RUN -- nothing was fired. WOULD FIRE %r (id %s) on %s "
              "[%s]. Add --do." % (hit.get("label"), hit.get("id"), name, thing_id))
        return 0
    # Last check before the write: the id in hand is only valid while the
    # selection that produced it stands, and listing the bar is itself a call.
    now = pick.selection()
    if not pick.holds(now, thing_id):
        print("   REFUSED: the selection changed between listing the bar and "
              "firing -- it is now %s, expected %s [%s]. Nothing was fired."
              % (now["text"], name, thing_id))
        return 1
    out = rim.game("rimworld/execute_gizmo", {"gizmoId": hit.get("id")},
                   strict=False)
    if isinstance(out, dict) and out.get("success") is False:
        print("   REFUSED: %s" % (out.get("message") or out.get("error")
                                  or "execute_gizmo refused, no reason given"))
        return 1
    print("   FIRED %r (id %s) on %s [%s]."
          % (hit.get("label"), hit.get("id"), name, thing_id))
    print("   The selection is left up on purpose: a gizmo like 'Reinstall "
          "at...' arms a targeter that needs it. Read the result with "
          "`buildings.py --pending`.")
    return 0


def _bar_via_selection(target, label, do=False, as_json=False):
    """The bar of a thing `home/building_config` will not address at all."""
    tid = target.get("thingId") or target.get("id")
    p = target.get("position") or {}
    name = target.get("label") or target.get("defName") or tid
    print("GIZMO%s -- %s (%s) at %s,%s   thingId %s"
          % ("" if label else "S", name, target.get("defName"),
             p.get("x"), p.get("z"), tid))
    print("   not a colony building (%s), so the bar is read through the "
          "SELECTION, not home/building_config." % target.get("className"))
    if _is_minified(target):
        print("   packed-up furniture: `python mini_install.py %s --to <x> <z> "
              "--do` selects it, fires Install and places it." % tid)
    if not target.get("spawned", True):
        print("   REFUSED: this thing is not spawned on the map (it is inside "
              "something), so there is no cell to click.")
        return 1
    try:
        pick.select_thing(tid, p.get("x"), p.get("z"), label=name)
    except (pick.ClickMissed, pick.CameraStuck) as e:
        print("   REFUSED: %s" % e)
        return 1
    listed = rim.game("rimworld/list_selected_gizmos", {}, strict=False)
    rows = (listed.get("gizmos") or []) if isinstance(listed, dict) else []
    if as_json:
        print(json.dumps({"target": target, "selectedGizmos": rows}, indent=1))
        return 0 if rows else 1
    return _fire_from_bar(rows, label, do, name, tid)


def gizmos_cmd(spec, as_json=False):
    """Every gizmo the game would draw on this thing's bar, read and not fired."""
    r = configure(thing=spec, gizmos=True)
    if not r.get("success"):
        target = _map_target(spec)
        if target is not None:
            return _bar_via_selection(target, None, do=False, as_json=as_json)
    if as_json:
        print(json.dumps(r, indent=1))
        return 0 if r.get("success") else 1
    if not r.get("success"):
        return _refusal(r)
    t, rows = r["thing"], r.get("gizmos")
    p = t.get("position") or {}
    print("GIZMOS -- %s (%s) at %s,%s   thingId %s"
          % (t.get("label"), t.get("defName"), p.get("x"), p.get("z"), t.get("thingId")))
    if rows is None:
        print("   (this DLL returned no gizmos list at all -- rebuild and reinstall it.)")
        return 1
    for g in rows:
        active = g.get("isActive")
        box = "   " if active is None else ("[x]" if active else "[ ]")
        flag = ""
        if g.get("disabled"):
            flag = "   DISABLED: %s" % (g.get("disabledReason") or "no reason given")
        elif g.get("disabledReason"):
            flag = "   (%s)" % g["disabledReason"]
        print("   %s %-34s %-22s%s"
              % (box, (g.get("label") or "(not a Command)")[:34], g.get("type") or "?", flag))
    print("-- %d gizmo(s) on the bar. Writable from here: --forbid, --power, "
          "--temperature, --medical, --prisoners, --owner; anything else is listed only."
          % (r.get("gizmoCount") or 0))
    _config_state(r["after"])
    return 0


def gizmo_cmd(spec, label, do=False, as_json=False):
    """Select the thing, list its bar, fire ONE gizmo by label -- one process.

    `gizmos` reads the bar through home/building_config, which returns no ids
    and fires nothing. The ids `rimworld/execute_gizmo` takes come from
    `rimworld/list_selected_gizmos`, and they live only as long as the
    selection that produced them -- which no process can hand to the next. So
    select, list and execute here, in that order, in one call. Turret
    `Uninstall` and `Reinstall at...` are the motivating cases.
    """
    r = configure(thing=spec, gizmos=True)
    if not r.get("success"):
        target = _map_target(spec)
        if target is not None:
            return _bar_via_selection(target, label, do=do, as_json=as_json)
        return _refusal(r)
    t = r["thing"]
    p = t.get("position") or {}
    if p.get("x") is None or p.get("z") is None:
        print("REFUSED: %s (%s) has no readable position, so it cannot be "
              "selected." % (t.get("label"), t.get("defName")))
        return 1
    tid = t.get("thingId")
    if not as_json:
        print("GIZMO -- %s (%s) at %d,%d   thingId %s"
              % (t.get("label"), t.get("defName"), p["x"], p["z"], tid))
    # THE fix for turn 14. The read path above addresses the thing by id; this
    # path can only click a cell, and a cell holds whatever is lying on it --
    # 12 plainleather on a turret's tile gave the write path a one-gizmo bar.
    # `pick.select_thing` drops an armed designator, puts the camera on the
    # cell (a click outside the frame does nothing and reports success),
    # cycles the stack, and refuses unless the selected id IS this thing.
    try:
        pick.select_thing(tid, p["x"], p["z"], label=t.get("label"),
                          announce=not as_json)
    except (pick.ClickMissed, pick.CameraStuck) as e:
        if as_json:
            print(json.dumps({"thing": t, "selectedGizmos": [],
                              "error": str(e)}, indent=1))
            return 1
        print("   REFUSED: %s" % e)
        print("   Nothing was fired. If something is lying on this tile, haul "
              "it off (`inv.py` at %d,%d lists it) and try again."
              % (p["x"], p["z"]))
        return 1
    listed = rim.game("rimworld/list_selected_gizmos", {}, strict=False)
    rows = (listed.get("gizmos") or []) if isinstance(listed, dict) else []
    if as_json:
        print(json.dumps({"thing": t, "selectedGizmos": rows}, indent=1))
        return 0 if rows else 1
    return _fire_from_bar(rows, label, do, t.get("label"), tid,
                          expect_count=r.get("gizmoCount"))


def reinstall_cmd(spec, x, z, do=False):
    """Move a building whole: fire `Reinstall at...` and click the destination.

    A shelf has NO `Uninstall` gizmo -- only `Reinstall at...`, which arms a
    placement designator that needs a click, and the click is a click: it lands
    only on a cell the camera is looking at. That is the same two-camera
    operation `mini_install.py` performs for packed furniture, so the recipe is
    the same one: select the thing at ITS cell, fire the gizmo, move the camera
    to the DESTINATION, click, and read the destination cell back off the map
    rather than trusting the click's own `success`.

    Read-only without `--do`, which is the whole call except the two clicks.
    """
    r = configure(thing=spec, gizmos=True)
    if not r.get("success"):
        return _refusal(r)
    t = r["thing"]
    p = t.get("position") or {}
    tid = t.get("thingId")
    if p.get("x") is None or p.get("z") is None:
        print("REFUSED: %s has no readable position, so it cannot be selected."
              % t.get("label"))
        return 1
    rows = r.get("gizmos") or []
    hits = [g for g in rows
            if (g.get("label") or "").strip().lower().startswith("reinstall")]
    print("REINSTALL -- %s (%s) at %d,%d  thingId %s   ->   %d,%d"
          % (t.get("label"), t.get("defName"), p["x"], p["z"], tid, x, z))
    if not hits:
        print("   REFUSED: this thing has no `Reinstall at...` gizmo. Its bar "
              "holds: %s" % (", ".join((g.get("label") or "?") for g in rows)
                             or "nothing"))
        print("   Not every building can be moved whole. `buildings.py gizmos "
              "%s` is the full bar." % spec)
        return 1
    if hits[0].get("disabled"):
        print("   REFUSED: %r is DISABLED: %s"
              % (hits[0].get("label"),
                 hits[0].get("disabledReason") or "no reason given"))
        return 1
    if not do:
        print("   DRY RUN -- nothing was fired and no click was sent. WOULD fire "
              "%r, move the camera to %d,%d and click there, then read the cell "
              "back. Add --do." % (hits[0].get("label"), x, z))
        return 0
    before = _blueprint_at(x, z)
    try:
        pick.select_thing(tid, p["x"], p["z"], label=t.get("label"))
    except (pick.ClickMissed, pick.CameraStuck) as e:
        print("   REFUSED: %s -- nothing was fired." % e)
        return 1
    listed = rim.game("rimworld/list_selected_gizmos", {}, strict=False)
    live = (listed.get("gizmos") or []) if isinstance(listed, dict) else []
    armed = [g for g in live
             if (g.get("label") or "").strip().lower().startswith("reinstall")
             and not g.get("disabled") and g.get("id")]
    if len(armed) != 1:
        print("   REFUSED: the selection carries %d enabled Reinstall gizmo(s), "
              "not 1. Nothing was fired." % len(armed))
        return 1
    now = pick.selection()
    if not pick.holds(now, tid):
        print("   REFUSED: the selection is %s, not %s. Nothing was fired."
              % (now["text"], tid))
        return 1
    out = rim.game("rimworld/execute_gizmo", {"gizmoId": armed[0]["id"]},
                   strict=False)
    if isinstance(out, dict) and out.get("success") is False:
        print("   REFUSED: %s" % (out.get("message") or out.get("error")
                                  or "execute_gizmo refused, no reason given"))
        return 1
    print("   fired %r; the placement designator is armed."
          % armed[0].get("label"))
    # The second camera position of the operation. A click at a cell the camera
    # is not looking at does nothing and still reports success.
    try:
        pick.ensure_camera(x, z, reason="buildings.py reinstall -> %d,%d" % (x, z))
    except pick.CameraStuck as e:
        print("   !! %s. The designator is STILL ARMED -- `python act.py clear` "
              "drops it. Nothing was placed." % e)
        return 1
    rim.game("rimworld/click_cell", {"x": int(x), "z": int(z)}, strict=False)
    time.sleep(0.4)
    after = _blueprint_at(x, z)
    if after and after != before:
        print("   PLACED: %s is now an install blueprint at %d,%d (%s). A "
              "builder has to carry it; `buildings.py --pending` tracks it."
              % (t.get("label"), x, z, after))
        return 0
    print("   !! the placing click at %d,%d left NO blueprint there (the cell "
          "reads %s). The designator may still be armed -- `python act.py clear` "
          "drops it. Nothing else was changed." % (x, z, after or "empty"))
    return 1


def _blueprint_at(x, z):
    """A blueprint or frame standing on that cell, or None. Read off the map:
    a placement click that missed reports success exactly like one that landed."""
    r = rim.game("home/get_cells_plus",
                 {"x": int(x), "z": int(z), "width": 1, "height": 1,
                  "fields": "things",
                  "thingFields": "defName,label,isBlueprint,isFrame",
                  "sparse": True}, strict=False)
    if not isinstance(r, dict) or not r.get("success"):
        return None
    for c in r.get("cells") or []:
        for t in c.get("things") or []:
            if t.get("isBlueprint") or t.get("isFrame"):
                return t.get("label") or t.get("defName") or "a blueprint"
    return None


def set_cmd(spec, kw, do=False, watch=True, as_json=False):
    """Set any subset of the four gizmos with dialogs. Dry run until --do."""
    args = dict(kw)
    args["thing"] = spec
    args["dryRun"] = not do
    if not watch:
        args["watch"] = False
    r = configure(**args)
    if as_json:
        print(json.dumps(r, indent=1))
        return 0 if r.get("success") else 1
    if not r.get("success"):
        return _refusal(r)

    t = r["thing"]
    p = t.get("position") or {}
    head = "APPLIED" if not r["dryRun"] else "DRY RUN -- nothing was written"
    print("%s: %s (%s) at %s,%s   thingId %s"
          % (head, t.get("label"), t.get("defName"), p.get("x"), p.get("z"),
             t.get("thingId")))
    for f in r["fields"]:
        if f.get("refused"):
            print("   !! REFUSED  %-12s %s"
                  % (f["field"], f.get("reason") or "no reason given"))
            continue
        mark = "->" if f.get("changed") else "  "
        print("   %s %-12s %s  ->  %s%s"
              % (mark, f["field"], _val(f.get("before")), _val(f.get("after")),
                 "" if f.get("changed") else "   (no change)"))
        for key in ("ownersDropped", "unassignedFrom", "evicts"):
            if f.get(key):
                print("        %s: %s" % (key, _val(f[key])))
        if f.get("note"):
            print("        %s" % f["note"])
    if not r["fields"]:
        print("   (no field named -- pass --forbid / --power / --medical / "
              "--prisoners / --owner. Current state:)")
    _config_state(r["after"])
    w = r.get("watch") or {}
    print("-- %d changed, %d refused. watch: %s"
          % (r["changeCount"], r["refusedCount"],
             "shown" if w.get("shown") else "not shown (%s)" % w.get("reason")))
    if r["dryRun"] and r["fields"] and r["refusedCount"] < len(r["fields"]):
        print("   Nothing was written. Add --do to apply it.")
    return 0


SET_FLAGS = {"--forbid": "forbidden", "--medical": "medical",
             "--prisoners": "forPrisoners"}


def _config_argv(argv):
    """-> (thing, {tool kwargs}) or (None, "what was wrong with the line")."""
    if not argv:
        return None, "needs a <thing>: a ThingID, DefName@x,z, or a unique substring."
    spec, rest, kw = argv[0], argv[1:], {}
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in ("--do", "--no-watch", "--json"):
            i += 1
            continue
        if a in SET_FLAGS or a in ("--power", "--owner", "--temperature"):
            if i + 1 >= len(rest):
                return None, "%s needs a value." % a
            value = rest[i + 1]
            if a in SET_FLAGS:
                if value not in ("on", "off"):
                    return None, "%s takes on or off, not %r." % (a, value)
                kw[SET_FLAGS[a]] = value == "on"
            elif a == "--power":
                if value not in ("on", "off"):
                    return None, "--power takes on or off, not %r." % value
                kw["power"] = value
            elif a == "--temperature":
                try:
                    kw["temperature"] = float(value)
                except ValueError:
                    return None, "--temperature takes a number in Celsius, not %r." % value
            else:
                kw["owner"] = value
            i += 2
            continue
        return None, "unknown option %r." % a
    return spec, kw


def main():
    argv = sys.argv[1:]
    kw = {}

    if "--help" in argv or "-h" in argv:
        # Answered before rim.init(): asking what the flags are must never cost
        # a bridge call, and it used to fall through to a live read.
        print(__doc__)
        return 0

    # The two write-side subcommands, checked before the listing flags: a bare
    # word is otherwise read as a --match filter, and `set` is not a filter.
    if argv and argv[0] == "gizmo":
        # Its own branch: the second positional is a gizmo LABEL, not an option,
        # and _config_argv would read it as one.
        rest = argv[1:]
        words = [a for a in rest if not a.startswith("-")]
        if len(words) < 2:
            print('buildings.py gizmo needs <thing> "<gizmo label>" [--do]')
            return 1
        try:
            rim.init()
            return gizmo_cmd(words[0], words[1], do="--do" in rest,
                             as_json="--json" in rest)
        except Exception as e:
            print("buildings.py gizmo FAILED -- NOTHING WAS READ OR WRITTEN.")
            print("%s: %s" % (type(e).__name__, e))
            return 1

    if argv and argv[0] == "reinstall":
        rest = argv[1:]
        words = [a for a in rest if not a.startswith("-")]
        if len(words) < 3 or not words[1].lstrip("-").isdigit() \
                or not words[2].lstrip("-").isdigit():
            print("buildings.py reinstall <thing> <x> <z> [--do]")
            return 1
        try:
            rim.init()
            return reinstall_cmd(words[0], int(words[1]), int(words[2]),
                                 do="--do" in rest)
        except Exception as e:
            print("buildings.py reinstall FAILED.")
            print("%s: %s" % (type(e).__name__, e))
            return 1

    if argv and argv[0] in ("gizmos", "set"):
        verb, rest = argv[0], argv[1:]
        spec, fields = _config_argv(rest)
        if spec is None:
            print("buildings.py %s %s" % (verb, fields))
            return 1
        as_json = "--json" in rest
        try:
            rim.init()
            if verb == "gizmos":
                return gizmos_cmd(spec, as_json=as_json)
            return set_cmd(spec, fields, do="--do" in rest,
                           watch="--no-watch" not in rest, as_json=as_json)
        except Exception as e:
            print("buildings.py %s FAILED -- NOTHING WAS READ OR WRITTEN." % verb)
            print("%s: %s" % (type(e).__name__, e))
            return 1

    if "--pending" in argv:
        kw["status"] = "pending"
    if "--blueprints" in argv:
        kw["status"] = "blueprint"
    if "--frames" in argv:
        kw["status"] = "frame"
    if "--built" in argv:
        kw["status"] = "built"
    if "--rock" in argv:
        kw["category"] = "all"
    if "--all" in argv:
        kw["playerOnly"] = False
    # `--player` was the old opt-IN and is now the default. Accepted so an old
    # command line still means what it meant; it does nothing.
    if "--every" in argv:
        kw["aggregate"] = False
    if "--inspect" in argv:
        kw["inspect"] = True
    if "--damaged-below" in argv:
        i = argv.index("--damaged-below")
        if i + 1 < len(argv) and argv[i + 1].lstrip("-").isdigit():
            kw["damagedBelowPct"] = int(argv[i + 1])
            del argv[i:i + 2]
        else:
            print("--damaged-below needs a percentage, e.g. --damaged-below 50")
            return 1
    if "--near" in argv:
        i = argv.index("--near")
        nums = [int(v) for v in argv[i + 1:i + 4] if v.lstrip("-").isdigit()]
        if len(nums) != 3:
            print("--near needs x z radius")
            return 1
        kw["x"], kw["z"], kw["radius"] = nums
        del argv[i:i + 4]
    want_power = "--power" in argv
    # A bare positional is a --match substring UNLESS it is an address. A
    # coordinate is an address, and answering it with "none matched, 637
    # removed by filters" is how misuse reads as data.
    target = None
    words = [a for a in argv if not a.startswith("-")]
    if words:
        target = parse_target(words[0])
        if target and target["kind"] == "bad":
            print("buildings.py: %s" % target["why"])
            return 2
        if target is None:
            kw["match"] = words[0]

    try:
        rim.init()
        if target is not None and target["kind"] == "thing":
            # One id: let the write side's resolver -- which already takes every
            # id spelling, the bare thingIDNumber included -- turn it into a
            # cell, then ask the same cell question.
            found = configure(thing=target["id"])
            if not found.get("success"):
                return _refusal(found)
            at = (found.get("thing") or {}).get("position") or {}
            if at.get("x") is None or at.get("z") is None:
                print("REFUSED: %s resolved to %s, which has no readable "
                      "position, so there is no cell to look at."
                      % (target["spec"], (found.get("thing") or {}).get("label")))
                return 1
            target = {"kind": "cell", "x": at["x"], "z": at["z"],
                      "match": None, "spec": target["spec"]}
        if target is not None:
            kw["x"], kw["z"], kw["radius"] = target["x"], target["z"], TARGET_PAD
            kw["aggregate"] = False
            kw["inspect"] = True
        r = survey(**kw)
    except Exception as e:
        # LOUD. Never an empty list: an empty list here reads as "nothing is
        # under construction", which is the one wrong answer that matters.
        print("buildings.py FAILED -- NO BUILDING DATA WAS READ.")
        print("%s: %s" % (type(e).__name__, e))
        return 1

    if "--json" in argv:
        print(json.dumps(r, indent=1))
        return 0
    if target is not None:
        return target_block(r, target, target_rows(r, target))
    if want_power:
        power_nets_block(r)
        power_block(r, warn=False)
        footer(r)
        return 0
    show(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
