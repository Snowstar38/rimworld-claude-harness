"""Manual data-scout helpers and legacy report inspection.

  python rota.py reports     # receive current reports; starts nothing
  python rota.py tick        # compatibility alias for reports
  python rota.py status      # read clocks and next reader
  python rota.py daemon      # disabled compatibility entry point
  python rota.py reset       # reset clocks for a new play session
  python rota.py buildings|colonists|rooms|selftest

The automatic path lives entirely in rota_service.py: one blind screenshot
reviewer at service launch and every fixed 180-second wall slot thereafter.
It does not call this module's seeded Scout scheduler. Automatic reports publish
straight to event_bus; `reports` exists only for legacy/manual mailboxes.
"""
import json
import os
import subprocess
import sys
import time
import game_session
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
ROTA = os.path.join(STATE, "rota.json")
LOOKOUT = os.path.join(STATE, "lookout-latest.txt")
SCOUTOUT = os.path.join(STATE, "scout-latest.txt")
HANDS = os.path.join(STATE, "hands-last.md")
LOOKLOG = os.path.join(STATE, "look-spawn.log")
SCOUTLOG = os.path.join(STATE, "scout-spawn.log")

# Sol's scouts run on the same codex binary as look.py's, with no --model: the
# default is Sol's model, which is how run-sol.ps1 invokes it.
CODEX = os.path.join(os.path.dirname(os.path.dirname(HERE)), "tools", "codex", "codex.exe")
SCOUT_TIMEOUT = 240.0     # a seeded read of four questions, not a sentence rewrite
SCOUT_STALE = 300.0       # past the timeout, so a live scout is never called dead
NO_WINDOW = 0x08000000    # see look.py: a new console window minimises the game

WALL_DUE = 240.0        # 4 real minutes
# 3 real minutes at the third speed button. That button is "Superfast", which is
# 6x and not 3x -- watch.py steps 15 real seconds of it and the notes record that
# as ~2 in-game hours, i.e. 5000 ticks / 15s = ~333 ticks/s = ~5.5x. So:
# 180s * 60 ticks/s * 6 = 64800 ticks, which is ~26 game-hours, a bit over a day.
# It lands just inside WALL_DUE on purpose: while the game is fast-forwarding the
# tick clock should be the one that fires, which is the only reason it exists.
TICK_DUE = 64800
INFLIGHT_STALE = 180.0  # a look.py that has not landed in 3 min is dead, not slow

DETACHED = 0x00000008 | 0x00000200

# Luna writes curly quotes and RimWorld labels carry non-ASCII; a Windows console
# is cp1252, so relaying a Lookout report through a plain print() either mojibakes
# it or raises UnicodeEncodeError and loses the report entirely. Fix it once here
# rather than in every print below.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ------------------------------------------------------------------ state

def load():
    try:
        with open(ROTA, "r", encoding="utf-8") as f:
            s = json.load(f)
    except Exception:
        s = {}
    for team in ("scout", "lookout"):
        d = s.setdefault(team, {})
        d.setdefault("lastWall", 0.0)
        d.setdefault("lastTick", None)
        d.setdefault("turn", 0)          # which of the pair is up: 0 or 1
    # 0 = the next scout is a Claude subagent the Core dispatches, 1 = Sol.
    s["scout"].setdefault("reader", 0)
    s["lookout"].setdefault("pass", 0)   # counts spawns; picks Luna or Sol
    s.setdefault("inflight", None)       # {"started": ts} for the spawned look.py
    s.setdefault("delivered", 0.0)       # mtime of the last lookout report we printed
    s.setdefault("scoutInflight", None)  # {"started": ts, "tag": "A"} for a Sol scout
    s.setdefault("scoutDelivered", 0.0)  # mtime of the last Sol scout report we printed
    return s


def save(s):
    os.makedirs(STATE, exist_ok=True)
    tmp = ROTA + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1)
    os.replace(tmp, ROTA)


def ticks():
    """Current game tick, or None if the bridge is not answering.

    Deliberately swallows everything. A watch rotation that crashes because the
    game is at the main menu has removed the eyes at the moment they were about
    to be needed; the wall clock alone is a degraded rota, not a broken one.
    """
    try:
        import rim
        rim.init()
        gi = rim.game("home/get_time", {}, strict=False, timeout=3)
        return (gi.get("ticksGame") if isinstance(gi, dict)
                and gi.get("status") != "no_game" and gi.get("success") is not False else None)
    except Exception:
        return None


