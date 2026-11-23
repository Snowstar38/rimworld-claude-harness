#!/usr/bin/env python3
"""
Local server for the errata stream overlay. Standard library only.

    python server.py              -> http://127.0.0.1:8090
    python server.py --port 9000

The default was 8080 until 2026-09-01, which is GABS -- the game bridge -- on
this machine, so a bare `python server.py` bound nothing and died with
WinError 10013. Every wrapper already compensated (`start-overlay.bat`,
`overlay_client.py`, `relay.py`); only the thing you actually type did not.
Bind 127.0.0.1, not `localhost`: this server is IPv4-only, Windows resolves
`localhost` to ::1 first, and the failed attempt costs 2.006s per request
(measured -- see `overlay_client.py:38-45`).

Endpoints (everything is JSON):

    POST /event    {"kind": "thought", "text": "...", "mood": "happy"}
                   kinds:  thought | summary | human | chat | mood | letter
                   optional keys: "mood" (see MOODS), "name" (chat only),
                                  "turn" (summary only), "tone" (letter only:
                                  good | bad | neutral), "tick" (any kind:
                                  the in-game ticksGame this happened at)
    POST /goals    {"long": "...", "short": "..."}     either key optional
    POST /status   {"phase": "hands" | "core", "turn": 12, "label": "..."}
                   "label" overrides the generated status line if present
    POST /game     {"tick": 1234567}   a tick push from the play harness
    POST /reset    clears the feed (goals/status stay)
    GET  /state    everything the overlay needs
    GET  /         the overlay page
    GET  /control.html   a little page for sending messages / poking state

Every event and every goal/status change is appended to events.jsonl and the
current state is written to state.json, both next to this file, so nothing on
screen is ever only on screen. Tick pushes are the one exception: they arrive
every second or two and are held in memory only, so the disk isn't churned for
a clock.

## In-game time (added 2026-09-01)

`/state` carries `game` -- the colony's own clock, not the wall clock:

    "game": {"tick": int|null, "paused": bool, "stale": bool, "wallTs": float|null}

It is filled from two places, and either alone is enough:

* **Pushes.** `run.py` and `watch.py` already read `ticksGame` every loop, so
  they POST /game with the number they have in hand. No extra bridge traffic.
* **A fallback poller** in this process (`_poller`), which calls
  `rimworld/get_game_info` only when no push has landed for POLL_AFTER
  seconds. It exists so the clock still moves when nobody is playing through
  the harness, and it is written to be invisible when it fails: the bridge
  call happens OUTSIDE the state lock, every exception is swallowed, and the
  worst case is `stale: true` beside the last tick anyone knew. **A dead
  bridge must never break or slow a request handler.** If `rim` can't even be
  imported, the poller never starts and `tick` stays null forever.

Two rules that look like bugs and aren't:

* **It never acknowledges a GABS attention item.** `rim.game()` does, which is
  right for the play loop and wrong here -- an attention item is the player's
  to see. So the poller goes through `rim.tool()` and reads a refusal as "no
  answer this time".
* **Ticks may go BACKWARDS** when an older save is loaded. The new reading is
  the truth; feed items stamped with a future tick are the client's problem to
  hide (rule: `item.tick > game.tick` means "from a timeline that no longer
  happened").

`paused` is inferred, not asked: two readings in a row with the same tick mean
the game isn't advancing. Nothing on the bridge reports the speed cheaply --
see `setup.observe_speed()` for the expensive honest answer.
"""
import argparse
import itertools
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_PATH = ROOT / "events.jsonl"
STATE_PATH = ROOT / "state.json"

# Where rim.py lives. Appended, not prepended: that folder holds map.py, run.py,
# time-adjacent names and a __pycache__, and none of them should get the chance
# to shadow a standard-library import in this process.
RIM_DIR = r"C:\Home\rimworld\instruments"

MOODS = ("happy", "thinking", "sad", "scared", "welp", "excited", "angry", "veryhappy")
KINDS = ("thought", "summary", "human", "chat", "mood", "letter")
TONES = ("good", "bad", "neutral")
FEED_KEEP = 60

# The name bouncer. On anything a viewer can read, she is "M" -- and it is
# enforced HERE, at the door, so no prompt upstream needs a "don't say the
# name" rule. Every string the overlay will display passes through scrub()
# on its way into state, whoever posted it (that includes her own /event
# human messages, deliberately). Word-boundary match, any case, so
# possessives come out as "M's". Add spellings to NAMES if any slip past.
NAMES = ("M",)
_NAME_RES = tuple(re.compile(r"\b%s\b" % re.escape(n), re.IGNORECASE) for n in NAMES)


