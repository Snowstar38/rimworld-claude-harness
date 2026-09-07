"""Background lease owner for ``home/supervised_play``.

The service is deliberately small: one short bridge call at a time, a 10-second
heartbeat for a 30-second lease, and durable cursor reads relayed to event_bus.
If its registered session host vanishes or changes identity it stops renewing,
asks the companion to pause, and exits. The in-game lease is the final backstop.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import game_session
import rim

HERE = Path(__file__).resolve().parent
STATE_DIR = HERE / "state"
RUNTIME = STATE_DIR / "session-runtime.json"
SERVICE_STATE = STATE_DIR / "play-service.json"
STOP = STATE_DIR / "play-service.stop"
TOOL = "home/supervised_play"
HEARTBEAT_SECONDS = 10.0
EVENT_SECONDS = 1.0
# Announcement letters pile up under supervised play unless something sweeps
# them: watch.py and run.py did, this service did not (M, 2026-09-07).
SWEEP_SECONDS = 30.0
# A queued long event (the autosave) refuses `start` and clears in about a
# second. Five tries across two seconds costs nothing and saves a manual restart.
START_ATTEMPTS = 5
START_RETRY_SECONDS = 0.5
DEFAULT_INJURY_COOLDOWN_MS = 180000
# The companion's own stop reasons that are not a safety guard firing.
STOP_KINDS = {"requested_pause": "requested", "lease_expired": "lease"}


def stop_kind(reason, fallback):
    """Why the service exited. The companion's reason refines the loop's."""
    if not reason:
        return fallback
    return STOP_KINDS.get(reason, "guard")


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:
        return None


def atomic_json(path, value):
    """Retries on PermissionError; a lost state write orphans the supervisor."""
    game_session.write(path, value, indent=1)


def pid_alive(pid):
    # os.kill(pid, 0) calls TerminateProcess on Windows. A status read must
    # never kill the supervisor (or the controlling session it checks).
    import runtime_binding
    return runtime_binding.process_identity(pid) is not None


def binding(runtime_path=RUNTIME):
    if Path(runtime_path) == RUNTIME:
        try:
            import runtime_binding
            row = runtime_binding.load()
        except Exception:
            row = None
    else:
        row = read_json(runtime_path)
    if not row:
        return None
    required = ("session_id", "host_pid", "host_started")
    if any(row.get(k) in (None, "") for k in required):
        return None
    return {k: row[k] for k in required}


def binding_alive(expected, runtime_path=RUNTIME, alive=pid_alive):
    current = binding(runtime_path)
    if current != expected:
        return False
    if Path(runtime_path) == RUNTIME:
        try:
            import runtime_binding
            return runtime_binding.alive(expected)
        except Exception:
            return False
    return alive(expected.get("host_pid"))


def publish_event(epoch, generation, row, publisher=None):
    """Relay one companion row; rejection leaves the cursor uncommitted."""
    # These remain in the companion's native ring/status history. They are
    # routine telemetry, not reasons to wake Hands and interrupt a decision.
    if (row.get("kind") or row.get("type")) in (
            "started", "injury_observed", "speed_changed", "requested_pause"):
        return True
    if publisher is None:
        import event_bus
        publisher = event_bus.publish
    cursor = row.get("cursor") or row.get("id")
    event_id = "supervised:%s:%s:%s" % (generation, epoch, cursor)
    kind = row.get("kind") or row.get("type") or "supervised_play"
    body = dict(row)
    captured = row.get("capturedAt")
    if captured is None and row.get("atMs") is not None:
        captured = float(row["atMs"]) / 1000.0
    meta = {"epoch": epoch}
    if kind == "notification_new":
        # Neutral and positive announcements ride along with the next wakeup;
        # a negative one (short circuit, dead crop, departed visitor) wakes a
        # parked Hands so it is looked at without the clock ever stopping.
        meta["wake"] = bool((row.get("event") or {}).get("negative")
                            or row.get("negative"))
    if kind == "hostiles_cleared":
        # The fight is over and pawns are still drafted or bleeding out on the
        # ground. Nothing pauses, so wake a parked Hands to finish the job.
        meta["wake"] = True
    if kind == "alert_new":
        # An alert never pauses the game, so only a Critical one is worth
        # waking a parked Hands; a High one rides along with the next wakeup.
        priority = str(((row.get("event") or {}).get("priority") or "")).lower()
        meta["wake"] = priority == "critical"
    result = publisher(kind, body, event_id=event_id,
                       captured_at=captured,
                       generation=generation, source="supervised_play",
                       meta=meta)
    return isinstance(result, dict) and result.get("accepted") is not False


def event_line(row):
    """One supervisor ring row as a readable line, for a stop with no reason."""
    kind = row.get("kind") or row.get("type") or "?"
    detail = " ".join(str(row.get("detail") or "").split())
    return "%s: %s" % (kind, detail[:160]) if detail else kind


def final_note(final, epoch):
    """Why the companion's closing status read told us nothing."""
    if not isinstance(final, dict):
        return "the read did not answer at all"
    if final.get("epoch") != epoch:
        return "it answered for epoch %s, not %s" % (final.get("epoch"), epoch)
    return "it carried no stopReason"


