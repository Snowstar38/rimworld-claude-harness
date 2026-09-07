"""The whole between-turns read in one call: clock, letters, messages, alerts,
colonists, threats, UI.

  python status.py            # the ~20-line board
  python status.py --brief    # three lines
  python status.py --explain  # + each alert's explanation prose
  python status.py --detail   # + every need and hediff per colonist
  python status.py --json     # the raw reply

  import status
  status.read()               status.read(colonists=False, threats=False)

One `home/status` call replaces `rimworld/get_game_info` + `list_letters` +
`list_messages` + `list_alerts` + `list_colonists` + `get_ui_state`. Every block
comes back present and empty rather than absent, so a quiet colony and an
unread one never look the same.

## What each line says

  CLOCK      the date the game prints, and whether it is stopped -- and WHY.
             `PAUSED by guard colonist_injury -- Longhoff was injured.
             (service exited 43 s ago; fix the cause, then python play.py
             start)`. The reason comes from the companion, which keeps
             StopReason for the last epoch after the supervisor process is
             gone, and falls back to `state/play-service.json`. Restarting
             into a live guard is the loop this line exists to stop, so
             `play.py start` is never the FIRST thing a guard stop says.
             `PAUSED (no supervised play running, and nothing recorded a
             stop)` now means exactly that: nowhere has a stop record.
             `PAUSED (forced)` is a modal window holding the clock, which is a
             different problem.
  LETTERS    one row each with its AGE in game hours and a STALE mark past
             two of them, marked CHOICE / OPEN / INFO / ANNOUNCE by calling
             letters.py's own classifier rather than a copy of it. INFO is the
             opportunity-quest shape -- every button only jumps or closes, so
             there is nothing to answer; the copy that used to live here called
             those CHOICE.
  MESSAGES   the fading top-left text, with its age in REAL seconds. It dies at
             13s whatever the game clock is doing; a message this misses is
             gone with no way to recover it.
  ALERTS     loud (High/Critical) first, each with the culprits it names. Names
             come from `targets[]`, never from the prose.
  COLONISTS  one line each: mood with its break band, health, current job, and
             a `[...]` tag for drafted / in bed / downed / bleeding / mental.
  THREATS    hostiles, hunting predators, WILD PREDATORS NEAR a colonist and
             DOWNED pawns near one. A tame predator's hunt and a wild one
             eating wildlife are excluded by the tool, not here. The last two
             rows exist because on 2026-09-04 this board said "THREATS none"
             with a timber wolf 15 cells from the medic: a predator is not
             hostile until it starts hunting, and by then it is on somebody.
             The hostile NUMBER folds the downed-near rows in -- `2 hostile(s)
             (1 downed -- gets back up)` -- because a manhunter that goes down
             loses its mental state and leaves hostiles[] by the companion's
             own design, and on 2026-09-07 that read as `0 hostiles` with one
             four cells from three colonists. The hunting-predator number
             carries the GUARD's count beside the board's: `home/status` counts
             a hunt only when the prey is ours, the supervised-play guard stops
             for any non-player PredatorHunt within 40 cells, and the two
             disagreeing is what let `THREATS 0 hunting predator` stand beside
             a refusal on `predator_hunt: Grizzly bear`.
  UI         printed only when something is open: a main tab, a selection, or a
             modal window eating map input.

Exit code is non-zero when the tool call fails. A failure is LOUD and prints no
board: an empty board would read as "nothing is happening", which is the one
wrong answer that matters here.
"""
import json
import sys

import letters
import rim

TOOL = "home/status"

# High and Critical get a line of their own. Same meaning as alerts.py's LOUD,
# and compared as a RANK against the ordered RimWorld.AlertPriority, so a
# priority this file has never heard of is treated as loud.
PRIORITY_ORDER = ("Low", "Medium", "High", "Critical")
LOUD_FLOOR = PRIORITY_ORDER.index("High")

