"""Durable supervised game control.

  python play.py start [--speed Normal|1] [--allow-injured ID] [--injury-cooldown 180]
  python play.py start --ignore-hostile            (bare = all; or a name, or a ThingID)
  python play.py start --ignore-predator           (bare = every wild predator this epoch)
  python play.py speed Normal|Fast|Superfast  (or 1|2|3)
  python play.py status
  python play.py pause

``start`` returns after the hidden service acquires its lease. The service, not
the invoking model turn, renews it while the registered session host remains
alive. Companion safety runs inside RimWorld every frame and pauses on events.
Standing alerts are baselined at start and listed on the start line; a new
one arrives later as a message and never pauses the game. Events are durably
routed to the current Hands process by ``event_bus``; its quiet ``wait`` helper
can be used without polling the game.

An injury or health stop is remembered per colonist inside the companion. For
``--injury-cooldown`` seconds after one (default 180, across restarts) nothing
about that pawn stops the clock again -- going down and dying still do -- so a
wound that keeps arriving cannot turn every restart into a ten-second epoch.
``--allow-injured <pawnId>`` says the same thing explicitly for one pawn, for
the whole epoch.

A hostile asleep or dormant more than 50 cells from every colonist does not
stop the clock and does not block ``start``. It is still listed, by name and
category ``hostile_dormant``, and it stops the clock the moment it stands up or
walks closer. A manhunter is never dormant.

A wild predator is scenery, not a threat. A wolverine on the map, hunting a
hare, or standing next to a colonist does not stop the clock and does not block
``start`` at any distance; it becomes a threat only when it is a manhunter or
when its current job targets a colonist, a colony animal or a colony prisoner.
That case is acknowledged with ``--ignore-predator`` (bare = every predator,
for the epoch), which never covers a hostile faction or a manhunter. Every
start refusal on a threat names the category each one fell in.

The guard stops on a colonist going down, dying, taking a serious new wound
(10 hit points, roughly a gunshot) or newly crossing a health threshold. A
scratch, an animal nip and a social fight between colonists never stop it.
A colonist already at or under ``--min-health`` when the epoch starts is
acknowledged the same way and named on the start line.

``start`` against a session that is already owned reads the companion. If the
guard has stopped and only the service is still alive, the clock is paused: the
old service is retired and a new epoch starts, rather than reporting ownership
over a stopped game.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import play_service
import rim
import game_session

HERE = Path(__file__).resolve().parent
STATE = HERE / "state"
CONFIG = STATE / "play-service-config.json"
SERVICE_STATE = play_service.SERVICE_STATE
STOP = play_service.STOP
DETACHED = 0x00000008 if os.name == "nt" else 0
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# RimWorld's own speed keys are 1/2/3, so a number is what a person reaches
# for. Both spellings mean the same speed.
SPEED_NAMES = ("Normal", "Fast", "Superfast")
SPEED_ALIASES = {"1": "Normal", "2": "Fast", "3": "Superfast"}


def speed_name(value):
    """``1|2|3`` or a name -> the companion's name for that speed."""
    token = str(value).strip()
    if token in SPEED_ALIASES:
        return SPEED_ALIASES[token]
    for name in SPEED_NAMES:
        if token.lower() == name.lower():
            return name
    raise argparse.ArgumentTypeError(
        "speed must be Normal, Fast, Superfast or 1, 2, 3 "
        "(1=Normal, 2=Fast, 3=Superfast). Got: %s" % value)


def service_state(row):
    """(alive, why). A bare False with no reason is what made this confusing."""
    if not row:
        return False, "no service state on record"
    if not row.get("ready"):
        return False, "service state says ready: false"
    recorded = row.get("processStarted")
    if recorded is None:
        return False, "service state records no process identity"
    try:
        import runtime_binding
    except Exception as exc:
        return False, "runtime_binding unavailable (%s)" % exc
    identity = runtime_binding.process_identity(row.get("pid"))
    if identity is None:
        return False, "service pid %s is gone" % row.get("pid")
    # Both sides are the same process creation time. Compare their string
    # forms: one side has been through JSON and may come back as a number,
    # and a numeric/string mismatch must not read as a dead service.
    if str(identity).strip() != str(recorded).strip():
        return False, ("service pid %s was reused (identity %s, recorded %s)"
                       % (row.get("pid"), identity, recorded))
    return True, "service pid %s alive" % row.get("pid")


