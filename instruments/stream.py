"""The Core's end of the stream. Turn counter, handoffs, and M's channel.

  python stream.py goals --long "..." --short "..."     # fill the bar before turn 1
  python stream.py go [--keep-turns]                      # start session services, turn counter -> 0
  python stream.py hands-start --goal "Get the stove bill cooking insect meat"
  python stream.py hands-claim                           # first native Hands command
  python stream.py hands-release                         # explicit final handoff
  python stream.py hands-close --reason "TaskStop, no stop hook"  # unstick a dead fork
  python stream.py handback --summary "..." --mood welp --short "..." [--long "..."]
  python stream.py human-check
  python stream.py hands-check                         # has the fork already handed back?
  python stream.py bind-check [--repair]                # is the runtime binding live?
  python stream.py mode live                           # what kind of session this is
  python stream.py status
  python stream.py reset                               # back to pre-turn-1

The Core never touches the game; it does own the narration around the turns, so
this is the one stream tool it runs. Hands narrates with `say.py` or the
`--say/--mood` flags on the instruments.

`hands-close` is the whole recovery for a turn whose fork is gone without its
SubagentStop hook having landed -- a fork killed by TaskStop, or one whose
`hands-claim` raised before the claim was recorded. It stamps `handsEndedAt`
and touches **nothing** else: not the counter, the pin, the goals bar, the
feed, the rota service or the game clock. `reset` was the only cure for this
before 2026-09-07 and cost all six. It refuses only on positive evidence that
the fork is alive -- a heartbeat (`state\\hands-heartbeat.json`, rewritten at
every tool boundary a fork crosses) younger than `turnclock.LIVE_WITHIN`.

**The harness owns the turn counter -- never ask the model to track it.** It
lives in `state\\stream.json` (gitignored, like the rest of state\\), is
incremented by `hands-start`, zeroed by `reset`, and read by everything else.

`human-check` is the legacy/manual overlay inbox. Native lifecycle hooks route
durable game and reviewer events directly; this command never polls the rota.

**Every subcommand: if the server is down, one warning line and exit 0.** The
Core reads a non-zero exit as "the turn broke", so a post that does not land is
a warning line naming the endpoint and the reason (see `missed`), never a
failing exit code. Every post's result is checked, `/goals` included. This
process never raises, and never blocks longer than ~3 seconds of talking to the
server -- plus, at most once for the whole process,
`overlay_client.RETRY_BUDGET` of retrying.

A summary over `SUMMARY_CAP` still posts in full, immediately, and then
`luna.py` is spawned *detached* to ask for a version that fits the card and swap
the pin when it comes back. Nothing on this path waits for that: see
`hand_to_luna` and luna.py's docstring.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import overlay_client as ov
import turnclock

# M types emoji. On Windows stdout is cp1252 by default and a single
# heart crashes human-check with UnicodeEncodeError -- which silently drops her
# message, because the crash happens before it is marked delivered. Force UTF-8
# and replace anything even that cannot render, so this can never eat her words.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

STATE = Path(__file__).resolve().parent / "state" / "stream.json"

# Shorter than overlay_client's 2s: hands-start makes two calls and the whole
# process has to stay inside ~3s even when nothing is listening.
FAST = 1.2

# What the summary card in the bottom bar actually holds. Aim for 220-300; this
# is the cap. Over it we still post -- narration never blocks a turn -- but we
# say so, because the overflow is cut off on screen rather than shrunk.
#
# It is also the one place the number lives: `luna.py` imports it from here.
SUMMARY_CAP = 320

# The three session modes, one small file each in `modes\`. The launcher asks
# M which one this is in the same popup that asks whether she is ready
# (`errata.md` step 4) and records the answer here, so that anything running
# later -- a Hands fork's instruments, say.py, a future check -- can find out
# what kind of session it is in without asking her twice.
MODES = ("workshop", "practice", "live")

# A mode from yesterday is not a fact about today. Twelve hours after it was
# set we stop believing it, because the staleness is dangerous in exactly one
# direction: a leftover "workshop" during a real stream would license the
# debugging that `modes\live.md` exists to forbid. Unset reads as unset.
MODE_MAX_AGE = 12 * 3600

# What `reset` pins in place of the last run's headline. The server 400s an
# empty summary, so the card is overwritten, not emptied; this is the overlay's
# own no-summary wording.
FRESH_SUMMARY = "Nothing summarised yet"


def load():
    try:
        d = json.loads(STATE.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {}


def save(d):
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
        tmp.replace(STATE)
    except Exception as e:
        # The turn number is worth a warning; it is not worth an exception.
        warn("could not write %s (%s)" % (STATE.name, e))


def update(change):
    """Read-modify-write `stream.json` under the same lock the hooks take.

    `save()` is a plain overwrite, which is fine for the Core's own fields; a
    lifecycle stamp is not, because `runtime_hook.close_turn` can be writing
    the very same keys from a fork's stop hook at the same moment.
    """
    try:
        import game_session
        with game_session.locked("stream-state"):
            d = load()
            result = change(d)
            save(d)
            return result
    except Exception as e:
        warn("could not take the stream-state lock (%s: %s) -- writing unlocked" % (
            type(e).__name__, e))
        d = load()
        result = change(d)
        save(d)
        return result


def warn(msg):
    print("[stream] " + msg)


# The bottom bar's two goal boxes are `-webkit-line-clamp: 6` at 27px serif in a
# ~200px column (overlay.html, `.goal p`), so a long goal renders as a fragment
# ending in an ellipsis. Nothing in the loop reports that -- the post succeeds,
# /state echoes the whole string back, and the truncation exists only on the
# screen. Measured 2026-09-09 off M's OBS captures in `~\Videos`, by
# matching each frame's mtime to the goals event live at that instant:
#
#     82 chars  "Five alive at spring thaw -- winter is meat, ..."   rendered whole
#     84 chars  "Hold to spring -- berries, hydroponics, and a ..."  cut: "behind its ow..."
#     87 chars  "Close the south side -- Cassandra knows where ..."  cut: "is six..."
#    106 chars  "Five alive at spring thaw -- the field is next..."  cut: "cooking and..."
#
# so the box holds ~82. Wrapping is proportional, not per-character, so treat
# this as a bracket rather than a hard boundary. Over the whole log to that
# date: 62% of distinct long goals and 38% of short ones were over it.
GOAL_CHARS = 82


def goal_warning(**goals):
    """Name any goal that will render truncated. Returns a line, or None."""
    over = [(k, v) for k, v in sorted(goals.items()) if v and len(v) > GOAL_CHARS]
    if not over:
        return None
    out = []
    for k, v in over:
        out.append("%s goal is %d chars and the bar holds about %d -- viewers "
                   "will see %r and then an ellipsis"
                   % (k, len(v), GOAL_CHARS, v[:GOAL_CHARS].rstrip()))
    return "\n".join("[stream] " + line for line in out)


def down(reason):
    """One line, once, and we are done. Callers return 0 straight after."""
    warn("overlay is not answering at %s -- nothing narrated (%s)"
         % (ov.base_url(), reason))


def missed(path, reason):
    """A single post that did not land, named. Used where we carry on anyway.

    `down()` is for the first call of a subcommand, where a failure means the
    whole thing is off. This is for the later ones: the server answered a moment
    ago, so it is up, and this one post is the thing that is missing. Naming the
    endpoint matters -- "the goals bar is stale" and "the summary card is stale"
    are different problems on screen and only the path tells them apart.
    """
    warn("POST %s did not land at %s (%s) -- that panel is still showing "
         "whatever was there before" % (path, ov.base_url(), reason))


def current_mode():
    """The session mode as a plain string, or None if unset or stale.

    Importable (`from stream import current_mode`) and never raises, for the
    same reason nothing else on this path does: a tool that has to know how
    loud it is allowed to be must not fail when the answer is unavailable.
    None means "nobody said" -- treat it as the careful case, not as workshop.
    """
    try:
        d = load()
        name = d.get("mode")
        if name not in MODES:
            return None
        ts = float(d.get("mode_ts") or 0)
        if ts and time.time() - ts > MODE_MAX_AGE:
            return None
        return name
    except Exception:
        return None


def cmd_mode(a):
    """Read or set the session mode. Purely local -- the overlay never sees it.

    Set by the launcher straight from M's answer, then the session reads
    `modes\\<name>.md`. This subcommand only remembers the answer; the file is
    what actually changes how the session behaves.
    """
    if not a.name:
        print(current_mode() or "none")
        return 0
    name = a.name.strip().lower()
    if name not in MODES:
        print("stream.py: %r is not a mode. The three are: %s"
              % (a.name, " ".join(MODES)), file=sys.stderr)
        return 1
    d = load()
    d["mode"] = name
    d["mode_ts"] = time.time()
    save(d)
    print("mode: %s  (now read modes\\%s.md)" % (name, name))
    return 0


def cmd_goals(a):
    """Set either goal zone without a turn boundary.

    `hands-start` can only set the short one and `handback` needs a summary, so
    before turn 1 there was no way to fill the bar -- which is why the standing
    long-term goal sat in CHRONICLE.md and "No long-term goal yet" sat on
    screen. Run it once at session start.
    """
    goals = {k: v for k, v in (("short", a.short), ("long", a.long)) if v}
    if not goals:
        warn("nothing to set -- pass --long and/or --short")
        return 0
    _over = goal_warning(**goals)
    if _over:
        print(_over)
    ok, why = ov.post("/goals", timeout=FAST, **goals)
    if not ok:
        # Named rather than the generic `down()`: this subcommand is one post,
        # and if it did not land the bar is empty (or worse, stale) on stream.
        # Still exit 0 -- see the module docstring; this is not a broken turn.
        missed("/goals", why)
        return 0
    for k in ("long", "short"):
        if k in goals:
            print("%-5s : %s" % (k, goals[k]))
    return 0


def cmd_hands_start(a):
    """Bump the counter, start the turn clock, tell the overlay a fork has it.

    `handsStartedAt` is the only clock the harness has: `turnclock` reads it,
    `run.py` and `watch.py` print the remaining budget beside every step from
    it, and `hands-check` uses it to tell a fork that is still playing from one
    that finished. `handsEndedAt` is dropped here rather than at handback, so
    that a turn is open from this instant until `handback` closes it.
    """
    d = load()
    started = float(d.get("handsStartedAt") or 0)
    ended = float(d.get("handsEndedAt") or 0)
    if started and ended < started:
        print("HANDS START REFUSED -- turn %s still has a native Hands running; "
              "wait for its stop or use TaskStop" % int(d.get("turn") or 0))
        line = turnclock.liveness_line(d)
        if line:
            print(line)
        else:
            print("   no heartbeat for that turn. If the fork is gone, "
                  "`python stream.py hands-close` closes it without touching "
                  "the counter, the pin, the goals bar, the feed or the rota.")
        return 1
    turn = int(d.get("turn") or 0) + 1
    d["turn"] = turn
    d["handsStartedAt"] = time.time()
    d.pop("handsEndedAt", None)
    d.pop("handsEndedBy", None)
    d.pop("handsAgent", None)
    save(d)
    print("turn %d" % turn)
    try:
        import combat
        inherited = combat.inherited_warning(current={
            "turn": turn, "handsStartedAt": d["handsStartedAt"]})
        if inherited:
            print(inherited)
    except Exception as ex:
        warn("could not check combat-ledger ownership (%s)" % ex)
    status = {"phase": "hands", "turn": turn}
    if a.goal:
        status["label"] = a.goal
    ok, why = ov.post("/status", timeout=FAST, **status)
    if not ok:
        down(why)
        return 0
    if a.goal:
        _over = goal_warning(short=a.goal)
        if _over:
            print(_over)
        # The result of this one used to go straight in the bin. /status having
        # just succeeded means the server is up, so a failure here is a real
        # miss -- the turn's goal never reached the bar -- and it says so.
        ok, why = ov.post("/goals", timeout=FAST, short=a.goal)
        if not ok:
            missed("/goals", why)
    return 0


def reset_turn_counter():
    """Zero the turn counter for a new session. Returns True if it did.

    M asked for this on 2026-09-07: after three dead turns at the door
    she had to say "reset" out loud, and `reset` is the sledgehammer -- it also
    zeroes the pinned summary, the goals bar, the feed and the rota service.
    A new session only needs the counter, so this closes any stale open turn
    through the `hands-close` path and then zeroes the number. Goals, mood,
    feed, pin and rota survive; `stream.py reset` is still there when the whole
    board should go.
    """
    d = load()
    started = float(d.get("handsStartedAt") or 0)
    ended = float(d.get("handsEndedAt") or 0)
    if started and ended < started:
        closing = argparse.Namespace(reason="new session: stream.py go",
                                     force=False)
        if cmd_hands_close(closing) != 0:
            # `hands-close` refuses only against a live heartbeat, and a fork
            # that is still playing means this is not a new session at all.
            print("TURN COUNTER NOT RESET -- a fork still holds turn %d. Run "
                  "`python stream.py go` again once it has stopped, or pass "
                  "--keep-turns." % int(d.get("turn") or 0))
            return False

    def zero(state):
        state["turn"] = 0
        for key in ("handsStartedAt", "handsEndedAt", "handsEndedBy",
                    "handsAgent", "handsClaimedAt", "handsClosedReason",
                    "handsClosedUnclaimed", "handsClaimError"):
            state.pop(key, None)
        return True

    update(zero)
    push_phase_core(0)
    print("turn counter reset to 0 for this session -- goals, mood, feed and "
          "the pinned summary are untouched (`--keep-turns` opts out; "
          "`stream.py reset` clears the whole board)")
    return True


def cmd_go(a):
    """Start session-level services once, independent of Hands turns.

    Also the session boundary for the turn counter: `go` runs once, at the
    start, so it is the honest place to put the numbering back to 0.
    """
    warning = binding_warning()
    if warning:
        print(warning)
    if not getattr(a, "keep_turns", False):
        reset_turn_counter()
    try:
        import rota_service
        ok, why = rota_service.ensure_started()
        if not ok:
            print("STREAM GO REFUSED -- automatic rota service: %s" % why)
            return 1
        print("STREAM GO -- automatic rota service %s; durable play starts "
              "separately with `python play.py start`." % why)
        return 0
    except Exception as ex:
        print("STREAM GO REFUSED -- automatic rota service: %s: %s"
              % (type(ex).__name__, ex))
        return 1


def binding_state():
    """(state, line) from runtime_binding, or ('unknown', why) if it can't be read.

    Nothing on this path raises: a stream tool that dies while diagnosing a dead
    binding is the 2026-09-07 failure twice over.
    """
    try:
        import runtime_binding
        session = (runtime_binding.note() or {}).get("lastCoreSession")
        return runtime_binding.describe(session)
    except Exception as ex:
        return "unknown", "could not read the runtime binding (%s: %s)" % (
            type(ex).__name__, ex)


BINDING_REPAIR = "\n".join((
    "   A Hands CANNOT fix this from inside a turn -- it cannot read its own",
    "   session id without working the problem. Hand back and report this line.",
    "   Core repairs it with:  python stream.py bind-check --repair",
    "   (or simply crosses one more tool boundary: the lifecycle hook now",
    "   re-binds a dead binding by itself at the Core's next Bash call.)"))


def binding_warning():
    """One loud line for `go`/`status`/`setup`, or None when the binding is fine."""
    state, line = binding_state()
    if state == "live":
        return None
    return "!! BINDING %s -- %s\n%s" % (state.upper(), line, BINDING_REPAIR)


def cmd_bind_check(a):
    """Say what the session-runtime binding is, and optionally rebind it.

    The 2026-09-07 warm start cost three turns: `errata.bat` was launched at a
    running game while the previous session's claude.exe was still exiting, so
    SessionStart's bind lost to a binding that was alive for a few more seconds
    and dead for the rest of the night. Nothing re-bound, and every `hands-claim`
    refused with a message about the hook.
    """
    state, line = binding_state()
    print("binding : %s -- %s" % (state.upper(), line))
    if not getattr(a, "repair", False):
        if state != "live":
            print(BINDING_REPAIR)
        return 0 if state == "live" else 1
    if state == "live":
        print("NOTHING TO REPAIR -- the binding is already live.")
        return 0
    if state == "mismatch":
        print("REPAIR REFUSED -- another LIVE session holds the binding. Two "
              "sessions cannot both drive one game; close the other one first.")
        return 1
    alive, info = turnclock.hands_alive()
    if alive:
        print("REPAIR REFUSED -- agent %s crossed a tool boundary %s ago: a Hands "
              "fork is running. A fork must HAND BACK; only the Core may rebind, "
              "because only the Core's session id is the one to bind."
              % (info.get("agent"), turnclock.mmss(info.get("age") or 0)))
        return 1
    try:
        import runtime_binding
        session = (runtime_binding.note() or {}).get("lastCoreSession")
        if not session:
            print("REPAIR REFUSED -- no Core session id on record. The lifecycle "
                  "hook writes it on every Core tool call; if it is missing, "
                  "RIMWORLD_SESSION is not set or the hooks are not wired "
                  "(instruments\\.claude\\settings.json).")
            return 1
        result = runtime_binding.bind(session, runtime_binding.claude_ancestor())
    except Exception as ex:
        print("REPAIR FAILED -- %s: %s" % (type(ex).__name__, ex))
        return 1
    print("BOUND -- session %s to host pid %s. `hands-claim` will work now."
          % (result["session_id"], result["host_pid"]))
    return 0


def cmd_hands_claim(_a):
    """Verify that PreToolUse routed durable events to this native Hands.

    Four different conditions used to print one sentence about the hook. Only
    the last of them is actually about the hook; the other three are the
    binding, and a fork that believed the message went looking in the wrong
    place for twenty minutes.
    """
    try:
        import event_bus
        import runtime_binding
        state, line = binding_state()
        if state != "live":
            print("HANDS CLAIM REFUSED -- %s" % line)
            print(BINDING_REPAIR)
            return 1
        session = runtime_binding.load().get("session_id")
        recipient = event_bus.current_recipient(session)
        if recipient == "core":
            print("HANDS CLAIM REFUSED -- durable events still route to `core` "
                  "even though the %s. The PreToolUse `register` hook did not "
                  "run for THIS agent." % line)
            print("   Check RIMWORLD_SESSION=1 and the `register` hook in "
                  "instruments\\.claude\\settings.json, then run hands-claim "
                  "once more. If it refuses again, hand back and say so.")
            return 1
        print("HANDS CLAIMED -- turn %s events route to native agent %s"
              % (load().get("turn", 0), recipient))
        return 0
    except Exception as ex:
        print("HANDS CLAIM REFUSED -- %s: %s" % (type(ex).__name__, ex))
        return 1


def cmd_hands_release(a):
    """Explicit handoff: stop durable play first, then route events to Core.

    Release pauses. A turn boundary is a paused boundary, and the pause is what
    stops a dead fork's last order running on into the next turn.

    `--no-pause` is the documented escape and the only one: the Core clearing up
    after a fork it has confirmed gone, where freezing the picture on stream
    buys nothing. A 2026-09-06 pass also made a stale heartbeat skip the pause
    implicitly, which contradicted both the tests and the PLAYBOOK -- a stale
    heartbeat is now a printed hint to pass the flag, not a silent decision.
    """
    try:
        import event_bus
        import runtime_binding
        import play
        session = runtime_binding.load().get("session_id")
        recipient = event_bus.current_recipient(session)
        if not session or recipient == "core":
            print("HANDS RELEASE REFUSED -- no native Hands owns this session")
            return 1
        alive, info = turnclock.hands_alive()
        if bool(getattr(a, "no_pause", False)):
            event_bus.handback(session, recipient)
            print("HANDS RELEASED WITHOUT PAUSING (--no-pause) -- durable events "
                  "now route to Core and the game clock is untouched; "
                  "`python play.py pause` if you do want it stopped")
            return 0
        if alive is False:
            print("[hands] agent %s has touched nothing for %s -- if that fork is "
                  "gone, `hands-release --no-pause` leaves the picture moving. "
                  "Pausing, because release pauses unless told otherwise."
                  % (info.get("agent"), turnclock.mmss(info.get("age") or 0)))
        if play.pause() != 0:
            print("HANDS RELEASE REFUSED -- supervised play did not pause")
            return 1
        event_bus.handback(session, recipient)
        print("HANDS RELEASED -- supervised play paused; durable events now route to Core; native stop will close the turn")
        return 0
    except Exception as ex:
        print("HANDS RELEASE REFUSED -- %s: %s" % (type(ex).__name__, ex))
        return 1


def cmd_hands_close(a):
    """Stamp `handsEndedAt` for a turn whose fork is gone. Nothing else.

    The one recovery for the stuck tracker. Everything a `reset` also destroyed
    -- the turn counter, the pinned summary, the goals bar, the feed, the rota
    service, the game clock -- is deliberately left exactly as it was, so the
    cost of an unclosed turn is one command instead of a scheduler restart, a
    counter patch and a re-posted bar.

    The one exception, added 2026-09-07: the overlay's `phase` label is pushed
    back to `core`. It is the closure, not a narration, and leaving it saying
    `hands (turn N)` after the turn closed is what made `status` contradict
    itself in a single output.
    """
    d = load()
    turn = int(d.get("turn") or 0)
    started = float(d.get("handsStartedAt") or 0)
    ended = float(d.get("handsEndedAt") or 0)
    if not started:
        print("NOTHING TO CLOSE -- no turn is open (`stream.py hands-start` "
              "opens turn %d)" % (turn + 1))
        return 0
    if ended >= started:
        print("NOTHING TO CLOSE -- turn %d was already closed %s ago by %s"
              % (turn, turnclock.mmss(max(0.0, time.time() - ended)),
                 d.get("handsEndedBy") or "an unnamed agent"))
        return 0
    alive, info = turnclock.hands_alive(d)
    if alive and not a.force:
        print("HANDS CLOSE REFUSED -- agent %s crossed a tool boundary %s ago: "
              "that fork is ALIVE and still holds turn %d. End it with TaskStop "
              "and let its stop hook close the turn; --force overrides this only "
              "if you know the heartbeat is lying."
              % (info.get("agent"), turnclock.mmss(info.get("age") or 0), turn))
        return 1
    now = time.time()

    def stamp(state):
        if float(state.get("handsEndedAt") or 0) >= float(state.get("handsStartedAt") or 0):
            return False
        state["handsEndedAt"] = now
        state["handsEndedBy"] = "core-forced"
        state["handsClosedReason"] = a.reason or "not given"
        return True

    if not update(stamp):
        print("NOTHING TO CLOSE -- a native stop closed turn %d while this ran"
              % turn)
        return 0
    print("CLOSED turn %d -- handsEndedAt stamped %s after hands-start, "
          "handsEndedBy core-forced" % (turn, turnclock.mmss(now - started)))
    print("   reason   : %s" % (a.reason or "not given"))
    if alive is False:
        print("   liveness : agent %s had touched nothing for %s"
              % (info.get("agent"), turnclock.mmss(info.get("age") or 0)))
    else:
        print("   liveness : no heartbeat for this turn -- unknown, not alive")
    print("   untouched: turn counter (still %d), pinned summary, goals bar, "
          "feed, rota service, game clock" % turn)
    print("   " + turnclock.report_line())
    push_phase_core(turn)
    print("   next     : `handback --summary ... --mood ...` narrates the turn, "
          "then `hands-start` opens turn %d" % (turn + 1))
    return 0


def push_phase_core(turn):
    """Put the overlay's phase label back on `core`. One line if it misses.

    The label used to survive the close, so `status` printed `no turn open` and
    `phase : hands (turn 1)` together and a turn's diagnosis went the wrong way
    entirely. Every path that closes a turn -- this, `handback`, the native
    SubagentStop and SessionEnd hooks -- pushes it now.
    """
    ok, why = ov.post("/status", timeout=FAST, phase="core", turn=int(turn or 0))
    if not ok:
        missed("/status", why)
    return ok


def cmd_handback(a):
    d0 = load()
    started = float(d0.get("handsStartedAt") or 0)
    ended = float(d0.get("handsEndedAt") or 0)
    if started and ended < started:
        print("HANDBACK REFUSED -- native Hands has not stopped; do not use an "
              "intermediate report as a turn boundary")
        line = turnclock.liveness_line(d0)
        if line:
            print(line)
        else:
            print("   no heartbeat for that turn. If the fork is gone, "
                  "`python stream.py hands-close` closes it and leaves the "
                  "counter, pin, goals bar, feed and rota alone.")
        return 1
    # A Core TaskStop can kill Hands before it runs hands-release. Recover that
    # route here so neither native play nor durable events remain owned by a
    # dead agent after the turn is declared closed.
    try:
        import event_bus
        import runtime_binding
        import play
        session = runtime_binding.load().get("session_id")
        recipient = event_bus.current_recipient(session) if session else "core"
        if recipient != "core":
            if play.pause() != 0:
                print("HANDBACK REFUSED -- supervised play did not pause")
                return 1
            event_bus.handback(session, recipient)
    except Exception as ex:
        print("HANDBACK REFUSED -- could not recover Hands route: %s: %s"
              % (type(ex).__name__, ex))
        return 1
    turn = int(d0.get("turn") or 0)
    # Only native SubagentStop writes handsEndedAt. Core handback publishes the
    # completed report; it must never turn an intermediate report into proof
    # that a live fork exited.
    elapsed = max(0.0, ended - started) if started and ended >= started else None
    # Printed first and unconditionally, before a word goes to the overlay: this
    # is the line that says the fork holding turn N is DONE. A completed fork
    # that is sent a message starts running again, and then two agents are in
    # the game -- so the Core needs the closure in its own transcript even on a
    # night when nothing is narrated. `hands-check` answers the same question
    # from the other side, before a message goes out.
    ran = "" if elapsed is None else ", hands ran %s" % turnclock.mmss(elapsed)
    print("closed: turn %d%s -- that fork is DONE; never SendMessage it, "
          "spawn a new one" % (turn, ran))
    # Which write of hands-last.md this is decides whether the next turn is
    # briefed off the report or off a draft the report reverses.
    print(turnclock.report_line(d0))
    long_summary = bool(a.summary) and len(a.summary) > SUMMARY_CAP
    if long_summary:
        warn("summary is %d chars, cap is %d -- posting it now; Luna will try to "
             "fit it to the card in the background."
             % (len(a.summary), SUMMARY_CAP))
    mood = a.mood
    if mood not in ov.MOODS:
        print("stream.py: %r is not a mood. The eight are: %s"
              % (mood, " ".join(ov.MOODS)), file=sys.stderr)
        mood = None
    status = {"phase": "core", "turn": turn}
    if a.short:
        status["label"] = a.short
    ok, why = ov.post("/status", timeout=FAST, **status)
    if not ok:
        down(why)
        return 0
    event = {"kind": "summary", "text": a.summary, "turn": turn}
    if mood:
        event["mood"] = mood
    # Both of these were fire-and-check-nothing until 2026-09-02. /status is the
    # probe -- if it answered, the server is up -- so anything that fails after
    # it is one specific panel going stale on screen, and worth a line.
    ok, why = ov.post("/event", timeout=FAST, **event)
    if not ok:
        missed("/event", why)
    goals = {k: v for k, v in (("short", a.short), ("long", a.long)) if v}
    if goals:
        ok, why = ov.post("/goals", timeout=FAST, **goals)
        if not ok:
            missed("/goals", why)
    if long_summary:
        hand_to_luna(a.summary, turn, mood)
    print("turn %d -> core" % turn)
    return 0


def hand_to_luna(summary, turn, mood):
    """Fire-and-forget: a detached Luna re-posts a version that fits the card.

    Deliberately last, and deliberately after the full summary has already gone
    up -- the pinned text is correct the instant handback returns, and Luna only
    ever swaps it for a shorter one. The import is here rather than at the top
    so a turn with a short summary never pays for it, and every failure is a
    warning line: the full text with its on-screen ellipsis is the fallback.
    """
    try:
        import luna
        luna.spawn(summary, turn, mood)
    except Exception as e:
        warn("could not hand the summary to Luna (%s: %s) -- the full text stands"
             % (type(e).__name__, e))


def cmd_human_check(_a):
    # Kept as a harmless compatibility command. Reading the overlay inbox here
    # duplicated M's message after the native durable route delivered it.
    print("native event routing owns M's inbox; nothing to poll")
    return 0


def cmd_hands_check(_a):
    """Is the fork holding the turn still running, or has it already finished?

    Run this **before every SendMessage to a fork.** A fork that has completed
    is restarted by a message, and then two agents are in one UI -- the Core
    cannot see the completion until the notification lands, which is after the
    send, so the transcript is not the place to look. The native SubagentStop
    hook stamps `handsEndedAt` only when the fork actually returns; report
    drafts are deliberately irrelevant.

    Purely local -- no overlay, no bridge, and it never fails a turn.
    """
    done, secs = turnclock.handed_back()
    if done is None:
        print("no turn open (run `stream.py hands-start` when you spawn a fork)"
              " -- nothing to message")
        return 0
    if done:
        print("FORK HANDED BACK (native stop completed %d s ago) -- DO NOT"
              " SendMessage it; start a new fork" % int(secs))
        print(turnclock.report_line())
        return 0
    if secs > turnclock.MAX_AGE:
        print("FORK STATE UNRESOLVED (unclosed native turn, %s old) -- do not "
              "message it or start another; check the native task list, then "
              "`python stream.py hands-close --reason \"...\"`"
              % turnclock.mmss(secs))
        print(turnclock.liveness_line() or
              "[hands] no heartbeat for this turn -- unknown, not proof of life")
        return 0
    print("fork still running (%s elapsed)" % turnclock.mmss(secs))
    live = turnclock.liveness_line()
    if live:
        print(live)
    print(turnclock.report_line())
    line = turnclock.turn_budget_line(secs)
    if line:
        print(line)
        print("   ending a turn early is TaskStop, never a message")
    return 0


def cmd_status(_a):
    # Local first: the mode and the turn clock live on disk, so they are the
    # lines here still worth printing when the overlay is down and everything
    # below it is not.
    print("mode  : %s" % (current_mode() or "none"))
    _e = turnclock.turn_elapsed()
    print("turn  : %s" % ("no turn open -- the Core has the game"
                          if _e is None else "%s elapsed of %s"
                          % (turnclock.mmss(_e), turnclock.mmss(turnclock.BUDGET))))
    _line = turnclock.turn_budget_line(_e)
    if _line:
        print(_line)
    # Only while a turn is open. A heartbeat left behind by the fork that just
    # handed back is history, and printing it under "no turn open" reported a
    # closed turn's stale token as a live problem.
    _live = turnclock.liveness_line() if _e is not None else None
    if _live:
        print(_live)
    _warning = binding_warning()
    if _warning:
        print(_warning)
    try:
        import combat
        inherited = combat.inherited_warning()
        if inherited:
            print(inherited)
    except Exception as ex:
        warn("could not check combat-ledger ownership (%s)" % ex)
    state, why = ov.get("/state", timeout=FAST)
    if state is None:
        down(why)
        return 0
    st = state.get("status") or {}
    goals = state.get("goals") or {}
    # One truth, from the local turn clock. The overlay's label is a picture on
    # a screen; it lags, and on 2026-09-07 it said `hands (turn 1)` in the same
    # output as `no turn open`. Both were printed as fact and one turn believed
    # the wrong one -- so the disagreement is now named as a disagreement.
    _label = "  label: %s" % st["label"] if st.get("label") else ""
    _truth = "hands (turn %s)" % load().get("turn", 0) if _e is not None else "core"
    _shown = st.get("phase")
    if (_shown == "hands") == (_e is not None):
        print("phase : %s (turn %s)%s" % (_shown, st.get("turn"), _label))
    else:
        print("phase : %s  <- the turn clock; the OVERLAY still shows %r (turn "
              "%s)%s and is stale" % (_truth, _shown, st.get("turn"), _label))
        print("        fix the label with `python stream.py handback ...` (or "
              "`hands-close`, which now pushes it too)")
    _local_turn = load().get("turn", 0)
    if st.get("turn") not in (None, _local_turn):
        print("!! overlay shows turn %s but the local counter is %s -- the overlay is stale;"
              " `python stream.py go` re-syncs it (and resets the counter for a new session)"
              % (st.get("turn"), _local_turn))
    print("mood  : %s" % state.get("mood"))
    print("short : %s" % (goals.get("short") or "-"))
    print("long  : %s" % (goals.get("long") or "-"))
    print("feed  : %d items; local turn counter %s"
          % (len(state.get("feed") or []), load().get("turn", 0)))
    return 0


def cmd_reset(_a):
    """Everything back to pre-turn-1: counter, pinned card, status, feed.

    The pin is the server's `lastSummary`, which lives outside the feed and so
    survives `/reset` -- clearing the feed alone leaves the previous run's
    headline and turn number on screen. The server rejects an empty summary, so
    the pin is overwritten rather than emptied, and the fresh one goes first so
    that `/reset` sweeps its feed item away with the rest.

    Local state: only `turn` is zeroed. `mode`/`mode_ts` describe the session,
    not the run, and `last_human_ts` is the watermark of M's messages
    already delivered -- rewinding it would replay them.
    """
    try:
        import rota_service
        rota_service.stop()
    except Exception as ex:
        warn("could not stop automatic rota service (%s: %s)" %
             (type(ex).__name__, ex))
    d = load()
    d["turn"] = 0
    d.pop("handsStartedAt", None)
    d.pop("handsEndedAt", None)
    d.pop("handsEndedBy", None)
    d.pop("handsAgent", None)
    save(d)
    ok, why = ov.post("/event", timeout=FAST, kind="summary",
                      text=FRESH_SUMMARY, turn=0)
    if not ok:
        down(why)
        warn("local counter is 0; the overlay still shows the old run")
        return 0
    for path, payload in (("/reset", {}),
                          ("/status", {"phase": "core", "turn": 0, "label": ""})):
        ok, why = ov.post(path, timeout=FAST, **payload)
        if not ok:
            missed(path, why)
    print("turn 0 -- pin, status and feed cleared")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="stream.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("goals", help="set the bottom bar outside a turn boundary")
    s.add_argument("--long", help="long-term goal (the standing one is in CHRONICLE.md)")
    s.add_argument("--short", help="current focus")
    s.set_defaults(fn=cmd_goals)

    s = sub.add_parser("go", help="start session-level automatic services and "
                                   "zero the turn counter for a new session")
    s.add_argument("--keep-turns", action="store_true",
                   help="do not reset the turn counter (mid-session re-run)")
    s.set_defaults(fn=cmd_go)

    s = sub.add_parser("hands-start", help="new turn: bump the counter, phase=hands")
    s.add_argument("--goal", help="short-term goal for the turn (posts /goals)")
    s.set_defaults(fn=cmd_hands_start)

    s = sub.add_parser("hands-claim", help="verify native Hands event ownership")
    s.set_defaults(fn=cmd_hands_claim)

    s = sub.add_parser("hands-release", help="pause play and hand events to Core")
    s.add_argument("--no-pause", action="store_true",
                   help="route events back without touching the game clock")
    s.set_defaults(fn=cmd_hands_release)

    s = sub.add_parser("hands-close",
                       help="stamp handsEndedAt for a turn whose fork is gone; "
                            "changes nothing else")
    s.add_argument("--reason", help="why the turn is being closed by hand")
    s.add_argument("--force", action="store_true",
                   help="close even against a live heartbeat")
    s.set_defaults(fn=cmd_hands_close)

    s = sub.add_parser("handback", help="turn over: phase=core, summary, goals")
    s.add_argument("--summary", required=True, help="what the turn did and what it means; aim 220-300 chars, Luna fits an overrun to the card")
    s.add_argument("--mood", required=True, help=" ".join(ov.MOODS))
    s.add_argument("--short", help="new short-term goal")
    s.add_argument("--long", help="new long-term goal (only when it changes)")
    s.set_defaults(fn=cmd_handback)

    s = sub.add_parser("human-check", help="print any new messages from M")
    s.set_defaults(fn=cmd_human_check)

    s = sub.add_parser("hands-check",
                       help="has the fork already handed back? run before any SendMessage")
    s.set_defaults(fn=cmd_hands_check)

    s = sub.add_parser("mode", help="read or set the session mode; then read modes\\<name>.md")
    s.add_argument("name", nargs="?", help="%s -- omit to read the current one"
                                           % " | ".join(MODES))
    s.set_defaults(fn=cmd_mode)

    s = sub.add_parser("bind-check",
                       help="is state\\session-runtime.json bound to a LIVE session host?")
    s.add_argument("--repair", action="store_true",
                   help="rebind this session's Core to the live Claude host")
    s.set_defaults(fn=cmd_bind_check)

    s = sub.add_parser("status", help="compact view of what the overlay is showing")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("reset", help="back to pre-turn-1: counter, pin, status, feed")
    s.set_defaults(fn=cmd_reset)
    return p


def main(argv):
    p = build_parser()
    a = p.parse_args(argv)
    if not getattr(a, "fn", None):
        p.print_help()
        return 0
    try:
        return a.fn(a)
    except Exception as e:
        # Belt and braces: overlay_client swallows its own, so this is only
        # reachable by a bug in this file. It still must not break the Core.
        warn("gave up: %s: %s" % (type(e).__name__, e))
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
