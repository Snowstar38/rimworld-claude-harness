"""The unit suite, hermetically: no test may touch the bridge or the overlay.

    python runtests.py [extra unittest-discover args]

Found live 2026-09-12 (BUGS.md): with RimWorld open, `python -m unittest
discover` started the supervised-play clock twice and dropped overlay posts.
The leaks were five unmocked seams, fixed 2026-09-13 -- but the suite is 130
files and the next forgotten mock would be silent again, so this runner is the
guard rail. Prefer it to bare `unittest discover`; with it, running the suite
beside a live game is safe by construction.

How it works: `socket.socket.connect` is wrapped before discovery. A connect
to 8080 (GABS) or 8090 (overlay) is refused on the spot -- the game cannot be
reached even when it is up, and nobody pays Windows' ~2 s refused-connect tax.
Every attempt, blocked or allowed, is written to state\\netguard.log with the
stack that made it, and ANY attempt fails the run: exit 2 with the log named,
even if all tests passed. A future test that legitimately needs a local socket
(its own fixture server) should mock at the client seam instead, or this
policy gets revisited then.
"""
import io
import os
import socket
import sys
import threading
import traceback
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "state", "netguard.log")
BLOCKED_PORTS = {8080, 8090}   # GABS bridge, stream overlay

_log_lock = threading.Lock()
_attempts = [0]
_real_connect = socket.socket.connect


def _record(address, blocked):
    with _log_lock:
        _attempts[0] += 1
    buf = io.StringIO()
    buf.write("%s connect to %r\n" % ("BLOCKED" if blocked else "allowed", address))
    for line in traceback.format_stack()[:-2]:
        if "instruments" in line or "test_" in line:
            buf.write(line)
    buf.write("-" * 60 + "\n")
    with _log_lock:
        try:
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(buf.getvalue())
        except OSError:
            pass


def _guarded_connect(self, address):
    try:
        blocked = int(address[1]) in BLOCKED_PORTS
    except Exception:
        blocked = False
    _record(address, blocked)
    if blocked:
        raise ConnectionRefusedError(
            "netguard: connect to %r blocked (would reach the bridge/overlay)"
            % (address,))
    return _real_connect(self, address)


if __name__ == "__main__":
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        open(LOG, "w", encoding="utf-8").close()
    except OSError:
        pass
    socket.socket.connect = _guarded_connect
    os.chdir(HERE)
    sys.path.insert(0, HERE)
    argv = [sys.argv[0], "discover", "-s", ".", "-p", "test_*.py"] + sys.argv[1:]
    result = unittest.main(module=None, argv=argv, exit=False).result
    if _attempts[0]:
        sys.stderr.write(
            "NETGUARD: %d socket connect attempt(s) during the suite -- a test"
            " is missing a mock. Stacks: %s\n" % (_attempts[0], LOG))
        sys.exit(2)
    sys.exit(0 if result.wasSuccessful() else 1)