def service_alive(row):
    return service_state(row)[0]


def suppressed_injuries(rows):
    """The start line for colonists whose injuries will not stop time."""
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return ""
    def name(row):
        return str(row.get("pawnName") or row.get("pawnId") or "?")
    cooldown = [r for r in rows if r.get("reason") == "cooldown"]
    below = [r for r in rows if r.get("reason") == "below_health_floor"]
    acknowledged = [r for r in rows
                    if r.get("reason") not in ("cooldown", "below_health_floor")]
    parts = []
    if cooldown:
        seconds = max(int(r.get("secondsRemaining") or 0) for r in cooldown)
        parts.append("injury stops suppressed for %d s: %s (a stop for that "
                     "pawn is still in cooldown; going down or dying still stops)"
                     % (seconds, ", ".join(name(r) for r in cooldown)))
    if below:
        floor = max(float(r.get("minHealthFraction") or 0.5) for r in below)
        parts.append("already under the %.2f health floor at start, so their "
                     "injuries are acknowledged: %s (going down or dying still "
                     "stops the clock)"
                     % (floor, ", ".join(name(r) for r in below)))
    if acknowledged:
        parts.append("injury stops acknowledged: %s"
                     % ", ".join(name(r) for r in acknowledged))
    return "; ".join(parts)


def summary_line(reply, service, alive):
    """One human line above the status JSON; it goes on screen during a stream."""
    companion = reply if isinstance(reply, dict) else {}
    service = service or {}
    epoch = companion.get("epoch") or service.get("epoch")
    if companion.get("active") is True:
        speed = companion.get("requestedSpeed") or service.get("speed") or "?"
        if alive:
            return "RUNNING (epoch %s, %s)" % (epoch, speed)
        return ("RUNNING UNSUPERVISED (epoch %s, %s); the clock is moving but the "
                "service process is gone -- python play.py pause" % (epoch, speed))
    reason = companion.get("stopReason") or service.get("stopReason")
    detail = companion.get("stopDetail") or service.get("stopDetail")
    if not reason and service.get("error"):
        # The service died before the companion had a reason of its own.
        reason, detail = "service error", service["error"]
    if not reason and not epoch:
        return "IDLE (no supervised-play epoch on record)"
    return "STOPPED by %s %s: %s (%s; epoch %s)" % (
        service.get("stopKind") or "guard", reason or "unknown",
        detail or "no detail recorded",
        "service alive" if alive else "service exited", epoch)


def standing_alerts(rows):
    """The alerts already up at start, told once. They never pause the game."""
    labels = [r.get("label") or r.get("alertKey") for r in (rows or [])]
    if not labels:
        return "standing alerts: none"
    return "standing alerts (%d): %s" % (len(labels), "; ".join(labels))


# The reason a supervised-play start refuses, mapped to the list that answers
# it. ``hostile`` and ``predator_hunt`` are gated on the same
# ``IgnoredHostiles`` set inside the companion, so one --ignore-hostile line
# covers both; the predator categories are additionally gated on
# ``IgnoredPredators`` / --ignore-predator, which can never wave a raid through.
BLOCKING_STOPS = ("hostile", "predator_hunt")

# The categories --ignore-predator is allowed to acknowledge. Everything else
# is a raid or a manhunter and needs the explicit --ignore-hostile decision.
# Two spellings of the same animal on purpose: `home/status`' hunting-predator
# list is classified here as ``predator_hunt``, and the guard's own
# ``stopThreats`` rows -- which is what a start refusal prints -- call it
# ``predator_hunting_ours``. The companion gates BOTH on IgnoredPredators
# (SupervisedPlayTool.IsIgnoredPredator), so a refusal built from the guard's
# rows has to offer the flag too; until 2026-09-12 it silently did not.
PREDATOR_CATEGORIES = ("predator_hunt", "predator_hunting_ours", "predator")

