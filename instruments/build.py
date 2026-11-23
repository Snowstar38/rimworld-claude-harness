"""Place a building with a ROTATION, and see which face lands where -- first.

  python build.py Cooler 114 145             # dry-run all four rotations
  python build.py Cooler 114 145 east        # dry-run one
  python build.py Cooler 114 145 east --do   # place the blueprint
  python build.py Cooler 114 145 east --do --no-watch   # place, no camera move
  python build.py Wall 110 146 --stuff WoodLog
  python build.py Wall 110 146 --do --stuff WoodLog   # no rotation needed
  python build.py Grave 120 136 --dry-run    # the default, said out loud

A LINE or a ROOM is ONE call, not one call per cell:

  python build.py Wall 141 130 --to 152 130 --stuff BlocksSandstone
  python build.py Wall 141 130 --to 152 130 --stuff BlocksSandstone --do
  python build.py Wall --cells "141,130;141,131;141,132" --do

`--to <x> <z>` is a straight line from the anchor cell (inclusive both ends);
`--cells "x,z;x,z"` is any set of cells. The batch is one `home/place_building`
call, one camera move, ONE 1.5 s watch lead and one read-back for all of it,
where a call per cell paid ~1.8 s each -- a 64-cell wall was about two minutes
in which nothing else could be answered. A refused cell does not stop the rest;
it is named with the game's own reason and counted in the verdict.

**Without `--do` nothing is placed**, and the first line of the output says so.
`--dry-run` is that default spelled out; a flag is never read as the rotation.

Backed by `home/place_building`, which calls `GenConstruct.CanPlaceBlueprintAt`
for every requested rotation, and for coolers and vents says which cell is the COLD face
and which the HOT one, with whether that cell is outdoors.

Try the four rotations before `--do`: the accepted rotation whose cold face is inside
the room and whose hot face is outdoors is the one you want. A rotation that
puts both faces in the same room is accepted by the game and useless.

`--do` wants ONE rotation from you for anything that turns, because for a cooler
or a vent which way it faces is the entire question. For a def with
`rotatable: false` -- a Wall, and everything else -- it stops asking: it takes
north, the rotation the game itself would build it at, and says so in the first
line of the output.

An unknown def is refused with the bridge's own reason, the correct defName for
known misses (`Conduit` -> `PowerConduit`), and the closest buildable names.
Nothing is substituted for what was asked for.

Every call ends with ONE `==` verdict line: PLACED with the blueprint id read
back from the game, ALREADY THERE, REFUSED with the reason, or DRY RUN with the
rotations the game accepted -- and, when the accepted placement removes
something, `would wipe/replace: rice plant, wooden wall` on that same line. An
accepted rotation over one of our own walls is a REPLACEMENT, not empty ground.
A wild plant under the footprint is cleared when the thing is built and costs
nothing, so it stays in the rotation rows and off the verdict. The per-rotation rows above it are detail, never a
second verdict -- on 2026-09-07 a call printed `PLACED` and `north REFUSED` in
the same breath and only `buildings.py --pending` said which was true.

`act.apply` is still the tool for designations that have no rotation (Harvest,
Mine, Deconstruct, zones). This one is for things you build.
"""
import difflib
import json
import sys
import time

import rim


SUGGEST = 8             # closest names printed on a miss. Never the whole menu.
MAX_BATCH = 200         # cells per call; the companion refuses more on its side
BATCH_NOTES = 8         # non-ordinary cell rows printed before "+N more"

# The read-back after a real batch, for the WHOLE batch in one call each time
# (buildings.py uses the same four reads over ~2.1 s for a placing click).
CONFIRM_READS = 4
CONFIRM_GAP = 0.7

# Common wrong names -> the real defName(s), keyed with case and punctuation
# folded out. Two values means genuinely ambiguous; both are printed.
ALIASES = {
    "conduit": ("PowerConduit",),
    "powerconduit": ("PowerConduit",),
    "sculptorstable": ("TableSculpting",),
    "sculptorsbench": ("TableSculpting",),
    "researchbench": ("SimpleResearchBench",),
    "stove": ("ElectricStove", "FueledStove"),
    "butchertable": ("TableButcher",),
    "butcherbench": ("TableButcher",),
    "stonecutter": ("TableStonecutter",),
    "stonecuttertable": ("TableStonecutter",),
    "turret": ("Turret_MiniTurret",),
    "miniturret": ("Turret_MiniTurret",),
    "battery": ("Battery",),
    "solar": ("SolarGenerator",),
    "solarpanel": ("SolarGenerator",),
}

# Offline difflib pool. A fallback, not a def database: the game still decides
# whether a name resolves.
KNOWN_DEFS = (
    "Autodoor", "Battery", "Bed", "ButcherSpot", "Campfire", "Column",
    "Cooler", "CraftingSpot", "Door", "DoubleBed", "DrugLab",
    "ElectricSmithy", "ElectricStove", "ElectricTailoringBench",
    "FueledSmithy", "FueledStove", "GeothermalGenerator",
    "HandTailoringBench", "HiTechResearchBench", "HospitalBed",
    "HydroponicsBasin", "PassiveCooler", "PlantPot", "PowerConduit",
    "Sandbags", "Shelf", "SimpleResearchBench", "SleepingSpot",
    "SolarGenerator", "StandingLamp", "SunLamp", "TableButcher",
    "TableMachining", "TableSculpting", "TableSmithy", "TableStonecutter",
    "TorchLamp", "Turret_Autocannon", "Turret_MiniTurret", "Vent", "Wall",
    "WindTurbine", "WoodFiredGenerator",
)

# Prefix of PlaceBuildingTool.Build's unresolvable-name refusal.
UNKNOWN_DEF = "No ThingDef or TerrainDef matches"


def _norm(name):
    """Fold case and punctuation: `Butcher table` and `butchertable` are one key."""
    return "".join(c for c in str(name).lower() if c.isalnum())


def reason(r):
    """The refusal text, wherever the bridge put it.

    `BridgeCommon.Failure` returns `{success, tool, error}` and carries no
    `message`, so reading only `message` yielded `FAILED: None`. Stock RimBridge
    tools do use `message`, so it is tried first. Never returns None.
    """
    if not isinstance(r, dict):
        return "the bridge did not return a payload: %.200r" % (r,)
    for key in ("message", "error", "detail"):
        v = r.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "no reason given by the bridge (reply keys: %s)" % (", ".join(sorted(r)) or "none")


def _menu_names():
    """Architect-menu labels, or `[]` when the game is unreachable.

    No bridge tool enumerates buildable defNames; `list_architect_designators`
    returns labels, which `TryResolveBuildable` accepts as well as a defName.
    `act` caches them, so the calls happen once per process.
    """
    try:
        import act
        return [row[0] for row in act.labels()]
    except Exception:
        return []


