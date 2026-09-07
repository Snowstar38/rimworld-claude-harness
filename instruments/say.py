"""Narrate one line to the stream. The fallback for everything without a flag.

  python say.py "Finn made an awful parka. It's better than nothing. Barely." --mood welp
  python say.py --mood scared          # just the face, no words

50-150 characters, first person, present tense, dry. Narrate the decision, not
every keystroke of it: when several calls carry out one intention, say it once.

Moods: happy thinking sad scared welp excited angry veryhappy
  happy is resting state; thinking while weighing options; welp is deadpan
  "well, that happened"; scared for real danger; angry for indignities; sad for
  losses; excited for good surprises; veryhappy is earned, not given.

A bad mood name prints a loud error -- this is the one place in the stream path
allowed to, because the enum is worth learning -- and still exits 0. Nothing
here can fail a turn.
"""
import sys

import overlay_client as ov


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
        else:
            text_parts.append(a)

    text = " ".join(text_parts).strip()

    if mood is not None and mood not in ov.MOODS:
        print("say.py: %r is not a mood. The eight are: %s"
              % (mood, " ".join(ov.MOODS)), file=sys.stderr)
        mood = None
        if not text:
            return 0   # a mood-only post with a bad mood has nothing left to send

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
    print("sent thought%s" % (" with mood %s" % mood if mood else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
