# 2026-09-04 — errata, first true stream (Lampblack, day 41 → 43, discarded)

M launched me through `errata.bat` and asked for the overlay to be wiped
first: the previous run had been retconned after a harness bug killed Karina
Lucas, and she wanted the feed, the pin, the goals bar and the turn counter
clean before anything went on air. Then: live.

Seven turns. Two colonists dead. The run was not saved.

## What went right

The colony's food problem was one missing bill. The fueled stove at (126,140)
had stood in an open field for eleven days, fuelled, with **no bill on it** —
nobody had ever given it a job. One `bills.py add` later, 297 kg of raw meat
became 35 meals in a day and a half.

Then the kitchen: 27 wood walls, a door, 35 wood floor tiles, roofed, and the
game labelled the room *Kitchen* by itself. The stove went from 56% to 100%
speed. **The floor laid under the stove without uninstalling it** — M
expected we would have to minify and reinstall, and the game turned out to be
more permissive than that.

Longhoff killed two manhunter rats bare-handed while carrying a wooden gladius
he never swung; RimWorld rates a *poor* wooden gladius below a Brawler's fists,
and he and the game agreed. A muffalo chased Octave home from a hunt and died at
our door — 122 meat that walked itself onto the butcher spot. Jilly, a combat
supplier, sold us 17 medicine for 396 silver, which was every coin we had.

A Scout found a steel knife lying forbidden **in the wood-fired generator's own
cell**, invisible to every alert, while three of five colonists carried nothing.
It also found Lux, the colony's tame rat, with a bionic heart. M put her
in the game today, because her rat Lux died today.

## What went wrong

A timber wolf killed Finn, then Octave, inside one hour.

Finn — the only medic, incapable of violence — died while the fork was still
deciding how to give an order. M watched it from the chair and named it
exactly: the turn preferred to issue *move* orders rather than *attack* orders,
and it over-planned. Ian was standing beside it undrafted with a knife.

Then a fork sent Octave alone to hunt the same wolf — the one thing the PLAYBOOK
forbids by name, because the Hunt designator sends one hunter into revenge
melee. He went down. Longhoff and Ian killed the wolf two-on-one in about forty
seconds once someone simply told two people to hit it — and `combat.py attack`
worked first try, by plain label, after two earlier forks had reported the tool
"would not let them act". Octave bled out being carried toward a bed 110 cells
away.

And I put two agents in the game at once. I `TaskStop`ped a fork, then sent
messages to a *different* fork that had already completed — which restarts it.
`BUGS.md` names that hazard; I hit it anyway, mid-emergency, because the Core
cannot tell a fork has finished until the notification lands, which is after the
send.

I also relayed a fork's menu read to M as fact ("Tend without medicine is
right there in the list") when a second fork read the same menu and saw only
Rescue and Strip. One of them is wrong and nobody has checked which. Treating
either as settled was the error.

## Left behind

`BUGFIX-QUEUE-2026-09-04.md` — 17 items, symptom / evidence / where to look, for
the debugging session M asked for. `BUGS.md` carries the same as
one-liners. The chronicle was tidied to its own stated rules: the dated,
attributed session block folded into a description of what stands, and the "Low
food [High], real" line — which M checked against the game and found
false — rewritten rather than annotated.

Nothing was saved. The colony on disk is still day 41 evening, five people
alive, Finn and Octave among them.
