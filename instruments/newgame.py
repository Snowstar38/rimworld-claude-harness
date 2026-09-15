"""Small, state-aware shortcuts for RimWorld's new-colony screens.

This is intentionally not a full colony generator.  It automates only the
boring/reliable clicks and stops where M should make a choice.

  python newgame.py where
  python newgame.py begin
  python newgame.py scenario "Crashlanded"
  python newgame.py storyteller Cassandra "Strive to survive" reload
  python newgame.py world-defaults
  python newgame.py site-random
  python newgame.py site-accept
  python newgame.py ideology-none
  python newgame.py pawns

The three pixel shortcuts are deliberately calibrated for fullscreen
4096x2160.  They refuse when the expected page is not open.
"""
import sys
import time

import click as real_mouse
import rim
import ui


PAGES = {
    "scenario": "RimWorld.Page_SelectScenario",
    "storyteller": "RimWorld.Page_SelectStoryteller",
    "world": "RimWorld.Page_CreateWorldParams",
    "site": "RimWorld.Page_SelectStartingSite",
    "ideology": "RimWorld.Page_ChooseIdeoPreset",
    "pawns": "RimWorld.Page_ConfigureStartingPawns",
}

MAIN_MENU = (3103, 745)
RANDOM_SITE = (1845, 2075)
NEXT_SITE = (2644, 2075)
STORYTELLERS = {"cassandra": 0, "phoebe": 1, "randy": 2}


def screen():
    return rim.game("rimworld/get_screen_targets", {}, strict=False)


def page(reply=None):
    reply = screen() if reply is None else reply
    if isinstance(reply.get("targets"), dict):
        reply = reply["targets"]
    state = reply.get("uiState") or {}
    top = state.get("topWindowType")
    if state.get("programState") == "Entry" and not top:
        return "main-menu"
    for short, typename in PAGES.items():
        if top == typename:
            return short
    # World pages create a topmost ImmediateWindow for the globe.  The actual
    # page remains the focused window underneath it and is the state we need.
    window_types = {w.get("type") for w in (reply.get("windows") or [])}
    for short, typename in PAGES.items():
        if typename in window_types:
            return short
    # A message box can sit over the page; name it rather than guessing.
    return top or "unknown"


def require(want):
    got = page()
    if got != want:
        raise RuntimeError("expected the %s page, but the top screen is %s" % (want, got))


def wait_for_page(want, timeout=5.0):
    deadline = time.time() + timeout
    got = page()
    while got != want and time.time() < deadline:
        time.sleep(0.25)
        got = page()
    if got != want:
        raise RuntimeError("expected the %s page, but the top screen is %s" % (want, got))


def text_click(label):
    result = ui.click(label)
    if not result.ok:
        raise RuntimeError("the bridge did not confirm the click on %r" % label)
    print("clicked %r -- %s" % (label, result.line()))


def begin():
    require("main-menu")
    real_mouse.click(*MAIN_MENU)
    wait_for_page("scenario")
    print("New colony -> scenario page")


def scenario(name):
    require("scenario")
    try:
        text_click(name)
    except LookupError:
        # RimWorld renders the selected scenario as inert text; all unselected
        # scenarios are buttons.  This is the one useful meaning of no target.
        raw = ui.layout()
        labels = [ui.printed_text(e.get("label"))
                  for s in (raw.get("surfaces") or [])
                  for e in (s.get("elements") or [])]
        if name not in labels:
            raise
        print("%r is already the selected (non-button) scenario" % name)
    text_click("Next")
    require("storyteller")


def storyteller(name, difficulty, save_mode):
    require("storyteller")
    key = name.strip().lower().split()[0]
    if key not in STORYTELLERS:
        raise ValueError("storyteller must be Cassandra, Phoebe, or Randy")
    got = ui.click_index(STORYTELLERS[key])
    if not got.ok:
        raise RuntimeError("storyteller portrait click was not confirmed")
    text_click(difficulty)
    modes = {"reload": "Reload anytime mode", "commitment": "Commitment mode"}
    if save_mode not in modes:
        raise ValueError("save mode must be reload or commitment")
    text_click(modes[save_mode])
    rendered = ui.render(ui.slim())
    print(rendered)
    if "[?]" in rendered:
        print("Selection state is still [?] in the installed bridge. The clicks were sent,"
              " but use python see.py if you want visual confirmation before Next.")
    else:
        print("Review the [x] choices above.")
    print("Then: python newgame.py next")


def next_page(expected):
    require(expected)
    text_click("Next")
    print("now at %s" % page())


def world_defaults():
    require("world")
    text_click("Generate")
    rim.game("rimbridge/wait_for_long_event_idle", {"timeoutMs": 240000})
    require("site")
    print("World generated with the visible defaults -> landing-site page")


def site_random():
    require("site")
    real_mouse.click(*RANDOM_SITE)
    print(ui.render(ui.slim()))
    print("Random site selected; its tile card is above. Re-run site-random or use site-accept.")


def site_accept():
    require("site")
    real_mouse.click(*NEXT_SITE)
    time.sleep(1.0)
    print("now at %s" % page())


def ideology_none():
    require("ideology")
    text_click("Ideology system inactive")
    text_click("Next")
    require("pawns")
    pawns()


def pawns():
    require("pawns")
    import startpawns
    code = startpawns.render(startpawns.read())
    if code:
        raise RuntimeError("starting-pawn reader refused")
    print("\nUse the game's Randomize buttons as desired. Start remains a deliberate final click.")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    rim.init()
    cmd, args = argv[0], argv[1:]
    try:
        if cmd == "where" and not args:
            print(page())
        elif cmd == "begin" and not args:
            begin()
        elif cmd == "scenario" and len(args) == 1:
            scenario(args[0])
        elif cmd == "storyteller" and len(args) == 3:
            storyteller(args[0], args[1], args[2].lower())
        elif cmd == "next" and not args:
            next_page("storyteller")
        elif cmd == "world-defaults" and not args:
            world_defaults()
        elif cmd == "site-random" and not args:
            site_random()
        elif cmd == "site-accept" and not args:
            site_accept()
        elif cmd == "ideology-none" and not args:
            ideology_none()
        elif cmd == "pawns" and not args:
            pawns()
        else:
            print("newgame.py: unknown command or wrong arguments; use --help")
            return 2
        return 0
    except (RuntimeError, ValueError, LookupError, rim.BridgeError) as exc:
        print("NEW GAME REFUSED: %s" % exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
