"""Fire-and-forget POSTs to the stream overlay. The stream never touches the game.

  import overlay_client as ov
  ov.say("Finn made an awful parka. It's better than nothing. Barely.", "welp")
  ov.overlay("/goals", short="Survive the cold snap")

The overlay server (`C:\\Home\\Fable 5\\made\\stream-overlay\\server.py`) holds what
the OBS page shows, and this module is the only thing in the harness that talks
to it. Its whole design is one sentence: if the server is down, errata plays
RimWorld and nobody narrates. So: 2-second socket timeout, every exception
swallowed, one line per dropped post in `state\\overlay-drops.log`. Nothing here
can raise into a caller.

Swallowed must not become invisible, so there is also **a bounded retry**, on a
budget the whole process shares, and **a warning that reaches a person** --
through the return value on the synchronous path, one stderr line at exit on the
fire-and-forget one. See RETRY_BUDGET and WARN_ON_EXIT. Neither can raise, block
a turn, or change what an instrument does.

## Daemon threads and short-lived CLIs

A daemon thread is right for a long-lived process and wrong for a one-shot CLI:
`python say.py "..."` exits microseconds later, and the interpreter kills daemon
threads on the way out, so the POST is dropped roughly every time. So every
thread is registered and an `atexit` hook joins whatever is still in flight with
a **total** deadline of JOIN_DEADLINE seconds. Long-lived processes are
unaffected; one-shot CLIs deliver before exit. Nothing can hang past ~2.5s no
matter how many posts are outstanding, because the deadline is shared, not
per-thread.

## Host and port

**Use `127.0.0.1`, never `localhost`.** The name resolves to `::1` first and the
server binds IPv4 only, so every post pays a full IPv6 connect failure -- two
seconds against one millisecond. `_ipv4()` rewrites a `localhost` host in
$OVERLAY_URL; any other host is left exactly as given.

**Port 8080 is GABS** (`rim.py` talks to the bridge there), so the overlay lives
on 8090:

    python server.py --port 8090            # in the stream-overlay folder
    ...and the OBS Browser source URL is http://127.0.0.1:8090/

**The default below is 8090, baked in.** It cannot be an env var: every say.py
call from a play session is a fresh process in a fresh shell, so `set
OVERLAY_URL=...` at session start reaches nothing, and a client defaulting to
8080 would silently post every narration at GABS. $OVERLAY_URL still wins when
present, for one-off testing.
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# Exactly the eight strings server.py validates against. Kept here so a typo can
# be caught locally, without a round trip, and so `say.py --mood` can list them.
MOODS = ("happy", "thinking", "sad", "scared", "welp", "excited", "angry", "veryhappy")
KINDS = ("thought", "summary", "human", "chat", "mood", "letter")
# Letter colouring. Not a mood: the face is errata's, the tone is the event's.
TONES = ("good", "bad", "neutral")

DEFAULT_URL = "http://127.0.0.1:8090"   # NOT localhost, NOT 8080 -- see docstring
TIMEOUT = 2.0          # per HANDOFF.md
JOIN_DEADLINE = 2.5    # total wall time an exiting process will wait for posts

# --- retries, added 2026-09-02 (BUGS.md) -------------------------------------
#
# The overlay is on 127.0.0.1, so a timeout here is never a slow network. It is
# either the server busy -- `persist()` rewrites state.json under the state lock
# on every single event -- or a server that has just been restarted. Both are
# worth one more go a fraction of a second later, and both used to lose the post
# outright.
#
# What a retry must NOT do is stall a turn, so the budget is per PROCESS rather
# than per post: RETRY_BUDGET is the most wall time this interpreter will ever
# spend retrying, however many posts it makes, and it is charged in real elapsed
# time. Worst case a caller can see is its own timeout on attempt one, plus
# RETRY_BUDGET, once, for the life of the process -- so `stream.py handback`,
# which makes three posts, still cannot cost more than one post's worth of
# waiting more than it did before this existed.
#
# **A third measured number for the pair in the docstring, 2026-09-02: on this
# machine a REFUSED TCP connect costs ~2.03 seconds**, not the microsecond a
# refusal costs on Linux. Measured against a loopback port with nothing bound:
# `connect()` sat for 2.051s and then raised WinError 10061. That is the same
# ~2s the docstring above blames on the IPv6 fallback, which makes the real
# story simpler than the one written there: it is not that ::1 is slow, it is
# that *any* refused connect on this box takes two seconds. It follows that a
# server which is simply not running is indistinguishable from one that hangs
# (the socket timeout always fires first) and that retrying costs real time
# either way. Hence a hard budget rather than a retry count: three attempts
# would otherwise be six seconds of nothing.
RETRY_SLEEPS = (0.10, 0.35)   # backoff before attempts 2 and 3
RETRY_BUDGET = 1.5            # seconds of retrying, total, for this process

# One line on stderr at exit when a fire-and-forget post never landed. It has to
# be at exit: this module's contract is that narration never interrupts a turn,
# and a background thread printing mid-instrument lands in the middle of that
# instrument's own output. At exit it interleaves with nothing -- and the
# alternative is the one we had, which is a blank overlay and nobody knowing.
# A caller that must be silent can set `overlay_client.WARN_ON_EXIT = False`.
WARN_ON_EXIT = True

_HERE = Path(__file__).resolve().parent
DROPS_LOG = _HERE / "state" / "overlay-drops.log"

_pending = []
_pending_lock = threading.Lock()
_atexit_armed = False

_retry_left = [RETRY_BUDGET]   # seconds of retrying this process still has
_retry_lock = threading.Lock()
_silent_drops = []             # (path, reason) from the fire-and-forget path only


def _ipv4(url):
    """Rewrite a `localhost` host to 127.0.0.1. Same machine, 2000x faster.

    Only the exact host `localhost` is touched -- a real hostname, an IP, or a
    machine on the LAN is passed through untouched.
    """
    for prefix in ("http://localhost", "https://localhost"):
        if url.startswith(prefix) and url[len(prefix):len(prefix) + 1] in ("", ":", "/"):
            return url.replace("//localhost", "//127.0.0.1", 1)
    return url


def base_url():
    """Where the overlay server is. $OVERLAY_URL wins; 127.0.0.1:8090 otherwise."""
    return _ipv4((os.environ.get("OVERLAY_URL") or DEFAULT_URL).strip()).rstrip("/")


def log_drop(path, payload, err):
    """One line per post that did not land. Never raises, never prints."""
    try:
        DROPS_LOG.parent.mkdir(parents=True, exist_ok=True)
        blob = json.dumps(payload, ensure_ascii=False)
        if len(blob) > 300:
            blob = blob[:297] + "..."
        with DROPS_LOG.open("a", encoding="utf-8") as f:
            f.write("%s  %s  %s  -- %s\n"
                    % (time.strftime("%Y-%m-%d %H:%M:%S"), path, blob, err))
    except Exception:
        pass


def _describe(err):
    """A short reason, including the server's own 400 text when there is one."""
    if isinstance(err, urllib.error.HTTPError):
        try:
            body = err.read().decode("utf-8", "replace")[:200]
        except Exception:
            body = ""
        return "HTTP %s %s" % (err.code, body)
    return "%s: %s" % (type(err).__name__, err)


