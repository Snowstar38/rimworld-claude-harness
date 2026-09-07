"""Play in bounded steps and report what changed.

  python watch.py [steps] [ms per step] [speed] [--say "..."] [--mood <mood>]

**Letters and messages do not cover the events that kill you.** A tamed animal,
or a predator hunting a pawn or a pet, is not a letter with a popup -- it just
happens, between two steps. So each step also sweeps for predators and hostiles
within THREAT_RADIUS of anything of ours and prints them. That sweep is the
point of the tool; the letters are the cheap part.

Never run unattended time with a pawn who cannot be drafted.
"""
import sys, letters, rim, turnclock
import map as rimmap

THREAT_RADIUS = 30
# A message fades 13 REAL seconds after it appears (`PlayUntilEventTool.cs:29`),
# so play is cut into chunks no longer than this and `list_messages` is read
# after each one -- a longer chunk lets a message live and die unseen.
PLAY_CHUNK_MS = 11000
# The alert priorities that get a line of their own. `RimWorld.AlertPriority` is
# Medium < High < Critical; everything below these is printed too, folded into
# one line -- see the alert block in step().
LOUD_ALERTS = ("High", "Critical")


class AutoPaused(Exception):
    """RimWorld paused ITSELF because something happened. Not a person.

    Added Aug 30. The class below stops the loop whenever play_for reports
    "Game was paused externally", on the theory that that line is always
    M. It isn't. A raid force-pauses the game and emits the identical
    string, so on day 16 this tool announced "somebody paused the game, not
    resuming" for a pirate marine walking in from the far corner, and I
    believed it and wrote her a note about it. M: *"The pause wasn't
    me, it's a raid."*

    The two are indistinguishable in the message and trivially distinguishable
    one call later: the game only auto-pauses alongside a LETTER. So snapshot
    the letters before the step and compare. A new letter means the game; no
    new letter leaves the source unknown. Both stop the loop -- only one of them may be
    resumed without asking.
    """


class Paused(Exception):
    """An outside pause with unknown source. That outranks this loop.

    On Aug 29 M watched Lucas collapse from heatstroke and pressed pause;
    the next play_for in this loop resumed with forceRequestedSpeed and undid
    her. She pressed it again; it resumed again. While she was trying to get a
    sleeping spot built so Finn could rescue Lucas, Finn collapsed too, and the
    colony had to be reloaded. The tool printed "paused externally" each time
    and carried on -- that line WAS the person, and it was written to shrug.

    A human at the screen is the one instrument here that never misses. Stop.
    """
# 2026-09-04, M's ask. There used to be a hardcoded PREDATORS set of
# sixteen defNames here, and a wild boar 15 cells from a colonist was invisible
# because "Boar" was not on the list -- as would be every modded predator, and
# every vanilla one somebody forgot. The game already knows: RaceProps.predator
# is a bool, and `home/list_pawns` now carries it as `predator` on the row. Ask
# the game, never a list maintained by hand.
#
# Mechanoids come off the row too, from `mechanoid` (RaceProps.IsMechanoid,
# ListPawnsTool.cs) -- the old `Mech_` defName prefix test is gone with the
# predator set, and for the same reason.
PREDATOR_FIELD = "predator"
MECH_FIELD = "mechanoid"
# The pre-2026-08-31 cell sweep can read neither field: `get_cells_plus` returns
# things, not pawn race properties. It keeps a minimal defName test so it is not
# blind, and says out loud that it is degraded. Do not grow this list -- if you
# are reading it, the companion is missing and that is the thing to fix.
FALLBACK_PREDATOR_DEFS = {"Cougar", "Bear_Grizzly", "Bear_Polar", "Lynx",
                          "Panther", "Wolf_Timber", "Wolf_Arctic", "Thrumbo",
                          "Megaspider", "Boomrat", "Boomalope"}
FALLBACK_MECH_PREFIX = ("Mech_",)


def ours():
    out = []
    for c in rim.game("rimworld/list_colonists", {}).get("colonists") or []:
        if not c.get("dead"):
            out.append((c["name"], c["position"]["x"], c["position"]["z"], c))
    return out


