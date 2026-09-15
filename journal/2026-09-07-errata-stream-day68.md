# 2026-09-07 — errata, the four-hour stream (turns 1–47)

M ran this one live for about four hours and called it at 91% usage. What
follows is the part worth keeping.

## The one lesson, found seventeen times and then broken once

Every apparent shortage this session was **routing, not production**. Cooking off
with a full larder. Three hunters at priority 0. Sculptures parked as cargo. Five
lumberjacks and no tree designated. Six parkas folded at −17 °C. A fur pile older
than the season. Nine sculptures already installed while Ian carved a tenth. The
art bench pointed at the wrong pawn. Nobody with Cleaning above 2.

And at the very end, the fire: **24 simultaneous blazes across the southern
approach, because every colonist had Firefighter at priority 3 — below Cleaning.**
Nobody was refusing to fight the fire. Everybody was doing laundry first. One
number, set on five pawns, and it was over.

Turn 44 broke the streak honestly: six meat and zero vegetables is not routing.
That one was real.

## The other lesson: a true statement that implies a false conclusion

Seven sightings, and it is the failure mode of this whole toolchain.

- Turn 25: "the prison was cancelled." True that no *frame* existed. False that no
  *wall* existed — 114,132 had a finished wooden wall the entire time.
- Turn 43: "all corpses forbidden." True when read. Already false when acted on.
- **The worst one: "Hydroponics 289/700."** Carried across many turns as a basin
  filling up. It is the *research*, at 396/700. There are **zero hydroponics
  basins and zero sun lamps on the map.** We planned around a building that does
  not exist and is 304 research points from being startable.
- `alerts.py` showing 24 fires as one, with no count.
- `buildings.py --power` flagging a lamp UNPOWERED while its own per-cell read on
  that same lamp says powered, connected, 4040 W excess.

The rule that came out of it: **a Scout's read is a lead, not a fact** — the same
rule we already had for Lookouts. M proved it herself mid-turn, overruling
a Scout: *"the west edge is fine its the east that is open."* She can see the map.
She wins.

## rygger_dracora was right every single time

Not once wrong, across four hours.

- **The entombed conduit.** They said the east turret was unpowered and the
  conduit wasn't on the grid, *"right in the middle between the batteries and the
  generators."* It was **one cell — 131,139** — a power conduit blueprint with
  walls on all four sides. No pawn can stand inside a wall, so it could never be
  built, so the whole net sat at 0 W. No instrument could see it. They could.
- **Take the inner wall, not the outer**, so the perimeter never opens. They and
  M both said it; we had designated the outer one first.
- **"Your grid is fragile, you need redundancies so one break doesn't take it
  apart."** We had just spent three turns collapsing four nets into one. One net
  is one point of failure. A ring, not a line. Still unbuilt.
- **"Get people to fight the fires"** — before we found the priority number.
- **"Protect the turret"** — which hangs off that one net through 132–134,139,
  and fire reached 132,118.

Their question about geothermal power and a steam geyser went unanswered for most
of the session, which is the one discourtesy of the day.

thestatpow spotted Octave walled into the south yard at −13 °C, logged by the game
as nothing but `1 colonist idle`. That catch probably saved his life.

## The operational failure, twice

**A message to a fork that has already handed back restarts it.** I checked
`ListAgents`, saw turn 40 running, sent it a raid warning — and it completed
inside the gap between the check and the delivery. It came back to life alongside
turn 41 and the two fought each other over one game. M caught it from chat
before I did.

`ListAgents` first is **necessary and not sufficient.** The race lives inside the
gap and no check closes it. The rule that actually works: **treat a turn as
unreachable the moment it hands back, and carry news into the next brief
instead.** Held to it for the rest of the session, including a fire, and it cost
nothing — the one time it might have mattered, rygger's conduit route had already
been satisfied a turn earlier.

## What got done

Prison built and named the Wagner-Dracora Penitentiary. Wagner buried. The fur
heap traded for 15 components. Ian's floor and lamp: −2 Awful to +1 Mediocre,
mood to 80%. Octave's room flipped by "Purple Guts," a statue he carved himself.
Storyteller switched to Cassandra Classic on stream, through real mouse clicks.
The world map opened on stream for the first time ever — Red Rabrada, hostile
−100, six tiles out. Perimeter closed south and east. **Buried conduit proven**
(123,127 and again at 129,142: a wall or a torch lamp and a live conduit in the
same cell). Two mini-turrets outside the line, and one of them killed a raid
minutes after it was built. Four power nets collapsed into one: 5400 W, zero
flags.

## What the next session inherits

1. **Zero plant food of any kind.** Both growing zones empty and under open sky at
   −6.7 °C, where RimWorld plants simply stop. The 99 wild berry bushes at 8–33%
   will not mature frozen.
2. **The fix needs no research: a sun lamp over roofed indoor soil, rice under
   it.** Roofed natural soil exists inside the mountain at x113–117/z147–160 — but
   it is Storeroom and Freezer today, so siting is real work. *Unverified that the
   sun lamp is unlocked;* Scouts can't run `build.py`. Check before promising it.
3. **The Firefoam popper at 115,149 has been in storage this whole time,
   uninstalled.** We own a fire defence and have never deployed it. The wood-fired
   generators at 117,136 and 124,135 are what it should protect.
4. **Firefighter=1 on all five. Never let it go back.**
5. Three stallions designated and unshot (129,86 / 130,80 / 135,80) — ~400 meat
   and the first leather of the season. A megasloth at 177,127 deliberately left
   alone; a colony-killer on revenge.
6. rygger's **grid ring**. And, per rygger, **butcher the boomalope — that's good
   meat**, which also implies it went down.
7. Finn is still the last −2 Awful bedroom, a 4-cell rock box with no free cell.
   Needs its own turn, and M's rule: **wall along the edge of the rock
   first, then mine**, so it is never outside.
8. M's machining-room wood wall, deferred four times, and a fire has now
   made her case for her.

57 WEIRD lines went to `BUGS.md`, dated. Live mode forbids chasing them; writing
them down is the whole obligation, and unwritten the rule just loses the bug.
