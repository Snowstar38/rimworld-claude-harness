"""The camera: who owns it, where home is, and how it gets back there.

  python cam.py                      # where is the camera, how much of the map is in it
  python cam.py probe                # the bridge's own schemas for every camera tool
  python cam.py wide                 # wide shot, then come down on the base
  python cam.py wide --glide         # ... stepped zoom instead of a snap. STROBES; don't.
  python cam.py base [x z]           # show or PIN the coordinate the camera calls home
  python cam.py go <x> <z> [--zoom <n>]   # HANDS: point the camera, claim it for the turn
  python cam.py zoom <rootSize>      # set the zoom only, leaving the centre alone
  python cam.py --help               # this text

Any of the above also takes `--say "..."` and `--mood <mood>`, which narrate to
the stream overlay once the command has run. See `overlay_client.py`.

A near frame sees ~7% of the map, because a screenshot shows wherever the camera
was last left. The wide frame is the only thing here that sees the other 93%.

## Root size, in numbers that mean something

Root size is the half-height of the view in cells, so it is a zoom LEVEL, not a
factor: smaller is closer.

  11-15   one pawn and the tile they stand on
  24-35   a colony view -- the base and its immediate surroundings
  45-60   most of a 250x250 map's quadrant; too far out to read a pawn
  ~127    the whole 250x250 map (needs `set_camera_zoom_extension`; `wide` does it)

60 is 45% of the map in frame and reads as "nothing is happening". For a stream,
come down to 24-35 whenever the turn has a subject.

## Owner and home

`camlock.py` has the model. Two consequences land in this file: **Hands wins
instantly** -- a claim aborts a glide mid-flight and the camera is left exactly
where the interrupt caught it, never tidied -- and **home is a pinned
coordinate, not a choice** (`base_cell()`), so the Lookout's move ends on the
base rather than back where it started.

Every landing is checked against the camera's own viewRect before it is
believed, and only a real landing queues the notice to Hands. `_need()` reads
each tool's real input schema via `games_tool_detail` rather than hardcoding
field names.

**Extended zoom is opt-in and we opt back out.** A 250x250 map needs a root size
of ~127 and the stock ceiling is far below it, so `set_camera_zoom_extension`
lifts it for the shot and drops it after -- a person may alt-tab back into this
game. The report states the fraction of the map actually in frame rather than
claiming "the map".
"""
import json
import os
import pathlib
import re
import sys
import time

import camlock
import rim

HERE = os.path.dirname(os.path.abspath(__file__))
SIZE_CACHE = os.path.join(HERE, "state", "mapsize.json")
SCREEN_ASPECT = 4096.0 / 2160.0

# GLIDING IS OFF BY DEFAULT AND SHOULD STAY OFF. The bridge only offers discrete
# jumps, so a "glide" is N hard cuts: at 20 steps over 5s that is 4 Hz, which
# reads as strobing rather than panning (M, watching the first live run:
# "glad i dont have epilepsy"). Smooth needs 30-60 Hz -- 2-4 bridge calls per
# rendered frame -- which would stutter the game far worse than the thing it is
# trying to prettify. A snap is one cut, takes 0.6s end to end, and is what a
# screenshot tool should do anyway.
GLIDE_SECONDS = 5.0
GLIDE_STEPS = 20

_schemas = {}


# ------------------------------------------------------------------ schemas ---

PARAM = re.compile(r"^\s*-\s*`([A-Za-z0-9_]+)`\s*\(([^)]*)\)", re.M)


def schema(toolname):
    """-> {parameter name: type} for a bridge tool, or {} if it cannot be read.

    `games_tool_detail` answers with prose, not JSON -- a header, a description,
    then an indented "Parameters:" list of ``- `name` (type): description``
    lines. Parse that; fall back to a real inputSchema dict if a future GABS
    returns one.
    """
    if toolname not in _schemas:
        props = {}
        try:
            d = rim.tool("games_tool_detail", {"gameId": rim.GAME, "tool": toolname})
            if isinstance(d, str):
                props = dict((m.group(1), m.group(2)) for m in PARAM.finditer(d))
            elif isinstance(d, dict):
                for key in ("inputSchema", "input_schema", "schema", "parameters"):
                    s = d.get(key)
                    if isinstance(s, dict) and isinstance(s.get("properties"), dict):
                        props = s["properties"]
                        break
        except Exception:
            props = {}
        _schemas[toolname] = props
    return _schemas[toolname]


