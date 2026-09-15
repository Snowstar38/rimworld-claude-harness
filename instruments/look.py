"""One Lookout pass: two screenshots, downscaled, handed to Luna with no context.

  python look.py                 # near frame + wide frame, ask Luna, record the leads
  python look.py --out <path>    # also write the report to <path>
  python look.py --shot <png>    # re-ask about an existing image (no game needed)
  python look.py --no-wide       # near frame only (rota does this on alternate passes)
  python look.py --who sol       # Sol reads this pass instead of Luna
  python look.py --glide         # 5s stepped zoom out and back -- STROBES, see cam.py
  python look.py --no-verify     # skip the read-only lead checks

Luna (gpt-5.6) is a different model looking at a picture, so she is wrong in
*different* ways than the Core, the Hands and the Scouts are. That uncorrelated
perspective is what this channel is for, and it is why it must not be made of
errata.

## Two readers

`--who sol` sends the same picture and the same prompt to Sol -- the same codex
binary on `gpt-5.6-sol`, which is what run-sol.ps1 runs Sol on -- instead
of to Luna. rota.py alternates the two, so half the passes are read by a model
that is wrong in different ways again. The only permitted difference is the model
and one leading sentence naming the reader: that is identity, not context, and
the blind rule below binds both readers equally.

## BLIND ON PURPOSE

The prompt tells Luna nothing about the colony: no names, no story, not even
that a pantry exists. **Scouts are seeded, Lookouts are blind.** Do not
"improve" the prompt by adding context; if you want a briefed reader, dispatch a
Scout, which is what rota.py's briefs are for.

Blind to our narrative, not to the screen: she will name colonists, having read
the label RimWorld draws over the pawn. That is not a leak of the split.

## What the prompt does and does not ask for

- **Fire is asked for, not banned.** `verify.check_fire` resolves it against the
  game's own authoritative alert, so a lit campfire costs one `NO FIRE` line
  instead of a turn. **A prompt-level ban and a verifier are alternatives, not
  layers** -- add a check, delete the ban.
- **The side panels stay readable.** A stale panel is our bug, not Luna's.
- **Never a coordinate.** Excellent on salience, off by 92 pixels on a click
  point. Leads come back as "an animal is standing among the sleeping colonists"
  and one targeted bridge read finds out where.
- **Panels are not the alert list.** Alerts are the right-edge column; the
  top-right "Learning helper" panel is tutorial topics and is never a lead.
- **Posture is not a diagnosis.** Only `pawns.py --health` says downed, and
  full shelves are not clutter -- both are banned as inferences from pixels.
- **Never a name against an alert.** Nothing in this stack joins alerts to
  pawns, so such a join can only have happened inside the photograph, between
  the alert panel on the right and a name label over a pawn.
  `python pawns.py --health` is the one bridge call that says who has which
  condition, and `verify.py` says so under any lead that makes the same join.

## Creature claims are grounded before they are printed

The prompt allows a named thing only where the screen carries its own label;
anything read off shape or colour comes back as `unverified: ...`. On top of
that, every LEAD or WEIRD line that claims a creature is checked here against
the `home/list_pawns` payload `verify.survey()` already holds -- no extra bridge
call, so the pass costs the same -- and gets one line under it when the data
does not carry it: a species no pawn on the map has, an insect with no
insectoid anywhere, or "beside" a colonist when the nearest animal is 70 cells
away. **The lead is never deleted**; it is printed with what the data says
under it, and the block ends with how many claims the payload matched.

This is the one claim worth the extra rule: shape and colour resolve into an
animal far more readily than an animal is actually there, and a named one reads
as a sighting rather than as a guess.

## About one false lead per run is the deal, not a defect

Do not filter them out: the lead that never fires is the lead you stopped
reading.

## Two frames

The near frame is wherever the camera already is, ~7% of the map. The wide frame
is the whole map, and the camera comes down on the base afterwards rather than
going back -- `cam.py` and `camlock.py` own that, including the rule that a
Hands claim beats the Lookout instantly. Both frames go to Luna at the same
time, so a pass costs max(near, wide), not the sum.

## verify.py

**It does not brief Luna.** The survey runs while she looks; every verdict is
computed after her report is written. **A failed check never deletes a lead**:
the lead prints in full with the failure under it. `--no-verify` is for
debugging the prompt, not for quieter reports.

## Runs headless

rota.py spawns this detached, with no console and nobody watching stderr. So
every failure -- no bridge, no game, Luna timing out, Codex missing -- is
written into `state\\lookout-latest.txt` in the same shape as a success.
"""
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import game_session

