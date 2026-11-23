"""The Lookout's second look, for when it is about to raise an alarm.

  import verify
  s = verify.survey()                  # four small list calls. No clicks, no sweep.
  print(verify.annotate(luna_text, s)) # alarmed/hedged leads gain a verdict

  python verify.py                     # survey the colony and print it, for a human
  python verify.py --self-test         # the gate and the report shapes, no game

## The gate: only two kinds of line earn a check

* **an alarm** -- the lead is a warning, and a wrong warning costs the Core a
  turn;
* **a hedge** -- "appears", "may be", "hard to tell". That is Luna saying she
  could not make it out.

Everything else passes through untouched, and the footer counts what it let
through, so the gate cannot quietly become a filter. A calm descriptive lead
does not want a second opinion; it wants to be read. **If you are adding a check
because the data happens to be available, that is the failure mode this file
already had once.**

## The two checks, and why neither reads a single map cell

**creature** -- is it there, and is it actually attacking? One call to
`home/list_pawns` (see `pawns.py`), which returns every spawned pawn on the map
with `hostile` and `job`. Nothing here has a radius: the whole-map call is far
cheaper than the old cell sweep, and `RADIUS` below only describes what counts
as "near" in the report.

**fire** -- reads `list_alerts`, on the ruling that the game's fire alert is
authoritative. An alerts read that did not answer returns UNVERIFIED, the same
as the pawn check: an empty list is only "no fire" when it is really a list.

`hostile` covers **both** ways a pawn gets a red nametag: a faction hostile to
the player, and a manhunter mental state -- a manhunter pack is ordinary
wildlife with no faction at all, so a faction-only test would call a pack of
enraged boars neutral. One residual, and it is a true fact rather than a
limitation: a predator that has not begun its hunt is genuinely not hostile, so
`job` is read and a `PredatorHunt` gets its own line, which is a sharper answer
than the alarm Luna was about to send.

## Luna stays blind

The survey runs while she is looking at the picture, and every verdict is
computed after her report is written. Scouts are seeded, Lookouts are blind.
This checks the lead; it does not brief the eye.

## The camera guard

`take_screenshot` photographs wherever the camera is pointing, which is wherever
the last action left it -- about 7% of the map. `get_camera_state` returns
`viewRect` and is **read only**, so the survey reads it and the report says how
many colonists were actually on screen. It does not move the camera: that is a
mutation on a screen a person may be watching.
"""
import re
import sys

import rim
import watch

# Only used to describe "near" in the report -- list_pawns returns the whole map.
RADIUS = watch.THREAT_RADIUS
PAWNS_TOOL = "home/list_pawns"
NAME_LIMIT = 4

TAG = re.compile(r"<[^>]*>")


def strip(s):
    return TAG.sub("", s or "").strip()


def _dict(r, what):
    """A bridge reply that is not a dict, named for what it was.

    GABS answers a call for a game it is not connected to with a refusal
    STRING, so `.get(...)` on it dies with `'str' object has no attribute
    'get'` -- rim.py's docstring warns about exactly this, and a Lookout report
    that says AttributeError instead of "no game is connected" is a footgun
    pointed at whoever reads it at 3am.
    """
    if isinstance(r, dict):
        return r
    raise RuntimeError("%s did not return a dict -- %s"
                       % (what, str(r)[:160] or "empty reply"))


# ------------------------------------------------------------------- gate ---
# Stage one: does this line deserve a check at all? M's two cases, and
# nothing else. Both lists are filters, so the footer counts what they dropped.

ALARM = ("danger", "dangerous", "threat", "attack", "attacking", "warning",
         "risk", "risky", "urgent", "critical", "emergency", "unsafe", "alarm",
         "hostile", "raid", "charging", "stalking", "prowl", "aggressive",
         "menacing", "about to", "at risk", "beware", "watch out", "!",
         # Added with the wide frame -- see the note above it in verify.py's git
         # history. Distance is the wide shot's whole subject, so its alarms
         # sound like arrival, not contact.
         "approach", "incoming", "advancing", "closing in", "converging",
         "heading toward", "heading for", "marching")

HEDGE = ("appears", "appear", "looks like", "look like", "looking like",
         "may be", "might", "maybe", "possibly", "possible", "unclear",
         "hard to tell", "difficult to tell", "seems", "seem", "could be",
         "unsure", "not sure", "uncertain", "something", "unidentified",
         "may need", "worth a closer look", "?")


