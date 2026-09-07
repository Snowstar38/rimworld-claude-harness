"""Designator helpers. The architect list returns `id` (not `designatorId`), and
`apply_architect_designator` wants that id, so look it up by label once.

  import act; act.apply("Harvest", 139, 145)
  act.clear()                                # drop the still-armed designator
  act.apply("Harvest", 139, 145, dry=True)   # validate without mutating

**`apply(label, x, z, w, h)` takes a WIDTH and a HEIGHT, not a second corner.**
`act.apply('Cooler', 113, 146, 1, 1)` is one cell at 113,146. Read as corners it
looks like the same call and is not: `act.apply('Cooler', 113, 146, 113, 146)`
asks the game for a 113x146 rectangle of coolers.

A label two categories both use is AMBIGUOUS and raises rather than picking one:
pass the pair instead -- `act.apply(("Structure", "Wall"), ...)`. Two cases are
NOT ambiguity and are resolved here rather than raised (see `_pick`): a dropdown
listed next to its own active child, and one designator listed once per category.

Lookup is case- and ellipsis-insensitive: the menu's real label is `wall...`,
and `Wall`, `wall` and `wall...` all resolve to it. A miss names the ~8 closest
labels, never the whole 250-entry menu -- `python act.py labels [word]` is the
listing.

  python act.py apply "Mine" 90 120 8 8          # dry run; sizes are positional
  python act.py apply "Mine" 90 120 --size 8 8   # ... or --size, or --width/--height
  python act.py hunt Alpaca            # resolve the CURRENT cell, dry run
  python act.py hunt Alpaca --do       # read + designate in ONE process
  python act.py tame Fox --do          # same machinery, Tame instead of Hunt
  python act.py allow 120 140 4 4      # unforbid loose items (alias: unforbid)
  python act.py undesignate 120 140 4 4   # Cancel: designations AND blueprints/
                                          # frames; dry, --do applies

Every write here is DRY BY DEFAULT and a dry run says so in the words
`DRY RUN - would designate N cell(s) (nothing applied; add --do)`. The applied
wording -- `APPLIED N cell(s)`, `N cell(s) newly designated` -- is printed only
after a real apply, and is printed AGAIN under the `!!` warning block so it
cannot be scrolled past (turns 5 and 16).

`hunt` exists because animals walk: a cell read by `pawns.py` in one process is
stale by the time a second process designates it, which is the whole of
"Must designate huntable animals" on a cell that had an alpaca on it.
"""
import argparse
import difflib
import json
import sys

import pick
import rim

# (category, label) -> id, plus the bare label for every label that means one
# id across the whole architect menu.
_cache = {}
# bare label -> [category, ...], for the labels that mean more than one id.
_ambiguous = {}
# normalised label -> the spelling the game actually uses, for error messages.
_exact = {}
# designator id -> the designation defName it PLACES. A build or zone tool
# places none and is absent.
_designation = {}
# (category label, designator payload) for every row the menu has, so the index
# can be rebuilt without a second round of bridge calls.
_rows = []
# designator id -> its architect row, for the questions the designation defName
# cannot answer: is this an AREA or a ZONE tool rather than a designation?
_meta = {}

SUGGEST = 8             # closest labels named on a miss. Never the whole menu.
SAMPLE = 6              # cells named in an area preview
CELLS_TOOL = "home/get_cells_plus"
CELL_CAP = 1024         # get_cells_plus refuses a bigger rectangle outright

# When one designator is listed once per category -- `Cancel`, `Deconstruct` --
# these categories win, in this order, instead of the bare label raising.
# Ordering only: every one of those ids does the same thing.
CATEGORY_PREFERENCE = ("Orders", "Structure")

# RimWorld's CompForbiddable toggle is drawn as **Allow**, checked when the
# thing is allowed. There is no Unforbid designator; this gizmo is the route.
ALLOW_GIZMO = "allow"


def _norm(label):
    """Fold case, whitespace and the menu's trailing `...` into one key."""
    return " ".join(str(label).split()).lower().rstrip(".… ")


def _applies(d):
    """Can this row be handed to `apply_architect_designator` at all?

    The bridge flattens a `Designator_Dropdown` into the dropdown row plus one
    row per child, and the dropdown reports its ACTIVE child's label -- so one
    label can be two rows in one category, and the dropdown one refuses every
    cell. A build emitting neither field flattened no dropdowns, so every row
    of it is appliable.
    """
    if "supportsCellApplication" in d:
        return bool(d["supportsCellApplication"])
    return str(d.get("kind") or "") != "dropdown"


def _identity(d):
    """What makes two rows the SAME designator wearing two ids."""
    return tuple(d.get(k) for k in ("className", "designationDefName",
                                    "buildableDefName", "stuffDefName"))


def _pick(rows):
    """The one designator these `(category, row)` pairs mean, or None.

    Two collisions are not ambiguity and are resolved rather than raised:

    * a dropdown listed beside its own active child -- prefer the row that can
      be applied to a cell (see `_applies`);
    * one designator listed once per category, as `Cancel` and `Deconstruct`
      are -- identical class, designation, buildable and stuff means identical
      behaviour, so pick by CATEGORY_PREFERENCE.

    Anything else is two different tools sharing a label, and None makes the
    caller say which.
    """
    usable = [(c, d) for c, d in rows if _applies(d)] or list(rows)
    if len({d.get("id") for _, d in usable}) == 1:
        return usable[0][1]
    if len({_identity(d) for _, d in usable}) == 1:
        order = {c.lower(): i for i, c in enumerate(CATEGORY_PREFERENCE)}
        return sorted(usable, key=lambda r: (order.get(str(r[0]).lower(), len(order)),
                                             str(r[0]).lower()))[0][1]
    return None


