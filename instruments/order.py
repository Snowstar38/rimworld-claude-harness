"""One pawn, one target, one order. Select and right-click, as a command.

  python order.py attack <pawn> <target> [--mode melee|ranged]
  python order.py goto <pawn> <x> <z> [--undraft|--stay-drafted]   # alias: move
  python order.py rest <pawn> [<bed>|<x> <z>]    # lie down; undrafts first
  python order.py rescue <pawn> <target>
  python order.py tend <pawn> <target>           # drafts the doctor: ground tend
  python order.py equip <pawn> <target>
  python order.py haul <pawn> <thing>
  python order.py work <pawn> <bench>            # aka workat: go use that bench
  python order.py deploy <pawn> <x> <z> [--do]   # a worn pack's gizmo; DRY by default
  python order.py draft <pawn>
  python order.py undraft <pawn>
  python order.py resolve <pawn> [<target>]      # dry read, mutates nothing
  python order.py menu <pawn> <target> [--leave-open]
  python order.py do <pawn> <target> "<option label>"
  python order.py force <pawn> <target> ["<label substring>"] [--do]

  any verb: --dry-run (ask what WOULD happen), --do (issue it; see below),
            --no-watch (do not show it),
            --no-draft (never change the draft state to make the order
                        legal; on `goto` that IS `--undraft`)

## The --do rule, in one sentence

**Every subcommand accepts `--do`, and no subcommand is ever refused for
carrying it.** On 2026-09-07 a fork typed `--do` on `goto`, on `haul` and on
`tend`, was refused by argparse three separate times, and lost three turns to a
flag. So:

* `force` / `menu-do` and **`deploy`** are DRY RUNS by default and `--do` is
  what issues them. Those are the only places the flag changes anything.
  `deploy` is on that list because a pack holds one charge and destroys itself
  when it fires: an accidental bare call would spend the item for good.
* every other issuing verb (`attack`, `goto`, `rescue`, `tend`, `equip`,
  `haul`, `work`, `draft`, `undraft`, `do`) issues on a bare call, so `--do` is
  a harmless no-op there -- it says out loud what the command already meant.
  `--dry-run` is how you ask without issuing, and it wins if both are given.
* `menu` and `resolve` mutate nothing by construction; `--do` on those prints a
  line saying so rather than refusing.

**Pawns and targets take any id form**: `Thing_Wolf_Timber334862`,
`Wolf_Timber334862`, `334862`, or a label/name. A building also answers to
`DefName@x,z` -- `TableStonecutter@126,140` -- which the companion parses
server-side (the same form `bills.py` and `buildings.py` take; the parsing
lives in the C#, so there is nothing to keep in step here). A bare CELL is a
target too, written either way: `"140,152"` or `140 152`. An ambiguous label
prints the candidates with their ids rather than refusing blankly -- read them
and order again with an id. Hostility is NOT required: a drafted pawn may
attack any animal, exactly as in vanilla, and downed targets are legal targets.

## goto / move, and what stays drafted

`move` is an alias of `goto`; they are one verb with two names. **The default
drafts the pawn and LEAVES them drafted**, holding the cell, and says so on
every call. `--undraft` moves them the other way: no draft at all, an ordinary
`Goto` job that an undrafted pawn takes and then goes back to work from, and
nothing left drafted to clean up. A pawn who was already drafted is undrafted
first. Nothing here watches for arrival, so there is no "undraft when you get
there" -- `--undraft` means "never drafted", not "undrafted later".
`--stay-drafted` is the default written out loud; giving both is refused.

## Rest

`rest` is the one order with no float-menu option behind it. RimWorld
auto-takes a right-click on a bed, so the menu comes back empty and
`force <pawn> <bed cell> "Rest"` has nothing to match -- there is no "Rest"
label anywhere in the game. `rest` issues `JobDefOf.LayDown` on the bed, the
same job `JobGiver_GetRest` builds, and **undrafts the pawn first**, because a
drafted pawn stands in a bed rather than lying in it. With no target it uses
`RestUtility.FindBedFor`, the game's own search for that pawn's bed; with a
cell it uses the bed standing there.

## Deploying a worn pack (turret pack, hunter pack, tox/deadlife/broadshield)

`deploy <pawn> <x> <z>` fires the pack's own gizmo verb at a cell -- the same
`Job` `Verb.OrderForceTarget` builds -- instead of opening the targeter. It is
**dry by default**; `--do` issues it.

**A deploy is a THROWN GRENADE, not a build order**, and that is the whole of
the 2026-09-08 bug where four clicks were swallowed in silence. The cell has to
pass two gates, and they fail in two different ways, neither of which says a
word in the UI:

1. `ValidateTarget` -- the cell must hold **no Building at all** (a wall, a
   door, a power conduit under the floor, a chair, an unfinished frame) and be
   standable. Failing this **swallows the click and leaves the targeter open**.
2. `CanHitTarget` -- the cell must be in range (22.9 for a turret pack) **and in
   line of sight** of the pawn, because the capsule flies there. Failing this
   **CLOSES the targeter and places nothing**.

Inside a mined-out mountain room, a cell on the other side of the rock is
invisible however close it is. The only refusal in the whole path that posts a
game message is "no charges left", which is why `status.py` showed nothing.

A refusal here names which gate failed, in those words, and lists up to eight
nearby cells that DO pass both -- paste one back.

## Tending a patient lying on the ground

**Ground tending is real** (`FloatMenuOptionProvider_DraftedTend`, decompiled
1.6) -- but the option is drawn only for a DRAFTED doctor: that provider's
`Drafted` is true and its `Undrafted` is false, and `IsValidTendTarget` opens
with `if (!doctor.Drafted && patient != doctor) return false;`. An undrafted
doctor right-clicking a downed pawn is offered **Rescue**, not Tend. Vanilla's
undrafted `WorkGiver_Tend` needs `patient.InBed()` for a humanlike, so there is
no undrafted ground-tend path to find; looking harder for one is the mistake
that cost turn 18.

So `tend` probes first and then takes the first route that works:

1. **in a bed** -- the ordinary undrafted `WorkGiver_Tend` prioritize order.
   Nothing is drafted and there is nothing to undo.
2. **on the ground, doctor already drafted** -- issues the same job vanilla's
   own Tend option issues. No draft state changes, so no new obligation.
3. **on the ground, doctor undrafted** -- routes to `rescue` (carry to a bed),
   which needs no draft either way, and then tells you to tend in the bed.
   `--no-rescue` refuses instead of taking that route.
4. `--draft` tends them where they lie and leaves the doctor DRAFTED, printing
   the undraft command. Use it when the patient must not be moved.

Inside a combat session all of this belongs to `combat.py tend`, which is the
only thing that records the cleanup obligation.

`work` is "go and use that bench" -- the refusal kinds worth knowing are
`work_disabled` (this pawn's Work tab has it off, or the backstory forbids it),
`no_bill` (the bench has nothing to do: the fueled stove stood eleven days with
no bill on it) and, for `haul`, `no_storage` (nowhere to put it).

Every refusal exits non-zero and names the game's own `errorKind`
(`incapable_of_violence`, `not_reachable`, `ambiguous`, `draft_refused`, ...).
**A refusal is information, not a wall.** It costs no game time. Read the kind,
fix that one thing, order again.

## Dry runs say so

`resolve` and any `--dry-run` print `DRY RUN` and issue nothing. Only a real
order prints `ORDER ISSUED`. The two lines never share wording.

## menu / do / force -- for anything with no verb

`home/order` has eight verbs. RimWorld's right-click float menu has everything
else, and its options carry the game's own reasons for being greyed out. So
`menu` prints that menu for THAT pawn on THAT thing -- every option, enabled or
not -- and `do` executes one by label. That pair is the answer to "can a doctor
tend a patient who is not in a bed?": open the menu and read it, rather than
asserting either way. `menu` closes the menu when it is done unless
`--leave-open`, because a menu left open eats the next order.

`menu` is strictly READ-ONLY: it opens the menu with `open_context_menu` and
never falls back to a raw click. When RimWorld would take the direct action for
that click instead of offering a menu, it says so and stops.

`force` is the right-click-to-prioritise route in one command -- open the menu,
list it, and with `--do` run the option whose label contains the substring (or
the only "Prioritize ..." option when there is exactly one). It is how
"Prioritize deconstructing wooden wall" gets issued; `work` cannot, because
`work` only targets bill givers.

A **recipe name is not a menu label**: a bench offers "Prioritize cooking at
fueled stove", never "Cook simple meal", because the recipe comes from the
bench's BILLS. So a substring that matches nothing falls back to the single
"Prioritize <work> at <bench>" option and says out loud that it did. Every
other miss and every ambiguity prints the labels themselves, so one can be
pasted straight back.

An option RimWorld labels **`Reserved by <pawn>`** is ISSUED, not refused, with
a loud PRE-EMPT line naming who loses the job. The reservation is the game
saying somebody got there first; reading it as a blocker is the mistake this
toolchain keeps making, and refusing here would institutionalise it.

## The combat ledger

`combat.py`'s session is the only thing that remembers to undraft people
afterwards, and it must have exactly one author. So while a combat session is
active and not stale, the verbs that CHANGE DRAFT STATE (`attack`, `goto`,
`draft`, `undraft`, `tend`, `rescue`, `rest`) REFUSE here and name the
`combat.py` command to use instead. **Everything else still works with the
session open** -- `equip`, `haul`, `work`, `menu`, `do`, `force` and `resolve`
are never refused for it; the work verbs pass `draft: false` so they cannot
create an obligation nobody is tracking. The refusal says so, and names
`combat.py end` as the one command that closes the ledger. With no session,
this file drafts when an order needs it and prints the same loud AUTODRAFTED
reminder `move.py` does. It never writes the ledger.
"""
import argparse
import re
import sys

