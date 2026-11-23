"""Alerts with the CULPRIT attached -- who, not just what.

  python alerts.py            # every live alert, resolved, one block each
  python alerts.py --brief    # the Scout-brief lines
  python alerts.py --status   # the compact watch/status block
  python alerts.py --json     # the resolved list, unformatted
  python alerts.py selftest   # the four real payloads + edge cases, NO game

## Names come from `targets[]`, NEVER from the prose

Every culprit here comes from `targets[]`, the structured list the companion
returns beside the prose -- `{defName, id, kind, label, mapId, mapIndex,
position, thingType}`, one entry per thing the game itself blames. When
`targets` is empty, `culprits` is `[]`, `mapWide` is True, and the bulleted
names in `explanation` are LEFT WHERE THEY ARE: a name lifted out of that
paragraph is a guess wearing a colonist's clothes. There is no fallback path
here on purpose.

`culpritCount` is the game's own `targetCount`, not `len(culprits)`, so a capped
target list can never read as a complete one: two names and a count of nine say
nine.

## Priority is ORDERED, and is never compared with `==`

`RimWorld.AlertPriority` is an ordered enum -- Medium < High < Critical -- and
the companion compares it as one (`priority < min`). Loudness here is a RANK
comparison against the lowest name in `LOUD` (matching `watch.py`'s
`LOUD_ALERTS`), so a priority above the set is loud too, and an UNRECOGNISED
priority is treated as loud: the one thing worse than an unfamiliar alert is a
silent one.

## `detail` is the sentence, not the essay

`explanation` is prose with `<color=...>` markup, CRLF, the bulleted culprits
and a stock advice tail. The detail kept here is the first paragraph with the
bullet lines removed (they are in `culprits`) and the tail dropped.

A row that will not read still appears, label intact, with a detail saying it
could not be read -- dropping it would make an unreadable alert
indistinguishable from an absent one.

## `Unhappy nudity` says WHICH part is bare

`Unhappy nudity (Finn: legs bare)`. The alert names the pawn and stops there, so
when -- and only when -- it is live, one `home/list_pawns {equipment: true}` call
reads each culprit's worn apparel and the body groups it declares. Torso and
Legs are the two RimWorld's nudity thought keys off; coverage is read from each
garment's own `bodyPartGroups`, never guessed from its name, and a garment that
does not carry the field makes the answer unreadable rather than bare.
"""
import json
import re
import sys

# Ordered low to high, as `RimWorld.AlertPriority` is. Index, never identity.
PRIORITY_ORDER = ("Low", "Medium", "High", "Critical")
# The priorities that get a line of their own. Same meaning as watch.py's
# LOUD_ALERTS; kept as a set of NAMES, used as a rank floor.
LOUD = ("High", "Critical")

TOOL = "rimworld/list_alerts"

# Unity/RimWorld rich-text tags. Listed rather than `<[^>]*>` so a stray "<"
# in a colonist's nickname survives instead of eating the rest of the line.
_TAG = re.compile(
    r"</?(?:color|b|i|u|s|size|material|quad|sup|sub|align|alpha|cspace|font|"
    r"gradient|indent|line-height|link|lowercase|uppercase|smallcaps|mark|"
    r"mspace|nobr|noparse|pos|rotate|space|sprite|style|voffset|width)"
    r"(?:=[^<>]*)?>", re.I)
_BULLET = re.compile(r"^\s*(?:[-*•‣●]|\d+[.)])\s+")

DETAIL_MAX = 220        # a detail is a line in a brief, not a paragraph
NAME_CAP = 3            # names shown inline before "+N more" takes over


def plain(s):
    """Markup out, CRLF out, whitespace collapsed. `None` -> ""."""
    if s is None:
        return ""
    return " ".join(_TAG.sub("", str(s)).replace("\r\n", "\n").split())


def priority_rank(priority):
    """Position in PRIORITY_ORDER, or -1 for a priority this file has never
    heard of. Never an equality test against one name -- that is the bug fixed
    in watch.py and setup.py on 2026-09-02."""
    try:
        return PRIORITY_ORDER.index(priority)
    except ValueError:
        return -1


