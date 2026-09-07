"""Who is on the map, how they are, what they carry, and how they are set up.

  python pawns.py                    # every pawn, one line each, colonists first
  python pawns.py --hostile          # only the red nametags
  python pawns.py --near 30          # only within N cells of a colonist
  python pawns.py --name cougar      # substring on name / def / kind, ANY pawn
  python pawns.py Finn --work        # a bare name is `--name Finn`
  python pawns.py --wild             # wild animals only
  python pawns.py --tame             # our animals only
  python pawns.py --prisoners        # prisoners; --humans --mechs --downed --drafted too
  python pawns.py --threats          # the watch view: hostiles anywhere + predators near
  python pawns.py --roster           # the colony: bio, gear, needs, thoughts, mood
  python pawns.py --health           # conditions, worst colonist first
  python pawns.py --health --needs   # + food / rest / joy / mood vs break threshold
  python pawns.py --work             # the Work tab: every priority, per colonist
  python pawns.py --settings --schedule --relations
  python pawns.py --animals          # the Animals tab: every animal, one line
  python pawns.py --health --all     # animals and wild pawns too
  python pawns.py --json             # the raw records
  python pawns.py --help             # this text

  python pawns.py set Ada --work Cooking=1,Hauling=3        # dry run
  python pawns.py set Ada --work Cooking=1 --do             # and apply it
  python pawns.py set Ada --schedule SSSSSSAAWWWWAAWWWWJJAASS --do
  python pawns.py set Ada --selftend on --medcare Best --area Home --do
  python pawns.py set Ada --selftend on --do --no-watch     # apply, no tab opens
  python pawns.py set Ada --selftend on --do --watch        # ...show the tab anyway
      (on a RUNNING clock the watch tab is skipped by default: it hides the
       colony behind a full-screen tab for ~8 s per write. See watch_choice.)
  python pawns.py set Muffalo --train Obedience=on,Release=off   # dry run
  python pawns.py set Thing1234 --slaughter on --do

  import pawns
  pawns.all_pawns()  pawns.hostiles()  pawns.near(30)  pawns.threats()  pawns.roster()
  pawns.config("Ada", work="Cooking=1", do=False)

## The blocks -- one flag per `home/list_pawns` block, any number in one call

  --health    every condition with the game's own severity word and body part,
              bleeding / hours to death, pain, impaired capacities, needs
              tending, and whether self-tend is OFF (it is, for everyone, by
              default)
  --needs     food / rest / joy / mood, hunger category, break risk against the
              pawn's own thresholds -- the number that shows a break coming
  --gear      the weapon (`unarmed` is a value, not a gap) and everything worn
  --bio       age, backstory, traits, skills with passion, and `cannot:` --
              what the pawn can never do (a disabled skill is never shown as 0)
  --thoughts  the memories and situational thoughts moving the mood, worst first
  --work      the Work tab: every job with its priority (0 = never, 1 urgent,
              4 least), what is switched off, and what the pawn can never do
  --schedule  the 24 hours as one letter each, plus what they are on now
  --settings  medical care, hostility response, self-tend, follow, allowed area,
              master -- and the ThingID `set` can address them by
  --relations  family with the label the game draws, partners, bonded animals,
              and what this colonist thinks of every other one
  --animals   the Animals tab: tame or wild, wildness, age, gender, master,
              bonded colonists, every trainable with its step count and whether
              the box is ticked, the slaughter / release / tame / hunt
              designations, and milk / wool / egg / pregnancy

  --all       widen from living colonists to every living pawn, ours and wild
  --hidden    include hediffs the game itself hides (implies --health)
  --name X    substring on name / def / kind; widens past living colonists.
              A bare word is the same thing: `pawns.py Finn --work` is
              `pawns.py --name Finn --work`. It narrows every block --
              --work, --bio, --health, --needs, --gear, --thoughts,
              --schedule, --settings, --roster. A name matching nobody
              prints the colonists that do exist rather than everyone
  --json      the raw records, blocks and all

## The narrowings -- applied by the bridge, not by eye over the answer

  --wild      animals with no faction        --tame      our animals
  --humans    humanlikes only                --mechs     mechanoids only
  --prisoners prisoners only                 --downed    downed only
  --drafted   drafted only

They combine by AND with each other and with `--hostile` / `--near`, and a
pair that cannot both hold (`--wild --tame`) comes back refused with the
reason rather than as an empty list. Any of them widens the list past living
colonists on its own, the way `--all` does, because narrowing a prisoner
question down to colonists answers it with silence.

`--name` is sent to the bridge too, so the reply arrives narrowed -- and it
WIDENS the same way: a name with no narrowing flag searches every pawn on the
map, because `--name cougar --health` asked about colonists is silence. Give a
narrowing flag with it and that flag wins.

Any block narrows the list to living colonists -- **except `--animals`, which
narrows to living ANIMALS**, because an animal question filtered down to
colonists answers itself with silence. If `--animals` is given with any other
block, animals win the narrowing and the other blocks print for those animals.
`--all` widens either back to every living pawn. The footer always names which
filter was applied and counts what survived it.

`--animals` also asks the bridge for the `settings` block, because `master` is
on the Animals tab but lives in `settings{}` in the payload -- one field, one
place, and the line says so rather than a second copy drifting.

`--animals` puts the ACT-NOW rows first -- downed, hostile, hunting -- ahead of
distance, and its footer accounts for every row the bridge sent: how many were
dropped as dead, how many as not-an-animal, how many printed, and whether the
bridge itself pre-filtered on a name. On 2026-09-07 a downed timber wolf three
cells from a colonist could not be found in this listing and nothing in the
output could tell a filtered row from an absent one.

## Distances carry BOTH endpoints

`  3 from Longhoff@123,149` -- the colonist's own cell, from the same snapshot
as the animal's. `nearestColonistDistance` is Chebyshev over live positions and
is right; what goes wrong is comparing it against a position read at another
MOMENT, with pawns walking in between, which on 2026-09-07 turned 100 cells
into "8 from Longhoff". If the arithmetic on the row does not hold, the row
says `!!ROW SAYS n, THE CELLS SAY m` rather than leaving it to be spotted.

`--health` sorts worst first by the act-now flags (downed, life-threatening,
bleeding out, needs tending), then the game's own health percentage, then pain.
`--roster` is the Hands turn opener: bio, gear, needs and mood, no flags.

## set -- the write side

`set <name>` is `home/pawn_config`, and **it is a dry run until `--do`**, the
same convention `zones.py` uses. It prints before -> after for every field named
and the reason for every refusal; nothing is ever silently skipped. On a real
run the "after" is read back out of the game, not echoed from the request.

  --work A=1,B=3   priorities, 0 = never. A job the pawn cannot do is refused
                   with the reason, never quietly dropped. Names are the Work
                   tab labels OR the raw defNames -- `Artistic`, `Art`,
                   `animals`, `Handling`, `plant cut` all resolve, case and
                   spacing ignored. An unknown name is refused before the
                   write, with the list of valid names
  --schedule XXXX  all 24 hours at once, hour 0 first. The letters are
                   A anything, W work, J joy (recreation), S sleep, and
                   M meditate with Royalty. **There is no R**: recreation is J.
                   One bad letter refuses the whole day rather than writing half
  --medcare        NoCare NoMeds HerbalOrWorse NormalOrWorse Best
  --hostility      Ignore Attack Flee
  --selftend on|off   --followdrafted on|off   --followfieldwork on|off
  --area <name>    or `none` to clear the restriction (= the whole map)
  --master <name>  animals only, and only once they have learned Obedience
  --train A=on,B=off   the Animals tab tick boxes. RimWorld cascades these:
                   ticking one on ticks its prerequisites on, off ticks its
                   dependents off. The dry run names every def it will move
  --slaughter on|off       mark or unmark for slaughter. Clears any
                   release-to-wild mark, the way the game's designator does
  --releasetowild on|off   the mirror of it, and clears a slaughter mark
  --nickname NAME  rename a colonist or a tamed animal. A colonist keeps their
                   first and last name and gets a new nick; an animal or a mech
                   is renamed outright. 1 to 32 characters, ours only

Animals share a race name -- five muffalo are all "Muffalo" -- so `set` refuses
an ambiguous name and prints every candidate with its ThingID. `--animals`
prints that ThingID on each line for exactly this reason; pass it instead of
the name.

With the Work tab in simple (checkbox) mode any priority above 0 is applied as
3, because that is what clicking the box does; the reply says so rather than
pretending the number landed.

## Two rules

A block the DLL does not send is REPORTED (`!! ... NOT REPORTED by this
build`), never defaulted: an absent `bio` is not "capable of everything" and an
absent `health` is not "healthy". A failed read raises rather than printing an
empty map.

Every hediff is printed; there is no watch list. The only filter is the game's
own `Hediff.Visible`, its count is in the footer, and `--hidden` turns it off.

## hostile

A red nametag, from either a faction hostile to the player or a manhunter
mental state (a manhunter pack has no faction); `hostileReason` says which. A
predator not yet hunting is genuinely not hostile; `job` carries `PredatorHunt`
and `--threats` flags it separately. Our own tame warg hunts too -- read the
`faction` column. Whole map, one call: never search cell by cell for a pawn,
never click through health tabs.
"""
import json
import sys

