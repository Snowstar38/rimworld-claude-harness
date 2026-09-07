"""Bring the whole RimWorld agent stack up with one command, and say so in 14 lines.

  python setup.py                          # GABS + RimWorld + companion, health report
  python setup.py --load "Lampblack - day 21, sealed"
  python setup.py --newest Lampblack       # ... or resolve the newest one AT LOAD TIME
  python setup.py --status                 # report only, start nothing
  python setup.py --void "<save name>" --reason "<why>"   # mark a save void
  python setup.py --unvoid "<save name>"   # ... and take the mark off again
  python setup.py --help                   # this docstring, and nothing is started

Three layers have to be alive before anything else here works: GABS on
127.0.0.1:8080, RimWorld launched *through* GABS so the GABP bridge connects,
and the HomeBridge companion DLL registered inside it. This starts whichever of
the three is missing and skips whichever isn't -- run it twice and the second
run costs three HTTP calls.

The report is capped at 14 lines: this block is the only thing that enters the
player's (the Core's) context, and that context is re-read on every later
message of the session. Details go to stderr or to `state\\`, not here.

## The safety rule that outranks everything else in this file

M plays RimWorld herself, in HER save profile, and it is the same
executable name -- `RimWorldWin64`. GABS's config sets
`"stopProcessName": "RimWorldWin64"`, so `games_stop` does not stop *our* game,
it stops *that process name*. If a RimWorld is running that our bridge is not
talking to, it is hers, and both of the obvious moves -- start ours over it,
stop the stale one -- end her session. So: a RimWorldWin64 process with no
bridge connection is a hard stop with exit code 2 and no further calls. It is
never "a stale process to clean up"; if it really is stale, a person can say so.

## Loading a save

`load_game_ready` returns as soon as the map is playable, and **the game is then
running, not paused**. Everything downstream assumes otherwise: `list_colonists`'
positional join goes racy, and a watcher rotation that was told the colony is
paused starts its clocks against a moving world. So after a load this pauses the
game, and the STATUS block prints the speed it actually observed rather than the
one it asked for.

## Voiding a save

`--newest` picks by write time, which cannot know that a save has been
repudiated -- so for two hours the documented startup path loaded a colony the
CHRONICLE calls void. The mark is a **sidecar file** beside the save in
`profile\\Saves`: `<save name>.void`, whose first line is the reason. Its
*presence* is what voids the save; the content only explains, and a sidecar that
cannot be read still voids, because the silent direction is the dangerous one.
`--void "<name>" --reason "<why>"` writes it and `--unvoid "<name>"` removes it;
neither needs a game. `--newest` then skips every voided save and names each one
it skipped with its reason on a `voided` line in the STATUS block -- a skip is
never silent -- and `--load` of a voided name refuses outright with the reason
unless `--force-void` is passed. RimWorld ignores the file; deleting it by hand
un-voids the save just as `--unvoid` does.
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

import rim

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
GABS_LOG = os.path.join(STATE, "gabs.log")
SAVES = r"C:\Home\rimworld\bridge\profile\Saves"
LISTENER_BAT = os.path.join(HERE, "listener.bat")

# Windows: keep GABS alive after this python exits, and out of our console's
# Ctrl-C group. Without CREATE_NEW_PROCESS_GROUP a Ctrl-C in the launching shell
# kills the server the next tool was going to talk to.
DETACHED = 0x00000008 | 0x00000200

CONNECT_TIMEOUT = 180          # gabs-config says gabpConnectSeconds: 180
GABS_TIMEOUT = 45

# The alert priorities that own the `alerts` line; everything below them gets one
# folded line under it. `RimWorld.AlertPriority` is Medium < High < Critical.
# Deliberately duplicated from `watch.py` -- see the alerts block in status_block().
LOUD_ALERTS = ("High", "Critical")


# --------------------------------------------------------------- primitives

def gabs_alive(timeout=2.0):
    """Is something answering MCP on 8080? Cheap, short-timeout, never raises.

    rim.rpc uses a 600s timeout, which is right for a map sweep and wrong for
    "is the server there" -- a dead port would hang the whole bring-up.
    """
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2024-11-05",
                                  "capabilities": {},
                                  "clientInfo": {"name": "setup", "version": "1"}}})
    req = urllib.request.Request(
        rim.URL, data=body.encode(),
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"})
    try:
        urllib.request.urlopen(req, timeout=timeout).read()
        return True
    except Exception:
        return False


def rimworld_pids():
    """PIDs of every RimWorldWin64 process on the machine, ours or M's."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq RimWorldWin64.exe", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[0].lower().startswith("rimworldwin64"):
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return pids


def bridge_connected():
    """True when GABS has a live GABP link into a RimWorld.

    Uses `rimbridge/get_bridge_status` rather than a game tool: it answers at the
    main menu too, so it separates "the bridge is up" from "a colony is loaded",
    which are different failures with different fixes.
    """
    try:
        r = rim.tool("games_call_tool", {"gameId": rim.GAME,
                                         "tool": "rimbridge/get_bridge_status",
                                         "arguments": {}})
    except Exception:
        return False
    return isinstance(r, dict) and r.get("success") is not False


