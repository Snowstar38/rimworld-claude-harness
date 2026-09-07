"""Safe combat-session ledger. Stage 1 never advances game time.

  python combat.py begin
  python combat.py status
  python combat.py move <pawn> <x> <z>
  python combat.py flee <pawn> <x> <z>
  python combat.py equip <pawn> <x> <z> (--weapon-id ID | --weapon-label LABEL)
  python combat.py attack <pawn> <target> [--mode auto|melee|ranged] [--dry-run]
  python combat.py tend <doctor> <patient> [--dry-run]  (ground tend may draft)
  python combat.py rescue <pawn> <patient> [--dry-run]
  python combat.py draft <pawn>
  python combat.py undraft <pawn> [--dry-run] [--force]
  python combat.py advance [seconds] [--resume]
  python combat.py end [--dry-run] [--force]
  python combat.py release <pawn> [--dry-run] [--force]

The ledger is deliberately write-ahead: future combat orders must call
``record_draft_intent`` before drafting and ``confirm_drafted`` afterwards.
An extra cleanup obligation is safe; an unrecorded drafted pawn is not.

``--force`` on ``end`` also means *abandon*: it drops obligations for pawns
who are no longer colonists (dead, captured, off-map) and discards a STALE
ledger without touching the game.  ``advance`` refuses after a person paused
or changed speed during the previous pulse until ``--resume`` is passed.

``end`` refuses while a CONSCIOUS hostile is on the map, and names ``--force``.
When every hostile still counted is DOWNED it proceeds and prints a reminder
naming them, because a downed raider is a thing to finish off or capture and a
downed animal stands back up -- not a reason to hold five colonists drafted.

## attack / draft / undraft go through ``home/order`` -- 2026-09-04

Every order verb here used to drive RimWorld's float menu and decide for
itself whether the order was legal.  On the night of the Lampblack stream that
cost two colonists: ``attack`` refused a wolf by ThingID ("not one unambiguous
current target"), refused a downed muffalo one cell away, required the target
to still be *hostile* -- and the fork reading those refusals concluded the tool
could not be used at all, drafted nobody, and watched a pawn incapable of
violence get killed.

So a refusal now comes from the GAME and says which of the fifteen possible
reasons it is (``errorKind``), and nothing in Python decides a pawn "cannot be
drafted".  Every order is a ``dryRun`` resolve first -- ambiguity prints the
candidates, and every reply prints the ``idForms`` the caller may use next time
-- and then the real call.  Hostility is NOT required: vanilla lets a drafted
pawn hit any animal, and so does this.  ``--mode melee|ranged`` overrides the
game's own choice of verb.

**If an order is refused, that is information, not a wall.** Read
``errorKind``, fix that one thing, order again.  The refusal costs no game time.
"""
import argparse
import json
import os
import tempfile
import time

import clock
import rim
import status as colony_status
import move as move_orders
import combat_actions
import combat_camera

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state")
LEDGER = os.path.join(STATE, "combat-session.json")
SESSION = os.path.join(STATE, "session.json")
VERSION = 1
STREAM_STATE = os.path.join(STATE, "stream.json")
COMBAT_EVENT_TOOL = "home/play_until_event"
COMBAT_ORDER_TOOL = "home/order"
COMBAT_POLL_MS = 250
# Hediff_Injury.Severity is HIT POINTS (a gunshot is ~10-18, a scratch 2-5),
# summed over a pawn's injuries and compared against the pulse's entry
# baseline. Bleed rate is RimWorld's per-day fraction. 2026-09-04: these were
# 0.15 / 0.05, below the smallest possible wound, so every hit stopped time.
COMBAT_INJURY_SEVERITY_DELTA = 8.0
# ...and 8 HP is still every pulse when the pawn is the one SWINGING: a melee
# brawl trades scratches continuously, so `advance 20` returned ~1 heartbeat of
# game time per call while Longhoff and Ian were killing the wolf. A pawn we
# ordered INTO a fight is expected to take hits; the question is whether the
# fight is going badly. 20 HP is roughly a serious wound rather than a graze.
#
# What still catches a pawn going DOWN at any threshold: `colonist_downed`,
# the companion's own downed-colonist watch, which is not an injury poll and
# has no delta at all -- a downed colonist stops the pulse whatever this says.
# Bleeding still stops on COMBAT_INJURY_BLEED_DELTA, unchanged.
COMBAT_ATTACK_INJURY_SEVERITY_DELTA = 20.0
COMBAT_INJURY_BLEED_DELTA = 0.1
# Orders that mean "this pawn is swinging", for the threshold above.
ATTACK_ORDER_KINDS = ("attack",)
# Same 300 s per alert label as run.py's ALERT_DEBOUNCE, for the same reason:
# a break-risk alert that has been standing for minutes is not news, and two
# `advance 20` pulses burned for ~0 game time on one during the stream. The
# companion keeps the memo, so it survives across calls the way run.py's does.
COMBAT_ALERT_DEBOUNCE_MS = 300000
DEFAULT_ADVANCE_SECONDS = 5.0
# PLAYBOOK: keep pulses at 20 s or less; the companion runs one call at a
# time, so a long pulse blocks every other bridge call for its duration.
MAX_ADVANCE_SECONDS = 20.0
DEFAULT_EQUIP_WAIT_SECONDS = 15.0
# Stops caused by a person. The next advance must be an explicit decision.
HUMAN_STOPS = ("external_pause", "speed_changed", "force_paused")
# Stops that mean the companion did NOT do what it was asked; time may still
# be running, so the caller pauses and refuses rather than recording a pulse.
FAILED_STOPS = ("error", "unavailable", "busy", "cancelled", "session_changed")


class CombatRefusal(RuntimeError):
    pass


class OwnershipRefusal(CombatRefusal):
    """A local turn-owner gate; handling this must not touch the game/ledger."""
    pass


class SupervisedPlayRefusal(CombatRefusal):
    """Durable supervisor owns the clock; refusal must not pause or rewrite."""
    pass


def supervised_status(call=None):
    """Cheap read of the persistent clock owner; failures mean unavailable."""
    try:
        invoke = call or (lambda p: rim.game("home/supervised_play", p,
                                             strict=False, timeout=3))
        row = invoke({"op": "status"})
        return row if isinstance(row, dict) else None
    except Exception:
        return None


def refuse_if_supervised(call=None):
    row = supervised_status(call)
    if row and row.get("active"):
        raise SupervisedPlayRefusal(
            "durable supervised play already owns the clock (epoch %s, speed %s); "
            "use `python play.py status`, `python play.py speed ...`, or "
            "`python play.py pause` instead of combat.py advance"
            % (row.get("epoch"), row.get("requestedSpeed")))
    return row


def pause_supervised_for_cleanup(pause_func=None, status_call=None):
    row = supervised_status(status_call)
    if not row or not row.get("active"):
        return False
    if pause_func is None:
        import play
        pause_func = play.pause
    result = pause_func()
    if result not in (None, 0):
        raise CombatRefusal("supervised play could not be paused before combat cleanup")
    return True


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise CombatRefusal("cannot safely read %s: %s" % (path, e))


def load_ledger(path=LEDGER):
    return _read_json(path)


def _atomic_write(data, path=LEDGER):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".combat-", suffix=".tmp",
                               dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _identity(session_path=SESSION):
    s = _read_json(session_path)
    if not s or not s.get("save"):
        raise CombatRefusal("loaded save identity is unknown; run setup.py to load/identify it")
    return {k: s.get(k) for k in ("save", "loadedAt", "loadedAtTick", "pid")}


def turn_identity(path=STREAM_STATE):
    """Current harness turn, from local state only. None outside Hands."""
    state = _read_json(path) or {}
    if state.get("handsEndedAt") is not None or not state.get("handsStartedAt"):
        return None
    return {"turn": int(state.get("turn") or 0),
            "handsStartedAt": state.get("handsStartedAt")}


def inherited_warning(ledger=None, current=None):
    """Actionable warning when this turn did not create/adopt the ledger."""
    ledger = load_ledger() if ledger is None else ledger
    current = turn_identity() if current is None else current
    if not ledger or not ledger.get("active") or not current:
        return None
    owner = ledger.get("turnOwner")
    if owner == current:
        return None
    old = "an older/unknown turn" if not owner else "turn %s" % owner.get("turn")
    return ("!! COMBAT LEDGER INHERITED from %s; cleanup obligations are preserved. "
            "Run `python combat.py adopt` before issuing this turn's combat "
            "orders, or `python combat.py end` to restore and close it." % old)


def adopt_turn(path=LEDGER, current=None):
    ledger = load_ledger(path)
    if not ledger or not ledger.get("active"):
        raise CombatRefusal("no active combat session to adopt")
    current = turn_identity() if current is None else current
    if not current:
        raise CombatRefusal("no Hands turn is open; run `python stream.py hands-start` first")
    ledger["turnOwner"] = current
    _atomic_write(ledger, path)
    return ledger


def stale_reason(ledger, tick, identity):
    old = ledger.get("identity") or {}
    for key in ("save", "loadedAt", "pid"):
        if old.get(key) != identity.get(key):
            return "loaded session identity changed (%s)" % key
    watermark = ledger.get("watermarkTick", ledger.get("startTick"))
    if tick is not None and watermark is not None and tick < watermark:
        return "game tick regressed from %s to %s (save was reloaded)" % (watermark, tick)
    return None


def _tick(snapshot):
    return (snapshot.get("time") or {}).get("ticksGame")