def _budget_left():
    with _retry_lock:
        return _retry_left[0]


def _budget_spend(secs):
    with _retry_lock:
        _retry_left[0] = max(0.0, _retry_left[0] - max(0.0, secs))


def _worth_retrying(err):
    """A timeout or a refused connection is worth another go; a 400 never is.

    The server validates every payload and answers 400 for a bad mood, a missing
    text, a non-integer tick. Sending the identical body again gets the
    identical 400 and spends budget a real outage may need thirty seconds later.
    5xx is different: that is a server that fell over part-way through, which the
    next attempt may well survive.
    """
    if isinstance(err, urllib.error.HTTPError):
        return err.code >= 500
    return True


def _send(path, payload, timeout):
    """One POST with a bounded retry. Returns (ok, reason). Never raises.

    The single funnel for both `post()` and the background `_blocking_post()`,
    so a thought, a mood, a letter and a `/goals` all get the same treatment --
    they all had the same silent drop, and BUGS.md only happened to notice it on
    the goals bar because that is the one that stays wrong on screen for an hour.

    `timeout` bounds the first attempt only. Everything after it comes out of
    the process-wide retry budget, including the sleeps, so this function cannot
    add more than RETRY_BUDGET to a process no matter how it is called.
    """
    url = base_url() + path
    data = json.dumps(payload).encode("utf-8")
    reason = None
    for attempt in range(len(RETRY_SLEEPS) + 1):
        started = time.time()
        try:
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"},
                method="POST")
            urllib.request.urlopen(req, timeout=timeout).read()
            if attempt:
                _budget_spend(time.time() - started)
            return True, None
        except Exception as e:
            if attempt:
                _budget_spend(time.time() - started)
            reason = _describe(e)
            if attempt == len(RETRY_SLEEPS) or not _worth_retrying(e):
                break
            nap = RETRY_SLEEPS[attempt]
            if _budget_left() <= nap + 0.05:
                break        # not enough left to sleep, let alone to ask again
            time.sleep(nap)
            _budget_spend(nap)
            timeout = min(timeout, _budget_left())
            if timeout <= 0.05:
                break
    log_drop(path, payload, reason)
    return False, reason


