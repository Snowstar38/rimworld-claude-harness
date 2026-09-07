"""Run a short guarded review slice and pause when it ends or something happens.

  python run.py [seconds] [speed] [interval] [--say "..."] [--mood <mood>]
  run.until(done)              # up to five seconds, same stopping rules
  run.run(seconds, speed)      # requests longer than five seconds yield early

**The turn clock rides along.** Every verbose run prints `turnclock`'s one-line
budget warning beside its report once the turn is past five minutes, and past
eight minutes `run()` refuses to start a pulse longer than 20 seconds -- a turn
that far over is not short of game time. `--force` overrides; `until()` does
not gate at all, so `move.goto`'s walks are never cut off.

**Stops on:** a new letter; a new transient message (the fading top-left text,
which expires after 13 real seconds and is the only channel that can be lost);
any hostile at any range; a predator on a hunt job within HUNT_RADIUS; a
colonist newly downed; an outside pause, speed change or modal dialog. Returns
`(reason, detail)`.

**Alerts never stop a pulse.** Any that appeared during it are diffed here and
printed as `NEW ALERT:` lines in the report.

**An outside pause always wins.** A key press in the game window, or a
`pause_game` from another session, stops this loop and is never undone.

The safety watcher runs in short calls:

* `home/play_until_event` (companion DLL) pauses at the moment of an event.
  Each bridge call is at most one second, and the whole command yields after
  five seconds with `review needed`, even when more time was requested.
* If the companion is unavailable, the clock is paused and the call returns.
  Running blind is never a fallback.

`step_game_ticks` is not an option here: `pauseFirst` defaults true, so it sets
Paused and advances anyway. Step it once, never in a loop.
"""
import re, sys, time, rim, watch, letters, turnclock
HUNT_RADIUS = 40
TOOL = "home/play_until_event"
# A bridge call monopolises the agent and every other bridge client until it
# returns.  Keep each pulse short enough that new instructions can be acted on.
MAX_PULSE_SECONDS = 1.0
MAX_REVIEW_SECONDS = 5.0
GUARD_RPC_TIMEOUT = 2.0

# stopReason from the companion -> the reason string this module returns.
# "done" and "time up" are set by the loop; move.goto tests `reason != "done"`.
_REASON = {
    "letter": "letter",
    "message": "message",
    "alert": "alert",
    "hostile": "threat",
    "predator_hunt": "threat",
    "colonist_downed": "colonist down",
    "colonist_health": "colonist hurt",
    "external_pause": "EXTERNAL PAUSE (source unknown)",
    "force_paused": "a dialog is open",
    "speed_changed": "SPEED CHANGED BY SOMEONE ELSE",
    "session_changed": "the game session changed",
}
# Reasons where the game is already stopped by someone else. Do not pause again.
_EXTERNAL = ("external_pause", "force_paused", "speed_changed", "session_changed")


class _NoCompanion(Exception):
    """The companion tool is missing or failed. Never swallowed silently."""


def _guard_game(name, args, strict=False):
    """Guard RPC with a client deadline; tolerate older injected clients."""
    try:
        return rim.game(name, args, strict=strict, timeout=GUARD_RPC_TIMEOUT)
    except TypeError as e:
        if "unexpected keyword argument 'timeout'" not in str(e):
            raise
        return rim.game(name, args, strict=strict)


def _pause_verified(reply):
    """Only the bridge's explicit postcondition proves that time is stopped."""
    return (isinstance(reply, dict) and reply.get("success") is True
            and reply.get("paused") is True)


def _letters():
    return {l.get("id") for l in (_guard_game("rimworld/list_letters", {}).get("letters") or [])}


def _ticks():
    """ticksGame, and the same number handed to the stream on the way past.

    The overlay stamps its feed with the colony clock and has its own poller
    for when nobody is playing -- but a poll is a bridge call, and the bridge
    serialises behind whatever this loop is doing. Pushing the number we
    already have costs one local HTTP POST on a daemon thread and keeps that
    poller asleep for the whole session. Fire-and-forget; it cannot fail a run.
    """
    t = _guard_game("rimworld/get_game_info", {}).get("ticksGame")
    try:
        import overlay_client as _ov
        _ov.push_tick(t)
    except Exception:
        pass
    return t


