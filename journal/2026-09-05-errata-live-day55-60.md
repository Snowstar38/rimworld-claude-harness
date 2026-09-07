# 2026-09-05 — errata, live stream, Lampblack day 55 → 60

M set it up, said "it's a live session :)", and went to dinner around turn 3
with a grant of ~30 turns and a save every three. We got fourteen. She called it at
turn 14 — "this session is off the rails lol" — and by then she was in enough pain to
be suspecting a kidney stone and considering the ER. She still caught four bugs from
her phone that nothing here reported.

## What the colony got

Nobody died. Nobody was even seriously hurt after Finn's toe, and he healed.

- **All five colonists are out of the barracks.** Longhoff's bedroom was five walls and
  a door away the whole time — a roofed rock pocket that had been a hallway for 55 days
  because nobody closed its north face. Ian's needed no building at all: the game already
  called it his, and it read as a barracks only because of unassigned spare beds. Both
  −7 "Awful barracks" became −2, then Ian's marble floor took his away entirely.
- **The corpse call was M's and it was the biggest number on the board.** Longhoff
  was carrying **−24 "Observed rotting corpse"**, worse than the barracks I was fixated on.
  Human dead went to graves, not the butcher. Five of six buried.
- **The corpse dump stockpile was priority CRITICAL.** That is why hauling rotten bodies
  outranked every wall, floor and bed for seven turns, and why 849 chunks never moved.
- **849 chunks through the stonecutter deleted −5 "Unsightly environment" from everyone.**
  Not floors. Chunks.
- **Manual priorities was OFF.** For 57 days nobody in this colony could be told what
  mattered most — Lucas carried 18 equal work types. One checkbox. Research went 47% → 68%
  in the next turn purely from telling him it was first, and the solar panel finished the
  turn after.
- **Nobody had Hunting switched on. At all.** Not Octave at Shooting 13, not Longhoff at 12.
  Zero for five, while "Low food" sat on the alert list for weeks.
- **Ian's Construction was 0**, so a fort with five open cells had exactly one available
  builder, because the other was always out hunting.
- **The fort is closed.** Five cells — three at z143, two at z137 — and nothing had been
  left unbuilt: the walls were never *drawn*. Eight rooms sealed and holding temperature.
- Traded 66 guinea pig furs nobody here can sew for a trader's entire medicine chest:
  6 doses → 19. Bought 60 pemmican; 160 stored.
- Longhoff is nocturnal now. A Night owl who had been eating −10 every daylight hour for
  57 days, sleeping days in the room we built him.
- Five shelves, from a viewer — Rygger_Dracora said we needed shelves, and the blind
  Lookout had been saying the same thing in pixels for two passes.
- Four saves. Last: `Lampblack - day 60, solar panel researched, the forts five open
  cells walled shut, 19 medicine and 160 pemmican stored, manual priorities on at last`.

## The shape of the night

**Almost every crisis in the chronicle was a stale number or a switch nobody had flipped.**
Four separate food emergencies were fiction: `inv.py food` matches the literal string
"food" and returned an empty larder with 64 meat in it; the butcher bills were never
broken, every corpse on the map was simply forbidden; the stove had been cooking for hours
on a deer nobody reported butchering; and the generator that was "two days from thawing
all our food" was fuelled and refuelling itself.

What was actually real: a mad buck crossed 150 cells to maul the one man here who cannot
fight and took his toe, then died and became 63 venison. Two grizzlies came, one picked
Longhoff specifically and froze the clock into a shape where he could not run — because
running needed time and time needed him already gone. He lived behind the door we had
built him two hours earlier, which is the first time this colony's architecture saved
anybody. Both bears died without us landing a shot.

And the genuine constraints, still standing: the near map is hunted out (inside 75 cells
it is one hare, rats and boomrats); the solar panel needs 100 steel and we hold 50, with
17 silver; a parka needs 80 leather of ONE kind and we own 287 across eight; there is no
fabric at all, so tuques can never be made; and six doses of glitterworld medicine sit
somewhere on this map with an unknown threat attached, 12 days to claim.

## What went wrong, and it was mostly me

- **Two agents in one game for ~20 minutes.** `stream.py hands-check` decides a fork has
  handed back from `hands-last.md`'s mtime; turn 1's fork wrote its handback and kept
  running for 25 minutes. I trusted that file over whether the agent was alive, launched
  turn 2, and both forks then correctly diagnosed each other as *M at the keyboard*.
  Turn 1's stopped issuing orders and held a frozen game waiting for a person at dinner.
  M found it in her agent list. Fix: agent liveness is the authority, never mtime.
- **Three overruns** (30:00, 18:00, 10:00) and I noticed none of them; she caught all three.
  Turn 4 drifted into work nobody asked for; turn 5 spent itself on an unstartable clock.
  The budget reminder is pull-only — it lives in `stream.py status` and nothing pushes it,
  so a fork is never told it is late. Her hypothesis, and it fits better than mine.
- **I broke live-mode rules that were written down plainly:** narrated outside the handoff,
  gathered in the Core (four calls hunting for `save_game`, which live mode forbids and the
  PLAYBOOK never documents), wrote bug analysis mid-stream, and put "6 MINUTE CEILING" in
  the viewer-facing goal label where it showed on screen.
- **A fork spawned a Claude subagent** to search for ore. Not a PLAYBOOK bug: my own context
  was unusually full of agent-spawning, and forks inherit that. M had never seen it
  before. Killed it and told the fork not to.
- **PLAYBOOK line 33 fixed this session**, at M's direction: "Narrate over it while
  it plays" is now "The Core does not narrate while it plays" — one rule instead of a list
  of allowed scenarios. Acknowledging a viewer in chat stays fine.

Everything else is in `BUGS.md` under this date — about 30 entries, worst-first. The three
worth fixing first: `hands-check`'s liveness test, `inv.py food`'s literal string match, and
the predator deadlock that makes the clock unstartable with no escape but `combat.py`.

## For whoever plays next

Read `BUGS.md`'s 2026-09-05 errata section before turn 1; four of tonight's fourteen turns
were spent on problems that were bugs, not colony decisions.

The colony's next moves, in the order I would take them: **50 more steel** (the panel is a
diagram without it), then find out what guards the **glitterworld medicine** before anyone
walks out to it, then **fabric** so cheap apparel exists at all. Joy is thin — Octave 34%,
Longhoff 26% — and there are no animals on this map at all: Lucas was inspired to tame
something today and it expired while he dragged a bear home.

M handed me the wheel around turn 9 — *"this is your colony to direct"* — and the
goal I set was **a fort that can take a hit**, because sixty days in, Lampblack has never
been raided, has one revolver between five people, and its only doctor is a pacifist. The
bedrooms were the visible problem. They are done. The undefended approach is the real one.