def post(path, timeout=TIMEOUT, **payload):
    """Synchronous POST. Returns (ok, reason). Never raises.

    Used where the caller wants to know -- `stream.py` prints one warning line
    naming the endpoint when a post does not land. Everything on the gameplay
    path uses `overlay()`. Retries per `_send`; `timeout` still bounds the first
    attempt exactly as it did before, which is what keeps stream.py's FAST=1.2
    meaningful.
    """
    return _send(path, payload, timeout)


def get(path="/state", timeout=TIMEOUT):
    """Synchronous GET. Returns (parsed_json_or_None, reason). Never raises."""
    try:
        raw = urllib.request.urlopen(base_url() + path, timeout=timeout).read()
        return json.loads(raw.decode("utf-8")), None
    except Exception as e:
        reason = _describe(e)
        log_drop(path, {"method": "GET"}, reason)
        return None, reason


def _reap():
    with _pending_lock:
        _pending[:] = [e for e in _pending if e[0].is_alive()]


def _drain():
    """atexit: give in-flight posts a bounded chance to land before we go."""
    deadline = time.time() + JOIN_DEADLINE
    with _pending_lock:
        entries = list(_pending)
    for t, _path in entries:
        left = deadline - time.time()
        if left <= 0:
            break
        try:
            t.join(left)
        except Exception:
            pass
    # A thread still running is a post being abandoned on the doorstep -- the
    # interpreter kills daemon threads on the way out. That is as much a dropped
    # post as a refused connection is, and the case a hanging server produces:
    # JOIN_DEADLINE is 2.5s and a post that times out and then retries can want
    # more than that. Counting them here is the only chance anyone gets to hear
    # about it, because the thread that would have logged it never returns.
    stuck = [path for t, path in entries if t.is_alive()]
    _warn_drops(stuck)


def _warn_drops(stuck=()):
    """One stderr line at exit if a fire-and-forget post never landed.

    Deliberately a summary rather than a line per post: a session run with the
    overlay switched off would otherwise narrate its own failure to narrate,
    once per thought, into the middle of an agent's context. One line naming the
    count, the endpoint, the URL and the reason is enough for whoever is reading
    the terminal to go and start the server -- which is all this needs to
    achieve, since by construction nothing here changes what the game does.
    """
    if not WARN_ON_EXIT:
        return
    with _pending_lock:
        drops = list(_silent_drops)
        del _silent_drops[:]
    if not drops and not stuck:
        return
    try:
        if drops:
            path, reason = drops[-1]
        else:
            path, reason = stuck[-1], "still in flight after %gs" % JOIN_DEADLINE
        sys.stderr.write(
            "[overlay] %d post%s did not land -- last %s at %s (%s). Nothing on"
            " screen changed; details in %s\n"
            % (len(drops) + len(stuck), "" if len(drops) + len(stuck) == 1 else "s",
               path, base_url(), reason, DROPS_LOG))
    except Exception:
        pass


