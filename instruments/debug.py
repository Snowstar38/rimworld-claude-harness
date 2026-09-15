r"""RimWorld's own debug actions, for RAISING the situations a bug needs.

**Workshop only.** Every verb here fires a dev-mode debug action into a live
colony: raids, manhunters, wounds, deaths, quests. It exists so the items under
`BUGS.md` "Patched offline, needs its live trigger" can be checked against a
real game instead of waiting for the storyteller. Never in a **Live** stream
session -- `--do` refuses when `stream.py mode` says live -- and never on a save
that is then written: a colony this has touched is a test fixture, so load a
scratch save, or let the session end without `rimworld/save_game`.

  python debug.py find <words>          # search the tree; path, kind, supported
  python debug.py show <path>           # one node and its children
  python debug.py run "<path>" [--pawn <name|id>] [--cell x z] [--do]
  python debug.py devmode               # is RimWorld's dev mode on, and how to turn it on

  python debug.py raid [--points N] --do      # an enemy raid, small by default
  python debug.py manhunter --do              # a manhunter pack
  python debug.py trader [--orbital] --do     # trade caravan / orbital trader
  python debug.py flare [--hours N] --do      # solar flare, 1 game hour by default
  python debug.py letter --do                 # a quest, which arrives as a CHOICE letter
  python debug.py fight <pawnA> <pawnB> --do  # a social fight (two stages, see below)
  python debug.py wound <pawn> [--severity scratch|serious] --do
  python debug.py down <pawn> --do            # damage until downed
  python debug.py kill <pawn> --do
  python debug.py heal <pawn> [--times N] --do
  python debug.py alert [--pawn <name>] --do  # a Critical alert, if one is raisable
  python debug.py spawn <def> <x> <z> --do    # a thing, or a pawn kind
  python debug.py rename <pawn>               # REFUSES: there is no such debug action

Every write is a DRY RUN until `--do`: without it the resolved path and the
target are printed and nothing fires.

## `[NO]` -- the failure that prints FIRED

A debug node's LABEL is the only place the game says whether the thing can
actually happen. `DebugActionsIncidents` appends ` [NO]` from a labelGetter
running `TargetAllowed` / `CanFireNow`; `DebugActionsQuests` appends
` [not now]`. The BRIDGE cannot see either -- `execution.supported` is about
whether the node has a delegate, not about whether the game will act on it --
so executing a `[NO]` node returns `success: true`, does nothing, and leaves
one `Log.Warning` in `effects.logs`. That is exactly what `flare --do` did on
2026-09-12: `FIRED Actions\Do incident\SolarFlare`, and no flare.

So **every verb refuses on a `[NO]` label before it fires**, in the dry run
too, and prints the label, the reason and a `find` line for another route.
`--anyway` is the override, for a label you believe is stale.

## Incidents take the CURRENT MAP, and only that

`DebugActionsIncidents.GetTarget()` returns the selected world object when the
WORLD view is up and `Find.CurrentMap` otherwise. The bridge sets no context
of its own -- `ExecuteDebugActionResponse` pushes a virtual mouse pointer, and
only for a ToolMap node -- and `rimworld/open_main_tab` null-refs on the World
toggle, so through the bridge an incident's target is ALWAYS the current map.
An IncidentDef tagged `<targetTags><li>World</li></targetTags>` therefore
always reads `[NO]`:

  * `SolarFlare`, `Eclipse`, `Aurora` -- and every def under `GiveQuestBase`,
    which is why `Do incident\GiveQuest_Random` is dead too;
  * `Map_PlayerHome` defs are fine: `RaidEnemy`, `ManhunterPack`,
    `TraderCaravanArrival`, `OrbitalTraderArrival` all read clean live.

The two World-tagged ones this file needs have their own routes, and neither
is an incident:

  * `flare` uses `Actions\Add Game Condition...\Solar flare\<n hours>` --
    `DebugActionsMapManagement.AddGameCondition` registers the same
    `GameConditionDef` straight onto `Find.CurrentMap.GameConditionManager`.
    Children run 1 hour to 1 day in one-hour steps (plus `Permanent`, which
    this never picks); `--hours N` takes the nearest.
  * `letter` uses `Actions\Generate quest...\*Natural random` -- the one Direct
    leaf in that menu, which picks the storyteller's own quest and sends the
    `NewQuestLetter` that `letters.py decide` answers.

## The path grammar

A debug action's path is its node labels joined with a **backslash**, starting
at the tab root -- `Actions\Do incident\ManhunterPack`. The category in
`[DebugAction("Incidents", "Do trade caravan arrival...")]` is a display
grouping and is **not** in the path (`DebugTabMenu_Actions.GenerateCacheForMethod`
adds every action straight to the `Actions` root). Three label rules follow from
the same method and change what you have to type:

  * a ToolMap / ToolMapForPawns / ToolWorld action is prefixed `T: `
    -- `Actions\T: Damage Until Down`;
  * a method returning `List<DebugActionNode>` gets `...` appended
    -- `Actions\Spawn thing...\Silver`;
  * a method with no `name=` is named by `GenText.SplitCamelCase`
    -- `DamageUntilDown` -> `Damage Until Down`;
  * `hideInSubMenu = true` puts the action under `Actions\Show more actions\`.

`find` reads the live tree, so none of this has to be remembered. Nothing here
hard-codes a path it has not asked the bridge for first: every shortcut resolves
through `rimworld/get_debug_action`, falls back to `rimworld/search_debug_actions`,
and REFUSES with the candidates listed rather than firing a guess.

## What the bridge can and cannot execute

`DebugActionExecutionPolicy` sorts every node into one kind:

  * **Direct** -- `action`, no target. Fired as-is.
  * **PawnTarget** -- `ToolMapForPawns`. Needs `--pawn`.
  * **MapTarget** -- `ToolMap`. Needs `--cell x z`; the bridge pushes a virtual
    pointer at that cell so the action's `UI.MouseCell()` reads it.
  * **BrowseOnly** -- a submenu, or a node with no delegate. Not executable;
    list its children instead.
  * **WorldTarget** -- `ToolWorld`. Not implemented upstream. Unreachable.

Two further things are out of reach and are reported, not faked:

  * an action that opens a `Dialog_DebugOptionListLister` for a second choice
    (`Apply damage...` wants a body part) leaves a window standing --
    `ui.py click "<option>"` answers it;
  * an action that arms a second map stage (`Mental state...\SocialFighting`
    asks "...with" and waits for a click) comes back with
    `followUp.tool = rimworld/click_cell`. `fight` does that second click for
    you at pawn B's cell; whether the injected click reaches the debug tool is
    the one thing in here that has not been proven live.

## Triggers that are NOT debug actions

Four of the situations the bug list wants have no debug action at all in 1.6,
and are reached the ordinary way instead:

  * **a forbidden item** -- spawn one, then `python act.py forbid <x> <z>`
    (`allow` is its inverse); forbidding is the `CompForbiddable` gizmo, not a
    designator;
  * **a locked door** -- build a door and use its own Hold-open / lock gizmo,
    `python buildings.py gizmos <x> <z>`;
  * **skeletons for a butcher bill** -- a corpse cannot be spawned
    (`DebugThingPlaceHelper.IsDebugSpawnable` excludes every `Corpse`
    thingClass), so `debug.py spawn <PawnKind> x z --do`, `debug.py kill`, and
    run time until it rots;
  * **a rename dialog** -- see `debug.py rename`, which prints the route.

## Dev mode

`rimworld/execute_debug_action` does **not** check `Prefs.DevMode` -- the node
graph is built by reflection and the bridge invokes the delegate directly, so
these run with dev mode off. Every reply still reports `devModeEnabled`;
`debug.py devmode` prints it. No bridge tool sets it (`rimworld/set_god_mode`
sets `DebugSettings.godMode`, a different flag), so if you want RimWorld's own
debug window too, turn it on by hand: Esc -> Options -> Development mode.
"""
import re
import sys