def unknown_def_lines(asked, limit=SUGGEST):
    """Lines under a "no such def" refusal: the aliased defName, then near
    misses. Names the def; never substitutes it."""
    lines = []
    hit = ALIASES.get(_norm(asked)) or ()
    if hit:
        lines.append("  %s is a known miss -- the def is %s.%s"
                     % (asked, " or ".join(hit),
                        "  Two defs match; name the one you meant." if len(hit) > 1 else ""))

    pool = sorted(set(KNOWN_DEFS) | set(hit) | set(_menu_names()))
    want = _norm(asked)
    # Alias, then substring, then difflib -- containment beats a ratio.
    close = list(hit)
    close += [p for p in pool if want and want in _norm(p) and p not in close]
    close += [p for p in difflib.get_close_matches(str(asked), pool, n=limit, cutoff=0.5)
              if p not in close]
    if close:
        lines.append("  closest: " + ", ".join(close[:limit]))
    return lines


def is_unknown_def(r):
    """True when the refusal is "that name is not a def", not something else."""
    return isinstance(r, dict) and reason(r).startswith(UNKNOWN_DEF)


def watch_line(r, on=None):
    """One line about the menu the write opened, if any. Claims the camera for
    Hands when the watch moved it, so the Lookout stays out of the shot."""
    if not isinstance(r, dict):
        # `place`/`place_batch` are strict=False, so a reply that is not a
        # payload comes straight back. `report_batch` already said so; reading
        # `watch` off the string here only renames that report
        # `'str' object has no attribute 'get'` one line later.
        return
    w = r.get("watch") or {}
    if not w.get("shown"):
        print("watch: skipped (%s)" % (w.get("reason") or "not shown"))
        return
    if w.get("cameraMoved"):
        try:
            import camlock
            camlock.claim("hands", "watch")
        except Exception:
            pass
    tab = (w.get("inspectTab") or "").replace("ITab_Pawn_", "").replace("ITab_", "") or w.get("mainTab")
    who = (" on " + on) if on else ""
    if tab:
        print("watch: %s tab open%s, closes in %s s" % (tab, who, w.get("closesAfterSeconds")))
    else:
        print("watch: %s%s, clears in %s s"
              % ("selected" if w.get("selected") else "camera moved", who,
                 w.get("closesAfterSeconds")))


def place(defname, x, z, rotation="all", stuff=None, do=False, god=False, watch=True):
    args = {"defName": defname, "x": x, "z": z, "rotation": rotation,
            "dryRun": not do, "godMode": god, "watch": watch}
    if stuff:
        args["stuff"] = stuff
    # strict=False: "no rotation fits" is the answer, not a fault.
    return rim.game("home/place_building", args, strict=False)


def cells_arg(cells):
    """`[(141,130), (142,130)]` -> `"141,130;142,130"`, the companion's spelling."""
    return ";".join("%d,%d" % (x, z) for x, z in cells)


def place_batch(defname, cells, rotation, stuff=None, do=False, god=False,
                watch=True):
    """ONE call for the whole line or room.

    The cost of the 64th cell is one more `CanPlaceBlueprintAt` and one more
    `PlaceBlueprintForBuild` inside a hop that is already running -- not another
    process, another handshake, another camera move and another 1.5 s watch
    lead. The companion chunks the placements 8 to a main-thread hop so the
    colony keeps ticking while they go in.
    """
    args = {"defName": defname, "x": cells[0][0], "z": cells[0][1],
            "cells": cells_arg(cells), "rotation": rotation,
            "dryRun": not do, "godMode": god, "watch": watch}
    if stuff:
        args["stuff"] = stuff
    return rim.game("home/place_building", args, strict=False)


def line_cells(x, z, to_x, to_z):
    """Every cell from (x,z) to (to_x,to_z) inclusive, or None for a diagonal.

    Straight only: a diagonal "line" of walls is not a wall, and guessing which
    of the two axes was meant is how you fence the wrong side of a room.
    """
    if x != to_x and z != to_z:
        return None
    if x == to_x:
        step = 1 if to_z >= z else -1
        return [(x, c) for c in range(z, to_z + step, step)]
    step = 1 if to_x >= x else -1
    return [(c, z) for c in range(x, to_x + step, step)]


def parse_cells(spec):
    """`"141,130;142,130"` -> [(141,130),(142,130)], or (None, why)."""
    out = []
    for part in str(spec).replace("|", ";").split(";"):
        text = part.strip()
        if not text:
            continue
        bits = text.split(",")
        if len(bits) != 2:
            return None, "%r is not a cell -- cells are \"x,z\", joined with ';'" % text
        try:
            cell = (int(bits[0].strip()), int(bits[1].strip()))
        except ValueError:
            return None, "%r is not a cell -- x and z must be whole numbers" % text
        if cell not in out:
            out.append(cell)
    if not out:
        return None, "--cells named no cell"
    return out, None


def _rotates(payload):
    """True / False / None-for-don't-know, read off a `place_building` reply.

    Asked, never guessed. The tool has carried `rotatable` on every reply since
    it was written (PlaceBuildingTool.cs, 2026-09-02) and it is `ThingDef.
    rotatable` itself -- the same field `Designator_Place` reads -- so there is
    no reason for a list of def names here, and a list would be wrong the first
    time a mod adds a wall.

    The one null is deliberate on the C# side: a TerrainDef has no `rotatable`
    flag because terrain has no rotation at all. That null is an answer (a
    floor does not turn), not a gap, so it is read as False. Any other missing
    value means the dry run itself did not come back, and that is None -- the
    caller must not read "the probe failed" as "it does not rotate".
    """
    if not isinstance(payload, dict):
        return None
    flag = payload.get("rotatable")
    if isinstance(flag, bool):
        return flag
    if flag is None and (payload.get("def") or {}).get("kind") == "TerrainDef":
        return False
    return None


def _side(s):
    if not s:
        return "?"
    where = ("OUTDOORS" if s.get("isOutdoors") else "inside") if s.get("inBounds") else "off-map"
    if s.get("impassable"):
        where = "IMPASSABLE"
    t = s.get("temperature")
    return "%s,%s %s%s" % (s.get("x"), s.get("z"), where,
                           "" if t is None else " %.1fC" % t)


def _qty(m, key):
    v = m.get(key)
    return 0 if v is None else v