def _alert_labels():
    """Current High+ alerts, keyed stably so a moving ` xN` count is not news."""
    out = {}
    try:
        rows = _guard_game("rimworld/list_alerts", {}).get("alerts") or []
    except Exception:
        return out
    for a in rows:
        if (a.get("priority") or "Medium") == "Medium" or not a.get("active", True):
            continue
        label = a.get("label") or ""
        key = "%s|%s" % (a.get("type") or "?", re.sub(r"\s+x\d+$", "", label))
        out[key] = label
    return out


def _sig(rows):
    """A condition's identity: who it is, not where they are (they move)."""
    return ";".join(sorted(str(h.get("id") or h.get("thingId") or
                                   h.get("idForm") or "unknown") for h in rows))


def _wild_hunters(r):
    """The hunting predators that are not ours. Rule lives in `watch.wild_hunter`.

    The colony owns a tame warg (war merchant, day ~30) that hunts wild animals
    FOR us. It runs a PredatorHunt job like any cougar, so the companion's
    hunt watch fired on it at entry -- 0 ticks, every call, forever.

    The companion has already applied the job test before it puts a pawn in
    `hunters` (`PlayUntilEventTool.cs:621-624`, and `DescribePawn` emits both
    `job` and `faction`), so only the faction half bites here; asking
    `watch.wild_hunter` anyway is what keeps this path,
    `watch._threats_from_list` and `verify.py` from drifting apart -- which is
    exactly how the degraded polling path kept the bug after this one lost it.
    """
    return [h for h in (r.get("hunters") or []) if watch.wild_hunter(h)]


def _threat_rows(r):
    """Companion pawn rows -> watch.threats()' (dist, label, x, z) tuples."""
    out = []
    for tag, key in (("HOSTILE", "hostiles"), ("HUNTING", "hunters")):
        for p in r.get(key) or []:
            pos = p.get("position") or {}
            d = p.get("nearestColonistDistance")
            out.append((d if d is not None else 9999,
                        "%s %s" % (tag, p.get("defName") or "?"),
                        pos.get("x", -1), pos.get("z", -1)))
    return sorted(out)


def _report(reason, detail, r, elapsed, wall, new_alerts=()):
    print("ran %d ticks (%.1f in-game hours) in %.1fs wall -- stopped: %s"
          % (elapsed, elapsed / 2500.0, wall, reason))
    # The turn clock, beside the step line, silent under five minutes. A fork
    # cannot feel wall time passing; this is the only place it is told.
    turnclock.print_budget_line("")
    for d in detail:
        print("   !! %s at %d,%d (%s cells)" % (d[1], d[2], d[3], d[0]))
    for m in (r or {}).get("messages") or []:
        if m.get("stopped"):
            print("   >>> MESSAGE [%s] %s" % (m.get("messageType"), m.get("text")))
    for label in new_alerts or []:
        print("   NEW ALERT: %s" % label)
    for c in (r or {}).get("downed") or []:
        print("   !! %s is %s" % (c.get("name"), "DEAD" if c.get("dead") else "DOWN"))
    # Seen and deliberately not stopped on. Counted so the filter is never silent.
    ign = (r or {}).get("messagesIgnored") or []
    if ign:
        print("   ~~ %d message(s) not stopped on: %s"
              % (len(ign), "; ".join((m.get("text") or "")[:60] for m in ign[:4])))


def _print_letters(before):
    """The letter's choices, not just its label -- the label alone cannot tell a
    decision from an announcement, and this is the tightest spot in the harness:
    the game is already paused on it."""
    try:
        new = letters.new_letters(before)
        # To the stream first, with each letter's own arrival tick. This is the
        # tightest spot in the harness -- the game is paused on the letter -- so
        # it is also the truest moment to show it. Swallows everything itself.
        letters.post_new_to_overlay(new)
        for l in new:
            real = letters.real_choices(l)
            mark = ("(%d choices)" % len(real) if real
                    else "(the game wants this opened)" if l["auto"]
                    else "(announcement)")
            print("   >>> NEW LETTER %s: %s" % (mark, l["label"]))
            for c in real:
                print("          [%s] %s%s"
                      % (c["index"], c["text"],
                         " -- DISABLED: %s" % c["disabledReason"] if c["disabled"] else ""))
            if letters.is_decision(l):
                print("       open it: python letters.py open %s" % l["id"])
    except Exception as e:
        print("   ~~ could not read the new letters (%s); run `python letters.py`"
              % str(e)[:110])
        for l in _guard_game("rimworld/list_letters", {}).get("letters") or []:
            if l.get("id") in (_letters() - before):
                print("   NEW LETTER:", l.get("label"))