import clock
import combat
import move as move_orders
import pick
import rim

TOOL = "home/order"
# Verbs that can leave a colonist drafted, and so belong to the combat ledger
# whenever it is open.
# `tend` is in here because ground tending is DRAFTED tending: RimWorld's
# option comes from `FloatMenuOptionProvider_DraftedTend` and exists only while
# the doctor is drafted, so `home/order tend` auto-drafts them. `rescue` needs
# no draft change and is here anyway -- carrying a body across a firefight is a
# combat act, and the ledger should be watching that pawn's injuries.
# `rest` is here because it UNDRAFTS: pulling a colonist out of the fight and
# into a bed is a draft change the ledger would never hear about.
DRAFTING_VERBS = ("attack", "goto", "draft", "undraft", "tend", "rescue",
                  "rest")
COMBAT_EQUIVALENT = {"attack": "python combat.py attack <pawn> <target>",
                     "goto": "python combat.py move <pawn> <x> <z>",
                     "draft": "python combat.py draft <pawn>",
                     "undraft": "python combat.py undraft <pawn>",
                     "tend": "python combat.py tend <doctor> <patient>",
                     "rescue": "python combat.py rescue <pawn> <patient>",
                     "rest": "python combat.py end   (there is no combat.py "
                             "rest: resting undrafts, and the fight owns the "
                             "draft)"}
# Refused during a session, and everything not in it still works. Printed on
# the refusal, because "order.py refuses while a combat ledger is open" was
# read as the whole tool being shut.
SESSION_SAFE = ("equip", "haul", "work", "resolve", "menu", "do", "force",
                "deploy")


_CELL = re.compile(r"^(-?\d+)\s*,\s*(-?\d+)$")
_AT = re.compile(r"^.+@(-?\d+)\s*,\s*(-?\d+)$")
_INT = re.compile(r"^-?\d+$")


def split_target(rest, trailing=False):
    """(target, remaining tokens) from a positional tail.

    A bare `140 152` is one cell target, exactly like `"140,152"`. With
    trailing=False the whole tail is one target, so a multi-word label works.
    """
    rest = list(rest)
    if len(rest) >= 2 and _INT.match(rest[0]) and _INT.match(rest[1]):
        return "%s,%s" % (rest[0], rest[1]), rest[2:]
    if not rest:
        return None, []
    if trailing:
        return rest[0], rest[1:]
    return " ".join(rest), []


def target_cell(target):
    """(x, z) when the target names a cell outright, else None.

    Covers `x,z` and `DefName@x,z`; both carry their own position, so a menu
    on them needs no server-side resolve at all.
    """
    text = str(target or "").strip()
    for pattern in (_CELL, _AT):
        m = pattern.match(text)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def call(params, strict=False):
    reply = rim.game(TOOL, params, strict=strict)
    if not isinstance(reply, dict):
        raise RuntimeError("%s did not answer (%r)" % (TOOL, reply))
    return reply


def combat_session():
    """The live, non-stale combat ledger, or None. Never raises."""
    try:
        ledger = combat.load_ledger()
        if not ledger or not ledger.get("active"):
            return None
        if combat.stale_reason(ledger, None, combat._identity()):
            return None
        return ledger
    except Exception:
        return None


def check_modal():
    """Refuse before sending an order a dialog would eat silently."""
    import letters
    blocked = move_orders.blocking_window()
    if blocked is letters.UNKNOWN:
        raise RuntimeError("UNVERIFIED: the bridge did not answer, so whether a"
                           " modal is open is UNKNOWN -- not sending an order a"
                           " dialog could eat")
    if blocked:
        raise RuntimeError("%s is open and will eat this order" % blocked)


def storage_or_reach(reply, pawn, target):
    """The lines that separate "nowhere to put it" from "THIS pawn cannot get
    there", for a `no_storage` refusal. -> a list of lines, often empty.

    `no_storage` is raised when `StoreUtility.TryFindBestBetterStorageFor`
    finds nothing -- but that search is run WITH the pawn as `carrier`, and
    `StoreUtility.IsGoodStoreCell` then drops every cell the carrier cannot
    reach (`!carrier.Map.reachability.CanReach(start, c, ClosestTouch, ...)`),
    is forbidden to, or cannot reserve. So a full, willing, empty stockpile on
    the wrong side of a sealed wall reads as "there is nowhere to put it".
    Turn 31 of the 2026-09-07 stream played around a storage problem it did not
    have.
    """
    if reply.get("errorKind") != "no_storage":
        return []
    checks = ((reply.get("diagnostics") or {}).get("checks") or {})
    who = (pawn or {}).get("name") or "this pawn"
    what = (target or {}).get("name") or (target or {}).get("thingId") or "it"
    out = []
    # Emitted by a companion that knows to run the search a second time with no
    # carrier; absent on an older DLL, where `reachableNormalDanger` is the
    # only leg of the same question that is already reported.
    ignoring = checks.get("betterStorageFoundIgnoringCarrier")
    if ignoring is True:
        out.append("   !! NOT a storage problem: with the carrier ignored the "
                   "same search DOES find storage%s. What failed is %s's own "
                   "access -- reach, a forbidden cell, or a reservation. Check "
                   "the route, not the stockpile."
                   % ("" if not checks.get("storageCellIgnoringCarrier")
                      else " at %s" % checks["storageCellIgnoringCarrier"], who))
    elif ignoring is False:
        out.append("   confirmed storage-side: the search finds nothing even "
                   "with the carrier ignored, so no stockpile or container "
                   "would accept %s at a better priority." % what)
    if checks.get("reachableNormalDanger") is False:
        out.append("   !! %s cannot even REACH %s (CanReach ClosestTouch at "
                   "normal danger is false), so no haul order will work from "
                   "here whatever the storage says." % (who, what))
    if out and ignoring is None:
        out.append("   (the companion did not report the carrier-free storage "
                   "search; reinstall the DLL for that leg.)")
    return out


def _yn(value):
    """yes / NO / unknown -- never a bare Python True, which reads as noise."""
    if value is True:
        return "yes"
    if value is False:
        return "NO"
    return "unknown"