def _materials(r):
    """The materials block, printed on every dry run and every placement.

    One line per SHORTFALL, because a shortfall is the thing you act on. When
    nothing is short it still prints one line -- "the tool looked and the
    colony has it" and "the tool did not look" must not read the same, which
    is the same rule the companion follows on its side.
    """
    mat = r.get("materials") or {}
    rows = mat.get("rows") or []
    if not rows:
        if mat.get("unreadable"):
            print("  materials: NOT READ -- %s" % (mat.get("note") or "the cost list could not be read"))
        return

    # On a batch the companion costs every requested cell, so the numbers here
    # are the whole line's, not one wall's. Said out loud: "need 5" beside a
    # 64-cell ask is how a caller starts a line it cannot finish.
    for_cells = mat.get("forCells")
    if isinstance(for_cells, int) and for_cells > 1:
        print("  materials are costed for all %d cells (%s per cell)" % (
            for_cells,
            ", ".join("%s %s" % (m.get("label") or m.get("defName"),
                                 _qty(m, "perCellNeeded"))
                      for m in rows)))

    short = [m for m in rows if _qty(m, "shortfall") > 0]
    for m in short:
        extra = []
        if _qty(m, "forbidden"):
            extra.append("%s forbidden" % m["forbidden"])
        if _qty(m, "reservedByOtherBlueprints"):
            extra.append("%s reserved by other blueprints" % m["reservedByOtherBlueprints"])
        print("  SHORT %s: need %s, available %s of %s on map%s -- short %s" % (
            m.get("label") or m.get("defName"),
            _qty(m, "needed"), _qty(m, "available"), _qty(m, "onMap"),
            (" (" + ", ".join(extra) + ")") if extra else "",
            _qty(m, "shortfall")))
    if not short:
        print("  materials: all present -- " + ", ".join(
            "%s %s of %s" % (m.get("label") or m.get("defName"),
                             _qty(m, "needed"), _qty(m, "available"))
            for m in rows))


# Filth is a Thing and stands in the thing grid, so it arrives in
# `blockingThings` with everything else. It blocks NOTHING: a blueprint goes
# down on blood and dirt, and the builder never touches it. On 2026-09-07 a
# refusal listed "Dirt at 122,140, Blood at 123,140" first and the six-item cap
# then hid the torch lamp that was the actual cause. Dropped here, and the count
# is still said out loud -- a filter that hides things has to say so.
_BLOCKER_RANK = {"Building": 0, "Item": 2, "Plant": 3, "Pawn": 1}


def _blockers(row):
    """(rows worth naming, how many filth were dropped), most-blocking first."""
    rows = row.get("blockingThings") or []
    filth = [b for b in rows if (b.get("category") or "") == "Filth"]
    keep = [b for b in rows if (b.get("category") or "") != "Filth"]

    def rank(b):
        # A blueprint or a frame in the way is a construction problem and reads
        # ahead of a loose item; a building is the thing that actually refuses.
        if b.get("isBlueprint") or b.get("isFrame"):
            return 1
        return _BLOCKER_RANK.get(b.get("category") or "", 4)

    keep.sort(key=rank)
    return keep, len(filth)


# What an accepted placement DOES to a thing standing in the footprint, in the
# companion's own word (`effectOnPlace`) -> how it is said, and whether it is
# worth the verdict line. Wild grass under a wall is cleared and costs nothing;
# a sown crop and a wall we already built cost something.
_EFFECT_WORD = {"wiped": "would be WIPED", "replaced": "would be REPLACED",
                "crop": "CROP -- destroyed when built",
                "cleared": "cleared when built, no effect",
                "hauled": "haul first", "frameCancelled": "frame cancelled",
                "none": "no effect"}
_EFFECT_COSTS = ("wiped", "replaced", "crop", "frameCancelled")


def _effect(b):
    """The companion's `effectOnPlace` for one blocker, or None on an old DLL.

    None is "this DLL does not say", never "nothing happens": the caller falls
    back to the two flags a DLL of any age carries.
    """
    word = b.get("effectOnPlace")
    return word if word in _EFFECT_WORD else None


def _note(b):
    """The parenthesised note after one blocker, or ""."""
    word = _effect(b)
    if word:
        return " (%s)" % _EFFECT_WORD[word]
    if b.get("mustBeHauledFirst"):
        return " (haul first)"
    if b.get("wouldBeWiped"):
        return " (would be wiped)"
    return ""


def costs(r):
    """[label, ...] the ACCEPTED placement removes or replaces, deduped.

    Read off the accepted rotation rows only: a thing under a rotation the game
    refuses is not going anywhere. A wild plant is cleared when the building
    goes up and costs nothing, so it is not here; a sown crop, a building being
    replaced and a cancelled frame are.
    """
    return _costs_from_rows(r.get("rotations") or [])


def _costs_from_rows(rows):
    """The same reading over any list of rotation rows -- the four of a single
    cell, or the one-per-cell rows of a batch."""
    out = []
    for row in rows or []:
        if not row.get("accepted"):
            continue
        for b in row.get("blockingThings") or []:
            word = _effect(b)
            if word is None:
                word = "wiped" if b.get("wouldBeWiped") else None
            if word not in _EFFECT_COSTS:
                continue
            label = b.get("label") or b.get("defName")
            if label and label not in out:
                out.append(label)
    return out


def _blocker_line(row, indent=8):
    """One line naming what stands in the footprint, or None.

    The heading is the rotation's own verdict: on a rotation the game ACCEPTED
    nothing here blocks anything, so "in the way" would be a lie.
    """
    keep, filth = _blockers(row)
    if not keep and not filth:
        return None
    bits = ", ".join(
        "%s at %s,%s%s" % (b.get("label") or b.get("defName"),
                           (b.get("position") or {}).get("x"),
                           (b.get("position") or {}).get("z"), _note(b))
        for b in keep[:6])
    if len(keep) > 6:
        bits += ", +%d more" % (len(keep) - 6)
    if not keep:
        bits = "nothing but filth"
    if filth:
        bits += "  (%d filth ignored -- filth blocks nothing)" % filth
    head = "accepted over: " if row.get("accepted") else "in the way: "
    return " " * indent + head + bits


def _outcome(r):
    """What happened to THIS call, in one word. `outcome` is the companion's own
    field (preview / placed / already_present / refused / error); the fallbacks
    below cover a DLL older than it.

    The 2026-09-07 bug was that the headline read `PLACED` off `dryRun` alone,
    so a call the game refused printed PLACED and a rotation row printed REFUSED
    in the same breath, and only `buildings.py --pending` said which was true.
    """
    word = r.get("outcome")
    if word in ("preview", "placed", "partial", "already_present", "refused", "error"):
        return word
    if r.get("dryRun"):
        return "preview"
    if r.get("placed"):
        return "placed"
    if r.get("alreadyPlaced"):
        return "already_present"
    return "refused" if r.get("success") is False else "error"


_HEAD_WORD = {"preview": "DRY RUN", "placed": "PLACED",
              "partial": "PARTLY PLACED",
              "already_present": "ALREADY THERE", "refused": "REFUSED",
              "error": "ERROR"}


def _cost_tail(r, cap=6):
    """`; would wipe/replace: ...` for the verdict, or "".

    An accepted rotation is not the whole answer. 4 of 4 is accepted over a
    rice crop and over one of our own wooden walls, and what that costs belongs
    on the line the reader is looking at.
    """
    return _cost_tail_from(costs(r), cap)


