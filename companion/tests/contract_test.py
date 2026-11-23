"""Check every `home/` tool against its own annotations -- offline, then live.

    cd rimworld\\instruments
    python ..\\companion\\tests\\contract_test.py --offline     # no game needed
    python ..\\companion\\tests\\contract_test.py               # + the live half
    python ..\\companion\\tests\\contract_test.py --tool home/list_rooms

Exit code is 0 only when nothing was reported as SUSPECT, MISSING, NULL or
FAIL. Advisory lines never change the exit code.

## The failure this exists to catch

`home/list_buildings` promised `promotedOut` in its own `[ToolResponse]`
annotation **from the day the tool was written** and no code emitted it for
days; `equipment` was documented on `home/list_pawns` and was not implemented.
Nothing read the annotations, so nothing could notice. This does, twice over:

  Step 1  parse `companion\\src\\*.cs` for every `[Tool("home/...")]` method,
          its `[ToolResponse]` promises and its `[ToolParameter]` list, and
          print the contract as a table.
  Step 2  OFFLINE. For every promised key, grep the tool's own source (with
          comments stripped, so a key that is only *discussed* in a doc comment
          does not count as emitted) for the key being written -- `["key"]`,
          `{ "key", ... }`, `.Add("key", ...)` or an anonymous member `key =`.
          A key found nowhere in `src\\` is a **SUSPECT**. This alone would have
          caught `promotedOut` on the day it was annotated.
  Step 3  LIVE, needs a loaded and PAUSED colony. Call every tool with its
          default arguments and with each opt-in block on its own, and assert
          the reply keeps the promise.

## What the live half asserts, per call

  * the reply is a dict and carries `success`
  * `unknownArguments == []` -- every argument name we sent was recognised
  * every `Always = true` key is present (else **MISSING**)
  * every present key whose annotation does NOT say `Nullable = true` is not
    null (else **NULL**)

A reply with `success: false` is a *refusal* -- the tool answering "I will not
do that, here is why". The Always keys are not checked on a refusal (a refusal
payload is allowed to be short), and it is not counted as a failure; but a tool
whose every case refused is reported at the end as **NEVER EXERCISED**, because
a contract nothing satisfied is a contract nothing checked.

## What it will not do to your colony

It never saves, never unpauses, never selects, never writes.

  * `home/play_until_event` is in a hard SKIP list: calling it *unpauses the
    game*, which is the one thing a test may not do behind a player's back.
  * `home/pawn_config`, `home/zone_cells` and `home/place_building` are write
    tools. They are called with `dryRun: true` only, with arguments chosen to
    plan and discard: a zone `repair` that computes and writes nothing, a wall
    blueprint evaluated and not placed.
  * A hard assert refuses to send `dryRun: false` on any call, whatever the
    table says, so a later edit to the table cannot quietly turn this into a
    tool that changes the map.
  * Every other tool is a read.

## Where to add to it

`CASES` at the top is the per-tool table: string and enum parameters are left
alone unless a safe value is written down there, because guessing at a string
argument is how a test starts doing something nobody asked for. Bool parameters
are swept automatically -- each one set to `true` on its own -- except for the
tools listed in `EXPLICIT_ONLY`, where only the table's cases are ever sent.

New tools are picked up with no edit here: the parser reads whatever `[Tool]`
methods `src\\` currently holds. **A new WRITE tool whose write is triggered by
a bool must be added to `EXPLICIT_ONLY`**, because the sweep would otherwise
turn that bool on. A write gated behind a string argument is already safe: the
sweep never invents a string.
"""
import argparse
import json
import os
import re
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
# CONTRACT_TEST_SRC points the parser at a different folder. It exists so this
# script can be tested against a doctored copy of the sources -- delete the one
# line that emits a key and the run must go red -- without editing the real
# ones. Nothing in normal use sets it.
SRC = os.environ.get("CONTRACT_TEST_SRC") or \
    os.path.normpath(os.path.join(HERE, "..", "src"))
INSTRUMENTS = os.path.normpath(os.path.join(HERE, "..", "..", "instruments"))

# Keys every tool carries by convention rather than by annotation. Not promised,
# so not checked as promises; excluded from the "emitted but not annotated"
# advisory so it reports something worth reading.
CONVENTION_KEYS = {"success", "tool", "message", "error",
                   "unknownArguments", "unknownArgumentsWarning"}


# ===================================================================== SKIPS

# Tools this must never call, with the reason. A skip is louder than a silence:
# it is printed in the run.
HARD_SKIP = {
    "home/play_until_event":
        "it UNPAUSES the game and lets it run (speed defaults to Superfast). "
        "A contract test may not move a colony's clock. Exercise it by hand, "
        "on purpose, with a person watching.",
}

# Tools where the automatic bool sweep is off and only CASES below are sent.
# Every one of these is a write tool or has an action verb whose other values
# do something.
# home/world is here for `show`: the sweep would turn it on and flip the game to
# the planet view behind the player.
EXPLICIT_ONLY = {"home/trade", "home/pawn_config", "home/zone_cells",
                 "home/place_building", "home/building_config", "home/bills",
                 "home/supervised_play", "home/world"}

# This read-only tool promises its full response shape even when no dialog is
# open.  That is the normal state during this suite, so its refusal payload is
# the safe, deterministic way to exercise the contract.
CHECK_ALWAYS_ON_REFUSAL = {"home/dialog_text"}


# ===================================================================== CASES