def is_loud(alert, loud=LOUD):
    """Rank comparison against the lowest name in `loud`, so anything ABOVE the
    set is loud too. An unrecognised priority counts as loud: a new priority the
    game grows, or a row whose priority would not read, is not something to fold
    into a one-line footnote."""
    ranks = [priority_rank(p) for p in loud]
    ranks = [r for r in ranks if r >= 0]
    floor = min(ranks) if ranks else 0
    r = priority_rank((alert or {}).get("priority"))
    return r < 0 or r >= floor


# ------------------------------------------------------------------ detail ---

def _clip(text, limit=DETAIL_MAX):
    """Trim at a sentence end inside the limit; otherwise cut and say so."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    stop = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if stop > limit // 2:
        return cut[:stop + 1]
    return cut.rstrip() + "..."


def detail_of(explanation):
    """The one useful sentence out of an alert's `explanation`.

    Three things go: the bulleted culprit lines (their names are in `culprits`,
    structured, and repeating them here would invite reading names off prose
    again), the "(Click to ...)" UI hint, and everything after the first
    paragraph -- which on the real payloads is the stock "You can tailor apparel
    at a crafting spot or tailoring table" advice. The trailing colon goes with
    the list it introduced."""
    text = str(explanation or "").replace("\r\n", "\n").replace("\r", "\n")
    para = []
    for line in text.split("\n"):
        if _BULLET.match(line):
            continue                                    # a culprit, not a sentence
        if plain(line).lower().startswith("(click"):
            continue                                    # UI hint, not a finding
        if line.strip():
            para.append(line)
        elif para:
            break                                       # end of the first paragraph
    out = plain(" ".join(para)).rstrip(":").strip()
    return _clip(out)


# ----------------------------------------------------------------- resolve ---

def _culprits(targets):
    """`targets[]` -> culprit dicts. The ONLY source of names in this module.

    A target that is not an object is skipped rather than guessed at; the row's
    `culpritCount` still carries the real total, so a skip cannot read as an
    absence."""
    out = []
    for t in targets or []:
        if not isinstance(t, dict):
            continue
        pos = t.get("position") or {}
        out.append({"name": t.get("label") or t.get("defName") or t.get("id") or "?",
                    "id": t.get("id"),
                    "kind": t.get("kind"),
                    "x": pos.get("x"),
                    "z": pos.get("z")})
    return out


def _unreadable(row, why):
    """A row that would not read, kept in the list. Never dropped: an absent
    alert and an unreadable one look identical to a clean-context Scout, and
    only one of them is safe."""
    row = row if isinstance(row, dict) else {}
    return {"label": str(row.get("label") or "(unlabelled alert)"),
            "priority": row.get("priority"),
            "type": row.get("type"),
            "ordinal": row.get("ordinal"),
            "culprits": [],
            "culpritsTruncated": False,
            "culpritCount": 0,
            "detail": "COULD NOT BE READ -- " + why,
            "mapWide": False}      # unknown, not map-wide: it names nobody YET


def resolve(rows=None, limit=40):
    """Alert rows -> resolved dicts, one per alert, culprits attached.

    `rows=None` makes the bridge call itself. `rows=<the alerts list>` (or the
    whole reply dict) is PURE -- no bridge, no clock -- and is the path the
    selftest exercises.

    Each dict has exactly: label, priority, type, ordinal, culprits
    [{name, id, kind, x, z}], culpritsTruncated, culpritCount, detail, mapWide.

    `mapWide` is True only when the alert names nobody at all: no structured
    culprits AND a `targetCount` of zero. A row with a count but no listed
    targets is NOT map-wide -- it is a truncated culprit list, and saying
    "map-wide" there would hide somebody."""
    if rows is None:
        import rim                                  # only the bridge path imports it
        reply = rim.game(TOOL, {"limit": limit})
        rows = (reply or {}).get("alerts") or []
    elif isinstance(rows, dict):
        rows = rows.get("alerts") or []

    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            out.append(_unreadable({"label": str(row)[:60]},
                                   "the alert row was a %s, not an object"
                                   % type(row).__name__))
            continue
        if row.get("error"):
            out.append(_unreadable(row, "%s%s" % (
                plain(row.get("error")),
                " (%s)" % row["exceptionType"] if row.get("exceptionType") else "")))
            continue
        try:
            culprits = _culprits(row.get("targets"))
            count = row.get("targetCount")
            if not isinstance(count, int) or count < len(culprits):
                # targetCount is the real total and outranks the list -- but a
                # missing or nonsense one must not UNDERstate what was listed.
                count = len(culprits)
            out.append({
                "label": str(row.get("label") or "(unlabelled alert)"),
                "priority": row.get("priority"),
                "type": row.get("type"),
                "ordinal": row.get("ordinal"),
                "culprits": culprits,
                "culpritsTruncated": bool(row.get("targetsTruncated"))
                                     or count > len(culprits),
                "culpritCount": count,
                "detail": detail_of(row.get("explanation")),
                "mapWide": not culprits and count == 0,
            })
        except Exception as e:                      # a shape nobody predicted
            out.append(_unreadable(row, "%s: %s" % (type(e).__name__, e)))
    return out


# ------------------------------------------------------------------- nudity ---
#
# The one alert whose culprit list is not the whole answer: it says who is
# unhappily nude and never says what is uncovered. Torso and Legs are the only
# two groups that matter -- RimWorld's nudity thought keys off those and nothing
# in the game cares about a bare neck or bare arms -- so the note stays one
# clause. The extra read happens ONLY when the alert is live; an ordinary sweep
# is still one call.
NUDITY_TYPE = "Alert_UnhappyNudity"
NUDITY_LABEL = "unhappy nudity"
NUDITY_GROUPS = (("Torso", "torso"), ("Legs", "legs"))
PAWNS_TOOL = "home/list_pawns"
BARE_UNREAD = "apparel NOT READ"


def is_nudity(a):
    """The nudity alert, by `type` first and label second."""
    return (NUDITY_TYPE in str((a or {}).get("type") or "")
            or str((a or {}).get("label") or "").strip().lower() == NUDITY_LABEL)


def bare_groups(row):
    """(bare group words, was every worn garment readable) for one pawn row.

    Coverage comes from each garment's own `bodyPartGroups`; a row without that
    list makes the answer UNREADABLE, not uncovered, because a build that never
    said is not a build that said "bare". Same for a missing `equipment` block or
    a missing `apparel[]` -- both are contracted to be there, and an absent one
    is not a naked pawn. A pawn wearing nothing needs no field: an EMPTY apparel
    list is itself the answer, and `hasApparelTracker: false` is too."""
    every = [word for _, word in NUDITY_GROUPS]
    eq = row.get("equipment") if isinstance(row, dict) else None
    if not isinstance(eq, dict):
        return every, False
    worn = eq.get("apparel")
    if not isinstance(worn, list):
        return every, eq.get("hasApparelTracker") is False
    covered, readable = set(), True
    for a in worn:
        groups = a.get("bodyPartGroups") if isinstance(a, dict) else None
        if not isinstance(groups, list):
            readable = False
            continue
        covered |= {str(g.get("defName")) for g in groups
                    if isinstance(g, dict) and g.get("defName")}
    return [word for defname, word in NUDITY_GROUPS if defname not in covered], readable


def bare_phrase(words, readable):
    """`legs bare` / `torso and legs bare`, or None when nothing is bare."""
    if not readable:
        return "apparel coverage NOT REPORTED by this build"
    if not words:
        return None
    return "%s bare" % " and ".join(words)


def add_nudity_detail(resolved, rows=None):
    """Attach `bare` to every `Unhappy nudity` culprit. Returns `resolved`.

    `rows=None` makes the one extra bridge call, narrowed by `nameFilter` when a
    single pawn is blamed. `rows=<pawn list>` is PURE and is the selftest path.
    Culprits still come from `targets[]`; this only matches a name the game
    already gave against the pawn list."""
    hits = [a for a in resolved if is_nudity(a)]
    named = [c for a in hits for c in (a.get("culprits") or [])]
    if not named:
        return resolved
    if rows is None:
        import rim                                  # only the bridge path imports it
        args = {"equipment": True, "humanlikeOnly": True}
        who = {str(c.get("name") or "") for c in named}
        if len(who) == 1:
            args["nameFilter"] = who.pop()          # one culprit, one small reply
        try:
            rows = (rim.game(PAWNS_TOOL, args, strict=False) or {}).get("pawns")
        except Exception:
            rows = None
    if not isinstance(rows, list):
        for c in named:
            c["bare"] = BARE_UNREAD
        return resolved
    by_name = {str(r.get("name") or "").lower(): r for r in rows if isinstance(r, dict)}
    for c in named:
        row = by_name.get(str(c.get("name") or "").lower())
        if row is None:
            # The game blames a pawn the pawn list did not return. Saying so
            # beats an alert line that quietly reads as fully dressed.
            c["bare"] = BARE_UNREAD
            continue
        phrase = bare_phrase(*bare_groups(row))
        if phrase:
            c["bare"] = phrase
    return resolved


# ----------------------------------------------------------------- phrasing ---

def one_line(a):
    """One resolved alert -> "Tattered apparel (Octave)".

    Map-wide alerts get the label alone -- no parenthesis, because an empty one
    reads as a name that failed to print. When the list is capped or the count
    outruns it, the count wins: "(Octave +3 more)". A culprit carrying a `bare`
    note from `add_nudity_detail` prints it: "(Finn: legs bare)"."""
    label = a.get("label") or "(unlabelled alert)"
    culprits = a.get("culprits") or []
    count = a.get("culpritCount") or 0
    if not culprits:
        if count:
            # Somebody is named by the game and was not listed to us. Say the
            # number; do not go looking for the name in the prose.
            return "%s (%d culprit(s), none listed)" % (label, count)
        return label
    names = ["%s: %s" % (c.get("name") or "?", c["bare"]) if c.get("bare")
             else str(c.get("name") or "?") for c in culprits[:NAME_CAP]]
    more = max(count, len(culprits)) - len(names)
    return "%s (%s%s)" % (label, ", ".join(names),
                          " +%d more" % more if more > 0 else "")


def status_lines(resolved, loud=LOUD, cap=8):
    """-> (loud_lines, minor_line_or_None) for a compact status block.

    Loud alerts get a line each; everything else folds into one line, which is
    the half `watch.py` was missing entirely until 2026-09-02. Both halves are
    capped, and both caps state themselves in the text they truncated."""
    hot = [a for a in resolved if is_loud(a, loud)]
    minor = [a for a in resolved if not is_loud(a, loud)]
    hot.sort(key=lambda a: (-priority_rank(a.get("priority")), a.get("ordinal") or 0))

    lines = [one_line(a) for a in hot[:cap]]
    if len(hot) > cap:
        lines.append("... +%d more loud alert(s) not listed (`python alerts.py`)"
                     % (len(hot) - cap))
    if not minor:
        return lines, None
    prios = "/".join(sorted({(a.get("priority") or "?") for a in minor},
                            key=priority_rank))
    body = "; ".join(one_line(a) for a in minor[:cap])
    if len(minor) > cap:
        body += " (+%d more)" % (len(minor) - cap)
    return lines, "alerts (%s): %s" % (prios, body)


def brief_lines(resolved, cap=6):
    """-> lines for a Scout brief: loud first, then minor, each via one_line().

    The cap states itself in the text it truncated, and the count in that line
    is over the alerts DROPPED, so a brief can never read as the whole list."""
    hot = [a for a in resolved if is_loud(a)]
    minor = [a for a in resolved if not is_loud(a)]
    hot.sort(key=lambda a: (-priority_rank(a.get("priority")), a.get("ordinal") or 0))
    minor.sort(key=lambda a: (-priority_rank(a.get("priority")), a.get("ordinal") or 0))

    if not hot and not minor:
        return ["ALERTS: none live. (checked -- read from %s, not assumed.)" % TOOL]
    lines, shown = [], 0
    for a in hot + minor:
        if shown >= cap:
            break
        mark = "!! ALERT" if is_loud(a) else "   alert"
        lines.append("%s %-8s %s" % (mark, a.get("priority") or "?", one_line(a)))
        shown += 1
    left = len(hot) + len(minor) - shown
    if left:
        lines.append("   ... +%d more alert(s) not listed (`python alerts.py`)" % left)
    return lines


# ------------------------------------------------------------------- output ---

def show(resolved):
    if not resolved:
        print("ALERTS: none live. (checked -- read from %s, not assumed.)" % TOOL)
        return
    hot, minor = [a for a in resolved if is_loud(a)], [a for a in resolved if not is_loud(a)]
    print("ALERTS %d -- %d loud (%s or above), %d minor"
          % (len(resolved), len(hot), "/".join(LOUD), len(minor)))
    for a in resolved:
        print("%s %-8s %s" % ("!!" if is_loud(a) else "  ",
                              a.get("priority") or "?", one_line(a)))
        if a.get("detail"):
            print("      %s" % a["detail"])
        for c in a.get("culprits") or []:
            print("      -> %-20s %-6s %s,%s   %s%s"
                  % (c.get("name"), c.get("kind"), c.get("x"), c.get("z"), c.get("id"),
                     "   %s" % c["bare"] if c.get("bare") else ""))
        if a.get("culpritsTruncated"):
            print("      !! CULPRITS TRUNCATED -- %d listed of %d the game blames."
                  % (len(a.get("culprits") or []), a.get("culpritCount") or 0))
        if a.get("mapWide"):
            # Said out loud so an empty culprit list is never mistaken for a
            # lookup that failed. This alert names nobody because there is
            # nobody to name -- and the bulleted names in some explanations are
            # not a substitute (rota.py SCOUT_B, 2026-09-02).
            print("      (map-wide: the game blames no specific thing here.)")
        print("      %s | ordinal %s" % (a.get("type") or "?", a.get("ordinal")))


# ----------------------------------------------------------------- selftest ---

# The four real rows captured from `rimworld/list_alerts` on Lampblack day 39
# (2026-09-02 20:40), verbatim -- colour markup, CRLF, advice tails and all.
# They are the fixtures because a hand-written explanation would agree with
# whatever this file's trimming happens to do.
LIVE_FIXTURE = [
    {"active": True, "anyCulpritValid": True, "error": None, "exceptionType": None,
     "explanation": "These colonists are wearing tattered apparel, which is making them "
                    "sad:\n  - <color=#D09B61FF>Octave</color>\r\n\n\nGet them some apparel "
                    "that isn't so badly damaged.\n\nYou can tailor apparel at a crafting "
                    "spot or tailoring table, or buy it from traders.",
     "id": "alert:alerts-ace02a59ce3af247:1:4fc958206ee0c70e",
     "label": "Tattered apparel", "ordinal": 1, "priority": "Medium", "targetCount": 1,
     "targets": [{"defName": "Human", "id": "Thing_Human195630", "kind": "pawn",
                  "label": "Octave", "mapId": "Map_0", "mapIndex": 0,
                  "position": {"x": 110, "z": 142}, "thingType": "Verse.Pawn"}],
     "targetsTruncated": False, "type": "RimWorld.Alert_TatteredApparel"},
    {"active": True, "anyCulpritValid": True, "error": None, "exceptionType": None,
     "explanation": "These colonists are nude and not happy about it:\n  - "
                    "<color=#D09B61FF>Octave</color>\r\n\n\nGet them some clothes.\n\nYou "
                    "can tailor apparel at a crafting spot or tailoring table, or buy it "
                    "from traders.",
     "id": "alert:alerts-ace02a59ce3af247:2:2abeef1ecc3e0c61",
     "label": "Unhappy nudity", "ordinal": 2, "priority": "Medium", "targetCount": 1,
     "targets": [{"defName": "Human", "id": "Thing_Human195630", "kind": "pawn",
                  "label": "Octave", "mapId": "Map_0", "mapIndex": 0,
                  "position": {"x": 110, "z": 142}, "thingType": "Verse.Pawn"}],
     "targetsTruncated": False, "type": "RimWorld.Alert_UnhappyNudity"},
    {"active": True, "anyCulpritValid": False, "error": None, "exceptionType": None,
     "explanation": "You have the equipment to do research but have not selected a "
                    "project.\n\n(Click to open the research menu.)",
     "id": "alert:alerts-ace02a59ce3af247:3:f4931b47cedf4cc5",
     "label": "Need research project", "ordinal": 3, "priority": "Medium",
     "targetCount": 0, "targets": [], "targetsTruncated": False,
     "type": "RimWorld.Alert_NeedResearchProject"},
    {"active": True, "anyCulpritValid": True, "error": None, "exceptionType": None,
     "explanation": "One of your colonists has the Brawler personality trait, but is "
                    "carrying a ranged weapon. This will make them very unhappy.",
     "id": "alert:alerts-ace02a59ce3af247:4:f6cc1833b09c82c2",
     "label": "Brawler has ranged weapon", "ordinal": 4, "priority": "Medium",
     "targetCount": 1,
     "targets": [{"defName": "Human", "id": "Thing_Human197118", "kind": "pawn",
                  "label": "Longhoff", "mapId": "Map_0", "mapIndex": 0,
                  "position": {"x": 112, "z": 140}, "thingType": "Verse.Pawn"}],
     "targetsTruncated": False, "type": "RimWorld.Alert_BrawlerHasRangedWeapon"},
]

# The shapes the live map did not happen to be in: a Critical row (the one an
# `== "High"` test dropped), a capped culprit list, a row with no `targets` key
# at all, and a row that will not read.
EDGE_FIXTURE = [
    {"label": "Colonist needs rescue", "ordinal": 1, "priority": "Critical",
     "type": "RimWorld.Alert_ColonistNeedsRescue", "targetCount": 1,
     "explanation": "These colonists are downed and need rescuing:\n  - "
                    "<color=#D09B61FF>Lucas</color>\r\n\n\nDraft someone and carry them "
                    "to a bed.",
     "targets": [{"defName": "Human", "id": "Thing_Human195644", "kind": "pawn",
                  "label": "Lucas", "position": {"x": 118, "z": 139}}],
     "targetsTruncated": False},
    {"label": "Hungry animals", "ordinal": 2, "priority": "High",
     "type": "RimWorld.Alert_AnimalsHungry", "targetCount": 9,
     "explanation": "These tame animals are hungry:\n  - <color=#D09B61FF>Bramble</color>"
                    "\r\n  - <color=#D09B61FF>Mote</color>\r\n\n\nMake sure they have "
                    "access to food.",
     "targets": [{"defName": "Muffalo", "id": "Thing_Muffalo1", "kind": "pawn",
                  "label": "Bramble", "position": {"x": 100, "z": 100}},
                 {"defName": "Muffalo", "id": "Thing_Muffalo2", "kind": "pawn",
                  "label": "Mote", "position": {"x": 101, "z": 100}}],
     "targetsTruncated": True},
    {"label": "Low food", "ordinal": 3, "priority": "High",
     "type": "RimWorld.Alert_LowFood",
     "explanation": "You have almost no food.\n\n(Click to see your stockpiles.)"},
    {"label": "Something broke", "ordinal": 4, "priority": "Medium",
     "type": "RimWorld.Alert_Unknown", "error": "NullReferenceException in GetReport",
     "exceptionType": "System.NullReferenceException",
     "explanation": None, "targets": [], "targetCount": 0},
]


# Pawn rows as `home/list_pawns {equipment:true}` returns them, trimmed to the
# fields the nudity note reads: a shirt declares Torso and Shoulders, pants
# declare Legs alone, and an older build's row carries no `bodyPartGroups` key.
NUDITY_PAWN_FIXTURE = [
    {"name": "Octave", "equipment": {"apparel": [
        {"label": "pants", "bodyPartGroups": [{"defName": "Legs", "label": "legs"}]}]}},
    {"name": "Finn", "equipment": {"apparel": [
        {"label": "button-down shirt",
         "bodyPartGroups": [{"defName": "Torso", "label": "torso"},
                            {"defName": "Shoulders", "label": "shoulders"}]}]}},
    {"name": "Ada", "equipment": {"apparel": []}},
    {"name": "Lucas", "equipment": {"apparel": [
        {"label": "duster"}]}},
]


def _self_test():
    fails = []

    def check(name, got, want):
        ok = got == want
        print("  %s %s" % ("ok  " if ok else "FAIL", name))
        if not ok:
            print("       got  %r\n       want %r" % (got, want))
            fails.append(name)

    print("--- the four real Lampblack day-39 payloads, resolved with NO bridge ---")
    live = resolve(LIVE_FIXTURE)
    for a in live:
        print("  %-26s | %s" % (one_line(a), a["detail"]))
    check("tattered detail", live[0]["detail"],
          "These colonists are wearing tattered apparel, which is making them sad")
    check("tattered culprit is structured", live[0]["culprits"],
          [{"name": "Octave", "id": "Thing_Human195630", "kind": "pawn",
            "x": 110, "z": 142}])
    check("nudity detail", live[1]["detail"],
          "These colonists are nude and not happy about it")
    check("nudity one_line", one_line(live[1]), "Unhappy nudity (Octave)")
    check("research detail", live[2]["detail"],
          "You have the equipment to do research but have not selected a project.")
    check("research names nobody", (live[2]["culprits"], live[2]["mapWide"],
                                    live[2]["culpritCount"]), ([], True, 0))
    check("research one_line", one_line(live[2]), "Need research project")
    check("brawler detail", live[3]["detail"],
          "One of your colonists has the Brawler personality trait, but is carrying a "
          "ranged weapon. This will make them very unhappy.")
    check("brawler one_line", one_line(live[3]), "Brawler has ranged weapon (Longhoff)")

    print("\n--- edge cases ---")
    check("empty list", resolve([]), [])
    check("empty reply dict", resolve({"alerts": []}), [])

    edge = resolve(EDGE_FIXTURE)
    check("every row survives", len(edge), 4)
    # Critical: the priority an `== \"High\"` test dropped in watch.py/setup.py.
    check("Critical is loud", is_loud(edge[0]), True)
    check("Critical one_line", one_line(edge[0]), "Colonist needs rescue (Lucas)")
    # A capped list must never read as a complete one.
    check("truncated count is the real total", edge[1]["culpritCount"], 9)
    check("truncated one_line", one_line(edge[1]), "Hungry animals (Bramble, Mote +7 more)")
    check("truncated flag", edge[1]["culpritsTruncated"], True)
    # No `targets` key at all: nobody is named, and nobody is invented.
    check("missing targets -> no culprits", edge[2]["culprits"], [])
    check("missing targets -> mapWide", edge[2]["mapWide"], True)
    check("missing targets detail", edge[2]["detail"], "You have almost no food.")
    check("missing targets one_line", one_line(edge[2]), "Low food")
    # An unreadable row is kept, labelled, and says so.
    check("unreadable row keeps its label", edge[3]["label"], "Something broke")
    check("unreadable row says so", edge[3]["detail"],
          "COULD NOT BE READ -- NullReferenceException in GetReport "
          "(System.NullReferenceException)")
    # Nothing here may ever pull a name out of the prose.
    check("no name is parsed from explanation",
          [c["name"] for a in edge for c in a["culprits"]], ["Lucas", "Bramble", "Mote"])
    check("unknown priority is loud, not silent", is_loud({"priority": "Extreme"}), True)
    check("priority order holds", [priority_rank(p) for p in PRIORITY_ORDER], [0, 1, 2, 3])
    check("plain(None)", plain(None), "")
    check("plain strips colour", plain("a <color=#D09B61FF>B</color>\r\n c"), "a B c")

    print("\n--- Unhappy nudity: which part is bare, NO bridge ---")

    def nude_line(who, rows=NUDITY_PAWN_FIXTURE):
        """The nudity alert's one_line with `who` blamed, resolved against rows."""
        got = resolve(LIVE_FIXTURE)
        row = next(a for a in got if is_nudity(a))
        for c in row["culprits"]:
            c["name"] = who
        add_nudity_detail(got, rows)
        return one_line(row)

    for who in ("Octave", "Finn", "Ada", "Lucas", "Nobody"):
        print("  " + nude_line(who))
    check("pants only -> torso bare", nude_line("Octave"),
          "Unhappy nudity (Octave: torso bare)")
    check("shirt only -> legs bare", nude_line("Finn"),
          "Unhappy nudity (Finn: legs bare)")
    check("wearing nothing -> both", nude_line("Ada"),
          "Unhappy nudity (Ada: torso and legs bare)")
    check("garment with no bodyPartGroups is unreadable, not bare",
          nude_line("Lucas"),
          "Unhappy nudity (Lucas: apparel coverage NOT REPORTED by this build)")
    check("a culprit the pawn list did not return says so", nude_line("Nobody"),
          "Unhappy nudity (Nobody: %s)" % BARE_UNREAD)
    check("a failed pawn read says so, and keeps the alert",
          nude_line("Octave", "not a list"),
          "Unhappy nudity (Octave: %s)" % BARE_UNREAD)
    check("only the nudity alert is annotated",
          [c.get("bare") for a in add_nudity_detail(resolve(LIVE_FIXTURE),
                                                    NUDITY_PAWN_FIXTURE)
           for c in a["culprits"] if not is_nudity(a)], [None, None])
    check("an absent equipment block is unreadable, not naked",
          bare_groups({"name": "Ada"}), (["torso", "legs"], False))
    check("an empty apparel list IS the answer",
          bare_groups({"equipment": {"apparel": []}}), (["torso", "legs"], True))
    check("no nudity alert, no pawn read",
          add_nudity_detail(resolve(EDGE_FIXTURE), "not a list") is not None, True)

    print("\n--- status_lines (live four + the edge four) ---")
    hot, minor = status_lines(live + edge)
    for l in hot:
        print("  !! " + l)
    print("  " + str(minor))
    check("loud lines are the loud ones", len(hot), 3)
    check("Critical sorts first", hot[0], "Colonist needs rescue (Lucas)")
    check("minor line folds the rest", minor.startswith("alerts (Medium): "), True)
    check("unreadable row reaches a reader", "Something broke" in minor, True)

    print("\n--- brief_lines, cap 3 (the cap must state itself) ---")
    for l in brief_lines(live + edge, cap=3):
        print("  " + l)
    check("cap states itself", brief_lines(live + edge, cap=3)[-1],
          "   ... +5 more alert(s) not listed (`python alerts.py`)")
    check("no alerts is a checked answer", brief_lines([]),
          ["ALERTS: none live. (checked -- read from %s, not assumed.)" % TOOL])

    print("\n%d check(s) failed." % len(fails) if fails else "\nAll checks passed.")
    return 1 if fails else 0


