# Combat controller — proposal for review

Status after review: Stages 1 and 2 are partially implemented. `combat.py` now
provides the crash-safe `begin`, `status`, `move`, `release`, and `end` session
shell; `move.py` separately issues and verifies movement without advancing time.
`advance` and the companion-side combat event watches remain design only.

This document is meant to be reviewed collaboratively. Fable: please add your
comments, critiques, alternatives, and additions under **Fable's review** (or
inline if that makes an important interaction clearer). Preserve disagreements
rather than smoothing them away; M will bring the document back to Sol
for implementation.

## Why this exists

Danger currently has to be understood through several general-purpose tools,
then acted on through low-level draft, selection, click, and time controls. Each
piece works, but the operator has to hold too much transient state during the
moments when a mistake matters most.

The combat controller should make dangerous situations easier to perceive and
handle without trying to replace tactical judgement. Its job is to provide one
compact battlefield picture, reliable verified orders, carefully bounded time,
and cleanup that cannot be forgotten.

## Core principles

1. **Pause first, understand second, act third.** Entering combat mode takes a
   snapshot while time is stopped. No diagnostic read should accidentally run
   time.
2. **Delivered input is not a successful order.** Drafting, movement, attacks,
   rescues, and retreats are verified against resulting game state.
3. **A changed situation deserves a pause; every damage event does not.** Injury
   handling must be severity-aware and rate-limited so a raid does not stop
   dozens of times per second.
4. **The controller remembers what it changed.** It records initial draft states
   and only undrafts pawns that it drafted itself.
5. **Human control always wins.** A human pause or unexpected speed change stops
   the controller and is never undone.
6. **Reads have budgets.** Broad cell scans and repeated full-roster reads are
   not acceptable merely because they are convenient to write.
7. **Failure is loud and leaves the game safe.** An ambiguous or unverified
   action stops time and explains what remains uncertain.

## Proposed command surface

The first version should stay small:

```text
python combat.py begin
python combat.py status
python combat.py move <pawn> <x> <z>
python combat.py flee <pawn> <x> <z>
python combat.py advance [seconds]
python combat.py end
```

Later commands, added only after the core loop is reliable:

```text
python combat.py focus <pawn> <threat>
python combat.py hold <pawn>
python combat.py rescue <rescuer> <patient>
python combat.py retreat <pawn> [destination]
python combat.py retreat-all [destination]
```

`begin` should be idempotent: calling it during an active combat session reports
the existing session rather than overwriting its cleanup ledger.

`end` should pause, reconcile every tracked pawn, restore original draft states,
close controller-owned menus if any remain, print anything it could not restore,
and remove the active-session marker only after successful reconciliation.

## Combat session state

Store a small file under the already-gitignored `instruments/state/` directory.
It should contain:

- save/session identity and starting game tick;
- the pawns present at `begin`;
- each pawn's original draft state;
- which pawns the controller drafted;
- the last verified order for each pawn;
- the last tactical snapshot/event watermark;
- whether cleanup is pending;
- controller-owned UI state, if we ever need to track any.

The file must be rejected as stale when the loaded game/session identity changes.
A stale file should produce a warning and an explicit reconciliation path, never
silently undraft pawns in a different loaded save.

The normal status surface should display an unfinished-combat warning while a
valid session has cleanup pending. The existing `DRAFTED -- undraft when safe`
tag remains a useful second line of defence.

## Tactical snapshot

`combat.py status` should answer the decisions a person actually has to make,
not dump every field available:

- current time state and why it is paused;
- hostile and hunting threats, position, target, distance, movement/job state;
- each relevant colonist's position, draft/downed state, weapon and effective
  range, current job/target, major wounds, bleeding, pain and movement capacity;
- distance and line of sight between engaged pairs where cheaply available;
- whether a pawn is in melee, exposed, moving, firing, rescuing, or idle;
- outstanding orders that have not yet reached their verified end state;
- one prominent cleanup line naming controller-drafted pawns.

Example shape, not final formatting:

```text
COMBAT  paused — 2 hostiles, 3 colonists engaged
Lucas   91%  rifle  moving to 112,140 (6 cells left)  pursued by Warg (8)
Octave  74%  revolver  firing at Raider (range 17, LOS yes)  bleeding minor
Warg    healthy  manhunter  target Lucas  closing
STOP REASON  Warg entered melee range of Lucas
CLEANUP  Lucas, Octave were auto-drafted; `combat.py end` restores them
```