# A hostile asleep or dormant this far from every colonist is still ON the map
# and still listed -- it just is not a reason to hold the clock. 50 cells is
# past every vanilla weapon's range (44.9, the sniper rifle), so nothing beyond
# it can hit anybody without walking first, and walking changes the job. Five
# Sorne Geneline insectoids asleep in a cave 74-78 cells out refused every bare
# `start` on Threadneedle. The same numbers as combat.py's `end`, and these are
# the game's own JobDefs -- `LayDownAwake` is deliberately absent.
DORMANT_HOSTILE_CELLS = 50
DORMANT_JOBS = {"LayDown": "asleep", "LayDownResting": "asleep",
                "Wait_Asleep": "asleep", "RevenantSleep": "asleep",
                "Wait_AsleepDormancy": "dormant",
                "ActivityDormant": "dormant"}


def dormant_reason(row):
    """"asleep 76 cells away" for a hostile that cannot reach anybody, else None.

    A manhunter is never dormant -- the mental state IS the wakefulness -- and
    the caller checks that before asking.
    """
    sleeping = DORMANT_JOBS.get((row or {}).get("job"))
    distance = (row or {}).get("distanceToNearestColonist")
    if not sleeping or isinstance(distance, bool) or not isinstance(distance, (int, float)):
        return None
    if distance <= DORMANT_HOSTILE_CELLS:
        return None
    return "%s %s cells away; wakes -> stops" % (sleeping, distance)


# What each category means on one line, for the refusal.
CATEGORY_WORDS = {
    "hostile": "hostile faction",
    "manhunter": "MANHUNTER",
    "hostile_dormant": "hostile, asleep or dormant and out of reach",
    "predator_hunt": "predator hunting one of ours",
    "predator_hunting_ours": "predator hunting one of ours",
    "predator": "wild predator, hunting wildlife or just there",
}


def is_thing_id(token):
    """Is this token an ID rather than a name?

    The companion parses the trailing digits off whatever it is given
    (``PawnIds``), so anything ending in digits already IS an id to it, and
    anything else has to be resolved against the map first.
    """
    return bool(re.search(r"\d+$", str(token).strip()))


def blocking_threats(caller=None):
    """Every thing the guard classified, with its category, blockers first.

    One refusal used to name one pawn, so a two-raider raid cost two refusals
    and two round trips to learn the second ThingID. This asks the companion
    once and lists them all -- including the predators that do NOT stop the
    clock, because "why did it stop for that wolverine and not this one" is
    exactly the question the operator has.
    """
    if caller is None:
        rim.init()
        caller = lambda: rim.game("home/status",
                                  {"threats": True, "colonists": False,
                                   "letters": False, "alerts": False},
                                  strict=False, timeout=8)
    reply = caller()
    threats = (reply or {}).get("threats") or {}
    rows = []
    seen = set()

    def add(row, category, blocks):
        if not isinstance(row, dict):
            return
        thing = row.get("thingId")
        if not thing or thing in seen:
            return
        seen.add(thing)
        distance = row.get("distanceToNearestColonist")
        downed = bool(row.get("downed"))
        why = CATEGORY_WORDS.get(category, category)
        if category == "hostile_dormant":
            why = dormant_reason(row) or why
        rows.append({"thingId": thing, "category": category, "blocks": blocks,
                     "why": why,
                     "reason": row.get("ignoredReason") or row.get("hostileReason"),
                     "name": row.get("name") or row.get("kindDef") or "?",
                     "defName": row.get("defName"),
                     "downed": downed, "distance": distance})

    for row in (threats.get("hostiles") or []):
        reason = str((row or {}).get("hostileReason") or "")
        if reason.lower().startswith("manhunter"):
            add(row, "manhunter", True)
        elif dormant_reason(row):
            # Listed, named, and not a blocker. It stops the clock the moment
            # it stands up or walks inside 50 cells: the guard re-reads the job
            # ten times a second.
            add(row, "hostile_dormant", False)
        else:
            add(row, "hostile", True)
    for row in (threats.get("huntingPredators") or []):
        add(row, "predator_hunt", True)
    # Never blockers, always listed: a tame warg's hunt and a wolverine eating a
    # hare. Seeing them named as NOT stopping is how the operator learns the
    # rule instead of reaching for --ignore-hostile all.
    for row in (threats.get("huntersIgnored") or []):
        add(row, "predator", False)
    rows.sort(key=lambda r: not r["blocks"])
    return rows


