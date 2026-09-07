#!/usr/bin/env python3
"""
Feeds the overlay a scripted colony so you can see it move in OBS before
errata is wired in. Standard library only.

    python fake_events.py               # ~90 seconds per turn
    python fake_events.py --speed 0.4   # faster: waits multiplied by 0.4
    python fake_events.py --url http://127.0.0.1:8091

Ctrl+C to stop. It loops forever.

It also drives the in-game clock: every beat POSTs /game with a tick a few
in-game minutes further on (2500 ticks = 1 hour, 60000 = 1 day), letters arrive
as `kind: "letter"` events carrying their own arrival tick and a tone, and each
turn's summary populates the pinned `lastSummary`. Two beats a turn push the
SAME tick twice on purpose -- that is what makes `game.paused` flip true, and
it is the only way to see the paused state without a real game.

**The default URL used to be localhost:8080, which is GABS on this machine**
(and `localhost` costs 2s a request against an IPv4-only server). It is
127.0.0.1:8090 now, the live overlay. Point it at 8091 when you are testing a
server you started yourself and don't want to scribble on the stream.
"""
import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request

# The colony clock. Starts on day 12 at dawn, because a fake stream that starts
# on day 0 hour 0 is indistinguishable from a broken clock.
START_TICK = 12 * 60000 + 6 * 2500
HOUR = 2500