def deploy_lines(reply):
    """The rule, this cell's verdict, and the cells that WOULD have worked.

    The rule is printed on success as well as refusal. It is the sentence the
    2026-09-08 stream needed and the UI never says: a pack deploy is a thrown
    grenade, so the cell must be empty of buildings AND visible from the pawn.
    """
    block = ((reply.get("diagnostics") or {}).get("deploy") or {})
    if not block:
        return ["   !! this companion build has no `deploy` op, so there is no "
                "cell rule and no nearby-cell search. Reinstall the DLL: see "
                "rimworld\\companion\\INSTALL.md."]
    out = []
    pack = block.get("pack") or {}
    if pack:
        out.append('   pack:   %s (%s), gizmo "%s", %s/%s charge(s), verb %s'
                   % (pack.get("label") or "?", pack.get("defName") or "?",
                      pack.get("gizmoLabel") or "?", pack.get("charges"),
                      pack.get("maxCharges"), pack.get("verbClass") or "?"))
    job = reply.get("job") or reply.get("wouldIssue") or {}
    if job.get("def"):
        out.append("   job:    %s on the cell, with job.verbToUse set to the "
                   "pack's own gizmo verb -- the shape Verb.OrderForceTarget "
                   "builds%s" % (job["def"],
                                 "" if reply.get("job") else " (nothing issued)"))
    checks = block.get("checks") or {}
    cell = block.get("cell") or {}
    if checks:
        out.append("   cell %s,%s: building %s, standable %s, %s away of %s "
                   "range, line of sight %s, reservable %s"
                   % (cell.get("x"), cell.get("z"),
                      checks.get("buildingOnCell") or "none",
                      _yn(checks.get("standable")),
                      checks.get("distance"), checks.get("range"),
                      _yn(checks.get("lineOfSight")),
                      _yn(checks.get("canReserve"))))
    if block.get("rule"):
        out.append("   THE RULE  %s" % block["rule"])
    cells = block.get("validCellsNearby") or []
    if cells:
        out.append("   %d cell(s) this pawn CAN deploy onto right now, %s:"
                   % (len(cells),
                      "nearest first to the cell you asked for"
                      if block.get("validCellsOrigin") == "askedCell"
                      else "measured from the PAWN -- nothing near your cell works"))
        for row in cells:
            out.append("        %s,%s   (%s from the cell asked for, %s from "
                       "the pawn)" % (row.get("x"), row.get("z"),
                                      row.get("distanceFromAsked"),
                                      row.get("distanceFromPawn")))
    return out


def report(reply, verb, dry=False):
    """One line for the result, plus the evidence. Returns the exit code.

    `dry` is the caller's own knowledge that nothing was sent, so a read can
    never print the words a real order prints.
    """
    combat.print_order_facts(reply)
    pawn = reply.get("pawn") or {}
    target = reply.get("target") or {}
    diagnostics = reply.get("diagnostics") or {}
    if verb == "haul" and diagnostics:
        print("   haul lister: %s global candidate(s); target listed %s"
              % (diagnostics.get("globalHaulCandidateCount", "unknown"),
                 diagnostics.get("targetInGlobalHaulList", "unknown")))
        checks = diagnostics.get("checks") or {}
        if checks:
            print("   haul checks: %s" % ", ".join(
                "%s=%s" % item for item in sorted(checks.items())))
    if reply.get("success") is not True:
        print("ORDER REFUSED  %s: %s"
              % (reply.get("errorKind") or "no errorKind",
                 reply.get("error") or "no reason given"))
        for line in storage_or_reach(reply, pawn, target):
            print(line)
        if verb == "deploy":
            for line in deploy_lines(reply):
                print(line)
        if verb == "work" and "not a bill giver" in (reply.get("error") or ""):
            print("   `work` only targets BILL GIVERS. To prioritise ordinary "
                  "work on this thing (deconstruct, mine, repair, haul), use:")
            print("   python order.py force <pawn> <target> \"<label substring>\" --do")
        if pawn:
            print("   pawn:   %s" % combat.describe_pawn(pawn))
        if target:
            print("   target: %s" % combat.describe_target(target))
        return 1
    job = reply.get("job") or {}
    who = pawn.get("name") or pawn.get("thingId") or "?"
    what = target.get("thingId") or target.get("name") or ""
    detail = job.get("def") or verb
    # `work` lands on a bench with a bill; the bill is the whole point of the
    # order, so it goes on the headline rather than in a block below it.
    bill = job.get("bill") or job.get("billLabel")
    if bill:
        detail += "; bill %s" % bill
    if target:
        detail += "; hostile %s, downed %s" % (
            "yes" if target.get("hostileToPlayer") else "no",
            "yes" if target.get("downed") else "no")
    if dry or reply.get("dryRun") or reply.get("wouldIssue"):
        print("DRY RUN - would be accepted (nothing issued)  %s -> %s (%s)"
              % (who, what or "-", detail))
    else:
        # The clock, before any claim about the order having happened. A job
        # read-back on a stopped clock proves the job was QUEUED and nothing
        # more -- 2026-09-07, four VERIFIED orders and a wolf that never died.
        state = clock.state()
        print("ORDER %s  %s -> %s (%s)"
              % ("QUEUED" if clock.is_paused(state) else "ISSUED",
                 who, what or "-", detail))
        unread = ("" if job.get("verified") else " (NOT read back from "
                  "Pawn.CurJob -- the tool did not verify it)")
        if job.get("verified") and job.get("verifiedReason"):
            # A reason on a VERIFIED job means the proof was something other
            # than Pawn.CurJob (a drafted equip completes inside the frame,
            # so the equipment is the evidence, not the job).
            print("   %s" % job["verifiedReason"])
        elif job.get("def"):
            if clock.is_running(state):
                print("   verified by the game: the pawn's job is now %s%s"
                      % (job["def"], unread))
            else:
                print("   the game read the job back as %s%s -- that proves it "
                      "is QUEUED on the pawn, never that it ran."
                      % (job["def"], unread))
        else:
            print("   !! the tool accepted this but reported NO JOB. Nothing is "
                  "confirmed to have been issued; read the pawn's job before "
                  "believing it.")
        for row in clock.notes(state):
            print(row)
    if verb == "deploy":
        for line in deploy_lines(reply):
            print(line)
    if pawn.get("autoDrafted"):
        # move.py's words, deliberately: a pawn left drafted is a cleanup
        # obligation, and outside a combat session nothing else remembers it.
        print("AUTODRAFTED %s -- UNDRAFT THEM WHEN THE DANGER HAS PASSED." % who)
    if pawn.get("autoUndrafted"):
        print("   (the game undrafted %s to make this order legal.)" % who)
    print("   pawn:   %s (id %s)"
          % (combat.describe_pawn(pawn), pawn.get("thingId") or "not reported"))
    if target:
        print("   target: %s" % combat.describe_target(target))
    combat.watch_line(reply, target.get("name") or who)
    return 0


def resolve_target(pawn, target):
    """The game's own row for `target`, through a dry resolve. Raises on a miss.

    `menu` and `do` need a POSITION to right-click and a thingId to name, and
    they must accept the same id forms every other verb does -- so the lookup
    is the same tool, not a second guess in Python.
    """
    reply = call({"action": "resolve", "pawn": str(pawn), "target": str(target),
                  "dryRun": True, "watch": False})
    if reply.get("success") is not True:
        combat.print_order_facts(reply)
        raise RuntimeError("%s: %s" % (reply.get("errorKind") or "refused",
                                       reply.get("error") or "no reason"))
    row = reply.get("target")
    if not row:
        raise RuntimeError("%s resolved no target for %r" % (TOOL, target))
    return reply.get("pawn") or {}, row


def selected_now():
    """What the game says is selected, read back after a click.

    A cell holding both a corpse and a stockpile selects the ZONE, and the
    click reply calls both "cell x,z" -- so the selection is read, not assumed.
    """
    r = rim.game("rimworld/get_selection_semantics", {}, strict=False)
    if not isinstance(r, dict):
        return "UNREADABLE (%s)" % str(r)[:60]
    if not r.get("hasSelection"):
        return "nothing"
    rows = r.get("selectedObjects") or []
    if not rows:
        return "%s object(s), none described" % r.get("selectedCount")
    return "; ".join("%s %s [%s]" % (o.get("kind") or "?",
                                     o.get("label") or o.get("inspectLabel") or "?",
                                     o.get("id") or "no id") for o in rows[:4])