def _index():
    """Rebuild every lookup from `_rows`. Dry run and `--do` share this path."""
    for table in (_cache, _ambiguous, _exact, _designation, _meta):
        table.clear()
    by_pair, by_label = {}, {}
    for cat, d in _rows:
        label = _norm(d.get("label"))
        _exact.setdefault(label, d.get("label"))
        if d.get("designationDefName"):
            _designation[d.get("id")] = d["designationDefName"]
        _meta[d.get("id")] = d
        by_pair.setdefault((cat, d.get("label")), []).append((cat, d))
        by_label.setdefault(label, []).append((cat, d))
    for (cat, label), rows in by_pair.items():
        pick = _pick(rows)
        if pick is None:
            continue
        _cache.setdefault((cat, label), pick.get("id"))
        _cache.setdefault((_norm(cat), _norm(label)), pick.get("id"))
    for label, rows in by_label.items():
        pick = _pick(rows)
        if pick is None:
            _ambiguous[label] = sorted({str(c) for c, _ in rows})
        else:
            _cache[label] = _cache[_exact[label]] = pick.get("id")


def _load():
    """Enumerate every category once, then index it."""
    del _rows[:]
    for cat in rim.game("rimworld/list_architect_categories", {}).get("categories") or []:
        for d in rim.game("rimworld/list_architect_designators",
                          {"categoryId": cat["id"]}).get("designators") or []:
            _rows.append((cat["label"], d))
    _index()


def _key(label):
    """The normalised form of a bare label or a (category, label) pair."""
    if isinstance(label, tuple):
        return tuple(_norm(part) for part in label)
    return _norm(label)


def _bare(label):
    """Just the label, whether a bare one or a (category, label) pair came in."""
    key = _key(label)
    return key[-1] if isinstance(key, tuple) else key


def suggest(label, limit=SUGGEST):
    """The closest labels to a miss: prefix/substring first, then difflib."""
    want = _bare(label)
    pool = dict(_exact)
    pool.update({k: k for k in _ambiguous})
    hits = [k for k in sorted(pool) if want and want in k]
    hits += [k for k in difflib.get_close_matches(want, sorted(pool), n=limit)
             if k not in hits]
    return [pool[k] for k in hits[:limit]]


def designator(label):
    """id for a `(category, label)` pair, or for an unambiguous bare label.

    Case- and ellipsis-insensitive: `Wall` finds the menu's `wall...`.
    """
    if not _cache:
        _load()
    key = _key(label)
    for k in (label, key):
        if not isinstance(k, tuple) and k in _ambiguous:
            raise KeyError(
                "designator %r is in %d categories with different ids (%s) -- "
                "name one: designator((%r, %r)), or --category %s"
                % (label, len(_ambiguous[k]), ", ".join(_ambiguous[k]),
                   _ambiguous[k][0], _exact.get(k, label), _ambiguous[k][0]))
        if k in _cache:
            return _cache[k]
    # Never dump the whole ~250-entry menu: a caller that retries would print
    # it again, and the answer was eight lines wide either way.
    close = suggest(label)
    raise KeyError(
        "no designator %r (labels are case-insensitive and `wall...` == `Wall`)"
        "%s -- full menu: python act.py labels [word]"
        % (label, "; closest: " + ", ".join(repr(c) for c in close)
           if close else "; nothing close"))


def designation(label):
    """The designation defName this label PLACES, or None for a build/zone tool.

    The game refuses an already-designated cell with the same sentence it uses
    for one it could never designate; this tells them apart (`_noop_reason`).
    """
    return _designation.get(designator(label))


def paints(label):
    """Where an APPLIED write by this designator can be SEEN, or None.

    A designation lands on the designation layer and `map.py --layers desig`
    shows it. An AREA (`Designator_AreaBuildRoof`, `AreaNoRoof`, the home and
    allowed areas) and a ZONE (`Designator_ZoneAdd`) do not: they write to
    `Map.areaManager` / `Map.zoneManager`, which that layer never reads. Turn
    2026-09-07/37 applied `Build roof area`, saw `APPLIED 15 cell(s)`, found
    nothing on the desig layer and could not tell a lie from a blind spot.
    The `%s` slots are x, z, width, height.
    """
    try:
        d = _meta.get(designator(label)) or {}
    except Exception:
        return None
    cn = str(d.get("className") or "")
    kind = str(d.get("applicationKind") or "").lower()
    if "Designator_Area" in cn or kind == "area":
        return ("this is an AREA, not a designation: `map.py --layers desig` "
                "shows NOTHING here and that is correct. The read-back is "
                "`python map.py areas %s %s %s %s`.")
    if "Designator_Zone" in cn or kind == "zone":
        return ("this is a ZONE, not a designation: `map.py --layers desig` "
                "shows NOTHING here and that is correct. The read-back is "
                "`python map.py %s %s %s %s --layers stock` (or `zones.py`).")
    return None


def labels():
    """Every bare label, plus the ambiguous ones with their categories."""
    if not _cache:
        _load()
    rows = [(_exact[k], "") for k in sorted(_exact) if k not in _ambiguous]
    rows += [(_exact.get(k, k), " (ambiguous: %s -- pass --category)" % ", ".join(v))
             for k, v in sorted(_ambiguous.items())]
    return sorted(rows)


def apply(label, x, z, w=1, h=1, dry=False, keep=False):
    # strict=False: for a designator, success:false means "no cell accepted",
    # which is the answer (the dry-run oracle lives on exactly that), not a fault.
    r = rim.game("rimworld/apply_architect_designator",
                 {"designatorId": designator(label), "x": x, "z": z,
                  "width": w, "height": h, "dryRun": dry,
                  "keepSelected": keep}, strict=False)
    if not isinstance(r, dict):
        return {"raw": r}
    return {k: v for k, v in r.items() if k not in ("operation", "state")}


# ------------------------------------------------------------ reading back ---
# Three questions the designator reply cannot answer -- was this cell already
# designated, does it hold something we built, is this item forbidden -- and
# one tool that answers all three.


def cells(x, z, w=1, h=1, fields="designations,things",
          thing_fields="defName,label,className,forbidden,build"):
    """Per-cell designations and things over a rect, or None when unavailable.

    None is "not read", never "nothing there": a rectangle over the tool's
    1024-cell cap, a missing companion and a refusal all land here, and every
    caller says out loud that it did not look rather than reporting an empty map.
    """
    if x is None or z is None or w < 1 or h < 1 or w * h > CELL_CAP:
        return None
    r = rim.game(CELLS_TOOL, {"x": x, "z": z, "width": w, "height": h,
                              "fields": fields, "thingFields": thing_fields,
                              "sparse": True}, strict=False)
    if not isinstance(r, dict) or not r.get("success") or "cells" not in r:
        return None
    return r["cells"] or []