def _pawns(snapshot):
    # home/status calls the stable identifier `thingId`; list_colonists calls
    # the same value `pawnId`. Combat accepts either because its fixtures and
    # live snapshot must exercise the actual producer contract, not one alias.
    rows = {}
    for pawn in snapshot.get("colonists") or []:
        pawn_id = pawn.get("pawnId") or pawn.get("thingId")
        if pawn_id is not None:
            rows[str(pawn_id)] = pawn
    return rows


def begin(snapshot, identity, path=LEDGER, turn_owner=None):
    existing = load_ledger(path)
    if existing:
        stale = stale_reason(existing, _tick(snapshot), identity)
        if stale:
            raise CombatRefusal("a STALE combat ledger exists (%s); run "
                                "`python combat.py end --force` to discard it, "
                                "then begin again" % stale)
        return existing, False
    tick = _tick(snapshot)
    if snapshot.get("status") != "game_loaded" or tick is None:
        raise CombatRefusal("no readable loaded game; combat session not started")
    pawns = {}
    for pid, p in _pawns(snapshot).items():
        pawns[pid] = {"name": p.get("name") or pid,
                      "originalDrafted": bool(p.get("drafted"))}
    ledger = {"version": VERSION, "active": True, "cleanupPending": False,
              "createdAt": time.time(), "identity": identity,
              "turnOwner": turn_owner if turn_owner is not None else turn_identity(),
              "startTick": tick, "watermarkTick": tick, "pawns": pawns,
              "draftObligations": {}, "orders": {}, "ui": {}}
    _atomic_write(ledger, path)
    return ledger, True


def record_draft_intent(pawn_id, name=None, path=LEDGER):
    """Durably record an obligation BEFORE a caller invokes set_draft."""
    ledger = load_ledger(path)
    if not ledger:
        raise CombatRefusal("no active combat session")
    pid = str(pawn_id)
    base = (ledger.get("pawns") or {}).get(pid)
    if base is None:
        raise CombatRefusal("%s was not present at combat begin" % (name or pid))
    ledger.setdefault("draftObligations", {})[pid] = {
        "name": name or base.get("name") or pid, "state": "intent",
        "recordedAt": time.time()}
    ledger["cleanupPending"] = True
    _atomic_write(ledger, path)


def confirm_drafted(pawn_id, tick=None, path=LEDGER):
    ledger = load_ledger(path)
    pid = str(pawn_id)
    obligation = (ledger or {}).get("draftObligations", {}).get(pid)
    if not obligation:
        raise CombatRefusal("no write-ahead draft intent for %s" % pid)
    obligation["state"] = "confirmed"
    if tick is not None:
        ledger["watermarkTick"] = max(ledger.get("watermarkTick") or tick, tick)
    _atomic_write(ledger, path)


def _resolve_session_pawn(token, ledger):
    """The ledger's pawn id for any id form ``home/order`` accepts.

    2026-09-04: this used to take the ledger key or an exact name and nothing
    else, so `combat.py move Thing_Human334862 ...` and `move 334862` both
    refused a pawn who was standing right there.  The companion accepts
    ``Thing_Human123``, ``Human123``, ``123`` or a name, and the four verbs in
    this file now accept the same four, because an operator who has a ThingID
    from `status` should never have to translate it by hand mid-fight.
    """
    pawns = ledger.get("pawns") or {}
    tok = str(token).strip()
    if tok in pawns:
        return tok
    low = tok.lower()
    hits = [pid for pid, row in pawns.items()
            if (row.get("name") or "").lower() == low]
    if not hits:
        # Id forms: the whole id in another case, a suffix after the prefix
        # ("Human123" for "Thing_Human123"), or the bare number.
        hits = [pid for pid in pawns
                if pid.lower() == low or pid.lower().endswith("_" + low)
                or (tok.isdigit() and pid.rstrip("0123456789") != pid
                    and pid[len(pid.rstrip("0123456789")):] == tok)]
    if len(hits) != 1:
        roster = ", ".join(sorted("%s=%s" % ((row.get("name") or "?"), pid)
                                  for pid, row in pawns.items())) or "none"
        raise CombatRefusal("pawn %r is not one unambiguous colonist at combat "
                            "begin (%d matches; the session knows: %s)"
                            % (token, len(hits), roster))
    return hits[0]


# ------------------------------------------------------- home/order plumbing

def _order_call(caller=None):
    """The one place a real ``home/order`` call is built. strict=False: a
    refusal is the tool ANSWERING, with the reason, and must be read -- not
    raised past the caller as a bridge fault."""
    return caller or (lambda params: rim.game(COMBAT_ORDER_TOOL, params,
                                              strict=False))


def _order_reply(reply, what):
    if not isinstance(reply, dict):
        raise CombatRefusal("%s did not answer %s (%r)"
                            % (COMBAT_ORDER_TOOL, what, reply))
    return reply


def describe_target(target):
    """What we are about to hit, in the game's own words.

    Printed on the VERIFIED line because the operator must see that the thing
    they just ordered an attack on is (for instance) NOT hostile and IS a
    predator -- the two facts that decided the Lampblack stream.
    """
    if not target:
        return "target not reported"
    bits = ["hostile" if target.get("hostileToPlayer") else "NOT hostile"]
    if target.get("predator"):
        bits.append("PREDATOR")
    if target.get("downed"):
        bits.append("downed")
    if target.get("dead"):
        bits.append("DEAD")
    if target.get("mentalState"):
        bits.append("mental %s" % target["mentalState"])
    bits.append("faction %s" % (target.get("faction") or "none/wild"))
    if target.get("distance") is not None:
        bits.append("%s cells" % target["distance"])
    if target.get("reachable") is False:
        bits.append("NOT reachable")
    return ", ".join(bits)


def describe_pawn(pawn):
    """The game's own answer to "can this one act?" -- never our guess."""
    if not pawn:
        return "pawn not reported"
    bits = ["drafted" if pawn.get("drafted") else "undrafted"]
    if pawn.get("incapableOfViolence"):
        bits.append("INCAPABLE OF VIOLENCE")
    if pawn.get("canBeDrafted") is False:
        bits.append("canBeDrafted=False")
    elif pawn.get("canBeDrafted") is True:
        bits.append("canBeDrafted=True")
    if pawn.get("downed"):
        bits.append("downed")
    if pawn.get("dead"):
        bits.append("DEAD")
    if pawn.get("mentalState"):
        bits.append("mental %s" % pawn["mentalState"])
    if pawn.get("playerControlled") is False:
        bits.append("not player-controlled")
    weapon = pawn.get("weapon")
    bits.append("weapon %s" % (weapon.get("label") if weapon else "none (fists)"))
    return ", ".join(bits)


def print_order_facts(reply):
    """Print what the reply taught us: id forms, candidates, watch, pawn state.

    The id forms are printed on success too. A caller who has just watched
    `Rat` fail as ambiguous needs to be handed the shapes that would not have
    been -- that is the whole difference between one refusal and a fork
    deciding the tool is unusable.
    """
    target = (reply or {}).get("target") or {}
    forms = target.get("idForms") or []
    if forms:
        print("   target id forms: %s" % ", ".join(str(f) for f in forms))
    for c in (reply or {}).get("candidates") or []:
        pos = c.get("position") or {}
        print("   candidate: %-22s %-18s %s at %s,%s  %s cells"
              % (str(c.get("thingId")), str(c.get("name") or "?"),
                 str(c.get("defName") or "?"), pos.get("x", "?"),
                 pos.get("z", "?"), c.get("distance", "?")))
    for arg in (reply or {}).get("unknownArguments") or []:
        print("   !! home/order ignored an argument it does not know: %s" % arg)


# --------------------------------------------------------------- the clock ---
# 2026-09-07: a wolf survived four orders that each printed VERIFIED, and
# `flee` printed FLEE VERIFIED while the pawn stood still through a manhunter
# attack. Every order had landed; the clock was stopped, so none of them ran.
#
# Stage 1 pauses ON PURPOSE (`_live_snapshot(pause=True)`), so a combat order is
# ALWAYS queued rather than done, and the only word that was ever true here is
# QUEUED. `advance` is what makes it happen. See clock.py.
CLOCK_HINT = ("combat.py itself -- stage 1 pauses before it issues any order, "
              "by design")
CLOCK_EXTRA = ("   in a combat session the bounded way to run it is: "
               "python combat.py advance 20",)


def clock_state(snapshot=None, hint=CLOCK_HINT):
    """The clock verdict for an order, from the snapshot already in hand.

    `snapshot` is the `home/status` reply `_live_snapshot` just took, so this
    costs NO extra bridge call. It was read after the pause and before the
    order, and an order cannot start the clock -- so the only way this is wrong
    is if a person pressed play in between, which makes it say QUEUED for a
    clock that is running. That is the safe direction to be wrong in.
    """
    if isinstance(snapshot, dict) and isinstance(snapshot.get("time"), dict):
        return clock.state(reader=lambda: snapshot, hint=hint)
    return clock.state(hint=hint)


def order_headline(kind, detail, snapshot=None, state=None):
    """`MOVE VERIFIED  ...` only when time is running; `MOVE QUEUED  ...` else.

    Returns the clock state so a caller can shape what it prints next.
    """
    st = clock_state(snapshot) if state is None else state
    print("%s %s  %s" % (kind, clock.word(st), detail))
    for row in clock.notes(st, extra=CLOCK_EXTRA):
        print(row)
    return st


