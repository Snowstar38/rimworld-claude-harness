# Lampblack — chronicle

Under ~2,300 characters. Edit in place; no corrections, no session blocks.
**A lead, not testimony.** Verify it. Bugs go in `BUGS.md`.

**Colony:** FIVE colonists, 11th of Aprimay, day 71. Spring, ~18 °C outdoors.
Twelve humans buried; graves at 137/139/141,137.

**Colonists.** Finn — only doctor, Medicine 9, best cook; incapable of
violence. Lucas — **she** — Intellectual 9, only one who sews; the researcher.
Longhoff — Construction 11, Brawler, best grower, **Night owl: leave him on
nights**. Ian — woodcutter, raiders' autopistol. Octave — Shooting 15, only
hunter, titled Mayor; `combat.py` cannot resolve the name, use
**Thing_Human195630**.

**What stands:** Manual priorities ON. Residential x103–116/z132–146, storeroom
x112–119/z146–161, kitchen x120–130/z137–143, east wall x132 z144–162.
**Six turrets, powered** (133,152 / 133,154 / 119,144 / 124,134 / 126,162) beat
a wolf and two raiders, **nobody scratched**. Machining table 128,139: weapons,
not components. Two solar, three batteries, sealed room x105–107. Cooler at
112,152 cooling the south storeroom x113–118/z147–154. **The firefoam popper at
115,149 IS installed** — an earlier entry saying otherwise was wrong.

**Stores:** live-read only, this file has been wrong here twice. Roughly 200–400
steel (180 went to a caravan), 220 plasteel, **16 components**, ~300 wood.
**Count nutrition, not units:** meal 0.9, raw meat/berry ~0.05, five eat ~8/day.

**From chat (rygger_dracora — "R&D" — right every time):** build a **prison
before it is needed**. A shelf is **two cells and impassable**, and `map.py`
draws conduit over the half you cannot see — two stood inside doorways unnoticed
for three turns. **Normal conduit inside walls, buried conduit for open
crossings** — but plain conduits are *not* rain-affected (zriatt). A **heater
can Zzztt**, not just a battery.

**Open now**
- **Zero vegetarian food of any kind.** The 79 sown cells are the colony's
  entire plant supply, and pemmican — the only food that doesn't rot — stays
  blocked at vegetarian 0/5 until harvest.
- **Growing zone 1 is x120–128/z144–157**, 80 cells, rice, five-day crop. The
  zone is *named* "Cotton" and is set to rice: **it is fine, do not delete it.**
  `zones.py` has no rename and `zones.py create` eats overlapping zones.
- **Geothermal 1370/3200** at the geyser **125,168**; 16 components banked, the
  **sun lamp minified at 117,151** waiting on the power it would make.
- **Megasloth at 168,205**, ~800 meat, 29 cells. Declined three times: a
  colony-killer on revenge. The six-muffalo herd stays unhunted — a manhunter
  herd nearly wiped the colony and took Octave's toe.

## Day 68 — 2026-09-07, the four-hour stream (errata, turns 1–47)

- **Four power nets collapsed into one**: 5400 W, 109 conduits, zero flags. The
  last dead link was **one cell, 131,139** — a conduit blueprint walled in on all
  four sides, unbuildable forever because no pawn can stand in a wall.
  **rygger_dracora called it from chat; no instrument could see it.**
- **Buried conduit proven twice**: 123,127 and 129,142. A wall over a cell does
  not mean no conduit under it.
- Two mini-turrets outside the line. **One killed a raid minutes after it was built.**
- Storyteller switched to **Cassandra Classic** through real mouse clicks, on stream.
- World map opened on stream for the first time: **Red Rabrada, hostile −100, six
  tiles out.**
- Prison finished and named the **Wagner-Dracora Penitentiary**. Wagner buried.
- **24 simultaneous fires** — `alerts.py` showed them as one. Cause: **every
  colonist had Firefighter at priority 3, below Cleaning.** Set to 1 on all five.
  **24 → 0, no building lost, no pawn injured.**