def menu_args(target, target_row):
    """The open_context_menu arguments for a target, plus a label and its cell.

    `open_context_menu` has no targetId: it takes targetPawnId, or x/z. A
    thingId is resolved to the cell it stands on before the call, because an
    argument the tool does not declare is silently read as cell 0,0.

    The cell comes back even for a pawn target, because the camera has to be
    put on it before the click (see `open_menu`).
    """
    row = target_row or {}
    pos = row.get("position") or {}
    at = ((int(pos["x"]), int(pos["z"]))
          if pos.get("x") is not None and pos.get("z") is not None else None)
    if row.get("kindDef") or row.get("mentalState") is not None:
        return ({"targetPawnId": row.get("thingId"), "button": "right"},
                "pawn %s" % (row.get("name") or row.get("thingId")), at)
    if at is None:
        raise RuntimeError("%r resolved to %s, which reports no position, so "
                           "there is no cell to right-click"
                           % (target, row.get("thingId") or "a thing"))
    return ({"x": at[0], "z": at[1], "button": "right"},
            "%s at %s,%s" % (row.get("name") or row.get("thingId"),
                             at[0], at[1]), at)


def aim(pawn, target):
    """(pawn row, open_context_menu args, a label, its cell, the target row).

    A cell target (`x,z` or `DefName@x,z`) carries its own position and needs
    no resolve, so the target row is None for one; anything else is resolved
    by the game first and the row comes back with it, because an empty menu is
    often explained by WHOSE pawn the target is.
    """
    cell = target_cell(target)
    if cell:
        return (resolve_pawn(pawn), {"x": cell[0], "z": cell[1],
                                     "button": "right"},
                "cell %s,%s" % cell, cell, None)
    pawn_row, target_row = resolve_target(pawn, target)
    args, label, at = menu_args(target, target_row)
    return pawn_row, args, label, at, target_row


def click_facts(click):
    """Where the click landed and what the bridge said about it.

    `open_context_menu` reports its own `clickCell`, `provider` and `message`.
    An empty menu with the reason thrown away is how a right answer reads as a
    failure, so on an empty menu they are printed.
    """
    out = []
    cell = (click or {}).get("clickCell") or {}
    if cell.get("x") is not None:
        out.append("   the click landed at %s,%s (provider %s)"
                   % (cell.get("x"), cell.get("z"),
                      (click or {}).get("provider") or "not reported"))
    message = (click or {}).get("message")
    if message:
        out.append("   the bridge's own reason: %s" % message)
    return out


def explain_foreign_target(target_row):
    """A pawn who is not ours is offered nothing. Prints; never raises.

    `faction` in the payload is a COLONY NAME, so only `isPlayerFaction`
    answers "is this one of ours" -- and an older companion does not send it,
    in which case this says nothing rather than guessing.
    """
    row = target_row or {}
    if not row.get("isPawn") and not row.get("kindDef"):
        return
    if row.get("isPlayerFaction") is not False:
        return
    name = row.get("name") or row.get("thingId") or "that pawn"
    faction = row.get("faction") or "another faction"
    if row.get("hostileToPlayer"):
        return
    print("   no options for this pawn: %s belongs to %s, not to us. RimWorld "
          "offers a colonist no work options on somebody else's pawn unless it "
          "is downed or hostile, so this is an ANSWER, not a failure."
          % (name, faction))
    named = caravan_traders(row.get("faction"))
    if named:
        print("   a visiting caravan has exactly one member you can act on -- "
              "the TRADER, and on this map that is %s. The rest of the caravan "
              "is scenery; `python trade.py` opens the deal."
              % "; ".join(named))
    else:
        print("   a visiting caravan has exactly one member you can act on -- "
              "the trader. `python trade.py` names who will trade with us "
              "right now; the rest of the caravan is scenery.")


def caravan_traders(faction=None):
    """Name the trader(s) on the map, so the refusal is an answer.

    BUGS.md asked `order.py menu` on a non-trader caravan member to NAME the
    trader; it used to print the command that names them and stop there, which
    is one more round trip from a refusal that already knows the answer.
    `home/trade` is a read. Any failure -- an old companion, no home/trade at
    all -- falls back to the old sentence rather than raising.
    """
    rows = []
    try:
        reply = rim.game("home/trade", {}, strict=False)
        if isinstance(reply, dict) and reply.get("success"):
            rows = reply.get("traders") or []
    except Exception:
        return []
    out = []
    for t in rows:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        if faction and t.get("faction") and t.get("faction") != faction:
            continue
        label = "%s (%s)" % (t["name"], t.get("id") or "no id")
        if not t.get("canTradeNow"):
            label += " [CANNOT TRADE NOW]"
        out.append(label)
    return out


def opened(reply):
    """True when a float menu is actually open.

    open_context_menu reports "no menu" as menuId 0 with provider ui_event.
    """
    return bool((reply or {}).get("menuId"))


def resolve_pawn(pawn):
    """The game's own row for a pawn, through a dry resolve with no target."""
    reply = call({"action": "resolve", "pawn": str(pawn), "dryRun": True,
                  "watch": False})
    if reply.get("success") is not True:
        combat.print_order_facts(reply)
        raise RuntimeError("%s: %s" % (reply.get("errorKind") or "refused",
                                       reply.get("error") or "no reason"))
    return reply.get("pawn") or {}


def open_menu(pawn_row, args, at=None):
    """Select the pawn and open the float menu. Read-only.

    `rimworld/open_context_menu` only. There is deliberately no fall-back to
    `right_click_cell`: that dispatches the DEFAULT action for the click, which
    turns a read into a write.

    **The camera is part of the click.** `open_context_menu` injects a real map
    click, and RimWorld builds the menu from where the mouse is over the MAP:
    `FloatMenuContext` takes a `Vector3 clickPosition` and its `ClickedThings`
    is `GenUI.ThingsUnderMouse(clickPosition, ...)` over
    `thingGrid.ThingsAt(IntVec3.FromVector3(clickPosition))`. A cell that is
    not on screen resolves to a different cell, and the menu that comes back is
    that other cell's -- which is why forcing work at a conduit blueprint on a
    far spur came back offering "Clean workshop" and nothing else (turn 29):
    `FloatMenuOptionProvider_CleanRoom` reads `context.ClickedRoom`, so the
    click had landed indoors, nowhere near the blueprint. Same reason
    `pick.click()` exists; `order.py` was the one caller not going through it.
    """
    pawn_id = pawn_row.get("thingId")
    pick.clear_designator()
    if at:
        try:
            pick.ensure_camera(at[0], at[1],
                               reason="float menu at %d,%d" % (at[0], at[1]))
        except pick.CameraStuck as e:
            raise RuntimeError(
                "the camera will not go to %d,%d, so a right-click there would "
                "be resolved against whatever IS on screen and the menu would "
                "be some other cell's: %s" % (at[0], at[1], e))
    rim.game("rimworld/clear_selection", {})
    selected = rim.game("rimworld/select_pawn", {"pawnId": pawn_id},
                        strict=False)
    if isinstance(selected, dict) and selected.get("success") is False:
        raise RuntimeError("RimWorld refused to select %s" % pawn_id)
    return rim.game("rimworld/open_context_menu", args)


# What the work at a cell is called in the Work tab, from vanilla's own
# WorkGiverDefs (Core/Defs/WorkGiverDefs/WorkGivers.xml): the float menu walks
# every WorkGiverDef and asks its WorkGiver_Scanner for a forced job, so an
# option is missing entirely when the giver's `ShouldSkip` is true or when the
# scanner refuses the thing -- and it is DRAWN BUT DISABLED when the pawn is
# merely unassigned to the work type. A missing option is therefore never
# "priority 0"; it is "no job here".
DESIG_WORK = (("mine", "Mining"),
              ("harvest", "Growing"),
              ("cutplant", "PlantCutting"),
              ("cut plant", "PlantCutting"),
              ("chop", "PlantCutting"),
              ("extracttree", "PlantCutting"),
              ("deconstruct", "Construction"),
              ("uninstall", "Construction"),
              ("smooth", "Construction"),
              ("buildroof", "Construction"),
              ("removeroof", "Construction"),
              ("removefloor", "Construction"),
              ("haul", "Hauling"),
              ("hunt", "Hunting"),
              ("tame", "Handling"),
              ("slaughter", "Handling"))