def _desig_names(cell):
    """The designation names on one cell, lowercased. Shape-tolerant."""
    out = set()
    for d in cell.get("designations") or []:
        name = d
        if isinstance(d, dict):
            name = (d.get("defName") or d.get("designationDefName")
                    or d.get("label") or d.get("name"))
        if name:
            out.add(str(name).lower())
    return out


def _is_built(thing):
    """Something we put there: a building, a blueprint or a frame.

    `className` is the runtime type name, so natural rock (`Mineable`), plants
    and filth do not match while `Building*` does. A steam geyser matches too;
    this drives a warning, not a refusal, so a generous match is the safe error.
    """
    if thing.get("isBlueprint") or thing.get("isFrame"):
        return True
    return str(thing.get("className") or "").startswith("Building")


# Only rock chunks carry `designateHaulable` in vanilla (Various_Stone.xml's
# `ChunkBase` is the ONE def in Core that sets it, plus Anomaly's stuff items).
# `Designator_Haul.CanDesignateCell` calls `GetFirstHaulable`, which walks the
# cell for `def.designateHaulable` and returns "MessageMustDesignateHaulable"
# when it finds none -- so steel, wood, components and corpses can NEVER be
# Haul-designated. They do not need to be: they are hauled by the Hauling work
# type on their own. Turn 2026-09-07 read that message over a base full of
# loose resources as a broken tool.
HAUL_ONLY_CHUNKS = (
    "the Haul designation only applies to ROCK CHUNKS -- `designateHaulable` "
    "is set on vanilla's ChunkBase and nothing else, and "
    "Designator_Haul.CanDesignateCell says \"Must designate haulable items\" "
    "for a cell with no chunk in it. Every other item (steel, wood, "
    "components, corpses) is hauled by the Hauling work type without a "
    "designation: `python order.py haul <thing> --do` forces one now, and a "
    "stockpile that accepts it is what makes it happen on its own")


def _designation_of(label):
    """`designation(label)`, or None when the menu cannot be reached.

    A read-back that cannot name the designation still has the Cancel and Haul
    rules to apply, so a bridge that will not answer must not take the whole
    classification down with it.
    """
    try:
        return designation(label)
    except Exception:
        return None


def _cell_note(bare, want, cell, reason=""):
    """(kind, text) for one rejected cell, or None when the refusal is real.

    `kind` is "noop" when the cell is already in the state the designator would
    have put it in. Reading the map back AFTER the apply answers exactly this
    question: a REJECTED cell was not touched by the call, so what sits on it
    now is what sat on it before.
    """
    names = _desig_names(cell)
    things = cell.get("things") or []
    if bare == "cancel":
        # Cancel removes a blueprint, a frame or a cancelable designation. A
        # cell with none of the three has nothing to cancel, and the game
        # refuses it with the same "the designator rejected this cell" it uses
        # for a real refusal -- three sightings, turns 6, 12 and 14.
        if names or any(t.get("isBlueprint") or t.get("isFrame") for t in things):
            return None
        return ("noop", "nothing to cancel (built, no blueprint/designation)")
    if want and want.lower() in names:
        pass                                  # handled below, before the rest
    elif bare in ("haul things", "haul"):
        if "storage" in str(reason).lower():
            return ("noop", "already in valid storage -- "
                            "Designator_Haul.CanDesignateThing refuses it "
                            "because it is where it belongs")
        return ("refused", HAUL_ONLY_CHUNKS)
    if want and want.lower() in names:
        # RimWorld's own wording for this is silence: Designator_Mine's
        # CanDesignateCell returns AcceptanceReport.WasRejected -- rejected
        # with an EMPTY reason -- the moment DesignationAt(c, Mine) is non-null,
        # and the bridge prints "the designator rejected this cell" for it, the
        # same sentence a solid floor gets. Three sightings, turns 22/24/36.
        return ("noop", "already carries the %s designation (the game refuses "
                        "a duplicate)" % want)
    return None


def _rejected_notes(label, result, x, z, w, h):
    """Per-cell verdicts for the cells the designator rejected.

    -> {(x, z): (kind, text)}; a cell whose refusal is real gets no entry and
    keeps the game's own reason. Empty when the rectangle was not read, which
    is never reported as "nothing was wrong with it".

    The 2026-09-06 already-designated fix could not see this case because it
    was all-or-nothing (every rejected cell had to be a no-op) AND was only
    consulted when the designator accepted ZERO cells. A Mine sweep over a rock
    face with nineteen standing orders in it fails both tests at once.
    """
    rejected = [((c.get("x"), c.get("z")), c.get("reason") or "")
                for c in result.get("rejectedCells") or []]
    if not rejected:
        return {}
    grid = cells(x, z, w, h)
    if grid is None:
        return {}
    by_cell = {(c.get("x"), c.get("z")): c for c in grid}
    bare, want = _bare(label), _designation_of(label)
    notes = {}
    for cell, reason in rejected:
        c = by_cell.get(cell)
        if c is None:
            # `sparse: True` drops a cell with no things, no designations and
            # no zone, so an absent cell is an EMPTY one, not an unread one.
            c = {"x": cell[0], "z": cell[1], "things": [], "designations": []}
        note = _cell_note(bare, want, c, reason)
        if note:
            notes[cell] = note
    return notes