def gate(lead):
    """-> (should_check, why). M's two cases: a warning, or a hedge."""
    low = lead.lower()
    hits = [w for w in ALARM if w in low]
    if hits:
        return True, "alarm (%s)" % hits[0]
    hits = [w for w in HEDGE if w in low]
    if hits:
        return True, "hedged (%s)" % hits[0]
    return False, ""


CHECKS = (
    ("creature", ("animal", "wildlife", "creature", "beast", "predator",
                  "insect", "bug", "spider", "raider", "hostile", "enem",
                  "intruder", "stranger", "visitor", "wolf", "bear", "cougar",
                  "boar", "muffalo", "herd", "stray", "rat", "squirrel",
                  "deer", "hare", "rabbit", "attack", "pack", "prowl",
                  "stalk", "charg", "menac", "pawn", "someone", "figure")),
    ("fire", ("fire", "burn", "flame", "blaze", "ablaze", "smoke", "scorch",
              "charred")),
)


def topics(lead):
    low = lead.lower()
    return [k for k, words in CHECKS if any(w in low for w in words)]


# ------------------------------------------------------------------ survey ---

def _blank(why):
    # `alerts` is None until list_alerts answers, exactly as `pawns` is: a check
    # must be able to tell "no alerts" from "no answer".
    return {"ok": False, "why": why, "notes": [], "colonists": [], "alerts": None,
            "pawns": None, "others": [], "hostiles": [], "hunters": [],
            "camera": None}


def survey():
    """One read-only pass: four small list calls. Never raises.

    Called from a thread inside look.py while Luna looks at the picture, so an
    exception here would either vanish into silence or kill a report that was
    otherwise fine. Every failure mode comes back as `ok: False` with a `why`,
    which annotate() turns into UNVERIFIED -- the same shape as a success,
    which is the rule the whole Lookout role is built on.
    """
    s = _blank("")
    try:
        rim.init()
        cols = [c for c in
                (_dict(rim.game("rimworld/list_colonists", {}),
                       "list_colonists").get("colonists") or [])
                if not c.get("dead")]
    except RuntimeError as e:
        return _blank(str(e)[:180])
    except Exception as e:
        return _blank("%s: %s" % (type(e).__name__, str(e)[:140]))
    s["colonists"] = [{"name": strip(c.get("name")),
                       "x": (c.get("position") or {}).get("x"),
                       "z": (c.get("position") or {}).get("z")} for c in cols]

    try:
        raw = _dict(rim.game("rimworld/list_alerts", {}),
                    "list_alerts").get("alerts")
        if raw is None:
            # No `alerts` key is a refusal wearing an empty list's clothes.
            raise RuntimeError("the reply carried no 'alerts' key")
        s["alerts"] = ["%s [%s]" % (a.get("label"), a.get("priority"))
                       for a in raw]
    except Exception as e:
        s["notes"].append("list_alerts failed (%s) -- the fire check has no "
                          "channel this run" % str(e)[:60])

    # The whole point of the file. If the companion is not loaded this is the
    # one failure that must be loud: a creature check that silently answers
    # "nothing there" is worse than no creature check at all.
    try:
        import pawns as pawnlib
        s["pawns"] = pawnlib.all_pawns()
        s["others"] = [p for p in s["pawns"] if not p.get("isColonist")]
        s["hostiles"] = [p for p in s["pawns"] if p.get("hostile")]
        # WILD hunters only. Our own tame warg runs a PredatorHunt job all day
        # and used to come back to Luna as a HUNTING verdict on the Lookout
        # report -- the Lookout being the one instrument whose job is to say
        # "is something attacking us". See `watch.wild_hunter`.
        s["hunters"] = [p for p in s["others"] if watch.wild_hunter(p)]
    except Exception as e:
        s["notes"].append("%s unavailable (%s) -- NO pawn check ran; the "
                          "companion DLL may not be installed"
                          % (PAWNS_TOOL, str(e)[:90]))

    try:
        cam = rim.game("rimworld/get_camera_state", {}, strict=False)
        v = (cam or {}).get("viewRect")
        if isinstance(v, dict):
            s["camera"] = v
    except Exception as e:
        s["notes"].append("get_camera_state failed (%s)" % str(e)[:60])

    s["ok"] = True
    return s


# ------------------------------------------------------------------ checks ---

def _some(items, fmt):
    head = ", ".join(fmt(i) for i in items[:NAME_LIMIT])
    return head + (" +%d" % (len(items) - NAME_LIMIT)
                   if len(items) > NAME_LIMIT else "")