def _pick(props, *names):
    """First candidate the tool actually accepts, matched case-insensitively."""
    low = dict((k.lower(), k) for k in props)
    for n in names:
        if n.lower() in low:
            return low[n.lower()]
    return None


def _need(toolname, *names):
    props = schema(toolname)
    got = _pick(props, *names)
    if got is None:
        raise RuntimeError("%s has no field like %s (it takes: %s)"
                           % (toolname, "/".join(names),
                              ", ".join(sorted(props)) or "nothing this could read"))
    return got


# -------------------------------------------------------------------- state ---

def state():
    """The camera as the bridge reports it. Read-only."""
    s = rim.game("rimworld/get_camera_state", {}, strict=False)
    if not isinstance(s, dict):
        raise RuntimeError("get_camera_state did not answer: %s" % str(s)[:160])
    return s


def _root(s):
    """The camera's zoom, whatever this build calls it."""
    for k in ("rootSize", "zoomRootSize", "size", "orthographicSize"):
        if isinstance(s.get(k), (int, float)) and not isinstance(s.get(k), bool):
            return float(s[k])
    for outer in ("camera", "zoom", "state"):
        v = s.get(outer)
        if isinstance(v, dict):
            r = _root(v)
            if r:
                return r
    return None


def _view(s):
    v = s.get("viewRect")
    return v if isinstance(v, dict) else None


def _centre(s):
    """Where the camera is pointing, from viewRect -- present on every build."""
    v = _view(s)
    if not v:
        return None
    return (int(round((v["minX"] + v["maxX"]) / 2.0)),
            int(round((v["minZ"] + v["maxZ"]) / 2.0)))


def map_size():
    """(w, h), cached on disk per save -- rim.map_size() is a ~20-call binary search.

    Keyed by the save name, so loading a different colony re-probes instead of
    inheriting the last map's bounds. A cache that could be silently wrong about
    the edge of the world would put the wide shot's centre in the wrong place.
    """
    try:
        with open(os.path.join(HERE, "state", "session.json"), encoding="utf-8") as f:
            save = json.load(f).get("save") or "?"
    except (OSError, ValueError):
        save = "?"
    try:
        with open(SIZE_CACHE, encoding="utf-8") as f:
            c = json.load(f)
        if c.get("save") == save:
            return tuple(c["size"])
    except (OSError, ValueError, KeyError):
        pass
    size = rim.map_size()
    try:
        os.makedirs(os.path.dirname(SIZE_CACHE), exist_ok=True)
        with open(SIZE_CACHE, "w", encoding="utf-8") as f:
            json.dump({"save": save, "size": list(size)}, f)
    except OSError:
        pass
    return size


def coverage(s, size=None):
    """-> (fraction of map cells in frame, 'WxH of MxN'). Pure arithmetic."""
    v = _view(s)
    if not v:
        return None, "no viewRect"
    w, h = size or map_size()
    seen = max(0, min(v["maxX"], w - 1) - max(v["minX"], 0) + 1) * \
           max(0, min(v["maxZ"], h - 1) - max(v["minZ"], 0) + 1)
    return seen / float(w * h), "%dx%d of %dx%d" % (
        v["maxX"] - v["minX"] + 1, v["maxZ"] - v["minZ"] + 1, w, h)


# -------------------------------------------------------------------- moves ---

def set_zoom(root):
    f = _need("rimworld/set_camera_zoom", "rootSize", "size", "zoom", "value")
    return rim.game("rimworld/set_camera_zoom", {f: float(root)}, strict=False)


def jump_to(x, z):
    t = "rimworld/jump_camera_to_cell"
    fx = _need(t, "x", "cellX", "posX")
    fz = _need(t, "z", "cellZ", "posZ")
    return rim.game(t, {fx: int(round(x)), fz: int(round(z))}, strict=False)


def zoom_extension(on):
    """-> the reply, or None if this build has no such switch (then we just clamp)."""
    t = "rimworld/set_camera_zoom_extension"
    f = _pick(schema(t), "enabled", "enable", "on", "value", "extended")
    if f is None:
        return None
    return rim.game(t, {f: bool(on)}, strict=False)