import clock
import health
import rim

TOOL = "home/list_pawns"

# The bridge-side narrowings of `home/list_pawns`, flag -> tool argument. Every
# one is a bool the tool echoes back in filters{}; conflicting pairs are refused
# by the tool with a reason rather than answered with an empty list.
FILTER_FLAGS = (
    ("--wild", "wildOnly"),
    ("--tame", "tameOnly"),
    ("--humans", "humanlikeOnly"),
    ("--mechs", "mechanoidsOnly"),
    ("--prisoners", "prisonersOnly"),
    ("--downed", "downedOnly"),
    ("--drafted", "draftedOnly"),
)

PREDATOR_JOB = "predatorhunt"

# Read-side flags that consume the next word. Anything else not starting with
# `-` is a name: `pawns.py Finn --work`.
VALUE_FLAGS = ("--near", "--name")


def _positional_name(argv):
    """The first bare word in argv, or None. `--near 30` is not a name."""
    skip = False
    for a in argv:
        if skip:
            skip = False
            continue
        if a.startswith("-"):
            skip = a in VALUE_FLAGS
            continue
        return a
    return None


def _call(args=None):
    r = rim.game(TOOL, args or {}, strict=False)
    if isinstance(r, dict) and r.get("success") is False and r.get("error"):
        # A refusal is the tool ANSWERING -- contradictory filters come back
        # this way, with the reason -- and is not the missing-companion case
        # below. Both are loud; only one of them is a plumbing fault.
        raise rim.BridgeError("%s refused: %s" % (TOOL, r["error"]))
    if not isinstance(r, dict) or not r.get("success") or "pawns" not in r:
        # A companion that is not loaded must be loud. A pawn check that
        # silently answers "nothing there" is worse than no pawn check: it is
        # the empty-result-that-looks-safe failure this whole stack keeps
        # relearning. Callers that want to degrade may catch this; none may
        # mistake it for an empty map.
        raise rim.BridgeError(
            "%s did not answer (%s). Is the HomeBridge companion DLL installed "
            "and was RimWorld restarted since? See rimworld\\companion\\"
            "INSTALL.md" % (TOOL, (isinstance(r, dict) and r.get("error")) or r))
    opts = args or {}
    if (opts.get("hostileOnly") and not r.get("pawns") and r.get("hostileCount", 0) > 0
            and not any(k in opts for k in ("withinOfColonists", "wildOnly", "tameOnly",
                                            "animalsOnly", "humanlikeOnly", "mechanoidsOnly",
                                            "colonistsOnly", "prisonersOnly", "downedOnly",
                                            "draftedOnly", "nameFilter"))):
        raise rim.BridgeError(
            "%s contradicted itself: hostileCount=%s but hostileOnly returned no rows. "
            "No safe empty result was printed." % (TOOL, r.get("hostileCount")))
    return r


def all_pawns(**kw):
    """Every spawned pawn on the map. kw passes straight to the bridge tool."""
    return _call(kw)["pawns"]


def hostiles():
    """Red nametags only: hostile faction OR manhunter. Map-wide, always."""
    return all_pawns(hostileOnly=True)


def near(radius, include_colonists=False):
    """Pawns within `radius` cells (Chebyshev) of any live colonist."""
    return all_pawns(withinOfColonists=radius, includeColonists=include_colonists)


def hunting(pawns=None):
    """Predators actually on a hunt. Not hostile, but the thing you meant."""
    return [p for p in (pawns if pawns is not None else all_pawns())
            if PREDATOR_JOB in (p.get("job") or "").lower()]


def threats(radius=30):
    """(hostiles anywhere, hunters anywhere, other non-colonists within radius).

    Split deliberately. A raid on the far side of the map is your problem no
    matter how far away it is, so hostiles are never distance-filtered; a
    squirrel is only interesting when it is close.
    """
    everyone = all_pawns()
    hos = [p for p in everyone if p.get("hostile")]
    hunt = [p for p in everyone
            if PREDATOR_JOB in (p.get("job") or "").lower() and not p.get("hostile")]
    close = sorted([p for p in everyone
                    if not p.get("isColonist") and not p.get("hostile")
                    and p.get("nearestColonistDistance") is not None
                    and p["nearestColonistDistance"] <= radius],
                   key=distance_key)
    return hos, hunt, close


