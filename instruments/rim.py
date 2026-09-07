"""Direct client for the RimWorld bridge, for agents that aren't Codex.

Codex drives the bridge (`rimworld\\bridge`) through GABS as an MCP server over
stdio. Claude Code can't add an MCP server mid-session, but GABS also serves the
same MCP surface over HTTP (`gabs server http`), so this speaks that directly.

  python rim.py serve          # start GABS on 127.0.0.1:8080 (run in background)
  python rim.py tools          # ALL the live game tool names (every page)
  python rim.py tools --grep cell   # ... only the ones whose name contains "cell"
  python rim.py detail <tool>  # schema for one game tool
  python rim.py call <tool> '<json args>'
  python rim.py g <gabs_tool> '<json args>'   # games_start, games_connect, ...

Add `--say "..."` and `--mood <mood>` to any of these to narrate the call to the
stream overlay after it returns. See `overlay_client.py`.
"""
import json, os, re, subprocess, sys, time, urllib.request

# --- timing log (Aug 30) -------------------------------------------------
# M asked what actually happens in the "forty seconds of JSON" between
# one visible game change and the next. Every RPC records start, end, name and
# both payload sizes; the GAPS between consecutive rows are the part that
# happens outside this process -- the model thinking, and the harness
# round-trip. Set RIM_TIMING to a path to turn it on; off by default.
TIMING = os.environ.get("RIM_TIMING")


def _log(row):
    if not TIMING:
        return
    try:
        with open(TIMING, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + chr(10))
    except Exception:
        pass

HARNESS = os.environ.get("RIMWORLD_HARNESS_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.join(HARNESS, "bridge")
GABS = os.path.join(ROOT, "gabs", "gabs.exe")
CFG = os.path.join(ROOT, "gabs-config")
URL = "http://127.0.0.1:8080/mcp"
GAME = "rimworld"
_id = [0]
_short_names = {}


def rpc(method, params=None, timeout=600):
    _id[0] += 1
    body = {"jsonrpc": "2.0", "id": _id[0], "method": method}
    if params is not None:
        body["params"] = params
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"})
    _t0 = time.time()
    raw = urllib.request.urlopen(req, timeout=timeout).read().decode()
    _t1 = time.time()
    if raw.startswith("event:") or "\ndata: " in raw:
        raw = raw.split("data: ")[-1].strip()
    _name = method
    if method == "tools/call":
        _name = (params or {}).get("name", "?")
        _inner = ((params or {}).get("arguments") or {}).get("tool")
        if _inner:
            _name = _inner
    _log({"t0": _t0, "t1": _t1, "dt": round(_t1 - _t0, 3), "call": _name,
          "sent": len(json.dumps(body)), "got": len(raw)})
    return json.loads(raw)


def init():
    rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "opus", "version": "1"}})


def tool(name, args=None, timeout=600):
    """Call a GABS-level tool and unwrap the MCP content envelope."""
    r = rpc("tools/call", {"name": name, "arguments": args or {}}, timeout=timeout)
    if "error" in r:
        return r
    out = []
    for c in r.get("result", {}).get("content", []):
        t = c.get("text", "")
        try:
            out.append(json.loads(t))
        except Exception:
            out.append(t)
    return out[0] if len(out) == 1 else out


class BridgeError(RuntimeError):
    pass


def _tool_args(name, args):
    """Small compatibility aliases for bridge schemas with renamed fields."""
    out = dict(args or {})
    if name == "rimworld/set_draft" and "pawn" in out:
        if "pawnName" in out or "pawnId" in out:
            raise BridgeError("rimworld/set_draft: 'pawn' conflicts with explicit "
                              "'pawnName'/'pawnId'; pass exactly one pawn selector")
        pawn = out.pop("pawn")
        stable_id = (isinstance(pawn, int) and not isinstance(pawn, bool)) or (
            isinstance(pawn, str) and (pawn.isdigit() or
                re.match(r"^(?:Pawn_|Thing_|Human)\S*\d+$", pawn) is not None))
        out["pawnId" if stable_id else "pawnName"] = str(pawn) if stable_id else pawn
    return out


# --- the tool list, ALL of it (2026-09-02) --------------------------------
#
# `rim.py tools` used to print one `games_tool_names` call: 50 names out of 134,
# with no hint that the other 84 existed. `games_tool_names` PAGINATES at 50 and
# says so only in a trailing "Next cursor: 50" line inside a PROSE reply -- the
# names in it are OpenAI-normalised (`rimworld_rimworld_click_cell`), so a
# caller matching on "/" also finds zero. setup.game_tools() has known both
# shapes since Aug 30; this is that loop, kept here where the CLI can reach it.
# Guarded for the dict shape too (`nextCursor`/`cursor`, `page`/`hasMore`), and
# an unrecognised pagination key is PRINTED rather than swallowed -- a silent
# page 1 is exactly the bug being fixed.
# Keys that would mean "there is more" in a shape this loop does not follow.
# `total`/`totalCount` are deliberately NOT here: they are a count, not a cursor,
# and flagging them would print a "the list may be short" warning about a field
# that says the opposite.
_PAGE_KEYS = ("nextCursor", "cursor", "nextPage", "page", "hasMore", "more",
              "isTruncated", "nextPageToken", "continuationToken")


