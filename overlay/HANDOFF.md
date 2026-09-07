# Handoff: wiring errata's harness into the stream overlay

To whoever's implementing this on M's machine — probably another Claude. The overlay and its server are already built and tested; this document is the half you own: making the core/hands stack emit stream events as a byproduct of playing. Nothing here changes how errata plays Rimworld. It changes what the tool schemas ask for and adds a handful of fire-and-forget HTTP POSTs to the wrappers that already execute game actions.

## What already exists

> **Read this document as INTENT, not as description — note added 2026-09-01.** It is a design doc that was never updated after the thing was built. Two of its facts are now wrong everywhere they appear: the server runs on **8090** (8080 is GABS, the game bridge, on this machine), and the address is **`127.0.0.1`**, never `localhost` — the server is IPv4-only, Windows tries ::1 first, and the failed attempt costs **2.006s per POST against 0.001s** (measured; `overlay_client.py:38-45`). The paste-ready helper below hardcodes `localhost:8080` and is therefore a trap; the real client is `rimworld\instruments\overlay_client.py`, which is what got built and which already defaults correctly. Its six-item wishlist is likewise a plan, and `todo-docs.md` / `BUGS.md` record which parts of it shipped.

`server.py` (standard library, runs on 127.0.0.1:8090) holds overlay state and logs everything to `events.jsonl`. The overlay page polls it. Your side only ever POSTs to it:

    POST /event   {"kind": "thought", "text": "...", "mood": "welp"}
                  kind:  thought | summary | human | chat | mood | letter
                  summary may carry "turn": int; chat may carry "name": str
                  letter may carry "tone": good | bad | neutral (default neutral)
                  ANY kind may carry "tick": int -- the in-game moment
                  a "mood" event with no text just changes the face
    POST /goals   {"long": "...", "short": "..."}     either key optional
    POST /status  {"phase": "hands" | "core", "turn": int}
    POST /game    {"tick": int}     the colony clock, pushed from the play loop
    POST /reset   clears the feed

Moods, exactly these strings: `happy thinking sad scared welp excited angry veryhappy`

The server validates kind, mood, tone and tick and returns 400 with a reason if it doesn't like something. Text is required for everything except `mood` events.

`GET /state` carries two keys beyond goals/status/mood/feed:

    "game": {"tick": int|null, "paused": bool, "stale": bool, "wallTs": float|null}
    "lastSummary": {"text": str, "turn": int, "mood": str|null,
                    "ts": float, "tick": int|null} | null

and every feed item carries `"tick": int|null`. See README.md's "In-game time" for the poller, the pause inference, and what a backwards tick means.

## Two channels this document didn't originally have (2026-09-01)

**Letters.** RimWorld's letter stack is the only place the *game* says in its own words that something happened, and for the first month it went to the harness's stdout and nowhere else — the overlay showed errata's commentary with no sight of the events being commented on. `letters.py:post_new_to_overlay()` now posts each new letter's LABEL (never its body: three paragraphs, one card) as `kind: "letter"`, with its `arrivalTick` and a tone derived from the `letterDef`. `watch.py` and `run.py` call it where they already read the stack, **before** `watch.py` right-clicks announcements away. Deduped on disk in `rimworld\instruments\state\overlay-letters.json`, keyed by (id, arrivalTick), so a decision that sits unanswered for six steps is narrated once. Nothing hand-posts letters; a hands session that also posts one is double-posting.

**The clock.** `run.py` and `watch.py` push `/game` with the `ticksGame` they have already read. That is why `game.tick` moves during a session without the server's poller ever touching the bridge.

## The core principle

Narration is a byproduct of acting, not a separate task. Hands should never have to remember to "also post to the stream." Instead:

1. **Every game-action tool gets two new parameters.**
   - `thought`: string, required. 50–150 characters, first person, present tense. The empty string is allowed and means "no stream message for this call" — see the rate section for when to use it.
   - `mood`: enum, required, one of the eight strings above.

   The wrapper that executes the action also POSTs `{"kind": "thought", "text": thought, "mood": mood}` — skipping the POST when `thought` is empty. Post once per model-issued call, after the call is accepted, and never again on internal retries.

2. **The hands → core handoff tool gets:**
   - `summary`: string, required, aim 220–300 characters. What the turn accomplished and what it means. 320 is what the card holds; an overrun is the harness's problem, not the model's — see "the turn summary" below.
   - `mood`: enum, required.
   - `short_term_goal`: string, required. This names the next session.
   - `long_term_goal`: string, optional. Only when it actually changes.

   The wrapper POSTs `/status {"phase": "core", "turn": N}` when hands ends, then the summary as `{"kind": "summary", "text": ..., "mood": ..., "turn": N}`, then `/goals` with whatever goal keys were provided.

3. **Core spawning a new hands**: the wrapper POSTs `/status {"phase": "hands", "turn": N+1}`. The harness owns the turn counter — never ask the model to track it.

4. **If a raid or similar lands mid-turn**, hands needs a way to update the board: either a small `update_goal(short_term_goal, mood)` tool, or optional `short_term_goal` on whichever tool responds to events. Wrapper POSTs `/goals` and, if a mood came with it, a `mood` event.