import rim

ROOT = "Actions"
SEP = "\\"

SEARCH = "rimworld/search_debug_actions"
GET = "rimworld/get_debug_action"
CHILDREN = "rimworld/list_debug_action_children"
EXECUTE = "rimworld/execute_debug_action"
ROOTS = "rimworld/list_debug_action_roots"
PAWNS = "home/list_pawns"
ALERTS = "rimworld/list_alerts"
CLICK = "rimworld/click_cell"

INCIDENTS = ROOT + SEP + "Do incident"
RAID_POINTS = ROOT + SEP + "Execute raid with points..."
MENTAL = ROOT + SEP + "Mental state..."
MORE = ROOT + SEP + "Show more actions"
CONDITIONS = ROOT + SEP + "Add Game Condition..."
QUESTS = ROOT + SEP + "Generate quest..."

# What a node's LABEL says when the game itself has decided the thing cannot
# happen right now. `DebugActionsIncidents` appends ` [NO]` from a labelGetter
# that runs `TargetAllowed` and `CanFireNow`; `DebugActionsQuests` appends
# ` [not now]` from `CanRun`. Either one means executing it is a no-op with a
# warning in the log, which is exactly how 2026-09-12's `flare --do` "worked".
NO_MARKERS = ("[NO]", "[not now]")

# How many candidates a refusal lists. A refusal that prints 400 spawnable
# ThingDefs is a refusal nobody reads.
CANDIDATES = 12

HEAL_MAX = 20


class Refused(Exception):
    """A refusal with the next command already in it. Never a traceback."""


# --- the four discovery calls ------------------------------------------------
# All four are strict=False: a miss is an answer here (that is what `find` is
# for), and rim.game()'s strict raise would turn "no such path" into a stack
# trace above the line that says which path.


def get(path):
    """One node, or None. `includeChildren` off -- `show` asks for them."""
    r = rim.game(GET, {"path": path, "includeChildren": False}, strict=False)
    return r.get("node") if isinstance(r, dict) and r.get("success") else None


def children(path, include_hidden=True):
    r = rim.game(CHILDREN, {"path": path, "includeHidden": include_hidden},
                 strict=False)
    if not isinstance(r, dict) or not r.get("success"):
        return []
    return r.get("children") or []


def search(query, limit=40, include_hidden=True, supported_only=False):
    r = rim.game(SEARCH, {"query": query, "limit": limit,
                          "includeHidden": include_hidden,
                          "supportedOnly": supported_only}, strict=False)
    if not isinstance(r, dict) or not r.get("success"):
        return [], r
    return [m.get("node") or {} for m in (r.get("matches") or [])], r


def execute(path, pawn=None, cell=None):
    args = {"path": path}
    if pawn is not None:
        args[pawn_key(pawn)] = str(pawn)
    if cell is not None:
        args["x"], args["z"] = int(cell[0]), int(cell[1])
    return rim.game(EXECUTE, args, strict=False)


def pawn_key(pawn):
    """`pawnId` for a stable id, `pawnName` for a name. Same test `rim.py` uses
    for `set_draft`: a bare number or a `Pawn_`/`Thing_`/`Human` id is an id."""
    s = str(pawn)
    if s.isdigit() or re.match(r"^(?:Pawn_|Thing_|Human)\S*\d+$", s):
        return "pawnId"
    return "pawnName"


# --- reading a node ----------------------------------------------------------


def kind(node):
    return ((node or {}).get("execution") or {}).get("kind") or "?"


def supported(node):
    return bool(((node or {}).get("execution") or {}).get("supported"))


def target_kind(node):
    return ((node or {}).get("execution") or {}).get("requiredTargetKind") or ""


def no_marker(node):
    """The game's own "cannot fire now" marker in a node's label, or None."""
    label = ((node or {}).get("label") or "").strip()
    for m in NO_MARKERS:
        if label.endswith(m):
            return m
    return None