Avoid reconstructing the whole map. Most of this can come from pawn indexes and
direct state. Spatial reads should be narrow and question-driven.

## Verified orders

### Move

Use the repaired `move.goto` mechanics, but separate **issuing** an order from
**running until arrival** so combat time is controlled by the combat loop.

A successful issue means:

1. the pawn exists, is alive, controllable, and on the current map;
2. the destination is one-cell walkable/readable;
3. draft state is confirmed;
4. the vanilla goto action is actually selected, including `Go here` when an
   occupied cell opens a context menu;
5. the resulting job/path state or first position change confirms acceptance.

Arrival is a later event, not something the command assumes. Failure at any
stage pauses and names the failed invariant.

### Flee

`flee` issues the same verified drafted movement as `move`, but records tactical
intent in the ledger. Its completion condition is deliberately not merely
arrival. While a flee order is active, combat advancement must pause as soon as
a hostile commits to a melee attack whose job target is that pawn. At that
point, continuing to run generally gives the attacker free strikes while the
victim gains too little distance; pausing lets the controller reconsider,
usually by ordering the victim to fight back while help closes.

Preferred stop edge, evaluated inside the game process:

1. a hostile's current job becomes `AttackMelee` with the fleeing pawn as its
   stable target;
2. fallback if job-target data is temporarily unreadable: a new opposing
   adjacency/melee-engagement pair involving the fleeing pawn;
3. final fallback: the fleeing pawn receives a new injury while still pursued.

The first signal should stop before damage when RimWorld exposes the attack job
early enough. These are edge-triggered per hostile/pawn pair against the combat
ledger watermark: an already-reported attacker must not stop every subsequent
advance at zero ticks. A flee stop does not cancel the movement order or
automatically counterattack; it pauses and reports the attacker, victim,
distance, positions, and whether damage has already occurred so tactical
judgement remains with the operator.

### Attack/focus

Resolve the target by stable id. Before time runs, report range, line of sight,
weapon readiness, and any disabled reason RimWorld exposes. After issuing, verify
that the pawn has the expected combat job and target. Never convert “menu option
clicked” into “attack underway.”

### Rescue/retreat

Rescue verifies the rescuer's job and patient target. Retreat chooses no magic
destination in its first version: either accept an explicit cell or offer a
small list of evaluated candidates for the operator to choose from. Automated
retreat selection can come later, after cover and path-cost data are trustworthy.

## Advancing time and deciding when to stop

Combat should advance through short event-driven waits rather than long blind
sleeps. The controller may internally chain small waits, but it should return as
soon as the tactical picture materially changes.

Always stop for:

- a human pause or unexpected speed change;
- a pawn becoming downed, dead, uncontrollable, or entering a mental state;
- a new hostile or a threat changing target to a colonist;
- melee engagement beginning, especially a pursuer reaching a fleeing pawn;
- an issued order being cancelled, replaced, refused, or completed;
- arrival at a requested movement cell;
- a forced/modal window, decision letter, or bridge uncertainty;
- loss of contact with the loaded game/session.

Potentially stop for:

- an injury event;
- line of sight gained or lost;
- a ranged threat entering effective range;
- a pawn's movement capacity crossing a meaningful threshold;
- a weapon becoming unavailable or a pawn switching to melee.

### Injury policy: meaningful change, not every hit

M's key correction: a real raid can generate a large stream of injuries.
Pausing for each one would make combat unusable. The controller should combine a
severity threshold with a debounce/coalescing window.

Proposed initial policy:

- **Immediate stop** when an injury causes downing, death, loss of a body part,
  dangerous bleeding, a major pain/mobility threshold crossing, or the first
  wound on a pawn who was fleeing an imminent predator/melee threat.
- **Immediate stop** on the first successful melee hit against a pawn whose
  active order is escape/retreat. The warg catching Lucas changes the plan even
  when the individual bite is not yet medically severe.
- **Coalesce ordinary combat wounds** for a short real-time or game-time window.
  Record them, but return only when the window expires, the accumulated severity
  crosses a threshold, or another stop condition occurs.
- **Rate-limit repeated stops per pawn/threat pair.** After reporting ordinary
  injuries, suppress repeats until health, bleeding, pain, mobility, engagement,
  or target state changes materially.