def _blocking_post(path, payload):
    """The actual request. Only ever called on a background thread.

    Retries and logs inside `_send`; the only thing added here is remembering
    that it failed, so `_warn_drops` can say so on the way out. The caller is
    long gone by now -- that is the whole point of this path -- so there is
    nobody to return the reason to.
    """
    try:
        ok, reason = _send(path, payload, TIMEOUT)
        if not ok:
            with _pending_lock:
                _silent_drops.append((path, reason))
    except Exception as e:
        # Unreachable unless _send itself has a bug; still not the game's problem.
        log_drop(path, payload, _describe(e))


def _start(worker, path, payload):
    """Run `worker` on a registered daemon thread. Returns it, or None."""
    global _atexit_armed
    try:
        t = threading.Thread(target=worker, daemon=True)
        _reap()
        with _pending_lock:
            _pending.append((t, path))   # the path so _drain can name a stuck one
        if not _atexit_armed:
            import atexit
            atexit.register(_drain)
            _atexit_armed = True
        t.start()
        return t
    except Exception as e:
        # Even failing to start a thread is the stream's problem, not the game's.
        log_drop(path, payload, _describe(e))
        return None


def overlay(path, **payload):
    """Fire-and-forget POST. Returns immediately; returns the thread, or None.

    Never raises, never blocks the caller. A post that does not land is retried
    on the shared budget (`_send`), then written to state\\overlay-drops.log and
    counted for the one-line stderr summary at exit (`_warn_drops`). None of
    that reaches the caller, which is still free to ignore the return value.
    """
    return _start(lambda: _blocking_post(path, payload), path, payload)


def overlay_ordered(path, payloads):
    """Several fire-and-forget POSTs that must land IN THE ORDER GIVEN.

    `overlay()` starts a thread per post, and threads race: five letters fired
    in one breath arrived in the feed shuffled (measured, 2026-09-01), because
    the server numbers them as they land. The feed is a timeline, so a batch of
    letters that reads out of order is a lie about the colony's day.

    One thread, posting in sequence, fixes it and costs the caller nothing: the
    caller still returns immediately, and the atexit drain still bounds the
    whole batch at JOIN_DEADLINE for a short-lived CLI.
    """
    payloads = [p for p in (payloads or []) if p]
    if not payloads:
        return None

    def _send_all():
        for p in payloads:
            _blocking_post(path, p)

    return _start(_send_all, path, {"batch": len(payloads)})


def say(text, mood=None):
    """Post one thought. Empty/None text posts nothing at all (per HANDOFF.md).

    A mood that isn't one of the eight is logged as a drop and the thought is
    posted *without* it -- losing the face is better than losing the line, and
    the server would 400 the whole event otherwise.
    """
    text = (text or "").strip()
    if not text:
        return None
    payload = {"kind": "thought", "text": text}
    if mood:
        if mood in MOODS:
            payload["mood"] = mood
        else:
            log_drop("/event", {"mood": mood}, "not one of %s; thought sent faceless"
                     % (list(MOODS),))
    return overlay("/event", **payload)


def set_mood(mood):
    """Change the face with no words. Invalid moods are dropped, not raised."""
    if not mood:
        return None
    if mood not in MOODS:
        log_drop("/event", {"kind": "mood", "mood": mood},
                 "not one of %s" % (list(MOODS),))
        return None
    return overlay("/event", kind="mood", mood=mood)


