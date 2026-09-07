"""Order a colonist to a cell, auto-drafting when needed.

`right_click_cell` is the drafted-goto path -- but a modal dialog eats it
silently and reports success, while `get_ui_layout` cheerfully lists the modal's
own buttons among the map UI. So: check for an open modal BEFORE concluding the
map is unreachable. `blocking_window()` is that check, and it tests for any open
modal, not for which window is focused.

(Real SendInput mouse/keyboard does not reach RimWorld here at all, modal or
not. click.py works on the menu scenes only.)
"""
import time

import camlock, letters, rim, run


CAMERA_DWELL_SECONDS = 0.5


def blocking_window():
    """The dialog swallowing map input: a type string, None, or letters.UNKNOWN.

    **Three answers, and only None means clear.** UNKNOWN is the bridge not
    answering: nothing was read, so the screen is not known to be empty and an
    order sent on it can be eaten silently. `goto` refuses on both.

    Delegates to `letters.dialog_window()`, which reads the window LIST rather
    than `uiState.focusedWindowType`. That field lies: the letter stack is
    itself a Super-layer `Verse.ImmediateWindow` and takes it, so the focus test
    returns None with a modal plainly open -- `Dialog_NodeTreeWithFactionInfo`,
    `Dialog_Trade` -- and then `right_click_cell` reports success while the pawn
    never moves. One scan, in one file, so the two cannot drift apart.
    """
    return letters.dialog_window()


def _colonist(pawn_id):
    """The living colonist row for pawn_id. Raises KeyError when unavailable."""
    for colonist in rim.game("rimworld/list_colonists", {})["colonists"]:
        if colonist["pawnId"] == pawn_id and not colonist.get("dead"):
            return colonist
    raise KeyError("%s: not among the living colonists" % pawn_id)


def _ensure_drafted(pawn_id, pawn=None, before_draft=None):
    """Draft pawn_id if necessary and return whether this call drafted them.

    `right_click_cell` only creates a goto job for drafted pawns. Its success
    means the synthetic click was delivered, not that RimWorld accepted a move
    order, so checking this precondition is part of the command's contract.
    """
    pawn = pawn or _colonist(pawn_id)
    if pawn.get("drafted"):
        return False

    # Combat's cleanup ledger must be write-ahead: if the process dies after
    # set_draft but before its return, remembering a phantom obligation is safe
    # while forgetting a genuinely drafted pawn is not.  The ordinary goto
    # path has no ledger, so the hook is optional.
    if before_draft is not None:
        before_draft(pawn)
    result = rim.game("rimworld/set_draft",
                      {"pawnId": pawn_id, "drafted": True})
    if result.get("drafted") is not True:
        raise RuntimeError("RimWorld did not confirm that %s was drafted; move "
                           "order not sent" % (pawn.get("name") or pawn_id))

    # This is intentionally loud and remains true even if a later part of
    # goto raises: a pawn left drafted is a cleanup obligation, not a detail.
    print("AUTODRAFTED %s -- UNDRAFT THEM WHEN THE DANGER HAS PASSED."
          % (pawn.get("name") or pawn_id))
    return True


def _send_goto(x, z):
    """Issue goto, resolving the occupied-cell float menu when one opens."""
    result = rim.game("rimworld/right_click_cell",
                      {"x": x, "z": z, "button": "right"})
    if result.get("actionKind") != "menu_opened":
        return {"actionKind": result.get("actionKind") or "direct",
                "menuOptionIndex": None}

    # Right-clicking an occupied cell opens the pawn/thing action menu instead
    # of taking the empty-cell fast path. `Go here` is still RimWorld's own
    # drafted movement option, and is safer than pretending the click moved.
    goto_options = [option for option in (result.get("options") or [])
                    if (option.get("label") or "").strip().lower() == "go here"
                    and not option.get("disabled")]
    if len(goto_options) != 1:
        rim.game("rimworld/close_context_menu", {}, strict=False)
        raise RuntimeError("Right-click at (%s,%s) opened a context menu but "
                           "offered no unambiguous enabled 'Go here'; move "
                           "order not accepted" % (x, z))
    rim.game("rimworld/execute_context_menu_option",
             {"optionIndex": goto_options[0]["index"]})
    return {"actionKind": "menu_option",
            "menuOptionIndex": goto_options[0]["index"]}