def _desc(p):
    d = p.get("nearestColonistDistance")
    return "%s at %d,%d (%s from %s)" % (
        p.get("name") or p.get("defName"),
        (p.get("position") or {}).get("x", -1),
        (p.get("position") or {}).get("z", -1),
        "%d cells" % d if d is not None else "? cells",
        p.get("nearestColonist") or "?")


def check_creature(s):
    """The bear question: is it there, and is it actually attacking?"""
    if s.get("pawns") is None:
        return "UNVERIFIED", "%s did not answer, so nothing checked the pawns" % PAWNS_TOOL

    near = [p for p in s["others"]
            if (p.get("nearestColonistDistance") is not None
                and p["nearestColonistDistance"] <= RADIUS)]
    if s["hostiles"]:
        return "HOSTILE", "%d pawn(s) hostile to us: %s" % (
            len(s["hostiles"]),
            _some(s["hostiles"],
                  lambda p: "%s [%s]" % (_desc(p), p.get("hostileReason"))))
    if s["hunters"]:
        return "HUNTING", "not flagged hostile, but %s" % _some(
            s["hunters"], lambda p: "%s is on a PredatorHunt job" % _desc(p))
    if near:
        return "NOT ATTACKING", "%d non-colonist pawn(s) within %d cells: %s " \
                                "-- none hostile, none hunting" % (
                                    len(near), RADIUS, _some(near, _desc))
    if s["others"]:
        closest = min(s["others"],
                      key=lambda p: p.get("nearestColonistDistance") or 9999)
        return "NOT ATTACKING", "nothing within %d cells of anyone; nearest of " \
                                "%d non-colonist pawns on the map is %s -- and " \
                                "0 hostile map-wide" % (
                                    RADIUS, len(s["others"]), _desc(closest))
    return "NOTHING THERE", "not one non-colonist pawn anywhere on the map"


# RimWorld's fire alerts are labelled "Fire", "Fire in home area" and the
# "... on fire" wordings; survey() stores each row as "label [priority]", so the
# label is at the start. Anchored and word-bounded because a bare `"fire" in`
# also matches Campfire, Firefoam and Ceasefire, none of which is a fire.
FIRE_ALERT = re.compile(r"^\s*fires?\b|\bon fire\b", re.I)


def check_fire(s):
    """The game's own fire alert is authoritative -- and a missing read is not one.

    Makes a lit campfire cost one NO FIRE line instead of a turn.
    """
    if s.get("alerts") is None:
        return "UNVERIFIED", "rimworld/list_alerts did not answer, so nothing " \
                             "checked for fire"
    alert = [a for a in s["alerts"] if FIRE_ALERT.search(a)]
    if alert:
        return "BURNING", "the game's own fire alert is up: %s" % alert[0]
    return "NO FIRE", "no fire alert among the %d active alerts (the game " \
                      "raises one for any fire, and that is authoritative)" \
                      % len(s["alerts"])


RUN = {"creature": check_creature, "fire": check_fire}


# --------------------------------------------------- named conditions (09-02)
#
# The Lookout reported "Octave is starving, malnourished". Finn had the
# malnutrition; Octave's alert was unhappy nudity. NOTHING in this stack joins
# alerts to pawns -- survey() stores alert LABELS and never touches a name -- so
# the join was made in the picture, between the alert panel on the right and a
# name label over a pawn. That is not a bug this file can fix by checking; it is
# a claim this file can refuse to let pass silently, for free, off the roster it
# already holds. Like fire, it runs gate or no gate: the wrong name is exactly
# the line that arrives sounding calm.
CONDITION_WORDS = ("starv", "malnutri", "malnourish", "hungry", "starvation",
                   "sick", "illness", "disease", "infect", "plague", "flu",
                   "hypotherm", "heatstroke", "bleed", "injur", "wounded",
                   "in pain", "exhaust", "mental break", "naked", "nudity")


