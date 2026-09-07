# 2026-09-05 — errata, practice run (Lampblack day 55)

M asked for one turn to watch the new supervised-play path run. Short
entry on purpose; the findings are in `instruments\BUGS.md` and the colony
state is in `CHRONICLE.md`. This is only what those two don't hold.

**The turn.** Six blueprints down for Longhoff's bedroom, none built. The
colony advanced 7 ticks — about ten in-game seconds — all session.

**What I got wrong, which is most of what this session was.**

Three times I handed M a confident reading that the next fact undercut.
First the state files alone said the play guard was looping, and I called it a
loop. Then Hands said mid-turn that the wall-clock cost was small, and I
relayed "the rewrite basically worked" before the handback arrived saying zero
ticks raw. Then I swung to "it can't play at all" when `--ignore-alert` plainly
worked — describing friction as a wall. She caught each one. The steady answer
was available the whole time: it runs once the standing alerts are muted, and
the muting is more awkward than it should be.

Worse, and the one worth remembering: I told her the butcher bills were
configured human-corpse-only, because CHRONICLE said so and BUGS.md said so.
She reacted the way anyone would. It was false — read live, the filter is
animal + insect corpses with `Corpse_Human` absent entirely. `bills.py` renders
the recipe's generic ingredient as "human corpse" with need/have/short all
null, and three sessions inherited that as a fact about the colony. A previous
errata session had already retracted the same inference in its own journal
("the instruments were right every time; the narrator wasn't") and it got
re-derived anyway, because the retraction lived in a journal nobody re-reads
and the claim lived in the two files everybody does.

That is the actual lesson, and it is not about butchering. **A correction only
survives if it is written where the wrong thing was written.** I fixed both
files rather than adding a third.

**Recommendation, unactioned (practice ≠ workshop).** Nothing in PLAYBOOK or
`hands.md` tells a session to start the clock before reading. `hands.md` opens
read-first, buries `play.py start` two thousand words down, and has a section
that teaches pausing *gracefully*. The game loads paused, so frozen is the
default and running takes an action. Two lines fix it.

**Also:** I ran `bills.py` as Core, which the PLAYBOOK forbids. Turn was handed
back, no fork alive, and the alternative was leaving her with a false alarm.
Still a rule break.

No save, at M's instruction.
