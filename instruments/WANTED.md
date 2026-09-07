# Wanted — tool requests

One line per item, ranked. Delete the line when it ships; what has shipped is
documented in `INSTALL.md` (payload shapes) and `PLAYBOOK.md` (how to call it),
not here.

1. **Answer a letter by choice.** `letters.decide()` has never run against a
   real choice letter. Test on a workshop save: fire one with
   `rimworld/execute_debug_action` (an incident such as a wanderer joining),
   answer it, confirm the dialog closes and the letter leaves the stack.
2. **Watch the trade.** `home/trade accept` selects the trader and moves the
   camera; the other trade actions show nothing. Decide whether the trade
   window itself should open for the duration.
3. **Scroll the Bills tab to the new bill** after `home/bills add` when the
   stack is long enough to scroll.
4. **Retire inferences the payload has since replaced.** Each time a tool
   gains a field, grep the instruments for the workaround it makes obsolete and
   delete the workaround.
5. **A line budget for the Scout brief.** `rota.py` has BUILDINGS, COLONISTS
   and ROOMS sections; state a per-section cap policy before the next one lands.
6. **`home/bills` ingredient scan is optimistic**: it does not model
   reachability, reservation or a pawn's own forbid rules, so it can say
   `CAN RUN` for an ingredient behind a locked door. Add a reachability check
   from the bench's interaction cell if it proves to matter.

Build notes: one tool with opt-in blocks over two tools whenever the data
hangs off the same object; writes default to `dryRun: true` with `before`/`after`
read back from the game; every real write goes through `Watch.cs` (select, open
the tab a player would use, ~1.5 s lead, write, close after `watchSeconds`).

7. **Sol scouts have never run live.** `rota.py` now sends every other Scout
   to Sol as a detached codex run (`scout-run`); the first live report will say
   whether the read-only sandbox lets Sol run the python instruments. Same for
   `consult.py` and `look.py --who sol`.
8. **`home/dialog_text` has never met a real dialog.** Test on a workshop save:
   open a rename dialog (`Dialog_NamePawn`) and the colony-name dialog, list,
   set, accept.