def paste_lines(rows):
    """The two commands that answer a threat refusal, for whatever is on the map."""
    blockers = [r for r in rows if r.get("blocks")]
    predators = [r for r in blockers if r["category"] in PREDATOR_CATEGORIES]
    lines = []
    if blockers:
        lines.append("   Acknowledging a threat is a decision, not a formality. "
                     "With a defensive plan for ALL of them:")
        lines.append("     python play.py start " + " ".join(
            "%s %s" % ("--ignore-predator"
                       if r.get("category") in PREDATOR_CATEGORIES
                       else "--ignore-hostile", r["thingId"])
            for r in blockers))
    if predators and len(predators) == len(blockers):
        lines.append("   Every blocker is a wild predator. If that is all this "
                     "is, one flag covers them for the whole epoch (it never "
                     "covers a raid or a manhunter):")
        lines.append("     python play.py start --ignore-predator")
    elif predators:
        lines.append("   Wild predators among them can be acknowledged by name "
                     "instead: python play.py start --ignore-predator %s"
                     % " --ignore-predator ".join(
                         sorted(set(r["name"] for r in predators))))
    return lines


def threat_rows_lines(rows, header):
    """One line per classified thing: id, category, reason. Read aloud on stream."""
    lines = [header]
    for row in rows:
        lines.append("     %-28s %-11s %s%s%s"
                     % (row["thingId"], row["category"],
                        row.get("why") or "",
                        "" if row.get("distance") is None else
                        ", %s cells from the nearest colonist" % row["distance"],
                        "" if row.get("blocks", True) else
                        " -- does NOT stop the clock"))
    return lines


def blocking_threat_lines(rows):
    """The refusal's extra lines: every blocker, then the line to paste."""
    if not rows:
        return ["   home/status lists no hostile and no hunting predator. The "
                "blocker named above is the only one the companion saw; read "
                "`python status.py --brief` before acknowledging anything."]
    blockers = [r for r in rows if r.get("blocks")]
    lines = threat_rows_lines(
        rows, "   %d thing(s) will block a start, not just the one named above; "
              "%d other(s) were classified and will not:"
              % (len(blockers), len(rows) - len(blockers)))
    lines.extend(paste_lines(rows))
    lines.append("   Otherwise fight it: `python combat.py begin`, then bounded "
                 "`python combat.py advance 5`.")
    return lines


def companion_threat_lines(rows):
    """The same block, from the guard's own ``stopThreats`` instead of a re-read.

    Better than asking `home/status` again: these are the rows the guard
    actually classified at the instant it stopped, so the categories in the
    refusal are the categories that made the decision.
    """
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return []
    shaped = [{"thingId": r.get("thingId") or r.get("pawnId"),
               "category": r.get("category") or "?",
               "blocks": bool(r.get("stops")),
               "why": r.get("reason") or CATEGORY_WORDS.get(r.get("category"), ""),
               "name": r.get("name") or "?",
               "distance": r.get("distanceToNearestColonist")}
              for r in rows]
    blockers = [r for r in shaped if r["blocks"]]
    lines = threat_rows_lines(
        shaped, "   the guard classified %d thing(s) on the map; %d of them stop "
                "a start:" % (len(shaped), len(blockers)))
    lines.extend(paste_lines(shaped))
    return lines


BINDING_REPAIR = (
    "   The runtime binding is what routes events and owns the clock. A Hands "
    "CANNOT fix it from inside a turn: hand back and report this line.\n"
    "   Core repairs it with:  python stream.py bind-check --repair")


def companion_status(caller=None):
    """One `home/supervised_play status`. None when it could not be read."""
    if caller is None:
        try:
            rim.init()
        except Exception:
            return None
        caller = lambda: rim.game(play_service.TOOL, {"op": "status"},
                                  strict=False, timeout=8)
    try:
        reply = caller()
    except Exception:
        return None
    return reply if isinstance(reply, dict) else None