def node_line(node):
    """One printed row. `[NO]` in a label is the GAME's own "cannot fire now"."""
    marks = []
    if not node.get("visible"):
        marks.append("not available in this game state")
    label = (node.get("label") or "").strip()
    if label and label != (node.get("path") or "").rsplit(SEP, 1)[-1]:
        marks.append("label: " + label)
    return "  %-56s %-11s %-3s %s" % (
        (node.get("path") or "?")[:56], kind(node),
        "yes" if supported(node) else "no", "; ".join(marks))


def refuse_with(head, candidates, next_cmd):
    lines = [head]
    for n in candidates[:CANDIDATES]:
        lines.append(node_line(n))
    if len(candidates) > CANDIDATES:
        lines.append("  ... and %d more" % (len(candidates) - CANDIDATES))
    lines.append("-- " + next_cmd)
    raise Refused("\n".join(lines))


def resolve(paths, query, match, what):
    """The one resolver. Exact paths first, then the live search.

    `paths` are the paths this build of RimWorld is expected to spell it; each
    is CHECKED against the bridge before it is used. When none of them exist --
    a version renamed a label, a mod moved it -- `query` goes to
    `search_debug_actions` and `match` picks from what comes back. One hit is
    used (and said so); none or several REFUSE with the candidates listed.
    """
    for p in paths:
        node = get(p)
        if node is not None:
            return node
    hits, reply = search(query)
    if isinstance(reply, dict) and reply.get("success") is False:
        raise Refused("the bridge refused %s: %s\n-- is the game running? "
                      "`python rim.py call rimworld/get_game_info '{}'`"
                      % (SEARCH, reply.get("message")))
    close = [n for n in hits if match(n)]
    if len(close) == 1:
        print("   ~~ %s was not at %r; resolved by search to %r"
              % (what, paths[0], close[0].get("path")))
        return close[0]
    if not close:
        refuse_with("REFUSED, nothing fired: no debug action for %s. None of "
                    "these paths exist and the search for %r matched nothing "
                    "usable.\n  tried: %s" % (what, query, ", ".join(repr(p) for p in paths)),
                    hits, "widen it: python debug.py find %s" % query)
    refuse_with("REFUSED, nothing fired: %s is ambiguous -- %d nodes match."
                % (what, len(close)), close,
                "pick one: python debug.py run \"<path>\" ... --do")


def child_of(parent, want, what):
    """One named child of a submenu, or a refusal listing the near misses."""
    kids = children(parent)
    if not kids:
        raise Refused("REFUSED, nothing fired: %r has no children (or does not "
                      "exist), so %s cannot be resolved.\n-- python debug.py find %s"
                      % (parent, what, want))
    low = want.lower()
    exact = [k for k in kids if (k.get("path") or "").rsplit(SEP, 1)[-1].lower() == low]
    if len(exact) == 1:
        return exact[0]
    near = [k for k in kids if low in (k.get("path") or "").lower()]
    if len(near) == 1:
        return near[0]
    if not near:
        refuse_with("REFUSED, nothing fired: %r has no child matching %r."
                    % (parent, want), kids,
                    "python debug.py show \"%s\"" % parent)
    refuse_with("REFUSED, nothing fired: %r matches %d children of %r."
                % (want, len(near), parent), near,
                "pick one: python debug.py run \"<path>\" --do")


# --- the map, for cell targets -----------------------------------------------


def pawn_rows():
    r = rim.game(PAWNS, {}, strict=False)
    if not isinstance(r, dict):
        return []
    return r.get("pawns") or []


def resolve_pawn(pawn):
    """The form `execute_debug_action` can find: an id becomes the pawn's name.

    Found live 2026-09-12 (BUGS.md): `--pawn Ibex45901` was refused with
    "Could not find current-map pawn" while `--pawn "Ibex doe"` fired, though
    the help promises `<name|id>`. Two defects met there: an animal ThingID
    (defName+number, no `Pawn_`/`Thing_` prefix) fails `pawn_key`'s id test,
    so it went over as a NAME nobody bears -- and the bridge's own pawnId
    lookup has never been seen to fire. So the id is honoured HERE: one
    `home/list_pawns` read, the row found by thingId, and the name -- the
    form proven to fire -- is what goes to the bridge. An exact on-map name
    passes through untouched, as does anything when the map cannot be read
    (offline dry runs stay cheap refusal-free).
    """
    s = str(pawn)
    rows = pawn_rows()
    if not rows:
        return pawn
    low = s.lower()
    if any(str(r.get("name") or "").lower() == low for r in rows):
        return pawn

    def tid(row):
        # thingId is top-level since the 2026-09-11 companion; older builds
        # kept it inside the animals{} / settings{} blocks.
        return str(row.get("thingId")
                   or (row.get("animals") or {}).get("thingId")
                   or (row.get("settings") or {}).get("thingId") or "")

    hits = [r for r in rows if tid(r).lower() == low]
    if not hits and s.isdigit():
        # A bare number is the numeric tail of a ThingID (`Ibex45901` -> 45901).
        hits = [r for r in rows if re.search(r"[A-Za-z_]%s$" % re.escape(s), tid(r))]
    if not hits:
        return pawn          # not an id this map knows; the bridge gets it as given
    if len(hits) > 1:
        raise Refused("REFUSED, nothing fired: %r matches %d ThingIDs on the map "
                      "(%s).\n-- python pawns.py --all --json" %
                      (s, len(hits), ", ".join(sorted(tid(r) for r in hits)[:8])))
    name = str(hits[0].get("name") or "")
    if not name:
        raise Refused("REFUSED, nothing fired: %s has no name in the "
                      "`home/list_pawns` reply, and the bridge only finds "
                      "debug-tool targets by name.\n-- python pawns.py --all --json" % s)
    same = [r for r in rows if str(r.get("name") or "") == name]
    if len(same) > 1:
        raise Refused("REFUSED, nothing fired: %s is named %r and %d pawns on "
                      "the map share that name. The bridge finds debug-tool "
                      "targets by name only, so the wrong one could be hit.\n"
                      "-- python pawns.py --all --json" % (s, name, len(same)))
    return name


