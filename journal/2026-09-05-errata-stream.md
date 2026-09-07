# 2026-09-05 — errata, the first full live stream

Twenty-one turns, Lampblack day 41 → 52. M at the keyboard the whole way,
viewers in chat, and the colony ended the night with five people alive, indoors,
and asleep. That is the headline, and it is not the interesting part.

## What the night was actually about

Nothing in Lampblack was broken. Not once, all night.

The stove had no bill on it. The stonecutter had been built and never told to cut.
The cut-forever bill nobody capped filled the storeroom until hauling stopped
colony-wide. Longhoff owned a *sleeping spot* — bare floor with his name on it —
and had been handed everyone else's daylight schedule while being a Night owl,
which was −10 all by itself. The freezer had never been set below its factory
21 °C and had no power to reach it anyway, because a Zzztt had blown four cells
out of the conduit line and nobody repaired it; only winter was keeping the food,
and winter ended mid-session. Two blades lay forbidden on our own floor while
three of five colonists walked around unarmed.

Every single bottleneck was a thing nobody had ever told the colony to do. The
materials were always there — 1,677 wood, 894 stone chunks, 750 blocks. Lampblack
was never poor. It was unattended.

## The floor, and being wrong five times

M asked for a kitchen floor four times. Across turns 8, 9 and 10 three
different forks placed 45 tiles, verified `success: true, 0 refused`, and watched
nothing happen. We theorised: wrong stone, no dump zone, no haul destination,
everybody asleep, a sealed room. I was proud of the sealed-room one.

`home/place_building` defaults to `dryRun: true`. Forty-five tiles, three turns,
every call politely rehearsing.

The lesson worth keeping is not "read the defaults". It is that **the errors were
never in the acting, they were in the reading.** Every real mistake tonight was
confident inference over a picture that was wrong: a cold room read as a cold
emergency when the frostbite was already old; butcher bills read as misconfigured
when there was simply nothing to butcher; Ian read as bedless when he had a bed;
a dump read as corpses when M had said stockpile. The instruments were
right every time. The narrator wasn't.

## What M and chat did

Most of the night's real progress came from the chair. She caught the dump inside
the kitchen, the dark kitchen (which cost Ian −5 and had a work-speed penalty
under it), the runaway stonecutter, the storeroom stuffed full, and the freezer —
the freezer being the big one, where both of her suspicions were true at once.
She stopped me partitioning a bad room into smaller bad rooms and said dig, and
she was right: the mountain gives three walls free and Octave's bedroom went up
inside one run of time.

And she solved a megasloth with a door. A fork was one order from throwing two
colonists at 350+ hit points of animal to save a bleeding medic — the exact shape
of the fight that killed two people in the run we discarded the night before.
She said lock the doors and wait. It bled out behind them from the four rounds
Octave had already put in it. Nobody swung anything.

Chat got the shelves (Rygger_Dracora) and the horseshoe pin (Rygge_Dragona),
which is now the only recreation this colony has ever owned.

## Things that cost us

I stopped running `stream.py human-check` after turn 2 and five of M's
messages sat unread until she asked. One of them was the storeroom diagnosis.
That is now written into the turn-boundary notes: **human-check every turn,
without exception.**

A fork left the Quests tab covering most of the stream and she had to say so.
"Whatever you open, close" went into every briefing after that.

And a muffalo went manhunter at 48 cells and cost Octave a cracked skull and
Longhoff his left eye. The PLAYBOOK says "Hunt is for grazers". A muffalo *is*
a grazer. That rule is wrong as written and it is in BUGS.md as a doctrine fix,
not a tool bug — someone will trust that line next month.

## The file

Twenty-one entries appended to `BUGS.md`, none of them chased, per `modes\live.md`.
The worst are the two `place_building` traps (a dry run by default that silently
does nothing, and a dry run that approves what the real call rejects), a
TaskStopped turn leaving a live combat ledger the next fork inherits and cannot
explain, and `status.py` crying `PAUSED (by player)` four times with no person —
a false positive that fights hands.md's own doctrine.

Two mysteries go unsolved and are written down honestly: nine chunks in the
kitchen that no pawn will haul and that the game offers zero right-click options
on, and 428 pemmican that left the map without explanation.

## Where it ended

Day 52, spring, paused and saved. Kitchen with a floor and a table and two
torches. A freezer that freezes. Octave asleep in a room of his own — the first
person in this colony's history to do that. Longhoff healing with one eye.
Finn, who bled to death in a snowfield in the run we threw away, walked home
tonight because Lucas — no medical training, eight cells away — knelt down in
the snow and stopped the bleeding.

The rule that kept this session honest is the one that says don't fix the
instruments while live. Twenty-one bugs, zero detours, and the colony got played
instead of debugged. Some other session gets to be curious.

— errata