def _downed_tag(p):
    """DOWNED is not dead. 2026-09-05: two animals read DOWNED for hours while
    `inv.py --corpses` showed nothing, and nobody could tell which tool lied."""
    if p.get("dead"):
        return "DOWNED (dead)"
    if not p.get("animal"):
        return "DOWNED (alive, not a corpse)"
    if p.get("tame") or p.get("isColonist"):
        which = "colony animal"
    elif p.get("wild"):
        which = "wild"
    else:
        which = p.get("faction") or "no faction"
    return "DOWNED (alive, not a corpse; %s)" % which


def colonist_positions(rows):
    """{name: (x, z)} for every colonist in a reply. Feeds the distance check
    below; empty when the reply carries no colonists (a bridge-side narrowing
    such as `--wild` removes them, and then the check simply does not run)."""
    out = {}
    for r in rows or []:
        if not r.get("isColonist"):
            continue
        pos = r.get("position") or {}
        if pos.get("x") is None or pos.get("z") is None:
            continue
        out[r.get("name")] = (pos["x"], pos["z"])
    return out


def distance_key(p):
    """Sort key for "how close is it".

    `p.get("nearestColonistDistance") or 9999` -- what this replaced -- sent a
    pawn standing ON a colonist's own tile (distance 0, which is falsey) to the
    BOTTOM of the list, behind every hare on the far edge of the map. Distance
    0 is the single most urgent row there is.
    """
    d = p.get("nearestColonistDistance")
    return d if isinstance(d, (int, float)) else 9999


def distance_phrase(p, colonists=None):
    """`  3 from Longhoff@123,149` -- the distance AND both endpoints.

    2026-09-07, turn 13: a horse at 233,156 read as "8 from Longhoff" while
    Longhoff was at 133,154, which is 100 cells. The companion's arithmetic is
    Chebyshev over live positions and checks out offline and live, so what was
    almost certainly compared were two numbers read at different MOMENTS -- the
    animal's position from this call, the colonist's from another, with pawns
    walking in between. Printing the colonist's own cell from the SAME snapshot
    makes that class of misread impossible to commit: both endpoints are on one
    line, and if the arithmetic does not hold the line says so out loud instead
    of leaving it to be spotted.
    """
    d = p.get("nearestColonistDistance")
    if d is None:
        return ""
    who = p.get("nearestColonist")
    at = (colonists or {}).get(who)
    if not at:
        return "%3d from %s" % (d, who)
    text = "%3d from %s@%s,%s" % (d, who, at[0], at[1])
    pos = p.get("position") or {}
    if pos.get("x") is not None and pos.get("z") is not None:
        cells = max(abs(at[0] - pos["x"]), abs(at[1] - pos["z"]))
        if cells != d:
            text += " !!ROW SAYS %d, THE CELLS SAY %d" % (d, cells)
    return text


def line(p, colonists=None):
    tags = []
    if p.get("isColonist"):
        tags.append("OURS")
    if p.get("hostile"):
        tags.append("HOSTILE:" + (p.get("hostileReason") or "?"))
    if PREDATOR_JOB in (p.get("job") or "").lower():
        tags.append("HUNTING")
    if p.get("downed"):
        tags.append(_downed_tag(p))
    if p.get("drafted"):
        tags.append("DRAFTED")
    if p.get("mentalState"):
        tags.append(p["mentalState"])
    return "%-14s %-16s @%3d,%-3d %-16s %s%s" % (
        (p.get("name") or "?")[:14], (p.get("defName") or "?")[:16],
        (p.get("position") or {}).get("x", -1), (p.get("position") or {}).get("z", -1),
        (p.get("faction") or "-")[:16],
        distance_phrase(p, colonists),
        ("  " + " ".join(tags)) if tags else "")



# ------------------------------------------------------------- block printers
#
# One formatter per block, so `--roster` and the flag-by-flag view can never
# drift into disagreeing about what a pawn's gear or mood says. The health and
# needs strings live in `health.py` for the same reason.
ROSTER_SKILL_FLOOR = 5          # below this, a skill is noise on a snapshot
ROSTER_MOOD_ROWS = 5            # worst-first; the negative end is the useful one


def _bio_lines(bio):
    """cannot / traits / skills -- whichever the block actually carries."""
    L = []
    cannot = bio.get("incapableOf")
    if cannot:
        L.append("cannot: " + ", ".join(cannot))
    traits = [x.get("label") for x in (bio.get("traits") or []) if x.get("label")]
    if traits:
        L.append("traits: " + ", ".join(traits))

    # A disabled skill reports level 0 with the real number in levelStored, so
    # printing the 0 would say "bad at shooting" about someone who cannot shoot
    # at all. `cannot:` above already carries that fact.
    sk = []
    for s in bio.get("skills") or []:
        if s.get("disabled"):
            continue
        lvl = s.get("level")
        if lvl is not None and lvl >= ROSTER_SKILL_FLOOR:
            pas = s.get("passion")
            sk.append("%s %d%s" % (s.get("name"), lvl,
                                   "(%s)" % pas if pas and pas != "None" else ""))
    if sk:
        L.append("skills: " + "  ".join(sk))
    return L


def _gear_line(eq):
    gear = eq.get("primaryLabel") or ("?" if not eq else "unarmed")
    worn = [a.get("label") for a in (eq.get("apparel") or []) if a.get("label")]
    return "gear:   %s | %s" % (gear, ", ".join(worn) if worn else "WEARING NOTHING")


def _roster_needs_line(nd):
    def pct(v):
        return "--" if v is None else "%d%%" % round(v * 100)
    risk = nd.get("breakRisk")
    return ("needs:  food %s  rest %s  joy %s  mood %s%s"
            % (pct(nd.get("food")), pct(nd.get("rest")), pct(nd.get("joy")),
               pct(nd.get("mood")),
               "  BREAK RISK: %s" % risk if risk not in (None, "None") else ""))


def _mood_line(th):
    moods = [m for m in (th.get("memories") or []) + (th.get("situational") or [])
             if m.get("moodOffset")]
    moods.sort(key=lambda m: m.get("moodOffset") or 0)
    if not moods:
        return None
    return "mood:   " + "  ".join("%+d %s" % (m["moodOffset"], m.get("label"))
                                 for m in moods[:ROSTER_MOOD_ROWS])


WORK_OFF_CAP = 8                # switched-off jobs past this print as a count
RELATION_OPINION_ROWS = 6       # worst-first; the negative end is the useful one