NAME_CAP = 3            # culprit names printed inline before "+N more"
ROW_CAP = 6             # rows per section on the default board
TEXT = 64               # message/letter text width


def read(colonists=True, threats=True, explanations=False, detail=False):
    """One `home/status` call. Raises on a tool failure."""
    return rim.game(TOOL, {"colonists": colonists, "threats": threats,
                           "explanations": explanations,
                           "colonistDetail": detail})


def _trim(s, n=TEXT):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n - 3] + "..."


def priority_rank(priority):
    """Position in PRIORITY_ORDER, or a rank ABOVE the set for a priority this
    file has never heard of -- the one thing worse than an unfamiliar alert is
    a silent one."""
    try:
        return PRIORITY_ORDER.index(priority)
    except ValueError:
        return len(PRIORITY_ORDER)


def letter_mark(row):
    """CHOICE / OPEN / INFO / ANNOUNCE -- `letters.py`'s own classifier.

    It is no longer a copy. The copy that lived here predated the
    opportunity-quest fix, so it still called a jump/close-only quest a CHOICE
    -- the label that sent three turns of 2026-09-07 hunting for a decision
    that did not exist. `home/status` letter rows carry the same `choices[]`
    and `shouldAutomaticallyOpenLetter` that `rimworld/list_letters` does, and
    letters.py's predicates take either spelling, so there is nothing to
    translate.
    """
    return letters._kind(row)


# --------------------------------------------------------- why it stopped ---
#
# 2026-09-07: the stream lost six turns to this. A guard stop and a dead
# service printed two different lines for the SAME state -- while the service
# process was still alive, `supervised play IS running ... something paused
# it`; a second later, once it had exited, `no supervised play running --
# python play.py start`, which names no guard and just says start again. Six
# turns read the second line as a crashed service and restarted straight back
# into `colonist_injury`, which re-fired every few seconds while a colonist
# stood next to a downed wolf. The stop REASON is the whole answer and it was
# never on the board.
#
# Two places know it, and they are ranked:
#   1. the companion (`home/supervised_play` op:status) keeps StopReason and
#      StopDetail for the last epoch AFTER the run goes inactive, so it is the
#      authority even when nothing of ours is left running;
#   2. `state/play-service.json`, which the supervisor writes on its way out.
# `home/status` does NOT carry supervised-play state, so (1) costs one extra
# bridge call -- taken ONLY when the clock is paused, not force-paused, and no
# service is alive: exactly the case that was misread, and one this file
# already treats as an emergency. A running colony pays nothing, and every
# failure falls through to the file.

# The companion's own stop kinds (SupervisedPlayTool.Stop) mapped onto the
# vocabulary play_service.py writes as `stopKind`. Anything NOT in here is a
# GUARD: the guard set is open-ended -- every PawnHit and every health/injury
# hit adds one -- and a guard nobody has seen before must not read as
# "unknown, try starting again".
STOP_KINDS = {
    "requested_pause": "requested",
    "lease_expired": "lease",
    "session_changed": "binding-lost",
    "external_pause": "external",
    "external_speed_changed": "external",
    "force_paused": "external",
    "unavailable": "error",
    "watcher_error": "error",
    "start_refused": "error",
    "stop_file": "stop-file",
}

# What each kind means and what to do about it. The guard line deliberately
# does NOT lead with `python play.py start`: restarting into a live guard is
# the exact loop this file exists to stop.
STOP_ADVICE = {
    "guard": "fix the cause, then `python play.py start`",
    "requested": "`python play.py start` when you are ready",
    "lease": "the supervisor stopped renewing; `python play.py start`",
    "binding-lost": "the session that owned it is gone; `python play.py start`",
    "stop-file": "state/play-service.stop was present; remove it, then "
                 "`python play.py start`",
    "external": "something outside supervised play moved the clock; "
                "`python play.py status`, then `python play.py start`",
    "error": "read the detail before restarting; `python play.py status`",
}