def threats(around):
    """Anything worth knowing about near one of ours. Now a list, not a sweep.

    ## One call instead of a tile-by-tile search -- [M], M, 2026-08-31

    This used to read every CELL in a 61x61 box around each colonist -- 8 calls,
    1.7 MB, 0.575s -- and throw away everything but the pawns standing on them.
    Shown the number, M said what it deserved: *"what the heck are we
    doing to check??? it should just be 'pawns within these coordinates, are you
    hostile (red nametag)'"*, and *"i figured the list of pawns in the game
    wouldn't be that long to run through"*.

    It is 45 pawns. `home/list_pawns` (companion DLL, see `pawns.py`) answers
    the whole map in **1 call, 32 KB, 0.077s** -- 51x less data than the old
    30-cell radius, for 250x250 coverage instead. (0.077s was the first
    measurement and this docstring is quoted for it elsewhere; five later calls
    on 46 pawns ran a **median of 26 ms, min 14 ms**, so a check every 1.5s
    costs about 1.7% of wall time. Both numbers are real; the second is the one
    to plan with.) So the radius is gone from the
    dangerous half of the question entirely:

    * **hostiles are never distance-filtered.** A raid on the far side of the
      map is your problem at any range, and the old sweep could not see it.
    * **THREAT_RADIUS now only filters the boring half** -- which harmless
      animals are close enough to bother printing.

    And a predator on an actual `PredatorHunt` job is now separable from one
    asleep in a field, which is the distinction that cost the colony its husky.

    ## The fallback is loud, on purpose

    If the companion is not loaded this falls back to the old cell sweep and
    says so on stdout every step. It must never quietly degrade: this is the
    function whose silence gets a colonist killed.

    Old docstring, kept because the lesson is still the lesson:
    letters and messages do not cover the events that kill you.

    ## Why this reads the companion now -- M, Aug 30 and Aug 31

    *"theres a big stutter every 5 seconds or so."* That is this function. A
    step is `play_for`, then six reads with the game STOPPED, then the next
    `play_for`; five of those reads are small and this one was not. Measured on
    Aug 31, two colonists, one sweep: **8 calls and 6.8 MB of JSON, 0.85s**,
    because it was still asking the stock tool, which spends 649KB per 1024
    cells on data no threat sweep looks at. `home/get_cells_plus` answers the
    same question in 152KB.

    She ruled the stutter acceptable at the time (*"I'm fine with it being every
    5 seconds, its not like its a problem for me"*) and demoted this to polish
    with one hard condition: **never buy smoothness by making the step longer**,
    because a colonist goes serious->down inside ~3 Superfast steps. So nothing
    here touches step length. It fetches the same cells and reads the same
    answer out of a smaller payload.

    ## The bug this had to fix on the way

    The old test was `t.get("className") != "Verse.Pawn"`. That is the STOCK
    spelling; `get_cells_plus` reports the SHORT type name, `Pawn`, so simply
    pointing the old loop at the companion would have matched nothing and
    reported an empty map with no error anywhere -- hazard 1 of the three in the
    design doc, in the one instrument whose silence gets a colonist killed.
    `map.bucket()` already accepts both spellings, so ask it instead.
    """
    try:
        import pawns
        return _threats_from_list(pawns.all_pawns())
    except Exception as e:
        print("   ~~ ** DEGRADED: home/list_pawns unavailable (%s) -- falling"
              " back to the tile-by-tile cell sweep, which CANNOT see faction"
              " hostility and only looks %d cells around each colonist. **"
              % (str(e)[:110], THREAT_RADIUS))
        return _threats_from_cells(around)


def wild_hunter(p):
    """Is this pawn a predator on a hunt that is NOT somebody's tame animal?

    THE ONE COPY OF THIS RULE. `run.py:_wild_hunters` and `verify.py` both call
    it so the three paths cannot drift apart again -- they did, and it cost a
    stream: the colony owns a tame warg (war merchant, day ~30) that hunts wild
    game FOR us. It runs a `PredatorHunt` job like any cougar, so every
    faction-blind hunt check fired on it -- `run.py` stopped at 0 ticks on every
    call, `watch.py` printed `!!NEAR: HUNTING Warg` every step, and Luna's
    Lookout got a HUNTING verdict on our own dog. A WILD predator has no
    faction at all; anything carrying a faction was tamed by somebody, which is
    the only test `home/list_pawns` gives us (`ListPawnsTool.cs:164-185`
    returns a faction display-name string and no `isPlayerFaction`/`tame` bool).

    A wild predator hunting something that is not ours is still returned here --
    it is informational, and the callers apply their own distance rule
    (`run.py:HUNT_RADIUS`) to decide whether it is worth stopping for.
    """
    return ("predatorhunt" in (p.get("job") or "").lower()
            and not p.get("faction"))