def _cost_tail_from(names, cap=6):
    if not names:
        return ""
    shown = ", ".join(names[:cap])
    if len(names) > cap:
        shown += ", +%d more" % (len(names) - cap)
    return "; would wipe/replace: " + shown


def verdict_line(r):
    """The one line that says what this call did. Printed last, always, and
    never contradicted by anything above it."""
    out = _outcome(r)
    if out == "preview":
        ok = r.get("acceptedRotations") or [row.get("rotation")
                                            for row in (r.get("rotations") or [])
                                            if row.get("accepted")]
        n = r.get("rotationsEvaluated") or len(r.get("rotations") or [])
        return ("== DRY RUN -- NOTHING placed (add --do). %d of %d rotation(s) "
                "accepted%s%s"
                % (len(ok), n, (": " + ", ".join(str(o) for o in ok)) if ok else "",
                   _cost_tail(r)))
    if out == "placed":
        p = r.get("placed") or {}
        return ("== PLACED -- blueprint id %s, %s at %s,%s facing %s%s"
                % (p.get("thingIDNumber"), p.get("label") or p.get("defName"),
                   (p.get("position") or {}).get("x"),
                   (p.get("position") or {}).get("z"),
                   p.get("rotation") or p.get("rotationWord"), _cost_tail(r)))
    if out == "already_present":
        return ("== ALREADY THERE -- an identical blueprint or the finished thing "
                "stands at this cell and rotation; NOTHING was placed and no new "
                "blueprint id exists.")
    return "== %s -- NOTHING was placed. %s" % (
        "REFUSED" if out == "refused" else "ERROR", reason(r))


def _cannot_build(r):
    """The loud line, printed once, after the rotation sweep: on a real
    placement that puts it directly above the PLACED confirmation, which is the
    line a person actually reads, and on a dry run it is the last word. Placing
    anyway is correct -- a blueprint waiting on steel is an ordinary thing to
    leave standing -- so this warns, it never refuses."""
    mat = r.get("materials") or {}
    if mat.get("canBuildNow") is False:
        print("  CANNOT BUILD YET -- %s" % (mat.get("missing") or "materials missing"))


def report(r, asked=None, dry=None):
    """`asked` is the def name the caller typed; a reply that failed to resolve
    carries no `def` block, so it cannot be recovered from `r`.

    `dry` is the CALLER's intent. It outranks the reply, and it is the first
    line of the output: a dry run whose rotation rows all read `ok` is easy to
    read as a placement, and nothing has been placed.
    """
    if not isinstance(r, dict):
        # The same shape `report_batch` guards for, on the single-cell path:
        # `reason()` and `is_unknown_def()` both expect it, and reading
        # `rotationDefaulted` off a string below turned the one reply that
        # most needs saying into a traceback.
        print("FAILED: %s" % reason(r))
        print("== NOTHING WAS PLACED.")
        return 1
    if dry is None:
        dry = _outcome(r) == "preview"
    if dry:
        print("DRY RUN -- nothing placed; add --do")
    # First line, and printed before the failure check: if the rotation was
    # chosen for you, you should read that even on a call that then failed.
    if r.get("rotationDefaulted"):
        print("rotation defaulted to %s -- this def is not rotatable, so the game "
              "builds it Rot4.North whatever is asked for." % r["rotationDefaulted"])
    if not r.get("success") and not r.get("rotations"):
        print("FAILED: %s" % reason(r))
        if asked and is_unknown_def(r):
            for line in unknown_def_lines(asked):
                print(line)
        # ONE `==` verdict per call, LAST -- the same promise the rest of this
        # file keeps. Stopping here left the one call most likely to be misread
        # (an unresolvable def name) ending on a list of near-misses.
        print("== NOTHING WAS PLACED.")
        return 1
    d = r.get("def") or {}
    print("%s (%s) at %s,%s -- %s%s%s" % (
        d.get("label") or d.get("defName"), d.get("defName"),
        (r.get("position") or {}).get("x"), (r.get("position") or {}).get("z"),
        _HEAD_WORD.get(_outcome(r), "?"),
        "" if r.get("researchFinished", True) else " | RESEARCH NOT DONE",
        "" if r.get("rotatable", True) else " | not rotatable"))
    if r.get("detail"):
        print("  " + r["detail"])
    cost = r.get("costList") or []
    if cost:
        print("  cost: " + ", ".join("%s x%s" % (c.get("label") or c.get("defName"), c.get("count")) for c in cost))
    _materials(r)
    # Per-rotation results are DETAIL under the verdict, never a second verdict:
    # the word REFUSED belongs to the call, not to a row of a sweep the call
    # only consulted. A row that the game would not accept says `no`.
    rows = r.get("rotations") or []
    if rows:
        print("  rotations checked (detail -- the verdict is the LAST line):")
    for row in rows:
        line = "    %-5s %s" % (row.get("rotation"),
                                "ok" if row.get("accepted") else "no")
        if row.get("reason"):
            line += " -- " + row["reason"]
        if row.get("alreadyPlaced") or row.get("identicalBlueprintExists"):
            line += " (identical blueprint already here)"
        print(line)
        sides = row.get("sides") or {}
        if "cold" in sides:
            print("        cold face %s | hot face %s" % (_side(sides["cold"]), _side(sides["hot"])))
        elif "a" in sides:
            print("        vent faces %s | %s" % (_side(sides["a"]), _side(sides["b"])))
        blocked = _blocker_line(row)
        if blocked:
            print(blocked)
    # Directly above the verdict. A blueprint short of steel is still placed, so
    # this warns; it never changes what the verdict says.
    _cannot_build(r)
    # `wiped` / `framesCancelled` are what a real placement DID and sit at the
    # top level; `wipeOnPlace` / `framesCancelledOnPlace` are what a rotation
    # WOULD do and sit on the rotation ROW (PlaceBuildingTool.cs:1291-1292,
    # INSTALL.md "Payload shapes"). Looked for at the top level, those two were
    # branches that could never fire, so a dry run never named what it would
    # clear -- the detail under the `would wipe/replace:` verdict tail.
    named = [(verb, w)
             for key, verb in (("wiped", "wiped"),
                               ("framesCancelled", "cancelled frame"))
             for w in r.get(key) or []]
    for row in r.get("rotations") or []:
        for key, verb in (("wipeOnPlace", "would wipe"),
                          ("framesCancelledOnPlace", "would cancel frame")):
            named += [(verb, w) for w in row.get(key) or []]
    seen = set()
    for verb, w in named:
        mark = (verb, w.get("label") or w.get("defName"),
                (w.get("position") or {}).get("x"),
                (w.get("position") or {}).get("z"))
        # Four rotations over one cell name the same plant four times.
        if mark in seen:
            continue
        seen.add(mark)
        print("  %s: %s at %s,%s" % mark)
    # ONE verdict per call, last, unmissable.
    print(verdict_line(r))
    # ...and the same exit code the batch path gives, for the same reason: a
    # refusal must stop a script that chained on this one.
    return 0 if _outcome(r) in ("preview", "placed", "already_present") else 1


