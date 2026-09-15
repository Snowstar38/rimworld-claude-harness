"""Narrate one line to the stream. The fallback for everything without a flag.

  python say.py "Finn made an awful parka. It's better than nothing. Barely." --mood welp
  python say.py "Back to the stonecutters."      # mood: happy (default)
  python say.py --mood scared          # just the face, no words
  python say.py "..." --mood none      # deliberately no face -- see below

50-150 characters, first person, present tense, dry. Narrate the decision, not
every keystroke of it: when several calls carry out one intention, say it once.

Moods: happy thinking sad scared welp excited angry veryhappy
  happy is resting state; thinking while weighing options; welp is deadpan
  "well, that happened"; scared for real danger; angry for indignities; sad for
  losses; excited for good surprises; veryhappy is earned, not given.

**Omitting --mood sends `happy`**, the resting face, and the sent line says
`mood: happy (default)`. It used to send the thought with no mood at all, which
is not "no face": the overlay leaves the big face showing whatever the LAST
thought set, so a line about a bandaged colonist read under a grin from two
turns ago, and the feed card lost its emoji. `--mood none` still posts with no
mood -- for when leaving the previous face up is the point -- and says so.

A bad mood name prints a loud error -- this is the one place in the stream path
allowed to, because the enum is worth learning -- then sends on the default face
rather than dropping one, and still exits 0. Nothing here can fail a turn.
"""
import re
import sys

import overlay_client as ov

# The overlay's own resting face: server.py starts state["mood"] at "happy" and
# overlay.html colours a moodless card with COLORS.happy. So this default changes
# no pixel that was not already implied -- it just stops the face going stale.
DEFAULT_MOOD = "happy"
NO_MOOD = "none"


# A FLAG shape, not merely "starts with a dash". Narration is prose, and
# `say.py "-- and then the wall went up."` is a line, not a mistyped option:
# one argv word, letters after the dashes, no spaces.
_FLAG = re.compile(r"^--?[A-Za-z][A-Za-z0-9-]*(=.*)?$", re.S)


def main(argv):
    text_parts, mood, want_mood = [], None, False
    for a in argv:
        if want_mood:
            mood, want_mood = a, False
        elif a in ("-m", "--mood"):
            want_mood = True
        elif a.startswith("--mood="):
            mood = a[len("--mood="):]
        elif a in ("-h", "--help"):
            print(__doc__)
            return 0
        elif _FLAG.match(a):
            # Everything unrecognised used to fall into the narration, and this
            # is the one tool whose swallowed argument is PUBLISHED: `say.py
            # "Back to work." --moood welp` read "Back to work. --moood welp"
            # out to viewers, silently, and exited 0.
            print("say.py: %r is not an option. The only one is --mood <mood> "
                  "(or --mood none). Nothing was sent." % a, file=sys.stderr)
            return 2
        else:
            text_parts.append(a)
    if want_mood:
        print("say.py: --mood wants a mood after it -- one of %s, or %s for no "
              "face. Nothing was sent." % (" ".join(ov.MOODS), NO_MOOD),
              file=sys.stderr)
        return 2

    text = " ".join(text_parts).strip()
    asked = mood

    if mood == NO_MOOD:
        mood = None                     # explicit, and said on the sent line
    elif mood is not None and mood not in ov.MOODS:
        print("say.py: %r is not a mood. The eight are: %s (or %s for no face)"
              % (mood, " ".join(ov.MOODS), NO_MOOD), file=sys.stderr)
        if not text:
            return 0   # a mood-only post with a bad mood has nothing left to send
        mood, asked = DEFAULT_MOOD, None   # a face, not a dropped one
    elif mood is None and text:
        mood = DEFAULT_MOOD

    if not text and not mood:
        # Empty thought is the documented "no stream message for this call".
        return 0

    # This is the dedicated command, so silence is actively misleading: it
    # looks identical to a command that never ran.  The shared --say helper is
    # intentionally silent because narration must never break another tool.
    sent = ov.say(text, mood) if text else ov.set_mood(mood)
    if sent is None:
        print("say.py: could not start the overlay post", file=sys.stderr)
        return 1
    if mood is None:
        face = "mood: none (asked for -- the overlay keeps the face it had)"
    else:
        face = "mood: %s%s" % (mood, "" if asked else " (default)")
    print("%s  %s" % ("sent thought" if text else "sent face", face))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