def retire_service(clock=time.time, sleeper=time.sleep, timeout=5.0):
    """Ask a service whose guard has already stopped to exit. -> did it.

    The service notices a stopped guard on its next heartbeat, up to ten
    seconds later. Until then it holds ownership of an epoch that is not
    running, which is what made `start` answer `already owned` over a paused
    game.
    """
    STATE.mkdir(parents=True, exist_ok=True)
    STOP.touch()
    deadline = clock() + timeout
    while True:
        if not service_alive(play_service.read_json(SERVICE_STATE)):
            return True
        if clock() >= deadline:
            return False
        sleeper(.1)


def owned_session(existing, status=None, clock=time.time, sleeper=time.sleep):
    """What to do about a live service `start` found. -> True when it owns a
    RUNNING clock and start should stop here.

    Raises when the state cannot be established: an unread companion is not
    evidence that the clock is running.
    """
    live = companion_status(status)
    epoch, pid = existing.get("epoch"), existing.get("pid")
    if live is None:
        raise RuntimeError(
            "a supervisor service (pid %s, epoch %s) is alive but the companion "
            "did not answer, so whether the clock is RUNNING is unknown. Read it "
            "with: python play.py status -- then python play.py pause and "
            "python play.py start" % (pid, epoch))
    if live.get("active") is True:
        print("SUPERVISED PLAY already owned by %s (epoch %s, service pid %s); "
              "the clock is RUNNING at %s"
              % (existing.get("owner"), epoch, pid,
                 live.get("requestedSpeed") or "?"))
        return True
    print("SUPERVISED PLAY service pid %s still holds epoch %s, but the guard "
          "stopped (%s) and left the clock PAUSED. Retiring it and starting a "
          "new epoch." % (pid, epoch, live.get("stopReason") or "no reason recorded"))
    if not retire_service(clock, sleeper):
        raise RuntimeError(
            "the old supervisor service (pid %s) did not exit within 5 s and the "
            "clock is still paused. Run: python play.py pause  then  "
            "python play.py start" % pid)
    return False


def resolve_threat_name(token, rows, flag, categories=None):
    """A name off the map -> its ThingID. Refuses rather than guessing.

    `--ignore-hostile wolverine` has to mean one animal. A miss names what IS
    there, because the operator typing a name has just read it off the screen
    and a silent no-op would start the clock over an unacknowledged threat.
    """
    candidates = [r for r in rows
                  if categories is None or r.get("category") in categories]
    needle = str(token).strip().lower()
    hits = [r for r in candidates
            if needle in str(r.get("name") or "").lower()
            or needle in str(r.get("defName") or "").lower()]
    if len(hits) == 1:
        return str(hits[0]["thingId"])
    listing = ", ".join("%s %s" % (r["name"], r["thingId"]) for r in candidates) \
        or "nothing of that kind is on the map"
    if not hits:
        raise RuntimeError("%s %s: no such thing on the map. What is there: %s"
                           % (flag, token, listing))
    raise RuntimeError(
        "%s %s matches %d things, and acknowledging the wrong one is not a "
        "small mistake. Name one by ThingID: %s"
        % (flag, token, len(hits),
           ", ".join("%s %s" % (r["name"], r["thingId"]) for r in hits)))


def acknowledged_hostiles(values, threats=None):
    """`--ignore-hostile` values with ``all`` expanded and names resolved.
    -> (ids, line or None).

    ``all`` -- which is also what a bare `--ignore-hostile` means -- is every
    blocker `home/status` reports right now, by ThingID. One that arrives later
    still stops the clock.
    """
    values = [str(x) for x in (values or [])]
    wants_all = any(x.strip().lower() == "all" for x in values)
    named = [x for x in values if x.strip().lower() != "all"]
    if not wants_all and all(is_thing_id(x) for x in named):
        return list(dict.fromkeys(named)), None
    rows = blocking_threats() if threats is None else threats
    resolved = [x if is_thing_id(x) else resolve_threat_name(x, rows, "--ignore-hostile")
                for x in named]
    if not wants_all:
        return list(dict.fromkeys(resolved)), None
    blockers = [r for r in rows if r.get("blocks", True)]
    ids = list(dict.fromkeys(resolved + [str(r["thingId"]) for r in blockers]))
    if not blockers:
        line = ("--ignore-hostile all: home/status reports no hostile and no "
                "hunting predator, so nothing was acknowledged")
    else:
        line = ("--ignore-hostile all: acknowledging %d current threat(s) -- %s. "
                "One that arrives later still stops the clock."
                % (len(blockers), ", ".join(
                    "%s (%s)" % (r["thingId"], r.get("category") or r.get("why"))
                    for r in blockers)))
    return ids, line