def start_gabs():
    os.makedirs(STATE, exist_ok=True)
    log = open(GABS_LOG, "ab")
    log.write(("\n===== setup.py started GABS %s =====\n"
               % time.strftime("%Y-%m-%d %H:%M:%S")).encode())
    log.flush()
    subprocess.Popen([rim.GABS, "server", "http", "--configDir", rim.CFG,
                      "--log-level", "info"],
                     stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                     creationflags=DETACHED, close_fds=True)
    t0 = time.time()
    while time.time() - t0 < GABS_TIMEOUT:
        if gabs_alive():
            return True
        time.sleep(1.0)
    return False


# --------------------------------------------------------------- game facts

def game_tools():
    """The live tool surface as `namespace/name`, all 127 of them.

    Two shapes to undo, neither documented anywhere we had written down:

    1. `games_tool_names` returns PROSE, not JSON, and the names in it are
       OpenAI-normalised -- `rimworld_rimworld_click_cell`, not
       `rimworld/click_cell`. `enableOpenAINormalization` in gabs-config is what
       does it. A caller matching on "/" finds zero tools and concludes the game
       is not connected.
    2. It PAGINATES at 50 and says so only in a trailing "Next cursor: 50" line.
       Read one page and you will "discover" that load_game_ready does not exist.

    Also: INSTALL.md records that GABS never lists the companion tools because it
    caches its surface at connect time. **That is no longer true** -- both
    `rimworld_home_ping` and `rimworld_home_get_cells_plus` appear in page 1 here
    (2026-08-30 evening, same DLL). Discovery-based callers are fine now; the
    hardcoded-name advice still works either way.
    """
    names, cursor = set(), 0
    while cursor is not None and cursor < 400:
        r = rim.tool("games_tool_names", {"gameId": rim.GAME, "cursor": cursor})
        if not isinstance(r, str):
            break
        page = [l.strip() for l in r.splitlines() if l.strip().startswith(rim.GAME + "_")]
        for n in page:
            body = n[len(rim.GAME) + 1:]
            ns, _, rest = body.partition("_")
            names.add("%s/%s" % (ns, rest) if rest else body)
        nxt = [l for l in r.splitlines() if l.startswith("Next cursor:")]
        cursor = int(nxt[0].split(":")[1]) if nxt and page else None
    return names


def first_tool(names, have):
    for n in names:
        if n in have:
            return n
    return None


def plain(s):
    """Strip RimWorld's rich-text markup out of a pawn label.

    Labels come back as `<color=#RRGGBB>Finn</color>`-ish; nothing in the stack
    strips it, so names print as markup unless someone does.
    """
    out, depth = [], 0
    for ch in str(s or ""):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif not depth:
            out.append(ch)
    return "".join(out).strip()


