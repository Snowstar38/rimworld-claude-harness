"""Luna: the net under errata's turn summary. Condenses it to fit the card.

  python luna.py condense --file some.txt          # condense and print, no post
  python luna.py condense --summary "..." --turn 2 --dry-run   # + show the POST
  python luna.py run state\\luna-job-2-1788.json   # what the detached child runs

The overlay's summary card is a fixed box in the bottom bar, so a long summary
is cut off on screen rather than shrunk. Luna sits behind `stream.py handback`:

    handback posts the FULL summary immediately
    -> if it is over SUMMARY_CAP, it spawns a detached Luna and returns
    -> Luna asks Codex for a <=cap version, then re-posts it with the same
       turn / mood / tick, and the overlay's pin swaps to the fitted text.

**Nothing here is allowed to delay a turn.** `spawn()` writes a small job file
and starts a process; that is the whole of Luna's cost on the fast path. Every
failure -- no Codex, a timeout, an over-cap answer, a dead overlay -- ends the
same way: nothing is posted, and the full summary with its ellipsis stays on
screen. The ellipsis is the fallback. An error on stream never is.

SUMMARY_CAP is imported from stream.py. There is one cap and it lives there.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import overlay_client as ov
from stream import SUMMARY_CAP   # one source of truth -- do not re-declare it

HERE = Path(__file__).resolve().parent
STATE = HERE / "state"
LOG = STATE / "luna.log"

# C:\Home\tools\codex\codex.exe, found by walking out of the room rather than
# spelled out, so a moved house doesn't need a second edit.
CODEX = HERE.parents[1] / "tools" / "codex" / "codex.exe"

# CREATE_NO_WINDOW, and it is load-bearing on a stream. M, Aug 31:
# *"codex popup windows were minimizing the game if we can figure out how to
# handle that"*. The worker below is spawned DETACHED with pythonw, so it has
# NO console of its own -- and codex.exe is a console program, so Windows
# allocates it a brand new console window, that window takes the foreground,
# and a fullscreen RimWorld minimises itself, on camera, on every handback
# whose summary runs long. `look.py:106-120` diagnosed and fixed exactly this;
# luna.py is the newer file and shipped without the flag. It cannot be set on
# the DETACHED parent spawn (CreateProcess rejects the pair -- see `_spawn`),
# but the codex child takes it freely. stdout is already piped, so nothing is
# lost by not having a console to draw.
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

AIM_LOW, AIM_HIGH = 220, 300   # what we ask for; SUMMARY_CAP is what we enforce
CODEX_TIMEOUT = 120.0          # generous: a turn is minutes long and nobody waits
ATTEMPTS = 2                   # one try, then one retry on an over-cap answer
NET_TIMEOUT = 3.0              # talking to the overlay, not to a model


PROMPT = """You are Luna. errata, an AI playing RimWorld on a live stream, wrote the turn \
summary below. It is {n} characters and the overlay's summary card holds {cap}, \
so the tail is being cut off on screen. Condense it.

Rules:
- Keep errata's voice: first person, concrete, a story and not a changelog. \
Name colonists rather than roles. End somewhere a viewer feels the shape of \
the story.
- Keep what matters: where we are, the stake, one thread still hanging. Cut \
detail the feed already carried.
- Target {low} to {high} characters. Never exceed {cap} characters.{extra}
- Reply with the condensed summary and nothing else. No preamble, no quotes \
around it, no markdown, no explanation.