def _work_line(wk):
    """`work: Doctor 1  Cooking 2 ...  off: Mining  cannot: Hunting`.

    A priority that was NOT READ prints as `?`, never as 0: reading the table of
    a pawn who has never had one pauses the colony and assigns it six jobs, so
    the DLL does not read it, and 0 there would say "never do this job" about
    someone whose settings were simply never looked at.
    """
    if not wk.get("applies"):
        return "work:   -- %s" % (wk.get("note") or "not applicable to this pawn")
    on, off, never = [], [], []
    for t in wk.get("types") or []:
        name = t.get("name") or "?"
        if t.get("disabled"):
            never.append(name)
        elif t.get("priority") is None:
            on.append("%s ?" % name)
        elif t.get("priority") > 0:
            on.append("%s %d" % (name, t["priority"]))
        else:
            off.append(name)
    line = "work:   " + ("  ".join(on) if on else "nothing assigned")
    if off:
        line += "  |  off: " + (" ".join(off[:WORK_OFF_CAP])
                                + (" +%d" % (len(off) - WORK_OFF_CAP)
                                   if len(off) > WORK_OFF_CAP else ""))
    if never:
        line += "  |  cannot: " + " ".join(never)
    if wk.get("manualPriorities") is False:
        # Every active job reads as 3 in this mode whatever is stored, so a
        # number here is the tab's, not the pawn's.
        line += "  |  SIMPLE MODE -- numbers are 3-or-0, not real priorities"
    elif wk.get("manualPriorities") is None:
        line += "  |  !! priority mode NOT READ -- the numbers may be masked"
    return line


def _schedule_line(sch):
    """`sched:  SSSSSSAAWWWW...  now Anything`. The letter key is a footer."""
    if not sch.get("applies") or not sch.get("hours"):
        return "sched:  -- %s" % (sch.get("note") or "no timetable")
    return "sched:  %s  now %s" % (sch["hours"], sch.get("current") or "?")


def _schedule_key_line(sch):
    """One footer line spelling the letters out, printed once per view."""
    key = (sch or {}).get("key") or {}
    if not key:
        return None
    return "   letters: " + "  ".join("%s=%s" % (k, v) for k, v in sorted(key.items()))


def _settings_line(st):
    """The Assign-tab row. `area -` is UNRESTRICTED, which is a setting."""
    if not st.get("applies"):
        return "set:    -- %s" % (st.get("note") or "no player settings")

    def onoff(v):
        return "?" if v is None else ("on" if v else "off")

    follow = [n for n, v in (("drafted", st.get("followDrafted")),
                             ("fieldwork", st.get("followFieldwork"))) if v]
    return ("set:    med %s  hostility %s  selfTend %s  area %s  follow %s  master %s"
            % (st.get("medCare") or "?", st.get("hostilityResponse") or "?",
               onoff(st.get("selfTend")),
               st.get("allowedArea") or ("unrestricted"
                                         if st.get("allowedAreaIsUnrestricted") else "?"),
               "+".join(follow) if follow else "-",
               st.get("master") or "-"))


def _relations_lines(rel):
    """Family on one line, opinions on the next. Either may be absent."""
    if not rel.get("applies"):
        return ["family: -- %s" % "no relations tracker"]
    L = []
    fam = []
    for r in rel.get("direct") or []:
        who = r.get("otherName") or "?"
        tags = []
        if r.get("otherDead"):
            tags.append("dead")
        elif not r.get("otherOnThisMap"):
            tags.append("away")
        if r.get("otherIsAnimal"):
            tags.append("animal")
        fam.append("%s %s%s" % ((r.get("label") or r.get("defName") or "?").lower(),
                                who, " (%s)" % ", ".join(tags) if tags else ""))
    if fam:
        L.append("family: " + ", ".join(fam))

    ops = [o for o in (rel.get("colonistOpinions") or []) if o.get("opinion")]
    ops.sort(key=lambda o: o.get("opinion") or 0)
    if ops:
        floor = any(o.get("situationalSocialCached") is False for o in ops)
        L.append("opinion: " + "  ".join(
            "%s %+d" % (o.get("name"), o["opinion"])
            for o in ops[:RELATION_OPINION_ROWS])
            + ("   (a floor -- RimWorld has not cached the situational half)"
               if floor else ""))
    return L


ANIMAL_TRAIN_KEY = ("   train key: x/y = steps done, done = learned, * = the box is ticked "
                    "(wanted), - = this animal can never learn it")


def _animal_line(p):
    """One line for one animal: what the Animals tab row says.

    `master` comes from the `settings` block, not from `animals` -- the payload
    keeps it in one place on purpose, and this line reads it from there rather
    than expecting a second copy. A `?` in that column means the settings block
    was not asked for, never "no master".
    """
    an = p.get("animals") or {}
    if not an.get("applies"):
        return "animal: -- %s" % (an.get("note") or "not an animal")

    st = p.get("settings") or {}
    if an.get("tame"):
        allegiance = "tame"
    elif an.get("wild"):
        allegiance = "wild"
    else:
        allegiance = (an.get("faction") or "other")[:8]

    age = an.get("ageYears")
    sex = (an.get("gender") or "?")[:1]
    master = st.get("master") if st else None
    bonded = an.get("bonded") or []

    tr = an.get("training") or {}
    toks = []
    for t in tr.get("trainables") or []:
        name = t.get("name") or "?"
        if not t.get("canTrain"):
            # A trainable this animal can never learn. Printed, not dropped:
            # "why will it not haul" is the question this column answers.
            toks.append("%s:-" % name)
            continue
        if t.get("learned"):
            state = "done"
        elif t.get("steps"):
            state = t["steps"]
        else:
            # steps is null when the internal getter was not reachable. `?` is
            # NOT ZERO, and the block says so in training.stepsReadable.
            state = "?"
        toks.append("%s:%s%s" % (name, state, "*" if t.get("wanted") else ""))
    if not tr.get("applies"):
        toks = ["(no training tracker)"]

    des = an.get("designations") or {}
    marks = [n.upper() for n, k in (("slaughter", "slaughter"),
                                    ("release", "releaseToWild"),
                                    ("tame", "tame"), ("hunt", "hunt"))
             if des.get(k)]
    if des.get("readable") is False:
        marks.append("DESIGNATIONS-NOT-READ")

    pr = an.get("produce") or {}
    prod = []
    for label, full, key in (("milk", "milkFullness", "hasMilkable"),
                             ("wool", "woolFullness", "hasShearable"),
                             ("egg", "eggFullness", "hasEggLayer")):
        if not pr.get(key):
            continue
        v = pr.get(full)
        prod.append("%s %s" % (label, "?" if v is None else "%d%%" % round(v * 100)))
    if pr.get("pregnant"):
        g = pr.get("gestationProgress")
        prod.append("PREGNANT" + ("" if g is None else " %d%%" % round(g * 100)))

    return ("animal: %-4s %3s%s  master %-10s  %s%s%s%s"
            % (allegiance,
               "?" if age is None else age, sex,
               (master or ("?" if not st else "-"))[:10],
               " ".join(toks) if toks else "(no trainables)",
               ("  bond:" + ",".join(str(b) for b in bonded)) if bonded else "",
               ("  " + " ".join(marks)) if marks else "",
               ("  " + " ".join(prod)) if prod else ""))


