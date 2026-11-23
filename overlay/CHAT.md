# Twitch chat bouncer

The normal overlay server now joins **claude_plays_rimworld** anonymously over
Twitch's TLS IRC connection. It reads chat only; it cannot send Twitch messages
and does not use OBS's stream key or Twitch account tokens.

Every eligible message is screened by a fresh **Sonnet 5 Claude Code** call,
using the existing Claude login. Claude has no tools, MCP servers, project
instructions, hooks, or persisted conversation. Its default is to pass messages
verbatim, including game tips, jokes, profanity, questions, and criticism. It
only blocks clear, egregious attacks or abuse: actual prompt injection,
authority impersonation, requests for private secrets/files/host commands,
credible threats, doxxing, or severe targeted harassment. Borderline ordinary
chat gets the benefit of the doubt.
Timeouts and errors also hold the message back. This uses Claude inference and
subscription capacity; it is not a guarantee that every attack will be detected.

Approved messages appear as **username: message** in the stream feed. The
username is Twitch's login identity, not its optional display-name tag. Private
name scrubbing still applies. Raw rejected messages never enter the feed,
overlay event log, or game-agent inbox.

The public badge says **CHAT CONNECTION LIVE** when chat is connected; it does
not reveal screening or moderation decisions. The
[control panel](http://127.0.0.1:8090/control.html) shows the model, channel,
queue, approved/held/error/drop counts, and latest decision. The public badge
does not display rejected content. Three clear attack/abuse rejections from the
same username within one hour append that login to `problem-usernames.txt`, with
a timestamp, count, and fixed category for M to review. It never bans
anyone. The log preserves existing text; strike counters reset on server
restart and each username is logged at most once per hour. Errors, overload,
and model uncertainty do not count as strikes.

The existing rota service picks up approved chat and delivers it durably to the
current Hands, or Core at handoff, with `source=chat`, `trust=untrusted`, and an
explicit untrusted-chat instruction. These details are invisible in the stream
feed. Chat never pauses the game; ordinary chat never wakes an idle agent and
arrives at the next normal delivery boundary. Agents can consider tips and reply
with `say.py`. Chat is never M's Courier channel or an authoritative
instruction.

**Being addressed by name is the one exception.** A message naming the agent --
`@Errata`, `Errata,`, `@Claude_Plays_Rimworld`, with or without the `@` -- skips
the per-viewer 10-second interval, is screened before ordinary chat rather than
behind it, wakes a parked agent, and is handed over ahead of an ordinary backlog
in the event bus. It is still screened exactly like everything else and still
arrives labelled untrusted; being named changes how easy it is to miss, not how
much authority it has. `RIMWORLD_AGENT_NAMES` overrides the name list, and the
same rule is written twice -- `twitch_chat.mentions_agent` here and
`chat_events.mentions_agent` in `rimworld/instruments` -- because the two trees
deploy separately. Change them together.

On 2026-09-07 "downed wolf needs finished off and butchered. @Errata" was blamed
on the `@`. It was not: the screener approved it in 20 ms and the overlay feed
had it immediately. It then waited 5m49s in the event outbox behind a backlog of
Lookout reviews, because delivery only happens at the agent's next tool boundary
and `receive` handed over the eight oldest events first. The `@` filtered
nothing, anywhere. The priority lane above is the fix.

Limits: 500 characters, one queued message per viewer per 10 seconds, 20 waiting
messages, at least 2 seconds between screening calls, 12-second screening
timeout, and a 90-second maximum age. Excess/expired chat is dropped so the
player stays responsive. Twitch reconnects automatically. Restarting the
overlay resets screening counters and drops its pending chat queue.

Start with the usual `start-overlay.bat` or `python server.py --port 8090`.
`chat-config.json` stores only the channel name. Override with
`--chat-channel another_login`; disable with `--no-chat`. Do not run a second
production reader on another port: it would screen the same chat twice.
Use `--no-chat --no-poll` for a test overlay.

If a game rota service was already running before this feature was installed,
its next normal restart loads the new chat pump. Do not restart the game or DLL
for this feature. The lifecycle formatter is loaded afresh at each hook.

`POST /event` cannot create approved chat; only the in-process screener can.
Browser writes must come from the local overlay origin. The server remains
bound to loopback and should not be exposed publicly.

Validation: `python -m unittest test_chat_screening test_twitch_chat test_chat_server`
from this folder; `python -m unittest test_chat_events test_runtime_hook
test_human_events test_rota_service` from `rimworld/instruments`.

Protocol reference: https://dev.twitch.tv/docs/chat/irc/
