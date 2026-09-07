# Combat live test — 2026-09-03

Fixture: `bad shell game`, paused, three colonists, one deliberately impaired
manhunter tortoise. The pre- and post-manunter saves (`shell game` and
`bad shell game`) were marked void so normal `setup.py --newest` cannot select
them. Raw timing rows are in gitignored `state/combat-live-20260903-2325.jsonl`.

## Results

| Operation | End-to-end | Principal game calls | Result |
|---|---:|---|---|
| `combat.py begin` | 235 ms | pause 25 ms; `home/status` 66 ms / 7.9 KB | Paused baseline written at tick 59 |
| `combat.py status` | 172 ms | `home/status` 55 ms / 7.9 KB | Active session reported |
| `status.py --brief` | 187 ms | `home/status` 56 ms / 7.9 KB | Threat and combat state visible |
| first `combat.py move` | 189 ms | pause 16 ms; status 50 ms | Safely refused before mutation due to a real schema bug |
| corrected `combat.py move John 121 117` | 491 ms | 12 calls after initialization, about 335 ms in game calls | Draft and `Goto` job verified at tick 59 |
| first guarded time pulse | 285 ms | in-process event watch | Stopped at 0 ticks on the new hostile edge |
| second guarded 3 s Normal pulse | 3.24 s | in-process event watch | Existing hostile suppressed; 181 ticks advanced; John arrived exactly |

The move path's one-cell `get_cell_info` call took 10 ms. Its two whole-roster
`list_colonists` reads took 16 ms each and returned about 4.9 KB each. The
largest avoidable cost is therefore not cell inspection; it is the multi-call
camera/selection/UI path. Those mutations are currently deliberate for
watchability.

## Behavioral checks

- The game remained paused while beginning the session and issuing the order.
- John was auto-drafted and the write-ahead obligation was durable before the
  draft call.
- RimWorld exposed a real `Goto` job; click delivery alone was not accepted.
- The ordinary status board continuously named John as pending cleanup.
- After a guarded pulse John reached `(121,117)`, remained drafted, and entered
  `Wait_Combat`.
- Cleanup refused to undraft him while the hostile remained.
- Forced partial release restored John to his original undrafted state.
- Ending the empty session removed its ledger. The game remained paused at tick
  240 with the tortoise still hostile.

## Bugs found and fixed during the live test

1. `home/status` emits stable colonist IDs as `thingId`; the original fixture
   used the `list_colonists` alias `pawnId`. The empty baseline made every name
   resolution refuse. Combat now accepts both producer names, and the primary
   fixture uses the real `home/status` shape.
2. Hostile protection ran before dry-run output, so even a non-mutating cleanup
   preview was refused. Dry-run now prints the proposed restoration plus a
   hostile warning; only real cleanup requires `--force`.

After these corrections, 22 deterministic combat/movement tests pass.

## Four-raccoon stress test

Fixture extension: four impaired manhunter raccoons, two entering from each end
of the map. Sammy received the bolt-action rifle, Iguchi the revolver, and John
the plasteel knife. Equip menu actions were accepted immediately but required
about sixteen seconds of Normal game time before all three weapons were actually
equipped; this needs job-completion tracking rather than a fixed timeout.

The colonists formed near `(122,118)`. The ranged pair killed the first attacker.
One raccoon reached Iguchi; the existing watcher stopped on the resulting new
Medical-treatment alert. Iguchi retreated successfully and John intercepted and
killed it. The last pair reached Sammy before being downed and finished by John.

Final state: four raccoon corpses verified, zero hostiles, John uninjured, Sammy
at 82% health with roughly 26 hours to bleed out, and Iguchi at 89% with roughly
45 hours. All three were restored to their original undrafted state and the
combat ledger was removed. The game was left paused at tick 10928.

### In-vivo findings

- Accepted equip orders are not equipped weapons; completion must be observed.
- Direct right-click on an active hostile can immediately create `AttackMelee`
  without leaving a menu. A helper that assumes every attack returns menu options
  can throw after the game has already accepted the order.
- A downed hostile does leave a menu with `Melee attack ... to death`; finishing
  an armored animal can take substantially longer than ten seconds.
- Hostile level changes currently cause a zero-tick reporting stop whenever the
  hostile signature changes (four to three to two to one). This is safe but too
  interruptive for combat; the future watcher needs policy-level edges rather
  than treating every surviving-set change as a fresh generic threat.
- Ordinary injuries were not an event edge. Sammy accumulated four wounds and
  fell to an approximately 26-hour bleed-out deadline before the twelve-second
  tactical boundary. The proposed companion-side injury deadline/capacity watch
  is required, not optional polish.