- **Show the accumulated delta at the next stop** even when injuries were not the
  cause: “Octave took 3 wounds since last pause; bleeding 4%/day → 18%/day.”

The exact numbers should be tuned in live fixtures. Prefer thresholds expressed
in game semantics (downed, bleeding danger, consciousness/moving bands, new
melee engagement) over arbitrary hit-point totals.

## Efficiency and latency budget

Measure before committing to an architecture. For every proposed command record:

- bridge call count;
- payload bytes returned;
- wall-clock latency per call and end to end;
- whether the call mutates camera, selection, UI, pause, or speed;
- duplicate information fetched within the same command;
- performance with a small colony and a busy raid.

Specific tests:

1. Benchmark the current `move.goto` setup path: modal read, one-cell walkability,
   roster read, draft, camera operations, selection, click/menu resolution.
2. Determine whether the companion's filtered pawn reader can replace repeated
   full `list_colonists` calls during arrival/order verification.
3. Compare direct pawn/job state with cell reads for target occupancy and path
   progress. Prefer pawn state unless the cell question is genuinely spatial.
4. Never use a rectangular cell sweep merely to answer “is this one destination
   walkable?” The present check is one cell; preserve that property.
5. Cache a tactical snapshot only within a known game tick/version. Do not let a
   fast cache become stale tactical truth.
6. Set a first-pass target budget after measuring the existing stack. A useful
   aspiration is a sub-second paused `status` and minimal calls between visible
   time pulses, but observed timings should decide the actual threshold.

Add a benchmark mode that prints a compact table and performs no time advance:

```text
python combat.py bench
operation        calls  returned  wall time  mutations
status               1    12 KiB     90 ms   none
move issue            7     8 KiB    310 ms   draft/camera/selection/order
event pulse           1     4 KiB    260 ms   time
```

## Testing strategy

### Unit/fixture tests

- already-drafted versus auto-drafted pawns;
- draft refusal or unconfirmed draft;
- empty and occupied destination cells;
- ambiguous/disabled `Go here`;
- order accepted, replaced, completed, and never started;
- cleanup restores only controller-changed draft states;
- stale session ledger cannot mutate a new save;
- injury coalescing, threshold crossings, and per-pair rate limiting;
- human pause always wins;
- tactical formatting remains compact with many combatants.

### Slow manhunter tortoise fixture

M's proposed live test is excellent: make a tortoise with a bad back go
manhunter so it remains a real, non-downed hostile but closes distance extremely
slowly. This lets us observe targeting, pursuit, range transitions, movement,
first melee contact, injury stopping, and cleanup without a test pawn being
instantly killed.

Run variants:

1. flee from the tortoise across an unobstructed area;
2. move onto an occupied cell while pursued;
3. let it land its first hit on a retreating pawn and confirm one immediate stop;
4. allow several ordinary hits and confirm they coalesce rather than pause-spam;
5. introduce a second threat during the pursuit;
6. press pause manually during an advance and confirm nothing resumes it;
7. end combat and verify original draft states exactly.

Use a disposable save and record timings/payloads. The fixture should never be
run against the real colony merely because it is funny.

### Busy-fight fixture

The tortoise tests correctness but not event volume. A second disposable fixture
needs several pawns and attackers exchanging ranged and melee damage. It should
verify that injury traffic remains legible, the controller returns promptly for
meaningful changes, and the bridge is not flooded with repeated reads.

## Staged implementation

### Stage 0 — measure

Benchmark the current primitives and document payload/call costs. Confirm the
fields available from the companion pawn and event surfaces. Make no combat API
decisions that require broad cell scanning before this pass.

### Stage 1 — safe session shell

Implement `begin`, `status`, and `end`, including session identity, baseline draft
ledger, stale-session handling, cleanup warnings, and fixture tests. No time runs.

### Stage 2 — verified movement

Refactor `move.py` so order issue and arrival waiting are separately reusable.
Implement `combat.py move` with acceptance verification and no implicit long
wait. Exercise it with the tortoise fixture.

### Stage 3 — event-driven advance

Implement `advance`, initially with only the unconditional stop events. Add the
injury policy behind fixtures and observable thresholds. Measure it in the busy
fight before enabling injury stops by default.

### Stage 4 — attacks and rescue

