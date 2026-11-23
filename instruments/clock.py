"""Is the game clock actually running? The question every VERIFIED line rests on.

  import clock
  st = clock.state()                      # one cheap read
  if clock.is_paused(st): ...             # -> the ORDER QUEUED wording
  print("\n".join(clock.notes(st)))

  python clock.py                         # print the verdict, for a human
  python clock.py --self-test             # every state, no game

## Why this file exists

On the 2026-09-07 stream a wolf survived **four separate orders that each
printed VERIFIED**, and `combat.py flee` printed `FLEE VERIFIED` while the pawn
stood still through a manhunter attack. Nothing was broken in the ordering: the
jobs really were on the pawns. The clock was stopped, so no job ever ran.

A job read-back proves **the job was queued**. On a paused game that is all it
can ever prove -- `Pawn.CurJob` is set the instant the order lands, ticks or no
ticks. So a read-back is necessary and never sufficient, and any line that says
VERIFIED without knowing the clock is running is lying by a word.

## The cheapest honest read

`home/status` returns `time.paused` (which is `TickManager.Paused`, and per
`status.py` "is always right"), plus `forcePaused`, `timeSpeed` and
`ticksGame` -- one call, everything needed. That is the primary read.

`sample()` is the fallback and the proof-of-last-resort: two `ticksGame` reads
across a short real-time gap, which is the only test that cannot be fooled by a
missing or wrong flag. It costs two calls and ~0.6s, so it runs only when the
flag is unreadable, or when a caller asks for it outright.

## Three answers, and only one of them is good news

``running``  the clock is moving; a read-back means what it says.
``paused``   the order is QUEUED. It will run when time runs, and not before.
``unknown``  nothing was read. **Not the same as running.** No caller may print
             VERIFIED on an unknown clock; it prints ACCEPTED and says so.

This file makes no bridge call that changes anything, and never pauses,
unpauses or changes speed. It reads.
"""
import sys
import time

import rim

TOOL = "home/status"
TICK_TOOL = "rimworld/get_game_info"

RUNNING = "running"
PAUSED = "paused"
UNKNOWN = "unknown"

# The remedy line. `play.py start` is the supervised way to make time pass; a
# combat session passes its own bounded `advance` instead (see EXTRA_COMBAT).
REMEDY = "python play.py start"
SAMPLE_GAP = 0.6

QUEUED_HEADLINE = ("ORDER QUEUED -- CLOCK IS STOPPED (paused by %s); nothing "
                   "happens until the clock runs: %s")
QUEUED_PROOF = ("   a job read-back on a paused game proves the job was QUEUED "
                "on the pawn -- never that it ran.")
UNKNOWN_HEADLINE = ("!! CLOCK UNKNOWN -- %s did not say whether time is running "
                    "(%s). This order is QUEUED at best; nothing here proves it "
                    "ran. Read the clock: python status.py")


def _read(reader=None):
    """The `time` block of one `home/status`, or a RuntimeError naming why not."""
    call = reader or (lambda: rim.game(TOOL, {"colonists": False,
                                              "threats": False}))
    reply = call()
    if not isinstance(reply, dict):
        raise RuntimeError("%s did not answer (%s)"
                           % (TOOL, str(reply)[:120] or "empty reply"))
    block = reply.get("time")
    if not isinstance(block, dict):
        raise RuntimeError("%s answered with no `time` block" % TOOL)
    return block


def _why_paused(block):
    """A short reason the clock is stopped, from what is already in hand plus disk.

    Never a bridge call: `status.py` established that the source of a pause is
    a disk question (is a supervisor alive?), not a game question.
    """
    if block.get("forcePaused"):
        return "a modal window is holding the clock"
    try:
        import play
        import play_service
        row = play_service.read_json(play_service.SERVICE_STATE)
        if play.service_alive(row):
            return ("supervised play IS running, service pid %s -- something "
                    "paused it" % row.get("pid"))
    except Exception:
        return "source unknown -- the supervised-play state is unreadable"
    return "no supervised play is running"


def state(reader=None, hint=None, sampler=None):
    """One cheap read. -> {"state", "tick", "speed", "forcePaused", "why", "error"}.

    Never raises: a clock this file could not read is `unknown`, which is a
    state callers must handle, not an exception that eats the order's report.

    `hint` is the caller's own knowledge of why the clock is stopped -- it is
    used verbatim in place of the derived reason, because a caller that just
    paused the game itself knows better than any inference from disk.
    """
    try:
        block = _read(reader)
    except Exception as e:
        # `home/status` is the companion's. On a bridge where the companion is
        # not loaded it is simply not there, and falling straight to `unknown`
        # would put a CLOCK UNKNOWN line under every order on a game that is
        # perfectly readable. Two `ticksGame` reads are the older, dumber test
        # and they answer without the companion. Only ever paid on this path.
        why = "%s: %s" % (type(e).__name__, str(e)[:140])
        try:
            measured, delta = (sampler or sample)()
        except Exception:
            measured, delta = UNKNOWN, None
        row = {"state": measured, "tick": None, "speed": None,
               "forcePaused": None, "why": None, "delta": delta,
               "error": None if measured != UNKNOWN
                        else "%s, and two `ticksGame` reads did not answer "
                             "either" % why}
        if measured == PAUSED:
            row["why"] = (hint or "measured by two tick reads; %s is "
                                  "unavailable, so the reason is unread" % TOOL)
        return row
    paused = block.get("paused")
    row = {"state": UNKNOWN, "tick": block.get("ticksGame"),
           "speed": block.get("timeSpeed"),
           "forcePaused": bool(block.get("forcePaused")),
           "why": None, "error": None}
    if paused is None:
        # No flag. Fall back to the one test that cannot be wrong: two ticks.
        try:
            measured, delta = (sampler or sample)()
        except Exception:
            measured, delta = UNKNOWN, None
        row["state"] = measured
        row["delta"] = delta
        if measured == PAUSED:
            row["why"] = hint or _why_paused(block)
        elif measured == UNKNOWN:
            row["error"] = ("%s reported no `paused` flag and two `ticksGame` "
                            "reads did not answer either" % TOOL)
        return row
    if paused:
        row["state"] = PAUSED
        row["why"] = hint or _why_paused(block)
    else:
        row["state"] = RUNNING
    return row