def fit_root(w, h):
    """Root size that puts a w x h map on a 4096x2160 screen.

    RimWorld's camera root size is the half-height of the view in cells, so the
    vertical need is h/2 and the horizontal need is w/(2*aspect); the map fits
    only at the larger of the two. +2% so the map edge is not flush with the
    screen edge.
    """
    return max(h / 2.0, w / (2.0 * SCREEN_ASPECT)) * 1.02


def glide(from_root, to_root, from_xz, to_xz, seconds=GLIDE_SECONDS,
          steps=GLIDE_STEPS, since=None):
    """Ease between two camera positions so a watcher sees a move, not a cut.

    -> (finished, interrupted_by). With `since`, the claim is checked before every
    step and the glide **abandons the camera where it stands** the moment Hands
    claims it. It does not tidy up on the way out: moving the camera again to
    "finish" is the exact bug the interrupt exists to prevent.

    Cost, measured: `jump_camera_to_cell` is 0.016s / 2 KB, so forty of them
    across a ten-second round trip is 0.6s of bridge time spread over ten
    seconds. The expensive call is `take_screenshot` at 0.47s, and there is one.
    """
    for i in range(1, steps + 1):
        if since is not None:
            stop, who = camlock.interrupted(since)
            if stop:
                return False, who
        t = i / float(steps)
        t = t * t * (3 - 2 * t)                      # smoothstep: no jerk at either end
        set_zoom(from_root + (to_root - from_root) * t)
        jump_to(from_xz[0] + (to_xz[0] - from_xz[0]) * t,
                from_xz[1] + (to_xz[1] - from_xz[1]) * t)
        time.sleep(seconds / float(steps))
    return True, None


# --------------------------------------------------------------------- base ---

BASE = os.path.join(HERE, "state", "base.json")
BASE_ROOT = 15.0          # "looking at the colony" zoom; 15 keeps cells ~25px
                          # on the 1080p stream (was 24 -> ~16px, too small
                          # after Twitch compression). Zoom, not resolution,
                          # sets map legibility on stream.