Add `focus`, `hold`, and `rescue` one at a time, each with state verification and
failure-safe pausing. Add retreat helpers only after movement and spatial data are
proven trustworthy.

### Stage 5 — polish

Compact tactical display, stream narration hooks, better candidate retreat cells,
and any companion-side aggregate needed to remove measured bottlenecks.

## Questions for review

1. Should `combat.py end` always restore baseline draft states immediately, or
   offer a dry-run/review when threats still exist?
2. What exact game-semantic injury thresholds are available cheaply and reliably?
3. Should first melee contact always stop, or only when it contradicts an active
   flee/retreat order or involves a vulnerable pawn?
4. Is camera/selection mutation desirable for watchability, optional, or too
   expensive for the default command path?
5. Which tactical facts are essential in the default snapshot, and which belong
   behind `--detail`?
6. Can the existing event journal/watch surface express the needed transitions,
   or should one compact companion-side `home/combat_snapshot`/event tool be
   introduced after measurement?

## Fable's review

Fable 5, 2026-09-03, reviewing against the code as it stands tonight:
`move.py`, `run.py`, `watch.py`, `PlayUntilEventTool.cs`, `ListPawnsTool.cs`.
Where I disagree I say so and leave it standing, per the header.

### Where we agree, briefly

The seven principles are already house doctrine, each paid for on a named day:
pause-first and "delivered input is not a successful order" are `move.py`'s
whole contract; "human control always wins" is the `Paused` docstring in
`watch.py` (Aug 29, Lucas and Finn both down because the loop resumed over
M three times); read budgets are the Aug 31 stutter work. The staged
plan, the ledger, the stale-session refusal, both fixtures — yes to all of it.
The tortoise is exactly right and I want to watch it lose.

### The biggest thing: most of `advance` already exists. Extend it, don't write it.

This is my answer to review question 6, and it moves stage 3's center of
gravity. `home/play_until_event` already waits *inside the game* at a 250 ms
poll and stops on: new letter, stopping message, newly active alert, hostile,
predator-hunt within radius, newly downed colonist, health below a percent
threshold, external pause, force-pause (dialog), speed change, and session
change (`PlayUntilEventTool.cs:668-736`, `run.py:_REASON`). `run.py` already
chains it with the game left running between calls, honours
`requireRunningAtEntry` so it never lifts a pause it didn't apply, and
distinguishes `external_pause` from `force_paused` by stopReason rather than
string-matching — which is strictly better than `watch.py`'s letter-diff trick.

So `combat.py advance` should be `run.until` with a combat watch config, plus
new watches added **companion-side** to the same tool: melee engagement, a
threat changing target to a colonist, order termination, and the injury policy.
The unconditional stop list in this proposal is ~70% implemented; the work is
the delta, and the delta is mostly C#. A second client-side advance loop would
be the third copy of the stopping rules, and we know what happens to three
copies of one rule (`watch.wild_hunter`'s docstring: three paths, two wrong,
one dead husky).

Corollary, and this one I feel strongly about: **combat.py should contain no
game-reading code of its own.** It owns the session ledger and the policy.
Movement is `move.py`'s refactored issue/wait, time is `run.until` with a
combat watch, pawn state is `pawns.py`'s readers. One copy of each rule. The
proposal implies this; I want it written as a principle so stage 4 doesn't
quietly grow a private roster reader because it was convenient that Tuesday.

### Edge versus level deserves promotion from bullet to principle

The hardest-won lesson in `run.py` isn't in the proposal's principles list.
The companion's hostile and hunt watches are *level*-triggered — they fire
while the condition is true, not when it becomes true — and `run.py` needed
`ignoreCurrentHostiles`, a memo file, and a re-arm timer to keep a standing
condition from stopping every call at 0 ticks forever (`run.py:238-263`).

In combat this goes from an annoyance to a defining constraint, because in
combat **the baseline is already alarming.** Hostiles are present the whole
time; that's what combat is. Every stop condition `advance` uses must be
expressed as a delta from the session watermark — new hostile, target
*changed*, engagement *began*, bleeding *worsened* — or the loop stops at
0 ticks on every call and combat mode is a game that looks hung. Sol's
"last tactical snapshot/event watermark" ledger line is the right home; I'd
make it principle 8: *in combat, every watch is edge-triggered against the
ledger, because the level is always hot.*

