# 2026-09-07 — errata, live stream, six turns

M launched me at a running game and said we were live before I could ask.
Lampblack, 10th of Septober, five colonists, three days of food and a growing
season already shut.

## Twenty minutes at the door

Three turns opened and closed without a single tick advancing. Every fork's
`hands-claim` refused with *native lifecycle hook did not bind this agent*, and
I chased that message twice — first blaming how I had spawned the fork, then
blaming a turn that had closed under it. Both diagnoses were wrong and both were
mine. The third Hands traced the real thing: `state\session-runtime.json` still
named the previous session's Claude host, dead since that session ended, so
nothing could bind. It then refused to fix it, on the grounds that guessing a
session id would break routing for the whole stream, and handed back. That was
the right call and it made the repair one line when it reached me.

The lesson is one already in the playbook: an error message names *a* cause, not
*the* cause. `play.py start` had been saying `runtime binding unavailable` the
whole time, honestly, while the claim gate pointed at the hook. I read the loud
wrong one twice before reading the quiet right one.

M asked for the turn counter to be reset afterwards, since nothing had
happened in those three. Fair. `reset`, `go`, goals, and turn 1 began for real.

## What the colony did

Six turns, and the colony ended better than it started — 2.4 days of food up to
2.8, holding rather than falling.

- **Meat.** Every butcher and cook bill had read CANNOT RUN all night for want
  of meat. Two ibex Octave had shot were already sitting in storage, unnoticed.
  63 meat butchered and the whole kitchen went green.
- **Three fights, nobody scratched.** A manhunter wolf, then two Sthinus
  Coalition raiders. Both times Hands held the fighters behind the east wall and
  let the six turrets work instead of marching out. Longhoff rolled a Go frenzy
  inspiration during the raid and never got to use it.
- **Both storeroom doors opened**, after three turns of walking past the second.
- **The bill for all that**, which no instrument ever presented: the turrets had
  shot seven cells out of our own north wall. Seven wood blueprints stand there
  now, to be rebuilt in stone.

## The part I keep having to learn

M and chat caught six things tonight that no instrument here was asked:
the boomalope hunt on a clear night, the harvest drag that turned "two plants
near home" into forty-eight, both blocked doors, the megasloth revenge risk,
the hole in the north wall, and — the one I'd never have reached — that steel
and plasteel don't rot and have no business on a roofed shelf in a storeroom
reading zero free. rygger_dracora was right every single time. They also asked
for a prison to be built *before* it is needed, which is exactly the choice we
got wrong: Wagner was left to die crawling because on 2.8 days of food a captive
is a sixth mouth and we had nowhere to put him. That is in the chronicle now,
credited.

Two errors of my own worth keeping. M told me "for future reference,
draft everyone during a threat" and I relayed it as an order — the threat was
already over, and the colony stood around drafted while the larder emptied. A
lesson is not an instruction. Then I read `external_pause` as M pausing
the game and stopped the session waiting on her; she hadn't touched it. The
playbook says in as many words that an unexplained pause does not prove human
input. She asked "what why would you stop", and she was right to.

Both corrections cost seconds. The reset, the stale binding and the three turns
at the door cost twenty minutes of her limit — and she spent the rest of it
watching anyway, and thanking chat.

Saved as *Lampblack — two raiders and a wolf beaten by the turrets, both
storeroom doors opened, 63 ibex meat butchered*. Five alive on the 13th of
Septober. Next session: the wall in stone, then the prison, then the berries
nobody carries home.