PROBE_STOP_REASON = True    # off switch for the one conditional bridge call
STOP_PROBE_TIMEOUT = 8


def _probe_stop_reason():
    """`home/supervised_play` op:status -- a pure read (Supervisor.Status()
    takes the lock and returns a snapshot; no main-thread work, no mutation).

    Returns (stopReason, stopDetail) for the last epoch, or None. Never raises:
    the file record is a complete answer on its own.
    """
    try:
        r = rim.game("home/supervised_play", {"op": "status"}, strict=False,
                     timeout=STOP_PROBE_TIMEOUT)
    except Exception:
        return None
    if not isinstance(r, dict) or r.get("active") is not False:
        return None
    if not r.get("stopReason"):
        return None
    return r.get("stopReason"), r.get("stopDetail")


def stop_record(row=None, probe=None, now=None):
    """Why the last supervised-play run ended, or None if nothing recorded it.

    `row` is `state/play-service.json`; `probe` returns the companion's
    (stopReason, stopDetail), and `probe=False` skips the bridge entirely. The
    companion WINS on the reason because it outlives the supervisor process:
    the file can be a stale write from a crash, the companion's copy is the
    epoch that actually stopped.
    """
    row = row or {}
    kind = row.get("stopKind")
    reason = row.get("stopReason")
    detail = row.get("stopDetail")
    live = None if probe is False else (probe or _probe_stop_reason)()
    if live:
        reason, detail = live[0], live[1] or detail
        kind = STOP_KINDS.get(reason, "guard")
    elif reason and not kind:
        kind = STOP_KINDS.get(reason, "guard")
    if not kind and not reason:
        if row.get("error"):
            kind, reason, detail = "error", "supervisor error", row["error"]
        else:
            return None
    if kind == "error" and not detail and row.get("error"):
        reason, detail = reason or "supervisor error", row["error"]
    stopped = row.get("stoppedAt")
    age = None
    if isinstance(stopped, (int, float)):
        import time
        age = max(0.0, (time.time() if now is None else now) - stopped)
    return {"kind": kind or "guard", "reason": reason, "detail": detail,
            "stoppedAt": stopped, "ageSeconds": age,
            # The supervisor's last few ring rows, written by play_service on
            # its way out. They are what a stop with NO recorded reason has
            # instead of a name; see stop_note.
            "lastEvents": [str(x) for x in (row.get("lastEvents") or [])],
            "source": "companion" if live else "state/play-service.json"}


def _exited_phrase(rec):
    age = rec.get("ageSeconds")
    if age is None:
        return "the service is not running"
    if age < 90:
        return "service exited %d s ago" % round(age)
    if age < 5400:
        return "service exited %d min ago" % round(age / 60.0)
    return "service exited %.1f h ago" % (age / 3600.0)


def stop_note(rec):
    """The text after `PAUSED` for a recorded stop. Names the guard FIRST --
    the reason is the decision, the command is the footnote."""
    kind = rec.get("kind") or "guard"
    detail = " ".join(str(rec.get("detail") or "").split())
    tail = "%s; %s" % (_exited_phrase(rec),
                       STOP_ADVICE.get(kind, "`python play.py status`"))
    if kind == "guard" and not rec.get("reason"):
        # 2026-09-07, turn 35: the clock stopped twice after fire events with
        # no guard reason on the board at all. `by guard unnamed` was the old
        # answer and it is not one. Say what is actually known.
        head = "by a guard that recorded NO reason"
    elif kind == "guard":
        head = "by guard %s" % rec["reason"]
    elif kind == "requested":
        head = "by `play.py pause`"
    elif kind == "stop-file":
        head = "by the stop file"
    else:
        head = "by %s%s" % (kind,
                            " %s" % rec["reason"] if rec.get("reason") else "")
    if detail:
        head += " -- %s" % detail
    if not rec.get("reason"):
        events = rec.get("lastEvents") or []
        head += (" -- last supervisor events: %s" % "; ".join(events)
                 if events else
                 " -- and the supervisor's event ring recorded nothing either")
    return "%s (%s)" % (head, tail)


