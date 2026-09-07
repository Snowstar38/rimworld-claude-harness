"""Seven layers of the same rectangle, because one character per tile is a lie.

Each tile carries several independent facts -- what is underfoot, what is
overhead, what is standing on it, whether it is zoned, what is built on it, what
has been ordered done to it, which room it is in -- and the old renderer
resolved that collision by priority, so roof lost to anything lying on the
ground.  Cost, Aug 30: an indoor cell holding a chunk and an outdoor cell
holding a chunk printed the same character, I read the map, and mined the last
wall between the base and open ground.  M, from her chair: *"you dug out
your house so now it's wide open."*

So: seven layers over the same rect, each one getting the whole character.
Layer 1 draws no things at all, so roof can never be suppressed by whatever is
standing on the tile.  Design agreed with M in
`notes\\rimworld-map-design.md`.

The seven layers are the model.  What an ordinary call PRINTS is a view over
them, because seven 48x48 grids is ~25KB that stays in context and is re-read on
every later turn -- so the default merges the pairs that answer the same
question ([M], Aug 30):

  1. terrain / roof, with PAWNS drawn on top (layers 1+3)
  2. items ON stockpiles (layers 2+4) -- UPPERCASE = stored, lowercase = loose
  3. buildings / furniture (layer 5)
  4. designations (layer 6), a coordinate list while they are few
  5. rooms (layer 7) as ONE key line, no grid

Layer 7 is the one layer the default deliberately does not draw as a grid.  A
seventh 48x48 grid is ~2.4KB of context on every call; the key line -- which
rooms are in view, what each is called, how many cells -- is the part that
answers a question, and it costs one line.  `--layers rooms` or `--full` draws
the grid.

`--full` prints the seven pure grids instead, and is the thing to reach for when
a merged glyph is in your way.

  python map.py 112 140                 # 48x48 centred on 112,140
  python map.py 112 140 --size 96       # bigger, costs more calls
  python map.py 112 140 --size 96 48    # W H, when the interesting shape is wide
  python map.py 96 120 48 32            # x z WIDTH HEIGHT -- act.py's own form
  python map.py 96 120 150 170 --corner # x0 z0 x1 z1, the opposite-corner form
  python map.py 96 120 150 170 --rect   # --rect is the old spelling of --corner
  python map.py 112 140 --no-who        # don't click to identify strange pawns
  python map.py 112 140 --stock         # force the stock cell tool (degraded)
  python map.py 112 140 --layers items,build   # only some layers
  python map.py 112 140 --layers rooms  # draw the room grid, not just its key
  python map.py areas 122 140 6 6      # ROOF AREAS and the rest of areaManager
                                       # -- what `act.py apply "Build roof area"`
                                       # writes; no layer can show it
  python map.py 112 140 --full          # seven pure grids, nothing compacted
  python map.py 112 140 --legend        # the map AND the full key under it
  python map.py rooms                   # whole-map room census, one line each
  python map.py rooms --cold            # the same census, coldest indoor room first
  python map.py rooms --hot             # ... warmest first
  python map.py rooms --outdoors        # ... listing the outdoors and the doorways too
  python map.py room 112 140            # the room covering that cell, in full
  python map.py --legend-only           # the full key, without calling the game

FOUR POSITIONAL NUMBERS ARE `x z WIDTH HEIGHT`, the same order `act.py apply`
takes.  `--corner` (or the older `--rect`) says the second pair is an opposite
corner instead.  Any form that works out to an empty window is REFUSED, naming
both forms -- it never prints an empty map.

`--layers items` DOES DRAW PLANTS: 'b' wild food, 'h' healroot, 'c' sown crop,
't'/'T' tree, and a species census in the layer's own footer.

NATURAL ROCK IS NEVER THE SAME CHARACTER AS A BUILT WALL, in any layer.
'#' is the mountain, 'S' is the mountain smoothed, 'H' is a wall a colonist
built, and each ore vein carries its own letter ('s' steel, 'k' compacted
machinery/components, 'p' plasteel, 'g' gold, 'l' silver, 'r' uranium,
'j' jade).

The sweep runs on `home/get_cells_plus`, the companion tool in
`rimworld\\companion` (a standalone DLL under the game root's
`BridgeTools\\`, not a mod).  Same rectangle semantics and the same 1024-cell
cap as `rimworld/get_cells_info`, plus the two fields the stock payload
provably does not carry -- per-thing `forbidden` and per-bed `ownerName` -- in a
payload 76.6% smaller.  Those two fields are layers 2 and 5's new indicators.
The sweep also asks that tool for only the fields these seven layers actually
read (`CELL_FIELDS` / `THING_FIELDS` below, and the ACCOUNTING block prints
them on every run), which is the second half of the same saving.
If the companion is not loaded, the sweep falls back to the stock tool and says
so, loudly, once at the top and again in every footer that just lost data: a
narrowed view that does not announce itself is the failure this whole file is
built against.

Two rules this file is built around, both bought with real failures:

1. **Every filter states itself in its own output.**  `inv.py` hid nine cells of
   boulders in the pantry because "chunks are clutter", and the colony read as
   starving while 145 berries lay outside.  Anything dropped here gets a
   one-line footer count.
1a. **...including the filters that exist to save context.**  `--layers` and
   sparse-layer compaction both cut output, so both announce themselves in the
   output they cut: a compacted layer says it compacted and names the override,
   and ACCOUNTING says which layers were skipped and that its counts still
   cover the whole payload.  Layers 1 and 4 never compact -- their content IS
   the spatial shape.

2. **Every layer checks itself.**  M is never going to look at this
   output -- she said so, and that is why the tool is tuned for me rather than
   for presentation -- so nobody downstream will catch a silent drop.  The
   ACCOUNTING block reconciles every thing in the payload against the layer it
   was drawn in and says MISMATCH in capitals when they disagree.
"""
import sys, collections, rim

SIZE = 48                 # [M] "an option to have it return a larger area"
BLOCK = 32                # the bridge refuses >1024 cells per call; 32x32 == 1024

# ---------------------------------------------------------------- terrain ---
# Keyword tests, not a def list -- and the layer-1 footer prints a census of
# every terrainDefName it saw with the class it assigned, so a bad guess here is
# visible in the output instead of quietly redrawing the floor.
WATER_HINT = ("Water", "Marsh")
FLOOR_HINT = ("Floor", "Tile", "Concrete", "Bridge", "Carpet", "Pavement",
              "Smooth", "Flagstone", "Sterile", "Wood")

INSECTS = {"Megaspider", "Spelopede", "Megascarab", "Insectoid"}

# ----------------------------------------------------------------- plants ---
# [M] "don't collapse all plants into one symbol".  "Food plant" here is a
# SPECIES property -- the game offers Harvest and it yields something -- not a
# ripeness question; ripeness is not in the payload at all (the parenthetical in
# a plant label is hit points, not growth).  Short def lists are a thing this
# repo is rightly wary of, so the fallback is the honest one: an unrecognised
# Plant_* draws the generic glyph AND gets named in the layer-2 footer, so the
# list gets extended instead of silently miscategorising.
HERB = {"Plant_Healroot", "Plant_HealrootWild"}
WILD_FOOD = {"Plant_Berry", "Plant_Agave", "Plant_Ambrosia"}
CROP = {"Plant_Potato", "Plant_Rice", "Plant_Corn", "Plant_Strawberry",
        "Plant_Haygrass", "Plant_Cotton", "Plant_Devilstrand", "Plant_Hops",
        "Plant_Smokeleaf", "Plant_Psychoid", "Plant_Pineapple", "Plant_Tinctoria"}


def plant_bucket(dn):
    if dn.startswith("Plant_Tree"):
        return "tree"
    if dn in HERB:
        return "herb"
    if dn in WILD_FOOD:
        return "wildfood"
    if dn in CROP:
        return "crop"
    return "plant"


# ------------------------------------------------- rock, ore, and a WALL ---
# Natural rock and a built wall must never draw the same character.  Neither
# payload carries a "natural" flag -- get_cells_plus emits className
# (thing.GetType().Name) and defName and no faction -- so the def name is the
# test: natural rock is `Mineable`/the rock defs, veins are `Mineable<Ore>`,
# smoothed rock walls are `Smoothed<Rock>`, a built wall is defName `Wall`.
# Anything unrecognised is still bucketed AND named in a footer.
NATURAL_ROCK = {"Granite", "Sandstone", "Slate", "Limestone", "Marble",
                "CollapsedRocks"}


def is_natural(cn, dn):
    """Is this thing the mountain rather than something somebody built?"""
    return ("Mineable" in cn or dn in NATURAL_ROCK
            or dn.startswith("Mineable") or dn.startswith("Smoothed"))


def rock_bucket(dn):
    """natural rock / smoothed rock wall / ore vein, from the def name alone."""
    if dn.startswith("Mineable"):
        return "vein"
    if dn.startswith("Smoothed"):
        return "smoothrock"
    return "rock"


# One letter per ore: steel and compacted machinery are not interchangeable and
# a single '%' hid the difference.  An ore def not listed here draws '%' and its
# def name is printed in the layer-1 footer.
ORE_KIND = [
    ("s", "MineableSteel", "compacted steel"),
    ("k", "MineableComponentsIndustrial", "compacted machinery (COMPONENTS)"),
    ("K", "MineableComponentsSpacer", "compacted machinery, spacer"),
    ("p", "MineablePlasteel", "plasteel"),
    ("g", "MineableGold", "gold"),
    ("l", "MineableSilver", "silver"),
    ("r", "MineableUranium", "uranium"),
    ("j", "MineableJade", "jade"),
]
ORE_BY_DEF = {d: g for g, d, _ in ORE_KIND}


def ore_glyph(dn):
    return ORE_BY_DEF.get(dn or "", "%")


# ------------------------------------------------------------- the things ---
# One thing -> exactly one bucket.  The buckets are what ACCOUNTING adds up, so
# a thing that lands nowhere is a loud failure rather than a missing character.
def bucket(t):
    # `get_cells_info` reports namespace-qualified classNames (`Verse.Pawn`);
    # `home/get_cells_plus` reports the short type name (`Pawn`).  Every test
    # below is a substring test for that reason -- except the pawn one, which
    # has to be exact or `Pawn` matches nothing and `.Pawn` matches only the
    # stock shape.  Both shapes are accepted so the fallback path renders the
    # same map.
    cn = t.get("className") or ""
    dn = t.get("defName") or ""
    if t.get("isBlueprint"):
        return "blueprint"
    if t.get("isFrame"):
        return "frame"
    if cn.rsplit(".", 1)[-1] == "Pawn":
        return "pawn"
    # Natural first, and on the def name as well as the class name: a payload
    # without `className` used to send a granite face down to "item".
    if is_natural(cn, dn):
        return rock_bucket(dn)
    if "Building_Door" in cn or dn in ("Door", "Autodoor"):
        return "door"
    if dn == "Wall" or dn.endswith("Wall"):
        return "wall"
    if "Building" in cn:
        return "building"
    if dn == "Fire":
        return "fire"
    if dn.startswith("Filth_"):
        return "filth"
    if dn.startswith("Plant_"):
        return plant_bucket(dn)
    if dn.startswith("Chunk"):
        return "chunk"
    if dn.startswith("Corpse"):
        return "corpse"
    return "item"


STRUCT = ("rock", "smoothrock", "vein", "wall", "door")      # layer 1 draws these
BUILDY = ("building", "wall", "door", "blueprint", "frame")   # layer 5
BLOCKS_HAUL = ("item", "corpse", "chunk", "tree")             # layer 4 occupancy

# Which building is it?  Matched on className suffix, then defName; anything
# unmatched draws '%' AND gets its label listed in the footer -- an unknown
# building is never allowed to look like a known one.
BUILD_KIND = [
    ("b", ("Building_Bed",)),
    ("w", ("Building_WorkTable", "Building_ResearchBench")),
    ("s", ("Building_Storage",)),
    ("c", ("Building_Grave", "Building_Casket", "Building_Crate",
           "Building_Container")),
    ("t", ("Building_Turret", "Building_Trap")),
    ("f", ("Building_Campfire", "Building_Heater", "Building_Torch")),
    ("=", ("Building_PowerConduit",)),
    ("e", ("Building_Battery", "Building_PowerPlant", "Building_Generator")),
    ("/", ("Building_Door",)),
]
BUILD_DEF = [
    # Conduit BEFORE the rest: it is wire, it is not a source and it is not
    # furniture, and drawing it as 'e' alongside a standing lamp is what let two
    # turns "trace an intact chain" to an unpowered turret (2026-09-07 turn 33).
    ("=", ("Conduit",)),
    ("e", ("SolarGenerator", "WindTurbine", "Battery",
           "StandingLamp", "SunLamp", "Cooler", "Heater", "WoodFiredGenerator")),
    ("f", ("Campfire", "FueledStove", "ElectricStove", "TorchLamp",
           "PassiveCooler")),
    ("w", ("TableButcher", "TailoringBench", "CraftingSpot", "ButcherSpot",
           "ResearchBench", "Smithy", "Table", "Bench")),
    ("s", ("Shelf",)),
    ("t", ("Turret", "Trap", "Sandbags", "Barricade")),
    ("H", ("Wall",)),
]


# Which glyph wins a cell that holds more than one build.  Lower wins.
# `!` (forbidden) is the loudest fact about a cell; a wall or a door under
# something else is not the interesting half, so those lose to furniture; and a
# CONDUIT loses to everything, because a conduit runs under half the base and
# saying so is never the answer to "what is standing here".
BUILD_RANK = {"!": 0, "H": 2, "/": 2, "=": 3}


def bed_owners(t):
    """-> list of owner names, or None when the payload cannot say.

    `home/get_cells_plus` emits `ownerName` for EVERY bed, explicitly null when
    nobody is assigned, and `ownerNames` as well when a bed has more than one
    owner.  The key being always present is the load-bearing part: it makes
    "unassigned" distinguishable from "not reported", which is exactly the
    distinction the stock payload cannot make (it has no owner field at all).
    So None here means unknown, [] means genuinely unassigned.
    """
    if not SRC["plus"]:
        return None
    if "ownerName" not in t and "ownerNames" not in t:
        return None
    names = t.get("ownerNames")
    if names:
        return list(names)
    return [t["ownerName"]] if t.get("ownerName") else []


def build_glyph(t):
    """The building's kind glyph.  [M] asked for beds to carry assignment in
    the glyph itself ("a special indicator (capital or smth)"), so a bed with an
    owner draws 'B' and an unassigned one 'b'.  A bed whose ownership is unknown
    -- the stock-tool fallback -- draws lowercase too, and the footer says in
    words that it was never checked, so 'b' can never be read as "checked, free"
    when nothing checked it."""
    cn = t.get("className") or ""
    dn = t.get("defName") or ""
    for g, keys in BUILD_KIND:
        if any(k in cn for k in keys):
            if g == "b" and bed_owners(t):
                return "B"
            return g
    for g, keys in BUILD_DEF:
        if any(k in dn for k in keys):
            return g
    return "%"


# ----------------------------------------------------------- designations ---
# [M], added mid-build: the Aug 30 wall breach was a Mine designation that
# existed and could not be seen in map context.  Matched on substring so the
# exact string shape (defName / label / raw string) does not matter; anything
# unmatched draws '?' and is named in the footer.
DESIG_KIND = [
    ("M", ("Mine",)),
    ("H", ("Harvest",)),
    ("C", ("CutPlant", "Cut plant", "ExtractTree", "Chop")),
    ("h", ("Haul",)),
    ("D", ("Deconstruct", "Uninstall")),
    ("S", ("Smooth",)),
    ("X", ("Hunt", "Slaughter")),
    ("T", ("Tame",)),
    ("A", ("Allow", "Forbid")),
    ("P", ("Plan",)),
    ("O", ("Open", "Flick", "Claim", "Strip", "RemoveFloor", "RemoveRoof",
           "BuildRoof")),
]