def watch_line(reply, on=None):
    """One line about what the screen was made to show. Same shape as
    trade.py's watch_line, and it claims the camera for Hands the same way, so
    the Lookout does not pan away from an order the stream just watched go in.
    """
    w = (reply or {}).get("watch") or {}
    if not w.get("shown"):
        print("watch: skipped (%s)" % (w.get("reason") or "not shown"))
        return
    if w.get("cameraMoved"):
        try:
            import camlock
            camlock.claim("hands", "order watch")
        except Exception:
            pass
    who = (" on " + on) if on else ""
    print("watch: %s%s, clears in %s s"
          % ("selected" if w.get("selected") else "camera moved", who,
             w.get("closesAfterSeconds")))


def _order_refusal(reply, what):
    """A CombatRefusal naming the game's own errorKind. Prints the evidence."""
    print_order_facts(reply)
    return CombatRefusal(
        "%s refused by the game [%s]: %s -- this is one named reason, not a "
        "wall; fix that and order again (it cost no game time)"
        % (what, reply.get("errorKind") or "no errorKind",
           reply.get("error") or "no reason given"))


def resolve_pawn(pawn, caller=None):
    """A dry ``home/order resolve`` read of one pawn. Never raises, never
    mutates, never moves the camera (``watch: false``): it exists so a failure
    path can print what the GAME says about drafting instead of asserting it.
    """
    try:
        reply = _order_call(caller)({"action": "resolve", "pawn": str(pawn),
                                     "dryRun": True, "watch": False})
    except Exception as e:
        return {"success": False, "error": str(e), "errorKind": "unavailable"}
    return reply if isinstance(reply, dict) else {"success": False,
                                                  "error": repr(reply)}


def print_pawn_verdict(pawn, caller=None):
    """Print the game's verdict on a pawn after an order failed. Never raises.

    The Lampblack fork asserted "he cannot be drafted" about a pawn it had
    never asked about, and then drafted nobody at all. Incapable of violence
    and undraftable are DIFFERENT things -- a pawn incapable of violence can be
    drafted and moved, they just cannot be ordered to attack -- and only the
    game knows which is true. So ask it, and print the answer.
    """
    reply = resolve_pawn(pawn, caller)
    row = (reply or {}).get("pawn")
    if row:
        print("   the game says of %s: %s"
              % (row.get("name") or pawn, describe_pawn(row)))
        if row.get("incapableOfViolence") and row.get("canBeDrafted"):
            print("   (incapable of violence is NOT undraftable: they can still "
                  "be drafted and MOVED out of danger.)")
    else:
        print("   could not read %s from %s: %s"
              % (pawn, COMBAT_ORDER_TOOL,
                 (reply or {}).get("error") or "no answer"))
    return reply


def issue_move(pawn, x, z, snapshot, identity, path=LEDGER, issuer=None,
               order_kind="move"):
    """Issue one verified Goto and persist it; never advance game time."""
    ledger = load_ledger(path)
    if not ledger or not ledger.get("active"):
        raise CombatRefusal("no active combat session; run `python combat.py begin`")
    stale = stale_reason(ledger, _tick(snapshot), identity)
    if stale:
        raise CombatRefusal("STALE ledger: %s; refusing movement" % stale)
    pid = _resolve_session_pawn(pawn, ledger)
    name = ledger["pawns"][pid].get("name") or pid

    def before_draft(row):
        record_draft_intent(pid, row.get("name") or name, path)

    issue = issuer or move_orders.issue_goto
    result = issue(pid, int(x), int(z), before_draft=before_draft)
    if not result or result.get("accepted") is not True:
        raise CombatRefusal("move primitive returned without verified acceptance")
    tick = _tick(snapshot)
    if result.get("autoDrafted"):
        confirm_drafted(pid, tick=tick, path=path)

    # Reload after callbacks so this write cannot overwrite their obligation.
    ledger = load_ledger(path)
    stale = stale_reason(ledger, tick, identity)
    if stale:
        raise CombatRefusal("session became stale before order recording: %s" % stale)
    order = dict(result)
    order["kind"] = order_kind
    order["verifiedTick"] = tick
    order["recordedAt"] = time.time()
    ledger.setdefault("orders", {})[pid] = order
    ledger.setdefault("pendingDecisions", {}).pop(pid, None)
    if tick is not None:
        ledger["watermarkTick"] = max(ledger.get("watermarkTick") or tick, tick)
    _atomic_write(ledger, path)
    return order


def issue_flee(pawn, x, z, snapshot, identity, path=LEDGER, issuer=None):
    """Issue a move tagged for immediate injury stopping during advance."""
    return issue_move(pawn, x, z, snapshot, identity, path, issuer,
                      order_kind="flee")


def record_order_failure(pawn, reason, message, path=LEDGER, **extra):
    """Latch a failed order so no later command advances time for that pawn.

    A token that names no session pawn mutated nothing, so it latches nothing:
    a typo must not wedge the session behind a decision no order can clear.
    """
    ledger = load_ledger(path)
    if not ledger or not ledger.get("active"):
        return
    try:
        pid = _resolve_session_pawn(pawn, ledger)
    except CombatRefusal:
        return
    row = {"reason": reason, "error": str(message),
           "atTick": ledger.get("watermarkTick")}
    row.update(extra)
    ledger.setdefault("pendingDecisions", {})[pid] = row
    _atomic_write(ledger, path)


def record_move_failure(pawn, x, z, message, path=LEDGER):
    """Latch a failed movement decision so no later command advances time."""
    record_order_failure(pawn, "move failed", message, path,
                         destination={"x": int(x), "z": int(z)})


def _checked_session(pawn, snapshot, identity, path, operation):
    ledger = load_ledger(path)
    if not ledger or not ledger.get("active"):
        raise CombatRefusal("no active combat session; run `python combat.py begin`")
    stale = stale_reason(ledger, _tick(snapshot), identity)
    if stale:
        raise CombatRefusal("STALE ledger: %s; refusing %s" % (stale, operation))
    pid = _resolve_session_pawn(pawn, ledger)
    return ledger, pid, ledger["pawns"][pid].get("name") or pid


# 2026-09-04: `_ensure_combat_drafted` lived here, drafting through
# `rimworld/set_draft` for the old menu-driven attack. `home/order` drafts as
# part of the order it was given and REPORTS it (`pawn.autoDrafted`), so the
# write-ahead pair is now `_draft_bookkeeping` / `_confirm_draft_bookkeeping`
# above and there is no second place that drafts. `move` still goes through
# `move_orders._ensure_drafted`, which is the goto path's own precondition.


def _record_action(pid, result, snapshot, identity, path):
    ledger = load_ledger(path)
    tick = _tick(snapshot)
    stale = stale_reason(ledger, tick, identity)
    if stale:
        raise CombatRefusal("session became stale before order recording: %s" % stale)
    order = dict(result)
    order["verifiedTick"] = tick
    order["recordedAt"] = time.time()
    ledger.setdefault("orders", {})[pid] = order
    ledger.setdefault("pendingDecisions", {}).pop(pid, None)
    if tick is not None:
        ledger["watermarkTick"] = max(ledger.get("watermarkTick") or tick, tick)
    _atomic_write(ledger, path)
    return order


def issue_equip(pawn, x, z, snapshot, identity, weapon_id=None,
                weapon_label=None, path=LEDGER, issuer=None):
    """Issue Equip and record acceptance separately from completion."""
    _, pid, name = _checked_session(pawn, snapshot, identity, path, "equipment")
    action = issuer or combat_actions.issue_equip
    result = action(pid, int(x), int(z), weapon_thing_id=weapon_id,
                    weapon_label=weapon_label)
    if not result or result.get("accepted") is not True:
        raise CombatRefusal("equip primitive returned without verified acceptance")
    result.setdefault("pawnName", name)
    return _record_action(pid, result, snapshot, identity, path)


def finish_equip(ticket, seconds, snapshot, identity, path=LEDGER,
                 equipment_waiter=None, advance_waiter=None):
    """Observe equip through one guarded combat pulse; never sleep or step."""
    def guarded(predicate, seconds):
        if predicate():
            return "already_equipped", None
        result = advance(seconds, snapshot, identity, path=path,
                         waiter=advance_waiter)
        return result.get("stopReason"), result.get("stopDetail")
    wait = equipment_waiter or combat_actions.wait_for_equipment
    completed = wait(ticket, guarded, seconds=seconds)
    # advance may have raised or persisted a newer watermark, so retain it.
    fresh = load_ledger(path)
    pid = str(ticket["pawnId"])
    completed["recordedAt"] = time.time()
    fresh.setdefault("orders", {})[pid] = completed
    _atomic_write(fresh, path)
    return completed


def _draft_bookkeeping(pid, name, probe_pawn, snapshot, path):
    """Write-ahead half of an order that may draft. Returns whether we owe one.

    The obligation is written BEFORE the call that could draft, exactly as the
    move path does, because a process that dies between the call and its reply
    must leave an extra obligation rather than a forgotten drafted colonist.
    """
    drafted = (probe_pawn or {}).get("drafted")
    if drafted is None:
        drafted = bool((_pawns(snapshot).get(pid) or {}).get("drafted"))
    if drafted:
        return False
    record_draft_intent(pid, (probe_pawn or {}).get("name") or name, path)
    return True