def _animal_id_line(p):
    """The ThingID `set` can address this animal by. Animals share a race name,
    so for most of them this is the only unambiguous handle there is."""
    an = p.get("animals") or {}
    tid = an.get("thingId")
    return "  id: %s" % tid if tid else None


def _missing_line(pairs):
    """`!!` line for every asked-for block the running DLL did not send.

    Reported, never defaulted: an absent block is "not looked at", not "empty".
    """
    missing = [n for n, blk in pairs if not blk]
    if not missing:
        return None
    return ("!! %s NOT REPORTED by this build -- not looked at, not empty. "
            "Rebuild the companion (INSTALL.md)." % "/".join(missing))


# ---------------------------------------------------------------- the roster
#
# What a Hands fork is handed at the start of a turn: every colonist, with the
# things you cannot get by looking at the map. ONE `home/list_pawns` call
# carrying four blocks -- no second round trip, no selection.
#
# Colonists only, by `isColonist`, which is also what keeps the ancient-ruins
# pawns in the fog out of it: they are pawns, they are not colonists.


def roster(rows=None):
    """[(pawn, lines)] for every colonist. Pure once `rows` is given."""
    if rows is None:
        rows = all_pawns(bio=True, thoughts=True, equipment=True, needs=True)
    out = []
    for p in sorted([r for r in rows if r.get("isColonist")],
                    key=lambda r: (r.get("name") or "")):
        bio = p.get("bio") or {}
        eq = p.get("equipment") or {}
        nd = p.get("needs") or {}
        th = p.get("thoughts") or {}

        L = _bio_lines(bio)
        L.append(_gear_line(eq))
        L.append(_roster_needs_line(nd))
        mood = _mood_line(th)
        if mood:
            L.append(mood)
        miss = _missing_line((("bio", bio), ("thoughts", th),
                              ("gear", eq), ("needs", nd)))
        if miss:
            L.append(miss)
        out.append((p, L))
    return out


def print_name_miss(name):
    """A name matched nothing: name who does exist, in one more read.

    Printing everyone reads as an answer to the question actually asked, and
    printing nothing reads as "that colonist is gone". Neither is true.
    """
    print("no pawn matched %r -- that is the name filter, not an empty map."
          % name)
    print("   the bridge matches a case-insensitive SUBSTRING of name / "
          "defName / kindDef. An animal's name is its race label (`Timber "
          "wolf`) unless it is tame and has been given one, and its defName is "
          "`Wolf_Timber` -- so `wolf` matches and `timberwolf` does not.")
    try:
        rows = all_pawns()
    except Exception as e:
        print("   (who DOES exist could not be read: %s: %s)"
              % (type(e).__name__, e))
        return
    who = sorted(p.get("name") or "?" for p in rows if p.get("isColonist"))
    print("   colonists: %s" % (", ".join(who) if who else
                                "none on the map at all"))
    # 2026-09-07, turn 20: the search that came back empty was for a downed
    # wolf. Name the animals too, and name the DOWNED ones first -- a listing
    # that answers "which colonists exist" to an animal question is silence
    # with a header on it.
    beasts = sorted((p for p in rows if p.get("animal")),
                    key=lambda p: (not act_now(p), distance_key(p)))
    if beasts:
        print("   animals (%d, act-now first): %s%s"
              % (len(beasts),
                 ", ".join("%s/%s%s" % (b.get("name"), b.get("defName"),
                                        " DOWNED" if b.get("downed") else "")
                           for b in beasts[:12]),
                 "" if len(beasts) <= 12 else ", +%d more" % (len(beasts) - 12)))


def print_roster(rows=None):
    rs = roster(rows)
    print("COLONY ROSTER -- %d colonist(s), one call, read now" % len(rs))
    for p, lines in rs:
        bio = p.get("bio") or {}
        age = bio.get("ageBiological")
        story = " / ".join(x for x in (bio.get("childhood"), bio.get("adulthood")) if x)
        print("")
        print("%s%s%s" % (p.get("name"),
                          "  %s" % age if age is not None else "",
                          "  %s" % story if story else ""))
        for l in lines:
            print("  " + l)
    if not rs:
        print("  no colonists returned -- this is NOT an empty colony, it is a "
              "failed read. Check `python setup.py --status`.")
    return rs


# --------------------------------------------------------------- the details
#
# The flag-by-flag view: the map line, then one indented line per block asked
# for. `health.summary` and `health.needs_summary` are the same strings the
# health view has always printed.


def detail_lines(p, want):
    """Indented block lines for one pawn. `want` is the set of block flags."""
    L = []
    if "health" in want:
        if p.get("health"):
            L.append("health: %s" % (health.summary(p) or "healthy"))
        else:
            # The one block whose absence would print as good news.
            L.append("health: !! NOT REPORTED by this build -- that is NOT "
                     "'healthy'. Rebuild the companion (INSTALL.md).")
    if "needs" in want and p.get("needs"):
        L.append("needs:  %s" % (health.needs_summary(p) or "-"))
    if "gear" in want:
        L.append(_gear_line(p.get("equipment") or {}))
    if "bio" in want:
        L.extend(_bio_lines(p.get("bio") or {}))
    if "thoughts" in want:
        mood = _mood_line(p.get("thoughts") or {})
        if mood:
            L.append(mood)
    # A block the DLL did not send gets no line at all -- `_missing_line` below
    # says so instead. Printing "not applicable" for a block that was never sent
    # would answer a question nobody asked with the wrong answer.
    if "work" in want and p.get("work"):
        L.append(_work_line(p["work"]))
    if "schedule" in want and p.get("schedule"):
        L.append(_schedule_line(p["schedule"]))
    if "settings" in want and p.get("settings"):
        L.append(_settings_line(p["settings"]))
    if "relations" in want and p.get("relations"):
        L.extend(_relations_lines(p["relations"]))
    if "animals" in want and p.get("animals"):
        L.append(_animal_line(p))
        idline = _animal_id_line(p)
        if idline:
            L.append(idline.strip())
    L = [l for l in L if l]
    miss = _missing_line([(n, p.get({"gear": "equipment"}.get(n, n)))
                          for n in ("needs", "gear", "bio", "thoughts",
                                    "work", "schedule", "settings", "relations",
                                    "animals")
                          if n in want])
    if miss:
        L.append(miss)
    return L


def act_now(p):
    """Downed, hostile or hunting: the rows that decide what happens next.

    A DOWNED animal belongs at the top of an animal listing, not buried by
    distance among forty hares: it gets back up, and until it does it is a
    hunt, a rescue or a butchering waiting to be ordered.
    """
    return bool(p.get("downed") or p.get("hostile")
                or PREDATOR_JOB in (p.get("job") or "").lower())


