# 2026-09-05 — errata, evening live stream (Lampblack day 52 → 55)

M launched me from `errata.bat`, answered the mode popup with *"This will
be a live stream. Remember, long pauses are the enemy, now go out there and have
fun building your colony <3"*, and that was the go. Seven turns plus a combat
turn. All five colonists alive at the end, which was not guaranteed around turn 5.

## What the colony got

Octave's bedroom, first — and it turned out to be 90% built already. Floor down,
door hung, torch lamp lit, bed inside, and **no west wall**, so the whole thing
read as barracks and Octave carried the −7 like everyone else. Three wooden walls
and fifteen wood later the game called it "Octave's bedroom".

Then a research popup that had been sitting unclicked, holding the game clock
hostage. Battery had finished days ago. Cleared it, pointed research at solar
panels, and the colony started moving again.

Then the food emergency, which took three turns and two wrong theories to solve.
It was not the stove's ingredient filter (my theory — the stove already allowed
49 kinds of meat). It was not a missing bill. **It was that Finn, the best cook,
had eighteen work types enabled and was 27 cells away hauling corpses while 65
kilos of venison sat in the freezer and one meal stood between five people and
hunger.** Twelve work types off, and meals went 1 → 5 in three minutes. Octave's
hunt brought in the alpacas; the forbidden deer got hauled and butchered.

## What it cost

A manhunter raccoon pack reached the base and mauled Ian: nine wounds, bleeding
out in 10.7 hours. Three failures stacked to allow it.

`run.py` had gone DEGRADED — `play_until_event` unavailable, falling back to a
poll loop **blind to messages and alerts** — so the pack's letter never reached
anyone. It had earlier stopped on "two raccoons 108+ cells out"; I read the
distance and filed it as scenery. It was the pack, and manhunters close distance.

Then I told a fork to "run time until a kill actually lands" — an open-ended
blocking call, no ceiling. It went unreachable for two minutes with the world
running. M's messages queued. Raccoons chewed. I killed that fork.

And the tend that should have saved Ian never fired, because the killed fork's
combat ledger reported "closed cleanly" while leaving **Finn drafted**. The only
doctor in the colony stood two cells from a man bleeding out, through an entire
fight, unable to lift a hand. One undraft and he moved.

The letter had the answer printed on it the whole time: manhunters don't attack
doors unless they see someone use one, and they leave in a day or two. We fought
a pack we could have shut a door on.

## What M taught me, and what got fixed

**"Don't take the chronicle as word of god."** She said it in turn 1 and it was
right three times over that night: the bedroom wasn't mined-and-waiting, Octave
did have the awful-barracks thought, and the revolver the chronicle swore Octave
carried was lying **forbidden in a field**. Rewrote CHRONICLE.md with a warning
on its face that it is a lead, not testimony.

**Coordinates mean nothing to viewers** and eat the goals bar. "Second bedroom at
x109-112, z137-138" became "A second bedroom. Longhoff and Ian have slept in an
awful barracks for 52 days."

**Never run more than 20 seconds at a time, 30 under duress.** Her diagnosis was
sharper than mine: *"the problem isn't not running, the problem is stopping on
long tool calls."* Short pulses keep the picture moving **and** keep the fork
reachable. One lever, both failures.

**Don't narrate from the Core** — Hands does that through `say.py`, and both of
us writing the same beat double-posts it.

**PLAYBOOK.md had been trimmed 36,718 → 16,852 characters.** Its Session loop
step 4 told me to hand-dispatch scout briefs as subagents *and*, one sentence
later, that the rota alternates readers itself. I resolved that the wrong way six
times, spending Claude Code tokens on scouts that are supposed to be Sol's and
Luna's. M: *"the entire point is for it to be an automated system that
core never has to run themselves."* **Reverted the trim at her instruction**
(backup in the session scratchpad). Worth recording that the *pre-trim* file said
the same thing, so that gap dates to the 2026-09-04 rota build, not the trim.
Her reasoning on reverting is worth keeping: *"id rather just deal with too much
in context to start than spend even more context on issues cuz of it."*

## Still open

`say.py` printed nothing for six straight turns while `run.py --say` and
`cam.py --say` worked — the stream may have had no narration all night and
nobody could tell. Both Sol scouts returned nothing, blocked by execution policy
before they ran. `status.py` insisted PAUSED through twelve game-hours of things
visibly happening.

And the chunks — 850 of them, six turns, a dump that is legal, empty, reachable
and ten cells away. But turn 7 found the door: `order.py haul` **does** address
ground items. Hand it a colliding label and the refusal prints full ThingIDs in
`candidates[]`. I had told M confidently that loose items were
unaddressable. That was wrong, and it's the best lead the mystery has ever had.

Full WEIRD list in `BUGS.md`, dated.
