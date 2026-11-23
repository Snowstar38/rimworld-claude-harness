> Superseded lifecycle/notification behavior: see BUGFIX-DAY60-2026-09-05.md. Hands now returns at SubagentStop, never parks; routine neutral/positive notifications no longer stop play. The evidence below describes the earlier implementation.

# Supervised play and independent review

This replaces the five-second stepping workflow and the old tool-driven rota as the normal stream architecture. The earlier bug-fix report remains a record of that first repair pass; its time-running and automatic-review sections are superseded here.

## Normal operation

Launch through `C:\Home\errata.bat`. The session-start hook binds the runtime to the actual Claude process and its birth identity. Once M chooses the session mode, `stream.py go` starts the independent reviewer. Each background Hands claims its native event route with `stream.py hands-claim`.

`play.py start` returns after a hidden service acquires the in-game lease. Hands continues building, changing bills, reading colony state, giving orders, and narrating while the game plays. `play.py pause` is an idempotent emergency pause; `play.py speed Normal|Fast|Superfast` changes speed without ending supervision. A safety stop requires an explicit new start.

The service renews a 30-second lease every ten seconds while the controlling runtime process remains alive, including model inactivity. The in-game main-thread watcher checks lifecycle conditions every frame and colony conditions approximately every 100 milliseconds. Lost heartbeats, failed monitoring, modal force-pauses, and unexpected speed changes fail closed. Failed pauses remain armed and retry.

New letters and significant transient messages pause once, without a wording classifier. This includes failed quests, predator-hunting notices, and visitors leaving. Simultaneous notices are preserved together in a single batch. Only command/UI feedback types RejectInput, CautionInput, SilentInput, and TaskCompletion are excluded; unknown types pause. Resume baselines existing notices to avoid repeated pauses. Alerts pause nothing at all: those standing at start are baselined by stable key and listed on the start line, and a later High or Critical one is delivered as a non-stopping `alert_new` message at the next tool boundary. Critical wakes a parked Hands; High rides along with the next wakeup. The `ignoredAlertLabels` parameter is accepted and does nothing. Hands is instructed to react through say.py so colony news reaches the stream, inspect decisions, and resume when appropriate.

Colony mode stops on new or worsening injuries. Combat mode permits ordinary wounds during an explicitly acknowledged fight, while guarding downing/death, health deterioration, new threats, and notifications. Combat starts require a current combat ledger and explicit known hostile IDs. No general danger acknowledgement is inferred from silence.

## Independent review and delivery

The external reviewer starts immediately and then on fixed 180-second monotonic deadlines, independent of Core, Hands, game time, and completion of the previous review. Each pass receives fresh screenshots and a generic visual-review prompt, with no colony briefing, transcript, or seeded data report. Readers rotate Luna/Sol/Sol/Luna. A hung pass has a 150-second budget; shutdown terminates only the owned review process tree.

A separate thread listens for overlay human messages without delaying reviewer deadlines. Both routes use a durable SQLite outbox. Native agent IDs determine the recipient: active Hands receives reports and human messages directly; Core receives them only when Hands has released ownership. Receipts, deduplication, session identity, and loaded-game generations prevent dropped or stale delivery. Failed delivery retries; acknowledgment ends retries.

Busy Hands receives inbox context at tool boundaries. An idle Hands parks quietly in its own synchronous SubagentStop lifecycle hook, without a model tool-call polling loop and without sending an idle completion to Core. An event returns native stop-control feedback to that same child. Core fallback uses asyncRewake. Testing showed child asyncRewake output went to Core, so that mechanism is deliberately not used for Hands.

Hands explicitly runs `stream.py hands-release` to pause and hand back. Core handback also recovers the route after an interrupted Hands task. Session end stops the services. A silent hour without any reviewer/event ends parked ownership safely, rather than leaving a completed agent registered.

## Validation

- Native Claude smoke tests: Core wakeup, direct child stop-hook resumption, and real session-start host binding passed.
- Live blind screenshot review completed in about 12 seconds and reached the Hands inbox with no Core copy.
- Live start returned in about 0.23 seconds; colony ticks advanced during an independent colony read and while the host was idle. Owned speed change, heartbeat renewal, explicit pause, and repeated pause passed.
- A real Quest failed notice paused the final policy build, reached Hands, and did not repeat after acknowledgment and resume.
- Live lease expiry and unexpected speed change both produced verified pauses.
- Fixed 180-second scheduling, hung-review cleanup, routing handoff, retries, generation changes, and combat thresholds have automated coverage. A full-length live stream and a fresh manhunter combat fixture were not run.
- Final automated checks: 194 instrument tests and 43 companion tests passed; all 826 live contract checks passed. Release build: zero warnings/errors.
- Installed DLL SHA-256: `C5F32F15A21607E0471A2E6ACD5A85F90FE31F299853619722F85533741FD41E` (2026-09-05 evening build: alerts never pause; the previous `C1D166CA…` is kept as `artifactsinstalled-backup-20260905HomeBridge.BridgeTools.dll.pre-alerts-never-pause`).
- Original day-55 save reloaded and left paused. Test services are stopped; the next errata.bat launch starts a fresh runtime binding.
- No save writes were issued. All 39 original save-file SHA-256 hashes remain unchanged.

Evidence is in instruments/scratchpad: supervised-live-final.log, notification-live-final.log, supervised-failsafe-live.log, reviewer-live-final.log, runtime-binding-live.log, native-wakeup-smoke, native-hands-park-smoke, supervised-instruments-tests.log, and supervised-live-contract.log.