def scrub(s):
    for rx in _NAME_RES:
        s = rx.sub("M", s)
    return s

POLL_EVERY = 4.0    # how often the poller wakes up
POLL_AFTER = 8.0    # ...and how old the last reading must be before it asks
STALE_AFTER = 20.0  # no reading this long -> say so rather than lie

STATIC = {
    "/": ("overlay.html", "text/html; charset=utf-8"),
    "/overlay.html": ("overlay.html", "text/html; charset=utf-8"),
    "/control.html": ("control.html", "text/html; charset=utf-8"),
    "/problem-usernames.txt": ("problem-usernames.txt", "text/plain; charset=utf-8"),
}

state = {
    "goals": {"long": "", "short": ""},
    "status": {"phase": "hands", "turn": 0, "label": ""},
    "mood": "happy",
    "feed": [],
    # The colony clock. Never persisted-and-reloaded: a tick from last week is
    # worse than no tick, because it reads as current.
    "game": {"tick": None, "paused": False, "stale": True, "wallTs": None},
    # The last thing the core said about a turn, kept out of the feed so it can
    # stay on screen after the feed has scrolled past it. Survives a restart.
    "lastSummary": None,
}
lock = threading.Lock()
next_id = itertools.count(1)
_last_reading = [0.0]   # wall time of the last tick we believed, push or poll


def persist(record=None):
    """Append one record to the log and rewrite state.json. Call with the lock held."""
    if record is not None:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def record_tick(tick):
    """One reading of the colony clock, from a push or from the poller.

    Call it from anywhere EXCEPT while holding the lock, and never with the
    bridge call still in flight -- the whole point is that a slow or dead
    bridge is somebody else's five seconds, not a request handler's.

    `paused` is two identical readings in a row. A reading that goes backwards
    is a reloaded save, and the new number simply wins.
    """
    now = time.time()
    with lock:
        g = state["game"]
        prev = g["tick"]
        g["paused"] = prev is not None and tick == prev
        g["tick"] = tick
        g["stale"] = False
        g["wallTs"] = now
        snap = dict(g)
    _last_reading[0] = now
    return snap


def current_tick():
    """The last tick anyone knew, or None. Cheap enough for every event."""
    with lock:
        return state["game"]["tick"]


def _read_int(body, key):
    """(value, error). Absent -> (None, None); unusable -> (None, message)."""
    if key not in body or body[key] is None:
        return None, None
    try:
        return int(body[key]), None
    except (TypeError, ValueError):
        return None, f"{key} must be an integer"


def handle_event(body, *, screened=False):
    kind = body.get("kind")
    if kind == "chat" and not screened:
        return 403, {"error": "chat enters through the Twitch screener"}
    if kind not in KINDS:
        return 400, {"error": f"kind must be one of {list(KINDS)}"}
    mood = body.get("mood")
    if mood is not None and mood not in MOODS:
        return 400, {"error": f"mood must be one of {list(MOODS)}"}
    raw_text = body.get("text") or ""
    text = scrub(raw_text if kind == "chat" else raw_text.strip())
    if kind != "mood" and not text:
        return 400, {"error": "text is required"}
    if kind == "mood" and not mood:
        return 400, {"error": "mood events need a mood"}
    tone = body.get("tone")
    if kind == "letter":
        tone = tone or "neutral"
        if tone not in TONES:
            return 400, {"error": f"tone must be one of {list(TONES)}"}
    tick, err = _read_int(body, "tick")
    if err:
        return 400, {"error": err}
    if tick is None:
        # Not "now" in wall-clock terms -- the last in-game moment anybody
        # reported. An event that arrives before the first tick reading gets a
        # null, which the page shows as no timestamp rather than as tick zero.
        tick = current_tick()

    item = {
        "id": next(next_id),
        "ts": time.time(),
        "tick": tick,
        "kind": kind,
        "text": text,
    }
    if mood:
        item["mood"] = mood
    if kind == "chat" and body.get("name"):
        item["name"] = scrub(str(body["name"])[:40])
        item["approved"] = True
        item["source"] = "twitch"
        item["channel"] = body.get("channel", "")
    if kind == "letter":
        item["tone"] = tone
    with lock:
        # Inside the lock: `handle_status` mutates state["status"] under the
        # same lock on a ThreadingHTTPServer, so reading the turn number out
        # here raced a handback and could stamp a summary with the neighbouring
        # turn -- on screen, in the one card viewers read.
        if kind == "summary":
            item["turn"] = body.get("turn", state["status"].get("turn"))
        if mood:
            state["mood"] = mood
        if kind == "summary":
            state["lastSummary"] = {
                "text": text, "turn": item["turn"], "mood": mood,
                "ts": item["ts"], "tick": item["tick"],
            }
        state["feed"].append(item)
        del state["feed"][:-FEED_KEEP]
        persist({"type": "event", **item})
    return 200, {"ok": True, "id": item["id"]}