def _pause_note(probe=None):
    """The text after `PAUSED`. Answers WHY, not just "nothing is running".

    `paused` is `TickManager.Paused` and is always right; it just never said
    whether anything was meant to be running, or what stopped it.
    """
    try:
        import play
        import play_service
        row = play_service.read_json(play_service.SERVICE_STATE)
        if play.service_alive(row):
            return ("(supervised play IS running, service pid %s -- something "
                    "paused it; `python play.py status`)" % row.get("pid"))
    except Exception:
        return "(source unknown -- supervised-play state unreadable)"
    try:
        rec = stop_record(row, probe=(probe if probe is not None
                                      else (None if PROBE_STOP_REASON else False)))
    except Exception:
        rec = None
    if rec:
        return stop_note(rec)
    # Only here -- with NO stop record in the companion OR on disk -- is "just
    # start it again" the right sentence.
    return ("(no supervised play running, and nothing recorded a stop -- "
            "`python play.py start`)")


def clock_line(r):
    """`Day 39, 14h, Aprimay, Autumn 5501 - PAUSED (no supervised play ...)`."""
    t = r.get("time") or {}
    date = t.get("dateFull")
    if not date:
        day = t.get("dayOfQuadrumDisplay")
        date = ("Day %s, %s, %s %s"
                % (day, t.get("quadrum"), t.get("season"), t.get("year"))
                if day is not None else "date unreadable (no map longitude)")
    hour = t.get("hourInteger")
    stamp = "%s%s" % (date, "" if hour is None else ", %dh" % hour)

    if t.get("paused"):
        if t.get("forcePaused"):
            state = "PAUSED (forced -- a window is holding the clock)"
        else:
            # _pause_note() brings its own brackets: a named guard reads
            # `PAUSED by guard colonist_injury -- ...`, not `PAUSED (by ...)`.
            state = "PAUSED %s" % _pause_note()
    else:
        state = "running %s" % (t.get("timeSpeed") or "?")

    return "CLOCK   %s -- %s   tick %s" % (stamp, state, t.get("ticksGame"))


def brief_lines(r):
    """Three lines: the clock, the counts, and the loudest thing standing."""
    c = r.get("counts") or {}
    out = [clock_line(r)]
    warning = _combat_warning(r)
    if warning:
        out.append(warning)
    threat = threat_summary(r)
    out.append("        %d letter(s) (%d choice), %d message(s), %d alert(s) "
               "(%d loud), %d colonist(s) (%d down), %s, %s%s"
               % (c.get("letterCount") or 0,
                  sum(letter_mark(row).strip() == "CHOICE" for row in (r.get("letters") or [])),
                  c.get("messageCount") or 0, c.get("alertCount") or 0,
                  c.get("loudAlertCount") or 0, c.get("colonistCount") or 0,
                  c.get("downedCount") or 0,
                  hostile_phrase(threat), hunter_phrase(threat),
                  ", %d wild predator(s) near a colonist" % threat["wildPredatorsNear"]
                  if threat["wildPredatorsNear"] else ""))

    loud = [a for a in (r.get("alerts") or [])
            if priority_rank(a.get("priority")) >= LOUD_FLOOR]
    decisions = [l for l in (r.get("letters") or [])
                 if letter_mark(l) != "ANNOUNCE"]
    for hunter in threat["guardHunts"]:
        out.append("        !! nearby hunt can stop supervised play: %s (%s), prey %s"
                   % (hunter.get("name"), hunter.get("thingId"), hunter.get("prey") or "unknown"))
    if threat["total"]:
        # The DOWNED ones are named on this line too. They left hostiles[] the
        # moment they went down and they rejoin it the moment they get up.
        out.append("        !! %s: %s"
                   % (hostile_phrase(threat),
                      ", ".join(_threat_name(h) for h in threat["rows"][:3])
                      or "none listed -- `python pawns.py --threats`"))
    elif loud:
        out.append("        !! %s" % alert_line(loud[0]).strip())
    elif decisions:
        out.append("        !! %d letter(s) waiting on somebody: %s"
                   % (len(decisions),
                      ", ".join(_trim(l.get("label"), 24) for l in decisions[:3])))
    else:
        out.append("        nothing loud. (checked -- read from %s, not assumed.)"
                   % TOOL)
    return out