def _confirm_draft_bookkeeping(pid, name, owed, reply_pawn, snapshot, path):
    """Second half, and it runs BEFORE any refusal is raised.

    A refusal after the game already drafted somebody is the dangerous case:
    the order failed, the colonist is standing in the open drafted, and the
    ledger is the only thing that will ever undraft them. So `autoDrafted` on
    the reply is honoured whether or not the order itself succeeded.
    """
    reply_pawn = reply_pawn or {}
    if reply_pawn.get("autoDrafted") and not owed:
        # The game drafted them though our read said otherwise. Record the
        # obligation now rather than lose it -- late is better than never.
        record_draft_intent(pid, reply_pawn.get("name") or name, path)
        owed = True
    if owed and (reply_pawn.get("autoDrafted") or reply_pawn.get("drafted")):
        confirm_drafted(pid, tick=_tick(snapshot), path=path)
        return True
    return False


def _issue_targeted_order(action, pawn, target, snapshot, identity, path,
                          issuer, extra=None, may_draft=True):
    """Dry probe -> write-ahead draft -> real call -> confirm. The shared core.

    Every verb here can draft (``tend`` provably does: RimWorld's ground-tend
    float option, `FloatMenuOptionProvider_DraftedTend`, exists ONLY while the
    doctor is drafted), so every verb needs the same write-ahead pair and the
    same rule that a refusal AFTER an auto-draft still owes the undraft.
    ``may_draft=False`` is for the verbs that provably need no draft change
    (`rescue`, `equip`): they ask the game not to draft AND skip the
    write-ahead intent, because an obligation recorded for an order that never
    drafts anybody is a false "!! UNFINISHED COMBAT" on the status board for
    the rest of the fight. The game drafting anyway is still caught -- the
    confirm half records the obligation late rather than losing it.

    Returns ``(reply, pid, name, auto_drafted)``.
    """
    _, pid, name = _checked_session(pawn, snapshot, identity, path, action)
    call = _order_call(issuer)
    base = {"action": action, "pawn": pid, "draft": bool(may_draft)}
    if action == "tend":
        # The ledger below is the lifecycle owner that makes this safe.
        base["allowPersistentDraft"] = True
    if target is not None:
        base["target"] = str(target)
    base.update(extra or {})
    probe = _order_reply(call(dict(base, dryRun=True, watch=False)),
                         "the %s dry run" % action)
    if probe.get("success") is not True:
        raise _order_refusal(probe, "%s (dry run)" % action)
    print_order_facts(probe)
    if issuer is None:
        combat_actions._clear_screen_for_order()
    owed = (may_draft
            and _draft_bookkeeping(pid, name, probe.get("pawn"), snapshot, path))
    reply = _order_reply(call(dict(base)), "the %s" % action)
    auto_drafted = _confirm_draft_bookkeeping(pid, name, owed,
                                              reply.get("pawn"), snapshot, path)
    if reply.get("success") is not True:
        raise _order_refusal(reply, action)
    job = reply.get("job") or {}
    if job.get("verified") is False:
        raise _order_refusal(dict(reply, errorKind="job_unverified",
                                  error="the game reported the job but did not "
                                        "verify it (%s)" % job.get("def")),
                             action)
    return reply, pid, name, auto_drafted


def _care_order(action, kind, pawn, target, snapshot, identity, path, issuer,
                may_draft=True):
    """`tend` and `rescue`: the two combat-time acts that are not violence."""
    reply, pid, name, auto = _issue_targeted_order(
        action, pawn, target, snapshot, identity, path, issuer,
        may_draft=may_draft)
    reply_pawn = reply.get("pawn") or {}
    target_row = reply.get("target") or {}
    job = reply.get("job") or {}
    order = {"kind": kind, "pawnId": pid,
             "pawnName": reply_pawn.get("name") or name,
             "targetId": target_row.get("thingId") or str(target),
             "targetName": target_row.get("name") or target_row.get("defName"),
             "target": target_row, "job": job.get("def"),
             "draftedTend": bool(reply.get("draftedTend")),
             "accepted": True, "autoDrafted": auto,
             "pawn": reply_pawn, "watch": reply.get("watch")}
    return _record_action(pid, order, snapshot, identity, path)


def issue_tend(pawn, target, snapshot, identity, path=LEDGER, issuer=None):
    """Tend a patient, on the ground if that is where they are.

    Ground tending is REAL -- decompiled 1.6 has
    `FloatMenuOptionProvider_DraftedTend` -- and the fork that told M it
    was impossible in RimWorld was wrong. The catch is in the class name: the
    option exists only while the doctor is DRAFTED, so this order drafts them,
    and that draft is a cleanup obligation like any other. Which is exactly why
    it belongs here and not in `order.py`: mid-fight, `order.py tend` passes
    `draft: false` so it cannot create an obligation the ledger never heard of,
    and a doctor kneeling over a downed colonist is the case that matters most.
    """
    return _care_order("tend", "tend", pawn, target, snapshot, identity, path,
                       issuer)


def issue_rescue(pawn, target, snapshot, identity, path=LEDGER, issuer=None):
    """Carry a downed pawn to a bed. Recorded even though it does not draft.

    RESCUE IS RECORDED ON PURPOSE. It needs no draft change, so it creates no
    obligation -- but Octave bled out being carried toward a bed 110 cells
    away, and a pawn crossing a battlefield with a body in their arms is a
    combatant for every watch this ledger arms: their injuries stop the pulse,
    and `advance` will not run time past them once something goes wrong.
    """
    return _care_order("rescue", "rescue", pawn, target, snapshot, identity,
                       path, issuer, may_draft=False)


def issue_attack(pawn, target, snapshot, identity, path=LEDGER, issuer=None,
                 mode="auto"):
    """Resolve a target through ``home/order``, then order the attack.

    Hostility is NOT required and downed targets are allowed: RimWorld lets a
    drafted pawn attack any spawned pawn or thing, and the refusals that did
    not come from RimWorld are what cost the colony Finn and Octave. The dry
    resolve runs first so an ambiguity, an unreachable target or an
    incapable-of-violence pawn is reported with the game's own ``errorKind``
    and its candidate list, before anything is drafted or any time passes.
    """
    # The dry read never touches the screen: no camera, no selection, nothing
    # for the stream to see, because nothing happened yet.
    reply, pid, name, auto_drafted = _issue_targeted_order(
        "attack", pawn, target, snapshot, identity, path, issuer,
        extra={"mode": mode})
    reply_pawn = reply.get("pawn") or {}
    job = reply.get("job") or {}
    target_row = reply.get("target") or {}
    order = {"kind": "attack", "pawnId": pid,
             "pawnName": reply_pawn.get("name") or name,
             "attackerId": pid,
             "targetId": target_row.get("thingId") or str(target),
             "targetName": target_row.get("name") or target_row.get("defName"),
             "target": target_row, "mode": mode,
             "job": job.get("def"), "verb": job.get("verb"),
             "finishing": bool(job.get("killIncappedTarget")),
             "accepted": True, "autoDrafted": auto_drafted,
             "pawn": reply_pawn, "watch": reply.get("watch")}
    return _record_action(pid, order, snapshot, identity, path)


def preview_targeted_order(action, pawn, target, snapshot, identity,
                           path=LEDGER, issuer=None, mode=None):
    """Ask home/order for the exact combat verdict without changing anything."""
    _, pid, _ = _checked_session(pawn, snapshot, identity, path,
                                 "%s dry run" % action)
    params = {"action": action, "pawn": pid, "target": str(target),
              "dryRun": True, "watch": False,
              "draft": action != "rescue"}
    if mode is not None:
        params["mode"] = mode
    reply = _order_reply(_order_call(issuer)(params), "the %s dry run" % action)
    print_order_facts(reply)
    if reply.get("success") is not True:
        raise _order_refusal(reply, "%s (dry run)" % action)
    return reply


def issue_draft(pawn, snapshot, identity, path=LEDGER, issuer=None):
    """Draft one pawn through ``home/order``, with the same write-ahead ledger.

    Separate from the orders that draft as a side effect because sometimes the
    right move is simply "get everyone drafted NOW" -- and on the night this
    was written, nobody was.
    """
    _, pid, name = _checked_session(pawn, snapshot, identity, path, "draft")
    call = _order_call(issuer)
    probe = _order_reply(call({"action": "resolve", "pawn": pid,
                               "dryRun": True, "watch": False}),
                         "the draft dry run")
    if probe.get("success") is not True:
        raise _order_refusal(probe, "draft (dry run)")
    probe_pawn = probe.get("pawn") or {}
    if probe_pawn.get("drafted"):
        return {"kind": "draft", "pawnId": pid,
                "pawnName": probe_pawn.get("name") or name,
                "accepted": True, "alreadyDrafted": True,
                "autoDrafted": False, "pawn": probe_pawn}
    owed = _draft_bookkeeping(pid, name, probe_pawn, snapshot, path)
    reply = _order_reply(call({"action": "draft", "pawn": pid}), "the draft")
    reply_pawn = reply.get("pawn") or {}
    _confirm_draft_bookkeeping(pid, name, owed, reply_pawn, snapshot, path)
    if reply.get("success") is not True:
        raise _order_refusal(reply, "draft")
    order = {"kind": "draft", "pawnId": pid,
             "pawnName": reply_pawn.get("name") or name, "accepted": True,
             "alreadyDrafted": False,
             "autoDrafted": bool(reply_pawn.get("autoDrafted")),
             "pawn": reply_pawn, "watch": reply.get("watch")}
    return _record_action(pid, order, snapshot, identity, path)


