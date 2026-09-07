"""Persist a loaded-game generation; discard observations from other timelines.

The companion sessionId changes on every load, including a same-tick reload.
Older companions still get rollback detection from clock snapshots. No RPCs here.
"""
import contextlib
import itertools
import json
import os
from pathlib import Path
import time
import uuid

STATE = Path(__file__).resolve().parent / "state"
WRITE_ATTEMPTS = 5
WRITE_BACKOFF = 0.05
_seq = itertools.count()


@contextlib.contextmanager
def locked(name="game-session"):
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / (name + ".lock")).open("a+b") as f:
        f.seek(0)
        if not f.read(1):
            f.write(b"0")
            f.flush()
        f.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            f.seek(0)
            if os.name == "nt":
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {} if default is None else default


def write(path, data, indent=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=indent)
    for attempt in range(WRITE_ATTEMPTS):
        # Unique per writer: a shared .tmp name is a second way to collide.
        tmp = path.with_name("%s.%d.%d.tmp" % (path.name, os.getpid(), next(_seq)))
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
            return
        except PermissionError:
            # Antivirus or the indexer holds the file for a moment on Windows.
            tmp.unlink(missing_ok=True)
            if attempt == WRITE_ATTEMPTS - 1:
                raise
            time.sleep(WRITE_BACKOFF * (attempt + 1))
        except Exception:
            tmp.unlink(missing_ok=True)
            raise


def current():
    return read(STATE / "game-session.json")


def observe(tick, session_id=None):
    if not isinstance(tick, int) or isinstance(tick, bool) or tick < 0:
        return current()
    with locked():
        old = current()
        changed = (not old.get("generation")
                   or tick < old.get("tick", tick)
                   or (session_id and old.get("sessionId")
                       and session_id != old["sessionId"]))
        if changed:
            old = {"generation": uuid.uuid4().hex, "startedAt": time.time()}
            # These are observations, never game state or user notes. Clearing
            # the whole dedupe is conservative: a repeated letter beats a lost one.
            for name, blank in (("overlay-letters.json", {"seen": []}),
                                ("letter-seen-at.json", {}),
                                ("run-last-stop.json", {})):
                write(STATE / name, blank)
        old["tick"] = tick
        if session_id:
            old["sessionId"] = session_id
        write(STATE / "game-session.json", old)
        return old


def stamp(text, context=None):
    context = current() if context is None else context
    return "SESSION %s tick %s captured %.6f\n%s" % (
        context.get("generation", "unknown"), context.get("tick", "unknown"),
        context.get("capturedAt", time.time()), text)


def report_is_current(text, context=None, max_age=300):
    context = current() if context is None else context
    try:
        header = text.splitlines()[0].split()
        return (len(header) == 6 and header[0] == "SESSION"
                and header[1] == context.get("generation")
                and int(header[3]) <= context["tick"]
                and 0 <= time.time() - float(header[5]) <= max_age)
    except (ValueError, KeyError, IndexError, TypeError):
        return False