# ---------------------------------------------------------------- the batch

LIST_TOOL = "home/list_buildings"


def batch(r):
    """The `batch` block, or None when this reply is about one cell."""
    b = r.get("batch") if isinstance(r, dict) else None
    return b if isinstance(b, dict) else None


def _span(cells):
    """How the header says which cells these are."""
    if len(cells) == 1:
        return "%d,%d" % cells[0]
    xs = [c[0] for c in cells]
    zs = [c[1] for c in cells]
    if len(set(xs)) == 1 or len(set(zs)) == 1:
        return "%d,%d -> %d,%d" % (cells[0][0], cells[0][1], cells[-1][0], cells[-1][1])
    return "%d cells within %d,%d..%d,%d" % (len(cells), min(xs), min(zs), max(xs), max(zs))


def _id_number(value):
    """The trailing digits of a ThingID: `Blueprint_Wall4519` -> 4519.

    `home/list_buildings` reports `thing.ThingID` (a string) and
    `home/place_building` reports `thingIDNumber` (an int). The number is the
    tail of the string, which is how the two are matched without a second read.
    """
    digits = ""
    for ch in reversed(str(value or "")):
        if ch.isdigit():
            digits = ch + digits
        else:
            break
    return int(digits) if digits else None


def _covers(b, x, z):
    """Does this row occupy that cell? An anchor is not a footprint."""
    pos = b.get("position") or {}
    if pos.get("x") == x and pos.get("z") == z:
        return True
    occ = b.get("occupies")
    if not occ:
        return False
    return (occ.get("minX", 1) <= x <= occ.get("maxX", -1)
            and occ.get("minZ", 1) <= z <= occ.get("maxZ", -1))


def _box(cells):
    """(centre x, centre z, Chebyshev radius) covering every cell."""
    xs = [c[0] for c in cells]
    zs = [c[1] for c in cells]
    cx = (min(xs) + max(xs)) // 2
    cz = (min(zs) + max(zs)) // 2
    radius = max(max(xs) - min(xs), max(zs) - min(zs)) // 2 + 2
    return cx, cz, radius


def read_cells(cells):
    """Every building, blueprint and frame over the WHOLE batch, in ONE call.

    One `home/list_buildings` read of the bounding box, not one read per cell:
    64 cells confirmed one at a time is 64 round trips and ~2 s of polling each,
    which is the cost this batching exists to remove. None means the read failed
    (which is not the same as "nothing is there", and is why it is polled).
    """
    cx, cz, radius = _box(cells)
    r = rim.game(LIST_TOOL, {"x": cx, "z": cz, "radius": radius, "status": "all",
                             "aggregate": False, "playerOnly": False},
                 strict=False)
    if not isinstance(r, dict) or not r.get("success"):
        return None
    return r.get("buildings") or []


def confirm_batch(placed, reads=CONFIRM_READS, gap=CONFIRM_GAP, pad="  "):
    """Read the whole batch back and say which of three things happened.

    `placed` is [(x, z, thingIDNumber, label)] -- what the tool says it made.
    The day-68 rule holds and is only batched: one read straight after a write
    can miss a blueprint the game has not committed yet, and "the cell reads
    empty" was printed nine times over blueprints that were really there. So
    the cells are read up to four times over ~2.1 s, every read covering the
    entire batch, and the outcome is one of three sentences that cannot be
    mistaken for each other. -> 0 only when every blueprint is confirmed.
    """
    want = dict(((row[0], row[1]), row) for row in placed)
    if not want:
        return 0
    cells = list(want)
    seen, others, taken = {}, {}, 0
    for attempt in range(max(1, int(reads))):
        if attempt:
            time.sleep(gap)
        taken = attempt + 1
        rows = read_cells(cells)
        if rows is None:
            continue
        others = {}
        for cell in cells:
            if cell in seen:
                continue
            for b in rows:
                if not _covers(b, cell[0], cell[1]):
                    continue
                if _id_number(b.get("thingId")) == want[cell][2]:
                    seen[cell] = b
                    break
                if cell not in others:
                    others[cell] = b
        if len(seen) == len(cells):
            break

    missing = [c for c in cells if c not in seen]
    if not missing:
        print("%sPLACED -- CONFIRMED: %d of %d blueprints stand at the cells "
              "asked, read back in ONE call on read %d of %d."
              % (pad, len(seen), len(cells), taken, reads))
        print("%sA builder has to carry them; `python buildings.py --pending` "
              "tracks them." % pad)
        return 0

    blocked = [c for c in missing if c in others]
    if blocked:
        named = ", ".join("%d,%d holds %s" % (c[0], c[1],
                                              others[c].get("label")
                                              or others[c].get("defName") or "?")
                          for c in blocked[:BATCH_NOTES])
        if len(blocked) > BATCH_NOTES:
            named += ", +%d more" % (len(blocked) - BATCH_NOTES)
        print("%sNOT PLACED -- SOMETHING ELSE IS THERE: %s, and the blueprint "
              "this call reported for %s did not appear over %d reads."
              % (pad, named, "those cells" if len(blocked) > 1 else "that cell", reads))
        print("%sNothing else was changed." % pad)
    rest = [c for c in missing if c not in others]
    if rest:
        cx, cz, radius = _box(cells)
        print("%sNOT VISIBLE YET: %d of %d cells show no new blueprint after %d "
              "reads over ~%.1f s. That is 'not seen', NOT 'the cells are empty' "
              "-- the game commits a placement a moment after the call returns."
              % (pad, len(rest), len(cells), reads, gap * (reads - 1)))
        print("%sCheck again with `python buildings.py --near %d %d %d --every`."
              % (pad, cx, cz, radius))
    return 1


def _cell_line(row, indent=4):
    """One line for a cell worth naming: refused, already there, or a placement
    that costs something. An ordinary placed cell prints nothing -- 64 lines
    saying "ok" is how the two that failed get missed."""
    out = row.get("outcome")
    where = "%s,%s" % (row.get("x"), row.get("z"))
    if out == "refused":
        line = " " * indent + "REFUSED %s -- %s" % (where, row.get("reason") or "no reason given")
        blocked = _blocker_line(row, indent + 4)
        return line + ("\n" + blocked if blocked else "")
    if out == "already_present":
        return " " * indent + ("ALREADY THERE %s -- an identical blueprint or the "
                               "finished thing stands here; nothing placed." % where)
    if out == "error":
        return " " * indent + "ERROR %s -- %s" % (where, row.get("error") or "no reason given")
    names = _costs_from_rows([row])
    if names:
        return " " * indent + "%s %s -- would wipe/replace: %s" % (
            "placed" if out == "placed" else "accepted", where, ", ".join(names[:4]))
    return None