def issue_undraft(pawn, snapshot, identity, path=LEDGER, issuer=None,
                  dry_run=False, force=False, set_draft=None):
    """Undraft. For a pawn this session drafted, that IS the release path.

    Undrafting somebody the ledger owes a restoration for and NOT clearing the
    obligation would leave `end` trying to restore a pawn nobody is holding, so
    an obligated pawn goes through `reconcile`, which restores the draft state
    they had at `begin` (not necessarily undrafted!) and drops the obligation.
    Anyone else is undrafted straight through ``home/order``.
    """
    ledger, pid, name = _checked_session(pawn, snapshot, identity, path,
                                         "undraft")
    if pid in (ledger.get("draftObligations") or {}):
        return {"released": True, "pawnId": pid, "pawnName": name,
                "lines": reconcile(snapshot, identity, pawn=pid,
                                   dry_run=dry_run, force=force, path=path,
                                   set_draft=set_draft)}
    if dry_run:
        return {"released": False, "pawnId": pid, "pawnName": name,
                "lines": ["DRY RUN", "%s has no cleanup obligation from this "
                          "session; undraft would go straight to the game"
                          % name]}
    call = _order_call(issuer)
    reply = _order_reply(call({"action": "undraft", "pawn": pid}),
                         "the undraft")
    if reply.get("success") is not True:
        raise _order_refusal(reply, "undraft")
    reply_pawn = reply.get("pawn") or {}
    order = {"kind": "undraft", "pawnId": pid,
             "pawnName": reply_pawn.get("name") or name, "accepted": True,
             "pawn": reply_pawn, "watch": reply.get("watch")}
    _record_action(pid, order, snapshot, identity, path)
    return {"released": False, "pawnId": pid, "pawnName": name, "order": order,
            "lines": ["%s undrafted (no cleanup obligation was outstanding)"
                      % (reply_pawn.get("name") or name)]}


def _ledger_pawn_id(ledger, reported_id):
    """Map companion numeric pawn IDs back to the ledger's stable ID form."""
    token = str(reported_id)
    orders = ledger.get("orders") or {}
    if token in orders:
        return token
    hits = []
    for pawn_id in orders:
        prefix = pawn_id.rstrip("0123456789")
        if prefix != pawn_id and pawn_id[len(prefix):] == token:
            hits.append(pawn_id)
    return hits[0] if len(hits) == 1 else token


def _reconcile_flee_orders(ledger, snapshot):
    """Retire flee intent once its Goto has arrived or ceased to exist.

    Arrival clears any pending decision.  A Goto that ended anywhere else is
    an escape that stopped short -- the pawn was hit, downed, or interrupted
    -- and that is precisely when a decision is required, so it is latched
    (never cleared) here.  2026-09-04: the old code popped the decision on
    both branches, which released the injury-hook latch on the next advance
    in exactly the case it existed for.
    """
    pawns = _pawns(snapshot)
    changed = []
    for pid, order in (ledger.get("orders") or {}).items():
        if order.get("kind") != "flee":
            continue
        pawn = pawns.get(pid)
        if not pawn:
            continue
        pos = pawn.get("position") or {}
        dest = order.get("destination") or {}
        arrived = (pos.get("x"), pos.get("z")) == (dest.get("x"), dest.get("z"))
        job = (pawn.get("job") or "").lower()
        if not (arrived or job != "goto"):
            continue
        order["kind"] = "flee_complete"
        order["completed"] = arrived
        order["completionReason"] = "arrived" if arrived else "goto ended"
        order["completedTick"] = _tick(snapshot)
        pending = ledger.setdefault("pendingDecisions", {})
        if arrived:
            pending.pop(pid, None)
        else:
            # setdefault keeps a more specific reason already latched by the
            # injury hook.
            pending.setdefault(pid, {
                "reason": "flee interrupted before arrival (job %s)"
                          % (pawn.get("job") or "none"),
                "atTick": _tick(snapshot), "position": pos,
                "downed": bool(pawn.get("downed"))})
        changed.append(pid)
    return changed


def _combat_watch_args(ledger, seconds, speed):
    """The only place combat-only companion probes are enabled."""
    orders = ledger.get("orders") or {}
    fleeing = [pid for pid, order in orders.items()
               if order.get("kind") == "flee"]
    # An undrafted pawn is out of the fight; watching their injuries would stop
    # every pulse for a stubbed toe in the kitchen.
    combatants = [pid for pid, order in orders.items()
                  if order.get("kind") != "undraft"]
    watched = fleeing if fleeing else combatants
    attacking = [pid for pid, order in orders.items()
                 if order.get("kind") in ATTACK_ORDER_KINDS]
    # ONE threshold for ONE pawn set (see the injuryPawnIds note below), so the
    # higher attacker threshold is used only when every watched pawn is one --
    # a mixed set keeps the lower one, because the pawn who was not ordered
    # into the fight is the one whose first wound is news.
    severity_delta = (COMBAT_ATTACK_INJURY_SEVERITY_DELTA
                      if watched and all(pid in attacking for pid in watched)
                      else COMBAT_INJURY_SEVERITY_DELTA)
    return {
        "speed": speed, "maxDurationMs": max(1, int(seconds * 1000)),
        "pollIntervalMs": COMBAT_POLL_MS, "pauseOnBudget": True,
        "requireRunningAtEntry": False,
        # Combat begins with known hostiles. ignoreCurrentHostiles baselines
        # them per call, so this pair is EDGE-triggered: a hostile that
        # arrives mid-pulse stops the clock, the ones already here do not.
        # (2026-09-04: watchHostiles was False, so a raider walking on
        # without a letter never stopped a pulse.) Predator hunts stay off:
        # that watch has no per-call baseline and re-fires every pulse.
        "watchHostiles": True, "ignoreCurrentHostiles": True, "huntWithin": 0,
        # Order changes only matter for a currently active escape.
        "watchedPawnIds": "",
        "meleeThreatPawnIds": ",".join(fleeing),
        # The hook stops on ANY injury to the pawns it is armed for, which is
        # right for an escape and wrong for a pawn ordered into melee. While
        # anyone flees the hook is armed for the fleeing pawns only; otherwise
        # the thresholded poll covers every combatant. (The companion has one
        # injury pawn set for both, so the two cannot run side by side yet.)
        "injuryPawnIds": ",".join(watched),
        # Fleeing stops on the first injury actually applied, synchronously in
        # RimWorld's health path. The former melee-intent poll was both late and
        # redundant once injury is the chosen tactical boundary.
        "watchMeleeThreats": False,
        "watchPawnOrders": False,
        "watchInjuries": bool(combatants) and not bool(fleeing),
        "watchInjuryHook": bool(fleeing),
        "injuryStopOnNew": False,
        "injuryMinSeverityDelta": severity_delta,
        "injuryMinBleedRateDelta": COMBAT_INJURY_BLEED_DELTA,
        # Alerts are LEVEL-triggered: an alert that is already up stays up, so
        # without this a standing break-risk alert stopped every pulse at ~0
        # game time. The companion keeps the memo across calls.
        # 2026-09-05: alerts never stop a pulse; a new one is reported after it.
        "watchAlerts": False,
        "alertDebounceMs": COMBAT_ALERT_DEBOUNCE_MS,
        "combatCamera": True,
        "combatCameraPawnIds": ",".join(combat_camera.participant_ids(ledger)),
        "combatCameraIntervalMs": 8000,
        "combatCameraHostileRadius": 20.0,
        "combatCameraCompactRootSize": 13.3315439,
        "combatCameraWideRootSize": 25.5292435,
        "combatCameraCompactSpan": 15.0,
        "combatCameraMargin": 5,
        "combatCameraLastFrameUnixMs": int((ledger.get("ui") or {}).get(
            "cameraLastFrameUnixMs") or 0),
        "combatCameraCooldownMs": 3500,
        "combatCameraManualSuppressUntilUnixMs": int((ledger.get("ui") or {}).get(
            "cameraManualSuppressUntilUnixMs") or 0),
        "combatCameraManualSuppressMs": 20000,
    }


def _pause_now():
    """Pause and report whether RimWorld CONFIRMED it. Never raises."""
    try:
        result = rim.game("rimworld/pause_game", {"pause": True}, strict=False)
    except Exception:
        return False
    return isinstance(result, dict) and result.get("paused") is True


def _pause_verdict(paused):
    return ("GAME PAUSED" if paused
            else "!! COULD NOT VERIFY PAUSE -- press Space in RimWorld now")


