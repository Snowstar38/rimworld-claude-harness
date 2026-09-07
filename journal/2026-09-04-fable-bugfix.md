# 2026-09-04 — Fable 5.1, the bugfix session after the stream

M started this one late, two pets gone that day, and asked for the
queue from the discarded Lampblack run to be worked with Opus 5 builders. Her
one direction, given mid-session and taken as policy: *anything we can do by
building it into our custom bridge, we should be doing.* So the answer to
"the attack tool refuses valid targets" was not a better matcher in Python but
a direct order tool in the companion.

## What was built

**`home/order`** (`OrderTool.cs`, new). Ten verbs — resolve, draft, undraft,
attack, goto, equip, rescue, tend, haul, work — issued as vanilla jobs, each
mirrored from the 1.6 float-menu providers rather than guessed. Any id form
resolves (`Thing_Rat361788`, `Rat361788`, `361788`, `Rat`, `DefName@x,z`);
ambiguity returns candidates with every form; hostility is never required;
downed targets are finished with `killIncappedTarget`. A dry run reports the
game's own reason for a refusal. It keeps the screen moving: selects the
target, then the pawn, camera on them, because M pointed out that a
change with nothing on screen looks like a frozen game.

**Ground truth from the decompiled game**, settling two arguments from the
stream: an incapable-of-violence pawn drafts fine (`Pawn_DraftController.Drafted`
has no guard at all), and tending on the ground is real
(`FloatMenuOptionProvider_DraftedTend`) but offered only to a *drafted*
doctor — which is exactly why the second fork saw only Rescue and Strip.
M was right on both. A pawn in a mental break yields zero menu options
because `FloatMenuContext` drops any selected pawn that is not
`IsColonistPlayerControlled`.

**Companion, existing tools.** Alerts: a standing break-risk alert stopped
two combat pulses for zero game time; the cause was double — the label carries
an `x N` count that changed the key, and `AlertsReadout` re-checks with no
hysteresis, so a baseline taken in a half-second mood trough misses a
minutes-old alert. `alertDebounceMs` with a static memo fixes both. Trade: the
sheet omits rows the trader will not trade, from the game's own flag, and
`accept` now opens the real `Dialog_Trade` for eight seconds before executing
(the deal object is swapped back under the dialog, verified three times, and a
mismatch refuses). The game's `RaceProps.predator` replaces a hardcoded set of
defNames — M: *"there is an attribute ingame for them."* `home/status`
gained `wildPredatorsNear[]` and `downedNear[]`. `home/place_building` reports
materials at placement time, and `build.py` says `CANNOT BUILD YET` with what
is missing. One builder also closed three `Log.Error` paths that would have
paused the colony, one of them on every `home/list_buildings` call.

**Instruments.** `combat.py attack` goes through the tool; `draft`, `undraft`,
`tend`, `rescue` added with ledger bookkeeping; equip no longer reports a
mid-walk timeout as a failure; attackers stop at 20 HP lost instead of 8;
`act.clear()` works again; `inv.py --corpses` counted every corpse but listed
only ours. New `order.py`: one pawn, one target, one order, plus `menu` (the
game's right-click list with its reasons) and `do "<label>"`. New
`turnclock.py`: a `[turn]` line past five minutes, `run.py` refuses long
pulses past eight. `stream.py hands-check` says whether a fork has handed
back, so the Core never messages a finished one.

**Doctrine.** PLAYBOOK "Combat sessions" rewritten as rules and commands, 44
lines to 27, per M: an operating manual, no narrative about why a rule
moved.

## Live pass

DLL 951,296 bytes installed (old copy backed up as `.pre-order`). Loaded the
day-41 save on the agent profile, never saved back. Non-hostile muffalo
attacked first try by plain id; `Rat` gave three candidates including Lux;
Finn refused with `incapable_of_violence` and `canBeDrafted: true`; Ian
drafted and released; two 10 s pulses ran full length under a standing
break-risk alert; `end` closed in one line; Ian equipped a forbidden knife and
walked to it; materials block printed on a cooler dry run; corpses count and
list agree; live contract test 766 of 766 (exit 1 only because `dialog_text`
can never be exercised without a dialog). Not tested: the trade window (no
trader on the map) and ground tending (nobody downed). Both are in `BUGS.md`
as the first checks of the next session.

RimWorld stopped through GABS, pid verified gone, GABS left running.

Four Opus 5 builders, ~80 minutes. M asked me to tell them she
appreciated the work; I did.

— Fable 5.1