def print_detail(ps, want, everyone, hidden, colonists=None, accounting=None):
    """The --health / --needs / --gear / --bio / --thoughts view."""
    if "health" in want:
        ps = sorted(ps, key=health._worst)
    elif "animals" in want:
        ps = sorted(ps, key=lambda p: (not act_now(p), distance_key(p)))
    else:
        ps = sorted(ps, key=lambda p: (not p.get("isColonist"),
                                       distance_key(p)))
    hurt = hiddenc = 0
    for p in ps:
        print(line(p, colonists))
        for l in detail_lines(p, want):
            print("    " + l)
        h = p.get("health") or {}
        hiddenc += h.get("hediffsHiddenByVisibleFilter") or 0
        if "health" in want and health.summary(p):
            hurt += 1
    if "schedule" in want:
        key = next((_schedule_key_line(p.get("schedule")) for p in ps
                    if (p.get("schedule") or {}).get("key")), None)
        if key:
            print(key)
    if "animals" in want:
        print(ANIMAL_TRAIN_KEY)
        blocks = [p.get("animals") or {} for p in ps]
        tame = sum(1 for b in blocks if b.get("tame"))
        wild = sum(1 for b in blocks if b.get("wild"))
        beasts = sum(1 for b in blocks if b.get("applies"))
        downed = sum(1 for x in ps if x.get("downed"))
        print("-- %d animal(s) of %d row(s): %d tame, %d wild, %d another "
              "faction, %d DOWNED (they get back up)"
              % (beasts, len(ps), tame, wild, beasts - tame - wild, downed))
        # 2026-09-07, turn 20: a downed timber wolf three cells from a colonist
        # could not be found in this listing at all, and there was no way to
        # tell a filtered row from an absent one. Every row the bridge sent is
        # accounted for here, so "it is not in the list" and "it was dropped"
        # can never read the same again.
        if accounting:
            print("   rows: bridge returned %d, %d dropped as dead, %d dropped "
                  "as not-an-animal, %d printed.%s"
                  % (accounting.get("returned", 0), accounting.get("dead", 0),
                     accounting.get("notAnimal", 0), len(ps),
                     "" if not accounting.get("nameFilter") else
                     "  The bridge ALSO pre-filtered on nameFilter=%r "
                     "(substring of name / defName / kindDef): drop it to see "
                     "every animal." % accounting["nameFilter"]))
        if any(b.get("applies") and (b.get("training") or {}).get("stepsReadable") is False
               for b in blocks):
            print("   !! training step counts were NOT READ in this build "
                  "(GetSteps is internal) -- an x/y of ? is unread, not zero.")
    if "health" in want:
        print("-- %d of %d with something; NO filter applied, every hediff the "
              "game shows%s" % (hurt, len(ps),
                                "" if hidden else
                                (", %d hidden one(s) not shown (--hidden)" % hiddenc
                                 if hiddenc else "")))
    else:
        print("-- %d pawns, %d hostile"
              % (len(ps), sum(1 for p in ps if p.get("hostile"))))
    if not everyone:
        print("   (living %s only -- `--all` widens it to every pawn)"
              % ("animals" if "animals" in want else "colonists"))


# ------------------------------------------------------------------- the write
#
# `home/pawn_config`. Dry run until `--do`, the same convention as `zones.py`,
# and for the same reason: the plan a real run would execute is worth reading
# before it runs.

# Work tab labels -> the WorkTypeDef defNames `home/pawn_config` accepts; the
# two differ for several types (Artistic/Art, Animals/Handling). Taken from
# Core/Defs/WorkTypeDefs/WorkTypes.xml plus Biotech, Anomaly and Odyssey. Each
# defName is a key of itself, so passing a real defName is unchanged.
# Offline fallback only: a live work{} block carries both name and label for
# every def the running game has, mods included, and work_lookup() prefers it.
WORK_ALIASES = {
    "Firefighter": "Firefighter", "Patient": "Patient", "Doctor": "Doctor",
    "Bed rest": "PatientBedRest", "Basic": "BasicWorker", "Warden": "Warden",
    "Handle": "Handling", "Animals": "Handling", "Cook": "Cooking",
    "Hunt": "Hunting", "Construct": "Construction", "Grow": "Growing",
    "Mine": "Mining", "Plant cut": "PlantCutting", "Smith": "Smithing",
    "Tailor": "Tailoring", "Art": "Art", "Artistic": "Art",
    "Craft": "Crafting", "Haul": "Hauling", "Clean": "Cleaning",
    "Research": "Research", "Childcare": "Childcare", "Dark study": "DarkStudy",
    "Fish": "Fishing",
}


def _work_key(s):
    """`Plant cut`, `plant-cut` and `PlantCut` are one key: case and
    punctuation are formatting, not identity."""
    return "".join(c for c in str(s).lower() if c.isalnum())


def work_lookup(types=None):
    """squashed word -> defName. `types` is a live work{}.types[] if there is one."""
    m = {}
    for label, defname in WORK_ALIASES.items():
        m[_work_key(label)] = defname
        m[_work_key(defname)] = defname
    for t in types or []:
        defname = t.get("name")
        if not defname:
            continue
        for word in (t.get("label"), defname):
            if word:
                m[_work_key(word)] = defname
    return m


def resolve_work(spec, types=None):
    """(`Cooking=1,Handling=3`, unknown[], lookup) for a --work spec.

    Names only: the priority half is passed through untouched, and so is a
    malformed pair with no `=` -- the bridge refuses that with the better message.
    """
    lookup = work_lookup(types)
    out, unknown = [], []
    for pair in str(spec).replace(";", ",").split(","):
        pair = pair.strip()
        if not pair:
            continue
        name, sep, prio = pair.partition("=")
        defname = lookup.get(_work_key(name)) if sep else None
        if not sep:
            out.append(pair)
        elif defname is None:
            unknown.append(name.strip())
            out.append(pair)
        else:
            out.append("%s=%s" % (defname, prio.strip()))
    return ",".join(out), unknown, lookup


def live_work_types(name):
    """The running game's work{}.types[] for one pawn, or None. Best effort --
    it catches work types (mods) the static table cannot list."""
    try:
        for p in all_pawns(work=True, nameFilter=name):
            types = (p.get("work") or {}).get("types")
            if types:
                return types
    except Exception:
        pass
    return None


SET_FLAGS = (("--work", "work"), ("--schedule", "schedule"),
             ("--medcare", "medCare"), ("--hostility", "hostilityResponse"),
             ("--selftend", "selfTend"), ("--followdrafted", "followDrafted"),
             ("--followfieldwork", "followFieldwork"),
             ("--area", "allowedArea"), ("--master", "master"),
             ("--train", "training"), ("--slaughter", "slaughter"),
             ("--releasetowild", "releaseToWild"), ("--nickname", "nickname"))