SUMMARY:
{summary}"""

RETRY_EXTRA = ("\n- Your previous answer was {was} characters, which is too long. "
               "This one must be shorter than {cap}.")


def log(action, detail=""):
    """One line per thing Luna did. Same shape as state\\overlay-drops.log."""
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write("%s  %-12s %s\n"
                    % (time.strftime("%Y-%m-%d %H:%M:%S"), action, detail))
    except Exception:
        pass


# --- the fast path: what stream.py calls -------------------------------------

def _pythonw():
    """pythonw.exe if it is beside the running python, so no console flashes.

    A black window popping up on M's screen every long handback would be
    a worse bug than the ellipsis Luna exists to remove.
    """
    try:
        w = Path(sys.executable).with_name("pythonw.exe")
        if w.exists():
            return str(w)
    except Exception:
        pass
    return sys.executable


def spawn(summary, turn, mood=None, dry_run=False):
    """Hand an over-long summary to a detached Luna. Returns at once, never raises.

    The summary travels in a job file rather than on the command line: paths
    here have spaces in them, summaries have quotes and dashes in them, and the
    house rule is to write a file rather than build a command string.

    `dry_run` runs the whole detached path -- job file, process, Codex call,
    staleness check -- and logs what it would have posted instead of posting it.
    That is how this is tested against a live stream without touching it.
    """
    try:
        summary = summary or ""
        if len(summary) <= SUMMARY_CAP:
            return None
        STATE.mkdir(parents=True, exist_ok=True)
        job = STATE / ("luna-job-%s-%d.json" % (turn, int(time.time() * 1000)))
        job.write_text(json.dumps({"summary": summary, "turn": turn, "mood": mood},
                                  ensure_ascii=False), encoding="utf-8")
        flags = 0
        if os.name == "nt":
            # DETACHED_PROCESS so the child outlives this CLI and owns no
            # console; CREATE_NEW_PROCESS_GROUP so a Ctrl-C in the play session
            # does not travel to it. (Not CREATE_NO_WINDOW -- it and
            # DETACHED_PROCESS are both console flags and CreateProcess
            # rejects the pair. pythonw.exe is what keeps it silent.)
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        argv = [_pythonw(), str(HERE / "luna.py"), "run", str(job)]
        if dry_run:
            argv.append("--dry-run")
        subprocess.Popen(
            argv, cwd=str(HERE), creationflags=flags, close_fds=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        log("spawn", "turn %s, %d chars (cap %d) -> %s%s"
            % (turn, len(summary), SUMMARY_CAP, job.name,
               " [dry-run]" if dry_run else ""))
        return job
    except Exception as e:
        # Losing the condensation is a cosmetic loss. Losing the turn is not.
        log("spawn-failed", "%s: %s" % (type(e).__name__, e))
        return None


# --- the slow path: what the detached child does -----------------------------

# errata types ASCII -- straight quotes, `--` for an em dash. The model does
# not, and a card that suddenly grows curly apostrophes reads as written by
# somebody else, which is exactly what Luna must not look like. Applied before
# the length check, because `--` is one character longer than the dash it
# replaces.
TYPOGRAPHY = {
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2014": " -- ", "\u2013": "-", "\u2026": "...", "\u00a0": " ",
}


def _clean(text):
    """What came back, as one line, in errata's typography, politeness stripped."""
    text = (text or "").strip()
    # A model that ignores "no markdown" usually gives a fenced block first.
    text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text).strip()
    for bad, good in TYPOGRAPHY.items():
        text = text.replace(bad, good)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 1 and text[0] in "\"'" and text[-1] in "\"'":
        text = text[1:-1].strip()
    return text


