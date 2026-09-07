"""Verified combat UI actions shared by the future combat controller.

This module deliberately does not advance RimWorld time.  Issuing an Equip job
and observing a weapon in a pawn's hands are separate operations; callers pass
their existing guarded/event-aware waiter to :func:`wait_for_equipment`.

Public integration surface::

    ticket = issue_equip(pawn_id, x, z, weapon_label="bolt-action rifle")
    done = wait_for_equipment(ticket, waiter=<combat's guarded pulse>, seconds=15)

``waiter`` has the same essential contract as ``run.until``: it accepts a
zero-argument predicate and ``seconds=`` and returns ``(reason, detail)``.
In combat it must be the guarded, event-aware pulse that ``combat.py``
builds around ``combat.advance`` -- never ``run.until`` (Superfast, client
polling, auto-dismissed letters) and never a plain sleep.

2026-09-04: ``issue_melee_attack`` was DELETED, not kept as a fallback. It
drove attacks through the float menu and refused any target whose snapshot row
did not say ``hostile: true`` -- and on the night two colonists died, that is
exactly what happened: the wolf stopped being hostile, the menu path refused,
and the fork read the refusal as "the tool will not let me act". ``home/order``
resolves a target by any id form, hostile or not, downed or not, and reports
*why* when it refuses. A second path that can still say no for the old reason
is not a fallback, it is the bug with a flag on it. Attacks live in
``combat.issue_attack`` now.
"""
import letters
import rim


def _clear_screen_for_order():
    window = letters.dialog_window()
    if window is letters.UNKNOWN:
        raise RuntimeError("UNVERIFIED: whether a modal is open is UNKNOWN")
    if window:
        raise RuntimeError("%s is open and will eat this order" % window)


def _id(row):
    return row.get("pawnId") or row.get("thingId")


def _pawn(pawn_id, equipment=False):
    # home/status is the common stable-ID source for both colonists and
    # hostiles. home/list_pawns intentionally identifies rows by display name,
    # so it cannot safely be the first lookup for a ThingID.
    status = rim.game("home/status", {})
    candidates = list(status.get("colonists") or [])
    # home/status emits no `hostile` boolean: membership in threats.hostiles is
    # the proof, so it is marked here. Nothing in this module gates on it any
    # more (the melee path that did is gone -- see the module docstring); it is
    # kept so a caller reading an equip ticket's row sees the same field the
    # threat board shows.
    candidates += [dict(row, hostile=True) for row in
                   (status.get("threats") or {}).get("hostiles") or []]
    matches = [p for p in candidates if str(_id(p)) == str(pawn_id)]
    if len(matches) != 1:
        raise KeyError("%s: expected exactly one pawn, found %d" %
                       (pawn_id, len(matches)))
    row = dict(matches[0])
    if equipment:
        reply = rim.game("home/list_pawns",
                         {"nameFilter": row.get("name"), "equipment": True})
        gear_rows = [p for p in reply.get("pawns") or []
                     if (p.get("name") or "").lower() ==
                        (row.get("name") or "").lower()]
        if len(gear_rows) != 1 or not isinstance(gear_rows[0].get("equipment"), dict):
            raise RuntimeError("equipment state for %s was not readable" % pawn_id)
        row.update(gear_rows[0])
    return row


def _primary_matches(row, thing_id=None, label=None):
    gear = row.get("equipment") or {}
    primary = gear.get("primary") or {}
    if thing_id and primary.get("thingId") == thing_id:
        return True
    actual = (primary.get("label") or gear.get("primaryLabel") or "").lower()
    return bool(label and label.lower() in actual)


def _one_enabled(options, predicate, description):
    matches = [o for o in options or []
               if not o.get("disabled") and predicate((o.get("label") or "").strip())]
    if len(matches) != 1:
        raise RuntimeError("expected one enabled %s option, found %d" %
                           (description, len(matches)))
    return matches[0]


