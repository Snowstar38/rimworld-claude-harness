# Verified combat actions: controller integration

`combat_actions.py` is a side-effecting library, not a second combat session
controller. It never advances time and owns no ledger.

For equipment, the controller should call `issue_equip(pawn_id, x, z,
weapon_thing_id=..., weapon_label=...)`. Its returned ticket distinguishes
`accepted` (RimWorld shows an `Equip` job) from `completed` (the requested
weapon is actually primary). If incomplete, pass the ticket to
`wait_for_equipment(ticket, waiter=combat_waiter, seconds=...)`. The waiter must
be the same guarded, event-aware primitive used by `combat.py advance`; do not
substitute a fixed sleep or an independent polling loop. A hostile/injury stop
before completion raises instead of claiming the pawn is armed.

For melee, call `issue_melee_attack(attacker_id, target_id, target_state=row)`.
Passing the current target row is recommended for a downed pawn because threat
summaries may cease listing it while it remains a valid finishing target. The helper accepts
the live UI's two observed paths: an active hostile may immediately create an
`AttackMelee` job with no menu, while a downed hostile must expose exactly one
enabled `Melee attack ... to death` option. Both paths finish by reading the
attacker's actual job. Click delivery alone is never success.

The controller remains responsible for pausing, drafting and recording draft
cleanup before either action. Targets should come from the current combat
snapshot so their stable ThingIDs and hostility/downed state are available.

The integration is now exposed as:

```
python combat.py equip Sammy 120 118 --weapon-label "bolt-action rifle"
python combat.py equip Sammy 120 118 --weapon-id Thing_Gun123 --wait-seconds 30
python combat.py attack John Thing_Raccoon456
```

Equip's wait is one `home/play_until_event` combat pulse followed by an actual
primary-weapon read. A tactical/event stop before completion is a refusal, not
a retry; the session remains paused for reassessment. `--wait-seconds 0` records
the verified Equip job without advancing time. Attack auto-drafts through the
same write-ahead cleanup ledger used by movement.