def advance(seconds, snapshot, identity, path=LEDGER, waiter=None, pause=None,
            speed="Normal", snapshot_reader=None, resume=False):
    """Run one companion-owned combat pulse; never use client polling."""
    if waiter is None:
        refuse_if_supervised()
    ledger = load_ledger(path)
    if not ledger or not ledger.get("active"):
        raise CombatRefusal("no active combat session; run `python combat.py begin`")
    tick = _tick(snapshot)
    stale = stale_reason(ledger, tick, identity)
    if stale:
        raise CombatRefusal("STALE ledger: %s; refusing to advance time" % stale)
    if _reconcile_flee_orders(ledger, snapshot):
        _atomic_write(ledger, path)
    pending = ledger.get("pendingDecisions") or {}
    if pending:
        names = ["%s (%s)" % (ledger.get("pawns", {}).get(pid, {}).get("name") or pid,
                              (row or {}).get("reason") or "?")
                 for pid, row in pending.items()]
        raise CombatRefusal("tactical decision required for %s; issue a new move/flee/order before advancing"
                            % ", ".join(names))
    # Human control always wins, across pulses too: a pause or speed change
    # by a person during the last pulse is not lifted until the operator
    # says so explicitly.
    last = ledger.get("lastStop") or {}
    if last.get("reason") in HUMAN_STOPS and not last.get("acknowledged"):
        if not resume:
            raise CombatRefusal("a person stopped the last pulse (%s: %s); "
                                "advance refused until you pass --resume"
                                % (last.get("reason"), last.get("detail")))
        last["acknowledged"] = True
        ledger["lastStop"] = last
        _atomic_write(ledger, path)
    seconds = float(seconds)
    if seconds <= 0:
        raise CombatRefusal("advance duration must be positive")
    if seconds > MAX_ADVANCE_SECONDS:
        raise CombatRefusal("advance of %.0f s refused; combat pulses are %.0f s or less "
                            "(the companion blocks every other bridge call while it runs)"
                            % (seconds, MAX_ADVANCE_SECONDS))
    args = _combat_watch_args(ledger, seconds, speed)
    call = waiter or (lambda payload: rim.game(COMBAT_EVENT_TOOL, payload,
                                                strict=False))
    result = call(args)
    stop = result.get("stopReason") if isinstance(result, dict) else None
    # The companion says more than stopReason: success is False when the
    # watch fired but the pause DID NOT TAKE, or when the call was cancelled
    # or the session changed under it. Every one of those means the clock may
    # still be running, so pause, verify, and refuse -- never record a pulse.
    failed = (not stop or stop in FAILED_STOPS
              or (isinstance(result, dict) and result.get("success") is False))
    if failed:
        stopper = pause or _pause_now
        paused = None
        try:
            paused = stopper()
        finally:
            detail = ((result.get("error") or result.get("stopDetail"))
                      if isinstance(result, dict) else result)
            raise CombatRefusal("combat pulse did not complete cleanly (%s: %s); %s"
                                % (stop or "invalid response",
                                   detail or "no detail", _pause_verdict(paused)))
    next_tick = result.get("ticksGame", result.get("endTick"))
    if next_tick is not None and tick is not None and next_tick < tick:
        raise CombatRefusal("game tick regressed during advance (%s -> %s)"
                            % (tick, next_tick))
    # One call, one entry baseline: the companion edge-triggers each hostile /
    # victim pair. Persist the returned baseline as an audit watermark, never
    # loop on a level-triggered result from Python.
    ledger["eventWatermark"] = result.get("baseline") or {
        "ticksGame": next_tick, "stopReason": stop}
    if next_tick is not None:
        ledger["watermarkTick"] = max(ledger.get("watermarkTick") or next_tick,
                                      next_tick)
    ledger["lastStop"] = {"reason": stop, "detail": result.get("stopDetail"),
                          "at": time.time(), "tick": next_tick}
    camera_result = result.get("combatCamera") or {}
    if camera_result.get("manualSuppressUntilUnixMs") is not None:
        ledger.setdefault("ui", {})["cameraManualSuppressUntilUnixMs"] = int(
            camera_result["manualSuppressUntilUnixMs"] or 0)
    # Remove the obsolete permanent-disable bit written by the first version.
    ledger.setdefault("ui", {}).pop("cameraManualOverride", None)
    if camera_result.get("lastFrameUnixMs"):
        ledger.setdefault("ui", {})["cameraLastFrameUnixMs"] = int(
            camera_result["lastFrameUnixMs"])
    if stop == "pawn_injury_hook":
        event = result.get("injuryHookEvent") or result.get("event") or {}
        pid = _ledger_pawn_id(ledger, event.get("pawnId"))
        if pid in (ledger.get("orders") or {}):
            ledger.setdefault("pendingDecisions", {})[pid] = {
                "reason": "injured while fleeing", "atTick": next_tick,
                "instigatorId": event.get("instigatorId"),
                "damageDef": event.get("damageDef")}
    elif stop == "melee_threat":
        for row in result.get("meleeThreats") or []:
            pid = _ledger_pawn_id(ledger, row.get("targetPawnId"))
            if pid in (ledger.get("orders") or {}):
                ledger.setdefault("pendingDecisions", {})[pid] = {
                    "reason": "hostile melee intent", "atTick": next_tick,
                    "attackerId": row.get("attackerId")}
    elif stop == "pawn_order_changed":
        # The event reports job completion but not the pawn's final position.
        # Read once while paused so ordinary arrival is not misclassified as
        # an interrupted escape.
        reader = snapshot_reader or (lambda: colony_status.read(detail=True))
        try:
            stopped_snapshot = reader()
        except Exception:
            stopped_snapshot = None
        stopped_pawns = _pawns(stopped_snapshot or {})
        for row in result.get("pawnOrderChanges") or []:
            pid = _ledger_pawn_id(ledger, row.get("pawnId"))
            order = (ledger.get("orders") or {}).get(pid)
            if order and order.get("kind") == "flee":
                pawn = stopped_pawns.get(pid) or {}
                pos = pawn.get("position") or {}
                dest = order.get("destination") or {}
                arrived = ((pos.get("x"), pos.get("z")) ==
                           (dest.get("x"), dest.get("z")))
                order["kind"] = "flee_complete"
                order["completed"] = arrived
                order["completionReason"] = ("arrived" if arrived else
                                               "order changed during escape")
                order["completedTick"] = next_tick
                if arrived:
                    ledger.setdefault("pendingDecisions", {}).pop(pid, None)
                else:
                    ledger.setdefault("pendingDecisions", {})[pid] = {
                        "reason": "flee order ended", "atTick": next_tick,
                        "job": ((row.get("after") or {}).get("job") or
                                row.get("job") or row.get("newJob"))}
    _atomic_write(ledger, path)
    return result


def session_summary(ledger, snapshot=None, identity=None):
    if not ledger:
        return "COMBAT  no active session"
    obs = ledger.get("draftObligations") or {}
    names = [v.get("name") or k for k, v in obs.items()]
    stale = stale_reason(ledger, _tick(snapshot), identity) if snapshot and identity else None
    state = "STALE -- %s" % stale if stale else "active"
    cleanup = ", ".join(names) if names else "none"
    decisions = [ledger.get("pawns", {}).get(pid, {}).get("name") or pid
                 for pid in (ledger.get("pendingDecisions") or {})]
    decision = ("; DECISION REQUIRED: %s" % ", ".join(decisions)
                if decisions else "")
    owner = ledger.get("turnOwner") or {}
    owned = "; owner turn %s" % owner.get("turn") if owner else "; owner unknown"
    return "COMBAT  %s%s; cleanup pending: %s%s" % (state, owned, cleanup, decision)


def status_warning(snapshot, ledger_path=LEDGER, session_path=SESSION):
    """Warning line for status.py; disk-only except for its supplied snapshot."""
    ledger = load_ledger(ledger_path)
    if not ledger or not ledger.get("active"):
        return None
    try:
        ident = _identity(session_path)
        stale = stale_reason(ledger, _tick(snapshot), ident)
    except CombatRefusal as e:
        stale = str(e)
    obs = ledger.get("draftObligations") or {}
    names = [v.get("name") or k for k, v in obs.items()]
    if stale:
        return "!! COMBAT SESSION STALE -- %s; cleanup NOT attempted" % stale
    inherited = inherited_warning(ledger)
    if inherited:
        return inherited
    if ledger.get("cleanupPending") or obs:
        return "!! UNFINISHED COMBAT -- run `python combat.py end`; cleanup: %s" % (", ".join(names) or "pending")
    # 2026-09-04: this said "COMBAT session active -- no auto-drafted pawns",
    # which read as a live fight one line above `end` reporting nothing to
    # restore. Both lines were true and came from different programs (status.py
    # prints this one; combat.py prints the other), and together they were
    # nonsense. Say what the ledger IS: an open bookkeeping file with no debt.
    return ("COMBAT ledger open, nothing to restore -- no pawn was drafted by "
            "this session. `python combat.py end` closes it.")


def _resolve_name(token, ledger):
    if token in (ledger.get("draftObligations") or {}):
        return token
    hits = [pid for pid, row in (ledger.get("draftObligations") or {}).items()
            if (row.get("name") or "").lower() == token.lower()]
    if len(hits) != 1:
        raise CombatRefusal("pawn %r is not one unambiguous cleanup obligation" % token)
    return hits[0]


# ---------------------------------------------------------- the end of a fight
#
# 2026-09-07: `combat.py end` refused while ONE downed raider lay on the map and
# kept five colonists drafted until somebody typed `--force`. A downed pawn does
# not shoot. It is also not finished -- it crawls, it gets carried off by its
# friends, an animal stands back up -- so `end` proceeds and NAMES it rather
# than pretending the fight is over. A CONSCIOUS hostile still refuses, and the
# refusal names --force.
#
# The counting is status.threat_summary's, not `threats.hostileCount` alone: a
# downed manhunter loses its mental state and leaves `hostiles[]` by the
# companion's design, and folding `downedNear[]` back in is exactly why that
# helper exists. "Every remaining hostile is downed" is only claimed when every
# counted hostile is ACCOUNTED FOR as downed by name -- a capped list that
# cannot name them all refuses, as before.


def _remaining_hostiles(snapshot):
    """(count, names of the downed ones, names of the conscious ones)."""
    try:
        summary = colony_status.threat_summary(snapshot)
    except Exception:
        threats = snapshot.get("threats") or {}
        count = threats.get("hostileCount") or len(threats.get("hostiles") or [])
        return count, [], []
    rows = summary.get("rows") or []
    downed = [_threat_name(r) for r in rows if r.get("downed")]
    awake = [_threat_name(r) for r in rows if not r.get("downed")]
    total = summary.get("total") or 0
    # Anything counted but not named is unaccounted for, and unaccounted-for is
    # treated as conscious: the false negative here is a colonist standing up in
    # front of a raider who can still shoot.
    unnamed = total - len(downed) - len(awake)
    if unnamed > 0:
        awake += ["%d unnamed hostile(s)" % unnamed]
    return total, downed, awake