def named_condition(lead, s):
    """One line if the lead pins a CONDITION on a NAMED colonist, else None.

    Free: it reads the roster survey() already fetched and makes no call. Silent
    when the survey failed, because with no roster there are no names to match
    and a guess here would be the same mistake in the other direction.
    """
    low = lead.lower()
    if not any(w in low for w in CONDITION_WORDS):
        return None
    names = [c["name"] for c in (s.get("colonists") or [])
             if c.get("name")
             and re.search(r"\b%s\b" % re.escape(c["name"]), lead, re.I)]
    if not names:
        return None
    return ("    -> READ NARROWLY: this pins a condition on %s by name. An "
            "alert names a CONDITION, not a pawn -- RimWorld lists the affected "
            "pawns in the alert's EXPLANATION text, never in its label, and "
            "nothing in this stack joins the two. `python pawns.py --health` "
            "is one bridge call and is the source for who has which condition."
            % ", ".join(names))


# ---------------------------------------------------------------- annotate ---

def verdict_lines(lead, s, used=None):
    """-> the '->' lines for one lead, or [] if it does not earn a check."""
    keys = topics(lead)
    named = named_condition(lead, s)
    ok, why = gate(lead)
    if "fire" in keys and not ok:
        # Fire always gets checked, gate or no gate. It is the one topic with a
        # free authoritative answer already sitting in the survey, and that is
        # the entire reason the prompt asks for fire instead of banning it -- a
        # fire lead nobody checked hands the noise straight back.
        ok, why = True, "fire, always checked"
    if not ok:
        # The named-condition note is gate-free for the same reason fire is: it
        # is free, and the line it catches ("X is starving") reads as calm.
        return [named] if named else []
    if not keys:
        # Alarmed or hedged, but about nothing this file can check. Say so: a
        # warning nobody verified must not read like a warning that passed.
        return ([named] if named else []) + [
            "    -> NOT CHECKED: %s, but no creature or fire in it "
            "(those are the only two checks)" % why]
    out = [named] if named else []
    for k in keys:
        if used is not None:
            used.add(k)
        if not s.get("ok"):
            out.append("    -> UNVERIFIED %s: no bridge (%s)" % (k, s.get("why")))
        else:
            v, ev = RUN[k](s)
            out.append("    -> %s: %s" % (v, ev))
    return out


def camera_line(s):
    """What was on screen in the NEAR frame. Read-only; nothing here moves the camera.

    The survey runs after look.py has taken and restored the wide frame, so the
    viewRect this reads is the near frame's -- the one the camera was already
    parked on. That is the frame this line has always been about, and it is the
    one where "not one colonist is on screen" is a real warning; the wide frame
    covers the map by construction and needs no guard.
    """
    v = s.get("camera")
    if not v:
        return None
    on = [c for c in s["colonists"] if c["x"] is not None
          and v["minX"] <= c["x"] <= v["maxX"] and v["minZ"] <= c["z"] <= v["maxZ"]]
    n = len(s["colonists"])
    where = "near frame x %d..%d z %d..%d" % (
        v["minX"], v["maxX"], v["minZ"], v["maxZ"])
    if not n:
        return where
    if not on:
        return ("** %s -- NOT ONE of the %d colonists is on screen. Luna is "
                "looking at empty ground; read every lead accordingly. **"
                % (where, n))
    if len(on) < n:
        off = [c["name"] for c in s["colonists"] if c not in on]
        return "%s -- %d of %d colonists on screen (%s off screen)" % (
            where, len(on), n, ", ".join(off))
    return "%s -- all %d colonists on screen" % (where, n)


def footer(s, used=(), checked=0, total=0):
    """What was looked at, and what was deliberately not."""
    lines = []
    cam = camera_line(s)
    if cam:
        lines.append("camera: " + cam)
    if not s.get("ok"):
        lines.append("verify: NOTHING CHECKED -- %s." % s.get("why"))
        return "\n".join(lines)
    bits = ["verify: %d of %d leads checked" % (checked, total)]
    gated = total - checked
    if gated:
        bits.append("%d neither a warning nor hedged, passed through unchecked "
                    "by design" % gated)
    if s.get("pawns") is not None:
        bits.append("%d pawns on the map, %d hostile, via %s"
                    % (len(s["pawns"]), len(s["hostiles"]), PAWNS_TOOL))
    # Scoped to THIS file on purpose. It was "camera not moved" until look.py
    # grew a wide frame that does move it (and puts it back) -- at which point a
    # blanket claim in the footer would have been the report telling a flat lie
    # about the one piece of state a Hands turn cares about. What the camera did
    # is look.py's line to write; what verify did is this one.
    bits.append("verify itself: reads only, no clicks, moved nothing")
    lines.append("; ".join(bits) + ".")
    for n in s.get("notes") or []:
        lines.append("        note: " + n)
    return "\n".join(lines)


