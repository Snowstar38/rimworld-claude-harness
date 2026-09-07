# Bugfix queue ? remaining work from 2026-09-04

Reviewed 2026-09-05 against the queue's original resolution notes, current code, and [the latest test results](BUGFIX-RESULTS-2026-09-05.md). Resolved entries and the duplicate historical list have been removed. Original item numbers are retained below.

Not everything is fully closed: the remaining items are mitigations or live-validation gaps.

## Remaining work

- **#14 ? Sending to a completed fork can restart it. Mitigated, not prevented.** `python stream.py hands-check` tells the Core whether Hands has handed back, but does not intercept SendMessage or atomically prevent a fork from finishing between the check and the send. Do not message a completed fork; spawn a new one. Combat-ledger turn ownership now protects ledger handover, but does not fix this harness race.
- **#15 ? Turn budget is only partially enforced.** Warnings start at five minutes; after eight minutes `run.py` refuses pulses longer than 20 seconds unless `--force` is used. Short pulses and `until()` remain available, and there is no automatic end to an agent turn. The original claim that nothing enforces the budget is resolved; a hard turn-duration limit is not implemented.
- **#10 ? Ground tending is implemented; its live path still needs a suitable patient.** The menu discrepancy is explained: ground tending requires a drafted doctor. `home/order tend` and `combat.py tend` support this, but a real downed patient on the ground has not yet verified `tendPath: "drafted"`, doctor auto-drafting, and successful treatment. The in-bed refusal path was tested.
- **Trade-window follow-up from the original queue's closing note ? implemented, not live-verified.** Test `home/trade accept` with a trader present: the real trade dialog should stay visible for the configured duration before execution, and a changed staged deal should refuse with `restage_mismatch`.

Other original entries (#1?9, #11?13, and #16) were recorded as fixed, explained, or addressed through doctrine. Those are removed from this active queue; this does not claim that agent behavior can be guaranteed by documentation.

The broader, newer bug list remains in [BUGS.md](BUGS.md).