def issue_goto(pawn_id, x, z, zoom=24, before_draft=None):
    """Issue and verify a drafted move without advancing game time.

    This is the reusable combat-facing half of :func:`goto`.  It returns an
    order record whose ``autoDrafted`` field tells a session ledger whether
    this operation changed draft state.  ``before_draft(pawn)`` is called
    immediately before that change so a caller can persist a write-ahead
    cleanup obligation.

    Acceptance is confirmed by an immediate ``Goto`` job, a first position
    change, or the pawn already occupying the requested destination.  Merely
    delivering a click is never reported as success.  No call in this function
    advances ticks or changes game speed.
    """
    blocked = blocking_window()
    if blocked is letters.UNKNOWN:
        raise RuntimeError("UNVERIFIED: the bridge did not answer, so whether a"
                           " modal is open is UNKNOWN -- not sending an order a"
                           " dialog could eat")
    if blocked:
        raise RuntimeError(f"{blocked} is open and will eat this order")

    cell = rim.game("rimworld/get_cell_info", {"x": x, "z": z})["cell"]
    if not cell["walkable"]:
        raise ValueError(f"({x},{z}) is not walkable")

    pawn = _colonist(pawn_id)
    if pawn.get("downed"):
        raise RuntimeError("%s is downed and cannot accept a move order"
                           % (pawn.get("name") or pawn_id))
    if pawn.get("mentalState"):
        raise RuntimeError("%s is in mental state %s and is not controllable"
                           % (pawn.get("name") or pawn_id,
                              pawn["mentalState"]))
    start = pawn["position"]
    auto_drafted = _ensure_drafted(pawn_id, pawn=pawn,
                                    before_draft=before_draft)

    # This already moved the camera, so it IS the "hands wants the camera"
    # signal camlock keys off.  Issuing an order remains watchable; status reads
    # do not acquire it.
    camlock.claim("hands", "drafted move to %d,%d" % (x, z))
    rim.game("rimworld/set_camera_zoom", {"rootSize": zoom}, strict=False)
    rim.game("rimworld/jump_camera_to_cell", {"x": x, "z": z}, strict=False)
    time.sleep(CAMERA_DWELL_SECONDS)
    rim.game("rimworld/clear_selection", {})
    rim.game("rimworld/select_pawn", {"pawnId": pawn_id}, strict=False)
    delivery = _send_goto(x, z)
    # The destination must be on-screen for the vanilla right-click, but the
    # useful view after delivery is the pawn and whatever is pursuing them.
    # Return there immediately at a readable combat zoom.
    rim.game("rimworld/set_camera_zoom", {"rootSize": zoom}, strict=False)
    rim.game("rimworld/jump_camera_to_cell",
             {"x": start["x"], "z": start["z"]}, strict=False)
    time.sleep(CAMERA_DWELL_SECONDS)

    accepted = _colonist(pawn_id)
    final = accepted["position"]
    job = accepted.get("job")
    at_destination = (final["x"], final["z"]) == (x, z)
    moved = (final["x"], final["z"]) != (start["x"], start["z"])
    if not (at_destination or moved or (job or "").lower() == "goto"):
        raise RuntimeError("UNVERIFIED: RimWorld did not show a Goto job or "
                           "position change for %s; move order not accepted"
                           % (accepted.get("name") or pawn_id))

    return {
        "pawnId": pawn_id,
        "pawnName": accepted.get("name") or pawn.get("name") or pawn_id,
        "destination": {"x": x, "z": z},
        "position": final,
        "job": job,
        "delivery": delivery,
        "autoDrafted": auto_drafted,
        "accepted": True,
        "arrived": at_destination,
    }


def goto(pawn_id, x, z, seconds=30, zoom=24, speed="Superfast"):
    """Send a pawn to (x, z), drafting first when needed.

    Returns (position reached, why it stopped). A pawn auto-drafted here stays
    drafted after arrival so follow-up combat orders remain possible; the call
    prints a cleanup reminder, and status.py repeats it while they are drafted.

    Time passes through `run.until`, which honours a person's pause. The old
    version looped `step_game_ticks` up to 20x400 = 3.2 in-game hours, and that
    call sets Paused and steps regardless (`pauseFirst` defaults true) -- so
    M pressing pause did nothing while this walked Lucas into manhunter
    muffalo. A `reason` other than "done" means the pawn is NOT where you asked.
    `run.until` yields after a five-second review slice; "review needed" leaves
    the game paused and the pawn drafted. Inspect, then call `goto` again to
    continue the same move deliberately.
    """
    issue_goto(pawn_id, x, z, zoom=zoom)
    def arrived():
        # A pawn that is no longer among the living never arrives -- `pos`
        # raises for the dead, and swallowing that here keeps the walk running
        # to its own timeout instead of blowing up inside `run.until`.
        try:
            p = pos(pawn_id)
        except KeyError:
            return False
        return (p["x"], p["z"]) == (x, z)

    reason, _ = run.until(arrived, seconds=seconds, speed=speed, verbose=False)
    try:
        return pos(pawn_id), reason
    except KeyError:
        return None, "the pawn is no longer among the living colonists"


def pos(pawn_id):
    """The living pawn's cell. Raises KeyError for the dead and the unknown.

    **The dead are skipped**, as `map.py` and `verify.py` skip them: a corpse
    keeps its `pawnId` and its position, so counting one would let a body lying
    on the destination satisfy `goto`'s arrival test.
    """
    return _colonist(pawn_id)["position"]
