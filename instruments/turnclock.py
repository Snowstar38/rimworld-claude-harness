"""The turn clock: how long the fork holding the game has been holding it.

Import it from anywhere; it is deliberately inert.

    import turnclock
    line = turnclock.turn_budget_line()   # None, or one line to print
    secs = turnclock.turn_elapsed()       # seconds since hands-start, or None

**No bridge, no overlay, no network, nothing at import time.** `run.py` and
`watch.py` both talk to the game and both print this line beside their step
output, so a clock that could raise, block or need a server would be a new way
for a turn to die. It reads one small JSON file, catches everything, and
returns None when it doesn't know.

The clock starts at `stream.py hands-start`, which writes `handsStartedAt`
(epoch seconds) into `state\\stream.json`, and stops only when the native
SubagentStop hook writes `handsEndedAt`. Between those two the fork is in the game and the
budget applies; outside them there is no active clock. A turn that predates
`MAX_AGE` is ignored by the budget display so an orphaned token does not scold
a later session for eleven hours. The lifecycle check remains unresolved until
native stop or explicit recovery; age never proves that an agent exited.

Why the harness enforces this at all: on 2026-09-04 a turn ran fifteen minutes
against a six-minute budget while a wolf killed two colonists, and the model
holding it had no way to feel the time pass. It can't be asked to watch a
clock it cannot see, so the instruments it runs anyway print one.
"""
import json
import os
import time

STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "state", "stream.json")

# The fork's own liveness token. Every tool boundary a native Hands crosses
# rewrites it (`runtime_hook.touch_heartbeat`), so it is the one cheap piece of
# positive evidence that the agent holding the turn is still there. mtime is
# deliberately not used for anything: the day-60 bug was `hands-check` reading
# the mtime of a report file a live fork had merely written early.
HEARTBEAT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "state", "hands-heartbeat.json")

# The fork's report. Whether it was written before or after the fork actually
# stopped is the difference between briefing off a draft and briefing off the
# truth -- see `report_stage`.
REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "state", "hands-last.md")

# A heartbeat younger than this is proof the fork is alive. Chosen above the
# longest single instrument call seen on stream (a ~2 minute build.py) so a
# slow tool is never mistaken for a dead agent, and well under the 6-minute
# budget so a wedged fork is still visible inside one turn.
LIVE_WITHIN = 150.0

# The budget from hands.md: turns are time-boxed to 4-6 minutes.
WARN_AT = 300.0     # 5:00 -- start saying so
BUDGET = 360.0      # 6:00 -- over
REMIND_EVERY = 45.0
# Past this, run.py stops handing out long pulses of game time. Chosen well
# clear of the budget so a fork finishing a fight is never cut off, and low
# enough that a fifteen-minute turn cannot happen quietly.
HARD_AT = 480.0     # 8:00
# The longest run a fork may start once past HARD_AT, in seconds. Enough to
# finish an order and see it land; not enough to keep playing.
HARD_MAX_RUN = 20.0
# A start older than this is debris, not a turn in progress.
MAX_AGE = 4 * 3600