def _companion(done, seconds, speed, interval, verbose, ignored_hostile_ids=None,
               ignored_downed_ids=None):
    """Chained home/play_until_event calls. Raises _NoCompanion if unusable."""
    poll = max(50, int(min(interval, 0.25) * 1000))
    chunk = max(250, int(min(interval, MAX_PULSE_SECONDS) * 1000))
    ignored_hostile_ids = ignored_hostile_ids or []
    ignored_hostile_ids = ",".join(str(x) for x in ignored_hostile_ids)
    ignored_downed_ids = [str(x) for x in (ignored_downed_ids or [])]
    if ignored_downed_ids:
        print("   ~~ acknowledged downed colonist ID(s) for this invocation: %s"
              % "; ".join(ignored_downed_ids))
    message_since_tick = -1
    requested_seconds = max(0.0, float(seconds))
    seconds = min(requested_seconds, MAX_REVIEW_SECONDS)
    # list_letters carries a clock and therefore lets game_session invalidate
    # stale run memo state before anything reads that memo.
    before = _letters()
    alerts_before = _alert_labels()
    t0, w0 = _ticks(), time.time()
    reason, detail, r = "time up", [], None
    running = False

    while time.time() - w0 < seconds:
        left = seconds - (time.time() - w0)
        r = _guard_game(TOOL, {
            "speed": speed,
            "maxDurationMs": int(min(chunk, left * 1000)),
            "pollIntervalMs": poll,
            "pauseOnBudget": False,
            # Alerts are the low-stakes channel: never a stop, diffed below.
            "watchAlerts": False,
            "stopOnCurrentAlerts": False,
            "stopOnCurrentDownedColonists": True,
            "ignoredDownedColonistIds": ",".join(ignored_downed_ids),
            "huntWithin": HUNT_RADIUS,
            "ignoredHostileIds": ignored_hostile_ids,
            "messageSinceTick": message_since_tick,
            # Never lift a pause this loop did not apply.
            "requireRunningAtEntry": running,
        }, strict=False)

        if not isinstance(r, dict) or "stopReason" not in r:
            raise _NoCompanion(str(r)[:160])
        stop = r["stopReason"]
        if stop in ("error", "unavailable", "busy"):
            raise _NoCompanion("%s: %s" % (stop, r.get("stopDetail")))

        # Threats are level-triggered. A previously reported predator or
        # hostile remains a reason to stop; only an explicit hostile ID can be
        # baselined. This avoids a same-name animal masking a new arrival.
        if stop in ("predator_hunt", "hostile"):
            rows = (_wild_hunters(r) if stop == "predator_hunt"
                    else (r.get("hostiles") or []))
            if stop == "predator_hunt" and not rows:
                # Companion rows that Python proves are tame cannot stop a run.
                # Reaching here with no wild rows is a watcher disagreement;
                # fail closed rather than disabling predator observation.
                reason = "watcher disagreement"
                break
        if stop != "budget_elapsed":
            reason = _REASON.get(stop, stop)
            detail = _threat_rows(r)
            break
        # A message born after the preceding snapshot but before this call's
        # entry must not become part of the next baseline.  The companion uses
        # this tick watermark to report such a still-live message immediately.
        message_since_tick = r.get("endTick", -1)
        if message_since_tick is None:
            message_since_tick = -1
        running = True
        if done is not None and done():
            reason = "done"
            break

    if reason == "time up" and requested_seconds > seconds:
        reason = "review needed"
    if (r or {}).get("stopReason") not in _EXTERNAL:
        try:
            pause_result = _guard_game("rimworld/pause_game", {}, strict=False)
            if not _pause_verified(pause_result):
                reason = "pause could not be verified"
                print("** PAUSE COULD NOT BE VERIFIED ** bridge reply: %r"
                      % pause_result)
        except Exception as e:
            reason = "pause could not be verified"
            print("** PAUSE COULD NOT BE VERIFIED ** %s" % str(e)[:160])

    elapsed = (_ticks() or 0) - (t0 or 0)
    new_alerts = [v for k, v in _alert_labels().items() if k not in alerts_before]
    if verbose:
        _report(reason, detail, r, elapsed, time.time() - w0, new_alerts)
        if reason == "letter":
            _print_letters(before)
    return reason, detail