def sample(gap=SAMPLE_GAP, reader=None, sleeper=time.sleep):
    """Two `ticksGame` reads across a real gap. -> (state, ticks elapsed).

    The proof, not the flag. Costs two calls and `gap` seconds of wall clock,
    which is why nothing calls it unless the flag is missing or a caller wants
    a measurement rather than an assertion.
    """
    call = reader or (lambda: rim.game(TICK_TOOL, {}, strict=False))

    def tick():
        # Never raises. `sample` is the fallback path of a report about an
        # order that already landed; an exception here would eat that report.
        try:
            r = call()
        except Exception:
            return None
        if not isinstance(r, dict):
            return None
        got = r.get("ticksGame")
        return got if isinstance(got, int) and not isinstance(got, bool) else None

    first = tick()
    if first is None:
        return UNKNOWN, None
    sleeper(gap)
    second = tick()
    if second is None:
        return UNKNOWN, None
    delta = second - first
    return (RUNNING if delta > 0 else PAUSED), delta


def is_paused(st):
    return (st or {}).get("state") == PAUSED


def is_running(st):
    return (st or {}).get("state") == RUNNING


def word(st, running="VERIFIED", paused="QUEUED", unknown="ACCEPTED"):
    """The headline word an order may honestly use for this clock state.

    `VERIFIED` is reachable from exactly one state. That is the whole point.
    """
    got = (st or {}).get("state")
    if got == RUNNING:
        return running
    if got == PAUSED:
        return paused
    return unknown


def notes(st, remedy=REMEDY, extra=()):
    """The lines to print under an order's headline. Empty when time is running."""
    if is_running(st):
        return []
    if is_paused(st):
        lines = [QUEUED_HEADLINE % (st.get("why") or "reason unknown", remedy),
                 QUEUED_PROOF]
        lines.extend(extra)
        return lines
    return [UNKNOWN_HEADLINE % (TOOL, (st or {}).get("error") or "no reason given")]


def line(st):
    """One line describing the clock, for a status board or a log."""
    if is_running(st):
        return "CLOCK running %s (tick %s)" % (st.get("speed") or "?",
                                               st.get("tick"))
    if is_paused(st):
        return "CLOCK STOPPED (%s) at tick %s" % (st.get("why") or "reason unknown",
                                                  st.get("tick"))
    return "CLOCK UNKNOWN -- %s" % ((st or {}).get("error") or "no reason given")


# --------------------------------------------------------------- self-test ---

BANNER = ("=" * 72 + "\n"
          "  SELF-TEST -- no game was read. Every number below is invented.\n"
          + "=" * 72)


def _self_test():
    print(BANNER)
    cases = (
        ("running", lambda: {"time": {"paused": False, "timeSpeed": "Normal",
                                      "ticksGame": 5000}}),
        ("paused", lambda: {"time": {"paused": True, "forcePaused": False,
                                     "ticksGame": 5000}}),
        ("forced", lambda: {"time": {"paused": True, "forcePaused": True,
                                     "ticksGame": 5000}}),
        ("no answer", lambda: "no game is connected"),
    )
    # The "no answer" case falls back to two tick reads, so the self-test hands
    # it a sampler; nothing in here may touch a game.
    blind = lambda: (UNKNOWN, None)
    for name, reader in cases:
        st = state(reader=reader, hint=None, sampler=blind)
        print("\n--- %s ---" % name)
        print(line(st))
        print("word: %s" % word(st))
        for row in notes(st):
            print(row)
    print("\n--- flag missing, measured by two tick reads ---")
    st = state(reader=lambda: {"time": {"ticksGame": 5000}},
               sampler=lambda: (PAUSED, 0), hint="combat.py paused it")
    print(line(st))
    for row in notes(st):
        print(row)
    print("\n" + BANNER)
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if "--self-test" in argv:
        return _self_test()
    rim.init()
    st = state()
    print(line(st))
    if not is_running(st):
        print("\n".join(notes(st)))
    return 0 if is_running(st) else 1


if __name__ == "__main__":
    sys.exit(main())