def _threats_from_list(everyone):
    """-> [(dist, defName, x, z)], the shape step() has always printed.

    Kept identical so nothing downstream had to change. What DID change is what
    gets in: anything hostile at any range, anything actually hunting AND wild
    (see `wild_hunter`), and any PREDATOR or mechanoid within THREAT_RADIUS --
    read off the row's own `predator` / `mechanoid` flags (2026-09-04), not off
    a list of defNames somebody has to remember to update. A tame predator is
    still dropped by the faction test in `wild_hunter` and by the WILD test
    below, so our own warg does not ring the bell every step.
    """
    out = {}
    saw_predator_field = False
    saw_noncolonist = False
    for p in everyone:
        if p.get("isColonist"):
            continue
        saw_noncolonist = True
        if PREDATOR_FIELD in p:
            saw_predator_field = True
        d = p.get("defName") or "?"
        pos = p.get("position") or {}
        x, z = pos.get("x", -1), pos.get("z", -1)
        dist = p.get("nearestColonistDistance")
        hunting = wild_hunter(p)
        # A predator that is OURS or somebody's is not a threat standing in a
        # field; a wild one is, whether or not it has started hunting yet.
        wild_predator = bool(p.get(PREDATOR_FIELD)) and not p.get("faction")
        # Hostile or hunting: report at ANY distance. Otherwise the old rule.
        if p.get("hostile"):
            d = "HOSTILE " + d
        elif hunting:
            d = "HUNTING " + d
        elif wild_predator:
            d = "PREDATOR " + d
        elif not p.get(MECH_FIELD):
            continue
        if not (p.get("hostile") or hunting) and (dist is None
                                                  or dist > THREAT_RADIUS):
            continue
        key = (d, x, z)
        if out.get(key, 99999) > (dist if dist is not None else 9999):
            out[key] = dist if dist is not None else 9999
    if saw_noncolonist and not saw_predator_field:
        # Silence here is the failure mode this whole function exists against:
        # no `predator` key means no predator line, which looks exactly like an
        # empty field. Say it instead.
        print("   ~~ ** DEGRADED: home/list_pawns rows carry no %r flag on this"
              " build -- predators are NOT being reported. Rebuild and"
              " reinstall the companion DLL (rimworld\\companion\\INSTALL.md)."
              " **" % PREDATOR_FIELD)
    return sorted((v, k[0], k[1], k[2]) for k, v in out.items())


def _threats_from_cells(around):
    """The pre-2026-08-31 tile-by-tile sweep. Only reached when the companion
    is missing, and only ever with the DEGRADED line above it.

    It is DEGRADED in a second way it cannot fix: cells carry things, not race
    properties, so there is no `predator` flag down here. The eleven defNames
    in FALLBACK_PREDATOR_DEFS are a floor, not a list of the predators on this
    map -- a boar, a warg, or anything modded is invisible to this path. It is
    a smoke alarm with a flat battery; the fix is to install the companion."""
    seen = {}
    mw, mh = rim.map_size()
    # Clamp to the map. A block running off the edge used to return no `cells`
    # key at all, which read as "nothing there" -- so the sweep was blind in
    # exactly the corners a pawn gets cornered in (Aug 29).
    rects = [(max(0, x - THREAT_RADIUS), max(0, z - THREAT_RADIUS),
              min(mw, x + THREAT_RADIUS + 1), min(mh, z + THREAT_RADIUS + 1))
             for _, x, z, _ in around]
    # Narrowed 2026-09-03 to what the three lines below read: the things on the
    # cell, and per thing the className and the blueprint/frame flags that
    # `rimmap.bucket()` tests (defName is always emitted). No terrain, roof,
    # walkable, passable, zone, areas or designations -- this sweep is looking
    # for a pawn, not drawing anything. `sparse` drops every cell holding
    # nothing, which is most of a 61x61 box. On the stock-tool fallback neither
    # argument exists and the loop sees everything, which is a superset and
    # gives the same answer.
    for (cx, cz), c in rimmap.scan_rects(
            rects, fields="things", thing_fields="className,build",
            sparse=True).items():
        for t in c.get("things") or []:
            if rimmap.bucket(t) != "pawn":
                continue
            d = t.get("defName") or ""
            if not (d in FALLBACK_PREDATOR_DEFS
                    or d.startswith(FALLBACK_MECH_PREFIX)):
                continue
            dist = min(max(abs(cx - x), abs(cz - z)) for _, x, z, _ in around)
            key = (d, cx, cz)
            if dist <= THREAT_RADIUS and seen.get(key, 999) > dist:
                seen[key] = dist
    return sorted((v, k[0], k[1], k[2]) for k, v in seen.items())


def letter_labels():
    return [l.get("label") for l in
            rim.game("rimworld/list_letters", {}).get("letters", []) or []]