def _noop_reason(label, result, x, z, w, h, notes=None):
    """The single NO-OP line for a call that accepted nothing, or None.

    Printed instead of a refusal when every rejected cell was already in the
    wanted state; the per-cell lines carry the detail either way.
    """
    rejected = [(c.get("x"), c.get("z")) for c in result.get("rejectedCells") or []]
    if not rejected:
        return None
    if notes is None:
        notes = _rejected_notes(label, result, x, z, w, h)
    if _bare(label) == "forbid":
        # Forbid is not a designation -- it is the per-thing CompForbiddable
        # toggle -- so the designation test cannot see it.
        grid = cells(x, z, w, h)
        if grid is None:
            return None
        loose = [t for c in grid for t in (c.get("things") or []) if "forbidden" in t]
        if loose and all(t.get("forbidden") for t in loose):
            return ("every forbiddable thing here (%d) is already forbidden; "
                    "`act.py allow` is the other direction" % len(loose))
        return None
    noop = [cell for cell in rejected
            if (notes.get(cell) or ("", ""))[0] == "noop"]
    if not noop or len(noop) != len(rejected):
        return None
    if _bare(label) == "cancel":
        if len(noop) == 1:
            return ("nothing to cancel at %s,%s (built, no "
                    "blueprint/designation)" % noop[0])
        return ("nothing to cancel at %d cell(s) (built, no "
                "blueprint/designation): %s"
                % (len(noop), " ".join("%s,%s" % c for c in noop[:SAMPLE])
                   + (" ..." if len(noop) > SAMPLE else "")))
    want = _designation_of(label)
    return ("%d cell(s) already carry the %s designation -- the game refuses "
            "a duplicate, so there is nothing left to do" % (len(noop), want))


def _preview(result, x, z, w, h, dry=False):
    """Which cells an area designation took, and what they touch.

    An accepted-cell COUNT cannot show that a sweep re-armed a cell somebody
    cancelled by hand, so name the cells and flag any sitting on or beside a
    built structure or blueprint. A warning, never a refusal.

    A dry run says so on this line too: the `!! touches built structure` block
    below it is long, and a reader who scrolled past the summary read the cell
    list as the record of an apply that had not happened.
    """
    took = [(c.get("x"), c.get("z")) for c in result.get("acceptedCells") or []]
    if not took:
        return
    shown = " ".join("%s,%s" % c for c in took[:SAMPLE])
    if dry:
        print("  DRY RUN - would designate %d cell(s) (nothing applied; add "
              "--do): %s%s" % (len(took), shown, " ..." if len(took) > SAMPLE else ""))
    else:
        print("  %d cell(s) newly designated: %s%s"
              % (len(took), shown, " ..." if len(took) > SAMPLE else ""))
    # The rectangle plus a one-cell border: a neighbour outside the sweep counts.
    bx, bz = max(0, x - 1), max(0, z - 1)
    grid = cells(bx, bz, w + (x - bx) + 1, h + (z - bz) + 1)
    if grid is None:
        print("  (no built-structure check: %s will not read this rectangle "
              "plus its border in one call, cap %d cells)" % (CELLS_TOOL, CELL_CAP))
        return
    built = {}
    for c in grid:
        for t in c.get("things") or []:
            if _is_built(t):
                built[(c.get("x"), c.get("z"))] = t.get("label") or t.get("defName")
                break
    warned = 0
    for cx, cz in took:
        for nx, nz in ((cx, cz), (cx + 1, cz), (cx - 1, cz), (cx, cz + 1), (cx, cz - 1)):
            if (nx, nz) in built:
                print("  !! touches built structure at %s,%s (%s)"
                      % (nx, nz, built[(nx, nz)]))
                warned += 1
                break
    if warned and not dry:
        # Turn 5: a Cancel sweep printed nothing but `!!` lines and was read as
        # having failed, while it had cancelled twelve blueprints. The warnings
        # are warnings; the apply still happened, and says so again under them.
        print("  -- APPLIED %d cell(s) (the %d !! line(s) above are warnings; "
              "nothing was undone)" % (len(took), warned))
    elif warned:
        print("  -- DRY RUN - nothing was applied; the %d !! line(s) above are "
              "what a real apply would touch" % warned)


# ------------------------------------------------------------------ allow ----


def forbidden_cells(x, z, w=1, h=1):
    """[(x, z, [label, ...])] for every cell holding a forbidden thing.

    Raises rather than returning [] when the read did not happen: an unread
    rectangle reported as "nothing forbidden here" is a silent zero.
    """
    grid = cells(x, z, w, h, fields="things",
                 thing_fields="defName,label,forbidden")
    if grid is None:
        raise rim.BridgeError(
            "%s did not read %sx%s at %s,%s (cap %d cells) -- nothing was "
            "looked at, so nothing can be said about what is forbidden"
            % (CELLS_TOOL, w, h, x, z, CELL_CAP))
    out = []
    for c in grid:
        names = [t.get("label") or t.get("defName")
                 for t in (c.get("things") or []) if t.get("forbidden")]
        if names:
            out.append((c.get("x"), c.get("z"), names))
    return sorted(out)


def allow(x, z, w=1, h=1, do=False):
    """Unforbid every forbidden item in a cell or rectangle. -> a report dict.

    **There is no Unforbid designator.** `Forbid` is one; its opposite is the
    per-thing `Allow` toggle on the gizmo bar, so this is `click_cell` ->
    `list_selected_gizmos` -> `execute_gizmo` per cell. `home/building_config`
    has a `forbidden` write but addresses only colony buildings, blueprints and
    frames, not loose items. `unforbidden` is read back off the map afterwards
    rather than inferred from the gizmo call.
    """
    found = forbidden_cells(x, z, w, h)
    report = {"dryRun": not do, "found": found, "unforbidden": [],
              "stillForbidden": [], "failed": []}
    if not do or not found:
        report["stillForbidden"] = list(found)
        return report
    # An armed architect designator swallows click_cell and returns an empty
    # selection with no error. Drop it first.
    pick.clear_designator()
    for cx, cz, _ in found:
        # Through pick: the camera has to be ON the cell or the click does
        # nothing and still reports success (turn 12), and the selection is
        # read back so a missing Allow gizmo can name what WAS selected.
        try:
            sel = pick.select_cell(cx, cz, clear=False)
        except (pick.ClickMissed, pick.CameraStuck) as e:
            report["failed"].append((cx, cz, str(e)))
            continue
        listed = rim.game("rimworld/list_selected_gizmos", {}, strict=False)
        rows = listed.get("gizmos") or [] if isinstance(listed, dict) else []
        hit = next((r for r in rows if _norm(r.get("label")) == ALLOW_GIZMO
                    and not r.get("disabled")), None)
        if hit is None:
            report["failed"].append(
                (cx, cz, "no enabled Allow gizmo on the selection (%d gizmo(s)); "
                 "the click selected: %s" % (len(rows), sel["text"])))
            continue
        r = rim.game("rimworld/execute_gizmo", {"gizmoId": hit.get("id")},
                     strict=False)
        if isinstance(r, dict) and r.get("success") is False:
            report["failed"].append((cx, cz, r.get("message") or "execute_gizmo refused"))
    still = forbidden_cells(x, z, w, h)
    left = {(c[0], c[1]) for c in still}
    report["stillForbidden"] = still
    report["unforbidden"] = [c for c in found if (c[0], c[1]) not in left]
    return report