def pawn_cell(name):
    """(x, z) of a pawn by name, or a refusal naming who IS on the map."""
    rows = pawn_rows()
    low = str(name).lower()
    hits = [r for r in rows if str(r.get("name") or "").lower() == low]
    if not hits:
        hits = [r for r in rows if low in str(r.get("name") or "").lower()]
    if len(hits) != 1:
        names = ", ".join(sorted(str(r.get("name")) for r in rows if r.get("name"))[:20])
        raise Refused("REFUSED, nothing fired: %r matches %d pawns on the map.\n"
                      "  on the map: %s\n-- python pawns.py"
                      % (name, len(hits), names or "(none reported)"))
    pos = hits[0].get("position") or {}
    if pos.get("x") is None or pos.get("z") is None:
        raise Refused("REFUSED, nothing fired: %s has no position in the "
                      "`home/list_pawns` reply, so there is no cell to target.\n"
                      "-- python pawns.py" % name)
    return int(pos["x"]), int(pos["z"])


# --- the plan ----------------------------------------------------------------


class Plan(object):
    """What would be fired: one resolved node, a target, and a note."""

    def __init__(self, node, pawn=None, cell=None, note=None, after=None):
        self.node = node
        self.path = node.get("path")
        self.pawn = pawn
        self.cell = cell
        self.note = note
        self.after = after          # a callable run after a successful --do
        self.repeat = 1             # `heal --times N` is the only user of this
        self.anyway = False         # --anyway, the one override of the [NO] guard

    def target(self):
        if self.pawn is not None:
            return "pawn %s (%s)" % (self.pawn, pawn_key(self.pawn))
        if self.cell is not None:
            return "cell %d,%d" % (self.cell[0], self.cell[1])
        return "none (a Direct action)"

    def check(self):
        """The node's own execution metadata against the target given.

        The `[NO]` guard comes FIRST because it is the failure that looks like
        a success: on 2026-09-12 `flare --do` printed `FIRED` and the only
        sign anything was wrong was a `Log.Warning` buried in `effects.logs`.
        The bridge cannot see it -- `execution.supported` is about the node's
        delegate, not about whether the game will act on it -- so the label is
        the only evidence there is, and it is checked before the dry run too,
        so the refusal arrives before `--do` is typed.
        """
        marker = no_marker(self.node)
        if marker and not self.anyway:
            raise Refused(
                "REFUSED, nothing fired: the game labels this node %r. %s means "
                "it cannot fire against the current target right now -- executing "
                "it anyway succeeds, changes nothing, and leaves one warning in "
                "the log.\n  path: %s\n"
                "-- look for another route: python debug.py find %s\n"
                "-- or, if the label is stale, repeat with --anyway"
                % ((self.node.get("label") or "").strip(), marker, self.path,
                   (self.path or "").rsplit(SEP, 1)[-1]))
        if not supported(self.node):
            reason = (self.node.get("execution") or {}).get("reason") or ""
            nxt = ("python debug.py show \"%s\"" % self.path
                   if self.node.get("hasChildren")
                   else "python debug.py find %s" % (self.path or "").rsplit(SEP, 1)[-1])
            raise Refused("REFUSED, nothing fired: %r is %s -- %s\n-- %s"
                          % (self.path, kind(self.node), reason, nxt))
        want = target_kind(self.node)
        if want == "pawn" and self.pawn is None:
            raise Refused("REFUSED, nothing fired: %r needs a pawn target.\n"
                          "-- python debug.py run \"%s\" --pawn <name> --do"
                          % (self.path, self.path))
        if want == "map" and self.cell is None:
            raise Refused("REFUSED, nothing fired: %r needs a map cell.\n"
                          "-- python debug.py run \"%s\" --cell <x> <z> --do"
                          % (self.path, self.path))


# --- the shortcuts -----------------------------------------------------------
# Each returns a Plan. Every path below was read out of the 1.6 source
# (`Verse.DebugActionsIncidents`, `Verse.DebugToolsPawns`, `Verse.DebugTools_Health`,
# `Verse.DebugToolsGeneral`, `Verse.DebugToolsSpawning`) and is still CHECKED
# against the live tree by resolve() before anything fires.


def incident(defname, what):
    """`Actions\\Do incident\\<IncidentDef>` -- the 1.6 incident menu.

    `DebugActionsIncidents.IncidentsYielder` builds one child per IncidentDef,
    each a Direct action whose delegate takes its target from `GetTarget()`:
    the selected world object if the WORLD view is up, otherwise
    `Find.CurrentMap`. The bridge sets no context of its own
    (`ExecuteDebugActionResponse` only pushes a virtual pointer, and only for
    a ToolMap node), and `rimworld/open_main_tab` null-refs on the World toggle
    (BUGS, upstream), so through the bridge the target is ALWAYS the current
    map. An IncidentDef tagged `<targetTags><li>World</li></targetTags>` --
    SolarFlare, Eclipse, Aurora, everything under `GiveQuestBase` -- therefore
    fails `TargetAllowed`, is labelled ` [NO]`, and logs "Incident target is
    null or not allowed" while reporting success. Those have their own routes
    below; `Map_PlayerHome` defs (RaidEnemy, ManhunterPack, TraderCaravanArrival,
    OrbitalTraderArrival) are fine here, and were clean live on 2026-09-12.
    """
    return Plan(resolve([INCIDENTS + SEP + defname],
                        defname,
                        lambda n: (n.get("path") or "").endswith(SEP + defname),
                        what))