def needs_map_read(hostiles, predators):
    """Does either ignore flag have to see the map first?

    A plain list of ThingIDs does not, and neither does `--ignore-predator all`
    -- that one is a literal the companion understands. Only `all` on the
    hostile side (a snapshot of ids) and a NAME on either side need a read.
    """
    for x in hostiles or []:
        if str(x).strip().lower() == "all" or not is_thing_id(x):
            return True
    for x in predators or []:
        if str(x).strip().lower() != "all" and not is_thing_id(x):
            return True
    return False


def acknowledged_predators(values, threats=None):
    """`--ignore-predator` values. -> (tokens, line or None).

    Unlike `--ignore-hostile all`, a bare `--ignore-predator` is passed to the
    companion as the literal ``all`` and holds for the whole epoch: predators
    wander in and out, and one wolverine that kept wandering back stopped the
    clock on four separate nights (Threadneedle, 2026-09-08). It acknowledges
    only the predator categories -- a raid or a manhunter still stops the clock,
    whatever this flag says.
    """
    values = [str(x) for x in (values or [])]
    if not values:
        return [], None
    if any(x.strip().lower() == "all" for x in values):
        return ["all"], ("--ignore-predator all: no wild predator stops the "
                         "clock this epoch, including ones that arrive later. A "
                         "hostile faction and a manhunter still do.")
    rows = blocking_threats() if threats is None else threats
    ids = [x if is_thing_id(x) else resolve_threat_name(
        x, rows, "--ignore-predator", PREDATOR_CATEGORIES) for x in values]
    ids = list(dict.fromkeys(ids))
    return ids, ("--ignore-predator: acknowledging %d predator(s) -- %s. A "
                 "hostile faction and a manhunter still stop the clock."
                 % (len(ids), ", ".join(ids)))