# ----------------------------------------------------------------- animals ---


def _ids(p):
    """Every spelling of this pawn's ThingID, `Thing_` prefix on and off.

    `home/list_pawns` puts the ThingID in `animals{}` and `settings{}`, never at
    the top level of a pawn row, so a read without those blocks carries no id.
    """
    raw = [p.get("thingId")]
    for block in ("animals", "settings"):
        b = p.get(block)
        if isinstance(b, dict):
            raw.append(b.get("thingId"))
    out = set()
    for value in raw:
        if value:
            value = str(value).lower()
            out.add(value)
            out.add(value[6:] if value.startswith("thing_") else "thing_" + value)
    return out


def _animal_id(p):
    """The ThingID `pawns.py --animals` prints for this animal, or None."""
    for block in ("animals", "settings"):
        b = p.get(block)
        if isinstance(b, dict) and b.get("thingId"):
            return b["thingId"]
    return p.get("thingId")


def _animal_matches(token, rows):
    """thingId exact, else a case-insensitive hit on name/defName/kindDef."""
    want = str(token).strip().lower()
    exact = [p for p in rows if want in _ids(p)]
    if exact:
        return exact
    return [p for p in rows
            if any(want in str(p.get(k) or "").lower()
                   for k in ("name", "defName", "kindDef"))]


def _read_animals():
    """Every living animal, with the animals{} block that carries the ThingID.

    `animalsOnly=True` narrows and adds no id; `animals=True` adds the block
    and narrows nothing. Both are needed, or every id reads None.
    """
    import pawns
    return pawns.all_pawns(animalsOnly=True, animals=True)


def _standing(p, label):
    """True when this animal already carries the designation `label` places.

    `animals.designations` hoists hunt/tame/slaughter as bools, so the duplicate
    the game would refuse is visible in the read we already made.
    """
    key = _bare(label)
    des = ((p.get("animals") or {}).get("designations") or {})
    return bool(des.get(key)) if key in des else False


def _at(rows, p):
    """The other animals standing on this one's cell.

    A cell designator's refusal names whichever thing in the cell it tried, so
    it can name an animal other than the target. Naming the neighbours is what
    makes that readable.
    """
    pos = p.get("position") or {}
    out = []
    for other in rows:
        if other is p:
            continue
        q = other.get("position") or {}
        if q.get("x") == pos.get("x") and q.get("z") == pos.get("z"):
            out.append(other)
    return out


def designate_animal(label, token, do=False, every=False):
    """Resolve an animal's CURRENT cell and designate in the SAME process.

    Animals walk, so a cell read by one `pawns.py` process is already stale
    when a second process designates it.

    -> [(pawn, result, others in the same cell)]
    """
    rows = _read_animals()
    hits = _animal_matches(token, rows)
    if not hits:
        raise KeyError("no living animal matches %r (python pawns.py --animals)"
                       % token)
    if len(hits) > 1 and not every:
        raise KeyError(
            "%r matches %d animals -- pass --all, or one thingId: %s"
            % (token, len(hits),
               ", ".join("%s %s at %s,%s"
                         % (p.get("name") or p.get("defName"), _animal_id(p),
                            (p.get("position") or {}).get("x"),
                            (p.get("position") or {}).get("z"))
                         for p in hits[:SUGGEST])))
    out = []
    for p in hits:
        # A read and a cell designator are separate bridge calls, so a running
        # animal can still cross a cell between them. Re-resolve and retry only
        # the game's stale-cell refusal; every other refusal is authoritative.
        tid = _animal_id(p)
        result = None
        for attempt in range(3):
            if tid:
                rows = _read_animals()
                fresh = _animal_matches(tid, rows)
                if fresh:
                    p = fresh[0]
            if _standing(p, label):
                # The game refuses a duplicate with the same sentence it uses
                # for "not huntable", so say which this is instead of retrying.
                result = {"success": True, "dryRun": not do,
                          "alreadyDesignated": True,
                          "message": "already carries the %s designation" % label}
                break
            pos = p.get("position") or {}
            result = apply(label, pos.get("x"), pos.get("z"), dry=not do)
            reason = " ".join(str(x.get("reason") or "")
                              for x in result.get("rejectedCells") or [])
            message = str(result.get("message") or "")
            if result.get("success") or "Must designate" not in (reason + " " + message):
                break
        out.append((p, result, _at(rows, p)))
    return out


def hunt(token, do=False, every=False):
    """`Hunt` on an animal's current cell. See `designate_animal`."""
    return designate_animal("Hunt", token, do=do, every=every)


def tame(token, do=False, every=False):
    """`Tame` on an animal's current cell. See `designate_animal`."""
    return designate_animal("Tame", token, do=do, every=every)


# The designator `clear()` arms and immediately drops. `Cancel` is one instance
# per architect category with a different id in each; `_pick` resolves that by
# CATEGORY_PREFERENCE, and naming the category here documents which is meant.
CLEAR_DESIGNATOR = ("Orders", "Cancel")


def clear():
    """Drop whatever architect designator is armed.

    apply_architect_designator defaults to keepSelected=True, and an armed build
    designator swallows every later click_cell - selection silently returns 0
    objects with no error anywhere. press_cancel does not clear it.

    **There is no deselect tool.** The bridge exposes get_designator_state,
    select_architect_designator (which hard-refuses a null id) and
    apply_architect_designator, and nothing else touches
    `Find.DesignatorManager`. Its one `Deselect()` call lives in
    apply_architect_designator, OUTSIDE the `!dryRun && acceptedCells > 0`
    guard -- so `dryRun: true, keepSelected: false` deselects while mutating
    nothing, which is why this apparently-odd call is the right one. It also
    means the id is a vehicle, not a meaning: apply SELECTS the designator
    before deselecting it, so any resolvable id clears the armed one.
    """
    rim.game("rimworld/apply_architect_designator",
             {"designatorId": designator(CLEAR_DESIGNATOR), "x": 0, "z": 0,
              "dryRun": True, "keepSelected": False}, strict=False)
    return rim.game("rimworld/get_designator_state", {}).get("designatorState", {}).get("hasSelection")