5. **M's message channel**: whenever a message from her is injected into errata's context, the same code POSTs `{"kind": "human", "text": ...}`. One POST per injection, at injection time — her messages skip the display queue and render in the feed immediately, so timing matters more here than elsewhere.

6. **Twitch chat, later**: `{"kind": "chat", "name": viewer, "text": ...}`. Muted styling. Don't borrow `thought` for it — the feed's default register is errata's voice, and anything that isn't errata should be marked as such. Same rule if the scouts/lookouts ever need to say something on stream: give them their own kind then, don't have them speak as errata.

## Rate: known concern, current answer

Hands can fire several tool calls in a couple of seconds. Two mechanisms already absorb this, and one convention prevents it:

- The overlay releases items no faster than one per ~2.6 s, so bursts display readably.
- If more than 5 items are ever waiting, the overlay silently drops the oldest queued thoughts (never summaries, never human messages) so the feed can't drift out of sync with the game. A dropped thought still exists in `events.jsonl` — nothing is lost, it just doesn't render.

The convention, which belongs in the tool descriptions so the model applies it itself: **narrate the decision, not every keystroke of it.** When several calls in a row serve one intention — queueing twenty wall segments, assigning three colonists to the same job — put the thought on the first call and pass `""` on the rest. A sustained pace of more than one thought every 3–4 seconds means messages are being dropped on the floor; that's the signal to batch harder, not to speed up the overlay.

Don't build server-side throttling or coalescing yet. M wants to see real volume first; the drop-oldest cap makes the worst case cosmetic rather than broken. If real play shows chronic dropping, the fix is in the tool descriptions before it's in the plumbing.

## Never let the stream touch the game

Every POST to the overlay is fire-and-forget: 2-second timeout, all exceptions swallowed and logged locally, no retries. If the server is down, errata plays Rimworld and nobody narrates. A helper like this, used everywhere:

```python
import json, threading, urllib.request

def overlay(path, **payload):
    def _send():
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:8090" + path,   # NOT localhost:8080 -- see the note above
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=2).read()
        except Exception as e:
            print(f"[overlay] dropped {path}: {e}")
    threading.Thread(target=_send, daemon=True).start()
```

## Voice and moods — paste-ready prompt material

For core/hands' system prompt, adapt freely:

> Your `thought` on each action appears live on stream beside the game. 50–150 characters, first person, present tense, dry and direct. Say what happened and what you make of it; no preamble, no hedging. The wit comes from the situation, not from reaching for a joke.
>
> Instead of "Finn finished crafting the parka. It is of awful quality": "Finn made an awful parka. It's better than nothing. Barely." Instead of "A raid consisting of three tribal warriors has arrived": "Raid. Three tribals from the east, and my defenses are one wall and a sense of optimism." Instead of "I will now prioritize hunting due to the food shortage": "Switching Mara to hunting. The muffalo don't know it yet."
>
> `mood` sets your face on stream: **happy** is your resting state; **thinking** while weighing options; **welp** is deadpan "well, that happened"; **scared** for real danger; **angry** for indignities (mad squirrels, raider audacity); **sad** for losses and injuries; **excited** for good surprises; **veryhappy** is earned, not given. Let the face move a step at a time unless the event genuinely deserves the jump.
>
> When several calls in a row carry out one decision, narrate the first and pass an empty thought on the rest.

The turn summary is the same voice with more room: aim for 220–300 characters. What the turn did and what it means, ending somewhere a viewer feels the shape of the story ("Confidence: rising.").

**320 is the card's constraint, not the writer's** (added 2026-09-01). The box in the bottom bar is fixed, so an overrun is cut off rather than shrunk — but the harness no longer makes errata count. `stream.py handback` posts the full summary immediately and, when it is over 320, spawns `C:\Home\rimworld\instruments\luna.py` detached: Luna asks Codex for a version that fits, then re-posts it with the same turn, mood and tick so the pin swaps a few seconds later. Nothing waits on it, and every failure path leaves the full summary and its ellipsis alone. So 320 belongs in *this* file and in `stream.py`, where Luna reads it from — it is not a rule to hand the model.

## Smoke test before going near OBS

1. `python server.py`, then confirm: `curl -s 127.0.0.1:8090/state` returns JSON.
2. Send one of each through your wrapper (not raw curl — the point is testing the wrapper): a thought, a status flip to core and back, a summary with goals, a human message. Watch them land on http://127.0.0.1:8090/.
3. Kill the server mid-turn and confirm gameplay continues without a stutter and the harness logs dropped posts.
4. Fire 10 thoughts in 2 seconds and confirm the game never waited on them, the overlay stayed readable, and `events.jsonl` has all 10.

That last check is the contract in one line: the log is complete, the screen is curated, the game never blocks.

## Editing these files

Comments state what a value must satisfy, in as few lines as it takes — never the story of the session that changed it, who asked, or what the old value was. When a change needs recording, record it as an objective note of what was added or changed.

Don't verify visual changes with screenshots or headless renders. Make the change, then have M look at the live overlay and say whether it's right — `python "C:\Home\tools\ask.py" "question" --from <you>` blocks until she answers. Her eyes are the cheap, accurate instrument here.