def start(args, popen=subprocess.Popen, clock=time.time, sleeper=time.sleep,
          status=None):
    bound = play_service.binding()
    if not bound or not play_service.binding_alive(bound):
        try:
            import runtime_binding
            _state, detail = runtime_binding.describe()
        except Exception:
            detail = "state/session-runtime.json must name a live session host"
        raise RuntimeError("runtime binding unavailable -- %s\n%s"
                           % (detail, BINDING_REPAIR))
    existing = play_service.read_json(SERVICE_STATE)
    if service_alive(existing):
        if existing.get("binding") != bound:
            raise RuntimeError("a supervisor service is alive for a different session host; pause it or wait for its lease to expire")
        if owned_session(existing, status, clock, sleeper):
            return 0
    STATE.mkdir(parents=True, exist_ok=True)
    try:
        STOP.unlink()
    except FileNotFoundError:
        pass
    owner = "%s:%s" % (bound["session_id"], uuid.uuid4().hex)
    generation = (game_session.current() or {}).get("generation")
    if not generation:
        raise RuntimeError("loaded-game generation unavailable; run setup.py first")
    # One read of the map answers both flags; a plain list of IDs, and
    # `--ignore-predator all`, read nothing at all.
    ignore_predator = list(getattr(args, "ignore_predator", None) or [])
    threats = (blocking_threats()
               if needs_map_read(args.ignore_hostile, ignore_predator) else None)
    ignored_hostiles, acknowledged_line = acknowledged_hostiles(
        args.ignore_hostile, threats)
    if acknowledged_line:
        print(acknowledged_line)
    ignored_predators, predator_line = acknowledged_predators(
        ignore_predator, threats)
    if predator_line:
        print(predator_line)
    if args.mode == "combat":
        import combat
        ledger = combat.load_ledger()
        if not ledger or not ledger.get("active"):
            raise RuntimeError("combat mode needs an active combat.py ledger")
        stale = combat.stale_reason(ledger, None, combat._identity())
        if stale:
            raise RuntimeError("combat mode refused a stale combat ledger: %s" % stale)
        for order in (ledger.get("orders") or {}).values():
            target = order.get("target") or {}
            target_id = target.get("thingId") or order.get("targetId")
            if target_id and target.get("hostileToPlayer") is True:
                ignored_hostiles.append(str(target_id))
        ignored_hostiles = list(dict.fromkeys(ignored_hostiles))
        if not ignored_hostiles:
            raise RuntimeError("combat mode needs at least one known enemy ID; pass --ignore-hostile Thing_ID (this acknowledges only that enemy)")
    config = {"binding": bound, "generation": generation,
              "owner": owner, "speed": args.speed,
              "mode": args.mode,
              "minHealthFraction": getattr(args, "min_health", 0.5),
              "leaseMs": 30000,
              "ignoredHostileIds": ",".join(ignored_hostiles),
              "ignoredPredatorIds": ",".join(ignored_predators),
              "ignoredDownedColonistIds": ",".join(args.allow_downed),
              "ignoredInjuredColonistIds": ",".join(getattr(args, "allow_injured", []) or []),
              "injuryStopCooldownMs": int(round(
                  float(getattr(args, "injury_cooldown", 180) or 0) * 1000))}
    play_service.atomic_json(CONFIG, config)
    popen([sys.executable, str(HERE / "play_service.py"), str(CONFIG)],
          cwd=str(HERE), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
          stderr=subprocess.DEVNULL, close_fds=True,
          creationflags=DETACHED | NO_WINDOW)
    deadline = clock() + 5.0
    while clock() < deadline:
        row = play_service.read_json(SERVICE_STATE)
        if row and row.get("owner") == owner:
            if row.get("ready"):
                line = ("SUPERVISED PLAY started at %s (epoch %s); service pid %s; %s"
                        % (args.speed, row.get("epoch"), row.get("pid"),
                           standing_alerts(row.get("baselineAlerts"))))
                held = suppressed_injuries(row.get("suppressedInjuryPawns"))
                print(line + ("; " + held if held else ""))
                return 0
            if row.get("error"):
                raise RuntimeError(enrich_start_error(row))
        sleeper(.05)
    raise RuntimeError(
        "supervisor service did not become ready within 5 seconds; the game "
        "may be running unsupervised -- recover with: python play.py pause "
        "then python play.py start")


def enrich_start_error(row, threats=None):
    """A start refusal, plus every other thing that would refuse it next.

    ``play_service`` raises ``supervised_play stopped during start: <reason>:
    <detail>`` naming exactly one pawn and not even its ThingID.
    """
    error = str(row.get("error") or "")
    if not any(("%s:" % kind) in error or (": %s " % kind) in error
               for kind in BLOCKING_STOPS):
        return error
    # The guard's own classification, carried out through the service state,
    # beats a second `home/status` read: it is what actually made the decision,
    # and until 2026-09-11 the two disagreed about wild predators entirely.
    lines = companion_threat_lines(row.get("stopThreats"))
    if lines:
        return error + "\n" + "\n".join(lines)
    try:
        rows = blocking_threats() if threats is None else threats
    except Exception as exc:
        return error + ("\n   (could not list the other blockers: %s: %s)"
                        % (type(exc).__name__, exc))
    return error + "\n" + "\n".join(blocking_threat_lines(rows))


def status():
    rim.init()
    reply = rim.game(play_service.TOOL, {"op": "status"}, strict=False, timeout=8)
    service = play_service.read_json(SERVICE_STATE) or {}
    current_binding = play_service.binding()
    alive, why = service_state(service)
    print(summary_line(reply, service, alive))
    print(json.dumps({"companion": reply, "service": service,
                      "serviceAlive": alive, "serviceAliveReason": why,
                      "bindingMatches": service.get("binding") == current_binding}, indent=1))
    return 0