def _refusal_text(b):
    """`(142,130): Space already occupied.` -> `142,130 Space already occupied`."""
    text = str(b.get("firstRefusal") or "").strip()
    if not text:
        return ""
    return text.lstrip("(").replace("): ", " ", 1).rstrip(".")


def _batch_outcome(r):
    """The batch's outcome word, corrected for the mix the companion calls
    `refused`.

    Live on Threadneedle (2026-09-11): re-running an identical 12-cell wall
    returned 0 placed, 9 already there, 3 refused, and the verdict read
    `REFUSED -- NOTHING was placed`. That is a lie by omission -- nine of those
    cells hold the blueprint this call asked for, with the same ids as the run
    before. `NOTHING was placed` is reserved for the case where nothing of ours
    stands there either.
    """
    out = _outcome(r)
    b = batch(r) or {}
    if out in ("refused", "error") and not b.get("placed") and b.get("alreadyPresent"):
        return "already_present"
    return out


def batch_verdict_line(r, cells):
    """The one line that says what this batch did, printed last, always."""
    b = batch(r) or {}
    n = b.get("requested") or len(cells)
    out = _batch_outcome(r)
    tail = _cost_tail_from(_costs_from_rows(b.get("rows") or []))
    extra = ""
    if b.get("alreadyPresent"):
        extra += ", %d already there" % b["alreadyPresent"]
    if b.get("refused"):
        extra += ", %d REFUSED" % b["refused"]
    if b.get("errors"):
        extra += ", %d errored" % b["errors"]
    first = ((" First refusal %s." % str(b["firstRefusal"]).rstrip("."))
             if b.get("firstRefusal") else "")
    ids = b.get("placedIds") or []
    idtail = ""
    if ids:
        idtail = ", blueprint ids %s" % (str(ids[0]) if len(ids) == 1
                                         else "%s..%s (%d)" % (ids[0], ids[-1], len(ids)))
    if out == "preview":
        return ("== DRY RUN -- NOTHING placed (add --do). %d of %d cell(s) "
                "accepted%s, %s facing %s%s"
                % (b.get("accepted", 0), n, extra, _span(cells),
                   b.get("rotation"), tail))
    if out in ("placed", "partial"):
        return ("== %s -- %d of %d cell(s) placed%s%s, %s facing %s.%s%s"
                % ("PLACED" if out == "placed" else "PARTLY PLACED",
                   b.get("placed", 0), n, extra, idtail, _span(cells),
                   b.get("rotation"), first, tail))
    if out == "already_present":
        already = b.get("alreadyPresent", 0)
        if already >= n:
            return ("== ALREADY THERE -- all %d cell(s) already hold this blueprint "
                    "or the finished thing; NOTHING was placed and no new blueprint "
                    "id exists." % n)
        # Some already there, some refused, nothing new. The count that says
        # what STANDS there leads; the refusals follow it, not the other way
        # round, and "NOTHING was placed" is not said over nine of our own
        # blueprints.
        why = _refusal_text(b)
        return ("== ALREADY THERE -- %d of %d cell(s) already had this blueprint, "
                "%d placed, %d REFUSED%s%s. No new blueprint id exists."
                % (already, n, b.get("placed", 0), b.get("refused", 0),
                   (", %d errored" % b["errors"]) if b.get("errors") else "",
                   (" (first: %s)" % why) if why else ""))
    if out == "error":
        return ("== ERROR -- NOTHING was placed at %d of %d cell(s).%s %s"
                % (b.get("errors", 0), n, first, reason(r) if not r.get("success") else ""))
    return ("== REFUSED -- NOTHING was placed; the game refused %d of %d cell(s).%s"
            % (b.get("refused", n), n, first))


def report_batch(r, cells, asked=None, dry=None, confirm=True):
    """The batch's output: a header, the materials for ALL the cells, only the
    cells worth naming, the read-back, and ONE `==` verdict on the last line."""
    if not isinstance(r, dict):
        # A reply that is not a payload at all. Said as itself, never rendered
        # as an empty batch.
        print("FAILED: %s" % reason(r))
        print("== NOTHING WAS PLACED.")
        return 1
    if dry is None:
        dry = _outcome(r) == "preview"
    if dry:
        print("DRY RUN -- nothing placed; add --do")
    if r.get("rotationDefaulted"):
        print("rotation defaulted to %s -- this def is not rotatable, so the game "
              "builds it Rot4.North whatever is asked for." % r["rotationDefaulted"])
    b = batch(r)
    if b is None:
        # No batch block: either the call was refused before any cell was
        # looked at, or the DLL predates batching. Both are said out loud.
        print("FAILED: %s" % reason(r))
        if asked and is_unknown_def(r):
            for line in unknown_def_lines(asked):
                print(line)
        elif r.get("success"):
            print("  This DLL answered a `cells` batch with no `batch` block, so "
                  "it predates batching. Place the cells one call at a time, or "
                  "install the current companion DLL.")
        print("== NOTHING WAS PLACED.")
        return 1

    d = r.get("def") or {}
    print("%s (%s) x%d cells, %s facing %s -- %s%s%s" % (
        d.get("label") or d.get("defName"), d.get("defName"), b.get("requested"),
        _span(cells), b.get("rotation"), _HEAD_WORD.get(_batch_outcome(r), "?"),
        "" if r.get("researchFinished", True) else " | RESEARCH NOT DONE",
        "" if r.get("rotatable", True) else " | not rotatable"))
    cost = r.get("costList") or []
    if cost:
        print("  cost: " + ", ".join("%s x%s" % (c.get("label") or c.get("defName"),
                                                 c.get("count")) for c in cost)
              + " per cell")
    _materials(r)
    # What a builder has to act on reads first. On a re-run the already-there
    # cells are the majority and the refusals are the point, so they lead; cell
    # order is kept inside each rank.
    rank = {"error": 0, "refused": 1, "placed": 2, "preview": 2, "already_present": 3}
    graded = [(rank.get(row.get("outcome"), 2), i, _cell_line(row))
              for i, row in enumerate(b.get("rows") or [])]
    notable = [line for _, _, line in sorted(g for g in graded if g[2])]
    if dry:
        print("  cells: %d accepted, %d already there, %d refused"
              % (b.get("accepted", 0), b.get("alreadyPresent", 0), b.get("refused", 0)))
    else:
        print("  cells: %d placed, %d already there, %d refused, %d errored"
              % (b.get("placed", 0), b.get("alreadyPresent", 0),
                 b.get("refused", 0), b.get("errors", 0)))
    for line in notable[:BATCH_NOTES]:
        print(line)
    if len(notable) > BATCH_NOTES:
        print("    +%d more cell(s) worth reading -- add --json for all of them"
              % (len(notable) - BATCH_NOTES))
    _cannot_build(r)
    unconfirmed = 0
    if confirm and not dry:
        placed = [(row.get("x"), row.get("z"),
                   (row.get("placed") or {}).get("thingIDNumber"),
                   (row.get("placed") or {}).get("label"))
                  for row in b.get("rows") or [] if row.get("outcome") == "placed"]
        if placed:
            unconfirmed = confirm_batch(placed)
    print(batch_verdict_line(r, cells))
    # Non-zero whenever a cell the caller asked for did not end up with the
    # blueprint on it, whatever the leading word: a chained script stops on a
    # wall with three holes in it, even one whose other nine cells were already
    # standing.
    if b.get("refused") or b.get("errors"):
        return 1
    # The read-back is the only thing that has actually LOOKED. `confirm_batch`
    # computes exactly this code ("0 only when every blueprint is confirmed")
    # and it was being thrown away, so NOT PLACED -- SOMETHING ELSE IS THERE
    # exited 0.
    if unconfirmed:
        return 1
    return 0 if _batch_outcome(r) in ("preview", "placed", "already_present") else 1