def annotate(text, s):
    """Luna's report, with a verdict under the lines that earned one.

    Lines that are neither LEAD nor WEIRD pass through untouched and in place.
    Nothing here removes a line.
    """
    out, used, checked, total = [], set(), 0, 0
    for raw in text.splitlines():
        out.append(raw)
        up = raw.strip().upper()
        if up.startswith("LEAD:") or up.startswith("WEIRD:"):
            body = raw.split(":", 1)[1].strip()
            if body and "nothing unusual" not in body.lower():
                total += 1
                lines = verdict_lines(body, s, used)
                if lines:
                    checked += 1
                    out.extend(lines)
    out.append(footer(s, used, checked, total))
    return "\n".join(out)


# --------------------------------------------------------------- self-test ---

SAMPLE = """LEAD: A bear is attacking the colonists near the entrance!
LEAD: Something appears to be moving in the trees, hard to tell what
LEAD: Two colonists are hauling steel into the warehouse
LEAD: The stockpile in the north looks dangerously empty
LEAD: Ada is starving, malnourished
LEAD: Smoke is rising near the kitchen
WEIRD: A pawn is standing perfectly still at the map edge"""

BANNER = ("=" * 72 + "\n"
          "  SELF-TEST -- EVERY NUMBER BELOW IS INVENTED. No game was read.\n"
          "  For the real thing run `python verify.py` with the game up.\n"
          + "=" * 72)


def _pawn(name, defName, x, z, dist, hostile=False, reason="none", job=None,
          colonist=False, faction=None):
    return {"name": name, "defName": defName, "position": {"x": x, "z": z},
            "hostile": hostile, "hostileReason": reason, "job": job,
            "faction": faction,
            "isColonist": colonist, "nearestColonist": "Ada",
            "nearestColonistDistance": dist}


def _fake(mode="calm"):
    s = _blank("")
    pawns = [_pawn("Lucas", "Human", 119, 138, 3, colonist=True),
             _pawn("Ada", "Human", 122, 141, 3, colonist=True),
             _pawn("Cougar", "Cougar", 130, 150, 9)]
    if mode == "hostile":
        pawns.append(_pawn("Pirate", "Human", 128, 148, 7, hostile=True,
                           reason="faction:Rough Pirates"))
    if mode == "hunting":
        pawns[2]["job"] = "PredatorHunt"
    if mode == "tame":
        # The negative control for `watch.wild_hunter`: our own warg, on a
        # real PredatorHunt job, must NOT come back as a HUNTING verdict.
        pawns.append(_pawn("Ripper", "Warg", 121, 140, 2, job="PredatorHunt",
                           faction="Lampblack"))
    s.update(ok=True, colonists=[{"name": "Lucas", "x": 119, "z": 138},
                                 {"name": "Ada", "x": 122, "z": 141}],
             alerts=["Low food [High]"], pawns=pawns,
             others=[p for p in pawns if not p["isColonist"]],
             hostiles=[p for p in pawns if p["hostile"]],
             hunters=[p for p in pawns if watch.wild_hunter(p)],
             camera={"minX": 90, "maxX": 160, "minZ": 110, "maxZ": 170})
    if mode == "burning":
        s["alerts"] = ["Fire in home area [Critical]"]
    if mode == "alerts down":
        # The alerts read failed: fire must come back UNVERIFIED, never NO FIRE.
        s["alerts"] = None
    return s


def _self_test():
    print(BANNER)
    for mode, note in (("calm", "a cougar 9 cells away, minding its business"),
                       ("hunting", "the same cougar, on a PredatorHunt job"),
                       ("tame", "our own warg hunting -- must NOT read HUNTING"),
                       ("hostile", "a pirate as well"),
                       ("burning", "the game's own fire alert is up"),
                       ("alerts down", "no alerts read -- fire is UNVERIFIED")):
        print("\n--- %s: %s ---" % (mode, note))
        print(annotate(SAMPLE, _fake(mode)))
    print("\n--- bridge down ---")
    print(annotate(SAMPLE, _blank("URLError: connection refused")))
    print("\n" + BANNER)
    return 0


def main():
    if "--self-test" in sys.argv:
        return _self_test()
    s = survey()
    print(footer(s))
    if s.get("ok"):
        for k in ("creature", "fire"):
            v, ev = RUN[k](s)
            print("  %-10s %-14s %s" % (k, v, ev))
    return 0


if __name__ == "__main__":
    sys.exit(main())
