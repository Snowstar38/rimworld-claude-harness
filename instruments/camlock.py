"""Who owns the camera right now: a Hands lease, and a queue of notices back.

  python camlock.py                    # who has it, for how much longer
  python camlock.py claim "why"        # Hands takes it for 5.5 minutes
  python camlock.py notices            # anything the Lookout wants Hands to know

## The model

**The lease is taken as a side effect of moving the camera.** Nothing has to
acquire it and -- the part that matters -- nothing has to release it, because a
forgotten release is how a lock silently retires the thing it was fencing. It
expires instead.

The rules, entire:

* **Hands always wins, instantly.** A claim lands mid-glide and the Lookout
  abandons the camera where it stands. It does not finish its move and it does
  not put anything back.
* **A claim silences the camera for 330 seconds**, deliberately longer than the
  Lookout's 150s pass, so a Hands turn gets at least two clear passes.
* **Outside a lease the Lookout owns the camera** and returns it to the pinned
  base coordinate (`cam.base_cell()`), not to wherever it was left.
* **Panning home queues a notice** for the next Hands turn, so a camera moved
  off something being watched is undoable rather than merely discovered.

**Nothing here raises.** Missing, corrupt or half-written state all return
something usable: this guards one screenshot and could otherwise break a turn.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state", "camera.json")

# M's "delaying over 5 mins". 330s is 5.5, which clears two whole Lookout
# passes (150s each) rather than landing awkwardly inside the second one.
LEASE = 330
# How long a mover may claim to be in motion before we assume it died. A glide is
# 5s each way; 20 is generous and still short enough that a crashed Lookout
# cannot wedge `settle()` for a whole turn.
MOVING_TTL = 20
NOTICE_CAP = 12


def _read():
    try:
        with open(STATE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(d):
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, STATE)
        return True
    except OSError:
        return False


# ------------------------------------------------------------------- lease ---

def claim(by="hands", reason="", seconds=LEASE):
    """Take the camera for `seconds`. -> seconds now remaining.

    Idempotent and cheap: every Hands camera move calls this, and re-claiming
    just restarts the clock, which is the intended behaviour. A turn that keeps
    moving the camera keeps the Lookout out of it for as long as it is working.
    """
    d = _read()
    d["claimedAt"] = time.time()
    d["by"] = by
    d["lease"] = float(seconds)
    d["reason"] = str(reason)[:120]
    _write(d)
    return float(seconds)


def claimed(d=None):
    """-> (live, seconds_left, by, reason). All zeros/None when the camera is free."""
    d = _read() if d is None else d
    at, lease = d.get("claimedAt"), d.get("lease") or LEASE
    if not at:
        return False, 0.0, None, ""
    left = at + lease - time.time()
    if left <= 0:
        return False, 0.0, None, ""
    return True, left, d.get("by"), d.get("reason") or ""


def may_move(who="lookout"):
    """May `who` move the camera right now? -> (ok, why not).

    Hands never asks -- it claims and moves. This is the Lookout's question, and
    the honest answer is almost always yes: a lease only exists while a turn is
    actively working somewhere.
    """
    d = _read()
    live, left, by, reason = claimed(d)
    if live and by != who:
        return False, "%s claimed the camera %.0fs ago, %.0fs of the lease left%s" % (
            by, time.time() - d.get("claimedAt", time.time()), left,
            " (%s)" % reason if reason else "")
    return True, ""


# ------------------------------------------------------------------ motion ---

def start_moving(by="lookout", seconds=MOVING_TTL):
    """Announce a move in progress, and remember when it started.

    The start time is the whole trick behind the interrupt: `interrupted()` asks
    whether a claim landed *after* this move began, which needs no locking and
    cannot race into a wrong answer.
    """
    d = _read()
    d["moving"] = {"by": by, "since": time.time(), "until": time.time() + seconds}
    _write(d)
    return d["moving"]["since"]


def stop_moving():
    d = _read()
    d.pop("moving", None)
    _write(d)


def moving(d=None):
    """-> the live motion record, or None."""
    d = _read() if d is None else d
    m = d.get("moving")
    if not isinstance(m, dict) or m.get("until", 0) < time.time():
        return None
    return m


def interrupted(since):
    """Did a claim land after this move started? -> (yes, by).

    Called between the small steps of a glide. M: a Hands camera command
    "would immediately interrupt any camera movement luna might be doing" -- so
    the Lookout checks every step and abandons the camera where it stands.
    """
    d = _read()
    at = d.get("claimedAt")
    if at and at >= since and d.get("by") != "lookout":
        return True, d.get("by")
    return False, None


def settle(timeout=3.0, poll=0.15):
    """Wait for any in-progress camera move to finish. -> True if the camera is still.

    For anything about to convert a cell to a screen pixel: `cell2px.to_px()`
    reads viewRect and the real click happens after, so the camera must not be
    mid-glide across that gap.
    """
    deadline = time.time() + timeout
    while moving():
        if time.time() >= deadline:
            return False
        time.sleep(poll)
    return True


# ----------------------------------------------------------------- notices ---

def notify(text):
    """Queue something for the next Hands turn to read.

    M: when the Lookout pans home it should "send a little message to
    hands (to queue it to be seen after their next action) that says the camera
    was centered on base, so they can move it back if needed." A queue rather
    than a print, because the Lookout is detached and Hands is not listening.
    """
    d = _read()
    q = [n for n in (d.get("notices") or []) if isinstance(n, dict)]
    text = str(text)[:300]
    if q and q[-1].get("text") == text:
        q[-1]["at"] = time.time()          # same news twice is once, freshly dated
        d["notices"] = q
        _write(d)
        return
    q.append({"at": time.time(), "text": text})
    d["notices"] = q[-NOTICE_CAP:]
    _write(d)


def take_notices():
    """-> [lines], and clears the queue. Delivered exactly once, like the Lookout report."""
    d = _read()
    q = [n for n in (d.get("notices") or []) if isinstance(n, dict)]
    if not q:
        return []
    d["notices"] = []
    _write(d)
    out = []
    for n in q:
        age = time.time() - n.get("at", 0)
        out.append("[camera] %s (%.0fs ago)" % (n.get("text", ""), age))
    return out


def peek_notices():
    """The same lines, without clearing. For a human at a prompt."""
    return ["[camera] %s (%.0fs ago)" % (n.get("text", ""), time.time() - n.get("at", 0))
            for n in (_read().get("notices") or []) if isinstance(n, dict)]


# --------------------------------------------------------------------- cli ---

def main():
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "status"

    if cmd == "claim":
        left = claim("hands", " ".join(argv[1:]))
        print("camera: claimed by hands for %.0fs -- the Lookout will not move it "
              "and will abandon any move in progress" % left)
        return 0

    if cmd == "notices":
        lines = take_notices()
        print("\n".join(lines) if lines else "[camera] nothing queued")
        return 0

    if cmd == "free":                       # for testing; a lease normally just expires
        d = _read()
        for k in ("claimedAt", "by", "lease", "reason"):
            d.pop(k, None)
        _write(d)
        print("camera: lease cleared")
        return 0

    live, left, by, reason = claimed()
    if live:
        print("camera: held by %s for another %.0fs%s"
              % (by, left, " (%s)" % reason if reason else ""))
    else:
        print("camera: free -- the Lookout may pan it home")
    m = moving()
    if m:
        print("        %s is moving it right now (%.1fs in)" % (m["by"], time.time() - m["since"]))
    for line in peek_notices():
        print("        " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