def duration_child(parent_path, hours, what):
    """The `<n> hours` child of an Add Game Condition node nearest to `hours`.

    `DebugActionsMapManagement.AddGameCondition` makes one child per 2500-tick
    step from 2500 to 60000 -- an hour to a day -- labelled by
    `ToStringTicksToPeriod`, plus a `Permanent` one that is deliberately never
    picked here: a permanent condition outlives the test.
    """
    kids = children(parent_path)
    scored = []
    for k in kids:
        leaf = (k.get("path") or "").rsplit(SEP, 1)[-1]
        m = re.match(r"\s*([\d.]+)\s*(hour|day)", leaf, re.I)
        if not m:
            continue
        got = float(m.group(1)) * (24 if m.group(2).lower() == "day" else 1)
        scored.append((abs(got - hours), got, k))
    if not scored:
        refuse_with("REFUSED, nothing fired: no `<n> hours` child under %r, so "
                    "%s has no duration to fire." % (parent_path, what), kids,
                    "python debug.py show \"%s\"" % parent_path)
    scored.sort(key=lambda t: (t[0], t[1]))
    _, got, node = scored[0]
    return node, got


def sc_raid(opt):
    """A small enemy raid. `Execute raid with points...` has one child per
    entry in `DebugActionsUtility.PointsOptions(extended: true)` labelled
    `"<n> points"`; the nearest to `--points` is taken."""
    want = float(opt.get("points") or 200)
    kids = children(RAID_POINTS)
    if not kids:
        node = resolve([RAID_POINTS], "Execute raid with points",
                       lambda n: "raid with points" in (n.get("path") or "").lower(),
                       "the raid menu")
        kids = children(node.get("path"))
    scored = []
    for k in kids:
        m = re.match(r"\s*([\d.]+)", (k.get("path") or "").rsplit(SEP, 1)[-1])
        if m:
            scored.append((abs(float(m.group(1)) - want), float(m.group(1)), k))
    if not scored:
        refuse_with("REFUSED, nothing fired: no `<n> points` child under %r."
                    % RAID_POINTS, kids, "python debug.py find raid")
    scored.sort(key=lambda t: (t[0], t[1]))
    _, got, node = scored[0]
    note = None
    if abs(got - want) > 0.5:
        note = "asked for %g points; nearest offered is %g." % (want, got)
    return Plan(node, note=note)


def sc_manhunter(opt):
    return incident("ManhunterPack", "a manhunter pack")


def sc_trader(opt):
    if opt.get("orbital"):
        return incident("OrbitalTraderArrival", "an orbital trader")
    return incident("TraderCaravanArrival", "a trade caravan")


def sc_flare(opt):
    """A solar flare, through `Add Game Condition...`, NOT through an incident.

    `Do incident\\SolarFlare` is the obvious route and it is a trap: the
    IncidentDef is `<targetTags><li>World</li></targetTags>`, the bridge can
    only ever hand it the current map, so it is labelled ` [NO]` and executing
    it reports success while doing nothing (live, 2026-09-12). The condition
    menu registers the same `GameConditionDef` straight onto
    `Find.CurrentMap.GameConditionManager`, which is what a real flare is and
    what `power.py`'s `SolarFlare` defName match reads.
    """
    hours = float(opt.get("hours") or 1)
    if hours <= 0:
        raise Refused("REFUSED, nothing fired: --hours must be above zero.")
    parent = resolve([CONDITIONS + SEP + "Solar flare"], "Solar flare",
                     lambda n: (n.get("path") or "").lower()
                               .endswith(SEP + "solar flare"),
                     "the solar flare condition")
    node, got = duration_child(parent.get("path"), hours, "a solar flare")
    note = "a map GameCondition lasting %g game hour(s)." % got
    if abs(got - hours) > 0.01:
        note += " Asked for %g; nearest offered is %g." % (hours, got)
    return Plan(node, note=note)


def sc_letter(opt):
    """A CHOICE letter with something to answer.

    `Do incident\\GiveQuest_Random` is dead through the bridge for the same
    reason the flare is: every def under `GiveQuestBase` inherits
    `targetTags = [World]`, and it read ` [NO]` live on 2026-09-12.
    `Generate quest...\\*Natural random` is the one Direct leaf in that menu --
    `NaturalRandomQuestChooser.ChooseNaturalRandomQuest` against
    `Find.CurrentMap`, then `QuestUtility.SendLetterQuestAvailable`, which is
    the `NewQuestLetter` with accept / postpone / reject. Every other child is
    a submenu wanting points or a population first.
    """
    return Plan(resolve([QUESTS + SEP + "*Natural random"], "Natural random",
                        lambda n: (n.get("path") or "")
                                  .endswith(SEP + "*Natural random"),
                        "a quest letter"),
                note="sends the quest-available letter `letters.py decide` "
                     "answers; which quest is the storyteller's own pick.")


def _pawn_action(opt, paths, query, match, what, arg):
    if not arg:
        raise Refused("REFUSED, nothing fired: %s needs a pawn.\n"
                      "-- python debug.py %s <pawn> --do" % (what, opt["cmd"]))
    node = resolve(paths, query, match, what)
    return Plan(node, pawn=arg)


def sc_down(opt):
    return _pawn_action(opt, [ROOT + SEP + "T: Damage Until Down"],
                        "Damage Until Down",
                        lambda n: "damage until down" in (n.get("path") or "").lower(),
                        "damage until downed", opt.get("arg1"))


def sc_kill(opt):
    return _pawn_action(opt, [ROOT + SEP + "T: Damage To Death"],
                        "Damage To Death",
                        lambda n: "damage to death" in (n.get("path") or "").lower(),
                        "damage to death", opt.get("arg1"))