def _page_names(r):
    """(names, next cursor or None, unrecognised pagination keys) for one page."""
    unknown = []
    if isinstance(r, str):
        names = [l.strip() for l in r.splitlines() if l.strip().startswith(GAME + "_")]
        nxt = [l for l in r.splitlines() if l.lower().startswith("next cursor:")]
        cur = None
        if nxt:
            try:
                cur = int(nxt[0].split(":", 1)[1].strip())
            except ValueError:
                unknown.append(nxt[0].strip())
        return names, cur, unknown
    if isinstance(r, dict):
        names = []
        for k in ("tools", "names", "toolNames", "items", "results"):
            v = r.get(k)
            if isinstance(v, list):
                names = [x if isinstance(x, str) else (x.get("name") or str(x)) for x in v]
                break
        cur = r.get("nextCursor")
        if cur is None and r.get("hasMore") and r.get("cursor") is not None:
            cur = r.get("cursor")
        if cur is None and r.get("hasMore") and r.get("page") is not None:
            cur = r.get("page")
        for k in _PAGE_KEYS:
            if k in r and k not in ("nextCursor", "cursor", "hasMore", "page"):
                unknown.append("%s=%r" % (k, r[k]))
        return names, cur, unknown
    if isinstance(r, list):
        return [x for x in r if isinstance(x, str)], None, []
    return [], None, ["unrecognised reply type %s" % type(r).__name__]


def tool_names(limit_pages=20):
    """Every game tool name, following the cursor to the end. -> sorted list.

    Names are returned BOTH ways: `rimworld/click_cell` (what `game()` takes)
    is what you get; the normalised `rimworld_rimworld_click_cell` never leaves
    this function.
    """
    out, cursor, seen, notes = set(), 0, set(), []
    for _ in range(limit_pages):
        r = tool("games_tool_names", {"gameId": GAME, "cursor": cursor})
        names, nxt, unknown = _page_names(r)
        notes.extend(unknown)
        for n in names:
            body = n[len(GAME) + 1:] if n.startswith(GAME + "_") else n
            ns, _, rest = body.partition("_")
            out.add("%s/%s" % (ns, rest) if rest else body)
        if not names or nxt is None or nxt in seen:
            break
        seen.add(nxt)
        cursor = nxt
    for n in notes:
        print("   ~~ games_tool_names carried a pagination field this loop does "
              "not know: %s -- the list below may be short." % n)
    return sorted(out)


def _ack_attention():
    """Clear a stuck attention item.

    A failed bridge call raises a GABS *attention item*, after which every later
    call returns a refusal STRING instead of a dict until it is acknowledged --
    so the next thing you try dies on `'str' object has no attribute 'get'`,
    naming the wrong culprit. Recover here instead of remembering to.
    """
    msg = tool("games_get_attention", {"gameId": GAME})
    if not isinstance(msg, str) or "attn_" not in msg:
        return False
    aid = "attn_" + msg.split("attn_", 1)[1].split("'")[0]
    tool("games_ack_attention", {"gameId": GAME, "attentionId": aid})
    return True