def desig_name(d):
    """The payload's designation shape is unverified, so accept all of them."""
    if isinstance(d, str):
        return d
    if isinstance(d, dict):
        for k in ("defName", "designationDefName", "label", "name", "def"):
            v = d.get(k)
            if isinstance(v, str) and v:
                return v
        return str(d)
    return str(d)


def desig_glyph(name):
    for g, keys in DESIG_KIND:
        if any(k.lower() in name.lower() for k in keys):
            return g
    return "?"


def mine_breaches(grid):
    """Mine orders that open a roof.  -> [(tier, x, z, nx, nz)], tier OPEN|near.

    Lifted out of layer 6 on Aug 31 so that `verify.py` -- the Lookout's
    read-only lead check -- COULD ask the same question in the same words.  A
    second copy of this rule is exactly the way the two would drift apart, and
    the rule is the one that cost the base its last exterior wall.

    **It was never wired up, and this docstring said it had been** (corrected
    2026-09-01): `grep mine_breaches verify.py` returns nothing, and the only
    callers are in this file.  It is available and it is the right shape; a
    Lookout lead about an open roof still gets no second opinion from it.
    Wiring it is a real to-do, in `BUGS.md` -- but a comment that claims a
    caller it does not have is worse than the gap, because it stops anyone
    looking.

    Two tiers, and the difference is real: an unroofed WALKABLE neighbour is
    open ground you can walk in from the moment the rock goes; an unroofed
    unwalkable one is still a rock face, one cell from becoming the first kind.
    Measured on Lampblack day 21, where the live Mine order at (103,132) has
    exactly the second shape -- calling that a breach would have been a false
    alarm.

    Only cells inside `grid` are looked at, in both roles: a Mine order one
    tile outside the swept rect is not checked, and neither is a neighbour that
    was never swept (missing reads as "no data", not as "roofed").
    """
    out = []
    for (x, z), c in grid.items():
        if c.get("fogged") or not c.get("roofDefName"):
            continue
        if not any(desig_glyph(desig_name(d)) == "M"
                   for d in (c.get("designations") or [])):
            continue
        for nx, nz in ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)):
            n = grid.get((nx, nz))
            if not n or n.get("fogged") or n.get("roofDefName"):
                continue
            out.append(("OPEN" if n.get("walkable") else "near", x, z, nx, nz))
    # Top-down, left-to-right, so the list reads in the same order as the grid
    # printed above it.
    return sorted(out, key=lambda b: (-b[2], b[1]))


# ------------------------------------------------------------------ scan ----
PLUS_TOOL = "home/get_cells_plus"      # the companion; carries forbidden+owners
STOCK_TOOL = "rimworld/get_cells_info"  # the bridge's own; carries neither

# One dict, read by every layer footer, so that a degraded run says it is
# degraded in each place a reader might otherwise trust the silence.
SRC = {"tool": PLUS_TOOL, "plus": True, "why": ""}

# ------------------------------------------ what the sweep actually asks for --
# `home/get_cells_plus` emits every field unless it is told otherwise, and a
# 32x32 block of the lot measured 197.9 KB on day 39.  These two strings are the
# fields THIS FILE reads, found by grepping every `c.get(...)` and `t.get(...)`
# in it rather than by memory:
#
#   terrain       layer 1's terrain_class() and its terrain census
#   roof          layer 1's roofed/unroofed glyph, and mine_breaches()
#   fogged        the '?' cell in every layer
#   walkable      layer 1's hidden-blocker count, mine_breaches()' OPEN-vs-near
#                 tier, and solid() under layer 7
#   zone          layers 4 and 2+4 (through normalise_plus, which hoists the
#                 top-level descriptor back onto the cell)
#   things        collect(), which feeds every layer that draws a thing
#   designations  layer 6, and mine_breaches()
#
# and per thing: label (footers), className (bucket/build_glyph), stackCount
# (forbid_line), hitPoints (merge_beds' identity key), forbidden (layers 2 and
# 5's indicators), owner (ownerName/ownerNames/medical -> bed_owners), build
# (isBlueprint/isFrame -> bucket and layer 5).  `defName` is always emitted by
# the tool whatever is asked for.
#
# Two cell fields and two thing fields are deliberately NOT asked for, and this
# is the list to change if a layer ever grows a use for one:
#   passable  -- nothing here reads it; `walkable` is the question every layer
#                asks, and the two are not derived from each other
#   areas     -- normalise_plus() used to unpack the per-cell ids into
#                c["areas"] that no layer has ever looked at
#   stuff     -- stuffDefName is never read
#   plant     -- growth/harvestableNow are never read; layer 2's footer says in
#                so many words that plant kinds here are SPECIES, not ripeness
# Asking for less is a filter, so ACCOUNTING prints both strings on every run.
CELL_FIELDS = "terrain,roof,fogged,walkable,zone,things,designations"
THING_FIELDS = "label,className,stackCount,hitPoints,forbidden,owner,build"


def degraded_line():
    return (f"  ** DEGRADED: {PLUS_TOOL} unavailable ({SRC['why']}) -- fell back"
            f" to {STOCK_TOOL}, which carries NO forbidden state and NO bed"
            f" ownership. Those two indicators are absent below, not empty. **")


def normalise_plus(r):
    """Fold the leaner `get_cells_plus` payload back into the stock cell shape.

    Two differences that matter to the layers below, both of them deliberate
    compression on the companion's side:

    * zone/area descriptors are emitted ONCE in top-level dictionaries and
      referenced per cell by id, instead of repeated in full for every cell of
      the zone.  Layer 4 wants the descriptor, so put it back.
    * `fogged` is omitted when false, which every reader already treats
      correctly (`.get("fogged")`), so it is left alone.

    **`walkable` is no longer defaulted here, and its absence RAISES.**  It used
    to be `c.setdefault("walkable", True)`, because the tool emitted the key
    only when it was false.  That has not been true since 2026-09-02: the tool
    emits `walkable` -- and `passable` -- on EVERY cell, as a real boolean or
    the string "unknown", precisely so that nobody has to guess.  Keeping the
    default would mean that if the field ever went missing again, every missing
    cell would silently read as walkable, and layer 6 would downgrade a real
    BREACH to a near-breach -- the exact bug that was fixed.  So a missing
    `walkable` on a reply whose `fieldsApplied` says walkable was emitted is a
    contradiction in the payload, and it is raised as one.  block() catches it
    and falls back to the stock tool with the DEGRADED line, so the run says out
    loud that it lost data rather than drawing a wrong map.

    A reply with no `fieldsApplied` at all is a companion DLL older than
    2026-09-03; walkable was still promised on every cell from 09-02 onward, so
    the same check applies and a pre-09-02 DLL fails it loudly.  That is the
    intended outcome: it means the installed DLL is older than this file.
    """
    zones, areas = r.get("zones") or {}, r.get("areas") or {}
    applied = r.get("fieldsApplied")
    want_walkable = applied is None or "walkable" in applied
    for c in r.get("cells") or []:
        if want_walkable and "walkable" not in c:
            raise rim.BridgeError(
                f"cell ({c.get('x')},{c.get('z')}) has no 'walkable' key, but"
                f" fieldsApplied={applied} says the field was emitted."
                " get_cells_plus has promised walkable on every cell since"
                " 2026-09-02; a missing one is a payload bug, not a false"
                " value, and defaulting it would hide a roof breach.")
        zid = c.get("zoneId")
        if zid:
            c["zone"] = zones.get(zid) or {"id": zid}
        aids = c.get("areaIds")
        if aids:
            c["areas"] = [areas.get(a) or {"id": a} for a in aids]
    return r


def scan(x0, z0, x1, z1, want_plus=True, fields=None, thing_fields=None,
         sparse=False):
    """Sweep the rect in 32x32 blocks -- 1024 cells, the cap on both tools.

    The companion is tried once.  If it is not there (stock RimBridgeServer, or
    the DLL removed from `<game root>\\BridgeTools\\HomeBridge\\`), this drops to
    the stock tool for the whole run and says so on stdout immediately, rather
    than retrying per block and rendering half a map from each.

    `fields` / `thing_fields` / `sparse` are handed straight to block(); the
    defaults are map.py's own CELL_FIELDS and THING_FIELDS and every cell.
    """
    if not want_plus:
        SRC.update(plus=False, tool=STOCK_TOOL,
                   why="--stock was passed on the command line")
        print(degraded_line())
    grid = {}
    for bx in range(x0, x1, BLOCK):
        for bz in range(z0, z1, BLOCK):
            for c in block(bx, bz, min(BLOCK, x1 - bx), min(BLOCK, z1 - bz),
                           fields=fields, thing_fields=thing_fields,
                           sparse=sparse):
                grid[(c["x"], c["z"])] = c
    return grid


def block(bx, bz, w, h, fields=None, thing_fields=None, sparse=False):
    """One <=1024-cell block, companion first, stock on any failure. -> cells[].

    Pulled out of scan() on Aug 31 so watch.py's threat sweep could have the
    same payload and the same loud fallback. That sweep was still on the stock
    tool and paid 649KB per 1024 cells for it -- 6.8 MB of JSON per step,
    measured, which is most of the "big stutter every 5 seconds" M was
    watching. The companion answers the same question in 152KB.

    Three optional narrowings, all of them arguments on the companion tool and
    none of them available on the stock one (2026-09-03, WANTED 16):

      fields        comma-separated per-cell field names, or None for map.py's
                    own CELL_FIELDS -- what the seven layers read and no more.
                    Pass "all" to get the whole payload back.
      thing_fields  the same for the keys inside things[]; None means
                    THING_FIELDS.  `defName` always comes back.
      sparse        drop cells that carry none of the SELECTED content -- no
                    things, no designations, no zone, no areas.  For callers
                    that want "what is in this rect" rather than a picture of
                    it (inv.py, watch.py).  NOT for map.py's own layers: fog
                    is not content, so an empty fogged cell is dropped, and
                    the grids need every cell.

    All three are dropped on the stock-tool fallback, which has no such
    arguments: the fallback then returns MORE than was asked for (every field,
    every cell), never less.  A caller that filters what it reads is unaffected;
    a caller that assumed sparse would shrink its loop just gets a longer one.
    """
    args = {"x": bx, "z": bz, "width": w, "height": h}
    r = None
    if SRC["plus"]:
        plus = dict(args)
        plus["fields"] = CELL_FIELDS if fields is None else fields
        plus["thingFields"] = THING_FIELDS if thing_fields is None else thing_fields
        if sparse:
            plus["sparse"] = True
        try:
            r = rim.game(PLUS_TOOL, plus)
            # A refusal comes back as a dict with no `cells` key, and
            # `.get("cells") or []` would read that as an empty region --
            # the silent zero rim.py already raises on. Same guard here
            # for a reply that is merely the wrong shape.
            if not isinstance(r, dict) or "cells" not in r:
                raise rim.BridgeError(
                    "no 'cells' in the reply: " + str(r)[:160])
            r = normalise_plus(r)
        except Exception as e:
            SRC.update(plus=False, tool=STOCK_TOOL,
                       why=f"{type(e).__name__}: {str(e)[:160]}")
            print(degraded_line())
            r = None
    if r is None:
        # The stock tool takes the rectangle and nothing else.
        r = rim.game(STOCK_TOOL, args)
    return r.get("cells") or []


def scan_rects(rects, fields=None, thing_fields=None, sparse=False):
    """Sweep several rectangles, fetching each identical block only once.

    watch.py asks for a box around every colonist, and colonists stand near each
    other, so the boxes overlap. Tiling is kept rect-relative (not snapped to a
    global 32-grid) because snapping sounds like better dedup and measures
    worse: aligning two 61x61 boxes to the grid turns 4 blocks each into 9 each,
    and the union goes UP, from 8 to 12.

    `fields` / `thing_fields` / `sparse` are block()'s, unchanged.
    """
    grid, done = {}, set()
    for (x0, z0, x1, z1) in rects:
        for bx in range(x0, x1, BLOCK):
            for bz in range(z0, z1, BLOCK):
                spec = (bx, bz, min(BLOCK, x1 - bx), min(BLOCK, z1 - bz))
                if spec in done:
                    continue
                done.add(spec)
                for c in block(*spec, fields=fields,
                               thing_fields=thing_fields, sparse=sparse):
                    grid[(c["x"], c["z"])] = c
    return grid


def colonists():
    """Ours, by position.  The cell payload has no faction field at all.

    A Verse.Pawn in a cell reports exactly defName/label/className/hitPoints/
    stuffDefName and the blueprint flags -- no faction, no owner (verified Aug
    30, in the same sweep that proved `forbidden` is absent from the STOCK
    payload; the companion adds forbidden and bed owners, not faction).
    `list_colonists`
    is the free half of the answer: it is documented "player-controlled
    colonists" only, and has no parameter that widens it, so it can never name
    a raider.  Allegiance is a positional join, and anything it cannot resolve
    goes to identify() or is drawn unknown -- never guessed.
    """
    out = {}
    try:
        for c in rim.game("rimworld/list_colonists", {}).get("colonists") or []:
            if c.get("dead"):
                continue
            p = c.get("position") or {}
            out[(p.get("x"), p.get("z"))] = c
    except Exception as e:
        print(f"!! list_colonists failed ({e}) -- NO colonist can be identified;"
              " every human below is drawn as unknown.")
    return out


IDENT_CAP = 24


def identify(cells):
    """Who is that pawn?  One list call.  No clicks since 2026-08-31.

    This used to click every unexplained pawn, because `click_cell`'s
    `selectionAfter...details` was the ONLY place the bridge exposed
    `factionHostileToPlayer`.  That made a map render a mutation: it changed the
    in-game selection, an armed architect designator could swallow it silently,
    and it was capped at 24 pawns because each one cost a round trip.

    `home/list_pawns` (companion DLL, see `pawns.py`) reports faction and
    hostility as plain fields on a read, for the whole map, in one call.  So the
    cap, the selection change and the clearing afterwards are all gone.  The
    return shape is unchanged, and the third element is now always 0 clicks --
    kept in the signature so the footers that print it still read true.

    Falls back to the old clicking path ONLY if the companion is missing, and
    says so, because a silently unidentified hostile is the failure this whole
    layer exists to prevent.
    """
    try:
        import pawns as pawnlib
        found = {}
        for p in pawnlib.all_pawns():
            pos = p.get("position") or {}
            key = (pos.get("x"), pos.get("z"))
            if key not in cells:
                continue
            found[key] = {
                "className": "Verse.Pawn",
                "label": p.get("name"),
                "kindDef": p.get("kindDef"),
                "faction": p.get("faction"),
                "factionHostileToPlayer": p.get("hostile"),
                "factionIsPlayer": p.get("isColonist"),
                "animal": p.get("animal"),
                "mechanoid": p.get("mechanoid"),
                "insect": (p.get("defName") or "") in INSECTS,
                "downed": p.get("downed"),
                "job": p.get("job"),
                "position": {"x": pos.get("x"), "z": pos.get("z")},
            }
        return found, 0, 0
    except Exception as e:
        print(f"  ** home/list_pawns unavailable ({str(e)[:90]}) -- falling back"
              f" to click_cell identification, which MUTATES the selection. **")
        return _identify_by_clicking(cells)