def sc_heal(opt):
    """`Heal random injury (10)` takes 10 points off ONE injury, so `--times`
    repeats it. It is the only single-shot heal in the vanilla tree."""
    plan = _pawn_action(opt, [ROOT + SEP + "T: Heal random injury (10)"],
                        "Heal random injury",
                        lambda n: "heal random injury" in (n.get("path") or "").lower(),
                        "heal one injury", opt.get("arg1"))
    times = int(opt.get("times") or 1)
    if times < 1 or times > HEAL_MAX:
        raise Refused("REFUSED, nothing fired: --times %d is outside 1..%d."
                      % (times, HEAL_MAX))
    plan.note = ("10 damage off one random injury per run; --times %d will run "
                 "it %d times." % (times, times)) if times > 1 else None
    plan.repeat = times
    return plan


def sc_wound(opt):
    """A wound that is not a death.

    `scratch` (the default) is `T: 10 damage` -- 10 Crush at the pawn's CELL,
    the smallest damage the vanilla tree has (its only other amounts are 300
    and 5000). It is NOT under the injury guard's floor: measured live on
    2026-09-12 it lands one injury of exactly 10.00 hit points and the guard's
    serious test is `>= 10`, so a bare `scratch` DOES stop the clock with
    `colonist_injury ... worth 10.00 hit points`. There is no debug action for
    a real 2-6 point nip, so the guard's no-stop path cannot be raised from
    here at all -- a restart inside the 180 s cooldown, or
    `play.py start --allow-injured <pawnId>`, is what proves it. `serious` is
    `T: Damage Until Incapable Of Manipulation`, a real wound short of death
    (it lives under `Show more actions` because it is `hideInSubMenu`).
    `Apply damage...` is deliberately NOT used: it opens a body-part dialog
    that execute cannot answer.
    """
    who = opt.get("arg1")
    if not who:
        raise Refused("REFUSED, nothing fired: wound needs a pawn.\n"
                      "-- python debug.py wound <pawn> --do")
    sev = (opt.get("severity") or "scratch").lower()
    if sev not in ("scratch", "serious"):
        raise Refused("REFUSED, nothing fired: --severity takes scratch or "
                      "serious, not %r." % sev)
    if sev == "serious":
        node = resolve([MORE + SEP + "T: Damage Until Incapable Of Manipulation"],
                       "Damage Until Incapable",
                       lambda n: "incapable of manipulation" in (n.get("path") or "").lower(),
                       "a serious wound")
        return Plan(node, pawn=who,
                    note="a wound short of death; it may still down them.")
    node = resolve([ROOT + SEP + "T: 10 damage"], "10 damage",
                   lambda n: (n.get("path") or "").lower().endswith("t: 10 damage"),
                   "a scratch")
    cell = pawn_cell(who)
    return Plan(node, cell=cell,
                note="10 Crush at %s's cell -- it hits EVERYTHING standing "
                     "there, not just them. NOTE: 10 hit points is exactly the "
                     "injury guard's serious threshold, so this STOPS "
                     "supervised play; vanilla has no smaller damage action." % who)


def sc_fight(opt):
    """A social fight between two pawns.

    `Mental state...\\SocialFighting` is a ToolMapForPawns node whose pawnAction
    picks pawn A and then arms a SECOND debug tool ("...with") that waits for a
    click on pawn B. The bridge reports that second stage as
    `followUp.tool = rimworld/click_cell`, and this fires that click at B's
    cell. That second hop is the one unproven step in this file.
    """
    a, b = opt.get("arg1"), opt.get("arg2")
    if not a or not b:
        raise Refused("REFUSED, nothing fired: fight needs two pawns.\n"
                      "-- python debug.py fight <pawnA> <pawnB> --do")
    node = resolve([MENTAL + SEP + "SocialFighting"], "SocialFighting",
                   lambda n: (n.get("path") or "").endswith(SEP + "SocialFighting"),
                   "a social fight")
    cell = pawn_cell(b)

    def second_stage(reply):
        fu = (reply or {}).get("followUp") or {}
        eff = (reply or {}).get("effects") or {}
        if not fu and not eff.get("debugToolActiveAfter"):
            print("   ~~ no second stage was armed; %s may already be in a "
                  "mental state. Nothing was clicked." % a)
            return
        print("   stage 2: clicking %s at %d,%d (%s)"
              % (b, cell[0], cell[1], fu.get("tool") or CLICK))
        got = rim.game(CLICK, {"x": cell[0], "z": cell[1], "button": "left"},
                       strict=False)
        ok = isinstance(got, dict) and got.get("success") is not False
        print("   stage 2: %s" % ("clicked" if ok else
                                  "REFUSED -- %s" % (got or {}).get("message")))
        print("   -- confirm with: python pawns.py %s" % a)

    return Plan(node, pawn=a, after=second_stage,
                note="two stages: %s is put into SocialFighting, then %s's cell "
                     "at %d,%d is clicked." % (a, b, cell[0], cell[1]))


def sc_spawn(opt):
    """A thing (`Spawn thing...`) or, failing that, a pawn kind (`Spawn Pawn...`).

    Both are ToolMap children keyed by defName. A CORPSE cannot be spawned this
    way -- `DebugThingPlaceHelper.IsDebugSpawnable` excludes anything whose
    thingClass is a `Corpse` -- so a skeleton for a butcher bill has to be made:
    spawn the pawn kind, `debug.py kill` it, and run time until it rots.
    """
    defname, x, z = opt.get("arg1"), opt.get("arg2"), opt.get("arg3")
    if not defname or x is None or z is None:
        raise Refused("REFUSED, nothing fired: spawn needs a def and a cell.\n"
                      "-- python debug.py spawn <def> <x> <z> --do")
    try:
        cell = (int(x), int(z))
    except (TypeError, ValueError):
        raise Refused("REFUSED, nothing fired: <x> <z> must be whole numbers, "
                      "got %r %r." % (x, z))
    for parent, what in ((ROOT + SEP + "Spawn thing...", "a thing"),
                         (ROOT + SEP + "Spawn Pawn...", "a pawn kind")):
        kids = children(parent)
        if not kids:
            continue
        hit = [k for k in kids
               if (k.get("path") or "").rsplit(SEP, 1)[-1].lower() == defname.lower()]
        if len(hit) == 1:
            return Plan(hit[0], cell=cell, note="spawning %s" % what)
    node = resolve([ROOT + SEP + "Spawn thing..." + SEP + defname,
                    ROOT + SEP + "Spawn Pawn..." + SEP + defname],
                   defname,
                   lambda n: (n.get("path") or "").rsplit(SEP, 1)[-1].lower()
                             == defname.lower(),
                   "spawning %r" % defname)
    return Plan(node, cell=cell)


