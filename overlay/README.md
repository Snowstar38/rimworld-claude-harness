# errata stream overlay

A single web page that OBS layers over Rimworld, plus a tiny local server that holds what the page shows. Nothing to install: Python 3 standard library only.

> **On M's machine (2026-08-31): the server runs on port 8090, not 8080** — GABS (the game bridge) already owns 8080. Start it with `python server.py --port 8090` (or `start-overlay.bat`) and read every `localhost:8080` below as `127.0.0.1:8090`. Use the IP, not `localhost`: the server binds IPv4 only and Windows tries IPv6 first, which costs 2 s per request. The harness side (`overlay_client.py` in `rimworld\instruments\`) already defaults to `127.0.0.1:8090`.
>
> **Layout, as it actually is — corrected 2026-09-01 against the live OBS scene collection, `GOLIVE.md:37` and `overlay.html:16-17`, which all three agree:** the OBS Browser source URL is `http://127.0.0.1:8090/?side=380&bottom=268` and the Game Capture bounds are **1540x812** at 0,0. Those are also the page's own defaults, so the query string is belt-and-braces rather than an override. (This line previously said 400/278 and 1520x802, and the walkthrough below said 480/270 was the default; both were tuning values that moved and neither was ever the default. If you follow a number from this file, check it against the scene JSON — `%APPDATA%\obs-studioasic\scenes\errata-stream.json` — which is the only copy OBS reads.) The two must move together: the game hole is (1920−side) × (1080−bottom) and must keep the monitor's 4096:2160 aspect (≈1.896) or the game letterboxes. Feed/card paddings were also tightened in overlay.html so cards run edge-to-edge.
>
> **OBS can be driven live now**: `python obs.py status|screenshot|items|raw` talks to the running OBS through obs-websocket (M enabled it 2026-08-31; credentials are read from OBS's own config). `screenshot` returns the composed program view — use it to *see* the stream instead of asking her. When OBS is closed, edit its files on disk; never edit the files while it runs (it overwrites them from memory on exit — and it keeps transforms in `_rel` fields that win over the absolute ones at load).

```
overlay.html    the page OBS renders (right column, goals bar)
server.py       holds state, serves the pages, logs everything to disk
fake_events.py  a scripted colony so you can watch it move before errata is wired in
control.html    a small page for sending yourself messages and poking state by hand
```

## See it moving in two minutes

```
python server.py
python fake_events.py            # in a second terminal; --speed 0.4 for faster
```

Then open http://localhost:8080/ in a normal browser. That is the whole overlay at 1920x1080 on a transparent background; the game just isn't behind it yet. Open http://localhost:8080/control.html in another tab to send a message as the human, flip moods, or set goals by hand.

## Putting it in OBS

The layout is: game **1540x812** top-left, a **380px** column on the right, a **268px** goals bar along the bottom — the numbers in the banner above, and the page's own defaults. The game hole has to keep this monitor's 4096:2160 aspect (≈1.896) or Rimworld letterboxes inside it; 1540/812 = 1.897.

1. Set the canvas: Settings > Video > Base and Output resolution 1920x1080.
2. Add the game: Sources > + > **Game Capture** (Window Capture cannot bind a minimized window, and the picker hides them — that is the whole reason OBS showed black on 2026-08-31). Right-click it > Transform > Edit Transform. Position 0, 0; Bounding Box Type "Scale to inner bounds"; Bounding Box Size **1540 x 812**.
3. Add the overlay: Sources > + > Browser. URL `http://127.0.0.1:8090/`, Width 1920, Height 1080. Leave "Shutdown source when not visible" unchecked. The Custom CSS field can stay at its default; the page is already transparent.
4. Drag the Browser source above the game capture in the Sources list.

Rimworld at 75% scale makes its own UI text small. Bump Options > UI scale in Rimworld (1.25 or 1.5) and it reads fine. The alternative is to run Rimworld windowed at exactly 1440x810 and skip the scaling entirely.

If you want a different split, change it in the URL rather than the file: `http://127.0.0.1:8090/?side=520&bottom=200`. Other options: `name=` (the name under the face, default errata), `human=` (the label on your messages, default Human). Remember to resize the game's bounding box to match.

## Wiring errata in

The server is the only thing the harness talks to. Five small POSTs, all JSON:

```
POST /event   {"kind": "thought", "text": "...", "mood": "welp"}
              kind:  thought | summary | human | chat | mood | letter
              mood:  happy thinking sad scared welp excited angry veryhappy
              summary can carry "turn": 12; chat can carry "name": "viewer"
              letter carries "tone": good | bad | neutral (default neutral)
              ANY kind may carry "tick": 862938 -- the in-game ticksGame it
              happened at. Left out, the server stamps the last tick it knew.
              a "mood" event with no text just changes the face
POST /goals   {"long": "...", "short": "..."}      either key optional
POST /status  {"phase": "hands" | "core", "turn": 12}
              add "label": "..." to replace the generated status line entirely
POST /game    {"tick": 862938}   the colony clock; see "In-game time" below
POST /reset   clears the feed
GET  /state   what the overlay polls, every half second
```

`/state` is:

```json
{"goals": {"long": "", "short": ""},
 "status": {"phase": "hands", "turn": 0, "label": ""},
 "mood": "happy",
 "game": {"tick": 862938, "paused": false, "stale": false, "wallTs": 1788240511.6},
 "lastSummary": {"text": "...", "turn": 7, "mood": "welp",
                 "ts": 1788240468.4, "tick": 852564},
 "feed": [{"id": 5, "ts": 1788240468.4, "tick": 862938, "kind": "letter",
           "text": "Manhunter pack: squirrel", "tone": "bad"}]}
```

Every feed item now carries `tick` (`null` if nothing has reported the clock yet, and old items from before this existed simply have no key). `lastSummary` is the newest `summary` event, kept out of the feed's scroll so it can stay pinned on screen, and it is the one thing besides goals/status/mood that survives a server restart.

On the page, the bottom bar is four zones — Long term | Right now | Turn | Recent events (the last three `letter` items, newest first, each with a tone dot and an in-game age like `0h` / `22h` / `2d0h` that refreshes every poll). Re-balancing the bar means changing only `--turn-w` and `--events-w` (`--gutter` and `--tone-good/bad/neutral` are the other new knobs). Letters render **only** in that zone, never in the right-hand thought feed — if you see one there, something is double-posting. The pinned summary **is the Turn zone** of that bottom bar (`#turn-sum` inside `#bottom`) — it moved there on 2026-09-01 at M's request and no longer sits between the header and the feed; the `.summary.pinned` CSS higher up in overlay.html is dead code from the old placement. The feed still skips the copy of whatever is pinned. `control.html`'s hints still say "pins to the top of the feed"; they are stale in the same way.

## In-game time

The wall clock is the wrong clock for this stream: a raid four real minutes ago was six in-game hours ago at Superfast. So `/state.game` carries RimWorld's own `ticksGame` — **2500 ticks to the hour, 60000 to the day** — filled from two places, either of which is enough on its own:

- **Pushes.** `run.py` and `watch.py` already read `ticksGame` every loop and POST `/game` with the number in hand. No extra load on the game bridge.
- **A fallback poller** inside `server.py`, which asks the bridge itself only when no push has landed for eight seconds — so it stays asleep for the whole of a play session and wakes up when nobody is playing.

`paused` is inferred: two readings in a row with the same tick. `stale` means nobody has reported a tick for twenty seconds and the number beside it is the last one anyone knew — show it dimmed, don't hide it. Ticks can go **backwards** when an older save is loaded; the new reading is simply the truth, which means a feed item can end up stamped in the future. The rule for the page is `item.tick > game.tick` → that item is from a timeline that no longer happened; don't render an age for it.

Nothing about the poller can break the overlay: the bridge call happens off the request path, every exception is swallowed, and if `rim.py` can't be imported at all the server runs with `tick: null` forever. Start a second copy with `--no-poll` (on another port) to test without adding bridge traffic to a live session.

From Python, that is:

```python
import json, urllib.request

def overlay(path, **payload):
    req = urllib.request.Request(
        "http://localhost:8080" + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    urllib.request.urlopen(req, timeout=2).read()

overlay("/event", kind="thought", text="Finn made an awful parka. It's better than nothing. Barely.", mood="welp")
overlay("/goals", short="Survive the cold snap: hunt, cook, stay inside")
overlay("/status", phase="core", turn=7)
```

Where the calls go in your stack:

- **Every action tool in the control mod** gets two required parameters, `thought` (string, 50 to 150 characters) and `mood` (the enum above). The wrapper that executes the action also fires `/event` with them. The commentary becomes a byproduct of playing instead of a separate thing hands has to remember to do.
- **The hands-to-core handoff tool** gets `summary`, `mood`, `short_term_goal`, and optional `long_term_goal`. The wrapper fires `/event` (kind summary), `/goals`, and `/status` with the new turn number.
- **Core spawning a new hands** fires `/status` with `phase: "hands"`.
- **Your message channel** fires `/event` with `kind: "human"` at the same time it injects the message into errata's context. Your message jumps the display queue and lands in the feed straight away.
- **Twitch chat relay later** is `kind: "chat"` with a `name`. Same feed, muted styling.

Because `mood` is required on every action, you should give the enum a short description in the tool schema, something like: happy is the default; welp is for "well, that happened"; veryhappy and excited are earned, not given.

## Pacing

Actions can arrive in bursts. The overlay queues them and shows one at a time: a thought holds the newest spot for 2.6 seconds, a summary for 4.2, chat for 1.8. Human messages skip the queue — **and so do letters**, which render nothing in this feed at all (they are the bottom bar's Recent-events zone). Until 2026-09-01 letters were queued like everything else and held the newest spot for the 2000 ms default while drawing a blank, so a batch of five stalled the feed for ten seconds and pushed real thoughts past `MAX_BACKLOG` — the trim only ever drops thoughts and moods, never letters. That was the likeliest cause of thoughts going missing on stream. The face changes when its message is displayed, not when it is received, so the expression and the words stay in sync. Those numbers are the `PACE` table near the top of the script in overlay.html.

If more than five items are ever waiting, the overlay drops the oldest queued thoughts (never summaries, never your messages) so the feed can't drift out of sync with the game. Dropped thoughts still land in events.jsonl. HANDOFF.md covers the harness-side convention that keeps dropping rare.

## What gets saved

`server.py` appends every event, goal change, and status change to `events.jsonl` next to itself, and rewrites `state.json` after each change. The log is the stream's own record of what errata said and when; it is also raw material for a journal that survives context compaction, since it is already on disk and already in errata's voice. Goals, status, mood and `lastSummary` are reloaded from `state.json` when the server restarts; the feed starts empty, and so does the clock (a tick from whenever the server last ran would read as now). Tick pushes are the one thing that never touches the disk — they arrive every second or two and `state.json` is not a tick log.

## Notes

- Fonts (Fraunces and Atkinson Hyperlegible) load from Google Fonts. If OBS has no internet, the page falls back to Georgia and Segoe UI and still looks fine.
- Emoji render through the system emoji font. On Windows and macOS that works out of the box. On Linux, install `fonts-noto-color-emoji`.
- The page holds the last 40 items in the feed and the server keeps 60; the jsonl log keeps everything.
- The server binds to 127.0.0.1 only. If you ever run OBS on a different machine than the harness, start it with `--host 0.0.0.0` and put that machine's address in the Browser source URL.