# ------------------------------------------------------------ undesignate ---
#
# There is no `remove_designation` anywhere: not in the 147 bridge tool names
# and not in the companion (checked 2026-09-06). The architect `Cancel`
# designator is the only route, and `apply_architect_designator` drives it
# through `DesignateSingleCell` -- so it reaches designations that sit on a
# CELL (Mine, Harvest, Chop, Smooth, Haul, Deconstruct) and not the ones the
# game hangs on a THING. Tame, Hunt and Slaughter live on the animal, which is
# why `Cancel` refuses the cell an animal is standing on (turn 2).
#
# Cancel also DESTROYS blueprints and frames (`CanDesignateThing` ends on
# `t.Faction == Faction.OfPlayer && (t is Frame || t is Blueprint)` and
# `DesignateThing` calls `t.Destroy(DestroyMode.Cancel)`), so both are counted
# before the drag and both are read back after.
#
# So this command does not claim a clean sweep: it reads the rectangle BEFORE,
# cancels, reads it AGAIN, and names every designation and every blueprint that
# survived. What is needed to remove the rest is written up in the report as
# `home/remove_designation`.
UNDESIGNATE_DESIGNATOR = ("Orders", "Cancel")

THING_DESIGNATIONS = ("tame", "hunt", "slaughter", "releaseanimaltowild")


def designations_in(x, z, w=1, h=1, grid=None):
    """[(x, z, [name, ...])] for every designated cell in the rect.

    Raises rather than returning [] when the read did not happen: an unread
    rectangle reported as "nothing designated here" is a silent zero, and this
    command's whole job is to say what is still designated.
    """
    if grid is None:
        grid = cells(x, z, w, h, fields="designations,things")
    if grid is None:
        raise rim.BridgeError(
            "%s did not read %sx%s at %s,%s (cap %d cells) -- nothing was "
            "looked at, so nothing can be said about what is designated"
            % (CELLS_TOOL, w, h, x, z, CELL_CAP))
    out = []
    for c in grid:
        names = sorted(_desig_names(c))
        if names:
            out.append((c.get("x"), c.get("z"), names))
    return sorted(out)


def pending_in(x, z, w=1, h=1, grid=None):
    """[(x, z, label, "blueprint"|"frame")] for the unfinished builds in a rect.

    `Designator_Cancel.CanDesignateThing` ends with
    `t.Faction == Faction.OfPlayer && (t is Frame || t is Blueprint)`, and
    `DesignateThing` calls `t.Destroy(DestroyMode.Cancel)` on them -- so a
    Cancel drag DELETES blueprints and frames as well as designations. Counting
    only designations is what let `act.py undesignate` print "nothing
    designated in this rectangle" over a live blueprint (turns 23 and 31): the
    sentence was true and the rectangle was not empty.
    """
    if grid is None:
        grid = cells(x, z, w, h, fields="designations,things")
    if grid is None:
        raise rim.BridgeError(
            "%s did not read %sx%s at %s,%s (cap %d cells) -- nothing was "
            "looked at, so nothing can be said about what is in it"
            % (CELLS_TOOL, w, h, x, z, CELL_CAP))
    out = []
    for c in grid:
        for t in c.get("things") or []:
            kind = ("blueprint" if t.get("isBlueprint")
                    else "frame" if t.get("isFrame") else None)
            if kind:
                out.append((c.get("x"), c.get("z"),
                            t.get("label") or t.get("defName") or kind, kind))
    return sorted(out)


def undesignate(x, z, w=1, h=1, do=False):
    """Cancel the designations AND unfinished builds in a cell or rectangle.

    Dry by default. Everything in `removed` / `left` / `leftPending` is read
    off the map afterwards, never inferred from the designator's own
    accepted-cell count.
    """
    grid = cells(x, z, w, h, fields="designations,things")
    found = designations_in(x, z, w, h, grid=grid)
    pending = pending_in(x, z, w, h, grid=grid)
    report = {"dryRun": not do, "found": found, "pending": pending,
              "removed": [], "removedPending": [], "left": [],
              "leftPending": [], "result": None}
    if not do or not (found or pending):
        report["left"] = list(found)
        report["leftPending"] = list(pending)
        return report
    pick.clear_designator()
    report["result"] = apply(UNDESIGNATE_DESIGNATOR, x, z, w, h, dry=False)
    after = cells(x, z, w, h, fields="designations,things")
    left = designations_in(x, z, w, h, grid=after)
    left_pending = pending_in(x, z, w, h, grid=after)
    still = {(cx, cz): names for cx, cz, names in left}
    for cx, cz, names in found:
        gone = [n for n in names if n not in still.get((cx, cz), [])]
        if gone:
            report["removed"].append((cx, cz, gone))
    survivors = list(left_pending)
    for row in pending:
        if row in survivors:
            survivors.remove(row)
        else:
            report["removedPending"].append(row)
    report["left"] = left
    report["leftPending"] = left_pending
    return report