def publish_screened_chat(body):
    code, result = handle_event(body, screened=True)
    if code != 200:
        raise ValueError("approved chat could not be published")
    return result


def update_chat_status(value):
    with lock:
        state["chatModeration"] = value


def handle_game(body):
    """A tick push. Deliberately does not touch the disk: these arrive every
    second or two from the play loop, and state.json is not a tick log."""
    tick, err = _read_int(body, "tick")
    if err or tick is None:
        return 400, {"error": err or "tick is required"}
    return 200, {"ok": True, "game": record_tick(tick)}


def handle_goals(body):
    with lock:
        for key in ("long", "short"):
            if key in body:
                state["goals"][key] = scrub(str(body[key] or "").strip())
        persist({"type": "goals", "ts": time.time(), **state["goals"]})
    return 200, {"ok": True, "goals": state["goals"]}


def handle_status(body):
    with lock:
        st = state["status"]
        if "phase" in body:
            st["phase"] = "core" if body["phase"] == "core" else "hands"
        if "turn" in body:
            try:
                st["turn"] = int(body["turn"])
            except (TypeError, ValueError):
                return 400, {"error": "turn must be an integer"}
        if "label" in body:
            st["label"] = scrub(str(body["label"] or ""))
        persist({"type": "status", "ts": time.time(), **st})
    return 200, {"ok": True, "status": st}


def handle_reset(_body):
    with lock:
        state["feed"].clear()
        persist({"type": "reset", "ts": time.time()})
    return 200, {"ok": True}


ROUTES = {
    "/event": handle_event,
    "/goals": handle_goals,
    "/status": handle_status,
    "/game": handle_game,
    "/reset": handle_reset,
}


# --- the fallback tick poller -------------------------------------------------
#
# Everything below runs on one daemon thread and is allowed to fail forever.

def _bridge_tick(rim):
    """`ticksGame`, or None. Never raises, NEVER acknowledges an attention item.

    `rim.game()` clears a stuck GABS attention item so the play loop can carry
    on. That is exactly the wrong behaviour for a clock: the attention item is
    a real failure somebody at the keyboard needs to see, and a poller that
    silently swallows one every four seconds is a poller that hides it. So this
    goes through the un-acking layer and treats anything that isn't a dict with
    an integer `ticksGame` -- a refusal string, `{"success": false}`, a
    `no_game` status -- as simply no answer this time.
    """
    r = rim.tool("games_call_tool", {"gameId": rim.GAME,
                                     "tool": "rimworld/get_game_info",
                                     "arguments": {}})
    if isinstance(r, dict):
        t = r.get("ticksGame")
        if isinstance(t, int) and not isinstance(t, bool):
            return t
    return None


def _mark_stale():
    """Say "I don't know" rather than keep presenting an old number as now.

    The tick itself is kept: an hour-old tick with `stale: true` beside it is
    still the right thing to show, because the colony really was there.
    """
    try:
        with lock:
            if time.time() - _last_reading[0] > STALE_AFTER:
                state["game"]["stale"] = True
    except Exception:
        pass


def _poller():
    rim = None
    ready = False
    while True:
        try:
            if rim is None:
                import rim as _rim     # noqa: F401  (path was set in main())
                rim = _rim
            if not ready:
                rim.init()             # once per process, retried if it fails
                ready = True
            # Only ask if nobody pushed recently. A play session pushing every
            # loop keeps this thread off the bridge entirely, which is the
            # point: the bridge serialises calls, and a poll can sit behind a
            # long play_until_event for seconds.
            if time.time() - _last_reading[0] > POLL_AFTER:
                t = _bridge_tick(rim)
                if t is None:
                    _mark_stale()
                else:
                    record_tick(t)
        except Exception:
            # Import error, dead GABS, half a JSON body, anything at all. The
            # overlay degrades silently or it isn't worth having.
            ready = False
            _mark_stale()
        time.sleep(POLL_EVERY)


def start_poller():
    """Best effort. A failure here must not stop the server from serving."""
    try:
        if RIM_DIR not in sys.path:
            sys.path.append(RIM_DIR)
        threading.Thread(target=_poller, name="game-tick", daemon=True).start()
        return True
    except Exception:
        return False