def ticks_phrase(t):
    """2500 ticks = one game hour, 60000 = one day -- and this is NOT the clock.

    Corrected 2026-09-02: this line and the companion's `home/get_time`
    disagreed by six hours at the same tick, and the arithmetic was the one that
    was wrong. RimWorld's hour is `GenDate.HourInteger(absTicks, longitude)`
    with `absTicks = ticksGame + gameStartAbsTick`, so BOTH the map's longitude
    offset and the start-tick offset are missing here -- and neither of them can
    be recovered from `ticksGame` alone. The number is still an honest count of
    elapsed ticks; it is not a date. So it says what it is, every time it
    prints, and a reader can never mistake it for the game clock.
    """
    if t is None:
        return "?"
    return ("tick %d = day %d, %02dh (tick arithmetic, no longitude)"
            % (t, t // 60000, t % 60000 // 2500))


def clock_phrase(t, have=None):
    """The game's OWN calendar via `home/get_time`, else labelled arithmetic.

    Added 2026-09-02 with the six-hour disagreement above. `home/get_time` is
    one cheap read that returns RimWorld's own answers -- `hourInteger`,
    `dayOfQuadrumDisplay`, `quadrum`, `year`, `dateFull` -- computed from
    absolute ticks at the map's longitude. Prefer it whenever the bridge
    answers; fall back to `ticks_phrase`, which labels itself, so the degraded
    line can never be read as the same claim as the real one.
    """
    if have is None or "home/get_time" in have:
        try:
            g = rim.game("home/get_time", {}, strict=False)
        except Exception:
            g = None
        if isinstance(g, dict) and g.get("hourInteger") is not None:
            date = g.get("dateFull") or "%s %s %s" % (
                g.get("dayOfQuadrumDisplay"), g.get("quadrum"), g.get("year"))
            return ("tick %s = %s, %02dh (game clock)"
                    % (g.get("ticksGame", t), date, g["hourInteger"]))
    return ticks_phrase(t)


SESSION = os.path.join(STATE, "session.json")


def session_save(name=None):
    """Remember which save is loaded, because the BRIDGE CANNOT TELL YOU.

    There is no tool that names the currently-loaded save or the colony:
    `get_game_info` gives `ticksGame` / `mapCount` / `status`, `list_saves` lists
    files on disk. So the only honest source is "the last thing setup.py loaded",
    written here and marked stale if the tick count ever goes BACKWARDS (which is
    what a load of an older save looks like from outside).
    """
    try:
        with open(SESSION, "r", encoding="utf-8") as f:
            cur = json.load(f)
    except Exception:
        cur = {}
    if name is not None:
        pids = rimworld_pids()
        cur = {"save": name, "loadedAt": time.time(),
               "loadedAtTick": game_info().get("ticksGame"),
               "pid": pids[0] if len(pids) == 1 else None}
        try:
            os.makedirs(STATE, exist_ok=True)
            with open(SESSION, "w", encoding="utf-8") as f:
                json.dump(cur, f, indent=1)
        except OSError:
            pass
        return name
    s = cur.get("save")
    if not s:
        return None
    # Two ways this name can be a lie, both cheap to catch:
    pids = rimworld_pids()
    if cur.get("pid") and pids and cur["pid"] not in pids:
        return "%s (STALE: RimWorld restarted since that load)" % s
    t0, t = cur.get("loadedAtTick"), game_info().get("ticksGame")
    if t0 is not None and t is not None and t < t0:
        return "%s (STALE: tick went backwards -- an older save was loaded)" % s
    return s


def saves_matching(prefix="Lampblack"):
    """[(mtime, name)] for saves whose name starts with `prefix`, NEWEST FIRST.

    Sorted by mtime, not by name: alphabetical order puts "day 6" after "day 21"
    and the first version of this printed the four oldest saves as the offer.
    The profile is shared with Sol, so `Wayside - *` is filtered out by the
    prefix and the Autosave-1..5 rotation is filtered out everywhere -- either
    colony overwrites those (the reason for the "name your saves" rule).
    """
    try:
        return sorted(((os.path.getmtime(os.path.join(SAVES, f)), f[:-4])
                       for f in os.listdir(SAVES)
                       if f.endswith(".rws")
                       and f.lower().startswith(prefix.lower())),
                      reverse=True)
    except OSError:
        return []


def lampblack_saves():
    """Just the names, newest first. The STATUS block's offer list.

    Voided saves are left out: this list is an OFFER, and offering a save that
    `--newest` would skip and `--load` would refuse is an invitation to waste a
    load. They are accounted for on the `voided` line instead, so nothing
    vanishes without being named.
    """
    return [n for _, n in saves_matching("Lampblack") if not void_reason(n)]


# ------------------------------------------------------------------- voiding

VOID_EXT = ".void"


def void_path(name):
    """The sidecar for a save name (no .rws): `<save name>.void` in SAVES."""
    return os.path.join(SAVES, name + VOID_EXT)


def void_reason(name):
    """Why `name` is void, or None when it is not.

    PRESENCE voids the save; the first line is only the explanation. A sidecar
    that exists and cannot be read still voids it -- an unreadable mark must
    never resolve to "fine", because the failure this guards is silent and
    total when it happens.
    """
    p = void_path(name)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            first = f.readline().strip()
    except OSError as e:
        return "(sidecar exists but could not be read: %s)" % e
    return first or "(no reason given)"


def voided_saves():
    """[(name, reason)] for every sidecar in SAVES, by name. Cheap, no game."""
    out = []
    try:
        files = sorted(os.listdir(SAVES))
    except OSError:
        return out
    for f in files:
        if f.endswith(VOID_EXT):
            n = f[:-len(VOID_EXT)]
            out.append((n, void_reason(n)))
    return out


def void_line(pairs, lead):
    """One STATUS line naming voided saves and their reasons, capped.

    Capped rather than dropped: the 14-line budget is real, but a skip nobody
    can see is the bug being fixed, so the count is always exact even when the
    names do not all fit.
    """
    bits = ["%r (%s)" % (n, r) for n, r in pairs]
    line = "%s: %s" % (lead, "; ".join(bits))
    if len(line) <= 118:
        return line
    kept = []
    for i, b in enumerate(bits):
        if len("%s: %s" % (lead, "; ".join(kept + [b]))) > 96:
            break
        kept.append(b)
    return "%s: %s (+%d more, see profile\\Saves\\*.void)" % (
        lead, "; ".join(kept) if kept else "", len(bits) - len(kept))


def split_voided(fs):
    """(live, voided) out of a saves_matching() list, order preserved."""
    live, dead = [], []
    for mt, name in fs:
        (dead if void_reason(name) else live).append((mt, name))
    return live, dead


def mark_void(name, reason):
    """`--void`: write the sidecar. -> exit code. Touches no save file.

    Refuses when there is no such save, and says which names are close: a typo
    that silently created a sidecar for a save nobody has would be a mark that
    protects nothing, which is worse than no mark at all.
    """
    if not os.path.exists(os.path.join(SAVES, name + ".rws")):
        print("no save named %r in %s" % (name, SAVES), file=sys.stderr)
        near = [n for _, n in saves_matching(name.split(" - ")[0][:6] or name[:6])]
        if near:
            print("did you mean: %s" % "; ".join(repr(n) for n in near[:6]),
                  file=sys.stderr)
        return 1
    text = (reason or "").strip() or "(no reason given)"
    try:
        with open(void_path(name), "w", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError as e:
        print("could not write %s: %s" % (void_path(name), e), file=sys.stderr)
        return 1
    print("voided %r: %s" % (name, text))
    print("  --newest will skip it and say so; --load refuses it without "
          "--force-void. Undo with: python setup.py --unvoid %r" % name)
    return 0


def unmark_void(name):
    """`--unvoid`: remove the sidecar. -> exit code. Touches no save file."""
    p = void_path(name)
    if not os.path.exists(p):
        print("%r is not marked void (no %s)" % (name, os.path.basename(p)),
              file=sys.stderr)
        return 1
    try:
        os.remove(p)
    except OSError as e:
        print("could not remove %s: %s" % (p, e), file=sys.stderr)
        return 1
    print("unvoided %r -- it is loadable and --newest can pick it again" % name)
    return 0


def void_cli(argv):
    """`--void` / `--unvoid`, handled before anything is started."""
    def after(flag):
        i = argv.index(flag)
        v = argv[i + 1] if len(argv) > i + 1 else None
        return None if (v is None or v.startswith("--")) else v

    if "--void" in argv:
        name = after("--void")
        if not name:
            print('--void needs a save name: --void "<save name>" '
                  '--reason "<why>"', file=sys.stderr)
            return 1
        return mark_void(name, after("--reason") if "--reason" in argv else None)
    name = after("--unvoid")
    if not name:
        print('--unvoid needs a save name', file=sys.stderr)
        return 1
    return unmark_void(name)


def refuse_if_void(name, force):
    """`--load <name>` of a voided save. -> a refusal sentence, or None."""
    reason = void_reason(name)
    if reason is None:
        return None
    if force:
        return None
    return ("REFUSED: %r is marked VOID -- %s\n"
            "Nothing was started. Load it anyway with --force-void, or take "
            "the mark off with:\n"
            "    python setup.py --unvoid %r" % (name, reason, name))


def describe_save(name, mt):
    return "%r  written %s (%s ago)" % (
        name, time.strftime("%Y-%m-%d %H:%M", time.localtime(mt)),
        age_phrase(time.time() - mt))


def age_phrase(sec):
    sec = max(0, int(sec))
    if sec < 90:
        return "%ds" % sec
    if sec < 5400:
        return "%dm" % (sec // 60)
    if sec < 172800:
        return "%dh%02dm" % (sec // 3600, (sec % 3600) // 60)
    return "%dd" % (sec // 86400)


def resolve_newest(prefix, notes):
    """The newest save matching `prefix`, RE-READ NOW. -> name or None.

    2026-09-01, on camera: errata read a directory listing, M
    saved-and-quit while he was reading it, the newest file on disk changed
    underneath him, and he loaded day 34 while her colony sat at day 35 under a
    different name. She caught it. *"A nice little lesson about reading the
    world once and then acting on the memory of it."*

    `errata.md` step 2 said load *"the newest Lampblack save"*, which is an
    instruction to a reader with a memory, and a memory is the thing that went
    stale. So the resolution happens HERE, one function call before the load,
    and the name and mtime it chose go into the STATUS block -- which is the
    only artefact of a session anyone re-reads. Nobody has to be trusted to
    have looked recently, because nothing looked earlier.
    """
    fs = saves_matching(prefix)
    live, dead = split_voided(fs)
    if dead:
        # Named, not merely counted. A voided save being skipped is exactly the
        # kind of thing that must never happen quietly -- the whole mechanism
        # exists because a silent pick loaded a repudiated colony.
        pairs = [(n, void_reason(n)) for _, n in dead]
        notes["voided"] = void_line(pairs, "skipped %d voided" % len(dead))
        for n, r in pairs:
            print("... skipping voided save %r: %s" % (n, r), file=sys.stderr)
    if not fs:
        notes["save"] = "NO save matching %r in %s" % (prefix, SAVES)
        return None
    if not live:
        notes["save"] = ("NO loadable save matching %r: all %d of them are "
                         "VOIDED. --unvoid one, or name it with --load "
                         "--force-void." % (prefix, len(dead)))
        return None
    mt, name = live[0]
    notes["save"] = "%s -- newest of %d matching %r, re-read at load time" % (
        describe_save(name, mt), len(live), prefix)
    return name


def warn_if_stale(name, notes):
    """`--load <literal name>`: is there a NEWER matching save than the one asked for?

    A warning, never a refusal -- loading an older save on purpose is a real
    thing to want (the Feb-17 Terraria lesson: the newest file is not always
    the right file). But it must be said out loud in the STATUS block, because
    the failure it guards is silent by construction.
    """
    prefix = name.split(" - ")[0] or name[:9]
    # Voided saves are not candidates for "there is a newer one": `--newest`
    # would skip them, so pointing at one would send the reader somewhere the
    # documented path refuses to go.
    everything = saves_matching(prefix)
    fs, _dead = split_voided(everything)
    if not fs:
        return
    mt, newest = fs[0]
    # `mine` is looked up in the UNFILTERED list: with --force-void the save
    # being loaded is itself voided, and reporting it as "(not on disk!)"
    # would be a false alarm about the one file we are certain of.
    mine = [t for t, n in everything if n == name]
    if newest == name:
        notes["save"] = "%s -- newest matching %r" % (describe_save(name, mt), prefix)
    else:
        notes["save"] = ("%s ** but %s is NEWER -- re-check, or use "
                         "--newest %s **"
                         % (describe_save(name, mine[0]) if mine
                            else "%r (not on disk!)" % name,
                            describe_save(newest, mt), prefix))


# --------------------------------------------------------------- the steps

def ensure_gabs(notes):
    if gabs_alive():
        notes["gabs"] = "up (was already running)"
        return True
    print("... GABS not answering on 8080, starting it", file=sys.stderr)
    if not start_gabs():
        notes["gabs"] = "FAILED to start -- see state\\gabs.log"
        return False
    notes["gabs"] = "up (started by setup.py, log state\\gabs.log)"
    return True


def ensure_game(notes):
    """Returns 0 ok, 2 = M's game may be running (stop, touch nothing)."""
    rim.init()
    if bridge_connected():
        pids = rimworld_pids()
        notes["game"] = "connected (was already up%s)" % (
            ", pid %d" % pids[0] if len(pids) == 1 else "")
        return 0

    pids = rimworld_pids()
    if pids:
        notes["game"] = "*** UNCONNECTED RimWorld running, pid %s ***" % (
            ",".join(map(str, pids)))
        print("\n*** STOP. A RimWorldWin64 process is running and our bridge is "
              "not connected to it.\n"
              "*** That may be M playing in her own profile -- same exe "
              "name, different save folder.\n"
              "*** games_stop would kill it (gabs-config stops by process name) "
              "and games_start would\n"
              "*** launch a second one over it. Doing neither. If this really is "
              "a stale agent game,\n"
              "*** a person has to say so.\n", file=sys.stderr)
        return 2

    print("... launching RimWorld through GABS", file=sys.stderr)
    rim.tool("games_start", {"gameId": rim.GAME})
    t0 = time.time()
    tried_connect = False
    while time.time() - t0 < CONNECT_TIMEOUT:
        if bridge_connected():
            notes["game"] = "connected in %ds (launched by setup.py)" % (time.time() - t0)
            return 0
        if not tried_connect and time.time() - t0 > 45:
            # games_start usually auto-connects; nudge it once rather than
            # hammering, because a connect attempt against a game still on the
            # splash screen just fails.
            tried_connect = True
            try:
                rim.tool("games_connect", {"gameId": rim.GAME})
            except Exception:
                pass
        time.sleep(3.0)
    notes["game"] = "launched but bridge never connected in %ds" % CONNECT_TIMEOUT
    return 1


# --- the listener window ------------------------------------------------
# M watches the stream from her phone, and Remote Control for Claude Code
# is the only app she has that reaches this PC. So when a playthrough starts, a
# second Claude session opens in an ordinary console she can type into from
# there; its whole job is to relay what she says into the run (listener.md,
# relay.py). It is spawned here rather than by hand because the moment it is
# needed -- something going wrong mid-stream -- is exactly the moment nobody is
# at the keyboard to start it.

# Any cmd.exe still holding the bat is the window: the launcher `cmd /c start`
# exits at once so its pid is worthless, and the window TITLE is not usable
# either -- Claude Code rewrites it as soon as it is running. The command line
# is the one thing that stays put for the life of the console.
LISTENER_PROBE = ("@(Get-CimInstance Win32_Process | ? { $_.Name -eq 'cmd.exe' "
                  "-and $_.CommandLine -like '*listener.bat*' }).Count")


def listener_running():
    """True only on a confident yes. Every failure reads as "no", because this
    decides whether to spawn and a stray extra window is a smaller problem than
    setup.py dying over one."""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", LISTENER_PROBE],
                             capture_output=True, text=True, timeout=30)
    except Exception:
        return False
    try:
        return int((out.stdout or "0").strip()) > 0
    except ValueError:
        return False


def ensure_listener():
    """Idempotent by design -- setup.py is meant to be re-run freely, and a
    second run must not stack up a second window. Never raises: nothing here is
    worth failing a working game over."""
    try:
        if not os.path.exists(LISTENER_BAT):
            print("... no listener.bat; M has no phone channel this run",
                  file=sys.stderr)
            return
        if listener_running():
            return
        # `start ""` gives the new console its own window; the outer cmd /c
        # exits immediately, and CREATE_NO_WINDOW keeps IT from flashing one.
        subprocess.Popen(["cmd", "/c", "start", "", "cmd", "/k", "call", LISTENER_BAT],
                         cwd=HERE, creationflags=0x08000000)
        print("... opened the listener window (M's phone channel)",
              file=sys.stderr)
    except Exception as e:
        print("... listener window did not open (%s) -- carrying on" % e,
              file=sys.stderr)


def check_companion(notes):
    """home/ping. The DLL is not discoverable through games_tool_names.

    GABS caches its tool surface at connect time and the companion registers
    after, so `home/ping` is absent from the list and works when called. Never
    infer "not installed" from the listing -- call it.
    """
    try:
        r = rim.game("home/ping", {}, strict=False)
    except Exception as e:
        r = {"success": False, "message": str(e)}
    if isinstance(r, dict) and r.get("success"):
        notes["companion"] = "home/ping ok, sdk %s%s" % (
            r.get("sdkVersion", "?"),
            "" if r.get("onMainThread") is False else
            " (onMainThread=%r -- expected False)" % r.get("onMainThread"))
        return True
    notes["companion"] = ("MISSING -- home/ping did not answer. map.py will run "
                          "** DEGRADED **")
    print("\n*** home/ping failed: %s\n"
          "*** Expected HomeBridge.BridgeTools.dll at\n"
          "***   C:\\Program Files (x86)\\Steam\\steamapps\\common\\RimWorld\\"
          "BridgeTools\\HomeBridge\\\n"
          "*** Companions are discovered ONCE at bridge startup, so installing it "
          "needs a RimWorld restart.\n"
          "*** Install steps: C:\\Home\\rimworld\\companion\\INSTALL.md\n"
          "*** (Not installing anything from here -- that is a deliberate "
          "human-in-the-loop step.)\n"
          % (r.get("message") if isinstance(r, dict) else r), file=sys.stderr)
    return False


def load_save(name, have, notes):
    """Load a named save and come back PAUSED.

    The design doc says the game runs unpaused after `load_game_ready`, which is
    true and is not the whole story: the tool has a `pauseIfNeeded` parameter,
    default **false**. Nobody was passing it. So pass it -- and then still pause
    explicitly and read the speed back, because a flag whose effect you did not
    observe is a claim, not a fact. That distinction is the whole reason
    `map.py` prints an ACCOUNTING block.

    Readiness: `playable` rather than the default `mapData`, so the call returns
    when a colonist can actually be given an order rather than when the grid
    exists.
    """
    tool = first_tool(["rimworld/load_game_ready", "rimworld/load_game"], have)
    if not tool:
        notes["load"] = "no load tool on this bridge (looked for load_game_ready)"
        return False
    print("... loading %r" % name, file=sys.stderr)
    args = {"saveName": name}
    if tool.endswith("load_game_ready"):
        args.update(readiness="playable", pauseIfNeeded=True, timeoutMs=180000)
    r = rim.game(tool, args, strict=False)
    if not (isinstance(r, dict) and r.get("success")):
        notes["load"] = "LOAD FAILED: %s" % (
            r.get("message") if isinstance(r, dict) else r)
        return False
    notes["load"] = "%r via %s (pauseIfNeeded)" % (name, tool.split("/")[-1])
    return True


def game_info():
    try:
        gi = rim.game("rimworld/get_game_info", {}, strict=False)
    except Exception:
        return {}
    return gi if isinstance(gi, dict) else {}


def observe_speed(gap=0.7):
    """(is_paused, ticks, ticks_per_second, ticks_moved) measured, not asked.

    **Nothing CHEAP on this bridge reports the game speed.** `get_game_info`
    carries `ticksGame`, `mapCount`, `status`, `selectedPawns` and nothing
    else; `get_ui_state` has `windowsForcePause` (a modal flag, not the speed)
    and no speed field; `pause_game` reports `paused: true` about the call it
    just made, which is a claim about an instant, not a state you can re-read
    later.

    The correction (2026-09-01): **`rimbridge/get_bridge_status` DOES report
    it** -- `state.paused` and `state.timeSpeed` are right there. What it also
    reports is everything else about the bridge, in one fat payload, which is
    why it is the right call for a one-off "what is going on" and the wrong one
    to put in a loop. This file's older claim that nothing reports speed was
    simply wrong, and it was repeated downstream; if you need one honest answer
    now, ask `get_bridge_status`.

    So for polling, measure it: two `ticksGame` reads across a short gap. At
    Normal that is ~60 ticks/s, at Superfast far more, at Paused exactly zero.
    Cost is two cheap calls and 0.7s. (The stream overlay does the same
    inference from the ticks the play loop pushes it, without any bridge call
    of its own -- see `stream-overlay\\server.py`.)
    """
    a = game_info().get("ticksGame")
    if a is None:
        return None, None, None, None
    t0 = time.time()
    time.sleep(gap)
    b = game_info().get("ticksGame")
    dt = time.time() - t0
    if b is None:
        return None, a, None, None
    return (b == a), b, (b - a) / dt, (b - a)


def pause_verified(have, tries=4):
    """Pause and keep pausing until the ticks actually stop. Returns (ok, tries).

    Measured 2026-08-30, and it contradicts the obvious reading of the tool:
    `load_game_ready` with **`pauseIfNeeded: true`** followed by an explicit
    `pause_game {pause:true}` still left the colony running at 60 ticks/s. The
    load returns at `playable` readiness, and RimWorld's own map-entry code sets
    the speed AFTER that, so an early pause is simply overwritten. One pause is a
    race you lose quietly -- the colony ages while the Core narrates over a
    STATUS block that says PAUSED.

    Hence: pause, watch the ticks, pause again. Cheap, and the loop exits on
    evidence.
    """
    for n in range(1, tries + 1):
        for tool, args in (("rimworld/pause_game", {"pause": True}),
                           ("rimworld/set_time_speed", {"speed": "Paused"})):
            if tool in have:
                try:
                    rim.game(tool, args, strict=False)
                except Exception:
                    pass
        stopped = observe_speed()[0]
        if stopped:
            return True, n
        time.sleep(0.5)
    return False, tries


SAMPLE_GAP = 0.7      # seconds each sample spans
SAMPLE_APART = 1.0    # seconds between the two samples


def speed_phrase(second_look=True):
    """The speed line for STATUS -- worded as the SAMPLE it actually is.

    2026-09-01, live: errata told M the clock was "RUNNING ~170 ticks/s"
    while she watched a frozen picture. Both were true. The play loop was
    halting and restarting on the colony's own tame warg (see
    `run.py:_wild_hunters`), and a 0.7-second sample landing inside one of the
    running windows honestly reads 170. The instrument was not wrong. The
    SENTENCE was: it reported a point sample in the present continuous, and
    "RUNNING" is a claim about now and next, not about 0.7 seconds ago. What
    found the real bug was M's phrasing -- *"like it ran for a half
    second"* -- because frozen and stuttering are different faults and the
    report had flattened them into one word.

    So this says what was measured, in ticks, and takes a SECOND sample a
    second later. Two samples cannot prove a stream is smooth; a second one
    showing zero proves it is not, which is exactly the case that was missed.
    Cost is 4 cheap calls and ~2.4s on a once-per-STATUS path -- never in a
    loop. `run.py` has the other half of this signal: a repeated `ran 0 ticks`
    line in its per-call report is the same fault, seen from inside.
    """
    stopped, tick, tps, moved = observe_speed(SAMPLE_GAP)
    if stopped is None:
        return "no game", None
    if not second_look:
        return ("ticked %d in a %.1fs sample" % (moved, SAMPLE_GAP)), tick
    time.sleep(SAMPLE_APART)
    stopped2, tick2, tps2, moved2 = observe_speed(SAMPLE_GAP)
    if stopped2 is None:            # the game went away between samples
        return ("ticked %d in a %.1fs sample, then the game stopped answering"
                % (moved, SAMPLE_GAP)), tick
    tick = tick2
    if not moved and not moved2:
        return ("PAUSED (0 ticks in two %.1fs samples, %.0fs apart)"
                % (SAMPLE_GAP, SAMPLE_APART)), tick
    if moved and not moved2:
        return ("*** STALLED: %d ticks in one %.1fs sample, 0 in the next ***"
                % (moved, SAMPLE_GAP)), tick
    if moved2 and not moved:
        # Not starred. This one is often benign -- a sample that straddles a
        # pause boundary, e.g. another session stepping the clock -- whereas
        # STALLED above is the one that was on screen while the report said
        # RUNNING. Only the dangerous case shouts.
        return ("uneven: 0 ticks in one %.1fs sample, %d in the next"
                % (SAMPLE_GAP, moved2)), tick
    return ("~%d ticks/s (two %.1fs samples, %.0fs apart: %d then %d ticks)"
            % (round(((tps or 0) + (tps2 or 0)) / 2.0), SAMPLE_GAP,
               SAMPLE_APART, moved, moved2)), tick


# --------------------------------------------------------------- the report

def binding_note():
    """One line about state\\session-runtime.json, or None when it is live.

    Setup takes its "already up / connected" path when errata.bat is launched
    at a running game -- which is exactly the warm start where the previous
    session's binding survives, dead, and every `hands-claim` refuses. Setup is
    the first thing the session runs, so it is the first place that can say so.
    """
    try:
        import runtime_binding
        state, line = runtime_binding.describe(
            (runtime_binding.note() or {}).get("lastCoreSession"))
        if state == "live":
            return None
        return "%s -- %s. Repair: python stream.py bind-check --repair" % (
            state.upper(), line)
    except Exception as ex:
        return "unreadable (%s: %s)" % (type(ex).__name__, ex)


def status_block(notes, have):
    """<= 14 lines. Everything that does not change a decision goes to stderr."""
    lines = ["== RIMWORLD STACK == %s" % time.strftime("%H:%M:%S")]
    lines.append("gabs      : %s" % notes.get("gabs", "?"))
    _binding = binding_note()
    if _binding:
        lines.append("!! binding : %s" % _binding)
    lines.append("game      : %s" % notes.get("game", "?"))
    lines.append("companion : %s" % notes.get("companion", "?"))
    if "save" in notes:
        lines.append("save      : %s" % notes["save"])
    if "load" in notes:
        lines.append("load      : %s" % notes["load"])
    # One line, only when there is something to say. `--newest` fills this in
    # with what it skipped; on a plain `--status` it is read off the disk, so a
    # voided save is visible without having to try to load one.
    if "voided" not in notes:
        pairs = voided_saves()
        if pairs:
            notes["voided"] = void_line(pairs, "%d voided" % len(pairs))
    if "voided" in notes:
        lines.append("voided    : %s" % notes["voided"])
    if ("FAILED" in notes.get("gabs", "") or "DOWN" in notes.get("gabs", "")
            or "not connected" in notes.get("game", "")):
        # Don't go on to ask the game anything. With no server the colony
        # section would print "NONE loaded (main menu)" and a list of saves,
        # which reads as a healthy stack at the menu -- the wrong diagnosis
        # dressed as a fact.
        return lines

    spd, tick = speed_phrase()
    save = session_save()

    if tick is None:
        lines.append("colony    : NONE loaded (main menu). Newest Lampblack saves:")
        saves = lampblack_saves()
        for s in saves[:4]:
            lines.append("            %s" % s)
        if len(saves) > 4:
            lines.append("            (+%d older in profile\\Saves)" % (len(saves) - 4))
        return lines

    lines.append("colony    : %s | %s | %s"
                 % (save or "save name unknown", clock_phrase(tick, have), spd))

    try:
        cols = rim.game("rimworld/list_colonists", {}).get("colonists") or []
    except Exception:
        cols = []
    bits = []
    for c in cols:
        if c.get("dead"):
            continue
        flag = ""
        if c.get("downed"):
            flag = " DOWNED"
        elif c.get("mentalState"):
            flag = " [%s]" % c["mentalState"]
        p = c.get("position") or {}
        bits.append("%s@%s,%s%s" % (plain(c.get("name")), p.get("x"), p.get("z"), flag))
    lines.append("colonists : %s" % ("  ".join(bits) if bits else "none alive"))

    try:
        alerts = rim.game("rimworld/list_alerts", {}).get("alerts") or []
    except Exception:
        alerts = []
    # `priority == "High"` was wrong at BOTH ends, fixed 2026-09-02 with the same
    # bug in `watch.py`. `AlertPriority` is an ORDERED enum and the companion
    # compares it as one (`priority < min`, PlayUntilEventTool.cs:871), so equality
    # dropped Medium below and CRITICAL above -- the loudest thing the game can say
    # printed here as "no High alerts (checked, 2 total)", in the block a session
    # reads first and re-reads all day. Match the set; fold the minor ones onto one
    # capped line so they are visible without costing the 14-line budget. The long
    # version of this reasoning is the alert block in `watch.py:step()`.
    #
    # LOUD_ALERTS is copied there rather than imported: setup.py imports only `rim`,
    # and `--status` is what you run WHEN THE STACK IS BROKEN, so pulling in the play
    # loop (watch -> letters -> ui, map) would let a failure in any of them take out
    # the diagnostic. One tuple, one pointer, no new dependency edge.
    loud = [plain(a.get("label")) for a in alerts
            if a.get("priority") in LOUD_ALERTS]
    minor = [a for a in alerts if a.get("priority") not in LOUD_ALERTS]
    lines.append("alerts    : %s" % ("; ".join(loud) if loud else
                                     "no High/Critical alerts (checked, %d total)"
                                     % len(alerts)))
    if minor:
        lines.append("            (%s) %s"
                     % ("/".join(sorted({a.get("priority") or "?" for a in minor})),
                        "; ".join(plain(a.get("label")) or "?" for a in minor[:8])
                        + (" (+%d more)" % (len(minor) - 8) if len(minor) > 8 else "")))

    try:
        letters = rim.game("rimworld/list_letters", {}).get("letters") or []
    except Exception:
        letters = []
    if letters:
        lines.append("letters   : %s" % "; ".join(plain(l.get("label"))
                                                  for l in letters[-3:]))
    return lines


def main():
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        # Prints and starts NOTHING. Before this existed, `--help` fell through
        # to the full bring-up and launched GABS and RimWorld at whoever typed it.
        print(__doc__)
        return 0
    if "--void" in argv or "--unvoid" in argv:
        # Disk only. Nothing is started, so a save can be marked or unmarked
        # with the game closed, which is when you usually want to.
        return void_cli(argv)
    want_load = None
    want_newest = None
    if "--load" in argv:
        i = argv.index("--load")
        want_load = argv[i + 1] if len(argv) > i + 1 else None
        if not want_load:
            print("--load needs a save name", file=sys.stderr)
            return 1
    if "--newest" in argv:
        i = argv.index("--newest")
        want_newest = argv[i + 1] if len(argv) > i + 1 else "Lampblack"
        if want_newest.startswith("-"):
            want_newest = "Lampblack"
        if want_load:
            print("--load and --newest are two answers to one question; pick one",
                  file=sys.stderr)
            return 1
    if want_load:
        # Checked BEFORE anything is started: refusing after launching GABS and
        # RimWorld would cost a minute to say no.
        refusal = refuse_if_void(want_load, "--force-void" in argv)
        if refusal:
            print(refusal, file=sys.stderr)
            print("load      : %s" % refusal.splitlines()[0])
            return 1
    report_only = "--status" in argv

    os.makedirs(os.path.join(STATE, "shots"), exist_ok=True)
    notes = {}
    t0 = time.time()

    if report_only:
        if not gabs_alive():
            notes["gabs"] = "DOWN (nothing answering on 8080)"
            print("\n".join(status_block(notes, set())))
            return 1
        notes["gabs"] = "up"
        rim.init()
        if not bridge_connected():
            # Do NOT go on to home/ping here. With no game there is no bridge and
            # every tool is "not found", so the companion check would print a
            # five-line "the DLL is missing, reinstall it" alarm about a DLL that
            # is fine. Reporting the wrong cause is worse than reporting less.
            notes["game"] = "not connected (no RimWorld, or not started via GABS)"
            notes["companion"] = "not checked -- needs a connected game"
            print("\n".join(status_block(notes, set())))
            return 1
        notes["game"] = "connected"
        have = game_tools()
        ok = check_companion(notes)
        print("\n".join(status_block(notes, have)))
        return 0 if ok else 1

    if not ensure_gabs(notes):
        print("\n".join(status_block(notes, set())))
        return 1

    rc = ensure_game(notes)
    if rc:
        print("\n".join(status_block(notes, set())))
        return rc

    # The game is up, so M's way in should be too.
    ensure_listener()

    have = game_tools()
    ok_companion = check_companion(notes)

    if want_newest:
        # Resolved HERE, after the game is up -- as late as it can possibly be,
        # which is the whole point. See `resolve_newest`.
        want_load = resolve_newest(want_newest, notes)
    elif want_load:
        warn_if_stale(want_load, notes)
        forced = void_reason(want_load)
        if forced:
            notes["voided"] = ("%r is VOID (%s) -- loaded anyway, --force-void "
                               "was passed" % (want_load, forced))

    if want_load:
        if load_save(want_load, have, notes):
            session_save(want_load)
            ok, n = pause_verified(have)
            if ok:
                notes["load"] += "; paused, %d attempt%s" % (n, "" if n == 1 else "s")
            else:
                notes["load"] += "; *** COULD NOT PAUSE, colony is RUNNING ***"

    lines = status_block(notes, have)
    lines.append("setup     : %.0fs" % (time.time() - t0))
    print("\n".join(lines[:14]))
    if len(lines) > 14:
        print("... (%d further lines suppressed; 14 is the context budget)"
              % (len(lines) - 14), file=sys.stderr)
    return 0 if ok_companion else 1


if __name__ == "__main__":
    sys.exit(main())