REPORT_CONTEXT = None
REPORT_DATE = None
REPORT_EVENT_ID = None
REPORT_SESSION = None

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
SHOTS = os.path.join(STATE, "shots")
LATEST = os.path.join(STATE, "lookout-latest.txt")

# Soft import. look.py predates verify.py and its contract -- a screenshot, a
# blind read, a line somebody sees -- does not depend on it. If verify.py is
# broken or gone, the leads still go out, and the report says they are
# unchecked rather than implying they passed.
try:
    import verify
    VERIFY_ERR = ""
except Exception as _e:                # noqa: BLE001 -- any import failure at all
    verify, VERIFY_ERR = None, "%s: %s" % (type(_e).__name__, str(_e)[:100])

# Same soft-import rule for the camera; cam.py owns the number.
try:
    from cam import GLIDE_SECONDS
except Exception:
    GLIDE_SECONDS = 5.0

CODEX = r"C:\Home\tools\codex\codex.exe"
MODEL = "gpt-5.6-luna"
SOL_MODEL = "gpt-5.6-sol"
TIMEOUT = 120

# The two readers. Both pin their model. "sol" used to pass no --model and take
# codex's default, on the grounds that the default was the model run-sol.ps1
# runs Sol on -- that stopped being true on 2026-09-05, when GPT-6-Astra shipped
# and became the default, so the sol reader was silently about to become a third
# model. Same intent, now stated: gpt-5.6-sol. Nothing else about the call may
# differ -- the point of a second reader is a second uncorrelated read of the
# same question.
READERS = ("luna", "sol")
SOL_LEAD = "You are Sol. This is one Lookout pass over a live RimWorld stream. "

# CREATE_NO_WINDOW. M, Aug 31: *"codex popup windows were minimizing the
# game if we can figure out how to handle that"*.
#
# codex.exe is a console program. rota.py spawns look.py DETACHED, which means
# look.py has NO console of its own -- so when it launched codex, Windows
# allocated codex a brand new console window, that window took the foreground,
# and a fullscreen RimWorld minimised itself. Once every 150 seconds, all
# session. It never showed up in hand testing because a look.py run from a
# terminal already HAS a console for codex to inherit, so no window is created
# and nothing steals focus -- the bug only exists on the path nobody watches,
# which is the path the rota actually uses.
#
# CREATE_NO_WINDOW gives the child a console it cannot show. stdout/stderr are
# already piped, so nothing is lost by not having one to draw.
NO_WINDOW = 0x08000000
# The survey overlaps Luna and normally lands first. This is the ceiling on how
# long we will hold a finished report waiting for it: a hung bridge must not be
# able to retire the Lookout, so past this the leads go out marked UNVERIFIED
# rather than not going out at all.
VERIFY_WAIT = 45

# 4096x2160 native. Luna gets ~1024px wide, which lands in the 350-1500 input
# token band the design doc budgets for the Lookout (a full grid read is 6-10K).
# Wider buys nothing: this channel is asked for salience, not for legibility of
# a stack label, and the whole argument for it is that it is cheap enough to run
# every couple of minutes forever.
TARGET_W = 1024