def due(d, now, tick):
    """(is_due, why). Wall OR ticks, whichever crosses first."""
    if d["lastWall"] <= 0:
        return True, "first run"
    dw = now - d["lastWall"]
    if tick is not None and d["lastTick"] is not None:
        dt = tick - d["lastTick"]
        if dt >= TICK_DUE:
            return True, "%d ticks (%dh game)" % (dt, dt // 2500)
    if dw >= WALL_DUE:
        return True, "%ds wall" % dw
    return False, "%ds/%s" % (dw, "%dt" % (tick - d["lastTick"])
                              if tick is not None and d["lastTick"] is not None else "no tick")


def fired(s, team, tick):
    s[team]["lastWall"] = time.time()
    s[team]["lastTick"] = tick
    s[team]["turn"] = 1 - s[team]["turn"]
    if team == "scout":
        # This is the one moment a scout actually went out -- `ran scout` for a
        # Claude subagent, the spawn itself for Sol -- so it is the only place
        # the reader may advance. Advancing when the brief is merely PRINTED
        # would hand an undispatched brief's next tick to Sol.
        s[team]["reader"] = 1 - s[team].get("reader", 0)


# ------------------------------------------------------------------ lookout

def look_reader(p):
    """Which reader takes lookout pass `p`. -> "luna" or "sol".

    Half the passes each, but NOT `p % 2`: the wide/near flag is `turn`, which
    also flips once per pass, so a two-phase reader would pin Sol to one frame
    type forever. Four phases (luna, sol, sol, luna) keep the 50/50 split and
    give each reader one wide frame and one near frame per cycle.
    """
    return "sol" if p % 4 in (1, 2) else "luna"


def spawn_look(s):
    """Start look.py detached, unless one is already out. -> (ok, msg, who).

    The double-spawn guard is not paranoia: a Lookout pass takes ~11s and a Core
    turn can be shorter than that, so two ticks in a row would put two Codex
    subprocesses and two 14 MB screenshots in flight for one slot. The flag is
    cleared by a report landing OR by going stale (INFLIGHT_STALE) -- a crash
    that lost stdout must not retire the Lookout permanently, which is the one
    failure this role cannot have.
    """
    inf = s.get("inflight")
    if inf and time.time() - inf.get("started", 0) < INFLIGHT_STALE:
        return False, "already in flight (%ds)" % (time.time() - inf["started"]), None
    os.makedirs(STATE, exist_ok=True)
    log = open(LOOKLOG, "ab")
    log.write(("\n== spawn %s ==\n" % time.strftime("%H:%M:%S")).encode())
    log.flush()
    # Alternate passes take the wide frame. take_screenshot measures 0.47s
    # against 0.016s for a camera move, so the second frame -- not the gliding --
    # is what a person would feel; every other pass puts one wide frame every
    # ~5 minutes, which also matches the camera lease.
    args = [sys.executable, os.path.join(HERE, "look.py")]
    if s["lookout"]["turn"]:
        args.append("--no-wide")
    who = look_reader(s["lookout"].get("pass", 0))
    if who != "luna":
        args += ["--who", who]
    subprocess.Popen(args, cwd=HERE, stdout=log, stderr=log,
                     stdin=subprocess.DEVNULL, creationflags=DETACHED,
                     close_fds=True)
    # Counted here and only on a real spawn, so it stays in step with `turn`
    # and look_reader()'s four-phase cycle stays predictable.
    s["lookout"]["pass"] = s["lookout"].get("pass", 0) + 1
    s["inflight"] = {"started": time.time()}
    return True, "spawned", who


def pending_report(s, path=LOOKOUT, key="delivered"):
    """A finished watcher report we have not shown the Core yet, or None.

    Keyed on mtime, so the report is delivered exactly once and a look.py that
    failed still delivers -- look.py writes its failures into the same file in
    the same shape, on purpose. A Sol scout writes `scout-latest.txt` under the
    same contract, so it is read by the same function.
    """
    try:
        m = os.path.getmtime(path)
    except OSError:
        return None
    if m <= s.get(key, 0.0):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read().strip()
        if not game_session.report_is_current(text):
            return m, "[rota] discarded a stale or unversioned watcher report; awaiting a fresh watch."
        return m, text
    except OSError:
        return None


# ------------------------------------------------------------------ scouts

SCOUT_RULES = (
    "Report <=6 lines, each lead-shaped (a thing noticed, not a paragraph). "
    "End with a mandatory line 'WEIRD: <the single strangest thing>' or "
    "'WEIRD: nothing unusual seen' -- write it even when the answer is nothing. "
    "Name every filter you applied (which rect, which defNames you skipped, which "
    "cap you hit): the pantry full of boulders was invisible for two days because "
    "a reader dropped Chunk* as clutter and never said so. Do not fix anything, "
    "do not run time, do not write a save. Read and report."
)

SCOUT_A = """SCOUT BRIEF A -- colony data check (clean context, one bulk read)

You are a Scout in the RimWorld play stack at C:\\Home\\rimworld\\instruments\\.
Read `notes\\rimworld-from-claude-code.md` if you need the tool gotchas. Use
`rim.py` / `map.py` / `act.py`. The game is running; do not change it.

Answer these four, in this order:
1. THREATS: any predator, insectoid or mechanoid within 30 cells of a colonist.
   `watch.py`'s threats() does this; letters and alerts do NOT cover it.
2. FORBIDDEN: `act.apply('Allow', x, z, w, h, dry=True)['acceptedCellCount']`
   over the whole base rect. **Zero is the only number that means nothing is
   forbidden.** A non-zero count is the answer; do not apply it for real.
3. STALLED BLUEPRINTS: any blueprint with work left. The BUILDINGS section below
   is already read -- start there; `python buildings.py --pending` gives full
   rows. Do NOT click a blueprint: clicking is a mutation, and the section
   already carries per-resource have/need/stillNeeded.
4. STOCKPILE OCCUPANCY: cells free vs used per stockpile zone, counting only
   haulable things and buildings (grass and filth do not block hauling).
"""

SCOUT_B = """SCOUT BRIEF B -- colonist check (clean context, one bulk read)

You are a Scout in the RimWorld play stack at C:\\Home\\rimworld\\instruments\\.
Read `notes\\rimworld-from-claude-code.md` if you need the tool gotchas. Use
`rim.py` / `pawns.py` / `map.py`. The game is running; do not change it.

Answer these five, in this order:
1. HEALTH: every colonist's hediffs by LABEL, not just the downed flag. The label
   carries the severity that moves -- "Heatstroke (extreme)" -- and a pawn is
   `downed` only at the very end, which is too late to be news.
   **An alert names a CONDITION, not a pawn.** `Colonists starving` does not say
   who; the affected pawns are in the alert's explanation text, not its label,
   and reading a name off the alert list is how the Lookout reported the wrong
   colonist as malnourished on 2026-09-02. `python pawns.py --health` -- ONE
   bridge call, nothing on screen -- is the source for who has which condition.
   The PAWN ALERTS lines in the COLONISTS section below are the one safe join
   already made for you: they resolve culprits STRUCTURALLY, out of the alert's
   own `targets[]`, never by reading a name out of the explanation prose. Join
   an alert to a pawn that way or do not join it at all.
   **Hypothermia and heatstroke are facts about a ROOM, not about a wardrobe.**
   The ROOMS section below already carries the temperature of the room every
   colonist is standing in, read in the same call -- join the condition to that
   number rather than measuring anything.
2. NEEDS: food, rest, mood, and any pawn in or near a mental break.
3. IDLE: who has no job. Note that "idle" here can mean "not allowed to do the
   work that exists" -- the Work tab is not readable from the bridge at all, so
   report idleness as a QUESTION for a human, never as a diagnosis.
4. BEDS: who owns a bed and who does not. `home/get_cells_plus` puts `ownerName`
   on every `Building_Bed` (null when unassigned, key always present). A pawn
   sleeping on the floor beside an unassigned bed is invisible to everything else.
5. GEAR: the COLONISTS section below has ALREADY read every colonist's weapon and
   worn apparel in one call. Do not go discover it from scratch and do not click
   a pawn for it -- but do not act on it unread either: VERIFY anything you would
   act on (`python pawns.py --json` re-reads the same fields) and say plainly
   what that section cannot answer. It reports EXCEPTIONS, not a census, so a
   colonist it does not name is one it found nothing wrong with, not one it
   skipped; body coverage there is READ from each garment's own bodyPartGroups
   (torso and legs only -- the groups the nudity mood keys off), so it is a
   reading rather than a guess, and it says on its own line when it could not
   run; and traits are not in it at all, so "Brawler with a gun" can only ever
   reach you as an alert. A colonist
   INCAPABLE OF VIOLENCE is deliberately kept out of the UNARMED count -- arming
   them is not a thing anyone can do -- so when that line says the filter DID
   NOT RUN, treat every name on it as unconfirmed rather than as a task.
"""


# ------------------------------------------------------------ buildings seed

# Every brief carries one `home/list_buildings` summary, so a Scout arrives
# knowing the starving frame and the bill-less bench. It is COLONY-ONLY, which
# is `buildings.survey`'s default: a Scout is being told what OUR base is doing,
# and the ancient ruins on this map outnumber our walls several times over. The
# scope line below states which scope it got rather than assuming either.
# Three invariants:
#   * A failed read is a LOUD line, never a missing section -- to a clean-context
#     Scout, an absent block reads as "nothing pending, no bench idle", and this
#     data has no fallback path.
#   * Every cap states itself in the text it truncated.
#   * Counts stay complete even when sample rows are capped (they come from the
#     tool's `attention` block, emitted in full including zeros).
# buildings_summary() is pure (no bridge, no clock); `python rota.py selftest`
# exercises it against fixtures.
BUILD_CAP = 3                                   # sample rows per group
BUILD_UNAVAILABLE = "BUILDINGS CHECK UNAVAILABLE"


def _bpos(b):
    p = b.get("position") or {}
    return "%s,%s" % (p.get("x", "?"), p.get("z", "?"))


def _blabel(b, n=26):
    return str(b.get("buildLabel") or b.get("label") or b.get("buildDefName")
               or b.get("defName") or "?")[:n]


def _more(rows, cap):
    """(shown, 'and N more' suffix). The suffix is the cap stating itself."""
    return rows[:cap], ("" if len(rows) <= cap else
                        "    ... +%d more, not listed (`python buildings.py`)"
                        % (len(rows) - cap))


def build_unavailable(reason):
    """The loud marker. Never an empty section, never a silent one."""
    reason = " ".join(str(reason).split())
    # buildings.py appends its whole four-step "check the companion" procedure to
    # every error. That belongs in ITS output, not in a Scout's brief -- the
    # marker names the command that prints it. Cut at the sentinel, then clamp.
    reason = reason.split("There is no stock fallback")[0].strip(" -")
    if len(reason) > 200:
        reason = reason[:197] + "..."
    reason = reason or "no reason given"
    return ("!! %s -- %s\n"
            "  No building data was read. This is NOT 'nothing pending' and NOT 'no\n"
            "  bench idle': the check did not run, and there is no fallback for it.\n"
            "  Mark construction, bills, power and fuel UNCHECKED in your report.\n"
            "  `python buildings.py` prints what to check." % (BUILD_UNAVAILABLE, reason))


def _bill_ingredient_lines(r, benches, att, cap):
    """The BILLS subsection's second half: a queue that is live and cannot run.

    A bill's ingredient verdict is an OPT-IN block (`billIngredients`), so the
    three states are kept apart on purpose:

      * the check ran and found nothing  -> one quiet line saying so;
      * the check ran and found a bench  -> the `!!` rows plus a rolled-up
        INGREDIENTS STILL NEEDED line, which is the bill-side twin of
        NEEDED TOTAL;
      * the check DID NOT RUN            -> a loud line. An absent verdict must
        never read as "every bill can run", which is the exact shape of the
        insect-meat failure this whole subsection exists for.
    """
    if not benches:
        return []

    checked = (r.get("filters") or {}).get("billIngredients") is True
    if not checked:
        return ["  BILLS CAN-RUN CHECK DID NOT RUN -- this build answered without "
                "billIngredients, so an active queue here is NOT evidence a bill can "
                "run. `python bills.py` asks directly."]

    short, needed = [], {}
    active_bills, stuck = 0, 0
    for b in benches:
        rows = []
        for bill in b.get("bills") or []:
            if not bill.get("active"):
                continue
            active_bills += 1
            if bill.get("canRunNow") is not False:
                continue
            gaps = [i for i in (bill.get("ingredients") or []) if i.get("shortfall")]
            for ing in gaps:
                label = ing.get("label") or "?"
                needed[label] = needed.get(label, 0) + ing["shortfall"]
            if gaps:
                stuck += 1
                rows.append('"%s" SHORT OF %s'
                            % (str(bill.get("label") or "?")[:30],
                               ", ".join("%s (%s/%s)" % (i.get("label") or "?",
                                                         i.get("available"), i.get("needed"))
                                         for i in gaps[:2])))
        if rows:
            short.append((b, "; ".join(rows[:2])))

    if not short:
        return ["  BILLS CAN RUN: %d active bill(s) on %d bench(es), every ingredient "
                "on hand. (checked)" % (active_bills, len(benches))]

    # Counted here, over the ACTIVE bills, rather than taken from the tool's
    # attention block -- which counts suspended and finished bills short of
    # ingredients too, and those are not what this line is about.
    lines = ["  BILLS SHORT OF INGREDIENTS: %d of %d bench(es) -- %d of %d active "
             "bill(s) cannot run" % (len(short), len(benches), stuck, active_bills)]
    shown, tail = _more(short, cap)
    for b, why in shown:
        lines.append("    !! %s %s: %s" % (_blabel(b), _bpos(b), why))
    if tail:
        lines.append(tail)

    ranked = sorted(needed.items(), key=lambda kv: -kv[1])
    lines.append("  INGREDIENTS STILL NEEDED: "
                 + "; ".join("%s %d" % kv for kv in ranked[:cap])
                 + ("" if len(ranked) <= cap
                    else " ... +%d more ingredient(s)" % (len(ranked) - cap)))
    return lines


def buildings_summary(r, cap=BUILD_CAP, when=None):
    """Pure: home/list_buildings' parsed reply -> the brief's BUILDINGS section.

    Tight on purpose. A Scout needs what CHANGES WHAT IT NOTICES -- the deficit,
    the dead bill queue, the unpowered thing -- not the census. The census is one
    command away and is named here every time.
    """
    B = r.get("buildings") or []
    att = r.get("attention") or {}
    counts = r.get("counts") or {}
    stamp = when or time.strftime("%H:%M:%S")
    L = []

    # Reported, not assumed: a build that does not echo `playerOnly` gets said
    # so, because "whole map" and "ours only" are different briefs.
    po = (r.get("filters") or {}).get("playerOnly")
    scope_word = ("SCOPE NOT REPORTED by this build" if po is None else
                  "colony structures only" if po else "whole map, every faction")

    pending = [b for b in B if b.get("isBlueprint") or b.get("isFrame")]
    L.append("BUILDINGS %s -- %s, %d scanned, %d pending. Already read; do "
             "not sweep cells for it."
             % (stamp, scope_word, counts.get("scanned", 0), len(pending)))

    # 1. the starving frame ---------------------------------------------------
    short = [b for b in pending if not b.get("resourcesComplete", True)]
    if short:
        L.append("  SHORT OF MATERIALS: %d of %d pending" % (len(short), len(pending)))
        shown, tail = _more(short, cap)
        for b in shown:
            res = [x for x in (b.get("resources") or []) if x.get("stillNeeded", 0)]
            need = ", ".join("%s %d" % (x.get("label") or x.get("defName"), x["stillNeeded"])
                             for x in res)
            if not need:
                need = ("cost list unreadable" if b.get("materialCostUnreadable")
                        else "reported short, no per-resource rows")
            pct = b.get("percentComplete")
            L.append("    !! %s %s%s: needs %s"
                     % (_blabel(b), _bpos(b),
                        "" if pct is None else " %d%%" % round(100 * pct), need))
        if tail:
            L.append(tail)
    elif pending:
        L.append("  SHORT OF MATERIALS: none -- %d pending, all have materials. "
                 "(checked)" % len(pending))
    else:
        L.append("  PENDING: none. (checked)")

    deficit = r.get("resourceDeficit") or []
    if deficit:
        shown, tail = _more(deficit, cap)
        bits = []
        for d in shown:
            have = d.get("onMapTotal")
            gap = ("" if have is None or have >= d.get("stillNeeded", 0)
                   else " SHORT BY %d" % (d["stillNeeded"] - have))
            bits.append("%s %d (map %s%s)"
                        % (d.get("label") or d.get("defName"), d.get("stillNeeded", 0),
                           "?" if have is None else have, gap))
        L.append("  NEEDED TOTAL: " + "; ".join(bits)
                 + ("" if not tail else " ... +%d more resource(s)" % (len(deficit) - cap)))

    # 2. the bill-less bench --------------------------------------------------
    benches = [b for b in B if "bills" in b]
    bad = []
    for b in benches:
        bills = b.get("bills") or []
        if not bills:
            bad.append((b, "no bills at all"))
            continue
        flags = []
        for bill in bills:
            if bill.get("finished"):
                flags.append('FINISHED "%s" (0 left)' % str(bill.get("label") or "?")[:30])
            elif bill.get("suspended"):
                flags.append('SUSPENDED "%s"' % str(bill.get("label") or "?")[:30])
        if b.get("activeBillCount", 0) == 0 and not flags:
            flags.append("%d bill(s), none active" % len(bills))
        if flags:
            bad.append((b, "; ".join(flags[:2])))
    if bad:
        L.append("  BILLS: %d of %d bench(es) flagged -- finished %d, suspended %d, "
                 "no bills %d, none active %d"
                 % (len(bad), len(benches), att.get("finishedBills", 0),
                    att.get("suspendedBills", 0), att.get("billGiversWithNoBills", 0),
                    att.get("billGiversWithNoActiveBill", 0)))
        shown, tail = _more(bad, cap)
        for b, why in shown:
            L.append("    !! %s %s: %s" % (_blabel(b), _bpos(b), why))
        if tail:
            L.append(tail)
    elif benches:
        L.append("  BILLS: %d bench(es), all queues live. (checked)" % len(benches))
    else:
        L.append("  BILLS: no bill-giving building. (checked)")

    L.extend(_bill_ingredient_lines(r, benches, att, cap))

    # 3. reasons[] worth a Scout's attention ----------------------------------
    flagged = []
    for b in B:
        p, f = b.get("power") or {}, b.get("fuel") or {}
        why = []
        if p and not p.get("powered", True):
            if not p.get("connected"):
                why.append("NOT CONNECTED TO POWER")
            if not p.get("switchedOn", True):
                why.append("switched off")
            if p.get("brokenDown"):
                why.append("BROKEN DOWN")
            if not why:
                why.append("no power on its net")
        if f and not f.get("hasFuel", True):
            why.append("OUT OF FUEL")
        if why:
            flagged.append((b, ", ".join(why)))
    if flagged:
        L.append("  POWER/FUEL: %d flagged -- unpowered %d, out of fuel %d, broken %d%s"
                 % (len(flagged), att.get("unpowered", 0), att.get("outOfFuel", 0),
                    att.get("brokenDown", 0),
                    "" if not att.get("damagedBuildings") else
                    ", damaged %d" % att["damagedBuildings"]))
        shown, tail = _more(flagged, cap)
        for b, why in shown:
            L.append("    !! %s %s: %s" % (_blabel(b), _bpos(b), why))
        if tail:
            L.append(tail)
    else:
        L.append("  POWER/FUEL: nothing unpowered, broken or out of fuel in %d. "
                 "(checked)" % counts.get("scanned", 0))

    # 4. every filter states itself, even when it removed nothing -------------
    skipped = r.get("skipped") or {}
    removed = sum(v for v in skipped.values() if isinstance(v, int))
    scope = [scope_word if po is None else
             ("ours only -- ancient ruins and other factions NOT counted "
              "(`python buildings.py --all` includes them)" if po
              else "whole map, every faction")]
    if (r.get("notes") or {}).get("naturalRockExcluded"):
        scope.append("natural rock excluded")
    if removed:
        scope.append("%d row(s) dropped by the tool's filters (%s)"
                     % (removed, ", ".join("%s %d" % kv for kv in sorted(skipped.items()) if kv[1])))
    L.append("  scope: %s. Rows capped at %d per group; counts are complete. "
             "Detail: `python buildings.py`." % ("; ".join(scope), cap))
    return "\n".join(L)


def buildings_section(cap=BUILD_CAP):
    """Live read -> section text. Never raises, never returns empty."""
    try:
        if HERE not in sys.path:        # so an importer with another cwd still resolves
            sys.path.insert(0, HERE)
        import rim
        import buildings
        rim.init()
        # billIngredients is what turns "this bench has an active bill" into
        # "this bench has an active bill that can actually run". Without it a
        # stove queued over a larder of insect meat its own filter excludes
        # reads as a live queue -- which is the failure this section exists for.
        r = buildings.survey(billIngredients=True)
    except Exception as e:
        return build_unavailable("%s: %s" % (type(e).__name__, e))
    try:
        return buildings_summary(r, cap=cap)
    except Exception as e:
        return build_unavailable("the read succeeded but summarising it failed -- "
                                 "%s: %s" % (type(e).__name__, e))


# ------------------------------------------------------------ colonists seed

# M, 2026-09-02, asking for exactly this:
#
#     "can we hook in all those pawn calls into the automated scout so they
#      would be able to see, for example, something like the 'unhappy nudity'/
#      'tattered apparel' lines, in addition to the gear worn by colonists?
#      that way they may be able to catch things we otherwise wouldn't (there
#      are times that they wouldn't be looking for gear normally but there's
#      something important to notice they just aren't aware)."
#
# INCIDENTAL NOTICING is the whole request, which is why this rides on BOTH
# briefs and why it is seeded rather than asked for. Brief A (threats, forbidden
# cells, stockpiles) is precisely where a Scout is NOT looking at gear, and is
# therefore where a bare pair of legs or a colony with one revolver has to
# arrive unasked. Lucas died on day 40 of a gap of exactly this shape: five
# sessions of notes called her "the colony's only fighter, charge rifle" and
# nobody ever read what she was carrying, because nobody was looking.
#
# EXCEPTION REPORTING, NOT A CENSUS. Four colonists in four garments each is
# sixteen rows nobody reads, on every brief, for the whole session. What goes in
# is what CHANGES WHAT A SCOUT NOTICES: who is holding nothing, who is wearing
# nothing, what is falling apart, and the pawn-specific alerts WITH THE NAMES
# the alert label itself does not carry. The census is one command away and is
# named here every time.
#
# LINE COST -- the budget this is written against, measured by the self-test,
# which prints the line count beside every case so this comment can be checked:
#   quiet colony    2 lines: the header and one folded "(checked)" line.
#   day-39 colony  10 lines: 1 header, 1 alert header + 3 alert lines, 1 weapons,
#                  1 apparel header + 1 row, 1 scope -- 11 when a filter (the
#                  violence one, or the body-coverage one) has to report that it
#                  could not run. A colony in trouble may cost that; a colony
#                  that is fine may not.
# Same three invariants as the buildings seed above, for the same reasons: a
# failed read is LOUD and never a missing section, every cap states itself in
# the text it truncated, and counts stay complete when sample rows are capped.
# colonists_summary() is pure; `python rota.py selftest` exercises it.

PAWN_CAP = 4                    # sample rows / names per group
ALERT_CAP = 6                   # alert lines, passed to alerts.brief_lines
ALERT_LIMIT = 40                # alerts.resolve()'s own contract default

# RimWorld's OWN tattered line is 50%: Alert_TatteredApparel and the mood debuff
# behind it both fire under HitPoints/MaxHitPoints < 0.5. Confirmed live against
# this colony on day 39 -- Octave's tuque at 48.8% raised the alert, and nothing
# else worn by anybody (everything else >= 74%) did. So the game already reports
# below 50%, and repeating that threshold here would only ever restate an alert.
# WORN_PCT sits ABOVE the game's line on purpose: a garment between 50% and 60%
# is the one nothing will mention until it is already costing mood, which makes
# that band the only part of the range worth a Scout's attention.
TATTERED_PCT = 0.50
WORN_PCT = 0.60

# M, watching the live output on 2026-09-02:
#
#     "the scout should have pawns incapable of violence marked otherwise
#      theyll always have a line in their report about finn being unarmed."
#
# Finn cannot fight. Listing him as UNARMED is a finding that can never be
# acted on and never goes away, and a line like that teaches a reader to skim
# the section -- which costs the two names that DO matter. So a colonist whose
# `bio.incapableOf` carries Violent is counted apart from the unarmed and named
# in three words, not left in the actionable list.
#
# This is the only filter of its kind here, deliberately. It removes a line
# that cannot be true rather than a problem that is inconvenient: being unarmed
# is meaningless for somebody who can never hold a weapon. A filter that hid a
# real problem -- a colonist who cannot haul, say, in a colony drowning in
# unhauled steel -- would be the opposite thing wearing the same clothes.
VIOLENCE_INCAPABLE = "violen"       # matches "Violent" / "Violence", any case
COLONISTS_UNAVAILABLE = "COLONIST GEAR CHECK UNAVAILABLE"
ALERTS_UNAVAILABLE = "PAWN ALERT DETAIL UNAVAILABLE"
LOUD_FALLBACK = ("High", "Critical")

# Body coverage is READ, not guessed. Until 2026-09-02 this was a keyword match
# on the garment's name, because an apparel row carried label, quality, condition
# and slot and nothing about what it covered. The companion rebuilt that evening
# puts `ThingDef.apparel`'s own data on every row:
#
#   "bodyPartGroups": [{"defName": "Legs", "label": "legs"}, ...]
#   "apparelLayers":  ["OnSkin"] / ["Shell"] / ["Middle"] / ["Overhead"]
#
# so the name lists are gone entirely rather than kept as a quiet fallback. A
# guess that renders in the same shape as a fact is the failure this whole file
# is written against: when the field is absent, the section says the check did
# not run (see _covered() and the DID NOT RUN line below), which is what
# `filters.equipment` and `bio.incapableOf` already do with the same trap.
#
# TORSO AND LEGS ONLY, and that is a decision, not an oversight. A garment
# declares up to a dozen groups -- Neck, Shoulders, Arms, UpperHead, FullHead,
# Eyes, Hands, Feet -- and RimWorld itself only makes two of them a problem: the
# nudity thought and its mood debuff key off a bare torso and bare legs, and
# nothing in the game cares that a colonist's arms are uncovered. Reporting the
# rest would turn a two-line section into a census of bare necks on every brief,
# which the brevity rule at the top of this file forbids and which teaches a
# reader to skim past the one line that mattered.
#
# `apparelLayers` is READ INTO THE FIXTURES BUT DELIBERATELY NOT REPORTED. The
# tempting finding -- "a parka over a bare chest" -- is not a finding: the
# nudity thought counts a Shell-layer garment covering the Torso as covered, so
# a layer rule would print a line about a pawn the game considers dressed. It
# would cost a line per pawn to restate something the coverage check already
# answers correctly. If a use ever earns it (an armour-layer gap before a raid,
# say), the field is already here in the payload and in the fixtures.
BODY_GROUPS = (("Torso", "torso"), ("Legs", "legs"))

# One-slot cache for the alerts module, which is a SEPARATE file written on its
# own clock. It may be absent, half-written or broken at any moment, and none of
# those may take out a brief -- so the import lives behind this and every caller
# treats "no module" the same way it treats "the read failed": loudly, in one
# line, with the gear half of the section still rendering.
_ALERTS = []


def _alerts_mod():
    """The alerts module, or None. Imported defensively, exactly once."""
    if not _ALERTS:
        try:
            if HERE not in sys.path:
                sys.path.insert(0, HERE)
            import alerts
            _ALERTS.append(alerts)
        except Exception as e:                      # ImportError, SyntaxError, anything
            _ALERTS.append(None)
            _ALERTS.append("%s: %s" % (type(e).__name__, e))
    return _ALERTS[0]


def _alerts_why():
    return _ALERTS[1] if len(_ALERTS) > 1 else "alerts module not loaded"


def _loud_priorities():
    return tuple(getattr(_alerts_mod(), "LOUD", None) or LOUD_FALLBACK)


def _names(names, cap, sep=", "):
    """'a, b, c ... +2 more' -- the cap stating itself inside the list it cut."""
    names = [str(n) for n in names if n]
    if len(names) <= cap:
        return sep.join(names) or "none"
    return sep.join(names[:cap]) + " ... +%d more, not listed" % (len(names) - cap)


def _gname(a):
    """The short name of one garment: 'button-down shirt', 'tuque'."""
    return str(a.get("labelBase") or a.get("label") or a.get("defName") or "?")[:22]


def _garment(a):
    """'tuque 49%' -- the fact an alert about tattered apparel does not carry."""
    pct = a.get("conditionPct")
    if isinstance(pct, bool) or not isinstance(pct, (int, float)):
        return _gname(a)
    return "%s %d%%" % (_gname(a), round(100 * pct))


def _covered(item):
    """(body-part group defNames this garment covers, did the row carry the field).

    The same two-value shape as _cannot_fight() below, for the same reason: the
    SECOND value is the only thing separating "covers nothing" from "this build
    never said". The 2026-09-02 companion emits `bodyPartGroups` on every apparel
    row and an EMPTY list on a weapon -- so empty is real data -- while an older
    build emits the key nowhere at all, and .get() flattens both to the same
    falsy value. Presence of the list is the whole test.
    """
    groups = item.get("bodyPartGroups")
    if not isinstance(groups, list):
        return set(), False
    return ({str(g.get("defName")) for g in groups
             if isinstance(g, dict) and g.get("defName")}, True)


def _cannot_fight(p):
    """(is violence-incapable, was the question answerable at all).

    `bio.incapableOf` holds the labels the game shows -- "Violent",
    "Intellectual". A companion build without the `bio` parameter discards the
    argument at the SDK door and returns no block at all, and the SECOND value
    is what keeps that absence from reading as "everybody can fight". It is the
    same absent-key trap as `equipment: None`, which cost Lucas her life, and it
    is answered the same way: the caller is told the filter did not run.
    """
    bio = p.get("bio")
    if not isinstance(bio, dict):
        return False, False
    incap = bio.get("incapableOf")
    if not isinstance(incap, list):
        return False, False
    return any(VIOLENCE_INCAPABLE in str(x).lower() for x in incap), True


def _colonist_gear(p):
    """One colonist row -> (bare groups, worn-out garments, all worn, coverage read).

    The fourth value goes False as soon as ANY worn garment arrives without
    `bodyPartGroups`: one unreadable garment is enough to make "nothing covering
    legs" a guess, and a guess must not render in the same shape as a fact. A
    pawn wearing nothing at all is readable by definition -- an empty apparel
    list IS the answer, and needs no field to say so.
    """
    g = p.get("equipment")
    g = g if isinstance(g, dict) else {}
    worn = [a for a in (g.get("apparel") or []) if isinstance(a, dict)]
    covered, readable = set(), True
    for a in worn:
        groups, ok = _covered(a)
        covered |= groups
        readable = readable and ok
    bare = [pretty for defname, pretty in BODY_GROUPS if defname not in covered]
    bad = sorted([a for a in worn
                  if not isinstance(a.get("conditionPct"), bool)
                  and isinstance(a.get("conditionPct"), (int, float))
                  and a["conditionPct"] < WORN_PCT],
                 key=lambda a: a["conditionPct"])
    return bare, bad, worn, readable


def _floor_alert_line(a):
    """The floor under alerts.brief_lines/one_line: never leave an alert unprinted.

    Culprit names come from the resolved `culprits` list -- which alerts.py built
    from the alert's own `targets[]` -- and never from the explanation prose.
    """
    names = [c.get("name") for c in (a.get("culprits") or [])
             if isinstance(c, dict) and c.get("name")]
    shown = names[:3]
    total = a.get("culpritCount") or len(names)
    more = " +%d more" % (total - len(shown)) if total > len(shown) else ""
    who = ", ".join(shown) + more if shown else ("map-wide" if a.get("mapWide")
                                                 else "no culprit named")
    return "%s [%s] (%s)" % (a.get("label") or a.get("type") or "?",
                             a.get("priority") or "?", who)


def _alerts_block(resolved, cap):
    """(lines, alerted names, ok, alerts dropped as not pawn-specific).

    `resolved` is alerts.resolve()'s list. ANYTHING else -- None, a string, an
    exception -- means the alert read did not happen, and that is a loud block
    rather than an empty one: to a clean-context Scout "no alert names a
    colonist" and "nobody asked" are the same silence.
    """
    if not isinstance(resolved, list):
        why = " ".join(str(resolved).split())[:160] if resolved not in (None, "") \
            else "no reason given"
        return ([
            "  !! %s -- %s" % (ALERTS_UNAVAILABLE, why),
            "     NOT 'no alert names a colonist': the alert read did not run. Check",
            "     it by hand (`python alerts.py`, or `python setup.py --status`). The",
            "     gear lines below still stand, but with no alert list they cannot say",
            "     which of their findings the game is already shouting about."],
            set(), False, 0)

    loud = _loud_priorities()
    keep, alerted, truncated = [], set(), False
    for a in resolved:
        if not isinstance(a, dict):
            continue
        culprits = [c for c in (a.get("culprits") or []) if isinstance(c, dict)]
        pawns = [c.get("name") for c in culprits
                 if (c.get("kind") or "pawn") == "pawn" and c.get("name")]
        # Pawn-specific alerts are the ask. Loud ones are kept whatever they
        # name, because dropping a Critical alert for having no named pawn is
        # the same silent-absence failure in a smarter costume.
        if pawns or a.get("priority") in loud:
            keep.append(a)
            alerted.update(pawns)
            if a.get("culpritsTruncated"):
                truncated = True
    dropped = sum(1 for a in resolved if isinstance(a, dict)) - len(keep)

    if not keep:
        return [], set(), True, dropped

    lines = None
    mod = _alerts_mod()
    if mod is not None:
        # USE alerts.py's own formatter -- loud first, its cap stating itself --
        # rather than growing a second one here that can disagree with it.
        try:
            out = mod.brief_lines(keep, cap=cap)
            lines = [l for row in out for l in str(row).splitlines() if l.strip()]
        except Exception:
            try:
                lines = [str(mod.one_line(a)) for a in keep[:cap]]
            except Exception:
                lines = None
    if lines is None:
        lines = [_floor_alert_line(a) for a in keep[:cap]]
        if len(keep) > cap:
            lines.append("... +%d more alert(s), not listed (`python alerts.py`)"
                         % (len(keep) - cap))

    head = ("  PAWN ALERTS: %d of %d active alert(s) name a colonist or are "
            "High/Critical%s.%s"
            % (len(keep), len(keep) + dropped,
               "" if not dropped else "; the other %d are not colonist-specific "
                                      "and are not listed here" % dropped,
               "" if not truncated else " One alert truncated its own culprit "
                                        "list, so the [alerted] tags below are "
                                        "incomplete."))
    # Indented, never re-stripped: alerts.brief_lines aligns "!! ALERT" against
    # "   alert" on purpose, and left-stripping its rows would throw the loud
    # marker away along with the column it lives in.
    return [head] + ["  " + l.rstrip() for l in lines], alerted, True, dropped


def colonists_unavailable(reason):
    """The loud marker. Never an empty section, never a silent one."""
    reason = " ".join(str(reason).split())
    if len(reason) > 200:
        reason = reason[:197] + "..."
    reason = reason or "no reason given"
    return ("!! %s -- %s\n"
            "  No colonist gear was read. This is NOT 'everybody is armed' and NOT\n"
            "  'everybody is dressed': the check did not run, and a missing answer\n"
            "  here has already been read as an armed colonist once -- for five\n"
            "  sessions, until a warg killed her.\n"
            "  Mark weapons, apparel and pawn alerts UNCHECKED in your report.\n"
            "  `python pawns.py --json` and `python alerts.py` print what to check."
            % (COLONISTS_UNAVAILABLE, reason))


def colonists_summary(pawns_reply, resolved_alerts, cap=PAWN_CAP, when=None):
    """Pure: home/list_pawns{equipment:true} + alerts.resolve() -> the COLONISTS section.

    Never raises on a bad reply and never returns empty -- an unusable reply
    comes back as colonists_unavailable(), because to a clean-context Scout an
    absent section reads as "everyone is dressed and armed".

    ## How this deduplicates against the alert lines

    The alert block above these lines already says WHICH CONDITION is live and
    WHO it names. Nothing below restates that. What the gear lines add is the
    fact the alert does NOT carry -- which garment is at 48%, which body group
    is bare, which weapon the one armed pawn is actually holding -- and they tag
    the pawn `[alerted]` so a Scout can join the two without a second copy of
    either half. So:

      * no line here reads "Octave has tattered apparel"; the alert said that;
      * `!! Octave [alerted]: nothing covering legs; tuque 49%` is not a
        duplicate, it is the ANSWER to the alert -- WANTED.md item 8, "unhappy
        nudity should say who and what is uncovered";
      * the join is STRUCTURAL. Culprit names reach here through
        alerts.resolve(), which reads the alert's own `targets[]`; no name is
        ever parsed out of the explanation prose. Reading a name off an alert
        panel is how the Lookout reported the wrong colonist as malnourished on
        2026-09-02, and it is banned in look.py for the same reason.

    When the alert read failed there is no dedup basis at all, and the gear
    lines then print in full: saying a thing twice is the cheap failure, saying
    it zero times is the one that killed Lucas.
    """
    stamp = when or time.strftime("%H:%M:%S")
    if (not isinstance(pawns_reply, dict) or not pawns_reply.get("success")
            or not isinstance(pawns_reply.get("pawns"), list)):
        return colonists_unavailable(
            (isinstance(pawns_reply, dict) and pawns_reply.get("error"))
            or "reply was %s, not a home/list_pawns payload"
            % type(pawns_reply).__name__)

    rows = [p for p in pawns_reply["pawns"] if isinstance(p, dict)]
    crew = [p for p in rows if p.get("isColonist") and not p.get("dead")]
    alines, alerted, alerts_ok, dropped = _alerts_block(resolved_alerts, ALERT_CAP)

    L = ["COLONISTS %s -- %d alive of %d pawn(s) on the map; gear and pawn alerts "
         "ALREADY READ, do not re-read them to answer a question below."
         % (stamp, len(crew), pawns_reply.get("pawnCount") or len(rows))]

    if not crew:
        # The loudest thing this section can say, and it costs one line.
        L.append("  !! NO LIVE COLONIST in the reply. Either the colony is gone or "
                 "the filters hid it -- verify before reporting anything else.")
        return "\n".join(L + alines)

    # `filters.equipment` is the build's own answer to "did you understand the
    # question". A companion older than 2026-09-02 has no `equipment` parameter,
    # so the SDK binder discards the argument at the door and the rows come back
    # with no equipment key -- which .get() turns into None, which five sessions
    # of notes read as "armed". That distinction is the whole reason this section
    # exists, so a build that cannot answer says so LOUDLY instead of quietly
    # reporting nobody unarmed.
    have_gear = sum(1 for p in crew if isinstance(p.get("equipment"), dict))
    gear_ok = bool((pawns_reply.get("filters") or {}).get("equipment")) \
        and have_gear == len(crew)

    unarmed, armed, pack, pacifist = [], [], [], []
    flagged = []
    bio_read = 0
    cover_read = 0          # colonists every worn garment of whom carried bodyPartGroups
    if gear_ok:
        for p in crew:
            name = str(p.get("name") or "?")
            g = p.get("equipment") or {}
            no_fight, bio_ok = _cannot_fight(p)
            bio_read += 1 if bio_ok else 0
            # Derived from the same rows the apparel comes from, rather than
            # from the top-level unarmedColonists[], so the two halves of this
            # section can never disagree about who is on it.
            if g.get("armed"):
                armed.append("%s: %s" % (name, g.get("primaryLabel") or "a weapon"))
            elif no_fight:
                # Not unarmed. Unarmable. See VIOLENCE_INCAPABLE above.
                pacifist.append(name)
            else:
                unarmed.append(name)
                inv = [a for a in (g.get("inventoryWeapons") or []) if isinstance(a, dict)]
                if inv:
                    pack.append("%s is holding nothing but carries %s in the pack"
                                % (name, _gname(inv[0])))
            bare, bad, worn, cover_ok_row = _colonist_gear(p)
            cover_read += 1 if cover_ok_row else 0
            bits = []
            if not worn:
                # True without any body-part data at all: an empty apparel list
                # is the answer, so this one survives an old companion.
                bits.append("WEARING NOTHING AT ALL")
            elif bare and cover_ok_row:
                # Only ever printed off the garment's own bodyPartGroups. When
                # the row did not carry them there is NO line here -- the
                # section says the check did not run instead, once, below.
                bits.append("nothing covering " + " or ".join(bare))
            if bad:
                bits.append("; ".join(_garment(a) for a in bad[:2])
                            + ("" if len(bad) <= 2 else
                               " (+%d more under %d%%)" % (len(bad) - 2, 100 * WORN_PCT)))
            if bits:
                flagged.append("%s%s: %s" % (name, " [alerted]" if name in alerted else "",
                                             "; ".join(bits)))

    # A body-coverage check that could not run is not allowed into the quiet
    # line: "every torso and legs covered" would then be a thing nobody looked
    # at, in the one shape this section prints without qualification.
    cover_ok = gear_ok and cover_read == len(crew)

    quiet = gear_ok and cover_ok and alerts_ok and not alines and not unarmed \
        and not flagged \
        and (bool((pawns_reply.get("filters") or {}).get("bio")) or not pacifist)
    if quiet:
        # THE quiet shape: two lines total, and every clause names what it
        # checked rather than what it found, so silence is never ambiguous.
        L.append("  QUIET: no alert names a colonist (%d other alert(s) not listed), "
                 "all %d armed, every torso and legs covered (each garment's own "
                 "bodyPartGroups), nothing worn under %d%%. "
                 "(checked; `python pawns.py --json` for the census)"
                 % (dropped, len(crew), 100 * WORN_PCT))
        return "\n".join(L)

    L += alines

    if not gear_ok:
        L.append("  !! GEAR UNREAD -- %d of %d colonist row(s) carried an equipment "
                 "block and filters.equipment was %r. A companion build older than "
                 "2026-09-02 drops the parameter silently. This is NOT 'nobody is "
                 "unarmed': weapons and apparel are UNCHECKED."
                 % (have_gear, len(crew),
                    (pawns_reply.get("filters") or {}).get("equipment")))
    else:
        bio_ok = bool((pawns_reply.get("filters") or {}).get("bio")) \
            and bio_read == len(crew)
        cant = ("" if not pacifist else
                " (%s cannot fight, not counted)" % _names(pacifist, cap))
        if unarmed:
            L.append("  WEAPONS: %d of %d colonist(s) UNARMED -- %s%s. Armed: %s."
                     % (len(unarmed), len(crew), _names(unarmed, cap), cant,
                        _names(armed, cap, sep='; ') if armed else "NOBODY AT ALL"))
        elif pacifist:
            L.append("  WEAPONS: nobody who can fight is unarmed%s -- armed: %s. "
                     "(checked)" % (cant, _names(armed, cap, sep='; ')
                                    if armed else "NOBODY AT ALL"))
        else:
            L.append("  WEAPONS: all %d armed -- %s. (checked)"
                     % (len(crew), _names(armed, cap, sep='; ')))
        if unarmed and not bio_ok:
            # The absence states itself. Silence here would put a colonist who
            # can NEVER hold a weapon in a list of people to go and arm, on
            # every brief, forever -- which is how a section stops being read.
            L.append("    !! the 'incapable of violence' filter DID NOT RUN: "
                     "%d of %d row(s) carried a bio block and filters.bio was %r. "
                     "So the UNARMED list above may name somebody who can never "
                     "be armed. This is NOT 'everybody can fight'."
                     % (bio_read, len(crew),
                        (pawns_reply.get("filters") or {}).get("bio")))
        for row in pack[:cap]:
            L.append("    !! %s" % row)
        if len(pack) > cap:
            L.append("    ... +%d more unarmed pawn(s) carrying a weapon, not listed"
                     % (len(pack) - cap))

        if flagged:
            L.append("  APPAREL: %d of %d colonist(s) flagged" % (len(flagged), len(crew)))
            for row in flagged[:cap]:
                L.append("    !! %s" % row)
            if len(flagged) > cap:
                L.append("    ... +%d more, not listed (`python pawns.py --json`)"
                         % (len(flagged) - cap))
        elif cover_ok:
            L.append("  APPAREL: %d colonist(s), every torso and legs covered, nothing "
                     "worn under %d%%. (checked)" % (len(crew), 100 * WORN_PCT))
        else:
            # No coverage claim at all in this line -- the one below says why.
            L.append("  APPAREL: %d colonist(s), nothing worn under %d%%. (checked)"
                     % (len(crew), 100 * WORN_PCT))
        if not cover_ok:
            # The absence states itself, exactly as the bio filter does above.
            # Before 2026-09-02 this check guessed at garment NAMES; the guess
            # was deleted rather than kept as a silent fallback, because an
            # inference printed in the same shape as a reading is how "Lucas is
            # armed" survived five sessions.
            L.append("    !! the BODY COVERAGE check DID NOT RUN for %d of %d "
                     "colonist(s): a worn garment carried no `bodyPartGroups`, which "
                     "a companion older than 2026-09-02 never sends. This is NOT "
                     "'every torso and legs are covered' -- nobody looked. Rebuild "
                     "the companion (INSTALL.md); `python pawns.py --json` shows the "
                     "rows in the meantime." % (len(crew) - cover_read, len(crew)))

    # Every filter and every guess states itself, even where it found nothing.
    L.append("  scope: live colonists only (the dead and non-colonists are out); "
             "colonists incapable of violence are counted apart from the unarmed "
             "(filter %s); alerts kept = names a pawn, or High/Critical, %d "
             "other(s) dropped. "
             "'nothing covering X' is READ from each garment's own `bodyPartGroups` "
             "(check %s), and covers TORSO AND LEGS only -- the two groups RimWorld's "
             "nudity mood keys off; a bare neck or bare arms is not reported and is "
             "not a problem in this game. 'worn' is under %d%%, and the game itself "
             "tatters at %d%%. Rows capped at %d per group; counts complete. Detail: "
             "`python pawns.py --json`."
             % ("ran" if bool((pawns_reply.get("filters") or {}).get("bio"))
                and bio_read == len(crew) else "DID NOT RUN",
                dropped, "ran" if cover_ok else "DID NOT RUN",
                100 * WORN_PCT, 100 * TATTERED_PCT, cap))
    return "\n".join(L)


def colonists_section(cap=PAWN_CAP):
    """Live read -> section text. Never raises, never returns empty."""
    try:
        if HERE not in sys.path:        # so an importer with another cwd still resolves
            sys.path.insert(0, HERE)
        import rim
        rim.init()
        # equipment and bio only, deliberately. health{} and needs{} are brief
        # B's own items 1 and 2 and `python pawns.py --health --needs` owns
        # them; asking for blocks this section does not print would grow the
        # call for nobody.
        #
        # `bio` is requested even on a build that does not have it yet. An
        # undeclared argument is discarded silently by the SDK binder -- which
        # is exactly why the ANSWER is checked (filters.bio, and a bio block on
        # every row) rather than the request assumed to have landed.
        r = rim.game("home/list_pawns", {"equipment": True, "bio": True})
    except Exception as e:
        return colonists_unavailable("%s: %s" % (type(e).__name__, e))

    mod = _alerts_mod()
    if mod is None:
        resolved = "the alerts module did not import -- %s" % _alerts_why()
    else:
        try:
            resolved = mod.resolve(limit=ALERT_LIMIT)
            if not isinstance(resolved, list):
                resolved = ("alerts.resolve() returned %s, not a list"
                            % type(resolved).__name__)
        except Exception as e:
            resolved = "alerts.resolve() raised %s: %s" % (type(e).__name__, e)

    try:
        return colonists_summary(r, resolved, cap=cap)
    except Exception as e:
        return colonists_unavailable("the read succeeded but summarising it failed -- "
                                     "%s: %s" % (type(e).__name__, e))


# ---------------------------------------------------------------- rooms seed

# M, 2026-09-02: "can we make it so the scouts (the automated ones) also
# get a list of rooms". Same argument as the colonist seed above -- brief A is
# counting stockpile cells and would never look at a temperature -- and the same
# three invariants: a failed read is LOUD and never a missing section, every cap
# states itself, and counts stay complete when rows are capped.
#
# WHAT A SCOUT ACTUALLY NEEDS, in five lines rather than a census: where the
# people are and how warm it is there, then the two ends of the indoor range,
# then one count for everything else. `python map.py rooms --cold` is the census
# and is named here every time.
#
# ONE CALL, WITH includeOutdoors. The tool omits the outdoors mega-room and the
# doorway rooms by default -- and a colonist standing outdoors belongs to the
# mega-room, so under the default filter the one person who is actually freezing
# is the one person this section cannot see. So it is asked for, and that row
# pays for itself twice: the outdoors room's own temperature IS the weather,
# which is the only outdoor number anywhere in this payload.
#
# rooms_summary() is pure (no bridge, no clock); `python rota.py selftest`
# exercises it, including the absent-tool and unreadable-temperature branches.
ROOM_CAP = 3                            # named rooms per line
ROOMS_UNAVAILABLE = "ROOMS CHECK DID NOT RUN"


def _rtemp(t):
    """A temperature, or the word. Never a 0 standing in for a null."""
    return "UNREADABLE" if t is None else "%sC" % t


def _rname(r):
    """The room's name, which is the game's own, never invented."""
    for k in ("name", "roleLabel", "gameLabel"):
        v = r.get(k)
        if v and str(v).lower() != "none":
            return str(v)
    if r.get("isDoorway"):
        return "Doorway"
    if r.get("psychologicallyOutdoors"):
        return "Outdoors (no role)"
    return "(no role label)"


def _rindoor(r):
    """Ranked in the cold/warm picks? Only rooms with a temperature of their own.

    `outdoors` is Room.UsesOutdoorTemperature -- the number IS the weather, so
    ranking it says nothing about the fort. `psychologicallyOutdoors` is the
    separate mood flag, and a doorway is a one-cell room nobody lives in.
    """
    return not (r.get("outdoors") or r.get("psychologicallyOutdoors")
                or r.get("isDoorway"))


def _rwhere(r):
    """`@x,z` for a room, from its own centre. Most rooms in a colony have no
    role and are all called "Room"; this is what `map.py room <x> <z>` takes."""
    c = r.get("center") or {}
    if c.get("x") is None or c.get("z") is None:
        return ""
    return " @%s,%s" % (c["x"], c["z"])


def _rcolonists(r):
    """Colonists standing in the room. `pawns[]` is everyone spawned there --
    animals, visitors and raiders alike -- so isColonist is the filter."""
    return [str(p.get("name") or "?") for p in (r.get("pawns") or [])
            if p.get("isColonist")]


def rooms_unavailable(reason):
    """The loud marker. Never an empty section, never a silent one."""
    reason = " ".join(str(reason).split())
    if len(reason) > 200:
        reason = reason[:197] + "..."
    reason = reason or "no reason given"
    return ("!! %s -- %s\n"
            "  No room or temperature data was read. This is NOT 'no room is cold'\n"
            "  and NOT 'nobody is standing outdoors': the check did not run, and\n"
            "  there is no fallback for it. Mark room temperatures UNCHECKED in\n"
            "  your report. `python map.py rooms --cold` prints what to check."
            % (ROOMS_UNAVAILABLE, reason))


def rooms_summary(r, cap=ROOM_CAP, when=None):
    """Pure: home/list_rooms' parsed reply -> the brief's ROOMS section.

    The order is the order a Scout needs it in: the rooms people are actually
    standing in first, then the two ends of the indoor range, then one count for
    the rest. A room already named above is never named again.
    """
    if not isinstance(r, dict) or "rooms" not in r:
        return rooms_unavailable("the reply carried no rooms[]: %s" % str(r)[:160])
    rooms = r.get("rooms") or []
    stamp = when or time.strftime("%H:%M:%S")
    L = []

    # The outdoor number, out of the outdoors room's own row -- there is no
    # map-wide outdoor field in this payload and this makes no second call.
    out_t, out_how = None, ""
    outs = [q for q in rooms
            if q.get("outdoors") and q.get("temperature") is not None]
    if outs:
        out_t = max(outs, key=lambda q: q.get("cellCount") or 0).get("temperature")
    elif not r.get("includeOutdoors"):
        out_how = "the outdoors room was not listed on this call"
    else:
        out_how = "the outdoors room was listed and its own temperature was null"

    L.append("ROOMS %s -- %s of %s listed, outdoor %s%s. One call, already "
             "read; do not sweep cells for it."
             % (stamp, r.get("roomCount"), r.get("roomCountTotal"),
                _rtemp(out_t), "" if not out_how else " (%s)" % out_how))

    # 1. where the people are, coldest first ---------------------------------
    held = [(q, _rcolonists(q)) for q in rooms if _rcolonists(q)]
    held.sort(key=lambda p: (p[0].get("temperature") is not None,
                             p[0].get("temperature")))
    if held:
        rows = ["%s %s (%s)%s" % (_rname(q), _rtemp(q.get("temperature")),
                                  ", ".join(who),
                                  "" if _rindoor(q) else " OUTDOOR-SLAVED")
                for q, who in held[:cap]]
        L.append("  HOLDING PEOPLE: " + "; ".join(rows)
                 + ("" if len(held) <= cap else
                    "; +%d more room(s) with colonists in them, not listed"
                    % (len(held) - cap)))
    else:
        # Not "everyone is fine". Either nobody is home or the pawn sweep did
        # not answer, and an empty list reads identically for both.
        L.append("  HOLDING PEOPLE: no listed room has a colonist in it%s."
                 % ((" -- and the pawn sweep FAILED, so that is a floor and not"
                     " a fact: %s" % str(r.get("pawnIndexWarning")).rstrip("."))
                    if r.get("pawnIndexWarning") else
                    " (they are off-map, or in a room this call did not list)"))

    # 2. the two ends of the indoor range -------------------------------------
    named = {id(q) for q, _ in held[:cap]}
    shown = set(named)                  # named above, so never counted again
    ranked = [q for q in rooms
              if _rindoor(q) and q.get("temperature") is not None]
    if ranked:
        ends = []
        for word, pick in (("coldest", min(ranked, key=lambda q: q["temperature"])),
                           ("warmest", max(ranked, key=lambda q: q["temperature"]))):
            shown.add(id(pick))
            if id(pick) in named:
                ends.append("%s indoor: %s, already named above"
                            % (word, _rname(pick)))
            else:
                who = _rcolonists(pick)
                ends.append("%s indoor: %s%s %s (%s)"
                            % (word, _rname(pick), _rwhere(pick),
                               _rtemp(pick["temperature"]),
                               ", ".join(who) if who else "nobody in it"))
        L.append("  " + " | ".join(ends))
    else:
        L.append("  coldest/warmest indoor: NOT RANKED -- no listed indoor room"
                 " reported a temperature at all.")

    # 3. the rooms that would not answer --------------------------------------
    dark = [q for q in rooms if q.get("temperature") is None]
    if dark:
        L.append("  !! %d room(s) came back with NO temperature -- %s%s. Never"
                 " 0, never dropped: these are the rows to go look at."
                 % (len(dark), ", ".join(_rname(q) + _rwhere(q) for q in dark[:cap]),
                    "" if len(dark) <= cap else ", +%d more" % (len(dark) - cap)))

    # 4. one count line for everything else -----------------------------------
    rest = [q for q in ranked if id(q) not in shown]
    if rest:
        L.append("  %d other indoor room(s) not named above, %sC..%sC,"
                 " nobody standing in them."
                 % (len(rest), min(q["temperature"] for q in rest),
                    max(q["temperature"] for q in rest)))

    # 5. every filter states itself -------------------------------------------
    skipped = sum(1 for q in rooms if q.get("skipped"))
    L.append("  scope: only rooms with a temperature of their own are ranked"
             " (outdoor-slaved and doorway rooms are read but not ranked); a"
             " room a colonist stands in is named whatever it is. %sNames"
             " capped at %d per line; counts complete. Detail:"
             " `python map.py rooms --cold`."
             % (("%d room(s) could not read some field of their own --"
                 " `python map.py room <x> <z>` says which. " % skipped)
                if skipped else "", cap))
    return "\n".join(L)


def rooms_section(cap=ROOM_CAP):
    """Live read -> section text. Never raises, never returns empty."""
    try:
        if HERE not in sys.path:        # so an importer with another cwd still resolves
            sys.path.insert(0, HERE)
        import rim
        rim.init()
        # includeOutdoors -- see the seed comment above. It costs the outdoors
        # mega-room's own cell walk; it buys the outdoor temperature and the
        # colonist who is standing out in it.
        r = rim.game("home/list_rooms", {"includeOutdoors": True})
    except Exception as e:
        return rooms_unavailable("%s: %s" % (type(e).__name__, e))
    try:
        return rooms_summary(r, cap=cap)
    except Exception as e:
        return rooms_unavailable("the read succeeded but summarising it failed -- "
                                 "%s: %s" % (type(e).__name__, e))


# ----------------------------------------------------------- the line budget

# A Scout reads this brief with a small context and one job, so the brief has a
# LINE BUDGET as well as the per-group row caps each section already keeps. The
# policy is written out at the top of scout_brief()'s docstring, which is where
# somebody holding a printed brief will look for it; these are the numbers it
# names. Raising one is a decision about what a reader can hold, not a typo.
SECTION_CAPS = {"BUILDINGS": 12, "COLONISTS": 14, "ROOMS": 8}


def _is_head(line):
    """A group heading inside a section: exactly two spaces of indent."""
    return line.startswith("  ") and not line.startswith("   ")


def _is_detail(line):
    """A row underneath a heading: three or more spaces of indent."""
    return line.startswith("   ")


# A detail row that must survive the budget whatever else goes: it says a check
# DID NOT RUN, and a dropped one of those reads as an all-clear -- the exact
# silent zero every section in this file is written against.
PROTECTED = ("DID NOT RUN", "UNAVAILABLE", "UNCHECKED", "NOT CHECKED", "UNREAD")


def _protected(line):
    return any(w in line for w in PROTECTED)


def _cap(lines, n, label):
    """Hold one section to n lines, ending with `... and N more` when it cut.

    SHEDS SAMPLE ROWS BEFORE IT DROPS GROUPS, which is the whole point of
    having a budget rather than a slice. Each section is a short list of group
    HEADLINES -- "POWER/FUEL: 6 flagged -- unpowered 6, out of fuel 0" -- with
    a few indented SAMPLE ROWS under each naming individuals. The headline
    carries a complete count; a sample row carries one name. So when the
    section is over budget the rows go first, from the bottom group upward,
    and every group keeps the line that says how many. A Scout who reads
    "POWER/FUEL: 6 flagged" can go look; a Scout whose POWER/FUEL group was
    sliced off entirely reads the silence as "nothing unpowered", which is the
    failure this file exists to prevent.

    Three rules:

      * the cap STATES ITSELF in the text it truncated -- a Scout who cannot see
        the rest must at least be told the rest exists, and told the command
        that prints it. An absent tail reads as "that was everything";
      * a row saying a check DID NOT RUN is never shed (see PROTECTED). It is
        not a sample, it is the absence of one;
      * if headlines alone still overrun the budget the section is cut from the
        bottom -- sections write themselves most-relevant-first -- and the cut
        backs up rather than leave a heading standing over rows that are gone.
    """
    lines = list(lines)
    if len(lines) <= n:
        return lines

    keep = [True] * len(lines)
    for i in range(len(lines) - 1, 0, -1):
        if sum(keep) <= n:
            break
        if _is_detail(lines[i]) and not _protected(lines[i]):
            keep[i] = False
    kept = [l for l, k in zip(lines, keep) if k]
    gone = len(lines) - len(kept)

    if len(kept) > n:
        cut = n
        while cut > 1 and _is_head(kept[cut - 1]) and _is_detail(kept[cut]):
            cut -= 1
        gone += len(kept) - cut
        kept = kept[:cut]

    return kept + [
        "  ... and %d more %s line(s) not shown -- %s is budgeted at %d lines "
        "for a reader with limited context, and sample rows go before group "
        "headlines do. Every headline above still carries its complete "
        "count; `python rota.py %s` prints the section in full."
        % (gone, label.lower(), label, n, label.lower())]


def _capped(text, label):
    """One composed section, held to its budget in SECTION_CAPS."""
    return "\n".join(_cap(str(text).splitlines(), SECTION_CAPS[label], label))


def scout_brief(which):
    """One Scout brief: the question, the Hands seed, three read sections, rules.

    ## The line budget

    Each read section is held to a fixed number of lines by _cap(). The caps are
    here to keep the USEFUL rows on top, not merely to make the brief shorter:
    every section below already writes itself most-relevant-first, so the cap
    cuts from the bottom and what survives is the part that changes what a Scout
    notices.

      BUILDINGS  12 lines, in the order buildings_summary() writes them --
                 the starving frame (pending work SHORT OF MATERIALS, with the
                 per-resource shortfall), the rolled-up resource deficit, the
                 flagged bill queues, the live queues that CANNOT RUN for want
                 of ingredients, then unpowered / switched off / broken down /
                 out of fuel, then the scope line. Sample rows inside each of
                 those groups are capped again at BUILD_CAP (3).
      COLONISTS  14 lines -- the pawn alerts that name a colonist, High and
                 Critical first, ALERT_CAP (6) of them; then weapons (who is
                 holding nothing), then apparel (what is bare or falling
                 apart), then the scope line. The gear lines are the ANSWER to
                 the alert lines and tag pawns `[alerted]`, so they are never
                 reordered ahead of them. Rows inside a group: PAWN_CAP (4).
      ROOMS       8 lines -- the rooms actually holding colonists, coldest
                 first; the two ends of the indoor temperature range; the rooms
                 that would not report a temperature at all; one count line for
                 everything else; then the scope line. Names per line:
                 ROOM_CAP (3).

    When a cap cuts anything, the section ends with one `... and N more` line
    that says how many lines went and the command that prints the section in
    full. A section whose read FAILED is a five-to-seven-line loud marker and
    fits every budget above intact -- a truncated failure marker would be the
    one truncation that could be read as an all-clear.
    """
    body = SCOUT_A if which == 0 else SCOUT_B
    seed = ""
    try:
        if os.path.getmtime(HANDS) < game_session.current().get("startedAt", 0):
            raise OSError("Hands report predates the loaded game")
        with open(HANDS, "r", encoding="utf-8") as f:
            txt = f.read().strip()
        if txt:
            # Scouts SEEDED, Lookouts blind -- [M] split. A Scout is already
            # correlated with the Core, so the last Hands summary costs no new
            # blindness and buys relevance. look.py deliberately gets none of it.
            seed = ("\nCONTEXT -- the last turn, from the Hands. Use it for "
                    "relevance, not as truth to confirm:\n"
                    + "\n".join("  " + l for l in txt.splitlines()[:20]) + "\n")
    except OSError:
        seed = ""
    # The buildings read goes in BOTH briefs on purpose. A/B alternate, so
    # attaching it to A only would leave every other watch blind to it -- and B
    # (colonists, food, mood) is exactly where "the butcher bench has no bills"
    # changes what a Scout makes of "we are low on food".
    #
    # The colonist read goes in both for the STRONGER version of that argument.
    # B asks about colonists, so a Scout there would reach the gear eventually;
    # A asks about threats, forbidden cells and stockpiles, and would never look
    # -- which is M's whole ask ("there are times that they wouldn't be
    # looking for gear normally but there's something important to notice they
    # just aren't aware"). A Scout counting stockpile cells while three of four
    # colonists are holding nothing is the exact shape of the week that killed
    # Lucas.
    # The room read goes in both for the colonist seed's argument again:
    # brief A is counting stockpile cells and forbidden flags, and "Finn is
    # standing in a -14C room" has to arrive there unasked or it does not
    # arrive at all.
    return "%s%s\n%s\n\n%s\n\n%s\n\n%s\n" % (
        body, seed,
        _capped(buildings_section(), "BUILDINGS"),
        _capped(colonists_section(), "COLONISTS"),
        _capped(rooms_section(), "ROOMS"),
        SCOUT_RULES)


# ------------------------------------------------------------- scouts by Sol

# The one thing Sol is told that a Claude subagent is not: who they are, that
# the tools are read-only readers, and that nothing may move. Everything else
# is the brief, unchanged -- the two readers must be answering the same question
# for the alternation to be worth anything.
SOL_SCOUT_LEAD = """You are Sol, standing one Scout watch for errata, who is \
playing RimWorld live right now. Your working directory is the instruments \
folder the brief names, and the python tools in it are READ-ONLY readers of the \
running game: run the ones the brief names. `python rim.py call <tool> '<json>'` \
is the raw escape hatch for a read with no wrapper. Change nothing -- no clicks, \
no designations, no bills, no save, and never pause, unpause or change game \
speed. Reading and reporting is the whole job. Answer in plain text, no markdown \
headings, in the shape the brief's rules ask for.

"""


def record_scout(text, context=None):
    """Write the Sol scout's report where cmd_tick will find it. One slot.

    Overwrite, not append: same mailbox contract as look.py's, so the same
    mtime-keyed delivery reads it exactly once.
    """
    os.makedirs(STATE, exist_ok=True)
    tmp = SCOUTOUT + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(game_session.stamp(text, context))
        os.replace(tmp, SCOUTOUT)
    except OSError:
        pass


def spawn_scout(s, which):
    """Write the brief to a file and start a detached Sol scout. -> (ok, msg).

    The brief travels in a file, not on the command line: it is ~20 lines with
    quotes and backslashes in it, and paths here have spaces. Same double-spawn
    guard as spawn_look, cleared by a report landing or by SCOUT_STALE, so a
    child that died without writing cannot retire the team.
    """
    inf = s.get("scoutInflight")
    if inf and time.time() - inf.get("started", 0) < SCOUT_STALE:
        return False, "a Sol scout is already out (%ds)" % (time.time() - inf["started"])
    who = "sol" if s["scout"].get("reader", 0) else "claude"
    if who == "sol" and not os.path.isfile(CODEX):
        return False, "codex not found at %s" % CODEX
    if who == "claude" and not shutil.which("claude"):
        return False, "claude executable not found"
    os.makedirs(STATE, exist_ok=True)
    tag = "A" if which == 0 else "B"
    path = os.path.join(STATE, "scout-brief-%s.txt" % time.strftime("%Y%m%d-%H%M%S"))
    context = dict(game_session.current(), capturedAt=time.time())
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(scout_brief(which) + scout_snapshot())
        game_session.write(path + ".context", context)
    except OSError as e:
        return False, "the brief could not be written (%s)" % e
    log = open(SCOUTLOG, "ab")
    log.write(("\n== scout %s %s ==\n" % (tag, time.strftime("%H:%M:%S"))).encode())
    log.flush()
    subprocess.Popen([sys.executable, os.path.join(HERE, "rota.py"),
                      "scout-run", path, tag, who],
                     cwd=HERE, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                     creationflags=DETACHED, close_fds=True)
    s["scoutInflight"] = {"started": time.time(), "tag": tag, "reader": who}
    return True, "spawned"


def scout_snapshot():
    """Read fixed instruments before inference; scouts need no shell permission.

    Every failure and cap is evidence in the prompt. No generated command is run.
    """
    blocks = ["\nCAPTURED DATA: interpret these observations; do not execute tools.\n"]
    for args in (("status.py",), ("pawns.py", "--health", "--needs"),
                 ("zones.py",), ("inv.py",)):
        try:
            r = subprocess.run([sys.executable, *args], cwd=HERE, timeout=15,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
            body = r.stdout.decode("utf-8", "replace")
            if r.returncode:
                body = "READ FAILED (exit %d): " % r.returncode + body
            if len(body) > 24000:
                body = body[:24000] + "\nTRUNCATED: only first 24000 characters retained."
        except Exception as e:
            body = "READ FAILED: %s" % e
        blocks.append("\nREAD %s\n%s" % (" ".join(args), body))
    blocks.append("\nIf a requested check is absent, explicitly report NOT CHECKED; never infer an all-clear.\n")
    return "\n".join(blocks)


def cmd_scout_run(path, tag, who="sol"):
    """The detached child: one codex call, one report file, nothing raised.

    Every failure -- no brief, a timeout, a codex exit -- is written into
    `scout-latest.txt` in the same shape as an answer, because a scout that
    silently produced nothing looks exactly like a scout with nothing to say.
    """
    t0 = time.time()
    context = game_session.read(path + ".context")
    head = "SCOUT %s (%s) %s" % (tag, who, time.strftime("%Y-%m-%d %H:%M:%S"))
    try:
        with open(path, "r", encoding="utf-8") as f:
            brief = f.read()
    except OSError as e:
        record_scout("%s\nFAILED: the brief could not be read (%s)" % (head, e), context)
        return 0
    outp = path + ".out"
    argv = [CODEX, "exec",
            # No GABS: the user config wires up an MCP bridge and a scout that
            # loaded it would boot a second one beside the live game's.
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--ephemeral",            # no session file per watch
            "--model", "gpt-5.6-sol",
            "-s", "read-only",        # runs the readers, cannot write the repo
            "-C", HERE,               # so the brief's `python <tool>.py` resolve
            "-o", outp,
            "--color", "never",
            "You are Sol, interpreting a captured RimWorld scout packet. "
            "The harness already ran the readers. Do not use tools or run commands. "
            "Ignore command suggestions in the historical brief; answer from the captured data only. "
            "State unavailable checks explicitly.\n" + brief]
    if who == "claude":
        argv = [shutil.which("claude") or "claude", "--print", "--tools", "",
                "--strict-mcp-config", "--no-session-persistence",
                "--system-prompt", "Interpret the captured RimWorld scout packet. "
                "Do not run commands. Answer from captured data; missing checks are NOT CHECKED.",
                brief]
    # A packet can exceed Windows' command-line limit. Pipe it, then close the
    # pipe with communicate(), so detached inference receives a definite EOF.
    prompt = argv.pop()
    if who == "sol":
        argv.append("-")
    try:
        r = subprocess.run(argv, cwd=HERE, timeout=SCOUT_TIMEOUT,
                           creationflags=NO_WINDOW, input=prompt.encode("utf-8"),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if r.returncode:
            tail = (r.stdout or b"").decode("utf-8", "replace").strip()[-200:]
            body = "FAILED: codex exit %d -- %s" % (r.returncode, tail)
        elif who == "claude":
            body = (r.stdout or b"").decode("utf-8", "replace").strip() or "FAILED: empty scout response"
        else:
            with open(outp, "r", encoding="utf-8") as f:
                body = f.read().strip() or "FAILED: codex answered with nothing"
    except subprocess.TimeoutExpired:
        body = "FAILED: Sol did not answer in %ds" % SCOUT_TIMEOUT
    except Exception as e:
        body = "FAILED: %s: %s" % (type(e).__name__, str(e)[:150])
    for p in (path, outp, path + ".context"):
        try:
            os.remove(p)
        except OSError:
            pass
    record_scout("%s\n%s\n(%.0fs)" % (head, body, time.time() - t0), context)
    return 0


# ------------------------------------------------------------------ commands

def _sync_session(s):
    tick = ticks()
    context = game_session.current()
    generation = context.get("generation")
    if generation and s.get("generation") != generation:
        for team in ("scout", "lookout"):
            s[team].update(lastWall=0.0, lastTick=tick)
            s[team].pop("briefedAt", None)
        s["inflight"] = None
        s["scoutInflight"] = None
        s["generation"] = generation
    return tick


def cmd_reports():
    """Deliver current observations; never schedule work or advance clocks."""
    with game_session.locked("rota-reports"):
        delivery_path = os.path.join(STATE, "rota-delivered.json")
        s = game_session.read(delivery_path)
        tick = ticks()
        if tick is None:
            print("[rota] bridge unavailable; reports withheld until their session can be checked.")
            return 0
        out = []
        for path, key, inflight in ((LOOKOUT, "delivered", "inflight"),
                                   (SCOUTOUT, "scoutDelivered", "scoutInflight")):
            rep = pending_report(s, path, key)
            if rep:
                s[key], text = rep
                s[inflight] = None
                out.append(text)
        game_session.write(delivery_path, s)
    print("\n".join(out) if out else "[rota] no new current watcher reports.")
    return 0


def cmd_daemon():
    """Legacy entry point. Automatic cadence belongs only to rota_service."""
    print("[rota] daemon polling is disabled; rota_service owns fixed wall slots.")
    return 0


def cmd_tick():
    # Compatibility alias: old playbooks cannot accidentally become a second scheduler.
    return cmd_reports()


def cmd_ran(team):
    s = load()
    tick = ticks()
    if team not in ("scout", "lookout"):
        print("[rota] unknown team %r" % team)
        return 1
    which = "A" if s[team]["turn"] == 0 else "B"
    fired(s, team, tick)
    s[team].pop("briefedAt", None)
    save(s)
    print("[rota] %s %s logged; next in %ds or %d ticks"
          % (team, which, WALL_DUE, TICK_DUE))
    return 0


def cmd_reset():
    s = load()
    tick = ticks()
    now = time.time()
    for t in ("scout", "lookout"):
        s[t] = {"lastWall": now, "lastTick": tick, "turn": 0}
    # Both alternations start where a session expects them: the first scout is
    # the Core's own subagent, the first lookout pass is Luna on a wide frame.
    s["scout"]["reader"] = 0
    s["lookout"]["pass"] = 0
    s["inflight"] = None
    s["scoutInflight"] = None
    for path, key in ((LOOKOUT, "delivered"), (SCOUTOUT, "scoutDelivered")):
        try:
            s[key] = os.path.getmtime(path)
        except OSError:
            s[key] = 0.0
    save(s)
    print("[rota] reset. both teams start at A, first scout is a subagent, "
          "first lookout is luna; clocks from now%s"
          % ("" if tick is not None else " (bridge down: wall clock only)"))
    return 0


def cmd_status():
    s = load()
    now, tick = time.time(), ticks()
    print("rota %s   game tick %s" % (ROTA, tick))
    for t in ("scout", "lookout"):
        d = s[t]
        is_due, why = due(d, now, tick)
        who = (("sol" if d.get("reader") else "claude subagent") if t == "scout"
               else look_reader(d.get("pass", 0)))
        print("  %-8s next=%s  %s  (%s)  next reader=%s"
              % (t, "A" if d["turn"] == 0 else "B",
                 "DUE" if is_due else "waiting", why, who))
    print("  inflight=%s delivered=%s" % (s.get("inflight"), s.get("delivered")))
    print("  scoutInflight=%s scoutDelivered=%s"
          % (s.get("scoutInflight"), s.get("scoutDelivered")))
    print("  seed file %s: %s" % (HANDS, "present" if os.path.exists(HANDS) else "absent"))
    return 0


# ---------------------------------------------------------------- self-test ---

# verify.py's precedent: a fixture that is not labelled as a fixture gets read as
# a real observation (its fake campfire was, once). So the banner is not decoration.
BANNER = ("=" * 72 + "\n"
          "  SELF-TEST -- EVERY BUILDING, BILL, COLONIST, GARMENT, WEAPON AND\n"
          "  NUMBER BELOW IS INVENTED. No game was read. For the real thing:\n"
          "  `python rota.py buildings`, `colonists` and `rooms`.\n"
          + "=" * 72)


def _fixture(mode):
    """A home/list_buildings reply, shaped from INSTALL.md's documented payload."""
    def frame(label, x, z, pct, res, ok=False):
        return {"defName": "Frame_x", "label": label + " (building)",
                "buildLabel": label, "position": {"x": x, "z": z},
                "status": "frame", "isBlueprint": False, "isFrame": True,
                "faction": "Player", "stuff": "Steel", "reasons": ["frame"],
                "workLeft": 400.0, "workToBuild": 900.0, "percentComplete": pct,
                "resourcesComplete": ok, "materialCostUnreadable": False,
                "resources": res}

    def res(label, have, need):
        return {"defName": label, "label": label, "have": have, "need": need,
                "stillNeeded": max(0, need - have)}

    def bench(label, x, z, bills, active):
        return {"defName": label, "label": label, "position": {"x": x, "z": z},
                "status": "built", "isBlueprint": False, "isFrame": False,
                "faction": "Player", "stuff": "Steel", "reasons": ["billGiver"],
                "bills": bills, "billCount": len(bills), "activeBillCount": active,
                "billStackEmpty": not bills}

    def bill(label, finished=False, suspended=False, short=None, verdict=True):
        # `short` is [(label, have, need)]; verdict=False is the OLDER companion
        # that answers without billIngredients at all, which must not read as
        # "this bill can run".
        row = {"index": 0, "label": label, "recipe": "R", "repeatMode": "RepeatCount",
               "repeatCount": 0 if finished else 8, "targetCount": None,
               "repeatInfo": "0x" if finished else "8x", "suspended": suspended,
               "paused": False, "finished": finished, "active": not (finished or suspended),
               "completableEver": True}
        if not verdict:
            return row
        gaps = short or []
        row["ingredients"] = [
            {"summary": "%dx %s" % (need, lab), "label": lab, "needed": need,
             "available": have, "shortfall": max(0, need - have), "satisfied": have >= need,
             "availableTotal": have, "availableDefs": [], "availableDefsNotListed": 0,
             "filterAllowsAny": True, "excludedByFilter": 0, "excludedForbidden": 0,
             "excludedOutOfRadius": 0, "isFixedIngredient": False}
            for lab, have, need in gaps]
        row["canRunNow"] = not gaps and row["active"]
        row["blockedBy"] = ["missing %s (%d/%d)" % (lab, have, need)
                            for lab, have, need in gaps]
        return row

    r = {"success": True, "tool": "home/list_buildings",
         "counts": {"scanned": 218, "detailed": 6, "aggregatedRows": 9,
                    "aggregatedBuildings": 212, "blueprints": 1, "frames": 2, "built": 215},
         "attention": {"blueprints": 1, "frames": 2, "pendingMissingResources": 0,
                       "unpowered": 0, "notConnectedToPower": 0, "switchedOff": 0,
                       "brokenDown": 0, "outOfFuel": 0, "billGiversWithNoBills": 0,
                       "billGiversWithNoActiveBill": 0, "finishedBills": 0,
                       "suspendedBills": 0, "damagedBuildings": 0},
         "resourceDeficit": [], "skipped": {"byMatch": 0, "byStatus": 0, "byRadius": 0,
                                            "byPlayerOnly": 84, "byMaxDetailed": 0},
         "notes": {"naturalRockExcluded": True, "aggregatedRowsOmittedWhenEmpty": True,
                   "positionIs": "Thing.Position", "billShouldDoNowNotCalled": True},
         "filters": {"match": None, "status": "all", "category": "artificial",
                     "playerOnly": True, "aggregate": True, "damagedBelowPct": 0,
                     "x": None, "z": None, "radius": None,
                     "maxPositionsPerDef": 6, "maxDetailed": 400,
                     "billIngredients": True},
         "buildings": [], "aggregated": []}

    if mode == "clear":
        r["buildings"] = [frame("wall", 100, 101, 0.6, [res("Steel", 15, 15)], ok=True),
                          bench("electric stove", 118, 141, [bill("Cook simple meal")], 1)]
        return r

    if mode == "nobillcheck":      # an older companion: no verdict on any bill
        r["buildings"] = [bench("electric stove", 118, 141,
                                [bill("Cook simple meal", verdict=False)], 1)]
        r["filters"]["billIngredients"] = False
        return r

    if mode == "trouble":
        r["buildings"] = [
            frame("solar generator", 142, 88, 0.34,
                  [res("Steel", 60, 100), res("Component", 1, 3)]),
            frame("wall", 100, 101, 0.0, [res("Steel", 0, 15)]),
            bench("butcher table", 120, 140, [bill("Butcher creature", finished=True)], 0),
            bench("electric stove", 118, 141, [], 0),
            # The bill this whole subsection exists for: a live queue that
            # cannot run. M's larder is insect meat and the recipe's
            # default filter excludes it, so `available` is 0 with meat on the
            # floor -- and the bench is flagged by nothing else in this payload.
            bench("fueled stove", 117, 142,
                  [bill("Cook simple meal", short=[("Meat", 0, 10)])], 1),
            bench("smithy", 122, 138,
                  [bill("Make component", short=[("Steel", 4, 12), ("Gold", 0, 2)])], 1),
            {"defName": "Turret_Mini", "label": "mini-turret", "position": {"x": 130, "z": 152},
             "status": "built", "isBlueprint": False, "isFrame": False, "faction": "Player",
             "stuff": None, "reasons": ["turret", "unpowered"],
             "power": {"powered": False, "connected": False, "switchedOn": True,
                       "brokenDown": False, "powerOutput": -100.0}},
            {"defName": "Campfire", "label": "campfire", "position": {"x": 119, "z": 139},
             "status": "built", "isBlueprint": False, "isFrame": False, "faction": "Player",
             "stuff": None, "reasons": ["outOfFuel"],
             "fuel": {"hasFuel": False, "fuel": 0.0, "targetFuelLevel": 30.0}}]
        r["attention"].update(pendingMissingResources=2, unpowered=1, notConnectedToPower=1,
                              outOfFuel=1, finishedBills=1, billGiversWithNoBills=1,
                              billGiversWithNoActiveBill=1, billsShortOfIngredients=2)
        r["resourceDeficit"] = [
            {"defName": "Steel", "label": "Steel", "stillNeeded": 55, "sites": 2,
             "onMapTotal": 12, "countedAsResource": 12},
            {"defName": "Component", "label": "Component", "stillNeeded": 2, "sites": 1,
             "onMapTotal": 9, "countedAsResource": 9}]
        return r

    if mode == "flood":            # every group over the cap, so the caps speak
        r["buildings"] = [frame("wall %d" % i, 100 + i, 101, 0.0,
                                [res("Steel", 0, 15)]) for i in range(7)]
        r["buildings"] += [bench("stove %d" % i, 118 + i, 141, [], 0) for i in range(5)]
        r["buildings"] += [bench("smithy %d" % i, 122 + i, 138,
                                 [bill("Make component %d" % i,
                                       short=[("Steel", 0, 12), ("Gold", 1, 2),
                                              ("Plasteel", 0, 3)])], 1)
                           for i in range(5)]
        r["buildings"] += [
            {"defName": "Lamp%d" % i, "label": "standing lamp %d" % i,
             "position": {"x": 90 + i, "z": 90}, "status": "built",
             "isBlueprint": False, "isFrame": False, "faction": "Player", "stuff": None,
             "reasons": ["unpowered"],
             "power": {"powered": False, "connected": True, "switchedOn": False,
                       "brokenDown": False, "powerOutput": -10.0}} for i in range(6)]
        r["attention"].update(frames=7, pendingMissingResources=7, unpowered=6,
                              switchedOff=6, billGiversWithNoBills=5,
                              billGiversWithNoActiveBill=5, billsShortOfIngredients=5)
        r["resourceDeficit"] = [{"defName": n, "label": n, "stillNeeded": 105, "sites": 7,
                                 "onMapTotal": 12, "countedAsResource": 12}
                                for n in ("Steel", "Wood", "Component", "Silver")]
        r["skipped"]["byMaxDetailed"] = 3
        return r

    raise ValueError(mode)


def _pawn_fixture(mode):
    """A home/list_pawns{equipment, bio} reply, shaped from ListPawnsTool.cs.

    Same discipline as _fixture(): the shape comes from the C# that emits it, so
    a fixture that passes here is not agreeing with a guess about the payload.
    """
    # Every apparel row carries the two fields the companion rebuilt 2026-09-02
    # emits, copied from a LIVE read of this save rather than invented: a
    # button-down shirt really does declare Torso/Neck/Shoulders/Arms, pants
    # declare Legs alone, and a weapon comes back with an EMPTY bodyPartGroups
    # rather than none. The "-nogroups" mode drops the key from every row, which
    # is exactly and only what an older companion does.
    body_data = "nogroups" not in mode

    def item(def_name, base, pct, weapon=False, quality="Normal", label=None,
             groups=("Torso", "Neck", "Shoulders", "Arms"), layers=("OnSkin",)):
        hp = None if pct is None else int(round(130 * pct))
        d = {"slot": "primary" if weapon else "apparel",
             "defName": def_name, "labelBase": base,
             "label": label or ("%s (%s%s)" % (base, quality.lower(),
                                               "" if pct is None else
                                               " %d%%" % round(100 * pct))),
             "quality": quality, "stuff": None, "stackCount": 1,
             "isWeapon": weapon, "ranged": weapon, "melee": False,
             "isApparel": not weapon,
             "hitPoints": hp, "maxHitPoints": None if pct is None else 130,
             "conditionPct": pct}
        if body_data:
            d["bodyPartGroups"] = [] if weapon else [
                {"defName": g, "label": g.lower()} for g in groups]
            d["apparelLayers"] = [] if weapon else list(layers)
        return d

    def gear(primary=None, apparel=(), pack=()):
        return {"hasEquipmentTracker": True,
                "armed": primary is not None,
                "primary": primary,
                "primaryLabel": "unarmed" if primary is None else primary["label"],
                "equipped": [] if primary is None else [primary],
                "equippedCount": 0 if primary is None else 1,
                "hasInventoryTracker": True,
                "inventoryWeapons": list(pack),
                "inventoryWeaponCount": len(pack),
                "inventoryItemCount": len(pack),
                "hasApparelTracker": True,
                "apparel": list(apparel), "apparelCount": len(apparel)}

    def bio(incapable=(), traits=()):
        # Shaped from the `bio: true` block being added to home/list_pawns.
        # Only `incapableOf` is read by this file; the rest is here so the
        # fixture looks like the payload rather than like the reader.
        return {"childhood": "Colony kid", "adulthood": "Farmer",
                "traits": list(traits), "skills": {"Shooting": 4, "Melee": 3},
                "incapableOf": list(incapable),
                "incapableOfCount": len(incapable)}

    def colonist(name, equipment, x=110, z=142, dead=False, incapable=(),
                 traits=(), with_bio=True):
        row = {"name": name, "defName": "Human", "kindDef": "Colonist",
               "position": {"x": x, "z": z}, "faction": "Lampblack",
               "hostile": False, "hostileReason": "none", "isColonist": True,
               "isPrisoner": False, "animal": False, "mechanoid": False,
               "downed": False, "dead": dead, "job": "Haul", "mentalState": None,
               "nearestColonist": "Finn", "nearestColonistDistance": 3}
        if equipment is not None:
            row["equipment"] = equipment
        if with_bio:
            row["bio"] = bio(incapable, traits)
        return row

    shirt = lambda pct: item("Apparel_CollarShirt", "button-down shirt", pct)
    pants = lambda pct: item("Apparel_Pants", "pants", pct, groups=("Legs",))
    parka = lambda pct: item("Apparel_Parka", "parka", pct, quality="Awful",
                             layers=("Shell",))
    # The tuque is the case a name-guess used to have to special-case: it covers
    # UpperHead, which is neither of the two groups this section reports, so a
    # pawn in nothing but a tuque is bare-torsoed AND bare-legged.
    tuque = lambda pct: item("Apparel_Tuque", "tuque", pct, quality="Poor",
                             groups=("UpperHead",), layers=("Overhead",))
    revolver = item("Gun_Revolver", "revolver", 0.98, weapon=True, quality="Good",
                    label="Revolver (good)")

    r = {"success": True, "tool": "home/list_pawns", "pawnCount": 0,
         "spawnedPawnTotal": 25, "colonistCount": 0, "hostileCount": 0,
         "skippedByDistance": 0,
         "filters": {"hostileOnly": False, "includeColonists": True,
                     "withinOfColonists": 0, "includeDead": False,
                     "health": False, "needs": False, "equipment": True,
                     "bio": True, "visibleHediffsOnly": True},
         "notes": {}, "pawns": [], "unarmedColonists": []}

    # A wild animal and a corpse ride along in every mode: the section must
    # count neither, and a fixture with only colonists in it would never notice.
    extras = [{"name": "warg", "defName": "Warg", "kindDef": "Warg",
               "position": {"x": 208, "z": 158}, "faction": None, "hostile": False,
               "hostileReason": "none", "isColonist": False, "isPrisoner": False,
               "animal": True, "mechanoid": False, "downed": False, "dead": False,
               "job": "PredatorHunt", "mentalState": None,
               "nearestColonist": "Longhoff", "nearestColonistDistance": 80,
               "equipment": {"hasEquipmentTracker": False, "armed": False,
                             "primary": None, "primaryLabel": "unarmed",
                             "equipped": [], "equippedCount": 0,
                             "hasInventoryTracker": False, "inventoryWeapons": [],
                             "inventoryWeaponCount": 0, "inventoryItemCount": 0,
                             "hasApparelTracker": False, "apparel": [],
                             "apparelCount": 0}}]

    if mode == "quiet":
        r["pawns"] = [
            colonist("Ada", gear(revolver, [shirt(0.95), pants(0.93)])),
            colonist("Bo", gear(revolver, [shirt(0.88), pants(0.91), parka(0.99)])),
            colonist("Cyd", gear(revolver, [shirt(0.97), pants(0.9), tuque(0.8)]))]

    elif mode in ("day39", "day39-nobio", "day39-nogroups"):
        # Lampblack as the save actually stands: one revolver in the colony, a
        # tattered tuque, Octave with nothing on his legs -- and Finn, who is
        # INCAPABLE OF VIOLENCE and can therefore never be armed. Two degraded
        # variants of the SAME colony, because the interesting question about
        # each of these fields is what happens when it is not there:
        #   "-nobio"     : no `bio` parameter, so the violence filter cannot run;
        #   "-nogroups"  : no `bodyPartGroups` on any apparel row, so the body
        #                  coverage check cannot run -- and Octave's bare legs,
        #                  which are TRUE, must still not be printed as a
        #                  finding, because in that build nobody looked.
        bio_on = mode != "day39-nobio"
        if not bio_on:
            r["filters"].pop("bio")
        r["pawns"] = [
            colonist("Finn", gear(None, [shirt(0.846), pants(0.746), parka(0.979)]),
                     incapable=["Violent"], with_bio=bio_on),
            colonist("Lucas", gear(None, [pants(0.808), shirt(0.838), parka(0.957)],
                                   pack=[item("Gun_Revolver", "revolver", 0.9,
                                              weapon=True, label="Revolver")]),
                     with_bio=bio_on),
            # Octave: a T-shirt (Torso, Shoulders), a parka (Torso...) and a
            # tuque (UpperHead). Nothing anywhere declares Legs -- which is the
            # bare-legs finding, read off the payload instead of guessed from
            # the absence of a word like "pants".
            colonist("Octave", gear(None, [item("Apparel_BasicShirt", "T-shirt", 0.931,
                                                groups=("Torso", "Shoulders")),
                                           parka(0.974), tuque(0.488)]),
                     with_bio=bio_on),
            # Longhoff: torso and legs both covered several times over, so the
            # apparel check must say NOTHING about him -- the silence half of
            # the test, which is the half a broken coverage read still passes.
            colonist("Longhoff", gear(revolver, [pants(0.915), shirt(0.92),
                                                 item("Apparel_FlakVest", "flak vest", 0.97,
                                                      groups=("Torso", "Neck"),
                                                      layers=("Middle",))]),
                     traits=["Brawler"], with_bio=bio_on),
            colonist("Steve", gear(None, [shirt(0.55), pants(0.57)]), dead=True,
                     with_bio=bio_on)]

    elif mode == "gearless":
        # The five-session bug in fixture form: a companion build with no
        # `equipment` parameter. The rows come back with the key absent and the
        # filter echo says so, which is the ONLY thing that separates "nobody is
        # armed" from "nobody asked".
        r["filters"].pop("equipment")
        r["pawns"] = [colonist("Finn", None), colonist("Octave", None)]
        r.pop("unarmedColonists")

    elif mode == "flood":          # every group past its cap, so the caps speak
        r["pawns"] = [colonist("Colonist %d" % i,
                               gear(None, [shirt(0.4), tuque(0.3)],
                                    pack=[item("Gun_Revolver", "revolver", 0.9,
                                               weapon=True, label="Revolver")]))
                      for i in range(9)]

    else:
        raise ValueError(mode)

    r["pawns"] += extras
    live = [p for p in r["pawns"] if p.get("isColonist") and not p.get("dead")]
    r["pawnCount"] = len(r["pawns"])
    r["colonistCount"] = len(live)
    if "unarmedColonists" in r:
        r["unarmedColonists"] = [p["name"] for p in live
                                 if not (p.get("equipment") or {}).get("armed")]
    return r


def _alert_fixture(mode):
    """alerts.resolve()'s output shape: label, priority, type, ordinal, culprits."""
    def a(label, priority, typ, ordinal, names, count=None, truncated=False,
          map_wide=False):
        return {"label": label, "priority": priority, "type": typ,
                "ordinal": ordinal,
                "culprits": [{"name": n, "id": "Thing_Human%d" % (1000 + i),
                              "kind": "pawn", "x": 110, "z": 142}
                             for i, n in enumerate(names)],
                "culpritsTruncated": truncated,
                "culpritCount": len(names) if count is None else count,
                "detail": "", "mapWide": map_wide}

    if mode == "quiet":
        return [a("Need research project", "Medium",
                  "RimWorld.Alert_NeedResearchProject", 1, [], map_wide=True)]

    if mode == "day39":
        return [a("Tattered apparel", "Medium", "RimWorld.Alert_TatteredApparel",
                  1, ["Octave"]),
                a("Unhappy nudity", "Medium", "RimWorld.Alert_UnhappyNudity",
                  2, ["Octave"]),
                a("Brawler has ranged weapon", "Medium",
                  "RimWorld.Alert_BrawlerHasRangedWeapon", 3, ["Longhoff"]),
                a("Need research project", "Medium",
                  "RimWorld.Alert_NeedResearchProject", 4, [], map_wide=True)]

    if mode == "truncated":
        return [a("Colonists idle", "Medium", "RimWorld.Alert_ColonistsIdle", 1,
                  ["Colonist 0", "Colonist 1"], count=9, truncated=True),
                a("Colonist needs rescuing", "High",
                  "RimWorld.Alert_ColonistNeedsRescuing", 2, ["Colonist 3"]),
                a("Tattered apparel", "Medium", "RimWorld.Alert_TatteredApparel",
                  3, ["Colonist 0", "Colonist 2"]),
                a("Unhappy nudity", "Medium", "RimWorld.Alert_UnhappyNudity", 4,
                  ["Colonist 5"]),
                a("Low food", "High", "RimWorld.Alert_LowFood", 5, [], map_wide=True),
                a("Fire", "Critical", "RimWorld.Alert_Fire", 6, [], map_wide=True),
                a("Need warm clothes", "Medium",
                  "RimWorld.Alert_NeedWarmClothes", 7, ["Colonist 8"]),
                a("Need joy sources", "Low", "RimWorld.Alert_NeedJoySources", 8,
                  [], map_wide=True)]

    raise ValueError(mode)


def _room_fixture(mode):
    """A home/list_rooms{includeOutdoors:true} reply, shaped from ListRoomsTool.cs.

    Same discipline as the other two fixtures: the field names come from the C#
    that emits them (RoomRow and BuildPawnIndex), so a fixture that passes here
    is not agreeing with a guess about the payload. `pawns[]` rows carry name,
    isColonist and position and nothing else; `temperature` is a rounded number
    or null; the outdoors mega-room is a row like any other, distinguished by
    outdoors/psychologicallyOutdoors and by being ~49,000 cells.
    """
    def room(i, name, temp, cells=30, pawns=(), outdoors=False, doorway=False,
             skipped=()):
        return {"index": i, "id": 1000 + i, "name": name, "gameLabel": name,
                "role": None, "roleLabel": name, "cellCount": cells,
                "center": {"x": 100 + i, "z": 140 + i},
                "temperature": temp, "outdoors": outdoors,
                "psychologicallyOutdoors": outdoors, "isDoorway": doorway,
                "fogged": False, "properRoom": not outdoors,
                "pawns": [{"name": n, "isColonist": c,
                           "position": {"x": 110 + j, "z": 140}}
                          for j, (n, c) in enumerate(pawns)],
                "pawnCount": len(pawns), "owners": [], "ownersRead": True,
                "beds": [], "contents": [], "stockpiles": [],
                "skipped": list(skipped)}

    r = {"success": True, "tool": "home/list_rooms", "mapName": "Lampblack",
         "roomCountTotal": 14, "roomCount": 9, "roomsOmitted": 5,
         "outdoorRoomsOmitted": 0, "doorwaysOmitted": 0,
         "dereferencedRoomsOmitted": 5, "includeOutdoors": True,
         "includeBoundary": False, "cellsListed": False, "unit": "C",
         "omitted": [], "rooms": []}

    OUT = ("Outdoors", None)
    if mode == "quiet":
        r["rooms"] = [
            room(0, "Barracks", 18.6, 42, [("Octave", True), ("Finn", True)]),
            room(1, "Kitchen", 20.1, 24, [("Longhoff", True)]),
            room(2, "Freezer", -2.0, 18),
            room(3, "Workshop", 16.4, 36),
            room(4, "Bedroom (Lucas)", 17.9, 30),
            room(5, "Outdoors (no role)", 11.2, 49122,
                 [("muffalo", False)], outdoors=True)]
        return r

    if mode == "outdoor-colonist":
        # The case the whole includeOutdoors argument exists for: Finn is
        # standing in the open air at -18C and belongs to the mega-room, which
        # the tool omits by default. Under the old filter he is invisible.
        r["rooms"] = [
            room(0, "Barracks", 18.6, 42, [("Octave", True)]),
            room(1, "Kitchen", 20.1, 24),
            room(2, "Freezer", -8.4, 18),
            room(3, "Workshop", 16.4, 36),
            room(4, "Doorway", -17.0, 1, [("Longhoff", True)], doorway=True),
            room(5, "Outdoors (no role)", -18.3, 49122,
                 [("Finn", True), ("wild boar", False)], outdoors=True)]
        return r

    if mode == "unreadable":
        r["rooms"] = [
            room(0, "Barracks", None, 42, [("Octave", True)],
                 skipped=["temperature (Room.Temperature threw)"]),
            room(1, "Kitchen", 20.1, 24, [("Longhoff", True)]),
            room(2, "Freezer", None, 18),
            room(3, "Workshop", 16.4, 36),
            room(4, "Bedroom (Lucas)", None, 30),
            room(5, "Outdoors (no role)", None, 49122, outdoors=True)]
        return r

    if mode == "no-pawn-index":
        r["rooms"] = [room(0, "Barracks", 18.6, 42), room(1, "Kitchen", 20.1, 24),
                      room(2, "Freezer", -2.0, 18),
                      room(5, "Outdoors (no role)", 11.2, 49122, outdoors=True)]
        r["pawnIndexWarning"] = ("The pawn sweep threw NullReferenceException, "
                                 "so pawns[] is a floor rather than a total on "
                                 "every room.")
        return r

    if mode == "no-outdoors":
        # A caller that did NOT ask for the outdoors: there is then no outdoor
        # temperature anywhere in the payload, and the section must say so
        # rather than print a 0 or quietly leave the word out.
        r["includeOutdoors"] = False
        r["rooms"] = [room(0, "Barracks", 18.6, 42, [("Octave", True)]),
                      room(1, "Kitchen", 20.1, 24)]
        return r

    if mode == "flood":            # every cap over its limit, so the caps speak
        r["rooms"] = [room(i, "Bedroom %d" % i, 14.0 + i, 20,
                           [("Colonist %d" % i, True)]) for i in range(6)]
        r["rooms"] += [room(20 + i, "Store %d" % i, None, 12) for i in range(5)]
        r["rooms"] += [room(30 + i, "Hall %d" % i, 19.0 + i, 40) for i in range(4)]
        r["rooms"].append(room(40, "Outdoors (no role)", 6.5, 49122, outdoors=True))
        r["roomCount"] = len(r["rooms"])
        r["roomCountTotal"] = len(r["rooms"]) + r["roomsOmitted"]
        return r

    raise ValueError(mode)


def _self_test():
    print(BANNER)
    for mode, note in (("clear", "everything healthy -- the 'checked, not assumed' shape"),
                       ("trouble", "a starving frame, a bill-less bench, and two live "
                                   "queues that CANNOT RUN for want of ingredients"),
                       ("nobillcheck", "a companion that answered without billIngredients: "
                                       "the can-run check DID NOT RUN, and one live-looking "
                                       "queue must not be reported as runnable"),
                       ("flood", "every group past the cap, so the caps must state themselves")):
        print("\n--- %s: %s ---" % (mode, note))
        print(buildings_summary(_fixture(mode), when="00:00:00"))
    print("\n--- the failure path, which is the whole point ---")
    print(build_unavailable("BridgeError: home/list_buildings failed: "
                            "URLError: connection refused"))

    # The line count is printed beside each case because the brevity budget in
    # the colonists-seed comment is a claim, and a claim nobody can check is a
    # claim that quietly stops being true.
    for pmode, amode, note in (
            ("quiet", "quiet",
             "nothing wrong anywhere -- MUST stay at two lines"),
            ("day39", "day39",
             "Lampblack on day 39: Octave tattered and bare-legged, Longhoff "
             "the colony's only gun and a Brawler, and Finn -- who is INCAPABLE "
             "OF VIOLENCE and so is not an unarmed colonist, he is a colonist "
             "who cannot be armed"),
            ("day39-nobio", "day39",
             "the same colony, read by a companion with no `bio` block: the "
             "violence filter CANNOT run, and an absent bio must not read as "
             "'everybody can fight'"),
            ("day39-nogroups", "day39",
             "the same colony again, read by a companion older than 2026-09-02: "
             "no apparel row carries `bodyPartGroups`, so the BODY COVERAGE "
             "check cannot run. Octave's legs really ARE bare -- and this build "
             "must NOT say so, because it did not look. No 'nothing covering "
             "legs' anywhere below, one loud line saying the check did not run, "
             "and the tattered tuque (which needs no body-part data) still "
             "reported"),
            ("flood", "truncated",
             "every cap past its limit, plus an alert that truncated its own "
             "culprit list")):
        print("\n--- colonists %s/%s: %s ---" % (pmode, amode, note))
        txt = colonists_summary(_pawn_fixture(pmode), _alert_fixture(amode),
                                when="00:00:00")
        print(txt)
        print("  [%d lines]" % len(txt.splitlines()))

    print("\n--- the colonist failure paths, which are the whole point ---")
    print("\n(1) the companion build that has no `equipment` parameter -- the "
          "bug that killed Lucas, in fixture form:")
    print(colonists_summary(_pawn_fixture("gearless"), _alert_fixture("day39"),
                            when="00:00:00"))
    print("\n(2) alerts.py absent or broken at import: the gear half must still "
          "render, and the alert half must be LOUD rather than empty:")
    print(colonists_summary(_pawn_fixture("day39"),
                            "ImportError: No module named 'alerts'",
                            when="00:00:00"))
    print("\n(3) a pawn read that failed -- the reply itself is the error:")
    print(colonists_summary({"success": False, "tool": "home/list_pawns",
                             "error": "home/list_pawns requires an active map."},
                            _alert_fixture("day39"), when="00:00:00"))
    print("\n(4) no colonist survived the filters at all:")
    empty = _pawn_fixture("quiet")
    empty["pawns"] = [p for p in empty["pawns"] if not p.get("isColonist")]
    print(colonists_summary(empty, _alert_fixture("quiet"), when="00:00:00"))
    print("\n(5) the read never happened:")
    print(colonists_unavailable("BridgeError: home/list_pawns failed: "
                                "URLError: connection refused"))

    for mode, note in (
            ("quiet", "a warm fort -- everyone indoors, nothing to chase"),
            ("outdoor-colonist",
             "the case includeOutdoors exists for: Finn is standing in the OPEN "
             "AIR at -18.3C and Longhoff in a -17C doorway. Both rooms are ones "
             "the tool omits by default. Both MUST be named here, and neither "
             "may be ranked as an indoor room"),
            ("unreadable",
             "three rooms whose temperature came back null, one of them holding "
             "Octave. Every one must print UNREADABLE -- never a 0, never "
             "dropped -- and the coldest/warmest pick must ignore them rather "
             "than treat a null as cold"),
            ("no-pawn-index",
             "the pawn sweep threw, so every pawns[] is empty. 'no colonist in "
             "any room' must NOT read as 'everybody is somewhere warm'"),
            ("no-outdoors",
             "a reply that did not include the outdoors: there is no outdoor "
             "temperature in the payload at all and the header must say which"),
            ("flood",
             "every cap past its limit, so the caps must state themselves")):
        print("\n--- rooms %s: %s ---" % (mode, note))
        txt = rooms_summary(_room_fixture(mode), when="00:00:00")
        print(txt)
        print("  [%d lines]" % len(txt.splitlines()))

    print("\n--- the room failure paths, which are the whole point ---")
    print("\n(1) a build with no `home/list_rooms` at all -- the section must "
          "say it DID NOT RUN, never print an empty room list:")
    print(rooms_unavailable("BridgeError: home/list_rooms failed: -32601 Tool "
                            "not found"))
    print("\n(2) the tool answered, but with no rooms[] -- a refusal read as an "
          "empty colony is the silent zero this stack keeps relearning:")
    print(rooms_summary({"success": False, "tool": "home/list_rooms",
                         "error": "home/list_rooms requires an active map."},
                        when="00:00:00"))
    print("\n--- the line budget: what a Scout actually receives ---")
    print("Sections compose in full for `python rota.py buildings|colonists|"
          "rooms`; scout_brief() holds each to SECTION_CAPS. Sample rows shed "
          "from the lower groups up, so every headline keeps its count.")
    for label, txt in (
            ("BUILDINGS", buildings_summary(_fixture("flood"), when="00:00:00")),
            ("COLONISTS", colonists_summary(_pawn_fixture("flood"),
                                            _alert_fixture("truncated"),
                                            when="00:00:00")),
            ("ROOMS", rooms_summary(_room_fixture("flood"), when="00:00:00"))):
        capped = _capped(txt, label)
        print("\n%s: %d line(s) composed, %d in the brief (budget %d)"
              % (label, len(txt.splitlines()), len(capped.splitlines()),
                 SECTION_CAPS[label]))
        print(capped)

    print("\n" + BANNER)
    return 0


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "tick"
    if cmd in ("selftest", "--self-test"):
        return _self_test()
    if cmd == "buildings":
        print(buildings_section())
        return 0
    if cmd == "colonists":
        print(colonists_section())
        return 0
    if cmd == "rooms":
        print(rooms_section())
        return 0
    if cmd in ("tick", "reports"):
        return cmd_reports()
    if cmd == "daemon":
        return cmd_daemon()
    if cmd == "ran":
        print("[rota] dispatch is automatic; no manual acknowledgement is needed.")
        return 0
    if cmd == "scout-run":
        # The detached child spawn_scout() starts. Not for hand use.
        return cmd_scout_run(sys.argv[2] if len(sys.argv) > 2 else "",
                             sys.argv[3] if len(sys.argv) > 3 else "?",
                             sys.argv[4] if len(sys.argv) > 4 else "sol")
    if cmd == "brief":
        print(scout_brief(load()["scout"]["turn"]))
        return 0
    if cmd == "reset":
        return cmd_reset()
    if cmd == "status":
        return cmd_status()
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