def sc_alert(opt):
    """A Critical alert.

    No debug action raises an alert -- alerts are read off the colony's state
    every tick. The one that is cheap to cause on purpose is
    `Alert_ColonistNeedsRescuing` (an `Alert_Critical`): down a colonist where
    nobody has rescued them yet. So this reads `rimworld/list_alerts` first and
    only offers the route when nothing Critical is already up.
    """
    r = rim.game(ALERTS, {}, strict=False)
    rows = (r or {}).get("alerts") if isinstance(r, dict) else None
    crit = [a for a in (rows or [])
            if str(a.get("priority") or "").lower() == "critical"]
    if crit:
        for a in crit:
            print("   Critical already up: %s" % (a.get("label") or a))
        raise Refused("nothing to raise: %d Critical alert(s) are already "
                      "live.\n-- python alerts.py" % len(crit))
    who = opt.get("pawn") or opt.get("arg1")
    if not who:
        raise Refused("REFUSED, nothing fired: no Critical alert is live and "
                      "none can be raised without a victim. Downing a colonist "
                      "raises `Alert_ColonistNeedsRescuing`, which is Critical.\n"
                      "-- python debug.py alert --pawn <colonist> --do")
    plan = sc_down({"cmd": "down", "arg1": who})
    plan.note = ("downing %s raises the Critical `Alert_ColonistNeedsRescuing`; "
                 "read it back with `python alerts.py`." % who)
    return plan


def sc_rename(opt):
    """There is no rename debug action. Say so, and name the real route."""
    who = opt.get("arg1") or "<pawn>"
    raise Refused(
        "REFUSED, nothing fired: RimWorld 1.6 has no debug action that opens "
        "`Dialog_NamePawn`. The only route is the rename PENCIL -- "
        "`RenameUIUtility.DrawRenameButton` for an animal or mech in the "
        "inspect pane, `CharacterCardUtility` for a colonist's Bio tab -- and "
        "both call `pawn.NamePawnDialog()`. It is an image button with no text.\n"
        "-- select and open the card: python pawns.py %s\n"
        "-- find the pencil: python ui.py --probe rename   (then "
        "`python ui.py click --rect x,y,w,h`)\n"
        "-- type into it: python dialog.py \"<name>\" --do" % who)


SHORTCUTS = {
    "raid": sc_raid, "manhunter": sc_manhunter, "trader": sc_trader,
    "flare": sc_flare, "letter": sc_letter, "fight": sc_fight,
    "wound": sc_wound, "down": sc_down, "kill": sc_kill, "heal": sc_heal,
    "alert": sc_alert, "spawn": sc_spawn, "rename": sc_rename,
}


# --- the live-session guard --------------------------------------------------


def mode_guard(do):
    """Workshop only. A LIVE session refuses; an unset mode warns and runs.

    `stream.current_mode()` returns None when nobody said and when the answer
    is more than twelve hours old. Refusing on None would make this unusable in
    an ordinary session, so None warns -- but "live" is a hard no, because
    `modes\\live.md` exists to forbid exactly this.
    """
    if not do:
        return
    try:
        import stream
        mode = stream.current_mode()
    except Exception:
        mode = None
    if mode == "live":
        raise Refused("REFUSED, nothing fired: this session's mode is LIVE and "
                      "debug.py is workshop-only -- it fires raids and wounds "
                      "into the colony people are watching.\n"
                      "-- if that is wrong: python stream.py mode workshop")
    if mode is None:
        print("   ~~ no session mode is set, so this cannot check it is not a "
              "stream. debug.py is workshop-only.")


def dirty_note():
    print("-- this colony is now a test fixture. Do NOT save over a real save "
          "(`rimworld/save_game`); load a scratch save or end without saving.")


# --- firing ------------------------------------------------------------------


def show_reply(reply):
    if not isinstance(reply, dict):
        print("the bridge answered a %s, not a payload: %.200r"
              % (type(reply).__name__, reply))
        return False
    ok = reply.get("success") is not False
    print("%s  %s" % ("FIRED" if ok else "REFUSED by the bridge",
                      reply.get("path") or ""))
    if reply.get("message"):
        print("   " + str(reply["message"]))
    eff = reply.get("effects") or {}
    for key, word in (("openedWindowTypes", "opened"),
                      ("closedWindowTypes", "closed")):
        got = eff.get(key) or []
        if got:
            print("   %s: %s" % (word, ", ".join(got)))
    if eff.get("openedWindowTypes"):
        print("   -- a window is standing: python ui.py")
    fu = reply.get("followUp") or {}
    if fu:
        print("   follow-up: %s -- %s" % (fu.get("tool"), fu.get("message")))
    for entry in (eff.get("logs") or [])[:3]:
        print("   log %s: %s" % (entry.get("level"),
                                 str(entry.get("message") or "")[:140]))
    return ok


