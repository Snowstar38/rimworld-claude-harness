"""Read RimWorld's new-colony pawn page without requiring an active map.

  python startpawns.py
  python startpawns.py --json
"""
import json
import sys
import rim

TOOL = "home/starting_pawns"

def read():
    return rim.game(TOOL, {}, strict=False)

def render(reply):
    if not isinstance(reply, dict):
        print("STARTING PAWNS REFUSED: %s" % str(reply)[:300])
        return 1
    if not reply.get("success"):
        print("STARTING PAWNS REFUSED: %s" % (reply.get("error") or "no reason given"))
        return 1
    print("STARTING PAWNS  %d selected / %d available%s" % (
        reply.get("startingPawnCount", 0), reply.get("pawnCount", 0),
        "  [%s]" % reply["scenario"] if reply.get("scenario") else ""))
    for pawn in reply.get("pawns") or []:
        marker = "*" if pawn.get("selected") else "-"
        print("\n%s %d. %s" % (marker, pawn.get("index", 0) + 1, pawn.get("name") or "?"))
        print("  backstory : %s / %s" % (pawn.get("childhood") or "-", pawn.get("adulthood") or "-"))
        print("  traits    : %s" % (", ".join(t.get("label") or "?" for t in pawn.get("traits") or []) or "none"))
        print("  incapable : %s" % (", ".join(pawn.get("incapableOf") or []) or "nothing"))
        skill_bits = []
        for skill in pawn.get("skills") or []:
            passion = {"Minor": "+", "Major": "++"}.get(skill.get("passion"), "")
            level = "-" if skill.get("disabled") else skill.get("level")
            skill_bits.append("%s %s%s" % (skill.get("name") or "?", level, passion))
        print("  skills    : %s" % "; ".join(skill_bits))
    print("\nTEAM SKILLS (best selected pawn)")
    print("  " + "; ".join("%s %s%s [%s]" % (s.get("name"), "-" if s.get("disabled") else s.get("level"),
        {"Minor": "+", "Major": "++"}.get(s.get("passion"), ""), s.get("pawn") or "?") for s in reply.get("teamSkills") or []))
    controls = reply.get("controls") or {}
    print("\nCONTROLS  Randomize: %s | Start: %s" % ("available" if controls.get("randomize") else "unavailable", "available" if controls.get("start") else "unavailable"))
    if not reply.get("pageOpen"):
        print("  (Page_ConfigureStartingPawns is not open.)")
    return 0

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["--help"] or argv == ["-h"]:
        print(__doc__); return 0
    as_json = "--json" in argv
    argv = [a for a in argv if a != "--json"]
    if argv:
        print("startpawns.py does not take %r; see --help" % " ".join(argv)); return 2
    rim.init()
    try: reply = read()
    except rim.BridgeError as exc:
        print("STARTING PAWNS FAILED  %s" % exc); return 1
    if as_json: print(json.dumps(reply, indent=1)); return 0
    return render(reply)

if __name__ == "__main__":
    sys.exit(main())
