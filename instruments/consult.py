"""RARE and SLOW: one question to Sol, minutes long. Launch it as a background task.

  python consult.py "why is the butcher bench idle when there are 40 corpses?"
  python consult.py "..." --timeout 300 --words 150

Sol is another agent in this house, on codex's default model. `look.py` gets a
picture and no context; a Scout gets a brief and a fixed list of questions. This
is the third shape: **one question of our choosing, asked of a reader who can go
and look.** Sol arrives in the instruments directory with a read-only sandbox,
reads PLAYBOOK.md, runs whatever readers the question needs, and answers.

## What it is for

Detective work that would otherwise eat a turn. A thing that has been wrong for
three turns and is not in any instrument's output; a contradiction between two
readings; "what am I not asking". Not for anything an instrument answers
directly -- `bills.py` costs a second and this costs minutes.

## Why it must be a background task

Codex is a whole agent session: it reads files and runs commands before it
answers, and the ceiling here is TIMEOUT seconds. Blocking the Core on it stops
the game's turn loop for that long, on stream. Start it in the background, keep
playing, read `state\\consult-latest.txt` when it lands.

## Read-only, and told so twice

`-s read-only` is the sandbox; the prompt says the same thing in words. Nothing
consulted may click, designate, write a save, or touch pause or game speed --
the Hands own every mutation, and a consultant that moved something would have
changed the situation it was asked about.
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
LATEST = os.path.join(STATE, "consult-latest.txt")

# The same codex binary look.py and rota.py use, found by walking out of the
# room rather than spelled out.
CODEX = os.path.join(os.path.dirname(os.path.dirname(HERE)), "tools", "codex", "codex.exe")

TIMEOUT = 300.0   # a consultant reads before answering; a Lookout's 120s is short here
WORDS = 150       # the answer is a lead, not a report

# CREATE_NO_WINDOW: codex.exe is a console program, and a new console window
# taking the foreground minimises a fullscreen RimWorld. See look.py.
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

PROMPT = """You are Sol. errata, an AI playing RimWorld on a live stream, has \
stopped to consult you once about one thing. This is rare and it costs a turn, \
so answer the question that was asked.

Your working directory is the RimWorld instruments folder. Read `PLAYBOOK.md` \
there first: it names the tools and what each one already answers. They are \
read-only python readers of the running game -- run the ones the question needs. \
`python rim.py call <tool> '<json>'` is the raw escape hatch for a read with no \
wrapper.

READ ONLY. Do not click anything, place or cancel a designation, change a bill, \
write a save, or pause, unpause or change game speed. The game is live and \
somebody else is playing it; you are looking, not acting.

THE QUESTION:
{question}

Answer in at most {words} words, plain text, no markdown headings. Lead-shaped: \
the thing you found first, then what follows from it. If the honest answer is \
that the question cannot be answered from what is readable, say that and say \
what would answer it. End with exactly two more lines:
CONFIDENCE: <high|medium|low> -- <why, in a few words>
CHECKED: <the files and commands you actually read, comma separated>"""


def record(text):
    """One slot, overwritten. The Core reads this file when the answer lands."""
    os.makedirs(STATE, exist_ok=True)
    tmp = LATEST + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, LATEST)
    except OSError:
        pass


def ask(question, timeout=TIMEOUT, words=WORDS):
    """One codex call. -> (text, None) or (None, one-line reason it failed)."""
    if not os.path.isfile(CODEX):
        return None, "codex not found at %s" % CODEX
    os.makedirs(STATE, exist_ok=True)
    outp = os.path.join(STATE, "consult-%d.out" % int(time.time() * 1000))
    argv = [CODEX, "exec",
            # No GABS: the user config wires up an MCP bridge, and a consultant
            # that loaded it would boot a second one beside the live game's.
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--ephemeral",            # no session file per question
            "-s", "read-only",        # runs the readers, cannot write anything
            "-C", HERE,               # PLAYBOOK.md and the instruments are here
            "-o", outp,
            "--color", "never",
            PROMPT.format(question=question, words=words)]
    try:
        # stdin=DEVNULL: `codex exec` reads stdin even when a prompt argument is
        # given, so an inherited console handle waits for an EOF that never comes.
        r = subprocess.run(argv, cwd=HERE, timeout=timeout,
                           creationflags=NO_WINDOW, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if r.returncode:
            tail = (r.stdout or b"").decode("utf-8", "replace").strip()[-200:]
            return None, "codex exit %d -- %s" % (r.returncode, tail)
        with open(outp, "r", encoding="utf-8") as f:
            text = f.read().strip()
        return (text, None) if text else (None, "codex answered with nothing")
    except subprocess.TimeoutExpired:
        return None, "Sol did not answer in %ds" % timeout
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, str(e)[:150])
    finally:
        try:
            os.remove(outp)
        except OSError:
            pass


def main(argv):
    p = argparse.ArgumentParser(
        prog="consult.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("question", help="the one thing to ask Sol")
    p.add_argument("--timeout", type=float, default=TIMEOUT,
                   help="seconds to wait (default %d)" % TIMEOUT)
    p.add_argument("--words", type=int, default=WORDS,
                   help="answer length cap (default %d)" % WORDS)
    a = p.parse_args(argv)

    question = (a.question or "").strip()
    if not question:
        sys.stderr.write("consult.py: the question is empty\n")
        return 2

    # Sol writes curly quotes; a Windows console is cp1252, so a plain print
    # either mojibakes them or raises and loses the whole answer.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    t0 = time.time()
    text, why = ask(question, a.timeout, a.words)
    dt = time.time() - t0
    if text is None:
        # One line, non-zero: the Core should see a failure, not a wall of codex.
        sys.stderr.write("consult.py: FAILED after %.0fs -- %s\n" % (dt, why))
        return 1
    body = "CONSULT %s  (%.0fs)\nQ: %s\n\n%s" % (
        time.strftime("%Y-%m-%d %H:%M:%S"), dt, question, text)
    record(body)
    print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