GUARD_HUNT_RADIUS = 40      # SupervisedPlayTool: PredatorHunt within 40 cells


def guard_hunters(threats):
    """Every hunt the supervised-play guard would stop for.

    This is the companion's OWN predicate, copied field for field from
    `SupervisedPlayTool.Watch`:

        PredatorHunting(p) && p.Faction != Faction.OfPlayer
                           && colonists.Any(c => Distance(p, c) <= 40)

    -- `CurJob.def.defName == "PredatorHunt"`, not on the player faction,
    within 40 cells (Chebyshev) of a colonist. It says NOTHING about the prey.
    `home/status`'s own `huntingPredatorCount` is a narrower question (it also
    requires the prey to be ours), which is why the board could print
    `THREATS 0 hunting predator` while the guard refused on
    `predator_hunt: Grizzly bear`.

    2026-09-07: this used to read `huntersIgnored[]` only. A predator hunting a
    COLONIST lands in `huntingPredators[]` instead -- the worst case of all --
    and was the one hunt this function could not see. Both lists carry
    `predatorIsOurs`, set before the branch, so both are scanned and deduped on
    thingId.
    """
    out, seen = [], set()
    for key in ("huntingPredators", "huntersIgnored"):
        for row in (threats.get(key) or []):
            d = row.get("distanceToNearestColonist")
            if row.get("predatorIsOurs") is not False:
                continue
            if not isinstance(d, (int, float)) or d > GUARD_HUNT_RADIUS:
                continue
            tid = row.get("thingId")
            if tid is not None and tid in seen:
                continue
            seen.add(tid)
            out.append(row)
    return out


# ------------------------------------------------------- what is still armed ---
#
# 2026-09-07, turn 18: `--brief` printed `0 hostile(s)` with a DOWNED manhunter
# four cells from three colonists. That is not a bug in the companion -- a
# manhunter that goes down loses its MentalStateDef, so `IsHostile` stops being
# true and it leaves `hostiles[]` by design. The companion puts it in
# `downedNear[]` instead and says so in its own docs. `--brief` was reading
# `counts.hostileCount` alone and never looked at the fourth list.
#
# A downed animal gets back up. So the summary below FOLDS `downedNear[]` into
# the hostile total -- every row that is not already in `hostiles[]` -- and
# then NAMES every row it counted. The predicate is deliberately wide:
# `downedNear[]` is already bounded (within predatorRadius of a colonist, never
# a player-faction pawn, never a corpse), so the worst false positive is a
# downed hare showing up as "1 hostile (1 downed)" with the word "hare" printed
# next to it, which costs one glance. The false NEGATIVE cost two colonists.