def _identify_by_clicking(cells):
    """The pre-2026-08-31 path.  One click_cell per pawn.  Verified Aug 31.

    `click_cell` returns `selectionAfter.selectedObjects[].details`, and for a
    pawn that record carries `faction`, `factionHostileToPlayer`,
    `factionIsPlayer`, `animal`, `insect`, `mechanoid`, `kindDef`, `downed` and
    `job` -- everything the cell sweep is missing, in ONE call, no inspect tab
    and no screenshot.  Measured on both colonists in Lampblack day 21.

    Only pawns `list_colonists` could not explain are clicked, so the ordinary
    case (nobody hostile on the map) costs nothing and mutates nothing.  It IS
    a mutation when it fires: it changes the in-game selection, so the
    selection is cleared afterwards and the count is printed.
    """
    found, clicked, capped = {}, 0, 0
    for (x, z) in cells:
        if clicked >= IDENT_CAP:
            capped += 1
            continue
        clicked += 1
        try:
            # Through pick: a click at a cell the camera is not looking at
            # does nothing and still reports success, so this fallback used to
            # be able to identify nobody and say nothing about why.
            import pick
            r = pick.click(x, z, announce=False)
        except Exception:
            continue
        for o in ((r.get("selectionAfter") or {}).get("selectedObjects") or []):
            d = o.get("details") or {}
            p = d.get("position") or {}
            # An armed architect designator silently swallows click_cell, and a
            # cell with several things can hand back the wrong one. Only accept
            # a pawn that is actually standing where we clicked.
            if d.get("className", "").endswith(".Pawn") and \
                    (p.get("x"), p.get("z")) == (x, z):
                found[(x, z)] = d
    if clicked:
        try:
            rim.game("rimworld/clear_selection", {})
        except Exception:
            pass
    return found, clicked, capped


HOSTILE_ALERT = ("raid", "hostile", "enem", "siege", "manhunt", "infestation",
                 "mech", "insect", "attack", "threat")


def hostile_alerts():
    """A second, independent channel for 'is something attacking'.

    Map-wide and unbounded by the rectangle, which is the point: the sweep can
    only see inside the bounds, and what kills a colony walks in from outside
    them.  Cheap -- one call.
    """
    try:
        out = []
        for a in rim.game("rimworld/list_alerts", {}).get("alerts") or []:
            lab = a.get("label") or ""
            if any(k in lab.lower() for k in HOSTILE_ALERT):
                out.append(f"{lab} [{a.get('priority')}]")
        return out
    except Exception as e:
        return [f"(list_alerts failed: {e})"]