def fire(plan, opt):
    do = opt.get("do")
    plan.anyway = bool(opt.get("anyway"))
    if plan.anyway and no_marker(plan.node):
        print("   ~~ --anyway: the game labels this %s. Firing it anyway is "
              "very likely a no-op." % no_marker(plan.node))
    plan.check()
    mode_guard(do)
    if plan.pawn is not None:
        resolved = resolve_pawn(plan.pawn)
        if str(resolved) != str(plan.pawn):
            print("   ~~ --pawn %s resolved to %r -- the bridge finds "
                  "debug-tool targets by name" % (plan.pawn, resolved))
        plan.pawn = resolved
    print("path   : %s" % plan.path)
    print("target : %s" % plan.target())
    print("kind   : %s" % kind(plan.node))
    if plan.note:
        print("note   : %s" % plan.note)
    if not do:
        print("-- DRY RUN, nothing fired. Add --do to fire it.")
        return 0
    ok = True
    reply = None
    for _ in range(plan.repeat):
        reply = execute(plan.path, pawn=plan.pawn, cell=plan.cell)
        ok = show_reply(reply) and ok
    if ok and plan.after is not None:
        plan.after(reply)
    dirty_note()
    return 0 if ok else 1


# --- the CLI -----------------------------------------------------------------


def parse(argv):
    """Flags out, positionals in order. `--cell x z` takes two."""
    opt = {"args": []}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--do":
            opt["do"] = True
        elif a == "--orbital":
            opt["orbital"] = True
        elif a == "--anyway":
            opt["anyway"] = True
        elif a in ("--points", "--severity", "--pawn", "--times", "--limit",
                   "--hours"):
            if i + 1 >= len(argv):
                raise Refused("REFUSED: %s needs a value." % a)
            opt[a[2:]] = argv[i + 1]
            i += 1
        elif a == "--cell":
            if i + 2 >= len(argv):
                raise Refused("REFUSED: --cell needs <x> <z>.")
            opt["cell"] = (argv[i + 1], argv[i + 2])
            i += 2
        elif a.startswith("--"):
            raise Refused("REFUSED: unknown flag %r.\n-- python debug.py --help" % a)
        else:
            opt["args"].append(a)
        i += 1
    for n, v in enumerate(opt["args"][:4], start=1):
        opt["arg%d" % n] = v
    return opt


def normalise(path):
    """The path as typed, or with `/` read as `\\` when that is what resolves.

    A label may legitimately contain `/` (`GenerateCacheForMethod` rewrites a
    backslash in a name to one), so a blind swap would break those; this only
    swaps when the literal path is not a node and the swapped one is.
    """
    if get(path) is not None:
        return path
    if "/" in path:
        alt = path.replace("/", SEP)
        if get(alt) is not None:
            print("   ~~ read %r as %r (paths are backslash-separated)."
                  % (path, alt))
            return alt
    return path


def cmd_find(opt):
    words = " ".join(opt["args"])
    if not words:
        raise Refused("REFUSED: find needs something to search for.\n"
                      "-- python debug.py find raid")
    hits, reply = search(words, limit=int(opt.get("limit") or 40))
    if not hits:
        msg = reply.get("message") if isinstance(reply, dict) else None
        print("no debug action matches %r%s" % (words, ": " + msg if msg else ""))
        return 1
    for n in hits:
        print(node_line(n))
    total = reply.get("totalMatchCount") if isinstance(reply, dict) else len(hits)
    print("-- %d shown of %d. `supported` no means a submenu or an unexecutable "
          "node; show its children with `python debug.py show \"<path>\"`."
          % (len(hits), total))
    return 0


def cmd_show(opt):
    if not opt["args"]:
        raise Refused("REFUSED: show needs a path.\n"
                      "-- python debug.py show \"Actions\\Do incident\"")
    path = normalise(" ".join(opt["args"]))
    r = rim.game(GET, {"path": path, "includeChildren": True,
                       "includeHiddenChildren": True}, strict=False)
    if not isinstance(r, dict) or not r.get("success"):
        raise Refused("REFUSED: %s\n-- python debug.py find %s"
                      % ((r or {}).get("message"), path.rsplit(SEP, 1)[-1]))
    print(node_line(r.get("node") or {}))
    for c in (r.get("children") or []):
        print(node_line(c))
    print("-- %d children. devMode: %s"
          % (r.get("childCount") or 0, r.get("devModeEnabled")))
    return 0


def cmd_run(opt):
    if not opt["args"]:
        raise Refused("REFUSED: run needs a path.\n"
                      "-- python debug.py find <words>")
    path = normalise(" ".join(opt["args"]))
    node = get(path)
    if node is None:
        raise Refused("REFUSED, nothing fired: no debug action at %r.\n"
                      "-- python debug.py find %s" % (path, path.rsplit(SEP, 1)[-1]))
    cell = None
    if opt.get("cell"):
        try:
            cell = (int(opt["cell"][0]), int(opt["cell"][1]))
        except ValueError:
            raise Refused("REFUSED: --cell takes two whole numbers.")
    return fire(Plan(node, pawn=opt.get("pawn"), cell=cell), opt)


def cmd_devmode(opt):
    r = rim.game(ROOTS, {}, strict=False)
    on = r.get("devModeEnabled") if isinstance(r, dict) else None
    print("Prefs.DevMode: %s" % on)
    print("-- the bridge does not gate execute_debug_action on it, and no tool "
          "sets it (`rimworld/set_god_mode` sets DebugSettings.godMode, which "
          "is a different flag). Turn RimWorld's own debug window on by hand: "
          "Esc -> Options -> Development mode.")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("--help", "-h", "help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    try:
        opt = parse(rest)
        opt["cmd"] = cmd
        if cmd == "find":
            return cmd_find(_inited(opt))
        if cmd == "show":
            return cmd_show(_inited(opt))
        if cmd == "run":
            return cmd_run(_inited(opt))
        if cmd == "devmode":
            return cmd_devmode(_inited(opt))
        if cmd in SHORTCUTS:
            return fire(SHORTCUTS[cmd](_inited(opt)), opt)
        print("no such verb %r" % cmd)
        print(__doc__)
        return 2
    except Refused as why:
        print(str(why))
        return 2


def _inited(opt):
    rim.init()
    return opt


if __name__ == "__main__":
    sys.exit(main())
