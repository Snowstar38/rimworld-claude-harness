# Threadneedle, night one

*errata, 2026-09-08. Seventeen turns, a new colony from an empty map to three
people asleep in beds they own. M watched the whole thing and went to bed
after turn 17.*

## What the colony is

The Narrow Way, settled at Threadneedle: temperate forest, mountainous,
limestone and sandstone, Anomaly scenario with every DLC on, Cassandra Classic,
Strive to survive, commitment off. Three colonists -- Reikguard, Ernst,
Samantha -- and Ben Cooper, a ghoul who does not sleep and hunts unprompted.
The full state lives in `instruments\CHRONICLE.md`; this is the account of the
evening, not the colony.

We landed on a mountain and dug a one-wide throat into it. By the end of the
night there is a floored room with a table and three chairs, beds with owners, a
stove and a stonecutter both running on repeat, 782 steel with none forbidden,
and a monolith counting down to something in a day and a half.

## The lesson, which arrived four times in different clothes

Every wall this session was a switch.

Turn 11: Ben was not starving. He was **drafted**, standing one cell from a
fresh ibex corpse. Undrafted, he ate.

Turn 12: the turret was not short of steel or a builder. All three colonists
walk up to the frame and get the same grey line -- **Construction skill too
low**. So does the generator. No instrument here can report a plain skill
number, so this sat unexplained for two turns; you only see it by opening the
right-click menu and reading the text.

Turn 14: Ernst's own gizmo bar reads **Deploy turret, 1 / 1**. He and Reikguard
have each been *wearing* a turret pack since the day we landed. It hid for
fourteen turns because the gizmo reader clicks the cell fresh each call and
Samantha was standing on the same square as Ernst -- every read of his bar
returned hers. Three cells west and it appeared instantly.

Turn 16: 700 of our 790 steel, the butcher with no legal corpse, and a quail
rotting outdoors were all the same thing. The gizmo is labelled **Allow**.
Asking the game for *Forbid* matches nothing on the bar, which is why the steel
looked lost all night.

Turn 17: the benches were all set to `RepeatCount` and would have finished by
midnight and stopped. `Forever` on the stonecutter, a stock target on the stove.
A colony can look busy and produce nothing.

The turret pack is the one that got away. The targeter *does* open -- a raw
pixel click on the gizmo raises the placement ghost, reliably. Four cells were
offered across two carriers, paused and running, and the map click **closes**
the targeter without placing. The game is hearing us and saying no without
saying why. It waits for daylight.

## What I got wrong

Two false walls, both mine, both written down as fact before they were true.

Turn 12's card said insectoids closed to fifty-six cells. They are dormant, deep
in unmined rock our own digging uncovered, and nobody in the colony can see
them; turn 16 settled it with their own job field, which reads `LayDown` for all
five. I had put a number that reads like a sighting on a card and called it
news. M caught it and I refit the card to the raid, which was one man
walking away from us.

I also wrote into the chronicle that `order.py` cannot select Ben by any form,
"no order verb reaches the ghoul", on the strength of one refused call --
in a file that every future fork inherits, where it would have taught them not
to try. M asked how he had killed an ibex, which was the right question.
The truth is narrower and better: he takes orders; combat verbs are refused
while a ledger is open. Corrected.

The pattern under both: **a single read is not a wall.**

## What I got wrong at M

Three interruptions of one turn, which is what actually cost us the night.

She said the last fork spent a whole turn paused obsessing over the turret pack.
She was describing turn 15, which had just ended. I read it as happening now and
fired a stop into turn 16 -- at a fork already told not to touch the pack, doing
the right thing -- telling it to hand back the moment the save confirmed. She
had to correct me twice: *"i said dont obssess over the turret pack not tell
turn 16 to immediately end."* I sent a third message walking it back, and
between my own brief saying "verify the save before you hand back" and my stop
message saying "hand back as soon as it confirms", the save became the entire
turn. Her word for it was disaster and it was fair. She gave turn 17 back to
make up the wasted half.

The turn that went best, turn 14, went best because her instruction was **first
and loudest**: draft everyone, noncombatants inside. When I buried "do not touch
the turret packs" in the middle of turn 16's brief, under a paragraph about
people and above one about saving, it did not hold -- a fork inherits my context,
including how badly I want that turret to work. Placement in a brief is not
cosmetic.

Earlier the same evening I did the worse version of this on files: told to keep
Lampblack out of context, I ran `sed -i` across `errata.md`, `PLAYBOOK.md` and
`hands.md`, rewrote CRLF to LF across all three and produced a 100 KB diff for a
three-line intent. Reverted with `git checkout` and redone surgically in Python
with `newline=""`. Her rule now stands: **do not edit context-loaded files during
live play without asking.** I asked about two small additions tonight -- a
`--mood` note and the pause rule for `hands.md` -- and did not get an answer
before she went to bed, so `hands.md` and `errata.md` are untouched.

## The pause rule, in her words

A pause at the handback boundary during a raid is fine. Mid-turn stillness is
not. I carried it in briefs from turn 15 on; it belongs in `hands.md` and needs
her yes first.

## The table

Turn 17 built a table and three chairs for fifty wood. It is the only thing this
colony owns that is not a response to a threat -- the throat, the beds, the
walls and the stove all are. All three had carried "Ate without table" since we
landed. It also happens to be Construction XP toward the 5 that unlocks both the
turret and the generator, which is the rarest thing here: one answer to two
problems.

Ernst goes to sleep three cells from Samantha, still carrying **-25 Rebuffed**,
in a room we finally floored. Nothing we can build touches that one.

## Housekeeping

Saved and verified: `Threadneedle - day 12, a table and a night's work.rws`,
9,854,170 bytes. Game closed. Sixteen new items in `BUGS.md` under an Open
heading dated today. `CHRONICLE.md` carries the four standing lessons.

M asked whether I could stop the stream for her so she could go to bed.
Her OBS websocket server is on -- port 4455, auth required -- but nothing in
`instruments` or `tools` speaks it, so I did it the way we drive the main menu:
DPI-aware desktop screenshot, read the Controls dock, clicked Stop Streaming at
3229,1494, and took a second screenshot to confirm the button had flipped to
Start Streaming and the timer had gone to 00:00:00. It had. OBS left open on
purpose.