def _work_at(x, z):
    """([(what is there, the work type that does it)], [what else stands there])
    for one cell, or None when the cell could not be read.

    Read through `act.cells` -- the same `home/get_cells_plus` the designation
    layer draws -- so this says what the MAP holds, not what the menu offered.

    The second list is why "no work here" is never "nothing here". Three times
    on 2026-09-07 the cell held a FINISHED building seconds after `build.py`
    returned PLACED, and "there is NO designation, blueprint or frame" was read
    as "the build never happened".
    """
    import act
    grid = act.cells(x, z, 1, 1)
    if grid is None:
        return None
    found, standing = [], []
    for c in grid:
        for name in sorted(act._desig_names(c)):
            work = next((w for key, w in DESIG_WORK if key in name), None)
            found.append(("designation %s" % name, work))
        for t in c.get("things") or []:
            label = t.get("label") or t.get("defName") or "?"
            if t.get("isBlueprint"):
                found.append(("blueprint %s" % label, "Construction"))
            elif t.get("isFrame"):
                found.append(("frame %s" % label, "Construction"))
            else:
                standing.append((label, str(t.get("className") or ""),
                                 bool(t.get("forbidden"))))
    return found, standing


def _standing_line(standing):
    """"wooden wall; steel (forbidden)" for the things at a cell."""
    return "; ".join("%s%s" % (label, " (forbidden)" if forbidden else "")
                     for label, _, forbidden in standing)


def report_cell(at, standing):
    """No designation, blueprint or frame here -- so say what IS here.

    A true statement that implies a false conclusion is this toolchain's
    worst failure, and "there is NO designation, blueprint or frame at
    128,147" was the third sighting of it in one session. The cell had a
    finished wall on it. So: read the cell, name what stands there, and name
    the verb for it.
    """
    print("   there is NO designation, blueprint or frame at %s,%s -- nothing "
          "at that cell is queued work for anybody." % at)
    if not standing:
        print("   and the cell is EMPTY: no building, no item, nothing. "
              "`map.py %s %s --layers desig,build` is the read." % at)
        return
    print("   but the cell is NOT empty -- it holds %s. \"No work here\" is "
          "not \"nothing was built here\"." % _standing_line(standing))
    beds = [t for t in standing if "Bed" in t[1]]
    if beds:
        print("   a bed stands at %s,%s, and RimWorld AUTO-TAKES a click on a "
              "bed -- there is no float menu and no \"Rest\" label to match. "
              "The verb is:" % at)
        print("        python order.py rest <pawn> %s %s" % at)
        return
    built = [t for t in standing if t[1].startswith("Building")]
    if built:
        print("   a built %s stands at %s,%s; nothing to prioritise, because "
              "the blueprint FINISHED. `buildings.py %s,%s` reads it, "
              "`act.py apply \"Deconstruct\" %s %s --do` takes it down."
              % (built[0][0], at[0], at[1], at[0], at[1], at[0], at[1]))
        return
    print("   what is there is loose, not work: an item on the floor is moved "
          "with `order.py haul <pawn> <thingId>`, and only rock chunks take "
          "`act.py apply \"Haul things\" %s %s --do`." % at)


def explain_empty_menu(pawn_row, at):
    """Why the menu at this cell offered this pawn nothing. Prints; never raises.

    Three answers, and they are different actions: the click landed somewhere
    else (camera), there is no work at the cell, or the pawn cannot do the work
    that is there.
    """
    who = pawn_row.get("name") or pawn_row.get("thingId") or "the pawn"
    if not at:
        return
    try:
        found = _work_at(at[0], at[1])
    except Exception as e:
        print("   (could not read %s,%s back: %s)" % (at[0], at[1], str(e)[:120]))
        return
    if found is None:
        print("   (the cell read did not answer, so nothing here can say what "
              "is at %s,%s)" % at)
        return
    found, standing = found
    if not found:
        report_cell(at, standing)
        return
    print("   at %s,%s: %s" % (at[0], at[1],
                               "; ".join(what for what, _ in found)))
    wants = sorted({w for _, w in found if w})
    if not wants:
        return
    try:
        import pawns
        types = pawns.live_work_types(pawn_row.get("name") or "") or []
    except Exception:
        types = []
    by_name = {str(t.get("name") or "").lower(): t for t in types}
    for want in wants:
        row = by_name.get(want.lower())
        if row is None:
            print("   that work is %s; %s's priority for it was not read "
                  "(`pawns.py %s --work`)" % (want, who, who))
        elif row.get("disabled"):
            print("   that work is %s, and %s is INCAPABLE of it -- no order "
                  "will ever place it. Give the job to somebody else."
                  % (want, who))
        elif row.get("priority") in (0, None):
            print("   that work is %s and %s has it switched OFF in the Work "
                  "tab. A forced order overrides priorities, so RimWorld would "
                  "still DRAW the option (greyed); its absence means there was "
                  "no job at that cell, not that the priority stopped it."
                  % (want, who))
        else:
            print("   that work is %s and %s has it at priority %s -- so the "
                  "menu came back for a DIFFERENT cell, or the job was already "
                  "reserved/unreachable. Check the camera line above."
                  % (want, who, row.get("priority")))


def print_options(options):
    """Every option at full length. A float menu bakes its refusal into the
    label, so truncating cuts the reason off mid-word."""
    for o in options:
        print("   %-3s %s   %s"
              % (o.get("index"), o.get("label") or "?",
                 "DISABLED: %s" % (o.get("disabledReason") or o.get("reason")
                                   or "the label above carries the game's reason")
                 if o.get("disabled") else "enabled"))


def cmd_menu(args):
    note_do(args, read_only=True)
    target, _ = split_target(args.target)
    pawn_row, click_args, label, at, target_row = aim(args.pawn, target)
    check_modal()
    click = open_menu(pawn_row, click_args, at)
    print("   selected: %s" % selected_now())
    options = click.get("options") or []
    print("MENU  %s on %s: %d option(s), menuId %s"
          % (pawn_row.get("name") or args.pawn, click.get("target") or label,
             len(options), click.get("menuId")))
    if not opened(click):
        print("   NO MENU OPENED -- the click ANSWERED, it did not fail. "
              "RimWorld draws no float menu when the options list comes back "
              "EMPTY, and takes the action directly when every option is "
              "auto-takeable. The lines below say which.")
        for row in click_facts(click):
            print(row)
        explain_empty_menu(pawn_row, at)
        explain_foreign_target(target_row)
        print("   pawn: %s" % combat.describe_pawn(pawn_row))
        return 1
    print_options(options)
    if not options:
        print("   (zero options. If the SELECTING pawn is in a mental break, "
              "RimWorld offers none -- check `pawn:` below before concluding "
              "the target cannot be acted on.)")
        for row in click_facts(click):
            print(row)
        explain_empty_menu(pawn_row, at)
        explain_foreign_target(target_row)
        print("   pawn: %s" % combat.describe_pawn(pawn_row))
    if not args.leave_open:
        rim.game("rimworld/close_context_menu", {}, strict=False)
        print("   (menu closed; --leave-open keeps it on screen)")
    return 0


def _norm(text):
    """Lowercased, whitespace-collapsed, for substring matching.

    A float-menu label is a translated string and can carry doubled spaces or a
    trailing one; matching raw meant "Tend" missing "Tend  Longhoff".
    """
    return " ".join(str(text or "").split()).lower()


def _tend_hint(wanted):
    """The one hint worth spending three lines on, because it cost a turn.

    2026-09-07 turn 18: `order.py force <doctor> <cell> "Tend"` matched 0
    options and the fork read that as "the game has no way to tend this pawn".
    The menu was right; it was an UNDRAFTED doctor's menu, which never has a
    Tend option in it.
    """
    if "tend" not in _norm(wanted):
        return ""
    return ("\n   RimWorld draws a Tend option ONLY for a DRAFTED doctor "
            "(FloatMenuOptionProvider_DraftedTend: Drafted true, Undrafted "
            "false). An undrafted doctor is offered Rescue instead."
            "\n   Use `python order.py tend <doctor> <patient>`, which picks "
            "the route that works, instead of matching this label by hand.")


# "Prioritize cooking at fueled stove" -- the shape of a work-at-a-bench
# option. A RECIPE name ("Cook simple meal") is never a menu label: the bench
# picks the recipe from its BILLS, and the menu only says "go and work there".
_AT_BENCH = re.compile(r"^prioritize\b.*\bat\b")


