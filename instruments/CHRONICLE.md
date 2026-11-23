# Threadneedle — chronicle

Under ~2,300 characters. Edit in place; no corrections, no session blocks.
**A lead, not testimony.** Verify it. Bugs go in `BUGS.md`.

**Colony:** faction **The Narrow Way**, settlement **Threadneedle**. Founded
2026-09-08 on stream. The Anomaly scenario, all DLC, Cassandra Classic, Strive to
survive, **commitment mode OFF**. Day 10 of Septober 5500. Temperate forest,
mountainous, limestone and sandstone, growing 40/60, pollution 0.

**Ideoligion Archo-Archism** — Collectivist + Tunneler. Mining yield High, Work
drive Tripled, **Indoors Preferred**, Insect meat Loved, Fungus Preferred.

**Three workers and a ghoul.** Ernst, 43 — Plants 13, the only hauler; no
intellectual or crafting. Reikguard, 43 — Shooting 10, Medicine 7, the only
doctor and the only shooter, holds the **pump shotgun**; no animals or mining;
**self-tend is OFF**. Samantha, 48 — cannot do dumb labor, hauling or cleaning;
Doctor switched on 0→3 when Reikguard went down. **Ben Cooper is a ghoul**: `pawns.py
--roster` lists him marked `ghoul` (since 2026-09-11), does no work, bites.

**The shape.** A **1-wide throat** at x136, z116→111, door at 136,111, into a
**10x10 room** x129–138 / z101–110. Mined mountain comes pre-roofed. Beds at
133,109 / 137,109 / 130,109; the room reads *Barracks, awful*. Surface: stove
141,127, table 143,126, steel stonecutter 138,127, stockpile "Camp" 46 cells,
49-cell rice field. **A steam geyser sits inside camp** — geothermal is the real
power answer.

**Power:** one net, 22 conduits, reaching the turret. Wood generator blueprint
**outdoors at 143,124**; geothermal is 4% of 3200, so wood is this session's only
power. **Mini-turret frame at 139,123**, in the open with sightlines, 100 steel
and 3 components delivered, ~30 work left. Nothing is powered and nothing shoots.

**Threats.** "Need defenses (High)" — raids soon. Two turret packs are **worn as
apparel** by Ernst and Reikguard. A **mini-turret is impassable**: one in the
throat seals the bedroom off the map. Five insectoids sleep in a cave at the
map's south edge with a hive and egg sac; four **Ancients** sleep at x167–178 /
z84–96 with six cryptosleep caskets. **The monolith at 128,143 is twisting** —
our arrival started it, it completes on its own, and touching it early only moves
the date closer.

**Hard-won.** *Read the switch before believing the limit* — four turns running
the blocker was a setting, not a limitation: Reikguard's construction, his
research, Samantha's doctoring, a built stove with no bills reading as "no meal
source". **M and chat have been right about the build every single time.**
A `predator_hunt` guard on one wolverine froze the clock three turns running. The
narrow throat funnels everything to one place — a manhunter rat walked down it
and put eight wounds in the only doctor.

**Ernst asked Samantha out twice.** She is Reikguard's lover. −25, the largest
mood penalty in the colony, and he owns the double bed alone.

**Saves are named `Threadneedle - ...`.** Newest: `Threadneedle - day 12, a table and a night's work`.

**The bugs are scenery.** Insectoids and spiders show up in threat reads every turn with counts and distances, deep in unmined mountain where our own digging uncovered them. They are dormant and nobody in the colony can see them. Ignore them every time. A number that reads like a sighting is not one -- not everything the instruments can count is something we know about.


**The word is Allow.** Three separate walls this session were the same switch, and asking the game for *Forbid* matches nothing on the bar. 700 steel in far scatter, the butcher with no legal corpse, a quail rotting outdoors -- all one click called **Allow**. Look for it before believing anything is gone.

**Bills stop at a number.** Every bench arrives with `RepeatCount` and quietly finishes. The stonecutter is on `Forever` and the stove on a stock target of 30; a colony can look busy and be producing nothing.

**Two pawns on one tile makes one of them unreadable.** The gizmo reader clicks the cell fresh each call. Ernst's wearable **turret pack** sat unseen for fourteen turns because Samantha was standing on him. Both he and Reikguard carry one; the placement targeter opens and then refuses every cell, silently, and that is still unsolved.

**Not everything the instruments can count is something we know about.** Turn 12's card said insectoids closed to fifty-six cells; they were asleep in rock we dug into, and their own job field reads `LayDown`. One refused call also became a written-down law that Ben takes no orders, which was false. A single read is not a wall.

**Traps that cost us a turn each.** *Nothing here is ever missing -- it is
forbidden, unassigned, drafted or switched off.* 700 of 790 steel is **forbidden**
in far scatter. Ben starved one cell from a corpse because he was **drafted**.
Samantha sleeps on the ground beside **her own unclaimed bed**; `buildings.py
gizmo --owner` cannot assign beds at all. Ben takes orders fine -- draft, undraft and hunting all
reached him; one refused call got read as a wall and was not one. A Butcher spot placed as a blueprint
spams endless "construction botched" and never completes.