PROMPT = (
    "This is a RimWorld colony screenshot. Report 0-6 short LEAD lines: "
    "anything dangerous, odd, out of place, or worth a closer look "
    "(animals near pawns, breaches, fire or smoke, items piling up, pawns in "
    "strange places, things that look broken or unfinished). "
    "One line each, prefixed 'LEAD:'. Do not give pixel or cell coordinates. "
    "The ALERTS are the column down the RIGHT EDGE of the screen: each one "
    "names a CONDITION, not a person -- do not attach a colonist's name to an "
    "alert, say what the alert says. The panel in the TOP-RIGHT headed "
    "'Learning helper' is a list of tutorial topics ('Spoilage and freezers', "
    "'Forbidding doors', 'Allowed areas'). It is NOT the alert list and nothing "
    "in it is ever a lead. "
    "Letters and notices may be hours old: their wording is historical, not proof "
    "of a current attack. Distinguish visible evidence from inference. "
    "Name a creature, building or item only when its own on-screen label says so; "
    "anything you are inferring from shape, colour or silhouette goes as "
    "'unverified: <what you actually see>', never as a named thing. "
    "Never call a pawn collapsed, downed or unconscious: lying, kneeling and "
    "crawling are ordinary work postures and only a health read tells them "
    "apart -- describe the posture and say it is a posture. "
    "Full shelves are what a working store room looks like: do not report a "
    "store room as cluttered, messy or unfinished unless items are lying on "
    "open floor outside any storage area. "
    "Do not fill a quota: zero leads is the normal outcome, and is valid on a "
    "quiet or unreadable scene. Dark lighting alone does not establish smoke; "
    "an animal's presence does not establish hostility. State uncertainty when "
    "the pixels cannot resolve it. "
    "End with a mandatory line 'WEIRD: <the single strangest thing>' or "
    "'WEIRD: nothing unusual seen'."
)

# Who wrote which line, and what has to happen before a lead is acted on.
LEAD_RULES = (
    "SEEN: every LEAD/WEIRD line below is %s's inference from the screenshot.\n"
    "EVIDENCE: the '->' lines under them are instrument reads (verify.py).\n"
    "Check every lead against `python alerts.py`, `python pawns.py --health` or "
    "`python status.py --brief` before acting on it or saying it on stream, and "
    "drop any lead the instrument contradicts.")

# The wide frame is the same question about a different picture, and it says so
# -- Luna is told this one is zoomed out so she does not report the whole colony
# as "small and far away". Still no colony knowledge: what the camera is doing is
# a property of the photograph, not of the story. [M] seeding split intact.
WIDE_PROMPT = PROMPT.replace(
    "This is a RimWorld colony screenshot.",
    "This is a RimWorld colony screenshot, zoomed out to show most or all of the "
    "map, so everything in it is small. Look at the whole map, especially the "
    "parts far from the buildings.")


def stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def record(text, out=None):
    """Write the report where rota.py will find it, then to stdout.

    Overwrite, not append: the file is a mailbox with one slot. rota.py reads it,
    prints it into the Core's context once, and marks it delivered by mtime --
    a growing log would either be re-delivered or need parsing, and neither is
    worth a line of the Core's context.
    """
    text = game_session.stamp(text, REPORT_CONTEXT)
    if REPORT_EVENT_ID:
        import runtime_binding
        session_changed = runtime_binding.load().get("session_id") != REPORT_SESSION
        if session_changed:
            text += "\nEVENT DISCARDED: capturing session ended before review delivery"
        try:
            import event_bus
            result = ({"accepted": False, "reason": "stale session"} if session_changed else
                      event_bus.publish(kind="review", body=text, event_id=REPORT_EVENT_ID,
                                       source="rota-reviewer",
                                       captured_at=(REPORT_CONTEXT or {}).get("capturedAt"),
                                       generation=(REPORT_CONTEXT or {}).get("generation")))
            if not result.get("accepted") and not session_changed:
                import rota_service
                rota_service._queue_publish(
                    REPORT_EVENT_ID, text,
                    captured_at=(REPORT_CONTEXT or {}).get("capturedAt"),
                    generation=(REPORT_CONTEXT or {}).get("generation"))
        except Exception as ex:
            text += "\nEVENT DELIVERY FAILED: %s: %s" % (type(ex).__name__, ex)
            try:
                import rota_service
                if not session_changed:
                    rota_service._queue_publish(
                    REPORT_EVENT_ID, text,
                    captured_at=(REPORT_CONTEXT or {}).get("capturedAt"),
                    generation=(REPORT_CONTEXT or {}).get("generation"))
            except Exception:
                pass
    os.makedirs(STATE, exist_ok=True)
    if not REPORT_EVENT_ID:
        try:
            with open(LATEST, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass
    if out:
        try:
            with open(out, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass
    try:
        # Luna writes curly quotes. A Windows console is cp1252, so a plain print
        # either mojibakes them or raises UnicodeEncodeError and loses the whole
        # report -- which, detached, would look like the Lookout having nothing
        # to say. Reconfigure once, and fall back to ASCII rather than to silence.
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    except UnicodeEncodeError:
        sys.stdout.write(text.encode("ascii", "replace").decode("ascii") + "\n")
    except Exception:
        pass       # detached: there may be no stdout at all. The file is the record.


def game_clock():
    """-> (tick, hour) from `home/get_time`, (None, None) if it doesn't answer.

    RimWorld's own clock, computed from absolute ticks at the map's longitude:
    arithmetic on `ticksGame` alone has neither offset and lands hours out.
    `hour` is None with no map, where the game has no calendar either. Never
    fatal: a Lookout pass is worth having with the bridge down -- the screen is
    still the screen -- so this is a garnish on the report, not a precondition.
    """
    global REPORT_DATE
    try:
        import rim
        rim.init()
        g = rim.game("home/get_time", {}, strict=False)
        if isinstance(g, dict) and g.get("ticksGame") is not None:
            REPORT_DATE = g.get("dateFull")
            return g["ticksGame"], g.get("hourInteger")
    except Exception:
        pass
    return None, None


def downscale(src, name):
    """4K original -> our own downscaled copy, and DELETE the original.

    `take_screenshot` writes a ~14 MB 4K PNG into the shared profile's
    Screenshots folder. At one look every 2.5 minutes that folder would grow by
    350 MB an hour, and 197 MB in one evening is already why it is in
    .gitignore. Two shots a pass makes that 700 MB, so this is not optional.
    """
    from PIL import Image
    os.makedirs(SHOTS, exist_ok=True)
    dst = os.path.join(SHOTS, name + ".png")
    with Image.open(src) as im:
        w, h = im.size
        small = im.resize((TARGET_W, max(1, round(h * TARGET_W / w))), Image.LANCZOS)
        small.save(dst, optimize=True)
    try:
        os.remove(src)
    except OSError:
        pass
    return dst, (w, h)


def capture():
    """The near frame: whatever the camera is already looking at. Moves nothing."""
    import cam
    import rim
    rim.init()
    name = "look-" + time.strftime("%Y%m%d-%H%M%S")
    return downscale(cam.shoot(name), name)


def wide_capture(glide_seconds, name):
    """The wide frame, then the camera comes down on the base. -> (path, note).

    Runs while Luna is already looking at the near frame, so it costs almost no
    wall clock. Everything fails toward "no wide frame" rather than toward a
    camera left somewhere strange; three outcomes are worth telling apart in the
    report: **left alone** (a Hands lease is live), **interrupted** (Hands
    claimed it mid-glide -- camera is where Hands wants it, frame lost, cheap),
    and **landed** (camera on the base, notice queued for Hands).
    """
    try:
        import cam
    except Exception as e:
        return None, "wide frame skipped: %s" % str(e)[:100]
    r = cam.wide_and_home(name, glide_seconds=glide_seconds)
    if not r["path"]:
        return None, "wide frame skipped: %s" % (r["note"] or "no image came back")
    try:
        path, _ = downscale(r["path"], name)
    except Exception as e:
        return None, "wide frame captured but could not be downscaled (%s)" % str(e)[:80]
    frac = r["coverage"][0] if r["coverage"] else None
    note = "wide frame: %s of the map%s" % (
        "%.0f%%" % (frac * 100) if frac is not None else "an unknown share",
        " (%s)" % r["coverage"][1] if r["coverage"] else "")
    if r["note"]:
        note += " -- " + r["note"]
    return path, note


def start_luna(image, prompt=PROMPT, who="luna"):
    """Launch one Codex look and return at once. -> (proc, outfile, who).

    Non-blocking so both frames are looked at simultaneously: a pass costs
    max(near, wide), not the sum.

    Free text, no output schema. The format IS the prompt -- LEAD lines plus a
    mandatory WEIRD line -- because a schema would let Luna satisfy the shape
    while saying nothing, and forcing a sentence out of a model that saw nothing
    is how you find out whether it looked.
    """
    if not os.path.isfile(CODEX):
        raise RuntimeError("codex not found at %s" % CODEX)
    fd, outp = tempfile.mkstemp(prefix="rim-look-", suffix=".txt")
    os.close(fd)
    cmd = [CODEX, "exec", "--ephemeral", "--ignore-rules",
           "--sandbox", "read-only"]
    if who == "sol":
        # The identity sentence is the whole of what Sol is told that Luna is not.
        prompt = SOL_LEAD + prompt
        cmd += ["--model", SOL_MODEL]
    else:
        cmd += ["--model", MODEL]
    cmd += ["--image", image, "--output-last-message", outp,
            "--color", "never", prompt]
    p = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                         text=True, encoding="utf-8", errors="replace",
                         creationflags=NO_WINDOW)
    return p, outp, who


def collect_luna(started, timeout=TIMEOUT):
    """Wait for one look and read its answer. Raises with a usable message."""
    p, outp, who = started
    try:
        try:
            _, err = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
            raise RuntimeError("codex/%s did not answer in %ds" % (who, timeout))
        if p.returncode:
            tail = (err or "").strip().splitlines()
            raise RuntimeError("codex/%s exit %d: %s"
                               % (who, p.returncode, tail[-1] if tail else "no output"))
        with open(outp, "r", encoding="utf-8") as f:
            return f.read().strip()
    finally:
        try:
            os.unlink(outp)
        except OSError:
            pass


def ask_luna(image, timeout=TIMEOUT, prompt=PROMPT, who="luna"):
    """Blocking single look. Kept for `--shot` and for anything outside this file."""
    return collect_luna(start_luna(image, prompt, who), timeout)


def check_format(text):
    """Did Luna honour the contract? Say so in the report rather than silently not.

    A Lookout that quietly stopped producing WEIRD lines looks exactly like a
    Lookout with nothing to report, which is the failure this role cannot afford.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    leads = [l for l in lines if l.upper().startswith("LEAD:")]
    weird = [l for l in lines if l.upper().startswith("WEIRD:")]
    problems = []
    if not 0 <= len(leads) <= 6:
        problems.append("%d LEAD lines (asked for 0-6)" % len(leads))
    if not weird:
        problems.append("no WEIRD line (it is mandatory)")
    return leads, weird, problems


def add_verdicts(body, thread, box, no_verify, who="luna"):
    """Luna's lines, each with what the game's own data says about it.

    Every branch here still returns the body. A verifier that could swallow the
    report on its way past would be strictly worse than no verifier -- the
    Lookout's one job is that a look always produces a line somebody reads.
    """
    if not body:
        return body                    # a format failure already prints raw
    if verify is None:
        return body + "\nverify: verify.py did not import (%s) -- leads unchecked." % VERIFY_ERR
    if no_verify:
        return body + "\nverify: SKIPPED (--no-verify) -- no lead below was checked."
    if thread is not None:
        thread.join(timeout=VERIFY_WAIT)
    s = box.get("s")
    if s is None:
        s = verify._blank("the survey did not finish in %ds" % VERIFY_WAIT)
    try:
        return verify.annotate(body, s)
    except Exception as e:
        return body + "\nverify: FAILED while annotating (%s: %s) -- leads above " \
                      "are %s's, unchecked." % (type(e).__name__, str(e)[:100], who)


# ---------------------------------------------------------- grounding leads
#
# A creature is the Lookout's worst channel: shape and colour resolve into an
# animal on screen far more readily than an animal is actually there, and a
# named one ("a large animal indoors beside Octave") reads as a sighting. So
# every creature claim is checked against the pawn payload verify.survey()
# already holds -- no extra bridge call -- and anything the payload does not
# carry gets one line saying so. Nothing is ever deleted: the lead stands, with
# what the data says under it.

CREATURE_WORDS = ("animal", "creature", "beast", "wildlife", "insect", "bug",
                  "spider", "critter", "livestock", "herd", "predator",
                  "vermin", "rodent")

# "beside", not "on the map": the claim these check is proximity.
NEAR_WORDS = ("beside", "next to", "nearby", "near ", "among", "amongst",
              "indoors", "inside", "in the room", "close to", "adjacent",
              "standing over", "at the door")
NEAR_CELLS = 10

INSECT_WORDS = ("insect", "bug", "spider", "hive", "megaspider", "megascarab",
                "spelopede")
INSECT_DEFS = ("megaspider", "megascarab", "spelopede", "insect")

# The species a lead has an on-screen label for. A word from this list that no
# pawn on the map carries is a named animal that is not there.
SPECIES = ("bear", "grizzly", "wolf", "warg", "cougar", "panther", "lynx",
           "boar", "muffalo", "alpaca", "dromedary", "camel", "elephant",
           "rhinoceros", "deer", "elk", "caribou", "ibex", "gazelle", "hare",
           "squirrel", "rat", "boomrat", "boomalope", "tortoise", "cassowary",
           "emu", "ostrich", "chicken", "cow", "pig", "horse", "donkey",
           "goat", "sheep", "yak", "thrumbo", "cobra", "fox", "monkey",
           "chinchilla", "capybara", "megaspider", "megascarab", "spelopede")

PAWNS_TOOL = "home/list_pawns"


def _is_animal(p):
    """The row's own flag, or 'not a person' when the DLL predates it."""
    if "animal" in p:
        return bool(p["animal"])
    return not (p.get("humanlike") or p.get("isColonist"))


def _pawn_words(p):
    return " ".join(str(p.get(k) or "")
                    for k in ("name", "defName", "kindDef")).lower()


def _kinds(beasts, cap=4):
    seen = []
    for p in beasts:
        k = p.get("defName") or p.get("kindDef") or "?"
        if k not in seen:
            seen.append(k)
    return ", ".join(seen[:cap]) + (" +%d" % (len(seen) - cap)
                                    if len(seen) > cap else "")


def ground_lead(lead, pawns):
    """One `unverified` line for a creature claim the payload does not carry.

    -> None when the lead makes no creature claim, or when the data supports it.
    """
    low = lead.lower()
    if not any(w in low for w in CREATURE_WORDS + SPECIES):
        return None
    beasts = [p for p in (pawns or [])
              if not p.get("dead") and not p.get("isColonist") and _is_animal(p)]
    if not beasts:
        return ("    -> unverified: %s carries no animal on this map at all, so "
                "nothing on screen is one." % PAWNS_TOOL)
    pool = " | ".join(_pawn_words(p) for p in beasts)
    named = [s for s in SPECIES
             if re.search(r"\b%s\b" % re.escape(s), low) and s not in pool]
    if named:
        return ("    -> unverified: this names %s; %s has no such pawn -- the "
                "%d animal(s) on the map are %s. Do not name it on air."
                % (", ".join(named), PAWNS_TOOL, len(beasts), _kinds(beasts)))
    if any(w in low for w in INSECT_WORDS) and not any(
            any(i in _pawn_words(p) for i in INSECT_DEFS) for p in beasts):
        return ("    -> unverified: not one insect in %s -- the %d animal(s) on "
                "the map are %s." % (PAWNS_TOOL, len(beasts), _kinds(beasts)))
    if any(w in low for w in NEAR_WORDS):
        dists = [p for p in beasts
                 if p.get("nearestColonistDistance") is not None]
        if dists:
            closest = min(dists, key=lambda p: p["nearestColonistDistance"])
            d = closest["nearestColonistDistance"]
            if d > NEAR_CELLS:
                return ("    -> unverified: the nearest animal to any colonist "
                        "is %s, %d cells away (%s). Nothing is beside anyone."
                        % (closest.get("defName") or closest.get("name") or "?",
                           d, closest.get("nearestColonist") or "?"))
    return None


def ground_creatures(body, pawns):
    """`body` with an `unverified` line under every ungrounded creature claim.

    Costs no bridge call: `pawns` is the payload verify.survey() already read.
    With no payload the leads are untouched and one line says they are unchecked.
    """
    if not body:
        return body
    out, claims, grounded = [], 0, 0
    for raw in body.splitlines():
        out.append(raw)
        up = raw.strip().upper()
        if not (up.startswith("LEAD:") or up.startswith("WEIRD:")):
            continue
        lead = raw.split(":", 1)[1].strip()
        if not lead or "nothing unusual" in lead.lower():
            continue
        if not any(w in lead.lower() for w in CREATURE_WORDS + SPECIES):
            continue
        claims += 1
        if pawns is None:
            continue
        note = ground_lead(lead, pawns)
        if note:
            out.append(note)
        else:
            grounded += 1
    if claims and pawns is None:
        out.append("grounding: no %s payload this pass, so the %d creature "
                   "claim(s) above are unchecked." % (PAWNS_TOOL, claims))
    elif claims:
        out.append("grounding: %d of %d creature claim(s) match a pawn in %s; "
                   "the rest are marked unverified above."
                   % (grounded, claims, PAWNS_TOOL))
    return "\n".join(out)


def frame_block(label, text):
    """One frame's leads under a header. -> (block, raw_problem_note or '').

    The header is an ordinary line, so verify.annotate() carries it through in
    place and the verdicts still land under the right leads. Which frame a lead
    came from matters to whoever reads it: "items piling up" means one thing in
    the frame around the base and another in the frame that can see the whole
    map.
    """
    leads, weird, problems = check_format(text)
    head = "-- %s --" % label
    if problems:
        return "", "!! %s: %s -- raw reply follows\n%s" % (
            label, "; ".join(problems), text)
    return "\n".join([head] + leads + weird), ""


def main():
    global REPORT_CONTEXT, REPORT_EVENT_ID, REPORT_SESSION
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    out = argv[argv.index("--out") + 1] if "--out" in argv and len(argv) > argv.index("--out") + 1 else None
    shot = argv[argv.index("--shot") + 1] if "--shot" in argv and len(argv) > argv.index("--shot") + 1 else None
    who = argv[argv.index("--who") + 1].lower() if "--who" in argv and len(argv) > argv.index("--who") + 1 else "luna"
    REPORT_EVENT_ID = (argv[argv.index("--event-id") + 1]
                       if "--event-id" in argv and len(argv) > argv.index("--event-id") + 1
                       else None)
    if who not in READERS:
        # Fail loudly rather than silently falling back: a typo that quietly
        # sent every pass to the same reader would delete the whole point.
        sys.stderr.write("look.py: --who must be one of %s\n" % ", ".join(READERS))
        return 2

    t0 = time.time()
    no_verify = "--no-verify" in argv
    # The wide frame is on by default -- it is the half of the map nothing else
    # in this stack can see. --no-wide is for debugging and for the --shot path,
    # where there is no live camera to move.
    want_wide = "--no-wide" not in argv and not shot
    glide_s = GLIDE_SECONDS if "--glide" in argv else 0.0
    # The reader is on the header line: which model looked is part of the
    # report, not bookkeeping.
    head = "LOOKOUT %s %s" % (who, stamp())
    try:
        if shot:
            img, native = os.path.abspath(shot), None
        else:
            img, native = capture()
    except Exception as e:
        REPORT_CONTEXT = ({} if shot else dict(game_session.current(), capturedAt=time.time()))
        record("%s\nFAILED at screenshot: %s" % (head, e), out)
        return 1

    tick, hour = game_clock()
    # The evidence becomes an observation when the screenshot exists, not when
    # this process happened to start. game_clock also observess the loaded-game
    # generation before it is stamped onto the report.
    REPORT_CONTEXT = ({} if shot else dict(game_session.current(), capturedAt=time.time()))
    if not shot:
        import runtime_binding
        REPORT_SESSION = runtime_binding.load().get("session_id")

    # Luna starts on the near frame FIRST and keeps looking at it while the
    # camera goes travelling -- M's ordering, and it is what makes the
    # second frame nearly free. Everything below until the join is single
    # threaded against the bridge: rim.py's request-id counter is not thread
    # safe, so exactly one thread may be talking to it, and until the survey
    # starts that thread is this one.
    try:
        near = start_luna(img, PROMPT, who)
    except Exception as e:
        record("%s\nFAILED at %s: %s" % (head, who, e), out)
        return 1

    wide_img, wide_note, wide = None, "", None
    if want_wide:
        wide_img, wide_note = wide_capture(glide_s, "wide-" + time.strftime("%Y%m%d-%H%M%S"))
        if wide_img:
            try:
                wide = start_luna(wide_img, WIDE_PROMPT, who)
            except Exception as e:
                wide_note += " -- but %s could not be started on it (%s)" % (who, str(e)[:80])

    box = {}
    survey_thread = None
    if verify is not None and not no_verify:
        def _run():
            try:
                box["s"] = verify.survey()
            except Exception as e:                       # belt and braces: survey()
                box["s"] = verify._blank(                # already swallows its own
                    "%s: %s" % (type(e).__name__, str(e)[:120]))
        survey_thread = threading.Thread(target=_run, daemon=True)
        survey_thread.start()

    if tick is not None:
        # The hour is the game's own (`home/get_time`); the day is the colony's
        # age in days, which is what the chronicle and every other instrument
        # count in.
        head += "  tick %d (%s%s)" % (
            tick, REPORT_DATE or "calendar unavailable",
            ", %02dh" % hour if hour is not None else "")
    try:
        from PIL import Image
        with Image.open(img) as im:
            size = "%dx%d" % im.size
    except Exception:
        size = "?"
    head += "\nshot %s  %s%s  %.0f KB" % (
        os.path.basename(img), size,
        " (from %dx%d)" % native if native else "",
        os.path.getsize(img) / 1024.0 if os.path.exists(img) else 0)
    if wide_note:
        head += "\n" + wide_note

    try:
        near_text = collect_luna(near)
    except Exception as e:
        record("%s\nFAILED at %s: %s" % (head, who, e), out)
        return 1
    wide_text = ""
    if wide:
        try:
            wide_text = collect_luna(wide)
        except Exception as e:
            # A wide frame that failed must never cost the near frame's leads.
            head += "\nwide frame: %s failed on it (%s) -- near frame below is intact" % (who, str(e)[:90])

    blocks, raw = [], []
    for label, text in (("near frame, where the camera already was", near_text),
                        ("wide frame", wide_text)):
        if not text:
            continue
        block, problem = frame_block(label, text)
        (blocks if block else raw).append(block or problem)

    tail = "\n(%.0fs)" % (time.time() - t0)
    body = "\n".join(blocks)
    if raw:
        tail = "\n" + "\n".join(raw) + tail
    body = add_verdicts(body, survey_thread, box, no_verify, who)
    # The survey is joined by add_verdicts, so its pawn payload is in hand and
    # this costs no bridge call.
    body = ground_creatures(body, (box.get("s") or {}).get("pawns"))
    record("%s\n%s\n%s%s" % (head, LEAD_RULES % who, body, tail), out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
