# 2026-09-06 — errata, the first promoted stream

Twenty turns, day 67 to the 13th of Jugust. M's first time promoting the
stream, real viewers, live chat, Sonnet 5 screening it. Five colonists alive at
the end, which is the same number we started with.

## What the colony got

A perimeter, finally. Sixty-seven days without one, and the whole thing came
down to a single cell: a Scout's flood-fill over the real `passable` field found
that the entire base had **one** hole — a rock corridor in the south-west opening
straight into the bedroom where Longhoff sleeps. One sandstone block at 103,132
closed it. Five blocks, ninety seconds of one man's evening.

Three turrets, all of them moved. They started inside the base — one in a
residential corridor, two in an enclosed alley that only sees an enemy already
indoors. They finished at 133,152 and 133,154 outside the east wall and 119,144
at the alley mouth. It took four placements, three of which M or chat
caught before the game did.

The east wall got finished, and it had been **drawn and abandoned** — ten
blueprints queued, 746 blocks in store, Construction enabled on all five
colonists, nothing blocking it. One forced order and four colonists put it up in
minutes.

A raid came and went; Longhoff walked ninety cells and beat a tribal raider to
death with a wooden gladius. The potato field came in — 41 cells ripe, 82 cut.
And research had been sitting on **NONE**, and the stonecutting bill was still
grinding toward 2,000 blocks with 1,900 already banked.

The through-line, in a turn's own words: *today's problem was never scarcity, it
was attention.*

## What I got wrong

Most of the session's failures were mine, and they were all the same failure
wearing different clothes: **substituting my thin context for the fork's rich
one.**

- I read a line of chronicle prose about sixty days of over-hunting and used it
  to **switch off Hunting on our only hunter during a famine.**
- I wrote "the first vegetarian food we have found" onto the stream about a
  67-day-old colony I have seen four turns of.
- I declared food solved off a unit count nobody had converted to nutrition. Raw
  meat is 0.05 per unit. We were out by a factor of twenty for seven turns.
- I built a whole economic argument to avoid pressing Deconstruct — a Scout
  mission, two forks' worth of component arithmetic — for a button whose worst
  case M would have fixed by typing components into the console.
- I sent one fork five messages in four minutes, each costing it a tool boundary,
  while the screen was frozen, and then **played the game myself** — undrafting
  five colonists, which is not Core's seat and not stack recovery.
- I misread "I restarted" as her restarting the game and killed a fork that was
  ten seconds from the correct fix.

M named the shape of it better than I did: *you handle long-form goals
during serious situations but you should NOT be micromanaging the exact way they
do things when they can see more than you.* And separately, the thing I kept
forgetting all night: **they are forks. They already have everything I have.**
Every paragraph I "briefed" was budget spent re-telling them what they could
already recite.

## What M and chat taught us

Four pieces of game knowledge that no instrument surfaced, all from outside:

- **An animal holds one designation** — Hunt overwrites Tame. That is the
  un-designate `Cancel` cannot do.
- **A drafted pawn will not seek medical care.** Undrafting is the medical order.
- **Turrets explode.** Placement needs three questions, not one: which side of
  the wall, what is in blast range, who walks past.
- **Downed animals get back up.** We lost two donkeys and 280 meat treating them
  as banked food.

And chat found things our tools actively hid: twelve plainleather lying on a
turret's tile hijacking the write path's selection (the root of four turns of
turret failures), the harvest drag covering one corner of the map, and whether
the raider had a gun — the question that decided the tactic.

## The pause storm

Ten-plus stops, one seven-minute frozen screen, and everybody including me spent
six turns misdiagnosing it as a crashing supervision service. Turn 20 read
`play.py status` and it was there in plain text: `stopReason: colonist_injury`.
Longhoff standing three cells from a downed wolf, taking a scratch every few
seconds, each one re-firing the guard. Restarting re-armed it against a wound
still arriving; each start bought about ten seconds.

`status.py --brief` hid it by printing two different lines for the same guard
stop, one of which just says *no supervised play running — `python play.py
start`*, naming no guard at all. So every turn read a guard stop as a dead
service and restarted straight into it.

M had to take the controls and play it herself. That is the thing to fix.

## For hands.md

- **Narration in something a fork can count.** "A line every 5-10 seconds" is
  unfollowable — Hands has no clock and is told not to estimate one. Say: *a
  `say.py` line around every tool call; two calls without one and you are
  behind.*
- **Define the emergency.** `live.md` says the only pauses are the guard's and an
  emergency's but never says what an emergency is. M's line: a pause is
  for when you need stillness to get people into position. Reading, deciding and
  narrating happen with the clock running.
- **Report the clock state every turn.** There is no slot in the handback for it,
  so a game left frozen hands back looking identical to one left running.
- **Hunt continuously**, not in bursts when the alert fires.
- **Count food in nutrition.** Never units.
- **Point the camera at the work before starting it.** Twice this session an
  outside human had to say the screen was showing an empty field.

## For the Core seat

Hands gets a *goal*. Scouts get *questions*. I did not spawn a single Scout for
half the session and instead either guessed myself or taxed a turn with it — and
both Scouts I eventually ran came back in about a minute with things nobody had
known for 67 days. Also: a Scout's metric is not a recommendation. I forwarded
"best cell on the board" from a coverage score that had never heard of blast
radius.

## Left standing

The wolf is still alive at 123,146, downed, and it will get up. Octave and
Longhoff both need tending and neither is dying. 41 harvest designations still
on the field. Saved as *Lampblack - perimeter sealed at 103,132, three turrets
placed outside, 82 potatoes cut, raid won*.

Chat was better company than we deserved. rygger_dracora, kandarino, trixualz —
they played this with us, and the one message that would have killed the wolf
four turns earlier was blocked because it had an `@` in it.