def game(name, args=None, strict=True, timeout=600):
    """Call a bridge tool inside RimWorld (e.g. 'rimworld/get_game_info').

    A refused call comes back as an ordinary dict with `success: false`, a
    `message`, and **none of the keys you asked for**. So `get_cells_info` over
    a rect larger than 1024 cells returns no `cells` key at all, and every
    caller doing `.get("cells") or []` reads a refusal as an empty region --
    a silent zero in the one direction that matters (Aug 29). Raise instead.
    Pass strict=False where a false is a real answer you want to inspect.

    2026-09-02: a strict call that comes back as a plain STRING now raises too,
    with the first 300 characters attached. See the comment at the raise for the
    three things that shape looks like.
    """
    # The CLI examples use canonical ``namespace/tool`` names, while callers
    # naturally tend to pass the short name exposed by the Python helper.  A
    # short name is a RimBridge tool; companion tools already carry ``home/``.
    # Normalising here also makes dotted names copied from older GABS output
    # behave exactly like ``rim.py call rimworld/<tool>``.
    if "/" not in name:
        if "." in name:
            name = name.replace(".", "/", 1)
        elif not name.startswith(GAME + "_"):
            # Resolve a bare leaf across both namespaces.  This matters for
            # companion-only names such as list_things: guessing rimworld/
            # recreates the misleading "Tool not found" that the CLI avoids.
            if name not in _short_names:
                try:
                    hits = [n for n in tool_names() if n.rsplit("/", 1)[-1] == name]
                except Exception:
                    hits = []
                if len(hits) > 1:
                    raise BridgeError("bare tool name %r is ambiguous; use one of: %s"
                                      % (name, ", ".join(hits)))
                # Cache only proof. An empty page can be a transient discovery
                # failure and must be retried rather than remembered forever.
                if len(hits) == 1:
                    _short_names[name] = hits[0]
            if name in _short_names:
                name = _short_names[name]
            else:
                name = GAME + "/" + name
    call = {"gameId": GAME, "tool": name, "arguments": _tool_args(name, args)}
    r = tool("games_call_tool", call, timeout=timeout)
    if isinstance(r, str) and "requires acknowledgement" in r and _ack_attention():
        r = tool("games_call_tool", call, timeout=timeout)
    if strict and isinstance(r, dict) and r.get("success") is False:
        raise BridgeError(f"{name}: {r.get('message') or r}")
    if strict and isinstance(r, str):
        # A STRING got past `tool()`'s json.loads, so this is not a payload.
        # Observed 2026-09-02 for `home/get_cells_plus`; the plausible causes,
        # written down because the string itself never says which:
        #   1. GABS split a large result across several MCP `content` items --
        #      then `tool()` returns a LIST of fragments, and a caller that
        #      indexes it gets one fragment's text. Each fragment is valid JSON
        #      only by accident, so this is what a too-big reply looks like.
        #   2. A truncated payload: the reply was cut mid-object and json.loads
        #      failed on the tail, so the whole thing fell through as text.
        #   3. An attention-item refusal in prose ("... requires
        #      acknowledgement ..."), which `_ack_attention` above clears --
        #      if it is still a string HERE, that clear did not take.
        # Whichever it is, handing a str back to a caller expecting a dict just
        # moves the failure one line down and renames it `'str' object has no
        # attribute 'get'`. Raise where the evidence is.
        raise BridgeError("%s: reply was a non-JSON string, not a payload "
                          "(len %d). First 300 chars: %r"
                          % (name, len(r), r[:300]))
    if isinstance(r, dict) and r.get("success") is not False:
        tick = None
        if name in ("home/get_time", "rimworld/get_game_info"):
            tick = r.get("ticksGame")
        elif name == "rimworld/list_letters":
            tick = r.get("currentGameTick")
        if tick is not None:
            try:
                import game_session
                game_session.observe(tick, r.get("sessionId"))
            except Exception:
                # Session bookkeeping is a safety aid around observations; it
                # must never rename an otherwise successful game RPC.
                pass
    return r


_size = []


def map_size():
    """(width, height) of the current map, probed once.

    Nothing on the bridge reports it, and a scan that runs off the edge used to
    come back empty rather than erroring -- so an edge-adjacent sweep silently
    under-reported. Binary-search the boundary instead of assuming 250.
    """
    if _size:
        return _size[0]
    dims = []
    for axis in ("x", "z"):
        lo, hi = 1, 1024
        while lo < hi:
            mid = (lo + hi + 1) // 2
            args = {"x": 0, "z": 0}
            args[axis] = mid - 1
            ok = game("rimworld/get_cell_info", args, strict=False).get("success")
            lo, hi = (mid, hi) if ok else (lo, mid - 1)
        dims.append(lo)
    _size.append(tuple(dims))
    return _size[0]


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "tools"
    if cmd == "serve":
        subprocess.run([GABS, "server", "http", "--configDir", CFG,
                        "--log-level", "info"])
        return
    init()
    # `--grep <s>` is pulled out before argv[3] is parsed as JSON: `tools --grep
    # cell` used to reach json.loads("cell") and die on the flag it was given.
    argv, needle = list(sys.argv), None
    if "--grep" in argv:
        i = argv.index("--grep")
        needle = argv[i + 1] if i + 1 < len(argv) else ""
        del argv[i:i + 2]
    a = json.loads(argv[3]) if len(argv) > 3 else {}
    if cmd == "tools":
        names = tool_names()
        if needle is not None:
            hits = [n for n in names if needle.lower() in n.lower()]
            print("\n".join(hits) if hits else
                  "no tool name contains %r (of %d, checked)" % (needle, len(names)))
            print("-- %d of %d tool names match %r" % (len(hits), len(names), needle))
        else:
            print("\n".join(names))
            print("-- %d tool names, every page followed" % len(names))
    elif cmd == "detail":
        print(json.dumps(tool("games_tool_detail",
                              {"gameId": GAME, "tool": argv[2]}), indent=1))
    elif cmd == "call":
        print(json.dumps(game(argv[2], a), indent=1)[:8000])
    elif cmd == "g":
        print(json.dumps(tool(argv[2], a), indent=1)[:8000])
    else:
        print(__doc__)


if __name__ == "__main__":
    # Stream narration: --say/--mood are pulled out of argv before main() reads
    # it positionally, and posted after the call returns.
    try:
        import overlay_client as _ov
        sys.argv[1:], _say, _mood = _ov.take_flags(sys.argv[1:])
    except Exception:
        _ov, _say, _mood = None, None, None
    main()
    if _ov is not None:
        _ov.say_flags(_say, _mood)