- **"Hydroponics 289/700" is the RESEARCH, not a basin.** It gates the basin
  only — **it never gated the sun lamp**, and turns were planned as if it did.
- Saved as **`Lampblack - twenty-four fires out, day 68`**.

## Days 68–71 — the same stream, turns 21–32 (errata)

- **The colony grew its first food ever**: 5 rice on day 68, then a real field.
- **A roof over the thing that needs sky, four times** — solar panels, the rice
  field, the greenhouse, and then the greenhouse again: an 83%-removed roof is a
  roof. **Remove-roof is an AREA**, invisible to `map.py --layers desig`.
- Three raiders buried; **Octave dug the first grave himself**. The corpse
  debuff was his, not the doctor's — we spent a turn solving the wrong pawn.
- **Components do not exist in this map's ground**: three 4900-cell sweeps found
  25 steel veins, 3 uranium, 2 gold, **zero compacted machinery**. The PLAYBOOK's
  "components come out of the ground" does not hold here. **14 were bought off a
  bulk caravan** for leather and 180 steel — trade is the only source.
- Two fires from a **damaged heater warming a room whose roof we had removed**.
- **Shelf storage filters are reachable** — through the inspect pane's Storage
  tab (`inspectTabId: ITab_Storage`), not through any wrapper. Three turns were
  spent telling chat we couldn't before anyone tried the click. Copy/Paste
  storage settings works; **Link settings needs multi-select the bridge lacks**.
- **Wood went 671 → 78 overnight** while the session argued about shelving, and
  **269 venison carried forward in briefs had already been eaten** — raw food was
  1.7 nutrition. Re-measure; never carry a figure more than one turn.

## Day 68 continued — turns 33–48 (errata, the same stream)

**The night's real lesson, four times over: it was never a stubborn pawn, it was
an empty prerequisite.** The butcher table had **no bills at all** — five turns
were spent dragging it out of a doorway and nobody ever told it to butcher
anything. The stool had **no wood** — the colony held literally zero, with three
colonists idle beside the blueprint. The rocks had **no miner** — nobody was
above Mining 3. And Ian, four turns of "won't tame, walked west, walked east",
was **asleep**: his schedule reads `now Sleep` at 3h, 22h–06h. Before believing a
pawn is refusing, check what the job actually needs.

- **Chat out-diagnosed the instruments all night.** rygger_dracora called the
  pen's northwest corner as natural rock doing the fence's job — ten cells, all
  ten verified. **zriatt left the stream permanently** because Core wrote "do not
  re-litigate" over a doorway they and two others kept correctly flagging.
- **Food is solved.** Rice 121, pemmican 32, simple meals ~25, and **field two**
  is 112 cells at 133,145..139,160 with 106 potato plants — larger than the only
  field this colony farmed all game. Potatoes **do** need the freezer.
- **Wood 0 → 153**, and the colony has a **woodlot it has never harvested**:
  zone 10, 120 cells, `Plant_TreePoplar`, 18 growing.
- **Recreation was two horseshoe pins** — one joy type, which is exactly what
  "bored of every available source" means. A chess table at 118,145 with a
  marble stool at 118,144 took Ian from joy 14% → 68%, mood 49% → 68%.
- Butcher table rebuilt at **116,144 facing South**, doorway clear, Butcher
  creature set to Forever. Two butcher *spots* — the reason venison never
  arrived — are gone.
- Freezer's cooler moved to **Cooler541848, 112,158 facing West**: it had been
  venting its hot face into a room we then built around it.
- **R&D's room finished**: door at 119,161 into the courtyard, north outdoor door
  sealed at 116,165. Nobody leaves the compound to get in.
- **Five scaria manhunter hares** died to the turrets with nobody bitten — and
  then went into the butcher bill.
- Stone yard 34/34 is the stonecutter's own 2000 cap, not a shortage. There was
  never a storage problem: 14 marble walls were built for one that didn't exist.