def push_tick(tick):
    """Tell the overlay what in-game moment it is. Fire-and-forget.

    The overlay stamps every feed item with the colony clock, so a viewer can
    read "that raid was on day 12 at 14h" instead of "that was 40 real seconds
    ago while the game ran at Superfast". The server has its own fallback
    poller, but a poll costs a bridge call and the bridge serialises -- so
    wherever the harness has *already* read `ticksGame`, it hands the number
    over instead. `run.py` and `watch.py` do that every loop, which means the
    server's poller stays asleep for the whole of a play session.

    A non-integer tick (None from a refused `get_game_info`, most often) posts
    nothing at all rather than sending the server a 400.
    """
    try:
        tick = int(tick)
    except (TypeError, ValueError):
        return None
    return overlay("/game", tick=tick)


def post_letter(label, tick=None, tone="neutral"):
    """One RimWorld letter onto the feed. Label only -- the body stays in game.

    `tone` is good | bad | neutral and colours the card; an unknown one is
    logged and downgraded to neutral rather than losing the letter, the same
    trade `say()` makes with an unknown mood. `tick` should be the letter's
    `arrivalTick`, not now: letters sit on the stack for in-game hours, and
    stamping one with the moment it was noticed is how the overlay ends up
    saying a cold snap started at the wrong hour.
    """
    payload = _letter_payload(label, tick, tone)
    return overlay("/event", **payload) if payload else None


def _letter_payload(label, tick=None, tone="neutral"):
    label = (label or "").strip()
    if not label:
        return None
    if tone not in TONES:
        log_drop("/event", {"kind": "letter", "tone": tone},
                 "not one of %s; sent neutral" % (list(TONES),))
        tone = "neutral"
    payload = {"kind": "letter", "text": label, "tone": tone}
    try:
        if tick is not None:
            payload["tick"] = int(tick)
    except (TypeError, ValueError):
        pass
    return payload


def post_letters(items):
    """A batch of letters, kept in order. `items` is [(label, tick, tone), ...].

    Use this rather than a loop over `post_letter` whenever more than one
    letter is being sent at once -- see `overlay_ordered` for why.
    """
    payloads = []
    for it in items or ():
        try:
            label, tick, tone = (list(it) + [None, "neutral"])[:3]
        except Exception:
            continue
        p = _letter_payload(label, tick, tone or "neutral")
        if p:
            payloads.append(p)
    return overlay_ordered("/event", payloads)


def take_flags(argv):
    """Pull `--say TEXT` / `--mood X` out of an argv list.

    Returns (remaining_argv, text, mood). The instruments parse their arguments
    positionally by hand -- there is not one argparse in the folder -- so the
    flags are removed *before* their own parsing runs and nothing downstream
    shifts. Accepts `--say X` and `--say=X`. A trailing `--say` with no value is
    ignored rather than fatal: the stream never breaks a turn.
    """
    rest, text, mood = [], None, None
    i = 0
    argv = list(argv or [])
    while i < len(argv):
        a = argv[i]
        for name, setter in (("--say", "text"), ("--mood", "mood")):
            if a == name:
                if i + 1 < len(argv):
                    val = argv[i + 1]
                    i += 1
                else:
                    val = None
                if setter == "text":
                    text = val
                else:
                    mood = val
                break
            if a.startswith(name + "="):
                val = a[len(name) + 1:]
                if setter == "text":
                    text = val
                else:
                    mood = val
                break
        else:
            rest.append(a)
        i += 1
    return rest, text, mood


def say_flags(text, mood):
    """What an instrument's --say/--mood pair does once the action has run.

    Text present -> a thought (carrying the mood). Mood alone -> just the face.
    Neither -> nothing. Swallows everything.
    """
    try:
        if text:
            return say(text, mood)
        if mood:
            return set_mood(mood)
    except Exception:
        pass
    return None


if __name__ == "__main__":  # a smoke check, not an interface
    import sys
    rest, t, m = take_flags(sys.argv[1:])
    print("url  :", base_url())
    print("rest :", rest)
    print("say  :", t)
    print("mood :", m)
    say_flags(t, m)