def needs_rotation(defname, x, z, stuff=None, god=False, cells=None):
    """The loud refusal for `--do` on a def that turns.

    2026-09-07, turn 32: `build.py Battery ... --do` "silently did nothing"
    twice. It was not silent -- it was a bare SystemExit on STDERR, which on a
    stream scrolls past unread, and a refusal that reads as nothing happening is
    the same as a lie. So the refusal is on stdout, in the tool's own verdict
    shape, and it does the work the caller now has to do anyway: the four
    rotations, each with the game's own verdict, and the exact command for the
    ones the game would accept. Rotation is still NOT chosen here -- for a
    cooler or a vent which way it faces is the whole question, and picking is
    how you get a cooler heating the room it should chill.

    `cells` is the batch the caller asked for, if any: a batch needs one
    rotation for the same reason and gets the same sweep, done once on the
    anchor cell rather than once per cell.
    """
    sweep = place(defname, x, z, "all", stuff, do=False, god=god, watch=False)
    if cells:
        print("BUILD REFUSED -- %s rotates, and a batch of %d cells needs ONE "
              "rotation for all of them." % (defname, len(cells)))
    else:
        print("BUILD REFUSED -- %s rotates, and a real placement needs ONE rotation."
              % defname)
    if isinstance(sweep, dict) and sweep.get("rotations"):
        print("  the four rotations, as the game judges them right now:")
        for row in sweep["rotations"]:
            line = "    %-5s %s" % (row.get("rotation"),
                                    "ok" if row.get("accepted") else "no")
            if row.get("reason"):
                line += " -- " + row["reason"]
            print(line)
            blocked = _blocker_line(row)
            if blocked:
                print(blocked)
        ok = [row.get("rotation") for row in sweep["rotations"] if row.get("accepted")]
        if ok:
            tail = (' --cells "%s"' % cells_arg(cells)) if cells else ""
            # --stuff belongs in the advice: without it the re-run builds from
            # GenStuff.DefaultStuffFor, so the tool's own suggested command
            # quietly changes the material the caller asked for.
            tail += (" --stuff %s" % stuff) if stuff else ""
            print("== NOTHING WAS PLACED. Re-run naming a rotation, e.g. "
                  "`python build.py %s %s %s %s%s --do`  (accepted: %s)"
                  % (defname, x, z, ok[0], tail, ", ".join(str(o) for o in ok)))
        else:
            print("== NOTHING WAS PLACED, and the game accepts NO rotation here "
                  "-- read the reasons above; naming a rotation will not help.")
    else:
        print("== NOTHING WAS PLACED. Name a rotation: north, east, south or "
              "west. (The rotation sweep did not come back: %s)"
              % reason(sweep))
    return 1


# Flags, and the one flag that carries a value. Anything else beginning with a
# dash is an unknown flag and is refused by name -- a flag silently dropped
# reads as a flag that worked.
FLAGS = ("--do", "--dry-run", "--dry", "--god", "--json", "--no-watch")
VALUE_FLAGS = ("--stuff", "--cells")
# `--to x z` takes TWO values, the far end of a straight line.
PAIR_FLAGS = ("--to",)
ROTATIONS = ("north", "east", "south", "west", "0", "1", "2", "3", "all")


def split_args(argv):
    """(positionals, flags, values, unknown flags).

    A token beginning with `--` is NEVER a positional: the rotation argument
    takes a rotation, and a flag in that slot is a flag. `--to` is the one flag
    that takes two values (`--to 152 130`, or `--to=152,130`).
    """
    pos, flags, values, unknown = [], [], {}, []
    i = 0
    while i < len(argv):
        a = argv[i]
        name, _, inline = a.partition("=")
        if name in PAIR_FLAGS:
            if inline:
                values[name] = inline.replace(",", " ")
            elif i + 2 < len(argv):
                values[name] = "%s %s" % (argv[i + 1], argv[i + 2])
                i += 2
            else:
                unknown.append("%s (needs two values: --to <x> <z>)" % name)
        elif name in VALUE_FLAGS:
            if inline:
                values[name] = inline
            elif i + 1 < len(argv):
                values[name] = argv[i + 1]
                i += 1
            else:
                unknown.append("%s (needs a value)" % name)
        elif a in FLAGS:
            flags.append(a)
        elif a.startswith("-") and not a[1:].isdigit():
            unknown.append(a)
        else:
            pos.append(a)
        i += 1
    return pos, flags, values, unknown


def _refuse(first, *rest):
    """A refusal in the tool's own verdict shape, on stdout. Returns 1."""
    print("BUILD REFUSED -- %s" % first)
    for line in rest:
        print("  %s" % line)
    print("== NOTHING WAS PLACED.")
    return 1


def _ints(*text):
    """The whole numbers in `text`, or None if any of them is not one."""
    out = []
    for t in text:
        try:
            out.append(int(t))
        except (TypeError, ValueError):
            return None
    return out


