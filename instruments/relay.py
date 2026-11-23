"""M's phone -> the running playthrough. One command, no JSON to compose.

    python relay.py "the raiders are coming from the east"
    python relay.py "stop, you are about to sell the last meds" --pause

The listener session (see `listener.md`) is a relay and nothing else, so it gets
a relay-shaped command instead of an HTTP call it has to remember the shape of.

Normal messages land on the overlay as a `kind: "human"` event, the same channel
control.html writes to; the Core reads them with `stream.py human-check` between
turns. That is a wait of one turn.

`--pause` is the drop-everything lever. An external pause with no new letter is
the one signal the playing agent always reads as "a person -- stop and report",
so it interrupts within the turn instead of after it. It is also the ONLY bridge
call this file is allowed to make: the bridge serializes, one agent is in the
game at a time, and a second player would be a worse problem than a late
message. Read nothing, click nothing, pause and speak.

The message is posted BEFORE the pause on purpose: a bridge call can sit behind
whatever the playing agent is already doing, and the text arriving is worth more
than the order.
"""
import json
import os
import sys
import urllib.error
import urllib.request

# Not localhost: binding 8080 dies on this machine and a `localhost` host can
# resolve to ::1, which the overlay is not listening on. Same constant, same
# reasons, as overlay_client.DEFAULT_URL.
DEFAULT_URL = "http://127.0.0.1:8090"
TIMEOUT = 4


def base_url():
    return (os.environ.get("OVERLAY_URL") or DEFAULT_URL).strip().rstrip("/")


def post_human(text):
    """Returns (ok, reason). Never raises."""
    body = json.dumps({"kind": "human", "text": text}).encode("utf-8")
    req = urllib.request.Request(base_url() + "/event", data=body,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        urllib.request.urlopen(req, timeout=TIMEOUT).read()
        return True, None
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            detail = ""
        return False, "HTTP %s %s" % (e.code, detail)
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


def pause_game():
    """Returns (ok, reason). Never raises. Import is deferred so that a broken
    or missing bridge can't stop a plain message from going out."""
    try:
        import rim
    except Exception as e:
        return False, "could not import rim.py: %s" % e
    try:
        rim.init()
        r = rim.game("rimworld/pause_game", {"pause": True}, strict=False)
        if isinstance(r, dict) and r.get("success") is False:
            # Older bridge builds took no argument at all; a refusal here is
            # cheap enough to retry once rather than report a stopped colony
            # that is still running.
            r = rim.game("rimworld/pause_game", {}, strict=False)
        if isinstance(r, dict) and r.get("success") is False:
            return False, str(r.get("message") or r)
        return True, None
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


def main():
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    want_pause = "--pause" in argv
    words = [a for a in argv if a != "--pause"]
    text = " ".join(words).strip()
    if not text:
        print('usage: python relay.py "message" [--pause]', file=sys.stderr)
        return 2

    ok, why = post_human(text)
    if ok:
        print("SENT: the Core sees it at its next human-check (between turns).")
    else:
        print("NOT SENT -- the overlay did not take it (%s)." % why)
        print("The message reached nothing. Tell M the overlay is down.")

    if want_pause:
        paused, why2 = pause_game()
        if paused:
            print("PAUSED: the colony is stopped. The playing agent reads an "
                  "unexplained pause as 'a person -- stop and report'.")
        else:
            print("NOT PAUSED (%s). Tell M the game did not stop." % why2)
        return 0 if (ok or paused) else 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