def read_messages(seen):
    """Merge the live messages into `seen`, keyed by id. Cheap (~0.02s, 515 B).

    Messages EXPIRE, so one read at the end of a long step misses whatever
    appeared at the start of it; deduped here because chunked play reads more
    than once and the same message stands across chunks.
    """
    for m in rim.game("rimworld/list_messages", {}).get("messages", []) or []:
        seen.setdefault(m.get("id") or m.get("text"), m)


def step(ms=15000, speed="Superfast"):
    # RimWorld force-pauses itself on some letters, and play_for then reports a
    # short run. That is the game telling you something happened -- print it and
    # keep going rather than dying on it, and never treat it as a full step.
    before = set(letter_labels())
    msgs, left = {}, ms
    while True:
        # Play in PLAY_CHUNK_MS bites, reading messages after each; the total
        # duration and everything below are unchanged.
        chunk = max(0, min(left, PLAY_CHUNK_MS))
        left -= chunk
        if chunk:
            try:
                rim.game("rimworld/play_for",
                         {"durationMs": chunk, "speed": speed,
                          "forceRequestedSpeed": True})
            except rim.BridgeError as e:
                msg = str(e).split(": ", 1)[-1]
                print("   ~~", msg)
                if "paused externally" in msg:
                    # A person and a threat emit the same sentence. A letter
                    # tells them apart; see AutoPaused.
                    new = [l for l in letter_labels() if l not in before]
                    if new:
                        raise AutoPaused("; ".join(new))
                    raise Paused(msg)
                left = 0        # a failed chunk ends the step, not just itself
        read_messages(msgs)
        if left <= 0:
            break
    t = rim.game("rimworld/get_game_info", {}).get("ticksGame")
    # "day/hour" here is tick arithmetic, NOT RimWorld's clock: the real hour is
    # GenDate.HourInteger(ticksGame + gameStartAbsTick, longitude), and without
    # either offset this ran six hours out on 2026-09-02. Every step printing a
    # wrong date is worse than a step printing an honest count, so it says which
    # it is; `home/get_time` (setup.clock_phrase) is the game's own calendar.
    print(f"tick {t}  (day {t//60000}, {t%60000//2500:02d}h"
          f" -- tick arithmetic, no longitude)")
    # Wall time beside game time. Silent for the first five minutes of a turn;
    # after that it is the only thing in the loop that knows the turn is late.
    turnclock.print_budget_line("")
    # The stream wants the colony clock and this step already has it. One local
    # POST on a daemon thread, fire-and-forget: it keeps the overlay's own
    # fallback poller off the bridge while a session is playing.
    try:
        import overlay_client as _ovc
        _ovc.push_tick(t)
    except Exception:
        pass
    # The Lookout may have moved the camera home between steps. This is the one
    # thing every turn runs, so it is where the notice gets delivered -- read
    # once, then gone.
    try:
        import camlock
        for line in camlock.take_notices():
            print("   " + line)
    except Exception:
        pass
    us = ours()
    for name, x, z, c in us:
        print(f"   {name} @{x},{z} {c['job']}"
              f"{' DOWNED' if c['downed'] else ''}"
              f"{' [' + c['mentalState'] + ']' if c.get('mentalState') else ''}")
    for m in msgs.values():
        print("   MSG:", (m.get("text") or "")[:120])
    # Print, then sweep -- ANNOUNCEMENTS ONLY. Three rules meet here and all
    # of them were paid for:
    #
    # 1. Letters persist until somebody clears them, and nobody ever did -- the
    #    stack held an 8.3-day-old letter, so both the STATUS line and any
    #    vision Lookout read six-day-old news as current. M, Aug 31:
    #    "you should just dismiss alerts after you get them imo?" -- keep the
    #    panel true rather than stop reading it. Dismissing a quest letter
    #    clears the notification only; the quest lives in the Quests tab. We
    #    print before we dismiss, so nothing is lost unseen.
    #
    # 2. A letter with CHOICES is a decision, and right-clicking it answers it
    #    by throwing it away. `Wanderer joins` is a choice letter and this loop
    #    could not tell it from an announcement (CHRONICLE, day 26-33), so for
    #    a while every offer the colony got was dismissed unread at the speed
    #    of a print statement. A decision now stays ON THE STACK, and in this
    #    printout, until somebody opens it with `letters.py` and answers it.
    #
    # 3. **The dismissal is no longer instant** (2026-09-02). This loop used to
    #    print an announcement and right-click it away in the same breath, which
    #    kept the panel true and gave the stream a card it had no time to read;
    #    `run.py`, which is how time is actually run now, dismissed nothing at
    #    all and let the panel fill back up. Both ends are `letters.auto_dismiss`
    #    now: ten real seconds of grace, one keep-list, one place that decides.
    #
    # The test for which is which is `letters.is_decision` and is NOT
    # `choiceCount` -- an announcement's dialog has a Close button, so the
    # count says decision for everything (13 of 13 live, Aug 31). One
    # predicate, in one file: never re-derive it here.
    stack = letters.pending()
    # Narrate BEFORE the sweep below throws announcements away. An
    # announcement that is printed and right-clicked in the same breath never
    # existed as far as the overlay is concerned, and announcements are most of
    # what a colony's day is made of. Deduped on disk, so a decision that sits
    # unanswered for six steps is posted once.
    letters.post_new_to_overlay(stack)
    for l in stack:
        real = letters.real_choices(l)
        print("   LETTER: " + (l["label"] or "")
              + (" [%d choices]" % len(real) if real else ""))
        if letters.is_decision(l):
            print("   >>> DECIDE IT, don't leave it: python letters.py open %s"
                  % l["id"])
    # After the whole stack has been printed, never before: the sweep's own
    # line is what tells Hands a letter was thrown away, and it has to land
    # under the letter it is talking about.
    letters.auto_dismiss(stack)
    # Alerts, and the MINOR ones are the point of the second line.
    #
    # This printed `priority == "High"` and nothing else, so a Medium alert
    # reached nobody: not here, not in `setup.py --status` (same filter),
    # and not through `run.py`, whose companion watch takes `minAlertPriority:
    # High` by default. That is three filters agreeing silently, and BUGS.md
    # asked on 2026-09-02 whether a minor notification lands in Hands' context
    # at all. For alerts the honest answer was no. High still gets a line each
    # and still reads as an alarm; everything else gets one folded line, which
    # costs a fork ~15 tokens a step and is the difference between "checked and
    # quiet" and "never looked". Nothing here stops the run either way --
    # `watch.py` has never stopped on an alert and this does not change that.
    # **`== "High"` was also wrong at the top end.** `RimWorld.AlertPriority` is
    # an ordered enum, Medium < High < Critical, and the companion compares it
    # as one (`priority < min`, PlayUntilEventTool.cs:871) -- so `minAlertPriority:
    # High` there means High AND Critical, while an equality test here meant a
    # CRITICAL alert printed as neither. Match on the set, not on one name.
    high, minor = [], []
    for a in rim.game("rimworld/list_alerts", {}).get("alerts", []) or []:
        (high if a.get("priority") in LOUD_ALERTS else minor).append(a)
    for a in high:
        print("   !!ALERT:", a.get("label"))
    if minor:
        print("   alerts (%s): %s"
              % ("/".join(sorted({(a.get("priority") or "?") for a in minor})),
                 "; ".join((a.get("label") or "?") for a in minor[:8])
                 + (" (+%d more)" % (len(minor) - 8) if len(minor) > 8 else "")))
    for dist, defname, x, z in threats(us):
        print(f"   !!NEAR: {defname} at {x},{z} ({dist} cells)")
    # Hand the roster back rather than making the caller ask again. The loop
    # below wanted `list_colonists` a second time for its downed check, and
    # nothing plays the game between here and there -- so it was the same
    # roster, re-fetched, inside the stopped-game boundary M watched
    # stutter. One call saved per step; the answer is identical because the
    # moment is identical.
    return us


if __name__ == "__main__":
    # Stream narration: --say/--mood come out of argv first so the positional
    # parsing below is untouched. Posted after the steps finish.
    try:
        import overlay_client as _ov
        _a, _say, _mood = _ov.take_flags(sys.argv[1:])
    except Exception:
        _ov, _a, _say, _mood = None, sys.argv[1:], None, None
    rim.init()
    n = int(_a[0]) if len(_a) > 0 else 1
    ms = int(_a[1]) if len(_a) > 1 else 15000
    sp = _a[2] if len(_a) > 2 else "Superfast"
    us = []
    for _ in range(n):
        try:
            us = step(ms, sp)
        except AutoPaused as e:
            print(f"   >>> stopping: the GAME paused itself on a letter -- {e}.")
            print("   >>> that is an event, not a person. Handle it, then resume.")
            break
        except Paused:
            print("   >>> stopping: external pause (source unknown). Not resuming.")
            break
        if any(c["downed"] for _, _, _, c in us or ()):
            print("   >>> stopping: a colonist is DOWN.")
            break
    if _ov is not None:
        _ov.say_flags(_say, _mood)