# `Watch.DefaultSeconds` in the companion (`companion/src/Watch.cs`): how long
# the tab the write opens stays up. Named here because the cost of the watch is
# exactly this many seconds of the colony being invisible.
WATCH_SECONDS = 8


def watch_choice(do, on=False, off=False, state=None):
    """Should this write open its watch tab? -> (watch, why).

    The watch is the decorative half of a write: it selects the pawn, opens the
    Work / Schedule / Assign tab a player would have used, holds it
    WATCH_SECONDS, then closes it (`companion/src/Watch.cs`).

    **On a running clock that is eight seconds of the colony hidden behind a
    full-screen tab, once per settings write, live on stream** (BUGS,
    2026-09-05). On a stopped clock nothing is moving and the tab costs
    nothing, which is the case the watch was designed for. So the default is
    conditional on the clock rather than fixed: `--watch` forces it on,
    `--no-watch` forces it off, and neither one is second-guessed.
    """
    if off:
        return False, "--no-watch"
    if on:
        return True, "--watch: shown because you asked for it"
    if not do:
        return True, "dry run -- the companion skips the watch on a dry run anyway"
    st = clock.state() if state is None else state
    if clock.is_running(st):
        return False, ("the clock is RUNNING (%s), and the watch tab would hide"
                       " the colony for ~%d s per write. Pass --watch to show"
                       " it anyway." % (st.get("speed") or "?", WATCH_SECONDS))
    return True, ("the clock is %s, so the tab hides nothing that is moving"
                  % ((st or {}).get("state") or "unread"))


def watch_line(r, on=None, why=None):
    """One line about the menu the write opened, if any. Claims the camera for
    Hands when the watch moved it, so the Lookout stays out of the shot."""
    w = (r or {}).get("watch") or {}
    if not w.get("shown"):
        print("watch: skipped (%s)%s" % (w.get("reason") or "not shown",
                                         (" -- " + why) if why else ""))
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


def config(name, do=False, watch=True, **kw):
    """Set any subset of one pawn's configuration. Dry run unless do=True.

    watch=False writes with no UI; otherwise a real write opens the tab a
    player would use a moment before the change and closes it after."""
    args = {"pawn": name, "dryRun": not do, "watch": watch}
    args.update({k: v for k, v in kw.items() if v is not None})
    # strict=False: a refused field is the answer, not a fault.
    return rim.game("home/pawn_config", args, strict=False)


def _show(v):
    if v is None:
        return "(none)"
    if v is True:
        return "on"
    if v is False:
        return "off"
    # A nickname row's before/after is {name, nick}: the full name and the
    # short one the game labels the pawn with.
    if isinstance(v, dict) and "nick" in v:
        return "%s (%s)" % (v.get("nick") or "?", v.get("name") or "?")
    return str(v)


def print_config(r, watch_why=None):
    """before -> after per field, then every refusal. Pure: takes the reply."""
    if not r.get("success"):
        print("SET FAILED: %s" % (r.get("error") or r.get("message") or r))
        return
    who = r.get("pawn") or {}
    print("SET %s %s%s"
          % (who.get("name") or "?",
             "DRY RUN" if r.get("dryRun") else "DONE",
             "" if r.get("fields") else " -- no field was named, nothing to do"))
    for f in r.get("fields") or []:
        if f.get("refused"):
            continue
        print("  %-22s %s -> %s%s"
              % (f.get("field"), _show(f.get("before")), _show(f.get("after")),
                 "" if f.get("changed") else "   (no change)"))
        if f.get("note"):
            print("      note: " + f["note"])
        # A training write cascades and a designation write clears its
        # opposite; both are RimWorld's own rules and both are named in the row
        # rather than discovered later by a confused reader.
        for extra, word in (("cascades", "also sets"), ("alsoRemoved", "also clears")):
            vals = f.get(extra)
            if vals:
                print("      %s: %s" % (word, ", ".join(str(v) for v in vals)))
    for f in r.get("refused") or []:
        print("  REFUSED %-14s %s -- %s"
              % (f.get("field"), _show(f.get("requested")), f.get("reason")))
    if r.get("dryRun"):
        print("  nothing was written. Re-run with --do to apply.")
    elif r.get("afterIsPredicted"):
        # Should never happen on a real run; if it does, the "after" is a
        # prediction and saying so beats letting it read as measured.
        print("  !! the after values are PREDICTED, not read back.")
    watch_line(r, (r.get("pawn") or {}).get("name"), watch_why)


def main_set(argv):
    """`pawns.py set <name> [flags] [--do]`."""
    do = "--do" in argv
    watch_off = "--no-watch" in argv
    watch_on = "--watch" in argv
    argv = [a for a in argv if a not in ("--do", "--no-watch", "--watch")]
    if not argv or argv[0].startswith("--"):
        print("usage: pawns.py set <name> [--work A=1,B=3] [--schedule <24 letters>] "
              "[--medcare X] [--hostility X] [--selftend on|off] "
              "[--followdrafted on|off] [--followfieldwork on|off] "
              "[--area <name>|none] [--master <name>|none] "
              "[--train A=on,B=off] [--slaughter on|off] [--releasetowild on|off] "
              "[--nickname NAME] "
              "[--do] [--watch|--no-watch]")
        print("  --schedule letters: A anything, W work, J joy (recreation),"
              " S sleep, M meditate (Royalty). 24 of them, hour 0 first."
              " There is no R.")
        print("  the watch tab (the Work/Schedule/Assign tab a player would"
              " use) is SKIPPED by default while the clock is running -- it"
              " hides the colony for ~%d s per write. --watch shows it anyway."
              % WATCH_SECONDS)
        return 1
    name, rest = argv[0], argv[1:]

    kw = {}
    for flag, key in SET_FLAGS:
        if flag in rest:
            i = rest.index(flag)
            if i + 1 >= len(rest):
                print("pawns.py set: %s needs a value" % flag)
                return 1
            kw[key] = rest[i + 1]
    if not kw:
        print("pawns.py set: no field given -- nothing would change. "
              "`pawns.py --work --settings --schedule` reads them first.")
        return 1

    rim.init()

    if "work" in kw:
        # Labels -> defNames before the write. A name the static table misses is
        # re-checked against the running game, then refused here with the valid
        # names rather than sent for the bridge to reject.
        spec, unknown, lookup = resolve_work(kw["work"])
        if unknown:
            spec, unknown, lookup = resolve_work(kw["work"], live_work_types(name))
        if unknown:
            print("pawns.py set: no work type named %s"
                  % ", ".join(repr(u) for u in unknown))
            print("  names (label or defName, case and spaces ignored): %s"
                  % ", ".join(sorted(set(lookup.values()))))
            return 1
        kw["work"] = spec

    watch, why = watch_choice(do, on=watch_on, off=watch_off)
    print_config(config(name, do=do, watch=watch, **kw), watch_why=why)
    return 0