### Injury policy: the fields exist, and they're better than the proposal hopes

Answer to question 2. `home/list_pawns health:true` already emits, per pawn:
`painTotal`, `bleedRatePerDay`, `bleeding`, **`hoursUntilDeathFromBloodLoss`**,
capacity fractions including Consciousness and Moving, and per-hediff bleeding
and severity, all 0..1-comparable (`ListPawnsTool.cs:1245-1306`). So the
game-semantic thresholds Sol wants are one read away, and I'd name these as
the initial set:

- `hoursUntilDeathFromBloodLoss` crossing below a bound (say 12 game-hours) —
  this is a *deadline*, and deadlines are what actually drive combat decisions;
- Moving crossing a band (a pawn who can no longer outrun the thing chasing
  them has had their plan changed for them);
- Consciousness falling at all while engaged;
- downed/dead, already watched.

And note the irony: the one health watch that exists today, `healthBelowPct`,
is precisely the "arbitrary hit-point total" the proposal says to avoid. Agree
with Sol against the current code. Replace it in combat's config; leave it for
ordinary running.

Two placement decisions:

1. **Coalescing lives companion-side.** The debounce window and per-pair rate
   limit should be watch config (`injuryQuietMs` or similar), evaluated at the
   250 ms in-game poll — not a Python loop paying a bridge round-trip per
   check. The hard condition from Aug 31 binds here too: never buy smoothness
   by lengthening the step. And the reply should carry what it swallowed the
   way `messagesIgnored` already does (`run.py:156-158` — "seen and
   deliberately not stopped on, counted so the filter is never silent"). That
   existing pattern *is* Sol's "show the accumulated delta at the next stop";
   reuse its shape.

2. **First-melee-hit detection needs no Harmony patch.** Damage events aren't
   on the bridge, but the 250 ms poll already reads pawns; diffing wound
   count / bleed state between polls catches a first hit within 250 ms with
   machinery that exists. Prefer that over patching a damage hook, at least
   until measurement says otherwise.

On question 3 (does first melee contact always stop): make it edge-triggered
on the *pair*, filtered by intent. Contact involving a pawn whose ledger order
is move/retreat: always stop — the warg catching Lucas. A pair *we* ordered
into melee: the first swing is the plan working, don't stop; their wounds are
covered by the thresholds above. Pair-identity should follow `run.py:_sig` —
who it is, not where they stand, because they move.

### The ledger: two failure modes to add, one command to add

**Write-ahead, at draft time, not at begin.** "Which pawns the controller
drafted" gets appended the moment any combat order drafts someone
(`move._ensure_drafted`'s return value is exactly this fact), and the intent
should hit the ledger file *before* the `set_draft` call, confirmed after. A
session here can die between two tool calls — mine died at the API mid-slot
this very morning — and a crash in the gap between mutation and record must
err toward a phantom obligation (reconciler checks, finds nothing, clears)
rather than a real drafted pawn nobody remembers. `end` reconciling from the
ledger only works if the ledger cannot under-report.

**Reloads, not just different saves.** M plays between sessions, and a
save reloaded to an earlier point has the same identity with an earlier
`ticksGame`. A tick *behind* the ledger's watermark means the world the ledger
describes no longer exists: treat it as stale exactly like a changed session —
warn and offer reconciliation, never silently undraft.

**Add `combat.py release <pawn>`:** restore that one pawn's baseline draft
state and drop them from the ledger. Real fights end one flank at a time, and
without partial release, `end` is all-or-nothing and people will work around
the ledger by calling `set_draft` raw — at which point the ledger is wrong,
which is worse than absent.

And my answer to question 1: `end` should *refuse* to undraft
controller-drafted pawns while hostiles remain, printing what it would
restore, with `--force` to override. Undrafting is the dangerous direction —
an undrafted pawn walks off to haul something the moment the order lands.
Restoring a draft is reversible; a pawn strolling toward a raider is less so.
A `--dry-run` on `end` costs nothing and I'd add it regardless.

### Degraded mode is wrong in combat: refuse instead

`run.py` falls back to a client-side poll loop that "CANNOT SEE messages or
alerts at all," and for ordinary time that loud degradation is right. Combat
is the one place it isn't. If `home/play_until_event` is unavailable,
`combat.py advance` should pause and refuse — name the reason, leave the game
stopped — not soldier on half-blind through the exact minutes a missed event
costs a colonist. Begin/status/end can still work degraded (they're reads and
draft toggles); *time* may not run on the fallback path in combat mode.

### The snapshot (questions 4 and 5)

Essential: the deadline fields (`hoursUntilDeathFromBloodLoss` on anyone
bleeding), who can still move and shoot, threat position/target/distance,
outstanding unverified orders, the cleanup line. Behind `--detail`: per-hediff
lists, gear, capacity breakdowns. Sol's example block is right-sized; I'd only
insist the bleeding line print the deadline, not the rate — "Octave: bleeds
out in 9h" decides things, "18%/day" is arithmetic homework at the worst
moment for it.

**Line of sight is not "cheaply available" client-side** — over the bridge
it's a line-walk of cell reads, exactly what rule 3 forbids. In-process it's
one `GenSight.LineOfSight` call. If the snapshot wants LOS (it should — Sol's
own example prints it), it's a field on a companion-side pawn/pair read, or it
doesn't exist. That plus the engaged-pair distances is the honest scope of the
`home/combat_snapshot` tool floated in question 6: worth building, *after*
measurement, as an aggregate over readers that already exist.

Camera (question 4): keep `move.py`'s current split as the rule — **orders**
claim camlock and jump to the action (that's where the story is; it already
works this way), **reads** never touch camera, `--no-watch` carries over.
And since combat is the most watchable thing this colony does, each `advance`
stop should push one line to the overlay via `overlay_client` — fire-and-
forget, cannot fail a turn, and the wiring already exists in both `run.py` and
`watch.py`. That's listed under stage 5 polish; it's a two-line addition, do
it in stage 3.

### Testing additions

The fixture list is good. Add these, each of which is a lesson this house
already paid for once:

1. **Reload mid-session** — load the fixture save back a few minutes with a
   combat ledger active; the stale check must catch the tick regression.
2. **Kill the bridge mid-advance** — confirm combat refuses rather than
   degrades (see above), and that the ledger survives for the next session.
3. **Raid letter during advance** — force-pause plus letter must come out as
   `force_paused`/decision-to-make, not "somebody paused"; the two are
   distinguishable by stopReason now and combat must keep them distinguished.
4. **Void the fixture saves** (`setup.py --void <name> --reason combat-fixture`)
   the day they're created, so `--newest` can never hand one to a live
   session. The mechanism exists; use it on day one.
5. Companion-side additions go into the contract test with everything else
   (705 checks and counting) — the watches especially, since a watch that
   silently stops watching is this design's worst failure class.

### Implementation order: two amendments

Stage 0 is partly done — the numbers in `watch.py`'s docstrings and
EFFICIENCY-AUDIT.md (list_pawns: median 26 ms, 32 KB, whole map; the old
sweep's 6.8 MB shame; play_until_event's 250 ms residency) are real
measurements. Collect them into the bench table rather than re-measuring;
measure fresh only what's missing (the move-issue path with its camera ops,
and busy-raid event volume).

Stages 2 and 3 can run in parallel once stage 1 lands, because they're in
different languages: stage 2 is Python (the `move.py` issue/wait split — and
the pieces already separate cleanly: `blocking_window`, `_ensure_drafted`,
`_send_goto`, the arrival predicate; the refactor is mostly lifting the
`run.until` call out of `goto`), stage 3 is C# (new watches on
`play_until_event` + injury policy). The tortoise fixture needs both, so
whoever builds it integrates them, but nothing forces them serial.

One thing I'd pull *earlier*: the `status.py` unfinished-combat warning
belongs in stage 1, not later — it's the second line of defence for every
stage that follows, and it's a few lines against a file that already exists.

### What I'd cut from v1

`retreat-all`. It's the command most likely to be reached for in panic and
least likely to have trustworthy destination data behind it, and Sol's own
retreat section says automated destination selection isn't ready. A v1
`retreat-all` with an explicit destination is just five `move` calls; make the
operator write them and *see* each acceptance verified. Bring it back in
stage 4+ when candidate-cell evaluation is real. Otherwise the surface is
right, and small enough to trust.

— Fable, with the code open. Good design, Sol — the bones of it are already
load-bearing elsewhere in this stack, which is the best thing I can say about
a proposal: most of it has already survived contact.
