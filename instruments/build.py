"""Place a building with a ROTATION, and see which face lands where -- first.

  python build.py Cooler 114 145             # dry-run all four rotations
  python build.py Cooler 114 145 east        # dry-run one
  python build.py Cooler 114 145 east --do   # place the blueprint
  python build.py Cooler 114 145 east --do --no-watch   # place, no camera move
  python build.py Wall 110 146 --stuff WoodLog
  python build.py Wall 110 146 --do --stuff WoodLog   # no rotation needed

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
rotations the game accepted. The per-rotation rows above it are detail, never a
second verdict -- on 2026-09-07 a call printed `PLACED` and `north REFUSED` in
the same breath and only `buildings.py --pending` said which was true.

`act.apply` is still the tool for designations that have no rotation (Harvest,
Mine, Deconstruct, zones). This one is for things you build.
"""
import difflib
import json
import sys

import rim


SUGGEST = 8             # closest names printed on a miss. Never the whole menu.

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
    w = (r or {}).get("watch") or {}
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


def _blocker_line(row, indent=8):
    """One line naming what stands in the footprint, or None."""
    keep, filth = _blockers(row)
    if not keep and not filth:
        return None
    bits = ", ".join(
        "%s at %s,%s%s" % (b.get("label") or b.get("defName"),
                           (b.get("position") or {}).get("x"),
                           (b.get("position") or {}).get("z"),
                           " (haul first)" if b.get("mustBeHauledFirst") else
                           " (would be wiped)" if b.get("wouldBeWiped") else "")
        for b in keep[:6])
    if len(keep) > 6:
        bits += ", +%d more" % (len(keep) - 6)
    if not keep:
        bits = "nothing but filth"
    if filth:
        bits += "  (%d filth ignored -- filth blocks nothing)" % filth
    return " " * indent + "in the way: " + bits


def _outcome(r):
    """What happened to THIS call, in one word. `outcome` is the companion's own
    field (preview / placed / already_present / refused / error); the fallbacks
    below cover a DLL older than it.

    The 2026-09-07 bug was that the headline read `PLACED` off `dryRun` alone,
    so a call the game refused printed PLACED and a rotation row printed REFUSED
    in the same breath, and only `buildings.py --pending` said which was true.
    """
    word = r.get("outcome")
    if word in ("preview", "placed", "already_present", "refused", "error"):
        return word
    if r.get("dryRun"):
        return "preview"
    if r.get("placed"):
        return "placed"
    if r.get("alreadyPlaced"):
        return "already_present"
    return "refused" if r.get("success") is False else "error"


_HEAD_WORD = {"preview": "DRY RUN", "placed": "PLACED",
              "already_present": "ALREADY THERE", "refused": "REFUSED",
              "error": "ERROR"}


def verdict_line(r):
    """The one line that says what this call did. Printed last, always, and
    never contradicted by anything above it."""
    out = _outcome(r)
    if out == "preview":
        ok = r.get("acceptedRotations") or [row.get("rotation")
                                            for row in (r.get("rotations") or [])
                                            if row.get("accepted")]
        n = r.get("rotationsEvaluated") or len(r.get("rotations") or [])
        return ("== DRY RUN -- nothing was placed. %d of %d rotation(s) accepted%s"
                % (len(ok), n, (": " + ", ".join(str(o) for o in ok)) if ok else ""))
    if out == "placed":
        p = r.get("placed") or {}
        return ("== PLACED -- blueprint id %s, %s at %s,%s facing %s"
                % (p.get("thingIDNumber"), p.get("label") or p.get("defName"),
                   (p.get("position") or {}).get("x"),
                   (p.get("position") or {}).get("z"),
                   p.get("rotation") or p.get("rotationWord")))
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


def report(r, asked=None):
    """`asked` is the def name the caller typed; a reply that failed to resolve
    carries no `def` block, so it cannot be recovered from `r`."""
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
        return
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
    for key, verb in (("wiped", "wiped"), ("wipeOnPlace", "would wipe"),
                      ("framesCancelled", "cancelled frame"), ("framesCancelledOnPlace", "would cancel frame")):
        for w in r.get(key) or []:
            print("  %s: %s at %s,%s" % (verb, w.get("label") or w.get("defName"),
                                         (w.get("position") or {}).get("x"), (w.get("position") or {}).get("z")))
    # ONE verdict per call, last, unmissable.
    print(verdict_line(r))


def needs_rotation(defname, x, z, stuff=None, god=False):
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
    """
    sweep = place(defname, x, z, "all", stuff, do=False, god=god, watch=False)
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
            print("== NOTHING WAS PLACED. Re-run naming a rotation, e.g. "
                  "`python build.py %s %s %s %s --do`  (accepted: %s)"
                  % (defname, x, z, ok[0], ", ".join(str(o) for o in ok)))
        else:
            print("== NOTHING WAS PLACED, and the game accepts NO rotation here "
                  "-- read the reasons above; naming a rotation will not help.")
    else:
        print("== NOTHING WAS PLACED. Name a rotation: north, east, south or "
              "west. (The rotation sweep did not come back: %s)"
              % reason(sweep))
    return 1


def main(argv):
    do = "--do" in argv
    god = "--god" in argv
    watch = "--no-watch" not in argv
    stuff = argv[argv.index("--stuff") + 1] if "--stuff" in argv else None
    argv = [a for a in argv if a not in ("--do", "--god", "--json", "--no-watch")
            and a != "--stuff" and a != stuff]
    if len(argv) < 3:
        print(__doc__)
        return
    defname, x, z = argv[0], int(argv[1]), int(argv[2])
    rot = argv[3].lower() if len(argv) > 3 else None
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
    defaulted = None
    if do and rot is None:
        probe = place(defname, x, z, "north", stuff, do=False, god=god, watch=False)
        turns = _rotates(probe)
        if turns is None:
            # An unknown def is the answer, not an unreadable `rotatable`.
            if is_unknown_def(probe):
                report(probe, defname)
                return
            print("BUILD REFUSED -- asked the game whether %s rotates and got "
                  "no answer (%s), so the rotation cannot be defaulted."
                  % (defname, (probe.get("error") or probe.get("message")
                               if isinstance(probe, dict) else None)
                     or "no `rotatable` field in the reply"))
            print("== NOTHING WAS PLACED. Name a rotation: north, east, south "
                  "or west.")
            return 1
        if turns:
            return needs_rotation(defname, x, z, stuff, god)
        rot = "north"
        defaulted = "north"
    if rot is None:
        rot = "all"
    if do and rot == "all":
        return needs_rotation(defname, x, z, stuff, god)

    r = place(defname, x, z, rot, stuff, do, god, watch)
    # A client-side key, named so it can't be mistaken for something the game
    # said. The probe's own `accepted` is deliberately NOT reused as the
    # go-ahead: the placing call evaluates and places in one main-thread hop,
    # which is what stops "accepted" and "placed" describing different instants.
    if defaulted and isinstance(r, dict):
        r["rotationDefaulted"] = defaulted
    if "--json" in sys.argv:
        print(json.dumps(r, indent=1)[:8000])
    else:
        report(r, defname)
        if do:
            watch_line(r)


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