def _select(pawn_id):
    rim.game("rimworld/clear_selection", {})
    result = rim.game("rimworld/select_pawn", {"pawnId": pawn_id}, strict=False)
    if isinstance(result, dict) and result.get("success") is False:
        raise RuntimeError("RimWorld refused to select %s" % pawn_id)


def issue_equip(pawn_id, x, z, weapon_thing_id=None, weapon_label=None):
    """Issue one vanilla Equip action and verify job acceptance.

    Coordinates identify the map weapon.  At least one stable ThingID or label
    must identify what completion should look like; ThingID is preferred.
    """
    if not (weapon_thing_id or weapon_label):
        raise ValueError("weapon_thing_id or weapon_label is required")
    _clear_screen_for_order()
    before = _pawn(pawn_id, equipment=True)
    if _primary_matches(before, weapon_thing_id, weapon_label):
        return {"kind": "equip", "pawnId": pawn_id, "accepted": True,
                "completed": True, "weaponThingId": weapon_thing_id,
                "weaponLabel": weapon_label}

    _select(pawn_id)
    click = rim.game("rimworld/right_click_cell",
                     {"x": x, "z": z, "button": "right"})
    if click.get("actionKind") != "menu_opened":
        raise RuntimeError("weapon right-click did not open an Equip menu")
    try:
        option = _one_enabled(
            click.get("options"),
            lambda s: s.lower().startswith("equip ") and
                      (not weapon_label or weapon_label.lower() in s.lower()),
            "Equip")
    except Exception:
        rim.game("rimworld/close_context_menu", {}, strict=False)
        raise
    rim.game("rimworld/execute_context_menu_option",
             {"optionIndex": option["index"]})

    after = _pawn(pawn_id, equipment=True)
    completed = _primary_matches(after, weapon_thing_id, weapon_label)
    if not completed and (after.get("job") or "").lower() != "equip":
        raise RuntimeError("UNVERIFIED: no Equip job or matching primary weapon")
    return {"kind": "equip", "pawnId": pawn_id, "accepted": True,
            "completed": completed, "weaponThingId": weapon_thing_id,
            "weaponLabel": weapon_label, "job": after.get("job"),
            "menuOptionIndex": option["index"]}


def wait_for_equipment(ticket, waiter, seconds=30):
    """Use a caller-owned guarded waiter, then read actual equipment state.

    Three outcomes, not two (2026-09-04). The old code had only "weapon in hand"
    and "raise", so a pawn who was still WALKING to the weapon when the pulse's
    budget ran out came back as a failure: `combat.py equip` printed EQUIP
    FAILED, latched a pending decision, and the identical retry then "worked"
    -- twice, on two pawns, during the Lampblack stream. Walking to a weapon is
    the job doing exactly what it was told; the order stands and the next
    `advance` continues it. So a pawn still on an Equip job returns
    ``completed: False, stillWalking: True`` and the caller keeps the order.

    A pawn who has DROPPED the Equip job without the weapon is the real
    failure, and still raises: nothing is coming, and a new order is required.
    """
    if ticket.get("completed"):
        return dict(ticket)

    latest = [None]
    def equipped():
        latest[0] = _pawn(ticket["pawnId"], equipment=True)
        return _primary_matches(latest[0], ticket.get("weaponThingId"),
                                ticket.get("weaponLabel"))

    reason, detail = waiter(equipped, seconds=seconds)
    result = dict(ticket)
    if equipped():
        result.update(completed=True, stillWalking=False, waitReason=reason,
                      waitDetail=detail, job=(latest[0] or {}).get("job"))
        return result
    job = ((latest[0] or {}).get("job") or "")
    if job.lower() != "equip":
        raise RuntimeError("equip did not complete before %s and %s is no longer "
                           "on an Equip job (job %r); the order is dead"
                           % (reason, ticket["pawnId"], job or "none"))
    result.update(completed=False, stillWalking=True, waitReason=reason,
                  waitDetail=detail, job=job)
    return result