def _polling(done, seconds, speed, interval, verbose):
    """Pre-companion client-side loop. Blind to messages and alerts."""
    before = _letters()
    rim.game("rimworld/set_time_speed", {"speed": speed})
    t0, w0 = _ticks(), time.time()
    last = t0
    reason, detail = "time up", []
    while time.time() - w0 < seconds:
        time.sleep(interval)
        now = _ticks()
        if now == last:
            # Nothing advanced: a letter force-paused, or a person did.
            if _letters() - before:
                reason = "letter"
            elif rim.game("rimworld/get_ui_state", {}).get("nonImmediateDialogWindowOpen"):
                reason = "a dialog is open"
            else:
                reason = "EXTERNAL PAUSE (source unknown)"
            break
        last = now
        if done is not None and done():
            reason = "done"
            break
        hits = watch.threats(None)
        # Hostile at ANY range. A hunting predator only if near enough to switch
        # prey -- a wolf hunting a hare 104 cells away stopped the loop and told
        # us nothing. The faction filter is NOT repeated here: `watch.threats`
        # applies `watch.wild_hunter` before it ever writes a "HUNTING " label
        # (see `watch._threats_from_list`), so our own warg cannot reach this
        # list at all. It used to, and this degraded path was where the bug
        # survived after the companion path was fixed -- three copies of one
        # rule, two of them wrong. One copy now.
        alarming = [h for h in hits
                    if h[1].startswith("HOSTILE")
                    or (h[1].startswith("HUNTING") and h[0] <= HUNT_RADIUS)]
        if alarming:
            reason, detail = "threat", alarming
            break
        if _letters() - before:
            reason = "letter"
            break
    rim.game("rimworld/pause_game", {})
    elapsed = (_ticks() or 0) - (t0 or 0)
    if verbose:
        _report(reason, detail, None, elapsed, time.time() - w0)
        if reason == "letter":
            _print_letters(before)
    return reason, detail


def until(done=None, seconds=60, speed="Superfast", interval=1.5, verbose=True,
          ignored_hostile_ids=None, ignored_downed_ids=None):
    """Run time until done() is true or something stops it. -> (reason, detail).

    `done` is polled between chained companion calls, i.e. every `interval`.
    Everything else is watched inside the game every 250 ms.

    The command itself yields after five real seconds so its caller can receive
    new information. A longer requested duration returns ``review needed`` and
    can be continued deliberately with another call.
    """
    try:
        return _companion(done, seconds, speed, interval, verbose,
                          ignored_hostile_ids, ignored_downed_ids)
    except Exception as e:
        # Fail closed.  This may itself time out behind a legacy long-running
        # call, but it is still the only safe request to enqueue.
        paused = False
        try:
            pause_result = _guard_game("rimworld/pause_game", {}, strict=False)
            paused = _pause_verified(pause_result)
        except Exception as pause_error:
            print("** WATCHER FAILED; PAUSE COULD NOT BE VERIFIED ** %s" % pause_error)
        print("** WATCHER FAILED; %s ** %s is unavailable (%s)."
              " No blind fallback was started."
              % ("CLOCK PAUSED" if paused else "PAUSE COULD NOT BE VERIFIED", TOOL, e))
        return "watcher unavailable", []
    finally:
        # Letter sweeping performs its own unbounded bridge calls. Keep it a
        # separate command so this safety-critical entry point always yields.
        pass
