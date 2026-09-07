"""Real mouse clicks on the RimWorld window, for the two places the bridge can't
reach: the world globe and the Page_SelectStartingSite bottom bar (that page has
a zero-size window rect and draws its buttons in ExtraOnGUI, so get_ui_layout
returns an empty surface for it).

Coordinates are screen pixels, and RimWorld here is fullscreen 4096x2160, so a
pixel in a bridge screenshot is the same pixel here.
"""
import sys, time
sys.path.insert(0, r"C:\Home\tools\petz")
import winctl


def rimworld_hwnd():
    for w in winctl.list_windows():
        if w["class"] == "UnityWndClass" and "RimWorld" in (w["title"] or ""):
            return w["hwnd"]
    raise SystemExit("RimWorld window not found")


def click(x, y, right=False, focus=True):
    if focus:
        winctl.focus(rimworld_hwnd())
        time.sleep(0.2)
    winctl.move_screen(x, y)
    time.sleep(0.15)
    winctl.button(True, right=right)
    time.sleep(0.06)
    winctl.button(False, right=right)
    time.sleep(0.25)


if __name__ == "__main__":
    click(int(sys.argv[1]), int(sys.argv[2]), right="--right" in sys.argv)
    print(f"clicked {sys.argv[1]},{sys.argv[2]}")
