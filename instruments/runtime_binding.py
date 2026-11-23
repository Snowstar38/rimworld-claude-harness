"""Identify the controlling process, including its birth time (PID reuse safe)."""
import ctypes
import os
from pathlib import Path
import subprocess
import json
import time
import game_session

PATH = Path(__file__).resolve().parent / "state" / "session-runtime.json"
# What the lifecycle hook last saw and last failed at. Written by the hook, read
# by the refusals, so a Core that has to repair by hand has the session id it
# cannot otherwise learn about itself.
NOTE = Path(__file__).resolve().parent / "state" / "session-runtime-note.json"

# A warm start -- errata.bat launched at a game that is already up, with the
# previous session's claude.exe still exiting -- makes SessionStart's bind lose
# to a binding that is alive for ten more seconds and then dead for the rest of
# the night. Rebinding is retried from later hook events; this is how often.
REBIND_RETRY = 30.0

REPAIR = "python stream.py bind-check --repair"


def process_identity(pid):
    try:
        pid = int(pid)
        if pid <= 0:
            return None
        if os.name != "nt":
            return Path('/proc/%d/stat' % pid).read_text().rsplit(')', 1)[1].split()[19]
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        h = kernel.OpenProcess(0x1000, False, pid)
        if not h:
            return None
        try:
            status = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(h, ctypes.byref(status)) or status.value != 259:
                return None
            ts = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(h, *[ctypes.byref(t) for t in ts]):
                return None
            return str((ts[0].dwHighDateTime << 32) | ts[0].dwLowDateTime)
        finally:
            kernel.CloseHandle(h)
    except (OSError, ValueError, TypeError, IndexError):
        return None


def load():
    return game_session.read(PATH)


def alive(binding=None):
    b = load() if binding is None else binding
    return bool(b.get('session_id') and b.get('host_started')
                and process_identity(b.get('host_pid')) == str(b['host_started']))


def bind(session_id, pid):
    identity = process_identity(pid)
    if not identity or not session_id:
        raise RuntimeError('Cannot bind to a missing controlling process/session')
    with game_session.locked('runtime-binding'):
        old = load()
        # The same host process under another session id is still us: a
        # `bind-check --repair` that reused the previous session's id, or a
        # session id that changed under one claude.exe. Only a DIFFERENT live
        # process is a conflict.
        same_host = (old.get('host_pid') == int(pid)
                     and str(old.get('host_started')) == str(identity))
        if alive(old) and not same_host:
            raise RuntimeError('A different live process/session already controls this game')
        result = dict(session_id=str(session_id), host_pid=int(pid), host_started=identity)
        game_session.write(PATH, result)
        return result


def note():
    return game_session.read(NOTE)


def remember(**fields):
    """Merge into the hook note. Never raises: it is only ever a diagnostic."""
    try:
        current = note()
        current.update(fields)
        current['at'] = time.time()
        game_session.write(NOTE, current)
    except Exception:
        pass


def describe(session=None):
    """(state, line) for a refusal. state is live/missing/dead/mismatch.

    The 2026-09-07 stream lost three turns to one refusal that named the hook
    when the condition was a dead PID, so every caller says which of these four
    it is, with the pid and session in the text.
    """
    b = load()
    if not b.get('session_id') or not b.get('host_pid'):
        return 'missing', ('no runtime binding on record -- %s has never been '
                           'written for this session' % PATH.name)
    held = '%s on host pid %s' % (b['session_id'], b.get('host_pid'))
    if not alive(b):
        return 'dead', ('runtime binding is STALE: it names session %s, and that '
                        'process is GONE. Nothing routes events until it is '
                        'rebound.' % held)
    if session and str(b['session_id']) != str(session):
        return 'mismatch', ('runtime binding names a DIFFERENT live session: %s, '
                            'while this session is %s' % (held, session))
    return 'live', 'runtime binding is live: session %s' % held


def rebind_if_stale(session_id, ancestor=None):
    """Self-heal a warm start. (rebound, reason); never raises.

    Only the Core may call this -- a Hands fork shares its host process but not
    its session id, so a fork that rebound would write its parent's binding
    under its own name. The hook enforces that; this function is the mechanism.
    """
    try:
        b = load()
        if str(b.get('session_id') or '') == str(session_id) and alive(b):
            return False, 'live'
        pid = None
        if alive(b):
            # Alive under another session id. If it is OUR host process the id
            # is merely stale (a repair reused the old one); adopt it. Another
            # live claude.exe is a real second session and keeps the game.
            pid = (ancestor or claude_ancestor)()
            if b.get('host_pid') != pid:
                return False, ('another live session (%s) holds the binding'
                               % b.get('session_id'))
        last = note()
        if (last.get('rebindAt') and last.get('rebindSession') == str(session_id)
                and time.time() - float(last['rebindAt']) < REBIND_RETRY
                and last.get('rebindError')):
            return False, 'retry throttled (%s)' % last['rebindError']
        remember(rebindAt=time.time(), rebindSession=str(session_id),
                 rebindError=None)
        if pid is None:
            pid = (ancestor or claude_ancestor)()
        bind(session_id, pid)
        remember(rebindError=None, reboundAt=time.time())
        return True, 'rebound session %s to host pid %s' % (session_id, pid)
    except Exception as ex:
        remember(rebindError='%s: %s' % (type(ex).__name__, ex))
        return False, '%s: %s' % (type(ex).__name__, ex)


def claude_ancestor():
    """Only SessionStart calls this; never probe process command lines/secrets."""
    if os.name == 'nt':
        result = subprocess.run(['powershell', '-NoProfile', '-Command',
            'Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name | ConvertTo-Json -Compress'],
            capture_output=True, text=True, timeout=5, creationflags=0x08000000)
        rows = json.loads(result.stdout)
        processes = {int(r['ProcessId']): (int(r['ParentProcessId']), r['Name'].lower()) for r in rows}
        pid = os.getppid()
        for _ in range(16):
            parent, name = processes.get(pid, (0, ''))
            if name == 'claude.exe':
                return pid
            pid = parent
        raise RuntimeError('No controlling Claude process in hook ancestry')
    pid = os.getppid()
    for _ in range(16):
        data = Path('/proc/%d/stat' % pid).read_text()
        name = data.split('(', 1)[1].rsplit(')', 1)[0]
        if 'claude' in name:
            return pid
        pid = int(data.rsplit(')', 1)[1].split()[1])
    raise RuntimeError('No controlling Claude process in hook ancestry')