def main():
    argv = sys.argv[1:]

    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0

    def opt(flag, default=None):
        return argv[argv.index(flag) + 1] if flag in argv and len(argv) > argv.index(flag) + 1 else default

    try:
        if argv and argv[0] == "set":
            return main_set(argv[1:])

        rim.init()
        # A bare word is `--name`; it narrows every view below, not just the
        # plain list.
        name = opt("--name") or _positional_name(argv)

        if "--threats" in argv:
            hos, hunt, close = threats(int(opt("--near", 30)))
            where = colonist_positions(hos + hunt + close)
            if hos:
                for p in hos:
                    print("!!HOSTILE:", line(p, where))
            if hunt:
                for p in hunt:
                    print("!!HUNTING:", line(p, where))
            for p in close:
                print("   near   :", line(p, where))
            if not (hos or hunt):
                print("no hostile pawn anywhere on the map; %d non-colonist "
                      "pawn(s) within %s cells" % (len(close), opt("--near", 30)))
            return 0

        if "--roster" in argv and not any(flag in argv for flag, _ in
                                            (("--health", "health"), ("--needs", "needs"),
                                             ("--gear", "gear"), ("--bio", "bio"),
                                             ("--thoughts", "thoughts"), ("--work", "work"),
                                             ("--schedule", "schedule"), ("--settings", "settings"),
                                             ("--relations", "relations"), ("--animals", "animals"))):
            rows = all_pawns(bio=True, thoughts=True, equipment=True, needs=True,
                             **({"nameFilter": name} if name else {}))
            if name and not any(r.get("isColonist") for r in rows):
                print_name_miss(name)
                return 0
            print_roster(rows)
            return 0

        # One call, one flag per block of `home/list_pawns`.
        hidden = "--hidden" in argv
        want = set()
        for flag, block in (("--health", "health"), ("--needs", "needs"),
                            ("--gear", "gear"), ("--bio", "bio"),
                            ("--thoughts", "thoughts"), ("--work", "work"),
                            ("--schedule", "schedule"), ("--settings", "settings"),
                            ("--relations", "relations"), ("--animals", "animals")):
            if flag in argv:
                want.add(block)
        if hidden:
            want.add("health")          # --hidden is a health-block filter
        roster_requested = "--roster" in argv
        everyone = "--all" in argv or "--hostile" in argv

        kw = {}
        # The bridge-side narrowings. Each one widens past living colonists
        # on its own: a prisoner or mechanoid question narrowed down to the
        # colonists answers itself with silence, the same trap --animals has.
        narrowed = []
        for flag, key in FILTER_FLAGS:
            if flag in argv:
                kw[key] = True
                narrowed.append(flag)
        if narrowed:
            everyone = True
        if "--hostile" in argv:
            kw["hostileOnly"] = True
        # --all is a presentation widening, not a competing bridge filter.
        # Explicitly preserve colonists as well: this makes --hostile and
        # --hostile --all issue the same server query.
        if "--all" in argv:
            kw["includeColonists"] = True
        if "--near" in argv:
            kw["withinOfColonists"] = int(opt("--near", 30))
            if want:
                # The blocks are colonist questions; a radius filter that drops
                # the colonists themselves would answer every one with silence.
                kw["includeColonists"] = True
        if "health" in want:
            kw["health"] = True
            kw["visibleHediffsOnly"] = not hidden
        if "needs" in want:
            kw["needs"] = True
        if "gear" in want:
            kw["equipment"] = True
        if "bio" in want:
            kw["bio"] = True
        if "thoughts" in want:
            kw["thoughts"] = True
        if roster_requested:
            # Reuse this same bridge read for the roster and requested detail.
            kw.update(bio=True, thoughts=True, equipment=True, needs=True)
        for block in ("work", "schedule", "settings", "relations", "animals"):
            if block in want:
                kw[block] = True
        if name:
            # Matched server-side, so the reply arrives narrowed rather than
            # being sieved here after the whole map was serialised.
            kw["nameFilter"] = name
            if not narrowed:
                # A name WIDENS. `--name cougar --health` narrowed to colonists
                # prints nothing about the cougar; a name is already the filter,
                # and the only thing a second one can do here is hide the answer.
                # An explicit narrowing flag is a deliberate second filter and
                # keeps its meaning.
                everyone = True
        if "animals" in want:
            # master is an Animals-tab field that lives in settings{} in the
            # payload -- one field, one place. Asked for here so the animal line
            # can print it, and said out loud in the docstring.
            kw["settings"] = True

        ps = all_pawns(**kw)
        # Every row the bridge sent, BEFORE anything here narrows it. The
        # animal footer prints these so a missing animal shows up as a number
        # that does not add up rather than as silence.
        where = colonist_positions(ps)
        accounting = {"returned": len(ps), "dead": 0, "notAnimal": 0,
                      "nameFilter": kw.get("nameFilter")}
        if want:
            # A block is a colony question: living colonists unless told wider.
            # --animals is the exception: it is an ANIMAL question, and narrowing
            # it to colonists would answer it with silence every time. When both
            # kinds of block are asked for, animals win the narrowing and the
            # colonist blocks print for those animals.
            keep = [p for p in ps if not p.get("dead")]
            accounting["dead"] = len(ps) - len(keep)
            ps = keep
            if not everyone:
                keep = [p for p in ps
                        if (p.get("animal") if "animals" in want else p.get("isColonist"))]
                accounting["notAnimal"] = len(ps) - len(keep)
                ps = keep
        if name:
            print("(--name %r, matched by the bridge: %d pawn(s)%s)"
                  % (name, len(ps),
                     "" if narrowed else " -- a name searches every pawn, not "
                     "just colonists"))
        if narrowed:
            print("(bridge filters: %s)" % " ".join(narrowed))
        if "--json" in argv:
            print(json.dumps(ps, indent=1))
            return 0

        if want:
            if not ps:
                if name:
                    print_name_miss(name)
                    return 0
                kind = ("pawns" if everyone
                        else ("animals" if "animals" in want else "colonists"))
                print("no living %s matched -- this is a filter, not an empty "
                      "map. `--all` covers every living pawn." % kind)
                return 0
            if "--roster" in argv:
                print_roster(ps)
                print("")
            print_detail(ps, want, everyone, hidden, where, accounting)
            return 0

        if name and not ps:
            print_name_miss(name)
            return 0
        for p in sorted(ps, key=lambda p: (not p.get("isColonist"),
                                           distance_key(p))):
            print(line(p, where))
        print("-- %d pawns, %d hostile" % (len(ps), sum(1 for p in ps if p.get("hostile"))))
        return 0
    except Exception as e:
        print("pawns.py FAILED: %s: %s" % (type(e).__name__, e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
