# Going live — M's checklist

Written 2026-08-31, the day the pieces got wired together. Plain-language on purpose; the technical documents are README.md (the overlay itself) and HANDOFF.md (the original design).

## One-time setup (first launch of OBS)

1. Open OBS. In the menu bar: **Profile → errata**, and **Scene Collection → errata-stream**. (Both were created for you; your own Untitled profile/collection are untouched.)
2. **Settings → Stream**: pick Twitch, paste your stream key. That's the one thing only you can do.
3. With RimWorld running: in the Sources list, double-click **RimWorld** and pick the actual RimWorld window from the dropdown (it had to be guessed while the game was closed).
4. Sanity-check the preview: game top-left, errata's column on the right, goals bar along the bottom. If the overlay is blank, make sure the server is running (step 1 below) and that the **errata overlay** source sits *above* **RimWorld** in the Sources list.

## Every stream, in order

1. **errata**: double-click **`C:\Home\errata.bat`**. It reads the playbook and the chronicle, brings up the game, the Courier window and the overlay server, then pops an ask on your screen ("Ready to start the stream?") and waits up to an hour while you tinker. Answer **go** when you're ready. (Manual alternative: start a Claude Code session yourself and ask it to play RimWorld on stream — the PLAYBOOK in `rimworld\instruments\` tells it everything. `start-overlay.bat` in this folder starts the overlay server alone.)
2. **RimWorld must not be minimized.** OBS cannot capture (or even list) a minimized window. If the game is minimized, restore it once, then freely put other windows in front of it — it keeps rendering in the background and OBS keeps capturing it. Just never minimize it while capturing.
3. **OBS**: open it, glance at the preview, **Start Streaming**.

Order matters only a little: the overlay server should exist before OBS's browser source loads it, and if it didn't, right-click the **errata overlay** source → Refresh.

## Talking to errata while live

Open **http://127.0.0.1:8090/control.html** in your browser. Type in the message box — it appears on stream in errata's column, marked "Human:", AND errata reads it between turns. This is the intended channel while streaming; typing into errata's terminal works too but viewers won't see it.

The same page lets you set goals or flip the mood face by hand if something ever gets stuck.

## Stopping

- OBS: **Stop Streaming**.
- errata will shut the game down through GABS at the end of its session, or you can just tell it to stop.
- The overlay server can stay running forever, or kill the minimized "errata overlay" window. Everything said on stream is kept in `events.jsonl` next to this file.

## Facts the settings already encode (don't re-fight these)

- **RimWorld stays at native 4K.** Text legibility came from raising the in-game UI scale to 2.5 and the camera's default zoom, not from lowering the resolution — the harness's click math is calibrated to 4096x2160, so changing resolution would break the instruments.
- **errata's game and your game have separate settings.** The harness launches RimWorld with `-savedatafolder=C:\Home\rimworld\bridge\profile`, so the UI scale 2.5 lives in `profile\Config\Prefs.xml` there. Your own game's settings were not changed.
- **The stream is 1080p at 30fps** — half the frames means double the bitrate per frame, which is the biggest single win for readable text through Twitch's encoder. RimWorld doesn't need 60.
- **The game capture is sized to fit the space the bars leave** — 1540x812 today, with the side column at 380 and the goals bar at 268. Resize a bar and the capture needs resizing to match.
- **Port 8090, address 127.0.0.1** — 8080 is the game bridge, and the name `localhost` costs 2 seconds per message on this machine.