# A "turn" is what one hands session does before passing back to core.
# Each entry is (mood, text) for a thought, or a dict for anything else:
#   {"kind": "burst"}    the next thought lands fast
#   {"kind": "goals", ...} / {"kind": "human"|"chat", ...}
#   {"kind": "letter", "text": "Raid", "tone": "bad"}   a RimWorld letter
#   {"kind": "pause"}    push the same tick again -> game.paused goes true
TURNS = [
    {
        "goals": {"long": "Get everyone under a roof before the first cold snap",
                  "short": "Dig in against the cliff and plant rice"},
        "thoughts": [
            ("thinking", "Three colonists, one rifle, and a map that's 60% mountain. Fine. We build into the rock."),
            ("happy", "Marked a bedroom block against the cliff. Mountains don't need walls on three sides."),
            ("thinking", "Mara has a burning passion for cooking and zero skill. Enthusiasm noted; pemmican postponed."),
            ("welp", "Finn is 'incapable of intellectual.' He will be hauling. Forever."),
            ("happy", "Bao's on research. First target: batteries, because darkness is a choice."),
            ("veryhappy", "Found a steel deposit twelve tiles from the door. Randy is being nice. Suspicious."),
            ("thinking", "Rice fields go south of the cliff. Rich soil, no shade, close enough to run home."),
            ("happy", "Set the dog to follow Finn. Someone should."),
        ],
        "summary": ("happy", "Settled against the cliff with bedrooms and a rice field marked out, and Bao researching "
                             "batteries. Finn hauls, Mara pretends to cook, the dog supervises. No threats yet, which "
                             "is the most threatening part."),
    },
    {
        "goals": {"short": "Winter prep: parkas, stockpile, tailoring bench"},
        "thoughts": [
            ("happy", "Winter's four days out. Tailoring bench first, everything else second."),
            ("welp", "Finn made an awful parka. It's better than nothing. Barely."),
            ("thinking", "Devilstrand takes forever to grow. Planting it anyway; future me will say thanks."),
            {"kind": "letter", "text": "Manhunter pack: squirrel", "tone": "bad"},
            ("angry", "A mad squirrel bit Bao. Bao was researching. The squirrel had no right."),
            ("veryhappy", "Squirrel handled. Bao has a scar and a story."),
            ("happy", "Meals stockpiled: 14. That's three days if nobody gets ambitious."),
            {"kind": "letter", "text": "Cold snap", "tone": "bad"},
            {"kind": "pause"},
            {"kind": "burst"},
            ("scared", "Cold snap. It's -18 and the rice is dead."),
            ("welp", "Well. There goes the rice."),
            {"kind": "goals", "short": "Survive the cold snap: hunt, cook, stay inside"},
            ("thinking", "Switching Mara to hunting. The muffalo don't know it yet."),
            ("excited", "Mara hit the muffalo. First shot. Cooking passion, hunting talent. Life is strange."),
        ],
        "summary": ("welp", "Winter came early and ate the rice. Parkas exist, loosely. Mara turns out to be a hunter, "
                            "so we're eating muffalo instead of nothing. Squirrel incident contained."),
    },
    {
        "goals": {"short": "Wall off the cliff gap, keep researching"},
        "thoughts": [
            ("thinking", "Batteries done. Bao moves to microelectronics so we can stop guessing what's coming."),
            ("happy", "Built a stone wall across the cliff gap. One door, one chokepoint, one very confused raider someday."),
            {"kind": "letter", "text": "Raid: tribal warband", "tone": "bad"},
            {"kind": "pause"},
            {"kind": "burst"},
            ("scared", "Raid. Three tribals from the east, and my defenses are one wall and a sense of optimism."),
            {"kind": "goals", "short": "Defend the door. Everyone drafted."},
            ("angry", "Drafting everyone. Finn gets the rifle because he's the only one who won't drop it."),
            ("thinking", "Positioning behind the wall. Let them come to the door. Doors are where the shooting is best."),
            ("scared", "Mara's down. Not dead. Down. Odds are fine. Odds are FINE."),
            ("veryhappy", "Two raiders fled, one's bleeding in my doorway. That's a rescue, not a prisoner. Probably."),
            ("sad", "Mara's leg is a problem. Bao's doing surgery with Medicine 4 and good intentions."),
            ("excited", "Surgery worked. Bao has no idea how. Neither do I."),
            ("happy", "Everyone's alive. Wall gets a second layer tomorrow."),
        ],
        "summary": ("veryhappy", "First raid, and the wall-plus-door plan held. Mara took a leg hit and Bao fixed it on "
                                 "vibes. One captured raider named Tuk is recovering and may or may not become a "
                                 "colonist. Confidence: rising."),
        "goals_after": {"long": "Turn the cliff into a proper mountain base with a killbox",
                        "short": "Patch up Mara, recruit Tuk, thicken the wall"},
    },
    {
        "thoughts": [
            {"kind": "human", "text": "Tuk needs a bedroom with a door or he'll just walk out."},
            ("happy", "Fair. Tuk gets a real cell with a real door. Hospitality is a wall."),
            ("thinking", "Tuk's a decent shooter and hates the cold. Recruit chance 6%. This will take a while."),
            ("happy", "Hydroponics next. Rice that can't die of weather is rice I can trust."),
            {"kind": "letter", "text": "Solar flare", "tone": "neutral"},
            ("welp", "Solar flare. Everything electric is off. Bao is staring at a dark microscope."),
            {"kind": "chat", "name": "muffalo_enjoyer", "text": "is finn ok"},
            ("happy", "Finn is fine. Finn is always fine. Flare's over; Bao resumed staring at a lit microscope."),
            {"kind": "letter", "text": "Wanderer joins", "tone": "good"},
            ("excited", "Wanderer joins: Odette, level 11 construction. The mountain is about to get architecture."),
            ("thinking", "Odette gets the killbox. A gentle S-curve so raiders walk slow and we shoot fast."),
            ("happy", "Biscuit the dog has learned 'rescue.' Best colonist. No notes."),
        ],
        "summary": ("excited", "A reminder from the human that prisoners like doors, so Tuk has a cell now. Odette "
                               "wandered in with real construction skill and went straight onto killbox duty. "
                               "Hydroponics started. The dog is carrying its weight."),
        "goals_after": {"short": "Killbox and hydroponics, keep working on Tuk"},
    },
    {
        "thoughts": [
            ("thinking", "A thrumbo wandered in. It's worth a fortune and it will end us. Leaving it alone."),
            ("welp", "Finn is hauling toward the thrumbo. Finn, no."),
            {"kind": "burst"},
            ("scared", "Finn is now near the thrumbo."),
            ("happy", "Finn walked past the thrumbo. Nothing happened. I aged a year."),
            {"kind": "letter", "text": "Prisoner recruited: Tuk", "tone": "good"},
            ("excited", "Tuk joined. The 6% came through. Randy giveth."),
            ("thinking", "Kitchen needs a freezer before summer. Coolers, double walls, no windows for once."),
            ("veryhappy", "First proper meal in days: fine meals, muffalo, rice. Mood's up across the board."),
            ("happy", "Colony wealth 24k. Raids will scale. So will the killbox."),
        ],
        "summary": ("happy", "Thrumbo respected, Tuk recruited, freezer planned. Five colonists, one dog, zero regrets. "
                             "Next: making the mountain look like we meant it."),
        "goals_after": {"long": "A fully enclosed mountain base: freezer, hospital, and a killbox that scales",
                        "short": "Freezer before summer"},
    },
]