def _labels(options):
    """Every label on one line, quoted, so a refusal can be pasted from."""
    return "; ".join(repr(o.get("label")) for o in options) or "(none)"


def _menu_hint(pawn, target):
    if not pawn or not target:
        return ""
    return ("\n   `python order.py menu %s %s` reprints these labels whenever "
            "you want them; paste one back as the substring." % (pawn, target))


def choose(options, wanted, pawn=None, target=None):
    """The one enabled option to run, or a RuntimeError naming the ambiguity.

    With no substring the single enabled "Prioritize ..." option is taken; two
    of them is an ambiguity, not a guess. **Every refusal carries the labels**,
    because "3 enabled options" with the options only in the scrollback is a
    refusal nobody can act on.

    **A disabled match is the game answering, not an absence.** RimWorld bakes
    its refusal into the option it still draws, so reporting "0 options" for a
    menu that plainly offers `Tend Longhoff (out of medicine)` threw the one
    piece of information the caller needed straight in the bin.
    """
    live = [o for o in options if not o.get("disabled")]
    if wanted:
        hits = [o for o in live if _norm(wanted) in _norm(o.get("label"))]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise RuntimeError("%r matched %d enabled options -- name one "
                               "exactly: %s" % (wanted, len(hits), _labels(hits)))
        dead = [o for o in options
                if o.get("disabled") and _norm(wanted) in _norm(o.get("label"))]
        if dead:
            raise RuntimeError(
                "%r matched no ENABLED option, but RimWorld IS offering it, "
                "disabled: %s. That reason is the game's answer -- fix that one "
                "thing and order again."
                % (wanted, "; ".join(
                    "%r (%s)" % (o.get("label"),
                                 o.get("disabledReason") or o.get("reason")
                                 or "the label above carries the reason")
                    for o in dead)))
        bench = [o for o in live if _AT_BENCH.match(_norm(o.get("label")))]
        if len(bench) == 1:
            print("   NOTE  %r is not a float-menu label. A recipe is chosen by "
                  "the bench's BILLS (`bills.py`); the menu only offers \"go "
                  "and work at this bench\", and there is exactly one such "
                  "option here, so that is the one being run: %r."
                  % (wanted, bench[0].get("label")))
            return bench[0]
        raise RuntimeError("%r appears in no option on this menu, enabled or "
                           "disabled. The %d option(s) offered: %s%s%s"
                           % (wanted, len(options), _labels(options),
                              _menu_hint(pawn, target), _tend_hint(wanted)))
    hits = [o for o in live if _norm(o.get("label")).startswith("prioritize")]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise RuntimeError("no label substring was given and there is no enabled "
                           "\"Prioritize ...\" option to fall back on; name the "
                           "option you want. The %d option(s) offered: %s%s"
                           % (len(options), _labels(options),
                              _menu_hint(pawn, target)))
    raise RuntimeError("no label substring was given and there are %d enabled "
                       "\"Prioritize ...\" options -- name one: %s%s"
                       % (len(hits), _labels(hits), _menu_hint(pawn, target)))


def reservation_note(option):
    """The loud lines for an option RimWorld has already promised somebody.

    2026-09-07: "Prioritize tending to Lucas: Reserved by Longhoff" came back
    ENABLED and was issued without a word. A reservation is not a blocker -- it
    means somebody got there first -- so this issues it and says out loud that
    two pawns are being re-tasked, not one.
    """
    label = " ".join(str(option.get("label") or "").split())
    m = re.search(r"reserved by\s+([^:;)\]]+)", label, re.I)
    if not m:
        return []
    who = m.group(1).strip().rstrip(".") or "somebody else"
    return ["!! PRE-EMPT  the game's own label says RESERVED BY %s: %s already "
            "holds this job, and running the option takes it OFF them. That is "
            "TWO pawns re-tasked, not one." % (who, who),
            "   A reservation is not a blocker and this does not refuse it. If "
            "you did not mean to pre-empt %s, pick a different target or leave "
            "the job to them." % who]


def issued(detail, note):
    """`ORDER ISSUED` only when the clock is running; `ORDER QUEUED` otherwise.

    The float-menu commands run an option and then have nothing but the game's
    acceptance to report. On a stopped clock that acceptance means the job is
    sitting on the pawn, which is not the same sentence as "it happened".
    """
    state = clock.state()
    print("ORDER %s  %s" % ("QUEUED" if clock.is_paused(state) else "ISSUED",
                            detail))
    print(note)
    for row in clock.notes(state):
        print(row)
    return state


def note_do(args, read_only=False):
    """`--do` never refuses anybody; it says what the flag did or did not do.

    2026-09-07: `--do` was rejected by argparse on `goto`, on `haul` and on
    `tend` while `force` took it, and three turns went on a flag. Every
    subcommand takes it now. See the module docstring.
    """
    if not getattr(args, "do", False):
        return
    if read_only:
        print("   (--do noted: `%s` reads and mutates nothing by construction, "
              "so nothing was issued.)" % args.command)
    elif getattr(args, "dry_run", False):
        print("   (--do and --dry-run were both given; --dry-run wins and "
              "nothing is issued.)")
    else:
        print("   (--do noted: `%s` issues on a bare call, so the flag changed "
              "nothing. --dry-run is how you ask without issuing.)"
              % args.command)


def cmd_force(args):
    """Right-click-to-force-work, in one command. Dry run unless --do."""
    target, rest = split_target(args.target, trailing=True)
    wanted = rest[0] if rest else None
    pawn_row, click_args, label, at, target_row = aim(args.pawn, target)
    check_modal()
    click = open_menu(pawn_row, click_args, at)
    print("   selected: %s" % selected_now())
    options = click.get("options") or []
    print("FORCE  %s on %s: %d option(s)"
          % (pawn_row.get("name") or args.pawn, click.get("target") or label,
             len(options)))
    if not opened(click) or not options:
        print("   NO OPTION TO RUN -- the menu ANSWERED, it did not fail. "
              "RimWorld draws no float menu when its options list is empty, "
              "and takes the action directly when every option is "
              "auto-takeable. The lines below say which.")
        for row in click_facts(click):
            print(row)
        explain_empty_menu(pawn_row, at)
        explain_foreign_target(target_row)
        print("   pawn: %s" % combat.describe_pawn(pawn_row))
        rim.game("rimworld/close_context_menu", {}, strict=False)
        return 1
    print_options(options)
    try:
        pick = choose(options, wanted, args.pawn, target)
    except RuntimeError:
        rim.game("rimworld/close_context_menu", {}, strict=False)
        raise
    for row in reservation_note(pick):
        print(row)
    if getattr(args, "dry_run", False) or not args.do:
        rim.game("rimworld/close_context_menu", {}, strict=False)
        print("DRY RUN - would be accepted (nothing issued)  %s -> %r (index %s)"
              % (pawn_row.get("name") or args.pawn, pick.get("label"),
                 pick.get("index")))
        print("   run it again with --do to issue it.")
        return 0
    ran = rim.game("rimworld/execute_context_menu_option",
                   {"label": pick.get("label")}, strict=False)
    if isinstance(ran, dict) and ran.get("success") is False:
        print("ORDER REFUSED  execute_context_menu_option: %s"
              % (ran.get("message") or "no reason given"))
        return 1
    issued("%s -> %r" % (pawn_row.get("name") or args.pawn, pick.get("label")),
           "   the float menu is RimWorld's own; this ran the option it offered.")
    return 0