def _state():
    try:
        with open(STATE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def hands_started_at(d=None):
    """Epoch seconds when the current turn began, or None if no turn is open.

    None covers all four ways there is no turn: never started, already handed
    back, unreadable state, or stale enough that believing it would be worse
    than not knowing.
    """
    try:
        d = _state() if d is None else d
        started = float(d.get("handsStartedAt") or 0)
        if not started:
            return None
        ended = float(d.get("handsEndedAt") or 0)
        if ended >= started:
            return None            # handback already ran; the Core has it
        if time.time() - started > MAX_AGE:
            return None            # debris from a session that never closed
        return started
    except Exception:
        return None


def turn_elapsed():
    """Seconds since hands-start, or None when no turn is open."""
    started = hands_started_at()
    if started is None:
        return None
    return max(0.0, time.time() - started)


def mmss(seconds):
    """5:10. Seconds are what the reader is counting, so no hours."""
    try:
        s = int(round(float(seconds)))
    except Exception:
        return "?:??"
    return "%d:%02d" % (s // 60, s % 60)


def turn_budget_line(elapsed=None):
    """One line to print beside a step, or None when there is nothing to say.

    Silence under five minutes is the point: a nag on every step of every turn
    is a nag nobody reads by turn three.
    """
    try:
        e = turn_elapsed() if elapsed is None else float(elapsed)
        if e is None or e < WARN_AT:
            return None
        if e < BUDGET:
            return "[turn] %s of %s -- wrap up" % (mmss(e), mmss(BUDGET))
        return ("[turn] %s -- PAST THE %d-MINUTE BUDGET: write hands-last.md"
                " and hand back NOW" % (mmss(e), int(BUDGET // 60)))
    except Exception:
        return None


def next_budget_reminder(elapsed, previous=None):
    """Return (line, new state) for the throttled native-hook reminder.

    The caller persists ``new state`` under its session/agent/turn identity.
    Regular command output continues to use ``turn_budget_line`` unthrottled.
    """
    previous = dict(previous or {})
    try:
        e = float(elapsed)
    except Exception:
        return None, previous
    if e < WARN_AT:
        return None, previous
    if e < BUDGET:
        if previous.get("warnedAtFive"):
            return None, previous
        previous["warnedAtFive"] = True
        return turn_budget_line(e), previous
    last = previous.get("lastOverdueElapsed")
    if last is not None and e - float(last) < REMIND_EVERY:
        return None, previous
    previous["lastOverdueElapsed"] = e
    line = ("[turn] %s elapsed -- %s OVER the %d-minute budget: finish the "
            "final report and hand back" %
            (mmss(e), mmss(e - BUDGET), int(BUDGET // 60)))
    return line, previous


def print_budget_line(prefix="   "):
    """Print the line if there is one. Never raises; returns whether it did."""
    try:
        line = turn_budget_line()
        if line:
            print(prefix + line)
            return True
    except Exception:
        pass
    return False


def over_hard_limit():
    """(elapsed, True) once the turn is past HARD_AT, else (elapsed, False)."""
    e = turn_elapsed()
    return e, (e is not None and e >= HARD_AT)


def handed_back():
    """Return native lifecycle completion, independent of report-file writes."""
    d = _state()
    try:
        started = float(d.get("handsStartedAt") or 0)
        if not started:
            return None, None
        ended = float(d.get("handsEndedAt") or 0)
        if ended >= started:
            return True, max(0.0, time.time() - ended)
        return False, max(0.0, time.time() - started)
    except Exception:
        return None, None



def heartbeat():
    """The last tool boundary a native Hands crossed, as a dict (never raises).

    Keys: ``agent``, ``session``, ``turn``, ``at``. Empty when no fork has run
    a tool since the feature was installed -- which is *unknown*, not *dead*.
    """
    try:
        with open(HEARTBEAT, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def hands_alive(d=None):
    """Tri-state liveness of the fork holding the open turn: True/False/None.

    True  -- a heartbeat for *this* turn, younger than LIVE_WITHIN. Positive
             proof; nothing may force the turn closed on top of it.
    False -- a heartbeat for this turn, but stale. The fork stopped without its
             SubagentStop hook landing, or it is wedged.
    None  -- no usable heartbeat. Unknown, and unknown is not dead.

    Returns ``(state, info)`` where info carries ``agent`` and ``age``.
    """
    try:
        d = _state() if d is None else d
        started = float(d.get("handsStartedAt") or 0)
        beat = heartbeat()
        at = float(beat.get("at") or 0)
        if not started or not at or at < started:
            return None, {}
        age = max(0.0, time.time() - at)
        info = {"agent": beat.get("agent"), "age": age, "at": at,
                "turn": beat.get("turn")}
        return (age <= LIVE_WITHIN), info
    except Exception:
        return None, {}


def liveness_line(d=None):
    """One line about the fork's own token, or None when there is nothing to say."""
    alive, info = hands_alive(d)
    if alive is None:
        return None
    if alive:
        return "[hands] agent %s touched a tool %s ago -- it is alive" % (
            info.get("agent"), mmss(info.get("age") or 0))
    return ("[hands] agent %s has touched nothing for %s -- no SubagentStop "
            "landed; `python stream.py hands-close` closes the turn without "
            "resetting anything" % (info.get("agent"), mmss(info.get("age") or 0)))


# What a turn was closed BY, when that closure is a Core decision rather than
# the fork's own stop hook. Against these, `handsEndedAt` is the moment somebody
# gave up on the fork, not the moment the fork stopped, so it cannot date the
# report -- the last heartbeat can, and says so.
FORCED_BY = ("core-forced", "session-end")


def report_stage(d=None):
    """Was `hands-last.md` written before or after the fork actually stopped?

    ``("FINAL"|"INTERMEDIATE"|"STALE"|"OPEN"|"UNDATED", seconds, mtime)`` or
    ``(None, None, None)`` when there is no report. A fork writes its report
    more than once and an intermediate write can claim the opposite of the
    final one; on 2026-09-05 a Core briefed the next turn off a draft that said
    a buck had left the map while the final said it was shot for 63 venison.
    """
    try:
        d = _state() if d is None else d
        mtime = os.path.getmtime(REPORT)
    except Exception:
        return None, None, None
    try:
        started = float(d.get("handsStartedAt") or 0)
        ended = float(d.get("handsEndedAt") or 0)
        if started and mtime < started:
            return "STALE", started - mtime, mtime
        if not started or ended < started:
            return "OPEN", max(0.0, time.time() - mtime), mtime
        if d.get("handsEndedBy") in FORCED_BY:
            # Fall back to the fork's last tool boundary, which is a real
            # moment it was still alive, and refuse to guess without one.
            at = float(heartbeat().get("at") or 0)
            if at >= started:
                return ("FINAL" if mtime >= at else "INTERMEDIATE",
                        abs(mtime - at), mtime)
            return "UNDATED", max(0.0, ended - mtime), mtime
        if mtime >= ended:
            return "FINAL", mtime - ended, mtime
        return "INTERMEDIATE", ended - mtime, mtime
    except Exception:
        return None, None, None


def report_line(d=None):
    """The one line `hands-check`/`handback` print about the report file."""
    d = _state() if d is None else d
    stage, secs, _mtime = report_stage(d)
    forced = d.get("handsEndedBy") in FORCED_BY
    against = (" (measured against the fork's last tool boundary; the turn was "
               "closed by hand, so there is no stop to measure from)"
               if forced else "")
    if stage is None:
        return "hands-last.md has not been written"
    if stage == "FINAL":
        return "hands-last.md is FINAL (written %s after the fork stopped)%s" % (
            _secs(secs), against)
    if stage == "INTERMEDIATE":
        return ("hands-last.md is INTERMEDIATE (written %s before the fork "
                "stopped)%s -- it may claim things the final report reverses; "
                "ask the fork's own last message, not this file"
                % (_secs(secs), against))
    if stage == "STALE":
        return ("hands-last.md is STALE (written %s before this turn even "
                "started) -- it belongs to an earlier turn" % _secs(secs))
    if stage == "UNDATED":
        return ("hands-last.md cannot be dated: the turn was closed by hand %s "
                "after the report was written and the fork left no heartbeat, "
                "so FINAL and INTERMEDIATE are indistinguishable -- treat it as "
                "a draft" % _secs(secs))
    return "hands-last.md was written %s ago; the turn is still open" % _secs(secs)


def _secs(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "?"
    return "%d s" % int(round(value)) if value < 90 else mmss(value)


if __name__ == "__main__":
    e = turn_elapsed()
    print("turn   : %s" % ("no turn open" if e is None else mmss(e) + " elapsed"))
    print(turn_budget_line() or "[turn] inside budget")