def post(url, path, payload):
    req = urllib.request.Request(
        url + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.URLError as e:
        print(f"could not reach {url} ({e.reason}). Is server.py running?", file=sys.stderr)
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8090")
    ap.add_argument("--speed", type=float, default=1.0, help="multiplies every wait; 0.5 = twice as fast")
    args = ap.parse_args()
    url = args.url.rstrip("/")

    def nap(seconds):
        time.sleep(max(0.05, seconds * args.speed))

    clock = [START_TICK]

    def tick(minutes=None):
        """Advance the colony clock and push it. `minutes=0` re-pushes the same
        tick, which is how the server concludes the game is paused."""
        if minutes:
            clock[0] += int(HOUR * minutes / 60.0)
        post(url, "/game", {"tick": clock[0]})
        return clock[0]

    post(url, "/reset", {})
    post(url, "/goals", {"long": "", "short": ""})
    post(url, "/status", {"phase": "hands", "turn": 0})
    tick()
    nap(2)

    turn = 0
    while True:
        for script in TURNS:
            turn += 1
            post(url, "/status", {"phase": "hands", "turn": turn})
            if "goals" in script:
                post(url, "/goals", script["goals"])
            print(f"turn {turn}: hands playing")

            burst = False
            for step in script["thoughts"]:
                if isinstance(step, dict):
                    if step["kind"] == "burst":
                        burst = True            # next two thoughts land fast
                    elif step["kind"] == "pause":
                        # Same tick twice: the game stopped. RimWorld really
                        # does this, on exactly the letters above.
                        tick(0)
                        print(f"  [paused at tick {clock[0]}]")
                        nap(3)
                    elif step["kind"] == "letter":
                        at = tick(random.randint(10, 40))
                        post(url, "/event", {"kind": "letter", "text": step["text"],
                                             "tone": step.get("tone", "neutral"),
                                             "tick": at})
                        print(f"  [letter/{step.get('tone','neutral')}] {step['text']}  (tick {at})")
                        nap(4)
                    elif step["kind"] == "goals":
                        post(url, "/goals", {k: v for k, v in step.items() if k != "kind"})
                    elif step["kind"] in ("human", "chat"):
                        post(url, "/event", step)
                        print(f"  [{step['kind']}] {step['text']}")
                        nap(6)
                    continue
                mood, text = step
                tick(random.randint(20, 90))
                post(url, "/event", {"kind": "thought", "text": text, "mood": mood})
                print(f"  {mood:>9}  {text}")
                if burst:
                    nap(0.8)
                    burst = False
                else:
                    nap(random.uniform(4, 9))

            # hands passes back to core
            post(url, "/status", {"phase": "core", "turn": turn})
            print(f"turn {turn}: core summarizing")
            nap(7)
            mood, text = script["summary"]
            post(url, "/event", {"kind": "summary", "text": text, "mood": mood, "turn": turn})
            if "goals_after" in script:
                post(url, "/goals", script["goals_after"])
            nap(5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