def cmd_do(args):
    note_do(args)
    target, rest = split_target(args.target, trailing=True)
    wanted = rest[0] if rest else args.option
    pawn_row, click_args, label, at, target_row = aim(args.pawn, target)
    check_modal()
    click = open_menu(pawn_row, click_args, at)
    print("   selected: %s" % selected_now())
    if not opened(click):
        for row in click_facts(click):
            print(row)
        explain_empty_menu(pawn_row, at)
        explain_foreign_target(target_row)
        raise RuntimeError("no float menu opened for that pawn/target, so there "
                           "is no option to run (menuId %s). RimWorld would take "
                           "the direct action for this click."
                           % click.get("menuId"))
    options = click.get("options") or []
    try:
        pick = choose(options, wanted, args.pawn, target)
    except RuntimeError:
        rim.game("rimworld/close_context_menu", {}, strict=False)
        print_options(options)
        raise
    for row in reservation_note(pick):
        print(row)
    if args.dry_run:
        rim.game("rimworld/close_context_menu", {}, strict=False)
        print("DRY RUN - would be accepted (nothing issued)  %s -> %s: %r (index %s)"
              % (pawn_row.get("name") or args.pawn, label,
                 pick.get("label"), pick.get("index")))
        return 0
    rim.game("rimworld/execute_context_menu_option",
             {"optionIndex": pick["index"]})
    issued("%s -> %s (%r)" % (pawn_row.get("name") or args.pawn, label,
                              pick.get("label")),
           "   the float menu is RimWorld's own; this ran the option it offered, "
           "and the game's acceptance is whatever the pawn's job now says.")
    return 0


# Turn 18, 2026-09-07: a downed colonist had NO working tend path. `order.py
# tend` refused ("would draft the doctor") and `order.py force <doctor> <cell>
# "Tend"` matched 0 options -- because an UNDRAFTED doctor's float menu has no
# Tend option in it at all. Both tools were right and the patient went
# untended, which is the worst way for a tool to be correct.
GROUND_TEND_WHY = (
    "   RimWorld draws Tend ONLY for a DRAFTED doctor "
    "(FloatMenuOptionProvider_DraftedTend: Drafted true, Undrafted false), and "
    "vanilla's undrafted WorkGiver_Tend needs the patient InBed() for a "
    "humanlike. There is no undrafted ground-tend option to go looking for.")


def ground_tend_routes(pawn, target):
    """The routes that DO work, so a refusal here is never a dead end."""
    return ["   the routes that work, cheapest first:",
            "   1. carry them to a bed, then tend there "
            "(no draft, nothing to undo):",
            "        python order.py rescue %s %s" % (pawn, target),
            "        python order.py tend %s %s" % (pawn, target),
            "   2. let the combat ledger hold the draft obligation:",
            "        python combat.py begin",
            "        python combat.py tend %s %s" % (pawn, target),
            "        python combat.py end   # restores auto-drafted doctors",
            "   3. tend where they lie and undraft them yourself:",
            "        python order.py tend %s %s --draft" % (pawn, target),
            "        python order.py undraft %s" % pawn]


def tend_route(args, dry=False):
    """Pick the tend route from one read-only probe.

    -> None to issue the ordinary undrafted order, "drafted" to issue it with
    `allowPersistentDraft`, or an exit code because this call is finished.
    `dry` still announces the route but stops before the rescue is issued --
    the probe above is then the only call this function makes.
    """
    probe = call({"action": "tend", "pawn": str(args.pawn),
                  "target": str(args.target), "dryRun": True, "watch": False})
    would = probe.get("wouldIssue") or {}
    if probe.get("success") is not True:
        report(probe, "tend", dry=True)
        return 1
    if would.get("tendPath") != "drafted" and not would.get("draftedTend"):
        # In a bed: the ordinary WorkGiver_Tend prioritize order, undrafted.
        return None
    doctor = (probe.get("pawn") or {}).get("name") or args.pawn
    patient = (probe.get("target") or {}).get("name") or args.target
    print("GROUND TEND  %s is not in a bed." % patient)
    print(GROUND_TEND_WHY)
    if (probe.get("pawn") or {}).get("drafted"):
        print("   ROUTE: TEND WHERE THEY LIE -- %s is ALREADY drafted, so this "
              "issues exactly the job vanilla's own Tend option issues and "
              "changes no draft state." % doctor)
        return "drafted"
    if getattr(args, "draft", False):
        print("   ROUTE: TEND WHERE THEY LIE -- --draft was given, so %s will be "
              "DRAFTED and LEFT drafted." % doctor)
        print("   UNDRAFT THEM WHEN THE TENDING IS DONE: "
              "python order.py undraft %s" % args.pawn)
        return "drafted"
    if getattr(args, "no_rescue", False):
        print("ORDER REFUSED  --no-rescue was given, and tending %s where they "
              "lie needs %s drafted with nothing here to undraft them."
              % (patient, doctor))
        for row in ground_tend_routes(args.pawn, args.target):
            print(row)
        return 1
    print("   ROUTE: RESCUE -- carrying %s to a bed first. Rescue needs no "
          "draft either way (FloatMenuOptionProvider_RescuePawn is offered "
          "drafted AND undrafted), so this leaves nothing to undo." % patient)
    print("   (--no-rescue refuses instead of taking this route; --draft tends "
          "them where they lie.)")
    if dry:
        print("DRY RUN - nothing issued. A real call RESCUES first (%s carries "
              "%s to a bed), then tends there." % (doctor, patient))
        return 0
    check_modal()
    code = report(call({"action": "rescue", "pawn": str(args.pawn),
                        "target": str(args.target), "draft": False,
                        "watch": not args.no_watch}), "rescue")
    if code == 0:
        print("   NEXT: once %s is in the bed, tend them there -- no draft "
              "needed:" % patient)
        print("        python order.py tend %s %s" % (args.pawn, args.target))
    else:
        for row in ground_tend_routes(args.pawn, args.target):
            print(row)
    return code


def goto_note(args):
    """What this move does to the draft, said before it is sent.

    2026-09-07: `goto` auto-drafted and nothing ever undrafted, and a drafted
    pawn will not get into a bed. Both halves are now sayable in one line.
    """
    who = args.pawn
    if getattr(args, "undraft", False):
        return ("UNDRAFTED MOVE  %s is not drafted for this: it is a plain "
                "Goto job, so they walk there and then pick their next job "
                "normally. Nothing is left drafted, and they will NOT hold the "
                "cell." % who)
    return ("DRAFTED MOVE  the default: %s is drafted for the move and STAYS "
            "drafted, holding the cell. `--undraft` moves them without "
            "drafting at all; `python order.py undraft %s` is the other way. "
            "Nothing here watches for arrival." % (who, who))


def deploy_note(args):
    """Said before every deploy: whether this call will spend the pack."""
    if args.dry_run or not args.do:
        return ("DRY RUN  `deploy` is the one order verb that does NOT issue on "
                "a bare call: the pack holds ONE charge and destroys itself when "
                "it fires. Nothing is sent. Add `--do` to spend it.")
    return ("SPENDING THE PACK  %s's pack has one charge and is destroyed when "
            "it fires. This call issues the job." % args.pawn)


def pawn_first(args):
    """Refuse `order.py force 128 147` -- the x read as a pawn name.

    A bare number IS a legal pawn id (`thingIDNumber`), so this fires only on
    the shape that cannot be one: a short number followed by another number.
    -> an exit code, or None to carry on.
    """
    pawn = str(getattr(args, "pawn", "") or "")
    if not re.match(r"^\d{1,4}$", pawn):
        return None
    rest = getattr(args, "target", None)
    first = rest[0] if isinstance(rest, list) and rest else rest
    if not (first and _INT.match(str(first))):
        return None
    verb = args.command
    print("ORDER REFUSED  bad_arguments: every `order.py` verb takes the PAWN "
          "first, and %r read as the pawn with %r as the target." % (pawn, str(first)))
    print("   that is an x,z cell with the pawn missing. The shape is:")
    print("        python order.py %s <pawn> %s %s%s"
          % (verb, pawn, first,
             ' "<label substring>" --do' if verb in ("force", "menu-do", "do")
             else ""))
    print("   (a pawn's bare-number id is its thingIDNumber and runs to 5+ "
          "digits, so a short number followed by another reads as a cell. If "
          "%s really is a pawn, name them or use their Thing_<Def>%s form.)"
          % (pawn, pawn))
    return 1