def run(seconds=60, speed="Superfast", interval=1.5, verbose=True, force=False,
        ignored_hostile_ids=None, ignored_downed_ids=None):
    """Run time and stop on a guard. `until()` with no completion condition.

    **Past `turnclock.HARD_AT` this refuses a long pulse.** A turn that has run
    eight minutes is over; more game time is the one thing that cannot help it,
    and on 2026-09-04 a fifteen-minute turn was mostly this call, over and over,
    while the model planned. Short pulses still go through --
    `turnclock.HARD_MAX_RUN` seconds is enough to watch an order land -- and
    `--force` (or `force=True`) overrides, because a fork mid-fight must never
    be locked out of the game by a clock.

    The gate is here and not in `until()` on purpose: `move.goto` walks a pawn
    through `until()`, and a walk that dies at the eight-minute mark strands a
    drafted colonist in the open. Refusing to *start* long unattended time is
    the safe half of the rule.
    """
    if not force and seconds > turnclock.HARD_MAX_RUN:
        elapsed, over = turnclock.over_hard_limit()
        if over:
            print("[turn] %s elapsed -- NOT running %.0fs of game time. This turn"
                  " is %s past the %s budget; hand back."
                  % (turnclock.mmss(elapsed), seconds,
                     turnclock.mmss(elapsed - turnclock.BUDGET),
                     turnclock.mmss(turnclock.BUDGET)))
            print("   write state\\hands-last.md and return your report. If the"
                  " colony genuinely needs time right now: python run.py %d"
                  " (<=%ds is always allowed), or --force to override."
                  % (int(turnclock.HARD_MAX_RUN), int(turnclock.HARD_MAX_RUN)))
            return "turn budget", []
    return until(None, seconds, speed, interval, verbose, ignored_hostile_ids,
                 ignored_downed_ids)


if __name__ == "__main__":
    # -h/--help before any parsing: the positionals go straight to float(), so
    # a flag that reaches them is a traceback rather than a usage message.
    if {"-h", "--help"} & set(sys.argv[1:]):
        print((__doc__ or "").strip())
        print()
        print('usage: python run.py [seconds] [speed] [interval]'
              ' [--say "..."] [--mood <mood>] [--force] [--ignore-hostile ID]'
              ' [--allow-downed ID]')
        print('  --force  run anyway when the turn is past the budget and this'
              ' call is longer than %ds' % int(turnclock.HARD_MAX_RUN))
        print('  --ignore-hostile ID  explicitly baseline this known-contained'
              ' hostile; repeat for more than one (stable Thing ID only)')
        print('  --allow-downed ID  acknowledge this known downed colonist for'
              ' one invocation; repeat for more than one (stable Thing ID)')
        sys.exit(0)
    # --say/--mood are lifted out of argv before the positional parsing, so
    # nothing shifts without them. Narration is posted after the run returns:
    # if the run raises, nothing is narrated.
    try:
        import overlay_client as _ov
        a, _say, _mood = _ov.take_flags(sys.argv[1:])
    except Exception:
        _ov, a, _say, _mood = None, sys.argv[1:], None, None
    # Lifted out the same way, and before the positionals, so `--force` can go
    # anywhere on the line without shifting `seconds`.
    _force = "--force" in a
    a = [x for x in a if x != "--force"]
    _ignored = []
    while "--ignore-hostile" in a:
        _i = a.index("--ignore-hostile")
        if _i + 1 >= len(a):
            raise SystemExit("--ignore-hostile requires a stable Thing ID")
        _ignored.append(a[_i + 1])
        del a[_i:_i + 2]
    _allowed_downed = []
    while "--allow-downed" in a:
        _i = a.index("--allow-downed")
        if _i + 1 >= len(a):
            raise SystemExit("--allow-downed requires a stable pawn Thing ID")
        _allowed_downed.append(a[_i + 1])
        del a[_i:_i + 2]
    run(float(a[0]) if a else 60,
        a[1] if len(a) > 1 else "Superfast",
        float(a[2]) if len(a) > 2 else 1.5,
        force=_force, ignored_hostile_ids=_ignored,
        ignored_downed_ids=_allowed_downed)
    if _ov is not None:
        _ov.say_flags(_say, _mood)
