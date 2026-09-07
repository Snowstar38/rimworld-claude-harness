"""Append-only review log for repeated, egregious Twitch chat violations.

Three qualifying strikes within a rolling hour append one review entry. A
username can produce at most one entry per hour. Strike history and that
cooldown are bounded in memory and reset whenever the process restarts; this
class records usernames for human review and never bans or blocks anyone.
"""
from collections import OrderedDict, deque
from datetime import datetime, timezone
import math
import re
import threading
import time


_LOGIN = re.compile(r"[A-Za-z0-9_]{1,25}\Z")
_CATEGORIES = frozenset(("prompt_injection", "malice"))
_WINDOW_SECONDS = 60 * 60
_MAX_USERS = 2048


class ProblemUsers:
    """Track qualifying strikes in memory and append threshold crossings."""

    def __init__(self, path):
        self.path = path
        self._users = OrderedDict()
        self._lock = threading.Lock()

    def record(self, username, category, now=None):
        """Record one strike and return whether a review entry was appended.

        Invalid usernames and non-qualifying categories return ``False``.
        File-system errors from the append are intentionally raised so callers
        can observe them without changing their separate reject decision.
        """
        if not isinstance(username, str) or not _LOGIN.fullmatch(username):
            return False
        if category not in _CATEGORIES:
            return False
        if now is None:
            now = time.time()
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
            raise ValueError("now must be a finite Unix timestamp")
        now = float(now)

        with self._lock:
            state = self._users.get(username)
            if state is None:
                state = {"strikes": deque(), "last_logged": None}
                self._users[username] = state
            else:
                self._users.move_to_end(username)

            strikes = state["strikes"]
            cutoff = now - _WINDOW_SECONDS
            while strikes and strikes[0] < cutoff:
                strikes.popleft()
            strikes.append(now)

            while len(self._users) > _MAX_USERS:
                self._users.popitem(last=False)

            last_logged = state["last_logged"]
            if len(strikes) < 3 or (last_logged is not None and now - last_logged < _WINDOW_SECONDS):
                return False

            stamp = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            line = f"{stamp} username={username} strikes={len(strikes)} category={category}\n"
            with open(self.path, "a", encoding="utf-8", newline="") as handle:
                handle.write(line)
            state["last_logged"] = now
            return True