def threat_summary(r):
    """Hostiles, the downed ones among them, and the hunts the guard watches.

    Returns a dict; `total` counts the downed as hostiles because they stand
    back up, and `rows` names every one of them.
    """
    t = r.get("threats") or {}
    c = r.get("counts") or {}
    hostiles = list(t.get("hostiles") or [])
    hostile_count = t.get("hostileCount")
    if not isinstance(hostile_count, int):
        hostile_count = c.get("hostileCount") or 0
    listed = set(h.get("thingId") for h in hostiles if h.get("thingId"))

    near_rows = list(t.get("downedNear") or [])
    near_count = t.get("downedNearCount")
    if not isinstance(near_count, int):
        near_count = c.get("downedNearCount")
    if not isinstance(near_count, int):
        near_count = len(near_rows)
    extra_rows = [d for d in near_rows if d.get("thingId") not in listed]
    # `downedNear[]` is capped by the companion; the COUNT is not. Trust the
    # count for the number and the rows for the names.
    overlap = len(near_rows) - len(extra_rows)
    extra = max(0, near_count - overlap)

    downed_in_hostiles = [h for h in hostiles if h.get("downed")]
    wild_near = t.get("wildPredatorsNearCount")
    if not isinstance(wild_near, int):
        wild_near = len(t.get("wildPredatorsNear") or [])

    hunter_count = t.get("huntingPredatorCount")
    if not isinstance(hunter_count, int):
        hunter_count = c.get("huntingPredatorCount") or 0
    hunts = guard_hunters(t)

    total = hostile_count + extra
    downed = len(downed_in_hostiles) + extra
    if total and downed > total:            # a capped list can never out-count
        downed = total
    return {"total": total, "downed": downed,
            "hostileCount": hostile_count, "downedNearExtra": extra,
            "rows": hostiles + extra_rows,
            "wildPredatorsNear": wild_near,
            "huntingPredatorCount": hunter_count, "guardHunts": hunts}


def hostile_phrase(s):
    """`2 hostile(s) (1 downed -- gets back up)`."""
    if not s["downed"]:
        return "%d hostile(s)" % s["total"]
    return ("%d hostile(s) (%d downed -- gets back up)"
            % (s["total"], s["downed"]))


def hunter_phrase(s):
    """The guard's count beside the board's, when they disagree."""
    text = "%d hunting predator(s)" % s["huntingPredatorCount"]
    extra = len(s["guardHunts"]) - s["huntingPredatorCount"]
    if extra > 0:
        text += (" (+%d hunt(s) within %d cells that WOULD stop supervised "
                 "play)" % (extra, GUARD_HUNT_RADIUS))
    return text


def _threat_name(row):
    name = _trim(row.get("name") or row.get("defName"), 24)
    bits = []
    if row.get("downed"):
        bits.append("DOWNED")
    d = row.get("distanceToNearestColonist")
    if isinstance(d, (int, float)):
        bits.append("%d cells" % d)
    return "%s%s" % (name, " [%s]" % ", ".join(bits) if bits else "")


def alert_line(a):
    """`Tattered apparel [Medium] -- Octave, Lucas`, culprits from targets[]."""
    label = _trim(a.get("label") or a.get("type"), 48)
    head = "%s [%s]" % (label, a.get("priority"))
    targets = a.get("targets") or []
    count = a.get("targetCount") or 0

    if not targets:
        if a.get("culpritsReadable") and count == 0:
            return "  %s" % head                       # map-wide, nobody to name
        return "  %s (%d culprit(s), none listed)" % (head, count)

    names = [_trim(t.get("name") or "?", 20) for t in targets[:NAME_CAP]]
    more = max(count, len(targets)) - len(names)
    return "  %s -- %s%s" % (head, ", ".join(names),
                             " +%d more" % more if more > 0 else "")


def colonist_line(c):
    """`Lucas  mood 62% ok  health 91%  Hauling  [in bed]`."""
    def pct(v):
        return "  --" if v is None else "%3d%%" % round(v * 100)

    tags = []
    if c.get("dead"):
        tags.append("DEAD")
    if c.get("downed"):
        # 2026-09-05: two pawns read DOWNED here for hours while inv.py
        # --corpses showed nothing, because downed is not dead. Say so.
        tags.append("DOWNED" if c.get("dead") else "DOWNED (alive, not a corpse)")
    if c.get("bleeding"):
        tags.append("BLEEDING")
    if c.get("needsTend"):
        tags.append("needs tend")
    if c.get("drafted"):
        tags.append("DRAFTED -- undraft when safe")
    if c.get("inBed"):
        tags.append("in bed")
    if c.get("mentalState"):
        tags.append("MENTAL: %s" % c["mentalState"])

    risk = c.get("breakRisk")
    return ("  %-12s mood %s %-7s health %s  %-22s %s"
            % (_trim(c.get("name"), 12), pct(c.get("mood")),
               "ok" if risk == "none" else (risk or "--"),
               pct(c.get("healthPct")), _trim(c.get("job") or "idle", 22),
               "[%s]" % ", ".join(tags) if tags else "")).rstrip()