# Per-tool extra calls. Each is (label, args) or (label, callable(ctx) -> args
# or None). Returning None means "no safe arguments could be built" and prints
# a reason instead of a call. `ctx` carries what the live probe learned:
#   ctx["cell"]      (x, z) of a living colonist, or None
#   ctx["pawn"]      that colonist's name as list_pawns spells it, or None
#   ctx["medCare"]   that colonist's current medical-care setting, or None
#   ctx["building"]  "DefName@x,z" of a colony building, or None
#   ctx["bench"]     the same form for a bill giver, or None
def _rect(ctx, w=4, h=4, extra=None):
    if not ctx.get("cell"):
        return None
    x, z = ctx["cell"]
    args = {"x": max(0, x - w // 2), "z": max(0, z - h // 2),
            "width": w, "height": h}
    if extra:
        args.update(extra)
    return args


CASES = {
    "home/supervised_play": [("status", {"op": "status"}),
                              ("events", {"op": "events", "limit": 1})],
    "home/ping": [
        ("a label", {"label": "contract_test"}),
    ],
    "home/get_cells_plus": [
        # 4x4 around a colonist: 16 cells, far under the 1024-cell refusal.
        ("4x4 around a colonist", lambda c: _rect(c)),
        ("4x4 summary", lambda c: _rect(c, extra={"summary": True})),
    ],
    "home/list_pawns": [
        ("a name substring", {"nameFilter": "a"}),
        ("a contradiction is refused", {"wildOnly": True, "tameOnly": True}),
    ],
    "home/status": [
        ("colonists and threats off", {"colonists": False, "threats": False}),
    ],
    "home/research": [
        ("watch off", {"watch": False}),
    ],
    "home/building_config": [
        ("gizmos on a colony building",
         lambda c: ({"thing": c["building"], "gizmos": True}
                    if c.get("building") else None)),
        ("a dry-run forbid on the same building",
         lambda c: ({"thing": c["building"], "forbidden": True, "dryRun": True}
                    if c.get("building") else None)),
    ],
    "home/bills": [
        ("list, every bench", {"action": "list"}),
        ("list, allFactions", {"action": "list", "allFactions": True}),
        ("a bench that matches nothing", {"bench": "NoSuchBenchZZZ"}),
        ("recipes on a bench",
         lambda c: ({"action": "recipes", "bench": c["bench"]}
                    if c.get("bench") else None)),
    ],
    "home/get_temperatures": [
        ("rooms over a 4x4", lambda c: _rect(c, extra={"mode": "rooms"})),
        ("cells over a 4x4", lambda c: _rect(c, extra={"mode": "cells"})),
        # Two refusals with a contract of their own: both must come back with a
        # reason, not a bare success:false. The 1x1 is what a rooms call with no
        # rect arrives as, and it used to answer "one room, the outdoors".
        ("rooms with no rect is refused", {"x": 0, "z": 0, "mode": "rooms"}),
        ("an oversize rect is refused",
         {"x": 0, "z": 0, "width": 250, "height": 250, "mode": "cells"}),
    ],
    "home/list_rooms": [
        ("a rect", lambda c: _rect(c, 8, 8)),
        ("one cell", lambda c: ({"x": c["cell"][0], "z": c["cell"][1]}
                                if c.get("cell") else None)),
    ],
    "home/list_buildings": [
        ("status=built", {"status": "built"}),
        ("status=blueprint", {"status": "blueprint"}),
        ("status=frame", {"status": "frame"}),
        ("status=pending", {"status": "pending"}),
        ("category=all", {"category": "all"}),
    ],
    "home/list_things": [
        ("category=all", {"category": "all"}),
        ("ownership=all", {"ownership": "all"}),
    ],
    "home/trade": [
        # The ONLY two actions this test is allowed to send. Everything else in
        # this tool moves goods or opens a session.
        ("action=list_traders", {"action": "list_traders"}),
        ("action=status", {"action": "status"}),
    ],
    "home/pawn_config": [
        # A dry run that names a pawn and no field: the refusal path, which is
        # still a payload with a contract.
        ("a pawn, no field, dry run",
         lambda c: ({"pawn": c["pawn"], "dryRun": True} if c.get("pawn") else None)),
        # A dry run that writes the pawn's CURRENT medical care back onto it:
        # the field path, planning a change of nothing, applying nothing.
        ("a pawn, medCare set to its current value, dry run",
         lambda c: ({"pawn": c["pawn"], "medCare": c["medCare"], "dryRun": True}
                    if c.get("pawn") and c.get("medCare") else None)),
        # The drop field's refusal path: a label nothing can match, so the row
        # comes back refused with carried[] naming what the pawn does hold.
        # Nothing is dropped by a name no item has, and dryRun is true anyway.
        ("a pawn, a drop nothing matches, dry run",
         lambda c: ({"pawn": c["pawn"], "drop": "contractTestNoSuchItem",
                     "dryRun": True} if c.get("pawn") else None)),
    ],
    "home/zone_cells": [
        # op=repair with no zone named inspects EVERY zone; dryRun computes the
        # repair and writes nothing.
        ("op=repair, whole map, dry run", {"op": "repair", "dryRun": True}),
        # op=crop's refusal path: a plant name nothing matches, so the reply
        # lists what the zone would take and sets nothing. dryRun anyway.
        ("op=crop, a plant nothing matches, dry run",
         {"op": "crop", "zone": "contractTestNoSuchZone",
          "plant": "contractTestNoSuchPlant", "dryRun": True}),
    ],
    "home/world": [
        # The plain readout, which is the whole read-only surface.
        ("the current map's tile", {}),
        ("no settlement sweep", {"settlementRadius": 0}),
        # show WITHOUT dryRun:false, so the planet view stays put and the
        # worldView block still comes back with every key.
        ("show is refused on a dry run", {"show": True}),
    ],
    "home/place_building": [
        # Evaluates every rotation of a wall on a colonist's own cell and places
        # nothing. The cell is occupied, so this also exercises the refusal
        # reasons -- which are part of the payload contract.
        ("a wall on a colonist's cell, every rotation, dry run",
         lambda c: (dict({k: v for k, v in (_rect(c, 1, 1) or {}).items()
                          if k in ("x", "z")}, def_="Wall", rotation="all",
                         dryRun=True) if c.get("cell") else None)),
    ],
}

# `def` is a Python keyword, so the place_building case above spells it `def_`
# and it is renamed on the way out.
ARG_RENAMES = {"def_": "def"}

# Values that are never sent, whatever a table says.
FORBIDDEN_ARGS = {"dryRun": False}


# ============================================================ C# source scan

class Unresolved(object):
    """An attribute value the parser could read but not evaluate (a symbol)."""

    def __init__(self, expr):
        self.expr = expr

    def __repr__(self):
        return "<%s>" % self.expr

    __str__ = __repr__


def mask(text):
    """-> (code, nocomment, literals).

    `code`      comments blanked to spaces and string literals overwritten with
                `~` (same length, newlines kept), so brackets and commas inside
                either cannot confuse a scan. A masked literal stays visible as
                a non-blank token, because an argument list has to be able to
                tell "a string was here" from "nothing was here".
    `nocomment` comments blanked, string literals left intact -- this is what
                the emitted-key grep reads, so a key merely NAMED in a doc
                comment never counts as emitted.
    `literals`  {index of the literal's first character: its decoded value}.

    Same length as `text` throughout, so every index is shared between them.
    """
    code, nocom, lits = [], [], {}
    i, n = 0, len(text)

    def blank(seg):
        return "".join(ch if ch == "\n" else " " for ch in seg)

    def hide(seg):
        return "".join(ch if ch == "\n" else "~" for ch in seg)

    while i < n:
        c = text[i]
        two = text[i:i + 2]
        three = text[i:i + 3]
        if two == "//":
            j = text.find("\n", i)
            j = n if j < 0 else j
            code.append(blank(text[i:j]))
            nocom.append(blank(text[i:j]))
            i = j
            continue
        if two == "/*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            code.append(blank(text[i:j]))
            nocom.append(blank(text[i:j]))
            i = j
            continue
        if three in ('$@"', '@$"') or two == '@"':
            start = i
            q = i + (3 if three in ('$@"', '@$"') else 2)
            body = []
            while q < n:
                if text[q] == '"':
                    if text[q + 1:q + 2] == '"':
                        body.append('"')
                        q += 2
                        continue
                    q += 1
                    break
                body.append(text[q])
                q += 1
            seg = text[start:q]
            lits[start] = "".join(body)
            code.append(hide(seg))
            nocom.append(seg)
            i = q
            continue
        if two == '$"' or c == '"':
            start = i
            q = i + (2 if two == '$"' else 1)
            body = []
            while q < n:
                ch = text[q]
                if ch == "\\" and q + 1 < n:
                    body.append(_unescape(text[q + 1]))
                    q += 2
                    continue
                if ch == '"':
                    q += 1
                    break
                if ch == "\n":          # unterminated: bail at end of line
                    break
                body.append(ch)
                q += 1
            seg = text[start:q]
            lits[start] = "".join(body)
            code.append(hide(seg))
            nocom.append(seg)
            i = q
            continue
        if c == "'":
            q = i + 1
            while q < n:
                if text[q] == "\\":
                    q += 2
                    continue
                if text[q] == "'":
                    q += 1
                    break
                q += 1
            seg = text[i:q]
            code.append(hide(seg))
            nocom.append(seg)
            i = q
            continue
        code.append(c)
        nocom.append(c)
        i += 1
    return "".join(code), "".join(nocom), lits


def _unescape(ch):
    return {"n": "\n", "t": "\t", "r": "\r", "0": "\0",
            "\\": "\\", '"': '"', "'": "'"}.get(ch, ch)


def match_bracket(code, i):
    """Index of the bracket closing the one at `i`, or -1."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    opens = dict((v, k) for k, v in pairs.items())
    if code[i] not in pairs:
        return -1
    depth = 0
    for j in range(i, len(code)):
        c = code[j]
        if c in pairs:
            depth += 1
        elif c in opens:
            depth -= 1
            if depth == 0:
                return j
    return -1


def split_top(code, a, b, sep=","):
    """[(start, end)] of the top-level `sep`-separated spans in code[a:b]."""
    out, depth, start = [], 0, a
    for i in range(a, b):
        c = code[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == sep and depth == 0:
            out.append((start, i))
            start = i + 1
    out.append((start, b))
    return [(s, e) for s, e in out if code[s:e].strip()]


def lits_in(lits, a, b):
    return [v for k, v in sorted(lits.items()) if a <= k < b]


def value_of(code, lits, consts, a, b):
    """The value of the expression in code[a:b]: string (concatenation and all),
    bool, int, None, or an Unresolved symbol."""
    strings = lits_in(lits, a, b)
    if strings:
        return "".join(strings)
    expr = code[a:b].strip()
    if expr in ("true", "false"):
        return expr == "true"
    if expr == "null":
        return None
    if re.match(r"^-?\d+$", expr):
        return int(expr)
    if re.match(r"^-?\d*\.\d+[fdm]?$", expr):
        return float(expr.rstrip("fdm"))
    if expr in consts:
        return consts[expr]
    return Unresolved(expr)


def const_map(code, lits):
    """`const string ToolName = "home/x";` and friends, so `[Tool(ToolName)]`
    resolves. Also picks up the int caps used as parameter defaults."""
    out = {}
    for m in re.finditer(r"\bconst\s+(string|int|bool|double|float)\s+(\w+)\s*=",
                         code):
        end = code.find(";", m.end())
        if end < 0:
            continue
        out[m.group(2)] = value_of(code, lits, {}, m.end(), end)
    return out


ATTR_RE = re.compile(r"\[\s*(ToolResponse|ToolParameter|Tool)\s*\(")


def parse_file(path):
    """-> (tools, note). `note` is None, or a sentence about what was skipped.

    Never raises for a source file's sake: another agent may be editing these
    right now, and half a file is a report, not a crash.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    code, nocom, lits = mask(text)
    consts = const_map(code, lits)

    # Every attribute occurrence, in source order, with its bracket span.
    attrs = []
    for m in ATTR_RE.finditer(code):
        open_sq = m.start()
        close_sq = match_bracket(code, open_sq)
        open_par = m.end() - 1
        close_par = match_bracket(code, open_par)
        if close_sq < 0 or close_par < 0:
            continue
        attrs.append({"kind": m.group(1), "sq": (open_sq, close_sq),
                      "par": (open_par, close_par)})

    tools, skipped = [], []
    for idx, a in enumerate(attrs):
        if a["kind"] != "Tool":
            continue
        args = split_top(code, a["par"][0] + 1, a["par"][1])
        named, positional = {}, []
        for s, e in args:
            m = re.match(r"\s*(\w+)\s*=(?!=)", code[s:e])
            if m:
                named[m.group(1)] = value_of(code, lits, consts,
                                             s + m.end(), e)
            else:
                positional.append(value_of(code, lits, consts, s, e))
        name = positional[0] if positional else named.get("Name")
        if not isinstance(name, str):
            skipped.append("a [Tool] at offset %d whose name did not resolve "
                           "(%r)" % (a["sq"][0], name))
            continue

        # The [ToolResponse] attributes attached to the same method: the ones
        # adjacent to this [Tool], before it or after it, with nothing but
        # whitespace between the brackets.
        promises = []
        for step in (-1, 1):
            j = idx + step
            prev = a
            while 0 <= j < len(attrs):
                b = attrs[j]
                gap = (code[b["sq"][1] + 1:prev["sq"][0]] if step == -1
                       else code[prev["sq"][1] + 1:b["sq"][0]])
                if gap.strip():
                    break
                if b["kind"] != "ToolResponse":
                    break
                promises.append(b)
                prev = b
                j += step
        promised = []
        for b in sorted(promises, key=lambda q: q["sq"][0]):
            bargs = split_top(code, b["par"][0] + 1, b["par"][1])
            bnamed, bpos = {}, []
            for s, e in bargs:
                m = re.match(r"\s*(\w+)\s*=(?!=)", code[s:e])
                if m:
                    bnamed[m.group(1)] = value_of(code, lits, consts,
                                                  s + m.end(), e)
                else:
                    bpos.append(value_of(code, lits, consts, s, e))
            key = bpos[0] if bpos else bnamed.get("Name")
            if not isinstance(key, str):
                skipped.append("a [ToolResponse] on %s whose key did not "
                               "resolve" % name)
                continue
            promised.append({
                "key": key,
                "type": bpos[1] if len(bpos) > 1 else bnamed.get("Type"),
                "description": bpos[2] if len(bpos) > 2 else bnamed.get("Description"),
                "always": bnamed.get("Always") is True,
                "nullable": bnamed.get("Nullable") is True,
                "extra": dict((k, v) for k, v in bnamed.items()
                              if k not in ("Always", "Nullable", "Name",
                                           "Type", "Description")),
            })

        # The method header: after the last attribute in the run, skip any
        # further [...] attributes we do not know, then read to the parameter
        # list's closing paren.
        last = max([a] + promises, key=lambda q: q["sq"][1])["sq"][1]
        k = last + 1
        while k < len(code):
            while k < len(code) and code[k].isspace():
                k += 1
            if k < len(code) and code[k] == "[":
                nk = match_bracket(code, k)
                if nk < 0:
                    break
                k = nk + 1
                continue
            break
        open_par = code.find("(", k)
        if open_par < 0:
            skipped.append("%s: no parameter list found after the attributes"
                           % name)
            continue
        close_par = match_bracket(code, open_par)
        header = code[k:open_par]
        mname = re.search(r"(\w+)\s*$", header)
        params = []
        if close_par < 0:
            skipped.append("%s: unbalanced parameter list" % name)
        else:
            for s, e in split_top(code, open_par + 1, close_par):
                p = parse_param(code, lits, consts, s, e)
                if p:
                    params.append(p)

        tools.append({
            "name": name,
            "file": os.path.basename(path),
            "path": path,
            "method": mname.group(1) if mname else "?",
            "title": named.get("Title"),
            "promised": promised,
            "params": params,
            "code": code, "nocomment": nocom, "lits": lits,
        })
    note = "; ".join(skipped) if skipped else None
    return tools, note


def parse_param(code, lits, consts, s, e):
    """One parameter of a tool method -> dict, or None when it carries no
    [ToolParameter] (ctx and the cancellation token)."""
    i = s
    has_attr = False
    attr_args = {}
    while True:
        while i < e and code[i].isspace():
            i += 1
        if i < e and code[i] == "[":
            j = match_bracket(code, i)
            if j < 0 or j > e:
                return None
            m = ATTR_RE.match(code, i)
            if m and m.group(1) == "ToolParameter":
                has_attr = True
                op = m.end() - 1
                cp = match_bracket(code, op)
                if cp > 0:
                    for a2, b2 in split_top(code, op + 1, cp):
                        mm = re.match(r"\s*(\w+)\s*=(?!=)", code[a2:b2])
                        if mm:
                            attr_args[mm.group(1)] = value_of(
                                code, lits, consts, a2 + mm.end(), b2)
            i = j + 1
            continue
        break
    if not has_attr:
        return None
    rest = code[i:e]
    m = re.match(r"\s*([\w.<>\[\]?]+)\s+(\w+)\s*(=\s*(.*))?$", rest, re.S)
    if not m:
        return None
    default = None
    if m.group(3):
        off = i + m.start(4)
        default = value_of(code, lits, consts, off, off + len(m.group(4)))
    return {"type": m.group(1), "name": m.group(2),
            "hasDefault": bool(m.group(3)), "default": default,
            "description": attr_args.get("Description"),
            "declaredDefault": attr_args.get("DefaultValue"),
            "required": attr_args.get("Required") is True}


def load_tools():
    """-> (tools sorted by name, [notes about files that would not parse])."""
    tools, notes, sources = [], [], {}
    if not os.path.isdir(SRC):
        return [], ["source folder not found: %s" % SRC]
    for fn in sorted(os.listdir(SRC)):
        if not fn.endswith(".cs"):
            continue
        path = os.path.join(SRC, fn)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                _, nocom, _ = mask(f.read())
            sources[fn] = nocom
        except Exception as ex:
            notes.append("%s: could not be read (%s: %s)"
                         % (fn, type(ex).__name__, ex))
            continue
        try:
            found, note = parse_file(path)
        except Exception as ex:
            notes.append("%s: PARSE FAILED (%s: %s) -- this file's tools are "
                         "NOT checked in this run" % (fn, type(ex).__name__, ex))
            if os.environ.get("CONTRACT_TEST_TRACE"):
                traceback.print_exc()
            continue
        if note:
            notes.append("%s: %s" % (fn, note))
        tools.extend(found)
    return sorted(tools, key=lambda t: t["name"]), notes, sources


# ============================================================ step 2: offline

EMIT_FORMS = [
    (r'\[\s*"%s"\s*\]', 'indexer ["%s"]'),
    (r'\{\s*"%s"\s*,', 'initializer { "%s", ... }'),
    (r'\.\s*Add\s*\(\s*"%s"\s*,', '.Add("%s", ...)'),
    (r'(?<![\w.@$])%s\s*=(?!=)', 'anonymous member `%s =`'),
]


def find_emission(nocomment, key):
    """-> a sentence naming the form the key is written in, or None."""
    for pat, label in EMIT_FORMS:
        if re.search(pat % re.escape(key), nocomment):
            return label % key
    return None


# A camelCase word inside a description: `promotedOut`, `cellsNotListed`. The
# internal capital is what separates a field name from ordinary English, and it
# is why this heuristic is quiet enough to read.
PROSE_KEY_RE = re.compile(r"(?<![\w.])([a-z][a-z0-9]*(?:[A-Z][A-Za-z0-9]*)+)")

# Words that look like keys and are not.
PROSE_STOPWORDS = {"dryRun", "toString", "iEnumerable"}


def prose_keys(tool):
    """Sub-keys a [ToolResponse] DESCRIPTION names but the payload may not carry.

    `promotedOut` was never a top-level key: it was promised inside the prose of
    `aggregated`'s description, which is exactly where a promise can rot without
    anything noticing. -> [(annotation key, named key, verdict, detail)].
    """
    declared = set(p["name"] for p in tool["params"])
    # A description that names another annotated key of the same tool is not
    # promising a sub-key; step 2 already checks those on their own row.
    declared |= set(p["key"] for p in tool["promised"])
    rows = []
    for p in tool["promised"]:
        desc = p["description"] if isinstance(p["description"], str) else ""
        seen = []
        for m in PROSE_KEY_RE.finditer(desc):
            w = m.group(1)
            if w in declared or w in PROSE_STOPWORDS or w in seen:
                continue
            seen.append(w)
            if find_emission(tool["nocomment"], w):
                continue
            # It may still be a plain identifier in the file (a property that
            # is serialised under another name). Say which, rather than crying
            # wolf on a name the source does use.
            if re.search(r"(?<![\w.])%s(?![\w])" % re.escape(w),
                         tool["nocomment"]):
                rows.append((p["key"], w, "weak",
                             "named in the description of %r; the source uses "
                             "the name but never writes it as a key" % p["key"]))
            else:
                rows.append((p["key"], w, "SUSPECT",
                             "named in the description of %r and appearing "
                             "NOWHERE in %s" % (p["key"], tool["file"])))
    return rows


def top_level_keys(code, nocomment, lits):
    """Best-effort: the keys of any object initializer that carries `success`
    at its own top level. -> (keys, True) or (set(), False) when no such block
    could be identified in this file."""
    keys, found = set(), False
    for m in re.finditer(r"\bnew\b[^;={}()]*?\{", code):
        open_brace = m.end() - 1
        close = match_bracket(code, open_brace)
        if close < 0:
            continue
        here = set()
        for s, e in split_top(code, open_brace + 1, close):
            seg_lits = lits_in(lits, s, e)
            txt = code[s:e].strip()
            mm = re.match(r"^\{\s*$", txt) or None
            if txt.startswith("{") and seg_lits:
                here.add(seg_lits[0])                     # { "key", value }
            elif txt.startswith("[") and seg_lits:
                here.add(seg_lits[0])                     # ["key"] = value
            else:
                mm = re.match(r"^(\w+)\s*=(?!=)", txt)    # key = value
                if mm:
                    here.add(mm.group(1))
        if "success" in here:
            keys |= here
            found = True
    return keys, found


def offline(tools, sources, out):
    bad = 0
    out("")
    out("=" * 78)
    out("STEP 2  offline: is every promised key written anywhere in src\\ ?")
    out("=" * 78)
    for t in tools:
        own = t["nocomment"]
        rows = []
        for p in t["promised"]:
            where = find_emission(own, p["key"])
            if where:
                rows.append(("ok", p["key"], "%s, in %s" % (where, t["file"])))
                continue
            elsewhere = [fn for fn, txt in sources.items()
                         if fn != t["file"] and find_emission(txt, p["key"])]
            if elsewhere:
                rows.append(("shared", p["key"],
                             "not in %s; written in %s (a shared helper)"
                             % (t["file"], ", ".join(elsewhere))))
            else:
                rows.append(("SUSPECT", p["key"],
                             "promised%s and written NOWHERE in src\\ -- "
                             "either the code does not emit it or it is built "
                             "by a name this grep cannot see"
                             % (" with Always=true" if p["always"] else "")))
                bad += 1
        out("")
        out("%-24s %s" % (t["name"], t["file"]))
        for verdict, key, why in rows:
            out("  %-8s %-28s %s" % (verdict, key, why))
        if not rows:
            out("  (no [ToolResponse] annotations at all)")
    out("")
    out("-- %d SUSPECT key(s)." % bad)
    if bad:
        out("   A SUSPECT is not proof of a bug; it is proof that nothing in")
        out("   the source writes that name, which is what promotedOut looked")
        out("   like for the days it was missing. Check it by hand.")

    # The sub-keys the descriptions name. `promotedOut` lived HERE.
    out("")
    out("=" * 78)
    out("STEP 2c  sub-keys a description names but the source never writes")
    out("=" * 78)
    out("`promotedOut` was never a top-level key: it was promised inside the")
    out("prose of `aggregated`'s description. Every camelCase word in a")
    out("[ToolResponse] description is looked for as an emitted key here.")
    prose_bad = 0
    for t in tools:
        rows = prose_keys(t)
        if not rows:
            continue
        out("")
        out("%-24s %s" % (t["name"], t["file"]))
        for ann, word, verdict, why in rows:
            out("  %-8s %-28s %s" % (verdict, word, why))
            if verdict == "SUSPECT":
                prose_bad += 1
    out("")
    out("-- %d prose SUSPECT(s). A `weak` row is a name the file uses but does"
        % prose_bad)
    out("   not emit under that spelling: usually a C# property serialised")
    out("   under another key, occasionally a promise nobody kept.")
    bad += prose_bad

    # The reverse direction, advisory only.
    out("")
    out("=" * 78)
    out("STEP 2b  advisory: keys emitted at the top level with no annotation")
    out("=" * 78)
    out("Only promising what you emit is the house rule; annotating everything")
    out("you emit is not. This is a reading aid, and never changes the exit")
    out("code. It can only see a payload object built as one literal that")
    out("carries `success` -- where it cannot, it says so instead of guessing.")
    for t in tools:
        emitted, found = top_level_keys(t["code"], t["nocomment"], t["lits"])
        if not found:
            out("  %-24s top-level payload object not identifiable statically "
                "-- reverse check skipped (the live half does it exactly)"
                % t["name"])
            continue
        promised = set(p["key"] for p in t["promised"])
        extra = sorted(emitted - promised - CONVENTION_KEYS)
        out("  %-24s %d emitted, %d annotated%s"
            % (t["name"], len(emitted), len(promised),
               ("; not annotated: " + ", ".join(extra)) if extra else ""))
    return bad


def print_contract(tools, out):
    out("=" * 78)
    out("STEP 1  the contract, as the annotations state it")
    out("=" * 78)
    out("%-24s %-5s %-5s %s" % ("tool", "args", "keys", "promised (A = Always,"
                                " c = conditional, ? = Nullable)"))
    for t in tools:
        always = [p["key"] + ("?" if p["nullable"] else "")
                  for p in t["promised"] if p["always"]]
        cond = [p["key"] + ("?" if p["nullable"] else "")
                for p in t["promised"] if not p["always"]]
        bits = []
        if always:
            bits.append("A: " + ", ".join(always))
        if cond:
            bits.append("c: " + ", ".join(cond))
        out("%-24s %-5d %-5d %s" % (t["name"], len(t["params"]),
                                    len(t["promised"]), "  |  ".join(bits)))
    out("")
    out("Parameters, as the binder will see them:")
    for t in tools:
        bools = [p["name"] for p in t["params"] if p["type"] == "bool"]
        strs = [p["name"] for p in t["params"] if p["type"] == "string"]
        ints = [p["name"] for p in t["params"] if p["type"] not in ("bool", "string")]
        out("  %-24s bool: %-46s" % (t["name"], ", ".join(bools) or "-"))
        out("  %-24s str : %-46s" % ("", ", ".join(strs) or "-"))
        out("  %-24s num : %-46s" % ("", ", ".join(ints) or "-"))


# =============================================================== step 3: live

class Live(object):
    def __init__(self, rim, out):
        self.rim = rim
        self.out = out
        self.fails = []
        self.checks = 0
        self.advisory = []
        self.tag = ""

    def check(self, ok, what, detail=""):
        """`what` is printed bare; the failure list gets it with the tool and
        case prepended, so the summary at the end still says which call."""
        self.checks += 1
        if ok:
            self.out("    ok   %s" % what)
        else:
            self.out("    FAIL %s%s"
                     % (what, ("  -- " + str(detail)) if detail else ""))
            self.fails.append("%s%s%s" % (self.tag, what,
                                          ("  -- " + str(detail)) if detail else ""))
        return ok

    def probe(self):
        """What the argument table needs from the colony, read once."""
        ctx = {"cell": None, "pawn": None, "medCare": None,
               "building": None, "bench": None}
        try:
            cols = self.rim.game("rimworld/list_colonists", {}).get("colonists") or []
        except Exception as ex:
            self.out("  !! rimworld/list_colonists failed (%s) -- every case "
                     "that needs a colonist's cell will be SKIPPED, and the "
                     "rect halves of the contract go unchecked." % ex)
            cols = []
        alive = [c for c in cols if not c.get("dead")]
        if alive:
            p = alive[0].get("position") or {}
            if p.get("x") is not None and p.get("z") is not None:
                ctx["cell"] = (p["x"], p["z"])
        try:
            r = self.rim.game("home/list_pawns", {"settings": True})
            pawns = [p for p in r.get("pawns") or []
                     if p.get("isColonist") and not p.get("dead")]
            if pawns:
                ctx["pawn"] = pawns[0].get("name")
                s = pawns[0].get("settings") or {}
                mc = s.get("medCare")
                if isinstance(mc, str):
                    ctx["medCare"] = mc
        except Exception as ex:
            self.out("  !! home/list_pawns{settings} failed (%s) -- the "
                     "pawn_config cases will be SKIPPED." % ex)
        try:
            r = self.rim.game("home/list_buildings", {"playerOnly": True})
            rows = [b for b in r.get("buildings") or []
                    if b.get("defName") and (b.get("position") or {}).get("x") is not None]

            def handle(b):
                return "%s@%d,%d" % (b["defName"], b["position"]["x"], b["position"]["z"])
            if rows:
                ctx["building"] = handle(rows[0])
            benches = [b for b in rows if "bills" in b]
            if benches:
                ctx["bench"] = handle(benches[0])
        except Exception as ex:
            self.out("  !! home/list_buildings failed (%s) -- the "
                     "building_config and bills cases will be SKIPPED." % ex)
        self.out("  probe: colonist cell %s, pawn %r, medCare %r, building %r, bench %r"
                 % (ctx["cell"], ctx["pawn"], ctx["medCare"], ctx["building"], ctx["bench"]))
        return ctx

    def cases_for(self, tool, ctx):
        """[(label, args)] -- the default call, the bool sweep, the table."""
        name = tool["name"]
        cases = [("defaults", {})]
        if name not in EXPLICIT_ONLY:
            for p in tool["params"]:
                if p["type"] == "bool" and p["default"] is not True:
                    cases.append(("%s=true" % p["name"], {p["name"]: True}))
        for label, spec in CASES.get(name, []):
            args = spec(ctx) if callable(spec) else dict(spec)
            if args is None:
                self.out("    ---- %s: SKIPPED, no safe arguments could be "
                         "built from this colony" % label)
                continue
            cases.append((label, args))
        cases.append(("a bogus key", {"contractTestBogusKey": 1}))
        covered = set()
        for _, a in cases:
            covered |= set(ARG_RENAMES.get(k, k) for k in a)
        unset = [p["name"] for p in tool["params"]
                 if p["type"] == "string" and p["name"] not in covered]
        if unset:
            self.out("    ---- string parameters left unset (no safe value in "
                     "CASES): %s" % ", ".join(unset))
        return cases

    def call(self, tool, label, args):
        name = tool["name"]
        for k, v in FORBIDDEN_ARGS.items():
            assert args.get(k, "absent") != v, \
                "contract_test refuses to send %s=%r to %s" % (k, v, name)
        assert name not in HARD_SKIP, "contract_test refuses to call " + name
        sent = dict((ARG_RENAMES.get(k, k), v) for k, v in args.items())
        self.out("\n  %s  %s" % (label, json.dumps(sent)))
        try:
            return self.rim.game(name, sent, strict=False)
        except Exception as ex:
            self.check(False, "%s [%s]: answered at all" % (name, label),
                       "%s: %s" % (type(ex).__name__, ex))
            return None

    def assert_contract(self, tool, label, args, reply):
        name = tool["name"]
        self.tag = "%s [%s] " % (name, label)
        if not isinstance(reply, dict):
            self.check(False, "reply is an object",
                       "got %s: %r" % (type(reply).__name__, str(reply)[:160]))
            return "error"
        if not self.check("success" in reply, "carries success"):
            return "error"
        if "contractTestBogusKey" in args:
            self.check(reply.get("unknownArguments") == ["contractTestBogusKey"],
                       "unknownArguments names the bogus key",
                       repr(reply.get("unknownArguments")))
            self.check(bool(reply.get("unknownArgumentsWarning")),
                       "a warning sentence came with it")
            return "bogus"
        ua = reply.get("unknownArguments")
        self.check(ua == [], "unknownArguments == [] (every name we sent was "
                             "declared)", repr(ua))
        if reply.get("success") is not True:
            why = reply.get("message") or reply.get("error")
            self.out("    ---- REFUSED: %s" % str(why)[:200])
            self.check(bool(why), "a refusal says why (message or error)",
                       "success:false with no reason is a dead end for the "
                       "caller: repr is %r" % (str(reply)[:160],))
            if name not in CHECK_ALWAYS_ON_REFUSAL:
                self.out("         (a refusal is a legal answer; the other Always "
                         "keys are NOT checked on it)")
                return "refused"
            self.out("         (this tool promises its full shape on refusals; "
                     "checking the Always keys below)")
        for p in tool["promised"]:
            if p["always"] and p["key"] not in reply:
                self.check(False, "MISSING promised key %r (Always=true)"
                           % p["key"],
                           "the annotation promises it on every reply")
            elif p["key"] in reply:
                self.check(p["nullable"] or reply[p["key"]] is not None,
                           "%r present%s" % (p["key"],
                                             " (null, and declared Nullable)"
                                             if reply[p["key"]] is None else ""),
                           "NULL, and its annotation does not say "
                           "Nullable = true")
        promised = set(p["key"] for p in tool["promised"])
        extra = sorted(set(reply) - promised - CONVENTION_KEYS)
        if extra:
            self.advisory.append("%s emits %d top-level key(s) with no "
                                 "[ToolResponse]: %s"
                                 % (name, len(extra), ", ".join(extra)))
        return "ok"

    def run(self, tools):
        self.out("")
        self.out("=" * 78)
        self.out("STEP 3  live: every tool against its own promises")
        self.out("=" * 78)
        self.out("READ-ONLY and DRY-RUN. Nothing is saved, unpaused, selected "
                 "or written.")
        ctx = self.probe()
        never = []
        for t in tools:
            if t["name"] in HARD_SKIP:
                self.out("\n%s\n  SKIPPED ON PURPOSE: %s"
                         % (t["name"], HARD_SKIP[t["name"]]))
                continue
            self.out("\n" + "-" * 78)
            self.out(t["name"])
            outcomes = []
            for label, args in self.cases_for(t, ctx):
                reply = self.call(t, label, args)
                if reply is None:
                    outcomes.append("error")
                    continue
                outcomes.append(self.assert_contract(t, label, args, reply))
            if "ok" not in outcomes:
                never.append(t["name"])
        return never


# ========================================================================= go

def main():
    ap = argparse.ArgumentParser(
        description="Check every home/ tool against its [ToolResponse] "
                    "annotations.")
    ap.add_argument("--offline", action="store_true",
                    help="parse and grep the sources only; never touch a game")
    ap.add_argument("--tool", action="append", default=[],
                    help="restrict to this tool name (repeatable)")
    args = ap.parse_args()

    lines = []

    def out(s=""):
        print(s)
        lines.append(s)

    tools, notes, sources = load_tools()
    if args.tool:
        want = set(args.tool)
        tools = [t for t in tools if t["name"] in want]
        missing = want - set(t["name"] for t in tools)
        for m in sorted(missing):
            out("!! --tool %s matched no [Tool] in %s" % (m, SRC))
    if notes:
        out("!! source notes -- these files were NOT fully read:")
        for n in notes:
            out("   " + n)
        out("")
    if not tools:
        out("No home/ tools parsed out of %s. Nothing was checked." % SRC)
        return 1

    print_contract(tools, out)
    suspects = offline(tools, sources, out)

    if args.offline:
        out("")
        out("offline only (--offline): the live half was not run.")
        return 1 if suspects else 0

    sys.path.insert(0, INSTRUMENTS)
    try:
        import rim
    except Exception as ex:
        out("")
        out("!! could not import rim from %s (%s: %s) -- the live half cannot "
            "run. Use --offline." % (INSTRUMENTS, type(ex).__name__, ex))
        return 1
    rim.init()
    live = Live(rim, out)
    never = live.run(tools)

    out("")
    out("=" * 78)
    out("%d of %d live checks passed." % (live.checks - len(live.fails),
                                          live.checks))
    for f in live.fails:
        out("  FAIL " + f)
    if never:
        out("")
        out("NEVER EXERCISED -- every case refused, so no Always key was ever")
        out("checked on these. Give them safe arguments in CASES:")
        for n in never:
            out("  " + n)
    if live.advisory:
        out("")
        out("advisory (does not affect the exit code):")
        for a in live.advisory:
            out("  " + a)
    if suspects:
        out("")
        out("%d offline SUSPECT key(s) above still stand." % suspects)
    return 1 if (live.fails or suspects or never) else 0


if __name__ == "__main__":
    sys.exit(main())