def main():
    argv = sys.argv[1:]
    if argv and argv[0] in ("selftest", "--self-test"):
        return _self_test()
    if argv and argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    try:
        import rim
        rim.init()
        resolved = resolve()
    except Exception as e:
        # LOUD, like buildings.py. An empty list here reads as "nothing is
        # wrong", which is the one wrong answer that matters for alerts.
        print("alerts.py FAILED -- NO ALERTS WERE READ. This is NOT 'no alerts'.")
        print("%s: %s" % (type(e).__name__, e))
        print("  Check the bridge: python setup.py")
        return 1
    try:
        # Only reads pawns when the nudity alert is live, and never at the cost
        # of the alert list: a failure here is one line, not a lost sweep.
        add_nudity_detail(resolved)
    except Exception as e:
        print("   (nudity detail unavailable: %s: %s)" % (type(e).__name__, e))
    if "--json" in argv:
        print(json.dumps(resolved, indent=1))
    elif "--brief" in argv:
        for line in brief_lines(resolved):
            print(line)
    elif "--status" in argv:
        hot, minor = status_lines(resolved)
        for line in hot:
            print("   !!ALERT: " + line)
        if minor:
            print("   " + minor)
        if not hot and not minor:
            print("   alerts: none live. (checked)")
    else:
        show(resolved)
    return 0


if __name__ == "__main__":
    sys.exit(main())