def _print_undesignate(report):
    found, left = report["found"], report["left"]
    pending, left_pending = report["pending"], report["leftPending"]
    n_found = sum(len(names) for _, _, names in found)
    if not found and not pending:
        print("nothing to cancel in this rectangle: no designations, no "
              "blueprints, no frames. (Cancel removes all three; if something "
              "here still needs removing it is not one of them -- a built "
              "thing wants Deconstruct, and Tame/Hunt sit on the ANIMAL.)")
        return 0
    if report["dryRun"]:
        print("DRY RUN - would cancel %d designation(s) in %d cell(s) and %d "
              "blueprint(s)/frame(s) (nothing applied; add --do)"
              % (n_found, len(found), len(pending)))
        for x, z, names in found:
            print("  %s,%s -- %s" % (x, z, ", ".join(names)))
        for x, z, label, kind in pending:
            print("  %s,%s -- %s %s  << DESTROYED by Cancel, not just "
                  "un-designated" % (x, z, kind.upper(), label))
        return 0
    n_gone = sum(len(names) for _, _, names in report["removed"])
    print("CANCELLED %d of %d designation(s) in %d cell(s), and %d of %d "
          "blueprint(s)/frame(s)"
          % (n_gone, n_found, len(found),
             len(report["removedPending"]), len(pending)))
    for x, z, names in report["removed"]:
        print("  %s,%s -- removed %s" % (x, z, ", ".join(names)))
    for x, z, label, kind in report["removedPending"]:
        print("  %s,%s -- destroyed %s %s" % (x, z, kind, label))
    for x, z, names in left:
        thingy = [n for n in names if n.replace(" ", "") in THING_DESIGNATIONS]
        print("  !! still designated at %s,%s -- %s%s"
              % (x, z, ", ".join(names),
                 "   (that designation sits on the ANIMAL, not the cell; the "
                 "Cancel designator cannot reach it -- the bridge has no "
                 "remove_designation op, see BUGS.md)" if thingy else ""))
    for x, z, label, kind in left_pending:
        print("  !! %s still standing at %s,%s -- %s" % (kind, x, z, label))
    return 0 if not (left or left_pending) else 1


def _counts(result):
    """(accepted, rejected) from whichever of the two shapes the reply used.

    The COUNT fields have been absent from a reply that carried the cell lists,
    and `result.get("acceptedCellCount", 0)` then printed a real 12-cell apply
    as zero. Fall back to the lists rather than to nothing.
    """
    took = result.get("acceptedCells") or []
    lost = result.get("rejectedCells") or []
    a = result.get("acceptedCellCount")
    r = result.get("rejectedCellCount")
    return (len(took) if a is None else a, len(lost) if r is None else r)


def _print_result(result, as_json=False, dry=None, notes=None):
    """The one line that says whether anything happened. `dry` is the CALLER's
    intent and outranks the reply, because the reply has come back without a
    `dryRun` field and a missing one used to read as "applied" (turn 5/16).

    `notes` is `_rejected_notes()`: per-cell verdicts read off the map, so a
    cell the game refused BECAUSE IT WAS ALREADY DESIGNATED says so on its own
    line instead of wearing the same "the designator rejected this cell" a
    solid floor gets.
    """
    if as_json:
        print(json.dumps(result, indent=1))
        return 1 if result.get("success") is False else 0
    if result.get("alreadyDesignated"):
        print("NO-OP: %s" % result.get("message"))
        return 0
    said = result.get("dryRun")
    if dry is None:
        dry = bool(said)
    notes = notes or {}
    accepted, rejected = _counts(result)
    noop = sum(1 for kind, _ in notes.values() if kind == "noop")
    tail = ("%d rejected" % rejected
            + (" (%d of them already designated -- no-ops, not failures)" % noop
               if noop else ""))
    if said is not None and bool(said) != bool(dry):
        print("!! the bridge reply says dryRun=%s but this was called as a %s -- "
              "believing the call, not the reply"
              % (said, "dry run" if dry else "real apply"))
    if accepted > 0 or result.get("success"):
        if dry:
            # Never the applied wording. `act.py apply "Mine"` with no --do
            # printed "N cell(s) newly designated" and the designation layer
            # read 0 (turns 5 and 16).
            print("DRY RUN - would designate %d cell(s) (nothing applied; add "
                  "--do); %s" % (accepted, tail))
        else:
            print("APPLIED %d cell(s); %s" % (accepted, tail))
    else:
        print("REFUSED: %s" % (result.get("message") or
                               "no requested cell was accepted"))
    for cell in result.get("rejectedCells") or []:
        key = (cell.get("x"), cell.get("z"))
        kind, text = notes.get(key) or (None, None)
        if kind == "noop":
            print("  %s,%s -- NO-OP %s" % (key[0], key[1], text))
        elif kind:
            print("  %s,%s -- REFUSED: %s" % (key[0], key[1], text))
        else:
            print("  %s,%s -- REFUSED: %s"
                  % (key[0], key[1],
                     cell.get("reason") or "the game gave no reason (an empty "
                     "AcceptanceReport). Nothing on the cell explains it -- "
                     "`map.py <x> <z> --layers desig` is the read"))
    return 1 if result.get("success") is False and accepted == 0 else 0


def _print_allow(report):
    found, done = report["found"], report["unforbidden"]
    if not found:
        print("nothing forbidden in this rectangle")
        return 0
    if report["dryRun"]:
        print("WOULD UNFORBID %d cell(s):" % len(found))
        for x, z, names in found:
            print("  %s,%s -- %s" % (x, z, ", ".join(str(n) for n in names)))
        print("-- dry run; pass --do to allow them")
        return 0
    print("UNFORBID: %d of %d cell(s) allowed" % (len(done), len(found)))
    for x, z, names in done:
        print("  %s,%s -- %s" % (x, z, ", ".join(str(n) for n in names)))
    for x, z, names in report["stillForbidden"]:
        print("  !! still forbidden at %s,%s -- %s"
              % (x, z, ", ".join(str(n) for n in names)))
    for x, z, why in report["failed"]:
        print("  !! %s,%s -- %s" % (x, z, why))
    return 0 if done and not report["failed"] else 1


# -------------------------------------------------------------------- CLI ----
# Sizes are a WIDTH and a HEIGHT however they are spelled: positional,
# --width/--height, or --size W H. All three are accepted; only one at a time.


def _add_size(parser):
    parser.add_argument("width", type=int, nargs="?",
                        help="rectangle WIDTH in cells (not a second corner)")
    parser.add_argument("height", type=int, nargs="?", help="rectangle HEIGHT")
    parser.add_argument("--width", type=int, dest="width_opt",
                        metavar="W", help="same as the positional width")
    parser.add_argument("--height", type=int, dest="height_opt",
                        metavar="H", help="same as the positional height")
    parser.add_argument("--size", type=int, nargs=2, metavar=("W", "H"),
                        help="width and height together")