def set_base(x, z, save=None):
    """Pin the coordinate the camera calls home."""
    d = {"save": save or _save_name(), "x": int(x), "z": int(z), "at": time.time()}
    os.makedirs(os.path.dirname(BASE), exist_ok=True)
    with open(BASE, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    return d


def _save_name():
    try:
        with open(os.path.join(HERE, "state", "session.json"), encoding="utf-8") as f:
            return json.load(f).get("save") or "?"
    except (OSError, ValueError):
        return "?"


def base_cell():
    """Where home is. -> (x, z, how we know).

    A pinned constant, not a judgement: the Lookout must never be choosing where
    to point the camera. `cam.py base <x> <z>` sets it, keyed to the save.

    Unset, it is derived from the centroid of the colony's buildings -- a
    description of where the colony IS, not an opinion about where to look --
    and recomputed every call until somebody pins it. The verdict says which.
    """
    save = _save_name()
    try:
        with open(BASE, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("save") == save and d.get("x") is not None:
            return int(d["x"]), int(d["z"]), "pinned"
    except (OSError, ValueError, KeyError):
        pass
    try:
        import buildings
        # playerOnly is load-bearing, not tidiness: this map holds 285 walls and
        # most of them are ancient ruins scattered to the far corners, so an
        # unfiltered centroid points at open ground nobody lives on. aggregate
        # off because only the detailed rows carry a position each.
        rows = buildings.survey(playerOnly=True, aggregate=False,
                                maxDetailed=400).get("buildings") or []
        pts = [(b["position"]["x"], b["position"]["z"]) for b in rows
               if isinstance(b.get("position"), dict)]
        if pts:
            return (int(round(sum(p[0] for p in pts) / float(len(pts)))),
                    int(round(sum(p[1] for p in pts) / float(len(pts)))),
                    "derived from %d player buildings -- pin it with `cam.py base x z`" % len(pts))
    except Exception:
        pass
    try:
        cols = [c for c in rim.game("rimworld/list_colonists", {})["colonists"]
                if not c.get("dead") and c.get("position")]
        if cols:
            return (int(round(sum(c["position"]["x"] for c in cols) / float(len(cols)))),
                    int(round(sum(c["position"]["z"] for c in cols) / float(len(cols)))),
                    "derived from %d colonists -- they move, so pin it" % len(cols))
    except Exception:
        pass
    w, h = map_size()
    return w // 2, h // 2, "MAP CENTRE fallback -- nothing else answered; pin it"


def look_at(x, z, root=None, reason=""):
    """Point the camera at a cell and claim it for the turn. For HANDS.

    Reach for this whenever the turn's focus moves elsewhere on the map for more
    than a few seconds. Two things happen and the second is the point: the camera
    moves, **and** the claim lands -- interrupting any Lookout glide in flight and
    buying 5.5 quiet minutes. Nothing to release; the lease expires.
    """
    camlock.claim("hands", reason or "look_at(%d,%d)" % (x, z))
    if root:
        set_zoom(root)
    jump_to(x, z)
    return camlock.claimed()[1]


# How far before a capture began a file may be stamped and still count as that
# capture's output. Slack for clock granularity, not for a stale shot. Same
# rule and same number as `see.py`'s `_fresh_file`, deliberately: the two must
# agree, and `see.py` re-runs it on what this returns.
FRESH_SLACK_S = 5.0


def fresh_shot(path, started):
    """The image THIS call wrote, or a RuntimeError. Never an older shot.

    `rimworld/take_screenshot` returns `sourcePath: null` (upstream, BUGS.md)
    and the usable field is `path` -- but nothing in it says WHEN the file was
    written, and a picture of the wrong moment handed to a blind reviewer is
    the one failure this stack cannot catch downstream: it reads as evidence.
    `see.py` grew this check on 2026-09-07; `shoot` is the shared path that
    `look.py` and the Lookout rota go through, and it had none.

    The named path counts only if it is newer than the call that asked for it.
    If it has not landed, the newest PNG in the same folder is accepted on the
    same test -- newest AFTER the call, never "newest", which is exactly how a
    stale shot gets picked up. Anything else raises, naming what it found.
    """
    path = pathlib.Path(path)
    cutoff = started - FRESH_SLACK_S
    if path.is_file() and path.stat().st_size:
        if path.stat().st_mtime >= cutoff:
            return str(path.resolve())
        raise RuntimeError(
            "%s is %.0f s older than this capture, so it is a PREVIOUS "
            "screenshot, not this one. No image path is being returned."
            % (path, started - path.stat().st_mtime))
    try:
        shots = [p for p in path.parent.glob("*.png")
                 if p.stat().st_mtime >= cutoff and p.stat().st_size]
    except OSError:
        shots = []
    if shots:
        return str(max(shots, key=lambda p: p.stat().st_mtime).resolve())
    raise RuntimeError(
        "the screenshot was reported at %s and no file written since this "
        "call started is in %s. Nothing was captured; an older shot in that "
        "folder is NOT this frame and is not being returned."
        % (path, path.parent))


def shoot(name):
    """take_screenshot -> path on disk. Raises rather than returning a bad path.

    Including a path to a file that was already there: see `fresh_shot`.
    """
    started = time.time()
    r = rim.game("rimworld/take_screenshot",
                 {"fileName": name, "includeTargets": False,
                  "suppressMessage": True}, strict=False)
    if not (isinstance(r, dict) and r.get("success") and r.get("path")):
        raise RuntimeError("take_screenshot failed: %s"
                           % (r.get("message") if isinstance(r, dict) else str(r)[:120]))
    return fresh_shot(r["path"], started)


def wide_and_home(name, glide_seconds=0.0):
    """Zoom out, photograph the map, then come down on the BASE. For the LOOKOUT.

    The camera does **not** return where it was; it goes home. Safe only because
    home is pinned rather than chosen, and because Hands can veto the whole thing
    by claiming the camera.

    -> {path, coverage, note, moved, landed, interrupted}. Never raises.
    """
    out = {"path": None, "coverage": None, "note": "", "moved": False,
           "landed": None, "interrupted": None}

    ok, why = camlock.may_move("lookout")
    if not ok:
        out["note"] = "left the camera alone: %s" % why
        return out

    try:
        before = state()
    except Exception as e:
        out["note"] = "camera unreadable, nothing moved (%s)" % str(e)[:110]
        return out
    home_root, home_xz = _root(before), _centre(before)
    if home_root is None or home_xz is None:
        out["note"] = "camera state has no %s -- refusing to move it" % (
            "zoom/root size" if home_root is None else "viewRect")
        return out

    bx, bz, how = base_cell()
    since = camlock.start_moving("lookout")
    ext = False
    try:
        w, h = map_size()
        want = fit_root(w, h)
        if want > home_root:
            ext = zoom_extension(True) is not None
        if glide_seconds > 0:
            done, who = glide(home_root, want, home_xz, (w // 2, h // 2),
                              glide_seconds, since=since)
            if not done:
                out["interrupted"] = who
                out["note"] = "%s claimed the camera mid-zoom -- left it where it is" % who
                return out
        else:
            set_zoom(want)
            jump_to(w // 2, h // 2)
        out["moved"] = True
        after = state()
        out["coverage"] = coverage(after, (w, h))
        got = _root(after)
        if got is not None and got < want * 0.95:
            out["note"] = "zoom clamped to %.0f of the %.0f needed to fit the map" % (got, want)
        out["path"] = shoot(name)
    except Exception as e:
        out["note"] = (out["note"] + "; " if out["note"] else "") + \
                      "wide shot failed (%s: %s)" % (type(e).__name__, str(e)[:110])
    finally:
        try:
            if out["moved"] and out["interrupted"] is None:
                stop, who = camlock.interrupted(since)
                if stop:
                    out["interrupted"] = who
                    out["note"] = (out["note"] + "; " if out["note"] else "") + \
                        "%s claimed the camera -- left it where it is" % who
                else:
                    out["landed"], out["note"] = _come_home(
                        bx, bz, how, glide_seconds, since, out["note"])
            if ext:
                zoom_extension(False)
        except Exception as e:
            out["note"] = (out["note"] + "; " if out["note"] else "") + \
                "CAMERA MAY BE LEFT ZOOMED OUT -- the way home threw (%s)" % str(e)[:100]
        camlock.stop_moving()
    return out


def _come_home(bx, bz, how, glide_seconds, since, note):
    """The last leg: down onto the base, and tell Hands we did it.

    -> (landed_ok, note). The notice is queued only on a landing that actually
    happened, because "the camera is on the base now" is the kind of line that
    is worse than useless if it is not true -- Hands would stop looking for the
    camera it is actually holding.
    """
    now = state()
    if glide_seconds > 0:
        done, who = glide(_root(now) or BASE_ROOT, BASE_ROOT,
                          _centre(now) or (bx, bz), (bx, bz), glide_seconds, since=since)
        if not done:
            return False, (note + "; " if note else "") + \
                "%s claimed the camera on the way home -- left it where it is" % who
    set_zoom(BASE_ROOT)
    jump_to(bx, bz)
    v = _view(state())
    if v and not (v["minX"] <= bx <= v["maxX"] and v["minZ"] <= bz <= v["maxZ"]):
        return False, (note + "; " if note else "") + \
            "** tried to centre on the base at %d,%d but the camera is at " \
            "x %d..%d z %d..%d **" % (bx, bz, v["minX"], v["maxX"], v["minZ"], v["maxZ"])
    camlock.notify("the Lookout centred the camera on the base at %d,%d (%s). "
                   "Move it back if you were watching something else."
                   % (bx, bz, how))
    return True, (note + "; " if note else "") + "came home to the base at %d,%d" % (bx, bz)


# ---------------------------------------------------------------------- cli ---

PROBE = ("get_camera_state", "jump_camera_to_cell", "set_camera_zoom",
         "set_camera_zoom_extension", "zoom_camera", "move_camera",
         "frame_cell_rect", "screenshot_cell_rect", "take_screenshot")


HELP = ("--help", "-h", "help", "/?")


def main():
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "state"
    # Before rim.init(): usage must print with no bridge and no game running.
    if any(a in HELP for a in argv):
        print(__doc__)
        return 0
    rim.init()

    if cmd == "probe":
        for t in PROBE:
            p = schema("rimworld/" + t)
            print("%-28s %s" % (t, ", ".join(sorted(p)) if p else "-- no schema read --"))
        return 0

    if cmd == "base":
        if len(argv) >= 3:
            d = set_base(argv[1], argv[2])
            print("base pinned at %d,%d for save %r" % (d["x"], d["z"], d["save"]))
            return 0
        x, z, how = base_cell()
        print("base: %d,%d (%s)" % (x, z, how))
        return 0

    if cmd == "zoom":
        if len(argv) < 2:
            print("usage: cam.py zoom <rootSize>   "
                  "(24-35 is a colony view; 60 is 45% of a 250x250 map)")
            return 2
        try:
            want = float(argv[1])
        except ValueError:
            print("cam.py zoom takes a number: the camera root size.")
            return 2
        set_zoom(want)
        s = state()
        frac, shape = coverage(s)
        print("root size : %s (asked for %g)" % (_root(s), want))
        print("covering  : %s" % ("%.1f%% of the map (%s)" % (frac * 100, shape)
                                  if frac is not None else shape))
        if _root(s) is not None and abs(float(_root(s)) - want) > 0.51:
            print("   !! the camera CLAMPED: %s is outside this build's size "
                  "range. `wide` lifts the ceiling with "
                  "set_camera_zoom_extension and drops it again." % want)
        return 0

    if cmd == "go":
        # `--zoom <n>` is pulled out before the positional x/z are read.
        root = None
        if "--zoom" in argv:
            i = argv.index("--zoom")
            if i + 1 >= len(argv):
                print("usage: cam.py go <x> <z> [--zoom <rootSize>]")
                return 2
            try:
                root = float(argv[i + 1])
            except ValueError:
                print("--zoom takes a number: the camera root size.")
                return 2
            del argv[i:i + 2]
        if len(argv) < 3:
            print("usage: cam.py go <x> <z> [--zoom <rootSize>]")
            return 2
        left = look_at(int(argv[1]), int(argv[2]), root=root,
                       reason=" ".join(argv[3:]))
        print("camera moved%s, and claimed for the turn -- the Lookout will not "
              "touch it for %.0fs" % ("" if root is None else " at root size %g" % root,
                                      left))
        return 0

    if cmd == "wide":
        t0 = time.time()
        g = GLIDE_SECONDS if "--glide" in argv else 0.0
        r = wide_and_home("cam-wide-" + time.strftime("%H%M%S"), glide_seconds=g)
        frac = r["coverage"][0] if r["coverage"] else None
        print("wide shot: %s" % (r["path"] or "NONE"))
        print("  covered : %s%s"
              % ("%.0f%% of the map" % (frac * 100) if frac is not None else "?",
                 " (%s)" % r["coverage"][1] if r["coverage"] else ""))
        print("  camera  : %s" % ("landed on the base" if r["landed"] else
                                  "interrupted by %s" % r["interrupted"] if r["interrupted"]
                                  else "did not land"))
        if r["note"]:
            print("  note    : %s" % r["note"])
        print("  took    : %.1fs" % (time.time() - t0))
        # Diagnostic runs must not litter: take_screenshot writes a ~14 MB 4K PNG
        # into the shared profile, and look.py is the only caller that keeps one
        # (downscaled).
        if r["path"]:
            try:
                os.remove(r["path"])
                print("  (removed the 4K original)")
            except OSError:
                pass
        return 0 if (r["path"] and r["landed"]) else 1

    s = state()
    v = _view(s)
    frac, shape = coverage(s)
    print("root size : %s" % _root(s))
    print("centre    : %s" % (_centre(s),))
    print("viewRect  : %s" % ("x %d..%d  z %d..%d"
                              % (v["minX"], v["maxX"], v["minZ"], v["maxZ"]) if v else "?"))
    print("covering  : %s" % ("%.1f%% of the map (%s)" % (frac * 100, shape)
                              if frac is not None else shape))
    return 0


if __name__ == "__main__":
    # Stream narration. main() reads sys.argv itself and returns the exit code
    # from a dozen branches, so the flags are removed from argv here and the
    # thought is posted between main() returning and the process exiting --
    # exit code unchanged, main() untouched.
    try:
        import overlay_client as _ov
        sys.argv[1:], _say, _mood = _ov.take_flags(sys.argv[1:])
    except Exception:
        _ov, _say, _mood = None, None, None
    _rc = main()
    if _ov is not None:
        _ov.say_flags(_say, _mood)
    sys.exit(_rc)