def _threat_name(row):
    return (row.get("name") or row.get("label") or row.get("defName")
            or row.get("thingId") or "a hostile")


def _downed_reminder(downed_names, plans, snapshot):
    """The lines `end` prints when it proceeds over downed-only hostiles."""
    still = [name for _, name, _, wanted in plans if wanted]
    for pid, row in _pawns(snapshot).items():
        if row.get("drafted") and not any(pid == p for p, _, _, _ in plans):
            still.append(row.get("name") or pid)
    return ["REMINDER: every remaining hostile is DOWNED, not gone: %s."
            % ", ".join(downed_names),
            "  Finish them off or capture them -- a downed raider is rescued by "
            "its friends and a downed animal stands back up.",
            "  Still drafted after this cleanup: %s."
            % (", ".join(sorted(set(still))) or "nobody")]


def reconcile(snapshot, identity, pawn=None, dry_run=False, force=False,
              path=LEDGER, set_draft=None):
    ledger = load_ledger(path)
    if not ledger:
        return ["no active combat session"]
    stale = stale_reason(ledger, _tick(snapshot), identity)
    if stale:
        if force and not pawn and not dry_run:
            # The world this ledger describes is not the one loaded. Its
            # pawns cannot be touched safely, so --force means abandon it.
            os.unlink(path)
            return ["STALE ledger discarded without touching the game (%s); "
                    "check the status board for anyone still drafted" % stale]
        raise CombatRefusal("STALE ledger: %s; refusing to mutate this game "
                            "(`end --force` discards the ledger)" % stale)
    obligations = ledger.get("draftObligations") or {}
    ids = [_resolve_name(pawn, ledger)] if pawn else list(obligations)
    current = _pawns(snapshot)
    plans, unresolved, abandoned = [], [], []
    for pid in ids:
        base = (ledger.get("pawns") or {}).get(pid, {})
        row = current.get(pid)
        if row is None:
            name = obligations[pid].get("name") or pid
            if force:
                # Dead, captured, or off the map: nothing to restore. Without
                # --force this used to wedge end/release forever.
                abandoned.append("%s is no longer a colonist; obligation dropped" % name)
            else:
                unresolved.append("%s is not a current colonist (dead, captured or "
                                  "off-map?); --force drops the obligation" % name)
            continue
        wanted = bool(base.get("originalDrafted"))
        plans.append((pid, row.get("name") or pid, bool(row.get("drafted")), wanted))
    hostile_count, downed_names, awake_names = _remaining_hostiles(snapshot)
    all_downed = bool(hostile_count) and not awake_names and bool(downed_names)
    dangerous = [x for x in plans if x[2] and not x[3]]
    lines = ["%s: %s -> %s" % (name, "drafted" if have else "undrafted",
                                "drafted" if wanted else "undrafted")
             for _, name, have, wanted in plans]
    reminder = (_downed_reminder(downed_names, plans, snapshot)
                if all_downed and dangerous else [])
    if dry_run:
        if hostile_count and dangerous and not force and not all_downed:
            warning = ["WARNING: %d hostile(s) remain%s; real cleanup requires --force"
                       % (hostile_count,
                          (" -- " + ", ".join(awake_names)) if awake_names else "")]
        else:
            warning = []
        return ["DRY RUN"] + warning + lines + unresolved + abandoned + reminder
    if hostile_count and dangerous and not force and not all_downed:
        raise CombatRefusal(
            "%d hostile(s) remain and %s still conscious; refusing to undraft %s. "
            "`python combat.py end --force` overrides this."
            % (hostile_count,
               ("%s is" % awake_names[0]) if len(awake_names) == 1
               else ("%s are" % ", ".join(awake_names)) if awake_names
               else "at least one is",
               ", ".join(x[1] for x in dangerous)))
    setter = set_draft or (lambda pid, drafted: rim.game(
        "rimworld/set_draft", {"pawnId": pid, "drafted": drafted}))
    for pid, name, have, wanted in plans:
        if have != wanted:
            result = setter(pid, wanted)
            if result.get("drafted") is not wanted:
                unresolved.append("%s draft restoration was not confirmed" % name)
                continue
        obligations.pop(pid, None)
    if force:
        for pid in ids:
            if pid not in current:
                obligations.pop(pid, None)
    ledger["cleanupPending"] = bool(obligations)
    if unresolved:
        _atomic_write(ledger, path)
        raise CombatRefusal("cleanup incomplete: " + "; ".join(unresolved))
    if pawn or obligations:
        _atomic_write(ledger, path)
    else:
        os.unlink(path)
    # The reminder rides on the SUCCESS path: `end` proceeded over hostiles that
    # are down but not gone, so what is still on the map has to be said here or
    # it is not said at all.
    return ((lines + abandoned) or ["no controller-drafted pawns to restore"]) + reminder


def _live_snapshot(pause=False):
    if pause:
        result = rim.game("rimworld/pause_game", {"pause": True})
        if result.get("paused") is not True:
            raise CombatRefusal("RimWorld did not confirm pause; refusing cleanup")
    return colony_status.read()