def _rect(ns, parser):
    """(width, height) from positionals, --width/--height, or --size W H."""
    given = [name for name, used in (
        ("positional", ns.width is not None or ns.height is not None),
        ("--width/--height", ns.width_opt is not None or ns.height_opt is not None),
        ("--size", ns.size is not None)) if used]
    if len(given) > 1:
        parser.error("size given two ways (%s) -- pass one" % ", ".join(given))
    if ns.size is not None:
        return ns.size[0], ns.size[1]
    if ns.width_opt is not None or ns.height_opt is not None:
        return (1 if ns.width_opt is None else ns.width_opt,
                1 if ns.height_opt is None else ns.height_opt)
    return (1 if ns.width is None else ns.width,
            1 if ns.height is None else ns.height)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    ap = sub.add_parser("apply", help="apply or dry-run an architect designator")
    ap.add_argument("label")
    ap.add_argument("x", type=int)
    ap.add_argument("z", type=int)
    _add_size(ap)
    ap.add_argument("--category", help="category for an ambiguous label")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--do", action="store_true", help="apply; default is a dry run")
    mode.add_argument("--dry", action="store_true", help="validate without applying (the default)")
    ap.add_argument("--keep", action="store_true", help="leave the designator selected")
    ap.add_argument("--no-preview", action="store_true", dest="no_preview",
                    help="skip the which-cells / touches-built-structure report on an area")
    ap.add_argument("--json", action="store_true")
    sub.add_parser("clear", help="drop the currently armed designator")
    for verb, what in (("hunt", "Hunt"), ("tame", "Tame")):
        hp = sub.add_parser(verb, help="designate %s on an animal's CURRENT cell" % what)
        hp.add_argument("animal", help="name, defName, kindDef or thingId")
        hp.add_argument("--do", action="store_true", help="designate; default is a dry run")
        hp.add_argument("--all", action="store_true", dest="every",
                        help="every animal the name matches, not one")
    for verb in ("allow", "unforbid"):
        up = sub.add_parser(verb, help="unforbid loose items in a cell or rectangle")
        up.add_argument("x", type=int)
        up.add_argument("z", type=int)
        _add_size(up)
        up.add_argument("--do", action="store_true",
                        help="unforbid; default is a dry run")
        up.add_argument("--json", action="store_true")
    dp = sub.add_parser("undesignate",
                        help="remove the designations in a cell or rectangle")
    dp.add_argument("x", type=int)
    dp.add_argument("z", type=int)
    _add_size(dp)
    dp.add_argument("--do", action="store_true",
                    help="remove them; default is a dry run")
    dp.add_argument("--json", action="store_true")
    lp = sub.add_parser("labels", help="the whole designator menu")
    lp.add_argument("word", nargs="?", help="substring filter")
    ns = parser.parse_args(argv)

    rim.init()
    if ns.command == "labels":
        word = (ns.word or "").lower()
        for label, note in labels():
            if word in label.lower():
                print("  %s%s" % (label, note))
        return 0
    if ns.command in ("hunt", "tame"):
        try:
            results = designate_animal(ns.command.capitalize(), ns.animal,
                                       do=ns.do, every=ns.every)
        except KeyError as ex:
            parser.error(str(ex.args[0] if ex.args else ex))
        rc = 0
        for p, result, others in results:
            pos = p.get("position") or {}
            print("%s %s (%s) at %s,%s"
                  % (ns.command.upper() if ns.do else "would " + ns.command,
                     p.get("name") or p.get("defName"), _animal_id(p),
                     pos.get("x"), pos.get("z")))
            if others:
                print("  also on %s,%s: %s -- a refusal from this cell can name "
                      "any of them, not the one asked for"
                      % (pos.get("x"), pos.get("z"),
                         ", ".join("%s %s" % (o.get("name") or o.get("defName"),
                                              _animal_id(o))
                                   for o in others[:SUGGEST])))
            rc |= _print_result(result, dry=not ns.do)
        return rc
    if ns.command == "clear":
        selected = clear()
        print("CLEARED" if selected is False else
              "REFUSED: the designator still appears selected")
        return 0 if selected is False else 1
    if ns.command in ("allow", "unforbid"):
        w, h = _rect(ns, parser)
        report = allow(ns.x, ns.z, w, h, do=ns.do)
        if ns.json:
            print(json.dumps(report, indent=1))
            return 0
        return _print_allow(report)
    if ns.command == "undesignate":
        w, h = _rect(ns, parser)
        report = undesignate(ns.x, ns.z, w, h, do=ns.do)
        if ns.json:
            print(json.dumps(report, indent=1))
            return 0
        return _print_undesignate(report)
    label = (ns.category, ns.label) if ns.category else ns.label
    w, h = _rect(ns, parser)
    if ns.do:
        # `build.py` leaves a placement designator armed, and an armed
        # designator swallows the click the next gizmo call needs (turn 10).
        # apply_architect_designator arms its own, so drop the stale one here
        # and say so rather than leaving it for the next tool to trip on.
        pick.clear_designator()
    try:
        result = apply(label, ns.x, ns.z, w, h, dry=not ns.do, keep=ns.keep)
    except KeyError as ex:
        parser.error(str(ex.args[0] if ex.args else ex))
    # Read the designation layer back BEFORE printing anything, so every
    # rejected cell is classified -- accepted-cell count or not. The 09-06 fix
    # only looked when the designator took nothing, which is why a Mine sweep
    # that took some cells and refused nineteen already-designated ones printed
    # nineteen "the designator rejected this cell" lines (turns 22/24/36).
    notes = {} if ns.json else _rejected_notes(label, result, ns.x, ns.z, w, h)
    rc = _print_result(result, ns.json, dry=not ns.do, notes=notes)
    if ns.json:
        return rc
    accepted, _ = _counts(result)
    if accepted == 0:
        note = _noop_reason(label, result, ns.x, ns.z, w, h, notes=notes)
        if note:
            print("NO-OP: %s" % note)
            return 0
        return rc
    if not ns.no_preview and w * h > 1:
        _preview(result, ns.x, ns.z, w, h, dry=not ns.do)
    where = paints(label)
    if where:
        print("  -- %s" % where % (ns.x, ns.z, w, h))
    return rc


if __name__ == "__main__":
    sys.exit(main())
