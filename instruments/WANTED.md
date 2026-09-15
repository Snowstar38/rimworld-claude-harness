# Wanted — tool requests

One line per item, ranked. Delete the line when it ships; what has shipped is
documented in `INSTALL.md` (payload shapes) and `PLAYBOOK.md` (how to call it),
not here.

1. **Answer a letter by choice.** `letters.decide()` has never run against a
   real choice letter (the offline half was reviewed against `ChoiceLetter`
   on 2026-09-08). Test on a workshop save: fire one with
   `rimworld/execute_debug_action` (an incident such as a wanderer joining),
   answer it, confirm the dialog closes and the letter leaves the stack.
2. **Watch the trade.** `home/trade accept` selects the trader and moves the
   camera; the other trade actions show nothing. Decide whether the trade
   window itself should open for the duration.
3. **Retire inferences the payload has since replaced.** Each time a tool
   gains a field, grep the instruments for the workaround it makes obsolete and
   delete the workaround. This is the standing rule; **item 12 is the current
   list**, audited 2026-09-11.

Build notes: one tool with opt-in blocks over two tools whenever the data
hangs off the same object; writes default to `dryRun: true` with `before`/`after`
read back from the game; every real write goes through `Watch.cs` (select, open
the tab a player would use, ~1.5 s lead, write, close after `watchSeconds`).

4. **Sol scouts have never run live.** `rota.py` now sends every other Scout
   to Sol as a detached codex run (`scout-run`); the first live report will say
   whether the read-only sandbox lets Sol run the python instruments. Same for
   `consult.py` and `look.py --who sol`.
5. **`home/dialog_text` has never met a real dialog.** The offline half was
   fixed on 2026-09-08 (`Dialog_NamePawn` name rows only, caps enforced). Test
   on a workshop save: open a rename dialog (`Dialog_NamePawn`) and the
   colony-name dialog, list, set, accept.
6. **`status.py`'s predator count is stale.** It still prints `+N hunt(s)
   within 40 cells that WOULD stop supervised play`; the guard now stops only on
   a hunt against a colony pawn or a conscious predator within 12 cells.
   `play.blocking_threats()` already computes the right number — import it.
7. **`run.py --ignore-hostile` is still value-required and id-only**, with no
   `--ignore-predator`. `home/play_until_event` classifies prey ownership, so it
   never had play.py's bug, but it has no proximity rule and no bare form.
8. **Five instruments still read `isColonist` alone**, so a colony ghoul is
   missing from their counts: `rota.py:948,1227`, `status.py`, `map.py:1989,1994`,
   `verify.py:192`, `watch.py:200`. The one-line fix in each is
   `pawns.is_colony_member(row)` — `isColonist or (ghoul and playerFaction)`.
9. **`rimworld/select_pawn` is colonists-only upstream.**
   `SelectionCapabilityModule.SelectPawn` calls `ResolveColonist`, so an animal,
   prisoner, visitor or raider falls back to a cell click that cannot separate
   two pawns on one tile. Wanted: an `anyPawn` bool switching it to
   `ResolveCurrentMapPawn` (resolve first, then select — `Selector.Select` logs
   on a null, destroyed or world pawn).
10. **A `designator: {armed, label}` field beside `ui.targeter`.** A
    `Command_Action` that arms a designator (Mine, Deconstruct, Cancel) opens no
    window and no targeter, so `ui.py click` on one reports `nothing opened or
    closed AND no targeter` — true and incomplete. Read
    `Find.DesignatorManager.SelectedDesignator` in `UiBlock` the way `targeter` is.
11. **`inHomeArea` on `home/status`'s threat rows.** `combat.py end` can only
    ask how far a hostile is from the nearest colonist, not whether it is inside
    the home area; `ListThingsTool.cs:422` already computes the bool for things.
    Add it to `DescribeThreatPawn` and `harmless_reason` ANDs it in one line.
12. **Retire inferences the payload has replaced — the 2026-09-11 audit.**
    Twelve candidates, best first, from `INSTALL.md`'s payload sections read
    against `src\*.cs` at HEAD; file:line is the 2026-09-11 tree.
    1. `watch.py:181` `wild_hunter()` infers "is this hunting predator wild" from `not p["faction"]` — replace with `home/status` `threats.huntingPredators[]` / `huntersIgnored[].ignoredReason`, which also applies a prey test `wild_hunter` cannot.
    2. `watch.py:212` infers a wild predator from `predator and not faction` — replace with the `home/list_pawns` row field **`wild`** (`ListPawnsTool.cs:664`), which a factionless humanlike does not fool.
    3. `combat_actions.py:32` `_clear_screen_for_order()` calls `letters.dialog_window()`, a second `rimworld/get_ui_state` plus a client-side ranking — replace with `ui.modalOpen` off the `home/status` reply `_pawn()` fetches ten lines later.
    4. `run.py:367` reads the raw `nonImmediateDialogWindowOpen`, which counts `EditWindow_Log` — replace with `home/status` `ui.modalOpen` (the `EditWindow` prefixes are filtered server-side).
    5. `letters.py:380` `windows_ranked()` / `:435` `dialog_window()` re-rank the window list client-side — replace with `home/status` `ui.modalWindow`; `StatusTool.cs:1243-1258` is the same algorithm and `NonBlockingWindowPrefixes` is byte-identical to `IGNORE_WINDOWS`.
    6. `ui.py:1199` `selection()` has no fallback when `rimworld/get_selection_semantics` throws on a selected turret — fall back to `home/status` `ui.selectedCount` / `ui.selectedFirstLabel`, read off `Find.Selector` directly.
    7. `pick.py:88` `_selection_via_gizmos()` infers what is selected from `list_selected_gizmos` `owners[]` — same `ui.selectedCount` / `ui.selectedFirstLabel` for count and label; keep the gizmo scan only for the `ids` set.
    8. `watch.py:283,293,427` makes three stock calls a step (`list_letters`, `list_messages`, `list_alerts`) — one `home/status` returns `letters[]`, `messages[]`, `alerts[]` and `time{}` in ~7 KB. This is the function M's five-second stutter was measured on.
    9. `watch.py:80` `ours()` and `move.py:37` `_colonist()` read the roster from `rimworld/list_colonists` — replace with `home/status` `colonists[]`, which also carries the `thingId` both callers currently lack.
    10. `buildings.py:1559` `gizmo_cmd()`'s fallback goes through `get_map_target_info`, whose `target` has no `thingId` for a pawn — look the name up in `home/status` `colonists[].thingId` / `threats.hostiles[].thingId`.
    11. `rota.py:771` substring-matches `"violen"` against `bio.incapableOf`, the localized label the game draws — replace with `"Violent" in bio.incapableOfTags[]`, the raw `WorkTags` beside it.
    12. `clock.py:91` `_why_paused()` can say "a modal window is holding the clock" but not WHICH — pass the `ui` block through and name it from `ui.modalWindow` / `ui.windowsForcePause`, both already on the reply `clock._read()` makes.

    Checked and **left alone**: `letters.py:247` `is_expired()` (neither
    `rimworld/list_letters` nor `home/status.letters[]` carries `disappearAtTick`
    / `TimeoutPassed`, so the workaround still stands); `isBlueprint` / `isFrame`,
    the `billIngredients` verdicts and `powerNets[]` / `powerSummary` are fully
    adopted already, with no string tests or re-derivation left anywhere.