def ask_codex(summary, extra=""):
    """One Codex call. Returns the cleaned text, or None. Never raises.

    `--ignore-user-config` matters more than it looks: the user config at
    C:\\Users\\catsr\\.codex\\config.toml wires up the GABS MCP server, and a
    Luna that loaded it would boot a second GABS beside the live one on every
    long handback. Luna needs a sentence rewritten, not a game bridge.

    `stdin=DEVNULL` is not optional either. `codex exec` reads stdin even when
    a prompt argument is given ("Reading additional input from stdin..."), so a
    child with an inherited console handle waits for an EOF that never comes.
    Measured, 2026-09-01: that is a hang, not a slow answer.
    """
    if not CODEX.exists():
        log("no-codex", str(CODEX))
        return None
    work = None
    try:
        work = Path(tempfile.mkdtemp(prefix="luna-"))
        out = work / "answer.txt"
        prompt = PROMPT.format(n=len(summary), cap=SUMMARY_CAP, low=AIM_LOW,
                               high=AIM_HIGH, extra=extra, summary=summary)
        argv = [
            str(CODEX), "exec",
            "--ignore-user-config",   # see above: no GABS, no project rules
            "--skip-git-repo-check",  # the temp workdir is not a repo
            "--ephemeral",            # no session file per handback
            "-s", "read-only",        # Luna rewrites a sentence; it needs nothing
            "-C", str(work),
            "-o", str(out),
            prompt,
        ]
        r = subprocess.run(argv, cwd=str(work), timeout=CODEX_TIMEOUT,
                           creationflags=NO_WINDOW,   # see NO_WINDOW above
                           stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if r.returncode != 0:
            tail = (r.stdout or b"").decode("utf-8", "replace").strip()[-200:]
            log("codex-exit", "%s -- %s" % (r.returncode, tail))
            return None
        return _clean(out.read_text(encoding="utf-8"))
    except subprocess.TimeoutExpired:
        log("codex-timeout", "%.0fs" % CODEX_TIMEOUT)
        return None
    except Exception as e:
        log("codex-failed", "%s: %s" % (type(e).__name__, e))
        return None
    finally:
        if work:
            shutil.rmtree(work, ignore_errors=True)


def condense(summary):
    """A <=cap version of `summary`, or None. At most ATTEMPTS Codex calls."""
    summary = (summary or "").strip()
    if not summary:
        return None
    if len(summary) <= SUMMARY_CAP:
        log("skip", "%d chars, already inside the cap" % len(summary))
        return None
    extra = ""
    for attempt in range(1, ATTEMPTS + 1):
        t0 = time.time()
        text = ask_codex(summary, extra)
        dt = time.time() - t0
        if not text:
            log("no-answer", "attempt %d, %.1fs" % (attempt, dt))
            return None
        if len(text) <= SUMMARY_CAP:
            log("condensed", "attempt %d, %d -> %d chars, %.1fs"
                % (attempt, len(summary), len(text), dt))
            return text
        log("over-cap", "attempt %d, %d chars, %.1fs" % (attempt, len(text), dt))
        extra = RETRY_EXTRA.format(was=len(text), cap=SUMMARY_CAP)
    log("gave-up", "%d chars stands as posted" % len(summary))
    return None


def repost(text, turn, mood=None, dry_run=False):
    """Swap the pin to the fitted text -- unless the overlay has moved on.

    The guard is `turn` plus "the pinned summary is still over the cap". Not an
    exact text match: server.py's name bouncer rewrites the string on its way
    into state, so the text we posted and the text it holds are allowed to
    differ. Still-over-cap is the honest question anyway -- it asks whether
    there is anything left to fix, and answers no if a later handback, another
    Luna, or M already put something else on screen.
    """
    state, why = ov.get("/state", timeout=NET_TIMEOUT)
    if state is None:
        log("no-state", str(why))
        return False
    ls = state.get("lastSummary") or {}
    pinned = ls.get("text") or ""
    if str(ls.get("turn")) != str(turn):
        log("stale", "pin is turn %s, we condensed turn %s -- not posting"
            % (ls.get("turn"), turn))
        return False
    if len(pinned) <= SUMMARY_CAP:
        log("stale", "pin for turn %s is already %d chars -- not posting"
            % (turn, len(pinned)))
        return False

    payload = {"kind": "summary", "text": text, "turn": turn}
    if mood in ov.MOODS:
        payload["mood"] = mood
    # The card keeps the moment the turn ended, not the moment Luna finished:
    # re-stamping it with a newer tick would move the summary forward in the
    # colony's day by however long the condensation took.
    if isinstance(ls.get("tick"), int) and not isinstance(ls.get("tick"), bool):
        payload["tick"] = ls["tick"]

    if dry_run:
        print("[dry-run] POST %s/event  %s"
              % (ov.base_url(), json.dumps(payload, ensure_ascii=False)))
        log("dry-run", "would post turn %s, %d chars" % (turn, len(text)))
        return True
    ok, why = ov.post("/event", timeout=NET_TIMEOUT, **payload)
    log("posted" if ok else "post-failed",
        "turn %s, %d chars%s" % (turn, len(text), "" if ok else " -- %s" % why))
    return ok


# --- the two entry points -----------------------------------------------------

def cmd_run(a):
    """The detached child. Reads its job file, condenses, re-posts, tidies up."""
    job = Path(a.job)
    try:
        d = json.loads(job.read_text(encoding="utf-8"))
    except Exception as e:
        log("bad-job", "%s: %s" % (job.name, e))
        return 0
    try:
        summary = d.get("summary") or ""
        turn = d.get("turn")
        text = condense(summary)
        if text:
            repost(text, turn, d.get("mood"), dry_run=a.dry_run)
    finally:
        try:
            job.unlink()
        except Exception:
            pass
    return 0


def cmd_condense(a):
    """By hand: condense and print. Posts only if asked, so testing is safe."""
    summary = a.summary
    if a.file:
        summary = Path(a.file).read_text(encoding="utf-8").strip()
    if not summary:
        print("luna.py: pass --summary or --file", file=sys.stderr)
        return 2
    print("in  : %d chars" % len(summary))
    text = condense(summary)
    if not text:
        print("out : nothing -- the full summary stands (see state\\luna.log)")
        return 0
    print("out : %d chars" % len(text))
    print(text)
    if a.post or a.dry_run:
        repost(text, a.turn, a.mood, dry_run=a.dry_run)
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="luna.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("run", help="the detached child: job file in, pin swapped out")
    s.add_argument("job")
    s.add_argument("--dry-run", action="store_true", help="print the POST, don't send it")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("condense", help="condense some text and print it")
    s.add_argument("--summary")
    s.add_argument("--file", help="read the summary from a file instead")
    s.add_argument("--turn", type=int, default=-1, help="only needed with --post/--dry-run")
    s.add_argument("--mood", default=None)
    s.add_argument("--post", action="store_true", help="actually post it (stale-guarded)")
    s.add_argument("--dry-run", action="store_true", help="print the POST, don't send it")
    s.set_defaults(fn=cmd_condense)
    return p


def main(argv):
    p = build_parser()
    a = p.parse_args(argv)
    if not getattr(a, "fn", None):
        p.print_help()
        return 0
    try:
        return a.fn(a)
    except Exception as e:
        # Same contract as stream.py: this process is never allowed to raise.
        log("crashed", "%s: %s" % (type(e).__name__, e))
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