class Handler(BaseHTTPRequestHandler):
    server_version = "errata-overlay/1.0"

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    def _send(self, code, payload, ctype="application/json; charset=utf-8"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/state":
            with lock:
                self._send(200, state)
            return
        if path in STATIC:
            name, ctype = STATIC[path]
            try:
                self._send(200, (ROOT / name).read_bytes(), ctype)
            except FileNotFoundError:
                self._send(404, {"error": f"{name} is missing next to server.py"})
            return
        self._send(404, {"error": "no such path"})

    def do_POST(self):
        # A web page on another origin must not forge human/control events.
        # Local Python clients omit Origin; browser controls use this origin.
        origin = self.headers.get("Origin")
        expected = {f"http://127.0.0.1:{self.server.server_port}",
                    f"http://localhost:{self.server.server_port}"}
        if self.headers.get("Host") not in {value.removeprefix("http://") for value in expected}:
            self._send(403, {"error": "local host required"})
            return
        if origin is not None and origin not in expected:
            self._send(403, {"error": "cross-origin writes are disabled"})
            return
        path = self.path.split("?", 1)[0]
        route = ROUTES.get(path)
        if route is None:
            self._send(404, {"error": "no such path"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send(400, {"error": "invalid content length"})
            return
        if not 0 <= length <= 16384:
            self._send(413, {"error": "request too large"})
            return
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            self._send(400, {"error": "body must be a JSON object"})
            return
        code, payload = route(body)
        self._send(code, payload)


def main():
    ap = argparse.ArgumentParser(description="errata overlay server")
    ap.add_argument("--port", type=int, default=8090)   # 8080 is GABS; see the docstring
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--chat-channel", help="Twitch login; defaults to chat-config.json")
    ap.add_argument("--no-chat", action="store_true", help="disable Twitch chat screening")
    ap.add_argument("--no-poll", action="store_true",
                    help="don't ask the game bridge for the tick; /game pushes"
                         " still work. Use it for a second copy on another port"
                         " so a test server doesn't add bridge traffic to a"
                         " live play session.")
    args = ap.parse_args()

    if STATE_PATH.exists():  # pick up goals/status/summary from last time
        try:
            saved = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            # scrub on restore too: a state.json written before the bouncer
            # existed is the one place an un-scrubbed string could still enter.
            state["goals"].update({k: scrub(v) if isinstance(v, str) else v
                                   for k, v in saved.get("goals", {}).items()})
            state["status"].update(saved.get("status", {}))
            state["status"]["label"] = scrub(str(state["status"].get("label") or ""))
            if saved.get("mood") in MOODS:
                state["mood"] = saved["mood"]
            # The feed deliberately starts empty (a restart mid-stream should
            # not replay an hour of thoughts) but the pinned summary is the one
            # thing on screen that would otherwise go blank and stay blank
            # until the next handback -- which can be twenty minutes.
            last = saved.get("lastSummary")
            if isinstance(last, dict) and (last.get("text") or "").strip():
                state["lastSummary"] = {
                    "text": scrub(last.get("text")), "turn": last.get("turn"),
                    "mood": last.get("mood") if last.get("mood") in MOODS else None,
                    "ts": last.get("ts") or time.time(),
                    "tick": last.get("tick") if isinstance(last.get("tick"), int) else None,
                }
            # `game` is NOT restored: the saved tick is from whenever the server
            # last ran, and a stale clock presented as current is worse than none.
        except (ValueError, OSError):
            pass

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    polling = False if args.no_poll else start_poller()
    chat = None
    update_chat_status(dict(status="disabled", queued=0, approved=0, rejected=0,
                            errors=0, dropped=0, model="claude-sonnet-5", channel="",
                            lastDecision=""))
    if not args.no_chat:
        try:
            config_path = ROOT / "chat-config.json"
            config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
            channel = args.chat_channel or config.get("channel")
            if channel:
                from chat_screening import ClaudeChatScreener
                from twitch_chat import TwitchChat
                from problem_users import ProblemUsers
                chat = TwitchChat(channel, ClaudeChatScreener(model="claude-sonnet-5"),
                                  publish_screened_chat, update_chat_status,
                                  problem_users=ProblemUsers(ROOT / "problem-usernames.txt")).start()
        except Exception:
            update_chat_status(dict(status="error", queued=0, approved=0, rejected=0,
                                    errors=1, model="claude-sonnet-5", channel="",
                                    lastDecision="chat setup failed"))
    print(f"overlay:  http://{args.host}:{args.port}/")
    print(f"tick:     {'polling the bridge when nobody pushes' if polling else 'pushes only (/game)'}")
    print(f"controls: http://{args.host}:{args.port}/control.html")
    print(f"log:      {LOG_PATH}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if chat:
            chat.stop()
        httpd.server_close()


if __name__ == "__main__":
    main()