def pause():
    row = play_service.read_json(SERVICE_STATE) or {}
    rim.init()
    if not row.get("epoch") or not row.get("owner"):
        reply = rim.game("rimworld/pause_game", {"pause": True},
                         strict=False, timeout=8)
        if isinstance(reply, dict) and reply.get("success") is False:
            raise RuntimeError("emergency pause refused: %r" % reply)
        print("GAME paused (no owned supervised-play session)")
        return 0
    before = rim.game(play_service.TOOL, {"op": "status"},
                      strict=False, timeout=8)
    if isinstance(before, dict) and before.get("active") is False and before.get("paused") is True:
        STATE.mkdir(parents=True, exist_ok=True)
        STOP.touch()
        print("SUPERVISED PLAY already paused (epoch %s)" % row["epoch"])
        return 0
    reply = rim.game(play_service.TOOL,
                     {"op": "pause", "epoch": row["epoch"],
                      "owner": row["owner"]}, strict=False, timeout=8)
    STATE.mkdir(parents=True, exist_ok=True)
    STOP.touch()
    if isinstance(reply, dict) and reply.get("success") is False:
        verified = rim.game(play_service.TOOL, {"op": "status"},
                            strict=False, timeout=8)
        if not (isinstance(verified, dict) and verified.get("active") is False
                and verified.get("paused") is True):
            print("SUPERVISED PLAY pause refused (epoch %s): %s" %
                  (row["epoch"], reply))
            return 1
    print("SUPERVISED PLAY paused (epoch %s)" % row["epoch"])
    return 0


def speed(value):
    row = play_service.read_json(SERVICE_STATE) or {}
    if not row.get("epoch") or not row.get("owner") or not service_alive(row):
        raise RuntimeError("no live owned supervised-play service")
    rim.init()
    reply = rim.game(play_service.TOOL,
                     {"op": "speed", "epoch": row["epoch"],
                      "owner": row["owner"], "speed": value},
                     strict=False, timeout=8)
    if not isinstance(reply, dict) or reply.get("success") is False:
        raise RuntimeError("speed change refused: %r" % reply)
    print("SUPERVISED PLAY speed %s (epoch %s)" % (value, row["epoch"]))
    return 0


def parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("start")
    p.add_argument("--speed", type=speed_name, default="Normal",
                   metavar="Normal|Fast|Superfast|1|2|3")
    p.add_argument("--mode", choices=("colony", "combat"), default="colony")
    p.add_argument("--min-health", type=float, default=0.5, metavar="FRACTION",
                   help="Health floor (default 0.5). Crossing it from above "
                        "stops the clock; a colonist already under it at start "
                        "is acknowledged and named on the start line. The 0.15 "
                        "health-drop, down and death guards remain active.")
    # nargs="?" with const="all": a bare `--ignore-hostile` was an argparse
    # error on stream (Threadneedle, 2026-09-08) even though the refusal it
    # answers prints `--ignore-hostile all`. Bare now means all.
    p.add_argument("--ignore-hostile", action="append", nargs="?", const="all",
                   default=[], metavar="NAME|THING_ID|all",
                   help="Acknowledge one hostile by name or ThingID; bare "
                        "--ignore-hostile (or `all`) takes every blocker on the "
                        "map right now. Repeatable. One that arrives later "
                        "still stops the clock.")
    p.add_argument("--ignore-predator", action="append", nargs="?", const="all",
                   default=[], metavar="NAME|THING_ID|all",
                   help="Acknowledge a WILD PREDATOR hunting one of ours, by "
                        "name or ThingID; bare --ignore-predator holds for "
                        "every predator this epoch, including ones that arrive "
                        "later. A hostile faction and a manhunter still stop "
                        "the clock. Repeatable.")
    p.add_argument("--allow-downed", action="append", default=[])
    p.add_argument("--allow-injured", action="append", default=[],
                   help="Pawn ID whose injuries must not stop the clock this "
                        "epoch; going down or dying still does. Repeatable.")
    p.add_argument("--injury-cooldown", type=float, default=180.0, metavar="SECONDS",
                   help="After an injury or health stop, nothing about that "
                        "colonist stops the clock again for this long, across "
                        "restarts (default 180; 0 disables). Downs and deaths "
                        "are unaffected.")
    sub.add_parser("status")
    sub.add_parser("pause")
    changing = sub.add_parser("speed")
    changing.add_argument("value", type=speed_name,
                          metavar="Normal|Fast|Superfast|1|2|3")
    return ap


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "start":
            return start(args)
        if args.command == "status":
            return status()
        if args.command == "speed":
            return speed(args.value)
        return pause()
    except Exception as exc:
        print("PLAY REFUSED -- %s" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