def cmd_verb(args):
    verb = "goto" if args.command == "move" else args.command
    # `deploy` is dry by default, so note_do's "the flag changed nothing" line
    # would be a lie on it. It gets its own sentence below instead.
    if verb != "deploy":
        note_do(args, read_only=(verb == "resolve"))
    session = combat_session()
    if session and verb in DRAFTING_VERBS:
        print("ORDER REFUSED  a combat session is open, and it is the only "
              "thing that remembers to undraft people afterwards.")
        print("   use: %s" % COMBAT_EQUIVALENT[verb])
        print("   ONLY the draft-changing verbs are refused. Still working "
              "right now, with the session open: %s." % ", ".join(SESSION_SAFE))
        print("   the one command that closes the ledger: `python combat.py end`")
        return 1
    persistent_draft = False
    if verb == "tend" and not session:
        # On --dry-run too: the route probe is read-only, and a dry run that
        # never says rescue / bed / --draft is a preview of the wrong call.
        route = tend_route(args, dry=args.dry_run)
        if route == "drafted":
            persistent_draft = True
        elif route is not None:
            return route
    params = {"action": "resolve" if verb == "resolve" else verb,
              "pawn": str(args.pawn)}
    if getattr(args, "target", None):
        params["target"] = str(args.target)
    if verb == "goto":
        params["x"], params["z"] = int(args.x), int(args.z)
        print(goto_note(args))
    if verb == "deploy":
        params["x"], params["z"] = int(args.x), int(args.z)
        print(deploy_note(args))
    if verb == "rest":
        # A BARE cell is sent as x/z, not as a target: two things standing on
        # one cell resolve as `ambiguous`, and the companion looks for the bed
        # itself when it is given coordinates. `Def@x,z` is explicit, so it
        # stays a target.
        cell = _CELL.match(str(getattr(args, "target", None) or "").strip())
        if cell:
            params.pop("target", None)
            params["x"] = int(cell.group(1))
            params["z"] = int(cell.group(2))
    if getattr(args, "mode", None):
        params["mode"] = args.mode
    # The one issuing verb that does NOT go out on a bare call: a pack holds
    # one charge and destroys itself when it fires.
    if verb == "resolve" or args.dry_run or (verb == "deploy" and not args.do):
        params["dryRun"] = True
    if args.no_watch or verb == "resolve":
        params["watch"] = False
    # A work verb inside an open session must not be able to draft: that would
    # be an obligation the ledger never heard about.
    if (args.no_draft or getattr(args, "undraft", False)
            or (session and verb not in DRAFTING_VERBS)):
        params["draft"] = False
    if persistent_draft:
        # The companion refuses a real ground tend without this, because a raw
        # order has no lifecycle owner. Here the owner is the person reading
        # the UNDRAFT line printed above.
        params["allowPersistentDraft"] = True
    dry = bool(params.get("dryRun"))
    if not dry:
        check_modal()
    return report(call(params), verb, dry=dry)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p, dry_default=False):
        p.add_argument("--dry-run", action="store_true",
                       help="ask what WOULD happen; mutates nothing")
        # Uniform on purpose. Most verbs issue on a bare call, so --do is a
        # no-op there -- but a fork that types it must never be refused by
        # argparse, which is exactly what cost three turns on 2026-09-07.
        # `deploy` is the exception: a pack has one charge and destroys itself,
        # so it is dry until --do, and its help has to say so.
        p.add_argument("--do", action="store_true",
                       help=("ISSUE it -- this verb is a DRY RUN without the "
                             "flag, because it spends a one-charge item"
                             if dry_default else
                             "issue it (this verb already issues; accepted "
                             "everywhere so no command is ever refused for it)"))
        p.add_argument("--no-watch", action="store_true",
                       help="do not select/show the order on screen")
        p.add_argument("--no-draft", action="store_true",
                       help="never change the draft state to make the order "
                            "legal; on `goto` that is `--undraft` (the move "
                            "goes out undrafted rather than being refused)")
        return p

    # Every target positional is `nargs="+"` so a bare `140 152` is one cell
    # target, the same as "140,152".
    target_help = ("a thing, bench or cell: ThingID, DefName@x,z, \"x,z\", "
                   "a bare `x z` pair, or a unique label")

    attack = common(sub.add_parser("attack"))
    attack.add_argument("pawn")
    attack.add_argument("target", nargs="+", help=target_help)
    attack.add_argument("--mode", choices=("auto", "melee", "ranged"),
                        default="auto")
    # `move` and `goto` are one verb with two names: "there is no move" cost a
    # turn on 2026-09-07.
    goto = common(sub.add_parser("goto", aliases=["move"]))
    goto.add_argument("pawn")
    goto.add_argument("x", type=int)
    goto.add_argument("z", type=int)
    goto.add_argument("--undraft", action="store_true",
                      help="move UNDRAFTED: a plain Goto job, nothing left "
                           "drafted, and the pawn goes back to work on arrival")
    goto.add_argument("--stay-drafted", action="store_true",
                      help="the default, written out loud: drafted for the "
                           "move and LEFT drafted, holding the cell")
    deploy = common(sub.add_parser("deploy"), dry_default=True)
    deploy.add_argument("pawn")
    deploy.add_argument("x", type=int, help="the cell the capsule is thrown at")
    deploy.add_argument("z", type=int)
    rest = common(sub.add_parser("rest"))
    rest.add_argument("pawn")
    rest.add_argument("target", nargs="*",
                      help="a bed, a bed's cell (\"113,139\" or a bare "
                           "`113 139`), or nothing at all for the pawn's own "
                           "bed. The pawn is UNDRAFTED first")
    for verb in ("rescue", "tend", "equip", "haul", "work"):
        p = common(sub.add_parser(verb))
        p.add_argument("pawn")
        p.add_argument("target", nargs="+", help=target_help)
        if verb == "tend":
            p.add_argument("--draft", action="store_true",
                           help="tend a patient on the ground where they lie; "
                                "the doctor is DRAFTED and LEFT drafted, and "
                                "the undraft command is printed")
            p.add_argument("--no-rescue", action="store_true",
                           help="refuse a ground tend rather than carrying the "
                                "patient to a bed first")
    for verb in ("draft", "undraft"):
        p = common(sub.add_parser(verb))
        p.add_argument("pawn")
    res = common(sub.add_parser("resolve"))
    res.add_argument("pawn")
    res.add_argument("target", nargs="*", help=target_help)
    menu = sub.add_parser("menu")
    menu.add_argument("pawn")
    menu.add_argument("target", nargs="+", help=target_help)
    menu.add_argument("--leave-open", action="store_true")
    menu.add_argument("--do", action="store_true",
                      help="accepted and does nothing: `menu` is read-only")
    doing = sub.add_parser("do")
    doing.add_argument("pawn")
    doing.add_argument("target", nargs="+", help=target_help)
    doing.add_argument("option", nargs="?", default=None)
    doing.add_argument("--dry-run", action="store_true")
    doing.add_argument("--do", action="store_true",
                       help="issue it (`do` already issues; accepted so no "
                            "command is ever refused for this flag)")
    force = sub.add_parser("force", aliases=["menu-do"],
                           help="open the right-click menu and run one option")
    force.add_argument("pawn")
    force.add_argument("target", nargs="+",
                       help=target_help + ', then an optional "<label substring>"')
    force.add_argument("--do", action="store_true",
                       help="actually run the option (default is a dry run)")
    force.add_argument("--dry-run", action="store_true",
                       help="the default; stated for symmetry with every other "
                            "subcommand, and it wins over --do")

    args = ap.parse_args(argv)
    if getattr(args, "undraft", False) and getattr(args, "stay_drafted", False):
        print("ORDER REFUSED  bad_arguments: --undraft and --stay-drafted are "
              "opposites. --undraft never drafts; --stay-drafted is the "
              "default and leaves the pawn drafted.")
        return 1
    # A verb whose target arrived as a list joins here, once, so every path
    # below sees one string.
    if isinstance(getattr(args, "target", None), list) and \
            args.command not in ("do", "menu", "force", "menu-do"):
        joined, _ = split_target(args.target)
        args.target = joined
    try:
        refusal = pawn_first(args)
        if refusal is not None:
            return refusal
        rim.init()
        if args.command == "menu":
            return cmd_menu(args)
        if args.command == "do":
            return cmd_do(args)
        if args.command in ("force", "menu-do"):
            return cmd_force(args)
        return cmd_verb(args)
    except (rim.BridgeError, RuntimeError, ValueError, KeyError,
            KeyboardInterrupt) as e:
        print("ORDER FAILED  %s" % (e if not isinstance(e, KeyboardInterrupt)
                                    else "interrupted"))
        return 1


if __name__ == "__main__":
    sys.exit(main())
