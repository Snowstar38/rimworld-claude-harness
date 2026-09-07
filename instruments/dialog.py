"""Type into the dialog that is on screen -- the only way to answer a text box.

  python dialog.py                          # what boxes the top dialog has
  python dialog.py "Lampblack"              # DRY RUN -- what would be typed
  python dialog.py "Lampblack" --do         # actually type it
  python dialog.py "Lampblack" --do --accept        # type it and press accept
  python dialog.py "Lamplighters" --field nick --do # pick the box by name
  python dialog.py --json                   # the raw reply

  import dialog
  dialog.fields()               dialog.set_text("Lampblack", do=True, accept=True)

## Why this exists

Real OS keyboard input does not reach this RimWorld. A dialog that asks for a
typed name -- a colony, a settlement, a renamed animal -- used to need a person
at the keyboard. `home/dialog_text` sets the window's string field directly,
which is what typing does: RimWorld redraws the box from that field every frame.

## Naming the box

Most dialogs have one. When there is more than one, `--field` takes the name
`fields[]` printed, a substring of it, or a substring of the box's label --
"nick", "first", "last" on a rename dialog, or the field name on a
faction-and-settlement dialog, which has two.

## --accept

Presses the dialog's accept the way the Return key does. Ignored on a dry run.
A letter dialog has `closeOnAccept` false and ignores it -- that is the game's
own behaviour, not a failure here; close those with their own Close button.
"""
import json
import sys

import rim

TOOL = "home/dialog_text"


def fields():
    """The top dialog's string fields, written to nothing. Returns the reply."""
    return rim.game(TOOL, {"list": True, "dryRun": True}, strict=False)


def set_text(text, field=None, accept=False, do=False):
    """Set one box. Dry run unless do=True. Returns the reply."""
    args = {"text": text, "accept": accept, "dryRun": not do}
    if field:
        args["field"] = field
    return rim.game(TOOL, args, strict=False)


def show(r):
    """One line per box, then the before/after of whatever was set."""
    if not isinstance(r, dict):
        print("dialog.py: the bridge returned %s, not a reply." % r.__class__.__name__)
        return 1
    if not r.get("success"):
        print("REFUSED: %s" % (r.get("error") or r.get("message") or r))
        if r.get("window"):
            print("  top window: %s" % r["window"])
        return 1

    print("DIALOG %s" % (r.get("window") or "?"))
    rows = r.get("fields") or []
    if not rows:
        print("  (no string field -- nothing to type into)")
    for f in rows:
        print("  %-28s %s" % (f.get("name") or "?", _show(f.get("before"))))

    s = r.get("set")
    if s:
        print("SET %s" % ("DRY RUN" if r.get("dryRun") else "DONE"))
        print("  %-28s %s -> %s"
              % (s.get("field") or "?", _show(s.get("before")), _show(s.get("after"))))
        print("  accepted: %s" % ("yes" if r.get("accepted") else "no"))
    if r.get("error"):
        print("  note: %s" % r["error"])
    if r.get("dryRun") and s:
        print("  nothing was written. Re-run with --do to type it.")
    return 0


def _show(v):
    if v is None:
        return "(null)"
    if v == "":
        return "(empty)"
    return str(v)


def main():
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0

    as_json = "--json" in argv
    do = "--do" in argv
    accept = "--accept" in argv
    field = None
    if "--field" in argv:
        i = argv.index("--field")
        if i + 1 >= len(argv):
            print("dialog.py: --field needs a value")
            return 1
        field = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    argv = [a for a in argv if a not in ("--json", "--do", "--accept")]

    try:
        rim.init()
        r = fields() if not argv else set_text(" ".join(argv), field=field,
                                               accept=accept, do=do)
    except Exception as e:
        print("dialog.py FAILED -- NOTHING WAS TYPED.")
        print("%s: %s" % (e.__class__.__name__, e))
        return 1

    if as_json:
        print(json.dumps(r, indent=1))
        return 0
    return show(r)


if __name__ == "__main__":
    sys.exit(main())
