"""Durable supervised game control.

  python play.py start [--speed Normal] [--allow-injured ID] [--injury-cooldown 180]
  python play.py speed Normal|Fast|Superfast
  python play.py status
  python play.py pause

``start`` returns after the hidden service acquires its lease. The service, not
the invoking model turn, renews it while the registered session host remains
alive. Companion safety runs inside RimWorld every frame and pauses on events.
Standing alerts are baselined at start and listed on the start line; a new
one arrives later as a message and never pauses the game. Events are durably
routed to the current Hands process by ``event_bus``; its quiet ``wait`` helper
can be used without polling the game.

A ``colonist_injury`` stop is remembered per colonist inside the companion. A
restart within ``--injury-cooldown`` seconds (default 180) does not stop again on
that pawn's minor injuries -- severity thresholds, downs and deaths still do --
so a wound that keeps arriving cannot turn every restart into a ten-second
epoch. ``--allow-injured <pawnId>`` says the same thing explicitly for one pawn.
"""
import argparse
import json
import os
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
    """The start line for colonists whose minor injuries will not stop time."""
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    if not rows:
        return ""
    def name(row):
        return str(row.get("pawnName") or row.get("pawnId") or "?")
    cooldown = [r for r in rows if r.get("reason") == "cooldown"]
    acknowledged = [r for r in rows if r.get("reason") != "cooldown"]
    parts = []
    if cooldown:
        seconds = max(int(r.get("secondsRemaining") or 0) for r in cooldown)
        parts.append("injury stops suppressed for %d s: %s (repeat injury within "
                     "cooldown; severe injuries and downs still stop)"
                     % (seconds, ", ".join(name(r) for r in cooldown)))
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
# it. Both of these are gated on the same ``IgnoredHostiles`` set inside the
# companion (SupervisedPlayTool: the hostile check and the PredatorHunt check
# share it), so one --ignore-hostile line covers both kinds.
BLOCKING_STOPS = ("hostile", "predator_hunt")


def blocking_threats(caller=None):
    """Every hostile and hunting predator that will refuse a start, with IDs.

    One refusal used to name one pawn, so a two-raider raid cost two refusals
    and two round trips to learn the second ThingID. This asks the companion
    once and lists them all.
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
    for key, why in (("hostiles", "hostile"), ("huntingPredators", "hunting predator")):
        for row in (threats.get(key) or []):
            if not isinstance(row, dict):
                continue
            thing = row.get("thingId")
            if not thing or thing in seen:
                continue
            seen.add(thing)
            rows.append({"thingId": thing, "why": why,
                         "name": row.get("name") or row.get("kindDef") or "?",
                         "downed": bool(row.get("downed")),
                         "distance": row.get("distanceToNearestColonist")})
    return rows


def blocking_threat_lines(rows):
    """The refusal's extra lines: every blocker, then the line to paste."""
    if not rows:
        return ["   home/status lists no hostile and no hunting predator. The "
                "blocker named above is the only one the companion saw; read "
                "`python status.py --brief` before acknowledging anything."]
    lines = ["   %d thing(s) will block a start, not just the one named above:"
             % len(rows)]
    for row in rows:
        lines.append("     %-28s %s%s%s"
                     % (row["thingId"], row["why"],
                        "" if row["distance"] is None else
                        ", %s cells from the nearest colonist" % row["distance"],
                        " (DOWNED)" if row["downed"] else ""))
    lines.append("   Acknowledging a threat is a decision, not a formality. With "
                 "a defensive plan for ALL of them:")
    lines.append("     python play.py start " + " ".join(
        "--ignore-hostile %s" % r["thingId"] for r in rows))
    lines.append("   Otherwise fight it: `python combat.py begin`, then bounded "
                 "`python combat.py advance 5`.")
    return lines


BINDING_REPAIR = (
    "   The runtime binding is what routes events and owns the clock. A Hands "
    "CANNOT fix it from inside a turn: hand back and report this line.\n"
    "   Core repairs it with:  python stream.py bind-check --repair")


def start(args, popen=subprocess.Popen, clock=time.time, sleeper=time.sleep):
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
        print("SUPERVISED PLAY already owned by %s (epoch %s, service pid %s)"
              % (existing.get("owner"), existing.get("epoch"), existing.get("pid")))
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
    ignored_hostiles = list(args.ignore_hostile)
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
              "leaseMs": 30000,
              "ignoredHostileIds": ",".join(ignored_hostiles),
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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("start")
    p.add_argument("--speed", choices=("Normal", "Fast", "Superfast"), default="Normal")
    p.add_argument("--mode", choices=("colony", "combat"), default="colony")
    p.add_argument("--ignore-hostile", action="append", default=[])
    p.add_argument("--allow-downed", action="append", default=[])
    p.add_argument("--allow-injured", action="append", default=[],
                   help="Pawn ID whose non-severe injuries must not stop the "
                        "clock this epoch. Repeatable.")
    p.add_argument("--injury-cooldown", type=float, default=180.0, metavar="SECONDS",
                   help="After a colonist_injury stop, that colonist's minor "
                        "injuries do not stop again for this long, across "
                        "restarts (default 180; 0 disables).")
    sub.add_parser("status")
    sub.add_parser("pause")
    changing = sub.add_parser("speed")
    changing.add_argument("value", choices=("Normal", "Fast", "Superfast"))
    args = ap.parse_args(argv)
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