# ---------------------------------------------------------------- drawing ---
def render(x0, z0, x1, z1, grid, glyph):
    rows = ["     " + "".join(str(x // 10 % 10) for x in range(x0, x1)),
            "     " + "".join(str(x % 10) for x in range(x0, x1))]
    for z in range(z1 - 1, z0 - 1, -1):
        rows.append(f"{z:4d} " + "".join(glyph(grid.get((x, z)), x, z)
                                         for x in range(x0, x1)))
    return "\n".join(rows)


# ------------------------------------------------------------ compaction ---
# M, Aug 30: 21KB per call is a real cost, because this output does not
# scroll away -- it stays in context and is re-read on every later turn.  A
# 48x48 layer with three things on it spends 2,400 characters saying "empty".
# So a layer with few drawn cells prints them as coordinates instead.
#
# Two layers never compact.  Layer 1 is nothing BUT spatial shape -- a roof
# line as a coordinate list is unreadable and unusable, and reading the roof
# line wrong is the failure this file exists for.  Layer 4 is the same
# argument: its value is the wall of '#' against the '-', seen at once; a
# stockpile is never sparse in the sense that matters.
SPARSE = 12               # drawn cells at or below which a layer compacts
FULL = [False]            # --full: never compact
CONTEXT = (" ", ".", "?")  # glyphs that are background, not content


def draw(x0, z0, x1, z1, grid, g, compact=True, context=CONTEXT):
    """Render the layer, then decide whether the grid is worth printing.

    The glyph function runs over every cell either way -- it is what fills the
    tallies the footers print -- so compaction changes what is SHOWN and never
    what was counted.  `context` is the layer's own set of background glyphs;
    layer 5 widens it so its natural-rock characters do not count as content.
    """
    seen = []

    def wrap(c, x, z):
        s = g(c, x, z)
        if s not in context:
            seen.append((x, z, s))
        return s

    txt = render(x0, z0, x1, z1, grid, wrap)
    if compact and not FULL[0] and len(seen) <= SPARSE:
        print(f"  [COMPACTED: {len(seen)} drawn cell(s) in {x1-x0}x{z1-z0},"
              f" at or under the threshold of {SPARSE} -- the grid is not"
              " printed. `--full` prints it. Footers below are unchanged.]")
        by = collections.defaultdict(list)
        for x, z, s in seen:
            by[s].append((x, z))
        for s in sorted(by):
            print(f"    {s}  " + "  ".join(f"({x},{z})" for x, z in sorted(by[s])))
        if not seen:
            print("    (nothing at all on this layer inside these bounds)")
    else:
        print(txt)


def keyline(keys, used):
    """The legend, filtered to what is actually on screen, so it never lies."""
    parts = [f"{s} {d}" for s, d in keys if s in used]
    return "  key: " + "   ".join(parts) if parts else "  key: (empty)"


def hdr(n, title, x0, z0, x1, z1):
    # The bounds ride on every layer header.  A cropped view that does not say
    # so is a view that can be mistaken for the whole map -- which is how the
    # day-one Allow sweep "checked out" while the whole base stayed forbidden.
    print(f"\n== LAYER {n}: {title} ==  x {x0}..{x1-1}  z {z0}..{z1-1}"
          f"  ({x1-x0}x{z1-z0})")


def counted(d, order=None):
    items = [(k, v) for k, v in d.items() if v]
    items.sort(key=lambda kv: (order.index(kv[0]) if order and kv[0] in order
                               else 99, -kv[1]))
    return " ".join(f"{k} {v}" for k, v in items)


ROCK_CTX = ("#", "S", ":")     # layer 5's natural-rock context glyphs


def ctx(b, rock_only=False):
    """Faint terrain context in layers 2-6, so a thing can be located.

    Safe by construction: a rock/wall cell cannot hold a loose item, a pawn, a
    stockpile cell or (for rock) a building, so this is not a collision being
    resolved -- it is a cell that has nothing to say in this layer.

    Layer 5 (`rock_only`) is the exception: that layer is read to tell a built
    wall from the mountain, so the mountain gets its own characters here,
    matching layer 1's.  They stay CONTEXT for compaction (see draw()) so a
    rocky view does not lose its coordinate list.
    """
    if rock_only:
        if b.get("smoothrock"):
            return "S"
        if b.get("vein"):
            return ":"
        if b.get("rock"):
            return "#"
        return " "
    return "." if any(b.get(k) for k in STRUCT) else " "


# ------------------------------------------------------------------ layers --
L1KEY = ([("#", "NATURAL rock (the mountain -- mine it)"),
          ("S", "SMOOTHED natural rock wall (still the mountain, not built)"),
          ("H", "CONSTRUCTED wall (a colonist built it -- never natural)"),
          ("/", "door")]
         + [(g, "ore vein: " + d) for g, _dn, d in ORE_KIND]
         + [("%", "ore vein, kind not in ORE_KIND (named in the footer)"),
            (".", "ground, OPEN SKY"), (",", "built floor, OPEN SKY"),
            ("-", "ground, ROOFED"), ("=", "built floor, ROOFED"),
            ("~", "water"), ("?", "fogged"), (" ", "no data")])


def layer1(x0, z0, x1, z1, grid, things, overlay=None):
    """Terrain and roof, and -- in the merged default view -- pawns on top.

    The overlay is a deliberate, announced exception to "layer 1 draws no
    things", asked for by M: a pawn as a coordinate list loses what the
    pawn is NEXT to, which is the whole question you look at a pawn for.  Roof
    still never loses to an item, a chunk or a building; it loses only to a
    pawn, only in this view, and the footer says how many cells it cost.
    `--full` prints the pure layer with nothing on top.
    """
    tally = collections.Counter()
    terrains = collections.Counter()
    ores = collections.Counter()
    hidden_blockers = under = 0

    def g(c, x, z):
        nonlocal hidden_blockers, under
        if c is None:
            tally[" "] += 1
            return " "
        if c.get("fogged"):
            tally["?"] += 1
            return "?"
        b = things[(x, z)]
        s = None
        # One character per ore; every ore def is counted for the footer even
        # when something else wins the cell.
        for dn in b.get("_ore") or []:
            if dn:
                ores[dn] += 1
        for k, sym in (("wall", "H"), ("door", "/"), ("smoothrock", "S"),
                       ("vein", None), ("rock", "#")):
            if b.get(k):
                s = sym or ore_glyph((b.get("_ore") or [None])[0])
                break
        if s is None:
            tc = terrain_class(c.get("terrainDefName"))
            terrains[(c.get("terrainDefName"), tc)] += 1
            roofed = bool(c.get("roofDefName"))
            if tc == "water":
                s = "~"
            elif tc == "floor":
                s = "=" if roofed else ","
            else:
                s = "-" if roofed else "."
            if c.get("walkable") is False:
                hidden_blockers += 1
        tally[s] += 1
        if overlay and (x, z) in overlay:
            under += 1
            return overlay[(x, z)]
        return s

    hdr(1, "terrain / roof" + (" + PAWNS on top" if overlay is not None else ""),
        x0, z0, x1, z1)
    draw(x0, z0, x1, z1, grid, g, compact=False)   # never compacts: see SPARSE
    keys = L1KEY + [k for k in L3KEY if k[0] in "@dXvu"] if overlay is not None         else L1KEY
    print(keyline(keys, set(tally) | set((overlay or {}).values())))
    print("  " + counted(tally, [s for s, _ in L1KEY]))
    if set("#SH") & set(tally):
        print("  '#' is NATURAL rock and 'S' is smoothed natural rock; 'H' is a"
              " CONSTRUCTED wall. They are never the same character.")
    if ores:
        print("  ore veins: " + " ".join(f"{ore_glyph(k)}:{k} x{v}"
                                         for k, v in ores.most_common())
              + ("   (a vein drawn '%' has no letter yet -- add it to ORE_KIND)"
                 if any(ore_glyph(k) == "%" for k in ores) else ""))
    elif any(tally.get(g_) for g_, _dn, _d in ORE_KIND) or tally.get("%"):
        print("  ore veins: drawn, but no def name was reported for them.")
    if terrains:
        top = sorted(terrains.items(), key=lambda kv: -kv[1])
        print("  terrains: " + " ".join(f"{n}={c} x{v}" for (n, c), v in top[:12])
              + (f"  (+{len(top)-12} more)" if len(top) > 12 else ""))
    if hidden_blockers:
        print(f"  ({hidden_blockers} cells drawn as walkable ground are"
              " walkable:false -- blocked by a tree or a building, layers 2/5)")
    if overlay is not None:
        print(f"  ({under} cell(s) have their terrain/roof HIDDEN under a pawn"
              " glyph in this merged view -- the pure layer 1, with nothing on"
              " top, is `--full`. Roof never loses to anything else.)")


def terrain_class(name):
    if not name:
        return "none"
    if any(h in name for h in WATER_HINT):
        return "water"
    if any(h in name for h in FLOOR_HINT):
        return "floor"
    return "ground"


L2KEY = [("!", "FIRE"), ("F", "FORBIDDEN thing (its kind is in the footer)"),
         ("$", "item"), ("%", "corpse"), ("h", "healroot"),
         ("b", "wild food plant"), ("c", "sown crop"), ("o", "chunk"),
         ("T", "tree"), ('"', "other plant / grass"), ("'", "filth"),
         (".", "rock/wall (context)"), ("?", "fogged"), (" ", "nothing")]
# priority within the layer, most actionable first
L2ORDER = [("fire", "!"), ("item", "$"), ("corpse", "%"), ("herb", "h"),
           ("wildfood", "b"), ("crop", "c"), ("chunk", "o"), ("tree", "T"),
           ("plant", '"'), ("filth", "'")]
L2KINDS = {k for k, _ in L2ORDER}


def forbid_line(fs, limit=14):
    """One label per forbidden thing, with coordinates, because 'there are 3'
    is not enough to go and unforbid them."""
    out = []
    for t, x, z, _k in fs[:limit]:
        lab = t.get("label") or t.get("defName") or "?"
        n = t.get("stackCount")
        # The label already ends in the stack count ("Gold x30"), so appending
        # stackCount printed "Gold x30 x30" on the first live run.
        if n and n != 1 and f"x{n}" not in lab:
            lab += f" x{n}"
        out.append(f"{lab} @{x},{z}")
    return "; ".join(out) + (f"  (+{len(fs)-limit} more)" if len(fs) > limit else "")


def layer2(x0, z0, x1, z1, grid, things, unknown_plants, forbid):
    tally = collections.Counter()
    kinds = collections.Counter()
    hidden = 0

    def g(c, x, z):
        nonlocal hidden
        if c is None:
            return " "
        if c.get("fogged"):
            return "?"
        b = things[(x, z)]
        n = sum(b.get(k, 0) for k, _ in L2ORDER)
        if not n:
            return ctx(b)
        hidden += n - 1
        for kk, _ in L2ORDER:
            kinds[kk] += b.get(kk, 0)
        # Fire first -- it is the only thing on this layer more urgent than
        # "the colony cannot touch this".  Then FORBIDDEN, above every kind:
        # a forbidden stack is invisible to hauling and to every food count,
        # which is how 145 berries and a pile of steel sat unused for days
        # while the colony read as starving.  The kind is not lost, it is one
        # line down in the footer.
        if b.get("fire"):
            s = "!"
        elif b.get("_forbid_item"):
            s = "F"
        else:
            s = next(sym for k, sym in L2ORDER if b.get(k))
        tally[s] += 1
        return s

    hdr(2, "items -- loose things (className is not a Building)", x0, z0, x1, z1)
    draw(x0, z0, x1, z1, grid, g)
    print(keyline(L2KEY, set(tally) | {".", " "}))
    items_footer(tally, kinds, hidden, unknown_plants, forbid, L2KEY)


def items_footer(tally, kinds, hidden, unknown_plants, forbid, keys,
                 generic='"'):
    print("  cells drawn " + counted(tally, [s for s, _ in keys])
          + "   |  things " + counted(kinds, [k for k, _ in L2ORDER]))
    print("  plant kinds are SPECIES, not ripeness -- growth is not in the"
          " payload (a plant label's percentage is hit points).")
    fs = [f for f in forbid if f[3] in L2KINDS]
    if not SRC["plus"]:
        print("  ** forbidden state is UNKNOWN for every thing above: this run"
              f" used {STOCK_TOOL}, which does not carry it. 'F' cannot appear"
              " here at all -- see the DEGRADED line at the top. **")
    elif fs:
        print(f"  FORBIDDEN: {len(fs)} loose things, drawn 'F' -- the colony"
              f" will not haul, eat or use any of them: " + forbid_line(fs))
    else:
        print("  forbidden: 0 of the loose things here. Checked, not assumed --"
              f" {PLUS_TOOL} emits forbidden true OR false on everything that"
              " can carry the flag.")
    if unknown_plants:
        print(f"  ('{generic}' includes " + str(sum(unknown_plants.values()))
              + " plants of unlisted species, drawn generic: "
              + " ".join(f"{k} x{v}" for k, v in unknown_plants.most_common(12))
              + " -- add any harvestable one to WILD_FOOD/CROP in this file)")
    if hidden:
        print(f"  ({hidden} further items share a cell with the one drawn and"
              " have no character of their own -- `inv.py` lists them)")


L3KEY = [("@", "colonist"), ("d", "colonist DOWNED"), ("X", "HOSTILE"),
         ("v", "non-colonist, not hostile (visitor/trader/prisoner)"),
         ("u", "pawn of UNKNOWN faction (never identified)"),
         ("?", "fogged"),
         (".", "rock/wall (context)"), (" ", "nothing")]
# 'u', not '?': '?' is fogged in every other layer AND was this layer's own
# fogged glyph, so an unidentified human and an unseen cell printed the same
# character. Found while overlaying pawns onto layer 1, where the same
# collision would have made a fogged cell look like a stranger.
RANK = "Xdu@v"          # what wins the cell when two pawns share it


def layer3(x0, z0, x1, z1, grid, things, mine, do_ident=True, emit_grid=True):
    """emit_grid=False: identify everyone and return (pawn glyph map, footers).

    The merged default view draws pawns onto layer 1, so the classification has
    to happen BEFORE that grid renders while the footers -- who each pawn is,
    how allegiance was decided, the alert channel -- have to print AFTER it.
    Hence the split: the map comes back, the footers come back as a closure.
    """
    # Pass 1: who is standing where, and which of them list_colonists explains.
    pawn_cells, unexplained = {}, []
    for (x, z), b in things.items():
        if not (x0 <= x < x1 and z0 <= z < z1):
            continue
        for t in b.get("_pawns") or []:
            pawn_cells.setdefault((x, z), []).append(t)
            if (x, z) not in mine and (x, z) not in unexplained:
                unexplained.append((x, z))
    ident, clicked, capped = ({}, 0, 0)
    if do_ident and unexplained:
        ident, clicked, capped = identify(unexplained)

    tally = collections.Counter()
    drawn, animals, animal_where = [], collections.Counter(), []

    def classify(t, x, z):
        """-> (glyph or None-for-animal, name, note).  Never a guess when the
        game has been asked; a guess that says so when it has not."""
        dn = t.get("defName") or ""
        who = mine.get((x, z))
        if who is not None and dn == "Human":
            return ("d" if who.get("downed") else "@"), who.get("name"), "colonist"
        d = ident.get((x, z))
        if d is not None:
            nm = d.get("name") or d.get("label") or dn
            fac = d.get("faction") or "no faction"
            if d.get("animal"):
                return None, nm, fac + (" HOSTILE" if d.get("factionHostileToPlayer")
                                        or d.get("mentalState") else "")
            if d.get("factionIsPlayer"):
                return ("d" if d.get("downed") else "@"), nm, "colonist (" + fac + ")"
            if d.get("factionHostileToPlayer") or d.get("mechanoid") or d.get("insect"):
                return "X", nm, "HOSTILE, " + fac
            return "v", nm, "not hostile, " + fac
        # Not asked, or the click did not land. Only two things are knowable
        # from the sweep alone, and both are knowable for certain.
        if dn.startswith("Mech_") or dn in INSECTS:
            return "X", (t.get("label") or dn), "hostile by defName (mech/insect)"
        if dn != "Human":
            return None, dn, "animal by defName (not identified)"
        return "u", (t.get("label") or dn), "UNIDENTIFIED human"

    def g(c, x, z):
        if c is None:
            return " "
        if c.get("fogged"):
            return "?"
        b = things[(x, z)]
        if (x, z) not in pawn_cells:
            return ctx(b)
        best = None
        for t in pawn_cells[(x, z)]:
            kind, name, note = classify(t, x, z)
            if kind is None:
                animals[name] += 1
                animal_where.append((name, note, x, z))
                continue
            drawn.append((kind, name, note, x, z))
            if best is None or RANK.index(kind) < RANK.index(best):
                best = kind
        if best is None:
            return ctx(b)
        tally[best] += 1
        return best

    pawnmap = {}
    if emit_grid:
        hdr(3, "pawns -- colonists + hostiles; animals excluded [M]",
            x0, z0, x1, z1)
        draw(x0, z0, x1, z1, grid, g)
        print(keyline(L3KEY, set(tally) | {".", " "}))
    else:
        # Same glyph function, run only where a pawn stands: it fills the same
        # tallies, so the footers below are identical either way.
        for (x, z) in sorted(pawn_cells):
            sym = g(grid.get((x, z)), x, z)
            if sym not in CONTEXT:
                pawnmap[(x, z)] = sym

    def footers():
        if not emit_grid:
            print("  pawns (drawn on the terrain grid above):")
        for kind, name, note, x, z in sorted(drawn):
            print(f"    {kind} {name} @{x},{z}   ({note})")
        if not drawn:
            print("    (no colonists and no hostiles in these bounds)")
        if animals:
            print("  (" + str(sum(animals.values())) + " animals not drawn [M]: "
                  + " ".join(f"{k} x{v}" for k, v in animals.most_common(10))
                  + ")")
            for name, note, x, z in animal_where[:10]:
                print(f"        {name} @{x},{z}  ({note})")
        # How allegiance was decided, every run, in the output. The cell payload
        # has no faction field; everything above it comes from somewhere else,
        # and the reader has to be able to see which.
        known = len(set(pawn_cells) & set(mine))
        print(f"  faction source: list_colonists explained {known}"
              f" of {len(pawn_cells)} pawn cells; home/list_pawns explained"
              f" {len(ident)} more"
              + (f"; then click_cell identified some in {clicked} call(s),"
                 " changing and clearing the selection." if clicked else
                 " -- one read, no clicks, no selection change."))
        if capped:
            print(f"  ** {capped} pawn cells were NOT identified: the cap is"
                  f" {IDENT_CAP} clicks. They are drawn 'u'. **")
        if not do_ident and unexplained:
            print(f"  ** --no-who: {len(unexplained)} pawn cells left"
                  " unidentified and drawn 'u'. **")
        al = hostile_alerts()
        print("  list_alerts (map-wide, NOT limited to these bounds): "
              + ("; ".join(al) if al else
                 "nothing matching raid/hostile/manhunter"))

    if emit_grid:
        footers()
        return None
    return pawnmap, footers


L4KEY = [("#", "stockpile cell OCCUPIED"), ("-", "stockpile cell free"),
         (".", "rock/wall (context)"), (" ", "not a stockpile")]


def layer4(x0, z0, x1, z1, grid, things):
    tally = collections.Counter()
    per_zone = collections.defaultdict(lambda: [0, 0])   # [occupied, free]
    other_zones = collections.Counter()
    softcells = [0]

    def g(c, x, z):
        if c is None:
            return " "
        if c.get("fogged"):
            return "?"
        b = things[(x, z)]
        zone = c.get("zone")
        if not zone:
            return ctx(b)
        if "Zone_Stockpile" not in (zone.get("className") or ""):
            other_zones[zone.get("label") or zone.get("className")] += 1
            return ctx(b)
        # Occupied == something that actually stops a haul landing here: a
        # haulable thing or a building.  This is the boulder view -- 9 of 12
        # pantry cells full of sandstone chunks is what "Days worth of food in
        # storage: 0" looked like from the inside.
        #
        # Filth and growing plants do NOT block storage in RimWorld: an item is
        # stored straight over grass. Counting them would have called a grassy
        # dumping zone full -- the exact false alarm this layer exists to
        # prevent, running the other way. Measured Aug 31: it moved the dumping
        # zone from 32/47 to 22/47.
        blocked = (sum(b.get(k, 0) for k in BLOCKS_HAUL)
                   + sum(b.get(k, 0) for k in BUILDY))
        soft = sum(b.get(k, 0) for k in ("plant", "crop", "wildfood", "herb",
                                         "filth", "fire"))
        if not blocked and soft:
            softcells[0] += 1
        s = "#" if blocked else "-"
        per_zone[zone.get("label") or zone.get("id")][0 if blocked else 1] += 1
        tally[s] += 1
        return s

    hdr(4, "stockpiles -- occupancy", x0, z0, x1, z1)
    draw(x0, z0, x1, z1, grid, g, compact=False)   # never compacts: see SPARSE
    print(keyline(L4KEY, set(tally) | {".", " "}))
    stock_footer(per_zone, softcells, other_zones)


def stock_footer(per_zone, softcells, other_zones):
    for name, (occ, free) in sorted(per_zone.items()):
        n = occ + free
        print(f"    {name}: {occ}/{n} occupied, {free} free"
              + ("   <-- FULL" if free == 0 else ""))
    if not per_zone:
        print("    (no stockpile cells in these bounds)")
    print("  occupied = a haulable thing (item/corpse/chunk/tree) or a building"
          " on the cell. The counts above are only the part of each zone inside"
          " these bounds, not the whole zone. This layer never compacts: the"
          " point of it is seeing the block of stored cells against the free"
          " ones at once.")
    if softcells[0]:
        print(f"  ({softcells[0]} zone cells hold ONLY plants, filth or fire and"
              " are counted FREE -- those do not stop a haul in RimWorld)")
    if other_zones:
        print("  (" + str(sum(other_zones.values())) + " cells belong to"
              " non-stockpile zones, not drawn: "
              + " ".join(f"{k} x{v}" for k, v in other_zones.most_common(6)) + ")")


# ---------------------------------------------- merged layer 2 + layer 4 ---
# M's idea: items and stockpiles are the same question asked twice --
# "what is lying here" and "is the place meant for it full" -- so the default
# view merges them and distinguishes by CASE.  UPPERCASE = the thing is inside
# a stockpile zone (it is stored); lowercase = it is lying loose outside every
# zone.
#
# The design decision that needed making: the pure layer 2 draws several kinds
# with punctuation ($ % " ' T), which has no case.  So the merged grid uses a
# LETTER for every kind that can be stored, and keeps punctuation only for the
# three that are never a case question:
#   '!' fire and 'F' forbidden outrank the zone question -- what matters about
#       a forbidden stack is that nobody may touch it, in or out of a zone;
#   '"' plant and "'" filth are never "stored" at all -- they do not block a
#       haul, which is exactly why layer 4 counts their cells FREE.
# The merged alphabet is printed in the key line on every run, so nothing here
# has to be remembered.
L24 = {"item": "i", "corpse": "x", "herb": "h", "wildfood": "b", "crop": "c",
       "chunk": "o", "tree": "t"}
L24KEY = [("!", "FIRE"), ("F", "FORBIDDEN thing (kind in the footer)"),
          ("i", "item, loose"), ("I", "item, STORED (in a stockpile)"),
          ("x", "corpse, loose"), ("X", "corpse, STORED"),
          ("h", "healroot"), ("H", "healroot, in a stockpile"),
          ("b", "wild food plant"), ("B", "wild food, in a stockpile"),
          ("c", "sown crop"), ("C", "crop, in a stockpile"),
          ("o", "chunk, loose"), ("O", "chunk, STORED (this is the boulder"
                                       " view -- chunks fill a pantry)"),
          ("t", "tree"), ("T", "tree, in a stockpile"),
          ('"', "plant / grass (never cased: does not block a haul)"),
          ("'", "filth (never cased: does not block a haul)"),
          ("#", "stockpile cell blocked by a BUILDING"),
          ("-", "stockpile cell FREE"),
          (".", "rock/wall (context)"), ("?", "fogged"),
          (" ", "nothing, and not a stockpile")]


def layer24(x0, z0, x1, z1, grid, things, unknown_plants, forbid):
    tally = collections.Counter()
    kinds = collections.Counter()
    per_zone = collections.defaultdict(lambda: [0, 0])   # [occupied, free]
    other_zones = collections.Counter()
    softcells = [0]
    hidden = 0

    def g(c, x, z):
        nonlocal hidden
        if c is None:
            return " "
        if c.get("fogged"):
            return "?"
        b = things[(x, z)]
        zone = c.get("zone")
        stock = bool(zone) and "Zone_Stockpile" in (zone.get("className") or "")
        if zone is not None and not stock:
            other_zones[zone.get("label") or zone.get("className")] += 1
        blocked = 0
        if stock:
            # Identical bookkeeping to the pure layer 4, so the occupancy
            # numbers below are the same numbers either way.
            blocked = (sum(b.get(k, 0) for k in BLOCKS_HAUL)
                       + sum(b.get(k, 0) for k in BUILDY))
            soft = sum(b.get(k, 0) for k in ("plant", "crop", "wildfood",
                                             "herb", "filth", "fire"))
            if not blocked and soft:
                softcells[0] += 1
            per_zone[zone.get("label") or zone.get("id")][0 if blocked else 1] += 1
        n = sum(b.get(k, 0) for k, _ in L2ORDER)
        if n:
            hidden += n - 1
            for kk, _ in L2ORDER:
                kinds[kk] += b.get(kk, 0)
            if b.get("fire"):
                s = "!"
            elif b.get("_forbid_item"):
                s = "F"
            else:
                k = next(k for k, _ in L2ORDER if b.get(k))
                s = L24.get(k, {"plant": '"', "filth": "'"}.get(k, "?"))
                if stock and s in L24.values():
                    s = s.upper()
            tally[s] += 1
            return s
        if stock:
            s = "#" if blocked else "-"
            tally[s] += 1
            return s
        return ctx(b)

    hdr("2+4", "items ON stockpiles -- UPPERCASE = stored, lowercase = loose",
        x0, z0, x1, z1)
    draw(x0, z0, x1, z1, grid, g, compact=False)   # holds layer 4: never compacts
    print(keyline(L24KEY, set(tally) | {".", " "}))
    items_footer(tally, kinds, hidden, unknown_plants, forbid, L24KEY)
    stock_footer(per_zone, softcells, other_zones)


L5KEY = [("H", "CONSTRUCTED wall (a colonist built it)"),
         ("/", "door"), ("B", "bed, colonist ASSIGNED"),
         ("b", "bed, unassigned (or ownership unknown -- see footer)"),
         ("w", "worktable"),
         ("s", "shelf/storage -- PassThroughOnly: nothing stands or drops here"),
         ("c", "container/grave"), ("t", "turret/trap"),
         ("f", "fire source"),
         ("e", "power SOURCE or powered furniture (battery, generator, lamp)"),
         ("=", "power CONDUIT -- wire only, neither a source nor a load"),
         ("!", "FORBIDDEN building (its kind is in the footer)"),
         ("%", "other building"),
         ("-", "BLUEPRINT (not built)"), ("+", "frame (being built)"),
         ("T", "TREE (blocks building -- must be chopped)"),
         ("*", "wild food plant (berry bush etc. -- building destroys it)"),
         ("h", "healroot (building destroys it)"),
         ("^", "sown CROP (building destroys it)"),
         ('"', "other plant / grass (context -- cleared by building)"),
         ("#", "NATURAL rock (context -- NOT a built wall)"),
         ("S", "SMOOTHED natural rock wall (context -- NOT a built wall)"),
         (":", "ore vein (context -- kinds are in layer 1)"),
         (" ", "nothing built here")]

# Plants ON the build layer.  A berry bush standing on a build target was
# invisible on exactly the layer used to choose the cell (turn 13, caught by
# M from the screen, by no instrument).  The characters are NOT layer
# 2's: 'b' is a BED here and 'c' is a container, so the two harvestables that
# would have collided get '*' and '^' and the key says so on every drawing.
L5PLANT = [("herb", "h"), ("wildfood", "*"), ("crop", "^"), ("tree", "T"),
           ("plant", '"')]
# Grass is context: it is on almost every open cell outdoors, and a build layer
# that draws a quote mark on all of them stops being readable.  It still draws;
# it just does not count as content for compaction.
PLANT_CTX = ('"',)


def plant_glyph(b):
    """The plant character for a cell with nothing built on it, or None."""
    for kind, sym in L5PLANT:
        if b.get(kind):
            return sym
    return None


# Cells occupied by one bed of each vanilla def.  Nothing in either payload
# carries a thing id or a footprint, so two flush 1x1 sleeping spots and one
# 1x2 bed are the same shape to a cell sweep -- which is not hypothetical: the
# first live run merged the save's TWO sleeping spots into one and reported
# "1 assigned / 2 free" where the truth was 1 and 3.  A def not listed here
# counts as one bed per block AND is named in the footer, the same fallback the
# plant list uses, so the list gets extended instead of silently miscounting.
BED_CELLS = {"SleepingSpot": 1, "AnimalSleepingSpot": 1, "Bed": 2,
             "Bedroll": 2, "HospitalBed": 2, "AnimalBed": 1,
             "DoubleBed": 4, "RoyalBed": 4, "DoubleBedroll": 4}


def merge_beds(cells):
    """A 2x2 bed is four cells of the same thing; count it once.

    Identity has to be inferred: 4-adjacent cells holding the same defName, the
    same owner list and the same hitPoints are one block of bed.  The block is
    then divided by the def's known footprint above.

    -> [((x, z), thing, cells_in_block, beds_in_block, footprint_known)]
    """
    pos = {(x, z): t for x, z, t in cells}

    def key(t):
        o = bed_owners(t)
        return (t.get("defName"), tuple(o) if o is not None else None,
                t.get("hitPoints"))

    seen, groups = set(), []
    for start in sorted(pos):
        if start in seen:
            continue
        k, stack, comp = key(pos[start]), [start], []
        seen.add(start)
        while stack:
            cx, cz = stack.pop()
            comp.append((cx, cz))
            for n in ((cx + 1, cz), (cx - 1, cz), (cx, cz + 1), (cx, cz - 1)):
                if n in pos and n not in seen and key(pos[n]) == k:
                    seen.add(n)
                    stack.append(n)
        t = pos[start]
        f = BED_CELLS.get(t.get("defName"))
        n = max(1, len(comp) // f) if f else 1
        groups.append((min(comp), t, len(comp), n, f is not None))
    return groups


def bed_footer(beds):
    """[M]: "a special indicator (capital or smth) to indicate a bed has an
    assigned colonist or not".  Buildable now off `ownerName`, which the
    companion emits for every bed and leaves explicitly null when free."""
    groups = merge_beds(beds)
    unknown_size = {g_[1].get("defName") for g_ in groups if not g_[4]}
    if not beds:
        print("    beds: none in these bounds")
        return
    if not SRC["plus"]:
        n = sum(g_[3] for g_ in groups)
        print(f"    beds: {n} ({len(beds)} cells) -- ASSIGNMENT NOT CHECKED on"
              " this run, every one drawn lowercase 'b':")
        for (x, z), t, cells, n, _known in groups:
            print(f"      b {t.get('label') or t.get('defName')} @{x},{z}"
                  + (f" (x{n})" if n > 1 else "") + "  owner UNKNOWN")
        print(f"      ** 'b' here means NOT CHECKED, not free: {STOCK_TOOL}"
              " has no owner field. See the DEGRADED line at the top. **")
    else:
        assigned = sum(g_[3] for g_ in groups if bed_owners(g_[1]))
        free = sum(g_[3] for g_ in groups if not bed_owners(g_[1]))
        print(f"    beds: {assigned} assigned / {free} free"
              f"   ({len(beds)} bed cells; a block of adjacent same-def,"
              " same-owner cells is divided by that def's footprint)")
        for (x, z), t, cells, n, _known in groups:
            o = bed_owners(t)
            print(f"      {'B' if o else 'b'}"
                  f" {t.get('label') or t.get('defName')} @{x},{z}"
                  + (f" (x{n}, {cells} cells)" if n > 1 else "")
                  + ("  -> " + ", ".join(o) if o else "  -- UNASSIGNED")
                  + ("  [medical]" if t.get("medical") else ""))
    if unknown_size:
        print("      (footprint unknown for " + " ".join(sorted(unknown_size))
              + " -- each adjacent block counted as ONE bed, which undercounts"
              " if two of them sit flush. Add it to BED_CELLS in this file.)")


def layer5(x0, z0, x1, z1, grid, things, forbid):
    tally = collections.Counter()
    hidden = 0
    others = collections.Counter()
    plants = collections.Counter()
    plant_kinds = collections.Counter()
    under_build = 0
    beds, forb = [], []
    doors, blockers = [], {}

    def g(c, x, z):
        nonlocal hidden, under_build
        if c is None:
            return " "
        if c.get("fogged"):
            return "?"
        b = things[(x, z)]
        builds = b.get("_builds") or []
        if not builds:
            # A cell with nothing built is where a build GOES, so what is
            # standing in it belongs on this layer (turn 13).
            plant = plant_glyph(b)
            if plant:
                plants[plant] += 1
                for kind, _sym in L5PLANT:
                    plant_kinds[kind] += b.get(kind, 0)
                return plant
            return ctx(b, rock_only=True)
        if plant_glyph(b):
            under_build += 1
        s = None
        for t in builds:
            if t.get("isBlueprint"):
                cand = "-"
            elif t.get("isFrame"):
                cand = "+"
            else:
                cand = build_glyph(t)
                if cand == "%":
                    others[t.get("label") or t.get("defName")] += 1
                if cand in "bB":
                    beds.append((x, z, t))
            # A forbidden door is a door nobody may open and a forbidden
            # building is one nobody may use; that outranks knowing which kind
            # it is, and the kind is named in the footer.
            if SRC["plus"] and t.get("forbidden"):
                forb.append((cand, t, x, z))
                cand = "!"
            if cand == "s":
                blockers[(x, z)] = t.get("label") or t.get("defName") or "storage"
            elif cand == "/":
                doors.append((x, z))
            # By RANK, not by order of arrival. The list order used to decide
            # it -- everything beat a wall or a door, and anything later beat
            # everything -- so a conduit running under a shelf drew the conduit
            # and gave no sign a building was there. Two shelves stood in
            # storeroom doorways that way and three turns walked past the
            # second (2026-09-07). Conduit now loses to everything.
            if s is None or BUILD_RANK.get(cand, 1) < BUILD_RANK.get(s, 1):
                s = cand
        hidden += len(builds) - 1
        tally[s] += 1
        return s

    hdr(5, "buildings / furniture + the plants standing where a build would go",
        x0, z0, x1, z1)
    # ROCK_CTX stays context so a rocky view still compacts; grass joins it for
    # the same reason (see PLANT_CTX).
    draw(x0, z0, x1, z1, grid, g, context=CONTEXT + ROCK_CTX + PLANT_CTX)
    print(keyline(L5KEY, set(tally) | set(plants) | set(ROCK_CTX) | {" "}))
    print("  " + counted(tally, [s for s, _ in L5KEY]))
    if plants:
        print("  PLANTS on cells with nothing built: "
              + counted(plants, [s for _, s in L5PLANT])
              + "   -- a build order on one of these destroys it. On THIS layer"
              " 'b' is a bed and 'c' a container, so a wild food plant draws"
              " '*' and a sown crop '^'; layer 2 uses 'b'/'c' for those.")
    if under_build:
        print(f"  ({under_build} further plant cell(s) already carry a"
              " building, blueprint or frame and are drawn as that; layer 2"
              " has the plant.)")
    inb = [b for (x, z), b in things.items() if x0 <= x < x1 and z0 <= z < z1]
    rock = sum(1 for b in inb if b.get("rock"))
    smooth = sum(1 for b in inb if b.get("smoothrock"))
    vein = sum(1 for b in inb if b.get("vein"))
    if rock or smooth or vein:
        print(f"  natural rock in these bounds: {rock} '#', {smooth} smoothed"
              f" 'S', {vein} ore vein ':' -- none of them is a built wall, and"
              " they are context here, so they are absent from a COMPACTED"
              " coordinate list above. 'H' is a wall a colonist built.")
    # ---- [M]: "a special indicator (capital or smth) to indicate a bed has an
    # assigned colonist or not".  Buildable now, off `ownerName`, which the
    # companion emits for every bed and leaves explicitly null when free.
    bed_footer(beds)
    if others:
        print("  ('%' = " + " ".join(f"{k} x{v}" for k, v in others.most_common(10))
              + ")")
    if hidden:
        print(f"  ({hidden} further buildings share a cell with the one drawn)")
    # A shelf is `PassThroughOnly` (vanilla's `ShelfBase`, Buildings_Furniture
    # .xml), so `GenGrid.Standable` is false on its cells: nothing can be put
    # down there and nobody stops there. A door that opens onto one has no
    # landing square on the far side, and the map used to give no sign of it --
    # two storeroom doors sat like this and three turns walked past the second
    # (2026-09-07).
    for dx, dz in doors:
        for nx, nz in ((dx + 1, dz), (dx - 1, dz), (dx, dz + 1), (dx, dz - 1)):
            if (nx, nz) in blockers:
                print(f"  !! door at {dx},{dz} opens onto {blockers[(nx, nz)]}"
                      f" at {nx},{nz} -- storage furniture is PassThroughOnly,"
                      " so that cell is NOT standable: nothing can be dropped"
                      " there and a haul route through this door has no landing"
                      " square.")
    fs = [f for f in forbid if f[3] in BUILDY]
    if not SRC["plus"]:
        print("  forbidden state is UNKNOWN for every building above -- '!'"
              " cannot appear on this run at all.")
    elif fs:
        print(f"  FORBIDDEN: {len(fs)} buildings, drawn '!': " + forbid_line(fs))
    else:
        print("  forbidden: 0 of the buildings here (checked, not assumed).")


L6KEY = [("M", "MINE"), ("H", "harvest"), ("C", "cut/chop plant"), ("h", "haul"),
         ("D", "deconstruct/uninstall"), ("S", "smooth"), ("X", "hunt/slaughter"),
         ("T", "tame"), ("A", "allow/forbid"), ("P", "plan"), ("O", "other order"),
         ("?", "UNRECOGNISED (named below)"), (".", "rock/wall (context)"),
         (" ", "no order")]


def layer6(x0, z0, x1, z1, grid, things):
    """What has been ORDERED, drawn where it will happen.

    [M].  The Aug 30 wall breach was a Mine designation that existed, that I had
    placed myself, and that no view put next to the roof line.  A designation is
    an intention with a location, which is exactly the thing a grid is for.
    """
    tally = collections.Counter()
    names = collections.Counter()
    unknown = collections.Counter()
    hidden = 0

    def g(c, x, z):
        nonlocal hidden
        if c is None:
            return " "
        if c.get("fogged"):
            return "?"
        b = things[(x, z)]
        ds = c.get("designations") or []
        if not ds:
            return ctx(b)
        best = None
        for d in ds:
            nm = desig_name(d)
            names[nm] += 1
            gl = desig_glyph(nm)
            if gl == "?":
                unknown[nm] += 1
            if best is None:
                best = gl
            if gl == "M":
                best = "M"
        hidden += len(ds) - 1
        tally[best] += 1
        return best

    hdr(6, "designations -- pending orders", x0, z0, x1, z1)
    draw(x0, z0, x1, z1, grid, g)
    # The breach check, because roofDefName has been in every cell payload all
    # along.  The rule itself lives in mine_breaches() so verify.py CAN ask it
    # too -- it does not yet; see the note there.
    breaches = mine_breaches(grid)
    print(keyline(L6KEY, set(tally) | {".", " "}))
    if names:
        print("  " + " ".join(f"{desig_glyph(k)}:{k} x{v}"
                              for k, v in names.most_common()))
    else:
        print("  (no designations at all inside these bounds)")
    if unknown:
        print("  ('?' = unmapped designation names: "
              + " ".join(f"{k} x{v}" for k, v in unknown.most_common(10))
              + " -- add them to DESIG_KIND)")
    if hidden:
        print(f"  ({hidden} further designations share a cell with the one drawn)")
    hard = [b for b in breaches if b[0] == "OPEN"]
    soft = [b for b in breaches if b[0] != "OPEN"]
    if hard:
        print("  !! BREACH: a Mine order on a ROOFED cell whose neighbour is"
              " OPEN WALKABLE GROUND -- this opens the house:")
        for _, x, z, nx, nz in hard:
            print(f"       mine ({x},{z}) opens onto ({nx},{nz})")
    if soft:
        print("  ~  near-breach: a Mine order on a ROOFED cell next to an"
              " unroofed but still solid cell (one rock from open sky):")
        for _, x, z, nx, nz in soft:
            print(f"       mine ({x},{z}) is beside unroofed rock ({nx},{nz})")
    if not breaches:
        print("  breach check: no Mine order inside these bounds touches an"
              " unroofed cell.")
    print("  (the breach check only sees cells inside these bounds -- a Mine"
          " order one tile outside them is not checked.)")


# ----------------------------------------------------------------- rooms ---
# Which room is each cell in.  Walls and other impassable solids draw '#';
# every LISTED room draws one key character, 0-9 then a-z, with a key line
# naming each.
#
# The key character is the room's index in `home/list_rooms`' whole-map census,
# NOT a view-local number, so the same room wears the same character in the
# layer, in `map.py rooms` and in `map.py room x z`.  Past 36 rooms there are no
# characters left: those draw '*' and are named by index in the footer, because
# a room silently drawn as another room's character is the exact class of lie
# this file exists to prevent.
#
# There is deliberately no room key FILE.  A room has no player-given name and
# `Room.ID` is reassigned every time a wall changes -- RimWorld destroys and
# remakes the room -- so a saved key file would be stale ids pointing at rooms
# that no longer exist, and it would look authoritative while being wrong.  The
# key is instead the game's OWN role label plus whoever the beds in it belong to
# ("Bedroom (Lucas)"), recomputed on every call.
ROOMS_TOOL = "home/list_rooms"
KEYCHARS = "0123456789abcdefghijklmnopqrstuvwxyz"

# Same shape as SRC: one dict every room footer reads, so a run that could not
# reach the tool says so in each place a reader might trust the silence.
ROOMS = {"ok": False, "why": "not asked for on this run", "reply": None}


def room_key(i):
    return KEYCHARS[i] if isinstance(i, int) and 0 <= i < len(KEYCHARS) else "*"


def fetch_rooms(args):
    """One `home/list_rooms` call. -> reply dict, or None having said why.

    A refusal comes back as a dict with no `rooms` key and `.get("rooms") or []`
    would read that as "this colony has no rooms", which is the silent zero this
    whole file is built against.  Same guard as block().
    """
    try:
        r = rim.game(ROOMS_TOOL, args)
        if not isinstance(r, dict) or "rooms" not in r:
            raise rim.BridgeError("no 'rooms' in the reply: " + str(r)[:160])
        ROOMS.update(ok=True, why="", reply=r)
        return r
    except Exception as e:
        ROOMS.update(ok=False, why=f"{type(e).__name__}: {str(e)[:160]}",
                     reply=None)
        print(f"  ** {ROOMS_TOOL} unavailable ({ROOMS['why']}) -- NO room can be"
              " named or drawn on this run. It is a companion tool; see"
              " rimworld\\companion\\INSTALL.md. **")
        return None


def room_label(r):
    """The room's name, which is the game's, not ours.  Never invented."""
    if not r:
        return "(a room the census did not list)"
    for k in ("name", "roleLabel", "gameLabel"):
        v = r.get(k)
        if v and str(v).lower() != "none":
            return v
    if r.get("psychologicallyOutdoors"):
        return "Outdoors (no role)"
    if r.get("isDoorway"):
        return "Doorway"
    return "(the game gave this room no role label)"


def room_grid_reader(reply):
    """-> f(x, z) giving the roomGrid value at a cell, or None.

    The grid is row-major over the rect the tool was ASKED for, so a cell
    outside that rect has no entry -- which reads as None, i.e. "not covered",
    never as "no room".  The footer prints how many cells fell in that case.
    """
    rows = (reply or {}).get("roomGrid") or []
    rect = (reply or {}).get("rect") or {}
    rx, rz = rect.get("x", 0), rect.get("z", 0)

    def at(x, z):
        i, j = z - rz, x - rx
        if i < 0 or j < 0 or i >= len(rows):
            return None
        row = rows[i]
        return row[j] if 0 <= j < len(row) else None
    return at


def room_keyline(rooms, used):
    """The tiny key list: one entry per room that actually has a cell drawn."""
    by = {r.get("index"): r for r in rooms}
    parts = []
    for idx in sorted(used, key=lambda i: (i is None, i)):
        r = by.get(idx)
        n = used[idx]
        total = (r or {}).get("cellCount")
        size = f"{n} cells" if total in (None, n) else f"{n}/{total} cells"
        parts.append(f"{room_key(idx)} {room_label(r)} {size}")
    if not parts:
        return "  key: (no listed room has a cell inside these bounds)"
    return "  key: " + "   ".join(parts)


L7KEY = [("#", "wall, natural rock or other impassable solid"),
         ("0", "a listed room -- 0-9 then a-z, one character each, named below"),
         ("*", "a listed room past the 36 key characters (named by index below)"),
         ("?", "fogged"),
         (" ", "no LISTED room here: open ground, a doorway, or outside")]


def rooms_footer(reply, used, unkeyed, uncovered):
    """Every filter states itself, and the layer checks itself.

    Two separate honesty jobs.  The filter: `home/list_rooms` deliberately does
    not list the outdoors mega-room (~49,000 cells) or the one-tile doorway
    rooms, so those cells draw blank -- and blank would otherwise be
    indistinguishable from "no room at all", which is what a wall's interior is.
    The check: every cell the grid claimed for a room is reconciled against the
    cells actually drawn, and the tool's own three counts are printed beside it.
    """
    if not reply:
        print("  ** no room data on this run -- every cell above is drawn from"
              " the cell sweep alone, and NO cell is claimed for a room. **")
        return

    rooms = reply.get("rooms") or []
    print(f"  census: {reply.get('roomCount')} rooms listed of"
          f" {reply.get('roomCountTotal')} the game knows about;"
          f" {reply.get('roomsOmitted')} omitted"
          f" ({reply.get('outdoorRoomsOmitted')} outdoors,"
          f" {reply.get('doorwaysOmitted')} doorways,"
          f" {reply.get('dereferencedRoomsOmitted')} dereferenced)."
          " Omitted rooms draw BLANK above, the same as no room at all --"
          " `python map.py rooms` lists them.")
    drawn = sum(used.values())
    claimed = reply.get("gridCellsInUnlistedRooms")
    print(f"  cells drawn as a room: {drawn};"
          f"  grid says {claimed} more belong to an omitted room,"
          f" {reply.get('gridCellsWithNoRoom')} to no room at all,"
          f" and {reply.get('gridCellsOutOfBounds')} were off the map.")
    if uncovered:
        print(f"  ** {uncovered} cell(s) inside these bounds had NO entry in the"
              " roomGrid -- the rect the tool answered is smaller than the rect"
              " drawn. Those cells are drawn as if they had no room. **")
    if unkeyed:
        print(f"  ** {len(unkeyed)} room(s) are past the {len(KEYCHARS)} key"
              " characters and all draw '*': "
              + " ".join(f"index {i} x{n}" for i, n in sorted(unkeyed.items()))
              + " -- `python map.py rooms` names them. **")
    absent = [r for r in rooms if r.get("index") not in used]
    if absent:
        print(f"  ({len(absent)} listed room(s) have no cell inside these bounds"
              " and are not in the key above)")
    for r in rooms:
        sk = r.get("skipped") or []
        if sk and r.get("index") in used:
            print(f"    ** room {room_key(r.get('index'))}"
                  f" {room_label(r)}: could not read " + "; ".join(sk) + " **")


def layer7(x0, z0, x1, z1, grid, things, reply):
    """Which room is this cell in.  Walls and solids '#', rooms 0-9 then a-z.

    Never compacts, for layer 1 and layer 4's reason: the content of this layer
    IS its spatial shape.  A room as a coordinate list is not a room.
    """
    at = room_grid_reader(reply)
    tally = collections.Counter()
    used = collections.Counter()
    unkeyed = collections.Counter()
    uncovered = [0]

    def g(c, x, z):
        if c is None:
            return " "
        if c.get("fogged"):
            tally["?"] += 1
            return "?"
        idx = at(x, z) if reply else None
        if idx is None and reply and not solid(things[(x, z)], c):
            # Distinguish "the grid said this cell has no room" from "the grid
            # never covered this cell".  Only the second is a gap in the data.
            if not room_grid_covers(reply, x, z):
                uncovered[0] += 1
        if idx is not None:
            used[idx] += 1
            s = room_key(idx)
            if s == "*":
                unkeyed[idx] += 1
            tally[s] += 1
            return s
        if solid(things[(x, z)], c):
            tally["#"] += 1
            return "#"
        return " "

    hdr(7, "rooms -- one key character per room", x0, z0, x1, z1)
    draw(x0, z0, x1, z1, grid, g, compact=False)   # never compacts: see SPARSE
    print(keyline([k for k in L7KEY if k[0] in "#*? "],
                  set(tally) | {"#", " "}))
    print(room_keyline((reply or {}).get("rooms") or [], used))
    rooms_footer(reply, used, unkeyed, uncovered[0])


def merged_room_key(x0, z0, x1, z1, grid, things, reply):
    """The rooms layer's key line, without the grid, for the merged default.

    Runs the same glyph function over the same cells -- so `used` is counted
    exactly as the drawn layer would count it -- and prints only the key and a
    one-line census. Nothing is tallied differently; the grid is simply not
    printed, which is stated on the line itself.
    """
    at = room_grid_reader(reply)
    used = collections.Counter()
    for z in range(z0, z1):
        for x in range(x0, x1):
            c = grid.get((x, z))
            if c is None or c.get("fogged"):
                continue
            idx = at(x, z) if reply else None
            if idx is not None:
                used[idx] += 1
    print("\n== ROOMS (layer 7, key only -- the grid is `--layers rooms` or"
          " `--full`) ==")
    print(room_keyline((reply or {}).get("rooms") or [], used))
    if reply:
        print(f"  {reply.get('roomCount')} of {reply.get('roomCountTotal')}"
              f" rooms listed; {reply.get('roomsOmitted')} omitted"
              f" ({reply.get('outdoorRoomsOmitted')} outdoors,"
              f" {reply.get('doorwaysOmitted')} doorways,"
              f" {reply.get('dereferencedRoomsOmitted')} dereferenced)."
              " `python map.py rooms` is the whole-map census;"
              " `python map.py room <x> <z>` is one cell's room in full.")
    else:
        print("  ** no room data on this run -- see the failure above. **")


def solid(b, c):
    """Impassable: a wall, natural rock, an ore vein, or anything the sweep
    itself calls unwalkable.  A door is NOT solid here -- it is passable, and it
    lives in its own doorway room, which the census does not list."""
    return bool(any(b.get(k) for k in ("rock", "smoothrock", "vein", "wall"))
                or c.get("walkable") is False)


def room_grid_covers(reply, x, z):
    rect = (reply or {}).get("rect") or {}
    rx, rz = rect.get("x"), rect.get("z")
    if rx is None or rz is None:
        return False
    return (rx <= x < rx + rect.get("width", 0)
            and rz <= z < rz + rect.get("height", 0))


def room_holds_colonist(r):
    """Is a COLONIST standing in this room right now.

    `pawns[]` is every spawned pawn whose own cell resolves to the room --
    animals, visitors and raiders alike -- so `isColonist` is the filter.  It
    can come back null when the game would not answer, and null is not treated
    as a colonist here; the room's own `pawns:` line still names everybody
    either way, so nobody is hidden by that choice.
    """
    return any(p.get("isColonist") for p in (r.get("pawns") or []))


def room_colonists(r):
    return [p.get("name") or "?" for p in (r.get("pawns") or [])
            if p.get("isColonist")]


def room_is_outdoorish(r):
    """A room whose temperature is the weather's, or a one-tile doorway.

    Three separate flags meaning three different things, any one of which makes
    a row useless in a coldest-first list: `outdoors` is
    Room.UsesOutdoorTemperature, so the number IS the weather;
    `psychologicallyOutdoors` is the mood flag the census itself filters on; and
    a doorway is the one-cell room the game makes for a door, not a place
    anybody lives.
    """
    return bool(r.get("outdoors") or r.get("psychologicallyOutdoors")
                or r.get("isDoorway"))


def temp_sort_key(r, warmest_first=False):
    """Coldest first, or warmest first -- UNREADABLE ahead of both.

    A room whose `temperature` came back null is the row that has to be looked
    at by hand, so it outranks every number in either direction: "we do not
    know" beats "we know and it is fine".  It is never printed as a 0 and never
    dropped.  The silent zero is the failure this stack keeps relearning.
    """
    t = r.get("temperature")
    if t is None:
        return (0, 0.0)
    return (1, -float(t) if warmest_first else float(t))


def outdoor_temp(reply):
    """(temperature, how) for the open air, or (None, why not).  No second call.

    `home/list_rooms` carries no map-wide outdoor temperature field at all.
    What it can carry is the outdoors mega-room's OWN row -- that room is
    Room.UsesOutdoorTemperature, so its temperature is the weather -- and that
    row is listed only when includeOutdoors was set.  So this reads it out of
    rooms[] when it is there and says plainly that it was not asked for when it
    is not, rather than making a second call for one number.
    """
    cands = [r for r in (reply.get("rooms") or [])
             if r.get("outdoors") and r.get("temperature") is not None]
    if not cands:
        if not reply.get("includeOutdoors"):
            return None, ("not in this reply -- `home/list_rooms` has no map-wide"
                          " outdoor field, and the outdoors mega-room, whose own"
                          " temperature IS the weather, was not listed."
                          " `--cold`, `--hot` and `--outdoors` list it")
        return None, ("UNREADABLE -- the outdoors room was listed and its own"
                      " temperature came back null")
    # The mega-room is the biggest of them; a sealed shed with a hole in the
    # roof is outdoor-slaved too and is not the weather station.
    best = max(cands, key=lambda r: r.get("cellCount") or 0)
    return best.get("temperature"), ("the outdoors mega-room's own row, %s cells"
                                     % best.get("cellCount"))


def room_summary_line(r, note=""):
    """One census line: key, name, where, how big, how warm, who, what."""
    e = r.get("extents") or {}
    where = ("x %s..%s z %s..%s (%sx%s)" % (
        e.get("x"), (e.get("x") or 0) + (e.get("width") or 1) - 1,
        e.get("z"), (e.get("z") or 0) + (e.get("height") or 1) - 1,
        e.get("width"), e.get("height"))) if e else "extents UNREADABLE"
    c = r.get("center") or {}
    temp = r.get("temperature")
    pawns = [p.get("name") for p in (r.get("pawns") or []) if p.get("name")]
    stock = [f"{s.get('label')} {s.get('cellsInRoom')}c"
             for s in (r.get("stockpiles") or [])]
    furn = [f"{t.get('label') or t.get('defName')}"
            + (f" x{t.get('count')}" if (t.get("count") or 1) > 1 else "")
            for t in (r.get("contents") or [])[:4]]
    more = (r.get("contentsNotListed") or 0) + max(
        0, len(r.get("contents") or []) - 4)
    parts = [f"{room_key(r.get('index'))} {room_label(r):<28s}",
             where,
             f"c({c.get('x')},{c.get('z')})",
             f"{r.get('cellCount')} cells",
             ("UNREADABLE C" if temp is None else f"{temp}C")]
    line = "  " + "  ".join(parts) + note
    if pawns:
        line += "\n      pawns: " + ", ".join(pawns)
    if stock:
        line += "\n      stockpiles: " + ", ".join(stock)
    if furn:
        line += "\n      furniture: " + ", ".join(furn) + (
            f"  (+{more} more kinds)" if more else "")
    elif r.get("contents") is not None:
        line += "\n      furniture: none"
    sk = r.get("skipped") or []
    if sk:
        line += "\n      ** could not read: " + "; ".join(sk) + " **"
    return line


# The area classes RimWorld keeps in `Map.areaManager`, worst-confused first.
# `Designator_AreaBuildRoof` and `Designator_AreaNoRoof` write these, NOT the
# designation manager -- which is why `act.py apply "Build roof area"` can print
# APPLIED and `map.py --layers desig` show nothing, correctly, in the same
# minute (2026-09-07).  Order is glyph priority on a cell in several areas.
AREA_KIND = [
    ("Area_BuildRoof", "R", "BUILD ROOF area -- colonists will roof these cells"),
    ("Area_NoRoof", "N", "NO ROOF area -- any roof here gets torn down"),
    ("Area_SnowClear", "s", "snow/sand clear area"),
    ("Area_SnowOrSandClear", "s", "snow/sand clear area"),
    ("Area_PollutionClear", "p", "pollution clear area"),
    ("Area_Home", "H", "HOME area -- cleaning, firefighting, repairs happen here"),
    ("Area_Allowed", "a", "an allowed area (movement restriction)"),
]


def area_glyph(descriptors):
    """The one character for a cell's area membership, and every area on it."""
    names = []
    for d in descriptors or []:
        if isinstance(d, dict):
            names.append((str(d.get("className") or ""),
                          str(d.get("label") or d.get("id") or "?")))
    for cls, sym, _ in AREA_KIND:
        for cn, _label in names:
            if cls == cn:
                return sym, names
    return ("a" if names else "."), names


def cmd_areas(argv):
    """`python map.py areas <x> <z> [W] [H]` -- the AREAS over a rectangle.

    The read `act.py apply "Build roof area"` points at.  An area is not a
    designation and layer 6 will never show one; this is the only view that
    does.  Coordinates are `x z WIDTH HEIGHT`, act.py's order, so the line
    act.py prints can be pasted.
    """
    nums = [int(v) for v in argv if v.lstrip("-").isdigit()]
    if len(nums) < 2:
        print("map.py areas: needs at least a cell -- `python map.py areas <x>"
              " <z> [WIDTH] [HEIGHT]` (act.py's order; width and height default"
              " to 1).")
        return 2
    x0, z0 = nums[0], nums[1]
    w = nums[2] if len(nums) > 2 else 1
    h = nums[3] if len(nums) > 3 else 1
    if w < 1 or h < 1:
        print(f"map.py areas: {w}x{h} is an empty rectangle.")
        return 2
    rim.init()
    mw, mh = rim.map_size()
    x1, z1 = min(mw, x0 + w), min(mh, z0 + h)
    x0, z0 = max(0, x0), max(0, z0)
    grid = scan(x0, z0, x1, z1, fields="areas,roof,fogged")
    tally, roofed, seen = collections.Counter(), 0, collections.Counter()

    def g(c, x, z):
        nonlocal roofed
        if c is None:
            return " "
        if c.get("roofDefName"):
            roofed += 1
        sym, names = area_glyph(c.get("areas"))
        for _cn, label in names:
            seen[label] += 1
        tally[sym] += 1
        return sym

    print(f"\n== AREAS ==  x {x0}..{x1-1}  z {z0}..{z1-1}"
          "   (Map.areaManager, NOT designations -- layer 6 cannot show these)")
    draw(x0, z0, x1, z1, grid, g, context=(" ", "."))
    print("  " + counted(tally, [sym for _, sym, _ in AREA_KIND] + ["."]))
    for cls, sym, desc in AREA_KIND:
        if tally.get(sym):
            print(f"    {sym}  {desc}   ({cls})")
    if not seen:
        print("  NO cell in this rectangle belongs to any area at all.")
    else:
        print("  areas touching this rectangle: "
              + ", ".join(f"{k} ({v} cell(s))" for k, v in seen.most_common(10)))
    print(f"  roof BUILT on {roofed} of {(x1 - x0) * (z1 - z0)} cell(s) here"
          " (that is the roof itself; the R glyph is only the ORDER to build"
          " one).")
    return 0


def cmd_rooms(argv):
    """`python map.py rooms` -- the whole-map census, one line per room.

    `--cold` and `--hot` re-sort that census by temperature, and are the whole
    answer to "is anywhere in the fort freezing": every line already carries the
    room's temperature and the name of anyone standing in it.  Two rules ride on
    them, and both are older than this flag:

      * a temperature that came back null prints UNREADABLE and sorts to the
        top.  Never a 0, never dropped;
      * a room a colonist is standing in is listed even when the filter would
        hide it.  A colonist stood in a -20C "room" is the exact thing this
        exists to catch.
    """
    rim.init()
    cold, hot = "--cold" in argv, "--hot" in argv
    if cold and hot:
        print("map.py rooms: --cold and --hot are the two ends of one sort --"
              " pass one.")
        return
    args = {}
    # --cold/--hot ask the tool for the outdoors and the doorways as well, and
    # filter them HERE instead.  Both rules above need that.  The outdoors
    # mega-room's own row is the only place an outdoor temperature exists in
    # this payload, and a colonist standing outdoors belongs to that room -- a
    # room the tool omits by default, which would drop them out of the one view
    # built to notice them.  Still one call.
    if "--outdoors" in argv or cold or hot:
        args["includeOutdoors"] = True
    r = fetch_rooms(args)
    if not r:
        return
    rooms = r.get("rooms") or []
    print(f"ROOMS on {r.get('mapName')}: {r.get('roomCount')} listed of"
          f" {r.get('roomCountTotal')}")

    shown = [(room, "") for room in rooms]
    held_back = 0
    if cold or hot:
        t, how = outdoor_temp(r)
        print("  outdoor: %s   (%s)"
              % ("UNREADABLE" if t is None else f"{t}C", how))
        if "--outdoors" not in argv:
            shown = []
            for q in rooms:
                if not room_is_outdoorish(q):
                    shown.append((q, ""))
                elif room_holds_colonist(q):
                    shown.append((q, "   << OUTDOOR-SLAVED OR DOORWAY, listed"
                                     " anyway: %s is standing in it"
                                     % ", ".join(room_colonists(q))))
                else:
                    held_back += 1
        shown.sort(key=lambda pair: temp_sort_key(pair[0], warmest_first=hot))

    for room, note in shown:
        print(room_summary_line(room, note))
    if not shown:
        print("  (no room passed the filter)")
    if cold or hot:
        print("  sorted %s, and a room whose temperature is UNREADABLE sorts"
              " ahead of every number in both directions -- it is the row to go"
              " look at by hand, and it is never printed as 0."
              % ("warmest first" if hot else "coldest first"))
        if held_back:
            print(f"  {held_back} outdoor-slaved or doorway room(s) not ranked"
                  " above (their temperature is the weather's); a room a"
                  " colonist is standing in is never one of them."
                  " `--outdoors` lists them all.")
    print(f"  omitted {r.get('roomsOmitted')}"
          f" ({r.get('outdoorRoomsOmitted')} outdoors,"
          f" {r.get('doorwaysOmitted')} doorways,"
          f" {r.get('dereferencedRoomsOmitted')} dereferenced)"
          + ("" if args.get("includeOutdoors") else
             " -- `--outdoors` lists them too"))
    for o in (r.get("omitted") or [])[:8]:
        print(f"    - {room_label(o)}  {o.get('cellCount')} cells"
              f"   ({o.get('reason')})")
    if len(r.get("omitted") or []) > 8:
        print(f"    (+{len(r['omitted']) - 8} more omitted rooms)")
    print("  the key characters above are this reply's own indexes. Room ids"
          " change whenever a wall changes, so nothing here is worth writing"
          " down -- ask again instead.")


def cmd_room(argv):
    """`python map.py room <x> <z>` -- the room covering one cell, in full."""
    nums = [int(v) for v in argv if v.lstrip("-").isdigit()]
    if len(nums) < 2:
        print("usage: python map.py room <x> <z>")
        return
    x, z = nums[0], nums[1]
    rim.init()
    r = fetch_rooms({"x": x, "z": z})
    if not r:
        return
    cell = r.get("cell") or {}
    print(f"CELL ({x},{z}) on {r.get('mapName')}")
    if not cell.get("found"):
        print("  no LISTED room covers that cell.")
        print("  " + (cell.get("why") or "(the tool gave no reason)"))
        print(f"  inBounds={cell.get('inBounds')}  roomId={cell.get('roomId')}")
        return
    idx = cell.get("roomIndex")
    room = next((q for q in (r.get("rooms") or [])
                 if q.get("index") == idx), None)
    if room is None:
        print(f"  ** the tool said roomIndex {idx} and rooms[] has no such"
              " entry -- the reply is internally inconsistent. **")
        return
    print(room_summary_line(room))
    print(f"      id {room.get('id')}   role {room.get('role')}"
          f"   game's own label: {room.get('gameLabel')}")
    st = room.get("stats") or {}
    print("      stats: " + "  ".join(
        f"{k} {(st.get(k) or {}).get('value')}"
        f" ({(st.get(k) or {}).get('label') or 'no word'})"
        for k in ("cleanliness", "wealth", "space", "beauty", "impressiveness")))
    print(f"      properRoom={room.get('properRoom')}"
          f"  psychologicallyOutdoors={room.get('psychologicallyOutdoors')}"
          f"  outdoors={room.get('outdoors')}"
          f"  fogged={room.get('fogged')}"
          f"  isDoorway={room.get('isDoorway')}"
          f"  touchesMapEdge={room.get('touchesMapEdge')}"
          f"  openRoofCount={room.get('openRoofCount')}")
    owners = room.get("owners") or []
    print("      owners: " + (", ".join(owners) if owners else
                              ("none" if room.get("ownersRead")
                               else "NOT READ -- see skipped")))
    for b in room.get("beds") or []:
        o = b.get("owners") or []
        print(f"      bed {b.get('label') or b.get('defName')}"
              f" @{(b.get('position') or {}).get('x')},"
              f"{(b.get('position') or {}).get('z')}"
              + ("  -> " + ", ".join(o) if o else "  -- UNASSIGNED")
              + ("  [medical]" if b.get("medical") else "")
              + ("  [prisoners]" if b.get("forPrisoners") else ""))
    if room.get("looseThingCount"):
        print(f"      ({room.get('looseThingCount')} loose things in the room"
              " are counted, not listed -- `inv.py` lists them)")


# ------------------------------------------------------------- accounting ---
def account(x0, z0, x1, z1, grid, things, nthings, classes, requested, ndesig,
            forbid, shown, rooms_drawn=False):
    cells = (x1 - x0) * (z1 - z0)
    got = len(grid)
    fogged = sum(1 for c in grid.values() if c.get("fogged"))
    per = collections.Counter()
    for b in things.values():
        for k, v in b.items():
            if not k.startswith("_"):
                per[k] += v
    drawn = {
        "layer1 structure": sum(per[k] for k in STRUCT),
        "layer2 items": sum(per[k] for k, _ in L2ORDER),
        "layer3 pawns": per["pawn"],
        "layer5 buildings": per["building"] + per["blueprint"] + per["frame"],
    }
    total = sum(drawn.values())
    print("\n== ACCOUNTING ==")
    print(f"  bounds x {x0}..{x1-1} z {z0}..{z1-1}  ({x1-x0}x{z1-z0}"
          f" = {cells} cells);  requested {requested}")
    print(f"  cell source: {SRC['tool']}"
          + ("  (carries forbidden + bed ownership)" if SRC["plus"]
             else "  ** STOCK: NO forbidden, NO bed ownership **"))
    if SRC["plus"]:
        # Asking for fewer fields is a filter, so it says so here. Everything
        # the seven layers read is in these two lists; nothing below was drawn
        # from a field that was not asked for.
        print(f"  fields asked for: cells [{CELL_FIELDS}] (not passable, not"
              f" areas);  things [defName,{THING_FIELDS}] (not stuff, not"
              " plant). Every layer above reads only these.")
    if not SRC["plus"]:
        print(degraded_line())
    print("  view: " + ("SEVEN PURE LAYERS (--full)" if FULL[0] else
          "merged default -- terrain+pawns, items+stockpiles, buildings,"
          " designations, and rooms"
          + (" as a full grid because --layers named it"
             if rooms_drawn else " as a KEY LINE ONLY")
          + ". `--full` prints the seven canonical layers, which are what the"
            " internal model actually is; `--layers rooms` draws the room grid"
            " on its own."))
    if len(shown) < 7:
        print(f"  ** --layers: only layer(s) {','.join(str(n) for n in sorted(shown))}"
              f" of seven were printed. The counts below still cover"
              f" every thing in the payload, drawn or not. **")
    print(f"  cells returned by the bridge: {got}"
          + (f"   ** {cells-got} MISSING **" if got != cells else "  (all)")
          + f";  fogged {fogged}")
    for k, v in drawn.items():
        print(f"  {k:18s} {v:6d}")
    print(f"  {'sum':18s} {total:6d}  vs {nthings} things in payload  "
          + ("OK" if total == nthings else "** MISMATCH **"))
    print(f"  layers 4, 6 and 7 consume no things: layer 4 draws cell.zone (and"
          f" reads layer-2/5 things to decide occupied vs free), layer 6 draws"
          f" cell.designations ({ndesig} in these bounds), and layer 7 draws"
          f" {ROOMS_TOOL}'s roomGrid, which is a separate call and is"
          " reconciled in its own footer.")
    if 7 in shown:
        print("  room source: " + (ROOMS_TOOL if ROOMS["ok"] else
              f"** {ROOMS_TOOL} FAILED ({ROOMS['why']}) -- no room was named **"))
    print("  walls and doors are drawn in BOTH layer 1 and layer 5 (counted"
          " once above, under layer 1).")
    if SRC["plus"]:
        nb = sum(1 for f in forbid if f[3] in BUILDY)
        ni = sum(1 for f in forbid if f[3] in L2KINDS)
        print(f"  forbidden things: {len(forbid)}  (layer 2 items {ni},"
              f" layer 5 buildings {nb}, other {len(forbid)-ni-nb})")
    else:
        print("  forbidden things: NOT REPORTED on this run (stock cell tool)")
    census = " ".join(f"{k} x{v}" for k, v in classes.most_common())
    print("  classNames seen: " + (census or "(none)"))


# -------------------------------------------------------------------- main --
def collect(grid):
    """Fold every thing into per-cell bucket counts, once, for all seven layers.

    `forbidden` rides alongside the buckets rather than becoming one: a
    forbidden chunk is still a chunk, and ACCOUNTING reconciles buckets against
    the payload, so a seventh bucket here would break the one check that would
    catch a thing going missing.  It is an overlay, in `_forbid_item` /
    `_forbid_build`, both underscore-prefixed and therefore invisible to the
    accounting sum.
    """
    things = collections.defaultdict(collections.Counter)
    classes = collections.Counter()
    unknown_plants = collections.Counter()
    forbid = []
    n = ndesig = 0
    for (x, z), c in grid.items():
        b = things[(x, z)]
        ndesig += len(c.get("designations") or [])
        for t in c.get("things") or []:
            n += 1
            classes[t.get("className") or "(none)"] += 1
            k = bucket(t)
            b[k] += 1
            if t.get("forbidden"):
                forbid.append((t, x, z, k))
                b.setdefault("_forbid_build" if k in BUILDY
                             else "_forbid_item", []).append(t)
            if k == "pawn":
                b.setdefault("_pawns", []).append(t)
            elif k in BUILDY:
                b.setdefault("_builds", []).append(t)
            elif k == "vein":
                b.setdefault("_ore", []).append(t.get("defName"))
            elif k == "plant":
                unknown_plants[t.get("defName")] += 1
    return things, n, classes, unknown_plants, ndesig, forbid


def full_legend():
    print("\nTWO THINGS THIS LEGEND EXISTS TO SAY OUT LOUD:")
    print("  * NATURAL ROCK AND A BUILT WALL ARE NEVER THE SAME CHARACTER."
          "\n    '#' natural rock, 'S' smoothed natural rock wall, ':' ore vein"
          "\n    (layer 5's context), 'H' a wall a colonist CONSTRUCTED. Every"
          "\n    ore vein carries its own letter in layer 1:"
          + "".join(f"\n      {g}  {d}" for g, _dn, d in ORE_KIND)
          + "\n      %  an ore def not in ORE_KIND, named in the footer.")
    print("  * `--layers items` DOES DRAW PLANTS -- 'b' wild food, 'h'"
          " healroot,\n    'c' sown crop, 't'/'T' tree, '\"' any other plant or"
          " grass, and the\n    species census in that layer's own footer.")
    print("  * `--layers build` DRAWS PLANTS TOO, on every cell with nothing"
          "\n    built on it, because that is the layer a build cell is chosen"
          " from.\n    Its characters differ on purpose: 'b' is a BED and 'c' a"
          " container\n    there, so wild food draws '*' and a sown crop '^';"
          " 'h' healroot,\n    'T' tree and '\"' grass are the same as layer 2.")
    for n, title, keys in ((1, "terrain / roof", L1KEY), (2, "items", L2KEY),
                           (3, "pawns", L3KEY), (4, "stockpiles", L4KEY),
                           (5, "buildings", L5KEY), (6, "designations", L6KEY),
                           (7, "rooms", L7KEY)):
        print(f"\nLAYER {n}: {title}   (--layers {LAYER_NAMES[n]})")
        for s, d in keys:
            print(f"   {s!r:>5}  {d}")
    print("\nLayer 1 draws no things, so roof can never be hidden by whatever is"
          "\nstanding on the tile. That collision is the defect this rewrite"
          "\nexists to remove.")
    print("\nThe two indicators that come from the companion tool"
          f" `{PLUS_TOOL}`:"
          "\n   layer 2  'F'  a FORBIDDEN loose thing -- outranks its kind, which"
          "\n                 is named in the footer. Forbidden things are"
          "\n                 invisible to hauling and to every food count."
          "\n   layer 5  'B'  a bed with an assigned colonist; 'b' unassigned."
          "\n                 Owner names are listed in the footer. [M]"
          "\n   layer 5  '!'  a FORBIDDEN building or door, kind in the footer."
          "\nOn the stock tool neither field exists: 'F' and '!' cannot appear,"
          "\nevery bed draws 'b', and each of those footers says so in words.")
    print("\nMERGED DEFAULT VIEW -- what an ordinary call prints. The seven layers"
          "\nabove stay the canonical model; this is a view over them, and"
          "\n`--full` prints all seven pure grids instead.")
    print("\n  1. terrain/roof WITH PAWNS ON TOP (layers 1+3). Pawn glyphs win"
          "\n     their cell; the footer counts the cells whose terrain that"
          "\n     hid. Roof still never loses to an item or a building.")
    print("  2. items ON stockpiles (layers 2+4), distinguished by CASE:")
    for sym, desc in L24KEY:
        print(f"       {sym!r:>5}  {desc}")
    print("  3. buildings / furniture (layer 5), plus the plants standing on"
          "\n     cells with nothing built on them.")
    print("  4. designations (layer 6): a coordinate list when sparse.")
    print("  5. rooms (layer 7) as ONE KEY LINE and no grid. A seventh 48x48"
          "\n     grid is ~2.4KB on every call and the key line is the part"
          "\n     that answers a question. `--layers rooms` or `--full` draws"
          "\n     the grid. The key characters are indexes into"
          "\n     `home/list_rooms`' whole-map census, so the same room wears"
          "\n     the same character here, in `map.py rooms` and in"
          "\n     `map.py room x z`. Rooms have no player-given name: the name"
          "\n     is the game's own role label plus any bed owner, recomputed"
          "\n     fresh every call, because Room.ID is reassigned whenever a"
          "\n     wall changes and a saved key file would be stale ids.")
    print("\nOutput size:"
          "\n   --layers terrain,items,...  print only some layers. Names and"
          "\n   aliases: " + layer_names_help() + ";"
          "\n   in the merged view a merged grid prints if either of its halves"
          "\n   is asked for."
          "\n   a layer with <= "
          + str(SPARSE) + " drawn cells prints a coordinate list instead of"
          "\n   the grid, says that it did, and --full overrides it."
          "\n   THE TERRAIN, STOCKPILE AND ROOM GRIDS NEVER COMPACT: terrain is"
          "\n   nothing but spatial shape, and the stockpile view's whole value"
          "\n   is seeing the stored cells against the free ones at once, and"
          "\n   a room drawn as a coordinate list is not a room.")


# --layers takes these names (or bare numbers); default is all seven.
LAYER_NAMES = {1: "terrain", 2: "items", 3: "pawns", 4: "stock",
               5: "build", 6: "desig", 7: "rooms"}
# Aliases are the names people actually type. `beds` was rejected on
# 2026-09-05 by somebody looking for beds, which layer 5 draws.
LAYER_ALIAS = {"terrain": 1, "roof": 1, "roofs": 1, "items": 2, "item": 2,
               "things": 2, "pawns": 3, "pawn": 3, "who": 3, "colonists": 3,
               "animals": 3, "stock": 4, "stockpile": 4, "stockpiles": 4,
               "zones": 4, "zone": 4, "build": 5, "buildings": 5, "building": 5,
               "furniture": 5, "beds": 5, "bed": 5, "walls": 5, "doors": 5,
               "desig": 6, "designations": 6, "designation": 6, "orders": 6,
               "rooms": 7, "room": 7}
# Names that are NOT layers and must not be silently folded into one.  `roof`
# and `roofs` mean layer 1's built-roof glyph; the ROOF AREA -- what `act.py
# apply "Build roof area"` writes -- is a Map.areaManager area that no layer
# reads, and answering "--layers roofarea" with the terrain grid is answering a
# different question (2026-09-07 turn 38).
NOT_A_LAYER = {
    "roofarea": "python map.py areas <x> <z> <W> <H>",
    "roofareas": "python map.py areas <x> <z> <W> <H>",
    "buildroof": "python map.py areas <x> <z> <W> <H>",
    "noroof": "python map.py areas <x> <z> <W> <H>",
    "area": "python map.py areas <x> <z> <W> <H>",
    "areas": "python map.py areas <x> <z> <W> <H>",
    "home": "python map.py areas <x> <z> <W> <H>",
    "homearea": "python map.py areas <x> <z> <W> <H>",
}


def layer_names_help():
    """`terrain (roof) | items (item, things) | ...` -- names and aliases."""
    out = []
    for n in sorted(LAYER_NAMES):
        extra = sorted(a for a, m in LAYER_ALIAS.items()
                       if m == n and a != LAYER_NAMES[n])
        out.append(LAYER_NAMES[n] + (" (%s)" % ", ".join(extra) if extra else ""))
    return " | ".join(out)


def parse_layers(spec):
    """-> (set of layer numbers, list of unrecognised words, {word: pointer}).

    An unknown name is never silently dropped: it comes back and is printed.
    A name that is a real question about something OTHER than these seven
    layers comes back with the command that answers it instead.
    """
    want, bad, elsewhere = set(), [], {}
    for w in spec.replace(" ", ",").split(","):
        w = w.strip().lower()
        if not w:
            continue
        if w in NOT_A_LAYER:
            elsewhere[w] = NOT_A_LAYER[w]
        elif w.isdigit() and 1 <= int(w) <= 7:
            want.add(int(w))
        elif w in LAYER_ALIAS:
            want.add(LAYER_ALIAS[w])
        else:
            bad.append(w)
    return want, bad, elsewhere


SIZE_FORMS = ("the two accepted forms are `--size W` / `--size W H` around a"
              " centre, and the positional `x z WIDTH HEIGHT`"
              " (`--corner` reads the second pair as an opposite corner"
              " instead)")


def read_numbers(argv):
    """-> ([ints], [tokens that looked like coordinates and were not]).

    `118,144` is what a hand types when it copies a coordinate out of a footer,
    and every one of those used to fall out of `int(v) for v in argv if
    v.isdigit()` silently -- leaving fewer than two numbers, which printed the
    whole manual as if nothing had been asked (turn 3). Comma pairs are read;
    anything else that is neither a flag nor a number comes back NAMED.
    """
    nums, unread = [], []
    for v in argv:
        if v.startswith("--"):
            continue
        if v.lstrip("-").isdigit():
            nums.append(int(v))
            continue
        parts = [p.strip() for p in v.split(",") if p.strip()]
        if len(parts) > 1 and all(p.lstrip("-").isdigit() for p in parts):
            nums.extend(int(p) for p in parts)
            continue
        unread.append(v)
    return nums, unread


def base_centre():
    """The base cell, or None. Read-only: it never moves the camera.

    Pinned, it is a file read. Unpinned, `cam.base_cell()` derives it from the
    colony's own buildings, which is a bridge call -- hence the `rim.init()`,
    and hence returning None rather than raising when the game is not there.
    """
    try:
        import cam
        rim.init()
        x, z, _source = cam.base_cell()
        return (int(x), int(z))
    except Exception:
        return None


def take_size(argv):
    """Pull `--size W` or `--size W H` out of argv. -> (w, h) or None on error.

    The second number is taken only when at least two positional numbers are
    left without it, so `map.py --size 96 112 140` still means 96x96 at
    (112,140) and `map.py 112 140 --size 96 48` means 96x48.
    """
    if "--size" not in argv:
        return SIZE, SIZE
    i = argv.index("--size")
    vals = []
    j = i + 1
    while j < len(argv) and len(vals) < 2 and argv[j].lstrip("-").isdigit():
        vals.append(int(argv[j]))
        j += 1
    if not vals:
        print("map.py: --size needs a number -- " + SIZE_FORMS)
        return None
    if len(vals) == 2:
        rest = [v for v in argv[:i] + argv[j:] if v.lstrip("-").isdigit()]
        if len(rest) < 2:
            vals.pop()
            j -= 1
    del argv[i:j]
    return vals[0], (vals[1] if len(vals) > 1 else vals[0])


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    want_legend = "--legend" in argv
    if "--legend-only" in argv:
        full_legend()
        return 0
    # Two subcommands, both room questions, both on the same one call the
    # seventh layer makes. They live here rather than in a new instrument
    # because the cost of a tool is surface area: one more file is one more
    # doc row and one more thing to remember exists.
    if argv and argv[0] == "rooms":
        cmd_rooms(argv[1:])
        return 0
    if argv and argv[0] == "room":
        cmd_room(argv[1:])
        return 0
    if argv and argv[0] == "areas":
        return cmd_areas(argv[1:])
    asked_size = "--size" in argv
    size = take_size(argv)
    if size is None:
        return 2
    size_w, size_h = size
    shown = set(LAYER_NAMES)
    # Asking for the rooms layer BY NAME means you want the grid, not the key
    # line the merged default settles for. Named explicitly beats a default.
    asked_layers = "--layers" in argv
    if asked_layers:
        # Validated BEFORE anything is drawn: a typo used to cost a complete
        # seven-layer dump and only then say the name was unrecognised.
        i = argv.index("--layers")
        spec = argv[i + 1] if i + 1 < len(argv) else ""
        shown, bad, elsewhere = parse_layers(spec)
        del argv[i:i + 2]
        if bad or elsewhere or not shown:
            print("map.py: --layers "
                  + (("unrecognised name(s): " + ", ".join(bad)) if bad
                     else "named no layer") + ".")
            for word, how in sorted(elsewhere.items()):
                print(f"  {word!r} is NOT one of the seven layers: it is a"
                      f" different read -- {how}."
                      + ("  (`roof`/`roofs` DO mean layer 1, which draws the"
                         " roof that is BUILT, not the roof AREA.)"
                         if word.startswith(("roof", "buildroof", "noroof"))
                         else ""))
            print("  valid names (aliases in brackets), or the bare numbers"
                  " 1-7: " + layer_names_help())
            return 2
    FULL[0] = "--full" in argv
    corner = "--rect" in argv or "--corner" in argv
    nums, unread = read_numbers(argv)
    if unread and len(nums) >= 2:
        # Named, not dropped. `--layers roof desig` (a space instead of a
        # comma) took `roof` as the whole spec and threw `desig` away without a
        # word, which is a request half-answered and no way to notice.
        print("map.py: IGNORED " + ", ".join(repr(u) for u in unread)
              + " -- neither a flag nor a coordinate. `--layers` takes ONE"
              " comma-separated word list (`--layers roof,desig`), not several"
              " words: " + layer_names_help())
        return 2
    if corner and len(nums) != 4:
        # Checked BEFORE the "too few numbers" branch below: `--corner` with
        # the wrong count used to fall through to `print(__doc__)`, so a real
        # command answered with the whole manual (turn 3).
        print(f"map.py: --corner/--rect needs four numbers (x0 z0 x1 z1); got"
              f" {len(nums)}"
              + (" (could not read " + ", ".join(repr(u) for u in unread) + ")"
                 if unread else "")
              + " -- " + SIZE_FORMS)
        return 2
    if len(nums) < 2:
        if unread:
            print("map.py: could not read "
                  + ", ".join(repr(u) for u in unread)
                  + " as a number. Coordinates are two separate integers"
                  " (`map.py 118 144`), not one token -- " + SIZE_FORMS + ".")
            return 2
        # A command that asked for something and just left out the centre gets
        # the base cell, not the manual: `--size 100 --layers terrain --legend`
        # printed this file's own "Output size:" help text (turn 4).
        if asked_size or asked_layers or FULL[0] or "--stock" in argv:
            base = base_centre()
            if base is None:
                print("map.py: no coordinates given and no pinned base to fall"
                      " back on (`cam.py base x z` pins one) -- " + SIZE_FORMS + ".")
                return 2
            nums = list(base)
            print(f"  (no centre given -- using the pinned base at {base[0]},"
                  f"{base[1]}; `cam.py base x z` moves it)")
        elif want_legend:
            full_legend()
            return 0
        else:
            print(__doc__)
            return 0
    if len(nums) >= 4:
        if corner:
            x0, z0, x1, z1 = nums[:4]
            requested = f"corners {x0},{z0} .. {x1-1},{z1-1}"
        else:
            # Same order as `act.py apply <label> x z width height`.
            ax, az, aw, ah = nums[:4]
            x0, z0, x1, z1 = ax, az, ax + aw, az + ah
            requested = (f"{aw}x{ah} from {ax},{az} (x z WIDTH HEIGHT, act.py's"
                         " order -- `--corner` reads the pair as a corner)")
    else:
        # `corner` with anything but four numbers already returned above.
        cx, cz = nums[:2]
        x0, z0 = cx - size_w // 2, cz - size_h // 2
        x1, z1 = x0 + size_w, z0 + size_h
        requested = f"{size_w}x{size_h} centred on {cx},{cz}"
    if x1 <= x0 or z1 <= z0:
        print(f"map.py: those numbers describe an EMPTY window ({requested}):"
              f" x {x0}..{x1-1} z {z0}..{z1-1}. Refusing rather than printing"
              " an empty map -- " + SIZE_FORMS + ".")
        return 2
    rim.init()
    mw, mh = rim.map_size()
    cx0, cz0, cx1, cz1 = max(0, x0), max(0, z0), min(mw, x1), min(mh, z1)
    print(f"MAP {mw}x{mh}   requested {requested}")
    if (cx0, cz0, cx1, cz1) != (x0, z0, x1, z1):
        print(f"  ** CLAMPED to the map edge: asked x {x0}..{x1-1} z {z0}..{z1-1},"
              f" drawing x {cx0}..{cx1-1} z {cz0}..{cz1-1} **")
    x0, z0, x1, z1 = cx0, cz0, cx1, cz1
    if x1 <= x0 or z1 <= z0:
        print("  ** nothing left after clamping -- those bounds are off the"
              f" map ({mw}x{mh}). " + SIZE_FORMS + ". **")
        return 2
    grid = scan(x0, z0, x1, z1, want_plus="--stock" not in argv)
    print(f"  cell source: {SRC['tool']}"
          + ("  (+ forbidden, + bed ownership)" if SRC["plus"] else ""))
    things, nthings, classes, unknown_plants, ndesig, forbid = collect(grid)
    # One extra call, only when the rooms layer is wanted. The rect asked for is
    # exactly the rect drawn, so every cell above has a roomGrid entry and a
    # missing one is a reportable gap rather than a silent blank.
    rooms_reply = None
    if 7 in shown:
        rooms_reply = fetch_rooms({"x": x0, "z": z0,
                                   "width": x1 - x0, "height": z1 - z0})
    mine = colonists() if 3 in shown else {}
    ident = "--no-who" not in argv
    if FULL[0]:
        # The canonical form: seven pure grids, nothing drawn on top of anything.
        if 1 in shown:
            layer1(x0, z0, x1, z1, grid, things)
        if 2 in shown:
            layer2(x0, z0, x1, z1, grid, things, unknown_plants, forbid)
        if 3 in shown:
            layer3(x0, z0, x1, z1, grid, things, mine, do_ident=ident)
        if 4 in shown:
            layer4(x0, z0, x1, z1, grid, things)
        if 5 in shown:
            layer5(x0, z0, x1, z1, grid, things, forbid)
        if 6 in shown:
            layer6(x0, z0, x1, z1, grid, things)
        if 7 in shown:
            layer7(x0, z0, x1, z1, grid, things, rooms_reply)
    else:
        # The merged default: three grids, a list and a room key line, over
        # the same seven layers.
        pawnmap, pawn_footers = {}, None
        if 3 in shown:
            pawnmap, pawn_footers = layer3(x0, z0, x1, z1, grid, things, mine,
                                           do_ident=ident, emit_grid=False)
        if 1 in shown or 3 in shown:
            layer1(x0, z0, x1, z1, grid, things,
                   overlay=pawnmap if 3 in shown else None)
        if pawn_footers:
            pawn_footers()
        if 2 in shown or 4 in shown:
            layer24(x0, z0, x1, z1, grid, things, unknown_plants, forbid)
        if 5 in shown:
            layer5(x0, z0, x1, z1, grid, things, forbid)
        if 6 in shown:
            layer6(x0, z0, x1, z1, grid, things)
        if 7 in shown and asked_layers:
            layer7(x0, z0, x1, z1, grid, things, rooms_reply)
        elif 7 in shown:
            # The merged default's whole concession to layer 7: the key line,
            # no grid. Drawing the grid here would add ~2.4KB to every ordinary
            # call, and the question "which rooms am I looking at" is answered
            # by the key alone. `--layers rooms` and `--full` draw it.
            merged_room_key(x0, z0, x1, z1, grid, things, rooms_reply)
    account(x0, z0, x1, z1, grid, things, nthings, classes, requested, ndesig,
            forbid, shown, rooms_drawn=FULL[0] or (7 in shown and asked_layers))
    if want_legend:
        # `--legend` prints the key AS WELL AS the map, under it;
        # `--legend-only` is the key on its own with no bridge call.
        print("\n== LEGEND (--legend; `--legend-only` prints this alone) ==")
        full_legend()
    return 0


if __name__ == "__main__":
    sys.exit(main())
