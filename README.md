# RimWorld Claude Harness

This is the working harness behind the Lampblack streamed colony: Claude Code drives RimWorld through Pardeike's bridge, a custom companion DLL, a Python play layer, safety checks, a chronicle, and an optional browser overlay.

The repository intentionally does **not** redistribute Pardeike's projects. `Setup.bat` downloads the current releases directly from [RimBridgeServer](https://github.com/pardeike/RimBridgeServer) and [GABS](https://github.com/pardeike/GABS).

## Quick start (Windows / Steam)

Prerequisites: RimWorld 1.6 installed in Steam's default library, [Claude Code](https://docs.anthropic.com/en/docs/claude-code), Python 3, and a .NET SDK.

1. Clone or download this repository.
2. Double-click `Setup.bat`. It downloads Pardeike's bridge pieces, builds the included companion tools, installs them beside the RimWorld mod, creates an isolated game profile, and registers GABS as a project-local Claude Code MCP server.
3. Restart Claude Code if it was already open.
4. Double-click `Run RimWorld Harness.bat`.

The launcher loads the included Lampblack example save and asks before it begins playing. RimWorld itself and any DLC/mod dependencies are not included.

If Steam is installed somewhere else, edit `$modDir` and `$managed` near the top of `setup.ps1` before running it.

## What's included

- `instruments/` — the Python control, observation, safety, combat, and turn-management layer.
- `companion/` — source and tests for the custom `home/*` RimBridge tools.
- `profile/` — an isolated RimWorld configuration and one current example save.
- `journal/` and `instruments/CHRONICLE.md` — the real development/play history, retained as an example.
- `overlay/` — the optional local stream overlay. Copy `chat-config.example.json` to `chat-config.json` and set your own channel.
- `patches/` — source and a ready-built mod for the optional Adrenaline compatibility fix; see its README for when it applies and how to install it.

Generated state, bridge tokens, logs, screenshots, binaries, autosaves, and local chat configuration are excluded. The setup only writes to this checkout, RimWorld's local `Mods`/`BridgeTools` folders, and Claude Code's project-local MCP configuration.

## Important notes

This is an honest snapshot of a personal, evolving live harness, not a polished general-purpose mod. It contains colony names and narrative history by design. Write tools can alter a save; keep backups and start with the included isolated profile.

See `instruments/PLAYBOOK.md` for the operating model and `companion/INSTALL.md` for the companion tool contracts.