def relay_events(epoch, generation, cursor, call, publisher=None):
    reply = call({"op": "events", "afterCursor": int(cursor or 0),
                  "limit": 100})
    if not isinstance(reply, dict) or reply.get("success") is False:
        return cursor, reply
    if reply.get("gap"):
        gap_row = {"cursor": "gap-%s-%s" % (cursor, reply.get("oldestCursor")),
                   "kind": "supervised_play_gap", "gap": reply.get("gap"),
                   "oldestCursor": reply.get("oldestCursor"),
                   "requestedAfterCursor": cursor}
        if not publish_event(epoch, generation, gap_row, publisher):
            return cursor, dict(reply, relayError="event bus rejected gap")
    committed = cursor
    for row in reply.get("events") or []:
        if not publish_event(epoch, generation, row, publisher):
            return committed, dict(reply, relayError="event bus rejected %r" % row.get("cursor"))
        committed = row.get("cursor", committed)
    if not (reply.get("events") or []):
        committed = reply.get("nextCursor", committed)
    return committed, reply


def run(config, call=None, runtime_path=RUNTIME, state_path=SERVICE_STATE,
        stop_path=STOP, alive=pid_alive, publisher=None, clock=time.time,
        sleeper=time.sleep, max_cycles=None, init_func=None):
    real_call = call is None
    call = call or (lambda args: rim.game(TOOL, args, strict=False, timeout=8))
    expected = config["binding"]
    generation = config["generation"]
    if not binding_alive(expected, runtime_path, alive):
        raise RuntimeError("registered session host is missing or no longer matches")
    if real_call:
        (init_func or rim.init)()
    before = call({"op": "status"})
    before_cursor = before.get("newestCursor", 0) if isinstance(before, dict) else 0
    try:
        import runtime_binding
        service_started = runtime_binding.process_identity(os.getpid())
    except Exception:
        service_started = None
    if service_started is None:
        raise RuntimeError("could not record supervisor service process identity")
    # Identity on disk before the game call: a write that fails afterwards
    # leaves a running game whose epoch nobody can name.
    state = {"pid": os.getpid(), "owner": config["owner"], "epoch": None,
             "cursor": before_cursor, "binding": expected, "startedAt": clock(),
             "baselineAlerts": [], "processStarted": service_started,
             "ready": False}
    atomic_json(state_path, state)

    def commit(epoch=None):
        """Write state; if the game is already running, roll it back on failure."""
        try:
            atomic_json(state_path, state)
        except OSError as exc:
            if epoch is not None:
                try:
                    call({"op": "pause", "epoch": epoch, "owner": config["owner"]})
                except Exception:
                    pass
            raise RuntimeError(
                "could not write %s (%s); the game was paused again rather than "
                "left running unsupervised. Recover with: python play.py pause "
                "then python play.py start" % (state_path, exc))

    cooldown = config.get("injuryStopCooldownMs")
    args = {"op": "start", "speed": config["speed"],
            "mode": config.get("mode", "colony"),
            "leaseMs": config["leaseMs"], "owner": config["owner"],
            "ignoredAlertLabels": config.get("ignoredAlertLabels") or "",
            "ignoredHostileIds": config.get("ignoredHostileIds") or "",
            "ignoredDownedColonistIds": config.get("ignoredDownedColonistIds") or "",
            "ignoredInjuredColonistIds": config.get("ignoredInjuredColonistIds") or "",
            "injuryStopCooldownMs": int(DEFAULT_INJURY_COOLDOWN_MS
                                        if cooldown is None else cooldown)}
    # A refusal marked `retryable` is a long event -- the daily autosave holds
    # the clock for about a second and there is nothing to dismiss. Retrying is
    # the honest answer; reporting PLAY REFUSED is not. A window really being
    # open is not retryable and comes back on the first attempt.
    started = None
    for attempt in range(START_ATTEMPTS):
        started = call(args)
        if not (isinstance(started, dict) and started.get("retryable")):
            break
        if attempt + 1 < START_ATTEMPTS:
            sleeper(START_RETRY_SECONDS)
    if (not isinstance(started, dict) or started.get("success") is False
            or not started.get("epoch")):
        tail = ""
        if isinstance(started, dict) and started.get("retryable"):
            tail = (" (retried %d times over %.1f s; it never cleared)"
                    % (START_ATTEMPTS, START_ATTEMPTS * START_RETRY_SECONDS))
        raise RuntimeError("supervised_play start refused%s: %r" % (tail, started))
    epoch = started["epoch"]
    state["epoch"] = epoch
    commit(epoch)
    # The last few ring rows, kept so a stop that records no reason can still
    # name what the supervisor last saw. 2026-09-07: the clock stopped twice
    # after fire events with no guard reason on the board at all.
    recent = []

    def relay(from_cursor):
        new_cursor, reply = relay_events(epoch, generation, from_cursor, call,
                                         publisher)
        if isinstance(reply, dict):
            for row in reply.get("events") or []:
                recent.append(event_line(row))
            del recent[:-5]
        return new_cursor, reply

    cursor, initial_events = relay(before_cursor)
    if isinstance(initial_events, dict) and initial_events.get("relayError"):
        raise RuntimeError(initial_events["relayError"])
    if started.get("active") is not True:
        raise RuntimeError("supervised_play stopped during start: %s: %s"
                           % (started.get("stopReason"), started.get("stopDetail")))
    state.update({"cursor": cursor,
                  "baselineAlerts": started.get("baselineAlerts") or [],
                  "suppressedInjuryPawns": started.get("suppressedInjuryPawns") or [],
                  "ready": True})
    commit(epoch)
    next_heartbeat = clock() + HEARTBEAT_SECONDS
    next_events = clock()
    next_sweep = clock() + SWEEP_SECONDS
    sweep = real_call and config.get("sweepLetters", True)
    cycles = 0
    # Why the loop ended. "error" stands until something better is known, so an
    # exception on the way out is never recorded as an orderly stop.
    ended = "error"
    try:
        while True:
            if Path(stop_path).exists():
                ended = "stop-file"
                break
            if not binding_alive(expected, runtime_path, alive):
                ended = "binding-lost"
                break
            now = clock()
            if now >= next_heartbeat:
                reply = call({"op": "heartbeat", "epoch": epoch,
                              "owner": config["owner"], "leaseMs": config["leaseMs"]})
                if not isinstance(reply, dict) or reply.get("success") is False:
                    # A guard stop deactivates the supervisor, so the very next
                    # heartbeat is refused. That refusal IS the stop notice.
                    ended = "guard"
                    break
                # A safety event pauses and closes the run. A heartbeat is a
                # lease renewal only; it must never turn that stop into an
                # automatic restart.
                if reply.get("running") is False or reply.get("active") is False:
                    cursor, _ = relay(cursor)
                    state["cursor"] = cursor
                    ended = "guard"
                    break
                next_heartbeat = now + HEARTBEAT_SECONDS
            if now >= next_events:
                cursor, event_reply = relay(cursor)
                state["cursor"], state["lastRelayAt"] = cursor, now
                atomic_json(state_path, state)
                if (isinstance(event_reply, dict)
                        and (event_reply.get("running") is False
                             or event_reply.get("active") is False)):
                    ended = "guard"
                    break
                next_events = now + EVENT_SECONDS
            if sweep and now >= next_sweep:
                # letters.auto_dismiss keeps decisions and anything under ten
                # real seconds old; only standing announcements go.
                try:
                    import letters
                    gone = letters.auto_dismiss(verbose=False)
                    if gone:
                        recent.append("swept %d announcement letter(s): %s" % (
                            len(gone), ", ".join((r.get("label") or r.get("id") or "?")[:30]
                                                 for r, _ in gone)))
                        del recent[:-5]
                except Exception as exc:
                    recent.append("letter sweep failed: %s: %s" % (type(exc).__name__, exc))
                    del recent[:-5]
                next_sweep = now + SWEEP_SECONDS
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                return state
            sleeper(min(.25, max(.01, min(next_heartbeat, next_events) - clock())))
    finally:
        # Read the reason before pausing, or our own pause overwrites it.
        try:
            final = call({"op": "status"})
        except Exception:
            final = None
        try:
            call({"op": "pause", "epoch": epoch, "owner": config["owner"]})
        except Exception:
            pass
        reason = detail = None
        if isinstance(final, dict) and final.get("epoch") == epoch:
            reason, detail = final.get("stopReason"), final.get("stopDetail")
        if not reason and not detail:
            # An unnamed stop is the one thing the board must never print, and
            # this costs no extra bridge call: `recent` is what the 1 Hz relay
            # already saw. `state["lastEvents"]` carries it to status.py.
            detail = ("the service loop ended %r and the companion recorded "
                      "no stop reason for epoch %s (%s)"
                      % (ended, epoch, final_note(final, epoch)))
        state.update({"ready": False, "stoppedAt": clock(),
                      "stopReason": reason, "stopDetail": detail,
                      "lastEvents": recent[-5:],
                      "stopKind": stop_kind(reason, ended)})
        atomic_json(state_path, state)
    return state


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    args = ap.parse_args(argv)
    config = read_json(args.config)
    if not config:
        return 2
    try:
        run(config)
        return 0
    except Exception as exc:
        # Keep the epoch this run recorded: play.py pause needs it to recover.
        row = read_json(SERVICE_STATE) or {}
        if row.get("owner") != config.get("owner"):
            row = {}
        row.update({"pid": os.getpid(), "owner": config.get("owner"),
                    "ready": False, "error": str(exc), "stoppedAt": time.time()})
        # run()'s finally may already have named a better reason than "error".
        row.setdefault("stopKind", "error")
        row.setdefault("stopReason", None)
        row.setdefault("stopDetail", None)
        try:
            atomic_json(SERVICE_STATE, row)
        except OSError:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