def _combat_warning(r):
    try:
        import combat
        return combat.status_warning(r)
    except Exception as e:
        return "!! COMBAT LEDGER UNREADABLE -- %s" % e


def show(r, explain=False):
    """The board. At most about twenty lines on a quiet colony."""
    print(clock_line(r))
    # Disk-only ledger check: status already paid for the live snapshot, so the
    # safety reminder adds no bridge call and cannot mutate game/UI state.
    warning = _combat_warning(r)
    if warning:
        print(warning)
    if r.get("status") != "game_loaded":
        print("        status: %s -- every block below is empty because there is "
              "nothing to read, not because nothing is happening." % r.get("status"))

    rows = r.get("letters") or []
    if rows:
        print("LETTERS %d" % len(rows))
        for l in rows[:ROW_CAP]:
            # Age and STALE on every row, the way letters.py prints them: a
            # letter's wording is a report of the moment it arrived, and an
            # 8.6-hour-old bear letter drove three Lookout passes about a corpse.
            print("  %s %-46s %5.1fh %s"
                  % (letter_mark(l), _trim(l.get("label"), 46),
                     letters.age_hours(l),
                     "STALE" if letters.is_stale(l) else ""))
        if len(rows) > ROW_CAP:
            print("  ... +%d more (`python letters.py`)" % (len(rows) - ROW_CAP))

    messages = r.get("messages") or []
    if messages:
        print("MESSAGES %d  (these expire 13 real seconds after they appear)"
              % len(messages))
        for m in messages[:ROW_CAP]:
            age = m.get("ageSeconds")
            print("  %-5s %s" % ("%.0fs" % age if age is not None else "?",
                                 _trim(m.get("text"))))
        if len(messages) > ROW_CAP:
            print("  ... +%d more" % (len(messages) - ROW_CAP))

    alerts = r.get("alerts") or []
    if alerts:
        loud = [a for a in alerts if priority_rank(a.get("priority")) >= LOUD_FLOOR]
        print("ALERTS  %d (%d loud)" % (len(alerts), len(loud)))
        for a in (loud + [a for a in alerts if a not in loud])[:ROW_CAP]:
            print(alert_line(a))
            if explain and a.get("explanation"):
                print("        %s" % _trim(a["explanation"], 140))
        if len(alerts) > ROW_CAP:
            print("  ... +%d more (`python alerts.py`)" % (len(alerts) - ROW_CAP))

    blocks = r.get("blocks") or {}
    colonists = r.get("colonists") or []
    if colonists:
        print("COLONISTS %d" % len(colonists))
        for c in colonists:
            print(colonist_line(c))
    elif blocks.get("colonists"):
        print("COLONISTS none spawned on this map. (checked)")

    threats = r.get("threats") or {}
    # 2026-09-04: wildPredatorsNear and downedNear are new rows, and they are
    # the two the board was silent about on the night a timber wolf killed two
    # colonists. A predator 15 cells away is not "hostile" until it starts
    # hunting -- the board said THREATS none -- and a downed colonist is the
    # single most time-critical fact a between-turns read can carry.
    near = threats.get("wildPredatorsNear") or []
    near_count = threats.get("wildPredatorsNearCount", len(near))
    downed = threats.get("downedNear") or []
    downed_count = threats.get("downedNearCount", len(downed))
    nearby_hunts = guard_hunters(threats)
    if (threats.get("hostileCount") or threats.get("huntingPredatorCount")
            or near_count or downed_count or nearby_hunts):
        summary = threat_summary(r)
        # The hostile number already FOLDS the downed-near rows in (they get
        # back up); the last two are the raw lists, printed so the fold can be
        # checked rather than taken on trust.
        print("THREATS %s, %s   [lists: %d wild predator near, %d downed near]"
              % (hostile_phrase(summary), hunter_phrase(summary),
                 near_count or 0, downed_count or 0))
        for h in nearby_hunts:
            print("  !! %s (%s) hunting %s within 40 cells: supervised-play guard applies"
                  % (h.get("name"), h.get("thingId"), h.get("prey") or "unknown prey"))
        for h in (threats.get("hostiles") or [])[:ROW_CAP]:
            print("  !! %-18s %-22s at %s, %s cells from a colonist"
                  % (_trim(h.get("name"), 18), _trim(h.get("hostileReason"), 22),
                     _pos(h.get("position")), h.get("distanceToNearestColonist")))
        already = set(h.get("thingId") for h in nearby_hunts)
        for h in (threats.get("huntingPredators") or [])[:ROW_CAP]:
            if h.get("thingId") in already:
                continue        # already printed with its guard warning above
            print("  !! %-18s hunting %s at %s"
                  % (_trim(h.get("name"), 18), h.get("prey"),
                     _pos(h.get("position"))))
        for h in near[:ROW_CAP]:
            print("  !! %-18s WILD PREDATOR, not hunting yet, at %s, %s cells "
                  "from a colonist"
                  % (_trim(h.get("name") or h.get("defName"), 18),
                     _pos(h.get("position")), h.get("distanceToNearestColonist")))
        for h in downed[:ROW_CAP]:
            print("  !! %-18s DOWNED (alive, not a corpse -- GETS BACK UP), %s,"
                  " at %s, %s cells from a colonist"
                  % (_trim(h.get("name") or h.get("defName"), 18),
                     _owner(h), _pos(h.get("position")),
                     h.get("distanceToNearestColonist")))
        for label, rows_, count in (("wild predator", near, near_count),
                                    ("downed", downed, downed_count)):
            if count and len(rows_) > ROW_CAP:
                print("  ... +%d more %s (`python pawns.py --threats`)"
                      % (len(rows_) - ROW_CAP, label))
    elif blocks.get("threats"):
        print("THREATS none. (checked -- hostiles, predator hunts, wild "
              "predators near a colonist and the downed were all read.)")

    ui = r.get("ui") or {}
    if ui.get("modalOpen") or ui.get("mainTabOpen") or ui.get("selectedCount"):
        bits = []
        if ui.get("modalOpen"):
            bits.append("MODAL %s is eating map input" % ui.get("modalWindow"))
        if ui.get("mainTabOpen"):
            bits.append("tab %s open" % ui.get("mainTabDefName"))
        if ui.get("selectedCount"):
            bits.append("%d selected (%s)"
                        % (ui["selectedCount"], ui.get("selectedFirstLabel")))
        print("UI      " + "; ".join(bits))

    for s in r.get("skipped") or []:
        print("  ~~ %s: %s" % (s.get("field"), s.get("reason")))


def _pos(p):
    return "?" if not p else "%s,%s" % (p.get("x"), p.get("z"))


def _owner(row):
    """`wild` or the faction name. downedNear[] excludes player-faction pawns,
    so a colony animal never reaches this row -- say which anyway."""
    return row.get("faction") or "wild (no faction)"


def main():
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0

    try:
        rim.init()
        r = read(explanations="--explain" in argv, detail="--detail" in argv)
    except Exception as e:
        # LOUD. A printed board with nothing in it reads as "the colony is
        # quiet", and that is the one wrong answer this file can give.
        print("status.py FAILED -- NOTHING WAS READ. This is NOT 'all quiet'.")
        print("%s: %s" % (type(e).__name__, e))
        print("  Check the bridge: python setup.py")
        return 1

    if "--json" in argv:
        print(json.dumps(r, indent=1))
    elif "--brief" in argv:
        for line in brief_lines(r):
            print(line)
    else:
        show(r, explain="--explain" in argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