def main(argv):
    pos, flags, values, unknown = split_args(argv)
    if unknown:
        return _refuse(
            "unknown flag(s): %s" % ", ".join(unknown),
            "flags are: --do, --dry-run, --stuff <Def>, --to <x> <z>, "
            "--cells \"x,z;x,z\", --god, --no-watch, --json",
            "rotation is a POSITIONAL: build.py <def> <x> <z> [north|east|south|west]")
    do = "--do" in flags
    god = "--god" in flags
    watch = "--no-watch" not in flags
    as_json = "--json" in flags
    stuff = values.get("--stuff")
    cells_spec = values.get("--cells")
    to_spec = values.get("--to")
    if do and ("--dry-run" in flags or "--dry" in flags):
        return _refuse("--do and --dry-run were both given -- pass one.")
    if cells_spec and to_spec:
        return _refuse("--to and --cells were both given -- pass one.",
                       "--to is the straight line from the anchor cell; --cells "
                       "is any set of cells and can spell the same line out.")
    if len(pos) < (1 if (cells_spec or to_spec) else 3):
        print(__doc__)
        return
    defname = pos[0]
    rest = pos[1:]
    x = z = None
    pair = _ints(*rest[:2]) if len(rest) >= 2 else None
    if pair is not None:
        x, z = pair
        rest = rest[2:]
    elif len(rest) >= 2:
        # Two positionals that are not a cell. Without a batch flag this is the
        # only shape there is; with one, the cells came from the flag and these
        # two are still not a cell.
        return _refuse("x and z must be whole numbers, not %r and %r"
                       % (rest[0], rest[1]),
                       "usage: build.py <def> <x> <z> [rotation] [--stuff <Def>] [--do]")
    rot = rest[0].lower() if rest else None
    if rot is not None and rot not in ROTATIONS:
        return _refuse(
            "%r is not a rotation. The rotations are: %s."
            % (rest[0], ", ".join(ROTATIONS)),
            "a MATERIAL goes in --stuff: build.py %s %s %s --stuff %s"
            % (defname, x, z, rest[0]))
    if len(rest) > 1:
        return _refuse("too many positionals: %s" % ", ".join(rest[1:]),
                       "usage: build.py <def> <x> <z> [rotation]  -- a size is "
                       "not taken here; a line is --to <x> <z> and any other "
                       "shape is --cells \"x,z;x,z\"")

    # The batch, resolved before the game is touched: a bad cell list is a
    # refusal, never a partly-built wall.
    cells = None
    if cells_spec:
        cells, why = parse_cells(cells_spec)
        if cells is None:
            return _refuse(why,
                           'usage: build.py <def> --cells "141,130;142,130" '
                           '[rotation] [--stuff <Def>] [--do]')
        if x is not None:
            cells = [(x, z)] + [c for c in cells if c != (x, z)]
    elif to_spec:
        if x is None:
            return _refuse("--to needs the cell the line starts at.",
                           "usage: build.py <def> <x> <z> --to <x2> <z2> [--do]")
        far = _ints(*str(to_spec).split())
        if far is None or len(far) != 2:
            return _refuse("--to takes two whole numbers, not %r" % (to_spec,),
                           "usage: build.py %s %s %s --to <x2> <z2>" % (defname, x, z))
        cells = line_cells(x, z, far[0], far[1])
        if cells is None:
            return _refuse(
                "a --to line must be straight: %d,%d -> %d,%d shares neither x nor z."
                % (x, z, far[0], far[1]),
                "for a corner, an L or a room, name the cells: "
                "--cells \"x,z;x,z\" -- guessing which axis you meant is how you "
                "wall the wrong side of a room")
    if cells is not None:
        if len(cells) > MAX_BATCH:
            return _refuse(
                "%d cells is more than the %d-cell cap for one call."
                % (len(cells), MAX_BATCH),
                "Split it into runs of %d or fewer; the cap is there so one call "
                "cannot hold the game for longer than you expect." % MAX_BATCH)
        x, z = cells[0]
    rim.init()

    # 2026-09-02: `--do` with no rotation was a flat SystemExit, because the
    # argument defaulted to "all" and a real placement needs exactly one. That
    # is right for a Cooler -- which way it faces IS the question, and picking
    # for you is how you get a cooler heating the room it should be chilling --
    # and wrong for a Wall, which has `rotatable: false` and is built Rot4.North
    # no matter what is passed. Walls are most of what this tool places, so the
    # unrotatable case now answers itself: one dry run, read `rotatable` off the
    # reply, and if the def does not turn, use north and say so. A def that DOES
    # turn still gets the old refusal, word for word.
    #
    # A BATCH resolves its rotation the same way and for the same reason, once
    # for the whole line rather than once per cell: the companion refuses a
    # batch with rotation "all" (200 cells x 4 rotations is a reply nobody
    # reads), so a dry-run batch needs the answer too.
    defaulted = None
    if (do or cells is not None) and rot in (None, "all"):
        probe = place(defname, x, z, "north", stuff, do=False, god=god, watch=False)
        turns = _rotates(probe)
        if turns is None:
            # An unknown def is the answer, not an unreadable `rotatable`.
            if is_unknown_def(probe):
                return report(probe, defname)
            print("BUILD REFUSED -- asked the game whether %s rotates and got "
                  "no answer (%s), so the rotation cannot be defaulted."
                  % (defname, (probe.get("error") or probe.get("message")
                               if isinstance(probe, dict) else None)
                     or "no `rotatable` field in the reply"))
            print("== NOTHING WAS PLACED. Name a rotation: north, east, south "
                  "or west.")
            return 1
        if turns:
            return needs_rotation(defname, x, z, stuff, god, cells)
        rot = "north"
        defaulted = "north"
    if rot is None:
        rot = "all"
    if do and rot == "all":
        return needs_rotation(defname, x, z, stuff, god, cells)

    if cells is not None:
        # ONE call for every cell: one handshake, one materials scan, one camera
        # move, one 1.5 s watch lead, one read-back.
        r = place_batch(defname, cells, rot, stuff, do, god, watch)
        if defaulted and isinstance(r, dict):
            r["rotationDefaulted"] = defaulted
        if as_json:
            print(json.dumps(r, indent=1)[:8000])
            return
        code = report_batch(r, cells, defname, dry=not do)
        if do:
            watch_line(r)
        return code

    r = place(defname, x, z, rot, stuff, do, god, watch)
    # A client-side key, named so it can't be mistaken for something the game
    # said. The probe's own `accepted` is deliberately NOT reused as the
    # go-ahead: the placing call evaluates and places in one main-thread hop,
    # which is what stops "accepted" and "placed" describing different instants.
    if defaulted and isinstance(r, dict):
        r["rotationDefaulted"] = defaulted
    if as_json:
        print(json.dumps(r, indent=1)[:8000])
    else:
        # Same shape as the batch path above: the reporter owns the verdict and
        # the exit code. Discarding it here meant a reply that was not a payload
        # at all printed NOTHING WAS PLACED and still exited 0.
        code = report(r, defname, dry=not do)
        if do:
            watch_line(r)
        return code


if __name__ == "__main__":
    try:
        import overlay_client as _ov
        sys.argv[1:], _say, _mood = _ov.take_flags(sys.argv[1:])
    except Exception:
        _ov, _say, _mood = None, None, None
    _rc = main(sys.argv[1:])
    if _ov is not None:
        _ov.say_flags(_say, _mood)
    # A refusal exits non-zero so a script that chained on this one stops.
    if _rc:
        sys.exit(_rc)