def main(argv=None):
    # Raw, so the usage block above stays a usage block: argparse's default
    # formatter reflows the whole docstring into one paragraph, and the command
    # list is the half of it anybody reads mid-fight.
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("begin")
    sub.add_parser("status")
    sub.add_parser("adopt", help="take over an inherited ledger without losing obligations")
    moving = sub.add_parser("move")
    moving.add_argument("pawn")
    moving.add_argument("x", type=int)
    moving.add_argument("z", type=int)
    fleeing = sub.add_parser("flee")
    fleeing.add_argument("pawn")
    fleeing.add_argument("x", type=int)
    fleeing.add_argument("z", type=int)
    equipping = sub.add_parser("equip")
    equipping.add_argument("pawn")
    equipping.add_argument("x", type=int)
    equipping.add_argument("z", type=int)
    weapon = equipping.add_mutually_exclusive_group(required=True)
    weapon.add_argument("--weapon-id")
    weapon.add_argument("--weapon-label")
    equipping.add_argument("--wait-seconds", type=float,
                           default=DEFAULT_EQUIP_WAIT_SECONDS)
    attacking = sub.add_parser("attack")
    attacking.add_argument("pawn")
    attacking.add_argument("target",
                           help="any spawned pawn or thing: ThingID, "
                                "Human123, 123, or a label. Hostility is NOT "
                                "required and downed targets are allowed.")
    attacking.add_argument("--mode", choices=("auto", "melee", "ranged"),
                           default="auto",
                           help="override the game's own choice of verb")
    attacking.add_argument("--dry-run", action="store_true",
                           help="resolve and validate without drafting or ordering")
    for verb, who in (("tend", "patient"), ("rescue", "patient")):
        p = sub.add_parser(verb)
        p.add_argument("pawn")
        p.add_argument(who)
        p.add_argument("--dry-run", action="store_true",
                       help="resolve and validate without drafting or ordering")
    drafting = sub.add_parser("draft")
    drafting.add_argument("pawn")
    undrafting = sub.add_parser("undraft")
    undrafting.add_argument("pawn")
    undrafting.add_argument("--dry-run", action="store_true")
    undrafting.add_argument("--force", action="store_true")
    advancing = sub.add_parser("advance")
    advancing.add_argument("seconds", type=float, nargs="?",
                           default=DEFAULT_ADVANCE_SECONDS)
    advancing.add_argument("--resume", action="store_true",
                           help="run time again after a person paused or "
                                "changed speed during the previous pulse")
    for cmd in ("end", "release"):
        p = sub.add_parser(cmd)
        if cmd == "release":
            p.add_argument("pawn")
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    try:
        if args.command not in ("status", "adopt", "end", "release"):
            warning = inherited_warning()
            if warning:
                raise OwnershipRefusal(warning)
        rim.init()
        if args.command == "begin":
            snap = _live_snapshot(pause=True)
            ledger, made = begin(snap, _identity())
            print(("STARTED " if made else "EXISTING ") + session_summary(ledger, snap, _identity()))
        elif args.command == "status":
            snap = _live_snapshot()
            print(session_summary(load_ledger(), snap, _identity()))
            supervisor = supervised_status()
            if supervisor and supervisor.get("active"):
                print("SUPERVISED PLAY active; epoch %s, mode %s, speed %s"
                      % (supervisor.get("epoch"), supervisor.get("mode") or "?",
                         supervisor.get("requestedSpeed")))
            elif supervisor and supervisor.get("stopReason"):
                print("SUPERVISED PLAY stopped: %s: %s"
                      % (supervisor.get("stopReason"), supervisor.get("stopDetail")))
            warning = inherited_warning()
            if warning:
                print(warning)
        elif args.command == "adopt":
            ledger = adopt_turn()
            print("COMBAT LEDGER ADOPTED by turn %s; %d cleanup obligation(s) preserved."
                  % ((ledger.get("turnOwner") or {}).get("turn"),
                     len(ledger.get("draftObligations") or {})))
        elif args.command == "move":
            snap = _live_snapshot(pause=True)
            order = issue_move(args.pawn, args.x, args.z, snap, _identity())
            order_headline("MOVE", "%s -> %s,%s%s"
                           % (order.get("pawnName") or order.get("pawnId"),
                              args.x, args.z,
                              " (arrived)" if order.get("arrived") else ""),
                           snap)
        elif args.command == "flee":
            snap = _live_snapshot(pause=True)
            order = issue_flee(args.pawn, args.x, args.z, snap, _identity())
            order_headline("FLEE",
                           "%s -> %s,%s; watching immediate injury hook"
                           % (order.get("pawnName") or order.get("pawnId"),
                              args.x, args.z), snap)
        elif args.command == "equip":
            snap = _live_snapshot(pause=True)
            ident = _identity()
            order = issue_equip(args.pawn, args.x, args.z, snap, ident,
                                args.weapon_id, args.weapon_label)
            if not order.get("completed") and args.wait_seconds > 0:
                order = finish_equip(order, args.wait_seconds, snap, ident)
            # Three states, not two. "Still walking to the weapon" is the order
            # WORKING; it is not a failure and it latches nothing.
            if order.get("completed"):
                state = "EQUIPPED"
            elif order.get("stillWalking"):
                state = "EQUIP ACCEPTED -- still walking (job %s); the next " \
                        "`advance` continues it" % (order.get("job") or "Equip")
            else:
                state = "EQUIP ACCEPTED -- completion pending"
            print("%s  %s: %s" %
                  (state, order.get("pawnName") or order.get("pawnId"),
                   order.get("weaponLabel") or order.get("weaponThingId")))
            # EQUIPPED is a completion the pulse actually observed, so it needs
            # no clock caveat. Anything else is a queued job like any other.
            if not order.get("completed"):
                for row in clock.notes(clock_state(snap), extra=CLOCK_EXTRA):
                    print(row)
        elif args.command == "attack":
            snap = _live_snapshot(pause=True)
            if args.dry_run:
                reply = preview_targeted_order("attack", args.pawn,
                                                args.target, snap, _identity(),
                                                mode=args.mode)
                print("ATTACK WOULD ISSUE  %s -> %s"
                      % ((reply.get("pawn") or {}).get("name") or args.pawn,
                         (reply.get("target") or {}).get("name") or args.target))
                return 0
            order = issue_attack(args.pawn, args.target, snap, _identity(),
                                 mode=args.mode)
            order_headline("ATTACK", "%s -> %s (%s)%s"
                           % (order.get("pawnName") or order.get("attackerId"),
                              order.get("targetId"),
                              order.get("targetName") or "?",
                              " (finish to death)"
                              if order.get("finishing") else ""), snap)
            print("   target:  %s" % describe_target(order.get("target")))
            print("   attacker: %s%s"
                  % (describe_pawn(order.get("pawn")),
                     "; AUTODRAFTED -- undraft when the danger has passed"
                     if order.get("autoDrafted") else ""))
            print("   job %s%s" % (order.get("job") or "?",
                                   " via %s" % order["verb"]
                                   if order.get("verb") else ""))
            watch_line({"watch": order.get("watch")}, order.get("targetName"))
        elif args.command in ("tend", "rescue"):
            snap = _live_snapshot(pause=True)
            if args.dry_run:
                reply = preview_targeted_order(args.command, args.pawn,
                                                args.patient, snap, _identity())
                print("%s WOULD ISSUE  %s -> %s"
                      % (args.command.upper(),
                         (reply.get("pawn") or {}).get("name") or args.pawn,
                         (reply.get("target") or {}).get("name") or args.patient))
                return 0
            issue = issue_tend if args.command == "tend" else issue_rescue
            order = issue(args.pawn, args.patient, snap, _identity())
            order_headline(args.command.upper(), "%s -> %s (%s)"
                           % (order.get("pawnName") or order.get("pawnId"),
                              order.get("targetName") or order.get("targetId"),
                              order.get("job") or args.command), snap)
            print("   patient: %s" % describe_target(order.get("target")))
            print("   %s: %s%s"
                  % (args.command == "tend" and "doctor" or "carrier",
                     describe_pawn(order.get("pawn")),
                     "; AUTODRAFTED to tend on the ground -- undraft them "
                     "when the danger has passed" if order.get("autoDrafted")
                     else ""))
            watch_line({"watch": order.get("watch")}, order.get("targetName"))
        elif args.command == "draft":
            snap = _live_snapshot(pause=True)
            order = issue_draft(args.pawn, snap, _identity())
            print("DRAFTED  %s%s"
                  % (order.get("pawnName") or order.get("pawnId"),
                     " (was already drafted; no obligation recorded)"
                     if order.get("alreadyDrafted") else
                     " -- UNDRAFT THEM WHEN THE DANGER HAS PASSED "
                     "(`python combat.py undraft %s`)"
                     % (order.get("pawnName") or order.get("pawnId"))))
            print("   %s" % describe_pawn(order.get("pawn")))
            if order.get("watch"):
                watch_line({"watch": order["watch"]},
                           order.get("pawnName"))
        elif args.command == "undraft":
            snap = _live_snapshot(pause=not args.dry_run)
            result = issue_undraft(args.pawn, snap, _identity(),
                                   dry_run=args.dry_run, force=args.force)
            print("\n".join(result.get("lines") or []))
            if result.get("released"):
                print("RELEASED  %s: the draft state they had at `begin` is "
                      "restored and the session no longer owes them cleanup."
                      % result.get("pawnName"))
            if (result.get("order") or {}).get("watch"):
                watch_line({"watch": result["order"]["watch"]},
                           result.get("pawnName"))
        elif args.command == "advance":
            snap = _live_snapshot()
            result = advance(args.seconds, snap, _identity(), resume=args.resume)
            print("COMBAT STOP  %s%s" %
                  (result.get("stopReason"),
                   ": " + str(result.get("stopDetail"))
                   if result.get("stopDetail") else ""))
            if result.get("stopReason") in HUMAN_STOPS:
                print("   a person stopped the clock; the next advance needs --resume")
            for note in result.get("notes") or []:
                print("   note: %s" % note)
            # An alert that did NOT stop the pulse is worth one line: silence
            # here is how a standing alert looks identical to no alert at all.
            # The companion emits one row per key: label, priority,
            # msSinceLastCounted, pollsDebounced (a bare string is tolerated).
            for row in result.get("alertsDebounced") or []:
                if isinstance(row, dict):
                    label = row.get("label") or row.get("alertKey")
                    ago = row.get("msSinceLastCounted")
                    since = (", last counted %.0f s ago" % (ago / 1000.0)
                             if isinstance(ago, (int, float)) else "")
                else:
                    label, since = row, ""
                print("   note: alert %r was already standing and did not stop "
                      "this pulse (%.0f s debounce%s)"
                      % (label, COMBAT_ALERT_DEBOUNCE_MS / 1000.0, since))
        else:
            if args.command == "end" and not args.dry_run:
                if pause_supervised_for_cleanup():
                    print("SUPERVISED PLAY PAUSED before combat cleanup; it will not auto-resume.")
            snap = _live_snapshot(pause=not args.dry_run)
            lines = reconcile(snap, _identity(), getattr(args, "pawn", None),
                              args.dry_run, args.force)
            print("\n".join(lines))
            # 2026-09-04: `end` used to stop at reconcile's lines, which say
            # what was restored and nothing about what happened to the session
            # -- so "no controller-drafted pawns to restore" arrived with no
            # statement that the ledger was gone, one line under status.py
            # calling the session active. One closing sentence, always.
            if args.command == "end" and not args.dry_run:
                closed = not os.path.exists(LEDGER)
                print("COMBAT SESSION %s" %
                      ("CLOSED -- ledger removed; nothing is drafted on this "
                       "session's account." if closed else
                       "STILL OPEN -- obligations remain (see above); run "
                       "`python combat.py end --force` to abandon them."))
        return 0
    except (CombatRefusal, rim.BridgeError, OSError, RuntimeError,
            ValueError, KeyError, KeyboardInterrupt) as e:
        command = getattr(args, "command", None)
        if not isinstance(e, (OwnershipRefusal, SupervisedPlayRefusal)) and command in ("move", "flee", "attack", "equip", "advance", "draft",
                       "undraft", "tend", "rescue"):
            # Anything that may have touched a pawn or the clock ends paused,
            # and says whether the pause was CONFIRMED rather than assuming.
            paused = _pause_now()
            try:
                if command in ("move", "flee"):
                    record_move_failure(args.pawn, args.x, args.z, e)
                elif command == "attack":
                    record_order_failure(args.pawn, "attack failed", e,
                                         target=args.target)
                elif command in ("draft", "tend", "rescue"):
                    record_order_failure(args.pawn, "%s failed" % command, e,
                                         target=getattr(args, "patient", None))
            finally:
                print("%s FAILED -- %s; a new verified order is required"
                      % (command.upper(), _pause_verdict(paused)))
        print("COMBAT REFUSED -- %s" % (e if not isinstance(e, KeyboardInterrupt)
                                        else "interrupted"))
        warning = inherited_warning()
        if warning and warning not in str(e):
            print(warning)
        # Never let a refusal stand as "that pawn cannot be used". Ask the game
        # what it thinks of them and print it: incapableOfViolence,
        # canBeDrafted, weapon, mental state. One extra read, no game time, and
        # it is the read whose absence killed Finn.
        if not isinstance(e, (OwnershipRefusal, SupervisedPlayRefusal)) and command in ("move", "flee", "attack", "draft", "undraft", "tend",
                       "rescue"):
            try:
                print_pawn_verdict(getattr(args, "pawn", None))
            except Exception as probe_error:
                print("   (could not read the pawn's state: %s)" % probe_error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
